"""Niagara data interfaces for the offline VM (Waves N4-N5; spec N-O3 §1-§11).

Binding of VM external functions to cooked data interfaces, the UE 5.7 implementations of the supported DI functions,
the Gate V hash stub (vectorized) and the external-call handler used by both VM backends.
"""
import base64
import json
import math
from dataclasses import dataclass, field

import numpy as np

from niagara_runtime import _fma57

FAST_PATH = 'FastPathLibrary'
CURVE_ELEMENTS = {
    ('NiagaraDataInterfaceCurve', 'SampleCurve'): 1,
    ('NiagaraDataInterfaceVector2DCurve', 'SampleVector2DCurve'): 2,
    ('NiagaraDataInterfaceVectorCurve', 'SampleVectorCurve'): 3,
    ('NiagaraDataInterfaceVector4Curve', 'SampleColorCurve'): 4,
    ('NiagaraDataInterfaceColorCurve', 'SampleColorCurve'): 4,
}
CURVE_CLASSES = {cls: e for (cls, _), e in CURVE_ELEMENTS.items()}
SPRITE_INFO = 'NiagaraDataInterfaceSpriteRendererInfo'
EMITTER_PROPERTIES = 'NiagaraDataInterfaceEmitterProperties'
AUDIO_PLAYER = 'NiagaraDataInterfaceAudioPlayer'
VECTOR_FIELD = 'NiagaraDataInterfaceVectorField'
ARRAY_MESH = 'NiagaraDataInterfaceArrayMesh'
CAMERA = 'NiagaraDataInterfaceCamera'
PARTICLE_READ = 'NiagaraDataInterfaceParticleRead'
# value type -> (float components, int components, default words); NiagaraDataInterfaceParticleRead.cpp ReadWithCheck defaults
PR_TYPES = {'Int': (0, 1, [0]), 'Bool': (0, 1, [0]), 'Float': (1, 0, [0]), 'Vector 2D': (2, 0, [0, 0]), 'Vector': (3, 0, [0, 0, 0]),
            'Vector 4': (4, 0, [0, 0, 0, 0]), 'Color': (4, 0, [0x3f800000] * 4), 'Position': (3, 0, [0, 0, 0]),
            'Quaternion': (4, 0, [0, 0, 0, 0x3f800000]), 'ID': (0, 2, [0xffffffff, 0xffffffff])}
PR_READS = {('Get %s By %s' % (t, k)): (t, k) for t in PR_TYPES for k in ('Index', 'ID')}
COLLISION = 'NiagaraDataInterfaceCollisionQuery'
# class -> (cooked property, words per element, component keys, default element as floats/bool)
ARRAY_TYPES = {
    'NiagaraDataInterfaceArrayFloat': ('FloatData', 1, None, (0.0,)),
    'NiagaraDataInterfaceArrayFloat2': ('InternalFloatData', 2, ('X', 'Y'), (0.0, 0.0)),
    'NiagaraDataInterfaceArrayFloat3': ('InternalFloatData', 3, ('X', 'Y', 'Z'), (0.0, 0.0, 0.0)),
    'NiagaraDataInterfaceArrayPosition': ('PositionData', 3, ('X', 'Y', 'Z'), (0.0, 0.0, 0.0)),
    'NiagaraDataInterfaceArrayColor': ('ColorData', 4, ('R', 'G', 'B', 'A'), (1.0, 1.0, 1.0, 1.0)),
    'NiagaraDataInterfaceArrayBool': ('BoolData', 1, None, (False,)),
}
ARRAY_FUNCTIONS = ('Length', 'LastIndex', 'IsValidIndex', 'Get', 'SetArrayElem', 'Resize')
TRUE_WORD = 0xffffffff
IMPLEMENTED = frozenset(CURVE_ELEMENTS) | {(FAST_PATH, 'FastMatrixToQuaternion'), (SPRITE_INFO, 'GetSubUVDetails'),
                                           (EMITTER_PROPERTIES, 'SetFixedBounds'), (AUDIO_PLAYER, 'PlayAudioAtLocation'),
                                           (VECTOR_FIELD, 'SampleField'), (ARRAY_MESH, 'Length'),
                                           (CAMERA, 'GetCameraPropertiesCPU/GPU'), (COLLISION, 'PerformCollisionQueryAsyncCPU'),
                                           (PARTICLE_READ, 'Get Num Particles'), (PARTICLE_READ, 'Get Particle Index'),
                                           (PARTICLE_READ, 'Get Particle Index From ID Table')} | {(PARTICLE_READ, f) for f in PR_READS} | \
    {(cls, fn) for cls in ARRAY_TYPES for fn in ARRAY_FUNCTIONS}


class DIError(ValueError):
    pass


@dataclass(frozen=True)
class Binding:
    fn_index: int
    owner_name: str
    name: str
    num_inputs: int
    num_outputs: int
    specifiers: tuple
    variadic: tuple
    kind: str
    di_class: object
    di_export: object
    user_ptr: int

    @property
    def label(self):
        return '%s.%s' % (self.di_class if self.kind == 'di' else FAST_PATH, self.name)

    @property
    def identity(self):
        """Spec §11.4: User.* parameters share one instance per name; others are keyed by the bound DI export."""
        return ('user', self.owner_name) if str(self.owner_name).startswith('User.') else ('export', self.di_export)


@dataclass(frozen=True)
class CurveLUT:
    di_class: str
    e: int
    lut: np.ndarray
    min_time: np.float32
    max_time: np.float32
    inv_time_range: np.float32
    num_samples_minus_one: np.float32


@dataclass(frozen=True)
class VectorFieldData:
    asset: str
    size: tuple
    bounds_min: tuple
    bounds_max: tuple
    allow_cpu_access: bool
    halves: np.ndarray        # uint16, shape (SizeX*SizeY*SizeZ, 3), x fastest


