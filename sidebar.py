"""
UI panels for Arc Asset Importer
"""

import os
import bpy
from pathlib import Path
from .Arc import arc_catalog_data; from .Lighting import lighting_catalog as cat; from .Weapons import weapon_assets as assets, weapon_catalog as catalog, weapon_ui
from .Arc.arc_ui import _pioneer_image_path
from .Weapons.weapon_ui import _GUN_DISPLAY, _GUN_FAILED, _GUN_PENDING, _GUN_TEXTURES, _prepare_gun_textures, arc_preview_id, preview_id
from .Niagara.niagara import ARC_OUTFITS_OT_PlayNiagaraFX, display_label, filter_entries
from .Lighting.lighting_sky import _WORLD_TAG


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


def _draw_pak_folder_picker(layout, context):
    path = getattr(context.scene, "arc_pak_dir", "") or ""
    row = layout.row(align=True)
    row.label(text="Steam Install: " + os.path.basename(path.rstrip("/\\")) if path else "Steam Install", icon="CHECKMARK" if path else "FILE_FOLDER")
    row.operator("arc_outfits.pick_pak_dir", text="", icon="FILEBROWSER")
    if path:
        row.operator("arc_outfits.reindex_paks", text="", icon="FILE_REFRESH")
        row.operator("arc_outfits.clear_pak_dir", text="", icon="X")


def _draw_fmodel_folder_picker(layout, context):
    scene = context.scene
    pioneer_row = layout.row(align=True)
    pioneer_root = getattr(scene, "arc_pioneer_root", "") or ""
    if pioneer_root:
        pioneer_row.label(
            text="FModel Output: " + os.path.basename(pioneer_root.rstrip("/\\")),
            icon="CHECKMARK",
        )
    else:
        pioneer_row.label(text="FModel Output", icon="FILE_FOLDER")
    pioneer_row.operator("arc_outfits.pick_pioneer_root", text="", icon="FILEBROWSER")
    if pioneer_root:
        pioneer_row.operator("arc_outfits.clear_pioneer_root", text="", icon="X")



def _draw_import_section(layout, context):
    box = layout.box()
    scene = context.scene
    box.prop(scene, "arc_import_source", text="Import")
    if getattr(scene, "arc_import_source", "PAK") == "PAK":
        _draw_pak_folder_picker(box, context)
    else:
        _draw_fmodel_folder_picker(box, context)
    if getattr(scene, "arc_import_source", "PAK") == "PAK":
        row = box.row(align=True)
        row.operator("arc_outfits.import_from_pak", text="Import Single Model", icon="FILE").select_folder = False
        row.operator("arc_outfits.import_from_pak", text="Import Folder", icon="FILE_FOLDER").select_folder = True
    else:
        row = box.row(align=True)
        row.operator("arc_outfits.import_single_psk", text="Import Single Model", icon="FILE")
        row.operator("arc_outfits.import_outfit_folder", text="Import Folder", icon="FILE_FOLDER")


def _draw_offset_settings(layout, context):
    """Asset placement and character fit controls at the top of Settings."""
    scene = context.scene
    if not hasattr(scene, "arc_import_slot_offset"):
        return
    row = layout.row(align=True)
    row.prop(scene, "arc_import_slot_offset", text="Offset Asset")
    if getattr(scene, "arc_import_slot_offset", True) and hasattr(
        scene, "arc_import_slot_spacing"
    ):
        row.prop(scene, "arc_import_slot_spacing", text="X")
        if hasattr(scene, "arc_import_slot_row_spacing"):
            row.prop(scene, "arc_import_slot_row_spacing", text="Y")
    if hasattr(scene, "arc_inherit_shape_key"):
        layout.prop(scene, "arc_inherit_shape_key", text="Inherit Shape Key")


# Layout sizes are in UI-scaled pixels (including N-panel zoom).
_PREVIEW_SPLIT_MIN_PX = 430.0


def _sidebar_width(context):
    region = getattr(context, "region", None)
    width = float(getattr(region, "width", 320) or 320)
    system = getattr(getattr(context, "preferences", None), "system", None)
    scale = float(getattr(system, "ui_scale", 1.0) or 1.0)
    view = getattr(region, "view2d", None)
    if view:
        try:
            x0 = view.region_to_view(0, 0)[0]
            x1 = view.region_to_view(int(width), 0)[0]
            if x1 > x0:
                return max(64.0, x1 - x0 - 40.0)
        except (AttributeError, RuntimeError):
            pass
    return max(64.0, width / max(scale, 0.5) - 40.0)


def _split_controls_preview(layout, context, icon_id, *, rows=3):
    """Return controls and a sized preview, stacking below in narrow panels."""
    width = _sidebar_width(context)
    if not icon_id:
        return layout.column(align=True), None
    if width >= _PREVIEW_SPLIT_MIN_PX - 40.0:
        split = layout.split(factor=0.5, align=False)
        controls, preview = split.column(align=True), split.column(align=True)
        # One control row is approximately 20 UI pixels plus spacing.
        available_width = width * 0.5 - 12.0
        max_height = rows * 20.0
    else:
        controls = layout.column(align=True)
        centered = layout.row(align=True)
        margins = centered.split(factor=0.125, align=True)
        margins.column()
        content = margins.split(factor=6.0 / 7.0, align=True)
        preview = content.column(align=True)
        content.column()
        available_width = width * 0.75
        max_height = None
    preview.alignment = "CENTER"
    return controls, (preview, available_width, max_height)


def _draw_preview_icon(preview, icon_id):
    if preview is None or not icon_id:
        return
    column, available_width, max_height = preview
    # template_icon supports independent thumbnails in the same region.
    # Blender reserves a square and adds its own small internal border.
    size = available_width if max_height is None else min(available_width, max_height)
    column.template_icon(icon_value=icon_id, scale=max(0.5, size / 20.0))


