"""
UI panels for Arc Model Importer
"""

import os
import bpy


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
    box.prop(scene, "arc_import_source", text="Import:")
    if getattr(scene, "arc_import_source", "PAK") != "PAK":
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
    if hasattr(scene, "arc_pose_preset"):
        row = layout.row(align=True)
        row.prop(scene, "arc_pose_preset", text="Pose")
        row.operator("arc_outfits.apply_pose_preset", text="", icon="CHECKMARK")


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
    from . import properties
    scene = context.scene
    controls, preview = _split_controls_preview(layout, context, icon_id, rows=8)
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
    controls.menu("ARC_OUTFITS_MT_outfit_browse", text="Browse:")
    controls.separator(factor=1.5)
    actions = controls.box()
    actions.operator("arc_outfits.load_outfit", text="Load Outfit", icon="IMPORT")
    actions.operator("arc_outfits.load_all_colorways", text="Load All Colorways", icon="DUPLICATE")
    actions.operator("arc_outfits.load_all_outfits_colorways", text="Load All Outfits & Colorways", icon="IMPORT")
    _draw_preview_icon(preview, icon_id)


def _appearance_preview_icon_id(choice_id: str) -> int:
    if not choice_id or choice_id == "NONE":
        return 0
    try:
        from . import appearance

        return int(appearance.preview_icon_id(choice_id) or 0)
    except Exception:
        return 0


def _draw_head_section(layout, context):
    """Left: Head dropdown + Add Head. Right: capped-width preview."""
    scene = context.scene
    choice_id = getattr(scene, "arc_appearance_head", "NONE")
    icon_id = _appearance_preview_icon_id(choice_id)
    has_eyelash = hasattr(scene, "arc_appearance_eyelash_copy_hair_color")
    left, right = _split_controls_preview(layout, context, icon_id, rows=4 if has_eyelash else 3)
    left.prop(scene, "arc_appearance_head", text="Head")
    op = left.operator(
        "arc_outfits.add_appearance_part", text="Add Head", icon="USER",
    )
    op.part = "HEAD"
    op = left.operator("arc_outfits.add_all_appearance", text="Add All Heads", icon="IMPORT")
    op.part = "HEAD"
    if has_eyelash:
        left.prop(scene, "arc_appearance_eyelash_copy_hair_color", text="Eyelash copy Hair Color")
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
            from . import appearance

            icon_id = int(appearance.bald_preview_icon_id() or 0)
        except Exception:
            icon_id = 0
    else:
        icon_id = _appearance_preview_icon_id(choice_id)
    left, right = _split_controls_preview(
        layout, context, icon_id,
        rows=5 if hasattr(scene, color_prop) else 3,
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
    left, right = _split_controls_preview(
        layout, context, icon_id,
        rows=5 if part == "BACKPACK" and hasattr(scene, "arc_appearance_backpack_colorway") else 3,
    )
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


def _draw_queue_report(layout, context):
    scene = context.scene
    from . import appearance_queue
    from . import operators as outfit_operators
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
    pak_dir = getattr(scene, "arc_pak_dir", "") or ""
    if not getattr(scene, "arc_pioneer_root", "") and not pak_dir:
        layout.label(text="Set Steam Install or Paks Folder in Settings", icon="ERROR")

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


def _draw_weapons_content(layout, context):
    from . import weapon_ui
    weapon_ui.draw(layout, context)


def _draw_arc_content(layout, context):
    from . import arc_ui
    arc_ui.draw(layout, context)


def _draw_animations_content(layout, context):
    from . import animation_catalog as acat
    from . import animation_import as aimp

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
    from . import properties, importing

    scene = context.scene

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
            try:
                from . import appearance as _appearance

                outfit_icon = int(_appearance.outfit_preview_icon_id(context) or 0)
            except Exception:
                outfit_icon = 0
            _draw_outfit_selector_preview(outfit_sec, context, outfit_icon)

    _draw_appearance_section(layout, context)

def _draw_settings_content(layout, context):
    from .map_tools import fmodel_bridge as bridge

    scene = context.scene
    _draw_pak_folder_picker(layout, context)
    _draw_offset_settings(layout, context)
    advanced = _collapsible(layout, "arc_fmodel_bridge", "Advanced", default_closed=True)
    if advanced is None:
        return
    _draw_fmodel_folder_picker(advanced, context)
    _draw_mask_debug(advanced, scene)
    advanced.prop(scene, "arc_usmap_path")
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
        from .material_runtime.shader_parity.ui import draw_settings
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


def _panel_name():
    import sys
    info = getattr(sys.modules.get(__package__), 'bl_info', {})
    return info.get('name', 'Arc Model Importer')


class ARC_OUTFITS_PT_MainPanel(bpy.types.Panel):
    bl_label = _panel_name()
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

        _draw_queue_report(layout, context)

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
            arc_body = _collapsible(layout, "arc_enemies", "ARCs", default_closed=True)
            if arc_body is not None:
                _draw_arc_content(arc_body, context)
        except Exception as exc:
            layout.label(text=f"ARCs failed: {exc}", icon="ERROR")
            print(f"Arc Raiders: ARCs draw failed: {exc}")

        try:
            anim_body = _collapsible(layout, "arc_animations", "Animations", default_closed=True)
            if anim_body is not None:
                _draw_animations_content(anim_body, context)
        except Exception as exc:
            layout.label(text=f"Animations failed: {exc}", icon="ERROR")
            print(f"Arc Raiders: Animations draw failed: {exc}")

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


class ARC_OUTFITS_MT_outfit_browse(bpy.types.Menu):
    bl_label = "Browse"

    def draw(self, context):
        from . import properties

        scene = context.scene
        columns = self.layout.column_flow(columns=2)
        for key, label, *_ in properties.make_outfit_selector_items(scene, context):
            if key == "NONE":
                continue
            icon = "RADIOBUT_ON" if key == scene.arc_selected_outfit else "RADIOBUT_OFF"
            columns.operator("arc_outfits.select_outfit", text=label, icon=icon).outfit_key = key


classes = (
    ARC_OUTFITS_MT_outfit_browse,
    ARC_OUTFITS_PT_MainPanel,
)
