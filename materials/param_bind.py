"""MI parameter binding for the layered outfit graph: build once, swap colourways by value.

Every authored MI parameter that reaches the graph lives in one node tagged ``dr.parameter_key``
(RGB for vectors, Value for scalars and static switches as 0/1). The rest of the graph links from
those nodes, so a colourway switch only rewrites their values (``apply_mi_values``) and never
rebuilds or re-links anything. ``mi_values`` reads the effective (inherited) parameter set.
"""
from __future__ import annotations

from .. import textures

PARAM_KEY = "dr.parameter_key"


def _name(param: dict) -> str:
    return str((param.get("ParameterInfo") or {}).get("Name") or param.get("Name") or "")


def mi_values(json_path: str) -> dict:
    """Effective MI parameters: vectors as RGBA tuples, scalars as floats, static switches as 0/1."""
    props = textures.load_mi_properties(json_path) if json_path else {}
    values = {}
    for param in props.get("VectorParameterValues") or []:
        v = param.get("ParameterValue") or {}
        if _name(param) and isinstance(v, dict):
            values[_name(param)] = (float(v.get("R", 0.0)), float(v.get("G", 0.0)),
                                    float(v.get("B", 0.0)), float(v.get("A", 1.0)))
    for param in props.get("ScalarParameterValues") or []:
        if _name(param):
            try:
                values[_name(param)] = float(param.get("ParameterValue", 0.0))
            except (TypeError, ValueError):
                pass
    static = props.get("StaticParameters") or props.get("StaticParametersRuntime") or {}
    for param in static.get("StaticSwitchParameters") or []:
        if _name(param):
            values[_name(param)] = 1.0 if param.get("Value") else 0.0
    return values


_COMPONENT = {".R": 0, ".G": 1, ".B": 2, ".A": 3}


def lookup(values: dict, key: str):
    """``values[key]``; ``Name.R/.G/.B/.A`` reads one component of a vector (RGB nodes carry no alpha)."""
    if key in values:
        return values[key]
    base, comp = key[:-2], _COMPONENT.get(key[-2:])
    if comp is not None and isinstance(values.get(base), tuple):
        return values[base][comp]
    return None


def _write(node, value) -> bool:
    sock = node.outputs[0]
    if node.bl_idname == "ShaderNodeRGB":
        if not isinstance(value, tuple):
            return False
        new = tuple(float(x) for x in value[:4]) + ((1.0,) if len(value) < 4 else ())
    else:
        if isinstance(value, tuple):
            return False
        new = float(value)
    old = tuple(sock.default_value) if isinstance(new, tuple) else sock.default_value
    if old == new:   # each write re-runs the tree update
        return True
    sock.default_value = new
    return True


def bind(nodes, key: str, value, *, rgba: bool | None = None):
    """The output socket of the one node carrying MI parameter ``key`` (created on first use)."""
    name = "P:" + key
    node = nodes.get(name)
    if node is None:
        is_rgb = isinstance(value, tuple) if rgba is None else rgba
        node = nodes.new("ShaderNodeRGB" if is_rgb else "ShaderNodeValue")
        node.name, node.label, node.hide = name, key, True
        node[PARAM_KEY] = key
    _write(node, value)
    return node.outputs[0]


def _param_nodes(tree, seen=None):
    seen = set() if seen is None else seen
    for node in tree.nodes:
        if node.get(PARAM_KEY) and node.bl_idname in ("ShaderNodeRGB", "ShaderNodeValue"):
            yield node
        if node.bl_idname == "ShaderNodeGroup" and node.node_tree and node.node_tree.name not in seen:
            seen.add(node.node_tree.name)
            yield from _param_nodes(node.node_tree, seen)


_NEUTRAL = {"masks": (0.0, 0.0, 0.0, 1.0), "normals": (0.5, 0.5, 1.0, 1.0),
            "decal": (0.0, 0.0, 0.0, 0.0), "decal_data": (0.5, 0.5, 1.0, 0.0)}


def neutral_image(group: str):
    """1x1 stand-in for a texture-array slice a colourway does not use (black mask, flat normal):
    the sample node stays in the graph and contributes nothing until a swap binds a real slice."""
    import bpy
    rgba = _NEUTRAL.get(group)
    if rgba is None:
        return None
    name = f"DR_Neutral_{group}"
    img = bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.new(name, 1, 1, alpha=True, float_buffer=False, is_data=True)
        img.pixels[:] = rgba
        img.colorspace_settings.name = "Non-Color"
        img.pack()
    return img


