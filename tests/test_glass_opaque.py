"""M_GlassOpaque_NX_01 permutations equal their SM5 base-pass pixel shaders (no Blender).

The reference below is transcribed from the compiled shader maps 3904E4B1 (master, solid Color), 13377B85
(MI_Resin_01), 7806C5C8 (MI_Res_GlassWindowRoof_01) and 00C822AC (MI_WinExt_03_Glass_A_NoEmissive):

* dirt d = sat(sat((NXX.a - Low) / sat(High - Low)) * Dirt Amount)
* normal normalize(lerp(RNM(base, dirt detail), base, min(1.5 (1 - d), 1))); base is flat under Flat Normal Override
* F = max(1 - max(N.V, 0), 1e-4)^ExpIn * (1 - FresnelBaseReflect) + FresnelBaseReflect, colour lerp(c, c / 1.5, F)
  with F above 1 (an unclamped lerp)
* solid Color only: lerp(Color / 8, c, TwoSidedSign); Top Down WS Mask: lerp(c, 2c, max(N.z, 0.2)); dirt colour by d
* metallic sat((1 - d - Low) / sat(High - Low)), solid: sat(lerp(Interior Metallic, metallic, TwoSidedSign))
* roughness sat(lerp((C.r or NXX.b) * Glass Roughness, dirt.b, d)); specular sat(Specular)
"""
import importlib.util
import math
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
# Load materials.rebuild.glass_opaque and its sibling helpers without the package __init__ (which needs bpy).
package = types.ModuleType('_sr_glass_pkg')
package.__path__ = [str(ROOT / 'Shared/materials/rebuild')]
sys.modules['_sr_glass_pkg'] = package
spec = importlib.util.spec_from_file_location('_sr_glass_pkg.glass_opaque', ROOT / 'Shared/materials/rebuild/glass_opaque.py')
glass = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = glass
spec.loader.exec_module(glass)

# Instance values from the current native exports.
ROOF = {'scalars': {'Dirt Amount': .782498, 'Fresnel ExpIn': .3, 'FresnelBaseReflect': 2.5, 'Metallic High': 1.2,
                    'Metallic Low': .6, 'Glass Roughness': 2.089219, 'Interior Metallic': .4, 'Dirt Normal Tiling': 2.,
                    'Tiling': 1., 'ShadowMask': 1., 'Dirt Mask Low': .44, 'Dirt Mask High': .8,
                    'Dirt Normal Strength': 1., 'Specular': 2., 'EmissiveAmount': 0.},
        'colors': {'Color': (.445817, .541667, .39324, 1.), 'Dirt Color': (.291667, .260789, .201029, 1.),
                   'Color2': (.898246, .9375, .764453, 1.), 'Color1': (.317708, .41471, .5, 1.)},
        'switches': {'Flat Normal Override': True, 'Use Roughness Channel': True, 'Use Single Channel As Mask': True,
                     'Use solid Color instead of CR': False, 'Use Texture Coord 1': False, 'Top Down WS Mask': False}}
RESIN = {'scalars': {'Dirt Amount': 0., 'Fresnel ExpIn': 3.5001, 'FresnelBaseReflect': 2.1057, 'Metallic High': 0.,
                     'Metallic Low': 1., 'Glass Roughness': .3363, 'Interior Metallic': .4, 'Dirt Normal Tiling': 2.,
                     'Tiling': 1., 'ShadowMask': 0., 'Dirt Mask Low': 0., 'Dirt Mask High': 1.,
                     'Dirt Normal Strength': 1., 'Specular': 2., 'EmissiveAmount': 0.},
         'colors': {'Color': (.313, .375, .275, 1.)},
         'switches': {'Use solid Color instead of CR': False, 'Top Down WS Mask': True}}
WINEXT = {'scalars': {'Dirt Amount': .2, 'Fresnel ExpIn': 0., 'FresnelBaseReflect': .3, 'Metallic High': 1.2,
                      'Metallic Low': .9, 'Glass Roughness': 3., 'Interior Metallic': .4, 'Dirt Normal Tiling': 2.,
                      'Tiling': 1., 'ShadowMask': 4.5, 'Dirt Mask Low': 0., 'Dirt Mask High': 1.,
                      'Dirt Normal Strength': 1., 'Specular': 2., 'EmissiveAmount': 0.},
          'colors': {'Color': (.803, .88, .761, 1.)},
          'switches': {'Use solid Color instead of CR': False, 'Use Texture Coord 1': False, 'Top Down WS Mask': False}}
