"""
UI panels for Arc Asset Importer
"""

import os
import bpy
from pathlib import Path
from .Arc import arc_catalog_data
from .Arc.arc_ui import _pioneer_image_path

# Niagara, Lighting and Weapons load on first draw of their section, not at addon import.
_LAZY = {}


def _lazy(name):
    mod = _LAZY.get(name)
    if mod is None:
        import importlib
        mod = _LAZY[name] = importlib.import_module("." + name, __package__)
    return mod


def filter_entries(*args, **kwargs):
    return _lazy("Niagara.niagara").filter_entries(*args, **kwargs)


def display_label(entry):
    return _lazy("Niagara.niagara").display_label(entry)


class _SidebarLayout:
    def __init__(self, layout, headings=False):
        object.__setattr__(self, "_layout", layout)
        object.__setattr__(self, "_headings", headings)
        layout.use_property_split = False
        layout.use_property_decorate = False

    def __getattr__(self, name):
        value = getattr(self._layout, name)
        if name in {"row", "column", "column_flow", "box", "split"}:
            return lambda *args, **kwargs: _SidebarLayout(value(*args, **kwargs), self._headings)
        if name == "panel":
            def panel(*args, **kwargs):
                header, body = value(*args, **kwargs)
                return _SidebarLayout(header, headings=True), _SidebarLayout(body) if body is not None else None
            return panel
        if name == "progress":
            def progress(*args, **kwargs):
                kwargs["text"] = ""
                return value(*args, **kwargs)
            return progress
        return value

    def __setattr__(self, name, value):
        setattr(self._layout, name, False if name == "use_property_split" else value)

    def label(self, **kwargs):
        if self._headings:
            return self._layout.label(**kwargs)
        return None

    def heading(self, **kwargs):
        return self._layout.label(**kwargs)


def _collapsible(layout, panel_id: str, title: str, default_closed: bool = True, icon: str = "NONE"):
    """Blender 4.1+ inline panel; falls back to always-open box."""
    panel_fn = getattr(layout, "panel", None)
    if callable(panel_fn):
        header, body = panel_fn(panel_id, default_closed=default_closed)
        getattr(header, "heading", header.label)(text=title, icon=icon)
        return body
    box = layout.box()
    getattr(box, "heading", box.label)(text=title, icon=icon)
    return box



_DIR_OK = {}


def _dir_exists(path):
    """Draw-path folder check, refreshed at most once every two seconds per path."""
    import time
    now = time.monotonic()
    hit = _DIR_OK.get(path)
    if hit is None or now - hit[1] > 2.0:
        if len(_DIR_OK) > 16:
            _DIR_OK.clear()
        hit = _DIR_OK[path] = (os.path.isdir(path), now)
    return hit[0]


def _timed(key, compute, seconds=2.0, _memo={}):
    """Draw-path value refreshed at most once per interval per key."""
    import time
    now = time.monotonic()
    hit = _memo.get(key)
    if hit is None or now - hit[1] > seconds:
        if len(_memo) > 32:
            _memo.clear()
        hit = _memo[key] = (compute(), now)
    return hit[0]


def _pretty_name(text):
    """Asset names for people: "Aeronaut(Jet_Pilot)" reads "Aeronaut (Jet Pilot)"."""
    text = text.replace(" (mesh unavailable)", " (no mesh)").replace("_", " ")
    out = []
    for index, char in enumerate(text):
        if char == "(" and index and text[index - 1] != " ":
            out.append(" ")
        out.append(char)
    return "".join(out)


def _pretty_stem(label, prefix=""):
    """First part of a "stem · folder · tag" catalog label, without prefix or underscores."""
    import re
    stem = label.split("\u00b7")[0].strip()
    if prefix and stem.startswith(prefix):
        stem = stem[len(prefix):]
    stem = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem.replace("_", " "))
    return re.sub(r" 0+$", "", stem)


def _show_developer():
    try:
        from .Shared import prefs
        return bool(prefs.get("show_developer", False))
    except Exception:
        return False


def _ui_lists():
    # Lazy: tests exec this module with a fake bpy that cannot load the list module.
    try:
        from .Shared import ui_lists
        return ui_lists
    except Exception:
        return None


def _import_gen():
    """ui_targets.import_generation() for draw cache keys; 0 when unavailable."""
    try:
        from .Shared import ui_targets
        return ui_targets.import_generation()
    except Exception:
        return 0


_PART_ROLES = {"HAIR": "hair", "FACIAL_HAIR": "facial_hair", "BACKPACK": "backpack"}


def _target_line(context, role, verb="Editing"):
    """(name, status text) of the Target the edit rows for `role` act on; ("", "") fails closed.

    One ui_targets.resolve result drives both the "Editing: X" / "Editing last imported: X"
    line and the enabled state, so draw and the operator poll agree.
    """
    active = getattr(context, "active_object", None)
    selected = getattr(context, "selected_objects", None) or ()
    key = ("target_line", role, verb, getattr(active, "name", ""), len(selected), len(bpy.data.objects),
           (globals().get("_import_gen") or (lambda: 0))())

    def compute():
        try:
            from .Shared import ui_targets
            target = ui_targets.resolve(context, role)
            if target is None:
                return "", ""
            return str(getattr(target, "name", "") or ""), ui_targets.editing_text(target, verb)
        except Exception:
            return "", ""
    return _timed(key, compute, 0.5)


def _edit_target(context, part):
    """Status text for the imported part the apply buttons change, or ""."""
    return _target_line(context, _PART_ROLES.get(part, str(part or "").lower()))[1]


def _role_target_name(context, role):
    """Display name of the imported asset the edit rows for `role` change, or ""."""
    return _target_line(context, role)[0]


def _pick_label(module, key, fallback):
    fn = getattr(module, "display_label", None) if module is not None else None
    if fn is None or not key or key in ("NONE", ""):
        return fallback
    try:
        return _timed(("pick_label", getattr(module, "__name__", ""), key), lambda: fn(key) or fallback, 30.0)
    except Exception:
        return fallback


def _draw_picker_empty(layout, context, lists, kind):
    """Favorites/Recent empty state under a search picker, with Show All. True when drawn."""
    empty_fn = getattr(lists, "draw_filter_empty", None)
    keys_fn = getattr(lists, "filter_keys", None)
    mode_fn = getattr(lists, "filter_mode", None)
    if empty_fn is None or keys_fn is None or mode_fn is None:
        return False
    try:
        keys = keys_fn(kind, mode_fn(context, kind))
        return bool(empty_fn(layout, context, kind, 1 if keys is None else len(keys)))
    except Exception:
        return False


def _part_destination(context, mode):
    """ui_targets.part_destination(context, mode) memoised for draw; {} fails closed."""
    active = getattr(context, "active_object", None)
    selected = getattr(context, "selected_objects", None) or ()
    key = ("part_dest", mode, getattr(active, "name", ""), len(selected), len(bpy.data.objects),
           (globals().get("_import_gen") or (lambda: 0))())

    def compute():
        try:
            from .Shared import ui_targets
            plan = ui_targets.part_destination(context, mode)
            return dict(plan) if plan else {}
        except Exception:
            return {}
    return _timed(key, compute, 0.5)


def _scene_has_character(scene):
    def compute():
        try:
            from .Shared import ui_targets
            return bool(ui_targets.scene_has_character(scene))
        except Exception:
            return True
    return _timed(("has_character", getattr(scene, "name", ""), len(getattr(scene, "objects", ()) or ())),
                  compute, 1.0)


def _path_problem(name, value):
    if not value:
        return ""
    try:
        from .Shared import prefs
        return _timed(("path", name, value), lambda: prefs._path_problem(name, value))
    except Exception:
        return ""


def _source_state(scene):
    """(state, message): missing / invalid / indexing / ready. Uses source_status when the add-on has it."""
    for module_name in ("prefs", "pak_extract"):
        try:
            module = __import__(__package__ + ".Shared." + module_name, fromlist=["_"]) if __package__ else None
        except Exception:
            module = None
        status = getattr(module, "source_status", None)
        if callable(status):
            try:
                return _timed(("source_status", module_name), lambda: tuple(status(scene)), 1.0)
            except Exception:
                break
    path = getattr(scene, "arc_pak_dir", "") or ""
    if not path:
        return "missing", ""
    if not _dir_exists(path):
        return "invalid", "Folder not found. Choose the game folder again"
    return "ready", ""


def _has_imports(scene):
    """True once anything in this file came from the add-on; cheap, cached."""
    def compute():
        for obj in getattr(scene, "objects", ()) or ():
            try:
                if obj.get("arc_import_id") or obj.get("arc_assembly_role") or obj.get("arc_weapon_assembly") \
                        or obj.get("arc_codex_enemy_key"):
                    return True
            except Exception:
                continue
        return False
    return _timed(("has_imports", getattr(scene, "name", ""), len(getattr(scene, "objects", ()) or ())), compute, 2.0)




def _draw_pak_folder_picker(layout, context):
    row = layout.row(align=True)
    row.prop(context.scene, "arc_pak_dir", text="Game Folder")
    row.operator("arc_outfits.pick_pak_dir", text="", icon="FILEBROWSER")
    row.operator("arc_outfits.reindex_paks", text="", icon="FILE_REFRESH")
    if not getattr(context.scene, "arc_pak_dir", ""):
        layout.operator("arc_outfits.detect_game_install", text="Find Steam Install", icon="VIEWZOOM")


def _draw_fmodel_folder_picker(layout, context):
    row = layout.row(align=True)
    row.prop(context.scene, "arc_pioneer_root", text="FModel Export Folder")
    row.operator("arc_outfits.pick_pioneer_root", text="", icon="FILEBROWSER")
    row.operator("arc_outfits.clear_pioneer_root", text="", icon="X")



def _draw_import_section(layout, context):
    box = layout.box()
    scene = context.scene
    from_game = getattr(scene, "arc_import_source", "PAK") == "PAK"
    source = box.split(factor=0.5, align=True)
    source.prop_enum(scene, "arc_import_source", "PAK")
    source.prop_enum(scene, "arc_import_source", "FILES")
    if from_game:
        _draw_pak_folder_picker(box, context)
    else:
        _draw_fmodel_folder_picker(box, context)
    actions = box.split(factor=0.5, align=True)
    if from_game:
        actions.operator("arc_outfits.import_from_pak", text="Import Single", icon="FILE").select_folder = False
        actions.operator("arc_outfits.import_from_pak", text="Import Folder", icon="FILE_FOLDER").select_folder = True
    else:
        actions.operator("arc_outfits.import_single_psk", text="Import Single", icon="FILE")
        actions.operator("arc_outfits.import_outfit_folder", text="Import Folder", icon="FILE_FOLDER")
        box.operator("arc_outfits.import_again", text="Import Again", icon="FILE_REFRESH")



