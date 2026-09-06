"""
Material setup — character domain (split from materials.py monolith).
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
    _load_mi_textures,
    _parse_flat_mi_json,
    _set_material_alpha_mode,
)


# ---------------------------------------------------------------------------
# Other material setups (body, hair, weapon, misc, face)
# ---------------------------------------------------------------------------

def setup_body_material(obj, psk_path: str, body_variant: str):
    if body_variant == 'NONE':
        return
    
    tex_folder = os.path.join(os.path.dirname(psk_path), "Textures")
    albedo_name = BODY_ALBEDO.get(body_variant)
    normal_name = BODY_NORMAL.get(body_variant)
    
    albedo_path = os.path.join(tex_folder, albedo_name) if albedo_name else None
    normal_path = os.path.join(tex_folder, normal_name) if normal_name else None
    micro_path = os.path.join(tex_folder, "T_SkinMicroNormal_01.png")
    
    if albedo_path and not os.path.isfile(albedo_path):
        print(f"Arc Raiders PSK Importer: Body albedo not found: '{albedo_path}'")
        albedo_path = None
    if normal_path and not os.path.isfile(normal_path):
        print(f"Arc Raiders PSK Importer: Body normal not found: '{normal_path}'")
        normal_path = None
    if not os.path.isfile(micro_path):
        print(f"Arc Raiders PSK Importer: MicroNormal not found: '{micro_path}'")
        micro_path = None
    
    mat = bpy.data.materials.new(name=obj.name + "_Body_Mat")
    mat.use_nodes = True
    obj.active_material = mat
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    
    # Fix shape key seam splitting
    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = obj
    if obj.data.shape_keys:
        for key_block in obj.data.shape_keys.key_blocks:
            if key_block.name != 'Basis':
                key_block.value = 0.0
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.remove_doubles(threshold=0.001)
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.objects.active = prev_active
    
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (300, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (600, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])
    
    if albedo_path:
        img_albedo = bpy.data.images.load(albedo_path, check_existing=True)
        albedo_node = nodes.new("ShaderNodeTexImage")
        albedo_node.image = img_albedo
        albedo_node.label = albedo_name
        albedo_node.interpolation = "Cubic"
        albedo_node.location = (-600, 300)
        links.new(albedo_node.outputs["Color"], principled.inputs["Base Color"])
        
        ramp_node = nodes.new("ShaderNodeValToRGB")
        ramp_node.label = "Body Alpha Ramp"
        ramp_node.location = (-200, 0)
        ramp = ramp_node.color_ramp
        ramp.interpolation = "LINEAR"
        ramp.elements[0].position = 0.0
        ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
        ramp.elements[1].position = 0.086
        ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
        links.new(albedo_node.outputs["Color"], ramp_node.inputs["Fac"])
        links.new(ramp_node.outputs["Color"], principled.inputs["Alpha"])
    
    if normal_path or micro_path:
        normal_flipper = None
        if utils.ensure_node_group("NormalFlipper"):
            normal_flipper = nodes.new("ShaderNodeGroup")
            normal_flipper.node_tree = bpy.data.node_groups["NormalFlipper"]
            normal_flipper.location = (-800, -200)
        
        if normal_path and normal_flipper:
            img_normal = bpy.data.images.load(normal_path, check_existing=True)
            img_normal.colorspace_settings.name = "Non-Color"
            normal_tex = nodes.new("ShaderNodeTexImage")
            normal_tex.image = img_normal
            normal_tex.label = normal_name
            normal_tex.interpolation = "Cubic"
            normal_tex.location = (-1100, -200)
            links.new(normal_tex.outputs["Color"], normal_flipper.inputs[0])
        
        if micro_path:
            tex_coord = nodes.new("ShaderNodeTexCoord")
            tex_coord.location = (-1500, -500)
            mapping = nodes.new("ShaderNodeMapping")
            mapping.location = (-1300, -500)
            mapping.inputs["Scale"].default_value = (15.0, 15.0, 15.0)
            links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])
            
            img_micro = bpy.data.images.load(micro_path, check_existing=True)
            img_micro.colorspace_settings.name = "Non-Color"
            micro_node = nodes.new("ShaderNodeTexImage")
            micro_node.image = img_micro
            micro_node.label = "T_SkinMicroNormal_01"
            micro_node.interpolation = "Cubic"
            micro_node.location = (-1100, -500)
            links.new(mapping.outputs["Vector"], micro_node.inputs["Vector"])
        
        mix_node = nodes.new("ShaderNodeMix")
        mix_node.data_type = 'RGBA'
        mix_node.blend_type = 'OVERLAY'
        mix_node.location = (-500, -300)
        mix_node.inputs["Factor"].default_value = 1.0
        
        if normal_flipper:
            links.new(normal_flipper.outputs[0], mix_node.inputs[6])
        if micro_path:
            links.new(micro_node.outputs["Color"], mix_node.inputs[7])
        
        normal_map_node = nodes.new("ShaderNodeNormalMap")
        normal_map_node.location = (-100, -300)
        try:
            normal_map_node.convention = 'DIRECTX'
        except Exception:
            pass
        links.new(mix_node.outputs[2], normal_map_node.inputs["Color"])
        links.new(normal_map_node.outputs["Normal"], principled.inputs["Normal"])


# ---------------------------------------------------------------------------
# Face material setup
# ---------------------------------------------------------------------------

def _ensure_face_shader_node_group() -> bool:
    return utils.ensure_node_group("Face Shader")



def _find_head_mi_json(psk_path: str, mi_name: str) -> str:
    folder = os.path.dirname(psk_path)
    path = os.path.join(folder, mi_name + ".json")
    return path if os.path.isfile(path) else ""



def _mat_slot_suffix(mat_name: str) -> str:
    return mat_name.rsplit("_", 1)[-1].lower() if "_" in mat_name else mat_name.lower()



def _setup_head_slot_head(slot, psk_path: str):
    if not utils.ensure_node_group("Face Shader"):
        return
    mi_path = _find_head_mi_json(psk_path, slot.material.name)
    mat = bpy.data.materials.new(name=slot.material.name + "_Setup")
    mat.use_nodes = True
    slot.material = mat
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    group_node = nodes.new("ShaderNodeGroup")
    group_node.node_tree = bpy.data.node_groups["Face Shader"]
    group_node.location = (0, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (300, 0)
    if group_node.outputs:
        links.new(group_node.outputs[0], out_node.inputs["Surface"])
    if mi_path:
        _load_mi_textures(mi_path, nodes, links, group_node, connected={
            "BaseColor": "Face Albedo",
            "PM_Diffuse": "Face Albedo",
            "Normal": "Normal 1",
            "PM_Normals": "Normal 1",
            "T_Head_01_MASKS": "Blue Map",
        })



def _setup_head_slot_teeth(slot, psk_path: str):
    if not utils.ensure_node_group("Teeth"):
        return
    mi_path = _find_head_mi_json(psk_path, slot.material.name)
    mat = bpy.data.materials.new(name=slot.material.name + "_Setup")
    mat.use_nodes = True
    slot.material = mat
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    group_node = nodes.new("ShaderNodeGroup")
    group_node.node_tree = bpy.data.node_groups["Teeth"]
    group_node.location = (0, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (300, 0)
    if group_node.outputs:
        links.new(group_node.outputs[0], out_node.inputs["Surface"])
    if mi_path:
        color_input = "Base Colour" if "Base Colour" in group_node.inputs else "Base Color"
        _load_mi_textures(mi_path, nodes, links, group_node, connected={
            "teeth_color_map": color_input,
            "Normal": "Normal",
            "PM_Normals": "Normal",
            "teeth_normal_map": "Normal",
        })



def _setup_head_slot_eyes(slot, psk_path: str):
    if not utils.ensure_material("Eyes"):
        return
    slot.material = bpy.data.materials["Eyes"]



def _setup_head_slot_eyelashes(slot, psk_path: str):
    mi_path = _find_head_mi_json(psk_path, slot.material.name)
    mat = bpy.data.materials.new(name=slot.material.name + "_Setup")
    mat.use_nodes = True
    mat.blend_method = 'CLIP'
    slot.material = mat
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (0, 0)
    principled.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (300, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])
    if mi_path:
        mi = _parse_flat_mi_json(mi_path)
        row = 400
        for param, obj_path in mi['textures']:
            if 'CharacterSnow' in obj_path or obj_path.startswith('/Engine/'):
                continue
            fpath = textures.find_texture_from_object_path(obj_path + '.0')
            if not fpath:
                continue
            img = bpy.data.images.load(fpath, check_existing=True)
            img.colorspace_settings.name = "Non-Color"
            node = nodes.new("ShaderNodeTexImage")
            node.image = img
            node.label = param
            node.interpolation = "Cubic"
            node.location = (-400, row)
            row -= 300
            if param == "T_Eyelashes_M":
                links.new(node.outputs["Alpha"], principled.inputs["Alpha"])



def _setup_head_slot_wet(slot):
    if not utils.ensure_material("Wet"):
        return
    slot.material = bpy.data.materials["Wet"]



def setup_face_material(obj, psk_path: str):
    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = obj
    seen = set()
    i = 0
    while i < len(obj.material_slots):
        slot = obj.material_slots[i]
        mat_name = slot.material.name if slot.material else ""
        suffix = _mat_slot_suffix(mat_name)
        if suffix in seen:
            obj.active_material_index = i
            bpy.ops.object.material_slot_remove()
        else:
            seen.add(suffix)
            i += 1
    bpy.context.view_layer.objects.active = prev_active

    for slot in obj.material_slots:
        if not slot.material:
            continue
        suffix = _mat_slot_suffix(slot.material.name)
        if suffix == "head":
            _setup_head_slot_head(slot, psk_path)
        elif suffix in ("teeth", "saliva"):
            _setup_head_slot_teeth(slot, psk_path)
        elif suffix == "eyes":
            _setup_head_slot_eyes(slot, psk_path)
        elif suffix == "eyelashes":
            _setup_head_slot_eyelashes(slot, psk_path)
        elif suffix in ("eyeshell", "eyeedge"):
            _setup_head_slot_wet(slot)

    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = obj
    i = 0
    while i < len(obj.material_slots):
        slot = obj.material_slots[i]
        mat_name = slot.material.name if slot.material else ""
        if mat_name.lower().endswith("base_head"):
            obj.active_material_index = i
            bpy.ops.object.material_slot_remove()
        else:
            i += 1
    bpy.context.view_layer.objects.active = prev_active



# ---------------------------------------------------------------------------
# Hair material setup
# ---------------------------------------------------------------------------

def _hair_streaming_param_for_tex(texture_name: str) -> str:
    """Map TextureStreamingData names (BobSlick atlases) → hair MI param names."""
    tl = (texture_name or "").lower()
    if "coverage" in tl:
        return "Coverage"
    if "attribute" in tl:
        return "AttributeMap"
    if "depth" in tl:
        return "Depth"
    if "tangent" in tl:
        return "Tangent"
    if "globalhaircolor" in tl or ("fur" in tl and "basecolor" in tl):
        return "GlobalHairColor"
    return ""



def _parse_hair_mi(json_path: str) -> dict:
    result = {
        'textures': [], 'colours': [], 'scalars': {},
        'two_sided': False, 'opacity_clip': 0.333, 'uv_channels': {},
    }
    if not json_path or not os.path.isfile(json_path):
        return result
    try:
        with open(json_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        entry = utils.first_ue_export(data, "MaterialInstanceConstant") or utils.first_ue_export(data)
        props = (entry or {}).get('Properties') or {}
        have = set()
        for tp in props.get('TextureParameterValues') or []:
            if not isinstance(tp, dict):
                continue
            name = (tp.get('ParameterInfo') or {}).get('Name', '')
            pv = tp.get('ParameterValue') or {}
            if not isinstance(pv, dict):
                continue
            obj_path = pv.get('ObjectPath', '') or ''
            obj_name = pv.get('ObjectName', '') or ''
            m = re.search(r"'([^']+)'", str(obj_name))
            stem = m.group(1) if m else ''
            if stem:
                result['textures'].append((name, stem, obj_path))
                have.add(name)
        # FoxHatCards only overrides GlobalHairColor; Coverage/Depth/Tangent live on
        # the Metahuman parent and appear in TextureStreamingData.
        for ts in props.get('TextureStreamingData') or []:
            if not isinstance(ts, dict):
                continue
            tname = str(ts.get('TextureName', '') or "").strip()
            if not tname:
                continue
            param = _hair_streaming_param_for_tex(tname)
            try:
                uv_ch = int(ts.get('UVChannelIndex', 0) or 0)
            except (TypeError, ValueError):
                uv_ch = 0
            if param:
                result['uv_channels'][param] = uv_ch
            if not param or param in have:
                continue
            result['textures'].append((param, tname, ""))
            have.add(param)
        for vp in props.get('VectorParameterValues') or []:
            if not isinstance(vp, dict):
                continue
            name = (vp.get('ParameterInfo') or {}).get('Name', '')
            pv = vp.get('ParameterValue') or {}
            if not name or not isinstance(pv, dict):
                continue
            try:
                result['colours'].append((name, (
                    float(pv.get('R', 1.0)),
                    float(pv.get('G', 1.0)),
                    float(pv.get('B', 1.0)),
                    float(pv.get('A', 1.0)),
                )))
            except (TypeError, ValueError):
                continue
        for sp in props.get('ScalarParameterValues') or []:
            if not isinstance(sp, dict):
                continue
            name = str((sp.get('ParameterInfo') or {}).get('Name', '') or "")
            if not name:
                continue
            try:
                result['scalars'][name] = float(sp.get('ParameterValue'))
            except (TypeError, ValueError):
                pass
        bpo = props.get('BasePropertyOverrides') or {}
        if bpo.get('TwoSided') is not None:
            result['two_sided'] = bool(bpo.get('TwoSided'))
        if bpo.get('OpacityMaskClipValue') is not None:
            try:
                result['opacity_clip'] = float(bpo.get('OpacityMaskClipValue') or 0.333)
            except (TypeError, ValueError):
                pass
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to parse hair MI '{json_path}': {e}")
    return result



def _resolve_hair_texture(tex_stem: str, obj_path: str = "") -> str:
    """Resolve a hair atlas / colour map PNG from ObjectPath, FMDex, or Hairs/.

    FoxHatCards only overrides GlobalHairColor; Coverage/Depth/Tangent/Attribute
    come from TextureStreamingData names (BobSlick atlases) with no ObjectPath.
    FMDex often lacks those texture packages, so fall back to Content walks.
    """
    if obj_path:
        fpath = textures.find_texture_from_object_path(obj_path)
        if fpath:
            return fpath
    stem = (tex_stem or "").strip()
    if not stem:
        return ""
    try:
        from .. import fmdex
        found = fmdex.resolve_export_file(stem, ".png", context="any")
        if found and os.path.isfile(found):
            return found
    except Exception:
        pass

    # Guess common Metahuman / BobSlick atlas ObjectPaths.
    guesses = (
        f"/Game/Pioneer/Characters/Hairs/BobSlick/Textures/{stem}",
        f"/Game/Pioneer/Characters/Heads/Shared/Hair/Textures/{stem}",
    )
    for guess in guesses:
        fpath = textures.find_texture_from_object_path(guess)
        if fpath:
            return fpath

    # Last resort: basename under Characters/Hairs (and FacialHairs).
    target = (stem + ".png").lower()
    try:
        content_dirs = list(utils.get_content_dirs() or [])
    except Exception:
        content_dirs = []
    if not content_dirs:
        try:
            root = utils.get_pioneer_root() or ""
            cd = utils.find_content_dir(root) if root else ""
            if cd:
                content_dirs = [cd]
        except Exception:
            pass
    for content_dir in content_dirs:
        for rel in (
            os.path.join("Pioneer", "Characters", "Hairs"),
            os.path.join("Pioneer", "Characters", "FacialHairs"),
            os.path.join("Pioneer", "Characters", "Heads", "Shared", "Hair"),
        ):
            root = os.path.join(content_dir, rel)
            if not os.path.isdir(root):
                continue
            try:
                for walk_root, _dirs, files in os.walk(root):
                    for fname in files:
                        if fname.lower() == target:
                            return os.path.join(walk_root, fname)
            except OSError:
                continue
    return ""



def setup_hair_material(obj, json_path: str):
    mat_name = (getattr(obj, "name", "Hair") + "_Hair_Mat")[:63]
    mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    obj.active_material = mat
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (0, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (300, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    if not json_path or json_path == 'NONE':
        return

    mi = _parse_hair_mi(json_path)
    loaded = {}
    for param_name, tex_stem, obj_path in mi['textures']:
        fpath = _resolve_hair_texture(tex_stem, obj_path)
        if not fpath:
            continue
        img = bpy.data.images.load(fpath, check_existing=True)
        loaded[param_name] = (fpath, img)

    uv_channels = mi.get('uv_channels') or {}
    # FoxHatCards: GlobalHairColor → UV1; Coverage/atlases → UV0.
    if 'GlobalHairColor' in loaded and 'GlobalHairColor' not in uv_channels:
        uv_channels['GlobalHairColor'] = 1

    uv_cache = {}

    def _uv_for(param_name: str, loc):
        idx = int(uv_channels.get(param_name, 0) or 0)
        if idx in uv_cache:
            return uv_cache[idx]
        uv = nodes.new("ShaderNodeUVMap")
        # PSK imports name layers UV0 / UV1.
        uv.uv_map = f"UV{idx}"
        uv.label = f"UV{idx}"
        uv.location = loc
        uv_cache[idx] = uv
        return uv

    x_connected = -600
    x_unconnected = -1000
    row_conn = 400
    row_unconn = 400

    if 'GlobalHairColor' in loaded:
        _, img = loaded['GlobalHairColor']
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = "GlobalHairColor"
        node.interpolation = "Cubic"
        node.location = (x_connected, row_conn)
        uv = _uv_for('GlobalHairColor', (x_connected - 280, row_conn))
        links.new(uv.outputs["UV"], node.inputs["Vector"])
        links.new(node.outputs["Color"], principled.inputs["Base Color"])
        row_conn -= 300

    clip = float(mi.get('opacity_clip') or 0.333)
    two_sided = bool(mi.get('two_sided'))
    if 'Coverage' in loaded:
        _, img = loaded['Coverage']
        try:
            img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = "Coverage (Alpha)"
        node.interpolation = "Cubic"
        node.location = (x_connected, row_conn)
        uv = _uv_for('Coverage', (x_connected - 280, row_conn))
        links.new(uv.outputs["UV"], node.inputs["Vector"])
        # BobSlick atlas stores strand mask in RGB (grayscale); Alpha is solid 1.
        links.new(node.outputs["Color"], principled.inputs["Alpha"])
        row_conn -= 300
        _set_material_alpha_mode(
            mat, mode="CLIP", threshold=clip, two_sided=two_sided or True,
        )
    elif two_sided:
        _set_material_alpha_mode(mat, mode="OPAQUE", two_sided=True)

    rough_min = float((mi.get('scalars') or {}).get('Roughness min', 0.35) or 0.35)
    rough_max = float((mi.get('scalars') or {}).get('Roughness max', 0.65) or 0.65)
    principled.inputs["Roughness"].default_value = max(0.0, min(1.0, (rough_min + rough_max) * 0.5))

    handled = {'GlobalHairColor', 'Coverage'}
    for param_name in ('AttributeMap', 'Depth', 'Tangent'):
        if param_name not in loaded:
            continue
        _, img = loaded[param_name]
        try:
            img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param_name
        node.interpolation = "Cubic"
        node.location = (x_unconnected, row_unconn)
        uv = _uv_for(param_name, (x_unconnected - 280, row_unconn))
        links.new(uv.outputs["UV"], node.inputs["Vector"])
        row_unconn -= 300
        handled.add(param_name)

    for param_name, (fpath, img) in loaded.items():
        if param_name in handled:
            continue
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param_name
        node.interpolation = "Cubic"
        node.location = (x_unconnected, row_unconn)
        row_unconn -= 300

    for j, (param_name, rgba) in enumerate(mi['colours']):
        rgb_node = nodes.new("ShaderNodeRGB")
        rgb_node.label = param_name
        rgb_node.name = param_name
        rgb_node.outputs[0].default_value = rgba
        rgb_node.location = (700 + (j % 2) * 200, -(j // 2) * 180)

    try:
        mat["arc_mi_path"] = os.path.abspath(json_path)
        mat["arc_hair_cards"] = 1
    except Exception:
        pass

