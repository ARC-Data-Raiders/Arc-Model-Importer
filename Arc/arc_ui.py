"""ARC enemy selector, Codex art previews, and generic mesh import action."""
from __future__ import annotations

import os
from pathlib import Path

import bpy
from bpy.props import CollectionProperty, EnumProperty, StringProperty

from . import arc_catalog_data


_ARC_PREVIEW_DIR = Path(__file__).parent.parent / "assets" / "arc_browser"
_CLASSES = []


def _reads_alertness(mat):
    from ..Shared.materials.enemy_scan import is_scan_material
    return bool(mat and (mat.get("arc_alertness_live") or is_scan_material(mat)))


def _scan_meshes(objects):
    return {m for m in objects
            if getattr(m, "type", "") == "MESH" and any(_reads_alertness(s.material) for s in m.material_slots)}


def _target_members(targets, target, scene):
    """Objects owned by the resolved enemy: its import, never siblings under a shared parent."""
    root = target.obj
    members = getattr(targets, "members", None) if targets else None
    if callable(members):
        try:
            return list(members(target, scene))
        except TypeError:
            return list(members(target))
    out = [root, *getattr(root, "children_recursive", ())]
    import_id = root.get("arc_import_id") if hasattr(root, "get") else None
    if import_id and targets and scene is not None:
        out.extend(targets.objects_of(scene, import_id))
    return out


def enemy_target(context):
    """The enemy the sidebar, poll and execute all use (selection, else last imported)."""
    targets = _targets()
    if targets is None:
        return None
    try:
        return targets.resolve(context, "enemy")
    except Exception:
        return None


def enemy_meshes(context):
    """Scan display meshes of the resolved enemy (nearest owned root, never the top ancestor)."""
    target = enemy_target(context)
    if target is not None and getattr(target, "obj", None) is not None:
        return _scan_meshes(_target_members(_targets(), target, getattr(context, "scene", None)))
    # No resolver result: only the selected objects and their own descendants.
    candidates = list(getattr(context, "selected_objects", ()) or ())
    if not candidates and getattr(context, "active_object", None):
        candidates = [context.active_object]
    objects = set()
    for obj in candidates:
        objects |= _scan_meshes([obj, *getattr(obj, "children_recursive", ())])
    return objects


def _selected_arc_meshes(context):
    """Kept for callers: the resolved enemy's scan meshes."""
    return enemy_meshes(context)


def _targets():
    try:
        from ..Shared import ui_targets
        return ui_targets
    except Exception:
        return None


def _busy_message():
    """Text when another import job runs (D5), else ""."""
    if _ADD_ALL.get("active"):
        return "Wait for Importing all enemies to finish"
    targets = _targets()
    try:
        if targets and targets.job_active():
            return targets.job_busy_message() or "Another import is running"
    except Exception:
        return ""
    return ""


def display_label(key):
    """Clean enemy name for a catalog key (the code name stays in the tooltip)."""
    return arc_catalog_data.display_label(key)


def _enemy_meshes(context):
    """Selected enemy, else the enemy the sidebar targets (last imported)."""
    return enemy_meshes(context)


def apply_scan_state(objects, state):
    """Set the ARC's per-object AlertnessState read by its display and emissive graphs."""
    from ..Shared.materials.enemy_scan import STATE_VALUES, is_scan_material, setup_enemy_scan_display_material
    from ..Shared.materials.enemy_surface import ensure_alertness_property
    state = state if state in STATE_VALUES else "SCANNING"
    objects = {obj for obj in (objects or ()) if obj.type == "MESH"}
    for obj in objects:
        ensure_alertness_property(obj, STATE_VALUES[state])
    legacy = set()
    for obj in objects:
        for slot in obj.material_slots:
            mat = slot.material
            if not is_scan_material(mat) or mat.get("arc_alertness_live"):
                continue
            if mat.get("dr_native_family") == "display":
                # Earlier native graphs baked the state; rebuild them to read the object property.
                setup_enemy_scan_display_material(mat, str(mat.get("arc_mi_path", "")), obj=obj)
            if not mat.get("arc_alertness_live"):
                legacy.add(obj)
    return len(objects) if not legacy else len(objects) + _apply_legacy_scan_state(legacy, state)


