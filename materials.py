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
                                selected_skin_name: str = "", manual_skins_folder: str = "",
                                mi_data: dict = None, main_pngs: list = None, base_pngs: list = None):
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
    if main_pngs is None:
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
    if base_pngs is None:
        base_pngs = textures.scan_base_skin_textures(psk_path, selected_skin_name, manual_skins_folder) if psk_path else []
    base_normals = [p for p in base_pngs if textures.base_skin_texture_group(os.path.basename(p)) == "normals"]
    base_masks = [p for p in base_pngs if textures.base_skin_texture_group(os.path.basename(p)) == "masks"]
    base_other = [p for p in base_pngs if textures.base_skin_texture_group(os.path.basename(p)) == "other"]
    
    ROW_H = -360
    COL_W = 360
    
    def place_column(file_list, col_x, is_paths=False, non_color=False, connect_fn=None, collapsed=False, start_y=0):
        nodes_created = []
        step = -28 if collapsed else ROW_H
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
    
    ABOVE_X = -700
    ABOVE_Y = 1000
    ABOVE_STEP = -32
    
    def connect_occlusion(node, img):
        links.new(node.outputs["Color"], group_node.inputs["Main Texture"])
    place_column(occlusion_tex, ABOVE_X, connect_fn=connect_occlusion, collapsed=True, start_y=ABOVE_Y)
    
    def connect_normal(node, img):
        img.colorspace_settings.name = "Non-Color"
        links.new(node.outputs["Color"], group_node.inputs["Base Normal"])
    place_column(normal_tex, ABOVE_X + 20, connect_fn=connect_normal, collapsed=True,
                 start_y=ABOVE_Y + len(occlusion_tex) * ABOVE_STEP - 40)
    
    def connect_basecolor(node, img):
        if "Base Color" in group_node.inputs:
            links.new(node.outputs["Color"], group_node.inputs["Base Color"])
    place_column(basecolor_tex, ABOVE_X + 40, connect_fn=connect_basecolor, collapsed=True,
                 start_y=ABOVE_Y + (len(occlusion_tex) + len(normal_tex)) * ABOVE_STEP - 80)
    
    colormask_nodes = place_column(colormask_tex, -2200)
    
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
                cm_group.location = (-800, -1800)
            else:
                cm_group.location = (-800, -cm_idx * 650)
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
    tex_coord_node.location = (-5800, 200)
    tex_coord_node.label = "Texture Coordinate"
    
    mapping_node = nodes.new("ShaderNodeMapping")
    mapping_node.location = (-5200, 200)
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
    
    ta_ids = (mi_data or {}).get("ta_ids")
    if ta_ids is None:
        ta_ids = textures.parse_texture_array_ids(json_path) if json_path else {}
    slice_maps = {
        id(base_normals): textures.build_slice_png_map(base_normals),
        id(base_masks): textures.build_slice_png_map(base_masks),
        id(base_other): textures.build_slice_png_map(base_other),
    }
    
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
        slice_map = slice_maps[id(png_list)]
        for (zone, key_sfx), slice_idx in list(ta_ids.items()):
            if key_sfx != id_suffix:
                continue
            arc_sock = arc_template.replace("{zone}", zone)
            if arc_sock not in group_node.inputs:
                continue
            png_path = slice_map.get(slice_idx) or textures.find_slice_png(png_list, slice_idx)
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
            if id_suffix == "BaseNormalID" and zone not in _zones_with_normal:
                set_enable_slider(group_node, zone)
                _zones_with_normal.add(zone)
    
    _all_ta_nodes = set()
    for nd in list(_normal_nodes.values()) + list(_mask_nodes.values()) + list(_color_nodes.values()) + list(_pattern_nodes.values()):
        if nd is not None:
            _all_ta_nodes.add(nd)
    
    vector_links = {
        lnk.to_node: lnk
        for lnk in mat.node_tree.links
        if lnk.to_socket.name == "Vector"
    }
    for nd in _all_ta_nodes:
        is_wired = id(nd) in _ta_nodes_wired_to_arc
        lnk = vector_links.get(nd)
        if lnk is not None:
            if not is_wired:
                mat.node_tree.links.remove(lnk)
        elif is_wired:
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
    output_node.location = (450, 0)
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
        # These six keys drive the ColorMask_XYZ instances directly (see _CM_COLOUR_INPUTS
        # below). Unlike overlay/pattern/swatch keys, they carry no alpha-based "unused"
        # signal (alpha is always 1.0) and a literal black/white/red value here is real
        # authored data, not a sentinel — dropping it left the corresponding ColorMask_XYZ
        # socket (e.g. "Z_Pink") unlinked and falling back to the node group's raw debug
        # default. Always create + wire a node for these regardless of value.
        _CORE_COLOUR_KEYS = set(_ABC_KEYS) | set(_ABC2_KEYS)
        _abc_row = 0
        _abc2_row = 0
        ABC_X = -1600
        ABC2_X = -1300
        ABC_Y_START = 280
        ABC_ROW_H = -280
        
        j = 0
        for key in colour_keys:
            rgba = colours.get(key)
            if rgba is None:
                continue
            if key not in _CORE_COLOUR_KEYS and textures.skip_colour(rgba):
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
                rgb_node.location = (250 + pair_col * 260, -450 - pair_row * 120)
            elif is_base_overlay:
                zone_num = int(key.split("_")[0]) if key[0].isdigit() else 0
                rgb_node.location = (850, 250 - (zone_num - 1) * 260)
            elif key in _ABC_KEYS:
                rgb_node.location = (ABC_X, ABC_Y_START + _abc_row * ABC_ROW_H)
                _abc_row += 1
            elif key in _ABC2_KEYS:
                rgb_node.location = (ABC2_X, ABC_Y_START + _abc2_row * ABC_ROW_H)
                _abc2_row += 1
            else:
                rgb_node.location = (-1900 + (j % 2) * 260, -1100 - (j // 2) * 260)
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
            rgba = colours.get(key)
            if rgba is not None and textures.skip_colour(rgba):
                continue
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
    if mi_data is not None:
        _mi_params = mi_data.get("mi_params") or {"scalars": [], "vectors": []}
    elif json_path:
        _mi_params = textures.parse_all_mi_parameters(json_path, known_colour_names=set(colours.keys()))
    else:
        _mi_params = {"scalars": [], "vectors": []}
    if _mi_params['scalars'] or _mi_params['vectors']:
        place_mi_parameter_nodes(nodes, links, _mi_params)
    
    # Decals
    if mi_data is not None:
        decals = mi_data.get("decals") or []
    else:
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

def place_mi_parameter_nodes(nodes, links, mi_params: dict, base_x: float = -1900, base_y: float = -2800):
    row = 0
    col_w = 300
    row_h = -180
    per_col = 8
    
    for i, (name, value) in enumerate(mi_params.get('scalars', [])):
        node = nodes.new("ShaderNodeValue")
        node.label = name
        node.outputs[0].default_value = value
        col = i // per_col
        r = i % per_col
        node.location = (base_x + col * col_w, base_y + r * row_h)
    
    vec_col_x = base_x + (((len(mi_params.get('scalars', [])) - 1) // per_col) + 1) * col_w + 140
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
            ramp.elements[0].color = (color_b[0], color_b[1], color_b[2], color_b[3])
            if len(ramp.elements) < 2:
                ramp.elements.new(0.51)
            else:
                ramp.elements[1].position = 0.51
            ramp.elements[1].color = (color_a[0], color_a[1], color_a[2], color_a[3])
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
    result = {
        'textures': [], 'colours': [], 'switches': {}, 'scalars': {},
        'opacity_clip': 0.3333, 'blend_mode': None, 'is_null': False,
    }
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
        params = data.get('Parameters', {}) or {}
        for param, val in params.get('Colors', {}).items():
            result['colours'].append((param, (
                float(val.get('R', 1.0)),
                float(val.get('G', 1.0)),
                float(val.get('B', 1.0)),
                float(val.get('A', 1.0)),
            )))
        result['switches'] = params.get('Switches', {}) or {}
        result['scalars'] = params.get('Scalars', {}) or {}
        result['is_null'] = bool(params.get('IsNull', False))
        props = params.get('Properties') or {}
        bpo = props.get('BasePropertyOverrides') or {}
        result['blend_mode'] = bpo.get('BlendMode') or params.get('BlendMode')
        clip = bpo.get('OpacityMaskClipValue')
        if clip is not None:
            result['opacity_clip'] = float(clip)
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to parse flat MI JSON '{json_path}': {e}")
    return result


def _set_material_clip(mat, threshold: float = 0.3333):
    """Masked/CLIP alpha so shell-style meshes don't render as opaque floats."""
    try:
        mat.blend_method = 'CLIP'
    except Exception:
        pass
    try:
        mat.alpha_threshold = float(threshold)
    except Exception:
        pass
    try:
        # EEVEE Next / Blender 4.2+
        mat.surface_render_method = 'DITHERED'
    except Exception:
        pass
    try:
        mat.use_backface_culling = True
    except Exception:
        pass


def _is_enemy_decal_mi(mi: dict) -> bool:
    """True when MI uses the Masked NAO + Height/HX enemy-decal preset."""
    for param, _obj in mi.get('textures') or []:
        pl = param.lower()
        if pl in ("decaltrimsheet", "height/hx") or pl.endswith("_nao") or "/hx" in pl:
            return True
        if "enemydecals" in pl or pl.endswith("_h") and "height" in pl:
            return True
    for param, obj_path in mi.get('textures') or []:
        stem = (obj_path or "").rsplit("/", 1)[-1].lower()
        if "_nao" in stem or "enemydecals" in stem:
            return True
    return False


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
        entry = utils.first_ue_export(data, "MaterialInstanceConstant") or utils.first_ue_export(data)
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
# Multi-slot material setup (weapons + enemies)
# ---------------------------------------------------------------------------

def _resolve_mi_json_path(mi_stem: str, obj_path: str, psk_folder: str) -> str:
    """Find MI_*.json beside the PSK, in shared weapon mats, or via ObjectPath."""
    if not mi_stem:
        return ""
    for search_folder in [psk_folder, utils.get_weapon_shared_folder()]:
        if not search_folder:
            continue
        candidate = os.path.join(search_folder, mi_stem + ".json")
        if os.path.isfile(candidate):
            return candidate
    if obj_path:
        found = textures.find_asset_from_object_path(obj_path, ".json")
        if found:
            return found
    return ""


def _parse_sk_material_slots(psk_path: str) -> list:
    """Return [(slot_name, mi_stem, mi_json_path), ...] from sibling SK_/SM_*.json."""
    psk_stem = os.path.splitext(os.path.basename(psk_path))[0]
    base_stem = re.sub(r'_LOD\d+$', '', psk_stem, flags=re.IGNORECASE)
    folder = os.path.dirname(psk_path)
    candidates = [
        base_stem + ".json",
        re.sub(r'^SK_', '', base_stem) + ".json",
        re.sub(r'^SM_', '', base_stem) + ".json",
    ]
    # de-dupe while preserving order
    seen = set()
    ordered = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    for candidate in ordered:
        json_path = os.path.join(folder, candidate)
        if not os.path.isfile(json_path):
            continue
        try:
            with open(json_path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            for entry in utils.ue_export_entries(data):
                entry_type = entry.get('Type', '')
                if entry_type not in ('SkeletalMesh', 'StaticMesh'):
                    continue
                props = entry.get('Properties', {})
                slots = (
                    entry.get('SkeletalMaterials')
                    or props.get('SkeletalMaterials')
                    or entry.get('StaticMaterials')
                    or props.get('StaticMaterials')
                    or []
                )
                result = []
                for slot in slots:
                    slot_name = slot.get('MaterialSlotName', '') or ""
                    mat_ref = (
                        slot.get('Material')
                        or slot.get('MaterialInterface')
                        or {}
                    )
                    obj_path = mat_ref.get('ObjectPath', '') or ""
                    mi_stem = textures.mi_stem_from_material_ref(mat_ref)
                    if not mi_stem:
                        continue
                    mi_json = _resolve_mi_json_path(mi_stem, obj_path, folder)
                    result.append((slot_name, mi_stem, mi_json))
                if result:
                    return result
        except Exception as e:
            print(f"Arc Raiders PSK Importer: Failed to parse mesh material slots: {e}")
    return []


def _parse_weapon_sk_json(psk_path: str) -> list:
    """Back-compat alias for multi-slot SK parsing."""
    return _parse_sk_material_slots(psk_path)


def _match_material_slot(obj, slot_name: str, slot_index: int, used_indices: set):
    """Bind SK slot -> (Blender material slot, index) by index, then exact name."""
    if slot_index < len(obj.material_slots) and slot_index not in used_indices:
        return obj.material_slots[slot_index], slot_index

    want = (slot_name or "").strip().lower()
    if want:
        for i, s in enumerate(obj.material_slots):
            if i in used_indices or not s.material:
                continue
            sn = s.material.name.lower()
            # PSK importer may prefix object name: "Obj_SlotName"
            if sn == want or sn.endswith("_" + want) or sn.split(".")[0] == want:
                return s, i
    return None, -1


def _resolve_mi_texture_path(obj_path: str, local_folders: list = None) -> str:
    """Resolve a MI texture ObjectPath to a PNG, with local-folder fallback."""
    if not obj_path:
        return ""
    lookup = obj_path if obj_path.endswith(".0") else (obj_path + ".0")
    fpath = textures.find_texture_from_object_path(lookup)
    if fpath:
        return fpath
    leaf = obj_path.rstrip("/").split("/")[-1]
    leaf = leaf.split(".")[0]
    if not leaf:
        return ""
    for folder in (local_folders or []):
        if not folder or not os.path.isdir(folder):
            continue
        for ext in (".png", ".tga", ".jpg", ".jpeg"):
            candidate = os.path.join(folder, leaf + ext)
            if os.path.isfile(candidate):
                return candidate
    return ""


def _tex_lookup_from_flat_mi(mi: dict, local_folders: list = None) -> dict:
    """param -> (fpath, image) for textures referenced by a flat MI JSON."""
    tex_lookup = {}
    path_to_img = {}
    for param, obj_path in mi.get('textures', []):
        if not obj_path or obj_path.startswith('/Engine/') or 'CharacterSnow' in obj_path:
            continue
        fpath = _resolve_mi_texture_path(obj_path, local_folders)
        if not fpath:
            continue
        if fpath in path_to_img:
            tex_lookup[param] = (fpath, path_to_img[fpath])
            continue
        img = bpy.data.images.load(fpath, check_existing=True)
        path_to_img[fpath] = img
        tex_lookup[param] = (fpath, img)
    return tex_lookup


def _find_flat_tex(tex_lookup: dict, *keys_or_suffixes: str):
    """Find a texture by exact MI param name, then by filename suffix (_cr, _nom, ...)."""
    lower_keys = {k.lower(): k for k in tex_lookup}
    for key in keys_or_suffixes:
        hit = lower_keys.get(key.lower())
        if hit:
            return hit, tex_lookup[hit][1]
    for key in keys_or_suffixes:
        suffix = key.lower()
        if not suffix.startswith('_'):
            suffix = '_' + suffix
        for param, (fpath, img) in tex_lookup.items():
            stem = os.path.splitext(os.path.basename(fpath))[0].lower()
            if stem.endswith(suffix):
                return param, img
    return None, None


def _unlink_input(links, socket):
    """Remove any existing links into a node socket."""
    for lnk in list(socket.links):
        links.remove(lnk)


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



def _setup_weapon_main_material(mat, mi_path: str, psk_path: str):
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    # Layout columns (left → right): textures, utils, mixes, BSDF, output
    COL_TEX, COL_UTIL, COL_MIX, COL_BSDF, COL_OUT = -1600, -1100, -450, 400, 750
    Y_CR, Y_N, Y_ID, Y_TINT, Y_WEAR, Y_EXX, Y_EXTRA = 700, 250, -200, 450, -750, -1400, -2000
    scalars = mi.get("scalars") or {}

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path)):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    # Last-resort: pick CR/NXM/etc. directly from the mesh folder if ObjectPath resolve failed
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
                            img = bpy.data.images.load(fpath, check_existing=True)
                            tex_lookup[key.upper() if key != "id" else "Weapon ID"] = (fpath, img)
                            break
        except OSError:
            pass

    psk_stem = os.path.splitext(os.path.basename(psk_path))[0]
    weapon_stem = re.sub(r'_LOD\d+$', '', psk_stem, flags=re.IGNORECASE)
    weapon_stem = re.sub(r'^SK_', '', weapon_stem, flags=re.IGNORECASE)
    weapon_stem_base = re.sub(r'_[A-Z]$', '', weapon_stem, flags=re.IGNORECASE)

    def find_weapon_stem_tex(suffix):
        for stem in (weapon_stem.lower(), weapon_stem_base.lower()):
            target = f"t_{stem}_{suffix.lower()}"
            for param, (fpath, img) in tex_lookup.items():
                if os.path.splitext(os.path.basename(fpath))[0].lower() == target:
                    return param, img
        return None, None

    cr_node = None
    _, cr_img = _find_flat_tex(tex_lookup, "CR", "cr")
    if not cr_img:
        _, cr_img = find_weapon_stem_tex("cr")
    if cr_img:
        cr_node = nodes.new("ShaderNodeTexImage")
        cr_node.image = cr_img
        cr_node.label = "CR (Colour/Roughness)"
        cr_node.interpolation = "Cubic"
        cr_node.location = (COL_TEX, Y_CR)
        links.new(cr_node.outputs["Alpha"], principled.inputs["Roughness"])

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
        if utils.ensure_node_group("NormalFlipper"):
            flipper = nodes.new("ShaderNodeGroup")
            flipper.node_tree = bpy.data.node_groups["NormalFlipper"]
            flipper.location = (COL_UTIL, Y_N)
            links.new(normal_node.outputs["Color"], flipper.inputs[0])
            nm_in = flipper.outputs[0]
        else:
            nm_in = normal_node.outputs["Color"]
        nm_node = nodes.new("ShaderNodeNormalMap")
        nm_node.location = (COL_MIX, Y_N)
        try:
            nm_node.convention = 'DIRECTX'
        except Exception:
            pass
        links.new(nm_in, nm_node.inputs["Color"])
        links.new(nm_node.outputs["Normal"], principled.inputs["Normal"])
        links.new(normal_node.outputs["Alpha"], principled.inputs["Metallic"])

        if normal_type in ("nom", "nem", "nam"):
            sep_node = nodes.new("ShaderNodeSeparateColor")
            sep_node.location = (COL_UTIL, Y_N - 280)
            links.new(normal_node.outputs["Color"], sep_node.inputs["Color"])
            if normal_type == "nom":
                mul_node = nodes.new("ShaderNodeMix")
                mul_node.data_type = 'RGBA'
                mul_node.blend_type = 'MULTIPLY'
                mul_node.location = (COL_MIX, Y_N + 220)
                mul_node.inputs["Factor"].default_value = 1.0
                if cr_node:
                    links.new(cr_node.outputs["Color"], mul_node.inputs[6])
                links.new(sep_node.outputs["Blue"], mul_node.inputs[7])
                links.new(mul_node.outputs[2], principled.inputs["Base Color"])
            elif normal_type == "nem":
                links.new(sep_node.outputs["Blue"], principled.inputs["Emission Strength"])
                if cr_node:
                    links.new(cr_node.outputs["Color"], principled.inputs["Emission Color"])
                    links.new(cr_node.outputs["Color"], principled.inputs["Base Color"])
            elif normal_type == "nam":
                links.new(sep_node.outputs["Blue"], principled.inputs["Alpha"])
                if cr_node:
                    links.new(cr_node.outputs["Color"], principled.inputs["Base Color"])

    id_node = None
    _, id_img = _find_flat_tex(tex_lookup, "Weapon ID", "ID", "id")
    if not id_img:
        _, id_img = find_weapon_stem_tex("id")
    if id_img:
        id_img.colorspace_settings.name = "Non-Color"
        id_node = nodes.new("ShaderNodeTexImage")
        id_node.image = id_img
        id_node.label = "ID Map"
        id_node.interpolation = "Cubic"
        id_node.location = (COL_TEX, Y_ID)

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
        ch_key = param
        for short in COLOUR_CHANNELS:
            if param.endswith(short) or param == short:
                ch_key = short
                break
        else:
            continue
        switch_name = MASK_SWITCHES.get(ch_key, '')
        if switch_name and not switches.get(switch_name, True):
            continue
        zone_colours.append((COLOUR_CHANNELS[ch_key], rgba, param))

    base_color_linked = False
    if cr_node and zone_colours:
        sep_node = None
        if id_node:
            sep_node = nodes.new("ShaderNodeSeparateColor")
            sep_node.label = "ID Channels"
            sep_node.location = (COL_UTIL, Y_ID)
            links.new(id_node.outputs["Color"], sep_node.inputs["Color"])

        ch_names = {0: "Red", 1: "Green", 2: "Blue"}
        current_out = cr_node.outputs["Color"]
        mul_x = COL_MIX
        for i, (ch_idx, rgba, param) in enumerate(zone_colours):
            rgb_node = nodes.new("ShaderNodeRGB")
            rgb_node.label = param
            rgb_node.outputs[0].default_value = rgba
            rgb_node.location = (mul_x - 220, Y_TINT - 280 - i * 200)
            mul_node = nodes.new("ShaderNodeMix")
            mul_node.data_type = 'RGBA'
            mul_node.blend_type = 'MULTIPLY'
            mul_node.label = f"Tint {param}"
            mul_node.location = (mul_x, Y_TINT - i * 200)
            mul_node.inputs["Factor"].default_value = 1.0
            links.new(current_out, mul_node.inputs[6])
            links.new(rgb_node.outputs[0], mul_node.inputs[7])
            if sep_node:
                if ch_idx in ch_names and ch_names[ch_idx] in sep_node.outputs:
                    links.new(sep_node.outputs[ch_names[ch_idx]], mul_node.inputs["Factor"])
                elif ch_idx == 3 and id_node:
                    links.new(id_node.outputs["Alpha"], mul_node.inputs["Factor"])
            current_out = mul_node.outputs[2]
            mul_x += 280
        links.new(current_out, principled.inputs["Base Color"])
        base_color_linked = True
    elif cr_node and normal_type not in ("nom", "nem", "nam"):
        links.new(cr_node.outputs["Color"], principled.inputs["Base Color"])
        base_color_linked = True
    elif cr_node and not base_color_linked and normal_type is None:
        links.new(cr_node.outputs["Color"], principled.inputs["Base Color"])

    # Wear then EXX so each can insert into the albedo/roughness chain
    _, wear_img = _find_flat_tex(tex_lookup, "Wear", "wear")
    if not wear_img:
        _, wear_img = find_weapon_stem_tex("wear")
    if wear_img:
        _apply_wear_map(
            nodes, links, principled, wear_img, scalars,
            col_tex=COL_TEX, col_util=COL_UTIL, col_mix=COL_MIX, row_y=Y_WEAR,
        )

    _, ex_img = _find_flat_tex(tex_lookup, "EX", "EXX", "ex", "exx")
    if not ex_img:
        _, ex_img = find_weapon_stem_tex("exx")
    if not ex_img:
        _, ex_img = find_weapon_stem_tex("ex")
    if ex_img:
        _apply_exx_packing(
            nodes, links, principled, ex_img, scalars,
            col_tex=COL_TEX, col_util=COL_UTIL, col_mix=COL_MIX, row_y=Y_EXX,
        )

    handled = {node.image.filepath for node in nodes if hasattr(node, 'image') and node.image}
    row_u = Y_EXTRA
    for param, (fpath, img) in tex_lookup.items():
        if fpath in handled:
            continue
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param
        node.interpolation = "Cubic"
        node.location = (COL_TEX - 400, row_u)
        row_u -= 320

    colour_channel_ends = tuple(COLOUR_CHANNELS.keys())
    for j, (param, rgba) in enumerate(
        [(p, r) for p, r in mi['colours']
         if p not in COLOUR_CHANNELS and not any(p.endswith(k) for k in colour_channel_ends)]
    ):
        rgb_node = nodes.new("ShaderNodeRGB")
        rgb_node.label = param
        rgb_node.outputs[0].default_value = rgba
        rgb_node.location = (COL_OUT + 200 + (j % 2) * 240, 200 - (j // 2) * 200)


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

    # If ObjectPath resolve still failed, scan the MaterialLibrary enemies folder directly
    if not any("_nao" in os.path.basename(fp).lower() for _, (fp, _) in tex_lookup.items()):
        try:
            for folder in local_folders:
                for fname in os.listdir(folder):
                    fl = fname.lower()
                    if fl.endswith(".png") and ("enemydecals" in fl or "decals" in fl):
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


def _setup_enemy_scan_display_material(mat, mi_path: str):
    """Shared Arc enemy ScanDisplay: procedural emissive scanlines.

    MIs (MI_EnemyScanDisplay_*) are often IsNull with no textures — look comes
    from M_EnemyPreset_ScanDisplay (Tiling / ScanSpeed / AlertnessState / Loop).
    """
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    scalars = mi.get("scalars") or {}
    switches = mi.get("switches") or {}

    COL_GEN, COL_UTIL, COL_MIX, COL_BSDF, COL_OUT = -1100, -650, -200, 350, 650

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    principled.inputs["Base Color"].default_value = (0.01, 0.02, 0.03, 1.0)
    principled.inputs["Metallic"].default_value = 0.0
    principled.inputs["Roughness"].default_value = 0.35
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    tiling = float(scalars.get("Tiling", 64.0) or 64.0)
    scan_speed = float(scalars.get("ScanSpeed", 0.5) or 0.5)
    alert = min(max(float(scalars.get("AlertnessState", 0.0) or 0.0), 0.0), 1.0)
    do_loop = bool(switches.get("Loop", False))
    do_blink = bool(switches.get("Blink", False))
    do_glitch = bool(switches.get("Glitch Effect", switches.get("Glitch", False)))
    glitch_amt = float(scalars.get("GlitchAmount", 0.0) or 0.0)

    # Calm cyan → alert amber/red
    calm = (0.15, 0.85, 1.0, 1.0)
    alert_c = (1.0, 0.28, 0.05, 1.0)
    tint = (
        calm[0] * (1.0 - alert) + alert_c[0] * alert,
        calm[1] * (1.0 - alert) + alert_c[1] * alert,
        calm[2] * (1.0 - alert) + alert_c[2] * alert,
        1.0,
    )
    tint_rgb = nodes.new("ShaderNodeRGB")
    tint_rgb.label = "Scan Tint"
    tint_rgb.outputs[0].default_value = tint
    tint_rgb.location = (COL_MIX, 280)

    texcoord = nodes.new("ShaderNodeTexCoord")
    texcoord.location = (COL_GEN, 120)

    # Raw UV (0-1 space) for fade mask, and scaled UV for line tiling
    sep_uv = nodes.new("ShaderNodeSeparateXYZ")
    sep_uv.label = "UV Raw"
    sep_uv.location = (COL_GEN + 220, -200)
    links.new(texcoord.outputs["UV"], sep_uv.inputs["Vector"])

    mapping = nodes.new("ShaderNodeMapping")
    # Use a moderate line count; UE Tiling of 64 is per-game-unit, not pixels —
    # dividing by 32 gives ~2-4 visible lines per UV tile which looks correct.
    line_density = max(tiling / 32.0, 2.0)
    mapping.label = f"Scan UV ×{line_density:.1f}"
    mapping.location = (COL_GEN + 220, 120)
    mapping.inputs["Scale"].default_value = (1.0, line_density, 1.0)
    links.new(texcoord.outputs["UV"], mapping.inputs["Vector"])

    sep = nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (COL_UTIL, 120)
    links.new(mapping.outputs["Vector"], sep.inputs["Vector"])

    # Horizontal scanlines from V (clamped fract → sharp bands)
    lines = nodes.new("ShaderNodeMath")
    lines.operation = 'MULTIPLY'
    lines.label = "Line Frac Scale"
    lines.location = (COL_UTIL + 200, 180)
    lines.inputs[1].default_value = 1.0   # already scaled by mapping
    links.new(sep.outputs["Y"], lines.inputs[0])

    frac = nodes.new("ShaderNodeMath")
    try:
        frac.operation = 'FRACT'
    except Exception:
        frac.operation = 'MODULO'
        frac.inputs[1].default_value = 1.0
    frac.label = "Line Fract"
    frac.location = (COL_UTIL + 400, 180)
    links.new(lines.outputs[0], frac.inputs[0])

    line_wave = nodes.new("ShaderNodeMath")
    line_wave.operation = 'GREATER_THAN'
    line_wave.label = "Scanline Mask"
    line_wave.location = (COL_MIX, 160)
    line_wave.inputs[1].default_value = 0.60   # 40% duty cycle → clear dark gap between lines
    links.new(frac.outputs[0], line_wave.inputs[0])

    # UV edge fade: smoothstep in V so lines fade at top/bottom — reduces stripy look
    # on meshes where the scan display UV wraps across unrelated polygons.
    v_fade_lo = nodes.new("ShaderNodeMath")
    v_fade_lo.operation = 'SMOOTHMIN' if hasattr(bpy.types.ShaderNodeMath, 'operation') else 'MULTIPLY'
    try:
        v_fade_lo.operation = 'SMOOTH_MIN'
    except Exception:
        pass
    # Simpler: clamp(V * 4, 0, 1) * clamp((1-V) * 4, 0, 1)
    v_lo = nodes.new("ShaderNodeMath")
    v_lo.operation = 'MULTIPLY'
    v_lo.label = "V fade lo"
    v_lo.location = (COL_UTIL + 200, -160)
    v_lo.inputs[1].default_value = 3.5
    links.new(sep_uv.outputs["Y"], v_lo.inputs[0])

    v_lo_clamp = nodes.new("ShaderNodeMath")
    v_lo_clamp.operation = 'MINIMUM'
    v_lo_clamp.location = (COL_UTIL + 380, -160)
    v_lo_clamp.inputs[1].default_value = 1.0
    links.new(v_lo.outputs[0], v_lo_clamp.inputs[0])

    v_hi_inv = nodes.new("ShaderNodeMath")
    v_hi_inv.operation = 'SUBTRACT'
    v_hi_inv.label = "1 - V"
    v_hi_inv.location = (COL_UTIL + 200, -300)
    v_hi_inv.inputs[0].default_value = 1.0
    links.new(sep_uv.outputs["Y"], v_hi_inv.inputs[1])

    v_hi = nodes.new("ShaderNodeMath")
    v_hi.operation = 'MULTIPLY'
    v_hi.label = "V fade hi"
    v_hi.location = (COL_UTIL + 380, -300)
    v_hi.inputs[1].default_value = 3.5
    links.new(v_hi_inv.outputs[0], v_hi.inputs[0])

    v_hi_clamp = nodes.new("ShaderNodeMath")
    v_hi_clamp.operation = 'MINIMUM'
    v_hi_clamp.location = (COL_UTIL + 560, -300)
    v_hi_clamp.inputs[1].default_value = 1.0
    links.new(v_hi.outputs[0], v_hi_clamp.inputs[0])

    uv_fade = nodes.new("ShaderNodeMath")
    uv_fade.operation = 'MULTIPLY'
    uv_fade.label = "UV Edge Fade"
    uv_fade.location = (COL_MIX, -200)
    links.new(v_lo_clamp.outputs[0], uv_fade.inputs[0])
    links.new(v_hi_clamp.outputs[0], uv_fade.inputs[1])

    # Moving scan band driven by frame
    frame = nodes.new("ShaderNodeValue")
    frame.label = "Frame"
    frame.location = (COL_GEN, -500)
    try:
        frame.outputs[0].default_value = 0.0
        drv = frame.outputs[0].driver_add("default_value")
        drv.driver.type = 'SCRIPTED'
        drv.driver.expression = f"(frame * {scan_speed * 0.02:.5f})" + (" % 1.0" if do_loop else "")
    except Exception:
        frame.outputs[0].default_value = 0.0

    band_add = nodes.new("ShaderNodeMath")
    band_add.operation = 'ADD'
    band_add.label = "V + Scroll"
    band_add.location = (COL_UTIL, -460)
    links.new(sep_uv.outputs["Y"], band_add.inputs[0])
    links.new(frame.outputs[0], band_add.inputs[1])

    band_frac = nodes.new("ShaderNodeMath")
    try:
        band_frac.operation = 'FRACT'
    except Exception:
        band_frac.operation = 'MODULO'
        band_frac.inputs[1].default_value = 1.0
    band_frac.label = "Band Fract"
    band_frac.location = (COL_UTIL + 200, -460)
    links.new(band_add.outputs[0], band_frac.inputs[0])

    band_dist = nodes.new("ShaderNodeMath")
    band_dist.operation = 'SUBTRACT'
    band_dist.location = (COL_UTIL + 400, -460)
    band_dist.inputs[1].default_value = 0.5
    links.new(band_frac.outputs[0], band_dist.inputs[0])

    band_abs = nodes.new("ShaderNodeMath")
    band_abs.operation = 'ABSOLUTE'
    band_abs.location = (COL_MIX, -380)
    links.new(band_dist.outputs[0], band_abs.inputs[0])

    band_inv = nodes.new("ShaderNodeMath")
    band_inv.operation = 'SUBTRACT'
    band_inv.label = "Scan Band"
    band_inv.location = (COL_MIX + 180, -380)
    band_inv.inputs[0].default_value = 1.0
    links.new(band_abs.outputs[0], band_inv.inputs[1])

    band_pow = nodes.new("ShaderNodeMath")
    band_pow.operation = 'POWER'
    band_pow.location = (COL_MIX + 360, -380)
    band_pow.inputs[1].default_value = 10.0
    links.new(band_inv.outputs[0], band_pow.inputs[0])

    # Combine lines + scroll band, then apply UV edge fade
    comb = nodes.new("ShaderNodeMath")
    comb.operation = 'ADD'
    comb.label = "Lines + Band"
    comb.location = (COL_MIX + 180, 100)
    links.new(line_wave.outputs[0], comb.inputs[0])
    links.new(band_pow.outputs[0], comb.inputs[1])

    comb_clamp = nodes.new("ShaderNodeMath")
    comb_clamp.operation = 'MINIMUM'
    comb_clamp.location = (COL_MIX + 360, 100)
    comb_clamp.inputs[1].default_value = 1.0
    links.new(comb.outputs[0], comb_clamp.inputs[0])

    # Multiply by UV fade mask so lines disappear at UV seams/borders
    faded = nodes.new("ShaderNodeMath")
    faded.operation = 'MULTIPLY'
    faded.label = "Faded Scan"
    faded.location = (COL_MIX + 540, 100)
    links.new(comb_clamp.outputs[0], faded.inputs[0])
    links.new(uv_fade.outputs[0], faded.inputs[1])

    emit_mul = nodes.new("ShaderNodeMix")
    emit_mul.data_type = 'RGBA'
    emit_mul.blend_type = 'MULTIPLY'
    emit_mul.label = "Tint × Scan"
    emit_mul.location = (COL_MIX + 760, 200)
    emit_mul.inputs["Factor"].default_value = 1.0
    links.new(tint_rgb.outputs[0], emit_mul.inputs[6])
    scan_rgb = nodes.new("ShaderNodeCombineColor")
    scan_rgb.location = (COL_MIX + 560, 40)
    links.new(faded.outputs[0], scan_rgb.inputs["Red"])
    links.new(faded.outputs[0], scan_rgb.inputs["Green"])
    links.new(faded.outputs[0], scan_rgb.inputs["Blue"])
    links.new(scan_rgb.outputs["Color"], emit_mul.inputs[7])
    links.new(emit_mul.outputs[2], principled.inputs["Emission Color"])

    strength = 12.0 + alert * 10.0
    if do_blink:
        strength *= 0.85
    if do_glitch and glitch_amt > 0:
        strength *= 1.0 + min(glitch_amt, 2.0) * 0.25
    if "Emission Strength" in principled.inputs:
        principled.inputs["Emission Strength"].default_value = strength

    # Optional MI textures (rare) still get loaded for inspection / future use
    row = 450
    seen = set()
    for param, obj_path in mi.get("textures") or []:
        if not obj_path or obj_path.startswith("/Engine/"):
            continue
        fpath = textures.find_texture_from_object_path(obj_path + ".0")
        if not fpath or fpath in seen:
            continue
        seen.add(fpath)
        img = bpy.data.images.load(fpath, check_existing=True)
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param
        node.interpolation = "Cubic"
        node.location = (COL_GEN - 400, row)
        row -= 320


def _setup_weapon_screen_material(mat, mi_path: str):
    """Weapon screens + enemy ScanDisplay share the procedural scan setup."""
    _setup_enemy_scan_display_material(mat, mi_path)


def setup_weapon_material(obj, psk_path: str):
    """Assign per-slot materials for firearms and enemies from SK SkeletalMaterials."""
    slots = _parse_sk_material_slots(psk_path)
    if not slots:
        print(f"Arc Raiders PSK Importer: No SK material slots found for '{os.path.basename(psk_path)}'")
        return

    used_indices = set()
    for idx, (slot_name, mi_stem, mi_path) in enumerate(slots):
        target_slot, slot_i = _match_material_slot(obj, slot_name, idx, used_indices)
        if target_slot is None:
            print(f"Arc Raiders PSK Importer: No Blender slot for '{slot_name}' (index {idx})")
            continue
        used_indices.add(slot_i)

        mat = bpy.data.materials.new(name=f"{obj.name}_{mi_stem}_Mat")
        mat.use_nodes = True
        target_slot.material = mat
        mi_stem_lower = mi_stem.lower()
        slot_lower = (slot_name or "").lower()

        if not mi_path:
            print(f"Arc Raiders PSK Importer: MI JSON not found for slot '{slot_name}' ({mi_stem})")
            continue

        if 'emissive' in mi_stem_lower or 'light' in mi_stem_lower:
            _setup_weapon_emissive_material(mat, mi_path)
        elif (
            'scandisplay' in mi_stem_lower
            or 'scandisplay' in slot_lower
            or 'screen' in mi_stem_lower
        ):
            _setup_enemy_scan_display_material(mat, mi_path)
        elif 'decal' in mi_stem_lower or slot_lower == 'decals' or 'decal' in slot_lower:
            _setup_enemy_decal_material(mat, mi_path, psk_path)
        else:
            # Flat MIs that still use the NAO+Height decal preset
            mi_probe = _parse_flat_mi_json(mi_path)
            if _is_enemy_decal_mi(mi_probe):
                _setup_enemy_decal_material(mat, mi_path, psk_path)
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