"""Synthetic alpha-routing controls against the actual surface builder, without Blender."""
import unittest

from native_runtime.test_surface_layer_stages import FakeGraph, load_surfaces, record


class AlphaGraph(FakeGraph):
    def image(self, *args, **kwargs):
        texture = super().image(*args, **kwargs)
        if texture:
            texture['a'] = 0.25
        return texture

    def finish(self, *args, **kwargs):
        super().finish(*args, **kwargs)
        self.alpha = kwargs['alpha']


class SurfaceOpaqueContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.surfaces = load_surfaces()

    def alpha(self, mode, serialized=True):
        material = record('M_AlphaControl', {'BaseColor': '/Game/Tests/T_Control_CA'})
        if serialized:
            material['settings']['BlendMode'] = mode
        graph = AlphaGraph()
        self.surfaces.build_surface(graph, material)
        return graph.alpha

    def test_explicit_opaque_ignores_sample_alpha(self):
        for mode in (0, '0', 'BLEND_Opaque', 'EBlendMode::BLEND_Opaque', 'opaque', 'Opaque'):
            with self.subTest(mode=mode):
                self.assertEqual(self.alpha(mode), 1)

    def test_masked_still_clips_sample_alpha(self):
        for mode in (1, '1', 'BLEND_Masked', 'EBlendMode::BLEND_Masked'):
            with self.subTest(mode=mode):
                self.assertIn('GREATER_THAN', str(self.alpha(mode)))
                self.assertIn('0.25', str(self.alpha(mode)))

    def test_translucent_preserves_sample_alpha(self):
        for mode in (2, '2', 'BLEND_Translucent', 'EBlendMode::BLEND_Translucent'):
            with self.subTest(mode=mode):
                self.assertEqual(self.alpha(mode), 0.25)

    def test_other_blend_is_not_forced_opaque(self):
        self.assertEqual(self.alpha('BLEND_Additive'), 0.25)

    def test_unknown_blend_is_not_forced_opaque(self):
        self.assertEqual(self.alpha('unrecognized'), 0.25)
        self.assertEqual(self.alpha(None, serialized=False), 0.25)


if __name__ == '__main__':
    unittest.main()
