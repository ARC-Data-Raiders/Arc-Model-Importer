"""Shared weathering streaks and top-down dust for TrimMap / PropTrim props.

Both presets end their chain with two library-driven passes that no MI ever
overrides with its own texture, so they are invisible unless we pull the
texture out of the material library ourselves:

* ``5. Weathering`` — vertical grime streaks from the ``TA_Weathering_Props_01``
  texture array, tinted with ``WeatheringColor`` (``B18759``, a warm brown).
* ``Use Dust`` — top-down settled dust from ``T_Propdust_01_X``, masked by how
  upward-facing the surface is (``Top Down Dust Bias`` / ``Sharpness``).

UE texture arrays are exported one PNG per slice (``TA_Weathering_Props_01_0``
… ``_3``), so ``Weathering Index`` selects the file rather than a sub-rect.
"""
from __future__ import annotations

import os

from ..common import (
    _load_image_cached,
    _mask_channel_value,
    _mi_colour,
    _mi_scalar,
    _mi_switch,
    _mix_float,
    _mix_rgba,
    _new_tex_image,
    _resolve_mi_texture_path,
)

WEATHERING_ARRAY = "/Game/Pioneer/MaterialLibrary/Textures/Props/Detail/TA_Weathering_Props_01"
DUST_TEXTURE = "/Game/Pioneer/MaterialLibrary/Textures/Props/T_Propdust_01_X"
DUST_BREAKUP_TEXTURE = "/Game/Pioneer/MaterialLibrary/Textures/Props/T_Propdust_01_Breakup_01_X"
DETAIL_ARRAY = "/Game/Pioneer/MaterialLibrary/Textures/Props/Detail/TA_Detail_Props_02"

# WeatheringColor default from M_TrimMap_01 / M_PropTrimPreset (hex B18759).
_DEFAULT_WEATHERING_RGBA = (0.4375, 0.242337, 0.10026, 1.0)

# Probe-calibrated coverage scale (map_tools/probe_trimmap_albedo.py --variant
# v3_less_weather). Full strength*blend (~0.55 on the elevator) over-muddied
# grey paint away from RT Base Color olive; 0.45 restores chroma without
# inventing a ColorTint the MI did not author.
_WEATHERING_COVERAGE_SCALE = 0.45


def load_library_image(obj_path: str, local_folders=None, slice_index=None):
    """Load a MaterialLibrary texture, optionally a ``_N`` array slice."""
    if not obj_path:
        return None
    candidates = []
    if slice_index is not None:
        try:
            idx = max(int(round(float(slice_index))), 0)
        except (TypeError, ValueError):
            idx = 0
        candidates.append(f"{obj_path}_{idx}")
        if idx != 0:
            candidates.append(f"{obj_path}_0")
    candidates.append(obj_path)
    for cand in candidates:
        fpath = _resolve_mi_texture_path(cand, local_folders)
        if fpath and os.path.isfile(fpath):
            return _load_image_cached(fpath)
    return None


def _world_projected_vector(nodes, links, tiling: float, loc, label: str):
    """Object-space XY mapping so streaks/dust stay put under mesh UV shells."""
    tex_co = nodes.new("ShaderNodeTexCoord")
    tex_co.location = (loc[0] - 220, loc[1])
    mapping = nodes.new("ShaderNodeMapping")
    mapping.label = label
    mapping.location = loc
    scale = max(float(tiling), 0.0001)
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    links.new(tex_co.outputs["Object"], mapping.inputs["Vector"])
    return mapping.outputs["Vector"]


def _up_facing_mask(nodes, links, bias: float, sharpness: float, loc, label: str):
    """saturate(Nz * sharpness + bias) — UE's top-down dust / streak gate."""
    geo = nodes.new("ShaderNodeNewGeometry")
    geo.location = (loc[0] - 400, loc[1])
    sep = nodes.new("ShaderNodeSeparateXYZ")
    sep.label = "Normal Z"
    sep.location = (loc[0] - 220, loc[1])
    links.new(geo.outputs["Normal"], sep.inputs["Vector"])
    mul = nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY_ADD"
    mul.label = label
    mul.use_clamp = True
    mul.location = loc
    links.new(sep.outputs["Z"], mul.inputs[0])
    mul.inputs[1].default_value = max(float(sharpness), 0.01)
    mul.inputs[2].default_value = float(bias)
    return mul.outputs["Value"]


