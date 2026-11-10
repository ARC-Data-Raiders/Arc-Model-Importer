"""Non-destructive facial fitting from authored cloth cages and cooked mappings.

The source cage is embedded in the facial mesh; the target cage is selected by
an exact head visual-part reference and matching authored slot/topology. No
nearest-surface association or arbitrary reference head is used.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import numpy as np
import bpy
from mathutils import Vector
from . import hair_placement, import_layout; from ..Shared import pak_extract, utils

_KEY = 'ARC_AuthoredHeadWrap'
_CACHE = {}
_APPLIED = {}


def reset_cache():
    """Drop object-pointer and filesystem caches on load/unregister."""
    _CACHE.clear()
    _APPLIED.clear()


def _source(obj):
    source = hair_placement._source(obj)
    if not source:
        original = str(obj.get('arc_source_mesh') or obj.get('arc_psk_path') or '')
        source = os.path.splitext(original)[0] + '.uemodel' if original else ''
    normalized = source.replace('\\', '/')
    lower = normalized.casefold()
    for marker in ('/pioneergame/content/', '/remappedplugins/content/'):
        index = lower.find(marker)
        if index >= 0:
            package = normalized[index + 1:-len('.uemodel')]
            latest = pak_extract._expected_uemodel(normalized[:index], package) or source
            current = pak_extract._expected_uemodel(pak_extract.cache_dir(), package)
            if current and os.path.isfile(_sidecar(current)) and (not os.path.isfile(latest) or not os.path.isfile(_sidecar(latest))):
                return current
            return latest
    return source


def _sidecar(path):
    direct = os.path.splitext(path)[0] + '.arc-cloth.json'
    candidates = [direct]
    normalized = direct.replace('\\', '/')
    for original, alternate in (('/RemappedPlugins/Content/', '/PioneerGame/Content/'), ('/PioneerGame/Content/', '/RemappedPlugins/Content/')):
        index = normalized.casefold().find(original.casefold())
        if index >= 0:
            candidates.append(normalized[:index] + alternate + normalized[index + len(original):])
    from .pak_catalog import filesystem_path
    candidates = [filesystem_path(os.path.normpath(candidate)) for candidate in candidates]
    existing = [candidate for candidate in candidates if os.path.isfile(candidate)]
    return max(existing, key=os.path.getmtime) if existing else direct


def _is_static(source):
    if not source or not os.path.isfile(source):
        return False
    return not hair_placement.uemodel_import.parse_uemodel_bones(source)


def _read(path):
    try:
        stat = os.stat(path)
        stamp = (stat.st_mtime_ns, stat.st_size)
        if path in _CACHE and _CACHE[path][0] == stamp:
            return _CACHE[path][1]
        with open(path, encoding='utf-8-sig') as stream:
            data = json.load(stream)
        if data.get('schema') != 'dataraiders.cloth-wrap' or data.get('version') != 1:
            raise ValueError('Unsupported authored cloth sidecar schema')
        if data.get('coordinate_system') != 'unreal' or data.get('units') != 'centimeters':
            raise ValueError('Unexpected authored cloth coordinate system')
        _CACHE[path] = (stamp, data)
        return data
    except FileNotFoundError:
        return None


def _payload_root(source):
    norm = source.replace('\\', '/')
    for marker in ('/pioneergame/content/', '/remappedplugins/content/'):
        index = norm.casefold().find(marker)
        if index >= 0:
            return norm[:index]
    return ''


def _resolve_package(source, package):
    root = _payload_root(source)
    if not package:
        return ''
    original = pak_extract._expected_uemodel(root, package) if root else ''
    current = pak_extract._expected_uemodel(pak_extract.cache_dir(), package)
    if current and os.path.isfile(_sidecar(current)) and (not original or not os.path.isfile(_sidecar(original))):
        return current
    return original or current


def _head_wrap_path(head):
    source = _source(head)
    meta = _read(_sidecar(source))
    reference = (meta or {}).get('wrap_mesh') or ''
    if not reference:
        # Exported-file workflows may have the exact visual part JSON alongside
        # the head, even when they do not have the new mesh sidecar yet.
        stem = Path(source).stem.casefold()
        folders = {Path(source).parent, Path(_sidecar(source)).parent}
        paths = sorted({path for folder in folders for path in folder.glob('Part_Head*.json')})
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                continue
            for entry in utils.ue_export_entries(data):
                props = entry.get('Properties') or {}
                mesh = props.get('Mesh') or {}
                mesh_path = mesh.get('ObjectPath') or mesh.get('AssetPathName') or ''
                if Path(mesh_path.split('.')[0]).name.casefold() != stem:
                    continue
                wrap = (props.get('WrapDeformation') or {}).get('OptionalWrapDeformerMesh') or {}
                reference = wrap.get('ObjectPath') or wrap.get('AssetPathName') or ''
                if reference:
                    break
            if reference:
                break
    if not reference:
        return ''
    # Object references may end in a numeric export suffix.
    return _resolve_package(source, reference.split('.')[0])


def _weights(head):
    keys = head.data.shape_keys
    return {key.name.casefold(): float(key.value) for key in keys.key_blocks if key.name != keys.reference_key.name} if keys else {}


def _cage_vertices(cage, weights, morph_names=()):
    positions = np.asarray(cage['vertices'], dtype=np.float64).copy()
    for morph in cage.get('morph_targets') or []:
        index = int(morph.get('index', -1))
        name = str(morph.get('name') or (morph_names[index] if 0 <= index < len(morph_names) else '')).casefold()
        value = weights.get(name, 0.0)
        if not value:
            continue
        rows = np.asarray(morph.get('deltas') or [], dtype=np.float64)
        if rows.size:
            rows = rows.reshape(-1, 7)
            ids = rows[:, 0].astype(np.int64)
            if np.any(ids < 0) or np.any(ids >= len(positions)):
                raise ValueError('Target cage morph index outside its authored topology')
            np.add.at(positions, ids, rows[:, 1:4] * value)
    return positions


def transfer_positions(render, source_cage, target_cage, mapping):
    """Transport exact mapped offsets through corresponding authored triangles.

    The stored barycentric binding identifies the triangle and surface anchor.
    Source render offsets are retained in that triangle's tangent/normal frame;
    identity cages therefore reproduce every source point exactly. This static
    transfer preserves offsets without substituting the proxy cage for hair.
    """
    rows = np.asarray(mapping, dtype=np.float64)
    if rows.shape != (len(render), 17):
        raise ValueError('Only complete single-influence LOD0 cloth mappings are supported')
    indices = rows[:, :3].astype(np.int64)
    if np.any(indices < 0) or np.any(indices >= len(source_cage)):
        raise ValueError('Cloth binding index outside its source cage')
    if np.any(rows[:, 3] != 0):
        raise ValueError('Unsupported fixed/skinned cloth binding flag')
    weight = rows[:, 16]
    if np.any(weight < 0) or np.any(weight > 1.00001):
        raise ValueError('Unsupported cloth influence weight')
    source = source_cage[indices]
    target = target_cage[indices]
    bary = rows[:, 4:7]
    if np.max(np.abs(bary.sum(axis=1) - 1.0)) > .001:
        raise ValueError('Invalid barycentric cloth coordinates')
    def frame(triangles):
        first = triangles[:, 1] - triangles[:, 0]
        second = triangles[:, 2] - triangles[:, 0]
        normal = np.cross(first, second)
        length = np.linalg.norm(normal, axis=1)
        if np.any(length < 1e-10):
            raise ValueError('Degenerate authored cage triangle')
        normal /= length[:, None]
        return np.stack((first, second, normal), axis=2)
    source_anchor = np.einsum('ni,nij->nj', bary, source)
    target_anchor = np.einsum('ni,nij->nj', bary, target)
    offset = np.linalg.solve(frame(source), (render - source_anchor)[..., None])[..., 0]
    fitted = target_anchor + np.einsum('nij,nj->ni', frame(target), offset)
    return render + (fitted - render) * np.clip(weight, 0, 1)[:, None]


def _center(obj):
    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return Vector(tuple((min(p[i] for p in points) + max(p[i] for p in points)) * .5 for i in range(3)))


def _references(obj, scene):
    from . import reference_planes
    role = import_layout.infer_role(obj)
    slot = import_layout.slot_of_object(obj)
    result = []
    for plane in scene.objects:
        if not plane.get('arc_reference_plane') or reference_planes._reference_role(plane) != role:
            continue
        # Fixed per-type slot defaults do not follow changes in the fitted
        # mesh's bounds. Older cards retain their established AABB follow.
        if int(plane.get('arc_reference_layout_schema') or 0) >= 2:
            continue
        if slot is not None and import_layout.slot_of_object(plane) != slot:
            continue
        if plane.parent == obj or (obj.parent is not None and plane.parent == obj.parent):
            result.append((plane, plane.matrix_world.copy()))
    return result


def _head_package(head):
    source = str(head.get('arc_source_mesh') or head.get('arc_psk_path') or '').replace('\\', '/')
    for marker in ('/pioneergame/content/', '/remappedplugins/content/'):
        index = source.casefold().find(marker)
        if index >= 0:
            return source[index + len(marker):].rsplit('.', 1)[0].casefold()
    return source.casefold()


def _saved_fit_matches(obj, head):
    keys = obj.data.shape_keys
    return bool(obj.get('arc_head_wrap_managed') and keys and keys.key_blocks.get(_KEY)
                and obj.get('arc_head_wrap_head') == head.name
                and obj.get('arc_head_wrap_head_package') == _head_package(head))


def release_object(obj, scene=None):
    scene = scene or bpy.context.scene
    keys = obj.data.shape_keys
    key = keys.key_blocks.get(_KEY) if keys else None
    if key is not None and obj.get('arc_head_wrap_managed'):
        if obj.data.users > 1:
            obj.data = obj.data.copy()
            key = obj.data.shape_keys.key_blocks.get(_KEY)
        bpy.context.view_layer.update()
        old_center = _center(obj)
        references = _references(obj, scene)
        key.value = 0.0
        obj.data.update()
        bpy.context.view_layer.update()
        delta = _center(obj) - old_center
        for plane, world in references:
            world.translation += delta
            plane.matrix_world = world
    for name in ('arc_head_wrap_head', 'arc_head_wrap_head_package', 'arc_head_wrap_source', 'arc_head_wrap_managed'):
        if name in obj:
            del obj[name]
    _APPLIED.pop(obj.as_pointer(), None)


def fit_object(obj, head, scene=None):
    """Fit an existing mesh in place; return a status and diagnostic message."""
    scene = scene or bpy.context.scene
    source = _source(obj)
    if _is_static(source):
        return 'no_cloth', 'Static hairstyle uses its authored head reference-bone placement'
    data = _read(_sidecar(source))
    if data is None:
        return 'metadata_missing', 'Extract this facial mesh with current cloth sidecar support'
    sections = data.get('sections') or []
    if not sections:
        return 'no_cloth', 'This asset has no authored cloth binding; gender targets remain available'
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    target_path = _head_wrap_path(head)
    target_data = _read(_sidecar(target_path)) if target_path else None
    if target_data is None:
        return 'head_metadata_missing', 'Exact head OptionalWrapDeformerMesh sidecar is missing'
    source_lod = hair_placement.uemodel_import.parse_uemodel(source)[2][0]
    source_vertices = np.asarray(source_lod.vertices, dtype=np.float64)
    if len(source_vertices) != len(obj.data.vertices) or int(data['vertex_count']) != len(source_vertices):
        raise ValueError('Cloth sidecar/render topology does not match existing mesh')
    frame = hair_placement._frame(obj, source)
    head_source = _source(head)
    head_frame = hair_placement._frame(head, head_source)
    component = obj.matrix_world @ frame
    target_component = head.matrix_world @ head_frame
    relative = np.asarray(component.inverted() @ target_component, dtype=np.float64)
    weights = _weights(head)
    signature = (head.as_pointer(), head.data.as_pointer(), source, target_path,
                 tuple(sorted(weights.items())), tuple(relative.ravel()), obj.data.as_pointer(),
                 tuple((p, os.stat(p).st_mtime_ns) for p in (_sidecar(source), _sidecar(target_path))))
    current_keys = obj.data.shape_keys
    current_key = current_keys.key_blocks.get(_KEY) if current_keys else None
    if (_APPLIED.get(obj.as_pointer()) == signature and current_key is not None
            and obj.get('arc_head_wrap_managed') and abs(current_key.value - 1.0) < 1e-6):
        return 'fitted', 'Authored cloth cage transfer already current'
    converted = np.array((.01, -.01, .01))
    fitted = source_vertices.copy()
    covered = np.zeros(len(source_vertices), dtype=bool)
    source_cages = {(c['slot'], int(c['lod'])): c for c in data.get('cages') or []}
    target_cages = {(c['slot'], int(c['lod'])): c for c in target_data.get('cages') or []}
    for section in sections:
        slot = section['cage_slot']
        identity = (slot, int(section['cloth_lod']))
        old = source_cages.get(identity)
        new = target_cages.get(identity)
        if not old or not new:
            raise ValueError('Missing exact authored cage slot ' + slot)
        if old['indices'] != new['indices'] or len(old['vertices']) != len(new['vertices']):
            raise ValueError('Source/target authored cage topology differs for ' + slot)
        start = int(section['base_vertex'])
        count = int(section['vertex_count'])
        if start < 0 or start + count > len(fitted) or np.any(covered[start:start+count]):
            raise ValueError('Overlapping or invalid authored render section')
        old_points = np.asarray(old['vertices'], dtype=np.float64) * converted
        target_points = _cage_vertices(new, weights, target_data.get('render_morph_names') or []) * converted
        target_points = target_points @ relative[:3, :3].T + relative[:3, 3]
        fitted[start:start+count] = transfer_positions(source_vertices[start:start+count], old_points, target_points, section['mapping'])
        covered[start:start+count] = True
    # The target cage already includes the selected head's gender. The facial
    # render mesh's existing authored gender keys remain editable/driven, so
    # subtract their expected head contribution from this corrective key.
    correction = fitted - source_vertices
    for morph in source_lod.morph_targets:
        if morph.name.casefold() not in {'male', 'female'}:
            continue
        value = weights.get(morph.name.casefold(), 0.0)
        if value:
            selected = covered[morph.indices]
            np.add.at(correction, morph.indices[selected], -np.asarray(morph.position_deltas)[selected] * value)
    basis = obj.data.shape_keys.reference_key if obj.data.shape_keys else obj.shape_key_add(name='Basis', from_mix=False)
    values = np.empty((len(obj.data.vertices), 3), dtype=np.float64)
    basis.data.foreach_get('co', values.ravel())
    values += correction @ np.asarray(frame.to_3x3(), dtype=np.float64).T
    key = obj.data.shape_keys.key_blocks.get(_KEY)
    if key is not None and not (obj.get('arc_head_wrap_managed') or obj.get('arc_head_wrap_key_owned')):
        raise ValueError('User shape key uses reserved wrap name; refusing to overwrite')
    bpy.context.view_layer.update()
    old_center = _center(obj)
    references = _references(obj, scene)
    if key is None:
        key = obj.shape_key_add(name=_KEY, from_mix=False)
    key.relative_key = basis
    key.data.foreach_set('co', values.ravel())
    key.value = 1.0
    obj['arc_head_wrap_managed'] = True
    obj['arc_head_wrap_key_owned'] = True
    obj['arc_head_wrap_head'] = head.name
    obj['arc_head_wrap_head_package'] = _head_package(head)
    obj['arc_head_wrap_source'] = _sidecar(target_path)
    obj['arc_head_fit_note'] = 'Authored cage binding and head morphs transferred; static offset-preserving fit, no cloth simulation'
    obj.data.update()
    bpy.context.view_layer.update()
    delta = _center(obj) - old_center
    for plane, world in references:
        world.translation += delta
        plane.matrix_world = world
    _APPLIED[obj.as_pointer()] = signature
    return 'fitted', obj['arc_head_fit_note']


def metadata_paths(objects):
    """Plan incomplete exact packages, including old head sidecars with null refs."""
    from . import character_fit
    from .pak_catalog import canonical_path
    paths = []
    for obj in objects:
        source = _source(obj)
        if not source or _is_static(source):
            continue
        metadata = _read(_sidecar(source))
        dependency = (metadata or {}).get('wrap_mesh') or ''
        if metadata is None or (character_fit.is_head(obj) and not dependency):
            root = _payload_root(source)
            if root:
                package = source.replace('\\', '/')[len(root)+1:-len('.uemodel')]
                paths.append(canonical_path(package))
        elif dependency:
            target = _resolve_package(source, dependency)
            if not target or not os.path.isfile(_sidecar(target)):
                paths.append(canonical_path(dependency.split('.')[0]))
    return list(dict.fromkeys(paths))


def ensure_metadata(scene, objects):
    """Explicit main-thread refresh; metadata_paths also supports external workers."""
    paths = metadata_paths(objects)
    if paths:
        game = pak_extract.configured_dir(scene)
        if not game:
            return {'refreshed': 0, 'error': 'No configured Pak directory'}
        code, log, _meshes = pak_extract.extract_paths(game, pak_extract.resolve_usmap(scene), paths)
        if code:
            return {'refreshed': 0, 'error': log}
    return {'refreshed': len(paths)}


def sync_scene(scene=None, *, refresh_metadata=False, enabled=True):
    from . import character_fit
    scene = scene or bpy.context.scene
    meshes = [o for o in scene.objects if o.type == 'MESH' and not o.get('arc_reference_plane') and import_layout.infer_role(o) in {'Hair', 'FacialHair'}]
    result = {'fitted': 0, 'missing_head': 0, 'no_cloth': 0, 'metadata_missing': 0, 'head_metadata_missing': 0, 'preserved_saved_fit': 0, 'errors': []}
    if refresh_metadata:
        result['refresh'] = ensure_metadata(scene, meshes + [o for o in scene.objects if character_fit.is_head(o)])
    if not enabled:
        for obj in meshes:
            release_object(obj)
        result['disabled'] = True
        return result
    for obj in meshes:
        head = character_fit.head_for_object(obj, scene)
        if head is None:
            release_object(obj)
            result['missing_head'] += 1
            continue
        try:
            status, note = fit_object(obj, head, scene)
            result[status] = result.get(status, 0) + 1
            if status != 'fitted':
                if status in {'metadata_missing', 'head_metadata_missing'} and _saved_fit_matches(obj, head):
                    result['preserved_saved_fit'] += 1
                    obj['arc_head_fit_note'] = 'Saved authored head fit retained; local cloth metadata unavailable'
                else:
                    release_object(obj, scene)
                    obj['arc_head_fit_note'] = note
        except (ValueError, OSError, KeyError) as exc:
            release_object(obj)
            result['errors'].append({'object': obj.name, 'error': str(exc)})
    return result
