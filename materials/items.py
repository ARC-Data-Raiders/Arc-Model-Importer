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

_MISC_GENERIC_TOKENS = {'sm', 'sk', 'fx', 'mesh', 'lod', 'cr', 'nom', 'nem', 'nxm', 'nam', 'basecolor', 'basecolour',
                        'normal', 'normals'}


def _misc_name_tokens(stem: str) -> set:
    return {t for t in re.split(r'[^a-z0-9]+', stem.casefold())
            if len(t) > 1 and not t.isdigit() and t not in _MISC_GENERIC_TOKENS}


def _find_misc_textures(folder: str, mesh_name: str = '') -> dict:
    """Folder textures named after the mesh. Shared content folders (Effects/Meshes holds the radial blur disc beside
    T_MechanicalDebris_01_CR) carry other assets' maps, so a texture must share a distinctive name token with the
    mesh; the best-matching texture wins each role."""
    result = {'cr': None, 'albedo': None, 'normal_type': None, 'normal': None}
    mesh_tokens = _misc_name_tokens(os.path.splitext(os.path.basename(mesh_name))[0])
    best = {}

    def offer(role, fpath, score, ntype=None):
        if score > best.get(role, (0,))[0]:
            best[role] = (score, fpath, ntype)

    try:
        for fname in sorted(os.listdir(folder)):
            fl = fname.lower()
            fpath = os.path.join(folder, fname)
            score = len(mesh_tokens & _misc_name_tokens(fl[:-4])) if mesh_tokens else 1
            if not score:
                continue
            if fl.endswith('_cr.png'):
                offer('cr', fpath, score)
            elif fl.endswith('_basecolor.png') or fl.endswith('_basecolour.png'):
                offer('albedo', fpath, score)
            for ntype in ('nom', 'nem', 'nxm', 'nam'):
                if fl.endswith(f'_{ntype}.png'):
                    offer('normal', fpath, score + .5, ntype)
            if fl.endswith('_normal.png') or fl.endswith('_normals.png'):
                offer('normal', fpath, score, 'tangent')
    except OSError:
        pass
    for role, (_, fpath, ntype) in best.items():
        result[role] = fpath
        if role == 'normal':
            result['normal_type'] = ntype
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
    # Accessories use the same authored slot families as other meshes: packed
    # metal, masked tickets, glass shells, emissive displays, and layered cloth.
    # Resolve those MIs before the legacy basename-only texture fallback. A
    # folder CR alone cannot describe a separate opacity map or multiple slots.
    from .common import CTX_OUTFIT
    from .dispatch import fix_object_materials_from_mi_slots
    from .weapon import setup_weapon_material

    wired = fix_object_materials_from_mi_slots(
        obj, os.path.dirname(psk_path), context=CTX_OUTFIT,
    )
    if not wired:
        wired = setup_weapon_material(obj, psk_path)
    if wired:
        # Some charms have hair-card sections and a packed body surface on the
        # same mesh. Keep the established coverage shader on those sections.
        from .character import setup_hair_material
        from .clothing import apply_embedded_fur_slots
        original_index = obj.active_material_index
        for i, slot in enumerate(obj.material_slots):
            mi_path = str(slot.material.get("arc_mi_path") or "") if slot.material else ""
            if mi_path and textures.is_hair_cards_mi(mi_path):
                obj.active_material_index = i
                setup_hair_material(obj, mi_path)
        obj.active_material_index = original_index
        apply_embedded_fur_slots(obj, psk_path)
        _fill_shell_slots(obj, os.path.dirname(psk_path))
        # Opaque glass (the resin pickup) has a compiled-shader surface profile the shading-model family lacks.
        from ..revamped_bridge import rebuild_surface_families
        rebuild_surface_families(obj, psk_path, {"glass_opaque"})
        obj["arc_accessory_material_route"] = "authored-mi-slots"
        return
    folder = os.path.dirname(psk_path)
    texs = _find_misc_textures(folder, psk_path)

    if not texs['cr'] and not texs['normal'] and not texs['albedo']:
        print(f"Arc Raiders PSK Importer: No CR or N*M textures named after '{os.path.basename(psk_path)}'")
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
            flipper.node_tree = utils.find_node_group("NormalFlipper")
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
        from .common import _parse_flat_mi_json
        mi_path = str(mat.get("arc_mi_path") or "")
        mi = _parse_flat_mi_json(mi_path) if mi_path else {}
        params = mi.get("scalars") or {}
        if {"FallingSpeed", "ZScale", "Threshold", "SnowShadowIntensity"}.issubset(params):
            from .accessory_static import setup_static_snow_shell
            setup_static_snow_shell(mat, mi, smoke, obj)
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



