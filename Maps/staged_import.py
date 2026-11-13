"""Staged, non-blocking outfit batch import.

The batch generator in ``operators.iter_batch_import_instances`` yields after each
mesh step and each material step. This module drives that generator from a
window-manager timer in small time slices, shows progress on the window manager,
and supports Esc or the Cancel button. The sync path drains the same generator.
"""

import time

import bpy
from bpy.types import Operator

PLACEHOLDER_NAME = "Arc Loading"
# Timer interval while a step is ready: 0.0 fires on the next event-loop pass, so no
# wall time is lost between slices; the budget keeps each slice under one ~60 Hz frame.
TICK_SECONDS = 0.0
TICK_BUDGET_SECONDS = 0.014
# Poll interval while the generator only waits on a thread or subprocess (stage "wait").
POLL_SECONDS = 0.03
PACK_BUDGET_SECONDS = 0.014

_PENDING_JOB = None   # (presets, finish) queued by the caller before invoke
_PENDING_GEN = None  # (make_gen(context), finish) for a non-batch generator
_ACTIVE = None        # StagedRun while the modal operator is alive


def _alive(obj) -> bool:
    try:
        return obj is not None and obj.name in bpy.data.objects
    except Exception:
        return False


def _placeholder_material():
    mat = bpy.data.materials.get(PLACEHOLDER_NAME)
    if mat is None:
        mat = bpy.data.materials.new(PLACEHOLDER_NAME)
        mat.diffuse_color = (0.5, 0.5, 0.5, 1.0)
        mat.use_nodes = False
        mat["arc_placeholder"] = 1
    return mat


def stage_placeholder(objects) -> int:
    """Set every slot of each new mesh to the grey placeholder. Slot count is kept."""
    mat = _placeholder_material()
    count = 0
    for obj in objects or ():
        if not _alive(obj) or obj.type != "MESH" or obj.data is None:
            continue
        mats = obj.data.materials
        if "arc_staged_slots" not in obj.data:
            obj.data["arc_staged_slots"] = [m.name if m is not None else "" for m in mats]
        if len(mats) == 0:
            mats.append(mat)
        else:
            for i in range(len(mats)):
                mats[i] = mat
        count += 1
    return count


def restore_slots(obj) -> bool:
    """Put back the slot materials the mesh import made, so material setup can read their names."""
    if not _alive(obj) or obj.type != "MESH" or obj.data is None:
        return False
    names = obj.data.get("arc_staged_slots")
    if names is None:
        return False
    names = list(names)
    replace_slot_materials(obj.data, [bpy.data.materials.get(name) if name else None for name in names])
    del obj.data["arc_staged_slots"]
    return True


def replace_slot_materials(mesh, materials) -> None:
    """Replace the slot list of a mesh and keep every face on its slot.

    ``mesh.materials.clear()`` moves all faces to slot 0, so a two-section mesh (visor frame and
    glass) would end with one material on every face. Faces keep their index here.
    """
    mats = mesh.materials
    materials = list(materials)
    if len(mats) == len(materials):
        for i, mat in enumerate(materials):
            mats[i] = mat
        return
    n = len(mesh.polygons)
    saved = None
    if n and len(mats) > 1:
        import numpy as np
        saved = np.empty(n, dtype=np.int32)
        mesh.polygons.foreach_get("material_index", saved)
    mats.clear()
    for mat in materials:
        mats.append(mat)
    if saved is not None and materials:
        import numpy as np
        np.minimum(saved, len(materials) - 1, out=saved)
        mesh.polygons.foreach_set("material_index", saved)
        mesh.update()


def placeholder_slot_count(objects) -> int:
    """Count mesh slots still holding the placeholder material."""
    mat = bpy.data.materials.get(PLACEHOLDER_NAME)
    if mat is None:
        return 0
    n = 0
    for obj in objects or ():
        if not _alive(obj) or obj.type != "MESH" or obj.data is None:
            continue
        n += sum(1 for m in obj.data.materials if m is mat)
    return n


def sweep_placeholder(objects) -> int:
    """Clear any slot still holding the placeholder, then drop the placeholder datablock."""
    mat = bpy.data.materials.get(PLACEHOLDER_NAME)
    if mat is None:
        return 0
    left = 0
    meshes = bpy.data.meshes if objects is None else [o.data for o in objects if _alive(o) and o.type == "MESH" and o.data is not None]
    for me in meshes:
        for i, m in enumerate(me.materials):
            if m is mat:
                me.materials[i] = None
                left += 1
    if mat.users == 0:
        bpy.data.materials.remove(mat)
    return left


