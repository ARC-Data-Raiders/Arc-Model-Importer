"""Blender playback rigs for Niagara caches (spec N-O5 sections 5-7, 9). Requires bpy; maths mirrors niagara_render_ref.py.

build_system(cache_dir) creates a root empty at the bake origin with one rig object per sprite/mesh stream (Geometry Nodes reading
f_####.ply per frame) and a keyframed point-light pool per light stream. Every rig modifier has a 'Realize' input for Gate R0.
"""
import json
import math
from pathlib import Path

import bpy
import numpy as np

import niagara_render_ref as ref

PREVIEW_MATERIAL = 'DR_NX_Preview'
PLACEHOLDER_MESH = 'DR_NX_MeshPlaceholder'


class G:
    """Small node-building helper for one GeometryNodeTree."""

    def __init__(self, tree):
        self.t, self.n, self.l = tree, tree.nodes, tree.links

    def node(self, idname, **props):
        node = self.n.new(idname)
        for key, value in props.items():
            setattr(node, key, value)
        return node

    def link(self, a, b):
        self.l.new(a, b)
        return b

    def _in(self, node, i, value):
        sock = node.inputs[i]
        if isinstance(value, bpy.types.NodeSocket):
            self.link(value, sock)
        else:
            sock.default_value = value

    def attr(self, name, data_type='FLOAT'):
        node = self.node('GeometryNodeInputNamedAttribute', data_type=data_type)
        node.inputs['Name'].default_value = name
        return node.outputs['Attribute']

    def math(self, op, a, b=0.0, c=None):
        node = self.node('ShaderNodeMath', operation=op)
        self._in(node, 0, a)
        self._in(node, 1, b)
        if c is not None:
            self._in(node, 2, c)
        return node.outputs[0]

    def vmath(self, op, a, b=(0.0, 0.0, 0.0), scale=None):
        node = self.node('ShaderNodeVectorMath', operation=op)
        self._in(node, 0, a)
        self._in(node, 1, b)
        if scale is not None:
            self._in(node, 'Scale', scale)
        return node.outputs['Value' if op in ('LENGTH', 'DOT_PRODUCT') else 'Vector']

    def cross_ue(self, a, b):
        return self.vmath('SCALE', self.vmath('CROSS_PRODUCT', a, b), scale=-1.0)

    def combine(self, x, y, z):
        node = self.node('ShaderNodeCombineXYZ')
        for i, v in enumerate((x, y, z)):
            self._in(node, i, v)
        return node.outputs[0]

    def vec_attr(self, prefix, n):
        comps = [self.attr('%s_%d' % (prefix, c)) for c in range(n)] + [0.0] * (3 - n)
        return self.combine(*comps[:3])

    def color_attr(self, prefixes):
        node = self.node('FunctionNodeCombineColor')
        for i, p in enumerate(prefixes):
            self._in(node, i, self.attr(p) if isinstance(p, str) else p)
        return node.outputs[0]

    def switch(self, input_type, cond, false, true):
        node = self.node('GeometryNodeSwitch', input_type=input_type)
        self._in(node, 'Switch', cond)
        self._in(node, 'False', false)
        self._in(node, 'True', true)
        return node.outputs[0]

    def store(self, geo, name, data_type, value, domain='POINT'):
        node = self.node('GeometryNodeStoreNamedAttribute', data_type=data_type, domain=domain)
        node.inputs['Name'].default_value = name
        self.link(geo, node.inputs['Geometry'])
        self._in(node, 'Value', value)
        return node.outputs[0]

    def rotate_vector(self, v, rot):
        node = self.node('FunctionNodeRotateVector')
        self._in(node, 'Vector', v)
        self._in(node, 'Rotation', rot)
        return node.outputs[0]


def _new_tree(name):
    tree = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    tree.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    sock = tree.interface.new_socket('Realize', in_out='INPUT', socket_type='NodeSocketBool')
    sock.default_value = False
    return tree


