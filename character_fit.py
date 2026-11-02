"""Authored body/outfit gender morphs, scoped to each imported character."""
from __future__ import annotations

import os
import bpy
from bpy.app.handlers import persistent
from . import import_layout, utils

_CHECKED = {}
_DRIVER_VAR = 'arc_body_gender'
_DRIVER_EXPRESSIONS = {_DRIVER_VAR, '1.0 - ' + _DRIVER_VAR}
_SIGNATURES = {}
_WATCHED_IDS = {}
_SYNCING = False


def is_body(obj):
    if obj.type != 'MESH':
        return False
    if str(obj.get('arc_model_type') or '').casefold() == 'body':
        return True
    path = str(obj.get('arc_source_mesh') or obj.get('arc_psk_path') or '').replace('\\', '/').casefold()
    return '/shared/bodies/' in path or import_layout.infer_role(obj) == 'Body'


def _gender_keys(obj):
    keys = getattr(obj.data, 'shape_keys', None)
    return {k.name.casefold(): k for k in keys.key_blocks if k.name.casefold() in {'male', 'female'}} if keys else {}


def _armatures(obj):
    result = set()
    parent = obj.parent
    while parent:
        if parent.type == 'ARMATURE':
            result.add(parent)
        parent = parent.parent
    for mod in obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object:
            result.add(mod.object)
    return result


def body_for_object(obj, scene=None):
    """Prefer the shared rig, then a unique body on the same character pad."""
    scene = scene or bpy.context.scene
    bodies = [o for o in scene.objects if is_body(o)]
    rigs = _armatures(obj)
    linked = [body for body in bodies if rigs and rigs & _armatures(body)]
    if len(linked) == 1:
        return linked[0]
    if linked:
        return None
    slot = import_layout.slot_of_object(obj)
    if slot is not None:
        candidates = [body for body in bodies if import_layout.slot_of_object(body) == slot]
        return candidates[0] if len(candidates) == 1 else None
    # Legacy single-character scenes may predate slot stamps. Never choose an
    # arbitrary body when multiple characters share the origin.
    return bodies[0] if len(bodies) == 1 else None


def is_head(obj):
    if obj.type != 'MESH' or obj.get('arc_reference_plane'):
        return False
    path = str(obj.get('arc_source_mesh') or obj.get('arc_psk_path') or '').replace('\\', '/').casefold()
    return '/characters/heads/' in path or import_layout.infer_role(obj) == 'Head'


def head_for_object(obj, scene=None):
    """A unique shared-rig or same-pad head; never borrow a different character."""
    scene = scene or bpy.context.scene
    heads = [o for o in scene.objects if is_head(o)]
    rigs = _armatures(obj)
    linked = [head for head in heads if rigs and rigs & _armatures(head)]
    if linked:
        return linked[0] if len(linked) == 1 else None
    slot = import_layout.slot_of_object(obj)
    if slot is not None:
        same = [head for head in heads if import_layout.slot_of_object(head) == slot]
        return same[0] if len(same) == 1 else None
    return heads[0] if len(heads) == 1 else None


def character_objects(body, scene=None):
    scene = scene or bpy.context.scene
    return [obj for obj in scene.objects if obj.type == 'MESH' and (obj == body or (not is_body(obj) and body_for_object(obj, scene) == body))]


def _ensure_authored_keys(obj):
    source = str(obj.get('arc_source_mesh') or obj.get('arc_psk_path') or '')
    if not source.casefold().endswith('.uemodel') or not os.path.isfile(source):
        return []
    # A refreshed extraction can switch between equivalent mount aliases.
    # Recover from the newest exact package, rather than an obsolete alias.
    normalized = source.replace('\\', '/')
    for marker in ('/pioneergame/content/', '/remappedplugins/content/'):
        index = normalized.casefold().find(marker)
        if index >= 0:
            from . import pak_extract
            root = normalized[:index]
            latest = pak_extract._expected_uemodel(root, normalized[index + 1:-len('.uemodel')])
            if latest:
                source = latest
            break
    stat = os.stat(source)
    token = (obj.as_pointer(), source, stat.st_mtime_ns, stat.st_size, obj.data.as_pointer())
    if token in _CHECKED:
        return []
    from . import uemodel_import
    try:
        added = uemodel_import.ensure_uemodel_shape_keys(obj, source, allow_coincident_welds=is_body(obj))
    except uemodel_import.UeModelError as exc:
        _CHECKED[token] = False
        utils.get_logger().warning('Gender morphs unavailable for %s: %s', obj.name, exc)
        return []
    _CHECKED[token] = True
    if added:
        obj['arc_gender_morph_source'] = source
    return added


