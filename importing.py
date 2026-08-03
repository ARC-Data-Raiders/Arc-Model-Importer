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
    objs = [bpy.data.objects[k] for k in after - before]
    # PSK names UV1 as EXTRAUV0; materials (GraphicAtlas Use UV1) expect UV1.
    for obj in objs:
        utils.normalize_object_ue_uv_layers(obj)
    return objs

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
    import re
    flavour = row.get("Flavour") or row.get("ST") or "(unnamed)"

    st = row.get("ST", "")
    st_suffix = ""
    if st.upper().startswith("ID_PLAYERSKIN_"):
        st_suffix = st[len("ID_PLAYERSKIN_"):]
    st_suffix = re.sub(r'[^a-zA-Z0-9]', '', st_suffix).title()

    model_folder = (row.get("Model Folder Name") or "").strip().split(";")[0].strip()
    ui_folder = (row.get("Item/UI Folder Name") or "").strip().split(";")[0].strip()

    seen = {flavour.lower().replace("_", "")}
    parts = []

    def add_part(name):
        key = name.lower().replace("_", "")
        if key and key not in seen:
            seen.add(key)
            parts.append(name)

    if model_folder and ui_folder:
        if model_folder.lower() == ui_folder.lower():
            add_part(model_folder)
        else:
            add_part(model_folder)
            add_part(ui_folder)
    elif model_folder:
        add_part(model_folder)
    elif ui_folder:
        add_part(ui_folder)

    add_part(st_suffix)

    if not parts:
        return flavour

    return f"{flavour}({'/'.join(parts)})"

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
    csv_folder = _outfit_folder_from_csv(character_name)
    if csv_folder:
        outfit_root = utils.find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit"])
        if outfit_root:
            candidate = os.path.join(outfit_root, csv_folder)
            if os.path.isdir(candidate):
                return candidate
    char_map = build_outfit_character_map(root)
    return char_map.get(utils.normalize_folder_name(character_name), "")

def _outfit_folder_from_csv(character_name: str) -> str:
    try:
        from .properties import _csv_model_to_ui_folder, rebuild_csv_outfit_map
    except ImportError:
        return ""
    if not _csv_model_to_ui_folder:
        try:
            rebuild_csv_outfit_map()
        except Exception:
            return ""
    return _csv_model_to_ui_folder.get(utils.normalize_folder_name(character_name), "")

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
                entry = utils.first_ue_export(data, "CharacterVisualSkinOnlineItemDataAsset")
                if not entry:
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

# Backpack mesh folder → Items/Characters/Skins/<slot> mapping
_BACKPACK_SLOT_MAP = {
    "containers": "BackpackContainer",
    "attachments": "BackpackAttachment",
    "charms": "BackpackCharm",
    "frames": "BackpackFrame",
    "straps": "BackpackAttachment",
}

# Cosmetic DA_OI colourways: DA_OI_Outfit_X_Color_Y OR DA_OI_BackpackContainer_X_Color_Y
_COLORWAY_RE = re.compile(
    r'^DA_OI_(?:Outfit_)?(.+)_Color(?:_(.+))?$',
    re.IGNORECASE,
)


def scan_outfit_presets(character_name: str, manual_folder: str = "") -> list:
    if manual_folder and os.path.isdir(bpy.path.abspath(manual_folder)):
        return scan_outfit_presets_in_folder(bpy.path.abspath(manual_folder))
    folder = get_outfit_folder(character_name)
    if not folder or not os.path.isdir(folder):
        return []
    char_norm = utils.normalize_folder_name(character_name)
    return scan_outfit_presets_in_folder(folder, char_norm)