@dataclass
class DIInstanceState:
    """Per system instance DI state (spec §11.4): modified arrays plus the bounds and audio logs."""
    arrays: dict = field(default_factory=dict)
    bounds_log: list = field(default_factory=list)
    audio_log: list = field(default_factory=list)
    camera: tuple = None            # scene input (x, y, z, pitch, yaw, roll, fov), UE world units; None = no player camera
    collision: object = None        # CollisionWorld (scene triangles) or None = empty world
    collision_batches: dict = field(default_factory=dict)
    collision_log: list = field(default_factory=list)
    particle_sources: dict = field(default_factory=dict)   # emitter name -> callable() -> (count, layout, buffer, id_table, is_gpu)

    def pre_tick(self):
        """DI PerInstanceTick: collision batches collect the results of the traces dispatched last tick."""
        for batch in self.collision_batches.values():
            batch.collect(self.collision)

    def post_tick(self):
        """DI PerInstanceTickPostSimulate: dispatch this tick's traces (flip buffers, clear the write buffer)."""
        for batch in self.collision_batches.values():
            batch.dispatch()


def rotation_axes(pitch, yaw, roll):
    """FRotationMatrix(FRotator) scaled axes X (forward), Y (right), Z (up) in UE world space (double maths, as in 5.7 LWC)."""
    sp, cp = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    sy, cy = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
    sr, cr = math.sin(math.radians(roll)), math.cos(math.radians(roll))
    x = (cp * cy, cp * sy, sp)
    y = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    z = (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp)
    return x, y, z


def camera_properties_words(camera):
    """UNiagaraDataInterfaceCamera::GetCameraProperties outputs: position, forward (X), up (Z), right (Y) as float words."""
    if camera is None:  # FCameraDataInterface_InstanceData defaults (no player controller, no editor viewport)
        camera = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    x, y, z = rotation_axes(*camera[3:6])
    return _f32_words(list(camera[0:3]) + list(x) + list(z) + list(y))


class CollisionWorld:
    """Static scene triangles (UE world cm) for line traces; first blocking hit = nearest intersection (two-sided)."""

    def __init__(self, triangles):
        t = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
        self.v0, self.e1, self.e2 = t[:, 0], t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
        n = np.cross(self.e1, self.e2)
        self.normal = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
        self.lo, self.hi = t.min(axis=1), t.max(axis=1)

    @classmethod
    def load(cls, path):
        return cls(np.load(path)['triangles'])

    def trace(self, start, end):
        """(hit, position, normal) for one segment (Moller-Trumbore on bbox-overlapping triangles)."""
        start, end = np.asarray(start, dtype=np.float64), np.asarray(end, dtype=np.float64)
        lo, hi = np.minimum(start, end), np.maximum(start, end)
        cand = np.nonzero(np.all(self.hi >= lo, axis=1) & np.all(self.lo <= hi, axis=1))[0]
        if not len(cand):
            return False, None, None
        d = end - start
        e1, e2, v0 = self.e1[cand], self.e2[cand], self.v0[cand]
        p = np.cross(d, e2)
        det = np.einsum('ij,ij->i', e1, p)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = start - v0
        u = np.einsum('ij,ij->i', s, p) * inv
        q = np.cross(s, e1)
        v = (q @ d) * inv
        t = np.einsum('ij,ij->i', e2, q) * inv
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t >= 0) & (t <= 1)
        if not hit.any():
            return False, None, None
        k = np.argmin(np.where(hit, t, np.inf))
        nrm = self.normal[cand[k]]
        if np.dot(nrm, d) > 0:
            nrm = -nrm
        return True, start + d * t[k], nrm


class CollisionBatch:
    """FNiagaraDICollisionQueryBatch (NiagaraCollision.cpp): double-buffered async traces, results available next tick."""

    def __init__(self):
        self.traces = [[], []]
        self.hit_index = [[], []]
        self.results = []
        self.cur = 0

    def submit(self, start, end):
        if float(np.sum((np.asarray(end, dtype=np.float64) - np.asarray(start, dtype=np.float64)) ** 2)) <= 1e-8:  # SMALL_NUMBER
            return -1
        self.traces[self.cur].append((tuple(start), tuple(end)))
        self.hit_index[self.cur].append(-1)
        return len(self.traces[self.cur]) - 1

    def dispatch(self):
        self.cur ^= 1
        self.traces[self.cur] = []
        self.hit_index[self.cur] = []

    def collect(self, world):
        read = self.cur ^ 1
        if world is None or not self.traces[read]:
            return
        self.results = []
        for i, (a, b) in enumerate(self.traces[read]):
            hit, pos, nrm = world.trace(a, b)
            if hit:
                self.hit_index[read][i] = len(self.results)
                self.results.append((False, pos, nrm, 0.0, 0.0, 0))

    def result(self, trace_id):
        read = self.cur ^ 1
        if 0 <= trace_id < len(self.traces[read]):
            k = self.hit_index[read][trace_id]
            if 0 <= k < len(self.results):
                return self.results[k]
        return None


