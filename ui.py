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
            import_box.label(text="No CSV found — browse Outfit Reference CSV in Settings.", icon='ERROR')
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

        fix_box = layout.box()
        fix_box.label(text="Materials", icon='MATERIAL')
        fix_box.operator("arc.fix_materials", text="Update Materials", icon='FILE_REFRESH')
        fix_box.operator(
            "arc.reimport_selected",
            text="Re-import Selected",
            icon='IMPORT',
        )
        fix_box.label(text="Update = selected or all · Re-import = selection only")
        fix_box.label(text="Re-import refreshes UV (poster UV1) + Arc materials")
        fix_box.prop(scene, "arc_decal_method", text="Decals")
        if scene.arc_decal_method != 'FMODEL':
            fix_box.label(text="Non-FModel mode — accessory test only", icon='ERROR')
        if scene.arc_decal_method in {'WIDTH_2X', 'WIDTH_3X'}:
            fix_box.label(text="WIDTH_* widens along sx (mesh strip), not WidthRatio/sy")
            fix_box.label(text="LegBox duct tape: try WIDTH_2X then Update Materials")
        fix_box.prop(scene, "arc_palette_mode", text="Palette")
        pal_row = fix_box.row(align=True)
        pal_row.operator("arc.palette_set_selected", text="Set Selected")
        pal_row.operator("arc.palette_reset_selected", text="Reset")
        cal_row = fix_box.row(align=True)
        cal_row.operator("arc.palette_export_calibration", text="Export Cal")
        cal_row.operator("arc.palette_import_calibration", text="Import Cal")
        cal_row.operator("arc.palette_clear_calibration", text="Clear")
        obj = context.object
        if obj is not None:
            key = str(obj.get("arc_material_key", "") or "")
            resolved = str(obj.get("arc_palette_resolved", "") or "")
            if key or resolved:
                fix_box.label(text=f"Resolved: {resolved or '?'}  {key[:48]}")

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

        fmdex_row = settings_box.row(align=True)
        if scene.arc_fmdex_root:
            fmdex_row.label(
                text="FMDex: " + os.path.basename(scene.arc_fmdex_root.rstrip("/\\")),
                icon='CHECKMARK',
            )
        else:
            fmdex_row.label(text="FMDex Folder (FModel)", icon='FILE_FOLDER')
        fmdex_row.operator("arc.pick_fmdex_root", text="", icon='FILEBROWSER')
        if scene.arc_fmdex_root:
            fmdex_row.operator("arc.clear_fmdex_root", text="", icon='X')

        csv_path = importing.get_outfit_csv_path(context)
        csv_row = settings_box.row(align=True)
        if scene.arc_outfit_csv_path:
            csv_row.label(text=f"CSV: {os.path.basename(scene.arc_outfit_csv_path)}", icon='FILE')
            csv_row.operator("arc.clear_outfit_csv", text="", icon='LOOP_BACK')
        else:
            name = os.path.basename(csv_path) if csv_path else "(not found)"
            csv_row.label(text=f"CSV: {name}", icon='FILE')
        csv_row.operator("arc.pick_outfit_csv", text="", icon='FILEBROWSER')

        # FModel bridge — thin receiver; maps are two-stage
        place = layout.box()
        place.label(text="FModel Bridge", icon='WORLD')
        place.label(text="Single models: auto geometry + materials", icon='INFO')

        from .map_tools import fmodel_bridge as bridge

        place.prop(scene, "arc_auto_listen", text="Auto-start Listener")
        listen_row = place.row(align=True)
        if bridge.is_listening():
            listen_row.operator("arc.stop_placement_listener", text="Stop Listener", icon='PAUSE')
            listen_row.label(text=f":{bridge.listen_port()} · {bridge.last_status()}")
        else:
            listen_row.operator("arc.start_placement_listener", text="Start Listener", icon='PLAY')
            listen_row.label(text=bridge.last_status() or "idle")
        if bridge.last_error():
            place.label(text=bridge.last_error()[:60], icon='ERROR')
        last = bridge.last_import_path()
        if last:
            place.label(text=f"Last: {bridge.last_map_name()} · {os.path.basename(last)}", icon='FILE')

        map_box = place.box()
        map_box.label(text="Map", icon='MESH_GRID')
        map_box.operator(
            "arc.import_placement_instanced",
            text="Stage 1: Import Map Geometry (Fast)",
            icon='GEOMETRY_NODES',
        )
        map_box.operator(
            "arc.import_placement_meshes",
            text="Stage 1: Import Separate Objects (Slow)",
            icon='MESH_DATA',
        )
        map_box.operator(
            "arc.apply_map_materials",
            text="Stage 2: Apply Map Materials",
            icon='MATERIAL',
        )
        map_box.operator(
            "arc.reimport_selected",
            text="Re-import Selected (materials / UV)",
            icon='IMPORT',
        )
        # Instance fixer — expand do-not-instance GN cards into unique meshes
        # (decals / planes / posters / branding graphics). Keep near Stage 2.
        map_box.operator(
            "arc.realize_decal_instancers",
            text="Realize Unique Instancers (instance fixer)",
            icon='DUPLICATE',
        )
        map_box.label(
            text="Decals / planes / posters / branding → unique meshes, then Stage 2",
            icon='INFO',
        )
        map_box.operator(
            "arc.group_map_collections",
            text="Group Map Collections (+ Glass / SMA)",
            icon='OUTLINER_COLLECTION',
        )
        map_box.operator(
            "arc.fix_white_unassigned_materials",
            text="Fix Unassigned/White Materials",
            icon='SHADING_RENDERED',
        )
        map_box.operator(
            "arc.repair_sma_trim_materials",
            text="Repair SMA / Trim Materials",
            icon='MODIFIER',
        )
        map_box.label(
            text="Water: Shore Color near mesh intersections (proximity + AO)",
            icon='INFO',
        )
        shore = map_box.box()
        shore.label(text="Water Shore Proximity", icon='MOD_FLUIDSIM')
        shore.prop(scene, "arc_water_shore_distance", text="Distance (m)")
        shore.prop(scene, "arc_water_shore_strength", text="Strength")
        shore.prop(scene, "arc_water_shore_invert", text="Invert Mask")
        shore.prop(scene, "arc_water_shore_target_collection", text="Targets")
        shore.operator(
            "arc.refresh_water_shore_proximity",
            text="Refresh Water Shore Proximity",
            icon='FILE_REFRESH',
        )
        map_box.operator(
            "arc.audit_map_materials",
            text="Audit Map Materials (unique types)",
            icon='TEXT',
        )
        map_box.label(
            text="{Map}_Glass = transparent / BrokenGlass (costlier to render)",
            icon='INFO',
        )
        map_box.prop(scene, "arc_map_focus_instancer_material", text="Click Instancer → Show SRC Material")
        map_box.operator(
            "arc.focus_instancer_source_material",
            text="Focus Selected Instancer Material",
            icon='NODE_MATERIAL',
        )
        map_box.operator(
            "arc.select_instancer_source",
            text="Select Instance Source (SRC)",
            icon='EYEDROPPER',
        )
        map_box.separator()
        map_box.operator(
            "arc.reload_placement_heightmap",
            text="Reload Heightmap Only",
            icon='IMAGE_DATA',
        )
        map_box.operator(
            "arc.apply_heightmap_sand_material",
            text="Apply Sand to Heightmap",
            icon='TEXTURE',
        )
        ground_refs = map_box.box()
        ground_refs.label(text="Ground Map Texturing", icon='IMAGE_DATA')
        ig_row = ground_refs.row(align=True)
        if scene.arc_placement_ingame_map_image:
            ig_name = os.path.basename(scene.arc_placement_ingame_map_image)
            if len(ig_name) > 28:
                ig_name = "…" + ig_name[-27:]
            ig_row.label(text="Map: " + ig_name, icon='UV')
        else:
            ig_row.label(text="In-Game Map (auto)", icon='UV')
        ig_row.operator("arc.pick_placement_ingame_map", text="", icon='FILEBROWSER')
        if scene.arc_placement_ingame_map_image:
            ig_row.operator("arc.clear_placement_ingame_map", text="", icon='X')
        hl_row = ground_refs.row(align=True)
        if scene.arc_placement_hlod_color_image:
            hl_name = os.path.basename(scene.arc_placement_hlod_color_image)
            if len(hl_name) > 28:
                hl_name = "…" + hl_name[-27:]
            hl_row.label(text="HLOD: " + hl_name, icon='COLOR')
        else:
            hl_row.label(text="HLOD Color (auto palette)", icon='COLOR')
        hl_row.operator("arc.pick_placement_hlod_color", text="", icon='FILEBROWSER')
        if scene.arc_placement_hlod_color_image:
            hl_row.operator("arc.clear_placement_hlod_color", text="", icon='X')
        ground_refs.operator(
            "arc.resolve_heightmap_ground_refs",
            text="Auto-Find Map / HLOD Refs",
            icon='VIEWZOOM',
        )
        ground_refs.label(
            text="In-Game Map = masks · HLOD Color = cream/rock/pink palette",
            icon='INFO',
        )
        map_box.label(
            text="Reload = swap PNG/bounds on CityGroundPlane (masks voids)",
            icon='INFO',
        )
        map_box.label(
            text="Sand = dual-scale BRDF + map palette (keeps Displace)",
            icon='INFO',
        )
        map_box.label(
            text="After Stage 1: auto-group, hide skybox, frame city center",
            icon='INFO',
        )
        map_box.label(
            text="Heightmap: prepare_blender_heightmap.py then Reload",
            icon='INFO',
        )

        place.prop(scene, "arc_bridge_advanced", text="Advanced / Backup", toggle=True)
        if not scene.arc_bridge_advanced:
            return

        from . import map_placement as mp

        adv = place.box()
        adv.label(text="Advanced / Backup", icon='TOOL_SETTINGS')
        adv.prop(scene, "arc_placement_listen_port", text="TCP Port")

        ws = scene.arc_placement_workspace or mp.default_placement_workspace()
        ws_row = adv.row(align=True)
        ws_disp = ws.replace("\\", "/")
        if len(ws_disp) > 42:
            ws_disp = "…" + ws_disp[-41:]
        ws_row.label(text="Out: " + ws_disp, icon='FILE_FOLDER')
        ws_row.operator("arc.pick_placement_workspace", text="", icon='FILEBROWSER')
        if scene.arc_placement_workspace:
            ws_row.operator("arc.clear_placement_workspace", text="", icon='X')
        ws_row.operator("arc.open_placement_workspace", text="", icon='FILEBROWSER')

        adv.operator(
            "arc.import_last_fmodel_export",
            text="Import Last FModel Export",
            icon='FILE_FOLDER',
        )
        adv.prop(scene, "arc_placement_batch_size", text="Batch Size")
        adv.operator("arc.import_placement_empties", text="Import Empties Only", icon='EMPTY_AXIS')
        adv.label(
            text="Meshes: MapPlacements export and/or PioneerGame Root",
            icon='INFO',
        )
        adv.operator(
            "arc.frame_placement_view",
            text="Frame Map (fix clip / zoom)",
            icon='VIEWZOOM',
        )

        manual = adv.box()
        manual.label(text="Manual map extract (JSON)", icon='EXPORT')
        map_row = manual.row(align=True)
        map_row.prop(scene, "arc_placement_map", text="Map")
        map_row.operator("arc.refresh_placement_maps", text="", icon='FILE_REFRESH')
        ex_row = manual.row(align=True)
        ex_row.operator("arc.extract_map_placements", text="Extract Placements", icon='EXPORT')
        ex_row.operator("arc.generate_placement_overlay", text="Overlay PNG", icon='IMAGE_DATA')
        csv_row = manual.row(align=True)
        if scene.arc_placement_csv:
            csv_row.label(text=os.path.basename(scene.arc_placement_csv), icon='FILE')
        else:
            csv_row.label(text="CSV (auto after Extract / receive)", icon='FILE')
        csv_row.operator("arc.pick_placement_csv", text="", icon='FILEBROWSER')


classes = (
    ARC_PT_MainPanel,
)
