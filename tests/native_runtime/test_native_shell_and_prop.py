try:
    from .native_test_support import ROOT
except ImportError:
    from native_test_support import ROOT
import dataclasses,unittest
from material_runtime.core import MaterialRecord,evaluate,literal
from material_runtime.surfaces import compile_native_prop_emission,native_prop_coordinates
from material_runtime.decals import compile_enemy_shell

class NativeShellAndPropSemantics(unittest.TestCase):
    def prop(self,**scalars):
        return MaterialRecord('/Game/Test/Prop','/Game/Pioneer/MaterialLibrary/M_Presets/M_PropPreset_CR+NOX+XEM',scalars={'EmissiveIntensity':2.,**scalars},colors={'EmissiveColor':(.2,.4,.8,1.)},textures={'Packed Mask':'/Game/Mask','CR':'/Game/CR'},switches={'Use Masks':True,'Use Emissive':True})
    def prop_inputs(self,mask=.25,time=0.):return {'uv':(.2,.4,0.),'time':time,'texture':lambda ref,uv,attrs:(.9,mask,.8,.7) if ref=='/Game/Mask' else (_ for _ in ()).throw(AssertionError('authored emission sampled CR'))}
    def shell(self,**scalars):
        return MaterialRecord('/Game/Test/Shell','/Game/Pioneer/MaterialLibrary/M_Presets/M_EnemyPreset_Masked_NAO+Detail',scalars={'V0_Roughness':1.,'V1_Roughness':1.,'V2_Roughness':1.,'V0_Metalness':0.,'V1_Metalness':1.,'V2_Metalness':.5,'HeightRatio':.2,**scalars},colors={'V0_Color':(.2,.4,.6,1.),'V1_Color':(.4,.6,.8,1.),'V2_Color':(.6,.8,1.,1.),'SubSet_Color':(1.,0.,0.,1.)},textures={'DecalTrimsheet':'/Game/NAO','Height/HX':'/Game/HX','DetailTexture':'/Game/Detail'},switches={'V0_HideWhenDamaged':True,'V1_HideWhenDamaged':True,'V2_HideWhenDamaged':True})
    def shell_inputs(self,v=0.,calls=None):
        def sample(ref,uv,attrs):
            if calls is not None:calls.setdefault(ref,[]).append(uv)
            return {'/Game/NAO':(.5,.5,.8,1.),'/Game/HX':(.75,.25,0.,0.),'/Game/Detail':(.5,.5,.5,.5)}[ref]
        return {'uv':(.2,v,0.),'texture':sample}
    def test_packed_green_emission_preserves_authored_rgb(self):
        plan=compile_native_prop_emission(self.prop());self.assertEqual(evaluate(plan.outputs['emission'],self.prop_inputs()),(.05,.1,.2));self.assertEqual(evaluate(plan.outputs['emission_strength'],self.prop_inputs()),2.)
    def test_histogram_threshold_includes_equality(self):
        plan=compile_native_prop_emission(self.prop(**{'Use Histogram (0=false | 1=true)':1.,'EmissiveHistogramRange':.5}))
        self.assertEqual(evaluate(plan.outputs['emission'],self.prop_inputs(.499)),(0.,0.,0.));self.assertEqual(evaluate(plan.outputs['emission'],self.prop_inputs(.5)),(.2,.4,.8))
    def test_constant_pulse_and_zero_speed_are_distinct_from_mask(self):
        record=self.prop(PulsateSpeed=1.,PulsateAmount=.5);record=dataclasses.replace(record,switches={**record.switches,'Pulsate':True,'ConstantPulsate':True});plan=compile_native_prop_emission(record)
        self.assertAlmostEqual(evaluate(plan.outputs['emission_strength'],self.prop_inputs(time=.25)),1.);self.assertEqual(evaluate(plan.outputs['emission_strength'],self.prop_inputs(time=.75)),0.)
        zero=compile_native_prop_emission(dataclasses.replace(record,scalars={**record.scalars,'PulsateSpeed':0.}));self.assertAlmostEqual(evaluate(zero.outputs['emission_strength'],self.prop_inputs(time=0.)),1.)
        self.assertEqual(evaluate(plan.outputs['emission'],self.prop_inputs(time=.75)),(.05,.1,.2))
    def test_uv_mode_scales_native_uv0_instead_of_selecting_uv1(self):
        record=self.prop(**{'UV Mode':1.,'Scale Axis':1.,'Mask on UVs':0.,'Tiling U':2.,'Tiling V':3.});coords=native_prop_coordinates(record,{'object_scale':(2.,3.,4.)});value=evaluate(coords,{'uv':(.2,.4,0.)})
        self.assertAlmostEqual(value[0],1.2);self.assertAlmostEqual(value[1],1.2)
    def test_negative_uv_regions_and_exact_boundaries_select_native_variants(self):
        plan=compile_enemy_shell(self.shell())
        for v,expected in ((0.,(.1,.2,.3)),(-1.,(.2,.3,.4)),(-2.,(.3,.4,.5)),(-2.001,(.1,.2,.3))):
            with self.subTest(v=v):
                for a,b in zip(evaluate(plan.outputs['base_color'],self.shell_inputs(v)),expected):self.assertAlmostEqual(a,b)
    def test_blue_coverage_global_damage_and_normal_rg_have_separate_roles(self):
        plan=compile_enemy_shell(self.shell(DamagePercent=.25));inputs=self.shell_inputs();self.assertAlmostEqual(evaluate(plan.outputs['alpha'],inputs),.6);self.assertEqual(evaluate(plan.outputs['normal'],inputs),(0.,0.,1.));self.assertEqual(evaluate(plan.outputs['metallic'],inputs),0.)
    def test_subset_uses_hx_green_without_replacing_coverage(self):
        plan=compile_enemy_shell(self.shell(UseSubSetMaterial=1.,SubSet_Roughness=.8,SubSet_Metal=1.));inputs=self.shell_inputs()
        for a,b in zip(evaluate(plan.outputs['base_color'],inputs),(.2,.15,.225)):self.assertAlmostEqual(a,b)
        self.assertAlmostEqual(evaluate(plan.outputs['alpha'],inputs),.8);self.assertAlmostEqual(evaluate(plan.outputs['metallic'],inputs),.25);self.assertAlmostEqual(evaluate(plan.outputs['roughness'],inputs),.3125)
    def test_parallax_shifts_trimsheet_while_height_and_detail_keep_native_coordinates(self):
        record=self.shell();record=dataclasses.replace(record,switches={**record.switches,'Parallax':True});plan=compile_enemy_shell(record,{'tangent_view':literal((2.,1.,1.))});calls={};evaluate(plan.outputs['base_color'],self.shell_inputs(-.4,calls))
        for a,b in zip(calls['/Game/NAO'][0][:2],(.3,-.35)):self.assertAlmostEqual(a,b)
        for a,b in zip(calls['/Game/HX'][0][:2],(.2,-.4)):self.assertAlmostEqual(a,b)
        for a,b in zip(calls['/Game/Detail'][0][:2],(.6,-1.2)):self.assertAlmostEqual(a,b)
