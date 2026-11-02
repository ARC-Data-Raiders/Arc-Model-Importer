try:
    from .native_test_support import ROOT,namespace,functions_module,PACKAGE,temp_fixture,create_shell_fixture,write_json
except ImportError:
    from native_test_support import ROOT,namespace,functions_module,PACKAGE,temp_fixture,create_shell_fixture,write_json
"""Execute staged canonical cache/UI function bodies without addon registration."""
import ast,importlib,importlib.util,json,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
STAGE=ROOT
harness=types.SimpleNamespace(namespace=namespace,functions_module=functions_module,PACKAGE=PACKAGE)
nf=namespace()
class Obj:type='MESH'
class Mat(dict):use_nodes=True

def stale_function(expected='scan_display'):
    tree=ast.parse((STAGE/'materials/dispatch.py').read_text(encoding='utf-8'))
    fn=next(node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name=='_stale_shared')
    module=types.ModuleType(harness.PACKAGE+'.materials.cache_probe');module.__package__=harness.PACKAGE+'.materials'
    module.__dict__.update(signature='abc',_expected_family=lambda:expected,FAMILY_ENEMY='enemy',FAMILY_SCAN='scan_display',FAMILY_ENVIRONMENT='environment',FAMILY_METAL='metal',FAMILY_ROAD='road',stem='name',mi_path='source',obj=Obj())
    for name in ('_stale_decal_misclass','_needs_map_decal_mask_rebuild','_needs_trim_setup_rebuild','_needs_proptrim_or_glass_rebuild','_needs_env_props_rebuild','_needs_water_setup_rebuild'):module.__dict__[name]=lambda *a:False
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(STAGE/'materials/dispatch.py'),'exec'),module.__dict__)
    return module._stale_shared

