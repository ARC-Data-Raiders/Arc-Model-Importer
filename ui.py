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
                    text="Arc Model Importer  v{0}.{1}.{2}".format(_tup[0], _tup[1], _tup[2]),
                    icon="INFO",
                )
                return
    except Exception:
        pass
    layout.label(text="Arc Model Importer", icon="INFO")


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
    import_box.operator("arc_outfits.import_from_pak", text="Import from Pak", icon="PACKAGE")


def _draw_offset_settings(layout, context):
    """Character pad offset — first control under Outfit Tools."""
    scene = context.scene
    if not hasattr(scene, "arc_import_slot_offset"):
        return
    row = layout.row(align=True)
    row.prop(scene, "arc_import_slot_offset", text="Offset Characters")
    if getattr(scene, "arc_import_slot_offset", True) and hasattr(
        scene, "arc_import_slot_spacing"
    ):
        row.prop(scene, "arc_import_slot_spacing", text="X")
        if hasattr(scene, "arc_import_slot_row_spacing"):
            row.prop(scene, "arc_import_slot_row_spacing", text="Y")
    if hasattr(scene, "arc_inherit_shape_key"):
        layout.prop(scene, "arc_inherit_shape_key", text="Inherit Shape Key")


# Head/Hair previews: ~128px (template_icon scale unit ≈ 32px).
_APPEARANCE_PREVIEW_SCALE = 128.0 / 32.0
# Outfit preview sits under Load buttons (full row) — larger + alpha-bbox crops.
_OUTFIT_PREVIEW_SCALE = 256.0 / 32.0
# Cap preview column width so dropdowns keep the rest when the N-panel expands.
# Blender has no true px layout; ui_units_x ≈ 20px/unit, and icon scale uses ~32px.
_PREVIEW_UI_UNITS = 4.0  # ~128px icon column


def _appearance_preview_icon_id(choice_id: str) -> int:
    if not choice_id or choice_id == "NONE":
        return 0
    try:
        from . import appearance

        return int(appearance.preview_icon_id(choice_id) or 0)
    except Exception:
        return 0


def _split_controls_preview(layout, context, icon_id):
    """Left grows with sidebar; right preview column capped near 128px.

    ``layout.split(factor=0.5)`` wastes half the N-panel on the icon. Prefer a
    fixed ``ui_units_x`` preview column; fall back to a region-width factor.
    """
    if not icon_id:
        return layout, None
    row = layout.row(align=False)
    left = row.column()
    right = row.column()
    try:
        right.ui_units_x = _PREVIEW_UI_UNITS
        right.scale_x = 1.0
        return left, right
    except Exception:
        pass
    # Fallback when ui_units_x is unavailable: compute split from region width.
    try:
        rw = float(getattr(getattr(context, "region", None), "width", 0) or 0)
    except Exception:
        rw = 0.0
    if rw > 160.0:
        factor = max(0.45, min(0.88, 1.0 - (128.0 + 28.0) / rw))
        split = layout.split(factor=factor)
        return split.column(), split.column()
    split = layout.split(factor=0.62)
    return split.column(), split.column()


def _draw_preview_icon(column, icon_id):
    if column is None or not icon_id:
        return
    try:
        column.template_icon(icon_value=icon_id, scale=_APPEARANCE_PREVIEW_SCALE)
    except Exception:
        pass


def _draw_head_section(layout, context):
    """Left: Head dropdown + Add Head. Right: capped-width preview."""
    scene = context.scene
    choice_id = getattr(scene, "arc_appearance_head", "NONE")
    icon_id = _appearance_preview_icon_id(choice_id)
    left, right = _split_controls_preview(layout, context, icon_id)
    left.prop(scene, "arc_appearance_head", text="Head")
    op = left.operator(
        "arc_outfits.add_appearance_part", text="Add Head", icon="USER",
    )
    op.part = "HEAD"
    op = left.operator("arc_outfits.add_all_appearance", text="Add All Heads", icon="IMPORT")
    op.part = "HEAD"
    _draw_preview_icon(right, icon_id)


