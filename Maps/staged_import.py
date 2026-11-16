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


def _job_track():
    """ui_targets.job_track() when available, else a no-op context (never blocks a run)."""
    try:
        from ..Shared import ui_targets
        return ui_targets.job_track()
    except Exception:
        import contextlib
        return contextlib.nullcontext()


def _job_owned(pool="objects") -> list:
    try:
        from ..Shared import ui_targets
        return list(ui_targets.job_owned(pool))
    except Exception:
        return []


def discard_job_objects() -> int:
    """Remove only the objects and meshes this job created (job registry), then the placeholder.

    Objects the user added while the job ran are never touched; an empty registry removes nothing.
    """
    removed = 0
    for obj in _job_owned("objects"):
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
        except Exception:
            pass
    for mesh in _job_owned("meshes"):
        try:
            if mesh.users == 0:
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


def queue_generator(make_gen, finish, kind="Outfit") -> None:
    """Queue any step generator. make_gen(context) builds it; finish(op, context, result) runs on success.

    ``kind`` names the import in the stop/fail messages ("Weapon import failed: ...").
    """
    global _PENDING_GEN
    _PENDING_GEN = (make_gen, finish, str(kind or "Outfit"))


def _catalog_warming(start=True) -> bool:
    try:
        from ..Shared.materials.rebuild import native_catalog_bridge
        if start:
            native_catalog_bridge.prewarm()
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
        self.after_success = []  # callables run only after the run and its finish succeed
        self.waiting = False     # last event was ("wait", ...): nothing ready, poll slowly
        self.graphs_done = False # begin_graphs ran (material graph finishing phase)
        self.prewarm = True      # False: never start the catalog prewarm; measured for weapons: 1 s less total but longest freeze 6.5 s instead of 3.6 s, so left on

    def advance(self, budget=TICK_BUDGET_SECONDS) -> bool:
        """Run steps until the time budget is spent. Returns True when the run ended."""
        if self.done:
            return True
        if _catalog_warming(self.prewarm):
            # Let the catalog prewarm thread finish alone; see native_catalog_bridge.warming.
            self.waiting = True
            if not self.stage:
                self.stage, self.label = "prepare", "material catalog"
            return False
        t0 = time.perf_counter()
        try:
            with _job_track():
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

    def begin_graphs(self, objects, scene) -> bool:
        """After the build: lay out the new material graphs as one more sliced phase.

        graph_layout.finalize_objects ran synchronously in the finish (ui_targets.stamp,
        folder finish) and blocked the UI ~37 s on a 14-material outfit (each RNA op on a
        750-node group costs ~3 ms). Run here in slices, the later sync calls find every
        tree hooked and cached. Returns True when the phase started.
        """
        if self.graphs_done or self.error is not None or self.cancel_requested:
            return False
        self.graphs_done = True
        try:
            from ..Shared.materials import graph_layout
        except Exception:
            return False
        steps = graph_layout.iter_finalize_objects(list(objects or ()), scene)

        def phase():
            n = 0
            try:
                for _ in steps:
                    n += 1
                    yield ("finish", n, 0, "material graphs")
            except Exception as exc:  # layout is cosmetic: never fail the import over it
                print("Arc import: material graph finishing failed:", exc)
            return self.result

        self.gen = phase()
        self.done = False
        self.stage, self.label = "finish", "material graphs"
        return True

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
    "extract": "Extracting assets",
    "attach": "Attaching parts",
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
    bl_label = "Import Outfit"
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
            self.report({'WARNING'}, "An import is already running or nothing is queued")
            return {'CANCELLED'}
        from ..Shared import ui_targets
        busy = ui_targets.job_busy_message()
        if busy:
            _PENDING_JOB = None
            _PENDING_GEN = None
            self.report({'WARNING'}, busy)
            return {'CANCELLED'}
        self._kind = "Outfit"
        pending_gen, pending_job = _PENDING_GEN, _PENDING_JOB
        if pending_gen is not None:
            self._kind = pending_gen[2]
        self._label = self._import_label(context)
        if not ui_targets.job_begin(ui_targets.role_for_kind(self._kind) or "import",
                                    "Importing " + self._label, 0,
                                    "arc_outfits.staged_import_cancel"):
            _PENDING_JOB = None
            _PENDING_GEN = None
            self.report({'WARNING'}, ui_targets.job_busy_message() or "An import is already running")
            return {'CANCELLED'}
        try:
            if pending_gen is not None:
                make_gen, finish, self._kind = pending_gen
                _PENDING_GEN = None
                gen = make_gen(context)
            else:
                presets, finish = pending_job
                _PENDING_JOB = None
                from ..Shared import operators as _ops
                gen = _ops.iter_batch_import_instances(context, presets, staged=True)
        except Exception:
            _PENDING_GEN = None
            _PENDING_JOB = None
            ui_targets.job_end(0, 1, 0, [], status="failed")
            raise
        self._run = StagedRun(gen)
        self._finish = finish
        self._before = set(bpy.data.objects)
        self._before_meshes = set(bpy.data.meshes)
        try:
            from ..Shared import ui_lists
            self._snap = ui_lists.snapshot()
        except Exception:
            self._snap = None
        _ACTIVE = self._run
        wm = context.window_manager
        self._timer = None
        try:
            wm.arc_staged_cancel = False
            _publish(wm, self._run, True)
            self._timer_step = TICK_SECONDS
            self._timer = wm.event_timer_add(TICK_SECONDS, window=context.window)
            wm.modal_handler_add(self)
        except Exception:
            # Setup failed after the job began: release the job and the timer, then re-raise.
            if self._timer is not None:
                try:
                    wm.event_timer_remove(self._timer)
                except Exception:
                    pass
                self._timer = None
            self._run.cancel()
            _ACTIVE = None
            ui_targets.job_end(0, 1, 0, [], status="failed")
            raise
        return {'RUNNING_MODAL'}

    def _import_label(self, context) -> str:
        """Outfit display name for outfit runs ("Goalie"), else the kind ("Hair", "Weapon")."""
        kind = getattr(self, "_kind", "Outfit") or "Outfit"
        if kind != "Outfit":
            return kind
        try:
            from ..Shared import ui_targets
            scene = context.scene
            key = (getattr(scene, "arc_selected_outfit", "")
                   or getattr(scene, "arc_selected_outfit_browse", ""))
            self._outfit_key = str(key or "")
            self._outfit_label = ui_targets.outfit_label(context, key)
            return self._outfit_label or "Outfit Import"
        except Exception:
            return "Outfit Import"

    @staticmethod
    def _job_end(status, imported=0, failed=0, lines=(), left=None):
        try:
            from ..Shared import ui_targets
            if ui_targets.job_active() is not None:
                ui_targets.job_end(imported, failed, 0, list(lines), status=status, left=left)
        except Exception:
            pass

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
        if event.type != 'TIMER' or getattr(event, "timer", self._timer) is not self._timer:
            return {'RUNNING_MODAL'}
        if run.advance():
            if not run.begin_graphs(_job_owned("objects"), context.scene):
                return self._end(context, cancelled=False)
        self._retime(context, next_interval(run))
        _publish(wm, run, True)
        try:
            from ..Shared import ui_targets
            ui_targets.job_step(run.index, progress_text(run.stage, run.index, run.total, run.label),
                                total=run.total)
        except Exception:
            pass
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
        self._job_end("cancelled", left=1)

    def _fail(self, kind, error):
        """Failed run or finish: drop this run's objects, end the job failed, never "completed"."""
        try:
            from ..Shared import import_report
            import_report.abort()
        except Exception:
            pass
        discard_job_objects()
        label = getattr(self, "_label", "") or kind
        self._job_end("failed", failed=1, lines=[f"Failed: {label}: {error}"])
        self.report({'ERROR'}, f"{kind} import failed: {error}")
        return {'CANCELLED'}

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
        kind = getattr(self, "_kind", "Outfit") or "Outfit"
        if cancelled:
            try:
                from ..Shared import import_report
                import_report.abort()
            except Exception:
                pass
        if cancelled:
            # Remove the partial objects this run created so no placeholder is left behind.
            discard_job_objects()
            self._job_end("cancelled", lines=[f"{getattr(self, '_label', kind)}: stopped, partial objects removed"],
                          left=1)
            self.report({'WARNING'}, f"{kind} import stopped; partial objects were removed")
            return {'CANCELLED'}
        if run.error is not None:
            return self._fail(kind, run.error)
        sweep_placeholder(_job_owned("objects"))
        try:
            with _job_track():
                ok = self._finish(self, context, run.result) if self._finish is not None else True
        except Exception as exc:
            return self._fail(kind, exc)
        if ok is False:
            return self._fail(kind, "nothing was imported")
        step_errors = []
        for step in list(getattr(run, "after_success", ()) or ()):
            try:
                with _job_track():
                    for line in step() or ():
                        print("Arc import:", line)
            except Exception as exc:
                print("Arc import: cleanup after import failed:", exc)
                step_errors.append(str(exc) or exc.__class__.__name__)
        label0 = getattr(self, "_label", "") or kind
        if step_errors:
            # The new part is in, but the old part could not be removed: never a plain success.
            self._job_end("failed", imported=1, failed=1,
                          lines=[f"Failed: {label0}: old part not removed ({e})" for e in step_errors])
            self.report({'WARNING'}, f"{kind} imported, but the old part was not removed: {step_errors[0]}")
        else:
            self._job_end("completed", imported=1)
        try:
            from ..Shared import ui_targets
            new_objects = _job_owned("objects")
            label = getattr(self, "_label", "") or self._import_label(context)
            if kind == "Outfit" and not getattr(self, "_outfit_label", ""):
                # No catalog name captured at invoke: name it after its own new rig.
                rig = next((o for o in new_objects if getattr(o, "type", "") == "ARMATURE"), None)
                label = (ui_targets.clean_name(rig.name) if rig is not None else "") or label
            ui_targets.stamp(new_objects, ui_targets.new_import_id(),
                             kind=ui_targets.role_for_kind(kind), label=label)
        except Exception as exc:
            print("Arc import: objects not stamped:", exc)
        try:
            from ..Shared import ui_lists
            if getattr(self, "_snap", None) is not None:
                ui_lists.record_import(kind, self._snap)
            if kind == "Outfit":
                scene = context.scene
                ui_lists.record_recent("outfit", getattr(self, "_outfit_key", "")
                                       or getattr(scene, "arc_selected_outfit", "")
                                       or getattr(scene, "arc_selected_outfit_browse", ""))
        except Exception as exc:
            print("Arc import: history not recorded:", exc)
        return {'FINISHED'}


class ARC_OUTFITS_OT_StagedImportCancel(Operator):
    bl_idname = "arc_outfits.staged_import_cancel"
    bl_label = "Cancel Import"
    bl_description = "Stop the running import"

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


def after_success(step) -> bool:
    """Queue *step* (returns message lines) to run once the active run and its finish succeed.

    Returns False when no staged run is active; the caller then runs *step* itself. A cancel
    or failure drops the queue, so a replace keeps the old part.
    """
    run = _ACTIVE
    if run is None:
        return False
    run.after_success.append(step)
    return True


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
