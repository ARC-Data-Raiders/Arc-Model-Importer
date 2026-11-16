import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class QueuedSidebarTests(unittest.TestCase):
    def test_asset_modifications_has_section_icon(self):
        tree = ast.parse((ROOT / "sidebar.py").read_text())
        icons = next(node.value for node in tree.body if isinstance(node, ast.Assign) and any(getattr(target, "id", "") == "_SECTION_ICONS" for target in node.targets))
        self.assertNotEqual(ast.literal_eval(icons).get("arc_asset_modifications", "NONE"), "NONE")

    def test_asset_scrolls_default_to_three_rows(self):
        tree = ast.parse((ROOT / "sidebar.py").read_text())
        lists = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "template_list" and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "ARC_UL_rows"]
        self.assertTrue(lists)
        for call in lists:
            rows = next(keyword.value for keyword in call.keywords if keyword.arg == "rows")
            self.assertEqual(ast.literal_eval(rows), 3, ast.get_source_segment((ROOT / "sidebar.py").read_text(), call))

    def test_top_level_sections_have_distinct_wrapper(self):
        tree = ast.parse((ROOT / "sidebar.py").read_text())
        self.assertTrue(any(isinstance(node, ast.FunctionDef) and node.name == "_draw_top_level_section" for node in tree.body))


if __name__ == "__main__":
    unittest.main()