MASTER = {'scalars': {}, 'colors': {}, 'switches': {}, 'settings': {'TwoSided': True}}
# MI_HighwayAddon_Window_01 (PS BC8D5764): the solid-colour permutation with TwoSided overridden off, which the export
# records as bOverride_TwoSided alone because false is the struct default.
HIGHWAY = {'scalars': {'Glass Roughness': .336282, 'Interior Metallic': .4, 'Metallic High': 1.2, 'Metallic Low': .6,
                       'Tiling': 1., 'Dirt Amount': 2.582064, 'Dirt Mask High': 1.013596, 'Dirt Mask Low': -.04,
                       'Fresnel ExpIn': -1., 'FresnelBaseReflect': 2., 'Specular': 2., 'EmissiveAmount': 0.},
           'colors': {'Color': (.349389, .375, .334125, 1.), 'Dirt Color': (.161458, .147978, .133708, 1.)},
           'switches': {}, 'settings': {'TwoSided': False}}
UVS = {0: (.3, .6), 1: (.7, .2)}
VIEWS = ((0., 0., 1.), (.6, 0., .8), (.96, 0., .28))


def sat(v):
    return min(max(v, 0.), 1.)


def dot(a, b):
    return sum(p * q for p, q in zip(a, b))


def normalize(v):
    length = math.sqrt(dot(v, v))
    return tuple(x / length for x in v)


def lerp(a, b, f):
    if isinstance(a, tuple) or isinstance(b, tuple):
        a, b = (a if isinstance(a, tuple) else (a,) * 3), (b if isinstance(b, tuple) else (b,) * 3)
        return tuple(p + (q - p) * f for p, q in zip(a, b))
    return a + (b - a) * f


def scale(v, k):
    return tuple(x * k for x in v)


def unit(tex, strength=1.):
    x, y = tex['r'] * 2 - 1, tex['g'] * 2 - 1
    return (x * strength, y * strength, math.sqrt(max(1 - x * x - y * y, 0)))


def ramp(value, low, high):
    width = sat(high - low)
    if width <= 0:
        return float(value > low)
    return sat((value - low) / width)


def reference(case, nxx, dirt, mask, view=(0., 0., 1.), up=(0., 0., 1.), back=False):
    """The compiled pixel shader, written per channel in plain Python (UE RNM, TwoSidedSign +1/-1)."""
    s = {**glass.DEFAULTS, **case['scalars']}
    c = {k: tuple(v[:3]) for k, v in {**glass.COLORS, **case['colors']}.items()}
    sw = {**glass.SWITCHES, **case['switches']}
    d = sat(ramp(nxx['a'], s['Dirt Mask Low'], s['Dirt Mask High']) * s['Dirt Amount'])
    base = (0., 0., 1.) if sw['Flat Normal Override'] else unit(nxx)
    t, u = (base[0], base[1], base[2] + 1), scale(unit(dirt, s['Dirt Normal Strength']), 1)
    u = (-u[0], -u[1], u[2])
    detail = tuple(p * dot(t, u) / t[2] - q for p, q in zip(t, u))
    normal = normalize(lerp(detail, base, min(1.5 * (1 - d), 1)))
    world = normalize((normal[0], -normal[1], normal[2]))  # tangent X/Y on a +Z face, green flipped for Blender
    fresnel = max(1 - max(dot(world, view), 0), 1e-4) ** s['Fresnel ExpIn'] * (1 - s['FresnelBaseReflect']) \
        + s['FresnelBaseReflect']
    solid = sw['Use solid Color instead of CR']
    interior = solid and case.get('settings', {}).get('TwoSided', False)
    sign = -1. if back and interior else 1.
    if solid:
        color = c['Color']
    elif sw['Use Single Channel As Mask']:
        color = lerp(c['Color1'], c['Color2'], mask['r'])
    else:
        color = tuple(mask['color'])
    color = lerp(color, scale(color, 1 / 1.5), fresnel)
    shaded = lerp(scale(c['Color'], 1 / 8), color, sign) if interior else color
    if sw['Top Down WS Mask']:
        shaded = lerp(shaded, scale(color, 2), max(up[2], .2))
    color = tuple(sat(x) for x in lerp(shaded, c['Dirt Color'], d))
    metallic = ramp(1 - d, s['Metallic Low'], s['Metallic High'])
    if interior:
        metallic = sat(lerp(s['Interior Metallic'], metallic, sign))
    channel = mask['r'] if sw['Use Roughness Channel'] else nxx['b']
    rough = sat(lerp(channel * s['Glass Roughness'], dirt['b'], d))
    return {'color': color, 'roughness': rough, 'metallic': metallic, 'normal': world, 'specular': sat(s['Specular'])}


class Socket(SimpleNamespace):
    pass


