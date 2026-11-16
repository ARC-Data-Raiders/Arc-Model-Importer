import importlib
import unittest

from .fake_graph import FakeGraph, rebuild
from .native_test_support import PACKAGE, namespace
from .test_generic_families import rec

namespace()
rules = importlib.import_module(PACKAGE + '.Shared.materials.audit_param_rules')
MASTER = '/Game/Pioneer/MaterialLibrary/Materials/Themes/Residential/M_CR_NOH_Parallax_VTblend_01.M_CR_NOH_Parallax_VTblend_01'
PARAMETER = '1. Wear NOH'


class VTBlendWearGateTests(unittest.TestCase):
    def facts(self, enabled, overridden=False, master=MASTER):
        return {'master': master,
                'textures': {PARAMETER: {'path': '/Game/T_Metal_Rusted_04_NOH', 'overridden': overridden}},
                'switches': {} if enabled is None else {
                    'Use Rust': {'value': enabled, 'overridden': True}}}

    def test_disabled_default_matches_real_builder(self):
        graph = FakeGraph()
        rebuild('generic_families').build_vt_blend(
            graph, rec(MASTER, ['CR_1', 'NOH_1', PARAMETER], switches={'Use Rust': False}))
        self.assertNotIn(PARAMETER, graph.images)
        flag, severity, detail = rules.classify_unlinked(PARAMETER, self.facts(False))
        self.assertEqual((flag, severity), ('tex_default_unlinked:' + PARAMETER, 'info'))
        self.assertIs(detail['gate'], False)
        self.assertEqual(detail['switches'][0]['switch'], 'Use Rust')

    def test_enabled_default_is_required(self):
        flag, severity, detail = rules.classify_unlinked(PARAMETER, self.facts(True))
        self.assertEqual((flag, severity), ('tex_param_unlinked:' + PARAMETER, 'defect'))
        self.assertIs(detail['gate'], True)

    def test_disabled_override_is_gated_off(self):
        flag, severity, detail = rules.classify_unlinked(PARAMETER, self.facts(False, True))
        self.assertEqual((flag, severity), ('tex_gated_off:' + PARAMETER, 'info'))
        self.assertIs(detail['gate'], False)

    def test_missing_switch_keeps_unknown(self):
        flag, _, detail = rules.classify_unlinked(PARAMETER, self.facts(None))
        self.assertEqual(flag, 'tex_default_unlinked:' + PARAMETER)
        self.assertIsNone(detail['gate'])

    def test_other_master_keeps_unknown(self):
        flag, _, detail = rules.classify_unlinked(PARAMETER, self.facts(False, master='/Game/M_Other'))
        self.assertEqual(flag, 'tex_default_unlinked:' + PARAMETER)
        self.assertIsNone(detail['gate'])

    def test_enabled_builder_gap_remains_a_defect(self):
        graph = FakeGraph()
        rebuild('generic_families').build_vt_blend(
            graph, rec(MASTER, ['CR_1', 'NOH_1', PARAMETER], switches={'Use Rust': True}))
        self.assertNotIn(PARAMETER, graph.images)
        flag, severity, detail = rules.classify_unlinked(PARAMETER, self.facts(True, True))
        self.assertEqual((flag, severity), ('tex_param_unlinked:' + PARAMETER, 'defect'))
        self.assertIs(detail['gate'], True)


if __name__ == '__main__':
    unittest.main()
