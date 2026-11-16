"""
Property definitions for the Arc Raiders Importer
"""

import bpy
import os
import time
import zlib
from types import SimpleNamespace
from bpy.app.handlers import persistent
from bpy.props import StringProperty, CollectionProperty, BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty
from bpy.types import PropertyGroup

from ..Shared import utils
from ..Shared.textures import scan_skins, is_body, is_hair, scan_hair_mis, detect_model_type, get_skins_folder, get_all_skin_dirs, is_variant, get_default_skin_dir
from ..Shared.importing import get_outfit_folder, scan_outfit_presets, get_character_name, load_outfit_csv, get_outfit_csv_path

_csv_model_to_ui_folder = {}


def _default_bundled_usmap_path():
    from ..Shared import pak_extract
    return pak_extract.bundled_usmap()

def _default_steam_install_dir():
    return r"C:\Program Files (x86)\Steam\steamapps\common\Arc Raiders"

def rebuild_csv_outfit_map(csv_path=None):
    """Rebuild the Model Folder Name -> Item/UI Folder Name mapping."""
    if csv_path is None:
        try:
            csv_path = resolve_outfit_csv_path(bpy.context)
        except Exception:
            return
    rows = load_outfit_csv(csv_path)
    _csv_model_to_ui_folder.clear()
    for row in rows:
        models = [model.strip() for model in (row.get("Model Folder Name") or "").split(";") if model.strip()]
        ui = (row.get("Item/UI Folder Name") or "").strip().split(";")[0].strip()
        for model in models:
            if ui:
                _csv_model_to_ui_folder[utils.normalize_folder_name(model)] = ui

# ---------------------------------------------------------------------------
# Body variant data (not hardcoded in drawing)
# ---------------------------------------------------------------------------

BODY_VARIANTS = [
    ('NONE',          'Outfit Default',  'Keep the body skin the outfit comes with'),
    ('female_white',  'White Female',       'T_Body_Female_C / T_Body_Female_N'),
    ('male_white',    'White Male',         'T_Body_Male_C / T_Body_Male_N'),
    ('female_dark',   'Black Female',       'T_Female_Color_Dark / T_Body_Female_N'),
    ('male_dark',     'Black Male',         'T_Male_Color_Dark / T_Body_Male_N'),
]

BODY_ALBEDO = {
    'female_white': 'T_Body_Female_C.png',
    'male_white':   'T_Body_Male_C.png',
    'female_dark':  'T_Female_Color_Dark.png',
    'male_dark':    'T_Male_Color_Dark.png',
}
BODY_NORMAL = {
    'female_white': 'T_Body_Female_N.png',
    'male_white':   'T_Body_Male_N.png',
    'female_dark':  'T_Body_Female_N.png',
    'male_dark':    'T_Body_Male_N.png',
}

def _update_inherit_shape_key(self, context):
    from . import character_fit
    scene = getattr(context, "scene", None) or self
    try:
        if getattr(scene, "arc_inherit_shape_key", True):
            character_fit.sync_scene(scene)
        else:
            character_fit.release_scene(scene)
    except Exception as exc:
        utils.get_logger().warning("Shape key inheritance update failed: %s", exc)


def make_body_items(self, context):
    return BODY_VARIANTS

# ---------------------------------------------------------------------------
# Confirm-dialog scan cache (populate on invoke; draw/enums read only)
# ---------------------------------------------------------------------------

# Module-level so EnumProperty item callbacks can share the operator's invoke cache.
_CONFIRM_DIALOG_CACHE = None


def clear_confirm_dialog_cache():
    """Drop dialog-lifetime scan results (cancel / execute / fresh invoke)."""
    global _CONFIRM_DIALOG_CACHE
    _CONFIRM_DIALOG_CACHE = None


def publish_confirm_dialog_cache(cache):
    """Expose an operator-built cache to EnumProperty item callbacks."""
    global _CONFIRM_DIALOG_CACHE
    _CONFIRM_DIALOG_CACHE = cache


def confirm_dialog_fingerprint(context) -> tuple:
    """Cheap key of paths that invalidate dialog scans when changed."""
    parts = []
    for entry in context.scene.arc_psk_entries:
        psk = bpy.path.abspath(entry.psk_path) if entry.psk_path else ""
        manual = bpy.path.abspath(entry.manual_skins_folder) if entry.manual_skins_folder else ""
        parts.append((psk, manual))
    manual_outfit = getattr(context.scene, "arc_manual_outfit_folder", "") or ""
    if manual_outfit:
        manual_outfit = bpy.path.abspath(manual_outfit)
    return (tuple(parts), manual_outfit)


def _skin_enum_items(skins, default_dir: str) -> list:
    default_label_name = default_dir
    if not default_label_name:
        for skin_name, json_path in skins:
            if skin_name == "__DEFAULT__":
                default_label_name = os.path.splitext(os.path.basename(json_path))[0]
                break
    default_label = f"{default_label_name} (Default)" if default_label_name else "— Default —"
    items = [(
        'NONE',
        default_label,
        f'Import using the default skin ({default_label_name})' if default_label_name else 'Import without a skin',
    )]
    for skin_name, json_path in skins:
        if skin_name == default_dir or skin_name == "__DEFAULT__":
            continue
        items.append((json_path, skin_name, json_path))
    return items


def _build_outfit_preset_items(context, *, is_outfit_queue: bool) -> list:
    empty = [('NONE', '— Select Outfit —', '')]
    if not is_outfit_queue:
        return empty
    manual_folder = getattr(context.scene, "arc_manual_outfit_folder", "")
    character_name = ""
    for entry in context.scene.arc_psk_entries:
        character_name = get_character_name(bpy.path.abspath(entry.psk_path))
        if character_name:
            break
    if not character_name and not manual_folder:
        return empty
    presets = scan_outfit_presets(character_name, manual_folder)
    items = [('NONE', '— Select Outfit —', 'No outfit preset applied')]
    for preset_name, json_path in presets:
        items.append((json_path, preset_name, json_path))
    return items


def build_confirm_dialog_cache(context, *, is_outfit_queue=None):
    """Run filesystem/JSON scans once for the confirm dialog lifetime."""
    global _CONFIRM_DIALOG_CACHE
    from ..Shared.importing import queue_supports_outfit_batch

    if is_outfit_queue is None:
        is_outfit_queue = queue_supports_outfit_batch(context)

    entries = {}
    for entry in context.scene.arc_psk_entries:
        psk_abs = bpy.path.abspath(entry.psk_path) if entry.psk_path else ""
        manual_abs = bpy.path.abspath(entry.manual_skins_folder) if entry.manual_skins_folder else ""
        skins = scan_skins(entry.psk_path, entry.manual_skins_folder)
        if manual_abs and os.path.isdir(manual_abs):
            skins_folder = manual_abs
        else:
            skins_folder = get_skins_folder(entry.psk_path) or ""
        all_dirs = get_all_skin_dirs(skins_folder) if skins_folder else []
        default_dir = get_default_skin_dir(all_dirs) if all_dirs else ""
        is_body_part = is_body(psk_abs or entry.psk_path)
        is_hair_part = is_hair(psk_abs or entry.psk_path)
        model_type = detect_model_type(psk_abs) if psk_abs else "unknown"
        hair_mis = scan_hair_mis(psk_abs) if is_hair_part else []
        entries[psk_abs] = {
            "manual_folder": manual_abs,
            "skins": skins,
            "default_dir": default_dir,
            "skin_items": _skin_enum_items(skins, default_dir),
            "is_body": is_body_part,
            "is_hair": is_hair_part,
            "model_type": model_type,
            "hair_mis": hair_mis,
        }

    cache = {
        "fingerprint": confirm_dialog_fingerprint(context),
        "entries": entries,
        "is_outfit_queue": bool(is_outfit_queue),
        "outfit_preset_items": _build_outfit_preset_items(
            context, is_outfit_queue=bool(is_outfit_queue)
        ),
    }
    _CONFIRM_DIALOG_CACHE = cache
    return cache


def get_confirm_dialog_cache(context=None):
    """Return active cache, or None if missing / fingerprint mismatch."""
    cache = _CONFIRM_DIALOG_CACHE
    if not cache:
        return None
    if context is not None and cache.get("fingerprint") != confirm_dialog_fingerprint(context):
        return None
    return cache


def cached_entry_scan(context, entry):
    """Per-entry scan dict from dialog cache, or None if unavailable."""
    cache = get_confirm_dialog_cache(context)
    if not cache:
        return None
    psk_abs = bpy.path.abspath(entry.psk_path) if entry.psk_path else ""
    info = cache["entries"].get(psk_abs)
    if not info:
        return None
    manual_abs = bpy.path.abspath(entry.manual_skins_folder) if entry.manual_skins_folder else ""
    if info.get("manual_folder", "") != manual_abs:
        return None
    return info

# ---------------------------------------------------------------------------
# Enum callbacks (must be defined before PropertyGroup classes that reference them)
# ---------------------------------------------------------------------------

def make_skin_items(self, context):
    """Dynamic EnumProperty items callback for skin dropdown."""
    if not context:
        return [('NONE', '— Default —', '')]
    for entry in context.scene.arc_psk_entries:
        if entry == self:
            cached = cached_entry_scan(context, entry)
            if cached is not None:
                return list(cached["skin_items"])
            skins = scan_skins(entry.psk_path, entry.manual_skins_folder)
            skins_folder = entry.manual_skins_folder if entry.manual_skins_folder else get_skins_folder(entry.psk_path)
            all_dirs = get_all_skin_dirs(skins_folder) if skins_folder else []
            default_dir = get_default_skin_dir(all_dirs) if all_dirs else ""
            return _skin_enum_items(skins, default_dir)
    return [('NONE', '— Default —', '')]

def skin_choice_for_json(context, entry, json_path: str):
    """Dropdown ID that shows ``json_path`` for this entry, or None when not listed.

    Colourway presets store an exact MI path in ``batch_skin_json``; the
    dropdown lists scanned paths (often the ``.arc-material.json`` twin), so
    match by bare MI stem after an exact-path check.
    """
    from ..Shared.importing import _da_oi_mi_stem
    if not json_path:
        return None
    cached = cached_entry_scan(context, entry)
    if cached is not None:
        skins, default_dir = cached["skins"], cached["default_dir"]
    else:
        skins = scan_skins(entry.psk_path, entry.manual_skins_folder)
        skins_folder = entry.manual_skins_folder if entry.manual_skins_folder else get_skins_folder(entry.psk_path)
        all_dirs = get_all_skin_dirs(skins_folder) if skins_folder else []
        default_dir = get_default_skin_dir(all_dirs) if all_dirs else ""
    want_path = os.path.normcase(os.path.abspath(json_path))
    want_stem = _da_oi_mi_stem(json_path)

    def item_id(skin_name, path):
        return 'NONE' if skin_name in (default_dir, "__DEFAULT__") else path

    for skin_name, path in skins:
        if os.path.normcase(os.path.abspath(path)) == want_path:
            return item_id(skin_name, path)
    for skin_name, path in skins:
        if _da_oi_mi_stem(path) == want_stem:
            return item_id(skin_name, path)
    return None


