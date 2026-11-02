"""Authored prop effects from a Blueprint export: lights, beam cones, looping animation tracks.

Reads the BP dump that ``prop_assemble`` already uses and builds, under the assembled
prop root (same pass-through Unreal-centimetre frame as the meshes):

* ``SpotLightComponent`` / ``PointLightComponent`` / ``RectLightComponent`` -> Blender lights
  (UE light units converted to watts; see ``ue_light_watts``)
* lights with ``VolumetricScatteringIntensity`` > 0 and a cone -> optional volume beam cone
* ``SimpleAnimationSequenceComponent`` tracks in ``Loop`` mode -> cyclic keyframes from the
  ``CurveTranslateRotate`` asset (translate XYZ = FloatCurves[0..2], rotate roll/pitch/yaw = [3..5])
* ``NiagaraComponent`` -> marker empty carrying the system path (bake with the Niagara player)

Static data only. Values the BP leaves to runtime (e.g. a near-black authored LightColor on
tagged indicator lights) are flagged on the object instead of guessed silently.
"""
from __future__ import annotations

import json
import math
import os

try:
    from .prop_assemble import _rot, _scale, _ue_entries, _vec, unreal_to_blender_xyz_radians
except ImportError:  # tests import modules top-level
    from prop_assemble import _rot, _scale, _ue_entries, _vec, unreal_to_blender_xyz_radians

LIGHT_TYPES = {'SpotLightComponent': 'SPOT', 'PointLightComponent': 'POINT', 'RectLightComponent': 'AREA'}
NIAGARA_TYPES = {'NiagaraComponent'}
FX_TAG = 'arc_prop_fx'
# Blender spot/point irradiance at 1 m per watt (measured, N-O5 §15): e1 = 1/(4*pi).
_E1 = 1.0 / (4.0 * math.pi)
# Non-inverse-square UE lights have no 1/d^2; match Blender's inverse-square falloff at this
# fraction of AttenuationRadius (approximation, flagged on the light).
_NON_INV_SQ_MATCH = 0.3
# Volume beam density per unit VolumetricScatteringIntensity (1/m). Approximation: UE's beam
# visibility depends on the level's height fog, which a prop export does not carry.
_BEAM_DENSITY = 0.12
# Exported PSK vertices are Y-negated relative to UE (CUE4Parse writes -Y), so BP component
# transforms are mirrored to sit on the mesh: loc (x,-y,z); rotator -> (roll, -pitch, -yaw).
# Measured on SM_ExtractionElevator_01_A: warning lights land 0.07-0.46 m from the body vs 0.45-1.77 m unmirrored.
MIRROR_Y = True


def ue_loc(v):
    return (v[0], -v[1], v[2]) if MIRROR_Y else tuple(v)


def ue_euler(pitch, yaw, roll):
    if MIRROR_Y:
        return (math.radians(roll), math.radians(-pitch), math.radians(-yaw))
    return unreal_to_blender_xyz_radians(pitch, yaw, roll)


def _ref_name(ref) -> str:
    if not isinstance(ref, dict):
        return ''
    on = str(ref.get('ObjectName') or '')
    if ':' in on:
        on = on.split(':', 1)[1]
    return on.strip("'").split("'")[-1] if "'" in on else on


def _export_index(ref) -> int | None:
    try:
        return int(str((ref or {}).get('ObjectPath', '')).rsplit('.', 1)[1])
    except (IndexError, ValueError):
        return None


def _color(props, key='LightColor'):
    c = props.get(key)
    if not isinstance(c, dict):
        return (1.0, 1.0, 1.0)
    # FColor bytes are sRGB; UE converts with FLinearColor(FColor) (sRGB curve).
    def lin(b):
        v = float(b) / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return (lin(c.get('R', 255)), lin(c.get('G', 255)), lin(c.get('B', 255)))


