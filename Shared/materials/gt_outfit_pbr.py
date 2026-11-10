"""Ground-truth outfit PBR graph (no ArcTexturer).

Cooked Colour N is assembled in ``clothing._wire_outfit_color_ground_truth``.
This module muxes zones with ``floor(mid*8+0.5)`` and applies roughness,
metallic, extra normals, crease/edge overlays, occlusion, and ArcDecalUv decals
into Principled BSDF.
"""
from __future__ import annotations

import os
import hashlib
import json
import math

# DR selection composition; native shader/decal math remains retained below.
from .dr_primary_graph.public import candidate as _dr_primary_candidate
from .dr_primary_graph.identity import assign as _assign
from . import dr_primary_operations as _dr_ops
from . import graph_layout as _graph_layout

from .. import textures
from .common import (
    _WIDTH_RATIO_BY_PATH,
    _load_image_cached,
    _norm_path_key,
    _node_world_loc,
    _set_node_world_loc,
    begin_deferred_locations,
    end_deferred_locations,
    node_loc,
    resolve_node_overlaps,
    set_node_loc,
)

# Colour + decal share ONE Y ladder (Goalie Shirt 002 user alignment):
# columns = Colour 1..8 then Decal 1..8; rows = process steps shared across both.
# Colour N / mux_col / decal albedo sit on the same "colour" row; metal / rough /
# normals likewise — not separate colour-only vs decal-only row systems.
_GT_COLOUR_X0 = 14832.0
_GT_GRID_DX = 550.0
_GT_GRID_DY = 550.0
_GT_DX = _GT_GRID_DX
_GT_DY = _GT_GRID_DY
_GT_ROW_H = _GT_GRID_DY
_GT_MAPS_UV_X = 11575.0
_GT_MAPS_OCM_X = 12827.0
_GT_MAPS_CM_X = 13885.0
_GT_MAPS_X = _GT_MAPS_UV_X
_GT_PAD = 200.0
_GT_PAD_TOP = 200.0
_GT_PAD_X = 50.0
_GT_PAD_BOTTOM = 50.0
_GT_FINISH_X = _GT_COLOUR_X0
_GT_BSDF_GAP = 800.0
_GT_OUTPUT_GAP = 414.0
_GT_DECAL_ORIGIN_X = 23033.0
# Texture arrays sample UV1 with MI Tiling * DetailTiling * stage tiling.
# ArrayUvScale 25 was a Snooper visualization scale, not the character shader.

# Shared process rows (world Y). Colour columns and Decal columns use these.
# Half-rows from Shirt 003: e/c metal and e/c rough must NOT share one Y with base.
_SHARED_ROW_Y = {
    "zone_eq": -720.0,       # one GT_ZoneEq1 per Colour column (top)
    "decal_rot": 416.0,
    "decal_place": -134.0,
    "decal_color": -781.0,
    "decal_tint": -1881.0,
    "colour_n": -1910.0,     # Colour N groups (above mux / decal albedo)
    "colortex_blend": -2205.0,  # GT lerp + ×ColorTex (Shirt 003: under Colour N)
    "colour": -2552.0,       # mux_col + decal albedo
    "maps": -2672.0,         # ColorMask, occ, OCM helpers
    "decal_data": -2981.0,
    "decal_unpack": -3531.0,
    "e_metal": -3440.0,      # edge metallicity mux (above base metal)
    "metal": -3700.0,        # base mux_metal + Value satellites
    "c_metal": -4060.0,      # crease metallicity mux
    "wear_metal": -4200.0,   # wear metal + decal metal + Principled
    "e_rough": -4350.0,      # edge rough half-row
    "c_rough": -4600.0,      # crease rough half-row
    "rough": -4800.0,        # mux_rough + base TA masks + decal rough
    "ta_rough": -4950.0,     # BaseRough TA images sit just under mux_rough (003)
    "n_base": -5600.0,
    "ta_n_base": -5800.0,    # BaseN TA images under mux_nBase (003)
    "n_crease": -6400.0,
    "n_edge": -7200.0,
    "n_med": -7800.0,        # med + decal normal + NormalMap + mesh overlay
    # Finish-column OCM duplicates for crease/edge (Shirt 003 cleanup)
    "ocm_wear": -8900.0,
    # ColorTex UV / Mapping / TexImage parked below the grid (003)
    "colortex_sample": -9900.0,
}

# Legacy colour stage names → shared row.
_COLOUR_STAGE_TO_ROW = {
    "colour": "colour_n",
    "tex": "maps",
    "pattern": "maps",
    "wet": "maps",
    "ov_crease": "maps",
    "ov_edge": "maps",
    "rough": "rough",
    "c_rough": "c_rough",
    "e_rough": "e_rough",
    "metal": "metal",
    "e_metal": "e_metal",
    "c_metal": "c_metal",
    "n_base": "n_base",
    "n_crease": "n_crease",
    "n_edge": "n_edge",
    "n_med": "n_med",
    "mux_col": "colour",
    "mux_rough": "rough",
    "mux_metal": "metal",
    "mux_n": "n_base",
    "occ": "maps",
    "zone_eq": "zone_eq",
}

# Legacy decal stage names → shared row (albedo/metal/rough/normal align with colour).
_DECAL_STAGE_TO_ROW = {
    "rot": "decal_rot",
    "place": "decal_place",
    "flip": "decal_place",
    "color": "decal_color",
    "layer": "decal_tint",
    "tint": "decal_tint",
    "albedo": "colour",
    "data": "decal_data",
    "unpack": "decal_unpack",
    "mix_m": "wear_metal",
    "mix_r": "rough",
    "mix_n": "n_med",
}

# Keep names for callers that still index a stage list.
_COLOUR_STAGES = tuple(_COLOUR_STAGE_TO_ROW.keys())
_GT_GRID_Y0 = _SHARED_ROW_Y["decal_rot"]
_GT_ZONE_Y0 = _GT_GRID_Y0
_GT_SHARED_Y = _SHARED_ROW_Y["colour"]
_GT_BSDF_Y_LIFT = 0.0


def shared_row_y(row: str) -> float:
    return float(_SHARED_ROW_Y[row])


def colour_col_x(zone: int) -> float:
    z = max(1, min(8, int(zone)))
    return _GT_COLOUR_X0 + (z - 1) * _GT_GRID_DX


def colour_row_y(stage: str) -> float:
    row = _COLOUR_STAGE_TO_ROW.get(stage, stage)
    if row in _SHARED_ROW_Y:
        return shared_row_y(row)
    return shared_row_y("colour")


def colour_group_row_y() -> float:
    """GT_ColourN frames sit on the colour_n row (above mux/decal albedo)."""
    return shared_row_y("colour_n")


# Sub-column: Value/RGB satellites sit left of the grid cell they feed.
_GT_SUBCOL_DX = 200.0


def subcol_x(main_x: float) -> float:
    """X for a Value/RGB paired to a node at ``main_x`` (outside the column pitch)."""
    return float(main_x) - _GT_SUBCOL_DX


def maps_uv_x() -> float:
    return _GT_MAPS_UV_X


def maps_ocm_x() -> float:
    return _GT_MAPS_OCM_X


def maps_cm_x() -> float:
    return _GT_MAPS_CM_X


def wear_x() -> float:
    """Shared crease/edge/occ mixes: one column right of Colour 8."""
    return colour_col_x(8) + _GT_GRID_DX


def decal_grid_origin() -> tuple[float, float]:
    """Decal 1 rot cell on the shared ladder."""
    return _GT_DECAL_ORIGIN_X, shared_row_y("decal_rot")


def zone_y(zone: int) -> float:
    """Colour-group row Y. Zones are columns now; ``zone`` is unused."""
    return colour_group_row_y()


def world_loc(n) -> tuple[float, float]:
    return _node_world_loc(n)


def unparent_keep(n) -> None:
    if n is None or getattr(n, "parent", None) is None:
        return
    x, y = world_loc(n)
    try:
        n.parent = None
    except Exception:
        return
    set_node_loc(n, x, y)


def place(nd, x, y, *, hide=None, lock=False, parent=None):
    if nd is None:
        return None
    if parent is None:
        unparent_keep(nd)
    if hide is not None and nd.hide != bool(hide):
        nd.hide = hide
    if parent is not None:
        try:
            nd.parent = parent
        except Exception:
            pass
        _set_node_world_loc(nd, float(x), float(y))
    else:
        # Skips unchanged writes (each one re-runs the whole node-tree update) and defers
        # all of them while the block collapse will lay the tree out again.
        set_node_loc(nd, float(x), float(y))
    _quiet_node(nd)
    if lock:
        lock_layout(nd)
    return nd


@_dr_ops.operation_build
def _reroute(nodes, links, sock, name, x, y):
    """Layout-only bus hop (Shirt 003 cleanup). Returns the reroute Output socket."""
    existing = _dr_primary_candidate.lookup(nodes).get(name)
    if existing is not None and existing.bl_idname == "NodeReroute":
        rr = existing
        place(rr, float(x), float(y), lock=True)
        while rr.inputs[0].is_linked:
            try:
                links.remove(rr.inputs[0].links[0])
            except Exception:
                break
        for ln in list(rr.outputs[0].links):
            try:
                links.remove(ln)
            except Exception:
                pass
    else:
        rr = _dr_ops.new_operation(nodes, 'NodeReroute', name, stage=None, role=None)
        pass
        place(rr, float(x), float(y), lock=True)
    if sock is not None:
        try:
            links.new(sock, rr.inputs[0])
        except Exception:
            pass
    return rr.outputs[0]


def _wire_array_uv_reroutes(nodes, links, uv_src, *, bus_x=13420.0, y_snap=25.0):
    """One ArrayUv reroute per distinct TA image row — no diagonal Vector noodles.

    Groups GT/TA TexImage (and their Mapping) by world Y, snaps to the nearest
    shared TA row when close, drops a reroute on the bus at that Y, and rewires
    every Vector on the row to that hop.
    """
    if uv_src is None:
        return {}
    from collections import defaultdict

    canon = [
        shared_row_y("e_rough"),
        shared_row_y("c_rough"),
        shared_row_y("ta_rough"),
        shared_row_y("ta_n_base"),
        shared_row_y("n_crease"),
        shared_row_y("n_edge"),
        shared_row_y("n_med"),
    ]

    def _snap_y(wy):
        best = min(canon, key=lambda c: abs(c - float(wy)))
        return best if abs(best - float(wy)) <= 200.0 else round(float(wy) / float(y_snap)) * float(y_snap)

    targets = []
    for n in nodes:
        bid = getattr(n, "bl_idname", "") or ""
        name = _dr_ops.legacy_name(n) or ""
        lab = _dr_ops.legacy_label(n) or ""
        if bid == "ShaderNodeTexImage":
            if name.startswith("GT_ColorTex"):
                continue
            if not (
                name.startswith("GT_z")
                or "Masks_" in lab
                or "Normals_" in lab
                or lab.startswith("TA_")
            ):
                continue
            targets.append(n)
        elif bid == "ShaderNodeMapping" and name.startswith("GT_z") and "_uv" in name:
            if name.startswith("GT_TA_ArrayUv"):
                continue
            targets.append(n)

    rows = defaultdict(list)
    for n in targets:
        _wx, wy = world_loc(n)
        key = _snap_y(wy)
        # Also snap the node itself onto the bus row so the hop lines up.
        place(n, world_loc(n)[0], key, hide=getattr(n, "hide", None), lock=True)
        rows[key].append(n)

    # NodeSocket.links filters the whole tree on each access. Snapshot the
    # original first incoming edge once, before adding any layout reroutes.
    incoming = {}
    for link in links:
        incoming.setdefault(link.to_socket, link)

    bus = {}
    for y_key in sorted(rows.keys(), reverse=True):
        group = rows[y_key]
        rr_name = f"GT_RR_uv_y{int(round(y_key))}"
        rr_out = _reroute(nodes, links, uv_src, rr_name, float(bus_x), float(y_key))
        bus[y_key] = rr_out
        for n in group:
            vin = None
            try:
                vin = n.inputs.get("Vector")
            except Exception:
                vin = n.inputs[0] if n.inputs else None
            if vin is None:
                continue
            # Layout only: preserve every authored scale/rotation/final PNG V flip.
            # A linked semantic chain may only be rerouted at a direct bus edge.
            original = incoming.get(vin)
            if original is None or original.from_socket != uv_src:
                continue
            links.remove(original)
            links.new(rr_out, vin)
    return bus


def _insert_reroute_before(links, to_socket, name, x, y, nodes):
    """If ``to_socket`` is linked, splice a reroute on that link. Returns reroute out."""
    if to_socket is None or not to_socket.is_linked:
        return None
    ln = to_socket.links[0]
    fr = ln.from_socket
    try:
        links.remove(ln)
    except Exception:
        return None
    return _reroute(nodes, links, fr, name, x, y)


def _uturn_feed(nodes, links, mix_name, rr_right, rr_left, x_right, y_right, x_left, y_left):
    """Shirt 003 pattern: splice two reroutes on Mix color A (right → left → A)."""
    mix = _dr_primary_candidate.lookup(nodes).get(mix_name)
    if mix is None:
        return
    a_in = None
    for s in mix.inputs:
        if getattr(s, "name", "") == "A" and getattr(s, "type", "") == "RGBA":
            a_in = s
            break
    if a_in is None:
        _fac, a_in, _b, _r = mix_io(mix)
    if a_in is None or not a_in.is_linked:
        return
    fr = a_in.links[0].from_socket
    try:
        links.remove(a_in.links[0])
    except Exception:
        return
    right = _reroute(nodes, links, fr, rr_right, x_right, y_right)
    left = _reroute(nodes, links, right, rr_left, x_left, y_left)
    try:
        links.new(left, a_in)
    except Exception:
        pass


def lock_layout(nd):
    """Skip overlap-nudge so grid / ColorA-B satellites stay put."""
    if nd is None:
        return None
    try:
        nd["arc_layout_lock"] = 1
    except Exception:
        pass
    _quiet_node(nd)
    return nd


def _quiet_node(nd):
    """Shader Editor previews of Mix/Tex/RGB/Group re-eval the whole tree every redraw."""
    if nd is None:
        return
    try:
        # Each RNA write re-runs the node-tree update; skip no-op writes.
        if not nd.hide_preview:
            nd.hide_preview = True
    except Exception:
        pass


# Decal columns use the shared ladder via _DECAL_STAGE_TO_ROW.
# Order listed here is process order only; Y comes from shared rows.
_DECAL_STAGES = (
    "rot", "place", "flip", "color", "layer", "tint", "albedo",
    "data", "unpack", "mix_m", "mix_r", "mix_n",
)
_INPUT_STACK_GAP = 20.0
_DECAL_SAT_W = 180.0
_DECAL_COL_W = 420.0
_DECAL_SLOT_GAP = 80.0
_DECAL_ROW_PITCH = _GT_GRID_DY
_DECAL_SLOT_PITCH = _GT_GRID_DX


def _decal_col_index(slot_idx) -> int:
    try:
        n = int(slot_idx)
    except (TypeError, ValueError):
        n = 0
    return max(0, n - 1) if n >= 1 else max(0, n)


def _decal_slot_xs(origin_x, slot_idx):
    i = _decal_col_index(slot_idx)
    main_x = float(origin_x) + i * _DECAL_SLOT_PITCH
    sat_x = main_x
    return sat_x, main_x


def _decal_row_y(origin_y, stage: str) -> float:
    """Shared-ladder Y for a decal stage. ``origin_y`` is ignored (kept for callers)."""
    row = _DECAL_STAGE_TO_ROW.get(stage)
    if row is None:
        return float(origin_y)
    return shared_row_y(row)


def place_inputs_left_of(host, *input_nodes):
    """Value/RGB satellites: sub-column left of host (outside grid pitch)."""
    if host is None:
        return
    hx, hy = world_loc(host)
    sat_x = subcol_x(hx)
    y = hy
    for nd in input_nodes:
        if nd is None:
            continue
        _w, h = node_footprint(nd)
        parent = getattr(host, "parent", None)
        place(nd, sat_x, y, lock=True, parent=parent)
        y = y - h - _INPUT_STACK_GAP


def place_colors_left_of(host, *rgb_nodes):
    place_inputs_left_of(host, *rgb_nodes)


def node_footprint(n) -> tuple[float, float]:
    bid = getattr(n, "bl_idname", "") or ""
    if bid == "ShaderNodeBsdfPrincipled":
        return 320.0, 820.0
    if bid == "ShaderNodeOutputMaterial":
        return 220.0, 160.0
    if bid == "ShaderNodeTexImage":
        return 300.0, 300.0
    if bid in ("ShaderNodeMix", "ShaderNodeMixRGB"):
        return 240.0, 260.0
    if bid == "ShaderNodeMapping":
        return 280.0, 300.0
    if bid == "ShaderNodeNormalMap":
        return 240.0, 200.0
    if bid == "ShaderNodeMath":
        return 200.0, 180.0
    if bid in ("ShaderNodeSeparateColor", "ShaderNodeSeparateRGB", "ShaderNodeSeparateXYZ"):
        return 200.0, 200.0
    if bid == "ShaderNodeRGB":
        return 180.0, 220.0
    if bid == "ShaderNodeValue":
        return 160.0, 120.0
    if bid == "ShaderNodeTexCoord":
        return 200.0, 220.0
    if bid == "ShaderNodeClamp":
        return 180.0, 160.0
    if bid == "ShaderNodeCombineColor":
        return 180.0, 200.0
    return 220.0, 200.0


def declutter_nodes(nodes, pad: float = _GT_PAD) -> int:
    """Keep parents. Nudge until 200/50/50 padded boxes do not overlap."""
    top = float(pad) if pad and pad > 0 else _GT_PAD_TOP
    return resolve_node_overlaps(nodes, pad_top=top, pad_x=_GT_PAD_X, pad_bottom=_GT_PAD_BOTTOM)


@_dr_ops.operation_build
def make_tex(nodes, fpath, name, loc, *, non_color=False, hide=True):
    # Native sampling flags apply to packed maps too: OCM material IDs can
    # require sRGB decoding before the shader chooses a layer.
    if textures.authored_srgb_sidecar(fpath):
        non_color = False
    elif textures.authored_linear_sidecar(fpath):
        non_color = True
    img = _load_image_cached(fpath, non_color=non_color, alpha_mode="CHANNEL_PACKED")
    if img is None:
        return None
    nd = _dr_ops.new_operation(nodes, 'ShaderNodeTexImage', name, stage='LayerInputs', role=None)
    pass
    _dr_ops.set_label(nd, os.path.basename(fpath))
    nd.image = img
    _assign(nd, "interpolation", "Linear")
    nd.hide = bool(hide)
    set_node_loc(nd, float(loc[0]), float(loc[1]))
    _quiet_node(nd)
    return nd


def _wire_native_opacity_cutout(nodes, links, ocm_node, bsdf, *, masked, clip, x, y):
    # Apply native masked OCM alpha as an explicit binary Cycles input.
    if not masked or ocm_node is None or bsdf is None:
        return None
    alpha_out = ocm_node.outputs.get("Alpha")
    alpha_in = bsdf.inputs.get("Alpha")
    if alpha_out is None or alpha_in is None:
        return None
    try:
        threshold = float(clip)
    except (TypeError, ValueError):
        threshold = 1.0 / 3.0
    if not math.isfinite(threshold):
        threshold = 1.0 / 3.0
    threshold = min(1.0, max(0.0, threshold))
    below = _math(nodes, "LESS_THAN", "GT_NativeOpacityMaskBelowClip", x, y, value=threshold, hide=True)
    links.new(alpha_out, below.inputs[0])
    gate = _math(nodes, "SUBTRACT", "GT_NativeOpacityMaskKeep", x + 180.0, y, hide=True)
    gate.inputs[0].default_value = 1.0
    links.new(below.outputs[0], gate.inputs[1])
    links.new(gate.outputs[0], alpha_in)
    gate["arc_native_opacity_source"] = "OCM.Alpha"
    gate["arc_native_opacity_clip"] = threshold
    gate["arc_native_opacity_comparison"] = "alpha >= clip"
    return gate