def particle_read_impl(b, props, state):
    """UNiagaraDataInterfaceParticleRead CPU functions on the source emitter's current data (GPU sources read as invalid)."""
    source = ((props.get('EmitterBinding') or {}).get('EmitterName')) or ''
    attr = dict(b.specifiers).get('Attribute')

    def src():
        f = state.particle_sources.get(source)
        return f() if f is not None else None

    def column_words(layout, buffer, nf, ni, rows):
        v = layout.find(attr) if attr else None
        if v is None or len(v.type.float_offsets) != nf or len(v.type.int_offsets) != ni:
            return None
        cols = [buffer['float', v.float_start + j] for j in range(nf)] + [buffer['int32', v.int_start + j] for j in range(ni)]
        return [c[rows] for c in cols]

    def id_lookup(info, idx, tag):
        count, layout, buffer, table, gpu = info
        v = layout.find('ID')
        ok = np.zeros(len(idx), bool)
        rows = np.full(len(idx), -1, np.int64)
        if v is None or not table:
            return ok, rows
        table = np.asarray(table, np.int64)
        inr = (idx >= 0) & (idx < len(table))
        cand = np.where(inr, table[np.clip(idx, 0, len(table) - 1)], -1)
        live = (cand >= 0) & (cand < count)
        ids = buffer['int32', v.int_start][:count].view(np.int32)
        tags = buffer['int32', v.int_start + 1][:count].view(np.int32)
        c = np.clip(cand, 0, max(0, count - 1))
        ok = live & (ids[c] == idx) & (tags[c] == tag) if count else live & False
        return ok, np.where(ok, cand, -1)

    name = b.name
    if name == 'Get Num Particles':
        def fn(args, n, start):
            info = src()
            return [np.full(n, info[0] if info else 0, dtype=np.uint32)]
        return fn
    if name in ('Get Particle Index', 'Get Particle Index From ID Table'):
        def fn(args, n, start):
            info = src()
            idx = np.asarray(args[1], np.uint32).view(np.int32).astype(np.int64)
            if name == 'Get Particle Index':
                if info is None:
                    return [np.full(n, 0xffffffff, np.uint32)]
                tag = np.asarray(args[2], np.uint32).view(np.int32).astype(np.int64)
                ok, rows = id_lookup(info, idx, tag)
                return [rows.astype(np.int32).view(np.uint32)]
            table = np.asarray(info[3] if info else [], np.int64)
            inr = (idx >= 0) & (idx < len(table))
            rows = np.where(inr, table[np.clip(idx, 0, max(0, len(table) - 1))] if len(table) else -1, -1)
            return [np.where(inr & (rows != -1), TRUE_WORD, 0).astype(np.uint32), rows.astype(np.int32).view(np.uint32)]
        return fn
    t, key = PR_READS[name]
    nf, ni, default = PR_TYPES[t]

    def fn(args, n, start):
        info = src()
        out_valid = np.zeros(n, np.uint32)
        outs = [np.full(n, w, np.uint32) for w in default]
        if info is None or info[4]:
            return [out_valid] + outs
        count, layout, buffer, table, _ = info
        if key == 'Index':
            idx = np.asarray(args[1], np.uint32).view(np.int32).astype(np.int64)
            ok = (idx >= 0) & (idx < count) if count > 0 else np.zeros(n, bool)
            rows = np.where(ok, idx, 0)
        else:
            if not table:
                return [out_valid] + outs
            ok, rows = id_lookup(info, np.asarray(args[1], np.uint32).view(np.int32).astype(np.int64),
                                 np.asarray(args[2], np.uint32).view(np.int32).astype(np.int64))
            rows = np.where(ok, rows, 0)
        cols = column_words(layout, buffer, nf, ni, rows) if count else None
        if cols is None:
            return [out_valid] + outs
        return [np.where(ok, TRUE_WORD, 0).astype(np.uint32)] + [np.where(ok, c, d).astype(np.uint32) for c, d in zip(cols, outs)]
    return fn


def collision_query_async(batch, args, n):
    """UNiagaraDataInterfaceCollisionQuery::PerformQueryAsyncCPU (inputs: userptr, id, start, end, channel, skip, complex)."""
    w = [np.asarray(a, dtype=np.uint32) for a in args]
    f = [w[i].view(np.float32) for i in range(2, 8)]
    ids = w[1].view(np.int32)
    out = [np.zeros(n, dtype=np.uint32) for _ in range(12)]
    for i in range(n):
        skip = w[9][i] != 0
        qid = -1 if skip else batch.submit((float(f[0][i]), float(f[1][i]), float(f[2][i])), (float(f[3][i]), float(f[4][i]), float(f[5][i])))
        out[0][i] = np.uint32(np.int32(qid + 1).view(np.uint32))
        res = batch.result(int(ids[i]) - 1) if ids[i] > 0 else None
        if res is not None:
            inside, pos, nrm, friction, restitution, mat = res
            vals = _f32_words([float(pos[0]), float(pos[1]), float(pos[2]), float(nrm[0]), float(nrm[1]), float(nrm[2]), friction, restitution])
            out[1][i] = TRUE_WORD
            out[2][i] = TRUE_WORD if inside else 0
            for j in range(8):
                out[3 + j][i] = vals[j]
            out[11][i] = mat
    return out


def _class_short(typedef):
    name = ((typedef or {}).get('ClassStructOrEnum') or {}).get('ObjectName', '')
    return name.split("'")[1] if "'" in name else name


def _export_index(ref):
    path = ref.get('ObjectPath') if isinstance(ref, dict) else None
    if not path or '.' not in path:
        return None
    try:
        return int(path.rsplit('.', 1)[1])
    except ValueError:
        return None


def _f32_words(values):
    return np.asarray(values, dtype=np.float32).view(np.uint32)


def _bits_f64(text):
    return float(np.array([int(text, 16)], dtype=np.uint64).view(np.float64)[0])


def bind_script(exports, script_export_index):
    """Spec §1: one Binding per CalledVMExternalFunctions entry (index = VM fn_index)."""
    props = exports[script_export_index].get('Properties') or {}
    vm = props.get('CachedScriptVM') or {}
    infos = vm.get('DataInterfaceInfo') or []
    store = (props.get('ScriptExecutionParamStore') or {}).get('DataInterfaces') or []
    bindings = []
    for k, call in enumerate(vm.get('CalledVMExternalFunctions') or []):
        specifiers = tuple((s.get('Key'), s.get('Value')) for s in call.get('FunctionSpecifiers') or [])
        common = dict(fn_index=k, owner_name=call.get('OwnerName'), name=call.get('Name'),
                      num_inputs=len(call.get('InputParamLocations') or []), num_outputs=int(call.get('NumOutputs') or 0),
                      specifiers=specifiers,
                      variadic=(len(call.get('VariadicInputs') or []), len(call.get('VariadicOutputs') or [])))
        if call.get('OwnerName') == FAST_PATH:
            bindings.append(Binding(**common, kind='fastpath', di_class=None, di_export=None, user_ptr=-1))
            continue
        slot = next((i for i, info in enumerate(infos) if info.get('Name') == call.get('OwnerName')), None)
        if slot is None:
            raise DIError('di_unresolved')
        di_class = _class_short(infos[slot].get('Type'))
        di_export = _export_index(store[slot]) if slot < len(store) else None
        if di_export is None or not 0 <= di_export < len(exports) or exports[di_export].get('Type') != di_class:
            raise DIError('di_class_mismatch')
        bindings.append(Binding(**common, kind='di', di_class=di_class, di_export=di_export,
                                user_ptr=int(infos[slot].get('UserPtrIdx', -1))))
    return bindings


