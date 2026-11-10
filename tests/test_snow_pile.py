"""M_FZT_SnowPiles_01 equals its SM5 base-pass pixel shader (no Blender).

The reference follows the register flow of PS D2E9AB60 (shader map 71DC0E78):

* r8 = NOH normal + (0, 0, 1); r4 = detail operand (-s x, -s y, s (z - 1) + 1); r5 = r8 dot(r8, r4) - r4 r8.z
* second pass with r5 + (0, 0, 1) and the Blend Normal operand gives q; misc = sat((q.y - Low) / sat(High - Low))
* normal = normalize(lerp(q, (0, 0, 1), sat(2 (1 - detail.a))))
* top = sat((N.z - 0.2) / 0.3); colour = sat(lerp(2 sat(blend.a + 0.25) Snow, (0.85, 0.85, 0.9), min(top / misc, 1)))
  with Snow = (0.85, 0.85, 0.9) * Miscoloration and min(x / 0, 1) = 1
* roughness = sat(lerp(r, r * Miscoloration Roughness, 1 - top)), r = sat((detail.a - Low) / sat(High - Low))
"""
import importlib.util
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import test_glass_opaque as glass_test  # noqa: E402  (registers the _sr_glass_pkg loader package)

spec = importlib.util.spec_from_file_location('_sr_glass_pkg.snow_pile', ROOT / 'Shared/materials/rebuild/snow_pile.py')
snow = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = snow
spec.loader.exec_module(snow)

dot, normalize, lerp, scale, sat, ramp, texel = (glass_test.dot, glass_test.normalize, glass_test.lerp,
                                                  glass_test.scale, glass_test.sat, glass_test.ramp, glass_test.texel)
MASTER = {'scalars': {}, 'colors': {}}
# An instance-style case: weaker detail, stronger blend normal, a wider miscoloration band and a roughness multiplier.
VARIANT = {'scalars': {'Detail Tiling': 2., 'Detail Normal Strength': .5, 'Blend Normal Strength': 1.5,
                       'Miscoloration Range Low': -.2, 'Miscoloration Range High': .6, 'RoughnessLow': .1,
                       'RoughnessHihg': .7, 'Miscoloration Roughness': .6},
           'colors': {'Miscoloration': (.7, .75, .9, 1.)}}
UPS = ((0., 0., 1.), (1., 0., 0.), (.6, 0., .8), (0., .8, .6))
TEXELS = ((texel((.5, .5, 1.), .5), texel((.5, .5, 1.), .5), texel((.5, .5, 1.), .5)),
          (texel((.62, .41, .3), .9), texel((.55, .48, .7), .1), texel((.25, .5, .75), .05)),
          (texel((.45, .58, .15), .35), texel((.3, .62, .2), .8), texel((.6, .3, .4), .7)),
          (texel((.5, .45, .9), .6), texel((.52, .7, .2), .45), texel((.4, .62, .3), .98)))


class SnowGraph(glass_test.NumericGraph):
    def math(self, op, a, b=0, c=0, label=''):
        if op == 'LESS_THAN':
            return float(a < b)
        return super().math(op, a, b, c, label)

    def vector(self, op, a, b=(0, 0, 0), scale=1):
        if op == 'MULTIPLY':
            return tuple(p * q for p, q in zip(a[:3], b[:3]))
        return super().vector(op, a, b, scale)


def reference(case, noh, detail, blend, up):
    s = {**snow.DEFAULTS, **case['scalars']}
    tint = tuple(case['colors'].get('Miscoloration', snow.MISCOLORATION)[:3])

    def unit(tex):
        x, y = tex['r'] * 2 - 1, tex['g'] * 2 - 1
        return x, y, math.sqrt(max(1 - x * x - y * y, 0))

    def operand(tex, k):
        x, y, z = unit(tex)
        return (-k * x, -k * y, k * (z - 1) + 1)

    def rnm(t, u):
        return tuple(p * dot(t, u) - q * t[2] for p, q in zip(t, u))

    x, y, z = unit(noh)
    r5 = rnm((x, y, z + 1), operand(detail, s['Detail Normal Strength']))
    q = rnm((r5[0], r5[1], r5[2] + 1), operand(blend, s['Blend Normal Strength']))
    misc = ramp(q[1], s['Miscoloration Range Low'], s['Miscoloration Range High'])
    normal = normalize(lerp(q, (0., 0., 1.), sat(2 * (1 - detail['a']))))
    top = sat((up[2] - .2) / .3)
    lifted = scale(tuple(a * b for a, b in zip(tint, snow.SNOW_WHITE)), 2 * sat(blend['a'] + .25))
    whiten = 1. if misc <= 0 else min(top / misc, 1.)
    rough = ramp(detail['a'], s['RoughnessLow'], s['RoughnessHihg'])
    return {'color': tuple(sat(v) for v in lerp(lifted, snow.SNOW_WHITE, whiten)),
            'normal': normalize((normal[0], -normal[1], normal[2])),
            'roughness': sat(lerp(rough, rough * s['Miscoloration Roughness'], 1 - top))}