def _draw_offset_settings(layout, context):
    """Asset placement and character fit controls at the top of Settings."""
    scene = context.scene
    if not hasattr(scene, "arc_import_slot_offset"):
        return
    layout.prop(scene, "arc_import_slot_offset", text="Place Imports Side by Side")
    if getattr(scene, "arc_import_slot_offset", True) and hasattr(scene, "arc_import_slot_spacing"):
        pair = layout.column(align=True)
        pair.prop(scene, "arc_import_slot_spacing", text="Spacing X")
        if hasattr(scene, "arc_import_slot_row_spacing"):
            pair.prop(scene, "arc_import_slot_row_spacing", text="Spacing Y")


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


def _split_controls_preview(layout, context, icon_id, *, rows=3, factor=0.5):
    """Keep controls stacked on narrow sidebars and previews beside wide controls."""
    width = _sidebar_width(context)
    if icon_id and width >= 430.0:
        split = layout.split(factor=factor, align=True)
        controls = split.column(align=True)
        preview = split.column(align=True)
        preview.alignment = "CENTER"
        return controls, (preview, min(width * (1.0 - factor) - 12.0, 256.0), rows * 20.0)
    controls = layout.column(align=True)
    if not icon_id:
        return controls, None
    preview = layout.column(align=True)
    preview.alignment = "CENTER"
    return controls, (preview, min(max(64.0, width - 12.0), 256.0), None)


def _draw_preview_icon(preview, icon_id):
    if preview is None or not icon_id:
        return
    column, available_width, max_height = preview
    # template_icon supports independent thumbnails in the same region.
    # Blender reserves a square and adds its own small internal border.
    size = available_width if max_height is None else min(available_width, max_height)
    column.template_icon(icon_value=icon_id, scale=max(0.5, size / 20.0))


def _draw_outfit_selector_preview(layout, context, icon_id, _memo={}):
    from .Outfit_Accessories import properties
    scene = context.scene
    controls, preview = _split_controls_preview(layout, context, icon_id, rows=11, factor=0.6)
    # The text field has its own clear button once text is typed.
    search = controls.row(align=True)
    search.prop(scene, "arc_outfit_search", text="", icon="VIEWZOOM", placeholder="Search outfits")
    search.operator("arc_outfits.icon_browser", text="", icon="IMGDISPLAY").kind = "outfit"
    query = scene.arc_outfit_search.casefold()
    raw = properties.make_outfit_selector_items(scene, context)
    selected_key = scene.arc_selected_outfit or getattr(scene, "arc_selected_outfit_browse", "")
    lists = _ui_lists()
    # The items callback returns one cached list per CSV state; refilter only on a new list or query.
    memo_key = (id(raw), len(raw), query)
    hit = _memo.get(memo_key)
    if hit is None or hit[0] is not raw:
        if len(_memo) > 32:
            _memo.clear()
        hit = _memo[memo_key] = (raw,) + lists.outfit_rows(raw, query, _pretty_name)
    rows, total = hit[1], hit[2]
    lists.request_sync("outfit", memo_key, lambda: rows, selected_key)
    selected = next((row for row in rows if row[0] == selected_key), None)
    # Preview, Colorway and Import follow the visible list: a hidden choice is not shown or imported.
    is_visible = getattr(lists, "outfit_is_visible", None)
    if selected is not None and callable(is_visible):
        def check_visible():
            try:
                return bool(is_visible(context, selected_key, rows))
            except TypeError:
                try:
                    return bool(is_visible(context, selected_key))
                except Exception:
                    return False
            except Exception:
                return False
        # The key holds every input of the answer: the current query rows, filter mode and its keys.
        try:
            mode = lists.filter_mode(context, "outfit")
            keys = lists.filter_keys("outfit", mode)
        except Exception:
            mode, keys = "ALL", None
        fkeys = None if keys is None else tuple(keys)
        try:
            visible = _timed(("outfit_visible", selected_key, memo_key, mode, fkeys), check_visible, 0.5)
        except NameError:
            visible = check_visible()
        if not visible:
            selected = None
    header = getattr(lists, "draw_list_header", None)
    wm = context.window_manager
    if callable(header):
        if header(controls, context, "outfit", rows, total, "outfits", "arc_outfit_search", query):
            controls.template_list("ARC_UL_rows", "arc_outfits", wm, "arc_outfit_rows", wm, "arc_outfit_rows_index", rows=3)
    elif rows:
        controls.template_list("ARC_UL_rows", "arc_outfits", wm, "arc_outfit_rows", wm, "arc_outfit_rows_index", rows=3)
    else:
        controls.label(text="No matching outfits", icon="INFO")
    _draw_preview_icon(preview, icon_id if selected is not None else 0)
    # Full width below the list and preview: what Import Outfit will load, then the button.
    unavailable = selected is not None and not selected[2]
    if unavailable:
        warn = layout.row()
        warn.alert = True
    scene = context.scene
    if selected is not None and hasattr(scene, "arc_outfit_colorway"):
        colorway = layout.column()
        colorway.use_property_split = True
        colorway.use_property_decorate = False
        colorway.prop(scene, "arc_outfit_colorway", text="Colorway")
    if selected is None and selected_key:
        hidden = layout.row()
        hidden.active = False
        try:
            from .Shared import ui_targets as _uit
            hidden_text = getattr(_uit, "OUTFIT_HIDDEN_MESSAGE", "")
        except Exception:
            hidden_text = ""
    elif selected is None:
        pick = layout.row()
        pick.active = False
    actions = layout.row(align=True)
    actions.scale_y = 1.4
    actions.enabled = selected is not None and not unavailable
    actions.operator("arc_outfits.load_outfit", text="Import Outfit", icon="IMPORT")
    actions.menu("ARC_OUTFITS_MT_outfit_import_more", text="", icon="DOWNARROW_HLT")
    options = _collapsible(layout, "arc_outfit_options", "Outfit Options", default_closed=True)
    if options is not None:
        if hasattr(scene, "arc_include_reference"):
            options.prop(scene, "arc_include_reference", text="Add Reference Image with Each Outfit")
        if hasattr(scene, "arc_inherit_shape_key"):
            options.prop(scene, "arc_inherit_shape_key", text="Fit Parts to Body Shape")


# Draw-path memos: panel redraws must not touch the file system once warm.
# Found icons stay valid while the owning preview collection object is unchanged;
# misses are re-checked at most once per interval (previews load on timers).
_DRAW_MISS_RETRY_S = 1.0
_ICON_MEMO = {}
_PATH_MEMO = {}


def _icon_memo(key, owner, compute):
    import time as _time

    from .Outfit_Accessories import appearance as _appearance

    hit = _ICON_MEMO.get(key)
    now = _time.monotonic()
    # A miss is retried as soon as any preview finishes loading, not after a fixed wait.
    generation = _appearance._PREVIEW_GENERATION
    if hit is not None and hit[1] is owner and hit[3] == generation and (
            hit[0] or now - hit[2] < _DRAW_MISS_RETRY_S):
        return hit[0]
    icon = int(compute() or 0)
    _ICON_MEMO[key] = (icon, owner, now, generation)
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



def _part_mode_text(context, text):
    """(button text, destination ok) for a part button under the current part mode."""
    scene = getattr(context, "scene", None)
    if scene is None or not hasattr(scene, "arc_part_mode"):
        return text, True
    mode = str(getattr(scene, "arc_part_mode", "ADD") or "ADD")
    noun = text[4:] if text.startswith("Add ") else text
    try:
        from .Shared import ui_targets
        label = ui_targets.part_button_text(mode, noun)
    except Exception:
        label = text
    try:
        plan = _part_destination(context, mode)
    except NameError:
        plan = {}
    ok = mode == "NEW" or bool(plan and not plan.get("error"))
    return label, ok


_PROMPT_GEAR_PARTS = ("BACKPACK", "ATTACHMENT", "CHARM", "RAIDER_TOOL")


def _browse_button(layout, kind):
    op = layout.operator("arc_outfits.icon_browser", text="", icon="IMGDISPLAY")
    op.kind = kind
    return op


def _prop_with_browse(layout, scene, prop, text, kind):
    """Dropdown plus a small icon-browser button pre-filtered to ``kind``."""
    row = layout.row(align=True)
    row.prop(scene, prop, text=text)
    _browse_button(row, kind)
    return row


def _add_part_button(layout, *, text, icon, part, enabled=True, context=None):
    """Full-width import button for one character part; text and enabled state follow the part mode."""
    ok = True
    if context is not None:
        try:
            text, ok = _part_mode_text(context, text)
        except NameError:
            pass
    reason = ""
    if not ok and context is not None:
        try:
            mode = str(getattr(context.scene, "arc_part_mode", "ADD") or "ADD")
            reason = str((_part_destination(context, mode) or {}).get("error") or "")
        except (NameError, AttributeError):
            reason = ""
    elif not enabled:
        reason = "Pick one above to add it"
    if reason:
        hint = layout.row()
        hint.active = False
    row = layout.row()
    row.enabled = bool(enabled) and ok
    # Gear goes through the duplicate prompt (keep, hide or remove the current item).
    op_id = "arc_outfits.add_gear_part" if part in _PROMPT_GEAR_PARTS else "arc_outfits.add_appearance_part"
    op = row.operator(op_id, text=text, icon=icon)
    op.part = part
    return op


def _draw_apply_row(layout, context, part, *, op_id, text, icon="COLOR"):
    """One "Editing: <name>" line and its apply button; the button greys out without a target."""
    try:
        target = _edit_target(context, part)
    except NameError:
        target = ""
    row = layout.row()
    row.enabled = bool(target)
    return row.operator(op_id, text=text, icon=icon)


def _draw_head_section(layout, context):
    """Left: Head dropdown + Add Head. Right: capped-width preview."""
    scene = context.scene
    choice_id = getattr(scene, "arc_appearance_head", "NONE")
    icon_id = _appearance_preview_icon_id(choice_id)
    left, right = _split_controls_preview(layout, context, icon_id, rows=3)
    left.use_property_split = True
    left.use_property_decorate = False
    _prop_with_browse(left, scene, "arc_appearance_head", "Head", "head")
    if hasattr(scene, "arc_appearance_eyelash_copy_hair_color"):
        left.prop(scene, "arc_appearance_eyelash_copy_hair_color", text="Match Eyelashes to Hair")
    _add_part_button(left, text="Add Head", icon="USER", part="HEAD", enabled=choice_id not in ("", "NONE"),
                     context=context)
    _draw_preview_icon(right, icon_id)