# ---------------------------------------------------------------- curves (spec §3)
def curve_from_properties(di_class, properties):
    """Spec §3.2: cooked properties with class-default values for absent fields."""
    e = CURVE_CLASSES.get(di_class)
    if e is None:
        raise DIError('di_not_curve')
    if properties.get('bUseLUT', True) is False:
        raise DIError('curve_without_lut')
    lut = properties.get('ShaderLUT')
    lut = np.array([0.0] * e if lut is None else lut, dtype=np.float32)
    n1 = np.float32(properties.get('LUTNumSamplesMinusOne', 0.0))
    if lut.size % e or float(n1) != lut.size // e - 1:
        raise DIError('curve_lut_size')
    return CurveLUT(di_class, e, lut, np.float32(properties.get('LUTMinTime', 0.0)), np.float32(properties.get('LUTMaxTime', 1.0)),
                    np.float32(properties.get('LUTInvTimeRange', 1.0)), n1)


def _word_f32(word):
    return np.array([int(word) & 0xffffffff], dtype=np.uint32).view(np.float32)[0]


def curve_from_words(di):
    """Gate D case form: {class, function, lut: [uint32 words], lut_min_time, lut_max_time, lut_inv_time_range,
    lut_num_samples_minus_one: uint32 words}."""
    e = CURVE_ELEMENTS.get((di.get('class'), di.get('function')))
    if e is None:
        raise DIError('di_not_curve')
    lut = np.array([int(w) & 0xffffffff for w in di['lut']], dtype=np.uint32).view(np.float32)
    n1 = _word_f32(di['lut_num_samples_minus_one'])
    if lut.size % e or float(n1) != lut.size // e - 1:
        raise DIError('curve_lut_size')
    return CurveLUT(di['class'], e, lut, _word_f32(di['lut_min_time']), _word_f32(di['lut_max_time']),
                    _word_f32(di['lut_inv_time_range']), n1)


def curve_words(curve, function):
    """Inverse of curve_from_words (exact float32 words)."""
    def word(value):
        return int(np.array([value], dtype=np.float32).view(np.uint32)[0])
    return {'class': curve.di_class, 'function': function, 'lut': [int(w) for w in curve.lut.view(np.uint32)],
            'lut_min_time': word(curve.min_time), 'lut_max_time': word(curve.max_time),
            'lut_inv_time_range': word(curve.inv_time_range), 'lut_num_samples_minus_one': word(curve.num_samples_minus_one)}


def sample_curve(curve, x_words, fma_mode='unfused'):
    """Spec §3.3: UE 5.7 SampleCurveInternal<true>; float32 throughout; one uint32 word array per output element."""
    if fma_mode not in ('unfused', 'fused'):
        raise DIError('fma_mode')
    x = np.asarray(x_words, dtype=np.uint32).view(np.float32)
    n1, e, lut = curve.num_samples_minus_one, curve.e, curve.lut
    zero, one = np.float32(0.0), np.float32(1.0)
    with np.errstate(all='ignore'):
        r0 = ((x - curve.min_time) * curve.inv_time_range) * n1
        m = np.where(r0 < n1, r0, n1).astype(np.float32)
        r = np.where(zero < m, m, zero).astype(np.float32)
        lo = np.trunc(r).astype(np.float32)
        hi = np.where(lo < n1, lo + one, lo).astype(np.float32)
        w = (r - lo).astype(np.float32)
        ia = (lo * np.float32(e)).astype(np.int64)
        ib = (hi * np.float32(e)).astype(np.int64)
        outputs = []
        for k in range(e):
            a = lut[ia + k]
            d = (lut[ib + k] - a).astype(np.float32)
            if fma_mode == 'unfused':
                value = (a + (w * d).astype(np.float32)).astype(np.float32)
            else:
                value = np.array([_fma57(float(wi), float(di), float(ai)) for wi, di, ai in zip(w, d, a)], dtype=np.float32)
            outputs.append(value.view(np.uint32).copy())
    return outputs


# ---------------------------------------------------------------- fast path (spec §11.3a)
def fast_matrix_to_quaternion(args):
    """FastPathLibrary.FastMatrixToQuaternion, UE 5.3/5.7 CPU kernel; float32; InvSqrt = 1/sqrt correctly rounded."""
    m = [np.asarray(a, dtype=np.uint32).view(np.float32) for a in args]
    n = m[0].size
    one, half = np.float32(1.0), np.float32(0.5)
    mat = np.stack([np.stack(m[4 * r:4 * r + 3]) for r in range(3)])        # (3, 3, n): M[r][c] for r, c < 3
    with np.errstate(all='ignore'):
        tr = (mat[0, 0] + mat[1, 1]) + mat[2, 2]
        s0 = one / np.sqrt(tr + one)
        s = half * s0
        pos = [((mat[1, 2] - mat[2, 1]) * s), ((mat[2, 0] - mat[0, 2]) * s), ((mat[0, 1] - mat[1, 0]) * s), half * (one / s0)]
        lanes = np.arange(n)
        i = np.where(mat[1, 1] > mat[0, 0], 1, 0)
        i = np.where(mat[2, 2] > mat[i, i, lanes], 2, i)
        j = (i + 1) % 3
        k = (j + 1) % 3
        sn = ((mat[i, i, lanes] - mat[j, j, lanes]) - mat[k, k, lanes]) + one
        sn0 = one / np.sqrt(sn)
        sh = half * sn0
        q = np.zeros((4, n), dtype=np.float32)
        q[i, lanes] = half * (one / sn0)
        q[3] = (mat[j, k, lanes] - mat[k, j, lanes]) * sh
        q[j, lanes] = (mat[i, j, lanes] + mat[j, i, lanes]) * sh
        q[k, lanes] = (mat[i, k, lanes] + mat[k, i, lanes]) * sh
        positive = tr > np.float32(0.0)
        return [np.where(positive, pos[c], q[c]).astype(np.float32).view(np.uint32).copy() for c in range(4)]


