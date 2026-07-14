"""
UI panels for the Arc Raiders Importer
"""

import os
import bpy


class ARC_PT_MainPanel(bpy.types.Panel):
    bl_label = "Arc Raiders"
    bl_idname = "ARC_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Arc Raiders"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        import_box = layout.box()
        import_box.label(text="Import", icon='IMPORT')
        import_box.operator("arc.import_single_psk", text="Import Single Model", icon='FILE')
        import_box.operator("arc.import_outfit_folder", text="Import Folder", icon='FILE_FOLDER')
        import_box.operator("arc.open_outfit_selector", text="Outfit Selector", icon='PRESET')

        rig_box = layout.box()
        rig_box.label(text="Rig", icon='ARMATURE_DATA')
        rig_box.operator("arc.merge_selected_armatures", text="Merge Selected Armatures", icon='BONE_DATA')

        settings_box = layout.box()
        settings_box.label(text="Settings", icon='PREFERENCES')

        pioneer_row = settings_box.row(align=True)
        if scene.arc_pioneer_root:
            pioneer_row.label(
                text="Root: " + os.path.basename(scene.arc_pioneer_root.rstrip("/\\")),
                icon='CHECKMARK',
            )
        else:
            pioneer_row.label(text="PioneerGame Folder", icon='FILE_FOLDER')
        pioneer_row.operator("arc.pick_pioneer_root", text="", icon='FILEBROWSER')
        if scene.arc_pioneer_root:
            pioneer_row.operator("arc.clear_pioneer_root", text="", icon='X')


classes = (
    ARC_PT_MainPanel,
)
