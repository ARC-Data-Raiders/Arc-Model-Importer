"""'BaseColor/Mask' names RGB colour + alpha mask in rebuild/surfaces.gather (fake Graph, no Blender)."""
import unittest

from native_runtime.test_surface_layer_stages import FakeGraph, load_surfaces


class CompositeColorParamTest(unittest.TestCase):
    def test_base_color_mask_feeds_color_only(self):
        S = load_surfaces()
        g = FakeGraph()
        r = {'master': '/Game/M_TarmacSimple_01', 'scalars': {}, 'colors': {}, 'switches': {},
             'textures': {'BaseColor/Mask': '/Game/T_Tarmac_01_Highway_CM', 'Normal/AO/Height': '/Game/T_Tarmac_01_Highway_NOH'}}
        surf = S.gather(g, r)
        self.assertIn('/Game/T_Tarmac_01_Highway_CM', g.sampled)
        self.assertEqual(surf['color'], ('op', 'tex', '/Game/T_Tarmac_01_Highway_CM', 'color'))
        self.assertEqual(surf['metal'], 0)

    def test_plain_mask_still_skipped(self):
        S = load_surfaces()
        g = FakeGraph()
        S.gather(g, {'master': '/Game/M_X', 'scalars': {}, 'colors': {}, 'switches': {},
                     'textures': {'Breakup Mask': '/Game/T_Breakup_M'}})
        self.assertNotIn('/Game/T_Breakup_M', g.sampled)


if __name__ == '__main__':
    unittest.main()
