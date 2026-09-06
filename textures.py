"""
Texture and material parameter parsing for the Arc Raiders Importer
"""

import os
import re
import json
import bpy
from . import utils

# ---------------------------------------------------------------------------
# Colour key generation (loops instead of hardcoded lists)
# ---------------------------------------------------------------------------

def generate_colour_keys(num_zones=8):
    """Generate all colour-related parameter names dynamically."""
    keys = []
    
    # Pattern colors for each zone
    for z in range(1, num_zones + 1):
        keys.extend([
            f"{z}_PatternColorA",
            f"{z}_PatternColorB",
            f"{z}_PatternColorC",
            f"{z}_PatternMask",
            f"{z}_PatternSwatch",
            f"{z}_PatternSwatchMask",
            f"{z}_ColorMaskSwatch",
        ])
    
    # Core colour keys (only 3 zones for these)
    keys.extend(["ColorA", "ColorB", "ColorC", "ColorA2", "ColorB2", "ColorC2", "WetTint", "SnowColor"])
    
    # Overlay colours for each zone
    for z in range(1, num_zones + 1):
        keys.extend([
            f"{z}_EdgeColorOverlay",
            f"{z}_CreaseColorOverlay",
            f"{z}_BaseColorOverlay",
            f"{z}_EmissiveColorOverlay",
        ])
    
    return keys

# Cache the generated keys for performance
_COLOUR_KEYS = generate_colour_keys(8)

def get_colour_keys():
    """Return the full list of colour keys."""
    return _COLOUR_KEYS

def is_white(rgba, tol=1e-4):
    return (abs(rgba[0] - 1.0) < tol and abs(rgba[1] - 1.0) < tol and abs(rgba[2] - 1.0) < tol)

def is_black(rgba, tol=1e-4):
    return (abs(rgba[0]) < tol and abs(rgba[1]) < tol and abs(rgba[2]) < tol)

def is_pure_red(rgba, tol=1e-4):
    return (abs(rgba[0] - 1.0) < tol and abs(rgba[1]) < tol and abs(rgba[2]) < tol)

def skip_colour(rgba):
    return is_white(rgba) or is_black(rgba) or is_pure_red(rgba)

# ---------------------------------------------------------------------------
# Skin scanning / clothing MI identity
# ---------------------------------------------------------------------------

# Corrupt FModel multithread dumps often write the wrong asset under MI_*.json
# (BodySetup, DialogueBucket, or another character's MIC). Cosmetic Viewer
# Outfits/<Name>/Parts/ trees frequently keep the good copy — prefer those when
# Characters/Assets identity fails.
_CLOTHING_MI_INDEX = None  # stem.lower() -> [paths], Outfits first
_CLOTHING_MI_INDEX_ROOT = None
# Session caches for batch clothing import (cleared via clear_clothing_path_caches).
_CLOTHING_MI_PARSE_CACHE: dict[str, dict] = {}
_PART_MI_RESOLVE_CACHE: dict[tuple[str, str], str] = {}
_OBJECT_PATH_ASSET_CACHE: dict[tuple[str, str], str] = {}
_NULL_OBJECT_PATH_MARKERS = frozenset({
    "t_decal_null",
    "t_decal_null_m",
    "t_decal_null_c",
    "t_decal_null_n",
    "t_decal_null_d",
})


def _mi_entry_stem(entry: dict) -> str:
    name = (entry.get("Name") or "").strip()
    if "." in name:
        name = name.split(".", 1)[0]
    package = (entry.get("Package") or "").replace("\\", "/").strip()
    pkg_leaf = package.rsplit("/", 1)[-1] if package else ""
    if "." in pkg_leaf:
        pkg_leaf = pkg_leaf.split(".", 1)[0]
    return name or pkg_leaf


def _load_clothing_mi_entry(json_path: str):
    """Return (entry_dict_or_None, data) for a clothing MI JSON path."""
    if not json_path or not os.path.isfile(json_path):
        return None, None
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None, None
    entry = utils.first_ue_export(data, "MaterialInstanceConstant")
    if not entry:
        entry = utils.first_ue_export(data, "MaterialInstance")
    if not entry or entry.get("Type") not in ("MaterialInstanceConstant", "MaterialInstance"):
        return None, data
    return entry, data


def _mic_parent_name(entry: dict) -> str:
    props = (entry or {}).get("Properties") or {}
    parent = props.get("Parent") or {}
    if not isinstance(parent, dict):
        return ""
    return f"{parent.get('ObjectName') or ''} {parent.get('ObjectPath') or ''}"


def _mic_is_layered_character(entry: dict) -> bool:
    """True for MI_Character_Layered_* instances (need vectors for ColorA / patterns / decals)."""
    blob = _mic_parent_name(entry).lower()
    return "layered" in blob or "mi_character_" in blob


def _mic_has_instance_payload(entry: dict) -> bool:
    """Color/pattern/decal dumps live on Vector + Texture parameter arrays."""
    props = (entry or {}).get("Properties") or {}
    if not isinstance(props, dict):
        return False
    return bool(props.get("VectorParameterValues") or props.get("TextureParameterValues"))


def clothing_mi_json_is_valid(json_path: str, expected_stem: str = "") -> bool:
    """True when path is a real MIC whose Name matches the filename stem.

    Rejects BodySetup/mesh dumps and cross-wired MIC bodies (e.g. Janitor MIC
    saved as MI_Goalie_Boots_Leather_CyanWhite.json).

    Current Characters/Assets FModel JSON often keeps Name + 388 scalars but
    omits VectorParameterValues / TextureParameterValues (and StaticParameters).
    Those dumps match the stem yet import with debug ColorMask_XYZ and no
    patterns/decals — treat Layered instances without vectors as invalid so
    resolve_clothing_mi_json can pick the full Outfits/Parts copy.
    """
    stem = (expected_stem or os.path.splitext(os.path.basename(json_path or ""))[0]).strip()
    if "." in stem:
        stem = stem.split(".", 1)[0]
    if not stem or not stem.lower().startswith("mi_"):
        return False
    entry, _data = _load_clothing_mi_entry(json_path)
    if not entry:
        return False
    body_stem = _mi_entry_stem(entry)
    # Legacy wrappers with no Name/Package cannot be proven wrong.
    if not body_stem:
        return _mic_has_instance_payload(entry)
    if body_stem.lower() != stem.lower():
        return False
    if _mic_is_layered_character(entry) and not _mic_has_instance_payload(entry):
        return False
    return True