def scan_outfit_presets_in_folder(folder: str, char_norm: str = "") -> list:
    """Scan DA_OI_*_Color_* colourway JSONs (outfits + backpack/cosmetic slots)."""
    if not folder or not os.path.isdir(folder):
        return []
    results = []
    try:
        for fname in sorted(os.listdir(folder)):
            if not fname.lower().endswith(".json"):
                continue
            if "persistence" in fname.lower():
                continue
            stem = os.path.splitext(fname)[0]
            m = _COLORWAY_RE.match(stem)
            if not m:
                continue
            item_segment, preset_suffix = m.group(1), m.group(2)
            # Outfit mode: optionally require the character segment to match
            if char_norm:
                # DA_OI_Outfit_Beekeeper_Color_Blue → item_segment == Beekeeper
                # DA_OI_BackpackContainer_TechBag_Color_Green → skip char filter
                # (cosmetic folders are already item-scoped)
                if "outfit" in stem.lower() or stem.lower().startswith("da_oi_outfit"):
                    # With (?:Outfit_)? stripped, item_segment is the character name for outfits
                    if utils.normalize_folder_name(item_segment) != char_norm:
                        continue
            if preset_suffix:
                preset_name = preset_suffix
            else:
                # DA_OI_…_Color.json (no variant suffix) → Default
                preset_name = "Default"
            if preset_name:
                json_path = os.path.join(folder, fname)
                # Skip DA_OI colourways that don't carry a material modifier
                # (e.g. slot-only Color.json or incomplete Grey dumps).
                if not _mi_stems_from_colourway_json(json_path):
                    continue
                results.append((preset_name, json_path))
    except OSError:
        pass
    return results


def find_cosmetic_colourway_folder(psk_path: str) -> str:
    """Map a cosmetic mesh (backpack etc.) to its Items/Characters/Skins/<Slot>/<Item> folder."""
    if not psk_path:
        return ""
    root = utils.get_pioneer_root()
    if not root:
        return ""
    skins_root = utils.find_relative_dir(root, ["Items", "Characters", "Skins"])
    if not skins_root or not os.path.isdir(skins_root):
        return ""

    norm = bpy.path.abspath(psk_path).replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    parts_l = [p.lower() for p in parts]

    # Characters/Backpacks/<Containers|Attachments|…>/<Item>/…
    if "backpacks" in parts_l:
        bi = parts_l.index("backpacks")
        if bi + 2 < len(parts):
            slot_folder = parts[bi + 1]
            item_name = parts[bi + 2]
            skin_slot = _BACKPACK_SLOT_MAP.get(slot_folder.lower(), "")
            if skin_slot:
                candidate = os.path.join(skins_root, skin_slot, item_name)
                if os.path.isdir(candidate):
                    return candidate
                # Fuzzy item match under the skin slot
                fuzzy = _fuzzy_skin_item_dir(os.path.join(skins_root, skin_slot), item_name)
                if fuzzy:
                    return fuzzy

    # Characters/…/RaiderTools or Items path leftovers — try common cosmetic slots by mesh leaf
    mesh_dir = os.path.basename(os.path.dirname(norm))
    parent_dir = os.path.basename(os.path.dirname(os.path.dirname(norm)))
    for slot in (
        "BackpackContainer", "BackpackAttachment", "BackpackCharm",
        "BackpackFrame", "RaiderTool",
    ):
        for name in (mesh_dir, parent_dir):
            if not name:
                continue
            candidate = os.path.join(skins_root, slot, name)
            if os.path.isdir(candidate) and scan_outfit_presets_in_folder(candidate):
                return candidate
            fuzzy = _fuzzy_skin_item_dir(os.path.join(skins_root, slot), name)
            if fuzzy and scan_outfit_presets_in_folder(fuzzy):
                return fuzzy
    return ""


def _fuzzy_skin_item_dir(slot_dir: str, item_name: str) -> str:
    if not slot_dir or not os.path.isdir(slot_dir) or not item_name:
        return ""
    want = utils.normalize_folder_name(item_name)
    try:
        for entry in os.listdir(slot_dir):
            full = os.path.join(slot_dir, entry)
            if os.path.isdir(full) and utils.normalize_folder_name(entry) == want:
                return full
    except OSError:
        pass
    return ""