# ---------------------------------------------------------------- sprite renderer info (spec §11.3b)
def sprite_subuv_words(exports, properties):
    """GetSubUVDetails constants: [BlendEnabled, SubImageSize.X, SubImageSize.Y]; absent bSubImageBlend = true (game default)."""
    ref = properties.get('SpriteRenderer')
    renderer = {}
    if ref:
        idx = _export_index(ref)
        if idx is None or not 0 <= idx < len(exports) or exports[idx].get('Type') != 'NiagaraSpriteRendererProperties':
            raise DIError('sprite_renderer_ref')
        renderer = exports[idx].get('Properties') or {}
    size = renderer.get('SubImageSize') or {}
    return [TRUE_WORD if renderer.get('bSubImageBlend', True) else 0,
            int(_f32_words([float(size.get('X', 1.0))])[0]), int(_f32_words([float(size.get('Y', 1.0))])[0])]


# ---------------------------------------------------------------- vector field (spec §11.3f)
_DECODED = {}


def _decoded(vf, mode):
    key = (id(vf.halves), mode)
    if key not in _DECODED:
        _DECODED[key] = (vf.halves, _decode_halves(vf.halves, mode))
    return _DECODED[key][1]


def _decode_halves(h, mode):
    h = np.asarray(h, dtype=np.uint16)
    if mode == 'ieee':
        return h.view(np.float16).astype(np.float32).astype(np.float64)
    if mode == 'fast':
        bits = ((h.astype(np.uint32) & np.uint32(0x8000)) << np.uint32(16)) | \
               (((h.astype(np.uint32) & np.uint32(0x7fff)) << np.uint32(13)) + np.uint32(0x38000000))
        return bits.astype(np.uint32).view(np.float32).astype(np.float64)
    raise DIError('vf_half_decode')


def _fma_vec(a, b, c):
    return np.array([math.fma(float(x), float(y), float(z)) for x, y, z in zip(a, b, c)], dtype=np.float64)


def sample_vector_field(vf, tiling, xyz_words, fma_mode='unfused', half_decode='fast'):
    """VectorField.SampleField, the Win64 ISPC kernel: double precision trilinear sampling of half3 voxels."""
    n = np.asarray(xyz_words[0]).size
    zero = [np.zeros(n, dtype=np.uint32) for _ in range(3)]
    if fma_mode not in ('unfused', 'fused'):
        raise DIError('fma_mode')
    if vf is None or not vf.allow_cpu_access or vf.halves.size == 0 or min(vf.size) <= 0:
        return zero
    size = np.array(vf.size, dtype=np.float64)
    bmin = np.array(vf.bounds_min, dtype=np.float64)
    bsize = np.array(vf.bounds_max, dtype=np.float64) - bmin
    if bsize.min() <= 1e-8:
        return zero
    inv_b = 1.0 / bsize
    inv_s = 1.0 / size
    tile = np.array([1.0 if t else 0.0 for t in tiling], dtype=np.float64)
    count = vf.halves.shape[0]
    with np.errstate(all='ignore'):
        i0, i1, frac = [], [], []
        for c in range(3):
            x = np.asarray(xyz_words[c], dtype=np.uint32).view(np.float32).astype(np.float64)
            scaled = ((x - bmin[c]) * inv_b[c])
            p = _fma_vec(scaled, np.full(n, size[c]), np.full(n, -0.5)) if fma_mode == 'fused' else scaled * size[c] - 0.5
            a = np.floor(p)
            b = a + 1.0
            frac.append(p - a)
            out = []
            for v in (a, b):
                wrapped = v - (tile[c] * np.floor(v * inv_s[c])) * size[c]
                out.append(np.minimum(np.maximum(wrapped, 0.0), size[c] - 1.0))
            i0.append(out[0])
            i1.append(out[1])
        decoded = _decoded(vf, half_decode)

        def corner(dx, dy, dz):
            ix, iy, iz = (i1[0] if dx else i0[0]), (i1[1] if dy else i0[1]), (i1[2] if dz else i0[2])
            lin = (ix + size[0] * iy) + (size[0] * size[1]) * iz
            ok = np.isfinite(lin) & (lin >= 0) & (lin <= count - 1)
            idx = np.where(ok, np.trunc(np.where(ok, lin, 0.0)), 0.0).astype(np.int64)
            return decoded[idx]                                               # (n, 3) doubles

        def lerp(a, b, t):
            t = t[:, None]
            if fma_mode == 'fused':
                d = b - a
                return np.stack([_fma_vec(d[:, c], t[:, 0], a[:, c]) for c in range(3)], axis=1)
            return a + (b - a) * t
        v00 = lerp(corner(0, 0, 0), corner(1, 0, 0), frac[0])
        v10 = lerp(corner(0, 1, 0), corner(1, 1, 0), frac[0])
        v0 = lerp(v00, v10, frac[1])
        v01 = lerp(corner(0, 0, 1), corner(1, 0, 1), frac[0])
        v11 = lerp(corner(0, 1, 1), corner(1, 1, 1), frac[0])
        v1 = lerp(v01, v11, frac[1])
        v = lerp(v0, v1, frac[2])
        return [v[:, c].astype(np.float32).view(np.uint32).copy() for c in range(3)]


