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
_CLOTHING_MI_PARSE_SIGNATURES: dict[str, tuple] = {}
_MATERIAL_SOURCE_CACHE: dict[str, tuple] = {}
_MATERIAL_LINEAGE_CACHE: dict[str, dict] = {}
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
    json_path = _fs_abspath(json_path)
    if not json_path or not os.path.isfile(json_path):
        return None, None
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None, None
    if _is_thin_fmodel_mi_data(data):
        params = data.get("Parameters") or {}
        props = dict(params.get("Properties") or {})
        projected = {}
        for source, target in (('Scalars', 'ScalarParameterValues'), ('Colors', 'VectorParameterValues')):
            if source not in params:
                continue
            projected[target] = []
            for name, value in (params.get(source) or {}).items():
                if source == 'Colors' and isinstance(value, (list, tuple)):
                    value = dict(zip('RGBA', [*value, 1, 1, 1, 1][:4]))
                projected[target].append({'ParameterInfo': {'Name': name}, 'ParameterValue': value})
        if 'Textures' in data:
            projected['TextureParameterValues'] = []
            for name, value in (data.get('Textures') or {}).items():
                if isinstance(value, str):
                    stem = value.split('/')[-1].split('.')[0]
                    value = {'ObjectPath': value, 'ObjectName': "Texture2D'" + stem + "'"}
                projected['TextureParameterValues'].append({'ParameterInfo': {'Name': name}, 'ParameterValue': value})
        if 'Switches' in params:
            projected['StaticParametersRuntime'] = {'StaticSwitchParameters': [
                {'ParameterInfo': {'Name': name}, 'Value': bool(value), 'bOverride': True}
                for name, value in (params.get('Switches') or {}).items()]}
        props = _merge_material_properties(props, projected)
        if data.get('Parent'):
            props['Parent'] = data['Parent']
        entry = {'Type': 'MaterialInstanceConstant', 'Name': os.path.basename(json_path).split('.', 1)[0], 'Properties': props}
        return entry, data
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
        inherited = _material_lineage(json_path)['properties']
        if not (inherited.get('VectorParameterValues') or inherited.get('TextureParameterValues')):
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
            backpacks = os.path.join(content_dir, "Pioneer", "Characters", "Backpacks")
            if os.path.isdir(backpacks):
                search_roots.append(backpacks)
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
        if "/characters/backpacks/" in pl:
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
    abs_path = _fs_abspath(json_path)
    stem = (expected_stem or os.path.splitext(os.path.basename(abs_path))[0]).strip()
    if "." in stem:
        stem = stem.split(".", 1)[0]
    if clothing_mi_json_is_valid(abs_path, stem):
        return abs_path

    # Local file wrong/missing — find another copy of the same stem.
    for cand in _clothing_mi_stem_index().get(stem.lower(), []):
        if os.path.normcase(os.path.normpath(cand)) == os.path.normcase(abs_path):
            continue
        # Generic preview names repeat across outfits; require the same
        # Characters/... package before considering a repaired copy.
        original = abs_path.replace("\\", "/").lower()
        candidate = cand.replace("\\", "/").lower()
        if "/characters/" in original and "/characters/" in candidate:
            if original.split("/characters/", 1)[1] != candidate.split("/characters/", 1)[1]:
                continue
        if clothing_mi_json_is_valid(cand, stem):
            print(
                f"Arc Asset Importer: clothing MI identity repair — "
                f"'{os.path.basename(abs_path)}' invalid at '{abs_path}', "
                f"using '{cand}'"
            )
            return os.path.abspath(cand)
    if abs_path and os.path.isfile(abs_path):
        print(
            f"Arc Asset Importer: rejecting invalid clothing MI JSON "
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
    _CLOTHING_MI_PARSE_SIGNATURES.clear()
    _MATERIAL_SOURCE_CACHE.clear()
    _MATERIAL_LINEAGE_CACHE.clear()
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
    json_path = _fs_abspath(json_path)
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
    abs_path = _fs_abspath(json_path)
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
    manual_folder = _fs_abspath(manual_folder) if manual_folder else ""
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
    for directory in authored_shared_skin_dirs(psk_path):
        path = _pick_valid_mi_json_in_dir(directory)
        if path and all(os.path.normcase(existing) != os.path.normcase(path) for _label, existing in results):
            results.append((os.path.basename(directory), path))
    results.extend(_scan_main_folder_skins(psk_path))
    return results

def authored_srgb_sidecar(path: str) -> bool:
    """True when a PNG's .arc-texture.json leaves SRGB at its default on an sRGB-capable format.

    The game's sampler decodes those whatever role the material gives them
    (T_FurMask_04_A, T_Bilguun_CloakOrange_Tassels_Roughnessmetal).
    """
    try:
        with open(os.path.splitext(path)[0] + '.arc-texture.json', encoding='utf8') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    decode = globals().get('authored_srgb_decode')
    if decode is None:  # lazy: materials/ imports properties
        from .materials.native.interop import authored_srgb_decode as decode
    return isinstance(data, dict) and decode(
        {**(data.get('Properties') or {}), 'Type': data.get('Type'), 'source_path': path})

def authored_linear_sidecar(path: str) -> bool:
    """True when a PNG's .arc-texture.json authors SRGB=false.

    The game samples those raw whatever role the material gives them, colour maps included
    (T_ArcFieldCrate_01_A_CR, T_ConcretePanel_02_CR).
    """
    try:
        with open(os.path.splitext(path)[0] + '.arc-texture.json', encoding='utf8') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and (data.get('Properties') or {}).get('SRGB', True) is False

def _fs_abspath(path: str) -> str:
    """Resolve Blender-relative paths and preserve native absolute Windows paths."""
    from ..Outfit_Accessories.pak_catalog import filesystem_path
    if not path:
        return ""
    # Blender reserves // for paths relative to the current .blend. Native
    # Windows UNC uses backslashes; //?/ is an extended absolute path.
    blender_relative = path.startswith("//") and not path.startswith("//?/")
    if os.path.isabs(path) and not blender_relative:
        return filesystem_path(os.path.normpath(path))
    try:
        absolute = bpy.path.abspath(path)
    except Exception:
        absolute = os.path.abspath(path)
    return filesystem_path(os.path.normpath(absolute))


def get_skins_folder(psk_path: str):
    part_folder = os.path.dirname(_fs_abspath(psk_path))
    candidates = [os.path.join(part_folder, "Skins")]
    normalized = part_folder.replace("\\", "/")
    for boundary in ("/PioneerGame/Content/", "/RemappedPlugins/Content/"):
        if boundary.lower() not in normalized.lower():
            continue
        offset = normalized.lower().index(boundary.lower())
        root, tail = normalized[:offset], normalized[offset + len(boundary):]
        for mount in ("PioneerGame/Content", "RemappedPlugins/Content"):
            candidates.append(os.path.join(root, *mount.split("/"), *tail.split("/"), "Skins"))
    # Alias matching uses slashes; normalize back before extended Windows IO.
    candidates = [_fs_abspath(p) for p in candidates]
    candidates = [p for p in dict.fromkeys(candidates) if os.path.isdir(p)]
    def revision(folder):
        return max((os.path.getmtime(_fs_abspath(os.path.join(base, name))) for base, _dirs, files in os.walk(folder) for name in files if name.lower().startswith("mi_") and name.lower().endswith(".json")), default=0)
    return max(candidates, key=revision) if candidates else None


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
    # ArcPak writes MI_X.arc-material.json beside (or instead of) MI_X.json.
    # Both are the same material: key by the bare MI stem, plain .json first.
    by_stem = {}
    for fname in sorted(mi_files, key=lambda f: f.lower().endswith(".arc-material.json")):
        stem = fname[:-len(".json")]
        if stem.lower().endswith(".arc-material"):
            stem = stem[:-len(".arc-material")]
        by_stem.setdefault(stem, fname)
    stems = sorted(by_stem)
    mi_files = [by_stem[stem] for stem in stems]
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

def referenced_texture_array_slices(json_path: str) -> list:
    """Resolve exact inherited TA references, including another skin's bank."""
    result = []
    for parameter in load_mi_properties(json_path).get("TextureParameterValues") or []:
        reference = parameter.get("ParameterValue") or {}
        object_path = reference.get("ObjectPath") or reference.get("AssetPathName") or ""
        stem = mi_stem_from_material_ref(reference)
        if not stem.casefold().startswith("ta_") or not object_path:
            continue
        package = object_path.rsplit(".", 1)[0] if "." in object_path.rsplit("/", 1)[-1] else object_path
        first = ""
        for suffix in ("_LAYER0", "_0"):
            first = find_asset_from_object_path(package + suffix, ".png")
            if first:
                break
        if not first:
            continue
        directory = os.path.dirname(first)
        pattern = re.compile(re.escape(stem) + r"_(?:LAYER_?)?\d+\.png$", re.IGNORECASE)
        try:
            for name in sorted(os.listdir(directory)):
                if pattern.fullmatch(name):
                    path = os.path.join(directory, name)
                    if path not in result:
                        result.append(path)
        except OSError:
            continue
    return result


def scan_base_skin_textures(psk_path: str, selected_skin_name: str = "", manual_folder: str = "") -> list:
    # Use filesystem abspath so ThreadPool IO workers never touch bpy.
    manual_folder = _fs_abspath(manual_folder) if manual_folder else ""
    skins_folder = manual_folder if (manual_folder and os.path.isdir(manual_folder)) else get_skins_folder(psk_path)
    if not skins_folder:
        # Toggle meshes may have no local Skins folder while their authored
        # DA_VP points to another part's layered texture-array bank.
        shared = authored_shared_skin_dirs(psk_path)
        skins_folder = os.path.dirname(shared[0]) if shared else ""
    if not skins_folder:
        return []
    all_dirs = get_all_skin_dirs(skins_folder)
    if not all_dirs:
        if manual_folder:
            try:
                pngs = [os.path.join(skins_folder, f)
                        for f in sorted(os.listdir(skins_folder))
                        if f.lower().endswith(".png")]
                mi_json = _pick_valid_mi_json_in_dir(skins_folder)
                if mi_json:
                    pngs.extend(path for path in referenced_texture_array_slices(mi_json) if path not in pngs)
                return pngs
            except OSError:
                return []
        return []
    default_dir = get_default_skin_dir(all_dirs)
    scan_dirs = [selected_skin_name] if selected_skin_name in all_dirs else ([default_dir] if default_dir else [d for d in all_dirs if not is_variant(d, all_dirs)])
    pngs = []
    for skin_name in scan_dirs:
        skin_dir = os.path.join(skins_folder, skin_name)
        try:
            for fname in sorted(os.listdir(skin_dir)):
                if fname.lower().endswith(".png"):
                    pngs.append(os.path.join(skin_dir, fname))
        except OSError:
            pass
        mi_json = _pick_valid_mi_json_in_dir(skin_dir)
        if mi_json:
            for path in referenced_texture_array_slices(mi_json):
                if path not in pngs:
                    pngs.append(path)
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
    json_path = _fs_abspath(json_path)
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


def authored_visual_part_materials(psk_path: str) -> list:
    """Exact DA_VP Mesh/MaterialOverrides bindings for this mesh, across mount aliases.

    Raised/retracted parts can legitimately reference another part's material
    bank. Only an authored binding authorizes that reuse; names are not guessed.
    """
    mesh = _fs_abspath(psk_path)
    folder = os.path.dirname(mesh)
    folders = [folder]
    normalized = folder.replace("\\", "/")
    for source, target in (("/RemappedPlugins/Content/", "/PioneerGame/Content/"),
                           ("/PioneerGame/Content/", "/RemappedPlugins/Content/")):
        if source.casefold() in normalized.casefold():
            index = normalized.casefold().index(source.casefold())
            folders.append((normalized[:index] + target + normalized[index + len(source):]).replace("/", os.sep))
    mesh_stem = os.path.splitext(os.path.basename(mesh))[0].casefold()
    result = []
    seen = set()
    for directory in folders:
        try:
            names = sorted(name for name in os.listdir(directory) if name.casefold().startswith("da_vp_") and name.casefold().endswith(".json"))
        except OSError:
            continue
        for name in names:
            try:
                with open(os.path.join(directory, name), encoding="utf-8-sig") as stream:
                    data = json.load(stream)
            except (OSError, ValueError):
                continue
            for entry in utils.ue_export_entries(data):
                props = entry.get("Properties") or {}
                mesh_ref = props.get("Mesh") or props.get("StaticMesh") or {}
                if mi_stem_from_material_ref(mesh_ref).casefold() != mesh_stem:
                    continue
                overrides = props.get("MaterialOverrides") or []
                if isinstance(overrides, dict):
                    overrides = [{"Key": key, "Value": value} for key, value in overrides.items()]
                for override in overrides:
                    reference = override.get("Value") or {}
                    material_path = reference.get("ObjectPath") or reference.get("AssetPathName") or ""
                    path = find_asset_from_object_path(material_path, ".json")
                    path = resolve_part_mi_json(path, mi_stem_from_material_ref(reference)) if path else ""
                    key = (str(override.get("Key") if override.get("Key") is not None else ""), os.path.normcase(path))
                    if path and key not in seen:
                        seen.add(key)
                        result.append({"slot": key[0], "path": path, "material_path": material_path,
                                       "visual_part": os.path.join(directory, name)})
    return result


def authored_shared_skin_dirs(psk_path: str) -> list:
    """Colourway folders explicitly shared through the mesh's DA_VP materials."""
    own_folder = os.path.normcase(os.path.dirname(_fs_abspath(psk_path)))
    result = []
    for binding in authored_visual_part_materials(psk_path):
        material_dir = os.path.dirname(binding["path"])
        root = os.path.dirname(material_dir)
        if os.path.basename(root).casefold() != "skins" or os.path.normcase(os.path.dirname(root)) == own_folder:
            continue
        for skin in get_all_skin_dirs(root):
            directory = os.path.join(root, skin)
            if directory not in result:
                result.append(directory)
    return result


def prefer_authored_part_material(psk_path: str, json_path: str) -> str:
    """Repair an older preview-only stamp using this mesh's authored binding."""
    if not json_path or not psk_path:
        return json_path
    if os.path.normcase(os.path.dirname(_fs_abspath(json_path))) != os.path.normcase(os.path.dirname(_fs_abspath(psk_path))):
        return json_path
    if parse_clothing_mi(json_path).get("colours"):
        return json_path
    authored = [binding["path"] for binding in authored_visual_part_materials(psk_path)]
    candidates = [path for path in authored if not is_fur_mi(path) and parse_clothing_mi(path).get("colours")]
    return candidates[0] if len(set(candidates)) == 1 else json_path


def get_base_skin_json(psk_path: str, manual_folder: str = "") -> str:
    """Resolve the MI JSON that drives colours for a part.

    Order: Skins/<default>/… when present, else any ``MI_*.json`` beside the mesh
    (including thin FModel dumps with Parameters.Colors — shared across colorways).
    Prefer non-fur clothing MIs when both shell and fur instances share a folder.
    """
    manual_folder = _fs_abspath(manual_folder) if manual_folder else ""
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
    # A toggle mesh can reuse an authored material bank on another part (e.g.
    # Goalie HelmetUp -> Helmet/Plastic). Prefer it over its preview-only MI.
    if not manual_folder:
        authored = [binding["path"] for binding in authored_visual_part_materials(psk_path)]
        non_fur_authored = [path for path in authored if not is_fur_mi(path)]
        if len(set(non_fur_authored)) == 1:
            return non_fur_authored[0]
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
    if "breakupmask" in stem:
        return "breakup"
    return None

def base_skin_texture_group(fname: str) -> str:
    stem = os.path.splitext(fname)[0].lower()
    # FModel uses _0; current UEFormat/ArcPak emits _LAYER0.
    stem = re.sub(r"_(?:layer_?)?\d+$", "", stem)
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
    """Resolve exact Game, Engine, and plugin virtual packages across known roots.

    Non-Game mounts use only direct known-layout probes, never Content basename
    guesses or a directory walk. Game retains the existing Content fallback.
    """
    utils.invalidate_dir_caches_if_root_changed()
    if not obj_path:
        return ""
    # Engine null stubs — never BFS Content / EmbarkScript for these.
    if _object_path_is_null_stub(obj_path):
        return ""
    obj_path = str(obj_path).replace("\\", "/")
    parts = obj_path.strip("/").split("/")
    if any(not part or part in (".", "..") or ":" in part or "\x00" in part for part in parts):
        return ""
    non_game_mount = obj_path.startswith("/") and parts[0].casefold() != "game"
    # UE dumps often use AssetName.AssetName — strip trailing instance suffix
    leaf = obj_path.split("/")[-1]
    if "." in leaf:
        obj_path = obj_path[: -len(leaf)] + leaf.split(".")[0]
    clean = obj_path
    if clean.casefold().startswith('/game/'):
        rel = clean[len('/Game/'):]
    else:
        rel = clean.lstrip('/')
    if not extension.startswith('.'):
        extension = '.' + extension

    cache_key = (clean.replace("\\", "/").lower(), extension.lower())
    if cache_key in _OBJECT_PATH_ASSET_CACHE:
        cached = _OBJECT_PATH_ASSET_CACHE[cache_key]
        return _fs_abspath(cached) if cached else ""

    def _mount_candidates(cache_root):
        if not non_game_mount:
            return [_fs_abspath(os.path.join(cache_root, *mount.split("/"), rel.replace('/', os.sep)) + extension)
                    for mount in ("PioneerGame/Content", "RemappedPlugins/Content", "Game")]
        mount, _, tail = rel.partition("/")
        if tail.casefold().startswith("content/"):
            tail = tail[len("content/"):]
        layouts = ((mount,), (mount, "Content"), ("RemappedPlugins", mount, "Content"),
                   ("Plugins", mount, "Content"))
        return [_fs_abspath(os.path.join(cache_root, *layout, tail.replace('/', os.sep)) + extension)
                for layout in layouts]

    # Prefer current extraction payloads over old full-dump donor trees.
    for cache_root in reversed(getattr(utils, "_pak_cache_roots", [])):
        candidates = _mount_candidates(cache_root)
        candidates = [p for p in candidates if os.path.isfile(p)]
        if candidates:
            candidate = max(candidates, key=os.path.getmtime)
            _OBJECT_PATH_ASSET_CACHE[cache_key] = candidate
            return candidate
    content_dirs = utils.get_content_dirs()
    if not content_dirs:
        root = utils.get_pioneer_root()
        cd = utils.find_content_dir(root) if root else ""
        if cd:
            content_dirs = [cd]

    if non_game_mount:
        # A secondary FModel root is known from its exact Content directory.
        # Probe its sibling mount layouts; do not pretend plugin paths are Game.
        seen = set()
        for content_dir in content_dirs:
            if os.path.basename(os.path.normpath(content_dir)).casefold() == "content":
                export_root = os.path.dirname(os.path.dirname(content_dir))
            else:
                export_root = os.path.dirname(content_dir)
            if export_root in seen:
                continue
            seen.add(export_root)
            candidates = [path for path in _mount_candidates(export_root) if os.path.isfile(path)]
            if candidates:
                candidate = max(candidates, key=os.path.getmtime)
                _OBJECT_PATH_ASSET_CACHE[cache_key] = candidate
                return candidate
        _OBJECT_PATH_ASSET_CACHE[cache_key] = ""
        return ""

    rel_os = rel.replace('/', os.sep)
    for content_dir in content_dirs:
        candidate = _fs_abspath(os.path.join(content_dir, rel_os) + extension)
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
            candidate = _fs_abspath(os.path.join(found_dir, filename))
            if os.path.isfile(candidate):
                _OBJECT_PATH_ASSET_CACHE[cache_key] = candidate
                return candidate
    _OBJECT_PATH_ASSET_CACHE[cache_key] = ""
    return ""


def mi_stem_from_material_ref(material_ref: dict) -> str:
    """Extract MI stem from a UE Material soft reference (ObjectPath / ObjectName)."""
    if not material_ref:
        return ""
    if isinstance(material_ref, str):
        material_ref = {"ObjectPath": material_ref}
    obj_path = material_ref.get("ObjectPath", "") or material_ref.get("AssetPathName", "") or ""
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
    "Tiling", "BreakupMask", "DetailTiling", "MaskSoftness",
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
    'MediumMaskValue',
    'MediumMaskSoftness',
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
    'DetailTiling',
    'CreaseMaskSoftness',
    'EdgeMaskSoftness',
    'CreaseMaskTiling',
    'EdgeMaskTiling',
    'BaseRoughnessTiling',
    'CreaseRougnessTiling',
    'EdgeRougnessTiling',
    'DetailNormalOverride',
)


