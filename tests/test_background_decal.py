"""M_BackgroundDecal graph equals the game's SM5 translucent base-pass PS (no Blender).

The reference is transcribed from the master's shader map CD00082E (build 26159202): t6 is the fixed
T_GrungeAtlas_Props_A, sampled at TEXCOORD0 and read through its red channel; cb2[1] holds
(Grunge color.rgb, Opacity Strength); colour and opacity are saturate(mask * cb2[1]).
"""
import ast
import importlib.util
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_background_decal', ROOT / 'surface_rebuild/background_decal.py')
background_decal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(background_decal)

GANTRY_GRUNGE_A = {'scalars': {'Opacity Strength': .7}, 'colors': {'Grunge color': (.098958, .076815, .061592, 1.)}}


def shader_reference(mask, grunge, opacity):
    """PS instruction 13 (mul_sat r4, t6.r, cb2[1]) and the o0.w write."""
    color = tuple(min(max(mask * c, 0.), 1.) for c in grunge[:3])
    return color, min(max(mask * opacity, 0.), 1.)


class NumericGraph:
    """Eager stand-in for material_runtime.interop.Graph: sockets are floats and 3-tuples."""
    def __init__(self, images):
        self.report = {'warnings': []}
        self.images, self.result, self.requested = images, None, []

    @contextmanager
    def block(self, label): yield self
    def parameter(self, label, value, typ='Float'): return tuple(value) if isinstance(value, (tuple, list)) else float(value)
    def uv(self, channel=0): return 'UV%d' % channel
    def image(self, ref, uv=None, layer=None, srgb=None, label='', required=True):
        self.requested.append((ref, uv))
        return self.images.get(ref)
    def math(self, op, a, b=0, c=0, label=''):
        return {'MULTIPLY': lambda: a * b, 'MINIMUM': lambda: min(a, b), 'MAXIMUM': lambda: max(a, b)}[op]()
    def mul(self, a, b): return self.math('MULTIPLY', a, b)
    def clamp(self, a): return min(max(a, 0), 1)
    def vector(self, op, a, b=(0, 0, 0), scale=1):
        a, b = tuple(a[:3]), tuple(b[:3])
        return {'SCALE': lambda: tuple(x * scale for x in a), 'MAXIMUM': lambda: tuple(map(max, a, b)),
                'MINIMUM': lambda: tuple(map(min, a, b))}[op]()
    def finish(self, color, roughness=.5, metallic=0, normal=None, specular=.5, emission=None, alpha=1, **_):
        self.result = {'color': color, 'roughness': roughness, 'metallic': metallic, 'specular': specular, 'alpha': alpha}


class BackgroundDecalShaderTests(unittest.TestCase):
    def build(self, mask, record=GANTRY_GRUNGE_A, textures=None):
        images = {background_decal.ATLAS: {'r': mask, 'g': 0., 'b': 0., 'a': 1.}}
        g = NumericGraph(images)
        background_decal.build_background_decal(g, {**record, 'textures': textures or {}, 'switches': {}})
        return g

    def test_mask_scales_grunge_colour_and_opacity_like_the_game_shader(self):
        for mask in (0., .1, .5, .93, 1.):
            with self.subTest(mask=mask):
                g = self.build(mask)
                color, alpha = shader_reference(mask, GANTRY_GRUNGE_A['colors']['Grunge color'], .7)
                for got, want in zip(g.result['color'], color):
                    self.assertAlmostEqual(got, want, places=6)
                self.assertAlmostEqual(g.result['alpha'], alpha, places=6)
                self.assertAlmostEqual(g.result['roughness'], mask, places=6)
                self.assertEqual((g.result['metallic'], g.result['specular']), (0, .5))

    def test_master_defaults_apply_when_the_instance_overrides_nothing(self):
        g = self.build(.6, record={'scalars': {}, 'colors': {}})
        color, alpha = shader_reference(.6, background_decal.GRUNGE, background_decal.OPACITY)
        self.assertEqual(g.result['color'], color)
        self.assertAlmostEqual(g.result['alpha'], alpha, places=6)

    def test_fixed_atlas_is_sampled_at_uv0(self):
        g = self.build(.5)
        self.assertEqual(g.requested, [(background_decal.ATLAS, 'UV0')])

    def test_missing_atlas_hides_the_overlay_instead_of_painting_an_opaque_card(self):
        g = NumericGraph({})
        background_decal.build_background_decal(g, {**GANTRY_GRUNGE_A, 'textures': {}, 'switches': {}})
        self.assertEqual(g.result['alpha'], 0)
        self.assertTrue(any('missing' in w for w in g.report['warnings']))

    def test_master_routes_to_the_background_decal_family(self):
        tree = ast.parse((ROOT / 'surface_rebuild/records.py').read_text(encoding='utf-8'))
        body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'classify']
        package = types.ModuleType('_bgdecal_pkg'); package.__path__ = []
        sys.modules['_bgdecal_pkg'] = package
        sys.modules['_bgdecal_pkg.head_records'] = SimpleNamespace(family=lambda record: None)
        try:
            ns = {'__name__': '_bgdecal_pkg.records', '__package__': '_bgdecal_pkg'}
            exec(compile(ast.Module(body=body, type_ignores=[]), '<records>', 'exec'), ns)
            record = {'master': 'PioneerGame/Content/Pioneer/MaterialLibrary/M_Presets/M_BackgroundDecal', 'colors': {}, 'textures': {}}
            self.assertEqual(ns['classify'](record), 'background_decal')
        finally:
            sys.modules.pop('_bgdecal_pkg.head_records', None); sys.modules.pop('_bgdecal_pkg', None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
