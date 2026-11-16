import gzip
import hashlib
import json
from pathlib import Path
import re

import numpy as np

from . import binding, disasm, material_ub, opt, parse, ssa, volume

MATERIAL = '/Game/Pioneer/Effects/Materials/Enemies/VisionCone/MI_FX_VisionCone_Turret_Shadowed'
PS = '4D9D29B7ED3860212E9DD4CAC244867C8CFB6447'
VF = 'BDCAD2300871A26988902C7D1A57FEB3DE1B0CD0'
PS_CONTAINER = '977706f9264a79d4d979e4718a351d58071167378e417015c404bb209acb60b3'


class Unsupported(ValueError):
    pass


def matches(object_path, kind):
    return kind == 'mesh' and str(object_path).split('.')[0] == MATERIAL


def _pinned(entry):
    payload = Path(entry['file']).read_bytes()
    if hashlib.sha256(payload).hexdigest() != entry['sha256'].lower():
        raise Unsupported('source identity mismatch: ' + entry['file'])
    return payload


def _shader(context, frequency, expected):
    manifest = json.loads(_pinned(context['records']))
    rows = [row for row in manifest['records'] if row['shader_hash'] == expected and row['frequency'] == frequency]
    if len(rows) != 1:
        raise Unsupported('exact retained shader absent or ambiguous: ' + expected)
    row = rows[0]
    root = Path(context['records']['file']).parent.resolve()
    record = (root / row['record_file']).resolve()
    if not record.is_relative_to(root):
        raise Unsupported('shader record escapes source root')
    payload = _pinned(dict(file=str(record), sha256=row['record_sha256']))
    container = payload[row['container_offset']:row['container_offset'] + row['container_size']]
    if hashlib.sha256(container).hexdigest() != row['container_sha256'].lower():
        raise Unsupported('source container identity mismatch')
    if expected == PS and hashlib.sha256(container).hexdigest() != PS_CONTAINER:
        raise Unsupported('unsupported source PS container')
    shader = parse.parse(disasm.disassemble(container))
    return row, shader, binding.bindings(payload, row['container_offset'], row['container_size'])


def _dds_dimensions(resource, payload):
    if payload[:4] != b'DDS ' or payload[84:88] != b'DX10' or len(payload) < 148:
        raise Unsupported('observed dimensions require DX10 DDS')
    height, width = np.frombuffer(payload, '<u4', count=2, offset=12)
    depth = int(np.frombuffer(payload, '<u4', count=1, offset=24)[0]) or 1
    dimensions = (int(width), int(height), depth)
    if dimensions != (resource['width'], resource['height'], resource.get('depth', 1)) or min(dimensions) <= 0:
        raise Unsupported('observed DDS dimensions disagree with resource')
    if int(np.frombuffer(payload, '<u4', count=1, offset=128)[0]) != 2:
        raise Unsupported('diagnostic DDS must be RGBA32F')
    if len(payload) != 148 + int(np.prod(dimensions)) * 16:
        raise Unsupported('observed DDS payload size mismatch')
    return dimensions


def _context(roots):
    candidates = [Path(root) / 'visioncone-source.json' for root in roots if (Path(root) / 'visioncone-source.json').is_file()]
    if len(candidates) != 1:
        raise Unsupported('one explicit VisionCone source context is required')
    context = json.loads(candidates[0].read_text(encoding='utf-8'))
    if context.get('classification') != 'offline_emulator_gpu_compute_readback' or context.get('production_parity') != 'unverified':
        raise Unsupported('VisionCone context must remain diagnostic offline-emulator')
    return context


