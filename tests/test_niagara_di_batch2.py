"""Wave N5 (spec N-O3 §11): fast path, sprite renderer info, emitter properties, audio player, arrays, vector field."""
import base64
import json
import math
import os
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # repo root
from Niagara import niagara as di

CORPUS = os.environ.get('DR_NIAGARA_CORPUS')
SHIM = os.environ.get('DR_SVML_SHIM')
VECTOR_FIELDS = os.environ.get('DR_NIAGARA_VECTOR_FIELDS')
T = 0xffffffff


def fw(value):
    return struct.unpack('<I', struct.pack('<f', value))[0]


def wf(word):
    return struct.unpack('<f', struct.pack('<I', int(word) & 0xffffffff))[0]


def iw(value):
    return value & 0xffffffff


def ref_quaternion(m):
    """Independent scalar transcription of the 5.3/5.7 CPU kernel (float32 per operation)."""
    f = np.float32
    M = [[f(m[4 * r + c]) for c in range(4)] for r in range(4)]
    tr = (M[0][0] + M[1][1]) + M[2][2]
    with np.errstate(all='ignore'):
        if tr > f(0):
            inv = f(1) / np.sqrt(tr + f(1))
            s = f(0.5) * inv
            return [(M[1][2] - M[2][1]) * s, (M[2][0] - M[0][2]) * s, (M[0][1] - M[1][0]) * s, f(0.5) * (f(1) / inv)]
        i = 1 if M[1][1] > M[0][0] else 0
        if M[2][2] > M[i][i]:
            i = 2
        j, k = (i + 1) % 3, (i + 2) % 3
        s = ((M[i][i] - M[j][j]) - M[k][k]) + f(1)
        inv = f(1) / np.sqrt(s)
        q = [f(0)] * 4
        q[i] = f(0.5) * (f(1) / inv)
        s = f(0.5) * inv
        q[3] = (M[j][k] - M[k][j]) * s
        q[j] = (M[i][j] + M[j][i]) * s
        q[k] = (M[i][k] + M[k][i]) * s
        return q


