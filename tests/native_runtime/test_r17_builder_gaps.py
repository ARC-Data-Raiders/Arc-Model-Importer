"""R17: builder gaps from uncertain_reasons.md (Translucent_Atlas routing, Parallax decal, VTblend detail, Metal wear, Mural mask)."""
import ast
import os
import re
import sys
import types
import unittest
from types import SimpleNamespace
try:
    from .fake_graph import FakeGraph, rebuild
    from .native_test_support import ROOT
    from .test_surface_layer_stages import FakeGraph as SurfGraph, load_surfaces, record
except ImportError:
    from fake_graph import FakeGraph, rebuild
    from native_test_support import ROOT
    from test_surface_layer_stages import FakeGraph as SurfGraph, load_surfaces, record

LIB = '/Game/Pioneer/MaterialLibrary/M_Presets/'


class RnmFake(FakeGraph):
    def __init__(self):
        super().__init__(); self.rnm_calls = 0

    def rnm(self, a, b):
        self.rnm_calls += 1
        return self._f('RNM', a, b)


class RnmSurf(SurfGraph):
    def __init__(self, uv_layers=1):
        super().__init__(uv_layers); self.rnm_calls = 0; self.alpha = None

    def rnm(self, a, b):
        self.rnm_calls += 1
        return super().rnm(a, b)

    def finish(self, color, rough=None, metal=None, normal=None, spec=None, emission=None, **kw):
        super().finish(color, rough, metal, normal, spec, emission, **kw); self.alpha = kw.get('alpha')


class TranslucentAtlasRouting(unittest.TestCase):
    @staticmethod
    def classify_ns():
        """classify.py imports bpy at module level; exec its top-level defs without the imports (as test_background_decal)."""
        tree = ast.parse((ROOT / 'Shared/materials/classify.py').read_text(encoding='utf-8'))
        tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
        pkg = types.ModuleType('_r17_classify'); pkg.__path__ = []
        sys.modules['_r17_classify'] = pkg
        sys.modules['_r17_classify.enemy'] = SimpleNamespace(_is_enemy_scan_display_mi=lambda *a: False)
        ns = {'re': re, 'os': os, '__name__': '_r17_classify.classify', '__package__': '_r17_classify'}
        exec(compile(tree, 'classify.py', 'exec'), ns)
        return ns

    def test_light_token_mi_routes_to_decal(self):
        ns = self.classify_ns()
        try:
            master = LIB + 'M_DecalPreset_Translucent_Atlas+CA+Detail.M_DecalPreset_Translucent_Atlas+CA+Detail'
            mi = {'parent': master, 'textures': [('CA', '/Game/T_X_C')]}
            self.assertEqual(ns['classify_mi_family'](mi, 'mi_decal_leaks_01_light'), ns['FAMILY_DECAL'])
        finally:
            sys.modules.pop('_r17_classify.enemy', None); sys.modules.pop('_r17_classify', None)


class ParallaxDecal(unittest.TestCase):
    decals = rebuild('decals')

    def test_basecolor_normal_alpha(self):
        g = RnmFake()
        r = {'master': LIB + 'M_DecalPreset_Parallax_CA+NOH', 'name': 'MI_Test',
             'textures': {'BaseColor': '/Game/T/T_D_CA', 'Normal': '/Game/T/T_D_NOH'}, 'scalars': {},
             'colors': {}, 'switches': {'Use NormalMap': True, 'Use Parallax': True},
             'settings': {'BlendMode': 'BLEND_Translucent'}}
        self.decals.build_decal(g, r)
        self.assertIn('Decal BaseColor and alpha', g.images)
        self.assertIn('Decal NOH normal', g.images)
        self.assertIn('T[Decal BaseColor and alpha].a', str(g.out.get('alpha')))
        self.assertTrue(any(w.startswith('approximate:parallax') for w in g.report['warnings']))


class VtBlendDetailNormal(unittest.TestCase):
    gf = rebuild('generic_families')

    def test_detail_normal_sampled(self):
        g = RnmFake()
        r = {'master': LIB + 'M_CR_NOH_Parallax_VTblend_01', 'name': 'MI_Test',
             'textures': {'CR_1': '/Game/T/A_CR', 'NOH_1': '/Game/T/A_NOH', 'CR_2': '/Game/T/B_CR',
                          'NOH_2': '/Game/T/B_NOH', '4. Mask': '/Game/T/M', 'Detail Normal': '/Game/T/DN'},
             'scalars': {}, 'colors': {}, 'switches': {'Use Detail Normal': True}, 'settings': {}}
        self.gf.build_vt_blend(g, r)
        self.assertIn('Detail Normal', g.images)
        self.assertEqual(g.rnm_calls, 1)


class MetalWear(unittest.TestCase):
    surfaces = load_surfaces()
    TEX = {'CR': '/Game/T/M_CR', 'NOH': '/Game/T/M_NOH', 'Masks': '/Game/T/M_Masks', 'Detail Normal': '/Game/T/DN',
           'Detail Normal Worn': '/Game/T/DNW'}

    def test_masks_and_worn_detail(self):
        g = RnmSurf()
        self.surfaces.build_architecture(g, record('M_ArchitecturePreset_Metal', self.TEX, {'Use Detail Normal': True},
                                                   {'4.Detail Wear Tile': 4, '2.Detail Wear Normal Intensity': 2}))
        self.assertIn('/Game/T/M_Masks', g.sampled)
        self.assertIn('/Game/T/DNW', g.sampled)
        self.assertEqual(g.rnm_calls, 2)

    def test_worn_off_without_switch(self):
        g = RnmSurf()
        self.surfaces.build_architecture(g, record('M_ArchitecturePreset_Metal', self.TEX))
        self.assertNotIn('/Game/T/DNW', g.sampled)


class MuralMask(unittest.TestCase):
    surfaces = load_surfaces()

    def test_mask_in_alpha(self):
        g = RnmSurf()
        self.surfaces.build_surface(g, record('M_MuralPreset_Masked_NXX+X', {'Normal': '/Game/T/N', 'Mask': '/Game/T/MK'},
                                              scalars={'MaskTiling_U': 3}))
        self.assertIn('/Game/T/MK', g.sampled)
        self.assertIn('/Game/T/MK', str(g.alpha))


if __name__ == '__main__':
    unittest.main()