def apply_body_gender(obj, variant='NONE'):
    """Apply the selected body fit independently of outfit inheritance."""
    if not is_body(obj):
        return
    added = _ensure_authored_keys(obj)
    variant = str(variant or 'NONE')
    if variant == 'NONE':
        variant = str(obj.get('arc_body_variant') or 'NONE')
    gender = variant.split('_', 1)[0].casefold()
    keys = _gender_keys(obj)
    if gender not in {'female', 'male'}:
        female = float(keys['female'].value) if 'female' in keys else 0.0
    elif obj.get('arc_fit_variant_applied') != variant or added:
        if obj.data.users > 1:
            obj.data = obj.data.copy()
            keys = _gender_keys(obj)
        female = float(gender == 'female')
        for name, key in keys.items():
            key.value = female if name == 'female' else 1.0 - female
        obj['arc_body_variant'] = variant
        obj['arc_fit_variant_applied'] = variant
    else:
        female = float(keys['female'].value) if 'female' in keys else float(gender == 'female')
    obj['arc_fit_female'] = female


def _managed_driver(key):
    data = key.id_data
    animation = data.animation_data
    if not animation:
        return None
    path = key.path_from_id('value')
    for curve in animation.drivers:
        if curve.data_path != path:
            continue
        driver = curve.driver
        if driver.expression in _DRIVER_EXPRESSIONS and len(driver.variables) == 1 and driver.variables[0].name == _DRIVER_VAR:
            return curve
    return None


def _has_driver(key):
    data = key.id_data
    return bool(data.animation_data and any(c.data_path == key.path_from_id('value') for c in data.animation_data.drivers))


def release_object(obj):
    """Release only importer-owned drivers, retaining the current visible fit."""
    if obj.type != 'MESH':
        return
    for name in list(obj.get('arc_inherited_shape_keys') or []):
        key = obj.data.shape_keys.key_blocks.get(name) if obj.data.shape_keys else None
        if key is not None and _managed_driver(key):
            value = key.value
            key.driver_remove('value')
            key.value = value
    for prop in ('arc_inherited_shape_keys', 'arc_inherit_body', 'arc_inherit_head'):
        if prop in obj:
            del obj[prop]


def release_scene(scene=None):
    scene = scene or bpy.context.scene
    for obj in scene.objects:
        release_object(obj)


def _inherit(obj, body, *, source_role="Body"):
    _ensure_authored_keys(obj)
    keys = _gender_keys(obj)
    if not keys:
        return 0
    if obj.data.users > 1:
        obj.data = obj.data.copy()
        keys = _gender_keys(obj)
    body_keys = _gender_keys(body)
    managed = []
    for gender, key in keys.items():
        # User-authored animation/drivers are never overwritten.
        if _has_driver(key) and not _managed_driver(key):
            continue
        source = body_keys.get(gender)
        complement = False
        if source is None and gender == 'male':
            source = body_keys.get('female')
            complement = source is not None
        curve = key.driver_add('value')
        driver = curve.driver
        driver.type = 'SCRIPTED'
        for variable in list(driver.variables):
            driver.variables.remove(variable)
        variable = driver.variables.new()
        variable.name = _DRIVER_VAR
        variable.type = 'SINGLE_PROP'
        target = variable.targets[0]
        if source is not None:
            target.id_type = 'KEY'
            target.id = body.data.shape_keys
            target.data_path = source.path_from_id('value')
        else:
            target.id_type = 'OBJECT'
            target.id = body
            target.data_path = '["arc_fit_female"]'
            complement = gender == 'male'
        driver.expression = '1.0 - ' + _DRIVER_VAR if complement else _DRIVER_VAR
        managed.append(key.name)
    if managed:
        obj['arc_inherited_shape_keys'] = managed
        stamp = 'arc_inherit_head' if source_role == 'Head' else 'arc_inherit_body'
        other = 'arc_inherit_body' if source_role == 'Head' else 'arc_inherit_head'
        obj[stamp] = body.name
        if other in obj:
            del obj[other]
    return len(managed)