def _draw_outfit_selector_preview(layout, context, icon_id):
    from .Outfit_Accessories import properties
    scene = context.scene
    controls, preview = _split_controls_preview(layout, context, icon_id, rows=9)
    search_row = controls.row(align=True)
    search_row.prop(scene, "arc_outfit_search", text="", icon="VIEWZOOM")
    if scene.arc_outfit_search:
        search_row.operator("arc_outfits.clear_outfit_search", text="", icon="X")
    query = scene.arc_outfit_search.casefold()
    filtered = [item for item in properties.make_outfit_selector_items(scene, context)
                if item[0] != "NONE" and (not query or query in item[1].casefold())]
    if filtered:
        results = controls.box()
        for key, label, *_ in filtered[:3]:
            icon = "RADIOBUT_ON" if key == scene.arc_selected_outfit else "RADIOBUT_OFF"
            op = results.operator("arc_outfits.select_outfit", text=label, icon=icon)
            op.outfit_key = key
    else:
        controls.label(text="No matching outfits", icon="INFO")
    controls.menu("ARC_OUTFITS_MT_outfit_browse", text="Browse")
    controls.separator(factor=1.5)
    actions = controls.box()
    actions.operator("arc_outfits.load_outfit", text="Load Outfit", icon="IMPORT")
    actions.operator("arc_outfits.load_all_colorways", text="Load All Colorways", icon="DUPLICATE")
    actions.operator("arc_outfits.load_all_outfits_colorways", text="Load All Outfits & Colorways", icon="IMPORT")
    if hasattr(scene, "arc_pose_preset"):
        row = controls.row(align=True)
        row.prop(scene, "arc_pose_preset", text="Pose")
        row.operator("arc_outfits.apply_pose_preset", text="", icon="CHECKMARK")
    _draw_preview_icon(preview, icon_id)


# Draw-path memos: panel redraws must not touch the file system once warm.
# Found icons stay valid while the owning preview collection object is unchanged;
# misses are re-checked at most once per interval (previews load on timers).
_DRAW_MISS_RETRY_S = 1.0
_ICON_MEMO = {}
_PATH_MEMO = {}


def _icon_memo(key, owner, compute):
    import time as _time

    hit = _ICON_MEMO.get(key)
    now = _time.monotonic()
    if hit is not None and hit[1] is owner and (hit[0] or now - hit[2] < _DRAW_MISS_RETRY_S):
        return hit[0]
    icon = int(compute() or 0)
    _ICON_MEMO[key] = (icon, owner, now)
    return icon


def _path_memo(key, compute):
    if key in _PATH_MEMO:
        return _PATH_MEMO[key]
    value = compute()
    if len(_PATH_MEMO) > 512:
        _PATH_MEMO.clear()
    _PATH_MEMO[key] = value
    return value


def _appearance_preview_icon_id(choice_id: str) -> int:
    if not choice_id or choice_id == "NONE":
        return 0
    try:
        from .Outfit_Accessories import appearance

        return _icon_memo(("choice", choice_id), appearance._PREVIEW_COLLECTION,
                          lambda: appearance.preview_icon_id(choice_id))
    except Exception:
        return 0


def _outfit_preview_icon_cached(context) -> int:
    from .Outfit_Accessories import appearance

    scene = context.scene
    key = ("outfit",
           getattr(scene, "arc_selected_outfit", "") or getattr(scene, "arc_selected_outfit_browse", "") or "",
           getattr(scene, "arc_outfit_csv_path", ""))
    return _icon_memo(key, appearance._PREVIEW_COLLECTION, lambda: appearance.outfit_preview_icon_id(context))


def _add_or_all(layout, context, key, *, text, icon, op_id, all_text, all_op_id, part=None, extras=()):
    """Add button with an "Import All" checkbox on the row under it; checked, the button runs Add All.

    ``extras``: ``(data, prop, text, only_with_all)`` checkboxes drawn beside "All".
    """
    wm = getattr(context, "window_manager", None)
    prop = f"arc_add_all_{key.lower()}"
    use_all = bool(getattr(wm, prop, False))
    op = layout.operator(all_op_id if use_all else op_id, text=all_text if use_all else text,
                         icon="IMPORT" if use_all else icon)
    if part is not None:
        op.part = part
    shown = [(d, p, t) for d, p, t, only_all in extras
             if d is not None and hasattr(d, p) and (use_all or not only_all)]
    if (wm is not None and hasattr(wm, prop)) or shown:
        row = layout.row()
        if wm is not None and hasattr(wm, prop):
            row.prop(wm, prop, text="Import All")
        for d, p, t in shown:
            row.prop(d, p, text=t)
    return op


def _draw_head_section(layout, context):
    """Left: Head dropdown + Add Head. Right: capped-width preview."""
    scene = context.scene
    choice_id = getattr(scene, "arc_appearance_head", "NONE")
    icon_id = _appearance_preview_icon_id(choice_id)
    left, right = _split_controls_preview(layout, context, icon_id, rows=3)
    left.prop(scene, "arc_appearance_head", text="Head")
    _add_or_all(left, context, "HEAD", text="Add Head", icon="USER", op_id="arc_outfits.add_appearance_part",
                all_text="Add All Heads", all_op_id="arc_outfits.add_all_appearance", part="HEAD",
                extras=((scene, "arc_appearance_eyelash_copy_hair_color", "Eyelash copy Hair Color", False),))
    _draw_preview_icon(right, icon_id)


def _draw_hair_section(layout, context, *, facial=False):
    """Left: Hair dropdown, color, Update, Add. Right: Bald or style preview."""
    scene = context.scene
    prop = "arc_appearance_facial_hair" if facial else "arc_appearance_hair"
    label = "Facial Hair" if facial else "Hair"
    part = "FACIAL_HAIR" if facial else "HAIR"
    color_prop = "arc_appearance_facial_hair_color" if facial else "arc_appearance_hair_color"
    choice_id = getattr(scene, prop, "NONE")
    if not choice_id or choice_id == "NONE":
        try:
            from .Outfit_Accessories import appearance

            icon_id = _icon_memo(("bald",), appearance._PREVIEW_COLLECTION, appearance.bald_preview_icon_id)
        except Exception:
            icon_id = 0
    else:
        icon_id = _appearance_preview_icon_id(choice_id)
    left, right = _split_controls_preview(
        layout, context, icon_id,
        rows=6 if hasattr(scene, color_prop) else 4,
    )
    left.prop(scene, prop, text=label)
    if hasattr(scene, color_prop):
        left.prop(scene, color_prop, text="Hair Color")
        color_action = left.operator(
            "arc_outfits.update_hair_color",
            text="Update Hair Color",
            icon="COLOR",
        )
        color_action.part = part
    _add_or_all(left, context, part, text=f"Add {label}", icon="PARTICLE_DATA", op_id="arc_outfits.add_appearance_part",
                all_text=f"Add All {label}", all_op_id="arc_outfits.add_all_appearance", part=part)
    _draw_preview_icon(right, icon_id)


