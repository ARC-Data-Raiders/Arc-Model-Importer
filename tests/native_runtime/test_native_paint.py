try:
    from .native_test_support import ROOT
except ImportError:
    from native_test_support import ROOT
try: from .native_test_support import real_package as _ns
except ImportError: from native_test_support import real_package as _ns
_ns()
import unittest
from _native_runtime_regression_addon.Shared.materials.native.core import MaterialRecord,literal,evaluate
from _native_runtime_regression_addon.Weapons.material_runtime.weapons import compile_weapon,WEAPON_DETAIL_ARRAY,RUST_NORMAL,RUST_COLOR,_surface_type

class TestNativePaintCheckpoints(unittest.TestCase):
    def record(self,switches=None,scalars=None,colors=None):
        return MaterialRecord('/Game/test/nativepaint',textures={'NXM':'/Game/nxm','CR':'/Game/cr','Weapon ID':'/Game/id','Wear':'/Game/wear','Paintjob Pattern':'/Game/pattern'},switches={'Enable Red Mask':True,'UseWeaponQuality':True,**(switches or {})},scalars={'Base - Metalness Tweak - Red':.25,**(scalars or {})},colors={'Base - Color - Red':(.2,.4,.8,1),'Base - Color - Blue':(.8,.4,.2,1),'Base - Roughness Tweak - Red':(.3,0,0,1),**(colors or {})})
    def inputs(self,ids=(1,0,0,0),wear=(0,.5,0,.5),surface=0,pattern=(.3,.5,.7,1),rust_b=1):
        data={'/Game/nxm':(.5,.5,surface,.7),'/Game/cr':(.8,.6,.4,.5),'/Game/id':ids,'/Game/wear':wear,'/Game/pattern':pattern,RUST_NORMAL:(.5,.5,rust_b,1),RUST_COLOR:(.1,.2,.3,.8)}
        def sampler(ref,coords,attrs):
            if ref==WEAPON_DETAIL_ARRAY:return (.5,.5,1.,.5)
            return data[ref]
        return {'uv':(.5,.5,0.),'texture':sampler}
    def plan(self,r=None,q=0):return compile_weapon(r or self.record(),{'weapon_detail_amount':literal(q)})
    def test_native_swapc_surface_override_modes_and_boundaries(self):
        for original,mode,expected in ((.1,.3,.9),(.8,.3,.8),(.1,.6,.1),(.8,.6,.9),(.25,.3,.25),(.25,.6,.25),(.1,1.,.9),(.8,0.,.8),(.1,.15,.1)):
            self.assertAlmostEqual(evaluate(_surface_type(literal(original),literal(.9),literal(mode))),expected)
    def test_authored_palette_and_rough_metal_layer_controls(self):
        p=self.plan();i=self.inputs()
        self.assertEqual(evaluate(p.outputs['base_color'],i),(.2,.4,.8))
        self.assertAlmostEqual(evaluate(p.outputs['metallic'],i),.25)
        self.assertAlmostEqual(evaluate(p.outputs['roughness'],i),.3)
        self.assertEqual(evaluate(p.outputs['normal'],i),(0.,0.,1.))
    def test_id_masks_are_squared_and_leave_unrelated_material_zones(self):
        p=self.plan();i=self.inputs(ids=(.5,0,0,0))
        for actual,expected in zip(evaluate(p.outputs['base_color'],i),(.65,.55,.5)):self.assertAlmostEqual(actual,expected)
        self.assertAlmostEqual(evaluate(p.outputs['metallic'],i),.7*.75+.25*.25)
        i=self.inputs(ids=(0,1,0,0))
        self.assertEqual(evaluate(p.outputs['base_color'],i),(.8,.6,.4))
    def test_native_wear_resistance_and_delays(self):
        i=self.inputs(wear=(1,.5,0,.5))
        self.assertEqual(evaluate(self.plan().outputs['base_color'],i),(.8,.6,.4))
        p=self.plan(self.record(scalars={'Base - Wear Resistance - Red':1.}))
        self.assertEqual(evaluate(p.outputs['base_color'],i),(.2,.4,.8))
        # Wear.G=.1 produces native raw wear .4, then delay .5 doubles visibility.
        i=self.inputs(wear=(0,.1,0,.5))
        a=evaluate(self.plan().outputs['base_color'],i)
        b=evaluate(self.plan(self.record(scalars={'Base - Wear Delay':.5})).outputs['base_color'],i)
        self.assertAlmostEqual(a[0],.8+.4*(.2-.8));self.assertAlmostEqual(b[0],.8+.8*(.2-.8))
    def test_blue_pattern_and_black_as_alpha_are_separate_stages(self):
        r=self.record(switches={'Enable Red Mask':False,'Enable Blue Mask':True,'Pattern - Use - Blue':True,'Use Black as Alpha':True})
        p=self.plan(r)
        self.assertEqual(evaluate(p.outputs['base_color'],self.inputs(ids=(0,0,1,0))),(.3,.5,.7))
        self.assertEqual(evaluate(p.outputs['base_color'],self.inputs(ids=(0,0,1,0),pattern=(0,0,0,1))),(.8,.4,.2))
    def test_quality_rust_affects_color_metal_roughness_without_emission(self):
        p=self.plan(q=1);i=self.inputs(wear=(0,.5,1,.5))
        for actual,expected in zip(evaluate(p.outputs['base_color'],i),(.8*.05+.1*.95,.6*.05+.2*.95,.4*.05+.3*.95)):self.assertAlmostEqual(actual,expected)
        self.assertAlmostEqual(evaluate(p.outputs['metallic'],i),.7*.05)
        self.assertAlmostEqual(evaluate(p.outputs['roughness'],i),(.5**1.1)*1.1*.05+.8*.95)
        self.assertNotIn('emission',p.outputs)
    def test_plastic_surface_keeps_its_own_wear_response(self):
        p=self.plan(q=1);i=self.inputs(ids=(0,0,0,0),wear=(1,.5,0,.5),surface=.5,rust_b=.5)
        self.assertEqual(evaluate(p.outputs['base_color'],i),(.4,.3,.2))
        self.assertAlmostEqual(evaluate(p.outputs['roughness'],i),min((.5**1.1)*1.1+.4,1)*.5)

if __name__=='__main__':unittest.main()
