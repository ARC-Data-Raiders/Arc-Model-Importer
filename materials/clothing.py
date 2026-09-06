"""
Material setup — clothing domain (split from materials.py monolith).
"""
from __future__ import annotations

import os
import re
import json
import struct
import math
import difflib

import bpy
from mathutils import Vector

from .. import utils
from .. import textures
from .. import palette_calibration
from ..properties import BODY_ALBEDO, BODY_NORMAL

from . import gt_outfit_pbr as _gt_pbr
from .common import (
    _BASE_COLOR_OVERLAY_SLOT_LOCS,
    _BATCH_MATERIAL_MODE,
    _CLOTHING_CACHE_STATS,
    _CLOTHING_MATERIAL_CACHE,
    _IMAGE_BY_PATH,
    _NULL_DECAL_MARKERS,
    _WIDTH_RATIO_BY_PATH,
    _ensure_arc_on_glass_slot,
    _ensure_mesh_material_slot_count,
    _find_glass_mi_in_dir,
    _glass_slot_prefers_arc_override,
    _is_simple_pbr_material_name,
    _load_image_cached,
    _match_material_slot,
    _material_used_by_other_objects,
    _name_has_glass_lens_token,
    _norm_path_key,
    _normalize_mat_stem,
    _object_has_matchable_lens_slot,
    _parse_sk_material_slots,
    _psk_is_helmet_part,
    _psk_is_ocm_glass_hybrid_part,
    _remove_named_modifiers,
    _resolve_mi_json_path,
    _resolve_mi_texture_path,
    _set_material_alpha_mode,
    _slot_is_fur,
    apply_node_graph_padding,
    resolve_node_overlaps,
)



def expand_clothing_image_paths(
    path_union: list,
    io_cache: dict = None,
) -> list:
    """Re-resolve ObjectPath decals on the main thread into ``path_union``.

    Worker discovery skips ObjectPath (no bpy). This closes EmbarkScript /
    MaterialLibrary holes before ``prefetch_images``.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(p: str):
        if not p:
            return
        key = _norm_path_key(p)
        if key in seen:
            return
        seen.add(key)
        out.append(p)

    for p in path_union or ():
        _add(p)

    for _key, result in (io_cache or {}).items():
        if not isinstance(result, dict):
            continue
        json_path = (result.get("json_path") or "").strip()
        folder = ""
        try:
            # Prefer folder implied by main_pngs / psk beside json.
            mains = result.get("main_pngs") or []
            if json_path:
                folder = os.path.dirname(json_path)
            decals = []
            if json_path:
                try:
                    mi = textures.parse_clothing_mi(json_path) or {}
                    decals = mi.get("decals") or []
                except Exception:
                    decals = []
            extra = collect_clothing_image_paths(
                folder,
                main_pngs=mains,
                base_pngs=result.get("base_pngs") or [],
                decals=decals,
                decal_folder=utils.get_decal_folder() or "",
                search_dirs=[folder] if folder else None,
                resolve_object_paths=True,
            )
            for p in extra:
                _add(p)
        except Exception:
            continue
    return out



def collect_clothing_image_paths(
    folder: str = "",
    main_pngs=None,
    base_pngs=None,
    decals=None,
    decal_folder: str = "",
    search_dirs=None,
    *,
    resolve_object_paths: bool = True,
) -> list[str]:
    """Union of image paths a clothing build would load (Phase C prefetch hook).

    When ``resolve_object_paths`` is False (ThreadPool workers), skip UE ObjectPath
    lookups that touch ``bpy.context`` / global dir caches — local stem search only.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(p: str):
        if not p:
            return
        key = _norm_path_key(p)
        if key in seen:
            return
        seen.add(key)
        out.append(p)

    for fname in main_pngs or ():
        fpath = fname if os.path.isabs(fname) else os.path.join(folder or "", fname)
        _add(fpath)
    for p in base_pngs or ():
        _add(p)
    dirs = list(search_dirs or [])
    for d in (decals or ()):
        tex = (d.get("texture") or "").strip()
        if tex:
            try:
                _add(resolve_decal_texture(
                    tex,
                    d.get("texture_path", "") if resolve_object_paths else "",
                    decal_folder,
                    search_dirs=dirs,
                ))
            except Exception:
                pass
        data = (d.get("data_texture") or "").strip()
        if data:
            try:
                _add(resolve_decal_texture(
                    data,
                    d.get("data_texture_path", "") if resolve_object_paths else "",
                    decal_folder,
                    search_dirs=dirs,
                ))
            except Exception:
                pass
    return out