def _math(nodes, op, name, x, y, *, value=None, hide=True):
    nd = _dr_ops.new_operation(nodes, "ShaderNodeMath", name)
    _assign(nd, "operation", op)
    nd.hide = hide
    if value is not None:
        try:
            _assign(nd.inputs[1], "default_value", float(value))
        except Exception:
            pass
    place(nd, x, y, hide=hide)
    return nd


def _new_mix(nodes, name, label, x, y, dtype="RGBA", *, hide=False):
    mix = _dr_ops.new_operation(nodes, "ShaderNodeMix", name)
    _assign(mix, "data_type", dtype)
    _assign(mix, "blend_type", "MIX")
    try:
        _assign(mix, "clamp_factor", True)
    except Exception:
        pass
    _dr_ops.set_label(mix, label)
    mix.hide = hide
    place(mix, x, y, hide=hide)
    return mix


def mix_io(mix):
    """Factor / A / B / Result sockets for the Mix node's current ``data_type``.

    Blender 3.4–4 packed every type into one node (RGBA A/B at inputs 6/7).
    Blender 5 exposes one A/B pair; ``inputs["A"]`` is the float socket first, so
    colour links must be picked by ``socket.type``.
    """
    dt = str(getattr(mix, "data_type", "RGBA") or "RGBA")
    want = {"FLOAT": "VALUE", "VECTOR": "VECTOR", "RGBA": "RGBA"}.get(dt, "RGBA")
    color_types = ("RGBA", "COLOR")

    def _pick(sockets, name, stype):
        hits = [
            s for s in sockets
            if getattr(s, "name", "") == name and getattr(s, "type", "") == stype
        ]
        if hits:
            return hits[0]
        if stype == "RGBA":
            hits = [
                s for s in sockets
                if getattr(s, "name", "") == name and getattr(s, "type", "") in color_types
            ]
            if hits:
                return hits[0]
        try:
            return sockets.get(name)
        except Exception:
            return None

    factor = _pick(mix.inputs, "Factor", "VALUE")
    if factor is None and mix.inputs:
        factor = mix.inputs[0]
    a = _pick(mix.inputs, "A", want)
    b = _pick(mix.inputs, "B", want)
    result = _pick(mix.outputs, "Result", want)
    if a is None or b is None or result is None:
        try:
            if dt == "VECTOR":
                a = a or mix.inputs[4]
                b = b or mix.inputs[5]
                result = result or mix.outputs[1]
            elif dt == "FLOAT":
                a = a or mix.inputs[2]
                b = b or mix.inputs[3]
                result = result or mix.outputs[0]
            else:
                a = a or mix.inputs[6]
                b = b or mix.inputs[7]
                result = result or mix.outputs[2]
        except (IndexError, KeyError, TypeError):
            pass
    return factor, a, b, result


def _link_mix(links, mix, fac, a, b):
    factor, a_in, b_in, result = mix_io(mix)
    if fac is not None and factor is not None:
        try:
            links.new(fac, factor)
        except Exception:
            pass
    if a_in is not None:
        links.new(a, a_in)
    if b_in is not None:
        links.new(b, b_in)
    return result


def _gt_ng_sock(ng, name, in_out, socket_type, default=None):
    item = ng.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)
    if default is not None:
        try:
            item.default_value = default
        except Exception:
            pass
    return item


def prune_orphan_nodes(nodes) -> int:
    """Remove nodes whose outputs feed nothing (Material Output has no outputs)."""
    skip_types = {
        "NodeFrame", "NodeReroute", "NodeGroupInput", "NodeGroupOutput",
        "ShaderNodeOutputMaterial",
    }
    removed = 0
    guard = 0
    while guard < 12:
        guard += 1
        dead = []
        for n in list(nodes):
            bid = getattr(n, "bl_idname", "") or ""
            if bid in skip_types or getattr(n, "type", "") == "FRAME":
                continue
            outs = getattr(n, "outputs", None)
            if not outs:
                continue
            if any(getattr(s, "is_linked", False) for s in outs):
                continue
            dead.append(n)
        if not dead:
            break
        for n in dead:
            try:
                nodes.remove(n)
                removed += 1
            except Exception:
                pass
    return removed


def _reset_ng(ng):
    try:
        ng.nodes.clear()
    except Exception:
        for n in list(ng.nodes):
            try:
                ng.nodes.remove(n)
            except Exception:
                pass
    try:
        ng.interface.clear()
    except Exception:
        pass


def _gt_ng_fresh(name, ver):
    """Reuse a group in place. Never leave ``*_stale`` copies in bpy.data."""
    import bpy

    ng = _dr_ops.find_group(bpy.data.node_groups, name)
    if ng is not None and ng.get("arc_gt_ver") == ver:
        return ng, False
    if ng is None:
        ng = _dr_ops.new_group(bpy.data.node_groups, name, "ShaderNodeTree")
    else:
        _reset_ng(ng)
    ng["arc_gt_ver"] = ver
    return ng, True


def _purge_unused_gt_datablocks():
    import bpy

    for ng in list(bpy.data.node_groups):
        n = getattr(ng, "name", "") or ""
        if getattr(ng, "users", 1):
            continue
        if ng.get("dr.group.recipe") or n.startswith("GT_") or "_stale" in n or n.startswith("GT_ZonePick"):
            try:
                bpy.data.node_groups.remove(ng)
            except Exception:
                pass
    for txt in list(bpy.data.texts):
        n = getattr(txt, "name", "") or ""
        if n.startswith("GTNOTE_") and not getattr(txt, "users", 1):
            try:
                bpy.data.texts.remove(txt)
            except Exception:
                pass


def _collapse_broadcast(sock):
    """Hide 1→N noodles without muting. Blender 5 sets NodeLink.is_hidden when the socket is collapsed."""
    if sock is None:
        return
    try:
        sock.show_expanded = False
    except Exception:
        pass


@_dr_ops.operation_build
def ensure_gt_zone_eq1_group():
    """Single-zone COMPARE: Eq = (ZoneIndex == Match).

    One instance per Colour column (Match = Colour N = OCM floor 1–8).
    Matches ARC Texturer Revamped ``COMPARE(layer_id, layer)``.
    """
    name = "GT_ZoneEq1"
    ng, created = _gt_ng_fresh(name, 2)
    if not created:
        return _dr_ops.finish_definition(ng)
    _gt_ng_sock(ng, "ZoneIndex", "INPUT", "NodeSocketFloat", 0.0)
    _gt_ng_sock(ng, "Match", "INPUT", "NodeSocketFloat", 0.0)
    _gt_ng_sock(ng, "Eq", "OUTPUT", "NodeSocketFloat")
    gi = _dr_ops.new_operation(ng.nodes, 'NodeGroupInput', None, stage='Source', role='Layer.ZoneEquality.Input')
    gi.location = (-280.0, 0.0)
    go = _dr_ops.new_operation(ng.nodes, 'NodeGroupOutput', None, stage='Output', role='Layer.ZoneEquality.Output')
    go.location = (280.0, 0.0)
    cmp = _dr_ops.new_operation(ng.nodes, 'ShaderNodeMath', 'eq', stage='LayerInputs', role='Layer.ZoneEquality.CompareZoneToMatch')
    cmp.operation = "COMPARE"
    pass
    _dr_ops.set_label(cmp, 'ZoneIndex == Match')
    cmp.location = (0.0, 0.0)
    try:
        cmp.inputs[2].default_value = 0.45
    except Exception:
        pass
    links = ng.links
    try:
        links.new(gi.outputs["ZoneIndex"], cmp.inputs[0])
        links.new(gi.outputs["Match"], cmp.inputs[1])
        links.new(cmp.outputs[0], go.inputs["Eq"])
    except Exception:
        pass
    _quiet_node(cmp)
    return _dr_ops.finish_definition(ng)


@_dr_ops.operation_build
def ensure_gt_zone_eq_group():
    """Legacy 8-way Eq0..Eq7 hub. Prefer :func:`ensure_gt_zone_eq1_group` per column."""
    name = "GT_ZoneEq"
    ng, created = _gt_ng_fresh(name, 2)
    if not created:
        return _dr_ops.finish_definition(ng)
    _gt_ng_sock(ng, "ZoneIndex", "INPUT", "NodeSocketFloat", 0.0)
    _gt_ng_sock(ng, "Index", "OUTPUT", "NodeSocketFloat")
    for z0 in range(8):
        _gt_ng_sock(ng, f"Eq{z0}", "OUTPUT", "NodeSocketFloat")
    gi = _dr_ops.new_operation(ng.nodes, 'NodeGroupInput', None, stage='Source', role='Layer.ZoneEquality.Input')
    gi.location = (-280.0, 0.0)
    go = _dr_ops.new_operation(ng.nodes, 'NodeGroupOutput', None, stage='Output', role='Layer.ZoneEquality.Output')
    go.location = (280.0, 0.0)
    links = ng.links
    try:
        links.new(gi.outputs["ZoneIndex"], go.inputs["Index"])
    except Exception:
        pass
    for z0 in range(8):
        cmp = _dr_ops.new_operation(ng.nodes, 'ShaderNodeMath', f'eq_z{z0}', stage='LayerInputs', role='Layer.ZoneEquality.CompareZone')
        cmp.operation = "COMPARE"
        pass
        _dr_ops.set_label(cmp, f'== {z0}')
        cmp.location = (0.0, 280.0 - z0 * 80.0)
        try:
            cmp.inputs[1].default_value = float(z0)
            cmp.inputs[2].default_value = 0.45
        except Exception:
            pass
        try:
            links.new(gi.outputs["ZoneIndex"], cmp.inputs[0])
            links.new(cmp.outputs[0], go.inputs[f"Eq{z0}"])
        except Exception:
            pass
        _quiet_node(cmp)
    return _dr_ops.finish_definition(ng)


@_dr_ops.operation_build
def _wire_zone_eq(nodes, links, zone_idx, x, y):
    """One GT_ZoneEq1 at the top of each Colour column. Returns zone→Eq socket."""
    eqs = {}
    if zone_idx is None:
        return None, eqs
    ng = ensure_gt_zone_eq1_group()
    first = None
    for zone in range(1, 9):
        nd = _dr_ops.new_operation(nodes, 'ShaderNodeGroup', f'GT_ZoneEq_z{zone}', stage='LayerInputs', role='Layer.ZoneEquality.nd')
        nd.node_tree = ng
        pass
        _dr_ops.set_label(nd, f'zone == {zone}')
        place(nd, colour_col_x(zone), shared_row_y("zone_eq"), lock=True)
        try:
            links.new(zone_idx, nd.inputs["ZoneIndex"])
            nd.inputs["Match"].default_value = float(zone)
        except Exception:
            if nd.inputs:
                links.new(zone_idx, nd.inputs[0])
                if len(nd.inputs) > 1:
                    nd.inputs[1].default_value = float(zone)
        try:
            eqs[zone] = nd.outputs["Eq"]
        except Exception:
            eqs[zone] = nd.outputs[0] if nd.outputs else None
        try:
            _collapse_broadcast(nd.inputs["ZoneIndex"])
        except Exception:
            pass
        if first is None:
            first = nd
    return first, eqs


@_dr_ops.operation_build
def ensure_gt_albedo_occ_group():
    return _dr_stage_composer('occlusion')


@_dr_ops.operation_build
def ensure_gt_decal_layer_group(mask_int: int):
    """Legacy baked-bit tree. Prefer :func:`ensure_gt_decal_layer_edit_group`."""
    bits = int(mask_int) & 0xFF
    name = f"GT_DecalLayer_{bits}"
    ng, created = _gt_ng_fresh(name, 2)
    if not created:
        return _dr_ops.finish_definition(ng)
    _gt_ng_sock(ng, "ZoneIndex", "INPUT", "NodeSocketFloat", 0.0)
    _gt_ng_sock(ng, "Alpha", "INPUT", "NodeSocketFloat", 1.0)
    _gt_ng_sock(ng, "Masked", "OUTPUT", "NodeSocketFloat")
    gi = _dr_ops.new_operation(ng.nodes, 'NodeGroupInput', None, stage='Source', role='Decal.Coverage.Input')
    gi.location = (-640.0, 0.0)
    go = _dr_ops.new_operation(ng.nodes, 'NodeGroupOutput', None, stage='Output', role='Decal.Coverage.Output')
    go.location = (520.0, 0.0)
    links = ng.links
    gate = None
    x = -360.0
    for z0 in range(8):
        if (bits & (1 << z0)) == 0:
            continue
        z1 = z0 + 1
        cmp = _dr_ops.new_operation(ng.nodes, 'ShaderNodeMath', None, stage='Coverage', role='Decal.Coverage.Compare')
        cmp.operation = "COMPARE"
        _dr_ops.set_label(cmp, f'Colour {z1}')
        cmp.location = (x, 80.0)
        links.new(gi.outputs["ZoneIndex"], cmp.inputs[0])
        try:
            cmp.inputs[1].default_value = float(z1)
            cmp.inputs[2].default_value = 0.45
        except Exception:
            pass
        if gate is None:
            gate = cmp.outputs[0]
        else:
            mx = _dr_ops.new_operation(ng.nodes, 'ShaderNodeMath', None, stage='Coverage', role='Decal.Coverage.Maximum')
            mx.operation = "MAXIMUM"
            mx.location = (x, -40.0)
            links.new(gate, mx.inputs[0])
            links.new(cmp.outputs[0], mx.inputs[1])
            gate = mx.outputs[0]
        x += 160.0
    mul = _dr_ops.new_operation(ng.nodes, 'ShaderNodeMath', None, stage='Coverage', role='Decal.Coverage.Multiply')
    mul.operation = "MULTIPLY"
    mul.location = (x, 0.0)
    links.new(gi.outputs["Alpha"], mul.inputs[0])
    if gate is not None:
        links.new(gate, mul.inputs[1])
    else:
        try:
            mul.inputs[1].default_value = 1.0
        except Exception:
            pass
    try:
        links.new(mul.outputs[0], go.inputs["Masked"])
    except Exception:
        links.new(mul.outputs[0], go.inputs[0])
    return _dr_ops.finish_definition(ng)


@_dr_ops.operation_build
def ensure_gt_decal_layer_edit_group():
    """Editable LayerMask gate: cooked Allow1..8 × optional Excl1..8 checkboxes.

    ZoneIndex is OCM ``floor(B*8+0.5)`` (Colour N = 1–8). AllowN / ExclN map to
    cooked bit ``N-1``. ``Masked = Alpha × OR_z(match_z × Allow × (1−Excl))``.
    """
    name = "GT_DecalLayerEdit"
    ng, created = _gt_ng_fresh(name, 2)
    if not created:
        return _dr_ops.finish_definition(ng)
    _gt_ng_sock(ng, "ZoneIndex", "INPUT", "NodeSocketFloat", 0.0)
    _gt_ng_sock(ng, "Alpha", "INPUT", "NodeSocketFloat", 1.0)
    for z in range(1, 9):
        allow_item = _gt_ng_sock(ng, f"Allow{z}", "INPUT", "NodeSocketFloat", 1.0)
        excl_item = _gt_ng_sock(ng, f"Excl{z}", "INPUT", "NodeSocketFloat", 0.0)
        try:
            allow_item.hide = True  # set from cooked bits; Excl* are the user toggles
        except Exception:
            pass
        try:
            excl_item.description = f"Exclude decal from Colour {z} (OCM layer {z})"
        except Exception:
            pass
    _gt_ng_sock(ng, "Masked", "OUTPUT", "NodeSocketFloat")
    gi = _dr_ops.new_operation(ng.nodes, 'NodeGroupInput', None, stage='Source', role='Decal.Coverage.Edit.Input')
    gi.location = (-900.0, 0.0)
    go = _dr_ops.new_operation(ng.nodes, 'NodeGroupOutput', None, stage='Output', role='Decal.Coverage.Edit.Output')
    go.location = (720.0, 0.0)
    links = ng.links
    gate = None
    x = -560.0
    for z0 in range(8):
        z1 = z0 + 1
        y = 280.0 - z0 * 70.0
        cmp = _dr_ops.new_operation(ng.nodes, 'ShaderNodeMath', None, stage='Coverage', role='Decal.Coverage.Edit.Compare')
        cmp.operation = "COMPARE"
        _dr_ops.set_label(cmp, f'Colour {z1}')
        cmp.location = (x, y)
        links.new(gi.outputs["ZoneIndex"], cmp.inputs[0])
        try:
            cmp.inputs[1].default_value = float(z1)
            cmp.inputs[2].default_value = 0.45
        except Exception:
            pass
        inv = _dr_ops.new_operation(ng.nodes, 'ShaderNodeMath', None, stage='Coverage', role='Decal.Coverage.Edit.InvertExclusion')
        inv.operation = "SUBTRACT"
        _dr_ops.set_label(inv, f'1−Excl{z1}')
        inv.location = (x + 160.0, y - 20.0)
        try:
            inv.inputs[0].default_value = 1.0
        except Exception:
            pass
        links.new(gi.outputs[f"Excl{z1}"], inv.inputs[1])
        mul_a = _dr_ops.new_operation(ng.nodes, 'ShaderNodeMath', None, stage='Coverage', role='Decal.Coverage.Edit.AllowGate')
        mul_a.operation = "MULTIPLY"
        _dr_ops.set_label(mul_a, f'×Allow{z1}')
        mul_a.location = (x + 320.0, y)
        links.new(cmp.outputs[0], mul_a.inputs[0])
        links.new(gi.outputs[f"Allow{z1}"], mul_a.inputs[1])
        mul_e = _dr_ops.new_operation(ng.nodes, 'ShaderNodeMath', None, stage='Coverage', role='Decal.Coverage.Edit.ExcludeGate')
        mul_e.operation = "MULTIPLY"
        _dr_ops.set_label(mul_e, f'gate{z1}')
        mul_e.location = (x + 480.0, y)
        links.new(mul_a.outputs[0], mul_e.inputs[0])
        links.new(inv.outputs[0], mul_e.inputs[1])
        if gate is None:
            gate = mul_e.outputs[0]
        else:
            mx = _dr_ops.new_operation(ng.nodes, 'ShaderNodeMath', None, stage='Coverage', role='Decal.Coverage.Edit.Maximum')
            mx.operation = "MAXIMUM"
            mx.location = (x + 640.0, y)
            links.new(gate, mx.inputs[0])
            links.new(mul_e.outputs[0], mx.inputs[1])
            gate = mx.outputs[0]
    mul = _dr_ops.new_operation(ng.nodes, 'ShaderNodeMath', None, stage='Coverage', role='Decal.Coverage.Edit.Multiply')
    mul.operation = "MULTIPLY"
    _dr_ops.set_label(mul, '× Alpha')
    mul.location = (560.0, 0.0)
    links.new(gi.outputs["Alpha"], mul.inputs[0])
    if gate is not None:
        links.new(gate, mul.inputs[1])
    else:
        try:
            mul.inputs[1].default_value = 1.0
        except Exception:
            pass
    try:
        links.new(mul.outputs[0], go.inputs["Masked"])
    except Exception:
        links.new(mul.outputs[0], go.inputs[0])
    return _dr_ops.finish_definition(ng)


def _decal_layer_sock(gate_n, name: str):
    try:
        return gate_n.inputs[name]
    except Exception:
        for s in getattr(gate_n, "inputs", []) or []:
            if getattr(s, "name", "") == name:
                return s
    return None


