import hashlib,json,pathlib,sys,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from material_runtime import AssetRepository
from material_runtime.repository import invalidate_digest_cache
from material_runtime.catalog import NativeCatalog,generate_catalog,material_build_signature as _material_build_signature,iter_native_material_references,NativeMetadataError,StaleCatalogError

def material_build_signature(*args,**kwargs):
    kwargs.setdefault('recipe','test-family-v1'); kwargs.setdefault('implementation_revision','test-implementation-v1')
    return _material_build_signature(*args,**kwargs)

from material_runtime.assets import NativeAssetRepository,AmbiguousAssetError

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests'); self.root=pathlib.Path(self.tmp.name)
        self.native=self.root/'native'; self.game=self.native/'PioneerGame/Content'; self.game.mkdir(parents=True)
        self.local=self.root/'local'; self.local.mkdir(); self.images=self.root/'images'; self.images.mkdir(); self.output=self.root/'catalog.json'
        self.repo=AssetRepository({'/Game':self.local},image_mounts={'/Game':self.images},build='2.2.0',mapping='native-test')
    def tearDown(self): self.tmp.cleanup()
    def native_material(self,name,properties=None,typ='Material',extra=None):
        entry={'Type':typ,'Name':name.rsplit('/',1)[-1],'Package':'/Game/'+name,'Properties':properties or {}}
        entry.update(extra or {}); path=self.game/(name+'.json'); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps({'Exports':[entry]})); return path
    def local_material(self,name,properties=None,package=None):
        entry={'Type':'MaterialInstanceConstant','Name':name.rsplit('/',1)[-1],'Package':package or '/Game/'+name,'Properties':properties or {}}
        path=self.local/(name+'.arc-material.json'); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps({'Exports':[entry]})); return path
    def texture(self,name='T',count=None,sources=None):
        entry={'Type':'Texture2DArray' if count is not None else 'Texture2D','Name':name.rsplit('/',1)[-1],'Package':'/Game/'+name,'Properties':{'SRGB':False}}
        if count is not None: entry['Mips']=[{'SizeX':8,'SizeY':8,'SizeZ':count},{'SizeX':4,'SizeY':4,'SizeZ':count}]
        if sources is not None: entry['Properties']['SourceTextures']=sources
        path=self.game/(name+'.json'); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps({'Exports':[entry]})); return path
    def image(self,name='T',value=b'pixels'):
        path=self.images/(name+'.png'); path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(value); return path
    def catalog(self,refs=('/Game/C',),current=False):
        generate_catalog(self.native,self.output,refs,build='2.2.0',mapping='native-test')
        return NativeCatalog.read(self.output,native_root=self.native if current else None)
    def test_portable_local_facts_preserve_zero_false_null_and_scopes(self):
        self.native_material('P',{'scalars':{'Inherited':2,'Zero':1},'switches':{'Off':True},'textures':{'Disabled':'/Game/T'}})
        self.texture()
        self.native_material('C',{'Parent':'/Game/P','scalars':{'Zero':0},'switches':{'Off':False},'textures':{'Disabled':None},'VectorParameterValues':[{'ParameterInfo':{'Name':'Tint','Association':'LayerParameter','Index':2},'ParameterValue':{'R':0,'G':1,'B':0,'A':0}}]},'MaterialInstanceConstant')
        catalog=self.catalog(); facts=catalog.payload['records']['/Game/C']['facts']
        self.assertNotIn('Inherited',facts['scalars']); self.assertEqual(facts['parent'],'/Game/P')
        record=catalog.load_material('/Game/C',self.repo)
        self.assertEqual(record.scalars['Inherited'],2); self.assertEqual(record.scalars['Zero'],0); self.assertFalse(record.switches['Off']); self.assertIsNone(record.textures['Disabled'])
        self.assertEqual(record.scopes[('LayerParameter',2)]['colors']['Tint'],(0,1,0,0)); self.assertEqual(sum(source.get('kind')=='catalog-native' for source in record.provenance),2)
        signature=material_build_signature('/Game/C',self.repo,catalog=catalog)
        self.assertTrue(signature.complete); self.assertFalse(any(dep.get('kind')=='image' for dep in signature.dependencies))
        with self.assertRaises(TypeError): catalog.payload['records']['/Game/C']['facts']['scalars']['Zero']=1
    def test_changed_local_parent_and_reparent_never_reuse_flattened_defaults(self):
        self.native_material('P',{'scalars':{'Inherited':1,'OnlyP':7}}); self.native_material('C',{'Parent':'/Game/P','scalars':{'Zero':0}},'MaterialInstanceConstant')
        catalog=self.catalog(); before=material_build_signature('/Game/C',self.repo,catalog=catalog)
        self.local_material('P',{'scalars':{'Inherited':9}})
        after=material_build_signature('/Game/C',self.repo,catalog=catalog)
        self.assertEqual(after.record.scalars['Inherited'],9); self.assertNotIn('OnlyP',after.record.scalars); self.assertFalse(after.can_reuse(before.key))
        self.local_material('Q',{'scalars':{'Other':3}}); self.local_material('C',{'Parent':'/Game/Q','scalars':{'Zero':0}})
        reparented=material_build_signature('/Game/C',self.repo,catalog=catalog)
        self.assertEqual(reparented.record.scalars,{'Other':3,'Zero':0}); self.assertNotEqual(reparented.key,after.key)
    def test_live_native_parent_refresh_and_removal_fail(self):
        parent=self.native_material('P',{'scalars':{'Value':1}}); self.native_material('C',{'Parent':'/Game/P'},'MaterialInstanceConstant')
        catalog=self.catalog(current=True); before=material_build_signature('/Game/C',self.repo,catalog=catalog)
        self.native_material('P',{'scalars':{'Value':4}})
        after=material_build_signature('/Game/C',self.repo,catalog=catalog)
        self.assertEqual(after.record.scalars['Value'],4); self.assertNotEqual(before.key,after.key)
        parent.unlink()
        with self.assertRaises(StaleCatalogError): catalog.load_material('/Game/C',self.repo)
    def test_local_raw_wins_thin_is_ignored_but_dependency_tracked(self):
        self.native_material('C',{'scalars':{'Value':1},'BlendMode':1}); catalog=self.catalog()
        arc=self.local_material('C',{'scalars':{'Value':9},'BlendMode':2})
        thin=self.local/'C.json'; thin.write_text(json.dumps({'Data':[],'IsNull':True,'BlendMode':0}))
        before=material_build_signature('/Game/C',self.repo,catalog=catalog)
        self.assertEqual(before.record.scalars['Value'],9); self.assertEqual(before.record.settings['blend'],2); self.assertEqual(sum(source.get('kind')=='local-source' for source in before.record.provenance),2)
        thin.write_text(json.dumps({'Data':[],'IsNull':False,'BlendMode':0}))
        self.assertNotEqual(material_build_signature('/Game/C',self.repo,catalog=catalog).key,before.key)
        self.local_material('C',{'scalars':{'Value':99}},package='/Game/Other/C')
        with self.assertRaises(ValueError): catalog.load_material('/Game/C',self.repo)
    def test_portable_catalog_exact_identity_context_and_integrity(self):
        self.native_material('C',{'scalars':{'V':0}}); catalog=self.catalog()
        with self.assertRaises(FileNotFoundError): catalog.load_material('/Game/Else/C',self.repo)
        wrong=AssetRepository({'/Game':self.local},build='old',mapping='native-test')
        with self.assertRaises(StaleCatalogError): catalog.load_material('/Game/C',wrong)
        wrong.mapping='other'; wrong.build='2.2.0'
        with self.assertRaises(StaleCatalogError): catalog.load_material('/Game/C',wrong)
        corrupt=json.loads(self.output.read_text()); corrupt['records']['/Game/C']['facts']['scalars']['V']=99
        with self.assertRaises(ValueError): NativeCatalog(corrupt)
    def test_actual_declared_array_count_all_slices_roles_recipe_and_png_content(self):
        self.texture('Array',3,[]); self.native_material('C',{'textures':{'Detail':'/Game/Array'}}); catalog=self.catalog()
        self.repo.array_slices['/Game/Array']=tuple('/Game/Array_LAYER'+str(i) for i in range(3))
        paths=[self.image('Array_LAYER'+str(i),bytes([i+1])) for i in range(3)]
        before=material_build_signature('/Game/C',self.repo,catalog=catalog,roles={'Detail':'data'})
        self.assertTrue(before.complete); self.assertEqual({dep['slice'] for dep in before.dependencies if dep.get('kind')=='image'},{0,1,2})
        self.assertTrue(before.can_reuse(before.key))
        with self.assertRaises(ValueError): _material_build_signature('/Game/C',self.repo,catalog=catalog)
        self.assertNotEqual(before.key,material_build_signature('/Game/C',self.repo,catalog=catalog,implementation_revision='changed-implementation').key)
        paths[2].write_bytes(b'changed last native slice')
        changed=material_build_signature('/Game/C',self.repo,catalog=catalog,roles={'Detail':'data'})
        self.assertNotEqual(before.key,changed.key)
        self.assertNotEqual(changed.key,material_build_signature('/Game/C',self.repo,catalog=catalog,roles={'Detail':'color'}).key)
        self.assertNotEqual(changed.key,material_build_signature('/Game/C',self.repo,catalog=catalog,roles={'Detail':'data'},recipe='new-builder').key)
        self.repo.array_slices['/Game/Array']=self.repo.array_slices['/Game/Array'][:2]
        with self.assertRaises(NativeMetadataError): material_build_signature('/Game/C',self.repo,catalog=catalog)
    def test_explicit_array_transport_suffix_and_missing_resources_block_reuse(self):
        self.texture('Array',3); self.native_material('C',{'textures':{'Detail':'/Game/Array'}}); catalog=self.catalog()
        for i in range(3): self.image('Array_LAYER'+str(i),bytes([i]))
        good=material_build_signature('/Game/C',self.repo,catalog=catalog,slice_suffix='_LAYER{index}')
        self.assertTrue(good.complete)
        unknown=material_build_signature('/Game/C',self.repo,catalog=catalog)
        self.assertFalse(unknown.complete); self.assertFalse(unknown.can_reuse(unknown.key))
        (self.images/'Array_LAYER2.png').unlink()
        missing=material_build_signature('/Game/C',self.repo,catalog=catalog,slice_suffix='_LAYER{index}')
        self.assertFalse(missing.complete); self.assertFalse(missing.can_reuse(good.key))
        with self.assertRaises(FileNotFoundError): material_build_signature('/Game/C',self.repo,catalog=catalog,slice_suffix='_LAYER{index}',strict_images=True)
    def test_role_isolation_does_not_rehash_same_png(self):
        from unittest.mock import patch
        self.texture(); self.native_material('C',{'textures':{'Map':'/Game/T'}}); catalog=self.catalog(); image=self.image()
        invalidate_digest_cache(); opened=[]; original=pathlib.Path.open
        def open_file(path,*args,**kwargs):
            if args and args[0]=='rb' and str(path).lower().endswith('t.png'): opened.append(path)
            return original(path,*args,**kwargs)
        with patch('pathlib.Path.open',open_file):
            signature=material_build_signature('/Game/C',self.repo,catalog=catalog,roles={'Map':('color','data')})
            self.assertEqual(len(opened),1); self.assertEqual({dep['role'] for dep in signature.dependencies if dep.get('kind')=='image'},{'color','data'})
    def test_inconsistent_native_array_metadata_and_unknown_scope_remap_fail(self):
        path=self.texture('Array',3); raw=json.loads(path.read_text()); raw['Exports'][0]['Mips'][1]['SizeZ']=4; path.write_text(json.dumps(raw))
        self.native_material('C',{'textures':{'Map':'/Game/Array'}})
        with self.assertRaises(NativeMetadataError): self.catalog()
        self.native_material('P',{'ScalarParameterValues':[{'ParameterInfo':{'Name':'X','Association':'LayerParameter','Index':1},'ParameterValue':1}]})
        self.native_material('Scoped',{'Parent':'/Game/P'},'MaterialInstanceConstant',{'CachedData':{'ParentLayerIndexRemap':[-1]}})
        catalog=self.catalog(('/Game/Scoped',))
        with self.assertRaises(NotImplementedError): catalog.load_material('/Game/Scoped',self.repo)
    def test_two_physical_game_roots_exact_lookup_and_ambiguity(self):
        alternate=self.root/'alternate'; alternate.mkdir()
        (self.images/'A.png').write_bytes(b'primary'); (alternate/'B.png').write_bytes(b'alternate')
        repo=NativeAssetRepository({'/Game':[self.local,alternate]},image_mounts={'/Game':[self.images,alternate]},build='2.2.0',mapping='native-test')
        self.assertEqual(repo.image_path('/Game/A'),self.images/'A.png')
        self.assertEqual(repo.image_path('RemappedPlugins/Content/B'),alternate/'B.png')
        (alternate/'A.png').write_bytes(b'primary')
        self.assertEqual(repo.image_path('/Game/A'),self.images/'A.png')
        self.assertEqual(len(repo.image_aliases('/Game/A')),2)
        (alternate/'A.png').write_bytes(b'different')
        with self.assertRaises(AmbiguousAssetError): repo.image_path('/Game/A')
        with self.assertRaises(FileNotFoundError): repo.image_path('/Game/Else/B')
    def test_multi_root_raw_authority_tracks_all_thin_copies(self):
        alternate=self.root/'alternate'; alternate.mkdir()
        self.local_material('C',{'scalars':{'Value':9}})
        (self.local/'C.json').write_text(json.dumps({'Data':[],'BlendMode':0}))
        other=alternate/'C.json'; other.write_text(json.dumps({'Data':[],'IsNull':True}))
        repo=NativeAssetRepository({'/Game':[self.local,alternate]},build='2.2.0',mapping='native-test')
        before=material_build_signature('/Game/C',repo)
        self.assertEqual(before.record.scalars['Value'],9); self.assertEqual(sum(source.get('kind')=='local-source' for source in before.record.provenance),3)
        self.assertEqual(repo.resolve('/Game/C'),self.local/'C.json')
        other.write_text(json.dumps({'Data':[],'IsNull':False}))
        self.assertNotEqual(before.key,material_build_signature('/Game/C',repo).key)
        duplicate=json.loads((self.local/'C.arc-material.json').read_text()); (alternate/'C.arc-material.json').write_text(json.dumps(duplicate))
        coalesced=material_build_signature('/Game/C',repo)
        self.assertEqual(coalesced.record.scalars['Value'],9)
        duplicate['Exports'][0]['Properties']['scalars']['Value']=10; (alternate/'C.arc-material.json').write_text(json.dumps(duplicate))
        with self.assertRaises(AmbiguousAssetError): material_build_signature('/Game/C',repo)
    def test_catalog_generation_supports_both_native_json_layouts(self):
        self.native_material('P',{'scalars':{'X':1}})
        remapped=self.native/'RemappedPlugins/Content'; remapped.mkdir(parents=True)
        (remapped/'C.json').write_text(json.dumps({'Exports':[{'Type':'MaterialInstanceConstant','Name':'C','Package':'RemappedPlugins/Content/C','Properties':{'Parent':'/Game/P','scalars':{'Y':0}}}]}))
        catalog=self.catalog(current=True)
        self.assertEqual(len(catalog.payload['mounts']['/Game']),2)
        self.assertEqual(catalog.load_material('/Game/C',self.repo).scalars,{'X':1,'Y':0})
        self.assertEqual(set(iter_native_material_references(self.native)),{'/Game/P','/Game/C'})
    def test_cached_only_controls_are_associated_not_flattened_across_changed_parent(self):
        self.native_material('P',{'scalars':{'Inherited':1}})
        cache={'RuntimeEntries':{'ParameterInfoSet':[{'Name':'Inherited'},{'Name':'CacheOnly'},{'Name':'Zero'}]},'ScalarValues':[1,8,99]}
        self.native_material('C',{'Parent':'/Game/P','scalars':{'Zero':0}},'MaterialInstanceConstant',{'CachedExpressionData':cache})
        catalog=self.catalog(); entry=catalog.payload['records']['/Game/C']
        self.assertNotIn('CacheOnly',entry['facts']['scalars']); self.assertEqual(entry['cached_observations']['scalars']['CacheOnly'],8)
        original=catalog.load_material('/Game/C',self.repo)
        self.assertEqual(original.scalars,{'Inherited':1,'CacheOnly':8,'Zero':0})
        self.assertEqual(next(source for source in original.provenance if source.get('kind')=='cached-observation-binding')['status'],'associated')
        self.local_material('P',{'scalars':{'Inherited':9}})
        updated=catalog.load_material('/Game/C',self.repo)
        self.assertEqual(updated.scalars,{'Inherited':9,'Zero':0})
        self.assertEqual(next(source for source in updated.provenance if source.get('kind')=='cached-observation-binding')['status'],'suppressed-parent-change')
        self.assertEqual(entry['cached_observations']['scalars']['CacheOnly'],8)
    def test_unassociated_current_cache_is_explicit_policy_not_assumed_absent(self):
        self.native_material('P',{'scalars':{'Inherited':1}}); catalog=self.catalog(('/Game/P',))
        cache={'RuntimeEntries':{'ParameterInfoSet':[{'Name':'CacheOnly'}]},'ScalarValues':[8]}
        path=self.local_material('C',{'Parent':'/Game/P'}); raw=json.loads(path.read_text()); raw['Exports'][0]['CachedExpressionData']=cache; path.write_text(json.dumps(raw))
        compatible=catalog.load_material('/Game/C',self.repo)
        self.assertEqual(compatible.scalars['CacheOnly'],8)
        proof=next(source for source in compatible.provenance if source.get('kind')=='cached-observation-binding')
        self.assertEqual(proof['status'],'unassociated'); self.assertTrue(proof['applied'])
        strict=NativeCatalog.read(self.output,cached_policy='require-associated').load_material('/Game/C',self.repo)
        self.assertNotIn('CacheOnly',strict.scalars)
    def test_identical_image_alias_deletion_changes_signature_and_inactive_conflict_does_not_block_record(self):
        alternate=self.root/'alternate'; alternate.mkdir()
        self.texture(); self.native_material('C',{'textures':{'Map':'/Game/T'}}); catalog=self.catalog()
        first=self.image(); second=alternate/'T.png'; second.write_bytes(first.read_bytes())
        repo=NativeAssetRepository({'/Game':self.local},image_mounts={'/Game':[self.images,alternate]},build='2.2.0',mapping='native-test')
        before=material_build_signature('/Game/C',repo,catalog=catalog)
        images=[entry for entry in before.dependencies if entry.get('kind')=='image']
        self.assertEqual(len(images[0]['aliases']),2); self.assertTrue(any('byte_identical' in value for value in before.diagnostics))
        second.unlink(); after=material_build_signature('/Game/C',repo,catalog=catalog)
        self.assertNotEqual(before.key,after.key)
        second.write_bytes(b'different'); self.local_material('Local',{'textures':{'Unused':'/Game/T'},'scalars':{'X':0}})
        self.assertEqual(repo.load_material('/Game/Local').scalars['X'],0)
        with self.assertRaises(AmbiguousAssetError): repo.image_path('/Game/T')
    def test_active_plan_signature_ignores_inactive_image_ambiguity_and_tracks_plan(self):
        from material_runtime import MaterialPlan,expr,literal
        alternate=self.root/'alternate'; alternate.mkdir()
        self.texture('T'); self.texture('Unused'); self.native_material('C',{'textures':{'Used':'/Game/T','Unused':'/Game/Unused'}}); catalog=self.catalog()
        self.image('T'); self.image('Unused',b'old'); (alternate/'Unused.png').write_bytes(b'different')
        repo=NativeAssetRepository({'/Game':self.local},image_mounts={'/Game':[self.images,alternate]},build='2.2.0',mapping='native-test')
        plan=MaterialPlan('/Game/C','active',{'base_color':expr('texture',reference='/Game/T',role='data')})
        before=material_build_signature('/Game/C',repo,catalog=catalog,plan=plan)
        self.assertTrue(before.complete); self.assertEqual({entry['package'] for entry in before.dependencies if entry.get('kind')=='image'},{'/Game/T'})
        (alternate/'Unused.png').write_bytes(b'different again')
        self.assertEqual(before.key,material_build_signature('/Game/C',repo,catalog=catalog,plan=plan).key)
        changed=MaterialPlan('/Game/C','active',{'base_color':expr('multiply',plan.outputs['base_color'],literal(.5))})
        self.assertNotEqual(before.key,material_build_signature('/Game/C',repo,catalog=catalog,plan=changed).key)
        with self.assertRaises(AmbiguousAssetError): material_build_signature('/Game/C',repo,catalog=catalog)
    def test_explicit_opaque_signature_does_not_require_unused_alpha_resource(self):
        from material_runtime import MaterialPlan,expr,literal
        self.native_material('C'); catalog=self.catalog()
        plan=MaterialPlan('/Game/C','opaque',{'base_color':literal((1,1,1,1)),'alpha':expr('texture',reference='/Game/Absent',role='data')},{'BlendMode':0})
        signature=material_build_signature('/Game/C',self.repo,catalog=catalog,plan=plan)
        self.assertTrue(signature.complete); self.assertFalse(any(entry.get('kind')=='image' for entry in signature.dependencies))
        generic=MaterialPlan('/Game/C','generic',plan.outputs)
        self.assertFalse(material_build_signature('/Game/C',self.repo,catalog=catalog,plan=generic).complete)
    def test_texture_metadata_provider_preserves_native_array_depth_srgb_and_source(self):
        self.texture('Array',3); self.native_material('C',{'textures':{'Map':'/Game/Array'}}); catalog=self.catalog()
        metadata=catalog.texture_metadata('/Game/Array',self.repo)
        self.assertEqual(metadata['Type'],'Texture2DArray'); self.assertEqual(metadata['Package'],'/Game/Array'); self.assertEqual(metadata['Mips'][0]['SizeZ'],3)
        self.assertFalse(metadata['Properties']['SRGB']); self.assertEqual(len(metadata['DataRaidersSource']['source_sha256']),64)
    def test_arc_texture_authority_over_thin_and_partial_scan_is_explicit(self):
        self.texture('Array',3); self.native_material('C',{'textures':{'Map':'/Game/Array'}}); catalog=self.catalog()
        raw={'Type':'Texture2DArray','Name':'Array','Package':'RemappedPlugins/Content/Array','Properties':{'SRGB':False},'Mips':[{'SizeZ':3}],'ArrayLayerCount':3}
        sidecar=self.local/'Array.arc-texture.json'; sidecar.write_text(json.dumps(raw))
        thin=self.local/'Array.json'; thin.write_text(json.dumps({'Data':[],'Properties':{'SRGB':True}}))
        repo=NativeAssetRepository({'/Game':self.local},image_mounts={'/Game':self.images},build='2.2.0',mapping='native-test')
        metadata=catalog.texture_metadata('/Game/Array',repo)
        self.assertFalse(metadata['Properties']['SRGB']); self.assertEqual(metadata['Mips'][0]['SizeZ'],3); self.assertFalse(repo.texture_info('/Game/Array')['SRGB'])
        self.native_material('Bad',{'Parent':'/Game/Absent'},'MaterialInstanceConstant')
        partial=generate_catalog(self.native,self.output,('/Game/C','/Game/Bad'),build='2.2.0',mapping='native-test',continue_on_error=True)
        self.assertIn('/Game/C',partial.payload['records']); self.assertNotIn('/Game/Bad',partial.payload['records']); self.assertEqual(len(partial.payload['errors']),1)
        with self.assertRaises(ValueError): generate_catalog(self.native,self.game/'must-not-write.json',('/Game/C',),build='2.2.0',mapping='native-test')
    def test_streaming_material_corpus_excludes_non_material_assets(self):
        self.native_material('P'); self.native_material('C',{'Parent':'/Game/P'},'MaterialInstanceConstant'); self.texture()
        references=list(iter_native_material_references(self.native))
        self.assertEqual(set(references),{'/Game/P','/Game/C'})
        catalog=self.catalog(references); self.assertEqual(len(catalog.payload['records']),2)
if __name__=='__main__': unittest.main()





