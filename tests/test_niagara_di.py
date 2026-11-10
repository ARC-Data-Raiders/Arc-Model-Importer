"""Wave N4: data-interface binding, curve sampling (fixed supervisor vectors), stub equivalence and the VM handler path."""
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # repo root
from Niagara import niagara as di
from Niagara.niagara import ExecutionError, execute
from Niagara.niagara import OPCODES
from Niagara.niagara import execute_np

IDS = {name: code for code, name in OPCODES.items()}
FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'niagara_curve_vectors.json'
CORPUS = os.environ.get('DR_NIAGARA_CORPUS')
SHIM = os.environ.get('DR_SVML_SHIM')


def prog(*parts):
    return b''.join(parts) + b'\0'


def fw(value):
    return struct.unpack('<I', struct.pack('<f', value))[0]


STUB_SIGS = [{'InputParamLocations': [], 'NumOutputs': 0} for _ in range(3)]
STUB_SIGS[2] = {'InputParamLocations': [0, 0], 'NumOutputs': 2}
STUB_PROGRAM = prog(bytes([IDS['external_func_call'], 2]) + struct.pack('<HHHH', 0, 4, 10, 11))
TABLES = [struct.pack('<II', 0x3f800000, 7)]


def run_backend(backend, code, **kwargs):
    if backend == 'scalar':
        return execute(code, profile='ue5.3-legacy', x86_semantics=True, **kwargs)
    return execute_np(code, profile='ue5.3-legacy', **kwargs)


class CurveTests(unittest.TestCase):
    def test_vectors(self):
        vectors = json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.assertEqual(len(vectors), 19)
        for v in vectors:
            curve = di.curve_from_properties(v['class'], v['properties'])
            self.assertEqual([[int(w) for w in out] for out in di.sample_curve(curve, v['x'])], v['expected_unfused'], v['source'])
            again = di.curve_from_words(di.curve_words(curve, v['function']))
            self.assertEqual([[int(w) for w in out] for out in di.sample_curve(again, v['x'])], v['expected_unfused'], v['source'])

    def test_defaults_and_errors(self):
        curve = di.curve_from_properties('NiagaraDataInterfaceColorCurve', {})
        self.assertEqual((curve.e, curve.lut.tolist(), float(curve.min_time), float(curve.max_time), float(curve.inv_time_range),
                          float(curve.num_samples_minus_one)), (4, [0.0] * 4, 0.0, 1.0, 1.0, 0.0))
        self.assertEqual([o.tolist() for o in di.sample_curve(curve, [0x7fc00000, 0x3f000000])], [[0, 0]] * 4)
        for props, message in [({'bUseLUT': False}, 'curve_without_lut'), ({'ShaderLUT': [1.0, 2.0]}, 'curve_lut_size'),
                               ({'ShaderLUT': [1.0, 2.0], 'LUTNumSamplesMinusOne': 2.0}, 'curve_lut_size')]:
            with self.assertRaisesRegex(di.DIError, message):
                di.curve_from_properties('NiagaraDataInterfaceCurve', props)
        with self.assertRaisesRegex(di.DIError, 'di_not_curve'):
            di.curve_from_properties('NiagaraDataInterfaceCamera', {})
        with self.assertRaisesRegex(di.DIError, 'fma_mode'):
            di.sample_curve(curve, [0], 'bogus')

    def test_edges(self):
        curve = di.curve_from_properties('NiagaraDataInterfaceCurve', {'ShaderLUT': [2.0, 4.0, 8.0], 'LUTNumSamplesMinusOne': 2.0,
                                                                      'LUTMinTime': -1.0, 'LUTMaxTime': 1.0, 'LUTInvTimeRange': 0.5})
        x = [fw(-1.0), fw(1.0), fw(0.0), fw(-0.0), fw(float('inf')), fw(float('-inf')), 0x7fc00000, fw(-5.0), fw(5.0), fw(0.5)]
        self.assertEqual(di.sample_curve(curve, x)[0].tolist(), [fw(v) for v in (2.0, 8.0, 4.0, 4.0, 8.0, 2.0, 8.0, 2.0, 8.0, 6.0)])

    def test_fused_mode(self):
        curve = di.curve_from_properties('NiagaraDataInterfaceCurve', {'ShaderLUT': [0.0, 1.0, 0.25], 'LUTNumSamplesMinusOne': 2.0})
        x = [fw(v) for v in (0.0, 0.3, 0.5, 0.9, 1.0)]
        fused, unfused = di.sample_curve(curve, x, 'fused')[0].tolist(), di.sample_curve(curve, x)[0].tolist()
        self.assertEqual((fused[0], fused[2], fused[4]), (unfused[0], unfused[2], unfused[4]))