def configure_gt_decal_layer_edit(gate_n, allow_mask: int, exclude_mask: int = 0) -> None:
    """Set Allow1..8 / Excl1..8 on a ``GT_DecalLayerEdit`` instance (Colour N = bit N−1)."""
    allow = int(allow_mask) & 0xFF
    excl = int(exclude_mask) & 0xFF
    for z0 in range(8):
        z1 = z0 + 1
        bit = 1 << z0
        a = _decal_layer_sock(gate_n, f"Allow{z1}")
        e = _decal_layer_sock(gate_n, f"Excl{z1}")
        if a is not None:
            try:
                a.default_value = 1.0 if (allow & bit) else 0.0
            except Exception:
                pass
        if e is not None:
            try:
                e.default_value = 1.0 if (excl & bit) else 0.0
            except Exception:
                pass
    try:
        gate_n["arc_decal_layer_mask"] = allow
        gate_n["arc_decal_exclude_mask"] = excl
    except Exception:
        pass


def read_gt_decal_exclude_mask(gate_n) -> int:
    """Read Excl1..8 (or custom prop) → bitmask (bit0 = Colour 1)."""
    try:
        stored = gate_n.get("arc_decal_exclude_mask", None)
        if stored is not None:
            return int(stored) & 0xFF
    except Exception:
        pass
    mask = 0
    for z0 in range(8):
        sock = _decal_layer_sock(gate_n, f"Excl{z0 + 1}")
        if sock is None:
            continue
        try:
            if float(sock.default_value) >= 0.5:
                mask |= 1 << z0
        except Exception:
            pass
    return mask


def apply_decal_exclude_mask_to_material(mat, exclude_mask: int, *, slot: int = 0) -> int:
    """Manual override: set Excl1..8 on top of cooked Allow bits (does not replace them).

    ``slot`` 0 = all decals; 1..8 = that decal index only. Returns gates updated.
    ``exclude_mask`` 0 clears the override and restores cooked-only gating.
    """
    if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return 0
    excl = int(exclude_mask) & 0xFF
    try:
        if excl:
            mat["arc_decal_exclude_mask"] = excl
        elif "arc_decal_exclude_mask" in mat:
            # Clear override so Update Materials stays on cooked LayerMask only.
            del mat["arc_decal_exclude_mask"]
    except Exception:
        try:
            mat["arc_decal_exclude_mask"] = excl
        except Exception:
            pass
    edit = None
    try:
        edit = ensure_gt_decal_layer_edit_group()
    except Exception:
        edit = None
    updated = 0
    for n in mat.node_tree.nodes:
        name = _dr_ops.legacy_name(n) or ""
        lab = _dr_ops.legacy_label(n) or ""
        if n.bl_idname != "ShaderNodeGroup":
            continue
        is_gate = (
            (name.startswith("GT_decal") and name.endswith("_layer"))
            or ("LayerMask" in lab)
            or ("layermask" in lab.lower())
        )
        if not is_gate:
            continue
        idx = None
        try:
            idx = int(n.get("arc_decal_idx", 0) or 0)
        except Exception:
            idx = 0
        if not idx:
            import re

            m = re.search(r"(?i)decal\s*(\d+)", f"{name} {lab}")
            if m:
                idx = int(m.group(1))
        if slot and idx and idx != slot:
            continue
        allow = 255
        try:
            allow = int(n.get("arc_decal_layer_mask", 255) or 255) & 0xFF
        except Exception:
            allow = 255
        if allow <= 0:
            allow = 255
        if edit is not None:
            try:
                # Keep cooked Allow on the editable group; Excl* is the override.
                if _dr_ops.group_key(n.node_tree) != "GT_DecalLayerEdit":
                    n.node_tree = edit
            except Exception:
                pass
            configure_gt_decal_layer_edit(n, allow, excl)
        else:
            try:
                n["arc_decal_exclude_mask"] = excl
            except Exception:
                pass
        excl_txt = ""
        if excl:
            parts = [str(z + 1) for z in range(8) if excl & (1 << z)]
            excl_txt = f"; excl {','.join(parts)}"
        try:
            n.label = f"decal {idx or '?'} LayerMask ({allow}{excl_txt})"
        except Exception:
            pass
        updated += 1
    return updated


@_dr_ops.operation_build
def ensure_gt_colour_n_group():
    """Colour-N assemble with PackedR A/A2 lerp. Prefer per-zone baked groups."""
    import bpy

    name = "GT_ColourN"
    ng, created = _gt_ng_fresh(name, 4)
    if not created:
        return _dr_ops.finish_definition(ng)
    _gt_ng_sock(ng, "ColorMask", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "PackedR", "INPUT", "NodeSocketFloat", 0.0)
    _gt_ng_sock(ng, "Amt", "INPUT", "NodeSocketFloat", 1.0)
    _gt_ng_sock(ng, "Swatch", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "ColorA", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "ColorA2", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "ColorB", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "ColorB2", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "ColorC", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "ColorC2", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "Overlay", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "Colour", "OUTPUT", "NodeSocketColor")
    gi = _dr_ops.new_operation(ng.nodes, 'NodeGroupInput', None, stage='Source', role='Palette.Input')
    gi.location = (-1100.0, 0.0)
    go = _dr_ops.new_operation(ng.nodes, 'NodeGroupOutput', None, stage='Output', role='Palette.Output')
    go.location = (1100.0, 0.0)
    links = ng.links

    def gmix(blend, loc, dtype="RGBA"):
        m = _dr_ops.new_operation(ng.nodes, 'ShaderNodeMix', None, stage='LayerInputs', role='Palette.Blend')
        m.data_type = dtype
        m.blend_type = blend
        try:
            m.clamp_factor = True
        except Exception:
            pass
        m.location = loc
        return m

    mul_sw = gmix("MULTIPLY", (-820.0, 80.0))
    _link_mix(links, mul_sw, None, gi.outputs["ColorMask"], gi.outputs["Swatch"])
    try:
        mix_io(mul_sw)[0].default_value = 1.0
    except Exception:
        pass
    amt3 = _dr_ops.new_operation(ng.nodes, 'ShaderNodeCombineColor', None, stage='LayerInputs', role='Palette.StrengthVector')
    amt3.location = (-820.0, -180.0)
    links.new(gi.outputs["Amt"], amt3.inputs[0])
    links.new(gi.outputs["Amt"], amt3.inputs[1])
    links.new(gi.outputs["Amt"], amt3.inputs[2])
    mul_amt = gmix("MULTIPLY", (-580.0, 40.0))
    w = _link_mix(links, mul_amt, None, mix_io(mul_sw)[3], amt3.outputs[0])
    try:
        mix_io(mul_amt)[0].default_value = 1.0
    except Exception:
        pass
    sep = _dr_ops.new_operation(ng.nodes, 'ShaderNodeSeparateColor', None, stage='LayerInputs', role='Palette.SeparateChannels')
    sep.location = (-340.0, 40.0)
    links.new(w, sep.inputs[0])
    white = _dr_ops.new_operation(ng.nodes, 'ShaderNodeRGB', None, stage='LayerInputs', role='Palette.Canvas')
    white.outputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
    white.location = (-580.0, 260.0)
    out_a = gmix("MIX", (-340.0, 220.0))
    _link_mix(links, out_a, gi.outputs["PackedR"], gi.outputs["ColorA"], gi.outputs["ColorA2"])
    out_b = gmix("MIX", (-340.0, 40.0))
    _link_mix(links, out_b, gi.outputs["PackedR"], gi.outputs["ColorB"], gi.outputs["ColorB2"])
    out_c = gmix("MIX", (-340.0, -140.0))
    _link_mix(links, out_c, gi.outputs["PackedR"], gi.outputs["ColorC"], gi.outputs["ColorC2"])
    mix_a = gmix("MIX", (40.0, 160.0))
    _link_mix(links, mix_a, sep.outputs[0], white.outputs[0], mix_io(out_a)[3])
    mix_b = gmix("MIX", (260.0, 80.0))
    _link_mix(links, mix_b, sep.outputs[1], mix_io(mix_a)[3], mix_io(out_b)[3])
    mix_c = gmix("MIX", (480.0, 0.0))
    folded = _link_mix(links, mix_c, sep.outputs[2], mix_io(mix_b)[3], mix_io(out_c)[3])
    mul_ov = gmix("MULTIPLY", (720.0, 0.0))
    outc = _link_mix(links, mul_ov, None, folded, gi.outputs["Overlay"])
    try:
        mix_io(mul_ov)[0].default_value = 1.0
    except Exception:
        pass
    links.new(outc, go.inputs["Colour"])
    return _dr_ops.finish_definition(ng)


def _as_rgba(val, fallback=(1.0, 1.0, 1.0, 1.0)):
    if val is None:
        return fallback
    if hasattr(val, "outputs"):
        try:
            d = val.outputs[0].default_value
            return (
                float(d[0]),
                float(d[1]),
                float(d[2]),
                float(d[3]) if len(d) > 3 else 1.0,
            )
        except Exception:
            return fallback
    if isinstance(val, dict):
        try:
            return (
                float(val.get("R", fallback[0])),
                float(val.get("G", fallback[1])),
                float(val.get("B", fallback[2])),
                float(val.get("A", fallback[3] if len(fallback) > 3 else 1.0)),
            )
        except (TypeError, ValueError):
            return fallback
    if isinstance(val, (list, tuple)) and len(val) >= 3:
        try:
            a = float(val[3]) if len(val) > 3 else 1.0
            return (float(val[0]), float(val[1]), float(val[2]), a)
        except (TypeError, ValueError):
            return fallback
    return fallback


@_dr_ops.operation_build
def add_gt_note(nodes, name: str, title: str, body: str, x: float, y: float):
    """Frame label only. Do not assign Frame.text — Shader Editor rasterizes it every redraw."""

    fr = _dr_ops.new_operation(nodes, 'NodeFrame', name, stage='LayerInputs', role='Annotation.'+_dr_ops.role_for(name))
    pass
    _dr_ops.set_label(fr, title)
    try:
        fr.label_size = 16
    except Exception:
        pass
    set_node_loc(fr, float(x), float(y))
    extra = (body or "").replace("\n", " — ")
    _dr_ops.set_label(fr, f'{title} — {extra[:140]}' if extra else title)
    return fr


@_dr_ops.operation_build
def build_gt_colour_n_zone_group(
    *,
    zone: int,
    amt: float,
    swatch,
    color_a,
    color_b,
    color_c,
    overlay,
    tree_key: str,
    secondary: bool = False,
    color_a2=None,
    color_b2=None,
    color_c2=None,
):
    """Original contribution-DAG palette composer, retaining public controls."""
    from . import dr_layered_composer
    import sys
    return dr_layered_composer.build_palette(sys.modules[__name__],zone=zone,amt=amt,swatch=swatch,color_a=color_a,color_b=color_b,color_c=color_c,overlay=overlay,tree_key=tree_key,secondary=secondary,color_a2=color_a2,color_b2=color_b2,color_c2=color_c2)


@_dr_ops.operation_build
def ensure_gt_decal_tint_group():
    """ArcDecal colour: native red-channel B/A interpolation, then ColorOverride."""
    import bpy

    name = "GT_DecalTint"
    ng, created = _gt_ng_fresh(name, 3)
    if not created:
        return _dr_ops.finish_definition(ng)
    _gt_ng_sock(ng, "DecalColor", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "ColorA", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "ColorB", "INPUT", "NodeSocketColor", (0.0, 0.0, 0.0, 1.0))
    _gt_ng_sock(ng, "ColorOverride", "INPUT", "NodeSocketFloat", 0.0)
    _gt_ng_sock(ng, "Color", "OUTPUT", "NodeSocketColor")
    gi = _dr_ops.new_operation(ng.nodes, 'NodeGroupInput', None, stage='Source', role='Decal.Tint.Input')
    gi.location = (-700.0, 0.0)
    go = _dr_ops.new_operation(ng.nodes, 'NodeGroupOutput', None, stage='Output', role='Decal.Tint.Output')
    go.location = (520.0, 0.0)
    links = ng.links
    sep = _dr_ops.new_operation(ng.nodes, 'ShaderNodeSeparateColor', None, stage='LayerInputs', role='Decal.Tint.SeparateChannels')
    sep.location = (-480.0, 80.0)
    links.new(gi.outputs["DecalColor"], sep.inputs[0])
    pick = _dr_ops.new_operation(ng.nodes, 'ShaderNodeMix', None, stage='LayerInputs', role='Decal.Tint.SelectAB')
    pick.data_type = "RGBA"
    pick.blend_type = "MIX"
    pick.location = (260.0, -80.0)
    _link_mix(links, pick, sep.outputs["Red"], gi.outputs["ColorB"], gi.outputs["ColorA"])
    ov = _dr_ops.new_operation(ng.nodes, 'ShaderNodeMix', None, stage='LayerInputs', role='Decal.Tint.ColorOverride')
    ov.data_type = "RGBA"
    ov.blend_type = "MIX"
    ov.location = (480.0, 0.0)
    outc = _link_mix(links, ov, gi.outputs["ColorOverride"], gi.outputs["DecalColor"], mix_io(pick)[3])
    links.new(outc, go.inputs["Color"])
    add_gt_note(
        ng.nodes,
        "NOTE_tint",
        "Decal tint (ApplyArcDecals)",
        "mix(ColorB, ColorA, DecalColor.R). Then mix(original, tint, ColorOverride). "
        "Never mix the sticker toward clothing albedo.",
        -700.0,
        280.0,
    )
    return _dr_ops.finish_definition(ng)


def _decal_rgb3(val):
    if val is None:
        return None
    if isinstance(val, dict):
        try:
            return (float(val.get("R", 1.0)), float(val.get("G", 1.0)), float(val.get("B", 1.0)))
        except (TypeError, ValueError):
            return None
    if isinstance(val, (list, tuple)) and len(val) >= 3:
        try:
            return (float(val[0]), float(val[1]), float(val[2]))
        except (TypeError, ValueError):
            return None
    return None


def _sock_color(nd):
    if nd is None:
        return None
    try:
        return nd.outputs["Color"]
    except Exception:
        return nd.outputs[0] if nd.outputs else None


@_dr_ops.operation_build
def _clamp01(nodes, links, sock, name, x, y):
    cl = _dr_ops.new_operation(nodes, 'ShaderNodeClamp', name, stage=None, role=None)
    pass
    try:
        _assign(cl.inputs["Min"], "default_value", 0.0)
        _assign(cl.inputs["Max"], "default_value", 1.0)
    except Exception:
        cl.inputs[1].default_value = 0.0
        cl.inputs[2].default_value = 1.0
    place(cl, x, y)
    try:
        links.new(sock, cl.inputs["Value"])
    except Exception:
        links.new(sock, cl.inputs[0])
    try:
        return cl.outputs["Result"]
    except Exception:
        return cl.outputs[0]


def _value(nodes, name, label, val, x, y):
    nd = _dr_ops.new_operation(nodes, "ShaderNodeValue", name)
    _dr_ops.set_label(nd, label)
    _assign(nd.outputs[0], "default_value", float(val))
    place(nd, x, y)
    return nd.outputs[0]


_NEUTRAL_CACHE = {}


def _neutral_image(group):
    """param_bind.neutral_image once per group per session (each call re-reads and re-checks pixels)."""
    img = _NEUTRAL_CACHE.get(group)
    if img is not None:
        try:
            if img.name and img.packed_file is not None:
                return img
        except ReferenceError:
            pass
    from . import param_bind as _pb
    img = _pb.neutral_image(group)
    _NEUTRAL_CACHE[group] = img
    return img


def _param(nodes, key, val, x, y):
    """The one Value node carrying MI scalar ``key`` (param_bind rewrites it on a colourway swap)."""
    name = f"P:{key}"
    nd = _dr_primary_candidate.lookup(nodes).get(name) or nodes.get(name)
    if nd is not None:
        return nd.outputs[0]
    return _value(nodes, name, key, val, x, y)


def _param_product(nodes, links, socks, name, x, y):
    """Product of scalar sockets with Math nodes (bound tilings stay live, no baked scale)."""
    out = socks[0]
    for i, s in enumerate(socks[1:]):
        nd = _math(nodes, "MULTIPLY", f"{name}_x{i}", x, y, hide=True)
        links.new(out, nd.inputs[0])
        links.new(s, nd.inputs[1])
        out = nd.outputs[0]
    return out


def _rgb(nodes, name, label, rgba, x, y):
    nd = _dr_ops.new_operation(nodes, "ShaderNodeRGB", name)
    _dr_ops.set_label(nd, label)
    nd.outputs[0].default_value = (
        float(rgba[0]),
        float(rgba[1]),
        float(rgba[2]),
        float(rgba[3]) if len(rgba) > 3 else 1.0,
    )
    place(nd, x, y)
    return nd




def mux_by_zone(nodes, links, zone_idx, zone_socks: dict, *, dtype, start, prefix, x_fn=None, y=None, col0=None, y_fn=None, hide_mix=True, zone_eq=None):
    """Stable public API: balanced one-hot or general contribution selectors."""
    return _dr_primary_candidate.mux(
        None, nodes, links, zone_idx, zone_socks,
        dtype=dtype, start=start, prefix=prefix, x_fn=x_fn, y=y,
        col0=col0, y_fn=y_fn, hide_mix=hide_mix, zone_eq=zone_eq,
    )



_ZONE_UV_STAGES = (
    ("Base", "BaseRoughnessTiling"), ("CRough", "CreaseRougnessTiling"), ("ERough", "EdgeRougnessTiling"),
    ("CMask", "CreaseMaskTiling"), ("EMask", "EdgeMaskTiling"), ("BaseN", None),
    ("CreaseN", "CreaseNormalTiling"), ("EdgeN", "EdgeNormalTiling"), ("MediumN", "MediumNormalTiling"),
)


def _zone_uv_group():
    """One shared group per process: UV1 x Tiling x DetailTiling x stage tiling, oriented for sampling."""
    tree, created = _gt_ng_fresh("GT_ZoneLayerUV", 1)
    if not created:
        return tree
    _gt_ng_sock(tree, "UV", "INPUT", "NodeSocketVector", (0.0, 0.0, 0.0))
    for key in ("Tiling", "DetailTiling") + tuple(s for _t, s in _ZONE_UV_STAGES if s):
        _gt_ng_sock(tree, key, "INPUT", "NodeSocketFloat", 1.0)
    for tag, _s in _ZONE_UV_STAGES:
        _gt_ng_sock(tree, tag, "OUTPUT", "NodeSocketVector", (0.0, 0.0, 0.0))
    n, l = tree.nodes, tree.links
    gi = _dr_ops.new_operation(n, "NodeGroupInput", stage="Source", role="ZoneUV.Input")
    go = _dr_ops.new_operation(n, "NodeGroupOutput", stage="Output", role="ZoneUV.Output")
    base = _param_product(n, l, [gi.outputs["Tiling"], gi.outputs["DetailTiling"]], "GT_zU_DetailScale", 0.0, 0.0)
    for i, (tag, suffix) in enumerate(_ZONE_UV_STAGES):
        scale = base if not suffix else _param_product(n, l, [base, gi.outputs[suffix]], f"GT_zU_{tag}Tile", 0.0, i * 80.0)
        uv = _uv_scaled(n, l, gi.outputs["UV"], scale, f"GT_zU_{tag}_uv", 0.0, i * 80.0, z_one=True)
        l.new(orient_uv(n, l, uv, f"GT_zU_{tag}_ImageUV", 0.0, i * 80.0), go.inputs[tag])
    return _dr_ops.finish_definition(tree)


