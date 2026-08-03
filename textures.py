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
    keys.extend(["ColorA", "ColorB", "ColorC", "ColorA2", "ColorB2", "ColorC2"])
    
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
# Skin scanning
# ---------------------------------------------------------------------------

def scan_skins(psk_path: str, manual_folder: str = "") -> list:
    """Scan for all skin/colour options for a part."""
    manual_folder = bpy.path.abspath(manual_folder) if manual_folder else ""
    if manual_folder and os.path.isdir(manual_folder):
        results = []
        for skin_name in get_all_skin_dirs(manual_folder):
            skin_dir = os.path.join(manual_folder, skin_name)
            try:
                for fname in sorted(os.listdir(skin_dir)):
                    if fname.lower().endswith(".json"):
                        results.append((skin_name, os.path.join(skin_dir, fname)))
                        break
            except OSError:
                pass
        results.extend(_scan_mi_jsons_in_folder(manual_folder))
        return results
    
    results = []
    skins_folder = get_skins_folder(psk_path)
    if skins_folder:
        all_dirs = get_all_skin_dirs(skins_folder)
        for skin_name in all_dirs:
            skin_dir = os.path.join(skins_folder, skin_name)
            try:
                for fname in sorted(os.listdir(skin_dir)):
                    if fname.lower().endswith(".json"):
                        results.append((skin_name, os.path.join(skin_dir, fname)))
                        break
            except OSError:
                pass
    results.extend(_scan_main_folder_skins(psk_path))
    return results

def get_skins_folder(psk_path: str):
    part_folder = os.path.dirname(bpy.path.abspath(psk_path))
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

