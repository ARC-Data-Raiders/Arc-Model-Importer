"""Portable guard against Ascended scans on the normal weapon path."""
import ast
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "Weapons/weapon_import.py"


class VariantDispatchTests(unittest.TestCase):
    def test_every_variant_scan_is_guarded_by_non_off_selection(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        violations = []

        def is_non_off_guard(node):
            if not isinstance(node, ast.Compare):
                return False
            text = ast.unparse(node)
            return "variant" in text and "OFF" in text and "!=" in text

        def walk(node, guarded=False):
            if isinstance(node, list):
                for item in node:
                    walk(item, guarded)
                return
            if isinstance(node, ast.If):
                walk(node.test, guarded)
                walk(node.body, guarded or is_non_off_guard(node.test))
                walk(node.orelse, guarded)
                return
            if isinstance(node, ast.IfExp):
                walk(node.test, guarded)
                walk(node.body, guarded or is_non_off_guard(node.test))
                walk(node.orelse, guarded)
                return
            if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
                prefix_guard = guarded
                for value in node.values:
                    walk(value, prefix_guard)
                    prefix_guard = prefix_guard or is_non_off_guard(value)
                return
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "variants"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "assets"):
                if not guarded:
                    violations.append(node.lineno)
            for child in ast.iter_child_nodes(node):
                walk(child, guarded)

        walk(tree)
        self.assertEqual([], violations,
                         f"assets.variants is reachable with OFF at weapon_import.py:{violations}")

    def test_explicit_variant_validation_and_stale_enum_fallback_remain(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("if variant!='OFF' and chosen is None:raise ValueError", source)
        self.assertIn("if variant!='OFF' and variant not in {v['id'] for v in assets.variants", source)


if __name__ == "__main__":
    unittest.main()
