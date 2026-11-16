"""Texture switch: a failed relink rolls back, leaves no tags, stays pending, and retries."""
import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

import bpy

ROOT = Path(os.environ.get("DATARAIDERS_TEST_REPOSITORY", Path(__file__).resolve().parents[1]))
PKG = "_dr_probe_texture_state"


def _load():
    if PKG not in sys.modules:
        pkg = types.ModuleType(PKG)
        pkg.__path__ = [str(ROOT)]
        sys.modules[PKG] = pkg
    return importlib.import_module(PKG + ".Shared.texture_sets")


class _Scene(dict):
    arc_tex_format = "PNG"
    arc_tex_png_compression = 15
    arc_tex_res = "HALF"
    arc_tex_set = "OPTIMIZED"


def _png(path, size):
    img = bpy.data.images.new("_dr_tmp_png", size, size)
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


class TextureSwitchRollbackTest(unittest.TestCase):
    def setUp(self):
        self.ts = _load()
        self.tmp = tempfile.mkdtemp(prefix="dr_tex_rollback_")
        self.orig = os.path.join(self.tmp, "orig.png")
        self.opt = os.path.join(self.tmp, "opt.png")
        _png(self.orig, 8)
        _png(self.opt, 4)
        self.img = bpy.data.images.load(self.orig, check_existing=False)
        self._build = self.ts.build_optimized
        self._reload = self.ts._reload
        self.ts.build_optimized = lambda scene, img, stats=None: self.opt

    def tearDown(self):
        self.ts.build_optimized = self._build
        self.ts._reload = self._reload
        bpy.data.images.remove(self.img)
        for name in ("orig.png", "opt.png"):
            try:
                os.remove(os.path.join(self.tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(self.tmp)
        except OSError:
            pass

    def test_injected_failure_rolls_back_then_retry_switches(self):
        ts = self.ts
        calls = {"n": 0}

        def failing(img):
            calls["n"] += 1
            raise RuntimeError("injected reload failure")

        ts._reload = failing
        scene = _Scene()
        stats = ts.switch_set(scene, "OPTIMIZED", images=[self.img])
        self.assertEqual(calls["n"], 1)
        self.assertEqual(stats["switched"], 0)
        self.assertEqual([name for name, _err in stats["failed"]], [self.img.name])
        self.assertIn("injected reload failure", stats["failed"][0][1])
        self.assertEqual(os.path.normcase(bpy.path.abspath(self.img.filepath)), os.path.normcase(self.orig))
        for key in (ts.ORIGINAL_KEY, ts.SET_KEY, ts.SKIP_KEY):
            self.assertNotIn(key, self.img)
        state = ts.texture_state(scene, images=[self.img])
        self.assertEqual(state["label"], "Original")
        self.assertTrue(state["pending"])

        ts._reload = self._reload
        stats = ts.switch_set(scene, "OPTIMIZED", images=[self.img])
        self.assertEqual((stats["switched"], stats["failed"]), (1, []))
        self.assertEqual(self.img[ts.SET_KEY], ts.settings_key(scene))
        self.assertEqual(os.path.normcase(self.img[ts.ORIGINAL_KEY]), os.path.normcase(self.orig))
        self.assertEqual(os.path.normcase(bpy.path.abspath(self.img.filepath)), os.path.normcase(self.opt))
        state = ts.texture_state(scene, images=[self.img])
        self.assertEqual(state["label"], "Optimized")
        self.assertFalse(state["pending"])

    def test_missing_target_file_keeps_original(self):
        ts = self.ts
        ts.build_optimized = lambda scene, img, stats=None: os.path.join(self.tmp, "gone.png")
        stats = ts.switch_set(_Scene(), "OPTIMIZED", images=[self.img])
        self.assertEqual(len(stats["failed"]), 1)
        self.assertNotIn(ts.SET_KEY, self.img)
        self.assertEqual(os.path.normcase(bpy.path.abspath(self.img.filepath)), os.path.normcase(self.orig))


if __name__ == "__main__":
    unittest.main()
