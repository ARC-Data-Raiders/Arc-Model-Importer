"""Portable restart guard regressions; no native files, SDK, Blender or hot reload."""
try:
    from .native_test_support import ROOT,PACKAGE,namespace,temp_fixture,create_shell_fixture
except ImportError:
    from native_test_support import ROOT,PACKAGE,namespace,temp_fixture,create_shell_fixture
import importlib,types,sys,unittest
from pathlib import Path
from unittest.mock import patch
nf=namespace();identity=importlib.import_module(PACKAGE+'.material_runtime.loaded_identity')
class UV:name='NativeUV'
OBJ=types.SimpleNamespace(type='MESH',data=types.SimpleNamespace(uv_layers=[UV()]),scale=(1.,1.,1.))

class NativeLoadedIdentityTests(unittest.TestCase):
    def setUp(self):self.tmp=temp_fixture('loaded-identity-');self.directory=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def source(self,value):return 'from '+PACKAGE+'.material_runtime.loaded_identity import capture_loaded_source\ndef value():return '+str(value)+'\ncapture_loaded_source(globals())\n'
    def load(self,name,text,installed=None):
        path=self.directory/(name+'.py');path.write_text(text if installed is None else installed,encoding='utf8');module=types.ModuleType(name);module.__file__=str(path);sys.modules[name]=module;exec(compile(text,str(path),'exec'),module.__dict__);self.addCleanup(sys.modules.pop,name,None);return module,path
    def test_disk_edit_does_not_execute_new_code_or_gain_new_loaded_identity(self):
        module,path=self.load('identity_current',self.source(1));before=identity.require_loaded_identity(module);path.write_text(self.source(2),encoding='utf8');self.assertEqual(module.value(),1)
        with self.assertRaisesRegex(identity.RestartRequired,'source changed'):identity.require_loaded_identity(module)
        self.assertEqual(getattr(module,identity.MARKER)['source_sha256'],before['source_sha256'])
    def test_stale_executed_module_code_does_not_match_new_installed_source(self):
        module,_=self.load('identity_stale',self.source(1),installed=self.source(2));self.assertEqual(module.value(),1)
        with self.assertRaisesRegex(identity.RestartRequired,'loaded code disagrees'):identity.require_loaded_identity(module)
    def test_missing_marker_and_module_path_changes_require_restart(self):
        with self.assertRaisesRegex(identity.RestartRequired,'no load-time'):identity.require_loaded_identity(types.ModuleType('old_unmarked'))
        module,path=self.load('identity_path',self.source(1));other=self.directory/'copy.py';other.write_bytes(path.read_bytes());module.__file__=str(other)
        with self.assertRaisesRegex(identity.RestartRequired,'module path changed'):identity.require_loaded_identity(module)
    def test_all_transitive_helpers_are_bound_and_validation_does_not_recompile(self):
        first=nf._implementation_revision('prop')
        with patch('builtins.compile',side_effect=AssertionError('Per-material validation cannot recompile')):self.assertEqual(nf._implementation_revision('prop'),first)
        for name in ('renderer_vectors','defaults','compiler','blockout_glow'):
            module=importlib.import_module(PACKAGE+'.material_runtime.'+name);self.assertTrue(getattr(module,identity.MARKER)['valid'])
    def test_warm_mixed_load_preserves_graph_and_existing_signature(self):
        _,source=create_shell_fixture(self.directory);candidate=nf.prepare(source,obj=OBJ);self.assertTrue(candidate.accepted,candidate.reason);signature=nf._candidate_signature(candidate);sentinel=object();material={'graph':sentinel,'dr_native_recipe_version':nf.NATIVE_FAMILY_RECIPE,'dr_native_family':'enemy_shell','dr_native_active_signature':signature.key,'dr_native_options':'{}'};module=importlib.import_module(PACKAGE+'.material_runtime.renderer_vectors')
        with patch.object(module,identity.MARKER,None):self.assertFalse(nf.native_cache_is_stale(material,source,obj=OBJ))
        self.assertIs(material['graph'],sentinel);self.assertEqual(material['dr_native_active_signature'],signature.key);self.assertTrue(material['dr_native_restart_required']);self.assertFalse(material['dr_native_signature_complete']);self.assertIn('Restart Blender',material['dr_native_route_diagnostic'])
    def test_fresh_mixed_load_is_handled_refusal_without_generic_fallback_or_new_stamp(self):
        _,source=create_shell_fixture(self.directory);sentinel=object();material={'graph':sentinel};module=importlib.import_module(PACKAGE+'.material_runtime.defaults');compiler=importlib.import_module(PACKAGE+'.material_runtime.compiler')
        with patch.object(module,identity.MARKER,None),patch.object(compiler,'compile_plan',side_effect=AssertionError('Mixed code cannot compile')):
            handled=nf.setup_entrypoint(material,source,obj=OBJ,expected_family='enemy_shell',cache_family='decal')
        self.assertTrue(handled,'Dispatcher must stop before its legacy generic fallback');self.assertIs(material['graph'],sentinel);self.assertTrue(material['dr_native_restart_required']);self.assertNotIn('dr_native_active_signature',material);self.assertNotIn('dr_native_family',material)
    def test_freshly_loaded_current_source_has_its_own_new_identity(self):
        old,_=self.load('identity_before',self.source(1));before=identity.require_loaded_identity(old);current,_=self.load('identity_after',self.source(2));after=identity.require_loaded_identity(current);self.assertEqual(current.value(),2);self.assertNotEqual(before['loaded_code_sha256'],after['loaded_code_sha256'])

if __name__=='__main__':unittest.main(verbosity=2)
