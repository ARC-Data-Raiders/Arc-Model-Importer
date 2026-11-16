"""R36 builders: CR+NOM Mask emissive, Atlas decal Color Overlay, packed-prop overlay/snow layers."""
import ast
import os
import types
import unittest
from pathlib import Path

try:
    from .fake_graph import FakeGraph, rebuild
except ImportError:
    from fake_graph import FakeGraph, rebuild

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / 'Shared' / 'materials' / 'environment.py'
LIB = '/Game/Pioneer/MaterialLibrary/Masters/'


def env_funcs(*names, **inject):
    tree = ast.parse(ENV.read_text(encoding='utf-8'))
    body = [x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name in names]
    mod = types.ModuleType('env_slice')
    mod.__dict__.update(inject)
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(ENV), 'exec'), mod.__dict__)
    return mod


def _switch(sw, name, default=False):
    return bool(sw.get(name, default))


class Img:
    def __init__(self, path):
        self.filepath = path
        self.name = os.path.basename(path)


class NomEmissiveMask(unittest.TestCase):
    def mod(self, mask_path):
        lookup = {'Mask': Img(mask_path)} if mask_path else {}
        return env_funcs('_nom_emissive_mask_param', os=os, _mi_switch=_switch,
                         _find_env_tex=lambda tl, n: (n, tl.get(n))), lookup

    def test_emissive_e_mask_selected(self):
        m, lk = self.mod('//T/T_Lamp_E.png')
        self.assertEqual(m._nom_emissive_mask_param({'switches': {'Use Emissive': True}}, lk), 'Mask')

    def test_ao_suffix_not_emissive(self):
        m, lk = self.mod('//T/T_Lamp_A.png')
        self.assertIsNone(m._nom_emissive_mask_param({'switches': {'Use Emissive': True}}, lk))

    def test_switch_off(self):
        m, lk = self.mod('//T/T_Lamp_E.png')
        self.assertIsNone(m._nom_emissive_mask_param({'switches': {'Use Emissive': False}}, lk))

    def test_no_mask(self):
        m, lk = self.mod(None)
        self.assertIsNone(m._nom_emissive_mask_param({'switches': {'Use Emissive': True}}, lk))


class AtlasColorOverlay(unittest.TestCase):
    decals = rebuild('decals')

    def run_decal(self, switches, textures=None):
        tex = {'Mask': '/Game/T/T_Dec_M', 'Color Overlay': '/Game/T/T_Concrete_ColorVar_01_C'}
        tex.update(textures or {})
        r = {'master': LIB + 'M_DecalPreset_Masked_Atlas+Detail_NoContact', 'name': 'MI_Test', 'textures': tex,
             'scalars': {'Color Overlay Amount': .6, 'Overlay_Tiling': 3}, 'colors': {'BC Color': (.4, .4, .4, 1)},
             'switches': dict(switches), 'settings': {'BlendMode': 'BLEND_Translucent'}}
        g = FakeGraph(); self.decals.build_decal(g, r); return g

    def test_overlay_built(self):
        g = self.run_decal({'Use Color Overlay': True})
        self.assertIn('Color Overlay', g.images)
        self.assertNotIn('Atlas branch not reconstructed: Use Color Overlay', g.report['warnings'])
        self.assertIn('Color Overlay multiply (approximate)', g.report['implemented_stages'])

    def test_overlay_off(self):
        g = self.run_decal({'Use Color Overlay': False})
        self.assertNotIn('Color Overlay', g.images)

    def test_missing_texture_keeps_warning(self):
        g = self.run_decal({'Use Color Overlay': True}, {'Color Overlay': ''})
        self.assertIn('Atlas branch not reconstructed: Use Color Overlay', g.report['warnings'])


def _scalar(sc, *names, default=0.0):
    for n in names:
        if n in sc:
            return float(sc[n])
    return default


