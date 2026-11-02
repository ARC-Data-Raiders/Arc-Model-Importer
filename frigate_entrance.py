"""Frigate orbital entrance (BP_Frigate_Orbital_MultiStageSpawnOrchestrator): stage path, FX segments, socket owner tracks.

Static data only (cooked JSON). Stage 0 moves the orchestrated actor (the round manager's EntryProxyMesh,
SM_Frigate_Merged) along a CurveTranslateRotate relative to StageZeroTransform (identity on the cooked CDO), i.e.
relative to the landing transform:

* FloatCurves[0..2]: translation X/Y/Z (cm); [3..5]: rotation about X/Y/Z = roll/pitch/yaw (degrees). CUE4Parse writes
  channel 0 under the bare name ``FloatCurves``. The translation ends at (0, 0, 0) at t = 30 s.
* MotionData.NormalizedActorScale scales the actor over normalised stage time (0 -> 1 over the first 10%).
* Each segment attaches an EffectDataAsset's Niagara system to a socket of the component tagged ``Mesh``
  (SnapToTarget) from StartTime to EndTime (stage seconds). At the end the system is deactivated; with
  bDetachFXOnSegmentEnd it also stays where it was instead of following the ship.

UE frame throughout (cm; FQuat (x, y, z, w) with v' = q v q*). Blender conversion is niagara_cache.to_b / quat_b.
Uses absolute imports like the niagara_* bake modules (the addon folder is on sys.path). No bpy.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np

from niagara_cache import rotator_quat
from prop_assemble import _ue_entries, parse_static_mesh_sockets

LWC_SAFE = 1048576.0  # niagara_scheduler rejects owner translations at or beyond 2^20 cm


def _bezier(p0, p1, p2, p3, a):
    u = 1.0 - a
    return u * u * u * p0 + 3.0 * u * u * a * p1 + 3.0 * u * a * a * p2 + a * a * a * p3


def _weighted(k1, k2, alpha):
    """FRichCurve WeightedEvalForTwoKeys: tangent handles from angle + weight, time solved on the Bezier X curve."""
    t1, t2 = float(k1['Time']), float(k2['Time'])
    x = t2 - t1
    mode1, mode2 = str(k1.get('TangentWeightMode', '')), str(k2.get('TangentWeightMode', ''))
    leave, arrive = float(k1.get('LeaveTangent', 0.0)), float(k2.get('ArriveTangent', 0.0))
    w1 = (math.hypot(x, leave * x) / 3.0 if mode1.endswith(('WeightedNone', 'WeightedArrive'))
          else float(k1.get('LeaveTangentWeight', 0.0)))
    w2 = (math.hypot(x, arrive * x) / 3.0 if mode2.endswith(('WeightedNone', 'WeightedLeave'))
          else float(k2.get('ArriveTangentWeight', 0.0)))
    a1, a2 = math.atan(leave), math.atan(arrive)
    x1, y1 = (math.cos(a1) * w1) / x, math.sin(a1) * w1 + float(k1['Value'])
    x2, y2 = (x - math.cos(a2) * w2) / x, -math.sin(a2) * w2 + float(k2['Value'])
    # Bezier (0, x1, x2, 1) in power basis, minus alpha (BezierToPower + SolveCubic)
    a, b, c = x1, x2 - x1, 1.0 - x2
    d, e = b - a, c - b
    roots = np.roots([e - d, 3.0 * d, 3.0 * a, -alpha])
    real = [float(r.real) for r in roots if abs(r.imag) < 1e-9]
    if len(real) == 1:
        s = real[0]
    else:
        inside = [min(max(r, 0.0), 1.0) for r in real if -1e-9 <= r <= 1.0 + 1e-9]  # root finding lands on 1 +- eps at keys
        s = max(inside) if inside else 0.0
    return _bezier(float(k1['Value']), y1, y2, float(k2['Value']), s)


def eval_curve(keys, t: float) -> float:
    """FRichCurve::Eval with constant extrapolation, including weighted cubic tangents (lighting_grade.eval_rich_curve has none)."""
    if not keys:
        return 0.0
    if t <= float(keys[0]['Time']):
        return float(keys[0]['Value'])
    if t >= float(keys[-1]['Time']):
        return float(keys[-1]['Value'])
    for k1, k2 in zip(keys, keys[1:]):
        t1, t2 = float(k1['Time']), float(k2['Time'])
        if t > t2:
            continue
        diff = t2 - t1
        if diff <= 0.0:
            return float(k2['Value'])
        alpha = (t - t1) / diff
        mode = str(k1.get('InterpMode', 'RCIM_Cubic'))
        v1, v2 = float(k1['Value']), float(k2['Value'])
        if mode.endswith('RCIM_Constant'):
            return v1
        if mode.endswith('RCIM_Linear'):
            return v1 + (v2 - v1) * alpha
        if (str(k1.get('TangentWeightMode', '')).endswith(('WeightedNone', 'WeightedArrive'))
                and str(k2.get('TangentWeightMode', '')).endswith(('WeightedNone', 'WeightedLeave'))):
            return _bezier(v1, v1 + float(k1.get('LeaveTangent', 0.0)) * diff / 3.0,
                           v2 - float(k2.get('ArriveTangent', 0.0)) * diff / 3.0, v2, alpha)
        return _weighted(k1, k2, alpha)
    return float(keys[-1]['Value'])


def curve_channels(path: str) -> list[list[dict]]:
    """The six FloatCurves key lists of a CurveTranslateRotate export (missing channels are empty)."""
    with open(path, 'r', encoding='utf-8') as fh:
        entries = _ue_entries(json.load(fh))
    props = next((e.get('Properties') or {} for e in entries if e.get('Type') == 'CurveTranslateRotate'), {})
    out = [[] for _ in range(6)]
    for key, value in props.items():
        if key == 'FloatCurves':
            index = 0
        elif key.startswith('FloatCurves['):
            index = int(key[len('FloatCurves['):-1])
        else:
            continue
        if isinstance(value, list):
            value = value[0] if value else {}
        if isinstance(value, dict) and 0 <= index < 6:
            out[index] = sorted(value.get('Keys') or [], key=lambda k: float(k.get('Time', 0.0)))
    return out


def curve_duration(channels) -> float:
    return max((float(k.get('Time', 0.0)) for keys in channels for k in keys), default=0.0)


def quat_mul(a, b):
    """FQuat a * b (applies b, then a)."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def quat_rotate(q, v):
    """FQuat::RotateVector: v + w*t + q x t with t = 2 (q x v)."""
    x, y, z, w = q
    tx, ty, tz = 2.0 * (y * v[2] - z * v[1]), 2.0 * (z * v[0] - x * v[2]), 2.0 * (x * v[1] - y * v[0])
    return (v[0] + w * tx + (y * tz - z * ty), v[1] + w * ty + (z * tx - x * tz), v[2] + w * tz + (x * ty - y * tx))


