"""Portable character fallback control/route regressions without Blender."""
import ast,importlib.util,json,os,sys,types,unittest
from pathlib import Path
ROOT=Path(os.environ['DATARAIDERS_TEST_REPOSITORY']) if os.environ.get('DATARAIDERS_TEST_REPOSITORY') else next(p for p in Path(__file__).resolve().parents if (p/'materials/character.py').is_file())
STAGE=Path(os.environ.get('DATARAIDERS_CHARACTER_STAGE',ROOT))
class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.saved=sys.modules.get('bpy');sys.modules['bpy']=types.ModuleType('bpy');spec=importlib.util.spec_from_file_location('_character_fallback_probe',STAGE/'materials/character_fallback.py');self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
    def tearDown(self):
        if self.saved is None:sys.modules.pop('bpy',None)
        else:sys.modules['bpy']=self.saved
    def test_dual_authored_lash_colors_restore_after_hair_copy(self):
        class Node(dict):
            type='RGB'
            def __init__(self,index):super().__init__(dr_lash_color_index=index);self.outputs=[types.SimpleNamespace(default_value=None)]
        class Mat(dict):pass
        material=Mat(dr_lash_authored_colors=json.dumps([(.1,.2,.3,1),(.4,.5,.6,1)]));material.node_tree=types.SimpleNamespace(nodes=[Node(0),Node(1)])
        self.assertTrue(self.module.update_lash_color(material,(.9,.8,.7,1),True));self.assertEqual([n.outputs[0].default_value for n in material.node_tree.nodes],[(.9,.8,.7,1.)]*2)
        self.assertTrue(self.module.update_lash_color(material,None,False));self.assertEqual([tuple(n.outputs[0].default_value) for n in material.node_tree.nodes],[(.1,.2,.3,1),(.4,.5,.6,1)])
    def test_native_to_fallback_migration_clears_success_proofs_preserves_overrides(self):
        class Mat(dict):pass
        material=Mat(dr_native_family='teeth',dr_native_active_signature='old-native-key',dr_native_signature_complete=True,dr_native_report='{"status":"native-success"}',dr_native_options='{}',dr_native_user_override=True,arc_native_user_override=True,arc_teeth_setup='native-teeth-v1',arc_face_surface_revision='native-static-v1',arc_face_surface_note='old native proof',arc_face_parameter_sources='old-source',arc_eye_setup='authored-v2',arc_eye_aux_native_identity=True,arc_build_report=json.dumps({'status':'native-success','user_override':{'color':'explicit red'}}),arc_user_roughness=.217)
        material.node_tree=object();self.module.organize=lambda tree:None
        self.module.finish(material,'teeth',['Pristine source unavailable'])
        self.assertNotIn('dr_native_family',material);self.assertNotIn('dr_native_report',material);self.assertNotIn('dr_native_active_signature',material);self.assertNotIn('dr_native_signature_complete',material);self.assertNotIn('arc_face_surface_revision',material);self.assertNotIn('arc_face_surface_note',material);self.assertNotIn('arc_face_parameter_sources',material);self.assertNotIn('arc_eye_setup',material);self.assertNotIn('arc_eye_aux_native_identity',material)
        self.assertEqual(material['arc_teeth_setup'],'fallback-static-v2');self.assertTrue(material['dr_native_user_override']);self.assertTrue(material['arc_native_user_override']);self.assertEqual(material['arc_user_roughness'],.217)
        report=json.loads(material['arc_build_report']);self.assertEqual(report['status'],'original-character-fallback');self.assertEqual(report['user_override'],{'color':'explicit red'})
    def test_surface_control_snapshot_is_numeric_role_scoped(self):
        class Mat(dict):pass
        mat=Mat(arc_head_role='head');mat.use_nodes=True;node=types.SimpleNamespace(type='BSDF_PRINCIPLED',inputs={'Subsurface Weight':types.SimpleNamespace(default_value=.373),'Coat Roughness':types.SimpleNamespace(default_value=.217)})
        mat.node_tree=types.SimpleNamespace(nodes=[node],as_pointer=lambda:3);self.assertEqual(self.module.surface_controls(mat,'head'),{'Subsurface Weight':.373,'Coat Roughness':.217});self.assertEqual(self.module.surface_controls(mat,'eyes'),{})
    def test_all_public_character_roles_keep_explicit_overrides_first(self):
        source=ast.parse((STAGE/'materials/character.py').read_text(encoding='utf-8'));package=types.ModuleType('_fallback_route_probe');package.__path__=[];runtime=types.ModuleType(package.__name__+'.material_runtime');runtime.__path__=[];bridge=types.ModuleType(runtime.__name__+'.native_families');bridge.is_user_override=lambda m:True
        def forbidden(*a,**k):raise AssertionError('Override reached material builder')
        bridge.setup_entrypoint=forbidden
        for module in (package,runtime,bridge):sys.modules[module.__name__]=module
        try:
            for name in ('_setup_head_slot_head','_setup_head_slot_teeth','_setup_head_slot_eyes','_setup_head_slot_eyelashes','_setup_head_slot_wet'):
                with self.subTest(role=name):
                    f=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name==name);namespace={'__package__':'_fallback_route_probe.materials'};exec(compile(ast.Module(body=[f],type_ignores=[]),'<actual fallback entry>','exec'),namespace);namespace[name](types.SimpleNamespace(material={'override':True}),'unused')
        finally:
            for module in (package,runtime,bridge):sys.modules.pop(module.__name__,None)
    def test_no_character_binary_material_or_shader_template_calls(self):
        source=ast.parse((STAGE/'materials/character.py').read_text(encoding='utf-8'))
        for n in ast.walk(source):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('ensure_node_group','ensure_material'):
                self.assertEqual(n.func.attr,'ensure_node_group');self.assertEqual(ast.literal_eval(n.args[0]),'NormalFlipper')
        fidelity=ast.parse((STAGE/'materials/character_fidelity.py').read_text(encoding='utf-8'));prepare=next(n for n in fidelity.body if isinstance(n,ast.FunctionDef) and n.name=='prepare_face_shader');self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='copy' for n in ast.walk(prepare)))
    def test_teeth_static_fallback_never_invents_preskin_normal_or_weather(self):
        source=ast.parse((STAGE/'materials/character_fallback.py').read_text(encoding='utf-8'));function=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='static_teeth');text=ast.unparse(function);self.assertNotIn('ShaderNodeNewGeometry',text);self.assertNotIn('primitive_data',text);self.assertIn('snow/wet/proximity',text)
    def test_lash_mask_uses_proven_R_not_image_alpha(self):
        source=(STAGE/'materials/character_fallback.py').read_text(encoding='utf-8');tree=ast.parse(source);function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='lash_material');text=ast.unparse(function);self.assertIn('split.outputs[0]',text);self.assertNotIn("outputs['Alpha']",text)
if __name__=='__main__':unittest.main(verbosity=2)
