"""
PSK import and outfit scanning logic for the Arc Raiders Importer
"""

import os
import re
import json
import csv
import bpy
import mathutils
from . import utils
from . import textures

_OUTFIT_CSV_CACHE = {}

# ---------------------------------------------------------------------------
# PSK Import
# ---------------------------------------------------------------------------

def import_psk(filepath: str) -> list:
    before = set(bpy.data.objects.keys())
    try:
        result = bpy.ops.psk.import_file(filepath=filepath)
    except AttributeError:
        raise RuntimeError(
            "The 'io_scene_psk_psa' extension is not installed or enabled."
        )
    if 'FINISHED' not in result:
        raise RuntimeError(f"PSK import operator returned: {result}")
    after = set(bpy.data.objects.keys())
    return [bpy.data.objects[k] for k in after - before]

# ---------------------------------------------------------------------------
# Outfit CSV handling
# ---------------------------------------------------------------------------

def default_outfit_csv_path() -> str:
    addon_dir = os.path.dirname(__file__)
    return os.path.join(addon_dir, "outfit_reference.csv")

def get_outfit_csv_path(context) -> str:
    custom = getattr(context.scene, "arc_outfit_csv_path", "")
    if custom:
        abspath = bpy.path.abspath(custom)
        if os.path.isfile(abspath):
            return abspath
    default = default_outfit_csv_path()
    return default if os.path.isfile(default) else ""

