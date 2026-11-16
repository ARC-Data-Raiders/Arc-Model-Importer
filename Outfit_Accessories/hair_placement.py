"""Authored head-local static hair placement, without modifying mesh geometry."""
from __future__ import annotations
import os
import numpy as np
import bpy
from mathutils import Matrix, Quaternion, Vector
from ..Shared import uemodel_import


def _source(obj):
    path = str(obj.get('arc_source_mesh') or obj.get('arc_psk_path') or '')
    native = os.path.splitext(path)[0] + '.uemodel'
    return native if os.path.isfile(native) else ''


def _frame(obj, path):
    """Fit the source-to-current local frame and reject edited/nonmatching geometry."""
    lod = uemodel_import.parse_uemodel(path)[2][0]
    source = np.asarray(lod.vertices, dtype=np.float64).reshape(-1, 3)
    data = obj.data.shape_keys.reference_key.data if obj.data.shape_keys else obj.data.vertices
    if len(source) != len(data):
        raise ValueError('Source/current vertex counts differ; cannot establish attachment frame')
    current = np.empty((len(data), 3), dtype=np.float64)
    data.foreach_get('co', current.ravel())
    augmented = np.column_stack((source, np.ones(len(source))))
    fitted, _residuals, rank, _singular = np.linalg.lstsq(augmented, current, rcond=None)
    if rank != 4 or np.max(np.linalg.norm(augmented @ fitted - current, axis=1)) > 0.00001:
        raise ValueError('Source/current geometry does not define an exact attachment frame')
    result = Matrix.Identity(4)
    for row in range(3):
        for col in range(4):
            result[row][col] = float(fitted[col, row])
    return result


def _head_anchor(path):
    matrices = {}
    for index, bone in enumerate(uemodel_import.parse_uemodel_bones(path)):
        local = Matrix.LocRotScale(Vector(bone.translation), Quaternion((bone.rotation[3], *bone.rotation[:3])), Vector(bone.scale))
        matrix = matrices[bone.parent] @ local if bone.parent in matrices else local
        matrices[index] = matrix
        if bone.name.casefold() == 'head':
            return matrix
    return None


def _character_component(obj):
    from ..Animations.animation_import import _psa_translation_scale

    armature = obj.find_armature()
    parent = obj.parent
    while armature is None and parent is not None:
        if parent.type == 'ARMATURE':
            armature = parent
            break
        parent = parent.parent
    if armature is None:
        return None
    return armature.matrix_world @ Matrix.Scale(_psa_translation_scale(armature) / 0.01, 4)


def place_hair_at_head(obj, scene=None):
    """Align authored component-space hair to its same-slot character frame.

    Origin recentering must not center an asymmetric hairstyle independently of
    the skull. Static styles still use the verified head-bone attachment path.
    """
    from . import import_layout, reference_planes
    scene = scene or bpy.context.scene
    source = _source(obj)
    norm = source.replace('\\', '/').casefold()
    if not source or ('/characters/hairs/' not in norm and '/characters/facialhairs/' not in norm):
        return False, 'Not an authored hairstyle'
    if not uemodel_import.parse_uemodel_bones(source):
        cards = [(p, p.matrix_world.copy()) for p in scene.objects
                 if p.get('arc_reference_plane') and int(p.get('arc_reference_layout_schema') or 0) >= 2
                 and p.parent == obj]
        result = place_static_hair(obj, scene)
        bpy.context.view_layer.update()
        for plane, world in cards:
            reference_planes._anchor_plane(plane, obj, organize=False, world_matrix=world)
        return result
    # Facial cages have their own authored correspondence/transform transfer.
    if '/characters/facialhairs/' in norm:
        return False, 'Facial hair retains its authored cage frame'
    frame = _frame(obj, source)
    slot = import_layout.slot_of_object(obj)
    candidates = [o for o in scene.objects if o.type == 'MESH' and o != obj
                  and import_layout.infer_role(o) in {'Head', 'Body'}
                  and import_layout.slot_of_object(o) == slot and _source(o)]
    candidates.sort(key=lambda o: 0 if import_layout.infer_role(o) == 'Head' else 1)
    component = _character_component(obj)
    reference = ''
    for candidate in candidates if component is None else ():
        try:
            component = candidate.matrix_world @ _frame(candidate, _source(candidate))
        except ValueError:
            continue
        reference = candidate.name
        break
    if component is None:
        if slot is None:
            return False, 'No verified character slot for component-space hair'
        x, y = import_layout.slot_world_xy(slot, scene)
        component = Matrix.Translation((x, y, 0.0))
        reference = 'character pad'
    cards = [(p, p.matrix_world.copy()) for p in scene.objects
             if p.get('arc_reference_plane') and int(p.get('arc_reference_layout_schema') or 0) >= 2
             and p.parent == obj]
    obj.matrix_world = component @ frame.inverted()
    obj['arc_hair_component_reference'] = reference
    bpy.context.view_layer.update()
    for plane, world in cards:
        reference_planes._anchor_plane(plane, obj, organize=False, world_matrix=world)
    return True, 'Skeletal hair aligned to authored character component frame'


