import importlib
import unittest
from unittest.mock import MagicMock

from .fake_graph import FakeGraph, rebuild
from .native_test_support import PACKAGE, namespace
from .test_generic_families import rec

namespace()
rules = importlib.import_module(PACKAGE + '.Shared.materials.audit_param_rules')
MASTER = '/Game/Pioneer/MaterialLibrary/M_Presets/M_TrimPreset_Rusty.M_TrimPreset_Rusty'


class OptionalNormalGateTests(unittest.TestCase):
    def facts(self, enabled, overridden=False, master=MASTER):
        return {'master': master,
                'textures': {'Base Normal': {'path': '/Game/T_Base_NOH', 'overridden': overridden}},
                'switches': {} if enabled is None else {'UseNormalMap': {'value': enabled, 'overridden': False}}}

    def test_disabled_default_normal_matches_the_real_builder(self):
        graph = FakeGraph()
        rebuild('generic_families').build_rusty_trim(
            graph, rec(MASTER, ['Base Texture', 'Base Normal', 'Overlay'], switches={'UseNormalMap': False}))
        self.assertNotIn('Base Normal', graph.images)
        self.assertIsNone(graph.out['normal'])
        flag, severity, detail = rules.classify_unlinked('Base Normal', self.facts(False))
        self.assertEqual((flag, severity), ('tex_default_unlinked:Base Normal', 'info'))
        self.assertFalse(detail['gate'])
        self.assertEqual(detail['switches'][0]['switch'], 'UseNormalMap')

    def test_enabled_default_normal_is_still_required(self):
        flag, severity, detail = rules.classify_unlinked('Base Normal', self.facts(True))
        self.assertEqual((flag, severity), ('tex_param_unlinked:Base Normal', 'defect'))
        self.assertTrue(detail['gate'])

    def test_disabled_override_has_the_same_gate(self):
        flag, severity, _ = rules.classify_unlinked('Base Normal', self.facts(False, overridden=True))
        self.assertEqual((flag, severity), ('tex_gated_off:Base Normal', 'info'))

    def test_missing_switch_does_not_claim_disabled(self):
        flag, _, detail = rules.classify_unlinked('Base Normal', self.facts(None))
        self.assertEqual(flag, 'tex_default_unlinked:Base Normal')
        self.assertIsNone(detail['gate'])

    def test_other_master_is_not_suppressed(self):
        flag, _, detail = rules.classify_unlinked('Base Normal', self.facts(False, master='/Game/M_Other'))
        self.assertEqual(flag, 'tex_default_unlinked:Base Normal')
        self.assertIsNone(detail['gate'])

    def test_enabled_builder_still_links_authored_normal(self):
        graph = FakeGraph()
        rebuild('generic_families').build_rusty_trim(
            graph, rec(MASTER, ['Base Texture', 'Base Normal', 'Overlay'], switches={'UseNormalMap': True}))
        self.assertIn('Base Normal', graph.images)
        self.assertIsNotNone(graph.out['normal'])


class GraphicAtlasNormalGateTests(unittest.TestCase):
    master = '/Game/Pioneer/MaterialLibrary/M_Presets/M_Graphic_Atlas_01.M_Graphic_Atlas_01'

    def facts(self, enabled, overridden=False, master=None):
        return {'master': master or self.master,
                'textures': {'NX': {'path': '/Game/T_Default_01_NX', 'overridden': overridden}},
                'switches': {} if enabled is None else {'Use Normal (NX)': {'value': enabled}}}

    def test_disabled_default_matches_real_builder(self):
        graph = FakeGraph()
        rebuild('generic_families').build_graphic_atlas(
            graph, rec(self.master, ['Graphic Atlas', 'NX'], switches={'Use Normal (NX)': False}))
        self.assertNotIn('NX', graph.images)
        self.assertIsNone(graph.out['normal'])
        flag, severity, detail = rules.classify_unlinked('NX', self.facts(False))
        self.assertEqual((flag, severity), ('tex_default_unlinked:NX', 'info'))
        self.assertFalse(detail['gate'])
        self.assertEqual(detail['switches'][0]['switch'], 'Use Normal (NX)')

    def test_enabled_default_remains_required(self):
        flag, severity, detail = rules.classify_unlinked('NX', self.facts(True))
        self.assertEqual((flag, severity), ('tex_param_unlinked:NX', 'defect'))
        self.assertTrue(detail['gate'])

    def test_disabled_override_uses_same_gate(self):
        flag, severity, detail = rules.classify_unlinked('NX', self.facts(False, overridden=True))
        self.assertEqual((flag, severity), ('tex_gated_off:NX', 'info'))
        self.assertFalse(detail['gate'])

    def test_missing_switch_cannot_disable_normal(self):
        flag, _, detail = rules.classify_unlinked('NX', self.facts(None))
        self.assertEqual(flag, 'tex_default_unlinked:NX')
        self.assertIsNone(detail['gate'])

    def test_other_master_is_not_suppressed(self):
        flag, _, detail = rules.classify_unlinked('NX', self.facts(False, master='/Game/M_Other'))
        self.assertEqual(flag, 'tex_default_unlinked:NX')
        self.assertIsNone(detail['gate'])

    def test_enabled_builder_links_normal(self):
        graph = FakeGraph()
        rebuild('generic_families').build_graphic_atlas(
            graph, rec(self.master, ['Graphic Atlas', 'NX'], switches={'Use Normal (NX)': True}))
        self.assertIn('NX', graph.images)
        self.assertIsNotNone(graph.out['normal'])


