"""
UI panels for Arc Model Importer
"""

import os
import bpy


def _draw_addon_version(layout):
    try:
        import sys as _sys

        _pkg = _sys.modules.get(__package__)
        _ver = getattr(_pkg, "bl_info", None) if _pkg is not None else None
        if isinstance(_ver, dict):
            _tup = _ver.get("version")
            if _tup and len(_tup) >= 3:
                layout.label(
                    text="Addon v{0}.{1}.{2}".format(_tup[0], _tup[1], _tup[2]),
                    icon="INFO",
                )
    except Exception:
        pass


def _collapsible(layout, panel_id: str, title: str, default_closed: bool = True):
    """Blender 4.1+ inline panel; falls back to always-open box."""
    panel_fn = getattr(layout, "panel", None)
    if callable(panel_fn):
        header, body = panel_fn(panel_id, default_closed=default_closed)
        header.label(text=title)
        return body
    box = layout.box()
    box.label(text=title)
    return box


def _draw_import_section(layout, context):
    import_box = layout.box()
    import_box.label(text="Import", icon="IMPORT")
    import_box.operator("arc_outfits.import_single_psk", text="Import Single Model", icon="FILE")
    import_box.operator("arc_outfits.import_outfit_folder", text="Import Folder", icon="FILE_FOLDER")


def _draw_prop_assemble_row(layout, context):
    layout.separator()
    layout.label(text="Extraction Elevator", icon="OUTLINER_OB_GROUP_INSTANCE")
    layout.operator(
        "arc_outfits.assemble_prop_folder",
        text="Assemble Elevator Prop Folder",
        icon="OUTLINER_OB_GROUP_INSTANCE",
    )
    layout.label(text="Map prop only — not for firearms", icon="INFO")


def _draw_weapons_content(layout, context):
    scene = context.scene

    if hasattr(scene, "arc_weapon_key"):
        layout.prop(scene, "arc_weapon_key", text="Gun")
        layout.operator("arc_outfits.import_weapon", text="Import Weapon", icon="IMPORT")
    else:
        layout.label(text="Weapon props missing — re-enable addon", icon="ERROR")
        return

    layout.separator()
    layout.label(text="Mods", icon="MODIFIER")
    for mod_type, prop_name in (
        ("Muzzle", "arc_weapon_mod_muzzle"),
        ("Stock", "arc_weapon_mod_stock"),
        ("Magazine", "arc_weapon_mod_magazine"),
        ("UnderBarrel", "arc_weapon_mod_underbarrel"),
        ("Tech", "arc_weapon_mod_tech"),
    ):
        if not hasattr(scene, prop_name):
            continue
        row = layout.row(align=True)
        row.prop(scene, prop_name, text=mod_type)
        op = row.operator("arc_outfits.import_weapon_mod", text="", icon="IMPORT")
        op.mod_type = mod_type

    layout.separator()
    layout.label(text="Paintjob Pattern", icon="TEXTURE")
    if hasattr(scene, "arc_weapon_pattern"):
        layout.prop(scene, "arc_weapon_pattern", text="Pattern")
    row = layout.row(align=True)
    row.operator("arc_outfits.apply_weapon_pattern", text="Apply Pattern", icon="CHECKMARK")
    row.operator("arc_outfits.clear_weapon_pattern", text="Clear", icon="X")
    layout.label(text="R/G/B Use·Color·Strength · mask=Weapon ID unless Pattern ID", icon="INFO")