def _png_size_from_header(path: str):
    """Read PNG IHDR width/height without decoding pixels. None on failure."""
    try:
        with open(path, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                return None
            length = int.from_bytes(f.read(4), "big")
            if f.read(4) != b"IHDR" or length < 8:
                return None
            wh = f.read(8)
            if len(wh) < 8:
                return None
            w, h = struct.unpack(">II", wh)
            if w > 0 and h > 0:
                return int(w), int(h)
    except Exception:
        return None
    return None



def _width_ratio_from_path(path: str) -> float:
    """Aspect (w/h) via PNG header or cached image size; 0.0 if unknown."""
    if not path:
        return 0.0
    key = _norm_path_key(path)
    cached = _WIDTH_RATIO_BY_PATH.get(key)
    if cached is not None:
        return float(cached)
    size = _png_size_from_header(path)
    if size and size[1] > 0:
        ratio = float(size[0]) / float(size[1])
        _WIDTH_RATIO_BY_PATH[key] = ratio
        return ratio
    img = _IMAGE_BY_PATH.get(key)
    if img is not None:
        try:
            if img.size[1] > 0:
                ratio = float(img.size[0]) / float(img.size[1])
                _WIDTH_RATIO_BY_PATH[key] = ratio
                return ratio
        except Exception:
            pass
    return 0.0



def compute_clothing_texture_fingerprint(
    folder: str = "",
    main_pngs=None,
    base_pngs=None,
    colours: dict = None,
    decals=None,
) -> str:
    """Stable fingerprint of textures (+ colours) for clothing material sharing."""
    parts: list[str] = []
    for fname in main_pngs or ():
        fpath = fname if os.path.isabs(fname) else os.path.join(folder or "", fname)
        parts.append(_norm_path_key(fpath))
    for p in base_pngs or ():
        parts.append(_norm_path_key(p))
    for d in decals or ():
        tex = (d.get("texture") or "").strip()
        if tex:
            parts.append(f"decal:{tex}")
        data = (d.get("data_texture") or "").strip()
        if data:
            parts.append(f"dn:{data}")
    if colours:
        for k in sorted(colours.keys()):
            rgba = colours.get(k)
            if rgba is None:
                continue
            try:
                parts.append(
                    f"c:{k}:{float(rgba[0]):.5f},{float(rgba[1]):.5f},"
                    f"{float(rgba[2]):.5f},{float(rgba[3]):.5f}"
                )
            except (TypeError, ValueError, IndexError):
                parts.append(f"c:{k}")
    return "|".join(parts)



def clothing_material_cache_key(
    mi_json_path: str,
    texture_fingerprint: str,
    *,
    outfit_color_pipeline: str = "",
) -> tuple:
    """Cache key: ``(mi_path, texture_fingerprint, pipeline)``.

    Pipeline is included so Ground Truth vs Legacy graphs never share a cache hit.
    """
    pipe = palette_calibration.parse_outfit_color_pipeline(outfit_color_pipeline)
    # gt_pbr2: cooked Colour N plus roughness/metal/decals/normals/edge-crease (no Arc).
    gen = "gt_pbr2" if pipe == palette_calibration.OUTFIT_COLOR_PIPELINE_GROUND_TRUTH else "legacy"
    return (_norm_path_key(mi_json_path), texture_fingerprint or "", pipe, gen)



def _assign_clothing_material(obj, mat, psk_path: str = "") -> None:
    """Assign ``mat`` to the clothing/visor shell slot (or active material)."""
    if obj is None or mat is None:
        return
    _ensure_clothing_sk_slot_count(obj, psk_path)
    shell_idx = _find_clothing_shell_slot_index(obj, psk_path)
    if 0 <= shell_idx < len(getattr(obj, "material_slots", []) or []):
        obj.material_slots[shell_idx].material = mat
        try:
            obj.active_material_index = shell_idx
        except Exception:
            pass
    else:
        obj.active_material = mat



def _ensure_clothing_sk_slot_count(obj, psk_path: str = "") -> int:
    """Grow mesh material slots to match sibling SK StaticMaterials count.

    PSK import uses ``should_import_materials=False`` → often 0 Blender slots.
    Clothing then did ``active_material = mat`` (one slot); multi-slot visors
    need shell + glass indices from SK JSON before assign / glass apply.
    """
    if obj is None or not psk_path:
        return 0
    try:
        sk_slots = _parse_sk_material_slots(psk_path)
    except Exception:
        sk_slots = []
    if not sk_slots:
        return 0
    n = len(sk_slots)
    if n <= len(getattr(obj, "material_slots", []) or []):
        return 0
    added = _ensure_mesh_material_slot_count(obj, n)
    if added:
        print(
            f"Arc Raiders PSK Importer: Created {added} material slot(s) on "
            f"'{getattr(obj, 'name', '?')}' for clothing/visor SK materials"
        )
    return added



def _clothing_cache_get(key: tuple):
    mat = _CLOTHING_MATERIAL_CACHE.get(key)
    if mat is None:
        return None
    try:
        _ = mat.name
        return mat
    except ReferenceError:
        _CLOTHING_MATERIAL_CACHE.pop(key, None)
        return None



def _clothing_cache_store(key: tuple, mat) -> None:
    if key and mat is not None:
        _CLOTHING_MATERIAL_CACHE[key] = mat



def clothing_cache_stats() -> dict:
    """Return ``{hits, misses, size}`` for the clothing share-cache (perf drill-down)."""
    return {
        "hits": int(_CLOTHING_CACHE_STATS.get("hits", 0)),
        "misses": int(_CLOTHING_CACHE_STATS.get("misses", 0)),
        "size": len(_CLOTHING_MATERIAL_CACHE),
    }



def clear_clothing_material_cache():
    """Drop clothing shared-material session cache (call before Update Materials)."""
    _CLOTHING_MATERIAL_CACHE.clear()
    _CLOTHING_CACHE_STATS["hits"] = 0
    _CLOTHING_CACHE_STATS["misses"] = 0



def get_or_build_clothing_material(
    obj,
    folder: str,
    colours: dict,
    psk_path: str = "",
    json_path: str = "",
    decal_folder: str = "",
    selected_skin_name: str = "",
    manual_skins_folder: str = "",
    mi_data: dict = None,
    main_pngs: list = None,
    base_pngs: list = None,
    texture_fingerprint: str = "",
):
    """Return a shared clothing Material for this MI+texture set; build on miss.

    Phase C batch pipeline should call this instead of ``setup_arc_texturer_material``
    so identical colourway parts reuse one datablock.
    """
    return setup_arc_texturer_material(
        obj, folder, colours, psk_path,
        json_path=json_path, decal_folder=decal_folder,
        selected_skin_name=selected_skin_name,
        manual_skins_folder=manual_skins_folder,
        mi_data=mi_data, main_pngs=main_pngs, base_pngs=base_pngs,
        texture_fingerprint=texture_fingerprint,
        use_material_cache=True,
    )



def _find_arc_texturer_group_node(nodes):
    want = (getattr(utils, "_NODE_GROUP", "ArcTexturer") or "ArcTexturer").replace(" ", "").lower()
    for node in nodes or ():
        if getattr(node, "type", "") != "GROUP":
            continue
        tree = getattr(node, "node_tree", None)
        name = (getattr(tree, "name", "") or "") if tree else ""
        compact = name.replace(" ", "").lower()
        if compact == want or compact.startswith(want + "."):
            return node
        # Fallback: clothing graphs use a single large group with Character out.
        outs = getattr(node, "outputs", None)
        if outs and ("Character" in outs or "Displacement" in outs):
            return node
    return None



def _has_mi_param_value_nodes(nodes) -> bool:
    for node in nodes or ():
        if getattr(node, "bl_idname", "") not in ("ShaderNodeValue", "ShaderNodeRGB"):
            continue
        label = (getattr(node, "label", "") or "").strip()
        if _MI_PARAM_NAME_RE.match(label):
            return True
    return False



def organize_clothing_material_nodes(mat, mi_params: dict = None) -> bool:
    """Run deferred NCT / place_* / MI-param layout on one clothing material."""
    if mat is None or not getattr(mat, "use_nodes", False) or not mat.node_tree:
        return False
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    group_node = _find_arc_texturer_group_node(nodes)
    if group_node is None:
        if any((getattr(n, "name", "") or "").startswith("GT_") for n in nodes):
            for n in nodes:
                _gt_pbr._quiet_node(n)
            try:
                mat["arc_nodes_organized"] = 1
            except Exception:
                pass
            return True
        return False

    params = mi_params
    if params is None:
        json_path = str(mat.get("arc_mi_path", "") or mat.get("arc_skin_json", "") or "").strip()
        if json_path and os.path.isfile(json_path):
            try:
                known = set()
                for node in nodes:
                    if getattr(node, "bl_idname", "") == "ShaderNodeRGB":
                        lab = (getattr(node, "label", "") or getattr(node, "name", "") or "").strip()
                        if lab:
                            known.add(lab)
                params = textures.parse_all_mi_parameters(json_path, known_colour_names=known)
            except Exception:
                params = None
    if params and (params.get("scalars") or params.get("vectors")):
        if not _has_mi_param_value_nodes(nodes):
            place_mi_parameter_nodes(nodes, links, params)

    place_zone_overlay_controls(nodes, group_node)
    placed = apply_nct_clothing_layout(nodes)
    place_unconnected_colour_swatches(nodes)
    align_ta_normal_column(nodes, group_node=group_node)
    place_unconnected_ta_textures(nodes)
    if placed < 8:
        apply_node_graph_padding(nodes)
    try:
        mat["arc_nodes_organized"] = 1
        if "arc_nodes_need_organize" in mat:
            del mat["arc_nodes_need_organize"]
    except Exception:
        pass
    return True



def organize_clothing_nodes(materials=None, objects=None) -> int:
    """Organize nodes on given materials, or materials on objects. Returns count."""
    seen = set()
    mats = []
    for mat in materials or ():
        if mat is None:
            continue
        mid = id(mat)
        if mid in seen:
            continue
        seen.add(mid)
        mats.append(mat)
    for obj in objects or ():
        for slot in getattr(obj, "material_slots", []) or []:
            mat = getattr(slot, "material", None)
            if mat is None:
                continue
            mid = id(mat)
            if mid in seen:
                continue
            seen.add(mid)
            mats.append(mat)
    n = 0
    for mat in mats:
        if organize_clothing_material_nodes(mat):
            n += 1
    return n


def _unlink_socket_inputs(links, sock) -> None:
    """Remove all incoming links on a node socket (safe if already free)."""
    if sock is None:
        return
    while getattr(sock, "is_linked", False):
        try:
            links.remove(sock.links[0])
        except Exception:
            break


_CREASE_EDGE_COLOR_SOCK_RE = re.compile(r"^(Crease|Edge)\s+(\d+)$", re.I)
_CREASE_EDGE_OVERLAY_LABEL_RE = re.compile(
    r"^(\d+)_(Crease|Edge)ColorOverlay$", re.I
)
# TA IDs that only feed Arc edge/crease wear (skip when crease/edge color is off).
_CREASE_EDGE_TA_ID_SUFFIXES = frozenset({
    "EdgeNormalID",
    "CreaseNormalID",
    "EdgeRoughnessID",
    "CreaseRoughnessID",
    "CreaseMaskID",
    "EdgeMaskID",
})


def _iter_crease_edge_color_sockets(group_node):
    """Yield ArcTexturer Crease N / Edge N color input sockets only (not normals)."""
    if group_node is None:
        return
    for sock in group_node.inputs:
        name = (getattr(sock, "name", "") or "").strip()
        if _CREASE_EDGE_COLOR_SOCK_RE.match(name):
            yield sock


def _node_mentions_edge_crease(node) -> bool:
    tree = getattr(node, "node_tree", None)
    tag = (
        f"{getattr(node, 'name', '')}|"
        f"{getattr(node, 'label', '')}|"
        f"{getattr(tree, 'name', '') or ''}"
    )
    return "EdgeCrease" in tag.replace(" ", "")


def _zero_factor_inputs(node) -> None:
    """Unlink and zero Fac/Factor/Amount/Strength-like inputs on a node."""
    links = getattr(getattr(node, "id_data", None), "links", None)
    for inp in getattr(node, "inputs", []) or []:
        iname = (getattr(inp, "name", "") or "").lower()
        if not any(k in iname for k in ("fac", "factor", "amount", "strength", "blend")):
            continue
        if links is not None:
            while getattr(inp, "is_linked", False):
                try:
                    links.remove(inp.links[0])
                except Exception:
                    break
        try:
            dv = inp.default_value
            if isinstance(dv, float) or type(dv).__name__ in ("float", "bpy_prop_array"):
                # float or color — set scalar factors to 0; skip colors
                if "color" in iname:
                    continue
                inp.default_value = 0.0
        except Exception:
            try:
                inp.default_value = 0.0
            except Exception:
                pass


def _set_arc_edge_crease_controller_muted(group_node, muted: bool) -> int:
    """Mute/unmute EdgeCrease-Controller (and zero its factors when muted)."""
    ng = getattr(group_node, "node_tree", None) if group_node is not None else None
    if ng is None:
        return 0
    n = 0
    for node in ng.nodes:
        if not _node_mentions_edge_crease(node):
            continue
        try:
            node.mute = bool(muted)
        except Exception:
            pass
        if muted:
            _zero_factor_inputs(node)
            # MaterialID drives where crease/edge tint applies — cut that feed too.
            for inp in getattr(node, "inputs", []) or []:
                iname = (getattr(inp, "name", "") or "").lower()
                if "material" not in iname and "curvature" not in iname and "mask" not in iname:
                    continue
                links = getattr(ng, "links", None)
                if links is None:
                    continue
                while getattr(inp, "is_linked", False):
                    try:
                        links.remove(inp.links[0])
                    except Exception:
                        break
        n += 1
    return n


def apply_crease_edge_color_wiring(group_node, links, nodes, colours, *, enabled: bool) -> None:
    """Wire or fully disable ArcTexturer crease/edge color contribution.

    When ``enabled``, link ``N_CreaseColorOverlay`` / ``N_EdgeColorOverlay`` RGB
    into ``Crease N`` / ``Edge N`` and unmute EdgeCrease-Controller.

    When disabled:
    - Unlink every ``Crease N`` / ``Edge N`` color socket (nothing connected)
    - Hide those sockets on the ArcTexturer instance
    - Mute ``EdgeCrease-Controller`` inside the shared group and zero its Fac
      inputs (controller also takes MaterialID — color sockets alone are not enough)
    Do **not** passthrough Colour N into Crease/Edge (breaks ColorMask placement).
    """
    if group_node is None:
        return

    if not enabled:
        for sock in _iter_crease_edge_color_sockets(group_node):
            _unlink_socket_inputs(links, sock)
            try:
                sock.hide = True
            except Exception:
                pass
            try:
                sock.default_value = (0.0, 0.0, 0.0, 1.0)
            except Exception:
                pass
        _set_arc_edge_crease_controller_muted(group_node, True)
        return

    for sock in _iter_crease_edge_color_sockets(group_node):
        try:
            sock.hide = False
        except Exception:
            pass
    _set_arc_edge_crease_controller_muted(group_node, False)

    overlay_map = {
        "_CreaseColorOverlay": "Crease",
        "_EdgeColorOverlay": "Edge",
    }
    colour_keys = list(colours.keys()) if colours else []
    for node in nodes:
        lab = (getattr(node, "label", "") or getattr(node, "name", "") or "").strip()
        if lab and lab not in colour_keys and (
            lab.endswith("_CreaseColorOverlay") or lab.endswith("_EdgeColorOverlay")
        ):
            colour_keys.append(lab)

    for key in colour_keys:
        rgba = (colours or {}).get(key)
        if rgba is not None and textures.skip_colour(rgba):
            continue
        for suffix, arc_prefix in overlay_map.items():
            if not key.endswith(suffix):
                continue
            zone_str = key[: -len(suffix)]
            if not zone_str.isdigit():
                continue
            arc_sock = f"{arc_prefix} {zone_str}"
            sock = group_node.inputs.get(arc_sock)
            rgb_nd = nodes.get(key)
            if sock is None or rgb_nd is None:
                break
            _unlink_socket_inputs(links, sock)
            try:
                links.new(rgb_nd.outputs[0], sock)
            except Exception:
                pass
            break


def crease_edge_color_enabled_from_scene() -> bool:
    """Scene toggle: wire crease/edge color overlays (default off while XYZ focus)."""
    try:
        scene = getattr(bpy.context, "scene", None)
        if scene is not None and hasattr(scene, "arc_crease_edge_color"):
            return bool(scene.arc_crease_edge_color)
    except Exception:
        pass
    return False


_CM_ZONE_LABEL_RE = re.compile(r"ColorMask_XYZ\s*\(zone\s*(\d+)\)", re.I)


def outfit_color_pipeline_from_scene() -> str:
    """``legacy`` | ``ground_truth`` from Scene (default **legacy** / ColorMask_XYZ).

    Revert: set ``scene.arc_outfit_color_pipeline = 'LEGACY'`` then Update Materials.
    Ground-truth PatternHue / ColorTex assemble must not run on this path.
    """
    try:
        from .. import properties as _props

        if hasattr(_props, "ensure_scene_properties"):
            _props.ensure_scene_properties()
    except Exception:
        pass
    try:
        scene = getattr(bpy.context, "scene", None)
        raw = str(getattr(scene, "arc_outfit_color_pipeline", "") or "") if scene else ""
        return palette_calibration.parse_outfit_color_pipeline(raw)
    except Exception:
        return palette_calibration.OUTFIT_COLOR_PIPELINE_DEFAULT


def _find_basecolor_tex_for_arc(nodes, group_node):
    """Image node already linked to Arc ``Base Color``, if any."""
    try:
        sock = group_node.inputs.get("Base Color") if group_node else None
        if sock is not None and getattr(sock, "is_linked", False) and sock.links:
            return sock.links[0].from_node
    except Exception:
        pass
    for node in nodes:
        if getattr(node, "bl_idname", "") != "ShaderNodeTexImage":
            continue
        lab = (getattr(node, "label", "") or getattr(node, "name", "") or "").lower()
        if "basecolor" in lab or "base_color" in lab or lab.endswith("_bch"):
            return node
    return None


def _rgba_from_colours(colours: dict | None, key: str, fallback=(1.0, 1.0, 1.0, 1.0)):
    """Return RGBA tuple for ``key`` from MI colours, else ``fallback``."""
    rgba = (colours or {}).get(key)
    if rgba is None:
        return (
            float(fallback[0]),
            float(fallback[1]),
            float(fallback[2]),
            float(fallback[3]) if len(fallback) > 3 else 1.0,
        )
    try:
        return (
            float(rgba[0]),
            float(rgba[1]),
            float(rgba[2]),
            float(rgba[3]) if len(rgba) > 3 else 1.0,
        )
    except (TypeError, ValueError, IndexError):
        return (
            float(fallback[0]),
            float(fallback[1]),
            float(fallback[2]),
            float(fallback[3]) if len(fallback) > 3 else 1.0,
        )


def _ensure_core_scheme_colour_nodes(nodes, colours: dict | None) -> dict:
    """Ensure ColorA/B/C and ColorA2/B2/C2 RGB nodes exist for scheme Mix wiring.

    Secondary (A2/B2/C2) nodes are always created — even when the MI omits them —
    so GT ``lerp(ColorX, ColorX2, ColorMask.r)`` never silently aliases both Mix
    endpoints to ColorA/B/C (which looked like "A2 unused / ABC→XYZ only").
    Missing A2 values copy the matching primary.
    """
    created = []
    pri = {
        "ColorA": _rgba_from_colours(colours, "ColorA"),
        "ColorB": _rgba_from_colours(colours, "ColorB"),
        "ColorC": _rgba_from_colours(colours, "ColorC"),
    }
    for key, loc in (
        ("ColorA", (-1600.0, 280.0)),
        ("ColorB", (-1600.0, -20.0)),
        ("ColorC", (-1600.0, -320.0)),
    ):
        nd = nodes.get(key)
        if nd is None:
            nd = nodes.new("ShaderNodeRGB")
            nd.name = key
            nd.label = key
            nd.outputs[0].default_value = pri[key]
            nd.location = loc
            created.append(key)
        elif key in (colours or {}):
            nd.outputs[0].default_value = pri[key]
    for pri_key, sec_key, loc in (
        ("ColorA", "ColorA2", (-1280.0, 280.0)),
        ("ColorB", "ColorB2", (-1280.0, -20.0)),
        ("ColorC", "ColorC2", (-1280.0, -320.0)),
    ):
        rgba = _rgba_from_colours(colours, sec_key, fallback=pri[pri_key])
        nd = nodes.get(sec_key)
        if nd is None:
            nd = nodes.new("ShaderNodeRGB")
            nd.name = sec_key
            nd.label = sec_key
            nd.outputs[0].default_value = rgba
            nd.location = loc
            created.append(sec_key)
        elif sec_key in (colours or {}):
            nd.outputs[0].default_value = rgba
        else:
            try:
                unused = not any(o.links for o in nd.outputs)
            except Exception:
                unused = False
            if unused:
                nd.outputs[0].default_value = rgba
    return {"created": created, "primary": pri}


def _unlink_colour_socket(links, group_node, zone: int) -> None:
    """Clear whatever currently drives Arc ``Colour N`` (GT assemble or Mask_Color)."""
    if group_node is None:
        return
    colour_sock = f"Colour {zone}"
    if colour_sock not in group_node.inputs:
        return
    sock = group_node.inputs[colour_sock]
    while getattr(sock, "is_linked", False):
        try:
            links.remove(sock.links[0])
        except Exception:
            break


# Back-compat alias (older call sites / live rewire helpers).
_unlink_colour_from_colormask = _unlink_colour_socket


def _new_mix_multiply(nodes, *, name: str, label: str, location, hide: bool = True):
    """RGBA Multiply Mix with Factor forced to 1 (full multiply)."""
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.name = name
    mix.label = label
    mix.location = location
    mix.hide = hide
    factor, _, _, _ = _gt_pbr.mix_io(mix)
    if factor is not None:
        try:
            factor.default_value = 1.0
        except Exception:
            pass
    try:
        mix.inputs[0].default_value = 1.0
    except Exception:
        pass
    return mix


def _gt_mix_link(links, mix, a_sock, b_sock, *, fac_sock=None, fac_value=None):
    """Link Mix A/B (and optional Factor) using type-correct sockets. Returns Result."""
    factor, a_in, b_in, result = _gt_pbr.mix_io(mix)
    if fac_sock is not None and factor is not None:
        links.new(fac_sock, factor)
    elif fac_value is not None and factor is not None:
        try:
            factor.default_value = float(fac_value)
        except Exception:
            pass
    if a_in is not None:
        links.new(a_sock, a_in)
    if b_in is not None:
        links.new(b_sock, b_in)
    if result is not None:
        return result
    try:
        return mix.outputs["Result"]
    except Exception:
        return mix.outputs[0] if mix.outputs else None


def _new_mix_lerp_white(nodes, *, name: str, label: str, location, hide: bool = True):
    """RGBA Mix: A=white, B=color, Factor=fac → lerp(1, color, fac) (D039 canvas)."""
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MIX"
    mix.name = name
    mix.label = label
    mix.location = location
    mix.hide = hide
    return mix


def _link_mix_factor_from_sep(links, sep, mix, *, channel: str = "Red", index: int = 0) -> bool:
    """Wire Separate Color/RGB channel → Mix Factor (Blender 3.4+ ShaderNodeMix)."""
    if sep is None or mix is None:
        return False
    fac_in = None
    try:
        fac_in = mix.inputs.get("Factor") or mix.inputs[0]
    except Exception:
        fac_in = mix.inputs[0] if mix.inputs else None
    if fac_in is None:
        return False
    aliases = {
        "Red": ("Red", "R"),
        "Green": ("Green", "G"),
        "Blue": ("Blue", "B"),
    }
    names = aliases.get(channel, (channel,))
    ch_out = None
    for name in names:
        try:
            ch_out = sep.outputs.get(name)
        except Exception:
            ch_out = None
        if ch_out is not None:
            break
    if ch_out is None and sep.outputs:
        try:
            ch_out = sep.outputs[index]
        except Exception:
            ch_out = sep.outputs[0]
    if ch_out is None:
        return False
    try:
        links.new(ch_out, fac_in)
        return True
    except Exception:
        return False


def _link_mix_factor_from_sep_red(links, sep, mix) -> bool:
    """Wire Separate Color/RGB **R** → Mix Factor (Blender 3.4+ ShaderNodeMix)."""
    return _link_mix_factor_from_sep(links, sep, mix, channel="Red", index=0)


def _mi_layer_float(store: dict | None, layer: int, suffix: str, default: float = 0.0) -> float:
    """Read ``N_Suffix`` from parse_clothing_mi zone_scalars / ta_ids."""
    if not store:
        return float(default)
    layer_s = str(layer)
    for key, val in store.items():
        if isinstance(key, tuple) and len(key) == 2:
            if str(key[0]) != layer_s or key[1] != suffix:
                continue
        elif isinstance(key, str):
            if key != f"{layer_s}_{suffix}":
                continue
        else:
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            return float(default)
    return float(default)


def _colour_rgba(colours: dict | None, key: str, default=(1.0, 1.0, 1.0, 1.0)):
    if not colours:
        return default
    val = colours.get(key)
    if val is None or len(val) < 3:
        return default
    a = float(val[3]) if len(val) > 3 else 1.0
    return (float(val[0]), float(val[1]), float(val[2]), a)


def _gt_param_float(mi_data, name, default=0.0):
    """Read a named scalar from ``parse_clothing_mi`` ``mi_params``."""
    params = (mi_data or {}).get("mi_params") or {}
    for n, v in (params.get("scalars") or []):
        if n == name:
            try:
                return float(v)
            except (TypeError, ValueError):
                return default
    return default


def _gt_ta_id(ta_ids, zone, suffix):
    if not ta_ids:
        return None
    z = str(zone)
    val = ta_ids.get((z, suffix))
    if val is None:
        val = ta_ids.get((int(zone), suffix))
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _gt_attach_local(nd, parent, xy, *, hide=None, lock=True):
    """Parent first, then set local location.

    Blender keeps world location when parenting. Setting location while unparented
    then assigning a Colour N frame left GT_ColourN_z* at world (0,0).
    """
    if nd is None:
        return None
    if parent is not None:
        try:
            nd.parent = parent
        except Exception:
            pass
    nd.location = (float(xy[0]), float(xy[1]))
    if hide is not None:
        nd.hide = hide
    if lock:
        _gt_pbr.lock_layout(nd)
    return nd


def _new_math(nodes, op, name, location, parent=None, value=None, hide=True):
    nd = nodes.new("ShaderNodeMath")
    nd.operation = op
    nd.name = name
    nd.hide = hide
    if value is not None:
        try:
            nd.inputs[1].default_value = float(value)
        except Exception:
            pass
    return _gt_attach_local(nd, parent, location, hide=None, lock=True)


def _wire_gt_zone_wet(
    nodes, links, parent, colours, zone_scalars, zone, colour_src, y0, *, col_x=None, hide=True
):
    """WetTint.x × ShadeAsCloth × view-Z _3712 → lerp toward SnowColor (Layered DXBC)."""
    wet_rgba = _colour_rgba(colours, "WetTint", (0.0, 0.0, 0.0, 1.0))
    snow_rgba = _colour_rgba(colours, "SnowColor", (1.0, 1.0, 1.0, 1.0))
    wet_x = max(0.0, min(1.0, wet_rgba[0]))
    cloth = max(0.0, min(1.0, _mi_layer_float(zone_scalars, zone, "ShadeAsCloth", 0.0)))
    max_wet = 0.0
    if isinstance(zone_scalars, dict):
        try:
            max_wet = float(zone_scalars.get("MaxWetAmount", 0.0) or 0.0)
        except (TypeError, ValueError):
            max_wet = 0.0
    max_wet = max(0.0, min(1.0, max_wet))
    if cloth * wet_x <= 1e-6:
        return colour_src

    def _xy(legacy_x, dy=0.0, step=0):
        if col_x is None:
            return (legacy_x, y0 + dy)
        pitch = 0.0 if hide else 100.0
        return (col_x + step * pitch, y0 + dy)

    snow_nd = nodes.new("ShaderNodeRGB")
    snow_nd.name = f"GT_SnowColor_z{zone}"
    snow_nd.label = f"SnowColor z{zone}"
    snow_nd.outputs[0].default_value = snow_rgba
    _gt_attach_local(snow_nd, parent, _xy(-220.0, 36.0, 12), hide=hide)

    cam = nodes.new("ShaderNodeCameraData")
    cam.name = f"GT_viewZ_cam_z{zone}"
    cam.label = "view-Z (Layered _3712)"
    _gt_attach_local(cam, parent, _xy(-620.0, 0.0, 0), hide=hide)
    sep = nodes.new("ShaderNodeSeparateXYZ")
    sep.name = f"GT_viewZ_sep_z{zone}"
    _gt_attach_local(sep, parent, _xy(-500.0, 0.0, 1), hide=hide)
    links.new(cam.outputs["View Vector"], sep.inputs[0])
    absz = _new_math(nodes, "ABSOLUTE", f"GT_viewZ_abs_z{zone}", _xy(-420.0, 0.0, 2), parent)
    links.new(sep.outputs[2], absz.inputs[0])
    mul_mw = _new_math(nodes, "MULTIPLY", f"GT_viewZ_mw_z{zone}", _xy(-340.0, 0.0, 3), parent, max_wet)
    links.new(absz.outputs[0], mul_mw.inputs[0])
    wet_inv = _new_math(nodes, "SUBTRACT", f"GT_viewZ_inv_z{zone}", _xy(-260.0, 40.0, 4), parent)
    wet_inv.inputs[0].default_value = 1.0
    links.new(mul_mw.outputs[0], wet_inv.inputs[1])
    sq = _new_math(nodes, "MULTIPLY", f"GT_viewZ_sq_z{zone}", _xy(-180.0, 40.0, 5), parent)
    links.new(wet_inv.outputs[0], sq.inputs[0])
    links.new(wet_inv.outputs[0], sq.inputs[1])
    times = _new_math(nodes, "MULTIPLY", f"GT_viewZ_115_z{zone}", _xy(-100.0, 40.0, 6), parent, 1.15)
    links.new(sq.outputs[0], times.inputs[0])
    sub = _new_math(nodes, "SUBTRACT", f"GT_viewZ_occ2_z{zone}", _xy(-20.0, 40.0, 7), parent)
    sub.inputs[0].default_value = 1.0
    links.new(times.outputs[0], sub.inputs[1])
    scale = _new_math(nodes, "MULTIPLY", f"GT_viewZ_333_z{zone}", _xy(60.0, 40.0, 8), parent, 10.0 / 3.0)
    links.new(sub.outputs[0], scale.inputs[0])
    nv = _new_math(nodes, "MINIMUM", f"GT_nvTerm_z{zone}", _xy(140.0, 40.0, 9), parent, 1.0)
    links.new(scale.outputs[0], nv.inputs[0])
    nv0 = _new_math(nodes, "MAXIMUM", f"GT_nvTerm0_z{zone}", _xy(220.0, 40.0, 10), parent, 0.0)
    nv0.inputs[1].default_value = 0.0
    links.new(nv.outputs[0], nv0.inputs[0])
    wet_scale = _new_math(
        nodes, "MULTIPLY", f"GT_wetScale_z{zone}", _xy(300.0, 40.0, 11), parent, cloth * wet_x
    )
    links.new(nv0.outputs[0], wet_scale.inputs[0])
    wet_fac = _new_math(nodes, "SUBTRACT", f"GT_wetFac_z{zone}", _xy(-260.0, 0.0, 13), parent)
    wet_fac.inputs[0].default_value = 1.0
    links.new(wet_scale.outputs[0], wet_fac.inputs[1])

    mul = _new_mix_multiply(
        nodes,
        name=f"GT_wet_mul_z{zone}",
        label=f"GT rgb×wetFac viewZ z{zone}",
        location=_xy(-200.0, 0.0, 14),
        hide=hide,
    )
    _gt_attach_local(mul, parent, _xy(-200.0, 0.0, 14), hide=hide)
    comb = nodes.new("ShaderNodeCombineColor")
    comb.name = f"GT_wetFac_comb_z{zone}"
    _gt_attach_local(comb, parent, _xy(-280.0, -10.0, 13), hide=hide)
    links.new(wet_fac.outputs[0], comb.inputs[0])
    links.new(wet_fac.outputs[0], comb.inputs[1])
    links.new(wet_fac.outputs[0], comb.inputs[2])
    colour_src = _gt_mix_link(links, mul, colour_src, comb.outputs[0], fac_value=1.0)

    mix_snow = nodes.new("ShaderNodeMix")
    mix_snow.data_type = "RGBA"
    mix_snow.blend_type = "MIX"
    mix_snow.name = f"GT_wet_snow_z{zone}"
    mix_snow.label = f"GT lerp(SnowColor, viewZ×cloth×WetTint.x) z{zone}"
    _gt_attach_local(mix_snow, parent, _xy(-80.0, 0.0, 15), hide=hide)
    return _gt_mix_link(
        links, mix_snow, colour_src, snow_nd.outputs[0], fac_sock=wet_scale.outputs[0]
    )


def _find_colortex_slice_png(png_lists, slice_idx: int) -> str:
    """Resolve ColorTexture array slice. Callers must refuse ID < 1 (F12)."""
    try:
        idx = int(round(float(slice_idx)))
    except (TypeError, ValueError):
        return ""
    if idx < 1:
        return ""
    preferred = []
    fallback = []
    for png_list in png_lists or ():
        for fpath in png_list or ():
            stem = os.path.splitext(os.path.basename(fpath))[0]
            if not stem.endswith(f"_{idx}"):
                continue
            low = stem.lower()
            if "colormask" in low or "basecolor" in low:
                continue
            if "_masks" in low or low.endswith("_mask"):
                continue
            if "pattern" in low or "_normal" in low:
                continue
            if "color" in low or "colour" in low:
                preferred.append(fpath)
            else:
                fallback.append(fpath)
    if preferred:
        return preferred[0]
    if fallback:
        return fallback[0]
    for png_list in png_lists or ():
        hit = textures.find_slice_png(list(png_list or []), idx)
        if hit:
            return hit
    return ""


def _find_pattern_slice_png(png_lists, slice_idx: int) -> str:
    """Resolve TextureArray pattern slice. Callers must refuse ID < 1."""
    try:
        idx = int(round(float(slice_idx)))
    except (TypeError, ValueError):
        return ""
    if idx < 1:
        return ""
    preferred = []
    fallback = []
    for png_list in png_lists or ():
        for fpath in png_list or ():
            stem = os.path.splitext(os.path.basename(fpath))[0]
            if not stem.endswith(f"_{idx}"):
                continue
            low = stem.lower()
            if "pattern" in low:
                preferred.append(fpath)
            elif "colormask" in low or "basecolor" in low or "huevariation" in low:
                continue
            else:
                fallback.append(fpath)
    if preferred:
        return preferred[0]
    if fallback:
        return fallback[0]
    for png_list in png_lists or ():
        hit = textures.find_slice_png(list(png_list or []), idx)
        if hit:
            return hit
    return ""


def _find_huevariation_png(png_lists) -> str:
    for png_list in png_lists or ():
        for fpath in png_list or ():
            if "huevariation" in os.path.basename(fpath).lower():
                return fpath
    return ""


def _mi_global_float(store: dict | None, name: str, default: float = 0.0) -> float:
    if not store:
        return float(default)
    val = store.get(name)
    if val is None:
        return float(default)
    try:
        return float(val)
    except (TypeError, ValueError):
        return float(default)


def _live_pattern_rgba(colours: dict | None, zone: int, suffix: str):
    """PatternColor with live alpha; None if unused / sentinel."""
    key = f"{zone}_{suffix}"
    rgba = (colours or {}).get(key)
    if rgba is None:
        return None
    try:
        a = float(rgba[3]) if len(rgba) > 3 else 0.0
        if abs(a) <= 1e-4:
            return None
        if textures.skip_colour(rgba):
            return None
        return (float(rgba[0]), float(rgba[1]), float(rgba[2]), a)
    except (TypeError, ValueError, IndexError):
        return None


def _wire_gt_zone_pattern_hue(
    nodes,
    links,
    parent,
    colours: dict,
    zone_scalars: dict,
    ta_ids: dict | None,
    zone: int,
    colour_src,
    y0: float,
    png_lists,
    pattern_images: dict,
    hue_cache: dict,
    *,
    col_x=None,
    hide=True,
):
    """Layered DXBC: Patterns×PatternColorA/B/C then HueVariation (strength×0.1).

    Arc Texturer ``Pattern N`` sockets stay on the legacy group; GT must mix
    here so Colour N matches Snooper ``ArcZoneColor``.
    """
    def _xy(legacy_x, dy=0.0, step=0):
        if col_x is None:
            return (legacy_x, y0 + dy)
        pitch = 0.0 if hide else 140.0
        return (col_x + step * pitch, y0 + dy)

    pattern_id = _mi_layer_float(ta_ids, zone, "PatternID", 0.0)
    live = []
    for suffix, channel in (("PatternColorA", "Red"), ("PatternColorB", "Green"), ("PatternColorC", "Blue")):
        rgba = _live_pattern_rgba(colours, zone, suffix)
        if rgba is not None:
            live.append((suffix, channel, rgba))
    if pattern_id >= 1.0 and live and png_lists:
        png_path = _find_pattern_slice_png(png_lists, pattern_id)
        img = pattern_images.get(png_path) if png_path else None
        if img is None and png_path:
            img = _load_image_cached(png_path)
            if img is not None:
                try:
                    img.colorspace_settings.name = "sRGB"
                except Exception:
                    pass
                pattern_images[png_path] = img
        if img is not None:
            tiling = _mi_layer_float(zone_scalars, zone, "PatternTiling", 0.0)
            if tiling <= 1e-6:
                tiling = _mi_layer_float(zone_scalars, zone, "ColorTextureTiling", 1.0)
            if tiling <= 1e-6:
                tiling = 1.0
            uv = hue_cache.get("pattern_uv")
            if uv is None:
                uv = nodes.new("ShaderNodeTexCoord")
                uv.name = "GT_Pattern_UV"
                uv.label = "GT Pattern UV"
                uv.location = (-1080.0, 540.0) if col_x is None else (_gt_pbr.maps_uv_x(), _gt_pbr.colour_row_y("pattern"))
                uv.hide = hide
                uv.parent = None if col_x is not None else parent
                hue_cache["pattern_uv"] = uv
            mapping = nodes.new("ShaderNodeMapping")
            mapping.name = f"GT_Pattern_map_z{zone}"
            mapping.label = f"GT Pattern tile={tiling:.3f} z{zone}"
            _gt_attach_local(mapping, parent, _xy(-260.0, 80.0, 0), hide=hide)
            mapping.inputs["Scale"].default_value = (tiling, tiling, tiling)
            links.new(uv.outputs["UV"], mapping.inputs["Vector"])
            tex_nd = nodes.new("ShaderNodeTexImage")
            tex_nd.name = f"GT_Pattern_z{zone}"
            tex_nd.label = os.path.basename(png_path)
            tex_nd.image = img
            tex_nd.interpolation = "Cubic"
            _gt_attach_local(tex_nd, parent, _xy(-160.0, 80.0, 1), hide=hide)
            links.new(mapping.outputs["Vector"], tex_nd.inputs["Vector"])
            sep = nodes.new("ShaderNodeSeparateColor")
            sep.name = f"GT_Pattern_sep_z{zone}"
            sep.label = f"GT Pattern.rgb z{zone}"
            _gt_attach_local(sep, parent, _xy(-40.0, 80.0, 2), hide=hide)
            links.new(tex_nd.outputs["Color"], sep.inputs["Color"])
            hue_cache.setdefault("pattern_zones", []).append(zone)
            for suffix, channel, rgba in live:
                rgb_nd = nodes.get(f"{zone}_{suffix}")
                if rgb_nd is None:
                    rgb_nd = nodes.new("ShaderNodeRGB")
                    rgb_nd.name = f"{zone}_{suffix}"
                    rgb_nd.label = f"{zone}_{suffix}"
                    rgb_nd.outputs[0].default_value = (*rgba,)
                    _gt_attach_local(rgb_nd, parent, _xy(40.0, 0.0, 3), hide=hide)
                fac = _new_math(
                    nodes,
                    "MULTIPLY",
                    f"GT_Pattern_fac_{suffix}_z{zone}",
                    _xy(40.0, 40.0, 3),
                    parent=parent,
                    value=rgba[3],
                )
                _link_mix_factor_from_sep(links, sep, fac, channel=channel, index={"Red": 0, "Green": 1, "Blue": 2}[channel])
                # Math: input 0 from pattern channel, input 1 = alpha (value=)
                # _new_math with value sets one input; we need channel × alpha.
                # Relink: Separate → Math[0], alpha stays on Math[1].
                mix = nodes.new("ShaderNodeMix")
                mix.data_type = "RGBA"
                mix.blend_type = "MIX"
                mix.name = f"GT_Pattern_{suffix}_z{zone}"
                mix.label = f"GT lerp(col,{suffix},a×pat) z{zone}"
                _gt_attach_local(mix, parent, _xy(160.0, 0.0, 4), hide=hide)
                colour_src = _gt_mix_link(
                    links, mix, colour_src, rgb_nd.outputs[0], fac_sock=fac.outputs[0]
                )

    hue_str = _mi_layer_float(zone_scalars, zone, "HueVariationStrength", 0.0)
    if abs(hue_str) <= 1e-8:
        hue_str = _mi_global_float(zone_scalars, "HueVariationStrength", 0.0)
    hue_fac = max(0.0, min(1.0, hue_str * 0.1))
    hue_png = _find_huevariation_png(png_lists)
    if hue_fac > 1e-6 and hue_png:
        img = hue_cache.get("hue_img")
        if img is None:
            img = _load_image_cached(hue_png)
            if img is not None:
                try:
                    img.colorspace_settings.name = "sRGB"
                except Exception:
                    pass
                hue_cache["hue_img"] = img
                hue_cache["hue_png"] = hue_png
        if img is not None:
            hue_tile = _mi_layer_float(zone_scalars, zone, "HueVariationTiling", 0.0)
            if hue_tile <= 1e-6:
                hue_tile = _mi_global_float(zone_scalars, "HueVariationTiling", 1.0)
            if hue_tile <= 1e-6:
                hue_tile = 1.0
            uv = hue_cache.get("hue_uv")
            if uv is None:
                uv = nodes.new("ShaderNodeTexCoord")
                uv.name = "GT_Hue_UV"
                uv.label = "GT HueVariation UV"
                uv.location = (-1080.0, 460.0) if col_x is None else (-2200.0, 2600.0)
                uv.hide = hide
                uv.parent = None if col_x is not None else parent
                hue_cache["hue_uv"] = uv
            mapping = nodes.new("ShaderNodeMapping")
            mapping.name = f"GT_Hue_map_z{zone}"
            mapping.label = f"GT Hue tile={hue_tile:.3f} z{zone}"
            _gt_attach_local(mapping, parent, _xy(280.0, 40.0, 5), hide=hide)
            mapping.inputs["Scale"].default_value = (hue_tile, hue_tile, hue_tile)
            links.new(uv.outputs["UV"], mapping.inputs["Vector"])
            tex_nd = nodes.new("ShaderNodeTexImage")
            tex_nd.name = f"GT_HueVariation_z{zone}"
            tex_nd.label = os.path.basename(hue_png)
            tex_nd.image = img
            tex_nd.interpolation = "Cubic"
            _gt_attach_local(tex_nd, parent, _xy(380.0, 0.0, 6), hide=hide)
            links.new(mapping.outputs["Vector"], tex_nd.inputs["Vector"])
            mix = nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.blend_type = "MIX"
            mix.name = f"GT_Hue_z{zone}"
            mix.label = f"GT lerp(col,Hue,str×0.1={hue_fac:.3f}) z{zone}"
            _gt_attach_local(mix, parent, _xy(480.0, 0.0, 7), hide=hide)
            colour_src = _gt_mix_link(
                links, mix, colour_src, tex_nd.outputs["Color"], fac_value=float(hue_fac)
            )
            hue_cache.setdefault("hue_zones", []).append(zone)
    return colour_src


def _gt_wire_colour_n_group(
    nodes, links, row_parent, loc, zone, amt, swatch_nd, cm_src, colours, uses_sec,
    mat_key: str = "mat",
):
    """Instance a per-zone GT_ColourN group (internals = this Color N only)."""
    sec = bool(uses_sec[zone - 1]) if uses_sec and 0 <= zone - 1 < len(uses_sec) else False
    key_a = "ColorA2" if sec else "ColorA"
    key_b = "ColorB2" if sec else "ColorB"
    key_c = "ColorC2" if sec else "ColorC"
    nd_a = nodes.get(key_a) or nodes.get("ColorA")
    nd_b = nodes.get(key_b) or nodes.get("ColorB")
    nd_c = nodes.get(key_c) or nodes.get("ColorC")
    if nd_a is None or nd_b is None or nd_c is None:
        return None
    overlay_nd = nodes.get(f"{zone}_BaseColorOverlay")
    try:
        gtree = _gt_pbr.build_gt_colour_n_zone_group(
            zone=zone,
            amt=amt,
            swatch=swatch_nd,
            color_a=nd_a,
            color_b=nd_b,
            color_c=nd_c,
            overlay=overlay_nd if overlay_nd is not None else (colours or {}).get(f"{zone}_BaseColorOverlay"),
            tree_key=mat_key,
            secondary=sec,
        )
    except Exception:
        return None
    if gtree is None:
        return None
    g = nodes.new("ShaderNodeGroup")
    g.node_tree = gtree
    g.name = f"GT_ColourN_z{zone}"
    g.label = f"Colour {zone} ({'A2' if sec else 'A'})"
    _gt_attach_local(g, row_parent, loc("fac"))
    if cm_src is not None and getattr(cm_src, "outputs", None):
        try:
            cm_col = cm_src.outputs["Color"]
        except Exception:
            cm_col = cm_src.outputs[0]
        try:
            links.new(cm_col, g.inputs["ColorMask"])
        except Exception:
            pass
    try:
        return g.outputs["Colour"]
    except Exception:
        return g.outputs[0] if g.outputs else None


def _wire_outfit_color_ground_truth(
    *,
    nodes,
    links,
    group_node,
    colours: dict,
    cm_groups: dict,
    cm_src,
    zone_scalars: dict,
    routing_mode: str,
    ta_ids: dict | None = None,
    colortex_pngs=None,
    colortex_pngs_fallback=None,
    bypass_arc: bool = False,
    base_tex=None,
    uses_secondary: list | None = None,
    mat_key: str = "mat",
) -> dict:
    """Cooked assemble on Arc Colour N (D031/D034/D043).

    Role split (critical — do not conflate):
      • OCM MaterialID mid×8 → which **Colour 1..8** zone is active.
      • ColorMask × ColorMaskSwatch × amt → wA/wB/wC.
      • Per-zone ColorA/B/C **or** ColorA2/B2/C2 from SecondaryMask / palette
        ``uses_secondary`` (Snooper ``ArcUsesSecondary``). Not BaseColor.r.
      • ``col = lerp(1, OutA, wA) → OutB wB → OutC wC``.
      • ``N_BaseColorOverlay`` multiplies into Colour N.
        ColorTex: ID≥1 only.

    ColorMask_XYZ is legacy albedo only.

    ``bypass_arc``: no ArcTexturer group. Colour 1..8 are columns; rows are
    process steps (top→bottom), matching the decal grid.
    """
    hide = not bypass_arc
    # Legacy ArcTexturer path still uses stage columns inside one scheme frame.
    _COL = {
        "swatch": 0.0,
        "fac": 300.0,
        "amt": 600.0,
        "amt_mul": 750.0,
        "sep": 900.0,
        "a": 1200.0,
        "ab": 1500.0,
        "c": 1800.0,
        "ov": 2100.0,
        "ov_mul": 2250.0,
        "tex_map": 2400.0,
        "tex": 2550.0,
        "tex_blend": 2700.0,
        "tex_mul": 2850.0,
        "pat": 3100.0,
        "wet": 4300.0,
    }
    _STAGE = {
        "swatch": "mux_col",
        "fac": "mux_col",
        "amt": "mux_col",
        "amt_mul": "mux_col",
        "sep": "mux_col",
        "a": "mux_col",
        "ab": "mux_col",
        "c": "mux_col",
        "ov": "mux_col",
        "ov_mul": "mux_col",
        "tex_map": "colortex_sample",
        "tex": "colortex_sample",
        "tex_blend": "colortex_blend",
        "tex_mul": "colortex_blend",
        "pat": "pattern",
        "wet": "wet",
    }
    ensured = _ensure_core_scheme_colour_nodes(nodes, colours)
    strengths = palette_calibration.extract_base_color_mask_strengths(zone_scalars)
    mask_bits = palette_calibration.extract_layer_mask_bits(zone_scalars)
    if base_tex is None:
        base_tex = _find_basecolor_tex_for_arc(nodes, group_node)
    pattern_images = {}
    hue_cache = {}

    scheme_frame = nodes.new("NodeFrame")
    scheme_frame.label = (
        "GT shared: per-zone ColorA/B/C vs A2 (SecondaryMask) + canvas white"
        if bypass_arc
        else (
            "GT: zone primary|secondary ABC → fac=amt×ColorMask×Swatch → lerp(1,A,fac.r)→B→C "
            "→ ×Overlay ×ColorTex (OCM/MID picks Colour N)"
        )
    )
    scheme_frame.label_size = 14
    if bypass_arc:
        # Shirt 003: shared ABC/canvas cluster sits above the Colour columns (not UV maps).
        scheme_frame.location = (_gt_pbr.colour_col_x(4), 727.0)

    sep = None
    if cm_src is not None and not bypass_arc:
        try:
            sep = nodes.new("ShaderNodeSeparateColor")
        except Exception:
            sep = None
        if sep is None:
            try:
                sep = nodes.new("ShaderNodeSeparateRGB")
            except Exception:
                sep = None
        if sep is not None:
            sep.label = "GT ColorMask.rgb splitter (×Swatch → wA/wB/wC; not XYZ G/B lerp)"
            sep.name = "GT_ColorMask_Separate"
            sep.location = (-1900.0, 520.0) if not bypass_arc else (-200.0, 80.0)
            sep.hide = False
            sep.parent = scheme_frame
            try:
                links.new(cm_src.outputs["Color"], sep.inputs[0])
            except Exception:
                if cm_src.outputs:
                    try:
                        links.new(cm_src.outputs[0], sep.inputs[0])
                    except Exception:
                        pass

    uses_sec = list(uses_secondary or [False] * 8)
    scheme = {}
    a2_linked = []
    for out_key, pri_key, sec_key in (
        ("OutA", "ColorA", "ColorA2"),
        ("OutB", "ColorB", "ColorB2"),
        ("OutC", "ColorC", "ColorC2"),
    ):
        pri_nd = nodes.get(pri_key)
        if not pri_nd:
            continue
        scheme[out_key] = pri_nd
        if nodes.get(sec_key) is not None:
            a2_linked.append(sec_key)

    # Shared white endpoint for D039 canvas lerps (one RGB node).
    # Needed as Mix A for lerp(1, ColorTex, strength) and parent-graph assemble lerps.
    white_nd = nodes.new("ShaderNodeRGB")
    white_nd.name = "GT_canvas_white"
    white_nd.label = "GT canvas white (D039)"
    white_nd.outputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
    # Shirt 003: local to the shared frame, just under the frame label.
    white_nd.location = (-1600.0, 560.0) if not bypass_arc else (30.0, -30.0)
    white_nd.hide = hide
    white_nd.parent = scheme_frame

    zones_wired = []
    zone_outputs = {}
    overlay_muls = 0
    colortex_wired = []
    colortex_uv = None
    colortex_images = {}
    png_lists = tuple(
        lst for lst in (colortex_pngs, colortex_pngs_fallback) if lst
    )

    for zone in range(1, 9):
        colour_sock = f"Colour {zone}"
        if bypass_arc:
            if nodes.get("ColorA") is None:
                continue
        else:
            if group_node is None or colour_sock not in group_node.inputs:
                continue
        if nodes.get("ColorA") is None or nodes.get("ColorB") is None or nodes.get("ColorC") is None:
            continue

        if bypass_arc:
            row_parent = nodes.new("NodeFrame")
            row_parent.label = f"Colour {zone}"
            row_parent.label_size = 16
            _gt_pbr.place(
                row_parent,
                _gt_pbr.colour_col_x(zone),
                _gt_pbr.colour_group_row_y(),
                lock=True,
            )
            y0 = 0.0

            def loc(col, dy=0.0):
                stage = _STAGE.get(col, "mux_col")
                local_y = _gt_pbr.colour_row_y(stage) - _gt_pbr.colour_group_row_y()
                return (0.0, local_y + dy)
        else:
            row_parent = scheme_frame
            y0 = 120.0 - zone * 90.0

            def loc(col, dy=0.0):
                legacy = {
                    "swatch": (-1550.0, -40.0 - zone * 20.0 + dy),
                    "fac": (-1480.0, y0 + dy),
                    "amt": (-1400.0, y0 + 36.0 + dy),
                    "amt_mul": (-1320.0, y0 + dy),
                    "sep": (-1180.0, y0 + dy),
                    "a": (-1080.0, y0 + dy),
                    "ab": (-940.0, y0 + dy),
                    "c": (-800.0, y0 + dy),
                    "ov": (-720.0, y0 + dy),
                    "ov_mul": (-660.0, y0 + dy),
                    "tex_map": (-580.0, y0 + 40.0 + dy),
                    "tex": (-480.0, y0 + dy),
                    "tex_blend": (-380.0, y0 + dy),
                    "tex_mul": (-280.0, y0 + dy),
                }
                return legacy.get(col, (0.0, y0 + dy))

        # amt = BaseColorMaskStrength only (default 1). Do not LayerMask-gate here.
        strength = float(strengths.get(zone, 1.0))
        amt = max(0.0, min(1.0, strength))

        swatch_key = f"{zone}_ColorMaskSwatch"
        swatch_rgba = (colours or {}).get(swatch_key)
        swatch_nd = nodes.get(swatch_key)
        if swatch_nd is None and not bypass_arc:
            swatch_nd = nodes.new("ShaderNodeRGB")
            swatch_nd.label = swatch_key
            swatch_nd.name = swatch_key
            if swatch_rgba is not None:
                swatch_nd.outputs[0].default_value = (
                    float(swatch_rgba[0]),
                    float(swatch_rgba[1]),
                    float(swatch_rgba[2]),
                    float(swatch_rgba[3]) if len(swatch_rgba) > 3 else 1.0,
                )
            else:
                swatch_nd.outputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
            _gt_attach_local(swatch_nd, row_parent, loc("swatch"), hide=hide)
        elif bypass_arc and swatch_nd is not None:
            _gt_attach_local(swatch_nd, row_parent, loc("swatch"), hide=hide)

        colour_src = None
        if bypass_arc:
            colour_src = _gt_wire_colour_n_group(
                nodes, links, row_parent, loc, zone, amt,
                swatch_nd if swatch_nd is not None else swatch_rgba,
                cm_src, colours or {}, uses_sec,
                mat_key=mat_key,
            )
        if colour_src is None:
            # wABC = ColorMask.rgb × ColorMaskSwatch (Snooper ColorMaskWeights, gate≈amt).
            # Do not put BaseColor in this product — that hid the mask. Missing ColorMask → white.
            fac_bs = _new_mix_multiply(
                nodes,
                name=f"GT_fac_MaskSw_z{zone}",
                label=f"GT w0=ColorMask×Swatch z{zone}",
                location=loc("fac"),
                hide=hide,
            )
            _gt_attach_local(fac_bs, row_parent, loc("fac"), hide=hide)
            swatch_out = swatch_nd.outputs[0]
            if cm_src is not None and getattr(cm_src, "outputs", None):
                try:
                    cm_col = cm_src.outputs["Color"]
                except Exception:
                    cm_col = cm_src.outputs[0]
                _gt_mix_link(links, fac_bs, cm_col, swatch_out, fac_value=1.0)
            else:
                _gt_mix_link(links, fac_bs, swatch_out, swatch_out, fac_value=1.0)
            _f, _a, _b, fac_col = _gt_pbr.mix_io(fac_bs)
            if fac_col is None:
                fac_col = fac_bs.outputs[0]
            if amt < 0.999:
                amt_nd = nodes.new("ShaderNodeRGB")
                amt_nd.name = f"GT_amt_z{zone}"
                amt_nd.label = f"GT amt={amt:.3f} z{zone}"
                amt_nd.outputs[0].default_value = (amt, amt, amt, 1.0)
                _gt_attach_local(amt_nd, row_parent, loc("amt"), hide=hide)
                fac_amt = _new_mix_multiply(
                    nodes,
                    name=f"GT_fac_Amt_z{zone}",
                    label=f"GT w=amt({amt:.3f})×ColorMask×Swatch z{zone}",
                    location=loc("amt_mul"),
                    hide=hide,
                )
                _gt_attach_local(fac_amt, row_parent, loc("amt_mul"), hide=hide)
                _gt_mix_link(links, fac_amt, fac_col, amt_nd.outputs[0], fac_value=1.0)
                _f, _a, _b, fac_col = _gt_pbr.mix_io(fac_amt)
                if fac_col is None:
                    fac_col = fac_amt.outputs[0]

            fac_sep = nodes.new("ShaderNodeSeparateColor")
            fac_sep.label = f"GT wA/wB/wC z{zone}"
            fac_sep.name = f"GT_fac_sep_z{zone}"
            _gt_attach_local(fac_sep, row_parent, loc("sep"), hide=hide)
            links.new(fac_col, fac_sep.inputs[0])

            mix_a = _new_mix_lerp_white(
                nodes,
                name=f"GT_assemble_A_z{zone}",
                label=f"GT lerp(1,OutA,fac.r) z{zone}",
                location=loc("a"),
                hide=hide,
            )
            _gt_attach_local(mix_a, row_parent, loc("a"), hide=hide)
            _link_mix_factor_from_sep(links, fac_sep, mix_a, channel="Red", index=0)
            canvas_sock = white_nd.outputs[0]
            sec = bool(uses_sec[zone - 1]) if 0 <= zone - 1 < len(uses_sec) else False
            nd_a = nodes.get("ColorA2" if sec else "ColorA") or nodes.get("ColorA")
            nd_b = nodes.get("ColorB2" if sec else "ColorB") or nodes.get("ColorB")
            nd_c = nodes.get("ColorC2" if sec else "ColorC") or nodes.get("ColorC")
            if nd_a is None or nd_b is None or nd_c is None:
                continue
            scheme_a = nd_a.outputs[0]
            _gt_mix_link(links, mix_a, canvas_sock, scheme_a)

            mix_ab = nodes.new("ShaderNodeMix")
            mix_ab.data_type = "RGBA"
            mix_ab.blend_type = "MIX"
            mix_ab.name = f"GT_assemble_AB_z{zone}"
            mix_ab.label = f"GT lerp(col,OutB,fac.g) z{zone}"
            mix_ab.hide = hide
            _gt_attach_local(mix_ab, row_parent, loc("ab"), hide=hide)
            _link_mix_factor_from_sep(links, fac_sep, mix_ab, channel="Green", index=1)
            mix_a_out = _gt_pbr.mix_io(mix_a)[3] or mix_a.outputs[0]
            scheme_b = nd_b.outputs[0]
            _gt_mix_link(links, mix_ab, mix_a_out, scheme_b)

            mix_c = nodes.new("ShaderNodeMix")
            mix_c.data_type = "RGBA"
            mix_c.blend_type = "MIX"
            mix_c.name = f"GT_assemble_C_z{zone}"
            mix_c.label = f"GT lerp(col,OutC,fac.b) z{zone}"
            mix_c.hide = hide
            _gt_attach_local(mix_c, row_parent, loc("c"), hide=hide)
            _link_mix_factor_from_sep(links, fac_sep, mix_c, channel="Blue", index=2)
            mix_ab_out = _gt_pbr.mix_io(mix_ab)[3] or mix_ab.outputs[0]
            scheme_c = nd_c.outputs[0]
            colour_src = _gt_mix_link(links, mix_c, mix_ab_out, scheme_c)

            # D034: col *= BaseColorOverlay (Fac Overlay stays 0 elsewhere).
            overlay_nd = nodes.get(f"{zone}_BaseColorOverlay")
            if overlay_nd is None:
                ov_rgba = (colours or {}).get(f"{zone}_BaseColorOverlay")
                if ov_rgba is not None:
                    overlay_nd = nodes.new("ShaderNodeRGB")
                    overlay_nd.label = f"{zone}_BaseColorOverlay"
                    overlay_nd.name = f"{zone}_BaseColorOverlay"
                    overlay_nd.outputs[0].default_value = (
                        float(ov_rgba[0]),
                        float(ov_rgba[1]),
                        float(ov_rgba[2]),
                        float(ov_rgba[3]) if len(ov_rgba) > 3 else 1.0,
                    )
                    _gt_attach_local(overlay_nd, row_parent, loc("ov"), hide=hide)

            if not bypass_arc:
                _unlink_colour_socket(links, group_node, zone)
            if overlay_nd is not None:
                mul_ov = _new_mix_multiply(
                    nodes,
                    name=f"GT_overlay_mul_z{zone}",
                    label=f"GT Colour×Overlay z{zone}",
                    location=loc("ov_mul"),
                    hide=hide,
                )
                _gt_attach_local(mul_ov, row_parent, loc("ov_mul"), hide=hide)
                colour_src = _gt_mix_link(links, mul_ov, colour_src, overlay_nd.outputs[0], fac_value=1.0)
                overlay_muls += 1

        # D043: col *= lerp(1, ColorTex, sat(strength)) when ID>=1. Never slice 0.
        colortex_id = _mi_layer_float(ta_ids, zone, "ColorTextureID", 0.0)
        str_base = max(0.0, min(1.0, _mi_layer_float(zone_scalars, zone, "BaseTextureStrength", 0.0)))
        str_edge = max(0.0, min(1.0, _mi_layer_float(zone_scalars, zone, "EdgeTextureStrength", 0.0)))
        str_crease = max(0.0, min(1.0, _mi_layer_float(zone_scalars, zone, "CreaseTextureStrength", 0.0)))
        tiling = _mi_layer_float(zone_scalars, zone, "ColorTextureTiling", 1.0)
        if tiling <= 1e-6:
            tiling = 1.0
        stages = (
            ("base", str_base),
            ("edge", str_edge),
            ("crease", str_crease),
        )
        live_strength = any(s > 1e-6 for _, s in stages)
        if colortex_id >= 1.0 and live_strength and png_lists:
            png_path = _find_colortex_slice_png(png_lists, colortex_id)
            img = colortex_images.get(png_path) if png_path else None
            if img is None and png_path:
                img = _load_image_cached(png_path)
                if img is not None:
                    try:
                        img.colorspace_settings.name = "sRGB"
                    except Exception:
                        pass
                    colortex_images[png_path] = img
            if img is not None:
                if colortex_uv is None:
                    colortex_uv = nodes.new("ShaderNodeTexCoord")
                    colortex_uv.name = "GT_ColorTex_UV"
                    colortex_uv.label = "GT ColorTex UV"
                    colortex_uv.location = (
                        (_gt_pbr.colour_col_x(zone) - _gt_pbr._GT_GRID_DX, _gt_pbr.shared_row_y("colortex_sample"))
                        if bypass_arc
                        else (-1080.0, 620.0)
                    )
                    colortex_uv.hide = hide
                    colortex_uv.parent = None if bypass_arc else scheme_frame
                mapping = nodes.new("ShaderNodeMapping")
                mapping.name = f"GT_ColorTex_map_z{zone}"
                mapping.label = f"GT ColorTex tile={tiling:.3f} z{zone}"
                mapping.hide = True
                _gt_attach_local(mapping, row_parent, loc("tex_map"), hide=True)
                mapping.inputs["Scale"].default_value = (tiling, tiling, tiling)
                links.new(colortex_uv.outputs["UV"], mapping.inputs["Vector"])
                tex_nd = nodes.new("ShaderNodeTexImage")
                tex_nd.name = f"GT_ColorTex_z{zone}"
                tex_nd.label = os.path.basename(png_path)
                tex_nd.image = img
                tex_nd.interpolation = "Cubic"
                _gt_attach_local(tex_nd, row_parent, loc("tex"), hide=False if bypass_arc else hide)
                links.new(mapping.outputs["Vector"], tex_nd.inputs["Vector"])

                tex_src = white_nd.outputs[0]
                for stage_name, stage_str in stages:
                    if stage_str <= 1e-6:
                        continue
                    blend = _new_mix_lerp_white(
                        nodes,
                        name=f"GT_ColorTex_{stage_name}_z{zone}",
                        label=f"GT lerp(tex,ColorTex,{stage_name}={stage_str:.3f}) z{zone}",
                        location=loc("tex_blend"),
                        hide=True if bypass_arc else hide,
                    )
                    _gt_attach_local(blend, row_parent, loc("tex_blend"), hide=True if bypass_arc else hide)
                    try:
                        fac_sock = blend.inputs.get("Factor") or blend.inputs[0]
                        fac_sock.default_value = float(stage_str)
                    except Exception:
                        pass
                    tex_src = _gt_mix_link(links, blend, tex_src, tex_nd.outputs["Color"])
                tex_mul = _new_mix_multiply(
                    nodes,
                    name=f"GT_ColorTex_mul_z{zone}",
                    label=f"GT ×ColorTex ID={int(round(colortex_id))} z{zone}",
                    location=loc("tex_mul"),
                    hide=True if bypass_arc else hide,
                )
                _gt_attach_local(tex_mul, row_parent, loc("tex_mul"), hide=True if bypass_arc else hide)
                colour_src = _gt_mix_link(links, tex_mul, colour_src, tex_src, fac_value=1.0)
                colortex_wired.append(zone)

        _pat_y = loc("pat")[1] if bypass_arc else y0
        colour_src = _wire_gt_zone_pattern_hue(
            nodes,
            links,
            row_parent,
            colours or {},
            zone_scalars,
            ta_ids,
            zone,
            colour_src,
            _pat_y,
            png_lists,
            pattern_images,
            hue_cache,
            col_x=0.0 if bypass_arc else None,
            hide=True if bypass_arc else hide,
        )

        _wet_y = loc("wet")[1] if bypass_arc else y0
        colour_src = _wire_gt_zone_wet(
            nodes,
            links,
            row_parent,
            colours or {},
            zone_scalars,
            zone,
            colour_src,
            _wet_y,
            col_x=0.0 if bypass_arc else None,
            hide=True if bypass_arc else hide,
        )

        if bypass_arc:
            zone_outputs[zone] = colour_src
        else:
            links.new(colour_src, group_node.inputs[colour_sock])
        zones_wired.append(zone)

    return {
        "zones": zones_wired,
        "layer_mask_bits": mask_bits,
        "strengths": strengths,
        "routing_mode": routing_mode,
        "blur": palette_calibration.BLURRY_CURVATURE_STATUS,
        "scheme_a2_linked": a2_linked,
        "scheme_nodes_ensured": ensured.get("created") or [],
        "scheme_fac_from_basecolor_r": False,
        "scheme_fac_from_colormask_r": False,
        "uses_secondary": uses_sec,
        "colormask_in_weights": cm_src is not None,
        "assemble_mode": "cooked_colormask_swatch_wabc_overlay_colortex_pattern_hue_wet",
        "pattern_zones": hue_cache.get("pattern_zones") or [],
        "hue_zones": hue_cache.get("hue_zones") or [],
        "basecolor_on_colour_n": False,
        "basecolor_in_fac": False,
        "base_tex_present": base_tex is not None,
        "overlay_muls": overlay_muls,
        "colortex_deferred": False,
        "colortex_zones": colortex_wired,
        "xyz_as_abc": False,
        "cm_groups_present": bool(cm_groups),
        "zone_outputs": zone_outputs,
        "bypass_arc": bypass_arc,
    }


def _wire_outfit_color_legacy(
    *,
    nodes,
    links,
    cm_groups: dict,
    colour_inputs: dict,
    csb_soft: dict,
) -> list[int]:
    """Pre-D022 path: ColorMask_XYZ G/B assemble + optional D010 soft Mix.

    Kept intact for ``arc_outfit_color_pipeline=legacy`` revert.
    """
    soft_zones: list[int] = []
    for zone_key, input_map in colour_inputs.items():
        cm_grp = cm_groups.get(int(zone_key)) if zone_key is not None else None
        if not cm_grp:
            continue
        zone_i = int(zone_key)
        mix_fac = csb_soft.get(zone_i)
        if mix_fac is not None:
            _pri_sec = (
                ("X_Green", "ColorA", "ColorA2"),
                ("Y_Blue", "ColorB", "ColorB2"),
                ("Z_Pink", "ColorC", "ColorC2"),
            )
            for sock_i, (socket_name, pri_key, sec_key) in enumerate(_pri_sec):
                if socket_name not in cm_grp.inputs:
                    continue
                pri_nd = nodes.get(pri_key)
                sec_nd = nodes.get(sec_key)
                if not pri_nd:
                    continue
                if not sec_nd:
                    links.new(pri_nd.outputs[0], cm_grp.inputs[socket_name])
                    continue
                mix = nodes.new("ShaderNodeMix")
                mix.data_type = "RGBA"
                mix.blend_type = "MIX"
                mix.label = f"ColorSchemeBlend z{zone_i} {pri_key}↔{sec_key}"
                mix.name = f"CSB_z{zone_i}_{pri_key}"
                try:
                    mix.inputs[0].default_value = float(mix_fac)
                except Exception:
                    pass
                mix.location = (
                    cm_grp.location[0] - 280.0,
                    cm_grp.location[1] - sock_i * 60.0,
                )
                mix.hide = True
                links.new(pri_nd.outputs[0], mix.inputs[6])
                links.new(sec_nd.outputs[0], mix.inputs[7])
                links.new(mix.outputs[2], cm_grp.inputs[socket_name])
            soft_zones.append(zone_i)
        else:
            for socket_name, colour_key in input_map.items():
                rgb_nd = nodes.get(colour_key)
                if rgb_nd and socket_name in cm_grp.inputs:
                    sock = cm_grp.inputs[socket_name]
                    while getattr(sock, "is_linked", False):
                        try:
                            links.remove(sock.links[0])
                        except Exception:
                            break
                    links.new(rgb_nd.outputs[0], sock)
    return soft_zones


def apply_palette_routing_live(mat, mode: str, *, obj=None) -> int:
    """Rewire ColorMask_XYZ X/Y/Z to ColorA/B/C vs A2 for ``mode`` without full rebuild.

    Returns number of zone sockets rewired. Soft ColorSchemeBlend Mix nodes (rare)
    are bypassed: hard primary/secondary/swap/auto map wins for live probing.

    Ground-truth materials own Colour N via GT assemble — live XYZ rewire is
    skipped; use Update Materials after changing pipeline / palette.
    """
    if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return 0
    try:
        pipe = palette_calibration.parse_outfit_color_pipeline(
            mat.get("arc_outfit_color_pipeline", "")
        )
        if pipe == palette_calibration.OUTFIT_COLOR_PIPELINE_GROUND_TRUTH:
            return 0
    except Exception:
        pass
    mode = palette_calibration.parse_mode(mode)
    inputs_map = palette_calibration.section_colour_inputs(mode)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    rewired = 0
    for node in nodes:
        if getattr(node, "bl_idname", "") != "ShaderNodeGroup":
            continue
        ng = getattr(node, "node_tree", None)
        tname = getattr(ng, "name", "") or ""
        if not tname.startswith("ColorMask"):
            continue
        lab = (getattr(node, "label", "") or "").strip()
        m = _CM_ZONE_LABEL_RE.search(lab)
        if not m:
            continue
        try:
            zone = int(m.group(1))
        except (TypeError, ValueError):
            continue
        zone_inputs = inputs_map.get(zone)
        if not zone_inputs:
            continue
        for socket_name, colour_key in zone_inputs.items():
            sock = node.inputs.get(socket_name)
            rgb_nd = nodes.get(colour_key)
            if sock is None or rgb_nd is None:
                continue
            while getattr(sock, "is_linked", False):
                try:
                    links.remove(sock.links[0])
                except Exception:
                    break
            try:
                links.new(rgb_nd.outputs[0], sock)
                rewired += 1
            except Exception:
                pass
    try:
        mat["arc_palette_mode"] = mode
        mat["arc_palette_resolved"] = mode
        mat["arc_palette_source"] = "live"
        if obj is not None:
            obj["arc_palette_mode"] = mode
            obj["arc_palette_resolved"] = mode
    except Exception:
        pass
    return rewired


# Re-export for callers / tests that import from materials.
base_overlay_mix_factor = palette_calibration.base_overlay_mix_factor
overlay_mix_factor = palette_calibration.overlay_mix_factor




# Addon-root reference/ (not materials/reference — __file__ lives under materials/).
_NCT_LAYOUT_INDEX_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "reference", "goalie_shirt_nct_layout_index.json"
))