def make_outfit_preset_items(self, context):
    """Dynamic EnumProperty items callback for outfit preset dropdown."""
    if not context:
        return [('NONE', '— Select Outfit —', '')]
    cache = get_confirm_dialog_cache(context)
    if cache is not None:
        return list(cache["outfit_preset_items"])
    # Outside the dialog this runs on every redraw of any region that shows the property.
    # The colourway index underneath is cached on a directory stat signature, so a
    # rebuild is cheap; hand back the held list object while the items are unchanged
    # (Blender keeps string pointers into the returned list).
    global _PRESET_ITEMS_IDLE
    from ..Shared.importing import queue_supports_outfit_batch
    items = _build_outfit_preset_items(
        context, is_outfit_queue=queue_supports_outfit_batch(context)
    )
    held = _PRESET_ITEMS_IDLE
    if held is not None and held == items:
        return held
    _PRESET_ITEMS_IDLE = items
    return items

_PRESET_ITEMS_IDLE = None
_OUTFIT_SELECTION_UPDATING = False
_DEFAULT_OUTFIT_KEY = 'ID_PLAYERSKIN_ABYSS'


def _browse_outfit_get(self):
    # The string key owns selection; RNA never stores a second competing value.
    items = make_outfit_selector_items(self, SimpleNamespace(scene=self))
    selected = getattr(self, 'arc_selected_outfit', '')
    return next((item[4] for item in items if item[0] == selected), 0)


def _browse_outfit_set(self, value):
    items = make_outfit_selector_items(self, SimpleNamespace(scene=self))
    selected = next((item[0] for item in items if item[4] == value), None)
    if selected is not None:
        self.arc_selected_outfit = selected


def _on_selected_outfit_changed(self, context):
    if not _OUTFIT_SELECTION_UPDATING:
        ensure_outfit_selection(self, context)
        _request_selected_colorways(self)


# Colorway names per outfit key, scanned once when the outfit is selected (not per redraw).
_COLORWAY_NAMES_BY_OUTFIT = {}
_COLORWAY_PENDING_SCENE = None


def _request_selected_colorways(scene):
    global _COLORWAY_PENDING_SCENE
    _COLORWAY_PENDING_SCENE = scene
    if not bpy.app.timers.is_registered(_prepare_selected_colorways):
        bpy.app.timers.register(_prepare_selected_colorways, first_interval=0.15)


def _prepare_selected_colorways():
    global _COLORWAY_PENDING_SCENE
    scene, _COLORWAY_PENDING_SCENE = _COLORWAY_PENDING_SCENE, None
    if scene is not None:
        try:
            _scan_selected_colorways(scene)
        except ReferenceError:
            pass
    return None


def _scan_selected_colorways(scene, context=None):
    """Fill the Colorway menu for the chosen outfit without waiting for Import Outfit."""
    key = str(getattr(scene, 'arc_selected_outfit', '') or '')
    if not key or key in _COLORWAY_NAMES_BY_OUTFIT:
        return
    names = []
    try:
        from ..Shared import importing, utils
        root = utils.get_pioneer_root()
        if root:
            ctx = context if getattr(context, 'scene', None) is not None else SimpleNamespace(scene=scene)
            rows = importing.load_outfit_csv(importing.get_outfit_csv_path(ctx))
            row = importing.find_outfit_row(rows, key)
            for name, _path in importing.colorways_for_outfit_row(root, row, allow_extract=False) if row else ():
                name = str(name or '')
                if name and name not in names:
                    names.append(name)
    except Exception:
        names = []
    _COLORWAY_NAMES_BY_OUTFIT[key] = names


def ensure_outfit_selection(scene, context=None):
    """Keep list, Browse All, preview and import on one stable outfit identity."""
    global _OUTFIT_SELECTION_UPDATING
    if _OUTFIT_SELECTION_UPDATING:
        return
    context = context or SimpleNamespace(scene=scene)
    items = make_outfit_selector_items(scene, context)
    keys = {item[0] for item in items}
    selected = getattr(scene, 'arc_selected_outfit', '')
    if selected not in keys:
        selected = _DEFAULT_OUTFIT_KEY if _DEFAULT_OUTFIT_KEY in keys else items[0][0]
    _OUTFIT_SELECTION_UPDATING = True
    try:
        # Each scene write tags the scene for a depsgraph update (a Cycles viewport restarts), so write once.
        if getattr(scene, 'arc_selected_outfit', '') != selected:
            scene.arc_selected_outfit = selected
        if scene.get('arc_outfit_enum_schema') != 1:
            scene['arc_outfit_enum_schema'] = 1
    finally:
        _OUTFIT_SELECTION_UPDATING = False


def _initialize_outfit_defaults():
    scenes = getattr(bpy.data, 'scenes', None)
    if scenes is None:
        return 0.1
    for scene in scenes:
        # Preserve legacy facial selections when splitting the old mixed hair enum.
        if not scene.get('arc_hair_selector_schema') and hasattr(scene, 'arc_appearance_facial_hair'):
            from . import appearance
            raw = scene.get('arc_appearance_hair', 0)
            choices = appearance._list_all_hair_choices()
            choice = choices[raw - 1] if isinstance(raw, int) and 0 < raw <= len(choices) else appearance.resolve_choice(raw) if isinstance(raw, str) else None
            if choice and appearance.hair_choice_role(choice) == 'FacialHair':
                scene.arc_appearance_facial_hair = choice['id']
                scene.arc_appearance_hair = 'NONE'
            scene['arc_hair_selector_schema'] = 1
        if hasattr(scene, 'arc_selected_outfit_browse'):
            if not scene.get('arc_outfit_selection_initialized'):
                ensure_outfit_selection(scene)
                scene['arc_outfit_selection_initialized'] = True
    return None


@persistent
def _load_outfit_defaults(_unused):
    _initialize_outfit_defaults()


def register_outfit_defaults():
    # Saved-scene defaults are repaired lazily by the selector/property actions.
    unregister_outfit_defaults()
    if not bpy.app.timers.is_registered(_refresh_outfit_csv_state):
        bpy.app.timers.register(_refresh_outfit_csv_state, first_interval=0.01)


def unregister_outfit_defaults():
    global _COLORWAY_PENDING_SCENE
    _COLORWAY_PENDING_SCENE = None
    while _load_outfit_defaults in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_outfit_defaults)
    if bpy.app.timers.is_registered(_initialize_outfit_defaults):
        bpy.app.timers.unregister(_initialize_outfit_defaults)
    if bpy.app.timers.is_registered(_prepare_selected_colorways):
        bpy.app.timers.unregister(_prepare_selected_colorways)


def _tag_search_redraw(_self, context):
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _on_mask_debug_color_n(self, context):
    """Live Color N zone gate whenever Mask Debug inject is present."""
    global _mask_debug_updating
    if _mask_debug_updating:
        return
    try:
        from ..Script_Helpers import mask_debug
        mask_debug.sync_color_n_from_ui(context)
    except Exception as exc:
        print(f"Arc Raiders Mask Debug Color N live update failed: {exc}")


_MASK_DEBUG_MODE_ITEMS = (
    ("0", "Passthrough", "Original Arc Texturer; optional zone digit overlay"),
    ("1", "ColorN Highlight", "Magenta/black checker on selected Color N"),
    ("2", "CurvatureID Overlay", "Raw OCM / CurvatureID over the base material"),
    ("3", "False Color", "Distinct saturated colour per Color N zone"),
)

_MASK_DEBUG_SOURCE_ITEMS = (
    ("procedural", "Procedural", "Live OCM Blue → ColorRamp at BANDS (tunable stops)"),
    ("baked", "Baked", "Import-time ZoneIndex PNG (Closest sample)"),
    ("mismatch", "Mismatch", "Highlight where procedural and baked disagree"),
)


_OUTFIT_ENUM_CACHE = {"key": None, "items": None}

# Panel draws call this per redraw; re-stat the CSV at most once per interval.
_CSV_STAT_INTERVAL_S = 1.0
_CSV_STAT_MEMO = {}
_CSV_ROWS_MEMO = {}


def resolve_outfit_csv_path(context) -> str:
    """Scene CSV when it exists, else the add-on preference CSV, else the bundled default."""
    from ..Shared import prefs
    scene = getattr(context, "scene", None)
    custom = getattr(scene, "arc_outfit_csv_path", "") if scene is not None else ""
    if custom:
        abspath = bpy.path.abspath(custom)
        if os.path.isfile(abspath):
            prefs.remember("outfit_csv_path", abspath)
            return abspath
    pref = prefs.get("outfit_csv_path", "")
    if pref:
        pref = bpy.path.abspath(pref)
        if os.path.isfile(pref):
            return pref
    return get_outfit_csv_path(context) if scene is not None else ""


def _refresh_outfit_csv_state():
    context = bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    custom = getattr(scene, "arc_outfit_csv_path", "")
    csv_path = resolve_outfit_csv_path(context)
    try:
        mtime = os.path.getmtime(csv_path) if csv_path else 0.0
    except OSError:
        mtime = 0.0
    _CSV_ROWS_MEMO[csv_path] = load_outfit_csv(csv_path)
    _CSV_STAT_MEMO[custom] = (csv_path, mtime, time.monotonic())
    make_outfit_selector_items(scene, context)
    _tag_search_redraw(scene, context)
    return None


def outfit_csv_state(context):
    """Return cached CSV state and schedule filesystem checks outside drawing."""
    scene = getattr(context, 'scene', None)
    custom = getattr(scene, "arc_outfit_csv_path", "") if scene is not None else None
    now = time.monotonic()
    memo = _CSV_STAT_MEMO.get(custom)
    if memo is None or now - memo[2] >= _CSV_STAT_INTERVAL_S:
        if not bpy.app.timers.is_registered(_refresh_outfit_csv_state):
            bpy.app.timers.register(_refresh_outfit_csv_state, first_interval=0.01)
    return (memo[0], memo[1]) if memo is not None else ("", 0.0)


def outfit_csv_path_cached(context) -> str:
    return outfit_csv_state(context)[0]


_PART_MODE_ITEMS = (
    ('NEW', "New Character", "Import the part on its own"),
    ('ADD', "Add to Character", "Add the part to the selected character, or import it on its own when none is selected"),
    ('REPLACE', "Replace Part", "Swap the same part on the selected character"),
)


_PART_MODE_INDEX = {key: i for i, (key, _name, _desc) in enumerate(_PART_MODE_ITEMS)}


