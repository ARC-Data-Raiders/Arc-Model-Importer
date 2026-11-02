"""
Material setup — weapon domain (split from materials.py monolith).
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

from .common import (
    _ensure_mesh_material_slot_count,
    _find_flat_tex,
    _load_image_cached,
    _match_material_slot,
    _parse_flat_mi_json,
    _parse_sk_material_slots,
    _tex_lookup_from_flat_mi,
    _unlink_input,
)
from .enemy import (
    _setup_enemy_scan_display_material,
)



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




def _new_iface_float(ng, name: str, default: float, minimum=None, maximum=None):
    sock = ng.interface.new_socket(name=name, in_out="INPUT", socket_type="NodeSocketFloat")
    try:
        sock.default_value = float(default)
    except Exception:
        pass
    if minimum is not None:
        try:
            sock.min_value = float(minimum)
        except Exception:
            pass
    if maximum is not None:
        try:
            sock.max_value = float(maximum)
        except Exception:
            pass
    return sock


def _new_iface_color(ng, name: str, default=(0.0, 0.0, 0.0, 1.0), in_out="INPUT"):
    sock = ng.interface.new_socket(name=name, in_out=in_out, socket_type="NodeSocketColor")
    try:
        sock.default_value = default
    except Exception:
        pass
    return sock


_WEAPON_TEXTURER = "WeaponTexturer"
_WEAPON_TEXTURER_VER = 3


def _weapon_texturer_is_current(ng) -> bool:
    if ng is None:
        return False
    try:
        if int(ng.get("arc_weapon_texturer_ver", 0) or 0) < _WEAPON_TEXTURER_VER:
            return False
    except Exception:
        return False
    try:
        names = {
            item.name
            for item in ng.interface.items_tree
            if getattr(item, "in_out", "") == "INPUT" and hasattr(item, "name")
        }
    except Exception:
        return False
    need = {
        "CR Color", "CR Alpha", "Normal Color", "Normal Alpha",
        "Weapon ID", "Weapon ID Alpha",
        "Base Color Red", "Base Color Green", "Base Color Blue", "Base Color Alpha",
        "Wear Color", "Wear Alpha", "Pattern Color", "Pattern ID",
        "Use Pattern R", "Use Pattern G", "Use Pattern B",
        "Pattern Color R", "Pattern Color G", "Pattern Color B",
        "Pattern Strength R", "Pattern Strength G", "Pattern Strength B",
        "Use Separate Pattern ID",
        "Wear Scuff Strength", "Wear Hotspot Strength", "Wear Roughness Strength",
    }
    # v1 sockets must be gone
    if "Specific Pattern Color" in names or "Use Pattern" in names:
        return False
    return need.issubset(names)


def _mix_rgba(nodes, links, *, label, location, factor_sock, color_a, color_b):
    """Create an RGBA MIX node; Factor drives A→B. Returns the Mix node."""
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MIX"
    mix.label = label
    mix.location = location
    links.new(factor_sock, mix.inputs["Factor"])
    links.new(color_a, mix.inputs[6])
    links.new(color_b, mix.inputs[7])
    return mix


def ensure_weapon_texturer_node_group():
    """Build WeaponTexturer v3: ID-zone Base Color tints + per-channel paintjob pattern."""
    existing = utils.find_node_group(_WEAPON_TEXTURER)
    if _weapon_texturer_is_current(existing):
        return existing

    # Stale blend copies are rebuilt in-memory; do not prefer old blend.
    if existing is not None:
        try:
            existing.name = f"{_WEAPON_TEXTURER}_stale_v{int(existing.get('arc_weapon_texturer_ver', 0) or 0)}"
        except Exception:
            try:
                existing.name = f"{_WEAPON_TEXTURER}_stale"
            except Exception:
                pass

    ng = bpy.data.node_groups.new(_WEAPON_TEXTURER, "ShaderNodeTree")
    try:
        ng["arc_weapon_texturer_ver"] = _WEAPON_TEXTURER_VER
    except Exception:
        pass

    _new_iface_color(ng, "CR Color", (0.5, 0.5, 0.5, 1.0))
    _new_iface_float(ng, "CR Alpha", 0.5, 0.0, 1.0)
    _new_iface_color(ng, "Normal Color", (0.5, 0.5, 1.0, 1.0))
    _new_iface_float(ng, "Normal Alpha", 0.0, 0.0, 1.0)
    _new_iface_color(ng, "Weapon ID", (0.0, 0.0, 0.0, 1.0))
    _new_iface_float(ng, "Weapon ID Alpha", 0.0, 0.0, 1.0)
    # Skin stencil "Base - Color - {Red,Green,Blue,Alpha}" (default white = no tint).
    white = (1.0, 1.0, 1.0, 1.0)
    _new_iface_color(ng, "Base Color Red", white)
    _new_iface_color(ng, "Base Color Green", white)
    _new_iface_color(ng, "Base Color Blue", white)
    _new_iface_color(ng, "Base Color Alpha", white)
    _new_iface_color(ng, "Wear Color", (0.5, 0.5, 0.5, 1.0))
    _new_iface_float(ng, "Wear Alpha", 0.0, 0.0, 1.0)
    _new_iface_color(ng, "Pattern Color", (1.0, 1.0, 1.0, 1.0))
    _new_iface_color(ng, "Pattern ID", (0.0, 0.0, 0.0, 1.0))
    # 0 = mask from Weapon ID; 1 = mask from Pattern ID socket (dedicated map).
    _new_iface_float(ng, "Use Separate Pattern ID", 0.0, 0.0, 1.0)

    for ch in ("R", "G", "B"):
        _new_iface_float(ng, f"Use Pattern {ch}", 0.0, 0.0, 1.0)
        _new_iface_color(ng, f"Pattern Color {ch}", (1.0, 1.0, 1.0, 1.0))
        _new_iface_float(ng, f"Pattern Strength {ch}", 1.0, 0.0, 1.0)

    _new_iface_float(ng, "Wear Scuff Strength", 0.35, 0.0, 2.0)
    _new_iface_float(ng, "Wear Hotspot Strength", 0.55, 0.0, 2.0)
    _new_iface_float(ng, "Wear Roughness Strength", 0.4, 0.0, 2.0)

    _new_iface_color(ng, "Base Color", in_out="OUTPUT")
    ng.interface.new_socket(name="Roughness", in_out="OUTPUT", socket_type="NodeSocketFloat")
    ng.interface.new_socket(name="Metallic", in_out="OUTPUT", socket_type="NodeSocketFloat")
    _new_iface_color(ng, "Normal Color Out", (0.5, 0.5, 1.0, 1.0), in_out="OUTPUT")

    nodes = ng.nodes
    links = ng.links
    gi = nodes.new("NodeGroupInput")
    gi.location = (-2200, 0)
    go = nodes.new("NodeGroupOutput")
    go.location = (1400, 0)

    # --- Weapon ID zone tints (before paintjob) ---
    # UE: mix(CR, CR * BaseColor{ch}, ID.{ch}) for Red/Green/Blue/Alpha.
    wid_sep = nodes.new("ShaderNodeSeparateColor")
    wid_sep.label = "Weapon ID (zones)"
    wid_sep.location = (-1900, 200)
    links.new(gi.outputs["Weapon ID"], wid_sep.inputs["Color"])

    zone_map = (
        ("Red", "Red", 360),
        ("Green", "Green", 160),
        ("Blue", "Blue", -40),
        ("Alpha", None, -240),  # Alpha from dedicated float socket
    )
    albedo = gi.outputs["CR Color"]
    zx = -1600
    for zone_name, sep_name, y in zone_map:
        tint = nodes.new("ShaderNodeMix")
        tint.data_type = "RGBA"
        tint.blend_type = "MULTIPLY"
        tint.inputs["Factor"].default_value = 1.0
        tint.label = f"CR x Base Color {zone_name}"
        tint.location = (zx, y)
        # UE: mix(prev, prev * BaseColor{ch}, ID.{ch})
        links.new(albedo, tint.inputs[6])
        links.new(gi.outputs[f"Base Color {zone_name}"], tint.inputs[7])

        if sep_name is not None:
            fac_sock = wid_sep.outputs[sep_name]
        else:
            fac_sock = gi.outputs["Weapon ID Alpha"]

        mix = _mix_rgba(
            nodes,
            links,
            label=f"ID zone {zone_name}",
            location=(zx + 220, y),
            factor_sock=fac_sock,
            color_a=albedo,
            color_b=tint.outputs[2],
        )
        albedo = mix.outputs[2]
        zx += 40

    # --- Effective Pattern ID ---
    # Default (Use Separate Pattern ID = 0): mask from Weapon ID.
    # Dedicated Pattern ID map: set Use Separate Pattern ID = 1 and link Pattern ID.
    id_pick = nodes.new("ShaderNodeMix")
    id_pick.data_type = "RGBA"
    id_pick.blend_type = "MIX"
    id_pick.label = "Pattern ID <- Weapon ID default"
    id_pick.location = (-1100, 420)
    links.new(gi.outputs["Use Separate Pattern ID"], id_pick.inputs["Factor"])
    links.new(gi.outputs["Weapon ID"], id_pick.inputs[6])
    links.new(gi.outputs["Pattern ID"], id_pick.inputs[7])

    id_sep = nodes.new("ShaderNodeSeparateColor")
    id_sep.label = "Pattern ID (zones)"
    id_sep.location = (-900, 420)
    links.new(id_pick.outputs[2], id_sep.inputs["Color"])

    # --- Per-channel pattern apply (R then G then B) ---
    # tinted = Pattern Texture × Pattern Color {ch}
    # fac = ID.{ch} × Use Pattern {ch} × Pattern Strength {ch}
    # albedo = mix(prev, tinted, fac)
    channel_map = (
        ("R", "Red", 200),
        ("G", "Green", 0),
        ("B", "Blue", -200),
    )
    x0 = -700
    for i, (ch, sep_name, y) in enumerate(channel_map):
        tint = nodes.new("ShaderNodeMix")
        tint.data_type = "RGBA"
        tint.blend_type = "MULTIPLY"
        tint.inputs["Factor"].default_value = 1.0
        tint.label = f"Pattern × Color {ch}"
        tint.location = (x0, y)
        links.new(gi.outputs["Pattern Color"], tint.inputs[6])
        links.new(gi.outputs[f"Pattern Color {ch}"], tint.inputs[7])

        use_str = nodes.new("ShaderNodeMath")
        use_str.operation = "MULTIPLY"
        use_str.label = f"Use × Str {ch}"
        use_str.location = (x0 + 180, y - 120)
        links.new(gi.outputs[f"Use Pattern {ch}"], use_str.inputs[0])
        links.new(gi.outputs[f"Pattern Strength {ch}"], use_str.inputs[1])

        fac = nodes.new("ShaderNodeMath")
        fac.operation = "MULTIPLY"
        fac.use_clamp = True
        fac.label = f"ID.{ch} mask"
        fac.location = (x0 + 360, y - 120)
        links.new(id_sep.outputs[sep_name], fac.inputs[0])
        links.new(use_str.outputs[0], fac.inputs[1])

        mix = _mix_rgba(
            nodes,
            links,
            label=f"Apply Pattern {ch}",
            location=(x0 + 560, y),
            factor_sock=fac.outputs[0],
            color_a=albedo,
            color_b=tint.outputs[2],
        )
        albedo = mix.outputs[2]
        x0 = x0 + 200

    # --- Wear (after pattern) ---
    wear_sep = nodes.new("ShaderNodeSeparateColor")
    wear_sep.location = (400, -420)
    links.new(gi.outputs["Wear Color"], wear_sep.inputs["Color"])

    huesat = nodes.new("ShaderNodeHueSaturation")
    huesat.label = "Wear Desat"
    huesat.location = (400, -620)
    huesat.inputs["Saturation"].default_value = 0.05
    huesat.inputs["Value"].default_value = 0.85
    links.new(gi.outputs["Wear Color"], huesat.inputs["Color"])

    wear_mask = nodes.new("ShaderNodeMapRange")
    wear_mask.label = "Wear Hotspot"
    wear_mask.clamp = True
    wear_mask.location = (620, -420)
    wear_mask.inputs["From Min"].default_value = 0.55
    wear_mask.inputs["From Max"].default_value = 0.92
    links.new(wear_sep.outputs["Red"], wear_mask.inputs["Value"])

    hot_fac = nodes.new("ShaderNodeMath")
    hot_fac.operation = "MULTIPLY"
    hot_fac.location = (820, -420)
    links.new(wear_mask.outputs["Result"], hot_fac.inputs[0])
    links.new(gi.outputs["Wear Hotspot Strength"], hot_fac.inputs[1])

    scuff_fac = nodes.new("ShaderNodeMath")
    scuff_fac.operation = "MULTIPLY"
    scuff_fac.location = (820, -560)
    links.new(hot_fac.outputs[0], scuff_fac.inputs[0])
    links.new(gi.outputs["Wear Scuff Strength"], scuff_fac.inputs[1])

    wear_mix = _mix_rgba(
        nodes,
        links,
        label="Wear -> Albedo",
        location=(1000, 40),
        factor_sock=scuff_fac.outputs[0],
        color_a=albedo,
        color_b=huesat.outputs["Color"],
    )

    rough_delta = nodes.new("ShaderNodeMath")
    rough_delta.operation = "MULTIPLY"
    rough_delta.location = (820, -200)
    links.new(gi.outputs["Wear Alpha"], rough_delta.inputs[0])
    links.new(gi.outputs["Wear Roughness Strength"], rough_delta.inputs[1])

    rough_add = nodes.new("ShaderNodeMath")
    rough_add.operation = "ADD"
    rough_add.use_clamp = True
    rough_add.location = (1000, -200)
    links.new(gi.outputs["CR Alpha"], rough_add.inputs[0])
    links.new(rough_delta.outputs[0], rough_add.inputs[1])

    links.new(wear_mix.outputs[2], go.inputs["Base Color"])
    links.new(rough_add.outputs[0], go.inputs["Roughness"])
    links.new(gi.outputs["Normal Alpha"], go.inputs["Metallic"])
    links.new(gi.outputs["Normal Color"], go.inputs["Normal Color Out"])

    return ng


def _find_weapon_texturer_instance(mat):
    tree = getattr(mat, "node_tree", None)
    if tree is None:
        return None
    for node in tree.nodes:
        ng = getattr(node, "node_tree", None)
        if ng is not None and (
            ng.name == _WEAPON_TEXTURER or ng.name.startswith(_WEAPON_TEXTURER)
        ):
            return node
    return None


def _set_group_input_default(node, name: str, value) -> bool:
    sock = node.inputs.get(name)
    if sock is None:
        return False
    try:
        sock.default_value = value
        return True
    except Exception:
        return False


def apply_pattern_to_material(mat, pattern_path: str, use_pattern: bool = True) -> bool:
    """Plug pattern texture into WeaponTexturer and enable R/G/B Use Pattern."""
    if mat is None or not pattern_path:
        return False
    ensure_weapon_texturer_node_group()
    node = _find_weapon_texturer_instance(mat)
    if node is None:
        return False
    tree = mat.node_tree
    img = _load_image_cached(pattern_path)
    if img is None:
        return False
    # Find or create Pattern image node linked to group
    pat_img_node = None
    for link in list(tree.links):
        if link.to_node == node and link.to_socket.name == "Pattern Color":
            if getattr(link.from_node, "type", "") == "TEX_IMAGE":
                pat_img_node = link.from_node
                break
    if pat_img_node is None:
        pat_img_node = tree.nodes.new("ShaderNodeTexImage")
        pat_img_node.label = "Paintjob Pattern"
        pat_img_node.location = (node.location.x - 360, node.location.y - 200)
        tree.links.new(pat_img_node.outputs["Color"], node.inputs["Pattern Color"])
    pat_img_node.image = img

    # Optional companion Pattern ID map (*_ID.png next to *_C.png, or
    # T_Weapons_Pattern_*_ID under any Content root — 2.0 Unpack often has
    # only the colour map while Steam dumps still have the ID).
    id_path = ""
    base, ext = os.path.splitext(pattern_path)
    low = base.lower()
    if low.endswith("_c"):
        stem = os.path.basename(base)
        folder = os.path.dirname(pattern_path)
        candidates = [
            stem[:-2] + "_ID" + ext,
            stem[:-2] + "_Id" + ext,
            stem[:-2] + "_id" + ext,
        ]
        core = stem[2:] if stem.lower().startswith("t_") else stem
        core = core[:-2] if core.lower().endswith("_c") else core
        candidates.append(f"T_Weapons_Pattern_{core}_ID{ext}")
        candidates.append(
            f"T_Weapons_Pattern_{core.replace('ISlands', 'Islands')}_ID{ext}"
        )
        candidates.append(
            f"T_Weapons_Pattern_{core.replace('Islands', 'ISlands')}_ID{ext}"
        )
        for name in candidates:
            cand = os.path.join(folder, name)
            if os.path.isfile(cand):
                id_path = cand
                break
            obj_guess = (
                "/Game/Pioneer/MaterialLibrary/Textures/Weapons/Patterns/"
                + os.path.splitext(name)[0]
            )
            found = textures.find_texture_from_object_path(obj_guess)
            if found and os.path.isfile(found):
                id_path = found
                break
    if id_path:
        id_img = _load_image_cached(id_path)
        if id_img is not None:
            try:
                id_img.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
            for link in list(tree.links):
                if link.to_node == node and link.to_socket.name == "Pattern ID":
                    tree.links.remove(link)
            pat_id_node = tree.nodes.new("ShaderNodeTexImage")
            pat_id_node.label = "Pattern ID"
            pat_id_node.location = (node.location.x - 360, node.location.y - 520)
            pat_id_node.image = id_img
            tree.links.new(pat_id_node.outputs["Color"], node.inputs["Pattern ID"])
            _set_group_input_default(node, "Use Separate Pattern ID", 1.0)
    else:
        _set_group_input_default(node, "Use Separate Pattern ID", 0.0)

    enabled = 1.0 if use_pattern else 0.0
    for ch in ("R", "G", "B"):
        _set_group_input_default(node, f"Use Pattern {ch}", enabled)
    return True


def clear_pattern_on_material(mat) -> bool:
    node = _find_weapon_texturer_instance(mat)
    if node is None:
        return False
    ok = False
    for ch in ("R", "G", "B"):
        if _set_group_input_default(node, f"Use Pattern {ch}", 0.0):
            ok = True
    _set_group_input_default(node, "Use Separate Pattern ID", 0.0)
    return ok


def _weapon_effective_switches(mi: dict) -> dict:
    """MI StaticSwitch values over WeaponPreset defaults (Enable * Mask = false)."""
    from . import preset_defaults

    parent = str((mi or {}).get("parent") or "")
    switches = {}
    # Walk preset for the ultimate WeaponPreset parent when known.
    for key in (
        parent,
        "M_WeaponPreset_CR+NXX+WPO+ID",
        "M_WeaponPreset_CR+NOM",
    ):
        if not key:
            continue
        presets = preset_defaults.preset_switches(key)
        if presets:
            switches.update(presets)
            break
    switches.update((mi or {}).get("switches") or {})
    return switches


def _resolve_paintjob_paths(mi: dict, tex_lookup: dict) -> tuple[str, str, list[str]]:
    """Return (pattern_png, mask_png, missing_object_path_notes)."""
    from .. import textures as _tex

    pat_path = ""
    mask_path = ""
    missing: list[str] = []
    for param, (fpath, _img) in (tex_lookup or {}).items():
        pl = str(param).lower()
        if not fpath:
            continue
        if "paintjob" in pl and "pattern" in pl and "mask" in pl:
            if os.path.isfile(fpath):
                mask_path = fpath
        elif ("paintjob" in pl and "pattern" in pl) or pl in (
            "paintjob pattern", "pattern color", "pattern",
        ):
            if os.path.isfile(fpath) and not pat_path:
                pat_path = fpath
    for param, obj_path in mi.get("textures") or []:
        pl = str(param).lower()
        is_mask = "paintjob" in pl and "pattern" in pl and "mask" in pl
        is_pat = (not is_mask) and (
            ("paintjob" in pl and "pattern" in pl)
            or pl in ("paintjob pattern", "pattern color", "pattern")
        )
        if not is_mask and not is_pat:
            continue
        found = _tex.find_texture_from_object_path(obj_path) if obj_path else ""
        if found and os.path.isfile(found):
            if is_mask and not mask_path:
                mask_path = found
            elif is_pat and not pat_path:
                pat_path = found
        else:
            leaf = (obj_path or "").rstrip("/").replace("\\", "/").split("/")[-1]
            leaf = leaf.split(".", 1)[0]
            missing.append(f"{param} ({leaf})")
    # Some dumps point Pattern Mask at the same _C map as the colour pattern.
    # That is not a zone ID — keep Weapon ID as the mask instead.
    if (
        pat_path
        and mask_path
        and os.path.normcase(os.path.normpath(pat_path))
        == os.path.normcase(os.path.normpath(mask_path))
    ):
        mask_path = ""
    # Companion ID next to colour map, plus common T_Weapons_Pattern_*_ID rename.
    if pat_path and not mask_path:
        base, ext = os.path.splitext(pat_path)
        stem = os.path.basename(base)
        folder = os.path.dirname(pat_path)
        candidates = []
        if stem.lower().endswith("_c"):
            candidates.append(stem[:-2] + "_ID" + ext)
            candidates.append(stem[:-2] + "_Id" + ext)
            # T_OrganicISlands_01_C → T_Weapons_Pattern_OrganicIslands_01_ID
            core = stem[2:] if stem.lower().startswith("t_") else stem
            core = core[:-2] if core.lower().endswith("_c") else core
            candidates.append(f"T_Weapons_Pattern_{core}_ID{ext}")
            # typo / plural variants seen in dumps
            candidates.append(
                f"T_Weapons_Pattern_{core.replace('ISlands', 'Islands')}_ID{ext}"
            )
            candidates.append(
                f"T_Weapons_Pattern_{core.replace('Islands', 'ISlands')}_ID{ext}"
            )
        for name in candidates:
            cand = os.path.join(folder, name)
            if os.path.isfile(cand):
                mask_path = cand
                break
            # Also search other Content roots for the companion ID.
            obj_guess = (
                "/Game/Pioneer/MaterialLibrary/Textures/Weapons/Patterns/"
                + os.path.splitext(name)[0]
            )
            found = _tex.find_texture_from_object_path(obj_guess)
            if found and os.path.isfile(found):
                mask_path = found
                break
    return pat_path, mask_path, missing


def _apply_mi_paintjob_pattern(
    mat, mi: dict, tex_lookup: dict, switches: dict | None = None,
) -> bool:
    """Wire Paintjob Pattern (+ optional Mask) from a skin / stencil MI."""
    if mat is None or not mi:
        return False
    pat_path, mask_path, missing = _resolve_paintjob_paths(mi, tex_lookup)
    if not pat_path:
        if missing:
            print(
                "Arc Raiders: Paintjob Pattern texture missing on disk: "
                + ", ".join(missing)
                + " (export MaterialLibrary/Textures/Weapons/Patterns from FModel, "
                "or point Pioneer at a dump that includes those PNGs)"
            )
        return False
    if not apply_pattern_to_material(mat, pat_path, use_pattern=True):
        return False
    sw = switches if switches is not None else _weapon_effective_switches(mi)
    # Honour per-channel Use Pattern switches when any are explicitly True;
    # otherwise keep all channels on (skin paintjobs often omit the switches).
    explicit = [
        bool(sw.get(f"Use Pattern - {zone}", False))
        for zone in ("Red", "Green", "Blue")
    ]
    node = _find_weapon_texturer_instance(mat)
    if node is not None and any(explicit):
        for ch, on in zip(("R", "G", "B"), explicit):
            _set_group_input_default(node, f"Use Pattern {ch}", 1.0 if on else 0.0)
    # Wire dedicated Pattern ID / mask when present.
    if mask_path and os.path.isfile(mask_path) and node is not None:
        tree = getattr(mat, "node_tree", None)
        if tree is not None:
            id_img = _load_image_cached(mask_path)
            if id_img is not None:
                try:
                    id_img.colorspace_settings.name = "Non-Color"
                except Exception:
                    pass
                for link in list(tree.links):
                    if link.to_node == node and link.to_socket.name == "Pattern ID":
                        tree.links.remove(link)
                pat_id_node = tree.nodes.new("ShaderNodeTexImage")
                pat_id_node.label = "Paintjob Pattern Mask"
                pat_id_node.location = (node.location.x - 360, node.location.y - 520)
                pat_id_node.image = id_img
                tree.links.new(pat_id_node.outputs["Color"], node.inputs["Pattern ID"])
                _set_group_input_default(node, "Use Separate Pattern ID", 1.0)
    elif missing:
        # Pattern colour applied; note missing ID mask (Weapon ID zones still used).
        print(
            "Arc Raiders: Paintjob Pattern applied without dedicated ID mask "
            f"(missing: {', '.join(missing)}; using Weapon ID zones)"
        )
    return True


def _tex_path_is_ascended(fpath_or_name: str) -> bool:
    return "ascended" in (fpath_or_name or "").lower()


def _ascended_companion_from_cr(cr_fpath: str, suffix: str, local_folders: list) -> object | None:
    """Prefer Ascended_*_<suffix> beside an Ascended CR when MI still points at base maps.

    Ascended Default/Addon meshes remapped their CR atlas; base ID/Wear on those
    UVs smear. Ascended_Base MIs keep base CR and are not handled here.
    """
    if not cr_fpath or not _tex_path_is_ascended(cr_fpath):
        return None
    stem = os.path.splitext(os.path.basename(cr_fpath))[0]
    if not stem.lower().endswith("_cr"):
        return None
    base = stem[:-3]
    candidates = [
        f"{base}_{suffix}.png",
        f"{base}_{suffix.upper()}.png",
    ]
    # T_*_Ascended_Default_Addon_A_CR → T_*_Ascended_Default_ID / _A_ID
    m = re.match(r"(.+_Ascended_Default)(?:_Addon)?(?:_[AB])?$", base, flags=re.IGNORECASE)
    if m:
        root = m.group(1)
        candidates.extend(
            [
                f"{root}_{suffix}.png",
                f"{root}_A_{suffix}.png",
                f"{root}_ID.png" if suffix.lower() == "id" else f"{root}_Wear.png",
            ]
        )
    search_dirs = list(local_folders or [])
    parent = os.path.dirname(cr_fpath)
    if parent and parent not in search_dirs:
        search_dirs.insert(0, parent)
        # Ascended/Default → Ascended
        grand = os.path.dirname(parent)
        if grand and grand not in search_dirs:
            search_dirs.append(grand)
    for folder in search_dirs:
        if not folder or not os.path.isdir(folder):
            continue
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        lower_map = {n.lower(): n for n in names}
        for cand in candidates:
            hit = lower_map.get(cand.lower())
            if hit:
                return _load_image_cached(os.path.join(folder, hit))
    return None


def _setup_weapon_main_material(mat, mi_path: str, psk_path: str):
    if any(part in mi_path.replace("\\", "/").casefold() for part in ("/items/firearms/", "/items/launchers/", "/items/weaponmod/")):
        from . import weapon_surface
        return weapon_surface.setup(mat, mi_path, psk_path)
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    COL_TEX, COL_UTIL, COL_GROUP, COL_BSDF, COL_OUT = -1400, -900, -200, 350, 650
    Y_CR, Y_N, Y_ID, Y_WEAR, Y_PAT, Y_EXTRA = 400, 50, -280, -650, -1000, -1400
    scalars = mi.get("scalars") or {}

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    wt = ensure_weapon_texturer_node_group()
    group = nodes.new("ShaderNodeGroup")
    group.node_tree = wt
    group.label = "WeaponTexturer"
    group.location = (COL_GROUP, 0)

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path)):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

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
    weapon_stem = re.sub(r"_LOD\d+$", "", psk_stem, flags=re.IGNORECASE)
    weapon_stem = re.sub(r"^SK_", "", weapon_stem, flags=re.IGNORECASE)
    weapon_stem_base = re.sub(r"_[A-Z]$", "", weapon_stem, flags=re.IGNORECASE)

    def find_weapon_stem_tex(suffix):
        for stem in (weapon_stem.lower(), weapon_stem_base.lower()):
            target = f"t_{stem}_{suffix.lower()}"
            for param, (fpath, img) in tex_lookup.items():
                if os.path.splitext(os.path.basename(fpath))[0].lower() == target:
                    return param, img
        return None, None

    _, cr_img = _find_flat_tex(tex_lookup, "CR", "cr")
    if not cr_img:
        _, cr_img = find_weapon_stem_tex("cr")
    if cr_img:
        cr_node = nodes.new("ShaderNodeTexImage")
        cr_node.image = cr_img
        cr_node.label = "CR (Colour/Roughness)"
        cr_node.interpolation = "Cubic"
        cr_node.location = (COL_TEX, Y_CR)
        links.new(cr_node.outputs["Color"], group.inputs["CR Color"])
        links.new(cr_node.outputs["Alpha"], group.inputs["CR Alpha"])

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
        nm_src = normal_node.outputs["Color"]
        if utils.ensure_node_group("NormalFlipper"):
            flipper = nodes.new("ShaderNodeGroup")
            flipper.node_tree = utils.find_node_group("NormalFlipper")
            flipper.location = (COL_UTIL, Y_N)
            links.new(normal_node.outputs["Color"], flipper.inputs[0])
            nm_src = flipper.outputs[0]
        links.new(nm_src, group.inputs["Normal Color"])
        links.new(normal_node.outputs["Alpha"], group.inputs["Normal Alpha"])

    cr_fpath = ""
    if cr_img:
        cr_fpath = getattr(cr_img, "filepath_raw", None) or getattr(cr_img, "filepath", "") or cr_img.name
        try:
            cr_fpath = bpy.path.abspath(cr_fpath) if cr_fpath else cr_img.name
        except Exception:
            cr_fpath = cr_img.name

    _, id_img = _find_flat_tex(tex_lookup, "Weapon ID", "ID", "id")
    if not id_img:
        _, id_img = find_weapon_stem_tex("id")
    # Ascended CR + base ID warps remapped Addon/Default meshes — prefer Ascended ID
    # or omit rather than smear base UV zones across the new atlas.
    if cr_fpath and _tex_path_is_ascended(cr_fpath):
        id_name = (getattr(id_img, "name", "") if id_img else "") or ""
        id_path = (getattr(id_img, "filepath", "") if id_img else "") or id_name
        if id_img and not _tex_path_is_ascended(id_path):
            asc_id = _ascended_companion_from_cr(cr_fpath, "ID", local_folders)
            id_img = asc_id  # may be None → skip base ID
    if id_img:
        id_img.colorspace_settings.name = "Non-Color"
        id_node = nodes.new("ShaderNodeTexImage")
        id_node.image = id_img
        id_node.label = "ID Map"
        id_node.interpolation = "Cubic"
        id_node.location = (COL_TEX, Y_ID)
        links.new(id_node.outputs["Color"], group.inputs["Weapon ID"])
        if "Weapon ID Alpha" in group.inputs:
            links.new(id_node.outputs["Alpha"], group.inputs["Weapon ID Alpha"])
        # Pattern ID defaults to Weapon ID until a dedicated Pattern ID map is linked.
        links.new(id_node.outputs["Color"], group.inputs["Pattern ID"])

    _, wear_img = _find_flat_tex(tex_lookup, "Wear", "wear")
    if not wear_img:
        _, wear_img = find_weapon_stem_tex("wear")
    if cr_fpath and _tex_path_is_ascended(cr_fpath):
        wear_name = (getattr(wear_img, "name", "") if wear_img else "") or ""
        wear_path = (getattr(wear_img, "filepath", "") if wear_img else "") or wear_name
        if wear_img and not _tex_path_is_ascended(wear_path):
            asc_wear = _ascended_companion_from_cr(cr_fpath, "Wear", local_folders)
            wear_img = asc_wear
    if wear_img:
        wear_img.colorspace_settings.name = "Non-Color"
        wear_node = nodes.new("ShaderNodeTexImage")
        wear_node.image = wear_img
        wear_node.label = "Wear"
        wear_node.interpolation = "Cubic"
        wear_node.location = (COL_TEX, Y_WEAR)
        links.new(wear_node.outputs["Color"], group.inputs["Wear Color"])
        links.new(wear_node.outputs["Alpha"], group.inputs["Wear Alpha"])

    # Pattern sockets always present; Use Pattern R/G/B stay 0 until Apply Pattern.
    # Pattern zone mask defaults to Weapon ID (Use Separate Pattern ID = 0).
    _set_group_input_default(group, "Use Separate Pattern ID", 0.0)
    for ch in ("R", "G", "B"):
        _set_group_input_default(group, f"Use Pattern {ch}", 0.0)
        _set_group_input_default(group, f"Pattern Strength {ch}", 1.0)

    # Resolve Enable * Mask / Use Pattern switches: MI overrides, else WeaponPreset defaults
    # (all Enable * Mask = false). Stencil Base Color R/G/B are palette slots — applying
    # them without the matching Enable switch paints guns neon orange/teal.
    switches = _weapon_effective_switches(mi)
    colour_by = {str(n): rgba for n, rgba in (mi.get("colours") or []) if n}
    white = (1.0, 1.0, 1.0, 1.0)
    for zone in ("Red", "Green", "Blue", "Alpha"):
        enabled = bool(switches.get(f"Enable {zone} Mask", False))
        rgba = colour_by.get(f"Base - Color - {zone}") if enabled else None
        _set_group_input_default(group, f"Base Color {zone}", rgba if rgba is not None else white)

    # Paintjob Pattern colours (game typo: "Patern") — only used when Use Pattern is on.
    for ch, zone in (("R", "Red"), ("G", "Green"), ("B", "Blue")):
        rgba = (
            colour_by.get(f"Paintjob Patern - {zone}")
            or colour_by.get(f"Paintjob Pattern - {zone}")
            or colour_by.get(f"Pattern Color - {zone}")
        )
        if rgba is not None:
            _set_group_input_default(group, f"Pattern Color {ch}", rgba)

    # Align wear defaults with MI scalars when present
    for sock_name, keys in (
        ("Wear Scuff Strength", ("Wear Scuff Strength", "Scuff Strength")),
        ("Wear Hotspot Strength", ("Wear Hotspot Strength", "Hotspot Strength")),
        ("Wear Roughness Strength", ("Wear Roughness Strength", "Wear Roughness")),
    ):
        for k in keys:
            if k in scalars:
                try:
                    _set_group_input_default(group, sock_name, float(scalars[k]))
                except Exception:
                    pass
                break

    links.new(group.outputs["Base Color"], principled.inputs["Base Color"])
    links.new(group.outputs["Roughness"], principled.inputs["Roughness"])
    links.new(group.outputs["Metallic"], principled.inputs["Metallic"])
    nm_node = nodes.new("ShaderNodeNormalMap")
    nm_node.location = (COL_GROUP + 280, Y_N)
    try:
        nm_node.convention = "DIRECTX"
    except Exception:
        pass
    links.new(group.outputs["Normal Color Out"], nm_node.inputs["Color"])
    links.new(nm_node.outputs["Normal"], principled.inputs["Normal"])

    # Leftover textures (EXX etc.) stay visible for inspection
    handled = {node.image.filepath for node in nodes if hasattr(node, "image") and node.image}
    row_u = Y_EXTRA
    for param, (fpath, img) in tex_lookup.items():
        if fpath in handled:
            continue
        pl = str(param).lower()
        # Applied below via _apply_mi_paintjob_pattern; skip duplicate image nodes.
        if "paintjob" in pl and "pattern" in pl:
            continue
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param
        node.interpolation = "Cubic"
        node.location = (COL_TEX - 400, row_u)
        row_u -= 320

    # Optional EXX still applied after group for emissive/soot when present
    _, ex_img = _find_flat_tex(tex_lookup, "EX", "EXX", "ex", "exx")
    if not ex_img:
        _, ex_img = find_weapon_stem_tex("exx")
    if not ex_img:
        _, ex_img = find_weapon_stem_tex("ex")
    if ex_img:
        _apply_exx_packing(
            nodes, links, principled, ex_img, scalars,
            col_tex=COL_TEX, col_util=COL_UTIL, col_mix=COL_GROUP, row_y=Y_PAT,
        )

    # Auto-apply Paintjob Pattern only when the MI authors a pattern map.
    # Channel enables follow Use Pattern - R/G/B switches (default false → all on
    # when a pattern texture is present, matching skin paintjobs that omit switches).
    _apply_mi_paintjob_pattern(mat, mi, tex_lookup, switches=switches)


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



def _setup_weapon_screen_material(mat, mi_path: str):
    """Weapon screens + enemy ScanDisplay share the procedural scan setup."""
    _setup_enemy_scan_display_material(mat, mi_path)



def setup_weapon_material(obj, psk_path: str, skin_json: str = "") -> int:
    if any(part in psk_path.replace("\\", "/").casefold() for part in ("/items/firearms/", "/items/launchers/", "/items/weaponmod/")):
        from pathlib import Path
        from .. import weapon_import
        return weapon_import._material(obj, Path(psk_path), {}, skin_json)
    """Assign per-slot materials from SK/SM SkeletalMaterials / StaticMaterials.

    Used for firearms and hero/character SKs (e.g. Kalika Base Body) whose MIs
    live beside the mesh via ObjectPath / Materials/ siblings. Arc enemies use
    ``setup_enemy_material`` (same slot wiring, explicit domain entrypoint).

    When *skin_json* points at a Firearms ``Skins/*/MI_*.json``, weapon-family
    slots use that MI (CR/ID/Wear + stencil Base Color / Paintjob Pattern).

    Returns the number of Blender slots successfully wired.
    """
    from .classify import FAMILY_WEAPON, classify_mi_family
    from .dispatch import _get_or_build_shared_mi_material

    skin_path = ""
    if skin_json and skin_json not in ("NONE", "none"):
        cand = os.path.abspath(skin_json)
        if os.path.isfile(cand):
            skin_path = cand

    slots = _parse_sk_material_slots(psk_path)
    # 2.0 firearm SK dumps often have SkeletalMaterials: null. Sibling SM JSON
    # usually still has StaticMaterials; when that also fails, synthesize one
    # slot from the chosen skin MI or folder MI_*_A.json.
    if not slots or not any(mi for _n, _s, mi in slots):
        folder_mi = _fallback_weapon_folder_mi(psk_path, skin_path)
        if folder_mi:
            stem = os.path.splitext(os.path.basename(folder_mi))[0]
            slots = [(stem, stem, folder_mi)]
            print(
                f"Arc Raiders PSK Importer: SK/SM slots empty for "
                f"'{os.path.basename(psk_path)}'; using folder MI '{stem}'"
            )
    if not slots:
        print(f"Arc Raiders PSK Importer: No SK material slots found for '{os.path.basename(psk_path)}'")
        return 0

    # PSK import uses should_import_materials=False (blank placeholder mats are
    # useless), but face material_index values still reference SK slot order.
    # Without growing mesh.materials, index fallback matches nothing and enemies
    # / weapons land with zero textures (Pop, Wasp/LightDrone, etc.).
    added = _ensure_mesh_material_slot_count(obj, len(slots))
    if added:
        print(
            f"Arc Raiders PSK Importer: Created {added} material slot(s) on "
            f"'{getattr(obj, 'name', '?')}' for SK/SM materials"
        )

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

        use_path = mi_path
        use_stem = mi_stem
        if skin_path:
            try:
                probe = _parse_flat_mi_json(mi_path)
                fam = classify_mi_family(probe, (mi_stem or "").lower(), slot_lower)
            except Exception:
                fam = ""
            # Replace weapon surfaces with the chosen Skins/*/MI_*. Keep emissive/scan slots.
            if fam == FAMILY_WEAPON:
                use_path = skin_path
                use_stem = os.path.splitext(os.path.basename(skin_path))[0]

        mat = _get_or_build_shared_mi_material(use_stem, use_path, psk_path, slot_lower)
        if mat is None:
            print(
                "Arc Raiders PSK Importer: Failed to set up material for slot "
                f"'{slot_name}' (index {idx}, MI '{use_stem}', path '{use_path}')"
            )
            continue
        target_slot.material = mat
        if skin_path and use_path == skin_path:
            try:
                mat["arc_skin_json"] = skin_path
                obj["arc_skin_json"] = skin_path
                obj["arc_skin_choice"] = skin_path
            except Exception:
                pass
        wired += 1
    return wired