def prepare(roots, runtime):
    if not runtime or not all(key in runtime for key in ('cache', 'manifest', 'renderer')):
        raise Unsupported('missing current renderer/cache context')
    renderer, manifest = runtime['renderer'], runtime['manifest']
    if (renderer.get('emitter'), renderer.get('ordinal'), renderer.get('kind')) != (2, 0, 'mesh'):
        raise Unsupported('unsupported VisionCone renderer')
    entries = renderer.get('material_parameters', {}).get('AttributeBindings', [])
    bindings = [entry for entry in entries if entry.get('MaterialParameterName') == 'DistanceTexture']
    if len(bindings) != 1:
        raise Unsupported('missing or ambiguous source DistanceTexture binding')
    attribute = bindings[0]
    if attribute.get('NiagaraVariable', {}).get('Name') != 'System.Render Target 2D' or attribute.get('ResolvedNiagaraVariable', {}).get('Name') != 'System.Render Target 2D' or attribute.get('NiagaraChildVariable', {}).get('Name') != 'RenderTarget':
        raise Unsupported('unsupported source render-target variable/child')
    if 'System.Render Target 2D' not in manifest.get('render_target_snapshots', {}):
        raise Unsupported('missing retained render-target snapshots')
    snapshots = manifest['render_target_snapshots']['System.Render Target 2D'].get('frames')
    if not snapshots or any(row.get('filter') != 'TextureFilter::TF_Bilinear' or row.get('format') != 'R16f' for row in snapshots):
        raise Unsupported('unsupported observed runtime texture filter/format')
    context = _context(roots)
    ps_record, shader, resources = _shader(context, 'SF_Pixel', PS)
    vf_record, vertex, vf_resources = _shader(context, 'SF_Vertex', VF)
    metadata = json.loads(gzip.decompress(_pinned(context['shadermaps'])))
    if metadata['chain'][0]['object_path'].split('.')[0] != MATERIAL:
        raise Unsupported('unsupported material instance source')
    resource = next(row for row in metadata['chain'][0]['json']['LoadedMaterialResources'] if row['LoadedShaderMap']['ShaderPlatform'] == 'SP_PCD3D_SM5')
    maps = resource['LoadedShaderMap']['Content']['OrderedMeshShaderMaps']
    pairs = [row for row in maps if any(shader_entry['Type'] == 'TBasePassPSFNoLightMapPolicy' and shader_entry['ResourceIndex'] == 1 for shader_entry in row['Shaders']) and any(shader_entry['Type'] == 'TBasePassVSFNoLightMapPolicy' and shader_entry['ResourceIndex'] == 10 for shader_entry in row['Shaders'])]
    records = json.loads(_pinned(context['records']))['records']
    ordered = sorted([row for row in records if row['map_hash'] == ps_record['map_hash']], key=lambda row: row['shader_index'])
    if len(pairs) != 1 or resource['LoadedShaderMap']['ResourceHash'] != ps_record['map_hash'] or ps_record['map_hash'] != vf_record['map_hash'] or ordered[1]['shader_hash'] != PS or ordered[10]['shader_hash'] != VF or vf_resources['cb'].get('3') != 'NiagaraMeshVF':
        raise Unsupported('retained PS/VF pair provenance mismatch')
    fixture = json.loads(_pinned(context['fixture']))
    if fixture['sha'] != PS or fixture['mi'] != MATERIAL or fixture['vf'] != 'mesh' or fixture.get('param_source') != 'game':
        raise Unsupported('unsupported offline material context')
    sampler_pin = context.get('sampling_job')
    if not sampler_pin or sampler_pin.get('file') != fixture.get('job'):
        raise Unsupported('missing pinned retained sampler job')
    sampler_payload = _pinned(sampler_pin)
    fixture['retained_sampler_job'] = sampler_pin
    vin_path = Path(fixture['job']).parent / 'vin.json'
    vin = json.loads(_pinned(dict(file=str(vin_path), sha256=context['inputs'][str(vin_path)])))
    for member in vin['members'].values():
        filename = str(vin_path.parent / member['file'])
        _pinned(dict(file=filename, sha256=context['inputs'][filename]))
    dimensions, buffers, uniforms = {}, {}, {}
    for entry in fixture['resources']:
        filename = entry.get('file')
        if not filename:
            continue
        expected = context['inputs'].get(filename)
        if expected is None:
            raise Unsupported('unpinned diagnostic input: ' + filename)
        payload = _pinned(dict(file=filename, sha256=expected))
        if entry['kind'] == 'cb':
            buffers[entry['slot']] = np.frombuffer(payload, '<u4').reshape(-1, 4)
        elif entry['kind'].startswith('texture'):
            size = _dds_dimensions(entry, payload)
            if 'texture' in entry:
                dimensions[entry['texture']] = size
            values = np.frombuffer(payload, '<u4', offset=148).reshape(-1, 4)
            if entry['slot'] != 8 and (values == values[0]).all():
                uniforms[entry['slot']] = values[0]
        elif entry['kind'] == 'structured':
            values = np.frombuffer(payload, '<u4').reshape(-1, entry['stride'] // 4)
            if (values == values[0]).all():
                uniforms[entry['slot']] = values[0]
    ub = material_ub.evaluate(context['shadermaps']['file'], dimensions)
    if ub['unresolved_floats'] or ub['textures'][4]['parameter'] != 'DistanceTexture' or resources['t']['8'] != dict(ub='Material', res=8, map='texture', param=None, param_index=None, type_index=None) or resources['s']['3']['ub'] != 'Material' or resources['s']['3']['res'] != 9:
        raise Unsupported('source Material UB/t8/s3 mismatch')
    buffers[5] = material_ub.cb_words(ub, len(buffers[5]))
    if runtime.get('view') is not None:
        from . import visioncone_vf
        buffers[0] = visioncone_vf.apply_view(buffers[0], runtime['view'])
    flags = [renderer.get('bindings', {}).get('dyn%d' % index, {}).get('exists_on_source_flag') for index in range(4)]
    tested = set()
    for instruction in shader['instructions']:
        if instruction['opcode'] == 'and' and any(operand['text'].startswith('cb3[25].z') for operand in instruction['operands']):
            for operand in instruction['operands']:
                if operand['text'].startswith('l('):
                    tested.update(int(word, 16).bit_length() - 1 for word in re.findall(r'0x[0-9a-fA-F]+', operand['text']) if int(word, 16))
    if any(bit >= 16 or type(flags[bit // 4]) is not bool for bit in tested):
        raise Unsupported('unknown source dynamic-parameter validity')
    mask = sum(15 << (4 * index) for index, flag in enumerate(flags) if flag is True)
    buffers[3] = buffers[3].copy()
    buffers[3][25, 2] = mask
    dag = ssa.Builder(shader).build()
    for node in dag['nodes']:
        if node['op'] == 'cbload':
            info = node['args'][-1]
            if info['dynamic']:
                raise Unsupported('dynamic source cbuffer indexing')
            node.update(op='const', args=[int(buffers[info['slot']][info['offset'], info['component']])])
        elif node['op'] in ('sample', 'texfetch', 'sbload', 'bufload'):
            info = node['args'][-1]
            if info['slot'] in uniforms:
                node.update(op='const', args=[int(uniforms[info['slot']][info['component']])])
            elif node['op'] == 'sample':
                instruction = next(instruction for instruction in shader['instructions'] if instruction['index'] == node['src'])
                sampler = next(operand['text'] for operand in instruction['operands'] if re.fullmatch(r's\d+', operand['text']))
                info['sampler'] = int(sampler[1:])
                if info['slot'] == 8 and info['sampler'] != 3:
                    raise Unsupported('source runtime sampler is not s3')
    folded, stats = opt.optimize(dag, ssa.Evaluator(shader, fixture))
    # Exact retained PS 280-284: AND a float 0/1 word with a comparison mask.
    # A full uint mask selects the float word; zero clears it. This is not numeric AND.
    for node in folded['nodes']:
        if node['op'] == 'and' and node['src'] == 283:
            word, condition = [folded['nodes'][a] for a in node['args']]
            if (word['op'] != 'movc' or word['src'] != 282 or condition['type'] != 'bool-mask' or
                [folded['nodes'][a]['args'] for a in word['args'][1:]] != [[0], [0x3F800000]]):
                raise Unsupported('retained source float-mask idiom changed')
            node.update(op='movc', args=[condition['id'], word['id'], word['args'][1]], type='f32')
    runtime_nodes = [node for node in folded['nodes'] if node['op'] == 'sample' and node['args'][-1]['slot'] == 8]
    if not runtime_nodes:
        raise Unsupported('runtime t8 was folded away')
    signatures = {'v' + row['Register']: row['Name'] + row['Index'] for row in shader['signatures']['input']}
    required = sorted({(signatures[node['args'][0]], node['args'][1]) for node in folded['nodes'] if node['op'] == 'input'})
    volume_nodes = [node for node in folded['nodes'] if node['op'] == 'sample' and node['args'][-1]['slot'] == 9]
    if len(volume_nodes) != 1 or volume_nodes[0]['args'][-1].get('sampler') != 4:
        raise Unsupported('retained source t9/s4 read absent or ambiguous')
    if resources['t']['9']['ub'] != 'Material' or resources['t']['9']['res'] != 10 or resources['s']['4']['ub'] != 'Material' or resources['s']['4']['res'] != 12:
        raise Unsupported('retained volume resource/sampler routing mismatch')
    if len(ub['textures']) != 6 or ub['textures'][5]['type_index'] != 4 or ub['textures'][5]['sampler_source'] != 'SSM_Wrap_WorldGroupSettings':
        raise Unsupported('retained volume shared wrap provenance mismatch')
    source_volume = next(entry for entry in fixture['resources'] if entry.get('slot') == 9 and entry['kind'] == 'texture3d')
    if source_volume['texture'] != ub['textures'][5]['texture']:
        raise Unsupported('retained volume object path mismatch')
    volume.decode(source_volume, _pinned(dict(file=source_volume['file'], sha256=context['inputs'][source_volume['file']])))
    volume_contract = volume.retained_sampler(sampler_payload, 4)
    return dict(shader=shader, vertex=vertex, dag=folded, ub=ub, fixture=fixture,
                resources=resources, source_context=context, stats=stats, required_semantics=required,
                runtime_slots={8: dict(parameter='DistanceTexture', variable='System.Render Target 2D', child='RenderTarget', sampler=3, sampling='bilinear_clamp')},
                samplers={(8, 3): 'bilinear_clamp', (9, 4): volume.POLICY}, volume_resources={9: source_volume}, volume_contract=volume_contract,
                protected_runtime_nodes=len(runtime_nodes), valid_mask=mask,
                classification='offline_emulator_gpu_compute_readback', production_parity='unverified')


def require_supported_sampling(plan):
    resources = {entry['slot']: entry for entry in plan['fixture']['resources'] if entry['kind'].startswith('texture')}
    unsupported = sorted({node['args'][-1]['slot'] for node in plan['dag']['nodes'] if node['op'] == 'sample' and resources[node['args'][-1]['slot']]['kind'] != 'texture2d' and not (
        resources[node['args'][-1]['slot']]['kind'] == 'texture3d' and node['args'][-1]['slot'] in plan.get('volume_resources', {}) and
        plan.get('samplers', {}).get((node['args'][-1]['slot'], node['args'][-1].get('sampler'))) == volume.POLICY)})
    if unsupported:
        raise Unsupported('current source lowerer lacks source-supported 3D sampling: ' + ','.join('t%d' % slot for slot in unsupported))


def store_cone(graph, geometry, renderer, camera, rotation, scale):
    if (renderer.get('emitter'), renderer.get('ordinal'), renderer.get('kind')) != (2, 0, 'mesh'):
        raise Unsupported('unsupported VisionCone renderer')
    if renderer.get('props', {}).get('FacingMode') != 'Default':
        raise Unsupported('unverified source VisionCone facing mode')
    flags = [renderer.get('bindings', {}).get(name, {}).get('exists_on_source_flag') for name in ('dyn0', 'dyn1')]
    if any(type(flag) is not bool for flag in flags):
        raise Unsupported('unknown source cone dynamic-parameter validity')
    separate = graph.node('ShaderNodeSeparateXYZ')
    graph.link(scale, separate.inputs[0])
    axis = graph.rotate_vector((0.0, 0.0, -1.0), rotation)
    axis = graph.vmath('SCALE', axis, scale=graph.math('SIGN', separate.outputs['Z']))
    axis = graph.vmath('NORMALIZE', axis)
    direction = graph.vmath('NORMALIZE', graph.vmath('SUBTRACT', camera['pos'], graph.node('GeometryNodeInputPosition').outputs[0]))
    cosine = graph.vmath('DOT_PRODUCT', axis, direction)
    absolute = graph.math('ABSOLUTE', cosine)
    polynomial = graph.math('ADD', 1.5707963705062866, graph.math('MULTIPLY', -0.156583, absolute))
    root = graph.math('SQRT', graph.math('SUBTRACT', 1.0, absolute))
    angle = graph.math('MULTIPLY', polynomial, root)
    angle = graph.switch('FLOAT', graph.math('LESS_THAN', cosine, 0.0), angle, graph.math('SUBTRACT', 3.1415927410125732, angle))
    width = graph.attr('dyn0_2') if flags[0] else 1.0
    minimum = graph.attr('dyn1_0') if flags[1] else 1.0
    maximum = graph.attr('dyn1_1') if flags[1] else 1.0
    ratio = graph.math('DIVIDE', graph.math('MULTIPLY', angle, 57.295780181884766), width)
    ratio = graph.math('MINIMUM', 1.0, graph.math('MAXIMUM', 0.0, ratio))
    ratio = graph.switch('FLOAT', graph.math('COMPARE', width, 0.0, 0.0), ratio, graph.math('GREATER_THAN', angle, 0.0))
    fade = graph.math('ADD', minimum, graph.math('MULTIPLY', ratio, graph.math('SUBTRACT', maximum, minimum)))
    components = graph.node('ShaderNodeSeparateXYZ')
    graph.link(graph.vmath('MULTIPLY', axis, (1.0, -1.0, 1.0)), components.inputs[0])
    geometry = graph.store(geometry, 'nx_vc_axis_angle', 'FLOAT_COLOR', graph.color_attr([components.outputs['X'], components.outputs['Y'], components.outputs['Z'], ratio]))
    return graph.store(geometry, 'nx_vc_fade0', 'FLOAT', fade)


def build(object_path, kind, resolver):
    if not matches(object_path, kind):
        return None
    plan = prepare(resolver.roots, getattr(resolver, 'source_runtime', None))
    resolver.source_plan = plan
    require_supported_sampling(plan)
    import bpy
    from . import lower_dag, lower_blender as lb, visioncone_vf, visioncone_runtime
    runtime = resolver.source_runtime
    visioncone_vf.validate_view(runtime.get('view'), bpy.context.scene)
    if not runtime.get('geometry_context'):
        raise Unsupported('validated source mesh/particle producers required before material admission')
    record = visioncone_runtime.prepare(runtime, runtime['view'])
    textures = {8: visioncone_runtime.image(record)}
    for resource in plan['fixture']['resources']:
        if not resource['kind'].startswith('texture') or resource['slot'] in textures:
            continue
        slot = resource['slot']
        if not any(node['op'] == 'sample' and node['args'][-1]['slot'] == slot for node in plan['dag']['nodes']):
            continue
        payload = _pinned(dict(file=resource['file'], sha256=plan['source_context']['inputs'][resource['file']]))
        if slot in plan['volume_resources']:
            textures[slot] = volume.image(resource, payload, plan['volume_contract'])
            static_image = textures[slot].image
        else:
            width, height, depth = _dds_dimensions(resource, payload)
            if depth != 1:
                raise Unsupported('unadmitted non-2D material resource')
            image = bpy.data.images.new('NX_VisionCone_t%d' % slot, width=width, height=height, alpha=True, float_buffer=True)
            image.colorspace_settings.name, image.alpha_mode = 'Non-Color', 'CHANNEL_PACKED'
            image.pixels.foreach_set(np.flipud(np.frombuffer(payload, '<f4', offset=148).reshape(height,width,4)).ravel())
            image.update()
            textures[slot] = image
            static_image = image
        static_image['dr_nx_visioncone_static'] = json.dumps(dict(resource=resource,sha256=plan['source_context']['inputs'][resource['file']]),sort_keys=True)
        static_image['dr_nx_static_error'] = ''
    material = bpy.data.materials.new('NXM_VisionCone_diagnostic')
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    inputs = visioncone_vf.sources(tree, plan, runtime['view'])
    outputs = ['o0.' + lane for lane in 'xyzw'] + ['kill']
    result = lower_dag.lower(tree, plan['dag'], inputs, textures, outputs, 'production', samplers=plan['samplers'])
    rgb = tree.nodes.new('ShaderNodeCombineColor')
    for index, lane in enumerate('xyz'):
        tree.links.new(result['o0.' + lane], rgb.inputs[index])
    emission = tree.nodes.new('ShaderNodeEmission')
    tree.links.new(rgb.outputs[0], emission.inputs['Color'])
    transparent = tree.nodes.new('ShaderNodeBsdfTransparent')
    gate = tree.nodes.new('ShaderNodeValue')
    gate.name = 'NX runtime valid'
    gate.outputs[0].default_value = 1.
    survive = lb.mul(tree, lb.math(tree, 'SUBTRACT', 1., result['kill']), gate.outputs[0])
    mix = tree.nodes.new('ShaderNodeMixShader')
    tree.links.new(survive, mix.inputs[0])
    tree.links.new(transparent.outputs[0], mix.inputs[1])
    tree.links.new(emission.outputs[0], mix.inputs[2])
    output = tree.nodes.new('ShaderNodeOutputMaterial')
    tree.links.new(mix.outputs[0], output.inputs['Surface'])
    material['dr_nx_ps'], material['dr_nx_vf'] = PS, VF
    material['dr_nx_material'] = str(object_path)
    material['dr_nx_runtime_image'] = textures[8].name
    material['dr_nx_view_domain'] = runtime['view']['domain']
    material['dr_nx_classification'] = plan['classification']
    material['dr_nx_production_parity'] = 'unverified'
    material['dr_nx_owner_socket_transform'] = 'unknown'
    return material