class StubAndHandlerTests(unittest.TestCase):
    def test_stub_outputs_match_vm_stub(self):
        self.assertEqual([o.tolist() for o in di.stub_outputs(2, [np.array([0x3f800000], np.uint32), np.array([7], np.uint32)], 2, 1)],
                         [[0x3dc5a370], [0x3dada350]])
        self.assertEqual([o.tolist() for o in di.stub_outputs(0, [], 1, 1)], [[0x3f0e18ae]])
        rng = np.random.default_rng(5)
        args = [rng.integers(0, 2 ** 32, 37, dtype=np.uint64).astype(np.uint32) for _ in range(3)]
        sigs = [{'InputParamLocations': [0, 0, 0], 'NumOutputs': 4}]
        code = prog(bytes([IDS['external_func_call'], 0]) + struct.pack('<HHHHHHH', 0x8000, 0x8001, 0x8002, 10, 11, 12, 13))
        r = execute(code, num_instances=37, initial_registers={i: [int(v) for v in a] for i, a in enumerate(args)},
                    external_functions=sigs, x86_semantics=True, external_handler='gate_v_stub')
        self.assertEqual([o.tolist() for o in di.stub_outputs(0, args, 4, 37)], [list(r.registers[10 + k]) for k in range(4)])

    def test_handler_stub_equivalence(self):
        ref = execute(STUB_PROGRAM, num_instances=1, constant_tables=TABLES, external_functions=STUB_SIGS, x86_semantics=True,
                      external_handler='gate_v_stub')
        for backend in ('scalar', 'numpy'):
            handler = di.ScriptHandler({}, {k: s['NumOutputs'] for k, s in enumerate(STUB_SIGS)}, log_stub_calls=True)
            r = run_backend(backend, STUB_PROGRAM, num_instances=1, constant_tables=TABLES, external_functions=STUB_SIGS, external_handler=handler)
            self.assertEqual([int(r.registers[10][0]), int(r.registers[11][0])], [ref.registers[10][0], ref.registers[11][0]], backend)
            self.assertEqual(r.ext_calls, [{'fn': 2, 'start': 0, 'count': 1, 'input_taints': [[], []]}], backend)
            self.assertEqual(handler.stub_calls, [{'fn': 2, 'start': 0, 'count': 1, 'inputs': [0x3f800000, 7]}], backend)

    def test_handler_curve(self):
        curve = di.curve_from_properties('NiagaraDataInterfaceVectorCurve', {'ShaderLUT': [0.0, 1.0, 2.0, 4.0, 5.0, 6.0], 'LUTNumSamplesMinusOne': 1.0})
        sigs = [{'InputParamLocations': [0], 'NumOutputs': 3}]
        code = prog(bytes([IDS['external_func_call'], 0]) + struct.pack('<HHHH', 0x8000, 10, 0xffff, 12))
        x = [fw(v) for v in (0.0, 0.25, 0.5, 1.0)] + [0x7fc00000]
        want = di.sample_curve(curve, x)
        for backend in ('scalar', 'numpy'):
            handler = di.ScriptHandler({0: curve}, {0: 3})
            r = run_backend(backend, code, num_instances=5, initial_registers={0: x}, external_functions=sigs, external_handler=handler)
            self.assertEqual([int(v) for v in r.registers[10]], want[0].tolist(), backend)
            self.assertEqual([int(v) for v in r.registers[12]], want[2].tolist(), backend)
            self.assertNotIn(11, r.registers)
            self.assertEqual(handler.stub_calls, [])

    def test_handler_errors(self):
        sigs = [{'InputParamLocations': [], 'NumOutputs': 1}]
        code = prog(bytes([IDS['external_func_call'], 0]) + struct.pack('<H', 10))
        for backend in ('scalar', 'numpy'):
            with self.assertRaisesRegex(ExecutionError, 'external handler output shape'):
                run_backend(backend, code, num_instances=2, external_functions=sigs, external_handler=lambda fn, args, n, start: [])
            with self.assertRaisesRegex(ExecutionError, 'requires gate_v_stub or a handler'):
                run_backend(backend, code, num_instances=1, external_functions=sigs, external_handler='nope')