def _get_part_mode(self):
    # Until the user picks a mode, a scene with no character opens on New Character.
    stored = self.get("arc_part_mode")
    if stored is not None:
        return int(stored)
    from ..Shared import ui_targets
    return _PART_MODE_INDEX[ui_targets.default_part_mode(self, has_armatures=len(bpy.data.armatures) > 0)]


def _set_part_mode(self, value):
    self["arc_part_mode"] = int(value)


def _part_mode(scene):
    try:
        return str(scene.arc_part_mode or 'ADD')
    except Exception:
        return 'ADD'


def _get_attach_to_outfit(self):
    return _part_mode(self) == 'ADD'


def _set_attach_to_outfit(self, value):
    if value:
        self.arc_part_mode = 'ADD'
    elif _part_mode(self) == 'ADD':
        self.arc_part_mode = 'NEW'


def _get_replace_selected(self):
    return _part_mode(self) == 'REPLACE'


def _set_replace_selected(self, value):
    if value:
        self.arc_part_mode = 'REPLACE'
    elif _part_mode(self) == 'REPLACE':
        self.arc_part_mode = 'ADD'


_COLORWAY_ITEMS_CACHE = {"key": None, "items": None}


def make_outfit_colorway_items(self, context):
    """Default first, then the colorways scanned for the chosen outfit.

    No IO: reads the names scanned when the outfit was selected plus the in-memory
    ``arc_outfit_selections`` collection, which Import Outfit fills.
    """
    names = []
    try:
        selected = str(getattr(self, "arc_selected_outfit", "") or "")
        names.extend(_COLORWAY_NAMES_BY_OUTFIT.get(selected, ()))
    except Exception:
        names = []
    try:
        for sel in self.arc_outfit_selections:
            name = str(sel.preset_name or "")
            if name and name not in names:
                names.append(name)
    except Exception:
        pass
    key = tuple(names)
    cache = _COLORWAY_ITEMS_CACHE
    if cache["key"] != key or cache["items"] is None:
        items = [("DEFAULT", "Default", "Keep the outfit's own colors")]
        for index, name in enumerate(names[:200]):
            items.append((name, name, f"Import with the {name} colorway"))
        cache["key"] = key
        # Blender needs the item strings kept alive while the enum is in use.
        cache["items"] = items
    return cache["items"]


def make_outfit_selector_items(self, context):
    """Dynamic EnumProperty items callback for outfit selector."""
    from ..Shared.importing import load_outfit_csv, outfit_row_key, outfit_display_label

    csv_path, mtime = outfit_csv_state(context)
    cache_key = (csv_path, mtime)
    cached = _OUTFIT_ENUM_CACHE
    if cached["key"] == cache_key and cached["items"]:
        return cached["items"]

    rows = _CSV_ROWS_MEMO.get(csv_path, ())
    from .outfit_catalog import is_outfit_option
    items = []
    unnamed = []
    for row in rows:
        if not is_outfit_option(row):
            continue
        key = outfit_row_key(row)
        label = outfit_display_label(row)
        if row.get("Pak Status") == "no cooked mesh" or not (row.get("Item/UI Folder Name") or row.get("Model Folder Name") or row.get("Pak Mesh Paths")):
            label += " (mesh unavailable)"
        if "unnamed" in label.lower():
            unnamed.append((key, label, label))
        else:
            items.append((key, label, label))
    items.sort(key=lambda t: t[1].lower())
    unnamed.sort(key=lambda t: t[1].lower())
    items.extend(unnamed)
    # Dynamic Blender enums normally store list indices. Stable numeric IDs keep
    # saved selections intact when names/order change; Abyss is the first/default.
    items.sort(key=lambda item: (item[0] != _DEFAULT_OUTFIT_KEY, 'unnamed' in item[1].lower(), item[1].lower()))
    numbered = []
    used = set()
    for key, label, description in items:
        number = 0 if key == _DEFAULT_OUTFIT_KEY else (zlib.crc32(key.encode('utf-8')) & 0x00ffffff) or 1
        while number in used:
            number = ((number + 1) & 0x00ffffff) or 1
        used.add(number)
        numbered.append((key, label, description, 0, number))
    result = numbered or [('NONE', '(no outfits found — check CSV path)', '', 0, 0)]
    cached["key"] = cache_key
    cached["items"] = result
    return result


# Module-level weapon EnumProperty callbacks (must not be nested in register()).
def _weapon_enum_items(self, context):
    from ..Weapons import weapon_catalog as _wcat
    return _wcat.weapon_enum_items(self, context)


def _mod_items_muzzle(self, context):
    from ..Weapons import weapon_catalog as _wcat
    return _wcat.mod_enum_items("Muzzle")


def _mod_items_stock(self, context):
    from ..Weapons import weapon_catalog as _wcat
    return _wcat.mod_enum_items("Stock")


def _mod_items_magazine(self, context):
    from ..Weapons import weapon_catalog as _wcat
    return _wcat.mod_enum_items("Magazine")


def _mod_items_underbarrel(self, context):
    from ..Weapons import weapon_catalog as _wcat
    return _wcat.mod_enum_items("UnderBarrel")


def _mod_items_tech(self, context):
    from ..Weapons import weapon_catalog as _wcat
    return _wcat.mod_enum_items("Tech")


def _pattern_enum_items(self, context):
    from ..Weapons import weapon_catalog as _wcat
    return _wcat.pattern_enum_items(self, context)


def _weapon_skin_enum_items(self, context):
    from ..Weapons import weapon_catalog as _wcat
    return _wcat.skin_enum_items(self, context)


_ANIM_KIND_ITEMS = (
    ("ALL", "All kinds", "AnimSequence, AnimMontage, and AnimComposite"),
    ("AnimSequence", "Sequences", "UAnimSequence only"),
    ("AnimMontage", "Montages", "UAnimMontage only"),
    ("AnimComposite", "Composites", "UAnimComposite only"),
)


# ---------------------------------------------------------------------------
# Property Groups
# ---------------------------------------------------------------------------

def _clear_batch_skin_override(self, _context):
    """Let a manual dropdown change replace the last authored preset binding."""
    try:
        self.batch_skin_json = ""
    except (AttributeError, ReferenceError, RuntimeError):
        pass


class ArcOutfitsPSKEntry(PropertyGroup):
    psk_path: StringProperty(name="PSK Path", subtype='FILE_PATH')
    # Exact DA_OI-resolved MI path for the active batch colourway. This must be
    # a string rather than skin_choice's dynamic EnumProperty: enum IDs can use
    # generated .arc-material.json paths that differ from authored MI paths.
    batch_skin_json: StringProperty(name="Batch Skin JSON", default="", options={'HIDDEN'})
    skin_choice: EnumProperty(
        name="Skin",
        description="",
        items=make_skin_items,
        update=_clear_batch_skin_override,
    )
    body_choice: EnumProperty(
        name="Body Skin",
        description="",
        items=make_body_items,
    )
    hair_mi: StringProperty(
        name="Hair MI Path",
        description="",
        default="",
    )
    hair_mi_options: StringProperty(
        name="Hair MI Options",
        description="",
        default="",
    )
    display_name: StringProperty(name="Display Name")
    manual_skins_folder: StringProperty(
        name="Manual Skins Folder",
        description="",
        default="",
        subtype='DIR_PATH',
    )

class ArcOutfitsSelection(PropertyGroup):
    """One selectable outfit colourway for batch import."""
    preset_name: StringProperty(name="Preset")
    json_path: StringProperty(name="JSON Path")
    # No update callback: a tick only marks the colourway. The preset is resolved on OK
    # (batch import) or by the Apply button; resolving here froze the dialog for seconds.
    selected: BoolProperty(name="Import", default=False)

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    ArcOutfitsPSKEntry,
    ArcOutfitsSelection,
)


def _safe_register_class(cls):
    # Re-registering the same live PropertyGroup invalidates existing Scene
    # CollectionProperty RNA even when its fixed_type identifier stays equal.
    if getattr(cls, "is_registered", False):
        return
    try:
        bpy.utils.register_class(cls)
        return
    except (ValueError, RuntimeError) as exc:
        if "already registered" not in str(exc).lower():
            raise
    existing = getattr(bpy.types, cls.__name__, None)
    for candidate in (cls, existing):
        if candidate is None:
            continue
        try:
            bpy.utils.unregister_class(candidate)
        except (ValueError, RuntimeError):
            pass
    bpy.utils.register_class(cls)


def _safe_unregister_class(cls):
    existing = getattr(bpy.types, getattr(cls, "__name__", ""), None)
    for candidate in (cls, existing):
        if candidate is None:
            continue
        try:
            bpy.utils.unregister_class(candidate)
        except (ValueError, RuntimeError):
            pass


def _safe_del_scene_prop(name: str) -> None:
    try:
        delattr(bpy.types.Scene, name)
    except Exception:
        pass