def _draw_animations_content(layout, context):
    from . import animation_catalog as acat
    from . import animation_import as aimp
    from . import fmdex

    scene = context.scene
    if not hasattr(scene, "arc_anim_search"):
        layout.label(text="Scene properties missing — re-enable addon", icon="ERROR")
        return

    cache_dir = getattr(scene, "arc_animation_cache", "") or ""
    cache_row = layout.row(align=True)
    root = acat.resolved_cache_dir(cache_dir)
    if cache_dir:
        cache_row.label(
            text="Cache: " + os.path.basename(cache_dir.rstrip("/\\")),
            icon="CHECKMARK",
        )
        cache_row.operator("arc_outfits.clear_animation_cache", text="", icon="X")
    elif root:
        cache_row.label(
            text="Cache: Pioneer root (default)",
            icon="FILE_FOLDER",
        )
    else:
        cache_row.label(text="Content root (Pioneer or FModel export)", icon="FILE_FOLDER")
    cache_row.operator("arc_outfits.pick_animation_cache", text="", icon="FILEBROWSER")

    fmdex.ensure_loaded()
    stats = acat.catalog_stats(cache_dir)
    total_clips = int(stats.get("total") or 0)
    # PSA files alone populate the catalog; FMDex only adds names/tags.
    if total_clips:
        layout.label(
            text="Catalog: {total} clips  ·  {tagged} tagged  ·  {named} by path  ·  {psa} PSA".format(
                total=total_clips,
                tagged=int(stats.get("fmdex_tagged") or 0),
                named=int(stats.get("fmdex_named") or 0),
                psa=int(stats.get("psa_files") or 0),
            )
        )
    if not stats.get("fmdex_loaded"):
        layout.label(
            text=(
                "No FMDex — searching PSA filenames only; set FMDex Folder for tags"
                if total_clips
                else "Set FMDex Folder in Settings — used for animation names"
            ),
            icon="INFO",
        )
        if not total_clips:
            err = fmdex.last_error() or stats.get("fmdex_error") or ""
            if err:
                layout.label(text=str(err)[:70], icon="ERROR")

    if hasattr(scene, "arc_anim_kind_filter"):
        layout.prop(scene, "arc_anim_kind_filter", text="Kind")

    search_row = layout.row(align=True)
    search_row.prop(scene, "arc_anim_search", text="", icon="VIEWZOOM")
    if scene.arc_anim_search:
        search_row.operator("arc_outfits.clear_anim_search", text="", icon="X")

    query = scene.arc_anim_search or ""
    kind = getattr(scene, "arc_anim_kind_filter", "ALL") or "ALL"
    selected = getattr(scene, "arc_selected_anim", "") or ""
    if len(query.strip()) < 2:
        matches = []
        total = 0
        layout.label(text="Type 2+ characters to search — nothing is imported until Apply", icon="INFO")
    else:
        matches = acat.filter_entries(query, kind=kind, cache_dir=cache_dir, limit=8)
        total = acat.count_entries(query, kind=kind, cache_dir=cache_dir) if matches else 0

    if matches:
        list_box = layout.box()
        for entry in matches:
            key = entry.get("key") or ""
            label = acat.display_label(entry)
            row = list_box.row()
            icon = "RADIOBUT_ON" if key == selected else "RADIOBUT_OFF"
            op = row.operator("arc_outfits.select_anim", text=label[:60], icon=icon)
            op.anim_key = key
        if total > len(matches):
            list_box.label(text=f"... and {total - len(matches)} more (refine search)")
    elif query.strip():
        if total_clips == 0:
            layout.label(
                text="Catalog is empty — FMDex has no Animation folders / AS_ names, and no .psa in cache",
                icon="ERROR",
            )
        else:
            layout.label(
                text=f"No name match in {total_clips} clips — try stem or folder (idle, emote, celeste)",
                icon="INFO",
            )

    if selected:
        entry = acat.get_entry(selected, cache_dir)
        if entry:
            acat.enrich_entry(entry, cache_dir)
            psa = acat.resolve_psa_path(entry, cache_dir)
            bits = [entry.get("kind") or ""]
            if entry.get("skeleton"):
                bits.append(entry["skeleton"])
            if entry.get("length"):
                bits.append(f"{entry['length']:.2f}s")
            if entry.get("notify_count"):
                bits.append(f"{entry['notify_count']} notifies")
            layout.label(text=" · ".join(b for b in bits if b)[:70])
            if psa:
                layout.label(text="PSA ready: " + os.path.basename(psa), icon="CHECKMARK")
            else:
                layout.label(
                    text="No PSA yet — Send from FModel or set cache folder",
                    icon="ERROR",
                )

    if hasattr(scene, "arc_anim_spawn_notifies"):
        layout.prop(scene, "arc_anim_spawn_notifies", text="Spawn notify props / FX")
    if hasattr(scene, "arc_anim_replace_action"):
        layout.prop(scene, "arc_anim_replace_action", text="Replace previous Action")

    arm = aimp.resolve_target_armature(context)
    if arm is None:
        layout.label(text="Select a character armature to apply onto", icon="INFO")
    else:
        layout.label(text="Target: " + arm.name, icon="ARMATURE_DATA")

    apply_row = layout.row(align=True)
    apply_row.operator("arc_outfits.apply_anim", text="Apply Animation", icon="PLAY")
    apply_row.operator("arc_outfits.clear_anim_props", text="", icon="X")
    layout.operator("arc_outfits.refresh_anim_catalog", text="Refresh Catalog", icon="FILE_REFRESH")


