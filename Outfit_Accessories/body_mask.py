"""Authored body coverage: exact active visual parts and UV1 region controls."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import bpy

from . import outfit_catalog; from ..Shared import textures, utils

REGIONS = ('Chest', 'Hips', 'UpperArm', 'LowerArm', 'Thigh', 'Calf', 'Foot', 'Hand', 'Thumb', 'Index', 'Middle', 'Ring', 'Pinky')
REGION_NAMES = tuple(name if i < 2 else side + name for i, name in enumerate(REGIONS) for side in (('',) if i < 2 else ('L_', 'R_')))
_GROUP = 'ArcBodyCoverageUV1'
_SCHEMA = 1
_JSON_CACHE = {}


def region_name(index, right=False):
    return REGIONS[index] if index < 2 else ('R_' if right else 'L_') + REGIONS[index]


def coverage_interval(value):
    split, below, above = value[:3]
    return split * (1.0 - below), split + (1.0 - split) * above


def coverage_at_uv(uv, masks, hidden=False, *, blender_uv=True):
    """CPU equivalent of the coverage shader, for diagnostics/verification."""
    if hidden:
        return 0.0
    u, v = float(uv[0]), float(uv[1])
    if blender_uv:
        v = 1.0 - v
    index = math.ceil(u) - 1
    if not 0 <= index < len(REGIONS):
        return 0.0
    name = region_name(index, math.floor(v) > 0.5)
    low, high = coverage_interval(masks.get(name, (1.0, 1.0, 1.0)))
    position = v - math.floor(v)
    return float(low <= position <= high)


def _load_json(path):
    try:
        stamp = (os.path.getmtime(path), os.path.getsize(path))
        cached = _JSON_CACHE.get(path)
        if cached and cached[0] == stamp:
            return cached[1]
        with open(path, encoding='utf-8-sig') as stream:
            data = json.load(stream)
        _JSON_CACHE[path] = (stamp, data)
        return data
    except (OSError, ValueError):
        return {}


def _reference(value):
    if isinstance(value, dict):
        return value.get('ObjectPath') or value.get('AssetPathName') or ''
    return value if isinstance(value, str) else ''


def _source(obj):
    return str(obj.get('arc_source_mesh') or obj.get('arc_psk_path') or '')


def _payload_root(source):
    normalized = source.replace('\\', '/')
    lower = normalized.casefold()
    for mount in ('/pioneergame/content/', '/remappedplugins/content/'):
        if mount in lower:
            return normalized[:lower.index(mount)]
    return ''


def _part_folders(source):
    folder = os.path.dirname(source)
    folders = [folder]
    normalized = folder.replace('\\', '/')
    lower = normalized.casefold()
    for original, alternate in (('/RemappedPlugins/Content/', '/PioneerGame/Content/'), ('/PioneerGame/Content/', '/RemappedPlugins/Content/')):
        index = lower.find(original.casefold())
        if index >= 0:
            folders.append(normalized[:index] + alternate + normalized[index + len(original):])
    return folders


def definitions_for_mesh(obj):
    """Only DA_VP files that explicitly bind this exact mesh or a mesh modifier."""
    source = _source(obj)
    if not source:
        return []
    stem = os.path.splitext(os.path.basename(source))[0].casefold()
    result = []
    seen = set()
    for folder in _part_folders(source):
        try:
            paths = sorted(Path(folder).glob('DA_VP_*.json'))
        except OSError:
            continue
        for path in paths:
            data = _load_json(str(path))
            entries = list(utils.ue_export_entries(data))
            part = next((entry for entry in entries if entry.get('Type') == 'CustomizationVisualPartDataAsset'), None)
            if part is None:
                continue
            references = [_reference((part.get('Properties') or {}).get('Mesh'))]
            for entry in entries:
                if entry.get('Type') == 'CustomizationVisualPartSetMeshModifier':
                    references.append(_reference((entry.get('Properties') or {}).get('SkeletalMesh')))
            if not any(outfit_catalog.canonical_package(ref).rsplit('/', 1)[-1].casefold() == stem for ref in references if ref):
                continue
            package = part.get('Package') or str(path)
            if package.casefold() in seen:
                continue
            seen.add(package.casefold())
            result.append({'package': package, 'path': str(path), 'source_mesh': source,
                           'properties': part.get('Properties') or {}, 'exports': entries})
    if len(result) > 1:
        explicit = str(obj.get('arc_visual_part') or obj.get('arc_visual_part_path') or '')
        if explicit:
            identity = outfit_catalog.canonical_package(explicit).casefold()
            selected = [definition for definition in result if outfit_catalog.canonical_package(definition['package']).casefold() == identity
                        or os.path.normcase(definition['path']) == os.path.normcase(explicit)]
            if len(selected) == 1:
                return selected
        conventional = 'da_vp_' + (stem[3:] if stem.startswith(('sk_', 'sm_')) else stem)
        selected = [definition for definition in result if definition['package'].rsplit('/', 1)[-1].casefold() == conventional]
        if len(selected) == 1:
            return selected
        # Shared geometry alone does not establish which mutually exclusive
        # visual-part variant is active. Do not combine unrelated body masks.
        signatures = {json.dumps([definition['properties'].get('MaterialParameterOverrides'),
                                  definition['properties'].get('BroadcastTags'), definition['properties'].get('TagOverrides')], sort_keys=True)
                      for definition in result}
        if len(signatures) > 1:
            return [{'package': '', 'path': '', 'source_mesh': source, 'properties': {}, 'exports': [],
                     'warning': 'Ambiguous active visual part for ' + os.path.basename(source) + ': ' + '; '.join(definition['package'] for definition in result)}]
        return result[:1]
    return result


def _is_body(obj):
    if getattr(obj, 'type', '') != 'MESH':
        return False
    from . import character_fit
    return character_fit.is_body(obj)


def _rig(obj):
    for modifier in getattr(obj, 'modifiers', ()):
        if modifier.type == 'ARMATURE' and modifier.object:
            return modifier.object
    parent = getattr(obj, 'parent', None)
    while parent:
        if parent.type == 'ARMATURE':
            return parent
        parent = parent.parent
    return None


def character_objects(body, scene):
    from . import character_fit
    matcher = getattr(character_fit, 'character_objects', None)
    if matcher:
        return list(matcher(body, scene))
    body_rig = _rig(body)
    body_slot = body.get('arc_import_slot')
    one_body = sum(_is_body(obj) for obj in scene.objects) == 1
    result = []
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        rig = _rig(obj)
        if body_rig is not None and rig is not None:
            matches = body_rig == rig
        elif body_slot is not None and obj.get('arc_import_slot') is not None:
            matches = int(body_slot) == int(obj.get('arc_import_slot'))
        else:
            matches = one_body and body_slot is None and obj.get('arc_import_slot') is None
        if matches or obj == body:
            result.append(obj)
    return result


def query_matches(query, active_tags):
    """Evaluate UE GameplayTagQuery tag/expr ANY, ALL and NONE token expressions."""
    names = [str(entry.get('TagName') or '') for entry in query.get('TagDictionary') or []]
    tokens = query.get('QueryTokenStream') or []
    if not tokens:
        return True
    position = 2
    def expression():
        nonlocal position
        kind, count = int(tokens[position]), int(tokens[position + 1]); position += 2
        values = []
        for _ in range(count):
            if kind <= 3:
                name = names[int(tokens[position])]; position += 1
                values.append(any(tag == name or tag.startswith(name + '.') for tag in active_tags))
            else:
                values.append(expression())
        if kind in (1, 4):
            return any(values)
        if kind in (2, 5):
            return all(values)
        if kind in (3, 6):
            return not any(values)
        return False
    try:
        return bool(tokens[1]) and expression()
    except (IndexError, TypeError, ValueError):
        return False


def _conditional_exports(definition, tags):
    by_name = {entry.get('Name'): entry for entry in definition['exports']}
    result = []
    for rule in definition['properties'].get('TagOverrides') or []:
        if not query_matches(rule.get('TagOverrideQuery') or {}, tags):
            continue
        for reference in rule.get('Modifiers') or []:
            name = str(reference.get('ObjectName') or '').rsplit(':', 1)[-1].rstrip("'")
            entry = by_name.get(name)
            if entry:
                result.append(entry)
    return result


def _resolve_template(reference, source):
    package = outfit_catalog.canonical_package(reference)
    root = _payload_root(source)
    if root and package.casefold().startswith('pioneergame/content/'):
        tail = package[len('PioneerGame/Content/'):]
        for mount in ('PioneerGame/Content', 'RemappedPlugins/Content'):
            candidate = os.path.join(root, *mount.split('/'), *tail.split('/')) + '.json'
            if os.path.isfile(candidate):
                return candidate
    return textures.find_asset_from_object_path(reference, '.json')


def _region_values(path):
    data = _load_json(path)
    if isinstance(data, dict) and 'Parameters' in data:
        values = (data.get('Parameters') or {}).get('Colors') or {}
    elif isinstance(data, dict) and isinstance(data.get('Colors'), dict):
        # Flat FModel material dump (Textures/Colors/Scalars at the top level).
        values = data['Colors']
    else:
        entry = utils.first_ue_export(data, 'MaterialInstanceConstant') or {}
        values = {parameter.get('ParameterInfo', {}).get('Name'): parameter.get('ParameterValue')
                  for parameter in (entry.get('Properties') or {}).get('VectorParameterValues') or []}
    result = {}
    for name, value in values.items():
        if name not in REGION_NAMES:
            continue
        if isinstance(value, dict):
            result[name] = tuple(float(value.get(channel, 1.0)) for channel in ('R', 'G', 'B'))
        elif isinstance(value, (list, tuple)) and len(value) >= 3:
            result[name] = tuple(float(channel) for channel in value[:3])
    return result


def body_mask_state(body, scene=None):
    scene = scene or bpy.context.scene
    definitions = []
    warnings = []
    seen = set()
    for obj in character_objects(body, scene):
        if obj == body or _is_body(obj) or obj.hide_render:
            continue
        active_definitions = definitions_for_mesh(obj)
        if not active_definitions and '/characters/assets/' in _source(obj).replace('\\', '/').casefold():
            warnings.append('Visual-part metadata unavailable: ' + _source(obj))
        for definition in active_definitions:
            if definition.get('warning'):
                warnings.append(definition['warning'])
                continue
            identity = definition['package'].casefold()
            if identity not in seen:
                seen.add(identity); definitions.append(definition)
    tags = {str(tag) for definition in definitions for tag in definition['properties'].get('BroadcastTags') or []}
    female = str(body.get('arc_body_variant') or '').casefold().startswith('female')
    keys = getattr(body.data, 'shape_keys', None)
    if keys:
        key = next((key for key in keys.key_blocks if key.name.casefold() == 'female'), None)
        if key is not None:
            female = key.value >= 0.5
    tags.add('Character.MorphTarget.' + ('female' if female else 'male'))
    masks = {name: (1.0, 1.0, 1.0) for name in REGION_NAMES}
    sources = []
    for definition in definitions:
        overrides = list(definition['properties'].get('MaterialParameterOverrides') or [])
        for modifier in _conditional_exports(definition, tags):
            overrides.extend((modifier.get('Properties') or {}).get('MaterialParameterOverrides') or [])
        for override in overrides:
            if override.get('FilterOnMaterialSlotName') not in (None, '', 'Body'):
                continue
            reference = _reference(override.get('MaterialTemplate'))
            if not reference or reference in sources:
                continue
            path = _resolve_template(reference, definition['source_mesh'])
            values = _region_values(path) if path else {}
            if values:
                masks.update(values); sources.append(reference)
            elif 'bodymask' in reference.casefold():
                warnings.append('Body mask template unavailable: ' + reference)
    # BodyA/B's authored TagOverrides evaluate Hide then Show. The explicit
    # Show rule therefore restores short-sleeve exposed skin when both are active.
    hidden = 'Character.Body.Hide' in tags and 'Character.Body.Show' not in tags
    return {'masks': masks, 'sources': sources, 'hidden': hidden, 'tags': sorted(tags),
            'definitions': [definition['package'] for definition in definitions], 'warnings': warnings}


def _coverage_group():
    group = bpy.data.node_groups.get(_GROUP)
    if group is not None and group.get('arc_body_coverage_schema') == _SCHEMA:
        return group
    if group is not None:
        group.name = _GROUP + '_previous'
    group = bpy.data.node_groups.new(_GROUP, 'ShaderNodeTree')
    group['arc_body_coverage_schema'] = _SCHEMA
    group.interface.new_socket(name='UV1', in_out='INPUT', socket_type='NodeSocketVector')
    hidden = group.interface.new_socket(name='Hidden', in_out='INPUT', socket_type='NodeSocketFloat'); hidden.default_value = 0.0
    for name in REGION_NAMES:
        socket = group.interface.new_socket(name=name, in_out='INPUT', socket_type='NodeSocketVector'); socket.default_value = (1.0, 1.0, 1.0)
    group.interface.new_socket(name='Visible', in_out='OUTPUT', socket_type='NodeSocketFloat')
    nodes, links = group.nodes, group.links
    inputs = nodes.new('NodeGroupInput'); output = nodes.new('NodeGroupOutput')
    def math_node(operation, *values):
        node = nodes.new('ShaderNodeMath'); node.operation = operation
        for index, value in enumerate(values):
            if isinstance(value, (int, float)):
                node.inputs[index].default_value = value
            else:
                links.new(value, node.inputs[index])
        return node.outputs[0]
    separate = nodes.new('ShaderNodeSeparateXYZ'); links.new(inputs.outputs['UV1'], separate.inputs[0])
    u, blender_v = separate.outputs[0], separate.outputs[1]
    v = math_node('SUBTRACT', 1.0, blender_v)
    region = math_node('SUBTRACT', math_node('CEIL', u), 1.0)
    right = math_node('GREATER_THAN', math_node('FLOOR', v), 0.5)
    position = math_node('FRACT', v)
    total = 0.0
    for index in range(len(REGIONS)):
        chosen = math_node('COMPARE', region, float(index), 0.00001)
        for is_right in ((False,) if index < 2 else (False, True)):
            name = region_name(index, is_right)
            control = nodes.new('ShaderNodeSeparateXYZ'); control.label = name
            links.new(inputs.outputs[name], control.inputs[0])
            split, below, above = control.outputs[0], control.outputs[1], control.outputs[2]
            low = math_node('MULTIPLY', split, math_node('SUBTRACT', 1.0, below))
            high = math_node('ADD', split, math_node('MULTIPLY', math_node('SUBTRACT', 1.0, split), above))
            inside = math_node('MULTIPLY', math_node('SUBTRACT', 1.0, math_node('LESS_THAN', position, low)), math_node('SUBTRACT', 1.0, math_node('GREATER_THAN', position, high)))
            if index > 1:
                inside = math_node('MULTIPLY', inside, right if is_right else math_node('SUBTRACT', 1.0, right))
            total = math_node('ADD', total, math_node('MULTIPLY', chosen, inside))
    visible = math_node('MULTIPLY', math_node('MINIMUM', total, 1.0), math_node('SUBTRACT', 1.0, inputs.outputs['Hidden']))
    links.new(visible, output.inputs['Visible'])
    # Keep helper internals organized without touching the existing skin graph.
    for index, node in enumerate(nodes):
        node.location = ((index % 9) * 190.0, -(index // 9) * 150.0)
        node.width = 160.0
    return group


def _surface_outputs(material):
    outputs=[node for node in material.node_tree.nodes if node.type=='OUTPUT_MATERIAL' and getattr(node,'is_active_output',True)]
    return [node for node in outputs if node.inputs.get('Surface') is not None and node.inputs['Surface'].links]


def _coverage_mode(material):
    outputs=_surface_outputs(material)
    if not outputs:return '',outputs
    # Direct flat Principled roots retain the historical alpha multiplication.
    # Any private/combined shader closure is covered externally as one response.
    mode='alpha' if all(output.inputs['Surface'].links[0].from_node.type=='BSDF_PRINCIPLED' for output in outputs) else 'surface'
    return mode,outputs


def _coverage_connected(material,coverage):
    mode,outputs=_coverage_mode(material)
    if not mode:return False
    if mode=='alpha':
        for output in outputs:
            alpha=output.inputs['Surface'].links[0].from_node.inputs.get('Alpha')
            if alpha is None or not alpha.links:return False
            multiply=alpha.links[0].from_node
            if multiply.type!='MATH' or multiply.operation!='MULTIPLY':return False
            if not multiply.inputs[1].links or multiply.inputs[1].links[0].from_node!=coverage:return False
        return True
    for output in outputs:
        mix=output.inputs['Surface'].links[0].from_node
        if not mix.get('dr_body_coverage_wrapper') or mix.type!='MIX_SHADER':return False
        if not mix.inputs[0].links or mix.inputs[0].links[0].from_node!=coverage:return False
        if not mix.inputs[1].links or not mix.inputs[1].links[0].from_node.get('dr_body_coverage_transparent'):return False
        if not mix.inputs[2].links:return False
        if mix.inputs[2].links[0].from_node.get('dr_body_coverage_wrapper'):return False
    return True


def _external_surface_coverage(material,coverage,outputs):
    nodes,links=material.node_tree.nodes,material.node_tree.links
    transparent=next((node for node in nodes if node.get('dr_body_coverage_transparent')),None)
    if transparent is None:
        transparent=nodes.new('ShaderNodeBsdfTransparent');transparent.name='DR.BodyCoverage.Transparent';transparent.label='Outfit-hidden body';transparent['dr_body_coverage_transparent']=True
    transparent.inputs[0].default_value=(1.,1.,1.,1.);transparent.location=(180,-580)
    for output in outputs:
        source=output.inputs['Surface'].links[0].from_socket;seen=set()
        # Collapse only our own wrappers; never unwrap the native shader closure.
        while source.node.get('dr_body_coverage_wrapper'):
            if source.node.as_pointer() in seen:raise ValueError('Body coverage wrapper cycle')
            seen.add(source.node.as_pointer())
            if not source.node.inputs[2].links:raise ValueError('Body coverage wrapper lost its native response')
            source=source.node.inputs[2].links[0].from_socket
        mix=next((node for node in nodes if node.get('dr_body_coverage_wrapper') and node.get('dr_body_coverage_output')==output.name),None)
        if mix is None:
            mix=nodes.new('ShaderNodeMixShader');mix.name='DR.BodyCoverage.Surface.'+output.name;mix.label='Native body response × outfit coverage';mix['dr_body_coverage_wrapper']=True;mix['dr_body_coverage_output']=output.name
        mix.location=(420,-300);links.new(coverage.outputs['Visible'],mix.inputs[0]);links.new(transparent.outputs[0],mix.inputs[1]);links.new(source,mix.inputs[2]);links.new(mix.outputs[0],output.inputs['Surface'])


def _flat_alpha_coverage(material,coverage,outputs):
    nodes,links=material.node_tree.nodes,material.node_tree.links;seen=set()
    for output in outputs:
        bsdf=output.inputs['Surface'].links[0].from_node
        if bsdf.as_pointer() in seen:continue
        seen.add(bsdf.as_pointer());alpha=bsdf.inputs.get('Alpha')
        if alpha is None:raise ValueError('Body Principled response lacks Alpha input')
        name='ArcBodyCoverageAlpha_'+bsdf.name;multiply=nodes.get(name)
        if multiply is None:
            multiply=nodes.new('ShaderNodeMath');multiply.name=name;multiply.label='Skin Alpha × Outfit Coverage';multiply.operation='MULTIPLY';multiply.location=(80,-350)
        # Preserve an original alpha link/default, including a user's replacement.
        if not alpha.links or alpha.links[0].from_node!=multiply:
            for link in list(multiply.inputs[0].links):links.remove(link)
            if alpha.links:links.new(alpha.links[0].from_socket,multiply.inputs[0])
            else:multiply.inputs[0].default_value=alpha.default_value
        links.new(coverage.outputs['Visible'],multiply.inputs[1]);links.new(multiply.outputs[0],alpha)


def apply_body_masks(body, scene=None, *, state=None):
    if not _is_body(body):return False
    scene=scene or bpy.context.scene;state=state or body_mask_state(body,scene)
    uv_name=body.data.uv_layers[1].name if len(body.data.uv_layers)>1 else ''
    body['arc_body_mask_sources']=json.dumps(state['sources']);body['arc_body_masks']=json.dumps(state['masks'],sort_keys=True);body['arc_body_hidden']=state['hidden'];body['arc_body_mask_warnings']=json.dumps(state['warnings'])
    if not uv_name and not state['hidden']:
        body['arc_body_mask_warnings']=json.dumps(state['warnings']+['UV1 unavailable; region coverage not applied']);return False
    signature=json.dumps([state['masks'],state['hidden'],uv_name],sort_keys=True);changed=False;warnings=list(state['warnings'])
    for slot in body.material_slots:
        material=slot.material
        if not material or not material.use_nodes:continue
        coverage=material.node_tree.nodes.get('ArcBodyCoverage')
        if material.get('arc_body_mask_signature')==signature and coverage is not None and _coverage_connected(material,coverage):continue
        mode,outputs=_coverage_mode(material)
        if not mode:
            warnings.append('Body coverage surface unavailable: '+material.name);continue
        # Mesh DATA slots may be shared even when Material.users is only1.
        shared_mesh=getattr(slot,'link','DATA')!='OBJECT' and getattr(body.data,'users',1)>1
        if material.users>1 or shared_mesh:
            material=material.copy()
            if shared_mesh:slot.link='OBJECT'
            slot.material=material;mode,outputs=_coverage_mode(material)
        nodes,links=material.node_tree.nodes,material.node_tree.links
        coverage=nodes.get('ArcBodyCoverage') or nodes.new('ShaderNodeGroup');coverage.name='ArcBodyCoverage';coverage.label='Authored Body Coverage (UV1)';coverage.node_tree=_coverage_group();coverage.location=(-220,-520)
        uv=nodes.get('ArcBodyCoverageUV') or nodes.new('ShaderNodeUVMap');uv.name='ArcBodyCoverageUV';uv.uv_map=uv_name;uv.location=(-460,-520);links.new(uv.outputs['UV'],coverage.inputs['UV1'])
        coverage.inputs['Hidden'].default_value=float(state['hidden'])
        for name in REGION_NAMES:coverage.inputs[name].default_value=state['masks'][name]
        if mode=='surface':_external_surface_coverage(material,coverage,outputs)
        else:_flat_alpha_coverage(material,coverage,outputs)
        if not _coverage_connected(material,coverage):raise ValueError('Body coverage did not reach active surface')
        if hasattr(material,'surface_render_method'):material.surface_render_method='DITHERED'
        material['arc_body_mask_signature']=signature;material['arc_body_mask_sources']=json.dumps(state['sources']);material['dr_body_mask_response_mode']=mode;changed=True
    body['arc_body_mask_warnings']=json.dumps(warnings)
    return changed


def sync_body_masks(scene=None):
    scene = scene or bpy.context.scene
    return sum(bool(apply_body_masks(obj, scene)) for obj in scene.objects if _is_body(obj))