def _stream_points(g, stream_dir, frame_start, frame_end, props, prefix='f_', ribbon=False):
    """Import PLY for the clamped scene frame; empty outside the cache range; drop sentinels and hidden visibility tags."""
    time = g.node('GeometryNodeInputSceneTime')
    clamp = g.node('ShaderNodeClamp')
    g.link(time.outputs['Frame'], clamp.inputs['Value'])
    clamp.inputs['Min'].default_value = float(frame_start)
    clamp.inputs['Max'].default_value = float(frame_end)
    to_int = g.node('FunctionNodeFloatToInt', rounding_mode='ROUND')
    g.link(clamp.outputs[0], to_int.inputs[0])
    fmt = g.node('FunctionNodeFormatString')
    fmt.inputs['Format'].default_value = str(stream_dir).replace('\\', '/') + '/' + prefix + '{frame:04}.ply'
    fmt.format_items.new('INT', 'frame')
    g.link(to_int.outputs[0], fmt.inputs['frame'])
    imp = g.node('GeometryNodeImportPLY')
    g.link(fmt.outputs[0], imp.inputs['Path'])
    below = g.math('LESS_THAN', time.outputs['Frame'], float(frame_start) - 0.5)
    above = g.math('GREATER_THAN', time.outputs['Frame'], float(frame_end) + 0.5)
    outside = g.math('MAXIMUM', below, above)
    if ribbon:  # ribbon mesh sentinel: the only vertex with a zero slice normal
        hidden = g.math('LESS_THAN', g.math('ADD', g.math('ABSOLUTE', g.attr('sl_nx')), g.math('ABSOLUTE', g.attr('sl_ny'))), 0.5)
    else:
        hidden = g.math('LESS_THAN', g.attr('nx_valid'), 0.5)
        tag = g.math('ROUND', g.attr('vistag'))
        hidden = g.math('MAXIMUM', hidden, g.math('SUBTRACT', 1.0, g.math('COMPARE', tag, float(props.get('RendererVisibility', 0)), 0.25)))
    hidden = g.math('MAXIMUM', hidden, outside)
    delete = g.node('GeometryNodeDeleteGeometry', domain='POINT')
    g.link(imp.outputs[0], delete.inputs['Geometry'])
    g.link(hidden, delete.inputs['Selection'])
    return delete.outputs[0]


def _camera(g):
    cam = g.node('GeometryNodeObjectInfo', transform_space='RELATIVE')
    g.link(g.node('GeometryNodeInputActiveCamera').outputs[0], cam.inputs['Object'])
    rot = cam.outputs['Rotation']
    return {'pos': cam.outputs['Location'], 'x': g.rotate_vector((1.0, 0.0, 0.0), rot),
            'y': g.rotate_vector((0.0, 1.0, 0.0), rot), 'z': g.rotate_vector((0.0, 0.0, 1.0), rot)}


def _safe_normalize(g, v, eps):
    """v / max(|v|, eps) (UE SafeNormalize)."""
    length = g.math('MAXIMUM', g.vmath('LENGTH', v), eps)
    return g.vmath('SCALE', v, scale=g.math('DIVIDE', 1.0, length))


def _finish(g, inst, realize_input):
    realize = g.node('GeometryNodeRealizeInstances')
    g.link(inst, realize.inputs['Geometry'])
    out = g.node('NodeGroupOutput')
    g.link(g.switch('GEOMETRY', realize_input, inst, realize.outputs[0]), out.inputs['Geometry'])


def _sort(g, geo, cam, mode):
    if mode not in ('ViewDistance', 'ViewDepth'):
        return geo
    pos = g.node('GeometryNodeInputPosition').outputs[0]
    rel = g.vmath('SUBTRACT', pos, cam['pos'])
    key = g.vmath('LENGTH', rel) if mode == 'ViewDistance' else g.vmath('DOT_PRODUCT', rel, g.vmath('SCALE', cam['z'], scale=-1.0))
    node = g.node('GeometryNodeSortElements', domain='POINT')
    g.link(geo, node.inputs['Geometry'])
    g.link(g.math('MULTIPLY', key, -1.0), node.inputs['Sort Weight'])
    return node.outputs[0]


def _packed(g, geo, info):
    """Store the N-O6 instancer attributes (spec section 9)."""
    pr = info['props']
    for name, prefixes in (('nx_color', ['col_0', 'col_1', 'col_2', 'col_3']),) + tuple(
            ('nx_dyn%d' % k, ['dyn%d_%d' % (k, c) for c in range(4)]) for k in range(4)):
        geo = g.store(geo, name, 'FLOAT_COLOR', g.color_attr(prefixes))
    blend = 1.0 if pr.get('bSubImageBlend', True) else 0.0
    geo = g.store(geo, 'nx_misc', 'FLOAT_COLOR', g.color_attr(['subimg', 'nage', 'matrand', blend]))
    if info['kind'] == 'sprite':
        sub = pr.get('SubImageSize', [1.0, 1.0])
        sx = g.math('MULTIPLY', g.attr('uvs_0'), g.math('SIGN', g.attr('size_0')))
        sy = g.math('MULTIPLY', g.attr('uvs_1'), g.math('SIGN', g.attr('size_1')))
        geo = g.store(geo, 'nx_uvx', 'FLOAT_COLOR', g.color_attr([sx, sy, float(sub[0]), float(sub[1])]))
    return geo


def _quad(g, material):
    """Unit quad with verts at (u, -v, 0), u, v in {0, 1}; nx_quad_uv = (u, v) in UE convention."""
    grid = g.node('GeometryNodeMeshGrid')
    grid.inputs['Size X'].default_value = 1.0
    grid.inputs['Size Y'].default_value = 1.0
    grid.inputs['Vertices X'].default_value = 2
    grid.inputs['Vertices Y'].default_value = 2
    xf = g.node('GeometryNodeTransform')
    g.link(grid.outputs['Mesh'], xf.inputs['Geometry'])
    xf.inputs['Translation'].default_value = (0.5, -0.5, 0.0)
    pos = g.node('GeometryNodeInputPosition').outputs[0]
    sep = g.node('ShaderNodeSeparateXYZ')
    g.link(pos, sep.inputs[0])
    uv = g.combine(sep.outputs['X'], g.math('MULTIPLY', sep.outputs['Y'], -1.0), 0.0)
    geo = g.store(xf.outputs[0], 'nx_quad_uv', 'FLOAT2', uv)
    setm = g.node('GeometryNodeSetMaterial')
    g.link(geo, setm.inputs['Geometry'])
    setm.inputs['Material'].default_value = material
    return setm.outputs[0]


