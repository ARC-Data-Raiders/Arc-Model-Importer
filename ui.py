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

        import_box.separator()
        import_box.label(text="Outfit Selector:", icon='PRESET')

        from . import properties, importing
        csv_path = importing.get_outfit_csv_path(context)
        if not csv_path:
            import_box.label(text="No CSV found — set PioneerGame root or browse in Settings.", icon='ERROR')
        else:
            search_row = import_box.row(align=True)
            search_row.prop(scene, "arc_outfit_search", text="", icon='VIEWZOOM')
            if scene.arc_outfit_search:
                search_row.operator("arc.clear_outfit_search", text="", icon='X')

            search_lower = scene.arc_outfit_search.lower()
            all_items = properties.make_outfit_selector_items(None, context)
            filtered = [(k, l) for k, l, d in all_items if k != 'NONE' and (not search_lower or search_lower in l.lower())]
            filtered.sort(key=lambda t: (1 if "unnamed" in t[1].lower() else 0, t[1].lower()))

            if filtered:
                list_box = import_box.box()
                selected_key = scene.arc_selected_outfit
                for key, label in filtered[:4]:
                    row = list_box.row()
                    icon = 'RADIOBUT_ON' if key == selected_key else 'RADIOBUT_OFF'
                    op = row.operator("arc.select_outfit", text=label, icon=icon)
                    op.outfit_key = key
                if len(filtered) > 4:
                    list_box.label(text=f"... and {len(filtered) - 4} more (refine search)")

            import_box.separator()
            import_box.prop(scene, "arc_selected_outfit_browse", text="Browse All")

            load_row = import_box.row(align=True)
            load_row.operator("arc.load_outfit", text="Load Outfit", icon='IMPORT')

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

        csv_path = importing.get_outfit_csv_path(context)
        csv_row = settings_box.row(align=True)
        if scene.arc_outfit_csv_path:
            csv_row.label(text=f"CSV: {os.path.basename(scene.arc_outfit_csv_path)}", icon='FILE')
            csv_row.operator("arc.clear_outfit_csv", text="", icon='LOOP_BACK')
        else:
            name = os.path.basename(csv_path) if csv_path else "(not found)"
            csv_row.label(text=f"CSV: {name}", icon='FILE')
        csv_row.operator("arc.pick_outfit_csv", text="", icon='FILEBROWSER')


classes = (
    ARC_PT_MainPanel,
)
