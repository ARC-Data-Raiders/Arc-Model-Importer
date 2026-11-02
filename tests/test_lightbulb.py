"""M_MasterLight_Lightbulb_01 equals its SM5 base-pass pixel shader (no Blender).

PS 83C059E2 (shader map 709BC325): o3 = sat(GlassColor); o2.z = the view roughness bias (roughness 0); emissive
o0 = Lightbulb.rgb * cb2[1] + GlassTexture.g * cb2[2], with the preshader fields cb2[1] = LightIntensity * LightColor.rgb
and cb2[2] = normalize(LightColor.rgb) * LightIntensity.
"""
import importlib.util
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import test_glass_opaque as glass_test  # noqa: E402  (registers the _sr_glass_pkg loader package)

spec = importlib.util.spec_from_file_location('_sr_glass_pkg.lightbulb', ROOT / 'surface_rebuild/lightbulb.py')
bulb = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bulb
spec.loader.exec_module(bulb)

texel = glass_test.texel
TEXTURES = {'Lightbulb': 'BULB_T', 'GlassTexture': 'GLASS_T', 'Normal': 'NORMAL_T'}


class LightGraph(glass_test.NumericGraph):
    def vector(self, op, a, b=(0, 0, 0), scale=1):
        if op == 'MULTIPLY':
            return tuple(p * q for p, q in zip(a[:3], b[:3]))
        return super().vector(op, a, b, scale)

    def decode_normal(self, tex, strength=1):
        return ('decoded', tex['r'], tex['g'])


def build(scalars, colors, images):
    g = LightGraph(images)
    bulb.build_lightbulb(g, {'scalars': scalars, 'colors': colors, 'textures': TEXTURES, 'switches': {}, 'settings': {}})
    return g


def reference(scalars, colors, bulb_texel, glass_texel):
    intensity = scalars.get('LightIntensity', 100.)
    light = colors.get('LightColor', (1., 1., 1., 0.))[:3]
    length = math.sqrt(sum(x * x for x in light))
    cb1 = tuple(intensity * x for x in light)
    cb2 = tuple(x / length * intensity for x in light)
    return tuple(b * p + glass_texel['g'] * q for b, p, q in zip(bulb_texel['color'], cb1, cb2))


class LightbulbParityTests(unittest.TestCase):
    def assertVector(self, got, want, places=5):
        for p, q in zip(got, want):
            self.assertAlmostEqual(p, q, places=places)

    def test_master_defaults_glow_at_intensity_100(self):
        b, glass = texel((.9, .7, .4)), texel((.2, .5, .1))
        g = build({}, {}, {'BULB_T': b, 'GLASS_T': glass, 'NORMAL_T': texel((.5, .5, 1.))})
        self.assertVector(g.result['emission'], reference({}, {}, b, glass))
        self.assertAlmostEqual(g.result['emission'][0], 90 + .5 * 100 / math.sqrt(3), places=4)
        self.assertVector(g.result['color'], (.354167,) * 3)
        self.assertEqual((g.result['roughness'], g.result['metallic']), (0., 0))
        self.assertEqual(g.result['normal'], ('decoded', .5, .5))

    def test_coloured_instance_and_clamped_glass(self):
        scalars, colors = {'LightIntensity': 20., 'Tiling': 2.}, {'LightColor': (1., .5, .25, 1.), 'GlassColor': (1.2, .3, -.1, 1.)}
        b, glass = texel((.5, .6, .7)), texel((.1, .8, .3))
        g = build(scalars, colors, {'BULB_T': b, 'GLASS_T': glass})
        self.assertVector(g.result['emission'], reference(scalars, colors, b, glass))
        self.assertVector(g.result['color'], (1., .3, 0.))
        requests = {ref: uv for ref, uv, _ in g.requested}
        self.assertEqual(requests['BULB_T'], (.6, 1.2, 0.))
        self.assertEqual(requests['NORMAL_T'], (.3, .6, 0.))

    def test_off_instance_keeps_only_the_glass(self):
        g = build({'LightIntensity': 0.}, {}, {'BULB_T': texel((1., 1., 1.)), 'GLASS_T': texel((1., 1., 1.))})
        self.assertVector(g.result['emission'], (0., 0., 0.))

    def test_missing_bulb_texture_keeps_the_glass_glow(self):
        glass = texel((.0, .4, .0))
        g = build({}, {}, {'GLASS_T': glass})
        self.assertVector(g.result['emission'], (.4 * 100 / math.sqrt(3),) * 3, places=4)
        self.assertTrue(any('Lightbulb texture missing' in w for w in g.report['warnings']))


if __name__ == '__main__':
    unittest.main()