def build_sprite_tree(name, stream_dir, manifest, info, material):
    tree = _new_tree(name)
    g = G(tree)
    gin = g.node('NodeGroupInput')
    pr = info['props']
    cam = _camera(g)
    geo = _stream_points(g, stream_dir, manifest['frame_start'], manifest['frame_end'], pr)
    geo = _sort(g, geo, cam, pr.get('SortMode', 'None'))
    geo = _packed(g, geo, info)
    P = g.node('GeometryNodeInputPosition').outputs[0]
    S = g.vec_attr('size', 2)
    aS = g.vmath('ABSOLUTE', S)
    sep = g.node('ShaderNodeSeparateXYZ')
    g.link(aS, sep.inputs[0])
    theta = g.math('MULTIPLY', g.attr('rot'), math.pi / 180.0)
    D = _safe_normalize(g, g.vmath('SUBTRACT', cam['pos'], P), 1e-8)
    align, facing = pr['Alignment_resolved'], pr['FacingMode_resolved']
    F = _safe_normalize(g, g.vec_attr('facing', 3), 1e-6)
    A = _safe_normalize(g, g.vec_attr('align', 3), 1e-6)
    if align == 'Unaligned':
        if facing == 'FaceCamera':
            R, U = cam['x'], g.vmath('SCALE', cam['y'], scale=-1.0)
        elif facing == 'FaceCameraPlane':
            R = _safe_normalize(g, g.cross_ue(cam['z'], cam['y']), 1e-6)
            U = g.cross_ue(cam['z'], R)
        elif facing == 'CustomFacingVector':
            R = _safe_normalize(g, g.cross_ue(F, cam['y']), 1e-6)
            U = g.cross_ue(F, R)
        elif facing == 'FaceCameraPosition':
            R = _safe_normalize(g, g.cross_ue(D, (0.0, 0.0, 1.0)), 1e-6)
            U = g.cross_ue(D, R)
        else:
            raise ValueError('facing_unsupported:' + facing)
    else:
        if align == 'VelocityAligned':
            V = g.vec_attr('vel', 3)
            Vl = g.vmath('LENGTH', V)
            Dir = g.switch('VECTOR', g.math('GREATER_THAN', Vl, 1e-8), (0.0, 0.0, 1.0),
                           g.vmath('SCALE', V, scale=g.math('DIVIDE', 1.0, g.math('MAXIMUM', Vl, 1e-30))))
        else:
            Dir = A
        CF = F if facing == 'CustomFacingVector' else D
        R = _safe_normalize(g, g.cross_ue(CF, Dir), 1e-6)
        U = g.cross_ue(CF, R) if facing == 'CustomFacingVector' else g.vmath('SCALE', Dir, scale=-1.0)
    c, s = g.math('COSINE', theta), g.math('SINE', theta)
    OutR = g.vmath('ADD', g.vmath('SCALE', R, scale=c), g.vmath('SCALE', U, scale=s))
    OutU = g.vmath('SUBTRACT', g.vmath('SCALE', U, scale=c), g.vmath('SCALE', R, scale=s))
    axes = g.node('FunctionNodeAxesToRotation', primary_axis='X', secondary_axis='Y')
    g.link(OutR, axes.inputs['Primary Axis'])
    g.link(g.vmath('SCALE', OutU, scale=-1.0), axes.inputs['Secondary Axis'])
    geo = g.store(geo, 'nx_rot', 'QUATERNION', axes.outputs[0])
    geo = g.store(geo, 'nx_scale', 'FLOAT_VECTOR', g.combine(sep.outputs['X'], sep.outputs['Y'], 1.0))
    CO = g.vmath('SCALE', D, scale=g.attr('camoff'))
    off_r = g.vmath('SCALE', OutR, scale=g.math('MULTIPLY', sep.outputs['X'], g.attr('pivot_0')))
    off_u = g.vmath('SCALE', OutU, scale=g.math('MULTIPLY', sep.outputs['Y'], g.attr('pivot_1')))
    setp = g.node('GeometryNodeSetPosition')
    g.link(geo, setp.inputs['Geometry'])
    g.link(g.vmath('SUBTRACT', g.vmath('SUBTRACT', g.vmath('ADD', P, CO), off_r), off_u), setp.inputs['Position'])
    inst = g.node('GeometryNodeInstanceOnPoints')
    g.link(setp.outputs[0], inst.inputs['Points'])
    g.link(_quad(g, material), inst.inputs['Instance'])
    g.link(g.attr('nx_rot', 'QUATERNION'), inst.inputs['Rotation'])
    g.link(g.attr('nx_scale', 'FLOAT_VECTOR'), inst.inputs['Scale'])
    _finish(g, inst.outputs[0], gin.outputs['Realize'])
    return tree