def _draw_hair_section(layout, context, *, facial=False):
    """Left: Hair dropdown, color, Update, Add. Right: Bald or style preview."""
    scene = context.scene
    prop = "arc_appearance_facial_hair" if facial else "arc_appearance_hair"
    label = "Facial Hair" if facial else "Hair"
    part = "FACIAL_HAIR" if facial else "HAIR"
    choice_id = getattr(scene, prop, "NONE")
    if not choice_id or choice_id == "NONE":
        try:
            from . import appearance

            icon_id = int(appearance.bald_preview_icon_id() or 0)
        except Exception:
            icon_id = 0
    else:
        icon_id = _appearance_preview_icon_id(choice_id)
    left, right = _split_controls_preview(layout, context, icon_id)
    left.prop(scene, prop, text=label)
    if hasattr(scene, "arc_appearance_hair_color"):
        left.prop(scene, "arc_appearance_hair_color", text="Hair Color")
        left.operator(
            "arc_outfits.update_hair_color",
            text="Update Hair Color",
            icon="COLOR",
        )
    op = left.operator(
        "arc_outfits.add_appearance_part", text=f"Add {label}", icon="PARTICLE_DATA",
    )
    op.part = part
    op = left.operator("arc_outfits.add_all_appearance", text=f"Add All {label}", icon="IMPORT")
    op.part = part
    _draw_preview_icon(right, icon_id)


def _draw_gear_section(layout, context, *, prop: str, part: str, label: str, icon: str):
    """Dropdown + Add + optional preview for backpack / attachment / charm / tool."""
    scene = context.scene
    if not hasattr(scene, prop):
        return
    choice_id = getattr(scene, prop, "NONE")
    icon_id = _appearance_preview_icon_id(choice_id)
    left, right = _split_controls_preview(layout, context, icon_id)
    left.prop(scene, prop, text=label)
    if part == "BACKPACK" and hasattr(scene, "arc_appearance_backpack_colorway"):
        left.prop(scene, "arc_appearance_backpack_colorway", text="Colorway")
        left.operator("arc_outfits.update_backpack_colorway", text="Update Colorway", icon="COLOR")
    op = left.operator(
        "arc_outfits.add_appearance_part", text=f"Add {label}", icon=icon,
    )
    op.part = part
    plural = {"Backpack": "Backpacks", "Attachment": "Attachments", "Charm": "Charms", "Raider Tool": "Raider Tools"}[label]
    op = left.operator("arc_outfits.add_all_appearance", text=f"Add All {plural}", icon="IMPORT")
    op.part = part
    _draw_preview_icon(right, icon_id)


