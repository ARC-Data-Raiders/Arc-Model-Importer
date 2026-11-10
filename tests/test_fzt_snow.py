"""M_FZT_Snow_01 equals its SM5 base-pass pixel shader (no Blender).

The reference follows the register flow of PS 8DF77F37 (shader map 51400BCA):

* uv = world XY / '1. Texture Size' (t2, '1. Snow_NR_1') and world XY / 128 (t3, fixed T_FZT_SnowDetail_01_NH)
* n = (s x, s y, s (z - 1) + 1) for the t2 unit normal, then n.xy += 1.5 (t3.rg - 0.5)
* n = lerp(n, (0, 0, 1), COLOR0.r * round(IsRoof)) through the tangent basis, normalised
* f = sat(17 max(|1 - max(N.V, 0)|, 1e-4)^0.1 - 16); colour (0.85, 0.85, 0.9) + f (0.05, 0.05, 0.1)
* roughness sat(t3.a - f Roughness Multiplier 0.5); specular 0.2 - 0.1 sat((distance - 950) / 650); metallic 0
"""
import importlib.util
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import test_glass_opaque as glass_test  # noqa: E402  (registers the _sr_glass_pkg loader package)

spec = importlib.util.spec_from_file_location('_sr_glass_pkg.fzt_snow', ROOT / 'Shared/materials/rebuild/fzt_snow.py')
snow = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = snow
spec.loader.exec_module(snow)

dot, normalize, lerp, sat, texel = glass_test.dot, glass_test.normalize, glass_test.lerp, glass_test.sat, glass_test.texel
MASTER = {'scalars': {}}
# Instance-style values: a smaller projection, stronger normal, glossier grazing response and the roof flatten off.
VARIANT = {'scalars': {'1. Texture Size': 512., '3. Normal Strength': 1.6, 'Roughness Multiplier': 1.4, 'IsRoof': 0.}}
VIEWS = ((0., 0., 1.), (.6, 0., .8), (.96, 0., .28), (1., 0., 0.))
TEXELS = ((texel((.5, .5, 1.), .5), texel((.5, .5, 1.), .5)),
          (texel((.62, .41, .3), .9), texel((.55, .48, .7), .8)),
          (texel((.45, .58, .15), .35), texel((.3, .62, .2), .05)))


class Obj(dict):
    def __init__(self, scale, colors):
        super().__init__(arc_ue_unit_scale=scale)
        self.data = SimpleNamespace(color_attributes=[SimpleNamespace(name='Col')] if colors else [], get=lambda k, d=None: d)


class FztGraph(glass_test.NumericGraph):
    def __init__(self, images, red=None, position=(3.2, -1.7, .4), distance=12., scale=.01, **graph):
        super().__init__(images, **graph)
        self.obj, self.position, self.distance, self.red = Obj(scale, red is not None), position, distance, red

    def node(self, typ, label='', **attrs):
        if typ == 'ShaderNodeNewGeometry':
            return glass_test.Socket(outputs={'Position': self.position, 'Incoming': self.view, 'Normal': self.up})
        if typ == 'ShaderNodeCameraData':
            return glass_test.Socket(outputs={'View Distance': self.distance})
        assert typ == 'ShaderNodeVertexColor', typ
        return glass_test.Socket(outputs={'Color': (self.red, .3, .7), 'Alpha': 1.})

    def math(self, op, a, b=0, c=0, label=''):
        if op == 'ABSOLUTE':
            return abs(a)
        return super().math(op, a, b, c, label)


def reference(case, plain, detail, red, up, view, distance, scale=.01):
    s = {**snow.DEFAULTS, **case['scalars']}
    k = s['3. Normal Strength']
    x, y = plain['r'] * 2 - 1, plain['g'] * 2 - 1
    z = math.sqrt(max(1 - x * x - y * y, 0))
    n = (k * x + 1.5 * (detail['r'] - .5), k * y + 1.5 * (detail['g'] - .5), k * (z - 1) + 1)
    n = lerp(n, (0., 0., 1.), (1. if red is None else red) * round(s['IsRoof']))
    n = normalize(n)
    f = sat(17 * max(abs(1 - max(dot(up, view), 0)), 1e-4) ** .1 - 16)
    return {'color': (.85 + .05 * f, .85 + .05 * f, .9 + .1 * f),
            'normal': normalize((n[0], -n[1], n[2])),
            'roughness': sat(detail['a'] - f * s['Roughness Multiplier'] * .5),
            'specular': .2 - .1 * sat((distance / scale - 950) / 650)}