@_dr_ops.operation_build
def _slice_sample(nodes, links, cache, png_list, slice_idx, uv, tile, *, non_color, name, x, y, param=None, group=None, oriented=False):
    if not png_list and not param:
        return None
    png_list = png_list or []
    try:
        idx = int(round(float(slice_idx))) if slice_idx is not None else -1
    except (TypeError, ValueError):
        idx = -1
    # A bound sample is its own node: two zones sharing one would swap to a single slice.
    key = (id(png_list), idx, id(uv) if uv is not None else 0, float(tile), bool(non_color), param)
    if key in cache:
        return cache[key]
    path = textures.find_slice_png(png_list, idx) if idx >= 0 else ""
    if not path:
        # Unused here but swappable: bound sample with a neutral 1x1 image (param_bind.neutral_image).
        if not param or group not in ("masks", "normals"):
            return None
        from . import param_bind as _pb
        nd = _dr_ops.new_operation(nodes, "ShaderNodeTexImage", name)
        nd.image = _neutral_image(group)
        place(nd, x, y, hide=True)
        nd.hide = True
        if uv is not None:   # same UV path as a real slice, so a swapped-in slice samples correctly
            try:
                links.new(uv if oriented else orient_uv(nodes, links, uv, f"{name}_ImageUV", x - 300.0, y), nd.inputs["Vector"])
            except Exception:
                pass
        nd["dr.texture_index_param"] = param
        nd["dr.texture_group"] = group
        cache[key] = nd
        return nd
    mapped = uv
    if abs(float(tile) - 1.0) > 1e-3 and uv is not None:
        mp = _dr_ops.new_operation(nodes, 'ShaderNodeMapping', f'{name}_map', stage='Coordinates', role=None)
        _assign(mp, "vector_type", "POINT")
        pass
        _dr_ops.set_label(mp, f'{name} tile {tile:g}')
        mp.inputs["Scale"].default_value = (float(tile), float(tile), 1.0)
        place(mp, x - _GT_DX * 0.55, y)
        try:
            links.new(uv, mp.inputs["Vector"])
        except Exception:
            pass
        mapped = mp.outputs["Vector"]
    nd = make_tex(nodes, path, name, (x, y), non_color=non_color, hide=True)
    if nd is None:
        return None
    place(nd, x, y, hide=True)
    nd.hide = True
    if mapped is not None:
        try:
            image_uv = mapped if (oriented and mapped is uv) else orient_uv(nodes, links, mapped, f"{name}_ImageUV", x - 300.0, y)
            links.new(image_uv, nd.inputs["Vector"])
        except Exception:
            pass
    if param:
        # param_bind swaps the layer image when a colourway changes the ID (or the whole array).
        nd["dr.texture_index_param"] = param
        nd["dr.texture_group"] = group or ""
    cache[key] = nd
    return nd


@_dr_ops.operation_build
def _uv_scaled(nodes, links, uv, scale_sock, name, x, y, *, z_one=False, hide=True):
    """UV × uniform scale via Mapping.

    ArrayUvScale is isotropic (25 on all axes) — wire the Value straight into
    Mapping Scale (Blender broadcasts float→vector). Only ``z_one`` (tile with
    Z locked to 1) still needs CombineXYZ.
    """
    mp = _dr_ops.new_operation(nodes, 'ShaderNodeMapping', name, stage='Coordinates', role=None)
    _assign(mp, "vector_type", "POINT")
    pass
    _dr_ops.set_label(mp, name.replace('_', ' '))
    mp.hide = hide
    place(mp, x, y, hide=hide)
    try:
        links.new(uv, mp.inputs["Vector"])
    except Exception:
        pass
    if z_one:
        comb = _dr_ops.new_operation(nodes, 'ShaderNodeCombineXYZ', f'{name}_xyz', stage='Coordinates', role=None)
        pass
        _dr_ops.set_label(comb, f'{name} scale')
        comb.hide = hide
        try:
            links.new(scale_sock, comb.inputs[0])
            links.new(scale_sock, comb.inputs[1])
            comb.inputs[2].default_value = 1.0
        except Exception:
            pass
        cw, _ch = node_footprint(comb)
        place(comb, x - cw - _GT_PAD_X * 2, y, hide=hide)
        try:
            links.new(comb.outputs[0], mp.inputs["Scale"])
        except Exception:
            pass
        place_inputs_left_of(comb, getattr(scale_sock, "node", None))
    else:
        try:
            links.new(scale_sock, mp.inputs["Scale"])
        except Exception:
            pass
        place_inputs_left_of(mp, getattr(scale_sock, "node", None))
    return mp.outputs["Vector"]


@_dr_ops.operation_build
def _mask_float(color_sock):
    """Sample the authored mask R channel, independent of G/B and alpha."""
    source = color_sock.node
    tree = source.id_data
    name = f"{_dr_ops.legacy_name(source)}_MaskRed"
    sep = _dr_primary_candidate.lookup(tree.nodes).get(name)
    if sep is None:
        sep = _dr_ops.new_operation(tree.nodes, 'ShaderNodeSeparateColor', name, stage=None, role=None)
        pass
        _dr_ops.set_label(sep, 'TextureArray mask R')
        place(sep, node_loc(source)[0] + 180.0, node_loc(source)[1], hide=True)
        tree.links.new(color_sock, sep.inputs["Color"])
    return sep.outputs["Red"]


@_dr_ops.operation_build
def orient_uv(nodes, links, uv_sock, name, x, y, *, hide=True):
    """Convert between imported Blender UV and native Unreal UV: (u, 1-v, w).

    Transform/tile in native space, then call again before PNG sampling.
    The final flip must follow tiling: 1-T*v, never T*(1-v).
    """
    if uv_sock is None:
        return None
    group = _dr_ops.new_operation(nodes, 'ShaderNodeGroup', name, stage='Coordinates', role=None)
    group.node_tree = _dr_stage_composer('orient_uv')
    _dr_ops.set_label(group, 'UV (Unreal V)')
    place(group, x + 180.0, y, hide=hide)
    try:
        links.new(uv_sock, group.inputs['Vector'])
    except Exception:
        return uv_sock
    return group.outputs['Vector']


@_dr_ops.operation_build
def uv_map_socket(nodes, layer_name, name, x, y, *, hide=False, links=None, orient=True):
    """Named UV Map socket (PSK layers are renamed to UV0 / UV1).

    When ``orient`` and ``links`` are set, apply Revamped V-flip (1-v).
    """
    n = _dr_ops.new_operation(nodes, 'ShaderNodeUVMap', name, stage='Source', role=None)
    pass
    _dr_ops.set_label(n, layer_name)
    try:
        n.uv_map = str(layer_name or "UV0")
    except Exception:
        pass
    place(n, x, y, hide=hide, lock=True)
    sock = n.outputs["UV"]
    if orient and links is not None:
        sock = orient_uv(nodes, links, sock, f"{name}_orient", x + 200.0, y, hide=True)
    return sock


@_dr_ops.operation_build
def ensure_gt_dx_decode_group():
    return _dr_stage_composer('decode')


@_dr_ops.operation_build
def ensure_gt_rnm_group():
    return _dr_stage_composer('rnm')


@_dr_ops.operation_build
def _decode_dx_color(nodes, links, color_sock, name, x, y):
    if color_sock is None:
        return None
    g = _dr_ops.new_operation(nodes, 'ShaderNodeGroup', name, stage=None, role=None)
    g.node_tree = ensure_gt_dx_decode_group()
    pass
    _dr_ops.set_label(g, 'DX RG decode')
    place(g, x, y, hide=True)
    try:
        links.new(color_sock, g.inputs["Color"])
    except Exception:
        return color_sock
    try:
        return g.outputs["Normal"]
    except Exception:
        return g.outputs[0]


@_dr_ops.operation_build
def _tangent_up(nodes, name, x, y):
    c = _dr_ops.new_operation(nodes, 'ShaderNodeCombineXYZ', name, stage=None, role=None)
    pass
    _dr_ops.set_label(c, 'tangent +Z')
    _assign(c.inputs[0], "default_value", 0.0)
    _assign(c.inputs[1], "default_value", 0.0)
    _assign(c.inputs[2], "default_value", 1.0)
    place(c, x, y, hide=True)
    return c.outputs[0]


def _clamp_signed(nodes, links, sock, name, x, y):
    mn = _math(nodes, "MINIMUM", f"{name}_hi", x - 160.0, y, value=1.0, hide=True)
    links.new(sock, mn.inputs[0])
    mx = _math(nodes, "MAXIMUM", f"{name}_lo", x, y, value=-1.0, hide=True)
    links.new(mn.outputs[0], mx.inputs[0])
    return mx.outputs[0]


def _wear_mask(nodes, links, raw, amount, softness, name, x, y, *, decal_wear=None):
    from . import dr_layered_composer
    import sys
    return dr_layered_composer.build_wear(sys.modules[__name__],nodes,links,raw,amount,softness,name,x,y,decal_wear=decal_wear)


@_dr_ops.operation_build
def _quarter_turn_normal(nodes, links, vec, uv0, name, x, y):
    """Rotate tangent XY by floor(UV0.u) * pi/2 (M_Character_Layered)."""
    sep = _dr_ops.new_operation(nodes, 'ShaderNodeSeparateXYZ', f'{name}_uv', stage=None, role=None)
    pass
    place(sep, x - 520.0, y, hide=True)
    links.new(uv0, sep.inputs[0])
    flr = _math(nodes, "FLOOR", f"{name}_floor", x - 360.0, y, hide=True)
    links.new(sep.outputs[0], flr.inputs[0])
    ang = _math(nodes, "MULTIPLY", f"{name}_ang", x - 200.0, y, value=1.5707963267948966, hide=True)
    links.new(flr.outputs[0], ang.inputs[0])
    sn = _math(nodes, "SINE", f"{name}_sin", x - 40.0, y + 40.0, hide=True)
    links.new(ang.outputs[0], sn.inputs[0])
    co = _math(nodes, "COSINE", f"{name}_cos", x - 40.0, y - 40.0, hide=True)
    links.new(ang.outputs[0], co.inputs[0])
    nsep = _dr_ops.new_operation(nodes, 'ShaderNodeSeparateXYZ', f'{name}_n', stage=None, role=None)
    pass
    place(nsep, x - 200.0, y + 160.0, hide=True)
    links.new(vec, nsep.inputs[0])
    nx_co = _math(nodes, "MULTIPLY", f"{name}_nxco", x + 120.0, y + 80.0, hide=True)
    links.new(nsep.outputs[0], nx_co.inputs[0])
    links.new(co.outputs[0], nx_co.inputs[1])
    ny_sn = _math(nodes, "MULTIPLY", f"{name}_nysn", x + 120.0, y + 20.0, hide=True)
    links.new(nsep.outputs[1], ny_sn.inputs[0])
    links.new(sn.outputs[0], ny_sn.inputs[1])
    nx = _math(nodes, "SUBTRACT", f"{name}_nx", x + 280.0, y + 80.0, hide=True)
    links.new(nx_co.outputs[0], nx.inputs[0])
    links.new(ny_sn.outputs[0], nx.inputs[1])
    nx_sn = _math(nodes, "MULTIPLY", f"{name}_nxsn", x + 120.0, y - 40.0, hide=True)
    links.new(nsep.outputs[0], nx_sn.inputs[0])
    links.new(sn.outputs[0], nx_sn.inputs[1])
    ny_co = _math(nodes, "MULTIPLY", f"{name}_nyco", x + 120.0, y - 100.0, hide=True)
    links.new(nsep.outputs[1], ny_co.inputs[0])
    links.new(co.outputs[0], ny_co.inputs[1])
    ny = _math(nodes, "ADD", f"{name}_ny", x + 280.0, y - 40.0, hide=True)
    links.new(nx_sn.outputs[0], ny.inputs[0])
    links.new(ny_co.outputs[0], ny.inputs[1])
    comb = _dr_ops.new_operation(nodes, 'ShaderNodeCombineXYZ', name, stage=None, role=None)
    pass
    place(comb, x + 440.0, y, hide=True)
    links.new(nx.outputs[0], comb.inputs[0])
    links.new(ny.outputs[0], comb.inputs[1])
    links.new(nsep.outputs[2], comb.inputs[2])
    return comb.outputs[0]


@_dr_ops.operation_build
def _encode_dx_for_nmap(nodes, links, vec, name, x, y):
    """Encode DX tangent vector to Blender Normal Map color (Y inverted)."""
    sep = _dr_ops.new_operation(nodes, 'ShaderNodeSeparateXYZ', f'{name}_sep', stage=None, role=None)
    pass
    place(sep, x - 280.0, y, hide=True)
    links.new(vec, sep.inputs[0])
    hx = _math(nodes, "MULTIPLY", f"{name}_hx", x - 80.0, y + 80.0, value=0.5, hide=True)
    links.new(sep.outputs[0], hx.inputs[0])
    px = _math(nodes, "ADD", f"{name}_px", x + 80.0, y + 80.0, value=0.5, hide=True)
    links.new(hx.outputs[0], px.inputs[0])
    hy = _math(nodes, "MULTIPLY", f"{name}_hy", x - 80.0, y, value=0.5, hide=True)
    links.new(sep.outputs[1], hy.inputs[0])
    py = _math(nodes, "SUBTRACT", f"{name}_py", x + 80.0, y, hide=True)
    py.inputs[0].default_value = 0.5
    links.new(hy.outputs[0], py.inputs[1])
    hz = _math(nodes, "MULTIPLY", f"{name}_hz", x - 80.0, y - 80.0, value=0.5, hide=True)
    links.new(sep.outputs[2], hz.inputs[0])
    pz = _math(nodes, "ADD", f"{name}_pz", x + 80.0, y - 80.0, value=0.5, hide=True)
    links.new(hz.outputs[0], pz.inputs[0])
    comb = _dr_ops.new_operation(nodes, 'ShaderNodeCombineColor', name, stage=None, role=None)
    pass
    place(comb, x + 260.0, y, hide=True)
    links.new(px.outputs[0], comb.inputs[0])
    links.new(py.outputs[0], comb.inputs[1])
    links.new(pz.outputs[0], comb.inputs[2])
    return comb.outputs[0]


def _link_zone_picks(nodes, links, zone_idx, zone_outputs, rough_socks, metal_socks, n_base, zone_eq=None):
    """Pick active Colour N without instancing the same node group in a chain.

    Feeding ``GT_ZonePick`` into another ``GT_ZonePick`` makes EEVEE/Cycles/the
    Shader Editor recursively inline that group (same tree, Acc ← previous
    Result) and RAM explodes when Shading compiles the material.
    """
    start_x = colour_col_x(1) - _GT_GRID_DX

    def _hide_start(sock):
        nd = getattr(sock, "node", None)
        if nd is not None:
            nd.hide = True
            _quiet_node(nd)
            lock_layout(nd)

    black = _rgb(
        nodes, "GT_mux_black", "GT mux start (black)", (0.0, 0.0, 0.0, 1.0),
        start_x, colour_row_y("mux_col"),
    )
    _hide_start(black.outputs[0])
    albedo = mux_by_zone(
        nodes, links, zone_idx, zone_outputs or {},
        dtype="RGBA", start=black.outputs[0], prefix="GT_mux_col",
        zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("mux_col"), hide_mix=False,
    )
    r0 = _value(nodes, "GT_rough_start", "rough start", 0.5, start_x, colour_row_y("mux_rough"))
    _hide_start(r0)
    roughness = mux_by_zone(
        nodes, links, zone_idx, rough_socks or {},
        dtype="FLOAT", start=r0, prefix="GT_mux_rough",
        zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("mux_rough"), hide_mix=False,
    )
    m0 = _value(nodes, "GT_metal_start", "metal start", 0.0, start_x, colour_row_y("mux_metal"))
    _hide_start(m0)
    metallic = mux_by_zone(
        nodes, links, zone_idx, metal_socks or {},
        dtype="FLOAT", start=m0, prefix="GT_mux_metal",
        zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("mux_metal"), hide_mix=False,
    )
    n_flat = _tangent_up(nodes, "GT_n_flat", start_x, colour_row_y("mux_n"))
    _hide_start(n_flat)
    detail = mux_by_zone(
        nodes, links, zone_idx, n_base or {},
        dtype="VECTOR", start=n_flat, prefix="GT_mux_nBase",
        zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("mux_n"), hide_mix=False,
    )
    return albedo, roughness, metallic, detail


@_dr_ops.operation_build
def _frame_gt_nodes(nodes) -> None:
    """Colour/decal grids are the layout. Only group the Principled output cluster."""

    def _take(pred):
        out = []
        for n in list(nodes):
            if getattr(n, "type", "") == "FRAME":
                continue
            if getattr(n, "parent", None) is not None:
                continue
            name = _dr_ops.legacy_name(n) or ""
            if pred(name):
                out.append(n)
        return out

    def _mk(fname, label, members):
        if not members:
            return
        fr = _dr_ops.new_operation(nodes, 'NodeFrame', fname, stage='Output', role='Layout.OutputLegacy')
        pass
        _dr_ops.set_label(fr, label)
        fr.label_size = 18
        for n in members:
            wx, wy = world_loc(n)
            try:
                n.parent = fr
            except Exception:
                continue
            _set_node_world_loc(n, wx, wy)
            _quiet_node(n)

    _mk(
        "GT_FR_OUT",
        "Principled output",
        _take(lambda n: n in ("GT_Principled", "GT_NormalMap", "GT_MaterialOutput")),
    )



def _layered_scalar_response(nodes, links, mi_data, zone_eq, crease, edge,
                             suffix, default, *, x, y):
    from . import dr_layered_composer
    import sys
    return dr_layered_composer.build_scalar_response(sys.modules[__name__],nodes,links,mi_data,zone_eq,crease,edge,suffix,default,x=x,y=y)


@_dr_ops.operation_build
def _dry_cloth_specular(nodes, links, specular, mi_data, zone_eq, world_normal):
    from . import dr_layered_composer
    import sys
    return dr_layered_composer.build_cloth(sys.modules[__name__],nodes,links,specular,mi_data,zone_eq,world_normal)


def _hue_variation(nodes, links, albedo, zone_scalars, zone_eq, uv1, tiling, path):
    """One native HueVariation RGB sample after wear; no palette-mask gate."""
    from . import clothing as C
    strengths, scales = {}, {}
    if not path or not any(C._mi_layer_float(zone_scalars, z, "HueVariationStrength", 0) != 0
                           for z in range(1, 9)):
        return albedo
    for zone in range(1, 9):
        strengths[zone] = _value(
            nodes, f"GT_z{zone}_HueStrength", "HueVariationStrength",
            C._mi_layer_float(zone_scalars, zone, "HueVariationStrength", 0) * .1,
            colour_col_x(zone), colour_row_y("ov_edge") - 200,
        )
        scale = tiling * C._mi_layer_float(zone_scalars, zone, "DetailTiling", 1) * C._mi_layer_float(
            zone_scalars, zone, "HueVariationTiling", 1)
        scales[zone] = _value(nodes, f"GT_z{zone}_HueTile", "Native Hue UV1 scale", scale,
                             colour_col_x(zone), colour_row_y("ov_edge") - 400)
    zero = _value(nodes, "GT_Hue_Zero", "Hue off", 0, wear_x(), 0)
    one = _value(nodes, "GT_Hue_One", "Hue default scale", 1, wear_x(), 0)
    amount = mux_by_zone(nodes, links, None, strengths, dtype="FLOAT", start=zero,
                         prefix="GT_mux_HueStrength", zone_eq=zone_eq, x_fn=colour_col_x,
                         y=colour_row_y("ov_edge") - 200)
    scale = mux_by_zone(nodes, links, None, scales, dtype="FLOAT", start=one,
                        prefix="GT_mux_HueTile", zone_eq=zone_eq, x_fn=colour_col_x,
                        y=colour_row_y("ov_edge") - 400)
    native_uv = _uv_scaled(nodes, links, uv1, scale, "GT_Hue_NativeUV", wear_x(), 0, z_one=True)
    # UV1 is already restored to native UE coordinates by uv_map_socket.
    image_uv = orient_uv(nodes, links, native_uv, "GT_Hue_ImageUV", wear_x(), 0)
    image = make_tex(nodes, path, "GT_HueVariation", (wear_x(), 0), non_color=False)
    links.new(image_uv, image.inputs["Vector"])
    mix = _new_mix(nodes, "GT_HueVariationMix", "Native wear + 0.1*S*(HueNoise-wear)",
                   wear_x(), 0)
    mix.clamp_factor = False
    return _link_mix(links, mix, amount, albedo, image.outputs["Color"])