def ue_light_watts(props: dict, kind: str) -> float:
    """Blender watts for a UE local light (LocalLightComponent / SpotLightComponent unit rules)."""
    intensity = float(props.get('Intensity', 5000.0 if kind != 'AREA' else 1000.0))
    if not props.get('bUseInverseSquaredFalloff', True):
        # UE uses Intensity as-is here (no unit conversion, no x16, no cm^2 scale) with radial falloff
        # (1 - (d/R)^2)^exp and no 1/d^2. Match Blender's inverse-square at d = 0.3 R:
        # E(d) = I * falloff(d), watts = E(d) * d^2 / e1.
        d = _NON_INV_SQ_MATCH * float(props.get('AttenuationRadius', 1000.0)) * 0.01
        falloff = (1.0 - _NON_INV_SQ_MATCH ** 2) ** float(props.get('LightFalloffExponent', 8.0))
        return intensity * falloff * d * d / _E1
    units = str(props.get('IntensityUnits') or 'ELightUnits::Unitless').split('::')[-1]
    outer = math.radians(float(props.get('OuterConeAngle', 44.0))) if kind == 'SPOT' else math.pi
    if units == 'Candelas':
        brightness = intensity * 1e4
    elif units == 'Lumens':
        solid = 2.0 * math.pi * (1.0 - math.cos(outer)) if kind == 'SPOT' else 4.0 * math.pi
        brightness = intensity * 1e4 / solid
    elif units == 'EV':
        brightness = (2.0 ** intensity) * 1.25 * 1e4  # EV100 -> nits ~ candela/m^2 (rare on props)
    else:
        brightness = intensity * 16.0
    # brightness is in UE "candela x 1e4" scale (cm^2); Blender power gives irradiance P*e1 at 1 m.
    return brightness / 1e4 / _E1


def parse_bp_fx(bp_json_path: str) -> dict:
    """Light / Niagara components with SCS parent var, plus looping animation tracks."""
    out = {'lights': [], 'niagara': [], 'tracks': []}
    if not bp_json_path or not os.path.isfile(bp_json_path):
        return out
    with open(bp_json_path, 'r', encoding='utf-8') as fh:
        entries = _ue_entries(json.load(fh))
    by_index = dict(enumerate(entries))
    templates = {str(e.get('Name') or ''): e for e in entries}
    # SCS tree: child export index -> parent var
    node_var = {}
    tree_parent = {}
    for i, e in by_index.items():
        if e.get('Type') == 'SCS_Node':
            node_var[i] = str((e.get('Properties') or {}).get('InternalVariableName') or '')
    for i, e in by_index.items():
        if e.get('Type') != 'SCS_Node':
            continue
        for c in (e.get('Properties') or {}).get('ChildNodes') or []:
            ci = _export_index(c)
            if ci is not None:
                tree_parent[ci] = node_var.get(i, '')
    for i, e in by_index.items():
        if e.get('Type') != 'SCS_Node':
            continue
        props = e.get('Properties') or {}
        tmpl = templates.get(_ref_name(props.get('ComponentTemplate'))) or {}
        ctype = tmpl.get('Type')
        if ctype not in LIGHT_TYPES and ctype not in NIAGARA_TYPES:
            continue
        tp = tmpl.get('Properties') or {}
        row = {
            'var': node_var[i],
            'type': ctype,
            'parent': str(props.get('ParentComponentOrVariableName') or tree_parent.get(i) or ''),
            'attach': str(props.get('AttachToName') or ''),
            'location': _vec(tp, 'RelativeLocation'),
            'rotation': _rot(tp),
            'scale': _scale(tp),
            'props': tp,
        }
        (out['lights'] if ctype in LIGHT_TYPES else out['niagara']).append(row)
    for e in entries:
        if e.get('Type') != 'SimpleAnimationSequenceComponent':
            continue
        props = e.get('Properties') or {}
        if not str(props.get('AnimationPlaybackMode') or '').endswith('Loop'):
            continue  # event-driven (doors, barriers): not an always-on effect
        for t in props.get('AnimationTracks') or []:
            comp = (t.get('ComponentPicker') or {}).get('SanitizedComponentName') or ''
            curve = (t.get('CurveTranslateRotate') or {}).get('ObjectPath') or ''
            if comp and curve:
                out['tracks'].append({'component': comp, 'curve': curve, 'owner': str(e.get('Name') or ''),
                                      'mode': str(t.get('AnimationTrackApplicationMode') or '').split('::')[-1],
                                      'play_rate': float(props.get('PlayRate', 1.0) or 1.0)})
    return out


