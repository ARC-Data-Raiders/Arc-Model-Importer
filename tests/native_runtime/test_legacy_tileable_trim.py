"""Legacy prop_trim emulates the Tileable Metal contract (tint x2, metal B^2.2, MaskTintFromMetalness) when native refuses."""
try:
    from .fake_graph import FakeGraph, rebuild
except ImportError:
    from fake_graph import FakeGraph, rebuild
import unittest

TILEABLE='/Game/M_Tileable_Metal_CR+NXX'


def rec(scalars,switches,master=TILEABLE):
    return {'master':master,'name':'MI_T','scalars':scalars,'colors':{'Tint Color 01':(.5,.4,.3,1)},
        'textures':{'CR Texture':'/Game/T/CR','NXX/NMX Texture':'/Game/T/NXX'},'switches':switches}


def run(scalars,switches,master=TILEABLE):
    g=FakeGraph();rebuild('prop_trim').build_prop_trim(g,rec(scalars,switches,master));return g


class LegacyTileableTrim(unittest.TestCase):
    def test_tileable_nmx_metal_is_b_pow_22(self):
        g=run({'MaskTintFromMetalness':1,'Tiling':2},{'Use Tint':True,'Use NMX':True,'Turn on basic tiling':True})
        self.assertIn('POWER(T[Trim normal / tint-or-metal / surface mask].b,2.2)',g.out['metallic'])
        self.assertIn('TintMetalness',g.params)
    def test_tileable_tint_doubles_and_masks_by_metal(self):
        g=run({'MaskTintFromMetalness':.5},{'Use Tint':True})
        self.assertIn("VSCALE(P[Tint Color 01],(0, 0, 0),2)",g.out['color'])
        self.assertIn('P[MaskTintFromMetalness]',g.out['color'])
        self.assertIn('MULTIPLY(T[Trim normal / tint-or-metal / surface mask].a,8)',g.out['color'])
        self.assertTrue(any(w.startswith('approximate:') for w in g.report['warnings']))
    def test_tileable_root_without_scalar_uses_root_default(self):
        # 65/88 Tileable Metal MIs leave MaskTintFromMetalness at the root default.
        g=run({},{'Use Tint':True,'Use NMX':True})
        self.assertIn('POWER(T[Trim normal / tint-or-metal / surface mask].b,2.2)',g.out['metallic'])
        self.assertIn('MaskTintFromMetalness',g.params)
        self.assertIn("VSCALE(P[Tint Color 01],(0, 0, 0),2)",g.out['color'])
        self.assertTrue(any(w.startswith('approximate:') for w in g.report['warnings']))
    def test_proptrim_without_tileable_scalar_unchanged(self):
        g=run({},{'Use Tint':True},master='/Game/M_PropPreset_Trim_CR+NXX')
        self.assertNotIn('POWER',g.out['metallic']);self.assertNotIn('MaskTintFromMetalness',g.params)
        self.assertIn('MULTIPLY(T[Trim normal / tint-or-metal / surface mask].a,10)',g.out['metallic'])
        self.assertFalse(any(w.startswith('approximate:') for w in g.report['warnings']))

if __name__=='__main__':unittest.main()