def _mesh_branch(g, geo, info, cam, k, entry, mesh_object):
    pr = info['props']
    if info.get('mesh_index_bound'):
        other = g.math('SUBTRACT', 1.0, g.math('COMPARE', g.math('ROUND', g.attr('meshidx')), float(k), 0.25))
        delete = g.node('GeometryNodeDeleteGeometry', domain='POINT')
        g.link(geo, delete.inputs['Geometry'])
        g.link(other, delete.inputs['Selection'])
        geo = delete.outputs[0]
    P = g.node('GeometryNodeInputPosition').outputs[0]
    D = _safe_normalize(g, g.vmath('SUBTRACT', cam['pos'], P), 1e-8)
    qx, qy, qz, qw = (g.attr('quat_%d' % c) for c in range(4))
    qlen = g.math('MAXIMUM', g.math('SQRT', g.math('ADD', g.math('ADD', g.math('MULTIPLY', qx, qx), g.math('MULTIPLY', qy, qy)),
                                                     g.math('ADD', g.math('MULTIPLY', qz, qz), g.math('MULTIPLY', qw, qw)))), 1e-30)
    q2r = g.node('FunctionNodeQuaternionToRotation')
    for sock, v in zip(('X', 'Y', 'Z', 'W'), (qx, qy, qz, qw)):
        g.link(g.math('DIVIDE', v, qlen), q2r.inputs[sock])
    mq = entry['rotation_quat_ue']
    mrot = g.node('FunctionNodeQuaternionToRotation')
    for sock, v in zip(('X', 'Y', 'Z', 'W'), (-mq[0], mq[1], -mq[2], mq[3])):
        mrot.inputs[sock].default_value = v
    rr = g.node('FunctionNodeRotateRotation', rotation_space='LOCAL')
    g.link(q2r.outputs[0], rr.inputs['Rotation'])
    g.link(mrot.outputs[0], rr.inputs['Rotate By'])
    rot = rr.outputs[0]
    mode = pr.get('FacingMode', 'Default')
    if mode != 'Default':
        if mode == 'Velocity':
            V = g.vec_attr('vel', 3)
            Vl = g.vmath('LENGTH', V)
            F = g.switch('VECTOR', g.math('GREATER_THAN', Vl, 0.0), (0.0, 0.0, 1.0),
                         g.vmath('SCALE', V, scale=g.math('DIVIDE', 1.0, g.math('MAXIMUM', Vl, 1e-30))))
        elif mode == 'CameraPosition':
            F = D
        else:
            F = cam['z']
        if pr.get('bLockedAxisEnable'):
            la = pr.get('LockedAxis', [0.0, 0.0, 1.0])
            L = (float(la[0]), -float(la[1]), float(la[2]))
            near = g.math('GREATER_THAN', g.math('ABSOLUTE', g.vmath('DOT_PRODUCT', F, L)), 0.99)
            F = g.switch('VECTOR', near, F, (1.0, 0.0, 0.0) if abs(L[2]) > 0.99 else (0.0, 0.0, 1.0))
            Zc = L
            Yc = g.vmath('NORMALIZE', g.cross_ue(Zc, F))
            Xc = g.cross_ue(Yc, Zc)
        else:
            if mode == 'CameraPlane':
                Ref = cam['y']
            else:
                dz = g.vmath('DOT_PRODUCT', F, (0.0, 0.0, 1.0))
                flip = g.vmath('SCALE', (1.0, 0.0, 0.0), scale=g.math('MULTIPLY', g.math('SIGN', dz), -1.0))
                Ref = g.switch('VECTOR', g.math('GREATER_THAN', g.math('ABSOLUTE', dz), 0.99), (0.0, 0.0, 1.0), flip)
            Xc = F
            Yc = g.vmath('NORMALIZE', g.cross_ue(Ref, F))
        axes = g.node('FunctionNodeAxesToRotation', primary_axis='X', secondary_axis='Y')
        g.link(Xc, axes.inputs['Primary Axis'])
        g.link(g.vmath('SCALE', Yc, scale=-1.0), axes.inputs['Secondary Axis'])
        rf = g.node('FunctionNodeRotateRotation', rotation_space='GLOBAL')
        g.link(rot, rf.inputs['Rotation'])
        g.link(axes.outputs[0], rf.inputs['Rotate By'])
        rot = rf.outputs[0]
    pscale = g.vec_attr('scale', 3)
    ms = entry['scale']
    S = g.vmath('MULTIPLY', pscale, (float(ms[0]), float(ms[1]), float(ms[2])))
    geo = g.store(geo, 'nx_rot', 'QUATERNION', rot)
    geo = g.store(geo, 'nx_scale', 'FLOAT_VECTOR', S)
    geo = g.store(geo, 'nx_mesh', 'FLOAT', float(k))  # Join Geometry does not keep link order; branches are told apart by this tag
    po = entry['pivot_offset']
    piv = (float(po[0]) * 0.01, -float(po[1]) * 0.01, float(po[2]) * 0.01)
    pivot = g.rotate_vector(g.vmath('MULTIPLY', pscale, piv), rot) if entry.get('pivot_space', 'Mesh') == 'Mesh' else piv
    setp = g.node('GeometryNodeSetPosition')
    g.link(geo, setp.inputs['Geometry'])
    g.link(g.vmath('ADD', g.vmath('ADD', P, g.vmath('SCALE', D, scale=g.attr('camoff'))), pivot), setp.inputs['Position'])
    info_node = g.node('GeometryNodeObjectInfo', transform_space='ORIGINAL')
    info_node.inputs['Object'].default_value = mesh_object
    info_node.inputs['As Instance'].default_value = True
    inst = g.node('GeometryNodeInstanceOnPoints')
    g.link(setp.outputs[0], inst.inputs['Points'])
    g.link(info_node.outputs['Geometry'], inst.inputs['Instance'])
    g.link(g.attr('nx_rot', 'QUATERNION'), inst.inputs['Rotation'])
    g.link(g.attr('nx_scale', 'FLOAT_VECTOR'), inst.inputs['Scale'])
    return inst.outputs[0]


