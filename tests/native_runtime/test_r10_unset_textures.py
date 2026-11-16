"""R10: the stored audit record carries the builder's unset_textures list, from any build report key."""
import importlib
import json
import unittest
from unittest import mock
from .native_test_support import PACKAGE, namespace
namespace()
audit = importlib.import_module(PACKAGE + '.Shared.materials.material_audit')

MI = {'package': '/Game/MI_X', 'master': '/Game/M_Base', 'family': 'surface',
      'textures': {'BaseColor': '/Game/T_X_BC'}, 'settings': {}}
GRAPH = {'name': 'MI_X', 'images': [], 'render_method': 'DITHERED', 'alpha_linked': False,
         'alpha_clip_threshold': None, 'emission_linked': False, 'vertex_colour_attrs': [],
         'uv_maps_used': [], 'output_linked': True}


class Mat(dict):
    name = 'MI_X'


class UnsetTexturesRecordTests(unittest.TestCase):
    def test_facts_carry_unset_list_and_gate(self):
        rec = audit.audit_material_facts(GRAPH, MI, {'unset_textures': ['Decal Mask', 'BaseColor']},
                                         {'uv_layers': ['UV0'], 'color_attributes': []})
        self.assertIn('unset_textures', rec['flags'])
        self.assertEqual(rec['details']['unset_textures'], ['BaseColor', 'Decal Mask'])
        audit.apply_severity(rec)
        self.assertEqual(rec['severity']['unset_textures'], 'inconsistency')

    def test_no_unset_no_flag(self):
        rec = audit.audit_material_facts(GRAPH, MI, {}, {'uv_layers': ['UV0'], 'color_attributes': []})
        self.assertNotIn('unset_textures', rec['flags'])

    def test_native_report_key_is_read(self):
        for key in ('dr_native_report', 'arc_enemy_build_report'):
            with self.subTest(key=key):
                mat = Mat({key: json.dumps({'unset_textures': ['Decal Mask']})})
                with mock.patch.object(audit, 'graph_facts', return_value=dict(GRAPH)), \
                        mock.patch.object(audit, 'mesh_facts', return_value={'uv_layers': ['UV0']}), \
                        mock.patch.object(audit, '_store'):
                    rec = audit.audit_material(mat, MI)
                self.assertIn('unset_textures', rec['flags'])
                self.assertEqual(rec['details']['unset_textures'], ['Decal Mask'])


if __name__ == '__main__':
    unittest.main()
