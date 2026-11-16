"""Native M_FurShells_Weapons (weapon skin fur, Renegade Bells): dry static surface and authored shell offsets.

Cooked SM5 base pass of MI_BattleRifle_LeverAction_01_Addon_Fur_Bells (resource B89FB7BC, PS 9F3DC9F9, VS 6CA5E042):
the character M_FurShells surface (fur_shells.py) without wind, plus an edge fade from COL0.R and EdgeFadeNoise.
FlowmapStrength reaches the shader x0.01. Shells are authored in the mesh (shell index = floor(UE UV0.V), base
layer 0); the VS pushes each shell along its normal by index*Length*G*(1+(G-1)*PinchStrength), G = COL0 green.
"""
from __future__ import annotations
import json
import bpy
import numpy as np
from . import fur_shells
from .. import utils

MASTER = 'M_FurShells_Weapons'
SCHEMA = 'weapon-fur-shells-v1'
OFFSET = 'arc_weapon_fur_offset'
MODIFIER = 'ARC Weapon Fur Shell Offset'
# M_FurShells_Weapons cooked defaults (CachedExpressionData ScalarValues/VectorValues/TextureValues).
_SCALARS = {'Metallic': .4, 'SubSurfaceScattering': .88, 'Length': .15, 'FurTiling': 9., 'Roughness': .56, 'FlowmapStrength': 0.,
            'FlowmapTiling': 3., 'ColorTiling': 1., 'AlphaCutoff': .378122, 'Fresnel': .18, 'Sheen': .25, 'ShellCount': 6.,
            'MaskAlphaCutoff': 0., 'EdgeFadeStrength': 1., 'EdgeFadeNoiseTiling': 20., 'EdgeFadeNoiseStrength': .5, 'PinchStrength': 1.}
_COLORS = {'FresnelColor': (.463542,) * 3 + (1.,), 'SheenColor': (.192708,) * 3 + (1.,), 'ColorTint': (.578125,) * 3 + (1.,),
           'RootColor': (0., 0., 0., 1.), 'TipColor': (1., 1., 1., 1.)}
_TEXTURES = {'FurMask': '/Game/Pioneer/MaterialLibrary/Character/CustomMaterials/Textures/T_FurMask_01_A.T_FurMask_01_A',
             'Color': '/Game/Pioneer/MaterialLibrary/Character/LayeredMaterials/Textures/Color/T_White_C.T_White_C',
             'Flowmap': '/Game/Pioneer/MaterialLibrary/Textures/Default/T_Default_01_M.T_Default_01_M',
             'EdgeFadeNoise': '/Game/Pioneer/MaterialLibrary/Character/CustomMaterials/Textures/T_FurMask_04_A.T_FurMask_04_A'}
_GRADIENT = '/Game/Pioneer/MaterialLibrary/Character/CustomMaterials/Textures/T_Gradient_Anisotropy_M'


def is_weapon_fur(master):
    return str(master or '').replace('\\', '/').rsplit('/', 1)[-1].split('.', 1)[0] == MASTER


def profile(mi_path):
    """MI values over the master defaults; flat (.json Parameters) and full (Exports) MI layouts."""
    with open(mi_path, encoding='utf-8-sig') as fh:
        data = json.load(fh)
    s, c, t = dict(_SCALARS), dict(_COLORS), dict(_TEXTURES)
    flat = data if isinstance(data, dict) and ('Parameters' in data or ('Type' not in data and 'Exports' not in data)) else {}
    props = {} if flat else (utils.first_ue_export(data, 'MaterialInstanceConstant') or {}).get('Properties') or {}
    for item in props.get('ScalarParameterValues', []):
        s[item['ParameterInfo']['Name']] = float(item['ParameterValue'])
    for item in props.get('VectorParameterValues', []):
        c[item['ParameterInfo']['Name']] = tuple(float(item['ParameterValue'].get(k, 1)) for k in 'RGBA')
    for item in props.get('TextureParameterValues', []):
        t[item['ParameterInfo']['Name']] = fur_shells.textures._material_ref_path(item.get('ParameterValue'))
    params = flat.get('Parameters') or flat
    s.update({k: float(v) for k, v in (params.get('Scalars') or {}).items()})
    c.update({k: tuple(float(v.get(ch, 1)) for ch in 'RGBA') for k, v in (params.get('Colors') or {}).items()})
    t.update({k: fur_shells.textures._material_ref_path(v) for k, v in (flat.get('Textures') or {}).items() if k in _TEXTURES})
    bpo = props.get('BasePropertyOverrides') or (params.get('Properties') or {}).get('BasePropertyOverrides') or {}
    return {'scalars': s, 'colors': c, 'textures': t, 'clip': float(bpo.get('OpacityMaskClipValue', .3333)),
            'two_sided': bool(bpo.get('TwoSided', False))}


