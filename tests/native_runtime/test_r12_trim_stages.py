"""R12: PropTrim second-layer blend textures and prop AO dirt are built, not warned."""
try:
    from .fake_graph import FakeGraph, rebuild
except ImportError:
    from fake_graph import FakeGraph, rebuild
import unittest

MASTER='/Game/M_PropTrimPreset_CR+NOM+AO'
TEX={'CR Texture':'/Game/T/CR','NXX/NMX Texture':'/Game/T/NXX','CR Blend':'/Game/T/CRB','NOH Blend':'/Game/T/NOHB',
    'Overlay':'/Game/T/OV','Prop AO Texture':'/Game/T/AO'}


def run(scalars,switches,colors=None,textures=None):
    r={'master':MASTER,'name':'MI_T','scalars':scalars,'colors':colors or {},'textures':dict(textures or TEX),'switches':switches}
    g=FakeGraph();rebuild('prop_trim').build_prop_trim(g,r);return g


class TrimBlendStage(unittest.TestCase):
    def test_blend_textures_sampled_and_mixed(self):
        g=run({'Blend Mask Range Low':.2,'Blend Mask Range High':.6,'Blend Texture Normal Strength':.8,'BlendTextureTile':3,'BlendTextureAffectRoughness':.5},
            {'Enable Blend Textures':True},{'Blend Tint':(.6,.5,.4,1)})
        self.assertIn('Blend colour / roughness',g.images);self.assertIn('Blend normal',g.images)
        for p in ['Blend Mask Range Low','Blend Mask Range High','Blend Texture Normal Strength','BlendTextureTile','Blend Tint','BlendTextureAffectRoughness']:
            self.assertIn(p,g.params)
        self.assertIn('T[Blend colour / roughness]',g.out['color']);self.assertIn('T[Blend colour / roughness].a',g.out['roughness'])
        self.assertIn('T[Blend normal]',str(g.out['normal']))
        self.assertFalse(any('Enable Blend Textures' in w for w in g.report['warnings']))
        self.assertTrue(any(w.startswith('approximate: trim blend') for w in g.report['warnings']))
    def test_invert_blend_mask(self):
        a=run({},{'Enable Blend Textures':True});b=run({},{'Enable Blend Textures':True,'Invert Blend Mask':True})
        self.assertNotEqual(a.out['color'],b.out['color'])
    def test_blend_off_untouched(self):
        g=run({},{});self.assertNotIn('Blend colour / roughness',g.images)


class TrimAODirtStage(unittest.TestCase):
    def test_ao_dirt_tints_by_ao(self):
        g=run({'AO Dirt Strength':.7},{'Use Prop AO Dirt':True},{'AO Dirt Colour':(.2,.1,.05,1)})
        self.assertIn('Prop AO',g.images);self.assertIn('AO Dirt Colour',g.params);self.assertIn('AO Dirt Strength',g.params)
        self.assertIn('T[Prop AO].r',g.out['color']);self.assertIn('P[AO Dirt Colour]',g.out['color'])
        self.assertFalse(any('Use Prop AO Dirt' in w for w in g.report['warnings']))
        self.assertTrue(any(w.startswith('approximate: prop AO dirt') for w in g.report['warnings']))
    def test_zero_strength_skips(self):
        g=run({'AO Dirt Strength':0},{'Use Prop AO Dirt':True});self.assertNotIn('Prop AO',g.images)

if __name__=='__main__':unittest.main()