def _setup_flipbook_material(mat, mi_path: str, psk_path: str = ""):
    """Authored texture atlas display (Rows/Columns, Speed, Tint, Emissive)."""
    from .common import (_parse_flat_mi_json, _tex_lookup_from_flat_mi,
                         _character_layout_search_folders, _find_env_tex,
                         _mi_colour, _mi_scalar, _mix_rgba, _new_tex_image)
    mi = _parse_flat_mi_json(mi_path)
    lookup = _tex_lookup_from_flat_mi(mi, local_folders=_character_layout_search_folders(mi_path, psk_path))
    _param, image = _find_env_tex(lookup, "Flipbook")
    path = bpy.path.abspath(image.filepath) if image else ""
    if image is None:
        raise ValueError("Authored Flipbook atlas is missing: " + mi_path)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    s = mi.get("scalars") or {}
    rows = max(1, int(_mi_scalar(s, "Rows", "Number of rows", default=1)))
    columns = max(1, int(_mi_scalar(s, "Columns", default=rows)))
    speed = _mi_scalar(s, "Speed", default=1)
    uv = nodes.new("ShaderNodeTexCoord")
    sep = nodes.new("ShaderNodeSeparateXYZ");links.new(uv.outputs["UV"], sep.inputs[0])
    def math_node(operation, a, b=0):
        n = nodes.new("ShaderNodeMath");n.operation = operation
        for i,v in enumerate((a,b)):
            if hasattr(v, "node"):links.new(v,n.inputs[i])
            else:n.inputs[i].default_value = v
        return n.outputs[0]
    frame = nodes.new("ShaderNodeValue");frame.label = "Flipbook frame"
    # Static appearance pass: use an authored capture frame/time when provided,
    # otherwise the first atlas frame. Animated effects are intentionally deferred.
    capture = next((float(s[k]) for k in ("Capture Frame", "Frame") if k in s and s[k] is not None), None)
    if capture is None:
        capture = _mi_scalar(s, "IconCaptureTime", "Capture Time", default=0) * speed * rows * columns
    frame.outputs[0].default_value = capture
    mat["arc_flipbook_static_frame"] = float(capture)
    cell = math_node("MODULO",math_node("FLOOR",frame.outputs[0]), rows * columns)
    col = math_node("MODULO",cell,columns)
    row = math_node("FLOOR",math_node("DIVIDE",cell,columns))
    coords = nodes.new("ShaderNodeCombineXYZ")
    links.new(math_node("DIVIDE",math_node("ADD",sep.outputs["X"],col),columns),coords.inputs["X"])
    top_row = math_node("SUBTRACT",rows - 1,row)
    links.new(math_node("DIVIDE",math_node("ADD",sep.outputs["Y"],top_row),rows),coords.inputs["Y"])
    atlas = _new_tex_image(nodes,image,"Authored flipbook atlas",(-500,0));atlas.extension = "CLIP"
    links.new(coords.outputs[0],atlas.inputs["Vector"])
    tint = nodes.new("ShaderNodeRGB");tint.outputs[0].default_value = _mi_colour(mi.get("colours") or [],"Tint",default=(1,1,1,1))
    color = _mix_rgba(nodes,links,atlas.outputs["Color"],tint.outputs[0],1,(0,0),"Display tint",blend="MULTIPLY")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    links.new(color,bsdf.inputs["Base Color"]);links.new(color,bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = _mi_scalar(s,"Emissive","Emissive Power",default=1)
    bsdf.inputs["Roughness"].default_value = max(0,min(1,_mi_scalar(s,"Roughness",default=.5)))
    out = nodes.new("ShaderNodeOutputMaterial");links.new(bsdf.outputs[0],out.inputs["Surface"])
    mat["arc_flipbook_rows"] = rows;mat["arc_flipbook_columns"] = columns
    mat["arc_flipbook_atlas"] = path