def _draw_hair_section(layout, context, *, facial=False):
    """Left: style, color, Add, Apply Color. Right: Bald or style preview."""
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
    left.use_property_split = True
    left.use_property_decorate = False
    _prop_with_browse(left, scene, prop, "Style", part.lower())
    if hasattr(scene, color_prop):
        left.prop(scene, color_prop, text="Color")
    _add_part_button(left, text="Add " + label, icon="PARTICLE_DATA", part=part,
                     enabled=bool(choice_id) and choice_id != "NONE", context=context)
    if hasattr(scene, color_prop):
        # Recolors hair that is already in the scene; Add above brings in a new one.
        action = _draw_apply_row(left, context, part, op_id="arc_outfits.update_hair_color",
                                 text="Apply " + label + " Color")
        action.part = part
    _draw_preview_icon(right, icon_id)


_GEAR_SLOTS = {
    "BACKPACK": ("arc_appearance_backpack", "Add Backpack", "OUTLINER_OB_ARMATURE"),
    "ATTACHMENT": ("arc_appearance_attachment", "Add Backpack Attachment", "MODIFIER"),
    "CHARM": ("arc_appearance_charm", "Add Charm", "LINKED"),
    "RAIDER_TOOL": ("arc_appearance_raider_tool", "Add Raider Tool", "TOOL_SETTINGS"),
}
_GEAR_LABELS = {"BACKPACK": "Backpack", "ATTACHMENT": "Backpack Attachment", "CHARM": "Charm",
                "RAIDER_TOOL": "Raider Tool"}


def _backpack_has_colorways(scene, context, choice_id):
    """True when the chosen backpack has a colorway besides its base color (memoised)."""
    if choice_id in ("", "NONE"):
        return False

    def count():
        try:
            from .Outfit_Accessories import appearance
            return len(appearance.make_backpack_colorway_enum_items(scene, context) or ())
        except Exception:
            return 2
    return _timed(("bp_colorways", choice_id), count, 5.0) > 1


def _draw_gear_section(layout, context, *, prop: str, part: str, text: str, icon: str):
    """Item dropdown + Add + preview for backpack / attachment / charm / tool."""
    scene = context.scene
    if not hasattr(scene, prop):
        return
    choice_id = getattr(scene, prop, "NONE")
    icon_id = _appearance_preview_icon_id(choice_id)
    has_colorway = part == "BACKPACK" and hasattr(scene, "arc_appearance_backpack_colorway")
    left, right = _split_controls_preview(layout, context, icon_id, rows=6 if has_colorway else 4)
    left.use_property_split = True
    left.use_property_decorate = False
    try:
        slot_label = _GEAR_LABELS.get(part, "Item")
    except NameError:
        slot_label = "Item"
    _prop_with_browse(left, scene, prop, slot_label, part.lower())
    if has_colorway:
        left.prop(scene, "arc_appearance_backpack_colorway", text="Colorway")
    _add_part_button(left, text=text, icon=icon, part=part, enabled=choice_id not in ("", "NONE"),
                     context=context)
    if has_colorway:
        try:
            has_ways = _backpack_has_colorways(scene, context, choice_id)
        except NameError:
            has_ways = True
        if not has_ways and choice_id not in ("", "NONE"):
            pass
        sub = left.column()
        sub.enabled = has_ways
        _draw_apply_row(sub, context, "BACKPACK", op_id="arc_outfits.update_backpack_colorway",
                        text="Apply Backpack Colorway")
    _draw_preview_icon(right, icon_id)


def _job_api():
    try:
        from .Shared import ui_targets
    except Exception:
        return None
    return ui_targets if hasattr(ui_targets, "job_active") else None


def _draw_job_card(layout, context):
    """One card for the running job, else one for the finished report. Returns 'active', 'report' or ''."""
    api = _job_api()
    if api is None:
        return ""
    try:
        job = api.job_active()
    except Exception:
        job = None
    if job:
        box = layout.box()
        done, total = int(job.get("done") or 0), int(job.get("total") or 0)
        head = box.row()
        if total and hasattr(box, "progress"):
            box.progress(factor=min(1.0, done / total), text=f"{round(100 * done / total)}%")
        current = str(job.get("current") or "")
        if current:
            pass
        cancel = job.get("cancel_idname") or ""
        if cancel and job.get("cancellable", True):
            box.operator(cancel, text="Cancel", icon="CANCEL")
        else:
            hint = box.row()
            hint.active = False
        return "active"
    try:
        report = api.job_report()
    except Exception:
        report = None
    if not report:
        return ""
    box = layout.box()
    failed = int(report.get("failed") or 0)
    status = str(report.get("status") or "completed")
    title_fn = getattr(api, "report_title", None)
    error_fn = getattr(api, "report_is_error", None)
    try:
        title = title_fn(report) if callable(title_fn) else ""
    except Exception:
        title = ""
    if not title:
        title = f"Finished with {failed} failures" if failed else "Finished"
    try:
        is_error = bool(error_fn(report)) if callable(error_fn) else False
    except Exception:
        is_error = False
    label = str(report.get("label") or "")
    head = box.row()
    head.alert = is_error
    icon = "ERROR" if is_error else ("CANCEL" if status == "cancelled" else ("INFO" if failed else "CHECKMARK"))
    head.operator("arc_outfits.dismiss_job_report", text="", icon="X", emboss=False)
    lines = list(report.get("lines") or ())
    for line in lines[:3]:
        pass
    if len(lines) > 3:
        more = box.row()
        more.active = False
    buttons = box.row(align=True)
    buttons.operator("arc_outfits.view_details", text="View Details...", icon="TEXT").source = "report"
    buttons.operator("arc_outfits.copy_job_report", text="Copy Report", icon="COPYDOWN")
    return "report"


def _error_in_report(api, err):
    """True when the finished report card already shows this failure (one box per failure)."""
    try:
        report = api.job_report()
    except Exception:
        report = None
    if not report or not int(report.get("failed") or 0):
        return False
    detail = str(err.get("detail") or "")[:60]
    if detail and any(detail in str(line) for line in (report.get("lines") or ())):
        return True
    return int(report.get("failed") or 0) == 1 and int(report.get("total") or 1) <= 1


def _draw_last_error(layout, shown=""):
    api = _job_api()
    getter = getattr(api, "last_error", None) if api else None
    if not callable(getter):
        return
    try:
        err = getter()
    except Exception:
        err = None
    if not err:
        return
    try:
        if shown == "report" and _error_in_report(api, err):
            return
    except NameError:
        pass
    box = layout.box()
    head = box.row()
    head.alert = True
    head.operator("arc_outfits.dismiss_error", text="", icon="X", emboss=False)
    detail = str(err.get("detail") or "")
    if detail:
        pass
    buttons = box.row(align=True)
    if len(detail) > 90 or "\n" in detail:
        buttons.operator("arc_outfits.view_details", text="View Details...", icon="TEXT").source = "error"
    buttons.operator("arc_outfits.copy_error", text="Copy Error", icon="COPYDOWN")


def _draw_queue_report(layout, context):
    """Every job (batches, staged map import, queues) renders through the one job card."""
    return _draw_job_card(layout, context)


def _draw_appearance_section(layout, context):
    """Character Parts section body: where parts go, then Body and Head, Hair, Gear."""
    scene = context.scene
    if not hasattr(scene, "arc_appearance_head"):
        return
    # Where new parts go: stated once, above the part panels it applies to.
    dest = layout.column(align=True)
    has_mode = hasattr(scene, "arc_part_mode")
    if has_mode:
        mode = str(getattr(scene, "arc_part_mode", "ADD") or "ADD")
    elif getattr(scene, "arc_appearance_replace_selected", False):
        mode = "REPLACE"
    elif getattr(scene, "arc_appearance_attach_to_outfit", False):
        mode = "ADD"
    else:
        mode = "NEW"
    # One destination plan (same as the operator computes) drives the line and the buttons.
    try:
        plan = _part_destination(context, mode) if mode != "NEW" else {}
        no_character = mode != "NEW" and not _scene_has_character(scene)
    except NameError:
        plan, no_character = {}, False
    if has_mode:
        dest.row(align=True).prop(scene, "arc_part_mode", expand=True)
    if mode == "NEW":
        pass
    elif no_character:
        # Nothing to add to: New is the only mode that can run here.
        if has_mode:
            dest.prop_enum(scene, "arc_part_mode", "NEW", text="Use New Character", icon="ADD")
    elif plan.get("error") or not plan.get("text"):
        warn = dest.row()
        warn.alert = True
    else:
        pass
    if not has_mode:
        if hasattr(scene, "arc_appearance_attach_to_outfit"):
            dest.prop(scene, "arc_appearance_attach_to_outfit", text="Attach to Selected Outfit")
        if hasattr(scene, "arc_appearance_replace_selected"):
            dest.prop(scene, "arc_appearance_replace_selected", text="Replace Selected Part")

    body_sec = _collapsible(layout, "arc_appearance_body", "Body and Head", default_closed=True)
    if body_sec is not None:
        body = body_sec.column()
        body.use_property_split = True
        body.use_property_decorate = False
        body.prop(scene, "arc_appearance_body_skin", text="Skin Tone")
        _add_part_button(body, text="Add Body", icon="ARMATURE_DATA", part="BODY", context=context)
        body_sec.separator()
        _draw_head_section(body_sec, context)

    hair_sec = _collapsible(layout, "arc_appearance_hair_sec", "Hair", default_closed=True)
    if hair_sec is not None:
        if hasattr(scene, "arc_hair_tab"):
            hair_sec.row(align=True).prop(scene, "arc_hair_tab", expand=True)
            facial = str(getattr(scene, "arc_hair_tab", "HAIR") or "HAIR") != "HAIR"
            _draw_hair_section(hair_sec, context, facial=facial)
        else:
            hair_sec.heading(text="Hair")
            _draw_hair_section(hair_sec, context)
            hair_sec.separator()
            hair_sec.heading(text="Facial Hair")
            _draw_hair_section(hair_sec, context, facial=True)

    for slot, (prop, text, icon) in _GEAR_SLOTS.items():
        gear_sec = _collapsible(layout, "arc_gear_" + slot.lower(), _GEAR_LABELS[slot], default_closed=True)
        if gear_sec is not None:
            _draw_gear_section(gear_sec, context, prop=prop, part=slot, text=text, icon=icon)


