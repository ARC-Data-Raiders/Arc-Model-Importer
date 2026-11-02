try:
    from .native_test_support import ROOT
except ImportError:
    from native_test_support import ROOT
import unittest
from material_runtime.core import MaterialRecord,literal,evaluate
from material_runtime.weapons import compile_weapon,WEAPON_DETAIL_ARRAY

class TestNativeWeaponStages(unittest.TestCase):
    def record(self,b=0.,**scalars):return MaterialRecord('/Game/test/weapon',textures={'CR':'/Game/cr','NXM':'/Game/nxm'},scalars=scalars)
    def inputs(self,b=0.,detail_alpha=.5):
        def sampler(ref,uv,attrs):
            if ref=='/Game/cr':return (.8,.6,.4,.5)
            if ref=='/Game/nxm':return (.5,.5,b,.7)
            self.assertEqual(ref,WEAPON_DETAIL_ARRAY)
            self.assertEqual(attrs['role'],'data')
            self.assertEqual(uv,(.125,.125,0.))
            return (.5,.5,(1.,.5,.25,.125)[attrs['slice']],detail_alpha)
        return {'uv':(.5,.5,0.),'texture':sampler}
    def test_packed_metal_is_alpha_and_blue_selects_detail(self):
        for b,factor in ((0.,1.),(1/3,.5),(2/3,.25),(1.,.25)):
            p=compile_weapon(self.record(),{'weapon_detail_amount':literal(0.)})
            data=self.inputs(b)
            self.assertEqual(evaluate(p.outputs['metallic'],data),.7)
            self.assertEqual(evaluate(p.outputs['base_color'],data),tuple(v*factor for v in (.8,.6,.4)))
            self.assertEqual(evaluate(p.outputs['normal'],data),(0.,0.,1.))
            self.assertNotIn('emission',p.outputs)
    def test_roughness_native_power_and_detail_transfer(self):
        p=compile_weapon(self.record(),{'weapon_detail_amount':literal(0.)})
        self.assertAlmostEqual(evaluate(p.outputs['roughness'],self.inputs()),.5**1.1)
        p=compile_weapon(self.record(),{'weapon_detail_amount':literal(1.)})
        self.assertAlmostEqual(evaluate(p.outputs['roughness'],self.inputs()),(.5**1.1)*1.1)
        p=compile_weapon(self.record(**{'Detail R Intensity':0.}),{'weapon_detail_amount':literal(1.)})
        self.assertAlmostEqual(evaluate(p.outputs['roughness'],self.inputs()),.5**1.1)
    def test_source_roles_do_not_alias_color_and_data(self):
        p=compile_weapon(self.record())
        references=[]
        from material_runtime.core import Expr
        def walk(node):
            if not isinstance(node,Expr):return
            if node.op=='texture':references.append((node.attrs['reference'],node.attrs['role']))
            for a in node.args:walk(a)
        for n in p.outputs.values():walk(n)
        self.assertIn(('/Game/cr','color'),references);self.assertIn(('/Game/cr','data'),references)
    def test_unproven_paint_permutation_is_not_silently_called_native(self):
        r=MaterialRecord('/Game/test/paint',textures={'CR':'/Game/cr','NXM':'/Game/nxm'},switches={'Enable Red Mask':True,'UseWeaponQuality':True,'Mid Layer - Use - Red':True,'Pattern - Use - Red':True})
        p=compile_weapon(r)
        self.assertTrue(any('paint permutation' in d for d in p.diagnostics))
        self.assertFalse(any('portable_weapon_palette' in d for d in p.diagnostics))

if __name__=='__main__':unittest.main()
