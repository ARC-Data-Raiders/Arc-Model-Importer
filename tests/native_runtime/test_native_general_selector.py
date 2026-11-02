"""Original fractional/overlap selector against an independent sequential oracle."""
try:
    from .native_test_support import ROOT,PACKAGE,namespace
except ImportError:
    from native_test_support import ROOT,PACKAGE,namespace
import ast,importlib,random,unittest
namespace();general=importlib.import_module(PACKAGE+'.materials.dr_primary_graph.general')

def sequential(values,factors,default):
    active=[z for z in range(1,9) if values.get(z) is not None];result=default
    for zone in active:
        factor=factors.get(zone);factor=(1. if zone==active[0] else 0.) if factor is None else max(0.,min(1.,factor))
        result=tuple(a*(1-factor)+b*factor for a,b in zip(result,values[zone])) if isinstance(result,(tuple,list)) else result*(1-factor)+values[zone]*factor
    return result

class GeneralSelectorContracts(unittest.TestCase):
    def test_fractional_overlap_sparse_HDR_and_typed_values(self):
        rng=random.Random(20261028)
        for width in (0,3,4):
            for case in range(64):
                def value():return tuple(rng.uniform(-16.,16.) for _ in range(width)) if width else rng.uniform(-16.,16.)
                zones=(1,3,8) if case%3 else (2,5);values={z:value() for z in zones};start=value();factors={z:rng.uniform(-.25,1.25) for z in zones}
                if case%8==0:factors={}
                if case%8==1:factors={zones[-1]:.75}
                if case%8==2:factors={z:0. for z in zones}
                actual=general.contribution_value(zones,values,factors,start);expected=sequential(values,factors,start)
                with self.subTest(width=width,case=case):
                    a=actual if width else (actual,);b=expected if width else (expected,)
                    for left,right in zip(a,b):self.assertAlmostEqual(left,right,delta=1e-12)
    def test_empty_none_zero_and_implicit_first(self):
        self.assertIsNone(general.contribution_value((),{}, {},None))
        self.assertEqual(general.contribution_value((2,5),{2:0.,5:1.},{2:1.,5:0.},17.),0.)
        self.assertEqual(general.contribution_value((2,5),{2:.25,5:1.},{},17.),.25)
        self.assertEqual(general.contribution_value((2,5),{2:None,5:.75},{},17.),.75)
    def test_no_production_retained_selector_call(self):
        tree=ast.parse((ROOT/'materials/gt_outfit_pbr.py').read_text(encoding='utf-8'));self.assertFalse(any(isinstance(n,ast.Name) and n.id=='_mux_by_zone_retained' for n in ast.walk(tree)))
        tree=ast.parse((ROOT/'materials/dr_primary_graph/public.py').read_text(encoding='utf-8'));calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call)];self.assertFalse(any(isinstance(n.func,ast.Name) and n.func.id=='retained' for n in calls));self.assertTrue(any(isinstance(n.func,ast.Name) and n.func.id=='build_general' for n in calls))
if __name__=='__main__':unittest.main()
