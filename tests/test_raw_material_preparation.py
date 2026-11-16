import ast,functools,hashlib,json,os,re,sys,tempfile,types,unittest
from pathlib import Path
from section_fixture import add_parents
ROOT=Path(__file__).resolve().parents[1]
REPO=ROOT
def load(name,namespace):
 tree=ast.parse((ROOT/'Shared/pak_extract.py').read_text(encoding='utf-8-sig'));node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(ROOT/'Shared/pak_extract.py'),'exec'),namespace);return namespace[name]
class RawMaterialTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  pkg=types.ModuleType('raw_fixture');pkg.__path__=[];catalog=types.ModuleType('raw_fixture.Outfit_Accessories.pak_catalog');catalog.filesystem_path=os.path.abspath;catalog.display_path=str;catalog.installed_build=lambda game:'25979580'
  tree=ast.parse((REPO/'Outfit_Accessories/pak_catalog.py').read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='canonical_path');exec(compile(ast.Module(body=[node],type_ignores=[]),'Outfit_Accessories/pak_catalog.py','exec'),catalog.__dict__)
  for m in (pkg,catalog):sys.modules[m.__name__]=m
  import importlib.util;spec=importlib.util.spec_from_file_location('raw_fixture.Shared.material_texture_metadata',REPO/'Shared/material_texture_metadata.py');mtm=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mtm;spec.loader.exec_module(mtm);pkg.material_texture_metadata=mtm;add_parents(sys.modules,'raw_fixture')
  self.catalog=catalog;self.scene=types.SimpleNamespace(arc_import_source='PAK')
  self.ns={'__package__':'raw_fixture.Shared','os':os,'re':re,'json':json,'hashlib':hashlib,'lru_cache':functools.lru_cache,
   'configured_dir':lambda scene:str(self.root/'game'),'cache_dir':lambda:str(self.root),'resolve_usmap':lambda scene:str(self.root/'map.usmap'),
   'index_dir':lambda:str(self.root/'index'),'exe_path':lambda:'ArcPak.Export.exe','_addon_dir':lambda:str(self.root),
   'saved_material_package':lambda path:catalog.canonical_path(str(path).replace('\\','/').split('/RemappedPlugins/Content/',1)[-1] if False else 'RemappedPlugins/Content/'+str(path).replace('\\','/').split('/RemappedPlugins/Content/',1)[1]),
   'textures':types.SimpleNamespace(authored_visual_part_materials=lambda model:[]),'_log_tail':lambda text:text}
  for name in ('_material_companion','_raw_material_record','_material_hierarchy_dependencies','_raw_material_digest','_material_dependency_signature','_material_receipt_root','_write_material_receipt','complete_raw_material_metadata','material_hierarchy_is_current','_installed_material_provenance','raw_material_metadata_plan','extraction_command','_mapping_sha256','_extraction_revision'):load(name,self.ns)
  # pak_extract now imports textures lazily (`from . import textures` inside the plan); bind the same stub object.
  sys.modules['raw_fixture.Shared.textures']=self.ns['textures'];sys.modules['raw_fixture.Shared'].textures=self.ns['textures'];self.addCleanup(sys.modules.pop,'raw_fixture.Shared.textures',None)
  self.ns['_installed_material_provenance']=lambda game:('25979580','922241259843304590')
  FAKE={'arcpak':{'assembly_name':'fixture','sha256':'0'}};mtm.producer_signature=lambda exe:FAKE;mtm.closure_dependencies=lambda *args,**kw:[];self.fake=FAKE;write=self.ns['_write_material_receipt']
  self.ns['_write_material_receipt']=lambda f,rev,deps,**kw:write(f,rev,deps,**{'producer':FAKE,'metadata_revision':mtm.metadata_revision(rev,FAKE),'mapping_sha':self.mapping_sha(),**kw})
  self.leaf='PioneerGame/Content/Pioneer/Test/MI_Leaf';self.parent='PioneerGame/Content/Pioneer/Test/M_Master'
  self.file=self.root/'RemappedPlugins/Content/Pioneer/Test/MI_Leaf.json';self.file.parent.mkdir(parents=True);self.file.write_text('{}')
  self.make(self.leaf,self.parent);self.make(self.parent,'')
  (self.root/'game').mkdir();(self.root/'game/game.utoc').write_bytes(b'container');(self.root/'map.usmap').write_bytes(b'mapping')
  dependencies=self.ns['_material_hierarchy_dependencies'](str(self.file),'25979580','922241259843304590')
  self.ns['_write_material_receipt'](str(self.file),self.ns['_extraction_revision'](self.scene),dependencies)
 def path(self,key):return self.root/('RemappedPlugins/Content/'+key.split('/Content/',1)[1]+'.arc-material.json')
 def make(self,key,parent):
  ref=lambda p:'/Game/'+p.split('/Content/',1)[1]+'.'+p.rsplit('/',1)[-1] if p else ''
  data={'Exports':[{'Name':key.rsplit('/',1)[-1],'Properties':{'Parent':{'ObjectPath':ref(parent)} if parent else None}}],
   'DataRaidersSource':{'schema':'dataraiders.native-material-hierarchy','version':1,'package':key,'object_path':ref(key),'parent':ref(parent),'build_id':'25979580','depot_manifest':'922241259843304590'}}
  p=self.path(key);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data));return data
 def mapping_sha(self):st=os.stat(self.root/'map.usmap');return self.ns['_mapping_sha256'](str(self.root/'map.usmap'),st.st_mtime_ns,st.st_size)
 def plan_extra(self,rev):return {'producer':self.fake,'metadata_revision':sys.modules['raw_fixture.Shared.material_texture_metadata'].metadata_revision(rev,self.fake),'mapping_sha':self.mapping_sha()}
 def current(self,build='25979580',depot='922241259843304590'):return self.ns['material_hierarchy_is_current'](str(self.file),build,depot,self.ns['_extraction_revision'](self.scene))
 def test_exact_remapped_parent_closure_current(self):self.assertTrue(self.current())
 def test_missing_parent_needs_raw_upgrade(self):self.path(self.parent).unlink();self.assertFalse(self.current())
 def test_legacy_raw_without_provenance_needs_upgrade(self):self.path(self.leaf).write_text('{"Exports":[]}');self.assertFalse(self.current())
 def test_changed_build_or_depot_invalidates(self):self.assertFalse(self.current('new'));self.assertFalse(self.current(depot='new'))
 def test_source_package_mismatch_is_error(self):
  d=self.make(self.leaf,self.parent);d['DataRaidersSource']['package']=self.parent;self.path(self.leaf).write_text(json.dumps(d))
  with self.assertRaisesRegex(ValueError,'provenance mismatch'):self.current()
 def test_parent_provenance_disagreement_is_error(self):
  d=self.make(self.leaf,self.parent);d['DataRaidersSource']['parent']='';self.path(self.leaf).write_text(json.dumps(d))
  with self.assertRaisesRegex(ValueError,'Parent disagrees'):self.current()
 def test_parent_cycle_is_error(self):
  self.make(self.parent,self.leaf)
  with self.assertRaisesRegex(ValueError,'cycle'):self.current()
 def test_files_source_never_reads_game_or_launches_helper(self):
  self.scene.arc_import_source='FILES';self.ns['configured_dir']=lambda scene:self.fail('offline source touched Steam')
  self.assertIsNone(self.ns['raw_material_metadata_plan'](self.scene,material_paths=[str(self.file)]))
 def test_ready_material_never_requests_export(self):self.assertIsNone(self.ns['raw_material_metadata_plan'](self.scene,material_paths=[str(self.file)]))
 def test_stale_material_plan_pairs_exact_physical_alias(self):
  self.path(self.parent).unlink();p=self.ns['raw_material_metadata_plan'](self.scene,material_paths=[str(self.file)])
  self.assertEqual(p['command'][1],'material-metadata');self.assertIn('--material-file',p['command']);self.assertIn(str(self.file),p['command']);self.assertEqual(p['paths'],[self.leaf]);self.assertNotIn('extract',p['command'])
 def test_material_command_rejects_unpaired_file(self):
  with self.assertRaises(ValueError):self.ns['extraction_command']('game','map',['one'],material_metadata_only=True,material_files=['a','b'])
 def test_geometry_stamp_changes_for_build_root_and_mapping(self):
  revision=self.ns['_extraction_revision'];first=revision(self.scene);self.assertTrue(first.startswith('native-source-v1:'))
  self.ns['_installed_material_provenance']=lambda game:('new','922241259843304590');self.assertNotEqual(first,revision(self.scene))
  self.ns['_installed_material_provenance']=lambda game:('25979580','922241259843304590');(self.root/'map.usmap').write_bytes(b'new mapping');second=revision(self.scene);self.assertNotEqual(first,second)
  new=self.root/'othergame';new.mkdir();(new/'game.utoc').write_bytes(b'container');self.ns['configured_dir']=lambda scene:str(new);self.assertNotEqual(second,revision(self.scene))
 def test_mapping_digest_reuses_exact_stat_key(self):
  path=str(self.root/'map.usmap');stat=os.stat(path);f=self.ns['_mapping_sha256'];f.cache_clear();a=f(path,stat.st_mtime_ns,stat.st_size);f(path,stat.st_mtime_ns,stat.st_size)
  self.assertEqual(f.cache_info().hits,1);self.assertEqual(a,hashlib.sha256(b'mapping').hexdigest())
 def test_geometry_stamp_changes_for_ucas_payload_only(self):
  payload=self.root/'game/game.ucas';payload.write_bytes(b'old')
  before=self.ns['_extraction_revision'](self.scene);payload.write_bytes(b'new payload')
  self.assertNotEqual(before,self.ns['_extraction_revision'](self.scene))
 def test_owned_model_cannot_borrow_default_material_from_foreign_cache(self):
  model=self.root/'RemappedPlugins/Content/Pioneer/Test/SK_Mesh.uemodel';model.write_bytes(b'fixture')
  model.with_suffix('.arc-material-slots.json').write_text(json.dumps({'materials':[{'material':'/Game/Pioneer/Test/MI_Leaf.MI_Leaf'}]}))
  self.ns['textures'].find_asset_from_object_path=lambda *args:str(self.root.parent/'foreign/MI_Leaf.json')
  with self.assertRaisesRegex(ValueError,'foreign cache'):
   self.ns['raw_material_metadata_plan'](self.scene,model_paths=[str(model)])
 def test_current_actual_french_braids_raw_metadata_is_ready(self):
  actual=ROOT/'french-fixture/RemappedPlugins/Content/Pioneer/Characters/Hairs/FrenchBraids/MI_FrenchBraidHairTie.json'
  if not actual.is_file():self.skipTest('current native FrenchBraids fixture is optional outside the actual-game probe')
  self.assertEqual(len(self.ns['_material_hierarchy_dependencies'](str(actual),'25979580','922241259843304590')),2)
 def test_usmap_only_change_requires_raw_refresh(self):
  self.assertTrue(self.current());(self.root/'map.usmap').write_bytes(b'updated usmap same game build')
  self.assertFalse(self.current());self.assertIsNotNone(self.ns['raw_material_metadata_plan'](self.scene,material_paths=[str(self.file)]))
 def test_ucas_only_change_requires_raw_refresh(self):
  (self.root/'game/game.ucas').write_bytes(b'payload changed same manifest')
  self.assertFalse(self.current());self.assertIsNotNone(self.ns['raw_material_metadata_plan'](self.scene,material_paths=[str(self.file)]))
 def test_changed_parent_content_invalidates_dependency_hash(self):
  path=self.path(self.parent);data=json.loads(path.read_text());data['Exports'][0]['Properties']['Specular']=0.23;path.write_text(json.dumps(data))
  self.assertFalse(self.current())
 def test_shared_parent_identical_rewrite_does_not_pingpong_receipts(self):
  self.assertTrue(self.current());second=self.root/'RemappedPlugins/Content/Pioneer/Test/MI_Second.json';second.write_text('{}')
  other='PioneerGame/Content/Pioneer/Test/MI_Second';self.make(other,self.parent)
  parent=self.path(self.parent);contents=parent.read_bytes();parent.write_bytes(contents)
  dependencies=self.ns['_material_hierarchy_dependencies'](str(second),'25979580','922241259843304590')
  revision=self.ns['_extraction_revision'](self.scene);self.ns['_write_material_receipt'](str(second),revision,dependencies)
  self.assertTrue(self.current());self.assertTrue(self.ns['material_hierarchy_is_current'](str(second),'25979580','922241259843304590',revision))
 def test_source_changed_during_helper_cannot_create_receipt(self):
  receipt=Path(self.ns['_material_companion'](str(self.file))+'.ready');receipt.unlink()
  plan={'files':[str(self.file)],'build':'25979580','depot':'922241259843304590','revision':self.ns['_extraction_revision'](self.scene),**self.plan_extra(self.ns['_extraction_revision'](self.scene))}
  self.ns['_extraction_revision']=lambda scene:'changed-during-helper'
  with self.assertRaisesRegex(RuntimeError,'changed during raw material metadata export'):
   self.ns['complete_raw_material_metadata'](self.scene,plan,0,'success')
  self.assertFalse(receipt.exists())
 def test_source_changed_during_receipt_commit_rejects_completion(self):
  revision=self.ns['_extraction_revision'](self.scene)
  plan={'files':[str(self.file)],'build':'25979580','depot':'922241259843304590','revision':revision,**self.plan_extra(revision)}
  calls=iter((revision,'changed-during-commit'));self.ns['_extraction_revision']=lambda scene:next(calls)
  with self.assertRaisesRegex(RuntimeError,'changed during raw material receipt commit'):
   self.ns['complete_raw_material_metadata'](self.scene,plan,0,'success')
  self.assertFalse(self.ns['material_hierarchy_is_current'](str(self.file),'25979580','922241259843304590','changed-during-commit'))
 def test_nonzero_helper_never_commits_ready_receipt(self):
  receipt=Path(self.ns['_material_companion'](str(self.file))+'.ready');receipt.unlink()
  with self.assertRaisesRegex(RuntimeError,'preparation failed'):
   self.ns['complete_raw_material_metadata'](self.scene,{'files':[str(self.file)]},1,'failed')
  self.assertFalse(receipt.exists())
 def test_dependency_signature_uses_hash_not_volatile_mtime(self):
  receipt=json.loads(Path(self.ns['_material_companion'](str(self.file))+'.ready').read_text())
  self.assertEqual(set(receipt['dependencies'][0]),{'path','sha256'})
  self.assertEqual(len(receipt['dependencies']),2)
 def test_raw_exporter_contains_no_export_session_or_texture_queue(self):
  source=(ROOT/'ArcPak/MaterialMetadataExport.cs').read_text(encoding='utf-8-sig')
  self.assertNotIn('ExportSession',source);self.assertNotIn('session.Add',source);self.assertIn('MaterialMetadataExport',source)
if __name__=='__main__':unittest.main()
