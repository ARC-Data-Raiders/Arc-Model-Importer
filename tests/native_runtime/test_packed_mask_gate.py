"""CR+NOM "Don't use Packed Mask" switch in rebuild/surfaces.gather (fake Graph, no Blender)."""
import unittest

from native_runtime.test_surface_layer_stages import FakeGraph, load_surfaces


def record(switch=None, mask=True, master='/Game/M_PropPreset_CR+NOM'):
    textures = {'CR': '/Game/T_A_CR', 'Normal': '/Game/T_A_N'}
    if mask:
        textures['Packed Mask'] = '/Game/T_A_M'
    switches = {} if switch is None else {"Don't use Packed Mask": switch}
    return {'master': master, 'textures': textures, 'switches': switches, 'scalars': {}, 'colors': {}}


class PackedMaskGateTest(unittest.TestCase):
    def setUp(self):
        self.S = load_surfaces()

    def run_gather(self, r):
        g = FakeGraph()
        self.S.gather(g, r)
        return g

    def test_switch_on_is_built_path(self):
        g = self.run_gather(record(True))
        self.assertIn('packed mask off (CR+NOM)', g.report.get('implemented_stages', []))
        self.assertNotIn('/Game/T_A_M', g.sampled)
        self.assertFalse(any('Packed Mask' in n for n in g.report['notes']))

    def test_switch_off_with_mask_is_approximate(self):
        g = self.run_gather(record(False))
        self.assertTrue(any(n.startswith('approximate: CR+NOM Packed Mask') for n in g.report['notes']))
        self.assertNotIn('packed mask off (CR+NOM)', g.report.get('implemented_stages', []))

    def test_xem_master_untouched(self):
        g = self.run_gather(record(True, master='/Game/M_PropPreset_CR+NOX+XEM'))
        self.assertNotIn('packed mask off (CR+NOM)', g.report.get('implemented_stages', []))


if __name__ == '__main__':
    unittest.main()
