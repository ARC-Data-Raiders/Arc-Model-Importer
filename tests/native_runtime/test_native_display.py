try:
    from .native_test_support import ROOT
except ImportError:
    from native_test_support import ROOT
"""Native instruction-backed checkpoints, separate from portable preview tests."""
import unittest
from Shared.materials.native.core import MaterialRecord, evaluate, literal
from Shared.materials.native.enemies import compile_enemy_display, native_alert_state

def inputs(uv=(.5,.5,0.),time=0.):return {'uv':uv,'time':time}
def context(**extra):return {'enemy_view_depth':literal(0.),'enemy_bone_damage':literal(0.),'enemy_eye_adaptation':literal(1.),**extra}
def record(alert=0.,**kw):return MaterialRecord('/Game/test/display',scalars={'AlertnessState':alert,'ScanSpeed':.5,'Tiling':1.,'DamagePercent':0.,**kw})

class TestNativeDisplayCheckpoints(unittest.TestCase):
    def test_exact_shader_palette_and_window_boundaries(self):
        for alert,color in ((0.,(0.,.325897,1.)),(.5,(1.,.406525,.006859)),(1.,(1.,0.,0.)),(1.5,(0.,.325897,1.))):
            self.assertEqual(evaluate(native_alert_state(literal(alert))[0]),color)
        # Red state override wins in the overlapping amber/red comparison windows.
        self.assertEqual(evaluate(native_alert_state(literal(.7))[0]),(1.,0.,0.))
    def test_scan_ridge_and_native_sixteenfold_emission(self):
        p=compile_enemy_display(record(),context())
        self.assertEqual(p.settings['enemy_display_mode'],'scan')
        self.assertEqual(evaluate(p.outputs['emission'],inputs()),(0.,.325897*16.,16.))
        self.assertEqual(evaluate(p.outputs['emission'],inputs(uv=(.75,.5,0.))),(0.,0.,0.))
        self.assertAlmostEqual(evaluate(p.outputs['roughness'],inputs()),.4)
    def test_loop_amber_has_distinct_native_nested_sine_behavior(self):
        scan=compile_enemy_display(record(.5),context())
        r=MaterialRecord('/Game/test/loop',scalars=dict(record(.5).scalars),switches={'Loop':True})
        loop=compile_enemy_display(r,context())
        self.assertEqual(loop.settings['enemy_display_mode'],'loop')
        point=inputs(uv=(.75,.5,0.))
        self.assertEqual(evaluate(scan.outputs['emission'],point),(0.,0.,0.))
        self.assertEqual(evaluate(loop.outputs['emission'],point),(16.,.406525*16.,.006859*16.))
    def test_damage_and_eye_adaptation_preserve_independent_roles(self):
        p=compile_enemy_display(record(DamagePercent=1.),context())
        self.assertEqual(evaluate(p.outputs['emission'],inputs()),(0.,0.,0.))
        p=compile_enemy_display(record(),context(enemy_eye_adaptation=literal(2.)))
        self.assertEqual(evaluate(p.outputs['emission'],inputs()),(0.,.325897*8.,8.))
        self.assertEqual(evaluate(p.outputs['alpha']),1.)
    def test_glitch_native_mix_is_not_repurposed_as_coverage(self):
        r=MaterialRecord('/Game/test/glitch',scalars={**dict(record().scalars),'GlitchAmount':1.},switches={'Glitch Effect':True})
        p=compile_enemy_display(r,context(enemy_glitch_noise_sum=literal(0.),enemy_glitch_evidence='native volume fixture'))
        result=evaluate(p.outputs['emission'],inputs())
        self.assertAlmostEqual(result[1],.325897*.028);self.assertAlmostEqual(result[2],.028)
        self.assertEqual(evaluate(p.outputs['alpha']),1.)
        high=MaterialRecord('/Game/test/glitch',scalars={**dict(record().scalars),'GlitchAmount':1.921946},switches={'Glitch Effect':True})
        hp=compile_enemy_display(high,context(enemy_glitch_noise_sum=literal(0.),enemy_glitch_evidence='native volume fixture'))
        self.assertTrue(all(v>=0. for v in evaluate(hp.outputs['emission'],inputs())))
    def test_blink_native_cyan_sine_amber_square_and_red_constant(self):
        def blink(alert):
            return compile_enemy_display(MaterialRecord('/Game/test/blink',scalars=dict(record(alert).scalars),switches={'Blink':True}),context())
        self.assertEqual(evaluate(blink(0.).outputs['emission'],inputs(time=0.)),(0.,0.,0.))
        self.assertEqual(evaluate(blink(.5).outputs['emission'],inputs(time=.25)),(16.,.406525*16.,.006859*16.))
        self.assertEqual(evaluate(blink(1.).outputs['emission'],inputs(time=0.)),(16.,0.,0.))
        self.assertEqual(evaluate(blink(1.).outputs['emission'],inputs(uv=(.99,.5,0.),time=0.)),(16.,0.,0.))
        cyan=evaluate(blink(0.).outputs['emission'],inputs(uv=(.99,.5,0.),time=1/3))
        self.assertAlmostEqual(cyan[2],12.8,places=8)
    def test_missing_volume_is_explicit_and_blink_uses_proven_program(self):
        g=MaterialRecord('/Game/test/glitch',switches={'Glitch Effect':True})
        self.assertTrue(any('3Dnoise' in d for d in compile_enemy_display(g,context()).diagnostics))
        b=MaterialRecord('/Game/test/blink',switches={'Blink':True})
        p=compile_enemy_display(b,context());self.assertEqual(p.settings['enemy_display_mode'],'blink')
        self.assertIn('emission',p.outputs)

if __name__=='__main__':unittest.main()

