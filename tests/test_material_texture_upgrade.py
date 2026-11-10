"""Owned warm cache upgrade from valid v1 receipt, with no pixel/mesh encoder."""
import ast,functools,hashlib,importlib,json,os,subprocess,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY',Path(__file__).resolve().parents[1])).resolve()
STAGE=ROOT
PACKAGE='_warm_material_metadata_upgrade_probe'
SELECTED={'_mapping_sha256','_extraction_revision','mesh_cache_is_current','_material_companion','_raw_material_record','_material_hierarchy_dependencies','_raw_material_digest','_material_dependency_signature','_material_receipt_root','material_hierarchy_is_current','_write_material_receipt','_installed_material_provenance','raw_material_metadata_plan','complete_raw_material_metadata','ensure_cached_material_hierarchy','saved_material_package','extraction_command'}

def load_pak(source_root=None):
    source_root=Path(source_root or STAGE)
    package=types.ModuleType(PACKAGE);package.__path__=[str(source_root),str(STAGE),str(ROOT)];sys.modules[PACKAGE]=package
    rebuild=types.ModuleType(PACKAGE+'.Shared.materials.rebuild');rebuild.__path__=[];sys.modules[rebuild.__name__]=rebuild
    asset=types.ModuleType(PACKAGE+'.Shared.materials.rebuild.assets');asset.invalidate_index=lambda:None;sys.modules[asset.__name__]=asset
    path=source_root/'Shared/pak_extract.py';tree=ast.parse(path.read_text(encoding='utf-8'));nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in SELECTED]
    if {n.name for n in nodes}!=SELECTED:raise AssertionError('metadata function coverage changed')
    module=types.ModuleType(PACKAGE+'.Shared.pak_extract');module.__package__=PACKAGE+'.Shared';module.__dict__.update(os=os,re=__import__('re'),subprocess=subprocess,hashlib=hashlib,json=json,lru_cache=functools.lru_cache,_CREATE_NO_WINDOW=0,_log_tail=lambda value:value[-1000:])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[])),str(path),'exec'),module.__dict__);return module

def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,sort_keys=True),encoding='utf-8');return path

def snapshot(path):return (path.read_bytes(),path.stat().st_mtime_ns)