# ---------------------------------------------------------------------------
# Outfit preset application
# ---------------------------------------------------------------------------

def apply_outfit_preset(context, preset_json_path: str) -> int:
    preset_map = parse_outfit_preset(preset_json_path)
    mi_stems_from_preset = list(preset_map.values()) if preset_map else []
    # Cosmetics often fail Assets-style part keys — also collect bare MI stems from the JSON
    if not mi_stems_from_preset:
        mi_stems_from_preset = _mi_stems_from_colourway_json(preset_json_path)

    updated = 0
    for entry in context.scene.arc_psk_entries:
        psk_path = bpy.path.abspath(entry.psk_path)
        mi_stem = None

        part_key = get_part_key(psk_path) or get_characters_rel_key(psk_path)
        if part_key and preset_map:
            part_key_norm = utils.normalize_part_key(part_key)
            for pk, stem in preset_map.items():
                if utils.normalize_part_key(pk) == part_key_norm:
                    mi_stem = stem
                    break

        # Cosmetic fallback: any MI stem from the colourway that resolves beside this mesh
        if not mi_stem:
            for stem in mi_stems_from_preset:
                if textures.find_skin_json_for_mi_stem(psk_path, stem):
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
        for entry in utils.ue_export_entries(data):
            if entry.get("Type") not in _VALID_TYPES:
                continue
            props = entry.get("Properties", {})
            mat_path = props.get("Material", {}).get("AssetPathName", "")
            part_path = props.get("Part", {}).get("AssetPathName", "")
            if not mat_path or not part_path:
                continue
            part_key = get_part_key_from_asset_path(part_path) or get_characters_rel_key(part_path)
            if not part_key:
                continue
            mi_stem = mat_path.split("/")[-1].split(".")[0]
            if mi_stem:
                result[part_key] = mi_stem
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to parse outfit preset '{json_path}': {e}")
    return result


def _mi_stems_from_colourway_json(json_path: str) -> list:
    """Collect Material MI stems from a DA_OI colourway JSON (outfit or cosmetic)."""
    stems = []
    if not json_path or not os.path.isfile(json_path):
        return stems
    _VALID_TYPES = {
        "CustomizationVisualPartSetMaterialModifier",
        "CustomizationVisualPartSetMaterialPropertyModifier",
    }
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for entry in utils.ue_export_entries(data):
            if entry.get("Type") not in _VALID_TYPES:
                continue
            mat_path = entry.get("Properties", {}).get("Material", {}).get("AssetPathName", "")
            if not mat_path:
                continue
            stem = mat_path.split("/")[-1].split(".")[0]
            if stem and stem not in stems:
                stems.append(stem)
    except Exception:
        pass
    return stems

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


def get_characters_rel_key(path: str) -> str:
    """Relative key under Characters/ for backpacks/cosmetics (drops asset filename)."""
    if not path:
        return ""
    clean = path.replace("\\", "/")
    leaf = clean.split("/")[-1]
    if "." in leaf:
        clean = clean[: -(len(leaf))] + leaf.split(".")[0]
    parts = [p for p in clean.strip("/").split("/") if p]
    for i, part in enumerate(parts):
        if part.lower() != "characters" or i + 1 >= len(parts):
            continue
        segs = parts[i + 1 :]
        if not segs:
            return ""
        last = segs[-1].lower()
        if last.startswith(("da_vp_", "da_oi_", "sm_", "sk_", "mi_", "t_")):
            segs = segs[:-1]
        return "/".join(segs) if segs else ""
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

def collect_psks_from_model_folder(root: str, model_folder_name: str) -> list:
    model_folder_name = (model_folder_name or "").strip()
    if not model_folder_name:
        return []
    char_dir = utils.find_relative_dir(root, ["Characters", "Assets", model_folder_name])
    if not char_dir:
        return []
    paths, _ = utils.find_psks_in_folder(char_dir)
    return paths

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
        entry = utils.first_ue_export(data, "CharacterVisualSkinOnlineItemDataAsset")
        if not entry:
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