def _anim_target(context, aimp, _memo):
    """Target rig for animations; re-resolved at most every 0.5 s for an unchanged selection."""
    import time
    scene = context.scene
    active = getattr(context, "active_object", None)
    try:
        akey = ("arm", getattr(scene, "name", ""), getattr(active, "name", ""),
                tuple(o.name for o in (getattr(context, "selected_objects", None) or ())),
                getattr(scene, "arc_last_applied_anim_armature", ""))
        hit = _memo.get("arm")
        now = time.monotonic()
        if hit is not None and hit[0] == akey and now - hit[1] < 0.5:
            return None if hit[2] is None else hit[2].name
        arm = aimp.resolve_target_armature(context)
        _memo["arm"] = (akey, now, arm)
    except ReferenceError:
        _memo.pop("arm", None)
        arm = aimp.resolve_target_armature(context)
    return None if arm is None else arm.name


def _anim_target_line(context):
    """(status text, rig name) from the shared character Target, or None when ui_targets is missing."""
    active = getattr(context, "active_object", None)
    selected = getattr(context, "selected_objects", None) or ()
    key = ("anim_target", getattr(active, "name", ""), len(selected), len(bpy.data.objects),
           (globals().get("_import_gen") or (lambda: 0))())

    def compute():
        try:
            from .Shared import ui_targets
        except Exception:
            return None
        if not hasattr(ui_targets, "target_armature"):
            return None
        try:
            # The same edit target as "Editing:" above; a weapon or enemy gets a reason, not a rig.
            refusal_fn = getattr(ui_targets, "animation_refusal", None)
            if refusal_fn is not None:
                refusal = refusal_fn(ui_targets.resolve(context, "asset"))
                if refusal:
                    return (refusal, "")
            target = ui_targets.resolve(context, "character")
            arm = ui_targets.target_armature(target) if target is not None else None
            if arm is None:
                return ("", "")
            return (ui_targets.editing_text(target), str(arm.name))
        except Exception:
            return None
    return _timed(key, compute, 0.5)


def _draw_animations_content(layout, context, _memo={}):
    from .Animations import animation_catalog as acat
    from .Animations import animation_import as aimp

    scene = context.scene
    # The rig that Apply acts on, stated before any choice is made; same Target wording as the other tabs.
    try:
        line = _anim_target_line(context)
    except NameError:
        line = None
    if line is None:
        arm_name = _anim_target(context, aimp, _memo)
        edit_text = "Editing: " + arm_name if arm_name is not None else ""
    else:
        edit_text, arm_name = line[0], (line[1] or None)
    if arm_name is not None:
        action_name = ""
        try:
            import bpy
            arm_obj = bpy.data.objects.get(arm_name)
            anim_data = getattr(arm_obj, "animation_data", None)
            action = getattr(anim_data, "action", None)
            action_name = getattr(action, "name", "") or ""
        except Exception:
            action_name = ""
        action_row = layout.row()
        action_row.active = bool(action_name)
    else:
        none_row = layout.row()
        none_row.active = False

    def draw_static_pose():
        if not hasattr(scene, "arc_pose_preset"):
            return
        pose = _collapsible(layout, "arc_anim_static_pose", "Static Pose", default_closed=True, icon="POSE_HLT")
        if pose is None:
            return
        pose_col = pose.column()
        pose_col.use_property_split = True
        pose_col.use_property_decorate = False
        pose_col.prop(scene, "arc_pose_preset", text="Pose")
        # The operator poll greys the button and explains why when no rig resolves.
        pose_col.operator("arc_outfits.apply_pose_preset", text="Apply Pose", icon="POSE_HLT")

    if not hasattr(scene, "arc_anim_search"):
        draw_static_pose()
        return
    cache_dir = getattr(scene, "arc_animation_cache", "") or ""
    layout.prop(scene, "arc_anim_search", text="", icon="VIEWZOOM", placeholder="Search animations")
    top = layout.column()
    top.use_property_split = True
    top.use_property_decorate = False
    top.prop(scene, "arc_anim_kind_filter", text="Type")
    query = scene.arc_anim_search or ""
    kind = getattr(scene, "arc_anim_kind_filter", "ALL") or "ALL"
    selected = getattr(scene, "arc_selected_anim", "") or ""
    lists = _ui_lists()
    # The catalog scan runs in the list timer, never in draw; Reload replaces the entries list.
    catalog = (getattr(acat, "_cache", None) or {}).get("entries")

    tokens = getattr(lists, "display_tokens", None) or (lambda text: text)

    def build():
        found = acat.filter_entries(query, kind=kind, cache_dir=cache_dir, limit=0)
        return lists.named_rows(found, acat.display_label,
                                lambda label: tokens(_pretty_stem(label.split("·")[0].split(" [")[0].split(" (")[0].strip(), "HM_")),
                                late_words=() if query else lists._ANIM_LATE)

    lists.request_sync("anim", (query, kind, cache_dir, id(catalog)), build, selected)
    anim_rows = lists.rows_of(context, "anim")
    shown = len(anim_rows)
    wm = context.window_manager
    header = getattr(lists, "draw_list_header", None)
    if catalog is None:
        pass
    elif callable(header):
        shown = header(layout, context, "anim", anim_rows, len(catalog), "animations", "arc_anim_search",
                       query or ("" if kind == "ALL" else kind))
    elif shown:
        pass
    else:
        pass
    if shown:
        layout.template_list("ARC_UL_rows", "arc_anims", wm, "arc_anim_rows", wm, "arc_anim_rows_index", rows=3)
    row = layout.row(align=True)
    row.scale_y = 1.4
    row.operator("arc_outfits.apply_anim", text="Apply Animation", icon="ACTION")
    draw_static_pose()
    advanced = _collapsible(layout, "arc_anim_advanced", "Animation Options", default_closed=True)
    if advanced is not None:
        advanced.prop(scene, "arc_anim_spawn_notifies", text="Include Animation Props and Effects")
        advanced.prop(scene, "arc_anim_replace_action", text="Replace Current Animation")
        remove = advanced.row()
        remove.enabled = arm_name is not None
        remove.operator("arc_outfits.clear_anim_props", text="Remove Animation Props", icon="TRASH")


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
    dbg.prop(scene, "arc_mask_debug_show_colormask", text="Show Color Mask")
    dbg.prop(scene, "arc_mask_debug_show_numbers", text="Show Numbers")
    dbg = dbg.column(align=True)
    dbg.prop(scene, "arc_mask_debug_grid_scale", text="Grid Scale")
    if hasattr(scene, "arc_mask_debug_overlay_opacity"):
        dbg.prop(scene, "arc_mask_debug_overlay_opacity", text="Overlay Opacity")
    else:
        dbg.prop(scene, "arc_mask_debug_opacity", text="Overlay Opacity")
    if hasattr(scene, "arc_mask_debug_colormask_opacity"):
        dbg.prop(scene, "arc_mask_debug_colormask_opacity", text="Color Mask Opacity")
    dbg.prop(scene, "arc_mask_debug_numbers_opacity", text="Numbers Opacity")
    dbg_row = dbg.row(align=True)
    dbg_row.operator("arc_outfits.mask_debug_apply", text="Apply", icon="HIDE_OFF")
    dbg_row.operator("arc_outfits.mask_debug_clear", text="Clear", icon="LOOP_BACK")

def _draw_outfit_tools_content(layout, context):
    from .Outfit_Accessories import properties; from .Shared import importing

    scene = context.scene

    outfit_sec = layout
    if outfit_sec is not None:
        from .Outfit_Accessories.properties import outfit_csv_path_cached

        csv_path = outfit_csv_path_cached(context)
        if not properties._CSV_STAT_MEMO:
            pass
        elif not csv_path:
            pass
        elif not hasattr(scene, "arc_outfit_search"):
            pass
        else:
            try:
                outfit_icon = _outfit_preview_icon_cached(context)
            except Exception:
                outfit_icon = 0
            _draw_outfit_selector_preview(outfit_sec, context, outfit_icon)


def _draw_niagara_fx_content(layout, context):

    draw_niagara_fx_box(layout, context)



def _addon_version():
    import sys
    info = getattr(sys.modules.get((__package__ or "").split('.')[0]), 'bl_info', {}) or {}
    version = info.get('version') or ()
    return ".".join(str(part) for part in version)


def _draw_source_settings_content(layout, context):
    scene = context.scene
    _draw_import_section(layout, context)
    overrides = _collapsible(layout, "arc_settings_overrides", "Advanced File Overrides", default_closed=True)
    if overrides is not None:
        row = overrides.row(align=True)
        # The path field has its own file browser button.
        row.prop(scene, "arc_usmap_path", text="Mappings (.usmap)")
        row.operator("arc_outfits.clear_usmap", text="", icon="X")
        problem = _path_problem("usmap_path", getattr(scene, "arc_usmap_path", ""))
        if problem:
            warn = overrides.row()
            warn.alert = True
        row = overrides.row(align=True)
        row.prop(scene, "arc_outfit_csv_path", text="Outfit Catalog (.csv)")
        row.operator("arc_outfits.clear_outfit_csv", text="", icon="X")
        hint = overrides.row()
        hint.active = False
        problem = _path_problem("outfit_csv_path", getattr(scene, "arc_outfit_csv_path", ""))
        if problem:
            warn = overrides.row()
            warn.alert = True
        overrides.separator()
        _draw_custom_effect_files(overrides, scene)
        overrides.separator()
        cache_dir = getattr(scene, "arc_animation_cache", "") or ""
        if cache_dir:
            row = overrides.row(align=True)
            row.operator("arc_outfits.pick_animation_cache", text="", icon="FILEBROWSER")
            row.operator("arc_outfits.clear_animation_cache", text="", icon="X")
        else:
            overrides.operator("arc_outfits.pick_animation_cache", text="Use Animation Files from a Folder...",
                               icon="FILEBROWSER")
        overrides.operator("arc_outfits.refresh_anim_catalog", text="Reload Animation List", icon="FILE_REFRESH")