def apply_weathering(
    nodes,
    links,
    *,
    mi: dict,
    albedo_sock,
    rough_sock=None,
    local_folders=None,
    loc=(0, -2000),
):
    """Mix ``WeatheringColor`` streaks over *albedo_sock*.

    Returns ``(albedo_sock, rough_sock, applied)``.
    """
    if albedo_sock is None:
        return albedo_sock, rough_sock, False
    scalars = mi.get("scalars") or {}
    switches = mi.get("switches") or {}
    colours = mi.get("colours") or []

    enabled = _mi_switch(
        switches, "5. Weathering", "Weathering", "Use Weathering", default=False,
    )
    if not enabled:
        return albedo_sock, rough_sock, False

    strength = _mi_scalar(
        scalars, "5. Weathering Strength", "Weathering Strength", default=1.0,
    )
    if strength <= 0.001:
        return albedo_sock, rough_sock, False

    index = _mi_scalar(scalars, "Weathering Index", default=0.0)
    img = load_library_image(WEATHERING_ARRAY, local_folders, slice_index=index)
    if img is None:
        return albedo_sock, rough_sock, False

    blend = _mi_scalar(scalars, "5. Weathering Blend", "Weathering Blend", default=0.53)
    rng = _mi_scalar(scalars, "5. Weathering Range", "Weathering Range", default=1.0)
    opacity = _mi_scalar(scalars, "5. Weathering Opacity", default=1.0)
    mask_tile = _mi_scalar(
        scalars, "5. Weather Mask Tiling", "Weathering Tiling", default=5.0,
    )
    w_rough = _mi_scalar(scalars, "5. Weathering Roughness", default=0.0)

    ox, oy = loc
    node = _new_tex_image(nodes, img, "Weathering Streaks", (ox, oy))
    links.new(
        _world_projected_vector(
            nodes, links, mask_tile, (ox - 400, oy), f"Weathering ×{mask_tile:g}",
        ),
        node.inputs["Vector"],
    )
    mask = _mask_channel_value(nodes, links, node, (ox + 300, oy))

    # Streaks run down from upward-facing edges: bias the mask by facing.
    facing = _up_facing_mask(
        nodes, links, -0.15, 1.35, (ox + 300, oy - 260), "Streak Facing",
    )
    gate = nodes.new("ShaderNodeMath")
    gate.operation = "MULTIPLY"
    gate.label = "Streaks × Facing"
    gate.use_clamp = True
    gate.location = (ox + 500, oy - 120)
    links.new(mask, gate.inputs[0])
    links.new(facing, gate.inputs[1])

    amount = nodes.new("ShaderNodeMath")
    amount.operation = "MULTIPLY"
    amount.label = "Weathering Amount"
    amount.use_clamp = True
    amount.location = (ox + 680, oy - 120)
    links.new(gate.outputs["Value"], amount.inputs[0])
    amount.inputs[1].default_value = min(
        max(
            float(strength)
            * float(blend)
            * max(float(rng), 0.0)
            * float(opacity)
            * _WEATHERING_COVERAGE_SCALE,
            0.0,
        ),
        1.0,
    )
    fac = amount.outputs["Value"]

    rgba = _mi_colour(
        colours, "WeatheringColor", "Weathering Color", default=_DEFAULT_WEATHERING_RGBA,
    )
    col = nodes.new("ShaderNodeRGB")
    col.label = "WeatheringColor"
    col.location = (ox + 680, oy + 180)
    col.outputs[0].default_value = (
        float(rgba[0]), float(rgba[1]), float(rgba[2]), 1.0,
    )

    stained = _mix_rgba(
        nodes, links, albedo_sock, col.outputs[0], 1.0,
        (ox + 880, oy + 120), "Albedo × Weathering", blend="MULTIPLY",
    )
    albedo_out = _mix_rgba(
        nodes, links, albedo_sock, stained, fac, (ox + 1060, oy), "Weathering Mix",
    )

    rough_out = rough_sock
    if rough_sock is not None and w_rough > 0.001:
        rv = nodes.new("ShaderNodeValue")
        rv.label = "Weathering Roughness"
        rv.location = (ox + 880, oy - 300)
        rv.outputs[0].default_value = min(max(float(w_rough), 0.0), 1.0)
        rough_out = _mix_float(
            nodes, links, rough_sock, rv.outputs[0], fac,
            (ox + 1060, oy - 300), "Weathering → Rough",
        )
    return albedo_out, rough_out, True


