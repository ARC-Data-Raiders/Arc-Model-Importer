"""Ground-truth outfit PBR graph (no ArcTexturer).

Cooked Colour N is assembled in ``clothing._wire_outfit_color_ground_truth``.
This module muxes zones with ``floor(mid*8+0.5)`` and applies roughness,
metallic, extra normals, crease/edge overlays, occlusion, and ArcDecalUv decals
into Principled BSDF.
"""
from __future__ import annotations

import os

from .. import textures
from .common import (
    _WIDTH_RATIO_BY_PATH,
    _load_image_cached,
    _norm_path_key,
    _set_node_world_loc,
    resolve_node_overlaps,
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
# Snooper / ArcTexturer TA UVs: uArc.ArrayUvScale (ArcMaterial.cs).
_GT_ARRAY_UV_SCALE = 25.0

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
    x, y = float(n.location.x), float(n.location.y)
    p = getattr(n, "parent", None)
    while p is not None:
        x += float(p.location.x)
        y += float(p.location.y)
        p = getattr(p, "parent", None)
    return x, y


def unparent_keep(n) -> None:
    if n is None or getattr(n, "parent", None) is None:
        return
    x, y = world_loc(n)
    try:
        n.parent = None
    except Exception:
        return
    n.location = (x, y)


def place(nd, x, y, *, hide=None, lock=False, parent=None):
    if nd is None:
        return None
    if parent is None:
        unparent_keep(nd)
    if hide is not None:
        nd.hide = hide
    if parent is not None:
        try:
            nd.parent = parent
        except Exception:
            pass
        _set_node_world_loc(nd, float(x), float(y))
    else:
        nd.location = (float(x), float(y))
    _quiet_node(nd)
    if lock:
        lock_layout(nd)
    return nd


def _reroute(nodes, links, sock, name, x, y):
    """Layout-only bus hop (Shirt 003 cleanup). Returns the reroute Output socket."""
    existing = nodes.get(name)
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
        rr = nodes.new("NodeReroute")
        rr.name = name
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
        name = getattr(n, "name", "") or ""
        lab = getattr(n, "label", "") or ""
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
            if n.bl_idname == "ShaderNodeTexImage" and vin.is_linked:
                fr = vin.links[0].from_node
                # Never rewire the root ArrayUv Mapping — that creates a feedback
                # loop (Mapping → RR → Mapping.Vector) and blacks out the mesh.
                if (
                    fr is not None
                    and fr.bl_idname == "ShaderNodeMapping"
                    and (fr.name or "") != "GT_TA_ArrayUv"
                    and not (fr.name or "").startswith("GT_TA_ArrayUv")
                ):
                    mv = None
                    try:
                        mv = fr.inputs.get("Vector")
                    except Exception:
                        mv = fr.inputs[0] if fr.inputs else None
                    if mv is not None:
                        while mv.is_linked:
                            try:
                                links.remove(mv.links[0])
                            except Exception:
                                break
                        try:
                            links.new(rr_out, mv)
                        except Exception:
                            pass
                    continue
                if fr is not None and (fr.name or "") == "GT_TA_ArrayUv":
                    # Fall through: replace direct Mapping→TA with bus hop.
                    pass
            while vin.is_linked:
                try:
                    links.remove(vin.links[0])
                except Exception:
                    break
            try:
                links.new(rr_out, vin)
            except Exception:
                pass
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
    mix = nodes.get(mix_name)
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


def make_tex(nodes, fpath, name, loc, *, non_color=False, hide=True):
    img = _load_image_cached(fpath)
    if img is None:
        return None
    if non_color:
        try:
            img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
    nd = nodes.new("ShaderNodeTexImage")
    nd.name = name
    nd.label = os.path.basename(fpath)
    nd.image = img
    nd.interpolation = "Cubic"
    nd.hide = bool(hide)
    nd.location = loc
    _quiet_node(nd)
    return nd


def _math(nodes, op, name, x, y, *, value=None, hide=True):
    nd = nodes.new("ShaderNodeMath")
    nd.operation = op
    nd.name = name
    nd.hide = hide
    if value is not None:
        try:
            nd.inputs[1].default_value = float(value)
        except Exception:
            pass
    place(nd, x, y, hide=hide)
    return nd


def _new_mix(nodes, name, label, x, y, dtype="RGBA", *, hide=False):
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = dtype
    mix.blend_type = "MIX"
    try:
        mix.clamp_factor = True
    except Exception:
        pass
    mix.name = name
    mix.label = label
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

    ng = bpy.data.node_groups.get(name)
    if ng is not None and ng.get("arc_gt_ver") == ver:
        return ng, False
    if ng is None:
        ng = bpy.data.node_groups.new(name, "ShaderNodeTree")
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
        if n.startswith("GT_") or "_stale" in n or n.startswith("GT_ZonePick"):
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


def ensure_gt_zone_eq1_group():
    """Single-zone COMPARE: Eq = (ZoneIndex == Match).

    One instance per Colour column (Match = zone-1). Replaces the fat GT_ZoneEq
    that emitted Eq0..Eq7 from one node.
    """
    name = "GT_ZoneEq1"
    ng, created = _gt_ng_fresh(name, 1)
    if not created:
        return ng
    _gt_ng_sock(ng, "ZoneIndex", "INPUT", "NodeSocketFloat", 0.0)
    _gt_ng_sock(ng, "Match", "INPUT", "NodeSocketFloat", 0.0)
    _gt_ng_sock(ng, "Eq", "OUTPUT", "NodeSocketFloat")
    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-280.0, 0.0)
    go = ng.nodes.new("NodeGroupOutput")
    go.location = (280.0, 0.0)
    cmp = ng.nodes.new("ShaderNodeMath")
    cmp.operation = "COMPARE"
    cmp.name = "eq"
    cmp.label = "ZoneIndex == Match"
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
    return ng


def ensure_gt_zone_eq_group():
    """Legacy 8-way Eq0..Eq7 hub. Prefer :func:`ensure_gt_zone_eq1_group` per column."""
    name = "GT_ZoneEq"
    ng, created = _gt_ng_fresh(name, 2)
    if not created:
        return ng
    _gt_ng_sock(ng, "ZoneIndex", "INPUT", "NodeSocketFloat", 0.0)
    _gt_ng_sock(ng, "Index", "OUTPUT", "NodeSocketFloat")
    for z0 in range(8):
        _gt_ng_sock(ng, f"Eq{z0}", "OUTPUT", "NodeSocketFloat")
    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-280.0, 0.0)
    go = ng.nodes.new("NodeGroupOutput")
    go.location = (280.0, 0.0)
    links = ng.links
    try:
        links.new(gi.outputs["ZoneIndex"], go.inputs["Index"])
    except Exception:
        pass
    for z0 in range(8):
        cmp = ng.nodes.new("ShaderNodeMath")
        cmp.operation = "COMPARE"
        cmp.name = f"eq_z{z0}"
        cmp.label = f"== {z0}"
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
    return ng


def _wire_zone_eq(nodes, links, zone_idx, x, y):
    """One GT_ZoneEq1 at the top of each Colour column. Returns zone→Eq socket."""
    eqs = {}
    if zone_idx is None:
        return None, eqs
    ng = ensure_gt_zone_eq1_group()
    first = None
    for zone in range(1, 9):
        nd = nodes.new("ShaderNodeGroup")
        nd.node_tree = ng
        nd.name = f"GT_ZoneEq_z{zone}"
        nd.label = f"zone == {zone - 1}"
        place(nd, colour_col_x(zone), shared_row_y("zone_eq"), lock=True)
        try:
            links.new(zone_idx, nd.inputs["ZoneIndex"])
            nd.inputs["Match"].default_value = float(zone - 1)
        except Exception:
            if nd.inputs:
                links.new(zone_idx, nd.inputs[0])
                if len(nd.inputs) > 1:
                    nd.inputs[1].default_value = float(zone - 1)
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


def ensure_gt_albedo_occ_group():
    """Engine ``albedo *= OCM.R``. Mix Color B is RGB, so R is broadcast inside this group."""
    import bpy

    name = "GT_AlbedoXOcc"
    ng, created = _gt_ng_fresh(name, 1)
    if not created:
        return ng
    _gt_ng_sock(ng, "Albedo", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "Occ", "INPUT", "NodeSocketFloat", 1.0)
    _gt_ng_sock(ng, "Result", "OUTPUT", "NodeSocketColor")
    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-360.0, 0.0)
    go = ng.nodes.new("NodeGroupOutput")
    go.location = (280.0, 0.0)
    comb = ng.nodes.new("ShaderNodeCombineColor")
    comb.label = "Occ as RGB"
    comb.location = (-160.0, -80.0)
    links = ng.links
    links.new(gi.outputs["Occ"], comb.inputs[0])
    links.new(gi.outputs["Occ"], comb.inputs[1])
    links.new(gi.outputs["Occ"], comb.inputs[2])
    mix = ng.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.location = (40.0, 40.0)
    try:
        mix.clamp_factor = True
    except Exception:
        pass
    try:
        mix_io(mix)[0].default_value = 1.0
    except Exception:
        pass
    outc = _link_mix(links, mix, None, gi.outputs["Albedo"], comb.outputs[0])
    try:
        links.new(outc, go.inputs["Result"])
    except Exception:
        links.new(outc, go.inputs[0])
    return ng