def sync_scene(scene=None):
    """Reconcile authored fits; runtime head-specific cloth wrapping is separate."""
    scene = scene or bpy.context.scene
    bodies = [o for o in scene.objects if is_body(o)]
    heads = [o for o in scene.objects if is_head(o)]
    for body in bodies:
        apply_body_gender(body)
    result = {'bodies': len(bodies), 'meshes': 0, 'keys': 0,
              'head_meshes': 0, 'hair_meshes': 0, 'appearance_keys': 0,
              'missing_head': 0, 'no_authored_gender': 0,
              'head_specific_wrap': 'authored cage transfer where exact sidecars are available'}
    enabled = bool(getattr(scene, 'arc_inherit_shape_key', True))
    if enabled:
        # Heads follow their character's body first. Hair then reads the exact
        # head key values, including manual blends and user-authored drivers.
        for head in heads:
            body = body_for_object(head, scene)
            if body is None:
                if head.get('arc_inherit_body'):
                    release_object(head)
                continue
            count = _inherit(head, body)
            if count:
                result['head_meshes'] += 1
                result['appearance_keys'] += count
        for obj in scene.objects:
            if obj.type != 'MESH' or is_body(obj) or obj.get('arc_reference_plane'):
                continue
            role = import_layout.infer_role(obj)
            if role in {'Outfit', 'BackpackStrap'}:
                body = body_for_object(obj, scene)
                if body is None:
                    continue
                count = _inherit(obj, body)
                if count:
                    result['meshes'] += 1
                    result['keys'] += count
            elif role in {'Hair', 'FacialHair'}:
                head = head_for_object(obj, scene)
                if head is None:
                    result['missing_head'] += 1
                    if obj.get('arc_inherit_head'):
                        release_object(obj)
                    continue
                _ensure_authored_keys(head)
                _ensure_authored_keys(obj)
                if not _gender_keys(obj):
                    result['no_authored_gender'] += 1
                    obj['arc_head_fit_note'] = 'No authored gender targets; checking exact authored cloth cage'
                    continue
                if not _gender_keys(head):
                    obj['arc_head_fit_note'] = 'Matching head has no authored gender targets; no fit guessed'
                    continue
                count = _inherit(obj, head, source_role='Head')
                if count:
                    result['hair_meshes'] += 1
                    result['appearance_keys'] += count
                    obj['arc_head_fit_note'] = 'Authored head gender targets inherited'
    else:
        release_scene(scene)
    from . import body_mask, head_wrap
    bpy.context.view_layer.update()
    from . import uemodel_import
    result['normal_meshes'] = 0
    for obj in scene.objects:
        if obj.type == 'MESH' and obj.data.get('arc_native_normal_targets'):
            result['normal_meshes'] += int(uemodel_import.apply_uemodel_morph_normals(obj))
    result['head_wrap'] = head_wrap.sync_scene(scene, enabled=enabled)
    result['body_masks'] = body_mask.sync_body_masks(scene)
    _SIGNATURES[scene.as_pointer()] = _scene_signature(scene)
    return result