def stage_pose(channels, t: float):
    """(translation cm, FQuat) of the orchestrated actor relative to the landing transform at stage time t."""
    v = [eval_curve(keys, t) if keys else 0.0 for keys in channels]
    return (v[0], v[1], v[2]), rotator_quat({'Roll': v[3], 'Pitch': v[4], 'Yaw': v[5]})


def actor_scale(motion: dict, t: float, duration: float) -> float:
    keys = motion.get('scale_keys') or []
    return eval_curve(keys, t / duration) if keys and duration > 0 else 1.0


def socket_pose(stage_loc, stage_quat, scale: float, socket: dict):
    """Landing-relative location/rotation of a 'Mesh' socket ({'location': cm, 'rotation': (pitch, yaw, roll)})."""
    pitch, yaw, roll = socket.get('rotation', (0.0, 0.0, 0.0))
    offset = quat_rotate(stage_quat, tuple(scale * c for c in socket.get('location', (0.0, 0.0, 0.0))))
    return (tuple(a + b for a, b in zip(stage_loc, offset)),
            quat_mul(stage_quat, rotator_quat({'Pitch': pitch, 'Yaw': yaw, 'Roll': roll})))


def _effect_system(effect_dir: str, effect_path: str) -> str:
    name = effect_path.rsplit('/', 1)[-1].split('.')[0]
    path = os.path.join(effect_dir or '', name + '.json')
    if not os.path.isfile(path):
        return ''
    with open(path, 'r', encoding='utf-8') as fh:
        for e in _ue_entries(json.load(fh)):
            ref = ((((e.get('Properties') or {}).get('NiagaraSettings') or {}).get('DefaultNiagaraSystem') or {})
                   .get('NiagaraSystem') or {})
            if ref.get('AssetPathName'):
                return ref['AssetPathName']
    return ''


