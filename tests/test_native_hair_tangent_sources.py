"""Exact tangent-family gates; no Blender, no scalar-name inference."""
import ast,unittest
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'materials/hair_tangent.py'
n=next(x for x in ast.parse(p.read_text(encoding='utf-8')).body if isinstance(x,ast.FunctionDef) and x.name=='is_native_hair_tangent_source')
g={};exec(compile(ast.Module(body=[n],type_ignores=[]),str(p),'exec'),g);gate=g['is_native_hair_tangent_source']
class Tests(unittest.TestCase):
 def test_exact_colormap_companion(self):self.assertTrue(gate(['/root/Pioneer/Characters/Heads/Shared/Hair/Materials/M_Hair_Metahuman_ColorMap.arc-material.json']))
 def test_exact_metahuman01_current_parent(self):self.assertTrue(gate([], 'PioneerGame/Content/Pioneer/Characters/Heads/Shared/Hair/Materials/M_Hair_Metahuman_01'))
 def test_same_basename_elsewhere_is_not_proof(self):self.assertFalse(gate(['/root/Other/M_Hair_Metahuman_01.arc-material.json']))
 def test_similar_unevaluated_master_rejected(self):self.assertFalse(gate(['/Game/Pioneer/Characters/Heads/Shared/Hair/Materials/M_Hair_Metahuman_02']))
if __name__=='__main__':unittest.main()