def place_static_hair(obj, scene=None):
    """Attach static hair to its authored head rest bone; repeated calls are safe.

    Skeletal styles already include component-space head coordinates. Static
    styles such as Buzzcut are head-local, and need the rest-bone transform.
    A matching head/body supplies the component frame. Without one, another
    exported human head reference supplies the bone transform only, retaining
    the hair's own pad/component frame.
    """
    from . import appearance, import_layout
    scene = scene or bpy.context.scene
    source = _source(obj)
    if not source or uemodel_import.parse_uemodel_bones(source):
        return False, 'Skeletal hair retains authored bind coordinates'
    norm = source.replace('\\', '/').casefold()
    if '/characters/hairs/' not in norm and '/characters/facialhairs/' not in norm:
        return False, 'Not an authored hairstyle'
    frame = _frame(obj, source)
    character_component = _character_component(obj)
    component = character_component if character_component is not None else obj.matrix_world @ frame
    old = obj.get('arc_static_hair_anchor')
    if character_component is None and old and len(old) == 16:
        previous = Matrix([list(old)[i:i+4] for i in range(0, 16, 4)])
        component = component @ previous.inverted()
    slot = import_layout.slot_of_object(obj)
    candidates = [o for o in scene.objects if o.type == 'MESH' and o != obj and import_layout.infer_role(o) in {'Head', 'Body'} and _source(o)]
    same = [o for o in candidates if slot is not None and import_layout.slot_of_object(o) == slot]
    same.sort(key=lambda o: 0 if import_layout.infer_role(o) == 'Head' else 1)
    candidates = same + [o for o in candidates if o not in same]
    anchor = None
    reference = ''
    for candidate in candidates:
        path = _source(candidate)
        anchor = _head_anchor(path)
        if anchor is None:
            continue
        reference = path
        if candidate in same and character_component is None:
            try:
                component = candidate.matrix_world @ _frame(candidate, path)
            except ValueError:
                pass  # legacy coincident-weld body: retain the hair component frame
        break
    if anchor is None:
        paths = [appearance.list_body_mesh()] + [c.get('path') or '' for c in appearance.list_head_choices()]
        for path in paths:
            native = os.path.splitext(path)[0] + '.uemodel'
            if os.path.isfile(native):
                anchor = _head_anchor(native)
                if anchor is not None:
                    reference = native
                    break
    if anchor is None:
        # Pak-direct sessions have no body or head on disk until one is imported, which
        # left a standalone static style (Buzzcut) at the origin. Extract the shared body
        # skeleton once; only its head rest bone is read.
        try:
            native = appearance.locate_appearance_mesh('Characters/Shared/Bodies/SK_Body') or ''
        except Exception:
            native = ''
        native = os.path.splitext(native)[0] + '.uemodel' if native else ''
        if native and os.path.isfile(native):
            anchor = _head_anchor(native)
            if anchor is not None:
                reference = native
    if anchor is None:
        return False, 'No exported human head reference bone available for static hair'
    obj.matrix_world = component @ anchor @ frame.inverted()
    obj['arc_static_hair_anchor'] = [float(value) for row in anchor for value in row]
    obj['arc_static_hair_reference'] = reference
    obj['arc_attachment_bone'] = 'head'
    return True, 'Static hair placed at authored head reference bone'