def collapse_enabled() -> bool:
    """True when the block collapse will lay out the finished tree (env DR_GRAPH_BLOCKS != 0)."""
    return os.environ.get("DR_GRAPH_BLOCKS") != "0"


def begin_flat_build(tree) -> bool:
    """Defer node.location writes in tree while a collapse will follow. Returns True when set."""
    if tree is None or not collapse_enabled() or os.environ.get("DR_LOC_DEFER") == "0":
        return False
    begin_deferred_locations(tree)
    return True


def iter_finish_gt_outfit_graph(*, nodes, **kwargs):
    """Mux cooked colour + PBR into Principled. Returns the BSDF node.

    The first yield is "defer": callers that queue the generator prime it with next()
    so its finally (which ends location deferral) runs even if it is closed unstarted.
    """
    tree = nodes.id_data
    begin_flat_build(tree)
    try:
        yield "defer"
        return (yield from _iter_finish_gt_outfit_graph_flat(nodes=nodes, **kwargs))
    finally:
        # Still deferred here only when the build stopped before the collapse: the tree is
        # still the flat graph, so the pending locations belong to its nodes.
        end_deferred_locations(tree, flush=True)
        from . import cw_replay as _cw_replay
        _cw_replay.abort_for(tree)


@_dr_ops.operation_build
def _iter_finish_gt_outfit_graph_flat(
    *,
    nodes,
    links,
    ocm_node,
    cm_src,
    base_tex,
    normal_node,
    zone_outputs: dict,
    colours: dict,
    zone_scalars: dict,
    ta_ids: dict,
    mi_data: dict,
    base_normals: list,
    base_masks: list,
    decals: list,
    decal_folder: str,
    search_dirs: list,
    sparse_colormask: bool = False,
    breakup_node=None,
    colortex_outputs: dict | None = None,
    pattern_png_lists=None,
    hue_variation_path: str = "",
    native_opacity_masked: bool = False,
    native_opacity_clip: float = 1.0 / 3.0,
):
    """Mux cooked colour + PBR into Principled. Returns the BSDF node."""
    from . import clothing as C
    from .. import utils as _utils

    y0 = shared_row_y("maps")
    mx_uv = maps_uv_x()
    mx_ocm = maps_ocm_x()
    mx_cm = maps_cm_x()
    gx = wear_x()
    decal_x, decal_y = decal_grid_origin()

    if ocm_node is not None:
        place(ocm_node, mx_ocm - 400.0, shared_row_y("zone_eq"), lock=True)
    if cm_src is not None:
        place(cm_src, mx_cm, colour_group_row_y(), lock=True)
    if base_tex is not None:
        place(base_tex, mx_cm, colour_row_y("tex"), lock=True)

    yield "start"
    texcoord = None
    uv0 = uv_map_socket(
        nodes, "UV0", "GT_UV0", mx_uv, shared_row_y("metal"), links=links, orient=True,
    )
    uv1 = uv_map_socket(
        nodes, "UV1", "GT_UV1", mx_uv, shared_row_y("c_rough"), links=links, orient=True,
    )
    tiling = C._mi_global_float(zone_scalars, "Tiling", 1.0, mi_data=mi_data)
    uv_bus = {
        "e_rough": uv1,
        "c_rough": uv1,
        "rough": uv1,
        "n_base": uv1,
        "n_crease": uv1,
        "n_edge": uv1,
        "n_med": uv1,
        "uv0": uv0,
        "uv1": uv1,
        "tiling": tiling,
    }

    mid_sock = None
    ocm_r = ocm_g = None
    ocm_sep = None
    if ocm_node is not None and getattr(ocm_node, "outputs", None):
        ocm_sep = _dr_ops.new_operation(nodes, 'ShaderNodeSeparateColor', 'GT_OCM_Separate', stage=None, role=None)
        pass
        _dr_ops.set_label(ocm_sep, 'OCM R=occ G=curv B=mid')
        place(ocm_sep, mx_ocm, shared_row_y("zone_eq"), lock=True)
        try:
            links.new(ocm_node.outputs["Color"], ocm_sep.inputs[0])
        except Exception:
            links.new(ocm_node.outputs[0], ocm_sep.inputs[0])
        try:
            ocm_r = ocm_sep.outputs["Red"]
            ocm_g = ocm_sep.outputs["Green"]
            mid_sock = ocm_sep.outputs["Blue"]
        except Exception:
            ocm_r = ocm_sep.outputs[0]
            ocm_g = ocm_sep.outputs[1] if len(ocm_sep.outputs) > 1 else ocm_r
            mid_sock = ocm_sep.outputs[2] if len(ocm_sep.outputs) > 2 else ocm_r

    zone_idx = None
    zone_eq = {}
    zone_broadcast = None
    if mid_sock is not None:
        # Compact zone math on the zone_eq row (Shirt 003), left of Colour columns.
        zx = colour_col_x(1) - _GT_GRID_DX
        mul = _math(nodes, "MULTIPLY", "GT_mid_x8", zx - 900.0, shared_row_y("zone_eq"), value=8.0, hide=True)
        links.new(mid_sock, mul.inputs[0])
        add = _math(nodes, "ADD", "GT_mid_x8_half", zx - 600.0, shared_row_y("zone_eq"), value=0.5, hide=True)
        links.new(mul.outputs[0], add.inputs[0])
        flr = _dr_ops.new_operation(nodes, 'ShaderNodeMath', 'GT_zone_floor', stage=None, role=None)
        flr.operation = "FLOOR"
        pass
        # Revamped: raw floor(mid×8+0.5); COMPARE to Layer N (1–8), no 0-based shift.
        _dr_ops.set_label(flr, 'zone 1–8 (floor)')
        flr.hide = False
        place(flr, zx, shared_row_y("zone_eq"), hide=False)
        links.new(add.outputs[0], flr.inputs[0])
        zone_idx = flr.outputs[0]
        zeq_node, zone_eq = _wire_zone_eq(nodes, links, zone_idx, mx_ocm, shared_row_y("zone_eq"))
        zone_broadcast = zone_idx
        _ = zeq_node

    # Native decal inputs are shared across colour, surface and normal stages.
    yield "zones_eq"
    from . import dr_layered_decals as decal_comp
    import sys
    decal_slots = _build_gt_decals(C, nodes, links, zone_broadcast, decals or [],
        decal_folder, search_dirs or [], decal_x, decal_y, ocm_node=ocm_node)
    yield "decal_build"
    decal_wear = decal_comp.wear_multiplier(sys.modules[__name__], nodes, links, decal_slots)
    yield "decals"
    crease_fac = None
    edge_fac = None
    body = None
    # Revamped always applies Crease/EdgeColorOverlay; scene toggle defaults on.
    try:
        apply_ce = bool(C.crease_edge_color_enabled_from_scene())
    except Exception:
        apply_ce = True

    packed_sep = None
    packed_r = packed_g = packed_b = None
    if base_tex is not None and getattr(base_tex, "outputs", None):
        packed_sep = _dr_ops.new_operation(nodes, 'ShaderNodeSeparateColor', 'GT_Packed_Separate', stage=None, role=None)
        pass
        _dr_ops.set_label(packed_sep, 'packed R=A/A2 G=crease B=edge')
        place(packed_sep, mx_cm, colour_row_y("tex"), hide=True)
        try:
            links.new(base_tex.outputs["Color"], packed_sep.inputs[0])
        except Exception:
            links.new(base_tex.outputs[0], packed_sep.inputs[0])
        packed_r = packed_sep.outputs[0]
        packed_g = packed_sep.outputs[1] if len(packed_sep.outputs) > 1 else packed_r
        packed_b = packed_sep.outputs[2] if len(packed_sep.outputs) > 2 else packed_r

    br = None
    if breakup_node is not None:
        br_tile = C._mi_global_float(zone_scalars, "BreakupMaskTiling", 1.0, mi_data=mi_data)
        br_uv = uv1
        if abs(float(br_tile) * tiling - 1.0) > 1e-3:
            brs = _value(
                nodes, "GT_BreakupTile", "BreakupMaskTiling",
                max(1e-6, float(br_tile) * tiling), mx_uv, shared_row_y("ocm_wear"),
            )
            br_uv = _uv_scaled(
                nodes, links, uv1, brs, "GT_Breakup_uv", mx_uv, shared_row_y("ocm_wear"),
                z_one=True, hide=True,
            )
        try:
            image_uv = orient_uv(nodes, links, br_uv, "GT_Breakup_ImageUV", mx_uv + 500.0, shared_row_y("ocm_wear"))
            links.new(image_uv, breakup_node.inputs["Vector"])
        except Exception:
            pass
        bsep = _dr_ops.new_operation(nodes, 'ShaderNodeSeparateColor', 'GT_Breakup_sep', stage=None, role=None)
        pass
        place(bsep, gx - 900.0, shared_row_y("ocm_wear"), hide=True)
        try:
            links.new(breakup_node.outputs["Color"], bsep.inputs[0])
        except Exception:
            pass
        br = bsep.outputs[0]
    if br is None:
        br = _value(nodes, "GT_Breakup1", "breakup=1", 1.0, gx - 900.0, shared_row_y("ocm_wear"))

    ta_cache = {}
    rough_socks = {}
    crease_r_socks = {}
    edge_r_socks = {}
    crease_mask_socks = {}
    edge_mask_socks = {}
    metal_socks = {}
    n_base = {}
    n_crease = {}
    n_edge = {}
    n_medium = {}
    uv1 = uv_bus["uv1"]
    uv_layer = {
        "base": uv1,
        "mask": uv1,
    }
    tiling_sock = _param(nodes, "Tiling", tiling, mx_uv, shared_row_y("ta_rough"))
    for zone in range(1, 9):
        if zone > 1:
            yield "zone"
        tx = colour_col_x(zone)
        bid = C._gt_ta_id(ta_ids, zone, "BaseRoughnessID")
        cid = C._gt_ta_id(ta_ids, zone, "CreaseRoughnessID")
        eid = C._gt_ta_id(ta_ids, zone, "EdgeRoughnessID")
        cmid = C._gt_ta_id(ta_ids, zone, "CreaseMaskID")
        emid = C._gt_ta_id(ta_ids, zone, "EdgeMaskID")
        bn = C._gt_ta_id(ta_ids, zone, "BaseNormalID")
        cn = C._gt_ta_id(ta_ids, zone, "CreaseNormalID")
        en = C._gt_ta_id(ta_ids, zone, "EdgeNormalID")
        # Tiling × DetailTiling × stage tiling stay live parameter nodes: a colourway swap rewrites them.
        zuv = _dr_ops.new_operation(nodes, "ShaderNodeGroup", f"GT_z{zone}_LayerUV", stage="Coordinates", role=None)
        zuv.node_tree = _zone_uv_group()
        _dr_ops.set_label(zuv, f"Zone {zone} layer UVs")
        place(zuv, mx_uv, shared_row_y("ta_rough"))
        links.new(uv1, zuv.inputs["UV"])
        links.new(tiling_sock, zuv.inputs["Tiling"])
        links.new(_param(nodes, f"{zone}_DetailTiling", C._mi_layer_float(zone_scalars, zone, "DetailTiling", 1.0),
                         mx_uv, shared_row_y("ta_rough")), zuv.inputs["DetailTiling"])
        for _tag, _suffix in _ZONE_UV_STAGES:
            if _suffix:
                links.new(_param(nodes, f"{zone}_{_suffix}", C._mi_layer_float(zone_scalars, zone, _suffix, 1.0),
                                 mx_uv, shared_row_y("ta_rough")), zuv.inputs[_suffix])

        def _stage_uv(suffix, row, tag):
            # Held in uv_layer: _slice_sample keys its cache on id(uv), so the socket object must stay alive.
            if (zone, tag) not in uv_layer:
                uv_layer[(zone, tag)] = zuv.outputs[tag]
            return uv_layer[(zone, tag)]

        uv_base = _stage_uv("BaseRoughnessTiling", "rough", "Base")
        uv_crough = _stage_uv("CreaseRougnessTiling", "c_rough", "CRough")
        uv_erough = _stage_uv("EdgeRougnessTiling", "e_rough", "ERough")
        uv_cmask = _stage_uv("CreaseMaskTiling", "c_rough", "CMask")
        uv_emask = _stage_uv("EdgeMaskTiling", "e_rough", "EMask")
        uv_bn = _stage_uv(None, "n_base", "BaseN")
        uv_cn = _stage_uv("CreaseNormalTiling", "n_crease", "CreaseN")
        uv_en = _stage_uv("EdgeNormalTiling", "n_edge", "EdgeN")
        # Revamped: clamp(Masks.r + Base/Crease/EdgeRoughness offset).
        nd = _slice_sample(
            nodes, links, ta_cache, base_masks, bid, uv_base, 1.0,
            non_color=True, name=f"GT_z{zone}_BaseRough", x=tx, y=shared_row_y("ta_rough"), param=f"{zone}_BaseRoughnessID", group="masks", oriented=True,
        )
        base_off = C._gt_param_float(mi_data, f"{zone}_BaseRoughness", 0.5)
        if nd is not None:
            add_r = _math(
                nodes, "ADD", f"GT_z{zone}_BaseRough_add", tx, shared_row_y("ta_rough"),
                hide=True,
            )
            links.new(_mask_float(_sock_color(nd)), add_r.inputs[0])
            links.new(_param(nodes, f"{zone}_BaseRoughness", base_off, subcol_x(tx), shared_row_y("ta_rough")), add_r.inputs[1])
            rough_socks[zone] = _clamp01(
                nodes, links, add_r.outputs[0], f"GT_z{zone}_BaseRough_c", tx, shared_row_y("ta_rough"),
            )
        else:
            rough_socks[zone] = _param(nodes, f"{zone}_BaseRoughness", base_off, subcol_x(tx), colour_row_y("rough"))
        nd = _slice_sample(
            nodes, links, ta_cache, base_masks, cid, uv_crough, 1.0,
            non_color=True, name=f"GT_z{zone}_CreaseRough", x=tx, y=colour_row_y("c_rough"), param=f"{zone}_CreaseRoughnessID", group="masks", oriented=True,
        )
        c_off = C._gt_param_float(mi_data, f"{zone}_CreaseRoughness", 0.5)
        if nd is not None:
            add_c = _math(
                nodes, "ADD", f"GT_z{zone}_CreaseRough_add", tx, colour_row_y("c_rough"),
                hide=True,
            )
            links.new(_mask_float(_sock_color(nd)), add_c.inputs[0])
            links.new(_param(nodes, f"{zone}_CreaseRoughness", c_off, subcol_x(tx), colour_row_y("c_rough")), add_c.inputs[1])
            crease_r_socks[zone] = _clamp01(
                nodes, links, add_c.outputs[0], f"GT_z{zone}_CreaseRough_c", tx, colour_row_y("c_rough"),
            )
        else:
            crease_r_socks[zone] = _param(nodes, f"{zone}_CreaseRoughness", c_off, subcol_x(tx), colour_row_y("c_rough"))
        nd = _slice_sample(
            nodes, links, ta_cache, base_masks, eid, uv_erough, 1.0,
            non_color=True, name=f"GT_z{zone}_EdgeRough", x=tx, y=colour_row_y("e_rough"), param=f"{zone}_EdgeRoughnessID", group="masks", oriented=True,
        )
        e_off = C._gt_param_float(mi_data, f"{zone}_EdgeRoughness", 0.5)
        if nd is not None:
            add_e = _math(
                nodes, "ADD", f"GT_z{zone}_EdgeRough_add", tx, colour_row_y("e_rough"),
                hide=True,
            )
            links.new(_mask_float(_sock_color(nd)), add_e.inputs[0])
            links.new(_param(nodes, f"{zone}_EdgeRoughness", e_off, subcol_x(tx), colour_row_y("e_rough")), add_e.inputs[1])
            edge_r_socks[zone] = _clamp01(
                nodes, links, add_e.outputs[0], f"GT_z{zone}_EdgeRough_c", tx, colour_row_y("e_rough"),
            )
        else:
            edge_r_socks[zone] = _param(nodes, f"{zone}_EdgeRoughness", e_off, subcol_x(tx), colour_row_y("e_rough"))
        nd = _slice_sample(
            nodes, links, ta_cache, base_masks, cmid, uv_cmask, 1.0,
            non_color=True, name=f"GT_z{zone}_CreaseMask", x=tx, y=colour_row_y("c_rough"), param=f"{zone}_CreaseMaskID", group="masks", oriented=True,
        )
        if nd is not None:
            crease_mask_socks[zone] = _mask_float(_sock_color(nd))
        nd = _slice_sample(
            nodes, links, ta_cache, base_masks, emid, uv_emask, 1.0,
            non_color=True, name=f"GT_z{zone}_EdgeMask", x=tx, y=colour_row_y("e_rough"), param=f"{zone}_EdgeMaskID", group="masks", oriented=True,
        )
        if nd is not None:
            edge_mask_socks[zone] = _mask_float(_sock_color(nd))
        metal_v = C._gt_param_float(mi_data, f"{zone}_BaseMetallicity", 0.0)
        # Always bound (0 is a valid value): a colourway may turn metal on or off.
        metal_socks[zone] = _param(nodes, f"{zone}_BaseMetallicity", metal_v, subcol_x(tx), colour_row_y("metal"))
        nd = _slice_sample(
            nodes, links, ta_cache, base_normals, bn, uv_bn, 1.0,
            non_color=True, name=f"GT_z{zone}_BaseN", x=tx, y=shared_row_y("ta_n_base"), param=f"{zone}_BaseNormalID", group="normals", oriented=True,
        )
        if nd is not None:
            n_base[zone] = _decode_dx_color(nodes, links, _sock_color(nd), f"GT_z{zone}_BaseN_dx", tx, shared_row_y("ta_n_base"))
        nd = _slice_sample(
            nodes, links, ta_cache, base_normals, cn, uv_cn, 1.0,
            non_color=True, name=f"GT_z{zone}_CreaseN", x=tx, y=colour_row_y("n_crease"), param=f"{zone}_CreaseNormalID", group="normals", oriented=True,
        )
        if nd is not None:
            n_crease[zone] = _decode_dx_color(nodes, links, _sock_color(nd), f"GT_z{zone}_CreaseN_dx", tx, colour_row_y("n_crease"))
        nd = _slice_sample(
            nodes, links, ta_cache, base_normals, en, uv_en, 1.0,
            non_color=True, name=f"GT_z{zone}_EdgeN", x=tx, y=colour_row_y("n_edge"), param=f"{zone}_EdgeNormalID", group="normals", oriented=True,
        )
        if nd is not None:
            n_edge[zone] = _decode_dx_color(nodes, links, _sock_color(nd), f"GT_z{zone}_EdgeN_dx", tx, colour_row_y("n_edge"))
        # Always built: strength is a bound parameter (layered_medium), so 0 is just "off".
        medium_uv = _stage_uv("MediumNormalTiling", "n_med", "MediumN")
        medium_id = C._gt_ta_id(ta_ids, zone, "MediumNormalID")
        sample = _slice_sample(nodes, links, ta_cache, base_normals, medium_id, medium_uv, 1.0,
            non_color=True, name=f"GT_z{zone}_MediumN", x=tx, y=colour_row_y("n_med"), param=f"{zone}_MediumNormalID", group="normals", oriented=True)
        if sample is not None:
            n_medium[zone] = _decode_dx_color(nodes, links, _sock_color(sample), f"GT_z{zone}_MediumN_dx", tx, colour_row_y("n_med"))

    # Wear: packed G/B + curvature + TA crease/edge masks + BreakupMask (Revamped character.py).
    yield "wear"
    if ocm_g is not None:
        curv_m = _math(nodes, "MULTIPLY", "GT_curv_x2", gx - 900.0, shared_row_y("ocm_wear"), value=2.0, hide=True)
        links.new(ocm_g, curv_m.inputs[0])
        curvature = _clamp01(nodes, links, curv_m.outputs[0], "GT_curvature", gx - 700.0, shared_row_y("ocm_wear"))
        if packed_g is not None:
            pg2 = _math(nodes, "MULTIPLY", "GT_pg2", gx - 900.0, shared_row_y("ocm_wear") - 80.0, value=2.0, hide=True)
            links.new(packed_g, pg2.inputs[0])
            pg1 = _math(nodes, "SUBTRACT", "GT_pg1", gx - 740.0, shared_row_y("ocm_wear") - 80.0, value=1.0, hide=True)
            links.new(pg2.outputs[0], pg1.inputs[0])
            two_br = _math(nodes, "SUBTRACT", "GT_2br", gx - 900.0, shared_row_y("ocm_wear") - 160.0, value=2.0, hide=True)
            two_br.inputs[0].default_value = 2.0
            links.new(br, two_br.inputs[1])
            sc_m = _math(nodes, "MULTIPLY", "GT_signed_c_m", gx - 580.0, shared_row_y("ocm_wear") - 80.0, hide=True)
            links.new(pg1.outputs[0], sc_m.inputs[0])
            links.new(two_br.outputs[0], sc_m.inputs[1])
            signed_c = _clamp_signed(nodes, links, sc_m.outputs[0], "GT_signed_c", gx - 400.0, shared_row_y("ocm_wear") - 80.0)
        else:
            signed_c = _value(nodes, "GT_signed_c0", "signed crease 0", 0.0, gx - 400.0, shared_row_y("ocm_wear") - 80.0)
        if packed_b is not None:
            pb2 = _math(nodes, "MULTIPLY", "GT_pb2", gx - 900.0, shared_row_y("ocm_wear") + 120.0, value=2.0, hide=True)
            links.new(packed_b, pb2.inputs[0])
            pb1 = _math(nodes, "SUBTRACT", "GT_pb1", gx - 740.0, shared_row_y("ocm_wear") + 120.0, value=1.0, hide=True)
            links.new(pb2.outputs[0], pb1.inputs[0])
            one_br = _math(nodes, "ADD", "GT_1br", gx - 900.0, shared_row_y("ocm_wear") + 200.0, value=1.0, hide=True)
            links.new(br, one_br.inputs[0])
            se_m = _math(nodes, "MULTIPLY", "GT_signed_e_m", gx - 580.0, shared_row_y("ocm_wear") + 120.0, hide=True)
            links.new(pb1.outputs[0], se_m.inputs[0])
            links.new(one_br.outputs[0], se_m.inputs[1])
            signed_e = _clamp_signed(nodes, links, se_m.outputs[0], "GT_signed_e", gx - 400.0, shared_row_y("ocm_wear") + 120.0)
        else:
            signed_e = _value(nodes, "GT_signed_e0", "signed edge 0", 0.0, gx - 400.0, shared_row_y("ocm_wear") + 120.0)
        if ocm_r is not None:
            g05 = _math(nodes, "SUBTRACT", "GT_g05", gx - 900.0, shared_row_y("ocm_wear") + 280.0, value=0.5, hide=True)
            links.new(ocm_g, g05.inputs[0])
            g2 = _math(nodes, "MULTIPLY", "GT_g2", gx - 740.0, shared_row_y("ocm_wear") + 280.0, value=2.0, hide=True)
            links.new(g05.outputs[0], g2.inputs[0])
            gr = _math(nodes, "MULTIPLY", "GT_gr", gx - 580.0, shared_row_y("ocm_wear") + 280.0, hide=True)
            links.new(g2.outputs[0], gr.inputs[0])
            links.new(ocm_r, gr.inputs[1])
            grc = _clamp01(nodes, links, gr.outputs[0], "GT_grc", gx - 400.0, shared_row_y("ocm_wear") + 280.0)
            edge_base = _math(nodes, "SUBTRACT", "GT_edge_base", gx - 220.0, shared_row_y("ocm_wear") + 280.0, hide=True)
            edge_base.inputs[0].default_value = 1.0
            links.new(grc, edge_base.inputs[1])
            edge_base = edge_base.outputs[0]
        else:
            edge_base = _value(nodes, "GT_edge_base1", "edge base 1", 1.0, gx - 220.0, shared_row_y("ocm_wear") + 280.0)
        crease_z = {}
        edge_z = {}
        for zone in range(1, 9):
            ctex = crease_mask_socks.get(zone)
            etex = edge_mask_socks.get(zone)
            if ctex is None:
                ctex = _value(nodes, f"GT_z{zone}_cMask0", "crease mask 0", 0.0, colour_col_x(zone), shared_row_y("ocm_wear"))
            if etex is None:
                etex = _value(nodes, f"GT_z{zone}_eMask0", "edge mask 0", 0.0, colour_col_x(zone), shared_row_y("ocm_wear"))
            csum = _math(nodes, "ADD", f"GT_z{zone}_csum", colour_col_x(zone), shared_row_y("ocm_wear"), hide=True)
            links.new(curvature, csum.inputs[0])
            links.new(ctex, csum.inputs[1])
            cmul = _math(nodes, "MULTIPLY", f"GT_z{zone}_cmul", colour_col_x(zone), shared_row_y("ocm_wear"), hide=True)
            links.new(curvature, cmul.inputs[0])
            links.new(csum.outputs[0], cmul.inputs[1])
            craw = _math(nodes, "ADD", f"GT_z{zone}_craw", colour_col_x(zone), shared_row_y("ocm_wear"), hide=True)
            links.new(cmul.outputs[0], craw.inputs[0])
            links.new(signed_c, craw.inputs[1])
            amount_c = C._mi_layer_float(zone_scalars, zone, "CreaseAmount", 0.0)
            # Softness 0 → hard LESS_THAN (Revamped); do not coerce to 1.0.
            soft_c = C._mi_layer_float(zone_scalars, zone, "CreaseMaskSoftness", 1.0)
            crease_z[zone] = _wear_mask(
                nodes, links, craw.outputs[0], amount_c, soft_c,
                f"GT_z{zone}_crease", colour_col_x(zone), shared_row_y("ocm_wear"),
                decal_wear=decal_wear,
            )
            e1 = _math(nodes, "ADD", f"GT_z{zone}_e1", colour_col_x(zone), shared_row_y("ocm_wear") + 200.0, value=1.0, hide=True)
            e1.inputs[0].default_value = 1.0
            links.new(edge_base, e1.inputs[1])
            e2 = _math(nodes, "SUBTRACT", f"GT_z{zone}_e2", colour_col_x(zone), shared_row_y("ocm_wear") + 200.0, hide=True)
            links.new(e1.outputs[0], e2.inputs[0])
            links.new(etex, e2.inputs[1])
            emul = _math(nodes, "MULTIPLY", f"GT_z{zone}_emul", colour_col_x(zone), shared_row_y("ocm_wear") + 200.0, hide=True)
            links.new(edge_base, emul.inputs[0])
            links.new(e2.outputs[0], emul.inputs[1])
            eraw = _math(nodes, "ADD", f"GT_z{zone}_eraw", colour_col_x(zone), shared_row_y("ocm_wear") + 200.0, hide=True)
            links.new(emul.outputs[0], eraw.inputs[0])
            links.new(signed_e, eraw.inputs[1])
            amount_e = C._mi_layer_float(zone_scalars, zone, "EdgeAmount", 0.0)
            soft_e = C._mi_layer_float(zone_scalars, zone, "EdgeMaskSoftness", 1.0)
            edge_z[zone] = _wear_mask(
                nodes, links, eraw.outputs[0], amount_e, soft_e,
                f"GT_z{zone}_edge", colour_col_x(zone), shared_row_y("ocm_wear") + 200.0,
                decal_wear=decal_wear,
            )
            yield "wear_masks"
        z0c = _value(nodes, "GT_crease0", "crease 0", 0.0, colour_col_x(1) - _GT_GRID_DX, shared_row_y("ocm_wear"))
        z0e = _value(nodes, "GT_edge0", "edge 0", 0.0, colour_col_x(1) - _GT_GRID_DX, shared_row_y("ocm_wear") + 200.0)
        crease_fac = mux_by_zone(
            nodes, links, zone_idx, crease_z, dtype="FLOAT", start=z0c, prefix="GT_mux_crease",
            zone_eq=zone_eq, x_fn=colour_col_x, y=shared_row_y("ocm_wear"),
        )
        yield "wear_masks"
        edge_fac = mux_by_zone(
            nodes, links, zone_idx, edge_z, dtype="FLOAT", start=z0e, prefix="GT_mux_edge",
            zone_eq=zone_eq, x_fn=colour_col_x, y=shared_row_y("ocm_wear") + 200.0,
        )
        # Clear user knobs (Revamped exposes "Crease/Edge amount"; these scale the
        # muxed wear fac after MI amounts). Default 1.0 = MI parity.
        str_x = gx + 40.0
        crease_str = _value(
            nodes, "GT_CreaseStrength", "Crease Strength", 1.0,
            str_x, shared_row_y("ocm_wear") - 40.0,
        )
        edge_str = _value(
            nodes, "GT_EdgeStrength", "Edge Strength", 1.0,
            str_x, shared_row_y("ocm_wear") + 240.0,
        )
        for _nm in ("GT_CreaseStrength", "GT_EdgeStrength"):
            nd = _dr_primary_candidate.lookup(nodes).get(_nm)
            if nd is not None:
                nd.hide = False
                try:
                    nd.hide_preview = False
                except Exception:
                    pass
        cm = _math(nodes, "MULTIPLY", "GT_crease_x_str", str_x + 180.0, shared_row_y("ocm_wear"), hide=True)
        links.new(crease_fac, cm.inputs[0])
        links.new(crease_str, cm.inputs[1])
        crease_fac = _clamp01(nodes, links, cm.outputs[0], "GT_crease_fac", str_x + 360.0, shared_row_y("ocm_wear"))
        em = _math(nodes, "MULTIPLY", "GT_edge_x_str", str_x + 180.0, shared_row_y("ocm_wear") + 200.0, hide=True)
        links.new(edge_fac, em.inputs[0])
        links.new(edge_str, em.inputs[1])
        edge_fac = _clamp01(nodes, links, em.outputs[0], "GT_edge_fac", str_x + 360.0, shared_row_y("ocm_wear") + 200.0)

    yield "crease_edge"
    if crease_fac is not None and edge_fac is not None:
        ce = _math(nodes, "ADD", "GT_ce", gx, shared_row_y("ocm_wear"), hide=True)
        links.new(crease_fac, ce.inputs[0])
        links.new(edge_fac, ce.inputs[1])
        one_ce = _math(nodes, "SUBTRACT", "GT_1ce", gx + 160.0, shared_row_y("ocm_wear"), hide=True)
        one_ce.inputs[0].default_value = 1.0
        links.new(ce.outputs[0], one_ce.inputs[1])
        body2 = _math(nodes, "MULTIPLY", "GT_body2", gx + 320.0, shared_row_y("ocm_wear"), value=2.0, hide=True)
        links.new(one_ce.outputs[0], body2.inputs[0])
        body = _clamp01(nodes, links, body2.outputs[0], "GT_body", gx + 500.0, shared_row_y("ocm_wear"))
        for zone in range(1, 9):
            gnode = _dr_primary_candidate.lookup(nodes).get(f"GT_ColourN_z{zone}")
            if gnode is None or "Amt" not in getattr(gnode, "inputs", {}):
                continue
            bs = C._mi_layer_float(zone_scalars, zone, "BaseColorMaskStrength", 1.0)
            es = C._mi_layer_float(zone_scalars, zone, "EdgeColorMaskStrength", 0.0)
            cs = C._mi_layer_float(zone_scalars, zone, "CreaseColorMaskStrength", 0.0)
            mb = _math(nodes, "MULTIPLY", f"GT_z{zone}_amtB", colour_col_x(zone), shared_row_y("ocm_wear") - 120.0, hide=True)
            links.new(body, mb.inputs[0])
            links.new(_param(nodes, f"{zone}_BaseColorMaskStrength", bs, subcol_x(colour_col_x(zone)), shared_row_y("ocm_wear") - 120.0), mb.inputs[1])
            me = _math(nodes, "MULTIPLY", f"GT_z{zone}_amtE", colour_col_x(zone), shared_row_y("ocm_wear") - 120.0, hide=True)
            links.new(edge_fac, me.inputs[0])
            links.new(_param(nodes, f"{zone}_EdgeColorMaskStrength", es, subcol_x(colour_col_x(zone)), shared_row_y("ocm_wear") - 120.0), me.inputs[1])
            mc = _math(nodes, "MULTIPLY", f"GT_z{zone}_amtC", colour_col_x(zone), shared_row_y("ocm_wear") - 120.0, hide=True)
            links.new(crease_fac, mc.inputs[0])
            links.new(_param(nodes, f"{zone}_CreaseColorMaskStrength", cs, subcol_x(colour_col_x(zone)), shared_row_y("ocm_wear") - 120.0), mc.inputs[1])
            s1 = _math(nodes, "ADD", f"GT_z{zone}_amt1", colour_col_x(zone), shared_row_y("ocm_wear") - 120.0, hide=True)
            links.new(mb.outputs[0], s1.inputs[0])
            links.new(me.outputs[0], s1.inputs[1])
            s2 = _math(nodes, "ADD", f"GT_z{zone}_amt2", colour_col_x(zone), shared_row_y("ocm_wear") - 120.0, hide=True)
            links.new(s1.outputs[0], s2.inputs[0])
            links.new(mc.outputs[0], s2.inputs[1])
            amt = _clamp01(nodes, links, s2.outputs[0], f"GT_z{zone}_amt", colour_col_x(zone), shared_row_y("ocm_wear") - 120.0)
            try:
                for lnk in list(gnode.inputs["Amt"].links):
                    links.remove(lnk)
                links.new(amt, gnode.inputs["Amt"])
            except Exception:
                pass
            yield "wear_amounts"
        if packed_r is not None:
            for zone in range(1, 9):
                gnode = _dr_primary_candidate.lookup(nodes).get(f"GT_ColourN_z{zone}")
                if gnode is None:
                    continue
                try:
                    if "PackedR" in gnode.inputs:
                        links.new(packed_r, gnode.inputs["PackedR"])
                except Exception:
                    pass

    yield "wear_amounts"
    albedo, roughness, metallic, detail = _link_zone_picks(
        nodes, links, zone_idx, zone_outputs, rough_socks, metal_socks, n_base,
        zone_eq=zone_eq,
    )

    yield "zone_picks"
    roughness, metallic = decal_comp.surface(sys.modules[__name__], nodes, links,
        roughness, metallic, decal_slots)

    # Native order: palette/ColorTex/pattern, then decals, then garment wear.
    yield "colortex"
    if colortex_outputs:
        white = _dr_primary_candidate.lookup(nodes).get("GT_canvas_white")
        white_s = white.outputs[0] if white is not None else _rgb(
            nodes, "GT_canvas_white", "GT canvas white", (1.0, 1.0, 1.0, 1.0),
            gx, colour_row_y("tex"),
        ).outputs[0]
        ct = mux_by_zone(
            nodes, links, zone_idx, colortex_outputs, dtype="RGBA", start=white_s,
            prefix="GT_mux_colortex", zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("tex"),
        )
        bs_vals, es_vals, cs_vals = {}, {}, {}
        for zone in range(1, 9):
            if zone not in colortex_outputs:
                continue
            bs_vals[zone] = _value(
                nodes, f"GT_z{zone}_bts", "BaseTextureStrength",
                max(0.0, min(1.0, C._mi_layer_float(zone_scalars, zone, "BaseTextureStrength", 0.0))),
                subcol_x(colour_col_x(zone)), colour_row_y("tex"),
            )
            es_vals[zone] = _value(
                nodes, f"GT_z{zone}_ets", "EdgeTextureStrength",
                max(0.0, min(1.0, C._mi_layer_float(zone_scalars, zone, "EdgeTextureStrength", 0.0))),
                subcol_x(colour_col_x(zone)), colour_row_y("tex"),
            )
            cs_vals[zone] = _value(
                nodes, f"GT_z{zone}_cts", "CreaseTextureStrength",
                max(0.0, min(1.0, C._mi_layer_float(zone_scalars, zone, "CreaseTextureStrength", 0.0))),
                subcol_x(colour_col_x(zone)), colour_row_y("tex"),
            )
        z0 = _value(nodes, "GT_texStr0", "tex 0", 0.0, colour_col_x(1) - _GT_GRID_DX, colour_row_y("tex"))
        mux_bs = mux_by_zone(nodes, links, zone_idx, bs_vals, dtype="FLOAT", start=z0, prefix="GT_mux_bts", zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("tex")) if bs_vals else z0
        mux_es = mux_by_zone(nodes, links, zone_idx, es_vals, dtype="FLOAT", start=z0, prefix="GT_mux_ets", zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("tex")) if es_vals else z0
        mux_cs = mux_by_zone(nodes, links, zone_idx, cs_vals, dtype="FLOAT", start=z0, prefix="GT_mux_cts", zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("tex")) if cs_vals else z0
        body_s = mux_bs
        if body is not None:
            bm = _math(nodes, "MULTIPLY", "GT_tex_body", gx, colour_row_y("tex"), hide=True)
            links.new(body, bm.inputs[0])
            links.new(mux_bs, bm.inputs[1])
            body_s = _clamp01(nodes, links, bm.outputs[0], "GT_tex_body_c", gx, colour_row_y("tex"))
        detail_c = _link_mix(
            links, _new_mix(nodes, "GT_tex_body_mix", "lerp(1,ColorTex,body)", gx, colour_row_y("tex")),
            body_s, white_s, ct,
        )
        if edge_fac is not None:
            em = _math(nodes, "MULTIPLY", "GT_tex_edge", gx, colour_row_y("tex"), hide=True)
            links.new(edge_fac, em.inputs[0])
            links.new(mux_es, em.inputs[1])
            ef = _clamp01(nodes, links, em.outputs[0], "GT_tex_edge_c", gx, colour_row_y("tex"))
            detail_c = _link_mix(
                links, _new_mix(nodes, "GT_tex_edge_mix", "ColorTex edge", gx, colour_row_y("tex")),
                ef, detail_c, ct,
            )
        if crease_fac is not None:
            cm = _math(nodes, "MULTIPLY", "GT_tex_crease", gx, colour_row_y("tex"), hide=True)
            links.new(crease_fac, cm.inputs[0])
            links.new(mux_cs, cm.inputs[1])
            cf = _clamp01(nodes, links, cm.outputs[0], "GT_tex_crease_c", gx, colour_row_y("tex"))
            detail_c = _link_mix(
                links, _new_mix(nodes, "GT_tex_crease_mix", "ColorTex crease", gx, colour_row_y("tex")),
                cf, detail_c, ct,
            )
        mul = _new_mix(nodes, "GT_albedo_x_tex", "albedo x ColorTex", gx, colour_row_y("tex"))
        mul.blend_type = "MULTIPLY"
        try:
            mix_io(mul)[0].default_value = 1.0
        except Exception:
            pass
        albedo = _link_mix(links, mul, None, albedo, detail_c)

    # Native layered order: ColorTex first, pattern swatches next, wear last.
    # Pattern ID 0 is a real array slice. Zone and ColorMask gates keep each
    # swatch confined to its authored material/palette channels.
    yield "pattern"
    if pattern_png_lists and cm_src is not None:
        pattern_images, pattern_cache = {}, {}
        for zone in range(1, 9):
            albedo = C._wire_gt_zone_pattern_hue(
                nodes, links, None, colours or {}, zone_scalars, ta_ids,
                zone, albedo, colour_row_y("pattern"), pattern_png_lists,
                pattern_images, pattern_cache, col_x=colour_col_x(zone),
                hide=True, omit_hue_variation=True,
                color_mask_socket=_sock_color(cm_src),
                zone_mask_socket=zone_eq.get(zone), global_tiling=tiling,
            )
            yield "pattern"

    if sparse_colormask and cm_src is not None and base_tex is not None:
        cm_sep = _dr_ops.new_operation(nodes, 'ShaderNodeSeparateColor', 'GT_ColorMask_cov', stage=None, role=None)
        pass
        _dr_ops.set_label(cm_sep, 'ColorMask coverage (RadioBag)')
        place(cm_sep, mx_cm, colour_row_y("pattern"), hide=True)
        try:
            links.new(cm_src.outputs["Color"], cm_sep.inputs[0])
        except Exception:
            links.new(cm_src.outputs[0], cm_sep.inputs[0])
        add = _math(nodes, "ADD", "GT_cm_rg", mx_cm, colour_row_y("wet"))
        try:
            links.new(cm_sep.outputs["Red"], add.inputs[0])
            links.new(cm_sep.outputs["Green"], add.inputs[1])
        except Exception:
            links.new(cm_sep.outputs[0], add.inputs[0])
        add2 = _math(nodes, "ADD", "GT_cm_rgb", mx_cm, colour_row_y("ov_crease"))
        links.new(add.outputs[0], add2.inputs[0])
        try:
            links.new(cm_sep.outputs["Blue"], add2.inputs[1])
        except Exception:
            pass
        cov = _clamp01(nodes, links, add2.outputs[0], "GT_cm_cov", mx_cm, colour_row_y("ov_edge"))
        mix = _new_mix(nodes, "GT_basecolor_fallback", "sparse ColorMask → BaseColor", gx, colour_row_y("mux_col"))
        albedo = _link_mix(links, mix, cov, _sock_color(base_tex), albedo)

    yield "albedo_decals"
    albedo = decal_comp.color(sys.modules[__name__], nodes, links, albedo, decal_slots)
    yield "decal_colour"
    crease_ov = {}
    edge_ov = {}
    crease_ov_a = {}
    edge_ov_a = {}
    if apply_ce:
        for zone in range(1, 9):
            ck = f"{zone}_CreaseColorOverlay"
            ek = f"{zone}_EdgeColorOverlay"
            cr = (colours or {}).get(ck)
            er = (colours or {}).get(ek)
            # Always built from the authored value (unset = transparent black): alpha is the blend
            # weight, so black at alpha 0.7 is real crease darkening, not a placeholder to skip.
            if cr is None:
                cr = (0.0, 0.0, 0.0, 0.0)
            nd = _dr_primary_candidate.lookup(nodes).get(ck) or _rgb(nodes, ck, ck, cr, 0.0, 0.0)
            crease_ov[zone] = nd.outputs[0]
            # Separate Color has no Alpha: MI A is its own bound float (Revamped
            # uses overlay[3]; falling back to 1.0 was over-applying wear colour).
            a = float(cr[3]) if len(cr) > 3 else 0.0
            crease_ov_a[zone] = _param(nodes, f"{ck}.A", a, subcol_x(colour_col_x(zone)), colour_row_y("ov_crease"))
            if er is None:
                er = (0.0, 0.0, 0.0, 0.0)
            nd = _dr_primary_candidate.lookup(nodes).get(ek) or _rgb(nodes, ek, ek, er, 0.0, 0.0)
            edge_ov[zone] = nd.outputs[0]
            a = float(er[3]) if len(er) > 3 else 0.0
            edge_ov_a[zone] = _param(nodes, f"{ek}.A", a, subcol_x(colour_col_x(zone)), colour_row_y("ov_edge"))
        zero = None
        yield "overlays"
        if crease_ov or edge_ov:
            zero = _rgb(
                nodes, "GT_overlay_zero", "overlay idle", (0.0, 0.0, 0.0, 0.0),
                colour_col_x(1) - _GT_GRID_DX, colour_row_y("ov_crease"),
            )
            zero.hide = True
            _quiet_node(zero)
        if crease_ov and zero is not None:
            crease_col = mux_by_zone(
                nodes, links, zone_idx, crease_ov,
                dtype="RGBA", start=zero.outputs[0], prefix="GT_mux_creaseOv",
                zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("ov_crease"),
            )
            if crease_fac is not None:
                z0a = _value(
                    nodes, "GT_creaseOvA0", "crease α 0", 0.0,
                    colour_col_x(1) - _GT_GRID_DX, colour_row_y("ov_crease"),
                )
                crease_a = mux_by_zone(
                    nodes, links, zone_idx, crease_ov_a, dtype="FLOAT", start=z0a,
                    prefix="GT_mux_creaseOvA", zone_eq=zone_eq,
                    x_fn=colour_col_x, y=colour_row_y("ov_crease"),
                ) if crease_ov_a else z0a
                mul = _math(nodes, "MULTIPLY", "GT_creaseOv_fac", gx, colour_row_y("ov_crease"), hide=True)
                links.new(crease_fac, mul.inputs[0])
                links.new(crease_a, mul.inputs[1])
                mix = _new_mix(nodes, "GT_apply_creaseOv", "crease overlay", gx, colour_row_y("ov_crease"))
                albedo = _link_mix(links, mix, mul.outputs[0], albedo, crease_col)
            yield "overlays"
            for zone, sock in crease_ov.items():
                nd = getattr(sock, "node", None)
                if nd is not None:
                    nd.hide = True
                    place(nd, colour_col_x(zone), colour_row_y("ov_crease"), hide=True)
        if edge_ov and zero is not None:
            edge_col = mux_by_zone(
                nodes, links, zone_idx, edge_ov,
                dtype="RGBA", start=zero.outputs[0], prefix="GT_mux_edgeOv",
                zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("ov_edge"),
            )
            if edge_fac is not None:
                z0a = _value(
                    nodes, "GT_edgeOvA0", "edge α 0", 0.0,
                    colour_col_x(1) - _GT_GRID_DX, colour_row_y("ov_edge"),
                )
                edge_a = mux_by_zone(
                    nodes, links, zone_idx, edge_ov_a, dtype="FLOAT", start=z0a,
                    prefix="GT_mux_edgeOvA", zone_eq=zone_eq,
                    x_fn=colour_col_x, y=colour_row_y("ov_edge"),
                ) if edge_ov_a else z0a
                mul = _math(nodes, "MULTIPLY", "GT_edgeOv_fac", gx, colour_row_y("ov_edge"), hide=True)
                links.new(edge_fac, mul.inputs[0])
                links.new(edge_a, mul.inputs[1])
                mix = _new_mix(nodes, "GT_apply_edgeOv", "edge overlay", gx, colour_row_y("ov_edge"))
                albedo = _link_mix(links, mix, mul.outputs[0], albedo, edge_col)
            for zone, sock in edge_ov.items():
                nd = getattr(sock, "node", None)
                if nd is not None:
                    nd.hide = True
                    place(nd, colour_col_x(zone), colour_row_y("ov_edge"), hide=True)

    # Emission is authored from the pre-Hue wear colour in the native shader.
    yield "hue"
    emission_albedo = albedo
    albedo = _hue_variation(nodes, links, albedo, zone_scalars, zone_eq,
                           uv_bus["uv1"], tiling, hue_variation_path)
    yield "hue"

    if crease_r_socks and crease_fac is not None:
        rc = mux_by_zone(
            nodes, links, zone_idx, crease_r_socks, dtype="FLOAT", start=roughness,
            prefix="GT_mux_cRough", zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("c_rough"),
        )
        mix = _new_mix(nodes, "GT_wear_cRough", "rough ← crease TA", gx, colour_row_y("c_rough"), dtype="FLOAT")
        roughness = _link_mix(links, mix, crease_fac, roughness, rc)
    if edge_r_socks and edge_fac is not None:
        re_ = mux_by_zone(
            nodes, links, zone_idx, edge_r_socks, dtype="FLOAT", start=roughness,
            prefix="GT_mux_eRough", zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("e_rough"),
        )
        mix = _new_mix(nodes, "GT_wear_eRough", "rough ← edge TA", gx, colour_row_y("e_rough"), dtype="FLOAT")
        roughness = _link_mix(links, mix, edge_fac, roughness, re_)
    yield "wear_rough"

    if crease_fac is not None:
        cm_vals = {}
        for zone in range(1, 9):
            v = C._gt_param_float(mi_data, f"{zone}_CreaseMetallicity", 0.0)
            cm_vals[zone] = _value(
                nodes, f"GT_z{zone}_cMetal", f"{zone}_CreaseMetallicity", v,
                subcol_x(colour_col_x(zone)), colour_row_y("c_metal"),
            )
            if getattr(cm_vals[zone], "node", None) is not None:
                cm_vals[zone].node.hide = True
        if cm_vals:
            cmux = mux_by_zone(
                nodes, links, zone_idx, cm_vals, dtype="FLOAT", start=metallic,
                prefix="GT_mux_cMetal", zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("c_metal"),
            )
            mix = _new_mix(nodes, "GT_wear_cMetal", "metal ← crease", gx, shared_row_y("wear_metal"), dtype="FLOAT")
            metallic = _link_mix(links, mix, crease_fac, metallic, cmux)
    yield "wear_metal"
    if edge_fac is not None:
        em_vals = {}
        for zone in range(1, 9):
            v = C._gt_param_float(mi_data, f"{zone}_EdgeMetallicity", 0.0)
            em_vals[zone] = _value(
                nodes, f"GT_z{zone}_eMetal", f"{zone}_EdgeMetallicity", v,
                subcol_x(colour_col_x(zone)), colour_row_y("e_metal"),
            )
            if getattr(em_vals[zone], "node", None) is not None:
                em_vals[zone].node.hide = True
        if em_vals:
            emux = mux_by_zone(
                nodes, links, zone_idx, em_vals, dtype="FLOAT", start=metallic,
                prefix="GT_mux_eMetal", zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("e_metal"),
            )
            mix = _new_mix(nodes, "GT_wear_eMetal", "metal ← edge", gx + 280.0, shared_row_y("wear_metal"), dtype="FLOAT")
            metallic = _link_mix(links, mix, edge_fac, metallic, emux)

    yield "spec"
    specular = _layered_scalar_response(
        nodes, links, mi_data, zone_eq, crease_fac, edge_fac, "Specular", 0.5,
        x=gx, y=colour_row_y("metal") - 250.0,
    )
    yield "spec"

    # Extra normals: mux already chose Base N. Wear mixes Crease → Edge → Medium.
    # Shirt 003 U-turn reroutes on first mux A of each wear stage.
    if n_crease and crease_fac is not None:
        first_c = next((z for z in range(1, 9) if z in n_crease), None)
        nc = mux_by_zone(
            nodes, links, zone_idx, n_crease, dtype="VECTOR", start=detail,
            prefix="GT_mux_nCrease", zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("n_crease"),
        )
        if first_c is not None:
            _uturn_feed(
                nodes, links, f"GT_mux_nCrease_z{first_c}",
                "GT_RR_nCrease_R", "GT_RR_nCrease_L",
                gx - 200.0, colour_row_y("n_crease") + 300.0,
                colour_col_x(1) - 150.0, colour_row_y("n_crease") + 260.0,
            )
        mix = _new_mix(nodes, "GT_n_crease", "N Base→Crease", gx, colour_row_y("n_crease"), dtype="VECTOR")
        detail = _link_mix(links, mix, crease_fac, detail, nc)
    yield "wear_normals"
    if n_edge and edge_fac is not None:
        first_e = next((z for z in range(1, 9) if z in n_edge), None)
        ne = mux_by_zone(
            nodes, links, zone_idx, n_edge, dtype="VECTOR", start=detail,
            prefix="GT_mux_nEdge", zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("n_edge"),
        )
        if first_e is not None:
            _uturn_feed(
                nodes, links, f"GT_mux_nEdge_z{first_e}",
                "GT_RR_nEdge_R", "GT_RR_nEdge_L",
                gx + 100.0, colour_row_y("n_edge") + 300.0,
                colour_col_x(1) - 150.0, colour_row_y("n_edge") + 300.0,
            )
        mix = _new_mix(nodes, "GT_n_edge", "N →Edge", gx, colour_row_y("n_edge") + 150.0, dtype="VECTOR")
        detail = _link_mix(links, mix, edge_fac, detail, ne)
    yield "medium"
    from .layered_medium import iter_wire_medium
    detail = yield from iter_wire_medium(nodes, links, detail, n_medium, zone_eq, zone_scalars, mi_data,
        ocm_node.image if ocm_node is not None else None, uv_bus["uv0"])
    mesh_n = _decode_dx_color(
        nodes, links, _sock_color(normal_node), "GT_meshN_dx", gx, colour_row_y("mux_n"),
    ) if normal_node is not None else None
    if mesh_n is None:
        mesh_n = _tangent_up(nodes, "GT_meshN_flat", gx, colour_row_y("mux_n"))
    yield "mesh_n"
    detail = _quarter_turn_normal(
        nodes, links, detail, uv_bus["uv0"], "GT_n_qturn", gx, colour_row_y("mux_n"),
    )
    yield "mesh_n"
    mesh_n, detail = decal_comp.normals(sys.modules[__name__], nodes, links,
        mesh_n, detail, decal_slots)
    yield "decal_normals"
    rnm = _dr_ops.new_operation(nodes, 'ShaderNodeGroup', 'GT_n_rnm', stage=None, role=None)
    rnm.node_tree = ensure_gt_rnm_group()
    pass
    _dr_ops.set_label(rnm, 'RNM mesh + detail')
    place(rnm, gx, colour_row_y("mux_n"), lock=True)
    try:
        links.new(mesh_n, rnm.inputs["Base"])
        links.new(detail, rnm.inputs["Detail"])
        blended = rnm.outputs["Normal"]
    except Exception:
        blended = detail
    n_color = _encode_dx_for_nmap(nodes, links, blended, "GT_n_encode", gx + 280.0, colour_row_y("mux_n"))

    yield "rnm"
    # UV1 bus: one reroute per TA image row.
    _wire_array_uv_reroutes(nodes, links, uv_bus["uv1"], bus_x=maps_ocm_x() + 560.0)

    # Emission uses the authored layer colors before surface decals.
    yield "composer"
    from . import dr_layered_composer
    import sys
    emission = dr_layered_composer.build_emission(sys.modules[__name__],nodes,links,mi_data,colours,zone_eq,emission_albedo,crease_fac,edge_fac)

    # Decals have already been composed at the native colour/surface/normal stages.
    nodes.id_data["dr.layered_decal_contract"] = 1
    for sock in (zone_eq or {}).values():
        _collapse_broadcast(sock)
    if zone_broadcast is not None:
        _collapse_broadcast(zone_broadcast)

    last_decal_x = decal_x + 7.0 * _GT_GRID_DX
    bsdf_x = last_decal_x + _GT_BSDF_GAP
    bsdf_y = shared_row_y("wear_metal")
    mix_n_y = shared_row_y("n_med")
    if normal_node is not None:
        place(normal_node, bsdf_x - 2.0 * _GT_GRID_DX, mix_n_y, lock=True, hide=True)
    yield "terminal"
    from . import user_controls as _uc
    _uc_node, albedo, roughness, metallic = _uc.insert(
        nodes, links, albedo, roughness, metallic,
        wear=edge_fac, crease=crease_fac, location=(bsdf_x - 2.0 * _GT_GRID_DX, bsdf_y))
    yield "terminal"
    bsdf,nmap,out = dr_layered_composer.build_terminal(sys.modules[__name__],nodes,links,albedo=albedo,roughness=roughness,metallic=metallic,specular=specular,n_color=n_color,emission=emission,mi_data=mi_data,zone_eq=zone_eq)
    _wire_native_opacity_cutout(
        nodes, links, ocm_node, bsdf, masked=native_opacity_masked,
        clip=native_opacity_clip, x=bsdf_x - 1.5 * _GT_GRID_DX,
        y=bsdf_y - _GT_GRID_DY,
    )
    place(bsdf,bsdf_x,bsdf_y,lock=True)
    place(nmap,bsdf_x-_GT_GRID_DX,mix_n_y,lock=True)
    place(out,bsdf_x+_GT_OUTPUT_GAP,bsdf_y,lock=True)
    yield "terminal"
    note_y = shared_row_y("decal_rot") + _GT_GRID_DY
    add_gt_note(
        nodes, "NOTE_UV", "UV / ArrayUvScale",
        "UV0/UV1 use Revamped (u, 1-v) orientation. UV1 for TextureArray "
        "(Tiling x DetailTiling x stage tiling). Packed maps stay on UV0.",
        mx_uv, note_y,
    )
    add_gt_note(
        nodes, "NOTE_ZONE", "OCM zone index",
        "floor(mid×8+0.5) as Revamped Layer N (1–8). One GT_ZoneEq1 per "
        "Colour column (Match = N); Maximum only links once into those groups.",
        mx_ocm, note_y,
    )
    add_gt_note(
        nodes, "NOTE_ALBEDO", "Shared rows",
        "One Y ladder for Colour + Decal columns. Colour N / mux_col / decal albedo share the "
        "colour row; metal, rough, and normals are each one shared row across the graph.",
        colour_col_x(1), note_y,
    )
    add_gt_note(
        nodes, "NOTE_N", "Normals",
        "DX RG decode, wear-mix, UV0 quarter-turn, RNM onto mesh normal, then Normal Map.",
        bsdf_x - _GT_GRID_DX, note_y,
    )
    add_gt_note(
        nodes, "NOTE_DECAL", "Decals",
        "ArcDecalUv placement, mix by sticker alpha. CONSTANT 0.075 ColorA/B then ColorOverride. "
        "Column = Decal N (MI slot). Row = process stage; missing stages stay blank. "
        "Color/Value satellites sit left of the node they feed, 20px stacked.",
        decal_x, note_y,
    )
    add_gt_note(
        nodes, "NOTE_OUT", "Principled",
        "Cooked albedo / roughness / metallic / tangent normal. No ArcTexturer on this material.",
        bsdf_x, bsdf_y + _GT_GRID_DY,
    )
    yield "notes"
    n_orphans = prune_orphan_nodes(nodes)
    if n_orphans:
        print(f"Arc Raiders: GT pruned {n_orphans} unlinked node(s)")
    # A replay build (cw_replay) writes the flat graph into the real tree here, once.
    from . import cw_replay as _cw_replay
    nodes, links = _cw_replay.commit(nodes, links)
    # Do not run global overlap-nudge: locked decal/ZonePick grids become
    # obstacles and shove Principled/notes tens of thousands of units away.
    # Opening the Shader Editor then allocates a View2D for that huge AABB.
    yield "tail"
    for n in nodes:
        _quiet_node(n)
    _purge_unused_gt_datablocks()
    yield "tidy"
    if os.environ.get("DR_LOC_COUNT") == "1":
        flat = [0.0] * (2 * len(nodes))
        nodes.foreach_get("location", flat)
        nz = [n.name for i, n in enumerate(nodes) if flat[2 * i] or flat[2 * i + 1]]
        print(f"DR_LOC_COUNT nodes={len(nodes)} nonzero={len(nz)} sample={nz[:12]}", flush=True)
    _frame_gt_nodes(nodes)
    yield "tidy"
    _dr_ops.organize(nodes, links)
    yield "tidy"
    # Collapse repeated logic into shared block groups and lay the rest out left to right.
    # A failure here must never break the import: the column tidy pass stays as fallback.
    blocks = None
    tree = nodes.id_data
    bsdf_name = bsdf.name
    try:
        from . import graph_blocks as _graph_blocks
        blocks = yield from _graph_blocks.iter_collapse_and_arrange(tree)
    except Exception as exc:
        import traceback
        print(f"DR_BLOCKS_ERROR {type(exc).__name__}: {exc}")
        traceback.print_exc()
    if not blocks or blocks.get('skipped'):
        # The flat graph stays (failure or skip): write the deferred locations first.
        end_deferred_locations(tree, flush=True)
    if not blocks or blocks.get('skipped') in ('env', 'nowrap'):
        _graph_layout.tidy_tree(tree)
    if blocks and not blocks.get('skipped'):
        # The collapse rebuilt the tree: pending entries point at removed nodes.
        end_deferred_locations(tree, flush=False)
    # The collapse rebuilds the material tree around the wrapper node: the old node handle is dead.
    return tree.nodes.get(bsdf_name)