def _apply_legacy_scan_state(objects, state):
    """Isolate shared fallback scan materials, which bake the state, before applying it per ARC."""
    from ..Shared.materials.enemy_scan import is_scan_material, set_scan_state
    objects = set(objects or ())
    for obj in objects:
        if obj.type == "MESH" and obj.data.users > 1 and any(
            other not in objects and other.type == "MESH" and other.data == obj.data
            for other in bpy.data.objects
        ):
            # A linked duplicate also shares its DATA-linked material slots.
            obj.data = obj.data.copy()
    mats = {slot.material for obj in objects if obj.type == "MESH"
            for slot in obj.material_slots
            if is_scan_material(slot.material) and not slot.material.get("arc_alertness_live")}
    changed = 0
    for original in mats:
        # Material datablocks can be shared by two imported enemies.
        outside_users = any(obj not in objects and obj.type == "MESH" and
                            any(s.material == original for s in obj.material_slots)
                            for obj in bpy.data.objects)
        mat = original.copy() if outside_users else original
        if outside_users:
            for obj in objects:
                for slot in obj.material_slots:
                    if slot.material == original:
                        slot.material = mat
        if set_scan_state(mat, state):
            changed += 1
    return changed


class ARC_OT_update_scan_state(bpy.types.Operator):
    """Apply the selected alert level to the selected ARC hierarchy (eye display and alertness emissive)."""

    bl_idname = "arc_outfits.update_scan_state"
    bl_label = "Apply Alert Level"
    bl_description = "Show the chosen alert level on the selected enemy's eyes and lights, or on the last imported enemy"
    bl_options = {"REGISTER", "UNDO"}

    _poll_memo = [None, 0.0, False]

    @classmethod
    def poll(cls, context):
        # The hierarchy walk is re-run at most once per second for an unchanged selection.
        import time
        memo = cls._poll_memo
        key = (getattr(getattr(context, "active_object", None), "name", ""),
               len(getattr(context, "selected_objects", ()) or ()))
        now = time.monotonic()
        if memo[0] != key or now - memo[1] > 1.0:
            try:
                found = bool(_enemy_meshes(context))
            except Exception:
                found = False
            memo[:] = [key, now, found]
        if memo[2]:
            return True
        targets = _targets()
        cls.poll_message_set(targets.poll_message("enemy") if targets else "Select an imported enemy first")
        return False

    def execute(self, context):
        count = apply_scan_state(_enemy_meshes(context), context.scene.arc_scan_state)
        if not count:
            self.report({"WARNING"}, "Select an imported enemy with eye or alert lights first")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Set the alert level on {count} enemy part(s)")
        return {"FINISHED"}


def _pioneer_image_path(filename: str) -> Path | None:
    path = _ARC_PREVIEW_DIR / filename
    return path if path.is_file() else None


def _arc_enemy_directory() -> str:
    """Use the selected export's Enemies folder as the initial file-browser path."""
    try:
        from ..Shared import utils
        root = utils.get_pioneer_root()
        content = utils.find_content_dir(root) if root else ""
        if content:
            candidate = os.path.join(content, "Pioneer", "Enemies")
            if os.path.isdir(candidate):
                return candidate
        if root and os.path.isdir(root):
            return root
    except Exception:
        pass
    return ""


_MESH_EXTS = (".uemodel", ".psk", ".pskx")


def resolve_arc_meshes(context, key):
    """Mesh files of one catalog ARC: Pak extraction first, then the on-disk export. No prompt."""
    rels = list(arc_catalog_data.mesh_rels(key))
    if not rels:
        return []
    scene = context.scene
    try:
        from ..Shared import pak_extract
        if pak_extract.configured_dir(scene):
            found = [p for p in (pak_extract.ensure_rels(scene, rels, reuse_current=True) or [])
                     if p and os.path.isfile(p)]
            if found:
                return found
    except Exception as exc:
        print(f"[Arc] Pak extraction for {key} failed: {exc}")
    found = []
    try:
        from ..Shared import utils
        root = utils.get_pioneer_root()
        content = utils.find_content_dir(root) if root else ""
    except Exception:
        content = ""
    if content:
        for rel in rels:
            base = os.path.join(content, "Pioneer", *rel.split("/"))
            hit = next((base + ext for ext in _MESH_EXTS if os.path.isfile(base + ext)), "")
            if hit:
                found.append(hit)
    return found


def _bind_rigid_parts(objects):
    """Bone-parented or unweighted meshes: one full-weight group + Armature modifier."""
    from ..Shared import uemodel_import
    for obj in objects:
        if obj.type != "MESH" or obj.vertex_groups or not len(obj.data.vertices):
            continue
        arm = obj.parent if obj.parent is not None and obj.parent.type == "ARMATURE" else None
        if arm is None:
            arm = next((o for o in objects if o.type == "ARMATURE"), None)
        if arm is None or not arm.data.bones:
            continue
        bone = obj.parent_bone if obj.parent_type == "BONE" and obj.parent_bone in arm.data.bones else arm.data.bones[0].name
        uemodel_import.rigid_bind_to_bone(obj, arm, bone)


