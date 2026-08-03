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
# Enum callbacks (must be defined before PropertyGroup classes that reference them)
# ---------------------------------------------------------------------------

def make_skin_items(self, context):
    """Dynamic EnumProperty items callback for skin dropdown."""
    if not context:
        return [('NONE', '— Default —', '')]
    for entry in context.scene.arc_psk_entries:
        if entry == self:
            skins = scan_skins(entry.psk_path, entry.manual_skins_folder)
            skins_folder = entry.manual_skins_folder if entry.manual_skins_folder else get_skins_folder(entry.psk_path)
            all_dirs = get_all_skin_dirs(skins_folder) if skins_folder else []
            default_dir = get_default_skin_dir(all_dirs) if all_dirs else ""
            
            default_label_name = default_dir
            if not default_label_name:
                for skin_name, _json_path in skins:
                    if skin_name == "__DEFAULT__":
                        default_label_name = os.path.splitext(os.path.basename(_json_path))[0]
                        break
            
            default_label = f"{default_label_name} (Default)" if default_label_name else "— Default —"
            items = [('NONE', default_label, f'Import using the default skin ({default_label_name})' if default_label_name else 'Import without a skin')]
            for skin_name, json_path in skins:
                if skin_name == default_dir or skin_name == "__DEFAULT__":
                    continue
                items.append((json_path, skin_name, json_path))
            return items
    return [('NONE', '— Default —', '')]

def make_outfit_preset_items(self, context):
    """Dynamic EnumProperty items callback for outfit preset dropdown."""
    if not context:
        return [('NONE', '— Select Outfit —', '')]
    from .importing import queue_supports_outfit_batch
    if not queue_supports_outfit_batch(context):
        return [('NONE', '— Select Outfit —', '')]
    manual_folder = getattr(context.scene, 'arc_manual_outfit_folder', '')
    entries = context.scene.arc_psk_entries
    character_name = ""
    for entry in entries:
        character_name = get_character_name(bpy.path.abspath(entry.psk_path))
        if character_name:
            break
    if not character_name and not manual_folder:
        return [('NONE', '— Select Outfit —', '')]
    presets = scan_outfit_presets(character_name, manual_folder)
    items = [('NONE', '— Select Outfit —', 'No outfit preset applied')]
    for preset_name, json_path in presets:
        items.append((json_path, preset_name, json_path))
    return items

def _on_browse_outfit_changed(self, context):
    if self.arc_selected_outfit_browse and self.arc_selected_outfit_browse != 'NONE':
        self.arc_selected_outfit = self.arc_selected_outfit_browse

def make_outfit_selector_items(self, context):
    """Dynamic EnumProperty items callback for outfit selector."""
    from .importing import load_outfit_csv, get_outfit_csv_path, outfit_row_key, outfit_display_label
    
    rows = load_outfit_csv(get_outfit_csv_path(context))
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
    return items or [('NONE', '(no outfits found — check CSV path)', '')]

# ---------------------------------------------------------------------------
# Property Groups
# ---------------------------------------------------------------------------

class ArcPSKEntry(PropertyGroup):
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

class ArcOutfitSelection(PropertyGroup):
    """One selectable outfit colourway for batch import."""
    preset_name: StringProperty(name="Preset")
    json_path: StringProperty(name="JSON Path")
    selected: BoolProperty(name="Import", default=False)

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    ArcPSKEntry,
    ArcOutfitSelection,
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


def register():
    for cls in classes:
        _safe_register_class(cls)

    bpy.types.Scene.arc_psk_entries = CollectionProperty(type=ArcPSKEntry)
    bpy.types.Scene.arc_outfit_selections = CollectionProperty(type=ArcOutfitSelection)
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
    # Map Placement (separate from outfit colourway paths)
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
    bpy.types.Scene.arc_palette_mode = EnumProperty(
        name="Palette Routing",
        description=(
            "Default ColorA/B/C vs ColorA2/B2/C2 routing when a material has no override. "
            "Auto uses the documented zone map; Primary/Secondary force one triple; "
            "Swap inverts the Auto map. Per-material overrides win. Re-run Update Materials"
        ),
        items=[
            ('AUTO', "Auto", "Default zone map (Goalie-correct; ambiguous when both triples differ)"),
            ('PRIMARY', "Primary", "Force ColorA/B/C on all zones"),
            ('SECONDARY', "Secondary", "Force ColorA2/B2/C2 on all zones"),
            ('SWAP', "Swap", "Invert the Auto primary/secondary zone map"),
        ],
        default='AUTO',
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

def unregister():
    for name in (
        "arc_psk_entries",
        "arc_outfit_selections",
        "arc_pioneer_root",
        "arc_fmdex_root",
        "arc_manual_outfit_folder",
        "arc_outfit_preset",
        "arc_outfit_csv_path",
        "arc_selected_outfit",
        "arc_selected_outfit_browse",
        "arc_outfit_search",
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
        "arc_placement_listen_port",
        "arc_auto_listen",
        "arc_bridge_advanced",
        "arc_placement_mesh_root",
        "arc_map_unit_scale",
        "arc_map_mirror_y",
        "arc_decal_method",
        "arc_palette_mode",
        "arc_water_shore_distance",
        "arc_water_shore_strength",
        "arc_water_shore_invert",
        "arc_water_shore_target_collection",
    ):
        _safe_del_scene_prop(name)

    for cls in reversed(classes):
        _safe_unregister_class(cls)