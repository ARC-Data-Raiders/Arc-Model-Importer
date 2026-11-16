"""Private-Blender checks for the weapon look graph contract (plain names, wrapper controls, panels)."""
import sys
import unittest
from pathlib import Path

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
class WeaponGraphContractTests(unittest.TestCase):
    def setUp(self):
        compiler.clear_template_cache()
        self.material = bpy.data.materials.new('Graph contract test')
        plan = MaterialPlan('/Game/Test', 'test', {'base_color': literal((.2,.3,.4)), 'roughness': literal(.4)})
        compiler.compile_plan(plan, self.material, replace=True)
        self.wrapper = self.material.node_tree.nodes['DR.Material']

    def tearDown(self):
        groups = [node.node_tree for node in self.material.node_tree.nodes if node.type == 'GROUP']
        bpy.data.materials.remove(self.material)
        for group in groups:
            if group is not None and group.users == 0:
                bpy.data.node_groups.remove(group)
        compiler.clear_template_cache()

    def test_material_root_has_no_control_value_nodes(self):
        values = [node.name for node in self.material.node_tree.nodes if node.type == 'VALUE']
        self.assertEqual(values, [])

    def test_wrapper_and_output_use_plain_labels(self):
        self.assertTrue(self.wrapper.label.startswith('Arc '), self.wrapper.label)
        self.assertTrue(self.wrapper.label.endswith(' Look'), self.wrapper.label)
        outputs = [node for node in self.material.node_tree.nodes if node.type == 'OUTPUT_MATERIAL']
        self.assertTrue(outputs)
        self.assertEqual(outputs[0].label, 'Material Output')

    def test_controls_are_named_wrapper_inputs_with_hints(self):
        keys = compiler.control_keys(self.wrapper.node_tree)
        self.assertIn('dr_roughness_scale', keys)
        socket = compiler.control_input(self.wrapper, 'dr_roughness_scale')
        self.assertIsNotNone(socket)
        self.assertEqual(socket.name, 'Roughness Scale')
        self.assertEqual(socket.default_value, 1)
        item = next(i for i in self.wrapper.node_tree.interface.items_tree
                    if getattr(i, 'identifier', None) == keys['dr_roughness_scale'])
        self.assertTrue(item.description)
        self.assertFalse(item.description.endswith('.'))
        self.assertLess(item.min_value, item.max_value)

    def test_controls_sit_in_panels(self):
        panels = [i for i in self.wrapper.node_tree.interface.items_tree if getattr(i, 'item_type', '') == 'PANEL']
        self.assertTrue(panels)
        self.assertTrue(all(p.name in compiler._PANELS for p in panels))


if __name__ == '__main__':
    unittest.main()
