"""Scout-drone light beam and fog sheet equal their SM5 translucent base-pass pixel shaders (no Blender).

Light beam PS FEF1C128 (shader map BAEF12EA) and fog sheet PS 6821CB6E (shader map D93C73C8), transcribed below per
register flow with time 0 and the scan ring at rest; the soft scene-depth fades are 1 (nothing behind the mesh).
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

spec = importlib.util.spec_from_file_location('_sr_glass_pkg.fx_beams', ROOT / 'surface_rebuild/fx_beams.py')
fx = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fx
spec.loader.exec_module(fx)

dot, normalize, texel = glass_test.dot, glass_test.normalize, glass_test.texel


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def sat(v):
    return min(max(v, 0.), 1.)


class FxGraph(glass_test.NumericGraph):
    """Numeric graph with the beam's UV, tangent, camera depth and per-UV texture lookups."""
    def __init__(self, sampler, uv, view, normal, tangent, depth_m):
        super().__init__({}, view=view, up=normal)
        self.sampler, self.uv0, self.tangent, self.depth_m = sampler, uv, tangent, depth_m
        self.obj = SimpleNamespace(get=lambda key, default=None: default,
                                   data=SimpleNamespace(get=lambda key, default=None: default, uv_layers=()))

    def uv(self, channel=0):
        return (self.uv0[0], self.uv0[1], 0.)

    def node(self, typ, label='', **attrs):
        if typ == 'ShaderNodeTangent':
            return SimpleNamespace(outputs={'Tangent': self.tangent})
        if typ == 'ShaderNodeCameraData':
            return SimpleNamespace(outputs={'View Z Depth': self.depth_m})
        return super().node(typ, label, **attrs)

    def image(self, ref, uv=None, layer=None, srgb=None, label='', required=True):
        self.requested.append((ref, uv, srgb))
        return self.sampler(ref, uv)

    def math(self, op, a, b=0, c=0, label=''):
        extra = {'FRACT': lambda: a - math.floor(a), 'ABSOLUTE': lambda: abs(a), 'LESS_THAN': lambda: float(a < b)}
        return extra[op]() if op in extra else super().math(op, a, b, c, label)

    def vector(self, op, a, b=(0, 0, 0), scale=1):
        if op == 'CROSS_PRODUCT':
            return cross(a, b)
        if op == 'MULTIPLY':
            return tuple(p * q for p, q in zip(a[:3], b[:3]))
        return super().vector(op, a, b, scale)

    def finish(self, color, roughness=.5, metallic=0, normal=None, specular=.5, emission=None, alpha=1, **_):
        super().finish(color, roughness, metallic, normal, specular, emission)
        self.result['alpha'] = alpha


def field(ref, uv):
    """Smooth stand-in texture: channels vary with the sample coordinates so a wrong UV changes the result."""
    x, y = uv[0], uv[1]
    salt = sum(map(ord, ref.rsplit('/', 1)[-1])) % 7 / 10
    r = .5 + .4 * math.sin(3.1 * x + 1.7 * y + salt)
    g = .5 + .4 * math.cos(2.3 * x - 1.3 * y + salt)
    return texel((r, g, .5 + .3 * math.sin(x * y + salt)), .8)


def build(builder, scalars, textures, uv, view, normal=(0., 0., 1.), tangent=(1., 0., 0.), depth_m=12.):
    g = FxGraph(field, uv, normalize(view), normal, tangent, depth_m)
    builder(g, {'scalars': scalars, 'colors': {}, 'switches': {}, 'settings': {}, 'textures': textures})
    return g


def band(x, thickness):
    f = sat((abs(.5 - x) - (.5 - thickness)) / thickness)
    return f * f * (3 - 2 * f)


def beam_reference(scalars, textures, uv, view, normal, tangent, depth_m):
    s = {**fx.BEAM_DEFAULTS, **scalars}
    u, v = uv
    p = u - math.floor(u)
    line = max((1 - p) ** s['Scan Line Exp'], band(p, s['Scan Thickness']))
    q = v * s['Scan Ring Amount'] - math.floor(v * s['Scan Ring Amount'])
    ring = max(band(q, s['Scan Thickness']), q ** s['Scan Ring Exp'] if q > 1.1920929e-07 else 0.)
    glow = (1 + s['Scan Intensity'] * (line + ring)) * s['Overall Intensity']
    view = normalize(view)
    reflect = tuple(2 * dot(normal, view) * n - w for n, w in zip(normal, view))
    rt = (dot(reflect, tangent), dot(reflect, cross(normal, tangent)), dot(reflect, normal))
    m = math.sqrt((rt[2] + 8) * .8)
    mu, mv = rt[0] / m + .5, rt[1] / m + .5
    falloff = field(textures.get('FalloffTexture') or fx.BEAM_FALLOFF, (mu, v))['g'] ** s['FalloffExponent']
    radial = field(fx.BEAM_RADIAL, (mu, mv))['r']
    dust_ref = textures.get('MultiplyTexture1') or fx.BEAM_DUST
    sx, sy = s['TextureScaleX'], s['TextureScaleY']
    first = field(dust_ref, (u * sx, v * sy))['r']
    second = field(dust_ref, ((u + .5 - math.floor(u + .5)) * sx, (v - math.floor(v)) * sy))['r']
    dust = 1 + s['TextureContrast'] * (first + (second - first) * abs(2 * (u - .5)) - 1)
    near = sat(depth_m / .01 * (.0025 / s['NearFadeDistance']))
    alpha = sat(radial * falloff * dust * s['Opacity'] * near * sat(10 * (1 - v)))
    return glow, alpha