def queue_supports_outfit_batch(context) -> bool:
    """True when the queued PSKs should show the colourway batch UI.

    Covers layered character clothing, backpacks/cosmetics with DA_OI colourways,
    and explicit manual DA_OI folder overrides.
    """
    manual_folder = getattr(context.scene, "arc_manual_outfit_folder", "")
    if manual_folder and os.path.isdir(bpy.path.abspath(manual_folder)):
        return True
    for entry in context.scene.arc_psk_entries:
        psk_path = bpy.path.abspath(entry.psk_path)
        model_type = textures.detect_model_type(psk_path)
        if model_type == "clothing":
            return True
        if find_cosmetic_colourway_folder(psk_path):
            return True
    return False

def populate_outfit_selections(context) -> int:
    sels = context.scene.arc_outfit_selections
    prior = {s.json_path: s.selected for s in sels}
    sels.clear()
    if not queue_supports_outfit_batch(context):
        return 0
    manual_folder = getattr(context.scene, 'arc_manual_outfit_folder', '')
    if manual_folder and os.path.isdir(bpy.path.abspath(manual_folder)):
        presets = scan_outfit_presets_in_folder(bpy.path.abspath(manual_folder))
        for preset_name, json_path in presets:
            s = sels.add()
            s.preset_name = preset_name
            s.json_path = json_path
            s.selected = prior.get(json_path, False)
        return len(sels)

    seen_paths = set()
    added = 0

    # 1) Cosmetic / backpack colourways linked to queued meshes
    for entry in context.scene.arc_psk_entries:
        psk_path = bpy.path.abspath(entry.psk_path)
        cosmetic_folder = find_cosmetic_colourway_folder(psk_path)
        if not cosmetic_folder or cosmetic_folder in seen_paths:
            continue
        seen_paths.add(cosmetic_folder)
        presets = scan_outfit_presets_in_folder(cosmetic_folder)
        for preset_name, json_path in presets:
            if json_path in seen_paths:
                continue
            seen_paths.add(json_path)
            s = sels.add()
            s.preset_name = preset_name
            s.json_path = json_path
            s.selected = prior.get(json_path, False)
            added += 1
    if added:
        return added

    # 2) Character outfit colourways (DA_OI_Outfit_*)
    root = utils.get_pioneer_root()
    if not root:
        return 0
    outfit_root = utils.find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit"])
    if not outfit_root:
        return 0
    from .properties import _csv_model_to_ui_folder, rebuild_csv_outfit_map
    if not _csv_model_to_ui_folder:
        rebuild_csv_outfit_map()
    char_names = set()
    for entry in context.scene.arc_psk_entries:
        cn = get_character_name(bpy.path.abspath(entry.psk_path))
        if cn:
            char_names.add(cn)
    for cn in char_names:
        folder_name = _csv_model_to_ui_folder.get(utils.normalize_folder_name(cn), "")
        if not folder_name:
            continue
        candidate = os.path.join(outfit_root, folder_name)
        if not os.path.isdir(candidate):
            continue
        presets = scan_outfit_presets_in_folder(candidate)
        if not presets:
            continue
        for preset_name, json_path in presets:
            s = sels.add()
            s.preset_name = preset_name
            s.json_path = json_path
            s.selected = prior.get(json_path, False)
        return len(sels)
    return 0

def get_character_name(psk_path: str) -> str:
    norm = bpy.path.abspath(psk_path).replace("\\", "/")
    parts = norm.split("/")
    for i, part in enumerate(parts):
        if part.lower() == "assets" and i > 0 and parts[i - 1].lower() == "characters":
            if i + 1 < len(parts):
                return parts[i + 1]
    return ""