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

# Replace materials immediately; retain authored body/clothing gender morphs.
# Keep extra UVs (EXTRAUV0 → UV1). components='ALL' keeps armature for rig.merge.
# Phase 2: consider first-part components='ALL' + rest 'MESH' to cut armature edit-mode cost.
# Materials off: PSK blank mats are discarded; setup_weapon_material / map Stage 2
# grow mesh.materials from SK/SM JSON so face material_index still binds.
_PSK_IMPORT_KWARGS = {
    "should_import_materials": False,
    "should_import_vertex_colors": False,
    "should_import_shape_keys": True,
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
    options.should_import_shape_keys = True
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
    if (psk is None and str(filepath).casefold().endswith(".uemodel")):
        # psk_psa_py reads UEFORMAT as an empty Psk (0 verts, no weights).
        from . import uemodel_import
        if (uemodel_import.peek_uemodel_version(filepath) or 0) >= uemodel_import.ATTR_VERSION:
            objs = uemodel_import.import_uemodel_skinned(filepath, scale=ue_import_unit_scale())
            if objs:
                for obj in objs:
                    utils.normalize_object_ue_uv_layers(obj)
                return objs
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
    addon_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(addon_dir, 'Data/outfit_reference.csv')

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
    return row.get("ST") or row.get("Item/UI Folder Name") or row.get("Model Folder Name") or row.get("Flavour") or "?"

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


_PAK_COLOURWAY_TRIED: set[str] = set()
_PAK_COLOURWAY_MI_DONE: set[str] = set()


def _ensure_pak_colourway_folder(character_name: str) -> str:
    """Extract DA_OI colourway JSON and the MI files they reference when paks are set."""
    name = (character_name or "").strip()
    if not name:
        return ""
    key = utils.normalize_folder_name(name)
    folder_name = _outfit_folder_from_csv(name) or name
    try:
        from . import pak_extract
    except Exception:
        return ""
    scene = bpy.context.scene
    if not pak_extract.configured_dir(scene):
        return ""
    rel = f"Items/Characters/Skins/Outfit/{folder_name}"
    cache_folder = os.path.join(
        pak_extract.cache_dir(),
        "PioneerGame", "Content", "Pioneer",
        "Items", "Characters", "Skins", "Outfit", folder_name,
    )
    if key not in _PAK_COLOURWAY_TRIED and not _folder_has_colourway_json(cache_folder):
        _PAK_COLOURWAY_TRIED.add(key)
        try:
            pak_extract.extract_paths(
                pak_extract.configured_dir(scene),
                pak_extract.resolve_usmap(scene),
                [pak_extract.game_path(rel)],
            )
        except Exception as exc:
            print(f"Arc Pak: colourway extract failed: {exc}")
    if not _folder_has_colourway_json(cache_folder):
        return ""
    if key not in _PAK_COLOURWAY_MI_DONE:
        _PAK_COLOURWAY_MI_DONE.add(key)
        _extract_colourway_materials(cache_folder)
    _COLOURWAY_INDEX_CACHE.pop(os.path.abspath(cache_folder), None)
    parent = os.path.dirname(cache_folder)
    _COLOURWAY_INDEX_CACHE.pop(os.path.abspath(parent), None)
    return cache_folder


def _folder_has_colourway_json(folder: str) -> bool:
    if not folder or not os.path.isdir(folder):
        return False
    try:
        for name in os.listdir(folder):
            low = name.casefold()
            if low.startswith("da_oi") and "_color" in low and low.endswith(".json") and "persistence" not in low:
                return True
    except OSError:
        return False
    return False


def _extract_colourway_materials(folder: str) -> None:
    """Pull MI packages named by the colourway JSON so the selector can apply them."""
    try:
        from . import pak_extract
    except Exception:
        return
    scene = bpy.context.scene
    game = pak_extract.configured_dir(scene)
    usmap = pak_extract.resolve_usmap(scene)
    if not game or not usmap:
        return
    wanted = []
    seen = set()
    for path in _iter_colourway_json_paths(folder):
        data = _load_ue_json(path)
        if not data:
            continue
        for entry in utils.ue_export_entries(data):
            if (entry.get("Type") or "") not in _COLOURWAY_MOD_TYPES:
                continue
            mat = _soft_asset_path((entry.get("Properties") or {}).get("Material"))
            cooked = pak_extract.game_path(mat) if mat else ""
            if not cooked or cooked.casefold() in seen:
                continue
            seen.add(cooked.casefold())
            dest = os.path.join(pak_extract.cache_dir(), *cooked.split("/")) + ".json"
            if os.path.isfile(dest):
                continue
            wanted.append(cooked)
    if not wanted:
        return
    try:
        pak_extract.extract_paths(game, usmap, wanted)
    except Exception as exc:
        print(f"Arc Pak: colourway material extract failed: {exc}")


def colorways_for_outfit_row(root: str, row: dict) -> list:
    """Return ``[(preset_name, json_path), ...]`` for a CSV outfit row.

    Scans ``Items/Characters/Skins/Outfit/<Item/UI Folder>`` (and Model Folder
    as fallback) for DA_OI colourway JSONs — same discovery the batch UI uses.
    """
    if not row:
        return []
    outfit_root = utils.find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit"]) if root else ""
    if not outfit_root or not os.path.isdir(outfit_root):
        outfit_root = ""
    folders = []
    for f in (row.get("Item/UI Folder Name") or "").split(";"):
        f = f.strip()
        if f and f not in folders:
            folders.append(f)
    model = (row.get("Model Folder Name") or "").strip().split(";")[0].strip()
    if model and model not in folders:
        folders.append(model)
    if outfit_root:
        for folder_name in folders:
            candidate = os.path.join(outfit_root, folder_name)
            if not os.path.isdir(candidate):
                continue
            presets = scan_outfit_presets_in_folder(candidate)
            if presets:
                return presets
    for folder_name in folders:
        pak_folder = _ensure_pak_colourway_folder(folder_name)
        if not pak_folder:
            continue
        presets = scan_outfit_presets_in_folder(pak_folder)
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
        from ..Outfit_Accessories.properties import _csv_model_to_ui_folder, rebuild_csv_outfit_map
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
        found = scan_outfit_presets_in_folder(folder, char_norm)
        if found:
            return found
    if not char_norm:
        return []
    root = utils.get_pioneer_root()
    outfit_root = utils.find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit"]) if root else ""
    if outfit_root:
        found = list(_index_colourways(outfit_root).get(char_norm, []))
        if found:
            return found
    pak_folder = _ensure_pak_colourway_folder(character_name)
    if pak_folder:
        return scan_outfit_presets_in_folder(pak_folder, char_norm)
    return []


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

def apply_outfit_preset(context, preset_json_path: str = "", *, visibility_option_paths=()) -> int:
    """Apply a colourway preset and any explicitly selected visibility options."""
    updated = 0
    if preset_json_path:
        from . import pak_extract
        scene = context.scene
        with pak_extract.batched_material_hierarchy(scene):
            meshes = [textures._fs_abspath(bpy.path.abspath(entry.psk_path)) for entry in scene.arc_psk_entries]
            pak_extract.ensure_cached_mesh_material_metadata(scene, meshes)
            updated = _apply_outfit_preset(context, preset_json_path)
    selected_visibility = list(visibility_option_paths or ())
    if preset_json_path:
        selected_visibility.insert(0, preset_json_path)
    from ..Outfit_Accessories import outfit_visibility
    outfit_dir = os.path.dirname(preset_json_path) if preset_json_path else ""
    preset_visibility = outfit_visibility.preset_option_paths(outfit_dir) if outfit_dir else []
    if outfit_dir:
        _apply_default_option_material_swaps(context.scene, outfit_dir, selected_visibility)
    if selected_visibility or preset_visibility:
        outfit_visibility.apply_visibility_options(context.scene, selected_visibility, preset_visibility)
    if outfit_dir:
        outfit_visibility.hide_wrap_deformer_drivers(context.scene)
    return updated


def _da_oi_entries(path: str):
    data = _load_ue_json(path)
    return utils.ue_export_entries(data) if data else []


def _da_oi_tags(props) -> list:
    tags = [d.get("TagName", "") for d in (props or {}).get("TagDictionary") or []]
    tags.extend(t for t in ((props or {}).get("Tags") or []) if isinstance(t, str))
    return tags


def _da_oi_setting_option(tags):
    setting = next((t.split("Character.Setting.", 1)[1] for t in tags if t.startswith("Character.Setting.")), "")
    option = next((t for t in tags if t.startswith("Character.Option.")), "")
    return setting, option


def _da_oi_chosen_options(outfit_dir: str, explicit_paths) -> dict:
    """Map setting tag -> DA_OI option file. Explicit user choices win over DefaultItemQuery."""
    chosen = {}
    for path in explicit_paths:
        for entry in _da_oi_entries(str(path)):
            if entry.get("Type") != "CharacterVisualPartOnlineItemDataAsset":
                continue
            tags = _da_oi_tags(entry.get("Properties"))
            setting, _ = _da_oi_setting_option(tags)
            if setting:
                chosen["Character.Setting." + setting] = str(path)
    option_files = []
    for name in sorted(os.listdir(outfit_dir)):
        if name.startswith("DA_OI") and name.endswith(".json"):
            option_files.append(os.path.join(outfit_dir, name))
    for path in option_files:
        for slot in _da_oi_entries(path):
            if slot.get("Type") != "CharacterVisualSlotOnlineItemDataAsset":
                continue
            dq = (slot.get("Properties") or {}).get("DefaultItemQuery")
            if not dq:
                continue
            setting, option = _da_oi_setting_option(_da_oi_tags(dq))
            if not setting or not option or ("Character.Setting." + setting) in chosen:
                continue
            found = ""
            for other in option_files:
                for opt in _da_oi_entries(other):
                    if opt.get("Type") != "CharacterVisualPartOnlineItemDataAsset":
                        continue
                    tags = _da_oi_tags(opt.get("Properties"))
                    if "Character.ItemType.Option" in tags and ("Character.Setting." + setting) in tags and option in tags:
                        found = other
                        break
                if found:
                    break
            if not found:
                stem = os.path.splitext(os.path.basename(path))[0]
                cand = os.path.join(outfit_dir, stem + "_" + option.split(".")[-1] + ".json")
                if os.path.isfile(cand):
                    found = cand
            if found:
                chosen["Character.Setting." + setting] = found
            else:
                print(f"Arc Pak: default option file missing for {setting} ({option}); skipped")
    return chosen


def _da_oi_content_root(root: str) -> str:
    """ArcPak cache root -> Content/Pioneer dir (falls back to root when already content)."""
    if not root:
        return root
    content = os.path.join(root, "PioneerGame", "Content", "Pioneer")
    return content if os.path.isdir(content) else root


def _da_oi_resolve_mi(mi_ref: str, root: str, index: dict, scene=None):
    """Return (json_path, note) for the exact MI package the option names.

    Looks under root, then extracts the exact package from the paks into the
    addon cache. Never substitutes a same-named MI from another folder: those
    are different materials (Angler/Visor vs StationMaster/HelmetVisor
    MI_CRT_Screen).
    """
    pkg = mi_ref.split(".", 1)[0]
    rel = pkg.split("/Game/Pioneer/", 1)[-1] if "/Game/Pioneer/" in pkg else pkg.lstrip("/")

    def lookup():
        roots = [root]
        try:
            from . import pak_extract
            roots.append(_da_oi_content_root(pak_extract.cache_dir()))
        except Exception:
            pass
        for base in roots:
            if not base:
                continue
            stem_path = os.path.join(base, *rel.split("/"))
            for suffix in (".arc-material.json", ".json"):
                if os.path.isfile(stem_path + suffix):
                    return stem_path + suffix
        return ""

    found = lookup()
    if found:
        return found, ""
    tried = index.setdefault("extracted", set())
    if scene is not None and pkg not in tried:
        tried.add(pkg)
        try:
            from . import pak_extract
            pak_extract.ensure_rels(scene, ["Game/Pioneer/" + rel])
        except Exception as exc:
            return "", f"missing; exact extract failed ({type(exc).__name__}: {exc})"
        found = lookup()
        if found:
            return found, "extracted"
    return "", "missing (exact package not in cache or paks)"


def _da_oi_objects_for_part(scene, part_ref: str):
    from ..Outfit_Accessories import outfit_visibility
    part_key = outfit_visibility._package_key(part_ref)
    mesh_keys = outfit_visibility._mesh_keys_for_visual_part(part_ref)
    out = []
    if not part_key or not mesh_keys:
        return out
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        explicit_value = obj.get("arc_visual_part") or obj.get("arc_visual_part_path")
        if explicit_value:
            matches = outfit_visibility._package_key(explicit_value) == part_key
        else:
            source = outfit_visibility._package_key(obj.get("arc_source_mesh") or obj.get("arc_psk_path"))
            matches = source in mesh_keys
        if matches:
            out.append(obj)
    return out


def _da_oi_slot_index(obj, mi_json: str, part_ref: str):
    """Pick the material slot index for a default option MI. One slot: 0. Else a unique name-token match, else None."""
    slots = list(obj.material_slots)
    if len(slots) <= 1:
        return 0
    generic = {"mi", "da", "vp", "outfit", "wrapper", "visor", "skin", "material", "json"}
    stem = os.path.splitext(os.path.basename(mi_json))[0].lower()
    part_stem = os.path.splitext(os.path.basename(part_ref))[0].lower()
    tokens = {t for t in (stem + "_" + part_stem).replace("-", "_").split("_") if len(t) > 2 and t not in generic}
    hits = [i for i, sl in enumerate(slots) if sl.material and any(t in sl.material.name.lower() for t in tokens)]
    return hits[0] if len(hits) == 1 else None


def _da_oi_is_visor_obj(obj) -> bool:
    """True when the object's stamped model folder is Visor (Wrapper/Visor/SK_*.uemodel)."""
    try:
        stamped = str(obj.get("arc_psk_path", "") or "").replace("\\", "/")
    except Exception:
        return False
    parts = [p for p in stamped.split("/") if p]
    return len(parts) >= 2 and parts[-2].lower() == "visor"


def _da_oi_mi_stem(value) -> str:
    """'/Game/.../MI_X.MI_X', 'C:/.../MI_X.arc-material.json' or 'MI_X|parent' -> 'mi_x'."""
    s = str(value or "").replace("\\", "/").split("|", 1)[0]
    b = s.rsplit("/", 1)[-1]
    for suffix in (".arc-material.json", ".json"):
        if b.lower().endswith(suffix):
            b = b[: -len(suffix)]
    return b.split(".", 1)[0].lower()


def _da_oi_setting_material_stems(outfit_dir: str) -> dict:
    """setting tag -> {option file: set of MI stems its material modifiers set}."""
    from ..Outfit_Accessories import outfit_visibility
    out = {}
    if not outfit_dir or not os.path.isdir(outfit_dir):
        return out
    for name in sorted(os.listdir(outfit_dir)):
        if not (name.startswith("DA_OI") and name.endswith(".json")):
            continue
        path = os.path.abspath(os.path.join(outfit_dir, name))
        entries = _da_oi_entries(path)
        setting = ""
        for entry in entries:
            if entry.get("Type") == "CharacterVisualPartOnlineItemDataAsset":
                s, _ = _da_oi_setting_option(_da_oi_tags(entry.get("Properties")))
                if s:
                    setting = "Character.Setting." + s
                    break
        if not setting:
            continue
        stems = set()
        for entry in entries:
            if entry.get("Type") == "CustomizationVisualPartSetMaterialModifier":
                ref = outfit_visibility._asset_path((entry.get("Properties") or {}).get("Material"))
                if ref:
                    stems.add(_da_oi_mi_stem(ref))
        if stems:
            out.setdefault(setting, {})[path] = stems
    return out


def _da_oi_current_mi_stems(obj) -> set:
    """MI stems the object is currently built from (bindings, stamped MI paths, material key)."""
    vals = []
    try:
        b = json.loads(obj.get("arc_outfit_material_bindings") or "{}")
        if isinstance(b, dict):
            vals += list(b.values())
    except Exception:
        pass
    for k in ("arc_mi_path", "arc_skin_json", "arc_glass_skin_json", "arc_material_key"):
        vals.append(obj.get(k))
    for sl in obj.material_slots:
        m = sl.material
        if m is not None:
            for k in ("arc_mi_path", "arc_material_key"):
                try:
                    vals.append(m.get(k))
                except Exception:
                    pass
    return {s for s in (_da_oi_mi_stem(v) for v in vals if v) if s}


def _apply_default_option_material_swaps(scene, outfit_dir: str, explicit_paths=()) -> int:
    """Rebind material slots from each setting's chosen DA_OI option (default unless user picked one)."""
    from ..Outfit_Accessories import outfit_visibility
    try:
        from .materials import clothing
    except Exception as exc:
        print(f"Arc Pak: default option swaps skipped, clothing import failed: {exc}")
        return 0
    root = _da_oi_content_root(utils.get_pioneer_root())
    if not root or not os.path.isdir(root):
        print("Arc Pak: default option swaps skipped, no Pioneer root")
        return 0
    index = {}
    rebound = set()
    # Card preset options (outfit_presets) count as chosen, before the user's explicit picks.
    explicit_paths = list(outfit_visibility.preset_option_paths(outfit_dir)) + list(explicit_paths or ())
    explicit_set = {os.path.normcase(os.path.abspath(str(p))) for p in explicit_paths}
    alt_mis = _da_oi_setting_material_stems(outfit_dir)
    for setting_tag, option_path in sorted(_da_oi_chosen_options(outfit_dir, explicit_paths).items()):
        is_default = os.path.normcase(os.path.abspath(option_path)) not in explicit_set
        others = set()
        for path, stems in alt_mis.get(setting_tag, {}).items():
            if os.path.normcase(path) != os.path.normcase(option_path):
                others |= stems
        for entry in _da_oi_entries(option_path):
            if entry.get("Type") != "CustomizationVisualPartSetMaterialModifier":
                continue
            props = entry.get("Properties") or {}
            part_ref = outfit_visibility._asset_path(props.get("Part"))
            mat_ref = outfit_visibility._asset_path(props.get("Material"))
            if not part_ref or not mat_ref:
                continue
            mi_json, note = _da_oi_resolve_mi(mat_ref, root, index, scene)
            if not mi_json:
                print(f"Arc Pak: swap {setting_tag} {os.path.basename(option_path)}: MI {mat_ref} {note}; skipped")
                continue
            if note == "extracted":
                print(f"Arc Pak: swap {setting_tag}: extracted exact {mat_ref} -> {mi_json}")
            is_glass = False
            try:
                is_glass = clothing._is_visor_glass_mi(clothing._parse_visor_mi(mi_json))
            except Exception as exc:
                print(f"Arc Pak: glass check failed for {mi_json}: {exc}")
            for obj in _da_oi_objects_for_part(scene, part_ref):
                if is_default and others and (_da_oi_current_mi_stems(obj) & others):
                    # The object already carries another option of this setting (a colourway or
                    # variant chosen at import): that choice wins over the slot default.
                    print(f"Arc Pak: swap {setting_tag}: {obj.name} keeps chosen option; default skipped")
                    continue
                slot = _da_oi_slot_index(obj, mi_json, part_ref)
                if slot is None:
                    print(f"Arc Pak: swap {setting_tag}: no unique slot on {obj.name} for {os.path.basename(mi_json)}; skipped")
                    continue
                try:
                    bindings = json.loads(obj.get("arc_outfit_material_bindings") or "{}")
                    if not isinstance(bindings, dict):
                        bindings = {}
                except Exception:
                    bindings = {}
                bindings[str(slot)] = mi_json
                obj["arc_outfit_material_bindings"] = json.dumps(bindings, sort_keys=True)
                if is_glass or _da_oi_is_visor_obj(obj):
                    # Visor route reads arc_glass_skin_json, then arc_skin_json, and
                    # skips arc_material_key. Without these the rebuild keeps the
                    # Wrapper placeholder MI (exp3 failure, w12j runs/subway_exp3.log).
                    obj["arc_skin_json"] = mi_json
                    obj["arc_mi_path"] = mi_json
                    obj["arc_glass_skin_json"] = mi_json
                    for _k in ("arc_material_key", "arc_gt_texture_bindings"):
                        if _k in obj:
                            del obj[_k]
                if "arc_material_mi_unresolved" in obj:
                    del obj["arc_material_mi_unresolved"]
                rebound.add(obj.name)
    print(f"Arc Pak: default option swaps rebound {len(rebound)} object(s) in {os.path.basename(outfit_dir)}")
    return len(rebound)


def _show_skin_in_dropdown(context, entry, skin_json: str) -> None:
    """Point the per-part dropdown at the colourway's MI.

    Display only: ``batch_skin_json`` stays the import source. Call before
    setting ``batch_skin_json`` because the dropdown's update hook clears it.
    """
    from ..Outfit_Accessories.properties import skin_choice_for_json, get_confirm_dialog_cache
    # Only while the confirm dialog is open. Batch runs apply presets in turn;
    # a mirrored pick would leak into later colourways that skip this part.
    if get_confirm_dialog_cache(context) is None:
        return
    try:
        choice = skin_choice_for_json(context, entry, skin_json)
        if choice is not None and entry.skin_choice != choice:
            entry.skin_choice = choice
    except Exception as exc:
        print(f"Arc Asset Importer: Could not show colourway skin in dropdown: {exc}")


def preview_outfit_preset_skins(context, preset_json_path: str) -> int:
    """Per-part skin assignment only (confirm dialog colourway ticks).

    Skips apply_outfit_preset's scene visibility and material swaps, which act
    on already imported objects.
    """
    from . import pak_extract
    scene = context.scene
    with pak_extract.batched_material_hierarchy(scene):
        meshes = [textures._fs_abspath(bpy.path.abspath(entry.psk_path)) for entry in scene.arc_psk_entries]
        pak_extract.ensure_cached_mesh_material_metadata(scene, meshes)
        return _apply_outfit_preset(context, preset_json_path)


def _apply_outfit_preset(context, preset_json_path: str) -> int:
    # Do not route authored batch choices through ``skin_choice``. It is a
    # dynamic EnumProperty whose IDs are the scanned filenames; ArcPak may expose
    # ``MI_*.arc-material.json`` there while DA_OI references ``MI_*.json``.
    # Blender rejects the latter as an enum value and silently leaves the entry
    # on NONE (the default skin). A string override keeps the exact authored path.
    bindings = parse_outfit_preset_bindings(preset_json_path)
    preset_map = parse_outfit_preset(preset_json_path)
    mi_stems_from_preset = list(preset_map.values()) if preset_map else []
    if not mi_stems_from_preset:
        mi_stems_from_preset = _mi_stems_from_colourway_json(preset_json_path)

    updated = 0
    for entry in context.scene.arc_psk_entries:
        try:
            entry.batch_skin_json = ""
            entry["arc_outfit_material_bindings"] = "{}"
            entry["arc_unused_outfit_material_bindings"] = "[]"
        except Exception:
            pass
        psk_path = bpy.path.abspath(entry.psk_path)
        part_key = get_part_key(psk_path) or get_characters_rel_key(psk_path)
        part_key_norm = utils.normalize_part_key(part_key) if part_key else ""
        material_ref = bindings.get(part_key_norm, "")
        resolved_bindings = resolve_outfit_preset_material_bindings(preset_json_path, psk_path)
        entry["arc_outfit_material_bindings"] = json.dumps(resolved_bindings, sort_keys=True)
        entry["arc_unused_outfit_material_bindings"] = json.dumps(getattr(resolved_bindings, "unused_native_bindings", []), sort_keys=True)
        from .materials.dispatch import _mi_uses_layered_character_profile
        layered = {path for path in resolved_bindings.values() if _mi_uses_layered_character_profile(path)}
        skin_json = next(iter(layered)) if len(layered) == 1 else resolved_bindings.get("0", "")
        secondary_only = bool(resolved_bindings) and not skin_json
        mi_stem = os.path.basename(skin_json).split(".", 1)[0] if skin_json else ""

        # Prefer the full Unreal package reference, matching ARC Texturer's
        # part-and-slot-aware resolution. Basename matching remains a fallback
        # for exports whose referenced MI has not been extracted yet.
        if material_ref and not skin_json:
            mi_stem = material_ref.rsplit("/", 1)[-1].split(".", 1)[0]
            try:
                skin_json = textures.find_asset_from_object_path(material_ref, ".json")
                skin_json = textures.resolve_part_mi_json(skin_json, mi_stem) if skin_json else ""
            except Exception:
                skin_json = ""

        # Cosmetic fallback: any MI stem from the colourway that resolves beside this mesh
        if not secondary_only and not skin_json and not mi_stem:
            mi_stem = next((stem for pk, stem in preset_map.items()
                            if part_key_norm and utils.normalize_part_key(pk) == part_key_norm), "")
        if not secondary_only and not skin_json and not mi_stem:
            for stem in mi_stems_from_preset:
                if textures.find_skin_json_for_mi_stem(psk_path, stem):
                    mi_stem = stem
                    break

        if not skin_json and mi_stem:
            skin_json = textures.find_skin_json_for_mi_stem(psk_path, mi_stem)
        if skin_json:
            _show_skin_in_dropdown(context, entry, skin_json)
            try:
                entry.batch_skin_json = os.path.abspath(skin_json)
                updated += 1
            except Exception as e:
                print(f"Arc Asset Importer: Could not set exact colourway MI for '{part_key}': {e}")
    return updated


def parse_outfit_preset_slot_bindings(json_path: str) -> dict:
    """Preserve exact DA_VP identity and material-slot selectors from modifiers."""
    result = {}
    data = _load_ue_json(json_path)
    if not data:
        return result
    for entry in utils.ue_export_entries(data):
        if entry.get("Type") not in _COLOURWAY_MOD_TYPES:
            continue
        props = entry.get("Properties") or {}
        material = _soft_asset_path(props.get("Material"))
        part = _soft_asset_path(props.get("Part"))
        if not material or not part:
            continue
        part_key = get_part_key_from_asset_path(part) or get_characters_rel_key(part)
        if not part_key:
            continue
        key = props.get("MaterialOverrideKey")
        slot = key if key is not None and key != "" else props.get("MaterialSlot", 0)
        if isinstance(slot, dict):
            slot = slot.get("Name") or slot.get("Value")
        if isinstance(slot, bool) or slot is None:
            raise ValueError("Invalid authored material slot selector: " + repr(slot))
        if isinstance(slot, int) or str(slot).isdigit():
            slot = int(slot)
        result.setdefault(utils.normalize_part_key(part_key), []).append({
            "part": part, "slot": slot, "material": material,
            "explicit_slot": "MaterialSlot" in props or "MaterialOverrideKey" in props,
            "material_slot": props.get("MaterialSlot"), "material_override_key": props.get("MaterialOverrideKey")})
    return result


def parse_outfit_preset_bindings(json_path: str) -> dict:
    """Compatibility primary part binding; secondary section overrides stay separate."""
    result = {}
    for part, records in parse_outfit_preset_slot_bindings(json_path).items():
        primary = {record["material"] for record in records if record["slot"] == 0}
        if len(primary) > 1:
            # Exact per-mesh API resolves distinct visual-part variants. The
            # compatibility map cannot represent them and must not pick one.
            continue
        if primary:
            result[part] = next(iter(primary))
    return result


def parse_outfit_preset(json_path: str) -> dict:
    slots = parse_outfit_preset_slot_bindings(json_path)
    result = {}
    for part, reference in parse_outfit_preset_bindings(json_path).items():
        original = get_part_key_from_asset_path(slots[part][0]["part"]) or get_characters_rel_key(slots[part][0]["part"])
        result[original] = reference.rsplit("/", 1)[-1].split(".", 1)[0]
    return result


def _outfit_package_key(path):
    text = str(path or "").replace("\\", "/")
    for boundary in ("/PioneerGame/Content/", "/RemappedPlugins/Content/", "/Game/"):
        offset = text.casefold().find(boundary.casefold())
        if offset >= 0:
            text = text[offset + len(boundary):]
            break
    leaf = text.rsplit("/", 1)[-1]
    if "." in leaf:
        text = text[:-len(leaf)] + leaf.split(".", 1)[0]
    return text.strip("/").casefold()


def _merge_outfit_slot_records(records, section_names=()):
    """Merge exact matching visual-part records without a last-wins rule."""
    result = {}
    for record in records:
        selector = record["slot"]
        if isinstance(selector, int):
            index = selector
        else:
            matches = [i for i, name in enumerate(section_names) if str(name).casefold() == str(selector).casefold()]
            if len(matches) != 1:
                raise ValueError("Authored material key has no unique native section: " + str(selector))
            index = matches[0]
        if index < 0 or (section_names and index >= len(section_names)):
            raise ValueError("Authored material slot exceeds native section count: " + str(index))
        previous = result.get(str(index))
        if previous and _outfit_package_key(previous) != _outfit_package_key(record["material"]):
            raise ValueError("Distinct materials bind the same exact visual-part slot: " + str(index))
        result[str(index)] = record["material"]
    return result


def resolve_outfit_preset_material_bindings(json_path: str, mesh_path: str) -> dict:
    """Resolve exact DA_VP.Mesh + selector to current section-indexed MI files."""
    mesh_path = textures._fs_abspath(mesh_path)
    import threading
    if threading.current_thread() is threading.main_thread():
        scene = getattr(getattr(globals().get('bpy'), "context", None), "scene", None)
        if scene is not None:
            from . import pak_extract
            pak_extract.ensure_cached_mesh_material_metadata(scene, [mesh_path])
    part = get_part_key(mesh_path) or get_characters_rel_key(mesh_path)
    records = parse_outfit_preset_slot_bindings(json_path).get(utils.normalize_part_key(part), [])
    selected, unresolved_parts = [], []
    mesh_key = _outfit_package_key(mesh_path)
    for record in records:
        path = textures.find_asset_from_object_path(record["part"], ".json")
        data = _load_ue_json(path) if path else None
        mesh_refs = []
        for entry in utils.ue_export_entries(data) if data else []:
            props = entry.get("Properties") or {}
            reference = _soft_asset_path(props.get("Mesh") or props.get("StaticMesh"))
            if reference:
                mesh_refs.append(reference)
        if not mesh_refs:
            unresolved_parts.append(record)
        elif any(_outfit_package_key(reference) == mesh_key for reference in mesh_refs):
            selected.append(record)
    if not selected and records:
        # Legacy exports without DA_VP JSON retain only an unambiguous main MI.
        unique = {record["material"] for record in records if record["slot"] == 0}
        if len(unique) == 1 and all(record["slot"] == 0 for record in records) and unresolved_parts:
            selected = unresolved_parts
        elif unresolved_parts:
            raise ValueError("Missing exact visual-part mesh metadata for slot-aware preset: " + part)
    if not selected:
        return {}
    sections = []
    native_layout = None
    if str(mesh_path).casefold().endswith(".uemodel") and os.path.isfile(mesh_path):
        from .uemodel_import import parse_uemodel
        _, _, lods = parse_uemodel(mesh_path)
        sections = [row[0] for row in lods[0].materials]
        from ..Outfit_Accessories.native_material_slots import load_native_material_layout
        native_layout = load_native_material_layout(mesh_path, lods[0].name, lods[0].materials)
    else:
        from .materials.common import _parse_sk_material_slots
        sections = [row[0] for row in _parse_sk_material_slots(mesh_path)]
    if native_layout is not None:
        from ..Outfit_Accessories.native_material_slots import resolve_native_slot_records, ResolvedMaterialBindings
        references = resolve_native_slot_records(selected, native_layout)
        result = ResolvedMaterialBindings(unused_native_bindings=references.unused_native_bindings)
        if references.unused_native_bindings:
            utils.get_logger().info("Retained unused native material bindings for %s: %s",
                                    os.path.basename(mesh_path), references.unused_native_bindings)
    else:
        # Legacy caches retain strict section-range validation; never waive an
        # out-of-range selector without exact native material-table metadata.
        references = _merge_outfit_slot_records(selected, sections)
        result = {}
    for index, reference in references.items():
        path = textures.find_asset_from_object_path(reference, ".json")
        if not path or not os.path.isfile(path):
            raise FileNotFoundError("Missing exact preset material: " + reference)
        result[index] = os.path.abspath(path)
    if threading.current_thread() is threading.main_thread():
        scene = getattr(getattr(globals().get('bpy'), "context", None), "scene", None)
        if scene is not None:
            from . import pak_extract
            pak_extract.ensure_cached_material_hierarchy(scene, material_paths=list(result.values()))
    return result


def stamp_outfit_preset_material_bindings(obj, json_path, mesh_path):
    bindings = resolve_outfit_preset_material_bindings(json_path, mesh_path)
    obj["arc_outfit_material_bindings"] = json.dumps(bindings, sort_keys=True)
    obj["arc_unused_outfit_material_bindings"] = json.dumps(getattr(bindings, "unused_native_bindings", []), sort_keys=True)
    obj["arc_outfit_material_preset"] = os.path.abspath(json_path)
    return bindings


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