def _texture_nodes(tree, seen=None):
    seen = set() if seen is None else seen
    for node in tree.nodes:
        if node.bl_idname == "ShaderNodeTexImage" and node.get("dr.texture_index_param"):
            yield node
        if node.bl_idname == "ShaderNodeGroup" and node.node_tree and node.node_tree.name not in seen:
            seen.add(node.node_tree.name)
            yield from _texture_nodes(node.node_tree, seen)


def _skin_layer_pngs(json_path: str) -> dict:
    """The layer PNGs a full build of this MI samples, grouped as the builder groups them.

    Same list as textures.scan_base_skin_textures for the selected skin: PNGs beside the MI, then the
    texture-array slices it references (inherited ones included), so a swap never resolves differently.
    ``__all__`` keeps that order; pattern and colour slices search (other, all) like the builder."""
    import os
    path = textures._fs_abspath(json_path)
    folder = os.path.dirname(path)
    try:
        pngs = [os.path.join(folder, f) for f in sorted(os.listdir(folder)) if f.lower().endswith(".png")]
    except OSError:
        pngs = []
    pngs.extend(p for p in textures.referenced_texture_array_slices(json_path) if p not in pngs)
    groups = {"__all__": pngs}
    for f in pngs:
        groups.setdefault(textures.base_skin_texture_group(os.path.basename(f)), []).append(f)
    return groups


def _swap_images(mat, json_path, values, stats, dry_run):
    import bpy
    groups = None
    current = None
    for node in _texture_nodes(mat.node_tree):
        param = str(node["dr.texture_index_param"])
        index = lookup(values, param)
        if index is None:
            # Unauthored here: the ID stays at the graph's value, but its image still comes from
            # this colourway's arrays (a full build resolves the same ID against them).
            if current is None:
                current = {str(n[PARAM_KEY]): n.outputs[0].default_value for n in _param_nodes(mat.node_tree)
                           if n.bl_idname == "ShaderNodeValue"}
            index = current.get(param)
            if index is None:
                stats["unbound"].append(param)
                continue
        if groups is None:
            groups = _skin_layer_pngs(json_path)
        group = str(node.get("dr.texture_group") or "")
        index = int(round(index))
        if group == "pattern":
            from .clothing import _find_pattern_slice_png
            path = _find_pattern_slice_png((groups.get("other", []), groups["__all__"]), index) if index >= 0 else ""
        elif group == "colortex":
            from .clothing import _find_colortex_slice_png
            if index < 1:   # off: the graph's ID gate outputs white, the image is not sampled
                continue
            path = _find_colortex_slice_png((groups.get("other", []), groups["__all__"]), index)
        else:
            path = textures.find_slice_png(groups.get(group, []), index)
        if not path:
            stats["no_slice"].append(param)
            neutral = neutral_image(group)
            if neutral is not None and node.image is not neutral and not dry_run:
                node.image = neutral
            continue
        old = node.image.filepath if node.image else ""
        if bpy.path.abspath(old) != path:
            stats["images"][param] = (old.rsplit("\\", 1)[-1], path.rsplit("\\", 1)[-1])
            if not dry_run:
                from .common import _load_image_cached   # copes with >MAX_PATH cache paths
                if node.image is not None and node.image.name.startswith("DR_Neutral_"):
                    colorspace = "Non-Color"
                elif node.image is not None:
                    colorspace = node.image.colorspace_settings.name
                else:   # built without an image: colour arrays are sRGB, every other array is data
                    colorspace = "sRGB" if group == "colortex" else "Non-Color"
                alpha = node.image.alpha_mode if node.image else "CHANNEL_PACKED"
                img = _load_image_cached(path, non_color=colorspace != "sRGB", alpha_mode=alpha)
                if img is None:
                    stats["no_slice"].append(param)
                    continue
                node.image = img


