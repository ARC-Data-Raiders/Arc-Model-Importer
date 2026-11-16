"""R9 producer fallbacks gate as inconsistency in the audit rules (never verified)."""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.normpath(os.path.join(HERE, '..', '..', 'Shared', 'materials', 'audit_param_rules.py'))


def _rules():
    spec = importlib.util.spec_from_file_location('audit_param_rules_r9', RULES)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ApproxSeverityTests(unittest.TestCase):
    def setUp(self):
        self.R = _rules()

    def test_fallback_flags_are_inconsistency(self):
        for flag in ('normals_approximate', 'unset_textures', 'uv_owner_unresolved'):
            self.assertEqual(self.R.severity(flag), self.R.INCONSISTENCY, flag)

    def test_fallback_flags_gate(self):
        gating = (self.R.DEFECT, self.R.INCONSISTENCY)
        for flag in ('normals_approximate', 'unset_textures', 'uv_owner_unresolved'):
            self.assertIn(self.R.severity(flag), gating)

    def test_unknown_flag_stays_defect(self):
        self.assertEqual(self.R.severity('normals_broken_xyz'), self.R.DEFECT)


if __name__ == '__main__':
    unittest.main()
