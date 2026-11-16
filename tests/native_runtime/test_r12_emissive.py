"""R12: switch-on XEM emissive reaches the finished surface once the Packed Mask master routes to build_surface."""
import unittest
try:
    from .test_surface_layer_stages import FakeGraph as SurfGraph, load_surfaces, record
except ImportError:
    from test_surface_layer_stages import FakeGraph as SurfGraph, load_surfaces, record


class EmitGraph(SurfGraph):
    def finish(self, color, rough=None, metal=None, normal=None, spec=None, emission=None, **kw):
        super().finish(color, rough, metal, normal, spec, emission, **kw)
        self.finished['emission'] = emission
        self.finished['kw'] = kw


TEX = {'CR': '/Game/T/T_P_CR', 'NOX': '/Game/T/T_P_NOX', 'Packed Mask': '/Game/T/T_P_XEM'}


class XemEmissive(unittest.TestCase):
    surfaces = load_surfaces()

    def run_xem(self, switches):
        g = EmitGraph()
        self.surfaces.build_surface(g, record('M_PropPreset_CR+NOX+XEM', TEX, switches, {'EmissiveIntensity': 20},
                                              {'EmissiveColor': (1, .5, .2, 1)}))
        return g

    def test_use_emissive_wires_emission(self):
        g = self.run_xem({'Use Emissive': True})
        self.assertIsNotNone(g.finished.get('emission'))

    def test_switch_off_no_emission(self):
        g = self.run_xem({})
        self.assertIsNone(g.finished.get('emission'))


if __name__ == '__main__':
    unittest.main()