_NCT_LAYOUT_CACHE = None
_NCT_LAYOUT_MISSING_LOGGED = False



def _load_nct_layout_index() -> dict:
    """Load compact GoalieShirt NCT node positions (cached).

    Empty cache from a prior miss is re-read so a fixed path takes effect without
    restarting Blender.
    """
    global _NCT_LAYOUT_CACHE, _NCT_LAYOUT_MISSING_LOGGED
    if _NCT_LAYOUT_CACHE:
        return _NCT_LAYOUT_CACHE
    path = _NCT_LAYOUT_INDEX_PATH
    if not os.path.isfile(path):
        if not _NCT_LAYOUT_MISSING_LOGGED:
            print(f"Arc Raiders PSK Importer: NCT layout index missing: {path}")
            _NCT_LAYOUT_MISSING_LOGGED = True
        _NCT_LAYOUT_CACHE = {}
        return _NCT_LAYOUT_CACHE
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        print(f"Arc Raiders PSK Importer: NCT layout index unreadable ({exc})")
        _NCT_LAYOUT_CACHE = {}
        return _NCT_LAYOUT_CACHE
    by_key = {}
    for entry in data.get("nodes") or []:
        key = entry.get("key")
        if key and key not in by_key:
            by_key[key] = entry
    _NCT_LAYOUT_CACHE = by_key
    if not by_key and not _NCT_LAYOUT_MISSING_LOGGED:
        print(f"Arc Raiders PSK Importer: NCT layout index empty: {path}")
        _NCT_LAYOUT_MISSING_LOGGED = True
    return _NCT_LAYOUT_CACHE



def _nct_outgoing_by_node(tree) -> dict:
    """Build from_node → [to_node, ...] once (avoids O(links) scans per node)."""
    out: dict = {}
    if tree is None:
        return out
    links = getattr(tree, "links", None)
    if not links:
        return out
    for lnk in links:
        out.setdefault(lnk.from_node, []).append(lnk.to_node)
    return out



def _nct_tree_from_nodes(nodes):
    tree = getattr(nodes, "id_data", None)
    if tree is not None:
        return tree
    for n in nodes:
        tree = getattr(n, "id_data", None)
        if tree is not None:
            return tree
    return None



def _nct_ta_uv_chain_role(node, which: str, outgoing=None):
    """Classify TA TexCoord/Mapping as normal vs mask UV by downstream image labels.

    ``which`` is ``\"uv\"`` (TexCoord → Mapping → images) or ``\"mapping\"``
    (Mapping → images). Returns a role key or None.
    """
    if outgoing is None:
        tree = getattr(node, "id_data", None)
        if tree is None:
            return None
        outgoing = _nct_outgoing_by_node(tree)
    if which == "uv":
        mids = outgoing.get(node) or ()
        images = []
        for mid in mids:
            images.extend(outgoing.get(mid) or ())
    else:
        images = list(outgoing.get(node) or ())
    if not images:
        return None
    saw_normal = False
    saw_mask = False
    for img in images:
        lab = (
            (getattr(img, "label", None) or "")
            + " "
            + (
                os.path.basename(
                    getattr(getattr(img, "image", None), "filepath", "")
                    or getattr(getattr(img, "image", None), "name", "")
                    or ""
                )
            )
        ).lower()
        if "normals_" in lab:
            saw_normal = True
        if "masks_" in lab:
            saw_mask = True
    if saw_normal and not saw_mask:
        return "role:TANormalUV" if which == "uv" else "role:TANormalMapping"
    if saw_mask and not saw_normal:
        return "role:TAMaskUV" if which == "uv" else "role:TAMaskMapping"
    return None



def _iter_nct_layout_keys(node, outgoing=None):
    """Yield layout-index keys that may match a built clothing node."""
    label = (getattr(node, "label", None) or "").strip()
    ntype = getattr(node, "bl_idname", "") or ""
    tree = getattr(node, "node_tree", None)
    tree_name = getattr(tree, "name", "") if tree is not None else ""

    if ntype == "ShaderNodeGroup" and tree_name:
        if tree_name.replace(" ", "") == "ArcTexturer" or tree_name.startswith("ArcTexturer"):
            yield "role:ArcTexturer"
        if tree_name.startswith("ColorMask_XYZ") and label.startswith("ColorMask_XYZ (zone "):
            yield f"label:{label}"
        if (tree_name == "Decal Data" or tree_name.startswith("Decal Data")) and re.match(
            r"^Decal \d+ Data$", label
        ):
            yield f"label:{label}"

    if ntype == "ShaderNodeOutputMaterial":
        yield "role:MaterialOutput"

    # Dual TA UV chains (normals near Arc, masks near TA masks) — prefer roles
    # over the shared "Texture Coordinate" / "Mapping" labels.
    if ntype == "ShaderNodeTexCoord" and label == "Texture Coordinate":
        role = _nct_ta_uv_chain_role(node, "uv", outgoing=outgoing)
        if role:
            yield role
    if ntype == "ShaderNodeMapping" and label == "Mapping":
        role = _nct_ta_uv_chain_role(node, "mapping", outgoing=outgoing)
        if role:
            yield role

    if label:
        yield f"label:{label}"
        m = re.match(r"^(Decal \d+) LayerMask \(\d+\)$", label)
        if m:
            yield f"label:{m.group(1)} LayerMask"
        m = re.match(r"^(Decal \d+):\s", label)
        if m:
            yield f"label:{m.group(1)} ColorTex"
        m = re.match(r"^(Decal \d+) (?:Data|Normal):\s", label)
        if m:
            yield f"label:{m.group(1)} DataTex"
        m = re.match(r"^(Decal \d+)\s+R=", label)
        if m:
            yield f"label:{m.group(1)} UVMap"
        m = re.match(r"^Base Overlay .+ zone (\d+)$", label)
        if m:
            yield f"label:Base Overlay \u2194 XYZ zone {m.group(1)}"

    if ntype == "ShaderNodeTexImage":
        fname = label
        if not fname and getattr(node, "image", None) is not None:
            fname = os.path.basename(node.image.filepath or node.image.name or "")
        low = (fname or "").lower()
        if low:
            if "occlusion" in low or "curvature" in low or "materialid" in low:
                yield "role:MainOcclusion"
            if "colormask" in low:
                yield "role:MainColorMask"
            if "basecolor" in low:
                yield "role:MainBaseColor"
            if re.search(r"(^|_)normal", low) and "normals_" not in low:
                yield "role:MainNormal"
            # TA roles only for Texture Array files (TA_*Masks_N / TA_*Normals_N).
            # Never match ColorMask / ColorABC / other "*Color*" textures here.
            base = os.path.basename(fname or "").upper()
            if base.startswith("TA_"):
                m = re.search(r"masks_(\d+)\.png$", low)
                if m:
                    yield f"role:TAMask:{m.group(1)}"
                m = re.search(r"normals_(\d+)\.png$", low)
                if m:
                    yield f"role:TANormal:{m.group(1)}"



def apply_nct_clothing_layout(nodes) -> int:
    """Place clothing nodes using the GoalieShirt NodeConnectionTest layout.

    Returns the number of nodes positioned. Callers should skip generic padding
    when this returns a meaningful count — padding would destroy the NCT layout.
    """
    by_key = _load_nct_layout_index()
    if not by_key:
        return 0

    tree = _nct_tree_from_nodes(nodes)
    outgoing = _nct_outgoing_by_node(tree)
    placed = 0
    for node in list(nodes):
        if getattr(node, "bl_idname", "") == "NodeFrame" or getattr(node, "type", "") == "FRAME":
            continue
        keys = list(_iter_nct_layout_keys(node, outgoing=outgoing))
        # TA normals use Mix-slot placement (place_ta_normals_by_first_link);
        # slice-index / label NCT entries must not pin them to the wrong row.
        if any(k.startswith("role:TANormal:") for k in keys):
            continue
        # ColorMask-adjacent TA_* feeds use place_ta_colormask_by_first_link.
        # ColorABC / overlays are not TA nodes and keep NCT absolute coords.
        if _ta_colormask_zone_from_first_link(node, tree) is not None:
            continue
        # Decal normals: first DN N link (NCT has DataTex for 2–5 only — Decal 1
        # would otherwise stay on the Decals frame at builder (1200, 350)).
        if any(k.endswith(" DataTex") for k in keys) or _decal_normal_slot_from_first_link(node, tree) is not None:
            continue
        # Crease/Edge ColorOverlay RGB only (never skip ColorMask_XYZ / others that
        # might briefly share a Crease/Edge link — that wrecked NCT placement).
        label = (getattr(node, "label", "") or "").strip()
        if "_CreaseColorOverlay" in label or "_EdgeColorOverlay" in label:
            continue
        # BaseColorOverlay: first Overlay N link (NCT only had zones 1–5; socket-Y
        # fallback parked 6+ on the Colour 5 row).
        if _base_overlay_slot_from_first_link(node, tree) is not None:
            continue
        # MI scalar/vector params: column-by-number grid from place_mi_parameter_nodes.
        # NCT still has Goalie's old JSON-order coords — do not re-scatter them.
        if getattr(node, "bl_idname", "") in ("ShaderNodeValue", "ShaderNodeRGB") and _MI_PARAM_NAME_RE.match(
            label
        ):
            continue
        if "_BaseColorOverlay" in label:
            continue
        entry = None
        for key in keys:
            entry = by_key.get(key)
            if entry is not None:
                break
        if entry is None:
            continue
        loc = entry.get("location") or [0.0, 0.0]
        try:
            node.parent = None
        except Exception:
            pass
        try:
            node.location = (float(loc[0]), float(loc[1]))
        except Exception:
            continue
        if "hide" in entry:
            try:
                node.hide = bool(entry["hide"])
            except Exception:
                pass
        if entry.get("width"):
            try:
                node.width = float(entry["width"])
            except Exception:
                pass
        if hasattr(node, "hide_preview"):
            try:
                node.hide_preview = True
            except Exception:
                pass
        placed += 1

    # NCT dump had no frames; drop empty organizer frames after unparenting.
    # One parent-set pass — avoid O(frames × nodes) membership scans.
    parents = {getattr(n, "parent", None) for n in nodes}
    parents.discard(None)
    for node in list(nodes):
        if getattr(node, "bl_idname", "") != "NodeFrame" and getattr(node, "type", "") != "FRAME":
            continue
        if node not in parents:
            try:
                nodes.remove(node)
            except Exception:
                pass
    return placed



# TA normals sit just left of BaseColorOverlay, NOT the mask column.
# Slot 1–9 coords captured from SK_Goalie_UpperBody Mix 1–9 placeholder normals.
_TA_NORMAL_COL_X = -440.0

_TA_NORMAL_UV_LOC = (-876.0, 71.0)

_TA_NORMAL_MAPPING_LOC = (-674.0, 77.0)

_TA_MASK_COL_X = -4359.54  # masks only — do not place normals here

_TA_NORMAL_SLOT_RE = re.compile(
    r"^(?:Mix|Medium Normal|Edge Normal|Crease Normal)\s+(\d+)$",
    re.I,
)

# Mix N → (x, y). Placeholders: UpperBody TA normals wired 1:1 to Mix 1–9.
_TA_NORMAL_SLOT_LOCS = {
    1: (-457.0, -506.26),
    2: (-452.32, -754.57),
    3: (-440.92, -996.53),
    4: (-446.67, -1246.10),
    5: (-446.93, -1497.60),
    6: (-434.81, -1745.88),
    7: (-426.16, -1995.47),
    8: (-428.23, -2245.93),
    9: (-427.88, -2497.41),
}

# ColorMask_XYZ zone → (x, y). Captured from SK_Goalie_Pants TA feeds placed
# adjacent to each ColorMask_XYZ (zone N) Roughness_X/Y/Z connection.
_TA_COLORMASK_ZONE_RE = re.compile(r"ColorMask_XYZ\s*\(zone\s*(\d+)\)", re.I)

# TA mask feeds only (Roughness/Metal). ColorABC uses X_Green/Y_Blue/Z_Pink —
# those must never be treated as TA placement targets.
_TA_COLORMASK_FEED_SOCK_RE = re.compile(
    r"^(?:Roughness_[XYZ]|Metal_[XYZ])$",
    re.I,
)

_TA_TEXTURE_NAME_RE = re.compile(r"(?i)^TA_")



def _ta_texture_basename(node) -> str:
    """Basename from image label/filepath for TA_* matching."""
    label = (getattr(node, "label", None) or "").strip()
    if label.lower().endswith((".png", ".tga", ".jpg", ".jpeg", ".exr")):
        return os.path.basename(label)
    img = getattr(node, "image", None)
    if img is not None:
        return os.path.basename(img.filepath or img.name or "")
    return os.path.basename(label)



def _is_ta_texture_node(node) -> bool:
    """True only for Texture Array image nodes (TA_* masks/normals/colors)."""
    if getattr(node, "bl_idname", "") != "ShaderNodeTexImage":
        return False
    if getattr(node, "type", "") == "FRAME":
        return False
    fname = _ta_texture_basename(node)
    return bool(fname) and _TA_TEXTURE_NAME_RE.match(os.path.basename(fname)) is not None

_TA_COLORMASK_ZONE_LOCS = {
    1: (-3607.39, -537.89),
    2: (-3342.95, -798.86),
    3: (-3114.59, -1042.52),
    4: (-2865.84, -1304.82),
    5: (-2597.95, -1578.98),
    6: (-2367.99, -1805.97),
    7: (-2106.69, -2077.15),
    8: (-1855.38, -2341.55),
}


# Decal normals (DN N): same first-link slot table approach as TA Mix normals.
# Slots 2–5 from GoalieShirt NCT DataTex; slot 1 extrapolated (NCT had no Decal 1
# DataTex — ColorTex Y −35, DataTex column X). Collapsed like other DataTex.
_DECAL_NORMAL_COL_X = -167.0

_DECAL_NORMAL_SLOT_RE = re.compile(r"^DN(?:\s+Alpha)?\s+(\d+)$", re.I)

_DECAL_NORMAL_SLOT_LOCS = {
    1: (-167.0, -2685.0),
    2: (-166.65, -2896.79),
    3: (-168.02, -3106.03),
    4: (-170.38, -3309.59),
    5: (-167.14, -3518.96),
}


# Crease/Edge color overlays: placeholders from SK_Goalie_ChestGuard.001 (slots
# present), placed by first Crease N / Edge N Arc link. Collapsed.
_CREASE_EDGE_SOCK_RE = re.compile(r"^(Crease|Edge)\s+(\d+)$", re.I)

_CREASE_COLOR_SLOT_LOCS = {
    1: (66.57, -405.75),
    2: (81.62, -651.63),
    3: (93.34, -903.33),
    4: (99.36, -1149.19),
    5: (98.24, -1406.29),
    6: (94.31, -1654.38),
    7: (89.55, -1898.63),
    8: (86.96, -2153.46),
    9: (89.19, -2403.53),
}

_EDGE_COLOR_SLOT_LOCS = {
    1: (69.42, -441.9),
    2: (81.64, -681.27),
    3: (93.91, -936.86),
    4: (100.01, -1183.34),
    5: (97.78, -1435.0),
    6: (93.88, -1687.01),
    7: (89.02, -1935.21),
    8: (85.03, -2190.88),
    9: (87.97, -2438.1),
}


# BaseColorOverlay RGB → Arc ``Overlay N`` (not ``Overlay Fac N``).
# Zones 1–5: GoalieShirt NCT absolutes. 6–9: same column, ~-249.47 Y/step
# (NCT mean step). Socket-index Y (_arc_input_socket_y) put zone 6 above
# Colour 5 — never use that for overlay rows.
_OVERLAY_COLOR_SOCK_RE = re.compile(r"^Overlay\s+(\d+)$", re.I)

_BASE_OVERLAY_ROW_DY = -249.4725



def _nct_entry_for_keys(keys, layout) -> dict | None:
    """First layout entry matching any key (same preference order as apply)."""
    if not layout:
        return None
    for k in keys:
        entry = layout.get(k)
        if entry is not None:
            return entry
    return None



def _ta_normal_slot_from_first_link(node, tree) -> int | None:
    """Return Mix/zone slot 1–9 from the first outgoing Arc normal link."""
    if tree is None or node is None:
        return None
    links = getattr(tree, "links", None)
    if not links:
        return None
    for lnk in links:
        if lnk.from_node != node:
            continue
        sock = getattr(lnk.to_socket, "name", "") or ""
        m = _TA_NORMAL_SLOT_RE.match(sock.strip())
        if not m:
            continue
        try:
            slot = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if 1 <= slot <= 9:
            return slot
    return None



def _ta_normal_slot_location(slot: int, group_node=None, tree=None) -> tuple[float, float] | None:
    """Slot table coords, or first-link socket Y + column X if a slot is missing."""
    if slot in _TA_NORMAL_SLOT_LOCS:
        x, y = _TA_NORMAL_SLOT_LOCS[slot]
        return (float(x), float(y))
    # Fallback: align to Arc Mix N socket row when a placeholder was not captured.
    if group_node is not None:
        sock = group_node.inputs.get(f"Mix {slot}")
        if sock is not None:
            return (_TA_NORMAL_COL_X, _arc_input_socket_y(group_node, sock))
    return None



def _ta_colormask_zone_from_first_link(node, tree) -> int | None:
    """Return ColorMask_XYZ zone 1–8 from the first outgoing TA_* ColorMask feed.

    Only TA_* image textures that feed Roughness_/Metal_ sockets qualify.
    ColorABC RGB (X_Green/Y_Blue/Z_Pink), ColorMaskSwatch, and overlays are
    ignored so TA placers cannot steal their absolute NCT positions.
    """
    if tree is None or node is None:
        return None
    if not _is_ta_texture_node(node):
        return None
    links = getattr(tree, "links", None)
    if not links:
        return None
    for lnk in links:
        if lnk.from_node != node:
            continue
        to = lnk.to_node
        if getattr(to, "bl_idname", "") != "ShaderNodeGroup":
            continue
        ng = getattr(to, "node_tree", None)
        name = getattr(ng, "name", "") or ""
        if not name.startswith("ColorMask"):
            continue
        sock = (getattr(lnk.to_socket, "name", "") or "").strip()
        if not _TA_COLORMASK_FEED_SOCK_RE.match(sock):
            continue
        label = (getattr(to, "label", "") or "").strip()
        m = _TA_COLORMASK_ZONE_RE.search(label)
        if not m:
            continue
        try:
            zone = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if 1 <= zone <= 8:
            return zone
    return None



def _ta_colormask_zone_location(zone: int, cm_group=None) -> tuple[float, float] | None:
    """Zone table coords, or offset left of the ColorMask group if missing."""
    if zone in _TA_COLORMASK_ZONE_LOCS:
        x, y = _TA_COLORMASK_ZONE_LOCS[zone]
        return (float(x), float(y))
    if cm_group is not None:
        try:
            return (float(cm_group.location.x) - 780.0, float(cm_group.location.y) - 240.0)
        except Exception:
            return None
    return None



def _decal_normal_slot_from_first_link(node, tree) -> int | None:
    """Return DN slot 1–9 from the first outgoing DN / DN Alpha link."""
    if tree is None or node is None:
        return None
    links = getattr(tree, "links", None)
    if not links:
        return None
    for lnk in links:
        if lnk.from_node != node:
            continue
        sock = (getattr(lnk.to_socket, "name", "") or "").strip()
        m = _DECAL_NORMAL_SLOT_RE.match(sock)
        if not m:
            continue
        try:
            slot = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if 1 <= slot <= 9:
            return slot
    return None



def _decal_normal_slot_location(slot: int, group_node=None) -> tuple[float, float] | None:
    """DN slot table coords, or Arc DN N socket row fallback."""
    if slot in _DECAL_NORMAL_SLOT_LOCS:
        x, y = _DECAL_NORMAL_SLOT_LOCS[slot]
        return (float(x), float(y))
    if group_node is not None:
        sock = group_node.inputs.get(f"DN {slot}")
        if sock is not None:
            return (_DECAL_NORMAL_COL_X, _arc_input_socket_y(group_node, sock))
    return None



def _crease_edge_kind_slot_from_first_link(node, tree) -> tuple[str, int] | None:
    """Return ('crease'|'edge', slot) from the first Crease N / Edge N link."""
    if tree is None or node is None:
        return None
    links = getattr(tree, "links", None)
    if not links:
        return None
    for lnk in links:
        if lnk.from_node != node:
            continue
        sock = (getattr(lnk.to_socket, "name", "") or "").strip()
        m = _CREASE_EDGE_SOCK_RE.match(sock)
        if not m:
            continue
        kind = m.group(1).lower()
        try:
            slot = int(m.group(2))
        except (TypeError, ValueError):
            continue
        if 1 <= slot <= 9:
            return (kind, slot)
    return None



def _base_overlay_slot_from_first_link(node, tree) -> int | None:
    """Return Overlay slot 1–9 from the first outgoing Overlay N link.

    Does not match ``Overlay Fac N`` (Fac is the Arc slider, not an RGB feed).
    """
    if tree is None or node is None:
        return None
    links = getattr(tree, "links", None)
    if not links:
        return None
    for lnk in links:
        if lnk.from_node != node:
            continue
        sock = (getattr(lnk.to_socket, "name", "") or "").strip()
        m = _OVERLAY_COLOR_SOCK_RE.match(sock)
        if not m:
            continue
        try:
            slot = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if 1 <= slot <= 9:
            return slot
    return None



def _base_overlay_slot_location(slot: int, group_node=None) -> tuple[float, float] | None:
    """Overlay-N row coords from NCT/extrapolated table, or Arc Overlay N fallback."""
    if slot in _BASE_COLOR_OVERLAY_SLOT_LOCS:
        x, y = _BASE_COLOR_OVERLAY_SLOT_LOCS[slot]
        return (float(x), float(y))
    x5, y5 = _BASE_COLOR_OVERLAY_SLOT_LOCS[5]
    if 1 <= slot <= 9:
        return (float(x5), float(y5 + (slot - 5) * _BASE_OVERLAY_ROW_DY))
    if group_node is not None:
        sock = group_node.inputs.get(f"Overlay {slot}")
        if sock is not None:
            # Last resort only — idx*22 pitch is wrong vs NCT zone rows.
            return (-79.4, _arc_input_socket_y(group_node, sock) + 10.0)
    return None



def _crease_edge_slot_location(kind: str, slot: int, group_node=None) -> tuple[float, float] | None:
    """Crease/Edge placeholder table, or Arc Crease/Edge N socket row fallback."""
    table = _CREASE_COLOR_SLOT_LOCS if kind == "crease" else _EDGE_COLOR_SLOT_LOCS
    if slot in table:
        x, y = table[slot]
        return (float(x), float(y))
    if group_node is not None:
        prefix = "Crease" if kind == "crease" else "Edge"
        sock = group_node.inputs.get(f"{prefix} {slot}")
        if sock is not None:
            return (90.0, _arc_input_socket_y(group_node, sock))
    return None



_DECAL_LAYERMASK_LABEL_RE = re.compile(
    r"^Decal\s+(\d+)\s+LayerMask(?:\s*[×x]\s*Alpha)?(?:\s*\((\d+)\))?$",
    re.I,
)

_DECAL_COLOR_TEX_LABEL_RE = re.compile(r"^Decal\s+(\d+)\s*:", re.I)

_DECAL_DATA_TEX_LABEL_RE = re.compile(
    r"^Decal\s+(\d+)\s+(?:Normal|Data)\s*:", re.I
)

# Right of ColorTex (+300) overlaps NCT Mapping; prefer DataTex+280 or Color+560.
_DECAL_LAYERMASK_X_AFTER_COLOR = 560.0

_DECAL_LAYERMASK_X_AFTER_DATA = 280.0