def _stencil_grade(color):
    """The weapon paint grade (material_runtime.weapons PAINT_SATURATION / PAINT_VALUE, fitted to captures) on an
    authored fur colour: the Bells fur albedo reads Lab b* ~6 against ~16 in the capture."""
    from .native import weapons
    s, v = getattr(weapons, 'PAINT_SATURATION', 1.), getattr(weapons, 'PAINT_VALUE', 1.)
    y = .2126 * color[0] + .7152 * color[1] + .0722 * color[2]
    return tuple(max(y + (x - y) * s, 0.) * v for x in color[:3]) + tuple(color[3:])


def setup_material(mat, mi_path):
    p = profile(mi_path)
    s, c, t = p['scalars'], p['colors'], p['textures']
    c = {k: _stencil_grade(v) if k in ('RootColor', 'TipColor', 'FresnelColor') else v for k, v in c.items()}
    from .fur_controls import material_overrides, color_control, value_control
    saved = material_overrides(mat)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    g = fur_shells._Graph(mat, mi_path)
    uv = g.nodes.new('ShaderNodeUVMap'); uv.uv_map = 'UV0'; uv.label = 'Authored shell UV0'
    ux, by, _ = g.split(uv.outputs[0]); uy = g.m('SUBTRACT', 1, by, 'Original Unreal V')
    index = g.m('FLOOR', uy, label='Authored integer shell index')
    base = g.combine(ux, g.m('SUBTRACT', uy, index, 'Fractional strand UV'))
    vertex = g.nodes.new('ShaderNodeVertexColor'); vertex.layer_name = 'COL0'; vertex.label = 'Authored COL0 (R edge, G length)'
    col = g.nodes.new('ShaderNodeSeparateColor'); g.links.new(vertex.outputs['Color'], col.inputs['Color'])
    red, green = col.outputs['Red'], col.outputs['Green']
    # PS 56-63: flow offset (2*index*flow-1)*FlowmapStrength*0.01; mask at (base+flow)*FurTiling.
    flow = g.sample(t['Flowmap'], g.v('SCALE', base, s['FlowmapTiling']), 'Native Flowmap')
    strength = s['FlowmapStrength'] * .01
    flowvec = g.combine(*(g.m('MULTIPLY', g.m('SUBTRACT', g.m('MULTIPLY', g.m('MULTIPLY', flow[k], index), 2), 1), strength) for k in 'rg'))
    strand = g.v('ADD', base, flowvec)
    mask = g.sample(t['FurMask'], g.v('SCALE', strand, s['FurTiling']), 'Native FurMask')['r']
    # PS 37-51: rim from 1-sat(N.V): sheen gradient and fresnel, scaled by mask*V*0.2 (PS 65-67).
    geometry = g.nodes.new('ShaderNodeNewGeometry')
    facing = g.m('SUBTRACT', 1, g.sat(g.v('DOT_PRODUCT', geometry.outputs['Normal'], geometry.outputs['Incoming'])), 'Native facing rim')
    sheen = g.m('MULTIPLY', g.m('SUBTRACT', facing, s['Sheen']), 1 / (1 - s['Sheen']))
    gradient = g.sample(_GRADIENT, g.combine(sheen, sheen), 'Native sheen gradient')['r']
    fresnel = g.sat(g.m('MULTIPLY', g.m('SUBTRACT', facing, s['Fresnel']), 1 / (1 - s['Fresnel'])))
    rim = g.v('ADD', g.v('SCALE', g.color(c['SheenColor']), gradient), g.v('SCALE', g.color(c['FresnelColor']), fresnel))
    rim = g.v('SCALE', rim, g.m('MULTIPLY', g.m('MULTIPLY', uy, .2), mask))
    # PS 68-79: root->tip by sat(index/(ShellCount+1)) + 1-G; Color at strand*ColorTiling * ColorTint.
    factor = g.m('ADD', g.sat(g.m('DIVIDE', index, s['ShellCount'] + 1)), g.m('SUBTRACT', 1, green))
    tint = g.lerp_color(color_control(g, 'Root Color', c['RootColor'], saved), color_control(g, 'Tip Color', c['TipColor'], saved), factor)
    color = g.sample(t['Color'], g.v('SCALE', strand, s['ColorTiling']), 'Native Color', data='furmask' in t['Color'].lower())['color']
    albedo = g.clamp_color(g.v('ADD', rim, g.v('MULTIPLY', g.v('MULTIPLY', color, color_control(g, 'Color Tint', c['ColorTint'], saved)), tint)))
    # PS 81-82: the cooked bias is 0.12 for Roughness 0.56 / SubSurfaceScattering 0.88 (1-SSS; no MI separates them yet).
    rough = g.sat(g.m('ADD', g.m('SUBTRACT', 1, g.m('MULTIPLY', gradient, mask)), 1 - s['SubSurfaceScattering']))
    # PS 83-95: keep mask*sat(G+1-MaskAlphaCutoff)/(index*AlphaCutoff) * edge fade >= clip; index 0 divides by 0 and is kept.
    ratio = g.m('DIVIDE', g.m('MULTIPLY', mask, g.sat(g.m('ADD', green, 1 - s['MaskAlphaCutoff']))), g.m('MAXIMUM', g.m('MULTIPLY', index, s['AlphaCutoff']), 1e-8))
    noise = g.sample(t['EdgeFadeNoise'], g.v('SCALE', g.combine(ux, uy), s['EdgeFadeNoiseTiling']), 'Native EdgeFadeNoise')['r']
    edge = g.sat(g.m('SUBTRACT', red, g.m('MULTIPLY', g.m('MULTIPLY', noise, g.m('SUBTRACT', 1, red)), s['EdgeFadeNoiseStrength'])))
    fade = g.m('ADD', 1, g.m('MULTIPLY', g.m('SUBTRACT', edge, 1), s['EdgeFadeStrength']), 'Native edge fade')
    keep = g.m('GREATER_THAN', g.m('MULTIPLY', ratio, fade), p['clip'] - 1e-6)
    alpha = g.m('MAXIMUM', g.m('LESS_THAN', index, .5), keep, 'Native masked shell cutoff')
    bsdf = g.nodes.new('ShaderNodeBsdfPrincipled'); bsdf.label = 'Native dry weapon fur (BSDF approximation)'
    rough = g.sat(g.m('ADD', rough, value_control(g, 'Roughness Offset', 0., saved)))
    for name, value in [('Base Color', albedo), ('Roughness', rough), ('Metallic', min(max(s['Metallic'], 0.), 1.)),
                        ('Specular IOR Level', g.m('MINIMUM', g.m('MULTIPLY', mask, mask), 1)), ('Alpha', alpha)]:
        g.put(bsdf.inputs[name], value)
    out = g.nodes.new('ShaderNodeOutputMaterial'); g.links.new(bsdf.outputs[0], out.inputs['Surface'])
    from .common import _set_material_alpha_mode
    _set_material_alpha_mode(mat, 'HASHED', threshold=p['clip'], two_sided=p['two_sided'])
    mat.use_backface_culling = not p['two_sided']
    mat['arc_weapon_fur'] = json.dumps({'schema': SCHEMA, 'Length': s['Length'], 'PinchStrength': s['PinchStrength']}, sort_keys=True)
    mat['arc_mi_path'] = str(mi_path)
    if g.missing:
        mat['arc_fur_missing_textures'] = json.dumps(sorted(set(g.missing)))
    depth, lanes = {}, {}
    for node in g.nodes:
        d = max((depth.get(link.from_node.name, 0) + 1 for socket in node.inputs for link in socket.links), default=0)
        depth[node.name] = d; lane = lanes.get(d, 0); lanes[d] = lane + 1; node.location = (d * 230, -lane * 190)
    return mat