def _draw_settings_content(layout, context):
    from .Maps.map_tools import fmodel_bridge as bridge

    scene = context.scene
    from_game = getattr(scene, "arc_import_source", "PAK") == "PAK"
    developer = _show_developer()
    placement_sec = _collapsible(layout, "arc_settings_placement", "Placement", default_closed=True)
    if placement_sec is not None:
        placement = placement_sec.column()
        placement.use_property_split = True
        placement.use_property_decorate = False
        _draw_offset_settings(placement, context)
        if hasattr(scene, "arc_collection_sort"):
            placement.prop(scene, "arc_collection_sort", text="Collections")
    textures = _collapsible(layout, "arc_texture_settings", "Textures", default_closed=True)
    if textures is not None:
        try:
            from .Shared import texture_sets
            texture_sets.draw_settings(textures, context)
        except ImportError:
            pass
        textures.operator("arc.textures_pack_blend", text="Pack Textures into Blend File", icon="PACKAGE")
        if developer:
            textures.operator("arc_outfits.clear_graph_cache", text="Clear Material Graph Cache...", icon="TRASH")
    batch = _collapsible(layout, "arc_settings_batch", "Batch Import", default_closed=True)
    if batch is not None:
        _draw_batch_import(batch, context)
    if not from_game or developer:
        listener = _collapsible(layout, "arc_listener_settings", "Integration", default_closed=True)
        _draw_fmodel_bridge(listener, scene, bridge)
    if developer:
        _draw_developer_tools(layout, context)
    about = _collapsible(layout, "arc_settings_about", "About", default_closed=True)
    if about is not None:
        more = about.operator("preferences.addon_show", text="Add-on Preferences...", icon="PREFERENCES")
        more.module = __package__ or ""
        about.operator("arc_outfits.copy_diagnostics", text="Copy Diagnostics", icon="COPYDOWN")


def _draw_batch_import(layout, context):
    scene = context.scene
    lists = _ui_lists()
    if not hasattr(scene, "arc_batch_kind") or lists is None:
        layout.operator("arc_outfits.load_all_outfits_colorways", text="Import All Outfits and Colorways...", icon="IMPORT")
        return
    col = layout.column()
    col.use_property_split = True
    col.use_property_decorate = False
    col.prop(scene, "arc_batch_kind", text="Import")
    hint = getattr(lists, "batch_hint", None)
    kind = scene.arc_batch_kind
    count = None
    try:
        # The real job plan, counted on a timer outside draw; None until counted.
        from .Shared import ui_targets as _targets
        plan_fn = getattr(_targets, "batch_plan_count", None)
    except Exception:
        plan_fn = None
    if callable(plan_fn):
        try:
            count = plan_fn(scene, kind)
        except Exception:
            count = None
    else:
        count_fn = getattr(lists, "batch_count", None)
        if callable(count_fn):
            try:
                count = _timed(("batch_count", kind), lambda: count_fn(kind), 30)
            except Exception:
                count = None
    lines = hint(kind, count) if callable(hint) else ""
    if isinstance(lines, str):
        lines = [part.strip() for part in lines.split(". ") if part.strip()]
    note = layout.column(align=True)
    if count is None:
        pass
    for index, line in enumerate(lines or ()):
        pass
    row = layout.row(align=True)
    row.scale_y = 1.3
    row.operator("arc_outfits.review_batch_plan", text="Review Plan...", icon="VIEWZOOM")
    row.operator("arc_outfits.start_batch_import", text="Start Batch Import...", icon="IMPORT")


def _draw_developer_tools(layout, context):
    scene = context.scene
    advanced = _collapsible(layout, "arc_fmodel_bridge", "Developer Tools", default_closed=True)
    if advanced is None:
        return
    _draw_mask_debug(advanced, scene)
    fx = _collapsible(advanced, "arc_fx_settings", "Effect Playback", default_closed=True)
    if fx is not None:
        for name, label in (("arc_ns_play_cache_dir", "Cache Folder"), ("arc_ns_play_shim", "Simulation Math Library"),
                            ("arc_ns_play_vector_fields", "Vector Field Folder")):
            if hasattr(scene, name):
                fx.prop(scene, name, text=label)
    parity = _collapsible(advanced, "arc_parity_settings", "Shader Compatibility", default_closed=True)
    if parity is not None:
        from .Shared.materials.native.shader_parity.ui import draw_settings
        draw_settings(parity, context)


def _draw_fmodel_bridge(listener, scene, bridge):
    if listener is not None:
        listening = bridge.is_listening()
        if listening:
            listener.operator("arc_outfits.stop_placement_listener", text="Stop Receiver", icon="PAUSE")
        else:
            listener.operator("arc_outfits.start_placement_listener", text="Start Receiver", icon="PLAY")
        if hasattr(scene, "arc_auto_listen"):
            listener.prop(scene, "arc_auto_listen", text="Start When a File Opens")
        if hasattr(scene, "arc_placement_listen_port"):
            row = listener.row()
            row.enabled = not listening
            row.prop(scene, "arc_placement_listen_port", text="TCP Port")


def _draw_map_placement_section(layout, context):
    try:
        from .Outfit_Accessories import addon_line as _addon_line

        if _addon_line.is_map_importer_line():
            map_body = _collapsible(layout, "arc_map_placement", "Map Placement", default_closed=False)
            if map_body is not None:
                draw_map_placement_panel(map_body, context)
    except Exception as exc:
        pass


def _panel_name():
    import sys
    import re
    info = getattr(sys.modules.get(__package__.split('.')[0]), 'bl_info', {})
    # The version sits at the bottom of Settings, not in the title.
    return re.sub(r"\s+v\d+(\.\d+)*$", "", info.get('name', 'Arc Asset Importer'))


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
        return "No Arc asset selected", "INFO"
    scene = context.scene
    part = sco.uc_part(context)
    if mode == "WHOLE":
        return "Sliders change all parts", "OUTLINER_OB_EMPTY"
    if mode == "PART":
        if part is None:
            return f"{root.name}: click a part in the viewport to edit it alone", "INFO"
        return f"Editing part {part.name} only", "MESH_DATA"
    zone = getattr(scene, "arc_shader_pick_zone", 0)
    if zone and part is not None:
        from .Shared.shader_pick import OUTFIT_ZONE_NAMES, WEAPON_ZONE_NAMES
        names = WEAPON_ZONE_NAMES if scene.arc_shader_pick_zone_kind == "weapon" else OUTFIT_ZONE_NAMES
        return f"Editing zone {names.get(zone, 'Zone %d' % zone)} on {part.name}", "COLOR"
    if part is not None:
        return f"Part {part.name}: no zone picked yet, sliders change this part", "MESH_DATA"
    return f"{root.name}: pick a color zone or decal", "INFO"


def _draw_shader_control_content(layout, context):
    """Whole / Part / Zone-Decal tabs. Each tab: pick button, target label, its controls, its reset.
    Colorway presets, Cold Snap snow and the older controls sit in closed sections below."""
    from .Shared import shader_control_ops as sco
    scene = context.scene
    if not hasattr(scene, "arc_shader_select_mode"):
        return
    mode = scene.arc_shader_select_mode
    # One target result drives the whole draw: root, name, where it came from and its state.
    info = None
    info_fn = getattr(sco, "shader_target_info", None)
    if info_fn is not None:
        try:
            info = info_fn(context)
        except Exception:
            info = None
    if info is not None:
        state = info.get("state", "") or ""
        state_name = info.get("name", "") or ""
        root = info.get("root") if state == "ready" else None
    else:
        obj = context.active_object
        root = sco.asset_root(obj) if obj is not None else None
        state_fn = getattr(sco, "shader_target_state", None)
        state, state_name = ("", "")
        if state_fn is not None:
            try:
                state, state_name = state_fn(context)
            except Exception:
                state, state_name = ("", "")
        if state == "none":
            root = None
        elif not state and root is not None and not len(scene.arc_shader_items):
            # An object with no Arc material controls (the default Cube, a lamp) is not an editable asset.
            root = None
    if state == "syncing":
        return
    if state == "empty":
        return
    if root is None:
        return
    name = state_name or root.name
    if info is not None and info.get("source") == "last_import":
        pass
    else:
        pass
    # The tabs only show once there is something to edit.
    tabs = layout.row(align=True)
    tabs.prop(scene, "arc_shader_select_mode", expand=True)
    text, icon = _sc_status(context, sco, root, mode)
    status = layout.row()
    box = layout.box()
    row = box.row(align=True)
    row.operator("arc.pick_shader_zone", text="Pick in Viewport", icon="EYEDROPPER")
    from .Shared import shader_pick as _sp
    if _sp.highlight_state() is not None:
        row.operator("arc.shader_highlight_clear", text="Clear Highlight", icon="X")
    if mode == "WHOLE":
        _draw_shader_whole(box, context, sco, root)
    elif mode == "PART":
        _draw_shader_part(box, context, sco, root)
    else:
        _draw_shader_zone_edit(box, context, sco, root)
    if hasattr(scene, "arc_colourways"):
        cbox = _collapsible(layout, "arc_shader_colourways", "Colorway Presets", default_closed=True)
        if cbox is not None:
            row = cbox.row(align=True)
            row.prop(scene, "arc_colourway_name", text="")
            row.operator("arc.shader_colourway_save", text="Save Current", icon="ADD")
            saved = len(scene.arc_colourways)
            if saved:
                cbox.template_list("ARC_UL_colourways", "", scene, "arc_colourways", scene, "arc_colourway_index", rows=3)
            else:
                pass
            row = cbox.row(align=True)
            row.enabled = bool(saved)
            row.operator("arc.shader_colourway_apply", text="Apply", icon="CHECKMARK")
            row.operator("arc.shader_colourway_delete", text="Delete...", icon="TRASH")
            row = cbox.row(align=True)
            sub = row.row(align=True)
            sub.enabled = bool(saved)
            sub.operator("arc.shader_colourway_export", text="Export...", icon="EXPORT")
            row.operator("arc.shader_colourway_import", text="Import...", icon="IMPORT")
    if hasattr(scene, "arc_cold_snap_outdoors"):
        snow = _collapsible(layout, "arc_shader_cold_snap", "Cold Snap Snow", default_closed=True)
        if snow is not None:
            col = snow.column(align=True)
            col.prop(scene, "arc_cold_snap_outdoors", text="Outdoors", slider=True)
            col.prop(scene, "arc_cold_snap_sheltered", text="Sheltered", slider=True)
    if not _show_developer():
        return
    adv = _collapsible(layout, "arc_shader_advanced", "Material Tools", default_closed=True)
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
        pass
    _sc_look(box, context, sco, root)
    box.operator("arc.shader_reset_outfit", text="Reset Whole Asset...", icon="LOOP_BACK")


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
        pass
    _sc_look(box, context, sco, root)
    box.operator("arc.shader_reset_part", text="Reset Part", icon="LOOP_BACK")


