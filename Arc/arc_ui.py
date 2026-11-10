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


def _selected_arc_meshes(context):
    """Only the selected ARC hierarchy, never all scan displays in the scene."""
    candidates = list(getattr(context, "selected_objects", ()) or ())
    if not candidates and getattr(context, "active_object", None):
        candidates = [context.active_object]
    objects = set()
    for obj in candidates:
        root = obj
        while root.parent is not None:
            root = root.parent
        for member in [root, *root.children_recursive]:
            if member.type == "MESH" and any(_reads_alertness(s.material) for s in member.material_slots):
                objects.add(member)
    return objects


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
    bl_label = "Set Selected ARC Alert Level"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        count = apply_scan_state(_selected_arc_meshes(context), context.scene.arc_scan_state)
        if not count:
            self.report({"WARNING"}, "Select an ARC with an eye display or alertness emissive material")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Set the alert level on {count} ARC part(s)")
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
    for path in paths:
        ok, message, objects = operators.import_psk_with_materials(path)
        if not ok:
            errors.append(f"{Path(path).name}: {message}")
            continue
        _bind_rigid_parts(objects or ())
        if entry:
            for obj in objects or ():
                if obj.type == "MESH":
                    obj["arc_codex_enemy_key"] = entry[0]
                    obj["arc_codex_enemy_label"] = entry[1]
        apply_scan_state(objects or (), getattr(context.scene, "arc_scan_state", "SCANNING"))
        imported += 1
    return imported, errors


class ARC_OT_import_codex_enemy(bpy.types.Operator):
    """Import the ARC selected in the list. A file browser opens only when its mesh is not found."""

    bl_idname = "arc_outfits.import_codex_enemy"
    bl_label = "Add Arc"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    filter_glob: StringProperty(default="*.psk;*.pskx;*.uemodel", options={"HIDDEN"})

    def invoke(self, context, _event):
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
        key = getattr(context.scene, "arc_enemy_key", "")
        path = bpy.path.abspath(self.filepath or "")
        if os.path.isfile(path):
            if Path(path).suffix.casefold() not in {".psk", ".pskx", ".uemodel"}:
                self.report({"ERROR"}, "Choose a .psk, .pskx, or .uemodel ARC mesh")
                return {"CANCELLED"}
            return self._import(context, [path], key)
        paths = resolve_arc_meshes(context, key)
        if not paths:
            self.report({"ERROR"}, f"ARC mesh not found for '{key}'. Set the Steam Install root or Paks folder.")
            return {"CANCELLED"}
        return self._import(context, paths, key)

    def _import(self, context, paths, key):
        imported, errors = _import_arc_paths(context, paths, key)
        if errors:
            self.report({"WARNING" if imported else "ERROR"}, "; ".join(errors[:2])[:240])
        elif imported:
            entry = arc_catalog_data.by_key(key)
            self.report({"INFO"}, f"Added {entry[1] if entry else key}")
        return {"FINISHED"} if imported else {"CANCELLED"}


class ARC_OT_add_all(bpy.types.Operator):
    """Import every ARC in the list. A file browser opens only when no catalog mesh is found."""

    bl_idname = "arc_outfits.add_all_arc"
    bl_label = "Add All Arc"
    bl_options = {"REGISTER", "UNDO"}

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

    def invoke(self, context, _event):
        imported, errors, total = self._catalog(context)
        if imported:
            return self._finish(imported, total, errors)
        directory = _arc_enemy_directory()
        self.directory = os.path.join(directory, "") if directory else ""
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        paths = [os.path.join(bpy.path.abspath(self.directory), item.name) for item in self.files]
        if not paths and self.filepath:
            paths = [bpy.path.abspath(self.filepath)]
        paths = [path for path in paths if Path(path).suffix.casefold() in {".psk", ".pskx", ".uemodel"}]
        if not paths:
            imported, errors, total = self._catalog(context)
            if not imported:
                self.report({"ERROR"}, "No ARC meshes found. Set the Steam Install root or Paks folder.")
                return {"CANCELLED"}
            return self._finish(imported, total, errors)
        imported, errors = 0, []
        for path in paths:
            key = _codename_for_path(path) or getattr(context.scene, "arc_enemy_key", "")
            done, errs = _import_arc_paths(context, [path], key)
            imported += done
            errors.extend(errs)
        return self._finish(imported, len(paths), errors)

    def _finish(self, imported, total, errors):
        if errors:
            self.report({"WARNING"}, f"Added {imported}/{total} ARC; " + "; ".join(errors[:2])[:180])
        elif imported:
            self.report({"INFO"}, f"Added {imported} ARC")
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
            name="Alert Level", items=STATE_ITEMS, default="SCANNING",
        )
    if not hasattr(bpy.types.Scene, "arc_enemy_key"):
        bpy.types.Scene.arc_enemy_key = EnumProperty(
            name="ARC",
            items=arc_catalog_data.enum_items,
            default=0,
        )
    _CLASSES[:] = [ARC_OT_import_codex_enemy, ARC_OT_add_all, ARC_OT_update_scan_state]
    for cls in _CLASSES:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass


def unregister():
    for cls in reversed(_CLASSES or [ARC_OT_import_codex_enemy, ARC_OT_add_all, ARC_OT_update_scan_state]):
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