def fog_reference(scalars, textures, uv, view, normal, depth_m):
    s = {**fx.FOG_DEFAULTS, **scalars}
    fade = sat((depth_m / .01 - s['StartOffset']) * (.0005 / s['Distance']))
    facing = abs(dot(normalize(view), normal)) ** s['FalloffExponent']
    tex = field(textures.get('FalloffTexture') or fx.FOG_FALLOFF, uv)['g']
    return sat(fade * facing * tex * s['Opacity']) ** 2


UVS = ((.03, .2), (.5, .5), (.97, .85), (1.4, .95))
VIEWS = ((0., 0., 1.), (.6, .2, .77), (-.9, .1, .3))


class FxBeamParityTests(unittest.TestCase):
    def test_light_beam_master_glow_and_alpha(self):
        for uv in UVS:
            for view in VIEWS:
                with self.subTest(uv=uv, view=view):
                    g = build(fx.build_fx_lightbeam, {}, {}, uv, view)
                    glow, alpha = beam_reference({}, {}, uv, view, (0., 0., 1.), (1., 0., 0.), 12.)
                    for channel in g.result['emission']:
                        self.assertAlmostEqual(channel, glow, places=6)
                    self.assertAlmostEqual(g.result['alpha'], alpha, places=6)
        self.assertEqual(g.result['color'], (0., 0., 0.))
        self.assertEqual(g.result['specular'], 0.)

    def test_light_beam_instance_ring_scale_and_tilted_basis(self):
        scalars = {'Scan Ring Amount': 3., 'Scan Thickness': .05, 'TextureScaleX': 2., 'TextureContrast': .4,
                   'Opacity': .6, 'FalloffExponent': 2.5, 'Overall Intensity': 3.}
        textures = {'FalloffTexture': 'FALLOFF_T', 'MultiplyTexture1': 'DUST_T'}
        normal, tangent = normalize((0., .6, .8)), (1., 0., 0.)
        for uv in UVS:
            for view in VIEWS:
                with self.subTest(uv=uv, view=view):
                    g = build(fx.build_fx_lightbeam, scalars, textures, uv, view, normal, tangent, 2.)
                    glow, alpha = beam_reference(scalars, textures, uv, view, normal, tangent, 2.)
                    self.assertAlmostEqual(g.result['emission'][0], glow, places=6)
                    self.assertAlmostEqual(g.result['alpha'], alpha, places=6)

    def test_light_beam_resting_ring_adds_the_full_scan_intensity(self):
        # Ring Amount 0 keeps q at 0, where the band edge is fully on: the beam rests at 1 + 2 * (line + 1).
        g = build(fx.build_fx_lightbeam, {}, {}, (.5, .5), (0., 0., 1.))
        self.assertAlmostEqual(g.result['emission'][0], 1 + 2 * (.5 ** 8 + 1), places=6)

    def test_light_beam_near_camera_and_far_end_fade_out(self):
        self.assertEqual(build(fx.build_fx_lightbeam, {}, {}, (.5, .5), (0., 0., 1.), depth_m=0.).result['alpha'], 0.)
        self.assertEqual(build(fx.build_fx_lightbeam, {}, {}, (.5, 1.), (0., 0., 1.)).result['alpha'], 0.)

    def test_fog_sheet_alpha_is_squared_and_distance_faded(self):
        for scalars in ({}, {'Opacity': .9, 'FalloffExponent': 1., 'Distance': .5, 'StartOffset': 100.}):
            for uv in UVS:
                for view in VIEWS:
                    for depth in (1., 30.):
                        with self.subTest(scalars=scalars, uv=uv, view=view, depth=depth):
                            g = build(fx.build_fx_fogsheet, scalars, {}, uv, view, depth_m=depth)
                            self.assertAlmostEqual(g.result['alpha'],
                                                   fog_reference(scalars, {}, uv, view, (0., 0., 1.), depth), places=6)
                            self.assertEqual(g.result['emission'], (1., 1., 1.))
        # The master caps at Opacity^2: a fog sheet is at most 6.25 % opaque.
        g = build(fx.build_fx_fogsheet, {}, {}, (.5, .5), (0., 0., 1.), depth_m=1e4)
        self.assertLessEqual(g.result['alpha'], .25 ** 2 + 1e-9)


if __name__ == '__main__':
    unittest.main()
