"""Rust / VX colouring / vegetation tint stages in rebuild/surfaces.py (prop-audit R7), fake Graph, no Blender."""
import unittest

from native_runtime.test_surface_layer_stages import FakeGraph, load_surfaces


def record(switches=None, scalars=None, colors=None, textures=None):
    return {'switches': switches or {}, 'scalars': scalars or {}, 'colors': colors or {},
            'textures': textures or {}, 'settings': {}, 'master': 'x'}


def surf():
    return {'color': 'BASE', 'rough': 'ROUGH', 'ao': 'AO'}


class WeatheringLayers(unittest.TestCase):
    def setUp(self):
        self.mod = load_surfaces()
        self.g = FakeGraph()

    def run_stage(self, r):
        return self.mod.weathering_layers(self.g, r, surf(), 'UV')

    def test_off_is_untouched(self):
        out = self.run_stage(record(switches={'1. Use Rust': False, 'Second Tint': False}))
        self.assertEqual(out['color'], 'BASE')
        self.assertEqual(out['rough'], 'ROUGH')

    def test_metal_rust_samples_additional_mask(self):
        r = record(switches={'1. Use Rust': True, '3. Use Additional Rust Mask': True},
                   scalars={'Rust Multiply': 2, '7. Rust Desaturation': .5},
                   colors={'Rust Tint': (.4, .2, .1, 1)}, textures={'3.1 Additional Rust Mask': 'T_Rust'})
        out = self.run_stage(r)
        self.assertIn('T_Rust', self.g.sampled)
        self.assertIn('Rust', str(out['color']))
        self.assertIn('Rust roughness', str(out['rough']))
        self.assertTrue(any(n.startswith('approximate: rust') for n in self.g.report['notes']))

    def test_wall_rust_uses_cavity(self):
        out = self.run_stage(record(switches={'UseRust': True}, scalars={'RustMask_Low': .2, 'RustMask_High': .8}))
        self.assertEqual(self.g.sampled, [])
        self.assertIn('AO', str(out['color']))

    def test_vx_colouring(self):
        out = self.run_stage(record(switches={'8  Use VX Coloring': True}, colors={'8.4 VX Color': (1, 0, 0, 1)}))
        self.assertIn('VX colouring', str(out['color']))

    def test_vegetation_tints(self):
        r = record(switches={'Second Tint': True, 'Tint Flowers': True},
                   colors={'2:nd Leaves Tint': (.5, .25, .5, 1), 'Flower Tint': (.5, .5, .1, 1)},
                   scalars={'2:nd Tint Amount': .7})
        out = self.run_stage(r)
        self.assertIn('Second tint', str(out['color']))
        self.assertIn('Flower tint', str(out['color']))
        self.assertIn('vegetation flower tint (approximate)', self.g.report['implemented_stages'])


if __name__ == '__main__':
    unittest.main()
