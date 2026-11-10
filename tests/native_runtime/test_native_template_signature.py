"""Only complete dependency signatures may authorize native graph templates."""
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


class NativeTemplateSignatureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = temp_fixture('template-signature-')
        self.addCleanup(self.temporary.cleanup)
        _, self.source = create_shell_fixture(Path(self.temporary.name))

    def test_setup_passes_complete_current_signature(self):
        candidate = nf.prepare(self.source, obj=OBJ)
        self.assertTrue(candidate.accepted, candidate.reason)
        signature = nf._candidate_signature(candidate)
        self.assertTrue(signature.complete, signature.diagnostics)
        material = {}
        with patch.object(compiler, 'compile_plan') as compile_plan:
            self.assertTrue(nf.setup(material, self.source, obj=OBJ))
        self.assertEqual(compile_plan.call_args.kwargs['template_key'], signature.key)
        self.assertEqual(material['dr_native_active_signature'], signature.key)

    def test_incomplete_signature_never_consults_compiler_cache(self):
        incomplete = types.SimpleNamespace(complete=False, diagnostics=('missing dependency',), key='not-authorized')
        with patch.object(nf, '_candidate_signature', return_value=incomplete), patch.object(compiler, 'compile_plan') as compile_plan:
            self.assertFalse(nf.setup({}, self.source, obj=OBJ))
        compile_plan.assert_not_called()

    def test_build_and_mapping_changes_cannot_reuse_template_key(self):
        candidate = nf.prepare(self.source, obj=OBJ)
        before = nf._candidate_signature(candidate)
        for attribute in ('build', 'mapping'):
            original = getattr(candidate.repository, attribute)
            setattr(candidate.repository, attribute, original + '-changed')
            changed = nf._candidate_signature(candidate)
            self.assertNotEqual(before.key, changed.key)
            setattr(candidate.repository, attribute, original)

    def test_changed_source_cannot_reuse_template_key(self):
        before = nf._candidate_signature(nf.prepare(self.source, obj=OBJ))
        source = Path(self.source)
        source.write_text(source.read_text(encoding='utf8') + '\n', encoding='utf8')
        changed = nf._candidate_signature(nf.prepare(self.source, obj=OBJ))
        self.assertNotEqual(before.key, changed.key)

    def test_restart_guard_never_consults_compiler_cache(self):
        with patch.object(nf, '_candidate_signature', side_effect=nf.RestartRequired('Restart Blender')), patch.object(compiler, 'compile_plan') as compile_plan:
            material = {}
            self.assertFalse(nf.setup(material, self.source, obj=OBJ))
        compile_plan.assert_not_called()
        self.assertTrue(material['dr_native_restart_required'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