def _material_file_stamp(path: str) -> tuple:
    path = _fs_abspath(path)
    try:
        stat = os.stat(path)
        return (_norm_cache_path(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return (_norm_cache_path(path), -1, -1)


def _material_ref_path(reference) -> str:
    if isinstance(reference, dict):
        reference = reference.get('ObjectPath') or reference.get('AssetPathName') or reference.get('ObjectName')
    value = str(reference or '').replace('\\', '/')
    if "'" in value:
        value = value.split("'", 1)[1].rstrip("'")
    leaf = value.rsplit('/', 1)[-1]
    return value[:-len(leaf)] + leaf.split('.', 1)[0] if leaf else ''


def _material_package_identity(reference) -> str:
    value = _material_ref_path(reference).strip('/').casefold()
    if value.startswith('game/'):
        return value[5:]
    if '/content/' in value:
        return value.split('/content/', 1)[1]
    if value.startswith('content/'):
        return value[8:]
    return value


def _material_source(path: str, expected_ref=None):
    """Read one exact source identity. No Blender calls and no basename search."""
    path = _fs_abspath(path)
    stamp = _material_file_stamp(path)
    if stamp[1] < 0:
        return None, None
    cached = _MATERIAL_SOURCE_CACHE.get(stamp[0])
    if cached and cached[0] == stamp:
        entry, data = cached[1:]
    else:
        data = _load_mi_json_data(path)
        entry = None
        if _is_thin_fmodel_mi_data(data):
            entry, _ = _load_clothing_mi_entry(path)
        else:
            for candidate in utils.ue_export_entries(data):
                if isinstance(candidate, dict) and candidate.get('Type') in ('Material', 'MaterialInstanceConstant', 'MaterialInstance'):
                    entry = candidate
                    break
        if entry:
            # Shader resources/disassemblies can dwarf the named parameters in
            # full FModel dumps. Retain only fields needed for inheritance.
            entry = {key: entry[key] for key in ('Type', 'Name', 'Package', 'Properties',
                                                'CachedExpressionData', 'cached') if key in entry}
        if not _is_thin_fmodel_mi_data(data):
            metadata = data.get('Metadata') if isinstance(data, dict) else None
            data = {'Metadata': {'ImportMap': (metadata or {}).get('ImportMap', [])}} if metadata else None
        _MATERIAL_SOURCE_CACHE[stamp[0]] = (stamp, entry, data)
    if not entry:
        return None, data
    expected_stem = _material_ref_path(expected_ref).rsplit('/', 1)[-1] if expected_ref else os.path.basename(path).split('.', 1)[0]
    if _mi_entry_stem(entry).casefold() != expected_stem.casefold():
        return None, data
    if expected_ref and '/' in _material_ref_path(expected_ref) and entry.get('Package'):
        if _material_package_identity(entry['Package']) != _material_package_identity(expected_ref):
            return None, data
    return entry, data


def _cached_material_properties(entry: dict) -> dict:
    """Translate named current master defaults into the existing MIC interface."""
    cached = entry.get('CachedExpressionData', entry.get('cached', {})) or {}
    properties = {}
    kinds = [('ScalarValues', (0,), 'ScalarParameterValues'),
             ('VectorValues', (1,), 'VectorParameterValues'),
             ('TextureValues', (3,), 'TextureParameterValues'),
             ('StaticSwitchValues', (8, 7), 'StaticSwitchParameters')]
    runtime = cached.get('RuntimeEntries')
    for value_key, indices, output_key in kinds:
        values = cached.get(value_key) or []
        if not values:
            continue
        infos = []
        for index in indices:
            block = (runtime[index] if isinstance(runtime, list) and index < len(runtime)
                     else cached.get('RuntimeEntries' + (f'[{index}]' if index else ''), {}))
            candidate = block.get('ParameterInfoSet') or [] if isinstance(block, dict) else []
            if len(candidate) == len(values):
                infos = candidate
                break
        parameters = []
        for info, value in zip(infos, values):
            if not isinstance(info, dict) or not info.get('Name'):
                continue
            info = dict(info)
            if output_key == 'StaticSwitchParameters':
                parameters.append({'ParameterInfo': info, 'Value': bool(value), 'bOverride': True})
                continue
            if output_key == 'VectorParameterValues' and isinstance(value, (list, tuple)):
                value = dict(zip('RGBA', [*value, 1, 1, 1, 1][:4]))
            if output_key == 'TextureParameterValues':
                if not isinstance(value, dict):
                    value = {'ObjectPath': value}
                value = dict(value)
                if value.get('ObjectPath') and not value.get('ObjectName'):
                    stem = _material_ref_path(value['ObjectPath']).rsplit('/', 1)[-1]
                    value['ObjectName'] = "Texture2D'" + stem + "'"
            parameters.append({'ParameterInfo': info, 'ParameterValue': value})
        if output_key == 'StaticSwitchParameters':
            properties['StaticParametersRuntime'] = {output_key: parameters}
        else:
            properties[output_key] = parameters
    return properties


def _material_parameter_identity(parameter: dict) -> tuple:
    info = parameter.get('ParameterInfo') or {}
    association = str(info.get('Association', 'GlobalParameter')).rsplit('::', 1)[-1]
    return (str(info.get('Name') or parameter.get('ParameterName') or ''),
            association, info.get('Index', -1))


def _merge_material_properties(parent: dict, child: dict) -> dict:
    result = dict(parent or {})
    result.update(child or {})
    for key in ('ScalarParameterValues', 'VectorParameterValues', 'TextureParameterValues'):
        values = {}
        for parameter in [*((parent or {}).get(key) or []), *((child or {}).get(key) or [])]:
            if isinstance(parameter, dict):
                values[_material_parameter_identity(parameter)] = parameter
        if values:
            result[key] = list(values.values())
    switches = {}
    for properties in (parent or {}, child or {}):
        static = properties.get('StaticParametersRuntime') or properties.get('StaticParameters') or {}
        for parameter in static.get('StaticSwitchParameters') or []:
            if isinstance(parameter, dict) and parameter.get('bOverride', True):
                switches[_material_parameter_identity(parameter)] = parameter
    if switches:
        result['StaticParametersRuntime'] = {'StaticSwitchParameters': list(switches.values())}
    if (parent or {}).get('BasePropertyOverrides') or (child or {}).get('BasePropertyOverrides'):
        overrides = dict((parent or {}).get('BasePropertyOverrides') or {})
        own = (child or {}).get('BasePropertyOverrides') or {}
        for key, value in own.items():
            if key.startswith('bOverride_'):
                continue
            if own.get('bOverride_' + key, True):
                overrides[key] = value
                overrides['bOverride_' + key] = True
        result['BasePropertyOverrides'] = overrides
    return result


def _local_material_parent_candidates(reference, current_path: str) -> list:
    """Exact package paths within the source export, including mount aliases."""
    reference = _material_ref_path(reference)
    if not reference:
        return []
    directory = os.path.dirname(_fs_abspath(current_path))
    if '/' not in reference:
        bases = [os.path.join(directory, reference)]
    else:
        normalized = directory.replace('\\', '/')
        lower = normalized.casefold()
        roots = []
        for boundary in ('/pioneergame/content/', '/engine/content/', '/remappedplugins/', '/content/pioneer/', '/game/pioneer/', '/outfits/'):
            index = lower.find(boundary)
            if index >= 0:
                roots.append(normalized[:index])
        # A tree rooted directly at PioneerGame/Content still has that boundary;
        # unstructured exports can only use an explicitly local package subtree.
        roots = list(dict.fromkeys(roots)) or [directory]
        clean = reference.strip('/')
        relative = clean[5:] if clean.casefold().startswith('game/') else clean
        layouts = ['PioneerGame/Content/' + relative, 'Content/' + relative]
        if clean.casefold().startswith(('game/', 'pioneergame/content/')):
            if clean.casefold().startswith('pioneergame/content/'):
                relative = clean.split('/', 2)[2]
                layouts = [clean, 'Content/' + relative]
            layouts.extend(['RemappedPlugins/Content/' + relative, 'Game/' + relative])
        elif clean.casefold().startswith('engine/'):
            layouts = ['Engine/Content/' + clean[7:]]
        elif clean.casefold().startswith('remappedplugins/'):
            layouts = [clean]
        elif reference.startswith('/'):
            mount, _, rest = clean.partition('/')
            layouts = ['RemappedPlugins/' + mount + '/Content/' + rest, mount + '/Content/' + rest]
        bases = [os.path.join(root, *layout.split('/')) for root in roots for layout in layouts]
    result = []
    for base in bases:
        for suffix in ('.arc-material.json', '.json'):
            candidate = _fs_abspath(base + suffix)
            if candidate not in result:
                result.append(candidate)
    return result


def _material_lineage(json_path: str, stack=()) -> dict:
    """Resolve current source-local inheritance and cache every dependency stamp."""
    path = _fs_abspath(json_path)
    identity = _norm_cache_path(path)
    cached = _MATERIAL_LINEAGE_CACHE.get(identity)
    if cached and cached['signature'] == tuple(_material_file_stamp(p) for p in cached['dependencies']):
        return cached
    if identity in stack or len(stack) >= 64:
        return {'properties': {}, 'local_properties': {}, 'dependencies': (path,),
                'signature': (_material_file_stamp(path),), 'sources': (),
                'unresolved': ('Material inheritance cycle: ' + path,)}
    dependencies, sources, unresolved = [path], [], []
    entry, data = _material_source(path)
    if not entry:
        result = {'properties': {}, 'local_properties': {}, 'dependencies': (path,),
                  'signature': (_material_file_stamp(path),), 'sources': (),
                  'unresolved': ('Material source identity missing or invalid: ' + path,)}
        _MATERIAL_LINEAGE_CACHE[identity] = result
        return result
    fallback = {}
    native_path = path[:-len('.json')] + '.arc-material.json' if not path.casefold().endswith('.arc-material.json') else path
    if native_path != path:
        dependencies.append(native_path)
        native_entry, _ = _material_source(native_path, entry.get('Package') or _mi_entry_stem(entry))
        if native_entry:
            fallback = _merge_material_properties(_cached_material_properties(entry), entry.get('Properties') or {})
            entry = native_entry
            sources.append(native_path)
    sources.append(path)
    local = _merge_material_properties(_cached_material_properties(entry), entry.get('Properties') or {})
    effective = {}
    parent = local.get('Parent')
    if _material_ref_path(parent):
        found = ''
        for candidate in _local_material_parent_candidates(parent, path):
            dependencies.append(candidate)
            parent_entry, _ = _material_source(candidate, parent)
            if parent_entry:
                found = candidate
                break
        if found:
            inherited = _material_lineage(found, (*stack, identity))
            effective = inherited['properties']
            dependencies.extend(inherited['dependencies'])
            sources.extend(inherited['sources'])
            unresolved.extend(inherited['unresolved'])
        else:
            unresolved.append(_material_ref_path(parent))
    effective = _merge_material_properties(effective, local)
    if fallback:
        effective = _merge_material_properties(fallback, effective)
    dependencies = tuple(dict.fromkeys(dependencies))
    result = {'properties': effective, 'local_properties': local, 'dependencies': dependencies,
              'signature': tuple(_material_file_stamp(p) for p in dependencies),
              'sources': tuple(dict.fromkeys(sources)), 'unresolved': tuple(dict.fromkeys(unresolved))}
    _MATERIAL_LINEAGE_CACHE[identity] = result
    return result


def material_lineage_signature(json_path: str) -> tuple:
    """Content signature covering child, native sidecar and all parent sources."""
    if not json_path:
        return ()
    return _material_lineage(json_path)['signature']


def load_mi_properties_local(json_path: str) -> dict:
    """Effective exact-source properties without UI-root lookup or Blender access."""
    return _material_lineage(json_path)['properties'] if json_path else {}


def load_mi_properties(json_path: str) -> dict:
    """Load identity-checked current properties with exact source-local parents."""
    if not json_path:
        return {}
    resolved = resolve_clothing_mi_json(json_path)
    return _material_lineage(resolved or json_path)['properties']


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
            "Tiling",
            "BreakupMaskTiling",
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
    # An authored N_UseDecal switch decides its slot, also when it is off: the
    # master material's default DecalColor (T_Decal_Checkers_01_C) stays bound
    # on disabled slots (Voltedge OrangePurple trousers rendered a checkerboard).
    authored_switch = False
    static_rt = props.get("StaticParametersRuntime") or {}
    if not isinstance(static_rt, dict):
        static_rt = {}
    for sw in static_rt.get("StaticSwitchParameters") or []:
        if not isinstance(sw, dict):
            continue
        name = (sw.get("ParameterInfo") or {}).get("Name", "")
        m = re.match(r"(\d+)_UseDecal$", name)
        if m:
            authored_switch = True
            if sw.get("Value", False):
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
    if not active_slots and not authored_switch:
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
            "surface_override": scalar_lookup.get(f"{idx}_SurfaceOverride", 0.0),
            "wear_override": scalar_lookup.get(f"{idx}_WearOverride", 0.0),
            "base_normal_override": scalar_lookup.get(f"{idx}_BaseNormalOverride", 0.0),
            "detail_normal_override": scalar_lookup.get(f"{idx}_DetailNormalOverride", 0.0),
            "tiling": scalar_lookup.get(f"{idx}_Tiling", 0.0),
            "color_override": color_override,
            "original_color": color_override < 0.9999,
        })
    return results