def vector_field_from_dump(asset, row):
    """Field data from a plugin vfdump result (exact bit patterns; CPU data = half3 per voxel plus one padding half)."""
    size = tuple(int(row[k]) for k in ('size_x', 'size_y', 'size_z'))
    count = size[0] * size[1] * size[2]
    raw = base64.b64decode(row.get('cpu_data_b64') or '')
    halves = np.frombuffer(raw, dtype='<u2')
    if row.get('has_cpu_data') and halves.size < 3 * count:
        raise DIError('vector_field_cpu_data_size')
    halves = halves[:3 * count].reshape(-1, 3) if row.get('has_cpu_data') else np.zeros((0, 3), dtype=np.uint16)
    return VectorFieldData(asset, size, tuple(_bits_f64(b) for b in row['bounds_min_bits']), tuple(_bits_f64(b) for b in row['bounds_max_bits']),
                           bool(row['allow_cpu_access']), halves.astype(np.uint16))


def load_vector_fields(path):
    """{asset path -> VectorFieldData} from vector_fields.json (written by niagara_gate_d.py vfdump-collect)."""
    with open(path, encoding='utf-8-sig') as handle:
        doc = json.load(handle)
    return {asset: vector_field_from_dump(asset, row) for asset, row in doc['fields'].items()}


def field_asset(ref):
    """'/Niagara/VectorFields/Assets/TilingCurl32.0' + "VectorFieldStatic'TilingCurl32'" -> ('/Niagara/.../TilingCurl32.TilingCurl32', 'VectorFieldStatic')."""
    if not isinstance(ref, dict) or not ref.get('ObjectPath'):
        return None, None
    name = ref.get('ObjectName', '')
    cls, _, obj = name.partition("'")
    return ref['ObjectPath'].rsplit('.', 1)[0] + '.' + obj.rstrip("'"), cls


# ---------------------------------------------------------------- arrays (spec §11.3e)
def array_words(di_class, values):
    """Cooked elements -> uint32 words, shape (Num, E)."""
    _, e, keys, _ = ARRAY_TYPES[di_class]
    values = values or []
    if di_class == 'NiagaraDataInterfaceArrayBool':
        return np.array([[TRUE_WORD if v else 0] for v in values], dtype=np.uint32).reshape(-1, 1)
    rows = [[float(v)] if keys is None else [float(v.get(key, 0.0)) for key in keys] for v in values]
    return _f32_words(np.array(rows, dtype=np.float64).reshape(-1, e))


def array_default_words(di_class):
    _, e, _, default = ARRAY_TYPES[di_class]
    if di_class == 'NiagaraDataInterfaceArrayBool':
        return np.zeros(1, dtype=np.uint32)
    return _f32_words(np.array(default, dtype=np.float64))


class ArrayInstance:
    """Copy-on-write array state (5.7 FReadArrayRef / FWriteArrayRef), shared through DIInstanceState by DI identity."""

    def __init__(self, di_class, properties, identity, state):
        self.di_class, self.identity, self.state = di_class, identity, state
        self.e = ARRAY_TYPES[di_class][1]
        self.cooked = array_words(di_class, properties.get(ARRAY_TYPES[di_class][0]))
        self.default = array_default_words(di_class)

    def data(self):
        return self.state.arrays.get(self.identity, self.cooked)

    def call(self, name, args, n):
        data = self.data()
        num = data.shape[0]
        if name == 'Length':
            return [np.full(n, num, dtype=np.int64).astype(np.int32).view(np.uint32)]
        if name == 'LastIndex':
            return [np.full(n, num - 1, dtype=np.int64).astype(np.int32).view(np.uint32)]
        index = np.asarray(args[1], dtype=np.uint32).view(np.int32).astype(np.int64)
        if name == 'IsValidIndex':
            return [np.where((index >= 0) & (index < num), TRUE_WORD, 0).astype(np.uint32)]
        if name == 'Get':
            if num == 0:
                return [np.full(n, w, dtype=np.uint32) for w in self.default]
            rows = data[np.clip(index, 0, num - 1)]
            return [rows[:, c].copy() for c in range(self.e)]
        if name == 'SetArrayElem':
            skip = np.asarray(args[1], dtype=np.uint32)
            index = np.asarray(args[2], dtype=np.uint32).view(np.int32).astype(np.int64)
            values = np.stack([np.asarray(a, dtype=np.uint32) for a in args[3:3 + self.e]], axis=1)
            if self.di_class == 'NiagaraDataInterfaceArrayBool':
                values = np.where(values != 0, TRUE_WORD, 0).astype(np.uint32)
            target = self.state.arrays.setdefault(self.identity, self.cooked.copy())
            for lane in range(n):
                if skip[lane] == 0 and 0 <= index[lane] < target.shape[0]:
                    target[index[lane]] = values[lane]
            return []
        if name == 'Resize':
            new_num = min(int(np.asarray(args[1], dtype=np.uint32).view(np.int32)[0]), 2 ** 31 - 1)
            if new_num < 0:
                raise DIError('array_resize_negative')
            current = self.state.arrays.get(self.identity, self.cooked)
            if new_num <= current.shape[0]:
                resized = current[:new_num].copy()
            else:
                resized = np.concatenate([current, np.tile(self.default, (new_num - current.shape[0], 1))]).astype(np.uint32)
            self.state.arrays[self.identity] = resized
            return []
        raise DIError('di_unimplemented:%s.%s' % (self.di_class, name))


