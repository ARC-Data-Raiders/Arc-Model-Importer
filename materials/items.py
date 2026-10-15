"""
Material setup — items domain (split from materials.py monolith).
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




# ---------------------------------------------------------------------------
# Misc item material setup
# ---------------------------------------------------------------------------

def _find_misc_textures(folder: str) -> dict:
    result = {'cr': None, 'albedo': None, 'normal_type': None, 'normal': None}
    try:
        for fname in os.listdir(folder):
            fl = fname.lower()
            fpath = os.path.join(folder, fname)
            if fl.endswith('_cr.png'):
                result['cr'] = fpath
            elif fl.endswith('_basecolor.png') or fl.endswith('_basecolour.png'):
                result['albedo'] = fpath
            for ntype in ('nom', 'nem', 'nxm', 'nam'):
                if fl.endswith(f'_{ntype}.png'):
                    result['normal_type'] = ntype
                    result['normal'] = fpath
            if result['normal'] is None and (
                fl.endswith('_normal.png') or fl.endswith('_normals.png')
            ):
                result['normal_type'] = 'tangent'
                result['normal'] = fpath
    except OSError:
        pass
    return result



def _albedo_tex_node(nodes, path: str):
    """BaseColor image node. Shell maps often store RGB with a zero alpha."""
    if not path:
        return None
    img = bpy.data.images.load(path, check_existing=True)
    try:
        img.alpha_mode = "NONE"
    except Exception:
        pass
    node = nodes.new("ShaderNodeTexImage")
    node.image = img
    node.label = "Base Color"
    node.interpolation = "Cubic"
    node.location = (-900, 300)
    return node


def setup_misc_material(obj, psk_path: str):
    folder = os.path.dirname(psk_path)
    texs = _find_misc_textures(folder)

    if not texs['cr'] and not texs['normal'] and not texs['albedo']:
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
        if ntype not in ('nom', 'tangent'):
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
        elif ntype == 'tangent':
            color_node = cr_node or _albedo_tex_node(nodes, texs['albedo'])
            if color_node is not None:
                links.new(color_node.outputs["Color"], principled.inputs["Base Color"])

    elif cr_node:
        links.new(cr_node.outputs["Color"], principled.inputs["Base Color"])
    elif texs['albedo']:
        albedo = _albedo_tex_node(nodes, texs['albedo'])
        if albedo is not None:
            links.new(albedo.outputs["Color"], principled.inputs["Base Color"])
    _fill_shell_slots(obj, folder)


def _fill_shell_slots(obj, folder: str) -> None:
    """Second slot on charms such as the snowglobe. The body CR map does not cover it."""
    smoke = ""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        low = name.lower()
        if not low.endswith(".png"):
            continue
        if "snowsmoke" in low or low.endswith("_nox.png"):
            smoke = os.path.join(folder, name)
            if "snowsmoke" in low:
                break
    if not smoke:
        return
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or "shell" not in (mat.name or "").lower():
            continue
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()
        principled = nodes.new("ShaderNodeBsdfPrincipled")
        principled.location = (400, 0)
        out_node = nodes.new("ShaderNodeOutputMaterial")
        out_node.location = (700, 0)
        links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])
        img = bpy.data.images.load(smoke, check_existing=True)
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.label = "Shell"
        tex.interpolation = "Cubic"
        tex.location = (-500, 80)
        links.new(tex.outputs["Color"], principled.inputs["Base Color"])
        try:
            principled.inputs["Roughness"].default_value = 0.08
        except Exception:
            pass
        trans = principled.inputs.get("Transmission Weight") or principled.inputs.get("Transmission")
        if trans is not None:
            try:
                trans.default_value = 0.85
            except Exception:
                pass
        for attr, value in (
            ("blend_method", "HASHED"),
            ("surface_render_method", "DITHERED"),
            ("use_transparency_overlap", True),
            ("show_transparent_back", True),
        ):
            try:
                setattr(mat, attr, value)
            except Exception:
                pass
        try:
            mat.use_backface_culling = False
        except Exception:
            pass

