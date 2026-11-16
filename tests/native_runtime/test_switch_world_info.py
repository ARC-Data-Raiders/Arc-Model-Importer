"""R7: world-projected / view / light dependent switches and projected dirt are INFO."""
import unittest

from native_runtime.test_switch_render_only import R


class SwitchWorldInfo(unittest.TestCase):
    def test_world_projected(self):
        for name in ('TopMaskBlending', 'Use Top Mask Blending', '3.Top Down Moss', 'Use Side Mask',
                     'WorldZMask', 'VerticalMask'):
            self.assertEqual(R.switch_class(name), ('world_projected', R.INFO), name)
        self.assertEqual(R.severity('switch_world_projected:Use Side Mask'), R.INFO)

    def test_view_light_runtime(self):
        self.assertEqual(R.switch_class('Use POM'), ('view_dependent', R.INFO))
        self.assertEqual(R.switch_class('Use FakeSSS'), ('light_dependent', R.INFO))
        self.assertEqual(R.switch_class('6. Vt Blend Mask'), ('runtime_input', R.INFO))

    def test_projected_dirt_is_info(self):
        on = [{'switch': 'Use Top Down Dirt', 'value': True, 'overridden': True}]
        self.assertEqual(R.layer_rule('dirt', True, on), ('layer_world_projected:dirt', R.INFO))
        plain = [{'switch': 'Use Dirt', 'value': True, 'overridden': True}]
        self.assertEqual(R.layer_rule('dirt', True, plain), ('layer_not_built:dirt', R.INCONSISTENCY))

    def test_overlay_modifier_switches_do_not_enable(self):
        xem = {'TriPlanar Overlay': {'value': True, 'overridden': True},
               'UseOverlay': {'value': False, 'overridden': False},
               'Use Pattern Overlay': {'value': False, 'overridden': False}}
        state, ev = R.layer_gate('overlay', xem)
        self.assertIs(state, False)
        self.assertNotIn('TriPlanar Overlay', [e['switch'] for e in ev])
        trim = {'TriPlanar Overlay': {'value': True}, 'UseOverlay': {'value': False}, 'Color Overlay': {'value': True}}
        self.assertIs(R.layer_gate('overlay', trim)[0], True)
        concrete = {'Flip Overlay on Paint': {'value': True}, 'Use UV1 as Overlay UVs': {'value': True},
                    'UseOverlay': {'value': False}}
        self.assertIs(R.layer_gate('overlay', concrete)[0], False)
        trimmap = {'1. Overlay Random Offset': {'value': True}, '4. Overlay Texture': {'value': False}}
        self.assertIs(R.layer_gate('overlay', trimmap)[0], False)
        rusty = {'Overlay Use Triplanar': {'value': True}}
        self.assertIs(R.layer_gate('overlay', rusty)[0], True)


if __name__ == '__main__':
    unittest.main()