def _draw_outfit_tools_content(layout, context):
    from . import properties, importing

    scene = context.scene

    bench_box = layout.box()
    bench_box.label(text="Color Benchmark (inspection)", icon="NODETREE")
    if hasattr(scene, "arc_color_benchmark_id"):
        bench_box.prop(scene, "arc_color_benchmark_id", text="Set")
    try:
        op = bench_box.operator(
            "arc_outfits.import_color_benchmark",
            text="Import Color Benchmark",
            icon="IMPORT",
        )
        op.benchmark_id = getattr(scene, "arc_color_benchmark_id", "ALL") or "ALL"
        bench_box.label(text="No ArcTexturer · exposed nodes · no overlaps")
    except Exception as exc:
        bench_box.label(
            text="Operator missing — disable MapImporter, enable Outfits, then re-enable",
            icon="ERROR",
        )
        print(f"Arc Raiders: Color Benchmark draw failed: {exc}")

    layout.separator()
    layout.label(text="Outfit Selector:", icon="PRESET")

    csv_path = importing.get_outfit_csv_path(context)
    if not csv_path:
        layout.label(text="No CSV found — browse Outfit Reference CSV in Settings.", icon="ERROR")
    elif not hasattr(scene, "arc_outfit_search"):
        layout.label(
            text="Scene properties missing — disable/enable Arc Model Importer.",
            icon="ERROR",
        )
    else:
        search_row = layout.row(align=True)
        search_row.prop(scene, "arc_outfit_search", text="", icon="VIEWZOOM")
        if scene.arc_outfit_search:
            search_row.operator("arc_outfits.clear_outfit_search", text="", icon="X")

        search_lower = scene.arc_outfit_search.lower()
        all_items = properties.make_outfit_selector_items(None, context)
        filtered = [
            (k, l)
            for k, l, d in all_items
            if k != "NONE" and (not search_lower or search_lower in l.lower())
        ]
        filtered.sort(key=lambda t: (1 if "unnamed" in t[1].lower() else 0, t[1].lower()))

        if filtered:
            list_box = layout.box()
            selected_key = getattr(scene, "arc_selected_outfit", "")
            for key, label in filtered[:4]:
                row = list_box.row()
                icon = "RADIOBUT_ON" if key == selected_key else "RADIOBUT_OFF"
                op = row.operator("arc_outfits.select_outfit", text=label, icon=icon)
                op.outfit_key = key
            if len(filtered) > 4:
                list_box.label(text=f"... and {len(filtered) - 4} more (refine search)")

        layout.separator()
        if hasattr(scene, "arc_selected_outfit_browse"):
            layout.prop(scene, "arc_selected_outfit_browse", text="Browse All")

        load_col = layout.column(align=True)
        load_col.operator("arc_outfits.load_outfit", text="Load Outfit", icon="IMPORT")
        load_col.operator(
            "arc_outfits.load_all_colorways",
            text="Load All Colorways",
            icon="DUPLICATE",
        )

    if hasattr(scene, "arc_mask_debug_mode"):
        layout.separator()
        dbg = layout.box()
        dbg.label(text="Mask Debug (viewport only)")
        if hasattr(scene, "arc_mask_debug_source"):
            dbg.prop(scene, "arc_mask_debug_source", text="Source")
        dbg.prop(scene, "arc_mask_debug_mode", text="Mode")
        try:
            mode_i = int(getattr(scene, "arc_mask_debug_mode", 0) or 0)
        except Exception:
            mode_i = 0
        if mode_i == 1:
            dbg.prop(scene, "arc_mask_debug_color_n", text="Color N")
        dbg.prop(scene, "arc_mask_debug_show_colormask", text="Show ColorMask")
        dbg.prop(scene, "arc_mask_debug_show_numbers", text="Show Numbers")
        dbg.prop(scene, "arc_mask_debug_grid_scale", text="Grid Scale")
        if hasattr(scene, "arc_mask_debug_overlay_opacity"):
            dbg.prop(scene, "arc_mask_debug_overlay_opacity", text="Overlay Opacity")
        else:
            dbg.prop(scene, "arc_mask_debug_opacity", text="Overlay Opacity")
        if hasattr(scene, "arc_mask_debug_colormask_opacity"):
            dbg.prop(scene, "arc_mask_debug_colormask_opacity", text="ColorMask Opacity")
        dbg.prop(scene, "arc_mask_debug_numbers_opacity", text="Numbers Opacity")
        dbg.label(text="Apply-only · Base → Overlay → ColorMask → Digits")
        dbg_row = dbg.row(align=True)
        dbg_row.operator("arc_outfits.mask_debug_apply", text="Apply", icon="HIDE_OFF")
        dbg_row.operator("arc_outfits.mask_debug_clear", text="Clear", icon="LOOP_BACK")

    _draw_material_settings_tools(layout, context)


