"""
Property definitions for the Arc Raiders Importer
"""

import bpy
import os
from bpy.props import StringProperty, CollectionProperty, BoolProperty, EnumProperty, FloatProperty
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

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    bpy.types.Scene.arc_psk_entries = CollectionProperty(type=ArcPSKEntry)
    bpy.types.Scene.arc_outfit_selections = CollectionProperty(type=ArcOutfitSelection)
    bpy.types.Scene.arc_pioneer_root = StringProperty(name='PioneerGame Root', subtype='DIR_PATH')
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

def unregister():
    del bpy.types.Scene.arc_psk_entries
    del bpy.types.Scene.arc_outfit_selections
    del bpy.types.Scene.arc_pioneer_root
    del bpy.types.Scene.arc_manual_outfit_folder
    del bpy.types.Scene.arc_outfit_preset
    del bpy.types.Scene.arc_outfit_csv_path
    del bpy.types.Scene.arc_selected_outfit
    del bpy.types.Scene.arc_selected_outfit_browse
    del bpy.types.Scene.arc_outfit_search
    
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)