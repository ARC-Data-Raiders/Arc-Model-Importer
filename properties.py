"""
Property definitions for the Arc Raiders Importer
"""

import bpy
import os
from bpy.props import StringProperty, CollectionProperty, BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty
from bpy.types import PropertyGroup

from . import utils
from .textures import (
    scan_skins, is_body, is_hair, scan_hair_mis, detect_model_type,
    get_skins_folder, get_all_skin_dirs, is_variant, get_default_skin_dir,
)
from .importing import get_outfit_folder, scan_outfit_presets, get_character_name, load_outfit_csv, get_outfit_csv_path

_csv_model_to_ui_folder = {}

def rebuild_csv_outfit_map(csv_path=None):
    """Rebuild the Model Folder Name -> Item/UI Folder Name mapping."""
    if csv_path is None:
        try:
            csv_path = get_outfit_csv_path(bpy.context)
        except Exception:
            return
    rows = load_outfit_csv(csv_path)
    _csv_model_to_ui_folder.clear()
    for row in rows:
        model = (row.get("Model Folder Name") or "").strip()
        ui = (row.get("Item/UI Folder Name") or "").strip().split(";")[0].strip()
        if model and ui:
            _csv_model_to_ui_folder[utils.normalize_folder_name(model)] = ui

# ---------------------------------------------------------------------------
# Body variant data (not hardcoded in drawing)
# ---------------------------------------------------------------------------

BODY_VARIANTS = [
    ('NONE',          '— No Body Skin —',  ''),
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
    from .importing import queue_supports_outfit_batch

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

def make_outfit_preset_items(self, context):
    """Dynamic EnumProperty items callback for outfit preset dropdown."""
    if not context:
        return [('NONE', '— Select Outfit —', '')]
    cache = get_confirm_dialog_cache(context)
    if cache is not None:
        return list(cache["outfit_preset_items"])
    from .importing import queue_supports_outfit_batch
    return _build_outfit_preset_items(
        context, is_outfit_queue=queue_supports_outfit_batch(context)
    )

def _on_browse_outfit_changed(self, context):
    if self.arc_selected_outfit_browse and self.arc_selected_outfit_browse != 'NONE':
        self.arc_selected_outfit = self.arc_selected_outfit_browse


def _on_mask_debug_color_n(self, context):
    """Live Color N zone gate whenever Mask Debug inject is present."""
    global _mask_debug_updating
    if _mask_debug_updating:
        return
    try:
        from . import mask_debug
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


def make_outfit_selector_items(self, context):
    """Dynamic EnumProperty items callback for outfit selector."""
    from .importing import load_outfit_csv, get_outfit_csv_path, outfit_row_key, outfit_display_label

    csv_path = get_outfit_csv_path(context)
    try:
        mtime = os.path.getmtime(csv_path) if csv_path else 0.0
    except OSError:
        mtime = 0.0
    cache_key = (csv_path, mtime)
    cached = _OUTFIT_ENUM_CACHE
    if cached["key"] == cache_key and cached["items"]:
        return cached["items"]

    rows = load_outfit_csv(csv_path)
    items = []
    unnamed = []
    for row in rows:
        st = row.get("ST", "")
        if st and not st.upper().startswith("ID_PLAYERSKIN_"):
            continue
        if not row.get("Item/UI Folder Name") and not row.get("Model Folder Name"):
            continue
        key = outfit_row_key(row)
        label = outfit_display_label(row)
        if "unnamed" in label.lower():
            unnamed.append((key, label, label))
        else:
            items.append((key, label, label))
    items.sort(key=lambda t: t[1].lower())
    unnamed.sort(key=lambda t: t[1].lower())
    items.extend(unnamed)
    result = items or [('NONE', '(no outfits found — check CSV path)', '')]
    cached["key"] = cache_key
    cached["items"] = result
    return result


# Module-level weapon EnumProperty callbacks (must not be nested in register()).
def _weapon_enum_items(self, context):
    from . import weapon_catalog as _wcat
    return _wcat.weapon_enum_items(self, context)


def _mod_items_muzzle(self, context):
    from . import weapon_catalog as _wcat
    return _wcat.mod_enum_items("Muzzle")


def _mod_items_stock(self, context):
    from . import weapon_catalog as _wcat
    return _wcat.mod_enum_items("Stock")


def _mod_items_magazine(self, context):
    from . import weapon_catalog as _wcat
    return _wcat.mod_enum_items("Magazine")


def _mod_items_underbarrel(self, context):
    from . import weapon_catalog as _wcat
    return _wcat.mod_enum_items("UnderBarrel")


def _mod_items_tech(self, context):
    from . import weapon_catalog as _wcat
    return _wcat.mod_enum_items("Tech")


def _pattern_enum_items(self, context):
    from . import weapon_catalog as _wcat
    return _wcat.pattern_enum_items(self, context)


_ANIM_KIND_ITEMS = (
    ("ALL", "All kinds", "AnimSequence, AnimMontage, and AnimComposite"),
    ("AnimSequence", "Sequences", "UAnimSequence only"),
    ("AnimMontage", "Montages", "UAnimMontage only"),
    ("AnimComposite", "Composites", "UAnimComposite only"),
)


# ---------------------------------------------------------------------------
# Property Groups
# ---------------------------------------------------------------------------

class ArcOutfitsPSKEntry(PropertyGroup):
    psk_path: StringProperty(name="PSK Path", subtype='FILE_PATH')
    skin_choice: EnumProperty(
        name="Skin",
        description="Select a skin from the Skins folder next to this PSK",
        items=make_skin_items,
    )
    body_choice: EnumProperty(
        name="Body Skin",
        description="Select body skin variant",
        items=make_body_items,
    )
    hair_mi: StringProperty(
        name="Hair MI Path",
        description="Path to selected hair MI JSON",
        default="",
    )
    hair_mi_options: StringProperty(
        name="Hair MI Options",
        description="Pipe-separated list of name|path pairs",
        default="",
    )
    display_name: StringProperty(name="Display Name")
    manual_skins_folder: StringProperty(
        name="Manual Skins Folder",
        description="Override folder to scan for skins when auto-detection finds none",
        default="",
        subtype='DIR_PATH',
    )

class ArcOutfitsSelection(PropertyGroup):
    """One selectable outfit colourway for batch import."""
    preset_name: StringProperty(name="Preset")
    json_path: StringProperty(name="JSON Path")
    selected: BoolProperty(name="Import", default=False)

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    ArcOutfitsPSKEntry,
    ArcOutfitsSelection,
)