class CanonicalIntegrationTests(unittest.TestCase):
    def test_installed_runtime_uses_relative_imports_and_exact_family_gate(self):
        for path in (STAGE/'material_runtime').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node,ast.ImportFrom):self.assertFalse((node.module or '').startswith('material_runtime'),str(path))
        self.assertTrue({'weapon','display','eyelashes','enemy_shell','prop'}.issubset(set(nf.MASTERS.values())));self.assertTrue({'skin','eyes','hair'}.isdisjoint(set(nf.MASTERS.values())))
    def test_native_warm_cache_skips_legacy_stale_heuristics(self):
        stale=stale_function();mat=Mat(dr_native_family='display',dr_native_recipe_version=nf.NATIVE_FAMILY_RECIPE,arc_mi_family='scan_display',arc_mi_source_signature='abc')
        with patch.object(nf,'native_cache_is_stale',return_value=False) as probe:
            self.assertFalse(stale(mat));probe.assert_called_once()
        with patch.object(nf,'native_cache_is_stale',return_value=True):self.assertTrue(stale(mat))
        self.assertTrue(nf.native_cache_is_stale(mat,'source',obj=Obj()))
        self.assertTrue(nf.cache_recipe_is_current(mat));mat['dr_native_recipe_version']=0;self.assertFalse(nf.cache_recipe_is_current(mat))
    def test_retained_legacy_scan_version_does_not_rebuild_forever(self):
        mat=Mat(arc_mi_family='scan_display',arc_mi_source_signature='abc',arc_scan_reconstruction_version=4)
        self.assertFalse(stale_function()(mat))
    def test_explicit_override_skips_cache_source_queries(self):
        stale=stale_function();stale.__globals__['_expected_family']=lambda:(_ for _ in ()).throw(AssertionError('override caused source query'))
        self.assertFalse(stale(Mat(dr_native_user_override=True)))
    def test_metadata_refusal_keeps_existing_profile_and_no_new_recipe_stamp(self):
        mat=Mat(arc_weapon_shader_version=1)
        with patch.object(nf,'prepare',return_value=nf.Prepared(False,'raw native parent unavailable')):
            self.assertTrue(nf.setup_entrypoint(mat,'source',obj=Obj(),expected_family='weapon'))
        self.assertEqual(mat['arc_weapon_shader_version'],1);self.assertNotIn('dr_native_recipe_version',mat);self.assertIn('unavailable',mat['dr_native_route_diagnostic'])
    def test_scan_callback_branches_before_legacy_named_nodes(self):
        module=harness.functions_module('enemy_scan',('set_scan_state',),None);mat=Mat(dr_native_family='display')
        with patch.object(nf,'set_scan_state',return_value=True) as update:
            self.assertTrue(module.set_scan_state(mat,'ALERT'));update.assert_called_once_with(mat,'ALERT')
        with patch.object(nf,'set_scan_state',return_value=True) as update:
            self.assertTrue(module.set_scan_state(mat,'invalid'));update.assert_called_once_with(mat,'SCANNING')
    def test_lash_copy_off_branches_before_flat_root_bsdf_tint(self):
        module=harness.functions_module('character',('_apply_hair_color_to_eyelash_material',),None);mat=Mat(dr_native_family='eyelashes')
        with patch.object(nf,'set_eyelash_hair_color',return_value=True) as update:
            self.assertTrue(module._apply_hair_color_to_eyelash_material(mat,use_hair_color=False));update.assert_called_once_with(mat,None,enabled=False)

    def test_existing_report_user_override_preserved_before_setup_and_state_callback(self):
        mat=Mat(arc_build_report=json.dumps({'family':'scan_display','user_override':{'base_color_srgb':'#FF6666FF','emission_color_srgb':'#FF6666FF','emission_strength':2.0}}))
        self.assertTrue(nf.is_user_override(mat))
        with patch.object(nf,'prepare',side_effect=AssertionError('override caused native source query')):
            self.assertTrue(nf.setup_entrypoint(mat,'source',obj=Obj(),expected_family='display'))
        module=harness.functions_module('enemy_scan',('set_scan_state',),None)
        self.assertTrue(module.set_scan_state(mat,'SCANNING'))
        self.assertNotIn('dr_native_recipe_version',mat)
    def test_active_image_and_metadata_bytes_invalidate_native_route_cache(self):
        core=importlib.import_module(nf.__package__+'.core')
        with temp_fixture(prefix='native-active-cache-') as folder:
            base=Path(folder)
            (base/'Material.arc-material.json').write_text(json.dumps({'Type':'Material','Name':'Material','Package':'/Game/Material','Properties':{'BlendMode':'BLEND_Opaque'}}),encoding='utf-8')
            metadata={'Type':'Texture2D','Name':'T','Package':'/Game/T','Properties':{'SRGB':True},'Mips':[{'SizeX':1,'SizeY':1,'SizeZ':1}]}
            (base/'T.arc-texture.json').write_text(json.dumps(metadata),encoding='utf-8');(base/'T.png').write_bytes(b'original-image-bytes')
            repository=nf.VerificationRepository({'/Game':str(base)})
            record=repository.load_material('/Game/Material')
            plan=core.MaterialPlan(record.package,'weapon',{'base_color':core.expr('texture',reference='/Game/T',role='color'),'alpha':core.expr('texture',reference='/Game/UnusedOpaqueAlpha',role='data')},{'blend':'opaque'})
            candidate=nf.Prepared(True,'fixture',family='weapon',record=record,plan=plan,repository=repository)
            first=nf._candidate_signature(candidate);self.assertTrue(first.complete,first.diagnostics)
            mat=Mat(dr_native_family='weapon',dr_native_recipe_version=nf.NATIVE_FAMILY_RECIPE,dr_native_active_signature=first.key,dr_native_options='{}')
            with patch.object(nf,'prepare',return_value=candidate):self.assertFalse(nf.native_cache_is_stale(mat,'source',obj=Obj()))
            (base/'T.png').write_bytes(b'changed-active-image-bytes')
            with patch.object(nf,'prepare',return_value=candidate):self.assertTrue(nf.native_cache_is_stale(mat,'source',obj=Obj()))
            second=nf._candidate_signature(candidate);mat['dr_native_active_signature']=second.key;metadata['Properties']['Filter']='TextureFilter::TF_Nearest';(base/'T.arc-texture.json').write_text(json.dumps(metadata),encoding='utf-8')
            with patch.object(nf,'prepare',return_value=candidate):self.assertTrue(nf.native_cache_is_stale(mat,'source',obj=Obj()))
            mat.pop('dr_native_active_signature')
            self.assertTrue(nf.native_cache_is_stale(mat,'source',obj=Obj()))

    def test_explicit_report_override_skips_direct_dispatch_before_metadata_services(self):
        module=harness.functions_module('dispatch',('_dispatch_weapon_slot_material',),None)
        mat=Mat(arc_build_report=json.dumps({'user_override':{'emission_strength':2.0}}))
        module._dispatch_weapon_slot_material(mat,'missing','','mi_loop','slot')
        self.assertNotIn('dr_native_recipe_version',mat)

    def test_all_native_array_layers_invalidate_even_when_plan_samples_one_slice(self):
        core=importlib.import_module(nf.__package__+'.core')
        with temp_fixture(prefix='native-array-cache-') as folder:
            base=Path(folder);write_json(base/'Material.arc-material.json',{'Type':'Material','Name':'Material','Package':'/Game/Material','Properties':{'BlendMode':'BLEND_Opaque'}})
            write_json(base/'Array.arc-texture.json',{'Type':'Texture2DArray','Name':'Array','Package':'/Game/Array','Properties':{'SRGB':False},'Mips':[{'SizeX':1,'SizeY':1,'SizeZ':3}]})
            for i in range(3):(base/('Array_LAYER'+str(i)+'.png')).write_bytes(b'layer-'+bytes([i]))
            repository=nf.VerificationRepository({'/Game':str(base)});nf.register_array(repository,'/Game/Array');record=repository.load_material('/Game/Material');plan=core.MaterialPlan(record.package,'weapon',{'base_color':core.expr('texture',reference='/Game/Array',slice=0,role='data')},{'blend':'opaque'});candidate=nf.Prepared(True,'fixture',family='weapon',record=record,plan=plan,repository=repository)
            before=nf._candidate_signature(candidate);self.assertTrue(before.complete,before.diagnostics)
            (base/'Array_LAYER2.png').write_bytes(b'changed-unsampled-native-layer')
            after=nf._candidate_signature(candidate);self.assertTrue(after.complete,after.diagnostics);self.assertNotEqual(before.key,after.key)
    def test_missing_active_texture_metadata_keeps_profile_without_success_stamp(self):
        core=importlib.import_module(nf.__package__+'.core')
        with temp_fixture(prefix='native-incomplete-cache-') as folder:
            base=Path(folder);write_json(base/'Material.arc-material.json',{'Type':'Material','Name':'Material','Package':'/Game/Material','Properties':{'BlendMode':'BLEND_Opaque'}});(base/'T.png').write_bytes(b'image-without-native-metadata')
            repository=nf.VerificationRepository({'/Game':str(base)});record=repository.load_material('/Game/Material');plan=core.MaterialPlan(record.package,'weapon',{'base_color':core.expr('texture',reference='/Game/T',role='color')},{'blend':'opaque'});candidate=nf.Prepared(True,'fixture',family='weapon',record=record,plan=plan,repository=repository);mat=Mat(arc_weapon_shader_version=1)
            with patch.object(nf,'prepare',return_value=candidate):self.assertTrue(nf.setup_entrypoint(mat,'source',obj=Obj(),expected_family='weapon'))
            self.assertEqual(mat['arc_weapon_shader_version'],1);self.assertNotIn('dr_native_recipe_version',mat);self.assertIn('Incomplete native dependency signature',mat['dr_native_route_diagnostic'])