class NumericGraph:
    """Eager stand-in for materials.native.interop.Graph: sockets are floats and 3-tuples."""
    def __init__(self, images, view=(0., 0., 1.), up=(0., 0., 1.), back=False):
        self.report = {'warnings': []}
        self.images, self.result, self.requested = images, None, []
        self.view, self.up, self.back = view, up, back

    @contextmanager
    def block(self, label): yield self
    def parameter(self, label, value, typ='Float'): return tuple(value[:3]) if isinstance(value, (tuple, list)) else float(value)
    def transform_uv(self, channel=0, scale=1, offset=(0, 0), rotation=0):
        u, v = UVS[channel]
        return (u * scale + offset[0], v * scale + offset[1], 0.)
    def image(self, ref, uv=None, layer=None, srgb=None, label='', required=True):
        self.requested.append((ref, uv, srgb))
        return self.images.get(ref)
    def node(self, typ, label='', **attrs):
        assert typ == 'ShaderNodeNewGeometry', typ
        return Socket(outputs={'Incoming': self.view, 'Backfacing': float(self.back), 'Normal': self.up})
    def math(self, op, a, b=0, c=0, label=''):
        return {'ADD': lambda: a + b, 'SUBTRACT': lambda: a - b, 'MULTIPLY': lambda: a * b, 'DIVIDE': lambda: a / b,
                'MINIMUM': lambda: min(a, b), 'MAXIMUM': lambda: max(a, b), 'POWER': lambda: a ** b,
                'GREATER_THAN': lambda: float(a > b), 'SQRT': lambda: math.sqrt(a)}[op]()
    def add(self, a, b): return self.math('ADD', a, b)
    def sub(self, a, b): return self.math('SUBTRACT', a, b)
    def mul(self, a, b): return self.math('MULTIPLY', a, b)
    def div(self, a, b): return self.math('DIVIDE', a, b)
    def clamp(self, a): return min(max(a, 0), 1)
    def mix(self, a, b, f, label='lerp'): return a * (1 - f) + b * f
    def split(self, v): return tuple(v[:3])
    def combine(self, x, y, z=0): return (x, y, z)
    def vector(self, op, a, b=(0, 0, 0), scale=1):
        a, b = tuple(a[:3]), tuple(b[:3])
        return {'SCALE': lambda: tuple(x * scale for x in a), 'ADD': lambda: tuple(map(lambda p, q: p + q, a, b)),
                'SUBTRACT': lambda: tuple(map(lambda p, q: p - q, a, b)), 'MAXIMUM': lambda: tuple(map(max, a, b)),
                'MINIMUM': lambda: tuple(map(min, a, b)), 'DOT_PRODUCT': lambda: dot(a, b),
                'NORMALIZE': lambda: normalize(a)}[op]()
    def rnm(self, a, b):
        t, u = (a[0], a[1], a[2] + 1), (-b[0], -b[1], b[2])
        return normalize(tuple(p * dot(t, u) / t[2] - q for p, q in zip(t, u)))
    def normal_to_world(self, normal, uv_channel=0):
        return normalize((normal[0], -normal[1], normal[2]))
    def finish(self, color, roughness=.5, metallic=0, normal=None, specular=.5, emission=None, alpha=1, **_):
        self.result = {'color': color, 'roughness': roughness, 'metallic': metallic, 'normal': normal,
                       'specular': specular, 'emission': emission}


def texel(rgb, a=1.):
    return {'color': tuple(rgb), 'r': rgb[0], 'g': rgb[1], 'b': rgb[2], 'a': a}


TEXELS = ((texel((.62, .41, .3), .1), texel((.55, .48, .7)), texel((.25, .5, .75))),
          (texel((.5, .5, .9), .65), texel((.3, .62, .2)), texel((.8, .2, .1))),
          (texel((.45, .58, .15), .95), texel((.5, .5, .55)), texel((.05, .9, .4))))


def build(case, nxx, dirt, mask, **graph):
    images = {'NXX_T': nxx, glass.DIRT_NORMAL: dirt, 'C_T': mask}
    g = NumericGraph(images, **graph)
    glass.build_glass_opaque(g, {'scalars': case['scalars'], 'colors': case['colors'], 'switches': case['switches'],
                                 'textures': {'NXX': 'NXX_T', 'C': 'C_T'}, 'settings': case.get('settings', {})})
    return g