def _clothing_mi_stem_index() -> dict:
    """Basename index of MI_*.json under Outfits/ + Characters/Assets."""
    global _CLOTHING_MI_INDEX, _CLOTHING_MI_INDEX_ROOT
    root = ""
    try:
        root = os.path.normcase(os.path.normpath(utils.get_pioneer_root() or ""))
    except Exception:
        root = ""
    if _CLOTHING_MI_INDEX is not None and root == _CLOTHING_MI_INDEX_ROOT:
        return _CLOTHING_MI_INDEX

    index: dict[str, list[str]] = {}
    search_roots: list[str] = []
    pioneer = utils.get_pioneer_root() or ""
    if pioneer and os.path.isdir(pioneer):
        outfits = os.path.join(pioneer, "Outfits")
        if os.path.isdir(outfits):
            search_roots.append(outfits)
        chars = os.path.join(
            pioneer, "PioneerGame", "Content", "Pioneer", "Characters", "Assets",
        )
        if os.path.isdir(chars):
            search_roots.append(chars)
    try:
        for content_dir in utils.get_content_dirs() or []:
            chars = os.path.join(content_dir, "Pioneer", "Characters", "Assets")
            if os.path.isdir(chars):
                search_roots.append(chars)
            # Sibling Outfits next to PioneerGame
            sibling_outfits = os.path.join(os.path.dirname(content_dir), "Outfits")
            if not os.path.isdir(sibling_outfits):
                sibling_outfits = os.path.join(
                    os.path.dirname(os.path.dirname(content_dir)), "Outfits",
                )
            if os.path.isdir(sibling_outfits):
                search_roots.append(sibling_outfits)
    except Exception:
        pass

    seen_roots: set[str] = set()
    for search_root in search_roots:
        key = os.path.normcase(os.path.normpath(search_root))
        if key in seen_roots or not os.path.isdir(search_root):
            continue
        seen_roots.add(key)
        try:
            for walk_root, _dirs, files in os.walk(search_root):
                for fname in files:
                    if not _is_mi_json_filename(fname):
                        continue
                    stem = fname[:-5]
                    path = os.path.join(walk_root, fname)
                    index.setdefault(stem.lower(), []).append(path)
        except OSError:
            continue

    def _rank(path: str) -> tuple:
        pl = path.replace("\\", "/").lower()
        # Cosmetic Viewer Outfits export is the reliable colorway source when
        # Characters/Assets MICs are cross-wired multithread dumps.
        if "/outfits/" in pl:
            return (0, len(path))
        if "/characters/assets/" in pl:
            return (1, len(path))
        return (2, len(path))

    for paths in index.values():
        paths.sort(key=_rank)

    _CLOTHING_MI_INDEX = index
    _CLOTHING_MI_INDEX_ROOT = root
    return index


def resolve_clothing_mi_json(json_path: str, expected_stem: str = "") -> str:
    """Return an identity-valid clothing MIC path, searching Outfits if needed."""
    if not json_path:
        return ""
    try:
        abs_path = os.path.abspath(bpy.path.abspath(json_path))
    except Exception:
        abs_path = os.path.abspath(json_path)
    stem = (expected_stem or os.path.splitext(os.path.basename(abs_path))[0]).strip()
    if "." in stem:
        stem = stem.split(".", 1)[0]
    if clothing_mi_json_is_valid(abs_path, stem):
        return abs_path

    # Local file wrong/missing — find another copy of the same stem.
    for cand in _clothing_mi_stem_index().get(stem.lower(), []):
        if os.path.normcase(os.path.normpath(cand)) == os.path.normcase(abs_path):
            continue
        if clothing_mi_json_is_valid(cand, stem):
            print(
                f"Arc Raiders PSK Importer: clothing MI identity repair — "
                f"'{os.path.basename(abs_path)}' invalid at '{abs_path}', "
                f"using '{cand}'"
            )
            return os.path.abspath(cand)
    if abs_path and os.path.isfile(abs_path):
        print(
            f"Arc Raiders PSK Importer: rejecting invalid clothing MI JSON "
            f"'{abs_path}' (not a matching MaterialInstanceConstant)"
        )
    return ""


def invalidate_clothing_mi_index() -> None:
    """Drop the Outfits/Characters MI basename index (tests / root change)."""
    global _CLOTHING_MI_INDEX, _CLOTHING_MI_INDEX_ROOT
    _CLOTHING_MI_INDEX = None
    _CLOTHING_MI_INDEX_ROOT = None


def clear_clothing_path_caches() -> None:
    """Drop session caches for MI parse / part resolve / ObjectPath assets."""
    _CLOTHING_MI_PARSE_CACHE.clear()
    _PART_MI_RESOLVE_CACHE.clear()
    _OBJECT_PATH_ASSET_CACHE.clear()


def _norm_cache_path(path: str) -> str:
    if not path:
        return ""
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))
    except Exception:
        return os.path.normcase(os.path.normpath(path))


def _object_path_is_null_stub(obj_path: str) -> bool:
    """True for engine null decal stubs (EmbarkScript / T_Decal_Null_*)."""
    if not obj_path:
        return False
    path_l = obj_path.replace("\\", "/").lower()
    leaf = path_l.rsplit("/", 1)[-1]
    if "." in leaf:
        leaf = leaf.split(".", 1)[0]
    if leaf in _NULL_OBJECT_PATH_MARKERS or "decal_null" in leaf:
        return True
    if "/embarkscript/" in path_l and "null" in leaf:
        return True
    return False


def _is_mi_json_filename(fname: str) -> bool:
    fl = (fname or "").lower()
    if not fl.startswith("mi_") or not fl.endswith(".json"):
        return False
    # Skip sidecar dumps next to the real MIC.
    if ".palette." in fl or ".metadata." in fl:
        return False
    return True


def _load_mi_json_data(json_path: str):
    """Load MI JSON; return parsed object or None."""
    if not json_path or not os.path.isfile(json_path):
        return None
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _is_thin_fmodel_mi_data(data) -> bool:
    """True for compact FModel dumps: {Textures, Parameters} without MIC export."""
    if not isinstance(data, dict):
        return False
    if "Textures" not in data and "Parameters" not in data:
        return False
    # Full MIC list dumps are not "thin"
    if isinstance(data.get("Type"), str) and "MaterialInstance" in data["Type"]:
        return False
    return True


def _thin_mi_has_usable_params(data: dict) -> bool:
    """Thin dump is usable as a shared part MI when it carries colours and/or textures."""
    if not _is_thin_fmodel_mi_data(data):
        return False
    params = data.get("Parameters") or {}
    colors = params.get("Colors") or {}
    textures = data.get("Textures") or {}
    return bool(colors) or bool(textures)


def part_mi_json_is_usable(json_path: str, expected_stem: str = "") -> bool:
    """True when path can drive part materials: identity-valid MIC or thin dump with params.

    Mesh folders like PonchoFringe often ship only ``MI_SimplePBR_*.json`` thin dumps
    (ColorA/B/C shared across colorways) and **no** Skins/ tree. Those must still be used.
    """
    if clothing_mi_json_is_valid(json_path, expected_stem):
        return True
    data = _load_mi_json_data(json_path)
    return _thin_mi_has_usable_params(data) if data is not None else False


def resolve_part_mi_json(json_path: str, expected_stem: str = "") -> str:
    """Prefer identity-valid MIC; else keep a usable thin dump beside the mesh."""
    if not json_path:
        return ""
    try:
        abs_path = os.path.abspath(bpy.path.abspath(json_path))
    except Exception:
        abs_path = os.path.abspath(json_path)
    cache_key = (_norm_cache_path(abs_path), (expected_stem or "").strip().lower())
    if cache_key[0] and cache_key in _PART_MI_RESOLVE_CACHE:
        return _PART_MI_RESOLVE_CACHE[cache_key]
    repaired = resolve_clothing_mi_json(abs_path, expected_stem)
    if repaired:
        result = repaired
    elif part_mi_json_is_usable(abs_path, expected_stem):
        result = abs_path
    else:
        result = ""
    if cache_key[0]:
        _PART_MI_RESOLVE_CACHE[cache_key] = result
    return result