def _draw_material_settings_tools(layout, context):
    """Decals / palette / pipeline / crease — live under Outfit Tools (closed by default)."""
    scene = context.scene
    layout.separator()
    mat = layout.box()
    mat.label(text="Material Settings", icon="MATERIAL")

    if hasattr(scene, "arc_decal_method"):
        mat.prop(scene, "arc_decal_method", text="Decals")
        if scene.arc_decal_method != "FMODEL":
            mat.label(text="Non-FModel mode — accessory test only", icon="ERROR")
        if scene.arc_decal_method in {"WIDTH_2X", "WIDTH_3X"}:
            mat.label(text="WIDTH_* widens along sx (mesh strip), not WidthRatio/sy")
            mat.label(text="LegBox duct tape: try WIDTH_2X then Update Materials")
    if hasattr(scene, "arc_decal_excl_c1"):
        excl = mat.box()
        excl.label(text="LayerMask Manual Override", icon="MODIFIER")
        excl.label(text="On top of cooked Allow bits — tick Colour N to block")
        if hasattr(scene, "arc_decal_excl_slot"):
            excl.prop(scene, "arc_decal_excl_slot", text="Slot")
        row = excl.row(align=True)
        for zi in range(1, 9):
            row.prop(scene, f"arc_decal_excl_c{zi}", text=str(zi), toggle=True)
        er = excl.row(align=True)
        er.operator("arc_outfits.decal_exclude_apply", text="Apply Override")
        er.operator("arc_outfits.decal_exclude_clear", text="Clear Override")
    if hasattr(scene, "arc_palette_mode"):
        mat.prop(scene, "arc_palette_mode", text="Palette")
        mat.label(text="Set Selected applies now (rewires ColorMask)", icon="INFO")
    if hasattr(scene, "arc_outfit_color_pipeline"):
        mat.prop(scene, "arc_outfit_color_pipeline", text="Color Pipeline")
        if getattr(scene, "arc_outfit_color_pipeline", "") == "LEGACY":
            mat.label(text="Legacy (default during reverse) — Update Materials to apply")
        elif getattr(scene, "arc_outfit_color_pipeline", "") == "GROUND_TRUTH":
            mat.label(text="GT D044 Swatch assemble — Update Materials to apply")
    if hasattr(scene, "arc_crease_edge_color"):
        mat.prop(scene, "arc_crease_edge_color", text="Crease/Edge Color")
    pal_row = mat.row(align=True)
    pal_row.operator("arc_outfits.palette_set_selected", text="Set Selected")
    pal_row.operator("arc_outfits.palette_reset_selected", text="Reset")
    cal_row = mat.row(align=True)
    cal_row.operator("arc_outfits.palette_export_calibration", text="Export Cal")
    cal_row.operator("arc_outfits.palette_import_calibration", text="Import Cal")
    cal_row.operator("arc_outfits.palette_clear_calibration", text="Clear")
    obj = context.object
    if obj is not None:
        key = str(obj.get("arc_material_key", "") or "")
        resolved = str(obj.get("arc_palette_resolved", "") or "")
        if key or resolved:
            mat.label(text=f"Resolved: {resolved or '?'}  {key[:48]}")