def build_ribbon_tree(name, stream_dir, manifest, info, material):
    """Per-vertex view-dependent ribbon expansion (NiagaraRibbonVertexFactory.ush GetRibbonPositionIntermediates, ConstructWorldPosition)."""
    tree = _new_tree(name)
    g = G(tree)
    gin = g.node('NodeGroupInput')
    pr = info['props']
    cam = _camera(g)
    geo = _stream_points(g, stream_dir, manifest['frame_start'], manifest['frame_end'], pr, prefix='m_', ribbon=True)
    for nm, prefixes in (('nx_color', ['col_0', 'col_1', 'col_2', 'col_3']),) + tuple(
            ('nx_dyn%d' % k, ['dyn%d_%d' % (k, c) for c in range(4)]) for k in range(4)):
        geo = g.store(geo, nm, 'FLOAT_COLOR', g.color_attr(prefixes))
    geo = g.store(geo, 'nx_misc', 'FLOAT_COLOR', g.color_attr([0.0, 'nage', 'matrand', 0.0]))
    geo = g.store(geo, 'nx_uv', 'FLOAT_COLOR', g.color_attr(['uv0_0', 'uv0_1', 'uv1_0', 'uv1_1']))
    P = g.node('GeometryNodeInputPosition').outputs[0]
    Dir = g.vec_attr('dir', 3)
    custom = pr.get('FacingMode', 'Screen') in ('Custom', 'CustomSideVector')
    F = _ribbon_normalize(g, g.vec_attr('facing', 3) if custom else g.vmath('SUBTRACT', cam['pos'], P))
    R = _ribbon_normalize(g, g.cross_ue(F, Dir))
    if pr.get('FacingMode') == 'CustomSideVector':
        R, F = g.vmath('SCALE', F, scale=-1.0), R
    if pr.get('twist_bound'):
        tw = g.attr('twist')
        c, s = g.math('COSINE', tw), g.math('SINE', tw)
        R, F = (g.vmath('ADD', g.vmath('SCALE', R, scale=c), g.vmath('SCALE', F, scale=s)),
                g.vmath('ADD', g.vmath('SCALE', R, scale=g.math('MULTIPLY', s, -1.0)), g.vmath('SCALE', F, scale=c)))
    up = g.cross_ue(Dir, R)
    off = g.vmath('ADD', g.vmath('SCALE', R, scale=g.attr('sl_px')), g.vmath('SCALE', up, scale=g.attr('sl_py')))
    setp = g.node('GeometryNodeSetPosition')
    g.link(geo, setp.inputs['Geometry'])
    g.link(g.vmath('ADD', P, g.vmath('SCALE', off, scale=g.attr('width'))), setp.inputs['Position'])
    setm = g.node('GeometryNodeSetMaterial')
    g.link(setp.outputs[0], setm.inputs['Geometry'])
    setm.inputs['Material'].default_value = material
    out = g.node('NodeGroupOutput')
    g.link(setm.outputs[0], out.inputs['Geometry'])
    return tree


def _ribbon_normalize(g, v):
    """SafeNormalize(V, 0) in NiagaraRibbonVertexFactory.ush: zero for a zero vector."""
    return g.vmath('NORMALIZE', v)