def finish_gt_outfit_graph(*args, **kwargs):
    """Sync wrapper: drain iter_finish_gt_outfit_graph and return its BSDF."""
    gen = iter_finish_gt_outfit_graph(*args, **kwargs)
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


@_dr_ops.operation_build
def _build_gt_decals(
    C, nodes, links, zone_idx, decals, decal_folder, search_dirs,
    origin_x, origin_y, *, ocm_node=None,
):
    from .. import utils as _utils

    dirs = list(search_dirs or [])
    df = decal_folder or ""
    if not df:
        try:
            df = _utils.get_decal_folder() or ""
        except Exception:
            df = ""
    if df and df not in dirs:
        dirs.insert(0, df)

    # Every slot is a fixed shell (same nodes for any colourway); write_decal_slot fills in the
    # MI's values and textures, so a colourway swap rewrites the slots and never adds nodes.
    from . import param_bind as _pb
    method = C.active_decal_method()
    by_idx = {}
    for decal in decals or []:
        try:
            by_idx.setdefault(int(decal.get("index")), decal)
        except (TypeError, ValueError):
            continue
    nodes.id_data["dr.decal_dirs"] = list(dirs)
    excl_mask = 0
    try:
        _tree = nodes.id_data
        _owner = getattr(_tree, "id_data", None) or _tree
        if hasattr(_owner, "get"):
            excl_mask = int(_owner.get("arc_decal_exclude_mask", 0) or 0) & 0xFF
    except Exception:
        excl_mask = 0
    from . import dr_layered_decals as decal_comp
    import sys
    decal_uv_node = _dr_ops.new_operation(nodes, 'ShaderNodeUVMap', 'GT_DecalUV', stage='Source', role='Decal.UV')
    decal_uv_node.uv_map = 'UV0' 
    _dr_ops.set_label(decal_uv_node, 'Decal UV')
    place(decal_uv_node, origin_x - _GT_GRID_DX, _decal_row_y(origin_y, "rot"), lock=True)
    aspect = 1.0
    if ocm_node is not None and ocm_node.image is not None:
        width, height = ocm_node.image.size
        if width: aspect = height / width
    decal_uv = decal_comp.native_uv(sys.modules[__name__], nodes, links,
        decal_uv_node.outputs['UV'], aspect)
    used = 0
    slots = []
    for idx in range(1, DECAL_SLOTS + 1):
        sat_x, main_x = _decal_slot_xs(origin_x, idx)

        def _at(stage, nd=None):
            y = _decal_row_y(origin_y, stage)
            if nd is not None:
                place(nd, main_x, y, lock=True)
                nd["dr.decal_slot"] = idx
                nd["dr.decal_role"] = stage
            return y

        mapping_node = _dr_ops.new_operation(nodes, 'ShaderNodeMapping', None, stage='Coordinates', role=f'Decal.Slot{idx}.Rotation')
        mapping_node.vector_type = "POINT"
        _dr_ops.set_label(mapping_node, f'Decal {idx} rot')
        _at("rot", mapping_node)
        links.new(decal_uv, mapping_node.inputs["Vector"])
        place_node = _dr_ops.new_operation(nodes, 'ShaderNodeMapping', None, stage='Coordinates', role=f'Decal.Slot{idx}.ScalePlacement')
        place_node.vector_type = "POINT"
        _dr_ops.set_label(place_node, f'Decal {idx} Scale/Loc')
        _at("place", place_node)
        links.new(mapping_node.outputs["Vector"], place_node.inputs["Vector"])
        # Identity unless the slot's mapping flips V (write_decal_slot sets it).
        flip_node = _dr_ops.new_operation(nodes, 'ShaderNodeMapping', None, stage='Coordinates', role=f'Decal.Slot{idx}.ImageVFlip')
        flip_node.vector_type = "POINT"
        _dr_ops.set_label(flip_node, f'Decal {idx} V Flip')
        _at("flip", flip_node)
        links.new(place_node.outputs["Vector"], flip_node.inputs["Vector"])
        placed_uv = flip_node.outputs["Vector"]

        color_tex = _dr_ops.new_operation(nodes, 'ShaderNodeTexImage', None, stage='LayerInputs', role=f'Decal.Slot{idx}.ColorTexture')
        color_tex.interpolation = "Cubic"
        _at("color", color_tex)
        links.new(placed_uv, color_tex.inputs["Vector"])
        data_tex = _dr_ops.new_operation(nodes, 'ShaderNodeTexImage', None, stage='LayerInputs', role=f'Decal.Slot{idx}.PackedSurfaceTexture')
        data_tex.interpolation = "Cubic"
        _at("data", data_tex)
        links.new(placed_uv, data_tex.inputs["Vector"])

        # Bound on/off: UseDecal 0 zeroes the slot's alpha.
        use_m = _math(nodes, "MULTIPLY", f"GT_decal{idx}_use", sat_x, _decal_row_y(origin_y, "layer"), hide=True)
        use = _pb.bind(nodes, f"{idx}_UseDecal", 0.0)
        tiling = _pb.bind(nodes, f"{idx}_Tiling", 0.0)
        coverage, normal_domain = decal_comp.slot_coverage(sys.modules[__name__], nodes, links,
            placed_uv, tiling, color_tex.outputs['Alpha'], use, idx)
        links.new(coverage, use_m.inputs[0])
        links.new(use, use_m.inputs[1])
        _at("use", use_m)
        alpha = use_m.outputs[0]
        if zone_idx is not None:
            lg = None
            try:
                lg = ensure_gt_decal_layer_edit_group()   # Allow1..8 / Excl1..8: rewritable per colourway
            except Exception:
                lg = None
            if lg is not None:
                gate_n = _dr_ops.new_operation(nodes, 'ShaderNodeGroup', f'GT_decal{idx}_layer', stage='LayerInputs', role=f'Decal.Slot{idx}.LayerGate')
                gate_n.node_tree = lg
                gate_n["arc_decal_idx"] = int(idx)
                gate_n["arc_decal_exclude_mask"] = excl_mask
                _dr_ops.set_label(gate_n, f'decal {idx} LayerMask')
                _at("layer", gate_n)
                try:
                    links.new(zone_idx, gate_n.inputs["ZoneIndex"])
                    links.new(alpha, gate_n.inputs["Alpha"])
                    alpha = gate_n.outputs["Masked"]
                    _collapse_broadcast(gate_n.inputs["ZoneIndex"])
                except Exception:
                    pass

        if zone_idx is not None and lg is not None:
            domain_gate = _dr_ops.new_operation(nodes, 'ShaderNodeGroup', stage='Coverage', role=f'Decal.Slot{idx}.NormalLayerGate')
            domain_gate.node_tree = lg
            domain_gate['arc_decal_idx'] = idx
            domain_gate['arc_decal_exclude_mask'] = excl_mask
            _at('normal_layer', domain_gate)
            links.new(zone_idx, domain_gate.inputs['ZoneIndex'])
            links.new(normal_domain, domain_gate.inputs['Alpha'])
            normal_domain = domain_gate.outputs['Masked']

        # M_Character_Layered lerps the sampled artwork to the A/B pick by ColorOverride; 0 (the master default)
        # keeps the artwork (Driftcoat's T_Patch_02_Color_C chest patch).
        decal_rgb = _sock_color(color_tex)
        tint_tree = None
        try:
            tint_tree = ensure_gt_decal_tint_group()
        except Exception:
            tint_tree = None
        if tint_tree is not None:
            tint = _dr_ops.new_operation(nodes, 'ShaderNodeGroup', f'GT_decal{idx}_tint', stage='LayerInputs', role=f'Decal.Slot{idx}.ColorTint')
            tint.node_tree = tint_tree
            _dr_ops.set_label(tint, f'decal {idx} A/B + ColorOverride')
            _at("tint", tint)
            links.new(decal_rgb, tint.inputs["DecalColor"])
            links.new(_pb.bind(nodes, f"{idx}_ColorA", (1.0, 1.0, 1.0, 1.0)), tint.inputs["ColorA"])
            links.new(_pb.bind(nodes, f"{idx}_ColorB", (1.0, 1.0, 1.0, 1.0)), tint.inputs["ColorB"])
            links.new(_pb.bind(nodes, f"{idx}_ColorOverride", 0.0), tint.inputs["ColorOverride"])
            decal_rgb = tint.outputs["Color"] if "Color" in tint.outputs else tint.outputs[0]

        d_rough = d_metal = d_n = None
        # Native packed data: RG tangent XY, B roughness, A metallic.
        from . import dr_packed_decal as _packed_decal
        import bpy
        dd_tree = _packed_decal.build_group(bpy)
        if dd_tree is not None:
            dd = _dr_ops.new_operation(nodes, 'ShaderNodeGroup', f'GT_decal{idx}_dataG', stage='LayerInputs', role=f'Decal.Slot{idx}.PackedSurface')
            dd.node_tree = dd_tree
            _dr_ops.set_label(dd, f'decal {idx} Decal Data')
            _at("unpack", dd)
            for iname in ("Color", "DecalData", "Decal Data"):
                if iname in dd.inputs:
                    links.new(_sock_color(data_tex), dd.inputs[iname])
                    break
            links.new(data_tex.outputs['Alpha'], dd.inputs['Alpha'])
            if "Mask" in dd.inputs:
                links.new(alpha, dd.inputs["Mask"])
            d_n = dd.outputs["Normal"] if "Normal" in dd.outputs else None
            d_rough = dd.outputs["Roughness"] if "Roughness" in dd.outputs else None
            d_metal = dd.outputs["Metallic"] if "Metallic" in dd.outputs else None
        if d_n is None:
            dsep = _dr_ops.new_operation(nodes, 'ShaderNodeSeparateColor', f'GT_decal{idx}_data', stage='LayerInputs', role=f'Decal.Slot{idx}.PackedChannels')
            _at("unpack", dsep)
            links.new(_sock_color(data_tex), dsep.inputs[0])
            comb_n = _dr_ops.new_operation(nodes, 'ShaderNodeCombineColor', f'GT_decal{idx}_nxy', stage='LayerInputs', role=f'Decal.Slot{idx}.NormalXY')
            _w, h = node_footprint(dsep)
            place(comb_n, main_x, _decal_row_y(origin_y, "unpack") - h - _INPUT_STACK_GAP, lock=True)
            links.new(dsep.outputs[0], comb_n.inputs[0])
            links.new(dsep.outputs[1], comb_n.inputs[1])
            comb_n.inputs[2].default_value = 1.0
            d_n = comb_n.outputs[0]
            d_rough = dsep.outputs[2]
            d_metal = data_tex.outputs["Alpha"]
        if d_rough is not None:
            # A BC5 data texture samples blue as 0 in the game; its PNG holds a rebuilt normal Z there.
            blue = _math(nodes, "MULTIPLY", f"GT_decal{idx}_dataB", main_x, _decal_row_y(origin_y, "unpack"), hide=True)
            links.new(d_rough, blue.inputs[0])
            links.new(_pb.bind(nodes, f"{idx}_DataBlue", 1.0), blue.inputs[1])
            d_rough = blue.outputs[0]
        controls ={key: _pb.bind(nodes, f"{idx}_{native}", 0.0) for key, native in (
            ('surface', 'SurfaceOverride'), ('wear', 'WearOverride'),
            ('base_normal', 'BaseNormalOverride'), ('detail_normal', 'DetailNormalOverride'))}
        rotation = _pb.bind(nodes, f"{idx}_DecalPlacement.A", 0.0)
        normal_rotation = decal_comp._math(sys.modules[__name__], nodes, links,
            f'Slot{idx}.NormalPlacementInverse', 'MULTIPLY', rotation, -6.283185307179586)
        slots.append(dict(index=idx, use=use, alpha=alpha, domain=normal_domain,
            color=decal_rgb, decoder=dd, roughness=d_rough, metallic=d_metal,
            normal_rotation=normal_rotation, **controls))

        if write_decal_slot(C, nodes, idx, by_idx.get(idx), df, dirs, method):
            used += 1

    print(f"Arc Raiders: GT decals parsed={len(by_idx)} on={used} slots={DECAL_SLOTS}")
    return slots