def _draw_gear_section(layout, context, *, prop: str, part: str, label: str, icon: str):
    """Dropdown + Add + optional preview for backpack / attachment / charm / tool."""
    scene = context.scene
    if not hasattr(scene, prop):
        return
    choice_id = getattr(scene, prop, "NONE")
    icon_id = _appearance_preview_icon_id(choice_id)
    left, right = _split_controls_preview(
        layout, context, icon_id,
        rows=6 if part == "BACKPACK" and hasattr(scene, "arc_appearance_backpack_colorway") else 4,
    )
    left.prop(scene, prop, text=label)
    if part == "BACKPACK" and hasattr(scene, "arc_appearance_backpack_colorway"):
        left.prop(scene, "arc_appearance_backpack_colorway", text="Colorway")
        left.operator("arc_outfits.update_backpack_colorway", text="Update Colorway", icon="COLOR")
    plural = {"Backpack": "Backpacks", "Attachment": "Attachments", "Charm": "Charms", "Raider Tool": "Raider Tools"}[label]
    _add_or_all(left, context, part, text=f"Add {label}", icon=icon, op_id="arc_outfits.add_appearance_part",
                all_text=f"Add All {plural}", all_op_id="arc_outfits.add_all_appearance", part=part)
    _draw_preview_icon(right, icon_id)


def _draw_queue_report(layout, context):
    scene = context.scene
    from .Outfit_Accessories import appearance_queue
    from .Shared import operators as outfit_operators
    batch = outfit_operators.all_outfits_colorways_status()
    if batch:
        box = layout.box()
        box.label(text=f"All Outfits: {batch['done']}/{batch['total']} · Imported {batch['imported']} · Failed {batch['failed']}")
        box.label(text=f"{batch['phase']}: {batch['current'][:55]}")
        if batch['phase'].startswith('Auditing'):
            box.label(text=f"Material audit: {batch['audit_done']}/{batch['audit_total']}")
    elif scene.get("arc_all_outfits_colorways_progress"):
        import json
        try:
            last = json.loads(scene["arc_all_outfits_colorways_progress"])
            box = layout.box()
            failed = last.get('failed_outfits', len(last.get('failures', [])))
            unresolved = last.get('unresolved_materials', len(last.get('untextured_after_repair', [])))
            setup_count = last.get('unique_material_setups', [])
            setup_count = setup_count if isinstance(setup_count, int) else len(setup_count)
            box.label(text=f"All Outfits: {last['imported_outfits']}/{last['total_outfits']} imported · {failed} failed")
            box.label(text=f"Material audit unresolved: {unresolved} · {setup_count} shader setups")
            box.label(text=f"Report: {os.path.basename(last['report'])}")
        except (ValueError, KeyError, TypeError):
            pass
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


def _draw_appearance_section(layout, context):
    """Body / head / hair / gear as sidebar options (not sequential dialogs)."""
    scene = context.scene
    if not hasattr(scene, "arc_appearance_head"):
        return
    body_sec = _collapsible(layout, "arc_appearance_body", "Body", default_closed=True)
    if body_sec is not None:
        body_sec.prop(scene, "arc_appearance_body_skin", text="Body Skin")
        _add_or_all(body_sec, context, "BODY", text="Add Body", icon="ARMATURE_DATA",
                    op_id="arc_outfits.add_appearance_part", all_text="Add All Bodies",
                    all_op_id="arc_outfits.add_all_appearance", part="BODY")

    head_sec = _collapsible(layout, "arc_appearance_head_sec", "Head", default_closed=True)
    if head_sec is not None:
        _draw_head_section(head_sec, context)

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
    layout.operator(
        "arc_outfits.merge_selected_armatures",
        text="Merge Selected Armatures",
        icon="BONE_DATA",
    )






def _draw_animations_content(layout, context):
    from .Animations import animation_catalog as acat
    from .Animations import animation_import as aimp

    scene = context.scene
    if hasattr(scene, "arc_pose_preset"):
        row = layout.row(align=True)
        row.prop(scene, "arc_pose_preset", text="Pose")
        row.operator("arc_outfits.apply_pose_preset", text="", icon="CHECKMARK")
    if not hasattr(scene, "arc_anim_search"):
        return
    cache_dir = getattr(scene, "arc_animation_cache", "") or ""
    layout.prop(scene, "arc_anim_kind_filter", text="Kind")
    row = layout.row(align=True)
    row.prop(scene, "arc_anim_search", text="", icon="VIEWZOOM")
    if scene.arc_anim_search:
        row.operator("arc_outfits.clear_anim_search", text="", icon="X")
    query = scene.arc_anim_search or ""
    kind = getattr(scene, "arc_anim_kind_filter", "ALL") or "ALL"
    selected = getattr(scene, "arc_selected_anim", "") or ""
    results = layout.box()
    for entry in acat.filter_entries(query, kind=kind, cache_dir=cache_dir, limit=3):
        key = entry.get("key") or ""
        row = results.row()
        icon = "RADIOBUT_ON" if key == selected else "RADIOBUT_OFF"
        row.operator("arc_outfits.select_anim", text=acat.display_label(entry)[:60], icon=icon).anim_key = key
    arm = aimp.resolve_target_armature(context)
    if arm is not None:
        layout.label(text=arm.name, icon="ARMATURE_DATA")
    layout.separator(factor=1.5)
    row = layout.box().row(align=True)
    row.operator("arc_outfits.apply_anim", text="Apply Animation", icon="PLAY")
    row.operator("arc_outfits.clear_anim_props", text="", icon="X")
    advanced = _collapsible(layout, "arc_anim_advanced", "Advanced", default_closed=True)
    if advanced is not None:
        advanced.prop(scene, "arc_anim_spawn_notifies", text="Spawn notify props / FX")
        advanced.prop(scene, "arc_anim_replace_action", text="Replace previous Action")
        advanced.operator("arc_outfits.pick_animation_cache", icon="FILEBROWSER")
        if cache_dir:
            advanced.label(text=os.path.basename(cache_dir.rstrip("/\\")), icon="FILE_FOLDER")
            advanced.operator("arc_outfits.clear_animation_cache", text="", icon="X")
        advanced.operator("arc_outfits.refresh_anim_catalog", text="Refresh Catalog", icon="FILE_REFRESH")


def _draw_mask_debug(layout, scene):
    if not hasattr(scene, "arc_mask_debug_mode"):
        return
    dbg = _collapsible(layout, "arc_mask_debug", "Mask Debug", default_closed=True)
    if dbg is None:
        return
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