def ribbon_preview_material():
    """Geometry-attribute colour x UV grid at UV0; alpha-blended. Replaced by translated FX materials in N-O6."""
    mat = bpy.data.materials.get(PREVIEW_MATERIAL + '_Ribbon')
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(PREVIEW_MATERIAL + '_Ribbon')
    mat.use_nodes = True
    mat.surface_render_method = 'BLENDED'
    nt = mat.node_tree
    nt.nodes.clear()
    N, L = nt.nodes, nt.links
    col = N.new('ShaderNodeAttribute')
    col.attribute_type = 'GEOMETRY'
    col.attribute_name = 'nx_color'
    uv = N.new('ShaderNodeAttribute')
    uv.attribute_type = 'GEOMETRY'
    uv.attribute_name = 'nx_uv'
    sep = N.new('ShaderNodeSeparateXYZ')
    L.new(uv.outputs['Vector'], sep.inputs[0])
    inv = N.new('ShaderNodeMath')
    inv.operation = 'SUBTRACT'
    inv.inputs[0].default_value = 1.0
    L.new(sep.outputs['Y'], inv.inputs[1])
    comb = N.new('ShaderNodeCombineXYZ')
    L.new(sep.outputs['X'], comb.inputs[0])
    L.new(inv.outputs[0], comb.inputs[1])
    tex = N.new('ShaderNodeTexImage')
    img = bpy.data.images.get('DR_NX_UVGrid') or bpy.data.images.new('DR_NX_UVGrid', 256, 256, alpha=True)
    img.generated_type = 'UV_GRID'
    tex.image = img
    L.new(comb.outputs[0], tex.inputs['Vector'])
    rgb = N.new('ShaderNodeMix')
    rgb.data_type = 'RGBA'
    rgb.blend_type = 'MULTIPLY'
    rgb.inputs['Factor'].default_value = 1.0
    L.new(col.outputs['Color'], rgb.inputs['A'])
    L.new(tex.outputs['Color'], rgb.inputs['B'])
    em = N.new('ShaderNodeEmission')
    L.new(rgb.outputs['Result'], em.inputs['Color'])
    tr = N.new('ShaderNodeBsdfTransparent')
    mix = N.new('ShaderNodeMixShader')
    L.new(col.outputs['Alpha'], mix.inputs['Fac'])
    L.new(tr.outputs[0], mix.inputs[1])
    L.new(em.outputs[0], mix.inputs[2])
    out = N.new('ShaderNodeOutputMaterial')
    L.new(mix.outputs[0], out.inputs['Surface'])
    return mat


def build_mesh_tree(name, stream_dir, manifest, info, mesh_objects):
    tree = _new_tree(name)
    g = G(tree)
    gin = g.node('NodeGroupInput')
    cam = _camera(g)
    geo = _stream_points(g, stream_dir, manifest['frame_start'], manifest['frame_end'], info['props'])
    geo = _packed(g, geo, info)
    join = g.node('GeometryNodeJoinGeometry')
    for k, entry in enumerate(info['meshes']):
        g.link(_mesh_branch(g, geo, info, cam, k, entry, mesh_objects[k]), join.inputs[0])
    _finish(g, join.outputs[0], gin.outputs['Realize'])
    return tree


def placeholder_mesh_object():
    obj = bpy.data.objects.get(PLACEHOLDER_MESH)
    if obj is None:
        mesh = bpy.data.meshes.new(PLACEHOLDER_MESH)
        s = 0.05
        mesh.from_pydata([(s, 0, 0), (-s, s, 0), (-s, -s, 0), (0, 0, 2 * s)], [], [(0, 1, 2), (0, 1, 3), (1, 2, 3), (2, 0, 3)])
        obj = bpy.data.objects.new(PLACEHOLDER_MESH, mesh)
        obj['dr_nx_label'] = 'mesh: placeholder'
    return obj