DECAL_SLOTS = 8


def decal_slot_values(C, decal, df, dirs, method):
    """Everything one decal slot takes from an MI (None or an unused slot: off)."""
    off = {"enabled": False}
    if not decal or not C._decal_slot_is_used(decal):
        return off
    tex_stem = (decal.get("texture") or "").strip()
    tex_fpath = C.resolve_decal_texture(tex_stem, decal.get("texture_path", ""), df, search_dirs=dirs)
    if not tex_fpath:
        return off
    data_stem = (decal.get("data_texture") or "").strip()
    data_fpath = C.resolve_decal_texture(data_stem, decal.get("data_texture_path", ""), df, search_dirs=dirs) if data_stem else ""
    try:
        width_ratio = float(decal.get("width_ratio"))
    except (TypeError, ValueError):
        width_ratio = 0.0
    if width_ratio <= 0.0:
        width_ratio = C._width_ratio_from_path(tex_fpath)
        if width_ratio <= 0.0:
            probe = _load_image_cached(tex_fpath)
            if probe is not None and probe.size[1] > 0:
                width_ratio = float(probe.size[0]) / float(probe.size[1])
                _WIDTH_RATIO_BY_PATH[_norm_path_key(tex_fpath)] = width_ratio
    if width_ratio <= 0.0:
        width_ratio = 1.0
    # Engine placement for every stem (sx = 1/scale, sy = widthRatio/scale). The old ducttape
    # WIDTH_2X boost doubled tape length and pushed pants tape across the leg (Goalie, 2.3.12).
    slot_method = method
    layer_mask_int = C._decal_layer_mask_int(decal.get("layer_mask", 255.0))
    ca = _decal_rgb3(decal.get("color_a"))
    cb = _decal_rgb3(decal.get("color_b"))
    override = max(0.0, min(1.0, float(decal.get("color_override", 0.0))))
    if ca is None or cb is None:   # one authored colour: tint toward it alone (or keep the artwork)
        ca = cb = ca or cb or (1.0, 1.0, 1.0)
        if ca == (1.0, 1.0, 1.0):
            override = 0.0
    return {
        "enabled": True,
        "texture": tex_fpath,
        "data": data_fpath,
        "data_blue": 0.0 if data_fpath and textures.two_channel_sidecar(data_fpath) else 1.0,
        # Native sampler wraps; explicit domain/tiling controls coverage.
        "extension": "REPEAT",
        **{key: float(decal.get(key, 0.0)) for key in (
            "surface_override", "wear_override", "base_normal_override",
            "detail_normal_override", "tiling")},
        "rotation": float(decal.get("rotation", 0.0)),
        "params": C._decal_mapping_params(decal["uv_u"], decal["uv_v"], decal["scale"], decal["rotation"],
                                          width_ratio, method=slot_method),
        "allow": C._decal_visibility_layer_mask(layer_mask_int),
        "color_a": (*ca, 1.0),
        "color_b": (*cb, 1.0),
        "override": override,
        "label": tex_stem,
    }


