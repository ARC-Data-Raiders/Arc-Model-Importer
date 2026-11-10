import importlib.util
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "dr_precompute", os.path.join(HERE, "..", "Shared", "materials", "precompute.py"))
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)

PARENT = "/Game/Pioneer/MaterialLibrary/Character/LayeredMaterials/M_Character_Layered"
TEX = "/Game/X/T_Null_N"
B = {"build": "100", "mapping_sha": "abc"}


class PrecomputeLoader(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "textures", "Game", "X"))
        with open(os.path.join(self.root, "textures", "Game", "X", "T_Null_N.png"), "wb") as fh:
            fh.write(b"png")
        with open(os.path.join(self.root, "parents.json"), "w") as fh:
            json.dump({PARENT: {"deps": [TEX]}}, fh)
        with open(os.path.join(self.root, "manifest.json"), "w") as fh:
            json.dump({"build": "100", "mapping_sha": "abc",
                       "textures": {TEX: {"file": "textures/Game/X/T_Null_N.png"}}}, fh)
        pc.set_root(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_hits(self):
        self.assertTrue(pc.manifest_ok(**B))
        self.assertEqual(pc.parent_info(PARENT + ".0", **B), {"deps": [TEX]})
        self.assertTrue(pc.shared_texture_path(TEX + ".0", **B).endswith("T_Null_N.png"))

    def test_positional_build(self):
        self.assertEqual(pc.lookup_parent(PARENT, "100"), {"deps": [TEX]})
        self.assertIsNone(pc.lookup_parent(PARENT, "101"))
        self.assertTrue(pc.shared_texture_path(TEX, "100").endswith("T_Null_N.png"))
        self.assertIsNone(pc.shared_texture_path(TEX, "101"))
        self.assertIsNone(pc.lookup_parent("/Game/M_Unknown", B))

    def test_misses(self):
        self.assertIsNone(pc.parent_info("/Game/M_Unknown", **B))
        self.assertIsNone(pc.shared_texture_path("/Game/T_Unknown", **B))

    def test_missing_folder(self):
        pc.set_root(os.path.join(self.root, "nope"))
        self.assertFalse(pc.manifest_ok(**B))
        self.assertIsNone(pc.parent_info(PARENT, **B))
        self.assertIsNone(pc.shared_texture_path(TEX, **B))

    def test_build_mismatch(self):
        bad = {"build": "101", "mapping_sha": "abc"}
        self.assertFalse(pc.manifest_ok(**bad))
        self.assertFalse(pc.manifest_ok(build="100", mapping_sha="zzz"))
        self.assertIsNone(pc.parent_info(PARENT, **bad))
        self.assertIsNone(pc.shared_texture_path(TEX, **bad))


if __name__ == "__main__":
    unittest.main()