def resolve_game_path(game_path: str, pioneer_root: str, bp_json_path: str = '') -> str:
    """'/Game/Pioneer/X/Y.0' -> '<pioneer_root>/X/Y.json' (falls back to the BP's folder)."""
    rel = game_path.split('.', 1)[0]
    for prefix in ('/Game/Pioneer/', '/Game/'):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    cands = []
    if pioneer_root:
        cands.append(os.path.join(pioneer_root, *rel.split('/')) + '.json')
    if bp_json_path:
        cands.append(os.path.join(os.path.dirname(bp_json_path), rel.split('/')[-1] + '.json'))
    return next((c for c in cands if os.path.isfile(c)), '')


def load_translate_rotate(path: str) -> dict[int, list[tuple[float, float, str]]]:
    """FloatCurves index -> [(time, value, interp)]."""
    curves = {}
    if not path:
        return curves
    with open(path, 'r', encoding='utf-8') as fh:
        for e in _ue_entries(json.load(fh)):
            for k, v in (e.get('Properties') or {}).items():
                # CUE4Parse writes static-array element 0 under the bare name
                if (k == 'FloatCurves' or k.startswith('FloatCurves[')) and isinstance(v, dict):
                    idx = 0 if k == 'FloatCurves' else int(k[len('FloatCurves['):-1])
                    curves[idx] = [(float(x.get('Time', 0.0)), float(x.get('Value', 0.0)),
                                    str(x.get('InterpMode') or 'RCIM_Linear')) for x in v.get('Keys') or []]
    return curves


def _new_object(name, data, collection, parent):
    import bpy
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    obj.parent = parent
    if parent is not None:
        obj.matrix_parent_inverse.identity()
    obj[FX_TAG] = 1
    return obj


def _beam_cone(name, length_cm, half_angle, density, color, collection, parent):
    """Cone along local +X (UE forward), apex at origin, in parent's cm space."""
    import bmesh
    import bpy
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    r = length_cm * math.tan(half_angle)
    segs = 24
    apex = bm.verts.new((0.0, 0.0, 0.0))
    ring = [bm.verts.new((length_cm, r * math.cos(2 * math.pi * i / segs), r * math.sin(2 * math.pi * i / segs)))
            for i in range(segs)]
    for i in range(segs):
        bm.faces.new((apex, ring[(i + 1) % segs], ring[i]))
    bm.faces.new(ring)
    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    mat = bpy.data.materials.new(name + '_Beam')
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    outn = nt.nodes.new('ShaderNodeOutputMaterial')
    vol = nt.nodes.new('ShaderNodeVolumeScatter')
    vol.inputs['Color'].default_value = (*color, 1.0)
    vol.inputs['Density'].default_value = density
    nt.links.new(vol.outputs['Volume'], outn.inputs['Volume'])
    me.materials.append(mat)
    obj = _new_object(name, me, collection, parent)
    obj.display_type = 'WIRE'
    obj.visible_shadow = False
    return obj


def _keyframe_track(obj, curves, fps, play_rate):
    """AdditiveLocal track on a dedicated child empty: rotation_euler / location keyframes, cyclic."""
    import bpy
    paths = []
    span = 0.0
    for idx, keys in sorted(curves.items()):
        if not keys or idx > 5:
            continue
        if idx < 3:
            prop, axis, sign = 'location', idx, (-1.0 if idx == 1 and MIRROR_Y else 1.0)
        else:
            # roll/pitch/yaw, same mapping as ue_euler
            prop, axis = 'rotation_euler', idx - 3
            sign = (1.0, -1.0, -1.0)[axis] if MIRROR_Y else (-1.0, -1.0, 1.0)[axis]
        for t, v, interp in keys:
            val = sign * (math.radians(v) if prop == 'rotation_euler' else v)
            getattr(obj, prop)[axis] = val
            obj.keyframe_insert(prop, index=axis, frame=1.0 + t * fps / play_rate)
            span = max(span, t)
        paths.append((prop, axis, [k[2] for k in keys]))
    act = obj.animation_data.action if obj.animation_data else None
    if act is None:
        return 0.0
    for fc in _action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'
        fc.modifiers.new('CYCLES')
    return span / play_rate


