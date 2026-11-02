"""Public-boundary checkpoints from isolated064/069/072 linearEXR probes.
These are compatibility observations, not Unreal shader parity claims.
"""
import importlib,sys,types,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];PKG='_dr_registry_boundary_test'
for name,path in ((PKG,ROOT),(PKG+'.material_runtime',ROOT/'material_runtime')):
 m=types.ModuleType(name);m.__path__=[str(path)];sys.modules[name]=m
helpers=importlib.import_module(PKG+'.material_runtime.shared_helpers')
class BoundaryTests(unittest.TestCase):
 def test_normal_radial_height_not_dx_reconstruction(self):
  for color,z in (((.5,.5,.2),1.),((1.,0.,.9),.7071067811865476),((.75,.5,.1),.8660254037844386)):
   actual=helpers.evaluate_normal_flipper(color);self.assertEqual(actual[:2],color[:2]);self.assertAlmostEqual(actual[2],z,places=7);self.assertEqual(actual[3],1.)
 def test_colormask_r_ignored_and_scalar_overlap_not_clamped(self):
  a=helpers.evaluate_colormask((0.,1.,1.),(1,0,0),(0,1,0),(0,0,1),(.1,.7,.7));b=helpers.evaluate_colormask((1.,1.,1.),(1,0,0),(0,1,0),(0,0,1),(.1,.7,.7));self.assertEqual(a,b);self.assertEqual(a['Mask_Color'],(0.,0.,1.));self.assertAlmostEqual(a['Mask_Roughness'],1.3)
 def test_calibrated_id_color3_and_known_gap(self):
  self.assertEqual(helpers.evaluate_id_mask(.125,3),1.);self.assertEqual(helpers.evaluate_id_mask(.125,2),0.);self.assertFalse(any(helpers.evaluate_id_mask(.75,n) for n in range(1,10)));self.assertEqual(helpers.evaluate_id_mask(1.,9),1.)
 def test_strict_edges_and_observed_softness_region(self):
  self.assertEqual(helpers.evaluate_id_mask(.1015625,3),0.);self.assertEqual(helpers.evaluate_id_mask(.6,6),0.);self.assertEqual(helpers.evaluate_id_mask(.6,6,.1),1.)
 def test_layer_gate_bit_identity_and_no_ninth_bit(self):
  self.assertEqual(helpers.evaluate_layer_gate(.125,4),1.);self.assertEqual(helpers.evaluate_layer_gate(.125,2),0.);self.assertEqual(helpers.evaluate_layer_gate(.5,32),1.);self.assertEqual(helpers.evaluate_layer_gate(1.,255),0.)
if __name__=='__main__':unittest.main()
