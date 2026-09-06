"""
PSK import and outfit scanning logic for the Arc Raiders Importer
"""

import os
import re
import json
import csv
import importlib
import bpy
import mathutils
from . import utils
from . import textures

_OUTFIT_CSV_CACHE = {}
_log = utils.get_logger()

# Skip flags: we replace materials immediately; outfits don't use VCols / shape keys.
# Keep extra UVs (EXTRAUV0 → UV1). components='ALL' keeps armature for rig.merge.
# Phase 2: consider first-part components='ALL' + rest 'MESH' to cut armature edit-mode cost.
# Materials off: PSK blank mats are discarded; setup_weapon_material / map Stage 2
# grow mesh.materials from SK/SM JSON so face material_index still binds.
_PSK_IMPORT_KWARGS = {
    "should_import_materials": False,
    "should_import_vertex_colors": False,
    "should_import_shape_keys": False,
    "should_import_vertex_normals": False,
    "should_import_extra_uvs": True,
    "components": "ALL",
}

# ---------------------------------------------------------------------------
# PSK Import
# ---------------------------------------------------------------------------

def _get_read_psk_from_file():
    """Return ``read_psk_from_file`` from psk_psa_py when importable, else None."""
    try:
        from psk_psa_py.psk.reader import read_psk_from_file
        return read_psk_from_file
    except ImportError:
        pass
    for base in (
        "bl_ext.blender_org.io_scene_psk_psa",
        "bl_ext.user_default.io_scene_psk_psa",
        "io_scene_psk_psa",
    ):
        try:
            mod = importlib.import_module(f"{base}.psk.import_.operators")
            reader = getattr(mod, "read_psk_from_file", None)
            if callable(reader):
                return reader
        except ImportError:
            continue
    return None


def _get_psk_importer_api():
    """Return ``(import_psk_fn, PskImportOptions)`` from the PSK addon, or (None, None)."""
    for base in (
        "bl_ext.blender_org.io_scene_psk_psa",
        "bl_ext.user_default.io_scene_psk_psa",
        "io_scene_psk_psa",
    ):
        try:
            mod = importlib.import_module(f"{base}.psk.importer")
            fn = getattr(mod, "import_psk", None)
            opts_cls = getattr(mod, "PskImportOptions", None)
            if callable(fn) and opts_cls is not None:
                return fn, opts_cls
        except ImportError:
            continue
    return None, None


def read_psk_safe(filepath: str):
    """Parse a PSK file off-main (no bpy). Returns a Psk object or None."""
    reader = _get_read_psk_from_file()
    if reader is None or not filepath:
        return None
    name = os.path.splitext(os.path.basename(filepath))[0]
    try:
        with utils.timed(f"psk.parse:{name}"):
            return reader(filepath)
    except Exception as exc:
        _log.debug("read_psk_safe(%s) failed: %s", filepath, exc)
        return None


def _psk_options_from_kwargs(opts_cls):
    options = opts_cls()
    options.should_import_materials = False
    options.should_import_vertex_colors = False
    options.should_import_shape_keys = False
    options.should_import_vertex_normals = False
    options.should_import_extra_uvs = True
    options.should_import_mesh = True
    options.should_import_armature = True  # components='ALL'; phase-2 hook for MESH-only
    return options


def _finalize_imported_objects(before_keys: set) -> list:
    after = set(bpy.data.objects.keys())
    objs = [bpy.data.objects[k] for k in after - before_keys]
    # PSK names UV1 as EXTRAUV0; materials (GraphicAtlas Use UV1) expect UV1.
    for obj in objs:
        utils.normalize_object_ue_uv_layers(obj)
    _apply_ue_import_unit_scale(objs)
    return objs


def ue_import_unit_scale(scene=None) -> float:
    """Blender units per Unreal centimeter for outfit/PSK imports (0.01 when toggle on)."""
    if scene is None:
        try:
            scene = bpy.context.scene
        except Exception:
            scene = None
    if scene is not None and not bool(getattr(scene, "arc_ue_to_blender_units", True)):
        return 1.0
    return 0.01