def _draw_shader_zone_edit(box, context, sco, root):
    from .Shared.materials import user_controls as _uc
    scene = context.scene
    zone = getattr(scene, "arc_shader_pick_zone", 0)
    decal = getattr(scene, "arc_shader_pick_decal", 0)
    if not zone and not decal:
        if sco.uc_part(context) is not None:
            _sc_look(box, context, sco, root)
        return
    if zone:
        item = sco.zone_item(scene)
        if item is not None:
            box.prop(item, "color_value", text="Zone Color")
        if sco.zone_offsets_supported(context):
            col = box.column(align=True)
            for key, _b, label, _k, *_rest in _uc.ZO_CHANNELS:
                prop = sco.zo_prop(key)
                if hasattr(scene, prop):
                    col.prop(scene, prop, text=label)
        elif item is None:
            pass
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
    row.operator("arc.shader_control_reset_all", text="Reset All...", icon="LOOP_BACK")
    row.operator("arc.shader_tweaks_save", text="Save Tweaks...", icon="EXPORT")
    row.operator("arc.shader_tweaks_load", text="Load Tweaks...", icon="IMPORT")
    if getattr(scene, "arc_shader_select_mode", "") in ("PART", "ZONE"):
        layout.operator("arc.shader_tweaks_sync_parts", text="Copy Part to All Parts", icon="DUPLICATE")
    outdated = sco.outdated_groups() if hasattr(sco, "outdated_groups") else []
    if outdated:
        row = layout.row(align=True)
        row.operator("arc.shader_upgrade", text="Upgrade", icon="FILE_REFRESH")
    if hasattr(scene, "arc_shader_pick_material"):
        row = layout.row(align=True)
        row.operator("arc.shader_pick", text="Pick Material", icon="EYEDROPPER")
        if scene.arc_shader_pick_material:
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
        row.prop(scene, "arc_shader_edited_only", text="Edited Only", toggle=True)
    groups = sco.visible_groups(context)
    if not groups:
        return
    collapsed = sco.collapsed_groups(scene)
    for group, entries in groups:
        box = layout.box()
        head = box.row()
        closed = group in collapsed
        head.operator("arc.shader_section_toggle", text="", icon="RIGHTARROW" if closed else "DOWNARROW_HLT", emboss=False).group = group
        getattr(head, "heading", head.label)(text=group)
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


_SECTION_ICONS = {"arc_outfit_tools": "USER", "arc_character_parts": "MOD_CLOTH", "arc_weapons": "MOD_PHYSICS",
                  "arc_enemies": "GHOST_ENABLED", "arc_animations": "ARMATURE_DATA", "arc_lighting_look": "LIGHT_SUN",
                  "arc_shader_control": "COLOR", "arc_niagara_fx": "PARTICLES", "arc_scene_items": "OUTLINER",
                  "arc_settings": "PREFERENCES", "arc_asset_modifications": "MODIFIER"}


def _draw_group_label(layout, text, icon="NONE"):
    layout.separator()
    row = layout.row()
    getattr(row, "heading", row.label)(text=text, icon=icon)


def _editing_name(context):
    try:
        from .Shared import ui_targets
    except Exception:
        return ""
    scene = context.scene
    active = getattr(context, "active_object", None)
    selected = getattr(context, "selected_objects", None) or ()
    key = ("editing", getattr(active, "name", ""), len(selected), len(getattr(scene, "objects", ()) or ()),
           (globals().get("_import_gen") or (lambda: 0))())

    def compute():
        target = ui_targets.resolve(context, "asset")
        if target is None:
            return ""
        text_fn = getattr(ui_targets, "editing_text", None)
        if text_fn is not None:
            return text_fn(target, "Editing")
        return "Editing: %s" % getattr(target, "name", "")
    return _timed(key, compute, 0.5) or ""


_FIRST_IMAGE_STEPS = (
    ("import", "1. Import an outfit"),
    ("pose", "2. Choose a pose"),
    ("lighting", "3. Apply lighting"),
    ("camera", "4. Frame the camera"),
    ("render", "5. Render the image"),
)






def _draw_editing_line(layout, context):
    text = _editing_name(context)
    row = layout.row()
    if text:
        # A fallback target (the last import, not the selection) gets the info icon.
        icon = "INFO" if "last imported" in text.lower() else "RESTRICT_SELECT_OFF"
    else:
        row.enabled = False


def _scene_count(context):
    lists = _ui_lists()
    sync = getattr(lists, "request_scene_sync", None)
    if sync is None:
        return 0
    scene = context.scene
    key = ("scene_rows", getattr(scene, "name", ""), len(getattr(scene, "objects", ()) or ()))
    return _timed(key, lambda: sync(scene), 1.0) or 0


def _scene_items_title(context):
    try:
        count = _scene_count(context)
    except Exception:
        count = 0
    return "In This Scene (%d)" % count if count else "In This Scene"


def _draw_scene_items_content(layout, context):
    wm = context.window_manager
    if not (hasattr(wm, "arc_scene_rows") and len(wm.arc_scene_rows)):
        row = layout.row()
        row.enabled = False
        return
    layout.template_list("ARC_UL_scene_rows", "", wm, "arc_scene_rows", wm, "arc_scene_rows_index", rows=5)
    col = layout.column(align=True)
    col.operator("arc_outfits.remove_last_import", text="Delete Last Import...", icon="TRASH")
    col.operator("arc_outfits.sort_into_collections", text="Sort into Collections", icon="OUTLINER_COLLECTION")
    tools = _collapsible(layout, "arc_scene_tools", "Scene Tools", default_closed=True)
    if tools is not None:
        rigs = sum(1 for o in (getattr(context, "selected_objects", None) or ())[:256] if getattr(o, "type", "") == "ARMATURE")
        if rigs < 2:
            hint = tools.row()
            hint.active = False
        tools.operator("arc_outfits.merge_selected_armatures", text="Merge Selected Armatures", icon="ARMATURE_DATA")


def _draw_box_section(layout, context, panel_id, title, draw_content, *, default_closed=True):
    try:
        body = _collapsible(layout, panel_id, title, default_closed=default_closed, icon=_SECTION_ICONS.get(panel_id, "NONE"))
        if body is not None:
            draw_content(body, context)
    except Exception as exc:
        print(f"Arc Raiders: {title} draw failed: {exc}")


def _draw_top_level_section(layout, context, panel_id, title, draw_content, *, default_closed=True, boxed=False):
    try:
        body = _collapsible(layout, panel_id, title, default_closed=default_closed, icon=_SECTION_ICONS.get(panel_id, "NONE"))
        if body is not None:
            margins = body.split(factor=0.04, align=True)
            margins.column()
            center = margins.split(factor=0.92 / 0.96, align=True)
            contents = center.column()
            center.column()
            if boxed:
                contents = contents.box()
            draw_content(contents, context)
            body.separator(type="LINE")
    except Exception as exc:
        print(f"Arc Raiders: {title} draw failed: {exc}")


def _draw_asset_modifications(layout, context):
    _draw_box_section(layout, context, "arc_animations", "Animations", _draw_animations_content)
    _draw_box_section(layout, context, "arc_shader_control", "Colors and Wear", _draw_shader_control_content, default_closed=True)
    from .Shared.materials.fur_controls import fur_objects, draw_controls
    if next(fur_objects(context), None) is not None:
        _draw_box_section(layout, context, "arc_fur_controls", "Fur", draw_controls, default_closed=True)


class ARC_OUTFITS_PT_MainPanel(bpy.types.Panel):
    bl_label = _panel_name()
    bl_idname = "ARC_OUTFITS_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Arc Asset Importer"

    def draw(self, context):
        layout = _SidebarLayout(self.layout)
        from .Outfit_Accessories import properties

        if not properties.ensure_scene_properties():
            return
        if _job_api() is None:
            # Older ui_targets without the job card: keep the staged-import progress row.
            from .Maps import staged_import
            staged_import.draw_progress(layout, context)
        # Header: source status and first-run steps, one job card (staged imports included), last error.
        _draw_top_level_section(layout, context, "arc_settings_paths", "Source", _draw_source_settings_content)
        shown = _draw_queue_report(layout, context)
        _draw_last_error(layout, shown)

        # Weapons/Animations stay closed so catalog walks and enums are not rebuilt on every panel open.
        _draw_top_level_section(layout, context, "arc_outfit_tools", "Outfits", _draw_outfit_tools_content, default_closed=False, boxed=True)
        _draw_top_level_section(layout, context, "arc_character_parts", "Character Parts", _draw_appearance_section)
        _draw_top_level_section(layout, context, "arc_weapons", "Weapons", _draw_weapons_content, boxed=True)
        _draw_top_level_section(layout, context, "arc_enemies", "ARC Enemies", _draw_arc_content)
        _draw_top_level_section(layout, context, "arc_niagara_fx", "Niagara Effects", _draw_niagara_fx_content, boxed=True)
        _draw_top_level_section(layout, context, "arc_asset_modifications", "Asset Modifications", _draw_asset_modifications)
        _draw_top_level_section(layout, context, "arc_lighting_look", "Scene & Lighting", _draw_scene_lighting_content, boxed=True)

        layout.separator()
        _draw_top_level_section(layout, context, "arc_settings", "Settings", _draw_settings_content)


class ARC_OUTFITS_MT_import_files(bpy.types.Menu):
    bl_label = "Import Model Files"
    bl_description = "Import one model file or a whole folder of models that is not in the lists below"

    def draw(self, context):
        layout = self.layout
        if getattr(context.scene, "arc_import_source", "PAK") == "PAK":
            layout.operator("arc_outfits.import_from_pak", text="Import Single Model...", icon="FILE").select_folder = False
            layout.operator("arc_outfits.import_from_pak", text="Import Whole Folder...", icon="FILE_FOLDER").select_folder = True
        else:
            layout.operator("arc_outfits.import_single_psk", text="Import Single Model...", icon="FILE")
            layout.operator("arc_outfits.import_outfit_folder", text="Import Whole Folder...", icon="FILE_FOLDER")