def scan_skins(psk_path: str, manual_folder: str = "") -> list:
    """Scan for all skin/colour options for a part."""
    manual_folder = bpy.path.abspath(manual_folder) if manual_folder else ""
    if manual_folder and os.path.isdir(manual_folder):
        results = []
        for skin_name in get_all_skin_dirs(manual_folder):
            skin_dir = os.path.join(manual_folder, skin_name)
            path = _pick_valid_mi_json_in_dir(skin_dir)
            if path:
                results.append((skin_name, path))
        # Manual folder may itself be a part root with sibling colourway dirs.
        for skin_name, skin_dir in iter_part_colorway_dirs(manual_folder):
            path = _pick_valid_mi_json_in_dir(skin_dir)
            if path:
                results.append((skin_name, path))
        results.extend(_scan_mi_jsons_in_folder(manual_folder))
        return results
    
    results = []
    skins_folder = get_skins_folder(psk_path)
    if skins_folder:
        all_dirs = get_all_skin_dirs(skins_folder)
        for skin_name in all_dirs:
            skin_dir = os.path.join(skins_folder, skin_name)
            path = _pick_valid_mi_json_in_dir(skin_dir)
            if path:
                results.append((skin_name, path))
    # Sibling colourways (FoxHat/ArcticFox…) when there is no Skins/ tree.
    if not skins_folder:
        part_folder = os.path.dirname(_fs_abspath(psk_path))
        for skin_name, skin_dir in iter_part_colorway_dirs(part_folder):
            path = _pick_valid_mi_json_in_dir(skin_dir)
            if path:
                results.append((skin_name, path))
    results.extend(_scan_main_folder_skins(psk_path))
    return results

def _fs_abspath(path: str) -> str:
    """Absolute filesystem path without requiring bpy (worker-safe when already abs)."""
    if not path:
        return ""
    try:
        if os.path.isabs(path):
            return os.path.normpath(path)
    except Exception:
        pass
    try:
        return os.path.normpath(bpy.path.abspath(path))
    except Exception:
        return os.path.normpath(os.path.abspath(path))


def get_skins_folder(psk_path: str):
    part_folder = os.path.dirname(_fs_abspath(psk_path))
    skins_folder = os.path.join(part_folder, "Skins")
    return skins_folder if os.path.isdir(skins_folder) else None

def get_all_skin_dirs(skins_folder: str) -> list:
    try:
        return [
            d for d in os.listdir(skins_folder)
            if os.path.isdir(os.path.join(skins_folder, d))
            and "persistence" not in d.lower()
        ]
    except OSError:
        return []


# Sibling colourway folders next to the PSK (FoxHat/ArcticFox, FoxHatCards/BlackFox…)
# — not under Skins/, not mesh/texture sidecars.
_PART_COLORWAY_SKIP_DIRS = frozenset({
    "skins", "textures", "materials", "mesh", "meshes", "lod", "lods",
    "physics", "resources", "source", "export", "exports",
})


def iter_part_colorway_dirs(part_folder: str) -> list:
    """Immediate subdirs of a part folder that hold MI_*.json colourways.

    Huntsman FoxHat / FoxHatCards ship ArcticFox, BlackFox, Racoon beside the mesh
    instead of under ``Skins/``. Returns ``(skin_name, abs_dir)`` pairs.
    """
    part_folder = _fs_abspath(part_folder) if part_folder else ""
    if not part_folder or not os.path.isdir(part_folder):
        return []
    results = []
    try:
        names = sorted(os.listdir(part_folder))
    except OSError:
        return []
    for name in names:
        if name.startswith("."):
            continue
        if name.lower() in _PART_COLORWAY_SKIP_DIRS:
            continue
        if "persistence" in name.lower():
            continue
        full = os.path.join(part_folder, name)
        if not os.path.isdir(full):
            continue
        try:
            has_mi = any(_is_mi_json_filename(f) for f in os.listdir(full))
        except OSError:
            has_mi = False
        if has_mi:
            results.append((name, full))
    return results

def _scan_mi_jsons_in_folder(folder: str) -> list:
    try:
        mi_files = sorted(
            f for f in os.listdir(folder)
            if _is_mi_json_filename(f) and "persistence" not in f.lower()
        )
    except OSError:
        return []
    if not mi_files:
        return []
    stems = [os.path.splitext(f)[0] for f in mi_files]
    common = os.path.commonprefix(stems)
    if common not in stems:
        if "_" in common:
            common = common.rsplit("_", 1)[0]
        else:
            common = ""
    results = []
    for fname, stem in zip(mi_files, stems):
        # Accept thin mesh-folder dumps (e.g. MI_SimplePBR_PonchoFringe) when there
        # is no Skins/ tree — colours are shared across outfit colorways.
        path = resolve_part_mi_json(os.path.join(folder, fname))
        if not path:
            continue
        if common and stem.startswith(common):
            suffix = stem[len(common):].lstrip("_")
            skin_name = suffix if suffix else "__DEFAULT__"
        else:
            skin_name = stem
        results.append((skin_name, path))
    return results

def _scan_main_folder_skins(psk_path: str) -> list:
    return _scan_mi_jsons_in_folder(os.path.dirname(bpy.path.abspath(psk_path)))

def scan_base_skin_textures(psk_path: str, selected_skin_name: str = "", manual_folder: str = "") -> list:
    # Use filesystem abspath so ThreadPool IO workers never touch bpy.
    manual_folder = _fs_abspath(manual_folder) if manual_folder else ""
    skins_folder = manual_folder if (manual_folder and os.path.isdir(manual_folder)) else get_skins_folder(psk_path)
    if not skins_folder:
        return []
    all_dirs = get_all_skin_dirs(skins_folder)
    if not all_dirs:
        if manual_folder:
            try:
                return [os.path.join(skins_folder, f)
                        for f in sorted(os.listdir(skins_folder))
                        if f.lower().endswith(".png")]
            except OSError:
                return []
        return []
    default_dir = get_default_skin_dir(all_dirs)
    scan_dirs = [default_dir] if default_dir else [d for d in all_dirs if not is_variant(d, all_dirs)]
    pngs = []
    for skin_name in scan_dirs:
        skin_dir = os.path.join(skins_folder, skin_name)
        try:
            for fname in sorted(os.listdir(skin_dir)):
                if fname.lower().endswith(".png"):
                    pngs.append(os.path.join(skin_dir, fname))
        except OSError:
            pass
    return pngs

def is_variant(name: str, all_dirs: list) -> bool:
    return any(name != other and name.startswith(other) for other in all_dirs)

def get_default_skin_dir(all_dirs: list) -> str:
    if not all_dirs:
        return ""
    non_variants = [d for d in all_dirs if not is_variant(d, all_dirs)]
    if not non_variants:
        return sorted(all_dirs)[0]
    if len(non_variants) == 1:
        return non_variants[0]
    counts = {base: sum(1 for d in all_dirs if d != base and d.startswith(base)) for base in non_variants}
    best = max(counts.items(), key=lambda kv: kv[1])
    if best[1] > 0:
        return best[0]
    return sorted(non_variants)[0]

def name_suggests_fur(name: str) -> bool:
    """True when a slot / MI / folder leaf looks like Arc fur (Fur, FurLOD, FurShells…)."""
    if not name:
        return False
    leaf = str(name).replace("\\", "/").rsplit("/", 1)[-1].lower()
    if leaf.endswith(".json"):
        leaf = leaf[:-5]
    # Match Fur / FurLOD / FurShells as a substring token (ShoulderFur, UpperBody_Fur…).
    return bool(re.search(r"fur(lod|shells)?", leaf))