def discard_run_objects(before_objects, before_meshes) -> int:
    """Remove objects and meshes created since the snapshot, then drop the placeholder."""
    removed = 0
    for obj in [o for o in bpy.data.objects if o not in before_objects]:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
        except Exception:
            pass
    for mesh in [m for m in bpy.data.meshes if m not in before_meshes and m.users == 0]:
        try:
            bpy.data.meshes.remove(mesh)
        except Exception:
            pass
    sweep_placeholder(None)
    return removed


def enter_solid_viewports() -> list:
    """Switch every 3D viewport to Solid. Returns [(space, old_type)] for restore.

    A Rendered Cycles viewport is left alone: the outfit import sets it up on purpose so
    each material is seen arriving (Shared/import_view.py).
    """
    saved = []
    try:
        keep_rendered = bpy.context.scene.render.engine == 'CYCLES'
    except Exception:
        keep_rendered = False
    try:
        wms = list(bpy.data.window_managers)
    except Exception:
        return saved
    for wm in wms:
        for win in wm.windows:
            screen = win.screen
            if screen is None:
                continue
            for area in screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                for space in area.spaces:
                    if space.type != 'VIEW_3D':
                        continue
                    try:
                        old = space.shading.type
                        if old == 'RENDERED' and keep_rendered:
                            continue
                        if old != 'SOLID':
                            space.shading.type = 'SOLID'
                            saved.append((space, old))
                    except Exception:
                        pass
    return saved


def restore_viewports(saved) -> int:
    """Put each switched viewport back to its own shading type."""
    n = 0
    for space, old in saved or ():
        try:
            space.shading.type = old
            n += 1
        except Exception:
            pass
    return n


class LazyImageBind:
    """Unbind image nodes of materials created in this run; bind them later in slices.

    Holds a reference to every image so nothing becomes an orphan meanwhile.
    """

    def __init__(self, before_materials):
        self.before = set(before_materials)
        self.seen = set()
        self.pending = []   # [(material, [(node_name, image), ...])]

    def defer_new(self) -> int:
        n = 0
        for mat in list(bpy.data.materials):
            if mat in self.before or mat in self.seen:
                continue
            self.seen.add(mat)
            nt = mat.node_tree if mat.use_nodes else None
            if nt is None:
                continue
            nodes = []
            for node in nt.nodes:
                if node.type == 'TEX_IMAGE' and node.image is not None:
                    nodes.append((node.name, node.image))
            if not nodes:
                continue
            for name, _img in nodes:
                nt.nodes[name].image = None
            self.pending.append((mat, nodes))
            n += len(nodes)
        return n

    def material_count(self) -> int:
        return len(self.pending)

    def _bind_one(self, mat, nodes) -> None:
        try:
            nt = mat.node_tree
        except ReferenceError:
            return
        for name, img in nodes:
            node = nt.nodes.get(name)
            if node is not None and node.image is None:
                try:
                    node.image = img
                except ReferenceError:
                    pass

    def iter_bind(self):
        while self.pending:
            mat, nodes = self.pending.pop(0)
            self._bind_one(mat, nodes)
            try:
                label = mat.name
            except ReferenceError:
                label = "?"
            yield label

    def bind_all(self) -> None:
        for _label in self.iter_bind():
            pass


_PACK_QUEUE = []


def _pack_candidates() -> list:
    out = []
    for img in bpy.data.images:
        try:
            if img.packed_file or img.users == 0:
                continue
            if img.source == 'FILE':
                if not img.filepath:
                    continue
            elif img.source != 'GENERATED' or not (img.is_dirty or img.has_data):
                continue
        except ReferenceError:
            continue
        out.append(img.name)
    return out


def pack_next() -> bool:
    """Pack one queued image. Returns True while more remain."""
    while _PACK_QUEUE:
        name = _PACK_QUEUE.pop(0)
        img = bpy.data.images.get(name)
        if img is None or img.packed_file:
            continue
        try:
            img.pack()
        except Exception as exc:
            print(f"Arc auto pack skipped '{name}': {exc}")
        return bool(_PACK_QUEUE)
    return False


def _pack_timer():
    if _ACTIVE is not None:
        return 0.5  # another import is running; wait until it ends
    t0 = time.perf_counter()
    while pack_next():
        if time.perf_counter() - t0 >= PACK_BUDGET_SECONDS:
            return 0.0  # more images queued; continue on the next event-loop pass
    return None


def schedule_auto_pack() -> int:
    """Queue every unpacked file image; a timer packs one per tick after import."""
    names = [n for n in _pack_candidates() if n not in _PACK_QUEUE]
    _PACK_QUEUE.extend(names)
    if _PACK_QUEUE:
        try:
            if not bpy.app.timers.is_registered(_pack_timer):
                bpy.app.timers.register(_pack_timer, first_interval=0.1)
        except Exception:
            pass
    return len(names)