def place_decal_layermasks_on_color_rows(nodes) -> int:
    """Snap each Decal N LayerMask node onto the same Y row as Decal N ColorTex.

    NCT moves color textures to absolute coords and leaves LayerMask on the
    builder frame; keep one LayerMask node per decal on that row, to the right
    of ColorTex / DataTex so it does not sit on Mapping.
    """
    color_by_idx = {}
    data_by_idx = {}
    mask_nodes = []
    for node in nodes:
        if getattr(node, "bl_idname", "") == "NodeFrame" or getattr(node, "type", "") == "FRAME":
            continue
        lab = (getattr(node, "label", None) or "").strip()
        m_data = _DECAL_DATA_TEX_LABEL_RE.match(lab)
        if m_data and getattr(node, "type", "") == "TEX_IMAGE":
            try:
                data_by_idx[int(m_data.group(1))] = node
            except (TypeError, ValueError):
                pass
            continue
        m_tex = _DECAL_COLOR_TEX_LABEL_RE.match(lab)
        if m_tex and getattr(node, "type", "") == "TEX_IMAGE":
            if "Normal" in lab or "Data" in lab:
                continue
            try:
                color_by_idx[int(m_tex.group(1))] = node
            except (TypeError, ValueError):
                pass
            continue
        m_mask = _DECAL_LAYERMASK_LABEL_RE.match(lab)
        if not m_mask:
            continue
        # Only real LayerMask helpers (group / ColorRamp / legacy Math) — Mapping
        # nodes also store arc_decal_layer_mask and must not be moved here.
        bl_id = getattr(node, "bl_idname", "") or ""
        tree_name = getattr(getattr(node, "node_tree", None), "name", "") or ""
        is_lm = (
            bl_id == "ShaderNodeValToRGB"
            or bl_id == "ShaderNodeMath"
            or (
                bl_id == "ShaderNodeGroup"
                and ("LayerMask" in tree_name.replace(" ", "") or "LayerMask" in lab)
            )
        )
        if not is_lm:
            continue
        try:
            mask_nodes.append((int(m_mask.group(1)), node))
        except (TypeError, ValueError):
            continue

    moved = 0
    for idx, mask_node in mask_nodes:
        color = color_by_idx.get(idx)
        if color is None:
            continue
        data = data_by_idx.get(idx)
        try:
            mask_node.parent = None
        except Exception:
            pass
        try:
            mask_node.parent = getattr(color, "parent", None)
        except Exception:
            pass
        try:
            if data is not None:
                x = float(data.location.x) + _DECAL_LAYERMASK_X_AFTER_DATA
            else:
                x = float(color.location.x) + _DECAL_LAYERMASK_X_AFTER_COLOR
            mask_node.location = (x, float(color.location.y))
            moved += 1
        except Exception:
            continue
    return moved



def place_decal_normals_by_first_link(nodes, group_node=None) -> int:
    """Place each Decal Normal at the DN-slot coords of its first Arc connection.

    NCT only captured DataTex for Decal 2–5; Decal 1 Normal was left on the
    Decals [FMODEL] frame. First DN N / DN Alpha N link wins (same rule as Mix
    TA normals). Collapsed (hide=True).
    """
    tree = _nct_tree_from_nodes(nodes)
    if group_node is None and tree is not None:
        for n in nodes:
            if getattr(n, "bl_idname", "") != "ShaderNodeGroup":
                continue
            ng = getattr(n, "node_tree", None)
            name = getattr(ng, "name", "") or ""
            if name.replace(" ", "") == "ArcTexturer" or name.startswith("ArcTexturer"):
                group_node = n
                break

    moved = 0
    for node in list(nodes):
        if getattr(node, "bl_idname", "") == "NodeFrame" or getattr(node, "type", "") == "FRAME":
            continue
        slot = _decal_normal_slot_from_first_link(node, tree)
        if slot is None:
            continue
        loc = _decal_normal_slot_location(slot, group_node=group_node)
        if loc is None:
            continue
        try:
            node.parent = None
        except Exception:
            pass
        try:
            node.location = loc
            node.hide = True
            moved += 1
        except Exception:
            continue
    return moved



def place_crease_edge_overlays_by_first_link(nodes, group_node=None) -> int:
    """Place Crease/Edge ColorOverlay RGB at slot coords.

    Only nodes labeled ``N_CreaseColorOverlay`` / ``N_EdgeColorOverlay`` move.
    Prefer first Crease N / Edge N link when wired; else use the label zone so
    unlinked (disabled) overlays still park on the crease/edge table — never
    relocate ColorMask_XYZ or other feeds that happen to touch those sockets.
    """
    tree = _nct_tree_from_nodes(nodes)
    if group_node is None and tree is not None:
        for n in nodes:
            if getattr(n, "bl_idname", "") != "ShaderNodeGroup":
                continue
            ng = getattr(n, "node_tree", None)
            name = getattr(ng, "name", "") or ""
            if name.replace(" ", "") == "ArcTexturer" or name.startswith("ArcTexturer"):
                group_node = n
                break

    moved = 0
    for node in list(nodes):
        if getattr(node, "bl_idname", "") == "NodeFrame" or getattr(node, "type", "") == "FRAME":
            continue
        lab = (getattr(node, "label", "") or getattr(node, "name", "") or "").strip()
        m_lab = _CREASE_EDGE_OVERLAY_LABEL_RE.match(lab)
        if not m_lab:
            continue
        try:
            label_zone = int(m_lab.group(1))
        except (TypeError, ValueError):
            continue
        label_kind = m_lab.group(2).lower()
        kind_slot = _crease_edge_kind_slot_from_first_link(node, tree)
        if kind_slot is not None:
            kind, slot = kind_slot
        else:
            kind, slot = label_kind, label_zone
        loc = _crease_edge_slot_location(kind, slot, group_node=group_node)
        if loc is None:
            continue
        try:
            node.parent = None
        except Exception:
            pass
        try:
            node.location = loc
            node.hide = True
            moved += 1
        except Exception:
            continue
    return moved



def place_base_color_overlays_by_first_link(nodes, group_node=None) -> int:
    """Place BaseColorOverlay RGB on Overlay N row of its first Arc link.

    NCT captured zones 1–5 only. The old socket-index Y fallback parked zone 6+
    near Colour 5 (above the NCT zone-5 overlay). First ``Overlay N`` link wins
    (same rule as Crease/Edge); Fac sliders are ignored. Not collapsed.
    """
    tree = _nct_tree_from_nodes(nodes)
    if group_node is None and tree is not None:
        for n in nodes:
            if getattr(n, "bl_idname", "") != "ShaderNodeGroup":
                continue
            ng = getattr(n, "node_tree", None)
            name = getattr(ng, "name", "") or ""
            if name.replace(" ", "") == "ArcTexturer" or name.startswith("ArcTexturer"):
                group_node = n
                break

    moved = 0
    for node in list(nodes):
        if getattr(node, "bl_idname", "") == "NodeFrame" or getattr(node, "type", "") == "FRAME":
            continue
        # Prefer wired Overlay N; fall back to label zone for unlinked RGB.
        slot = _base_overlay_slot_from_first_link(node, tree)
        if slot is None:
            lab = (getattr(node, "label", "") or getattr(node, "name", "") or "").strip()
            m = re.match(r"^(\d+)_BaseColorOverlay$", lab)
            if not m:
                continue
            try:
                slot = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if not (1 <= slot <= 9):
                continue
        loc = _base_overlay_slot_location(slot, group_node=group_node)
        if loc is None:
            continue
        try:
            node.parent = None
        except Exception:
            pass
        try:
            node.location = loc
            moved += 1
        except Exception:
            continue
    return moved



def place_ta_normals_by_first_link(nodes, group_node=None) -> int:
    """Place each TA_* normal at the Mix-slot coords of its first Arc connection.

    Multi-link normals use the first outgoing Mix/Medium/Edge/Crease Normal link
    only (not an average, not the mask column). Slot 1–9 heights come from the
    captured placeholder table. Nodes are collapsed (hide=True).
    Non-TA_* nodes (ColorABC, overlays, ColorMaskSwatch) are never moved.
    """
    tree = _nct_tree_from_nodes(nodes)
    if group_node is None and tree is not None:
        for n in nodes:
            if getattr(n, "bl_idname", "") != "ShaderNodeGroup":
                continue
            ng = getattr(n, "node_tree", None)
            name = getattr(ng, "name", "") or ""
            if name.replace(" ", "") == "ArcTexturer" or name.startswith("ArcTexturer"):
                group_node = n
                break

    outgoing = _nct_outgoing_by_node(tree)
    moved = 0
    for node in list(nodes):
        if getattr(node, "bl_idname", "") == "NodeFrame" or getattr(node, "type", "") == "FRAME":
            continue
        if not _is_ta_texture_node(node):
            continue
        keys = list(_iter_nct_layout_keys(node, outgoing=outgoing))
        if not any(k.startswith("role:TANormal:") for k in keys):
            continue
        # ColorMask-only feeds (no Arc Mix link) use place_ta_colormask_by_first_link.
        slot = _ta_normal_slot_from_first_link(node, tree)
        if slot is None:
            continue
        loc = _ta_normal_slot_location(slot, group_node=group_node, tree=tree)
        if loc is None:
            continue
        try:
            node.parent = None
        except Exception:
            pass
        try:
            node.location = loc
            node.hide = True
            moved += 1
        except Exception:
            continue
        # Collapse any Normal Map node fed by this texture.
        for lnk in getattr(tree, "links", []) or []:
            if lnk.from_node != node:
                continue
            to = lnk.to_node
            if getattr(to, "bl_idname", "") == "ShaderNodeNormalMap":
                try:
                    to.hide = True
                except Exception:
                    pass
    return moved



def place_ta_colormask_by_first_link(nodes) -> int:
    """Place TA_* feeds at ColorMask_XYZ zone coords of their first CM connection.

    First outgoing ColorMask Roughness/Metal link on a TA_* texture wins
    (same first-link rule as Mix-slot TA normals). Collapsed by default.
    ColorABC / ColorMaskSwatch / overlays are never placed by this path.
    """
    tree = _nct_tree_from_nodes(nodes)
    if tree is None:
        return 0
    cm_by_zone = {}
    for n in nodes:
        if getattr(n, "bl_idname", "") != "ShaderNodeGroup":
            continue
        ng = getattr(n, "node_tree", None)
        name = getattr(ng, "name", "") or ""
        if not name.startswith("ColorMask"):
            continue
        m = _TA_COLORMASK_ZONE_RE.search((getattr(n, "label", "") or "").strip())
        if not m:
            continue
        try:
            zone = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if 1 <= zone <= 8:
            cm_by_zone[zone] = n

    outgoing = _nct_outgoing_by_node(tree)
    moved = 0
    for node in list(nodes):
        if getattr(node, "bl_idname", "") == "NodeFrame" or getattr(node, "type", "") == "FRAME":
            continue
        if not _is_ta_texture_node(node):
            continue
        # Skip Main ColorMask texture (ColorMask socket only, not a feed).
        keys = list(_iter_nct_layout_keys(node, outgoing=outgoing))
        if "role:MainColorMask" in keys:
            continue
        zone = _ta_colormask_zone_from_first_link(node, tree)
        if zone is None:
            continue
        # Arc Mix-family TA normals keep Mix-slot placement.
        if any(k.startswith("role:TANormal:") for k in keys):
            if _ta_normal_slot_from_first_link(node, tree) is not None:
                continue
        loc = _ta_colormask_zone_location(zone, cm_group=cm_by_zone.get(zone))
        if loc is None:
            continue
        try:
            node.parent = None
        except Exception:
            pass
        try:
            node.location = loc
            node.hide = True
            moved += 1
        except Exception:
            continue
        for lnk in getattr(tree, "links", []) or []:
            if lnk.from_node != node:
                continue
            to = lnk.to_node
            if getattr(to, "bl_idname", "") == "ShaderNodeNormalMap":
                try:
                    to.hide = True
                except Exception:
                    pass
    return moved



def align_ta_normal_column(nodes, group_node=None) -> int:
    """Place TA normals on Mix slots; ColorMask feeds on zone-adjacent slots.

    Also places Decal normals (DN slots), Crease/Edge, and BaseColorOverlay by
    first Arc link. Mix/DN/Crease/Edge/Overlay tables win over NCT label keys.
    UV/Mapping for normals stay near Arc; mask UV chain keeps NCT when present.
    """
    outgoing = _nct_outgoing_by_node(_nct_tree_from_nodes(nodes))
    layout = _load_nct_layout_index()
    uv_nodes = []
    mapping_nodes = []
    normal_count = 0
    for node in list(nodes):
        if getattr(node, "bl_idname", "") == "NodeFrame" or getattr(node, "type", "") == "FRAME":
            continue
        keys = list(_iter_nct_layout_keys(node, outgoing=outgoing))
        if any(k.startswith("role:TANormal:") for k in keys):
            normal_count += 1
        if "role:TANormalUV" in keys:
            uv_nodes.append(node)
        elif "role:TANormalMapping" in keys:
            mapping_nodes.append(node)

    moved = place_ta_normals_by_first_link(nodes, group_node=group_node)
    moved += place_ta_colormask_by_first_link(nodes)
    moved += place_decal_normals_by_first_link(nodes, group_node=group_node)
    moved += place_decal_layermasks_on_color_rows(nodes)
    moved += place_crease_edge_overlays_by_first_link(nodes, group_node=group_node)
    moved += place_base_color_overlays_by_first_link(nodes, group_node=group_node)

    # TA-normal UV chain: keep NCT when present; else snap near Arc.
    if layout.get("role:TANormalUV") is None:
        for node in uv_nodes:
            node.location = _TA_NORMAL_UV_LOC
    if layout.get("role:TANormalMapping") is None:
        for node in mapping_nodes:
            node.location = _TA_NORMAL_MAPPING_LOC
    return moved if moved else normal_count



def _arc_input_socket_y(group_node, socket) -> float:
    """Approximate world Y of an Arc Texturer input socket (header + row pitch)."""
    inputs = [
        s for s in group_node.inputs
        if not getattr(s, "is_unavailable", False)
    ]
    try:
        idx = inputs.index(socket)
    except ValueError:
        idx = list(group_node.inputs).index(socket)
    return float(group_node.location.y) - 30.0 - idx * 22.0



def place_zone_overlay_controls(nodes, group_node) -> int:
    """Place BaseColorOverlay RGB on Overlay N rows (zones 1–9).

    Fac is the Arc Texturer ``Overlay Fac N`` socket (forced 0 at setup —
    strength/LayerMask Fac parked after 2.18.180 regression). Uses first
    ``Overlay N`` link + NCT/extrapolated slot table — not socket-index Y.
    """
    return place_base_color_overlays_by_first_link(nodes, group_node=group_node)



# ---------------------------------------------------------------------------
# Material setup - Arc Texturer
# ---------------------------------------------------------------------------

# GT bypass layout — shared with gt_outfit_pbr (world-space grid + keep-parent coords).
_GT_DX = _gt_pbr._GT_DX
_GT_DY = _gt_pbr._GT_DY
_GT_ROW_H = _gt_pbr._GT_ROW_H
_GT_ZONE_Y0 = _gt_pbr._GT_ZONE_Y0
_GT_SHARED_Y = _gt_pbr._GT_SHARED_Y
_GT_MAPS_X = _gt_pbr._GT_MAPS_X
_GT_PAD = _gt_pbr._GT_PAD


def _gt_zone_y(zone: int) -> float:
    return _gt_pbr.zone_y(zone)


def _gt_place(nd, x, y, *, hide=False):
    return _gt_pbr.place(nd, x, y, hide=hide)


def _gt_declutter_nodes(nodes, pad: float = _GT_PAD) -> int:
    return _gt_pbr.declutter_nodes(nodes, pad=pad)


def _gt_make_tex(nodes, fpath, name, loc, *, non_color=False):
    return _gt_pbr.make_tex(nodes, fpath, name, loc, non_color=non_color)


def _setup_gt_bypass_material(
    obj,
    mat,
    folder: str,
    colours: dict,
    json_path: str,
    mi_data: dict,
    main_pngs: list,
    base_pngs: list,
    selected_skin_name: str,
    psk_path: str,
    decal_folder: str = "",
    manual_skins_folder: str = "",
):
    """Ground-truth graph: cooked Colour N + Principled PBR (no ArcTexturer)."""
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    occlusion_tex = []
    normal_tex = []
    basecolor_tex = []
    colormask_tex = []
    for fname in main_pngs or []:
        role = textures.identify_texture(fname)
        if role == "occlusion":
            occlusion_tex.append(fname)
        elif role == "normal":
            normal_tex.append(fname)
        elif role == "basecolor":
            basecolor_tex.append(fname)
        elif role == "colormask":
            colormask_tex.append(fname)

    base_other = [
        p for p in (base_pngs or [])
        if textures.base_skin_texture_group(os.path.basename(p)) == "other"
    ]
    base_normals = [
        p for p in (base_pngs or [])
        if textures.base_skin_texture_group(os.path.basename(p)) == "normals"
    ]
    base_masks = [
        p for p in (base_pngs or [])
        if textures.base_skin_texture_group(os.path.basename(p)) == "masks"
    ]

    ocm_node = None
    for i, fname in enumerate(occlusion_tex):
        nd = _gt_make_tex(
            nodes,
            os.path.join(folder, fname) if folder else fname,
            f"GT_OCM_{i}",
            (_gt_pbr.maps_ocm_x(), _gt_pbr.colour_row_y("colour") - i * _GT_DY),
            non_color=True,
        )
        if nd is None:
            continue
        _gt_place(nd, _gt_pbr.maps_ocm_x(), _gt_pbr.colour_row_y("colour") - i * _GT_DY)
        nd.label = f"OCM {fname}"
        if ocm_node is None:
            ocm_node = nd
            try:
                from .. import ocm_zone_cache
                note = ocm_zone_cache.preprocess_ocm_image(nd.image)
                if note:
                    print(f"Arc Raiders: {note}")
            except Exception:
                pass

    cm_src = None
    for i, fname in enumerate(colormask_tex):
        nd = _gt_make_tex(
            nodes,
            os.path.join(folder, fname) if folder else fname,
            f"GT_ColorMask_{i}",
            (_gt_pbr.maps_cm_x(), _gt_pbr.colour_row_y("colour") - i * _GT_DY),
        )
        if nd is None:
            continue
        _gt_place(nd, _gt_pbr.maps_cm_x(), _gt_pbr.colour_row_y("colour") - i * _GT_DY)
        nd.label = f"ColorMask {fname}"
        if cm_src is None:
            cm_src = nd

    base_tex = None
    for i, fname in enumerate(basecolor_tex):
        nd = _gt_make_tex(
            nodes,
            os.path.join(folder, fname) if folder else fname,
            f"GT_BaseColor_{i}",
            (_gt_pbr.maps_cm_x(), _gt_pbr.colour_row_y("tex") - i * _GT_DY),
        )
        if nd is None:
            continue
        _gt_place(nd, _gt_pbr.maps_cm_x(), _gt_pbr.colour_row_y("tex") - i * _GT_DY)
        nd.label = f"BaseColor {fname}"
        if base_tex is None:
            base_tex = nd

    normal_node = None
    for i, fname in enumerate(normal_tex):
        nd = _gt_make_tex(
            nodes,
            os.path.join(folder, fname) if folder else fname,
            f"GT_Normal_{i}",
            (_gt_pbr.maps_uv_x(), _gt_pbr.colour_row_y("n_base") - i * _GT_DY),
            non_color=True,
        )
        if nd is None:
            continue
        _gt_place(nd, _gt_pbr.maps_uv_x(), _gt_pbr.colour_row_y("n_base") - i * _GT_DY)
        nd.label = f"Normal {fname}"
        if normal_node is None:
            normal_node = nd

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
        _scene_mode = str(getattr(bpy.context.scene, "arc_palette_mode", "auto") or "auto")
        _mat_mode = str(obj.get("arc_palette_mode", "") or mat.get("arc_palette_mode", "auto") or "auto")
        _manifest_mode = str(obj.get("arc_palette_routing", "auto") or "auto")
    except Exception:
        pass
    _routing_mode, _routing_source, _material_key, _uses_secondary = palette_calibration.resolve_routing(
        _mat_name, _parent_name,
        scene_mode=_scene_mode,
        material_mode=_mat_mode,
        manifest_mode=_manifest_mode,
        json_path=json_path,
    )
    _zone_scalars = (mi_data or {}).get("zone_scalars") or {}
    ta_ids = (mi_data or {}).get("ta_ids")
    if ta_ids is None:
        ta_ids = textures.parse_texture_array_ids(json_path) if json_path else {}

    try:
        mat["arc_outfit_color_pipeline"] = palette_calibration.OUTFIT_COLOR_PIPELINE_GROUND_TRUTH
        mat["arc_gt_bypass_arc_texturer"] = 1
        mat["arc_material_key"] = _material_key
        mat["arc_palette_resolved"] = _routing_mode
        mat["arc_palette_source"] = _routing_source
        obj["arc_outfit_color_pipeline"] = palette_calibration.OUTFIT_COLOR_PIPELINE_GROUND_TRUTH
        obj["arc_material_key"] = _material_key
    except Exception:
        pass

    _ensure_core_scheme_colour_nodes(nodes, colours or {})
    for i, key in enumerate(("ColorA", "ColorB", "ColorC")):
        nd = nodes.get(key)
        if nd is None:
            continue
        _gt_place(nd, _gt_pbr.maps_cm_x(), _gt_pbr.colour_row_y("wet") - i * 80.0)
        nd.hide = True
    for i, key in enumerate(("ColorA2", "ColorB2", "ColorC2")):
        nd = nodes.get(key)
        if nd is None:
            continue
        _gt_place(nd, _gt_pbr.maps_cm_x() + 80.0, _gt_pbr.colour_row_y("wet") - i * 80.0)
        nd.hide = True

    _gt = _wire_outfit_color_ground_truth(
        nodes=nodes,
        links=links,
        group_node=None,
        colours=colours or {},
        cm_groups={},
        cm_src=cm_src,
        zone_scalars=_zone_scalars,
        routing_mode=_routing_mode,
        ta_ids=ta_ids,
        colortex_pngs=base_other,
        colortex_pngs_fallback=base_pngs,
        bypass_arc=True,
        base_tex=base_tex,
        uses_secondary=_uses_secondary,
        mat_key=getattr(mat, "name", "mat") or "mat",
    )
    from .. import utils as _utils
    search_dirs = []
    df = decal_folder or ""
    if not df:
        try:
            df = _utils.get_decal_folder() or ""
        except Exception:
            df = ""
    for d in (
        df,
        manual_skins_folder,
        os.path.dirname(json_path) if json_path else "",
        folder,
        os.path.dirname(psk_path) if psk_path else "",
    ):
        if d and d not in search_dirs:
            search_dirs.append(d)
    _decals = (mi_data or {}).get("decals") if mi_data else None
    if _decals is None and json_path:
        try:
            _decals = textures.parse_decals(json_path) or []
        except Exception:
            _decals = []
    _gt_pbr.finish_gt_outfit_graph(
        nodes=nodes,
        links=links,
        ocm_node=ocm_node,
        cm_src=cm_src,
        base_tex=base_tex,
        normal_node=normal_node,
        zone_outputs=_gt.get("zone_outputs") or {},
        colours=colours or {},
        zone_scalars=_zone_scalars,
        ta_ids=ta_ids or {},
        mi_data=mi_data or {},
        base_normals=base_normals,
        base_masks=base_masks,
        decals=_decals or [],
        decal_folder=df,
        search_dirs=search_dirs,
        sparse_colormask=("radiobag" in f"{_material_key} {_mat_name} {json_path or ''}".lower()),
    )
    # Keep Colour-row frames; do not unparent/declutter (that made the spaghetti graph).
    n_moved = 0
    print(
        "Arc Raiders PSK Importer: outfit color pipeline=ground_truth "
        "(Principled PBR: rough/metal/decals/normals/edge-crease; no ArcTexturer) "
        f"mode={_routing_mode} src={_routing_source} key={_material_key} "
        f"zones={_gt.get('zones')} "
        f"assemble={_gt.get('assemble_mode')} "
        f"scheme_a2={_gt.get('scheme_a2_linked')} "
        f"scheme_basecolor_r={_gt.get('scheme_fac_from_basecolor_r')} "
        f"colormask_weights={_gt.get('colormask_in_weights')} "
        f"layout_nudged={n_moved} "
        f"colortex_zones={_gt.get('colortex_zones')} "
        f"pattern_zones={_gt.get('pattern_zones')} "
        f"hue_zones={_gt.get('hue_zones')}"
        + (f"; skin={selected_skin_name}" if selected_skin_name else "")
    )
    return mat


