"""R30: Prop AO Texture with Use Prop AO Dirt off on PropTrim masters is info ao_pin_only. Pure facts, no Blender."""
import importlib
import unittest

from .native_test_support import PACKAGE, namespace

namespace()
rules = importlib.import_module(PACKAGE + '.Shared.materials.audit_param_rules')
TRIM = '/Game/Materials/M_PropTrimPreset_CR+NOM+AO.M_PropTrimPreset_CR+NOM+AO'


def facts(master, overridden, dirt):
    return {'master': master, 'textures': {'Prop AO Texture': {'path': '/Game/T_AO.T_AO', 'overridden': overridden}},
            'switches': {'Use Prop AO Dirt': {'value': dirt, 'overridden': True}}}


class AoPinOnly(unittest.TestCase):
    def test_switch_off_is_ao_pin_only_info(self):
        for overridden in (False, True):
            flag, sev, detail = rules.classify_unlinked('Prop AO Texture', facts(TRIM, overridden, False))
            self.assertEqual((flag, sev), ('ao_pin_only:Prop AO Texture', rules.INFO))
            self.assertIn('constant 1.0', detail['reason'])

    def test_switch_on_stays_defect(self):
        flag, sev, _ = rules.classify_unlinked('Prop AO Texture', facts(TRIM, True, True))
        self.assertEqual((flag, sev), ('tex_param_unlinked:Prop AO Texture', rules.DEFECT))

    def test_other_master_keeps_gated_off(self):
        flag, _, _ = rules.classify_unlinked('Prop AO Texture', facts('/Game/M_Other.M_Other', True, False))
        self.assertEqual(flag, 'tex_gated_off:Prop AO Texture')

    def test_severity_registered(self):
        self.assertEqual(rules.SEVERITY['ao_pin_only'], rules.INFO)


if __name__ == '__main__':
    unittest.main()