def ensure_gt_decal_layer_group(mask_int: int):
    """Legacy baked-bit tree. Prefer :func:`ensure_gt_decal_layer_edit_group`."""
    bits = int(mask_int) & 0xFF
    name = f"GT_DecalLayer_{bits}"
    ng, created = _gt_ng_fresh(name, 1)
    if not created:
        return ng
    _gt_ng_sock(ng, "ZoneIndex", "INPUT", "NodeSocketFloat", 0.0)
    _gt_ng_sock(ng, "Alpha", "INPUT", "NodeSocketFloat", 1.0)
    _gt_ng_sock(ng, "Masked", "OUTPUT", "NodeSocketFloat")
    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-640.0, 0.0)
    go = ng.nodes.new("NodeGroupOutput")
    go.location = (520.0, 0.0)
    links = ng.links
    gate = None
    x = -360.0
    for z0 in range(8):
        if (bits & (1 << z0)) == 0:
            continue
        cmp = ng.nodes.new("ShaderNodeMath")
        cmp.operation = "COMPARE"
        cmp.label = f"z{z0}"
        cmp.location = (x, 80.0)
        links.new(gi.outputs["ZoneIndex"], cmp.inputs[0])
        try:
            cmp.inputs[1].default_value = float(z0)
            cmp.inputs[2].default_value = 0.45
        except Exception:
            pass
        if gate is None:
            gate = cmp.outputs[0]
        else:
            mx = ng.nodes.new("ShaderNodeMath")
            mx.operation = "MAXIMUM"
            mx.location = (x, -40.0)
            links.new(gate, mx.inputs[0])
            links.new(cmp.outputs[0], mx.inputs[1])
            gate = mx.outputs[0]
        x += 160.0
    mul = ng.nodes.new("ShaderNodeMath")
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
    return ng


def ensure_gt_decal_layer_edit_group():
    """Editable LayerMask gate: cooked Allow1..8 × optional Excl1..8 checkboxes.

    Zone index is 0-based (OCM mid×8). Colour N in the UI is 1-based (Allow1/Excl1
    = Colour 1 = zone 0). ``Masked = Alpha × OR_z(match_z × Allow × (1−Excl))``.
    """
    name = "GT_DecalLayerEdit"
    ng, created = _gt_ng_fresh(name, 1)
    if not created:
        return ng
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
            excl_item.description = f"Exclude decal from Colour {z} (OCM zone {z - 1})"
        except Exception:
            pass
    _gt_ng_sock(ng, "Masked", "OUTPUT", "NodeSocketFloat")
    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-900.0, 0.0)
    go = ng.nodes.new("NodeGroupOutput")
    go.location = (720.0, 0.0)
    links = ng.links
    gate = None
    x = -560.0
    for z0 in range(8):
        z1 = z0 + 1
        y = 280.0 - z0 * 70.0
        cmp = ng.nodes.new("ShaderNodeMath")
        cmp.operation = "COMPARE"
        cmp.label = f"Colour {z1}"
        cmp.location = (x, y)
        links.new(gi.outputs["ZoneIndex"], cmp.inputs[0])
        try:
            cmp.inputs[1].default_value = float(z0)
            cmp.inputs[2].default_value = 0.45
        except Exception:
            pass
        inv = ng.nodes.new("ShaderNodeMath")
        inv.operation = "SUBTRACT"
        inv.label = f"1−Excl{z1}"
        inv.location = (x + 160.0, y - 20.0)
        try:
            inv.inputs[0].default_value = 1.0
        except Exception:
            pass
        links.new(gi.outputs[f"Excl{z1}"], inv.inputs[1])
        mul_a = ng.nodes.new("ShaderNodeMath")
        mul_a.operation = "MULTIPLY"
        mul_a.label = f"×Allow{z1}"
        mul_a.location = (x + 320.0, y)
        links.new(cmp.outputs[0], mul_a.inputs[0])
        links.new(gi.outputs[f"Allow{z1}"], mul_a.inputs[1])
        mul_e = ng.nodes.new("ShaderNodeMath")
        mul_e.operation = "MULTIPLY"
        mul_e.label = f"gate{z1}"
        mul_e.location = (x + 480.0, y)
        links.new(mul_a.outputs[0], mul_e.inputs[0])
        links.new(inv.outputs[0], mul_e.inputs[1])
        if gate is None:
            gate = mul_e.outputs[0]
        else:
            mx = ng.nodes.new("ShaderNodeMath")
            mx.operation = "MAXIMUM"
            mx.location = (x + 640.0, y)
            links.new(gate, mx.inputs[0])
            links.new(mul_e.outputs[0], mx.inputs[1])
            gate = mx.outputs[0]
    mul = ng.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    mul.label = "× Alpha"
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
    return ng


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
        name = getattr(n, "name", "") or ""
        lab = getattr(n, "label", "") or ""
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
                if getattr(n.node_tree, "name", "") != "GT_DecalLayerEdit":
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


def ensure_gt_colour_n_group():
    """One Colour-N assemble: ColorMask×Swatch×amt, fold-from-white A→B→C, ×Overlay.

    Engine math: ``default.frag`` ArcZoneColor (no Blender UV conversion).
    """
    import bpy

    name = "GT_ColourN"
    ng = bpy.data.node_groups.get(name)
    if ng is not None and ng.get("arc_gt_ver") == 2:
        return ng
    if ng is not None:
        try:
            ng.name = f"{name}_stale"
        except Exception:
            pass
    ng = bpy.data.node_groups.new(name, "ShaderNodeTree")
    ng["arc_gt_ver"] = 2
    _gt_ng_sock(ng, "ColorMask", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "Swatch", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "Amt", "INPUT", "NodeSocketFloat", 1.0)
    _gt_ng_sock(ng, "ColorA", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "ColorB", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "ColorC", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "Overlay", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "Colour", "OUTPUT", "NodeSocketColor")
    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-900.0, 0.0)
    go = ng.nodes.new("NodeGroupOutput")
    go.location = (900.0, 0.0)
    links = ng.links

    def gmix(blend, loc, dtype="RGBA"):
        m = ng.nodes.new("ShaderNodeMix")
        m.data_type = dtype
        m.blend_type = blend
        try:
            m.clamp_factor = True
        except Exception:
            pass
        m.location = loc
        return m

    mul_sw = gmix("MULTIPLY", (-620.0, 80.0))
    _link_mix(links, mul_sw, None, gi.outputs["ColorMask"], gi.outputs["Swatch"])
    try:
        mix_io(mul_sw)[0].default_value = 1.0
    except Exception:
        pass
    amt3 = ng.nodes.new("ShaderNodeCombineColor")
    amt3.location = (-620.0, -160.0)
    links.new(gi.outputs["Amt"], amt3.inputs[0])
    links.new(gi.outputs["Amt"], amt3.inputs[1])
    links.new(gi.outputs["Amt"], amt3.inputs[2])
    mul_amt = gmix("MULTIPLY", (-400.0, 40.0))
    w = _link_mix(links, mul_amt, None, mix_io(mul_sw)[3], amt3.outputs[0])
    try:
        mix_io(mul_amt)[0].default_value = 1.0
    except Exception:
        pass
    sep = ng.nodes.new("ShaderNodeSeparateColor")
    sep.location = (-180.0, 40.0)
    links.new(w, sep.inputs[0])
    white = ng.nodes.new("ShaderNodeRGB")
    white.outputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
    white.location = (-400.0, 260.0)
    mix_a = gmix("MIX", (40.0, 160.0))
    _link_mix(links, mix_a, sep.outputs[0], white.outputs[0], gi.outputs["ColorA"])
    mix_b = gmix("MIX", (260.0, 80.0))
    _link_mix(links, mix_b, sep.outputs[1], mix_io(mix_a)[3], gi.outputs["ColorB"])
    mix_c = gmix("MIX", (480.0, 0.0))
    folded = _link_mix(links, mix_c, sep.outputs[2], mix_io(mix_b)[3], gi.outputs["ColorC"])
    mul_ov = gmix("MULTIPLY", (700.0, 0.0))
    outc = _link_mix(links, mul_ov, None, folded, gi.outputs["Overlay"])
    try:
        mix_io(mul_ov)[0].default_value = 1.0
    except Exception:
        pass
    links.new(outc, go.inputs["Colour"])
    return ng


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


