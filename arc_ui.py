"""ARC enemy selector, Codex art previews, and generic mesh import action."""
from __future__ import annotations

import os
from pathlib import Path

import bpy
from bpy.props import CollectionProperty, EnumProperty, StringProperty

from . import arc_catalog_data


_ARC_PREVIEW_DIR = Path(__file__).parent / "assets" / "arc_browser"
_CLASSES = []


def _reads_alertness(mat):
    from .materials.enemy_scan import is_scan_material
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
    from .materials.enemy_scan import STATE_VALUES, is_scan_material, setup_enemy_scan_display_material
    from .materials.enemy_surface import ensure_alertness_property
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
    from .materials.enemy_scan import is_scan_material, set_scan_state
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
        from . import utils
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


class ARC_OT_import_codex_enemy(bpy.types.Operator):
    """Choose and import an ARC PSK/PSKX/UEModel using the standard material path."""

    bl_idname = "arc_outfits.import_codex_enemy"
    bl_label = "Add Arc"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.psk;*.pskx;*.uemodel", options={"HIDDEN"})

    def invoke(self, context, _event):
        directory = _arc_enemy_directory()
        self.filepath = os.path.join(directory, "") if directory else ""
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        path = bpy.path.abspath(self.filepath or "")
        if not os.path.isfile(path):
            self.report({"ERROR"}, f"ARC mesh not found: {path or '(no file selected)'}")
            return {"CANCELLED"}
        if Path(path).suffix.casefold() not in {".psk", ".pskx", ".uemodel"}:
            self.report({"ERROR"}, "Choose a .psk, .pskx, or .uemodel ARC mesh")
            return {"CANCELLED"}

        from . import operators
        ok, message, objects = operators.import_psk_with_materials(path)
        if not ok:
            self.report({"ERROR"}, str(message)[:240])
            return {"CANCELLED"}

        selected = arc_catalog_data.by_key(getattr(context.scene, "arc_enemy_key", ""))
        if selected:
            key, label, _image = selected
            for obj in objects or ():
                if obj.type != "MESH":
                    continue
                obj["arc_codex_enemy_key"] = key
                obj["arc_codex_enemy_label"] = label
        apply_scan_state(objects or (), getattr(context.scene, "arc_scan_state", "SCANNING"))
        self.report({"INFO"}, str(message)[:240])
        return {"FINISHED"}


class ARC_OT_add_all(bpy.types.Operator):
    """Select multiple ARC meshes and add them through the normal import path."""

    bl_idname = "arc_outfits.add_all_arc"
    bl_label = "Add All Arc"
    bl_options = {"REGISTER", "UNDO"}

    directory: StringProperty(subtype="DIR_PATH")
    filepath: StringProperty(subtype="FILE_PATH", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    filter_glob: StringProperty(default="*.psk;*.pskx;*.uemodel", options={"HIDDEN"})

    def invoke(self, context, _event):
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
            self.report({"ERROR"}, "Select one or more ARC mesh files")
            return {"CANCELLED"}

        from . import operators
        imported = 0
        errors = []
        for path in paths:
            ok, message, objects = operators.import_psk_with_materials(path)
            if not ok:
                errors.append(f"{Path(path).name}: {message}")
                continue
            key = _codename_for_path(path) or getattr(context.scene, "arc_enemy_key", "")
            entry = arc_catalog_data.by_key(key)
            for obj in objects or ():
                if obj.type == "MESH" and entry:
                    obj["arc_codex_enemy_key"] = entry[0]
                    obj["arc_codex_enemy_label"] = next(
                        label for item_key, label, _tip in arc_catalog_data.enum_items()
                        if item_key == entry[0]
                    )
            apply_scan_state(objects or (), getattr(context.scene, "arc_scan_state", "SCANNING"))
            imported += 1
        if errors:
            self.report({"WARNING"}, f"Added {imported}/{len(paths)} ARC meshes; " + "; ".join(errors[:2])[:180])
        elif imported:
            self.report({"INFO"}, f"Added {imported} ARC meshes")
        return {"FINISHED"} if imported else {"CANCELLED"}


def _codename_for_path(path):
    import re
    stem = re.sub(r"[^A-Z0-9]", "", Path(path).stem.upper())
    for key, label, _image in arc_catalog_data.ARC_ENEMIES:
        variants = [key.replace("_", ""), label.split("(", 1)[0].strip().replace(" ", "")]
        if any(value and value in stem for value in variants):
            return key
    return ""


def draw(layout, context):
    scene = context.scene
    if not hasattr(scene, "arc_enemy_key"):
        return

    layout.prop(scene, "arc_enemy_key", text="ARC")
    selected = arc_catalog_data.by_key(scene.arc_enemy_key)
    if selected:
        _key, _label, filename = selected
        image_path = _pioneer_image_path(filename)
        if image_path:
            from . import weapon_ui
            weapon_ui.draw_arc_preview(layout, context, image_path)

    layout.operator("arc_outfits.import_codex_enemy", text="Add Arc", icon="IMPORT")
    layout.operator("arc_outfits.add_all_arc", text="Add All Arc", icon="DUPLICATE")
    layout.prop(scene, "arc_scan_state", text="Alert Level")
    layout.operator("arc_outfits.update_scan_state", icon="FILE_REFRESH")
    active = getattr(context, "active_object", None)
    if active is not None and "arc_alertness_state" in active:
        # Object property: keyframe it to animate idle/alert/combat transitions.
        layout.prop(active, '["arc_alertness_state"]', text="AlertnessState", slider=True)


def register():
    from .materials.enemy_scan import STATE_ITEMS
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