def draw_niagara_fx_box(layout, context) -> None:
    scene = context.scene
    catalog_root = getattr(scene, "arc_ns_catalog_root", "") or ""
    selected = getattr(scene, "arc_ns_selected", "") or ""
    query = getattr(scene, "arc_ns_search", "") or ""
    search = layout.row(align=True)
    search.prop(scene, "arc_ns_search", text="", icon="VIEWZOOM", placeholder="Search effects")
    if hasattr(scene, "arc_ns_lut_only"):
        search.prop(scene, "arc_ns_lut_only", text="", icon="IPO_BEZIER", toggle=True)
    search.operator("arc_outfits.icon_browser", text="", icon="IMGDISPLAY").kind = "effect"
    lut_only = bool(getattr(scene, "arc_ns_lut_only", False))
    lists = _ui_lists()

    def build():
        # Runs in the list timer: the catalog scan stays out of draw.
        found = filter_entries(query, catalog_root=catalog_root, lut_only=lut_only, limit=0)
        return lists.named_rows(found, display_label,
                                lambda label: _pretty_stem(label.split("·")[0].strip(), "NS_"))

    lists.request_sync("fx", (query, catalog_root, lut_only), build, selected)
    shown = len(lists.rows_of(context, "fx"))
    try:
        total = _timed(("ns_total", catalog_root, lut_only),
                       lambda: _lazy("Niagara.niagara").count_entries("", catalog_root=catalog_root, lut_only=lut_only), 30.0)
    except Exception:
        total = 0
    wm = context.window_manager
    header = getattr(lists, "draw_list_header", None)
    if header is not None:
        visible = header(layout, context, "fx", lists.rows_of(context, "fx"), total, "effects", "arc_ns_search", query)
        if visible is None:
            visible = shown
    else:
        visible = shown
        if shown:
            pass
        else:
            pass
        if getattr(lists, "draw_filter_row", None):
            lists.draw_filter_row(layout, context, "fx")
    if visible:
        layout.template_list("ARC_UL_rows", "arc_fx", wm, "arc_fx_rows", wm, "arc_fx_rows_index", rows=3)
    action = layout.row(align=True)
    action.scale_y = 1.4
    action.operator("arc_outfits.import_niagara_fx", text="Import Effect", icon="PARTICLES")
    if hasattr(scene, "arc_ns_play_ticks"):
        action.operator("arc_outfits.play_niagara_fx", text="Bake Scene Preview", icon="PLAY")
    advanced = _collapsible(layout, "arc_ns_advanced", "Effect Options", default_closed=True)
    if advanced is not None:
        draw_playback_box(advanced, context)
        _draw_effect_file_import(advanced, scene)


def _draw_effect_file_import(layout, scene):
    """Effects > Effect Options: import one effect from an own FModel effect export."""
    if not hasattr(scene, "arc_ns_json_path"):
        return
    own = layout.column()
    own.use_property_split = True
    own.use_property_decorate = False
    own.prop(scene, "arc_ns_json_path", text="Effect File")
    file_row = layout.row()
    file_row.enabled = bool((getattr(scene, "arc_ns_json_path", "") or "").strip())
    file_row.operator("arc_outfits.import_niagara_fx", text="Import from Effect File", icon="FILEBROWSER").use_selected = False


def _draw_custom_effect_files(layout, scene):
    """Settings > File Paths: own effect folders instead of the bundled list."""
    if not hasattr(scene, "arc_ns_catalog_root"):
        return
    own = layout.column()
    own.use_property_split = True
    own.use_property_decorate = False
    own.prop(scene, "arc_ns_catalog_root", text="Effect List Folder")
    own.prop(scene, "arc_ns_asset_root", text="Effect Asset Folder")


def draw_playback_box(layout, context, enabled=True):
    scene = context.scene
    play = hasattr(scene, 'arc_ns_play_ticks')
    bake = hasattr(scene, 'arc_ns_bake_curves')
    if not (play or bake):
        return
    col = layout.column(align=True)
    col.use_property_split = True
    col.use_property_decorate = False
    if play:
        col.prop(scene, 'arc_ns_play_ticks', text="Preview Frames")
        col.prop(scene, 'arc_ns_play_at_cursor', text="Start at the 3D Cursor")
        col.prop(scene, 'arc_ns_play_collision', text="Collide with the Scene")
    if bake:
        col.prop(scene, 'arc_ns_bake_curves', text="Bake Curves on Import")


def draw_map_placement_panel(layout, context):
    """Map Placement + gameplay import UI (map-importer line); the section header is the title."""
    scene = context.scene
    box = layout.column()
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
    if world is None or not world.get(_lazy("Lighting.lighting_sky")._WORLD_TAG) or not world.use_nodes:
        return
    nodes = world.node_tree.nodes
    rotation = nodes.get("ArcSkyRotation")
    if rotation is not None:
        layout.prop(rotation.inputs["Angle"], "default_value", text="Rotation")
    for name in ("Extra_Contrast", "Overall Intensity", "Saturation"):
        control = nodes.get(name)
        if control is not None:
            layout.prop(control.outputs[0], "default_value", text=name.replace("_", " "))

from .Outfit_Accessories import icon_browser as _icon_browser

classes = (
    ARC_OUTFITS_MT_import_files,
    ARC_OUTFITS_PT_MainPanel,
    *_icon_browser.classes,
)

def _resolved_str(path):
    return _path_memo(("resolve", str(path)), lambda: str(Path(path).resolve()))


def draw_arc_preview(layout,context,path,max_px=None):
    weapon_ui=_lazy("Weapons.weapon_ui");arc_preview_id=weapon_ui.arc_preview_id
    previews=weapon_ui._PREVIEWS
    icon=_icon_memo(("arc",str(path)),previews,lambda:arc_preview_id(path))
    if not icon:return
    prefs=context.preferences.system
    region=context.region
    width=max(64,(region.width if region else 320)-48)
    if max_px:width=min(width,max_px*max(prefs.ui_scale,.5))
    scale=max(1,width/(20*max(prefs.ui_scale,.5)))
    row=layout.row();row.alignment='CENTER';row.template_icon(icon_value=icon,scale=scale)

def draw_preview(layout,context,path,width_fraction=1.0):
    w=_lazy("Weapons.weapon_ui")
    # preview_id stats and resolves the file; memo per path while the preview collection is unchanged.
    icon=_icon_memo(("wpn",str(path)),w._PREVIEWS,lambda:w.preview_id(path)) if path is not None else 0
    if icon:
        prefs=context.preferences.system
        # template_icon respects the source aspect within its image area.
        # Scale with the actual sidebar content width, including UI zoom/DPI.
        region=context.region
        width=max(64,(region.width if region else 320)-48)
        scale=max(1,width*width_fraction/(20*max(prefs.ui_scale,.5)))
        row=layout.row();row.alignment='CENTER';row.template_icon(icon_value=icon,scale=scale)

def draw_gun_preview(layout,context,path):
    w=_lazy("Weapons.weapon_ui")
    _GUN_TEXTURES,_GUN_FAILED,_GUN_PENDING,_GUN_DISPLAY,_prepare_gun_textures=w._GUN_TEXTURES,w._GUN_FAILED,w._GUN_PENDING,w._GUN_DISPLAY,w._prepare_gun_textures
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
    # Compact: about a third of the width, never taller than 120 px, so the choices stay in view.
    height=min(width*.32,120.0)
    row=layout.row()
    row.use_property_split=False;row.use_property_decorate=False
    # The preview is display-only. Disable Blender's built-in resize grip.
    row.enabled=False
    if texture is None:
        row.scale_y=height/(20*max(context.preferences.system.ui_scale,.5))
        row.label(text='')
        return
    row.scale_y=height/(20*7.6*max(context.preferences.system.ui_scale,.5))
    row.template_preview(texture,show_buttons=False,preview_id='arc_weapon_gun_fixed_5x2')

def _weapon_section(layout,id,label):
    header,body=layout.panel(id,default_closed=True);getattr(header,"heading",header.label)(text=label);return body

def _draw_catalog_selector(layout, context, kind, items, selected, icon_id=0):
    lists = _ui_lists()
    search_prop = "arc_" + kind + "_search"
    query = getattr(context.scene, search_prop, "").casefold()
    rows, total = lists.outfit_rows(items, query, _pretty_name)
    filter_keys = lists.filter_keys(kind, lists.filter_mode(context, kind))
    visible = any(row[0] == selected for row in rows) and (filter_keys is None or selected in filter_keys)
    controls, preview = _split_controls_preview(layout, context, icon_id if visible else 0, rows=11, factor=0.6)
    search = controls.row(align=True)
    search.prop(context.scene, search_prop, text="", icon="VIEWZOOM")
    search.operator("arc_outfits.icon_browser", text="", icon="IMGDISPLAY").kind = kind
    lists.request_sync(kind, (query, tuple(rows)), lambda: rows, selected)
    noun = "weapons" if kind == "weapon" else "enemies"
    shown = lists.draw_list_header(controls, context, kind, rows, total, noun, search_prop, query)
    if shown:
        controls.template_list("ARC_UL_rows", "arc_" + kind, context.window_manager,
                               "arc_" + kind + "_rows", context.window_manager,
                               "arc_" + kind + "_rows_index", rows=3)
    _draw_preview_icon(preview, icon_id if visible else 0)
    return not visible