def _colours_from_palette_sidecar(json_path: str) -> dict:
    """ColorA/B/C(/2) from FModel ``*.palette.json`` next to a scalar-only MIC."""
    if not json_path:
        return {}
    sidecar = _fs_abspath(os.path.splitext(json_path)[0] + ".palette.json")
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
    sidecar = _fs_abspath(os.path.splitext(json_path)[0] + ".palette.json")
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
            **{key: float(item.get(key) or 0.0) for key in (
                "surface_override", "wear_override", "base_normal_override",
                "detail_normal_override", "tiling")},
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


def _parse_effective_clothing_mi(json_path: str) -> dict:
    lineage = _material_lineage(json_path)
    props, local = lineage['properties'], lineage['local_properties']
    if not props:
        return _empty_clothing_mi()
    colours = _colours_from_props(props)
    # Preserve the effective FModel palette sidecar fallback when the child
    # itself omitted colour vectors; inherited master whites must not hide it.
    if 'ColorA' not in _colours_from_props(local):
        colours.update(_colours_from_palette_sidecar(json_path))
    local_decals = _decals_from_props(local)
    sidecar_decals = _decals_from_palette_sidecar(json_path)
    decals = _decals_from_props(props) if local_decals else (sidecar_decals or _decals_from_props(props))
    return {
        'colours': colours,
        'ta_ids': _ta_ids_from_props(props),
        'zone_scalars': _zone_scalars_from_props(props),
        'mi_params': _mi_params_from_props(props, set(colours.keys())),
        'decals': decals,
        'inheritance': {'sources': list(lineage['sources']), 'unresolved': list(lineage['unresolved'])},
    }