def _safe_register_class(cls):
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
    "arc_psk_entries",
    "arc_outfit_selections",
    "arc_pioneer_root",
    "arc_fmdex_root",
    "arc_ns_json_path",
    "arc_ns_asset_root",
    "arc_ns_bake_curves",
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
    "arc_last_weapon_armature",
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
    "arc_lighting_hdri_strength",
    "arc_lighting_hdri_rotation_z",
    "arc_lighting_hdri_flip_x",
    "arc_lighting_apply_hdri",
    "arc_lighting_apply_lut",
    "arc_lighting_look_applied",
    "arc_lighting_map",
    "arc_lighting_scenario",
    "arc_lighting_apply_bloom",
    "arc_lighting_apply_fog",
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
    "arc_anim_search",
    "arc_selected_anim",
    "arc_anim_kind_filter",
    "arc_anim_spawn_notifies",
    "arc_anim_replace_action",
    "arc_animation_cache",
    "arc_lighting_look",
    "arc_lighting_map",
    "arc_lighting_scenario",
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


def ensure_scene_properties() -> bool:
    """Re-register Scene props if another add-on line wiped them. Returns True if ready.

    Must stay cheap: the N-panel calls this on every redraw. Do not re-assign RNA
    properties here — that fires update callbacks and can tag_redraw VIEW_3D in a loop.
    """
    missing = [n for n in _UI_CRITICAL_SCENE_PROPS if not hasattr(bpy.types.Scene, n)]
    if missing:
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
    return True


def _register_mask_debug_color_n():
    bpy.types.Scene.arc_mask_debug_color_n = IntProperty(
        name="Debug Color N",
        description=(
            "OCM Material ID band (1–8) for ColorN Highlight. "
            "Updates live when Mask Debug Mode 1 is already applied"
        ),
        default=6,
        min=1,
        max=8,
        update=_on_mask_debug_color_n,
    )


