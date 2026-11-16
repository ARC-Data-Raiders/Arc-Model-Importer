import importlib
import unittest

from .fake_graph import FakeGraph, rebuild
from .native_test_support import PACKAGE, namespace
from .test_generic_families import rec

namespace()
rules = importlib.import_module(PACKAGE + '.Shared.materials.audit_param_rules')
MASTER = '/Game/Pioneer/MaterialLibrary/M_Presets/M_BlockoutPreset.M_BlockoutPreset'


class BlockoutDamageGateTests(unittest.TestCase):
    def facts(self, enabled, overridden=False, master=MASTER):
        return {'master': master,
                'textures': {name: {'path': '/Game/T_' + name, 'overridden': overridden}
                             for name in ('DamageNormal', 'DamageMask')},
                'switches': {} if enabled is None else {
                    'EnableDamage': {'value': enabled, 'overridden': True}}}

    def test_disabled_default_matches_real_builder(self):
        graph = FakeGraph()
        rebuild('generic_families').build_blockout(
            graph, rec(MASTER, ['DamageNormal', 'DamageMask'], switches={'EnableDamage': False}))
        self.assertNotIn('DamageNormal', graph.images)
        self.assertIsNone(graph.out['normal'])
        for parameter in ('DamageNormal', 'DamageMask'):
            flag, severity, detail = rules.classify_unlinked(parameter, self.facts(False))
            self.assertEqual((flag, severity), ('tex_default_unlinked:' + parameter, 'info'))
            self.assertIs(detail['gate'], False)
            self.assertEqual(detail['switches'][0]['switch'], 'EnableDamage')

    def test_enabled_default_is_required(self):
        for parameter in ('DamageNormal', 'DamageMask'):
            flag, severity, detail = rules.classify_unlinked(parameter, self.facts(True))
            self.assertEqual((flag, severity), ('tex_param_unlinked:' + parameter, 'defect'))
            self.assertIs(detail['gate'], True)

    def test_disabled_override_is_gated_off(self):
        flag, severity, detail = rules.classify_unlinked('DamageNormal', self.facts(False, True))
        self.assertEqual((flag, severity), ('tex_gated_off:DamageNormal', 'info'))
        self.assertIs(detail['gate'], False)

    def test_missing_switch_keeps_unknown(self):
        flag, _, detail = rules.classify_unlinked('DamageNormal', self.facts(None))
        self.assertEqual(flag, 'tex_default_unlinked:DamageNormal')
        self.assertIsNone(detail['gate'])

    def test_other_master_keeps_unknown(self):
        flag, _, detail = rules.classify_unlinked('DamageNormal', self.facts(False, master='/Game/M_Other'))
        self.assertEqual(flag, 'tex_default_unlinked:DamageNormal')
        self.assertIsNone(detail['gate'])

    def test_enabled_builder_still_links_normal(self):
        graph = FakeGraph()
        rebuild('generic_families').build_blockout(
            graph, rec(MASTER, ['DamageNormal', 'DamageMask'], switches={'EnableDamage': True}))
        self.assertIn('DamageNormal', graph.images)
        self.assertIsNotNone(graph.out['normal'])


if __name__ == '__main__':
    unittest.main()