def add_gt_note(nodes, name: str, title: str, body: str, x: float, y: float):
    """Frame label only. Do not assign Frame.text — Shader Editor rasterizes it every redraw."""

    fr = nodes.new("NodeFrame")
    fr.name = name
    fr.label = title
    try:
        fr.label_size = 16
    except Exception:
        pass
    fr.location = (float(x), float(y))
    extra = (body or "").replace("\n", " — ")
    fr.label = f"{title} — {extra[:140]}" if extra else title
    return fr


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
    secondary: bool,
):
    """Node group whose internals are only this Colour N chain (baked colorway)."""
    import bpy

    key = "".join(ch for ch in str(tree_key or "mat") if ch.isalnum() or ch in "_-")[:28] or "mat"
    name = f"GT_Colour{int(zone)}_{key}"
    ng = bpy.data.node_groups.get(name)
    if ng is None:
        ng = bpy.data.node_groups.new(name, "ShaderNodeTree")
    else:
        _reset_ng(ng)
    ng["arc_gt_ver"] = 3
    ng["arc_gt_zone"] = int(zone)
    _gt_ng_sock(ng, "ColorMask", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "Colour", "OUTPUT", "NodeSocketColor")
    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-1100.0, 40.0)
    go = ng.nodes.new("NodeGroupOutput")
    go.location = (1100.0, 40.0)
    links = ng.links
    scheme = "ColorA2/B2/C2 (secondary)" if secondary else "ColorA/B/C (primary)"
    add_gt_note(
        ng.nodes,
        "NOTE_weights",
        f"Colour {zone} — weights",
        "ColorMask.rgb × ColorMaskSwatch × Amt (BaseColorMaskStrength). "
        "These are wA/wB/wC for the fold. Engine: ArcZoneColor, not BaseColor.r.",
        -1100.0,
        420.0,
    )
    add_gt_note(
        ng.nodes,
        "NOTE_fold",
        f"Colour {zone} — fold from white",
        f"lerp(white, {scheme}) R then G then B. Tab into this group to read one zone only.",
        40.0,
        420.0,
    )
    add_gt_note(
        ng.nodes,
        "NOTE_ov",
        f"Colour {zone} — overlay",
        "Multiply BaseColorOverlay. Pattern / hue / wet stay outside this group.",
        780.0,
        420.0,
    )

    def gmix(blend, loc, dtype="RGBA"):
        m = ng.nodes.new("ShaderNodeMix")
        m.data_type = dtype
        m.blend_type = blend
        try:
            m.clamp_factor = True
        except Exception:
            pass
        m.location = loc
        m.hide = False
        return m

    sw = ng.nodes.new("ShaderNodeRGB")
    sw.label = f"{zone}_ColorMaskSwatch"
    sw.outputs[0].default_value = _as_rgba(swatch)
    sw.location = (-820.0, -40.0)
    amt_nd = ng.nodes.new("ShaderNodeValue")
    amt_nd.label = f"Amt={float(amt):.3f}"
    amt_nd.outputs[0].default_value = float(amt)
    amt_nd.location = (-820.0, -280.0)
    mul_sw = gmix("MULTIPLY", (-560.0, 80.0))
    _link_mix(links, mul_sw, None, gi.outputs["ColorMask"], sw.outputs[0])
    try:
        mix_io(mul_sw)[0].default_value = 1.0
    except Exception:
        pass
    amt3 = ng.nodes.new("ShaderNodeCombineColor")
    amt3.location = (-560.0, -200.0)
    links.new(amt_nd.outputs[0], amt3.inputs[0])
    links.new(amt_nd.outputs[0], amt3.inputs[1])
    links.new(amt_nd.outputs[0], amt3.inputs[2])
    mul_amt = gmix("MULTIPLY", (-320.0, 40.0))
    w = _link_mix(links, mul_amt, None, mix_io(mul_sw)[3], amt3.outputs[0])
    try:
        mix_io(mul_amt)[0].default_value = 1.0
    except Exception:
        pass
    sep = ng.nodes.new("ShaderNodeSeparateColor")
    sep.label = "wA wB wC"
    sep.location = (-80.0, 40.0)
    links.new(w, sep.inputs[0])
    white = ng.nodes.new("ShaderNodeRGB")
    white.label = "white canvas"
    white.outputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
    white.location = (-320.0, 280.0)
    na = ng.nodes.new("ShaderNodeRGB")
    na.label = "ColorA2" if secondary else "ColorA"
    na.outputs[0].default_value = _as_rgba(color_a)
    na.location = (40.0, 280.0)
    nb = ng.nodes.new("ShaderNodeRGB")
    nb.label = "ColorB2" if secondary else "ColorB"
    nb.outputs[0].default_value = _as_rgba(color_b)
    nb.location = (280.0, 200.0)
    nc = ng.nodes.new("ShaderNodeRGB")
    nc.label = "ColorC2" if secondary else "ColorC"
    nc.outputs[0].default_value = _as_rgba(color_c)
    nc.location = (520.0, 120.0)
    mix_a = gmix("MIX", (40.0, 40.0))
    _link_mix(links, mix_a, sep.outputs[0], white.outputs[0], na.outputs[0])
    mix_b = gmix("MIX", (280.0, -40.0))
    _link_mix(links, mix_b, sep.outputs[1], mix_io(mix_a)[3], nb.outputs[0])
    mix_c = gmix("MIX", (520.0, -120.0))
    folded = _link_mix(links, mix_c, sep.outputs[2], mix_io(mix_b)[3], nc.outputs[0])
    ov = ng.nodes.new("ShaderNodeRGB")
    ov.label = f"{zone}_BaseColorOverlay"
    ov.outputs[0].default_value = _as_rgba(overlay)
    ov.location = (760.0, 200.0)
    mul_ov = gmix("MULTIPLY", (760.0, 0.0))
    outc = _link_mix(links, mul_ov, None, folded, ov.outputs[0])
    try:
        mix_io(mul_ov)[0].default_value = 1.0
    except Exception:
        pass
    links.new(outc, go.inputs["Colour"])
    for n in ng.nodes:
        _quiet_node(n)
    return ng


def ensure_gt_decal_tint_group():
    """ArcDecal colour: CONSTANT 0.075 A/B + ColorOverride lerp (default.frag ApplyArcDecals)."""
    import bpy

    name = "GT_DecalTint"
    ng, created = _gt_ng_fresh(name, 2)
    if not created:
        return ng
    _gt_ng_sock(ng, "DecalColor", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "ColorA", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gt_ng_sock(ng, "ColorB", "INPUT", "NodeSocketColor", (0.0, 0.0, 0.0, 1.0))
    _gt_ng_sock(ng, "ColorOverride", "INPUT", "NodeSocketFloat", 0.0)
    _gt_ng_sock(ng, "Color", "OUTPUT", "NodeSocketColor")
    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-700.0, 0.0)
    go = ng.nodes.new("NodeGroupOutput")
    go.location = (520.0, 0.0)
    links = ng.links
    sep = ng.nodes.new("ShaderNodeSeparateColor")
    sep.location = (-480.0, 80.0)
    links.new(gi.outputs["DecalColor"], sep.inputs[0])
    add = ng.nodes.new("ShaderNodeMath")
    add.operation = "ADD"
    add.location = (-280.0, 120.0)
    links.new(sep.outputs[0], add.inputs[0])
    links.new(sep.outputs[1], add.inputs[1])
    add2 = ng.nodes.new("ShaderNodeMath")
    add2.operation = "ADD"
    add2.location = (-100.0, 80.0)
    links.new(add.outputs[0], add2.inputs[0])
    links.new(sep.outputs[2], add2.inputs[1])
    div = ng.nodes.new("ShaderNodeMath")
    div.operation = "DIVIDE"
    div.location = (80.0, 80.0)
    links.new(add2.outputs[0], div.inputs[0])
    div.inputs[1].default_value = 3.0
    cmp = ng.nodes.new("ShaderNodeMath")
    cmp.operation = "LESS_THAN"
    cmp.location = (260.0, 80.0)
    links.new(div.outputs[0], cmp.inputs[0])
    cmp.inputs[1].default_value = 0.075
    pick = ng.nodes.new("ShaderNodeMix")
    pick.data_type = "RGBA"
    pick.blend_type = "MIX"
    pick.location = (260.0, -80.0)
    _link_mix(links, pick, cmp.outputs[0], gi.outputs["ColorA"], gi.outputs["ColorB"])
    ov = ng.nodes.new("ShaderNodeMix")
    ov.data_type = "RGBA"
    ov.blend_type = "MIX"
    ov.location = (480.0, 0.0)
    outc = _link_mix(links, ov, gi.outputs["ColorOverride"], gi.outputs["DecalColor"], mix_io(pick)[3])
    links.new(outc, go.inputs["Color"])
    add_gt_note(
        ng.nodes,
        "NOTE_tint",
        "Decal tint (ApplyArcDecals)",
        "avg(RGB) < 0.075 → ColorB else ColorA (CONSTANT). Then mix(original, ramp, ColorOverride). "
        "Never mix the sticker toward clothing albedo.",
        -700.0,
        280.0,
    )
    return ng


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