def _draw_outfit_tools_content(layout, context):
    from .Outfit_Accessories import properties; from .Shared import importing

    scene = context.scene

    outfit_sec = _collapsible(layout, "arc_outfit_selector", "Outfit Selector", default_closed=False)
    if outfit_sec is not None:
        from .Outfit_Accessories.properties import outfit_csv_path_cached

        csv_path = outfit_csv_path_cached(context)
        if not csv_path:
            outfit_sec.label(text="No CSV found — browse Outfit Reference CSV in Settings.", icon="ERROR")
        elif not hasattr(scene, "arc_outfit_search"):
            outfit_sec.label(
                text="Scene properties missing — disable/enable Arc Asset Importer.",
                icon="ERROR",
            )
        else:
            try:
                outfit_icon = _outfit_preview_icon_cached(context)
            except Exception:
                outfit_icon = 0
            _draw_outfit_selector_preview(outfit_sec, context, outfit_icon)

    _draw_appearance_section(layout, context)

def _draw_niagara_fx_content(layout, context):

    draw_niagara_fx_box(layout, context)


def _draw_settings_content(layout, context):
    from .Maps.map_tools import fmodel_bridge as bridge

    scene = context.scene
    _draw_offset_settings(layout, context)
    textures = _collapsible(layout, "arc_texture_settings", "Textures", default_closed=True)
    if textures is not None:
        try:
            from .Shared import texture_sets
            texture_sets.draw_settings(textures, context)
        except ImportError:
            textures.label(text="Texture settings not installed", icon="INFO")
    advanced = _collapsible(layout, "arc_fmodel_bridge", "Advanced", default_closed=True)
    if advanced is None:
        return
    _draw_fmodel_folder_picker(advanced, context)
    _draw_mask_debug(advanced, scene)
    row = advanced.row(align=True)
    row.prop(scene, "arc_usmap_path", text="Usmap")
    row.operator("arc_outfits.pick_usmap", text="", icon="FILEBROWSER")
    row.operator("arc_outfits.clear_usmap", text="", icon="X")
    advanced.operator("arc_outfits.clear_graph_cache", icon="TRASH")
    advanced.prop(scene, "arc_outfit_csv_path")
    row = advanced.row(align=True)
    row.operator("arc_outfits.group_by_asset_type", text="Group by Asset Type", icon="OUTLINER_COLLECTION")
    row.operator("arc_outfits.group_by_outfit_slot", text="Group by Outfit Slot", icon="OUTLINER_COLLECTION")
    fx = _collapsible(advanced, "arc_fx_settings", "Niagara FX", default_closed=True)
    if fx is not None:
        for name in ("arc_ns_play_cache_dir", "arc_ns_play_shim", "arc_ns_play_vector_fields"):
            if hasattr(scene, name):
                fx.prop(scene, name)
    parity = _collapsible(advanced, "arc_parity_settings", "Shader Parity", default_closed=True)
    if parity is not None:
        from .Shared.materials.native.shader_parity.ui import draw_settings
        draw_settings(parity, context)
    listener = _collapsible(advanced, "arc_listener_settings", "FModel Bridge", default_closed=True)
    if listener is not None:
        if hasattr(scene, "arc_auto_listen"):
            listener.prop(scene, "arc_auto_listen", text="Auto-start Listener")
        if bridge.is_listening():
            listener.operator("arc_outfits.stop_placement_listener", text="Stop Listener", icon="PAUSE")
        else:
            listener.operator("arc_outfits.start_placement_listener", text="Start Listener", icon="PLAY")
        if hasattr(scene, "arc_placement_listen_port"):
            listener.prop(scene, "arc_placement_listen_port", text="TCP Port")


def _draw_map_placement_section(layout, context):
    try:
        from .Outfit_Accessories import addon_line as _addon_line

        if _addon_line.is_map_importer_line():
            map_body = _collapsible(layout, "arc_map_placement", "Map Placement", default_closed=False)
            if map_body is not None:
                draw_map_placement_panel(map_body, context)
    except Exception as exc:
        layout.label(text=f"Map UI unavailable: {exc}", icon="ERROR")


def _panel_name():
    import sys
    info = getattr(sys.modules.get(__package__.split('.')[0]), 'bl_info', {})
    return info.get('name', 'Arc Asset Importer')


_SC_WHOLE_SWATCHES = (("Eq0", "Primary"), ("Eq1", "Secondary"), ("Eq2", "Accent"), ("Eq3", "Accent 2"))
# The look sliders: same order and labels in the Whole, Part and Zone tabs.
_SC_LOOK = (("arc_uc_hue", "Hue"), ("arc_uc_sat", "Saturation"), ("arc_uc_val", "Value"),
            ("arc_uc_wear", "Wear"), ("arc_uc_dirt", "Dirt"), ("arc_uc_wet", "Wetness"),
            ("arc_uc_snow", "Snow"), ("arc_uc_rust", "Rust"))


def _sc_palette(scene):
    """{param name: item} of the Palette colour items in the current control list."""
    return {i.name: i for i in scene.arc_shader_items if i.kind == "COLOR" and i.group == "Palette"}


def _sc_look(box, context, sco, root):
    """The look sliders. An edited control gets its own reset button on its row."""
    scene = context.scene
    edited = sco.uc_edited(root, sco.uc_part(context))
    col = box.column(align=True)
    for prop, label in _SC_LOOK:
        if not hasattr(scene, prop):
            continue
        row = col.row(align=True)
        row.prop(scene, prop, text=label, slider=True)
        if prop in edited:
            row.operator("arc.user_control_reset", text="", icon="LOOP_BACK").prop = prop
        elif edited:
            row.label(text="", icon="BLANK1")


def _sc_status(context, sco, root, mode):
    """One plain line: what is selected and what the sliders below change."""
    if root is None:
        return "Nothing selected: pick or select an outfit or weapon part", "INFO"
    scene = context.scene
    part = sco.uc_part(context)
    if mode == "WHOLE":
        return f"{root.name}: sliders change all parts", "OUTLINER_OB_EMPTY"
    if mode == "PART":
        if part is None:
            return f"{root.name}: pick a part to edit it alone", "INFO"
        return f"Part {part.name}: sliders change this part only", "MESH_DATA"
    zone = getattr(scene, "arc_shader_pick_zone", 0)
    if zone and part is not None:
        from .Shared.shader_pick import OUTFIT_ZONE_NAMES, WEAPON_ZONE_NAMES
        names = WEAPON_ZONE_NAMES if scene.arc_shader_pick_zone_kind == "weapon" else OUTFIT_ZONE_NAMES
        return f"Zone {names.get(zone, 'Zone %d' % zone)} on {part.name}: sliders change this part", "COLOR"
    if part is not None:
        return f"Part {part.name}: no zone picked, sliders change this part", "MESH_DATA"
    return f"{root.name}: pick a colour zone or decal", "INFO"