def _draw_appearance_section(layout, context):
    """Body / head / hair / gear as sidebar options (not sequential dialogs)."""
    scene = context.scene
    from . import appearance_queue
    status = appearance_queue.queue_status()
    if status:
        box = layout.box()
        box.label(text=f"Add All: {status['done']}/{status['total']} · Batch {status['batch']}")
        box.label(text=status["phase"])
        box.operator("arc_outfits.cancel_appearance_queue", text="Cancel Add All", icon="CANCEL")
    elif scene.get("arc_add_all_last_report"):
        import json
        try:
            last = json.loads(scene["arc_add_all_last_report"])
            box = layout.box()
            label = appearance_queue.PARTS[last["part"]][1]
            box.label(text=f"Last Add All {label}: {last['imported']}/{last['total']}")
            for item in [item for item in last["items"] if not item["ok"]][:3]:
                box.label(text="Missing / failed: " + item["label"], icon="ERROR")
            if last["cancelled"]:
                box.label(text="Cancelled; completed imports kept")
            if last.get("error"):
                box.label(text=last["error"][:90], icon="ERROR")
        except (ValueError, KeyError, TypeError):
            pass
    if hasattr(scene, "arc_pose_preset"):
        layout.prop(scene, "arc_pose_preset", text="Pose")
    if not hasattr(scene, "arc_appearance_head"):
        return
    pak_dir = getattr(scene, "arc_pak_dir", "") or ""
    if not getattr(scene, "arc_pioneer_root", "") and not pak_dir:
        layout.label(text="Set PioneerGame Folder or Paks Folder in Settings", icon="ERROR")

    body_sec = _collapsible(layout, "arc_appearance_body", "Body", default_closed=True)
    if body_sec is not None:
        body_sec.prop(scene, "arc_appearance_body_skin", text="Body Skin")
        op = body_sec.operator(
            "arc_outfits.add_appearance_part", text="Add Body", icon="ARMATURE_DATA",
        )
        op.part = "BODY"
        op = body_sec.operator("arc_outfits.add_all_appearance", text="Add All Bodies", icon="IMPORT")
        op.part = "BODY"

    head_sec = _collapsible(layout, "arc_appearance_head_sec", "Head", default_closed=True)
    if head_sec is not None:
        _draw_head_section(head_sec, context)
        if hasattr(scene, "arc_appearance_eyelash_copy_hair_color"):
            head_sec.prop(
                scene,
                "arc_appearance_eyelash_copy_hair_color",
                text="Eyelash copy Hair Color",
            )

    hair_sec = _collapsible(layout, "arc_appearance_hair_sec", "Hair", default_closed=True)
    if hair_sec is not None:
        _draw_hair_section(hair_sec, context)

    facial_sec = _collapsible(layout, "arc_appearance_facial_hair_sec", "Facial Hair", default_closed=True)
    if facial_sec is not None:
        _draw_hair_section(facial_sec, context, facial=True)

    backpack_sec = _collapsible(
        layout, "arc_appearance_backpack_sec", "Backpack", default_closed=True,
    )
    if backpack_sec is not None:
        _draw_gear_section(
            backpack_sec, context,
            prop="arc_appearance_backpack", part="BACKPACK",
            label="Backpack", icon="OUTLINER_OB_ARMATURE",
        )

    attach_sec = _collapsible(
        layout, "arc_appearance_attachment_sec", "Attachment", default_closed=True,
    )
    if attach_sec is not None:
        _draw_gear_section(
            attach_sec, context,
            prop="arc_appearance_attachment", part="ATTACHMENT",
            label="Attachment", icon="MODIFIER",
        )

    charm_sec = _collapsible(
        layout, "arc_appearance_charm_sec", "Charm", default_closed=True,
    )
    if charm_sec is not None:
        _draw_gear_section(
            charm_sec, context,
            prop="arc_appearance_charm", part="CHARM",
            label="Charm", icon="SOLO_ON",
        )

    tool_sec = _collapsible(
        layout, "arc_appearance_raider_tool_sec", "Raider Tool", default_closed=True,
    )
    if tool_sec is not None:
        _draw_gear_section(
            tool_sec, context,
            prop="arc_appearance_raider_tool", part="RAIDER_TOOL",
            label="Raider Tool", icon="TOOL_SETTINGS",
        )

    # Shared attach/replace / reference — under the part sections.
    if hasattr(scene, "arc_appearance_attach_to_outfit"):
        layout.prop(scene, "arc_appearance_attach_to_outfit", text="Add to Selected Outfit")
    if hasattr(scene, "arc_appearance_replace_selected"):
        layout.prop(scene, "arc_appearance_replace_selected", text="Replace Selected")
    if hasattr(scene, "arc_include_reference"):
        layout.prop(scene, "arc_include_reference", text="Include Reference")
    layout.operator(
        "arc_outfits.import_reference", text="Import Reference", icon="IMAGE_DATA",
    )


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
    layout.label(text="Skin", icon="TEXTURE")
    if hasattr(scene, "arc_weapon_skin"):
        layout.prop(scene, "arc_weapon_skin", text="Colourway")
    row = layout.row(align=True)
    row.operator("arc_outfits.apply_weapon_skin", text="Apply Skin", icon="CHECKMARK")
    row.operator("arc_outfits.clear_weapon_skin", text="Base", icon="X")
    layout.label(text="From Items/Firearms/<gun>/Skins (CommonBlue, MuralTree, …)", icon="INFO")

    layout.separator()
    box = layout.box()
    box.label(text="Paintjob Pattern (advanced)", icon="NODE_TEXTURE")
    if hasattr(scene, "arc_weapon_pattern"):
        box.prop(scene, "arc_weapon_pattern", text="Pattern PNG")
    row = box.row(align=True)
    row.operator("arc_outfits.apply_weapon_pattern", text="Apply Pattern", icon="CHECKMARK")
    row.operator("arc_outfits.clear_weapon_pattern", text="Clear", icon="X")
    box.label(text="MaterialLibrary Patterns PNGs · optional overlay", icon="INFO")