def _clamp01(nodes, links, sock, name, x, y):
    cl = nodes.new("ShaderNodeClamp")
    cl.name = name
    try:
        cl.inputs["Min"].default_value = 0.0
        cl.inputs["Max"].default_value = 1.0
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
    nd = nodes.new("ShaderNodeValue")
    nd.name = name
    nd.label = label
    nd.outputs[0].default_value = float(val)
    place(nd, x, y)
    return nd.outputs[0]


def _rgb(nodes, name, label, rgba, x, y):
    nd = nodes.new("ShaderNodeRGB")
    nd.name = name
    nd.label = label
    nd.outputs[0].default_value = (
        float(rgba[0]),
        float(rgba[1]),
        float(rgba[2]),
        float(rgba[3]) if len(rgba) > 3 else 1.0,
    )
    place(nd, x, y)
    return nd


def mux_by_zone(nodes, links, zone_idx, zone_socks: dict, *, dtype, start, prefix, x_fn=None, y=None, col0=None, y_fn=None, hide_mix=True, zone_eq=None):
    """One mix per Colour column on a shared row. Missing zones leave a blank cell.

    Prefer ``zone_eq`` (shared GT_ZoneEq outputs) so the OCM Maximum node does not
    grow a noodle to every COMPARE.
    """
    acc = start
    first = None
    for zone in range(1, 9):
        src = zone_socks.get(zone)
        if src is None:
            continue
        if first is None:
            first = zone
        x = float(x_fn(zone)) if x_fn is not None else float(col0)
        yy = float(y) if y is not None else float(y_fn(zone))
        fac = None
        if zone_eq:
            fac = zone_eq.get(zone)
        elif zone_idx is not None:
            cmp = _math(
                nodes,
                "COMPARE",
                f"{prefix}_eq_z{zone}",
                x,
                yy,
                value=float(zone - 1),
                hide=True,
            )
            try:
                cmp.inputs[2].default_value = 0.45
            except Exception:
                pass
            links.new(zone_idx, cmp.inputs[0])
            fac = cmp.outputs[0]
        mix = _new_mix(
            nodes,
            f"{prefix}_z{zone}",
            f"{prefix} {zone}",
            x,
            yy,
            dtype=dtype,
            hide=hide_mix,
        )
        if fac is None:
            try:
                mix.inputs[0].default_value = 1.0 if zone == first else 0.0
            except Exception:
                pass
        acc = _link_mix(links, mix, fac, acc, src)
        if zone_eq:
            factor, _a, _b, _r = mix_io(mix)
            _collapse_broadcast(factor)
        lock_layout(mix)
    return acc


def _slice_sample(nodes, links, cache, png_list, slice_idx, uv, tile, *, non_color, name, x, y):
    if slice_idx is None or png_list is None:
        return None
    try:
        idx = int(round(float(slice_idx)))
    except (TypeError, ValueError):
        return None
    if idx < 0:
        return None
    key = (id(png_list), idx, id(uv) if uv is not None else 0, bool(non_color))
    if key in cache:
        return cache[key]
    path = textures.find_slice_png(png_list, idx)
    if not path:
        return None
    mapped = uv
    if abs(float(tile) - 1.0) > 1e-3 and uv is not None:
        mp = nodes.new("ShaderNodeMapping")
        mp.vector_type = "POINT"
        mp.name = f"{name}_map"
        mp.label = f"{name} tile {tile:g}"
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
            links.new(mapped, nd.inputs["Vector"])
        except Exception:
            pass
    cache[key] = nd
    return nd


def _uv_scaled(nodes, links, uv, scale_sock, name, x, y, *, z_one=False, hide=True):
    """UV × uniform scale via Mapping.

    ArrayUvScale is isotropic (25 on all axes) — wire the Value straight into
    Mapping Scale (Blender broadcasts float→vector). Only ``z_one`` (tile with
    Z locked to 1) still needs CombineXYZ.
    """
    mp = nodes.new("ShaderNodeMapping")
    mp.vector_type = "POINT"
    mp.name = name
    mp.label = name.replace("_", " ")
    mp.hide = hide
    place(mp, x, y, hide=hide)
    try:
        links.new(uv, mp.inputs["Vector"])
    except Exception:
        pass
    if z_one:
        comb = nodes.new("ShaderNodeCombineXYZ")
        comb.name = f"{name}_xyz"
        comb.label = f"{name} scale"
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


def _mask_float(color_sock):
    """TA Masks roughness as FLOAT without Separate Color.

    Snooper samples ``Masks.r`` (default.frag ArcRoughness / crease|edge rough).
    Cooked TA_*_Masks slices are grayscale Non-Color, so R=G=B and Blender's
    Color→Float (luminance) matches ``.r``. Skip one Separate per zone sample.
    """
    return color_sock


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
    n_flat = _rgb(
        nodes, "GT_n_flat", "neutral normal", (0.5, 0.5, 1.0, 1.0),
        start_x, colour_row_y("mux_n"),
    )
    _hide_start(n_flat.outputs[0])
    detail = mux_by_zone(
        nodes, links, zone_idx, n_base or {},
        dtype="RGBA", start=n_flat.outputs[0], prefix="GT_mux_nBase",
        zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("mux_n"), hide_mix=False,
    )
    return albedo, roughness, metallic, detail


def _frame_gt_nodes(nodes) -> None:
    """Colour/decal grids are the layout. Only group the Principled output cluster."""

    def _take(pred):
        out = []
        for n in list(nodes):
            if getattr(n, "type", "") == "FRAME":
                continue
            if getattr(n, "parent", None) is not None:
                continue
            name = getattr(n, "name", "") or ""
            if pred(name):
                out.append(n)
        return out

    def _mk(fname, label, members):
        if not members:
            return
        fr = nodes.new("NodeFrame")
        fr.name = fname
        fr.label = label
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