def _scan_mi_jsons_in_folder(folder: str) -> list:
    try:
        mi_files = sorted(
            f for f in os.listdir(folder)
            if f.lower().startswith("mi_") and f.lower().endswith(".json")
            and "persistence" not in f.lower()
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
        if common and stem.startswith(common):
            suffix = stem[len(common):].lstrip("_")
            skin_name = suffix if suffix else "__DEFAULT__"
        else:
            skin_name = stem
        results.append((skin_name, os.path.join(folder, fname)))
    return results

def _scan_main_folder_skins(psk_path: str) -> list:
    return _scan_mi_jsons_in_folder(os.path.dirname(bpy.path.abspath(psk_path)))

def scan_base_skin_textures(psk_path: str, selected_skin_name: str = "", manual_folder: str = "") -> list:
    manual_folder = bpy.path.abspath(manual_folder) if manual_folder else ""
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

def get_base_skin_json(psk_path: str, manual_folder: str = "") -> str:
    manual_folder = bpy.path.abspath(manual_folder) if manual_folder else ""
    skins_folder = manual_folder if (manual_folder and os.path.isdir(manual_folder)) else get_skins_folder(psk_path)
    if skins_folder:
        all_dirs = get_all_skin_dirs(skins_folder)
        if all_dirs:
            default_dir = get_default_skin_dir(all_dirs)
            search_dirs = [default_dir] if default_dir else all_dirs
            for skin_name in search_dirs:
                skin_dir = os.path.join(skins_folder, skin_name)
                try:
                    for fname in sorted(os.listdir(skin_dir)):
                        if fname.lower().endswith(".json"):
                            return os.path.join(skin_dir, fname)
                except OSError:
                    pass
    fallback_folder = skins_folder if manual_folder else os.path.dirname(bpy.path.abspath(psk_path))
    for skin_name, json_path in _scan_mi_jsons_in_folder(fallback_folder):
        if skin_name == "__DEFAULT__":
            return json_path
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
                return candidate
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
_MI_KEEP_TOKENS = ("Roughness", "Metallic", "Specular", "BaseTextureStrength")
_ZONE_SCALAR_SUFFIXES = (
    'BaseTextureStrength',
    'BaseColorMaskStrength',
    'MediumNormalStrength',
    'MediumNormalTiling',
    'EdgeNormalTiling',
    'CreaseNormalTiling',
)


def load_mi_properties(json_path: str) -> dict:
    """Load an MI JSON once and return its Properties dict (or {})."""
    if not json_path or not os.path.isfile(json_path):
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        entry = utils.first_ue_export(data, "MaterialInstanceConstant")
        return entry.get("Properties", {}) if entry else {}
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to load MI JSON '{json_path}': {e}")
        return {}


def _colours_from_props(props: dict) -> dict:
    colour_names = set(get_colour_keys())
    result = {}
    for param in props.get("VectorParameterValues", []):
        name = param.get("ParameterInfo", {}).get("Name", "")
        if name not in colour_names:
            continue
        pv = param.get("ParameterValue", {})
        result[name] = (
            float(pv.get("R", 1.0)),
            float(pv.get("G", 1.0)),
            float(pv.get("B", 1.0)),
            float(pv.get("A", 1.0)),
        )
    return result


def _ta_ids_from_props(props: dict) -> dict:
    result = {}
    for param in props.get("ScalarParameterValues", []):
        name = param.get("ParameterInfo", {}).get("Name", "")
        for suffix in _TA_ID_SUFFIXES:
            m = re.match(r'^(\d+)_' + re.escape(suffix) + r'$', name)
            if m:
                result[(m.group(1), suffix)] = int(float(param.get("ParameterValue", 0)))
                break
    return result


def _zone_scalars_from_props(props: dict) -> dict:
    """Per-zone scalars such as BaseTextureStrength / MediumNormalStrength."""
    result = {}
    for param in props.get("ScalarParameterValues", []):
        name = param.get("ParameterInfo", {}).get("Name", "")
        for suffix in _ZONE_SCALAR_SUFFIXES:
            m = re.match(r'^(\d+)_' + re.escape(suffix) + r'$', name)
            if not m:
                continue
            try:
                result[(m.group(1), suffix)] = float(param.get("ParameterValue", 0))
            except (TypeError, ValueError):
                pass
            break
    return result


def _mi_params_from_props(props: dict, known_colour_names: set) -> dict:
    result = {"scalars": [], "vectors": []}
    seen_scalar_names = set()
    for param in props.get("ScalarParameterValues", []):
        name = param.get("ParameterInfo", {}).get("Name", "")
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
    for param in props.get("VectorParameterValues", []):
        name = param.get("ParameterInfo", {}).get("Name", "")
        if not name or name in seen_vector_names or name in known_colour_names:
            continue
        if not any(tok in name for tok in _MI_KEEP_TOKENS):
            continue
        pv = param.get("ParameterValue", {})
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
    active_slots = set()
    for sw in props.get("StaticParametersRuntime", {}).get("StaticSwitchParameters", []):
        name = sw.get("ParameterInfo", {}).get("Name", "")
        m = re.match(r"(\d+)_UseDecal$", name)
        if m and sw.get("Value", False):
            active_slots.add(int(m.group(1)))
    if not active_slots:
        return []
    scalar_lookup = {}
    for sp in props.get("ScalarParameterValues", []):
        n = sp.get("ParameterInfo", {}).get("Name", "")
        scalar_lookup[n] = float(sp.get("ParameterValue", 0.0))
    tex_lookup = {}
    for tp in props.get("TextureParameterValues", []):
        n = tp.get("ParameterInfo", {}).get("Name", "")
        pv = tp.get("ParameterValue", {})
        ov = pv.get("ObjectName", "")
        op = pv.get("ObjectPath", "")
        m2 = re.search(r"'([^']+)'", ov)
        if m2:
            tex_lookup[n] = (m2.group(1), op)
    vec_lookup = {}
    for vp in props.get("VectorParameterValues", []):
        n = vp.get("ParameterInfo", {}).get("Name", "")
        pv = vp.get("ParameterValue", {})
        vec_lookup[n] = (
            float(pv.get("R", 0.0)),
            float(pv.get("G", 0.0)),
            float(pv.get("B", 0.0)),
            float(pv.get("A", 0.0)),
        )
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


def parse_clothing_mi(json_path: str) -> dict:
    """Parse colours, texture-array IDs, MI params, and decals from one MI load."""
    props = load_mi_properties(json_path)
    if not props:
        return {
            "colours": {},
            "ta_ids": {},
            "zone_scalars": {},
            "mi_params": {"scalars": [], "vectors": []},
            "decals": [],
        }
    colours = _colours_from_props(props)
    return {
        "colours": colours,
        "ta_ids": _ta_ids_from_props(props),
        "zone_scalars": _zone_scalars_from_props(props),
        "mi_params": _mi_params_from_props(props, set(colours.keys())),
        "decals": _decals_from_props(props),
    }


def parse_skin_colours(json_path: str) -> dict:
    return parse_clothing_mi(json_path)["colours"] if json_path else {}


def parse_texture_array_ids(json_path: str) -> dict:
    return _ta_ids_from_props(load_mi_properties(json_path))


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
    return _mi_params_from_props(load_mi_properties(json_path), known_colour_names)


def parse_decals(json_path: str) -> list:
    if not json_path:
        return []
    try:
        return _decals_from_props(load_mi_properties(json_path))
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to parse decals from '{json_path}': {e}")
        return []

# ---------------------------------------------------------------------------
# Model type detection
# ---------------------------------------------------------------------------

def detect_model_type(psk_path: str) -> str:
    basename = os.path.basename(psk_path).lower()
    folder = os.path.dirname(psk_path)
    if "head_face_" in basename:
        return "face"
    if is_body(psk_path):
        return "body"
    if is_hair(psk_path):
        return "hair"
    if is_weapon(psk_path) or is_enemy(psk_path):
        return "weapon"
    if is_visor(psk_path):
        return "visor"
    if folder_has_occlusion_png(folder):
        return "clothing"
    if is_misc(psk_path):
        return "misc"
    return "unknown"

def is_body(psk_path: str) -> bool:
    return "sk_body" in os.path.basename(psk_path).lower()

def is_hair(psk_path: str) -> bool:
    norm = psk_path.replace("\\", "/").lower()
    return "/characters/hairs/" in norm

def is_weapon(psk_path: str) -> bool:
    norm = psk_path.replace("\\", "/").lower()
    return "/firearms/" in norm

def is_enemy(psk_path: str) -> bool:
    norm = psk_path.replace("\\", "/").lower()
    return "/enemies/" in norm

def is_visor(psk_path: str, mat_slot_name: str = "") -> bool:
    """Return True if this part is a visor/glass/screen overlay."""
    norm = psk_path.replace("\\", "/").lower()
    basename = os.path.basename(psk_path).lower()
    if "visor" in norm or "visor" in basename:
        return True
    if mat_slot_name and any(k in mat_slot_name.lower() for k in ("visor", "glass", "screen")):
        return True
    return False

def is_misc(psk_path: str) -> bool:
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
    candidate_dirs.append(part_folder)
    for d in candidate_dirs:
        candidate = os.path.join(d, mi_stem + ".json")
        if os.path.isfile(candidate):
            return candidate
    mi_stem_norm = utils.normalize_folder_name(mi_stem)
    for d in candidate_dirs:
        try:
            for fname in os.listdir(d):
                if not fname.lower().endswith(".json"):
                    continue
                stem = os.path.splitext(fname)[0]
                if utils.normalize_folder_name(stem) == mi_stem_norm:
                    return os.path.join(d, fname)
        except OSError:
            pass
    local_mi_stems = []
    for d in candidate_dirs:
        try:
            for fname in os.listdir(d):
                if fname.lower().startswith("mi_") and fname.lower().endswith(".json"):
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
                    return os.path.join(d, fname)
    return ""