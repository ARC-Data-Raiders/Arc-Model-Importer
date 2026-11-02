try:
    from .native_test_support import ROOT,namespace,functions_module,PACKAGE,temp_fixture,create_shell_fixture,write_json
except ImportError:
    from native_test_support import ROOT,namespace,functions_module,PACKAGE,temp_fixture,create_shell_fixture,write_json
"""Bounded candidate adapter contracts, source authority and native arrays."""
import importlib.util,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
import material_runtime.native_families as adapter
from material_runtime.assets import NativeAssetRepository
from material_runtime.core import expr,literal,evaluate,serialize

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=temp_fixture();self.addCleanup(self.temp.cleanup)
        self.assets,self.source=create_shell_fixture(Path(self.temp.name))

    def test_known_export_mount_and_package_identity(self):
        source=self.source
        mounts=adapter.infer_mounts(source)
        self.assertEqual(adapter.source_package(source,mounts),'/Game/Pioneer/Enemies/Fixture/MI_Shell')
        self.assertEqual(len(mounts['/Game']),2)
    def test_flat_or_unproven_source_retains_existing_graph(self):
        mat={'original':'working'}
        self.assertFalse(adapter.setup(mat,Path(self.temp.name)/'unproven/Flat.json'))
        self.assertEqual(mat,{'original':'working'})
    def test_explicit_override_and_overlay_requirements_do_not_mutate(self):
        mat={}
        self.assertFalse(adapter.setup(mat,'unused',preserve_user_override=True))
        self.assertEqual(mat,{})
        prepared=adapter.prepare('unused',verification_sources={'/Game/A':'unused'})
        self.assertFalse(prepared.accepted);self.assertIn('disabled',prepared.reason)
    def test_saved_runtime_context_roundtrips_native_expressions(self):
        context={'context':{'enemy_eye_adaptation':literal(2.),'weapon_detail_amount':expr('add',literal(.2),literal(.3))}}
        restored=adapter._restore_options(json.loads(json.dumps(serialize(context))))
        self.assertEqual(evaluate(restored['context']['weapon_detail_amount']),.5)
        self.assertEqual(evaluate(restored['context']['enemy_eye_adaptation']),2.)
    def test_explicit_three_and_nine_layer_arrays_use_all_native_mips(self):
        repo=NativeAssetRepository({'/Game':[self.assets/'PioneerGame/Content',self.assets/'RemappedPlugins/Content']})
        for ref,depth in (('/Game/Array3',3),('/Game/Array9',9)):
            base=self.assets/'PioneerGame/Content'
            write_json(base/(ref.removeprefix('/Game/')+'.arc-texture.json'),{'Type':'Texture2DArray','Name':ref.rsplit('/',1)[-1],'Package':ref,'Mips':[{'SizeX':2,'SizeY':2,'SizeZ':depth},{'SizeX':1,'SizeY':1,'SizeZ':depth}]})
            for i in range(depth):(base/(ref.removeprefix('/Game/')+'_LAYER'+str(i)+'.png')).write_bytes(b'fixture-layer-'+bytes([i]))
            result=adapter.register_array(repo,ref)
            self.assertEqual(result['depth'],depth);self.assertEqual(len(repo.array_slices[ref]),depth)
            self.assertTrue(repo.array_slices[ref][-1].endswith('_LAYER'+str(depth-1)))
    def test_catalog_projection_needs_exact_package_and_native_provenance(self):
        class Repo:
            array_slices={}
            def image_path(self,ref,role):return ref+'.png'
        def projection(package):return {'Type':'Texture2DArray','Name':'Array','Package':package,'Mips':[{'SizeZ':3},{'SizeZ':3}],'DataRaidersSource':{'projection':'native-catalog-texture-v1','source_sha256':'a'*64}}
        result=adapter.register_array(Repo(),'/Game/Array',native_texture_metadata=lambda ref:projection(ref))
        self.assertEqual(result['depth'],3)
        with self.assertRaises(ValueError):adapter.register_array(Repo(),'/Game/Array',native_texture_metadata=lambda ref:projection('/Game/Other'))
        bad=projection('/Game/Array');bad['Mips'][1]['SizeZ']=2
        with self.assertRaises(ValueError):adapter.register_array(Repo(),'/Game/Array',native_texture_metadata=lambda ref:bad)
    def test_explicit_shell_uv_name_and_native_temporal_limit_survive(self):
        class UV:name='ExactNativeUV'
        class Data:uv_layers=[UV()]
        class Obj:data=Data();scale=(1,1,1)
        source=self.source
        candidate=adapter.prepare(source,obj=Obj());self.assertTrue(candidate.accepted,candidate.reason)
        found=[];seen=set()
        def walk(node):
            from material_runtime.core import Expr
            if not isinstance(node,Expr) or id(node) in seen:return
            seen.add(id(node))
            if node.op=='tangent_view':found.append(node.attrs['uv_map'])
            for x in node.args:walk(x)
            walk(node.attrs.get('uv'))
        for node in candidate.plan.outputs.values():walk(node)
        self.assertEqual(set(found),{'ExactNativeUV'});self.assertTrue(any('temporal' in x for x in candidate.plan.diagnostics))

    def test_windows_extended_drive_unc_and_idempotence(self):
        slash=chr(92);prefix=slash*2+'?'+slash
        drive='Z:'+slash+'Exports'+slash+'deep'+slash+'a'*280+'.png'
        self.assertEqual(adapter.extended_windows_path_text(drive),prefix+drive)
        unc=slash*2+'server'+slash+'share'+slash+'asset.png'
        expected=prefix+'UNC'+slash+'server'+slash+'share'+slash+'asset.png'
        self.assertEqual(adapter.extended_windows_path_text(unc),expected)
        self.assertEqual(adapter.extended_windows_path_text(expected),expected)
        with self.assertRaises(ValueError):adapter.extended_windows_path_text('Z:relative.png')
    def test_every_selected_parent_requires_raw_native_authority(self):
        repository=adapter.VerificationRepository({})
        with patch.object(repository,'material_sources',return_value=[('.json',Path('unused'))]),patch.object(repository,'_is_native_json',side_effect=lambda path,ref:ref=='/Game/Top'):
            def parent_load(repo,reference,chain=()):return repo.load_material('/Game/ThinParent',chain+('/Game/Top',))
            with patch.object(NativeAssetRepository,'load_material',new=parent_load):
                with self.assertRaisesRegex(ValueError,'selected material/parent /Game/ThinParent'):
                    repository.load_material('/Game/Top')
    def test_inferred_repository_and_resolved_images_keep_extended_prefix(self):
        import os
        if os.name!='nt':self.skipTest('actual Windows filesystem contract')
        source=self.source
        class UV:name='NativeUV'
        class Data:uv_layers=[UV()]
        class Obj:data=Data();scale=(1,1,1)
        prepared=adapter.prepare(source,obj=Obj());self.assertTrue(prepared.accepted,prepared.reason)
        prefix=chr(92)*2+'?'+chr(92)
        self.assertTrue(prepared.source.startswith(prefix))
        self.assertTrue(all(path.startswith(prefix) for path in prepared.report['images']))
        self.assertTrue(all(str(root).startswith(prefix) for roots in prepared.repository.mount_roots.values() for root in roots))

    def test_opaque_alpha_dependency_is_inactive_while_masked_and_unset_remain_required(self):
        from material_runtime.core import MaterialPlan
        class UV:name='NativeUV'
        class Data:uv_layers=[UV()]
        class Obj:data=Data();scale=(1,1,1)
        source=self.source
        missing_alpha=expr('texture',reference='/Game/AbsentAlphaDependency',role='data')
        for settings,accepted in (({'blend':'opaque'},True),({'blend':'masked'},False),({},False)):
            with self.subTest(settings=settings):
                plan=MaterialPlan('/Game/Fixture','enemy_shell',{'base_color':literal((.2,.3,.4)),'alpha':missing_alpha},settings)
                with patch('material_runtime.decals.compile_enemy_shell',return_value=plan):candidate=adapter.prepare(source,obj=Obj())
                self.assertEqual(candidate.accepted,accepted,candidate.reason)
                if accepted:self.assertEqual(candidate.report['images'],[])
