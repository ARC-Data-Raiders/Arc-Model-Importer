"""Portable tests of explicit original generic emission preview policy."""
import ast,importlib.util,os,unittest
from pathlib import Path
ROOT=Path(os.environ['DATARAIDERS_TEST_REPOSITORY']) if os.environ.get('DATARAIDERS_TEST_REPOSITORY') else next(p for p in Path(__file__).resolve().parents if (p/'Shared/materials/rebuild/surfaces.py').is_file())
SOURCE=Path(os.environ.get('DATARAIDERS_EMISSION_STAGE',ROOT));spec=importlib.util.spec_from_file_location('_generic_emission_policy',SOURCE/'Shared/materials/rebuild/emission_preview.py');policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
class Graph:
    def __init__(self):self.report={'warnings':[]};self.multiplies=[]
    def mul(self,a,b):self.multiplies.append((a,b));return a*b

def record(**changes):return {'master':'/Game/Example/M_EnemyGeneric','family':'surface','colors':{},'scalars':{},'switches':{},**changes}
class GenericEmission(unittest.TestCase):
    def test_scalar_mask_is_strength_authored_hue_keeps_blue(self):
        g=Graph();hue,strength=policy.apply_preview(g,record(colors={'Emissive Color':(.1,.2,.8,1.)},scalars={'Emissive Power':4}),(.8,.1,.1,1.),.25);self.assertEqual(hue,(.1,.2,.8,1.));self.assertEqual(strength,1.);self.assertEqual(g.multiplies,[(.25,4.)]);self.assertEqual(g.report['emission_preview']['accuracy'],'original_preview_policy');self.assertFalse(g.report['emission_preview']['native_verified'])
    def test_explicit_use_cr_has_priority(self):
        g=Graph();hue,strength=policy.apply_preview(g,record(colors={'EmissiveColor':(0,0,1,1)},switches={'Use CR as Emissive':True}),(.8,.1,.1,1.),.5);self.assertEqual(hue,(.8,.1,.1,1.));self.assertEqual(strength,.5)
    def test_false_cr_without_authored_hue_does_not_borrow_surface(self):
        g=Graph();hue,_=policy.apply_preview(g,record(switches={'Use CR as Emissive':False}),(.8,.1,.1,1.),.5);self.assertEqual(hue,(1.,1.,1.,1.));self.assertEqual(g.report['emission_preview']['hue_source'],'neutral_missing_authored_hue')
    def test_explicit_black_and_zero_gain_are_preserved(self):
        g=Graph();hue,strength=policy.apply_preview(g,record(colors={'EmissiveColor':(0,0,0,1)},scalars={'EmissiveIntensity':0}),(.8,.1,.1,1.),.9);self.assertEqual(hue,(0,0,0,1));self.assertEqual(strength,0.)
    def test_state_switch_off_ignores_available_mask(self):
        g=Graph();self.assertEqual(policy.apply_preview(g,record(switches={'Use Emissive':False}),(.8,.1,.1,1),1.),(None,0));self.assertFalse(g.multiplies);self.assertFalse(g.report['emission_preview']['enabled'])
    def test_missing_hue_uses_labeled_surface_preview(self):
        g=Graph();hue,strength=policy.apply_preview(g,record(),(.1,.2,.9,1.),.2);self.assertEqual(hue,(.1,.2,.9,1.));self.assertEqual(strength,.2);self.assertEqual(g.report['emission_preview']['hue_source'],'resolved_surface_color_preview');self.assertIn('native state unavailable',g.report['emission_preview']['state_interpretation'])
    def test_exact_xem_and_blockout_profiles_untouched(self):
        for name in ('M_PropPreset_CR+NOX+XEM','M_BlockoutPreset_Glow'):
            g=Graph();mask=object();r=record(master='/Game/'+name);self.assertEqual(policy.apply_preview(g,r,(1,0,0,1),mask),(mask,1));self.assertEqual(g.report,{'warnings':[]});self.assertFalse(policy.applies_to_cache(r))
    def test_nonfinite_gain_disables_preview_and_reports(self):
        g=Graph();_,strength=policy.apply_preview(g,record(scalars={'EmissiveIntensity':float('nan')}),(.1,.2,.3,1),1.);self.assertEqual(strength,0.);self.assertTrue(any('Nonfinite' in warning for warning in g.report['warnings']))
    def test_cache_recipe_is_limited_to_generic_surface_families(self):
        self.assertTrue(policy.applies_to_cache(record()));self.assertTrue(policy.applies_to_cache(record(family='fallback')));self.assertFalse(policy.applies_to_cache(record(family='character')));source=(SOURCE/'Shared/materials/rebuild/materials.py').read_text(encoding='utf-8');self.assertIn("shader_record['generic_emission_preview_recipe']",source);self.assertLess(source.index("shader_record['generic_emission_preview_recipe']"),source.index('key = hashlib.sha256'))
if __name__=='__main__':unittest.main(verbosity=2)