class PackedOverlayGate(unittest.TestCase):
    m = env_funcs('_packed_overlay_gate', _PACKED_OVERLAY_GATES=("UseOverlay", "EnableOverlay", "Color Overlay"),
                  _PACKED_OVERLAY_MODIFIERS=("TriPlanar Overlay",))

    def test_inherited_false_does_not_mask_color_overlay(self):
        on, keys = self.m._packed_overlay_gate({'UseOverlay': False, 'Color Overlay': True})
        self.assertTrue(on)
        self.assertEqual(keys, ['Color Overlay'])

    def test_triplanar_alone_enables(self):
        self.assertEqual(self.m._packed_overlay_gate({'TriPlanar Overlay': True}), (True, ['TriPlanar Overlay']))

    def test_triplanar_ignored_with_gate_off(self):
        self.assertFalse(self.m._packed_overlay_gate({'UseOverlay': False, 'TriPlanar Overlay': True})[0])

    def test_pattern_overlay_not_a_gate(self):
        self.assertFalse(self.m._packed_overlay_gate({'Use Pattern Overlay': True})[0])


class PackedSnowPlan(unittest.TestCase):
    m = env_funcs('_packed_snow_plan', _mi_scalar=_scalar)

    def test_off(self):
        self.assertIsNone(self.m._packed_snow_plan({'switches': {'Use Perma Snow': False}}))

    def test_perma_snow(self):
        p = self.m._packed_snow_plan({'switches': {'Use Perma Snow': True},
                                      'scalars': {'Snow Mask Min': .2, 'Snow Mask Max': .7, '10. Snow Amount': 3}})
        self.assertEqual(p['keys'], ['Use Perma Snow'])
        self.assertEqual((p['min'], p['max'], p['amount']), (.2, .7, 1.0))


class MixSockConst(unittest.TestCase):
    m = env_funcs('_mix_sock_const')

    def test_const_and_socket(self):
        class Inp:
            def __init__(s): s.default_value = None
        class Node:
            def __init__(s):
                s.inputs = {i: Inp() for i in list(range(8)) + ['Factor']}; s.outputs = ['f', 'v', 'c']
        made, linked = [], []
        nodes = types.SimpleNamespace(new=lambda t: made.append(Node()) or made[-1])
        links = types.SimpleNamespace(new=lambda a, b: linked.append((a, b)))
        sock = types.SimpleNamespace(node=1)
        out = self.m._mix_sock_const(nodes, links, sock, (1, 1, 1, 1), .5, (0, 0), 'x', 'RGBA')
        self.assertEqual(out, 'c')
        self.assertEqual(made[0].inputs[7].default_value, (1, 1, 1, 1))
        self.assertEqual(made[0].inputs['Factor'].default_value, .5)
        self.assertEqual(linked, [(sock, made[0].inputs[6])])


def _load(name):
    import importlib.util
    p = ROOT / 'Shared' / 'materials' / f'{name}.py'
    spec = importlib.util.spec_from_file_location(f'r36_{name}', p)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


class PulsatingSwitch(unittest.TestCase):
    R = _load('audit_param_rules')

    def test_pulsating_is_time_dependent_info(self):
        cls, sev = self.R.switch_class('Pulsating')
        self.assertEqual(cls, 'time_dependent')
        self.assertEqual(sev, self.R.SEVERITY['switch_time_dependent'])
        self.assertEqual(sev, self.R.INFO)

    def test_unrelated_switch_not_time_dependent(self):
        self.assertNotEqual(self.R.switch_class('Use Emissive')[0], 'time_dependent')


class NddLayout(unittest.TestCase):
    L = _load('texture_layouts')

    def test_ndd_known_normal_and_b_mask(self):
        self.assertEqual(self.L.layout_code('/Game/T/T_BrokenGlassSDF_NDD'), 'ndd')
        self.assertTrue(self.L.is_known('t_brokenglasssdf_ndd', 'ndd'))
        self.assertEqual(self.L.LAYOUTS['NDD'], [('normal', 'normal'), ('b', 'mask')])
        self.assertIn('NDD', self.L.UNPROVEN_CHANNELS)

    def test_mask_param_nx_still_unknown(self):
        self.assertFalse(self.L.is_known('3. Blend Mask', 'nx') and self.L.param_layout('3. Blend Mask', 'NX'))


if __name__ == '__main__':
    unittest.main()