def build(case, noh, detail, blend, up=(0., 0., 1.), textures=None):
    images = {'NOH_T': noh, snow.SNOW_DETAIL: detail, 'BLEND_T': blend}
    g = SnowGraph({k: v for k, v in images.items() if v is not None}, up=up)
    snow.build_snow_pile(g, {'scalars': case['scalars'], 'colors': case['colors'], 'switches': {}, 'settings': {},
                             'textures': textures or {'NOH': 'NOH_T', 'Blend Normal': 'BLEND_T'}})
    return g


class SnowPileParityTests(unittest.TestCase):
    def assertVector(self, got, want, places=6):
        for p, q in zip(got, want):
            self.assertAlmostEqual(p, q, places=places)

    def test_master_and_instance_match_the_pixel_shader(self):
        for case in (MASTER, VARIANT):
            for noh, detail, blend in TEXELS:
                for up in UPS:
                    with self.subTest(case=case['scalars'].get('Detail Tiling'), a=(detail['a'], blend['a']), up=up):
                        g = build(case, noh, detail, blend, up)
                        want = reference(case, noh, detail, blend, up)
                        self.assertVector(g.result['color'], want['color'])
                        self.assertVector(g.result['normal'], want['normal'])
                        self.assertAlmostEqual(g.result['roughness'], want['roughness'], places=6)
                        self.assertEqual(g.result['metallic'], 0)

    def test_flat_snow_is_near_white_not_grey(self):
        # Regression: the generic packed-channel fallback rendered the pile 0.5 grey. Flat textures give q = (0, 0, 6),
        # misc = 0.2: a top face reaches (0.85, 0.85, 0.9) and a wall clamps 2 * 0.75 * Snow to white.
        flat = texel((.5, .5, 1.), .5)
        self.assertVector(build(MASTER, flat, flat, flat, (0., 0., 1.)).result['color'], snow.SNOW_WHITE)
        self.assertVector(build(MASTER, flat, flat, flat, (1., 0., 0.)).result['color'], (1., 1., 1.))

    def test_texture_frequencies_and_defaults(self):
        flat = texel((.5, .5, 1.), .5)
        g = build(MASTER, flat, flat, flat)
        requests = {ref: (uv, srgb) for ref, uv, srgb in g.requested}
        self.assertVector(requests['NOH_T'][0], (.3, .6, 0.))
        self.assertVector(requests[snow.SNOW_DETAIL][0], (.3 * 12, .6 * 12, 0.))  # UV0 x 8 x Detail Tiling 1.5
        self.assertVector(requests['BLEND_T'][0], (.3 * 6, .6 * 6, 0.))
        self.assertTrue(all(srgb is False for _, srgb in requests.values()))
        # A master without a Blend Normal override samples the fixed fresh-snow texture.
        g = build(MASTER, flat, flat, flat, textures={'NOH': 'NOH_T'})
        self.assertIn(snow.SNOW_FRESH, [ref for ref, _, _ in g.requested])

    def test_missing_detail_textures_degrade_with_warnings(self):
        g = build(MASTER, texel((.5, .5, 1.)), None, None)
        self.assertEqual(g.result['roughness'], .375)
        self.assertEqual(len([w for w in g.report['warnings'] if 'missing' in w]), 2)
        self.assertTrue(all(0 <= v <= 1 for v in g.result['color']))


if __name__ == '__main__':
    unittest.main()
