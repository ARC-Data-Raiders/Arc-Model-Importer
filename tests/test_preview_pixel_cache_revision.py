import importlib
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class PreviewPixelCacheRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.appearance = importlib.import_module(ROOT.name + ".Outfit_Accessories.appearance")
        cls.assets = importlib.import_module(ROOT.name + ".Outfit_Accessories.preview_assets")

    def test_bundled_thumbnail_uses_pixels_without_avif_decode(self):
        class Collection(dict):
            def new(self, key):
                self[key] = SimpleNamespace(image_size=(0, 0), image_pixels=[], icon_id=99)
                return self[key]

            def load(self, *args):
                raise AssertionError("Bundled pixel cache must avoid AVIF decoding")

        collection = Collection()
        pixels = bytes((10, 20, 30, 255, 40, 50, 60, 128))
        with patch.object(self.appearance, "_PREVIEW_COLLECTION", collection), patch.object(self.assets, "thumbnail_pixels", create=True, return_value=((2, 1), pixels)) as read:
            self.assertEqual(self.appearance._load_low_resolution_thumbnail("cached.avif"), 99)
            self.assertEqual(self.appearance._load_low_resolution_thumbnail("cached.avif"), 99)
        read.assert_called_once_with("cached.avif")
        preview = collection[os.path.normcase("cached.avif")]
        self.assertEqual(preview.image_size, (2, 1))
        self.assertEqual(len(preview.image_pixels), 2)

    def test_source_upgrades_yield_to_visible_thumbnail_queue(self):
        with patch.object(self.appearance, "_LOW_RESOLUTION_PRIORITY", {"visible"}), patch.object(self.appearance, "_PREVIEW_PENDING", {"source": "source"}), patch.object(self.appearance, "register_previews") as register:
            self.assertEqual(self.appearance._prepare_sidebar_previews(), 0.001)
        register.assert_not_called()

    def test_all_cached_pixels_match_their_bundled_avif_and_native_hash(self):
        data = self.assets._manifest_data()
        entries = data["pixel_cache"]["entries"]
        self.assertEqual(len(entries), len({entry["thumbnail"] for entry in data["entries"].values()}))
        for name, entry in entries.items():
            with self.subTest(thumbnail=name):
                path = ROOT / name
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"])
                dimensions, pixels = self.assets.thumbnail_pixels(str(path))
                self.assertEqual(dimensions, (entry["width"], entry["height"]))
                self.assertEqual(hashlib.sha256(pixels).hexdigest(), entry["pixels_sha256"])

    def test_pixel_cache_matches_native_blender_preview(self):
        import bpy.utils.previews
        import numpy as np

        collection = bpy.utils.previews.new()
        try:
            entries = self.assets._manifest_data()["pixel_cache"]["entries"]
            names = list(entries)
            for name in (names[0], names[len(names) // 2], names[-1]):
                path = ROOT / name
                dimensions, pixels = self.assets.thumbnail_pixels(str(path))
                preview = collection.load(name, str(path), "IMAGE")
                self.assertEqual(dimensions, tuple(preview.image_size))
                self.assertEqual(pixels, np.asarray(preview.image_pixels[:], dtype="<i4").tobytes())
        finally:
            bpy.utils.previews.remove(collection)

    def test_missing_or_damaged_pixel_cache_retains_avif_fallback(self):
        path = ROOT / next(iter(self.assets._manifest_data()["pixel_cache"]["entries"]))
        with patch.object(Path, "open", side_effect=OSError("missing cache")):
            self.assertIsNone(self.assets.thumbnail_pixels(str(path)))
        with patch.object(self.assets.zlib, "decompress", side_effect=self.assets.zlib.error("damaged cache")):
            self.assertIsNone(self.assets.thumbnail_pixels(str(path)))
        self.assertIsNone(self.assets.thumbnail_pixels(str(ROOT / "outside.avif")))
