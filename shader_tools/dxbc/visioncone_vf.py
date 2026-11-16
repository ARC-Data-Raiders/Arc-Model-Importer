"""Inputs for the pinned VisionCone VF in an explicit orthographic diagnostic view.

The cached world is the offline emulator's world. Owner/socket state is unknown.
Only affine orthographic raster interpolation is admitted here. No game view claim.
"""
import hashlib
import io
import json

import numpy as np

from .visioncone import Unsupported, _pinned

MESH_SHA256 = '8147023b95d9c646b0ce883df5dbffea8437ea64a9e4c6c0dca3027ec98311b3'


def view(scene):
    camera = scene.camera
    if camera is None or camera.data.type != 'ORTHO':
        raise Unsupported('explicit diagnostic orthographic camera required')
    with __import__('bpy').context.temp_override(scene=scene):
        depsgraph = __import__('bpy').context.evaluated_depsgraph_get()
        camera = camera.evaluated_get(depsgraph)
    width = round(scene.render.resolution_x * scene.render.resolution_percentage / 100)
    height = round(scene.render.resolution_y * scene.render.resolution_percentage / 100)
    if min(width, height) <= 0:
        raise Unsupported('invalid diagnostic viewport')
    projection = np.asarray(camera.calc_matrix_camera(depsgraph, x=width, y=height,
                            scale_x=scene.render.pixel_aspect_x, scale_y=scene.render.pixel_aspect_y), np.float64)
    world = np.asarray(camera.matrix_world, np.float64)
    # Column-vector Blender world metres -> UE world centimetres; D3D depth [0,1].
    conversion = np.diag([.01, -.01, .01, 1.])
    d3d = np.eye(4)
    d3d[2, 2], d3d[2, 3] = .5, .5
    matrix = d3d @ projection @ np.linalg.inv(world) @ conversion
    pixel = np.eye(4)
    pixel[0, 0], pixel[0, 3] = width / 2, width / 2
    pixel[1, 1], pixel[1, 3] = -height / 2, height / 2
    screen = pixel @ matrix
    if not np.isfinite(screen).all() or not np.array_equal(screen[3], [0, 0, 0, 1]):
        raise Unsupported('non-affine diagnostic projection')
    payload = dict(domain='diagnostic_orthographic_affine', camera=scene.camera.name,
                   world=world.tolist(), clip=matrix.tolist(), screen=screen.tolist(),
                   inverse_screen=np.linalg.inv(screen).tolist(), size=[width, height],
                   position=(world[:3, 3] * [100, -100, 100]).tolist(),
                   forward=(-world[:3, 2] * [1, -1, 1]).tolist(),
                   owner_socket_transform='unknown', production_parity='unverified')
    payload['identity'] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload


def validate_view(context, scene):
    if not context or context != view(scene):
        raise Unsupported('absent or stale explicit diagnostic camera context')


def apply_view(buffer, context):
    result = buffer.copy()
    floats = result.view('<f4')
    floats[44:48] = np.asarray(context['inverse_screen'], np.float32).T
    floats[122, :3] = context['position']
    floats[73, :3] = context['forward']
    floats[31, 3] = 1.
    floats[148, :2] = 0.
    w, h = context['size']
    floats[149] = [w, h, 1/w, 1/h]
    floats[152] = floats[149]
    return result