def setup_arc_texturer_material(obj, folder: str, colours: dict, psk_path: str = "", 
                                json_path: str = "", decal_folder: str = "", 
                                selected_skin_name: str = "", manual_skins_folder: str = "",
                                mi_data: dict = None, main_pngs: list = None, base_pngs: list = None,
                                texture_fingerprint: str = "", use_material_cache: bool = True):
    _pipe = outfit_color_pipeline_from_scene()
    _gt_mode = _pipe == palette_calibration.OUTFIT_COLOR_PIPELINE_GROUND_TRUTH
    arc_ng = None
    if not _gt_mode:
        # Legacy: ColorMask_XYZ albedo + ArcTexturer for the rest.
        arc_ng = utils.ensure_arc_texturer_node_group()
        if not arc_ng:
            print(f"Arc Raiders PSK Importer: Skipping material for '{obj.name}' — ArcTexturer unavailable.")
            return None

    # Grow SK-aligned slots before any assign (visor shell+glass, multi-slot clothing).
    _ensure_clothing_sk_slot_count(obj, psk_path)

    # Categorise main folder PNGs (needed for cache fingerprint before build)
    with utils.timed("mat.scan_textures"):
        if main_pngs is None:
            try:
                main_pngs = sorted(f for f in os.listdir(folder) if f.lower().endswith(".png")) if folder else []
            except OSError:
                main_pngs = []

        # Categorise base skin PNGs
        if base_pngs is None:
            base_pngs = textures.scan_base_skin_textures(psk_path, selected_skin_name, manual_skins_folder) if psk_path else []

    with utils.timed("mat.parse_mi"):
        if mi_data is None and json_path:
            try:
                mi_data = textures.parse_clothing_mi(json_path) or {}
            except Exception:
                mi_data = {}
        _decals_for_fp = None
        if mi_data is not None:
            _decals_for_fp = mi_data.get("decals") or []
        elif json_path:
            try:
                _decals_for_fp = textures.parse_decals(json_path) or []
            except Exception:
                _decals_for_fp = []

    fp = texture_fingerprint or compute_clothing_texture_fingerprint(
        folder, main_pngs, base_pngs, colours=colours, decals=_decals_for_fp,
    )
    cache_key = (
        clothing_material_cache_key(
            json_path,
            fp,
            outfit_color_pipeline=outfit_color_pipeline_from_scene(),
        )
        if (use_material_cache and json_path)
        else None
    )
    if cache_key is not None:
        cached = _clothing_cache_get(cache_key)
        if cached is not None:
            _CLOTHING_CACHE_STATS["hits"] = int(_CLOTHING_CACHE_STATS.get("hits", 0)) + 1
            _assign_clothing_material(obj, cached, psk_path)
            return cached
        _CLOTHING_CACHE_STATS["misses"] = int(_CLOTHING_CACHE_STATS.get("misses", 0)) + 1

    mat = bpy.data.materials.new(name=obj.name + "_Mat")
    mat.use_nodes = True
    # Prefer the opaque clothing / Visor shell slot. On multi-slot visor PSKs,
    # active_material often lands on glass — which apply_embedded_visor_slots then
    # overwrites, leaving MI_Visor_Visor untextured.
    _assign_clothing_material(obj, mat, psk_path)
    try:
        if json_path:
            mat["arc_mi_path"] = _norm_path_key(json_path)
            mat["arc_skin_json"] = os.path.abspath(json_path)
    except Exception:
        pass

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    if _gt_mode:
        _setup_gt_bypass_material(
            obj,
            mat,
            folder,
            colours,
            json_path,
            mi_data or {},
            main_pngs or [],
            base_pngs or [],
            selected_skin_name,
            psk_path,
            decal_folder=decal_folder or "",
            manual_skins_folder=manual_skins_folder or "",
        )
        try:
            mat["arc_nodes_organized"] = 1
            if "arc_nodes_need_organize" in mat:
                del mat["arc_nodes_need_organize"]
        except Exception:
            pass
        if cache_key is not None:
            _clothing_cache_store(cache_key, mat)
        return mat

    group_node = nodes.new("ShaderNodeGroup")
    group_node.node_tree = arc_ng
    # Match NodeConnectionTest GoalieShirt Arc Texturer placement.
    group_node.location = (282.24, -47.04)
    # ColorABC debug: Dirt/Variation wash ColorMask toward white (Q01).
    utils.apply_arc_instance_weather_defaults(group_node)
    
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
            img = _load_image_cached(fpath)
            if img is None:
                continue
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
    
    with utils.timed("mat.load_images"):
        def connect_occlusion(node, img):
            # Cooked OCM is SRGB=False (data ladder on B). Must be Non-Color so
            # MaterialID bands match palette_calibration.BANDS (see CURVATURE_ID_ROUTING.md).
            try:
                img.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
            links.new(node.outputs["Color"], group_node.inputs["Main Texture"])
            # OCM stays on Main Texture (Arc needs R/G/B). Bake Color N grayscale
            # masks for Mask Debug once per OCM (shared across colorways) — does
            # not replace this wiring.
            try:
                from .. import ocm_zone_cache
                note = ocm_zone_cache.preprocess_ocm_image(img)
                if note:
                    print(f"Arc Raiders: {note}")
            except Exception as exc:
                print(f"Arc Raiders: OCM zone mask preprocess skipped: {exc}")
        place_column(
            occlusion_tex, ABOVE_X, non_color=True,
            connect_fn=connect_occlusion, collapsed=True, start_y=ABOVE_Y,
        )

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
        place_column(other_tex, -2400, start_y=-2000)
    
    # One ColorMask_XYZ per Colour/Roughness/Metallic channel (zones 1..8).
    # Layout note: preferred node.location values can be MCP-dumped later
    # (execute_blender_code → node.location) once an outfit graph is arranged by hand.
    _ZONE_CHANNELS = tuple(range(1, 9))

    _CM_COLOUR_INPUTS = {
        # Default AUTO — all primary ColorA/B/C (Abyss measure 2.18.147).
        # Resolved per material via palette_calibration.resolve_routing.
        # Airbag Leather_Blue uses MEASURED_MODE_OVERRIDES → secondary.
        1: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
        2: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
        3: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
        4: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
        5: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
        6: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
        7: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
        8: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
    }
    _cm_groups = {}

    with utils.timed("mat.colormask_zones"):
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
            json_path=json_path,
        )
        _CM_COLOUR_INPUTS = palette_calibration.section_colour_inputs(_routing_mode)
        _zone_scalars = (mi_data or {}).get("zone_scalars") or {}
        _csb_blends = palette_calibration.extract_color_scheme_blends(_zone_scalars)
        _csb_soft = {
            z: fac
            for z, raw in _csb_blends.items()
            if (fac := palette_calibration.provisional_scheme_mix_factor(raw)) is not None
        }
        _outfit_color_pipeline = outfit_color_pipeline_from_scene()
        # Legacy LayerMask-as-band (inspection). GT uses bitmasks (D029/D032).
        _layer_masks = palette_calibration.extract_layer_masks(_zone_scalars)
        _layer_mask_bits = palette_calibration.extract_layer_mask_bits(_zone_scalars)
        try:
            if str(_mat_mode).lower() not in ("", "auto"):
                mat["arc_palette_mode"] = str(_mat_mode).lower()
                obj["arc_palette_mode"] = str(_mat_mode).lower()
            mat["arc_material_key"] = _material_key
            mat["arc_palette_resolved"] = _routing_mode
            mat["arc_palette_source"] = _routing_source
            mat["arc_outfit_color_pipeline"] = _outfit_color_pipeline
            mat["arc_color_scheme_blend_default"] = float(
                palette_calibration.COLOR_SCHEME_BLEND_DEFAULT
            )
            mat["arc_color_scheme_blend_semantics"] = (
                palette_calibration.COLOR_SCHEME_BLEND_SEMANTICS
                if _outfit_color_pipeline
                == palette_calibration.OUTFIT_COLOR_PIPELINE_LEGACY
                else "d022_scheme_swatch_gate_mask_color"
            )
            mat["arc_blurry_curvature"] = palette_calibration.BLURRY_CURVATURE_STATUS
            if _csb_blends:
                mat["arc_color_scheme_blends"] = {
                    str(k): round(float(v), 6) for k, v in sorted(_csb_blends.items())
                }
            if _csb_soft:
                mat["arc_color_scheme_blend_soft_mix_zones"] = sorted(_csb_soft.keys())
            if _layer_masks:
                mat["arc_layer_masks"] = {
                    str(k): int(v) for k, v in sorted(_layer_masks.items())
                }
            if _layer_mask_bits:
                mat["arc_layer_mask_bits"] = {
                    str(k): int(v) for k, v in sorted(_layer_mask_bits.items())
                }
            obj["arc_material_key"] = _material_key
            obj["arc_palette_resolved"] = _routing_mode
            obj["arc_outfit_color_pipeline"] = _outfit_color_pipeline
        except Exception:
            pass
    
        _cm_group_ok = utils.ensure_colormask_node_group()
        _cm_ng = utils.find_node_group(utils._COLORMASK_GROUP) if _cm_group_ok else None
        _cm_groups = {}  # zone int → ColorMask_XYZ group node
        cm_frame = None
        _cm_src = colormask_nodes[0] if colormask_nodes else None
        _CM_XYZ_X = -800
        _CM_XYZ_Y0 = 400
        _CM_XYZ_STEP = -420

        for zone in _ZONE_CHANNELS:
            colour_sock = f"Colour {zone}"
            rough_sock = f"Roughness {zone}"
            metal_sock = f"Metallic {zone}"
            if colour_sock not in group_node.inputs:
                continue
            cm_group = None
            if _cm_ng is not None:
                cm_group = nodes.new("ShaderNodeGroup")
                cm_group.node_tree = _cm_ng
                cm_group.label = f"ColorMask_XYZ (zone {zone})"
                cm_group.location = (_CM_XYZ_X, _CM_XYZ_Y0 + (zone - 1) * _CM_XYZ_STEP)
                if cm_frame is None:
                    cm_frame = nodes.new("NodeFrame")
                    cm_frame.label = "ColorMask_XYZ per channel → Colour / Rough / Metal"
                    cm_frame.label_size = 18
                cm_group.parent = cm_frame
            _cm_groups[zone] = cm_group

            if cm_group and _cm_src:
                if "ColorMask" in cm_group.inputs:
                    links.new(_cm_src.outputs["Color"], cm_group.inputs["ColorMask"])
                elif cm_group.inputs:
                    links.new(_cm_src.outputs["Color"], cm_group.inputs[0])

                def _wire_out(cm_grp, out_name, arc_sock):
                    if arc_sock not in group_node.inputs:
                        return
                    out = cm_grp.outputs.get(out_name)
                    if out:
                        links.new(out, group_node.inputs[arc_sock])

                # Legacy wires Mask_Color now. GT drives Colour N from cooked
                # fac=amt×BaseColor×Swatch assemble (not Mask_Color / XYZ G/B-as-ABC).
                if _outfit_color_pipeline == palette_calibration.OUTFIT_COLOR_PIPELINE_LEGACY:
                    _wire_out(cm_group, "Mask_Color", colour_sock)
                _wire_out(cm_group, "Mask_Roughness", rough_sock)
                _wire_out(cm_group, "Mask_Metal", metal_sock)
            elif (
                _cm_src
                and not cm_group
                and _outfit_color_pipeline
                == palette_calibration.OUTFIT_COLOR_PIPELINE_LEGACY
            ):
                links.new(_cm_src.outputs["Color"], group_node.inputs[colour_sock])

    with utils.timed("mat.ta_slices"):
        # Dual TA UV chains matching GoalieShirt NCT layout: normals near Arc,
        # masks/patterns near the TA mask column. Shared labels; NCT roles disambiguate.
        normal_uv_node = nodes.new("ShaderNodeTexCoord")
        normal_uv_node.location = (-876.0, 71.0)
        normal_uv_node.label = "Texture Coordinate"
        normal_mapping_node = nodes.new("ShaderNodeMapping")
        normal_mapping_node.location = (-674.0, 77.0)
        normal_mapping_node.label = "Mapping"
        normal_mapping_node.inputs["Scale"].default_value = (25.0, 25.0, 25.0)
        links.new(normal_uv_node.outputs["UV"], normal_mapping_node.inputs["Vector"])

        mask_uv_node = nodes.new("ShaderNodeTexCoord")
        mask_uv_node.location = (-4896.0, -605.0)
        mask_uv_node.label = "Texture Coordinate"
        mask_mapping_node = nodes.new("ShaderNodeMapping")
        mask_mapping_node.location = (-4603.0, -602.0)
        mask_mapping_node.label = "Mapping"
        mask_mapping_node.inputs["Scale"].default_value = (25.0, 25.0, 25.0)
        links.new(mask_uv_node.outputs["UV"], mask_mapping_node.inputs["Vector"])
        # Legacy alias used by ColorTexture / pattern modulation below.
        mapping_node = mask_mapping_node
    
        _normal_nodes = {}
        _mask_nodes = {}
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
            # BaseRoughnessID → ColorMask_XYZ Roughness_X/Y/Z (see block below), not Arc directly.
            ("EdgeRoughnessID", "Edge Roughness {zone}", _mask_nodes, base_masks),
            ("CreaseRoughnessID", "Crease Roughness {zone}", _mask_nodes, base_masks),
            ("CreaseMaskID", "Crease Mask {zone}", _mask_nodes, base_masks),
            ("EdgeMaskID", "Edge Mask {zone}", _mask_nodes, base_masks),
            # Pattern wired below with PatternColor gates. ColorTexture slices
            # are bound in GT assemble (ID≥1 only; never slice 0).
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
                img = _load_image_cached(png_path)
                if img is None:
                    return None, ""
                if non_color:
                    img.colorspace_settings.name = "Non-Color"
                tex_nd = nodes.new("ShaderNodeTexImage")
                tex_nd.image = img
                tex_nd.label = os.path.basename(png_path)
                tex_nd.interpolation = "Cubic"
                # Normals: temporary X; final Mix-slot Y via place_ta_normals_by_first_link.
                if node_dict is _normal_nodes:
                    tex_nd.location = (_TA_NORMAL_COL_X, -500.0 - len(node_dict) * 40.0)
                    tex_nd.hide = True
                else:
                    tex_nd.location = (-3000, len(node_dict) * ROW_H)
                node_dict[stem] = tex_nd
            return tex_nd, png_path

        for (id_suffix, arc_template, node_dict, png_list) in ID_TO_SOCKET:
            if (
                not crease_edge_color_enabled_from_scene()
                and id_suffix in _CREASE_EDGE_TA_ID_SUFFIXES
            ):
                continue
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

        # BaseRoughnessID → ColorMask_XYZ Roughness_X/Y/Z (TA B&W Color, no RGB split),
        # then Mask_Roughness → Arc. VALUE sockets accept Color via Blender's RGBA→float link.
        for (zone, key_sfx), slice_idx in list(ta_ids.items()):
            if key_sfx != "BaseRoughnessID":
                continue
            try:
                zone_i = int(zone)
            except (TypeError, ValueError):
                continue
            cm_grp = _cm_groups.get(zone_i)
            if not cm_grp:
                continue
            tex_nd, png_path = _ensure_slice_node(_mask_nodes, base_masks, slice_idx, non_color=True)
            if tex_nd is None:
                print(f"    WARNING: No PNG for slice {slice_idx} (BaseRoughness zone {zone})")
                continue
            for sock_name in ("Roughness_X", "Roughness_Y", "Roughness_Z"):
                if sock_name in cm_grp.inputs and not cm_grp.inputs[sock_name].is_linked:
                    links.new(tex_nd.outputs["Color"], cm_grp.inputs[sock_name])
            # Collapsed; final XY via place_ta_colormask_by_first_link (first CM zone).
            try:
                tex_nd.hide = True
                loc = _ta_colormask_zone_location(zone_i, cm_group=cm_grp)
                if loc is not None and id(tex_nd) not in _ta_nodes_wired_to_arc:
                    tex_nd.location = loc
            except Exception:
                pass
            _ta_nodes_wired_to_arc.add(id(tex_nd))

        # Pattern: only when PatternColorA/B/C has a live (non-zero alpha) swatch.
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
    
        # One links pass — avoid O(nodes × links) Vector membership checks.
        _vector_linked = {
            id(lnk.to_node)
            for lnk in links
            if getattr(lnk.to_socket, "name", None) == "Vector"
        }
        for nd in list(_normal_nodes.values()):
            if nd is None:
                continue
            if id(nd) not in _ta_nodes_wired_to_arc:
                continue
            if id(nd) not in _vector_linked:
                links.new(normal_mapping_node.outputs["Vector"], nd.inputs["Vector"])
        for nd in list(_mask_nodes.values()) + list(_pattern_nodes.values()):
            if nd is None:
                continue
            if id(nd) not in _ta_nodes_wired_to_arc:
                continue
            if id(nd) not in _vector_linked:
                links.new(mask_mapping_node.outputs["Vector"], nd.inputs["Vector"])
    
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
    
    with utils.timed("mat.palette"):
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
            _nct_layout = _load_nct_layout_index()
        
            j = 0
            for key in colour_keys:
                rgba = colours.get(key)
                if rgba is None:
                    continue
                # Skip keys already materialized as RGB nodes.
                if nodes.get(key) is not None:
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
                    # Temporary; final XY via place_crease_edge_overlays_by_first_link
                    # (first Crease N / Edge N link — labels often mismatch zone).
                    rgb_node.hide = True
                    zone_num = int(key.split("_")[0]) if key[0].isdigit() else 0
                    is_crease = "_Crease" in key
                    table = _CREASE_COLOR_SLOT_LOCS if is_crease else _EDGE_COLOR_SLOT_LOCS
                    if zone_num in table:
                        rgb_node.location = table[zone_num]
                    else:
                        pair_row = max(zone_num - 1, 0)
                        pair_col = 0 if is_crease else 1
                        rgb_node.location = (250 + pair_col * 260, -450 - pair_row * 120)
                elif is_base_overlay:
                    # Temporary; final XY via place_base_color_overlays_by_first_link
                    # (first Overlay N link — NCT 1–5 + extrapolated 6–9).
                    zone_num = int(key.split("_")[0]) if key[0].isdigit() else 0
                    if zone_num in _BASE_COLOR_OVERLAY_SLOT_LOCS:
                        rgb_node.location = _BASE_COLOR_OVERLAY_SLOT_LOCS[zone_num]
                    else:
                        rgb_node.location = (-79.4, -1290.58 + (zone_num - 5) * _BASE_OVERLAY_ROW_DY)
                elif key in _ABC_KEYS or key in _ABC2_KEYS:
                    # Prefer captured GoalieShirt / NCT coords (user-fixed ColorABC columns).
                    nct = _nct_layout.get(f"label:{key}")
                    placed_abc = False
                    if nct and nct.get("location"):
                        loc = nct["location"]
                        try:
                            rgb_node.location = (float(loc[0]), float(loc[1]))
                            placed_abc = True
                        except (TypeError, ValueError, IndexError):
                            pass
                        if "hide" in nct:
                            try:
                                rgb_node.hide = bool(nct["hide"])
                            except Exception:
                                pass
                    if not placed_abc:
                        if key in _ABC_KEYS:
                            rgb_node.location = (ABC_X, ABC_Y_START + _abc_row * ABC_ROW_H)
                            _abc_row += 1
                        else:
                            rgb_node.location = (ABC2_X, ABC_Y_START + _abc2_row * ABC_ROW_H)
                            _abc2_row += 1
                else:
                    # Leftover inspection colours (ColorMaskSwatch, Pattern*, …).
                    # place_unconnected_colour_swatches re-packs into one horizontal row.
                    # Never include connected ColorABC / overlay RGB here.
                    rgb_node.location = (
                        _UNCONNECTED_COLOUR_ROW_X + j * _UNCONNECTED_COLOUR_STEP_X,
                        _UNCONNECTED_COLOUR_ROW_Y,
                    )
                    j += 1
        
            # Outfit color pipeline: ground_truth (D022–D039) or legacy ColorMask_XYZ.
            # Revert: scene.arc_outfit_color_pipeline = 'LEGACY' → Update Materials.
            # GT always materializes ColorA2/B2/C2 (Goalie dual-scheme). Legacy
            # keeps the pre-GT path: ColorA/B/C(/2) only from the MI colour_keys
            # loop, then ColorMask_XYZ X/Y/Z + Mask_Color → Colour N.
            _pipe = _outfit_color_pipeline
            if _pipe == palette_calibration.OUTFIT_COLOR_PIPELINE_GROUND_TRUTH:
                _ensure_core_scheme_colour_nodes(nodes, colours or {})
                _gt = _wire_outfit_color_ground_truth(
                    nodes=nodes,
                    links=links,
                    group_node=group_node,
                    colours=colours or {},
                    cm_groups=_cm_groups,
                    cm_src=_cm_src,
                    zone_scalars=_zone_scalars,
                    routing_mode=_routing_mode,
                    ta_ids=ta_ids,
                    colortex_pngs=base_other,
                    colortex_pngs_fallback=base_pngs,
                    uses_secondary=_uses_secondary,
                    mat_key=getattr(mat, "name", "mat") or "mat",
                )
                _soft_list = []
                print(
                    "Arc Raiders PSK Importer: outfit color pipeline=ground_truth "
                    f"(cooked fac+overlay+ColorTex) mode={_routing_mode} src={_routing_source} "
                    f"key={_material_key} zones={_gt.get('zones')} "
                    f"assemble={_gt.get('assemble_mode')} "
                    f"scheme_a2={_gt.get('scheme_a2_linked')} "
                    f"scheme_basecolor_r={_gt.get('scheme_fac_from_basecolor_r')} "
                    f"colormask_weights={_gt.get('colormask_in_weights')} "
                    f"arc=decals+normals+rough+alpha "
                    f"colortex_zones={_gt.get('colortex_zones')} "
                    f"pattern_zones={_gt.get('pattern_zones')} "
                    f"hue_zones={_gt.get('hue_zones')} "
                    f"layer_mask_bits={_gt.get('layer_mask_bits')} "
                    f"blur={_gt.get('blur')}"
                    + (f"; skin={selected_skin_name}" if selected_skin_name else "")
                )
            else:
                _soft_list = _wire_outfit_color_legacy(
                    nodes=nodes,
                    links=links,
                    cm_groups=_cm_groups,
                    colour_inputs=_CM_COLOUR_INPUTS,
                    csb_soft=_csb_soft,
                )
                _sec = [i + 1 for i, f in enumerate(_uses_secondary) if f and i < 8]
                _pri = [i + 1 for i, f in enumerate(_uses_secondary) if (not f) and i < 8]
                print(
                    "Arc Raiders PSK Importer: outfit color pipeline=legacy - "
                    f"mode={_routing_mode} src={_routing_source} key={_material_key} "
                    f"PRI zones {_pri}; SEC zones {_sec}"
                    + (
                        f"; ColorSchemeBlend soft-mix zones {_soft_list}"
                        if _soft_list
                        else "; ColorSchemeBlend=AUTO(default)"
                    )
                    + (f"; skin={selected_skin_name}" if selected_skin_name else "")
                )
            # Compact palette calibration report beside the skin JSON.
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
                    color_scheme_blends=_csb_blends,
                    soft_mix_zones=_soft_list,
                )
                _report["outfit_color_pipeline"] = _pipe
                if json_path and not _BATCH_MATERIAL_MODE:
                    _report["skin_json"] = os.path.basename(json_path)
                    # Cache under LOCALAPPDATA only — never write beside Pioneer skin/MI JSON.
                    palette_calibration.write_report(json_path, _report)
            except Exception as _exc:
                print(f"Arc Raiders PSK Importer: palette report skipped: {_exc}")
    
        # Crease/Edge ColorOverlay → Arc Crease N / Edge N (or neutralize when toggle off).
        # Runs even when colours dict is empty so disable still clears leftover links.
        apply_crease_edge_color_wiring(
            group_node,
            links,
            nodes,
            colours or {},
            enabled=crease_edge_color_enabled_from_scene(),
        )

        # Overlay Fac stays 0 (Arc Mix-replace ≠ cooked multiply — F04/F05).
        # GT already multiplies BaseColorOverlay into Colour N; Overlay N RGB
        # may still link here for inspection / Legacy layout.
        _overlay_inside = (
            "Overlay 1" in group_node.inputs and "Overlay Fac 1" in group_node.inputs
        )
        for zone_str in [str(z) for z in range(1, 10)]:
            colour_sock = f"Colour {zone_str}"
            if colour_sock not in group_node.inputs:
                continue
            colour_in = group_node.inputs[colour_sock]
            if not colour_in.is_linked:
                continue

            overlay_nd = nodes.get(f"{zone_str}_BaseColorOverlay")
            if overlay_nd is None:
                continue
            mix_fac = 0.0
            overlay_sock = f"Overlay {zone_str}"
            fac_sock = f"Overlay Fac {zone_str}"
            if _overlay_inside and overlay_sock in group_node.inputs and fac_sock in group_node.inputs:
                # Wire overlay color for inspection; Fac stays 0 so XYZ drives albedo.
                links.new(overlay_nd.outputs[0], group_node.inputs[overlay_sock])
                fac_in = group_node.inputs[fac_sock]
                if fac_in.is_linked:
                    try:
                        links.remove(fac_in.links[0])
                    except Exception:
                        pass
                try:
                    fac_in.default_value = float(mix_fac)
                except Exception:
                    pass
            # Legacy external Mix path: skip entirely when Fac would be 0 (XYZ already linked).

    # Decals
    with utils.timed("mat.decals"):
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

    # MI parameters + NCT / place_* always run at the very end of material build.
    # scene.arc_organize_nodes_on_import is ignored; Organize Nodes op remains for re-runs.
    if mi_data is not None:
        _mi_params = mi_data.get("mi_params") or {"scalars": [], "vectors": []}
    elif json_path:
        _mi_params = textures.parse_all_mi_parameters(json_path, known_colour_names=set(colours.keys()))
    else:
        _mi_params = {"scalars": [], "vectors": []}

    if _mi_params['scalars'] or _mi_params['vectors']:
        place_mi_parameter_nodes(nodes, links, _mi_params)

    # BaseColorOverlay on Overlay N rows (first-link + NCT/extrapolated table).
    # Overlay Fac N is the Arc group slider — no external Fac Value node.
    place_zone_overlay_controls(nodes, group_node)

    # Final NCT pass wins over builder / MI / overlay temporary placements.
    placed = apply_nct_clothing_layout(nodes)
    # Leftover ColorMaskSwatch / Pattern* RGB only — never connected ColorABC.
    place_unconnected_colour_swatches(nodes)
    # TA normals → Mix slots; ColorMask TA feeds → zone-adjacent slots (collapsed).
    align_ta_normal_column(nodes, group_node=group_node)
    # Unconnected TA masks → left-side cluster (after NCT mid-graph pins).
    place_unconnected_ta_textures(nodes)
    if placed < 8:
        apply_node_graph_padding(nodes)

    # Re-assert crease/edge kill after NCT/TA placement (shared Arc tree + sockets).
    apply_crease_edge_color_wiring(
        group_node,
        links,
        nodes,
        colours or {},
        enabled=crease_edge_color_enabled_from_scene(),
    )

    # CurvatureID_Override between Character → Material Output (Enable OFF).
    # Ready for Visor/Principled override without changing default Arc look.
    try:
        insert_curvature_id_override(mat, group_node)
    except Exception as exc:
        print(f"Arc Raiders PSK Importer: CurvatureID_Override insert skipped: {exc}")

    try:
        mat["arc_nodes_organized"] = 1
        if "arc_nodes_need_organize" in mat:
            del mat["arc_nodes_need_organize"]
    except Exception:
        pass

    if cache_key is not None:
        _clothing_cache_store(cache_key, mat)
    return mat



def set_enable_slider(group_node, zone: str, value: float = 1.0):
    sock = group_node.inputs.get(f"Enable {zone}")
    if sock is not None:
        try:
            sock.default_value = value
        except Exception:
            pass



def insert_curvature_id_override(mat, group_node) -> bool:
    """Insert ``CurvatureID_Override`` between Arc ``Character`` and Material Output.

    Wires ``Material ID Map`` from the OCM / Main Texture Color (same source as
    :func:`_find_ocm_image_node`). Leaves ``Override`` unconnected. Mixer
    ``Enable`` stays at its group default (OFF) so empty Override does not punch
    black holes — Fac = band_mask × Enable. Does not create/connect Visor —
    :func:`apply_ocm_curvature_visor_override` adds the reusable Visor group
    under this mixer (lower Y) when Enable turns on.
    """
    if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return False
    if group_node is None:
        return False
    if not utils.ensure_curvature_id_override_node_group():
        return False
    mixer_tree = utils.find_node_group(utils._CURVATURE_ID_OVERRIDE_GROUP)
    if mixer_tree is None:
        return False

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # Already inserted (idempotent).
    for node in nodes:
        if getattr(node, "type", "") != "GROUP":
            continue
        tree = getattr(node, "node_tree", None)
        name = (getattr(tree, "name", "") or "") if tree else ""
        if name.replace(" ", "") == "CurvatureID_Override" or name.startswith(
            "CurvatureID_Override"
        ):
            # Refresh Debug defaults / ColorMask link on already-inserted mixers.
            dbg = node.inputs.get("Debug")
            if dbg is not None:
                try:
                    dbg.default_value = 0
                except Exception:
                    pass
            cm_sock = node.inputs.get("ColorMask")
            if cm_sock is not None and not cm_sock.is_linked:
                cm_node = _find_colormask_image_node(nodes, group_node)
                if cm_node is not None and "Color" in cm_node.outputs:
                    try:
                        links.new(cm_node.outputs["Color"], cm_sock)
                    except Exception:
                        pass
            return True

    output_node = None
    for node in nodes:
        if getattr(node, "bl_idname", "") == "ShaderNodeOutputMaterial" or getattr(
            node, "type", ""
        ) == "OUTPUT_MATERIAL":
            output_node = node
            break
    if output_node is None:
        return False

    surface = output_node.inputs.get("Surface")
    if surface is None:
        return False

    char_out = group_node.outputs.get("Character") or (
        group_node.outputs[0] if group_node.outputs else None
    )
    if char_out is None:
        return False

    # Drop Character → Surface (or any current Surface source from Arc).
    while surface.is_linked:
        links.remove(surface.links[0])

    mixer = nodes.new("ShaderNodeGroup")
    mixer.node_tree = mixer_tree
    mixer.label = "CurvatureID_Override"
    mixer.name = "CurvatureID_Override"
    mixer.location = (
        float(group_node.location.x) + 280.0,
        float(group_node.location.y),
    )
    output_node.location = (
        float(mixer.location.x) + 260.0,
        float(output_node.location.y),
    )

    if "Shader" in mixer.inputs:
        links.new(char_out, mixer.inputs["Shader"])
    shader_out = mixer.outputs.get("Shader") or (
        mixer.outputs[0] if mixer.outputs else None
    )
    if shader_out is not None:
        links.new(shader_out, surface)

    # Material ID Map ← OCM / Main Texture Color; Override left empty.
    ocm = _find_ocm_image_node(nodes, group_node)
    mid = mixer.inputs.get("Material ID Map")
    if ocm is not None and mid is not None and "Color" in ocm.outputs:
        links.new(ocm.outputs["Color"], mid)

    # ColorMask ← main ColorMask image (Debug mode 3).
    cm_sock = mixer.inputs.get("ColorMask")
    if cm_sock is not None:
        cm_node = _find_colormask_image_node(nodes, group_node)
        if cm_node is not None and "Color" in cm_node.outputs:
            try:
                links.new(cm_node.outputs["Color"], cm_sock)
            except Exception:
                pass

    enable = mixer.inputs.get("Enable")
    if enable is not None:
        try:
            enable.default_value = 0.0
        except Exception:
            pass
    debug = mixer.inputs.get("Debug")
    if debug is not None:
        try:
            debug.default_value = 0
        except Exception:
            pass
    color_n = mixer.inputs.get("Color N")
    if color_n is not None:
        try:
            color_n.default_value = 6
        except Exception:
            pass
    return True



def _find_colormask_image_node(nodes, group_node):
    """Find the clothing ColorMask TexImage (not OCM / normals)."""
    # Prefer image already wired into ColorMask_XYZ groups.
    for node in nodes:
        if getattr(node, "type", "") != "GROUP":
            continue
        tree = getattr(node, "node_tree", None)
        tname = (getattr(tree, "name", "") or "") if tree else ""
        if not tname.startswith("ColorMask_XYZ"):
            continue
        sock = node.inputs.get("ColorMask") if node.inputs else None
        if sock is not None and getattr(sock, "is_linked", False) and sock.links:
            src = sock.links[0].from_node
            if src is not None and src.type == "TEX_IMAGE":
                return src
    for node in nodes:
        if node.type != "TEX_IMAGE" or node.image is None:
            continue
        name = (node.image.name or "").lower()
        label = (node.label or "").lower()
        blob = f"{name} {label}"
        if "colormask" in blob.replace("_", "") or "color_mask" in blob:
            if "occlusion" in blob or "curvature" in blob or "materialid" in blob:
                continue
            return node
    return None


# Goalie Shirt live blend: physical top-left of the MI param grid region.
# Columns = parameter number (1, 2, …); rows = kind (BaseRoughness, …).
_MI_PARAM_GRID_FALLBACK_X = -9263.12

_MI_PARAM_GRID_FALLBACK_Y = -2769.48

_MI_PARAM_NAME_RE = re.compile(r"^(\d+)_(.+)$")

# Preferred row order within each numbered column (unknowns follow, A–Z).
_MI_PARAM_KIND_ORDER = (
    "BaseRoughness",
    "BaseMetallicity",
    "BaseSpecular",
    "EdgeRoughness",
    "EdgeMetallicity",
    "EdgeSpecular",
    "CreaseRoughness",
    "CreaseMetallicity",
    "CreaseSpecular",
    "BaseRoughnessTiling",
    "BaseTextureStrength",
    "ColorSchemeBlend",
)

_MI_PARAM_KIND_RANK = {k: i for i, k in enumerate(_MI_PARAM_KIND_ORDER)}


# Leftover inspection colour RGB row (ColorMaskSwatch, Pattern*, …).
# Origin = live Goalie UpperBody 5_ColorMaskSwatch placement.
# Connected ColorABC / Base|Crease|Edge overlays are NEVER included.
_UNCONNECTED_COLOUR_ROW_X = -2618.32

_UNCONNECTED_COLOUR_ROW_Y = 1287.78

_UNCONNECTED_COLOUR_STEP_X = 280.0

_UNCONNECTED_COLOUR_LABEL_RE = re.compile(
    r"(?i)^\d+_(?:ColorMaskSwatch|PatternSwatch(?:Mask)?|PatternColor[ABC]|EmissiveColorOverlay)$"
)

_UNCONNECTED_COLOUR_ANCHOR_RE = re.compile(r"(?i)^5_ColorMaskSwatch$")

_COLOR_MASK_SWATCH_RE = re.compile(r"(?i)^\d+_ColorMaskSwatch$")


# Left-side unconnected TA mask cluster (Goalie live capture).
_UNCONNECTED_TA_ORIGIN_X = -5441.27

_UNCONNECTED_TA_ORIGIN_Y = -1343.98

_UNCONNECTED_TA_COL_W = 320.0

_UNCONNECTED_TA_ROW_H = -490.0

_UNCONNECTED_TA_COLS = 2

_UNCONNECTED_TA_CLUSTER_X_MAX = -4800.0

_TA_MASK_SLICE_RE = re.compile(r"(?i)masks_(\d+)\.png$")



def _colour_node_label(node) -> str:
    return (getattr(node, "label", None) or getattr(node, "name", None) or "").strip()



def _rgb_node_is_linked(node) -> bool:
    try:
        if any(o.links for o in node.outputs):
            return True
        if any(i.links for i in node.inputs):
            return True
    except Exception:
        return False
    return False



def _is_unconnected_colour_swatch_node(node) -> bool:
    """True only for leftover ColorMaskSwatch / Pattern* / EmissiveColorOverlay RGB.

    Connected ColorABC and zone overlays are excluded — the horizontal row must
    not fight user/NCT palette placement.
    """
    if getattr(node, "bl_idname", "") != "ShaderNodeRGB":
        return False
    if getattr(node, "type", "") == "FRAME":
        return False
    if _rgb_node_is_linked(node):
        return False
    label = _colour_node_label(node)
    if not label:
        return False
    # Explicit leftovers only — no broad color/swatch/tint fallback.
    return bool(_UNCONNECTED_COLOUR_LABEL_RE.match(label))



def _is_legacy_unconnected_colour_grid(x: float, y: float) -> bool:
    """Old 2-column leftover colour stack at (-1900, -1100)."""
    return abs(float(x) - (-1900.0)) < 2.0 and abs(float(y) - (-1100.0)) < 2.0



def _find_unconnected_colour_row_origin(nodes, candidates=None):
    """Return (x, y, anchor_node_or_None) for the leftover colour row."""
    pool = list(candidates) if candidates is not None else [
        n for n in nodes if _is_unconnected_colour_swatch_node(n)
    ]
    anchor = None
    for node in pool:
        if _UNCONNECTED_COLOUR_ANCHOR_RE.match(_colour_node_label(node)):
            anchor = node
            break
    if anchor is None:
        for node in pool:
            if _COLOR_MASK_SWATCH_RE.match(_colour_node_label(node)):
                anchor = node
                break
    if anchor is not None:
        loc = getattr(anchor, "location", None)
        if loc is not None:
            x, y = float(loc[0]), float(loc[1])
            # Pre-2.18.50 builder left swatches on the legacy grid — use capture.
            if _is_legacy_unconnected_colour_grid(x, y):
                return (_UNCONNECTED_COLOUR_ROW_X, _UNCONNECTED_COLOUR_ROW_Y, anchor)
            return (x, y, anchor)

    layout = _load_nct_layout_index()
    for key in ("label:5_ColorMaskSwatch", "label:4_ColorMaskSwatch"):
        entry = layout.get(key)
        if entry and entry.get("location"):
            loc = entry["location"]
            try:
                return (float(loc[0]), float(loc[1]), None)
            except (TypeError, ValueError, IndexError):
                pass
    return (_UNCONNECTED_COLOUR_ROW_X, _UNCONNECTED_COLOUR_ROW_Y, None)