def apply_top_down_dust(
    nodes,
    links,
    *,
    mi: dict,
    albedo_sock,
    rough_sock=None,
    local_folders=None,
    loc=(0, -2600),
):
    """Settle ``T_Propdust_01_X`` on upward-facing surfaces.

    Returns ``(albedo_sock, rough_sock, applied)``.
    """
    if albedo_sock is None:
        return albedo_sock, rough_sock, False
    scalars = mi.get("scalars") or {}
    switches = mi.get("switches") or {}
    colours = mi.get("colours") or []

    if not _mi_switch(switches, "Use Dust", "UseDust", default=False):
        return albedo_sock, rough_sock, False

    multiply = _mi_scalar(scalars, "AO Dust Multiply", default=1.0)
    if multiply <= 0.001:
        return albedo_sock, rough_sock, False
    contrast = _mi_scalar(scalars, "AO Dust Contrast", default=0.5)
    bias = _mi_scalar(scalars, "Top Down Dust Bias", default=-1.5)
    sharpness = _mi_scalar(scalars, "Top Down Dust Sharpness", default=5.0)
    tiling = _mi_scalar(scalars, "Dust texture Tilling", "Dust Texture Tiling", default=512.0)
    breakup_tile = _mi_scalar(scalars, "Dust Detail Breakup Tiling", default=1.0)
    breakup_str = _mi_scalar(scalars, "Dust Extra World Breakup Strength", default=0.5)

    img = load_library_image(DUST_TEXTURE, local_folders)
    if img is None:
        return albedo_sock, rough_sock, False

    ox, oy = loc
    # Authored against UE's 100 uu/m world; keep the grain visible in Blender.
    uv_tiling = max(float(tiling) / 128.0, 0.05)

    facing = _up_facing_mask(
        nodes, links, bias, sharpness, (ox, oy), "Top-Down Dust Facing",
    )

    dust_node = _new_tex_image(nodes, img, "Prop Dust", (ox, oy - 300))
    links.new(
        _world_projected_vector(
            nodes, links, uv_tiling, (ox - 400, oy - 300), f"Dust ×{uv_tiling:g}",
        ),
        dust_node.inputs["Vector"],
    )
    dust_mask = _mask_channel_value(nodes, links, dust_node, (ox + 300, oy - 300))

    # AO Dust Contrast shapes the dust grain. Contrast ≈ 0 means "use the
    # texture as authored" — never force a solid white mask (that used to
    # paint every upward face brown and wash the tint out).
    span = min(max(float(contrast), 0.0), 0.95)
    if span < 0.02:
        mask_sock = dust_mask
    else:
        shaped = nodes.new("ShaderNodeMapRange")
        shaped.label = "Dust Contrast"
        shaped.clamp = True
        shaped.location = (ox + 480, oy - 300)
        shaped.inputs["From Min"].default_value = 0.5 - span * 0.5
        shaped.inputs["From Max"].default_value = 0.5 + span * 0.5 + 0.001
        shaped.inputs["To Min"].default_value = 0.0
        shaped.inputs["To Max"].default_value = 1.0
        links.new(dust_mask, shaped.inputs["Value"])
        mask_sock = shaped.outputs["Result"]

    breakup_img = load_library_image(DUST_BREAKUP_TEXTURE, local_folders)
    if breakup_img is not None and breakup_str > 0.001:
        b_node = _new_tex_image(nodes, breakup_img, "Dust Breakup", (ox, oy - 600))
        links.new(
            _world_projected_vector(
                nodes, links, max(float(breakup_tile) * 0.25, 0.02),
                (ox - 400, oy - 600), "Dust Breakup UV",
            ),
            b_node.inputs["Vector"],
        )
        b_mask = _mask_channel_value(nodes, links, b_node, (ox + 300, oy - 600))
        b_mix = _mix_float(
            nodes, links, mask_sock, b_mask, min(float(breakup_str), 1.0),
            (ox + 480, oy - 600), "Dust × Breakup",
        )
        mask_sock = b_mix

    fac_m = nodes.new("ShaderNodeMath")
    fac_m.operation = "MULTIPLY"
    fac_m.label = "Dust Amount"
    fac_m.use_clamp = True
    fac_m.location = (ox + 680, oy - 150)
    links.new(facing, fac_m.inputs[0])
    links.new(mask_sock, fac_m.inputs[1])

    # ``AO Dust Multiply`` is a gain on the mask (often 5.0). Applying it as a
    # raw mix factor clamped at 4 made any non-zero dust solid coverage.
    # Treat 5.0 as "full strength of the shaped mask" instead.
    strength = nodes.new("ShaderNodeMath")
    strength.operation = "MULTIPLY"
    strength.label = f"AO Dust Multiply ×{multiply:g}"
    strength.use_clamp = True
    strength.location = (ox + 860, oy - 150)
    links.new(fac_m.outputs["Value"], strength.inputs[0])
    strength.inputs[1].default_value = min(max(float(multiply), 0.0) / 5.0, 1.0)
    fac = strength.outputs["Value"]

    dirt = _mi_colour(
        colours, "Tint Dirt", "AO Dirt Colour", "Dust Color",
        default=(0.5, 0.5, 0.5, 1.0),
    )
    col = nodes.new("ShaderNodeRGB")
    col.label = "Dust Tint"
    col.location = (ox + 860, oy + 120)
    col.outputs[0].default_value = (
        float(dirt[0]), float(dirt[1]), float(dirt[2]), 1.0,
    )
    dusty = _mix_rgba(
        nodes, links, albedo_sock, col.outputs[0], 1.0,
        (ox + 1040, oy + 60), "Albedo × Dust", blend="MULTIPLY",
    )
    albedo_out = _mix_rgba(
        nodes, links, albedo_sock, dusty, fac, (ox + 1220, oy), "Dust Mix",
    )

    rough_out = rough_sock
    if rough_sock is not None:
        rv = nodes.new("ShaderNodeValue")
        rv.label = "Dust Roughness"
        rv.location = (ox + 1040, oy - 400)
        rv.outputs[0].default_value = 0.9
        rough_out = _mix_float(
            nodes, links, rough_sock, rv.outputs[0], fac,
            (ox + 1220, oy - 400), "Dust → Rough",
        )
    return albedo_out, rough_out, True
