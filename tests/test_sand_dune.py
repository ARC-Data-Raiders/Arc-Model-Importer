"""M_SandDune stand-in: the landscape RVT colour becomes the sand layer texture (or its mean), NR drives normal and
roughness (no Blender)."""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import test_glass_opaque as glass_test  # noqa: E402  (registers the _sr_glass_pkg loader package)

spec = importlib.util.spec_from_file_location('_sr_glass_pkg.sand_dune', ROOT / 'surface_rebuild/sand_dune.py')
sand = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sand
spec.loader.exec_module(sand)

texel = glass_test.texel


class SandGraph(glass_test.NumericGraph):
    def decode_normal(self, tex, strength=1):
        return ('decoded', tex['r'], tex['g'])


def build(textures, images, tiling=None):
    g = SandGraph(images)
    sand.build_sand_dune(g, {'scalars': {} if tiling is None else {'Tiling': tiling}, 'colors': {}, 'switches': {},
                             'settings': {}, 'textures': textures})
    return g


class SandDuneTests(unittest.TestCase):
    def test_spline_copy_uses_the_sand_layer_stand_in(self):
        # Dune_Spline binds only Param_1 (NR): colour comes from the landscape sand layer, flagged as a stand-in.
        layer, nr = texel((.4, .3, .2)), texel((.5, .5, .35))
        g = build({'Param_1': 'NR_T'}, {sand.SAND_LAYER: layer, 'NR_T': nr}, tiling=2.)
        self.assertEqual(g.result['color'], layer['color'])
        self.assertEqual(g.result['roughness'], .35)
        self.assertEqual(g.result['normal'], ('decoded', .5, .5))
        self.assertTrue(any('stand-in' in w for w in g.report['warnings']))
        requests = {ref: (uv, srgb) for ref, uv, srgb in g.requested}
        self.assertEqual(requests['NR_T'], ((.6, 1.2, 0.), False))

    def test_authored_colour_wins_and_missing_layer_uses_its_mean(self):
        g = build({'Param': 'CH_T', 'Param_1': 'NR_T'}, {'CH_T': texel((.7, .6, .5)), 'NR_T': texel((.5, .5, .9))})
        self.assertEqual(g.result['color'], (.7, .6, .5))
        self.assertFalse(any('stand-in' in w for w in g.report['warnings']))
        g = build({}, {})
        self.assertEqual(g.result['color'], sand.SAND_MEAN)
        self.assertEqual(g.result['roughness'], .8)
        self.assertIsNone(g.result['normal'])
        self.assertTrue(any('linear mean' in w for w in g.report['warnings']))


if __name__ == '__main__':
    unittest.main()
