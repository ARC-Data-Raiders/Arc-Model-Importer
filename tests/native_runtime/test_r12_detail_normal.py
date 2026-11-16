"""R12: Detail Normal Texture is built for every detail-normal switch spelling (surface, architecture fallback, atlas decals)."""
import unittest
try:
    from .fake_graph import FakeGraph, rebuild
    from .test_surface_layer_stages import FakeGraph as SurfGraph, load_surfaces, record
except ImportError:
    from fake_graph import FakeGraph, rebuild
    from test_surface_layer_stages import FakeGraph as SurfGraph, load_surfaces, record

LIB = '/Game/Pioneer/MaterialLibrary/Masters/'
DN = '/Game/T/T_Detail_N'


class RnmGraph(SurfGraph):
    def __init__(self, uv_layers=1):
        super().__init__(uv_layers)
        self.rnm_calls = 0

    def rnm(self, a, b):
        self.rnm_calls += 1
        return super().rnm(a, b) if hasattr(SurfGraph, 'rnm') else ('RNM', a, b)


def decal(master, textures, switches, scalars=None):
    return {'master': LIB + master, 'name': 'MI_Test', 'textures': dict(textures), 'scalars': dict(scalars or {}),
            'colors': {}, 'switches': dict(switches), 'settings': {'BlendMode': 'BLEND_Translucent'}}


class SurfaceDetailNormal(unittest.TestCase):
    surfaces = load_surfaces()
    TEX = {'CR': '/Game/T/T_W_CR', 'NOH': '/Game/T/T_W_NOH', 'Detail Normal Texture': DN}

    def run_surface(self, master, switches):
        g = RnmGraph(); self.surfaces.build_surface(g, record(master, self.TEX, switches)); return g

    def test_use_detail_normal_spelling(self):
        self.assertEqual(self.run_surface('M_Wrh_WallExt_01', {'Use Detail Normal': True}).rnm_calls, 1)

    def test_enable_detail_normal_spelling(self):
        self.assertEqual(self.run_surface('M_InteriorWall_Residential_01', {'Enable Detail Normal': True}).rnm_calls, 1)

    def test_switch_off_skips(self):
        self.assertEqual(self.run_surface('M_Wrh_WallExt_01', {'Use Detail Normal': False}).rnm_calls, 0)

    def test_architecture_fallback(self):
        g = RnmGraph(); r = record('M_ArchitecturePreset_ConcreteDamage_01', self.TEX, {'Enable Detail Normal': True})
        self.surfaces.build_architecture(g, r)
        self.assertEqual(g.rnm_calls, 1)


class AtlasDecalDetailNormal(unittest.TestCase):
    decals = rebuild('decals')

    def run_decal(self, master, switches):
        g = FakeGraph()
        self.decals.build_decal(g, decal(master, {'Mask': '/Game/T/T_M', 'Detail Normal Texture': DN}, switches,
                                         {'Custom Detail Normal Tiling': 4, 'Detail Normal Strength': .5}))
        return g

    def test_parallax_atlas_detail_normal(self):
        g = self.run_decal('M_DecalPreset_Parallax_CA+NOH', {'Enable Detail Normal': True})
        self.assertIn('Detail normal', g.images)
        self.assertIn('T[Detail normal]', str(g.out['normal']))
        self.assertTrue(any(w.startswith('approximate: atlas decal detail normal') for w in g.report['warnings']))

    def test_atlas_detail_nocontact(self):
        g = self.run_decal('M_DecalPreset_Masked_Atlas+Detail_NoContact', {'Enable Detail Normal': True})
        self.assertIn('T[Detail normal]', str(g.out['normal']))

    def test_off_no_normal(self):
        g = self.run_decal('M_DecalPreset_Parallax_CA+NOH', {})
        self.assertNotIn('Detail normal', g.images)


if __name__ == '__main__':
    unittest.main()