def _fallback_weapon_folder_mi(psk_path: str, skin_path: str = "") -> str:
    """Pick a usable MI JSON when SK/SM material arrays are null (2.0 dumps)."""
    if skin_path and os.path.isfile(skin_path):
        return skin_path
    folder = os.path.dirname(psk_path or "")
    if not folder or not os.path.isdir(folder):
        return ""
    psk_stem = os.path.splitext(os.path.basename(psk_path or ""))[0]
    bare = re.sub(r"^(SK_|SM_)", "", psk_stem, flags=re.IGNORECASE)
    bare = re.sub(r"_LOD\d+$", "", bare, flags=re.IGNORECASE)
    candidates = [
        f"MI_{bare}.json",
        f"MI_{psk_stem}.json",
    ]
    # Prefer exact letter suffix match (…_A), then any MI_* matching bare stem.
    exact = os.path.join(folder, f"MI_{bare}.json")
    if os.path.isfile(exact):
        return exact
    try:
        for fname in sorted(os.listdir(folder)):
            fl = fname.lower()
            if not fl.startswith("mi_") or not fl.endswith(".json"):
                continue
            if "skeleton" in fl:
                continue
            if bare.lower() in fl.replace("-", "_"):
                return os.path.join(folder, fname)
    except OSError:
        pass
    for name in candidates:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return ""