def load_orchestrator(path: str, effect_dir: str = '') -> dict:
    """Stage 0 of the cooked orchestrator CDO plus its CloudPassthroughComponent template.

    segments: [{effect, system (Niagara object path or ''), component_tag, socket, start, end, detach}]."""
    with open(path, 'r', encoding='utf-8') as fh:
        entries = _ue_entries(json.load(fh))
    cdo = next((e.get('Properties') or {} for e in entries if 'SpawnStages' in (e.get('Properties') or {})), {})
    stage = (cdo.get('SpawnStages') or [{}])[0]
    segments = []
    for s in stage.get('Segments') or []:
        effect = (s.get('SegmentStartEffect') or {}).get('ObjectPath') or ''
        segments.append({'effect': effect.rsplit('/', 1)[-1].split('.')[0], 'system': _effect_system(effect_dir, effect),
                         'component_tag': s.get('FXAttachToComponentTag') or '', 'socket': s.get('FXAttachBoneOrSocketName') or '',
                         'start': float(s.get('StartTime', 0.0)), 'end': float(s.get('EndTime', 0.0)),
                         'detach': bool(s.get('bDetachFXOnSegmentEnd', False))})
    md = stage.get('MotionData') or {}

    def keys(name):
        return sorted(((md.get(name) or {}).get('EditorCurveData') or {}).get('Keys') or [], key=lambda k: float(k.get('Time', 0.0)))

    cloud = next((e.get('Properties') or {} for e in entries if e.get('Type') == 'CloudPassthroughComponent'), {})
    return {'curve': ((md.get('Curve') or {}).get('ObjectPath') or '').rsplit('.', 1)[0], 'segments': segments,
            'motion': {'scale_keys': keys('NormalizedActorScale'), 'shake_keys': keys('NormalizedShakeAmount'),
                       'shake_max': md.get('ShakeMaximumMagnitude'), 'shake_smooth': md.get('ShakeSmooth')},
            'cloud': cloud}


def owner_track(channels, motion: dict, socket: dict, t0: float, t1: float, fps: float, end: float | None = None,
                detach: bool = False):
    """Per-tick owner transforms for a system activated at t0 and simulated to t1 (tick k ends at t0 + k / fps).

    Returns (start pose, start velocity cm/s, translations, rotations, deactivate_tick or None)."""
    duration = curve_duration(channels)

    def pose(t):
        if detach and end is not None and t > end:
            t = end
        loc, q = stage_pose(channels, t)
        return socket_pose(loc, q, actor_scale(motion, t, duration), socket)

    n = int(round((t1 - t0) * fps))
    track = [pose(t0 + k / fps) for k in range(n + 1)]
    velocity = tuple((b - a) * fps for a, b in zip(track[0][0], track[1][0])) if n else (0.0, 0.0, 0.0)
    deactivate = int(round((end - t0) * fps)) if end is not None and end < t1 else None
    return track[0], velocity, [p[0] for p in track[1:]], [p[1] for p in track[1:]], deactivate


def bake_offset(points, limit: float = LWC_SAFE):
    """World offset (cm, whole metres) to subtract so every owner translation fits the scheduler's float32 range;
    (0, 0, 0) when the track already fits."""
    pts = list(points)
    if all(abs(c) < limit for p in pts for c in p):
        return (0.0, 0.0, 0.0)
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    mid = tuple(100.0 * round((a + b) / 200.0) for a, b in zip(lo, hi))
    if any(h - m >= limit or m - a >= limit for a, h, m in zip(lo, hi, mid)):
        raise ValueError('owner track spans more than 2 x %g cm' % limit)
    return mid


def float_curve(path: str) -> list[dict]:
    """Sorted keys of a CurveFloat export."""
    with open(path, 'r', encoding='utf-8') as fh:
        entries = _ue_entries(json.load(fh))
    props = next((e.get('Properties') or {} for e in entries if e.get('Type') == 'CurveFloat'), {})
    return sorted((props.get('FloatCurve') or {}).get('Keys') or [], key=lambda k: float(k.get('Time', 0.0)))


def load_dispersion(path: str) -> dict:
    """BP_FrigateCloudDispersion CDO (ReplicatedCloudDispersionActor): size, growth origin and the curve it plays."""
    with open(path, 'r', encoding='utf-8') as fh:
        entries = _ue_entries(json.load(fh))
    p = next((e.get('Properties') or {} for e in entries if 'DisperseCurve' in (e.get('Properties') or {})), {})
    origin = (p.get('GrowthOrigin') or {}).get('RelativeOrigin') or {}
    return {'curve': ((p.get('DisperseCurve') or {}).get('ObjectPath') or '').rsplit('.', 1)[0],
            'max_size': float(p.get('MaxFlashFreezeSize', 0.0)), 'amount': float(p.get('FlashFreezeAmount', 1.0)),
            'relative_origin': tuple(float(origin.get(k, 0.0)) for k in 'XYZ'),
            'clamp_to_sphere': bool((p.get('GrowthOrigin') or {}).get('bClampToSphereSize', False))}