def _draw_shader_control_content(layout, context):
    """Whole / Part / Zone-Decal tabs. Each tab: pick button, target label, its controls, its reset.
    Colourway presets, Cold Snap snow and the older controls sit in closed sections below."""
    from .Shared import shader_control_ops as sco
    scene = context.scene
    if not hasattr(scene, "arc_shader_select_mode"):
        layout.label(text="Shader controls not registered", icon="ERROR")
        return
    mode = scene.arc_shader_select_mode
    layout.row(align=True).prop(scene, "arc_shader_select_mode", expand=True)
    obj = context.active_object
    root = sco.asset_root(obj) if obj is not None else None
    text, icon = _sc_status(context, sco, root, mode)
    layout.label(text=text, icon=icon)
    box = layout.box()
    row = box.row(align=True)
    row.operator("arc.pick_shader_zone", text="Pick in Viewport", icon="EYEDROPPER")
    from .Shared import shader_pick as _sp
    if _sp.highlight_state() is not None:
        row.operator("arc.shader_highlight_clear", text="Clear Highlight", icon="X")
    if root is None:
        pass
    elif mode == "WHOLE":
        _draw_shader_whole(box, context, sco, root)
    elif mode == "PART":
        _draw_shader_part(box, context, sco, root)
    else:
        _draw_shader_zone_edit(box, context, sco, root)
    if hasattr(scene, "arc_colourways"):
        cbox = _collapsible(layout, "arc_shader_colourways", "Colourway presets", default_closed=True)
        if cbox is not None:
            row = cbox.row(align=True)
            row.prop(scene, "arc_colourway_name", text="")
            row.operator("arc.shader_colourway_save", text="Save Current", icon="ADD")
            cbox.template_list("ARC_UL_colourways", "", scene, "arc_colourways", scene, "arc_colourway_index", rows=3)
            row = cbox.row(align=True)
            row.operator("arc.shader_colourway_apply", text="Apply", icon="CHECKMARK")
            row.operator("arc.shader_colourway_delete", text="Delete", icon="TRASH")
            row.operator("arc.shader_colourway_export", text="Export…", icon="EXPORT")
            row.operator("arc.shader_colourway_import", text="Import…", icon="IMPORT")
    if hasattr(scene, "arc_cold_snap_outdoors"):
        snow = _collapsible(layout, "arc_shader_cold_snap", "Cold Snap snow", default_closed=True)
        if snow is not None:
            col = snow.column(align=True)
            col.prop(scene, "arc_cold_snap_outdoors", text="Outdoors", slider=True)
            col.prop(scene, "arc_cold_snap_sheltered", text="Sheltered", slider=True)
            snow.label(text=f"Snow {scene.arc_uc_snow:.2f}   Wetness {scene.arc_uc_wet:.2f}")
    if root is not None:
        adv = _collapsible(layout, "arc_shader_advanced", "Advanced", default_closed=True)
        if adv is not None:
            _draw_shader_advanced(adv, context, sco, root)


def _draw_shader_whole(box, context, sco, root):
    scene = context.scene
    pal = _sc_palette(scene)
    col = box.column(align=True)
    shown = 0
    for name, label in _SC_WHOLE_SWATCHES:
        item = pal.get(name)
        if item is not None:
            col.prop(item, "color_value", text=label)
            shown += 1
    if not shown:
        box.label(text="No palette colours on this asset", icon="INFO")
    _sc_look(box, context, sco, root)
    box.operator("arc.shader_reset_outfit", text="Reset Outfit", icon="LOOP_BACK")


def _draw_shader_part(box, context, sco, root):
    from .Shared.shader_pick import OUTFIT_ZONE_NAMES
    scene = context.scene
    part = sco.uc_part(context)
    if part is None:
        return
    pal = _sc_palette(scene)
    col = box.column(align=True)
    shown = 0
    for zone in range(1, 9):
        item = pal.get(f"Eq{zone - 1}")
        if item is None:
            continue
        row = col.row(align=True)
        row.label(text=f"{zone}  {OUTFIT_ZONE_NAMES.get(zone, 'Zone %d' % zone)}")
        row.prop(item, "color_value", text="")
        row.operator("arc.shader_zone_select", text="", icon="RESTRICT_SELECT_OFF").zone = zone
        shown += 1
    if not shown:
        box.label(text="No palette zones on this part", icon="INFO")
    _sc_look(box, context, sco, root)
    box.operator("arc.shader_reset_part", text="Reset Part", icon="LOOP_BACK")


def _draw_shader_zone_edit(box, context, sco, root):
    from .Shared.materials import user_controls as _uc
    scene = context.scene
    zone = getattr(scene, "arc_shader_pick_zone", 0)
    decal = getattr(scene, "arc_shader_pick_decal", 0)
    if not zone and not decal:
        box.label(text="Pick a colour zone or decal", icon="INFO")
        if sco.uc_part(context) is not None:
            _sc_look(box, context, sco, root)
        return
    if zone:
        item = sco.zone_item(scene)
        if item is not None:
            box.prop(item, "color_value", text="Zone colour")
        if sco.zone_offsets_supported(context):
            col = box.column(align=True)
            for key, _b, label, _k, *_rest in _uc.ZO_CHANNELS:
                prop = sco.zo_prop(key)
                if hasattr(scene, prop):
                    col.prop(scene, prop, text=label)
        elif item is None:
            box.label(text="This zone has no editable controls", icon="INFO")
    if decal:
        op = box.operator("arc.drag_decal", text=f"Move Decal {decal}", icon="EMPTY_ARROWS")
        op.active_slot = decal
    if sco.uc_part(context) is not None:
        _sc_look(box, context, sco, root)
    box.operator("arc.shader_zone_reset", text="Reset Zone", icon="LOOP_BACK")