def write_decal_slot(C, nodes, idx, decal, df, dirs, method) -> bool:
    """Write one shell slot's values and textures from an MI decal entry; returns whether it is on."""
    from . import param_bind as _pb
    vals = decal_slot_values(C, decal, df, dirs, method)
    role = {}
    for nd in nodes:
        if nd.get("dr.decal_slot") == idx:
            role[nd.get("dr.decal_role")] = nd
    on = bool(vals.get("enabled"))
    _pb.bind(nodes, f"{idx}_UseDecal", 1.0 if on else 0.0)
    if not on:   # keep placement and images: an off slot only needs alpha 0
        for stage, group in (("color", "decal"), ("data", "decal_data")):
            nd = role.get(stage)
            if nd is not None and nd.image is None:
                nd.image = _neutral_image(group)
        return False
    p = vals["params"]
    for stage, loc, rot, scale in (("rot", p["rot_location"], p["rot_rotation"], p["rot_scale"]),
                                   ("place", p["place_location"], p["place_rotation"], p["place_scale"])):
        nd = role.get(stage)
        if nd is not None:
            nd.inputs["Location"].default_value = loc
            nd.inputs["Rotation"].default_value = (0.0, 0.0, rot)
            nd.inputs["Scale"].default_value = scale
    flip = role.get("flip")
    if flip is not None:
        flip.inputs["Location"].default_value = (0.0, 1.0, 0.0) if p["flip_v"] else (0.0, 0.0, 0.0)
        flip.inputs["Scale"].default_value = (1.0, -1.0, 1.0) if p["flip_v"] else (1.0, 1.0, 1.0)
    for stage, path, non_color, group in (("color", vals["texture"], False, "decal"), ("data", vals["data"], True, "decal_data")):
        nd = role.get(stage)
        if nd is None:
            continue
        img = _load_image_cached(path, non_color=non_color, alpha_mode="CHANNEL_PACKED") if path else None
        if img is not None and non_color:
            try:
                img.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
        nd.image = img if img is not None else _neutral_image(group)
        nd.extension = vals["extension"]
    color = role.get("color")
    if color is not None:
        _dr_ops.set_label(color, f'Decal {idx}: {vals["label"]}')
    for stage in ('layer', 'normal_layer'):
        gate = role.get(stage)
        if gate is not None:
            configure_gt_decal_layer_edit(gate, vals["allow"], int(gate.get("arc_decal_exclude_mask", 0) or 0))
    for key, native in (
        ('surface_override', 'SurfaceOverride'), ('wear_override', 'WearOverride'),
        ('base_normal_override', 'BaseNormalOverride'), ('detail_normal_override', 'DetailNormalOverride'),
        ('tiling', 'Tiling'), ('rotation', 'DecalPlacement.A')):
        _pb.bind(nodes, f"{idx}_{native}", vals[key])
    _pb.bind(nodes, f"{idx}_DataBlue", vals["data_blue"])
    _pb.bind(nodes, f"{idx}_ColorA", vals["color_a"])
    _pb.bind(nodes, f"{idx}_ColorB", vals["color_b"])
    _pb.bind(nodes, f"{idx}_ColorOverride", vals["override"])
    return True


def _dr_stage_composer(kind):
    from . import dr_layered_composer
    import sys
    return dr_layered_composer.build_stage_group(sys.modules[__name__],kind)
