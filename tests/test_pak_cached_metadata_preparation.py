"""Old primary Pak geometry gets metadata-only preparation; FModel stays offline."""
import ast,functools,json,os,sys,tempfile,types,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def function(source,name,namespace):
 tree=ast.parse((ROOT/source).read_text(encoding='utf-8-sig'))
 node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(ROOT/source),'exec'),namespace)
 return namespace[name]

class MetadataPreparationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.model=self.root/'PioneerGame/Content/Pioneer/Characters/SK_Test.uemodel';self.model.parent.mkdir(parents=True);self.model.write_bytes(b'original-model')
  self.sidecar=self.model.with_suffix('.arc-material-slots.json');self.calls=[];self.valid=True;self.skinned=True
  package=types.ModuleType('metadata_fixture');package.__path__=[]
  catalog=types.ModuleType('metadata_fixture.pak_catalog');catalog.filesystem_path=os.path.abspath;catalog.display_path=lambda path:str(path).removeprefix(chr(92)*2+'?'+chr(92))
  ue=types.ModuleType('metadata_fixture.uemodel_import');ue._read_uemodel_root=lambda path:('mesh',10,True,[('SKELETON',b'')] if self.skinned else [])
  ue.parse_uemodel=lambda path:('mesh',10,[types.SimpleNamespace(name='LOD0',materials=[])])
  native=types.ModuleType('metadata_fixture.native_material_slots');native.load_native_material_layout=lambda *a:(object() if self.valid else None)
  self.package=package;self.ue=ue
  for module in (package,catalog,ue,native):sys.modules[module.__name__]=module
  self.scene=types.SimpleNamespace(arc_import_source='PAK')
  self.ns={'os':os,'json':json,'lru_cache':functools.lru_cache,'__package__':package.__name__,
   'configured_dir':lambda scene:'GamePaks','resolve_usmap':lambda scene:'current.usmap',
   'cache_dir':lambda:str(self.root),'index_dir':lambda:str(self.root/'indexes'),'exe_path':lambda:'ArcPak.Export.exe',
   '_addon_dir':lambda:str(self.root),'saved_material_package':lambda path:'PioneerGame/Content/Pioneer/Characters/SK_Test',
   '_CREATE_NO_WINDOW':0,'_log_tail':lambda log:log[-300:],
   'ensure_cached_material_hierarchy':lambda *a,**kwargs:None,
   'mesh_cache_is_current':lambda *a,**k:True}
  def run(command,**kwargs):
   self.calls.append(command);self.sidecar.write_text('{}');return types.SimpleNamespace(returncode=0,stdout='metadata-only done',stderr='')
  self.ns['subprocess']=types.SimpleNamespace(run=run)
  for name in ('_mesh_metadata_state','mesh_material_metadata_is_current','extraction_command','material_metadata_plan','ensure_cached_mesh_material_metadata'):
   function('pak_extract.py',name,self.ns)
 def test_files_mode_never_inspects_game_or_launches_helper(self):
  self.scene.arc_import_source='FILES';self.ns['configured_dir']=lambda scene:self.fail('FModel inspected game')
  self.ns['ensure_cached_mesh_material_metadata'](self.scene,[str(self.model)]);self.assertFalse(self.calls)
 def test_pak_cached_geometry_missing_sidecar_gets_metadata_only_exact_file_plan(self):
  plan=self.ns['material_metadata_plan'](self.scene,[str(self.model)])
  self.assertEqual(plan['command'][1],'mesh-metadata');self.assertIn('--model-file',plan['command'])
  self.assertEqual(plan['models'],[str(self.model)]);self.assertNotIn('extract',plan['command'])
 def test_automatic_warmup_keeps_model_bytes_and_revalidates_metadata(self):
  before=self.model.read_bytes();self.ns['ensure_cached_mesh_material_metadata'](self.scene,[str(self.model)])
  self.assertEqual(self.model.read_bytes(),before);self.assertTrue(self.sidecar.is_file());self.assertEqual(len(self.calls),1)
  self.ns['ensure_cached_mesh_material_metadata'](self.scene,[str(self.model)]);self.assertEqual(len(self.calls),1)
 def test_fmodel_file_outside_primary_cache_never_gets_helper_even_in_pak_scene(self):
  outside=self.root.parent/'unrelated-fmodel-model.uemodel'
  self.assertIsNone(self.ns['material_metadata_plan'](self.scene,[str(outside)]));self.assertFalse(self.calls)
 def test_static_uemodel_without_skeleton_does_not_request_skinned_metadata(self):
  self.skinned=False;self.assertIsNone(self.ns['material_metadata_plan'](self.scene,[str(self.model)]))
 def test_failed_or_unvalidated_metadata_stops_instead_of_using_old_cache(self):
  self.valid=False
  with self.assertRaisesRegex(RuntimeError,'does not match cached geometry'):self.ns['ensure_cached_mesh_material_metadata'](self.scene,[str(self.model)])
 def test_nonzero_helper_result_is_not_ready(self):
  self.ns['subprocess'].run=lambda *a,**kw:types.SimpleNamespace(returncode=1,stdout='wrong header',stderr='')
  with self.assertRaisesRegex(RuntimeError,'wrong header'):self.ns['ensure_cached_mesh_material_metadata'](self.scene,[str(self.model)])
 def test_metadata_command_rejects_unpaired_models_and_preserves_argument_strings(self):
  with self.assertRaises(ValueError):self.ns['extraction_command']('game','map',['A'],metadata_only=True,model_files=['one','two'])
  command=self.ns['extraction_command']('game dir','map path',['A','B'],metadata_only=True,model_files=['one path','two path'])
  self.assertEqual(command.count('--model-file'),2);self.assertIn('one path',command)
 def test_all_cached_batch_still_schedules_background_metadata_preparation(self):
  appearance=types.ModuleType('metadata_fixture.appearance');appearance._mesh_file_usable=lambda path:True
  pak=types.ModuleType('metadata_fixture.pak_extract')
  for name,value in self.ns.items():
   if not name.startswith('__'):setattr(pak,name,value)
  pak.game_path=lambda path:path;pak._expected_uemodel=lambda out,path:str(self.model);pak.mesh_cache_is_current=lambda *a,**k:True;pak._extraction_revision=lambda scene:'geometry-rev'
  sys.modules[appearance.__name__]=appearance;sys.modules[pak.__name__]=pak;self.package.appearance=appearance;self.package.pak_extract=pak
  submissions=[];executor=types.SimpleNamespace(submit=lambda callback,commands:submissions.append((callback,commands)) or 'future')
  owner=types.SimpleNamespace(_scene=self.scene,_reference=False,_executor=executor)
  namespace={'__package__':self.package.__name__,'_run_export_commands':object()}
  prepare=function('appearance_queue.py','_prepare_batch',namespace)
  self.assertEqual(prepare(owner,[{'part':'BODY','rel':'exact-package','id':'test'}]),'future')
  self.assertEqual(submissions[0][1][0][1],'mesh-metadata');self.assertEqual(owner._extract_plan['paths'],[])
 def test_extended_windows_model_path_remains_inside_short_cache_root(self):
  model=chr(92)*2+'?'+chr(92)+str(self.model)
  plan=self.ns['material_metadata_plan'](self.scene,[model])
  self.assertIsNotNone(plan);self.assertEqual(plan['models'],[os.path.abspath(model)])
 def test_metadata_mount_has_no_preview_or_index_generation_and_preserves_pairs(self):
  source=(ROOT/'ArcPak/Program.cs').read_text(encoding='utf-8-sig')
  body=source.split('private static AbstractVfsFileProvider OpenMetadataProvider(',1)[1].split('private static AbstractVfsFileProvider OpenProvider(',1)[0]
  for forbidden in ('CachePreviewIcons(', 'Reindex(', 'PakIndex.Write(', 'paths.Clear(', 'paths.AddRange('):self.assertNotIn(forbidden,body)
  self.assertIn('TrySelective(',body);self.assertIn('MountEverything(',body)
  self.assertIn('(metadataOnly || materialMetadataOnly) ? OpenMetadataProvider(',source)
 def test_background_worker_does_not_read_blender_or_scene(self):
  tree=ast.parse((ROOT/'appearance_queue.py').read_text(encoding='utf-8-sig'))
  worker=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_run_export_commands')
  text=ast.unparse(worker)
  self.assertNotIn('bpy',text);self.assertNotIn('_scene',text);self.assertNotIn('arc_import_source',text)
 def test_worker_resolver_never_reads_bpy_context_or_launches_helper(self):
  import threading
  class ForbiddenBpy:
   @property
   def context(self):raise AssertionError('worker touched Blender context')
  class StopAfterGuard(Exception):pass
  def stop(path):raise StopAfterGuard()
  ns={'bpy':ForbiddenBpy(),'textures':types.SimpleNamespace(_fs_abspath=lambda path:path),'get_part_key':stop}
  resolver=function('importing.py','resolve_outfit_preset_material_bindings',ns)
  outcomes=[]
  def worker():
   try:resolver('preset.json','mesh.uemodel')
   except Exception as error:outcomes.append(error)
  thread=threading.Thread(target=worker);thread.start();thread.join()
  self.assertEqual(len(outcomes),1);self.assertIsInstance(outcomes[0],StopAfterGuard)
 def test_stale_primary_geometry_is_not_relabelled_by_metadata_only_warmup(self):
  self.ns['mesh_cache_is_current']=lambda *a,**k:False
  with self.assertRaisesRegex(RuntimeError,'geometry source stamp is stale'):
   self.ns['ensure_cached_mesh_material_metadata'](self.scene,[str(self.model)])
  self.assertFalse(self.calls)
 def test_worker_command_plan_stops_before_extract_if_metadata_fails(self):
  calls=[];namespace={'_run_export':lambda command:(calls.append(command) or (1,'metadata failure'))}
  worker=function('appearance_queue.py','_run_export_commands',namespace)
  self.assertEqual(worker([['metadata'],['extract']])[0],1);self.assertEqual(calls,[['metadata']])

if __name__=='__main__':unittest.main()