def _apply_ue_import_unit_scale(objs: list) -> None:
    """Scale newly imported roots by UE→Blender factor (default ×0.01)."""
    scale = ue_import_unit_scale()
    if abs(scale - 1.0) < 1e-12 or not objs:
        return
    obj_set = set(objs)
    roots = [o for o in objs if o.parent is None or o.parent not in obj_set]
    targets = roots or list(objs)
    for obj in targets:
        try:
            if obj.get("arc_ue_unit_scale") is not None:
                continue
            obj.scale = (
                float(obj.scale[0]) * scale,
                float(obj.scale[1]) * scale,
                float(obj.scale[2]) * scale,
            )
            obj["arc_ue_unit_scale"] = scale
        except Exception:
            pass


def import_psk(filepath: str, *, psk=None, context=None) -> list:
    """Import a PSK/PSKX file; prefer pre-parsed ``psk`` when provided.

    Uses the addon's ``import_psk(psk, context, name, options)`` when available,
    else ``bpy.ops.psk.import_file`` with skip flags. UV normalize always runs after.
    """
    before = set(bpy.data.objects.keys())
    if not utils.psk_import_available():
        # Reinstall / prefs reset often leaves the bundled extension disabled.
        utils.ensure_psk_addon()

    ctx = context if context is not None else bpy.context
    name = os.path.splitext(os.path.basename(filepath))[0]
    import_fn, opts_cls = _get_psk_importer_api()

    # Prefer direct importer with a pre-parsed (or freshly read) Psk object.
    if import_fn is not None and opts_cls is not None:
        parsed = psk
        if parsed is None:
            parsed = read_psk_safe(filepath)
        if parsed is not None:
            try:
                with utils.timed(f"psk.create:{name}"):
                    import_fn(parsed, ctx, name, _psk_options_from_kwargs(opts_cls))
                return _finalize_imported_objects(before)
            except Exception as exc:
                _log.warning(
                    "direct import_psk failed for %s (%s); falling back to operator",
                    filepath, exc,
                )

    try:
        with utils.timed(f"psk.ops:{name}"):
            result = bpy.ops.psk.import_file(filepath=filepath, **_PSK_IMPORT_KWARGS)
    except TypeError:
        # Older PSK addon builds may lack some kwargs — retry with filepath only.
        try:
            with utils.timed(f"psk.ops:{name}"):
                result = bpy.ops.psk.import_file(filepath=filepath)
        except Exception as exc:
            raise RuntimeError(
                "The 'Unreal PSK/PSA' (io_scene_psk_psa) extension is not installed "
                "or enabled. Enable it in Preferences → Extensions, or re-enable "
                f"the Outfits addon. ({type(exc).__name__}: {exc})"
            ) from exc
    except Exception as exc:
        raise RuntimeError(
            "The 'Unreal PSK/PSA' (io_scene_psk_psa) extension is not installed "
            "or enabled. Enable it in Preferences → Extensions, or re-enable "
            f"the Outfits addon. ({type(exc).__name__}: {exc})"
        ) from exc
    if 'FINISHED' not in result:
        raise RuntimeError(f"PSK import operator returned: {result}")
    return _finalize_imported_objects(before)


def psk_reader_available() -> bool:
    """True when ``psk_psa_py`` ``read_psk_from_file`` can be imported."""
    return _get_read_psk_from_file() is not None

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


def outfit_group_label_for_character(character_name: str, context=None) -> str:
    """Flavour(CodeName) label matching the outfit selector, e.g. Bonecrown(AntlerShaman)."""
    char = (character_name or "").strip()
    if not char:
        return "Outfit"
    try:
        csv_path = get_outfit_csv_path(context) if context is not None else default_outfit_csv_path()
    except Exception:
        csv_path = default_outfit_csv_path()
    rows = load_outfit_csv(csv_path)
    char_norm = utils.normalize_folder_name(char)
    for row in rows or []:
        model = (row.get("Model Folder Name") or "").strip().split(";")[0].strip()
        ui = (row.get("Item/UI Folder Name") or "").strip().split(";")[0].strip()
        if utils.normalize_folder_name(model) == char_norm or utils.normalize_folder_name(ui) == char_norm:
            return outfit_display_label(row)
    return char


def character_sk_name(character_name: str) -> str:
    """Armature object name: SK_AntlerShaman (no body-part suffix)."""
    char = (character_name or "").strip()
    if not char:
        return "SK_Outfit"
    if char.upper().startswith("SK_"):
        return char
    return f"SK_{char}"