def load_outfit_csv(csv_path: str) -> list:
    if not csv_path or not os.path.isfile(csv_path):
        return []
    try:
        mtime = os.path.getmtime(csv_path)
    except OSError:
        return []
    cached = _OUTFIT_CSV_CACHE.get(csv_path)
    if cached and cached[0] == mtime:
        return cached[1]
    rows = []
    try:
        with open(csv_path, "r", newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    except OSError:
        pass
    _OUTFIT_CSV_CACHE[csv_path] = (mtime, rows)
    return rows

def outfit_row_key(row: dict) -> str:
    return row.get("ST") or row.get("Item/UI Folder Name") or row.get("Flavour") or "?"

def outfit_display_label(row: dict) -> str:
    flavour = row.get("Flavour") or row.get("ST") or "(unnamed)"
    st = row.get("ST", "")
    st_suffix = st[len("ID_PLAYERSKIN_"):].title() if st.upper().startswith("ID_PLAYERSKIN_") else ""
    folder = (row.get("Item/UI Folder Name") or row.get("Model Folder Name") or "").replace("; ", "/")
    paren = "/".join(p for p in (st_suffix, folder) if p)
    return f"{flavour}({paren})" if paren else flavour

def find_outfit_row(rows: list, key: str) -> dict:
    for row in rows:
        if outfit_row_key(row) == key:
            return row
    return None

# ---------------------------------------------------------------------------
# Outfit scanning
# ---------------------------------------------------------------------------

def get_outfit_folder(character_name: str) -> str:
    root = utils.get_pioneer_root()
    if not root or not character_name:
        return ""
    direct = utils.find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit", character_name])
    if direct:
        return direct
    char_map = build_outfit_character_map(root)
    return char_map.get(utils.normalize_folder_name(character_name), "")

def build_outfit_character_map(root: str) -> dict:
    if root in utils._OUTFIT_CHAR_MAP_CACHE:
        return utils._OUTFIT_CHAR_MAP_CACHE[root]
    result = {}
    outfit_root = utils.find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit"])
    if outfit_root and os.path.isdir(outfit_root):
        try:
            subfolders = sorted(d for d in os.listdir(outfit_root)
                                if os.path.isdir(os.path.join(outfit_root, d)))
        except OSError:
            subfolders = []
        for sub in subfolders:
            sub_path = os.path.join(outfit_root, sub)
            try:
                fnames = sorted(f for f in os.listdir(sub_path) if f.lower().endswith(".json"))
            except OSError:
                continue
            for fname in fnames:
                fpath = os.path.join(sub_path, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                except Exception:
                    continue
                entry = data[0] if isinstance(data, list) else data
                if entry.get("Type") != "CharacterVisualSkinOnlineItemDataAsset":
                    continue
                parts = entry.get("Properties", {}).get("Parts", [])
                for p in parts:
                    seg = character_from_asset_path(p.get("AssetPathName", ""))
                    if seg:
                        result.setdefault(utils.normalize_folder_name(seg), sub_path)
                break
    utils._OUTFIT_CHAR_MAP_CACHE[root] = result
    return result

def character_from_asset_path(asset_path: str) -> str:
    clean = asset_path.split(".")[0] if "." in asset_path.split("/")[-1] else asset_path
    parts = clean.strip("/").split("/")
    for i, part in enumerate(parts):
        if part.lower() == "assets" and i > 0 and parts[i - 1].lower() == "characters":
            if i + 1 < len(parts):
                return parts[i + 1]
    return ""

def scan_outfit_presets(character_name: str, manual_folder: str = "") -> list:
    if manual_folder and os.path.isdir(bpy.path.abspath(manual_folder)):
        return scan_outfit_presets_in_folder(bpy.path.abspath(manual_folder))
    folder = get_outfit_folder(character_name)
    if not folder or not os.path.isdir(folder):
        return []
    char_norm = utils.normalize_folder_name(character_name)
    return scan_outfit_presets_in_folder(folder, char_norm)

def scan_outfit_presets_in_folder(folder: str, char_norm: str = "") -> list:
    if not folder or not os.path.isdir(folder):
        return []
    pattern = re.compile(r'^DA_OI_Outfit_(.+?)_Color(_.*)?$', re.IGNORECASE)
    results = []
    try:
        for fname in sorted(os.listdir(folder)):
            if not fname.lower().endswith(".json"):
                continue
            if "persistence" in fname.lower():
                continue
            stem = os.path.splitext(fname)[0]
            m = pattern.match(stem)
            if not m:
                continue
            file_char_segment, suffix = m.group(1), m.group(2)
            if char_norm and utils.normalize_folder_name(file_char_segment) != char_norm:
                continue
            if not suffix or not suffix.startswith("_"):
                continue
            preset_name = suffix.lstrip("_")
            if preset_name:
                results.append((preset_name, os.path.join(folder, fname)))
    except OSError:
        pass
    return results

# ---------------------------------------------------------------------------
# Outfit preset application
# ---------------------------------------------------------------------------

def apply_outfit_preset(context, preset_json_path: str) -> int:
    preset_map = parse_outfit_preset(preset_json_path)
    if not preset_map:
        return 0
    updated = 0
    for entry in context.scene.arc_psk_entries:
        psk_path = bpy.path.abspath(entry.psk_path)
        part_key = get_part_key(psk_path)
        if not part_key:
            continue
        part_key_norm = utils.normalize_part_key(part_key)
        mi_stem = None
        for pk, stem in preset_map.items():
            if utils.normalize_part_key(pk) == part_key_norm:
                mi_stem = stem
                break
        if not mi_stem:
            continue
        skin_json = textures.find_skin_json_for_mi_stem(psk_path, mi_stem)
        if skin_json:
            default_json = textures.get_base_skin_json(psk_path, entry.manual_skins_folder)
            target_value = 'NONE'
            if not (default_json and os.path.normcase(os.path.normpath(skin_json)) == os.path.normcase(os.path.normpath(default_json))):
                target_value = skin_json
            try:
                entry.skin_choice = target_value
                updated += 1
            except TypeError as e:
                print(f"Arc Raiders PSK Importer: Could not set skin for '{part_key}': {e}")
    return updated

def parse_outfit_preset(json_path: str) -> dict:
    result = {}
    if not json_path or not os.path.isfile(json_path):
        return result
    _VALID_TYPES = {
        "CustomizationVisualPartSetMaterialModifier",
        "CustomizationVisualPartSetMaterialPropertyModifier",
    }
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for entry in data if isinstance(data, list) else [data]:
            if entry.get("Type") not in _VALID_TYPES:
                continue
            props = entry.get("Properties", {})
            mat_path = props.get("Material", {}).get("AssetPathName", "")
            part_path = props.get("Part", {}).get("AssetPathName", "")
            if not mat_path or not part_path:
                continue
            part_key = get_part_key_from_asset_path(part_path)
            if not part_key:
                continue
            mi_stem = mat_path.split("/")[-1].split(".")[0]
            if mi_stem:
                result[part_key] = mi_stem
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to parse outfit preset '{json_path}': {e}")
    return result

def get_part_key(psk_path: str) -> str:
    norm = bpy.path.abspath(psk_path).replace("\\", "/")
    parts = norm.split("/")
    for i, part in enumerate(parts):
        if part.lower() == "assets" and i > 0 and parts[i - 1].lower() == "characters":
            if i + 2 < len(parts):
                return f"{parts[i + 1]}/{parts[i + 2]}"
    return ""

def get_part_key_from_asset_path(asset_path: str) -> str:
    clean = asset_path.split(".")[0] if "." in asset_path.split("/")[-1] else asset_path
    parts = clean.strip("/").split("/")
    for i, part in enumerate(parts):
        if part.lower() == "assets" and i > 0 and parts[i - 1].lower() == "characters":
            if i + 2 < len(parts):
                return f"{parts[i + 1]}/{parts[i + 2]}"
    return ""

def collect_psks_for_outfit_row(root: str, item_ui_folders: list) -> list:
    seen_keys = set()
    psks = []
    for folder_name in item_ui_folders:
        for asset_path in read_oi_parts_asset_paths(root, folder_name):
            key = get_part_key_from_asset_path(asset_path)
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            character, part = key.split("/", 1)
            part_folder = utils.find_relative_dir(root, ["Characters", "Assets", character, part])
            if not part_folder:
                continue
            psk = find_psk_in_specific_folder(part_folder)
            if psk:
                psks.append(psk)
    return psks

def read_oi_parts_asset_paths(root: str, outfit_folder_name: str) -> list:
    outfit_root = utils.find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit"])
    if not outfit_root:
        return []
    folder_path = os.path.join(outfit_root, outfit_folder_name)
    if not os.path.isdir(folder_path):
        return []
    try:
        fnames = sorted(f for f in os.listdir(folder_path) if f.lower().endswith(".json"))
    except OSError:
        return []
    for fname in fnames:
        try:
            with open(os.path.join(folder_path, fname), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        entry = data[0] if isinstance(data, list) else data
        if entry.get("Type") != "CharacterVisualSkinOnlineItemDataAsset":
            continue
        return [p.get("AssetPathName", "") for p in entry.get("Properties", {}).get("Parts", [])]
    return []

def find_psk_in_specific_folder(folder: str):
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return None
    psks = sorted(
        os.path.join(folder, f) for f in entries
        if f.lower().endswith(".psk") or f.lower().endswith(".pskx")
    )
    if not psks:
        return None
    lod0 = [p for p in psks if "lod0" in p.lower()]
    return lod0[0] if lod0 else psks[0]

# ---------------------------------------------------------------------------
# Populate outfit selections
# ---------------------------------------------------------------------------

def populate_outfit_selections(context) -> int:
    sels = context.scene.arc_outfit_selections
    prior = {s.json_path: s.selected for s in sels}
    sels.clear()
    manual_folder = getattr(context.scene, 'arc_manual_outfit_folder', '')
    character_name = ""
    for entry in context.scene.arc_psk_entries:
        character_name = get_character_name(bpy.path.abspath(entry.psk_path))
        if character_name:
            break
    if not character_name and not manual_folder:
        return 0
    for preset_name, json_path in scan_outfit_presets(character_name, manual_folder):
        s = sels.add()
        s.preset_name = preset_name
        s.json_path = json_path
        s.selected = prior.get(json_path, False)
    return len(sels)

def get_character_name(psk_path: str) -> str:
    norm = bpy.path.abspath(psk_path).replace("\\", "/")
    parts = norm.split("/")
    for i, part in enumerate(parts):
        if part.lower() == "assets" and i > 0 and parts[i - 1].lower() == "characters":
            if i + 1 < len(parts):
                return parts[i + 1]
    return ""