def apply_shell_offsets(obj):
    """VS shell push along the authored normal, as a point attribute + Set Position modifier. Idempotent."""
    if obj is None or obj.type != 'MESH':
        return 0
    fur = {i: json.loads(s.material['arc_weapon_fur']) for i, s in enumerate(obj.material_slots)
           if s.material and 'arc_weapon_fur' in s.material}
    if not fur or 'COL0' not in obj.data.color_attributes or 'UV0' not in obj.data.uv_layers:
        return 0
    me = obj.data
    loops = len(me.loops)
    uv = np.empty(loops * 2, np.float32); me.uv_layers['UV0'].data.foreach_get('uv', uv)
    lv = np.empty(loops, np.int32); me.loops.foreach_get('vertex_index', lv)
    normals = np.empty(loops * 3, np.float32); me.corner_normals.foreach_get('vector', normals)
    mi = np.empty(len(me.polygons), np.int32); me.polygons.foreach_get('material_index', mi)
    lt = np.empty(len(me.polygons), np.int32); me.polygons.foreach_get('loop_total', lt)
    loop_mat = np.repeat(mi, lt)
    col = me.color_attributes['COL0']
    rgba = np.empty(len(col.data) * 4, np.float32); col.data.foreach_get('color', rgba); rgba = rgba.reshape(-1, 4)
    green = rgba[lv, 1] if col.domain == 'CORNER' else rgba[:, 1][lv]
    index = np.floor(1 - uv[1::2])
    distance = np.zeros(loops)
    for slot, p in fur.items():
        m = loop_mat == slot
        g = green[m]
        distance[m] = index[m] * p['Length'] * g * (1 + (g - 1) * p['PinchStrength'])
    scale = float(me.get('arc_uemodel_scale', .01))
    offset = np.zeros((len(me.vertices), 3))
    np.add.at(offset, lv, normals.reshape(-1, 3) * (distance * scale)[:, None])
    count = np.bincount(lv, minlength=len(me.vertices))[:, None]
    offset /= np.maximum(count, 1)
    attr = me.attributes.get(OFFSET) or me.attributes.new(OFFSET, 'FLOAT_VECTOR', 'POINT')
    attr.data.foreach_set('vector', offset.astype(np.float32).ravel())
    mod = obj.modifiers.get(MODIFIER)
    if mod is None:
        group = bpy.data.node_groups.get('ARC Weapon Fur Set Position')
        if group is None:
            group = bpy.data.node_groups.new('ARC Weapon Fur Set Position', 'GeometryNodeTree')
            group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
            group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
            n, l = group.nodes, group.links
            inp, out = n.new('NodeGroupInput'), n.new('NodeGroupOutput')
            named = n.new('GeometryNodeInputNamedAttribute'); named.data_type = 'FLOAT_VECTOR'; named.inputs['Name'].default_value = OFFSET
            setpos = n.new('GeometryNodeSetPosition')
            l.new(inp.outputs['Geometry'], setpos.inputs['Geometry']); l.new(named.outputs['Attribute'], setpos.inputs['Offset'])
            l.new(setpos.outputs['Geometry'], out.inputs['Geometry'])
        mod = obj.modifiers.new(MODIFIER, 'NODES'); mod.node_group = group
        arm = next((x for x in obj.modifiers if x.type == 'ARMATURE'), None)
        if arm:
            obj.modifiers.move(list(obj.modifiers).index(mod), list(obj.modifiers).index(arm))
    from .fur_controls import ensure_length_control
    ensure_length_control(mod)
    arm = next((item for item in obj.modifiers if item.type == 'ARMATURE'), None)
    if arm and list(obj.modifiers).index(mod) > list(obj.modifiers).index(arm):
        obj.modifiers.move(list(obj.modifiers).index(mod), list(obj.modifiers).index(arm))
    obj['arc_weapon_fur_max_offset_m'] = float(np.linalg.norm(offset, axis=1).max())
    return int((np.linalg.norm(offset, axis=1) > 0).sum())