def queue_job(presets, finish) -> None:
    global _PENDING_JOB
    _PENDING_JOB = (list(presets or []), finish)


def queue_generator(make_gen, finish) -> None:
    """Queue any step generator. make_gen(context) builds it; finish(op, context, result) runs on success."""
    global _PENDING_GEN
    _PENDING_GEN = (make_gen, finish)


def _catalog_warming() -> bool:
    try:
        from ..Shared.materials.rebuild import native_catalog_bridge
        return native_catalog_bridge.warming()
    except Exception:
        return False


class StagedRun:
    """Drives one batch generator in time slices. Records every step for checks."""

    def __init__(self, gen):
        self.gen = gen
        self.stage = ""
        self.index = 0
        self.total = 0
        self.label = ""
        self.steps = []          # (stage, index, total, label)
        self.step_times = []     # seconds per step, same order as steps
        self.max_step_s = 0.0
        self.max_tick_ms = 0.0   # frame-stall log: longest single advance() call
        self.tick_count = 0
        self.result = None
        self.error = None
        self.done = False
        self.cancel_requested = False
        self.waiting = False     # last event was ("wait", ...): nothing ready, poll slowly

    def advance(self, budget=TICK_BUDGET_SECONDS) -> bool:
        """Run steps until the time budget is spent. Returns True when the run ended."""
        if self.done:
            return True
        if _catalog_warming():
            # Let the catalog prewarm thread finish alone; see native_catalog_bridge.warming.
            self.waiting = True
            if not self.stage:
                self.stage, self.label = "prepare", "material catalog"
            return False
        t0 = time.perf_counter()
        try:
            return self._advance(t0, budget)
        finally:
            self.tick_count += 1
            self.max_tick_ms = max(self.max_tick_ms, (time.perf_counter() - t0) * 1000.0)
            if self.done:
                print("STALL_MAX_MS %.0f ticks %d" % (self.max_tick_ms, self.tick_count))

    def _advance(self, t0, budget) -> bool:
        self.waiting = False
        while True:
            s0 = time.perf_counter()
            try:
                event = next(self.gen)
            except StopIteration as stop:
                self.result = stop.value
                self.done = True
                return True
            except Exception as exc:  # reported by the operator, never swallowed silently
                self.error = exc
                self.done = True
                return True
            step_s = time.perf_counter() - s0
            self.max_step_s = max(self.max_step_s, step_s)
            self.step_times.append(step_s)
            stage, index, total, label = event
            if stage == "wait":
                # Generator polls a thread/subprocess: end the slice, sleep POLL_SECONDS.
                self.waiting = True
                return False
            self.stage, self.index, self.total, self.label = stage, index, total, label
            self.steps.append((stage, index, total, label))
            if time.perf_counter() - t0 >= budget:
                return False

    def cancel(self) -> None:
        """Close the generator so its finally blocks run (batch material mode off)."""
        try:
            self.gen.close()
        except Exception:
            pass
        self.done = True


def next_interval(run) -> float:
    """Timer interval after a slice: next loop pass while work is ready, slow poll while waiting."""
    return POLL_SECONDS if run is not None and run.waiting else TICK_SECONDS


def _publish(wm, run, active: bool) -> None:
    try:
        wm.arc_staged_active = active
        if run is not None:
            wm.arc_staged_stage = run.stage
            wm.arc_staged_index = run.index
            wm.arc_staged_total = run.total
            wm.arc_staged_label = str(run.label or "")
        # A timer tick alone does not redraw the sidebar or the viewport; ask for it so the
        # progress text and each new part show after every slice.
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type in {'VIEW_3D', 'NODE_EDITOR'}:
                    area.tag_redraw()
    except Exception:
        pass


STAGE_TEXT = {
    "mesh": "Importing mesh",
    "rig": "Fixing rig",
    "textures": "Finding textures",
    "material": "Building material",
    "slice": "Building material",
    "tidy": "Tidying node graph",
    "bind": "Binding textures",
    "finish": "Finishing",
    "prepare": "Preparing",
}


def progress_text(stage, index, total, label) -> str:
    """One line naming the current piece: phase, index of total, part or material name."""
    head = STAGE_TEXT.get(str(stage), "Importing")
    count = f" {index}/{total}" if total else ""
    return f"{head}{count}: {label}" if label else f"{head}{count}"


def draw_progress(layout, context) -> None:
    """Draw the staged import progress and Cancel button in an outfit panel."""
    wm = context.window_manager
    try:
        if not wm.arc_staged_active:
            return
    except Exception:
        return
    text = progress_text(wm.arc_staged_stage, wm.arc_staged_index, wm.arc_staged_total,
                         wm.arc_staged_label)
    box = layout.box()
    row = box.row()
    if hasattr(layout, "progress") and wm.arc_staged_total > 0:
        box.progress(factor=wm.arc_staged_index / max(1, wm.arc_staged_total), text=text)
    else:
        row.label(text=text)
    row.operator("arc_outfits.staged_import_cancel", text="Cancel", icon='CANCEL')