def hydrate_mesh(obj, reader, pin):
    payload = _pinned(pin)
    if hashlib.sha256(payload).hexdigest() != MESH_SHA256:
        raise Unsupported('unsupported retained VisionCone mesh identity')
    name, version, flip, root = reader._read_uemodel_root(pin['file'])
    if version != 10 or not flip:
        raise Unsupported('VisionCone requires the retained v10 source mesh')
    buf = io.BytesIO(dict(root)['LODS'])
    reader._read_array_header(buf)
    reader._read_fstring(buf)
    attrs = dict(reader._read_attribute_set(buf))
    _, _, lods = reader.parse_uemodel(pin['file'])
    lod = lods[0]
    mesh = obj.data
    actual = np.empty((len(mesh.vertices), 3), np.float32)
    mesh.vertices.foreach_get('co', actual.ravel())
    if actual.shape != lod.vertices.shape or not np.array_equal(actual, lod.vertices):
        raise Unsupported('source mesh positions or unit scale differ')
    if [tuple(p.vertices) for p in mesh.polygons] != [tuple(p) for p in lod.indices]:
        raise Unsupported('source mesh topology differs')
    n = len(actual)
    normal = np.frombuffer(attrs['NORMALS'], '<f4', offset=4).reshape(n, 4)
    tangent = np.frombuffer(attrs['TANGENTS'], '<f4', offset=4).reshape(n, 3)
    if not np.isfinite(normal).all() or not np.isfinite(tangent).all():
        raise Unsupported('invalid source mesh tangent frame')
    source_normal = normal[:, 1:]
    first = (source_normal * tangent[:, [1,2,0]] - tangent * source_normal[:, [1,2,0]]) * normal[:, :1]
    tc10 = (first[:, [2,0,1]] * source_normal[:, [2,0,1]] - source_normal[:, [1,2,0]] * first) * normal[:, :1]
    # Raw source t5 entries, before Blender axis conversion. VF 616-624.
    for key, values in [('nx_source_tangent', np.column_stack([tangent, np.ones(n)])),
                        ('nx_source_tc10', np.column_stack([tc10, np.zeros(n)])),
                        ('nx_source_normal', np.column_stack([normal[:, 1:], normal[:, 0]]))]:
        attribute = mesh.attributes.get(key) or mesh.attributes.new(key, 'FLOAT_COLOR', 'POINT')
        attribute.data.foreach_set('color', np.asarray(values, np.float32).ravel())
    if not mesh.uv_layers or len(lod.uvs) != 1:
        raise Unsupported('source mesh UV0 missing')
    uv = np.empty((len(mesh.loops), 2), np.float32)
    mesh.uv_layers[0].data.foreach_get('uv', uv.ravel())
    indices = np.asarray([loop.vertex_index for loop in mesh.loops])
    if not np.array_equal(uv, lod.uvs[0][indices]):
        raise Unsupported('source mesh UV0 differs')
    obj['dr_nx_vf_source'] = hashlib.sha256(payload).hexdigest()
    return dict(sha256=obj['dr_nx_vf_source'], vertices=n, source_tangents=True, source_normals=True, source_uv=True)