def _import_arc_paths(context, paths, key):
    """Import mesh files as one ARC. Returns (imported_count, errors)."""
    from ..Shared import operators
    entry = arc_catalog_data.by_key(key)
    imported, errors = 0, []
    targets = _targets()
    import_id = targets.new_import_id() if targets else ""
    for path in paths:
        ok, message, objects = operators.import_psk_with_materials(path)
        if not ok:
            errors.append(f"{Path(path).name}: {message}")
            continue
        _bind_rigid_parts(objects or ())
        if entry:
            for obj in objects or ():
                if obj.type in ("MESH", "ARMATURE", "EMPTY"):
                    obj["arc_codex_enemy_key"] = entry[0]
                    obj["arc_codex_enemy_label"] = display_label(entry[0]) or entry[1]
        apply_scan_state(objects or (), getattr(context.scene, "arc_scan_state", "SCANNING"))
        if targets and objects:
            try:
                targets.stamp(list(objects), import_id, "enemy", display_label(key) or key)
            except Exception as exc:
                print(f"[Arc] Could not tag {key}: {exc}")
        imported += 1
    return imported, errors


class ARC_OT_import_codex_enemy(bpy.types.Operator):
    """Import the ARC selected in the list. A file browser opens only when its mesh is not found."""

    bl_idname = "arc_outfits.import_codex_enemy"
    bl_label = "Import Enemy"
    bl_description = "Import the enemy chosen above. A file browser opens only when its mesh is not found"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    filter_glob: StringProperty(default="*.psk;*.pskx;*.uemodel", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        if not arc_catalog_data.by_key(getattr(context.scene, "arc_enemy_key", "")):
            cls.poll_message_set("Select an enemy first")
            return False
        busy = _busy_message()
        if busy:
            cls.poll_message_set(busy)
            return False
        return True

    def invoke(self, context, _event):
        busy = _busy_message()
        if busy:
            self.report({"WARNING"}, busy)
            return {"CANCELLED"}
        key = getattr(context.scene, "arc_enemy_key", "")
        paths = resolve_arc_meshes(context, key)
        if paths:
            return self._import(context, paths, key)
        self.report({"WARNING"}, f"No mesh found for '{key}' in the Pak index or export; choose the file")
        directory = _arc_enemy_directory()
        self.filepath = os.path.join(directory, "") if directory else ""
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        busy = _busy_message()
        if busy:
            self.report({"WARNING"}, busy)
            return {"CANCELLED"}
        key = getattr(context.scene, "arc_enemy_key", "")
        path = bpy.path.abspath(self.filepath or "")
        if os.path.isfile(path):
            if Path(path).suffix.casefold() not in {".psk", ".pskx", ".uemodel"}:
                self.report({"ERROR"}, "Choose a .psk, .pskx, or .uemodel ARC mesh")
                return {"CANCELLED"}
            return self._import(context, [path], key)
        paths = resolve_arc_meshes(context, key)
        if not paths:
            self.report({"ERROR"}, f"Enemy mesh not found for {display_label(key) or key}. Choose the Arc Raiders game folder first")
            return {"CANCELLED"}
        return self._import(context, paths, key)

    def _import(self, context, paths, key):
        from ..Shared import ui_lists
        snap = ui_lists.snapshot()
        imported, errors = _import_arc_paths(context, paths, key)
        if imported:
            ui_lists.record_import("Enemy", snap)
            ui_lists.record_recent("enemy", key)
        if errors:
            self.report({"WARNING" if imported else "ERROR"}, "; ".join(errors[:2])[:240])
        elif imported:
            entry = arc_catalog_data.by_key(key)
            self.report({"INFO"}, f"Imported {display_label(key) or key}")
        return {"FINISHED"} if imported else {"CANCELLED"}


_ADD_ALL = {"active": False, "cancel": False, "done": 0, "total": 0, "label": ""}
REPORT_KEY = "arc_add_all_enemies_report"


def add_all_status():
    """Progress of a running Import All Enemies, or None."""
    return dict(_ADD_ALL) if _ADD_ALL["active"] else None


def _redraw(context):
    for area in getattr(getattr(context, "screen", None), "areas", ()):
        if area.type == "VIEW_3D":
            area.tag_redraw()


class ARC_OT_cancel_add_all(bpy.types.Operator):
    bl_idname = "arc_outfits.cancel_add_all_arc"
    bl_label = "Cancel"
    bl_description = "Stop importing enemies after the current one. Enemies already imported are kept"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return bool(_ADD_ALL["active"])

    def execute(self, context):
        _ADD_ALL["cancel"] = True
        return {"FINISHED"}


class ARC_OT_add_all(bpy.types.Operator):
    """Import every ARC in the list, one per step. Esc or Cancel stops after the current one."""

    bl_idname = "arc_outfits.add_all_arc"
    bl_label = "Import All Enemies"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        targets = _targets()
        if _ADD_ALL["active"] or (targets and targets.job_active()):
            cls.poll_message_set("Another import is running")
            return False
        return True

    directory: StringProperty(subtype="DIR_PATH", options={"SKIP_SAVE"})
    filepath: StringProperty(subtype="FILE_PATH", options={"HIDDEN", "SKIP_SAVE"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    filter_glob: StringProperty(default="*.psk;*.pskx;*.uemodel", options={"HIDDEN"})

    def _catalog(self, context):
        imported, errors, total = 0, [], 0
        for key, label, _image in arc_catalog_data.ARC_ENEMIES:
            total += 1
            paths = resolve_arc_meshes(context, key)
            if not paths:
                errors.append(f"{label}: mesh not found")
                continue
            done, errs = _import_arc_paths(context, paths, key)
            errors.extend(errs)
            imported += 1 if done else 0
        return imported, errors, total

    bl_description = "Import every enemy in the list, one at a time. Press Esc or Cancel to stop"

    def invoke(self, context, _event):
        # The dialog's button runs execute, which imports from the game install.
        return context.window_manager.invoke_props_dialog(
            self, title="Import all %d enemies?" % len(arc_catalog_data.ARC_ENEMIES), confirm_text="Import All Enemies")

    def draw(self, context):
        col = self.layout.column()
        col.label(text="Imports every enemy in the list, one at a time", icon="QUESTION")
        col.label(text="Progress shows in the sidebar. Press Esc or Cancel to stop")

    def execute(self, context):
        paths = [os.path.join(bpy.path.abspath(self.directory), item.name) for item in self.files]
        if not paths and self.filepath:
            paths = [bpy.path.abspath(self.filepath)]
        paths = [path for path in paths if Path(path).suffix.casefold() in {".psk", ".pskx", ".uemodel"}]
        if not paths and not bpy.app.background and context.window is not None:
            return self._start(context)
        if not paths:
            imported, errors, total = self._catalog(context)
            if not imported:
                self.report({"ERROR"}, "No enemy meshes found. Choose the Arc Raiders game folder first")
                return {"CANCELLED"}
            return self._finish(imported, total, errors)
        imported, errors = 0, []
        for path in paths:
            key = _codename_for_path(path) or getattr(context.scene, "arc_enemy_key", "")
            done, errs = _import_arc_paths(context, [path], key)
            imported += done
            errors.extend(errs)
        return self._finish(imported, len(paths), errors)

    def _start(self, context):
        targets = _targets()
        if _ADD_ALL["active"] or (targets and targets.job_active()):
            self.report({"WARNING"}, (targets.job_busy_message() if targets else "") or "Another import is running")
            return {"CANCELLED"}
        from ..Shared import ui_lists
        self._queue = list(arc_catalog_data.ARC_ENEMIES)
        if targets and not targets.job_begin("enemy", "Importing all enemies", len(self._queue),
                                             "arc_outfits.cancel_add_all_arc"):
            self.report({"WARNING"}, "Another import is running")
            return {"CANCELLED"}
        self._stopped = False
        self._imported, self._failed, self._errors = 0, 0, []
        self._snap = ui_lists.snapshot()
        first = self._queue[0][1] if self._queue else ""
        _ADD_ALL.update(active=True, cancel=False, done=0, total=len(self._queue), label=first)
        if REPORT_KEY in context.scene:
            del context.scene[REPORT_KEY]
        wm = context.window_manager
        wm.progress_begin(0, max(1, len(self._queue)))
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        _redraw(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC" and event.value == "PRESS":
            _ADD_ALL["cancel"] = True
        if _ADD_ALL["cancel"]:
            return self._stop(context, True)
        if event.type != "TIMER" or event.timer is not getattr(self, "_timer", None):
            return {"PASS_THROUGH"}
        if not self._queue:
            return self._stop(context, False)
        try:
            key, label, _image = self._queue.pop(0)
            name = display_label(key) or label
            targets = _targets()
            if targets:
                targets.job_step(_ADD_ALL["done"], name)
            paths = resolve_arc_meshes(context, key)
            if not paths:
                self._failed += 1
                self._errors.append(f"{name}: mesh not found")
            else:
                done, errs = _import_arc_paths(context, paths, key)
                self._imported += 1 if done else 0
                self._failed += 0 if done else 1
                self._errors.extend(errs)
            _ADD_ALL["done"] += 1
            _ADD_ALL["label"] = display_label(self._queue[0][0]) if self._queue else ""
            if targets:
                targets.job_step(_ADD_ALL["done"], _ADD_ALL["label"])
            context.window_manager.progress_update(_ADD_ALL["done"])
        except BaseException:
            self._stop(context, True)
            raise
        _redraw(context)
        return {"RUNNING_MODAL"}

    def _end_timer(self, context):
        try:
            wm = context.window_manager
            if getattr(self, "_timer", None) is not None:
                wm.event_timer_remove(self._timer)
                self._timer = None
            wm.progress_end()
        finally:
            _ADD_ALL.update(active=False, cancel=False)

    def _end_job(self, cancelled):
        if getattr(self, "_stopped", True):
            return
        self._stopped = True
        targets = _targets()
        if targets:
            lines = [f"Failed: {e}" for e in self._errors]
            # R5: unattempted items are left, not skipped; job_end keeps the planned total.
            skipped = 0
            if cancelled:
                lines.append(f"Stopped with {len(self._queue)} left")
            if cancelled:
                status = "cancelled"
            elif self._failed and not self._imported:
                status = "failed"
            else:
                status = "completed"
            try:
                targets.job_end(self._imported, self._failed, skipped, lines, status=status,
                                left=len(self._queue) if cancelled else None)
            except TypeError:
                targets.job_end(self._imported, self._failed, skipped, lines)

    def cancel(self, context):
        # Blender ended the modal itself (file load, window close): free the timer and the job.
        try:
            self._end_timer(context)
        except Exception:
            pass
        finally:
            self._end_job(True)

    def _stop(self, context, cancelled):
        from ..Shared import ui_lists
        try:
            self._end_timer(context)
            if self._imported:
                ui_lists.record_import("All Enemies", self._snap)
            text = f"{self._imported} imported, {self._failed} failed"
            if cancelled:
                text += f", stopped with {len(self._queue)} left"
            context.scene[REPORT_KEY] = text
        finally:
            self._end_job(cancelled)
            _redraw(context)
        return self._finish(self._imported, _ADD_ALL["total"], self._errors)

    def _finish(self, imported, total, errors):
        if errors:
            self.report({"WARNING"}, f"Imported {imported} of {total} enemies; " + "; ".join(errors[:2])[:180])
        elif imported:
            self.report({"INFO"}, f"Imported {imported} enemies")
        return {"FINISHED"} if imported else {"CANCELLED"}


def _codename_for_path(path):
    import re
    stem = re.sub(r"[^A-Z0-9]", "", Path(path).stem.upper())
    for key, label, _image in arc_catalog_data.ARC_ENEMIES:
        variants = [key.replace("_", ""), label.split("(", 1)[0].strip().replace(" ", "")]
        if any(value and value in stem for value in variants):
            return key
    return ""




def register():
    from ..Shared.materials.enemy_scan import STATE_ITEMS
    if not hasattr(bpy.types.Scene, "arc_scan_state"):
        bpy.types.Scene.arc_scan_state = EnumProperty(
            name="Alert Level", description="Alert state shown on the selected enemy's eyes and lights",
            items=STATE_ITEMS, default="SCANNING",
        )
    if not hasattr(bpy.types.Scene, "arc_enemy_key"):
        bpy.types.Scene.arc_enemy_key = EnumProperty(
            name="Enemy", description="Enemy to import",
            items=arc_catalog_data.enum_items,
            default=0,
        )
    _CLASSES[:] = [ARC_OT_import_codex_enemy, ARC_OT_add_all, ARC_OT_cancel_add_all, ARC_OT_update_scan_state]
    for cls in _CLASSES:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass


def unregister():
    for cls in reversed(_CLASSES or [ARC_OT_import_codex_enemy, ARC_OT_add_all, ARC_OT_cancel_add_all, ARC_OT_update_scan_state]):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    _CLASSES.clear()
    if hasattr(bpy.types.Scene, "arc_scan_state"):
        del bpy.types.Scene.arc_scan_state
    if hasattr(bpy.types.Scene, "arc_enemy_key"):
        try:
            del bpy.types.Scene.arc_enemy_key
        except Exception:
            pass
