"""Share identical default backpack geometry without coupling rigs or material variants."""
from __future__ import annotations
import hashlib
import json
import os
import bpy
import numpy as np

_ROLES = {'BackpackFrame', 'BackpackStrap'}
_SOURCES = {
    'BackpackFrame': '/characters/backpacks/frames/default/sm_backpack_frame_01_a',
    'BackpackStrap': '/characters/backpacks/straps/default/strap/sk_default_strap',
    'CharmConnector': '/characters/backpacks/charms/karabiner/sm_backpackcharm_karabiner',
}


def _source(obj):
    source = str(obj.get('arc_source_mesh') or obj.get('arc_psk_path') or '').replace('\\', '/').casefold()
    return os.path.splitext(source)[0]


def _eligible(obj, roles=None):
    roles = _ROLES if roles is None else roles
    return (obj.type == 'MESH' and obj.get('arc_assembly_role') in roles
            and _source(obj).endswith(_SOURCES[obj.get('arc_assembly_role')])
            and obj.mode == 'OBJECT' and not obj.data.shape_keys and not obj.data.animation_data
            and not obj.data.library and not obj.library)


def mesh_signature(obj):
    """All authored attributes, topology and deform weights; never a count-only match."""
    mesh = obj.data
    digest = hashlib.sha256()
    digest.update(repr((obj.get('arc_assembly_role'), len(mesh.materials),
                        [(g.index, g.name) for g in obj.vertex_groups])).encode())
    for attr in sorted(mesh.attributes, key=lambda value: value.name):
        digest.update(repr((attr.name, attr.data_type, attr.domain, len(attr.data))).encode())
        if not attr.data:
            continue
        for prop in attr.data[0].bl_rna.properties:
            if prop.identifier == 'rna_type':
                continue
            if prop.type not in {'FLOAT', 'INT', 'BOOLEAN'}:
                return None  # Preserve unsupported user attributes independently.
            width = prop.array_length if getattr(prop, 'is_array', False) else 1
            values = np.empty(len(attr.data) * width, dtype=np.float32 if prop.type == 'FLOAT' else np.int32)
            attr.data.foreach_get(prop.identifier, values)
            digest.update(prop.identifier.encode()); digest.update(values.tobytes())
    digest.update(json.dumps(sorted((str(key), _value(value)) for key, value in mesh.items()), default=repr).encode())
    if mesh.has_custom_normals:
        normals = np.empty(len(mesh.corner_normals) * 3, dtype=np.float32)
        mesh.corner_normals.foreach_get('vector', normals); digest.update(normals.tobytes())
    for property_name in ('loop_start', 'loop_total', 'use_smooth'):
        values = np.empty(len(mesh.polygons), dtype=np.int32)
        mesh.polygons.foreach_get(property_name, values); digest.update(values.tobytes())
    # Deform weights live in the shared mesh, but their names/index map is object data.
    for vertex in mesh.vertices:
        digest.update(repr([(weight.group, float(weight.weight)) for weight in vertex.groups]).encode())
    return digest.hexdigest()


def _value(value):
    if isinstance(value, bpy.types.ID):
        return ('ID', value.as_pointer())
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return list(value)
    except TypeError:
        return repr(value)


def _rna_values(value, skip=(), depth=0):
    if depth > 8:
        return None
    result = []
    for prop in value.bl_rna.properties:
        if prop.identifier in {'rna_type', 'id_data', *skip}:
            continue
        try:
            current = getattr(value, prop.identifier)
            if prop.type == 'COLLECTION':
                current = [_rna_values(item, depth=depth + 1) for item in current]
                if any(item is None for item in current):
                    return None
            elif prop.type == 'POINTER' and current is not None and not isinstance(current, bpy.types.ID):
                current = _rna_values(current, depth=depth + 1)
                if current is None:
                    return None
            else:
                current = _value(current)
            result.append((prop.identifier, current))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return None
    return result


