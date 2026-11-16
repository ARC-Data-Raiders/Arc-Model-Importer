"""Warm outfit queues reuse a current cached extraction without launching ArcPak."""
import ast,functools,hashlib,json,os,subprocess,sys,tempfile,types,unittest
from pathlib import Path
ROOT=Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY',Path(__file__).resolve().parents[1])).resolve()
PACKAGE='_pak_extract_reuse_probe'
SELECTED={'_mapping_sha256','_extraction_revision','_installed_material_provenance','game_path','_normalize_asset_path','_package_aliases','_expected_uemodel','_collect_uemodels','complete_extraction','_extraction_producer','current_extracted_meshes','ensure_rels','_finish_extraction','register_defaults','unregister_defaults','_initialize_pak_defaults','_load_pak_defaults','configured_dir','resolve_pak_folder','_resolve_pak_folder','pak_dir_has_game_files','_prefs_pak_dir','_report_extract_failure'}

def load_pak():
    package=types.ModuleType(PACKAGE);package.__path__=[str(ROOT)];sys.modules[PACKAGE]=package
    path=ROOT/'Shared/pak_extract.py';tree=ast.parse(path.read_text(encoding='utf-8'));nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in SELECTED]
    if {n.name for n in nodes}!=SELECTED:raise AssertionError('reuse function coverage changed')
    module=types.ModuleType(PACKAGE+'.Shared.pak_extract');module.__package__=PACKAGE+'.Shared';module.__dict__.update(os=os,re=__import__('re'),json=json,hashlib=hashlib,subprocess=subprocess,lru_cache=functools.lru_cache,_log_tail=lambda value,limit=6:value[-1000:],threading=__import__('threading'),stop_serve=lambda:None)
    # Shared/prefs.py stub: no add-on preferences in the probe, so get() returns the default.
    module.prefs=types.SimpleNamespace(get=lambda key,default=None:default,remember=lambda key,value:None,set_value=lambda key,value:None,register=lambda:None,unregister=lambda:None)
    module.bpy=types.SimpleNamespace(app=types.SimpleNamespace(handlers=types.SimpleNamespace(persistent=lambda function:function)))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[])),str(path),'exec'),module.__dict__);return module