def build(case, plain, detail, textures=None, **graph):
    images = {'PLAIN_T': plain, snow.SNOW_DETAIL: detail}
    g = FztGraph({k: v for k, v in images.items() if v is not None}, **graph)
    snow.build_fzt_snow(g, {'scalars': case['scalars'], 'colors': {}, 'switches': {}, 'settings': {},
                            'textures': {'1. Snow_NR_1': 'PLAIN_T'} if textures is None else textures})
    return g


class FztSnowParityTests(unittest.TestCase):
    def assertVector(self, got, want, places=6):
        for p, q in zip(got, want):
            self.assertAlmostEqual(p, q, places=places)

    def test_master_and_instance_match_the_pixel_shader(self):
        for case in (MASTER, VARIANT):
            for plain, detail in TEXELS:
                for view in VIEWS:
                    for red, distance in ((None, 4.), (0., 12.), (.4, 25.)):
                        with self.subTest(case=case['scalars'], a=detail['a'], view=view, red=red):
                            g = build(case, plain, detail, view=view, up=(0., 0., 1.), red=red, distance=distance)
                            want = reference(case, plain, detail, red, (0., 0., 1.), view, distance)
                            self.assertVector(g.result['color'], want['color'])
                            self.assertVector(g.result['normal'], want['normal'])
                            for key in ('roughness', 'specular'):
                                self.assertAlmostEqual(g.result[key], want[key], places=6)
                            self.assertEqual(g.result['metallic'], 0)

    def test_meshes_without_vertex_colour_are_fully_flattened_roofs(self):
        # UE binds white vertex colour when a mesh has no colour buffer: SnowBus and SnowScatter carry none, so the
        # IsRoof = 1 master flattens the projected normals to the vertex normal.
        plain, detail = TEXELS[1]
        self.assertVector(build(MASTER, plain, detail).result['normal'], (0., 0., 1.))
        self.assertNotAlmostEqual(build(MASTER, plain, detail, red=0.).result['normal'][2], 1., places=3)

    def test_head_on_snow_is_near_white_and_grazing_lifts_blue(self):
        plain, detail = TEXELS[0]
        self.assertVector(build(MASTER, plain, detail, view=(0., 0., 1.)).result['color'], (.85, .85, .9))
        self.assertVector(build(MASTER, plain, detail, view=(1., 0., 0.)).result['color'], (.9, .9, 1.))

    def test_world_projection_frequencies(self):
        plain, detail = TEXELS[0]
        g = build(MASTER, plain, detail, position=(3.2, -1.7, .4))
        requests = {ref: (uv, srgb) for ref, uv, srgb in g.requested}
        # Blender metres / 0.01 = UE centimetres with Y flipped: (320, 170) cm.
        self.assertVector(requests['PLAIN_T'][0], (320 / 1024, 170 / 1024, 0.))
        self.assertVector(requests[snow.SNOW_DETAIL][0], (320 / 128, 170 / 128, 0.))
        self.assertTrue(all(srgb is False for _, srgb in requests.values()))
        g = build(VARIANT, plain, detail, position=(3.2, -1.7, .4), scale=1.)
        self.assertVector({ref: uv for ref, uv, _ in g.requested}['PLAIN_T'], (3.2 / 512, 1.7 / 512, 0.))

    def test_master_default_texture_and_missing_maps_degrade_with_warnings(self):
        plain, detail = TEXELS[1]
        g = build(MASTER, plain, detail, textures={})
        self.assertIn(snow.SNOW_PLAIN, [ref for ref, _, _ in g.requested])
        g = build(MASTER, None, None, red=0.)
        self.assertEqual(len([w for w in g.report['warnings'] if 'missing' in w]), 2)
        self.assertVector(g.result['normal'], (0., 0., 1.))
        self.assertAlmostEqual(g.result['roughness'], .5)


if __name__ == '__main__':
    unittest.main()