def material_signature(material):
    """Conservative exact graph/state match; animated materials stay independent."""
    if material is None or material.library or material.animation_data:
        return None
    tree = material.node_tree
    if tree and tree.animation_data:
        return None
    record = [_rna_values(material, {'name', 'name_full', 'node_tree', 'preview', 'users', 'is_updated',
                                    'is_updated_data', 'is_updated_viewport', 'original', 'session_uid'}), []]
    if record[0] is None:
        return None
    if tree:
        for node in sorted(tree.nodes, key=lambda n: n.name):
            properties = _rna_values(node, {'inputs', 'outputs', 'internal_links', 'parent'})
            if properties is None:
                return None
            record[1].append((node.name, node.bl_idname, properties,
                              [(socket.identifier, _value(socket.default_value)) for socket in node.inputs if hasattr(socket, 'default_value')],
                              [(socket.identifier, _value(socket.default_value)) for socket in node.outputs if hasattr(socket, 'default_value')],
                              sorted((str(key), _value(value)) for key, value in node.items())))
        record.append(sorted((link.from_node.name, link.from_socket.identifier, link.to_node.name,
                              link.to_socket.identifier, link.is_muted) for link in tree.links))
    return hashlib.sha256(json.dumps(record, sort_keys=True, default=repr).encode()).hexdigest()


def _share_data(objects=None, *, scene=None, roles):
    """Link exact default geometry; preserve each object's materials, rig and pose.

    Objects/armatures remain distinct. Morph-bearing geometry is excluded because
    Blender stores shape-key values and drivers on the shared mesh datablock.
    """
    scene = scene or bpy.context.scene
    requested = set(objects) if objects is not None else None
    candidates = [obj for obj in scene.objects if _eligible(obj, roles)]
    ordered = sorted(candidates, key=lambda obj: (requested is not None and obj in requested, obj.name))
    registry, material_registry, mesh_tokens, material_tokens = {}, {}, {}, {}
    result = {'meshes_linked': 0, 'materials_linked': 0, 'eligible': len(candidates), 'morphs_skipped': 0}
    for obj in scene.objects:
        if obj.type == 'MESH' and obj.get('arc_assembly_role') in roles and obj.data.shape_keys:
            result['morphs_skipped'] += 1
    for obj in ordered:
        pointer = obj.data.as_pointer()
        mapping = tuple((g.index, g.name) for g in obj.vertex_groups)
        cache_key = (pointer, mapping)
        token = mesh_tokens.setdefault(cache_key, None)
        if token is None:
            token = mesh_signature(obj); mesh_tokens[cache_key] = token
        if token is None:
            continue
        materials = [slot.material for slot in obj.material_slots]
        shared_materials = []
        for material in materials:
            if material is None:
                shared_materials.append(None); continue
            key = material.as_pointer()
            if key not in material_tokens:
                material_tokens[key] = material_signature(material)
            signature = material_tokens[key]
            canonical = material_registry.setdefault(signature, material) if signature else material
            shared_materials.append(canonical)
            if canonical != material and (requested is None or obj in requested):
                result['materials_linked'] += 1
        shared = registry.setdefault(token, obj.data)
        if requested is not None and obj not in requested:
            continue
        # Object slots isolate frame colourways even after the geometry is linked.
        if obj.data != shared:
            previous = obj.data
            obj.data = shared
            result['meshes_linked'] += 1
            if previous.users == 0 and not previous.use_fake_user:
                bpy.data.meshes.remove(previous)
        for slot, material in zip(obj.material_slots, shared_materials):
            slot.link = 'OBJECT'
            slot.material = material
        obj['arc_shared_backpack_geometry'] = 'exact-v1'
    return result


def share_backpack_data(objects=None, *, scene=None):
    """Share exact default frame/strap meshes with independent object materials."""
    return _share_data(objects, scene=scene, roles=_ROLES)


def share_charm_connector_data(objects=None, *, scene=None):
    """Share exact authored karabiners; retain independent socket poses/materials."""
    return _share_data(objects, scene=scene, roles={'CharmConnector'})


def assign_instance_materials(obj, materials):
    """Update cached materials on one shared default rack/harness/connector instance.

    Existing same-count slots remain shared geometry with OBJECT overrides.
    A different slot layout gets its own mesh before changing DATA slot count.
    """
    if (obj is None or obj.type != 'MESH' or not obj.get('arc_shared_backpack_geometry')
            or obj.get('arc_assembly_role') not in _SOURCES or not materials):
        return False
    values = list(materials)
    if len(values) != len(obj.material_slots):
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        obj.data.materials.clear()
        for _material in values:
            obj.data.materials.append(None)
    for slot, material in zip(obj.material_slots, values):
        slot.link = 'OBJECT'
        slot.material = material
    return True