class ReuseCurrentExtraction(unittest.TestCase):
    PACKAGE='PioneerGame/Content/Characters/Test/SK_Part'
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='pak-reuse-',dir=os.environ.get('DATARAIDERS_TEST_TEMP'));self.addCleanup(self.temp.cleanup);root=Path(self.temp.name);self.pak=pak=load_pak()
        self.game=root/'steamapps/common/Game/PioneerGame/Content/Paks';self.game.mkdir(parents=True);self.container=self.game/'Pioneer.utoc';self.container.write_bytes(b'fixture container index, not parsed')
        (root/'steamapps/appmanifest_1808500.acf').write_text('"AppState" { "buildid" "12345" "InstalledDepots" { "1808501" { "manifest" "67890" } } }',encoding='utf-8')
        mapping=root/'fixture.usmap';mapping.write_bytes(b'fixture mapping, not parsed');self.cache=root/'cache'
        self.exe=root/'producer/ArcPak.Export.exe';self.exe.parent.mkdir();self.exe.write_bytes(b'not executed')
        for filename in ('ArcPak.Export.dll','CUE4Parse.Arc.dll','CUE4Parse.Conversion.Arc.dll'):(self.exe.parent/filename).write_bytes(('fixture-'+filename).encode())
        pak.configured_dir=lambda scene:str(self.game);pak.resolve_usmap=lambda scene:str(mapping);pak.cache_dir=lambda:str(self.cache);pak.exe_path=lambda:str(self.exe)
        self.finished=[];self.published=[];pak.utils=types.SimpleNamespace(set_pak_cache_root=self.published.append)
        pak.textures=types.SimpleNamespace(clear_clothing_path_caches=lambda:self.finished.append('textures'))
        # _finish_extraction does `from . import textures`; register the stub so the real module never loads.
        textures=types.ModuleType(PACKAGE+'.Shared.textures');textures.clear_clothing_path_caches=pak.textures.clear_clothing_path_caches;sys.modules[textures.__name__]=textures
        if PACKAGE+'.Shared' in sys.modules:sys.modules[PACKAGE+'.Shared'].textures=textures
        common=types.ModuleType(PACKAGE+'.Shared.materials.common');common.clear_material_session_caches=lambda:self.finished.append('materials');sys.modules[common.__name__]=common
        assets=types.ModuleType(PACKAGE+'.Shared.materials.rebuild.assets');assets.invalidate_index=lambda root:self.finished.append(('index',root));sys.modules[assets.__name__]=assets
        pak.appearance=types.SimpleNamespace(_ICON_SCAN_CACHE={'warm':'retained'});self.extracted=[];pak.extract_paths=self.fake_extract
        self.scene=types.SimpleNamespace();self.model=self.cache/(self.PACKAGE+'.uemodel');self.model.parent.mkdir(parents=True);self.model.write_bytes(b'cached model fixture; not parsed')
        self.receipt=Path(str(self.model)+'.arc-extract.json')
    def fake_extract(self,game,usmap,paths,code=0):
        self.extracted.append(list(paths));return code,'fixture exporter log',self.pak.complete_extraction(str(self.cache),paths,code,'fixture exporter log',self.pak._extraction_revision(self.scene))
    def queue(self,paths=None,reuse=True):return self.pak.ensure_rels(self.scene,paths or [self.PACKAGE],reuse_current=reuse)
    def same(self,meshes):self.assertEqual([os.path.normcase(m) for m in meshes],[os.path.normcase(str(self.model))])
    def test_cold_cache_extracts_and_stamps_revision_and_producer(self):
        self.same(self.queue());self.assertEqual(self.extracted,[[self.PACKAGE]])
        producer=importlib_metadata().producer_signature(str(self.exe));receipt=json.loads(self.receipt.read_text(encoding='utf-8'))
        self.assertEqual(receipt,{'revision':self.pak._extraction_revision(self.scene),'producer':producer})
    def test_current_receipt_reuses_cache_without_exporter(self):
        self.queue();self.finished.clear();before=(self.model.read_bytes(),self.model.stat().st_mtime_ns,self.receipt.read_bytes())
        self.pak.appearance._ICON_SCAN_CACHE['warm']='retained';self.published.clear()
        self.same(self.queue());self.assertEqual(len(self.extracted),1);self.assertEqual(self.finished,[])
        self.assertEqual(self.published,[str(self.cache)]);self.assertEqual(self.pak.appearance._ICON_SCAN_CACHE,{'warm':'retained'})
        self.assertEqual((self.model.read_bytes(),self.model.stat().st_mtime_ns,self.receipt.read_bytes()),before)
    def test_receipt_without_producer_reextracts_once(self):
        self.receipt.write_text(json.dumps({'revision':self.pak._extraction_revision(self.scene)}),encoding='utf-8')
        self.queue();self.queue();self.assertEqual(len(self.extracted),1)
    def test_exporter_or_game_change_reextracts(self):
        self.queue();(self.exe.parent/'ArcPak.Export.dll').write_bytes(b'new fixture exporter build');self.queue();self.assertEqual(len(self.extracted),2)
        self.container.write_bytes(b'updated fixture container index');self.queue();self.assertEqual(len(self.extracted),3);self.queue();self.assertEqual(len(self.extracted),3)
    def test_mapping_change_reextracts_and_invalidates(self):
        self.queue();self.finished.clear();Path(self.pak.resolve_usmap(self.scene)).write_bytes(b'updated fixture mapping')
        self.queue();self.assertEqual(len(self.extracted),2);self.assertTrue(self.finished)
    def test_default_folder_and_legacy_root_paths_always_extract(self):
        self.queue();self.queue(reuse=False);self.assertEqual(len(self.extracted),2)
        self.queue(['PioneerGame/Content/Characters/Test']);self.assertEqual(len(self.extracted),3)
        legacy=self.cache/'RemappedPlugins/Content/Characters/Test/SK_Legacy.uemodel';legacy.parent.mkdir(parents=True);legacy.write_bytes(b'legacy root model')
        Path(str(legacy)+'.arc-extract.json').write_text(self.receipt.read_text(encoding='utf-8'),encoding='utf-8')
        self.queue(['PioneerGame/Content/Characters/Test/SK_Legacy']);self.assertEqual(len(self.extracted),4)
    def test_failed_extraction_drops_receipt_and_retries(self):
        self.queue();self.fake_extract(str(self.game),'',[self.PACKAGE],code=1);self.assertFalse(self.receipt.exists())
        self.queue();self.assertEqual(len(self.extracted),3);self.assertTrue(self.receipt.exists())
    def test_missing_cached_model_extracts(self):
        self.queue();self.model.unlink();self.queue();self.assertEqual(len(self.extracted),2)
    def test_changed_and_failed_extractions_still_invalidate_all_consumers(self):
        for code in (0,1):
            self.finished.clear();self.published.clear();self.pak.appearance._ICON_SCAN_CACHE['warm']='retained'
            self.fake_extract(str(self.game),'',[self.PACKAGE],code=code)
            self.assertEqual(self.published,[str(self.cache)])
            self.assertEqual(self.finished,['textures','materials',('index',str(self.cache))])
            self.assertEqual(self.pak.appearance._ICON_SCAN_CACHE,{})
    def test_partial_receipt_hit_does_not_preserve_caches(self):
        self.queue();self.finished.clear()
        self.queue([self.PACKAGE,'PioneerGame/Content/Characters/Test/SK_Missing'])
        self.assertEqual(len(self.extracted),2);self.assertTrue(self.finished)
    def test_registration_and_old_load_callback_do_not_discover_paths(self):
        from unittest.mock import Mock
        pak=self.pak;pak.reconcile_pak_dir=Mock(side_effect=AssertionError('pre-action PAK discovery'))
        pak.bpy.data=types.SimpleNamespace(scenes=[self.scene])
        pak.bpy.app.handlers.load_post=[pak._load_pak_defaults]
        pak.bpy.app.timers=types.SimpleNamespace(is_registered=lambda callback:True,unregister=Mock())
        pak.register_defaults()
        self.assertEqual(pak.bpy.app.handlers.load_post,[])
        pak._load_pak_defaults();pak.reconcile_pak_dir.assert_not_called()
    def test_explicit_action_resolves_and_validates_configured_path(self):
        pak=load_pak();pak.bpy.path=types.SimpleNamespace(abspath=lambda value:value)
        class Scene(dict):
            def __init__(self,folder):super().__init__();self.arc_pak_dir=folder
        self.assertEqual(pak.configured_dir(Scene(str(self.game))),str(self.game))
        self.assertEqual(pak.configured_dir(Scene(str(self.game/'missing'))),'')

def importlib_metadata():
    import importlib;return importlib.import_module(PACKAGE+'.Shared.material_texture_metadata')

if __name__=='__main__':unittest.main()