def find_outfit_row(rows: list, key: str) -> dict:
    for row in rows:
        if outfit_row_key(row) == key:
            return row
    return None


def colorways_for_outfit_row(root: str, row: dict) -> list:
    """Return ``[(preset_name, json_path), ...]`` for a CSV outfit row.

    Scans ``Items/Characters/Skins/Outfit/<Item/UI Folder>`` (and Model Folder
    as fallback) for DA_OI colourway JSONs — same discovery the batch UI uses.
    """
    if not root or not row:
        return []
    outfit_root = utils.find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit"])
    if not outfit_root or not os.path.isdir(outfit_root):
        return []
    folders = []
    for f in (row.get("Item/UI Folder Name") or "").split(";"):
        f = f.strip()
        if f and f not in folders:
            folders.append(f)
    model = (row.get("Model Folder Name") or "").strip().split(";")[0].strip()
    if model and model not in folders:
        folders.append(model)
    for folder_name in folders:
        candidate = os.path.join(outfit_root, folder_name)
        if not os.path.isdir(candidate):
            continue
        presets = scan_outfit_presets_in_folder(candidate)
        if presets:
            return presets
    return []


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
    outfit_root = utils.find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit"])
    if outfit_root and os.path.isdir(outfit_root):
        want = utils.normalize_folder_name(character_name)
        try:
            for name in os.listdir(outfit_root):
                full = os.path.join(outfit_root, name)
                if os.path.isdir(full) and utils.normalize_folder_name(name) == want:
                    return full
        except OSError:
            pass
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
# FModel dumps after a game patch often write the wrong asset into a matching
# filename (TickHunter Color_Red.json may be Angler, while TickHunter Green
# lives in Hoplomachus_Color_Red.json). Identity comes from Name/Package.
_COLORWAY_RE = re.compile(
    r'^DA_OI_(?:Outfit_|BackpackContainer_|BackpackAttachment_|BackpackCharm_|BackpackFrame_|RaiderTool_)?(.+)_Color(?:_(.+))?$',
    re.IGNORECASE,
)
_COLOURWAY_INDEX_CACHE: dict[str, dict[str, list]] = {}
_COLOURWAY_PART_TYPES = {
    "CharacterVisualPartOnlineItemDataAsset",
}
_COLOURWAY_MOD_TYPES = {
    "CustomizationVisualPartSetMaterialModifier",
    "CustomizationVisualPartSetMaterialPropertyModifier",
}


def scan_outfit_presets(character_name: str, manual_folder: str = "") -> list:
    if manual_folder and os.path.isdir(bpy.path.abspath(manual_folder)):
        char_norm = utils.normalize_folder_name(character_name) if character_name else ""
        return scan_outfit_presets_in_folder(bpy.path.abspath(manual_folder), char_norm)
    folder = get_outfit_folder(character_name)
    char_norm = utils.normalize_folder_name(character_name)
    if folder and os.path.isdir(folder):
        return scan_outfit_presets_in_folder(folder, char_norm)
    if not char_norm:
        return []
    root = utils.get_pioneer_root()
    outfit_root = utils.find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit"]) if root else ""
    if not outfit_root:
        return []
    return list(_index_colourways(outfit_root).get(char_norm, []))


def scan_outfit_presets_in_folder(folder: str, char_norm: str = "") -> list:
    """Scan DA_OI_*_Color_* colourways by inner Name/Package, not filename.

    When ``folder`` is an item under Items/Characters/Skins/<Slot>/, sibling
    folders are searched too so a misnamed FModel dump still matches.
    """
    if not folder or not os.path.isdir(folder):
        return []
    slot_root = _skins_slot_root(folder)
    want = char_norm or (
        utils.normalize_folder_name(os.path.basename(folder))
        if os.path.normcase(os.path.abspath(folder)) != os.path.normcase(slot_root)
        else ""
    )
    if not want:
        return []
    return list(_index_colourways(slot_root).get(want, []))


def _skins_slot_root(folder: str) -> str:
    """.../Skins/Outfit/Abyss → Outfit; .../Skins/Outfit → Outfit."""
    folder = os.path.abspath(folder)
    parent = os.path.dirname(folder)
    if os.path.basename(os.path.dirname(parent)).lower() == "skins":
        return parent
    if os.path.basename(parent).lower() == "skins":
        return folder
    return folder