class WarmMetadataUpgrade(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='warm-metadata-',dir=os.environ.get('DATARAIDERS_TEST_TEMP'));self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.pak=load_pak()
        self.game=self.root/'steamapps/common/Game/PioneerGame/Content/Paks';self.game.mkdir(parents=True);(self.game/'Pioneer.utoc').write_bytes(b'fixture container index, not parsed')
        manifest=self.root/'steamapps/appmanifest_1808500.acf';manifest.write_text('"AppState" { "buildid" "12345" "InstalledDepots" { "1808501" { "manifest" "67890" } } }',encoding='utf-8')
        self.mapping=self.root/'fixture.usmap';self.mapping.write_bytes(b'fixture mapping, not parsed');self.cache=self.root/'cache';self.content=self.cache/'PioneerGame/Content';self.material=self.content/'Pioneer/Test/MI_Warm.json';write_json(self.material,{'Data':{'Parent':'M_Base'}})
        self.exe=self.root/'producer/ArcPak.Export.exe';self.exe.parent.mkdir();self.exe.write_bytes(b'not executed')
        for filename in ('ArcPak.Export.dll','CUE4Parse.Arc.dll','CUE4Parse.Conversion.Arc.dll'):(self.exe.parent/filename).write_bytes(('fixture-'+filename).encode())
        self.pak.configured_dir=lambda scene:str(self.game);self.pak.resolve_usmap=lambda scene:str(self.mapping);self.pak.cache_dir=lambda:str(self.cache);self.pak.exe_path=lambda:str(self.exe);self.pak.index_dir=lambda:str(self.cache/'index');self.pak._addon_dir=lambda:str(self.root/'addon')
        self.scene=types.SimpleNamespace(arc_import_source='PAK');self.revision=self.pak._extraction_revision(self.scene)
        self.master=self.content/'Pioneer/Test/M_Base.arc-material.json';self.sidecar=self.content/'Pioneer/Test/MI_Warm.arc-material.json'
        def raw(package,parent):
            name=package.rsplit('/',1)[-1];props={'TextureParameterValues':[{'ParameterInfo':{'Name':'CR'},'ParameterValue':{'ObjectPath':'/Game/Pioneer/Test/T_Warm.T_Warm'}}]}
            if parent:props['Parent']={'ObjectPath':parent}
            return {'Exports':[{'Type':'MaterialInstanceConstant' if parent else 'Material','Name':name,'Package':package,'Properties':props}],'DataRaidersSource':{'schema':'dataraiders.native-material-hierarchy','version':1,'package':'PioneerGame/Content/'+package.removeprefix('/Game/'),'object_path':package+'.'+name,'parent':parent,'build_id':'12345','depot_manifest':'67890'}}
        write_json(self.sidecar,raw('/Game/Pioneer/Test/MI_Warm','/Game/Pioneer/Test/M_Base.M_Base'));write_json(self.master,raw('/Game/Pioneer/Test/M_Base',''))
        self.png=self.content/'Pioneer/Test/T_Warm.png';self.png.write_bytes(b'existing cached PNG bytes; no decoder invoked')
        self.mesh=self.content/'Pioneer/Test/SK_Cached.uemodel';self.mesh.write_bytes(b'cached mesh revision fixture; no mesh parser invoked');write_json(Path(str(self.mesh)+'.arc-extract.json'),{'revision':self.revision})
        dependencies=self.pak._material_hierarchy_dependencies(str(self.material),'12345','67890')
        self.ready=Path(str(self.sidecar)+'.ready');write_json(self.ready,{'schema':'dataraiders.material-hierarchy-ready','version':1,'root':self.pak._material_receipt_root(str(self.material)),'revision':self.revision,'dependencies':self.pak._material_dependency_signature(dependencies)})
        self.calls=[]
    def fake_helper(self,command,**options):
        self.calls.append(command);self.assertEqual(command[1],'material-metadata');self.assertIn('--material-file',command);self.assertNotIn('extract',command);self.assertNotIn('mesh-metadata',command)
        metadata=importlib.import_module(PACKAGE+'.Shared.material_texture_metadata');producer=metadata.producer_signature(str(self.exe));mapping=hashlib.sha256(self.mapping.read_bytes()).hexdigest()
        fact={'Type':'Texture2D','Name':'T_Warm','Package':'/Game/Pioneer/Test/T_Warm','Properties':{'SRGB':True},'Mips':[{'SizeX':1,'SizeY':1,'SizeZ':1}],'DataRaidersSource':{'projection':'native-texture-sidecar-v1','schema_version':1,'build':'12345','depot_manifest':'67890','mapping_sha256':mapping,'producer_assemblies':producer}}
        texture=self.content/'Pioneer/Test/T_Warm.arc-texture.json';write_json(texture,fact)
        closure={'schema':'dataraiders.native-material-texture-closure','version':1,'recipe':'inherited-native-texture-metadata-v1','material_package':'/Game/Pioneer/Test/MI_Warm','material_object_path':'/Game/Pioneer/Test/MI_Warm.MI_Warm','build_id':'12345','depot_manifest':'67890','mapping_sha256':mapping,'texture_projection':'native-texture-sidecar-v1','texture_schema_version':1,'producer_assemblies':producer,'dependencies':[{'package':fact['Package'],'relative_path':texture.relative_to(self.cache).as_posix(),'sha256':hashlib.sha256(texture.read_bytes()).hexdigest()}]}
        write_json(Path(str(self.sidecar)+'.textures.json'),closure)
        return subprocess.CompletedProcess(command,0,'compact metadata only; no mesh/image encoding','')
    def upgrade(self):
        with patch.object(self.pak.subprocess,'run',side_effect=self.fake_helper):self.pak.ensure_cached_material_hierarchy(self.scene,material_paths=[str(self.material)])
    def test_valid_v1_receipt_and_existing_png_auto_upgrade_once_then_warm_reuse(self):
        png_before=snapshot(self.png);mesh_before=snapshot(self.mesh);self.assertTrue(self.pak.mesh_cache_is_current(str(self.mesh),self.scene));self.assertFalse(self.pak.material_hierarchy_is_current(str(self.material),'12345','67890',self.revision))
        self.upgrade();self.assertEqual(len(self.calls),1);receipt=json.loads(self.ready.read_text(encoding='utf-8'));self.assertEqual(receipt['version'],2);self.assertIn('metadata_revision',receipt);self.assertIn('producer_assemblies',receipt)
        self.assertTrue(self.pak.material_hierarchy_is_current(str(self.material),'12345','67890',self.revision));self.upgrade();self.assertEqual(len(self.calls),1)
        self.assertEqual(snapshot(self.png),png_before);self.assertEqual(snapshot(self.mesh),mesh_before);self.assertEqual(self.pak._extraction_revision(self.scene),self.revision);self.assertTrue(self.pak.mesh_cache_is_current(str(self.mesh),self.scene))
    def test_missing_compact_fact_auto_refreshes_without_png_or_mesh_work(self):
        self.upgrade();before=snapshot(self.png);(self.content/'Pioneer/Test/T_Warm.arc-texture.json').unlink();self.upgrade();self.assertEqual(len(self.calls),2);self.assertEqual(snapshot(self.png),before);self.assertTrue(self.pak.mesh_cache_is_current(str(self.mesh),self.scene))
    def test_producer_change_only_invalidates_metadata_receipt_not_mesh_revision(self):
        self.upgrade();before=snapshot(self.mesh);(self.exe.parent/'ArcPak.Export.dll').write_bytes(b'new fixture metadata producer revision');self.assertEqual(self.pak._extraction_revision(self.scene),self.revision);self.upgrade();self.assertEqual(len(self.calls),2);self.assertEqual(snapshot(self.mesh),before)
    def test_legacy_remapped_mesh_cache_reextracts_once(self):
        canonical=self.cache/'PioneerGame/Content/Pioneer/Test/SK_Root.uemodel';legacy=self.cache/'RemappedPlugins/Content/Pioneer/Test/SK_Root.uemodel'
        for model in (canonical,legacy):write_json(Path(str(model)+'.arc-extract.json'),{'revision':self.revision})
        self.assertTrue(self.pak.mesh_cache_is_current(str(canonical),self.scene));self.assertFalse(self.pak.mesh_cache_is_current(str(legacy),self.scene))
        self.assertTrue(self.pak.mesh_cache_is_current(str(legacy),self.scene,allow_legacy_root=True))
    def test_foreign_and_fmodel_material_paths_never_invoke_primary_helper(self):
        self.scene.arc_import_source='FMODEL'
        with patch.object(self.pak.subprocess,'run',side_effect=AssertionError('foreign helper invocation')):self.pak.ensure_cached_material_hierarchy(self.scene,material_paths=[str(self.material)])
        self.scene.arc_import_source='PAK';foreign=self.root/'foreign/PioneerGame/Content/Pioneer/Test/MI_Warm.json';write_json(foreign,{})
        with patch.object(self.pak.subprocess,'run',side_effect=AssertionError('foreign helper invocation')):self.pak.ensure_cached_material_hierarchy(self.scene,material_paths=[str(foreign)])
    def test_failed_helper_or_incomplete_closure_never_commits_ready_v2(self):
        plan=self.pak.raw_material_metadata_plan(self.scene,material_paths=[str(self.material)])
        with self.assertRaises(RuntimeError):self.pak.complete_raw_material_metadata(self.scene,plan,1,'failed')
        with self.assertRaises(RuntimeError):self.pak.complete_raw_material_metadata(self.scene,plan,0,'[12:00:00 ERR] ParserException: malformed native data')
        with self.assertRaises(RuntimeError):self.pak.complete_raw_material_metadata(self.scene,plan,0,'missing closure')
        self.assertEqual(json.loads(self.ready.read_text(encoding='utf-8'))['version'],1)
    def test_unsafe_manifest_dependency_cannot_escape_owned_cache(self):
        self.upgrade();path=Path(str(self.sidecar)+'.textures.json');closure=json.loads(path.read_text(encoding='utf-8'));closure['dependencies'][0]['relative_path']='../outside.arc-texture.json';write_json(path,closure)
        with self.assertRaises(ValueError):self.pak.material_hierarchy_is_current(str(self.material),'12345','67890',self.revision)
    def test_global_mesh_revision_function_matches_original_ast(self):
        original=ast.parse((Path(__file__).parent/'fixtures/native_mesh_revision_v1.py.txt').read_text(encoding='utf-8'));candidate=ast.parse((ROOT/'Shared/pak_extract.py').read_text(encoding='utf-8'))
        def revision(tree):return ast.dump(next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='_extraction_revision'))
        self.assertEqual(revision(original),revision(candidate))

if __name__=='__main__':unittest.main()