def _draw_shader_advanced(layout, context, sco, root):
    """Controls from before the first-version panel. Kept working, shown only on request."""
    scene = context.scene
    row = layout.row(align=True)
    row.operator("arc.shader_control_reset_all", icon="LOOP_BACK")
    row.operator("arc.shader_tweaks_save", text="Save Tweaks…", icon="EXPORT")
    row.operator("arc.shader_tweaks_load", text="Load Tweaks…", icon="IMPORT")
    if getattr(scene, "arc_shader_select_mode", "") in ("PART", "ZONE"):
        layout.operator("arc.shader_tweaks_sync_parts", text="Copy Part to All Parts", icon="DUPLICATE")
    outdated = sco.outdated_groups() if hasattr(sco, "outdated_groups") else []
    if outdated:
        row = layout.row(align=True)
        row.label(text=f"{len(outdated)} shader groups outdated", icon="ERROR")
        row.operator("arc.shader_upgrade", text="Upgrade", icon="FILE_REFRESH")
    if hasattr(scene, "arc_shader_pick_material"):
        row = layout.row(align=True)
        row.operator("arc.shader_pick", text="Pick Material", icon="EYEDROPPER")
        if scene.arc_shader_pick_material:
            row.label(text=f"Material: {scene.arc_shader_pick_material}", icon="MATERIAL")
            row.operator("arc.shader_pick_clear", text="", icon="X")
    pmat = sco.pro_material(context) if hasattr(sco, "pro_material") else None
    if pmat is not None:
        targets = sco.pro_available(pmat)
        row = layout.row(align=True)
        for key, label in sco.PRO_TOGGLES:
            sub = row.row(align=True)
            sub.enabled = bool(targets[key])
            op = sub.operator("arc.shader_pro_toggle", text=label, depress=sco.pro_state(pmat, key))
            op.key = key
            op.material = pmat.name
    if hasattr(scene, "arc_shader_search"):
        row = layout.row(align=True)
        row.prop(scene, "arc_shader_search", text="", icon="VIEWZOOM")
        row.prop(scene, "arc_shader_edited_only", text="Edited only", toggle=True)
    groups = sco.visible_groups(context)
    if not groups:
        layout.label(text="No matching shader controls" if scene.arc_shader_items else "No shader controls found")
        return
    collapsed = sco.collapsed_groups(scene)
    for group, entries in groups:
        box = layout.box()
        head = box.row()
        closed = group in collapsed
        head.operator("arc.shader_section_toggle", text="", icon="RIGHTARROW" if closed else "DOWNARROW_HLT", emboss=False).group = group
        head.label(text=group)
        head.operator("arc.shader_control_reset", text="", icon="LOOP_BACK").group = group
        if closed:
            continue
        for item in entries:
            prop = "color_value" if item.kind == "COLOR" else "float_value"
            row = box.row(align=True)
            row.prop(item, prop, text=item.label)
            if item.label != item.name:
                row.operator("arc.shader_param_info", text="", icon="INFO", emboss=False).name = item.name
            if getattr(item, "edited", False):
                row.label(text="", icon="DECORATE_KEYFRAME")


def _draw_box_section(layout, context, panel_id, title, draw_content, *, default_closed=True):
    try:
        body = _collapsible(layout, panel_id, title, default_closed=default_closed)
        if body is not None:
            draw_content(body, context)
    except Exception as exc:
        layout.label(text=f"{title} failed: {exc}", icon="ERROR")
        print(f"Arc Raiders: {title} draw failed: {exc}")


class ARC_OUTFITS_PT_MainPanel(bpy.types.Panel):
    bl_label = _panel_name()
    bl_idname = "ARC_OUTFITS_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Arc Asset Importer"

    def draw(self, context):
        layout = self.layout
        from .Outfit_Accessories import properties

        if not properties.ensure_scene_properties():
            layout.label(
                text="Scene properties missing — disable/enable Arc Asset Importer.",
                icon="ERROR",
            )
            return

        from .Maps import staged_import
        staged_import.draw_progress(layout, context)
        _draw_queue_report(layout, context)

        # Outfit Tools first and open. Weapons/Animations stay closed so FMDex
        # walks and weapon enums are not rebuilt on every N-panel open.
        _draw_import_section(layout, context)
        _draw_box_section(layout, context, "arc_shader_control", "Asset Shader Control", _draw_shader_control_content, default_closed=True)
        _draw_box_section(layout, context, "arc_outfit_tools", "Outfit Tools", _draw_outfit_tools_content, default_closed=False)
        _draw_box_section(layout, context, "arc_weapons", "Weapons", _draw_weapons_content)
        _draw_box_section(layout, context, "arc_enemies", "ARCs", _draw_arc_content)
        _draw_box_section(layout, context, "arc_animations", "Animations", _draw_animations_content)
        _draw_box_section(layout, context, "arc_niagara_fx", "Niagara FX", _draw_niagara_fx_content)
        _draw_box_section(layout, context, "arc_lighting_look", "Lighting Look", _draw_lighting_look_content)
        _draw_box_section(layout, context, "arc_settings", "Settings", _draw_settings_content)
        _draw_map_placement_section(layout, context)


class ARC_OUTFITS_MT_outfit_browse(bpy.types.Menu):
    bl_label = "Browse"

    def draw(self, context):
        from .Outfit_Accessories import properties

        scene = context.scene
        columns = self.layout.column_flow(columns=2)
        for key, label, *_ in properties.make_outfit_selector_items(scene, context):
            if key == "NONE":
                continue
            icon = "RADIOBUT_ON" if key == scene.arc_selected_outfit else "RADIOBUT_OFF"
            columns.operator("arc_outfits.select_outfit", text=label, icon=icon).outfit_key = key




def draw_niagara_fx_box(layout, context) -> None:
    pass

    scene = context.scene
    catalog_root = getattr(scene, "arc_ns_catalog_root", "") or ""
    selected = getattr(scene, "arc_ns_selected", "") or ""
    query = getattr(scene, "arc_ns_search", "") or ""
    row = layout.row(align=True)
    row.prop(scene, "arc_ns_search", text="", icon="VIEWZOOM")
    if query:
        row.operator("arc_outfits.clear_ns_search", text="", icon="X")
    lut_only = bool(getattr(scene, "arc_ns_lut_only", False))
    results = layout.box()
    for entry in filter_entries(query, catalog_root=catalog_root, lut_only=lut_only, limit=3):
        key = entry.get("key") or ""
        icon = "RADIOBUT_ON" if key == selected else "RADIOBUT_OFF"
        results.operator("arc_outfits.select_ns", text=display_label(entry)[:64], icon=icon).ns_key = key
    layout.separator(factor=1.5)
    layout.box().row().operator("arc_outfits.import_niagara_fx", text="Import Selected", icon="PARTICLES")
    advanced = _collapsible(layout, "arc_ns_advanced", "Advanced", default_closed=True)
    if advanced is not None:
        advanced.prop(scene, "arc_ns_lut_only", text="LUT dumps only")
        advanced.prop(scene, "arc_ns_bake_curves")
        advanced.prop(scene, "arc_ns_json_path", text="Or NS JSON file")
        advanced.operator("arc_outfits.import_niagara_fx", text="Import NS File", icon="FILEBROWSER").use_selected = False
        advanced.prop(scene, "arc_ns_catalog_root", text="Catalog Folder")
        advanced.prop(scene, "arc_ns_asset_root", text="Asset Root")
        pass
        draw_playback_box(advanced, context)