def _draw_animations_content(layout, context):
    from . import animation_catalog as acat
    from . import animation_import as aimp
    from . import fmdex

    scene = context.scene
    if hasattr(scene, "arc_pose_preset"):
        layout.prop(scene, "arc_pose_preset", text="Pose")
        layout.label(text="Still frame. Search below for full clips", icon="INFO")
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
    if not stats.get("fmdex_loaded") and total_clips:
        layout.label(text="No FMDex tags — searching PSA filenames", icon="INFO")

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

    _draw_offset_settings(layout, context)
    layout.separator()

    outfit_sec = _collapsible(layout, "arc_outfit_selector", "Outfit Selector", default_closed=False)
    if outfit_sec is not None:
        csv_path = importing.get_outfit_csv_path(context)
        if not csv_path:
            outfit_sec.label(text="No CSV found — browse Outfit Reference CSV in Settings.", icon="ERROR")
        elif not hasattr(scene, "arc_outfit_search"):
            outfit_sec.label(
                text="Scene properties missing — disable/enable Arc Model Importer.",
                icon="ERROR",
            )
        else:
            search_row = outfit_sec.row(align=True)
            search_row.prop(scene, "arc_outfit_search", text="", icon="VIEWZOOM")
            if scene.arc_outfit_search:
                search_row.operator("arc_outfits.clear_outfit_search", text="", icon="X")

            search_lower = scene.arc_outfit_search.lower()
            all_items = properties.make_outfit_selector_items(None, context)
            filtered = [
                (item[0], item[1])
                for item in all_items
                if item[0] != "NONE" and (not search_lower or search_lower in item[1].lower())
            ]
            filtered.sort(key=lambda t: (1 if "unnamed" in t[1].lower() else 0, t[1].lower()))

            if filtered:
                list_box = outfit_sec.box()
                selected_key = getattr(scene, "arc_selected_outfit", "")
                for key, label in filtered[:4]:
                    row = list_box.row()
                    icon = "RADIOBUT_ON" if key == selected_key else "RADIOBUT_OFF"
                    op = row.operator("arc_outfits.select_outfit", text=label, icon=icon)
                    op.outfit_key = key
                if len(filtered) > 4:
                    list_box.label(text=f"... and {len(filtered) - 4} more (refine search)")

            outfit_sec.separator()
            if hasattr(scene, "arc_selected_outfit_browse"):
                outfit_sec.prop(scene, "arc_selected_outfit_browse", text="Browse All")

            load_col = outfit_sec.column(align=True)
            load_col.operator("arc_outfits.load_outfit", text="Load Outfit", icon="IMPORT")
            load_col.operator(
                "arc_outfits.load_all_colorways",
                text="Load All Colorways",
                icon="DUPLICATE",
            )
            try:
                from . import appearance as _appearance

                outfit_icon = int(_appearance.outfit_preview_icon_id(context) or 0)
            except Exception:
                outfit_icon = 0
            if outfit_icon:
                try:
                    load_col.template_icon(
                        icon_value=outfit_icon, scale=_OUTFIT_PREVIEW_SCALE,
                    )
                except Exception:
                    pass

    _draw_appearance_section(layout, context)

    if hasattr(scene, "arc_mask_debug_mode"):
        dbg = _collapsible(layout, "arc_mask_debug", "Mask Debug", default_closed=True)
        if dbg is not None:
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
            dbg_row = dbg.row(align=True)
            dbg_row.operator("arc_outfits.mask_debug_apply", text="Apply", icon="HIDE_OFF")
            dbg_row.operator("arc_outfits.mask_debug_clear", text="Clear", icon="LOOP_BACK")


