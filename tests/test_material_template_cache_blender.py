"""Private-Blender regressions for validated native graph template reuse."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import bpy
except ImportError:
    bpy = None

sys.path.insert(0, str(Path(__file__).resolve().parent / 'native_runtime'))
from native_test_support import namespace
namespace()
from _native_runtime_regression_addon.Weapons.material_runtime import compiler
from _native_runtime_regression_addon.Shared.materials.native.core import MaterialPlan, literal


@unittest.skipUnless(bpy is not None, 'Requires factory-startup Blender')
class MaterialTemplateCacheTests(unittest.TestCase):
    def setUp(self):
        compiler.clear_template_cache()
        self.plan = MaterialPlan('/Game/Test', 'test', {'base_color': literal((.2,.3,.4)), 'roughness': literal(.4)})
        self.materials = []

    def tearDown(self):
        for material in self.materials:
            groups = [node.node_tree for node in material.node_tree.nodes if node.type == 'GROUP']
            bpy.data.materials.remove(material)
            for group in groups:
                if group.users == 0:
                    bpy.data.node_groups.remove(group)
        compiler.clear_template_cache()

    def build(self, key):
        material = bpy.data.materials.new('Template test')
        self.materials.append(material)
        compiler.compile_plan(self.plan, material, replace=True, template_key=key)
        return material

    def group(self, material):
        return material.node_tree.nodes['DR.Material'].node_tree

    def test_warm_hit_does_not_lower_or_share_live_groups(self):
        first = self.build('complete-signature-A')
        with patch.object(compiler, '_OUTPUTS', {}):
            second = self.build('complete-signature-A')
        self.assertNotEqual(self.group(first), self.group(second))
        self.assertEqual(len(self.group(first).nodes), len(self.group(second).nodes))
        self.assertEqual(len(self.group(first).links), len(self.group(second).links))

    def test_user_graph_and_root_control_edits_do_not_contaminate_template(self):
        first = self.build('complete-signature-A')
        control = first.node_tree.nodes['DR.Control.dr_roughness_scale']
        control.outputs[0].default_value = .25
        shader = next(node for node in self.group(first).nodes if node.type == 'BSDF_PRINCIPLED')
        shader.inputs['Metallic'].default_value = .75
        second = self.build('complete-signature-A')
        self.assertEqual(control.outputs[0].default_value, .25)
        self.assertEqual(second.node_tree.nodes['DR.Control.dr_roughness_scale'].outputs[0].default_value, 1)
        second_shader = next(node for node in self.group(second).nodes if node.type == 'BSDF_PRINCIPLED')
        self.assertEqual(second_shader.inputs['Metallic'].default_value, 0)

    def test_changed_signature_rebuilds(self):
        self.build('complete-signature-A')
        with patch.object(compiler, '_OUTPUTS', {}):
            with self.assertRaisesRegex(NotImplementedError, 'Unknown shader output'):
                self.build('changed-source-build-mapping-or-code')

    def test_no_signature_does_not_reuse(self):
        self.build(None)
        self.assertFalse(compiler._TEMPLATE_CACHE)
        with patch.object(compiler, '_OUTPUTS', {}):
            with self.assertRaisesRegex(NotImplementedError, 'Unknown shader output'):
                self.build(None)

    def test_cache_is_bounded_and_recent_hit_survives(self):
        for index in range(8):
            self.build(str(index))
        self.build('0')
        self.build('8')
        self.assertEqual(len(compiler._TEMPLATE_CACHE), 8)
        self.assertIn('0', compiler._TEMPLATE_CACHE)
        self.assertNotIn('1', compiler._TEMPLATE_CACHE)

    def test_removed_template_is_rebuilt(self):
        self.build('complete-signature-A')
        pristine = compiler._TEMPLATE_CACHE['complete-signature-A'][0]
        bpy.data.node_groups.remove(pristine)
        second = self.build('complete-signature-A')
        self.assertGreater(len(self.group(second).nodes), 0)

    def test_clear_preserves_live_material_graphs(self):
        first = self.build('complete-signature-A')
        group = self.group(first)
        compiler.clear_template_cache()
        self.assertFalse(compiler._TEMPLATE_CACHE)
        self.assertGreater(len(group.nodes), 0)


if __name__ == '__main__':
    unittest.main(argv=[__file__], verbosity=2)

