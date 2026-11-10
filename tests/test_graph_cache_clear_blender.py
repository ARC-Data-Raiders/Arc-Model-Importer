"""Clear Material Graph Cache removes cache files and loaded entries (needs bpy)."""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import bpy  # noqa: F401
except ImportError:
    bpy = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(bpy is None, "needs Blender")
class GraphCacheClearTests(unittest.TestCase):
    def setUp(self):
        if str(ROOT.parent) not in sys.path:
            sys.path.insert(0, str(ROOT.parent))
        self.gc = importlib.import_module(ROOT.name + ".Shared.materials.graph_cache")

    def test_clear_removes_files_and_loaded_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "graph_cache")
            os.makedirs(os.path.join(root, "build", "recipe"))
            Path(root, "build", "recipe", "index.json").write_text("{}")
            self.gc._INDEX["k"] = {"file": "x"}
            self.gc._PRELOADED["k"] = object()
            with patch.object(self.gc, "cache_root", return_value=root):
                self.assertTrue(self.gc.clear_material_cache())
                self.assertFalse(os.path.exists(root))
                self.assertEqual(self.gc._INDEX, {})
                self.assertEqual(self.gc._PRELOADED, {})
                self.assertFalse(self.gc.clear_material_cache())


if __name__ == "__main__":
    unittest.main()