def _draw_weapons_content(layout,context):
    scene=context.scene
    weapon_ui,catalog,assets=_lazy("Weapons.weapon_ui"),_lazy("Weapons.weapon_catalog"),_lazy("Weapons.weapon_assets")
    if not weapon_ui.ensure_registered():
        # Keep the original selector usable while deferred registration runs.
        layout.prop(scene,'arc_weapon_key',text='Weapon')
        return
    try:
        lists=_ui_lists()
    except NameError:
        lists=None
    wkey=getattr(scene,'arc_weapon_key','NONE')
    entry=catalog.weapon_by_key(wkey)
    variant=getattr(scene,'arc_weapon_ascended','OFF') or 'OFF'
    levels=assets.bundled().get('weapons',{}).get(entry['stem'],{}).get('quality_levels',[0,1,2,3]) if entry else [0,1,2,3]
    tier=str(levels[0]) if variant=='OFF' and len(levels)==1 else getattr(scene,'arc_weapon_tier','3')
    if getattr(lists,'draw_list_header',None):
        path=assets.bundled_preview(entry,variant,tier=tier,stencil=getattr(scene,'arc_weapon_skin','NONE')) if entry else None
        icon_id=_icon_memo(('wpn',str(path)),weapon_ui._PREVIEWS,lambda:weapon_ui.preview_id(path)) if path else 0
        empty=_draw_catalog_selector(layout,context,'weapon',catalog.weapon_enum_items(),wkey,icon_id)
    else:
        empty=False
        fields=layout.column();fields.use_property_split=True;fields.use_property_decorate=False
        fields.prop(scene,'arc_weapon_browser_key',text='Weapon')
    # An empty Favorites/Recent filter dims the choices under it; they still act on the chosen weapon.
    edit_layout=layout
    layout=layout.column();layout.enabled=not empty
    # Choices first, in the order they are read; the import button closes the group.
    fields=layout.column();fields.use_property_split=True;fields.use_property_decorate=False
    fields.prop(scene,'arc_weapon_ascended',text='Variant')
    tiers=fields.row(align=True);tiers.enabled=len(levels)>1;tiers.prop(scene,'arc_weapon_tier',text='Wear Tier',expand=True)
    fields.prop(scene,'arc_weapon_position',text='Position')
    main=layout.row();main.scale_y=1.4
    main.operator('arc_outfits.import_weapon',text='Import Weapon',icon='IMPORT')
    layout=edit_layout
    layout.separator()
    # The buttons below act on a weapon that is already in the scene.
    try:name,line=_target_line(context,'weapon')
    except Exception:name,line='',''
    row=layout.row(align=True);row.enabled=bool(name)
    if hasattr(weapon_ui,'ARC_OT_apply_weapon_wear_position'):
        row.operator('arc_outfits.apply_weapon_wear_position',text='Apply Wear and Position',icon='CHECKMARK')
    else:
        row.operator('arc_outfits.set_weapon_tier',text='Apply Wear Tier',icon='MOD_NOISE')
        row.operator('arc_outfits.update_weapon_position',text='Apply Position',icon='CON_LOCLIKE')
    mods=_weapon_section(layout,'arc_weapon_mods_v2','Attachments')
    if mods is not None:
        mods.use_property_split=True;mods.use_property_decorate=False
        for kind,label,prop in [('Muzzle','Muzzle','arc_weapon_mod_muzzle'),('Stock','Stock','arc_weapon_mod_stock'),('Magazine','Magazine','arc_weapon_mod_magazine'),('UnderBarrel','Underbarrel','arc_weapon_mod_underbarrel')]:
            row=mods.row(align=True);row.prop(scene,prop,text=label)
            add=row.row(align=True);add.enabled=getattr(scene,prop,'NONE') not in ('','NONE')
            op=add.operator('arc_outfits.import_weapon_mod',text='Add Attachment',icon='IMPORT');op.mod_type=kind
            mod=catalog.mod_by_key(getattr(scene,prop,'NONE'))
            if mod:draw_preview(mods,context,_path_memo(("wmod",getattr(scene,prop,'NONE')),lambda:assets.bundled_preview(mod,mod=True)),width_fraction=.5)
    skin=_weapon_section(layout,'arc_weapon_skin_v2','Skin')
    if skin is not None:
        skin.use_property_split=True;skin.use_property_decorate=False
        skin.prop(scene,'arc_weapon_skin',text='Skin')
        row=skin.row(align=True);row.operator('arc_outfits.apply_weapon_skin',text='Apply Skin',icon='MATERIAL');row.operator('arc_outfits.clear_weapon_skin',text='Remove Skin',icon='X')



def _draw_arc_content(layout, context):
    scene = context.scene
    if not hasattr(scene, "arc_enemy_key"):
        return

    try:
        lists = _ui_lists()
    except NameError:
        lists = None
    ekey = getattr(scene, "arc_enemy_key", "")
    if getattr(lists, "draw_list_header", None):
        selected = arc_catalog_data.by_key(ekey)
        path = _path_memo(("pioneer", selected[2]), lambda: _pioneer_image_path(selected[2])) if selected else None
        weapon_ui = _lazy("Weapons.weapon_ui")
        icon_id = _icon_memo(("arc", str(path)), weapon_ui._PREVIEWS, lambda: weapon_ui.arc_preview_id(path)) if path else 0
        empty = _draw_catalog_selector(layout, context, "enemy", arc_catalog_data.enum_items(), ekey, icon_id)
    else:
        empty = False
        fields = layout.column()
        fields.use_property_split = True
        fields.use_property_decorate = False
        fields.prop(scene, "arc_enemy_key", text="Enemy")
    # An empty Favorites/Recent filter dims the choices under it; they still act on the chosen enemy.
    edit_layout = layout
    layout = layout.column()
    layout.enabled = not empty
    selected = arc_catalog_data.by_key(scene.arc_enemy_key)
    fields = layout.column()
    fields.use_property_split = True
    fields.use_property_decorate = False
    fields.prop(scene, "arc_scan_state", text="Alert Level")
    main = layout.row()
    main.scale_y = 1.4
    main.operator("arc_outfits.import_codex_enemy", text="Import Enemy", icon="IMPORT")
    layout = edit_layout
    layout.separator()
    # Fail closed: one resolve drives both the line and the enabled state.
    try:
        enemy_name, enemy_line = _target_line(context, "enemy")
    except Exception:
        enemy_name, enemy_line = "", ""
    apply_row = layout.row()
    apply_row.enabled = bool(enemy_name)
    apply_row.operator("arc_outfits.update_scan_state", text="Apply Alert Level", icon="FILE_REFRESH")
    active = getattr(context, "active_object", None)
    if active is not None and "arc_alertness_state" in active:
        # Object property: keyframe it to animate idle/alert/combat transitions.
        layout.prop(active, '["arc_alertness_state"]', text="Alert Blend (Animatable)", slider=True)


def _section(layout, panel_id: str, title: str, toggle: str | None, scene, default_closed=True):
    panel_fn = getattr(layout, "panel", None)
    if callable(panel_fn):
        header, body = panel_fn(panel_id, default_closed=default_closed)
        if toggle and hasattr(scene, toggle):
            header.prop(scene, toggle, text=title)
        else:
            getattr(header, "heading", header.label)(text=title)
        if body is not None:
            body.use_property_split = True
            body.use_property_decorate = False
        return body
    box = layout.box()
    if toggle and hasattr(scene, toggle):
        box.prop(scene, toggle, text=title)
    else:
        getattr(box, "heading", box.label)(text=title)
    return box


def _draw_scene_lighting_content(layout, context):
    _draw_box_section(layout, context, "arc_scene_items", "In this Scene", _draw_scene_items_content)
    _draw_map_placement_section(layout, context)
    _draw_lighting_look_content(layout, context)


def _draw_lighting_look_content(layout, context) -> None:
    scene = context.scene
    col = layout.column()
    if not hasattr(scene, "arc_lighting_map") or not hasattr(scene, "arc_lighting_exposure_mode"):
        return
    if not _lazy("Lighting.lighting_catalog").map_ids():
        return

    none = _lazy("Lighting.lighting_catalog").NONE
    has_map = getattr(scene, "arc_lighting_map", none) not in ("", none)

    col.use_property_split = True
    col.use_property_decorate = False
    col.prop(scene, "arc_lighting_map", text="Map", icon="WORLD")
    scenario = col.row()
    scenario.enabled = has_map
    scenario.prop(scene, "arc_lighting_scenario", text="Scenario", icon="LIGHT_SUN")
    if hasattr(scene, "arc_lighting_import_camera"):
        col.prop(scene, "arc_lighting_import_camera", text="Add and Activate Scenario Camera")
    # Pending, applied and warnings all come from the same stored result.
    light_state = {}
    try:
        state_fn = getattr(_lazy("Lighting.lighting_looks"), "lighting_state", None)
        if state_fn is not None:
            light_state = state_fn(scene) or {}
    except Exception:
        light_state = {}
    if light_state.get("pending"):
        warn = col.row()
        warn.alert = True
    row = col.row(align=True)
    row.scale_y = 1.4
    row.operator("arc_outfits.apply_lighting_look", text="Apply Lighting", icon="WORLD")
    applied = scene.get("arc_lighting_applied_label") if hasattr(scene, "get") else None
    applied = applied or light_state.get("applied_label") or ""
    is_applied = bool(light_state.get("applied")) if light_state else ("arc_lighting_state" in scene)
    if applied and is_applied:
        warnings = [w for w in (light_state.get("warnings") or []) if w]
        row = col.row(align=True)
        if warnings:
            row.alert = True
        else:
            pass
        row.operator("arc_outfits.remove_lighting_look", text="Remove Lighting", icon="X")
        for line in warnings[:4]:
            pass
        if len(warnings) > 4:
            pass
        if warnings:
            col.operator("arc_outfits.view_details", text="View Details...", icon="TEXT").source = "lighting"
    col = _collapsible(layout, "arc_lighting_advanced", "Lighting Options", default_closed=True)
    if col is None:
        return

    # One pattern per option: a closed section whose header checkbox turns it on; its settings show inside.
    body = _section(col, "arc_lighting_color", "Game Color Grade", "arc_lighting_apply_lut", scene, default_closed=True)
    if body is not None and scene.arc_lighting_apply_lut:
        body.prop(scene, "arc_lighting_exposure_mode", text="Exposure")
        row = body.column(align=True)
        if scene.arc_lighting_exposure_mode == "MANUAL":
            row.prop(scene, "arc_lighting_meter_ev", text="Brightness (EV)")
        row.prop(scene, "arc_lighting_exposure_comp", text="Compensation")
        # Auto columns: three toggles in one row at wide widths, wrapping instead of clipping at 256 px.
        grid = getattr(body, "grid_flow", None)
        row = grid(row_major=True, columns=0, even_columns=True, align=True) if callable(grid) else body.row(align=True)
        row.use_property_split = False
        row.prop(scene, "arc_lighting_apply_bloom", text="Bloom", toggle=True)
        row.prop(scene, "arc_lighting_apply_vignette", text="Vignette", toggle=True)
        row.prop(scene, "arc_lighting_apply_local_exposure", text="Local Contrast", toggle=True)

    body = _section(col, "arc_lighting_sky", "Sky & Sun", "arc_lighting_apply_hdri", scene)
    if body is not None and scene.arc_lighting_apply_hdri:
        row = body.row(align=True)
        row.use_property_split = False
        row.prop(scene, "arc_lighting_apply_sun", text="Sun Lamp", toggle=True)
        row.prop(scene, "arc_lighting_apply_clouds", text="Clouds", toggle=True)
        row = body.column(align=True)
        row.prop(scene, "arc_lighting_hdri_strength", text="Strength")
        row.prop(scene, "arc_lighting_cloud_time", text="Cloud Time")
        draw_sky_controls(body, scene)

    body = _section(col, "arc_lighting_fog", "Fog Volume", "arc_lighting_apply_fog", scene)
    if body is not None and scene.arc_lighting_apply_fog:
        row = body.column(align=True)
        row.prop(scene, "arc_lighting_fog_size_x", text="Size X")
        row.prop(scene, "arc_lighting_fog_size_y", text="Y")
        row.prop(scene, "arc_lighting_fog_size_z", text="Z")

    body = _section(col, "arc_lighting_override_section", "Override Color Grade and Clouds", "arc_lighting_override", scene)
    if body is not None and scene.arc_lighting_override:
        body.prop(scene, "arc_lighting_look", text="Color Grade")
        body.prop(scene, "arc_lighting_lut_intensity", text="Grade Strength", slider=True)
        body.prop(scene, "arc_lighting_hdri", text="Clouds")