def _draw_rig_section(layout, context):
    rig_box = layout.box()
    rig_box.label(text="Rig", icon="ARMATURE_DATA")
    rig_box.operator(
        "arc_outfits.merge_selected_armatures",
        text="Merge Selected Armatures",
        icon="BONE_DATA",
    )


def _draw_deep_path_search_row(layout, context):
    """Offer consent-gated deep folder search when known layouts miss."""
    from . import utils

    scene = context.scene
    deep = getattr(scene, "arc_deep_path_search_root", "") or ""
    row = layout.row(align=True)
    if deep:
        row.label(
            text="Deep search: " + os.path.basename(deep.rstrip("/\\")),
            icon="VIEWZOOM",
        )
        row.operator("arc_outfits.clear_deep_path_search", text="", icon="X")
    else:
        miss = utils.last_path_miss()
        if miss and miss[1]:
            tip = "/".join(miss[1][:4])
            row.label(text=f"Layout miss: {tip}", icon="ERROR")
        else:
            row.label(text="Nonstandard folder layout?", icon="INFO")
        row.operator(
            "arc_outfits.deep_path_search",
            text="Search Selected Folder…",
            icon="VIEWZOOM",
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

    pak_row = layout.row(align=True)
    pak_dir = getattr(scene, "arc_pak_dir", "") or ""
    if pak_dir:
        pak_row.label(
            text="Paks: " + os.path.basename(pak_dir.rstrip("/\\")),
            icon="CHECKMARK",
        )
    else:
        pak_row.label(text="Paks Folder", icon="FILE_FOLDER")
    pak_row.operator("arc_outfits.pick_pak_dir", text="", icon="FILEBROWSER")
    if pak_dir:
        pak_row.operator("arc_outfits.reindex_paks", text="", icon="FILE_REFRESH")
        pak_row.operator("arc_outfits.clear_pak_dir", text="", icon="X")

    usmap_row = layout.row(align=True)
    usmap = getattr(scene, "arc_usmap_path", "") or ""
    if usmap:
        usmap_row.label(text="Usmap: " + os.path.basename(usmap), icon="FILE")
    else:
        usmap_row.label(text="Usmap: bundled", icon="FILE")
    usmap_row.operator("arc_outfits.pick_usmap", text="", icon="FILEBROWSER")
    if usmap:
        usmap_row.operator("arc_outfits.clear_usmap", text="", icon="X")

    _draw_deep_path_search_row(layout, context)

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

    advanced = _collapsible(layout, "arc_fmodel_bridge", "Advanced", default_closed=True)
    if advanced is not None:
        advanced.label(text="FModel Bridge", icon="WORLD")
        advanced.label(text="Single models / outfits from FModel", icon="INFO")
        if hasattr(scene, "arc_auto_listen"):
            advanced.prop(scene, "arc_auto_listen", text="Auto-start Listener")
        listen_row = advanced.row(align=True)
        if bridge.is_listening():
            listen_row.operator("arc_outfits.stop_placement_listener", text="Stop Listener", icon="PAUSE")
            listen_row.label(text=f":{bridge.listen_port()} · {bridge.last_status()}")
        else:
            listen_row.operator("arc_outfits.start_placement_listener", text="Start Listener", icon="PLAY")
            listen_row.label(text=bridge.last_status() or "idle")
        if bridge.last_error():
            advanced.label(text=bridge.last_error()[:60], icon="ERROR")
        last = bridge.last_import_path()
        if last:
            advanced.label(text=f"Last: {bridge.last_map_name()} · {os.path.basename(last)}", icon="FILE")
        if hasattr(scene, "arc_placement_listen_port"):
            advanced.prop(scene, "arc_placement_listen_port", text="TCP Port")


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

        _draw_addon_version(layout)

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
        except Exception as exc:
            layout.label(text=f"Rig failed: {exc}", icon="ERROR")
            print(f"Arc Raiders: Rig draw failed: {exc}")

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


classes = (
    ARC_OUTFITS_PT_MainPanel,
)