# ---------------------------------------------------------------- stub and handler (spec §4, §5, §11)
def stub_outputs(fn, args, num_outputs, num_instances):
    """Gate V deterministic stub, vectorized; bit-identical to niagara_runtime._execute57 external_handler='gate_v_stub'."""
    h = np.full(num_instances, (0x811c9dc5 ^ fn) & 0xffffffff, dtype=np.uint32)
    prime = np.uint32(0x01000193)
    with np.errstate(over='ignore'):
        for values in args:
            h = (h ^ np.asarray(values, dtype=np.uint32)) * prime
        outputs = []
        for k in range(num_outputs):
            hk = (h ^ np.uint32((0x9e3779b9 + k) & 0xffffffff)) * prime
            value = ((hk >> np.uint32(9)) | np.uint32(0x3f800000)).view(np.float32) - np.float32(1.0)
            outputs.append(value.astype(np.float32).view(np.uint32).copy())
    return outputs


class ScriptHandler:
    """VM external handler (spec §4). impls[fn] is a CurveLUT (sampled) or a callable(args, n, start) -> outputs; other fn -> stub."""

    def __init__(self, curves, num_outputs, *, fma_mode='unfused', log_stub_calls=False, unimplemented_functions=()):
        if fma_mode not in ('unfused', 'fused'):
            raise DIError('fma_mode')
        self.curves = dict(curves)
        self.num_outputs = dict(num_outputs)
        self.fma_mode = fma_mode
        self.log_stub_calls = log_stub_calls
        self.unimplemented_functions = sorted(unimplemented_functions)
        self.stub_calls = []

    def __call__(self, fn, args, num_instances, start_instance):
        impl = self.curves.get(fn)
        if isinstance(impl, CurveLUT):
            if len(args) != 1:
                raise DIError('di_signature')
            return sample_curve(impl, args[0], self.fma_mode)
        if impl is not None:
            return impl(args, num_instances, start_instance)
        outputs = stub_outputs(fn, args, self.num_outputs[fn], num_instances)
        if self.log_stub_calls:
            inputs = np.stack([np.asarray(a, dtype=np.uint32) for a in args], axis=1).reshape(-1) if args else np.zeros(0, dtype=np.uint32)
            self.stub_calls.append({'fn': fn, 'start': start_instance, 'count': num_instances, 'inputs': [int(v) for v in inputs]})
        return outputs


def _signature(di_class, name):
    """(inputs, outputs) per spec §11.2, user pointer included."""
    if di_class == PARTICLE_READ:
        if name in PR_READS:
            t, key = PR_READS[name]
            nf, ni, _ = PR_TYPES[t]
            return (2 if key == 'Index' else 3), 1 + nf + ni
        return {'Get Num Particles': (1, 1), 'Get Particle Index': (3, 1), 'Get Particle Index From ID Table': (2, 2)}[name]
    if (di_class, name) in CURVE_ELEMENTS:
        return 1, CURVE_ELEMENTS[di_class, name]
    if di_class in ARRAY_TYPES:
        e = ARRAY_TYPES[di_class][1]
        return {'Length': (1, 1), 'LastIndex': (1, 1), 'IsValidIndex': (2, 1), 'Get': (2, e), 'SetArrayElem': (3 + e, 0), 'Resize': (2, 0)}[name]
    return {(FAST_PATH, 'FastMatrixToQuaternion'): (16, 4), (SPRITE_INFO, 'GetSubUVDetails'): (0, 3), (EMITTER_PROPERTIES, 'SetFixedBounds'): (9, 0),
            (AUDIO_PLAYER, 'PlayAudioAtLocation'): (12, 1), (VECTOR_FIELD, 'SampleField'): (3, 3), (ARRAY_MESH, 'Length'): (1, 1),
            (CAMERA, 'GetCameraPropertiesCPU/GPU'): (1, 12), (COLLISION, 'PerformCollisionQueryAsyncCPU'): (11, 12)}[di_class, name]


def _constant(words):
    return lambda args, n, start: [np.full(n, w, dtype=np.uint32) for w in words]


def _impl(b, exports, state, vector_fields, vf_fma_mode, vf_half_decode):
    props = exports[b.di_export].get('Properties') or {} if b.kind == 'di' else {}
    cls = b.di_class if b.kind == 'di' else FAST_PATH
    if (cls, b.name) in CURVE_ELEMENTS:
        return curve_from_properties(cls, props)
    if cls == FAST_PATH:
        return lambda args, n, start: fast_matrix_to_quaternion(args)
    if cls == SPRITE_INFO:
        return _constant(sprite_subuv_words(exports, props))
    if cls == EMITTER_PROPERTIES:
        def bounds(args, n, start):
            execute = np.asarray(args[1], dtype=np.uint32) != 0
            state.bounds_log.append({'fn': b.fn_index, 'identity': list(b.identity), 'lanes': int(execute.sum())})
            return []
        return bounds
    if cls == AUDIO_PLAYER:
        valid = bool(props.get('SoundToPlay'))

        def play(args, n, start):
            success = (np.asarray(args[1], dtype=np.uint32) != 0) & valid
            state.audio_log.append({'fn': b.fn_index, 'identity': list(b.identity), 'plays': int(success.sum())})
            return [np.where(success, TRUE_WORD, 0).astype(np.uint32)]
        return play
    if cls == ARRAY_MESH:
        return _constant([len(props.get('MeshData') or [])])
    if cls == CAMERA:
        return lambda args, n, start: [np.full(n, w, dtype=np.uint32) for w in camera_properties_words(state.camera)]
    if cls == PARTICLE_READ:
        return particle_read_impl(b, props, state)
    if cls == COLLISION:
        batch = state.collision_batches.setdefault(tuple(b.identity), CollisionBatch())

        def query(args, n, start):
            out = collision_query_async(batch, args, n)
            state.collision_log.append({'fn': b.fn_index, 'lanes': n, 'valid': int((out[1] != 0).sum())})
            return out
        return query
    if cls in ARRAY_TYPES:
        array = ArrayInstance(cls, props, b.identity, state)
        return lambda args, n, start: array.call(b.name, args, n)
    if cls == VECTOR_FIELD:
        asset, field_class = field_asset(props.get('Field'))
        tiling = (bool(props.get('bTileX', False)), bool(props.get('bTileY', False)), bool(props.get('bTileZ', False)))
        vf = None
        if asset is not None and field_class == 'VectorFieldStatic':
            if asset not in vector_fields:
                raise DIError('vector_field_data_missing:' + asset)
            vf = vector_fields[asset]
        return lambda args, n, start: sample_vector_field(vf, tiling, args, vf_fma_mode, vf_half_decode)
    raise DIError('di_unimplemented:%s.%s' % (cls, b.name))