def _index_colourways(slot_root: str) -> dict:
    """Map normalized item name → [(preset_name, json_path), ...]."""
    slot_root = os.path.abspath(slot_root)
    cached = _COLOURWAY_INDEX_CACHE.get(slot_root)
    if cached is not None:
        return cached
    best: dict[tuple[str, str], tuple[int, str, str]] = {}
    folders = [slot_root]
    try:
        for name in os.listdir(slot_root):
            full = os.path.join(slot_root, name)
            if os.path.isdir(full):
                folders.append(full)
    except OSError:
        pass
    for folder in folders:
        folder_norm = utils.normalize_folder_name(os.path.basename(folder))
        for path in _iter_colourway_json_paths(folder):
            rec = _colourway_record_from_json(path)
            if not rec:
                continue
            item_norm, preset, json_path = rec
            preset_norm = utils.normalize_folder_name(preset)
            score = 0
            if folder_norm == item_norm:
                score += 2
            fname_ident = _parse_colourway_identity(os.path.splitext(os.path.basename(path))[0])
            if fname_ident and utils.normalize_folder_name(fname_ident[0]) == item_norm:
                score += 1
            key = (item_norm, preset_norm)
            prev = best.get(key)
            if prev is None or score > prev[0]:
                best[key] = (score, preset, json_path)
    result: dict[str, list] = {}
    for (item_norm, _pn), (_score, preset, path) in best.items():
        result.setdefault(item_norm, []).append((preset, path))
    for key in result:
        result[key].sort(key=lambda row: row[0].lower())
    _COLOURWAY_INDEX_CACHE[slot_root] = result
    return result


def _iter_colourway_json_paths(folder: str):
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for fname in names:
        low = fname.lower()
        if not low.endswith(".json"):
            continue
        if "persistence" in low:
            continue
        if not low.startswith("da_oi"):
            continue
        if "_color" not in low:
            continue
        yield os.path.join(folder, fname)


def _parse_colourway_identity(stem: str):
    if not stem:
        return None
    stem = re.sub(r'_Persistence$', '', stem, flags=re.IGNORECASE)
    m = _COLORWAY_RE.match(stem)
    if not m:
        return None
    item_segment, suffix = m.group(1), m.group(2)
    if not item_segment:
        return None
    return item_segment, (suffix or "Default")


def _colourway_record_from_json(json_path: str):
    """Return (item_norm, preset_name, json_path) from inner Name/Package."""
    data = _load_ue_json(json_path)
    if data is None:
        return None
    identity = None
    has_mod = False
    for entry in utils.ue_export_entries(data):
        t = entry.get("Type") or ""
        if t in _COLOURWAY_PART_TYPES and identity is None:
            ident = _parse_colourway_identity(_colourway_stem_from_entry(entry))
            if ident:
                identity = ident
        if t in _COLOURWAY_MOD_TYPES:
            mat = _soft_asset_path(entry.get("Properties", {}).get("Material"))
            if mat:
                has_mod = True
                if identity:
                    break
    if not identity or not has_mod:
        return None
    item_segment, preset = identity
    return utils.normalize_folder_name(item_segment), preset, json_path


def _colourway_stem_from_entry(entry: dict) -> str:
    name = (entry.get("Name") or "").strip()
    if name:
        return name
    pkg = (entry.get("Package") or "").replace("\\", "/").strip()
    if pkg:
        return pkg.split("/")[-1].split(".")[0]
    return ""


def _soft_asset_path(val) -> str:
    if isinstance(val, dict):
        return val.get("AssetPathName") or val.get("ObjectPath") or ""
    if isinstance(val, str):
        return val
    return ""