def _texture_refs(json_path: str) -> dict:
    """Effective MI texture parameters: name -> object path (inherited included)."""
    refs = {}
    for param in textures.load_mi_properties(json_path).get("TextureParameterValues") or []:
        name = _name(param)
        if name.startswith("Global."):
            name = name[len("Global."):]
        value = param.get("ParameterValue") or {}
        ref = str(value.get("ObjectPath") or value.get("AssetPathName") or "") if isinstance(value, dict) else str(value)
        if name and ref:
            refs[name] = ref
    return refs


def _swap_base_textures(mat, json_path, stats, dry_run):
    """Rebind the part's own maps (BaseColor, ColorMask, Normal, BreakupMask, OCM) by MI parameter."""
    import os
    from .clothing import _resolve_mi_texture_path
    from .common import _load_image_cached
    nodes = [n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeTexImage" and n.get("dr.texture_param")]
    if not nodes:
        return
    refs = _texture_refs(json_path)
    for node in nodes:
        param = str(node["dr.texture_param"])
        path = _resolve_mi_texture_path(refs[param]) if param in refs else ""
        if not path:
            stats["no_slice"].append(param)
            continue
        old = node.image.filepath if node.image else ""
        if os.path.normcase(textures._fs_abspath(old)) == os.path.normcase(textures._fs_abspath(path)):
            continue
        stats["images"][param] = (os.path.basename(old), os.path.basename(path))
        if dry_run:
            continue
        colorspace = node.image.colorspace_settings.name if node.image else "Non-Color"
        img = _load_image_cached(path, non_color=colorspace != "sRGB",
                                 alpha_mode=node.image.alpha_mode if node.image else None)
        if img is None:
            stats["no_slice"].append(param)
            continue
        node.image = img
        if param == "OcclusionCurvatureMaterialID":
            stats["ocm_changed"] = True


def _swap_decals(mat, json_path, stats, dry_run):
    """Rewrite the fixed decal shell slots (gt_outfit_pbr.write_decal_slot) from this MI's decals."""
    nodes = mat.node_tree.nodes
    slots = sorted({int(n["dr.decal_slot"]) for n in nodes if n.get("dr.decal_slot") is not None})
    if not slots or dry_run:
        return
    from . import clothing as C, gt_outfit_pbr as G
    from .. import utils
    decals = {}
    for decal in textures.parse_clothing_mi(json_path).get("decals") or []:
        try:
            decals.setdefault(int(decal.get("index")), decal)
        except (TypeError, ValueError):
            continue
    try:
        df = utils.get_decal_folder() or ""
    except Exception:
        df = ""
    dirs = [str(d) for d in (mat.node_tree.get("dr.decal_dirs") or [])]
    method = C.active_decal_method()
    for idx in slots:
        if G.write_decal_slot(C, nodes, idx, decals.get(idx), df, dirs, method):
            stats["decals_on"] += 1


def apply_mi_values(mat, json_path: str, *, dry_run: bool = False) -> dict:
    """Rewrite every bound parameter node of ``mat`` from ``json_path``. No nodes are created or linked.

    Returns counts plus ``changed`` (key: (old, new)) and ``unbound`` (bound keys the MI lacks)."""
    values = mi_values(json_path)
    stats = {"bound": 0, "set": 0, "changed": {}, "unbound": [], "type_mismatch": [], "images": {}, "no_slice": [],
             "decals_on": 0}
    if mat is None or not mat.node_tree:
        return stats
    _swap_images(mat, json_path, values, stats, dry_run)
    _swap_base_textures(mat, json_path, stats, dry_run)
    for node in _param_nodes(mat.node_tree):
        key = str(node[PARAM_KEY])
        stats["bound"] += 1
        new = lookup(values, key)
        if new is None:
            stats["unbound"].append(key)
            continue
        old = tuple(node.outputs[0].default_value) if node.bl_idname == "ShaderNodeRGB" else node.outputs[0].default_value
        if isinstance(old, tuple) != isinstance(new, tuple):
            stats["type_mismatch"].append(key)
            continue
        diff = (max(abs(a - b) for a, b in zip(old, new)) if isinstance(new, tuple) else abs(old - new))
        if diff > 1e-4:
            stats["changed"][key] = (old, new)
        if not dry_run:
            _write(node, new)
        stats["set"] += 1
    # After the generic values: a slot's on/off and colours follow the decal rules, not raw MI switches.
    _swap_decals(mat, json_path, stats, dry_run)
    return stats