def draw_playback_box(layout, context):
    scene = context.scene
    if not hasattr(scene, 'arc_ns_play_ticks'):
        return
    box = layout.box()
    col = box.column(align=True)
    col.prop(scene, 'arc_ns_play_ticks')
    col.prop(scene, 'arc_ns_play_at_cursor')
    col.prop(scene, 'arc_ns_play_collision')
    box.operator(ARC_OUTFITS_OT_PlayNiagaraFX.bl_idname, icon='PLAY')

def draw_map_placement_panel(layout, context):
    """Map Placement + gameplay import UI (map-importer line)."""
    scene = context.scene
    box = layout.box()
    box.label(text="Map Placement", icon="WORLD")
    if hasattr(scene, "arc_placement_map"):
        box.prop(scene, "arc_placement_map", text="Map")
    row = box.row(align=True)
    row.prop(scene, "arc_placement_csv", text="")
    row.operator("arc_outfits.pick_placement_csv", text="", icon="FILEBROWSER")
    col = box.column(align=True)
    col.operator("arc_outfits.import_placement_instanced", text="Stage 1: Import (Instanced)", icon="IMPORT")
    col.operator("arc_outfits.apply_map_materials", text="Stage 2: Apply Materials", icon="MATERIAL")



def draw_sky_controls(layout, scene) -> None:
    world = scene.world
    if world is None or not world.get(_WORLD_TAG) or not world.use_nodes:
        return
    nodes = world.node_tree.nodes
    rotation = nodes.get("ArcSkyRotation")
    if rotation is not None:
        layout.prop(rotation.inputs["Angle"], "default_value", text="Rotation")
    for name in ("Extra_Contrast", "Overall Intensity", "Saturation"):
        control = nodes.get(name)
        if control is not None:
            layout.prop(control.outputs[0], "default_value", text=name)

classes = (
    ARC_OUTFITS_MT_outfit_browse,
    ARC_OUTFITS_PT_MainPanel,
)

def _resolved_str(path):
    return _path_memo(("resolve", str(path)), lambda: str(Path(path).resolve()))


def draw_arc_preview(layout,context,path):
    previews=weapon_ui._PREVIEWS
    icon=0
    if previews is not None:
        cached=previews.get("arc-5x4-512:"+_resolved_str(path))
        if cached is not None:
            try:icon=cached.icon_id
            except Exception:icon=0
    if not icon:
        # A miss queues a timer load; retry at most once per interval per path.
        icon=_icon_memo(("arc",str(path)),previews,lambda:arc_preview_id(path))
    if not icon:return
    prefs=context.preferences.system
    region=context.region
    width=max(64,(region.width if region else 320)-48)
    scale=max(1,width/(20*max(prefs.ui_scale,.5)))
    row=layout.row();row.alignment='CENTER';row.template_icon(icon_value=icon,scale=scale)

def draw_preview(layout,context,path,width_fraction=1.0):
    icon=preview_id(path)
    if icon:
        prefs=context.preferences.system
        # template_icon respects the source aspect within its image area.
        # Scale with the actual sidebar content width, including UI zoom/DPI.
        region=context.region
        width=max(64,(region.width if region else 320)-48)
        scale=max(1,width*width_fraction/(20*max(prefs.ui_scale,.5)))
        row=layout.row();row.alignment='CENTER';row.template_icon(icon_value=icon,scale=scale)

def draw_gun_preview(layout,context,path):
    region=context.region
    display=region.as_pointer() if region else 0
    key=_resolved_str(path) if path is not None else None
    texture=_GUN_TEXTURES.get(key)
    if texture is not None:
        try:texture.name
        except ReferenceError:
            _GUN_TEXTURES.pop(key,None);texture=None
    if texture is None and key is not None and key not in _GUN_FAILED:
        _GUN_PENDING.add(key)
        if not bpy.app.timers.is_registered(_prepare_gun_textures):
            bpy.app.timers.register(_prepare_gun_textures,first_interval=0.0)
        texture=_GUN_TEXTURES.get(_GUN_DISPLAY.get(display))
        if texture is not None:
            try:texture.name
            except ReferenceError:texture=None
    elif texture is not None:
        _GUN_DISPLAY[display]=key
    else:
        _GUN_DISPLAY.pop(display,None)
    # template_icon fits into a square even when its row is rectangular. A
    # native texture preview fills the row; keep its image stable on resize.
    width=max(64,(region.width if region else 320)-48)
    row=layout.row()
    row.use_property_split=False;row.use_property_decorate=False
    # The preview is display-only. Disable Blender's built-in resize grip.
    row.enabled=False
    if texture is None:
        row.scale_y=width*.4/(20*max(context.preferences.system.ui_scale,.5))
        row.label(text='')
        return
    row.scale_y=width*.4/(20*7.6*max(context.preferences.system.ui_scale,.5))
    row.template_preview(texture,show_buttons=False,preview_id='arc_weapon_gun_fixed_5x2')

def _weapon_section(layout,id,label):
    header,body=layout.panel(id,default_closed=True);header.label(text=label);return body
def _draw_weapons_content(layout,context):
    scene=context.scene
    if not weapon_ui.ensure_registered():
        # Keep the original selector usable while deferred registration runs.
        layout.prop(scene,'arc_weapon_key',text='Gun')
        return
    layout.prop(scene,'arc_weapon_browser_key',text='Gun')
    entry=catalog.weapon_by_key(getattr(scene,'arc_weapon_key','NONE'))
    variant=getattr(scene,'arc_weapon_ascended','OFF') or 'OFF'
    levels=assets.bundled().get('weapons',{}).get(entry['stem'],{}).get('quality_levels',[0,1,2,3]) if entry else [0,1,2,3]
    tier=str(levels[0]) if variant=='OFF' and len(levels)==1 else getattr(scene,'arc_weapon_tier','3')
    if entry:draw_gun_preview(layout,context,assets.bundled_preview(entry,variant,tier=tier,stencil=getattr(scene,'arc_weapon_skin','NONE')))
    _add_or_all(layout,context,'WEAPONS',text='Import Weapon',icon='IMPORT',op_id='arc_outfits.import_weapon',all_text='Add All Weapons',all_op_id='arc_outfits.add_all_weapons',extras=((scene,'arc_weapon_include_ascended','Include Ascended Varients',True),))
    layout.label(text='Gun Position')
    layout.prop(scene,'arc_weapon_position',text='')
    layout.label(text='Tier (wear)')
    row=layout.row(align=True);row.enabled=len(levels)>1;row.prop(scene,'arc_weapon_tier',expand=True)
    layout.operator('arc_outfits.set_weapon_tier',text='Set Tier on Selection',icon='MOD_NOISE')
    layout.operator('arc_outfits.update_weapon_position',text='Update Position',icon='CON_LOCLIKE')
    layout.separator()
    layout.label(text='Ascended Varient')
    layout.prop(scene,'arc_weapon_ascended',text='')
    mods=_weapon_section(layout,'arc_weapon_mods_v2','Mods')
    if mods is not None:
        for kind,prop in [('Muzzle','arc_weapon_mod_muzzle'),('Stock','arc_weapon_mod_stock'),('Magazine','arc_weapon_mod_magazine'),('UnderBarrel','arc_weapon_mod_underbarrel')]:
            row=mods.row(align=True);row.prop(scene,prop,text='');op=row.operator('arc_outfits.import_weapon_mod',text='',icon='IMPORT');op.mod_type=kind
            mod=catalog.mod_by_key(getattr(scene,prop,'NONE'))
            if mod:draw_preview(mods,context,assets.bundled_preview(mod,mod=True),width_fraction=.5)
    skin=_weapon_section(layout,'arc_weapon_skin_v2','Skin')
    if skin is not None:
        skin.prop(scene,'arc_weapon_skin',text='Colourway')
        row=skin.row(align=True);row.operator('arc_outfits.apply_weapon_skin',text='Apply Skin',icon='CHECKMARK');row.operator('arc_outfits.clear_weapon_skin',text='Base',icon='X')