def _action_fcurves(act):
    fcs = getattr(act, 'fcurves', None)
    if fcs is not None:
        return list(fcs)
    out = []  # Blender 4.4+ layered actions
    for layer in getattr(act, 'layers', []):
        for strip in layer.strips:
            for bag in getattr(strip, 'channelbags', []):
                out.extend(bag.fcurves)
    return out


def build_prop_fx(root_obj, bp_json_path: str, *, pioneer_root: str = '', activated: bool = True,
                  beams: bool = True, collection=None) -> dict:
    """Create authored lights / beams / looping tracks / Niagara markers under *root_obj*.

    ``activated`` shows runtime-toggled lights (``bVisible`` false in the BP, e.g. elevator warning
    lights tagged ``ExtractionActivatedIndicator``) and normalises a near-black authored colour to its
    hue, since the BP export carries no runtime colour. Both cases are flagged on the light object.
    """
    import bpy
    from mathutils import Euler, Vector

    stats = {'lights': 0, 'beams': 0, 'tracks': 0, 'niagara': 0, 'flags': [], 'error': ''}
    fx = parse_bp_fx(bp_json_path)
    if collection is None:
        collection = root_obj.users_collection[0] if root_obj.users_collection else bpy.context.scene.collection
    clear_prop_fx(root_obj)
    by_var = {str(o.get('arc_prop_attach_var') or ''): o for o in bpy.data.objects
              if o.get('arc_prop_attach_var') and _is_descendant(o, root_obj)}
    fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
    track_by_comp = {t['component']: t for t in fx['tracks']}
    curve_cache = {}

    def pose_empty(row):
        parent = by_var.get(row['parent']) or root_obj
        emp = _new_object(f"{root_obj.name}.{row['var']}", None, collection, parent)
        emp.empty_display_type = 'ARROWS'
        emp.empty_display_size = 30.0
        emp.rotation_mode = 'XYZ'
        emp.location = Vector(ue_loc(row['location']))
        emp.rotation_euler = Euler(ue_euler(*row['rotation']), 'XYZ')
        emp.scale = Vector(row['scale'])
        emp['arc_prop_fx_var'] = row['var']
        emp['arc_prop_fx_parent'] = row['parent'] if row['parent'] in by_var else 'root'
        track = track_by_comp.get(row['var'])
        if track is None:
            return emp
        path = track['curve']
        if path not in curve_cache:
            curve_cache[path] = load_translate_rotate(resolve_game_path(path, pioneer_root, bp_json_path))
        curves = curve_cache[path]
        if not curves:
            stats['flags'].append(f"{row['var']}: curve not exported ({path.split('/')[-1]})")
            return emp
        anim = _new_object(emp.name + '.Anim', None, collection, emp)
        anim.empty_display_size = 20.0
        anim['arc_prop_fx_track'] = path
        anim['arc_prop_fx_track_mode'] = track['mode']
        period = _keyframe_track(anim, curves, fps, track['play_rate'])
        anim['arc_prop_fx_period_s'] = period
        stats['tracks'] += 1
        return anim

    for row in fx['lights']:
        p = row['props']
        kind = LIGHT_TYPES[row['type']]
        holder = pose_empty(row)
        ld = bpy.data.lights.new(f"{root_obj.name}.{row['var']}", kind)
        color = _color(p)
        flags = []
        if max(color) < 0.01:
            flags.append('authored LightColor near black (runtime-set); hue kept, normalised')
            m = max(color) or 1.0
            color = tuple(c / m for c in color) if max(color) > 0 else (1.0, 1.0, 1.0)
        ld.color = color
        ld.energy = ue_light_watts(p, kind)
        if kind == 'SPOT':
            outer = math.radians(float(p.get('OuterConeAngle', 44.0)))
            inner = math.radians(float(p.get('InnerConeAngle', 0.0)))
            ld.spot_size = min(math.pi, 2.0 * outer)
            ld.spot_blend = max(0.0, min(1.0, (outer - inner) / outer)) if outer > 0 else 0.0
        if kind == 'AREA':
            ld.shape = 'RECTANGLE'
            # area size scales with object scale (root carries the 0.01 UE unit scale)
            bpy.context.view_layer.update()
            ws = max(1e-9, abs(holder.matrix_world.to_scale().x))
            ld.size = float(p.get('SourceWidth', 64.0)) * 0.01 / ws
            ld.size_y = float(p.get('SourceHeight', 64.0)) * 0.01 / ws
        else:
            ld.shadow_soft_size = float(p.get('SourceRadius', 0.0)) * 0.01
        ld.use_shadow = bool(p.get('CastShadows', True))
        if hasattr(ld, 'use_custom_distance'):
            ld.use_custom_distance = True
            ld.cutoff_distance = float(p.get('AttenuationRadius', 1000.0)) * 0.01
        if not p.get('bUseInverseSquaredFalloff', True):
            flags.append(f"non-inverse-square falloff (exp {p.get('LightFalloffExponent', 8.0)}) matched at "
                         f"{_NON_INV_SQ_MATCH:g} x radius")
        if hasattr(ld, 'volume_factor'):
            ld.volume_factor = float(p.get('VolumetricScatteringIntensity', 1.0))
        lo = _new_object(ld.name + '.Light', ld, collection, holder)
        # Blender lights aim down local -Z; UE components aim down +X.
        lo.rotation_euler = Euler((0.0, -math.pi / 2.0, 0.0), 'XYZ')
        lo['arc_prop_fx_units'] = str(p.get('IntensityUnits') or 'ELightUnits::Unitless')
        lo['arc_prop_fx_intensity'] = float(p.get('Intensity', 0.0))
        tags = p.get('ComponentTags') or []
        if tags:
            lo['arc_prop_fx_tags'] = ','.join(map(str, tags))
        hidden = p.get('bVisible') is False
        if hidden:
            lo['arc_prop_fx_runtime_toggled'] = 1
            flags.append('bVisible false in BP (runtime-activated)')
        off = hidden and not activated
        lo.hide_render = lo.hide_viewport = off
        vsi = float(p.get('VolumetricScatteringIntensity', 1.0))
        if beams and kind == 'SPOT' and 'VolumetricScatteringIntensity' in p and vsi > 0:
            cone = _beam_cone(f"{ld.name}.Beam", float(p.get('AttenuationRadius', 1000.0)),
                              math.radians(float(p.get('OuterConeAngle', 44.0))), _BEAM_DENSITY * vsi,
                              color, collection, holder)
            cone.hide_render = cone.hide_viewport = off
            cone['arc_prop_fx_beam'] = 1
            stats['beams'] += 1
        if flags:
            lo['arc_prop_fx_flags'] = '; '.join(flags)
            stats['flags'].append(f"{row['var']}: " + '; '.join(flags))
        stats['lights'] += 1

    for row in fx['niagara']:
        emp = pose_empty(row)
        asset = (row['props'].get('Asset') or {}).get('ObjectPath', '')
        emp['arc_ns_asset'] = asset
        emp['arc_ns_json'] = resolve_game_path(asset, pioneer_root, bp_json_path) if asset else ''
        emp['arc_ns_auto_activate'] = int(row['props'].get('bAutoActivate', True) is not False)
        stats['niagara'] += 1
    root_obj['arc_prop_fx'] = json.dumps({k: stats[k] for k in ('lights', 'beams', 'tracks', 'niagara')})
    return stats


def _is_descendant(obj, root) -> bool:
    p = obj.parent
    while p is not None:
        if p == root:
            return True
        p = p.parent
    return False


def clear_prop_fx(root_obj) -> int:
    """Remove FX objects previously built under *root_obj* (idempotent re-run)."""
    import bpy
    doomed = [o for o in bpy.data.objects if o.get(FX_TAG) and _is_descendant(o, root_obj)]
    for o in doomed:
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Light):
                bpy.data.lights.remove(data)
            elif isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
    return len(doomed)
