"""Portable exact static Glow regressions; native numeric oracle is a checked fixture."""
try:
    from .native_test_support import ROOT
except ImportError:
    from native_test_support import ROOT
import json,math,unittest
from pathlib import Path
from material_runtime.core import MaterialRecord,expr,evaluate,expression_fingerprint
from material_runtime.blockout_glow import compile_blockout_glow,MASTER,EXPOSURE_KEY

FIXTURE=json.loads((Path(__file__).parent/'fixtures/blockout_glow_native_checkpoints.json').read_text(encoding='utf-8'))

def record(scalars=None,colors=None,**kwargs):
    return MaterialRecord('/Game/Tests/Glow',master=MASTER,scalars={} if scalars is None else scalars,colors={} if colors is None else colors,**kwargs)

class NativeBlockoutGlowTests(unittest.TestCase):
    def test_independent_native_instruction_replay_checkpoints(self):
        self.assertEqual(len(FIXTURE['cases']),32)
        for row in FIXTURE['cases']:
            plan=compile_blockout_glow(MaterialRecord(**row['record']),row['context']);self.assertTrue(plan.outputs,plan.diagnostics)
            inputs={'uv':(.17,.83,0.),'texture':lambda ref,uv,attrs:row['gradient_rgba']}
            for output,expected in row['expected'].items():
                with self.subTest(case=row['case'],output=output):
                    actual=evaluate(plan.outputs[output],inputs)
                    for left,right in zip(actual,expected):self.assertAlmostEqual(left,right,delta=1e-10)
    def test_finite_preview_parameter_default_override_and_fingerprint(self):
        control=expr('preview_parameter',key=EXPOSURE_KEY,default=1.);self.assertEqual(evaluate(control),1.);self.assertEqual(evaluate(control,{'preview_parameters':{EXPOSURE_KEY:0.}}),0.)
        self.assertNotEqual(expression_fingerprint(control),expression_fingerprint(expr('preview_parameter',key=EXPOSURE_KEY,default=2.)))
        for invalid in (float('nan'),float('inf'),'1',True):
            with self.assertRaises(ValueError):expr('preview_parameter',key='Bad',default=invalid)
            with self.assertRaises(ValueError):evaluate(control,{'preview_parameters':{EXPOSURE_KEY:invalid}})
    def test_neutral_preview_is_diagnosed_and_editable_gain_is_not_native_default(self):
        plan=compile_blockout_glow(record(),{'vertex_ndotv':1.});self.assertIn('game eye-adaptation value unavailable; Blender neutral preview uses1',plan.diagnostics)
        neutral=evaluate(plan.outputs['emission']);double=evaluate(plan.outputs['emission'],{'preview_parameters':{EXPOSURE_KEY:2.}})
        for left,right in zip(neutral,double):self.assertAlmostEqual(right,2*left)
    def test_zero_native_controls_and_inactive_gradient_do_not_require_resources(self):
        def unavailable(*args):raise AssertionError('Inactive fixed gradient was sampled')
        plan=compile_blockout_glow(record({'Intensity':0.,'GradientAmount':0.}),{'vertex_ndotv':1.,'eye_adaptation':1.})
        self.assertEqual(evaluate(plan.outputs['emission'],{'texture':unavailable}),(0.,0.,0.))
        plan=compile_blockout_glow(record({'DamagePercent':-1.}),{'vertex_ndotv':0.,'exposure_reciprocal':1.});self.assertEqual(evaluate(plan.outputs['emission']),(0.,0.,0.))
    def test_unextracted_permutations_and_wrong_mount_refuse(self):
        rejected=compile_blockout_glow(record(switches={'SwirlTexture':True,'Pulsing':True}));self.assertFalse(rejected.outputs);self.assertIn('SwirlTexture',rejected.diagnostics[0])
        self.assertFalse(compile_blockout_glow(MaterialRecord('/Engine/Tests/Glow',master=MASTER.replace('/Game/','/Engine/'))).outputs)
    def test_pulsing_permutation_freezes_at_the_pulse_mean(self):
        # MI_Glow_Pulse_Green: pixel 1F6CC0D6 scales the static glow by 1 + (DamagePercent + .2) * (sin(0) + 1).
        scalars={'Intensity':20.,'PulsingFrequency':0.,'DamagePercent':.5};colors={'Color':(.031804,.427083,.155801,0.)};context={'vertex_ndotv':.7,'eye_adaptation':1.}
        static=compile_blockout_glow(record(scalars,colors),context);pulsing=compile_blockout_glow(record(scalars,colors,switches={'Pulsing':True}),context)
        self.assertTrue(pulsing.outputs,pulsing.diagnostics);self.assertIn('pixel:1F6CC0D631C8BA2A6878E2E6E112248952142CDD5562CD7ADE958AA47BBEB5F5',pulsing.evidence)
        for left,right in zip(evaluate(static.outputs['emission']),evaluate(pulsing.outputs['emission'])):self.assertAlmostEqual(right,left*1.7,places=9)
        self.assertEqual(evaluate(static.outputs['base_color']),evaluate(pulsing.outputs['base_color']))
    def test_native_gbuffer_numeric_facts_match_material_outputs(self):
        facts=FIXTURE['native_instruction_facts'];plan=compile_blockout_glow(record(colors={'Color':(.2,.7,1.3,1.)}),{'vertex_ndotv':1.})
        for output,expected in facts['material_outputs'].items():self.assertEqual(evaluate(plan.outputs[output]),expected)
        self.assertEqual(evaluate(plan.outputs['base_color']),(.2,.7,1.));self.assertEqual(plan.settings['shading_model'],'default_lit')
        self.assertEqual({encoded&15 for encoded in facts['gbufferB_model_encoded_uint8']},{facts['shading_model_low4bits']})
    def test_explicit_native_blend_and_shading_values_are_not_shadowed(self):
        opaque=compile_blockout_glow(record(settings={'BlendMode':0}));self.assertTrue(opaque.outputs);self.assertNotIn('blend',opaque.settings);self.assertEqual(opaque.settings['BlendMode'],0)
        for settings in ({'BlendMode':1},{'BlendMode':2},{'blend_mode':'translucent'},{'ShadingModel':'EMaterialShadingModel::MSM_Unlit'}):self.assertFalse(compile_blockout_glow(record(settings=settings)).outputs)
    def test_external_input_validation_and_explicit_context_remove_neutral_preview(self):
        for invalid in (0.,-1.,float('nan')):self.assertFalse(compile_blockout_glow(record(),{'eye_adaptation':invalid}).outputs)
        plan=compile_blockout_glow(record(),{'vertex_ndotv':.4,'eye_adaptation':2.,'fx_emissive_intensity':.5});self.assertTrue(plan.outputs);self.assertFalse(any('neutral preview' in text for text in plan.diagnostics))

if __name__=='__main__':unittest.main(verbosity=2)