def is_fur_mi(json_path: str) -> bool:
    """Generic fur MI detection from filename, parent, FurMask textures, or fur scalars."""
    if not json_path or not os.path.isfile(json_path):
        return False
    if name_suggests_fur(json_path):
        return True
    data = _load_mi_json_data(json_path)
    if data is None:
        return False
    # Full MIC export
    try:
        entry = None
        if isinstance(data, list):
            for e in data:
                if isinstance(e, dict) and e.get("Type") in (
                    "MaterialInstanceConstant", "MaterialInstance",
                ):
                    entry = e
                    break
        elif isinstance(data, dict) and data.get("Type") in (
            "MaterialInstanceConstant", "MaterialInstance",
        ):
            entry = data
        if isinstance(entry, dict):
            props = entry.get("Properties") or {}
            parent = props.get("Parent") or {}
            parent_s = (
                str(parent.get("ObjectName", "") or "")
                + " "
                + str(parent.get("ObjectPath", "") or "")
            ).lower()
            if "m_furshells" in parent_s or "m_furlod" in parent_s or "/m_fur" in parent_s:
                return True
            for tp in props.get("TextureParameterValues") or []:
                pname = str((tp.get("ParameterInfo") or {}).get("Name", "") or "").lower()
                pv = tp.get("ParameterValue") or {}
                if not isinstance(pv, dict):
                    continue
                tpath = str(pv.get("ObjectPath", "") or pv.get("ObjectName", "") or "").lower()
                if "furmask" in pname or "t_furmask" in tpath:
                    return True
            scalar_names = {
                str((sp.get("ParameterInfo") or {}).get("Name", "") or "")
                for sp in (props.get("ScalarParameterValues") or [])
                if isinstance(sp, dict)
            }
            if "FurTiling" in scalar_names and "Length" in scalar_names:
                return True
    except Exception:
        pass
    # Thin FModel dump
    if _is_thin_fmodel_mi_data(data):
        textures_map = data.get("Textures") or {}
        for key, path in textures_map.items():
            blob = f"{key} {path}".lower()
            if "furmask" in blob or "t_furmask" in blob:
                return True
        scalars = (data.get("Parameters") or {}).get("Scalars") or {}
        if "FurTiling" in scalars and "Length" in scalars:
            return True
    return False


def is_fur_part(psk_path: str) -> bool:
    """True for dedicated fur meshes (ShoulderFur, Loomer/Fur…) — not clothing with fur slots.

    Clothing parts that also carry fur keep occlusion ColorMask and stay ``clothing``.
    """
    if not psk_path:
        return False
    folder = os.path.dirname(bpy.path.abspath(psk_path))
    if folder_has_occlusion_png(folder):
        return False
    norm = psk_path.replace("\\", "/").lower()
    base = os.path.basename(norm)
    folder_leaf = os.path.basename(folder.rstrip("/\\")).lower()
    if name_suggests_fur(base) or name_suggests_fur(folder_leaf) or "/fur/" in norm:
        return True
    try:
        mi_names = [
            f for f in os.listdir(folder)
            if _is_mi_json_filename(f) and "persistence" not in f.lower()
        ]
    except OSError:
        mi_names = []
    if not mi_names:
        return False
    fur_count = sum(1 for f in mi_names if is_fur_mi(os.path.join(folder, f)))
    return fur_count > 0 and fur_count >= max(1, len(mi_names) - 1)


def _pick_valid_mi_json_in_dir(skin_dir: str, *, prefer_non_fur: bool = True) -> str:
    """First usable MI_*.json in a skin/part folder (MIC or thin dump; Outfits fallback)."""
    try:
        names = sorted(os.listdir(skin_dir))
    except OSError:
        return ""
    candidates = []
    for fname in names:
        if not _is_mi_json_filename(fname):
            continue
        path = os.path.join(skin_dir, fname)
        resolved = resolve_part_mi_json(path)
        if resolved:
            candidates.append(resolved)
    if not candidates:
        return ""
    if prefer_non_fur:
        for path in candidates:
            if not is_fur_mi(path):
                return path
    return candidates[0]


def get_base_skin_json(psk_path: str, manual_folder: str = "") -> str:
    """Resolve the MI JSON that drives colours for a part.

    Order: Skins/<default>/… when present, else any ``MI_*.json`` beside the mesh
    (including thin FModel dumps with Parameters.Colors — shared across colorways).
    Prefer non-fur clothing MIs when both shell and fur instances share a folder.
    """
    manual_folder = bpy.path.abspath(manual_folder) if manual_folder else ""
    skins_folder = manual_folder if (manual_folder and os.path.isdir(manual_folder)) else get_skins_folder(psk_path)
    if skins_folder:
        all_dirs = get_all_skin_dirs(skins_folder)
        if all_dirs:
            default_dir = get_default_skin_dir(all_dirs)
            search_dirs = [default_dir] if default_dir else all_dirs
            for skin_name in search_dirs:
                skin_dir = os.path.join(skins_folder, skin_name)
                path = _pick_valid_mi_json_in_dir(skin_dir, prefer_non_fur=True)
                if path:
                    return path
    # No Skins/ (or empty): use MI beside the PSK — PonchoFringe / shared SimplePBR.
    fallback_folder = skins_folder if manual_folder else os.path.dirname(bpy.path.abspath(psk_path))
    scanned = _scan_mi_jsons_in_folder(fallback_folder)
    non_fur = [(n, p) for n, p in scanned if not is_fur_mi(p)]
    pool = non_fur or scanned
    for skin_name, json_path in pool:
        if skin_name == "__DEFAULT__":
            return json_path
    if pool:
        return pool[0][1]
    return ""

# ---------------------------------------------------------------------------
# Texture role identification
# ---------------------------------------------------------------------------

def identify_texture(fname: str):
    stem = os.path.splitext(fname)[0].lower()
    if stem.endswith("_basecolor") or stem.endswith("_basecolour"):
        return "basecolor"
    if stem.endswith("_colormask") or stem.endswith("_colourmask"):
        return "colormask"
    if stem.endswith("_normal"):
        return "normal"
    if stem.endswith("occlusioncurvaturematerialid"):
        return "occlusion"
    return None

def base_skin_texture_group(fname: str) -> str:
    stem = os.path.splitext(fname)[0].lower()
    stem = re.sub(r"_\d+$", "", stem)
    if stem.endswith("_normals") or stem.endswith("_normal"):
        return "normals"
    if stem.endswith("_masks") or stem.endswith("_mask"):
        return "masks"
    return "other"

def folder_has_occlusion_png(folder: str) -> bool:
    try:
        for fname in os.listdir(folder):
            if fname.lower().endswith("occlusioncurvaturematerialid.png"):
                return True
    except OSError:
        pass
    return False

def find_texture_from_object_path(obj_path: str) -> str:
    return find_asset_from_object_path(obj_path, ".png")


