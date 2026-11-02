"""Portable numerical contracts for original layered stage composition."""
try:
    from .native_test_support import ROOT,PACKAGE,namespace
except ImportError:
    from native_test_support import ROOT,PACKAGE,namespace
import ast,importlib,json,unittest
from pathlib import Path
namespace();composer=importlib.import_module(PACKAGE+'.materials.dr_layered_composer');core=importlib.import_module(PACKAGE+'.material_runtime.core')
FIXTURE=json.loads((Path(__file__).parent/'fixtures/layered_composer_numeric_checkpoints.json').read_text(encoding='utf-8'))
class LayeredComposerCheckpoints(unittest.TestCase):
    def test_independent_numeric_stage_checkpoints(self):
        self.assertEqual(len(FIXTURE['cases']),64)
        for index,row in enumerate(FIXTURE['cases']):
            kind=row['kind']
            if kind=='response':expression=composer.response_circuit()
            elif kind=='wear':expression=composer.wear_circuit(row['hard'])
            elif kind=='emission':expression=composer.emission_circuit(row['rows'],overall=row['overall'])
            else:expression=getattr(composer,kind+'_circuit')()
            with self.subTest(case=index,stage=kind):
                actual=core.evaluate(expression,{'attribute':row['inputs']});expected=row['expected'];a=actual if isinstance(actual,(tuple,list)) else (actual,);b=expected if isinstance(expected,list) else (expected,);self.assertEqual(len(a),len(b))
                for value,checkpoint in zip(a,b):self.assertAlmostEqual(value,checkpoint,delta=FIXTURE['tolerance'])
    def test_negative_literal_gain_does_not_invert_emission(self):
        cases=[row for row in FIXTURE['cases'] if row['kind']=='emission' and row['overall']<0]
        self.assertTrue(cases)
        for row in cases:self.assertEqual(core.evaluate(composer.emission_circuit(row['rows'],overall=row['overall']),{'attribute':row['inputs']}),(0.,0.,0.))
    def test_primary_routes_enter_original_composer(self):
        tree=ast.parse((ROOT/'materials/gt_outfit_pbr.py').read_text(encoding='utf-8'))
        calls={node.func.attr for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name) and node.func.value.id=='dr_layered_composer'}
        self.assertTrue({'build_palette','build_wear','build_stage_group','build_scalar_response','build_cloth','build_emission','build_terminal'}<=calls)
        source=ast.parse((ROOT/'materials/dr_layered_composer.py').read_text(encoding='utf-8'))
        self.assertFalse(any(isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='load' and isinstance(node.func.value,ast.Attribute) and node.func.value.attr=='libraries' for node in ast.walk(source)))
if __name__=='__main__':unittest.main()
