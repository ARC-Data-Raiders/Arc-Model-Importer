"""W4: M_MasterLight_Lightbulb_01 'Use CR texture for BC' drives base colour from CR (MI_SupplyStation_01_RadioLights)."""
try:
    from .fake_graph import FakeGraph, rebuild
except ImportError:
    from fake_graph import FakeGraph, rebuild
import unittest

MASTER = '/Game/Pioneer/Lighting/Materials/Lightfixtures/M_MasterLight_Lightbulb_01'


def rec(textures, switches=None, scalars=None, colors=None):
    return {'master': MASTER, 'name': 'MI_Test', 'textures': dict.fromkeys(textures, '/Game/T/X'),
            'scalars': scalars or {}, 'colors': colors or {}, 'switches': switches or {}, 'settings': {}}


def build(r):
    g = FakeGraph()
    rebuild('lightbulb').build_lightbulb(g, r)
    return g


class LightbulbBaseColour(unittest.TestCase):
    def test_use_cr_for_bc_drives_base_color(self):
        g = build(rec(['CR', 'Lightbulb', 'GlassTexture'], {'Use CR texture for BC': True, 'Pulsating': True},
                      {'LightIntensity': 50., 'Tiling': 1000.}, {'GlassColor': (.724, .818, .787, 1.), 'LightColor': (1.34, 1.143, .433, 1.)}))
        self.assertIn('T[CR', str(g.out['color']))
        self.assertTrue(any(w.startswith('approximate:') for w in g.report['warnings']))

    def test_switch_off_keeps_constant_glass_and_authored_emission(self):
        g = build(rec(['CR', 'Lightbulb'], {}, {'LightIntensity': 1.}, {'GlassColor': (.247, .259, .264, 1.), 'LightColor': (.991, .958, .917, 1.)}))
        self.assertNotIn('T[CR', str(g.out['color']))
        self.assertEqual(g.out['color'], (.247, .259, .264))
        self.assertIn('0.991', str(g.out['emission'])); self.assertIn('P[LightIntensity]', str(g.out['emission']))

    def test_switch_on_without_cr_keeps_glass(self):
        g = build(rec(['Lightbulb'], {'Use CR texture for BC': True}))
        self.assertEqual(g.out['color'], (.354167, .354167, .354167))


FIX = '/Game/Pioneer/Lighting/Materials/Lightfixtures/M_MasterLight_Emissive_01'
FLICKER = '/Game/Pioneer/MaterialLibrary/MI_Generic/Blockout/M_Blockout_Flickerlight'


class LightEmissive(unittest.TestCase):
    def r(self, master, scalars, colors):
        x = rec(['Texture', 'Normal'], {}, scalars, colors); x['master'] = master; return x

    def test_routes_light_emissive_masters(self):
        for m in (FIX, FLICKER):
            with self.subTest(master=m): self.assertEqual(rebuild('records').classify(self.r(m, {}, {})), 'light_emissive')

    def test_authored_light_colour_and_intensity(self):
        g = FakeGraph()
        rebuild('light_emissive').build_light_emissive(g, self.r(FLICKER, {'LightIntensity': 75.}, {'LightColor': (1., .3185, .2509, 1.)}))
        self.assertEqual(g.out['color'], (0., 0., 0.))
        self.assertIn('0.3185', str(g.out['emission'])); self.assertIn('P[LightIntensity]', str(g.out['emission']))
        self.assertEqual(g.params['LightIntensity'], 75.)

    def test_negative_intensity_clamped_off(self):
        g = FakeGraph()
        rebuild('light_emissive').build_light_emissive(g, self.r(FIX, {'LightIntensity': -321985540.}, {'LightColor': (1., .64, .17, 1.)}))
        self.assertEqual(g.params['LightIntensity'], 0.)

    def test_registered(self):
        text = open(rebuild('records').__file__.replace('records.py', 'materials.py'), encoding='utf-8').read()
        self.assertIn("'light_emissive':build_light_emissive", text)


if __name__ == '__main__':
    unittest.main()