class GlassOpaqueParityTests(unittest.TestCase):
    def assertVector(self, got, want, places=6):
        for p, q in zip(got, want):
            self.assertAlmostEqual(p, q, places=places)

    def check(self, case, **graph):
        for nxx, dirt, mask in TEXELS:
            with self.subTest(nxx=nxx['a'], **{k: v for k, v in graph.items()}):
                g = build(case, nxx, dirt, mask, **graph)
                want = reference(case, nxx, dirt, mask, **graph)
                self.assertVector(g.result['color'], want['color'])
                self.assertVector(g.result['normal'], want['normal'])
                for key in ('roughness', 'metallic', 'specular'):
                    self.assertAlmostEqual(g.result[key], want[key], places=6)
                self.assertIsNone(g.result['emission'])
        return g

    def test_roof_window_mask_colours_flat_normal_and_mask_roughness(self):
        for view in VIEWS:
            g = self.check(ROOF, view=view)
        requests = {ref: (uv, srgb) for ref, uv, srgb in g.requested}
        self.assertEqual(requests['C_T'], ((.3, .6, 0.), False))
        self.assertEqual(requests['NXX_T'][0], (.3, .6, 0.))
        self.assertEqual(requests[glass.DIRT_NORMAL][0], (.6, 1.2, 0.))

    def test_fresnel_above_one_darkens_past_the_grazing_colour(self):
        # Regression: a clamped MixRGB factor stops at c / 1.5. Head-on, FresnelBaseReflect 2.5 and the shader's
        # 1e-4 grazing floor give F = 2.5 - 1.5 * 1e-4^0.3 = 2.405 and the colour c (1 - F / 3).
        clean = texel((.5, .5, 1.), 0.)
        g = build(ROOF, clean, texel((.5, .5, 1.)), texel((0., 0., 0.)))
        fresnel = 2.5 - 1.5 * 1e-4 ** .3
        self.assertVector(g.result['color'], scale(ROOF['colors']['Color1'][:3], 1 - fresnel / 3))

    def test_resin_cr_colour_with_top_down_mask_on_uv1(self):
        for up in ((0., 0., 1.), (1., 0., 0.), (0., 0., -1.)):
            for view in VIEWS:
                g = self.check(RESIN, view=view, up=up)
        self.assertEqual(g.result['metallic'], 0.)  # Metallic High < Low is the shader's closed-range step
        requests = {ref: (uv, srgb) for ref, uv, srgb in g.requested}
        self.assertEqual(requests['NXX_T'][0], (.7, .2, 0.))
        self.assertIsNone(requests['C_T'][1])

    def test_winext_zero_exponent_flattens_the_fresnel(self):
        for view in VIEWS:
            g = self.check(WINEXT, view=view)
        mask = TEXELS[0][2]
        g = build(WINEXT, texel((.5, .5, 0.), 0.), texel((.5, .5, 0.)), mask, view=VIEWS[2])
        self.assertVector(g.result['color'], scale(mask['color'], 1 / 1.5))

    def test_master_solid_colour_back_faces_extrapolate_the_interior_blend(self):
        for back in (False, True):
            for view in VIEWS:
                self.check(MASTER, view=view, back=back)
        # On a wall (top-down factor 0.2) the back face keeps Color / 4 - c of the -1 sign; an upward face takes 2c on
        # both sides.
        nxx, wall = texel((.5, .5, .2), 0.), (1., 0., 0.)
        front = build(MASTER, nxx, texel((.5, .5, 0.)), None, up=wall)
        back = build(MASTER, nxx, texel((.5, .5, 0.)), None, up=wall, back=True)
        lit = 1 - (3 - 2 * 1e-4 ** .4) / 3  # head-on fresnel darkening of the master defaults
        for got, colour in zip(back.result['color'], glass.COLORS['Color'][:3]):
            self.assertAlmostEqual(got, .8 * (colour / 4 - colour * lit) + .4 * colour * lit, places=6)
        self.assertGreater(sum(back.result['color']), sum(front.result['color']))
        self.assertAlmostEqual(back.result['metallic'], sat(2 * .4 - front.result['metallic']), places=6)
        self.assertNotIn('C_T', {ref for ref, _, _ in front.requested})

    def test_one_sided_solid_colour_has_no_interior_blend(self):
        for back in (False, True):
            for view in VIEWS:
                self.check(HIGHWAY, view=view, back=back)
        nxx, wall = texel((.5, .5, .2), 0.), (1., 0., 0.)
        front = build(HIGHWAY, nxx, texel((.5, .5, 0.)), None, up=wall, view=VIEWS[1])
        back = build(HIGHWAY, nxx, texel((.5, .5, 0.)), None, up=wall, view=VIEWS[1], back=True)
        self.assertVector(back.result['color'], front.result['color'])
        self.assertAlmostEqual(back.result['metallic'], front.result['metallic'], places=9)
        # Clean texel: Dirt Mask Low -0.04 over a range that saturates to 1 still leaves d = 0.04 x Dirt Amount, and
        # metallic is the plain ramp sat((1 - d - 0.6) / 0.6) with no Interior Metallic.
        d = .04 * 2.582064
        self.assertAlmostEqual(front.result['metallic'], (1 - d - .6) / .6, places=6)


if __name__ == '__main__':
    unittest.main()