def find_asset_from_object_path(obj_path: str, extension: str) -> str:
    """Resolve a /Game/... ObjectPath to a file under Pioneer Content (multi-root).

    Searches every Content dir from :func:`utils.get_content_dirs` so MapPlacements
    mesh-only trees still resolve MI/SM JSON from the sibling full FModel dump.
    """
    utils.invalidate_dir_caches_if_root_changed()
    if not obj_path:
        return ""
    # Engine null stubs — never BFS Content / EmbarkScript for these.
    if _object_path_is_null_stub(obj_path):
        return ""
    # UE dumps often use AssetName.AssetName — strip trailing instance suffix
    leaf = obj_path.split("/")[-1]
    if "." in leaf:
        obj_path = obj_path[: -len(leaf)] + leaf.split(".")[0]
    clean = obj_path
    if clean.startswith('/Game/'):
        rel = clean[len('/Game/'):]
    else:
        rel = clean.lstrip('/')
    if not extension.startswith('.'):
        extension = '.' + extension

    cache_key = (clean.replace("\\", "/").lower(), extension.lower())
    if cache_key in _OBJECT_PATH_ASSET_CACHE:
        return _OBJECT_PATH_ASSET_CACHE[cache_key]

    content_dirs = utils.get_content_dirs()
    if not content_dirs:
        root = utils.get_pioneer_root()
        cd = utils.find_content_dir(root) if root else ""
        if cd:
            content_dirs = [cd]

    rel_os = rel.replace('/', os.sep)
    for content_dir in content_dirs:
        candidate = os.path.join(content_dir, rel_os) + extension
        if os.path.isfile(candidate):
            _OBJECT_PATH_ASSET_CACHE[cache_key] = candidate
            return candidate

    # Fallback: folder search under pioneer root (legacy shallow resolve).
    root = utils.get_pioneer_root()
    rel_parts = rel.split('/')
    if root and len(rel_parts) > 1:
        dir_parts = rel_parts[:-1]
        filename = rel_parts[-1] + extension
        found_dir = utils.find_relative_dir(root, dir_parts)
        if found_dir:
            candidate = os.path.join(found_dir, filename)
            if os.path.isfile(candidate):
                _OBJECT_PATH_ASSET_CACHE[cache_key] = candidate
                return candidate
    _OBJECT_PATH_ASSET_CACHE[cache_key] = ""
    return ""


def mi_stem_from_material_ref(material_ref: dict) -> str:
    """Extract MI stem from a UE Material soft reference (ObjectPath / ObjectName)."""
    if not material_ref:
        return ""
    obj_path = material_ref.get("ObjectPath", "") or ""
    if obj_path:
        leaf = obj_path.split("/")[-1]
        return os.path.splitext(leaf)[0]
    obj_name = material_ref.get("ObjectName", "") or ""
    m = re.search(r"'([^']+)'", obj_name)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Skin JSON parsing
# ---------------------------------------------------------------------------

_TA_ID_SUFFIXES = (
    'BaseNormalID', 'MediumNormalID', 'EdgeNormalID', 'CreaseNormalID',
    'BaseRoughnessID', 'EdgeRoughnessID', 'CreaseRoughnessID',
    'CreaseMaskID', 'EdgeMaskID', 'ColorTextureID', 'PatternID',
)
_MI_KEEP_TOKENS = (
    "Roughness", "Metallic", "Specular", "BaseTextureStrength", "ColorSchemeBlend",
    "Wet", "Snow", "Emissive", "HueVariation", "PatternTiling",
)
_ZONE_SCALAR_SUFFIXES = (
    'BaseTextureStrength',
    'BaseColorMaskStrength',
    'EdgeColorMaskStrength',
    'CreaseColorMaskStrength',
    'ShadeAsCloth',
    'ColorSchemeBlend',
    'LayerMask',
    'MediumNormalStrength',
    'MediumNormalTiling',
    'EdgeNormalTiling',
    'CreaseNormalTiling',
    'ColorTextureTiling',
    'PatternTiling',
    'HueVariationStrength',
    'HueVariationTiling',
    'EdgeTextureStrength',
    'CreaseTextureStrength',
    'CreaseAmount',
    'EdgeAmount',
    'MaxWetAmount',
)


def load_mi_properties(json_path: str) -> dict:
    """Load an MI JSON once and return its Properties dict (or {}).

    Resolves corrupt Characters dumps to identity-valid Outfits Parts copies when
    available. Never returns Properties from a non-MIC export.
    """
    if not json_path:
        return {}
    resolved = resolve_clothing_mi_json(json_path)
    if not resolved:
        # Non-clothing MI callers (hair etc.) may still want strict local load.
        if not os.path.isfile(json_path):
            return {}
        entry, _data = _load_clothing_mi_entry(json_path)
        return (entry.get("Properties") or {}) if entry else {}
    try:
        entry, _data = _load_clothing_mi_entry(resolved)
        return (entry.get("Properties") or {}) if entry else {}
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to load MI JSON '{resolved}': {e}")
        return {}


def _colours_from_props(props: dict) -> dict:
    if not isinstance(props, dict):
        return {}
    colour_names = set(get_colour_keys())
    result = {}
    for param in props.get("VectorParameterValues") or []:
        if not isinstance(param, dict):
            continue
        name = (param.get("ParameterInfo") or {}).get("Name", "")
        if name not in colour_names:
            continue
        pv = param.get("ParameterValue") or {}
        if not isinstance(pv, dict):
            continue
        try:
            result[name] = (
                float(pv.get("R", 1.0)),
                float(pv.get("G", 1.0)),
                float(pv.get("B", 1.0)),
                float(pv.get("A", 1.0)),
            )
        except (TypeError, ValueError):
            continue
    return result


def _ta_ids_from_props(props: dict) -> dict:
    if not isinstance(props, dict):
        return {}
    result = {}
    for param in props.get("ScalarParameterValues") or []:
        if not isinstance(param, dict):
            continue
        name = (param.get("ParameterInfo") or {}).get("Name", "")
        for suffix in _TA_ID_SUFFIXES:
            m = re.match(r'^(\d+)_' + re.escape(suffix) + r'$', name)
            if m:
                try:
                    result[(m.group(1), suffix)] = int(float(param.get("ParameterValue", 0)))
                except (TypeError, ValueError):
                    pass
                break
    return result


def _zone_scalars_from_props(props: dict) -> dict:
    """Per-zone scalars such as BaseTextureStrength / MediumNormalStrength."""
    if not isinstance(props, dict):
        return {}
    result = {}
    for param in props.get("ScalarParameterValues") or []:
        if not isinstance(param, dict):
            continue
        name = (param.get("ParameterInfo") or {}).get("Name", "")
        for suffix in _ZONE_SCALAR_SUFFIXES:
            m = re.match(r'^(\d+)_' + re.escape(suffix) + r'$', name)
            if not m:
                continue
            try:
                result[(m.group(1), suffix)] = float(param.get("ParameterValue", 0))
            except (TypeError, ValueError):
                pass
            break
        if name in (
            "MaxWetAmount",
            "WetGloss",
            "WetGlossCloth",
            "OverallEmissive",
            "HueVariationStrength",
            "HueVariationTiling",
        ):
            try:
                result[name] = float(param.get("ParameterValue", 0))
            except (TypeError, ValueError):
                pass
    return result


def _mi_params_from_props(props: dict, known_colour_names: set) -> dict:
    result = {"scalars": [], "vectors": []}
    if not isinstance(props, dict):
        return result
    seen_scalar_names = set()
    for param in props.get("ScalarParameterValues") or []:
        if not isinstance(param, dict):
            continue
        name = (param.get("ParameterInfo") or {}).get("Name", "")
        if not name or name in seen_scalar_names or name.endswith("ID"):
            continue
        if not any(tok in name for tok in _MI_KEEP_TOKENS):
            continue
        try:
            value = float(param.get("ParameterValue", 0))
        except (TypeError, ValueError):
            continue
        seen_scalar_names.add(name)
        result["scalars"].append((name, value))
    seen_vector_names = set()
    for param in props.get("VectorParameterValues") or []:
        if not isinstance(param, dict):
            continue
        name = (param.get("ParameterInfo") or {}).get("Name", "")
        if not name or name in seen_vector_names or name in known_colour_names:
            continue
        if not any(tok in name for tok in _MI_KEEP_TOKENS):
            continue
        pv = param.get("ParameterValue") or {}
        if not isinstance(pv, dict):
            continue
        try:
            rgba = (
                float(pv.get("R", 1.0)),
                float(pv.get("G", 1.0)),
                float(pv.get("B", 1.0)),
                float(pv.get("A", 1.0)),
            )
        except (TypeError, ValueError):
            continue
        seen_vector_names.add(name)
        result["vectors"].append((name, rgba))
    return result