def _draw_rig_section(layout, context):
    rig_box = layout.box()
    rig_box.label(text="Rig", icon="ARMATURE_DATA")
    rig_box.operator(
        "arc_outfits.merge_selected_armatures",
        text="Merge Selected Armatures",
        icon="BONE_DATA",
    )


def _draw_materials_section(layout, context):
    fix_box = layout.box()
    fix_box.label(text="Materials", icon="MATERIAL")
    fix_box.operator("arc_outfits.fix_materials", text="Update Materials", icon="FILE_REFRESH")
    fix_box.operator(
        "arc_outfits.reimport_selected",
        text="Re-import Selected",
        icon="IMPORT",
    )
    fix_box.operator("arc_outfits.organize_nodes", text="Organize Nodes", icon="NODETREE")
    fix_box.operator(
        "arc_outfits.update_selected_group_nodes",
        text="Update Selected Group Nodes",
        icon="FILE_REFRESH",
    )


def _draw_settings_content(layout, context):
    from . import importing
    from .map_tools import fmodel_bridge as bridge

    scene = context.scene

    pioneer_row = layout.row(align=True)
    pioneer_root = getattr(scene, "arc_pioneer_root", "") or ""
    if pioneer_root:
        pioneer_row.label(
            text="Root: " + os.path.basename(pioneer_root.rstrip("/\\")),
            icon="CHECKMARK",
        )
    else:
        pioneer_row.label(text="PioneerGame Folder", icon="FILE_FOLDER")
    pioneer_row.operator("arc_outfits.pick_pioneer_root", text="", icon="FILEBROWSER")
    if pioneer_root:
        pioneer_row.operator("arc_outfits.clear_pioneer_root", text="", icon="X")

    fmdex_row = layout.row(align=True)
    fmdex_root = getattr(scene, "arc_fmdex_root", "") or ""
    if fmdex_root:
        fmdex_row.label(
            text="FMDex: " + os.path.basename(fmdex_root.rstrip("/\\")),
            icon="CHECKMARK",
        )
    else:
        fmdex_row.label(text="FMDex Folder (FModel)", icon="FILE_FOLDER")
    fmdex_row.operator("arc_outfits.pick_fmdex_root", text="", icon="FILEBROWSER")
    if fmdex_root:
        fmdex_row.operator("arc_outfits.clear_fmdex_root", text="", icon="X")

    csv_path = importing.get_outfit_csv_path(context)
    csv_row = layout.row(align=True)
    outfit_csv = getattr(scene, "arc_outfit_csv_path", "") or ""
    if outfit_csv:
        csv_row.label(text=f"CSV: {os.path.basename(outfit_csv)}", icon="FILE")
        csv_row.operator("arc_outfits.clear_outfit_csv", text="", icon="LOOP_BACK")
    else:
        name = os.path.basename(csv_path) if csv_path else "(not found)"
        csv_row.label(text=f"CSV: {name}", icon="FILE")
    csv_row.operator("arc_outfits.pick_outfit_csv", text="", icon="FILEBROWSER")

    layout.separator()
    layout.label(text="FModel Bridge", icon="WORLD")
    layout.label(text="Single models / outfits from FModel", icon="INFO")

    if hasattr(scene, "arc_auto_listen"):
        layout.prop(scene, "arc_auto_listen", text="Auto-start Listener")
    listen_row = layout.row(align=True)
    if bridge.is_listening():
        listen_row.operator("arc_outfits.stop_placement_listener", text="Stop Listener", icon="PAUSE")
        listen_row.label(text=f":{bridge.listen_port()} · {bridge.last_status()}")
    else:
        listen_row.operator("arc_outfits.start_placement_listener", text="Start Listener", icon="PLAY")
        listen_row.label(text=bridge.last_status() or "idle")
    if bridge.last_error():
        layout.label(text=bridge.last_error()[:60], icon="ERROR")
    last = bridge.last_import_path()
    if last:
        layout.label(text=f"Last: {bridge.last_map_name()} · {os.path.basename(last)}", icon="FILE")

    if hasattr(scene, "arc_placement_listen_port"):
        layout.prop(scene, "arc_placement_listen_port", text="TCP Port")

    _draw_prop_assemble_row(layout, context)