@unittest.skipUnless(CORPUS and SHIM, 'DR_NIAGARA_CORPUS or DR_SVML_SHIM unset')
class CorpusTests(unittest.TestCase):
    def corpus_file(self, name):
        return next(Path(CORPUS).rglob(name + '.json'))

    def test_bind_script(self):
        from Niagara import niagara as niagara_ir
        exports = niagara_ir.exports_from_document(json.loads(self.corpus_file('NS_Cin_FakeWeaponTracers').read_text(encoding='utf-8-sig')))
        rows = [(b.fn_index, b.name, b.kind, b.di_class, b.di_export, b.user_ptr, b.num_inputs, b.num_outputs) for b in di.bind_script(exports, 7)]
        self.assertEqual(rows, [(0, 'SampleCurve', 'di', 'NiagaraDataInterfaceCurve', 1, -1, 1, 1)])
        self.assertEqual([(b.name, b.di_class, b.di_export) for b in di.bind_script(exports, 10)], [('SampleColorCurve', 'NiagaraDataInterfaceColorCurve', 0)])
        self.assertEqual(di.bind_script(exports, 11), [])
        handler = di.script_handler(exports, di.bind_script(exports, 7))
        self.assertEqual(handler.unimplemented_functions, [])
        self.assertEqual(handler(0, [np.array([0x3f000000], np.uint32)], 1, 0)[0].tolist(), [0x3f0108e6])

    def test_scheduler_real(self):
        from Niagara.niagara import load_system
        from Niagara.niagara import BakeError, BakeInputs, Scheduler, bake
        model = load_system(self.corpus_file('NS_Cin_FakeWeaponTracers'))
        inputs = BakeInputs(ticks=120, di_mode='real', svml_shim_path=SHIM)
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(bake(model, inputs, Path(temp) / 'a'), bake(model, inputs, Path(temp) / 'b'))
            manifest = json.loads((Path(temp) / 'a' / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual((manifest['inputs']['di_mode'], manifest['di_stubbed_functions']), ('real', []))
        camera = self.corpus_file('NS_GravityGun')  # Camera DI is a scene input since N-O5 follow-up (spec N-O3 §12)
        scheduler = Scheduler(load_system(camera), BakeInputs(ticks=2, di_mode='real', svml_shim_path=SHIM, camera=(100.0, -50.0, 170.0, -10.0, 30.0, 0.0, 90.0)))
        scheduler.run()
        self.assertEqual(scheduler.di_unimplemented, [])
        self.assertEqual(scheduler.di_state.camera[6], 90.0)
        with self.assertRaisesRegex(BakeError, 'di_mode'):
            Scheduler(model, BakeInputs(ticks=1, di_mode='bogus', svml_shim_path=SHIM))


if __name__ == '__main__':
    unittest.main()