def _decals_from_props(props: dict) -> list:
    """Build active decal slots from MI Properties.

    FModel may emit ``"ParameterValue": null`` (key present, value null).  dict.get
    then returns None — not the default — so every loop must guard non-dict values.
    """
    if not isinstance(props, dict):
        return []
    active_slots = set()
    static_rt = props.get("StaticParametersRuntime") or {}
    if not isinstance(static_rt, dict):
        static_rt = {}
    for sw in static_rt.get("StaticSwitchParameters") or []:
        if not isinstance(sw, dict):
            continue
        name = (sw.get("ParameterInfo") or {}).get("Name", "")
        m = re.match(r"(\d+)_UseDecal$", name)
        if m and sw.get("Value", False):
            active_slots.add(int(m.group(1)))
    scalar_lookup = {}
    for sp in props.get("ScalarParameterValues") or []:
        if not isinstance(sp, dict):
            continue
        n = (sp.get("ParameterInfo") or {}).get("Name", "")
        if not n:
            continue
        try:
            scalar_lookup[n] = float(sp.get("ParameterValue", 0.0))
        except (TypeError, ValueError):
            continue
    tex_lookup = {}
    for tp in props.get("TextureParameterValues") or []:
        if not isinstance(tp, dict):
            continue
        n = (tp.get("ParameterInfo") or {}).get("Name", "")
        pv = tp.get("ParameterValue") or {}
        # Null / non-object ParameterValue is common on unused decal slots.
        if not n or not isinstance(pv, dict):
            continue
        ov = pv.get("ObjectName", "") or ""
        op = pv.get("ObjectPath", "") or ""
        m2 = re.search(r"'([^']+)'", str(ov))
        if m2:
            tex_lookup[n] = (m2.group(1), op)
    vec_lookup = {}
    for vp in props.get("VectorParameterValues") or []:
        if not isinstance(vp, dict):
            continue
        n = (vp.get("ParameterInfo") or {}).get("Name", "")
        pv = vp.get("ParameterValue") or {}
        if not n or not isinstance(pv, dict):
            continue
        try:
            vec_lookup[n] = (
                float(pv.get("R", 0.0)),
                float(pv.get("G", 0.0)),
                float(pv.get("B", 0.0)),
                float(pv.get("A", 0.0)),
            )
        except (TypeError, ValueError):
            continue
    if not active_slots:
        for n, pair in tex_lookup.items():
            m = re.match(r"(\d+)_DecalColor$", n)
            if not m:
                continue
            stem = (pair[0] if pair else "") or ""
            if not stem or "null" in stem.lower() or "decal_null" in stem.lower():
                continue
            active_slots.add(int(m.group(1)))
    results = []
    for idx in sorted(active_slots):
        tex_stem, tex_objpath = tex_lookup.get(f"{idx}_DecalColor", ("", ""))
        data_stem, data_objpath = tex_lookup.get(f"{idx}_DecalData", ("", ""))
        if not tex_stem:
            continue
        placement = vec_lookup.get(f"{idx}_DecalPlacement", (0.0, 0.0, 1.0, 0.0))
        color_override = max(0.0, min(1.0, scalar_lookup.get(f"{idx}_ColorOverride", 0.0)))
        results.append({
            "index": idx,
            "texture": tex_stem,
            "texture_path": tex_objpath,
            "data_texture": data_stem,
            "data_texture_path": data_objpath,
            "color_a": vec_lookup.get(f"{idx}_ColorA"),
            "color_b": vec_lookup.get(f"{idx}_ColorB"),
            "uv_u": placement[0],
            "uv_v": placement[1],
            "scale": placement[2],
            "rotation": placement[3],
            # None lets materials.py derive width/height from the loaded decal
            # image if a broken/older MI omits the authored scalar.
            "width_ratio": scalar_lookup.get(f"{idx}_WidthRatio"),
            "layer_mask": scalar_lookup.get(f"{idx}_LayerMask", 255.0),
            # M_Character_Layered lerps sampled decal RGB (0) to ColorA/B (1).
            # The master material's authored default is 0.
            "color_override": color_override,
            "original_color": color_override < 0.9999,
        })
    return results