class FastPathTests(unittest.TestCase):
    def quat(self, rows):
        flat = [v for row in rows for v in row]
        return [o.tolist() for o in di.fast_matrix_to_quaternion([np.array([fw(v)], np.uint32) for v in flat])]

    def test_exact_cases(self):
        I = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        self.assertEqual(self.quat(I), [[0], [0], [0], [fw(1.0)]])
        self.assertEqual(self.quat([[1, 0, 0, 0], [0, -1, 0, 0], [0, 0, -1, 0], [0, 0, 0, 1]]), [[fw(1.0)], [0], [0], [0]])
        self.assertEqual(self.quat([[-1, 0, 0, 0], [0, 1, 0, 0], [0, 0, -1, 0], [0, 0, 0, 1]]), [[0], [fw(1.0)], [0], [0]])
        z90 = [[0, 1, 0, 0], [-1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        self.assertEqual(self.quat(z90), [[fw(float(v))] for v in ref_quaternion([v for row in z90 for v in row])])

    def test_random_against_scalar(self):
        rng = np.random.default_rng(11)
        mats = rng.uniform(-2, 2, (200, 16)).astype(np.float32)
        mats[:20, [0, 5, 10]] = np.abs(mats[:20, [0, 5, 10]]) + 1      # trace > 0 lanes
        mats[20:40, [0, 5, 10]] = -np.abs(mats[20:40, [0, 5, 10]]) - 1  # trace < 0 lanes
        got = di.fast_matrix_to_quaternion([mats[:, j].view(np.uint32) for j in range(16)])
        for lane in range(200):
            want = [np.array([v], np.float32).view(np.uint32)[0] for v in ref_quaternion(mats[lane].tolist())]
            have = [got[c][lane] for c in range(4)]
            same = all(a == b or (math.isnan(wf(a)) and math.isnan(wf(b))) for a, b in zip(have, want))
            self.assertTrue(same, (lane, have, want))


class SpriteInfoTests(unittest.TestCase):
    def exports(self, renderer_props, renderer_type='NiagaraSpriteRendererProperties'):
        return [{'Type': di.di_SPRITE_INFO, 'Properties': {'SpriteRenderer': {'ObjectPath': '/Game/X.1'}}},
                {'Type': renderer_type, 'Properties': renderer_props}]

    def test_values_and_defaults(self):
        ex = self.exports({'SubImageSize': {'X': 8.0, 'Y': 4.0}, 'bSubImageBlend': False})
        self.assertEqual(di.sprite_subuv_words(ex, ex[0]['Properties']), [0, fw(8.0), fw(4.0)])
        ex = self.exports({})
        self.assertEqual(di.sprite_subuv_words(ex, ex[0]['Properties']), [T, fw(1.0), fw(1.0)])
        self.assertEqual(di.sprite_subuv_words(ex, {}), [T, fw(1.0), fw(1.0)])
        ex = self.exports({}, renderer_type='NiagaraMeshRendererProperties')
        with self.assertRaisesRegex(di.DIError, 'sprite_renderer_ref'):
            di.sprite_subuv_words(ex, ex[0]['Properties'])


def binding(fn, cls, name, nin, nout, export, owner='Emitter.X'):
    return di.Binding(fn, owner, name, nin, nout, (), (0, 0), 'di', cls, export, 0)


class ArrayTests(unittest.TestCase):
    def test_reads(self):
        state = di.DIInstanceState()
        a = di.ArrayInstance('NiagaraDataInterfaceArrayFloat3', {'InternalFloatData': [{'X': 1, 'Y': 2, 'Z': 3}, {'X': 4, 'Y': 5, 'Z': 6}]}, ('export', 1), state)
        u = np.zeros(4, np.uint32)
        idx = np.array([iw(-5), 0, 1, 7], np.uint32)
        self.assertEqual([o.tolist() for o in a.call('Get', [u, idx], 4)],
                         [[fw(1), fw(1), fw(4), fw(4)], [fw(2), fw(2), fw(5), fw(5)], [fw(3), fw(3), fw(6), fw(6)]])
        self.assertEqual(a.call('Length', [u], 2)[0].tolist(), [2, 2])
        self.assertEqual(a.call('LastIndex', [u], 1)[0].tolist(), [1])
        self.assertEqual(a.call('IsValidIndex', [u, np.array([iw(-1), 0, 1, 2], np.uint32)], 4)[0].tolist(), [0, T, T, 0])
        c = di.ArrayInstance('NiagaraDataInterfaceArrayColor', {}, ('export', 2), state)
        self.assertEqual([o.tolist() for o in c.call('Get', [u[:2], np.zeros(2, np.uint32)], 2)], [[fw(1.0)] * 2] * 4)
        self.assertEqual(c.call('LastIndex', [u[:1]], 1)[0].tolist(), [T])
        b = di.ArrayInstance('NiagaraDataInterfaceArrayBool', {'BoolData': [True, False]}, ('export', 3), state)
        self.assertEqual(b.call('Get', [u[:2], np.array([0, 1], np.uint32)], 2)[0].tolist(), [T, 0])

    def test_writes_copy_on_write_and_identity(self):
        state = di.DIInstanceState()
        props = {'FloatData': [1.0, 2.0, 3.0]}
        a = di.ArrayInstance('NiagaraDataInterfaceArrayFloat', props, ('user', 'User.A'), state)
        b = di.ArrayInstance('NiagaraDataInterfaceArrayFloat', props, ('user', 'User.A'), state)
        other = di.ArrayInstance('NiagaraDataInterfaceArrayFloat', props, ('export', 9), state)
        u = np.zeros(4, np.uint32)
        skip = np.array([0, 0, T, 0], np.uint32)
        index = np.array([1, 1, 0, 5], np.uint32)
        value = np.array([fw(10.0), fw(20.0), fw(30.0), fw(40.0)], np.uint32)
        self.assertEqual(a.call('SetArrayElem', [u, skip, index, value], 4), [])
        self.assertEqual(b.call('Get', [u[:3], np.array([0, 1, 2], np.uint32)], 3)[0].tolist(), [fw(1.0), fw(20.0), fw(3.0)])
        self.assertEqual(other.call('Get', [u[:1], np.array([1], np.uint32)], 1)[0].tolist(), [fw(2.0)])
        self.assertEqual(a.cooked[:, 0].tolist(), [fw(1.0), fw(2.0), fw(3.0)])
        a.call('Resize', [u[:1], np.array([5], np.uint32)], 1)
        self.assertEqual(b.call('Get', [u[:2], np.array([4, 2], np.uint32)], 2)[0].tolist(), [0, fw(3.0)])
        a.call('Resize', [u[:1], np.array([1], np.uint32)], 1)
        self.assertEqual(b.call('Length', [u[:1]], 1)[0].tolist(), [1])
        with self.assertRaisesRegex(di.DIError, 'array_resize_negative'):
            a.call('Resize', [u[:1], np.array([iw(-1)], np.uint32)], 1)
        flags = di.ArrayInstance('NiagaraDataInterfaceArrayBool', {'BoolData': [False]}, ('export', 4), state)
        flags.call('SetArrayElem', [u[:1], np.zeros(1, np.uint32), np.zeros(1, np.uint32), np.array([7], np.uint32)], 1)
        self.assertEqual(flags.call('Get', [u[:1], np.zeros(1, np.uint32)], 1)[0].tolist(), [T])


class HandlerTests(unittest.TestCase):
    def test_side_effect_functions_and_signatures(self):
        exports = [{'Type': 'NiagaraScript', 'Properties': {}},
                   {'Type': di.EMITTER_PROPERTIES, 'Properties': {'EmitterBinding': {'EmitterName': 'E'}}},
                   {'Type': di.AUDIO_PLAYER, 'Properties': {'SoundToPlay': {'ObjectPath': '/Game/S.0'}}},
                   {'Type': di.AUDIO_PLAYER, 'Properties': {}},
                   {'Type': 'NiagaraDataInterfaceArrayFloat', 'Properties': {'FloatData': [5.0]}},
                   {'Type': 'NiagaraDataInterfaceCamera', 'Properties': {}}]
        bs = [binding(0, di.EMITTER_PROPERTIES, 'SetFixedBounds', 9, 0, 1), binding(1, di.AUDIO_PLAYER, 'PlayAudioAtLocation', 12, 1, 2),
              binding(2, di.AUDIO_PLAYER, 'PlayAudioAtLocation', 12, 1, 3), binding(3, 'NiagaraDataInterfaceArrayFloat', 'Get', 2, 1, 4)]
        h = di.script_handler(exports, bs)
        z = np.zeros(3, np.uint32)
        play = np.array([T, 0, 1], np.uint32)
        self.assertEqual(h(0, [z, np.array([T, 0, T], np.uint32)] + [z] * 7, 3, 0), [])
        self.assertEqual(h(1, [z, play] + [z] * 10, 3, 0)[0].tolist(), [T, 0, T])
        self.assertEqual(h(2, [z, play] + [z] * 10, 3, 0)[0].tolist(), [0, 0, 0])
        self.assertEqual(h(3, [z, z], 3, 0)[0].tolist(), [fw(5.0)] * 3)
        self.assertEqual((h.state.bounds_log[0]['lanes'], [e['plays'] for e in h.state.audio_log]), (2, [2, 0]))
        with self.assertRaisesRegex(di.DIError, 'di_signature'):
            di.script_handler(exports, [binding(0, 'NiagaraDataInterfaceArrayFloat', 'Get', 1, 1, 4)])
        with self.assertRaisesRegex(di.DIError, 'di_unimplemented:NiagaraDataInterfaceLandscape.GetHeight'):
            di.script_handler(exports, [binding(0, 'NiagaraDataInterfaceLandscape', 'GetHeight', 2, 1, 5)])


def synthetic_field(halves=None, allow=True, size=(2, 3, 4), bmin=(-10.0, 0.0, 5.0), bmax=(10.0, 30.0, 45.0)):
    count = size[0] * size[1] * size[2]
    if halves is None:
        values = np.array([[(x + 2 * y + 6 * z) / 32.0, -(x + 1) / 8.0, (z + 1) / 4.0]
                           for z in range(size[2]) for y in range(size[1]) for x in range(size[0])], dtype=np.float16)
        halves = values.view(np.uint16)
    return di.VectorFieldData('/Test/F.F', size, bmin, bmax, allow, np.asarray(halves, np.uint16).reshape(count, 3))


def ref_vector_field(vf, tiling, p, decode='fast'):
    """Independent scalar transcription of spec §11.3(f) (unfused)."""
    def half(h):
        if decode == 'ieee':
            return float(np.array([h], np.uint16).view(np.float16)[0])
        bits = ((h & 0x8000) << 16) | (((h & 0x7fff) << 13) + 0x38000000)
        return wf(bits)
    size = [float(s) for s in vf.size]
    idx0, idx1, frac = [], [], []
    for c in range(3):
        q = ((float(np.float32(p[c])) - vf.bounds_min[c]) * (1.0 / (vf.bounds_max[c] - vf.bounds_min[c]))) * size[c] - 0.5
        a = math.floor(q)
        pair = []
        for v in (a, a + 1.0):
            v = v - (float(tiling[c]) * math.floor(v * (1.0 / size[c]))) * size[c]
            pair.append(min(max(v, 0.0), size[c] - 1.0))
        idx0.append(pair[0]); idx1.append(pair[1]); frac.append(q - a)
    count = vf.halves.shape[0]

    def voxel(dx, dy, dz):
        x, y, z = (idx1[0] if dx else idx0[0]), (idx1[1] if dy else idx0[1]), (idx1[2] if dz else idx0[2])
        lin = (x + size[0] * y) + (size[0] * size[1]) * z
        n = int(lin) if 0 <= lin <= count - 1 else 0
        return [half(int(h)) for h in vf.halves[n]]

    def lerp(a, b, t):
        return [x + (y - x) * t for x, y in zip(a, b)]
    v0 = lerp(lerp(voxel(0, 0, 0), voxel(1, 0, 0), frac[0]), lerp(voxel(0, 1, 0), voxel(1, 1, 0), frac[0]), frac[1])
    v1 = lerp(lerp(voxel(0, 0, 1), voxel(1, 0, 1), frac[0]), lerp(voxel(0, 1, 1), voxel(1, 1, 1), frac[0]), frac[1])
    return [fw(float(np.float32(v))) for v in lerp(v0, v1, frac[2])]


class VectorFieldTests(unittest.TestCase):
    def sample(self, vf, tiling, points, **kw):
        cols = [np.array([fw(p[c]) for p in points], np.uint32) for c in range(3)]
        out = di.sample_vector_field(vf, tiling, cols, **kw)
        return [[int(out[c][i]) for c in range(3)] for i in range(len(points))]

    def test_against_scalar_reference(self):
        vf = synthetic_field()
        rng = np.random.default_rng(3)
        points = [(-5.0, 5.0, 10.0), (0.0, 15.0, 25.0), (12.0, -3.0, 50.0), (-10.0, 0.0, 5.0), (9.9, 29.9, 44.9)] + \
                 [tuple(rng.uniform([-15, -5, 0], [15, 35, 50])) for _ in range(60)]
        for tiling in [(False, False, False), (True, False, True), (True, True, True)]:
            for decode in ('fast', 'ieee'):
                got = self.sample(vf, tiling, points, half_decode=decode)
                want = [ref_vector_field(vf, tiling, p, decode) for p in points]
                self.assertEqual(got, want, (tiling, decode))

    def test_fallbacks_and_half_decode(self):
        vf = synthetic_field()
        self.assertEqual(self.sample(synthetic_field(allow=False), (False,) * 3, [(0.0, 0.0, 0.0)]), [[0, 0, 0]])
        self.assertEqual(self.sample(None, (False,) * 3, [(0.0, 0.0, 0.0)]), [[0, 0, 0]])
        self.assertEqual(self.sample(synthetic_field(bmax=(-10.0, 30.0, 45.0)), (False,) * 3, [(0.0, 0.0, 0.0)]), [[0, 0, 0]])
        zero = synthetic_field(halves=np.zeros((24, 3), np.uint16))
        self.assertEqual(self.sample(zero, (False,) * 3, [(0.0, 15.0, 25.0)], half_decode='ieee'), [[0, 0, 0]])
        self.assertEqual(self.sample(zero, (False,) * 3, [(0.0, 15.0, 25.0)], half_decode='fast'), [[fw(2.0 ** -15)] * 3])
        nan = self.sample(vf, (False,) * 3, [(float('nan'), 15.0, 25.0)])
        self.assertTrue(all(math.isnan(wf(w)) for w in nan[0]))

    def test_dump_round_trip(self):
        vf = synthetic_field()
        def bits(v):
            return format(int(np.array([v], np.float64).view(np.uint64)[0]), '016X')
        raw = np.concatenate([vf.halves.reshape(-1), np.zeros(1, np.uint16)]).astype('<u2').tobytes()
        row = {'size_x': 2, 'size_y': 3, 'size_z': 4, 'bounds_min_bits': [bits(v) for v in vf.bounds_min],
               'bounds_max_bits': [bits(v) for v in vf.bounds_max], 'intensity_bits': '3F800000', 'allow_cpu_access': True,
               'has_cpu_data': True, 'cpu_data_b64': base64.b64encode(raw).decode('ascii'), 'cpu_data_halves': 73}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'vf.json'
            path.write_text(json.dumps({'schema': 'dr.niagara-vector-fields/1', 'fields': {'/Test/F.F': row}}), encoding='utf-8')
            loaded = di.load_vector_fields(str(path))['/Test/F.F']
        self.assertEqual((loaded.size, loaded.bounds_min, loaded.bounds_max, loaded.allow_cpu_access), (vf.size, vf.bounds_min, vf.bounds_max, True))
        self.assertTrue(np.array_equal(loaded.halves, vf.halves))
        self.assertEqual(di.field_asset({'ObjectPath': '/Niagara/VectorFields/Assets/TilingCurl32.0', 'ObjectName': "VectorFieldStatic'TilingCurl32'"}),
                         ('/Niagara/VectorFields/Assets/TilingCurl32.TilingCurl32', 'VectorFieldStatic'))

    def test_case_handler(self):
        vf = synthetic_field()
        ext = [{'num_inputs': 3, 'num_outputs': 3, 'di': {'class': di.di_VECTOR_FIELD, 'function': 'SampleField', 'field': '/Test/F.F',
                                                           'tile_x': True, 'tile_y': False, 'tile_z': False}},
               {'num_inputs': 0, 'num_outputs': 3, 'di': {'class': di.di_SPRITE_INFO, 'function': 'GetSubUVDetails', 'has_renderer': False,
                                                           'sub_image_blend': False, 'sub_image_size_x_bits': '4020000000000000', 'sub_image_size_y_bits': '4020000000000000'}},
               {'num_inputs': 1, 'num_outputs': 2}]
        h = di.case_handler(ext, vector_fields={'/Test/F.F': vf})
        p = [np.array([fw(13.0)], np.uint32), np.array([fw(15.0)], np.uint32), np.array([fw(25.0)], np.uint32)]
        self.assertEqual([int(o[0]) for o in h(0, p, 1, 0)], ref_vector_field(vf, (True, False, False), (13.0, 15.0, 25.0)))
        self.assertEqual([int(o[0]) for o in h(1, [], 1, 0)], [T, fw(1.0), fw(1.0)])
        self.assertEqual(len(h(2, [np.zeros(1, np.uint32)], 1, 0)), 2)
        with self.assertRaisesRegex(di.DIError, 'vector_field_data_missing'):
            di.case_handler(ext)


@unittest.skipUnless(CORPUS and SHIM and VECTOR_FIELDS, 'DR_NIAGARA_CORPUS, DR_SVML_SHIM or DR_NIAGARA_VECTOR_FIELDS unset')
class CorpusVectorFieldTests(unittest.TestCase):
    def test_vector_field_system_bakes(self):
        from Niagara.niagara import load_system
        from Niagara.niagara import BakeError, BakeInputs, Scheduler, bake
        model = load_system(next(Path(CORPUS).rglob('NS_Augment_HealingCloud.json')))
        inputs = BakeInputs(ticks=60, di_mode='real', svml_shim_path=SHIM, vector_fields=VECTOR_FIELDS)
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(bake(model, inputs, Path(temp) / 'a'), bake(model, inputs, Path(temp) / 'b'))
            manifest = json.loads((Path(temp) / 'a' / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['di_stubbed_functions'], [])
        with self.assertRaisesRegex(BakeError, 'vector_field_data_missing:/Niagara/VectorFields/Assets/TilingCurl'):
            Scheduler(model, BakeInputs(ticks=1, di_mode='real', svml_shim_path=SHIM))


if __name__ == '__main__':
    unittest.main()