def _infer_unconnected_colour_step_x(candidates, origin_y: float) -> float:
    """Use existing horizontal spacing when several leftovers already share the row Y."""
    xs = []
    for node in candidates:
        loc = getattr(node, "location", None)
        if loc is None:
            continue
        if abs(float(loc[1]) - origin_y) > 40.0:
            continue
        xs.append(float(loc[0]))
    xs = sorted(set(round(x, 2) for x in xs))
    gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1) if xs[i + 1] - xs[i] > 40.0]
    if gaps:
        gaps.sort()
        return float(gaps[len(gaps) // 2])
    return _UNCONNECTED_COLOUR_STEP_X



def place_unconnected_colour_swatches(nodes) -> int:
    """Place unconnected ColorMaskSwatch / Pattern* RGB leftovers in a horizontal row.

    Starts at the live ``5_ColorMaskSwatch`` location (Goalie UpperBody capture),
    then steps +X. Connected ColorABC / overlay / Arc-wired colours are untouched.
    """
    candidates = [n for n in nodes if _is_unconnected_colour_swatch_node(n)]
    if not candidates:
        return 0

    origin_x, origin_y, anchor = _find_unconnected_colour_row_origin(nodes, candidates)
    step_x = _infer_unconnected_colour_step_x(candidates, origin_y)

    rest = sorted(
        (n for n in candidates if n is not anchor),
        key=lambda n: _colour_node_label(n).lower(),
    )
    ordered = ([anchor] if anchor is not None else []) + rest
    if anchor is None:
        ordered = sorted(candidates, key=lambda n: _colour_node_label(n).lower())

    placed = 0
    for i, node in enumerate(ordered):
        try:
            node.parent = None
        except Exception:
            pass
        try:
            node.location = (origin_x + i * step_x, origin_y)
            placed += 1
        except Exception:
            continue
    return placed



def _ta_mask_image_basename(node) -> str:
    return _ta_texture_basename(node)



def _is_unconnected_ta_mask_node(node) -> bool:
    """True for TA_*Masks_N image textures with no output links."""
    if getattr(node, "bl_idname", "") != "ShaderNodeTexImage":
        return False
    if getattr(node, "type", "") == "FRAME":
        return False
    try:
        if any(o.links for o in node.outputs):
            return False
    except Exception:
        return False
    fname = _ta_mask_image_basename(node)
    if not fname.upper().startswith("TA_"):
        return False
    return bool(_TA_MASK_SLICE_RE.search(fname))



def _ta_mask_slice_index(node) -> int:
    m = _TA_MASK_SLICE_RE.search(_ta_mask_image_basename(node))
    return int(m.group(1)) if m else 10**9



def _infer_unconnected_ta_grid(cluster_nodes):
    """Infer (origin_x, origin_y, col_w, row_h) from the existing left TA stack."""
    origin_x = _UNCONNECTED_TA_ORIGIN_X
    origin_y = _UNCONNECTED_TA_ORIGIN_Y
    col_w = _UNCONNECTED_TA_COL_W
    row_h = _UNCONNECTED_TA_ROW_H
    if not cluster_nodes:
        return origin_x, origin_y, col_w, row_h

    xs = sorted({round(float(n.location[0]), 1) for n in cluster_nodes})
    ys = sorted({round(float(n.location[1]), 1) for n in cluster_nodes}, reverse=True)
    if xs:
        origin_x = float(xs[0])
    if ys:
        origin_y = float(ys[0])
    if len(xs) >= 2:
        gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1) if xs[i + 1] - xs[i] > 40.0]
        if gaps:
            gaps.sort()
            col_w = float(gaps[len(gaps) // 2])
    if len(ys) >= 2:
        gaps = [ys[i] - ys[i + 1] for i in range(len(ys) - 1) if ys[i] - ys[i + 1] > 40.0]
        if gaps:
            gaps.sort()
            row_h = -float(gaps[len(gaps) // 2])
    return origin_x, origin_y, col_w, row_h



def place_unconnected_ta_textures(nodes) -> int:
    """Park unconnected TA mask textures into the left-side 2-column cluster.

    Connected TA masks (ColorMask / Arc feeds) are untouched. Orphans that NCT
    pinned mid-graph are appended into free slots beside the existing left stack.
    """
    unconnected = [n for n in nodes if _is_unconnected_ta_mask_node(n)]
    if not unconnected:
        return 0

    cluster = []
    orphans = []
    for node in unconnected:
        loc = getattr(node, "location", None)
        if loc is not None and float(loc[0]) < _UNCONNECTED_TA_CLUSTER_X_MAX:
            cluster.append(node)
        else:
            orphans.append(node)
    if not orphans:
        return 0

    origin_x, origin_y, col_w, row_h = _infer_unconnected_ta_grid(cluster)
    occupied = set()
    for node in cluster:
        loc = getattr(node, "location", None)
        if loc is None:
            continue
        occupied.add((round(float(loc[0]), 1), round(float(loc[1]), 1)))

    def _slot_pos(slot_i: int):
        col = slot_i % _UNCONNECTED_TA_COLS
        row = slot_i // _UNCONNECTED_TA_COLS
        return (origin_x + col * col_w, origin_y + row * row_h)

    # Skip slots already used by the left cluster (tolerant match).
    def _slot_free(pos):
        x, y = pos
        for ox, oy in occupied:
            if abs(ox - x) < 60.0 and abs(oy - y) < 60.0:
                return False
        return True

    slot_i = 0
    placed = 0
    for node in sorted(orphans, key=_ta_mask_slice_index):
        while True:
            pos = _slot_pos(slot_i)
            slot_i += 1
            if _slot_free(pos):
                break
            if slot_i > 64:
                break
        try:
            node.parent = None
        except Exception:
            pass
        try:
            node.location = pos
            node.hide = False
            if hasattr(node, "hide_preview"):
                try:
                    node.hide_preview = True
                except Exception:
                    pass
            occupied.add((round(pos[0], 1), round(pos[1], 1)))
            placed += 1
        except Exception:
            continue
    return placed



def _find_mi_param_grid_anchor(nodes):
    """Return (x, y) of an existing numbered ``N_BaseRoughness`` Value node, if any.

    Prefer the lowest zone number so re-layouts keep column 1 at the left.
    Ignores TA image nodes — those live in a different region of the graph.
    """
    best = None
    for node in nodes:
        if getattr(node, "bl_idname", "") != "ShaderNodeValue":
            continue
        label = (getattr(node, "label", None) or "").strip()
        m = _MI_PARAM_NAME_RE.match(label)
        if not m or m.group(2) != "BaseRoughness":
            continue
        loc = getattr(node, "location", None)
        if loc is None:
            continue
        zone = int(m.group(1))
        score = (zone, float(loc[0]), -float(loc[1]))
        if best is None or score < best[0]:
            best = (score, float(loc[0]), float(loc[1]))
    if best is None:
        return None
    return (best[1], best[2])



def _mi_param_kind_sort_key(kind: str):
    rank = _MI_PARAM_KIND_RANK.get(kind)
    if rank is not None:
        return (0, rank, kind)
    return (1, 0, kind.lower())



def _mi_param_grid_slots(entries: list):
    """Split ``(name, value)`` entries into numbered grid cells + leftovers.

    Returns ``(cells, numbers, kinds, leftovers)`` where
    ``cells[(num, kind)] = (name, value)``, ``numbers`` are sorted column
    indices, and ``kinds`` is the unified row order.
    """
    cells: dict[tuple[int, str], tuple] = {}
    kinds_seen: set[str] = set()
    leftovers: list = []
    for name, value in entries:
        m = _MI_PARAM_NAME_RE.match(name or "")
        if not m:
            leftovers.append((name, value))
            continue
        num = int(m.group(1))
        kind = m.group(2)
        key = (num, kind)
        # First wins — MI dumps can repeat the same parameter name.
        if key not in cells:
            cells[key] = (name, value)
            kinds_seen.add(kind)
    numbers = sorted({n for n, _ in cells})
    kinds = sorted(kinds_seen, key=_mi_param_kind_sort_key)
    return cells, numbers, kinds, leftovers



def place_mi_parameter_nodes(
    nodes,
    links,
    mi_params: dict,
    base_x: float = _MI_PARAM_GRID_FALLBACK_X,
    base_y: float = _MI_PARAM_GRID_FALLBACK_Y,
):
    """Place MI scalar/vector Value/RGB nodes left of the TA mask section.

    Grid origin is the Goalie Shirt param region (x~-9263, y~-2769), or an
    existing numbered ``N_BaseRoughness`` Value node when already present.

    Layout: **columns = parameter number** (1, 2, 3, …), **rows = kind**
    (BaseRoughness, BaseMetallicity, …). Unnumbered leftovers fill extra columns
    after the numbered block. Spacing: 300×280. NCT per-label snaps are skipped
    so outfit-varying param sets stay in a consistent grid (NCT still stores the
    old JSON-order positions).
    """
    anchor = _find_mi_param_grid_anchor(nodes)
    if anchor is not None:
        base_x, base_y = anchor

    col_w = 300
    row_h = -280

    def _place_value_nodes(entries, origin_x, origin_y):
        cells, numbers, kinds, leftovers = _mi_param_grid_slots(entries)
        for col_i, num in enumerate(numbers):
            for row_i, kind in enumerate(kinds):
                cell = cells.get((num, kind))
                if cell is None:
                    continue
                name, value = cell
                node = nodes.new("ShaderNodeValue")
                node.label = name
                node.outputs[0].default_value = value
                node.location = (origin_x + col_i * col_w, origin_y + row_i * row_h)
        # Unnumbered scalars: continue to the right, stacked top→bottom then next col.
        per_col = max(len(kinds), 8)
        leftover_origin_x = origin_x + len(numbers) * col_w
        if numbers and leftovers:
            leftover_origin_x += 140
        for i, (name, value) in enumerate(leftovers):
            node = nodes.new("ShaderNodeValue")
            node.label = name
            node.outputs[0].default_value = value
            col = i // per_col
            r = i % per_col
            node.location = (leftover_origin_x + col * col_w, origin_y + r * row_h)
        n_cols = len(numbers)
        if leftovers:
            n_cols += ((len(leftovers) - 1) // per_col) + 1
            if numbers:
                # Account for the 140 gap before leftovers.
                return leftover_origin_x + (((len(leftovers) - 1) // per_col) + 1) * col_w
        return origin_x + max(n_cols, 1) * col_w

    scalars = list(mi_params.get("scalars") or [])
    vectors = list(mi_params.get("vectors") or [])

    next_x = base_x
    if scalars:
        next_x = _place_value_nodes(scalars, base_x, base_y)

    if vectors:
        vec_x = next_x + (140 if scalars else 0)
        cells, numbers, kinds, leftovers = _mi_param_grid_slots(vectors)
        for col_i, num in enumerate(numbers):
            for row_i, kind in enumerate(kinds):
                cell = cells.get((num, kind))
                if cell is None:
                    continue
                name, rgba = cell
                node = nodes.new("ShaderNodeRGB")
                node.label = name
                node.outputs[0].default_value = rgba
                node.location = (vec_x + col_i * col_w, base_y + row_i * row_h)
        per_col = max(len(kinds), 8)
        leftover_origin_x = vec_x + len(numbers) * col_w
        if numbers and leftovers:
            leftover_origin_x += 140
        for i, (name, rgba) in enumerate(leftovers):
            node = nodes.new("ShaderNodeRGB")
            node.label = name
            node.outputs[0].default_value = rgba
            col = i // per_col
            r = i % per_col
            node.location = (leftover_origin_x + col * col_w, base_y + r * row_h)


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



# OCM MaterialID bands (Non-Color byte/255 midpoints). Same as palette_calibration.BANDS.
# Eight ladder steps — legacy FModel 9-zone table skipped zone 7 under sRGB decode.
_ARC_DECAL_BANDS = (0.0, 0.2020, 0.3176, 0.4353, 0.5569, 0.6824, 0.8078, 0.9353, 1.01)

_ARC_DECAL_ZONE_COUNT = 8

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



def _find_arc_role_image_node(nodes, group_node, role: str):
    """Find a clothing TexImage for ``basecolor`` / ``normal`` / ``occlusion``."""
    role = (role or "").strip().lower()
    if role in ("occlusion", "ocm", "curvature", "materialid"):
        return _find_ocm_image_node(nodes, group_node)

    sock_names = ()
    needles = ()
    if role in ("basecolor", "base_color", "albedo"):
        sock_names = ("Base Color", "BaseColor", "Albedo")
        needles = ("basecolor", "base_color")
    elif role in ("normal", "normals"):
        sock_names = ("Normal", "Normals", "Normal Map")
        needles = ("normal",)
    else:
        return None

    if group_node is not None:
        for sock_name in sock_names:
            sock = group_node.inputs.get(sock_name)
            if sock is not None and getattr(sock, "is_linked", False) and sock.links:
                src = sock.links[0].from_node
                if src is not None and src.type == "TEX_IMAGE":
                    return src
                # Normal often goes TexImage → Normal Map → group.
                if src is not None and getattr(src, "bl_idname", "") == "ShaderNodeNormalMap":
                    color_in = src.inputs.get("Color")
                    if color_in is not None and color_in.is_linked and color_in.links:
                        tex = color_in.links[0].from_node
                        if tex is not None and tex.type == "TEX_IMAGE":
                            return tex

    for node in nodes or ():
        if getattr(node, "type", "") != "TEX_IMAGE" or node.image is None:
            continue
        name = (node.image.name or "").lower()
        label = (node.label or "").lower()
        path = ""
        try:
            path = (node.image.filepath or "").replace("\\", "/").lower()
        except Exception:
            path = ""
        blob = f"{name} {label} {os.path.basename(path)}"
        if role in ("normal", "normals"):
            if "dirtnormal" in blob or "dirt_normal" in blob:
                continue
            if re.search(r"(^|_|/)normal", blob) and "normals_" not in blob:
                return node
        else:
            if any(n in blob for n in needles):
                return node
    return None



def _resolve_visor_shell_map_paths(psk_path: str = "", skin_json: str = "") -> dict:
    """BaseColor / Normal / OCM PNG paths beside the mesh or clothing skin."""
    folders = []
    for p in (psk_path, skin_json):
        if not p:
            continue
        d = os.path.dirname(p) if (os.path.isfile(p) or str(p).lower().endswith(".psk")) else p
        if d and d not in folders and os.path.isdir(d):
            folders.append(d)
    out = {}
    for folder in folders:
        try:
            pngs = sorted(f for f in os.listdir(folder) if f.lower().endswith(".png"))
        except OSError:
            continue
        for fname in pngs:
            role = ""
            try:
                role = textures.identify_texture(fname) or ""
            except Exception:
                role = ""
            fl = fname.lower()
            if not role:
                if "occlusioncurvaturematerialid" in fl or (
                    "occlusion" in fl and "curvature" in fl
                ):
                    role = "occlusion"
                elif "basecolor" in fl:
                    role = "basecolor"
                elif re.search(r"(^|_)normal", fl) and "normals_" not in fl:
                    role = "normal"
            key = {"occlusion": "Curvature ID", "basecolor": "Base Color",
                   "normal": "Normal"}.get(role)
            if key and key not in out:
                out[key] = os.path.join(folder, fname)
        if len(out) >= 3:
            break
    return out



def _decal_layer_mask_int(raw) -> int:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 255
    if value <= 0.0 or value < 1.0:
        return 255
    iv = int(round(value))
    if iv <= 0:
        return 255
    return min(iv, 255)


def _decal_visibility_layer_mask(mask_int: int) -> int:
    """Cooked ``N_LayerMask`` → bitmask for sticker ``ArcDecalLayerOk``.

    Snooper: ``(mask & (1 << zone)) != 0`` with zone id 0..7 from OCM mid×8.

    Do **not** widen single-bit masks to 255. That was tried for Goalie Shirt
    (colour-ownership scalars bleeding onto UseDecal slots) but it also lets
    pants stickers (e.g. ``LayerMask=4`` → zone 2 only) paint Colour 4 knee pads.

    Invalid / 0 / full ``255`` → no restriction (255). Any other partial mask,
    including single-bit, keeps its cooked bits.
    """
    m = int(mask_int) & _ARC_DECAL_ALL_ZONES_MASK
    if m <= 0 or m == _ARC_DECAL_ALL_ZONES_MASK:
        return _ARC_DECAL_ALL_ZONES_MASK
    return m


def _should_emit_decal_layer_mask_node(mask_int: int) -> bool:
    """True when the cooked LayerMask should appear as a graph node.

    Any partial mask (incl. single-bit) gets a node so the MIC value is visible
    and wired into alpha. Full ``255`` / empty stay omitted.
    """
    m = int(mask_int) & _ARC_DECAL_ALL_ZONES_MASK
    return 0 < m < _ARC_DECAL_ALL_ZONES_MASK


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

    rotation_rad = math.radians(rotation * -360.0)
    if method == "INVERTED_ROTATION":
        rotation_rad = -rotation_rad
    centre_u = 0.5 - uv_u
    centre_v = 0.5 + uv_v
    cos_t = math.cos(rotation_rad)
    sin_t = math.sin(rotation_rad)

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



def _decal_slot_is_used(decal: dict) -> bool:
    """True when the MI slot has a DecalColor reference worth building nodes for."""
    if not decal:
        return False
    tex = (decal.get("texture") or "").strip()
    if not tex:
        return False
    try:
        scale = float(decal.get("scale", 1.0))
    except (TypeError, ValueError):
        scale = 1.0
    if abs(scale) < 1e-8:
        return False
    return True



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
    layer_stack = []  # legacy external PBR only (pre-internal Decal Data)
    _arc_tree = getattr(group_node, "node_tree", None)
    _decal_proc_inside = utils.arc_texturer_has_internal_decal_data(_arc_tree)
    _color_ov_inside = utils.arc_texturer_has_internal_decal_color_override(_arc_tree)
    # Resolve Decal Data once — legacy path used to ensure/find per decal slot.
    _dd_group = None
    if not _decal_proc_inside and utils.ensure_decal_data_node_group():
        _dd_group = utils.find_node_group(utils._DECAL_DATA_GROUP)
    # Lazy frame: only create when at least one used decal builds nodes.
    # Unused MI slots leave Arc Decal/DN sockets at defaults (intact).
    decal_frame = None
    used_build_idx = 0

    for decal in (decals or []):
        if not _decal_slot_is_used(decal):
            continue
        tex_stem = (decal.get("texture") or "").strip()
        tex_fpath = resolve_decal_texture(
            tex_stem, decal.get("texture_path", ""), decal_folder, search_dirs=search_dirs
        )
        # No resolvable DecalColor and not already cached → skip placeholders.
        if not tex_fpath and tex_stem not in _decal_tex_cache:
            continue
        if tex_stem in _decal_tex_cache and _decal_tex_cache[tex_stem] is None and not tex_fpath:
            continue

        idx = decal["index"]
        col_x = 1200 + used_build_idx * 520
        used_build_idx += 1
        row_y = 900
        extension = decal_image_extension(decal)

        if decal_frame is None:
            decal_frame = nodes.new("NodeFrame")
            decal_frame.label = f"Decals [{decal_method}]"
            decal_frame.label_size = 20

        if decal_uv_node is None:
            decal_uv_node = nodes.new("ShaderNodeTexCoord")
            decal_uv_node.label = "Decal UV"
            decal_uv_node.location = (col_x - 250, row_y + 200)
            decal_uv_node.parent = decal_frame

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
            # Prefer PNG IHDR aspect (no full decode); else cached/loaded image.
            probe_path = tex_fpath or resolve_decal_texture(
                tex_stem, decal.get("texture_path", ""),
                decal_folder, search_dirs=search_dirs,
            )
            if probe_path:
                ratio = _width_ratio_from_path(probe_path)
                if ratio <= 0.0:
                    probe_image = _load_image_cached(probe_path)
                    if probe_image is not None and probe_image.size[1] > 0:
                        ratio = float(probe_image.size[0]) / float(probe_image.size[1])
                        _WIDTH_RATIO_BY_PATH[_norm_path_key(probe_path)] = ratio
                if ratio > 0.0:
                    width_ratio = ratio
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
                              f"Rot={math.degrees(rotation_rad):.1f}°  {extension}"
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

        if tex_stem not in _decal_tex_cache:
            if tex_fpath:
                img = _load_image_cached(tex_fpath)
                if img is not None:
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
                    data_img = _load_image_cached(data_fpath)
                    if data_img is not None:
                        data_img.colorspace_settings.name = "Non-Color"
                        data_node = nodes.new("ShaderNodeTexImage")
                        data_node.image = data_img
                        data_node.label = f"Decal {idx} Normal: {data_stem}"
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
                    _decal_data_cache[data_stem] = None
            else:
                prev_data = _decal_data_cache[data_stem]
                if prev_data is not None:
                    dup_data = nodes.new("ShaderNodeTexImage")
                    dup_data.image = prev_data.image
                    dup_data.label = f"Decal {idx} Normal: {data_stem}"
                    dup_data.interpolation = "Cubic"
                    dup_data.extension = extension
                    dup_data.location = (col_x, row_y)
                    dup_data.parent = decal_frame
                    links.new(placed_uv, dup_data.inputs["Vector"])
                    data_tex_node = dup_data
                    row_y -= 300

        color_a = decal.get("color_a")
        color_b = decal.get("color_b")

        # ArcTexturer 2.18.46+: ColorA/B + ColorOverride Fac live on Arc sockets
        # (internal BW→threshold→Mix). Outside keeps coords / texture / normal only.
        if not _color_ov_inside:
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
                rgb_node.outputs[0].default_value = (
                    color_a[0], color_a[1], color_a[2], color_a[3]
                )
                rgb_node.location = (col_x, row_y)
                rgb_node.parent = decal_frame
                row_y -= 200

            # M_Character_Layered's {slot}_ColorOverride is a continuous lerp from
            # sampled RGB (0) to the ColorA/B mask result (1). Keep original artwork
            # for partial values such as Goalie Gold's chest patch (0.22557278).
            if (
                color_tex_node is not None
                and color_override < 0.9999
                and (ramp_node or rgb_node)
            ):
                color_mix_node = nodes.new("ShaderNodeMixRGB")
                color_mix_node.blend_type = "MIX"
                color_mix_node.label = (
                    f"Decal {idx} Original ↔ Override ({color_override:.3f})"
                )
                color_mix_node.location = (col_x + 280, row_y)
                color_mix_node.parent = decal_frame
                color_mix_node.inputs["Fac"].default_value = color_override
                color_mix_node["arc_decal_original_color"] = True
                color_mix_node["arc_decal_color_override"] = color_override
                row_y -= 220

        # ArcTexturer: emit LayerMask for any partial cooked mask; gate with the
        # same bits (ArcDecalLayerOk). Single-bit masks are real zone filters
        # (e.g. pants LayerMask=4 → zone 2 only — keep stickers off Colour 4 pads).
        layer_gate_sock = f"Decal LayerGate {idx}"
        _layer_gate_sock = (not _decal_proc_inside) and (layer_gate_sock in group_node.inputs)
        mask_ramp = None
        vis_mask = _decal_visibility_layer_mask(layer_mask_int)
        need_zone_mask = (
            color_tex_node is not None
            and _should_emit_decal_layer_mask_node(layer_mask_int)
        )

        if need_zone_mask:
            if ocm_node is None:
                if not ocm_warned:
                    print("Arc Raiders PSK Importer: No OCM texture for decal LayerMask; "
                          "decals will not be zone-masked.")
                    ocm_warned = True
            else:
                if ocm_sep_node is None:
                    ocm_sep_node = nodes.new("ShaderNodeSeparateColor")
                    ocm_sep_node.label = "OCM → Material ID"
                    # Match NodeConnectionTest GoalieShirt placement.
                    ocm_sep_node.location = (-687.5, 351.2)
                    ocm_sep_node.parent = decal_frame
                    links.new(ocm_node.outputs["Color"], ocm_sep_node.inputs["Color"])

                # Sit on the color-texture row (NCT may later move ColorTex; we re-snap).
                tex_x = float(color_tex_node.location.x)
                tex_y = float(color_tex_node.location.y)
                if data_tex_node is not None:
                    lm_x = float(data_tex_node.location.x) + _DECAL_LAYERMASK_X_AFTER_DATA
                else:
                    lm_x = tex_x + _DECAL_LAYERMASK_X_AFTER_COLOR
                lm_y = tex_y

                use_group = (
                    not _layer_gate_sock
                    and utils.ensure_decal_layer_mask_node_group()
                )
                if use_group:
                    lm_tree = utils.find_node_group(utils._DECAL_LAYER_MASK_GROUP)
                    layer_mask_mul = nodes.new("ShaderNodeGroup")
                    layer_mask_mul.node_tree = lm_tree
                    layer_mask_mul.label = f"Decal {idx} LayerMask ({vis_mask})"
                    layer_mask_mul.location = (lm_x, lm_y)
                    layer_mask_mul.parent = decal_frame
                    layer_mask_mul["arc_decal_layer_mask"] = vis_mask
                    layer_mask_mul["arc_decal_layer_mask_raw"] = layer_mask_int
                    layer_mask_mul["arc_decal_idx"] = idx
                    try:
                        layer_mask_mul.inputs["LayerMask"].default_value = float(
                            vis_mask
                        )
                    except Exception:
                        pass
                    links.new(
                        ocm_sep_node.outputs["Blue"],
                        layer_mask_mul.inputs["Material ID"],
                    )
                    links.new(
                        color_tex_node.outputs["Alpha"],
                        layer_mask_mul.inputs["Alpha"],
                    )
                else:
                    # Legacy LayerGate socket, or group unavailable: ColorRamp path.
                    mask_ramp = nodes.new("ShaderNodeValToRGB")
                    mask_ramp.label = f"Decal {idx} LayerMask ({vis_mask})"
                    mask_ramp.location = (lm_x, lm_y)
                    mask_ramp.parent = decal_frame
                    mask_ramp["arc_decal_layer_mask"] = vis_mask
                    mask_ramp["arc_decal_layer_mask_raw"] = layer_mask_int
                    mask_ramp["arc_decal_idx"] = idx
                    _build_layer_mask_ramp(mask_ramp.color_ramp, vis_mask)
                    links.new(ocm_sep_node.outputs["Blue"], mask_ramp.inputs["Fac"])

                    if _layer_gate_sock:
                        links.new(
                            mask_ramp.outputs["Alpha"],
                            group_node.inputs[layer_gate_sock],
                        )
                    else:
                        layer_mask_mul = nodes.new("ShaderNodeMath")
                        layer_mask_mul.operation = "MULTIPLY"
                        layer_mask_mul.label = f"Decal {idx} LayerMask × Alpha"
                        layer_mask_mul.location = (lm_x + 280.0, lm_y)
                        layer_mask_mul.parent = decal_frame
                        layer_mask_mul["arc_decal_layer_mask"] = layer_mask_int
                        layer_mask_mul["arc_decal_idx"] = idx
                        links.new(
                            color_tex_node.outputs["Alpha"], layer_mask_mul.inputs[0]
                        )
                        links.new(
                            mask_ramp.outputs["Alpha"], layer_mask_mul.inputs[1]
                        )
        elif _layer_gate_sock:
            try:
                group_node.inputs[layer_gate_sock].default_value = 1.0
            except Exception:
                pass

        n = idx
        colour_out = None
        alpha_out = None
        if color_tex_node is not None:
            if f"Decal {n}" in group_node.inputs:
                if _color_ov_inside:
                    # Raw sticker RGB into Arc; ColorA/B + ColorOverride Fac on sockets.
                    colour_out = color_tex_node.outputs["Color"]
                    ca_sock = f"Decal {n} ColorA"
                    cb_sock = f"Decal {n} ColorB"
                    co_sock = f"Decal {n} ColorOverride"
                    if color_a and ca_sock in group_node.inputs:
                        try:
                            group_node.inputs[ca_sock].default_value = (
                                float(color_a[0]), float(color_a[1]),
                                float(color_a[2]), float(color_a[3]),
                            )
                        except Exception:
                            pass
                    if cb_sock in group_node.inputs:
                        try:
                            # Solid ColorA when ColorB is absent (legacy RGB path).
                            src = color_b if color_b else color_a
                            if src:
                                group_node.inputs[cb_sock].default_value = (
                                    float(src[0]), float(src[1]),
                                    float(src[2]), float(src[3]),
                                )
                        except Exception:
                            pass
                    if co_sock in group_node.inputs:
                        try:
                            # No ColorA/B authored → force pass-through (keep sampled RGB).
                            fac = (
                                0.0
                                if not color_a and not color_b
                                else float(color_override)
                            )
                            group_node.inputs[co_sock].default_value = fac
                        except Exception:
                            pass
                else:
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
                    # Prefer externally zone-masked alpha when LayerMask is restricted.
                    # Otherwise raw sticker alpha (internal LayerGate stays at 255 / open).
                    if layer_mask_mul is not None:
                        alpha_out = (
                            layer_mask_mul.outputs["Alpha"]
                            if "Alpha" in layer_mask_mul.outputs
                            else layer_mask_mul.outputs[0]
                        )
                    else:
                        alpha_out = color_tex_node.outputs["Alpha"]
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
                if layer_mask_mul is not None:
                    alpha_out = (
                        layer_mask_mul.outputs["Alpha"]
                        if "Alpha" in layer_mask_mul.outputs
                        else layer_mask_mul.outputs[0]
                    )
                else:
                    alpha_out = color_tex_node.outputs["Alpha"]

        rough_out = None
        metal_out = None
        if data_tex_node is not None:
            if _decal_proc_inside and f"DN {n}" in group_node.inputs:
                # Raw packed DecalData into Arc; unpack + rough/metal mix inside.
                links.new(data_tex_node.outputs["Color"], group_node.inputs[f"DN {n}"])
                if f"DN Alpha {n}" in group_node.inputs:
                    links.new(
                        data_tex_node.outputs["Alpha"],
                        group_node.inputs[f"DN Alpha {n}"],
                    )
                if f"DN Enable {n}" in group_node.inputs:
                    try:
                        group_node.inputs[f"DN Enable {n}"].default_value = 1.0
                    except Exception:
                        pass
            else:
                # Legacy: external Decal Data (or inline) + optional PBR stack.
                if _dd_group is not None:
                    dd = nodes.new("ShaderNodeGroup")
                    dd.node_tree = _dd_group
                    dd.label = f"Decal {idx} Data"
                    dd.location = (col_x + 280, row_y)
                    dd.parent = decal_frame
                    links.new(data_tex_node.outputs["Color"], dd.inputs["Color"])
                    if "Alpha" in dd.inputs:
                        links.new(data_tex_node.outputs["Alpha"], dd.inputs["Alpha"])
                    if f"DN {n}" in group_node.inputs:
                        links.new(dd.outputs["Normal"], group_node.inputs[f"DN {n}"])
                        if f"DN Enable {n}" in group_node.inputs:
                            try:
                                group_node.inputs[f"DN Enable {n}"].default_value = 1.0
                            except Exception:
                                pass
                    rough_out = dd.outputs["Roughness"] if "Roughness" in dd.outputs else None
                    metal_out = (
                        dd.outputs["Metallic"]
                        if "Metallic" in dd.outputs
                        else data_tex_node.outputs["Alpha"]
                    )
                else:
                    # Legacy inline reconstruct (pre-Decal Data group).
                    sep = nodes.new("ShaderNodeSeparateColor")
                    sep.label = f"Decal {idx} Data RGB"
                    sep.location = (col_x + 280, row_y)
                    sep.parent = decal_frame
                    links.new(data_tex_node.outputs["Color"], sep.inputs["Color"])

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
                    metal_out = data_tex_node.outputs["Alpha"]

        if (
            not _decal_proc_inside
            and colour_out is not None
            and alpha_out is not None
            and (rough_out is not None or metal_out is not None)
        ):
            stack_alpha = alpha_out
            if _layer_gate_sock and mask_ramp is not None:
                gate_mul = nodes.new("ShaderNodeMath")
                gate_mul.operation = "MULTIPLY"
                gate_mul.label = f"Decal {idx} PBR Alpha"
                gate_mul.location = (col_x + 560, row_y - 40)
                gate_mul.parent = decal_frame
                gate_mul.hide = True
                links.new(color_tex_node.outputs["Alpha"], gate_mul.inputs[0])
                links.new(mask_ramp.outputs["Alpha"], gate_mul.inputs[1])
                stack_alpha = gate_mul.outputs[0]
            elif layer_mask_mul is not None:
                stack_alpha = layer_mask_mul.outputs[0]
            layer_stack.append({
                "idx": n,
                "color": colour_out,
                "alpha": stack_alpha,
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
    stem_l = (stem or "").strip().lower()
    path_l = (object_path or "").replace("\\", "/").lower()
    # Engine null stubs — never walk Content / EmbarkScript for these.
    if stem_l in _NULL_DECAL_MARKERS or "decal_null" in stem_l:
        return ""
    if path_l and (
        "decal_null" in path_l
        or ("/embarkscript/" in path_l and "null" in path_l.split("/")[-1])
    ):
        return ""
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



# ---------------------------------------------------------------------------
# Fur (M_FurShells / M_FurLOD) — Principled + displacement, no particle hair
# ---------------------------------------------------------------------------

_FUR_SETUP_V = "v2"

# UE Length is shell extrusion; Blender displacement scale (metres) after clamp.
# Length≈0.1 → ~0.04 m (matches hand-tuned GWRanger UpperBody_Fur).
_FUR_DISP_SCALE_MIN = 0.0015

_FUR_DISP_SCALE_MAX = 0.045

_FUR_SUBDIV_VERT_CAP = 80000

_FUR_GN_SUBDIV_LEVEL = 3

_FUR_GN_MOD_NAME = "ArcFurSubdiv"

_FUR_GN_TREE_PREFIX = "ArcFurSubdivGN"

# Albedo ColorRamp stop positions (fixed — black gives shadow trough).
_FUR_RAMP_BLACK_POS = 0.10909

_FUR_RAMP_ROOT_POS = 0.42955

_FUR_RAMP_TIP_POS = 1.0

# Displacement ColorRamp stop positions.
_FUR_DISP_BLACK_POS = 0.2

_FUR_DISP_TIP_POS = 1.0



def _parse_fur_mi(mi_path: str) -> dict:
    """Parent, scalars, colours and texture ObjectPaths of a fur MI JSON.

    Accepts full MIC exports and thin ``{Textures, Parameters}`` FModel dumps.
    """
    result = {
        "parent": "",
        "scalars": {},
        "colors": {},
        "textures": {},
        "opacity_clip": 0.3333,
        "found": False,
    }
    if not mi_path or not os.path.isfile(mi_path):
        return result
    try:
        with open(mi_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Could not parse fur MI '{mi_path}': {e}")
        return result
    try:
        entry = utils.first_ue_export(data, "MaterialInstanceConstant") or {}
        props = entry.get("Properties", {}) or {}
        parent = props.get("Parent") or {}
        result["parent"] = str(
            parent.get("ObjectName", "") or parent.get("ObjectPath", "") or ""
        )
        for sp in props.get("ScalarParameterValues", []) or []:
            name = sp.get("ParameterInfo", {}).get("Name", "")
            if name:
                result["scalars"][name] = float(sp.get("ParameterValue", 0.0))
        for vp in props.get("VectorParameterValues", []) or []:
            name = vp.get("ParameterInfo", {}).get("Name", "")
            pv = vp.get("ParameterValue", {}) or {}
            if name:
                result["colors"][name] = (
                    float(pv.get("R", 1.0)),
                    float(pv.get("G", 1.0)),
                    float(pv.get("B", 1.0)),
                    float(pv.get("A", 1.0)),
                )
        for tp in props.get("TextureParameterValues", []) or []:
            name = tp.get("ParameterInfo", {}).get("Name", "")
            pv = tp.get("ParameterValue", {}) or {}
            obj_path = str(pv.get("ObjectPath", "") or "")
            if name and obj_path:
                result["textures"][name] = obj_path
        bpo = props.get("BasePropertyOverrides", {}) or {}
        if "OpacityMaskClipValue" in bpo:
            try:
                result["opacity_clip"] = float(bpo.get("OpacityMaskClipValue") or 0.3333)
            except Exception:
                pass

        flat = data[0] if isinstance(data, list) and data else data
        if isinstance(flat, dict):
            params = flat.get("Parameters") or {}
            for name, value in (params.get("Scalars") or {}).items():
                result["scalars"].setdefault(name, float(value))
            for name, value in (params.get("Colors") or {}).items():
                if isinstance(value, dict):
                    result["colors"].setdefault(name, (
                        float(value.get("R", 1.0)),
                        float(value.get("G", 1.0)),
                        float(value.get("B", 1.0)),
                        float(value.get("A", 1.0)),
                    ))
            for name, path in (flat.get("Textures") or {}).items():
                if path:
                    result["textures"].setdefault(name, path)
            flat_bpo = (params.get("Properties") or {}).get("BasePropertyOverrides") or {}
            if "OpacityMaskClipValue" in flat_bpo:
                try:
                    result["opacity_clip"] = float(
                        flat_bpo.get("OpacityMaskClipValue") or result["opacity_clip"]
                    )
                except Exception:
                    pass
        if "AlphaCutoff" in result["scalars"]:
            try:
                result["opacity_clip"] = float(result["scalars"]["AlphaCutoff"])
            except Exception:
                pass
        result["found"] = bool(
            result["scalars"] or result["colors"] or result["textures"] or result["parent"]
        )
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Could not parse fur MI '{mi_path}': {e}")
    return result



def _fur_slot_kind(names, mi_stem: str = "") -> str:
    """Classify a fur slot: ``shells`` | ``lod`` | ``fur`` (base cards / undercoat)."""
    blob = " ".join(str(n or "") for n in (names or ())).lower().replace(" ", "")
    blob += " " + str(mi_stem or "").lower().replace(" ", "")
    compact = blob.replace("_", "")
    if "furlod" in compact or "lodmaterial" in compact:
        return "lod"
    if "furshell" in compact:
        return "shells"
    return "fur"



def _find_fur_mi_in_dir(
    folder: str,
    *,
    kind: str = "fur",
    prefer_stem: str = "",
) -> str:
    """Pick a fur MI beside the mesh or in a colourway folder for the slot kind."""
    if not folder or not os.path.isdir(folder):
        return ""
    try:
        names = sorted(f for f in os.listdir(folder) if textures._is_mi_json_filename(f))
    except OSError:
        return ""
    prefer = (prefer_stem or "").strip().lower()
    if "." in prefer:
        prefer = prefer.split(".", 1)[0]
    kind = (kind or "fur").lower()
    scored = []
    for fname in names:
        path = os.path.join(folder, fname)
        if not textures.is_fur_mi(path):
            continue
        stem = os.path.splitext(fname)[0]
        stem_l = stem.lower()
        compact = stem_l.replace("_", "")
        is_lod = "furlod" in compact
        is_shells = "furshell" in compact
        if kind == "lod":
            score = 0 if is_lod else 20
        elif kind == "shells":
            score = 0 if is_shells else (15 if is_lod else 8)
        else:
            # Base fur: prefer non-LOD non-shells; SK often points at MI_*_Fur.
            score = 0
            if is_lod:
                score += 20
            if is_shells:
                score += 12
        if prefer:
            if stem_l == prefer:
                score -= 8
            elif prefer in stem_l:
                score -= 5
            elif stem_l.startswith(prefer):
                score -= 3
        scored.append((score, stem_l, path))
    if not scored:
        return ""
    scored.sort()
    return scored[0][2]



def _resolve_colorway_fur_mi(
    mi_path: str,
    mi_stem: str,
    slot_name: str,
    skin_json: str,
    psk_folder: str,
) -> str:
    """Prefer colourway folder fur MI (e.g. MI_UpperBody_Fur_Cotton_White)."""
    kind = _fur_slot_kind((slot_name, mi_stem), mi_stem)
    skin_dir = os.path.dirname(skin_json) if skin_json and os.path.isfile(skin_json) else ""
    prefer = mi_stem or ""
    # SK ObjectPath is authoritative when it already resolves to a fur MI of the
    # right kind — colourway search only overrides with a skin-folder variant.
    for folder in (skin_dir, psk_folder):
        found = _find_fur_mi_in_dir(folder, kind=kind, prefer_stem=prefer)
        if found:
            return found
    if mi_path and os.path.isfile(mi_path) and textures.is_fur_mi(mi_path):
        return mi_path
    return mi_path or ""



def _match_fur_material_slot(
    obj,
    slot_name: str,
    slot_index: int,
    used_indices: set,
    mi_stem: str = "",
):
    """Bind SK fur slot → Blender slot without collapsing same-MI sections.

    Prefer slot name, then SK index. MI-stem matching is last-resort only when a
    single unused candidate exists — two sections often share one MI (GWRanger
    ``UpperBody_Fur`` + ``Fur`` both → ``MI_UpperBody_Fur``).
    """
    want_slot = (slot_name or "").strip().lower()
    if want_slot:
        for i, s in enumerate(obj.material_slots):
            if i in used_indices or not s.material:
                continue
            sn = s.material.name.lower().split(".")[0]
            sn = re.sub(r"(_force_rebuild)+$", "", sn)
            if sn == want_slot or sn.endswith("_" + want_slot):
                return s, i
    if (
        slot_index < len(obj.material_slots)
        and slot_index not in used_indices
    ):
        return obj.material_slots[slot_index], slot_index
    want_mi = (mi_stem or "").strip().lower()
    if want_mi:
        hits = []
        for i, s in enumerate(obj.material_slots):
            if i in used_indices or not s.material:
                continue
            sn = s.material.name.lower().split(".")[0]
            sn = re.sub(r"(_force_rebuild)+$", "", sn)
            arc_stem = ""
            try:
                arc_stem = str(s.material.get("arc_mi_stem") or "").strip().lower()
            except Exception:
                arc_stem = ""
            if sn == want_mi or arc_stem == want_mi or sn.endswith("_" + want_mi):
                hits.append((s, i))
        if len(hits) == 1:
            return hits[0]
    return None, -1



def _fur_disp_scale(length: float) -> float:
    # Length≈0 (FurLOD) → tiny relief; typical 0.1 → ~0.04 m.
    base = max(0.0, float(length))
    if base <= 1e-6:
        return _FUR_DISP_SCALE_MIN
    return max(_FUR_DISP_SCALE_MIN, min(_FUR_DISP_SCALE_MAX, base * 0.4))



def _fur_slot_indices(obj) -> list:
    """Material slot indices tagged as fur on this mesh."""
    idxs = []
    if obj is None or not getattr(obj, "material_slots", None):
        return idxs
    for i, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat is None:
            continue
        try:
            if mat.get("arc_fur_setup"):
                idxs.append(i)
                continue
        except Exception:
            pass
        if _slot_is_fur((mat.name,), ""):
            idxs.append(i)
    return idxs



def _set_fur_color_ramp(ramp_node, stops):
    """Set ColorRamp elements to exact (position, rgba) stops."""
    ramp = ramp_node.color_ramp
    ramp.interpolation = "LINEAR"
    # Ensure enough elements.
    while len(ramp.elements) < len(stops):
        ramp.elements.new(1.0)
    # Trim extras from the end (Blender needs ≥1 element).
    while len(ramp.elements) > len(stops) and len(ramp.elements) > 1:
        ramp.elements.remove(ramp.elements[-1])
    for i, (pos, color) in enumerate(stops):
        el = ramp.elements[i]
        el.position = float(pos)
        el.color = (
            float(color[0]),
            float(color[1]),
            float(color[2]),
            float(color[3]) if len(color) > 3 else 1.0,
        )



def _build_fur_subdiv_geometry_nodes(indices: list, level: int = _FUR_GN_SUBDIV_LEVEL):
    """GN: subdivide only faces whose material_index is in ``indices``."""
    key = ",".join(str(i) for i in sorted(set(int(i) for i in indices)))
    tree_name = f"{_FUR_GN_TREE_PREFIX}_{key}_L{int(level)}"
    existing = bpy.data.node_groups.get(tree_name)
    if existing is not None:
        return existing

    ng = bpy.data.node_groups.new(tree_name, "GeometryNodeTree")
    # Interface sockets (Blender 4+/5).
    try:
        ng.interface.new_socket(
            name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry",
        )
        ng.interface.new_socket(
            name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry",
        )
    except Exception:
        try:
            ng.inputs.new("NodeSocketGeometry", "Geometry")
            ng.outputs.new("NodeSocketGeometry", "Geometry")
        except Exception:
            pass

    nodes = ng.nodes
    links = ng.links
    nodes.clear()

    n_in = nodes.new("NodeGroupInput")
    n_in.location = (-900, 0)
    n_out = nodes.new("NodeGroupOutput")
    n_out.location = (700, 0)

    n_mat = nodes.new("GeometryNodeInputMaterialIndex")
    n_mat.location = (-900, -220)

    def _cmp_a_input(cmp_n):
        if "A" in cmp_n.inputs:
            return cmp_n.inputs["A"]
        # INT compare: socket order varies by Blender version.
        for sock in cmp_n.inputs:
            if sock.type == "INT" and sock.name.upper().startswith("A"):
                return sock
        for sock in cmp_n.inputs:
            if sock.type == "INT":
                return sock
        return cmp_n.inputs[0]

    def _cmp_b_input(cmp_n):
        if "B" in cmp_n.inputs:
            return cmp_n.inputs["B"]
        ints = [s for s in cmp_n.inputs if s.type == "INT"]
        if len(ints) >= 2:
            return ints[1]
        return cmp_n.inputs[1] if len(cmp_n.inputs) > 1 else cmp_n.inputs[0]

    # selection = (mat_index == i0) OR (mat_index == i1) OR ...
    selection_socket = None
    x = -650
    for idx in sorted(set(int(i) for i in indices)):
        cmp_n = nodes.new("FunctionNodeCompare")
        cmp_n.data_type = "INT"
        cmp_n.operation = "EQUAL"
        cmp_n.location = (x, -220)
        b_in = _cmp_b_input(cmp_n)
        try:
            b_in.default_value = idx
        except Exception:
            pass
        links.new(n_mat.outputs["Material Index"], _cmp_a_input(cmp_n))
        result = cmp_n.outputs.get("Result") or cmp_n.outputs[0]
        if selection_socket is None:
            selection_socket = result
        else:
            bor = nodes.new("FunctionNodeBooleanMath")
            bor.operation = "OR"
            bor.location = (x, 40)
            links.new(selection_socket, bor.inputs[0])
            links.new(result, bor.inputs[1])
            selection_socket = bor.outputs["Boolean"]
        x += 220

    if selection_socket is None:
        links.new(n_in.outputs["Geometry"], n_out.inputs["Geometry"])
        return ng

    sep = nodes.new("GeometryNodeSeparateGeometry")
    try:
        sep.domain = "FACE"
    except Exception:
        pass
    sep.location = (-200, 0)
    links.new(n_in.outputs["Geometry"], sep.inputs["Geometry"])
    links.new(selection_socket, sep.inputs["Selection"])

    subdiv = nodes.new("GeometryNodeSubdivideMesh")
    subdiv.location = (120, 80)
    subdiv.inputs["Level"].default_value = max(1, min(6, int(level)))
    links.new(sep.outputs["Selection"], subdiv.inputs["Mesh"])

    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (380, 0)
    links.new(subdiv.outputs["Mesh"], join.inputs["Geometry"])
    links.new(sep.outputs["Inverted"], join.inputs["Geometry"])
    links.new(join.outputs["Geometry"], n_out.inputs["Geometry"])
    return ng



def _ensure_fur_displacement_subdiv(obj) -> None:
    """Densify fur material faces only (GN); leave cloth faces alone."""
    if obj is None or getattr(obj, "type", "") != "MESH":
        return
    indices = _fur_slot_indices(obj)
    if not indices:
        return
    mesh = obj.data
    try:
        nverts = len(mesh.vertices) if mesh else 0
    except Exception:
        nverts = 0
    if nverts >= _FUR_SUBDIV_VERT_CAP:
        return

    # Drop legacy whole-object Subsurf from earlier fur setups.
    _remove_named_modifiers(obj, (_FUR_GN_MOD_NAME,))
    for mod in list(obj.modifiers):
        if getattr(mod, "type", "") == "SUBSURF" and "fur" in mod.name.lower():
            try:
                obj.modifiers.remove(mod)
            except Exception:
                pass

    try:
        ng = _build_fur_subdiv_geometry_nodes(indices, level=_FUR_GN_SUBDIV_LEVEL)
    except Exception as e:
        print(f"Arc Raiders: Fur GN subdiv build failed on '{obj.name}': {e}")
        # Fallback: whole-object Subsurf at high levels (matches hand-tuned look).
        try:
            mod = obj.modifiers.new(name=_FUR_GN_MOD_NAME, type="SUBSURF")
            mod.levels = 3
            mod.render_levels = 4
            obj["arc_fur_subdiv"] = "subsurf_fallback"
        except Exception:
            pass
        return

    try:
        mod = obj.modifiers.new(name=_FUR_GN_MOD_NAME, type="NODES")
        mod.node_group = ng
        obj["arc_fur_subdiv"] = "gn"
        obj["arc_fur_subdiv_indices"] = ",".join(str(i) for i in indices)
    except Exception as e:
        print(f"Arc Raiders: Fur GN modifier failed on '{obj.name}': {e}")
        try:
            mod = obj.modifiers.new(name=_FUR_GN_MOD_NAME, type="SUBSURF")
            mod.levels = 3
            mod.render_levels = 4
            obj["arc_fur_subdiv"] = "subsurf_fallback"
        except Exception:
            pass



def _ensure_unique_fur_slot_material(
    obj,
    target,
    index: int,
    mi_path: str = "",
    slot_name: str = "",
):
    """Always give this mesh section its own material datablock.

    GWRanger (and similar) author multiple fur sections that reference the same
    MI — Blender must not leave those slots sharing one datablock.
    """
    slot_token = re.sub(r"[^A-Za-z0-9]+", "_", (slot_name or f"slot{index}").strip())
    slot_token = slot_token.strip("_") or f"slot{index}"
    stem = os.path.splitext(os.path.basename(mi_path or ""))[0] or "Fur"
    obj_name = obj.name if obj is not None else "Fur"
    name = f"{obj_name}_{slot_token}"
    slot_key = f"{index}:{slot_token}:{stem.lower()}"

    current = target.material if target is not None else None
    already_ours = False
    if current is not None:
        try:
            already_ours = str(current.get("arc_fur_slot") or "") == slot_key
        except Exception:
            already_ours = False
    shared = (
        current is not None
        and not already_ours
        and (
            current.users > 1
            or _material_used_by_other_objects(current, obj)
            or sum(1 for s in obj.material_slots if s.material == current) > 1
        )
    )

    if current is None or shared or not already_ours:
        # Fresh datablock per section (copy keeps nothing useful from PSK stubs).
        mat = bpy.data.materials.new(name=name)
        target.material = mat
    else:
        mat = current
        if mat.name != name and not mat.name.startswith(name + "."):
            try:
                mat.name = name
            except Exception:
                pass
    try:
        mat["arc_fur_slot"] = slot_key
        mat["arc_mi_stem"] = stem
    except Exception:
        pass
    return mat



def _setup_fur_material(mat, mi_path: str, psk_path: str = "", obj=None):
    """Single-layer fur: FurMask → ColorRamp (black/Root/Tip) + Disp ramp → Displacement.

    Matches the hand-tuned GWRanger UpperBody_Fur graph. ColorTint ignored.
    """
    del psk_path  # unused; kept for call-site compatibility
    mi = _parse_fur_mi(mi_path)
    if not mat.use_nodes:
        mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    scalars = mi.get("scalars") or {}
    colors = mi.get("colors") or {}
    tiling = float(scalars.get("FurTiling", scalars.get("ColorTiling", 8.0)) or 8.0)
    length = float(scalars.get("Length", 0.1) or 0.0)
    if length <= 1e-6:
        length = float(scalars.get("MaskLength", 0.0) or 0.0)
    sheen = float(scalars.get("Sheen", 0.15) or 0.15)
    roughness = float(scalars.get("Roughness", 0.75) or 0.75)
    alpha_cut = float(mi.get("opacity_clip") or scalars.get("AlphaCutoff", 0.3333) or 0.3333)
    disp_scale = _fur_disp_scale(length if length > 1e-6 else 0.04)

    root = colors.get("RootColor") or (0.2, 0.16, 0.12, 1.0)
    tip = colors.get("TipColor") or colors.get("FresnelColor") or (0.85, 0.8, 0.72, 1.0)
    root_rgba = (float(root[0]), float(root[1]), float(root[2]), 1.0)
    tip_rgba = (float(tip[0]), float(tip[1]), float(tip[2]), 1.0)
    black = (0.0, 0.0, 0.0, 1.0)

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (610, 130)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (900, 60)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    tex_coord = nodes.new("ShaderNodeTexCoord")
    tex_coord.location = (-1200, -40)
    mapping = nodes.new("ShaderNodeMapping")
    mapping.location = (-1000, -40)
    mapping.inputs["Scale"].default_value = (tiling, tiling, tiling)
    links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])

    fur_tex_path = ""
    for key in ("FurMask", "PM_SpecularMasks", "Color", "T_FurMask_03_A", "T_FurMask_04_A"):
        obj_path = (mi.get("textures") or {}).get(key) or ""
        if not obj_path:
            continue
        fur_tex_path = textures.find_texture_from_object_path(obj_path) or ""
        if fur_tex_path:
            break
    if not fur_tex_path:
        for obj_path in (mi.get("textures") or {}).values():
            if "furmask" not in str(obj_path).lower():
                continue
            fur_tex_path = textures.find_texture_from_object_path(obj_path) or ""
            if fur_tex_path:
                break

    if fur_tex_path:
        img = bpy.data.images.load(fur_tex_path, check_existing=True)
        try:
            img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.label = "FurMask"
        tex.interpolation = "Cubic"
        tex.location = (-780, 40)
        links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
        mask_color = tex.outputs["Color"]
    else:
        noise = nodes.new("ShaderNodeTexNoise")
        noise.location = (-780, 40)
        noise.inputs["Scale"].default_value = max(4.0, tiling * 0.5)
        noise.inputs["Detail"].default_value = 8.0
        noise.inputs["Roughness"].default_value = 0.65
        links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
        # Fac is float — pack via Combine so ColorRamp Factor still works.
        comb = nodes.new("ShaderNodeCombineColor")
        comb.location = (-560, 40)
        links.new(noise.outputs["Fac"], comb.inputs["Red"])
        links.new(noise.outputs["Fac"], comb.inputs["Green"])
        links.new(noise.outputs["Fac"], comb.inputs["Blue"])
        mask_color = comb.outputs["Color"]

    # Albedo: black (shadow) → Root → Tip. Positions are fixed.
    ramp_color = nodes.new("ShaderNodeValToRGB")
    ramp_color.label = "Fur Albedo"
    ramp_color.location = (-240, 160)
    _set_fur_color_ramp(
        ramp_color,
        (
            (_FUR_RAMP_BLACK_POS, black),
            (_FUR_RAMP_ROOT_POS, root_rgba),
            (_FUR_RAMP_TIP_POS, tip_rgba),
        ),
    )
    links.new(mask_color, ramp_color.inputs["Fac"])
    links.new(ramp_color.outputs["Color"], principled.inputs["Base Color"])

    # Displacement height ramp: black → Tip (same tip colour as albedo).
    ramp_disp = nodes.new("ShaderNodeValToRGB")
    ramp_disp.label = "Fur Height"
    ramp_disp.location = (-240, -280)
    _set_fur_color_ramp(
        ramp_disp,
        (
            (_FUR_DISP_BLACK_POS, black),
            (_FUR_DISP_TIP_POS, tip_rgba),
        ),
    )
    links.new(mask_color, ramp_disp.inputs["Fac"])

    principled.inputs["Roughness"].default_value = max(0.15, min(1.0, roughness))
    principled.inputs["Metallic"].default_value = 0.0
    sheen_w = max(0.0, min(1.0, sheen * 2.0))
    for sock_name, value in (
        ("Sheen Weight", sheen_w),
        ("Sheen", sheen_w),
        ("Sheen Roughness", 0.35),
    ):
        if sock_name in principled.inputs:
            principled.inputs[sock_name].default_value = value
    if "Sheen Tint" in principled.inputs:
        try:
            principled.inputs["Sheen Tint"].default_value = tip_rgba
        except Exception:
            pass

    disp = nodes.new("ShaderNodeDisplacement")
    disp.location = (400, -280)
    disp.inputs["Midlevel"].default_value = 0.5
    disp.inputs["Scale"].default_value = disp_scale
    links.new(ramp_disp.outputs["Color"], disp.inputs["Height"])
    if "Displacement" in out_node.inputs:
        links.new(disp.outputs["Displacement"], out_node.inputs["Displacement"])

    _set_material_alpha_mode(mat, "HASHED", threshold=alpha_cut, two_sided=True)
    for attr, value in (
        ("displacement_method", "BOTH"),
    ):
        try:
            setattr(mat, attr, value)
        except Exception:
            pass
    try:
        mat.cycles.displacement_method = "BOTH"
    except Exception:
        pass

    try:
        mat["arc_fur_setup"] = _FUR_SETUP_V
        mat["arc_mi_stem"] = os.path.splitext(os.path.basename(mi_path or ""))[0]
        if mi_path:
            mat["arc_mi_path"] = os.path.abspath(mi_path)
    except Exception:
        pass



def setup_fur_material(obj, mi_path: str = "", psk_path: str = ""):
    """Apply fur shader to every fur slot (fallback when SK walk found nothing)."""
    if obj is None or obj.type != "MESH":
        return
    if apply_embedded_fur_slots(obj, psk_path, skin_json=mi_path or ""):
        return
    if not mi_path:
        mi_path = _find_fur_mi_in_dir(
            os.path.dirname(psk_path) if psk_path else "",
            kind="fur",
        )
    if not mi_path:
        return
    if not obj.material_slots:
        mat = bpy.data.materials.new(name=f"{obj.name}_Fur")
        mat.use_nodes = True
        obj.data.materials.append(mat)
        _setup_fur_material(mat, mi_path, psk_path=psk_path, obj=obj)
        _ensure_fur_displacement_subdiv(obj)
        return
    for index, slot in enumerate(obj.material_slots):
        mat = _ensure_unique_fur_slot_material(
            obj, slot, index, mi_path=mi_path, slot_name=f"Fur{index}",
        )
        mat.use_nodes = True
        _setup_fur_material(mat, mi_path, psk_path=psk_path, obj=obj)
    _ensure_fur_displacement_subdiv(obj)



def apply_embedded_fur_slots(obj, psk_path: str, skin_json: str = "") -> int:
    """After ArcTexturer, wire each Fur / FurShells / FurLOD section to its own mat.

    Distinct mesh sections keep distinct Blender material datablocks even when
    SK JSON points multiple slots at the same MI (layered fur cards).
    """
    if obj is None or not getattr(obj, "material_slots", None):
        return 0
    try:
        sk_slots = _parse_sk_material_slots(psk_path) if psk_path else []
    except Exception:
        sk_slots = []
    # Fast path: single-slot clothing with no fur name → skip resolve/FMDex probes.
    if sk_slots and len(sk_slots) == 1:
        sk_name, mi_stem, mi_json = sk_slots[0]
        names = (sk_name, mi_stem)
        if not _slot_is_fur(names, mi_json or "") and not any(
            "fur" in (n or "").lower() for n in names
        ):
            return 0
    elif not sk_slots and len(obj.material_slots) <= 1:
        mat_name = ""
        if obj.material_slots and obj.material_slots[0].material:
            mat_name = obj.material_slots[0].material.name
        if "fur" not in mat_name.lower():
            return 0
    psk_folder = os.path.dirname(psk_path) if psk_path else ""
    if not sk_slots:
        sk_slots = [
            ((s.material.name if s.material else ""), "", "")
            for s in obj.material_slots
        ]

    used_indices = set()
    applied = 0
    for i, (sk_name, mi_stem, mi_json) in enumerate(sk_slots):
        mat_name_probe = ""
        if i < len(obj.material_slots) and obj.material_slots[i].material:
            mat_name_probe = obj.material_slots[i].material.name
        names_probe = (sk_name, mi_stem, mat_name_probe)
        mi_path = mi_json if mi_json and os.path.isfile(mi_json) else ""
        if not mi_path:
            for stem in names_probe:
                if not stem:
                    continue
                mi_path = _resolve_mi_json_path(stem, "", psk_folder)
                if mi_path:
                    break
        if not _slot_is_fur(names_probe, mi_path):
            continue

        target, index = _match_fur_material_slot(
            obj, sk_name, i, used_indices, mi_stem,
        )
        if target is None:
            continue
        mat_name = target.material.name if target.material else ""
        names = (sk_name, mi_stem, mat_name)
        mi_path = _resolve_colorway_fur_mi(
            mi_path, mi_stem, sk_name, skin_json, psk_folder,
        )
        if not mi_path or not textures.is_fur_mi(mi_path):
            continue
        used_indices.add(index)
        mat = _ensure_unique_fur_slot_material(
            obj, target, index, mi_path=mi_path, slot_name=sk_name or mat_name,
        )
        mat.use_nodes = True
        _setup_fur_material(mat, mi_path, psk_path=psk_path, obj=obj)
        applied += 1
        kind = _fur_slot_kind(names, mi_stem)
        print(
            f"Arc Raiders: Applied fur shader ({kind}) to slot "
            f"'{sk_name or mat_name}' (index {index}) on '{obj.name}' "
            f"← {os.path.basename(mi_path)} → '{mat.name}'"
        )
    if applied:
        _ensure_fur_displacement_subdiv(obj)
    return applied



def apply_fur_materials_to_object(obj, psk_path: str, skin_json: str = "") -> int:
    """Wire every fur slot on a dedicated fur part (or fall back to first MI)."""
    applied = apply_embedded_fur_slots(obj, psk_path, skin_json=skin_json)
    if applied:
        return applied
    folder = os.path.dirname(psk_path) if psk_path else ""
    skin_dir = os.path.dirname(skin_json) if skin_json and os.path.isfile(skin_json) else ""
    mi_path = (
        _find_fur_mi_in_dir(skin_dir, kind="fur")
        or _find_fur_mi_in_dir(folder, kind="fur")
    )
    if not mi_path:
        return 0
    setup_fur_material(obj, mi_path=mi_path, psk_path=psk_path)
    return 1



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



def _is_visor_shell_slot_name(name: str) -> bool:
    """ColorMask / opaque frame around the lens — not the glass material.

    Common stems: ``Visor``, ``MI_Visor_Visor``, ``HelmetVisor``,
    ``MI_HelmetVisor_HelmetVisor``, ``MI_Goggles_Goggles``, ``MI_*_Goggles_*``.
    These must never route into the full-mesh glass pipeline (compound
    ``*visor*`` / bare ``*goggles*`` / bare ``HelmetVisor`` matching previously
    stole shells). ``Helmet_Visor`` (underscore) remains a lens slot name.
    """
    stem = _normalize_mat_stem(name)
    if not stem or _name_has_glass_lens_token(stem):
        return False
    if stem == "visor":
        return True
    # MI_Visor_Visor / Visor_Visor — clothing shell MIC beside MI_*_Glass*
    if stem == "mi_visor_visor" or stem == "visor_visor" or stem.endswith("_visor_visor"):
        return True
    # Pilot / HydroScav HelmetVisor ColorMask shell (glass is HelmetVisor_Glass).
    # Distinct from Helmet_Visor lens sections on multi-slot helmet meshes.
    if "helmetvisor" in stem and "helmet_visor" not in stem:
        return True
    # Goggles / glasses ColorMask+OCM frames (glass is an OCM Material ID region).
    if "goggles" in stem or "glasses" in stem:
        return True
    return False



def _names_suggest_visor_glass_slot(names) -> bool:
    """Lens slots (Helmet_Visor / Visor_Glass / Goggles_Glass) — not ColorMask shells.

    Requires an explicit glass/lens token, or the legacy ``Helmet_Visor`` lens
    section name. Bare ``Visor`` / ``HelmetVisor`` / ``MI_Visor_Visor`` /
    ``MI_*_Goggles_*`` are the opaque shell and must stay on ArcTexturer.
    """
    for raw in names or ():
        if _is_visor_shell_slot_name(raw):
            continue
        n = (raw or "").strip().lower()
        if not n:
            continue
        if _name_has_glass_lens_token(n):
            return True
        # Helmet_Visor lens placeholders on helmet meshes (not HelmetVisor shells).
        if "helmet_visor" in n:
            return True
    return False



def _slot_is_visor_lens(names, psk_path: str = "", mi_path: str = "") -> bool:
    """True when this material slot should receive the glass shader (not ArcTexturer)."""
    if any(_is_visor_shell_slot_name(n) for n in (names or ())):
        # Shell name wins even if another alias looks lens-like or MI is glass.
        if not any(_name_has_glass_lens_token(n or "") for n in (names or ())):
            return False
    if any(_name_has_glass_lens_token(n or "") for n in (names or ())):
        return True
    if mi_path and os.path.isfile(mi_path) and _is_visor_glass_mi(_parse_visor_mi(mi_path)):
        return True
    if _should_route_simple_pbr_to_visor(names, psk_path):
        return True
    return _names_suggest_visor_glass_slot(names)



def _find_clothing_shell_slot_index(obj, psk_path: str = "") -> int:
    """Index of the ColorMask / opaque clothing slot (skip glass / SimplePBR lens).

    ``setup_arc_texturer_material`` must land here — ``active_material`` alone often
    hits the glass slot on multi-slot visor PSKs, then glass setup overwrites it and
    the shell stays untextured.
    """
    if obj is None:
        return -1
    _ensure_clothing_sk_slot_count(obj, psk_path)
    if not getattr(obj, "material_slots", None):
        return -1
    try:
        sk_slots = _parse_sk_material_slots(psk_path) if psk_path else []
    except Exception:
        sk_slots = []
    if not sk_slots:
        sk_slots = [
            ((s.material.name if s.material else ""), "", "")
            for s in obj.material_slots
        ]
    used = set()
    psk_folder = os.path.dirname(psk_path) if psk_path else ""
    for i, (sk_name, mi_stem, mi_json) in enumerate(sk_slots):
        target, index = _match_material_slot(obj, sk_name, i, used, mi_stem)
        if target is None:
            continue
        used.add(index)
        mat_name = target.material.name if target.material else ""
        names = (sk_name, mi_stem, mat_name)
        mi_path = mi_json if mi_json and os.path.isfile(mi_json) else ""
        if not mi_path:
            for stem in names:
                if _is_simple_pbr_material_name(stem) or _is_visor_shell_slot_name(stem):
                    # Still try resolve for shell MIC; skip SimplePBR library parent.
                    if _is_simple_pbr_material_name(stem):
                        continue
                mi_path = _resolve_mi_json_path(stem, "", psk_folder) if stem else ""
                if mi_path:
                    break
        if _slot_is_visor_lens(names, psk_path, mi_path):
            continue
        if _slot_is_fur(names, mi_path):
            continue
        return index
    # Single-slot mesh: use 0 even if heuristics were inconclusive.
    if len(obj.material_slots) == 1:
        return 0
    return -1



def _psk_is_visor_part(psk_path: str) -> bool:
    """True when the mesh path/filename is a dedicated Visor part folder."""
    norm = (psk_path or "").replace("\\", "/").lower()
    if not norm:
        return False
    base = os.path.basename(norm)
    if "nightvision" in norm or "night_vision" in norm:
        return False
    return (
        "/visor/" in norm
        or "/visors/" in norm
        or "/helmetvisor/" in norm
        or (base.startswith("sk_") and "visor" in base)
    )



def _clothing_shell_maps_available(
    psk_path: str = "", mi_path: str = "", skin_json: str = ""
) -> bool:
    """True when ArcTexturer can load clothing-style maps (folder and/or MI).

    ColorMask+OCM, BaseColor/Normal/OCM beside the mesh, or a clothing shell MI
    all count — including ``*_Glass`` slots whose glass MI only has dirt maps while
    the mesh folder still ships shell textures (Moonball Headgear_Outer, etc.).
    """
    folder = os.path.dirname(psk_path) if psk_path else ""
    if _folder_looks_like_clothing_visor(folder):
        return True
    if mi_path and _mi_is_clothing_shell_mi(mi_path):
        return True
    if skin_json and os.path.isfile(skin_json) and _mi_is_clothing_shell_mi(skin_json):
        return True
    paths = _resolve_visor_shell_map_paths(psk_path, skin_json or mi_path)
    if not paths:
        return False
    # Any shell map Arc can bind is enough to prefer Arc + override over Visor-only.
    return bool(
        paths.get("Base Color")
        or paths.get("Normal")
        or paths.get("Curvature ID")
    )



def _should_route_simple_pbr_to_visor(names, psk_path: str = "") -> bool:
    """Helmet / visor lens ``M_SimplePBR`` placeholders → visor glass pipeline.

    Keeps non-helmet SimplePBR (props, PonchoFringe MI_SimplePBR_*, Nightvision
    Emissive) on their existing paths.
    """
    if not any(_is_simple_pbr_material_name(n) for n in (names or ())):
        return False
    if _names_suggest_visor_glass_slot(names):
        return True
    # PSK importer often names the Blender slot ``M_SimplePBR``; SK still says
    # Helmet_Visor / Visor_Glass.
    return _psk_is_helmet_part(psk_path) or _psk_is_visor_part(psk_path)



def _mi_is_clothing_shell_mi(mi_path: str) -> bool:
    """True when an MI is ColorMask + OCM clothing (not glass / SimplePBR)."""
    if not mi_path or not os.path.isfile(mi_path):
        return False
    mi = _parse_visor_mi(mi_path)
    if _is_visor_glass_mi(mi):
        return False
    parent_l = (mi.get("parent") or "").lower()
    tex_keys = {(k or "").lower() for k in (mi.get("textures") or {})}
    has_cm = any("colormask" in k for k in tex_keys)
    has_ocm = any(
        "occlusion" in k or "curvature" in k or "materialid" in k for k in tex_keys
    )
    if has_cm and has_ocm:
        return True
    if "character_preview" in parent_l or "character_ingame" in parent_l:
        return has_cm or has_ocm
    return False



def _folder_looks_like_clothing_visor(folder: str) -> bool:
    """Mesh folder ships ColorMask + OcclusionCurvatureMaterialID (OCM region glass)."""
    if not folder or not os.path.isdir(folder):
        return False
    has_cm = False
    has_ocm = False
    try:
        for fname in os.listdir(folder):
            fl = fname.lower()
            if not (fl.endswith(".png") or fl.endswith(".json")):
                continue
            if "colormask" in fl:
                has_cm = True
            if "occlusioncurvaturematerialid" in fl:
                has_ocm = True
            if has_cm and has_ocm:
                return True
    except OSError:
        return False
    return False



def _find_arc_texturer_group_on_material(mat):
    """ArcTexturer group on a clothing material, or None.

    Uses the nodes-based finder defined earlier in this module (do not redefine
    ``_find_arc_texturer_group_node`` here — that would shadow the clothing path).
    """
    if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return None
    return _find_arc_texturer_group_node(mat.node_tree.nodes)



def _find_curvature_id_override_node(mat):
    if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return None
    for node in mat.node_tree.nodes:
        if getattr(node, "type", "") != "GROUP":
            continue
        tree = getattr(node, "node_tree", None)
        name = (getattr(tree, "name", "") or "") if tree else ""
        if name.replace(" ", "") == "CurvatureID_Override" or name.startswith(
            "CurvatureID_Override"
        ):
            return node
    return None



# Visor group for CurvatureID_Override.Override — mixer X + 1000, lower Y (under it).
_CURVATURE_OVERRIDE_VISOR_DX = 1000.0

_CURVATURE_OVERRIDE_VISOR_DY = -420.0



def _is_visor_group_node(node) -> bool:
    if node is None:
        return False
    if getattr(node, "type", "") != "GROUP" and getattr(node, "bl_idname", "") != "ShaderNodeGroup":
        return False
    tree = getattr(node, "node_tree", None)
    name = (getattr(tree, "name", "") or "") if tree else ""
    compact = name.replace(" ", "")
    return compact == "Visor"



def _place_visor_below_curvature_override(visor, mixer) -> None:
    """Park the Visor group node under CurvatureID_Override in the node editor."""
    if visor is None or mixer is None:
        return
    visor.location = (
        float(mixer.location.x) + _CURVATURE_OVERRIDE_VISOR_DX,
        float(mixer.location.y) + _CURVATURE_OVERRIDE_VISOR_DY,
    )



def _ensure_visor_group_on_override(nodes, links, mixer, visor_tree):
    """Return a Visor *group* wired to mixer Override; never an inline BSDF tree.

    Always repositions that group below ``mixer`` (lower Y).
    """
    if mixer is None or visor_tree is None:
        return None
    override_in = mixer.inputs.get("Override")
    if override_in is None:
        return None

    visor = None
    if override_in.is_linked and override_in.links:
        src = override_in.links[0].from_node
        if _is_visor_group_node(src):
            visor = src
        else:
            # Drop Principled / expanded inline trees — Override must be the group.
            while override_in.is_linked:
                links.remove(override_in.links[0])

    if visor is None:
        # Prefer an existing unbound Visor group on this tree before spawning another.
        for node in nodes:
            if not _is_visor_group_node(node):
                continue
            bsdf = node.outputs.get("BSDF") or (node.outputs[0] if node.outputs else None)
            if bsdf is None:
                continue
            already_to_override = False
            for lnk in getattr(bsdf, "links", []) or []:
                if getattr(lnk, "to_node", None) is mixer and getattr(
                    getattr(lnk, "to_socket", None), "name", ""
                ) == "Override":
                    already_to_override = True
                    break
            if already_to_override or not bsdf.is_linked:
                visor = node
                break
        if visor is None:
            visor = nodes.new("ShaderNodeGroup")
            visor.node_tree = visor_tree
        try:
            visor.name = "Visor"
        except Exception:
            pass
        bsdf = visor.outputs.get("BSDF") or (visor.outputs[0] if visor.outputs else None)
        if bsdf is None:
            return None
        if not override_in.is_linked:
            links.new(bsdf, override_in)

    _place_visor_below_curvature_override(visor, mixer)
    return visor



def _object_wants_ocm_curvature_visor(obj, psk_path: str = "", skin_json: str = "") -> bool:
    """Single-shell Visor/Goggles/Headgear with ColorMask+OCM — glass as OCM Color N.

    Used when there is no matchable Blender glass slot (or glass slots were already
    handled by ``apply_embedded_visor_slots`` Arc+override). Multi-slot shell+glass
    meshes with clothing maps prefer Arc+override on the glass slot instead of
    Visor-only. Ordinary clothing ColorMask+OCM parts (armor, sleeves, …) must not
    enable the Visor override.
    """
    if obj is None:
        return False
    if _object_has_matchable_lens_slot(obj, psk_path):
        return False

    folder = os.path.dirname(psk_path) if psk_path else ""
    try:
        sk_slots = _parse_sk_material_slots(psk_path) if psk_path else []
    except Exception:
        sk_slots = []

    hybrid = _psk_is_ocm_glass_hybrid_part(psk_path)
    if not hybrid:
        for sk_name, mi_stem, _mi_json in sk_slots:
            if _is_visor_shell_slot_name(sk_name) or _is_visor_shell_slot_name(mi_stem):
                hybrid = True
                break
    if not hybrid:
        return False

    if _folder_looks_like_clothing_visor(folder):
        return True
    for sk_name, mi_stem, mi_json in sk_slots:
        if mi_json and _mi_is_clothing_shell_mi(mi_json):
            return True
        if (_is_visor_shell_slot_name(sk_name) or _is_visor_shell_slot_name(mi_stem)) and (
            _folder_looks_like_clothing_visor(folder)
        ):
            return True
    if skin_json and os.path.isfile(skin_json) and _mi_is_clothing_shell_mi(skin_json):
        return True
    shell_idx = _find_clothing_shell_slot_index(obj, psk_path)
    if shell_idx < 0 and obj.material_slots:
        shell_idx = 0
    if shell_idx >= 0:
        mat = obj.material_slots[shell_idx].material
        group = _find_arc_texturer_group_on_material(mat)
        if group is not None and _find_ocm_image_node(mat.node_tree.nodes, group):
            return True
    return False



def _object_is_glass_only_visor(obj, psk_path: str = "", glass_mi: str = "") -> bool:
    """True when the mesh should be a full-material Visor (no clothing shell).

    Last resort only: no ColorMask/OCM/BaseColor/Normal maps Arc can load.
    """
    if obj is None or not getattr(obj, "material_slots", None):
        return False
    if _clothing_shell_maps_available(psk_path, mi_path=glass_mi):
        return False
    if _object_wants_ocm_curvature_visor(obj, psk_path):
        return False
    folder = os.path.dirname(psk_path) if psk_path else ""
    if _folder_looks_like_clothing_visor(folder):
        return False
    if glass_mi and os.path.isfile(glass_mi) and _is_visor_glass_mi(_parse_visor_mi(glass_mi)):
        # Dedicated glass mesh beside a glass MI, no ColorMask shell maps.
        if len(obj.material_slots) <= 1:
            return True
    for slot in obj.material_slots:
        mat_name = slot.material.name if slot.material else ""
        if _is_visor_shell_slot_name(mat_name):
            return False
        try:
            arc_stem = str(slot.material.get("arc_mi_stem") or "") if slot.material else ""
        except Exception:
            arc_stem = ""
        if _is_visor_shell_slot_name(arc_stem):
            return False
    names = []
    for slot in obj.material_slots:
        if slot.material:
            names.append(slot.material.name)
    if _slot_is_visor_lens(names, psk_path, glass_mi or ""):
        return True
    if any(_is_simple_pbr_material_name(n) for n in names) and (
        _psk_is_helmet_part(psk_path) or _psk_is_visor_part(psk_path)
    ):
        return True
    return False



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



def _stamp_visor_manual_fallback(mat, reason: str) -> None:
    if mat is None:
        return
    try:
        mat["arc_visor_setup"] = "manual_fallback"
        mat["arc_visor_fallback"] = 1
        mat["arc_visor_fallback_reason"] = str(reason or "")[:240]
    except Exception:
        pass


def _apply_visor_manual_fallback(
    obj,
    mat,
    *,
    mi_path: str = "",
    psk_path: str = "",
    skin_json: str = "",
    reason: str = "",
    slot_label: str = "",
) -> bool:
    """Previous Visor node-group material (no ArcTexturer / CurvatureID_Override)."""
    if mat is None:
        return False
    why = reason or (
        "CurvatureID_Override needs ArcTexturer "
        "(Ground Truth bypass or missing Arc group)"
    )
    extra = f" slot '{slot_label}'" if slot_label else ""
    print(
        f"Arc Raiders: Visor MANUAL FALLBACK on '{getattr(obj, 'name', '?')}'{extra} — {why}; "
        "using previous Visor node-group setup (not OCM Color-N / CurvatureID_Override)"
    )
    _setup_visor_material(mat, mi_path=mi_path, psk_path=psk_path, skin_json=skin_json)
    _stamp_visor_manual_fallback(mat, why)
    return True


def apply_embedded_visor_slots(obj, psk_path: str, skin_json: str = ""):
    """After ArcTexturer is set up on *obj*, handle glass / lens material slots.

    When the mesh folder (or MI) still has clothing-style maps (ColorMask+OCM /
    BaseColor / Normal), glass-named slots keep **ArcTexturer** and get Visor only
    as ``CurvatureID_Override.Override`` (Enable OFF). Pure Visor-only replace is
    last resort for empty glass MIs with zero shell textures.

    *skin_json* may be FModel's glass MI path or the clothing skin; the matching glass MI is
    resolved so a colourway swap actually changes the lens tint (ColorA/B, else ColorABC).
    Each outfit/colorway gets its own material datablock — never share Visor_Glass across skins.

    Helmet / lens parts often reference the ``M_SimplePBR`` parent on the glass section while
    the real ``MI_*_Glass*`` sits beside the mesh — those slots use this same pipeline.
    """
    # Grow SK-aligned slots first — PSK import often leaves 0 slots; early-return
    # on empty material_slots would skip glass entirely.
    try:
        sk_slots = _parse_sk_material_slots(psk_path)
    except Exception:
        sk_slots = []
    if sk_slots:
        _ensure_clothing_sk_slot_count(obj, psk_path)

    if not obj.material_slots:
        return 0

    # Fast path: single non-glass / non-visor-lens slot → skip FMDex / MI probes.
    if sk_slots and len(sk_slots) == 1:
        sk_name, mi_stem, mi_json = sk_slots[0]
        names = (sk_name, mi_stem)
        if not _slot_is_visor_lens(names, psk_path, mi_json or "") and not any(
            _name_has_glass_lens_token(n or "") for n in names
        ):
            return 0
    elif not sk_slots and len(obj.material_slots) <= 1:
        mat_name = ""
        if obj.material_slots and obj.material_slots[0].material:
            mat_name = obj.material_slots[0].material.name
        if (
            not _name_has_glass_lens_token(mat_name)
            and "visor" not in mat_name.lower()
            and "helmet_visor" not in mat_name.lower()
        ):
            return 0

    psk_folder = os.path.dirname(psk_path) if psk_path else ""
    skin_dir = os.path.dirname(skin_json) if skin_json and os.path.isfile(skin_json) else ""
    # Direct glass MI path from FModel GlassSkinJsonPath wins over directory search.
    # Colourway folders rarely contain glass; authored glass usually lives next to the mesh.
    if skin_json and os.path.isfile(skin_json) and _is_visor_glass_mi(_parse_visor_mi(skin_json)):
        colorway_glass = skin_json
    else:
        colorway_glass = (
            _find_glass_mi_in_dir(skin_dir)
            or _find_glass_mi_in_dir(psk_folder)
        )

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
                # Never resolve the shared parent M_SimplePBR MaterialLibrary asset as an MI.
                if _is_simple_pbr_material_name(stem):
                    continue
                mi_path = _resolve_mi_json_path(stem, "", psk_folder)
                if mi_path:
                    break

        # Shell (Visor / MI_Visor_Visor / MI_*_Goggles_*) stays on ArcTexturer.
        # Only real glass / Helmet_Visor / M_SimplePBR lens slots take the glass instance.
        if any(_is_visor_shell_slot_name(n) for n in names) and not any(
            _name_has_glass_lens_token(n or "") for n in names
        ):
            continue
        is_simple_pbr_lens = _should_route_simple_pbr_to_visor(names, psk_path)
        if not _slot_is_visor_lens(names, psk_path, mi_path):
            continue

        # The mesh names the base glass instance; a colourway skin JSON overrides it.
        if colorway_glass:
            mi_path = colorway_glass
        elif not (mi_path and _is_visor_glass_mi(_parse_visor_mi(mi_path))):
            mi_path = _find_glass_mi_in_dir(psk_folder) or mi_path
        glass_mi_ok = bool(mi_path) and _is_visor_glass_mi(_parse_visor_mi(mi_path))
        if not glass_mi_ok:
            # Explicit lens slots (Visor_Glass / Helmet_Visor / SimplePBR on
            # helmet|visor parts) still get the Visor path; tint falls back to
            # clothing ColorABC inside _setup_visor_material / override wiring.
            if not (
                any(_name_has_glass_lens_token(n or "") for n in names)
                or _names_suggest_visor_glass_slot(names)
                or is_simple_pbr_lens
            ):
                continue
            mi_path = ""

        used_indices.add(index)

        # Prefer ArcTexturer + CurvatureID_Override whenever clothing maps exist
        # (folder ColorMask+OCM / BaseColor / Normal) — including *_Glass slots.
        if _glass_slot_prefers_arc_override(psk_path, mi_path=mi_path, skin_json=skin_json):
            mat = _ensure_arc_on_glass_slot(
                obj, target, index, psk_path=psk_path,
                mi_path=mi_path, skin_json=skin_json,
            )
            if mat is not None:
                n = apply_ocm_curvature_visor_override(
                    obj, psk_path, skin_json=skin_json, glass_mi=mi_path,
                    color_n=6, mat=mat,
                )
                if n:
                    applied += 1
                    print(
                        f"Arc Raiders: Glass slot '{sk_name or mat_name}' on '{obj.name}' "
                        f"kept ArcTexturer [arc+override] "
                        f"(Visor→CurvatureID_Override, Enable=0) → '{mat.name}'"
                    )
                    continue
        # Never replace the ColorMask clothing shell with the Visor node group
        # (Ground Truth Principled or ArcTexturer). Lens slots still get Visor below.
        shell_idx = _find_clothing_shell_slot_index(obj, psk_path)
        if index == shell_idx:
            print(
                f"Arc Raiders: Skipping Visor replace on clothing shell "
                f"'{sk_name or mat_name}' of '{obj.name}'"
            )
            continue

        _ensure_unique_visor_slot_material(
            obj, target, index, mi_path=mi_path, skin_json=skin_json
        )

        _apply_visor_manual_fallback(
            obj,
            target.material,
            mi_path=mi_path,
            psk_path=psk_path,
            skin_json=skin_json,
            reason=(
                "no ArcTexturer on glass/shell slot for CurvatureID_Override "
                "(Ground Truth bypass or missing group)"
            ),
            slot_label=sk_name or mat_name,
        )
        applied += 1

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

    Always instances the bundled ``Visor`` node group (never expands the glass
    graph into the material). Shape × fresnel, ColorA/B/C, dirt / smudge live
    inside the group; texture sampling stays outside so each material keeps its
    own image datablocks. Falls back to a minimal Principled stub only if the
    group is missing from ArcTexturer.blend.
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

    if not utils.ensure_visor_node_group():
        # Minimal stub — keep import usable if ArcTexturer.blend is incomplete.
        out_node = nodes.new("ShaderNodeOutputMaterial")
        out_node.location = (300, 0)
        principled = nodes.new("ShaderNodeBsdfPrincipled")
        principled.location = (0, 0)
        principled.inputs["Base Color"].default_value = color_a
        if "Transmission Weight" in principled.inputs:
            principled.inputs["Transmission Weight"].default_value = 1.0
        elif "Transmission" in principled.inputs:
            principled.inputs["Transmission"].default_value = 1.0
        links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])
        return

    visor_tree = utils.find_node_group("Visor")
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (420, 0)

    visor = nodes.new("ShaderNodeGroup")
    visor.node_tree = visor_tree
    visor.label = f"Visor [{color_source}]"
    visor.location = (80, 0)
    links.new(visor.outputs["BSDF"], out_node.inputs["Surface"])

    def _set_in(name, value):
        sock = visor.inputs.get(name)
        if sock is None:
            return
        try:
            sock.default_value = value
        except Exception:
            pass

    _set_in("ColorA", color_a)
    _set_in("ColorB", color_b)
    _set_in("ColorC", color_c if color_c is not None else color_b)
    _set_in("DirtColor", dirt_color)

    for name in (
        "FresnelLow", "FresnelHigh", "Metallic", "Opacity", "RoughnessModifier",
        "DirtAmount", "DirtSoftness", "DirtRoughness", "MaskDirt",
        "VerticalStart", "VerticalEnd",
        "CircularPositionU", "CircularPositionV",
        "CircularScaleU", "CircularScaleV", "CircularSize",
    ):
        _set_in(name, _visor_scalar(mi, name))

    _set_in("IOR", 1.0)
    _set_in("Transmission", 1.0)
    _set_in("Coat Weight", 1.0)
    _set_in("Coat Roughness", 0.1)
    _set_in("Coat IOR", 1.5)
    _set_in("Sheen Weight", 0.5)
    _set_in("Sheen Roughness", 0.5)

    opacity = _visor_scalar(mi, "Opacity")
    if mi.get("is_transparent"):
        _set_in("Alpha", max(0.05, min(1.0, opacity if opacity > 0.0 else 0.65)))
    else:
        _set_in("Alpha", 0.65)

    # Swatches for node-editor readability (group already holds the live colors).
    color_frame = nodes.new("NodeFrame")
    color_frame.label = f"Visor ColorABC [{color_source}]"
    color_frame.label_size = 16
    for i, (label, rgba) in enumerate((
        ("ColorA", color_a), ("ColorB", color_b),
        *( [("ColorC", color_c)] if color_c is not None else [] ),
    )):
        swatch = nodes.new("ShaderNodeRGB")
        swatch.label = label
        swatch.location = (-200, 320 - i * 160)
        swatch.parent = color_frame
        swatch.outputs[0].default_value = rgba

    dirt_frame = nodes.new("NodeFrame")
    dirt_frame.label = "Visor Dirt / Roughness"
    dirt_frame.label_size = 16

    tex_coord = nodes.new("ShaderNodeTexCoord")
    tex_coord.location = (-900, -200)
    tex_coord.parent = dirt_frame
    uv = tex_coord.outputs["UV"]

    dirt_map_out = None
    if textures_by_param.get("DirtMask"):
        dirt_tiling = _visor_scalar(mi, "DirtTiling")
        dirt_map = nodes.new("ShaderNodeMapping")
        dirt_map.label = "Dirt Tiling"
        dirt_map.location = (-700, -200)
        dirt_map.parent = dirt_frame
        dirt_map.inputs["Scale"].default_value = (dirt_tiling, dirt_tiling, 1.0)
        links.new(uv, dirt_map.inputs["Vector"])
        dirt_map_out = dirt_map.outputs["Vector"]
        dirt_image = _visor_image(
            nodes, links, textures_by_param["DirtMask"], "DirtMask",
            (-480, -200), dirt_map_out,
        )
        dirt_image.parent = dirt_frame
        dirt_red = _visor_red(nodes, links, dirt_image, (-260, -200))
        for node in nodes:
            if node.type == "SEPARATE_COLOR" and node.location.x == -260 and node.location.y == -200:
                node.parent = dirt_frame
                break
        links.new(dirt_red, visor.inputs["Dirt Mask"])
        _set_in("Use Dirt", 1.0)

    if textures_by_param.get("Roughness"):
        rough_tiling = _visor_scalar(mi, "RoughnessTiling")
        rough_map = nodes.new("ShaderNodeMapping")
        rough_map.label = "Smudge Tiling"
        rough_map.location = (-700, -520)
        rough_map.parent = dirt_frame
        rough_map.inputs["Scale"].default_value = (rough_tiling, rough_tiling, 1.0)
        links.new(uv, rough_map.inputs["Vector"])
        rough_image = _visor_image(
            nodes, links, textures_by_param["Roughness"], "Smudges",
            (-480, -520), rough_map.outputs["Vector"],
        )
        rough_image.parent = dirt_frame
        rough_red = _visor_red(nodes, links, rough_image, (-260, -520))
        for node in nodes:
            if node.type == "SEPARATE_COLOR" and node.location.x == -260 and node.location.y == -520:
                node.parent = dirt_frame
                break
        links.new(rough_red, visor.inputs["Smudge"])
        _set_in("Use Smudge", 1.0)

    if textures_by_param.get("DirtNormal") and textures_by_param.get("DirtMask"):
        normal_image = _visor_image(
            nodes, links, textures_by_param["DirtNormal"], "DirtNormal",
            (-480, -840), dirt_map_out if dirt_map_out is not None else uv,
        )
        normal_image.parent = dirt_frame
        links.new(normal_image.outputs["Color"], visor.inputs["Dirt Normal"])
        _set_in("Use Dirt Normal", 1.0)

    # Clothing Base Color / Normal / OCM from the mesh folder (glass-slot materials
    # do not share the ArcTexturer tree, so load the same maps Arc uses).
    _wire_visor_shell_surface_inputs(
        visor, nodes, links,
        group_node=None,
        psk_path=psk_path,
        skin_json=skin_json or mi_path,
        origin=(-200.0, 200.0),
        skip_if_linked=True,
    )



def _resolve_visor_glass_mi_path(psk_path: str = "", mi_path: str = "",
                                  skin_json: str = "") -> str:
    """Best glass MI beside the mesh / colourway (never MI_Visor_Visor clothing)."""
    folder = os.path.dirname(psk_path) if psk_path else ""
    skin_folder = os.path.dirname(skin_json) if skin_json and os.path.isfile(skin_json) else ""
    if mi_path and os.path.isfile(mi_path) and _is_visor_glass_mi(_parse_visor_mi(mi_path)):
        return mi_path
    if skin_json and os.path.isfile(skin_json) and _is_visor_glass_mi(_parse_visor_mi(skin_json)):
        return skin_json
    found = _find_glass_mi_in_dir(skin_folder) or _find_glass_mi_in_dir(folder)
    if found:
        return found
    return ""



def _wire_visor_shell_surface_inputs(
    visor,
    nodes,
    links,
    group_node=None,
    psk_path: str = "",
    skin_json: str = "",
    origin=(0.0, 0.0),
    skip_if_linked: bool = True,
):
    """Connect Arc Base Color / Normal / OCM into Visor shell-map sockets.

    Prefer existing TexImage nodes on the same tree (CurvatureID_Override path);
    otherwise load clothing maps from the mesh / skin folder (glass-slot path).
    """
    if visor is None:
        return

    def _set_use(name, value=1.0):
        sock = visor.inputs.get(name)
        if sock is None:
            return
        try:
            sock.default_value = value
        except Exception:
            pass

    def _sock_linked(name) -> bool:
        sock = visor.inputs.get(name)
        return bool(sock is not None and sock.is_linked)

    role_to_sock = (
        ("basecolor", "Base Color", "Use Base Color"),
        ("normal", "Normal", "Use Normal"),
        ("occlusion", "Curvature ID", "Use Curvature"),
    )
    path_map = _resolve_visor_shell_map_paths(psk_path, skin_json)
    ox, oy = float(origin[0]), float(origin[1])
    y_off = 0

    for role, sock_name, use_name in role_to_sock:
        sock = visor.inputs.get(sock_name)
        if sock is None:
            continue
        if skip_if_linked and _sock_linked(sock_name):
            _set_use(use_name, 1.0)
            continue

        tex = _find_arc_role_image_node(nodes, group_node, role)
        if tex is None:
            path = path_map.get(sock_name, "")
            if path and os.path.isfile(path):
                non_color = role in ("normal", "occlusion")
                tex = _visor_image(
                    nodes, links, path, sock_name,
                    (ox - 480, oy + 200 - y_off), None,
                )
                if not non_color:
                    try:
                        tex.image.colorspace_settings.name = "sRGB"
                    except Exception:
                        pass
                y_off += 280
        if tex is None:
            continue
        color_out = tex.outputs.get("Color")
        if color_out is None:
            continue
        if sock.is_linked:
            while sock.is_linked:
                links.remove(sock.links[0])
        links.new(color_out, sock)
        _set_use(use_name, 1.0)



def _wire_visor_group_params(visor, nodes, links, mi_path: str, psk_path: str,
                             skin_json: str, origin=(0.0, 0.0),
                             skip_textures_if_linked: bool = False,
                             group_node=None):
    """Set Visor group ColorABC / dirt / scalars; attach dirt + shell maps near *origin*."""
    mi = _parse_visor_mi(mi_path)
    textures_by_param = _resolve_visor_textures(mi, mi_path, psk_path, skin_json)
    color_a, color_b, color_c, dirt_color, color_source = _resolve_visor_lens_colors(
        mi, skin_json
    )

    def _set_in(name, value):
        sock = visor.inputs.get(name)
        if sock is None:
            return
        try:
            sock.default_value = value
        except Exception:
            pass

    def _sock_linked(name) -> bool:
        sock = visor.inputs.get(name)
        return bool(sock is not None and sock.is_linked)

    _set_in("ColorA", color_a)
    _set_in("ColorB", color_b)
    _set_in("ColorC", color_c if color_c is not None else color_b)
    _set_in("DirtColor", dirt_color)
    for name in (
        "FresnelLow", "FresnelHigh", "Metallic", "Opacity", "RoughnessModifier",
        "DirtAmount", "DirtSoftness", "DirtRoughness", "MaskDirt",
        "VerticalStart", "VerticalEnd",
        "CircularPositionU", "CircularPositionV",
        "CircularScaleU", "CircularScaleV", "CircularSize",
    ):
        _set_in(name, _visor_scalar(mi, name))
    _set_in("IOR", 1.0)
    _set_in("Transmission", 1.0)
    _set_in("Coat Weight", 1.0)
    _set_in("Coat Roughness", 0.1)
    _set_in("Coat IOR", 1.5)
    _set_in("Sheen Weight", 0.5)
    _set_in("Sheen Roughness", 0.5)
    opacity = _visor_scalar(mi, "Opacity")
    if mi.get("is_transparent"):
        _set_in("Alpha", max(0.05, min(1.0, opacity if opacity > 0.0 else 0.65)))
    else:
        _set_in("Alpha", 0.65)

    try:
        visor.label = f"Visor [{color_source}]"
    except Exception:
        pass

    need_dirt = bool(textures_by_param.get("DirtMask")) and not (
        skip_textures_if_linked and _sock_linked("Dirt Mask")
    )
    need_smudge = bool(textures_by_param.get("Roughness")) and not (
        skip_textures_if_linked and _sock_linked("Smudge")
    )
    need_normal = bool(
        textures_by_param.get("DirtNormal") and textures_by_param.get("DirtMask")
    ) and not (skip_textures_if_linked and _sock_linked("Dirt Normal"))
    ox, oy = float(origin[0]), float(origin[1])
    if not (need_dirt or need_smudge or need_normal):
        if _sock_linked("Dirt Mask"):
            _set_in("Use Dirt", 1.0)
        if _sock_linked("Smudge"):
            _set_in("Use Smudge", 1.0)
        if _sock_linked("Dirt Normal"):
            _set_in("Use Dirt Normal", 1.0)
        _wire_visor_shell_surface_inputs(
            visor, nodes, links,
            group_node=group_node,
            psk_path=psk_path,
            skin_json=skin_json,
            origin=(ox, oy + 420),
            skip_if_linked=skip_textures_if_linked,
        )
        return color_source

    tex_coord = nodes.new("ShaderNodeTexCoord")
    tex_coord.location = (ox - 900, oy - 200)
    uv = tex_coord.outputs["UV"]
    dirt_map_out = None
    if need_dirt:
        dirt_tiling = _visor_scalar(mi, "DirtTiling")
        dirt_map = nodes.new("ShaderNodeMapping")
        dirt_map.label = "Dirt Tiling"
        dirt_map.location = (ox - 700, oy - 200)
        dirt_map.inputs["Scale"].default_value = (dirt_tiling, dirt_tiling, 1.0)
        links.new(uv, dirt_map.inputs["Vector"])
        dirt_map_out = dirt_map.outputs["Vector"]
        dirt_image = _visor_image(
            nodes, links, textures_by_param["DirtMask"], "DirtMask",
            (ox - 480, oy - 200), dirt_map_out,
        )
        dirt_red = _visor_red(nodes, links, dirt_image, (ox - 260, oy - 200))
        links.new(dirt_red, visor.inputs["Dirt Mask"])
        _set_in("Use Dirt", 1.0)
    elif _sock_linked("Dirt Mask"):
        _set_in("Use Dirt", 1.0)
    if need_smudge:
        rough_tiling = _visor_scalar(mi, "RoughnessTiling")
        rough_map = nodes.new("ShaderNodeMapping")
        rough_map.label = "Smudge Tiling"
        rough_map.location = (ox - 700, oy - 520)
        rough_map.inputs["Scale"].default_value = (rough_tiling, rough_tiling, 1.0)
        links.new(uv, rough_map.inputs["Vector"])
        rough_image = _visor_image(
            nodes, links, textures_by_param["Roughness"], "Smudges",
            (ox - 480, oy - 520), rough_map.outputs["Vector"],
        )
        rough_red = _visor_red(nodes, links, rough_image, (ox - 260, oy - 520))
        links.new(rough_red, visor.inputs["Smudge"])
        _set_in("Use Smudge", 1.0)
    elif _sock_linked("Smudge"):
        _set_in("Use Smudge", 1.0)
    if need_normal:
        normal_image = _visor_image(
            nodes, links, textures_by_param["DirtNormal"], "DirtNormal",
            (ox - 480, oy - 840), dirt_map_out if dirt_map_out is not None else uv,
        )
        links.new(normal_image.outputs["Color"], visor.inputs["Dirt Normal"])
        _set_in("Use Dirt Normal", 1.0)
    elif _sock_linked("Dirt Normal"):
        _set_in("Use Dirt Normal", 1.0)

    _wire_visor_shell_surface_inputs(
        visor, nodes, links,
        group_node=group_node,
        psk_path=psk_path,
        skin_json=skin_json,
        origin=(ox, oy + 420),
        skip_if_linked=skip_textures_if_linked,
    )
    return color_source



def apply_ocm_curvature_visor_override(obj, psk_path: str = "", skin_json: str = "",
                                       glass_mi: str = "", color_n: int = 6,
                                       mat=None) -> int:
    """Keep ArcTexturer; plug Visor into CurvatureID_Override.Override (Enable stays OFF).

    For clothing / headgear / glass-named slots where the lens is an OCM Material ID
    (Color N) region — or a ``*_Glass`` slot that still has shell maps beside the
    mesh. Visor is pre-wired for the user, but ``Enable`` defaults to 0 so
    Fac = band_mask × Enable keeps pure Arc until they turn the override on.
    Transparent blend is only applied when Enable is already on.

    *mat* — optional material to wire (glass-slot Arc copy). Defaults to the
    clothing shell slot's material.
    """
    if mat is None:
        if obj is None or not getattr(obj, "material_slots", None):
            return 0
        shell_idx = _find_clothing_shell_slot_index(obj, psk_path)
        if shell_idx < 0:
            shell_idx = 0
        if shell_idx >= len(obj.material_slots):
            return 0
        mat = obj.material_slots[shell_idx].material
    if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return 0

    group_node = _find_arc_texturer_group_on_material(mat)
    if group_node is None:
        return 0

    mixer = _find_curvature_id_override_node(mat)
    if mixer is None:
        try:
            insert_curvature_id_override(mat, group_node)
        except Exception as exc:
            print(f"Arc Raiders: CurvatureID_Override insert failed: {exc}")
            return 0
        mixer = _find_curvature_id_override_node(mat)
    if mixer is None:
        return 0

    if not utils.ensure_visor_node_group():
        print("Arc Raiders: Visor node group unavailable for OCM curvature override")
        return 0
    visor_tree = utils.find_node_group("Visor")
    if visor_tree is None:
        return 0

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    # Override ← reusable Visor group only (never inline fresnel/Principled tree).
    # Group is placed under CurvatureID_Override (mixer X + 1000, Y -420).
    visor = _ensure_visor_group_on_override(nodes, links, mixer, visor_tree)
    if visor is None:
        return 0

    glass_mi = glass_mi or _resolve_visor_glass_mi_path(
        psk_path, skin_json=skin_json
    )
    # Dirt/smudge + shell Base Color / Normal / OCM sit beside the Visor group.
    _wire_visor_group_params(
        visor, nodes, links, glass_mi, psk_path, skin_json,
        origin=(float(visor.location.x), float(visor.location.y)),
        skip_textures_if_linked=True,
        group_node=group_node,
    )

    # Leave Enable OFF (group/insert default). Forcing 1 made every OCM hybrid
    # look like glass on import; Fac = band_mask × Enable already passthroughs
    # Arc when Enable is 0 even with Visor plugged into Override.
    enable = mixer.inputs.get("Enable")
    if enable is not None:
        try:
            enable.default_value = 0.0
        except Exception:
            pass
    debug = mixer.inputs.get("Debug")
    if debug is not None:
        try:
            debug.default_value = 0
        except Exception:
            pass
    color_n_sock = mixer.inputs.get("Color N")
    if color_n_sock is not None:
        try:
            color_n_sock.default_value = int(color_n)
        except Exception:
            pass

    # OCM → Material ID Map if not already linked.
    mid = mixer.inputs.get("Material ID Map")
    if mid is not None and not mid.is_linked:
        ocm = _find_ocm_image_node(nodes, group_node)
        if ocm is not None and "Color" in ocm.outputs:
            links.new(ocm.outputs["Color"], mid)

    cm_sock = mixer.inputs.get("ColorMask")
    if cm_sock is not None and not cm_sock.is_linked:
        cm_node = _find_colormask_image_node(nodes, group_node)
        if cm_node is not None and "Color" in cm_node.outputs:
            try:
                links.new(cm_node.outputs["Color"], cm_sock)
            except Exception:
                pass

    try:
        mat["arc_ocm_curvature_visor"] = 1
        mat["arc_ocm_curvature_color_n"] = int(color_n)
        if glass_mi:
            mat["arc_visor_mi"] = os.path.basename(glass_mi)
    except Exception:
        pass

    # Transparent blend only when override is active; Enable=0 → Arc look.
    enable_on = False
    if enable is not None:
        try:
            enable_on = float(enable.default_value) > 0.5
        except Exception:
            enable_on = False
    _set_visor_render_method(mat, enable_on)
    print(
        f"Arc Raiders: OCM CurvatureID_Override Visor on '{obj.name if obj else mat.name}' "
        f"(Color N={color_n}, Enable=0, "
        f"glass={os.path.basename(glass_mi) if glass_mi else 'clothing_abc'})"
    )
    return 1



def setup_visor_material(obj, psk_path: str, mi_path: str = "", skin_json: str = ""):
    """Public entry point for a visor mesh: always ArcTexturer; Visor as override.

    Policy (helmet / visor / goggles / headgear / ``*_Glass``):
    1. Always keep ArcTexturer when ColorMask+OCM or clothing-style maps exist —
       including glass-named slots (``HelmetVisor_Glass``, ``*_Outer_Glass``, …).
    2. Wire Visor into ``CurvatureID_Override.Override`` (Enable OFF on import).
    3. Do **not** replace the whole material with Visor-only when shell maps exist.
    4. True glass-only meshes with zero clothing textures → full-material
       ``_Visor_Mat`` as last resort.
    """
    applied = apply_embedded_visor_slots(obj, psk_path, skin_json=skin_json)
    if applied:
        return applied

    folder = os.path.dirname(psk_path) if psk_path else ""
    glass_mi = _resolve_visor_glass_mi_path(psk_path, mi_path=mi_path, skin_json=skin_json)

    # Hybrids (visor/goggles shell with ColorMask+OCM): Arc + CurvatureID_Override.
    # Do **not** use ``_clothing_shell_maps_available`` here — that is true for every
    # shirt/pants part and, on Ground Truth (no Arc), used to replace the whole
    # material with the Visor node group.
    if _object_wants_ocm_curvature_visor(obj, psk_path, skin_json=skin_json):
        n = apply_ocm_curvature_visor_override(
            obj, psk_path, skin_json=skin_json, glass_mi=glass_mi, color_n=6,
        )
        if n:
            return n
        print(
            f"Arc Raiders: Keeping clothing shader on '{getattr(obj, 'name', '?')}' "
            "(no ArcTexturer for CurvatureID_Override — Ground Truth or missing group; "
            "not replacing shell with Visor)"
        )
        return 0

    if not _object_is_glass_only_visor(obj, psk_path, glass_mi):
        print(
            f"Arc Raiders: Skipping full-mesh Visor_Mat on '{getattr(obj, 'name', '?')}' "
            f"(clothing shell / no dedicated glass slot)"
        )
        return 0

    mat = bpy.data.materials.new(name=obj.name + "_Visor_Mat")
    obj.active_material = mat
    _setup_visor_material(mat, mi_path=glass_mi, psk_path=psk_path, skin_json=skin_json)
    return 1

