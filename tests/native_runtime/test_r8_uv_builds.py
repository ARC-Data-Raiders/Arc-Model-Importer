"""R8 UV builds: exported UV switches (Simpel Tilling, Use Atlas, Orientation is Y, 4. Use UV2) reach the shared UV path."""
import unittest

try:
    from .fake_graph import FakeGraph, rebuild
    from .test_surface_layer_stages import FakeGraph as SurfGraph, load_surfaces, record, EnvGraph, SNOW_TEXTURES
except ImportError:
    from fake_graph import FakeGraph, rebuild
    from test_surface_layer_stages import FakeGraph as SurfGraph, load_surfaces, record, EnvGraph, SNOW_TEXTURES

LIB = '/Game/Pioneer/MaterialLibrary/Masters/'


class UVGraph(FakeGraph):
    def __init__(self):
        super().__init__()
        self.uvs = []

    def image(self, ref, uv=None, layer=None, srgb=None, label='', required=True):
        self.uvs.append(str(uv))
        return super().image(ref, uv, layer, srgb, label, required)


class SurfUVGraph(SurfGraph):
    def __init__(self, uv_layers=3):
        super().__init__(uv_layers)
        self.uvs = []

    def image(self, ref, uv=None, layer=None, srgb=None, label='', required=True):
        self.uvs.append(str(uv))
        return super().image(ref, uv, layer, srgb, label, required)


def decal(master, textures, switches, scalars=None):
    return {'master': LIB + master, 'name': 'MI_Test', 'textures': dict(textures), 'scalars': dict(scalars or {}),
            'colors': {}, 'switches': dict(switches), 'settings': {'BlendMode': 'BLEND_Translucent'}}


class R8DecalUV(unittest.TestCase):
    decals = rebuild('decals')

    def test_use_atlas_selects_cell(self):
        r = decal('M_DecalPreset_Parallax_CA+NOH', {'CA': '/Game/T/T_A_CA', 'Mask': '/Game/T/T_A_M'}, {'Use Atlas': True},
                  {'Rows': 2, 'Columns': 4, 'Position': 3})
        on = UVGraph(); self.decals.build_decal(on, r)
        r['switches'] = {'Use Atlas': False}
        off = UVGraph(); self.decals.build_decal(off, r)
        self.assertNotEqual(on.uvs, off.uvs)

    def test_simpel_tilling_off_warns(self):
        r = decal('M_DecalPreset_Translucent_Atlas+CA+Detail', {'CA': '/Game/T/T_A_CA'}, {'Simpel Tilling': False},
                  {'Rows': 1, 'Columns': 1})
        g = UVGraph(); self.decals.build_decal(g, r)
        self.assertTrue(any('Simpel Tilling' in w and w.startswith('approximate:') for w in g.report['warnings']))
        r['switches'] = {'Simpel Tilling': True}
        g = UVGraph(); self.decals.build_decal(g, r)
        self.assertFalse(any('Simpel Tilling' in w for w in g.report['warnings']))


class R8SurfaceUV(unittest.TestCase):
    surfaces = load_surfaces()
    TEX = {'CR': '/Game/T/T_Cable_CR', 'NOH': '/Game/T/T_Cable_NOH'}

    def run_surface(self, master, switches):
        g = SurfUVGraph(); self.surfaces.build_surface(g, record(master, self.TEX, switches))
        return g

    def test_orientation_is_y_swaps_uv(self):
        on = self.run_surface('M_Cables_01', {'Orientation is Y': True})
        off = self.run_surface('M_Cables_01', {'Orientation is Y': False})
        self.assertNotEqual(on.uvs, off.uvs)
        self.assertTrue(any('Orientation is Y' in w for w in on.report['warnings']))

    def test_numbered_use_uv2_selects_channel_2(self):
        g = self.run_surface('M_ArchitecturePreset_Metal', {'4. Use UV2': True})
        self.assertTrue(any("'uv', '2'" in u for u in g.uvs), g.uvs[:2])


class R8EnvColorTint(unittest.TestCase):
    surfaces = load_surfaces()

    def run_env(self, on):
        g = EnvGraph()
        self.surfaces.build_environment_layers(g, record('M_Res_HouseWallPreset', SNOW_TEXTURES,
                                                         {'0. Use Triplanar Overlay': False, 'Use Color Tint': on},
                                                         colors={'Tint Texture': (.6, .4, .3, 1)}))
        return g

    def test_use_color_tint_multiplies_tint_texture(self):
        on, off = self.run_env(True), self.run_env(False)
        self.assertIn('0.6', str(on.finished['color']))
        self.assertNotIn('0.6', str(off.finished['color']))
        self.assertTrue(any('Use Color Tint' in w for w in on.report['warnings']))


import importlib.util
import os

_P = os.path.join(os.path.dirname(__file__), '..', '..', 'Shared', 'materials', 'audit_param_rules.py')
_spec = importlib.util.spec_from_file_location('_r8_rules', _P)
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)


class R8SwitchRules(unittest.TestCase):
    def test_derivative_scaling_is_view_dependent(self):
        self.assertEqual(R.switch_class('UseDerivativeScaling'), ('view_dependent', R.INFO))

    def test_use_heat_is_runtime_input(self):
        # Heat Amount defaults to 0 on every exported MI; runtime heat drives it.
        self.assertEqual(R.switch_class('Use Heat'), ('runtime_input', R.INFO))

    def test_gated_reasons(self):
        for name in ('Optimize VT Color', 'WareHouse', 'Outpost', 'DitherMask', 'Advanced UV Options'):
            self.assertTrue(R.gated_reason(name), name)
        self.assertEqual(R.gated_reason('Use Foo'), '')


if __name__ == '__main__':
    unittest.main()