def penetration_time(channels, altitude: float, step: float = 1.0 / 600.0):
    """First stage time the actor root's landing-relative Z drops to altitude (cm), or None."""
    duration = curve_duration(channels)
    prev_t, prev_z = 0.0, stage_pose(channels, 0.0)[0][2]
    if prev_z <= altitude:
        return 0.0
    t = step
    while t <= duration + step:
        z = stage_pose(channels, t)[0][2]
        if z <= altitude:
            lo, hi = prev_t, t
            for _ in range(40):
                mid = (lo + hi) / 2.0
                lo, hi = (mid, hi) if stage_pose(channels, mid)[0][2] > altitude else (lo, mid)
            return hi
        prev_t, prev_z, t = t, z, t + step
    return None


def dispersion_track(channels, cloud: dict, disp: dict, disperse_keys, times):
    """FlashFreezeLoc / FlashFreezeSize over stage times for the cloud hole the frigate punches.

    Cooked data gives the trigger (CloudPassthroughComponent: bTriggerOnPathPenetration, CloudLayerAltitudeOffset,
    SecondsBeforeBottomCloudLayerPenetrationToTriggerAt) and the growth (DisperseCurve x MaxFlashFreezeSize, GrowthOrigin).
    The placement logic is compiled Angelscript; reconstruction used here: the hole grows from the root's
    cloud-bottom penetration point P, which stays at RelativeOrigin x size of the sphere in the travel frame
    (X = horizontal velocity at P, Z = up): centre = P - size * (ox X + oy Y + oz Z). DispersionOffset is not applied.

    Returns {'trigger', 'penetration', 'origin', 'frame', 'samples': [(t, centre cm, size cm)]}."""
    altitude = float(cloud.get('CloudLayerAltitudeOffset', 0.0))
    t_pen = penetration_time(channels, altitude)
    if t_pen is None:
        return {'trigger': None, 'penetration': None, 'origin': None, 'frame': None, 'samples': [(t, None, 0.0) for t in times]}
    t_trig = max(0.0, t_pen - float(cloud.get('SecondsBeforeBottomCloudLayerPenetrationToTriggerAt', 0.0)))
    p = stage_pose(channels, t_pen)[0]
    q = stage_pose(channels, t_pen + 0.01)[0]
    hx, hy = q[0] - p[0], q[1] - p[1]
    n = math.hypot(hx, hy) or 1.0
    fx, fy = (hx / n, hy / n, 0.0), (-hy / n, hx / n, 0.0)
    ox, oy, oz = disp.get('relative_origin', (0.0, 0.0, 0.0))
    samples = []
    for t in times:
        size = disp.get('max_size', 0.0) * eval_curve(disperse_keys, t - t_trig) if t >= t_trig else 0.0
        centre = tuple(p[i] - size * (ox * fx[i] + oy * fy[i] + oz * (1.0 if i == 2 else 0.0)) for i in range(3))
        samples.append((t, centre, size))
    return {'trigger': t_trig, 'penetration': t_pen, 'origin': p, 'frame': (fx, fy, (0.0, 0.0, 1.0)), 'samples': samples}


def frame_of(t: float, fps: float) -> int:
    """Scene frame of stage time t (frame 1 = t 0)."""
    return 1 + int(round(t * fps))


def sockets(sm_json_path: str) -> dict:
    return parse_static_mesh_sockets(sm_json_path)


def degrees(q) -> tuple:
    """FQuat -> (pitch, yaw, roll) degrees (FQuat::Rotator, for reports)."""
    x, y, z, w = q
    sing = z * x - w * y
    yaw = math.degrees(math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)))
    if sing < -0.4999995:
        return (-90.0, yaw, -yaw - 2.0 * math.degrees(math.atan2(x, w)))
    if sing > 0.4999995:
        return (90.0, yaw, yaw - 2.0 * math.degrees(math.atan2(x, w)))
    pitch = math.degrees(math.asin(2.0 * sing))
    roll = math.degrees(math.atan2(-2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y)))
    return (pitch, yaw, roll)
