import importlib
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
import bpy.utils.previews

ROOT = Path(__file__).resolve().parents[1]


class PreviewCacheTests(unittest.TestCase):
    def setUp(self):
        assets = importlib.import_module(ROOT.name + ".Outfit_Accessories.preview_assets")
        unbundled = patch.object(assets, "low_resolution_path", return_value="")
        unbundled.start()
        self.addCleanup(unbundled.stop)

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT.parent))
        cls.appearance = importlib.import_module(ROOT.name + ".Outfit_Accessories.appearance")
        cls.cache = importlib.import_module(ROOT.name + ".Outfit_Accessories.preview_cache")

    def test_disk_thumbnail_survives_preview_collection_restart(self):
        appearance = self.appearance
        source = str(next((ROOT / "assets/ui_icons/appearance").glob("*.avif")))
        previous = appearance._PREVIEW_COLLECTION
        collection = bpy.utils.previews.new()
        appearance._PREVIEW_COLLECTION = collection
        appearance._PREVIEW_PENDING.clear()
        try:
            with tempfile.TemporaryDirectory(dir=os.environ["DATARAIDERS_TEST_TEMP"]) as folder, patch.dict(os.environ, {"DATARAIDERS_PREVIEW_CACHE": folder}):
                started = time.perf_counter()
                appearance._preview_icon_id_for_path(source)
                while appearance._PREVIEW_PENDING:
                    appearance._prepare_sidebar_previews()
                cold_seconds = time.perf_counter() - started
                cached = self.cache.cache_path(source)
                self.assertTrue(Path(cached).is_file())
                cold = tuple(collection[os.path.normcase(source)].image_size)
                cold_pixels = list(collection[os.path.normcase(source)].image_pixels[:])
                self.assertLessEqual(max(cold), self.cache.MAX_SIDE)
                collection.clear()
                started = time.perf_counter()
                with patch.object(self.cache, "thumbnail", side_effect=AssertionError("warm cache decoded original pixels")):
                    appearance._preview_icon_id_for_path(source)
                    while appearance._PREVIEW_PENDING:
                        appearance._prepare_sidebar_previews()
                disk_seconds = time.perf_counter() - started
                self.assertEqual(tuple(collection[os.path.normcase(source)].image_size), cold)
                import numpy as np
                before = np.asarray(cold_pixels, dtype=np.int32).view(np.uint8)
                after = np.asarray(collection[os.path.normcase(source)].image_pixels[:], dtype=np.int32).view(np.uint8)
                difference = np.abs(after.astype(np.int16) - before.astype(np.int16))
                self.assertLessEqual(int(difference.max()), 2)
                self.assertLess(float(difference.mean()), 0.5)
                np.testing.assert_array_equal(after[3::4], before[3::4])
                print("PREVIEW_CACHE_DECODE cold_ms=%.3f disk_ms=%.3f" % (cold_seconds * 1000, disk_seconds * 1000))
                start = time.perf_counter()
                for index in range(1000):
                    self.assertEqual(appearance._preview_icon_id_for_path(source), collection[os.path.normcase(source)].icon_id)
                elapsed = time.perf_counter() - start
                self.assertLess(elapsed, 0.1)
                report = Path(os.environ['DATARAIDERS_TEST_TEMP']).parent / 'preview-report.txt'
                report.write_text('cold_ms=%.3f disk_ms=%.3f memory_1000_ms=%.3f max_channel_error=%d mean_channel_error=%.6f size=%s\n' % (cold_seconds * 1000, disk_seconds * 1000, elapsed * 1000, difference.max(), difference.mean(), cold), encoding='utf-8')
                print("PREVIEW_CACHE_MEMORY calls=1000 seconds=%.6f" % elapsed)
        finally:
            appearance._PREVIEW_COLLECTION = previous
            appearance._PREVIEW_PENDING.clear()
            bpy.utils.previews.remove(collection)

    def test_source_changes_invalidate_disk_cache_identity(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DATARAIDERS_TEST_TEMP"]) as folder:
            source = Path(folder) / "source.png"
            source.write_bytes(b"first")
            before = self.cache.cache_path(str(source))
            source.write_bytes(b"second-content")
            self.assertNotEqual(self.cache.cache_path(str(source)), before)

    def test_mtime_change_rebuilds_disk_thumbnail_after_restart(self):
        appearance = self.appearance
        original = next((ROOT / 'assets/ui_icons/appearance').glob('*.avif'))
        previous = appearance._PREVIEW_COLLECTION
        collection = bpy.utils.previews.new()
        appearance._PREVIEW_COLLECTION = collection
        appearance._PREVIEW_PENDING.clear()
        try:
            with tempfile.TemporaryDirectory(dir=os.environ['DATARAIDERS_TEST_TEMP']) as folder, patch.dict(os.environ, {'DATARAIDERS_PREVIEW_CACHE': str(Path(folder) / 'cache')}):
                source = Path(folder) / 'source.avif'
                shutil.copyfile(original, source)
                with patch.object(self.cache, 'thumbnail', wraps=self.cache.thumbnail) as thumbnail:
                    appearance._preview_icon_id_for_path(str(source))
                    while appearance._PREVIEW_PENDING:
                        appearance._prepare_sidebar_previews()
                    first = self.cache.cache_path(str(source))
                    self.assertTrue(Path(first).is_file())
                    self.assertEqual(thumbnail.call_count, 1)
                    collection.clear()
                    appearance._preview_icon_id_for_path(str(source))
                    while appearance._PREVIEW_PENDING:
                        appearance._prepare_sidebar_previews()
                    self.assertEqual(thumbnail.call_count, 1)
                    collection.clear()
                    stat = source.stat()
                    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000000))
                    second = self.cache.cache_path(str(source))
                    self.assertNotEqual(first, second)
                    appearance._preview_icon_id_for_path(str(source))
                    while appearance._PREVIEW_PENDING:
                        appearance._prepare_sidebar_previews()
                    self.assertEqual(thumbnail.call_count, 2)
                    self.assertTrue(Path(second).is_file())
        finally:
            appearance._PREVIEW_COLLECTION = previous
            appearance._PREVIEW_PENDING.clear()
            bpy.utils.previews.remove(collection)

    def test_png_round_trip_preserves_orientation_and_channels(self):
        import numpy as np
        rgba = np.array([[[255, 0, 0, 255], [0, 255, 0, 255]],
                         [[0, 0, 255, 255], [160, 80, 20, 200]],
                         [[10, 20, 30, 50], [255, 255, 255, 255]]], dtype=np.uint8)
        collection = bpy.utils.previews.new()
        try:
            with tempfile.TemporaryDirectory(dir=os.environ["DATARAIDERS_TEST_TEMP"]) as folder:
                destination = str(Path(folder) / "colors.png")
                self.cache.store(destination, rgba)
                preview = collection.load("colors", destination, "IMAGE")
                self.assertEqual(tuple(preview.image_size), (2, 3))
                actual = np.asarray(preview.image_pixels[:], dtype=np.int32).view(np.uint8).reshape(3, 2, 4)
                np.testing.assert_allclose(actual.astype(np.int16), rgba.astype(np.int16), atol=1, rtol=0)
        finally:
            bpy.utils.previews.remove(collection)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