class ARC_OUTFITS_OT_StagedImport(Operator):
    bl_idname = "arc_outfits.staged_import"
    bl_label = "Import Outfit Colourways"
    bl_description = "Import outfit meshes, then build materials, without blocking the UI"
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    _timer_step = None
    _run = None
    _finish = None
    _before = None
    _before_meshes = None

    def invoke(self, context, event):
        global _ACTIVE, _PENDING_JOB, _PENDING_GEN
        if _ACTIVE is not None or (_PENDING_JOB is None and _PENDING_GEN is None):
            self.report({'WARNING'}, "An outfit import is already running or nothing is queued.")
            return {'CANCELLED'}
        if _PENDING_GEN is not None:
            make_gen, finish = _PENDING_GEN
            _PENDING_GEN = None
            gen = make_gen(context)
        else:
            presets, finish = _PENDING_JOB
            _PENDING_JOB = None
            from ..Shared import operators as _ops
            gen = _ops.iter_batch_import_instances(context, presets, staged=True)
        self._run = StagedRun(gen)
        self._finish = finish
        self._before = set(bpy.data.objects)
        self._before_meshes = set(bpy.data.meshes)
        _ACTIVE = self._run
        wm = context.window_manager
        wm.arc_staged_cancel = False
        _publish(wm, self._run, True)
        self._timer_step = TICK_SECONDS
        self._timer = wm.event_timer_add(TICK_SECONDS, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _retime(self, context, step) -> None:
        """Swap the timer only when the wanted interval changes (ready <-> waiting)."""
        if self._timer is None or step == self._timer_step:
            return
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        self._timer = wm.event_timer_add(step, window=context.window)
        self._timer_step = step

    def modal(self, context, event):
        global _ACTIVE
        wm = context.window_manager
        run = self._run
        if event.type == 'ESC' and event.value == 'PRESS':
            run.cancel_requested = True
        if wm.arc_staged_cancel:
            run.cancel_requested = True
        if run.cancel_requested and not run.done:
            run.cancel()
            return self._end(context, cancelled=True)
        if event.type != 'TIMER':
            return {'RUNNING_MODAL'}
        if run.advance():
            return self._end(context, cancelled=False)
        self._retime(context, next_interval(run))
        _publish(wm, run, True)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        # Blender calls this when it ends the modal itself (file load, window close).
        # Data may already be replaced, so only free the timer, close the generator and
        # clear the running flag; no object cleanup or report here.
        global _ACTIVE
        if self._run is not None and not self._run.done:
            self._run.cancel()
        try:
            if self._timer is not None:
                context.window_manager.event_timer_remove(self._timer)
        except Exception:
            pass
        self._timer = None
        _ACTIVE = None

    def _end(self, context, cancelled: bool):
        global _ACTIVE
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        run = self._run
        _ACTIVE = None
        _publish(wm, run, False)
        wm.arc_staged_cancel = False
        if cancelled:
            # Remove the partial objects this run created so no placeholder is left behind.
            discard_run_objects(self._before, self._before_meshes)
            self.report({'WARNING'}, "Outfit import stopped; partial objects were removed.")
            return {'CANCELLED'}
        if run.error is not None:
            self.report({'ERROR'}, f"Outfit import failed: {run.error}")
            return {'CANCELLED'}
        sweep_placeholder(
            [o for o in bpy.data.objects if o not in self._before]
        )
        if self._finish is not None:
            self._finish(self, context, run.result)
        return {'FINISHED'}


class ARC_OUTFITS_OT_StagedImportCancel(Operator):
    bl_idname = "arc_outfits.staged_import_cancel"
    bl_label = "Cancel Outfit Import"
    bl_description = "Stop the running outfit import"

    def execute(self, context):
        context.window_manager.arc_staged_cancel = True
        return {'FINISHED'}


classes = (
    ARC_OUTFITS_OT_StagedImport,
    ARC_OUTFITS_OT_StagedImportCancel,
)


# O1g: staged mode defers finish_gt_outfit_graph generators so the batch
# generator can slice them across ticks. None = normal (sync) mode.
FINISH_SINK = None


def defer_finish(gen, after) -> bool:
    if FINISH_SINK is None:
        return False
    FINISH_SINK.append({"gen": gen, "after": [after]})
    return True


def defer_after(after) -> bool:
    if not FINISH_SINK:
        return False
    FINISH_SINK[-1]["after"].append(after)
    return True