def finish_gt_outfit_graph(
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

    texcoord = nodes.new("ShaderNodeTexCoord")
    texcoord.name = "GT_UV"
    texcoord.label = "GT UV (mesh)"
    place(texcoord, mx_uv, shared_row_y("metal"), lock=True)
    uv_mesh = texcoord.outputs["UV"]
    array_scale = _value(
        nodes, "GT_ArrayUvScale", "ArrayUvScale (TA UV×25)",
        _GT_ARRAY_UV_SCALE, mx_uv, shared_row_y("c_rough"),
    )
    uv_ta = _uv_scaled(
        nodes, links, uv_mesh, array_scale,
        "GT_TA_ArrayUv", mx_uv, shared_row_y("e_rough"), z_one=False, hide=False,
    )
    # Per-row ArrayUv reroutes are built after TA images exist (_wire_array_uv_reroutes).
    uv_bus = {
        "e_rough": uv_ta,
        "c_rough": uv_ta,
        "rough": uv_ta,
        "n_base": uv_ta,
        "n_crease": uv_ta,
        "n_edge": uv_ta,
        "n_med": uv_ta,
    }

    mid_sock = None
    ocm_r = ocm_g = None
    ocm_sep = None
    if ocm_node is not None and getattr(ocm_node, "outputs", None):
        ocm_sep = nodes.new("ShaderNodeSeparateColor")
        ocm_sep.name = "GT_OCM_Separate"
        ocm_sep.label = "OCM R=occ G=curv B=mid"
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
        flr = nodes.new("ShaderNodeMath")
        flr.operation = "FLOOR"
        flr.name = "GT_zone_floor"
        flr.label = "floor(mid×8+0.5)"
        flr.hide = True
        place(flr, zx - 300.0, shared_row_y("zone_eq"), hide=True)
        links.new(add.outputs[0], flr.inputs[0])
        mn = _math(nodes, "MINIMUM", "GT_zone_max7", zx - 150.0, shared_row_y("zone_eq"), value=7.0, hide=True)
        links.new(flr.outputs[0], mn.inputs[0])
        mx = _math(nodes, "MAXIMUM", "GT_zone_min0", zx, shared_row_y("zone_eq"), value=0.0, hide=False)
        mx.label = "zone 0–7"
        links.new(mn.outputs[0], mx.inputs[0])
        zone_idx = mx.outputs[0]
        zeq_node, zone_eq = _wire_zone_eq(nodes, links, zone_idx, mx_ocm, shared_row_y("zone_eq"))
        zone_broadcast = zone_idx
        _ = zeq_node

    # Shirt 003: duplicate OCM near finish column for crease/edge clamps (clean local wiring).
    crease_fac = None
    edge_fac = None
    if ocm_node is not None and getattr(ocm_node, "outputs", None):
        ocm_wear = nodes.new("ShaderNodeTexImage")
        ocm_wear.name = "GT_OCM_Wear"
        ocm_wear.label = ocm_node.label or "OCM (wear)"
        try:
            ocm_wear.image = ocm_node.image
        except Exception:
            pass
        place(ocm_wear, gx - 900.0, shared_row_y("ocm_wear"), lock=True)
        ocm_wear_sep = nodes.new("ShaderNodeSeparateColor")
        ocm_wear_sep.name = "GT_OCM_Wear_Separate"
        ocm_wear_sep.label = "OCM R=occ G=curv B=mid"
        place(ocm_wear_sep, gx - 550.0, shared_row_y("ocm_wear"), lock=True)
        try:
            links.new(ocm_wear.outputs["Color"], ocm_wear_sep.inputs[0])
        except Exception:
            links.new(ocm_wear.outputs[0], ocm_wear_sep.inputs[0])
        try:
            wear_r = ocm_wear_sep.outputs["Red"]
            wear_g = ocm_wear_sep.outputs["Green"]
        except Exception:
            wear_r = ocm_wear_sep.outputs[0]
            wear_g = ocm_wear_sep.outputs[1] if len(ocm_wear_sep.outputs) > 1 else wear_r
        crease_fac = _clamp01(nodes, links, wear_g, "GT_creaseFac", gx - 200.0, shared_row_y("ocm_wear"))
        edge_fac = _clamp01(nodes, links, wear_r, "GT_edgeFac", gx, shared_row_y("ocm_wear") + 250.0)
    elif ocm_g is not None:
        crease_fac = _clamp01(nodes, links, ocm_g, "GT_creaseFac", mx_ocm, colour_row_y("c_rough"))
        if ocm_r is not None:
            edge_fac = _clamp01(nodes, links, ocm_r, "GT_edgeFac", mx_ocm, colour_row_y("e_rough"))

    apply_ce = False
    try:
        apply_ce = bool(C.crease_edge_color_enabled_from_scene())
    except Exception:
        apply_ce = False

    ta_cache = {}
    rough_socks = {}
    crease_r_socks = {}
    edge_r_socks = {}
    metal_socks = {}
    n_base = {}
    n_crease = {}
    n_edge = {}
    n_med = {}
    uv_layer = {
        "base": uv_bus["rough"],
        "mask": uv_bus["rough"],
    }
    for zone in range(1, 9):
        tx = colour_col_x(zone)
        bid = C._gt_ta_id(ta_ids, zone, "BaseRoughnessID")
        cid = C._gt_ta_id(ta_ids, zone, "CreaseRoughnessID")
        eid = C._gt_ta_id(ta_ids, zone, "EdgeRoughnessID")
        bn = C._gt_ta_id(ta_ids, zone, "BaseNormalID")
        cn = C._gt_ta_id(ta_ids, zone, "CreaseNormalID")
        en = C._gt_ta_id(ta_ids, zone, "EdgeNormalID")
        mn = C._gt_ta_id(ta_ids, zone, "MediumNormalID")
        ctile = max(1.0, C._mi_layer_float(zone_scalars, zone, "CreaseNormalTiling", 1.0) or 1.0)
        etile = max(1.0, C._mi_layer_float(zone_scalars, zone, "EdgeNormalTiling", 1.0) or 1.0)
        mtile = max(1.0, C._mi_layer_float(zone_scalars, zone, "MediumNormalTiling", 1.0) or 1.0)
        c_key = ("crease", round(ctile, 4))
        e_key = ("edge", round(etile, 4))
        m_key = ("med", round(mtile, 4))
        if c_key not in uv_layer:
            if abs(ctile - 1.0) <= 1e-3:
                uv_layer[c_key] = uv_bus["n_crease"]
            else:
                csv = _value(
                    nodes, f"GT_z{zone}_CreaseNTile", f"{zone}_CreaseNormalTiling",
                    ctile, mx_uv, colour_row_y("n_crease"),
                )
                uv_layer[c_key] = _uv_scaled(
                    nodes, links, uv_bus["n_crease"], csv,
                    f"GT_z{zone}_CreaseN_uv", mx_uv, colour_row_y("n_crease"),
                    z_one=True, hide=False,
                )
        if e_key not in uv_layer:
            if abs(etile - 1.0) <= 1e-3:
                uv_layer[e_key] = uv_bus["n_edge"]
            else:
                esv = _value(
                    nodes, f"GT_z{zone}_EdgeNTile", f"{zone}_EdgeNormalTiling",
                    etile, mx_uv, colour_row_y("n_edge"),
                )
                uv_layer[e_key] = _uv_scaled(
                    nodes, links, uv_bus["n_edge"], esv,
                    f"GT_z{zone}_EdgeN_uv", mx_uv, colour_row_y("n_edge"),
                    z_one=True, hide=False,
                )
        if m_key not in uv_layer:
            if abs(mtile - 1.0) <= 1e-3:
                uv_layer[m_key] = uv_bus["n_med"]
            else:
                msv = _value(
                    nodes, f"GT_z{zone}_MedNTile", f"{zone}_MediumNormalTiling",
                    mtile, mx_uv, colour_row_y("n_med"),
                )
                uv_layer[m_key] = _uv_scaled(
                    nodes, links, uv_bus["n_med"], msv,
                    f"GT_z{zone}_MedN_uv", mx_uv, colour_row_y("n_med"),
                    z_one=True, hide=False,
                )
        nd = _slice_sample(
            nodes, links, ta_cache, base_masks, bid, uv_bus["rough"], 1.0,
            non_color=True, name=f"GT_z{zone}_BaseRough", x=tx, y=shared_row_y("ta_rough"),
        )
        if nd is not None:
            rough_socks[zone] = _mask_float(_sock_color(nd))
        else:
            rough_socks[zone] = _value(
                nodes, f"GT_z{zone}_BaseRough_v", f"{zone}_BaseRoughness",
                C._gt_param_float(mi_data, f"{zone}_BaseRoughness", 0.5),
                subcol_x(tx), colour_row_y("rough"),
            )
        nd = _slice_sample(
            nodes, links, ta_cache, base_masks, cid, uv_bus["c_rough"], 1.0,
            non_color=True, name=f"GT_z{zone}_CreaseRough", x=tx, y=colour_row_y("c_rough"),
        )
        if nd is not None:
            crease_r_socks[zone] = _mask_float(_sock_color(nd))
        nd = _slice_sample(
            nodes, links, ta_cache, base_masks, eid, uv_bus["e_rough"], 1.0,
            non_color=True, name=f"GT_z{zone}_EdgeRough", x=tx, y=colour_row_y("e_rough"),
        )
        if nd is not None:
            edge_r_socks[zone] = _mask_float(_sock_color(nd))
        metal_v = C._gt_param_float(mi_data, f"{zone}_BaseMetallicity", 0.0)
        if abs(float(metal_v)) > 1e-6:
            metal_socks[zone] = _value(
                nodes, f"GT_z{zone}_Metal_v", f"{zone}_BaseMetallicity",
                metal_v, subcol_x(tx), colour_row_y("metal"),
            )
        nd = _slice_sample(
            nodes, links, ta_cache, base_normals, bn, uv_bus["n_base"], 1.0,
            non_color=True, name=f"GT_z{zone}_BaseN", x=tx, y=shared_row_y("ta_n_base"),
        )
        if nd is not None:
            n_base[zone] = _sock_color(nd)
        nd = _slice_sample(
            nodes, links, ta_cache, base_normals, cn, uv_layer[c_key], 1.0,
            non_color=True, name=f"GT_z{zone}_CreaseN", x=tx, y=colour_row_y("n_crease"),
        )
        if nd is not None:
            n_crease[zone] = _sock_color(nd)
        nd = _slice_sample(
            nodes, links, ta_cache, base_normals, en, uv_layer[e_key], 1.0,
            non_color=True, name=f"GT_z{zone}_EdgeN", x=tx, y=colour_row_y("n_edge"),
        )
        if nd is not None:
            n_edge[zone] = _sock_color(nd)
        nd = _slice_sample(
            nodes, links, ta_cache, base_normals, mn, uv_layer[m_key], 1.0,
            non_color=True, name=f"GT_z{zone}_MedN", x=tx, y=colour_row_y("n_med"),
        )
        if nd is not None:
            n_med[zone] = _sock_color(nd)

    albedo, roughness, metallic, detail = _link_zone_picks(
        nodes, links, zone_idx, zone_outputs, rough_socks, metal_socks, n_base,
        zone_eq=zone_eq,
    )

    crease_ov = {}
    edge_ov = {}
    if apply_ce:
        for zone in range(1, 9):
            ck = f"{zone}_CreaseColorOverlay"
            ek = f"{zone}_EdgeColorOverlay"
            cr = (colours or {}).get(ck)
            er = (colours or {}).get(ek)
            if cr is not None and not textures.skip_colour(cr):
                nd = nodes.get(ck) or _rgb(nodes, ck, ck, cr, 0.0, 0.0)
                crease_ov[zone] = nd.outputs[0]
            if er is not None and not textures.skip_colour(er):
                nd = nodes.get(ek) or _rgb(nodes, ek, ek, er, 0.0, 0.0)
                edge_ov[zone] = nd.outputs[0]
        zero = None
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
                sep = nodes.new("ShaderNodeSeparateColor")
                sep.name = "GT_creaseOv_sep"
                place(sep, gx, colour_row_y("ov_crease"), hide=True)
                links.new(crease_col, sep.inputs[0])
                mul = _math(nodes, "MULTIPLY", "GT_creaseOv_fac", gx, colour_row_y("ov_crease"))
                links.new(crease_fac, mul.inputs[0])
                try:
                    links.new(sep.outputs["Alpha"], mul.inputs[1])
                except Exception:
                    mul.inputs[1].default_value = 1.0
                mix = _new_mix(nodes, "GT_apply_creaseOv", "crease overlay", gx, colour_row_y("ov_crease"))
                albedo = _link_mix(links, mix, mul.outputs[0], albedo, crease_col)
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
                sep = nodes.new("ShaderNodeSeparateColor")
                sep.name = "GT_edgeOv_sep"
                place(sep, gx, colour_row_y("ov_edge"), hide=True)
                links.new(edge_col, sep.inputs[0])
                mul = _math(nodes, "MULTIPLY", "GT_edgeOv_fac", gx, colour_row_y("ov_edge"))
                links.new(edge_fac, mul.inputs[0])
                try:
                    links.new(sep.outputs["Alpha"], mul.inputs[1])
                except Exception:
                    mul.inputs[1].default_value = 1.0
                mix = _new_mix(nodes, "GT_apply_edgeOv", "edge overlay", gx, colour_row_y("ov_edge"))
                albedo = _link_mix(links, mix, mul.outputs[0], albedo, edge_col)
            for zone, sock in edge_ov.items():
                nd = getattr(sock, "node", None)
                if nd is not None:
                    nd.hide = True
                    place(nd, colour_col_x(zone), colour_row_y("ov_edge"), hide=True)

    if sparse_colormask and cm_src is not None and base_tex is not None:
        cm_sep = nodes.new("ShaderNodeSeparateColor")
        cm_sep.name = "GT_ColorMask_cov"
        cm_sep.label = "ColorMask coverage (RadioBag)"
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

    if ocm_r is not None and albedo is not None:
        occ_g = None
        try:
            occ_g = ensure_gt_albedo_occ_group()
        except Exception:
            occ_g = None
        if occ_g is not None:
            occ = nodes.new("ShaderNodeGroup")
            occ.node_tree = occ_g
            occ.name = "GT_albedo_x_occ"
            occ.label = "albedo × OCM.R"
            place(occ, gx, colour_row_y("occ"), lock=True)
            try:
                links.new(albedo, occ.inputs["Albedo"])
                links.new(ocm_r, occ.inputs["Occ"])
                albedo = occ.outputs["Result"]
            except Exception:
                albedo = _link_mix(
                    links,
                    _new_mix(nodes, "GT_albedo_x_occ_fb", "albedo × OCM.R", gx, colour_row_y("occ")),
                    None, albedo, albedo,
                )
        # else leave albedo un-occluded rather than invent Combine Color on the parent graph

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

    if crease_fac is not None:
        cm_vals = {}
        for zone in range(1, 9):
            v = C._gt_param_float(mi_data, f"{zone}_CreaseMetallicity", None)
            if v is None or abs(float(v)) <= 1e-6:
                continue
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
    if edge_fac is not None:
        em_vals = {}
        for zone in range(1, 9):
            v = C._gt_param_float(mi_data, f"{zone}_EdgeMetallicity", None)
            if v is None or abs(float(v)) <= 1e-6:
                continue
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

    # Extra normals: mux already chose Base N. Wear mixes Crease → Edge → Medium.
    # Shirt 003 U-turn reroutes on first mux A of each wear stage.
    if n_crease and crease_fac is not None:
        first_c = next((z for z in range(1, 9) if z in n_crease), None)
        nc = mux_by_zone(
            nodes, links, zone_idx, n_crease, dtype="RGBA", start=detail,
            prefix="GT_mux_nCrease", zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("n_crease"),
        )
        if first_c is not None:
            _uturn_feed(
                nodes, links, f"GT_mux_nCrease_z{first_c}",
                "GT_RR_nCrease_R", "GT_RR_nCrease_L",
                gx - 200.0, colour_row_y("n_crease") + 300.0,
                colour_col_x(1) - 150.0, colour_row_y("n_crease") + 260.0,
            )
        mix = _new_mix(nodes, "GT_n_crease", "N Base→Crease", gx, colour_row_y("n_crease"))
        detail = _link_mix(links, mix, crease_fac, detail, nc)
    if n_edge and edge_fac is not None:
        first_e = next((z for z in range(1, 9) if z in n_edge), None)
        ne = mux_by_zone(
            nodes, links, zone_idx, n_edge, dtype="RGBA", start=detail,
            prefix="GT_mux_nEdge", zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("n_edge"),
        )
        if first_e is not None:
            _uturn_feed(
                nodes, links, f"GT_mux_nEdge_z{first_e}",
                "GT_RR_nEdge_R", "GT_RR_nEdge_L",
                gx + 100.0, colour_row_y("n_edge") + 300.0,
                colour_col_x(1) - 150.0, colour_row_y("n_edge") + 300.0,
            )
        mix = _new_mix(nodes, "GT_n_edge", "N →Edge", gx, colour_row_y("n_edge") + 150.0)
        detail = _link_mix(links, mix, edge_fac, detail, ne)
    if n_med:
        med_facs = {}
        for zone in range(1, 9):
            s = max(0.0, min(1.0, C._mi_layer_float(zone_scalars, zone, "MediumNormalStrength", 0.0)))
            if s <= 1e-5 or zone not in n_med:
                continue
            med_facs[zone] = _value(
                nodes, f"GT_z{zone}_medStr", "MediumNormalStrength", s,
                subcol_x(colour_col_x(zone)), colour_row_y("n_med"),
            )
            if getattr(med_facs[zone], "node", None) is not None:
                med_facs[zone].node.hide = True
        nm = mux_by_zone(
            nodes, links, zone_idx, n_med, dtype="RGBA", start=detail,
            prefix="GT_mux_nMed", zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("n_med"),
        )
        first_m = next((z for z in range(1, 9) if z in n_med), None)
        if first_m is not None:
            _uturn_feed(
                nodes, links, f"GT_mux_nMed_z{first_m}",
                "GT_RR_nMed_R", "GT_RR_nMed_L",
                gx + 380.0, colour_row_y("n_med") + 400.0,
                colour_col_x(1) - 110.0, colour_row_y("n_med") + 320.0,
            )
        med0 = _value(
            nodes, "GT_medStr0", "med 0", 0.0,
            colour_col_x(1) - _GT_GRID_DX, colour_row_y("n_med"),
        ) if med_facs else None
        if med0 is not None and getattr(med0, "node", None) is not None:
            med0.node.hide = True
        fac_m = mux_by_zone(
            nodes, links, zone_idx, med_facs, dtype="FLOAT",
            start=med0,
            prefix="GT_mux_medStr", zone_eq=zone_eq, x_fn=colour_col_x, y=colour_row_y("n_med"),
        ) if med_facs else None
        if fac_m is not None:
            mix = _new_mix(nodes, "GT_n_medium", "N →Medium", gx, colour_row_y("n_med"))
            detail = _link_mix(links, mix, fac_m, detail, nm)
    mesh_n = _sock_color(normal_node) if normal_node is not None else None
    if mesh_n is None:
        n_flat = nodes.get("GT_n_flat")
        if n_flat is not None:
            mesh_n = n_flat.outputs[0]
        else:
            n_flat = _rgb(
                nodes, "GT_n_flat", "neutral normal", (0.5, 0.5, 1.0, 1.0),
                colour_col_x(1) - _GT_GRID_DX, colour_row_y("mux_n"),
            )
            mesh_n = n_flat.outputs[0]
    n_mix = _new_mix(
        nodes, "GT_n_mesh_detail", "mesh N Overlay (EnableNormalMask)",
        gx, colour_row_y("mux_n"),
    )
    n_mix.blend_type = "OVERLAY"
    try:
        n_mix.inputs[0].default_value = 1.0
    except Exception:
        pass
    n_color = _link_mix(links, n_mix, None, mesh_n, detail)

    # Dynamic ArrayUv bus: one reroute per TA image row (Shirt 003 style).
    _wire_array_uv_reroutes(nodes, links, uv_ta, bus_x=maps_ocm_x() + 560.0)

    # Decals (FMODEL / ArcDecalUv), mixed here — not Arc sockets.
    albedo, n_color, roughness, metallic = _apply_gt_decals(
        C, nodes, links, albedo, n_color, roughness, metallic,
        zone_broadcast, decals or [], decal_folder, search_dirs or [],
        decal_x, decal_y,
    )
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
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.name = "GT_Principled"
    bsdf.label = "GT Principled (full outfit PBR)"
    place(bsdf, bsdf_x, bsdf_y, lock=True)
    try:
        links.new(albedo, bsdf.inputs["Base Color"])
    except Exception:
        links.new(albedo, bsdf.inputs[0])
    try:
        links.new(roughness, bsdf.inputs["Roughness"])
    except Exception:
        pass
    try:
        links.new(metallic, bsdf.inputs["Metallic"])
    except Exception:
        pass
    nmap = nodes.new("ShaderNodeNormalMap")
    nmap.name = "GT_NormalMap"
    place(nmap, bsdf_x - _GT_GRID_DX, mix_n_y, lock=True)
    try:
        links.new(n_color, nmap.inputs["Color"])
        links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    except Exception:
        pass
    out = nodes.new("ShaderNodeOutputMaterial")
    out.name = "GT_MaterialOutput"
    place(out, bsdf_x + _GT_OUTPUT_GAP, bsdf_y, lock=True)
    try:
        links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    except Exception:
        links.new(bsdf.outputs[0], out.inputs[0])
    note_y = shared_row_y("decal_rot") + _GT_GRID_DY
    add_gt_note(
        nodes, "NOTE_UV", "UV / ArrayUvScale",
        "Mesh UV × 25 for TextureArray extras (Snooper ArrayUvScale). Engine math, not a Blender conversion.",
        mx_uv, note_y,
    )
    add_gt_note(
        nodes, "NOTE_ZONE", "OCM zone index",
        "floor(mid×8+0.5) clamped 0–7. One GT_ZoneEq1 per Colour column "
        "(Match = zone id); Maximum only links once into those groups.",
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
        "Overlay extra TA onto the mesh normal (EnableNormalMask), then one Normal Map (*2-1 + TBN).",
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
    n_orphans = prune_orphan_nodes(nodes)
    if n_orphans:
        print(f"Arc Raiders: GT pruned {n_orphans} unlinked node(s)")
    # Do not run global overlap-nudge: locked decal/ZonePick grids become
    # obstacles and shove Principled/notes tens of thousands of units away.
    # Opening the Shader Editor then allocates a View2D for that huge AABB.
    for n in nodes:
        _quiet_node(n)
    _purge_unused_gt_datablocks()
    _frame_gt_nodes(nodes)
    return bsdf


def _apply_gt_decals(
    C, nodes, links, albedo, n_color, roughness, metallic,
    zone_idx, decals, decal_folder, search_dirs, origin_x, origin_y,
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

    decal_uv_node = None
    tex_cache = {}
    data_cache = {}
    method = C.active_decal_method()
    used = 0
    skipped = []
    parsed = len(decals or [])
    for decal in decals:
        if not C._decal_slot_is_used(decal):
            skipped.append(f"unused:{decal.get('index')}")
            continue
        tex_stem = (decal.get("texture") or "").strip()
        tex_fpath = C.resolve_decal_texture(
            tex_stem, decal.get("texture_path", ""), df, search_dirs=dirs,
        )
        if not tex_fpath and tex_stem not in tex_cache:
            skipped.append(f"missing:{tex_stem or decal.get('index')}")
            continue
        if tex_stem in tex_cache and tex_cache[tex_stem] is None and not tex_fpath:
            skipped.append(f"missing:{tex_stem}")
            continue
        idx = decal["index"]
        sat_x, main_x = _decal_slot_xs(origin_x, idx)
        used += 1

        def _at(stage, nd=None):
            y = _decal_row_y(origin_y, stage)
            if nd is not None:
                place(nd, main_x, y, lock=True)
            return y

        extension = C.decal_image_extension(decal)
        if decal_uv_node is None:
            decal_uv_node = nodes.new("ShaderNodeTexCoord")
            decal_uv_node.name = "GT_DecalUV"
            decal_uv_node.label = "Decal UV"
            uv_x = origin_x - _GT_GRID_DX
            place(decal_uv_node, uv_x, _decal_row_y(origin_y, "rot"), lock=True)

        uv_u = decal["uv_u"]
        uv_v = decal["uv_v"]
        scale = decal["scale"]
        rotation = decal["rotation"]
        width_ratio = decal.get("width_ratio")
        try:
            width_ratio = float(width_ratio)
        except (TypeError, ValueError):
            width_ratio = 0.0
        if width_ratio <= 0.0:
            probe_path = tex_fpath or C.resolve_decal_texture(
                tex_stem, decal.get("texture_path", ""), df, search_dirs=dirs,
            )
            if probe_path:
                ratio = C._width_ratio_from_path(probe_path)
                if ratio <= 0.0:
                    probe_image = _load_image_cached(probe_path)
                    if probe_image is not None and probe_image.size[1] > 0:
                        ratio = float(probe_image.size[0]) / float(probe_image.size[1])
                        _WIDTH_RATIO_BY_PATH[_norm_path_key(probe_path)] = ratio
                if ratio > 0.0:
                    width_ratio = ratio
        if width_ratio <= 0.0:
            width_ratio = 1.0
        layer_mask_int = C._decal_layer_mask_int(decal.get("layer_mask", 255.0))
        color_override = max(0.0, min(1.0, float(decal.get("color_override", 0.0))))
        slot_method = method
        if slot_method == "FMODEL" and "ducttape" in tex_stem.lower():
            slot_method = "WIDTH_2X"
        params = C._decal_mapping_params(uv_u, uv_v, scale, rotation, width_ratio, method=slot_method)

        mapping_node = nodes.new("ShaderNodeMapping")
        mapping_node.vector_type = "POINT"
        mapping_node.label = f"Decal {idx} rot [{slot_method}]"
        mapping_node["arc_decal_layer_mask"] = C._decal_visibility_layer_mask(layer_mask_int)
        mapping_node["arc_decal_layer_mask_raw"] = layer_mask_int
        _at("rot", mapping_node)
        mapping_node.inputs["Location"].default_value = params["rot_location"]
        mapping_node.inputs["Rotation"].default_value = (0.0, 0.0, params["rot_rotation"])
        mapping_node.inputs["Scale"].default_value = params["rot_scale"]
        links.new(decal_uv_node.outputs["UV"], mapping_node.inputs["Vector"])

        place_node = nodes.new("ShaderNodeMapping")
        place_node.vector_type = "POINT"
        place_node.label = f"Decal {idx} Scale/Loc"
        _at("place", place_node)
        place_node.inputs["Location"].default_value = params["place_location"]
        place_node.inputs["Rotation"].default_value = (0.0, 0.0, params["place_rotation"])
        place_node.inputs["Scale"].default_value = params["place_scale"]
        links.new(mapping_node.outputs["Vector"], place_node.inputs["Vector"])
        placed_uv = place_node.outputs["Vector"]
        if params["flip_v"]:
            flip_node = nodes.new("ShaderNodeMapping")
            flip_node.vector_type = "POINT"
            flip_node.label = f"Decal {idx} V Flip"
            _at("flip", flip_node)
            flip_node.inputs["Location"].default_value = (0.0, 1.0, 0.0)
            flip_node.inputs["Scale"].default_value = (1.0, -1.0, 1.0)
            links.new(placed_uv, flip_node.inputs["Vector"])
            placed_uv = flip_node.outputs["Vector"]

        color_tex = None
        if tex_stem not in tex_cache:
            if tex_fpath:
                img = _load_image_cached(tex_fpath)
                if img is not None:
                    tex_node = nodes.new("ShaderNodeTexImage")
                    tex_node.image = img
                    tex_node.label = f"Decal {idx}: {tex_stem}"
                    tex_node.interpolation = "Cubic"
                    tex_node.extension = extension
                    _at("color", tex_node)
                    links.new(placed_uv, tex_node.inputs["Vector"])
                    tex_cache[tex_stem] = tex_node
                    color_tex = tex_node
                else:
                    tex_cache[tex_stem] = None
            else:
                tex_cache[tex_stem] = None
        else:
            prev = tex_cache[tex_stem]
            if prev is not None:
                dup = nodes.new("ShaderNodeTexImage")
                dup.image = prev.image
                dup.label = f"Decal {idx}: {tex_stem}"
                dup.interpolation = "Cubic"
                dup.extension = extension
                _at("color", dup)
                links.new(placed_uv, dup.inputs["Vector"])
                color_tex = dup

        data_tex = None
        data_stem = decal.get("data_texture", "")
        if data_stem:
            if data_stem not in data_cache:
                data_fpath = C.resolve_decal_texture(
                    data_stem, decal.get("data_texture_path", ""), df, search_dirs=dirs,
                )
                if data_fpath:
                    data_img = _load_image_cached(data_fpath)
                    if data_img is not None:
                        try:
                            data_img.colorspace_settings.name = "Non-Color"
                        except Exception:
                            pass
                        data_node = nodes.new("ShaderNodeTexImage")
                        data_node.image = data_img
                        data_node.label = f"Decal {idx} Data"
                        data_node.interpolation = "Cubic"
                        data_node.extension = extension
                        _at("data", data_node)
                        links.new(placed_uv, data_node.inputs["Vector"])
                        data_cache[data_stem] = data_node
                        data_tex = data_node
                    else:
                        data_cache[data_stem] = None
                else:
                    data_cache[data_stem] = None
            else:
                prev = data_cache[data_stem]
                if prev is not None:
                    dup = nodes.new("ShaderNodeTexImage")
                    dup.image = prev.image
                    dup.label = f"Decal {idx} Data"
                    dup.interpolation = "Cubic"
                    dup.extension = extension
                    _at("data", dup)
                    links.new(placed_uv, dup.inputs["Vector"])
                    data_tex = dup

        if color_tex is None:
            skipped.append(f"noload:{tex_stem}")
            continue
        alpha = None
        if "Alpha" in color_tex.outputs:
            alpha = color_tex.outputs["Alpha"]
        if alpha is None and len(color_tex.outputs) > 1:
            alpha = color_tex.outputs[1]
        if alpha is None:
            alpha = _value(
                nodes, f"GT_decal{idx}_opaque", "opaque alpha", 1.0,
                sat_x, _decal_row_y(origin_y, "layer"),
            )
        # Cooked ArcDecalLayerOk (Allow bits) first. Optional material
        # ``arc_decal_exclude_mask`` is a manual override on top (Excl1..8) —
        # it never replaces the cooked Allow map.
        vis_mask = C._decal_visibility_layer_mask(layer_mask_int)
        excl_mask = 0
        try:
            _tree = nodes.id_data
            _owner = getattr(_tree, "id_data", None) or _tree
            if hasattr(_owner, "get"):
                excl_mask = int(_owner.get("arc_decal_exclude_mask", 0) or 0) & 0xFF
        except Exception:
            excl_mask = 0
        need_gate = zone_idx is not None and (
            C._should_emit_decal_layer_mask_node(layer_mask_int) or excl_mask != 0
        )
        if need_gate:
            lg = None
            try:
                # Editable group keeps cooked Allow* and optional Excl* override.
                lg = ensure_gt_decal_layer_edit_group()
            except Exception:
                lg = None
            if lg is None:
                try:
                    lg = ensure_gt_decal_layer_group(vis_mask)
                except Exception:
                    lg = None
            if lg is not None:
                gate_n = nodes.new("ShaderNodeGroup")
                gate_n.node_tree = lg
                gate_n.name = f"GT_decal{idx}_layer"
                gate_n["arc_decal_layer_mask"] = vis_mask
                gate_n["arc_decal_layer_mask_raw"] = int(layer_mask_int)
                gate_n["arc_decal_idx"] = int(idx)
                if getattr(lg, "name", "") == "GT_DecalLayerEdit":
                    configure_gt_decal_layer_edit(gate_n, vis_mask, excl_mask)
                else:
                    try:
                        gate_n["arc_decal_exclude_mask"] = excl_mask
                    except Exception:
                        pass
                excl_txt = ""
                if excl_mask:
                    parts = [str(z + 1) for z in range(8) if excl_mask & (1 << z)]
                    excl_txt = f"; excl {','.join(parts)}"
                gate_n.label = f"decal {idx} LayerMask ({vis_mask}{excl_txt})"
                _at("layer", gate_n)
                try:
                    links.new(zone_idx, gate_n.inputs["ZoneIndex"])
                    links.new(alpha, gate_n.inputs["Alpha"])
                    alpha = gate_n.outputs["Masked"]
                    _collapse_broadcast(gate_n.inputs["ZoneIndex"])
                except Exception:
                    pass
                if getattr(alpha, "node", None) is not None and getattr(alpha.node, "bl_idname", "") == "ShaderNodeValue":
                    place_inputs_left_of(gate_n, alpha.node)

        decal_rgb = _sock_color(color_tex)
        ca = _decal_rgb3(decal.get("color_a"))
        cb = _decal_rgb3(decal.get("color_b"))
        tint_fac = float(color_override)
        if ca is not None and cb is not None and tint_fac < 0.01:
            tint_fac = 1.0
        if ca is not None and cb is not None:
            tint_tree = None
            try:
                tint_tree = ensure_gt_decal_tint_group()
            except Exception:
                tint_tree = None
            if tint_tree is not None:
                tint = nodes.new("ShaderNodeGroup")
                tint.node_tree = tint_tree
                tint.name = f"GT_decal{idx}_tint"
                tint.label = f"decal {idx} A/B + ColorOverride"
                _at("tint", tint)
                na = _rgb(nodes, f"GT_decal{idx}_A", f"{idx}_ColorA", (*ca, 1.0), 0.0, 0.0)
                nb = _rgb(nodes, f"GT_decal{idx}_B", f"{idx}_ColorB", (*cb, 1.0), 0.0, 0.0)
                place_colors_left_of(tint, na, nb)
                links.new(decal_rgb, tint.inputs["DecalColor"])
                links.new(na.outputs[0], tint.inputs["ColorA"])
                links.new(nb.outputs[0], tint.inputs["ColorB"])
                try:
                    tint.inputs["ColorOverride"].default_value = float(tint_fac)
                except Exception:
                    pass
                try:
                    decal_rgb = tint.outputs["Color"]
                except Exception:
                    decal_rgb = tint.outputs[0]
        elif ca is not None and tint_fac > 0.01:
            mix_a = _new_mix(nodes, f"GT_decal{idx}_Aonly", f"decal {idx} ColorA", 0.0, 0.0)
            _at("tint", mix_a)
            na = _rgb(nodes, f"GT_decal{idx}_A", f"{idx}_ColorA", (*ca, 1.0), 0.0, 0.0)
            place_colors_left_of(mix_a, na)
            try:
                mix_a.inputs[0].default_value = float(tint_fac)
            except Exception:
                pass
            decal_rgb = _link_mix(links, mix_a, None, decal_rgb, na.outputs[0])

        mix_c = _new_mix(nodes, f"GT_decal{idx}_albedo", f"decal {idx} albedo", 0.0, 0.0)
        _at("albedo", mix_c)
        albedo = _link_mix(links, mix_c, alpha, albedo, decal_rgb)

        if data_tex is not None:
            d_rough = None
            d_metal = None
            d_n = None
            dd_tree = None
            try:
                from .. import utils as _u
                if _u.ensure_decal_data_node_group():
                    dd_tree = _u.find_node_group(_u._DECAL_DATA_GROUP)
            except Exception:
                dd_tree = None
            if dd_tree is not None:
                dd = nodes.new("ShaderNodeGroup")
                dd.node_tree = dd_tree
                dd.name = f"GT_decal{idx}_dataG"
                dd.label = f"decal {idx} Decal Data"
                _at("unpack", dd)
                for iname in ("Color", "DecalData", "Decal Data"):
                    if iname in dd.inputs:
                        links.new(_sock_color(data_tex), dd.inputs[iname])
                        break
                if "Mask" in dd.inputs:
                    links.new(alpha, dd.inputs["Mask"])
                if "Normal" in dd.outputs:
                    d_n = dd.outputs["Normal"]
                if "Roughness" in dd.outputs:
                    d_rough = dd.outputs["Roughness"]
                if "Metallic" in dd.outputs:
                    d_metal = dd.outputs["Metallic"]
            if d_n is None:
                dsep = nodes.new("ShaderNodeSeparateColor")
                dsep.name = f"GT_decal{idx}_data"
                _at("unpack", dsep)
                links.new(_sock_color(data_tex), dsep.inputs[0])
                d_r, d_g = dsep.outputs[0], dsep.outputs[1]
                d_b = dsep.outputs[2] if len(dsep.outputs) > 2 else d_r
                comb_n = nodes.new("ShaderNodeCombineColor")
                comb_n.name = f"GT_decal{idx}_nxy"
                _w, h = node_footprint(dsep)
                place(
                    comb_n, main_x,
                    _decal_row_y(origin_y, "unpack") - h - _INPUT_STACK_GAP,
                    lock=True,
                )
                links.new(d_r, comb_n.inputs[0])
                links.new(d_g, comb_n.inputs[1])
                try:
                    comb_n.inputs[2].default_value = 1.0
                except Exception:
                    pass
                d_n = comb_n.outputs[0]
                d_rough = d_b
                if "Alpha" in data_tex.outputs:
                    d_metal = data_tex.outputs["Alpha"]
            mix_n = _new_mix(nodes, f"GT_decal{idx}_n", f"decal {idx} normal", 0.0, 0.0)
            _at("mix_n", mix_n)
            n_color = _link_mix(links, mix_n, alpha, n_color, d_n)
            if d_rough is not None:
                mix_r = _new_mix(
                    nodes, f"GT_decal{idx}_r", f"decal {idx} rough", 0.0, 0.0, dtype="FLOAT",
                )
                _at("mix_r", mix_r)
                roughness = _link_mix(links, mix_r, alpha, roughness, d_rough)
            if d_metal is not None:
                mix_m = _new_mix(
                    nodes, f"GT_decal{idx}_m", f"decal {idx} metal", 0.0, 0.0, dtype="FLOAT",
                )
                _at("mix_m", mix_m)
                metallic = _link_mix(links, mix_m, alpha, metallic, d_metal)

    print(
        f"Arc Raiders: GT decals parsed={parsed} built={used}"
        + (f" skipped={skipped}" if skipped else "")
    )
    return albedo, n_color, roughness, metallic
