"""M_TrimMap_01 painted metal: tinted paint, wear to bare metal, rust overlay.

This is the preset behind the ExtractionElevator's green ramp and frame. The MI
only supplies a neutral grey painted sheet (``T_Metal_Painted_04_A_CR`` averages
``#90908F``); everything that makes it read as weathered green metal is
parameter-driven:

* ``2. ColorTint`` multiplies the paint (normalised against the preset's mid-grey
  default so an untinted MI stays neutral)
* ``2. Wear Options`` blends ``1. Wear CR`` / ``1. Wear NOH`` — rusted bare metal
  inherited from the preset — through a wear mask
* ``2. OverlayTexture`` layers ``2. Overlay CR`` rust streaks, shaped by
  ``Overlay Range`` / ``Offset`` / ``Desaturate`` and boosted by ``Rust Strenght``
* weathering streaks and top-down dust close the chain

The preset's cooked shader graph is not in the dumps, so the blend order follows
the numbered parameter groups plus the in-game reference. Groups 2/3/4 are
vertex-blended layers; we build the first group the MI actually authored.
"""
from __future__ import annotations

import os

from ..common import (
    _find_env_tex,
    _mask_channel_value,
    _mi_colour,
    _mi_scalar,
    _mi_switch,
    _mix_float,
    _mix_normals_vec,
    _mix_rgba,
    _new_tex_image,
    _noh_ao_from_tex,
    _parse_flat_mi_json,
    _set_material_alpha_mode,
    _stamp_mi_family,
    _tex_lookup_from_flat_mi,
    _wire_normal_map,
)
from . import parent_infer, weathering

# Bump when the TrimMap graph changes so Force materials rebuilds old scenes.
# v2: tint_multiplier no longer washes near-white ColorTint to 2× brightness.
# v3: weathering coverage scaled to probe (elevator RT Base Color L2 improved).
# v4: NOH AO from Alpha.
# v5: honour ``Use Flat Base Color for RT`` as a viewport albedo wash (Blender has
#     no separate RT path; without this, grey CR reads as missing the olive metal).
# v6: wear mask drives Metallic (paint dielectric → worn bare metal); no flat
#     constant left undriven on Principled.
TRIMMAP_SETUP_V = "v6"

_LAYER_GROUPS = ("2.", "3.", "4.")


def _active_layer(mi: dict) -> str:
    """Which numbered layer group the MI authored (``2.`` unless told otherwise)."""
    names = set(mi.get("scalars") or ())
    names |= set(mi.get("switches") or ())
    names |= {p for p, _ in (mi.get("colours") or [])}
    for group in _LAYER_GROUPS:
        if any(n.startswith(group + " ") for n in names):
            return group
    return "2."


def _g(group: str, suffix: str) -> str:
    return f"{group} {suffix}"


def _tiled_vector(nodes, links, tiling: float, loc, label: str):
    uv = nodes.new("ShaderNodeTexCoord")
    uv.location = (loc[0] - 220, loc[1])
    mapping = nodes.new("ShaderNodeMapping")
    mapping.label = label
    mapping.location = loc
    scale = max(float(tiling), 0.001)
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    links.new(uv.outputs["UV"], mapping.inputs["Vector"])
    return mapping.outputs["Vector"]


def _desaturate(nodes, links, colour_sock, amount: float, loc, label: str):
    if amount <= 0.001:
        return colour_sock
    hsv = nodes.new("ShaderNodeHueSaturation")
    hsv.label = label
    hsv.location = loc
    hsv.inputs["Saturation"].default_value = max(1.0 - min(float(amount), 1.0), 0.0)
    links.new(colour_sock, hsv.inputs["Color"])
    return hsv.outputs["Color"]


