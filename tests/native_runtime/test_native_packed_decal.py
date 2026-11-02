"""Portable frozen-observation decoder regressions; no Blender or asset cache."""
try:
    from .native_test_support import ROOT,PACKAGE,namespace
except ImportError:
    from native_test_support import ROOT,PACKAGE,namespace
import ast,importlib,json,unittest
from pathlib import Path
namespace()
decoder=importlib.import_module(PACKAGE+'.materials.dr_packed_decal')
core=importlib.import_module(PACKAGE+'.material_runtime.core')
FIXTURE=json.loads((Path(__file__).parent/'fixtures/packed_decal_observed_checkpoints.json').read_text(encoding='utf-8'))

class PackedDecalObservedTests(unittest.TestCase):
    def test_frozen_observed_eighty_cases(self):
        self.assertEqual(len(FIXTURE['cases']),80)
        self.assertEqual([s['case_count'] for s in FIXTURE['sources']],[60,20])
        outputs=decoder.contract_dag()
        for row in FIXTURE['cases']:
            inputs={'attribute':{'Color':row['color'],'Alpha':row['alpha'],'Mask':row['mask']}}
            for key,expected in row['expected'].items():
                with self.subTest(source=row['source'],case=row['case'],output=key):
                    actual=core.evaluate(outputs[key],inputs)
                    a=actual if isinstance(actual,(tuple,list)) else (actual,)
                    b=expected if isinstance(expected,list) else (expected,)
                    self.assertEqual(len(a),len(b))
                    for value,observed in zip(a,b):self.assertAlmostEqual(value,observed,delta=FIXTURE['tolerance'])

    def test_diagnostics_and_normal_share_one_decode(self):
        outputs=decoder.contract_dag();seen={}
        def visit(e):
            if not isinstance(e,core.Expr) or id(e) in seen:return
            seen[id(e)]=e
            for arg in e.args:visit(arg)
        for e in outputs.values():visit(e)
        for role in ('Normal.X.Signed','Normal.Y.Signed','Normal.Z.Positive'):
            self.assertEqual(sum(e.attrs.get('name')==role for e in seen.values()),1)
        self.assertEqual(set(outputs),{'Normal','Roughness','Metallic','Nx','Ny','Nz'})

    def test_gt_binds_native_metallic_alpha(self):
        # Packed decal A is native metallic; the importer must pass texture Alpha.
        tree=ast.parse((ROOT/'materials/gt_outfit_pbr.py').read_text(encoding='utf-8'))
        links=[];allowed_dynamic=set()
        def dd_input(node):
            return isinstance(node,ast.Subscript) and isinstance(node.value,ast.Attribute) and node.value.attr=='inputs' and isinstance(node.value.value,ast.Name) and node.value.value.id=='dd'
        for node in ast.walk(tree):
            if isinstance(node,ast.For) and isinstance(node.target,ast.Name) and node.target.id=='iname' and isinstance(node.iter,(ast.Tuple,ast.List)):
                values=[v.value for v in node.iter.elts if isinstance(v,ast.Constant)]
                if any(dd_input(n) for n in ast.walk(node)):
                    self.assertEqual(values,['Color','DecalData','Decal Data']);allowed_dynamic.add('iname')
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='new' and len(node.args)==2 and dd_input(node.args[1]):links.append(node.args[1].slice)
            if isinstance(node,(ast.Assign,ast.AnnAssign,ast.AugAssign)):
                targets=node.targets if isinstance(node,ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target,ast.Attribute) and dd_input(target.value):
                        self.fail('GT mutates decoder socket default instead of preserving input contract')
        self.assertEqual(len(links),3)
        self.assertEqual(sorted(n.value for n in links if isinstance(n,ast.Constant)),['Alpha','Mask'])
        self.assertEqual([n.id for n in links if isinstance(n,ast.Name)],['iname'])
        self.assertIn('iname',allowed_dynamic)
        alpha_link=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='new' and len(n.args)==2 and dd_input(n.args[1]) and isinstance(n.args[1].slice,ast.Constant) and n.args[1].slice.value=='Alpha')
        self.assertEqual(ast.unparse(alpha_link.args[0]), "data_tex.outputs['Alpha']")
        # The generic decoder retains a neutral default for callers without data.
        build=next(n for n in ast.parse((ROOT/'materials/dr_packed_decal.py').read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef) and n.name=='build_group')
        alpha=[n for n in ast.walk(build) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='socket' and n.args and isinstance(n.args[0],ast.Constant) and n.args[0].value=='Alpha']
        self.assertEqual(len(alpha),1);self.assertEqual([ast.literal_eval(a) for a in alpha[0].args],['Alpha','INPUT','NodeSocketFloat',0.])

    def test_primary_decoder_has_no_retained_library_loader(self):
        tree=ast.parse((ROOT/'materials/dr_packed_decal.py').read_text(encoding='utf-8'))
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call)]
        self.assertFalse(any(isinstance(n.func,ast.Attribute) and n.func.attr=='ensure_decal_data_node_group' for n in calls))
        self.assertFalse(any(isinstance(n.func,ast.Attribute) and n.func.attr=='load' and isinstance(n.func.value,ast.Attribute) and n.func.value.attr=='libraries' for n in calls))

if __name__=='__main__':unittest.main()
