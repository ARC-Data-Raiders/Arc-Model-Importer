"""Arc Look: the Material properties panel, reset and look preset operators.

Draws ``user_controls.schema(material)``. ``draw_schema(layout, context, material)`` and
``draw_reset_row(layout)`` are the shared drawers: the node editor "Arc" tab (G1) and the
sidebar (G5) reach them through getattr. Draw code reads node sockets only (no file IO).
"""
import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from .materials import user_controls

_EPS = 1e-4


def _differs(a, b):
    if a is None or b is None:
        return False
    if isinstance(a, (tuple, list)):
        return any(abs(float(x) - float(y)) > _EPS for x, y in zip(a, b))
    return abs(float(a) - float(b)) > _EPS


def schema_rows(material):
    """Schema rows of *material*; [] on any graph read error."""
    try:
        return user_controls.schema(material)
    except Exception:
        return []


def _root(context):
    """Asset root from the resolved target, else from the active object."""
    try:
        from . import ui_targets
        target = ui_targets.resolve(context, "asset")
    except Exception:
        target = None
    obj = getattr(target, "obj", None)
    from . import shader_control_ops as sco
    if obj is None:
        obj = getattr(context, "object", None) or sco.target_object(context)
    return sco.asset_root(obj) if obj is not None else None


def asset_materials(root):
    """Node materials on the asset root and its children (in slot order, no duplicates)."""
    if root is None:
        return []
    out = []
    for ob in [root] + list(root.children_recursive):
        for slot in getattr(ob, "material_slots", ()):
            mat = slot.material
            if mat is not None and getattr(mat, "use_nodes", False) and mat not in out:
                out.append(mat)
    return out


def _material(context, name):
    if name:
        return bpy.data.materials.get(name)
    return None


def _after_edit(context):
    from . import shader_control_ops as sco
    try:
        sco.clear_discover_cache()
        sco._resync(context)
    except Exception:
        pass
    sco.tag_redraw(context)


def reset_look(root, material=None):
    """Imported look back on *material* (or every material of *root*). Whole-asset reset also
    drops the saved tweaks so a rebuild does not bring the edits back. Returns sockets written."""
    mats = [material] if material is not None else asset_materials(root)
    count = 0
    for mat in mats:
        count += user_controls.schema_reset(mat)
    if material is None and root is not None:
        try:
            from . import shader_tweaks
            shader_tweaks.reset(root)
        except Exception:
            pass
    return count


def look_values(root):
    """{material name: {control id: value}} of every material of *root*."""
    out = {}
    for mat in asset_materials(root):
        row = {r["id"]: r["value"] for r in schema_rows(mat)}
        if row:
            out[mat.name] = row
    return out


def apply_preset(root, data):
    """Write a preset onto the asset; returns sockets written."""
    from . import look_presets
    count = 0
    for mat in asset_materials(root):
        for cid, value in look_presets.values_for(data, mat.name).items():
            count += user_controls.schema_set(mat, cid, value)
    return count


