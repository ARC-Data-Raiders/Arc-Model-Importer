"""Native decal accumulation, scalar import and composition-order regressions."""
import ast, importlib, re, types, unittest
from unittest.mock import patch
try:
    from .native_test_support import ROOT, PACKAGE, namespace
except ImportError:
    from native_test_support import ROOT, PACKAGE, namespace
namespace()
m=importlib.import_module(PACKAGE+'.Shared.materials.dr_layered_decals')
class Scalar:
    def __init__(self,value):self.value=value;self.node=types.SimpleNamespace(use_clamp=False)
    def read(self):return max(0.,min(1.,self.value)) if self.node.use_clamp else self.value
def number(x):return x.read() if isinstance(x,Scalar) else x
def math_node(api,nodes,links,role,operation,a,b=None):
    a,b=number(a),number(b)
    return Scalar({'ADD':lambda:a+b,'MULTIPLY':lambda:a*b}[operation]())
def mix_node(api,nodes,links,role,a,b,w,**kw):
    w=max(0.,min(1.,number(w)));return Scalar(number(a)*(1-w)+number(b)*w)
class LayeredDecalTests(unittest.TestCase):
    def setUp(self):
        self.math=patch.object(m,'_math',math_node);self.mix=patch.object(m,'_mix',mix_node)
        self.math.start();self.mix.start();self.addCleanup(self.math.stop);self.addCleanup(self.mix.stop)
    def slots(self):
        return [dict(index=1,use=1.,alpha=.5,roughness=.2,surface=.4,wear=0.),dict(index=2,use=1.,alpha=.25,roughness=.8,surface=.8,wear=.5)]
    def test_native_surface_accumulator_is_not_sequential_base_overpaint(self):
        # Native: decal lerp(.2,.8,.25*.8)=.32, coverage=.5*.4+.25*.8=.4.
        rough,metal=m.surface(None,None,None,.6,.1,[dict(s,metallic=s['roughness']) for s in self.slots()])
        self.assertAlmostEqual(number(rough),.488)
        self.assertAlmostEqual(number(metal),.188)
    def test_disabled_leading_slot_does_not_supply_first_sample(self):
        slots=self.slots();slots[0].update(use=0.,alpha=0.,roughness=.99)
        value,coverage=m._stack(None,None,None,slots,'roughness','surface')
        self.assertAlmostEqual(number(value),.8);self.assertAlmostEqual(number(coverage),.2)
    def test_zero_surface_override_preserves_base(self):
        slots=[dict(s,surface=0.) for s in self.slots()]
        rough,_=m.surface(None,None,None,.6,.1,[dict(s,metallic=1.) for s in slots])
        self.assertAlmostEqual(number(rough),.6)
    def test_wear_override_one_retains_garment_wear(self):
        self.assertAlmostEqual(number(m.wear_multiplier(None,None,None,[dict(s,wear=1.) for s in self.slots()])),1.)
        self.assertAlmostEqual(number(m.wear_multiplier(None,None,None,self.slots())),.5)
    def test_scalar_import_and_disabled_switch(self):
        tree=ast.parse((ROOT/'Shared/textures.py').read_text(encoding='utf8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_decals_from_props')
        ns={'re':re};exec(compile(ast.Module(body=[fn],type_ignores=[]),'native-parser','exec'),ns)
        props={'TextureParameterValues':[{'ParameterInfo':{'Name':'2_DecalColor'},'ParameterValue':{'ObjectName':"Texture2D'T_Line_Single_C'"}}],
            'ScalarParameterValues':[{'ParameterInfo':{'Name':'2_'+key},'ParameterValue':value} for key,value in [('SurfaceOverride',.43656728),('WearOverride',1.),('BaseNormalOverride',0.),('DetailNormalOverride',0.),('Tiling',1.)]]}
        row=ns['_decals_from_props'](props)[0]
        self.assertEqual([row[k] for k in ['surface_override','wear_override','base_normal_override','detail_normal_override','tiling']],[.43656728,1.,0.,0.,1.])
        props['StaticParametersRuntime']={'StaticSwitchParameters':[{'ParameterInfo':{'Name':'2_UseDecal'},'Value':False}]}
        self.assertEqual(ns['_decals_from_props'](props),[])
    def test_no_outfit_name_or_local_roughness_override_in_compositor(self):
        tree=ast.parse((ROOT/'Shared/materials/dr_layered_decals.py').read_text(encoding='utf8'))
        strings=[n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str)]
        self.assertFalse(any('Bomber' in x or 'T_Line_Single' in x for x in strings))
if __name__=='__main__':unittest.main()