def _draw_arc_content(layout, context):
    scene = context.scene
    if not hasattr(scene, "arc_enemy_key"):
        return

    layout.prop(scene, "arc_enemy_key", text="ARC")
    selected = arc_catalog_data.by_key(scene.arc_enemy_key)
    if selected:
        _key, _label, filename = selected
        image_path = _path_memo(("pioneer", filename), lambda: _pioneer_image_path(filename))
        if image_path:
            draw_arc_preview(layout, context, image_path)

    _add_or_all(layout, context, "ARC", text="Add Arc", icon="IMPORT", op_id="arc_outfits.import_codex_enemy",
                all_text="Add All Arc", all_op_id="arc_outfits.add_all_arc")
    layout.prop(scene, "arc_scan_state", text="Alert Level")
    layout.operator("arc_outfits.update_scan_state", icon="FILE_REFRESH")
    active = getattr(context, "active_object", None)
    if active is not None and "arc_alertness_state" in active:
        # Object property: keyframe it to animate idle/alert/combat transitions.
        layout.prop(active, '["arc_alertness_state"]', text="AlertnessState", slider=True)


def _section(layout, panel_id: str, title: str, toggle: str | None, scene, default_closed=True):
    panel_fn = getattr(layout, "panel", None)
    if callable(panel_fn):
        header, body = panel_fn(panel_id, default_closed=default_closed)
        if toggle and hasattr(scene, toggle):
            header.prop(scene, toggle, text=title)
        else:
            header.label(text=title)
        return body
    box = layout.box()
    if toggle and hasattr(scene, toggle):
        box.prop(scene, toggle, text=title)
    else:
        box.label(text=title)
    return box

def _draw_lighting_look_content(layout, context) -> None:
    scene = context.scene
    col = layout.column()
    if not hasattr(scene, "arc_lighting_map") or not hasattr(scene, "arc_lighting_exposure_mode"):
        col.label(text="Lighting props missing — reload the add-on", icon="ERROR")
        return
    if not cat.map_ids():
        col.label(text="Lighting catalog missing from assets/", icon="ERROR")
        return

    col.prop(scene, "arc_lighting_map", text="Map", icon="WORLD")
    col.prop(scene, "arc_lighting_scenario", text="Scenario", icon="LIGHT_SUN")
    if hasattr(scene, "arc_lighting_import_camera"):
        col.prop(scene, "arc_lighting_import_camera", text="Camera", icon="CAMERA_DATA")
    row = col.row(align=True)
    row.scale_y = 1.4
    row.operator("arc_outfits.apply_lighting_look", text="Apply Lighting Look", icon="CHECKMARK")
    col = _collapsible(col, "arc_lighting_advanced", "Advanced", default_closed=True)
    if col is None:
        return

    body = _section(col, "arc_lighting_color", "Game Color Grade", "arc_lighting_apply_lut", scene, default_closed=True)
    if body is not None:
        body.active = bool(scene.arc_lighting_apply_lut)
        body.prop(scene, "arc_lighting_exposure_mode", text="Exposure")
        row = body.row(align=True)
        if scene.arc_lighting_exposure_mode == "MANUAL":
            row.prop(scene, "arc_lighting_meter_ev", text="Meter EV100")
        row.prop(scene, "arc_lighting_exposure_comp", text="Comp")
        row = body.row(align=True)
        row.prop(scene, "arc_lighting_apply_bloom", text="Bloom", toggle=True)
        row.prop(scene, "arc_lighting_apply_vignette", text="Vignette", toggle=True)
        row.prop(scene, "arc_lighting_apply_local_exposure", text="Local Exp", toggle=True)

    body = _section(col, "arc_lighting_sky", "Sky & Sun", "arc_lighting_apply_hdri", scene)
    if body is not None:
        body.active = bool(scene.arc_lighting_apply_hdri)
        row = body.row(align=True)
        row.prop(scene, "arc_lighting_apply_sun", text="Sun Lamp", toggle=True)
        row.prop(scene, "arc_lighting_apply_clouds", text="Clouds", toggle=True)
        row = body.row(align=True)
        row.prop(scene, "arc_lighting_hdri_strength", text="Strength")
        row.prop(scene, "arc_lighting_cloud_time", text="Cloud Time")
        draw_sky_controls(body, scene)

    body = _section(col, "arc_lighting_fog", "Fog Box", "arc_lighting_apply_fog", scene)
    if body is not None:
        body.active = bool(scene.arc_lighting_apply_fog)
        row = body.row(align=True)
        row.prop(scene, "arc_lighting_fog_size_x", text="X")
        row.prop(scene, "arc_lighting_fog_size_y", text="Y")
        row.prop(scene, "arc_lighting_fog_size_z", text="Z")

    body = _section(col, "arc_lighting_override_panel", "Override LUT / Clouds", "arc_lighting_override", scene)
    if body is not None:
        body.active = bool(scene.arc_lighting_override)
        body.prop(scene, "arc_lighting_look", text="LUT")
        body.prop(scene, "arc_lighting_lut_intensity", text="LUT Intensity", slider=True)
        body.prop(scene, "arc_lighting_hdri", text="Clouds")