def register():
    for cls in classes:
        _safe_register_class(cls)

    bpy.types.Scene.arc_psk_entries = CollectionProperty(type=ArcOutfitsPSKEntry)
    bpy.types.Scene.arc_outfit_selections = CollectionProperty(type=ArcOutfitsSelection)
    bpy.types.Scene.arc_pioneer_root = StringProperty(
        name='PioneerGame Root',
        description=(
            "Content search root for textures, MI JSONs, and outfits. Prefer the full "
            "FModel output folder (parent of PioneerGame/ + MapPlacements/), not a "
            "MapPlacements/{Map} mesh-only tree — those usually have .uemodel without "
            "MI/SM JSON. Map meshes still resolve via the placements manifest / "
            "arc_placement_mesh_root. Leave empty to auto-detect from the manifest."
        ),
        default="",
        subtype='DIR_PATH',
    )
    bpy.types.Scene.arc_ns_json_path = StringProperty(
        name='NS JSON',
        description=(
            "Exported cooked Niagara system (NS_*.json). Must come from a property "
            "export that resolves Niagara data-interface classes from mappings — "
            "FModel's own export silently drops the curve tables. Dump with "
            "CUE4Parse.Example niagara-ns-dump"
        ),
        default="",
        subtype='FILE_PATH',
    )
    bpy.types.Scene.arc_ns_asset_root = StringProperty(
        name='NS Asset Root',
        description=(
            "Export root searched for the renderer's .pskx/.psk meshes. "
            "Leave empty to build placeholder geometry instead"
        ),
        default="",
        subtype='DIR_PATH',
    )
    bpy.types.Scene.arc_ns_bake_curves = BoolProperty(
        name='Bake Curve LUTs',
        description=(
            "Bake each curve data interface's cooked ShaderLUT as an F-curve on a "
            "custom property of the emitter empty. These are real sampled values, "
            "not invented motion"
        ),
        default=True,
    )
    bpy.types.Scene.arc_fmdex_root = StringProperty(
        name='FMDex Folder (FModel)',
        description=(
            "Folder containing FModel *_FMDex.json.br indexes "
            "(usually {FModel install}/FMDex/{Profile}), not the FMDex source code"
        ),
        default="",
        subtype='DIR_PATH',
    )
    bpy.types.Scene.arc_manual_outfit_folder = StringProperty(
        name='Manual DA_OI_Outfit Folder',
        description="Override folder to scan for outfit colourways when auto-detection finds none",
        default="", subtype='DIR_PATH',
    )
    bpy.types.Scene.arc_outfit_preset = EnumProperty(
        name="Outfit Preset",
        description="Select a full named outfit colourway to auto-fill per-part skins below",
        items=make_outfit_preset_items,
    )
    bpy.types.Scene.arc_outfit_csv_path = StringProperty(
        name="Outfit Reference CSV",
        description="Override CSV for the Outfit Selector",
        default="", subtype='FILE_PATH',
    )
    bpy.types.Scene.arc_selected_outfit = StringProperty(
        name="Selected Outfit Key",
        description="Key of the currently selected outfit for import",
        default="",
    )
    bpy.types.Scene.arc_selected_outfit_browse = EnumProperty(
        name="Browse Outfits",
        description="Browse all outfits — selecting one also selects it above",
        items=make_outfit_selector_items,
        update=_on_browse_outfit_changed,
    )
    bpy.types.Scene.arc_outfit_search = StringProperty(
        name="Search Outfits",
        description="Filter the outfit list",
        default="",
    )
    # FModel bridge (outfits + map-importer): listener for import_models / import_outfit
    bpy.types.Scene.arc_placement_listen_port = IntProperty(
        name="FModel Listen Port",
        description="TCP port for FModel model and placement commands (default 28563; SurfBlender uses 28562)",
        default=28563,
        min=1024,
        max=65535,
    )
    bpy.types.Scene.arc_auto_listen = BoolProperty(
        name="Auto-start FModel Listener",
        description="Start the local FModel receiver when this add-on registers",
        default=True,
    )

    # Map Placement / GroundPlane / shore — map-importer line only
    from . import addon_line as _addon_line

    if _addon_line.is_map_importer_line():
        from . import map_placement as _map_placement

        bpy.types.Scene.arc_placement_workspace = StringProperty(
            name="Placement Workspace",
            description=(
                "Central folder for all map placement outputs "
                "({Workspace}/{MapName}/ — CSVs, world_bounds, overlay PNG). "
                "Default: this addon's MapPlacement/ folder"
            ),
            default="",
            subtype='DIR_PATH',
        )
        bpy.types.Scene.arc_placement_map = EnumProperty(
            name="Map",
            description="Maps detected under Pioneer/Maps (and already extracted in the workspace)",
            items=_map_placement.make_map_enum_items,
            update=_map_placement._on_placement_map_changed,
        )
        bpy.types.Scene.arc_placement_csv = StringProperty(
            name="Placement CSV",
            description="Auto-filled from workspace after Extract, or pick manually",
            default="",
            subtype='FILE_PATH',
        )
        bpy.types.Scene.arc_placement_world_bounds_json = StringProperty(
            name="World Bounds JSON",
            description="Heightmap world_bounds JSON for plane sizing / overlay alignment",
            default="",
            subtype='FILE_PATH',
        )
        bpy.types.Scene.arc_placement_heightmap_image = StringProperty(
            name="Heightmap Image",
            description="Optional heightmap / overlay PNG for the reference plane",
            default="",
            subtype='FILE_PATH',
        )
        bpy.types.Scene.arc_placement_ingame_map_image = StringProperty(
            name="In-Game Map Image",
            description=(
                "UI in-game map (T_InGameMap_*) — spatial masks for rock/sand/flats on "
                "CityGroundPlane. Auto-resolved from Pioneer Content when empty"
            ),
            default="",
            subtype='FILE_PATH',
        )
        bpy.types.Scene.arc_placement_hlod_color_image = StringProperty(
            name="HLOD Color Image",
            description=(
                "HLOD / landscape Color texture (T_*_Color_*) — albedo palette for ground. "
                "Single xN_yM tiles are sampled for cream/rock/pink tones (not full-map UVs). "
                "Auto-resolved from Content when empty"
            ),
            default="",
            subtype='FILE_PATH',
        )
        bpy.types.Scene.arc_placement_batch_size = IntProperty(
            name="Placement Batch Size",
            description="How many empties/meshes to create per timer tick (50–200 recommended)",
            default=100,
            min=1,
            max=2000,
        )
        bpy.types.Scene.arc_map_focus_instancer_material = BoolProperty(
            name="Focus Instancer Materials",
            description=(
                "When you select a Fast-import Geometry Nodes instancer, pin its SRC mesh "
                "material in Shader Editor node trees so you see the real node graph without "
                "hunting InstanceSources"
            ),
            default=True,
        )
        bpy.types.Scene.arc_bridge_advanced = BoolProperty(
            name="Advanced / Backup",
            description="Show map placement and bridge backup controls",
            default=False,
        )
        bpy.types.Scene.arc_placement_mesh_root = StringProperty(
            name="Map Mesh Export Folder",
            description=(
                "FModel MapPlacements/{MapName} folder containing auto-exported .uemodel files. "
                "Filled automatically when FModel pushes placements; override if needed"
            ),
            default="",
            subtype='DIR_PATH',
        )
        bpy.types.Scene.arc_map_unit_scale = FloatProperty(
            name="Map Unit Scale",
            description=(
                "Blender units per Unreal centimeter for map imports (default 0.01 → 1 BU = 1 m). "
                "CSV stays in cm; Scale Map to Meters tags existing scenes"
            ),
            default=_map_placement.MAP_UNIT_SCALE,
            min=0.0001,
            max=1.0,
        )
        bpy.types.Scene.arc_map_mirror_y = BoolProperty(
            name="Map Mirror Y",
            description=(
                "Negate Unreal Y on import so Blender top-down matches in-game maps "
                "(Buried City: tracks bottom-left). Fix Map Orientation applies this in-place"
            ),
            default=_map_placement.MAP_MIRROR_Y,
        )
        bpy.types.Scene.arc_water_shore_distance = FloatProperty(
            name="Water Shore Distance",
            description=(
                "Meters: how far from other meshes (heightmap / StaticMeshActors) Shore Color "
                "blends in. Used by Refresh Water Shore Proximity and the shader AO Distance"
            ),
            default=2.0,
            min=0.05,
            max=50.0,
            subtype='DISTANCE',
        )
        bpy.types.Scene.arc_water_shore_strength = FloatProperty(
            name="Water Shore Strength",
            description="Multiply shore proximity / AO factor before clamping (1 = normal)",
            default=1.0,
            min=0.0,
            max=4.0,
        )
        bpy.types.Scene.arc_water_shore_invert = BoolProperty(
            name="Invert Water Shore Mask",
            description="Invert the Water↔Shore mix factor (debug / artistic flip)",
            default=False,
        )
        bpy.types.Scene.arc_water_shore_target_collection = PointerProperty(
            name="Shore Target Collection",
            description=(
                "Optional: only measure shore proximity against meshes in this collection. "
                "Empty = heightmap + StaticMeshActors + other opaque map meshes "
                "(excludes water / glass / decals)"
            ),
            type=bpy.types.Collection,
        )

    # Blender-only experiment switch: FModel keeps one validated decal transform.
    from . import materials as _materials

    bpy.types.Scene.arc_decal_method = EnumProperty(
        name="Decal Placement",
        description=(
            "How decal UVs are placed. FModel (validated) matches the FModel viewer and "
            "is correct for clothing; the others exist to test accessories that may use "
            "a different convention. Re-run Update Materials after changing this"
        ),
        items=_materials.DECAL_PLACEMENT_METHODS,
        default=_materials.DECAL_PLACEMENT_DEFAULT,
    )
    bpy.types.Scene.arc_decal_excl_slot = EnumProperty(
        name="Decal Slot",
        description="Which decal LayerMask gate receives the Colour N exclude checkboxes",
        items=[
            ("0", "All Decals", "Apply excludes to every decal LayerMask on the material"),
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
    for _zi in range(1, 9):
        setattr(
            bpy.types.Scene,
            f"arc_decal_excl_c{_zi}",
            BoolProperty(
                name=f"Colour {_zi}",
                description=(
                    f"Manual override: also exclude stickers from Colour {_zi} "
                    f"(OCM zone {_zi - 1}) on top of cooked LayerMask Allow bits"
                ),
                default=False,
            ),
        )
    bpy.types.Scene.arc_palette_mode = EnumProperty(
        name="Palette Routing",
        description=(
            "ColorA/B/C vs ColorA2/B2/C2 for ColorMask_XYZ. Auto = measured zone map; "
            "Primary/Secondary force one triple; Swap inverts Auto. "
            "Click Set Selected to apply to selected meshes immediately"
        ),
        items=[
            ('AUTO', "Auto", "Default zone map (all-primary + measured overrides)"),
            ('PRIMARY', "Primary", "Force ColorA/B/C on all zones"),
            ('SECONDARY', "Secondary", "Force ColorA2/B2/C2 on all zones"),
            ('SWAP', "Swap", "Invert the Auto primary/secondary zone map"),
        ],
        default='AUTO',
    )
    # D022–D032 ground truth vs pre-D022 ColorMask_XYZ / D010 soft Mix.
    # Revert: set LEGACY then Update Materials (legacy path is fully retained).
    bpy.types.Scene.arc_outfit_color_pipeline = EnumProperty(
        name="Outfit Color Pipeline",
        description=(
            "How outfit colour is built. Legacy: ColorMask_XYZ G/B lerp of ColorA/B/C "
            "into ArcTexturer. Ground Truth: ColorMask.rgb × ColorMaskSwatch fold-from-white "
            "into Principled (rough/metal/decals/TA normals/edge-crease; no ArcTexturer). "
            "TA UVs use ArrayUvScale 25 plus per-layer Crease/Edge/Medium NormalTiling Value nodes. "
            "Re-run Update Materials after changing"
        ),
        items=[
            (
                'LEGACY',
                "Legacy",
                "Pre-D022: ColorMask_XYZ G/B assemble + D010 ColorSchemeBlend soft Mix",
            ),
            (
                'GROUND_TRUTH',
                "Ground Truth",
                "Cooked ColorMask×Swatch Colour N muxed by mid×8; Principled PBR "
                "(rough/metal/decals/extra normals/edge-crease). ArrayUvScale=25. No ArcTexturer.",
            ),
        ],
        default='LEGACY',
    )
    bpy.types.Scene.arc_color_benchmark_id = EnumProperty(
        name="Color Benchmark",
        description=(
            "Which curated body part(s) to import as flat inspection graphs "
            "(no ArcTexturer). See docs/OUTFIT_COLOR_BENCHMARK.md"
        ),
        items=[
            ('ALL', "All B1–B8", "Import every benchmark body part"),
            ('B1', "B1 Abyss Lower Cotton", "Abyss Lowerbody Cotton"),
            ('B2', "B2 Abyss YellowBlack", "Abyss Lowerbody Cotton_YellowBlack"),
            ('B3', "B3 Moonball Upper Poly", "Moonball Upperbody Polyester"),
            ('B4', "B4 Moonball Lower Cotton", "Moonball Lowerbody Cotton"),
            ('B5', "B5 Horns Leather_Black", "Horns Upperbody Leather_Black"),
            ('B6', "B6 Horns Leather", "Horns Upperbody Leather"),
            ('B7', "B7 Dweller Leather_Yellow", "Dweller Lowerbody Leather_Yellow"),
            ('B8', "B8 Batter Quilted_Blue", "Batter Upperbody Quilted_Blue"),
        ],
        default='ALL',
    )
    bpy.types.Scene.arc_crease_edge_color = BoolProperty(
        name="Crease/Edge Color",
        description=(
            "When on: wire N_Crease/EdgeColorOverlay into ArcTexturer Crease/Edge sockets. "
            "When off (default): leave those sockets unconnected, hide them on the Arc "
            "node, and mute EdgeCrease-Controller so crease/edge color cannot tint. "
            "Re-run Update Materials after changing"
        ),
        default=False,
    )
    # Internal: UI toggle removed; import always organizes nodes at end of material build.
    bpy.types.Scene.arc_organize_nodes_on_import = BoolProperty(
        name="Organize Nodes on Import",
        description=(
            "Legacy compatibility prop — ignored. Import always runs NCT / place_* / "
            "MI-parameter layout at the end of material build"
        ),
        default=True,
    )
    # Internal: UI toggle removed; import path hardcodes prefetch On via utils.io_prefetch_enabled.

    bpy.types.Scene.arc_io_prefetch = BoolProperty(
        name="IO Prefetch",
        description=(
            "Use a small ThreadPool to discover texture paths while meshes import "
            "(workers never touch bpy or full-read PNGs; MI colours always parse on "
            "the main thread). Not shown in UI — kept for compatibility / override"
        ),
        default=True,
    )

    # Mask Debug — Apply-only (no live update callbacks).
    bpy.types.Scene.arc_mask_debug_source = EnumProperty(
        name="Zone Source",
        description=(
            "How Color N is derived. Procedural uses a live ColorRamp (tunable band "
            "stops). Baked uses the import-time ZoneIndex PNG. Mismatch highlights "
            "disagreement between the two"
        ),
        items=_MASK_DEBUG_SOURCE_ITEMS,
        default="procedural",
    )
    bpy.types.Scene.arc_mask_debug_mode = EnumProperty(
        name="Mask Debug",
        description=(
            "Viewport mask preview injected into clothing materials (ColorABC stay live). "
            "Click Apply after changing settings"
        ),
        items=_MASK_DEBUG_MODE_ITEMS,
        default="0",
    )
    _register_mask_debug_color_n()
    bpy.types.Scene.arc_mask_debug_grid_scale = FloatProperty(
        name="Debug Grid Scale",
        description=(
            "Digit / checker density. Digits auto-fit the mesh (about 24 glyph rows "
            "across the model at 1.0, any unit scale); Mode 1 checker uses 50× on "
            "mesh UV. Higher = denser / smaller. When Mask Debug is already applied, "
            "changing only Grid Scale + Apply soft-updates Mapping nodes (no rebuild)"
        ),
        default=1.0,
        min=0.5,
        soft_max=10.0,
        max=10.0,
    )
    bpy.types.Scene.arc_mask_debug_overlay_opacity = FloatProperty(
        name="Overlay Opacity",
        description="Mode overlay mix factor (highlight / CurvatureID / false colour). Click Apply",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    bpy.types.Scene.arc_mask_debug_opacity = FloatProperty(
        name="Overlay Opacity (legacy)",
        description="Deprecated alias of Overlay Opacity",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    bpy.types.Scene.arc_mask_debug_colormask_opacity = FloatProperty(
        name="ColorMask Opacity",
        description="ColorMask layer mix factor (above mode overlay). Click Apply",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    bpy.types.Scene.arc_mask_debug_numbers_opacity = FloatProperty(
        name="Numbers Opacity",
        description="Zone digit overlay opacity. Click Apply",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    bpy.types.Scene.arc_mask_debug_show_colormask = BoolProperty(
        name="Show ColorMask",
        description="Composite ColorMask above the mode overlay (Apply to enable)",
        default=False,
    )
    bpy.types.Scene.arc_mask_debug_show_numbers = BoolProperty(
        name="Show Numbers",
        description="Overlay Color N zone digits (Apply to enable / disable)",
        default=True,
    )

    # Weapons browser (ST hard catalog). Callbacks are module-level so Blender
    # RNA keeps a stable reference (nested defs can be GC'd → mojibake labels).
    bpy.types.Scene.arc_weapon_key = EnumProperty(
        name="Weapon",
        description="Firearm / launcher from ST_ItemNames (hard catalog)",
        items=_weapon_enum_items,
    )
    bpy.types.Scene.arc_weapon_mod_muzzle = EnumProperty(
        name="Muzzle",
        description="Muzzle mod from ST catalog",
        items=_mod_items_muzzle,
    )
    bpy.types.Scene.arc_weapon_mod_stock = EnumProperty(
        name="Stock",
        description="Stock mod from ST catalog",
        items=_mod_items_stock,
    )
    bpy.types.Scene.arc_weapon_mod_magazine = EnumProperty(
        name="Magazine",
        description="Magazine mod from ST catalog",
        items=_mod_items_magazine,
    )
    bpy.types.Scene.arc_weapon_mod_underbarrel = EnumProperty(
        name="UnderBarrel",
        description="Under-barrel mod from ST catalog",
        items=_mod_items_underbarrel,
    )
    bpy.types.Scene.arc_weapon_mod_tech = EnumProperty(
        name="Tech",
        description="Tech mod from ST catalog",
        items=_mod_items_tech,
    )
    bpy.types.Scene.arc_weapon_pattern = EnumProperty(
        name="Weapon Pattern",
        description="Paintjob pattern PNG under MaterialLibrary/Textures/Weapons/Patterns",
        items=_pattern_enum_items,
    )
    bpy.types.Scene.arc_last_weapon_armature = StringProperty(
        name="Last Weapon Armature",
        description="Name of the most recently imported weapon armature (mod snap fallback)",
        default="",
    )

    bpy.types.Scene.arc_anim_search = StringProperty(
        name="Search Animations",
        description="Filter the animation catalog by name or folder (does not import)",
        default="",
    )
    bpy.types.Scene.arc_selected_anim = StringProperty(
        name="Selected Animation",
        description="Catalog key of the animation that Apply will import onto the armature",
        default="",
    )
    bpy.types.Scene.arc_anim_kind_filter = EnumProperty(
        name="Animation Kind",
        description="Restrict the picker to sequences, montages, or composites",
        items=_ANIM_KIND_ITEMS,
        default="ALL",
    )
    bpy.types.Scene.arc_anim_spawn_notifies = BoolProperty(
        name="Spawn Notify Props",
        description="When applying, attach mesh props and FX markers from animation notifies",
        default=True,
    )
    bpy.types.Scene.arc_anim_replace_action = BoolProperty(
        name="Replace Previous Action",
        description="Drop the last applied animation Action instead of accumulating Actions in the blend",
        default=True,
    )
    bpy.types.Scene.arc_anim_spawn_other_notifies = BoolProperty(
        name="Spawn Other Notifies",
        description="Also spawn empties for sound/trail/misc notifies (not just mesh props and Niagara)",
        default=False,
    )
    bpy.types.Scene.arc_animation_cache = StringProperty(
        name="Animation Cache",
        description=(
            "PioneerGame / FModel export root — used to find .psa files. "
            "Leave empty to use PioneerGame Root. Names also come from FMDex "
            "(Animation folders and AS_/Anim_ stems), not only this folder."
        ),
        default="",
        subtype="DIR_PATH",
    )
    bpy.types.Scene.arc_last_applied_anim = StringProperty(
        name="Last Applied Animation",
        default="",
    )
    bpy.types.Scene.arc_last_applied_anim_armature = StringProperty(
        name="Last Animation Armature",
        default="",
    )

    # Lighting Look (Kodak LUT + HDRI from Pioneer/Lighting)
    from . import lighting_looks as _lighting_looks

    bpy.types.Scene.arc_lighting_look = EnumProperty(
        name="Lighting Look",
        description=(
            "Kodak5218 RGBTable16x1 looks under Pioneer/Lighting/LUTs "
            "(paired HDRI from assets/lighting_looks.json)"
        ),
        items=_lighting_looks.make_lighting_look_items,
    )
    bpy.types.Scene.arc_lighting_lut_strength = FloatProperty(
        name="LUT Strength",
        description="Mix factor for the compositor Kodak LUT (0 = bypass, 1 = full)",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    bpy.types.Scene.arc_lighting_hdri_strength = FloatProperty(
        name="HDRI Strength",
        description="World Environment strength multiplier (combined with look exposure_ev)",
        default=1.0,
        min=0.0,
        soft_max=8.0,
        max=64.0,
    )
    bpy.types.Scene.arc_lighting_hdri_rotation_z = FloatProperty(
        name="HDRI Rotation Z",
        description=(
            "World Mapping Z rotation in degrees — UE SourceCubemapAngle "
            "(Pioneer default ~227.5°; studio equirect may need other values)"
        ),
        default=227.52,
        min=-360.0,
        max=360.0,
        soft_min=0.0,
        soft_max=360.0,
    )
    bpy.types.Scene.arc_lighting_hdri_flip_x = BoolProperty(
        name="HDRI Flip X",
        description="Mirror HDRI on X via Mapping Scale (fixes left/right vs UE)",
        default=False,
    )
    bpy.types.Scene.arc_lighting_apply_lut = BoolProperty(
        name="Apply LUT",
        description="Enable compositor Kodak strip LUT when applying a Lighting Look",
        default=True,
    )
    bpy.types.Scene.arc_lighting_apply_hdri = BoolProperty(
        name="Apply HDRI",
        description="Load paired World HDRI when applying a Lighting Look",
        default=True,
    )
    bpy.types.Scene.arc_lighting_look_applied = StringProperty(
        name="Last Lighting Look",
        description="Status of the last Apply Lighting Look",
        default="",
    )

    from . import lighting_atmosphere as _lighting_atmosphere

    bpy.types.Scene.arc_lighting_map = EnumProperty(
        name="Lighting Map",
        description="Map whose DA_*_LightingScenarios (or Home_Lighting_*) drive atmosphere",
        items=_lighting_atmosphere.make_lighting_map_items,
    )
    bpy.types.Scene.arc_lighting_scenario = EnumProperty(
        name="Lighting Scenario",
        description="Gameplay Lighting.* tag for this map (HeightFog / PostProcess source)",
        items=_lighting_atmosphere.make_lighting_scenario_items,
    )
    bpy.types.Scene.arc_lighting_apply_bloom = BoolProperty(
        name="Apply Bloom",
        description="Compositor Fog Glow bloom from PostProcess BloomIntensity/Threshold",
        default=True,
    )
    bpy.types.Scene.arc_lighting_apply_fog = BoolProperty(
        name="Apply Volume Fog",
        description="World Principled Volume from ExponentialHeightFog (not compositor mist)",
        default=True,
    )

    # Migrate legacy mode ints: old FalseColor becomes Mode 3.
    try:
        for scene in bpy.data.scenes:
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



def unregister():
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