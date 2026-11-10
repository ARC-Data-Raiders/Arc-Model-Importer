"""Shader parity cache and live weather controls."""
import bpy
from . import props


class ARC_OT_parity_refresh(bpy.types.Operator):
    bl_idname = "arc_outfits.parity_refresh"
    bl_label = "Refresh Shader Parity"

    def execute(self, context):
        props.ensure_scene(context.scene)
        count = 0
        for obj in context.scene.objects:
            if props.has_parity(obj):
                props.ensure_object(obj)
                count += 1
        props.activate()
        self.report({"INFO"}, "Refreshed shader parity on " + str(count) + " objects")
        return {"FINISHED"}


def draw_settings(layout, context):
    layout.prop(context.scene, "dr_parity_root")
    layout.operator("arc_outfits.parity_refresh")


class ARC_PT_shader_parity(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Arc Asset Importer"
    bl_label = "Shader Parity"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        draw_settings(self.layout, context)


classes = (ARC_OT_parity_refresh,)