def setup_trim_map_material(mat, mi_path: str, psk_path: str = ""):
    """Build the M_TrimMap_01 graph for *mat* from its MI JSON."""
    from ..classify import FAMILY_TRIMMAP

    raw_mi = _parse_flat_mi_json(mi_path)
    mi = parent_infer.merge_preset_defaults(raw_mi, mi_path)
    preset_key = str(mi.get("preset_key") or parent_infer.PRESET_TRIMMAP)

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_TRIMMAP)

    scalars = mi.get("scalars") or {}
    switches = mi.get("switches") or {}
    colours = mi.get("colours") or []
    group = _active_layer(raw_mi)

    COL_TEX, COL_UTIL, COL_MIX, COL_BSDF, COL_OUT = -1900, -1200, -450, 500, 800
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

    _, base_cr = _find_env_tex(tex_lookup, "1.  Material CR", "1. Material CR", "CR")
    _, base_noh = _find_env_tex(tex_lookup, "1.  Material NOH", "1. Material NOH", "NOH")
    _, wear_cr = _find_env_tex(tex_lookup, "1. Wear CR")
    _, wear_noh = _find_env_tex(tex_lookup, "1. Wear NOH")
    _, overlay_cr = _find_env_tex(
        tex_lookup, _g(group, "Overlay CR"), "2. Overlay CR", "3. Overlay CR",
        "4. Overlay CR",
    )

    base_tile = _mi_scalar(scalars, _g(group, "Base Material Tiling"), "Tiling", default=1.0)
    y_cr, y_n, y_wear, y_ov = 700, 200, -400, -1000

    albedo_sock = None
    rough_sock = None
    normal_sock = None
    ao_sock = None
    wear_metal_fac = None

    # ── Painted base ────────────────────────────────────────────────────────
    if base_cr is not None:
        cr_node = _new_tex_image(nodes, base_cr, "Painted CR", (COL_TEX, y_cr))
        links.new(
            _tiled_vector(nodes, links, base_tile, (COL_TEX - 350, y_cr), f"Base ×{base_tile:g}"),
            cr_node.inputs["Vector"],
        )
        albedo_sock = cr_node.outputs["Color"]
        rough_sock = cr_node.outputs["Alpha"]

    # Blender has one shading path. When the MI opts into flat RT colour, wash the
    # painted CR toward ``RT Base Color`` so the shell reads olive like the game
    # instead of mid-grey sheet metal (ColorTint on the elevator is near-white).
    if albedo_sock is not None and _mi_switch(
        switches, "Use Flat Base Color for RT", default=False,
    ):
        rt = _mi_colour(colours, "RT Base Color", default=None)
        if rt is not None and any(abs(float(c) - 1.0) > 0.02 for c in rt[:3]):
            rt_node = nodes.new("ShaderNodeRGB")
            rt_node.label = "RT Base Color (flat)"
            rt_node.location = (COL_MIX - 200, y_cr + 280)
            rt_node.outputs[0].default_value = (
                float(rt[0]), float(rt[1]), float(rt[2]), 1.0,
            )
            albedo_sock = _mix_rgba(
                nodes, links, albedo_sock, rt_node.outputs[0], 0.62,
                (COL_MIX, y_cr + 200), "CR → RT Flat Base",
            )

    if base_noh is not None:
        noh_node = _new_tex_image(
            nodes, base_noh, "Painted NOH", (COL_TEX, y_n), non_color=True,
        )
        links.new(
            _tiled_vector(nodes, links, base_tile, (COL_TEX - 350, y_n), f"Base ×{base_tile:g}"),
            noh_node.inputs["Vector"],
        )
        n_str = _mi_scalar(
            scalars, _g(group, "Material Normal Strenght"), "Normal Strength", default=1.0,
        )
        n_color, ao_sock = _noh_ao_from_tex(nodes, links, noh_node, (COL_UTIL, y_n))
        normal_sock = _wire_normal_map(
            nodes, links, n_color, (COL_MIX - 250, y_n),
            strength=min(max(n_str, 0.0), 2.5), label="Base Normal",
        )

    # ── Worn paint revealing rusted bare metal ──────────────────────────────
    wear_on = bool(_mi_switch(switches, _g(group, "Wear Options"), default=False))
    wear_amount = _mi_scalar(scalars, _g(group, "Wear Stain Amount"), default=0.0)
    wear_applied = False
    if wear_on and wear_cr is not None and albedo_sock is not None and wear_amount > 0.001:
        wear_tile = _mi_scalar(scalars, _g(group, "Wear Tiling"), default=base_tile)
        wear_node = _new_tex_image(nodes, wear_cr, "Wear CR (bare metal)", (COL_TEX, y_wear))
        links.new(
            _tiled_vector(nodes, links, wear_tile, (COL_TEX - 350, y_wear), f"Wear ×{wear_tile:g}"),
            wear_node.inputs["Vector"],
        )

        # Mask: the rusted sheet's own luminance, windowed by Wear Range Min/Max.
        raw_mask = _mask_channel_value(nodes, links, wear_node, (COL_UTIL, y_wear))
        rng_lo = _mi_scalar(scalars, _g(group, "Wear Range Min"), default=0.0)
        rng_hi = _mi_scalar(scalars, _g(group, "Wear Range Max"), default=1.0)
        window = nodes.new("ShaderNodeMapRange")
        window.label = "Wear Range"
        window.clamp = True
        window.location = (COL_UTIL + 220, y_wear)
        lo, hi = float(rng_lo), float(rng_hi)
        if hi <= lo:
            hi = lo + 0.001
        window.inputs["From Min"].default_value = lo
        window.inputs["From Max"].default_value = hi
        links.new(raw_mask, window.inputs["Value"])
        wear_fac = window.outputs["Result"]

        large_on = bool(_mi_switch(switches, _g(group, "Wear Large Scale Mask"), default=False))
        large_str = _mi_scalar(scalars, _g(group, "Wear Large Mask Strenght"), default=0.0)
        if large_on:
            large_tile = _mi_scalar(scalars, _g(group, "Wear Large Mask Tile"), default=3344.0)
            noise = nodes.new("ShaderNodeTexNoise")
            noise.label = "Wear Large Mask"
            noise.location = (COL_UTIL, y_wear - 320)
            # Authored in UE texture-space units; keep it a broad blotch in UV space.
            noise.inputs["Scale"].default_value = max(float(large_tile) / 1200.0, 0.5)
            noise.inputs["Detail"].default_value = 3.0
            wear_fac = _mix_float(
                nodes, links, wear_fac, noise.outputs["Fac"],
                min(max(float(large_str), 0.0), 1.0),
                (COL_UTIL + 420, y_wear - 160), "Wear × Large Mask",
            )

        amount = nodes.new("ShaderNodeMath")
        amount.operation = "MULTIPLY"
        amount.label = f"Wear Stain ×{wear_amount:g}"
        amount.use_clamp = True
        amount.location = (COL_UTIL + 620, y_wear)
        links.new(wear_fac, amount.inputs[0])
        amount.inputs[1].default_value = min(max(float(wear_amount), 0.0), 1.0)
        wear_fac = amount.outputs["Value"]

        albedo_sock = _mix_rgba(
            nodes, links, albedo_sock, wear_node.outputs["Color"], wear_fac,
            (COL_MIX, y_cr - 260), "Paint → Worn Metal",
        )
        if rough_sock is not None:
            rough_sock = _mix_float(
                nodes, links, rough_sock, wear_node.outputs["Alpha"], wear_fac,
                (COL_MIX, y_cr - 420), "Wear → Roughness",
            )
        if wear_noh is not None and normal_sock is not None:
            wn_node = _new_tex_image(
                nodes, wear_noh, "Wear NOH", (COL_TEX, y_wear - 620), non_color=True,
            )
            links.new(
                _tiled_vector(
                    nodes, links, wear_tile, (COL_TEX - 350, y_wear - 620), "Wear NOH UV",
                ),
                wn_node.inputs["Vector"],
            )
            wear_n_str = _mi_scalar(scalars, _g(group, "Wear Normal Strenght"), default=1.0)
            wn = _wire_normal_map(
                nodes, links, wn_node.outputs["Color"], (COL_MIX - 250, y_wear - 620),
                strength=min(max(wear_n_str, 0.0), 2.5), label="Wear Normal",
            )
            normal_sock = _mix_normals_vec(
                nodes, links, normal_sock, wn, wear_fac,
                (COL_MIX, y_n - 300), "Base ↔ Wear Normal",
            )
        wear_metal_fac = wear_fac
        wear_applied = True

    # ── Paint tint (green) — after wear so bare metal stays metal-coloured ──
    tint = parent_infer.tint_multiplier(
        mi, preset_key, _g(group, "ColorTint"), "2. ColorTint", "ColorTint",
    )
    tint_on = _mi_switch(
        switches, _g(group, "Tint"), "Use Tint", "Enable Tinting", default=True,
    )
    if albedo_sock is not None and tint_on and any(abs(c - 1.0) > 0.01 for c in tint):
        tint_node = nodes.new("ShaderNodeRGB")
        tint_node.label = "ColorTint"
        tint_node.location = (COL_MIX + 200, y_cr + 240)
        tint_node.outputs[0].default_value = (tint[0], tint[1], tint[2], 1.0)
        albedo_sock = _mix_rgba(
            nodes, links, albedo_sock, tint_node.outputs[0], 1.0,
            (COL_MIX + 400, y_cr + 120), "ColorTint × Paint", blend="MULTIPLY",
        )

    # ── Rust overlay ────────────────────────────────────────────────────────
    overlay_on = _mi_switch(
        switches, _g(group, "OverlayTexture"), _g(group, "Overlay Texture"),
        "UseOverlay", default=False,
    )
    ov_strength = _mi_scalar(scalars, _g(group, "Overlay Strength"), default=0.0)
    rust_applied = False
    if overlay_on and overlay_cr is not None and albedo_sock is not None and ov_strength > 0.001:
        ov_tile = _mi_scalar(scalars, _g(group, "OverlayTiling"), default=1.0)
        ov_node = _new_tex_image(nodes, overlay_cr, "Rust Overlay CR", (COL_TEX, y_ov))
        links.new(
            _tiled_vector(nodes, links, ov_tile, (COL_TEX - 350, y_ov), f"Overlay ×{ov_tile:g}"),
            ov_node.inputs["Vector"],
        )

        ov_range = _mi_scalar(scalars, _g(group, "Overlay Range"), default=0.5)
        ov_offset = _mi_scalar(scalars, _g(group, "Overlay Offset"), default=0.0)
        raw = _mask_channel_value(nodes, links, ov_node, (COL_UTIL, y_ov))
        shape = nodes.new("ShaderNodeMapRange")
        shape.label = "Overlay Range/Offset"
        shape.clamp = True
        shape.location = (COL_UTIL + 220, y_ov)
        half = max(float(ov_range), 0.01) * 0.5
        centre = 0.5 - float(ov_offset)
        shape.inputs["From Min"].default_value = centre - half
        shape.inputs["From Max"].default_value = centre + half
        links.new(raw, shape.inputs["Value"])
        rust_fac = shape.outputs["Result"]

        rust_str = _mi_scalar(scalars, _g(group, "Rust Strenght"), default=1.0)
        breakup_str = _mi_scalar(scalars, _g(group, "Rust Breakup Strenght"), default=0.0)
        if breakup_str > 0.001:
            breakup_tile = _mi_scalar(scalars, _g(group, "Rust Breakup Tiling"), default=0.25)
            noise = nodes.new("ShaderNodeTexNoise")
            noise.label = "Rust Breakup"
            noise.location = (COL_UTIL, y_ov - 320)
            noise.inputs["Scale"].default_value = max(float(breakup_tile) * 8.0, 0.5)
            noise.inputs["Detail"].default_value = 4.0
            rust_fac = _mix_float(
                nodes, links, rust_fac, noise.outputs["Fac"],
                min(max(float(breakup_str), 0.0), 1.0),
                (COL_UTIL + 420, y_ov - 180), "Rust × Breakup",
            )

        amount = nodes.new("ShaderNodeMath")
        amount.operation = "MULTIPLY"
        amount.label = f"Rust ×{rust_str:g}"
        amount.use_clamp = True
        amount.location = (COL_UTIL + 620, y_ov)
        links.new(rust_fac, amount.inputs[0])
        amount.inputs[1].default_value = min(
            max(float(rust_str) * min(float(ov_strength), 1.0), 0.0), 1.0,
        )
        rust_fac = amount.outputs["Value"]

        rust_col = _desaturate(
            nodes, links, ov_node.outputs["Color"],
            _mi_scalar(scalars, _g(group, "Overlay Desaturate"), default=0.0),
            (COL_UTIL + 220, y_ov + 220), "Overlay Desaturate",
        )
        if _mi_switch(switches, _g(group, "OverlayTint Color"), default=False):
            ov_tint = parent_infer.tint_multiplier(
                mi, preset_key, _g(group, "Overlay Tint Color"), "2. Overlay Tint Color",
            )
            if any(abs(c - 1.0) > 0.01 for c in ov_tint):
                ot = nodes.new("ShaderNodeRGB")
                ot.label = "Overlay Tint Color"
                ot.location = (COL_UTIL + 420, y_ov + 380)
                ot.outputs[0].default_value = (ov_tint[0], ov_tint[1], ov_tint[2], 1.0)
                rust_col = _mix_rgba(
                    nodes, links, rust_col, ot.outputs[0], 1.0,
                    (COL_UTIL + 620, y_ov + 300), "Rust × Tint", blend="MULTIPLY",
                )

        albedo_sock = _mix_rgba(
            nodes, links, albedo_sock, rust_col, rust_fac,
            (COL_MIX + 400, y_ov + 120), "Rust Overlay Mix",
        )
        ov_rough = _mi_scalar(scalars, _g(group, "Overlay Roughness"), default=0.0)
        if rough_sock is not None and ov_rough > 0.001:
            rv = nodes.new("ShaderNodeValue")
            rv.label = "Overlay Roughness"
            rv.location = (COL_MIX + 200, y_ov - 260)
            rv.outputs[0].default_value = min(float(ov_rough), 1.0)
            rough_sock = _mix_float(
                nodes, links, rough_sock, rv.outputs[0], rust_fac,
                (COL_MIX + 400, y_ov - 260), "Rust → Roughness",
            )
        rust_applied = True

    # ── AO from the packed normal ───────────────────────────────────────────
    if albedo_sock is not None and ao_sock is not None:
        ao_rgb = nodes.new("ShaderNodeCombineColor")
        ao_rgb.location = (COL_MIX + 200, y_cr - 620)
        links.new(ao_sock, ao_rgb.inputs["Red"])
        links.new(ao_sock, ao_rgb.inputs["Green"])
        links.new(ao_sock, ao_rgb.inputs["Blue"])
        ao_mul = nodes.new("ShaderNodeMix")
        ao_mul.data_type = "RGBA"
        ao_mul.blend_type = "MULTIPLY"
        ao_mul.label = "AO → Albedo"
        ao_mul.location = (COL_MIX + 400, y_cr - 560)
        ao_mul.inputs["Factor"].default_value = 0.6
        links.new(albedo_sock, ao_mul.inputs[6])
        links.new(ao_rgb.outputs["Color"], ao_mul.inputs[7])
        albedo_sock = ao_mul.outputs[2]

    # ── Weathering streaks + settled dust ───────────────────────────────────
    albedo_sock, rough_sock, weather_applied = weathering.apply_weathering(
        nodes, links, mi=mi, albedo_sock=albedo_sock, rough_sock=rough_sock,
        local_folders=local_folders, loc=(COL_TEX, -2200),
    )
    albedo_sock, rough_sock, dust_applied = weathering.apply_top_down_dust(
        nodes, links, mi=mi, albedo_sock=albedo_sock, rough_sock=rough_sock,
        local_folders=local_folders, loc=(COL_TEX, -3000),
    )

    # ── Bind ────────────────────────────────────────────────────────────────
    if albedo_sock is not None:
        links.new(albedo_sock, principled.inputs["Base Color"])
    else:
        fallback = _mi_colour(colours, "RT Base Color", default=(0.45, 0.45, 0.45, 1.0))
        principled.inputs["Base Color"].default_value = (
            float(fallback[0]), float(fallback[1]), float(fallback[2]), 1.0,
        )

    rough_scalar = _mi_scalar(scalars, _g(group, "Roughness"), "Roughness", default=-1.0)
    if rough_sock is not None:
        if rough_scalar > 0.0:
            rs = nodes.new("ShaderNodeMath")
            rs.operation = "MULTIPLY"
            rs.label = f"Roughness ×{rough_scalar:g}"
            rs.use_clamp = True
            rs.location = (COL_BSDF - 250, -300)
            links.new(rough_sock, rs.inputs[0])
            rs.inputs[1].default_value = float(rough_scalar)
            rough_sock = rs.outputs["Value"]
        links.new(rough_sock, principled.inputs["Roughness"])
    else:
        principled.inputs["Roughness"].default_value = (
            rough_scalar if rough_scalar > 0.0 else 0.6
        )

    if normal_sock is not None:
        links.new(normal_sock, principled.inputs["Normal"])

    # Painted steel: dielectric paint, metal where wear reveals bare plate.
    paint_metal = _mi_scalar(
        scalars, _g(group, "Metallic"), "Metallic", "Paint Metallic", default=0.12,
    )
    bare_metal = _mi_scalar(
        scalars, "Wear Metallic", "Bare Metallic", "1. Metallic", default=0.92,
    )
    if wear_metal_fac is not None:
        metal_mix = nodes.new("ShaderNodeMix")
        metal_mix.data_type = "FLOAT"
        metal_mix.label = "Paint → Wear Metallic"
        metal_mix.location = (COL_BSDF - 250, -520)
        metal_mix.inputs[2].default_value = min(max(float(paint_metal), 0.0), 1.0)
        metal_mix.inputs[3].default_value = min(max(float(bare_metal), 0.0), 1.0)
        links.new(wear_metal_fac, metal_mix.inputs["Factor"])
        links.new(metal_mix.outputs[0], principled.inputs["Metallic"])
    else:
        principled.inputs["Metallic"].default_value = min(
            max(float(paint_metal if not wear_applied else bare_metal), 0.0), 1.0,
        )
    _set_material_alpha_mode(mat, mode="OPAQUE", threshold=1.0, two_sided=False)

    try:
        mat["arc_trimmap_setup"] = TRIMMAP_SETUP_V
        mat["arc_trimmap_layer"] = group
        mat["arc_trimmap_preset"] = preset_key
        mat["arc_trimmap_wear"] = 1 if wear_applied else 0
        mat["arc_trimmap_rust"] = 1 if rust_applied else 0
        mat["arc_trimmap_weathering"] = 1 if weather_applied else 0
        mat["arc_trimmap_dust"] = 1 if dust_applied else 0
    except Exception:
        pass