def preview_material():
    """Instancer colour x SubUV-sampled UV grid; alpha-blended. Replaced by translated FX materials in N-O6."""
    mat = bpy.data.materials.get(PREVIEW_MATERIAL)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(PREVIEW_MATERIAL)
    mat.use_nodes = True
    mat.surface_render_method = 'BLENDED'
    nt = mat.node_tree
    nt.nodes.clear()
    N, L = nt.nodes, nt.links

    def attr(name, kind):
        node = N.new('ShaderNodeAttribute')
        node.attribute_type = kind
        node.attribute_name = name
        return node

    def m(op, a, b=0.0):
        node = N.new('ShaderNodeMath')
        node.operation = op
        for i, v in enumerate((a, b)):
            if isinstance(v, bpy.types.NodeSocket):
                L.new(v, node.inputs[i])
            else:
                node.inputs[i].default_value = v
        return node.outputs[0]

    col, misc, uvx = attr('nx_color', 'INSTANCER'), attr('nx_misc', 'INSTANCER'), attr('nx_uvx', 'INSTANCER')
    quv = attr('nx_quad_uv', 'GEOMETRY')
    sep_misc, sep_uvx, sep_q = N.new('ShaderNodeSeparateXYZ'), N.new('ShaderNodeSeparateXYZ'), N.new('ShaderNodeSeparateXYZ')
    L.new(misc.outputs['Vector'], sep_misc.inputs[0])
    L.new(uvx.outputs['Vector'], sep_uvx.inputs[0])
    L.new(quv.outputs['Vector'], sep_q.inputs[0])
    idx = sep_misc.outputs['X']
    sx, sy = sep_uvx.outputs['Z'], uvx.outputs['Alpha']
    lerp = m('SUBTRACT', idx, m('FLOOR', idx))
    a = m('ADD', m('SUBTRACT', idx, lerp), 0.5)
    b = m('ADD', a, 1.0)
    ut = m('ADD', 0.5, m('MULTIPLY', m('SUBTRACT', sep_q.outputs['X'], 0.5), sep_uvx.outputs['X']))
    vt = m('ADD', 0.5, m('MULTIPLY', m('SUBTRACT', sep_q.outputs['Y'], 0.5), sep_uvx.outputs['Y']))
    img = bpy.data.images.get('DR_NX_UVGrid') or bpy.data.images.new('DR_NX_UVGrid', 256, 256, alpha=True)
    img.generated_type = 'UV_GRID'
    samples = []
    for k in (a, b):
        h = m('FLOOR', m('MODULO', k, sx))
        v = m('FLOOR', m('DIVIDE', k, sx))
        tu = m('DIVIDE', m('ADD', h, ut), sx)
        tv = m('DIVIDE', m('ADD', v, vt), sy)
        comb = N.new('ShaderNodeCombineXYZ')
        L.new(tu, comb.inputs[0])
        L.new(m('SUBTRACT', 1.0, tv), comb.inputs[1])
        tex = N.new('ShaderNodeTexImage')
        tex.name = 'nx_tex_%s' % ('a' if k is a else 'b')
        tex.image = img
        tex.interpolation = 'Closest'
        L.new(comb.outputs[0], tex.inputs['Vector'])
        samples.append(tex)
    fac = m('MULTIPLY', lerp, misc.outputs['Alpha'])
    mix = N.new('ShaderNodeMix')
    mix.data_type = 'RGBA'
    L.new(fac, mix.inputs['Factor'])
    L.new(samples[0].outputs['Color'], mix.inputs['A'])
    L.new(samples[1].outputs['Color'], mix.inputs['B'])
    mixa = m('ADD', m('MULTIPLY', samples[0].outputs['Alpha'], m('SUBTRACT', 1.0, fac)), m('MULTIPLY', samples[1].outputs['Alpha'], fac))
    rgb = N.new('ShaderNodeMix')
    rgb.data_type = 'RGBA'
    rgb.blend_type = 'MULTIPLY'
    rgb.inputs['Factor'].default_value = 1.0
    L.new(col.outputs['Color'], rgb.inputs['A'])
    L.new(mix.outputs['Result'], rgb.inputs['B'])
    em = N.new('ShaderNodeEmission')
    L.new(rgb.outputs['Result'], em.inputs['Color'])
    tr = N.new('ShaderNodeBsdfTransparent')
    mixs = N.new('ShaderNodeMixShader')
    L.new(m('MULTIPLY', col.outputs['Alpha'], mixa), mixs.inputs['Fac'])
    L.new(tr.outputs[0], mixs.inputs[1])
    L.new(em.outputs[0], mixs.inputs[2])
    out = N.new('ShaderNodeOutputMaterial')
    L.new(mixs.outputs[0], out.inputs['Surface'])
    return mat


def _keyframe_constant(idblock):
    ad = idblock.animation_data
    if ad is None or ad.action is None:
        return
    from bpy_extras import anim_utils
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    if bag is None:
        return
    for fc in bag.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = 'CONSTANT'


def fx_exposure_scale():
    """UE-units-to-scene scale of the FX exposure group (niagara_fx_materials), 1.0 when the scene has none."""
    grp = bpy.data.node_groups.get('DR_NX_FXExposure')
    value = next((n for n in grp.nodes if n.type == 'VALUE'), None) if grp else None
    return float(value.outputs[0].default_value) if value else 1.0


def build_light_pool(name, stream_dir, manifest, info, parent, collection, e1=ref.E1_CYCLES, exposure=1.0):
    """exposure: the scale FX emissives get (fx_exposure_scale), so particle lights keep their authored ratio to them."""
    frames = list(range(manifest['frame_start'], manifest['frame_end'] + 1))
    states = [ref.light_frame(ref.read_ply(Path(stream_dir) / ('f_%04d.ply' % f)), info, e1) for f in frames]
    for st in states:
        for s in st:
            s['energy'] *= exposure
    slots, size = ref.assign_slots([[s['uid'] for s in st] for st in states])
    objects = []
    for k in range(size):
        data = bpy.data.lights.new('%s_L%02d' % (name, k), 'POINT')
        data.use_shadow = False
        data.shadow_soft_size = 0.0
        data.use_custom_distance = True
        obj = bpy.data.objects.new(data.name, data)
        obj.parent = parent
        obj['dr_nx_label'] = 'light_falloff: approx' if info['props'].get('bUseInverseSquaredFalloff', True) else 'light_falloff: unsupported_exponent'
        collection.objects.link(obj)
        objects.append(obj)
    for f, st, sl in zip(frames, states, slots):
        by_slot = dict(zip(sl, st))
        for k, obj in enumerate(objects):
            s = by_slot.get(k)
            d = obj.data
            obj.hide_render = obj.hide_viewport = s is None
            if s is not None:
                obj.location = tuple(s['pos'])
                d.color = tuple(s['color'])
                d.energy = s['energy']
                d.cutoff_distance = s['radius']
                d.volume_factor, d.specular_factor, d.diffuse_factor = s['volume'], s['specular'], s['diffuse']
            else:
                d.energy = 0.0
            for path in ('location', 'hide_render', 'hide_viewport'):
                obj.keyframe_insert(path, frame=f)
            for path in ('color', 'energy', 'cutoff_distance', 'volume_factor', 'specular_factor', 'diffuse_factor'):
                d.keyframe_insert(path, frame=f)
    for obj in objects:
        _keyframe_constant(obj)
        _keyframe_constant(obj.data)
    return objects


