"""Native setup prepares once without bypassing dependency validation."""
try:
    from .native_test_support import PACKAGE, namespace, temp_fixture, create_shell_fixture
except ImportError:
    from native_test_support import PACKAGE, namespace, temp_fixture, create_shell_fixture
import importlib
from pathlib import Path
import types
import unittest
from unittest.mock import patch

nf = namespace()
compiler = importlib.import_module(PACKAGE + '.Weapons.material_runtime.compiler')
OBJ = types.SimpleNamespace(type='MESH', data=types.SimpleNamespace(uv_layers=[types.SimpleNamespace(name='NativeUV')]), scale=(1., 1., 1.))


class NativePreparedSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = temp_fixture('prepared-setup-')
        self.addCleanup(self.temporary.cleanup)
        _, self.source = create_shell_fixture(Path(self.temporary.name))

    def test_entrypoint_prepares_once_and_validates_before_compiling(self):
        with patch.object(nf, 'prepare', wraps=nf.prepare) as prepare, patch.object(nf, '_candidate_signature', wraps=nf._candidate_signature) as signature, patch.object(compiler, 'compile_plan') as compile_plan:
            material = {}
            self.assertTrue(nf.setup_entrypoint(material, self.source, obj=OBJ, expected_family='enemy_shell'))
        self.assertEqual(prepare.call_count, 1)
        signature.assert_called_once()
        compile_plan.assert_called_once()
        self.assertEqual(material['dr_native_active_signature'], compile_plan.call_args.kwargs['template_key'])

    def test_direct_setup_still_prepares_and_validates(self):
        with patch.object(nf, 'prepare', wraps=nf.prepare) as prepare, patch.object(nf, '_candidate_signature', wraps=nf._candidate_signature) as signature, patch.object(compiler, 'compile_plan') as compile_plan:
            self.assertTrue(nf.setup({}, self.source, obj=OBJ))
        prepare.assert_called_once()
        signature.assert_called_once()
        compile_plan.assert_called_once()

    def test_separate_actions_prepare_again_and_changed_source_changes_signature(self):
        with patch.object(nf, 'prepare', wraps=nf.prepare) as prepare, patch.object(compiler, 'compile_plan') as compile_plan:
            self.assertTrue(nf.setup_entrypoint({}, self.source, obj=OBJ, expected_family='enemy_shell'))
            original = compile_plan.call_args.kwargs['template_key']
            source = Path(self.source)
            source.write_text(source.read_text(encoding='utf8') + '\n', encoding='utf8')
            self.assertTrue(nf.setup_entrypoint({}, self.source, obj=OBJ, expected_family='enemy_shell'))
            self.assertNotEqual(original, compile_plan.call_args.kwargs['template_key'])
        self.assertEqual(prepare.call_count, 2)

    def test_incomplete_signature_never_compiles(self):
        incomplete = types.SimpleNamespace(complete=False, diagnostics=('missing dependency',), key='invalid')
        with patch.object(nf, '_candidate_signature', return_value=incomplete), patch.object(compiler, 'compile_plan') as compile_plan:
            material = {}
            self.assertFalse(nf.setup_entrypoint(material, self.source, obj=OBJ, expected_family='enemy_shell'))
        compile_plan.assert_not_called()
        self.assertIn('Incomplete native dependency signature', material['dr_native_route_diagnostic'])

    def test_restart_guard_never_compiles(self):
        with patch.object(nf, '_candidate_signature', side_effect=nf.RestartRequired('Restart Blender')), patch.object(compiler, 'compile_plan') as compile_plan:
            material = {}
            self.assertTrue(nf.setup_entrypoint(material, self.source, obj=OBJ, expected_family='enemy_shell'))
        compile_plan.assert_not_called()
        self.assertTrue(material['dr_native_restart_required'])

    def test_family_mismatch_never_compiles(self):
        with patch.object(nf, 'prepare', wraps=nf.prepare) as prepare, patch.object(compiler, 'compile_plan') as compile_plan:
            self.assertFalse(nf.setup_entrypoint({}, self.source, obj=OBJ, expected_family='weapon'))
        prepare.assert_called_once()
        compile_plan.assert_not_called()

    def test_user_override_never_prepares(self):
        with patch.object(nf, 'is_user_override', return_value=True), patch.object(nf, 'prepare') as prepare, patch.object(compiler, 'compile_plan') as compile_plan:
            self.assertTrue(nf.setup_entrypoint({}, self.source, obj=OBJ))
        prepare.assert_not_called()
        compile_plan.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)