def _colours_from_palette_sidecar(json_path: str) -> dict:
    """ColorA/B/C(/2) from FModel ``*.palette.json`` next to a scalar-only MIC."""
    if not json_path:
        return {}
    sidecar = os.path.splitext(json_path)[0] + ".palette.json"
    if not os.path.isfile(sidecar):
        return {}
    try:
        with open(sidecar, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    result = {}
    mapping = (
        ("primary", "a", "ColorA"),
        ("primary", "b", "ColorB"),
        ("primary", "c", "ColorC"),
        ("secondary", "a", "ColorA2"),
        ("secondary", "b", "ColorB2"),
        ("secondary", "c", "ColorC2"),
    )
    for group, ch, key in mapping:
        grp = data.get(group)
        rgb = grp.get(ch) if isinstance(grp, dict) else None
        if not isinstance(rgb, (list, tuple)) or len(rgb) < 3:
            continue
        try:
            result[key] = (float(rgb[0]), float(rgb[1]), float(rgb[2]), 1.0)
        except (TypeError, ValueError):
            continue
    return result


def _decals_from_palette_sidecar(json_path: str) -> list:
    """Decal slots from FModel ``*.palette.json`` (CUE4Parse GetParams, inherited)."""
    if not json_path:
        return []
    sidecar = os.path.splitext(json_path)[0] + ".palette.json"
    if not os.path.isfile(sidecar):
        return []
    try:
        with open(sidecar, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    slots = data.get("decals") if isinstance(data, dict) else None
    if not isinstance(slots, list):
        return []
    results = []
    for item in slots:
        if not isinstance(item, dict):
            continue
        tex = (item.get("texture") or "").strip()
        if not tex:
            continue
        try:
            idx = int(item.get("index", 0))
        except (TypeError, ValueError):
            continue
        if idx < 1:
            continue
        placement = item.get("placement") or {}
        try:
            uv_u = float(placement.get("u", item.get("uv_u", 0.0)) or 0.0)
            uv_v = float(placement.get("v", item.get("uv_v", 0.0)) or 0.0)
            scale = float(placement.get("scale", item.get("scale", 1.0)) or 1.0)
            rotation = float(placement.get("rotation", item.get("rotation", 0.0)) or 0.0)
        except (TypeError, ValueError):
            uv_u, uv_v, scale, rotation = 0.0, 0.0, 1.0, 0.0
        results.append({
            "index": idx,
            "texture": tex,
            "texture_path": item.get("texture_path") or "",
            "data_texture": item.get("data_texture") or "",
            "data_texture_path": item.get("data_texture_path") or "",
            "color_a": item.get("color_a"),
            "color_b": item.get("color_b"),
            "uv_u": uv_u,
            "uv_v": uv_v,
            "scale": scale,
            "rotation": rotation,
            "width_ratio": item.get("width_ratio"),
            "layer_mask": item.get("layer_mask", 255.0),
            "color_override": float(item.get("color_override") or 0.0),
            "original_color": float(item.get("color_override") or 0.0) < 0.9999,
        })
    return results


def _colours_from_thin_mi_data(data: dict) -> dict:
    """ColorA/B/C… from compact FModel {Parameters: {Colors: …}} dumps."""
    if not isinstance(data, dict):
        return {}
    params = data.get("Parameters") or {}
    colors = params.get("Colors") or {}
    result = {}
    colour_names = set(get_colour_keys())
    for name, val in colors.items():
        if name not in colour_names or not isinstance(val, dict):
            continue
        result[name] = (
            float(val.get("R", 1.0)),
            float(val.get("G", 1.0)),
            float(val.get("B", 1.0)),
            float(val.get("A", 1.0)),
        )
    return result


def _empty_clothing_mi() -> dict:
    return {
        "colours": {},
        "ta_ids": {},
        "zone_scalars": {},
        "mi_params": {"scalars": [], "vectors": []},
        "decals": [],
    }


def parse_clothing_mi_local(json_path: str) -> dict:
    """Parse an already-resolved MI path. No bpy, no stem-index repair (worker-safe)."""
    empty = _empty_clothing_mi()
    if not json_path:
        return empty
    try:
        entry, data = _load_clothing_mi_entry(json_path)
        if entry:
            props = entry.get("Properties") or {}
            colours = _colours_from_props(props)
            if "ColorA" not in colours:
                colours.update(_colours_from_palette_sidecar(json_path))
            return {
                "colours": colours,
                "ta_ids": _ta_ids_from_props(props),
                "zone_scalars": _zone_scalars_from_props(props),
                "mi_params": _mi_params_from_props(props, set(colours.keys())),
                "decals": _decals_from_props(props) or _decals_from_palette_sidecar(json_path),
            }
        if data and _is_thin_fmodel_mi_data(data):
            colours = _colours_from_thin_mi_data(data)
            if colours:
                return {**empty, "colours": colours}
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to parse clothing MI (local) '{json_path}': {e}")
    return empty


def parse_clothing_mi(json_path: str) -> dict:
    """Parse colours, texture-array IDs, MI params, and decals from one MI load.

    Results are session-cached by absolute path so batch colourway imports do not
    re-parse multi-MB MIC JSON for every part.  Never raises — bad/null FModel
    fields return an empty MI so FoxHat/Huntsman/etc. cannot abort a batch.
    """
    empty = _empty_clothing_mi()
    if not json_path:
        return empty
    key = _norm_cache_path(json_path)
    if key and key in _CLOTHING_MI_PARSE_CACHE:
        return _CLOTHING_MI_PARSE_CACHE[key]
    try:
        props = load_mi_properties(json_path)
        if props:
            colours = _colours_from_props(props)
            if "ColorA" not in colours:
                colours.update(_colours_from_palette_sidecar(json_path))
            result = {
                "colours": colours,
                "ta_ids": _ta_ids_from_props(props),
                "zone_scalars": _zone_scalars_from_props(props),
                "mi_params": _mi_params_from_props(props, set(colours.keys())),
                "decals": _decals_from_props(props) or _decals_from_palette_sidecar(json_path),
            }
            if key:
                _CLOTHING_MI_PARSE_CACHE[key] = result
            return result
        # Thin mesh-folder dumps (no MIC export) — still expose ColorA/B/C for shared SimplePBR.
        data = _load_mi_json_data(json_path)
        if data and _is_thin_fmodel_mi_data(data):
            colours = _colours_from_thin_mi_data(data)
            if colours:
                result = {**empty, "colours": colours}
                if key:
                    _CLOTHING_MI_PARSE_CACHE[key] = result
                return result
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to parse clothing MI '{json_path}': {e}")
    if key:
        _CLOTHING_MI_PARSE_CACHE[key] = empty
    return empty


def parse_skin_colours(json_path: str) -> dict:
    return parse_clothing_mi(json_path)["colours"] if json_path else {}


def parse_texture_array_ids(json_path: str) -> dict:
    try:
        return _ta_ids_from_props(load_mi_properties(json_path))
    except Exception:
        return {}


def find_slice_png(png_list: list, slice_idx: int) -> str:
    for fpath in png_list:
        stem = os.path.splitext(os.path.basename(fpath))[0]
        if stem.endswith(f'_{slice_idx}'):
            return fpath
    return ''


def build_slice_png_map(png_list: list) -> dict:
    """Map trailing _N slice index -> png path for O(1) lookups."""
    result = {}
    for fpath in png_list:
        stem = os.path.splitext(os.path.basename(fpath))[0]
        m = re.search(r'_(\d+)$', stem)
        if m:
            result[int(m.group(1))] = fpath
    return result


def parse_all_mi_parameters(json_path: str, known_colour_names: set) -> dict:
    try:
        return _mi_params_from_props(load_mi_properties(json_path), known_colour_names)
    except Exception:
        return {"scalars": [], "vectors": []}


def parse_decals(json_path: str) -> list:
    if not json_path:
        return []
    try:
        return _decals_from_props(load_mi_properties(json_path))
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to parse decals from '{json_path}': {e}")
        return []

# ---------------------------------------------------------------------------
# Model type detection (folder-path domains first — see asset_domain.py)
# ---------------------------------------------------------------------------

def is_map_prop(psk_path: str) -> bool:
    """True for Environment / MapPlacements props (map Stage 2 materials)."""
    from . import asset_domain as _ad
    return _ad.is_environment_domain(psk_path)


def detect_model_type(psk_path: str) -> str:
    """Infer ``arc_model_type`` from Pioneer folder domain, then outfit subtype.

    Hard domains (path only): enemy / weapon / environment / outfit.
    Outfit subtypes still use name + occlusion heuristics *inside* outfit only.
    """
    from . import asset_domain as _ad

    domain = _ad.classify_asset_domain(psk_path)
    if domain == _ad.DOMAIN_ENEMY:
        return "enemy"
    if domain == _ad.DOMAIN_WEAPON:
        return "weapon"
    if domain == _ad.DOMAIN_ENVIRONMENT:
        return "map"

    # Outfit domain (or unknown): refine character/outfit subtypes only.
    # Never let occlusion / misc heuristics reclassify env/gun/arc paths.
    basename = os.path.basename(psk_path).lower()
    folder = os.path.dirname(psk_path)
    if domain == _ad.DOMAIN_OUTFIT or domain == _ad.DOMAIN_UNKNOWN:
        if "head_face_" in basename:
            return "face"
        if is_body(psk_path):
            return "body"
        if is_hair(psk_path):
            return "hair"
        if is_visor(psk_path):
            return "visor"
        if folder_has_occlusion_png(folder):
            return "clothing"
        if is_fur_part(psk_path):
            return "fur"
        if domain == _ad.DOMAIN_OUTFIT:
            if is_misc(psk_path):
                return "misc"
            # Outfit mesh without occlusion — still clothing pipeline (part MI).
            return "clothing"
        if is_misc(psk_path):
            return "misc"
    return "unknown"


def is_body(psk_path: str) -> bool:
    return "sk_body" in os.path.basename(psk_path).lower()

def is_hair(psk_path: str) -> bool:
    from . import asset_domain as _ad
    # Hair only under outfit domain (never env/gun/arc).
    if _ad.classify_asset_domain(psk_path) not in (
        _ad.DOMAIN_OUTFIT, _ad.DOMAIN_UNKNOWN,
    ):
        return False
    norm = psk_path.replace("\\", "/").lower()
    if "/characters/hairs/" in norm:
        return True
    # Outfit fur-card meshes (FoxHatCards) reuse Metahuman hair shaders.
    return is_hair_cards_part(psk_path)


def _mi_parent_blob(json_path: str) -> str:
    """Lowercased Parent ObjectName+ObjectPath for an MI JSON, or \"\"."""
    data = _load_mi_json_data(json_path)
    if data is None:
        return ""
    entry = None
    try:
        if isinstance(data, list):
            for e in data:
                if isinstance(e, dict) and e.get("Type") in (
                    "MaterialInstanceConstant", "MaterialInstance",
                ):
                    entry = e
                    break
        elif isinstance(data, dict) and data.get("Type") in (
            "MaterialInstanceConstant", "MaterialInstance",
        ):
            entry = data
        if not isinstance(entry, dict):
            return ""
        parent = (entry.get("Properties") or {}).get("Parent") or {}
        return (
            str(parent.get("ObjectName", "") or "")
            + " "
            + str(parent.get("ObjectPath", "") or "")
        ).lower()
    except Exception:
        return ""


def is_hair_cards_mi(json_path: str) -> bool:
    """True for Metahuman hair-card MIs (M_Hair_Metahuman_*, GlobalHairColor)."""
    if not json_path or not os.path.isfile(json_path):
        return False
    parent_s = _mi_parent_blob(json_path)
    if "m_hair" in parent_s or "hair_metahuman" in parent_s:
        return True
    data = _load_mi_json_data(json_path)
    if data is None:
        return False
    try:
        entry = None
        if isinstance(data, list):
            for e in data:
                if isinstance(e, dict) and e.get("Type") in (
                    "MaterialInstanceConstant", "MaterialInstance",
                ):
                    entry = e
                    break
        elif isinstance(data, dict):
            entry = data
        props = (entry or {}).get("Properties") or {}
        for tp in props.get("TextureParameterValues") or []:
            pname = str((tp.get("ParameterInfo") or {}).get("Name", "") or "").lower()
            if pname in ("globalhaircolor", "coverage", "attributemap"):
                return True
    except Exception:
        pass
    return False


def is_hair_cards_part(psk_path: str) -> bool:
    """True for dedicated hair-card meshes (e.g. Huntsman FoxHatCards)."""
    if not psk_path:
        return False
    folder = os.path.dirname(_fs_abspath(psk_path))
    folder_leaf = os.path.basename(folder.rstrip("/\\")).lower()
    base = os.path.basename(psk_path).lower()
    name_hit = (
        "hatcards" in folder_leaf
        or "haircards" in folder_leaf
        or "furcards" in folder_leaf
        or "hatcards" in base
        or "haircards" in base
        or "furcards" in base
    )
    # Cheap name gate — avoid MI walks on every clothing/weapon part.
    if not name_hit:
        return False
    mi = get_base_skin_json(psk_path) if folder else ""
    if mi and is_hair_cards_mi(mi):
        return True
    for _n, path in scan_skins(psk_path)[:8]:
        if is_hair_cards_mi(path):
            return True
    return False

def is_weapon(psk_path: str) -> bool:
    from . import asset_domain as _ad
    return _ad.is_weapon_domain(psk_path)

def is_enemy(psk_path: str) -> bool:
    from . import asset_domain as _ad
    return _ad.is_enemy_domain(psk_path)

def is_visor(psk_path: str, mat_slot_name: str = "") -> bool:
    """Return True if this part is a visor/glass/screen overlay."""
    from . import asset_domain as _ad
    # Visor heuristic is outfit-only (path "visor" under Characters).
    domain = _ad.classify_asset_domain(psk_path)
    if domain not in (_ad.DOMAIN_OUTFIT, _ad.DOMAIN_UNKNOWN):
        return False
    norm = psk_path.replace("\\", "/").lower()
    basename = os.path.basename(psk_path).lower()
    if "visor" in norm or "visor" in basename:
        return True
    if mat_slot_name and any(k in mat_slot_name.lower() for k in ("visor", "glass", "screen")):
        return True
    return False

def is_misc(psk_path: str) -> bool:
    """Outfit/item misc (NOM/NEM packs). Never classifies env/gun/arc paths."""
    from . import asset_domain as _ad
    if _ad.classify_asset_domain(psk_path) in (
        _ad.DOMAIN_ENVIRONMENT, _ad.DOMAIN_WEAPON, _ad.DOMAIN_ENEMY,
    ):
        return False
    folder = os.path.dirname(psk_path)
    try:
        for fname in os.listdir(folder):
            fl = fname.lower()
            if any(fl.endswith(f"_{t}.png") for t in ("nom", "nem", "nxm", "nam")):
                return True
    except OSError:
        pass
    return False

def scan_hair_mis(psk_path: str) -> list:
    folder = os.path.dirname(psk_path)
    results = []
    try:
        for fname in sorted(os.listdir(folder)):
            if fname.lower().endswith(".json") and fname.lower().startswith("mi_"):
                results.append((os.path.splitext(fname)[0], os.path.join(folder, fname)))
    except OSError:
        pass
    return results

def find_skin_json_for_mi_stem(psk_path: str, mi_stem: str) -> str:
    if not mi_stem:
        return ""
    skins_folder = get_skins_folder(psk_path)
    part_folder = os.path.dirname(bpy.path.abspath(psk_path))
    candidate_dirs = []
    if skins_folder:
        for skin_name in get_all_skin_dirs(skins_folder):
            candidate_dirs.append(os.path.join(skins_folder, skin_name))
    # FoxHat-style sibling colourway dirs (ArcticFox/BlackFox/Racoon).
    for _name, skin_dir in iter_part_colorway_dirs(part_folder):
        if skin_dir not in candidate_dirs:
            candidate_dirs.append(skin_dir)
    candidate_dirs.append(part_folder)
    for d in candidate_dirs:
        candidate = os.path.join(d, mi_stem + ".json")
        resolved = resolve_clothing_mi_json(candidate, mi_stem) if os.path.isfile(candidate) else ""
        if resolved:
            return resolved
    # Exact stem elsewhere under Outfits / Characters (identity-verified).
    resolved = resolve_clothing_mi_json(mi_stem + ".json", mi_stem)
    if resolved:
        return resolved
    mi_stem_norm = utils.normalize_folder_name(mi_stem)
    for d in candidate_dirs:
        try:
            for fname in os.listdir(d):
                if not _is_mi_json_filename(fname):
                    continue
                stem = os.path.splitext(fname)[0]
                if utils.normalize_folder_name(stem) == mi_stem_norm:
                    resolved = resolve_clothing_mi_json(os.path.join(d, fname), stem)
                    if resolved:
                        return resolved
        except OSError:
            pass
    local_mi_stems = []
    for d in candidate_dirs:
        try:
            for fname in os.listdir(d):
                if _is_mi_json_filename(fname):
                    local_mi_stems.append((d, fname, os.path.splitext(fname)[0]))
        except OSError:
            pass
    if local_mi_stems:
        mi_parts = mi_stem.split("_")
        for cut in range(1, len(mi_parts)):
            suffix = "_".join(mi_parts[cut:])
            if not suffix:
                continue
            suffix_norm = utils.normalize_folder_name(suffix)
            if not suffix_norm:
                continue
            for d, fname, stem in local_mi_stems:
                stem_norm = utils.normalize_folder_name(stem)
                if stem_norm.endswith(suffix_norm):
                    resolved = resolve_clothing_mi_json(os.path.join(d, fname), stem)
                    if resolved:
                        return resolved
    return ""