def _material_variant(obj, mats):
    """Hidden copy of a mesh source carrying per-slot renderer materials (overrides / translated slot materials); obj when none."""
    if not any(mats):
        return obj
    key = '%s|%s' % (obj.name, '|'.join(m.name if m else '' for m in mats))
    hit = next((o for o in bpy.data.objects if o.get('dr_nx_variant') == key), None)
    if hit is not None:
        return hit
    new = obj.copy()
    new.data = obj.data.copy()
    for i, m in enumerate(mats):
        if m is None:
            continue
        if i < len(new.data.materials):
            new.data.materials[i] = m
        else:
            new.data.materials.append(m)
    new['dr_nx_variant'] = key
    for c in obj.users_collection or [bpy.context.scene.collection]:
        c.objects.link(new)
    hide_instance_source(new)
    return new


def hide_instance_source(obj):
    """Hide a GN instancing source without dropping its instances from the Cycles Rendered viewport.

    'Disable in Viewports' (hide_viewport) also removes the Object Info instances from the Rendered viewport (F12 still shows
    them); the eye toggle and 'Disable in Renders' hide only the source."""
    obj.hide_render = True
    obj.hide_viewport = False
    try:
        obj.hide_set(True)
    except RuntimeError:  # not in the active view layer
        pass


def build_system(cache_dir, collection=None, mesh_objects=None, material=None, materials=None):
    """Build playback for one cache. mesh_objects maps a mesh ObjectPath to a bpy object (placeholder otherwise).

    materials: optional niagara_fx_materials.renderer_materials callback (renderer info, kind, slot, slot material name) ->
    translated material or None; unresolved renderers keep the preview material.
    """
    cache_dir = Path(cache_dir)
    manifest = json.loads((cache_dir / 'cache_manifest.json').read_text(encoding='utf-8'))
    collection = collection or bpy.context.scene.collection
    root = bpy.data.objects.new('NX_' + manifest['system'], None)
    root.location = tuple(manifest['bake_origin_b'])
    root['dr_nx_cache'] = str(cache_dir)
    collection.objects.link(root)
    material = material or preview_material()
    infos = {(r['emitter'], r['ordinal']): r for r in manifest['renderers']}
    built = {'root': root, 'rigs': [], 'lights': []}
    for st in manifest['streams']:
        info = infos[(st['emitter'], st['ordinal'])]
        name = '%s_%s_%s' % (manifest['system'], st['name'], info['kind'])
        sdir = cache_dir / st['name']
        if info['kind'] == 'light':
            built['lights'].append((st['name'], build_light_pool(name, sdir, manifest, info, root, collection, exposure=fx_exposure_scale())))
            continue
        if info['kind'] == 'sprite':
            tree = build_sprite_tree(name, sdir, manifest, info, (materials(info, 'sprite') if materials else None) or material)
        elif info['kind'] == 'ribbon':
            tree = build_ribbon_tree(name, sdir, manifest, info, ribbon_preview_material())
        else:
            objs = [(mesh_objects or {}).get(m['object_path']) or placeholder_mesh_object() for m in info['meshes']]
            if materials:
                objs = [_material_variant(o, [materials(info, 'mesh', i, n) for i, n in enumerate(
                    list(o.get('dr_nx_slot_materials') or []) or [s.material.name if s.material else None for s in o.material_slots] or [None])])
                    for o in objs]
            tree = build_mesh_tree(name, sdir, manifest, info, objs)
        obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        obj.parent = root
        obj['dr_nx_stream'] = st['name']
        obj['dr_nx_kind'] = info['kind']
        collection.objects.link(obj)
        mod = obj.modifiers.new('NX', 'NODES')
        mod.node_group = tree
        built['rigs'].append((st['name'], obj))
    scene = bpy.context.scene
    scene.render.fps = int(round(manifest['fps']))
    scene.render.fps_base = float(manifest['fps_base'])
    return built


def set_realize(obj, value):
    mod = obj.modifiers['NX']
    for item in mod.node_group.interface.items_tree:
        if getattr(item, 'name', '') == 'Realize' and item.in_out == 'INPUT':
            mod[item.identifier] = bool(value)
    obj.update_tag()
