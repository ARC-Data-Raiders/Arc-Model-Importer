"""
Material setup functions for the Arc Raiders Importer
"""

import os
import re
import json
import bpy
import math
from mathutils import Vector

from . import utils
from . import textures
from .properties import BODY_ALBEDO, BODY_NORMAL

# ---------------------------------------------------------------------------
# Material setup - Arc Texturer
# ---------------------------------------------------------------------------

def setup_arc_texturer_material(obj, folder: str, colours: dict, psk_path: str = "", 
                                json_path: str = "", decal_folder: str = "", 
                                selected_skin_name: str = "", manual_skins_folder: str = ""):
    if not utils.ensure_arc_texturer_node_group():
        print(f"Arc Raiders PSK Importer: Skipping material for '{obj.name}' — Arc Texturer unavailable.")
        return
    
    mat = bpy.data.materials.new(name=obj.name + "_Mat")
    mat.use_nodes = True
    obj.active_material = mat
    
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    
    group_node = nodes.new("ShaderNodeGroup")
    group_node.node_tree = bpy.data.node_groups[utils._NODE_GROUP]
    group_node.location = (0, 0)
    
    # Categorise main folder PNGs
    try:
        main_pngs = sorted(f for f in os.listdir(folder) if f.lower().endswith(".png"))
    except OSError:
        main_pngs = []
    
    occlusion_tex = []
    normal_tex = []
    basecolor_tex = []
    colormask_tex = []
    other_tex = []
    
    for fname in main_pngs:
        role = textures.identify_texture(fname)
        if role == "occlusion":
            occlusion_tex.append(fname)
        elif role == "normal":
            normal_tex.append(fname)
        elif role == "basecolor":
            basecolor_tex.append(fname)
        elif role == "colormask":
            colormask_tex.append(fname)
        else:
            other_tex.append(fname)
    
    # Categorise base skin PNGs
    base_pngs = textures.scan_base_skin_textures(psk_path, selected_skin_name, manual_skins_folder) if psk_path else []
    base_normals = [p for p in base_pngs if textures.base_skin_texture_group(os.path.basename(p)) == "normals"]
    base_masks = [p for p in base_pngs if textures.base_skin_texture_group(os.path.basename(p)) == "masks"]
    base_other = [p for p in base_pngs if textures.base_skin_texture_group(os.path.basename(p)) == "other"]
    
    ROW_H = -280
    COL_W = 300
    
    def place_column(file_list, col_x, is_paths=False, non_color=False, connect_fn=None, collapsed=False, start_y=0):
        nodes_created = []
        step = -22 if collapsed else ROW_H
        for row, item in enumerate(file_list):
            fpath = item if is_paths else os.path.join(folder, item)
            fname = os.path.basename(fpath)
            img = bpy.data.images.load(fpath, check_existing=True)
            if non_color:
                img.colorspace_settings.name = "Non-Color"
            node = nodes.new("ShaderNodeTexImage")
            node.image = img
            node.label = fname
            node.hide = collapsed
            node.interpolation = "Cubic"
            node.location = (col_x, start_y + row * step)
            nodes_created.append(node)
            if connect_fn:
                connect_fn(node, img)
        return nodes_created
    
    ABOVE_X = -600
    ABOVE_Y = 800
    ABOVE_STEP = -25
    
    def connect_occlusion(node, img):
        links.new(node.outputs["Color"], group_node.inputs["Main Texture"])
    place_column(occlusion_tex, ABOVE_X, connect_fn=connect_occlusion, collapsed=True, start_y=ABOVE_Y)
    
    def connect_normal(node, img):
        img.colorspace_settings.name = "Non-Color"
        links.new(node.outputs["Color"], group_node.inputs["Base Normal"])
    place_column(normal_tex, ABOVE_X + 10, connect_fn=connect_normal, collapsed=True,
                 start_y=ABOVE_Y + len(occlusion_tex) * ABOVE_STEP - 30)
    
    def connect_basecolor(node, img):
        if "Base Color" in group_node.inputs:
            links.new(node.outputs["Color"], group_node.inputs["Base Color"])
    place_column(basecolor_tex, ABOVE_X + 20, connect_fn=connect_basecolor, collapsed=True,
                 start_y=ABOVE_Y + (len(occlusion_tex) + len(normal_tex)) * ABOVE_STEP - 60)
    
    colormask_nodes = place_column(colormask_tex, -2000)
    
    _SECTION_SOCKETS = [
        (["Colour 1", "Colour 3", "Colour 5"],
         ["Roughness 1", "Roughness 3", "Roughness 5"],
         ["Metallic 1", "Metallic 3", "Metallic 5"]),
        (["Colour 2", "Colour 4", "Colour 6"],
         ["Roughness 2", "Roughness 4", "Roughness 6"],
         ["Metallic 2", "Metallic 4", "Metallic 6"]),
        (["Colour 7", "Colour 8"],
         ["Roughness 7", "Roughness 8"],
         ["Metallic 7", "Metallic 8"]),
    ]
    
    _CM_COLOUR_INPUTS = {
        0: {"X_Green": "ColorA2", "Y_Blue": "ColorB2", "Z_Pink": "ColorC2"},
        1: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
        2: {"X_Green": "ColorA2", "Y_Blue": "ColorB2", "Z_Pink": "ColorC2"},
    }
    
    _cm_group_ok = utils.ensure_colormask_node_group()
    _single_colormask = len(colormask_nodes) == 1
    _cm_groups = []
    
    for cm_idx, (colour_socks, rough_socks, metal_socks) in enumerate(_SECTION_SOCKETS):
        if not any(s in group_node.inputs for s in colour_socks):
            _cm_groups.append(None)
            continue
        cm_group = None
        if _cm_group_ok:
            cm_group = nodes.new("ShaderNodeGroup")
            cm_group.node_tree = bpy.data.node_groups[utils._COLORMASK_GROUP]
            cm_group.label = f"ColorMask_XYZ (instance {cm_idx + 1})"
            if cm_idx == 2:
                cm_group.location = (-600, -1500)
            else:
                cm_group.location = (-600, -cm_idx * 500)
        _cm_groups.append(cm_group)
        
        if _single_colormask:
            src_node = colormask_nodes[0]
        else:
            src_node = colormask_nodes[cm_idx] if cm_idx < len(colormask_nodes) else None
        
        if cm_group and src_node:
            if cm_group.inputs:
                links.new(src_node.outputs["Color"], cm_group.inputs[0])
            def _wire_out(cm_grp, out_name, arc_sock):
                if arc_sock not in group_node.inputs:
                    return
                out = cm_grp.outputs.get(out_name)
                if out:
                    links.new(out, group_node.inputs[arc_sock])
            for csock, rsock, msock in zip(colour_socks, rough_socks, metal_socks):
                _wire_out(cm_group, "Mask_Color", csock)
                _wire_out(cm_group, "Mask_Roughness", rsock)
                _wire_out(cm_group, "Mask_Metal", msock)
        elif src_node and not cm_group:
            for csock in colour_socks:
                if csock in group_node.inputs:
                    links.new(src_node.outputs["Color"], group_node.inputs[csock])
    
    place_column(other_tex, -2400, start_y=-2000)
    
    tex_coord_node = nodes.new("ShaderNodeTexCoord")
    tex_coord_node.location = (-5500, 0)
    tex_coord_node.label = "Texture Coordinate"
    
    mapping_node = nodes.new("ShaderNodeMapping")
    mapping_node.location = (-5000, 0)
    mapping_node.label = "Mapping"
    mapping_node.inputs["Scale"].default_value = (25.0, 25.0, 25.0)
    links.new(tex_coord_node.outputs["UV"], mapping_node.inputs["Vector"])
    
    _normal_nodes = {}
    _mask_nodes = {}
    _color_nodes = {}
    _pattern_nodes = {}
    
    for row, fpath in enumerate(base_normals):
        fname = os.path.basename(fpath)
        img = bpy.data.images.load(fpath, check_existing=True)
        img.colorspace_settings.name = "Non-Color"
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = fname
        node.interpolation = "Cubic"
        node.location = (-3000, row * ROW_H)
        links.new(mapping_node.outputs["Vector"], node.inputs["Vector"])
        _normal_nodes[os.path.splitext(fname)[0]] = node
    
    _mask_col_nodes = place_column(base_masks, -3300, is_paths=True, non_color=True)
    for n in _mask_col_nodes:
        if n and n.image:
            stem = os.path.splitext(os.path.basename(n.image.filepath))[0]
            _mask_nodes[stem] = n
    
    _other_col_nodes = place_column(base_other, -3600, is_paths=True, start_y=-2000)
    for n in _other_col_nodes:
        if n and n.image:
            fname = os.path.basename(n.image.filepath).lower()
            stem = os.path.splitext(os.path.basename(n.image.filepath))[0]
            if 'color' in fname:
                _color_nodes[stem] = n
            elif 'pattern' in fname:
                _pattern_nodes[stem] = n
    
    ta_ids = textures.parse_texture_array_ids(json_path) if json_path else {}
    
    ID_TO_SOCKET = [
        ("BaseNormalID", "Mix {zone}", _normal_nodes, base_normals),
        ("EdgeNormalID", "Edge Normal {zone}", _normal_nodes, base_normals),
        ("CreaseNormalID", "Crease Normal {zone}", _normal_nodes, base_normals),
        ("BaseRoughnessID", "Roughness {zone}", _mask_nodes, base_masks),
        ("EdgeRoughnessID", "Edge Roughness {zone}", _mask_nodes, base_masks),
        ("CreaseRoughnessID", "Crease Roughness {zone}", _mask_nodes, base_masks),
        ("CreaseMaskID", "Crease Mask {zone}", _mask_nodes, base_masks),
        ("EdgeMaskID", "Edge Mask {zone}", _mask_nodes, base_masks),
        ("ColorTextureID", "Color Texture {zone}", _color_nodes, base_other),
        ("PatternID", "Pattern {zone}", _pattern_nodes, base_other),
    ]
    
    _zones_with_normal = set()
    _ta_nodes_wired_to_arc = set()
    
    for (id_suffix, arc_template, node_dict, png_list) in ID_TO_SOCKET:
        for (zone, key_sfx), slice_idx in list(ta_ids.items()):
            if key_sfx != id_suffix:
                continue
            arc_sock = arc_template.replace("{zone}", zone)
            if arc_sock not in group_node.inputs:
                continue
            png_path = textures.find_slice_png(png_list, slice_idx)
            if not png_path:
                print(f"    WARNING: No PNG for slice {slice_idx} ({arc_sock})")
                continue
            stem = os.path.splitext(os.path.basename(png_path))[0]
            tex_nd = node_dict.get(stem)
            if tex_nd is None:
                is_nc = id_suffix not in ("ColorTextureID",)
                img = bpy.data.images.load(png_path, check_existing=True)
                if is_nc:
                    img.colorspace_settings.name = "Non-Color"
                tex_nd = nodes.new("ShaderNodeTexImage")
                tex_nd.image = img
                tex_nd.label = os.path.basename(png_path)
                tex_nd.interpolation = "Cubic"
                tex_nd.location = (-3000, len(node_dict) * ROW_H)
                node_dict[stem] = tex_nd
            links.new(tex_nd.outputs["Color"], group_node.inputs[arc_sock])
            _ta_nodes_wired_to_arc.add(id(tex_nd))
            print(f"    Wired {stem} -> {arc_sock}")
            if id_suffix == "BaseNormalID" and zone not in _zones_with_normal:
                set_enable_slider(group_node, zone)
                _zones_with_normal.add(zone)
    
    _all_ta_nodes = set()
    for nd in list(_normal_nodes.values()) + list(_mask_nodes.values()) + list(_color_nodes.values()) + list(_pattern_nodes.values()):
        if nd is not None:
            _all_ta_nodes.add(nd)
    
    for nd in _all_ta_nodes:
        is_wired = id(nd) in _ta_nodes_wired_to_arc
        for lnk in list(mat.node_tree.links):
            if lnk.to_node == nd and lnk.to_socket.name == "Vector":
                if not is_wired:
                    mat.node_tree.links.remove(lnk)
                break
        else:
            if is_wired:
                links.new(mapping_node.outputs["Vector"], nd.inputs["Vector"])
        if not is_wired:
            nd.location = (nd.location.x - 2000, nd.location.y - 2000)
    
    force_white = {
        'Colour 2',
        'Roughness 1', 'Roughness 2', 'Roughness 3',
        'Roughness 4', 'Roughness 5', 'Roughness 6',
        'Roughness 7', 'Roughness 8', 'Roughness 9',
    }
    for socket in group_node.inputs:
        if socket.name in force_white:
            try:
                socket.default_value = (1.0, 1.0, 1.0, 1.0)
            except Exception:
                pass
    
    output_node = nodes.new("ShaderNodeOutputMaterial")
    output_node.location = (300, 0)
    if group_node.outputs:
        char_out = group_node.outputs.get("Character") or group_node.outputs[0]
        links.new(char_out, output_node.inputs["Surface"])
        disp_out = (group_node.outputs.get("Displacement") or group_node.outputs.get("Displacement Map"))
        if disp_out:
            links.new(disp_out, output_node.inputs["Displacement"])
    
    # Skin colour RGB nodes
    if colours:
        colour_keys = textures.get_colour_keys()
        
        _ABC_KEYS = ["ColorA", "ColorB", "ColorC"]
        _ABC2_KEYS = ["ColorA2", "ColorB2", "ColorC2"]
        _abc_row = 0
        _abc2_row = 0
        ABC_X = -1400
        ABC2_X = -1180
        ABC_Y_START = 220
        ABC_ROW_H = -220
        
        j = 0
        for key in colour_keys:
            rgba = colours.get(key)
            if rgba is None:
                continue
            if textures.skip_colour(rgba):
                continue
            rgb_node = nodes.new("ShaderNodeRGB")
            rgb_node.label = key
            rgb_node.name = key
            rgb_node.outputs[0].default_value = (rgba[0], rgba[1], rgba[2], rgba[3])
            
            is_crease_or_edge = ("_CreaseColorOverlay" in key or "_EdgeColorOverlay" in key)
            is_base_overlay = "_BaseColorOverlay" in key
            
            if is_crease_or_edge:
                zone_num = int(key.split("_")[0]) if key[0].isdigit() else 0
                is_crease = "_Crease" in key
                pair_row = zone_num - 1
                pair_col = 0 if is_crease else 1
                rgb_node.hide = True
                rgb_node.location = (200 + pair_col * 220, -400 - pair_row * 100)
            elif is_base_overlay:
                zone_num = int(key.split("_")[0]) if key[0].isdigit() else 0
                rgb_node.location = (700, 200 - (zone_num - 1) * 220)
            elif key in _ABC_KEYS:
                rgb_node.location = (ABC_X, ABC_Y_START + _abc_row * ABC_ROW_H)
                _abc_row += 1
            elif key in _ABC2_KEYS:
                rgb_node.location = (ABC2_X, ABC_Y_START + _abc2_row * ABC_ROW_H)
                _abc2_row += 1
            else:
                rgb_node.location = (-1700 + (j % 2) * 220, -1000 - (j // 2) * 220)
                j += 1
        
        for inst_idx, input_map in _CM_COLOUR_INPUTS.items():
            cm_grp = _cm_groups[inst_idx] if inst_idx < len(_cm_groups) else None
            if not cm_grp:
                continue
            for socket_name, colour_key in input_map.items():
                rgb_nd = nodes.get(colour_key)
                if rgb_nd and socket_name in cm_grp.inputs:
                    links.new(rgb_nd.outputs[0], cm_grp.inputs[socket_name])
        
        _OVERLAY_SOCKET_MAP = {
            "_BaseColorOverlay": "Colour",
            "_CreaseColorOverlay": "Crease",
            "_EdgeColorOverlay": "Edge",
        }
        _basecolor_overlay_zones = set()
        
        for key in colour_keys:
            for suffix, arc_prefix in _OVERLAY_SOCKET_MAP.items():
                if not key.endswith(suffix):
                    continue
                zone_str = key[: -len(suffix)]
                if not zone_str.isdigit():
                    continue
                arc_sock = f"{arc_prefix} {zone_str}"
                rgb_nd = nodes.get(key)
                if rgb_nd and arc_sock in group_node.inputs:
                    links.new(rgb_nd.outputs[0], group_node.inputs[arc_sock])
                    if suffix == "_BaseColorOverlay":
                        _basecolor_overlay_zones.add(zone_str)
                break
        
        if _basecolor_overlay_zones:
            for lnk in list(mat.node_tree.links):
                if lnk.to_node != group_node:
                    continue
                parts = lnk.to_socket.name.rsplit(" ", 1)
                if len(parts) != 2:
                    continue
                sock_type, zone_num = parts
                if sock_type in ("Roughness", "Metallic") and zone_num in _basecolor_overlay_zones:
                    if (lnk.from_node.type == "GROUP" and
                            lnk.from_node.node_tree and
                            lnk.from_node.node_tree.name == utils._COLORMASK_GROUP):
                        mat.node_tree.links.remove(lnk)
    
    # MI parameters
    if json_path:
        _mi_params = textures.parse_all_mi_parameters(json_path, known_colour_names=set(colours.keys()))
        if _mi_params['scalars'] or _mi_params['vectors']:
            place_mi_parameter_nodes(nodes, links, _mi_params)
    
    # Decals
    decals = textures.parse_decals(json_path) if json_path else []
    if decals:
        setup_decals(nodes, links, group_node, decals, decal_folder)

def set_enable_slider(group_node, zone: str, value: float = 1.0):
    sock = group_node.inputs.get(f"Enable {zone}")
    if sock is not None:
        try:
            sock.default_value = value
        except Exception:
            pass

def place_mi_parameter_nodes(nodes, links, mi_params: dict, base_x: float = -1700, base_y: float = -2400):
    row = 0
    col_w = 260
    row_h = -140
    per_col = 10
    
    for i, (name, value) in enumerate(mi_params.get('scalars', [])):
        node = nodes.new("ShaderNodeValue")
        node.label = name
        node.outputs[0].default_value = value
        col = i // per_col
        r = i % per_col
        node.location = (base_x + col * col_w, base_y + r * row_h)
    
    vec_col_x = base_x + (((len(mi_params.get('scalars', [])) - 1) // per_col) + 1) * col_w + 100
    for i, (name, rgba) in enumerate(mi_params.get('vectors', [])):
        node = nodes.new("ShaderNodeRGB")
        node.label = name
        node.outputs[0].default_value = rgba
        col = i // per_col
        r = i % per_col
        node.location = (vec_col_x + col * col_w, base_y + r * row_h)

def setup_decals(nodes, links, group_node, decals: list, decal_folder: str):
    decal_uv_node = None
    _decal_tex_cache = {}
    _decal_data_cache = {}
    
    for d_idx, decal in enumerate(decals):
        idx = decal["index"]
        col_x = 1200 + d_idx * 450
        row_y = 800
        
        if decal_uv_node is None:
            decal_uv_node = nodes.new("ShaderNodeTexCoord")
            decal_uv_node.label = "Decal UV"
            decal_uv_node.location = (col_x - 250, row_y + 200)
        
        import math as _math
        
        uv_u = decal["uv_u"]
        uv_v = decal["uv_v"]
        scale = decal["scale"]
        rotation = decal["rotation"]
        width_ratio = decal["width_ratio"]
        
        if scale > 0.0001:
            scale_x = 1.0 / scale
            scale_y = width_ratio / scale
        else:
            scale_x = scale_y = 1.0
        
        rotation_rad = _math.radians(rotation * -360.0)
        centre_u = 0.5 - uv_u
        centre_v = 0.5 + uv_v
        _px = scale_x * centre_u
        _py = scale_y * centre_v
        _cos = _math.cos(rotation_rad)
        _sin = _math.sin(rotation_rad)
        loc_x = 0.5 - (_cos * _px - _sin * _py)
        loc_y = 0.5 - (_sin * _px + _cos * _py)
        
        mapping_node = nodes.new("ShaderNodeMapping")
        mapping_node.vector_type = "POINT"
        mapping_node.label = (f"Decal {idx}  R={uv_u:.4f} G={uv_v:.4f}  "
                              f"SX={scale_x:.3f} SY={scale_y:.3f}  "
                              f"Rot={_math.degrees(rotation_rad):.1f}°")
        mapping_node.location = (col_x - 250, row_y)
        
        mapping_node["arc_decal_R"] = uv_u
        mapping_node["arc_decal_G"] = uv_v
        mapping_node["arc_decal_B"] = scale
        mapping_node["arc_decal_A"] = rotation
        mapping_node["arc_decal_WR"] = width_ratio
        mapping_node["arc_decal_idx"] = idx
        
        mapping_node.inputs["Location"].default_value = (loc_x, loc_y, 0.0)
        mapping_node.inputs["Rotation"].default_value = (0.0, 0.0, rotation_rad)
        mapping_node.inputs["Scale"].default_value = (scale_x, scale_y, 1.0)
        
        links.new(decal_uv_node.outputs["UV"], mapping_node.inputs["Vector"])
        row_y -= 250
        
        color_tex_node = None
        data_tex_node = None
        ramp_node = None
        rgb_node = None
        
        tex_stem = decal["texture"]
        if tex_stem not in _decal_tex_cache:
            tex_fpath = resolve_decal_texture(tex_stem, decal.get("texture_path", ""), decal_folder)
            if tex_fpath:
                img = bpy.data.images.load(tex_fpath, check_existing=True)
                tex_node = nodes.new("ShaderNodeTexImage")
                tex_node.image = img
                tex_node.label = f"Decal {idx}: {tex_stem}"
                tex_node.interpolation = "Cubic"
                tex_node.extension = "CLIP"
                tex_node.location = (col_x, row_y)
                links.new(mapping_node.outputs["Vector"], tex_node.inputs["Vector"])
                _decal_tex_cache[tex_stem] = tex_node
                color_tex_node = tex_node
                row_y -= 300
            else:
                _decal_tex_cache[tex_stem] = None
        else:
            prev_node = _decal_tex_cache[tex_stem]
            if prev_node is not None:
                dup_node = nodes.new("ShaderNodeTexImage")
                dup_node.image = prev_node.image
                dup_node.label = f"Decal {idx}: {tex_stem}"
                dup_node.interpolation = "Cubic"
                dup_node.extension = "CLIP"
                dup_node.location = (col_x, row_y)
                links.new(mapping_node.outputs["Vector"], dup_node.inputs["Vector"])
                color_tex_node = dup_node
                row_y -= 300
        
        data_stem = decal.get("data_texture", "")
        if data_stem:
            if data_stem not in _decal_data_cache:
                data_fpath = resolve_decal_texture(data_stem, decal.get("data_texture_path", ""), decal_folder)
                if data_fpath:
                    data_img = bpy.data.images.load(data_fpath, check_existing=True)
                    data_img.colorspace_settings.name = "Non-Color"
                    data_node = nodes.new("ShaderNodeTexImage")
                    data_node.image = data_img
                    data_node.label = f"Decal {idx} Data: {data_stem}"
                    data_node.interpolation = "Cubic"
                    data_node.extension = "CLIP"
                    data_node.location = (col_x, row_y)
                    links.new(mapping_node.outputs["Vector"], data_node.inputs["Vector"])
                    _decal_data_cache[data_stem] = data_node
                    data_tex_node = data_node
                    row_y -= 300
                else:
                    _decal_data_cache[data_stem] = None
            else:
                prev_data = _decal_data_cache[data_stem]
                if prev_data is not None:
                    dup_data = nodes.new("ShaderNodeTexImage")
                    dup_data.image = prev_data.image
                    dup_data.label = f"Decal {idx} Data: {data_stem}"
                    dup_data.interpolation = "Cubic"
                    dup_data.extension = "CLIP"
                    dup_data.location = (col_x, row_y)
                    links.new(mapping_node.outputs["Vector"], dup_data.inputs["Vector"])
                    data_tex_node = dup_data
                    row_y -= 300
        
        color_a = decal.get("color_a")
        color_b = decal.get("color_b")
        
        if color_a and color_b:
            ramp_node = nodes.new("ShaderNodeValToRGB")
            ramp_node.label = f"Decal {idx} Colours"
            ramp_node.location = (col_x, row_y)
            ramp = ramp_node.color_ramp
            ramp.interpolation = "CONSTANT"
            ramp.elements[0].position = 0.0
            ramp.elements[0].color = (color_a[0], color_a[1], color_a[2], color_a[3])
            if len(ramp.elements) < 2:
                ramp.elements.new(0.51)
            else:
                ramp.elements[1].position = 0.51
            ramp.elements[1].color = (color_b[0], color_b[1], color_b[2], color_b[3])
        elif color_a:
            rgb_node = nodes.new("ShaderNodeRGB")
            rgb_node.label = f"Decal {idx} Color"
            rgb_node.outputs[0].default_value = (color_a[0], color_a[1], color_a[2], color_a[3])
            rgb_node.location = (col_x, row_y)
        
        n = idx
        if color_tex_node is not None:
            if f"Decal {n}" in group_node.inputs:
                if ramp_node is not None:
                    links.new(color_tex_node.outputs["Color"], ramp_node.inputs["Fac"])
                    colour_out = ramp_node.outputs["Color"]
                elif rgb_node is not None:
                    colour_out = rgb_node.outputs[0]
                else:
                    colour_out = color_tex_node.outputs["Color"]
                links.new(colour_out, group_node.inputs[f"Decal {n}"])
                if f"Decal Alpha {n}" in group_node.inputs:
                    links.new(color_tex_node.outputs["Alpha"], group_node.inputs[f"Decal Alpha {n}"])
                if f"Decal Enable {n}" in group_node.inputs:
                    try:
                        group_node.inputs[f"Decal Enable {n}"].default_value = 1.0
                    except Exception:
                        pass
        
        if data_tex_node is not None and f"DN {n}" in group_node.inputs:
            links.new(data_tex_node.outputs["Color"], group_node.inputs[f"DN {n}"])
            if f"DN Enable {n}" in group_node.inputs:
                try:
                    group_node.inputs[f"DN Enable {n}"].default_value = 1.0
                except Exception:
                    pass

def resolve_decal_texture(stem: str, object_path: str, decal_folder: str) -> str:
    if object_path:
        fpath = textures.find_texture_from_object_path(object_path)
        if fpath:
            return fpath
    if decal_folder and os.path.isdir(decal_folder) and stem:
        stem_lower = stem.lower()
        try:
            for fname in os.listdir(decal_folder):
                if os.path.splitext(fname)[0].lower() == stem_lower and fname.lower().endswith(".png"):
                    return os.path.join(decal_folder, fname)
        except OSError:
            pass
    return ""

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


def _parse_flat_mi_json(json_path: str) -> dict:
    result = {'textures': [], 'colours': [], 'switches': {}}
    if not json_path or not os.path.isfile(json_path):
        return result
    try:
        with open(json_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if isinstance(data, list):
            data = data[0]
        for param, path in data.get('Textures', {}).items():
            obj_path = path.rsplit('.', 1)[0] if '.' in path.split('/')[-1] else path
            result['textures'].append((param, obj_path))
        for param, val in data.get('Parameters', {}).get('Colors', {}).items():
            result['colours'].append((param, (
                float(val.get('R', 1.0)),
                float(val.get('G', 1.0)),
                float(val.get('B', 1.0)),
                float(val.get('A', 1.0)),
            )))
        result['switches'] = data.get('Parameters', {}).get('Switches', {})
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to parse flat MI JSON '{json_path}': {e}")
    return result


def _load_mi_textures(json_path: str, nodes, links, group_node,
                      connected: dict, x_conn=-600, x_unconn=-1000):
    mi = _parse_flat_mi_json(json_path)
    row_c = 400
    row_u = 400
    seen_paths = {}
    connected_set = set()

    for param, obj_path in mi['textures']:
        if obj_path.startswith('/Engine/') or 'CharacterSnow' in obj_path:
            continue
        fpath = textures.find_texture_from_object_path(obj_path + '.0')
        if not fpath:
            continue

        if fpath in seen_paths:
            existing_node = seen_paths[fpath]
            if param in connected and group_node:
                socket = connected[param]
                if socket not in connected_set and socket in group_node.inputs:
                    links.new(existing_node.outputs["Color"], group_node.inputs[socket])
                    connected_set.add(socket)
            continue

        img = bpy.data.images.load(fpath, check_existing=True)
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param
        node.interpolation = "Cubic"
        seen_paths[fpath] = node

        is_normal = ("Normal" in param or "normal" in param or
                     param in ("PM_Normals", "T_EYE_NORMALS", "T_Eye_Wet_Normal"))

        if param in connected and group_node:
            socket = connected[param]
            if socket not in connected_set and socket in group_node.inputs:
                if is_normal:
                    img.colorspace_settings.name = "Non-Color"
                node.location = (x_conn, row_c)
                links.new(node.outputs["Color"], group_node.inputs[socket])
                connected_set.add(socket)
                row_c -= 300
            else:
                node.location = (x_unconn, row_u)
                row_u -= 300
        else:
            if is_normal:
                img.colorspace_settings.name = "Non-Color"
            node.location = (x_unconn, row_u)
            row_u -= 300

    for j, (param, rgba) in enumerate(mi['colours']):
        rgb_node = nodes.new("ShaderNodeRGB")
        rgb_node.label = param
        rgb_node.outputs[0].default_value = rgba
        rgb_node.location = (700 + (j % 2) * 200, -(j // 2) * 180)


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

def _parse_hair_mi(json_path: str) -> dict:
    result = {'textures': [], 'colours': []}
    if not json_path or not os.path.isfile(json_path):
        return result
    try:
        with open(json_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        entry = data[0] if isinstance(data, list) else data
        props = entry.get('Properties', {})
        for tp in props.get('TextureParameterValues', []):
            name = tp.get('ParameterInfo', {}).get('Name', '')
            obj_path = tp.get('ParameterValue', {}).get('ObjectPath', '')
            obj_name = tp.get('ParameterValue', {}).get('ObjectName', '')
            m = re.search(r"'([^']+)'", obj_name)
            stem = m.group(1) if m else ''
            if stem:
                result['textures'].append((name, stem, obj_path))
        for vp in props.get('VectorParameterValues', []):
            name = vp.get('ParameterInfo', {}).get('Name', '')
            pv = vp.get('ParameterValue', {})
            result['colours'].append((name, (
                float(pv.get('R', 1.0)),
                float(pv.get('G', 1.0)),
                float(pv.get('B', 1.0)),
                float(pv.get('A', 1.0)),
            )))
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to parse hair MI '{json_path}': {e}")
    return result


def setup_hair_material(obj, json_path: str):
    mat = bpy.data.materials.new(name=obj.name + "_Hair_Mat")
    mat.use_nodes = True
    mat.blend_method = 'CLIP'
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
        fpath = textures.find_texture_from_object_path(obj_path)
        if not fpath:
            continue
        img = bpy.data.images.load(fpath, check_existing=True)
        loaded[param_name] = (fpath, img)

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
        links.new(node.outputs["Color"], principled.inputs["Base Color"])
        row_conn -= 300

    if 'Coverage' in loaded:
        _, img = loaded['Coverage']
        img.colorspace_settings.name = "Non-Color"
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = "Coverage (Alpha)"
        node.interpolation = "Cubic"
        node.location = (x_connected, row_conn)
        links.new(node.outputs["Color"], principled.inputs["Alpha"])
        row_conn -= 300

    handled = {'GlobalHairColor', 'Coverage'}
    for param_name in ('AttributeMap', 'Depth', 'Tangent'):
        if param_name not in loaded:
            continue
        _, img = loaded[param_name]
        img.colorspace_settings.name = "Non-Color"
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param_name
        node.interpolation = "Cubic"
        node.location = (x_unconnected, row_unconn)
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


# ---------------------------------------------------------------------------
# Weapon material setup
# ---------------------------------------------------------------------------

def _parse_weapon_sk_json(psk_path: str) -> list:
    psk_stem = os.path.splitext(os.path.basename(psk_path))[0]
    base_stem = re.sub(r'_LOD\d+$', '', psk_stem, flags=re.IGNORECASE)
    folder = os.path.dirname(psk_path)
    for candidate in [base_stem + ".json",
                      re.sub(r'^SK_', '', base_stem) + ".json"]:
        json_path = os.path.join(folder, candidate)
        if not os.path.isfile(json_path):
            continue
        try:
            with open(json_path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            for entry in (data if isinstance(data, list) else [data]):
                if entry.get('Type') == 'SkeletalMesh':
                    props = entry.get('Properties', {})
                    slots = entry.get('SkeletalMaterials',
                            props.get('SkeletalMaterials', []))
                    result = []
                    for slot in slots:
                        slot_name = slot.get('MaterialSlotName', '')
                        obj_path = slot.get('Material', {}).get('ObjectPath', '')
                        if not obj_path:
                            continue
                        mi_stem = os.path.splitext(obj_path.split('/')[-1])[0]
                        mi_json = ""
                        for search_folder in [folder, utils.get_weapon_shared_folder()]:
                            if not search_folder:
                                continue
                            candidate_path = os.path.join(search_folder, mi_stem + ".json")
                            if os.path.isfile(candidate_path):
                                mi_json = candidate_path
                                break
                        result.append((slot_name, mi_stem, mi_json))
                    return result
        except Exception as e:
            print(f"Arc Raiders PSK Importer: Failed to parse weapon SK JSON: {e}")
    return []


def _setup_weapon_main_material(mat, mi_path: str, psk_path: str):
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (600, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (900, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    psk_stem = os.path.splitext(os.path.basename(psk_path))[0]
    weapon_stem = re.sub(r'_LOD\d+$', '', psk_stem, flags=re.IGNORECASE)
    weapon_stem = re.sub(r'^SK_', '', weapon_stem, flags=re.IGNORECASE)
    weapon_stem_base = re.sub(r'_[A-Z]$', '', weapon_stem, flags=re.IGNORECASE)
    weapon_stem_lower = weapon_stem.lower()
    weapon_stem_base_lower = weapon_stem_base.lower()

    tex_lookup = {}
    seen_fpaths = set()
    for param, obj_path in mi['textures']:
        if obj_path.startswith('/Engine/') or 'CharacterSnow' in obj_path:
            continue
        fpath = textures.find_texture_from_object_path(obj_path + '.0')
        if not fpath or fpath in seen_fpaths:
            continue
        seen_fpaths.add(fpath)
        img = bpy.data.images.load(fpath, check_existing=True)
        tex_lookup[param] = (fpath, img)

    def find_weapon_tex(suffix):
        for stem in (weapon_stem_lower, weapon_stem_base_lower):
            target = f"t_{stem}_{suffix.lower()}"
            for param, (fpath, img) in tex_lookup.items():
                if os.path.splitext(os.path.basename(fpath))[0].lower() == target:
                    return param, img
        return None, None

    cr_node = None
    _, cr_img = find_weapon_tex("cr")
    if cr_img:
        cr_node = nodes.new("ShaderNodeTexImage")
        cr_node.image = cr_img
        cr_node.label = "CR (Colour/Roughness)"
        cr_node.interpolation = "Cubic"
        cr_node.location = (-900, 400)
        links.new(cr_node.outputs["Alpha"], principled.inputs["Roughness"])

    _, nxm_img = find_weapon_tex("nxm")
    if nxm_img:
        nxm_img.colorspace_settings.name = "Non-Color"
        nxm_node = nodes.new("ShaderNodeTexImage")
        nxm_node.image = nxm_img
        nxm_node.label = "NXM (Normal/Metallic)"
        nxm_node.interpolation = "Cubic"
        nxm_node.location = (-900, 100)
        if utils.ensure_node_group("NormalFlipper"):
            flipper = nodes.new("ShaderNodeGroup")
            flipper.node_tree = bpy.data.node_groups["NormalFlipper"]
            flipper.location = (-300, 100)
            links.new(nxm_node.outputs["Color"], flipper.inputs[0])
            nm_in = flipper.outputs[0]
        else:
            nm_in = nxm_node.outputs["Color"]
        nm_node = nodes.new("ShaderNodeNormalMap")
        nm_node.location = (100, 100)
        try:
            nm_node.convention = 'DIRECTX'
        except Exception:
            pass
        links.new(nm_in, nm_node.inputs["Color"])
        links.new(nm_node.outputs["Normal"], principled.inputs["Normal"])
        links.new(nxm_node.outputs["Alpha"], principled.inputs["Metallic"])

    id_node = None
    _, id_img = find_weapon_tex("id")
    if id_img:
        id_img.colorspace_settings.name = "Non-Color"
        id_node = nodes.new("ShaderNodeTexImage")
        id_node.image = id_img
        id_node.label = "ID Map"
        id_node.interpolation = "Cubic"
        id_node.location = (-900, -200)

    COLOUR_CHANNELS = {
        'Color - Red': 0, 'Color - Green': 1,
        'Color - Blue': 2, 'Color - Alpha': 3,
    }
    MASK_SWITCHES = {
        'Color - Red': 'Enable Red Mask', 'Color - Green': 'Enable Green Mask',
        'Color - Blue': 'Enable Blue Mask', 'Color - Alpha': 'Enable Alpha Mask',
    }
    switches = mi.get('switches', {})
    zone_colours = []
    for param, rgba in mi['colours']:
        if param not in COLOUR_CHANNELS:
            continue
        switch_name = MASK_SWITCHES.get(param, '')
        if switch_name and not switches.get(switch_name, True):
            continue
        zone_colours.append((COLOUR_CHANNELS[param], rgba, param))

    if cr_node and zone_colours:
        sep_node = None
        if id_node:
            sep_node = nodes.new("ShaderNodeSeparateColor")
            sep_node.location = (-200, -200)
            links.new(id_node.outputs["Color"], sep_node.inputs["Color"])

        ch_names = {0: "Red", 1: "Green", 2: "Blue"}
        current_out = cr_node.outputs["Color"]
        mul_x = 200
        for i, (ch_idx, rgba, param) in enumerate(zone_colours):
            rgb_node = nodes.new("ShaderNodeRGB")
            rgb_node.label = param
            rgb_node.outputs[0].default_value = rgba
            rgb_node.location = (mul_x - 200, -400 - i * 180)
            mul_node = nodes.new("ShaderNodeMix")
            mul_node.data_type = 'RGBA'
            mul_node.blend_type = 'MULTIPLY'
            mul_node.location = (mul_x, 200)
            mul_node.inputs["Factor"].default_value = 1.0
            links.new(current_out, mul_node.inputs[6])
            links.new(rgb_node.outputs[0], mul_node.inputs[7])
            if sep_node:
                if ch_idx in ch_names and ch_names[ch_idx] in sep_node.outputs:
                    links.new(sep_node.outputs[ch_names[ch_idx]], mul_node.inputs["Factor"])
                elif ch_idx == 3 and id_node:
                    links.new(id_node.outputs["Alpha"], mul_node.inputs["Factor"])
            current_out = mul_node.outputs[2]
            mul_x += 250
        links.new(current_out, principled.inputs["Base Color"])
    elif cr_node:
        links.new(cr_node.outputs["Color"], principled.inputs["Base Color"])

    handled = {node.image.filepath for node in nodes if hasattr(node, 'image') and node.image}
    row_u = -500
    for param, (fpath, img) in tex_lookup.items():
        if fpath in handled:
            continue
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param
        node.interpolation = "Cubic"
        node.location = (-1300, row_u)
        row_u -= 300

    for j, (param, rgba) in enumerate(
        [(p, r) for p, r in mi['colours'] if p not in COLOUR_CHANNELS]
    ):
        rgb_node = nodes.new("ShaderNodeRGB")
        rgb_node.label = param
        rgb_node.outputs[0].default_value = rgba
        rgb_node.location = (1200 + (j % 2) * 200, -(j // 2) * 180)


def _setup_weapon_emissive_material(mat, mi_path: str):
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (300, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (600, 0)
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
        principled.inputs["Emission Color"].default_value = light_color
        principled.inputs["Emission Strength"].default_value = light_intensity


def _setup_weapon_screen_material(mat, mi_path: str):
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (300, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (600, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    tint = next((rgba for p, rgba in mi['colours'] if 'tint' in p.lower()), None)
    try:
        with open(mi_path, 'r', encoding='utf-8') as fh:
            raw = json.load(fh)
        if isinstance(raw, list):
            raw = raw[0]
        emissive_strength = raw.get('Parameters', {}).get('Scalars', {}).get('Emissive', 1.0)
    except Exception:
        emissive_strength = 1.0

    if tint:
        principled.inputs["Emission Color"].default_value = tint
        principled.inputs["Emission Strength"].default_value = emissive_strength

    row = 400
    seen = set()
    for param, obj_path in mi['textures']:
        if obj_path.startswith('/Engine/'):
            continue
        fpath = textures.find_texture_from_object_path(obj_path + '.0')
        if not fpath or fpath in seen:
            continue
        seen.add(fpath)
        img = bpy.data.images.load(fpath, check_existing=True)
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param
        node.interpolation = "Cubic"
        node.location = (-600, row)
        row -= 300


def setup_weapon_material(obj, psk_path: str):
    slots = _parse_weapon_sk_json(psk_path)
    if not slots:
        print(f"Arc Raiders PSK Importer: No weapon SK JSON found for '{os.path.basename(psk_path)}'")
        return

    for slot_name, mi_stem, mi_path in slots:
        target_slot = None
        for s in obj.material_slots:
            if s.material:
                sn = s.material.name.lower()
                if slot_name.lower() in sn or sn in slot_name.lower():
                    target_slot = s
                    break
        if target_slot is None:
            idx = [s[0] for s in slots].index(slot_name) if slot_name in [s[0] for s in slots] else 0
            if idx < len(obj.material_slots):
                target_slot = obj.material_slots[idx]
        if target_slot is None:
            continue

        mat = bpy.data.materials.new(name=f"{obj.name}_{mi_stem}_Mat")
        mat.use_nodes = True
        target_slot.material = mat
        mi_stem_lower = mi_stem.lower()

        if not mi_path:
            print(f"Arc Raiders PSK Importer: MI JSON not found for slot '{slot_name}' ({mi_stem})")
            continue

        if 'emissive' in mi_stem_lower or 'light' in mi_stem_lower:
            _setup_weapon_emissive_material(mat, mi_path)
        elif 'screen' in mi_stem_lower:
            _setup_weapon_screen_material(mat, mi_path)
        else:
            _setup_weapon_main_material(mat, mi_path, psk_path)


# ---------------------------------------------------------------------------
# Misc item material setup
# ---------------------------------------------------------------------------

def _find_misc_textures(folder: str) -> dict:
    result = {'cr': None, 'normal_type': None, 'normal': None}
    try:
        for fname in os.listdir(folder):
            fl = fname.lower()
            fpath = os.path.join(folder, fname)
            if fl.endswith('_cr.png'):
                result['cr'] = fpath
            for ntype in ('nom', 'nem', 'nxm', 'nam'):
                if fl.endswith(f'_{ntype}.png'):
                    result['normal_type'] = ntype
                    result['normal'] = fpath
    except OSError:
        pass
    return result


def setup_misc_material(obj, psk_path: str):
    folder = os.path.dirname(psk_path)
    texs = _find_misc_textures(folder)

    if not texs['cr'] and not texs['normal']:
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
        links.new(normal_node.outputs["Alpha"], principled.inputs["Metallic"])

        ntype = texs['normal_type']
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

    elif cr_node:
        links.new(cr_node.outputs["Color"], principled.inputs["Base Color"])