import ast,json,os,runpy,types,unittest
from pathlib import Path
from unittest.mock import patch
OUT=Path(__file__).resolve().parents[1]
REPO=OUT
source=OUT/'Shared/materials/clothing.py';tree=ast.parse(source.read_text(encoding='utf-8'))
node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_stamp_visor_material_source')
ns={'os':os,'json':json,'textures':types.SimpleNamespace(material_lineage_signature=lambda path:[(path,123,456)],_fs_abspath=os.path.abspath)}
exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
stamp=ns[node.name]
fakes=runpy.run_path(str(REPO/'tests/test_dedicated_profile_graphwalk.py'))
D=ast.parse((OUT/'Shared/materials/dispatch.py').read_text(encoding='utf-8'));node=next(n for n in D.body if isinstance(n,ast.FunctionDef) and n.name=='_active_dedicated_outfit_profile')
ns2={'utils':types.SimpleNamespace(_NODE_GROUP='ArcTexturer')};exec(compile(ast.Module(body=[node],type_ignores=[]),'candidate_dispatch.py','exec'),ns2)
profile=ns2[node.name]
class Tests(unittest.TestCase):
 def test_parsed_exact_source_stamped_without_parity_claim(self):
  mat={}
  with patch('os.path.isfile',return_value=True):self.assertTrue(stamp(mat,'exact/Glass.json',{'found':True}))
  self.assertEqual(mat['arc_mi_path'],os.path.abspath('exact/Glass.json'));self.assertEqual(mat['arc_skin_json'],mat['arc_mi_path'])
  self.assertFalse(mat['arc_visor_engine_parity_verified']);self.assertIn('exact',mat['arc_visor_source_signature'])
 def test_unparsed_source_removes_stale_identity(self):
  mat={'arc_mi_path':'old','arc_skin_json':'old','arc_visor_source_signature':'old'}
  with patch('os.path.isfile',return_value=True):self.assertFalse(stamp(mat,'new.json',{'found':False}))
  self.assertNotIn('arc_mi_path',mat);self.assertNotIn('arc_skin_json',mat);self.assertFalse(mat['arc_visor_source_read'])
 def test_missing_source_cannot_be_claimed_even_with_stale_parsed_record(self):
  with patch('os.path.isfile',return_value=False):self.assertFalse(stamp({},'missing.json',{'found':True}))
 def test_only_connected_parsed_nonfallback_visor_is_preserved(self):
  Node,Material,edge=fakes['Node'],fakes['Material'],fakes['edge']
  visor,output=Node('GROUP',group='Visor'),Node('OUTPUT_MATERIAL')
  mat=Material([visor,output],[edge(visor,output)],arc_visor_setup='bundled-visor-approx-v1',arc_visor_source_read=True)
  self.assertEqual(profile(mat),'visor_approx');mat['arc_visor_fallback']=1;mat['arc_visor_setup']='manual_fallback';self.assertEqual(profile(mat),'visor_approx_manual')
  mat['arc_visor_fallback']=0;mat['arc_visor_source_read']=False;self.assertEqual(profile(mat),'')
  mat['arc_visor_source_read']=True;mat.node_tree.edges=[];self.assertEqual(profile(mat),'')
  stub=Node('BSDF_PRINCIPLED');mat.node_tree.nodes=[stub,output];mat.node_tree.edges=[edge(stub,output)];self.assertEqual(profile(mat),'')
class DependencyTests(unittest.TestCase):
 def test_parent_edit_invalidates_same_path(self):
  node=next(n for n in D.body if isinstance(n,ast.FunctionDef) and n.name=='_dedicated_outfit_material_current')
  deps=[('exact.json',123,456)]
  namespace={'json':json,'_active_dedicated_outfit_profile':lambda m:'visor_approx_manual',
      'textures':types.SimpleNamespace(_fs_abspath=lambda p:p,material_lineage_signature=lambda path:deps),
      '_material_identity_path':lambda path:path}
  exec(compile(ast.Module(body=[node],type_ignores=[]),'candidate_dispatch.py','exec'),namespace)
  mat={'arc_mi_path':'exact.json','arc_visor_source_signature':json.dumps(deps)}
  current=namespace[node.name]
  self.assertTrue(current(mat,'exact.json'));self.assertFalse(current(mat,'another.json'))
  deps[0]=('exact.json',124,456);self.assertFalse(current(mat,'exact.json'))
if __name__=='__main__':unittest.main()
