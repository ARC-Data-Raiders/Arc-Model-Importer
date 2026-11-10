"""Exercise native graph operations against retained native analytic vectors."""
import importlib.util,json,math,unittest
from pathlib import Path
from types import SimpleNamespace
OUT=Path(__file__).resolve().parents[1]/'Shared/materials'
spec=importlib.util.spec_from_file_location('glass_native_candidate',OUT/'glass_native.py');M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
PARAM=json.loads((Path(__file__).resolve().parent/'fixtures/native_opaque_parameters.json').read_text())
CASES=json.loads((Path(__file__).resolve().parent/'fixtures/native_opaque_oracles.json').read_text())['cases']
class Graph:
 def __init__(self):self.samples={};self.values={}
 def value(self,value,label):self.values[label]=value;return value
 def split(self,value):return tuple(value[:3])
 def combine(self,*values):return tuple(values)
 def rgb(self,rgba,label):return tuple(rgba[:3])
 def clamp(self,x):return max(0,min(1,x))
 def lerp(self,a,b,f,label=''):return a+(b-a)*f
 def vlerp(self,a,b,f,label=''):return tuple(self.lerp(x,y,f) for x,y in zip(a,b))
 def math(self,op,a,b=0,label=''):
  return {'ADD':lambda:a+b,'SUBTRACT':lambda:a-b,'MULTIPLY':lambda:a*b,
   'DIVIDE':lambda:a/b if b else 0,'ABSOLUTE':lambda:abs(a),
   'GREATER_THAN':lambda:float(a>b),'MINIMUM':lambda:min(a,b),'MAXIMUM':lambda:max(a,b),
   'SQRT':lambda:math.sqrt(a),'FRACT':lambda:a-math.floor(a)}[op]()
 def vector(self,op,a,b=(0,0,0),label=''):
  if op=='LENGTH':return math.sqrt(sum(v*v for v in a))
  if op=='NORMALIZE':
   d=self.vector('LENGTH',a);return tuple(v/d for v in a)
  if op=='SCALE':return tuple(v*b for v in a)
  return tuple({'ADD':lambda:x+y,'SUBTRACT':lambda:x-y,'MULTIPLY':lambda:x*y}[op]() for x,y in zip(a,b))
 def decode_rg(self,rgb):
  x,y=2*rgb[0]-1,2*rgb[1]-1;return (x,y,math.sqrt(max(1-x*x-y*y,0)))
 def normal_to_world(self,value,uv_name):return value # Identity native basis; actual basis render is a separate required probe.
 def texture(self,image,uv,label):self.samples[label]=uv;return SimpleNamespace(outputs={'Color':image})

def run_case(case,overrides=None):
 record={'scalars':dict(PARAM['scalars'],**(overrides or {})),'colors':PARAM['colors']}
 g=Graph();rg=(.7,.4) if case['name'].endswith('normal') else (.5,.5)
 images={'DirtNormal':(*rg,1),'DirtMask':(case['dirt_sample_r'],0,0),'Roughness':(case['rough_sample_r'],0,0)}
 uv=case['uv_native'];result=M.build_response(g,record,images,(uv[0],1-uv[1],0),view_dot=case['normal_dot_view_after_dirt'])
 return g,result
class OpaqueGraphTests(unittest.TestCase):
 def test_nine_native_reference_vectors(self):
  for case in CASES:
   with self.subTest(case=case['name']):
    g,result=run_case(case)
    for native,key in [('color','color'),('roughness','roughness'),('metallic','metallic'),('circle','circle'),('dirt','dirt'),('normal','tangent_normal'),('view_factor','view_weight')]:
     expected=case['expected'][native];actual=result[key]
     if isinstance(expected,list):self.assertTrue(all(abs(a-b)<1e-12 for a,b in zip(actual,expected)),(native,actual,expected))
     else:self.assertAlmostEqual(actual,expected,places=12)
    self.assertEqual(result['alpha'],1);self.assertEqual(result['transmission'],0);self.assertEqual(result['specular'],.5)
 def test_native_tiling_converts_v_after_tiling(self):
  case=CASES[0];g,_=run_case(case);u,v=case['uv_native'];d=PARAM['scalars']['DirtTiling']
  self.assertEqual(g.samples['Native DirtMask data R'],(u*d,1-v*d,0))
 def test_dirt_never_attenuates_dry_native_metallic(self):
  for case in CASES:
   _,result=run_case(case)
   self.assertAlmostEqual(result['metallic'],max(0,min(1,PARAM['scalars']['Metallic'])),places=12)
  dirty=next(case for case in CASES if case['expected']['dirt']>.9)
  _,result=run_case(dirty,{'Metallic':.77})
  self.assertGreater(result['dirt'],.9)
  self.assertAlmostEqual(result['metallic'],.77,places=12)
 def test_explicit_metallic_zero_is_preserved(self):
  _,result=run_case(CASES[0],{'Metallic':0});self.assertEqual(result['metallic'],0)
 def test_native_zero_circle_scale_does_not_create_full_circle(self):
  _,result=run_case(CASES[0],{'CircularScaleU':0,'CircularScaleV':0,'CircularSize':0});self.assertEqual(result['circle'],0)
 def test_zero_softness_native_step_is_not_blender_zero_divide(self):
  _,result=run_case(CASES[5],{'DirtSoftness':0,'MaskDirt':0});self.assertEqual(result['dirt'],1)
if __name__=='__main__':unittest.main()
