"""Merged weapon wear + position operator and plain tier names (source check, no import)."""
import ast
import os
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("DATARAIDERS_TEST_REPOSITORY", Path(__file__).resolve().parents[1]))
SRC = (ROOT / "Weapons" / "weapon_ui.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def _classes():
    return {n.name: n for n in TREE.body if isinstance(n, ast.ClassDef)}


def _assign(node, name):
    for stmt in getattr(node, "body", []):
        if isinstance(stmt, ast.Assign) and any(getattr(t, "id", None) == name for t in stmt.targets):
            return ast.literal_eval(stmt.value)
    return None


def _module_names(name):
    for stmt in TREE.body:
        if isinstance(stmt, ast.Assign) and any(getattr(t, "id", None) == name for t in stmt.targets):
            return stmt.value
    return None


class WeaponWearPositionTest(unittest.TestCase):
    def test_merged_operator(self):
        cls = _classes().get("ARC_OT_apply_weapon_wear_position")
        self.assertIsNotNone(cls)
        self.assertEqual(_assign(cls, "bl_idname"), "arc_outfits.apply_weapon_wear_position")
        self.assertEqual(_assign(cls, "bl_label"), "Apply Wear and Position")

    def test_registered_with_old_operators(self):
        node = _module_names("CLASSES")
        names = [getattr(e, "id", None) for e in node.elts]
        self.assertIn("ARC_OT_apply_weapon_wear_position", names)
        idnames = {_assign(c, "bl_idname") for c in _classes().values()}
        self.assertIn("arc_outfits.apply_weapon_wear_position", idnames)
        self.assertGreaterEqual(len([i for i in idnames if i and "weapon" in i]), 3)

    def test_tier_names(self):
        tiers = ast.literal_eval(_module_names("TIERS"))
        self.assertEqual([t[1] for t in tiers], ["I", "II", "III", "IV"])
        self.assertTrue(tiers[0][2].startswith("Tier I, least worn"))
        self.assertTrue(tiers[3][2].startswith("Tier IV, most worn"))

    def test_tier_caption(self):
        ns = {}
        for stmt in TREE.body:
            if (isinstance(stmt, ast.Assign) and getattr(stmt.targets[0], "id", None) == "_TIER_CAPTIONS") \
                    or (isinstance(stmt, ast.FunctionDef) and stmt.name == "tier_caption"):
                exec(compile(ast.Module([stmt], []), "weapon_ui", "exec"), ns)
        self.assertEqual(ns["tier_caption"]("3"), "Tier IV: most worn")


if __name__ == "__main__":
    unittest.main()