class ARC_OT_look_reset_imported(bpy.types.Operator):
    bl_idname = "arc.look_reset_imported"
    bl_label = "Reset to Imported Look"
    bl_description = "Return every color and wear control to how it was imported"
    bl_options = {"REGISTER", "UNDO"}

    material: StringProperty(default="", options={"SKIP_SAVE", "HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _root(context) is not None or getattr(context, "material", None) is not None

    def execute(self, context):
        mat = _material(context, self.material)
        root = _root(context)
        count = reset_look(root, mat)
        _after_edit(context)
        if not count:
            self.report({"INFO"}, "Nothing to reset")
        return {"FINISHED"}


class ARC_OT_look_control_reset(bpy.types.Operator):
    bl_idname = "arc.look_control_reset"
    bl_label = "Reset Control"
    bl_description = "Return this control to its imported value"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    material: StringProperty(default="", options={"SKIP_SAVE", "HIDDEN"})
    control: StringProperty(default="", options={"SKIP_SAVE", "HIDDEN"})

    def execute(self, context):
        mat = _material(context, self.material)
        if mat is None or not self.control:
            return {"CANCELLED"}
        user_controls.schema_reset(mat, ids={self.control})
        root = _root(context)
        if root is not None:
            try:
                from . import shader_tweaks
                for scope in list(shader_tweaks.get(root)):
                    shader_tweaks.reset(root, scope, self.control)
            except Exception:
                pass
        _after_edit(context)
        return {"FINISHED"}


class ARC_OT_look_preset_save(bpy.types.Operator, ExportHelper):
    bl_idname = "arc.look_preset_save"
    bl_label = "Save Look Preset..."
    bl_description = "Save this asset's colors and wear to a preset file"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _root(context) is not None

    def invoke(self, context, event):
        from . import look_presets
        try:
            self.filepath = look_presets.preset_path(look_presets.preset_dir(), "Look")
        except Exception:
            pass
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        import os
        from . import look_presets
        root = _root(context)
        values = look_values(root)
        if not values:
            self.report({"WARNING"}, "This asset has no colors or wear to save")
            return {"CANCELLED"}
        name = os.path.splitext(os.path.basename(self.filepath))[0]
        try:
            look_presets.save(self.filepath, look_presets.build(name, values))
        except OSError:
            self.report({"ERROR"}, "Could not save the preset file")
            return {"CANCELLED"}
        self.report({"INFO"}, "Saved look preset %s" % name)
        return {"FINISHED"}


class ARC_OT_look_preset_load(bpy.types.Operator, ImportHelper):
    bl_idname = "arc.look_preset_load"
    bl_label = "Load Look Preset"
    bl_description = "Apply colors and wear from a saved preset file"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _root(context) is not None

    def invoke(self, context, event):
        import os
        from . import look_presets
        try:
            self.filepath = look_presets.preset_dir() + os.sep
        except Exception:
            pass
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from . import look_presets
        try:
            data = look_presets.load(self.filepath)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        count = apply_preset(_root(context), data)
        _after_edit(context)
        if not count:
            self.report({"WARNING"}, "The preset does not match this asset")
        return {"FINISHED"}


def draw_reset_row(layout, material=None):
    """Reset to Imported Look + preset buttons (sidebar, node tab, Material panel)."""
    row = layout.row(align=True)
    op = row.operator(ARC_OT_look_reset_imported.bl_idname, icon="LOOP_BACK")
    op.material = getattr(material, "name", "") if material is not None else ""
    row = layout.row(align=True)
    row.operator(ARC_OT_look_preset_save.bl_idname, icon="FILE_TICK")
    row.operator(ARC_OT_look_preset_load.bl_idname, icon="FILEBROWSER")


def draw_schema(layout, context, material, rows=None):
    """Controls of *material* grouped by schema panel, each with a reset button when edited.
    Returns the number of controls drawn."""
    rows = schema_rows(material) if rows is None else rows
    if not rows:
        return 0
    socks = user_controls.schema_sockets(material)
    current = None
    body = layout
    for r in rows:
        if r["panel"] != current:
            current = r["panel"]
            key = "arc_look_" + "".join(c for c in current.lower() if c.isalnum())
            header, body = layout.panel(key, default_closed=False)
            header.label(text=current)
        if body is None:
            continue
        sock = socks.get(r["id"])
        if sock is None:
            continue
        row = body.row(align=True)
        row.prop(sock, "default_value", text=r["label"])
        if _differs(r["value"], r["imported"]):
            op = row.operator(ARC_OT_look_control_reset.bl_idname, text="", icon="LOOP_BACK")
            op.material = material.name
            op.control = r["id"]
    return len(rows)


class ARC_PT_material_arc_look(bpy.types.Panel):
    bl_idname = "ARC_PT_material_arc_look"
    bl_label = "Arc Look"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"
    bl_order = -1           # before the render engine's Surface panel

    @classmethod
    def poll(cls, context):
        return bool(schema_rows(getattr(context, "material", None)))

    def draw(self, context):
        layout = self.layout
        mat = context.material
        draw_reset_row(layout, mat)
        draw_schema(layout, context, mat)


classes = (
    ARC_OT_look_reset_imported,
    ARC_OT_look_control_reset,
    ARC_OT_look_preset_save,
    ARC_OT_look_preset_load,
    ARC_PT_material_arc_look,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            bpy.utils.unregister_class(cls)
            bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