def _scene_signature(scene):
    result = []
    watched = set()
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        body = is_body(obj)
        if obj.get('arc_reference_plane') or (not body and not is_head(obj) and import_layout.infer_role(obj) not in {'Outfit', 'BackpackStrap', 'Hair', 'FacialHair'}):
            continue
        keys = ({key.name: key for key in obj.data.shape_keys.key_blocks if key != obj.data.shape_keys.reference_key} if is_head(obj) and obj.data.shape_keys else _gender_keys(obj) if body else {})
        values = tuple(sorted((name, round(float(key.value), 6)) for name, key in keys.items()))
        result.append((obj.as_pointer(), obj.name, obj.get('arc_import_slot'),
                       tuple(sorted(arm.as_pointer() for arm in _armatures(obj))),
                       str(obj.get('arc_body_variant') or ''), values,
                       bool(obj.hide_render), str(obj.get('arc_visual_part') or obj.get('arc_visual_part_path') or '')))
        # Blender's depsgraph callback can run many times per interaction. Keep
        # only IDs that contribute to this signature so unrelated scene updates
        # can be discarded without walking every object and shape key again.
        watched.add(obj.as_pointer())
        data = getattr(obj, 'data', None)
        if data is not None:
            watched.add(data.as_pointer())
            shape_keys = getattr(data, 'shape_keys', None)
            if shape_keys is not None:
                watched.add(shape_keys.as_pointer())
    _WATCHED_IDS[scene.as_pointer()] = watched
    return tuple(sorted(result))


def _deferred_sync():
    global _SYNCING
    _SYNCING = True
    try:
        for scene in bpy.data.scenes:
            sync_scene(scene)
    except Exception as exc:
        utils.get_logger().warning('Deferred character fit failed: %s', exc)
    finally:
        _SYNCING = False
    return None


def _schedule_sync():
    if not bpy.app.timers.is_registered(_deferred_sync):
        bpy.app.timers.register(_deferred_sync, first_interval=.1)


@persistent
def _load_fit(_unused):
    from . import head_wrap
    head_wrap.reset_cache()
    _CHECKED.clear()
    _SIGNATURES.clear()
    _WATCHED_IDS.clear()
    _schedule_sync()


@persistent
def _changed_fit(scene, depsgraph):
    if _SYNCING:
        return
    # Never compute _scene_signature() from depsgraph_update_post: that scans
    # every scene mesh, rig, and shape key on each graph evaluation. On large
    # imports it made an otherwise idle Blender session burn CPU continuously.
    # The signature is recomputed once, after a relevant update is debounced.
    watched = _WATCHED_IDS.get(scene.as_pointer(), ())
    for update in getattr(depsgraph, 'updates', ()):
        updated_id = getattr(update, 'id', None)
        if updated_id is None:
            continue
        try:
            pointer = updated_id.as_pointer()
        except (AttributeError, ReferenceError):
            continue
        if pointer in watched:
            # Animated transforms do not affect fit eligibility or shape-key
            # values and can generate depsgraph events every frame.
            if (getattr(update, 'is_updated_transform', False)
                    and not getattr(update, 'is_updated_geometry', False)):
                continue
            _schedule_sync()
            return
        # Catch newly added importer-tagged mesh objects without scanning the
        # scene. Normal transform/deform events on unrelated objects are cheap
        # to reject here and do not schedule a full fit reconciliation.
        if (isinstance(updated_id, bpy.types.Object)
                and updated_id.type == 'MESH'
                and (updated_id.get('arc_model_type') == 'Body'
                     or updated_id.get('arc_source_mesh')
                     or updated_id.get('arc_psk_path'))):
            _schedule_sync()
            return


def register():
    # Reconciliation is called directly by explicit import/property actions.
    # Keep no idle depsgraph watcher or load-time scene scan.
    unregister()


def unregister():
    from . import head_wrap
    head_wrap.reset_cache()
    if bpy.app.timers.is_registered(_deferred_sync):
        bpy.app.timers.unregister(_deferred_sync)
    for collection in (bpy.app.handlers.load_post, bpy.app.handlers.depsgraph_update_post):
        for handler in list(collection):
            if handler.__module__ == __name__:
                collection.remove(handler)
    _CHECKED.clear()
    _SIGNATURES.clear()
    _WATCHED_IDS.clear()
