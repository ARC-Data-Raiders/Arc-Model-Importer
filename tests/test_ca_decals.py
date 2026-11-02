"""CA decal masters equal their SM5 base-pass pixel shaders (no Blender).

References are transcribed from the compiled shader maps:

* D3392735 M_DecalPreset_Translucent_Atlas+CA+Detail (also MI_BilguunEyeDecal_01_A): colour
  saturate(lerp(rgb, dot(rgb, Rec.709), Color Desaturation) * Color Multiply); roughness
  saturate(lerp(1 - Contrast, Contrast, min((1 - (R + Bias))^2, 1))); opacity A * Opacity Amount.
* F30C875C M_DecalPreset_Masked_Atlas+CA+NOR+Detail_01: the same colour and opacity, NOR normal RG and roughness A.
* FF3F713F M_DecalPreset_Translucent_CA+Detail: opacity saturate(A - saturate(breakupR^Power * (1 + 2C) - C)),
  roughness saturate((breakupG + Roughness) / 2).

The atlas cell is floor(5 * (actor x + y + z)) mod Columns*Rows in Unreal centimetres, and DitherOpacityMask
keeps a pixel when mask - clip + dither >= 0.5 with dither spread over [0, 5/6).
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
# Load surface_rebuild.decals and its sibling helpers without the package __init__ (which needs bpy).
package = types.ModuleType('_sr_decals_pkg')
package.__path__ = [str(ROOT / 'surface_rebuild')]
sys.modules['_sr_decals_pkg'] = package
spec = importlib.util.spec_from_file_location('_sr_decals_pkg.decals', ROOT / 'surface_rebuild/decals.py')
decals = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = decals
spec.loader.exec_module(decals)

EYE = 'PioneerGame/Content/Pioneer/MaterialLibrary/M_Presets/M_DecalPreset_Translucent_Atlas+CA+Detail'
NOR = 'PioneerGame/Content/Pioneer/MaterialLibrary/M_Presets/M_DecalPreset_Masked_Atlas+CA+NOR+Detail_01'
CA_DETAIL = 'PioneerGame/Content/Pioneer/MaterialLibrary/M_Presets/M_DecalPreset_Translucent_CA+Detail'
# MI_BilguunEyeDecal_01_A overrides (the dither flag is inherited from the master).
EYE_SCALARS = {'Color Multiply': 1.0, 'Rows': 1.0, 'Columns': 1.0, 'Position': 0.0, 'Color Desaturation': 0.0,
               'Opacity Amount': 0.7, 'Roughness Bias': 13.790995, 'Roughness Contrast': 3.272468, 'Tiling U': 1.0,
               'Tiling V': 1.0, 'UV Mode': 0.0}
EYE_SETTINGS = {'BlendMode': 'EBlendMode::BLEND_Masked', 'OpacityMaskClipValue': 0.3333, 'DitherOpacityMask': True}
LUMA = (.2126, .7152, .0722)


def sat(v):
    return min(max(v, 0.), 1.)


def atlas_reference(rgb, red, a, desat, multiply, opacity, contrast=None, bias=None):
    luma = sum(c * w for c, w in zip(rgb, LUMA))
    color = tuple(sat((c + desat * (luma - c)) * multiply) for c in rgb)
    rough = None
    if contrast is not None:
        x = min((1 - (red + bias)) ** 2, 1)
        rough = sat((1 - contrast) + x * (contrast - (1 - contrast)))
    return color, rough, a * opacity


def dither_coverage(mask, clip):
    """Mean of the compiled 5-value dither test over its [0, 5/6) range."""
    steps = 6000
    hits = sum(mask - clip + (i + .5) / steps * 5 / 6 >= .5 for i in range(steps))
    return hits / steps


class Socket(SimpleNamespace):
    pass


class NumericGraph:
    """Eager stand-in for material_runtime.interop.Graph: sockets are floats and 3-tuples."""
    def __init__(self, images, location=(0., 0., 0.), uv=(.3, .6)):
        self.report = {'warnings': []}
        self.images, self.result, self.requested = images, None, []
        self.location, self._uv = location, uv

    @contextmanager
    def block(self, label): yield self
    def parameter(self, label, value, typ='Float'): return tuple(value[:3]) if isinstance(value, (tuple, list)) else float(value)
    def uv(self, channel=0): return (self._uv[0], self._uv[1], 0.)
    def transform_uv(self, channel=0, scale=1, offset=(0, 0), rotation=0):
        return (self._uv[0] * scale + offset[0], self._uv[1] * scale + offset[1], 0.)
    def image(self, ref, uv=None, layer=None, srgb=None, label='', required=True):
        self.requested.append((ref, uv))
        image = self.images.get(ref)
        return image(uv) if callable(image) else image
    def node(self, typ, label='', **attrs):
        assert typ == 'ShaderNodeObjectInfo', typ
        return Socket(outputs={'Location': tuple(self.location)})
    def math(self, op, a, b=0, c=0, label=''):
        return {'ADD': lambda: a + b, 'SUBTRACT': lambda: a - b, 'MULTIPLY': lambda: a * b, 'DIVIDE': lambda: a / b,
                'MINIMUM': lambda: min(a, b), 'MAXIMUM': lambda: max(a, b), 'POWER': lambda: a ** b,
                'GREATER_THAN': lambda: float(a > b), 'FLOOR': lambda: math.floor(a), 'FRACT': lambda: a - math.floor(a),
                'FLOORED_MODULO': lambda: a - math.floor(a / b) * b}[op]()
    def add(self, a, b): return self.math('ADD', a, b)
    def sub(self, a, b): return self.math('SUBTRACT', a, b)
    def mul(self, a, b): return self.math('MULTIPLY', a, b)
    def div(self, a, b): return self.math('DIVIDE', a, b)
    def clamp(self, a): return min(max(a, 0), 1)
    def split(self, v): return tuple(v[:3])
    def combine(self, x, y, z=0): return (x, y, z)
    def decode_normal(self, tex, strength=1):
        x, y = (tex['r'] * 2 - 1) * strength, (tex['g'] * 2 - 1) * strength
        return (x, y, math.sqrt(max(1 - x * x - y * y, 0)))
    def vector(self, op, a, b=(0, 0, 0), scale=1):
        a, b = tuple(a[:3]), tuple(b[:3])
        return {'SCALE': lambda: tuple(x * scale for x in a), 'ADD': lambda: tuple(map(lambda p, q: p + q, a, b)),
                'SUBTRACT': lambda: tuple(map(lambda p, q: p - q, a, b)), 'MAXIMUM': lambda: tuple(map(max, a, b)),
                'MINIMUM': lambda: tuple(map(min, a, b)), 'DOT_PRODUCT': lambda: sum(p * q for p, q in zip(a, b))}[op]()
    def finish(self, color, roughness=.5, metallic=0, normal=None, specular=.5, emission=None, alpha=1, **_):
        self.result = {'color': color, 'roughness': roughness, 'metallic': metallic, 'normal': normal,
                       'specular': specular, 'alpha': alpha}


def texel(rgb, a):
    return {'color': tuple(rgb), 'r': rgb[0], 'g': rgb[1], 'b': rgb[2], 'a': a}


def build(master, scalars, settings, images, textures, **graph):
    g = NumericGraph(images, **graph)
    decals.build_decal(g, {'master': master, 'scalars': scalars, 'colors': {}, 'switches': {'Use Randomness': True},
                           'textures': textures, 'settings': settings})
    return g


class TranslucentAtlasCaTests(unittest.TestCase):
    def eye(self, rgb, a, **overrides):
        return build(EYE, {**EYE_SCALARS, **overrides}, EYE_SETTINGS, {'CA_EYE': texel(rgb, a)}, {'CA': 'CA_EYE'})

    def test_zero_desaturation_keeps_the_authored_colour(self):
        # Regression: lerping by 1 - Desaturation greyed every decal whose instance left the default 0.
        rgb = (.42, .08, .05)
        g = self.eye(rgb, 1.)
        for got, want in zip(g.result['color'], rgb):
            self.assertAlmostEqual(got, want, places=6)

    def test_colour_roughness_and_opacity_match_the_pixel_shader(self):
        for rgb, a, desat, multiply in (((.42, .08, .05), 1., 0., 1.), ((.2, .5, .9), .6, .5, 1.4), ((.9, .9, .1), .3, 1., .7)):
            with self.subTest(rgb=rgb, desat=desat, multiply=multiply):
                g = self.eye(rgb, a, **{'Color Desaturation': desat, 'Color Multiply': multiply})
                color, rough, mask = atlas_reference(rgb, rgb[0], a, desat, multiply, .7, 3.272468, 13.790995)
                for got, want in zip(g.result['color'], color):
                    self.assertAlmostEqual(got, want, places=6)
                self.assertAlmostEqual(g.result['roughness'], rough, places=6)
                self.assertAlmostEqual(g.result['alpha'], dither_coverage(mask, .3333), places=3)
                self.assertEqual((g.result['metallic'], g.result['specular']), (0, .5))

    def test_roughness_bias_and_contrast_shape_the_red_channel(self):
        for red, bias, contrast in ((.3, 1., 0.), (.7, 1., .25), (.1, .2, .8)):
            with self.subTest(red=red, bias=bias, contrast=contrast):
                g = self.eye((red, .2, .2), 1., **{'Roughness Bias': bias, 'Roughness Contrast': contrast})
                _, rough, _ = atlas_reference((red, .2, .2), red, 1., 0., 1., .7, contrast, bias)
                self.assertAlmostEqual(g.result['roughness'], rough, places=6)

    def test_unset_columns_use_the_master_two_cell_atlas(self):
        scalars = {k: v for k, v in EYE_SCALARS.items() if k not in ('Columns', 'Rows')}
        g = build(EYE, scalars, EYE_SETTINGS, {'CA_EYE': texel((.5, .5, .5), 1.)}, {'CA': 'CA_EYE'})
        ref, uv = g.requested[0]
        self.assertAlmostEqual(uv[0], .3 / 2, places=6)
        self.assertAlmostEqual(uv[1], .6, places=6)

    def test_actor_position_selects_the_cell(self):
        # Blender (x, y, z) m is Unreal (x, -y, z) cm: 5 * (30 + 4 + 1) = 175 -> cell 175 mod 4 = 3 (column 1, row 1).
        scalars = {**EYE_SCALARS, 'Columns': 2., 'Rows': 2., 'Tiling U': 3., 'Tiling V': 1.}
        g = build(EYE, scalars, EYE_SETTINGS, {'CA_EYE': texel((.5, .5, .5), 1.)}, {'CA': 'CA_EYE'},
                  location=(.3, -.04, .01))
        _, uv = g.requested[0]
        u = .3 * 3
        self.assertAlmostEqual(uv[0], (u - math.floor(u) + 1) / 2, places=6)
        self.assertAlmostEqual(uv[1], (.6 + 1) / 2, places=6)

    def test_dither_off_keeps_the_binary_cutoff(self):
        settings = {**EYE_SETTINGS, 'DitherOpacityMask': False}
        for a, want in ((.4, 0.), (.6, 1.)):
            g = build(EYE, EYE_SCALARS, settings, {'CA_EYE': texel((.5, .5, .5), a)}, {'CA': 'CA_EYE'})
            self.assertEqual(g.result['alpha'], want)


class MaskedAtlasCaNorTests(unittest.TestCase):
    def test_nor_supplies_normal_and_roughness(self):
        rgb, a = (.6, .3, .2), .9
        images = {'CA_N': texel(rgb, a), 'NOR_N': {'r': .75, 'g': .4, 'b': 1., 'a': .35}}
        scalars = {'Columns': 1., 'Rows': 1., 'Color Desaturation': .25, 'Color Multiply': 1.2, 'Opacity Amount': .8}
        g = build(NOR, scalars, {'BlendMode': 'EBlendMode::BLEND_Masked', 'OpacityMaskClipValue': .3333,
                                 'DitherOpacityMask': True}, images, {'CA': 'CA_N', 'NOR': 'NOR_N'})
        color, _, mask = atlas_reference(rgb, rgb[0], a, .25, 1.2, .8)
        for got, want in zip(g.result['color'], color):
            self.assertAlmostEqual(got, want, places=6)
        self.assertAlmostEqual(g.result['roughness'], .35, places=6)
        x, y = .5, -.2
        for got, want in zip(g.result['normal'], (x, y, math.sqrt(1 - x * x - y * y))):
            self.assertAlmostEqual(got, want, places=6)
        self.assertAlmostEqual(g.result['alpha'], dither_coverage(mask, .3333), places=3)


class TranslucentCaDetailTests(unittest.TestCase):
    def test_breakup_erodes_opacity_and_averages_roughness(self):
        for a, red, green, contrast, power, roughness in ((1., .4, .2, .5, .5, .5), (.8, .9, .7, .1, 2., .3), (.6, 0., .5, .5, .5, .8)):
            with self.subTest(a=a, red=red, contrast=contrast, power=power):
                images = {'CA_D': texel((.3, .4, .5), a), decals.CA_DETAIL_GRUNGE: {'r': red, 'g': green, 'b': 0., 'a': 1.}}
                g = build(CA_DETAIL, {'Contrast': contrast, 'Power': power, 'Roughness': roughness},
                          {'BlendMode': 'EBlendMode::BLEND_Masked', 'OpacityMaskClipValue': .3333}, images, {'CA': 'CA_D'})
                erosion = 0. if red <= 1.19e-7 else red ** power * (1 + 2 * contrast)
                opacity = sat(a - sat(erosion - contrast))
                self.assertEqual(g.result['alpha'], float(opacity > .3333))
                self.assertAlmostEqual(g.result['roughness'], sat((green + roughness) / 2), places=6)
                self.assertEqual(g.result['color'], (.3, .4, .5))

    def test_breakup_tiling_scales_uv0(self):
        images = {'CA_D': texel((.3, .4, .5), 1.), decals.CA_DETAIL_GRUNGE: {'r': .5, 'g': .5, 'b': 0., 'a': 1.}}
        g = build(CA_DETAIL, {'Grunge Tiling': 4.}, {'BlendMode': 'EBlendMode::BLEND_Masked'}, images, {'CA': 'CA_D'})
        self.assertEqual(dict(g.requested)[decals.CA_DETAIL_GRUNGE], (.3 * 4, .6 * 4, 0.))


if __name__ == '__main__':
    unittest.main()