def parse_clothing_mi_local(json_path: str) -> dict:
    """Parse an already-resolved MI and exact parents without bpy (worker-safe)."""
    if not json_path:
        return _empty_clothing_mi()
    try:
        return _parse_effective_clothing_mi(json_path)
    except Exception as exc:
        print(f"Arc Asset Importer: Failed to parse clothing MI (local) '{json_path}': {exc}")
        return _empty_clothing_mi()


def parse_clothing_mi(json_path: str) -> dict:
    """Parse effective current MI values; invalidate on any ancestor overwrite."""
    if not json_path:
        return _empty_clothing_mi()
    try:
        resolved = resolve_clothing_mi_json(json_path) or json_path
        key = _norm_cache_path(resolved)
        sidecar = os.path.splitext(resolved)[0] + '.palette.json'
        signature = (*material_lineage_signature(resolved), _material_file_stamp(sidecar))
        if key in _CLOTHING_MI_PARSE_CACHE and _CLOTHING_MI_PARSE_SIGNATURES.get(key) == signature:
            return _CLOTHING_MI_PARSE_CACHE[key]
        result = _parse_effective_clothing_mi(resolved)
        _CLOTHING_MI_PARSE_CACHE[key] = result
        _CLOTHING_MI_PARSE_SIGNATURES[key] = signature
        return result
    except Exception as exc:
        print(f"Arc Asset Importer: Failed to parse clothing MI '{json_path}': {exc}")
        return _empty_clothing_mi()


