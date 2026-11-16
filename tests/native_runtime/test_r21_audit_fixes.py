"""R21: checker rules keyed on master / switch / param name. Pure facts, no Blender."""
import importlib
import unittest

from .native_test_support import PACKAGE, namespace

namespace()
rules = importlib.import_module(PACKAGE + '.Shared.materials.audit_param_rules')

LIGHTBULB = '/Game/Pioneer/Lighting/Materials/Lightfixtures/M_MasterLight_Lightbulb_01.M_MasterLight_Lightbulb_01'
XEM = '/Game/Pioneer/MaterialLibrary/M_Presets/M_PropPreset_CR+NOX+XEM.M_PropPreset_CR+NOX+XEM'
OTHER = '/Game/Pioneer/MaterialLibrary/M_Presets/M_Other_01.M_Other_01'


def facts(master, textures, switches=None):
    return {'master': master,
            'textures': {k: {'path': p, 'overridden': True} for k, p in textures.items()},
            'switches': {k: {'value': v, 'overridden': True} for k, v in (switches or {}).items()}}


class MasterUnread(unittest.TestCase):
    def test_lightbulb_cr_unread_is_info(self):
        f = facts(LIGHTBULB, {'CR': '/Game/T/T_Bulb_CR.T_Bulb_CR', 'Lightbulb': '/Game/T/T_Bulb_L.T_Bulb_L'})
        flag, sev, detail = rules.classify_unlinked('CR', f)
        self.assertEqual((flag, sev), ('tex_unread_unlinked:CR', rules.INFO))
        self.assertIn('83C059E2', detail['reason'])
        self.assertEqual(rules.severity(flag), rules.INFO)

    def test_lightbulb_sampled_param_keeps_defect(self):
        f = facts(LIGHTBULB, {'Lightbulb': '/Game/T/T_Bulb_L.T_Bulb_L'})
        self.assertEqual(rules.classify_unlinked('Lightbulb', f)[1], rules.DEFECT)

    def test_other_master_cr_keeps_defect(self):
        f = facts(OTHER, {'CR': '/Game/T/T_Bulb_CR.T_Bulb_CR'})
        self.assertNotEqual(rules.classify_unlinked('CR', f)[0], 'tex_unread_unlinked:CR')


class LayoutFollowsUnlinked(unittest.TestCase):
    def test_gated_off_packed_mask_layout_is_info(self):
        f = facts(XEM, {'Packed Mask': '/Game/T/T_Box_XAM.T_Box_XAM'},
                  {'Use Masks': False, 'Use single Mask Texture': False})
        self.assertEqual(rules.classify_unlinked('Packed Mask', f)[0], 'tex_gated_off:Packed Mask')
        self.assertEqual(rules.layout_severity('Packed Mask', f, False), rules.INFO)

    def test_gate_on_packed_mask_layout_stays_defect(self):
        f = facts(XEM, {'Packed Mask': '/Game/T/T_Box_XAM.T_Box_XAM'},
                  {'Use Masks': True, 'Use single Mask Texture': False})
        self.assertEqual(rules.layout_severity('Packed Mask', f, False), rules.DEFECT)

    def test_linked_stays_defect(self):
        f = facts(XEM, {'Packed Mask': '/Game/T/T_Box_XAM.T_Box_XAM'}, {'Use Masks': False})
        self.assertEqual(rules.layout_severity('Packed Mask', f, True), rules.DEFECT)

    def test_stem_duplicate_layout_is_info(self):
        f = facts(OTHER, {'Mask': '/Game/T/T_Box_XAM.T_Box_XAM', 'T_Box_XAM': '/Game/T/T_Box_XAM.T_Box_XAM'})
        self.assertEqual(rules.layout_severity('T_Box_XAM', f, False), rules.INFO)

    def test_explicit_param_sev_wins(self):
        f = facts(XEM, {'Packed Mask': '/Game/T/T_Box_XAM.T_Box_XAM'}, {'Use Masks': False})
        self.assertEqual(rules.layout_severity('Packed Mask', f, False, rules.DEFECT), rules.DEFECT)


class UvClamped(unittest.TestCase):
    def test_uv_clamped_is_info(self):
        self.assertEqual(rules.severity('uv_clamped:1'), rules.INFO)


class PackedMaskSwitch(unittest.TestCase):
    def test_dont_use_packed_mask_is_handled(self):
        self.assertEqual(rules.switch_class("Don't use Packed Mask"), ('handled', rules.INFO))
        self.assertEqual(rules.switch_class("Global.Don't use Packed Mask"), ('handled', rules.INFO))
        self.assertEqual(rules.severity("switch_handled:Don't use Packed Mask"), rules.INFO)

    def test_other_packed_switch_not_handled(self):
        self.assertNotEqual(rules.switch_class('Use Packed Tint Mask')[0], 'handled')
        self.assertEqual(rules.switch_class('Use Packed Mask Texture'), (None, None))


if __name__ == '__main__':
    unittest.main()
