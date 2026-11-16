import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class QueuedGridTests(unittest.TestCase):
    def test_page_changes_clamp_at_last_page(self):
        tree = ast.parse((ROOT / "Outfit_Accessories/icon_browser.py").read_text())
        browser = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ARC_OUTFITS_OT_IconBrowser")
        field = next(node for node in browser.body if isinstance(node, ast.AnnAssign) and node.target.id == "page")
        self.assertIn("update", {keyword.arg for keyword in field.annotation.keywords})

    def test_previews_are_sized_for_grid_cells(self):
        tree = ast.parse((ROOT / "Outfit_Accessories/icon_browser.py").read_text())
        previews = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "template_icon"]
        self.assertTrue(previews)
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "template_preview" for node in ast.walk(tree)))


if __name__ == "__main__":
    unittest.main()