def parse_skin_colours(json_path: str) -> dict:
    return parse_clothing_mi(json_path)["colours"] if json_path else {}


def parse_texture_array_ids(json_path: str) -> dict:
    try:
        return _ta_ids_from_props(load_mi_properties(json_path))
    except Exception:
        return {}


def _texture_array_slice_index(path: str) -> int | None:
    """Read FModel ``_N`` or UEFormat ``_LAYERN`` array-slice filenames."""
    stem = os.path.splitext(os.path.basename(path))[0]
    match = re.search(r"_(?:LAYER_?)?(\d+)$", stem, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def find_slice_png(png_list: list, slice_idx: int) -> str:
    for fpath in png_list:
        if _texture_array_slice_index(fpath) == slice_idx:
            return fpath
    return ''


def build_slice_png_map(png_list: list) -> dict:
    """Map FModel/UEFormat slice index to PNG path for O(1) lookups."""
    result = {}
    for fpath in png_list:
        index = _texture_array_slice_index(fpath)
        if index is not None:
            result[index] = fpath
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
        print(f"Arc Asset Importer: Failed to parse decals from '{json_path}': {e}")
        return []

# ---------------------------------------------------------------------------
# Model type detection (folder-path domains first — see asset_domain.py)
# ---------------------------------------------------------------------------

def is_map_prop(psk_path: str) -> bool:
    """True for Environment / MapPlacements props (map Stage 2 materials)."""
    from ..Outfit_Accessories import asset_domain as _ad
    return _ad.is_environment_domain(psk_path)


def detect_model_type(psk_path: str) -> str:
    """Infer ``arc_model_type`` from Pioneer folder domain, then outfit subtype.

    Hard domains (path only): enemy / weapon / environment / outfit.
    Outfit subtypes still use name + occlusion heuristics *inside* outfit only.
    """
    from ..Outfit_Accessories import asset_domain as _ad

    domain = _ad.classify_asset_domain(psk_path)
    if domain == _ad.DOMAIN_ENEMY:
        return "enemy"
    if domain == _ad.DOMAIN_WEAPON:
        return "weapon"
    if domain == _ad.DOMAIN_ENVIRONMENT:
        return "map"
    # Effect meshes are never outfit parts, and their export folder collects other effects' textures
    # (T_MechanicalDebris beside SM_FX_RadialBlur_01) that the outfit misc heuristics below would borrow.
    if domain == _ad.DOMAIN_UNKNOWN and "/pioneer/effects/" in _ad.norm_asset_path(psk_path):
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
        # Backpack shells and raider tools ship a preview albedo (CR or BaseColor).
        # Occlusion on those folders used to force the outfit ArcTexturer, which
        # stays black when the only colour map is the shell BaseColor.
        if _is_gear_shell(psk_path) and _folder_has_gear_albedo(folder) and not folder_has_occlusion_png(folder):
            return "misc"
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
    # Item, effect and gameplay meshes (WhiteFlag VAT, projectiles, light beams) keep their textures beside the
    # instances, not the mesh; the surface rebuild resolves their authored slots from the mesh export.
    if domain == _ad.DOMAIN_UNKNOWN and any(seg in _ad.norm_asset_path(psk_path)
                                            for seg in ("/pioneer/items/", "/pioneer/effects/", "/pioneer/core/")):
        return "map"
    return "unknown"


def _is_gear_shell(psk_path: str) -> bool:
    """Backpack frame/strap/container/attachment/charm, or a raider tool."""
    norm = (psk_path or "").replace("\\", "/").lower()
    return "/characters/backpacks/" in norm or "/characters/raidertool/" in norm


def _folder_has_gear_albedo(folder: str) -> bool:
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    for fname in names:
        fl = fname.lower()
        if fl.endswith("_cr.png") or fl.endswith("_basecolor.png") or fl.endswith("_basecolour.png"):
            return True
    return False


def gear_shell_has_colorway(psk_path: str) -> bool:
    """True when a backpack/tool folder has a Skins/ MI the outfit batch can apply."""
    if not _is_gear_shell(psk_path):
        return False
    skins = os.path.join(os.path.dirname(psk_path), "Skins")
    if not os.path.isdir(skins):
        return False
    try:
        children = os.listdir(skins)
    except OSError:
        return False
    for child in children:
        folder = os.path.join(skins, child)
        if not os.path.isdir(folder):
            continue
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            low = name.lower()
            if low.startswith("mi_") and low.endswith(".json"):
                return True
    return False


def is_body(psk_path: str) -> bool:
    return "sk_body" in os.path.basename(psk_path).lower()

def is_hair(psk_path: str) -> bool:
    from ..Outfit_Accessories import asset_domain as _ad
    # Hair only under outfit domain (never env/gun/arc).
    if _ad.classify_asset_domain(psk_path) not in (
        _ad.DOMAIN_OUTFIT, _ad.DOMAIN_UNKNOWN,
    ):
        return False
    norm = psk_path.replace("\\", "/").lower()
    if "/characters/hairs/" in norm or "/characters/facialhairs/" in norm:
        return True
    # Outfit fur-card meshes (FoxHatCards) reuse Metahuman hair shaders.
    return is_hair_cards_part(psk_path)


def _mi_parent_blob(json_path: str) -> str:
    """Lowercased Parent ObjectName+ObjectPath for an MI JSON, or \"\"."""
    data = _load_mi_json_data(json_path)
    if data is None:
        return ""
    try:
        entry = (utils.first_ue_export(data, "MaterialInstanceConstant")
                 or utils.first_ue_export(data, "MaterialInstance"))
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
    json_path = _fs_abspath(json_path)
    if not json_path or not os.path.isfile(json_path):
        return False
    parent_s = _mi_parent_blob(json_path)
    if "m_hair" in parent_s or "hair_metahuman" in parent_s:
        return True
    data = _load_mi_json_data(json_path)
    if data is None:
        return False
    # Direct pak exports use the compact Textures map instead of UE property
    # arrays. Coverage plus an attribute atlas is an unambiguous card setup.
    flat = data[0] if isinstance(data, list) and data else data
    if isinstance(flat, dict):
        names = {str(k).lower() for k in (flat.get("Textures") or {})}
        if "coverage" in names and names & {"globalhaircolor", "attributemap", "tangent"}:
            return True
    try:
        entry = (utils.first_ue_export(data, "MaterialInstanceConstant")
                 or utils.first_ue_export(data, "MaterialInstance"))
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
    from ..Outfit_Accessories import asset_domain as _ad
    return _ad.is_weapon_domain(psk_path)

def is_enemy(psk_path: str) -> bool:
    from ..Outfit_Accessories import asset_domain as _ad
    return _ad.is_enemy_domain(psk_path)

def is_visor(psk_path: str, mat_slot_name: str = "") -> bool:
    """Return True if this part is a visor/glass/screen overlay."""
    from ..Outfit_Accessories import asset_domain as _ad
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
    """Outfit/item misc (CR, NOM/NEM packs, or a lone albedo). Never classifies env/gun/arc paths."""
    from ..Outfit_Accessories import asset_domain as _ad
    if _ad.classify_asset_domain(psk_path) in (
        _ad.DOMAIN_ENVIRONMENT, _ad.DOMAIN_WEAPON, _ad.DOMAIN_ENEMY,
    ):
        return False
    folder = os.path.dirname(psk_path)
    if folder_has_occlusion_png(folder):
        return False
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    has_color = False
    for fname in names:
        fl = fname.lower()
        if not fl.endswith(".png"):
            continue
        if fl.endswith("_colormask.png") or fl.endswith("_colourmask.png"):
            return False
        if any(fl.endswith(f"_{t}.png") for t in ("nom", "nem", "nxm", "nam", "cr")):
            has_color = True
        if fl.endswith("_basecolor.png") or fl.endswith("_basecolour.png"):
            has_color = True
    return has_color

def scan_hair_mis(psk_path: str) -> list:
    """Only card MIs, with exact visual-part bindings before folder defaults.

    A hairstyle can also have opaque accessories (FrenchBraids' hair tie).
    Those are never usable as the strand Coverage/Attribute material.
    """
    results = []
    seen = set()
    candidates = [binding["path"] for binding in authored_visual_part_materials(psk_path)]
    folder = os.path.dirname(psk_path)
    try:
        candidates.extend(os.path.join(folder, name) for name in sorted(os.listdir(folder))
                          if name.lower().startswith("mi_") and name.lower().endswith(".json"))
    except OSError:
        pass
    for path in candidates:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen and is_hair_cards_mi(path):
            seen.add(key)
            results.append((os.path.splitext(os.path.basename(path))[0], path))
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
    candidate_dirs.extend(directory for directory in authored_shared_skin_dirs(psk_path) if directory not in candidate_dirs)
    candidate_dirs.append(part_folder)
    for d in candidate_dirs:
        candidate = os.path.join(d, mi_stem + ".json")
        resolved = resolve_clothing_mi_json(candidate, mi_stem) if os.path.isfile(candidate) else ""
        if resolved:
            return resolved
    # A globally matching MI name does not prove that it belongs to this part.
    # Authored shared material banks are already included in candidate_dirs.
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
