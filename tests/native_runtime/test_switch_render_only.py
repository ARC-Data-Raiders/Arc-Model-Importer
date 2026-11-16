"""Static switch name classes in audit_param_rules (pure Python, no bpy)."""
import importlib.util
import os
import unittest

_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'Shared', 'materials', 'audit_param_rules.py')
_SPEC = importlib.util.spec_from_file_location('audit_param_rules_switch_test', _PATH)
R = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(R)


class SwitchClassTest(unittest.TestCase):
    def test_render_only_switches_are_info(self):
        for name in ('DitherTempAA Stepping', 'Is_LASTLOD', 'Use Wind', 'UseWind', 'Use Wind for Trees',
                     'Cloth Wind', '6. Use WPO Wind', 'bUseGlobalWind', 'DebugStopMoving',
                     'TraversableSlope_Debug', 'Global.Use Wind'):
            self.assertEqual(R.switch_class(name), ('render_only', R.INFO), name)
        self.assertEqual(R.severity('switch_render_only:Use Wind'), R.INFO)

    def test_shading_switches_stay_gated(self):
        for name in ('DitherOpacityMask', 'UseDithering', 'DitherMask', 'Fade Opacity', 'IsOceanLODMaterial',
                     'VirtualTextureBlend', 'Terrain VT Normal', 'Vertex Color Debug', 'HQ window',
                     'Use Edge Fade'):
            self.assertEqual(R.switch_class(name), (None, None), name)

    def test_advanced_uv_is_inconsistency(self):
        self.assertEqual(R.switch_class('Advanced UV Options'), ('uv_unbuilt', R.INCONSISTENCY))
        self.assertEqual(R.severity('switch_uv_unbuilt:Advanced UV Options'), R.INCONSISTENCY)

    def test_uv_placement_switches_are_uv_unbuilt(self):
        for name in ('Use Atlas', 'Simpel Tilling', '4. Use UV2', 'Orientation is Y'):
            self.assertEqual(R.switch_class(name), ('uv_unbuilt', R.INCONSISTENCY), name)

    def test_runtime_inputs_are_info(self):
        for name in ('TerrainBlend', 'Is Terrain', 'Use Per Instance Random Tint', 'Per instance Random Tint',
                     'Set RT Base Color'):
            self.assertEqual(R.switch_class(name), ('runtime_input', R.INFO), name)
        self.assertEqual(R.severity('switch_runtime_input:TerrainBlend'), R.INFO)

    def test_unbuilt_layers_are_inconsistency(self):
        for name in ('Whiteness Mask', '3. Wear Large Scale Mask', 'Use Large Scale Tint',
                     '1. Use Rust', '8  Use VX Coloring', 'Second Tint', 'Use Second Paint', '3. Use Moss'):
            self.assertEqual(R.switch_class(name), ('layer_unbuilt', R.INCONSISTENCY), name)
        self.assertEqual(R.severity('switch_layer_unbuilt:1. Use Rust'), R.INCONSISTENCY)

    def test_lidar_colour_is_world_state(self):
        self.assertEqual(R.layer_of('Mesh Lidar Color'), 'lidar')
        self.assertEqual(R.layer_rule('lidar', None, []), ('layer_world_state:lidar', R.INFO))
        on = [{'switch': 'Use Lidar', 'value': True, 'overridden': True}]
        self.assertEqual(R.layer_rule('lidar', True, on), ('layer_world_state:lidar', R.INFO))


if __name__ == '__main__':
    unittest.main()
