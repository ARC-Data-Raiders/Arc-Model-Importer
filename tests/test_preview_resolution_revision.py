import importlib
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class PreviewResolutionRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets = importlib.import_module(ROOT.name + ".Outfit_Accessories.preview_assets")
        cls.appearance = importlib.import_module(ROOT.name + ".Outfit_Accessories.appearance")

    def test_full_resolution_avif_is_the_only_packaged_image(self):
        self.assertEqual(self.assets._manifest_data()["scale"], 1.0)
        for relative in self.assets.manifest():
            source = ROOT / relative
            self.assertEqual(Path(self.assets.low_resolution_path(str(source))), source)
        self.assertFalse(list(self.assets._FOLDER.glob("*.avif")))

    def test_loaded_source_never_replaces_bundled_preview(self):
        source = str(next((ROOT / "assets/ui_icons/appearance").glob("*.avif")))
        collection = {os.path.normcase(source): SimpleNamespace(icon_id=123)}
        with patch.object(self.appearance, "_PREVIEW_COLLECTION", collection), patch.object(self.assets, "source_preview_path", create=True, side_effect=AssertionError("No progressive extraction")), patch.object(self.appearance, "_queued_preview_icon_id_for_path", return_value=999):
            self.assertEqual(self.appearance._preview_icon_id_for_path(source), 123)
            self.assertEqual(self.appearance._preview_icon_id_for_path(source), 123)

    def test_unbundled_images_keep_queued_fallback(self):
        with patch.object(self.assets, "low_resolution_path", return_value=""), patch.object(self.appearance, "_queued_preview_icon_id_for_path", return_value=456) as queued:
            self.assertEqual(self.appearance._preview_icon_id_for_path("custom.png"), 456)
        queued.assert_called_once_with("custom.png", priority=True)
