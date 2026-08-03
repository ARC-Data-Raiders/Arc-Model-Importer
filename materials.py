"""
Material setup functions for the Arc Raiders Importer
"""

import os
import re
import json
import bpy
import math
import difflib
from mathutils import Vector

from . import utils
from . import textures
from . import palette_calibration
from .properties import BODY_ALBEDO, BODY_NORMAL

# ---------------------------------------------------------------------------
# Session caches (map Stage 2 / shared MI reuse)
# ---------------------------------------------------------------------------
# Identical MI defs are rebuilt thousands of times on Buried City-scale maps when
# each unique SRC mesh creates its own Material datablock. Cache by absolute MI
# JSON path so shaders + texture loads happen once per material definition.
_FLAT_MI_CACHE: dict[str, dict] = {}
_MI_JSON_PATH_CACHE: dict[tuple, str] = {}
_SK_SLOTS_CACHE: dict[str, list] = {}
_SHARED_MI_MATERIALS: dict[str, object] = {}
_IMAGE_BY_PATH: dict[str, object] = {}

# ---------------------------------------------------------------------------
# Resolve contexts — assignment/discovery scopes (node wiring stays shared)
# ---------------------------------------------------------------------------
# Map/prop Stage 2 must NEVER pull Characters/Heroes/outfit cosmetics via
# basename walks or mismatched SM JSON bodies (FModel dump races).
CTX_MAP = "map"
CTX_OUTFIT = "outfit"
CTX_WEAPON = "weapon"
CTX_EFFECT = "effect"
CTX_ENEMY = "enemy"
CTX_ANY = "any"

# Path segments forbidden when resolving MIs/textures for map/environment props.
_MAP_FORBIDDEN_SEGMENTS = (
    "/characters/",
    "/heroes/",
    "/outfits/",
    "/scrappy/",
    "/backpacks/charms/",
    "/heads/",
)
# Soft allow markers for map (ObjectPath / MaterialLibrary / Environment).
_MAP_ALLOWED_SEGMENTS = (
    "/environment/",
    "/materiallibrary/",
    "/lighting/",
    "/effects/",
    "/architecture/",
    "/foliage/",
    "/landscape/",
    "/blueprints/",
    "/toolkit/",
)


def _norm_game_path(path: str) -> str:
    return (path or "").replace("\\", "/").lower()


def path_allowed_for_context(path: str, context: str = CTX_ANY) -> bool:
    """True when an on-disk or ObjectPath location may be used for ``context``.

    Shared node-wiring helpers ignore this; only MI/texture *discovery* gates.
    """
    if not path or context in ("", CTX_ANY):
        return True
    p = _norm_game_path(path)
    if context == CTX_MAP:
        if any(seg in p for seg in _MAP_FORBIDDEN_SEGMENTS):
            return False
        # Allow MaterialLibrary + Environment + remapped mesh folders.
        if any(seg in p for seg in _MAP_ALLOWED_SEGMENTS):
            return True
        # Sibling files next to an Environment mesh (no mid-path marker yet)
        # still pass when under PioneerGame/Content and not Characters.
        if "/pioneergame/content/" in p or "/content/pioneer/" in p:
            return not any(seg in p for seg in _MAP_FORBIDDEN_SEGMENTS)
        # /Game/ ObjectPaths without Content prefix
        if p.startswith("/game/"):
            return not any(seg in p for seg in _MAP_FORBIDDEN_SEGMENTS)
        return True
    if context == CTX_OUTFIT:
        return any(
            seg in p
            for seg in (
                "/characters/", "/heroes/", "/outfits/", "/scrappy/",
                "/heads/", "/backpacks/",
            )
        )
    if context == CTX_WEAPON:
        return "/weapons/" in p or "/gun/" in p or "/firearm/" in p or "/materiallibrary/" in p
    if context == CTX_EFFECT:
        return any(
            seg in p
            for seg in ("/effects/", "/decals/", "/materiallibrary/", "/toolkit/", "/environment/")
        )
    if context == CTX_ENEMY:
        return "/enemies/" in p or "/materiallibrary/" in p
    return True


def context_from_model_type(model_type: str = "", psk_path: str = "") -> str:
    """Map ``arc_model_type`` / path hints to a resolve context."""
    mt = (model_type or "").strip().lower()
    if mt == "map":
        return CTX_MAP
    if mt in ("clothing", "visor", "face", "body", "hair", "misc"):
        return CTX_OUTFIT
    if mt == "weapon":
        return CTX_WEAPON
    pl = _norm_game_path(psk_path)
    if "/environment/" in pl or "/mapplacements/" in pl:
        return CTX_MAP
    if "/characters/" in pl or "/heroes/" in pl:
        return CTX_OUTFIT
    if "/weapons/" in pl:
        return CTX_WEAPON
    if "/enemies/" in pl:
        return CTX_ENEMY
    return CTX_ANY


def clear_material_session_caches():
    """Drop in-memory caches (e.g. after Pioneer root change). Keeps Blender data."""
    _FLAT_MI_CACHE.clear()
    _MI_JSON_PATH_CACHE.clear()
    _SK_SLOTS_CACHE.clear()
    _SHARED_MI_MATERIALS.clear()
    _IMAGE_BY_PATH.clear()


def warm_shared_mi_material_cache():
    """Index already-built materials stamped with arc_mi_path (Stage 2 re-runs)."""
    for mat in bpy.data.materials:
        key = str(mat.get("arc_mi_path", "") or "").strip()
        if not key:
            continue
        if key not in _SHARED_MI_MATERIALS:
            _SHARED_MI_MATERIALS[key] = mat


def _norm_path_key(path: str) -> str:
    if not path:
        return ""
    return os.path.normcase(os.path.normpath(path))


def _load_image_cached(fpath: str):
    """Load an image once per path; faster than repeated check_existing scans."""
    if not fpath:
        return None
    key = _norm_path_key(fpath)
    img = _IMAGE_BY_PATH.get(key)
    if img is not None:
        try:
            _ = img.name
            return img
        except ReferenceError:
            _IMAGE_BY_PATH.pop(key, None)
    img = bpy.data.images.load(fpath, check_existing=True)
    _IMAGE_BY_PATH[key] = img
    return img


def _get_or_build_shared_mi_material(
    mi_stem: str,
    mi_path: str,
    psk_path: str,
    slot_lower: str = "",
):
    """Return a shared Material datablock for this MI JSON (build once).

    Cache key is the MI JSON path. Stale datablocks that were mis-routed through
    the enemy-decal family (env NAO trims) are rebuilt when classification now
    disagrees.
    """
    if not mi_path or not os.path.isfile(mi_path):
        return None
    key = _norm_path_key(mi_path)
    stem = (mi_stem or os.path.splitext(os.path.basename(mi_path))[0] or "MI").strip()
    slot_l = (slot_lower or stem).lower()

    def _expected_family() -> str:
        try:
            mi_probe = _parse_flat_mi_json(mi_path)
        except Exception:
            mi_probe = {}
        return classify_mi_family(mi_probe, stem.lower(), slot_l)

    def _stale_decal_misclass(mat) -> bool:
        """Env trim previously stamped as enemy/map decal — must rebuild."""
        fam = str(mat.get("arc_mi_family") or "")
        if fam != FAMILY_DECAL:
            return False
        expected = _expected_family()
        return expected != FAMILY_DECAL

    def _stale_shared(mat) -> bool:
        return (
            _stale_decal_misclass(mat)
            or _needs_map_decal_mask_rebuild(mat, mi_path)
            or _needs_trim_setup_rebuild(mat, mi_path)
            or _needs_proptrim_or_glass_rebuild(mat, mi_path, stem)
            or _needs_water_setup_rebuild(mat, mi_path)
        )

    mat = _SHARED_MI_MATERIALS.get(key)
    if mat is not None:
        try:
            _ = mat.name
            if _stale_shared(mat):
                _SHARED_MI_MATERIALS.pop(key, None)
                try:
                    mat.name = f"{mat.name}_stale_decal"
                except Exception:
                    pass
                mat = None
            else:
                return mat
        except ReferenceError:
            _SHARED_MI_MATERIALS.pop(key, None)
            mat = None

    low = stem.lower()
    if low.startswith("mi_") or low.startswith("m_"):
        mat_name = stem
    else:
        mat_name = f"MI_{stem}"
    # Avoid colliding with a leftover empty/placeholder material of the same name
    existing = bpy.data.materials.get(mat_name)
    if existing is not None and str(existing.get("arc_mi_path", "") or "") == key:
        if _stale_shared(existing):
            try:
                existing.name = f"{existing.name}_stale_decal"
            except Exception:
                pass
        else:
            _SHARED_MI_MATERIALS[key] = existing
            return existing

    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    try:
        mat["arc_mi_path"] = key
        mat["arc_mi_stem"] = stem
    except Exception:
        pass
    try:
        _dispatch_weapon_slot_material(
            mat,
            mi_path,
            psk_path or "",
            stem.lower(),
            (slot_lower or stem).lower(),
        )
    except Exception as e:
        print(
            f"Arc Raiders PSK Importer: Failed shared MI '{stem}' "
            f"({mi_path}): {e}"
        )
        try:
            bpy.data.materials.remove(mat)
        except Exception:
            pass
        return None
    _SHARED_MI_MATERIALS[key] = mat
    return mat


# ---------------------------------------------------------------------------
# Outfit colour helpers (ColorMask_XYZ / BaseColorOverlay)
# ---------------------------------------------------------------------------

# Node layout: minimum clearance between node bounding boxes (Blender editor units ≈ px).
_NODE_PAD_X = 120.0
_NODE_PAD_Y = 100.0
_NODE_FALLBACK_W = 240.0
_NODE_FALLBACK_H = 180.0

# Re-export for callers / tests that import from materials.
base_overlay_mix_factor = palette_calibration.base_overlay_mix_factor


def _node_editor_size(node) -> tuple:
    """Return (width, height) for layout; prefer live dimensions, else fallbacks."""
    try:
        dims = getattr(node, "dimensions", None)
        if dims is not None and float(dims[0]) > 1.0 and float(dims[1]) > 1.0:
            return float(dims[0]), float(dims[1])
    except Exception:
        pass
    w = float(getattr(node, "width", 0) or 0) or _NODE_FALLBACK_W
    if getattr(node, "hide", False):
        return w, 36.0
    return w, _NODE_FALLBACK_H


def apply_node_graph_padding(nodes, pad_x: float = _NODE_PAD_X, pad_y: float = _NODE_PAD_Y):
    """Nudge nodes so bounding boxes do not overlap (pad_x / pad_y clearance).

    Location is treated as the top-left of the node. Frames are left alone.
    Collapses texture previews when possible so large previews do not stack.
    """
    items = []
    for node in nodes:
        if getattr(node, "bl_idname", "") == "NodeFrame" or node.type == "FRAME":
            continue
        if hasattr(node, "hide_preview"):
            try:
                node.hide_preview = True
            except Exception:
                pass
        w, h = _node_editor_size(node)
        items.append([node, float(node.location.x), float(node.location.y), w, h])

    # Left → right, top → bottom (Blender Y increases upward).
    items.sort(key=lambda t: (t[1], -t[2]))

    for i in range(len(items)):
        node_i, xi, yi, wi, hi = items[i]
        for j in range(i):
            _nj, xj, yj, wj, hj = items[j]
            # Axis-aligned boxes with padding.
            overlap_x = xi < xj + wj + pad_x and xi + wi + pad_x > xj
            overlap_y = yi > yj - hj - pad_y and yi - hi - pad_y < yj
            if not (overlap_x and overlap_y):
                continue
            # Same column → push down; otherwise push right.
            if abs(xi - xj) < max(wj, wi) * 0.55:
                yi = yj - hj - pad_y
            else:
                xi = xj + wj + pad_x
            items[i][1], items[i][2] = xi, yi
        node_i.location = (items[i][1], items[i][2])


def _zone_to_cm_instance(zone_str: str) -> int:
    """Map Colour zone 1..8 to ColorMask_XYZ instance index (0/1/2)."""
    try:
        z = int(zone_str)
    except (TypeError, ValueError):
        return 0
    if z in (1, 3, 5):
        return 0
    if z in (2, 4, 6):
        return 1
    if z in (7, 8):
        return 2
    return 0


# ---------------------------------------------------------------------------
# Material setup - Arc Texturer
# ---------------------------------------------------------------------------

def setup_arc_texturer_material(obj, folder: str, colours: dict, psk_path: str = "", 
                                json_path: str = "", decal_folder: str = "", 
                                selected_skin_name: str = "", manual_skins_folder: str = "",
                                mi_data: dict = None, main_pngs: list = None, base_pngs: list = None):
    if not utils.ensure_arc_texturer_node_group():
        print(f"Arc Raiders PSK Importer: Skipping material for '{obj.name}' — Arc Texturer unavailable.")
        return
    
    mat = bpy.data.materials.new(name=obj.name + "_Mat")
    mat.use_nodes = True
    obj.active_material = mat
    
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    
    group_node = nodes.new("ShaderNodeGroup")
    group_node.node_tree = bpy.data.node_groups[utils._NODE_GROUP]
    group_node.location = (0, 0)
    
    # Categorise main folder PNGs
    if main_pngs is None:
        try:
            main_pngs = sorted(f for f in os.listdir(folder) if f.lower().endswith(".png"))
        except OSError:
            main_pngs = []
    
    occlusion_tex = []
    normal_tex = []
    basecolor_tex = []
    colormask_tex = []
    other_tex = []
    
    for fname in main_pngs:
        role = textures.identify_texture(fname)
        if role == "occlusion":
            occlusion_tex.append(fname)
        elif role == "normal":
            normal_tex.append(fname)
        elif role == "basecolor":
            basecolor_tex.append(fname)
        elif role == "colormask":
            colormask_tex.append(fname)
        else:
            other_tex.append(fname)
    
    # Categorise base skin PNGs
    if base_pngs is None:
        base_pngs = textures.scan_base_skin_textures(psk_path, selected_skin_name, manual_skins_folder) if psk_path else []
    base_normals = [p for p in base_pngs if textures.base_skin_texture_group(os.path.basename(p)) == "normals"]
    base_masks = [p for p in base_pngs if textures.base_skin_texture_group(os.path.basename(p)) == "masks"]
    base_other = [p for p in base_pngs if textures.base_skin_texture_group(os.path.basename(p)) == "other"]
    
    ROW_H = -400
    COL_W = 400
    
    def place_column(file_list, col_x, is_paths=False, non_color=False, connect_fn=None, collapsed=False, start_y=0):
        nodes_created = []
        step = -40 if collapsed else ROW_H
        for row, item in enumerate(file_list):
            fpath = item if is_paths else os.path.join(folder, item)
            fname = os.path.basename(fpath)
            img = bpy.data.images.load(fpath, check_existing=True)
            if non_color:
                img.colorspace_settings.name = "Non-Color"
            node = nodes.new("ShaderNodeTexImage")
            node.image = img
            node.label = fname
            node.hide = collapsed
            if hasattr(node, "hide_preview"):
                try:
                    node.hide_preview = True
                except Exception:
                    pass
            node.interpolation = "Cubic"
            node.location = (col_x, start_y + row * step)
            nodes_created.append(node)
            if connect_fn:
                connect_fn(node, img)
        return nodes_created
    
    ABOVE_X = -700
    ABOVE_Y = 1000
    ABOVE_STEP = -32
    
    def connect_occlusion(node, img):
        links.new(node.outputs["Color"], group_node.inputs["Main Texture"])
    place_column(occlusion_tex, ABOVE_X, connect_fn=connect_occlusion, collapsed=True, start_y=ABOVE_Y)
    
    def connect_normal(node, img):
        img.colorspace_settings.name = "Non-Color"
        links.new(node.outputs["Color"], group_node.inputs["Base Normal"])
    place_column(normal_tex, ABOVE_X + 20, connect_fn=connect_normal, collapsed=True,
                 start_y=ABOVE_Y + len(occlusion_tex) * ABOVE_STEP - 40)
    
    def connect_basecolor(node, img):
        if "Base Color" in group_node.inputs:
            links.new(node.outputs["Color"], group_node.inputs["Base Color"])
    place_column(basecolor_tex, ABOVE_X + 40, connect_fn=connect_basecolor, collapsed=True,
                 start_y=ABOVE_Y + (len(occlusion_tex) + len(normal_tex)) * ABOVE_STEP - 80)
    
    colormask_nodes = place_column(colormask_tex, -2200)
    
    _SECTION_SOCKETS = [
        (["Colour 1", "Colour 3", "Colour 5"],
         ["Roughness 1", "Roughness 3", "Roughness 5"],
         ["Metallic 1", "Metallic 3", "Metallic 5"]),
        (["Colour 2", "Colour 4", "Colour 6"],
         ["Roughness 2", "Roughness 4", "Roughness 6"],
         ["Metallic 2", "Metallic 4", "Metallic 6"]),
        (["Colour 7", "Colour 8"],
         ["Roughness 7", "Roughness 8"],
         ["Metallic 7", "Metallic 8"]),
    ]
    
    _CM_COLOUR_INPUTS = {
        # Default AUTO map — resolved per material via palette_calibration.resolve_routing.
        # Odd zones 1/3/5/7 and zone 8 use ColorA/B/C; even zones 2/4/6 use ColorA2/B2/C2.
        # Cooked data has no StaticSwitch for this; overrides cover ambiguous items.
        0: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
        1: {"X_Green": "ColorA2", "Y_Blue": "ColorB2", "Z_Pink": "ColorC2"},
        2: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
    }

    # Per-material / scene / manifest routing (never outfit/object name special cases).
    _mi_props = {}
    if json_path:
        try:
            _mi_props = textures.load_mi_properties(json_path) or {}
        except Exception:
            _mi_props = {}
    _mat_name = palette_calibration.material_name_from_json_path(json_path)
    _parent_name = palette_calibration.parent_from_mi_props(_mi_props)
    _scene_mode = "auto"
    _mat_mode = "auto"
    _manifest_mode = "auto"
    try:
        import bpy as _bpy
        _scene_mode = str(getattr(_bpy.context.scene, "arc_palette_mode", "auto") or "auto")
        _mat_mode = str(obj.get("arc_palette_mode", "") or mat.get("arc_palette_mode", "auto") or "auto")
        _manifest_mode = str(obj.get("arc_palette_routing", "auto") or "auto")
    except Exception:
        pass
    _routing_mode, _routing_source, _material_key, _uses_secondary = palette_calibration.resolve_routing(
        _mat_name, _parent_name,
        scene_mode=_scene_mode,
        material_mode=_mat_mode,
        manifest_mode=_manifest_mode,
    )
    _CM_COLOUR_INPUTS = palette_calibration.section_colour_inputs(_routing_mode)
    try:
        if str(_mat_mode).lower() not in ("", "auto"):
            mat["arc_palette_mode"] = str(_mat_mode).lower()
            obj["arc_palette_mode"] = str(_mat_mode).lower()
        mat["arc_material_key"] = _material_key
        mat["arc_palette_resolved"] = _routing_mode
        mat["arc_palette_source"] = _routing_source
        obj["arc_material_key"] = _material_key
        obj["arc_palette_resolved"] = _routing_mode
    except Exception:
        pass
    
    _cm_group_ok = utils.ensure_colormask_node_group()
    _single_colormask = len(colormask_nodes) == 1
    _cm_groups = []
    cm_frame = None

    for cm_idx, (colour_socks, rough_socks, metal_socks) in enumerate(_SECTION_SOCKETS):
        if not any(s in group_node.inputs for s in colour_socks):
            _cm_groups.append(None)
            continue
        cm_group = None
        if _cm_group_ok:
            cm_group = nodes.new("ShaderNodeGroup")
            cm_group.node_tree = bpy.data.node_groups[utils._COLORMASK_GROUP]
            cm_group.label = f"ColorMask_XYZ (instance {cm_idx + 1})"
            # Vertical gap ≥ ~800 so the three XYZ groups never stack on import.
            if cm_idx == 2:
                cm_group.location = (-800, -2000)
            else:
                cm_group.location = (-800, -cm_idx * 800)
            if cm_frame is None:
                cm_frame = nodes.new("NodeFrame")
                cm_frame.label = "ColorMask Sections → Colour / Rough / Metal"
                cm_frame.label_size = 18
            cm_group.parent = cm_frame
        _cm_groups.append(cm_group)
        
        if _single_colormask:
            src_node = colormask_nodes[0]
        else:
            src_node = colormask_nodes[cm_idx] if cm_idx < len(colormask_nodes) else None
        
        if cm_group and src_node:
            if cm_group.inputs:
                links.new(src_node.outputs["Color"], cm_group.inputs[0])
            def _wire_out(cm_grp, out_name, arc_sock):
                if arc_sock not in group_node.inputs:
                    return
                out = cm_grp.outputs.get(out_name)
                if out:
                    links.new(out, group_node.inputs[arc_sock])
            for csock, rsock, msock in zip(colour_socks, rough_socks, metal_socks):
                _wire_out(cm_group, "Mask_Color", csock)
                _wire_out(cm_group, "Mask_Roughness", rsock)
                _wire_out(cm_group, "Mask_Metal", msock)
        elif src_node and not cm_group:
            for csock in colour_socks:
                if csock in group_node.inputs:
                    links.new(src_node.outputs["Color"], group_node.inputs[csock])
    
    place_column(other_tex, -2400, start_y=-2000)
    
    tex_coord_node = nodes.new("ShaderNodeTexCoord")
    tex_coord_node.location = (-5800, 200)
    tex_coord_node.label = "Texture Coordinate"
    
    mapping_node = nodes.new("ShaderNodeMapping")
    mapping_node.location = (-5200, 200)
    mapping_node.label = "Mapping"
    mapping_node.inputs["Scale"].default_value = (25.0, 25.0, 25.0)
    links.new(tex_coord_node.outputs["UV"], mapping_node.inputs["Vector"])
    
    _normal_nodes = {}
    _mask_nodes = {}
    _color_nodes = {}
    _pattern_nodes = {}
    # Lazily create slice image nodes only when an MI ID actually references them.
    
    _mask_col_nodes = place_column(base_masks, -3300, is_paths=True, non_color=True)
    for n in _mask_col_nodes:
        if n and n.image:
            stem = os.path.splitext(os.path.basename(n.image.filepath))[0]
            _mask_nodes[stem] = n
    
    ta_ids = (mi_data or {}).get("ta_ids")
    if ta_ids is None:
        ta_ids = textures.parse_texture_array_ids(json_path) if json_path else {}
    zone_scalars = (mi_data or {}).get("zone_scalars") or {}
    slice_maps = {
        id(base_normals): textures.build_slice_png_map(base_normals),
        id(base_masks): textures.build_slice_png_map(base_masks),
        id(base_other): textures.build_slice_png_map(base_other),
    }
    
    ID_TO_SOCKET = [
        ("BaseNormalID", "Mix {zone}", _normal_nodes, base_normals),
        ("MediumNormalID", "Medium Normal {zone}", _normal_nodes, base_normals),
        ("EdgeNormalID", "Edge Normal {zone}", _normal_nodes, base_normals),
        ("CreaseNormalID", "Crease Normal {zone}", _normal_nodes, base_normals),
        ("BaseRoughnessID", "Roughness {zone}", _mask_nodes, base_masks),
        ("EdgeRoughnessID", "Edge Roughness {zone}", _mask_nodes, base_masks),
        ("CreaseRoughnessID", "Crease Roughness {zone}", _mask_nodes, base_masks),
        ("CreaseMaskID", "Crease Mask {zone}", _mask_nodes, base_masks),
        ("EdgeMaskID", "Edge Mask {zone}", _mask_nodes, base_masks),
        # ColorTexture / Pattern are wired below with BaseTextureStrength / PatternColor gates.
    ]
    
    _zones_with_normal = set()
    _ta_nodes_wired_to_arc = set()
    
    def _ensure_slice_node(node_dict, png_list, slice_idx, non_color=True):
        slice_map = slice_maps[id(png_list)]
        png_path = slice_map.get(slice_idx) or textures.find_slice_png(png_list, slice_idx)
        if not png_path:
            return None, ""
        stem = os.path.splitext(os.path.basename(png_path))[0]
        tex_nd = node_dict.get(stem)
        if tex_nd is None:
            img = bpy.data.images.load(png_path, check_existing=True)
            if non_color:
                img.colorspace_settings.name = "Non-Color"
            tex_nd = nodes.new("ShaderNodeTexImage")
            tex_nd.image = img
            tex_nd.label = os.path.basename(png_path)
            tex_nd.interpolation = "Cubic"
            tex_nd.location = (-3000, len(node_dict) * ROW_H)
            node_dict[stem] = tex_nd
        return tex_nd, png_path

    for (id_suffix, arc_template, node_dict, png_list) in ID_TO_SOCKET:
        for (zone, key_sfx), slice_idx in list(ta_ids.items()):
            if key_sfx != id_suffix:
                continue
            arc_sock = arc_template.replace("{zone}", zone)
            if arc_sock not in group_node.inputs:
                continue
            tex_nd, png_path = _ensure_slice_node(node_dict, png_list, slice_idx, non_color=True)
            if tex_nd is None:
                print(f"    WARNING: No PNG for slice {slice_idx} ({arc_sock})")
                continue
            links.new(tex_nd.outputs["Color"], group_node.inputs[arc_sock])
            _ta_nodes_wired_to_arc.add(id(tex_nd))
            if id_suffix == "BaseNormalID" and zone not in _zones_with_normal:
                set_enable_slider(group_node, zone)
                _zones_with_normal.add(zone)

    # Pattern: only when PatternColorA/B/C has a live (non-zero alpha) swatch.
    # ColorTexture is applied after ColorMask→Colour N (see modulate block below).
    for (zone, key_sfx), slice_idx in list(ta_ids.items()):
        if key_sfx != "PatternID":
            continue
        live_pattern = False
        for suffix in ("PatternColorA", "PatternColorB", "PatternColorC"):
            rgba = (colours or {}).get(f"{zone}_{suffix}") if colours else None
            if rgba is not None and abs(float(rgba[3])) > 1e-4 and not textures.skip_colour(rgba):
                live_pattern = True
                break
        if not live_pattern:
            continue
        arc_sock = f"Pattern {zone}"
        if arc_sock not in group_node.inputs:
            continue
        tex_nd, _ = _ensure_slice_node(_pattern_nodes, base_other, slice_idx, non_color=False)
        if tex_nd is None:
            continue
        links.new(tex_nd.outputs["Color"], group_node.inputs[arc_sock])
        _ta_nodes_wired_to_arc.add(id(tex_nd))
    
    for nd in list(_normal_nodes.values()) + list(_mask_nodes.values()) + list(_pattern_nodes.values()):
        if nd is None:
            continue
        if id(nd) not in _ta_nodes_wired_to_arc:
            continue
        if not any(lnk.to_socket.name == "Vector" and lnk.to_node == nd for lnk in mat.node_tree.links):
            links.new(mapping_node.outputs["Vector"], nd.inputs["Vector"])
    
    force_white = {
        'Colour 2',
        'Roughness 1', 'Roughness 2', 'Roughness 3',
        'Roughness 4', 'Roughness 5', 'Roughness 6',
        'Roughness 7', 'Roughness 8', 'Roughness 9',
    }
    for socket in group_node.inputs:
        if socket.name in force_white:
            try:
                socket.default_value = (1.0, 1.0, 1.0, 1.0)
            except Exception:
                pass
    
    output_node = nodes.new("ShaderNodeOutputMaterial")
    output_node.location = (450, 0)
    if group_node.outputs:
        char_out = group_node.outputs.get("Character") or group_node.outputs[0]
        links.new(char_out, output_node.inputs["Surface"])
        disp_out = (group_node.outputs.get("Displacement") or group_node.outputs.get("Displacement Map"))
        if disp_out:
            links.new(disp_out, output_node.inputs["Displacement"])
    
    # Skin colour RGB nodes
    if colours:
        colour_keys = textures.get_colour_keys()
        
        _ABC_KEYS = ["ColorA", "ColorB", "ColorC"]
        _ABC2_KEYS = ["ColorA2", "ColorB2", "ColorC2"]
        # These six keys drive the ColorMask_XYZ instances directly (see _CM_COLOUR_INPUTS
        # below). Unlike overlay/pattern/swatch keys, they carry no alpha-based "unused"
        # signal (alpha is always 1.0) and a literal black/white/red value here is real
        # authored data, not a sentinel — dropping it left the corresponding ColorMask_XYZ
        # socket (e.g. "Z_Pink") unlinked and falling back to the node group's raw debug
        # default. Always create + wire a node for these regardless of value.
        _CORE_COLOUR_KEYS = set(_ABC_KEYS) | set(_ABC2_KEYS)
        _abc_row = 0
        _abc2_row = 0
        ABC_X = -1600
        ABC2_X = -1280
        ABC_Y_START = 280
        ABC_ROW_H = -300
        
        j = 0
        for key in colour_keys:
            rgba = colours.get(key)
            if rgba is None:
                continue
            # BaseColorOverlay is not a sentinel by itself: BaseColorMaskStrength decides
            # whether the authored overlay or ColorABC drives this zone.
            if (key not in _CORE_COLOUR_KEYS and
                    "_BaseColorOverlay" not in key and
                    textures.skip_colour(rgba)):
                continue
            rgb_node = nodes.new("ShaderNodeRGB")
            rgb_node.label = key
            rgb_node.name = key
            rgb_node.outputs[0].default_value = (rgba[0], rgba[1], rgba[2], rgba[3])
            
            is_crease_or_edge = ("_CreaseColorOverlay" in key or "_EdgeColorOverlay" in key)
            is_base_overlay = "_BaseColorOverlay" in key
            
            if is_crease_or_edge:
                zone_num = int(key.split("_")[0]) if key[0].isdigit() else 0
                is_crease = "_Crease" in key
                pair_row = zone_num - 1
                pair_col = 0 if is_crease else 1
                rgb_node.hide = True
                rgb_node.location = (250 + pair_col * 260, -450 - pair_row * 120)
            elif is_base_overlay:
                zone_num = int(key.split("_")[0]) if key[0].isdigit() else 0
                rgb_node.location = (-1000, 400 - (zone_num - 1) * 280)
            elif key in _ABC_KEYS:
                rgb_node.location = (ABC_X, ABC_Y_START + _abc_row * ABC_ROW_H)
                _abc_row += 1
            elif key in _ABC2_KEYS:
                rgb_node.location = (ABC2_X, ABC_Y_START + _abc2_row * ABC_ROW_H)
                _abc2_row += 1
            else:
                rgb_node.location = (-1900 + (j % 2) * 280, -1100 - (j // 2) * 280)
                j += 1
        
        for inst_idx, input_map in _CM_COLOUR_INPUTS.items():
            cm_grp = _cm_groups[inst_idx] if inst_idx < len(_cm_groups) else None
            if not cm_grp:
                continue
            for socket_name, colour_key in input_map.items():
                rgb_nd = nodes.get(colour_key)
                if rgb_nd and socket_name in cm_grp.inputs:
                    links.new(rgb_nd.outputs[0], cm_grp.inputs[socket_name])
        # Compact palette routing log + calibration report beside the skin JSON.
        _sec = [i + 1 for i, f in enumerate(_uses_secondary) if f and i < 8]
        _pri = [i + 1 for i, f in enumerate(_uses_secondary) if (not f) and i < 8]
        print(
            "Arc Raiders PSK Importer: ColorMask palette map - "
            f"mode={_routing_mode} src={_routing_source} key={_material_key} "
            f"PRI zones {_pri}; SEC zones {_sec}"
            + (f"; skin={selected_skin_name}" if selected_skin_name else "")
        )
        try:
            _pri_t = (
                tuple(colours.get("ColorA", (1, 1, 1, 1))[:3]),
                tuple(colours.get("ColorB", (1, 1, 1, 1))[:3]),
                tuple(colours.get("ColorC", (1, 1, 1, 1))[:3]),
            )
            _sec_authored = any(k in colours for k in ("ColorA2", "ColorB2", "ColorC2"))
            _sec_t = (
                tuple(colours.get("ColorA2", _pri_t[0])[:3]),
                tuple(colours.get("ColorB2", _pri_t[1])[:3]),
                tuple(colours.get("ColorC2", _pri_t[2])[:3]),
            )
            _report = palette_calibration.build_report(
                _mat_name, _parent_name, _routing_mode, _routing_source,
                _pri_t, _sec_t, _sec_authored,
            )
            if json_path:
                _report["skin_json"] = os.path.basename(json_path)
                palette_calibration.write_report_beside_skin(json_path, _report)
        except Exception as _exc:
            print(f"Arc Raiders PSK Importer: palette report skipped: {_exc}")
        
        _OVERLAY_SOCKET_MAP = {
            # BaseColorOverlay must NOT replace Colour N — that socket stays ColorMask→ColorABC.
            # White BaseColorOverlay is a no-op multiply and was previously wiping the tint path.
            "_CreaseColorOverlay": "Crease",
            "_EdgeColorOverlay": "Edge",
        }
        
        for key in colour_keys:
            rgba = colours.get(key)
            if rgba is not None and textures.skip_colour(rgba):
                continue
            for suffix, arc_prefix in _OVERLAY_SOCKET_MAP.items():
                if not key.endswith(suffix):
                    continue
                zone_str = key[: -len(suffix)]
                if not zone_str.isdigit():
                    continue
                arc_sock = f"{arc_prefix} {zone_str}"
                rgb_nd = nodes.get(key)
                if rgb_nd and arc_sock in group_node.inputs:
                    links.new(rgb_nd.outputs[0], group_node.inputs[arc_sock])
                break
    
    # base = mix(ColorMask_XYZ, BaseColorOverlay, factor_from_overlay_colour)
    # Fac 0 → XYZ; Fac 1 → overlay (see base_overlay_mix_factor).
    # albedo = base * mix(white, ColorTex[ColorTextureID], BaseTextureStrength)
    for zone_str in [str(z) for z in range(1, 10)]:
        colour_sock = f"Colour {zone_str}"
        if colour_sock not in group_node.inputs:
            continue
        colour_in = group_node.inputs[colour_sock]
        if not colour_in.is_linked:
            continue
        src = colour_in.links[0].from_socket

        overlay_nd = nodes.get(f"{zone_str}_BaseColorOverlay")
        if overlay_nd is not None:
            overlay_rgba = (colours or {}).get(f"{zone_str}_BaseColorOverlay")
            mix_fac = base_overlay_mix_factor(overlay_rgba)
            mat.node_tree.links.remove(colour_in.links[0])
            base_mix = nodes.new("ShaderNodeMix")
            base_mix.data_type = 'RGBA'
            base_mix.blend_type = 'MIX'
            cm_inst = _zone_to_cm_instance(zone_str)
            base_mix.label = f"Base Overlay ↔ XYZ{cm_inst + 1} {zone_str}"
            # Column of mix nodes with ≥100px vertical gap (fallback height ~180).
            base_mix.location = (-700, 200 - int(zone_str) * 280)
            base_mix.inputs[0].default_value = mix_fac
            # A = ColorMask_XYZ, B = overlay → Fac 0 favors XYZ, Fac 1 favors overlay.
            links.new(src, base_mix.inputs[6])
            links.new(overlay_nd.outputs[0], base_mix.inputs[7])
            links.new(base_mix.outputs[2], colour_in)
            src = base_mix.outputs[2]

        strength = float(zone_scalars.get((zone_str, "BaseTextureStrength"), 0.0) or 0.0)
        slice_idx = ta_ids.get((zone_str, "ColorTextureID"))
        if strength <= 1e-4 or slice_idx is None:
            continue
        tex_nd, _ = _ensure_slice_node(_color_nodes, base_other, slice_idx, non_color=False)
        if tex_nd is None:
            continue
        if colour_in.is_linked:
            mat.node_tree.links.remove(colour_in.links[0])
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = 'RGBA'
        mix.blend_type = 'MIX'
        mix.label = f"ColorTex Strength {zone_str}"
        mix.location = (-420, 200 - int(zone_str) * 280)
        mix.inputs[0].default_value = min(max(strength, 0.0), 1.0)
        mix.inputs[6].default_value = (1.0, 1.0, 1.0, 1.0)
        links.new(tex_nd.outputs["Color"], mix.inputs[7])
        mul = nodes.new("ShaderNodeMix")
        mul.data_type = 'RGBA'
        mul.blend_type = 'MULTIPLY'
        mul.label = f"ColorABC * Tex {zone_str}"
        mul.location = (-140, 200 - int(zone_str) * 280)
        mul.inputs[0].default_value = 1.0
        links.new(src, mul.inputs[6])
        links.new(mix.outputs[2], mul.inputs[7])
        links.new(mul.outputs[2], group_node.inputs[colour_sock])
        # Optional Color Texture socket: feed the same strength mix if ArcTexturer exposes it.
        tex_sock = f"Color Texture {zone_str}"
        if tex_sock in group_node.inputs and not group_node.inputs[tex_sock].is_linked:
            links.new(mix.outputs[2], group_node.inputs[tex_sock])
        if not any(lnk.to_node == tex_nd and lnk.to_socket.name == "Vector" for lnk in mat.node_tree.links):
            links.new(mapping_node.outputs["Vector"], tex_nd.inputs["Vector"])
    
    # MI parameters
    if mi_data is not None:
        _mi_params = mi_data.get("mi_params") or {"scalars": [], "vectors": []}
    elif json_path:
        _mi_params = textures.parse_all_mi_parameters(json_path, known_colour_names=set(colours.keys()))
    else:
        _mi_params = {"scalars": [], "vectors": []}
    if _mi_params['scalars'] or _mi_params['vectors']:
        place_mi_parameter_nodes(nodes, links, _mi_params)
    
    # Decals
    if mi_data is not None:
        decals = mi_data.get("decals") or []
    else:
        decals = textures.parse_decals(json_path) if json_path else []
    if decals:
        # FModel outfit exports may place DecalColor/DecalData PNGs beside the PSK or skin JSON.
        # Shared MaterialLibrary Decals still take priority via decal_folder / ObjectPath.
        search_dirs = []
        if manual_skins_folder:
            search_dirs.append(manual_skins_folder)
        if json_path:
            search_dirs.append(os.path.dirname(json_path))
        if folder:
            search_dirs.append(folder)
        if psk_path:
            search_dirs.append(os.path.dirname(psk_path))
        setup_decals(
            nodes, links, group_node, decals, decal_folder,
            search_dirs=search_dirs,
        )

    # Final pass: guarantee padding between every non-frame node on import.
    apply_node_graph_padding(nodes)

def set_enable_slider(group_node, zone: str, value: float = 1.0):
    sock = group_node.inputs.get(f"Enable {zone}")
    if sock is not None:
        try:
            sock.default_value = value
        except Exception:
            pass

def place_mi_parameter_nodes(nodes, links, mi_params: dict, base_x: float = -1900, base_y: float = -2800):
    row = 0
    col_w = 300
    row_h = -180
    per_col = 8
    
    for i, (name, value) in enumerate(mi_params.get('scalars', [])):
        node = nodes.new("ShaderNodeValue")
        node.label = name
        node.outputs[0].default_value = value
        col = i // per_col
        r = i % per_col
        node.location = (base_x + col * col_w, base_y + r * row_h)
    
    vec_col_x = base_x + (((len(mi_params.get('scalars', [])) - 1) // per_col) + 1) * col_w + 140
    for i, (name, rgba) in enumerate(mi_params.get('vectors', [])):
        node = nodes.new("ShaderNodeRGB")
        node.label = name
        node.outputs[0].default_value = rgba
        col = i // per_col
        r = i % per_col
        node.location = (vec_col_x + col * col_w, base_y + r * row_h)

# Fabric/tiling patterns reused as "decals" (Goalie DiamondQuilt, etc.). Stickers stay CLIP.
_DECAL_REPEAT_STEM_RE = re.compile(
    r"(?i)(DiamondQuilt|Quilt|Checkers|Weave|Plaid|Knit|(?<![A-Za-z])Tile(?![A-Za-z]))"
)


def decal_image_extension(decal: dict) -> str:
    """REPEAT for tiling fabric patterns; CLIP for logo/patch stickers.

    Cooked MIs always sample decals with SSM_Wrap, but sticker placement still
    needs CLIP so one stamp doesn't tile. Quilt-style stems (and Null color +
    quilt normal data) are meant to tile across the mesh like a material.
    """
    stems = f"{decal.get('texture', '')}|{decal.get('data_texture', '')}"
    if _DECAL_REPEAT_STEM_RE.search(stems):
        return "REPEAT"
    return "CLIP"


# FModel ARC_BANDS / ArcActiveZone (9 zones). bits 0..8 all set = 511.
_ARC_DECAL_BANDS = (0.0, 0.03, 0.10, 0.18, 0.30, 0.45, 0.60, 0.75, 0.88, 1.01)
_ARC_DECAL_ZONE_COUNT = 9
_ARC_DECAL_ALL_ZONES_MASK = (1 << _ARC_DECAL_ZONE_COUNT) - 1


def _find_ocm_image_node(nodes, group_node):
    main = group_node.inputs.get("Main Texture") if group_node is not None else None
    if main is not None and getattr(main, "is_linked", False) and main.links:
        src = main.links[0].from_node
        if src is not None and src.type == "TEX_IMAGE":
            return src
    for node in nodes:
        if node.type != "TEX_IMAGE" or node.image is None:
            continue
        name = (node.image.name or "").lower()
        label = (node.label or "").lower()
        if "occlusioncurvaturematerialid" in name or "occlusioncurvaturematerialid" in label:
            return node
    return None


def _decal_layer_mask_int(raw) -> int:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 255
    if value <= 0.0:
        return 255
    return int(value)


def _layer_mask_allows_all_zones(mask_int: int) -> bool:
    return (mask_int & _ARC_DECAL_ALL_ZONES_MASK) == _ARC_DECAL_ALL_ZONES_MASK


def _build_layer_mask_ramp(ramp, mask_int: int):
    ramp.interpolation = "CONSTANT"
    while len(ramp.elements) > 1:
        ramp.elements.remove(ramp.elements[-1])
    allowed0 = 1.0 if (mask_int & 1) != 0 else 0.0
    ramp.elements[0].position = _ARC_DECAL_BANDS[0]
    ramp.elements[0].color = (allowed0, allowed0, allowed0, allowed0)
    for zone in range(1, _ARC_DECAL_ZONE_COUNT):
        allowed = 1.0 if (mask_int & (1 << zone)) != 0 else 0.0
        el = ramp.elements.new(_ARC_DECAL_BANDS[zone])
        el.color = (allowed, allowed, allowed, allowed)


DECAL_PLACEMENT_DEFAULT = "FMODEL"

# Blender-side only. FModel has a single validated decal transform; these extra modes
# exist to test accessories that may be authored against a different convention. Once a
# mode is confirmed for a part, port it back into default.frag so both stay in sync.
#
# LegBox duct tape: position/rotation are close under FMODEL but the strip reads too
# narrow — try WIDTH_2X (doubles the post-rotation U footprint / sx, keeps centre/rotation).
#
# WIDTH_* boosts mesh-visible strip thickness by dividing sx (local U after rotate), NOT
# WidthRatio/sy. Dividing WidthRatio expands UV V — that matches the authored UV aspect
# axis, but for diagonal strips it stretches the wrong visual direction on the mesh.
DECAL_PLACEMENT_METHODS = (
    ("FMODEL", "FModel (validated)",
     "Scale applied after rotation about the decal centre. Matches default.frag "
     "ArcDecalUv and is correct for upper and lower body clothing"),
    ("WIDTH_2X", "FModel + 2× width",
     "Same as FModel but halves sx (post-rotation U scale) so the strip footprint is "
     "twice as wide on the mesh. Try for Goalie LegBox duct tape when rot/pos look right"),
    ("WIDTH_3X", "FModel + 3× width",
     "Same as FModel but sx / 3 — wider accessory stickers on the mesh"),
    ("SCALE_FIRST", "Scale before rotate",
     "Anisotropic scale applied before the rotation — the earlier near-miss placement. "
     "Try this on accessories that look sheared or stretched along one axis"),
    ("UNROTATED_CENTRE", "Unrotated centre",
     "Rotates the UVs but offsets by the raw decal centre instead of the rotated "
     "centre. Shows up as a decal that drifts as rotation increases"),
    ("FLIP_V", "FModel + V flip",
     "FModel placement with one extra V mirror, for parts whose UVs were not flipped "
     "by the mesh importer"),
    ("INVERTED_ROTATION", "FModel + inverted rotation",
     "FModel placement with the rotation direction reversed"),
)

# sx divisors for width-boost modes (larger → wider strip footprint along post-rotation U).
_DECAL_WIDTH_BOOST = {
    "WIDTH_2X": 2.0,
    "WIDTH_3X": 3.0,
}

_DECAL_METHOD_IDS = frozenset(entry[0] for entry in DECAL_PLACEMENT_METHODS)


def active_decal_method(scene=None) -> str:
    """Scene-selected decal placement mode, falling back to the FModel-matched math."""
    if scene is None:
        scene = getattr(bpy.context, "scene", None)
    method = getattr(scene, "arc_decal_method", DECAL_PLACEMENT_DEFAULT) if scene else DECAL_PLACEMENT_DEFAULT
    return method if method in _DECAL_METHOD_IDS else DECAL_PLACEMENT_DEFAULT


def _decal_mapping_params(uv_u, uv_v, scale, rotation, width_ratio,
                          method=DECAL_PLACEMENT_DEFAULT):
    """Decal placement expressed as two chained Mapping nodes.

    A Blender Mapping node evaluates ``p' = R * (S * p) + L``, so scale-before-rotate
    fits in one node while FModel's scale-after-rotate needs the rotation and the
    scale/offset split across two.

    The FMODEL mode ports default.frag ArcDecalUv. The shader works in UE UV space and
    does two V mirrors: it feeds ``(u, 1 - v)`` into the placement and mirrors the
    sampled ``v`` again on the way out. Blender's PSK importer already stores
    ``v_blender = 1 - v_ue`` and Blender images are bottom-origin, so both mirrors are
    exactly the UE<->Blender conversion and cancel — no flip nodes are emitted.
    """
    import math as _math
    wr = float(width_ratio) if width_ratio else 1.0
    boost = _DECAL_WIDTH_BOOST.get(method, 1.0)
    if scale > 0.0001:
        scale_x = 1.0 / scale
        scale_y = wr / scale
    else:
        scale_x = scale_y = 1.0
    # WIDTH_*: expand footprint along post-rotation U (sx), leave WidthRatio/sy alone.
    # Smaller mapping scale → larger mesh footprint; boost>1 halves/thirds sx.
    if boost > 1.0:
        scale_x = scale_x / boost

    rotation_rad = _math.radians(rotation * -360.0)
    if method == "INVERTED_ROTATION":
        rotation_rad = -rotation_rad
    centre_u = 0.5 - uv_u
    centre_v = 0.5 + uv_v
    cos_t = _math.cos(rotation_rad)
    sin_t = _math.sin(rotation_rad)

    # Width-boost modes share FMODEL centre math; only sx changes.
    place_method = "FMODEL" if method in _DECAL_WIDTH_BOOST else method

    if place_method == "SCALE_FIRST":
        # p' = R * (S * (p - c)) + 0.5, so the offset uses the scaled-then-rotated centre.
        sc_u = scale_x * centre_u
        sc_v = scale_y * centre_v
        loc_x = 0.5 - (cos_t * sc_u - sin_t * sc_v)
        loc_y = 0.5 - (sin_t * sc_u + cos_t * sc_v)
        return {
            "scale_x": scale_x,
            "scale_y": scale_y,
            "rotation_rad": rotation_rad,
            "rot_scale": (scale_x, scale_y, 1.0),
            "rot_rotation": rotation_rad,
            "rot_location": (loc_x, loc_y, 0.0),
            "place_scale": (1.0, 1.0, 1.0),
            "place_rotation": 0.0,
            "place_location": (0.0, 0.0, 0.0),
            "flip_v": False,
            "loc_x": loc_x,
            "loc_y": loc_y,
            "width_boost": boost,
        }

    if place_method == "UNROTATED_CENTRE":
        loc_x = 0.5 - scale_x * centre_u
        loc_y = 0.5 - scale_y * centre_v
    else:
        # FMODEL, WIDTH_*, FLIP_V, INVERTED_ROTATION: offset uses the rotated centre.
        rcx = cos_t * centre_u - sin_t * centre_v
        rcy = sin_t * centre_u + cos_t * centre_v
        loc_x = 0.5 - scale_x * rcx
        loc_y = 0.5 - scale_y * rcy

    return {
        "scale_x": scale_x,
        "scale_y": scale_y,
        "rotation_rad": rotation_rad,
        "rot_scale": (1.0, 1.0, 1.0),
        "rot_rotation": rotation_rad,
        "rot_location": (0.0, 0.0, 0.0),
        "place_scale": (scale_x, scale_y, 1.0),
        "place_rotation": 0.0,
        "place_location": (loc_x, loc_y, 0.0),
        "flip_v": method == "FLIP_V",
        "loc_x": loc_x,
        "loc_y": loc_y,
        "width_boost": boost,
    }


def setup_decals(nodes, links, group_node, decals: list, decal_folder: str,
                 search_dirs: list = None):
    decal_uv_node = None
    _decal_tex_cache = {}
    _decal_data_cache = {}
    ocm_node = _find_ocm_image_node(nodes, group_node)
    ocm_sep_node = None
    ocm_warned = False
    decal_method = active_decal_method()
    search_dirs = search_dirs or []
    layer_stack = []  # (colour_out, alpha_out, rough_out, metal_out, idx) for Mix Shader layering
    decal_frame = nodes.new("NodeFrame")
    decal_frame.label = f"Decals [{decal_method}]"
    decal_frame.label_size = 20

    for d_idx, decal in enumerate(decals):
        idx = decal["index"]
        col_x = 1200 + d_idx * 520
        row_y = 900
        extension = decal_image_extension(decal)

        if decal_uv_node is None:
            decal_uv_node = nodes.new("ShaderNodeTexCoord")
            decal_uv_node.label = "Decal UV"
            decal_uv_node.location = (col_x - 250, row_y + 200)
            decal_uv_node.parent = decal_frame

        import math as _math

        uv_u = decal["uv_u"]
        uv_v = decal["uv_v"]
        scale = decal["scale"]
        rotation = decal["rotation"]
        width_ratio = decal.get("width_ratio")
        width_ratio_derived = False
        try:
            width_ratio = float(width_ratio)
        except (TypeError, ValueError):
            width_ratio = 0.0
        if width_ratio <= 0.0:
            # WidthRatio is normally authored on every slot. For malformed or
            # older MIs, derive the same aspect quantity from the actual image.
            probe_path = resolve_decal_texture(
                decal.get("texture", ""), decal.get("texture_path", ""),
                decal_folder, search_dirs=search_dirs,
            )
            if probe_path:
                probe_image = bpy.data.images.load(probe_path, check_existing=True)
                if probe_image.size[1] > 0:
                    width_ratio = float(probe_image.size[0]) / float(probe_image.size[1])
                    width_ratio_derived = True
        if width_ratio <= 0.0:
            width_ratio = 1.0
        layer_mask_int = _decal_layer_mask_int(decal.get("layer_mask", 255.0))
        color_override = max(0.0, min(1.0, float(decal.get("color_override", 0.0))))
        params = _decal_mapping_params(uv_u, uv_v, scale, rotation, width_ratio,
                                       method=decal_method)
        scale_x = params["scale_x"]
        scale_y = params["scale_y"]
        rotation_rad = params["rotation_rad"]

        # Rotation about the UV origin, matching the shader's rotMesh term.
        mapping_node = nodes.new("ShaderNodeMapping")
        mapping_node.vector_type = "POINT"
        boost = params.get("width_boost", 1.0)
        boost_txt = f"  W×{boost:g}" if boost and boost != 1.0 else ""
        mapping_node.label = (f"Decal {idx}  R={uv_u:.4f} G={uv_v:.4f}  "
                              f"SX={scale_x:.3f} SY={scale_y:.3f}  "
                              f"Rot={_math.degrees(rotation_rad):.1f}°  {extension}"
                              f"  [{decal_method}]{boost_txt}")
        mapping_node.location = (col_x - 450, row_y)
        mapping_node.parent = decal_frame
        mapping_node["arc_decal_method"] = decal_method

        mapping_node["arc_decal_R"] = uv_u
        mapping_node["arc_decal_G"] = uv_v
        mapping_node["arc_decal_B"] = scale
        mapping_node["arc_decal_A"] = rotation
        mapping_node["arc_decal_WR"] = width_ratio
        mapping_node["arc_decal_WR_derived"] = width_ratio_derived
        mapping_node["arc_decal_width_boost"] = float(boost or 1.0)
        mapping_node["arc_decal_color_override"] = color_override
        mapping_node["arc_decal_original_color"] = color_override < 0.9999
        mapping_node["arc_decal_layer_mask"] = layer_mask_int
        mapping_node["arc_decal_idx"] = idx
        mapping_node["arc_decal_extension"] = extension

        mapping_node.inputs["Location"].default_value = params["rot_location"]
        mapping_node.inputs["Rotation"].default_value = (0.0, 0.0, params["rot_rotation"])
        mapping_node.inputs["Scale"].default_value = params["rot_scale"]

        links.new(decal_uv_node.outputs["UV"], mapping_node.inputs["Vector"])

        # Anisotropic scale applied *after* the rotation, then the recentred offset.
        place_node = nodes.new("ShaderNodeMapping")
        place_node.vector_type = "POINT"
        place_node.label = f"Decal {idx} Scale/Loc"
        place_node.location = (col_x - 250, row_y)
        place_node.parent = decal_frame
        place_node.inputs["Location"].default_value = params["place_location"]
        place_node.inputs["Rotation"].default_value = (0.0, 0.0, params["place_rotation"])
        place_node.inputs["Scale"].default_value = params["place_scale"]
        links.new(mapping_node.outputs["Vector"], place_node.inputs["Vector"])
        placed_uv = place_node.outputs["Vector"]

        if params["flip_v"]:
            flip_node = nodes.new("ShaderNodeMapping")
            flip_node.vector_type = "POINT"
            flip_node.label = f"Decal {idx} V Flip"
            flip_node.location = (col_x - 100, row_y)
            flip_node.parent = decal_frame
            flip_node.inputs["Location"].default_value = (0.0, 1.0, 0.0)
            flip_node.inputs["Rotation"].default_value = (0.0, 0.0, 0.0)
            flip_node.inputs["Scale"].default_value = (1.0, -1.0, 1.0)
            links.new(placed_uv, flip_node.inputs["Vector"])
            placed_uv = flip_node.outputs["Vector"]

        row_y -= 250

        color_tex_node = None
        data_tex_node = None
        ramp_node = None
        rgb_node = None
        color_mix_node = None
        layer_mask_mul = None

        tex_stem = decal["texture"]
        if tex_stem not in _decal_tex_cache:
            tex_fpath = resolve_decal_texture(
                tex_stem, decal.get("texture_path", ""), decal_folder, search_dirs=search_dirs
            )
            if tex_fpath:
                img = bpy.data.images.load(tex_fpath, check_existing=True)
                tex_node = nodes.new("ShaderNodeTexImage")
                tex_node.image = img
                tex_node.label = f"Decal {idx}: {tex_stem}"
                tex_node.interpolation = "Cubic"
                tex_node.extension = extension
                tex_node.location = (col_x, row_y)
                tex_node.parent = decal_frame
                links.new(placed_uv, tex_node.inputs["Vector"])
                _decal_tex_cache[tex_stem] = tex_node
                color_tex_node = tex_node
                row_y -= 300
            else:
                _decal_tex_cache[tex_stem] = None
        else:
            prev_node = _decal_tex_cache[tex_stem]
            if prev_node is not None:
                dup_node = nodes.new("ShaderNodeTexImage")
                dup_node.image = prev_node.image
                dup_node.label = f"Decal {idx}: {tex_stem}"
                dup_node.interpolation = "Cubic"
                dup_node.extension = extension
                dup_node.location = (col_x, row_y)
                dup_node.parent = decal_frame
                links.new(placed_uv, dup_node.inputs["Vector"])
                color_tex_node = dup_node
                row_y -= 300

        data_stem = decal.get("data_texture", "")
        if data_stem:
            if data_stem not in _decal_data_cache:
                data_fpath = resolve_decal_texture(
                    data_stem, decal.get("data_texture_path", ""), decal_folder, search_dirs=search_dirs
                )
                if data_fpath:
                    data_img = bpy.data.images.load(data_fpath, check_existing=True)
                    data_img.colorspace_settings.name = "Non-Color"
                    data_node = nodes.new("ShaderNodeTexImage")
                    data_node.image = data_img
                    data_node.label = f"Decal {idx} Data: {data_stem}"
                    data_node.interpolation = "Cubic"
                    data_node.extension = extension
                    data_node.location = (col_x, row_y)
                    data_node.parent = decal_frame
                    links.new(placed_uv, data_node.inputs["Vector"])
                    _decal_data_cache[data_stem] = data_node
                    data_tex_node = data_node
                    row_y -= 300
                else:
                    _decal_data_cache[data_stem] = None
            else:
                prev_data = _decal_data_cache[data_stem]
                if prev_data is not None:
                    dup_data = nodes.new("ShaderNodeTexImage")
                    dup_data.image = prev_data.image
                    dup_data.label = f"Decal {idx} Data: {data_stem}"
                    dup_data.interpolation = "Cubic"
                    dup_data.extension = extension
                    dup_data.location = (col_x, row_y)
                    dup_data.parent = decal_frame
                    links.new(placed_uv, dup_data.inputs["Vector"])
                    data_tex_node = dup_data
                    row_y -= 300

        color_a = decal.get("color_a")
        color_b = decal.get("color_b")

        if color_a and color_b:
            ramp_node = nodes.new("ShaderNodeValToRGB")
            ramp_node.label = f"Decal {idx} Colours"
            ramp_node.location = (col_x, row_y)
            ramp_node.parent = decal_frame
            ramp = ramp_node.color_ramp
            ramp.interpolation = "CONSTANT"
            ramp.elements[0].position = 0.0
            ramp.elements[0].color = (color_b[0], color_b[1], color_b[2], color_b[3])
            if len(ramp.elements) < 2:
                ramp.elements.new(0.075)
            else:
                ramp.elements[1].position = 0.075
            ramp.elements[1].color = (color_a[0], color_a[1], color_a[2], color_a[3])
            row_y -= 280
        elif color_a:
            rgb_node = nodes.new("ShaderNodeRGB")
            rgb_node.label = f"Decal {idx} Color"
            rgb_node.outputs[0].default_value = (color_a[0], color_a[1], color_a[2], color_a[3])
            rgb_node.location = (col_x, row_y)
            rgb_node.parent = decal_frame
            row_y -= 200

        # M_Character_Layered's {slot}_ColorOverride is a continuous lerp from
        # sampled RGB (0) to the ColorA/B mask result (1). Keep original artwork
        # for partial values such as Goalie Gold's chest patch (0.22557278).
        if color_tex_node is not None and color_override < 0.9999 and (ramp_node or rgb_node):
            color_mix_node = nodes.new("ShaderNodeMixRGB")
            color_mix_node.blend_type = "MIX"
            color_mix_node.label = f"Decal {idx} Original ↔ Override ({color_override:.3f})"
            color_mix_node.location = (col_x + 280, row_y)
            color_mix_node.parent = decal_frame
            color_mix_node.inputs["Fac"].default_value = color_override
            color_mix_node["arc_decal_original_color"] = True
            color_mix_node["arc_decal_color_override"] = color_override
            row_y -= 220

        if color_tex_node is not None and not _layer_mask_allows_all_zones(layer_mask_int):
            if ocm_node is None:
                if not ocm_warned:
                    print("Arc Raiders PSK Importer: No OCM texture for decal LayerMask; "
                          "decals will not be zone-masked.")
                    ocm_warned = True
            else:
                if ocm_sep_node is None:
                    ocm_sep_node = nodes.new("ShaderNodeSeparateColor")
                    ocm_sep_node.label = "OCM → Material ID"
                    ocm_sep_node.location = (950, 1100)
                    ocm_sep_node.parent = decal_frame
                    links.new(ocm_node.outputs["Color"], ocm_sep_node.inputs["Color"])

                mask_ramp = nodes.new("ShaderNodeValToRGB")
                mask_ramp.label = f"Decal {idx} LayerMask ({layer_mask_int})"
                mask_ramp.location = (col_x + 280, row_y)
                mask_ramp.parent = decal_frame
                mask_ramp["arc_decal_layer_mask"] = layer_mask_int
                _build_layer_mask_ramp(mask_ramp.color_ramp, layer_mask_int)
                links.new(ocm_sep_node.outputs["Blue"], mask_ramp.inputs["Fac"])

                layer_mask_mul = nodes.new("ShaderNodeMath")
                layer_mask_mul.operation = "MULTIPLY"
                layer_mask_mul.label = f"Decal {idx} LayerMask × Alpha"
                layer_mask_mul.location = (col_x + 560, row_y)
                layer_mask_mul.parent = decal_frame
                links.new(color_tex_node.outputs["Alpha"], layer_mask_mul.inputs[0])
                links.new(mask_ramp.outputs["Alpha"], layer_mask_mul.inputs[1])

        n = idx
        colour_out = None
        alpha_out = None
        if color_tex_node is not None:
            if f"Decal {n}" in group_node.inputs:
                if ramp_node is not None:
                    links.new(color_tex_node.outputs["Color"], ramp_node.inputs["Fac"])
                    override_out = ramp_node.outputs["Color"]
                elif rgb_node is not None:
                    override_out = rgb_node.outputs[0]
                else:
                    override_out = color_tex_node.outputs["Color"]
                if color_mix_node is not None:
                    links.new(color_tex_node.outputs["Color"], color_mix_node.inputs["Color1"])
                    links.new(override_out, color_mix_node.inputs["Color2"])
                    colour_out = color_mix_node.outputs["Color"]
                else:
                    colour_out = override_out
                links.new(colour_out, group_node.inputs[f"Decal {n}"])
                if f"Decal Alpha {n}" in group_node.inputs:
                    alpha_out = (layer_mask_mul.outputs[0] if layer_mask_mul is not None
                                 else color_tex_node.outputs["Alpha"])
                    links.new(alpha_out, group_node.inputs[f"Decal Alpha {n}"])
                if f"Decal Enable {n}" in group_node.inputs:
                    try:
                        group_node.inputs[f"Decal Enable {n}"].default_value = 1.0
                    except Exception:
                        pass
            else:
                if ramp_node is not None:
                    links.new(color_tex_node.outputs["Color"], ramp_node.inputs["Fac"])
                    colour_out = ramp_node.outputs["Color"]
                elif rgb_node is not None:
                    colour_out = rgb_node.outputs[0]
                else:
                    colour_out = color_tex_node.outputs["Color"]
                alpha_out = (layer_mask_mul.outputs[0] if layer_mask_mul is not None
                             else color_tex_node.outputs["Alpha"])

        rough_out = None
        metal_out = None
        if data_tex_node is not None:
            # DecalData packing (T_DuctTape_01_M evidence): RG = tangent normal,
            # B = roughness, A = metallic/specular. Rebuild Z for DN; drive PBR layer.
            sep = nodes.new("ShaderNodeSeparateColor")
            sep.label = f"Decal {idx} Data RGB"
            sep.location = (col_x + 280, row_y)
            sep.parent = decal_frame
            links.new(data_tex_node.outputs["Color"], sep.inputs["Color"])

            # Reconstruct Z from RG and pack back to 0-1 for ArcTexturer DN.
            r_s = nodes.new("ShaderNodeMath")
            r_s.operation = "MULTIPLY_ADD"
            r_s.label = f"Decal {idx} Nx"
            r_s.location = (col_x + 480, row_y + 80)
            r_s.parent = decal_frame
            r_s.inputs[1].default_value = 2.0
            r_s.inputs[2].default_value = -1.0
            links.new(sep.outputs["Red"], r_s.inputs[0])

            g_s = nodes.new("ShaderNodeMath")
            g_s.operation = "MULTIPLY_ADD"
            g_s.label = f"Decal {idx} Ny"
            g_s.location = (col_x + 480, row_y - 40)
            g_s.parent = decal_frame
            g_s.inputs[1].default_value = 2.0
            g_s.inputs[2].default_value = -1.0
            links.new(sep.outputs["Green"], g_s.inputs[0])

            nx2 = nodes.new("ShaderNodeMath")
            nx2.operation = "MULTIPLY"
            nx2.location = (col_x + 680, row_y + 80)
            nx2.parent = decal_frame
            links.new(r_s.outputs[0], nx2.inputs[0])
            links.new(r_s.outputs[0], nx2.inputs[1])

            ny2 = nodes.new("ShaderNodeMath")
            ny2.operation = "MULTIPLY"
            ny2.location = (col_x + 680, row_y - 40)
            ny2.parent = decal_frame
            links.new(g_s.outputs[0], ny2.inputs[0])
            links.new(g_s.outputs[0], ny2.inputs[1])

            nsum = nodes.new("ShaderNodeMath")
            nsum.operation = "ADD"
            nsum.location = (col_x + 860, row_y + 20)
            nsum.parent = decal_frame
            links.new(nx2.outputs[0], nsum.inputs[0])
            links.new(ny2.outputs[0], nsum.inputs[1])

            one_m = nodes.new("ShaderNodeMath")
            one_m.operation = "SUBTRACT"
            one_m.location = (col_x + 1040, row_y + 20)
            one_m.parent = decal_frame
            one_m.inputs[0].default_value = 1.0
            links.new(nsum.outputs[0], one_m.inputs[1])

            nz = nodes.new("ShaderNodeMath")
            nz.operation = "SQRT"
            nz.label = f"Decal {idx} Nz"
            nz.location = (col_x + 1220, row_y + 20)
            nz.parent = decal_frame
            links.new(one_m.outputs[0], nz.inputs[0])

            nz01 = nodes.new("ShaderNodeMath")
            nz01.operation = "MULTIPLY_ADD"
            nz01.location = (col_x + 1400, row_y + 20)
            nz01.parent = decal_frame
            nz01.inputs[1].default_value = 0.5
            nz01.inputs[2].default_value = 0.5
            links.new(nz.outputs[0], nz01.inputs[0])

            n_combine = nodes.new("ShaderNodeCombineColor")
            n_combine.label = f"Decal {idx} Normal RG+Z"
            n_combine.location = (col_x + 1580, row_y + 40)
            n_combine.parent = decal_frame
            links.new(sep.outputs["Red"], n_combine.inputs["Red"])
            links.new(sep.outputs["Green"], n_combine.inputs["Green"])
            links.new(nz01.outputs[0], n_combine.inputs["Blue"])

            if f"DN {n}" in group_node.inputs:
                links.new(n_combine.outputs["Color"], group_node.inputs[f"DN {n}"])
                if f"DN Enable {n}" in group_node.inputs:
                    try:
                        group_node.inputs[f"DN Enable {n}"].default_value = 1.0
                    except Exception:
                        pass

            rough_out = sep.outputs["Blue"]
            # Alpha channel of DecalData drives metallic (duct tape ~0.59).
            metal_out = data_tex_node.outputs["Alpha"]

        if colour_out is not None and alpha_out is not None and (
                rough_out is not None or metal_out is not None):
            layer_stack.append({
                "idx": n,
                "color": colour_out,
                "alpha": alpha_out,
                "rough": rough_out,
                "metal": metal_out,
                "col_x": col_x,
            })

    if layer_stack:
        _wire_decal_layered_pbr(nodes, links, group_node, layer_stack, decal_frame)


def _wire_decal_layered_pbr(nodes, links, group_node, layer_stack, parent_frame=None):
    """Mix a Principled decal layer on top of ArcTexturer using DecalData B/A.

    ArcTexturer already tints albedo; this pass makes the sticker read as a
    distinct surface (roughness + metallic) instead of a painted-on colour.
    """
    output = None
    for node in nodes:
        if node.type == "OUTPUT_MATERIAL":
            output = node
            break
    if output is None or not output.inputs["Surface"].is_linked:
        return

    surface_link = output.inputs["Surface"].links[0]
    shader_sock = surface_link.from_socket
    links.remove(surface_link)

    mix_y = 200
    for i, layer in enumerate(layer_stack):
        idx = layer["idx"]
        col_x = 700 + i * 280

        decal_bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        decal_bsdf.label = f"Decal {idx} Layer PBR"
        decal_bsdf.location = (col_x, mix_y - 280)
        if parent_frame is not None:
            decal_bsdf.parent = parent_frame
        links.new(layer["color"], decal_bsdf.inputs["Base Color"])
        if layer["rough"] is not None:
            links.new(layer["rough"], decal_bsdf.inputs["Roughness"])
        else:
            decal_bsdf.inputs["Roughness"].default_value = 0.45
        if layer["metal"] is not None and "Metallic" in decal_bsdf.inputs:
            links.new(layer["metal"], decal_bsdf.inputs["Metallic"])
        # Slightly higher specular so duct-tape edges catch light.
        for sock_name, value in (("Specular IOR Level", 0.55), ("Specular", 0.55)):
            if sock_name in decal_bsdf.inputs and not decal_bsdf.inputs[sock_name].is_linked:
                try:
                    decal_bsdf.inputs[sock_name].default_value = value
                except Exception:
                    pass

        mix = nodes.new("ShaderNodeMixShader")
        mix.label = f"Decal {idx} On Top"
        mix.location = (col_x + 220, mix_y)
        if parent_frame is not None:
            mix.parent = parent_frame
        links.new(layer["alpha"], mix.inputs["Fac"])
        links.new(shader_sock, mix.inputs[1])
        links.new(decal_bsdf.outputs["BSDF"], mix.inputs[2])
        shader_sock = mix.outputs["Shader"]
        mix_y -= 40

    links.new(shader_sock, output.inputs["Surface"])
    if output.location[0] < 900:
        output.location = (900, 0)


def resolve_decal_texture(
    stem: str,
    object_path: str,
    decal_folder: str,
    search_dirs=None,
) -> str:
    if object_path:
        fpath = textures.find_texture_from_object_path(object_path)
        if fpath:
            return fpath
    if not stem:
        return ""
    stem_lower = stem.lower()
    dirs = []
    if decal_folder:
        dirs.append(decal_folder)
    if search_dirs:
        dirs.extend(d for d in search_dirs if d)
    seen = set()
    for folder in dirs:
        norm = os.path.normcase(os.path.abspath(folder)) if folder else ""
        if not folder or norm in seen or not os.path.isdir(folder):
            continue
        seen.add(norm)
        try:
            for fname in os.listdir(folder):
                if os.path.splitext(fname)[0].lower() == stem_lower and fname.lower().endswith(".png"):
                    return os.path.join(folder, fname)
        except OSError:
            pass
    return ""

# ---------------------------------------------------------------------------
# Other material setups (body, hair, weapon, misc, face)
# ---------------------------------------------------------------------------

def setup_body_material(obj, psk_path: str, body_variant: str):
    if body_variant == 'NONE':
        return
    
    tex_folder = os.path.join(os.path.dirname(psk_path), "Textures")
    albedo_name = BODY_ALBEDO.get(body_variant)
    normal_name = BODY_NORMAL.get(body_variant)
    
    albedo_path = os.path.join(tex_folder, albedo_name) if albedo_name else None
    normal_path = os.path.join(tex_folder, normal_name) if normal_name else None
    micro_path = os.path.join(tex_folder, "T_SkinMicroNormal_01.png")
    
    if albedo_path and not os.path.isfile(albedo_path):
        print(f"Arc Raiders PSK Importer: Body albedo not found: '{albedo_path}'")
        albedo_path = None
    if normal_path and not os.path.isfile(normal_path):
        print(f"Arc Raiders PSK Importer: Body normal not found: '{normal_path}'")
        normal_path = None
    if not os.path.isfile(micro_path):
        print(f"Arc Raiders PSK Importer: MicroNormal not found: '{micro_path}'")
        micro_path = None
    
    mat = bpy.data.materials.new(name=obj.name + "_Body_Mat")
    mat.use_nodes = True
    obj.active_material = mat
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    
    # Fix shape key seam splitting
    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = obj
    if obj.data.shape_keys:
        for key_block in obj.data.shape_keys.key_blocks:
            if key_block.name != 'Basis':
                key_block.value = 0.0
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.remove_doubles(threshold=0.001)
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.objects.active = prev_active
    
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (300, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (600, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])
    
    if albedo_path:
        img_albedo = bpy.data.images.load(albedo_path, check_existing=True)
        albedo_node = nodes.new("ShaderNodeTexImage")
        albedo_node.image = img_albedo
        albedo_node.label = albedo_name
        albedo_node.interpolation = "Cubic"
        albedo_node.location = (-600, 300)
        links.new(albedo_node.outputs["Color"], principled.inputs["Base Color"])
        
        ramp_node = nodes.new("ShaderNodeValToRGB")
        ramp_node.label = "Body Alpha Ramp"
        ramp_node.location = (-200, 0)
        ramp = ramp_node.color_ramp
        ramp.interpolation = "LINEAR"
        ramp.elements[0].position = 0.0
        ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
        ramp.elements[1].position = 0.086
        ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
        links.new(albedo_node.outputs["Color"], ramp_node.inputs["Fac"])
        links.new(ramp_node.outputs["Color"], principled.inputs["Alpha"])
    
    if normal_path or micro_path:
        normal_flipper = None
        if utils.ensure_node_group("NormalFlipper"):
            normal_flipper = nodes.new("ShaderNodeGroup")
            normal_flipper.node_tree = bpy.data.node_groups["NormalFlipper"]
            normal_flipper.location = (-800, -200)
        
        if normal_path and normal_flipper:
            img_normal = bpy.data.images.load(normal_path, check_existing=True)
            img_normal.colorspace_settings.name = "Non-Color"
            normal_tex = nodes.new("ShaderNodeTexImage")
            normal_tex.image = img_normal
            normal_tex.label = normal_name
            normal_tex.interpolation = "Cubic"
            normal_tex.location = (-1100, -200)
            links.new(normal_tex.outputs["Color"], normal_flipper.inputs[0])
        
        if micro_path:
            tex_coord = nodes.new("ShaderNodeTexCoord")
            tex_coord.location = (-1500, -500)
            mapping = nodes.new("ShaderNodeMapping")
            mapping.location = (-1300, -500)
            mapping.inputs["Scale"].default_value = (15.0, 15.0, 15.0)
            links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])
            
            img_micro = bpy.data.images.load(micro_path, check_existing=True)
            img_micro.colorspace_settings.name = "Non-Color"
            micro_node = nodes.new("ShaderNodeTexImage")
            micro_node.image = img_micro
            micro_node.label = "T_SkinMicroNormal_01"
            micro_node.interpolation = "Cubic"
            micro_node.location = (-1100, -500)
            links.new(mapping.outputs["Vector"], micro_node.inputs["Vector"])
        
        mix_node = nodes.new("ShaderNodeMix")
        mix_node.data_type = 'RGBA'
        mix_node.blend_type = 'OVERLAY'
        mix_node.location = (-500, -300)
        mix_node.inputs["Factor"].default_value = 1.0
        
        if normal_flipper:
            links.new(normal_flipper.outputs[0], mix_node.inputs[6])
        if micro_path:
            links.new(micro_node.outputs["Color"], mix_node.inputs[7])
        
        normal_map_node = nodes.new("ShaderNodeNormalMap")
        normal_map_node.location = (-100, -300)
        try:
            normal_map_node.convention = 'DIRECTX'
        except Exception:
            pass
        links.new(mix_node.outputs[2], normal_map_node.inputs["Color"])
        links.new(normal_map_node.outputs["Normal"], principled.inputs["Normal"])

# ---------------------------------------------------------------------------
# Face material setup
# ---------------------------------------------------------------------------

def _ensure_face_shader_node_group() -> bool:
    return utils.ensure_node_group("Face Shader")


def _parent_name_from_object(parent_obj) -> str:
    """Extract Material'/MI' name from an FModel Parent ObjectName dict/string."""
    if not parent_obj:
        return ""
    if isinstance(parent_obj, str):
        raw = parent_obj
    elif isinstance(parent_obj, dict):
        raw = parent_obj.get("ObjectName") or parent_obj.get("ObjectPath") or ""
    else:
        return ""
    # ObjectName shapes: Material'M_Vegetation_01' / MaterialInstanceConstant'MI_X'
    m = re.search(r"'([^']+)'", str(raw))
    if m:
        return m.group(1)
    return str(raw).rsplit("/", 1)[-1].split(".", 1)[0]


def _apply_bpo_flags(result: dict, bpo: dict):
    """Copy BlendMode / TwoSided / OpacityMaskClip / shading from BasePropertyOverrides."""
    if not bpo:
        return
    if result.get("blend_mode") is None and bpo.get("BlendMode") is not None:
        result["blend_mode"] = bpo.get("BlendMode")
    if bpo.get("OpacityMaskClipValue") is not None:
        try:
            result["opacity_clip"] = float(bpo["OpacityMaskClipValue"])
        except (TypeError, ValueError):
            pass
    if "TwoSided" in bpo:
        result["two_sided"] = bool(bpo.get("TwoSided"))
    if bpo.get("ShadingModel") is not None:
        result["shading_model"] = str(bpo.get("ShadingModel") or "")
    if "DitheredLODTransition" in bpo:
        result["dithered_lod"] = bool(bpo.get("DitheredLODTransition"))


def _parse_flat_mi_json(json_path: str) -> dict:
    result = {
        'textures': [], 'colours': [], 'switches': {}, 'scalars': {},
        'opacity_clip': 0.3333, 'blend_mode': None, 'is_null': False,
        'parent': '', 'two_sided': False, 'shading_model': '',
        'dithered_lod': False, 'is_translucent': False,
    }
    if not json_path or not os.path.isfile(json_path):
        return result
    cache_key = _norm_path_key(json_path)
    cached = _FLAT_MI_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        with open(json_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if isinstance(data, list):
            data = data[0]
        for param, path in data.get('Textures', {}).items():
            obj_path = path.rsplit('.', 1)[0] if '.' in path.split('/')[-1] else path
            result['textures'].append((param, obj_path))
        params = data.get('Parameters', {}) or {}
        for param, val in params.get('Colors', {}).items():
            result['colours'].append((param, (
                float(val.get('R', 1.0)),
                float(val.get('G', 1.0)),
                float(val.get('B', 1.0)),
                float(val.get('A', 1.0)),
            )))
        result['switches'] = params.get('Switches', {}) or {}
        result['scalars'] = params.get('Scalars', {}) or {}
        result['is_null'] = bool(params.get('IsNull', False))
        result['is_translucent'] = bool(params.get('IsTranslucent', False))
        props = params.get('Properties') or {}
        bpo = props.get('BasePropertyOverrides') or {}
        result['blend_mode'] = bpo.get('BlendMode') or params.get('BlendMode')
        _apply_bpo_flags(result, bpo)

        # Full FModel Exports dump — fill gaps + always capture Parent / BPO flags.
        entry = utils.first_ue_export(data, "MaterialInstanceConstant")
        if entry:
            eprops = entry.get("Properties") or {}
            if not result.get("parent"):
                result["parent"] = _parent_name_from_object(eprops.get("Parent"))
            bpo2 = eprops.get("BasePropertyOverrides") or {}
            _apply_bpo_flags(result, bpo2)
            need_tex = not result['textures']
            need_scalars = not result['scalars']
            need_switches = not result['switches']
            need_colours = not result['colours']
            if need_tex or need_scalars or need_switches or need_colours:
                if need_tex:
                    for t in eprops.get("TextureParameterValues") or []:
                        info = t.get("ParameterInfo") or {}
                        name = info.get("Name") or t.get("ParameterName") or ""
                        val = t.get("ParameterValue") or {}
                        obj_path = ""
                        if isinstance(val, dict):
                            obj_path = val.get("ObjectPath") or ""
                        elif isinstance(val, str):
                            obj_path = val
                        if name and obj_path:
                            clean = obj_path.rsplit(".", 1)[0] if "." in obj_path.split("/")[-1] else obj_path
                            result['textures'].append((name, clean))
                if need_scalars:
                    for s in eprops.get("ScalarParameterValues") or []:
                        info = s.get("ParameterInfo") or {}
                        name = info.get("Name") or s.get("ParameterName") or ""
                        if name and "ParameterValue" in s:
                            try:
                                result['scalars'][name] = float(s["ParameterValue"])
                            except (TypeError, ValueError):
                                pass
                if need_switches:
                    static = eprops.get("StaticSwitchParameterValues")
                    if static is None:
                        static = (eprops.get("StaticParameters") or {}).get("StaticSwitchParameters")
                    if not static:
                        # Full FModel dumps often store switches here (ArchitecturePreset_Trim).
                        static = (eprops.get("StaticParametersRuntime") or {}).get(
                            "StaticSwitchParameters"
                        ) or []
                    for s in static or []:
                        info = s.get("ParameterInfo") or {}
                        name = info.get("Name") or s.get("ParameterName") or ""
                        if name:
                            result['switches'][name] = bool(s.get("Value", s.get("ParameterValue", False)))
                if need_colours:
                    for c in eprops.get("VectorParameterValues") or []:
                        info = c.get("ParameterInfo") or {}
                        name = info.get("Name") or c.get("ParameterName") or ""
                        val = c.get("ParameterValue") or {}
                        if name and isinstance(val, dict):
                            result['colours'].append((name, (
                                float(val.get("R", 1.0)),
                                float(val.get("G", 1.0)),
                                float(val.get("B", 1.0)),
                                float(val.get("A", 1.0)),
                            )))
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to parse flat MI JSON '{json_path}': {e}")
        return result
    # Compact FModel dumps for child PropTrim MIs often omit inherited CR/NXX
    # (only Prop AO + Overlay overrides). Pull those from the library parent.
    stem = os.path.splitext(os.path.basename(json_path or ""))[0]
    _enrich_proptrim_inherited_textures(result, stem, json_path)
    _FLAT_MI_CACHE[cache_key] = result
    return result


# Soft-match folder MI inference (Vent/PropTrim/Rebar guesses by name similarity).
# Hard-disabled: Stage 2 / Fix White use SM JSON StaticMaterials only, plus
# water/decal preferred_mi when already restricted by _preferred_mi_is_single_slot_override.
ENABLE_FUZZY_MI_INFER = False

# Child PropTrim MIs often only override Prop AO + Overlay; CR/NXX live on the
# MaterialLibrary parent (MI_PropTrim_Painted_01_A) or a sibling that authored
# the full sheet. Class-based inherit (token / sibling / folder / library
# fallback) — no per-asset allowlists, no fuzzy soft-match.
_PROPTRIM_INHERIT_PARAM_KEYS = frozenset({
    "CR Texture", "NXX/NMX Texture", "NXX", "NOM", "NXM", "NMX", "HolesNXX",
})
_PROPTRIM_LIBRARY_BY_TOKEN = (
    (("paintedbeams", "painted_beams"), "MI_PropTrim_Painted_01_A"),
    (("paintedworn", "painted_worn"), "MI_PropTrim_PaintedWorn_01_A"),
    (("paintedclean", "painted_clean"), "MI_PropTrim_Painted_01_A"),
    (("paintedmetal", "painted_metal"), "MI_PropTrim_Painted_01_A"),
    (("woodpaint", "woodpainted", "wood_paint"), "MI_PropTrim_WoodPaint_01"),
    (("barealuminium", "barealuminum", "bare_aluminium", "bare_aluminum"), "MI_PropTrim_Metal_01_A"),
    (("rusted", "rust"), "MI_PropTrim_Rusted_01_A"),
    (("metal", "cleanmetal", "aluminium", "aluminum", "steel"), "MI_PropTrim_Metal_01_A"),
    (("wood",), "MI_PropTrim_Wood_01"),
    (("worn",), "MI_PropTrim_PaintedWorn_01_A"),
    (("painted",), "MI_PropTrim_Painted_01_A"),
)
_PROPTRIM_LIBRARY_FALLBACKS = (
    "MI_PropTrim_Painted_01_A",
    "MI_PropTrim_Metal_01_A",
    "MI_PropTrim_PaintedWorn_01_A",
    "MI_PropTrim_Rusted_01_A",
)


def _proptrim_library_parent_stem(mi_stem: str) -> str:
    s = (mi_stem or "").lower().replace("-", "_")
    if "proptrim" not in s and "prop_trim" not in s:
        return ""
    for tokens, lib in _PROPTRIM_LIBRARY_BY_TOKEN:
        if any(t in s for t in tokens):
            return lib
    return ""


def _proptrim_sibling_mi_stems(mi_stem: str) -> list[str]:
    """MI_*_PropTrim_X → Beams / family siblings that often author full CR/NXX."""
    m = re.search(r"(?i)(_PropTrim_.+)$", mi_stem or "")
    if not m:
        return []
    suffix = m.group(1)
    out = []
    for prefix in (
        "MI_Wrh_Beams_01", "MI_Wrh_Beams_02", "MI_Wrh_Beams_03",
        "MI_Wrh_Ceiling_01", "MI_Wrh_Ceiling_02",
    ):
        out.append(f"{prefix}{suffix}")
        out.append(f"{prefix}{suffix}_MCP_01")
    return out


def _mi_json_has_cr_texture(json_path: str) -> bool:
    """Peek MI JSON for authored CR without running PropTrim inherit (avoids recursion)."""
    if not json_path or not os.path.isfile(json_path):
        return False
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return False
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return False
    tex = data.get("Textures") or {}
    if isinstance(tex, dict) and ("CR Texture" in tex or "CR" in tex):
        return True
    entry = utils.first_ue_export(data, "MaterialInstanceConstant") if hasattr(utils, "first_ue_export") else None
    if not entry and isinstance(data.get("Exports"), list):
        for e in data["Exports"]:
            if "MaterialInstance" in str(e.get("Type") or ""):
                entry = e
                break
    if not entry:
        return False
    for t in (entry.get("Properties") or {}).get("TextureParameterValues") or []:
        info = t.get("ParameterInfo") or {}
        name = str(info.get("Name") or t.get("ParameterName") or "")
        if name in ("CR Texture", "CR"):
            return True
    return False


def _proptrim_folder_cr_donor(folder: str, mi_path: str = "") -> str:
    """Same-folder PropTrim MI that already authors CR/NXX (any sibling family)."""
    if not folder or not os.path.isdir(folder):
        return ""
    mi_key = os.path.normcase(os.path.normpath(mi_path or ""))
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return ""
    for fname in names:
        low = fname.lower()
        if not low.endswith(".json"):
            continue
        if "proptrim" not in low and "prop_trim" not in low:
            continue
        if not (low.startswith("mi_") or low.startswith("m_")):
            continue
        path = os.path.join(folder, fname)
        if os.path.normcase(os.path.normpath(path)) == mi_key:
            continue
        if _mi_json_has_cr_texture(path):
            return path
    return ""


def _enrich_proptrim_inherited_textures(mi: dict, mi_stem: str, mi_path: str = "") -> None:
    """Fill missing CR/NXX on PropTrim children from library / sibling / folder donor."""
    if not mi:
        return
    stem = (mi_stem or "").strip()
    stem_l = stem.lower()
    if "proptrim" not in stem_l and "prop_trim" not in stem_l:
        return
    params = {p for p, _ in (mi.get("textures") or [])}
    if "CR Texture" in params or "CR" in params:
        return

    folder = os.path.dirname(mi_path or "")
    candidates: list[str] = []
    for sib in _proptrim_sibling_mi_stems(stem):
        if sib not in candidates:
            candidates.append(sib)
    lib = _proptrim_library_parent_stem(stem)
    if lib and lib not in candidates:
        candidates.append(lib)
    for fb in _PROPTRIM_LIBRARY_FALLBACKS:
        if fb not in candidates:
            candidates.append(fb)

    parent_path = ""
    mi_key = os.path.normcase(os.path.normpath(mi_path or ""))
    for cand in candidates:
        found = _resolve_mi_json_path(cand, "", folder)
        if not found or not os.path.isfile(found):
            continue
        if os.path.normcase(os.path.normpath(found)) == mi_key:
            continue
        # Prefer donors that actually expose CR (skip other AO-only children).
        if not _mi_json_has_cr_texture(found):
            continue
        parent_path = found
        break
    if not parent_path:
        parent_path = _proptrim_folder_cr_donor(folder, mi_path)
    if not parent_path:
        return

    # Parent parse re-enters enrich; parents with CR return immediately.
    parent = _parse_flat_mi_json(parent_path)
    have = {p for p, _ in (mi.get("textures") or [])}
    added = 0
    for param, path in parent.get("textures") or []:
        if not param or not path or param in have:
            continue
        pl = str(param).lower()
        # Never clobber child Prop AO / Overlay overrides.
        if param in ("Prop AO Texture", "Overlay") or "overlay" in pl:
            continue
        if "ao" in pl and "prop" in pl:
            continue
        inherit = (
            param in _PROPTRIM_INHERIT_PARAM_KEYS
            or pl.startswith("t_proptrimsheet")
            or pl.endswith("_cr")
            or "nxx" in pl
            or "nmx" in pl
            or pl.endswith("_nom")
        )
        if not inherit:
            continue
        mi.setdefault("textures", []).append((param, path))
        have.add(param)
        added += 1
    if added and not mi.get("parent"):
        mi["parent"] = os.path.splitext(os.path.basename(parent_path))[0]
    if added:
        try:
            utils.get_logger().info(
                "PropTrim inherit %s ← %s (+%d tex)",
                stem, os.path.basename(parent_path), added,
            )
        except Exception:
            pass


def _effective_sm_slot_name(slot: dict) -> str:
    """Prefer ImportedMaterialSlotName when MaterialSlotName is mapper / Material_N noise."""
    name = (slot.get("MaterialSlotName") or "").strip()
    imported = (slot.get("ImportedMaterialSlotName") or "").strip()
    nl = name.lower()
    if imported and (
        not name
        or nl.startswith("m_trimmapper")
        or nl.startswith("material_")
        or re.match(r"^material(\.\d+)?$", nl)
        or nl.startswith("uematerial")
    ):
        return imported
    return name or imported


def _set_material_clip(mat, threshold: float = 0.3333, two_sided: bool = False):
    """Masked/CLIP alpha so shell-style meshes don't render as opaque floats."""
    try:
        mat.blend_method = 'CLIP'
    except Exception:
        pass
    try:
        mat.alpha_threshold = float(threshold)
    except Exception:
        pass
    try:
        # EEVEE Next / Blender 4.2+
        mat.surface_render_method = 'DITHERED'
    except Exception:
        pass
    try:
        mat.use_backface_culling = not bool(two_sided)
    except Exception:
        pass


def _set_material_alpha_mode(
    mat,
    mode: str = "CLIP",
    threshold: float = 0.3333,
    two_sided: bool = False,
):
    """Apply CLIP / HASHED / BLEND / OPAQUE for map materials.

    ``mode``: CLIP | HASHED | BLEND | OPAQUE
    """
    mode_u = (mode or "CLIP").upper()
    try:
        if mode_u == "HASHED":
            mat.blend_method = "HASHED"
        elif mode_u == "BLEND":
            mat.blend_method = "BLEND"
        elif mode_u == "OPAQUE":
            mat.blend_method = "OPAQUE"
        else:
            mat.blend_method = "CLIP"
    except Exception:
        pass
    try:
        if mode_u in ("CLIP", "HASHED"):
            mat.alpha_threshold = float(threshold)
    except Exception:
        pass
    try:
        if mode_u == "BLEND":
            mat.surface_render_method = "BLENDED"
        elif mode_u == "OPAQUE":
            mat.surface_render_method = "DITHERED"
        else:
            mat.surface_render_method = "DITHERED"
    except Exception:
        pass
    try:
        mat.use_backface_culling = not bool(two_sided)
    except Exception:
        pass


def _is_enemy_decal_mi(mi: dict) -> bool:
    """True for enemy-shell Masked NAO + Height decals — not env trim NAO.

    Env materials (e.g. ``MI_Concrete_Trim_*``) also sample ``*_NAO`` trim sheets.
    Treating any ``_NAO`` path as an enemy decal previously routed wall edge
    slots through ``_setup_enemy_decal_material`` and could fall back onto
    ``T_EnemyDecals_*`` atlases.
    """
    textures = mi.get("textures") or []
    if not textures:
        return False
    params_l = {str(p).lower(): (obj or "") for p, obj in textures}
    path_blob = " ".join(params_l.values()).lower().replace("\\", "/")
    parent_l = (mi.get("parent") or "").lower()

    if "enemydecals" in path_blob or "enemydecals" in " ".join(params_l):
        return True
    if "decaltrimsheet" in params_l:
        return True
    # Classic enemy preset: NAO (or DecalTrimsheet) + Height/HX on an enemy parent
    has_nao = "nao" in params_l or "decaltrimsheet" in params_l
    has_height = any(k in params_l for k in ("height/hx", "height", "hx"))
    if has_nao and has_height and (
        "enemy" in path_blob or "enemypreset" in parent_l or "enemy" in parent_l
    ):
        return True
    return False


def _slot_implies_map_decal(slot_lower: str) -> bool:
    """True only for slots that are actually decal/sticker/leak — not *EdgeDecal* trims."""
    slot = (slot_lower or "").strip().lower()
    if not slot:
        return False
    # Structural surface names that merely contain "decal" (ConcreteEdgeDecal).
    if "edgedecal" in slot or "edge_decal" in slot:
        return False
    if any(k in slot for k in ("concrete", "metal", "rebar", "trim", "slab", "plaster", "brick")):
        if "decal" in slot:
            return False
    if slot in (
        "decals", "decal", "m_decal", "m_leak", "leak",
        "raidermark", "raider mark", "raider_mark",
        "stickerdecal", "tagdecal", "walldecal",
    ):
        return True
    if slot.startswith("decal") or slot.endswith("decal"):
        return True
    return False


def _is_character_enemy_outfit_decal_asset(mi_stem: str = "", mi_path: str = "") -> bool:
    """True when an MI belongs to enemy/character/outfit decal atlases — not map env."""
    blob = f"{mi_stem} {mi_path}".lower().replace("\\", "/")
    if "enemydecals" in blob or "/decals/enemies/" in blob:
        return True
    if re.search(r"mi_.*enemy.*decal", blob):
        return True
    if "/characters/" in blob and "decal" in blob:
        return True
    if any(k in blob for k in ("/outfits/", "/clothing/", "outfit")) and "decal" in blob:
        return True
    return False


def _load_mi_textures(json_path: str, nodes, links, group_node,
                      connected: dict, x_conn=-600, x_unconn=-1000):
    mi = _parse_flat_mi_json(json_path)
    row_c = 400
    row_u = 400
    seen_paths = {}
    connected_set = set()

    for param, obj_path in mi['textures']:
        if obj_path.startswith('/Engine/') or 'CharacterSnow' in obj_path:
            continue
        fpath = textures.find_texture_from_object_path(obj_path + '.0')
        if not fpath:
            continue

        if fpath in seen_paths:
            existing_node = seen_paths[fpath]
            if param in connected and group_node:
                socket = connected[param]
                if socket not in connected_set and socket in group_node.inputs:
                    links.new(existing_node.outputs["Color"], group_node.inputs[socket])
                    connected_set.add(socket)
            continue

        img = bpy.data.images.load(fpath, check_existing=True)
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param
        node.interpolation = "Cubic"
        seen_paths[fpath] = node

        is_normal = ("Normal" in param or "normal" in param or
                     param in ("PM_Normals", "T_EYE_NORMALS", "T_Eye_Wet_Normal"))

        if param in connected and group_node:
            socket = connected[param]
            if socket not in connected_set and socket in group_node.inputs:
                if is_normal:
                    img.colorspace_settings.name = "Non-Color"
                node.location = (x_conn, row_c)
                links.new(node.outputs["Color"], group_node.inputs[socket])
                connected_set.add(socket)
                row_c -= 300
            else:
                node.location = (x_unconn, row_u)
                row_u -= 300
        else:
            if is_normal:
                img.colorspace_settings.name = "Non-Color"
            node.location = (x_unconn, row_u)
            row_u -= 300

    for j, (param, rgba) in enumerate(mi['colours']):
        rgb_node = nodes.new("ShaderNodeRGB")
        rgb_node.label = param
        rgb_node.outputs[0].default_value = rgba
        rgb_node.location = (700 + (j % 2) * 200, -(j // 2) * 180)


def _find_head_mi_json(psk_path: str, mi_name: str) -> str:
    folder = os.path.dirname(psk_path)
    path = os.path.join(folder, mi_name + ".json")
    return path if os.path.isfile(path) else ""


def _mat_slot_suffix(mat_name: str) -> str:
    return mat_name.rsplit("_", 1)[-1].lower() if "_" in mat_name else mat_name.lower()


def _setup_head_slot_head(slot, psk_path: str):
    if not utils.ensure_node_group("Face Shader"):
        return
    mi_path = _find_head_mi_json(psk_path, slot.material.name)
    mat = bpy.data.materials.new(name=slot.material.name + "_Setup")
    mat.use_nodes = True
    slot.material = mat
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    group_node = nodes.new("ShaderNodeGroup")
    group_node.node_tree = bpy.data.node_groups["Face Shader"]
    group_node.location = (0, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (300, 0)
    if group_node.outputs:
        links.new(group_node.outputs[0], out_node.inputs["Surface"])
    if mi_path:
        _load_mi_textures(mi_path, nodes, links, group_node, connected={
            "BaseColor": "Face Albedo",
            "PM_Diffuse": "Face Albedo",
            "Normal": "Normal 1",
            "PM_Normals": "Normal 1",
            "T_Head_01_MASKS": "Blue Map",
        })


def _setup_head_slot_teeth(slot, psk_path: str):
    if not utils.ensure_node_group("Teeth"):
        return
    mi_path = _find_head_mi_json(psk_path, slot.material.name)
    mat = bpy.data.materials.new(name=slot.material.name + "_Setup")
    mat.use_nodes = True
    slot.material = mat
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    group_node = nodes.new("ShaderNodeGroup")
    group_node.node_tree = bpy.data.node_groups["Teeth"]
    group_node.location = (0, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (300, 0)
    if group_node.outputs:
        links.new(group_node.outputs[0], out_node.inputs["Surface"])
    if mi_path:
        color_input = "Base Colour" if "Base Colour" in group_node.inputs else "Base Color"
        _load_mi_textures(mi_path, nodes, links, group_node, connected={
            "teeth_color_map": color_input,
            "Normal": "Normal",
            "PM_Normals": "Normal",
            "teeth_normal_map": "Normal",
        })


def _setup_head_slot_eyes(slot, psk_path: str):
    if not utils.ensure_material("Eyes"):
        return
    slot.material = bpy.data.materials["Eyes"]


def _setup_head_slot_eyelashes(slot, psk_path: str):
    mi_path = _find_head_mi_json(psk_path, slot.material.name)
    mat = bpy.data.materials.new(name=slot.material.name + "_Setup")
    mat.use_nodes = True
    mat.blend_method = 'CLIP'
    slot.material = mat
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (0, 0)
    principled.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (300, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])
    if mi_path:
        mi = _parse_flat_mi_json(mi_path)
        row = 400
        for param, obj_path in mi['textures']:
            if 'CharacterSnow' in obj_path or obj_path.startswith('/Engine/'):
                continue
            fpath = textures.find_texture_from_object_path(obj_path + '.0')
            if not fpath:
                continue
            img = bpy.data.images.load(fpath, check_existing=True)
            img.colorspace_settings.name = "Non-Color"
            node = nodes.new("ShaderNodeTexImage")
            node.image = img
            node.label = param
            node.interpolation = "Cubic"
            node.location = (-400, row)
            row -= 300
            if param == "T_Eyelashes_M":
                links.new(node.outputs["Alpha"], principled.inputs["Alpha"])


def _setup_head_slot_wet(slot):
    if not utils.ensure_material("Wet"):
        return
    slot.material = bpy.data.materials["Wet"]


def setup_face_material(obj, psk_path: str):
    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = obj
    seen = set()
    i = 0
    while i < len(obj.material_slots):
        slot = obj.material_slots[i]
        mat_name = slot.material.name if slot.material else ""
        suffix = _mat_slot_suffix(mat_name)
        if suffix in seen:
            obj.active_material_index = i
            bpy.ops.object.material_slot_remove()
        else:
            seen.add(suffix)
            i += 1
    bpy.context.view_layer.objects.active = prev_active

    for slot in obj.material_slots:
        if not slot.material:
            continue
        suffix = _mat_slot_suffix(slot.material.name)
        if suffix == "head":
            _setup_head_slot_head(slot, psk_path)
        elif suffix in ("teeth", "saliva"):
            _setup_head_slot_teeth(slot, psk_path)
        elif suffix == "eyes":
            _setup_head_slot_eyes(slot, psk_path)
        elif suffix == "eyelashes":
            _setup_head_slot_eyelashes(slot, psk_path)
        elif suffix in ("eyeshell", "eyeedge"):
            _setup_head_slot_wet(slot)

    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = obj
    i = 0
    while i < len(obj.material_slots):
        slot = obj.material_slots[i]
        mat_name = slot.material.name if slot.material else ""
        if mat_name.lower().endswith("base_head"):
            obj.active_material_index = i
            bpy.ops.object.material_slot_remove()
        else:
            i += 1
    bpy.context.view_layer.objects.active = prev_active


# ---------------------------------------------------------------------------
# Hair material setup
# ---------------------------------------------------------------------------

def _parse_hair_mi(json_path: str) -> dict:
    result = {'textures': [], 'colours': []}
    if not json_path or not os.path.isfile(json_path):
        return result
    try:
        with open(json_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        entry = utils.first_ue_export(data, "MaterialInstanceConstant") or utils.first_ue_export(data)
        props = entry.get('Properties', {})
        for tp in props.get('TextureParameterValues', []):
            name = tp.get('ParameterInfo', {}).get('Name', '')
            obj_path = tp.get('ParameterValue', {}).get('ObjectPath', '')
            obj_name = tp.get('ParameterValue', {}).get('ObjectName', '')
            m = re.search(r"'([^']+)'", obj_name)
            stem = m.group(1) if m else ''
            if stem:
                result['textures'].append((name, stem, obj_path))
        for vp in props.get('VectorParameterValues', []):
            name = vp.get('ParameterInfo', {}).get('Name', '')
            pv = vp.get('ParameterValue', {})
            result['colours'].append((name, (
                float(pv.get('R', 1.0)),
                float(pv.get('G', 1.0)),
                float(pv.get('B', 1.0)),
                float(pv.get('A', 1.0)),
            )))
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to parse hair MI '{json_path}': {e}")
    return result


def setup_hair_material(obj, json_path: str):
    mat = bpy.data.materials.new(name=obj.name + "_Hair_Mat")
    mat.use_nodes = True
    mat.blend_method = 'CLIP'
    obj.active_material = mat
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (0, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (300, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    if not json_path or json_path == 'NONE':
        return

    mi = _parse_hair_mi(json_path)
    loaded = {}
    for param_name, tex_stem, obj_path in mi['textures']:
        fpath = textures.find_texture_from_object_path(obj_path)
        if not fpath:
            continue
        img = bpy.data.images.load(fpath, check_existing=True)
        loaded[param_name] = (fpath, img)

    x_connected = -600
    x_unconnected = -1000
    row_conn = 400
    row_unconn = 400

    if 'GlobalHairColor' in loaded:
        _, img = loaded['GlobalHairColor']
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = "GlobalHairColor"
        node.interpolation = "Cubic"
        node.location = (x_connected, row_conn)
        links.new(node.outputs["Color"], principled.inputs["Base Color"])
        row_conn -= 300

    if 'Coverage' in loaded:
        _, img = loaded['Coverage']
        img.colorspace_settings.name = "Non-Color"
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = "Coverage (Alpha)"
        node.interpolation = "Cubic"
        node.location = (x_connected, row_conn)
        links.new(node.outputs["Color"], principled.inputs["Alpha"])
        row_conn -= 300

    handled = {'GlobalHairColor', 'Coverage'}
    for param_name in ('AttributeMap', 'Depth', 'Tangent'):
        if param_name not in loaded:
            continue
        _, img = loaded[param_name]
        img.colorspace_settings.name = "Non-Color"
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param_name
        node.interpolation = "Cubic"
        node.location = (x_unconnected, row_unconn)
        row_unconn -= 300
        handled.add(param_name)

    for param_name, (fpath, img) in loaded.items():
        if param_name in handled:
            continue
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param_name
        node.interpolation = "Cubic"
        node.location = (x_unconnected, row_unconn)
        row_unconn -= 300

    for j, (param_name, rgba) in enumerate(mi['colours']):
        rgb_node = nodes.new("ShaderNodeRGB")
        rgb_node.label = param_name
        rgb_node.name = param_name
        rgb_node.outputs[0].default_value = rgba
        rgb_node.location = (700 + (j % 2) * 200, -(j // 2) * 180)


# ---------------------------------------------------------------------------
# Multi-slot material setup (weapons + enemies)
# ---------------------------------------------------------------------------

def _character_layout_search_folders(*paths: str) -> list[str]:
    """Heroes/character export layout: Meshes + Materials + Textures siblings.

    Kalika etc. keep ``SK_*.psk`` under ``.../Resources/Base/Meshes/`` while
    ``MI_*.json`` lives in ``.../Materials/`` and PNGs in ``.../Textures/``.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(folder: str) -> None:
        if not folder:
            return
        key = os.path.normcase(os.path.normpath(folder))
        if key in seen:
            return
        if os.path.isdir(folder):
            seen.add(key)
            out.append(folder)

    for path in paths:
        if not path:
            continue
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if not folder:
            continue
        _add(folder)
        parent = os.path.dirname(folder)
        _add(parent)
        for sub in ("Materials", "Material", "Textures", "Meshes"):
            _add(os.path.join(parent, sub))
            _add(os.path.join(folder, sub))
    return out


def _mi_json_matches_requested_stem(json_path: str, requested_stem: str) -> bool:
    """True when MI JSON body looks like the stem we asked for.

    Some FModel dumps write the wrong asset under an MI_*.json filename
    (Package/Name point at a different MI). Accept when Name/Package are
    missing (legacy wrappers) or match; reject clear mismatches so Stage 2
    does not paint Aircon with Awning / Concrete with Rock.
    """
    stem = (requested_stem or "").strip()
    if "." in stem:
        stem = stem.split(".", 1)[0]
    if not stem or not json_path or not os.path.isfile(json_path):
        return False
    stem_l = stem.lower()
    # Filename is a weak signal — still verify body when Name/Package exist.
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return False
    entry = None
    for e in utils.ue_export_entries(data):
        if not isinstance(e, dict):
            continue
        if e.get("Type") in ("MaterialInstanceConstant", "MaterialInstance"):
            entry = e
            break
        if entry is None:
            entry = e
    if not isinstance(entry, dict):
        return False
    name = (entry.get("Name") or "").strip()
    if "." in name:
        name = name.split(".", 1)[0]
    package = (entry.get("Package") or "").replace("\\", "/").strip()
    pkg_leaf = package.rsplit("/", 1)[-1] if package else ""
    if "." in pkg_leaf:
        pkg_leaf = pkg_leaf.split(".", 1)[0]
    # No identity fields — cannot prove mismatch (older / odd wrappers).
    if not name and not pkg_leaf:
        return True
    if name and name.lower() != stem_l:
        return False
    if pkg_leaf and pkg_leaf.lower() != stem_l:
        return False
    return True


def _mesh_json_matches_requested_stem(json_path: str, requested_stem: str) -> bool:
    """True when SM_/SK_ JSON body Name/Package matches the mesh stem we asked for.

    FModel parallel Properties export can write SK_Western_Belt under
    SM_POI16_WaterControl_Roof_01_A.json (shared Document race). Accepting that
    body paints belt/helmet cosmetics onto roofs and rocks.
    """
    stem = (requested_stem or "").strip()
    if "." in stem:
        stem = stem.split(".", 1)[0]
    if not stem or not json_path or not os.path.isfile(json_path):
        return False
    stem_l = stem.lower()
    # Hash-bake / LOD variants: compare against stem variants.
    variants = {v.lower() for v in _mesh_stem_variants(stem)}
    variants.add(stem_l)
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return False
    entry = None
    for e in utils.ue_export_entries(data):
        if not isinstance(e, dict):
            continue
        if e.get("Type") in ("StaticMesh", "SkeletalMesh"):
            entry = e
            break
    if not isinstance(entry, dict):
        return False
    name = (entry.get("Name") or "").strip()
    if "." in name:
        name = name.split(".", 1)[0]
    package = (entry.get("Package") or "").replace("\\", "/").strip()
    pkg_leaf = package.rsplit("/", 1)[-1] if package else ""
    if "." in pkg_leaf:
        pkg_leaf = pkg_leaf.split(".", 1)[0]
    if not name and not pkg_leaf:
        return True
    name_l = name.lower() if name else ""
    pkg_l = pkg_leaf.lower() if pkg_leaf else ""
    if name_l and name_l not in variants:
        return False
    if pkg_l and pkg_l not in variants:
        return False
    return True


def _resolve_mi_json_path(
    mi_stem: str,
    obj_path: str,
    psk_folder: str,
    *,
    context: str = CTX_ANY,
) -> str:
    """Find MI_*.json beside the PSK, via ObjectPath, or scoped FMDex.

    ``context`` gates which trees are legal (map never accepts Characters/).
    ObjectPath is preferred over any basename walk.
    """
    if not mi_stem:
        return ""

    mi_stem = mi_stem.strip()
    # ObjectPath outside the allowed context → refuse before any disk search.
    if obj_path and not path_allowed_for_context(obj_path, context):
        try:
            utils.get_logger().debug(
                "reject MI ObjectPath for context=%s stem=%s path=%s",
                context, mi_stem, obj_path,
            )
        except Exception:
            pass
        return ""

    cache_key = (
        mi_stem.lower(),
        _norm_path_key(psk_folder or ""),
        (obj_path or "").replace("\\", "/").lower(),
        (context or CTX_ANY),
    )
    if cache_key in _MI_JSON_PATH_CACHE:
        return _MI_JSON_PATH_CACHE[cache_key]

    def _remember(path: str) -> str:
        _MI_JSON_PATH_CACHE[cache_key] = path or ""
        return path or ""

    def _accept(path: str) -> str:
        """Cache + return path only when body identity + context match."""
        if not path or not os.path.isfile(path):
            return ""
        if not path_allowed_for_context(path, context):
            try:
                utils.get_logger().debug(
                    "reject MI path for context=%s stem=%s: %s",
                    context, mi_stem, path,
                )
            except Exception:
                pass
            return ""
        if not _mi_json_matches_requested_stem(path, mi_stem):
            try:
                utils.get_logger().debug(
                    "reject MI JSON identity mismatch for '%s': %s", mi_stem, path
                )
            except Exception:
                pass
            return ""
        return _remember(path)

    # UE asset ObjectPaths frequently include redundant dot segments (e.g.
    # "MI_X.MI_X.0"). Those may flow into mi_stem and break local filename
    # matching, so try both the raw stem and the "first token" stem.
    stem_candidates = [mi_stem]
    if "." in mi_stem:
        stem0 = mi_stem.split(".", 1)[0]
        if stem0 and stem0 not in stem_candidates:
            stem_candidates.append(stem0)

    for stem in stem_candidates:
        search_folders: list[str] = []
        if psk_folder:
            search_folders.append(psk_folder)
            # Heroes Base: MI JSON is under sibling Materials/, not Meshes/.
            # Never pull character layout siblings when resolving map/env props.
            if context != CTX_MAP:
                search_folders.extend(_character_layout_search_folders(psk_folder))
            for alt in utils.remap_path_into_content_dirs(
                os.path.join(psk_folder, "__probe__")
            ):
                search_folders.append(os.path.dirname(alt))
                if context != CTX_MAP:
                    search_folders.extend(
                        _character_layout_search_folders(os.path.dirname(alt))
                    )
        shared = utils.get_weapon_shared_folder()
        if shared and context in (CTX_ANY, CTX_WEAPON):
            search_folders.append(shared)
        seen_sf: set[str] = set()
        for search_folder in search_folders:
            key = os.path.normcase(os.path.normpath(search_folder or ""))
            if not search_folder or key in seen_sf:
                continue
            if not path_allowed_for_context(search_folder, context):
                continue
            seen_sf.add(key)
            candidate = os.path.join(search_folder, stem + ".json")
            hit = _accept(candidate)
            if hit:
                return hit

        # ObjectPath is O(1) path join — prefer before FMDex (basename keys may walk).
        if obj_path:
            found = textures.find_asset_from_object_path(obj_path, ".json")
            hit = _accept(found) if found else ""
            if hit:
                return hit

            # Extra defensive tries for instance-suffixed ObjectPaths.
            alt = obj_path
            if alt.endswith(".0"):
                found = textures.find_asset_from_object_path(alt[:-2], ".json")
                hit = _accept(found) if found else ""
                if hit:
                    return hit
            else:
                found = textures.find_asset_from_object_path(alt + ".0", ".json")
                hit = _accept(found) if found else ""
                if hit:
                    return hit

        # FMDex: full package path OK; basename Content walks are gated by context
        # (map forbids Characters/ and skips unconstrained Pioneer walks).
        try:
            from . import fmdex
            found = fmdex.resolve_export_file(
                stem, ".json", context=context, allow_basename_walk=(context != CTX_MAP),
            )
            hit = _accept(found) if found else ""
            if hit:
                return hit
        except TypeError:
            # Older fmdex without context kwargs
            try:
                from . import fmdex
                found = fmdex.resolve_export_file(stem, ".json")
                hit = _accept(found) if found else ""
                if hit:
                    return hit
            except Exception:
                pass
        except Exception:
            pass

        # Enemy meshes often live under Enemies/<Type>/.../Art while shared MIs
        # sit in Enemies/Shared/Art/<Set>/ (e.g. LightAndEliteDrone). Search there
        # before relying on PioneerGame Root ObjectPath resolve.
        if psk_folder and context in (CTX_ANY, CTX_ENEMY, CTX_WEAPON):
            norm = psk_folder.replace("\\", "/").lower()
            marker = "/enemies/"
            idx = norm.find(marker)
            if idx >= 0:
                enemies_root = psk_folder[: idx + len(marker) - 1]  # .../Enemies
                shared_art = os.path.join(enemies_root, "Shared", "Art")
                if os.path.isdir(shared_art):
                    target_l = (stem + ".json").lower()
                    try:
                        for walk_root, _dirs, files in os.walk(shared_art):
                            for fname in files:
                                if fname.lower() == target_l:
                                    hit = _accept(os.path.join(walk_root, fname))
                                    if hit:
                                        return hit
                    except OSError:
                        pass

        # Last-resort: MaterialLibrary MI folders under every known Content root.
        # Prefer direct folder hits; bounded walk of Material_Instances only (not
        # the whole Pioneer tree — that made Stage 2 unusable at city scale).
        # Map/effect contexts may use MaterialLibrary; never Characters.
        if context in (CTX_ANY, CTX_MAP, CTX_EFFECT, CTX_ENEMY, CTX_WEAPON):
            try:
                for content_dir in utils.get_content_dirs():
                    inst_root = os.path.join(
                        content_dir, "Pioneer", "MaterialLibrary", "Material_Instances",
                    )
                    for folder_name in (
                        "Enemies", "Decals", "Weapons", "Items", "Props", "Themes", "Trims",
                    ):
                        inst_folder = os.path.join(inst_root, folder_name)
                        candidate = os.path.join(inst_folder, stem + ".json")
                        hit = _accept(candidate)
                        if hit:
                            return hit
                    if os.path.isdir(inst_root):
                        target_l = (stem + ".json").lower()
                        walked = 0
                        for walk_root, _dirs, files in os.walk(inst_root):
                            for fname in files:
                                if fname.lower() == target_l:
                                    hit = _accept(os.path.join(walk_root, fname))
                                    if hit:
                                        return hit
                            walked += 1
                            if walked > 4000:
                                break
                    # Map props often live beside meshes under Environment/... —
                    # try ObjectPath-less basename only in the remapped mesh folder.
                    if psk_folder:
                        for alt in utils.remap_path_into_content_dirs(
                            os.path.join(psk_folder, stem + ".json"),
                            [content_dir],
                        ):
                            hit = _accept(alt)
                            if hit:
                                return hit
            except Exception:
                pass

    return _remember("")


def _mesh_stem_variants(psk_stem: str) -> list[str]:
    """Stems to try when resolving SM/SK material JSON beside a mesh file.

    Order matters:
      1. Exact stem (keeps PROXY ``*_LOD1`` working)
      2. FModel spline-bake hash strip: ``SM_Road_…-BA270E`` → ``SM_Road_…``
      3. LOD-number strip as a last fallback only
      4. SM_/SK_ prefix removed variants of the above
    """
    stem = (psk_stem or "").strip()
    if not stem:
        return []

    # Blender object names sometimes leak into callers — normalize.
    stem = re.sub(r"^SRC_", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"^Spline_", "", stem, flags=re.IGNORECASE)

    variants: list[str] = []

    def _add(s: str) -> None:
        s = (s or "").strip()
        if s and s not in variants:
            variants.append(s)

    _add(stem)

    # FModel Map + Meshes spline bake: unique deformed copy Name-HEX.uemodel
    # Hex length is typically 5–8 (BA270E, 018AA6, …). Also accept 4+ for
    # truncated object names like …-BA27 when stem comes from Blender object.
    hash_stripped = re.sub(r"-[0-9A-Fa-f]{4,10}$", "", stem)
    if hash_stripped != stem:
        _add(hash_stripped)
        print(
            f"Arc Raiders Stage 2: spline-bake stem '{stem}' -> base '{hash_stripped}'"
        )

    for base in list(variants):
        lod_stripped = re.sub(r"_LOD\d+$", "", base, flags=re.IGNORECASE)
        if lod_stripped != base:
            _add(lod_stripped)

    for base in list(variants):
        if base.upper().startswith("SM_"):
            _add(base[3:])
        elif base.upper().startswith("SK_"):
            _add(base[3:])

    return variants


def _mesh_material_json_paths(psk_path: str) -> list[str]:
    """Candidate SK_/SM_*.json paths beside the mesh and under full Content dumps.

    FModel Map + Meshes exports .uemodel under MapPlacements without sibling JSON.
    StaticMaterials live in the full PioneerGame Content dump at the same relative path.

    Spline bake exports use ``SM_Asset-HEX.uemodel``; materials come from base
    ``SM_Asset.json`` in the same package folder.
    """
    if not psk_path:
        return []
    psk_path = os.path.normpath(os.path.abspath(bpy.path.abspath(psk_path)))
    psk_stem = os.path.splitext(os.path.basename(psk_path))[0]
    stems = _mesh_stem_variants(psk_stem)
    names = [s + ".json" for s in stems]

    folders: list[str] = []
    seen_folders: set[str] = set()

    def _add_folder(folder: str) -> None:
        if not folder:
            return
        key = os.path.normcase(os.path.normpath(folder))
        if key in seen_folders:
            return
        seen_folders.add(key)
        folders.append(os.path.normpath(folder))

    _add_folder(os.path.dirname(psk_path))
    # Remap MapPlacements/.../Content/Pioneer/... → full dump Content/Pioneer/...
    for alt_mesh in utils.remap_path_into_content_dirs(psk_path):
        _add_folder(os.path.dirname(alt_mesh))

    out: list[str] = []
    seen_paths: set[str] = set()

    def _add_path(path: str) -> None:
        key = os.path.normcase(os.path.normpath(path))
        if key in seen_paths:
            return
        seen_paths.add(key)
        out.append(os.path.normpath(path))

    for folder in folders:
        for name in names:
            candidate = os.path.join(folder, name)
            _add_path(candidate)
            if os.path.isfile(candidate) and re.search(
                r"-[0-9A-Fa-f]{4,10}\.uemodel$", psk_path, re.IGNORECASE
            ):
                # Proof / debug: hashed bake found base JSON
                print(
                    f"Arc Raiders Stage 2: hash-bake JSON hit "
                    f"{os.path.basename(psk_path)} -> {candidate}"
                )
        # Nested export: .../StaticMesh_0/StaticMesh_0.json beside .../StaticMesh_0.uemodel
        for stem in stems:
            _add_path(os.path.join(folder, stem, stem + ".json"))

    # Basename fallback via FMDex when folder remap missed the canonical SM package.
    # Map context: never unconstrained Pioneer basename walks (Characters bleed).
    try:
        from . import fmdex

        for stem in stems:
            # Skip walking for the unique bake id itself (Name-HEX).
            if re.search(r"-[0-9A-Fa-f]{4,10}$", stem):
                continue
            found = fmdex.resolve_export_file(
                stem, ".json", context=CTX_MAP, allow_basename_walk=False,
            )
            if found and path_allowed_for_context(found, CTX_MAP):
                _add_path(found)
                print(
                    f"Arc Raiders Stage 2: FMDex JSON hit '{stem}' -> {found}"
                )
    except TypeError:
        try:
            from . import fmdex

            for stem in stems:
                if re.search(r"-[0-9A-Fa-f]{4,10}$", stem):
                    continue
                found = fmdex.resolve_export_file(stem, ".json")
                if found and path_allowed_for_context(found, CTX_MAP):
                    _add_path(found)
        except Exception:
            pass
    except Exception:
        pass

    return out


def map_material_cache_key(psk_path: str) -> str:
    """Cache key so spline bake hashes ``SM_Foo-HEX.uemodel`` share one Stage 2 result."""
    if not psk_path:
        return ""
    psk_path = os.path.normpath(os.path.abspath(bpy.path.abspath(psk_path)))
    stem = os.path.splitext(os.path.basename(psk_path))[0]
    variants = _mesh_stem_variants(stem)
    base = stem
    for v in variants:
        if not re.search(r"-[0-9A-Fa-f]{4,10}$", v):
            if v.upper().startswith("SM_") or v.upper().startswith("SK_"):
                base = v
                break
            base = v
    return os.path.normcase(os.path.join(os.path.dirname(psk_path), base))


_ENGINE_OR_EMPTY_MESH_RE = re.compile(
    r"^(cube|plane|sphere|cylinder|cone|staticmesh_\d+)$",
    re.IGNORECASE,
)


def is_engine_or_placeholder_mesh(psk_path: str = "", asset_path: str = "", object_name: str = "") -> bool:
    """True for Engine BasicShapes / nameless StaticMesh_N with no useful map MI."""
    blob = f"{psk_path} {asset_path} {object_name}".lower().replace("\\", "/")
    if "/engine/content/basicshapes/" in blob or "/engine/content/engine/" in blob:
        return True
    stem = os.path.splitext(os.path.basename(psk_path or asset_path or object_name))[0]
    stem = re.sub(r"^src_", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"^spline_", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"_[0-9a-f]{6,8}$", "", stem, flags=re.IGNORECASE)
    # Strip spline-bake hash before placeholder check
    stem = re.sub(r"-[0-9a-f]{4,10}$", "", stem, flags=re.IGNORECASE)
    return bool(_ENGINE_OR_EMPTY_MESH_RE.match(stem or ""))


def _parse_sk_material_slots(psk_path: str, *, context: str = CTX_ANY) -> list:
    """Return [(slot_name, mi_stem, mi_json_path), ...] from sibling SK_/SM_*.json.

    Rejects mesh JSON whose Name/Package does not match the requested SM_/SK_
    stem (corrupt FModel dumps that put helmet/belt packages under rock/roof
    filenames). ``context`` gates MI ObjectPath / disk resolves.
    """
    if not psk_path:
        return []
    cache_key = f"{_norm_path_key(psk_path)}|{context or CTX_ANY}"
    if cache_key in _SK_SLOTS_CACHE:
        return _SK_SLOTS_CACHE[cache_key]

    psk_stem = os.path.splitext(os.path.basename(psk_path))[0]
    json_paths = _mesh_material_json_paths(psk_path)
    for json_path in json_paths:
        if not os.path.isfile(json_path):
            continue
        if not _mesh_json_matches_requested_stem(json_path, psk_stem):
            try:
                utils.get_logger().debug(
                    "reject SM/SK JSON identity mismatch for '%s': %s",
                    psk_stem, json_path,
                )
            except Exception:
                pass
            print(
                f"Arc Raiders: reject mismatched mesh JSON for '{psk_stem}': "
                f"{os.path.basename(json_path)} (wrong Name/Package body)"
            )
            continue
        if context == CTX_MAP and not path_allowed_for_context(json_path, CTX_MAP):
            print(
                f"Arc Raiders: reject out-of-context mesh JSON for map '{psk_stem}': "
                f"{json_path}"
            )
            continue
        folder = os.path.dirname(json_path)
        try:
            with open(json_path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            for entry in utils.ue_export_entries(data):
                entry_type = entry.get('Type', '')
                if entry_type not in ('SkeletalMesh', 'StaticMesh'):
                    continue
                props = entry.get('Properties', {})
                slots = (
                    entry.get('SkeletalMaterials')
                    or props.get('SkeletalMaterials')
                    or entry.get('StaticMaterials')
                    or props.get('StaticMaterials')
                    or []
                )
                result = []
                for slot in slots:
                    slot_name = _effective_sm_slot_name(slot)
                    mat_ref = (
                        slot.get('Material')
                        or slot.get('MaterialInterface')
                        or {}
                    )
                    obj_path = mat_ref.get('ObjectPath', '') or ""
                    mi_stem = textures.mi_stem_from_material_ref(mat_ref)
                    if not mi_stem:
                        # Keep a placeholder so later slots don't shift left and
                        # get matched against the wrong Blender material slot index.
                        result.append((slot_name, "", ""))
                        continue
                    # DecalMesh cards ship with Engine WorldGridMaterial — not a real MI.
                    stem_l = mi_stem.lower()
                    obj_l = (obj_path or "").replace("\\", "/").lower()
                    if (
                        stem_l in {"worldgridmaterial", "defaultmaterial"}
                        or "/engine/" in obj_l
                        or obj_l.startswith("/engine/")
                    ):
                        result.append((slot_name, "", ""))
                        continue
                    # Map props: refuse character/outfit ObjectPaths even if SM matched.
                    if obj_path and not path_allowed_for_context(obj_path, context):
                        print(
                            f"Arc Raiders: skip out-of-context MI '{mi_stem}' "
                            f"({obj_path}) for context={context}"
                        )
                        result.append((slot_name, mi_stem, ""))
                        continue
                    mi_json = _resolve_mi_json_path(
                        mi_stem, obj_path, folder, context=context,
                    )
                    result.append((slot_name, mi_stem, mi_json))
                if result:
                    _SK_SLOTS_CACHE[cache_key] = result
                    return result
        except Exception as e:
            print(f"Arc Raiders PSK Importer: Failed to parse mesh material slots: {e}")
    _SK_SLOTS_CACHE[cache_key] = []
    return []


def _parse_weapon_sk_json(psk_path: str) -> list:
    """Back-compat alias for multi-slot SK parsing."""
    return _parse_sk_material_slots(psk_path)


def _match_material_slot(
    obj,
    slot_name: str,
    slot_index: int,
    used_indices: set,
    mi_stem: str = "",
    *,
    allow_index_fallback: bool = True,
):
    """Bind SK/SM slot → Blender material slot.

    Prefer MI-stem / slot-name match when the mesh already has named materials.
    UEModel LOD material order often differs from SM JSON ``StaticMaterials``
    order (e.g. PerimeterWall: JSON slot0=Rebar, UEModel slot0=ConcreteDamaged).
    Index-first assignment painted rebar onto every face that still indexed 0.

    When ``allow_index_fallback`` is False (map props with UEModel MI_* names),
    never clobber by index — that is how belt/helmet MIs from corrupt SM JSON
    overwrote roofs and rocks.
    """
    wants = []
    for raw in (mi_stem, slot_name):
        want = (raw or "").strip().lower()
        if want and want not in wants:
            wants.append(want)
    # Warehouse ceilings etc.: SM ImportedMaterialSlotName is M_PropTrims while
    # Blender/UEModel keeps that name; MI stem is MI_*_PropTrim_*.
    if any("proptrim" in w for w in wants):
        for extra in ("m_proptrims", "m_proptrims_02", "proptrims", "m_proptrim"):
            if extra not in wants:
                wants.append(extra)
    if any("brokenglass" in w or w == "glass" for w in wants):
        for extra in ("brokenglass", "m_brokenglasssdf"):
            if extra not in wants:
                wants.append(extra)
    if wants:
        for i, s in enumerate(obj.material_slots):
            if i in used_indices or not s.material:
                continue
            sn = s.material.name.lower()
            sn0 = sn.split(".")[0]
            # Strip Stage 2 force_rebuild suffixes for matching
            sn0 = re.sub(r"(_force_rebuild)+$", "", sn0)
            arc_stem = ""
            try:
                arc_stem = str(s.material.get("arc_mi_stem") or "").strip().lower()
            except Exception:
                arc_stem = ""
            for want in wants:
                # PSK importer may prefix object name: "Obj_SlotName"
                if (
                    sn == want
                    or sn0 == want
                    or arc_stem == want
                    or sn.endswith("_" + want)
                    or (arc_stem and arc_stem.endswith("_" + want))
                ):
                    return s, i
                # Substring only for long tokens — short wants like "01"/"decal"
                # previously collided with MI_EnemyDecals_01 / unrelated slots.
                if len(want) >= 10 and (
                    want in sn0 or sn0 in want or (arc_stem and want in arc_stem)
                ):
                    return s, i
        if not allow_index_fallback:
            return None, -1
    if allow_index_fallback and slot_index < len(obj.material_slots) and slot_index not in used_indices:
        return obj.material_slots[slot_index], slot_index
    return None, -1


def _resolve_mi_texture_path(obj_path: str, local_folders: list = None) -> str:
    """Resolve a MI texture ObjectPath to a PNG, with local-folder fallback."""
    if not obj_path:
        return ""
    lookup = obj_path if obj_path.endswith(".0") else (obj_path + ".0")
    fpath = textures.find_texture_from_object_path(lookup)
    if fpath:
        return fpath
    leaf = obj_path.rstrip("/").split("/")[-1]
    leaf = leaf.split(".")[0]
    if not leaf:
        return ""
    for folder in (local_folders or []):
        if not folder or not os.path.isdir(folder):
            continue
        for ext in (".png", ".tga", ".jpg", ".jpeg"):
            candidate = os.path.join(folder, leaf + ext)
            if os.path.isfile(candidate):
                return candidate
    return ""


def _tex_lookup_from_flat_mi(mi: dict, local_folders: list = None) -> dict:
    """param -> (fpath, image) for textures referenced by a flat MI JSON."""
    tex_lookup = {}
    path_to_img = {}
    for param, obj_path in mi.get('textures', []):
        if not obj_path or obj_path.startswith('/Engine/') or 'CharacterSnow' in obj_path:
            continue
        fpath = _resolve_mi_texture_path(obj_path, local_folders)
        if not fpath:
            continue
        if fpath in path_to_img:
            tex_lookup[param] = (fpath, path_to_img[fpath])
            continue
        img = _load_image_cached(fpath)
        path_to_img[fpath] = img
        tex_lookup[param] = (fpath, img)
    return tex_lookup


def _find_flat_tex(tex_lookup: dict, *keys_or_suffixes: str):
    """Find a texture by exact MI param name, then by filename suffix (_cr, _nom, ...)."""
    lower_keys = {k.lower(): k for k in tex_lookup}
    for key in keys_or_suffixes:
        hit = lower_keys.get(key.lower())
        if hit:
            return hit, tex_lookup[hit][1]
    for key in keys_or_suffixes:
        suffix = key.lower()
        if not suffix.startswith('_'):
            suffix = '_' + suffix
        for param, (fpath, img) in tex_lookup.items():
            stem = os.path.splitext(os.path.basename(fpath))[0].lower()
            if stem.endswith(suffix):
                return param, img
    return None, None


def _unlink_input(links, socket):
    """Remove any existing links into a node socket."""
    for lnk in list(socket.links):
        links.remove(lnk)


def _apply_exx_packing(nodes, links, principled, ex_img, scalars: dict,
                       col_tex=-1600, col_util=-1100, col_mix=-400, row_y=-1400):
    """Wire packed EX/EXX maps.

    Channel packing (enemy/weapon convention):
      R = emissive mask  → Mix Color → Emission Color
      G/B = surface imperfection / soot masks → darken Base Color + raise Roughness
    """
    ex_img.colorspace_settings.name = "Non-Color"
    ex_node = nodes.new("ShaderNodeTexImage")
    ex_node.image = ex_img
    ex_node.label = "EXX (Emissive + Imperfections)"
    ex_node.interpolation = "Cubic"
    ex_node.location = (col_tex, row_y)

    sep = nodes.new("ShaderNodeSeparateColor")
    sep.label = "EXX Channels"
    sep.location = (col_util, row_y)
    links.new(ex_node.outputs["Color"], sep.inputs["Color"])

    emit_mix = nodes.new("ShaderNodeMix")
    emit_mix.data_type = 'RGBA'
    emit_mix.blend_type = 'MIX'
    emit_mix.label = "EXX R → Emission"
    emit_mix.location = (col_mix, row_y + 120)
    emit_mix.inputs[6].default_value = (0.0, 0.0, 0.0, 1.0)
    emit_mix.inputs[7].default_value = (1.0, 1.0, 1.0, 1.0)
    links.new(sep.outputs["Red"], emit_mix.inputs["Factor"])
    _unlink_input(links, principled.inputs["Emission Color"])
    links.new(emit_mix.outputs[2], principled.inputs["Emission Color"])
    if "Emission Strength" in principled.inputs:
        strength = float(scalars.get("Emissive", scalars.get("LightIntensity", 1.0)) or 1.0)
        principled.inputs["Emission Strength"].default_value = max(strength, 1.0)

    dirt_max = nodes.new("ShaderNodeMath")
    dirt_max.operation = 'MAXIMUM'
    dirt_max.label = "Max(G, B) Dirt"
    dirt_max.location = (col_util + 220, row_y - 160)
    links.new(sep.outputs["Green"], dirt_max.inputs[0])
    links.new(sep.outputs["Blue"], dirt_max.inputs[1])

    # UE SootMultiply is often 1–4; as a direct Mix factor that blacks out the mesh.
    # Scale ~0.1 so default (1) → subtle soot, and keep a soft cap.
    raw_soot = float(scalars.get("SootMultiply", 1.0) or 1.0)
    soot_mul = min(max(raw_soot * 0.1, 0.0), 0.35)
    dirt_scale = nodes.new("ShaderNodeMath")
    dirt_scale.operation = 'MULTIPLY'
    dirt_scale.label = "SootMultiply"
    dirt_scale.location = (col_mix - 200, row_y - 160)
    dirt_scale.inputs[1].default_value = soot_mul
    links.new(dirt_max.outputs[0], dirt_scale.inputs[0])

    dirt_clamp = nodes.new("ShaderNodeMath")
    dirt_clamp.operation = 'MINIMUM'
    dirt_clamp.label = "Clamp Dirt"
    dirt_clamp.location = (col_mix, row_y - 160)
    dirt_clamp.inputs[1].default_value = 1.0
    links.new(dirt_scale.outputs[0], dirt_clamp.inputs[0])

    base_sock = principled.inputs["Base Color"]
    base_from = base_sock.links[0].from_socket if base_sock.links else None
    soot_color = nodes.new("ShaderNodeRGB")
    soot_color.label = "Soot Color"
    soot_color.outputs[0].default_value = (0.012, 0.004, 0.001, 1.0)  # deep dark brown #302117
    soot_color.location = (col_mix, row_y - 360)

    dirt_albedo = nodes.new("ShaderNodeMix")
    dirt_albedo.data_type = 'RGBA'
    dirt_albedo.blend_type = 'MIX'
    dirt_albedo.label = "Dirt → Base Color"
    dirt_albedo.location = (col_mix + 250, row_y - 120)
    links.new(dirt_clamp.outputs[0], dirt_albedo.inputs["Factor"])
    links.new(soot_color.outputs[0], dirt_albedo.inputs[7])
    if base_from is not None:
        _unlink_input(links, base_sock)
        links.new(base_from, dirt_albedo.inputs[6])
    else:
        dirt_albedo.inputs[6].default_value = (0.5, 0.5, 0.5, 1.0)
    links.new(dirt_albedo.outputs[2], base_sock)

    rough_sock = principled.inputs["Roughness"]
    rough_from = rough_sock.links[0].from_socket if rough_sock.links else None
    dirt_rough = nodes.new("ShaderNodeMix")
    dirt_rough.data_type = 'FLOAT'
    dirt_rough.blend_type = 'MIX'
    dirt_rough.label = "Dirt → Roughness"
    dirt_rough.location = (col_mix + 250, row_y - 340)
    links.new(dirt_clamp.outputs[0], dirt_rough.inputs["Factor"])
    dirt_rough.inputs[3].default_value = 1.0
    if rough_from is not None:
        _unlink_input(links, rough_sock)
        links.new(rough_from, dirt_rough.inputs[2])
    else:
        dirt_rough.inputs[2].default_value = 0.5
    links.new(dirt_rough.outputs[0], rough_sock)


def _apply_wear_map(nodes, links, principled, wear_img, scalars: dict,
                    col_tex=-1600, col_util=-1100, col_mix=-400, row_y=-900):
    """Wire packed Wear maps for localized scuffs / oxidation.

    Wear RGBA channel roles:
      - RGB = wear detail (desaturated before blend to avoid vivid hue shift)
      - R   = main edge-wear mask (contrast-remapped so only hotspots apply)
      - B   = finer scratch marks
      - Alpha = roughness variation

    Three labelled Value nodes act as easy strength controls:
      "Wear Scuff Strength"    - how much desaturated wear detail overlays albedo
      "Wear Hotspot Strength"  - extra reveal at true edge hotspots
      "Wear Roughness Strength"- how much roughness varies with wear alpha
    """
    wear_img.colorspace_settings.name = "Non-Color"
    wear_node = nodes.new("ShaderNodeTexImage")
    wear_node.image = wear_img
    wear_node.label = "Wear"
    wear_node.interpolation = "Cubic"
    wear_node.location = (col_tex, row_y)

    sep = nodes.new("ShaderNodeSeparateColor")
    sep.label = "Wear Channels"
    sep.location = (col_util, row_y)
    links.new(wear_node.outputs["Color"], sep.inputs["Color"])

    # Desaturate Wear RGB → luminance-only so no vivid blue/purple/green leaks through
    desat = nodes.new("ShaderNodeMix")
    desat.data_type = 'RGBA'
    desat.blend_type = 'MIX'
    desat.label = "Wear Desaturate"
    desat.location = (col_util, row_y - 240)
    desat.inputs["Factor"].default_value = 1.0
    # Use hue-saturation to strip colour from wear detail
    huesat = nodes.new("ShaderNodeHueSaturation")
    huesat.label = "Wear Colour Strip"
    huesat.location = (col_util, row_y - 440)
    huesat.inputs["Saturation"].default_value = 0.05   # near-zero keeps subtle tone
    huesat.inputs["Value"].default_value = 0.85
    links.new(wear_node.outputs["Color"], huesat.inputs["Color"])
    # Resulting desaturated wear colour
    wear_grey = huesat.outputs["Color"]

    # Crush midtones: only bright wear hotspots (scuffs/edges) contribute
    wear_mask = nodes.new("ShaderNodeMapRange")
    wear_mask.label = "Wear Mask Contrast"
    wear_mask.location = (col_util + 280, row_y + 60)
    wear_mask.clamp = True
    wear_mask.inputs["From Min"].default_value = 0.55
    wear_mask.inputs["From Max"].default_value = 0.92
    wear_mask.inputs["To Min"].default_value = 0.0
    wear_mask.inputs["To Max"].default_value = 1.0
    links.new(sep.outputs["Red"], wear_mask.inputs["Value"])

    scratch_mask = nodes.new("ShaderNodeMapRange")
    scratch_mask.label = "Scratch Mask Contrast"
    scratch_mask.location = (col_util + 280, row_y - 160)
    scratch_mask.clamp = True
    scratch_mask.inputs["From Min"].default_value = 0.45
    scratch_mask.inputs["From Max"].default_value = 0.85
    scratch_mask.inputs["To Min"].default_value = 0.0
    scratch_mask.inputs["To Max"].default_value = 1.0
    links.new(sep.outputs["Blue"], scratch_mask.inputs["Value"])

    mask_max = nodes.new("ShaderNodeMath")
    mask_max.operation = 'MAXIMUM'
    mask_max.label = "Wear ∪ Scratches"
    mask_max.location = (col_mix - 200, row_y)
    links.new(wear_mask.outputs["Result"], mask_max.inputs[0])
    links.new(scratch_mask.outputs["Result"], mask_max.inputs[1])

    # ── Strength control nodes (easy to tweak) ──────────────────────────────
    scuff_str = nodes.new("ShaderNodeValue")
    scuff_str.label = "Wear Scuff Strength"
    scuff_str.location = (col_mix - 200, row_y - 180)
    scuff_str.outputs[0].default_value = 0.18

    hotspot_str = nodes.new("ShaderNodeValue")
    hotspot_str.label = "Wear Hotspot Strength"
    hotspot_str.location = (col_mix - 200, row_y - 310)
    hotspot_str.outputs[0].default_value = 0.30

    rough_str = nodes.new("ShaderNodeValue")
    rough_str.label = "Wear Roughness Strength"
    rough_str.location = (col_mix - 200, row_y - 440)
    rough_str.outputs[0].default_value = 0.40

    # Scale mask by scuff strength
    scuff_fac = nodes.new("ShaderNodeMath")
    scuff_fac.operation = 'MULTIPLY'
    scuff_fac.label = "Scuff Factor"
    scuff_fac.location = (col_mix, row_y - 60)
    links.new(mask_max.outputs[0], scuff_fac.inputs[0])
    links.new(scuff_str.outputs[0], scuff_fac.inputs[1])

    scuff_clamp = nodes.new("ShaderNodeMath")
    scuff_clamp.operation = 'MINIMUM'
    scuff_clamp.location = (col_mix + 180, row_y - 60)
    scuff_clamp.inputs[1].default_value = 1.0
    links.new(scuff_fac.outputs[0], scuff_clamp.inputs[0])

    base_sock = principled.inputs["Base Color"]
    base_from = base_sock.links[0].from_socket if base_sock.links else None

    # Overlay desaturated wear detail (oxidized/scratched look, not vivid)
    wear_overlay = nodes.new("ShaderNodeMix")
    wear_overlay.data_type = 'RGBA'
    wear_overlay.blend_type = 'OVERLAY'
    wear_overlay.label = "Wear Scuff Overlay"
    wear_overlay.location = (col_mix + 420, row_y + 40)
    links.new(scuff_clamp.outputs[0], wear_overlay.inputs["Factor"])
    links.new(wear_grey, wear_overlay.inputs[7])
    if base_from is not None:
        _unlink_input(links, base_sock)
        links.new(base_from, wear_overlay.inputs[6])
    else:
        wear_overlay.inputs[6].default_value = (0.5, 0.5, 0.5, 1.0)

    # Hotspot threshold — only the brightest wear areas get a slightly stronger reveal
    hotspot = nodes.new("ShaderNodeMapRange")
    hotspot.label = "Hotspot Mask"
    hotspot.location = (col_mix + 180, row_y - 280)
    hotspot.clamp = True
    hotspot.inputs["From Min"].default_value = 0.72
    hotspot.inputs["From Max"].default_value = 1.0
    hotspot.inputs["To Min"].default_value = 0.0
    hotspot.inputs["To Max"].default_value = 1.0
    links.new(mask_max.outputs[0], hotspot.inputs["Value"])

    hs_fac = nodes.new("ShaderNodeMath")
    hs_fac.operation = 'MULTIPLY'
    hs_fac.label = "Hotspot Factor"
    hs_fac.location = (col_mix + 360, row_y - 280)
    links.new(hotspot.outputs["Result"], hs_fac.inputs[0])
    links.new(hotspot_str.outputs[0], hs_fac.inputs[1])

    hs_clamp = nodes.new("ShaderNodeMath")
    hs_clamp.operation = 'MINIMUM'
    hs_clamp.location = (col_mix + 540, row_y - 280)
    hs_clamp.inputs[1].default_value = 1.0
    links.new(hs_fac.outputs[0], hs_clamp.inputs[0])

    wear_mix = nodes.new("ShaderNodeMix")
    wear_mix.data_type = 'RGBA'
    wear_mix.blend_type = 'MIX'
    wear_mix.label = "Wear Hotspots → Albedo"
    wear_mix.location = (col_mix + 680, row_y + 40)
    links.new(hs_clamp.outputs[0], wear_mix.inputs["Factor"])
    links.new(wear_overlay.outputs[2], wear_mix.inputs[6])
    links.new(wear_grey, wear_mix.inputs[7])
    links.new(wear_mix.outputs[2], base_sock)

    metal_sock = principled.inputs.get("Metallic")
    if metal_sock is not None:
        metal_from = metal_sock.links[0].from_socket if metal_sock.links else None
        wear_metal = nodes.new("ShaderNodeMix")
        wear_metal.data_type = 'FLOAT'
        wear_metal.blend_type = 'MIX'
        wear_metal.label = "Wear → Metallic"
        wear_metal.location = (col_mix + 680, row_y - 220)
        links.new(hs_clamp.outputs[0], wear_metal.inputs["Factor"])
        wear_metal.inputs[3].default_value = 0.85
        if metal_from is not None:
            _unlink_input(links, metal_sock)
            links.new(metal_from, wear_metal.inputs[2])
        else:
            wear_metal.inputs[2].default_value = 0.0
        links.new(wear_metal.outputs[0], metal_sock)

    rough_sock = principled.inputs["Roughness"]
    rough_from = rough_sock.links[0].from_socket if rough_sock.links else None
    alpha_mask = nodes.new("ShaderNodeMapRange")
    alpha_mask.label = "Wear Alpha Contrast"
    alpha_mask.location = (col_mix + 420, row_y - 480)
    alpha_mask.clamp = True
    alpha_mask.inputs["From Min"].default_value = 0.35
    alpha_mask.inputs["From Max"].default_value = 0.9
    alpha_mask.inputs["To Min"].default_value = 0.0
    alpha_mask.inputs["To Max"].default_value = 1.0
    links.new(wear_node.outputs["Alpha"], alpha_mask.inputs["Value"])

    rough_fac = nodes.new("ShaderNodeMath")
    rough_fac.operation = 'MULTIPLY'
    rough_fac.label = "Roughness Factor"
    rough_fac.location = (col_mix + 600, row_y - 480)
    links.new(alpha_mask.outputs["Result"], rough_fac.inputs[0])
    links.new(rough_str.outputs[0], rough_fac.inputs[1])

    wear_rough = nodes.new("ShaderNodeMix")
    wear_rough.data_type = 'FLOAT'
    wear_rough.blend_type = 'MIX'
    wear_rough.label = "Wear → Roughness"
    wear_rough.location = (col_mix + 780, row_y - 480)
    links.new(rough_fac.outputs[0], wear_rough.inputs["Factor"])
    wear_rough.inputs[3].default_value = 0.75
    if rough_from is not None:
        _unlink_input(links, rough_sock)
        links.new(rough_from, wear_rough.inputs[2])
    else:
        wear_rough.inputs[2].default_value = 0.5
    links.new(wear_rough.outputs[0], rough_sock)



def _setup_weapon_main_material(mat, mi_path: str, psk_path: str):
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    # Layout columns (left → right): textures, utils, mixes, BSDF, output
    COL_TEX, COL_UTIL, COL_MIX, COL_BSDF, COL_OUT = -1600, -1100, -450, 400, 750
    Y_CR, Y_N, Y_ID, Y_TINT, Y_WEAR, Y_EXX, Y_EXTRA = 700, 250, -200, 450, -750, -1400, -2000
    scalars = mi.get("scalars") or {}

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path)):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    # Last-resort: pick CR/NXM/etc. directly from the mesh folder if ObjectPath resolve failed
    if not tex_lookup and local_folders:
        try:
            for folder in local_folders:
                for fname in sorted(os.listdir(folder)):
                    fl = fname.lower()
                    if not fl.endswith(".png"):
                        continue
                    for key in ("cr", "nxm", "nom", "nem", "nam", "id", "wear", "exx", "ex"):
                        if fl.endswith(f"_{key}.png"):
                            fpath = os.path.join(folder, fname)
                            img = _load_image_cached(fpath)
                            tex_lookup[key.upper() if key != "id" else "Weapon ID"] = (fpath, img)
                            break
        except OSError:
            pass

    psk_stem = os.path.splitext(os.path.basename(psk_path))[0]
    weapon_stem = re.sub(r'_LOD\d+$', '', psk_stem, flags=re.IGNORECASE)
    weapon_stem = re.sub(r'^SK_', '', weapon_stem, flags=re.IGNORECASE)
    weapon_stem_base = re.sub(r'_[A-Z]$', '', weapon_stem, flags=re.IGNORECASE)

    def find_weapon_stem_tex(suffix):
        for stem in (weapon_stem.lower(), weapon_stem_base.lower()):
            target = f"t_{stem}_{suffix.lower()}"
            for param, (fpath, img) in tex_lookup.items():
                if os.path.splitext(os.path.basename(fpath))[0].lower() == target:
                    return param, img
        return None, None

    cr_node = None
    _, cr_img = _find_flat_tex(tex_lookup, "CR", "cr")
    if not cr_img:
        _, cr_img = find_weapon_stem_tex("cr")
    if cr_img:
        cr_node = nodes.new("ShaderNodeTexImage")
        cr_node.image = cr_img
        cr_node.label = "CR (Colour/Roughness)"
        cr_node.interpolation = "Cubic"
        cr_node.location = (COL_TEX, Y_CR)
        links.new(cr_node.outputs["Alpha"], principled.inputs["Roughness"])

    normal_type = None
    normal_img = None
    for ntype in ("nxm", "nom", "nem", "nam"):
        _, img = _find_flat_tex(tex_lookup, ntype.upper(), ntype)
        if not img:
            _, img = find_weapon_stem_tex(ntype)
        if img:
            normal_type = ntype
            normal_img = img
            break

    if normal_img:
        normal_img.colorspace_settings.name = "Non-Color"
        normal_node = nodes.new("ShaderNodeTexImage")
        normal_node.image = normal_img
        normal_node.label = normal_type.upper()
        normal_node.interpolation = "Cubic"
        normal_node.location = (COL_TEX, Y_N)
        if utils.ensure_node_group("NormalFlipper"):
            flipper = nodes.new("ShaderNodeGroup")
            flipper.node_tree = bpy.data.node_groups["NormalFlipper"]
            flipper.location = (COL_UTIL, Y_N)
            links.new(normal_node.outputs["Color"], flipper.inputs[0])
            nm_in = flipper.outputs[0]
        else:
            nm_in = normal_node.outputs["Color"]
        nm_node = nodes.new("ShaderNodeNormalMap")
        nm_node.location = (COL_MIX, Y_N)
        try:
            nm_node.convention = 'DIRECTX'
        except Exception:
            pass
        links.new(nm_in, nm_node.inputs["Color"])
        links.new(nm_node.outputs["Normal"], principled.inputs["Normal"])
        if normal_type != "nom":
            links.new(normal_node.outputs["Alpha"], principled.inputs["Metallic"])

        if normal_type in ("nom", "nem", "nam"):
            sep_node = nodes.new("ShaderNodeSeparateColor")
            sep_node.location = (COL_UTIL, Y_N - 280)
            links.new(normal_node.outputs["Color"], sep_node.inputs["Color"])
            if normal_type == "nom":
                mul_node = nodes.new("ShaderNodeMix")
                mul_node.data_type = 'RGBA'
                mul_node.blend_type = 'MULTIPLY'
                mul_node.location = (COL_MIX, Y_N + 220)
                mul_node.inputs["Factor"].default_value = 1.0
                if cr_node:
                    links.new(cr_node.outputs["Color"], mul_node.inputs[6])
                links.new(sep_node.outputs["Blue"], mul_node.inputs[7])
                links.new(mul_node.outputs[2], principled.inputs["Base Color"])

                metal_mix = nodes.new("ShaderNodeMix")
                metal_mix.data_type = 'FLOAT'
                metal_mix.blend_type = 'MIX'
                metal_mix.label = "NOM Alpha → Metallic (Blue)"
                metal_mix.location = (COL_MIX, Y_N - 180)
                links.new(normal_node.outputs["Alpha"], metal_mix.inputs["Factor"])
                links.new(sep_node.outputs["Blue"], metal_mix.inputs[3])
                links.new(metal_mix.outputs[0], principled.inputs["Metallic"])
            elif normal_type == "nem":
                links.new(sep_node.outputs["Blue"], principled.inputs["Emission Strength"])
                if cr_node:
                    links.new(cr_node.outputs["Color"], principled.inputs["Emission Color"])
                    links.new(cr_node.outputs["Color"], principled.inputs["Base Color"])
            elif normal_type == "nam":
                links.new(sep_node.outputs["Blue"], principled.inputs["Alpha"])
                if cr_node:
                    links.new(cr_node.outputs["Color"], principled.inputs["Base Color"])

    id_node = None
    _, id_img = _find_flat_tex(tex_lookup, "Weapon ID", "ID", "id")
    if not id_img:
        _, id_img = find_weapon_stem_tex("id")
    if id_img:
        id_img.colorspace_settings.name = "Non-Color"
        id_node = nodes.new("ShaderNodeTexImage")
        id_node.image = id_img
        id_node.label = "ID Map"
        id_node.interpolation = "Cubic"
        id_node.location = (COL_TEX, Y_ID)

    COLOUR_CHANNELS = {
        'Color - Red': 0, 'Color - Green': 1,
        'Color - Blue': 2, 'Color - Alpha': 3,
    }
    MASK_SWITCHES = {
        'Color - Red': 'Enable Red Mask', 'Color - Green': 'Enable Green Mask',
        'Color - Blue': 'Enable Blue Mask', 'Color - Alpha': 'Enable Alpha Mask',
    }
    switches = mi.get('switches', {})
    zone_colours = []
    for param, rgba in mi['colours']:
        ch_key = param
        for short in COLOUR_CHANNELS:
            if param.endswith(short) or param == short:
                ch_key = short
                break
        else:
            continue
        switch_name = MASK_SWITCHES.get(ch_key, '')
        if switch_name and not switches.get(switch_name, True):
            continue
        zone_colours.append((COLOUR_CHANNELS[ch_key], rgba, param))

    base_color_linked = False
    if cr_node and zone_colours:
        sep_node = None
        if id_node:
            sep_node = nodes.new("ShaderNodeSeparateColor")
            sep_node.label = "ID Channels"
            sep_node.location = (COL_UTIL, Y_ID)
            links.new(id_node.outputs["Color"], sep_node.inputs["Color"])

        ch_names = {0: "Red", 1: "Green", 2: "Blue"}
        current_out = cr_node.outputs["Color"]
        mul_x = COL_MIX
        for i, (ch_idx, rgba, param) in enumerate(zone_colours):
            rgb_node = nodes.new("ShaderNodeRGB")
            rgb_node.label = param
            rgb_node.outputs[0].default_value = rgba
            rgb_node.location = (mul_x - 220, Y_TINT - 280 - i * 200)
            mul_node = nodes.new("ShaderNodeMix")
            mul_node.data_type = 'RGBA'
            mul_node.blend_type = 'MULTIPLY'
            mul_node.label = f"Tint {param}"
            mul_node.location = (mul_x, Y_TINT - i * 200)
            mul_node.inputs["Factor"].default_value = 1.0
            links.new(current_out, mul_node.inputs[6])
            links.new(rgb_node.outputs[0], mul_node.inputs[7])
            if sep_node:
                if ch_idx in ch_names and ch_names[ch_idx] in sep_node.outputs:
                    links.new(sep_node.outputs[ch_names[ch_idx]], mul_node.inputs["Factor"])
                elif ch_idx == 3 and id_node:
                    links.new(id_node.outputs["Alpha"], mul_node.inputs["Factor"])
            current_out = mul_node.outputs[2]
            mul_x += 280
        links.new(current_out, principled.inputs["Base Color"])
        base_color_linked = True
    elif cr_node and normal_type not in ("nom", "nem", "nam"):
        links.new(cr_node.outputs["Color"], principled.inputs["Base Color"])
        base_color_linked = True
    elif cr_node and not base_color_linked and normal_type is None:
        links.new(cr_node.outputs["Color"], principled.inputs["Base Color"])

    # Wear then EXX so each can insert into the albedo/roughness chain
    _, wear_img = _find_flat_tex(tex_lookup, "Wear", "wear")
    if not wear_img:
        _, wear_img = find_weapon_stem_tex("wear")
    if wear_img:
        _apply_wear_map(
            nodes, links, principled, wear_img, scalars,
            col_tex=COL_TEX, col_util=COL_UTIL, col_mix=COL_MIX, row_y=Y_WEAR,
        )

    _, ex_img = _find_flat_tex(tex_lookup, "EX", "EXX", "ex", "exx")
    if not ex_img:
        _, ex_img = find_weapon_stem_tex("exx")
    if not ex_img:
        _, ex_img = find_weapon_stem_tex("ex")
    if ex_img:
        _apply_exx_packing(
            nodes, links, principled, ex_img, scalars,
            col_tex=COL_TEX, col_util=COL_UTIL, col_mix=COL_MIX, row_y=Y_EXX,
        )

    handled = {node.image.filepath for node in nodes if hasattr(node, 'image') and node.image}
    row_u = Y_EXTRA
    for param, (fpath, img) in tex_lookup.items():
        if fpath in handled:
            continue
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param
        node.interpolation = "Cubic"
        node.location = (COL_TEX - 400, row_u)
        row_u -= 320

    colour_channel_ends = tuple(COLOUR_CHANNELS.keys())
    for j, (param, rgba) in enumerate(
        [(p, r) for p, r in mi['colours']
         if p not in COLOUR_CHANNELS and not any(p.endswith(k) for k in colour_channel_ends)]
    ):
        rgb_node = nodes.new("ShaderNodeRGB")
        rgb_node.label = param
        rgb_node.outputs[0].default_value = rgba
        rgb_node.location = (COL_OUT + 200 + (j % 2) * 240, 200 - (j // 2) * 200)


def _setup_weapon_emissive_material(mat, mi_path: str):
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (400, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (700, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    light_color = next((rgba for p, rgba in mi['colours'] if 'lightcolor' in p.lower()), None)
    try:
        with open(mi_path, 'r', encoding='utf-8') as fh:
            raw = json.load(fh)
        if isinstance(raw, list):
            raw = raw[0]
        light_intensity = raw.get('Parameters', {}).get('Scalars', {}).get('LightIntensity', 25.0)
    except Exception:
        light_intensity = 25.0

    if light_color:
        rgb = nodes.new("ShaderNodeRGB")
        rgb.label = "Light Color"
        rgb.outputs[0].default_value = light_color
        rgb.location = (0, 120)
        links.new(rgb.outputs[0], principled.inputs["Emission Color"])
        principled.inputs["Emission Strength"].default_value = light_intensity


def _setup_enemy_decal_material(mat, mi_path: str, psk_path: str):
    """Masked enemy Decals slot: NAO normals + Height bump + alpha clip.

    UE uses M_EnemyPreset_Masked_NAO+Detail on a slightly offset shell mesh.
    Without CLIP alpha the shell reads as floating above the body. Colour
    tint layers (V0/V1/V2) are left for a follow-up.
    """
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    _set_material_clip(mat, mi.get("opacity_clip", 0.3333))

    COL_TEX, COL_UTIL, COL_MIX, COL_BSDF, COL_OUT = -1400, -900, -350, 350, 650
    scalars = mi.get("scalars") or {}

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    # Neutral metal until V0/V1/V2 vertex-tint wiring lands
    base_rgba = (0.55, 0.55, 0.55, 1.0)
    for param, rgba in mi.get("colours") or []:
        if param.lower() in ("v0_color", "v0 colour"):
            base_rgba = rgba
            break
    principled.inputs["Base Color"].default_value = base_rgba

    metal = float(scalars.get("V0_Metalness", 0.5) or 0.5)
    principled.inputs["Metallic"].default_value = min(max(metal, 0.0), 1.0)
    rough = float(scalars.get("V1_Roughness", scalars.get("V0_Roughness", 0.55)) or 0.55)
    # UE roughness scalars often exceed 1; keep a usable Principled range
    principled.inputs["Roughness"].default_value = min(max(rough / 4.0 if rough > 1.0 else rough, 0.05), 1.0)

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path)):
        if folder and folder not in local_folders:
            local_folders.append(folder)

    # Enemy decal textures live in MaterialLibrary/Textures/Enemies, not next to
    # the PSK. Add that folder as a local search path so ObjectPath-less resolves work.
    try:
        pioneer_root = utils.get_pioneer_root()
        content_dir = utils.find_content_dir(pioneer_root) if pioneer_root else ""
        if content_dir:
            mat_lib_enemies = os.path.join(
                content_dir, "Pioneer", "MaterialLibrary", "Textures", "Enemies"
            )
            if os.path.isdir(mat_lib_enemies) and mat_lib_enemies not in local_folders:
                local_folders.append(mat_lib_enemies)
    except Exception:
        pass

    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    # If ObjectPath resolve still failed, scan the MaterialLibrary enemies folder
    # for enemy decal atlases only (never generic "*decals*" — that pulled the
    # wrong atlas onto env NAO trims).
    if not any("_nao" in os.path.basename(fp).lower() for _, (fp, _) in tex_lookup.items()):
        try:
            for folder in local_folders:
                for fname in os.listdir(folder):
                    fl = fname.lower()
                    if fl.endswith(".png") and "enemydecals" in fl:
                        fpath = os.path.join(folder, fname)
                        img = bpy.data.images.load(fpath, check_existing=True)
                        key = os.path.splitext(fname)[0]
                        if key not in tex_lookup:
                            tex_lookup[key] = (fpath, img)
        except OSError:
            pass

    _, nao_img = _find_flat_tex(
        tex_lookup, "DecalTrimsheet", "NAO", "nao",
    )
    if not nao_img:
        for param, (_fpath, img) in tex_lookup.items():
            stem = os.path.splitext(os.path.basename(_fpath))[0].lower()
            if stem.endswith("_nao") or ("enemydecals" in stem and "nao" in stem):
                nao_img = img
                break

    _, height_img = _find_flat_tex(
        tex_lookup, "Height/HX", "Height", "HX", "hx", "H", "h",
    )
    if not height_img:
        for param, (_fpath, img) in tex_lookup.items():
            stem = os.path.splitext(os.path.basename(_fpath))[0].lower()
            if stem.endswith("_h") or stem.endswith("_hx") or "height" in param.lower():
                height_img = img
                break

    normal_out = None
    nao_node = None
    if nao_img:
        nao_img.colorspace_settings.name = "Non-Color"
        nao_node = nodes.new("ShaderNodeTexImage")
        nao_node.image = nao_img
        nao_node.label = "NAO (N + AO + Opacity)"
        nao_node.interpolation = "Cubic"
        nao_node.location = (COL_TEX, 200)

        # RG = tangent normal; B = AO (not Z); A = opacity mask
        sep = nodes.new("ShaderNodeSeparateColor")
        sep.label = "NAO Channels"
        sep.location = (COL_UTIL, 200)
        links.new(nao_node.outputs["Color"], sep.inputs["Color"])

        combine = nodes.new("ShaderNodeCombineColor")
        combine.label = "Normal RG + Z=1"
        combine.location = (COL_UTIL + 220, 280)
        links.new(sep.outputs["Red"], combine.inputs["Red"])
        links.new(sep.outputs["Green"], combine.inputs["Green"])
        combine.inputs["Blue"].default_value = 1.0

        if utils.ensure_node_group("NormalFlipper"):
            flipper = nodes.new("ShaderNodeGroup")
            flipper.node_tree = bpy.data.node_groups["NormalFlipper"]
            flipper.location = (COL_MIX - 200, 280)
            links.new(combine.outputs["Color"], flipper.inputs[0])
            nm_in = flipper.outputs[0]
        else:
            nm_in = combine.outputs["Color"]

        nm_node = nodes.new("ShaderNodeNormalMap")
        nm_node.label = "Decal Normal"
        nm_node.location = (COL_MIX, 280)
        try:
            nm_node.convention = 'DIRECTX'
        except Exception:
            pass
        links.new(nm_in, nm_node.inputs["Color"])
        normal_out = nm_node.outputs["Normal"]

        links.new(nao_node.outputs["Alpha"], principled.inputs["Alpha"])

        ao_mul = nodes.new("ShaderNodeMix")
        ao_mul.data_type = 'RGBA'
        ao_mul.blend_type = 'MULTIPLY'
        ao_mul.label = "AO → Base"
        ao_mul.location = (COL_MIX, 40)
        ao_mul.inputs["Factor"].default_value = 0.65
        ao_mul.inputs[6].default_value = base_rgba
        ao_rgb = nodes.new("ShaderNodeCombineColor")
        ao_rgb.label = "AO Gray"
        ao_rgb.location = (COL_UTIL + 220, 40)
        links.new(sep.outputs["Blue"], ao_rgb.inputs["Red"])
        links.new(sep.outputs["Blue"], ao_rgb.inputs["Green"])
        links.new(sep.outputs["Blue"], ao_rgb.inputs["Blue"])
        links.new(ao_rgb.outputs["Color"], ao_mul.inputs[7])
        links.new(ao_mul.outputs[2], principled.inputs["Base Color"])

    if height_img:
        height_img.colorspace_settings.name = "Non-Color"
        h_node = nodes.new("ShaderNodeTexImage")
        h_node.image = height_img
        h_node.label = "Height / HX"
        h_node.interpolation = "Cubic"
        h_node.location = (COL_TEX, -250)

        bump = nodes.new("ShaderNodeBump")
        bump.label = "Decal Height Bump"
        bump.location = (COL_MIX, -120)
        height_ratio = float(scalars.get("HeightRatio", 0.1) or 0.1)
        bump.inputs["Strength"].default_value = min(max(height_ratio, 0.02), 0.35)
        bump.inputs["Distance"].default_value = 0.05
        links.new(h_node.outputs["Color"], bump.inputs["Height"])
        if normal_out is not None:
            links.new(normal_out, bump.inputs["Normal"])
        links.new(bump.outputs["Normal"], principled.inputs["Normal"])
    elif normal_out is not None:
        links.new(normal_out, principled.inputs["Normal"])

    # Leftover textures (unconnected) for inspection — skip already wired
    handled = {node.image.filepath for node in nodes if getattr(node, "image", None)}
    row_u = -550
    for param, (fpath, img) in tex_lookup.items():
        if fpath in handled:
            continue
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param
        node.interpolation = "Cubic"
        node.location = (COL_TEX - 350, row_u)
        row_u -= 300


def _scan_display_led_resolution(scalars: dict) -> tuple:
    """LED grid size for ScanDisplay UV scale. Defaults 64×48 (user-tuned crop)."""
    def _pick(*names, default):
        for name in names:
            if name in scalars and scalars[name] is not None:
                try:
                    val = float(scalars[name])
                except (TypeError, ValueError):
                    continue
                if val > 0.0:
                    return val
        return float(default)

    led_w = _pick(
        "LEDWidth", "Columns", "TilingX", "ResolutionX", "ScanColumns",
        default=64.0,
    )
    led_h = _pick(
        "LEDHeight", "Rows", "TilingY", "ResolutionY", "ScanRows",
        default=48.0,
    )
    # Single Tiling scalar → square grid when no explicit X/Y was set
    if (
        "Tiling" in scalars
        and not any(k in scalars for k in (
            "LEDWidth", "Columns", "TilingX", "ResolutionX", "ScanColumns",
            "LEDHeight", "Rows", "TilingY", "ResolutionY", "ScanRows",
        ))
    ):
        try:
            t = float(scalars["Tiling"])
            if t > 0.0:
                led_w = led_h = t
        except (TypeError, ValueError):
            pass
    return max(led_w, 1.0), max(led_h, 1.0)


def _drive_scan_frame_value(value_node, scan_speed: float):
    """Ping-pong 0↔1 from scene frame without requiring Auto Run Python Scripts.

    Uses a SINGLE_PROP variable on Scene.frame_current instead of the builtin
    ``frame`` name alone (which is blocked when Auto Run is off).
    """
    value_node.outputs[0].default_value = 0.0
    # Clear any stale drivers from a prior import into the same material.
    try:
        value_node.outputs[0].driver_remove("default_value")
    except (TypeError, AttributeError):
        pass

    # 2.5× prior speed (was 0.015) so the ping-pong reads clearly in the viewport.
    spd = max(float(scan_speed), 0.05) * 0.0375
    fcurve = value_node.outputs[0].driver_add("default_value")
    driver = fcurve.driver
    driver.type = "SCRIPTED"
    driver.expression = f"abs((frame*{spd:.5f})%2.0 - 1.0)"

    # Drop any auto-created vars, then bind frame → scene.frame_current.
    while driver.variables:
        driver.variables.remove(driver.variables[0])
    var = driver.variables.new()
    var.name = "frame"
    var.type = "SINGLE_PROP"
    target = var.targets[0]
    target.id_type = "SCENE"
    scene = bpy.context.scene
    target.id = scene
    target.data_path = "frame_current"

    # Nudge evaluation so viewport scrubbing works immediately after import.
    try:
        value_node.id_data.update_tag()
    except Exception:
        pass
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    try:
        # Seed current value so the node isn't stuck at 0 until the next depsgraph tick.
        frame_now = float(scene.frame_current)
        value_node.outputs[0].default_value = abs((frame_now * spd) % 2.0 - 1.0)
    except Exception:
        pass


def _hide_scan_node_previews(*node_list):
    """Collapse bulky white preview boxes on math / ramp nodes."""
    for n in node_list:
        try:
            n.hide_preview = True
        except Exception:
            pass
        try:
            if hasattr(n, "show_preview"):
                n.show_preview = False
        except Exception:
            pass


def _setup_enemy_scan_display_material(mat, mi_path: str):
    """Arc ScanDisplay: 64×48 LED panel, horizontal ping-pong scan.

    Clean left→right columns (Mapping style):
      UV → LED Mapping → Snap → |col−scan| MapRange → LED gaps → tint×band → Emission

    Off / idle LEDs are black; only the scan band is tinted.
    """
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    scalars = mi.get("scalars") or {}
    switches = mi.get("switches") or {}
    led_w, led_h = _scan_display_led_resolution(scalars)

    # Even column spacing — no overlaps
    C0, C1, C2, C3, C4, C5, C6 = -1200, -900, -560, -220, 140, 480, 820

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (C6, 40)
    principled.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    principled.inputs["Metallic"].default_value = 0.0
    principled.inputs["Roughness"].default_value = 0.35
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (C6 + 280, 40)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    scan_speed = float(scalars.get("ScanSpeed", 0.5) or 0.5)
    alert = min(max(float(scalars.get("AlertnessState", 0.0) or 0.0), 0.0), 1.0)
    do_blink = bool(switches.get("Blink", False))
    do_glitch = bool(switches.get("Glitch Effect", switches.get("Glitch", False)))
    glitch_amt = float(scalars.get("GlitchAmount", 0.0) or 0.0)

    calm = (0.15, 0.85, 1.0, 1.0)
    alert_c = (1.0, 0.28, 0.05, 1.0)
    tint = (
        calm[0] * (1.0 - alert) + alert_c[0] * alert,
        calm[1] * (1.0 - alert) + alert_c[1] * alert,
        calm[2] * (1.0 - alert) + alert_c[2] * alert,
        1.0,
    )

    # ── Col 0: UV + Frame ──────────────────────────────────────────────
    texcoord = nodes.new("ShaderNodeTexCoord")
    texcoord.location = (C0, 120)

    frame = nodes.new("ShaderNodeValue")
    frame.name = "ScanFrame"
    frame.label = "Frame"
    frame.location = (C0, -160)
    try:
        _drive_scan_frame_value(frame, scan_speed)
    except Exception as e:
        frame.outputs[0].default_value = 0.0
        print(f"Arc Raiders PSK Importer: ScanDisplay frame driver failed: {e}")

    # ── Col 1: LED Mapping + Scan Col ──────────────────────────────────
    led_map = nodes.new("ShaderNodeMapping")
    led_map.label = f"LED {int(led_w)}×{int(led_h)}"
    led_map.location = (C1, 140)
    led_map.inputs["Scale"].default_value = (led_w, led_h, 1.0)
    links.new(texcoord.outputs["UV"], led_map.inputs["Vector"])

    scan_col = nodes.new("ShaderNodeMath")
    scan_col.operation = "MULTIPLY"
    scan_col.label = "Scan Col"
    scan_col.location = (C1, -160)
    scan_col.inputs[1].default_value = led_w - 1.0
    links.new(frame.outputs[0], scan_col.inputs[0])

    # ── Col 2: Snap + Separate (LED index) / Separate (raw for gaps) ───
    snap = nodes.new("ShaderNodeVectorMath")
    snap.operation = "SNAP"
    snap.label = "LED Snap"
    snap.location = (C2, 180)
    snap.inputs[1].default_value = (1.0, 1.0, 1.0)
    links.new(led_map.outputs["Vector"], snap.inputs[0])

    sep = nodes.new("ShaderNodeSeparateXYZ")
    sep.label = "LED XY"
    sep.location = (C2, -20)
    links.new(snap.outputs["Vector"], sep.inputs["Vector"])

    sep_raw = nodes.new("ShaderNodeSeparateXYZ")
    sep_raw.label = "Cell UV"
    sep_raw.location = (C2, -220)
    links.new(led_map.outputs["Vector"], sep_raw.inputs["Vector"])

    # ── Col 3: |col−scan| + Fract U/V ──────────────────────────────────
    diff = nodes.new("ShaderNodeMath")
    diff.operation = "SUBTRACT"
    diff.label = "Col − Scan"
    diff.location = (C3, 200)
    links.new(sep.outputs["X"], diff.inputs[0])
    links.new(scan_col.outputs[0], diff.inputs[1])

    adiff = nodes.new("ShaderNodeMath")
    adiff.operation = "ABSOLUTE"
    adiff.label = "|Δ Col|"
    adiff.location = (C3, 40)
    links.new(diff.outputs[0], adiff.inputs[0])

    fract_u = nodes.new("ShaderNodeMath")
    try:
        fract_u.operation = "FRACT"
    except Exception:
        fract_u.operation = "MODULO"
        fract_u.inputs[1].default_value = 1.0
    fract_u.label = "Fract U"
    fract_u.location = (C3, -140)
    links.new(sep_raw.outputs["X"], fract_u.inputs[0])

    fract_v = nodes.new("ShaderNodeMath")
    try:
        fract_v.operation = "FRACT"
    except Exception:
        fract_v.operation = "MODULO"
        fract_v.inputs[1].default_value = 1.0
    fract_v.label = "Fract V"
    fract_v.location = (C3, -300)
    links.new(sep_raw.outputs["Y"], fract_v.inputs[0])

    # ── Col 4: Scan Band (~4 columns) + LED U/V ramps ──────────────────
    band = nodes.new("ShaderNodeMapRange")
    band.label = "Scan Band"
    band.clamp = True
    band.location = (C4, 180)
    band.inputs["From Min"].default_value = 0.0
    # ~4 LED columns wide (was 2.0)
    band.inputs["From Max"].default_value = 4.0
    band.inputs["To Min"].default_value = 1.0
    band.inputs["To Max"].default_value = 0.0
    links.new(adiff.outputs[0], band.inputs["Value"])

    # ColorRamps: hard LED window on U and V (mortar ~12%)
    ramp_u = nodes.new("ShaderNodeValToRGB")
    ramp_u.label = "LED U"
    ramp_u.location = (C4, -80)
    ramp_u.color_ramp.interpolation = "CONSTANT"
    ramp_u.color_ramp.elements[0].position = 0.0
    ramp_u.color_ramp.elements[0].color = (0, 0, 0, 1)
    ramp_u.color_ramp.elements[1].position = 0.88
    ramp_u.color_ramp.elements[1].color = (0, 0, 0, 1)
    el = ramp_u.color_ramp.elements.new(0.12)
    el.color = (1, 1, 1, 1)
    links.new(fract_u.outputs[0], ramp_u.inputs["Fac"])

    ramp_v = nodes.new("ShaderNodeValToRGB")
    ramp_v.label = "LED V"
    ramp_v.location = (C4, -320)
    ramp_v.color_ramp.interpolation = "CONSTANT"
    ramp_v.color_ramp.elements[0].position = 0.0
    ramp_v.color_ramp.elements[0].color = (0, 0, 0, 1)
    ramp_v.color_ramp.elements[1].position = 0.88
    ramp_v.color_ramp.elements[1].color = (0, 0, 0, 1)
    el = ramp_v.color_ramp.elements.new(0.12)
    el.color = (1, 1, 1, 1)
    links.new(fract_v.outputs[0], ramp_v.inputs["Fac"])

    # ── Col 5: LED mask × band → grayscale → tint (scan only) ──────────
    led = nodes.new("ShaderNodeMix")
    led.data_type = "RGBA"
    led.blend_type = "MULTIPLY"
    led.label = "LED Mask"
    led.location = (C5, -120)
    led.inputs["Factor"].default_value = 1.0
    links.new(ramp_u.outputs["Color"], led.inputs[6])
    links.new(ramp_v.outputs["Color"], led.inputs[7])

    rgb2bw = nodes.new("ShaderNodeRGBToBW")
    rgb2bw.label = "LED BW"
    rgb2bw.location = (C5, 40)
    links.new(led.outputs[2], rgb2bw.inputs["Color"])

    # Scan-only mask: band × LED (no idle — off pixels stay black)
    lit = nodes.new("ShaderNodeMath")
    lit.operation = "MULTIPLY"
    lit.label = "Scan×LED"
    lit.location = (C5, 200)
    links.new(band.outputs["Result"], lit.inputs[0])
    links.new(rgb2bw.outputs["Val"], lit.inputs[1])

    tint_rgb = nodes.new("ShaderNodeRGB")
    tint_rgb.label = "Scan Tint"
    tint_rgb.outputs[0].default_value = tint
    tint_rgb.location = (C5, 360)

    # Grayscale luminance → RGB (R=G=B) so Mix multiply keeps true tint, not blue-only
    scan_rgb = nodes.new("ShaderNodeCombineColor")
    scan_rgb.label = "Scan Mask RGB"
    scan_rgb.location = (C5 + 200, 200)
    links.new(lit.outputs[0], scan_rgb.inputs["Red"])
    links.new(lit.outputs[0], scan_rgb.inputs["Green"])
    links.new(lit.outputs[0], scan_rgb.inputs["Blue"])

    emit_mul = nodes.new("ShaderNodeMix")
    emit_mul.data_type = "RGBA"
    emit_mul.blend_type = "MULTIPLY"
    emit_mul.label = "Tint × Scan"
    emit_mul.location = (C5 + 200, 360)
    emit_mul.inputs["Factor"].default_value = 1.0
    # A = tint, B = scan mask (black where band is 0) → emission black off-band
    links.new(tint_rgb.outputs[0], emit_mul.inputs[6])
    links.new(scan_rgb.outputs["Color"], emit_mul.inputs[7])
    links.new(emit_mul.outputs[2], principled.inputs["Emission Color"])

    strength = 14.0 + alert * 10.0
    if do_blink:
        strength *= 0.85
    if do_glitch and glitch_amt > 0:
        strength *= 1.0 + min(glitch_amt, 2.0) * 0.25
    if "Emission Strength" in principled.inputs:
        principled.inputs["Emission Strength"].default_value = strength

    _hide_scan_node_previews(
        scan_col, snap, sep, sep_raw, diff, adiff,
        fract_u, fract_v, band, ramp_u, ramp_v,
        led, rgb2bw, lit, scan_rgb, emit_mul,
    )


def _setup_weapon_screen_material(mat, mi_path: str):
    """Weapon screens + enemy ScanDisplay share the procedural scan setup."""
    _setup_enemy_scan_display_material(mat, mi_path)


def setup_weapon_material(obj, psk_path: str) -> int:
    """Assign per-slot materials from SK/SM SkeletalMaterials / StaticMaterials.

    Used for firearms, enemies, and hero/character SKs (e.g. Kalika Base Body)
    whose MIs live beside the mesh via ObjectPath / Materials/ siblings.
    Returns the number of Blender slots successfully wired.
    """
    slots = _parse_sk_material_slots(psk_path)
    if not slots:
        print(f"Arc Raiders PSK Importer: No SK material slots found for '{os.path.basename(psk_path)}'")
        return 0

    used_indices = set()
    wired = 0
    for idx, (slot_name, mi_stem, mi_path) in enumerate(slots):
        target_slot, slot_i = _match_material_slot(obj, slot_name, idx, used_indices, mi_stem)
        if target_slot is None:
            print(f"Arc Raiders PSK Importer: No Blender slot for '{slot_name}' (index {idx})")
            continue
        used_indices.add(slot_i)

        slot_lower = (slot_name or "").lower()

        if not mi_path:
            print(f"Arc Raiders PSK Importer: MI JSON not found for slot '{slot_name}' ({mi_stem})")
            continue

        mat = _get_or_build_shared_mi_material(mi_stem, mi_path, psk_path, slot_lower)
        if mat is None:
            print(
                "Arc Raiders PSK Importer: Failed to set up material for slot "
                f"'{slot_name}' (index {idx}, MI '{mi_stem}', path '{mi_path}')"
            )
            continue
        target_slot.material = mat
        wired += 1
    return wired


def _is_weapon_emissive_light_mi(mi_stem_lower: str) -> bool:
    """True for pure emissive/light MIs (not surface mats whose names contain 'light').

    Weapon lights are named like MI_Weapon_Emissive_Light_01 / MI_EmissiveLight_*.
    Do NOT match MI_Lightdrone_*, MI_LightningBot_*, etc. — those are full CR/NOM
    surface materials that used to be mis-routed by a naive ``'light' in stem`` check.
    """
    if not mi_stem_lower:
        return False
    if "emissive" in mi_stem_lower:
        return True
    # Token-boundary "light": _light_ / _light / light_ at stem edges — not "lightdrone"
    return bool(re.search(r"(^|_)light(_|$)", mi_stem_lower))


# ---------------------------------------------------------------------------
# Map / weapon MI family routing (Stage 2 + SK slots)
# See docs/TEXTURE_PROTOCOL.md for packing + layering rules.
# ---------------------------------------------------------------------------

# Layered building / concrete / road markers (NOT plain BaseColor foliage).
_ENV_LAYER_MARKERS = frozenset({
    "NOH", "Overlay", "Detail Normal Texture", "Detail Normal", "Detail Normal Worn",
    "CR Blend", "NOH Blend", "CR_Blend", "NOH_Blend",
    "1. CR", "1. NOH", "2. CR", "2. NOH",
    "3. Blend Mask", "4.. Overlay", "Base_Material_CR", "Base_Material_NOH",
    "Breakup_Material_CR", "Breakup_Material_NOH", "CR Breakup", "NOH Breakup",
    "Color Base", "Normal Base", "Color Top", "Normal Top",
    "CR Texture", "NXX/NMX Texture", "PaintBreakup", "Breakup Mask",
    "Breakup Mask - Linear Grayscale",
    "0. CR 1", "0. CR 2", "0. NOH 1", "0. NOH 2", "4. Mask",
    "HolesNXX", "WaterlineOverlay",
})

_FOLIAGE_TEX_MARKERS = frozenset({
    "CA", "1. CA", "ColorAlpha", "NTR", "1. NTR",
    "TrunkBaseColor", "TrunkNormal", "Trunk CA", "Trunk NTR",
})

_FOLIAGE_NAME_KEYS = (
    "foliage", "vegetation", "leaf", "leaves", "grass", "vine", "ivy",
    "plant", "tree", "bush", "fern", "moss", "reed", "heather", "flora",
)

_GLASS_TEX_MARKERS = frozenset({
    "CubemapInside", "CubemapOutside", "MaskTexture",
})

_METAL_PACKED_NORMALS = frozenset({
    "NXX/NMX Texture", "NOM", "NXM", "NMX", "NXX", "HolesNXX",
})

_SIMPLE_ALBEDO_KEYS = (
    # Hero/character flat MIs use Color (+ _Color suffix); env packs use CR/CA/C.
    "BaseColor", "Color", "CA", "1. CA", "CR", "CR Texture", "PM_Diffuse", "BC", "C",
)
_SIMPLE_NORMAL_KEYS = (
    "Normals", "Normal", "NormalMap", "NOH", "1. NTR", "NTR",
    "NXX/NMX Texture", "NOM", "NXM", "NMX", "NXX", "PM_Normals",
)
# Hero packed ORM-like map: R = Roughness, G = Metallic (B unused / cavity).
_SIMPLE_ROUGHNESS_METAL_KEYS = (
    "RoughnessMetal", "roughnessmetal",
)
# Optional colourway tint mask (often empty / stub on base skins).
_SIMPLE_TINTMASK_KEYS = (
    "TintMask", "tintmask",
)

FAMILY_EMISSIVE = "emissive"
FAMILY_SCAN = "scan_display"
FAMILY_DECAL = "decal"
# Stamp on map-mask / branding / CA+NOH decal graphs after UseAlphaForMask fix.
_MAP_DECAL_MASK_SETUP_V = "v2"
# PropTrim atlas UVOffset / rotate wiring (pre-stamp graphs sample the vent cell).
_PROPTRIM_UV_SETUP_V = "v2"
# ArchitecturePreset_Trim: world-UV + NAO CLIP + UV Offset Amount / Color Multiply.
# v3: TrimInterior/EdgeTrim_*Decal* no longer misrouted through map-decal family.
_TRIM_SETUP_V = "v3"
# Water graph: Water↔Shore + ridged world noise bump + proximity/AO shore factor.
_WATER_SETUP_V = "v3_shore_prox"
_WATER_SHORE_ATTR = "arc_shore_proximity"
_DEFAULT_WATER_COLOR = (0.02, 0.09, 0.11, 1.0)
_DEFAULT_SHORE_COLOR = (0.42, 0.33, 0.20, 1.0)
FAMILY_GLASS = "glass"
FAMILY_WATER = "water"
FAMILY_FOLIAGE = "foliage"
FAMILY_SAND = "sand"
FAMILY_ROAD = "road"
FAMILY_TARP = "tarp"
FAMILY_METAL = "metal"
FAMILY_ENVIRONMENT = "environment"
FAMILY_WEAPON = "weapon"
FAMILY_SIMPLE = "simple"

# World-space metres per texture tile for flat road/plane surfaces (UV-independent).
_PLANE_ROAD_METERS_PER_TILE = 4.0
_TARP_METERS_PER_TILE = 2.0
# ArchitecturePreset_Trim WorldAlignedTexture density (UV Mode ≥ 1).
# ~1 m/tile matches UE WorldAligned defaults at UV Scale 1 for T_TrimConcrete_*.
_TRIM_WORLD_METERS_PER_TILE = 1.0

# Foliage texture fallback (when MI maps fail to resolve / are empty)
_FOLIAGE_ALBEDO_SUFFIXES = ("_ca", "_cs", "_c")
_FOLIAGE_NORMAL_SUFFIXES = ("_ntx", "_ntr", "_noh", "_n")
_FOLIAGE_TRUNK_ALBEDO_SUFFIXES = ("_cr", "_ca", "_cs", "_c")
_FOLIAGE_TRUNK_NORMAL_SUFFIXES = ("_noh", "_ntx", "_ntr", "_n")
_TEX_IMAGE_EXTS = (".png", ".tga", ".jpg", ".jpeg")

# Sand / dune packs (South Dunes): CH = colour+height, NR = normal+roughness, NH/NOH normals
_SAND_ALBEDO_KEYS = (
    "BaseColor", "PM_Diffuse", "BaceColor/Height", "1. CH", "CH", "CR", "CR Texture",
    "Param", "Color Base", "Base_Material_CR",
)
_SAND_NORMAL_KEYS = (
    "NormalMap", "PM_Normals", "Normal/Roughness", "1. NR", "NR", "NOH", "NH",
    "Param_1", "Param_2", "Normal Base", "Base_Material_NOH", "Normals", "Normal",
)
_SAND_ALBEDO_SUFFIXES = ("_ch", "_cr", "_ca", "_c")
_SAND_NORMAL_SUFFIXES = ("_nr", "_nh", "_noh", "_n")
_SAND_EXCLUDE_NAME = (
    "sandbox", "sandpaper", "sandbag", "sandwich", "sandstone_trim",
)
# Preferred South/Dunes (+ Base) stems for dual-scale / landscape sand (2.13+)
_SAND_MACRO_ALBEDO_STEMS = (
    "T_South_Base_Sand_Dunes_05_CR",
    "T_South_Base_Sand_Dunes_05_CH",
    "T_South_Base_Sand_Dunes_04_CH",
    "T_South_Base_Sand_Dunes_01_CR",
)
_SAND_MICRO_ALBEDO_STEMS = (
    "T_South_Base_Ground_Sand_02_CH",
    "T_South_Base_Sand_Twigs_01_CH",
    "T_South_Dunes_Detail_01_NCH",
    "T_Sand_01_A_CR",
)
_SAND_FINE_NORMAL_STEMS = (
    "T_South_Base_Sand_Dunes_04_NR",
    "T_South_Base_Ground_Sand_02_NR",
    "T_South_Dunes_Sand_01_NOH",
    "T_South_Base_Sand_Twigs_01_NR",
    "T_Sand_01_A_NOR",
    "T_Sand_01_A_NR",
)
_SAND_MACRO_NORMAL_STEMS = (
    "T_South_Base_Sand_Dunes_01_N",
    "T_South_Base_Sand_Dunes_03_NH",
    "T_South_Base_Sand_Dunes_Slope_01_N",
)
# Map names / tokens that should get sand BRDF on CityGroundPlane
_SANDY_MAP_TOKENS = (
    "riventides", "dunes", "desert", "whitedesert", "sandsea", "south_dunes",
)

# Default HLOD-inspired ground palette (Spaceport cream / gray rock / pink sediment)
_DEFAULT_HLOD_PALETTE = {
    "cream": (0.885, 0.860, 0.826),
    "rock": (0.741, 0.726, 0.709),
    "pink": (0.866, 0.785, 0.751),
    "dark": (0.42, 0.40, 0.38),
}
_INGAME_MAP_DIR_REL = os.path.join(
    "Pioneer", "UI", "Ingame", "HUD", "Map", "Assets",
)
_GROUND_MAP_REFS_DIR = os.path.join(
    os.path.dirname(__file__), "assets", "ground_map_refs",
)
_HLOD_COLOR_TILE_RE = re.compile(
    r"_color_x\d+_y\d+$", re.IGNORECASE,
)
_IMAGE_FILE_EXTS = (".png", ".tga", ".jpg", ".jpeg", ".tif", ".tiff", ".webp")

# Water plane colour / scalar aliases (real MI keys from RiverTool / lagoon / river)
_WATER_COLOR_KEYS = (
    "3. Water Color", "Water Color", "WaterColour", "WaterColor",
)
_WATER_SHALLOW_KEYS = (
    "3. Water Color Shallow", "Water Color Shallow", "Shallow",
)
_WATER_DEEP_KEYS = (
    "Deep", "Water Color Deep", "3. Water Color Deep",
)
_WATER_SHORE_KEYS = (
    "1. Shore Color", "Shore Color", "ShoreColour",
)
_WATER_EXCLUDE_NAME = (
    "watertower", "waterheater", "watertap", "watertank", "waterlily",
    "waterline", "seaweed", "seawall", "wavebreaker", "waterfall_rock",
)


def _mi_tex_params(mi: dict) -> set:
    return {p for p, _ in (mi.get("textures") or [])}


def _blend_mode_str(mi: dict) -> str:
    bm = mi.get("blend_mode")
    if bm is None:
        return ""
    return str(bm)


def _is_masked_blend(mi: dict) -> bool:
    bm = _blend_mode_str(mi).lower()
    if isinstance(mi.get("blend_mode"), int):
        # Compact dumps: 0=Opaque, 1=Masked, 2=Translucent…
        return int(mi["blend_mode"]) == 1
    return "masked" in bm or bm in ("1",)


def _is_translucent_blend(mi: dict) -> bool:
    if mi.get("is_translucent"):
        return True
    bm = _blend_mode_str(mi).lower()
    if isinstance(mi.get("blend_mode"), int):
        return int(mi["blend_mode"]) in (2, 3, 4, 5, 6)
    return "translucent" in bm or "additive" in bm


def _is_environment_surface_mi(mi: dict) -> bool:
    """True when MI uses environment packing (NOH/Overlay/layers) rather than weapon NOM/NXM."""
    params = _mi_tex_params(mi)
    if params & _ENV_LAYER_MARKERS:
        return True
    # Plain CR+NOH concrete slabs (no Overlay param) still need the env path —
    # weapon setup ignores NOH entirely.
    if "CR" in params and "NOH" in params:
        return True
    if "CR_1" in params and "NOH_1" in params:
        return True
    # Edge-trim sheets: CR + NAO (+ optional PM_Normals / Detail Normal)
    if "CR" in params and "NAO" in params:
        return True
    return False


def _is_architecture_trim_stem(stem: str = "") -> bool:
    """True for architecture edge/interior/spline trim MI names (incl. *Trim*_Decal_*)."""
    s = (stem or "").lower()
    if not s:
        return False
    if any(
        k in s
        for k in (
            "triminterior", "interiortrim", "edgedecal", "edge_decal", "edgetrim",
            "splinetrim", "spline_trim", "concretetrim", "concrete_trim",
            "architecturetrim", "architecture_trim", "concretedecal",
        )
    ):
        return True
    # MI_EdgeTrim_Decal_*, MI_Trim*_Decal_* (not MI_Decal_* map stickers)
    if "decal" in s and "trim" in s and not s.startswith("mi_decal"):
        return True
    return False


def _is_architecture_trim_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    """True for ArchitecturePreset_Trim CR+NAO edge/trim sheets (ConcreteEdgeDecal).

    Compact FModel dumps often name the NAO param ``NA`` (file still ``*_NAO``).
    Names like ``MI_TrimInteriorCeiling_Decal_01`` are trims, not map stickers.
    """
    params = _mi_tex_params(mi)
    # CR + NAO (or compact ``NA`` alias / filename marker)
    if "CR" in params and ("NAO" in params or "NA" in params):
        return True
    params_blob = " ".join(str(p) for p in params).lower()
    if "trimconcrete" in params_blob or "trimspline" in params_blob or "splinetile" in params_blob:
        return True
    parent = str(mi.get("parent") or "").lower()
    if "architecturepreset_trim" in parent or "trim+cr+nah" in parent or "trim+cr+nom" in parent:
        return True
    stem = (mi_stem_lower or "").lower()
    if _is_architecture_trim_stem(stem):
        return True
    if "concrete_trim" in stem or "concretetrim" in stem:
        return True
    return False


def _trim_wants_world_uv(mi: dict) -> bool:
    """WorldAlignedTexture for ArchitecturePreset_Trim family.

    UV Mode ≥ 1 is authoritative. Compact dumps often omit UV Mode — architecture
    trim sheets still need world density across modular edges (ControlTower /
    TrimInteriorCeiling_Decal etc.). Explicit UV Mode 0 keeps mesh UVs + offset.
    """
    if not _is_architecture_trim_mi(mi):
        return False
    scalars = mi.get("scalars") or {}
    if "UV Mode" in scalars:
        try:
            return float(scalars["UV Mode"]) >= 0.5
        except (TypeError, ValueError):
            pass
    parent = str(mi.get("parent") or "").lower()
    if "architecturepreset_trim" in parent or "trim+cr+nah" in parent or "trim+cr+nom" in parent:
        return True
    # Compact dumps omit parent + UV Mode — default world for confirmed trim sheets.
    return True


def _needs_trim_setup_rebuild(mat, mi_path: str = "") -> bool:
    """Rebuild env trims that predate world-UV / NAO alpha CLIP / setup stamp."""
    if not mi_path or not os.path.isfile(mi_path):
        return False
    fam = str(mat.get("arc_mi_family") or "")
    try:
        mi = _parse_flat_mi_json(mi_path)
    except Exception:
        return False
    if not _is_architecture_trim_mi(mi, str(mat.get("arc_mi_stem") or "")):
        return False
    # Previously misrouted through map-decal family — must leave DECAL path.
    if fam == FAMILY_DECAL:
        return True
    if fam and fam not in (FAMILY_ENVIRONMENT, FAMILY_METAL, FAMILY_ROAD):
        return False
    if str(mat.get("arc_trim_setup") or "") != _TRIM_SETUP_V:
        return True
    if _trim_wants_world_uv(mi) and not mat.get("arc_trim_world_uv"):
        return True
    use_alpha = _mi_switch(
        mi.get("switches") or {}, "Use Alpha mask", "UseAlphaMask", default=None,
    )
    if use_alpha is False:
        return False
    if (_is_masked_blend(mi) or use_alpha) and not mat.get("arc_trim_alpha"):
        return True
    return False


def _needs_proptrim_or_glass_rebuild(mat, mi_path: str = "", mi_stem: str = "") -> bool:
    """Rebuild PropTrim missing UVOffset stamp / CR, or BrokenGlass misrouted."""
    stem = (mi_stem or str(mat.get("arc_mi_stem") or "") or "").lower()
    fam = str(mat.get("arc_mi_family") or "")
    if "brokenglass" in stem or (stem.startswith("m_") and "glass" in stem and "visor" not in stem):
        if fam != FAMILY_GLASS:
            return True
    is_atlas = (
        "proptrim" in stem
        or "prop_trim" in stem
        or _is_prop_trim_atlas_mi_stem(stem)
    )
    if not is_atlas:
        return False
    if fam and fam != FAMILY_METAL:
        return True
    # Pre-v2 graphs sampled the default PropTrim atlas cell (often vent grille).
    if str(mat.get("arc_proptrim_uv") or "") != _PROPTRIM_UV_SETUP_V:
        return True
    if not mi_path or not os.path.isfile(mi_path):
        return False
    try:
        mi = _parse_flat_mi_json(mi_path)
    except Exception:
        return False
    params = _mi_tex_params(mi)
    if "CR Texture" not in params and "CR" not in params:
        return True
    _rgb, linked = _principled_base_color_info(mat)
    return not linked


def _needs_water_setup_rebuild(mat, mi_path: str = "") -> bool:
    """Rebuild water mats that predate Water↔Shore proximity + ridged world bump."""
    if mat is None:
        return False
    if str(mat.get("arc_mi_family") or "") != FAMILY_WATER:
        return False
    if str(mat.get("arc_water_setup") or "") == _WATER_SETUP_V:
        return False
    # Force-apply / Fix White always rebuild when stamp missing, even without MI path.
    return True


def _is_water_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    """Water / ocean / lagoon / river planes — prefer colour params over textures."""
    stem = (mi_stem_lower or "").lower()
    if any(x in stem for x in _WATER_EXCLUDE_NAME):
        return False

    colour_names = {str(p).lower() for p, _ in (mi.get("colours") or [])}
    water_colour_markers = {
        "1. shore color", "shore color",
        "3. water color", "water color",
        "3. water color shallow", "water color shallow",
    }
    if colour_names & water_colour_markers:
        return True
    if "deep" in colour_names and "shallow" in colour_names:
        if any(k in stem for k in ("water", "river", "ocean", "lake", "lagoon", "swamp")):
            return True

    # Empty stub MIs (inherit parent) — name routing
    if any(
        token in stem
        for token in (
            "mi_water", "m_water", "waterplane", "oceanbackdrop", "oceanlod",
            "water_river", "river_opaque", "driedriver", "shallowlake",
            "redlake", "dunelagoon", "minorswamp", "waterbluegate",
            "waterpolluted", "water_swamp", "water_buried",
        )
    ):
        return True
    if re.search(r"(^|_)(water|ocean|lagoon)(_|$)", stem):
        return True
    if "ocean" in stem and ("backdrop" in stem or "lod" in stem):
        return True
    return False


def _is_sand_dune_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    """Sand piles / dune splines / desert ground — often IsNull with textures on parent."""
    stem = (mi_stem_lower or "").lower()
    if any(x in stem for x in _SAND_EXCLUDE_NAME):
        return False
    # Explicit sand / dune / beach naming (avoid MCP *Sandbox* rooms)
    if re.search(
        r"(sanddune|sand_dune|sandpile|sand_pile|dune_spline|dunes_|_dunes|"
        r"mi_sand|m_sand|propsand|_sandy|_sand($|_))",
        stem,
    ):
        return True
    if re.search(r"(^|_)(dune|beach|desert)(_|$)", stem) and "lagoon" not in stem:
        return True

    params = _mi_tex_params(mi)
    # South Dunes landscape-style params: Param / Param_1 / Param_2 → CH / NH / NR
    if params & {"Param", "Param_1", "Param_2", "BaceColor/Height", "Normal/Roughness"}:
        tex_blob = " ".join(p for p, _ in (mi.get("textures") or [])).lower()
        path_blob = " ".join(str(t) for _, t in (mi.get("textures") or [])).lower()
        if any(k in path_blob for k in ("sand", "dune", "beach", "desert")):
            return True
        if "param" in tex_blob and any(k in stem for k in ("sand", "dune", "south")):
            return True

    # PhysMaterial stamp sometimes lands in parent string via Properties parse gaps —
    # also accept BaseColor Tint sand MIs that are IsNull (inherit M_SandDune / M_SandPile).
    if mi.get("is_null") and any(k in stem for k in ("sand", "dune")):
        return True
    return False


def _is_foliage_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    """Vegetation / leaves / grass / vines — alpha-clipped, usually two-sided."""
    params = _mi_tex_params(mi)
    parent_l = (mi.get("parent") or "").lower()
    shade_l = (mi.get("shading_model") or "").lower()
    stem = (mi_stem_lower or "").lower()

    if params & _FOLIAGE_TEX_MARKERS:
        return True
    if "twosidedfoliage" in shade_l or "vegetation" in parent_l or "foliage" in parent_l:
        return True
    if any(k in stem for k in _FOLIAGE_NAME_KEYS):
        # Name alone is weak — require a colour/normal pair typical of foliage
        has_c = bool(params & {"BaseColor", "CA", "1. CA", "PM_Diffuse", "ColorAlpha"})
        has_n = bool(params & {"Normals", "Normal", "NormalMap", "NTR", "1. NTR", "PM_Normals"})
        if has_c or has_n or mi.get("two_sided"):
            return True
    # BaseColor + Normals with Masked + TwoSided (reeds / grass) without name hit
    if (
        ("BaseColor" in params or "CA" in params)
        and ("Normals" in params or "NormalMap" in params or "NTR" in params)
        and (mi.get("two_sided") or _is_masked_blend(mi))
        and not (params & {"NOH", "CR", "CR Texture", "NOM", "NXM"})
    ):
        # Avoid gravel/scatter that is opaque + BaseColor/NormalMap only
        if mi.get("two_sided") or "foliage" in shade_l or _is_masked_blend(mi):
            # Gravel is often Masked? Usually Opaque. Prefer two_sided or foliage shade.
            if mi.get("two_sided") or "foliage" in shade_l:
                return True
    return False


def _is_glass_env_mi(mi: dict, mi_stem_lower: str = "", slot_lower: str = "") -> bool:
    """Environment window / pane glass (not clothing visor ColorA/B path)."""
    params = _mi_tex_params(mi)
    parent_l = (mi.get("parent") or "").lower()
    stem = (mi_stem_lower or "").lower()
    slot = (slot_lower or "").lower()
    if "brokenglass" in stem or "brokenglass" in slot or "brokenglass" in parent_l:
        return True
    if slot in ("glass", "windowpane") or "windowpane" in slot:
        return True
    if params & _GLASS_TEX_MARKERS:
        return True
    if "windowpane" in parent_l or "glass_" in parent_l or parent_l.endswith("glass"):
        # Clothing visors handled elsewhere; map glass parents still match
        if "character" in parent_l and "glass_opaque" in parent_l:
            return False
        return True
    if "glass" in stem and (
        params & {"C", "NXX", "MaskTexture", "CubemapInside", "CubemapOutside"}
        or _is_translucent_blend(mi)
        or _is_masked_blend(mi)
        or mi.get("two_sided")
    ):
        path_blob = " ".join(str(t) for _, t in (mi.get("textures") or [])).lower()
        if "glass" in path_blob or "window" in path_blob or _is_translucent_blend(mi):
            return True
        if "brokenglass" in path_blob or "m_brokenglass" in stem:
            return True
    return False


def _is_road_tarmac_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    params = _mi_tex_params(mi)
    parent_l = (mi.get("parent") or "").lower()
    stem = (mi_stem_lower or "").lower()
    if params & {"0. CR 1", "0. CR 2", "0. NOH 1", "0. NOH 2"}:
        return True
    if "tarmac" in parent_l or "tarmac" in stem or "asphalt" in stem:
        return True
    if ("road" in stem or "asphalt" in stem) and (params & {"NOH", "CR", "CR_Blend", "CR Blend"}):
        return True
    return False


def _is_tarp_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    stem = (mi_stem_lower or "").lower()
    parent_l = (mi.get("parent") or "").lower()
    if "tarp" in stem or "tarp" in parent_l:
        return True
    if "awning" in stem and ("tarp" in stem or "transparenttarp" in parent_l):
        return True
    params = _mi_tex_params(mi)
    # PropPreset NR+VPO+Detail tarps often only override Variation Mask + Base Color
    if "variation mask" in {p.lower() for p in params} and (
        "proppreset" in parent_l or "nr+vpo" in parent_l.replace(" ", "")
    ):
        if "tarp" in stem or "awning" in stem:
            return True
    return False


def _is_metal_prop_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    """Painted / trim metal: CR Texture + NXX/NMX (or NOM) packing."""
    params = _mi_tex_params(mi)
    stem = (mi_stem_lower or "").lower()
    parent_l = (mi.get("parent") or "").lower()
    # Enemy/weapon CR+NOM parents stay on the weapon path
    if "enemypreset" in parent_l or "weapon" in parent_l or "firearm" in parent_l:
        return False
    has_nmx = bool(params & {"NXX/NMX Texture", "NXM", "NMX", "HolesNXX"})
    has_nom = "NOM" in params
    if "CR Texture" in params and (has_nmx or has_nom or "NXX" in params):
        return True
    if any(k in stem for k in ("metal", "steel", "aluminium", "aluminum", "proptrim", "trim_metal")):
        if has_nmx or has_nom or "CR Texture" in params or "NXX" in params:
            return True
        # PropTrim children after inherit (or AO/Overlay-only compact dumps)
        if "proptrim" in stem or "prop_trim" in stem:
            if "Overlay" in params or "Prop AO Texture" in params or "proptrim" in parent_l:
                return True
    return False


def _is_simple_surface_mi(mi: dict) -> bool:
    """BaseColor/Color+Normal (or CR+Normal / hero RoughnessMetal) without layered packs."""
    params = _mi_tex_params(mi)
    if not params:
        return False
    if params & _ENV_LAYER_MARKERS:
        return False
    if params & {"NOM", "NXM", "NMX", "Wear", "1. Wear CR", "EX", "EXX"}:
        return False
    has_albedo = bool(params & set(_SIMPLE_ALBEDO_KEYS))
    has_normal = bool(params & set(_SIMPLE_NORMAL_KEYS))
    has_rm = bool(params & {"RoughnessMetal"})
    return has_albedo or has_normal or has_rm


def _is_graphic_atlas_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    """Company branding / MCP poster GraphicAtlas sheets (M_Graphic_Atlas_01)."""
    stem = (mi_stem_lower or "").lower()
    if "graphicatlas" in stem or "graphic_atlas" in stem:
        return True
    parent = str(mi.get("parent") or "").lower()
    if "graphic_atlas" in parent or "graphicatlas" in parent:
        return True
    params = _mi_tex_params(mi)
    return "Graphic Atlas" in params


def classify_mi_family(mi: dict, mi_stem_lower: str = "", slot_lower: str = "") -> str:
    """Classify an MI into a setup family. Order matters — specific before general."""
    stem = (mi_stem_lower or "").lower()
    slot = (slot_lower or "").lower()

    if _is_weapon_emissive_light_mi(stem):
        return FAMILY_EMISSIVE
    if (
        "scandisplay" in stem
        or "scandisplay" in slot
        or ("screen" in stem and "sunscreen" not in stem)
    ):
        return FAMILY_SCAN
    # Glass slots first (BrokenGlass / WindowPane) — before Decal name checks.
    if _is_glass_env_mi(mi, stem, slot):
        return FAMILY_GLASS
    # Architecture edge/interior trims BEFORE "decal" substring checks.
    # MI_TrimInteriorCeiling_Decal_01 / MI_EdgeTrim_Decal_* are CR+NAO trim sheets —
    # routing them to map-decal made BuriedCity's most-common SMAs look like vents.
    if _is_architecture_trim_mi(mi, stem) or _is_architecture_trim_stem(stem):
        return FAMILY_ENVIRONMENT
    # Branding GraphicAtlas posters BEFORE PropTrim/metal (Overlay+CR Blend params).
    # PosterFrame_*PropTrim* keeps the metal path below via proptrim stem.
    if _is_graphic_atlas_mi(mi, stem):
        return FAMILY_DECAL
    # Decal family: MI_Decal_* / crack stickers, dedicated decal slots, enemy NAO+H.
    # Do NOT treat ConcreteEdgeDecal / *Trim*_Decal_* surface slots as map decals.
    if _is_enemy_decal_mi(mi):
        return FAMILY_DECAL
    if _slot_implies_map_decal(slot):
        return FAMILY_DECAL
    if "decal" in stem and not _is_architecture_trim_stem(stem):
        return FAMILY_DECAL
    # Mask-only murals / raider marks still count as decals
    params = _mi_tex_params(mi)
    if params and params <= {"Mask", "Raider Mark Texture", "Decal Mask", "X", "SignTexture"}:
        if "sign" not in stem:
            return FAMILY_DECAL
    if _is_water_mi(mi, stem):
        return FAMILY_WATER
    if _is_sand_dune_mi(mi, stem):
        return FAMILY_SAND
    if _is_foliage_mi(mi, stem):
        return FAMILY_FOLIAGE
    if _is_road_tarmac_mi(mi, stem):
        return FAMILY_ROAD
    if _is_tarp_mi(mi, stem):
        return FAMILY_TARP
    if _is_metal_prop_mi(mi, stem):
        return FAMILY_METAL
    if _is_environment_surface_mi(mi):
        return FAMILY_ENVIRONMENT
    # Weapon: CR + packed metallic normal / wear, no env markers
    if params & {"NOM", "NXM", "NMX", "Wear", "1. Wear CR", "EXX", "EX"} and "CR" in params:
        return FAMILY_WEAPON
    if _is_simple_surface_mi(mi):
        return FAMILY_SIMPLE
    # Default: weapon path is a reasonable Principled CR/NOM attempt
    if "CR" in params or "NOM" in params or "NXM" in params:
        return FAMILY_WEAPON
    return FAMILY_SIMPLE


def _stamp_mi_family(mat, family: str):
    try:
        mat["arc_mi_family"] = family
    except Exception:
        pass


def _mi_switch(switches: dict, *names, default=None):
    """Return the first authored static switch among *names*, else *default*."""
    for name in names:
        if name in switches:
            return bool(switches[name])
    return default


def _mi_scalar(scalars: dict, *names, default=0.0) -> float:
    for name in names:
        if name in scalars and scalars[name] is not None:
            try:
                return float(scalars[name])
            except (TypeError, ValueError):
                continue
    return float(default)


def _mi_colour(colours: list, *names, default=None):
    lower = {n.lower(): n for n in names}
    for param, rgba in colours or []:
        if param.lower() in lower:
            return rgba
    return default


def _find_env_tex(tex_lookup: dict, *keys: str):
    """Exact MI param match first (order preserved), then filename suffix fallback."""
    return _find_flat_tex(tex_lookup, *keys)


def _new_tex_image(nodes, img, label: str, loc, non_color: bool = False):
    if non_color:
        try:
            img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
    node = nodes.new("ShaderNodeTexImage")
    node.image = img
    node.label = label
    node.interpolation = "Cubic"
    node.location = loc
    return node


def _mapping_tiled(nodes, links, tiling: float, loc, from_uv=None):
    """UV → Mapping with uniform tiling. Returns Mapping Vector output."""
    if from_uv is None:
        uv = nodes.new("ShaderNodeTexCoord")
        uv.location = (loc[0] - 220, loc[1])
        from_uv = uv.outputs["UV"]
    mapping = nodes.new("ShaderNodeMapping")
    mapping.label = f"Tile ×{tiling:g}"
    mapping.location = loc
    scale = max(float(tiling), 0.001)
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    links.new(from_uv, mapping.inputs["Vector"])
    return mapping.outputs["Vector"]


def _mapping_world_density(
    nodes, links, meters_per_tile: float, loc, label: str = "",
    uv_offset: float = 0.0,
):
    """World Position → Mapping so texel density is constant in metres (not UV).

    Large scaled planes (tarmac / water cards) keep the same grain as small ones.
    ``meters_per_tile`` = world metres for one full texture repeat.
    ``uv_offset`` folds ArchitecturePreset_Trim ``UV Offset Amount`` into Location.
    """
    geo = nodes.new("ShaderNodeNewGeometry")
    geo.location = (loc[0] - 220, loc[1])
    mapping = nodes.new("ShaderNodeMapping")
    mpt = max(float(meters_per_tile), 0.05)
    scale = 1.0 / mpt
    mapping.label = label or f"World {mpt:g}m/tile"
    if abs(float(uv_offset)) > 1e-5:
        mapping.label += f" off={float(uv_offset):.3g}"
    mapping.location = loc
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    if abs(float(uv_offset)) > 1e-5:
        mapping.inputs["Location"].default_value = (float(uv_offset), 0.0, 0.0)
    links.new(geo.outputs["Position"], mapping.inputs["Vector"])
    return mapping.outputs["Vector"]


def _env_tex_vector(
    nodes,
    links,
    *,
    loc,
    tiling: float = 1.0,
    world_meters_per_tile: float | None = None,
    uv_offset: float = 0.0,
    rotate_uv: bool = False,
    force_mapping: bool = False,
):
    """Pick UV tiling or world-density vector for environment / road / tarp tex.

    ``uv_offset`` / ``rotate_uv`` honour PropTrim atlas cell selection (UVOffset
    scalar + Use Rotate UV's) and ArchitecturePreset_Trim UV Offset Amount.
    Without these, every PropTrim mesh samples the default atlas cell — often
    the vent/grille panel.
    """
    if world_meters_per_tile is not None and world_meters_per_tile > 0.0:
        # Fold MI Tiling into density: higher Tiling → finer grain
        mpt = float(world_meters_per_tile) / max(float(tiling), 0.05)
        mpt = max(0.25, min(mpt, 64.0))
        return _mapping_world_density(
            nodes, links, mpt, loc, uv_offset=float(uv_offset) if abs(float(uv_offset)) > 1e-5 else 0.0,
        )
    need_map = (
        force_mapping
        or abs(float(tiling) - 1.0) > 0.01
        or abs(float(uv_offset)) > 1e-5
        or rotate_uv
    )
    if not need_map:
        return None
    uv = nodes.new("ShaderNodeTexCoord")
    uv.location = (loc[0] - 220, loc[1])
    mapping = nodes.new("ShaderNodeMapping")
    scale = max(float(tiling), 0.001)
    mapping.label = f"Tile ×{scale:g}"
    if abs(float(uv_offset)) > 1e-5:
        mapping.label += f" off={float(uv_offset):.3g}"
    mapping.location = loc
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    # UE UVOffset shifts along U (and often V when Rotate is on).
    off = float(uv_offset)
    if rotate_uv:
        mapping.inputs["Location"].default_value = (off, off, 0.0)
        try:
            mapping.inputs["Rotation"].default_value = (0.0, 0.0, 1.5707963)  # 90°
        except Exception:
            pass
    else:
        mapping.inputs["Location"].default_value = (off, 0.0, 0.0)
    links.new(uv.outputs["UV"], mapping.inputs["Vector"])
    return mapping.outputs["Vector"]


def _wire_normal_map(nodes, links, color_sock, loc, strength: float = 1.0, label: str = "Normal Map"):
    """Tangent normal Color → (optional NormalFlipper) → Normal Map. Returns Normal socket."""
    nm_in = color_sock
    if utils.ensure_node_group("NormalFlipper"):
        flipper = nodes.new("ShaderNodeGroup")
        flipper.node_tree = bpy.data.node_groups["NormalFlipper"]
        flipper.location = (loc[0] - 180, loc[1])
        links.new(color_sock, flipper.inputs[0])
        nm_in = flipper.outputs[0]
    nm = nodes.new("ShaderNodeNormalMap")
    nm.label = label
    nm.location = loc
    try:
        nm.convention = "DIRECTX"
    except Exception:
        pass
    try:
        nm.inputs["Strength"].default_value = max(float(strength), 0.0)
    except Exception:
        pass
    links.new(nm_in, nm.inputs["Color"])
    return nm.outputs["Normal"]


def _noh_ao_from_tex(nodes, links, noh_node, loc):
    """NOH packing: RGB=Normal(+Occlusion in B). Return (normal_color_sock, ao_value_sock)."""
    sep = nodes.new("ShaderNodeSeparateColor")
    sep.label = "NOH Channels"
    sep.location = loc
    links.new(noh_node.outputs["Color"], sep.inputs["Color"])
    # Rebuild RGB for Normal Map; Blue still carries occlusion for albedo darkening.
    return noh_node.outputs["Color"], sep.outputs["Blue"]


def _mix_float(nodes, links, a, b, fac, loc, label: str):
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "FLOAT"
    mix.blend_type = "MIX"
    mix.label = label
    mix.location = loc
    if hasattr(fac, "links") or hasattr(fac, "node"):
        links.new(fac, mix.inputs["Factor"])
    else:
        mix.inputs["Factor"].default_value = float(fac)
    links.new(a, mix.inputs[2])
    links.new(b, mix.inputs[3])
    return mix.outputs[0]


def _mix_rgba(nodes, links, a, b, fac, loc, label: str, blend: str = "MIX"):
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = blend
    mix.label = label
    mix.location = loc
    if hasattr(fac, "links") or hasattr(fac, "node"):
        links.new(fac, mix.inputs["Factor"])
    else:
        mix.inputs["Factor"].default_value = float(fac)
    links.new(a, mix.inputs[6])
    links.new(b, mix.inputs[7])
    return mix.outputs[2]


def _mix_normals_vec(nodes, links, a, b, fac, loc, label: str):
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "VECTOR"
    mix.blend_type = "MIX"
    mix.label = label
    mix.location = loc
    if hasattr(fac, "links") or hasattr(fac, "node"):
        links.new(fac, mix.inputs["Factor"])
    else:
        mix.inputs["Factor"].default_value = float(fac)
    links.new(a, mix.inputs[4])
    links.new(b, mix.inputs[5])
    return mix.outputs[1]


def _contrast_mask(nodes, links, value_sock, low: float, high: float, loc, label: str):
    """MapRange contrast for blend / overlay masks (UE Mask High/Low)."""
    ramp = nodes.new("ShaderNodeMapRange")
    ramp.label = label
    ramp.location = loc
    ramp.clamp = True
    lo, hi = float(low), float(high)
    if hi <= lo:
        hi = lo + 0.001
    ramp.inputs["From Min"].default_value = lo
    ramp.inputs["From Max"].default_value = hi
    ramp.inputs["To Min"].default_value = 0.0
    ramp.inputs["To Max"].default_value = 1.0
    links.new(value_sock, ramp.inputs["Value"])
    return ramp.outputs["Result"]


def _mask_channel_value(nodes, links, mask_node, loc, prefer: str = "Red"):
    """GRM / X / grayscale masks → single float. Prefer Red, else luminance via Separate."""
    sep = nodes.new("ShaderNodeSeparateColor")
    sep.label = "Mask Channels"
    sep.location = loc
    links.new(mask_node.outputs["Color"], sep.inputs["Color"])
    channel = prefer if prefer in sep.outputs else "Red"
    return sep.outputs[channel]


def _setup_environment_material(mat, mi_path: str, psk_path: str = "", family: str = FAMILY_ENVIRONMENT):
    """Principled setup for Arc environment MIs (concrete, buildings, roads, metal trims).

    Implements the layered protocol in docs/TEXTURE_PROTOCOL.md:
      base CR/NOH → optional layer-2 blend → Overlay tint → Detail Normal → Tint

    Road / tarmac families sample **world Position** so differently scaled plane
    meshes keep a consistent texel density (see ``_PLANE_ROAD_METERS_PER_TILE``).
    """
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    fam = family or FAMILY_ENVIRONMENT
    _stamp_mi_family(mat, fam)
    scalars = mi.get("scalars") or {}
    switches = mi.get("switches") or {}
    colours = mi.get("colours") or []
    is_trim = _is_architecture_trim_mi(mi)
    world_meters_per_tile = None
    if fam == FAMILY_ROAD:
        world_meters_per_tile = _PLANE_ROAD_METERS_PER_TILE
        try:
            mat["arc_world_tile_m"] = float(world_meters_per_tile)
        except Exception:
            pass
    elif is_trim and _trim_wants_world_uv(mi):
        # WorldAlignedTexture: continuous weathering across modular edge seams.
        uv_scale = _mi_scalar(scalars, "UV Scale", default=1.0)
        world_meters_per_tile = _TRIM_WORLD_METERS_PER_TILE / max(float(uv_scale), 0.05)
        world_meters_per_tile = max(0.25, min(world_meters_per_tile, 16.0))
        try:
            mat["arc_world_tile_m"] = float(world_meters_per_tile)
            mat["arc_trim_world_uv"] = 1
        except Exception:
            pass

    COL_TEX, COL_UTIL, COL_MIX, COL_BSDF, COL_OUT = -1800, -1100, -350, 450, 750

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    # ── Resolve layer-1 albedo / roughness (CR family) ─────────────────────
    cr1_keys = (
        "1. CR", "0. CR 1", "CR", "CR Texture", "Base_Material_CR", "Color Base",
        "CR_1", "B CR", "BC", "1.  Material CR", "1. Wear CR", "BaseColor",
    )
    cr2_keys = (
        "2. CR", "0. CR 2", "CR Blend", "CR_Blend", "Breakup_Material_CR",
        "CR Breakup", "Color Top", "CR_2", "B CR", "2. Overlay CR",
    )
    noh1_keys = (
        "1. NOH", "0. NOH 1", "NOH", "Normal Base", "NOH_1", "Base_Material_NOH",
        "1.  Material NOH", "1. Wear NOH", "Normal", "Normals", "NormalMap",
    )
    noh2_keys = (
        "2. NOH", "0. NOH 2", "NOH Blend", "NOH_Blend", "Breakup_Material_NOH",
        "NOH Breakup", "Normal Top", "NOH_2", "B NOH",
    )
    mask_keys = (
        "3. Blend Mask", "4. Mask", "Breakup Mask", "Breakup Mask - Linear Grayscale",
        "PaintBreakup", "Mask", "Variation Masks", "Variation Mask",
        "Packed Mask", "PM_SpecularMasks",
    )
    overlay_keys = (
        "4.. Overlay", "Overlay", "Color Overlay", "WaterlineOverlay",
        "2. Overlay CR", "Global Color Overlay",
    )
    detail_n_keys = (
        "1. Detail", "Detail Normal Texture", "Detail Normal",
        "Detail Normal Worn", "Detail NXR",
    )
    # Weapon-style packed normals used on some prop trims / painted metal
    packed_n_keys = (
        "NXX/NMX Texture", "NOM", "NXM", "NMX", "NXX", "HolesNXX",
        "NAO", "NA", "PM_Normals", "NormalMap",
    )

    _, cr1_img = _find_env_tex(tex_lookup, *cr1_keys)
    cr1_param = next((p for p, (_fp, im) in tex_lookup.items() if im == cr1_img), "") if cr1_img else ""
    _, cr2_img = _find_env_tex(tex_lookup, *cr2_keys)
    # Avoid treating the same CR as both layers
    if cr2_img is not None and cr1_img is not None and cr2_img == cr1_img:
        cr2_img = None
        for key in cr2_keys:
            hit, img = _find_env_tex(tex_lookup, key)
            if img is not None and img != cr1_img:
                cr2_img = img
                break

    _, noh1_img = _find_env_tex(tex_lookup, *noh1_keys)
    if not noh1_img:
        _, noh1_img = _find_env_tex(tex_lookup, *packed_n_keys)
    _, noh2_img = _find_env_tex(tex_lookup, *noh2_keys)
    if noh2_img is not None and noh1_img is not None and noh2_img == noh1_img:
        noh2_img = None

    _, mask_img = _find_env_tex(tex_lookup, *mask_keys)
    _, overlay_img = _find_env_tex(tex_lookup, *overlay_keys)
    # PM_Diffuse often aliases Overlay color-var; only use when Overlay missing
    if not overlay_img:
        _, pm_diff = _find_env_tex(tex_lookup, "PM_Diffuse")
        if pm_diff is not None and pm_diff != cr1_img:
            # Prefer when stem looks like color-var / overlay (_C / ColorVar)
            for param, (fpath, img) in tex_lookup.items():
                if img is pm_diff:
                    stem = os.path.splitext(os.path.basename(fpath))[0].lower()
                    if any(t in stem for t in ("colorvar", "color_var", "overlay", "_c")):
                        overlay_img = pm_diff
                    break

    _, detail_img = _find_env_tex(tex_lookup, *detail_n_keys)
    _, ao_img = _find_env_tex(tex_lookup, "Prop AO Texture", "AO")

    base_tiling = _mi_scalar(scalars, "Tiling", "Tile", default=1.0)
    # PropTrim atlases + ArchitecturePreset_Trim shift UV (vent grille vs panel cells /
    # trim sheet offsets). Always honour authored offsets — never METAL-only.
    mi_stem_l = os.path.splitext(os.path.basename(mi_path or ""))[0].lower()
    parent_l = str(mi.get("parent") or "").lower()
    is_proptrim_atlas = (
        "proptrim" in mi_stem_l
        or "prop_trim" in mi_stem_l
        or "proptrim" in parent_l
        or fam == FAMILY_METAL
    )
    uv_offset = _mi_scalar(
        scalars,
        "UVOffset", "UV Offset", "OffsetUVs", "UV Offset Amount",
        default=0.0,
    )
    rotate_uv = bool(_mi_switch(
        switches, "Use Rotate UV's", "Use Rotate UVs", "Rotate UVs", default=False,
    ))
    # Scalar form of rotate (some compact dumps store 0/1 under Scalars).
    if not rotate_uv and _mi_scalar(scalars, "Use Rotate UV's", "Use Rotate UVs", default=0.0) >= 0.5:
        rotate_uv = True
    apply_uv_xform = (
        is_proptrim_atlas
        or is_trim
        or abs(float(uv_offset)) > 1e-5
        or rotate_uv
    )
    y_cr, y_n, y_mask, y_ov, y_det, y_extra = 700, 200, -250, -700, -1100, -1600

    def _bind_tex_vec(tex_node, y: float, tiling: float = base_tiling):
        off = float(uv_offset) if apply_uv_xform else 0.0
        rot = bool(rotate_uv) if apply_uv_xform else False
        vec = _env_tex_vector(
            nodes,
            links,
            loc=(COL_TEX - 350, y),
            tiling=tiling,
            world_meters_per_tile=world_meters_per_tile,
            uv_offset=off,
            rotate_uv=rot,
            force_mapping=bool(apply_uv_xform and (abs(off) > 1e-5 or rot or is_proptrim_atlas)),
        )
        if vec is not None and tex_node is not None:
            links.new(vec, tex_node.inputs["Vector"])
        elif tex_node is not None and apply_uv_xform and (
            abs(float(uv_offset)) > 1e-5 or rotate_uv or is_proptrim_atlas
        ):
            # Even at tiling=1 / no world UV, PropTrim needs UVOffset / rotate.
            vec = _env_tex_vector(
                nodes,
                links,
                loc=(COL_TEX - 350, y),
                tiling=1.0,
                world_meters_per_tile=None,
                uv_offset=uv_offset,
                rotate_uv=rotate_uv,
                force_mapping=True,
            )
            if vec is not None:
                links.new(vec, tex_node.inputs["Vector"])

    albedo_sock = None
    rough_sock = None
    normal_sock = None
    ao_sock = None

    cr1_node = None
    if cr1_img:
        cr1_node = _new_tex_image(nodes, cr1_img, "CR / Layer1", (COL_TEX, y_cr))
        _bind_tex_vec(cr1_node, y_cr)
        albedo_sock = cr1_node.outputs["Color"]
        # BaseColor / CA alphas are opacity, not roughness
        cr1_pl = (cr1_param or "").lower()
        if cr1_pl not in ("basecolor", "ca", "1. ca", "coloralpha", "pm_diffuse"):
            rough_sock = cr1_node.outputs["Alpha"]
        else:
            rough_sock = None
            # Soft default; scalar Roughness may override below
            principled.inputs["Roughness"].default_value = _mi_scalar(
                scalars, "3. Roughness", "Roughness", "Glass Roughness", default=0.55,
            )

    noh1_node = None
    if noh1_img:
        noh1_node = _new_tex_image(
            nodes, noh1_img, "NOH / Normal L1", (COL_TEX, y_n), non_color=True,
        )
        _bind_tex_vec(noh1_node, y_n)
        n_str = _mi_scalar(
            scalars, "Normal Strength", "Base Normal Strength", "NormalStrength",
            default=1.0,
        )
        # UE often authors strengths > 1; Principled Normal Map Strength stays usable ≤ 2
        n_str = min(max(n_str, 0.0), 2.5)
        n_color, ao_from_noh = _noh_ao_from_tex(
            nodes, links, noh1_node, (COL_UTIL, y_n),
        )
        normal_sock = _wire_normal_map(
            nodes, links, n_color, (COL_MIX - 200, y_n), strength=n_str, label="Base Normal",
        )
        ao_sock = ao_from_noh
        # NXX/NMX / NOM alpha → Metallic when present on the packed normal
        stem = os.path.splitext(os.path.basename(
            next((fp for _p, (fp, im) in tex_lookup.items() if im == noh1_img), "")
        ))[0].lower()
        param_hit = next((p for p, (_fp, im) in tex_lookup.items() if im == noh1_img), "")
        param_l = (param_hit or "").lower()
        is_metallic_pack = (
            any(stem.endswith(s) for s in ("_nxm", "_nmx", "_nom", "_nhm"))
            or "nmx" in param_l
            or "nxm" in param_l
            or param_l == "nom"
            or "nxx/nmx" in param_l
        )
        is_pure_nxx = (
            stem.endswith("_nxx")
            or stem.endswith("_nx")
            or param_l in ("nxx", "holesnxx")
        )
        if is_metallic_pack and not is_pure_nxx:
            links.new(noh1_node.outputs["Alpha"], principled.inputs["Metallic"])
        elif fam == FAMILY_METAL:
            principled.inputs["Metallic"].default_value = 0.8

    # ── Layer-2 blend (concrete / brick / stucco dual materials) ────────────
    use_breakup = _mi_switch(
        switches,
        "Enable Breakup Material", "Use Breakup Material", "Use Breakup Texture",
        "Enable Blend Textures", "Vertex color breakup",
        default=None,
    )
    has_layer2 = bool(cr2_img or noh2_img)
    if has_layer2 and use_breakup is False and mask_img is None:
        has_layer2 = False

    blend_fac = None
    if has_layer2 and mask_img:
        mask_node = _new_tex_image(
            nodes, mask_img, "Blend / Breakup Mask", (COL_TEX, y_mask), non_color=True,
        )
        mask_tile = _mi_scalar(
            scalars, "BreakupMaskTiling", "Breakup Tiling", "3. Blend Mask Size",
            "Blend Texture Tiling", default=1.0,
        )
        # Huge authored values (e.g. 3000) are world-scale; clamp for mesh UV preview
        if mask_tile > 64.0:
            mask_tile = 4.0
        _bind_tex_vec(mask_node, y_mask, tiling=mask_tile)
        raw = _mask_channel_value(nodes, links, mask_node, (COL_UTIL, y_mask))
        lo = _mi_scalar(
            scalars,
            "3. Blend Mask Low", "Breakup MaskRange_Low", "Blending_RangeLow",
            "Blend Bias", default=0.35,
        )
        hi = _mi_scalar(
            scalars,
            "3. Blend Mask High", "Breakup MaskRange_High", "Blending_RangeHigh",
            default=0.65,
        )
        # Blend Bias on VT materials is often negative; remap softly
        if lo < 0.0:
            lo = 0.2
        blend_fac = _contrast_mask(
            nodes, links, raw, lo, hi, (COL_UTIL + 220, y_mask), "Blend Contrast",
        )
    elif has_layer2:
        # No mask → soft 50/50 so the second layer still contributes
        blend_fac = 0.35

    if has_layer2 and cr2_img and albedo_sock is not None and blend_fac is not None:
        cr2_node = _new_tex_image(nodes, cr2_img, "CR / Layer2", (COL_TEX - 500, y_cr - 320))
        layer_tile = _mi_scalar(scalars, "Blend Texture Tiling", "2. Base Material Tiling", default=base_tiling)
        _bind_tex_vec(cr2_node, y_cr - 320, tiling=layer_tile)
        albedo_sock = _mix_rgba(
            nodes, links, albedo_sock, cr2_node.outputs["Color"], blend_fac,
            (COL_MIX - 80, y_cr), "Layer1↔2 Albedo",
        )
        if rough_sock is not None:
            rough_sock = _mix_float(
                nodes, links, rough_sock, cr2_node.outputs["Alpha"], blend_fac,
                (COL_MIX - 80, y_cr - 180), "Layer1↔2 Roughness",
            )

    if has_layer2 and noh2_img and normal_sock is not None and blend_fac is not None:
        noh2_node = _new_tex_image(
            nodes, noh2_img, "NOH / Normal L2", (COL_TEX - 500, y_n - 320), non_color=True,
        )
        _bind_tex_vec(noh2_node, y_n - 320)
        blend_n_str = _mi_scalar(scalars, "Blend Normal Strength", default=1.0)
        blend_n_str = min(max(blend_n_str, 0.0), 2.5)
        n2 = _wire_normal_map(
            nodes, links, noh2_node.outputs["Color"],
            (COL_MIX - 200, y_n - 280), strength=blend_n_str, label="Layer2 Normal",
        )
        # Fac: how much layer2 replaces layer1
        fac = blend_fac
        if isinstance(blend_fac, (int, float)):
            fac = min(max(float(blend_fac) * 0.85, 0.0), 1.0)
        normal_sock = _mix_normals_vec(
            nodes, links, normal_sock, n2, fac,
            (COL_MIX + 80, y_n - 100), "Layer1↔2 Normal",
        )

    # ── Overlay color variation (concrete ColorVar / rust overlays) ────────
    use_overlay = _mi_switch(
        switches, "UseOverlay", "EnableOverlay", "Color Overlay", default=None,
    )
    ov_str = _mi_scalar(
        scalars,
        "Overlay_BaseColor_Strength", "Overlay Strength", "4. Overlay Strength",
        "OverlayIntensity", default=0.5,
    )
    if overlay_img and use_overlay is not False and ov_str > 0.001 and albedo_sock is not None:
        ov_node = _new_tex_image(nodes, overlay_img, "Overlay", (COL_TEX, y_ov))
        ov_tile = _mi_scalar(
            scalars, "OverlayTiling", "2. OverlayTiling", "4. Overlay Size", default=2.0,
        )
        _bind_tex_vec(ov_node, y_ov, tiling=ov_tile)
        # Soft contrast so mid-gray ColorVars don't flatten the base
        ov_lo = _mi_scalar(scalars, "4. Overlay Low", default=0.25)
        ov_hi = _mi_scalar(scalars, "4. Overlay High", default=0.75)
        ov_fac_val = min(max(ov_str, 0.0), 1.0)
        # Multiply overlay into albedo, then mix by strength (keeps ColorVar readable)
        mul = nodes.new("ShaderNodeMix")
        mul.data_type = "RGBA"
        mul.blend_type = "MULTIPLY"
        mul.label = "Overlay × Albedo"
        mul.location = (COL_MIX - 80, y_ov + 80)
        mul.inputs["Factor"].default_value = 1.0
        links.new(albedo_sock, mul.inputs[6])
        links.new(ov_node.outputs["Color"], mul.inputs[7])
        albedo_sock = _mix_rgba(
            nodes, links, albedo_sock, mul.outputs[2], ov_fac_val,
            (COL_MIX + 160, y_ov + 80), "Overlay Mix",
        )
        # Optional roughness lift from overlay alpha / strength
        ov_rough = _mi_scalar(scalars, "Overlay_Roughness_Strength", default=0.0)
        if ov_rough > 0.001 and rough_sock is not None:
            rough_sock = _mix_float(
                nodes, links, rough_sock, ov_node.outputs["Alpha"],
                min(ov_rough, 1.0), (COL_MIX + 160, y_ov - 80), "Overlay → Rough",
            )

    # ── Detail / micro normal (often overlaid on concrete & metals) ─────────
    use_detail = _mi_switch(
        switches,
        "Enable DetailNormal", "Enable Detail Normal", "EnableDetailNormal",
        "Use Detail", default=None,
    )
    det_str = _mi_scalar(
        scalars,
        "Detail Normal Strength", "1. Detail Normal Strength",
        "Detail Normal Intensity", "DetailNormalIntensity",
        default=0.5,
    )
    if detail_img and use_detail is not False and det_str > 0.001 and normal_sock is not None:
        det_node = _new_tex_image(
            nodes, detail_img, "Detail Normal", (COL_TEX, y_det), non_color=True,
        )
        det_tile = _mi_scalar(
            scalars,
            "Detail Normal Tiling", "DetailNormalTiling", "1. Detail Tiling",
            "Detail Normal Tile", "Detail Tile", "Custom Detail Normal Tiling",
            default=4.0,
        )
        _bind_tex_vec(det_node, y_det, tiling=det_tile)
        # NCR detail packs may carry albedo in RGB — still treat as normal for strength path
        det_n = _wire_normal_map(
            nodes, links, det_node.outputs["Color"],
            (COL_MIX - 200, y_det), strength=1.0, label="Detail Normal Map",
        )
        fac = min(max(det_str if det_str <= 1.0 else det_str / 2.0, 0.0), 1.0)
        normal_sock = _mix_normals_vec(
            nodes, links, normal_sock, det_n, fac,
            (COL_MIX + 120, y_det), "Base ⊕ Detail Normal",
        )

    # ── Prop AO / NOH occlusion → darken albedo ────────────────────────────
    if ao_img:
        ao_node = _new_tex_image(
            nodes, ao_img, "Prop AO", (COL_TEX - 500, y_extra), non_color=True,
        )
        ao_sock = _mask_channel_value(nodes, links, ao_node, (COL_UTIL, y_extra))

    if albedo_sock is not None and ao_sock is not None:
        ao_mul = nodes.new("ShaderNodeMix")
        ao_mul.data_type = "RGBA"
        ao_mul.blend_type = "MULTIPLY"
        ao_mul.label = "AO → Albedo"
        ao_mul.location = (COL_MIX + 280, y_cr - 40)
        ao_mul.inputs["Factor"].default_value = 0.7
        ao_rgb = nodes.new("ShaderNodeCombineColor")
        ao_rgb.location = (COL_MIX + 80, y_cr - 200)
        links.new(ao_sock, ao_rgb.inputs["Red"])
        links.new(ao_sock, ao_rgb.inputs["Green"])
        links.new(ao_sock, ao_rgb.inputs["Blue"])
        links.new(albedo_sock, ao_mul.inputs[6])
        links.new(ao_rgb.outputs["Color"], ao_mul.inputs[7])
        albedo_sock = ao_mul.outputs[2]

    # ── Global Tint / Color Multiply (trim Color vector, not RT-only base) ─
    if is_trim:
        tint = _mi_colour(colours, "Color", "Tint", "BaseColor Tint", "GlobalTint", default=None)
    else:
        tint = _mi_colour(
            colours, "Tint", "RT Base Color", "BaseColor Tint", "GlobalTint",
            "1. Tint", "Paint", default=None,
        )
    if tint is not None and albedo_sock is not None and _mi_switch(
        switches, "Use Tint", "Enable Tinting", "Tint", default=True,
    ):
        # Skip RT Base Color darkening on trims when Color is white / unused
        rgb = nodes.new("ShaderNodeRGB")
        rgb.label = "Tint"
        rgb.outputs[0].default_value = tint
        rgb.location = (COL_MIX + 280, y_cr + 220)
        albedo_sock = _mix_rgba(
            nodes, links, albedo_sock, rgb.outputs[0], 1.0,
            (COL_MIX + 480, y_cr + 120), "Tint × Albedo", blend="MULTIPLY",
        )

    color_mul = _mi_scalar(scalars, "Color Multiply", "ColorMultiply", default=1.0)
    if albedo_sock is not None and abs(float(color_mul) - 1.0) > 0.01:
        mul_n = nodes.new("ShaderNodeMix")
        mul_n.data_type = "RGBA"
        mul_n.blend_type = "MULTIPLY"
        mul_n.label = f"Color Multiply ×{color_mul:g}"
        mul_n.location = (COL_MIX + 480, y_cr - 40)
        mul_n.inputs["Factor"].default_value = 1.0
        links.new(albedo_sock, mul_n.inputs[6])
        c = max(0.0, float(color_mul))
        mul_n.inputs[7].default_value = (c, c, c, 1.0)
        albedo_sock = mul_n.outputs[2]

    # ── Bind Principled ────────────────────────────────────────────────────
    if albedo_sock is not None:
        links.new(albedo_sock, principled.inputs["Base Color"])
    else:
        # Fallback flat tint so empty MIs aren't pure pink
        fallback = tint or (0.45, 0.45, 0.45, 1.0)
        principled.inputs["Base Color"].default_value = fallback

    if rough_sock is not None:
        # Soft remap UE roughness alphas that sit very dark/bright
        links.new(rough_sock, principled.inputs["Roughness"])
    else:
        principled.inputs["Roughness"].default_value = 0.55

    if normal_sock is not None:
        links.new(normal_sock, principled.inputs["Normal"])

    # Architecture trim: NAO.A = opacity mask (Masked + Use Alpha mask)
    use_alpha_mask = _mi_switch(
        switches, "Use Alpha mask", "UseAlphaMask", default=None,
    )
    if is_trim and use_alpha_mask is not False and (
        _is_masked_blend(mi) or use_alpha_mask
    ):
        _, nao_img = _find_env_tex(tex_lookup, "NAO", "NA", "nao")
        nao_alpha_sock = None
        if nao_img is not None and noh1_node is not None and noh1_img is nao_img:
            nao_alpha_sock = noh1_node.outputs["Alpha"]
        elif nao_img is not None:
            nao_clip = _new_tex_image(
                nodes, nao_img, "NAO Opacity", (COL_TEX - 500, y_extra - 100), non_color=True,
            )
            _bind_tex_vec(nao_clip, y_extra - 100)
            nao_alpha_sock = nao_clip.outputs["Alpha"]
        if nao_alpha_sock is not None:
            links.new(nao_alpha_sock, principled.inputs["Alpha"])
            _set_material_alpha_mode(
                mat,
                mode="CLIP",
                threshold=float(mi.get("opacity_clip") or 0.3333),
                two_sided=False,
            )
            try:
                mat["arc_trim_alpha"] = 1
            except Exception:
                pass

    # Setup stamps so Force All / Fix White rebuild stale vent-cell / pre-world-UV graphs.
    try:
        if is_proptrim_atlas:
            mat["arc_proptrim_uv"] = _PROPTRIM_UV_SETUP_V
            # Audit hint: PropTrim atlas without authored UVOffset often samples the
            # default cell (vent grille on Painted sheets / full-slat Wood sheets).
            try:
                mat["arc_proptrim_uv_offset"] = float(uv_offset)
                mat["arc_proptrim_uv_rotate"] = 1 if rotate_uv else 0
                if abs(float(uv_offset)) < 1e-5 and not rotate_uv:
                    mat["arc_proptrim_uv_audit"] = (
                        "no_UVOffset_in_MI — mesh UVs must select atlas cell; "
                        "if this looks like a vent grille, check UVMap / wrong MI"
                    )
                else:
                    mat["arc_proptrim_uv_audit"] = (
                        f"UVOffset={float(uv_offset):.4g} rotate={bool(rotate_uv)}"
                    )
            except Exception:
                pass
        if is_trim:
            mat["arc_trim_setup"] = _TRIM_SETUP_V
    except Exception:
        pass

    # Unconnected leftovers for inspection
    handled = {
        node.image.filepath
        for node in nodes
        if getattr(node, "image", None) is not None
    }
    row_u = y_extra - 200
    for param, (fpath, img) in tex_lookup.items():
        if fpath in handled:
            continue
        # Skip duplicate texture-name aliases (T_Concrete_Wall_05_CR etc.)
        if param.startswith("T_") and any(
            param.endswith(suf) for suf in ("_CR", "_NOH", "_C", "_A", "_X", "_GRM", "_NOM", "_NXX", "_NMX", "_NAO",
                                           "_Color", "_Normal", "_Roughnessmetal", "_Tintmask")
        ):
            continue
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param
        node.interpolation = "Cubic"
        node.location = (COL_TEX - 700, row_u)
        row_u -= 280


def _dump_unconnected_tex(nodes, tex_lookup, handled_paths, loc_x, loc_y):
    """Leave unused MI textures as Image nodes for inspection."""
    row_u = loc_y
    for param, (fpath, img) in tex_lookup.items():
        if fpath in handled_paths:
            continue
        if param.startswith("T_") and any(
            param.endswith(suf) for suf in (
                "_CR", "_NOH", "_C", "_A", "_X", "_GRM", "_NOM", "_NXX", "_NMX",
                "_CA", "_CS", "_NTR", "_NX", "_EXX",
                "_Color", "_Normal", "_Roughnessmetal", "_Tintmask",
            )
        ):
            continue
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param
        node.interpolation = "Cubic"
        node.location = (loc_x, row_u)
        row_u -= 280
    return row_u


# ---------------------------------------------------------------------------
# Foliage texture inference (empty / unresolved MI maps)
# ---------------------------------------------------------------------------

def _stem_tokens(name: str) -> set[str]:
    """Split asset stem into comparable tokens (StonePine_01_Branches → …)."""
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    stem = re.sub(r"^(mi_|m_|sm_|sk_|t_)", "", stem, flags=re.I)
    parts = [p for p in re.split(r"[_\-\s]+", stem.lower()) if p and p not in (
        "01", "02", "03", "04", "a", "b", "c", "d", "south", "north", "east",
        "west", "forest", "the", "of",
    )]
    # Also keep compound plant tokens glued (stonepine → pine)
    extras = set()
    for p in parts:
        for key in ("pine", "oak", "birch", "cypress", "juniper", "aleppo",
                    "heather", "reed", "vine", "leaf", "leaves", "branch",
                    "branches", "trunk", "grass", "bush", "tree"):
            if key in p and key != p:
                extras.add(key)
    return set(parts) | extras


def _foliage_role_from_stem(stem: str) -> str:
    s = (stem or "").lower()
    if "trunk" in s or "bark" in s:
        return "trunk"
    if "billboard" in s or "impostor" in s or "imposter" in s:
        return "billboard"
    if any(k in s for k in ("branch", "leaf", "leaves", "foliage", "needle")):
        return "leaves"
    return "foliage"


def _list_image_files(folder: str) -> list[tuple[str, str]]:
    """Non-recursive [(stem_lower, abs_path), ...] for image textures in folder."""
    out = []
    if not folder or not os.path.isdir(folder):
        return out
    try:
        for fname in os.listdir(folder):
            low = fname.lower()
            if not low.startswith("t_"):
                continue
            if not any(low.endswith(ext) for ext in _TEX_IMAGE_EXTS):
                continue
            stem = os.path.splitext(fname)[0]
            out.append((stem.lower(), os.path.join(folder, fname)))
    except OSError:
        pass
    return out


def _collect_foliage_search_dirs(mi_path: str, psk_path: str = "") -> list[str]:
    """Search order: MI/mesh dir → parent + sibling asset folders → remapped Content → MaterialLibrary."""
    folders: list[str] = []
    seen: set[str] = set()

    def _add(path: str):
        if not path:
            return
        norm = os.path.normcase(os.path.normpath(path))
        if norm in seen or not os.path.isdir(path):
            return
        seen.add(norm)
        folders.append(os.path.normpath(path))

    seeds = []
    for base in (os.path.dirname(mi_path or ""), os.path.dirname(psk_path or "")):
        if base:
            seeds.append(base)
    for seed in seeds:
        _add(seed)
        parent = os.path.dirname(seed)
        if parent:
            _add(parent)
            try:
                for name in os.listdir(parent):
                    sib = os.path.join(parent, name)
                    if os.path.isdir(sib):
                        _add(sib)
            except OSError:
                pass

    # Remap into alternate Content trees (Jsons → Unpack / Desktop dump)
    for seed in list(folders):
        try:
            for alt in utils.remap_path_into_content_dirs(seed):
                _add(alt)
                alt_parent = os.path.dirname(alt)
                if alt_parent:
                    _add(alt_parent)
                    try:
                        for name in os.listdir(alt_parent):
                            sib = os.path.join(alt_parent, name)
                            if os.path.isdir(sib):
                                _add(sib)
                    except OSError:
                        pass
        except Exception:
            pass

    # Material Library textures (bounded top-level + Vegetation-ish)
    try:
        for content_dir in utils.get_content_dirs():
            ml = os.path.join(content_dir, "Pioneer", "MaterialLibrary", "Textures")
            _add(ml)
            if os.path.isdir(ml):
                try:
                    for name in os.listdir(ml):
                        low = name.lower()
                        if any(k in low for k in (
                            "veg", "foliage", "plant", "tree", "leaf", "nature", "decal",
                        )):
                            _add(os.path.join(ml, name))
                except OSError:
                    pass
            # Shared vegetation under Environment is often beside the MI already;
            # also peek MaterialLibrary/Material_Instances Themes if present.
            themes = os.path.join(
                content_dir, "Pioneer", "MaterialLibrary", "Material_Instances", "Themes",
            )
            _add(themes)
    except Exception:
        pass

    return folders


def _score_vegetation_tex(
    tex_stem: str,
    *,
    role: str,
    mi_role: str,
    hint_tokens: set[str],
    suffixes: tuple[str, ...],
) -> float:
    low = tex_stem.lower()
    # Must match packing suffix
    matched_suf = ""
    for suf in suffixes:
        if low.endswith(suf):
            matched_suf = suf
            break
    if not matched_suf:
        return -1.0

    score = 10.0
    # Prefer first / more specific packing suffixes
    score += max(0, 4 - suffixes.index(matched_suf)) * 0.5

    base = low[: -len(matched_suf)]
    tex_tokens = _stem_tokens(base)
    if hint_tokens and tex_tokens:
        overlap = hint_tokens & tex_tokens
        score += 3.0 * len(overlap)
        # Fuzzy leftover
        ratio = difflib.SequenceMatcher(
            None, "".join(sorted(hint_tokens)), "".join(sorted(tex_tokens)),
        ).ratio()
        score += 2.0 * ratio

    billboard = "billboard" in low or "impostor" in low or "imposter" in low
    trunkish = "trunk" in low or "bark" in low or low.endswith("_cr") or "_cr_" in low
    leafish = any(k in low for k in ("leaf", "leaves", "branch", "branches", "needle", "foliage"))

    if mi_role == "leaves":
        if leafish:
            score += 6.0
        if billboard:
            score -= 4.0
        if trunkish and not leafish:
            score -= 3.0
    elif mi_role == "trunk":
        if trunkish:
            score += 6.0
        if leafish and not trunkish:
            score -= 3.0
        if billboard:
            score -= 5.0
    elif mi_role == "billboard":
        if billboard:
            score += 6.0
    else:
        if leafish:
            score += 2.0
        if billboard:
            score -= 1.5

    if role == "albedo" and matched_suf == "_ca":
        score += 1.5
    if role == "normal" and matched_suf in ("_ntx", "_ntr"):
        score += 1.5

    return score


def infer_vegetation_texture(
    role: str,
    mi_path: str,
    psk_path: str = "",
    mi_stem: str = "",
    exclude_paths: set | None = None,
) -> tuple[str, str]:
    """Find a plausible vegetation albedo/normal when MI maps are missing.

    Returns ``(abs_path, reason)`` or ``("", "")``. Soft-fails with empty strings.
    """
    log = utils.get_logger()
    role = (role or "albedo").lower()
    stem = mi_stem or os.path.splitext(os.path.basename(mi_path or ""))[0]
    mi_role = _foliage_role_from_stem(stem)
    hint = _stem_tokens(stem) | _stem_tokens(os.path.basename(psk_path or ""))
    if mi_role == "trunk":
        suffixes = (
            _FOLIAGE_TRUNK_ALBEDO_SUFFIXES if role == "albedo"
            else _FOLIAGE_TRUNK_NORMAL_SUFFIXES
        )
    else:
        suffixes = (
            _FOLIAGE_ALBEDO_SUFFIXES if role == "albedo"
            else _FOLIAGE_NORMAL_SUFFIXES
        )

    exclude = {os.path.normcase(os.path.normpath(p)) for p in (exclude_paths or set()) if p}
    best_path = ""
    best_score = 0.0
    best_reason = ""

    for folder in _collect_foliage_search_dirs(mi_path, psk_path):
        for tex_stem, fpath in _list_image_files(folder):
            key = os.path.normcase(os.path.normpath(fpath))
            if key in exclude:
                continue
            score = _score_vegetation_tex(
                tex_stem, role=role, mi_role=mi_role, hint_tokens=hint, suffixes=suffixes,
            )
            if score < 8.0:
                continue
            if score > best_score:
                best_score = score
                best_path = fpath
                best_reason = f"{os.path.basename(folder)}/{os.path.basename(fpath)} score={score:.1f}"

    if best_path:
        log.info(
            "Foliage tex fallback (%s) for '%s' → %s",
            role, stem, best_reason,
        )
        return best_path, best_reason

    log.warning(
        "Foliage tex fallback (%s) found nothing for '%s' (searched adjacent/library)",
        role, stem,
    )
    return "", ""


def _load_fallback_tex_image(fpath: str):
    """Load via session image cache; returns Blender image or None."""
    if not fpath or not os.path.isfile(fpath):
        return None
    return _load_image_cached(fpath)


def _stamp_tex_fallback(mat, role: str, fpath: str, reason: str = ""):
    """Stamp custom props when a fallback texture was wired."""
    try:
        mat["arc_tex_fallback"] = True
        mat[f"arc_tex_fallback_{role}"] = fpath or ""
        if reason:
            mat[f"arc_tex_fallback_{role}_src"] = reason
    except Exception:
        pass


def _resolve_foliage_maps(
    mi: dict,
    mi_path: str,
    psk_path: str = "",
    tex_lookup: dict | None = None,
) -> tuple:
    """Return (albedo_img, normal_img, trunk_c_img, trunk_n_img, fallback_meta)."""
    tex_lookup = tex_lookup if tex_lookup is not None else {}
    albedo_keys = (
        "1. CA", "CA", "ColorAlpha", "BaseColor", "PM_Diffuse", "CR", "CR Texture",
    )
    normal_keys = (
        "1. NTR", "NTR", "Normals", "NormalMap", "Normal", "NOH", "PM_Normals",
        "NTX", "1. NTX",
    )
    trunk_c_keys = ("TrunkBaseColor", "Trunk CA", "TrunkCR")
    trunk_n_keys = ("TrunkNormal", "Trunk NTR", "TrunkNOH")

    _, albedo_img = _find_env_tex(tex_lookup, *albedo_keys)
    _, normal_img = _find_env_tex(tex_lookup, *normal_keys)
    _, trunk_c_img = _find_env_tex(tex_lookup, *trunk_c_keys)
    _, trunk_n_img = _find_env_tex(tex_lookup, *trunk_n_keys)
    if trunk_n_img is not None and trunk_n_img == normal_img:
        trunk_n_img = None

    meta: dict[str, str] = {}
    stem = os.path.splitext(os.path.basename(mi_path or ""))[0]
    used = set()
    for _p, (fp, _im) in tex_lookup.items():
        if fp:
            used.add(fp)

    if albedo_img is None:
        path, reason = infer_vegetation_texture(
            "albedo", mi_path, psk_path, mi_stem=stem, exclude_paths=used,
        )
        img = _load_fallback_tex_image(path)
        if img is not None:
            albedo_img = img
            meta["albedo"] = path
            meta["albedo_src"] = reason
            used.add(path)

    if normal_img is None:
        path, reason = infer_vegetation_texture(
            "normal", mi_path, psk_path, mi_stem=stem, exclude_paths=used,
        )
        img = _load_fallback_tex_image(path)
        if img is not None:
            normal_img = img
            meta["normal"] = path
            meta["normal_src"] = reason
            used.add(path)

    # Trunk role: if this is a trunk MI routed as foliage and still missing, try trunk pack
    if _foliage_role_from_stem(stem) == "trunk":
        if albedo_img is None:
            path, reason = infer_vegetation_texture(
                "albedo", mi_path, psk_path, mi_stem=stem, exclude_paths=used,
            )
            img = _load_fallback_tex_image(path)
            if img is not None:
                albedo_img = img
                meta["albedo"] = path
                meta["albedo_src"] = reason

    return albedo_img, normal_img, trunk_c_img, trunk_n_img, meta


def _setup_foliage_material(mat, mi_path: str, psk_path: str = ""):
    """Leaves / grass / vines / trees — BaseColor/CA + Normals/NTR, alpha clip, two-sided."""
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_FOLIAGE)

    COL_TEX, COL_UTIL, COL_MIX, COL_BSDF, COL_OUT = -1400, -800, -200, 400, 700
    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []
    switches = mi.get("switches") or {}
    log = utils.get_logger()

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    albedo_img, normal_img, trunk_c_img, trunk_n_img, fb_meta = _resolve_foliage_maps(
        mi, mi_path, psk_path, tex_lookup=tex_lookup,
    )
    for role in ("albedo", "normal"):
        if role in fb_meta:
            _stamp_tex_fallback(mat, role, fb_meta[role], fb_meta.get(f"{role}_src", ""))

    albedo_sock = None
    alpha_sock = None
    if albedo_img:
        label = "Foliage Color/CA"
        if "albedo" in fb_meta:
            label = "Foliage Color/CA (fallback)"
        alb = _new_tex_image(nodes, albedo_img, label, (COL_TEX, 400))
        albedo_sock = alb.outputs["Color"]
        alpha_sock = alb.outputs["Alpha"]
        links.new(albedo_sock, principled.inputs["Base Color"])
        links.new(alpha_sock, principled.inputs["Alpha"])
    else:
        # Prefer authored leaf tint over pure white when no texture exists
        tint = _mi_colour(
            colours,
            "Leaves Tint", "1. Tint", "Tint", "MossTint", "Raytrace Color",
            default=(0.25, 0.4, 0.15, 1.0),
        )
        principled.inputs["Base Color"].default_value = tint
        log.warning(
            "Foliage MI '%s' has no resolvable/fallback albedo — using tint colour",
            os.path.basename(mi_path),
        )

    if normal_img:
        label = "Foliage Normal/NTR"
        if "normal" in fb_meta:
            label = "Foliage Normal/NTR (fallback)"
        n_node = _new_tex_image(
            nodes, normal_img, label, (COL_TEX, 50), non_color=True,
        )
        n_str = _mi_scalar(
            scalars, "4. Normal Strength", "Normal Strength", default=1.0,
        )
        n_str = min(max(n_str, 0.0), 2.5)
        n_sock = _wire_normal_map(
            nodes, links, n_node.outputs["Color"], (COL_MIX, 50), strength=n_str,
            label="Foliage Normal",
        )
        links.new(n_sock, principled.inputs["Normal"])

    if trunk_c_img and albedo_sock is not None:
        trunk = _new_tex_image(nodes, trunk_c_img, "Trunk BaseColor", (COL_TEX - 400, -350))
        fac = _mi_scalar(scalars, "BlendRange", "Trunk Blend", default=0.0)
        fac = min(max(float(fac), 0.0), 0.35) if fac else 0.0
        if fac > 0.001:
            mixed = _mix_rgba(
                nodes, links, albedo_sock, trunk.outputs["Color"], fac,
                (COL_MIX, 250), "Leaf↔Trunk Albedo",
            )
            _unlink_input(links, principled.inputs["Base Color"])
            links.new(mixed, principled.inputs["Base Color"])
    elif trunk_c_img:
        _new_tex_image(nodes, trunk_c_img, "Trunk BaseColor", (COL_TEX - 400, -350))
    if trunk_n_img:
        _new_tex_image(nodes, trunk_n_img, "Trunk Normal", (COL_TEX - 400, -650), non_color=True)

    # Subsurface stub (StonePine: Subsurface Color; also 1. SS / SS Color)
    ss = _mi_colour(
        colours, "Subsurface Color", "1. SS", "Subsurface", "SS Color", default=None,
    )
    if ss is not None:
        # HDR subsurface colours (e.g. 5,6,2) → normalize for radius, keep weight soft
        mx = max(ss[0], ss[1], ss[2], 0.01)
        radius = (
            max(ss[0] / mx, 0.05),
            max(ss[1] / mx, 0.05),
            max(ss[2] / mx, 0.05),
        )
        try:
            principled.inputs["Subsurface Weight"].default_value = 0.15
            principled.inputs["Subsurface Radius"].default_value = radius
        except Exception:
            try:
                principled.inputs["Subsurface"].default_value = 0.15
            except Exception:
                pass

    rough = _mi_scalar(
        scalars,
        "Roughness Leaves", "Roughness Twigs", "3. Roughness", "Roughness",
        default=0.65,
    )
    principled.inputs["Roughness"].default_value = min(max(abs(float(rough)), 0.05), 1.0)

    clip = float(mi.get("opacity_clip") or 0.3333)
    use_hashed = bool(mi.get("dithered_lod")) or clip >= 0.55
    _set_material_alpha_mode(
        mat,
        mode="HASHED" if use_hashed else "CLIP",
        threshold=clip,
        two_sided=True,
    )

    # Leaves Tint / Tint — StonePine authors Use Tint + Leaves Tint
    tint = _mi_colour(
        colours,
        "Leaves Tint", "1. Tint", "Tint", "BlueGate Custom Mask Grass Tint",
        default=None,
    )
    use_tint = _mi_switch(switches, "1. Use Tint", "Use Tint", default=None)
    tint_amt = _mi_scalar(
        scalars, "Tint Leaves Amount", "Tint Amount", "Tint Branches Amount", default=0.0,
    )
    if tint is not None and albedo_sock is not None and (
        use_tint is True or (use_tint is None and tint_amt > 0.01)
    ):
        rgb = nodes.new("ShaderNodeRGB")
        rgb.label = "Leaves Tint"
        rgb.outputs[0].default_value = tint
        rgb.location = (COL_UTIL, 500)
        fac = 1.0 if use_tint else min(max(tint_amt, 0.0), 1.0)
        mul = _mix_rgba(
            nodes, links, albedo_sock, rgb.outputs[0], fac,
            (COL_MIX, 400), "Tint × Leaf", blend="MULTIPLY",
        )
        _unlink_input(links, principled.inputs["Base Color"])
        links.new(mul, principled.inputs["Base Color"])

    handled = {
        node.image.filepath
        for node in nodes
        if getattr(node, "image", None) is not None
    }
    _dump_unconnected_tex(nodes, tex_lookup, handled, COL_TEX - 700, -900)


def _collect_sand_search_dirs(mi_path: str, psk_path: str = "") -> list[str]:
    """MI/mesh dir → parent/siblings → **/Dunes/Textures → remapped Content roots."""
    folders: list[str] = []
    seen: set[str] = set()

    def _add(path: str):
        if not path:
            return
        norm = os.path.normcase(os.path.normpath(path))
        if norm in seen or not os.path.isdir(path):
            return
        seen.add(norm)
        folders.append(os.path.normpath(path))

    seeds = []
    for base in (os.path.dirname(mi_path or ""), os.path.dirname(psk_path or "")):
        if base:
            seeds.append(base)
    for seed in seeds:
        _add(seed)
        parent = os.path.dirname(seed)
        if parent:
            _add(parent)
            # Sibling mesh folders under Dunes/Meshes
            try:
                for name in os.listdir(parent):
                    sib = os.path.join(parent, name)
                    if os.path.isdir(sib):
                        _add(sib)
            except OSError:
                pass
            # Climb toward South/Dunes and add Textures/
            climb = parent
            for _ in range(6):
                if not climb:
                    break
                leaf = os.path.basename(climb).lower()
                if leaf in ("dunes", "south", "environment"):
                    _add(os.path.join(climb, "Textures"))
                    if leaf == "dunes":
                        _add(os.path.join(climb, "Textures"))
                        break
                climb = os.path.dirname(climb)

    for seed in list(folders):
        try:
            for alt in utils.remap_path_into_content_dirs(seed):
                _add(alt)
                alt_parent = os.path.dirname(alt)
                if alt_parent:
                    _add(alt_parent)
                    _add(os.path.join(alt_parent, "Textures"))
        except Exception:
            pass

    try:
        for content_dir in utils.get_content_dirs():
            dunes_tex = os.path.join(
                content_dir, "Pioneer", "Environment", "South", "Dunes", "Textures",
            )
            _add(dunes_tex)
            base_tex = os.path.join(
                content_dir, "Pioneer", "Environment", "South", "Base", "Textures",
            )
            _add(base_tex)
    except Exception:
        pass

    return folders


def _score_sand_tex(
    tex_stem: str,
    *,
    role: str,
    hint_tokens: set[str],
    suffixes: tuple[str, ...],
) -> float:
    low = tex_stem.lower()
    matched_suf = ""
    for suf in suffixes:
        if low.endswith(suf):
            matched_suf = suf
            break
    if not matched_suf:
        return 0.0

    score = 8.0
    if "sand" in low:
        score += 4.0
    if "dune" in low:
        score += 3.5
    if "beach" in low or "desert" in low:
        score += 2.0
    if "twig" in low:
        score += 1.0
    # Prefer base dune packs over slide/detail for macro albedo
    if "base_sand_dunes" in low or "south_base_sand" in low:
        score += 2.5
    if "ground_sand" in low:
        score += 2.0 if role in ("micro_albedo", "albedo") else 0.5
    if "detail" in low or "slide" in low or "breakup" in low:
        if role in ("micro_albedo", "fine_normal"):
            score += 1.5
        else:
            score -= 2.0
    if "white desert" in low.replace("_", " ") or "whitedesert" in low:
        score -= 3.0

    tokens = set(re.findall(r"[a-z0-9]+", low))
    overlap = len(tokens & hint_tokens)
    score += min(overlap, 4) * 0.75

    if role in ("albedo", "macro_albedo", "micro_albedo") and matched_suf in ("_ch", "_cr"):
        score += 1.5
    if role in ("normal", "fine_normal") and matched_suf == "_nr":
        score += 2.0
    elif role in ("normal", "fine_normal", "macro_normal") and matched_suf in ("_nh", "_noh", "_n"):
        score += 1.2
    if role == "macro_normal" and matched_suf in ("_n", "_nh"):
        score += 1.5
    if role == "micro_albedo" and ("ground" in low or "detail" in low or "twig" in low):
        score += 2.0
    return score


def map_prefers_sand_ground(map_name: str = "") -> bool:
    """True when CityGroundPlane should use sand BRDF (RivenTides / dunes / desert)."""
    low = (map_name or "").lower().replace(" ", "_")
    if not low:
        return False
    return any(tok in low for tok in _SANDY_MAP_TOKENS)


def _clamp_sand_uv_tiling(raw: float, default: float = 1.0) -> float:
    """MI Tiling for mesh UV — reject absurd values (e.g. 500) that scream-tile."""
    try:
        t = float(raw or 0.0)
    except (TypeError, ValueError):
        t = 0.0
    if t <= 0.0:
        return float(default)
    if t > 32.0:
        # Likely world-cm / mistaken Mapping Scale — keep a readable UV tile
        return 4.0
    return max(0.15, min(16.0, t))


def _find_sand_stem_image(
    preferred_stems: tuple[str, ...],
    search_dirs: list[str],
    exclude_paths: set | None = None,
    *,
    load: bool = True,
):
    """Find first matching preferred stem (order preserved) from sand search dirs.

    When ``load`` is False (or bpy.data unavailable), returns ``(None, path, reason)``.
    """
    exclude = {os.path.normcase(os.path.normpath(p)) for p in (exclude_paths or set()) if p}
    can_load = load and getattr(bpy, "data", None) is not None

    # Index stems once per folder for ordered preference walk
    by_stem: dict[str, tuple[str, str]] = {}
    for folder in search_dirs:
        for tex_stem, fpath in _list_image_files(folder):
            key = os.path.normcase(os.path.normpath(fpath))
            if key in exclude:
                continue
            low = tex_stem.lower()
            if low not in by_stem:
                by_stem[low] = (fpath, folder)

    for stem in preferred_stems:
        target = stem.lower()
        hit = by_stem.get(target)
        if hit is None:
            # Prefix fallback (e.g. stem without exact export)
            for low, (fpath, folder) in by_stem.items():
                if low.startswith(target):
                    hit = (fpath, folder)
                    break
        if hit is None:
            continue
        fpath, folder = hit
        reason = f"{os.path.basename(folder)}/{os.path.basename(fpath)}"
        if not can_load:
            return None, fpath, reason
        img = _load_fallback_tex_image(fpath)
        if img is not None:
            return img, fpath, reason
    return None, "", ""


def infer_sand_texture(
    role: str,
    mi_path: str,
    psk_path: str = "",
    mi_stem: str = "",
    exclude_paths: set | None = None,
) -> tuple[str, str]:
    """Find sand/dune albedo or normal when MI Texture params are empty / unresolved.

    Returns ``(abs_path, reason)`` or ``("", "")``.
    """
    log = utils.get_logger()
    role = (role or "albedo").lower()
    stem = mi_stem or os.path.splitext(os.path.basename(mi_path or ""))[0]
    hint = _stem_tokens(stem) | _stem_tokens(os.path.basename(psk_path or ""))
    hint |= {"sand", "dune", "south"}
    if role in ("albedo", "macro_albedo", "micro_albedo"):
        suffixes = _SAND_ALBEDO_SUFFIXES
    else:
        suffixes = _SAND_NORMAL_SUFFIXES

    # Prefer curated stems first for dual-scale roles
    dirs = _collect_sand_search_dirs(mi_path, psk_path)
    exclude = {os.path.normcase(os.path.normpath(p)) for p in (exclude_paths or set()) if p}
    preferred = ()
    if role in ("albedo", "macro_albedo"):
        preferred = _SAND_MACRO_ALBEDO_STEMS
    elif role == "micro_albedo":
        preferred = _SAND_MICRO_ALBEDO_STEMS
    elif role in ("normal", "fine_normal"):
        preferred = _SAND_FINE_NORMAL_STEMS
    elif role == "macro_normal":
        preferred = _SAND_MACRO_NORMAL_STEMS
    if preferred:
        _img, path, reason = _find_sand_stem_image(
            preferred, dirs, exclude, load=False,
        )
        if path:
            log.info("Sand tex preferred (%s) for '%s' → %s", role, stem, reason)
            return path, reason

    best_path = ""
    best_score = 0.0
    best_reason = ""

    for folder in dirs:
        for tex_stem, fpath in _list_image_files(folder):
            key = os.path.normcase(os.path.normpath(fpath))
            if key in exclude:
                continue
            score = _score_sand_tex(
                tex_stem, role=role, hint_tokens=hint, suffixes=suffixes,
            )
            if score < 8.0:
                continue
            if score > best_score:
                best_score = score
                best_path = fpath
                best_reason = f"{os.path.basename(folder)}/{os.path.basename(fpath)} score={score:.1f}"

    if best_path:
        log.info(
            "Sand tex fallback (%s) for '%s' → %s",
            role, stem, best_reason,
        )
        return best_path, best_reason

    log.warning(
        "Sand tex fallback (%s) found nothing for '%s' (searched Dunes/Textures)",
        role, stem,
    )
    return "", ""


def _resolve_sand_maps(
    mi: dict,
    mi_path: str,
    psk_path: str = "",
    tex_lookup: dict | None = None,
) -> dict:
    """Resolve dual-scale sand maps.

    Returns dict with keys: macro_albedo, micro_albedo, fine_normal, macro_normal,
    albedo_param, meta (fallback paths/reasons).
    """
    tex_lookup = tex_lookup if tex_lookup is not None else {}
    albedo_param, albedo_img = _find_env_tex(tex_lookup, *_SAND_ALBEDO_KEYS)
    normal_param, normal_img = _find_env_tex(tex_lookup, *_SAND_NORMAL_KEYS)
    if normal_img is not None and normal_img == albedo_img:
        for key in _SAND_NORMAL_KEYS:
            if key == albedo_param:
                continue
            hit, img = _find_env_tex(tex_lookup, key)
            if img is not None and img != albedo_img:
                normal_param, normal_img = hit, img
                break

    meta: dict[str, str] = {}
    stem = os.path.splitext(os.path.basename(mi_path or ""))[0]
    used = {fp for _p, (fp, _im) in tex_lookup.items() if fp}

    macro_alb = albedo_img
    if macro_alb is None:
        path, reason = infer_sand_texture(
            "macro_albedo", mi_path, psk_path, mi_stem=stem, exclude_paths=used,
        )
        img = _load_fallback_tex_image(path)
        if img is not None:
            macro_alb = img
            meta["macro_albedo"] = path
            meta["macro_albedo_src"] = reason
            used.add(path)

    micro_alb = None
    path, reason = infer_sand_texture(
        "micro_albedo", mi_path, psk_path, mi_stem=stem, exclude_paths=used,
    )
    img = _load_fallback_tex_image(path)
    if img is not None and img != macro_alb:
        micro_alb = img
        meta["micro_albedo"] = path
        meta["micro_albedo_src"] = reason
        used.add(path)

    fine_n = normal_img
    if fine_n is None:
        path, reason = infer_sand_texture(
            "fine_normal", mi_path, psk_path, mi_stem=stem, exclude_paths=used,
        )
        img = _load_fallback_tex_image(path)
        if img is not None:
            fine_n = img
            meta["fine_normal"] = path
            meta["fine_normal_src"] = reason
            used.add(path)

    macro_n = None
    path, reason = infer_sand_texture(
        "macro_normal", mi_path, psk_path, mi_stem=stem, exclude_paths=used,
    )
    img = _load_fallback_tex_image(path)
    if img is not None and img != fine_n:
        macro_n = img
        meta["macro_normal"] = path
        meta["macro_normal_src"] = reason

    # Back-compat stamps for verify / UI
    if "macro_albedo" in meta:
        meta["albedo"] = meta["macro_albedo"]
        meta["albedo_src"] = meta.get("macro_albedo_src", "")
    if "fine_normal" in meta:
        meta["normal"] = meta["fine_normal"]
        meta["normal_src"] = meta.get("fine_normal_src", "")

    return {
        "macro_albedo": macro_alb,
        "micro_albedo": micro_alb,
        "fine_normal": fine_n,
        "macro_normal": macro_n,
        "albedo_param": albedo_param or "",
        "normal_param": normal_param or "",
        "meta": meta,
    }


def _sand_img_stem(img, tex_lookup: dict, meta: dict, meta_key: str) -> str:
    path = ""
    if img is not None:
        path = next((fp for _p, (fp, im) in tex_lookup.items() if im == img), "") or ""
    if not path:
        path = meta.get(meta_key, "") or ""
    return os.path.splitext(os.path.basename(path))[0].lower()


def _wire_sand_breakup_noise(nodes, links, vector_sock, loc, scale: float = 2.5):
    """Noise Fac for dual-albedo / roughness variation (Musgrave replacement)."""
    noise = nodes.new("ShaderNodeTexNoise")
    noise.label = "Sand Breakup"
    noise.location = loc
    try:
        noise.noise_dimensions = "2D"
    except Exception:
        pass
    try:
        noise.inputs["Scale"].default_value = float(scale)
        noise.inputs["Detail"].default_value = 6.0
        noise.inputs["Roughness"].default_value = 0.55
        if "Distortion" in noise.inputs:
            noise.inputs["Distortion"].default_value = 0.15
    except Exception:
        pass
    if vector_sock is not None:
        links.new(vector_sock, noise.inputs["Vector"])
    return noise.outputs["Fac"]


def _wire_dual_scale_sand(
    mat,
    *,
    maps: dict,
    scalars: dict | None = None,
    colours: list | None = None,
    tex_lookup: dict | None = None,
    coord_mode: str = "uv",
    uv_tiling: float = 1.0,
    macro_meters: float = 12.0,
    micro_meters: float = 1.5,
    apply_tint: bool = True,
    masked_alpha: bool = False,
    opacity_clip: float = 0.3333,
):
    """Build dual-scale sand Principled graph. Macro dunes = geometry; micro = BRDF.

    coord_mode:
      - ``uv``: mesh UV × uv_tiling (props / piles)
      - ``object``: Object coords in meters (CityGroundPlane / landscape)
    Never plugs normals into Displacement — macro relief comes from heightmap mesh.
    """
    scalars = scalars or {}
    colours = colours or []
    tex_lookup = tex_lookup or {}
    meta = maps.get("meta") or {}
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_SAND)

    COL_COORD, COL_TEX, COL_MIX, COL_BSDF, COL_OUT = -1800, -1100, -250, 450, 750

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    tc = nodes.new("ShaderNodeTexCoord")
    tc.location = (COL_COORD, 100)
    if coord_mode == "object":
        base_vec = tc.outputs["Object"]
        macro_scale = 1.0 / max(float(macro_meters), 0.25)
        micro_scale = 1.0 / max(float(micro_meters), 0.05)
    else:
        base_vec = tc.outputs["UV"]
        tile = _clamp_sand_uv_tiling(uv_tiling, default=1.0)
        macro_scale = tile
        micro_scale = tile * 6.0

    macro_map = nodes.new("ShaderNodeMapping")
    macro_map.label = f"Macro ×{macro_scale:g}"
    macro_map.location = (COL_COORD + 220, 220)
    macro_map.inputs["Scale"].default_value = (macro_scale, macro_scale, macro_scale)
    links.new(base_vec, macro_map.inputs["Vector"])
    macro_vec = macro_map.outputs["Vector"]

    micro_map = nodes.new("ShaderNodeMapping")
    micro_map.label = f"Micro ×{micro_scale:g}"
    micro_map.location = (COL_COORD + 220, -80)
    micro_map.inputs["Scale"].default_value = (micro_scale, micro_scale, micro_scale)
    links.new(base_vec, micro_map.inputs["Vector"])
    micro_vec = micro_map.outputs["Vector"]

    breakup = _wire_sand_breakup_noise(
        nodes, links, macro_vec, (COL_TEX - 200, 520), scale=2.2,
    )

    macro_alb = maps.get("macro_albedo")
    micro_alb = maps.get("micro_albedo")
    fine_n = maps.get("fine_normal")
    macro_n = maps.get("macro_normal")

    albedo_sock = None
    rough_sock = None
    macro_a_node = None

    if macro_alb is not None:
        label = "Sand Macro (dune CR/CH)"
        if "macro_albedo" in meta or "albedo" in meta:
            label = "Sand Macro (fallback)"
        macro_a_node = _new_tex_image(nodes, macro_alb, label, (COL_TEX, 280))
        links.new(macro_vec, macro_a_node.inputs["Vector"])
        albedo_sock = macro_a_node.outputs["Color"]
        m_stem = _sand_img_stem(macro_alb, tex_lookup, meta, "macro_albedo")
        pl = (maps.get("albedo_param") or "").lower()
        if m_stem.endswith("_cr") or pl in ("cr", "cr texture"):
            rough_sock = macro_a_node.outputs["Alpha"]

    if micro_alb is not None and albedo_sock is not None:
        micro_a_node = _new_tex_image(
            nodes, micro_alb, "Sand Micro (grain CR/CH)", (COL_TEX, 80),
        )
        links.new(micro_vec, micro_a_node.inputs["Vector"])
        albedo_sock = _mix_rgba(
            nodes, links, albedo_sock, micro_a_node.outputs["Color"], breakup,
            (COL_MIX - 80, 220), "Macro↔Micro Albedo",
        )
        if rough_sock is None:
            mi_stem = _sand_img_stem(micro_alb, tex_lookup, meta, "micro_albedo")
            if mi_stem.endswith("_cr"):
                rough_sock = micro_a_node.outputs["Alpha"]
    elif micro_alb is not None and albedo_sock is None:
        micro_a_node = _new_tex_image(
            nodes, micro_alb, "Sand Micro (grain)", (COL_TEX, 80),
        )
        links.new(micro_vec, micro_a_node.inputs["Vector"])
        albedo_sock = micro_a_node.outputs["Color"]
        macro_a_node = micro_a_node

    if albedo_sock is not None:
        links.new(albedo_sock, principled.inputs["Base Color"])
    else:
        tint = _mi_colour(
            colours,
            "BaseColor Tint", "Tint", "Color", "BaseColor",
            default=(0.72, 0.62, 0.45, 1.0),
        )
        principled.inputs["Base Color"].default_value = tint

    if apply_tint and albedo_sock is not None:
        tint = _mi_colour(colours, "BaseColor Tint", "Tint", default=None)
        if tint is not None:
            tr = min(max(float(tint[0]), 0.0), 2.0)
            tg = min(max(float(tint[1]), 0.0), 2.0)
            tb = min(max(float(tint[2]), 0.0), 2.0)
            rgb = nodes.new("ShaderNodeRGB")
            rgb.label = "BaseColor Tint"
            rgb.outputs[0].default_value = (tr, tg, tb, 1.0)
            rgb.location = (COL_MIX - 200, 450)
            mul = _mix_rgba(
                nodes, links, albedo_sock, rgb.outputs[0], 1.0,
                (COL_MIX, 380), "Tint × Sand", blend="MULTIPLY",
            )
            _unlink_input(links, principled.inputs["Base Color"])
            links.new(mul, principled.inputs["Base Color"])
            albedo_sock = mul
            try:
                mat["arc_sand_tint"] = [tr, tg, tb, 1.0]
            except Exception:
                pass

    # Normals: fine grain + optional larger dune normal — never Displacement
    n_str = _mi_scalar(scalars, "Normal Strength", "Slope Normal Strength", default=1.0)
    n_str = min(max(float(n_str), 0.0), 2.5)
    normal_sock = None
    fine_n_node = None
    if fine_n is not None:
        fine_n_node = _new_tex_image(
            nodes, fine_n, "Sand Fine Normal (NR/NOH)", (COL_TEX, -200), non_color=True,
        )
        links.new(micro_vec, fine_n_node.inputs["Vector"])
        normal_sock = _wire_normal_map(
            nodes, links, fine_n_node.outputs["Color"], (COL_MIX - 40, -120),
            strength=n_str, label="Fine Sand N",
        )
        n_stem = _sand_img_stem(fine_n, tex_lookup, meta, "fine_normal")
        if n_stem.endswith("_nr") and rough_sock is None:
            rough_sock = fine_n_node.outputs["Alpha"]

    if macro_n is not None:
        macro_n_node = _new_tex_image(
            nodes, macro_n, "Sand Macro Normal (N/NH)", (COL_TEX, -480), non_color=True,
        )
        links.new(macro_vec, macro_n_node.inputs["Vector"])
        macro_n_sock = _wire_normal_map(
            nodes, links, macro_n_node.outputs["Color"], (COL_MIX - 40, -360),
            strength=min(n_str * 0.55, 1.2), label="Macro Sand N",
        )
        if normal_sock is not None:
            normal_sock = _mix_normals_vec(
                nodes, links, normal_sock, macro_n_sock, 0.35,
                (COL_MIX + 160, -200), "Fine+Macro N",
            )
        else:
            normal_sock = macro_n_sock

    # Optional subtle bump from CH luminance only (not NOR / not Displacement Height)
    if macro_alb is not None and macro_a_node is not None:
        m_stem = _sand_img_stem(macro_alb, tex_lookup, meta, "macro_albedo")
        if m_stem.endswith("_ch"):
            bump = nodes.new("ShaderNodeBump")
            bump.label = "CH micro-bump (subtle)"
            bump.location = (COL_MIX + 160, -420)
            bump.inputs["Strength"].default_value = 0.08
            bump.inputs["Distance"].default_value = 0.04
            links.new(macro_a_node.outputs["Color"], bump.inputs["Height"])
            if normal_sock is not None:
                links.new(normal_sock, bump.inputs["Normal"])
            normal_sock = bump.outputs["Normal"]

    if normal_sock is not None:
        links.new(normal_sock, principled.inputs["Normal"])

    # Roughness: packed alpha + slight noise variation (avoid fixed 1.0 / washed Brightness)
    if rough_sock is not None:
        rough_var = _mix_float(
            nodes, links, rough_sock, breakup, 0.12,
            (COL_MIX + 80, 40), "Rough + noise",
        )
        # Keep sand fairly rough — MapRange soft clamp
        ramp = nodes.new("ShaderNodeMapRange")
        ramp.label = "Sand Rough Clamp"
        ramp.location = (COL_MIX + 260, 40)
        ramp.clamp = True
        ramp.inputs["From Min"].default_value = 0.0
        ramp.inputs["From Max"].default_value = 1.0
        ramp.inputs["To Min"].default_value = 0.55
        ramp.inputs["To Max"].default_value = 0.95
        links.new(rough_var, ramp.inputs["Value"])
        links.new(ramp.outputs["Result"], principled.inputs["Roughness"])
    else:
        principled.inputs["Roughness"].default_value = 0.82

    if masked_alpha and macro_a_node is not None:
        links.new(macro_a_node.outputs["Alpha"], principled.inputs["Alpha"])
        _set_material_alpha_mode(
            mat, mode="CLIP", threshold=float(opacity_clip), two_sided=False,
        )

    try:
        mat["arc_sand_dual_scale"] = 1
        mat["arc_sand_coord"] = coord_mode
        mat["arc_sand_macro_scale"] = float(macro_scale)
        mat["arc_sand_micro_scale"] = float(micro_scale)
        if coord_mode == "object":
            mat["arc_sand_macro_meters"] = float(macro_meters)
            mat["arc_sand_micro_meters"] = float(micro_meters)
        else:
            mat["arc_sand_tiling"] = float(uv_tiling)
    except Exception:
        pass

    return principled, out_node


def _setup_sand_material(mat, mi_path: str, psk_path: str = ""):
    """Sand piles / dune splines / desert ground — dual-scale CH/CR + NR with fallback."""
    mi = _parse_flat_mi_json(mi_path)
    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []
    log = utils.get_logger()

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    for folder in _collect_sand_search_dirs(mi_path, psk_path)[:8]:
        if folder not in local_folders:
            local_folders.append(folder)

    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)
    maps = _resolve_sand_maps(mi, mi_path, psk_path, tex_lookup=tex_lookup)
    fb_meta = maps.get("meta") or {}
    for role in ("albedo", "normal", "macro_albedo", "micro_albedo", "fine_normal"):
        if role in fb_meta:
            _stamp_tex_fallback(mat, role, fb_meta[role], fb_meta.get(f"{role}_src", ""))

    raw_tile = _mi_scalar(scalars, "Tiling", default=1.0)
    uv_tiling = _clamp_sand_uv_tiling(raw_tile, default=1.0)

    if maps.get("macro_albedo") is None and maps.get("micro_albedo") is None:
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()
        _stamp_mi_family(mat, FAMILY_SAND)
        principled = nodes.new("ShaderNodeBsdfPrincipled")
        principled.location = (400, 0)
        out_node = nodes.new("ShaderNodeOutputMaterial")
        out_node.location = (700, 0)
        links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])
        tint = _mi_colour(
            colours,
            "BaseColor Tint", "Tint", "Color", "BaseColor",
            default=(0.72, 0.62, 0.45, 1.0),
        )
        principled.inputs["Base Color"].default_value = tint
        principled.inputs["Roughness"].default_value = 0.85
        log.warning(
            "Sand MI '%s' has no resolvable/fallback albedo — using tint",
            os.path.basename(mi_path),
        )
        return

    _wire_dual_scale_sand(
        mat,
        maps=maps,
        scalars=scalars,
        colours=colours,
        tex_lookup=tex_lookup,
        coord_mode="uv",
        uv_tiling=uv_tiling,
        apply_tint=True,
        masked_alpha=_is_masked_blend(mi),
        opacity_clip=float(mi.get("opacity_clip") or 0.3333),
    )

    if _is_masked_blend(mi):
        try:
            already_clip = str(getattr(mat, "blend_method", "") or "").upper() == "CLIP"
        except Exception:
            already_clip = False
        if not already_clip:
            _set_material_alpha_mode(
                mat,
                mode="CLIP",
                threshold=float(mi.get("opacity_clip") or 0.3333),
                two_sided=False,
            )

    handled = {
        node.image.filepath
        for node in mat.node_tree.nodes
        if getattr(node, "image", None) is not None
    }
    _dump_unconnected_tex(mat.node_tree.nodes, tex_lookup, handled, -1600, -700)


def apply_heightmap_void_mask_to_material(
    mat: bpy.types.Material,
    mask_img: bpy.types.Image | None,
) -> bool:
    """Wire void mask → Principled Alpha (CLIP). No-op when mask is None.

    Used by CityGroundPlane create/reload and Apply Sand so missing-data
    regions stay clipped after the sand node tree is rebuilt.
    """
    if mat is None or mask_img is None or not getattr(mat, "use_nodes", False):
        return False
    nt = mat.node_tree
    if nt is None:
        return False
    nodes, links = nt.nodes, nt.links

    for node in list(nodes):
        if str(getattr(node, "label", "") or "").startswith("ARC Height Void"):
            nodes.remove(node)

    bsdf = None
    for node in nodes:
        if node.type == "BSDF_PRINCIPLED":
            bsdf = node
            break
    if bsdf is None or "Alpha" not in bsdf.inputs:
        return False

    tex = nodes.new("ShaderNodeTexImage")
    tex.label = "ARC Height Void Mask"
    tex.image = mask_img
    tex.interpolation = "Closest"
    tex.location = (bsdf.location.x - 360, bsdf.location.y - 280)

    alpha_in = bsdf.inputs["Alpha"]
    existing = None
    try:
        if alpha_in.is_linked:
            existing = alpha_in.links[0].from_socket
            links.remove(alpha_in.links[0])
    except Exception:
        existing = None

    if existing is not None:
        mul = nodes.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        mul.label = "ARC Height Void × Alpha"
        mul.location = (bsdf.location.x - 160, bsdf.location.y - 280)
        links.new(tex.outputs["Color"], mul.inputs[0])
        links.new(existing, mul.inputs[1])
        links.new(mul.outputs["Value"], alpha_in)
    else:
        links.new(tex.outputs["Color"], alpha_in)

    _set_material_alpha_mode(mat, mode="CLIP", threshold=0.5, two_sided=False)
    try:
        mat["arc_heightmap_void_masked"] = 1
    except Exception:
        pass
    return True


def apply_sand_to_heightmap_object(
    obj,
    *,
    map_name: str = "",
    force: bool = False,
    macro_meters: float = 12.0,
    micro_meters: float = 1.5,
    ingame_map_path: str = "",
    hlod_color_path: str = "",
    use_map_texturing: bool = True,
) -> bool:
    """Assign landscape sand BRDF to a CityGroundPlane / heightmap object.

    Preserves Displace modifier (heightmap PNG). Replaces surface material only.
    When ``use_map_texturing``, also wires In-Game Map masks + HLOD color palette.
    Returns True when sand was applied.
    """
    if obj is None or getattr(obj, "type", "") != "MESH":
        return False
    name = map_name or str(obj.get("arc_map") or "")
    if not force and not map_prefers_sand_ground(name):
        # Still allow when object already tagged or name empty but user forced via op
        if not obj.get("arc_heightmap_plane"):
            return False
        # Heuristic: displaced heightmap on unknown map — skip unless force
        if not force:
            return False

    mat = setup_landscape_sand_material(
        None,
        map_name=name or "Map",
        macro_meters=macro_meters,
        micro_meters=micro_meters,
        ingame_map_path=ingame_map_path,
        hlod_color_path=hlod_color_path,
        use_map_texturing=use_map_texturing,
    )
    mesh = obj.data
    if mesh.materials:
        mesh.materials[0] = mat
    else:
        mesh.materials.append(mat)
    try:
        obj["arc_sand_ground"] = 1
        if mat.get("arc_ground_map_textured"):
            obj["arc_ground_map_textured"] = 1
    except Exception:
        pass
    # Preserve void alpha clip after sand rebuilds the node tree.
    try:
        mask_name = str(obj.get("arc_heightmap_void_mask") or "")
        mask_img = bpy.data.images.get(mask_name) if mask_name else None
        if mask_img is not None:
            apply_heightmap_void_mask_to_material(mat, mask_img)
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Heightmap ground: In-Game Map masks + HLOD color palette
# ---------------------------------------------------------------------------

def _map_name_stems(map_name: str) -> list[str]:
    """Return ordered stem candidates for Content lookups (Spaceport_01_P → …)."""
    raw = (map_name or "").strip()
    if not raw:
        return []
    base = os.path.splitext(os.path.basename(raw))[0]
    stems: list[str] = []
    seen: set[str] = set()

    def _add(s: str):
        s = (s or "").strip()
        if not s:
            return
        key = s.lower()
        if key in seen:
            return
        seen.add(key)
        stems.append(s)

    _add(base)
    # Drop common UE map suffixes
    cur = base
    for _ in range(3):
        low = cur.lower()
        stripped = cur
        for suf in ("_p", "_wp", "_terrain", "_landscape", "_main"):
            if low.endswith(suf):
                stripped = cur[: -len(suf)]
                break
        if stripped == cur:
            break
        _add(stripped)
        cur = stripped
    # Spaceport_01_P → Spaceport_01 → Spaceport
    if "_" in cur:
        head = cur.rsplit("_", 1)[0]
        if head and not head[-1:].isdigit():
            _add(head)
        elif head:
            _add(head)
    return stems


def _iter_image_files(folder: str):
    if not folder or not os.path.isdir(folder):
        return
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        low = name.lower()
        if not low.endswith(_IMAGE_FILE_EXTS):
            continue
        yield name, os.path.join(folder, name)


def _ground_search_roots(map_name: str = "") -> list[str]:
    """Folders to search for InGameMap / HLOD Color textures."""
    roots: list[str] = []
    seen: set[str] = set()

    def _add(path: str):
        if not path:
            return
        path = os.path.abspath(os.path.normpath(path))
        key = os.path.normcase(path)
        if key in seen:
            return
        if os.path.isdir(path):
            seen.add(key)
            roots.append(path)

    # Scene / workspace hints
    try:
        scene = bpy.context.scene
    except Exception:
        scene = None
    if scene is not None:
        for attr in (
            "arc_placement_mesh_root",
            "arc_placement_workspace",
        ):
            hint = getattr(scene, attr, "") or ""
            if hint:
                try:
                    _add(bpy.path.abspath(hint))
                except Exception:
                    _add(hint)
        try:
            from . import map_placement as mp

            name = (map_name or getattr(scene, "arc_placement_map", "") or "").strip()
            if name and name != "NONE":
                _add(mp.map_output_dir(name, scene))
        except Exception:
            pass

    pioneer = utils.get_pioneer_root()
    content = utils.find_content_dir(pioneer) if pioneer else ""
    if content:
        _add(os.path.join(content, _INGAME_MAP_DIR_REL))
        maps_root = os.path.join(content, "Pioneer", "Maps")
        _add(maps_root)
        for stem in _map_name_stems(map_name):
            map_dir = os.path.join(maps_root, stem)
            _add(map_dir)
            # Common landscape texture folders
            for sub in (
                os.path.join(f"L_{stem}", "Textures"),
                "Textures",
                os.path.join(stem, "Textures"),
            ):
                _add(os.path.join(map_dir, sub))
            # Also scan one level for L_* / Textures
            if os.path.isdir(map_dir):
                try:
                    for child in os.listdir(map_dir):
                        child_path = os.path.join(map_dir, child)
                        if not os.path.isdir(child_path):
                            continue
                        if child.lower().startswith("l_") or child.lower() == "textures":
                            _add(child_path)
                            _add(os.path.join(child_path, "Textures"))
                except OSError:
                    pass

    _add(_GROUND_MAP_REFS_DIR)
    return roots


def resolve_ingame_map_texture(
    map_name: str = "",
    hint: str = "",
) -> tuple[str, str]:
    """Find T_InGameMap_* PNG for the map. Returns ``(path, reason)`` or ``("", "")``."""
    hint = (hint or "").strip()
    if hint:
        try:
            hint = bpy.path.abspath(hint)
        except Exception:
            pass
        if os.path.isfile(hint):
            return hint, "manual"

    stems = _map_name_stems(map_name)
    preferred: list[str] = []
    for stem in stems:
        preferred.append(f"T_InGameMap_{stem}")
        # Spaceport_01 → keep; also try without trailing _01 style already in stems
    preferred.append("T_InGameMap")

    best = ("", "", -1.0)
    for folder in _ground_search_roots(map_name):
        for name, fpath in _iter_image_files(folder):
            stem = os.path.splitext(name)[0]
            low = stem.lower()
            if "ingamemap" not in low.replace("_", "").replace("-", ""):
                # Accept exact T_InGameMap_* naming
                if not low.startswith("t_ingamemap"):
                    continue
            # Skip underground / placeholder variants unless that's all we have
            score = 10.0
            if "underground" in low or "placeholder" in low or "bottom" in low:
                score -= 4.0
            for i, pref in enumerate(preferred):
                if low == pref.lower():
                    score += 20.0 - i
                    break
                if low.startswith(pref.lower()):
                    score += 12.0 - i * 0.5
                    break
            for stem_i, stem in enumerate(stems):
                if stem.lower() in low:
                    score += 8.0 - stem_i
            if score > best[2]:
                best = (fpath, f"{os.path.basename(folder)}/{name}", score)

    if best[0]:
        return best[0], best[1]
    return "", ""


def resolve_hlod_color_texture(
    map_name: str = "",
    hint: str = "",
) -> tuple[str, str, list[str]]:
    """Find HLOD / landscape Color texture(s) for palette sampling.

    Returns ``(primary_path, reason, all_tile_paths)``.
    Single ``_Color_xN_yM`` tiles are palette sources (not full-map UV albedos).
    """
    hint = (hint or "").strip()
    if hint:
        try:
            hint = bpy.path.abspath(hint)
        except Exception:
            pass
        if os.path.isfile(hint):
            return hint, "manual", [hint]

    stems = _map_name_stems(map_name)
    tiles: list[tuple[float, str, str]] = []
    full: list[tuple[float, str, str]] = []

    for folder in _ground_search_roots(map_name):
        for name, fpath in _iter_image_files(folder):
            stem = os.path.splitext(name)[0]
            low = stem.lower()
            if "color" not in low:
                continue
            # Prefer T_{Map}_Color* landscape tiles; skip unrelated BackdropMesh city colors
            score = 0.0
            matched_stem = False
            for i, ms in enumerate(stems):
                if ms.lower() in low:
                    score += 15.0 - i
                    matched_stem = True
                    break
            if not matched_stem and stems:
                # Bundled Spaceport fallback without map name match only if folder is refs
                if os.path.normcase(folder) != os.path.normcase(_GROUND_MAP_REFS_DIR):
                    continue
                score += 2.0
            if "hlod" in low:
                score += 3.0
            if low.startswith("t_") and "_color" in low:
                score += 4.0
            if "buriedcity" in low and not any("buried" in s.lower() for s in stems):
                score -= 20.0
            if _HLOD_COLOR_TILE_RE.search(stem):
                score += 5.0
                tiles.append((score, fpath, f"{os.path.basename(folder)}/{name}"))
            elif low.endswith("_color") or "_color_" in low:
                full.append((score, fpath, f"{os.path.basename(folder)}/{name}"))

    tiles.sort(key=lambda t: -t[0])
    full.sort(key=lambda t: -t[0])
    if full and full[0][0] >= 10.0:
        path, reason = full[0][1], full[0][2]
        extras = [t[1] for t in tiles[:8]]
        return path, reason + " (full)", [path] + extras
    if tiles:
        # Prefer a mid tile for diversity when several exist; else first
        paths = [t[1] for t in tiles[:12]]
        pick = tiles[len(tiles) // 2] if len(tiles) > 2 else tiles[0]
        return pick[1], pick[2] + " (tile/palette)", paths
    return "", "", []


def _avg_rgb(samples: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    if not samples:
        return (0.5, 0.5, 0.5)
    n = float(len(samples))
    return (
        sum(s[0] for s in samples) / n,
        sum(s[1] for s in samples) / n,
        sum(s[2] for s in samples) / n,
    )


def extract_hlod_palette(
    image_paths: str | list[str] | None,
    *,
    max_side: int = 96,
) -> dict[str, tuple[float, float, float]]:
    """Sample dominant cream / rock / pink / dark tones from HLOD Color image(s).

    Uses PIL when available; otherwise returns Spaceport defaults. Tile paths are
    palette-only — spatial UV alignment is not assumed.
    """
    palette = dict(_DEFAULT_HLOD_PALETTE)
    paths: list[str] = []
    if isinstance(image_paths, str) and image_paths:
        paths = [image_paths]
    elif isinstance(image_paths, (list, tuple)):
        paths = [p for p in image_paths if p]

    existing = [p for p in paths if p and os.path.isfile(p)]
    if not existing:
        return palette

    try:
        from PIL import Image
        import colorsys
    except Exception:
        return palette

    cream_s: list[tuple[float, float, float]] = []
    rock_s: list[tuple[float, float, float]] = []
    pink_s: list[tuple[float, float, float]] = []
    dark_s: list[tuple[float, float, float]] = []

    for fpath in existing[:8]:
        try:
            im = Image.open(fpath).convert("RGB")
        except Exception:
            continue
        w, h = im.size
        scale = max(w, h) / float(max(max_side, 16))
        if scale > 1.0:
            im = im.resize(
                (max(1, int(w / scale)), max(1, int(h / scale))),
                getattr(Image, "BILINEAR", 2),
            )
        for r, g, b in im.getdata():
            rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
            hh, ss, vv = colorsys.rgb_to_hsv(rf, gf, bf)
            if vv < 0.38:
                dark_s.append((rf, gf, bf))
                continue
            # Dusty pink / rose sediment
            if ss > 0.06 and rf >= gf and (hh < 0.12 or hh > 0.85 or (bf >= gf * 0.9 and rf > bf)):
                if rf - gf > 0.02 or bf - gf > 0.015:
                    pink_s.append((rf, gf, bf))
                    continue
            if ss < 0.14 and vv < 0.82:
                rock_s.append((rf, gf, bf))
                continue
            if vv >= 0.62:
                cream_s.append((rf, gf, bf))

    if cream_s:
        palette["cream"] = _avg_rgb(cream_s)
    if rock_s:
        palette["rock"] = _avg_rgb(rock_s)
    if pink_s:
        palette["pink"] = _avg_rgb(pink_s)
    if dark_s:
        palette["dark"] = _avg_rgb(dark_s)
    return palette


def _rgb_node(nodes, rgb: tuple[float, float, float], label: str, loc):
    node = nodes.new("ShaderNodeRGB")
    node.label = label
    node.location = loc
    r, g, b = float(rgb[0]), float(rgb[1]), float(rgb[2])
    node.outputs[0].default_value = (r, g, b, 1.0)
    return node.outputs[0]


def _invert_mask(nodes, links, value_sock, loc, label: str = "Invert Mask"):
    """1 - value for dark→1 luminance masks."""
    node = nodes.new("ShaderNodeMath")
    node.operation = "SUBTRACT"
    node.label = label
    node.location = loc
    node.inputs[0].default_value = 1.0
    links.new(value_sock, node.inputs[1])
    return node.outputs["Value"]


def _wire_heightmap_map_texturing(
    mat,
    *,
    ingame_img=None,
    palette: dict | None = None,
    mix_strength: float = 0.72,
    structure_darken: float = 0.22,
):
    """Overlay In-Game Map luminance masks + HLOD palette onto landscape sand Base Color.

    Macro relief stays on the Displace modifier. Sand dual-scale normals/roughness
    are left intact; only Base Color is remixed.
    """
    if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return False
    palette = palette or dict(_DEFAULT_HLOD_PALETTE)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    principled = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if principled is None:
        return False

    base_in = principled.inputs.get("Base Color")
    if base_in is None:
        return False

    if base_in.is_linked:
        sand_sock = base_in.links[0].from_socket
    else:
        flat = nodes.new("ShaderNodeRGB")
        flat.label = "Ground Base"
        flat.location = (-200, 200)
        dv = tuple(base_in.default_value)
        flat.outputs[0].default_value = (dv[0], dv[1], dv[2], 1.0)
        sand_sock = flat.outputs[0]

    COL = 200
    tc = nodes.new("ShaderNodeTexCoord")
    tc.label = "Ground Map UV"
    tc.location = (COL - 1600, 700)
    uv_vec = tc.outputs["UV"]

    if ingame_img is not None:
        map_tex = _new_tex_image(
            nodes, ingame_img, "In-Game Map (spatial)", (COL - 1300, 700),
        )
        try:
            map_tex.extension = "CLIP"
        except Exception:
            pass
        links.new(uv_vec, map_tex.inputs["Vector"])
        sep = nodes.new("ShaderNodeSeparateColor")
        sep.label = "Map RGB"
        sep.location = (COL - 1000, 700)
        links.new(map_tex.outputs["Color"], sep.inputs["Color"])
        mul_r = nodes.new("ShaderNodeMath")
        mul_r.operation = "MULTIPLY"
        mul_r.label = "0.30 R"
        mul_r.location = (COL - 780, 820)
        mul_r.inputs[1].default_value = 0.30
        links.new(sep.outputs["Red"], mul_r.inputs[0])
        mul_g = nodes.new("ShaderNodeMath")
        mul_g.operation = "MULTIPLY"
        mul_g.label = "0.59 G"
        mul_g.location = (COL - 780, 700)
        mul_g.inputs[1].default_value = 0.59
        links.new(sep.outputs["Green"], mul_g.inputs[0])
        mul_b = nodes.new("ShaderNodeMath")
        mul_b.operation = "MULTIPLY"
        mul_b.label = "0.11 B"
        mul_b.location = (COL - 780, 580)
        mul_b.inputs[1].default_value = 0.11
        links.new(sep.outputs["Blue"], mul_b.inputs[0])
        add1 = nodes.new("ShaderNodeMath")
        add1.operation = "ADD"
        add1.location = (COL - 560, 780)
        links.new(mul_r.outputs["Value"], add1.inputs[0])
        links.new(mul_g.outputs["Value"], add1.inputs[1])
        add2 = nodes.new("ShaderNodeMath")
        add2.operation = "ADD"
        add2.label = "Map Luminance"
        add2.location = (COL - 380, 740)
        links.new(add1.outputs["Value"], add2.inputs[0])
        links.new(mul_b.outputs["Value"], add2.inputs[1])
        luma_sock = add2.outputs["Value"]
    else:
        val = nodes.new("ShaderNodeValue")
        val.label = "Flat Mid (no InGameMap)"
        val.location = (COL - 380, 740)
        val.outputs[0].default_value = 0.55
        luma_sock = val.outputs[0]

    # Dark map → rock; bright → cream flats; mid band → pink sediment; very dark → structure
    rock_bright = _contrast_mask(
        nodes, links, luma_sock, 0.05, 0.32, (COL - 160, 860), "Rock luma window",
    )
    rock_mask = _invert_mask(
        nodes, links, rock_bright, (COL + 40, 860), "Rock (dark map)",
    )
    cream_mask = _contrast_mask(
        nodes, links, luma_sock, 0.40, 0.88, (COL - 160, 700), "Cream / flats",
    )
    pink_center = _contrast_mask(
        nodes, links, luma_sock, 0.30, 0.55, (COL - 160, 540), "Pink mid",
    )
    pink_edge = _contrast_mask(
        nodes, links, luma_sock, 0.55, 0.75, (COL - 160, 420), "Pink edge",
    )
    pink_mask = _mix_float(
        nodes, links, pink_center, pink_edge, 0.45, (COL + 40, 480), "Pink sediment",
    )
    # Soften pink
    pink_amt = nodes.new("ShaderNodeMath")
    pink_amt.operation = "MULTIPLY"
    pink_amt.label = "Pink ×0.35"
    pink_amt.location = (COL + 220, 480)
    pink_amt.inputs[1].default_value = 0.35
    links.new(pink_mask, pink_amt.inputs[0])
    pink_mask = pink_amt.outputs["Value"]

    struct_bright = _contrast_mask(
        nodes, links, luma_sock, 0.02, 0.16, (COL - 160, 300), "Structure luma",
    )
    struct_mask = _invert_mask(
        nodes, links, struct_bright, (COL + 40, 300), "Structure dark",
    )

    cream_rgb = _rgb_node(
        nodes, palette.get("cream", _DEFAULT_HLOD_PALETTE["cream"]),
        "HLOD Cream", (COL + 40, 980),
    )
    rock_rgb = _rgb_node(
        nodes, palette.get("rock", _DEFAULT_HLOD_PALETTE["rock"]),
        "HLOD Rock", (COL + 40, 780),
    )
    pink_rgb = _rgb_node(
        nodes, palette.get("pink", _DEFAULT_HLOD_PALETTE["pink"]),
        "HLOD Pink", (COL + 40, 620),
    )
    dark_rgb = _rgb_node(
        nodes, palette.get("dark", _DEFAULT_HLOD_PALETTE["dark"]),
        "HLOD Dark", (COL + 40, 360),
    )

    base_pal = _mix_rgba(
        nodes, links, cream_rgb, rock_rgb, rock_mask,
        (COL + 280, 820), "Cream↔Rock",
    )
    # Mild cream pull on bright dunes/cliffs
    cream_pull = nodes.new("ShaderNodeMath")
    cream_pull.operation = "MULTIPLY"
    cream_pull.label = "Cream pull ×0.4"
    cream_pull.location = (COL + 280, 680)
    cream_pull.inputs[1].default_value = 0.40
    links.new(cream_mask, cream_pull.inputs[0])
    base_pal = _mix_rgba(
        nodes, links, base_pal, cream_rgb, cream_pull.outputs["Value"],
        (COL + 500, 780), "Reinforce flats",
    )
    with_pink = _mix_rgba(
        nodes, links, base_pal, pink_rgb, pink_mask,
        (COL + 720, 700), "Pink veins",
    )
    struct_amt = nodes.new("ShaderNodeMath")
    struct_amt.operation = "MULTIPLY"
    struct_amt.label = f"Struct ×{structure_darken:g}"
    struct_amt.location = (COL + 720, 520)
    struct_amt.inputs[1].default_value = float(structure_darken)
    links.new(struct_mask, struct_amt.inputs[0])
    darkened = _mix_rgba(
        nodes, links, with_pink, dark_rgb, struct_amt.outputs["Value"],
        (COL + 960, 620), "Structure silhouette",
    )

    combined = _mix_rgba(
        nodes, links, sand_sock, darkened, float(mix_strength),
        (COL + 1200, 400), "Sand × HLOD palette",
    )
    combined = _mix_rgba(
        nodes, links, combined, sand_sock, 0.28,
        (COL + 1420, 320), "Grain multiply", blend="MULTIPLY",
    )

    _unlink_input(links, base_in)
    links.new(combined, base_in)

    try:
        mat["arc_ground_map_textured"] = 1
        mat["arc_ground_palette_cream"] = list(palette.get("cream", (0, 0, 0)))
        mat["arc_ground_palette_rock"] = list(palette.get("rock", (0, 0, 0)))
        mat["arc_ground_palette_pink"] = list(palette.get("pink", (0, 0, 0)))
        mat["arc_ground_mix_strength"] = float(mix_strength)
    except Exception:
        pass
    return True


def setup_landscape_sand_material(
    mat=None,
    *,
    map_name: str = "",
    macro_meters: float = 12.0,
    micro_meters: float = 1.5,
    ingame_map_path: str = "",
    hlod_color_path: str = "",
    use_map_texturing: bool = True,
):
    """Build / rebuild a dual-scale sand material for CityGroundPlane.

    Macro dune shape must come from displaced heightmap geometry — this shader only
    supplies micro BRDF (albedo breakup + detail normals). Does not use NOR as height.

    Optional In-Game Map + HLOD Color paths drive spatial masks and albedo palette.
    """
    name = f"{(map_name or 'Map').strip() or 'Map'}_LandscapeSand"
    if mat is None:
        mat = bpy.data.materials.get(name)
        if mat is None:
            mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    _stamp_mi_family(mat, FAMILY_SAND)

    dirs = _collect_sand_search_dirs("", "")
    used: set[str] = set()
    maps = {
        "macro_albedo": None,
        "micro_albedo": None,
        "fine_normal": None,
        "macro_normal": None,
        "albedo_param": "",
        "normal_param": "",
        "meta": {},
    }
    for role, stems, key in (
        ("macro_albedo", _SAND_MACRO_ALBEDO_STEMS, "macro_albedo"),
        ("micro_albedo", _SAND_MICRO_ALBEDO_STEMS, "micro_albedo"),
        ("fine_normal", _SAND_FINE_NORMAL_STEMS, "fine_normal"),
        ("macro_normal", _SAND_MACRO_NORMAL_STEMS, "macro_normal"),
    ):
        img, path, reason = _find_sand_stem_image(stems, dirs, used)
        if img is None:
            path2, reason2 = infer_sand_texture(role, "", "", mi_stem="landscape_sand", exclude_paths=used)
            img = _load_fallback_tex_image(path2)
            path, reason = path2, reason2
        if img is not None:
            maps[key] = img
            if path:
                used.add(path)
                maps["meta"][key] = path
                maps["meta"][f"{key}_src"] = reason

    _wire_dual_scale_sand(
        mat,
        maps=maps,
        scalars={},
        colours=[],
        tex_lookup={},
        coord_mode="object",
        macro_meters=macro_meters,
        micro_meters=micro_meters,
        apply_tint=False,
    )
    try:
        mat["arc_landscape_sand"] = 1
        if map_name:
            mat["arc_map"] = map_name
    except Exception:
        pass
    for role in ("macro_albedo", "micro_albedo", "fine_normal", "macro_normal"):
        if role in maps["meta"]:
            _stamp_tex_fallback(mat, role, maps["meta"][role], maps["meta"].get(f"{role}_src", ""))

    if use_map_texturing:
        apply_heightmap_map_texturing(
            mat,
            map_name=map_name or "",
            ingame_map_path=ingame_map_path,
            hlod_color_path=hlod_color_path,
        )
    return mat


def apply_heightmap_map_texturing(
    mat,
    *,
    map_name: str = "",
    ingame_map_path: str = "",
    hlod_color_path: str = "",
) -> dict[str, str]:
    """Resolve refs (if needed) and wire In-Game Map + HLOD palette onto ``mat``.

    Returns a small status dict: ingame, hlod, palette_mode, note.
    """
    status = {
        "ingame": "",
        "hlod": "",
        "palette_mode": "default",
        "note": "",
    }
    if mat is None:
        status["note"] = "no material"
        return status

    ingame_path, ingame_why = resolve_ingame_map_texture(map_name, ingame_map_path)
    hlod_path, hlod_why, hlod_tiles = resolve_hlod_color_texture(map_name, hlod_color_path)

    ingame_img = _load_fallback_tex_image(ingame_path) if ingame_path else None
    sample_paths = list(hlod_tiles) if hlod_tiles else ([hlod_path] if hlod_path else [])
    if hlod_color_path and os.path.isfile(
        bpy.path.abspath(hlod_color_path) if hlod_color_path else ""
    ):
        # Manual pick wins as primary sample; still allow discovered tiles for richness
        manual = bpy.path.abspath(hlod_color_path)
        if manual not in sample_paths:
            sample_paths.insert(0, manual)

    palette = extract_hlod_palette(sample_paths)
    palette_mode = "default"
    if sample_paths:
        tileish = any(_HLOD_COLOR_TILE_RE.search(os.path.splitext(os.path.basename(p))[0])
                      for p in sample_paths)
        palette_mode = "tile_palette" if tileish else "hlod_palette"

    ok = _wire_heightmap_map_texturing(
        mat,
        ingame_img=ingame_img,
        palette=palette,
        mix_strength=0.72 if ingame_img is not None else 0.55,
    )
    try:
        if ingame_path:
            mat["arc_ingame_map_path"] = ingame_path
            mat["arc_ingame_map_src"] = ingame_why
        if hlod_path:
            mat["arc_hlod_color_path"] = hlod_path
            mat["arc_hlod_color_src"] = hlod_why
        mat["arc_hlod_palette_mode"] = palette_mode
        mat["arc_ground_map_textured"] = 1 if ok else 0
    except Exception:
        pass

    status["ingame"] = ingame_path
    status["hlod"] = hlod_path
    status["palette_mode"] = palette_mode
    if ok:
        bits = []
        if ingame_path:
            bits.append(f"map={os.path.basename(ingame_path)}")
        if hlod_path:
            bits.append(f"hlod={os.path.basename(hlod_path)}/{palette_mode}")
        status["note"] = " · ".join(bits) if bits else "palette defaults (no refs found)"
    else:
        status["note"] = "wire failed"
    return status


def _scene_water_shore_settings():
    """Read shore proximity tunables from the active scene (safe defaults)."""
    dist = 2.0
    strength = 1.0
    invert = False
    try:
        scene = bpy.context.scene
        dist = float(getattr(scene, "arc_water_shore_distance", dist) or dist)
        strength = float(getattr(scene, "arc_water_shore_strength", strength) or strength)
        invert = bool(getattr(scene, "arc_water_shore_invert", False))
    except Exception:
        pass
    return max(dist, 0.05), min(max(strength, 0.0), 4.0), invert


def _wire_water_shore_factor(nodes, links, col_mix: float, y: float = 420):
    """Attribute proximity + Cycles AO → Mix Factor (1 = Shore Color).

    * ``arc_shore_proximity`` — vertex float baked by Refresh / Stage 2 (robust on maps).
    * Ambient Occlusion (Inside) — shading-point fallback; strong in Cycles, weak in Eevee.
    """
    shore_dist, shore_strength, shore_invert = _scene_water_shore_settings()

    attr = nodes.new("ShaderNodeAttribute")
    attr.label = "Shore Proximity"
    attr.attribute_name = _WATER_SHORE_ATTR
    try:
        attr.attribute_type = "GEOMETRY"
    except Exception:
        pass
    attr.location = (col_mix - 520, y + 80)

    ao = nodes.new("ShaderNodeAmbientOcclusion")
    ao.label = "Shore AO (Cycles)"
    ao.inside = True
    try:
        ao.only_local = False
    except Exception:
        pass
    try:
        ao.samples = 16
    except Exception:
        pass
    ao.location = (col_mix - 520, y - 120)

    dist_val = nodes.new("ShaderNodeValue")
    dist_val.label = "Shore Dist (m)"
    dist_val.outputs[0].default_value = shore_dist
    dist_val.location = (col_mix - 760, y - 80)
    try:
        links.new(dist_val.outputs[0], ao.inputs["Distance"])
    except Exception:
        pass

    # AO: 1 = open water, 0 = near geometry → invert to shore factor
    ao_inv = nodes.new("ShaderNodeMath")
    ao_inv.operation = "SUBTRACT"
    ao_inv.label = "1−AO"
    ao_inv.inputs[0].default_value = 1.0
    ao_inv.location = (col_mix - 280, y - 100)
    links.new(ao.outputs["AO"], ao_inv.inputs[1])

    # max(attribute, ao_shore)
    mx = nodes.new("ShaderNodeMath")
    mx.operation = "MAXIMUM"
    mx.label = "Prox∨AO"
    mx.location = (col_mix - 80, y)
    links.new(attr.outputs["Fac"], mx.inputs[0])
    links.new(ao_inv.outputs["Value"], mx.inputs[1])

    # Strength scale + clamp
    mul = nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    mul.label = "Shore Strength"
    mul.inputs[1].default_value = shore_strength
    mul.location = (col_mix + 100, y)
    links.new(mx.outputs["Value"], mul.inputs[0])

    clamp = nodes.new("ShaderNodeClamp")
    clamp.location = (col_mix + 280, y)
    links.new(mul.outputs["Value"], clamp.inputs["Value"])

    fac_out = clamp.outputs["Result"]
    if shore_invert:
        inv = nodes.new("ShaderNodeMath")
        inv.operation = "SUBTRACT"
        inv.label = "Invert Shore"
        inv.inputs[0].default_value = 1.0
        inv.location = (col_mix + 460, y)
        links.new(clamp.outputs["Result"], inv.inputs[1])
        fac_out = inv.outputs["Value"]

    return fac_out


def _wire_water_world_bump(nodes, links, principled, col_tex: float, col_mix: float):
    """World-space ridged noise → Bump → Principled Normal (target Shader Editor graph)."""
    geom = nodes.new("ShaderNodeNewGeometry")
    geom.label = "Plane World Pos"
    geom.location = (col_tex - 400, -320)

    mapping = nodes.new("ShaderNodeMapping")
    mapping.label = "World 4m/tile"
    mapping.vector_type = "POINT"
    mapping.inputs["Scale"].default_value = (0.25, 0.25, 0.25)
    mapping.location = (col_tex - 160, -320)
    links.new(geom.outputs["Position"], mapping.inputs["Vector"])

    noise = nodes.new("ShaderNodeTexNoise")
    noise.label = "Ridged Water"
    try:
        noise.noise_dimensions = "3D"
    except Exception:
        pass
    try:
        noise.noise_type = "RIDGED_MULTIFRACTAL"
    except Exception:
        try:
            noise.noise_type = "MULTIFRACTAL"
        except Exception:
            pass
    noise.inputs["Scale"].default_value = 2.0
    try:
        noise.inputs["Detail"].default_value = 0.0
    except Exception:
        pass
    try:
        noise.inputs["Roughness"].default_value = 1.0
    except Exception:
        pass
    try:
        noise.inputs["Lacunarity"].default_value = 74.0
    except Exception:
        pass
    try:
        noise.inputs["Distortion"].default_value = 1.3
    except Exception:
        pass
    noise.location = (col_mix - 200, -320)
    links.new(mapping.outputs["Vector"], noise.inputs["Vector"])

    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.5
    try:
        bump.inputs["Distance"].default_value = 0.1
    except Exception:
        pass
    bump.location = (col_mix + 80, -280)
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], principled.inputs["Normal"])


def _setup_water_material(mat, mi_path: str, psk_path: str = ""):
    """Water / ocean / lagoon / river — Water↔Shore + ridged bump + proximity/AO factor."""
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_WATER)
    try:
        mat["arc_water_setup"] = _WATER_SETUP_V
    except Exception:
        pass

    COL_TEX, COL_MIX, COL_BSDF, COL_OUT = -1200, -200, 400, 700
    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []
    log = utils.get_logger()

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    water = _mi_colour(colours, *_WATER_COLOR_KEYS, default=None)
    shallow = _mi_colour(colours, *_WATER_SHALLOW_KEYS, default=None)
    deep = _mi_colour(colours, *_WATER_DEEP_KEYS, default=None)
    shore = _mi_colour(colours, *_WATER_SHORE_KEYS, default=None)

    # Resolve Water Color / Shore Color for the target Mix graph.
    # RockyCreek-style Deep+Shallow maps onto Water↔Shore when Shore/Water keys absent.
    if water is not None:
        water_col = water
    elif deep is not None:
        water_col = deep
    elif shallow is not None and shore is None:
        water_col = shallow
    else:
        water_col = _DEFAULT_WATER_COLOR
        if water is None and deep is None and shallow is None and shore is None:
            log.warning(
                "Water MI '%s' has no Water/Shore colour params — using default teal/sand",
                os.path.basename(mi_path),
            )

    if shore is not None:
        shore_col = shore
    elif shallow is not None and (water is not None or deep is not None):
        shore_col = shallow
    else:
        shore_col = _DEFAULT_SHORE_COLOR

    w_rgb = nodes.new("ShaderNodeRGB")
    w_rgb.label = "Water Color"
    w_rgb.outputs[0].default_value = water_col
    w_rgb.location = (COL_TEX, 280)

    s_rgb = nodes.new("ShaderNodeRGB")
    s_rgb.label = "Shore Color"
    s_rgb.outputs[0].default_value = shore_col
    s_rgb.location = (COL_TEX, 80)

    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MIX"
    mix.label = "Water↔Shore"
    mix.location = (COL_MIX, 200)
    try:
        mix.clamp_factor = True
    except Exception:
        pass
    mix.inputs["Factor"].default_value = 0.0  # open water until proximity/AO drives shore
    links.new(w_rgb.outputs[0], mix.inputs[6])
    links.new(s_rgb.outputs[0], mix.inputs[7])
    links.new(mix.outputs[2], principled.inputs["Base Color"])

    fac_sock = _wire_water_shore_factor(nodes, links, COL_MIX, y=420)
    links.new(fac_sock, mix.inputs["Factor"])

    # Stamp authored colours on the datablock for debugging / later shore blends
    try:
        mat["arc_water_color"] = list(water_col)
        if shallow is not None:
            mat["arc_water_color_shallow"] = list(shallow)
        if deep is not None:
            mat["arc_water_color_deep"] = list(deep)
        mat["arc_shore_color"] = list(shore_col)
    except Exception:
        pass

    # Target Principled: glossy translucent water (opaque rivers stay denser).
    opaque_river = (
        "opaque" in os.path.basename(mi_path or "").lower()
        or (not _is_translucent_blend(mi) and not mi.get("is_translucent"))
    )
    clarity = max(float(_mi_scalar(scalars, "3. Water Clarity", "Water Clarity", default=1.0)), 0.0)
    if opaque_river and (deep is not None or water is not None):
        transmission = min(0.25 + 0.15 * min(clarity, 2.0), 0.55)
        alpha = 0.85
    else:
        transmission = 1.0
        alpha = 0.45
    alpha_mode = "BLEND"

    principled.inputs["Metallic"].default_value = 0.0
    principled.inputs["Roughness"].default_value = 0.0
    try:
        principled.inputs["Transmission Weight"].default_value = transmission
    except Exception:
        try:
            principled.inputs["Transmission"].default_value = transmission
        except Exception:
            pass
    principled.inputs["Alpha"].default_value = alpha
    try:
        principled.inputs["IOR"].default_value = 1.333
    except Exception:
        pass
    try:
        principled.inputs["Specular IOR Level"].default_value = 0.5
    except Exception:
        pass

    _wire_water_world_bump(nodes, links, principled, COL_TEX, COL_MIX)

    # Leave authored NR textures as inspection nodes (procedural bump is the live normal).
    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    _set_material_alpha_mode(mat, mode=alpha_mode, two_sided=False)

    handled = {
        node.image.filepath
        for node in nodes
        if getattr(node, "image", None) is not None
    }
    _dump_unconnected_tex(nodes, tex_lookup, handled, COL_TEX - 500, -700)


# ---------------------------------------------------------------------------
# Water shore proximity (vertex attribute → shader Attribute node)
# ---------------------------------------------------------------------------

def object_has_water_material(obj) -> bool:
    """True when any material slot is stamped water family."""
    if obj is None or getattr(obj, "type", "") != "MESH":
        return False
    for slot in obj.material_slots or []:
        mat = slot.material
        if mat is not None and str(mat.get("arc_mi_family") or "") == FAMILY_WATER:
            return True
    return False


def _shore_proximity_exclude_object(obj) -> bool:
    """Skip water / glass / decal / helper / non-mesh as proximity targets."""
    if obj is None or getattr(obj, "type", "") != "MESH":
        return True
    if obj.get("arc_plane_mesh") and object_has_water_material(obj):
        return True
    if object_has_water_material(obj):
        return True
    group = str(obj.get("arc_map_group") or "").lower()
    if group in ("helpers", "light", "sky", "debris"):
        return True
    name_l = (obj.name or "").lower()
    if any(t in name_l for t in ("helper", "occluder", "skybox", "skydome", "lightcard")):
        return True
    # Pure glass / decal cards — exclude; mixed SMA with glass slots still count via other mats
    water_glass_decal_only = True
    any_mat = False
    for slot in obj.material_slots or []:
        mat = slot.material
        if mat is None:
            continue
        any_mat = True
        fam = str(mat.get("arc_mi_family") or "")
        if fam not in (FAMILY_GLASS, FAMILY_DECAL, FAMILY_WATER, ""):
            water_glass_decal_only = False
            break
        if fam == "":
            # Unstamped opaque mesh — keep as target
            water_glass_decal_only = False
            break
    if any_mat and water_glass_decal_only:
        # All slots glass/decal/water
        fams = {
            str(slot.material.get("arc_mi_family") or "")
            for slot in obj.material_slots
            if slot.material is not None
        }
        if fams and fams <= {FAMILY_GLASS, FAMILY_DECAL, FAMILY_WATER}:
            return True
    return False


def _shore_proximity_include_object(obj, target_coll=None) -> bool:
    """Prefer heightmap + StaticMeshActors; optional explicit collection override."""
    if _shore_proximity_exclude_object(obj):
        return False
    if target_coll is not None:
        try:
            return obj.name in target_coll.objects
        except Exception:
            return False
    if obj.get("arc_heightmap_plane") or obj.get("arc_heightmap"):
        return True
    group = str(obj.get("arc_map_group") or "")
    if group == "StaticMeshActors":
        return True
    name_l = (obj.name or "").lower()
    if name_l.startswith("staticmeshactor") or "cityground" in name_l or "heightmap" in name_l:
        return True
    # SMA collection membership (group stamp may be missing before Group Collections)
    try:
        for coll in obj.users_collection:
            cname = (coll.name or "").lower()
            if cname.endswith("_staticmeshactors") or cname == "staticmeshactors":
                return True
    except Exception:
        pass
    return False


def _ensure_water_mesh_density(obj, max_edge_m: float = 1.25, max_cuts: int = 4) -> bool:
    """Subdivide sparse unique water planes so shore proximity has mid-face samples."""
    import bmesh

    mesh = obj.data
    if mesh is None or len(mesh.vertices) >= 200:
        return False
    if mesh.users > 1:
        # Don't densify shared datablocks — Stage 1 unique planes should be users==1
        try:
            obj.data = mesh.copy()
            mesh = obj.data
        except Exception:
            return False
    try:
        dims = obj.dimensions
        span = max(float(dims.x), float(dims.y), 0.01)
    except Exception:
        span = 32.0
    if span < max_edge_m * 2:
        return False
    # cuts needed so edge ≈ max_edge_m
    cuts = 0
    edge = span
    while edge > max_edge_m and cuts < max_cuts:
        cuts += 1
        edge *= 0.5
    if cuts <= 0:
        return False
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        for _ in range(cuts):
            bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1)
        bm.to_mesh(mesh)
        mesh.update()
    finally:
        bm.free()
    return True


def _build_shore_target_bvhs(objects, depsgraph):
    """Build world-space BVH trees for proximity targets."""
    from mathutils.bvhtree import BVHTree
    import bmesh

    trees = []
    for obj in objects:
        if obj is None or obj.type != "MESH" or obj.data is None:
            continue
        try:
            if obj.hide_get() and obj.hide_render:
                continue
        except Exception:
            pass
        bm = bmesh.new()
        try:
            bm.from_object(obj, depsgraph)
            bm.transform(obj.matrix_world)
            bmesh.ops.triangulate(bm, faces=bm.faces[:])
            if not bm.faces:
                continue
            tree = BVHTree.FromBMesh(bm, epsilon=0.0)
            trees.append(tree)
        except Exception:
            continue
        finally:
            bm.free()
    return trees


def _closest_shore_distance(co_world, trees):
    best = None
    for tree in trees:
        try:
            loc, _normal, _idx, dist = tree.find_nearest(co_world)
        except Exception:
            continue
        if loc is None:
            continue
        d = float(dist)
        if best is None or d < best:
            best = d
    return best


def refresh_water_shore_proximity(context=None, objects=None, densify: bool = True) -> dict:
    """Bake ``arc_shore_proximity`` (0 open … 1 shore) onto water mesh vertices.

    Measures distance to nearby opaque meshes (heightmap, StaticMeshActors, etc.).
    Call after Stage 2 / Fix White, or via ``arc.refresh_water_shore_proximity``.
    Not per-frame — static map bake.
    """
    context = context or bpy.context
    scene = context.scene
    shore_dist, _strength, invert = _scene_water_shore_settings()
    target_coll = None
    try:
        ptr = getattr(scene, "arc_water_shore_target_collection", None)
        if ptr is not None:
            target_coll = ptr
    except Exception:
        pass

    if objects is None:
        candidates = list(bpy.data.objects)
    else:
        candidates = list(objects)

    water_objs = [o for o in candidates if object_has_water_material(o)]
    if not water_objs:
        # Also scan full scene when callers pass Stage 2 targets that may miss SRC
        water_objs = [o for o in bpy.data.objects if object_has_water_material(o)]

    target_objs = [
        o for o in bpy.data.objects
        if _shore_proximity_include_object(o, target_coll=target_coll)
    ]
    # Never measure a water plane against itself
    water_set = set(water_objs)
    target_objs = [o for o in target_objs if o not in water_set]

    stats = {
        "water": 0,
        "targets": len(target_objs),
        "verts": 0,
        "densified": 0,
        "skipped": 0,
    }
    if not water_objs or not target_objs:
        return stats

    depsgraph = context.evaluated_depsgraph_get()
    trees = _build_shore_target_bvhs(target_objs, depsgraph)
    if not trees:
        return stats

    for obj in water_objs:
        mesh = obj.data
        if mesh is None:
            stats["skipped"] += 1
            continue
        if densify:
            try:
                if _ensure_water_mesh_density(obj):
                    stats["densified"] += 1
                    mesh = obj.data
            except Exception:
                pass
        attr = mesh.attributes.get(_WATER_SHORE_ATTR)
        if attr is None:
            attr = mesh.attributes.new(_WATER_SHORE_ATTR, "FLOAT", "POINT")
        elif attr.domain != "POINT" or attr.data_type != "FLOAT":
            try:
                mesh.attributes.remove(attr)
            except Exception:
                pass
            attr = mesh.attributes.new(_WATER_SHORE_ATTR, "FLOAT", "POINT")

        mw = obj.matrix_world
        n = len(mesh.vertices)
        values = [0.0] * n
        for i, v in enumerate(mesh.vertices):
            co = mw @ v.co
            d = _closest_shore_distance(co, trees)
            if d is None:
                fac = 0.0
            else:
                fac = 1.0 - min(max(d / shore_dist, 0.0), 1.0)
            if invert:
                fac = 1.0 - fac
            values[i] = fac
        try:
            attr.data.foreach_set("value", values)
        except Exception:
            for i, fac in enumerate(values):
                attr.data[i].value = fac
        mesh.update()
        try:
            obj["arc_shore_proximity_baked"] = 1
            obj["arc_shore_proximity_distance"] = shore_dist
        except Exception:
            pass
        stats["water"] += 1
        stats["verts"] += n

    return stats


def _setup_glass_env_material(mat, mi_path: str, psk_path: str = ""):
    """Window / pane glass — transmission stub (not clothing visor ColorA/B)."""
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_GLASS)

    COL_TEX, COL_MIX, COL_BSDF, COL_OUT = -1200, -200, 400, 700
    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    _, color_img = _find_env_tex(
        tex_lookup,
        "C", "CR", "BaseColor", "PM_Diffuse", "CA",
        "T_BrokenGlassOverlay_01_NC", "Overlay",
    )
    if not color_img:
        # BrokenGlassSDF / pane overlays often only expose NC / shard atlas stems
        for param, (_fp, img) in tex_lookup.items():
            stem = os.path.splitext(os.path.basename(_fp))[0].lower()
            if stem.endswith("_nc") or "overlay" in stem or "shard" in stem:
                if "noise" in stem or "engine" in stem:
                    continue
                color_img = img
                break
    _, normal_img = _find_env_tex(
        tex_lookup,
        "NXX", "NormalMap", "Normals", "Normal", "PM_Normals", "NXX/NMX Texture",
        "T_BrokenGlassSDF_NDD",
    )
    if not normal_img:
        for param, (_fp, img) in tex_lookup.items():
            stem = os.path.splitext(os.path.basename(_fp))[0].lower()
            if stem.endswith("_ndd") or stem.endswith("_n") or "sdf" in stem:
                if "noise" in stem:
                    continue
                normal_img = img
                break
    _, mask_img = _find_env_tex(tex_lookup, "MaskTexture", "Mask", "PM_SpecularMasks")

    if color_img:
        c_node = _new_tex_image(nodes, color_img, "Glass Color", (COL_TEX, 300))
        links.new(c_node.outputs["Color"], principled.inputs["Base Color"])
    else:
        col = _mi_colour(colours, "Color", "Tint", "Glass Color", default=(0.75, 0.85, 0.9, 1.0))
        principled.inputs["Base Color"].default_value = col

    if normal_img:
        n_node = _new_tex_image(
            nodes, normal_img, "Glass Normal", (COL_TEX, 0), non_color=True,
        )
        n_sock = _wire_normal_map(
            nodes, links, n_node.outputs["Color"], (COL_MIX, 0), strength=0.5,
            label="Glass Normal",
        )
        links.new(n_sock, principled.inputs["Normal"])

    if mask_img:
        m_node = _new_tex_image(
            nodes, mask_img, "Glass Mask", (COL_TEX, -350), non_color=True,
        )
        # Mask often dirt/opacity — feed alpha softly
        links.new(m_node.outputs["Color"], principled.inputs["Alpha"])

    rough = _mi_scalar(scalars, "Glass Roughness", "Roughness", default=0.05)
    principled.inputs["Roughness"].default_value = min(max(rough, 0.0), 0.5)
    try:
        principled.inputs["Transmission Weight"].default_value = 0.92
    except Exception:
        try:
            principled.inputs["Transmission"].default_value = 0.92
        except Exception:
            pass
    try:
        principled.inputs["IOR"].default_value = 1.45
    except Exception:
        pass
    metallic = _mi_scalar(scalars, "Interior Metallic", "Metallic High", default=0.0)
    if metallic > 0.01:
        principled.inputs["Metallic"].default_value = min(metallic, 0.4)

    translucent = _is_translucent_blend(mi) or bool(
        _mi_tex_params(mi) & {"CubemapInside", "CubemapOutside"}
    )
    if translucent and not mask_img:
        principled.inputs["Alpha"].default_value = 0.35
        _set_material_alpha_mode(mat, mode="BLEND", two_sided=True)
    elif mask_img or _is_masked_blend(mi):
        _set_material_alpha_mode(
            mat, mode="CLIP", threshold=float(mi.get("opacity_clip") or 0.3333), two_sided=True,
        )
    else:
        _set_material_alpha_mode(mat, mode="BLEND", two_sided=bool(mi.get("two_sided")))

    # Cubemaps left unconnected for inspection (env reflection stub)
    handled = {
        node.image.filepath
        for node in nodes
        if getattr(node, "image", None) is not None
    }
    _dump_unconnected_tex(nodes, tex_lookup, handled, COL_TEX - 500, -700)


def _setup_simple_material(mat, mi_path: str, psk_path: str = ""):
    """Best-effort Principled for unknown / BaseColor+Normal MIs."""
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_SIMPLE)

    log = utils.get_logger()
    params = sorted(_mi_tex_params(mi))
    log.info(
        "Simple/fallback MI setup for '%s' params=%s",
        os.path.basename(mi_path), params,
    )

    COL_TEX, COL_MIX, COL_BSDF, COL_OUT = -1200, -200, 400, 700
    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    local_folders = _character_layout_search_folders(
        mi_path, psk_path if psk_path else "",
    )
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    _, albedo_img = _find_env_tex(tex_lookup, *_SIMPLE_ALBEDO_KEYS)
    _, normal_img = _find_env_tex(tex_lookup, *_SIMPLE_NORMAL_KEYS)
    _, rm_img = _find_env_tex(tex_lookup, *_SIMPLE_ROUGHNESS_METAL_KEYS)
    _, tintmask_img = _find_env_tex(tex_lookup, *_SIMPLE_TINTMASK_KEYS)

    # Tree trunks that fall through to simple with missing maps — infer vegetation textures
    stem_l = os.path.splitext(os.path.basename(mi_path or ""))[0].lower()
    clearly_trunk = "trunk" in stem_l or "bark" in stem_l
    if (clearly_trunk or (
        any(k in stem_l for k in ("pine", "cypress", "juniper", "aleppo", "stonepine"))
        and "billboard" not in stem_l
        and "branch" not in stem_l
        and "leaf" not in stem_l
    )) and (albedo_img is None or normal_img is None):
        used = {fp for _p, (fp, _im) in tex_lookup.items() if fp}
        if albedo_img is None:
            path, reason = infer_vegetation_texture(
                "albedo", mi_path, psk_path, mi_stem=stem_l, exclude_paths=used,
            )
            img = _load_fallback_tex_image(path)
            if img is not None:
                albedo_img = img
                _stamp_tex_fallback(mat, "albedo", path, reason)
                used.add(path)
        if normal_img is None:
            path, reason = infer_vegetation_texture(
                "normal", mi_path, psk_path, mi_stem=stem_l, exclude_paths=used,
            )
            img = _load_fallback_tex_image(path)
            if img is not None:
                normal_img = img
                _stamp_tex_fallback(mat, "normal", path, reason)

    albedo_param = ""
    if albedo_img:
        albedo_param = next((p for p, (_fp, im) in tex_lookup.items() if im == albedo_img), "")
        a_node = _new_tex_image(nodes, albedo_img, f"Albedo ({albedo_param})", (COL_TEX, 300))
        links.new(a_node.outputs["Color"], principled.inputs["Base Color"])
        # CR alpha → roughness; CA/BaseColor/Color alpha → opacity when masked
        pl = albedo_param.lower()
        stem = os.path.splitext(os.path.basename(
            next((fp for _p, (fp, im) in tex_lookup.items() if im == albedo_img), "")
        ))[0].lower()
        if stem.endswith("_cr") or pl in ("cr", "cr texture", "bc"):
            links.new(a_node.outputs["Alpha"], principled.inputs["Roughness"])
        elif (
            stem.endswith(("_ca", "_color", "_c"))
            or pl in ("ca", "1. ca", "basecolor", "coloralpha", "color")
        ):
            if _is_masked_blend(mi) or mi.get("two_sided"):
                links.new(a_node.outputs["Alpha"], principled.inputs["Alpha"])
                _set_material_alpha_mode(
                    mat,
                    mode="CLIP",
                    threshold=float(mi.get("opacity_clip") or 0.3333),
                    two_sided=bool(mi.get("two_sided")),
                )
    else:
        tint = _mi_colour(colours, "Tint", "Color", "BaseColor", default=(0.45, 0.45, 0.45, 1.0))
        if tint:
            principled.inputs["Base Color"].default_value = tint
        log.warning("Simple MI '%s' has no resolvable albedo texture", os.path.basename(mi_path))

    if normal_img:
        n_node = _new_tex_image(
            nodes, normal_img, "Normal", (COL_TEX, -50), non_color=True,
        )
        n_str = min(max(_mi_scalar(scalars, "Normal Strength", default=1.0), 0.0), 2.5)
        n_sock = _wire_normal_map(
            nodes, links, n_node.outputs["Color"], (COL_MIX, -50), strength=n_str,
        )
        links.new(n_sock, principled.inputs["Normal"])
        stem = os.path.splitext(os.path.basename(
            next((fp for _p, (fp, im) in tex_lookup.items() if im == normal_img), "")
        ))[0].lower()
        if any(stem.endswith(s) for s in ("_nxm", "_nmx", "_nom")):
            links.new(n_node.outputs["Alpha"], principled.inputs["Metallic"])

    if rm_img is not None:
        # Hero RoughnessMetal: R → Roughness, G → Metallic (name order; channel stats agree).
        rm_node = _new_tex_image(
            nodes, rm_img, "RoughnessMetal", (COL_TEX, -350), non_color=True,
        )
        sep = nodes.new("ShaderNodeSeparateColor")
        sep.label = "Rough / Metal"
        sep.location = (COL_MIX - 80, -350)
        links.new(rm_node.outputs["Color"], sep.inputs["Color"])
        if not principled.inputs["Roughness"].links:
            links.new(sep.outputs["Red"], principled.inputs["Roughness"])
        if not principled.inputs["Metallic"].links:
            links.new(sep.outputs["Green"], principled.inputs["Metallic"])

    if tintmask_img is not None:
        # Colourway tint mask — keep as inspectable Image when no tint vectors present.
        _new_tex_image(
            nodes, tintmask_img, "TintMask", (COL_TEX - 280, -650), non_color=True,
        )

    if not principled.inputs["Roughness"].links:
        principled.inputs["Roughness"].default_value = _mi_scalar(
            scalars, "Roughness", "3. Roughness", default=0.55,
        )

    handled = {
        node.image.filepath
        for node in nodes
        if getattr(node, "image", None) is not None
    }
    _dump_unconnected_tex(nodes, tex_lookup, handled, COL_TEX - 500, -500)


def _needs_map_decal_mask_rebuild(mat, mi_path: str = "") -> bool:
    """True for pre-v2 map decals / GraphicAtlas that need a fresh graph."""
    if mat is None:
        return False
    path = (mi_path or str(mat.get("arc_mi_path") or "")).strip()
    if not path or not os.path.isfile(path):
        return False
    try:
        mi = _parse_flat_mi_json(path)
    except Exception:
        return False
    if _is_enemy_decal_mi(mi):
        return False
    stem_l = os.path.splitext(os.path.basename(path))[0].lower()
    # GraphicAtlas previously fell through to simple/metal (no Graphic Atlas wire).
    if _is_graphic_atlas_mi(mi, stem_l) and not mat.get("arc_graphic_atlas"):
        return True
    if str(mat.get("arc_mi_family") or "") != FAMILY_DECAL:
        return False
    if str(mat.get("arc_decal_mask_setup") or "") == _MAP_DECAL_MASK_SETUP_V:
        return False
    switches = mi.get("switches") or {}
    if _mi_switch(switches, "UseAlphaForMask", default=None) is True:
        return True
    params = {str(p).lower() for p, _ in (mi.get("textures") or [])}
    if params & {"mask", "decal mask", "raider mark texture", "signtexture"}:
        return True
    for _p, obj in (mi.get("textures") or []):
        leaf = (obj or "").rsplit("/", 1)[-1].split(".", 1)[0].lower()
        if leaf.endswith("_x"):
            return True
    return False


def _map_decal_mask_from_alpha(mi: dict, mask_fpath: str = "") -> bool:
    """Choose Alpha vs Color for opacity. Branding ``*_X`` masks store the logo in A."""
    switches = mi.get("switches") or {}
    flagged = _mi_switch(switches, "UseAlphaForMask", default=None)
    if flagged is True:
        return True
    if flagged is False:
        return False
    stem = os.path.splitext(os.path.basename(mask_fpath or ""))[0].lower()
    return stem.endswith("_x")


def _graphic_atlas_uv_layer_name(use_uv1: bool, mesh=None) -> str | None:
    """Pick the Blender UV layer name for GraphicAtlas sampling.

    Authored ``Use UV1`` means UE TexCoord[1]. After
    :func:`utils.normalize_ue_uv_layer_names` that is ``UV1``. Fall back through
    PSK's ``EXTRAUV0`` and index-1 for meshes that were not renormalized yet.
    Returns None to use the active UV (TexCoord node).
    """
    if not use_uv1:
        return None
    # Prefer UEFormat / normalized names; PSK EXTRAUV0 is TexCoord1 pre-normalize.
    candidates = ("UV1", "EXTRAUV0", "UVMap.001")
    if mesh is not None:
        names = [uv.name for uv in (getattr(mesh, "uv_layers", None) or [])]
        for name in candidates:
            if name in names:
                return name
        if len(names) > 1:
            return names[1]
        # Single UV channel (some poster cards): Use UV1 still authored, but only UV0 exists.
        return names[0] if names else None
    return "UV1"


def _graphic_atlas_uv_vector(nodes, links, mi: dict, loc, use_uv1: bool = False, mesh=None):
    """AtlasPosition cell + GraphicTiling for company branding poster sheets."""
    scalars = mi.get("scalars") or {}
    cols = max(float(_mi_scalar(scalars, "Number of columns", default=1.0) or 1.0), 1.0)
    rows = max(float(_mi_scalar(scalars, "Number of rows", default=1.0) or 1.0), 1.0)
    pos = float(_mi_scalar(scalars, "AtlasPosition", default=0.0) or 0.0)
    tiling = float(_mi_scalar(scalars, "GraphicTiling", "Tiling", default=1.0) or 1.0)
    # Linear atlas index → (col, row)
    idx = int(round(pos)) if abs(pos - round(pos)) < 1e-4 else int(pos)
    col_i = idx % int(cols)
    row_i = idx // int(cols)
    if row_i >= int(rows):
        row_i = int(rows) - 1
    scale_u = (1.0 / cols) * max(tiling, 0.001)
    scale_v = (1.0 / rows) * max(tiling, 0.001)
    loc_u = float(col_i) / cols
    # UE atlas rows typically grow downward in V; flip so row 0 is top.
    loc_v = 1.0 - (float(row_i) + 1.0) / rows

    # M_Graphic_Atlas_01 samples TexCoord[1] when Use UV1 is on (poster cards).
    uv_layer = _graphic_atlas_uv_layer_name(use_uv1, mesh=mesh)
    if uv_layer:
        uv_map = nodes.new("ShaderNodeUVMap")
        uv_map.location = (loc[0] - 220, loc[1])
        uv_map.uv_map = uv_layer
        uv_map.label = f"UE TexCoord[{1 if use_uv1 else 0}] ({uv_layer})"
        from_uv = uv_map.outputs["UV"]
    else:
        uv = nodes.new("ShaderNodeTexCoord")
        uv.location = (loc[0] - 220, loc[1])
        from_uv = uv.outputs["UV"]
    mapping = nodes.new("ShaderNodeMapping")
    mapping.label = f"Atlas {col_i},{row_i} ({int(cols)}x{int(rows)})"
    mapping.location = loc
    mapping.inputs["Scale"].default_value = (scale_u, scale_v, 1.0)
    mapping.inputs["Location"].default_value = (loc_u, loc_v, 0.0)
    links.new(from_uv, mapping.inputs["Vector"])
    return mapping.outputs["Vector"]


def _setup_graphic_atlas_material(mat, mi_path: str, psk_path: str = "", mesh=None):
    """MCP / company branding posters — Graphic Atlas albedo + AtlasPosition cell."""
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_DECAL)
    try:
        mat["arc_decal_mask_setup"] = _MAP_DECAL_MASK_SETUP_V
        mat["arc_graphic_atlas"] = 1
    except Exception:
        pass

    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []
    switches = mi.get("switches") or {}
    COL_TEX, COL_BSDF, COL_OUT = -1000, 350, 650
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    brightness = _mi_scalar(scalars, "Atlas Brightness", default=1.0)
    bc = _mi_colour(
        colours, "BC Color", "Base Color", "BaseColor", "Color", "Tint",
        default=(0.85, 0.85, 0.85, 1.0),
    )
    if bc and len(bc) >= 3:
        principled.inputs["Base Color"].default_value = (
            float(bc[0]) * float(brightness or 1.0),
            float(bc[1]) * float(brightness or 1.0),
            float(bc[2]) * float(brightness or 1.0),
            1.0,
        )

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    atlas_param, atlas_img = _find_env_tex(
        tex_lookup, "Graphic Atlas", "Atlas", "BaseColor", "CR", "CA",
    )
    _, normal_img = _find_env_tex(
        tex_lookup, "NX", "Normal Overlay", "Normal", "Normals", "NOH", "NXX",
    )
    # Parent M_Graphic_Atlas_01 defaults Use UV1 on; missing switch ⇒ UV1.
    use_uv1 = _mi_switch(switches, "Use UV1", "UseUV1", default=True) is True
    atlas_vec = _graphic_atlas_uv_vector(
        nodes, links, mi, (COL_TEX - 280, 250), use_uv1=use_uv1, mesh=mesh,
    )

    if atlas_img:
        a_node = _new_tex_image(
            nodes, atlas_img, f"Graphic Atlas ({atlas_param})", (COL_TEX, 250),
        )
        links.new(atlas_vec, a_node.inputs["Vector"])
        links.new(a_node.outputs["Color"], principled.inputs["Base Color"])

    if normal_img and normal_img != atlas_img:
        n_str = min(max(_mi_scalar(scalars, "Normal Strength", "Blend Texture Normal Strength", default=1.0), 0.0), 2.5)
        n_node = _new_tex_image(nodes, normal_img, "Poster NX", (COL_TEX, -100), non_color=True)
        n_vec = _graphic_atlas_uv_vector(
            nodes, links, mi, (COL_TEX - 280, -100), use_uv1=use_uv1, mesh=mesh,
        )
        links.new(n_vec, n_node.inputs["Vector"])
        n_sock = _wire_normal_map(
            nodes, links, n_node.outputs["Color"], (-100, -100), strength=n_str,
        )
        links.new(n_sock, principled.inputs["Normal"])

    # Opaque paper card by default (frame PropTrim handles the metal border).
    _set_material_alpha_mode(mat, mode="OPAQUE", two_sided=True)
    principled.inputs["Roughness"].default_value = min(
        max(_mi_scalar(scalars, "Roughness", "Roughness Bias", default=0.65), 0.05), 1.0,
    )
    handled = {
        node.image.filepath
        for node in nodes
        if getattr(node, "image", None) is not None
    }
    _dump_unconnected_tex(nodes, tex_lookup, handled, COL_TEX - 500, -500)


def _setup_map_decal_material(mat, mi_path: str, psk_path: str = "", mesh=None):
    """Map murals / branding / CA+NOH crack decals; fall back to enemy NAO path."""
    mi = _parse_flat_mi_json(mi_path)
    if _is_enemy_decal_mi(mi):
        _setup_enemy_decal_material(mat, mi_path, psk_path)
        _stamp_mi_family(mat, FAMILY_DECAL)
        return
    stem_l = os.path.splitext(os.path.basename(mi_path or ""))[0].lower()
    if _is_graphic_atlas_mi(mi, stem_l):
        _setup_graphic_atlas_material(mat, mi_path, psk_path, mesh=mesh)
        return

    # Mask / Sign / Raider mark / CA+NOH — CLIP alpha
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_DECAL)
    try:
        mat["arc_decal_mask_setup"] = _MAP_DECAL_MASK_SETUP_V
    except Exception:
        pass

    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []
    COL_TEX, COL_BSDF, COL_OUT = -1000, 350, 650
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    bc = _mi_colour(
        colours, "BC Color", "Base Color", "BaseColor", "Color", "Tint",
        default=(0.85, 0.85, 0.85, 1.0),
    )
    if bc and len(bc) >= 3:
        principled.inputs["Base Color"].default_value = (
            float(bc[0]), float(bc[1]), float(bc[2]), 1.0,
        )

    tile_u = _mi_scalar(scalars, "Tile U", "Tile", "Tiling", default=1.0)
    tile_v = _mi_scalar(scalars, "Tile V", "Tile", "Tiling", default=tile_u)

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    mask_param, mask_img = _find_env_tex(
        tex_lookup, "Mask", "Raider Mark Texture", "Decal Mask", "SignTexture", "X",
    )
    color_param, color_img = _find_env_tex(
        tex_lookup,
        "BaseColor", "CR", "CA", "PM_Diffuse", "Color Overlay", "Graphic Atlas",
    )
    _, normal_img = _find_env_tex(
        tex_lookup, "Normal Overlay", "Normal", "Normals", "NOH", "NAO", "PM_Normals", "NX",
    )

    def _apply_tile(tex_node, row_y: float) -> None:
        if abs(float(tile_u) - 1.0) < 0.01 and abs(float(tile_v) - 1.0) < 0.01:
            return
        uv = nodes.new("ShaderNodeTexCoord")
        uv.location = (COL_TEX - 480, row_y)
        mapping = nodes.new("ShaderNodeMapping")
        mapping.label = f"Tile U×{tile_u:g} V×{tile_v:g}"
        mapping.location = (COL_TEX - 280, row_y)
        mapping.inputs["Scale"].default_value = (
            max(float(tile_u), 0.001),
            max(float(tile_v), 0.001),
            1.0,
        )
        links.new(uv.outputs["UV"], mapping.inputs["Vector"])
        links.new(mapping.outputs["Vector"], tex_node.inputs["Vector"])

    if color_img and color_img != mask_img:
        c_node = _new_tex_image(nodes, color_img, "Decal Color", (COL_TEX, 250))
        _apply_tile(c_node, 250)
        links.new(c_node.outputs["Color"], principled.inputs["Base Color"])
        links.new(c_node.outputs["Alpha"], principled.inputs["Alpha"])
    elif mask_img:
        mask_fpath = ""
        for _p, (fp, im) in tex_lookup.items():
            if im == mask_img:
                mask_fpath = fp
                break
        m_node = _new_tex_image(nodes, mask_img, "Decal Mask", (COL_TEX, 250), non_color=True)
        _apply_tile(m_node, 250)
        # Branding T_*_X: RGB is solid white, logo lives in Alpha (UseAlphaForMask).
        if _map_decal_mask_from_alpha(mi, mask_fpath):
            links.new(m_node.outputs["Alpha"], principled.inputs["Alpha"])
        else:
            links.new(m_node.outputs["Color"], principled.inputs["Alpha"])
    elif color_img:
        # CA used as both albedo+mask when no separate Mask param
        c_node = _new_tex_image(nodes, color_img, "Decal Color", (COL_TEX, 250))
        _apply_tile(c_node, 250)
        links.new(c_node.outputs["Color"], principled.inputs["Base Color"])
        links.new(c_node.outputs["Alpha"], principled.inputs["Alpha"])

    if normal_img and normal_img != mask_img and normal_img != color_img:
        n_str = min(max(_mi_scalar(scalars, "Normal Strength", default=1.0), 0.0), 2.5)
        n_node = _new_tex_image(nodes, normal_img, "Decal Normal", (COL_TEX, -100), non_color=True)
        _apply_tile(n_node, -100)
        n_sock = _wire_normal_map(
            nodes, links, n_node.outputs["Color"], (-100, -100), strength=n_str,
        )
        links.new(n_sock, principled.inputs["Normal"])

    _set_material_alpha_mode(
        mat,
        mode="CLIP",
        threshold=float(mi.get("opacity_clip") or 0.3333),
        two_sided=True,
    )
    principled.inputs["Roughness"].default_value = min(
        max(_mi_scalar(scalars, "Roughness", "Roughness Bias", default=0.7), 0.05), 1.0,
    )
    _ = mask_param, color_param


def _setup_tarp_material(mat, mi_path: str, psk_path: str = ""):
    """Fabric / tarp / awning — Base Color + Variation Mask, two-sided masked.

    Real example: ``MI_Cmr_Awning_02_Tarp_A`` (``M_PropPreset_NR+VPO+Detail``) with
    ``Variation Mask`` → ``_CR``, ``Base Color`` / ``Subsurface Color`` vectors,
    ``Tile``, ``TwoSided`` + ``BLEND_Masked``.
    """
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_TARP)

    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []
    COL_TEX, COL_BSDF, COL_OUT = -900, 200, 500

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    # Prefer authored Base Color vector so tarps aren't white when textures thin
    base_rgba = _mi_colour(
        colours, "Base Color", "RT Base Color", "Base Color Variation",
        default=(0.45, 0.42, 0.35, 1.0),
    )
    if base_rgba and len(base_rgba) >= 3:
        principled.inputs["Base Color"].default_value = (
            float(base_rgba[0]), float(base_rgba[1]), float(base_rgba[2]), 1.0,
        )

    tile = _mi_scalar(scalars, "Tile", "Tiling", "Variation Mask Tile", default=1.0)
    _, var_img = _find_env_tex(
        tex_lookup, "Variation Mask", "CR", "BaseColor", "PM_Diffuse", "CA",
    )
    _, n_img = _find_env_tex(
        tex_lookup, "Normal", "Normals", "NormalMap", "NOH", "NR", "PM_Normals",
    )

    albedo = None
    if var_img:
        v_node = _new_tex_image(nodes, var_img, "Variation / CR", (COL_TEX, 200))
        if abs(tile - 1.0) > 0.01:
            vec = _mapping_tiled(nodes, links, tile, (COL_TEX - 280, 200))
            links.new(vec, v_node.inputs["Vector"])
        # Multiply variation into base colour
        mul = nodes.new("ShaderNodeMix")
        mul.data_type = "RGBA"
        mul.blend_type = "MULTIPLY"
        mul.label = "Base × Variation"
        mul.location = (-200, 200)
        strength = _mi_scalar(
            scalars, "Bc Variation Strength", "Variation Mask Intensity", default=0.5,
        )
        mul.inputs["Factor"].default_value = min(max(float(strength), 0.0), 1.0)
        # Solid colour as A
        rgb = nodes.new("ShaderNodeRGB")
        rgb.location = (COL_TEX, 420)
        rgb.outputs[0].default_value = principled.inputs["Base Color"].default_value
        links.new(rgb.outputs[0], mul.inputs[6])
        links.new(v_node.outputs["Color"], mul.inputs[7])
        albedo = mul.outputs[2]
        links.new(albedo, principled.inputs["Base Color"])
        # Masked opacity from CR alpha when present
        links.new(v_node.outputs["Alpha"], principled.inputs["Alpha"])

    if n_img:
        n_node = _new_tex_image(nodes, n_img, "Tarp Normal", (COL_TEX, -120), non_color=True)
        if abs(tile - 1.0) > 0.01:
            vec = _mapping_tiled(nodes, links, tile, (COL_TEX - 280, -120))
            links.new(vec, n_node.inputs["Vector"])
        n_str = _mi_scalar(scalars, "Normal Strength", default=0.5)
        n_sock = _wire_normal_map(
            nodes, links, n_node.outputs["Color"], (-100, -120),
            strength=min(max(n_str, 0.0), 2.0),
        )
        links.new(n_sock, principled.inputs["Normal"])

    rough_mul = _mi_scalar(scalars, "Rough Multiplier", "Roughness", default=1.0)
    principled.inputs["Roughness"].default_value = min(max(0.35 * rough_mul, 0.2), 1.0)

    # Soft subsurface stub (MSM_Subsurface on cooked tarps)
    ss = _mi_colour(colours, "Subsurface Color", default=None)
    if ss and len(ss) >= 3:
        try:
            principled.inputs["Subsurface Weight"].default_value = min(
                max(_mi_scalar(scalars, "Subsurface Strength", default=0.15), 0.0), 0.5,
            )
            principled.inputs["Subsurface Radius"].default_value = (
                float(ss[0]), float(ss[1]), float(ss[2]),
            )
        except Exception:
            pass

    two_sided = bool(mi.get("two_sided"))
    blend = str(mi.get("blend_mode") or "").upper()
    clip = float(mi.get("opacity_clip") or 0.3333)
    if "TRANSLUCENT" in blend or "transparent" in (mi_path or "").lower():
        _set_material_alpha_mode(mat, mode="BLEND", threshold=clip, two_sided=True)
    else:
        _set_material_alpha_mode(mat, mode="CLIP", threshold=clip, two_sided=True or two_sided)


def _dispatch_weapon_slot_material(mat, mi_path: str, psk_path: str, mi_stem_lower: str, slot_lower: str):
    """Route a single weapon/enemy/map SK material slot to its family setup."""
    log = utils.get_logger()
    try:
        mi_probe = _parse_flat_mi_json(mi_path)
        family = classify_mi_family(mi_probe, mi_stem_lower, slot_lower)
        _stamp_mi_family(mat, family)
        log.debug(
            "MI family '%s' → %s (slot=%s)",
            os.path.basename(mi_path), family, slot_lower or "(none)",
        )

        if family == FAMILY_EMISSIVE:
            _setup_weapon_emissive_material(mat, mi_path)
        elif family == FAMILY_SCAN:
            _setup_enemy_scan_display_material(mat, mi_path)
        elif family == FAMILY_DECAL:
            _setup_map_decal_material(mat, mi_path, psk_path)
        elif family == FAMILY_GLASS:
            _setup_glass_env_material(mat, mi_path, psk_path)
        elif family == FAMILY_WATER:
            _setup_water_material(mat, mi_path, psk_path)
        elif family == FAMILY_SAND:
            _setup_sand_material(mat, mi_path, psk_path)
        elif family == FAMILY_FOLIAGE:
            _setup_foliage_material(mat, mi_path, psk_path)
        elif family == FAMILY_TARP:
            _setup_tarp_material(mat, mi_path, psk_path)
        elif family in (FAMILY_ROAD, FAMILY_METAL, FAMILY_ENVIRONMENT):
            _setup_environment_material(mat, mi_path, psk_path, family=family)
        elif family == FAMILY_WEAPON:
            _setup_weapon_main_material(mat, mi_path, psk_path)
        else:
            _setup_simple_material(mat, mi_path, psk_path)
    except Exception as exc:
        # Soft-fail: never crash Stage 2 on a single bad MI
        log.error(
            "Material setup failed for '%s' (%s): %s — falling back to simple",
            os.path.basename(mi_path or ""), mi_stem_lower, exc,
        )
        try:
            _setup_simple_material(mat, mi_path, psk_path)
        except Exception as exc2:
            log.error("Simple fallback also failed for '%s': %s", mi_path, exc2)


def _mi_stem_from_blender_name(name: str) -> str:
    """Extract an MI_* stem from a Blender / BlenderUMap material name.

    Handles names like:
      MI_CatBed_01_A
      MI_CatBed_01_A.mat
      MI_CatBed_01_A.001
    """
    if not name:
        return ""
    stem = name.strip()
    stem = re.sub(r"\.mat$", "", stem, flags=re.IGNORECASE)
    stem = stem.split(".")[0].strip()
    # Skip materials we already created: Obj_MI_Foo_Mat
    if stem.lower().endswith("_mat") and "_mi_" in stem.lower():
        return ""
    if stem.lower().startswith("mi_"):
        return stem
    m = re.search(r"(MI_[A-Za-z0-9_]+)", stem, flags=re.IGNORECASE)
    return m.group(1) if m else ""


def fix_object_materials_from_mi_slots(
    obj, asset_folder: str = "", *, context: str = CTX_MAP,
) -> int:
    """Rebuild shaders for slots whose material names look like MI_*.

    Used when an object (e.g. from BlenderUMap) has MI-named materials but no
    sibling SK/SM mesh JSON is available. Returns the number of slots fixed.
    Shared MI datablocks are reused across meshes (map Stage 2).

    ``context`` defaults to map — never resolve Characters/ via FMDex basename.
    """
    if not obj or obj.type != "MESH":
        return 0

    from . import fmdex
    from . import textures as texmod

    log = utils.get_logger()
    fixed = 0
    for slot in obj.material_slots:
        if not slot.material:
            continue
        # Already wired to a shared Arc MI — skip rebuild unless out of context
        existing_key = str(slot.material.get("arc_mi_path", "") or "")
        if existing_key:
            if path_allowed_for_context(existing_key, context):
                fixed += 1
                continue
            # Stamped cosmetic MI on a map prop — drop so Stage 2 can rebuild
            # from UEModel MI names / valid SM JSON (never keep belt/helmet).
            try:
                log.warning(
                    "clearing out-of-context MI '%s' on map mesh '%s'",
                    existing_key, obj.name,
                )
            except Exception:
                pass
            try:
                old = slot.material
                stem_keep = _mi_stem_from_blender_name(old.name) or "MI_Pending"
                # Prefer renaming away from shared cosmetic datablock
                stub = bpy.data.materials.new(name=f"{stem_keep}_pending")
                stub.use_nodes = True
                slot.material = stub
            except Exception:
                slot.material = None
        mi_stem = _mi_stem_from_blender_name(
            slot.material.name if slot.material else ""
        )
        if not mi_stem:
            continue

        mi_path = ""
        folder = asset_folder or ""
        tags = []

        # FMDex: basename / full package path → exported JSON (context-gated)
        pkg, tags = fmdex.lookup_asset_path(mi_stem)
        if pkg:
            game_path = fmdex.package_to_game_path(pkg)
            if game_path and path_allowed_for_context(game_path, context):
                mi_path = texmod.find_asset_from_object_path(game_path, ".json")
            if not mi_path and context != CTX_MAP:
                mi_path = fmdex.resolve_export_file(
                    mi_stem, ".json", context=context, allow_basename_walk=True,
                )
            elif not mi_path and game_path:
                # Map: ObjectPath-only from FMDex full package keys — no basename walk
                mi_path = texmod.find_asset_from_object_path(game_path, ".json")
            if mi_path and not path_allowed_for_context(mi_path, context):
                mi_path = ""
            if mi_path:
                folder = os.path.dirname(mi_path)
                log.debug(
                    "FMDex MI '%s' → %s tags=%s", mi_stem, mi_path, tags
                )

        if not mi_path:
            mi_path = _resolve_mi_json_path(
                mi_stem, "", folder or asset_folder, context=context,
            )
            if mi_path:
                folder = os.path.dirname(mi_path)

        if not mi_path:
            log.warning(
                "MI JSON not found for '%s' on '%s' (folder=%s, ctx=%s, %s)",
                slot.material.name, obj.name, asset_folder or "(none)", context,
                fmdex.fmdex_summary_for_report(),
            )
            continue

        psk_hint = os.path.join(folder, "mesh.psk") if folder else ""
        try:
            tags_l = [t.lower() for t in (tags or [])]
            if tags_l and "materialinstanceconstant" not in tags_l and not any(
                "material" in t for t in tags_l
            ):
                log.debug(
                    "FMDex tags for '%s' are %s — still routing as MI slot",
                    mi_stem, tags,
                )
            mat = _get_or_build_shared_mi_material(
                mi_stem, mi_path, psk_hint, mi_stem.lower(),
            )
            if mat is None:
                continue
            slot.material = mat
            fixed += 1
        except Exception as e:
            log.error("Failed fixing MI slot '%s' on '%s': %s", mi_stem, obj.name, e)
    return fixed


def _ensure_mesh_material_slot_count(obj, count: int) -> int:
    """Grow ``obj.data.materials`` so index-aligned SM/SK slots can be assigned.

    Tiny UEModel planes (e.g. SM_WaterPlane) often import with zero slots; Stage 2
    previously matched nothing and left the mesh unassigned / Principled-white.
    """
    if not obj or obj.type != "MESH" or not obj.data or count <= 0:
        return 0
    mesh = obj.data
    added = 0
    while len(mesh.materials) < count:
        mesh.materials.append(None)
        added += 1
    return added


def _principled_base_color_info(mat) -> tuple[tuple[float, float, float] | None, bool]:
    """Return (rgb or None, has_image_tex_linked_to_base_color)."""
    if mat is None or not getattr(mat, "use_nodes", False) or not mat.node_tree:
        return None, False
    for node in mat.node_tree.nodes:
        if node.type != "BSDF_PRINCIPLED":
            continue
        sock = node.inputs.get("Base Color")
        if sock is None:
            return None, False
        linked_tex = False
        for link in sock.links:
            from_node = link.from_node
            if from_node is None:
                continue
            if from_node.type == "TEX_IMAGE" and getattr(from_node, "image", None):
                linked_tex = True
                break
            # Follow one Mix/RGB hop for water shore mixes etc.
            if from_node.type in {"MIX", "MIX_RGB", "RGB", "GAMMA", "HUE_SAT"}:
                for inp in getattr(from_node, "inputs", []) or []:
                    for ln in getattr(inp, "links", []) or []:
                        src = ln.from_node
                        if src is not None and src.type == "TEX_IMAGE" and getattr(src, "image", None):
                            linked_tex = True
                            break
                    if linked_tex:
                        break
        try:
            dv = sock.default_value
            rgb = (float(dv[0]), float(dv[1]), float(dv[2]))
        except Exception:
            rgb = None
        return rgb, linked_tex
    return None, False


def _is_default_white_rgb(rgb, tol: float = 0.06) -> bool:
    if not rgb or len(rgb) < 3:
        return False
    r, g, b = float(rgb[0]), float(rgb[1]), float(rgb[2])
    # Blender Principled default ≈ 0.8; pure white 1,1,1; near-greys without tex
    if abs(r - 1.0) <= tol and abs(g - 1.0) <= tol and abs(b - 1.0) <= tol:
        return True
    if abs(r - 0.8) <= tol and abs(g - 0.8) <= tol and abs(b - 0.8) <= tol:
        return True
    return False


def material_slot_needs_repair(mat) -> tuple[bool, str]:
    """True when a slot is empty / unstamped Arc white / default Principled white."""
    if mat is None:
        return True, "empty_slot"
    family = str(mat.get("arc_mi_family", "") or "").strip()
    mi_path = str(mat.get("arc_mi_path", "") or "").strip()
    name_l = (mat.name or "").lower()
    # Engine placeholder on DecalMesh cards — treat as unassigned
    if "worldgridmaterial" in name_l or name_l in {"material", "dots stroke"}:
        return True, "engine_placeholder"
    if family == FAMILY_WATER and _needs_water_setup_rebuild(mat, mi_path):
        return True, "stale_water_setup"
    if family and mi_path:
        # Already routed by Stage 2 / shared MI cache
        if family == FAMILY_WATER:
            return False, "water_ok"
        if family == FAMILY_DECAL and _needs_map_decal_mask_rebuild(mat, mi_path):
            return True, "stale_decal_mask"
        if family in (FAMILY_ENVIRONMENT, FAMILY_METAL, FAMILY_ROAD) and _needs_trim_setup_rebuild(
            mat, mi_path,
        ):
            return True, "stale_trim_setup"
        if _needs_proptrim_or_glass_rebuild(mat, mi_path, str(mat.get("arc_mi_stem") or "")):
            return True, "stale_proptrim_or_glass"
        rgb, linked = _principled_base_color_info(mat)
        if linked:
            return False, "textured_ok"
        if rgb is not None and not _is_default_white_rgb(rgb):
            return False, "tinted_ok"
        # Stamped but still default white (failed soft setup)
        if _is_default_white_rgb(rgb) and not linked:
            return True, "stamped_white"
        return False, "stamped_ok"
    rgb, linked = _principled_base_color_info(mat)
    if linked:
        return False, "textured_unstamped"
    if name_l.startswith("mi_") or ".mi_" in name_l:
        # MI-named placeholder from BlenderUMap — rebuild from JSON
        return True, "mi_named_unbuilt"
    if rgb is None:
        return True, "no_principled"
    if _is_default_white_rgb(rgb):
        return True, "default_white"
    # Non-white unstamped colour — still try to upgrade via SM/MI when possible
    return False, "custom_tint"


def object_needs_material_repair(obj) -> tuple[bool, str]:
    """True when any mesh slot is empty or effectively white / unassigned."""
    if not obj or obj.type != "MESH":
        return False, "not_mesh"
    slots = list(obj.material_slots or [])
    if not slots:
        return True, "no_slots"
    reasons = []
    # Map props: character/outfit MI paths are crossover artifacts (belt on roof).
    is_map = str(obj.get("arc_model_type") or "").strip().lower() == "map"
    if not is_map:
        psk = str(obj.get("arc_psk_path") or obj.get("arc_mesh_file") or "")
        is_map = context_from_model_type("", psk) == CTX_MAP
    for slot in slots:
        mat = slot.material
        if is_map and mat is not None:
            mi_path = str(mat.get("arc_mi_path") or "").strip()
            if mi_path and not path_allowed_for_context(mi_path, CTX_MAP):
                reasons.append("out_of_context_outfit")
                continue
        need, why = material_slot_needs_repair(mat)
        if need:
            reasons.append(why)
    if reasons:
        return True, "+".join(sorted(set(reasons)))
    return False, "ok"


def water_mi_hint_from_actor_name(actor_name: str = "") -> str:
    """BP_WaterPlane_MinorSwamp_C_UAID_… → MI_Water_MinorSwamp.

    Known RiverTool BP suffixes map to cooked ``MI_Water_*`` beside
    ``SM_WaterPlane_32x32``. Generic ``BP_WaterPlane_C_*`` returns empty so fuzzy
    / map heuristics can choose (BuriedCity→DuneLagoon, BlueGate→BlueGate, etc.).
    """
    raw = (actor_name or "").strip()
    if not raw:
        return ""
    # Explicit known variants (order: longer first)
    known = (
        ("minorswamp", "MI_Water_MinorSwamp"),
        ("dunelagoon_b", "MI_Water_DuneLagoon_B"),
        ("dunelagoon", "MI_Water_DuneLagoon"),
        ("shallowlake_b", "MI_Water_ShallowLake_B"),
        ("shallowlake", "MI_Water_ShallowLake"),
        ("driedriver", "MI_Water_DriedRiver_01"),
        ("redlake", "MI_Water_RedLake_01"),
        ("swamp", "MI_Water_Swamp_01"),
        ("cleanwater2", "MI_Water_ShallowLake"),
        ("cleanwater", "MI_Water_ShallowLake"),
        ("buriedcity", "MI_Water_BuriedCity_01"),
        ("bluegate", "MI_WaterBlueGate_01"),
        ("floodedpowerhouse", "MI_Water_FloodedPowerhouse_01"),
        ("rockycreek", "MI_Water_River_Opaque_River_RockyCreek"),
    )
    low = raw.lower()
    if "waterplane" not in low.replace("_", "") and "sm_waterplane" not in low:
        # Still allow BP_WaterPlane_* without requiring the substring twice
        if "bp_waterplane" not in low and "waterplane" not in low:
            return ""
    for key, mi in known:
        if key in low:
            return mi
    m = re.search(
        r"BP_WaterPlane_([A-Za-z][A-Za-z0-9]*(?:_[A-Za-z][A-Za-z0-9]*)*?)(?:_C(?:_|$)|$)",
        raw,
        flags=re.IGNORECASE,
    )
    if not m:
        return ""
    variant = m.group(1)
    if not variant:
        return ""
    # Reject UAID-looking captures (hex blobs)
    if re.fullmatch(r"[0-9A-Fa-f]{8,}", variant.replace("_", "")):
        return ""
    if variant.lower() in {"c", "uaid"}:
        return ""
    return f"MI_Water_{variant}"


def _bp_decal_package_stem(actor_name: str = "") -> str:
    """``BP_Decal_AddonWall_01_C_0_…`` → ``BP_Decal_AddonWall_01``."""
    raw = (actor_name or "").strip()
    if not raw:
        return ""
    m = re.search(
        r"(BP_Decal_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*?)(?:_C(?:_|$)|$)",
        raw,
        flags=re.IGNORECASE,
    )
    return m.group(1) if m else ""


def _rank_bp_decal_mi_stems(stems: list[str], bp_body: str = "") -> list[str]:
    """Prefer MIs whose stem matches the BP body (AddonWall_01 → MI_AddonWall_01).

    BP_Decal_* packages often carry a leftover OverrideMaterials on a secondary
    DecalMesh1 component (parent-template residue) before the real DecalMesh slot.
    """
    body = (bp_body or "").strip().lower()
    ranked: list[tuple[tuple, str]] = []
    seen: set[str] = set()
    for stem in stems or []:
        s = (stem or "").strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        core = key
        if core.startswith("mi_"):
            core = core[3:]
        core_no_decal = core[6:] if core.startswith("decal_") else core
        exact = 0
        if body:
            if core == body or core_no_decal == body or core == f"decal_{body}":
                exact = 3
            elif body in core or body in core_no_decal or core_no_decal in body:
                exact = 2
            else:
                # Token overlap (Damage_Wall_01 ↔ Decal_Damage_Wall_01)
                body_toks = set(body.split("_"))
                mi_toks = set(core_no_decal.split("_"))
                if body_toks and body_toks <= mi_toks:
                    exact = 2
                elif body_toks and len(body_toks & mi_toks) >= max(2, len(body_toks) - 1):
                    exact = 1
        # Cooked class-looking *_C suffixes are usually the wrong leftover slot
        cooked = 1 if key.endswith("_c") else 0
        ranked.append(((-exact, cooked, len(key), key), s))
    ranked.sort(key=lambda t: t[0])
    return [s for _k, s in ranked]


def _mi_stems_from_bp_decal_override_json(
    bp_stem: str, psk_folder: str = "", bp_body: str = ""
) -> list[str]:
    """Read ``OverrideMaterials`` from ``BP_Decal_*.json`` (authoritative projector MI).

    Name-guess alone misses retargets (e.g. ``BP_Decal_CrackPlaster_01`` →
    ``MI_CrackWall_01``, ``BP_Decal_Drain_01`` → ``MI_Drain_02``).
    """
    stem = (bp_stem or "").strip()
    if not stem:
        return []
    # BP JSONs live beside MIs under MaterialLibrary/.../Decals/
    bp_path = _resolve_mi_json_path(stem, "", psk_folder or "")
    if not bp_path or not os.path.isfile(bp_path):
        return []
    try:
        with open(bp_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    try:
        for entry in utils.ue_export_entries(data):
            props = entry.get("Properties") or {}
            overrides = props.get("OverrideMaterials") or []
            if not isinstance(overrides, list):
                continue
            # Prefer the primary DecalMesh component over DecalMesh1 leftovers
            entry_name = str(entry.get("Name") or "").lower()
            primary = "decalmesh1" not in entry_name and (
                entry_name.startswith("decalmesh") or "decalmesh_gen" in entry_name
            )
            for ref in overrides:
                if not isinstance(ref, dict):
                    continue
                mi_stem = textures.mi_stem_from_material_ref(ref)
                if not mi_stem:
                    continue
                key = mi_stem.lower()
                if key in seen:
                    continue
                seen.add(key)
                if primary:
                    out.insert(0, mi_stem)
                else:
                    out.append(mi_stem)
    except Exception:
        pass
    body = bp_body
    if not body and stem.lower().startswith("bp_decal_"):
        body = stem[len("BP_Decal_") :]
    return _rank_bp_decal_mi_stems(out, body)


def _mi_json_is_map_decal_library(path: str = "") -> bool:
    """True when MI/BP JSON lives under MaterialLibrary Decals (map projector cards)."""
    pl = (path or "").replace("\\", "/").lower()
    if not pl:
        return False
    if "/materiallibrary/" in pl and "/decals/" in pl:
        return True
    if "/material_instances/decals/" in pl:
        return True
    return False


def decal_mi_stem_candidates_from_actor(actor_name: str = "") -> list[str]:
    """BP_Decal_CrackTarmac_01_C_… → [MI_CrackTarmac_01, MI_Decal_CrackTarmac_01].

    Also ``SM_Decal_AstraVenturo_01_A`` → ``MI_Decal_AstraVenturo_01``.
    Shared projector mesh is always ``SM_DecalMesh_*``; the BP/actor name carries the MI.

    Prefer ``OverrideMaterials`` from the BP JSON when present (authoritative), then
    name-guess ``MI_<body>`` / ``MI_Decal_<body>``.
    """
    raw = (actor_name or "").strip()
    if not raw:
        return []
    out: list[str] = []

    def _add(stem: str) -> None:
        stem = (stem or "").strip()
        if stem and stem not in out:
            out.append(stem)

    bp_stem = _bp_decal_package_stem(raw)
    if bp_stem:
        body = bp_stem[len("BP_Decal_") :] if bp_stem.lower().startswith("bp_decal_") else ""
        for ov in _mi_stems_from_bp_decal_override_json(bp_stem, "", body):
            _add(ov)
        if body:
            _add(f"MI_{body}")
            _add(f"MI_Decal_{body}")
        return out

    m = re.search(
        r"SM_Decal_([A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)",
        raw,
        flags=re.IGNORECASE,
    )
    if m:
        body = m.group(1)
        # Mesh letter variants: AstraVenturo_01_A → AstraVenturo_01
        body = re.sub(r"_[A-Za-z]$", "", body)
        joined = body.replace("_", "").lower()
        if joined.startswith("mesh") or joined in {"mesh01", "mesh02"}:
            return out
        _add(f"MI_Decal_{body}")
        _add(f"MI_{body}")
    return out


def preferred_mi_hint_from_actor_name(actor_name: str = "", psk_folder: str = "") -> str:
    """Water BP or BP_Decal/SM_Decal → preferred MI stem (resolved when possible).

    Only actor / BP / SM_Decal *names* are valid input — never pass asset paths or
    full object blobs (those can accidentally match ``SM_Decal_*`` / water tokens).
    """
    # Guard: callers sometimes concatenated asset paths; keep the actor-like token only.
    raw = (actor_name or "").strip()
    if not raw:
        return ""
    # If a path leaked in, prefer the last path segment / first whitespace token that
    # looks like BP_/SM_Decal_/StaticMeshActor_/waterplane.
    if "/" in raw or "\\" in raw or " " in raw:
        parts = re.split(r"[\s/\\]+", raw)
        pick = ""
        for p in parts:
            pl = p.lower()
            if (
                pl.startswith("bp_")
                or pl.startswith("sm_decal_")
                or "waterplane" in pl.replace("_", "")
                or pl.startswith("staticmeshactor")
            ):
                pick = p
                break
        raw = pick or parts[0]
    water = water_mi_hint_from_actor_name(raw)
    if water:
        return water
    # BP OverrideMaterials first (needs folder for resolve)
    bp_stem = _bp_decal_package_stem(raw)
    if bp_stem:
        body = bp_stem[len("BP_Decal_") :] if bp_stem.lower().startswith("bp_decal_") else ""
        for ov in _mi_stems_from_bp_decal_override_json(bp_stem, psk_folder or "", body):
            path = _resolve_mi_json_path(ov, "", psk_folder or "")
            if path:
                leaf = os.path.splitext(os.path.basename(path))[0].lower()
                if leaf.startswith("bp_"):
                    continue
                return ov
            # Authoritative override stem even if path resolve lags
            return ov
    cands = decal_mi_stem_candidates_from_actor(raw)
    if not cands:
        return ""
    for stem in cands:
        path = _resolve_mi_json_path(stem, "", psk_folder or "")
        if path and not os.path.basename(path).lower().startswith("bp_"):
            return stem
        if path and _mi_json_is_map_decal_library(path):
            # Skip BP JSON collision (stem resolved to blueprint package)
            leaf = os.path.splitext(os.path.basename(path))[0].lower()
            if leaf.startswith("bp_"):
                continue
            return stem
        if path:
            leaf = os.path.splitext(os.path.basename(path))[0].lower()
            if not leaf.startswith("bp_"):
                return stem
    return cands[0]


def _preferred_mi_is_single_slot_override(preferred_stem: str = "") -> bool:
    """True when preferred MI may replace slot 0 (water / map-decal cards only).

    Regular StaticMeshActor props must keep SM JSON StaticMaterials — never let a
    leaked preferred stem (or PropTrim/Vent atlas) clobber every slot.

    Architecture ``*Trim*_Decal_*`` / EdgeTrim names are NOT map decals even though
    they contain ``_decal_`` (that false positive wiped SMA slots / wrong family).

    Many map-decal MIs omit the ``MI_Decal_`` prefix (``MI_AddonWall_01``,
    ``MI_BrokenTile_01``, ``MI_Drain_02``). Accept those when the JSON resolves
    under MaterialLibrary Decals.
    """
    stem_raw = (preferred_stem or "").strip()
    stem = stem_raw.lower()
    if not stem:
        return False
    if stem.startswith("mi_water") or stem.startswith("m_water") or "waterplane" in stem:
        return True
    if "mi_water" in stem:
        return True
    # Architecture trim sheets that happen to include "Decal" in the MI name
    if _is_architecture_trim_stem(stem):
        return False
    if _is_prop_trim_atlas_mi_stem(stem) or _is_rebar_mi_stem(stem):
        return False
    if stem.startswith("mi_decal") or stem.startswith("mi_crack"):
        return True
    # Narrow: only leading map-decal patterns, not arbitrary *_decal_* mid-tokens
    if stem.startswith("m_decal") or stem.startswith("mi_cracktarmac"):
        return True
    # Decals library MIs without MI_Decal_ prefix (AddonWall, BrokenTile, …)
    if stem.startswith("mi_") or stem.startswith("m_"):
        path = _resolve_mi_json_path(stem_raw, "", "")
        if path and _mi_json_is_map_decal_library(path):
            leaf = os.path.splitext(os.path.basename(path))[0].lower()
            if not leaf.startswith("bp_"):
                return True
    return False


def _is_prop_trim_atlas_mi_stem(stem: str = "") -> bool:
    """Vent / HVAC / PropTrim sheet atlases — must not fuzzy-default onto unrelated props."""
    s = (stem or "").lower()
    if not s:
        return False
    if "proptrim" in s or "prop_trim" in s:
        return True
    if "ventilation" in s or "ventwall" in s or "ventspace" in s:
        return True
    # Token-boundary vent (avoid RivenTides / AstraVenturo false positives via path joins)
    return bool(re.search(r"(^|_)vent(ilation)?(_|$)", s))


def _is_rebar_mi_stem(stem: str = "") -> bool:
    """Metal rebar MIs — slot-local only; must not fuzzy-default onto whole props."""
    s = (stem or "").lower()
    if not s:
        return False
    return "rebar" in s


def preferred_mi_from_placement_rows(rows: list | None) -> str:
    """Majority BP water / BP_Decal MI hint shared by an instancer's placements."""
    from collections import Counter

    counts: Counter[str] = Counter()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        actor = str(row.get("actor_name") or "")
        hint = preferred_mi_hint_from_actor_name(actor)
        if hint and _preferred_mi_is_single_slot_override(hint):
            counts[hint] += 1
    if not counts:
        return ""
    stem, n = counts.most_common(1)[0]
    if n >= max(1, sum(counts.values()) // 2):
        return stem
    return ""


def _object_name_blob(obj, psk_path: str = "", asset_path: str = "") -> str:
    parts = [
        getattr(obj, "name", "") if obj is not None else "",
        getattr(getattr(obj, "data", None), "name", "") if obj is not None else "",
        str((obj.get("arc_asset_path") if obj is not None else "") or ""),
        str((obj.get("arc_actor_name") if obj is not None else "") or ""),
        str((obj.get("arc_preferred_mi") if obj is not None else "") or ""),
        asset_path or "",
        os.path.basename(psk_path or ""),
    ]
    if obj is not None and obj.parent is not None:
        parts.append(obj.parent.name)
    return " ".join(p for p in parts if p).lower()


def _family_hint_from_name_blob(blob: str) -> str:
    b = (blob or "").lower().replace("\\", "/")
    if any(x in b for x in _WATER_EXCLUDE_NAME):
        pass
    elif any(
        k in b
        for k in (
            "waterplane", "sm_waterplane", "/rivertool/", "mi_water",
            "oceanbackdrop", "oceanlod", "oceantile", "dunelagoon",
            "minorswamp", "shallowlake", "driedriver",
        )
    ):
        return FAMILY_WATER
    if any(k in b for k in ("sanddune", "sandpile", "sand_pile", "/dunes/", "mi_sand")):
        return FAMILY_SAND
    if any(
        k in b
        for k in (
            "/vegetation/", "/foliage/", "houseweeds", "statelessleaves",
            "mi_leaf", "mi_leaves", "sm_tree", "sm_bush", "sm_grass",
        )
    ):
        return FAMILY_FOLIAGE
    if any(
        k in b
        for k in (
            "decalmesh",
            "/textures/decals/",
            "bp_decal",
            "sm_decal_",
            "brandingdecals",
            "brandingposter",
            "graphicatlas",
            "/props/branding/",
            "mi_decal_",
            "mi_crack",
            "deferreddecal",
            "defereddecal",
            "cracktarmac",
            "crackconcrete",
            "crackplaster",
        )
    ):
        return FAMILY_DECAL
    if "tarp" in b or "awning" in b:
        return FAMILY_TARP
    # Road/asphalt surfaces — exclude crack-* decals already handled above
    if ("tarmac" in b or "asphalt" in b) and "decal" not in b and "crack" not in b:
        return FAMILY_ROAD
    if "glass" in b or "windowpane" in b:
        return FAMILY_GLASS
    return ""


def _list_mi_jsons_in_folders(folders: list[str]) -> list[tuple[str, str]]:
    """[(stem, abs_path), ...] for MI_/M_ JSON beside remapped mesh folders."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        try:
            for fname in os.listdir(folder):
                low = fname.lower()
                if not low.endswith(".json"):
                    continue
                if not (low.startswith("mi_") or low.startswith("m_")):
                    continue
                stem = os.path.splitext(fname)[0]
                path = os.path.join(folder, fname)
                key = os.path.normcase(os.path.normpath(path))
                if key in seen:
                    continue
                seen.add(key)
                out.append((stem, path))
        except OSError:
            continue
    return out


def _mesh_search_folders(psk_path: str = "", asset_folder: str = "") -> list[str]:
    folders: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        if not path:
            return
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            return
        if os.path.isdir(path):
            seen.add(key)
            folders.append(os.path.normpath(path))

    for seed in (asset_folder, os.path.dirname(psk_path or "")):
        _add(seed)
        if seed:
            for alt in utils.remap_path_into_content_dirs(os.path.join(seed, "__probe__")):
                _add(os.path.dirname(alt))
    if psk_path:
        for alt in utils.remap_path_into_content_dirs(psk_path):
            _add(os.path.dirname(alt))
    return folders


def _score_mi_candidate(
    mi_stem: str,
    mi_path: str,
    *,
    hint_tokens: set[str],
    family_hint: str,
    preferred_stem: str = "",
    allow_enemy_character_decals: bool = False,
) -> float:
    stem_l = (mi_stem or "").lower()
    if (
        not allow_enemy_character_decals
        and _is_character_enemy_outfit_decal_asset(mi_stem, mi_path)
    ):
        return -1000.0

    score = 0.0
    pref = (preferred_stem or "").lower()
    if pref and stem_l == pref:
        score += 40.0
    elif pref and pref in stem_l:
        score += 20.0

    mi = {}
    try:
        mi = _parse_flat_mi_json(mi_path) if mi_path else {}
    except Exception:
        mi = {}
    family = classify_mi_family(mi, stem_l)
    if family_hint and family == family_hint:
        score += 18.0
    elif family_hint == FAMILY_WATER and _is_water_mi(mi, stem_l):
        score += 18.0

    # Env meshes must not soft-match into character/enemy Decal family MIs
    if not allow_enemy_character_decals and family == FAMILY_DECAL and family_hint != FAMILY_DECAL:
        if _is_enemy_decal_mi(mi) or "enemy" in stem_l:
            return -1000.0
        # Gate/sticker Decals in the same folder are OK only with a decal hint
        if "decal" in stem_l and family_hint != FAMILY_DECAL:
            score -= 12.0

    tokens = _stem_tokens(mi_stem)
    if hint_tokens and tokens:
        overlap = hint_tokens & tokens
        score += 4.0 * len(overlap)
        ratio = difflib.SequenceMatcher(
            None, "".join(sorted(hint_tokens)), "".join(sorted(tokens)),
        ).ratio()
        score += 6.0 * ratio

    # Soft boosts for waterplane ↔ MI_Water_*
    if family_hint == FAMILY_WATER:
        if stem_l.startswith("mi_water") or stem_l.startswith("m_water"):
            score += 8.0
        if "ocean" in stem_l and ("backdrop" in stem_l or "lod" in stem_l):
            score += 8.0
    return score


def infer_map_mi_candidates(
    obj,
    psk_path: str = "",
    *,
    asset_path: str = "",
    limit: int = 5,
) -> list[tuple[str, str, float, str]]:
    """Fuzzy MI JSON candidates for a map mesh.

    Returns ``[(mi_stem, mi_path, score, reason), ...]`` best-first.
    Never ranks character/enemy/outfit Decal atlases for ordinary env meshes;
    Decal-family fuzzy is reserved for DecalMesh / sticker assets.
    """
    blob = _object_name_blob(obj, psk_path, asset_path)
    family_hint = _family_hint_from_name_blob(blob)
    preferred = ""
    if obj is not None:
        preferred = str(obj.get("arc_preferred_mi") or "").strip()
    if not preferred:
        # Actor / object name ONLY — never asset paths (false SM_Decal_* / water hits).
        actor_blob = " ".join(
            p for p in (
                str(obj.get("arc_actor_name") or "") if obj is not None else "",
                getattr(obj, "name", "") or "" if obj is not None else "",
            ) if p
        )
        preferred = preferred_mi_hint_from_actor_name(
            actor_blob, os.path.dirname(psk_path or ""),
        )

    # DecalMesh / sticker cards may fuzzy toward MI_*Decal*; walls/POI must not.
    allow_char_enemy_decals = False
    blob_path = blob.replace("\\", "/")
    allow_decal_family = bool(
        family_hint == FAMILY_DECAL
        or "decalmesh" in blob_path
        or "/textures/decals/" in blob_path
        or "bp_decal" in blob_path
        or "brandingposter" in blob_path
        or "graphicatlas" in blob_path
        or "/props/branding/" in blob_path
        or (obj is not None and obj.get("arc_decal_mesh"))
        or (obj is not None and obj.get("arc_poster_mesh"))
    )

    hint_tokens = _stem_tokens(blob) | _stem_tokens(os.path.basename(psk_path or ""))
    if preferred:
        hint_tokens |= _stem_tokens(preferred)
    if family_hint == FAMILY_WATER:
        hint_tokens |= {"water", "plane", "river", "lagoon", "swamp", "ocean"}
    if family_hint == FAMILY_DECAL:
        hint_tokens |= {"decal", "crack", "tarmac", "mask"}

    candidates: list[tuple[str, str, float, str]] = []
    seen: set[str] = set()

    def _add(stem: str, path: str, score: float, reason: str) -> None:
        if not stem or not path or not os.path.isfile(path):
            return
        if score < 0:
            return
        if (
            not allow_decal_family
            and _is_character_enemy_outfit_decal_asset(stem, path)
        ):
            return
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            return
        seen.add(key)
        candidates.append((stem, path, score, reason))

    # 1) Authoritative SM/SK StaticMaterials (with MapPlacements → Content remap)
    for slot_name, mi_stem, mi_path in _parse_sk_material_slots(psk_path, context=CTX_MAP) if psk_path else []:
        if not mi_stem or not mi_path:
            continue
        score = 50.0
        if preferred and mi_stem.lower() == preferred.lower():
            score += 25.0
            reason = f"sm_json+preferred:{slot_name or mi_stem}"
        else:
            reason = f"sm_json:{slot_name or mi_stem}"
        # When BP override prefers another water MI, boost that file if present
        if preferred and preferred.lower() != mi_stem.lower():
            pref_path = _resolve_mi_json_path(
                preferred, "", os.path.dirname(mi_path) or os.path.dirname(psk_path or ""),
            )
            if pref_path:
                _add(
                    preferred,
                    pref_path,
                    70.0,
                    f"bp_override:{preferred}",
                )
        _add(mi_stem, mi_path, score, reason)

    # 2) Adjacent / remapped folder MI_* fuzzy — HARD DISABLED.
    # Soft-matching Vent/PropTrim/Rebar/etc. by name similarity caused wrong
    # materials on multi-slot props. Use SM JSON only (+ water/decal preferred).
    if ENABLE_FUZZY_MI_INFER:
        mesh_stem_tokens = (
            _stem_tokens(os.path.basename(psk_path or ""))
            | _stem_tokens(os.path.basename((asset_path or "").replace("\\", "/")))
        )
        mesh_stem_tokens -= {
            "src", "static", "mesh", "actor", "staticmeshactor", "uaid", "spline",
        }
        folders = _mesh_search_folders(psk_path, os.path.dirname(psk_path or ""))
        for stem, path in _list_mi_jsons_in_folders(folders):
            score = _score_mi_candidate(
                stem, path,
                hint_tokens=hint_tokens,
                family_hint=family_hint,
                preferred_stem=preferred,
                allow_enemy_character_decals=allow_char_enemy_decals,
            )
            if score < 10.0:
                continue
            if (
                not allow_decal_family
                and "decal" in stem.lower()
                and family_hint != FAMILY_DECAL
            ):
                continue
            mi_tokens = _stem_tokens(stem)
            mesh_overlap = mesh_stem_tokens & mi_tokens
            if not mesh_overlap and not (
                preferred and preferred.lower() in stem.lower()
            ) and family_hint not in (FAMILY_WATER, FAMILY_DECAL, FAMILY_SAND, FAMILY_FOLIAGE):
                if score < 25.0:
                    continue
            if _is_prop_trim_atlas_mi_stem(stem):
                evidence = mesh_overlap | (hint_tokens & mi_tokens)
                trim_keys = {
                    "proptrim", "trim", "vent", "ventilation", "ventwall", "metal",
                    "painted", "worn", "rust",
                }
                if not (evidence & trim_keys) and not (
                    preferred and preferred.lower() == stem.lower()
                ):
                    continue
            if _is_rebar_mi_stem(stem):
                evidence = mesh_overlap | (hint_tokens & mi_tokens)
                rebar_keys = {"rebar", "rebars"}
                if not (evidence & rebar_keys) and not (
                    preferred and preferred.lower() == stem.lower()
                ):
                    continue
            _add(stem, path, score, f"fuzzy_folder:{os.path.basename(path)}")

    # 3) Preferred stem via global resolve (water / map-decal only when restricted)
    if preferred and _preferred_mi_is_single_slot_override(preferred):
        pref_path = _resolve_mi_json_path(
            preferred, "", os.path.dirname(psk_path or ""),
        )
        if pref_path:
            _add(preferred, pref_path, 65.0, f"preferred_resolve:{preferred}")

    candidates.sort(key=lambda t: (-t[2], t[0].lower()))
    return candidates[: max(1, int(limit or 5))]


def _fuzzy_fill_white_slots_only(obj, psk_path: str = "", asset_path: str = "") -> int:
    """Fill empty/white slots from SM JSON StaticMaterials (exact).

    When ``ENABLE_FUZZY_MI_INFER`` is False (default), never invent an MI from
    folder name similarity. Water/decal preferred_mi may still fill slot 0 when
    restricted by ``_preferred_mi_is_single_slot_override``.
    """
    if not obj or obj.type != "MESH":
        return 0
    fixed = 0
    slots = _parse_sk_material_slots(psk_path, context=CTX_MAP) if psk_path else []
    if slots:
        _ensure_mesh_material_slot_count(obj, len(slots))
        used = set()
        has_mi_named = any(
            _mi_stem_from_blender_name(s.material.name)
            for s in (obj.material_slots or [])
            if s.material
        )
        for idx, (slot_name, mi_stem, mi_path) in enumerate(slots):
            if not mi_stem or not mi_path:
                continue
            if not path_allowed_for_context(mi_path, CTX_MAP):
                continue
            target_slot, slot_i = _match_material_slot(
                obj, slot_name, idx, used, mi_stem,
                allow_index_fallback=not has_mi_named,
            )
            if target_slot is None:
                if has_mi_named:
                    continue
                if idx < len(obj.material_slots) and idx not in used:
                    slot_i = idx
                else:
                    continue
            need, _why = material_slot_needs_repair(
                obj.material_slots[slot_i].material
                if slot_i < len(obj.material_slots) else None
            )
            if not need:
                used.add(slot_i)
                continue
            if _assign_shared_mi_to_object(
                obj, mi_stem, mi_path, psk_path,
                slot_index=slot_i, slot_name=slot_name or mi_stem,
            ):
                fixed += 1
                used.add(slot_i)
        return fixed

    # No SM slots — exact preferred water/decal only (never folder fuzzy).
    preferred = ""
    if obj is not None:
        preferred = str(obj.get("arc_preferred_mi") or "").strip()
    if not preferred:
        preferred = preferred_mi_hint_from_actor_name(
            " ".join(
                p for p in (
                    str(obj.get("arc_actor_name") or ""),
                    getattr(obj, "name", "") or "",
                ) if p
            ),
            os.path.dirname(psk_path or ""),
        )
    if preferred and _preferred_mi_is_single_slot_override(preferred):
        pref_path = _resolve_mi_json_path(
            preferred, "", os.path.dirname(psk_path or ""),
        )
        if pref_path:
            for idx, slot in enumerate(obj.material_slots or []):
                need, _why = material_slot_needs_repair(slot.material)
                if not need:
                    continue
                return 1 if _assign_shared_mi_to_object(
                    obj, preferred, pref_path, psk_path,
                    slot_index=idx, slot_name=preferred,
                ) else 0
            if not obj.material_slots:
                return 1 if _assign_shared_mi_to_object(
                    obj, preferred, pref_path, psk_path, slot_name=preferred,
                ) else 0

    if not ENABLE_FUZZY_MI_INFER:
        return 0

    # Legacy fuzzy path (disabled by default)
    cands = infer_map_mi_candidates(obj, psk_path, asset_path=asset_path, limit=4)
    if not cands:
        return 0
    mi_stem, mi_path, _score, _reason = cands[0]
    for idx, slot in enumerate(obj.material_slots or []):
        need, _why = material_slot_needs_repair(slot.material)
        if not need:
            continue
        return 1 if _assign_shared_mi_to_object(
            obj, mi_stem, mi_path, psk_path, slot_index=idx, slot_name=mi_stem,
        ) else 0
    if not obj.material_slots:
        return 1 if _assign_shared_mi_to_object(
            obj, mi_stem, mi_path, psk_path, slot_name=mi_stem,
        ) else 0
    return 0


def _assign_shared_mi_to_object(
    obj,
    mi_stem: str,
    mi_path: str,
    psk_path: str = "",
    *,
    slot_index: int = 0,
    slot_name: str = "",
) -> bool:
    if not obj or obj.type != "MESH" or not mi_path:
        return False
    _ensure_mesh_material_slot_count(obj, max(slot_index + 1, 1))
    mat = _get_or_build_shared_mi_material(
        mi_stem, mi_path, psk_path, (slot_name or mi_stem).lower(),
    )
    if mat is None:
        return False
    try:
        obj.material_slots[slot_index].material = mat
    except Exception:
        try:
            obj.data.materials[slot_index] = mat
        except Exception:
            return False
    try:
        obj["arc_materials_pending"] = 0
        obj["arc_mi_inferred"] = mi_stem
    except Exception:
        pass
    return True


def clear_leaked_preferred_mi(obj) -> bool:
    """Strip ``arc_preferred_mi`` unless it is a water/decal single-slot override.

    Opaque StaticMeshActors previously kept PropTrim/Vent preferred stamps that
    wiped every SM JSON slot with one atlas cell.
    """
    if obj is None:
        return False
    preferred = str(obj.get("arc_preferred_mi") or "").strip()
    if not preferred:
        return False
    if _preferred_mi_is_single_slot_override(preferred):
        return False
    try:
        if obj.get("arc_preferred_mi"):
            del obj["arc_preferred_mi"]
    except Exception:
        try:
            obj["arc_preferred_mi"] = ""
        except Exception:
            return False
    return True


def invalidate_shared_mi_on_object(obj) -> int:
    """Drop shared-cache entries + setup stamps so Stage 2 rebuilds graphs."""
    if not obj or obj.type != "MESH":
        return 0
    n = 0
    for slot in obj.material_slots or []:
        mat = slot.material
        if mat is None:
            continue
        key = str(mat.get("arc_mi_path") or "").strip()
        if key:
            _SHARED_MI_MATERIALS.pop(_norm_path_key(key), None)
        for stamp in (
            "arc_proptrim_uv", "arc_trim_setup", "arc_trim_world_uv",
            "arc_trim_alpha", "arc_water_setup", "arc_decal_mask_setup",
        ):
            try:
                if mat.get(stamp) is not None:
                    del mat[stamp]
            except Exception:
                pass
        n += 1
    return n


def repair_sma_trim_materials(obj, psk_path: str = "") -> dict:
    """Force-rebuild all slots from SM JSON; clear leaked preferred stamps.

    Used by the Repair SMA / Trim Materials operator and Force All Stage 2.
    """
    result = {
        "ok": False,
        "reason": "",
        "fixed": 0,
        "cleared_preferred": False,
    }
    if not obj or obj.type != "MESH":
        result["reason"] = "not_mesh"
        return result

    result["cleared_preferred"] = clear_leaked_preferred_mi(obj)
    invalidate_shared_mi_on_object(obj)

    if not psk_path:
        for key in ("arc_psk_path", "arc_mesh_file"):
            raw = obj.get(key) or ""
            if raw:
                psk_path = bpy.path.abspath(str(raw))
                break

    try:
        obj["arc_force_material_rebuild"] = 1
        obj["arc_materials_pending"] = 1
    except Exception:
        pass

    fixed = 0
    if psk_path:
        fixed = _setup_map_material_from_slots(obj, psk_path)
        if not fixed:
            fixed = setup_map_material(obj, psk_path)
    else:
        folder = ""
        for key in ("arc_asset_path",):
            raw = obj.get(key) or ""
            if raw:
                folder = os.path.dirname(bpy.path.abspath(str(raw)))
                break
        fixed = fix_object_materials_from_mi_slots(obj, folder)

    try:
        if obj.get("arc_force_material_rebuild"):
            del obj["arc_force_material_rebuild"]
        obj["arc_materials_pending"] = 0
    except Exception:
        pass

    result["fixed"] = int(fixed or 0)
    result["ok"] = bool(fixed) or result["cleared_preferred"]
    result["reason"] = f"rebuilt:{fixed}" if fixed else "no_slots_resolved"
    return result


def fix_white_unassigned_materials(obj, psk_path: str = "") -> dict:
    """Repair empty / default-white map materials via SM JSON (exact slots).

    Returns a result dict::
      {ok, reason, matched, mi_stem, mi_path, why_white}

    Soft-match folder MI inference is disabled (``ENABLE_FUZZY_MI_INFER``).
    """
    result = {
        "ok": False,
        "reason": "",
        "matched": "",
        "mi_stem": "",
        "mi_path": "",
        "why_white": "",
    }
    if not obj or obj.type != "MESH":
        result["reason"] = "not_mesh"
        return result

    need, why = object_needs_material_repair(obj)
    result["why_white"] = why
    if not need:
        result["ok"] = True
        result["reason"] = "already_ok"
        return result

    if not psk_path:
        for key in ("arc_psk_path", "arc_mesh_file"):
            raw = obj.get(key) or ""
            if raw:
                psk_path = bpy.path.abspath(str(raw))
                break
    asset_path = str(obj.get("arc_asset_path") or "")

    # Fast path: SM/SK slots + MI-named slots (no fuzzy recurse)
    if psk_path:
        fixed = _setup_map_material_from_slots(obj, psk_path)
        if fixed:
            need2, why2 = object_needs_material_repair(obj)
            if not need2:
                result["ok"] = True
                result["reason"] = f"setup_map:{fixed}"
                result["why_white"] = why2
                for slot in obj.material_slots:
                    mat = slot.material
                    if mat is not None and mat.get("arc_mi_path"):
                        result["mi_stem"] = str(mat.get("arc_mi_stem") or mat.name or "")
                        result["mi_path"] = str(mat.get("arc_mi_path") or "")
                        result["matched"] = "sm_json"
                        break
                return result
            # Partial SM assign — fill remaining white slots from SM JSON only.
            filled = _fuzzy_fill_white_slots_only(obj, psk_path, asset_path)
            need3, why3 = object_needs_material_repair(obj)
            result["ok"] = bool(fixed or filled)
            result["reason"] = f"setup_map_partial:{fixed}+fill:{filled}"
            result["why_white"] = why3
            result["matched"] = "sm_json_partial"
            return result

    # No SM success — SM-slot fill / water-decal preferred only (no folder fuzzy)
    filled = _fuzzy_fill_white_slots_only(obj, psk_path, asset_path)
    if filled:
        cands = infer_map_mi_candidates(obj, psk_path, asset_path=asset_path, limit=1)
        if cands:
            result["matched"] = cands[0][3]
            result["mi_stem"] = cands[0][0]
            result["mi_path"] = cands[0][1]
        result["ok"] = True
        result["reason"] = f"sm_fill:{filled}"
        return result

    result["reason"] = "no_sm_json" if not psk_path else "sm_unresolved"
    return result


def _setup_map_material_from_slots(obj, psk_path: str) -> int:
    """Assign from MI-named slots + SM/SK JSON + preferred BP MI (no fuzzy)."""
    if not obj or obj.type != "MESH":
        return 0

    folder = os.path.dirname(psk_path) if psk_path else ""
    # Prefer remapped Content folder when MapPlacements only has .uemodel.
    for alt in utils.remap_path_into_content_dirs(
        os.path.join(folder, "__probe__") if folder else ""
    ):
        alt_dir = os.path.dirname(alt)
        if alt_dir and os.path.isdir(alt_dir):
            folder = alt_dir
            break

    # UEModel often embeds the real MI_* names while SM StaticMaterials point at
    # shared PropTrim / wrong refs. Resolve MI-named Blender slots first (with
    # MapPlacements → Content remap), then fill leftovers from SM JSON.
    # Context=map: never Characters/Heroes via FMDex basename.
    fixed_named = fix_object_materials_from_mi_slots(obj, folder, context=CTX_MAP)
    slots = _parse_sk_material_slots(psk_path, context=CTX_MAP) if psk_path else []
    fixed = fixed_named
    force_rebuild = bool(obj.get("arc_force_material_rebuild"))

    # UEModel MI_* slot names are authoritative when present — do not
    # index-clobber them with unrelated SM StaticMaterials (belt/helmet from
    # corrupt dumps, PropTrim atlas, StreetSign refs on Aircon, etc.).
    has_mi_named = any(
        _mi_stem_from_blender_name(s.material.name)
        for s in (obj.material_slots or [])
        if s.material
    )

    preferred = str(obj.get("arc_preferred_mi") or "").strip()
    # Do not invent preferred_mi from actor name when SM/UEModel slots already
    # define materials — actor heuristics caused Vent/PropTrim leaks.
    if not preferred and not slots and not has_mi_named:
        preferred = preferred_mi_hint_from_actor_name(
            " ".join(
                p for p in (
                    str(obj.get("arc_actor_name") or ""),
                    getattr(obj, "name", "") or "",
                ) if p
            ),
            folder,
        )
        if preferred:
            try:
                obj["arc_preferred_mi"] = preferred
            except Exception:
                pass
    # Drop leaked preferred stems that are not water/decal single-slot overrides
    # (e.g. PropTrim/Vent atlas stamped onto every StaticMeshActor SRC).
    if preferred and not _preferred_mi_is_single_slot_override(preferred):
        try:
            if obj.get("arc_preferred_mi"):
                del obj["arc_preferred_mi"]
        except Exception:
            try:
                obj["arc_preferred_mi"] = ""
            except Exception:
                pass
        preferred = ""

    # Water / map-decal BP override: single MI on slot 0. Never use preferred to
    # wipe multi-slot StaticMeshActor props (that was assigning Vent/PropTrim atlases
    # onto every SMA when a leaked preferred stem resolved).
    if preferred and _preferred_mi_is_single_slot_override(preferred):
        real_slots = [(sn, st, mp) for sn, st, mp in slots if st and mp]
        pref_folder = ""
        for _sn, _st, mp in real_slots:
            pref_folder = os.path.dirname(mp)
            break
        pref_path = _resolve_mi_json_path(
            preferred, "", pref_folder or folder, context=CTX_MAP,
        )
        if not pref_path:
            for alt in decal_mi_stem_candidates_from_actor(
                str(obj.get("arc_actor_name") or obj.name or "")
            ):
                if alt.lower() == preferred.lower():
                    continue
                pref_path = _resolve_mi_json_path(
                    alt, "", pref_folder or folder, context=CTX_MAP,
                )
                if pref_path:
                    preferred = alt
                    break
        if pref_path:
            _ensure_mesh_material_slot_count(obj, max(1, len(real_slots) or 1))
            mat = _get_or_build_shared_mi_material(
                preferred, pref_path, psk_path, preferred.lower(),
            )
            if mat is not None:
                try:
                    obj.material_slots[0].material = mat
                    # DecalMesh / water cards are single-slot — done.
                    if len(real_slots) <= 1:
                        return 1
                    fixed = 1
                except Exception:
                    pass

    if slots:
        _ensure_mesh_material_slot_count(obj, len(slots))
        used_indices = set()
        # Keep BP water/decal preferred on slot 0 when it already won above.
        if fixed and preferred and _preferred_mi_is_single_slot_override(preferred):
            used_indices.add(0)
        allow_idx = not has_mi_named
        for idx, (slot_name, mi_stem, mi_path) in enumerate(slots):
            if not mi_stem or not mi_path:
                continue
            if not path_allowed_for_context(mi_path, CTX_MAP):
                continue
            if idx in used_indices:
                continue
            target_slot, slot_i = _match_material_slot(
                obj, slot_name, idx, used_indices, mi_stem,
                allow_index_fallback=allow_idx,
            )
            if target_slot is None:
                if has_mi_named:
                    continue
                if allow_idx and idx < len(obj.material_slots) and idx not in used_indices:
                    target_slot = obj.material_slots[idx]
                    slot_i = idx
                else:
                    continue
            used_indices.add(slot_i)
            cur = target_slot.material
            if (
                not force_rebuild
                and cur is not None
                and str(cur.get("arc_mi_path", "") or "") == _norm_path_key(mi_path)
            ):
                # Rebuild if this datablock was a stale enemy-decal misclass
                # or pre-v2 branding mask wiring (Color→Alpha).
                fam = str(cur.get("arc_mi_family") or "")
                if _needs_map_decal_mask_rebuild(cur, mi_path):
                    _SHARED_MI_MATERIALS.pop(_norm_path_key(mi_path), None)
                    try:
                        cur.name = f"{cur.name}_stale_decal_mask"
                    except Exception:
                        pass
                    # fall through to rebuild
                elif fam == FAMILY_DECAL:
                    try:
                        mi_probe = _parse_flat_mi_json(mi_path)
                        expected = classify_mi_family(
                            mi_probe, mi_stem.lower(), (slot_name or mi_stem).lower(),
                        )
                    except Exception:
                        expected = fam
                    if expected == FAMILY_DECAL:
                        fixed += 1
                        continue
                    # fall through to rebuild
                elif _needs_trim_setup_rebuild(cur, mi_path):
                    _SHARED_MI_MATERIALS.pop(_norm_path_key(mi_path), None)
                    try:
                        cur.name = f"{cur.name}_stale_trim"
                    except Exception:
                        pass
                    # fall through to rebuild
                elif _needs_proptrim_or_glass_rebuild(cur, mi_path, mi_stem):
                    _SHARED_MI_MATERIALS.pop(_norm_path_key(mi_path), None)
                    try:
                        cur.name = f"{cur.name}_stale_proptrim"
                    except Exception:
                        pass
                    # fall through to rebuild
                elif _needs_water_setup_rebuild(cur, mi_path):
                    _SHARED_MI_MATERIALS.pop(_norm_path_key(mi_path), None)
                    try:
                        cur.name = f"{cur.name}_stale_water"
                    except Exception:
                        pass
                    # fall through to rebuild
                else:
                    fixed += 1
                    continue
            elif force_rebuild and mi_path:
                _SHARED_MI_MATERIALS.pop(_norm_path_key(mi_path), None)
                if cur is not None:
                    try:
                        base_n = re.sub(r"(_force_rebuild)+$", "", cur.name or "")
                        cur.name = f"{base_n}_force_rebuild"
                    except Exception:
                        pass
            mat = _get_or_build_shared_mi_material(
                mi_stem, mi_path, psk_path, (slot_name or mi_stem).lower(),
            )
            if mat is None:
                continue
            target_slot.material = mat
            fixed += 1
        if fixed:
            return fixed

    return fixed if fixed else fix_object_materials_from_mi_slots(
        obj, folder, context=CTX_MAP,
    )


def setup_map_material(obj, psk_path: str) -> int:
    """Fast map-prop materials: SK/SM slots + shared MI cache (no outfit path).

    Returns the number of slots assigned. Prefer mesh JSON slot lists (index-
    aligned); fall back to MI-named Blender slots. Creates missing material
    slots when UEModel imports left the mesh with none (water planes etc.).
    Soft-match folder MI inference is disabled.
    """
    if not obj or obj.type != "MESH":
        return 0

    # Ensure Use UV1 posters bind UV1 (PSK EXTRAUV0 → UV1) before Stage 2 setup.
    try:
        utils.normalize_object_ue_uv_layers(obj)
    except Exception:
        pass

    fixed = _setup_map_material_from_slots(obj, psk_path)
    if fixed:
        # Rebuild any remaining white slots from SM JSON only
        need, _why = object_needs_material_repair(obj)
        if need:
            fixed += _fuzzy_fill_white_slots_only(
                obj, psk_path, str(obj.get("arc_asset_path") or ""),
            )
        _ensure_plane_world_tiling(obj)
        return fixed

    # Last resort: SM-slot fill / water-decal preferred (no folder fuzzy)
    need, _why = object_needs_material_repair(obj)
    if not need:
        _ensure_plane_world_tiling(obj)
        return 0
    asset_path = str(obj.get("arc_asset_path") or "")
    fixed = _fuzzy_fill_white_slots_only(obj, psk_path, asset_path)
    _ensure_plane_world_tiling(obj)
    return fixed


def _mesh_type_stem_from_obj(obj, psk_path: str = "") -> str:
    """Unique mesh type key: SM_/mesh basename without SRC_/hash/LOD noise."""
    for raw in (
        psk_path,
        str(obj.get("arc_psk_path") or "") if obj else "",
        str(obj.get("arc_mesh_file") or "") if obj else "",
        str(obj.get("arc_asset_path") or "") if obj else "",
        getattr(obj, "name", "") if obj else "",
    ):
        if not raw:
            continue
        leaf = os.path.basename(str(raw).replace("\\", "/"))
        stem = os.path.splitext(leaf)[0]
        stem = re.sub(r"^SRC_", "", stem, flags=re.IGNORECASE)
        stem = re.sub(r"-[0-9A-Fa-f]{4,10}$", "", stem)
        stem = re.sub(r"_LOD\d+$", "", stem, flags=re.IGNORECASE)
        if stem:
            return stem
    return "unknown"


def mesh_references_glass(obj, psk_path: str = "") -> bool:
    """True when SM JSON or stamped materials include a glass / BrokenGlass slot."""
    if not obj or obj.type != "MESH":
        return False
    for slot in obj.material_slots or []:
        mat = slot.material
        if mat is None:
            continue
        fam = str(mat.get("arc_mi_family") or "")
        if fam == FAMILY_GLASS:
            return True
        name_l = (mat.name or "").lower()
        stem_l = str(mat.get("arc_mi_stem") or "").lower()
        if "brokenglass" in name_l or "brokenglass" in stem_l:
            return True
        if fam == FAMILY_GLASS or (
            "glass" in name_l
            and "visor" not in name_l
            and any(k in name_l for k in ("broken", "window", "pane", "sdf"))
        ):
            return True
    path = psk_path or ""
    if not path:
        for key in ("arc_psk_path", "arc_mesh_file"):
            raw = obj.get(key) or ""
            if raw:
                path = bpy.path.abspath(str(raw))
                break
    for slot_name, mi_stem, mi_path in _parse_sk_material_slots(path) if path else []:
        sl = (slot_name or "").lower()
        ms = (mi_stem or "").lower()
        if "brokenglass" in sl or "brokenglass" in ms:
            return True
        if "glass" in sl or (ms.startswith("m_") and "glass" in ms and "visor" not in ms):
            return True
        if not mi_path:
            continue
        try:
            mi = _parse_flat_mi_json(mi_path)
            if classify_mi_family(mi, ms, sl) == FAMILY_GLASS:
                return True
        except Exception:
            pass
    return False


def _analyze_broken_mesh_type(
    stem: str,
    representative,
    psk_path: str,
    why: str,
) -> dict:
    """JSON / library / similar-mesh notes for one unique broken mesh type."""
    slots = _parse_sk_material_slots(psk_path) if psk_path else []
    slot_rows = []
    glass_slots = []
    trim_slots = []
    for slot_name, mi_stem, mi_path in slots:
        row = {
            "slot": slot_name or "",
            "mi": mi_stem or "",
            "mi_json": mi_path or "",
            "mi_exists": bool(mi_path and os.path.isfile(mi_path)),
            "family": "",
            "tex_params": [],
            "missing_cr": False,
            "notes": "",
        }
        if mi_path and os.path.isfile(mi_path):
            try:
                mi = _parse_flat_mi_json(mi_path)
                fam = classify_mi_family(mi, (mi_stem or "").lower(), (slot_name or "").lower())
                row["family"] = fam
                row["tex_params"] = sorted(_mi_tex_params(mi))
                if "proptrim" in (mi_stem or "").lower():
                    row["missing_cr"] = "CR Texture" not in row["tex_params"] and "CR" not in row["tex_params"]
                    sc = (mi.get("scalars") or {}) if mi_path else {}
                    uv_off = _mi_scalar(
                        sc, "UVOffset", "UV Offset", "OffsetUVs", "UV Offset Amount",
                        default=0.0,
                    )
                    if abs(float(uv_off)) < 1e-5:
                        row["notes"] = (
                            (row["notes"] + "; " if row["notes"] else "")
                            + "PropTrim has no UVOffset — mesh UVs select atlas cell; "
                              "vent-looking = wrong cell or wrong MI"
                        )
                    if not row["missing_cr"]:
                        row["notes"] = (
                            (row["notes"] + "; " if row["notes"] else "")
                            + "PropTrim CR/NXX resolved (library/sibling inherit ok)"
                        )
                    else:
                        lib = _proptrim_library_parent_stem(mi_stem or "")
                        row["notes"] = (
                            (row["notes"] + "; " if row["notes"] else "")
                            + f"PropTrim child lacks CR — expect inherit from {lib or 'library parent'}"
                        )
                if fam == FAMILY_ENVIRONMENT and _is_architecture_trim_stem(mi_stem or ""):
                    row["notes"] = (
                        (row["notes"] + "; " if row["notes"] else "")
                        + "architecture trim (not map-decal)"
                    )
                if fam == FAMILY_GLASS:
                    glass_slots.append(mi_stem or slot_name)
                    row["notes"] = (row["notes"] + "; " if row["notes"] else "") + "glass family"
                if "proptrim" in (mi_stem or "").lower() or "proptrim" in (slot_name or "").lower():
                    trim_slots.append(mi_stem or slot_name)
                if fam == FAMILY_DECAL and _is_architecture_trim_stem(mi_stem or ""):
                    row["notes"] = (
                        (row["notes"] + "; " if row["notes"] else "")
                        + "BUG: architecture trim still classified as map-decal"
                    )
            except Exception as exc:
                row["notes"] = f"parse_error:{exc}"
        elif mi_stem:
            row["notes"] = "MI JSON path unresolved"
        else:
            row["notes"] = "empty/engine slot"
        slot_rows.append(row)

    # Exact library MI match for first missing stem
    library_hits = []
    for row in slot_rows:
        mi = row["mi"]
        if not mi or row["mi_exists"]:
            continue
        found = _resolve_mi_json_path(mi, "", os.path.dirname(psk_path or ""))
        if found:
            library_hits.append(found)
            row["mi_json"] = found
            row["mi_exists"] = True
            row["notes"] = (row["notes"] + "; " if row["notes"] else "") + "found via MaterialLibrary resolve"

    # Similar mesh template: sibling stem that Stage 2 would set up from SM JSON
    similar = []
    folder = os.path.dirname(psk_path or "")
    if folder and os.path.isdir(folder):
        base = re.sub(r"(?i)_x\d+y\d+.*$", "", stem)
        base = re.sub(r"(?i)_win.*$", "", base)
        try:
            for fname in os.listdir(folder):
                if not fname.lower().endswith((".json",)):
                    continue
                if not fname.upper().startswith(("SM_", "SK_")):
                    continue
                other = os.path.splitext(fname)[0]
                if other == stem:
                    continue
                if base and base.lower() in other.lower():
                    similar.append(other)
                if len(similar) >= 5:
                    break
        except OSError:
            pass

    sig = "|".join(f"{r['slot']}:{r['mi']}" for r in slot_rows) or "(no_sm_slots)"
    return {
        "stem": stem,
        "why": why,
        "representative": getattr(representative, "name", "") if representative else "",
        "psk_path": psk_path or "",
        "slot_signature": sig,
        "slots": slot_rows,
        "glass_slots": glass_slots,
        "trim_slots": trim_slots,
        "library_hits": library_hits,
        "similar_meshes": similar,
        "recommendation": _audit_recommendation(slot_rows, glass_slots, trim_slots, why),
    }


def _audit_recommendation(slot_rows, glass_slots, trim_slots, why: str) -> str:
    if glass_slots and trim_slots:
        return (
            "Multi-slot glass+PropTrim: keep BrokenGlass on glass family and "
            "PropTrim on metal family with inherited CR/NXX; do not share one material."
        )
    if trim_slots and any(r.get("missing_cr") for r in slot_rows):
        return (
            "PropTrim compact MI missing CR/NXX — deterministic library/sibling inherit "
            "(MI_PropTrim_* / MI_Wrh_Beams_*_PropTrim_*)."
        )
    if any(not r.get("mi_exists") and r.get("mi") for r in slot_rows):
        return "Resolve missing MI JSON via ObjectPath / MaterialLibrary (exact stem)."
    if "worldgrid" in (why or "") or "empty" in (why or ""):
        return "SM StaticMaterials empty or WorldGrid — no fuzzy invent; fix export or preferred water/decal only."
    if "stamped_white" in (why or "") or "default_white" in (why or ""):
        return "Re-run Stage 2 after deterministic fixes; check texture PNG paths beside MI."
    return "Inspect SM JSON slots and MI texture ObjectPaths; no soft-match invent."


def audit_map_materials(
    context=None,
    map_name: str = "",
    *,
    only_selected: bool = False,
    write_report: bool = True,
) -> dict:
    """Scan map meshes for broken materials; group by unique mesh type.

    Writes markdown + CSV under addon ``docs/`` (and MapPlacement workspace when set).
    """
    try:
        from . import map_placement as mp
    except Exception:
        mp = None

    ctx = context or bpy.context
    scene = getattr(ctx, "scene", None)
    map_name = (map_name or "").strip()
    if (not map_name or map_name == "NONE") and scene is not None:
        map_name = (getattr(scene, "arc_placement_map", "") or "").strip()
        if not map_name or map_name == "NONE":
            map_name = (getattr(scene, "arc_placement_map_name", "") or "").strip()

    if mp is not None:
        targets = mp.collect_map_mesh_targets(ctx, map_name, only_selected=only_selected)
    else:
        targets = [o for o in bpy.data.objects if o.type == "MESH"]

    groups: dict[str, dict] = {}
    ok_count = 0
    broken_count = 0
    for obj in targets:
        need, why = object_needs_material_repair(obj)
        psk = ""
        for key in ("arc_psk_path", "arc_mesh_file"):
            raw = obj.get(key) or ""
            if raw:
                psk = bpy.path.abspath(str(raw))
                break
        stem = _mesh_type_stem_from_obj(obj, psk)
        # Slot signature for grouping even when healthy (glass vs trim called out)
        slots = _parse_sk_material_slots(psk) if psk else []
        sig = "|".join(f"{sn}:{ms}" for sn, ms, _mp in slots) or "(no_sm)"
        key = f"{stem}::{sig}"
        if not need:
            ok_count += 1
            if key not in groups:
                groups[key] = {
                    "stem": stem,
                    "ok": True,
                    "why": "ok",
                    "count": 0,
                    "representative": obj.name,
                    "psk_path": psk,
                    "slot_signature": sig,
                    "analysis": None,
                }
            groups[key]["count"] += 1
            continue
        broken_count += 1
        if key not in groups or groups[key].get("ok"):
            analysis = _analyze_broken_mesh_type(stem, obj, psk, why)
            groups[key] = {
                "stem": stem,
                "ok": False,
                "why": why,
                "count": 0,
                "representative": obj.name,
                "psk_path": psk,
                "slot_signature": sig,
                "analysis": analysis,
            }
        groups[key]["count"] += 1

    broken_types = [g for g in groups.values() if not g.get("ok")]
    broken_types.sort(key=lambda g: (-int(g.get("count") or 0), g.get("stem") or ""))

    report = {
        "map": map_name or "",
        "scanned": len(targets),
        "ok_meshes": ok_count,
        "broken_meshes": broken_count,
        "unique_types": len(groups),
        "unique_broken_types": len(broken_types),
        "broken": broken_types,
        "fuzzy_enabled": bool(ENABLE_FUZZY_MI_INFER),
        "paths": [],
    }

    if write_report:
        report["paths"] = _write_material_audit_report(report, scene)

    return report


def _write_material_audit_report(report: dict, scene=None) -> list[str]:
    """Write markdown + CSV; return written paths."""
    written: list[str] = []
    addon_dir = os.path.dirname(os.path.abspath(__file__))
    docs_dir = os.path.join(addon_dir, "docs")
    try:
        os.makedirs(docs_dir, exist_ok=True)
    except OSError:
        pass
    map_tag = re.sub(r"[^\w\-]+", "_", report.get("map") or "Map") or "Map"
    md_name = f"MATERIAL_AUDIT_{map_tag}.md"
    csv_name = f"MATERIAL_AUDIT_{map_tag}.csv"
    destinations = [docs_dir]
    if scene is not None:
        ws = getattr(scene, "arc_placement_workspace", "") or ""
        if ws:
            ws_abs = bpy.path.abspath(str(ws))
            if os.path.isdir(ws_abs):
                destinations.append(ws_abs)

    lines = [
        f"# Map Material Audit — {report.get('map') or '(any)'}",
        "",
        f"- Scanned meshes: **{report.get('scanned', 0)}**",
        f"- OK: **{report.get('ok_meshes', 0)}** · Broken: **{report.get('broken_meshes', 0)}**",
        f"- Unique types: **{report.get('unique_types', 0)}** · Unique broken: **{report.get('unique_broken_types', 0)}**",
        f"- Fuzzy soft-match: **{'ON' if report.get('fuzzy_enabled') else 'OFF'}**",
        "",
        "## Unique broken types",
        "",
    ]
    csv_rows = ["stem,count,why,slot_signature,representative,recommendation"]

    for g in report.get("broken") or []:
        a = g.get("analysis") or {}
        lines.append(f"### `{g.get('stem')}` ×{g.get('count', 0)}")
        lines.append(f"- Why: `{g.get('why')}`")
        lines.append(f"- Representative: `{g.get('representative')}`")
        lines.append(f"- Slot signature: `{g.get('slot_signature')}`")
        if a.get("glass_slots") or a.get("trim_slots"):
            lines.append(
                f"- Glass slots: `{', '.join(a.get('glass_slots') or []) or '—'}` · "
                f"PropTrim slots: `{', '.join(a.get('trim_slots') or []) or '—'}`"
            )
        lines.append(f"- Recommendation: {a.get('recommendation') or g.get('recommendation') or '—'}")
        for row in a.get("slots") or []:
            lines.append(
                f"  - slot `{row.get('slot')}` → `{row.get('mi')}` "
                f"family=`{row.get('family')}` exists={row.get('mi_exists')} "
                f"params={row.get('tex_params')} notes={row.get('notes')}"
            )
        if a.get("similar_meshes"):
            lines.append(f"- Similar meshes: {', '.join(a['similar_meshes'][:8])}")
        lines.append("")
        rec = (a.get("recommendation") or "").replace(",", ";")
        csv_rows.append(
            f"{g.get('stem')},{g.get('count')},{g.get('why')},"
            f"\"{g.get('slot_signature')}\",{g.get('representative')},\"{rec}\""
        )

    # Always document Wrh_Ceiling glass vs trim when present in content
    lines.extend([
        "## Reference — Warehouse ceiling (glass vs PropTrim)",
        "",
        "Win variants of `SM_Wrh_Ceiling_*` use separate StaticMaterials:",
        "1. `BrokenGlass` / `M_BrokenGlassSDF` → **glass** family (transmission)",
        "2. `M_PropTrims` / `MI_Wrh_Ceiling_01_PropTrim_PaintedBeams_01_A` → **metal** "
        "(inherits `CR Texture` + `NXX/NMX` from `MI_PropTrim_Painted_01_A` / Beams sibling)",
        "",
        "Non-win ceilings add concrete roof + `PaintedWorn` PropTrim slots — still multi-slot, no fuzzy.",
        "",
    ])

    md_body = "\n".join(lines)
    csv_body = "\n".join(csv_rows) + "\n"
    for dest in destinations:
        md_path = os.path.join(dest, md_name)
        csv_path = os.path.join(dest, csv_name)
        try:
            with open(md_path, "w", encoding="utf-8") as fh:
                fh.write(md_body)
            written.append(md_path)
        except OSError as exc:
            print(f"Arc Raiders material audit: failed MD {md_path}: {exc}")
        try:
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write(csv_body)
            written.append(csv_path)
        except OSError as exc:
            print(f"Arc Raiders material audit: failed CSV {csv_path}: {exc}")
    return written


def _object_looks_like_flat_plane(obj) -> bool:
    if not obj:
        return False
    if obj.get("arc_plane_mesh"):
        return True
    asset = str(obj.get("arc_asset_path") or "")
    try:
        from . import map_placement as mp

        return bool(mp.is_plane_mesh_asset(asset, obj.name))
    except Exception:
        blob = f"{asset} {obj.name}".lower()
        return "plane" in blob or "tarmacpatch" in blob.replace("_", "")


def _ensure_plane_world_tiling(obj, meters_per_tile: float | None = None) -> int:
    """For flat plane / tarmac-patch objects, force world-space density on materials.

    Shared road MIs already bake world tiling. Unique planes that landed on env/simple
    materials still use UV — rewire those Image Texture vectors to world Position so
    huge actor scales don't create wallpaper-sized grain.
    """
    if not _object_looks_like_flat_plane(obj):
        return 0
    mpt = float(meters_per_tile or _PLANE_ROAD_METERS_PER_TILE)
    touched = 0
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or not mat.use_nodes or mat.node_tree is None:
            continue
        fam = str(mat.get("arc_mi_family") or "")
        # Tarps / foliage keep UV; roads already world-tiled at build
        if fam in (FAMILY_TARP, FAMILY_FOLIAGE, FAMILY_WEAPON, FAMILY_DECAL):
            continue
        if mat.get("arc_world_tile_m") and fam == FAMILY_ROAD:
            continue
        # Per-object material copy so we don't mutate shared wall concrete MIs
        # used by non-plane props.
        if mat.users > 1 and not mat.get("arc_plane_world_tiling"):
            mat = mat.copy()
            mat.name = (mat.name + "_PlaneWorld")[:63]
            slot.material = mat
        if _rewire_material_world_tiling(mat, mpt):
            try:
                mat["arc_world_tile_m"] = mpt
                mat["arc_plane_world_tiling"] = 1
            except Exception:
                pass
            touched += 1
    return touched


def _rewire_material_world_tiling(mat, meters_per_tile: float) -> bool:
    """Point every Image Texture Vector at world-density Mapping. Returns True if changed."""
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return False
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    tex_nodes = [n for n in nodes if n.type == "TEX_IMAGE"]
    if not tex_nodes:
        return False
    # Shared world mapping
    geo = None
    mapping = None
    for n in nodes:
        if n.type == "NEW_GEOMETRY" and n.label == "Plane World Pos":
            geo = n
        if n.type == "MAPPING" and str(n.label).startswith("World "):
            mapping = n
    if geo is None:
        geo = nodes.new("ShaderNodeNewGeometry")
        geo.label = "Plane World Pos"
        geo.location = (-2200, 0)
    if mapping is None:
        mapping = nodes.new("ShaderNodeMapping")
        mapping.location = (-1950, 0)
    mpt = max(float(meters_per_tile), 0.25)
    mapping.label = f"World {mpt:g}m/tile"
    mapping.inputs["Scale"].default_value = (1.0 / mpt, 1.0 / mpt, 1.0 / mpt)
    # Ensure Position → Mapping
    if not mapping.inputs["Vector"].is_linked:
        links.new(geo.outputs["Position"], mapping.inputs["Vector"])
    vec_out = mapping.outputs["Vector"]
    changed = False
    for tex in tex_nodes:
        # Disconnect old UV mapping
        for link in list(tex.inputs["Vector"].links):
            links.remove(link)
        links.new(vec_out, tex.inputs["Vector"])
        changed = True
    return changed


# ---------------------------------------------------------------------------
# Misc item material setup
# ---------------------------------------------------------------------------

def _find_misc_textures(folder: str) -> dict:
    result = {'cr': None, 'normal_type': None, 'normal': None}
    try:
        for fname in os.listdir(folder):
            fl = fname.lower()
            fpath = os.path.join(folder, fname)
            if fl.endswith('_cr.png'):
                result['cr'] = fpath
            for ntype in ('nom', 'nem', 'nxm', 'nam'):
                if fl.endswith(f'_{ntype}.png'):
                    result['normal_type'] = ntype
                    result['normal'] = fpath
    except OSError:
        pass
    return result


def setup_misc_material(obj, psk_path: str):
    folder = os.path.dirname(psk_path)
    texs = _find_misc_textures(folder)

    if not texs['cr'] and not texs['normal']:
        print(f"Arc Raiders PSK Importer: No CR or N*M textures found for '{os.path.basename(psk_path)}'")
        return

    mat = bpy.data.materials.new(name=obj.name + "_Misc_Mat")
    mat.use_nodes = True
    obj.active_material = mat
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (400, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (700, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    cr_node = None
    normal_node = None

    if texs['cr']:
        img = bpy.data.images.load(texs['cr'], check_existing=True)
        cr_node = nodes.new("ShaderNodeTexImage")
        cr_node.image = img
        cr_node.label = "CR (Colour/Roughness)"
        cr_node.interpolation = "Cubic"
        cr_node.location = (-900, 300)
        links.new(cr_node.outputs["Alpha"], principled.inputs["Roughness"])

    if texs['normal']:
        img = bpy.data.images.load(texs['normal'], check_existing=True)
        img.colorspace_settings.name = "Non-Color"
        normal_node = nodes.new("ShaderNodeTexImage")
        normal_node.image = img
        normal_node.label = texs['normal_type'].upper()
        normal_node.interpolation = "Cubic"
        normal_node.location = (-900, -100)

        if utils.ensure_node_group("NormalFlipper"):
            flipper = nodes.new("ShaderNodeGroup")
            flipper.node_tree = bpy.data.node_groups["NormalFlipper"]
            flipper.location = (-400, -100)
            links.new(normal_node.outputs["Color"], flipper.inputs[0])
            nm_in = flipper.outputs[0]
        else:
            nm_in = normal_node.outputs["Color"]

        nm_node = nodes.new("ShaderNodeNormalMap")
        nm_node.location = (-100, -100)
        try:
            nm_node.convention = 'DIRECTX'
        except Exception:
            pass
        links.new(nm_in, nm_node.inputs["Color"])
        links.new(nm_node.outputs["Normal"], principled.inputs["Normal"])
        ntype = texs['normal_type']
        if ntype != 'nom':
            links.new(normal_node.outputs["Alpha"], principled.inputs["Metallic"])

        if ntype in ('nom', 'nem', 'nam'):
            sep_node = nodes.new("ShaderNodeSeparateColor")
            sep_node.location = (-400, -450)
            links.new(normal_node.outputs["Color"], sep_node.inputs["Color"])

            if ntype == 'nom':
                mul_node = nodes.new("ShaderNodeMix")
                mul_node.data_type = 'RGBA'
                mul_node.blend_type = 'MULTIPLY'
                mul_node.location = (100, 200)
                mul_node.inputs["Factor"].default_value = 1.0
                if cr_node:
                    links.new(cr_node.outputs["Color"], mul_node.inputs[6])
                links.new(sep_node.outputs["Blue"], mul_node.inputs[7])
                links.new(mul_node.outputs[2], principled.inputs["Base Color"])

                metal_mix = nodes.new("ShaderNodeMix")
                metal_mix.data_type = 'FLOAT'
                metal_mix.blend_type = 'MIX'
                metal_mix.label = "NOM Alpha → Metallic (Blue)"
                metal_mix.location = (100, -80)
                links.new(normal_node.outputs["Alpha"], metal_mix.inputs["Factor"])
                links.new(sep_node.outputs["Blue"], metal_mix.inputs[3])
                links.new(metal_mix.outputs[0], principled.inputs["Metallic"])

            elif ntype == 'nem':
                links.new(sep_node.outputs["Blue"], principled.inputs["Emission Strength"])
                if cr_node:
                    links.new(cr_node.outputs["Color"], principled.inputs["Emission Color"])
                    links.new(cr_node.outputs["Color"], principled.inputs["Base Color"])

            elif ntype == 'nam':
                links.new(sep_node.outputs["Blue"], principled.inputs["Alpha"])
                if cr_node:
                    links.new(cr_node.outputs["Color"], principled.inputs["Base Color"])

        elif ntype == 'nxm':
            if cr_node:
                links.new(cr_node.outputs["Color"], principled.inputs["Base Color"])

    elif cr_node:
        links.new(cr_node.outputs["Color"], principled.inputs["Base Color"])


# ---------------------------------------------------------------------------
# Visor / Glass material setup
# ---------------------------------------------------------------------------

# Medians across the shipped visor glass instances, so a partially authored MI still lands on a
# plausible lens instead of a debug default. Prefer authored ColorA/B(/C) from the glass MI, then
# fall back to the companion clothing skin's ColorABC — never invent outfit-name hacks.
_VISOR_SCALAR_DEFAULTS = {
    'FresnelLow': 0.464,
    'FresnelHigh': 1.238,
    'Metallic': 0.35,
    'Opacity': 1.0,
    'RoughnessModifier': 0.176,
    'RoughnessTiling': 0.823616,
    'VerticalStart': 1.0,
    'VerticalEnd': 0.0,
    'CircularPositionU': 0.5,
    'CircularPositionV': 0.5,
    'CircularScaleU': 1.0,
    'CircularScaleV': 1.0,
    'CircularSize': 1.0,
    'DirtAmount': 0.752,
    'DirtRoughness': 0.645,
    'DirtSoftness': 0.202,
    'DirtTiling': 2.55,
    'MaskDirt': 1.0,
}

# Neutral grey — not the old Goalie-blue median. Missing authored colours should not look like a
# tinted lens; callers fall back to clothing ColorABC when available.
_VISOR_COLOR_DEFAULTS = {
    'ColorA': (0.35, 0.35, 0.38, 1.0),
    'ColorB': (0.55, 0.55, 0.58, 1.0),
    'ColorC': (0.75, 0.75, 0.78, 1.0),
    'DirtColor': (0.057292, 0.033339, 0.026557, 1.0),
}


def _parse_visor_mi(mi_path: str) -> dict:
    """Parent, scalars, colours and texture ObjectPaths of a glass MI JSON.

    Accepts both the UE export shape (Properties/ScalarParameterValues) that FModel's outfit
    exporter writes and the flat Parameters/Scalars shape of a plain material dump.
    """
    result = {
        'parent': '',
        'is_transparent': False,
        'scalars': {},
        'colors': {},
        'textures': {},
        'found': False,
    }
    if not mi_path or not os.path.isfile(mi_path):
        return result

    try:
        with open(mi_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Could not parse visor MI '{mi_path}': {e}")
        return result

    try:
        entry = utils.first_ue_export(data, "MaterialInstanceConstant") or {}
        props = entry.get("Properties", {}) or {}
        parent = props.get("Parent") or {}
        result['parent'] = str(parent.get("ObjectName", "") or parent.get("ObjectPath", "") or "")

        for sp in props.get("ScalarParameterValues", []) or []:
            name = sp.get("ParameterInfo", {}).get("Name", "")
            if name:
                result['scalars'][name] = float(sp.get("ParameterValue", 0.0))
        for vp in props.get("VectorParameterValues", []) or []:
            name = vp.get("ParameterInfo", {}).get("Name", "")
            pv = vp.get("ParameterValue", {}) or {}
            if name:
                result['colors'][name] = (
                    float(pv.get("R", 1.0)), float(pv.get("G", 1.0)),
                    float(pv.get("B", 1.0)), float(pv.get("A", 1.0)),
                )
        for tp in props.get("TextureParameterValues", []) or []:
            name = tp.get("ParameterInfo", {}).get("Name", "")
            pv = tp.get("ParameterValue", {}) or {}
            obj_path = str(pv.get("ObjectPath", "") or "")
            if name and obj_path:
                result['textures'][name] = obj_path

        bpo = props.get("BasePropertyOverrides", {}) or {}
        blend_mode = str(bpo.get("BlendMode", "") or "")

        # Flat dump fallback: Parameters/{Scalars,Colors} with a Textures map beside it.
        translucent_flag = False
        flat = data[0] if isinstance(data, list) and data else data
        if isinstance(flat, dict):
            params = flat.get("Parameters") or {}
            for name, value in (params.get("Scalars") or {}).items():
                result['scalars'].setdefault(name, float(value))
            for name, value in (params.get("Colors") or {}).items():
                result['colors'].setdefault(name, (
                    float(value.get("R", 1.0)), float(value.get("G", 1.0)),
                    float(value.get("B", 1.0)), float(value.get("A", 1.0)),
                ))
            for name, path in (flat.get("Textures") or {}).items():
                result['textures'].setdefault(name, path)
            translucent_flag = bool(params.get("IsTranslucent"))
            if not blend_mode:
                flat_bpo = (params.get("Properties") or {}).get("BasePropertyOverrides") or {}
                blend_mode = str(flat_bpo.get("BlendMode", "") or "")

        # The gameplay parent authors Opacity but is Opaque + Default Lit, so the parent name wins
        # over any blend hint; only the front-end parent is really thin translucent.
        parent_l = result['parent'].lower()
        result['is_transparent'] = "glass_transparent" in parent_l or (
            "glass_opaque" not in parent_l
            and (translucent_flag or "translucent" in blend_mode.lower())
        )
        result['found'] = bool(result['scalars'] or result['colors'] or result['textures'])
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Could not parse visor MI '{mi_path}': {e}")
    return result


def _is_visor_glass_mi(mi: dict) -> bool:
    """True when an MI is one of the two Arc glass parents, by parent or parameter set."""
    parent_l = (mi.get('parent') or "").lower()
    if "glass_opaque" in parent_l or "glass_transparent" in parent_l:
        return True
    scalars = mi.get('scalars') or {}
    colors = mi.get('colors') or {}
    return ("FresnelHigh" in scalars and "RoughnessModifier" in scalars
            and "ColorA" in colors and "ColorB" in colors)


def _visor_scalar(mi: dict, name: str) -> float:
    value = (mi.get('scalars') or {}).get(name)
    return float(value) if value is not None else _VISOR_SCALAR_DEFAULTS[name]


def _visor_color(mi: dict, name: str):
    value = (mi.get('colors') or {}).get(name)
    return tuple(value) if value else _VISOR_COLOR_DEFAULTS.get(name, (0.5, 0.5, 0.5, 1.0))


def _clothing_color_fallback(skin_json: str) -> dict:
    """ColorA/B/C(/A2…) from a clothing skin JSON for glass slots that lack tint."""
    if not skin_json or not os.path.isfile(skin_json):
        return {}
    try:
        mi_data = textures.parse_clothing_mi(skin_json) or {}
        colours = mi_data.get("colours") or {}
        out = {}
        for key in ("ColorA", "ColorB", "ColorC", "ColorA2", "ColorB2", "ColorC2"):
            if key in colours:
                c = colours[key]
                out[key] = (
                    float(c[0]), float(c[1]), float(c[2]),
                    float(c[3]) if len(c) > 3 else 1.0,
                )
        return out
    except Exception:
        return {}


def _resolve_visor_lens_colors(mi: dict, skin_json: str = "") -> tuple:
    """Authored glass ColorA/B(/C), else clothing ColorABC, else neutral defaults.

    Glass parents author ColorA/ColorB. When missing, pull ColorA/B/C from the companion
    clothing skin so Visor_Glass tracks the colorway instead of a hardcoded blue.
    """
    colors = dict(mi.get("colors") or {})
    authored_a = "ColorA" in colors
    authored_b = "ColorB" in colors
    fallback = {}
    if not (authored_a and authored_b):
        search = []
        if skin_json and os.path.isfile(skin_json):
            if not _is_visor_glass_mi(_parse_visor_mi(skin_json)):
                search.append(skin_json)
            skin_dir = os.path.dirname(skin_json)
            try:
                for fname in sorted(os.listdir(skin_dir)):
                    if not fname.lower().endswith(".json") or "glass" in fname.lower():
                        continue
                    candidate = os.path.join(skin_dir, fname)
                    if candidate not in search:
                        search.append(candidate)
            except OSError:
                pass
        for path in search:
            fallback = _clothing_color_fallback(path)
            if fallback.get("ColorA") and fallback.get("ColorB"):
                break

    def pick(name):
        if name in colors:
            c = colors[name]
            return (
                float(c[0]), float(c[1]), float(c[2]),
                float(c[3]) if len(c) > 3 else 1.0,
            )
        if name in fallback:
            return fallback[name]
        return _VISOR_COLOR_DEFAULTS.get(name, (0.5, 0.5, 0.5, 1.0))

    color_a = pick("ColorA")
    color_b = pick("ColorB")
    color_c = pick("ColorC") if ("ColorC" in colors or "ColorC" in fallback) else None
    dirt = pick("DirtColor")
    source = (
        "glass_mi" if authored_a and authored_b
        else ("clothing_abc" if fallback.get("ColorA") else "neutral_default")
    )
    return color_a, color_b, color_c, dirt, source


def _visor_content_roots(start_folder: str) -> list:
    """Every 'Content' directory above start_folder, nearest first.

    FModel outfit exports mirror PioneerGame/Content inside each part folder, so shared
    MaterialLibrary textures resolve without the global Pioneer root being set.
    """
    roots = []
    current = os.path.abspath(start_folder) if start_folder else ""
    while current and os.path.basename(current):
        if os.path.basename(current).lower() == "content":
            roots.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return roots


def _resolve_visor_texture(obj_path: str, local_folders: list, content_roots: list) -> str:
    """Resolve a glass texture ObjectPath, preferring the export's own content mirror."""
    if not obj_path:
        return ""

    clean = obj_path
    leaf = clean.split("/")[-1]
    if "." in leaf:
        clean = clean[: -len(leaf)] + leaf.split(".")[0]
    rel = clean[len('/Game/'):] if clean.startswith('/Game/') else clean.lstrip('/')

    for root in content_roots:
        for ext in (".png", ".tga", ".jpg", ".jpeg"):
            candidate = os.path.join(root, rel.replace('/', os.sep)) + ext
            if os.path.isfile(candidate):
                return candidate

    return _resolve_mi_texture_path(obj_path, local_folders)


def _find_glass_mi_in_dir(folder: str) -> str:
    """Glass MI JSON beside an explicit skin JSON, e.g. MI_Goalie_Visor_Glass_Gold.json.

    Prefers opaque gameplay glass over FrontEnd variants when both sit in the same folder.
    """
    if not folder or not os.path.isdir(folder):
        return ""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return ""
    opaque = ""
    frontend = ""
    for fname in names:
        if not fname.lower().endswith(".json") or "glass" not in fname.lower():
            continue
        candidate = os.path.join(folder, fname)
        if not _is_visor_glass_mi(_parse_visor_mi(candidate)):
            continue
        if "frontend" in fname.lower():
            if not frontend:
                frontend = candidate
        elif not opaque:
            opaque = candidate
    return opaque or frontend


def _visor_unique_mat_name(obj, index: int, mi_path: str = "", skin_json: str = "") -> str:
    """Stable Blender material name keyed by outfit instance + glass/skin MI stem.

    PSK imports name the slot ``Visor_Glass``; without a unique datablock, every colorway
    mutates the same material and Gold/Green/etc. overwrite each other.
    """
    stem = ""
    for path in (mi_path, skin_json):
        if not path:
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem:
            break
    colorway = ""
    try:
        colorway = str(obj.get("arc_colorway", "") or "").strip()
    except Exception:
        colorway = ""
    bits = [getattr(obj, "name", "") or "Visor"]
    if colorway:
        bits.append(colorway)
    bits.append(stem or f"VisorGlass{index}")
    # Blender datablock names are capped at 63 chars.
    return "_".join(bits)[:63]


def _material_used_by_other_objects(mat, obj) -> bool:
    """True when *mat* is assigned on any mesh other than *obj*."""
    if mat is None:
        return False
    for other in bpy.data.objects:
        if other == obj or getattr(other, "type", None) != "MESH":
            continue
        for slot in other.material_slots:
            if slot.material == mat:
                return True
    return False


def _ensure_unique_visor_slot_material(obj, target, index: int,
                                       mi_path: str = "", skin_json: str = ""):
    """Ensure the glass slot owns a datablock unique to this outfit/colorway.

    Keyed by ``obj.name`` + ``arc_colorway`` + glass MI stem. Copies when the PSK
    ``Visor_Glass`` (or any glass) datablock is shared across objects or slots.
    """
    name = _visor_unique_mat_name(obj, index, mi_path=mi_path, skin_json=skin_json)
    current = target.material
    shared = (
        current is not None
        and (
            current.users > 1
            or _material_used_by_other_objects(current, obj)
            or sum(1 for s in obj.material_slots if s.material == current) > 1
        )
    )

    if current is None:
        mat = bpy.data.materials.new(name=name)
        target.material = mat
        return mat

    if not shared:
        # Sole owner — rename away from the generic PSK ``Visor_Glass`` name so the
        # next import cannot reattach to this datablock by slot name.
        if current.name != name and not current.name.startswith(name + "."):
            try:
                current.name = name
            except Exception:
                pass
        return current

    mat = current.copy()
    try:
        mat.name = name
    except Exception:
        pass
    target.material = mat
    return mat


def apply_embedded_visor_slots(obj, psk_path: str, skin_json: str = ""):
    """After ArcTexturer is set up on *obj*, replace any glass material slot with the glass
    shader. Works for clothing and visor PSKs that carry a Visor_Glass slot alongside the shell.

    *skin_json* may be FModel's glass MI path or the clothing skin; the matching glass MI is
    resolved so a colourway swap actually changes the lens tint (ColorA/B, else ColorABC).
    Each outfit/colorway gets its own material datablock — never share Visor_Glass across skins.
    """
    if not obj.material_slots:
        return 0

    try:
        sk_slots = _parse_sk_material_slots(psk_path)
    except Exception:
        sk_slots = []

    psk_folder = os.path.dirname(psk_path) if psk_path else ""
    skin_dir = os.path.dirname(skin_json) if skin_json and os.path.isfile(skin_json) else ""
    # Direct glass MI path from FModel GlassSkinJsonPath wins over directory search.
    if skin_json and os.path.isfile(skin_json) and _is_visor_glass_mi(_parse_visor_mi(skin_json)):
        colorway_glass = skin_json
    else:
        colorway_glass = _find_glass_mi_in_dir(skin_dir)

    if not sk_slots:
        # No sibling SK JSON: fall back to the Blender slot names the PSK importer created.
        sk_slots = [
            ((s.material.name if s.material else ""), "", "")
            for s in obj.material_slots
        ]

    used_indices = set()
    applied = 0
    for i, (sk_name, mi_stem, mi_json) in enumerate(sk_slots):
        target, index = _match_material_slot(obj, sk_name, i, used_indices, mi_stem)
        if target is None:
            continue
        mat_name = target.material.name if target.material else ""
        names = (sk_name, mi_stem, mat_name)

        mi_path = mi_json if mi_json and os.path.isfile(mi_json) else ""
        if not mi_path:
            for stem in names:
                mi_path = _resolve_mi_json_path(stem, "", psk_folder)
                if mi_path:
                    break

        # The shell slot is also called "Visor", so only a glass-named slot or a slot whose own MI
        # parses as glass may take the colourway's glass instance.
        if not any("glass" in name.lower() for name in names) and \
                not _is_visor_glass_mi(_parse_visor_mi(mi_path)):
            continue

        # The mesh names the base glass instance; a colourway skin JSON overrides it.
        if colorway_glass:
            mi_path = colorway_glass
        if not _is_visor_glass_mi(_parse_visor_mi(mi_path)):
            continue

        used_indices.add(index)
        _ensure_unique_visor_slot_material(
            obj, target, index, mi_path=mi_path, skin_json=skin_json
        )

        _setup_visor_material(target.material, mi_path=mi_path, psk_path=psk_path,
                              skin_json=skin_json)
        applied += 1
        print(
            f"Arc Raiders: Applied glass shader to visor slot "
            f"'{sk_name or mat_name}' (index {index}) on '{obj.name}' "
            f"→ '{target.material.name}'"
        )

    return applied


def _set_visor_render_method(mat, transparent: bool):
    """Blender 4.2+ renamed blend_method to surface_render_method; 5.x removed the old one."""
    try:
        mat.surface_render_method = 'BLENDED' if transparent else 'DITHERED'
    except Exception:
        pass
    try:
        mat.blend_method = 'BLEND' if transparent else 'OPAQUE'
    except Exception:
        pass
    try:
        mat.use_backface_culling = True
    except Exception:
        pass


def _visor_map_range(nodes, label, location, from_min, from_max,
                     to_min=0.0, to_max=1.0, interpolation='LINEAR'):
    node = nodes.new("ShaderNodeMapRange")
    node.label = label
    node.location = location
    node.clamp = True
    try:
        node.interpolation_type = interpolation
    except Exception:
        pass
    node.inputs["From Min"].default_value = from_min
    node.inputs["From Max"].default_value = from_max
    node.inputs["To Min"].default_value = to_min
    node.inputs["To Max"].default_value = to_max
    return node


def _visor_math(nodes, operation, location, label="", clamp=False):
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    node.location = location
    node.use_clamp = clamp
    if label:
        node.label = label
    return node


def _visor_image(nodes, links, path, label, location, vector_socket):
    """Non-colour image node wired to a tiled UV, plus its red channel."""
    img = bpy.data.images.load(path, check_existing=True)
    try:
        img.colorspace_settings.name = "Non-Color"
    except Exception:
        pass
    node = nodes.new("ShaderNodeTexImage")
    node.image = img
    node.label = label
    node.interpolation = "Cubic"
    node.location = location
    if vector_socket is not None:
        links.new(vector_socket, node.inputs["Vector"])
    return node


def _visor_red(nodes, links, image_node, location):
    separate = nodes.new("ShaderNodeSeparateColor")
    separate.location = location
    links.new(image_node.outputs["Color"], separate.inputs["Color"])
    return separate.outputs["Red"]


def _resolve_visor_textures(mi: dict, mi_path: str, psk_path: str, skin_json: str) -> dict:
    folders = [
        os.path.dirname(p) for p in (mi_path, skin_json, psk_path) if p
    ]
    folders = [f for f in folders if f]
    roots = []
    for folder in folders:
        for root in _visor_content_roots(folder):
            if root not in roots:
                roots.append(root)

    resolved = {}
    for param in ("Roughness", "DirtMask", "DirtNormal"):
        obj_path = (mi.get('textures') or {}).get(param, "")
        found = _resolve_visor_texture(obj_path, folders, roots)
        if found:
            resolved[param] = found
    return resolved


def _setup_visor_material(mat, mi_path: str = "", psk_path: str = "", skin_json: str = ""):
    """Arc Raiders visor glass with the Blender viewport material defaults.

    A vertical gradient crossed with an elliptical lens mask is screen-combined with a remapped
    fresnel term and picks between ColorA and ColorB (ColorC when authored on glass or clothing
    ColorABC fallback). A tiled smudge mask lifts roughness off RoughnessModifier and a tiled
    dirt mask layers DirtColor / DirtRoughness / DirtNormal on top.
    """
    mi = _parse_visor_mi(mi_path)
    textures_by_param = _resolve_visor_textures(mi, mi_path, psk_path, skin_json)
    color_a, color_b, color_c, dirt_color, color_source = _resolve_visor_lens_colors(mi, skin_json)
    try:
        mat["arc_visor_color_source"] = color_source
        mat["arc_visor_mi"] = os.path.basename(mi_path) if mi_path else ""
    except Exception:
        pass

    _set_visor_render_method(mat, True)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    shape_frame = nodes.new("NodeFrame")
    shape_frame.label = "Visor Shape + Fresnel"
    shape_frame.label_size = 16
    color_frame = nodes.new("NodeFrame")
    color_frame.label = f"Visor ColorABC [{color_source}]"
    color_frame.label_size = 16
    dirt_frame = nodes.new("NodeFrame")
    dirt_frame.label = "Visor Dirt / Roughness"
    dirt_frame.label_size = 16

    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (900, 0)
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (620, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    # Shared Blender visor defaults. Keep aliases for older Principled socket names.
    for socket, value in (
        ("IOR", 1.0),
        ("Transmission Weight", 1.0),
        ("Transmission", 1.0),
        ("Coat Weight", 1.0),
        ("Clearcoat", 1.0),
        ("Coat Roughness", 0.1),
        ("Clearcoat Roughness", 0.1),
        ("Coat IOR", 1.5),
        ("Sheen Weight", 0.5),
        ("Sheen", 0.5),
        ("Sheen Roughness", 0.5),
        ("Alpha", 0.65),
    ):
        if socket in principled.inputs:
            principled.inputs[socket].default_value = value
    if "Metallic" in principled.inputs:
        principled.inputs["Metallic"].default_value = _visor_scalar(mi, 'Metallic')

    tex_coord = nodes.new("ShaderNodeTexCoord")
    tex_coord.location = (-1900, 0)
    tex_coord.parent = shape_frame
    uv = tex_coord.outputs["UV"]

    separate_uv = nodes.new("ShaderNodeSeparateXYZ")
    separate_uv.location = (-1700, 320)
    separate_uv.parent = shape_frame
    links.new(uv, separate_uv.inputs[0])

    # --- Shape: vertical gradient crossed with the elliptical lens mask ---
    vertical = _visor_map_range(nodes, "Vertical Gradient", (-1500, 380),
                                _visor_scalar(mi, 'VerticalEnd'), _visor_scalar(mi, 'VerticalStart'))
    vertical.parent = shape_frame
    links.new(separate_uv.outputs["Y"], vertical.inputs["Value"])

    centred = nodes.new("ShaderNodeVectorMath")
    centred.operation = 'SUBTRACT'
    centred.location = (-1500, 120)
    centred.parent = shape_frame
    centred.inputs[1].default_value = (_visor_scalar(mi, 'CircularPositionU'),
                                       _visor_scalar(mi, 'CircularPositionV'), 0.0)
    links.new(uv, centred.inputs[0])

    scaled = nodes.new("ShaderNodeVectorMath")
    scaled.operation = 'DIVIDE'
    scaled.location = (-1300, 120)
    scaled.parent = shape_frame
    scaled.inputs[1].default_value = (max(abs(_visor_scalar(mi, 'CircularScaleU')), 1e-4),
                                      max(abs(_visor_scalar(mi, 'CircularScaleV')), 1e-4), 1.0)
    links.new(centred.outputs["Vector"], scaled.inputs[0])

    radius = nodes.new("ShaderNodeVectorMath")
    radius.operation = 'LENGTH'
    radius.location = (-1100, 120)
    radius.parent = shape_frame
    links.new(scaled.outputs["Vector"], radius.inputs[0])

    ellipse = _visor_map_range(nodes, "Elliptical Mask", (-900, 120),
                               max(_visor_scalar(mi, 'CircularSize'), 1e-4), 0.0)
    ellipse.parent = shape_frame
    links.new(radius.outputs["Value"], ellipse.inputs["Value"])

    shape = _visor_math(nodes, 'MULTIPLY', (-700, 300), "Shape", clamp=True)
    shape.parent = shape_frame
    links.new(vertical.outputs["Result"], shape.inputs[0])
    links.new(ellipse.outputs["Result"], shape.inputs[1])

    # --- View tint: FresnelLow / FresnelHigh remap the facing ratio ---
    geometry = nodes.new("ShaderNodeNewGeometry")
    geometry.location = (-1900, -260)
    geometry.parent = shape_frame

    facing_dot = nodes.new("ShaderNodeVectorMath")
    facing_dot.operation = 'DOT_PRODUCT'
    facing_dot.location = (-1700, -260)
    facing_dot.parent = shape_frame
    links.new(geometry.outputs["Incoming"], facing_dot.inputs[0])
    links.new(geometry.outputs["Normal"], facing_dot.inputs[1])

    facing = _visor_math(nodes, 'SUBTRACT', (-1500, -260), "1 - N.V", clamp=True)
    facing.parent = shape_frame
    facing.inputs[0].default_value = 1.0
    links.new(facing_dot.outputs["Value"], facing.inputs[1])

    view_tint = _visor_map_range(nodes, "Fresnel Remap", (-1300, -260),
                                 _visor_scalar(mi, 'FresnelLow'), _visor_scalar(mi, 'FresnelHigh'))
    view_tint.parent = shape_frame
    links.new(facing.outputs["Value"], view_tint.inputs["Value"])

    blend_add = _visor_math(nodes, 'ADD', (-500, 60))
    blend_add.parent = shape_frame
    links.new(shape.outputs["Value"], blend_add.inputs[0])
    links.new(view_tint.outputs["Result"], blend_add.inputs[1])

    blend_mul = _visor_math(nodes, 'MULTIPLY', (-500, -120))
    blend_mul.parent = shape_frame
    links.new(shape.outputs["Value"], blend_mul.inputs[0])
    links.new(view_tint.outputs["Result"], blend_mul.inputs[1])

    blend = _visor_math(nodes, 'SUBTRACT', (-300, -30), "Shape screen Tint", clamp=True)
    blend.parent = shape_frame
    links.new(blend_add.outputs["Value"], blend.inputs[0])
    links.new(blend_mul.outputs["Value"], blend.inputs[1])

    # ColorA → ColorB (→ ColorC when available from glass MI or clothing ColorABC fallback)
    glass_mix = nodes.new("ShaderNodeMix")
    glass_mix.data_type = 'RGBA'
    glass_mix.blend_type = 'MIX'
    glass_mix.label = "ColorA to ColorB"
    glass_mix.location = (-80, 200)
    glass_mix.parent = color_frame
    glass_mix.inputs[6].default_value = color_a
    glass_mix.inputs[7].default_value = color_b
    links.new(blend.outputs["Value"], glass_mix.inputs[0])
    glass_color_out = glass_mix.outputs[2]

    if color_c is not None:
        glass_mix_c = nodes.new("ShaderNodeMix")
        glass_mix_c.data_type = 'RGBA'
        glass_mix_c.blend_type = 'MIX'
        glass_mix_c.label = "ColorAB to ColorC"
        glass_mix_c.location = (120, 200)
        glass_mix_c.parent = color_frame
        glass_mix_c.inputs[7].default_value = color_c
        # Second half of the blend pulls toward ColorC (same role as ColorMask.B → C).
        half = _visor_math(nodes, 'MULTIPLY', (-80, 40), "Blend × 0.5", clamp=True)
        half.parent = color_frame
        half.inputs[1].default_value = 0.5
        links.new(blend.outputs["Value"], half.inputs[0])
        links.new(half.outputs["Value"], glass_mix_c.inputs[0])
        links.new(glass_mix.outputs[2], glass_mix_c.inputs[6])
        glass_color_out = glass_mix_c.outputs[2]

    for socket in ("Sheen Tint", "Sheen Color"):
        if socket in principled.inputs:
            links.new(glass_color_out, principled.inputs[socket])

    # Swatch RGBs for readability in the node editor
    for i, (label, rgba) in enumerate((
        ("ColorA", color_a), ("ColorB", color_b),
        *( [("ColorC", color_c)] if color_c is not None else [] ),
    )):
        swatch = nodes.new("ShaderNodeRGB")
        swatch.label = label
        swatch.location = (-80, 420 - i * 180)
        swatch.parent = color_frame
        swatch.outputs[0].default_value = rgba

    dirt_factor = None
    if textures_by_param.get('DirtMask'):
        dirt_tiling = _visor_scalar(mi, 'DirtTiling')
        dirt_map = nodes.new("ShaderNodeMapping")
        dirt_map.label = "Dirt Tiling"
        dirt_map.location = (-1500, -620)
        dirt_map.parent = dirt_frame
        dirt_map.inputs["Scale"].default_value = (dirt_tiling, dirt_tiling, 1.0)
        links.new(uv, dirt_map.inputs["Vector"])
        dirt_image = _visor_image(nodes, links, textures_by_param['DirtMask'], "DirtMask",
                                  (-1300, -620), dirt_map.outputs["Vector"])
        dirt_image.parent = dirt_frame
        dirt_red = _visor_red(nodes, links, dirt_image, (-1180, -620))
        # _visor_red returns the Red socket; parent the SeparateColor it created.
        for node in nodes:
            if node.type == "SEPARATE_COLOR" and node.location.x == -1180 and node.location.y == -620:
                node.parent = dirt_frame
                break

        threshold = 1.0 - min(max(_visor_scalar(mi, 'DirtAmount'), 0.0), 1.0)
        softness = max(_visor_scalar(mi, 'DirtSoftness'), 0.001)
        dirt_ramp = _visor_map_range(nodes, "Dirt Threshold", (-980, -620),
                                     threshold - softness, threshold + softness)
        dirt_ramp.parent = dirt_frame
        links.new(dirt_red, dirt_ramp.inputs["Value"])

        dirt_scale = _visor_math(nodes, 'MULTIPLY', (-760, -620), "MaskDirt", clamp=True)
        dirt_scale.parent = dirt_frame
        dirt_scale.inputs[1].default_value = _visor_scalar(mi, 'MaskDirt')
        links.new(dirt_ramp.outputs["Result"], dirt_scale.inputs[0])
        dirt_factor = dirt_scale.outputs["Value"]

    dirt_mix = nodes.new("ShaderNodeMix")
    dirt_mix.data_type = 'RGBA'
    dirt_mix.blend_type = 'MIX'
    dirt_mix.label = "Glass → DirtColor"
    dirt_mix.location = (240, 60)
    dirt_mix.parent = color_frame
    dirt_mix.inputs[7].default_value = dirt_color
    links.new(glass_color_out, dirt_mix.inputs[6])
    if dirt_factor is not None:
        links.new(dirt_factor, dirt_mix.inputs[0])
    else:
        dirt_mix.inputs[0].default_value = 0.0
    links.new(dirt_mix.outputs[2], principled.inputs["Base Color"])

    # --- Roughness: smudge map over the authored modifier, then dirt on top ---
    roughness_modifier = _visor_scalar(mi, 'RoughnessModifier')
    base_roughness = None
    if textures_by_param.get('Roughness'):
        rough_tiling = _visor_scalar(mi, 'RoughnessTiling')
        rough_map = nodes.new("ShaderNodeMapping")
        rough_map.label = "Smudge Tiling"
        rough_map.location = (-1500, -960)
        rough_map.parent = dirt_frame
        rough_map.inputs["Scale"].default_value = (rough_tiling, rough_tiling, 1.0)
        links.new(uv, rough_map.inputs["Vector"])
        rough_image = _visor_image(nodes, links, textures_by_param['Roughness'], "Smudges",
                                    (-1300, -960), rough_map.outputs["Vector"])
        rough_image.parent = dirt_frame
        rough_red = _visor_red(nodes, links, rough_image, (-1180, -960))
        for node in nodes:
            if node.type == "SEPARATE_COLOR" and node.location.x == -1180 and node.location.y == -960:
                node.parent = dirt_frame
                break
        smudge = _visor_map_range(nodes, "Smudge Roughness", (-980, -960), 0.0, 1.0,
                                  to_min=roughness_modifier, to_max=1.0)
        smudge.parent = dirt_frame
        links.new(rough_red, smudge.inputs["Value"])
        base_roughness = smudge.outputs["Result"]

    if dirt_factor is not None:
        dirt_roughness = _visor_map_range(nodes, "Dirt Roughness", (240, -420), 0.0, 1.0,
                                          to_min=roughness_modifier,
                                          to_max=_visor_scalar(mi, 'DirtRoughness'))
        dirt_roughness.parent = dirt_frame
        links.new(dirt_factor, dirt_roughness.inputs["Value"])
        if base_roughness is not None:
            links.new(base_roughness, dirt_roughness.inputs["To Min"])
        links.new(dirt_roughness.outputs["Result"], principled.inputs["Roughness"])
    elif base_roughness is not None:
        links.new(base_roughness, principled.inputs["Roughness"])
    else:
        principled.inputs["Roughness"].default_value = roughness_modifier

    if textures_by_param.get('DirtNormal') and dirt_factor is not None:
        normal_image = _visor_image(nodes, links, textures_by_param['DirtNormal'], "DirtNormal",
                                    (-1300, -1280), dirt_map.outputs["Vector"] if textures_by_param.get('DirtMask') else uv)
        normal_image.parent = dirt_frame
        nm = nodes.new("ShaderNodeNormalMap")
        nm.label = "Dirt Normal Map"
        nm.location = (-980, -1280)
        nm.parent = dirt_frame
        try:
            nm.convention = 'DIRECTX'
        except Exception:
            pass
        links.new(normal_image.outputs["Color"], nm.inputs["Color"])
        # Strength driven by dirt mask
        links.new(dirt_factor, nm.inputs["Strength"])
        links.new(nm.outputs["Normal"], principled.inputs["Normal"])

    # Opacity scalar (opaque gameplay visor instances commonly author Opacity as zero).
    opacity = _visor_scalar(mi, 'Opacity')
    if mi.get('is_transparent') and "Alpha" in principled.inputs:
        principled.inputs["Alpha"].default_value = max(0.05, min(1.0, opacity if opacity > 0.0 else 0.65))


def setup_visor_material(obj, psk_path: str, mi_path: str = "", skin_json: str = ""):
    """Public entry point for a visor mesh: glass on the glass slots, shell left alone."""
    applied = apply_embedded_visor_slots(obj, psk_path, skin_json=skin_json)
    if applied:
        return applied

    folder = os.path.dirname(psk_path) if psk_path else ""
    skin_folder = os.path.dirname(skin_json) if skin_json and os.path.isfile(skin_json) else ""
    if not mi_path:
        mi_path = _find_glass_mi_in_dir(skin_folder) or _find_glass_mi_in_dir(folder)
    if not mi_path and folder and os.path.isdir(folder):
        for fname in sorted(os.listdir(folder)):
            fl = fname.lower()
            if fl.startswith("mi_") and fl.endswith(".json") and "visor" in fl:
                mi_path = os.path.join(folder, fname)
                break

    mat = bpy.data.materials.new(name=obj.name + "_Visor_Mat")
    obj.active_material = mat
    _setup_visor_material(mat, mi_path=mi_path, psk_path=psk_path, skin_json=skin_json)
    return 1