def validate_particles(module, cache, manifest, renderer):
    from pathlib import Path
    if renderer['props'].get('FacingMode') != 'Default' or renderer['props'].get('bLockedAxisEnable'):
        raise Unsupported('unsupported source VisionCone facing context')
    if manifest.get('bake_origin_ue') != [0., 0., 0.]:
        raise Unsupported('unknown source large-world/owner transform')
    entry = renderer['meshes'][0]
    if entry['pivot_offset'] != [0., 0., 0.] or len(renderer['meshes']) != 1:
        raise Unsupported('unsupported source mesh pivot context')
    names = ['x', 'y', 'z', 'camoff'] + ['quat_%d' % i for i in range(4)] + ['scale_%d' % i for i in range(3)]
    names += ['col_%d' % i for i in range(4)] + ['dyn%d_%d' % (k, i) for k in range(3) for i in range(4)]
    identities = []
    for frame in range(manifest['frame_start'], manifest['frame_end'] + 1):
        path = Path(cache) / 'e2_r0' / ('f_%04d.ply' % frame)
        cols = module.read_ply(path)
        keep = cols['nx_valid'] > .5
        if not np.isfinite(cols['nx_valid']).all() or any(name not in cols or not np.isfinite(cols[name][keep]).all() for name in names):
            raise Unsupported('invalid source particle/dynamic data')
        if any(np.any(cols['scale_%d' % i][keep] == 0) for i in range(3)) or np.any(sum(cols['quat_%d' % i][keep] ** 2 for i in range(4)) == 0):
            raise Unsupported('singular source particle transform')
        identities.append(dict(frame=frame, file='e2_r0/' + path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return dict(frames=len(identities), files=identities, sha256=hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest())


def store_particles(g, geometry, rotation, scale):
    # VF 491-513, 683, and 862-878. Outputs are affine matrix rows in UE cm.
    separate = g.node('ShaderNodeSeparateXYZ')
    g.link(scale, separate.inputs[0])
    axes, columns = [], []
    for index, vector in enumerate(((1.,0.,0.), (0.,-1.,0.), (0.,0.,1.))):
        axis = g.vmath('MULTIPLY', g.rotate_vector(vector, rotation), (1.,-1.,1.))
        axes.append(axis)
        columns.append(g.vmath('SCALE', axis, scale=separate.outputs[index]))
    components = []
    for vector in columns:
        node = g.node('ShaderNodeSeparateXYZ')
        g.link(vector, node.inputs[0])
        components.append(list(node.outputs))
    position = g.vmath('MULTIPLY', g.node('GeometryNodeInputPosition').outputs[0], (100.,-100.,100.))
    geometry = g.store(geometry, 'nx_particle_position', 'FLOAT_VECTOR', position)
    position_components = g.node('ShaderNodeSeparateXYZ')
    g.link(position, position_components.inputs[0])
    for row in range(3):
        values = [components[col][row] for col in range(3)]
        values.append(position_components.outputs[row])
        geometry = g.store(geometry, 'nx_particle_local_to_world%d' % row, 'FLOAT_COLOR', g.color_attr(values))
        inverse = g.vmath('SCALE', axes[row], scale=g.math('DIVIDE', 1., separate.outputs[row]))
        node = g.node('ShaderNodeSeparateXYZ')
        g.link(inverse, node.inputs[0])
        offset = g.math('MULTIPLY', g.vmath('DOT_PRODUCT', inverse, position), -1.)
        geometry = g.store(geometry, 'nx_particle_world_to_local%d' % row, 'FLOAT_COLOR', g.color_attr(list(node.outputs) + [offset]))
        geometry = g.store(geometry, 'nx_particle_basis%d' % row, 'FLOAT_VECTOR', axes[row])
    return geometry


def sources(tree, plan, context):
    from . import lower_blender as lb
    cache = {}
    def attribute(name):
        if name not in cache:
            node = tree.nodes.new('ShaderNodeAttribute')
            node.attribute_name, node.attribute_type = name, 'GEOMETRY'
            separate = tree.nodes.new('ShaderNodeSeparateColor')
            tree.links.new(node.outputs['Color'], separate.inputs[0])
            cache[name] = (node, list(separate.outputs[:3]) + [node.outputs['Alpha']])
        return cache[name]
    values = {}
    for semantic, name in [('COLOR1','nx_color'), ('COLOR2','nx_dyn0'), ('COLOR3','nx_dyn1'), ('COLOR4','nx_dyn2')]:
        values[semantic] = attribute(name)[1]
    for semantic, prefix in [('PARTICLE_LOCAL_TO_WORLD','nx_particle_local_to_world'), ('PARTICLE_WORLD_TO_LOCAL','nx_particle_world_to_local')]:
        for index in range(3):
            values[semantic + str(index)] = attribute(prefix + str(index))[1]
    values['PARTICLE_POSITION0'] = attribute('nx_particle_position')[1]
    axis = attribute('nx_vc_axis_angle')[1]
    fade = attribute('nx_vc_fade0')[0].outputs['Fac']
    # Scalar geometry attributes appear on Fac; read vector/color lanes explicitly.
    from . import material_ub
    material = material_ub.cb_words(plan['ub'], 12).view('<f4')
    material_fade = lb.mad(tree, axis[3], float(material[0,0]-material[0,1]), float(material[0,1]))
    uv_node = attribute('UV0')[0]
    uv = tree.nodes.new('ShaderNodeSeparateXYZ')
    tree.links.new(uv_node.outputs['Vector'], uv.inputs[0])
    values['TEXCOORD0'] = [uv.outputs[0], lb.math(tree,'SUBTRACT',1.,uv.outputs[1]), fade, material_fade]
    values['TEXCOORD1'] = [axis[0],axis[1],0.,axis[2]]
    normal_node, normal = attribute('nx_source_normal')
    # Nonlinear VF 619-624 runs at source vertices before raster interpolation.
    crossed = attribute('nx_source_tc10')[1][:3]
    # Basis attributes are columns. Transformed xyz is sum(local_i * column_i).
    basis = [attribute('nx_particle_basis%d' % i)[1] for i in range(3)]
    def transformed(local):
        return [lb.dp3(tree, local, [basis[col][row] for col in range(3)]) for row in range(3)]
    values['TEXCOORD10'] = transformed(crossed) + [0.]
    values['TEXCOORD11'] = transformed(normal[:3]) + [normal[3]]
    values['TEXCOORD7'] = [0.,0.,0.,1.]  # Explicit diagnostic fog disabled, VF 852-855.
    position_node = tree.nodes.new('ShaderNodeNewGeometry')
    sep = tree.nodes.new('ShaderNodeSeparateXYZ')
    tree.links.new(position_node.outputs['Position'],sep.inputs[0])
    position = [lb.mul(tree,sep.outputs[i], [100.,-100.,100.][i]) for i in range(3)] + [1.]
    values['SV_Position0'] = [lb.dp4(tree,position,row) for row in context['screen'][:3]] + [1.]
    values['SV_IsFrontFace0'] = lb.math(tree,'SUBTRACT',1.,position_node.outputs['Backfacing'])
    signatures = {'v' + row['Register']: row['Name'] + row['Index'] for row in plan['shader']['signatures']['input']}
    result = {}
    for node in plan['dag']['nodes']:
        if node['op'] == 'input':
            register, lane = node['args']
            semantic = signatures[register]
            value = values[semantic]
            result[(register,lane)] = value if semantic == 'SV_IsFrontFace0' else value['xyzw'.index(lane)]
    return result