def _load_ue_json(json_path: str):
    if not json_path or not os.path.isfile(json_path):
        return None
    try:
        with open(json_path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return None


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
    try:
        data = _load_ue_json(json_path)
        if not data:
            return result
        for entry in utils.ue_export_entries(data):
            if entry.get("Type") not in _COLOURWAY_MOD_TYPES:
                continue
            props = entry.get("Properties", {})
            mat_path = _soft_asset_path(props.get("Material"))
            part_path = _soft_asset_path(props.get("Part"))
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
    data = _load_ue_json(json_path)
    if not data:
        return stems
    for entry in utils.ue_export_entries(data):
        if entry.get("Type") not in _COLOURWAY_MOD_TYPES:
            continue
        mat_path = _soft_asset_path(entry.get("Properties", {}).get("Material"))
        if not mat_path:
            continue
        stem = mat_path.split("/")[-1].split(".")[0]
        if stem and stem not in stems:
            stems.append(stem)
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

def _part_folder_has_uemodel(part_folder: str) -> bool:
    """True when a non-skeleton .uemodel exists (mesh exported as UEFormat, not ActorX)."""
    try:
        for fname in os.listdir(part_folder):
            fl = fname.lower()
            if not fl.endswith(".uemodel"):
                continue
            if fl.endswith("_skeleton.uemodel"):
                continue
            return True
    except OSError:
        pass
    return False


def collect_psks_for_outfit_row(root: str, item_ui_folders: list) -> tuple:
    """Collect PSK paths for DA_OI parts; also report parts missing ActorX meshes.

    Returns ``(psk_paths, missing_notes)`` where each missing note is a short
    human-readable string (part key + reason). Shared MaterialLibrary textures
    are unrelated — this only covers skeletal mesh exports under each part folder.
    """
    seen_keys = set()
    psks = []
    missing = []
    for folder_name in item_ui_folders:
        for asset_path in read_oi_parts_asset_paths(root, folder_name):
            key = get_part_key_from_asset_path(asset_path)
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            character, part = key.split("/", 1)
            part_folder = utils.find_relative_dir(root, ["Characters", "Assets", character, part])
            if not part_folder:
                missing.append(f"{key} (folder missing)")
                continue
            psk = find_psk_in_specific_folder(part_folder)
            if psk:
                psks.append(psk)
                continue
            if _part_folder_has_uemodel(part_folder):
                missing.append(f"{key} (.uemodel only — re-export as ActorX .psk)")
            else:
                missing.append(f"{key} (no .psk)")
    return psks, missing


def collect_psks_from_model_folder(root: str, model_folder_name: str) -> tuple:
    """Collect PSKs under Characters/Assets/<model>/; report part dirs lacking .psk.

    Returns ``(psk_paths, missing_notes)``.
    """
    model_folder_name = (model_folder_name or "").strip()
    if not model_folder_name:
        return [], []
    char_dir = utils.find_relative_dir(root, ["Characters", "Assets", model_folder_name])
    if not char_dir:
        return [], [f"{model_folder_name} (model folder missing)"]
    paths, _ = utils.find_psks_in_folder(char_dir)
    missing = []
    try:
        part_dirs = sorted(
            d for d in os.listdir(char_dir)
            if os.path.isdir(os.path.join(char_dir, d))
        )
    except OSError:
        part_dirs = []
    found_parts = set()
    for psk in paths:
        # .../Character/Part/SK_....psk → Part
        part_name = os.path.basename(os.path.dirname(psk))
        if part_name:
            found_parts.add(utils.normalize_folder_name(part_name))
    for part in part_dirs:
        if utils.normalize_folder_name(part) in found_parts:
            continue
        part_folder = os.path.join(char_dir, part)
        key = f"{model_folder_name}/{part}"
        if _part_folder_has_uemodel(part_folder):
            missing.append(f"{key} (.uemodel only — re-export as ActorX .psk)")
        else:
            # Skip empty/non-mesh dirs (no SK_/DA_VP_ sidecar).
            try:
                names = os.listdir(part_folder)
            except OSError:
                continue
            if not any(
                n.lower().startswith(("sk_", "da_vp_", "sm_"))
                for n in names
            ):
                continue
            missing.append(f"{key} (no .psk)")
    return paths, missing

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

    # 2) Character outfit colourways (DA_OI_Outfit_*) — identity index, not CSV/filename
    char_names = []
    seen_chars = set()
    for entry in context.scene.arc_psk_entries:
        cn = get_character_name(bpy.path.abspath(entry.psk_path))
        if not cn:
            continue
        key = utils.normalize_folder_name(cn)
        if key in seen_chars:
            continue
        seen_chars.add(key)
        char_names.append(cn)
    for cn in char_names:
        presets = scan_outfit_presets(cn)
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