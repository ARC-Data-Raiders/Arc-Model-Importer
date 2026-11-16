"""Texture state reads the images: Original, Optimized, or Mixed with counts."""
import importlib
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("DATARAIDERS_TEST_REPOSITORY", Path(__file__).resolve().parents[1]))
PKG = "_dr_probe_texture_state"


def _load():
    if PKG not in sys.modules:
        pkg = types.ModuleType(PKG)
        pkg.__path__ = [str(ROOT)]
        sys.modules[PKG] = pkg
    return importlib.import_module(PKG + ".Shared.texture_sets")


class _Image(dict):
    def __init__(self, path, **props):
        super().__init__(**props)
        self.source = "FILE"
        self.type = "IMAGE"
        self.filepath = path
        self.library = None


class _Scene:
    arc_tex_format = "PNG"
    arc_tex_png_compression = 15
    arc_tex_res = "HALF"
    arc_tex_set = "OPTIMIZED"


class TextureMixedStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ts = _load()
        cls.key = cls.ts.settings_key(_Scene())

    def _opt(self, key=None):
        return _Image("//opt.png", **{self.ts.ORIGINAL_KEY: "//orig.png",
                                     self.ts.SET_KEY: key or self.key})

    def test_mixed(self):
        state = self.ts.texture_state(_Scene(), images=[self._opt(), _Image("//b.png")])
        self.assertEqual(state["label"], "Mixed: 1 of 2 optimized")
        self.assertEqual((state["optimized"], state["total"], state["current"]), (1, 2, 1))
        self.assertTrue(state["pending"])

    def test_original(self):
        scene = _Scene()
        scene.arc_tex_set = "ORIGINAL"
        state = self.ts.texture_state(scene, images=[_Image("//a.png"), _Image("//b.png")])
        self.assertEqual(state["label"], "Original")
        self.assertFalse(state["pending"])

    def test_optimized_current_and_stale(self):
        state = self.ts.texture_state(_Scene(), images=[self._opt(), self._opt()])
        self.assertEqual(state["label"], "Optimized")
        self.assertFalse(state["pending"])
        state = self.ts.texture_state(_Scene(), images=[self._opt(), self._opt("PNG_c0_div4")])
        self.assertEqual(state["label"], "Optimized")
        self.assertTrue(state["pending"])

    def test_kept_original_left_out(self):
        kept = _Image("//k.png", **{self.ts.SKIP_KEY: self.key})
        state = self.ts.texture_state(_Scene(), images=[self._opt(), kept])
        self.assertEqual(state["label"], "Optimized")
        self.assertEqual(state["total"], 1)

    def test_library_and_generated_ignored(self):
        lib = _Image("//l.png")
        lib.library = object()
        gen = _Image("")
        gen.source = "GENERATED"
        state = self.ts.texture_state(_Scene(), images=[lib, gen])
        self.assertEqual((state["label"], state["total"]), ("Original", 0))


if __name__ == "__main__":
    unittest.main()