class ARC_OUTFITS_PT_MainPanel(bpy.types.Panel):
    bl_label = "Arc Model Importer"
    bl_idname = "ARC_OUTFITS_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Arc Model Importer"

    def draw(self, context):
        layout = self.layout
        from . import properties

        if not properties.ensure_scene_properties():
            layout.label(
                text="Scene properties missing — disable/enable Arc Model Importer.",
                icon="ERROR",
            )
            return

        # Outfit Tools first and open. Weapons/Animations stay closed so FMDex
        # walks and weapon enums are not rebuilt on every N-panel open.
        _draw_import_section(layout, context)

        try:
            tools_body = _collapsible(layout, "arc_outfit_tools", "Outfit Tools", default_closed=False)
            if tools_body is not None:
                _draw_outfit_tools_content(tools_body, context)
        except Exception as exc:
            layout.label(text=f"Outfit Tools failed: {exc}", icon="ERROR")
            print(f"Arc Raiders: Outfit Tools draw failed: {exc}")

        try:
            weapons_body = _collapsible(layout, "arc_weapons", "Weapons", default_closed=True)
            if weapons_body is not None:
                _draw_weapons_content(weapons_body, context)
        except Exception as exc:
            layout.label(text=f"Weapons failed: {exc}", icon="ERROR")
            print(f"Arc Raiders: Weapons draw failed: {exc}")

        try:
            anim_body = _collapsible(layout, "arc_animations", "Animations", default_closed=True)
            if anim_body is not None:
                _draw_animations_content(anim_body, context)
        except Exception as exc:
            layout.label(text=f"Animations failed: {exc}", icon="ERROR")
            print(f"Arc Raiders: Animations draw failed: {exc}")

        try:
            _draw_rig_section(layout, context)
            _draw_materials_section(layout, context)
        except Exception as exc:
            layout.label(text=f"Rig/Materials failed: {exc}", icon="ERROR")
            print(f"Arc Raiders: Rig/Materials draw failed: {exc}")

        try:
            fx_body = _collapsible(layout, "arc_niagara_fx", "Niagara FX", default_closed=True)
            if fx_body is not None:
                from . import niagara_fx

                niagara_fx.draw_niagara_fx_box(fx_body, context)
        except Exception as exc:
            layout.label(text=f"Niagara FX failed: {exc}", icon="ERROR")
            print(f"Arc Raiders: Niagara FX draw failed: {exc}")

        try:
            lighting_body = _collapsible(layout, "arc_lighting_look", "Lighting Look", default_closed=True)
            if lighting_body is not None:
                from . import lighting_looks

                lighting_looks.draw_lighting_look_box(lighting_body, context)
        except Exception as exc:
            layout.label(text=f"Lighting Look failed: {exc}", icon="ERROR")
            print(f"Arc Raiders: Lighting Look draw failed: {exc}")

        settings_body = _collapsible(layout, "arc_settings", "Settings", default_closed=True)
        if settings_body is not None:
            _draw_settings_content(settings_body, context)

        try:
            from . import addon_line as _addon_line
            from . import map_placement as _mp

            if _addon_line.is_map_importer_line():
                map_body = _collapsible(layout, "arc_map_placement", "Map Placement", default_closed=False)
                if map_body is not None:
                    _mp.draw_map_placement_panel(map_body, context)
        except Exception as exc:
            layout.label(text=f"Map UI unavailable: {exc}", icon="ERROR")

        _draw_addon_version(layout)


classes = (
    ARC_OUTFITS_PT_MainPanel,
)