class ArchitectureWearNormalGateTests(unittest.TestCase):
    master = '/Game/Pioneer/MaterialLibrary/M_Presets/M_ArchitecturePreset_CR+NOM+Detail.M_ArchitecturePreset_CR+NOM+Detail'

    def facts(self, enabled, overridden=False, master=None):
        switches = {'Use Detail Normal': {'value': True}}
        if enabled is not None:
            switches['Use Wear Material'] = {'value': enabled, 'overridden': True}
        return {'master': master or self.master,
                'textures': {'Detail Normal Worn': {'path': '/Game/T_Worn_N', 'overridden': overridden}},
                'switches': switches}

    def test_wear_off_does_not_require_default_worn_normal(self):
        flag, severity, detail = rules.classify_unlinked('Detail Normal Worn', self.facts(False))
        self.assertEqual((flag, severity), ('tex_default_unlinked:Detail Normal Worn', 'info'))
        self.assertFalse(detail['gate'])
        self.assertEqual(detail['switches'][0]['switch'], 'Use Wear Material')

    def test_wear_off_does_not_require_overridden_worn_normal(self):
        flag, severity, detail = rules.classify_unlinked('Detail Normal Worn', self.facts(False, True))
        self.assertEqual((flag, severity), ('tex_gated_off:Detail Normal Worn', 'info'))
        self.assertFalse(detail['gate'])

    def test_wear_on_keeps_worn_normal_required(self):
        flag, severity, detail = rules.classify_unlinked('Detail Normal Worn', self.facts(True))
        self.assertEqual((flag, severity), ('tex_param_unlinked:Detail Normal Worn', 'defect'))
        self.assertTrue(detail['gate'])

    def test_missing_wear_switch_cannot_disable_normal(self):
        flag, severity, detail = rules.classify_unlinked('Detail Normal Worn', self.facts(None))
        self.assertEqual((flag, severity), ('tex_param_unlinked:Detail Normal Worn', 'defect'))
        self.assertIsNot(detail['gate'], False)

    def test_other_master_remains_required(self):
        flag, severity, _ = rules.classify_unlinked('Detail Normal Worn', self.facts(False, master='/Game/M_Other'))
        self.assertEqual((flag, severity), ('tex_param_unlinked:Detail Normal Worn', 'defect'))

    def test_base_detail_normal_is_not_suppressed(self):
        facts = self.facts(False)
        facts['textures'] = {'Detail Normal': {'path': '/Game/T_Detail_N', 'overridden': False}}
        flag, severity, _ = rules.classify_unlinked('Detail Normal', facts)
        self.assertEqual((flag, severity), ('tex_param_unlinked:Detail Normal', 'defect'))


class OpaqueGlassColorGateTests(unittest.TestCase):
    master = '/Game/Pioneer/MaterialLibrary/M_Presets/M_GlassOpaque_NX_01.M_GlassOpaque_NX_01'

    def facts(self, solid, roughness=False, master=None):
        switches = {'Use Roughness Channel': {'value': roughness}}
        if solid is not None:
            switches['Use solid Color instead of CR'] = {'value': solid}
        return {'master': master or self.master,
                'textures': {'C': {'path': '/Game/T_Window_CR', 'overridden': False}},
                'switches': switches}

    def test_solid_color_does_not_require_unused_color_texture(self):
        flag, severity, detail = rules.classify_unlinked('C', self.facts(True))
        self.assertEqual((flag, severity), ('tex_gated_off:C', 'info'))
        self.assertFalse(detail['gate'])

    def test_textured_color_remains_required(self):
        flag, severity, _ = rules.classify_unlinked('C', self.facts(False))
        self.assertEqual((flag, severity), ('tex_param_unlinked:C', 'defect'))

    def test_missing_solid_switch_remains_required(self):
        flag, severity, _ = rules.classify_unlinked('C', self.facts(None))
        self.assertEqual((flag, severity), ('tex_param_unlinked:C', 'defect'))

    def test_roughness_reader_is_not_suppressed(self):
        flag, severity, _ = rules.classify_unlinked('C', self.facts(True, roughness=True))
        self.assertEqual((flag, severity), ('tex_param_unlinked:C', 'defect'))

    def test_missing_roughness_switch_is_not_suppressed(self):
        facts = self.facts(True)
        del facts['switches']['Use Roughness Channel']
        flag, severity, _ = rules.classify_unlinked('C', facts)
        self.assertEqual((flag, severity), ('tex_param_unlinked:C', 'defect'))

    def test_unrelated_master_is_not_suppressed(self):
        flag, severity, _ = rules.classify_unlinked('C', self.facts(True, master='/Game/M_Other'))
        self.assertEqual((flag, severity), ('tex_param_unlinked:C', 'defect'))

    def test_real_builder_requests_color_only_for_textured_permutation(self):
        for solid in (False, True):
            graph = MagicMock()
            graph.image.return_value = {'r': 'red', 'g': 'green', 'b': 'blue', 'a': 'alpha', 'color': 'rgb'}
            record = {'scalars': {}, 'colors': {}, 'textures': {'C': '/Game/T_Window_CR'},
                      'switches': {'Use solid Color instead of CR': solid, 'Use Roughness Channel': False}}
            rebuild('glass_opaque').build_glass_opaque(graph, record)
            sampled = any(call.args[0] == '/Game/T_Window_CR' for call in graph.image.call_args_list)
            self.assertEqual(sampled, not solid)