# Shared by Outfits + MapImporter installs (same RNA names on Scene).
# Includes decal/palette — both lines register them; never treat as map-only
# or a MapImporter reload will wipe props the Outfits panel still draws.
_SHARED_SCENE_PROPS = (
    "arc_studio_light_rig",
    "arc_psk_entries",
    "arc_outfit_selections",
    "arc_pioneer_root",
    "arc_pak_dir",
    "arc_import_source",
    "arc_usmap_path",
    "arc_fmdex_root",
    "arc_ns_json_path",
    "arc_ns_asset_root",
    "arc_ns_catalog_root",
    "arc_ns_search",
    "arc_ns_selected",
    "arc_ns_lut_only",
    "arc_ns_bake_curves",
    "arc_ns_play_ticks",
    "arc_ns_play_cache_dir",
    "arc_ns_play_shim",
    "arc_ns_play_vector_fields",
    "arc_ns_play_at_cursor",
    "arc_ns_play_collision",
    "arc_manual_outfit_folder",
    "arc_outfit_preset",
    "arc_outfit_csv_path",
    "arc_selected_outfit",
    "arc_selected_outfit_browse",
    "arc_outfit_search",
    "arc_placement_listen_port",
    "arc_auto_listen",
    "arc_organize_nodes_on_import",
    "arc_io_prefetch",
    "arc_decal_method",
    "arc_decal_excl_slot",
    "arc_decal_excl_c1",
    "arc_decal_excl_c2",
    "arc_decal_excl_c3",
    "arc_decal_excl_c4",
    "arc_decal_excl_c5",
    "arc_decal_excl_c6",
    "arc_decal_excl_c7",
    "arc_decal_excl_c8",
    "arc_palette_mode",
    "arc_outfit_color_pipeline",
    "arc_color_benchmark_id",
    "arc_appearance_attach_to_outfit",
    "arc_appearance_replace_selected",
    "arc_part_mode",
    "arc_outfit_colorway",
    "arc_appearance_body_skin",
    "arc_appearance_head",
    "arc_appearance_hair",
    "arc_appearance_facial_hair",
    "arc_appearance_hair_color",
    "arc_appearance_facial_hair_color",
    "arc_appearance_eyelash_copy_hair_color",
    "arc_appearance_backpack",
    "arc_appearance_backpack_colorway",
    "arc_appearance_attachment",
    "arc_appearance_charm",
    "arc_appearance_raider_tool",
    "arc_appearance_csv_path",
    "arc_include_reference",
    "arc_appearance_import_body",  # retired checkbox; deleted on register
    "arc_import_slot_offset",
    "arc_inherit_shape_key",
    "arc_import_slot_spacing",
    "arc_import_slot_row_spacing",
    "arc_deep_path_search_root",
    "arc_crease_edge_color",
    "arc_mask_debug_mode",
    "arc_mask_debug_source",
    "arc_mask_debug_color_n",
    "arc_mask_debug_grid_scale",
    "arc_mask_debug_opacity",  # legacy; prefer overlay_opacity
    "arc_mask_debug_overlay_opacity",
    "arc_mask_debug_colormask_opacity",
    "arc_mask_debug_numbers_opacity",
    "arc_mask_debug_show_colormask",
    "arc_mask_debug_show_numbers",
    "arc_mask_debug_numbers_xyz",  # retired; delete if present from older installs
    "arc_weapon_key",
    "arc_weapon_mod_muzzle",
    "arc_weapon_mod_stock",
    "arc_weapon_mod_magazine",
    "arc_weapon_mod_underbarrel",
    "arc_weapon_mod_tech",
    "arc_weapon_pattern",
    "arc_weapon_skin",
    "arc_last_weapon_armature",
    "arc_pose_preset",
    "arc_anim_search",
    "arc_selected_anim",
    "arc_anim_kind_filter",
    "arc_anim_spawn_notifies",
    "arc_anim_replace_action",
    "arc_anim_spawn_other_notifies",
    "arc_animation_cache",
    "arc_last_applied_anim",
    "arc_last_applied_anim_armature",
    "arc_lighting_look",
    "arc_lighting_lut_strength",
    "arc_lighting_hdri",
    "arc_lighting_hdri_strength",
    "arc_lighting_hdri_rotation_z",
    "arc_lighting_hdri_flip_x",
    "arc_lighting_apply_hdri",
    "arc_lighting_import_camera",
    "arc_lighting_apply_lut",
    "arc_lighting_override",
    "arc_lighting_look_applied",
    "arc_lighting_map",
    "arc_lighting_scenario",
    "arc_lighting_apply_bloom",
    "arc_lighting_apply_fog",
    "arc_lighting_fog_size_x",
    "arc_lighting_fog_size_y",
    "arc_lighting_fog_size_z",
    "arc_lighting_exposure_mode",
    "arc_lighting_meter_ev",
    "arc_lighting_exposure_comp",
    "arc_lighting_apply_vignette",
    "arc_lighting_apply_local_exposure",
    "arc_lighting_apply_sun",
    "arc_lighting_apply_clouds",
    "arc_lighting_cloud_time",
    "arc_lighting_lut_intensity",
)

# Props the Outfits (and shared) UI draws — used by ensure_scene_properties().
_UI_CRITICAL_SCENE_PROPS = (
    "arc_outfit_search",
    "arc_selected_outfit",
    "arc_selected_outfit_browse",
    "arc_pioneer_root",
    "arc_fmdex_root",
    "arc_outfit_csv_path",
    "arc_auto_listen",
    "arc_placement_listen_port",
    "arc_decal_method",
    "arc_decal_excl_slot",
    "arc_decal_excl_c1",
    "arc_decal_excl_c2",
    "arc_decal_excl_c3",
    "arc_decal_excl_c4",
    "arc_decal_excl_c5",
    "arc_decal_excl_c6",
    "arc_decal_excl_c7",
    "arc_decal_excl_c8",
    "arc_palette_mode",
    "arc_outfit_color_pipeline",
    "arc_color_benchmark_id",
    "arc_appearance_attach_to_outfit",
    "arc_appearance_replace_selected",
    "arc_part_mode",
    "arc_outfit_colorway",
    "arc_appearance_body_skin",
    "arc_appearance_head",
    "arc_appearance_hair",
    "arc_appearance_facial_hair",
    "arc_appearance_hair_color",
    "arc_appearance_facial_hair_color",
    "arc_appearance_eyelash_copy_hair_color",
    "arc_appearance_backpack",
    "arc_appearance_backpack_colorway",
    "arc_appearance_attachment",
    "arc_appearance_charm",
    "arc_appearance_raider_tool",
    "arc_appearance_csv_path",
    "arc_include_reference",
    "arc_import_slot_offset",
    "arc_inherit_shape_key",
    "arc_import_slot_spacing",
    "arc_import_slot_row_spacing",
    "arc_deep_path_search_root",
    "arc_crease_edge_color",
    "arc_mask_debug_mode",
    "arc_mask_debug_source",
    "arc_mask_debug_color_n",
    "arc_mask_debug_show_colormask",
    "arc_mask_debug_show_numbers",
    "arc_mask_debug_grid_scale",
    "arc_mask_debug_opacity",
    "arc_mask_debug_overlay_opacity",
    "arc_mask_debug_colormask_opacity",
    "arc_mask_debug_numbers_opacity",
    "arc_weapon_key",
    "arc_weapon_mod_muzzle",
    "arc_weapon_mod_stock",
    "arc_weapon_mod_magazine",
    "arc_weapon_mod_underbarrel",
    "arc_weapon_mod_tech",
    "arc_weapon_pattern",
    "arc_weapon_skin",
    "arc_pose_preset",
    "arc_anim_search",
    "arc_selected_anim",
    "arc_anim_kind_filter",
    "arc_anim_spawn_notifies",
    "arc_anim_replace_action",
    "arc_animation_cache",
    "arc_lighting_look",
    "arc_lighting_hdri",
    "arc_lighting_override",
    "arc_lighting_map",
    "arc_lighting_scenario",
    "arc_lighting_fog_size_x",
    "arc_lighting_fog_size_y",
    "arc_lighting_fog_size_z",
    "arc_lighting_exposure_mode",
    "arc_lighting_meter_ev",
    "arc_lighting_exposure_comp",
    "arc_lighting_apply_vignette",
    "arc_lighting_apply_local_exposure",
    "arc_lighting_apply_sun",
    "arc_lighting_apply_clouds",
    "arc_lighting_cloud_time",
    "arc_lighting_lut_intensity",
)

# Map-importer line only — safe to always remove when that line unregisters.
_MAP_ONLY_SCENE_PROPS = (
    "arc_placement_workspace",
    "arc_placement_map",
    "arc_placement_csv",
    "arc_placement_world_bounds_json",
    "arc_placement_heightmap_image",
    "arc_placement_ingame_map_image",
    "arc_placement_hlod_color_image",
    "arc_placement_batch_size",
    "arc_placement_map_name",
    "arc_map_focus_instancer_material",
    "arc_bridge_advanced",
    "arc_placement_mesh_root",
    "arc_map_unit_scale",
    "arc_map_mirror_y",
    "arc_water_shore_distance",
    "arc_water_shore_strength",
    "arc_water_shore_invert",
    "arc_water_shore_target_collection",
)


# Set True after a full check passes; cleared by register/unregister and load_post.
_SCENE_PROPS_VERIFIED = False


def _scene_props_need_repair() -> bool:
    if any(not hasattr(bpy.types.Scene, n) for n in _UI_CRITICAL_SCENE_PROPS):
        return True
    return (
        _scene_collection_fixed_type_id("arc_psk_entries") != "ArcOutfitsPSKEntry"
        or _scene_collection_fixed_type_id("arc_outfit_selections") != "ArcOutfitsSelection"
    )


def _deferred_scene_props_repair():
    ensure_scene_properties(repair=True)
    return None


def ensure_scene_properties(*, repair: bool = False) -> bool:
    """Re-register Scene props if another add-on line wiped them. Returns True if ready.

    Must stay cheap: the N-panel calls this on every redraw. Do not re-assign RNA
    properties here — that fires update callbacks and can tag_redraw VIEW_3D in a loop.
    Without ``repair`` (draw-time callers) it never registers: a missing prop returns
    False and schedules one repair on a timer, outside the draw callback.
    """
    global _SCENE_PROPS_VERIFIED
    if _SCENE_PROPS_VERIFIED and hasattr(bpy.types.Scene, "arc_outfit_search"):
        return True
    if not repair:
        if not _scene_props_need_repair():
            _SCENE_PROPS_VERIFIED = True
            return True
        try:
            if not bpy.app.timers.is_registered(_deferred_scene_props_repair):
                bpy.app.timers.register(_deferred_scene_props_repair, first_interval=0.0)
        except Exception:
            pass
        return False
    missing = [n for n in _UI_CRITICAL_SCENE_PROPS if not hasattr(bpy.types.Scene, n)]
    # Collection props can exist but point at a recycled Operator RNA id after reload.
    collections_broken = (
        _scene_collection_fixed_type_id("arc_psk_entries") != "ArcOutfitsPSKEntry"
        or _scene_collection_fixed_type_id("arc_outfit_selections") != "ArcOutfitsSelection"
    )
    if missing or collections_broken:
        try:
            register()
        except Exception as exc:
            print(f"Arc Raiders: ensure_scene_properties failed: {exc}")
    if not hasattr(bpy.types.Scene, "arc_mask_debug_color_n"):
        try:
            _register_mask_debug_color_n()
        except Exception as exc:
            print(f"Arc Raiders: refresh Color N prop failed: {exc}")
    still_missing = [n for n in _UI_CRITICAL_SCENE_PROPS if not hasattr(bpy.types.Scene, n)]
    if still_missing:
        print(f"Arc Raiders: Scene props still missing after ensure: {still_missing}")
        return False
    if (
        _scene_collection_fixed_type_id("arc_psk_entries") != "ArcOutfitsPSKEntry"
        or _scene_collection_fixed_type_id("arc_outfit_selections") != "ArcOutfitsSelection"
    ):
        print("Arc Raiders: Scene collection props still corrupted after ensure")
        return False
    _SCENE_PROPS_VERIFIED = True
    return True


@persistent
def _ensure_props_after_load(_unused):
    global _SCENE_PROPS_VERIFIED
    _SCENE_PROPS_VERIFIED = False
    try:
        ensure_scene_properties(repair=True)
    except Exception as exc:
        print(f"Arc Raiders: ensure props after load failed: {exc}")


def _drop_load_handlers():
    """Remove this handler, including copies left by a reloaded module object."""
    handlers = bpy.app.handlers.load_post
    for fn in list(handlers):
        if fn is _ensure_props_after_load or (
            getattr(fn, "__name__", "") == "_ensure_props_after_load"
            and getattr(fn, "__module__", "") == __name__
        ):
            handlers.remove(fn)


