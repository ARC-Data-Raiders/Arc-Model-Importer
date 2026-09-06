"""
Material setup — enemy domain (split from materials.py monolith).
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
    _find_flat_tex,
    _mix_float,
    _mix_rgba,
    _parse_flat_mi_json,
    _set_material_clip,
    _tex_lookup_from_flat_mi,
)



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



# Procedural enemy/weapon ScanDisplay group (M_EnemyPreset_ScanDisplay family).
_SCAN_DISPLAY_GROUP = "Arc_EnemyScanDisplay"

_SCAN_DISPLAY_GROUP_VER = 1

_SCAN_DISPLAY_INPUTS = (
    "Tiling",
    "ScanSpeed",
    "AlertnessState",
    "DamagePercent",
    "GlitchAmount",
    "GlitchSpeed",
    "Blink",
    "Loop",
    "Glitch Effect",
    "Frame",
)



def _scan_display_led_tiling(scalars: dict) -> float:
    """LED grid density from MI scalars. Parent default Tiling=64; Pop Pulse uses 256."""
    for name in (
        "Tiling", "LEDWidth", "Columns", "TilingX", "ResolutionX", "ScanColumns",
    ):
        if name in scalars and scalars[name] is not None:
            try:
                val = float(scalars[name])
            except (TypeError, ValueError):
                continue
            if val > 0.0:
                return max(val, 1.0)
    return 64.0



def _drive_scan_frame_value(value_node):
    """Drive a Value node from Scene.frame_current (works with Auto Run Scripts off)."""
    value_node.outputs[0].default_value = 0.0
    try:
        value_node.outputs[0].driver_remove("default_value")
    except (TypeError, AttributeError):
        pass

    fcurve = value_node.outputs[0].driver_add("default_value")
    driver = fcurve.driver
    driver.type = "SCRIPTED"
    driver.expression = "frame"
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
    try:
        value_node.outputs[0].default_value = float(scene.frame_current)
    except Exception:
        pass
    try:
        value_node.id_data.update_tag()
    except Exception:
        pass



def _scan_display_group_is_current(ng) -> bool:
    if ng is None:
        return False
    try:
        if int(ng.get("arc_scan_display_ver", 0) or 0) < _SCAN_DISPLAY_GROUP_VER:
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
    return set(_SCAN_DISPLAY_INPUTS).issubset(names)



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



def ensure_arc_enemy_scan_display_node_group():
    """Build ``Arc_EnemyScanDisplay``: procedural emissive LED scan (LinearGradient/Remap).

    Group inputs mirror cooked MI scalars/switches on M_EnemyPreset_ScanDisplay.
    AlertnessState stays exposed so AIStateStyleDriver-style runtime can be mocked in Blender.
    """
    existing = utils.find_node_group(_SCAN_DISPLAY_GROUP)
    if _scan_display_group_is_current(existing):
        return existing
    if existing is not None:
        try:
            existing.name = f"{_SCAN_DISPLAY_GROUP}_stale"
        except Exception:
            pass

    ng = bpy.data.node_groups.new(_SCAN_DISPLAY_GROUP, "ShaderNodeTree")
    try:
        ng["arc_scan_display_ver"] = _SCAN_DISPLAY_GROUP_VER
    except Exception:
        pass

    _new_iface_float(ng, "Tiling", 64.0, 1.0, 1024.0)
    _new_iface_float(ng, "ScanSpeed", 0.5, 0.0, 8.0)
    _new_iface_float(ng, "AlertnessState", 0.0, 0.0, 1.0)
    _new_iface_float(ng, "DamagePercent", 0.0, 0.0, 1.0)
    _new_iface_float(ng, "GlitchAmount", 0.0, 0.0, 4.0)
    _new_iface_float(ng, "GlitchSpeed", 1.0, 0.0, 16.0)
    _new_iface_float(ng, "Blink", 0.0, 0.0, 1.0)
    _new_iface_float(ng, "Loop", 0.0, 0.0, 1.0)
    _new_iface_float(ng, "Glitch Effect", 0.0, 0.0, 1.0)
    _new_iface_float(ng, "Frame", 0.0)
    ng.interface.new_socket(name="Emission Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    ng.interface.new_socket(name="Emission Strength", in_out="OUTPUT", socket_type="NodeSocketFloat")

    nodes = ng.nodes
    links = ng.links
    gi = nodes.new("NodeGroupInput")
    gi.location = (-1600.0, 40.0)
    go = nodes.new("NodeGroupOutput")
    go.location = (1280.0, 80.0)

    def _math(op, loc, label="", clamp=False):
        n = nodes.new("ShaderNodeMath")
        n.operation = op
        n.location = loc
        if label:
            n.label = label
        try:
            n.use_clamp = clamp
        except Exception:
            pass
        return n

    def _mix_float(loc, label=""):
        n = nodes.new("ShaderNodeMix")
        n.data_type = "FLOAT"
        n.location = loc
        if label:
            n.label = label
        return n

    def _mix_rgba(loc, label="", blend="MIX"):
        n = nodes.new("ShaderNodeMix")
        n.data_type = "RGBA"
        n.blend_type = blend
        n.location = loc
        if label:
            n.label = label
        return n

    def _link_mix_a(mix_n, sock):
        # Blender 4+/5 Mix: named A/B when present, else color sockets 6/7.
        if "A" in mix_n.inputs:
            links.new(sock, mix_n.inputs["A"])
        else:
            links.new(sock, mix_n.inputs[6])

    def _link_mix_b(mix_n, sock):
        if "B" in mix_n.inputs:
            links.new(sock, mix_n.inputs["B"])
        else:
            links.new(sock, mix_n.inputs[7])

    def _mix_out(mix_n):
        return mix_n.outputs.get("Result") or mix_n.outputs[2]

    # ── UV → LED grid (Tiling) ─────────────────────────────────────────
    texcoord = nodes.new("ShaderNodeTexCoord")
    texcoord.location = (-1600.0, 320.0)

    combine_scale = nodes.new("ShaderNodeCombineXYZ")
    combine_scale.label = "Tiling XYZ"
    combine_scale.location = (-1320.0, 420.0)
    links.new(gi.outputs["Tiling"], combine_scale.inputs["X"])
    links.new(gi.outputs["Tiling"], combine_scale.inputs["Y"])
    combine_scale.inputs["Z"].default_value = 1.0

    led_map = nodes.new("ShaderNodeMapping")
    led_map.label = "LED Scale"
    led_map.location = (-1100.0, 320.0)
    links.new(texcoord.outputs["UV"], led_map.inputs["Vector"])
    links.new(combine_scale.outputs["Vector"], led_map.inputs["Scale"])

    snap = nodes.new("ShaderNodeVectorMath")
    snap.operation = "SNAP"
    snap.label = "LED Snap"
    snap.location = (-860.0, 360.0)
    snap.inputs[1].default_value = (1.0, 1.0, 1.0)
    links.new(led_map.outputs["Vector"], snap.inputs[0])

    sep = nodes.new("ShaderNodeSeparateXYZ")
    sep.label = "LED XY"
    sep.location = (-640.0, 360.0)
    links.new(snap.outputs["Vector"], sep.inputs["Vector"])

    sep_raw = nodes.new("ShaderNodeSeparateXYZ")
    sep_raw.label = "Cell UV"
    sep_raw.location = (-640.0, 160.0)
    links.new(led_map.outputs["Vector"], sep_raw.inputs["Vector"])

    # ── Scan phase: ping-pong vs Loop (LinearGradient time) ────────────
    # time = Frame * ScanSpeed * 0.0375 (viewport-readable rate)
    spd_mul = _math("MULTIPLY", (-1320.0, -40.0), "Speed×k")
    spd_mul.inputs[1].default_value = 0.0375
    links.new(gi.outputs["ScanSpeed"], spd_mul.inputs[0])

    time_n = _math("MULTIPLY", (-1100.0, -40.0), "Time")
    links.new(gi.outputs["Frame"], time_n.inputs[0])
    links.new(spd_mul.outputs[0], time_n.inputs[1])

    mod2 = _math("MODULO", (-860.0, 40.0), "Time%2")
    mod2.inputs[1].default_value = 2.0
    links.new(time_n.outputs[0], mod2.inputs[0])

    sub1 = _math("SUBTRACT", (-640.0, 40.0), "%2−1")
    sub1.inputs[1].default_value = 1.0
    links.new(mod2.outputs[0], sub1.inputs[0])

    ping = _math("ABSOLUTE", (-420.0, 40.0), "PingPong")
    links.new(sub1.outputs[0], ping.inputs[0])

    loop_pos = nodes.new("ShaderNodeMath")
    loop_pos.label = "Loop Fract"
    loop_pos.location = (-640.0, -140.0)
    try:
        loop_pos.operation = "FRACT"
    except Exception:
        loop_pos.operation = "MODULO"
        loop_pos.inputs[1].default_value = 1.0
    links.new(time_n.outputs[0], loop_pos.inputs[0])

    scan_pos = _mix_float((-420.0, -80.0), "Loop Mix")
    links.new(gi.outputs["Loop"], scan_pos.inputs["Factor"])
    _link_mix_a(scan_pos, ping.outputs[0])
    _link_mix_b(scan_pos, loop_pos.outputs[0])

    # Glitch: offset scan position with 4D noise when Glitch Effect is on
    noise = nodes.new("ShaderNodeTexNoise")
    noise.label = "Glitch Noise"
    noise.location = (-860.0, -360.0)
    try:
        noise.noise_dimensions = "4D"
    except Exception:
        pass
    noise.inputs["Scale"].default_value = 12.0
    noise.inputs["Detail"].default_value = 2.0
    links.new(texcoord.outputs["UV"], noise.inputs["Vector"])
    g_w = _math("MULTIPLY", (-1100.0, -280.0), "Glitch W")
    g_w.inputs[1].default_value = 0.12
    links.new(gi.outputs["GlitchSpeed"], g_w.inputs[0])
    g_frame = _math("MULTIPLY", (-860.0, -280.0), "Frame×GSpeed")
    links.new(gi.outputs["Frame"], g_frame.inputs[0])
    links.new(g_w.outputs[0], g_frame.inputs[1])
    if "W" in noise.inputs:
        links.new(g_frame.outputs[0], noise.inputs["W"])

    n_centered = _math("SUBTRACT", (-640.0, -360.0), "Noise−0.5")
    n_centered.inputs[1].default_value = 0.5
    links.new(noise.outputs["Fac"], n_centered.inputs[0])

    g_amt = _math("MULTIPLY", (-420.0, -360.0), "×GlitchAmt")
    links.new(n_centered.outputs[0], g_amt.inputs[0])
    links.new(gi.outputs["GlitchAmount"], g_amt.inputs[1])

    g_gate = _math("MULTIPLY", (-220.0, -360.0), "×GlitchFX")
    links.new(g_amt.outputs[0], g_gate.inputs[0])
    links.new(gi.outputs["Glitch Effect"], g_gate.inputs[1])

    scan_glitched = _math("ADD", (-220.0, -80.0), "Scan+Glitch")
    links.new(_mix_out(scan_pos), scan_glitched.inputs[0])
    links.new(g_gate.outputs[0], scan_glitched.inputs[1])

    # scan_col = scan_pos * (Tiling - 1)
    tiling_m1 = _math("SUBTRACT", (-220.0, 200.0), "Tiling−1")
    tiling_m1.inputs[1].default_value = 1.0
    links.new(gi.outputs["Tiling"], tiling_m1.inputs[0])

    scan_col = _math("MULTIPLY", (0.0, 40.0), "Scan Col")
    links.new(scan_glitched.outputs[0], scan_col.inputs[0])
    links.new(tiling_m1.outputs[0], scan_col.inputs[1])

    # Remap-style band: |col − scan| → 1‥0 over ~4 LED columns
    diff = _math("SUBTRACT", (0.0, 220.0), "Col−Scan")
    links.new(sep.outputs["X"], diff.inputs[0])
    links.new(scan_col.outputs[0], diff.inputs[1])

    adiff = _math("ABSOLUTE", (200.0, 220.0), "|ΔCol|")
    links.new(diff.outputs[0], adiff.inputs[0])

    band = nodes.new("ShaderNodeMapRange")
    band.label = "Scan Band"
    band.clamp = True
    band.location = (420.0, 220.0)
    band.inputs["From Min"].default_value = 0.0
    band.inputs["From Max"].default_value = 4.0
    band.inputs["To Min"].default_value = 1.0
    band.inputs["To Max"].default_value = 0.0
    links.new(adiff.outputs[0], band.inputs["Value"])

    # LED mortar windows on fract(U/V)
    fract_u = _math("FRACT", (0.0, -40.0), "Fract U")
    try:
        fract_u.operation = "FRACT"
    except Exception:
        fract_u.operation = "MODULO"
        fract_u.inputs[1].default_value = 1.0
    links.new(sep_raw.outputs["X"], fract_u.inputs[0])

    fract_v = _math("FRACT", (0.0, -200.0), "Fract V")
    try:
        fract_v.operation = "FRACT"
    except Exception:
        fract_v.operation = "MODULO"
        fract_v.inputs[1].default_value = 1.0
    links.new(sep_raw.outputs["Y"], fract_v.inputs[0])

    ramp_u = nodes.new("ShaderNodeValToRGB")
    ramp_u.label = "LED U"
    ramp_u.location = (220.0, -40.0)
    ramp_u.color_ramp.interpolation = "CONSTANT"
    ramp_u.color_ramp.elements[0].position = 0.0
    ramp_u.color_ramp.elements[0].color = (0, 0, 0, 1)
    ramp_u.color_ramp.elements[1].position = 0.88
    ramp_u.color_ramp.elements[1].color = (0, 0, 0, 1)
    el_u = ramp_u.color_ramp.elements.new(0.12)
    el_u.color = (1, 1, 1, 1)
    links.new(fract_u.outputs[0], ramp_u.inputs["Fac"])

    ramp_v = nodes.new("ShaderNodeValToRGB")
    ramp_v.label = "LED V"
    ramp_v.location = (220.0, -260.0)
    ramp_v.color_ramp.interpolation = "CONSTANT"
    ramp_v.color_ramp.elements[0].position = 0.0
    ramp_v.color_ramp.elements[0].color = (0, 0, 0, 1)
    ramp_v.color_ramp.elements[1].position = 0.88
    ramp_v.color_ramp.elements[1].color = (0, 0, 0, 1)
    el_v = ramp_v.color_ramp.elements.new(0.12)
    el_v.color = (1, 1, 1, 1)
    links.new(fract_v.outputs[0], ramp_v.inputs["Fac"])

    led = _mix_rgba((460.0, -120.0), "LED Mask", blend="MULTIPLY")
    led.inputs["Factor"].default_value = 1.0
    _link_mix_a(led, ramp_u.outputs["Color"])
    _link_mix_b(led, ramp_v.outputs["Color"])

    rgb2bw = nodes.new("ShaderNodeRGBToBW")
    rgb2bw.label = "LED BW"
    rgb2bw.location = (680.0, -120.0)
    links.new(_mix_out(led), rgb2bw.inputs["Color"])

    lit = _math("MULTIPLY", (680.0, 120.0), "Scan×LED")
    links.new(band.outputs["Result"], lit.inputs[0])
    links.new(rgb2bw.outputs["Val"], lit.inputs[1])

    # DamagePercent → LED dropout (noise kill)
    dmg_noise = nodes.new("ShaderNodeTexNoise")
    dmg_noise.label = "Damage Noise"
    dmg_noise.location = (420.0, -420.0)
    dmg_noise.inputs["Scale"].default_value = 48.0
    links.new(led_map.outputs["Vector"], dmg_noise.inputs["Vector"])

    alive_thr = _math("SUBTRACT", (640.0, -360.0), "1−Damage")
    alive_thr.inputs[0].default_value = 1.0
    links.new(gi.outputs["DamagePercent"], alive_thr.inputs[1])

    alive = _math("GREATER_THAN", (820.0, -360.0), "Alive?")
    links.new(dmg_noise.outputs["Fac"], alive.inputs[0])
    links.new(alive_thr.outputs[0], alive.inputs[1])

    # Soften: when Damage≈0, keep all LEDs (mix toward 1)
    dmg_soft = _mix_float((820.0, -200.0), "Damage Soft")
    links.new(gi.outputs["DamagePercent"], dmg_soft.inputs["Factor"])
    one_val = _math("ADD", (640.0, -200.0), "One")
    one_val.inputs[0].default_value = 1.0
    one_val.inputs[1].default_value = 0.0
    _link_mix_a(dmg_soft, one_val.outputs[0])
    _link_mix_b(dmg_soft, alive.outputs[0])

    lit_dmg = _math("MULTIPLY", (980.0, 80.0), "Lit×Damage")
    links.new(lit.outputs[0], lit_dmg.inputs[0])
    links.new(_mix_out(dmg_soft), lit_dmg.inputs[1])

    # Alertness tint: calm cyan → alert orange
    calm = nodes.new("ShaderNodeRGB")
    calm.label = "Calm"
    calm.location = (680.0, 360.0)
    calm.outputs[0].default_value = (0.15, 0.85, 1.0, 1.0)

    alert_c = nodes.new("ShaderNodeRGB")
    alert_c.label = "Alert"
    alert_c.location = (680.0, 240.0)
    alert_c.outputs[0].default_value = (1.0, 0.28, 0.05, 1.0)

    tint = _mix_rgba((900.0, 300.0), "Alert Tint")
    links.new(gi.outputs["AlertnessState"], tint.inputs["Factor"])
    _link_mix_a(tint, calm.outputs[0])
    _link_mix_b(tint, alert_c.outputs[0])

    scan_rgb = nodes.new("ShaderNodeCombineColor")
    scan_rgb.label = "Scan Mask RGB"
    scan_rgb.location = (980.0, -40.0)
    links.new(lit_dmg.outputs[0], scan_rgb.inputs["Red"])
    links.new(lit_dmg.outputs[0], scan_rgb.inputs["Green"])
    links.new(lit_dmg.outputs[0], scan_rgb.inputs["Blue"])

    emit_col = _mix_rgba((1120.0, 200.0), "Tint×Scan", blend="MULTIPLY")
    emit_col.inputs["Factor"].default_value = 1.0
    _link_mix_a(emit_col, _mix_out(tint))
    _link_mix_b(emit_col, scan_rgb.outputs["Color"])

    # Strength: base + alert; Blink pulses; Glitch flickers
    base_str = _math("MULTIPLY", (900.0, -520.0), "Alert×10")
    base_str.inputs[1].default_value = 10.0
    links.new(gi.outputs["AlertnessState"], base_str.inputs[0])

    str0 = _math("ADD", (1080.0, -520.0), "Base Str")
    str0.inputs[0].default_value = 14.0
    links.new(base_str.outputs[0], str0.inputs[1])

    # Blink pulse (ping-pong), gated by Blink switch
    blink_spd = _math("MULTIPLY", (420.0, -560.0), "Blink Spd")
    blink_spd.inputs[1].default_value = 0.12
    links.new(gi.outputs["Frame"], blink_spd.inputs[0])

    blink_mod = _math("MODULO", (600.0, -560.0), "Blink%2")
    blink_mod.inputs[1].default_value = 2.0
    links.new(blink_spd.outputs[0], blink_mod.inputs[0])

    blink_sub = _math("SUBTRACT", (780.0, -560.0), "Blink−1")
    blink_sub.inputs[1].default_value = 1.0
    links.new(blink_mod.outputs[0], blink_sub.inputs[0])

    blink_wave = _math("ABSOLUTE", (960.0, -560.0), "Blink Wave")
    links.new(blink_sub.outputs[0], blink_wave.inputs[0])

    # Keep a visible floor so Pulse doesn't fully black out
    blink_floor = _math("MULTIPLY", (1140.0, -620.0), "Wave×0.85")
    blink_floor.inputs[1].default_value = 0.85
    links.new(blink_wave.outputs[0], blink_floor.inputs[0])

    blink_lift = _math("ADD", (1140.0, -500.0), "Blink+0.15")
    blink_lift.inputs[1].default_value = 0.15
    links.new(blink_floor.outputs[0], blink_lift.inputs[0])

    blink_mix = _mix_float((1080.0, -400.0), "Blink Gate")
    links.new(gi.outputs["Blink"], blink_mix.inputs["Factor"])
    _link_mix_a(blink_mix, one_val.outputs[0])
    _link_mix_b(blink_mix, blink_lift.outputs[0])

    str_blink = _math("MULTIPLY", (1120.0, -280.0), "Str×Blink")
    links.new(str0.outputs[0], str_blink.inputs[0])
    links.new(_mix_out(blink_mix), str_blink.inputs[1])

    # Glitch flicker on strength
    flick = _math("MULTIPLY", (900.0, -680.0), "Flicker")
    links.new(noise.outputs["Fac"], flick.inputs[0])
    links.new(gi.outputs["GlitchAmount"], flick.inputs[1])

    flick_amt = _math("MULTIPLY", (1080.0, -680.0), "Flicker×FX")
    flick_amt.inputs[1].default_value = 0.35
    links.new(flick.outputs[0], flick_amt.inputs[0])
    # reuse: multiply by Glitch Effect via extra node
    flick_gate = _math("MULTIPLY", (1080.0, -760.0), "Flicker Gate")
    links.new(flick_amt.outputs[0], flick_gate.inputs[0])
    links.new(gi.outputs["Glitch Effect"], flick_gate.inputs[1])

    str_glitch = _math("ADD", (1200.0, -320.0), "Str+Glitch")
    links.new(str_blink.outputs[0], str_glitch.inputs[0])
    links.new(flick_gate.outputs[0], str_glitch.inputs[1])

    links.new(_mix_out(emit_col), go.inputs["Emission Color"])
    links.new(str_glitch.outputs[0], go.inputs["Emission Strength"])

    stale = utils.find_node_group(f"{_SCAN_DISPLAY_GROUP}_stale")
    if stale is not None and stale.users == 0:
        try:
            bpy.data.node_groups.remove(stale)
        except Exception:
            pass
    return utils.find_node_group(_SCAN_DISPLAY_GROUP)



def _set_scan_display_group_defaults(group_node, *, tiling, scan_speed, alert,
                                     damage, glitch_amt, glitch_speed,
                                     blink, loop, glitch_fx):
    """Stamp MI scalars/switches onto an Arc_EnemyScanDisplay group instance."""
    values = {
        "Tiling": float(tiling),
        "ScanSpeed": float(scan_speed),
        "AlertnessState": float(alert),
        "DamagePercent": float(damage),
        "GlitchAmount": float(glitch_amt),
        "GlitchSpeed": float(glitch_speed),
        "Blink": 1.0 if blink else 0.0,
        "Loop": 1.0 if loop else 0.0,
        "Glitch Effect": 1.0 if glitch_fx else 0.0,
    }
    for name, val in values.items():
        sock = group_node.inputs.get(name)
        if sock is None:
            continue
        try:
            sock.default_value = val
        except Exception:
            pass



def _is_enemy_scan_display_mi(mi: dict, mi_stem_lower: str = "", slot_lower: str = "") -> bool:
    """True for MI_EnemyScanDisplay_* / ScanDisplay slots / M_EnemyPreset_ScanDisplay kids."""
    stem = (mi_stem_lower or "").lower().replace(" ", "")
    slot = (slot_lower or "").lower().replace(" ", "")
    parent = str((mi or {}).get("parent") or "").lower().replace(" ", "")
    if "scandisplay" in stem or "scandisplay" in slot:
        return True
    if "enemypreset_scandisplay" in parent or parent.endswith("m_enemypreset_scandisplay"):
        return True
    if "m_enemypreset_scandisplay" in parent:
        return True
    return False



def _setup_enemy_scan_display_material(mat, mi_path: str):
    """Wire Arc_EnemyScanDisplay from MI_EnemyScanDisplay_* / parent preset JSON."""
    mi = _parse_flat_mi_json(mi_path)
    ng = ensure_arc_enemy_scan_display_node_group()
    if ng is None:
        print("Arc Raiders PSK Importer: Arc_EnemyScanDisplay node group unavailable")
        return

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    scalars = mi.get("scalars") or {}
    switches = mi.get("switches") or {}

    def _sf(*names, default=0.0):
        for name in names:
            if name in scalars and scalars[name] is not None:
                try:
                    return float(scalars[name])
                except (TypeError, ValueError):
                    continue
        return float(default)

    def _sw(*names, default=False):
        for name in names:
            if name in switches:
                return bool(switches[name])
        return bool(default)

    tiling = _scan_display_led_tiling(scalars)
    scan_speed = _sf("ScanSpeed", default=0.5)
    alert = min(max(_sf("AlertnessState", default=0.0), 0.0), 1.0)
    damage = min(max(_sf("DamagePercent", default=0.0), 0.0), 1.0)
    glitch_amt = _sf("GlitchAmount", default=0.0)
    glitch_speed = _sf("GlitchSpeed", default=1.0)
    do_blink = _sw("Blink", default=False)
    do_loop = _sw("Loop", default=False)
    do_glitch = _sw("Glitch Effect", "Glitch", default=False)
    # Stem heuristics when JSON omitted switches (Pulse / Loop / Glitch MIs)
    stem_l = os.path.splitext(os.path.basename(mi_path or ""))[0].lower()
    if "pulse" in stem_l:
        do_blink = True
    if "_loop" in stem_l or stem_l.endswith("loop_a"):
        do_loop = True
    if "glitch" in stem_l:
        do_glitch = True
        if glitch_amt <= 0.0:
            glitch_amt = 1.0

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (360.0, 40.0)
    principled.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    principled.inputs["Metallic"].default_value = 0.0
    principled.inputs["Roughness"].default_value = 0.35

    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (640.0, 40.0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    group_node = nodes.new("ShaderNodeGroup")
    group_node.node_tree = ng
    group_node.name = _SCAN_DISPLAY_GROUP
    group_node.label = "Enemy ScanDisplay"
    group_node.location = (40.0, 80.0)
    _set_scan_display_group_defaults(
        group_node,
        tiling=tiling,
        scan_speed=scan_speed,
        alert=alert,
        damage=damage,
        glitch_amt=glitch_amt,
        glitch_speed=glitch_speed,
        blink=do_blink,
        loop=do_loop,
        glitch_fx=do_glitch,
    )

    frame = nodes.new("ShaderNodeValue")
    frame.name = "ScanFrame"
    frame.label = "Frame"
    frame.location = (-260.0, -80.0)
    try:
        _drive_scan_frame_value(frame)
    except Exception as e:
        frame.outputs[0].default_value = 0.0
        print(f"Arc Raiders PSK Importer: ScanDisplay frame driver failed: {e}")
    if "Frame" in group_node.inputs:
        links.new(frame.outputs[0], group_node.inputs["Frame"])

    if "Emission Color" in group_node.outputs and "Emission Color" in principled.inputs:
        links.new(group_node.outputs["Emission Color"], principled.inputs["Emission Color"])
    if "Emission Strength" in group_node.outputs and "Emission Strength" in principled.inputs:
        links.new(group_node.outputs["Emission Strength"], principled.inputs["Emission Strength"])

    try:
        mat["arc_mi_family"] = "scan_display"
        mat["arc_scan_display"] = True
        mat["arc_alertness_state"] = float(alert)
        mat["arc_scan_blink"] = bool(do_blink)
        mat["arc_scan_loop"] = bool(do_loop)
        mat["arc_scan_glitch"] = bool(do_glitch)
    except Exception:
        pass



def setup_enemy_material(obj, psk_path: str) -> int:
    """Enemy SK/SM slot materials (Arc enemies, ScanDisplay, enemy decals).

    Same multi-slot SK wiring as weapons, kept as an explicit enemy-domain
    entrypoint so operators do not route enemies through setup_weapon_material.
    """
    from .weapon import setup_weapon_material
    return setup_weapon_material(obj, psk_path)
