import importlib,json,os,sys,tempfile,types,unittest,hashlib
from pathlib import Path
from unittest.mock import patch
ROOT=Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY',str(Path(__file__).resolve().parents[2])));STAGE=ROOT;PKG='_current_compat_test'
for name,paths in ((PKG,[ROOT]),(PKG+'.material_runtime',[STAGE/'material_runtime',ROOT/'material_runtime']),(PKG+'.surface_rebuild',[STAGE/'surface_rebuild',ROOT/'surface_rebuild']),(PKG+'.materials',[STAGE/'materials',ROOT/'materials'])):
 m=types.ModuleType(name);m.__path__=[str(p) for p in paths];sys.modules[name]=m
catalog=importlib.import_module(PKG+'.material_runtime.catalog');data=importlib.import_module(PKG+'.material_runtime.native_data');assets=importlib.import_module(PKG+'.surface_rebuild.assets');records=importlib.import_module(PKG+'.surface_rebuild.records');bridge=importlib.import_module(PKG+'.surface_rebuild.native_catalog_bridge');presets=importlib.import_module(PKG+'.materials.preset_defaults')
class CompatTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=os.environ.get('DATARAIDERS_TEST_TEMP'));self.root=Path(self.temp.name);self.flat=self.root/'flat';self.flat.mkdir();self.old=data.ACTIVATION_PATH;self.master='/Game/Pioneer/MaterialLibrary/M_Presets/M_TrimMap_01';native=self.root/'native/PioneerGame/Content';(native/'Pioneer/MaterialLibrary/M_Presets').mkdir(parents=True);(native/'Test').mkdir()
  (native/'Pioneer/MaterialLibrary/M_Presets/M_TrimMap_01.json').write_text(json.dumps({'Type':'Material','Name':'M_TrimMap_01','Package':self.master,'Properties':{'scalars':{'Default':3,'Zero':0},'switches':{'Off':False},'textures':{'CR':'/Game/Test/T'},'BlendMode':0}}),encoding='utf8');(native/'Test/T.json').write_text(json.dumps({'Type':'Texture2D','Name':'T','Package':'/Game/Test/T','Properties':{'SRGB':False}}),encoding='utf8')
  obj=catalog.generate_catalog(self.root/'native',self.root/'bank.json',[self.master],build='fixture',mapping='fixture');payload=catalog.serialize(obj.payload);payload['statistics']['normalization_error_count']=0;catalog.NativeCatalog(payload).write(self.root/'bank.json');self.manifest=self.root/'active.json';self.manifest.write_text(json.dumps({'schema':bridge.LIMITED_SCHEMA,'reviewed_source_complete':True,'catalog':'bank.json','sha256':hashlib.sha256((self.root/'bank.json').read_bytes()).hexdigest(),'build':'fixture','mapping':'fixture','expected_material_records':1,'expected_texture_metadata':1,'expected_render_limitations':0,'expected_binding_sources':0,'reviewed_limitations':[],'reviewed_binding_sources':[]}),encoding='utf8');data.ACTIVATION_PATH=self.manifest
  self.source=self.flat/'MI_Flat.json';self.source.write_text(json.dumps({'Parent':self.master,'Textures':{'CR':'/Game/Test/T'},'Parameters':{'Scalars':{'Zero':0},'Switches':{'Off':False}}}),encoding='utf8');(self.flat/'T.png').write_bytes(b'fixture image bytes, never decoded')
 def tearDown(self):data.ACTIVATION_PATH=self.old;data._CACHE.clear();self.temp.cleanup()
 def index(self):
  index=assets.AssetIndex(self.flat);index.native_catalog_activation=self.manifest;return index
 def test_flat_fmodel_remains_textured_without_false_native_exact(self):
  index=self.index();record=records.MaterialLibrary(index).load('MI_Flat');self.assertEqual(record['scalars']['Default'],3);self.assertEqual(record['scalars']['Zero'],0);self.assertIs(record['switches']['Off'],False);self.assertEqual(record['native_source_mode'],'fmodel-compatibility');self.assertEqual(index.texture('/Game/Test/T'),self.flat/'T.png');self.assertIsNone(bridge.begin(index,record));self.assertTrue(any('uncertain' in d for d in record['native_source_diagnostics']))
 def test_flat_source_and_png_edits_change_compatibility_cache_identity(self):
  index=self.index();library=records.MaterialLibrary(index);record=library.load('MI_Flat');before=data.compatibility_signature(record,index);(self.flat/'T.png').write_bytes(b'changed');after=data.compatibility_signature(library.load('MI_Flat'),index);self.assertNotEqual(before,after);obj=json.loads(self.source.read_text(encoding='utf8'));obj['Parameters']['Scalars']['Zero']=2;self.source.write_text(json.dumps(obj),encoding='utf8');self.assertEqual(library.load('MI_Flat')['scalars']['Zero'],2)
 def test_game_engine_same_basename_never_cross_borrows(self):
  root=self.root/'mixed';game=root/'PioneerGame/Content/Test';engine=root/'Engine/Content/Test';game.mkdir(parents=True);engine.mkdir(parents=True);(game/'T.png').write_bytes(b'game');(engine/'T.png').write_bytes(b'engine');(engine/'Only.png').write_bytes(b'engine-only');index=assets.AssetIndex(root);self.assertEqual(index.texture('/Game/Test/T'),game/'T.png');self.assertIsNone(index.texture('/Game/Test/Only'))
  with self.assertRaises(assets.ResolutionError):index.resolve('T','.png')
 def test_old_material_texture_and_preset_banks_are_never_read(self):
  original=Path.read_bytes
  def forbid(path,*a,**k):
   if path.name in ('materials_142.json.gz','character_materials_142.json.gz','parameter_scopes_142.json.gz','bundled_textures_142.json','preset_defaults.json'):raise AssertionError('Retired data opened: '+str(path))
   return original(path,*a,**k)
  with patch.object(Path,'read_bytes',forbid):
   self.assertTrue(records.metadata()['materials']);self.assertEqual(presets.get_preset('M_TrimMap_01')['scalars']['Zero'],0);self.assertIsNone(self.index().texture('/Game/Test/TA_Missing',0))
 def test_presets_are_current_exact_namespace_scoped_and_source_tracked(self):
  preset=presets.get_preset('M_TrimMap_01');self.assertEqual(preset['scalars']['Default'],3);self.assertEqual(preset['source_provenance']['build'],'fixture');self.assertEqual(preset['source_provenance']['bank_sha256'],hashlib.sha256((self.root/'bank.json').read_bytes()).hexdigest());self.assertFalse(presets.has_preset('/Engine/Test/M_TrimMap_01'))
 def test_active_unsupported_volume_refuses_but_record_parsing_survives(self):
  (self.flat/'V.arc-texture.json').write_text(json.dumps({'Type':'VolumeTexture','Name':'V','Package':'/Game/Test/V','Properties':{}}),encoding='utf8');(self.flat/'V.png').write_bytes(b'not-a-2d-volume');index=self.index()
  with self.assertRaisesRegex(assets.ResolutionError,'Unsupported active'):index.texture('/Game/Test/V')
 def test_inactive_ambiguous_images_do_not_break_metadata_loading(self):
  (self.flat/'other').mkdir();(self.flat/'other/T.png').write_bytes(b'different');index=self.index();record=records.MaterialLibrary(index).load('MI_Flat');self.assertEqual(record['scalars']['Default'],3);signature=data.compatibility_signature(record,index);self.assertFalse(signature['native_exact']);self.assertTrue(any('unresolved' in row for row in signature['observations']))
 def test_flat_game_export_with_engine_subtree_preserves_selected_png_only(self):
  engine=self.flat/'Engine/Content/Test';engine.mkdir(parents=True);(engine/'Only.png').write_bytes(b'engine');index=self.index();record=records.MaterialLibrary(index).load('MI_Flat');self.assertEqual(record['scalars']['Default'],3);self.assertEqual(index.texture('/Game/Test/T'),self.flat/'T.png');self.assertIsNone(index.texture('/Game/Test/Only'))
 def test_guard_known_limitation_fails_before_primary_mutation(self):
  from unittest.mock import Mock
  fake={'limits':{self.master.casefold():['reviewed ambiguity']},'provenance':{'build':'fixture'}}
  with patch.object(data,'reviewed_source',return_value=fake):
   with self.assertRaises(bridge.NativeRenderLimitationError):data.guard_known_source(self.source,{'parent':self.master})
   self.assertEqual(data.guard_known_source(None,{'parent':'/Game/Other/M'}),{'build':'fixture'})
 def test_bank_source_change_invalidates_presets(self):
  first=presets.get_preset('M_TrimMap_01')['source_provenance']['bank_sha256']
  payload=json.loads((self.root/'bank.json').read_text(encoding='utf8'));payload['fixture_revision']=2;(self.root/'bank.json').write_text(json.dumps(payload),encoding='utf8');active=json.loads(self.manifest.read_text(encoding='utf8'));active['sha256']=hashlib.sha256((self.root/'bank.json').read_bytes()).hexdigest();self.manifest.write_text(json.dumps(active),encoding='utf8')
  self.assertNotEqual(presets.get_preset('M_TrimMap_01')['source_provenance']['bank_sha256'],first)
 def test_warm_verified_bank_does_not_read_or_hash_either_file(self):
  first=data.reviewed_source()
  with patch.object(data,'digest',side_effect=AssertionError('warm bank rehashed')),patch.object(Path,'read_text',side_effect=AssertionError('warm bank reread')),patch.object(data.NativeCatalog,'read',side_effect=AssertionError('warm catalog reparsed')):
   self.assertIs(data.reviewed_source(),first)
 def test_unavailable_file_change_identity_disables_reuse(self):
  data.reviewed_source()
  with patch.object(data,'_file_identity',return_value=None),patch.object(data,'digest',wraps=data.digest) as hashed:
   first=data.reviewed_source();second=data.reviewed_source()
   self.assertIsNot(first,second);self.assertEqual(hashed.call_count,4);self.assertEqual(len(data._CACHE),0)
 def test_source_mutation_during_validation_is_not_published(self):
  original=data.NativeCatalog.read
  def mutate(path):
   result=original(path);self.manifest.write_text(self.manifest.read_text(encoding='utf8')+' ',encoding='utf8');return result
  with patch.object(data.NativeCatalog,'read',side_effect=mutate):
   with self.assertRaisesRegex(ValueError,'changed during validation'):data.reviewed_source()
  self.assertEqual(len(data._CACHE),0)
 def test_manifest_change_during_warm_bank_check_cannot_return_old_snapshot(self):
  data.reviewed_source();original=data._file_identity
  def mutate(path):
   result=original(path)
   if path==self.root/'bank.json':self.manifest.write_text(self.manifest.read_text(encoding='utf8')+' ',encoding='utf8')
   return result
  with patch.object(data,'_file_identity',side_effect=mutate):
   with self.assertRaisesRegex(ValueError,'changed during validation'):data.reviewed_source()
  self.assertEqual(len(data._CACHE),0)
 def test_warm_bank_does_not_skip_catalog_schema_validation_after_rehash(self):
  data.reviewed_source();bank=self.root/'bank.json';payload=json.loads(bank.read_text(encoding='utf8'));payload['format']='unsupported';bank.write_text(json.dumps(payload),encoding='utf8')
  active=json.loads(self.manifest.read_text(encoding='utf8'));active['sha256']=hashlib.sha256(bank.read_bytes()).hexdigest();self.manifest.write_text(json.dumps(active),encoding='utf8')
  with self.assertRaises(ValueError):data.reviewed_source()
 def test_same_size_bank_edit_with_restored_mtime_is_rejected(self):
  data.reviewed_source();bank=self.root/'bank.json';before=bank.stat();content=bank.read_bytes();changed=content.replace(b'fixture',b'changed')
  self.assertNotEqual(changed,content);self.assertEqual(len(changed),len(content));bank.write_bytes(changed);os.utime(bank,ns=(before.st_atime_ns,before.st_mtime_ns))
  with self.assertRaisesRegex(ValueError,'SHA mismatch'):data.reviewed_source()
 def test_same_size_manifest_edit_with_restored_mtime_revalidates_context(self):
  data.reviewed_source();before=self.manifest.stat();content=self.manifest.read_text(encoding='utf8');changed=content.replace('fixture','changed')
  self.assertEqual(len(changed),len(content));self.manifest.write_text(changed,encoding='utf8');os.utime(self.manifest,ns=(before.st_atime_ns,before.st_mtime_ns))
  with self.assertRaisesRegex(ValueError,'context mismatch'):data.reviewed_source()
 def test_atomic_bank_replacement_with_matching_size_and_mtime_is_rejected(self):
  data.reviewed_source();bank=self.root/'bank.json';before=bank.stat();replacement=self.root/'replacement.json'
  replacement.write_bytes(bank.read_bytes().replace(b'fixture',b'changed'));os.utime(replacement,ns=(before.st_atime_ns,before.st_mtime_ns));os.replace(replacement,bank)
  with self.assertRaisesRegex(ValueError,'SHA mismatch'):data.reviewed_source()
 def test_warm_bank_still_rejects_schema_build_mapping_and_coverage_changes(self):
  original=json.loads(self.manifest.read_text(encoding='utf8'))
  for field,value in (('schema','unreviewed'),('build','other'),('mapping','other'),('expected_material_records',2),('expected_texture_metadata',2)):
   with self.subTest(field=field):
    self.manifest.write_text(json.dumps(original),encoding='utf8');data.reviewed_source()
    self.manifest.write_text(json.dumps({**original,field:value}),encoding='utf8')
    with self.assertRaises(ValueError):data.reviewed_source()
 def test_deleted_bank_and_manifest_cannot_return_a_warm_source(self):
  data.reviewed_source();bank=self.root/'bank.json';content=bank.read_bytes();bank.unlink()
  with self.assertRaises(OSError):data.reviewed_source()
  bank.write_bytes(content);data.reviewed_source();self.manifest.unlink();self.assertIsNone(data.reviewed_source())
 def test_verified_bank_cache_is_bounded(self):
  for revision in range(4):
   active=json.loads(self.manifest.read_text(encoding='utf8'));active['fixture_revision']=revision;self.manifest.write_text(json.dumps(active),encoding='utf8');data.reviewed_source()
   self.assertEqual(len(data._CACHE),1)
 def test_declared_array_all_actual_slices_invalidate_signature(self):
  (self.flat/'TA_Test.arc-texture.json').write_text(json.dumps({'Type':'Texture2DArray','Name':'TA_Test','Package':'/Game/Test/TA_Test','ArrayLayerCount':3,'Mips':[{'SizeZ':3}],'Properties':{}}),encoding='utf8')
  for i in range(3):(self.flat/f'TA_Test_LAYER{i}.png').write_bytes(bytes([i]))
  index=self.index();record={'textures':{'Array':'/Game/Test/TA_Test'}};first=data.compatibility_signature(record,index);self.assertEqual(len(first['observations']),3);(self.flat/'TA_Test_LAYER2.png').write_bytes(b'changed layer2');self.assertNotEqual(data.compatibility_signature(record,index),first)
 def test_flat_skin_anchor_uses_current_native_parent(self):
  library=records.MaterialLibrary(self.index());self.assertEqual(library.skin_anchor('MI_Flat'),'')
if __name__=='__main__':unittest.main()