def _register_mask_debug_color_n():
    bpy.types.Scene.arc_mask_debug_color_n = IntProperty(
        name="Debug Color N",
        description="Number of color zones shown by the debug overlay",
        default=6,
        min=1,
        max=8,
        update=_on_mask_debug_color_n,
    )


def _scene_collection_fixed_type_id(name: str) -> str:
    """Return the CollectionProperty fixed_type identifier, or ''."""
    try:
        prop = bpy.types.Scene.bl_rna.properties.get(name)
    except Exception:
        return ""
    if prop is None or getattr(prop, "type", "") != "COLLECTION":
        return ""
    ft = getattr(prop, "fixed_type", None)
    return getattr(ft, "identifier", "") or ""


def _assign_collection_prop(name: str, expected_cls, prop) -> None:
    """Bind a Scene CollectionProperty, repairing RNA type-id corruption.

    After dual-addon reload, Blender can leave ``arc_psk_entries`` pointing at a
    recycled Operator RNA id (e.g. ``ARC_OUTFITS_OT_clear_anim_search``). ``add()``
    then returns that operator stub and ``entry.psk_path = ...`` crashes. Rebind
    whenever the fixed_type is missing or not the expected PropertyGroup.
    """
    expected = getattr(expected_cls, "__name__", "") or ""
    current = _scene_collection_fixed_type_id(name)
    if current == expected and hasattr(bpy.types.Scene, name):
        fixed_type = bpy.types.Scene.bl_rna.properties[name].fixed_type
        required = getattr(expected_cls, "__annotations__", {})
        if all(field in fixed_type.properties for field in required):
            return
    if hasattr(bpy.types.Scene, name):
        if current and current != expected:
            print(
                f"Arc Raiders: repairing Scene.{name} "
                f"(fixed_type was '{current}', expected '{expected}')"
            )
        try:
            delattr(bpy.types.Scene, name)
        except Exception:
            pass
    setattr(bpy.types.Scene, name, prop)


def _get_backpack_colorway(self):
    """Return a valid stable enum number even for unset or obsolete saved values."""
    from . import appearance
    items = appearance.make_backpack_colorway_enum_items(self, None)
    raw = self.get('arc_appearance_backpack_colorway', 0)
    for identifier, _label, _description, number in items:
        if raw == number or raw == identifier:
            return number
    return 0  # Game Default is always present, including before a Pak index loads.


def _set_backpack_colorway(self, value):
    self['arc_appearance_backpack_colorway'] = int(value)


def _update_backpack_choice(self, context):
    self.arc_appearance_backpack_colorway = 'BASE'


def _update_browser_multiple(self, context):
    from . import icon_browser
    icon_browser.on_multiple_changed(context)


def register_staged_import_props():
    """WindowManager props that the staged outfit import writes and the panel reads."""
    wm_type = bpy.types.WindowManager
    specs = {
        "arc_browser_multiple": BoolProperty(name="Select / Import Multiple", default=False, update=_update_browser_multiple),
        "arc_staged_active": BoolProperty(default=False),
        "arc_staged_stage": StringProperty(default=""),
        "arc_staged_index": IntProperty(default=0),
        "arc_staged_total": IntProperty(default=0),
        "arc_staged_label": StringProperty(default=""),
        "arc_staged_cancel": BoolProperty(default=False),
        "arc_confirm_show_parts": BoolProperty(
            name="Per-Part Skins", default=False,
            description="Show the per-part skin choices in the outfit import dialog",
        ),
    }
    for key in ADD_ALL_KEYS:
        specs[f"arc_add_all_{key.lower()}"] = BoolProperty(
            name="All", default=False,
            description="Make the Add button add every item in this list instead of the selected one",
        )
    for name, prop in specs.items():
        if not hasattr(wm_type, name):
            setattr(wm_type, name, prop)


# Session-only "All" checkboxes that turn each Add button into its Add All action.
ADD_ALL_KEYS = ("BODY", "HEAD", "HAIR", "FACIAL_HAIR", "BACKPACK", "ATTACHMENT", "CHARM",
                "RAIDER_TOOL", "WEAPONS", "ARC")


def unregister_staged_import_props():
    wm_type = bpy.types.WindowManager
    for name in ("arc_staged_active", "arc_staged_stage", "arc_staged_index",
                 "arc_staged_total", "arc_staged_label", "arc_staged_cancel",
                 "arc_confirm_show_parts", "arc_browser_multiple",
                 *(f"arc_add_all_{key.lower()}" for key in ADD_ALL_KEYS)):
        if hasattr(wm_type, name):
            try:
                delattr(wm_type, name)
            except Exception:
                pass


# Tooltips for properties declared with an empty description.
_TOOLTIPS = {
    "arc_part_mode": "What a part import does: start a new character, add to the selected character, or replace the same part on it",
    "arc_outfit_colorway": "Colorway used by Import Outfit. Default keeps the outfit's own colors",
    "arc_import_source": "Where assets are read from: the installed game, or files already exported with FModel",
    "arc_pak_dir": "Arc Raiders game folder (the Steam install that contains the Paks folder)",
    "arc_pioneer_root": "Folder that holds an FModel export of the game content",
    "arc_usmap_path": "Game mappings file (.usmap) used to read the game files. The bundled file is used when this is empty",
    "arc_outfit_csv_path": "Outfit catalog (.csv) that lists every outfit. The bundled catalog is used when this is empty",
    "arc_outfit_search": "Type part of an outfit name to filter the list",
    "arc_appearance_attach_to_outfit": "Parent a new part to the selected outfit so it follows that character",
    "arc_appearance_replace_selected": "Remove the selected part of the same type before the new part is added",
    "arc_appearance_body_skin": "Skin tone used for the body and head",
    "arc_appearance_head": "Head to add to the character",
    "arc_appearance_hair": "Hairstyle to add. Bald adds no hair",
    "arc_appearance_facial_hair": "Facial hair to add. Bald adds no facial hair",
    "arc_appearance_hair_color": "Hair color. Original Color keeps the color that ships with the hairstyle",
    "arc_appearance_facial_hair_color": "Facial hair color. Original Color keeps the color that ships with the style",
    "arc_appearance_eyelash_copy_hair_color": "Give the eyelashes the same color as the hair",
    "arc_appearance_backpack": "Backpack to add to the character",
    "arc_appearance_backpack_colorway": "Color variant of the chosen backpack",
    "arc_appearance_attachment": "Backpack attachment to add",
    "arc_appearance_charm": "Backpack charm to add",
    "arc_appearance_raider_tool": "Raider tool to add",
    "arc_include_reference": "Also add the in-game card image of the outfit as a reference plane",
    "arc_import_slot_offset": "Place each new import beside the previous one instead of on top of it",
    "arc_import_slot_spacing": "Distance between imports along X",
    "arc_import_slot_row_spacing": "Distance between rows of imports along Y",
    "arc_inherit_shape_key": "Copy the body shape of the outfit to parts added to it",
    "arc_ns_search": "Type part of an effect name to filter the list",
    "arc_ns_lut_only": "List only effects that have baked curve data, which import with correct motion",
    "arc_ns_bake_curves": "Bake the effect's curves into the imported node setup",
    "arc_ns_json_path": "Effect description file (.json) to import instead of a catalog entry",
    "arc_ns_catalog_root": "Folder that holds the effect catalog",
    "arc_ns_asset_root": "Folder that holds the meshes and textures used by effects",
    "arc_placement_listen_port": "TCP port on which Blender waits for FModel",
    "arc_auto_listen": "Start waiting for FModel when a file is opened",
    "arc_weapon_key": "Weapon to import",
    "arc_weapon_mod_muzzle": "Muzzle attachment to add to the selected weapon",
    "arc_weapon_mod_stock": "Stock to add to the selected weapon",
    "arc_weapon_mod_magazine": "Magazine to add to the selected weapon",
    "arc_weapon_mod_underbarrel": "Underbarrel attachment to add to the selected weapon",
    "arc_weapon_skin": "Color variant to apply to the selected weapon",
    "arc_pose_preset": "Ready-made pose for the selected character rig",
    "arc_anim_search": "Type part of an animation name to filter the list",
    "arc_anim_kind_filter": "Show only animations of this kind",
    "arc_anim_spawn_notifies": "Also add the props and effects that the animation triggers in the game",
    "arc_anim_replace_action": "Remove the action applied before, so that only the new animation stays on the rig",
    "arc_animation_cache": "Folder with animation files to use instead of the game install",
    "arc_lighting_map": "Game map whose lighting is recreated",
    "arc_lighting_scenario": "Time of day or weather condition of the chosen map",
    "arc_lighting_apply_lut": "Apply the game's color grade to the render",
    "arc_lighting_apply_hdri": "Use the game's sky as world lighting",
    "arc_lighting_apply_sun": "Add a sun lamp that matches the sky",
    "arc_lighting_apply_clouds": "Add the game's cloud layer to the sky",
    "arc_lighting_apply_bloom": "Add the game's glow around bright areas",
    "arc_lighting_apply_vignette": "Darken the image corners as the game does",
    "arc_lighting_apply_local_exposure": "Apply the game's local contrast adjustment",
    "arc_lighting_apply_fog": "Add a fog volume around the scene",
    "arc_lighting_exposure_mode": "How scene brightness is chosen",
    "arc_lighting_meter_ev": "Scene brightness in EV100 used for manual exposure",
    "arc_lighting_exposure_comp": "Exposure compensation in stops",
    "arc_lighting_hdri_strength": "Brightness of the sky lighting",
    "arc_lighting_cloud_time": "Moves the clouds along their path",
    "arc_lighting_override": "Pick the color grade and clouds by hand instead of from the scenario",
    "arc_lighting_look": "Color grade to use when the override is on",
    "arc_lighting_lut_intensity": "Strength of the color grade",
    "arc_lighting_hdri": "Cloud set to use when the override is on",
    "arc_lighting_lut_strength": "Strength of the color grade",
    "arc_lighting_hdri_rotation_z": "Turns the sky and clouds around the vertical axis",
    "arc_lighting_hdri_flip_x": "Mirrors the sky and clouds left to right",
    "arc_lighting_look_applied": "What the last lighting apply set up",
    "arc_lighting_fog_size_x": "Fog volume size along X",
    "arc_lighting_fog_size_y": "Fog volume size along Y",
    "arc_lighting_fog_size_z": "Fog volume size along Z",
    "arc_mask_debug_source": "Mask texture shown by the debug overlay",
    "arc_mask_debug_mode": "What the debug overlay shows",
    "arc_mask_debug_grid_scale": "Size of the debug grid",
    "arc_mask_debug_show_colormask": "Show the color mask in the overlay",
    "arc_mask_debug_show_numbers": "Show the zone numbers in the overlay",
    "arc_mask_debug_overlay_opacity": "How strongly the debug overlay covers the model",
    "arc_mask_debug_opacity": "How strongly the debug overlay covers the model (older setting)",
    "arc_mask_debug_colormask_opacity": "How strongly the color mask shows in the overlay",
    "arc_mask_debug_numbers_opacity": "How strongly the zone numbers show in the overlay",
    "arc_selected_outfit": "Outfit chosen in the list",
    "arc_selected_outfit_browse": "Pick an outfit from the full list",
    "arc_ns_selected": "Effect chosen in the list",
    "arc_selected_anim": "Animation chosen in the list",
    "arc_ns_play_ticks": "Number of simulation steps run for the preview. More steps take longer",
    "arc_ns_play_cache_dir": "Folder where simulated effect frames are saved",
    "arc_ns_play_shim": "Optional helper library file for the effect simulator",
    "arc_ns_play_vector_fields": "Optional force field file that pushes the particles",
    "arc_ns_play_collision": "Let particles bounce off scene objects. Slower to simulate",
    "arc_ns_play_at_cursor": "Place the previewed effect at the 3D cursor",
}