def script_handler(exports, bindings, *, fma_mode='unfused', unimplemented='error', log_stub_calls=False, state=None,
                   vector_fields=None, vf_fma_mode='unfused', vf_half_decode='fast'):
    """Spec §5/§11.5: implemented bindings get real DIs; others raise (unimplemented='error') or use the stub ('stub')."""
    if unimplemented not in ('error', 'stub'):
        raise DIError('unimplemented')
    state = DIInstanceState() if state is None else state
    vector_fields = {} if vector_fields is None else vector_fields
    missing = [b for b in bindings if (b.di_class if b.kind == 'di' else FAST_PATH, b.name) not in IMPLEMENTED]
    if missing and unimplemented == 'error':
        raise DIError('di_unimplemented:' + min(missing, key=lambda b: b.fn_index).label)
    impls = {}
    for b in bindings:
        key = (b.di_class if b.kind == 'di' else FAST_PATH, b.name)
        if key not in IMPLEMENTED:
            continue
        if (b.num_inputs, b.num_outputs) != _signature(*key) or (b.specifiers and key[0] != PARTICLE_READ) or b.variadic != (0, 0):
            raise DIError('di_signature')
        impls[b.fn_index] = _impl(b, exports, state, vector_fields, vf_fma_mode, vf_half_decode)
    handler = ScriptHandler(impls, {b.fn_index: b.num_outputs for b in bindings}, fma_mode=fma_mode, log_stub_calls=log_stub_calls,
                            unimplemented_functions={b.label for b in missing})
    handler.state = state
    return handler


# ---------------------------------------------------------------- Gate D / D2 case forms
def case_di(binding, exports):
    """The `di` object a Gate D/D2 case carries for an oracle-checkable binding, or None."""
    cls = binding.di_class if binding.kind == 'di' else FAST_PATH
    props = exports[binding.di_export].get('Properties') or {} if binding.kind == 'di' else {}
    if (cls, binding.name) in CURVE_ELEMENTS:
        return curve_words(curve_from_properties(cls, props), binding.name)
    if (cls, binding.name) == (FAST_PATH, 'FastMatrixToQuaternion'):
        return {'class': FAST_PATH, 'function': binding.name}
    if (cls, binding.name) == (SPRITE_INFO, 'GetSubUVDetails'):
        ref = props.get('SpriteRenderer')
        renderer = exports[_export_index(ref)].get('Properties') or {} if ref else {}
        size = renderer.get('SubImageSize') or {}

        def bits(value):
            return format(int(np.array([float(value)], dtype=np.float64).view(np.uint64)[0]), '016X')
        return {'class': SPRITE_INFO, 'function': binding.name, 'has_renderer': bool(ref),
                'sub_image_blend': bool(renderer.get('bSubImageBlend', True)),
                'sub_image_size_x_bits': bits(size.get('X', 1.0)), 'sub_image_size_y_bits': bits(size.get('Y', 1.0))}
    if (cls, binding.name) == (VECTOR_FIELD, 'SampleField'):
        asset, field_class = field_asset(props.get('Field'))
        return {'class': VECTOR_FIELD, 'function': binding.name, 'field': asset if field_class == 'VectorFieldStatic' else '',
                'tile_x': bool(props.get('bTileX', False)), 'tile_y': bool(props.get('bTileY', False)), 'tile_z': bool(props.get('bTileZ', False))}
    return None


def case_handler(ext_functions, *, fma_mode='unfused', vector_fields=None, vf_fma_mode='unfused', vf_half_decode='fast', log_stub_calls=True):
    """Handler for a Gate D/D2 case: functions with `di` are real, every other function is the Gate V stub."""
    vector_fields = {} if vector_fields is None else vector_fields
    impls = {}
    for k, f in enumerate(ext_functions):
        di = f.get('di')
        if di is None:
            continue
        cls = di['class']
        if (cls, di['function']) in CURVE_ELEMENTS:
            impls[k] = curve_from_words(di)
        elif cls == FAST_PATH:
            impls[k] = lambda args, n, start: fast_matrix_to_quaternion(args)
        elif cls == SPRITE_INFO:
            if di['has_renderer']:
                words = [TRUE_WORD if di['sub_image_blend'] else 0,
                         int(_f32_words([_bits_f64(di['sub_image_size_x_bits'])])[0]), int(_f32_words([_bits_f64(di['sub_image_size_y_bits'])])[0])]
            else:
                words = [TRUE_WORD, int(_f32_words([1.0])[0]), int(_f32_words([1.0])[0])]
            impls[k] = _constant(words)
        elif cls == VECTOR_FIELD:
            vf = None
            if di['field']:
                if di['field'] not in vector_fields:
                    raise DIError('vector_field_data_missing:' + di['field'])
                vf = vector_fields[di['field']]
            tiling = (di['tile_x'], di['tile_y'], di['tile_z'])
            impls[k] = (lambda vf_, t_: (lambda args, n, start: sample_vector_field(vf_, t_, args, vf_fma_mode, vf_half_decode)))(vf, tiling)
        else:
            raise DIError('di_not_case_checkable:' + cls)
    return ScriptHandler(impls, {k: f['num_outputs'] for k, f in enumerate(ext_functions)}, fma_mode=fma_mode, log_stub_calls=log_stub_calls)
