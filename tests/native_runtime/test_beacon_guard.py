"""W10: the beacon sprite builder must not overwrite non-beacon MIs whose name merely contains 'beacon'.

MI_LampBeacon_01_A (parent M_PropPreset_CR+NOX+XEM) is the lamp housing with its own CR + NOX; the slot post-pass
matched 'beacon' in its name and replaced the packed-prop graph with T_Beacon_CR_01 and no normal."""
import json, os, unittest
try:
    from .native_test_support import real_package, temp_fixture
except ImportError:
    from native_test_support import real_package, temp_fixture
import bpy


def _mi(root, name, parent):
    path = os.path.join(root, name + '.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'Type': 'MaterialInstanceConstant', 'Name': name,
                   'Properties': {'Parent': {'ObjectPath': parent + '.' + parent.rsplit('/', 1)[-1]}}}, f)
    return path


class BeaconGuard(unittest.TestCase):
    def setUp(self):
        self.env = real_package('Shared.materials.environment')

    def test_lamp_housing_mi_is_left_alone(self):
        with temp_fixture() as root:
            path = _mi(root, 'MI_LampBeacon_01_A', '/Game/Pioneer/MaterialLibrary/M_Presets/M_PropPreset_CR+NOX+XEM')
            mat = bpy.data.materials.new('MI_LampBeacon_01_A.001'); mat.use_nodes = True
            mat.node_tree.nodes.new('ShaderNodeNormalMap').name = 'KeepNormal'
            self.env.setup_beacon_material(mat, path)
            self.assertIn('KeepNormal', mat.node_tree.nodes)
            self.assertNotEqual(mat.get('arc_mi_family'), 'beacon')

    def test_real_beacon_mi_still_built(self):
        with temp_fixture() as root:
            path = _mi(root, 'MI_Beacon_01', '/Game/Pioneer/MaterialLibrary/M_Beacon')
            mat = bpy.data.materials.new('MI_Beacon_01.002'); mat.use_nodes = True
            self.env.setup_beacon_material(mat, path)
            self.assertEqual(mat.get('arc_mi_family'), 'beacon')


if __name__ == '__main__':
    unittest.main()