def register():
    for cls in classes:
        _safe_register_class(cls)
    register_staged_import_props()

    def _fill_tooltip(name, prop):
        tip = _TOOLTIPS.get(name)
        if tip:
            try:
                # Fill only an empty tooltip; an explicit description wins.
                if not prop.keywords.get("description"):
                    prop.keywords["description"] = tip
            except AttributeError:
                pass

    def _assign_if_free(name, prop):
        if hasattr(bpy.types.Scene, name):
            return
        _fill_tooltip(name, prop)
        setattr(bpy.types.Scene, name, prop)

    def _assign_outfit_selector(name, prop):
        from . import addon_line
        if addon_line.is_map_importer_line():
            _assign_if_free(name, prop)
            return
        # Older Map Importer builds may have registered these shared properties
        # first. Outfits owns their callbacks and stable enum IDs; underlying
        # Scene ID properties (including the selected ST key) are retained.
        _fill_tooltip(name, prop)
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
        setattr(bpy.types.Scene, name, prop)

    _assign_collection_prop(
        "arc_psk_entries",
        ArcOutfitsPSKEntry,
        CollectionProperty(type=ArcOutfitsPSKEntry),
    )
    _assign_collection_prop(
        "arc_outfit_selections",
        ArcOutfitsSelection,
        CollectionProperty(type=ArcOutfitsSelection),
    )
    _assign_if_free(
        "arc_import_source",
        EnumProperty(
            name="Import Source",
            items=[('PAK', 'Game Install', "Read assets straight from the installed game"),
                   ('FILES', 'FModel Export', "Read assets from a folder exported with FModel")],
            default='PAK',
        ),
    )
    _assign_if_free(
        "arc_pioneer_root",
        StringProperty(
            name='FModel Output',
            description="",
            default="",
            subtype='DIR_PATH',
        ),
    )
    _assign_if_free(
        "arc_pak_dir",
        StringProperty(
            name="Steam Install",
            description="",
            default=_default_steam_install_dir(),
            subtype="DIR_PATH",
        ),
    )
    _assign_if_free(
        "arc_usmap_path",
        StringProperty(
            name="Usmap",
            description="",
            default=_default_bundled_usmap_path(),
            subtype="FILE_PATH",
        ),
    )

    # Body / head / hair sidebar (must use _assign_if_free — early return below
    # skips bulk registration on reload when MapImporter RNA is already present).
    from . import appearance as _appearance

    # Retired: body was a checkbox; now three Add buttons. Drop RNA if present.
    if hasattr(bpy.types.Scene, "arc_appearance_import_body"):
        try:
            delattr(bpy.types.Scene, "arc_appearance_import_body")
        except Exception:
            pass
    _assign_if_free(
        "arc_part_mode",
        EnumProperty(
            name="Part Mode",
            description="",
            items=_PART_MODE_ITEMS,
            get=_get_part_mode,
            set=_set_part_mode,
        ),
    )
    _assign_if_free(
        "arc_outfit_colorway",
        EnumProperty(
            name="Colorway",
            description="",
            items=make_outfit_colorway_items,
        ),
    )
    # Older blends and the old sidebar still read these; both now mirror arc_part_mode.
    _assign_if_free(
        "arc_appearance_attach_to_outfit",
        BoolProperty(
            name="Add to Character",
            description="",
            get=_get_attach_to_outfit,
            set=_set_attach_to_outfit,
        ),
    )
    _assign_if_free(
        "arc_appearance_replace_selected",
        BoolProperty(
            name="Replace Part",
            description="",
            get=_get_replace_selected,
            set=_set_replace_selected,
        ),
    )
    _assign_if_free(
        "arc_appearance_csv_path",
        StringProperty(
            name="Appearance Reference CSV",
            description="",
            default="",
            subtype='FILE_PATH',
        ),
    )
    _assign_if_free(
        "arc_appearance_body_skin",
        EnumProperty(
            name="Body Skin",
            description="",
            items=make_body_items,
        ),
    )
    _assign_if_free(
        "arc_appearance_head",
        EnumProperty(
            name="Head",
            description="",
            items=_appearance.make_head_enum_items,
        ),
    )
    _assign_outfit_selector(
        "arc_appearance_hair",
        EnumProperty(
            name="Hair",
            description="",
            items=_appearance.make_hair_enum_items,
        ),
    )
    _assign_outfit_selector(
        "arc_appearance_facial_hair",
        EnumProperty(
            name="Facial Hair",
            description="",
            items=_appearance.make_facial_hair_enum_items,
        ),
    )
    _assign_if_free(
        "arc_appearance_hair_color",
        EnumProperty(
            name="Hair Color",
            description="",
            items=_appearance.make_hair_color_enum_items,
        ),
    )
    _assign_if_free(
        "arc_appearance_facial_hair_color",
        EnumProperty(
            name="Hair Color",
            description="",
            items=_appearance.make_hair_color_enum_items,
        ),
    )
    _assign_if_free(
        "arc_appearance_eyelash_copy_hair_color",
        BoolProperty(
            name="Eyelash copy Hair Color",
            description="",
            default=True,
        ),
    )
    _assign_outfit_selector(
        "arc_appearance_backpack",
        EnumProperty(
            name="Backpack",
            description="",
            items=_appearance.make_backpack_enum_items,
            update=_update_backpack_choice,
        ),
    )
    _assign_outfit_selector(
        "arc_appearance_backpack_colorway",
        EnumProperty(
            name="Colorway",
            description="",
            items=_appearance.make_backpack_colorway_enum_items,
            default=0,
            get=_get_backpack_colorway,
            set=_set_backpack_colorway,
        ),
    )
    _assign_if_free(
        "arc_appearance_attachment",
        EnumProperty(
            name="Backpack Attachment",
            description="",
            items=_appearance.make_attachment_enum_items,
        ),
    )
    _assign_if_free(
        "arc_appearance_charm",
        EnumProperty(
            name="Charm",
            description="",
            items=_appearance.make_charm_enum_items,
        ),
    )
    _assign_if_free(
        "arc_appearance_raider_tool",
        EnumProperty(
            name="Raider Tool",
            description="",
            items=_appearance.make_raider_tool_enum_items,
        ),
    )
    _assign_if_free(
        "arc_include_reference",
        BoolProperty(
            name="Include Reference",
            description="",
            default=True,
        ),
    )
    _assign_if_free(
        "arc_deep_path_search_root",
        StringProperty(
            name="Deep Path Search Folder",
            description="",
            default="",
            subtype='DIR_PATH',
        ),
    )
    _assign_if_free(
        "arc_import_slot_offset",
        BoolProperty(
            name="Offset Characters",
            description="",
            default=True,
        ),
    )
    _assign_if_free(
        "arc_inherit_shape_key",
        BoolProperty(
            name="Inherit Shape Key",
            description="",
            default=True,
            update=_update_inherit_shape_key,
        ),
    )
    _assign_if_free(
        "arc_import_slot_spacing",
        FloatProperty(
            name="Character Spacing",
            description="",
            default=2.0,
            min=0.5,
            max=50.0,
            soft_min=1.0,
            soft_max=10.0,
            step=10,
            precision=2,
            unit='LENGTH',
        ),
    )

    _assign_if_free(
        "arc_import_slot_row_spacing",
        FloatProperty(
            name="Row Spacing",
            description="",
            default=8.0, min=0.5, max=100.0, soft_min=2.0, soft_max=20.0,
            step=10, precision=2, unit='LENGTH',
        ),
    )

    # Map Importer may already own shared Scene RNA. Later assigns skip
    # properties that exist so Pose / Animations / Lighting still register.

    _assign_if_free(
        "arc_ns_json_path",
        StringProperty(
        name='NS JSON',
        description="",
        default="",
        subtype='FILE_PATH',
    )
    )
    _assign_if_free(
        "arc_ns_asset_root",
        StringProperty(
        name='NS Asset Root',
        description="",
        default="",
        subtype='DIR_PATH',
    )
    )
    _assign_if_free(
        "arc_ns_catalog_root",
        StringProperty(
        name='NS Catalog Folder',
        description="",
        default="",
        subtype='DIR_PATH',
    )
    )
    _assign_if_free(
        "arc_ns_search",
        StringProperty(
        name='NS Search',
        description="",
        default="",
        options={'TEXTEDIT_UPDATE'},
        update=_tag_search_redraw,
    )
    )
    _assign_if_free(
        "arc_ns_selected",
        StringProperty(
        name='Selected NS',
        description="",
        default="",
    )
    )
    _assign_if_free(
        "arc_ns_lut_only",
        BoolProperty(
        name='LUT dumps only',
        description="",
        default=False,
    )
    )
    _assign_if_free(
        "arc_ns_bake_curves",
        BoolProperty(
        name='Bake Curve LUTs',
        description="",
        default=True,
    )
    )
    from ..Niagara import niagara as niagara_playback
    niagara_playback.register_properties(_assign_if_free)
    _assign_if_free(
        "arc_fmdex_root",
        StringProperty(
            name='FMDex Folder (FModel)',
            description="",
            default="",
            subtype='DIR_PATH',
        ),
    )
    _assign_if_free(
        "arc_manual_outfit_folder",
        StringProperty(
        name='Manual DA_OI_Outfit Folder',
        description="",
        default="", subtype='DIR_PATH',
    )
    )
    _assign_if_free(
        "arc_outfit_preset",
        EnumProperty(
        name="Outfit Preset",
        description="",
        items=make_outfit_preset_items,
    )
    )
    _assign_if_free(
        "arc_outfit_csv_path",
        StringProperty(
        name="Outfit Reference CSV",
        description="",
        default="", subtype='FILE_PATH',
    )
    )
    _assign_outfit_selector(
        "arc_selected_outfit",
        StringProperty(
        name="Selected Outfit Key",
        description="",
        default=_DEFAULT_OUTFIT_KEY,
        update=_on_selected_outfit_changed,
    )
    )
    _assign_outfit_selector(
        "arc_selected_outfit_browse",
        EnumProperty(
        name="Browse Outfits",
        description="",
        items=make_outfit_selector_items,
        default=0,
        get=_browse_outfit_get,
        set=_browse_outfit_set,
    )
    )
    _assign_outfit_selector(
        "arc_outfit_search",
        StringProperty(
        name="Search Outfits",
        description="",
        default="",
        options={'TEXTEDIT_UPDATE'},
        update=_tag_search_redraw,
    )
    )
    # FModel bridge (outfits + map-importer): listener for import_models / import_outfit
    _assign_if_free(
        "arc_placement_listen_port",
        IntProperty(
            name="FModel Listen Port",
            description="",
            default=28563,
            min=1024,
            max=65535,
        ),
    )
    _assign_if_free(
        "arc_auto_listen",
        BoolProperty(
            name="Auto-start FModel Listener",
            description="",
            default=True,
        ),
    )

    # Map Placement / GroundPlane / shore — map-importer line only
    from . import addon_line as _addon_line

    if _addon_line.is_map_importer_line():
        from ..Maps import map_placement as _map_placement

        bpy.types.Scene.arc_placement_workspace = StringProperty(
            name="Placement Workspace",
            description="",
            default="",
            subtype='DIR_PATH',
        )
        bpy.types.Scene.arc_placement_map = EnumProperty(
            name="Map",
            description="Game map whose objects are placed in the scene",
            items=_map_placement.make_map_enum_items,
            update=_map_placement._on_placement_map_changed,
        )
        bpy.types.Scene.arc_placement_csv = StringProperty(
            name="Placement CSV",
            description="Exported list of object positions for the map (.csv)",
            default="",
            subtype='FILE_PATH',
        )
        bpy.types.Scene.arc_placement_world_bounds_json = StringProperty(
            name="World Bounds JSON",
            description="",
            default="",
            subtype='FILE_PATH',
        )
        bpy.types.Scene.arc_placement_heightmap_image = StringProperty(
            name="Heightmap Image",
            description="",
            default="",
            subtype='FILE_PATH',
        )
        bpy.types.Scene.arc_placement_ingame_map_image = StringProperty(
            name="In-Game Map Image",
            description="",
            default="",
            subtype='FILE_PATH',
        )
        bpy.types.Scene.arc_placement_hlod_color_image = StringProperty(
            name="HLOD Color Image",
            description="",
            default="",
            subtype='FILE_PATH',
        )
        bpy.types.Scene.arc_placement_batch_size = IntProperty(
            name="Placement Batch Size",
            description="",
            default=100,
            min=1,
            max=2000,
        )
        bpy.types.Scene.arc_map_focus_instancer_material = BoolProperty(
            name="Focus Instancer Materials",
            description="",
            default=True,
        )
        bpy.types.Scene.arc_bridge_advanced = BoolProperty(
            name="Advanced / Backup",
            description="",
            default=False,
        )
        bpy.types.Scene.arc_placement_mesh_root = StringProperty(
            name="Map Mesh Export Folder",
            description="",
            default="",
            subtype='DIR_PATH',
        )
        bpy.types.Scene.arc_map_unit_scale = FloatProperty(
            name="Map Unit Scale",
            description="",
            default=_map_placement.MAP_UNIT_SCALE,
            min=0.0001,
            max=1.0,
        )
        bpy.types.Scene.arc_map_mirror_y = BoolProperty(
            name="Map Mirror Y",
            description="",
            default=_map_placement.MAP_MIRROR_Y,
        )
        bpy.types.Scene.arc_water_shore_distance = FloatProperty(
            name="Water Shore Distance",
            description="",
            default=2.0,
            min=0.05,
            max=50.0,
            subtype='DISTANCE',
        )
        bpy.types.Scene.arc_water_shore_strength = FloatProperty(
            name="Water Shore Strength",
            description="",
            default=1.0,
            min=0.0,
            max=4.0,
        )
        bpy.types.Scene.arc_water_shore_invert = BoolProperty(
            name="Invert Water Shore Mask",
            description="",
            default=False,
        )
        bpy.types.Scene.arc_water_shore_target_collection = PointerProperty(
            name="Shore Target Collection",
            description="",
            type=bpy.types.Collection,
        )

    # Blender-only experiment switch: FModel keeps one validated decal transform.
    from ..Shared import materials as _materials

    _assign_if_free(
        "arc_decal_method",
        EnumProperty(
        name="Decal Placement",
        description="",
        items=_materials.DECAL_PLACEMENT_METHODS,
        default=_materials.DECAL_PLACEMENT_DEFAULT,
    )
    )
    _assign_if_free(
        "arc_decal_excl_slot",
        EnumProperty(
        name="Decal Slot",
        description="",
        items=[
            ("0", "All Decals", ""),
            ("1", "Decal 1", ""),
            ("2", "Decal 2", ""),
            ("3", "Decal 3", ""),
            ("4", "Decal 4", ""),
            ("5", "Decal 5", ""),
            ("6", "Decal 6", ""),
            ("7", "Decal 7", ""),
            ("8", "Decal 8", ""),
        ],
        default="0",
    )
    )
    for _zi in range(1, 9):
        setattr(
            bpy.types.Scene,
            f"arc_decal_excl_c{_zi}",
            BoolProperty(
                name=f"Colour {_zi}",
                description="",
                default=False,
            ),
        )
    _assign_if_free(
        "arc_palette_mode",
        EnumProperty(
        name="Palette Routing",
        description="",
        items=[
            ('AUTO', "Auto", ""),
            ('PRIMARY', "Primary", ""),
            ('SECONDARY', "Secondary", ""),
            ('SWAP', "Swap", ""),
        ],
        default='AUTO',
    )
    )
    # D022–D032 ground truth vs pre-D022 ColorMask_XYZ / D010 soft Mix.
    # Revert: set LEGACY then Update Materials (legacy path is fully retained).
    _assign_if_free(
        "arc_outfit_color_pipeline",
        EnumProperty(
        name="Outfit Color Pipeline",
        description="",
        items=[
            (
                'LEGACY',
                "Legacy",
                "",
            ),
            (
                'GROUND_TRUTH',
                "Ground Truth",
                "",
            ),
        ],
        default='GROUND_TRUTH',
    )
    )
    _assign_if_free(
        "arc_color_benchmark_id",
        EnumProperty(
        name="Color Benchmark",
        description="",
        items=[
            ('ALL', "All B1–B8", ""),
            ('B1', "B1 Abyss Lower Cotton", ""),
            ('B2', "B2 Abyss YellowBlack", ""),
            ('B3', "B3 Moonball Upper Poly", ""),
            ('B4', "B4 Moonball Lower Cotton", ""),
            ('B5', "B5 Horns Leather_Black", ""),
            ('B6', "B6 Horns Leather", ""),
            ('B7', "B7 Dweller Leather_Yellow", ""),
            ('B8', "B8 Batter Quilted_Blue", ""),
        ],
        default='ALL',
    )
    )
    _assign_if_free(
        "arc_crease_edge_color",
        BoolProperty(
        name="Crease/Edge Color",
        description="",
        default=True,
    )
    )
    # Internal: UI toggle removed; import always organizes nodes at end of material build.
    _assign_if_free(
        "arc_organize_nodes_on_import",
        BoolProperty(
        name="Organize Nodes on Import",
        description="",
        default=True,
    )
    )
    # Internal: UI toggle removed; import path hardcodes prefetch On via utils.io_prefetch_enabled.

    _assign_if_free(
        "arc_io_prefetch",
        BoolProperty(
        name="IO Prefetch",
        description="",
        default=True,
    )
    )

    # Mask Debug — Apply-only (no live update callbacks).
    _assign_if_free(
        "arc_mask_debug_source",
        EnumProperty(
        name="Zone Source",
        description="",
        items=_MASK_DEBUG_SOURCE_ITEMS,
        default="procedural",
    )
    )
    _assign_if_free(
        "arc_mask_debug_mode",
        EnumProperty(
        name="Mask Debug",
        description="",
        items=_MASK_DEBUG_MODE_ITEMS,
        default="0",
    )
    )
    _register_mask_debug_color_n()
    _assign_if_free(
        "arc_mask_debug_grid_scale",
        FloatProperty(
        name="Debug Grid Scale",
        description="",
        default=1.0,
        min=0.5,
        soft_max=10.0,
        max=10.0,
    )
    )
    _assign_if_free(
        "arc_mask_debug_overlay_opacity",
        FloatProperty(
        name="Overlay Opacity",
        description="",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    )
    _assign_if_free(
        "arc_mask_debug_opacity",
        FloatProperty(
        name="Overlay Opacity (legacy)",
        description="",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    )
    _assign_if_free(
        "arc_mask_debug_colormask_opacity",
        FloatProperty(
        name="ColorMask Opacity",
        description="",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    )
    _assign_if_free(
        "arc_mask_debug_numbers_opacity",
        FloatProperty(
        name="Numbers Opacity",
        description="",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    )
    _assign_if_free(
        "arc_mask_debug_show_colormask",
        BoolProperty(
        name="Show ColorMask",
        description="",
        default=False,
    )
    )
    _assign_if_free(
        "arc_mask_debug_show_numbers",
        BoolProperty(
        name="Show Numbers",
        description="",
        default=True,
    )
    )

    # Weapons browser (ST hard catalog). Callbacks are module-level so Blender
    # RNA keeps a stable reference (nested defs can be GC'd → mojibake labels).
    _assign_if_free(
        "arc_weapon_key",
        EnumProperty(
        name="Weapon",
        description="",
        items=_weapon_enum_items,
    )
    )
    _assign_if_free(
        "arc_weapon_mod_muzzle",
        EnumProperty(
        name="Muzzle",
        description="",
        items=_mod_items_muzzle,
    )
    )
    _assign_if_free(
        "arc_weapon_mod_stock",
        EnumProperty(
        name="Stock",
        description="",
        items=_mod_items_stock,
    )
    )
    _assign_if_free(
        "arc_weapon_mod_magazine",
        EnumProperty(
        name="Magazine",
        description="",
        items=_mod_items_magazine,
    )
    )
    _assign_if_free(
        "arc_weapon_mod_underbarrel",
        EnumProperty(
        name="UnderBarrel",
        description="",
        items=_mod_items_underbarrel,
    )
    )
    _assign_if_free(
        "arc_weapon_mod_tech",
        EnumProperty(
        name="Tech",
        description="",
        items=_mod_items_tech,
    )
    )
    _assign_if_free(
        "arc_weapon_skin",
        EnumProperty(
        name="Weapon Skin",
        description="",
        items=_weapon_skin_enum_items,
    )
    )
    _assign_if_free(
        "arc_weapon_pattern",
        EnumProperty(
        name="Weapon Pattern",
        description="",
        items=_pattern_enum_items,
    )
    )
    _assign_if_free(
        "arc_last_weapon_armature",
        StringProperty(
        name="Last Weapon Armature",
        description="",
        default="",
    )
    )

    from ..Animations import pose_presets as _pose_presets

    _assign_if_free(
        "arc_pose_preset",
        EnumProperty(
        name="Pose",
        description="",
        items=_pose_presets.POSE_ENUM_ITEMS,
        default="APOSE",
    )
    )
    _assign_if_free(
        "arc_anim_search",
        StringProperty(
        name="Search Animations",
        description="",
        default="",
        options={'TEXTEDIT_UPDATE'},
        update=_tag_search_redraw,
    )
    )
    _assign_if_free(
        "arc_selected_anim",
        StringProperty(
        name="Selected Animation",
        description="",
        default="",
    )
    )
    _assign_if_free(
        "arc_anim_kind_filter",
        EnumProperty(
        name="Animation Kind",
        description="",
        items=_ANIM_KIND_ITEMS,
        default="ALL",
    )
    )
    _assign_if_free(
        "arc_anim_spawn_notifies",
        BoolProperty(
        name="Spawn Notify Props",
        description="",
        default=True,
    )
    )
    _assign_if_free(
        "arc_anim_replace_action",
        BoolProperty(
        name="Replace Previous Action",
        description="",
        default=True,
    )
    )
    _assign_if_free(
        "arc_anim_spawn_other_notifies",
        BoolProperty(
        name="Spawn Other Notifies",
        description="",
        default=False,
    )
    )
    _assign_if_free(
        "arc_animation_cache",
        StringProperty(
        name="Animation Cache",
        description="",
        default="",
        subtype="DIR_PATH",
    )
    )
    _assign_if_free(
        "arc_last_applied_anim",
        StringProperty(
        name="Last Applied Animation",
        default="",
    )
    )
    _assign_if_free(
        "arc_last_applied_anim_armature",
        StringProperty(
        name="Last Animation Armature",
        description="Rig that received the last applied animation",
        default="",
    )
    )

    # Lighting Look (game color pipeline, sky and sun from the scenario catalog)
    from ..Lighting import lighting_looks as _lighting_looks

    _assign_if_free(
        "arc_lighting_look",
        EnumProperty(
        name="LUT Override",
        description="",
        items=_lighting_looks.make_lighting_look_items,
    )
    )
    _assign_if_free(
        "arc_lighting_lut_intensity",
        FloatProperty(
        name="LUT Intensity",
        description="",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    )
    _assign_if_free(
        "arc_lighting_exposure_mode",
        EnumProperty(
        name="Exposure",
        description="",
        items=(
            ("AUTO", "Auto (game metering)",
             "", 0),
            ("MANUAL", "Locked meter",
             "", 1),
        ),
        default="AUTO",
    )
    )
    _assign_if_free(
        "arc_lighting_meter_ev",
        FloatProperty(
        name="Meter EV100",
        description="",
        default=2.0,
        soft_min=-8.0,
        soft_max=12.0,
        step=25,
    )
    )
    _assign_if_free(
        "arc_lighting_exposure_comp",
        FloatProperty(
        name="Exposure Compensation",
        description="",
        default=0.0,
        soft_min=-4.0,
        soft_max=4.0,
        step=25,
    )
    )
    _assign_if_free(
        "arc_lighting_apply_vignette",
        BoolProperty(
        name="Vignette",
        description="",
        default=True,
    )
    )
    _assign_if_free(
        "arc_lighting_apply_local_exposure",
        BoolProperty(
        name="Local Exposure",
        description="",
        default=True,
    )
    )
    _assign_if_free(
        "arc_lighting_apply_sun",
        BoolProperty(
        name="Sun Lamp",
        description="",
        default=True,
    )
    )
    _assign_if_free(
        "arc_lighting_apply_clouds",
        BoolProperty(
        name="Clouds",
        description="",
        default=True,
    )
    )
    _assign_if_free(
        "arc_lighting_cloud_time",
        FloatProperty(
        name="Cloud Time",
        description="",
        default=0.0,
        subtype="TIME_ABSOLUTE",
    )
    )
    _assign_if_free(
        "arc_lighting_lut_strength",
        FloatProperty(
        name="LUT Strength",
        description="",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    )
    _assign_if_free(
        "arc_lighting_hdri",
        EnumProperty(
        name="Clouds Override",
        description="",
        items=_lighting_looks.make_lighting_hdri_items,
    )
    )
    _assign_if_free(
        "arc_lighting_hdri_strength",
        FloatProperty(
        name="Sky Strength",
        description="",
        default=1.0,
        min=0.0,
        soft_max=4.0,
        max=64.0,
    )
    )
    _assign_if_free(
        "arc_lighting_hdri_rotation_z",
        FloatProperty(
        name="HDRI Rotation Z",
        description="",
        default=227.52,
        min=-360.0,
        max=360.0,
        soft_min=0.0,
        soft_max=360.0,
    )
    )
    _assign_if_free(
        "arc_lighting_hdri_flip_x",
        BoolProperty(
        name="HDRI Flip X",
        description="",
        default=False,
    )
    )
    _assign_if_free(
        "arc_lighting_apply_lut",
        BoolProperty(
        name="Game Color Grade",
        description="",
        default=True,
    )
    )
    _assign_if_free(
        "arc_lighting_import_camera",
        BoolProperty(
        name="Camera",
        description="Add the camera that matches the scenario (main menu, outfit icon, gun inspect) and make it the scene camera",
        default=True,
    )
    )
    _assign_if_free(
        "arc_lighting_apply_hdri",
        BoolProperty(
        name="Sky & Sun",
        description="",
        default=True,
    )
    )
    _assign_if_free(
        "arc_lighting_override",
        BoolProperty(
        name="Override LUT / Clouds",
        description="",
        default=False,
    )
    )
    _assign_if_free(
        "arc_lighting_look_applied",
        StringProperty(
        name="Last Lighting Look",
        description="",
        default="",
    )
    )

    from ..Lighting import lighting_catalog as _lighting_catalog

    from ..Lighting import studio_lights as _studio_lights

    _assign_if_free(
        "arc_studio_light_rig",
        EnumProperty(
        name="Studio Light Rig",
        description="User-authored light rig imported exactly as authored",
        items=_studio_lights.make_rig_items,
    )
    )
    _assign_if_free(
        "arc_lighting_map",
        EnumProperty(
        name="Lighting Map",
        description="",
        items=_lighting_catalog.make_lighting_map_items,
        update=_lighting_looks.on_lighting_map_update,
    )
    )
    _assign_if_free(
        "arc_lighting_scenario",
        EnumProperty(
        name="Lighting Scenario",
        description="",
        items=_lighting_catalog.make_lighting_scenario_items,
        update=_lighting_looks.on_lighting_scenario_update,
    )
    )
    _assign_if_free(
        "arc_lighting_apply_bloom",
        BoolProperty(
        name="Bloom",
        description="",
        default=True,
    )
    )
    _assign_if_free(
        "arc_lighting_apply_fog",
        BoolProperty(
        name="Fog Box",
        description="",
        default=False,
    )
    )
    _assign_if_free(
        "arc_lighting_fog_size_x",
        FloatProperty(
        name="Fog Size X",
        description="",
        default=2000.0,
        min=1.0,
        soft_max=8000.0,
        unit="LENGTH",
    )
    )
    _assign_if_free(
        "arc_lighting_fog_size_y",
        FloatProperty(
        name="Fog Size Y",
        description="",
        default=2000.0,
        min=1.0,
        soft_max=8000.0,
        unit="LENGTH",
    )
    )
    _assign_if_free(
        "arc_lighting_fog_size_z",
        FloatProperty(
        name="Fog Size Z",
        description="",
        default=500.0,
        min=1.0,
        soft_max=4000.0,
        unit="LENGTH",
    )
    )

    # Migrate legacy mode ints: old FalseColor becomes Mode 3.
    try:
        # addon_utils.enable() may call register() while bpy.data is restricted.
        # Skip this migration until Scene data is available; defaults remain valid.
        scenes = getattr(bpy.data, "scenes", None)
        for scene in scenes or ():
            raw = getattr(scene, "arc_mask_debug_mode", "0")
            try:
                mi = int(raw)
            except Exception:
                mi = 0
            if mi >= 3:
                scene.arc_mask_debug_mode = "3"
                scene.arc_mask_debug_show_colormask = True
            elif mi == 2:
                scene.arc_mask_debug_mode = "3"
            else:
                scene.arc_mask_debug_mode = str(max(0, min(3, mi)))
            try:
                if hasattr(scene, "arc_mask_debug_opacity") and hasattr(
                    scene, "arc_mask_debug_overlay_opacity"
                ):
                    scene.arc_mask_debug_overlay_opacity = float(scene.arc_mask_debug_opacity)
            except Exception:
                pass
    except Exception:
        pass



    register_outfit_defaults()
    global _SCENE_PROPS_VERIFIED
    _SCENE_PROPS_VERIFIED = False
    _drop_load_handlers()
    bpy.app.handlers.load_post.append(_ensure_props_after_load)


def unregister():
    global _SCENE_PROPS_VERIFIED
    _SCENE_PROPS_VERIFIED = False
    _drop_load_handlers()
    if bpy.app.timers.is_registered(_deferred_scene_props_repair):
        bpy.app.timers.unregister(_deferred_scene_props_repair)
    if bpy.app.timers.is_registered(_refresh_outfit_csv_state):
        bpy.app.timers.unregister(_refresh_outfit_csv_state)
    _CSV_STAT_MEMO.clear()
    _CSV_ROWS_MEMO.clear()
    _OUTFIT_ENUM_CACHE.update(key=None, items=None)
    unregister_outfit_defaults()
    unregister_staged_import_props()
    from . import addon_line as _addon_line

    # Always drop this line's exclusive Scene props.
    names = list(_MAP_ONLY_SCENE_PROPS) if _addon_line.is_map_importer_line() else []
    # Shared RNA: only delete when the sibling DataRaiders add-on is not enabled.
    if not _addon_line.sibling_addon_enabled():
        names.extend(_SHARED_SCENE_PROPS)
    else:
        print(
            "Arc Raiders: leaving shared Scene props registered "
            f"(sibling {_addon_line.sibling_addon_module_candidates()[0]} still enabled)"
        )

    for name in names:
        _safe_del_scene_prop(name)

    # PropertyGroup classes are shared RNA too — only unregister when alone.
    if not _addon_line.sibling_addon_enabled():
        for cls in reversed(classes):
            _safe_unregister_class(cls)
