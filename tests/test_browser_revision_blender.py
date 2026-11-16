import importlib
import hashlib
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))


class BrowserRevisionBlenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addon = importlib.import_module(ROOT.name)
        cls.browser = importlib.import_module(ROOT.name + ".Outfit_Accessories.icon_browser")
        cls.properties = importlib.import_module(ROOT.name + ".Outfit_Accessories.properties")
        cls.importing = importlib.import_module(ROOT.name + ".Shared.importing")
        cls.pak = importlib.import_module(ROOT.name + ".Shared.pak_extract")
        if not hasattr(bpy.types, "ARC_OUTFITS_OT_icon_browser"):
            cls.addon.register()
        cls.properties._refresh_outfit_csv_state()
        cls.metrics = {}

    @classmethod
    def tearDownClass(cls):
        destination = Path(os.environ["TEMP"]) / "browser_metrics.json"
        destination.write_text(json.dumps(cls.metrics, indent=2), encoding="utf-8")

    def setUp(self):
        self.browser._MULTI_SELECTION.clear()
        bpy.context.window_manager.arc_browser_multiple = False

    def test_heroes_are_selectable_as_outfits(self):
        choices = dict(self.browser.collect_items(bpy.context, "outfit"))
        for ident in ("ID_APOLLO_NAME", "ID_CELESTE_NAME", "ID_JUANITO_NAME", "ID_LANCE_NAME", "ID_SHANI_NAME"):
            self.assertIn(ident, choices)
            self.assertNotIn("mesh unavailable", choices[ident])
            self.assertTrue(self.browser.apply_selection(bpy.context, "outfit", ident))
        for ident in ("ID_TIANWEN_NAME",):
            self.assertIn("mesh unavailable", choices[ident])

    def test_selection_latency_and_no_synchronous_scan(self):
        self._assert_selection_latency()

    def test_hero_export_uses_model_folder_without_item_folder(self):
        operators = importlib.import_module(ROOT.name + ".Shared.operators")
        appearance = importlib.import_module(ROOT.name + ".Outfit_Accessories.appearance")
        utils = importlib.import_module(ROOT.name + ".Shared.utils")
        bpy.context.scene.arc_selected_outfit = "ID_CELESTE_NAME"
        row = {"ST": "ID_CELESTE_NAME", "Flavour": "Celeste", "Model Folder Name": "Celeste", "Item/UI Folder Name": ""}
        with patch.object(utils, "get_pioneer_root", return_value="export-root"), patch.object(self.importing, "load_outfit_csv", return_value=[row]), patch.object(self.pak, "configured_dir", return_value=""), patch.object(appearance, "_shipped_outfit_preview_path", return_value="preview"), patch.object(self.importing, "collect_psks_from_model_folder", return_value=(["Celeste/SK_Celeste_Jacket.psk"], [])) as collect:
            result = operators._queue_selected_outfit_psks(bpy.context)
        self.assertTrue(result[0], result[-1])
        collect.assert_called_once_with("export-root", "Celeste")
        self.assertEqual(bpy.context.scene.arc_psk_entries[0].psk_path, "Celeste/SK_Celeste_Jacket.psk")

    def _assert_selection_latency(self):
        choices = self.browser.collect_items(bpy.context, "outfit")[:20]
        self.assertGreater(len(choices), 1)
        timings = []
        with patch.object(self.properties, "_scan_selected_colorways", side_effect=AssertionError("blocking scan")):
            for ident, label in choices:
                start = time.perf_counter()
                result = bpy.ops.arc_outfits.icon_browser_pick(kind="outfit", item_id=ident)
                timings.append(time.perf_counter() - start)
                self.assertEqual(result, {"FINISHED"})
        self.assertLess(max(timings), 0.1)
        self.metrics["selection"] = {"count": len(timings), "max_ms": max(timings) * 1000}
        print("BROWSER_PICK_TIMING count=%d max_ms=%.3f" % (len(timings), max(timings) * 1000))

    def test_multiple_selection_toggles_and_imports_only_chosen(self):
        choices = self.browser.collect_items(bpy.context, "head")[:3]
        self.assertEqual(len(choices), 3)
        self.browser.apply_selection(bpy.context, "head", choices[0][0])
        bpy.context.window_manager.arc_browser_multiple = True
        self.assertEqual(self.browser.selected_items(bpy.context, "head"), {choices[0][0]})
        for ident, label in choices[1:]:
            self.assertEqual(bpy.ops.arc_outfits.icon_browser_pick(kind="head", item_id=ident), {"FINISHED"})
        bpy.ops.arc_outfits.icon_browser_pick(kind="head", item_id=choices[1][0])
        expected = {choices[0][0], choices[2][0]}
        self.assertEqual(self.browser.selected_items(bpy.context, "head"), expected)
        imports = []
        with patch.object(self.browser, "_dispatch_browser_import", side_effect=lambda kind: imports.append((kind, self.browser.current_selection(bpy.context, kind))) or {"FINISHED"}):
            self.assertEqual(bpy.ops.arc_outfits.import_browser_selection(kind="head"), {"FINISHED"})
        self.assertEqual(imports, [("head", choices[0][0]), ("head", choices[2][0])])
        self.assertIsNone(self.browser._BROWSER_IMPORT_ACTIVE)

    def test_enabling_multiple_preserves_existing_and_prior_picks(self):
        choices = self.browser.collect_items(bpy.context, "head")[:3]
        self.browser.apply_selection(bpy.context, "head", choices[0][0])
        self.browser.selected_items(bpy.context, "head").add(choices[2][0])
        bpy.context.window_manager.arc_browser_multiple = True
        self.assertEqual(self.browser.selected_items(bpy.context, "head"), {choices[0][0], choices[2][0]})
        bpy.ops.arc_outfits.icon_browser_pick(kind="head", item_id=choices[1][0])
        imports = []
        with patch.object(self.browser, "_dispatch_browser_import", side_effect=lambda kind: imports.append(self.browser.current_selection(bpy.context, kind)) or {"FINISHED"}):
            self.assertEqual(bpy.ops.arc_outfits.import_browser_selection(kind="head"), {"FINISHED"})
        self.assertEqual(set(imports), {ident for ident, label in choices})

    def test_single_confirmation_imports_current_and_releases_popup(self):
        ident = self.browser.collect_items(bpy.context, "head")[0][0]
        self.browser.apply_selection(bpy.context, "head", ident)
        self.browser._BROWSER_DIALOG = object()
        imports = []
        try:
            with patch.object(self.browser, "_dispatch_browser_import", side_effect=lambda kind: imports.append((kind, self.browser.current_selection(bpy.context, kind))) or {"FINISHED"}):
                self.assertEqual(bpy.ops.arc_outfits.import_browser_selection(kind="head"), {"FINISHED"})
            self.assertEqual(imports, [("head", ident)])
            self.assertIsNone(self.browser._BROWSER_DIALOG)
        finally:
            self.browser._BROWSER_DIALOG = None

    def test_cached_colourways_never_export(self):
        row = {"Item/UI Folder Name": "Dashwood"}
        with patch.object(self.importing, "_ensure_pak_colourway_folder", side_effect=AssertionError("PAK export during browse")), patch.object(self.importing, "scan_outfit_presets_in_folder", return_value=[]), patch.object(self.pak, "cache_dir", return_value=str(ROOT / "temp")):
            self.assertEqual(self.importing.colorways_for_outfit_row("", row, allow_extract=False), [])

    def test_browse_pick_is_importable_despite_sidebar_filters(self):
        lists = importlib.import_module(ROOT.name + ".Shared.ui_lists")
        targets = importlib.import_module(ROOT.name + ".Shared.ui_targets")
        ident = self.browser.collect_items(bpy.context, "outfit")[0][0]
        bpy.context.scene.arc_outfit_search = "not-the-browse-selection"
        bpy.context.window_manager.arc_filter_outfit = "FAV"
        lists._CURRENT_BUILD["outfit"] = lambda: []
        self.assertEqual(bpy.ops.arc_outfits.icon_browser_pick(kind="outfit", item_id=ident), {"FINISHED"})
        self.assertEqual(bpy.context.scene.arc_outfit_search, "")
        self.assertEqual(bpy.context.window_manager.arc_filter_outfit, "ALL")
        self.assertEqual(targets.visible_outfit_key(bpy.context), ident)

    def test_registered_browser_defaults_and_layout_choices(self):
        rna = bpy.ops.arc_outfits.icon_browser.get_rna_type()
        self.assertNotIn("columns", rna.properties)
        self.assertNotIn("width", rna.properties)
        self.assertTrue(hasattr(bpy.types.WindowManager, "arc_browser_multiple"))

    def test_arc_thumbnail_crop_preserves_packaged_art(self):
        import bpy.utils.previews
        appearance = importlib.import_module(ROOT.name + ".Outfit_Accessories.appearance")
        collection = bpy.utils.previews.new()
        sizes = []
        try:
            for source in sorted((ROOT / "assets/arc_browser").glob("*.avif"))[:3]:
                before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
                preview = collection.load(source.name, str(source), "IMAGE")
                before_size = tuple(preview.image_size)
                appearance._crop_sidebar_preview(preview)
                after_size = tuple(preview.image_size)
                self.assertGreater(min(after_size), 0)
                self.assertLessEqual(after_size[0], before_size[0])
                self.assertLessEqual(after_size[1], before_size[1])
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before_hash)
                sizes.append({"file": source.name, "source": before_size, "thumbnail": after_size})
        finally:
            bpy.utils.previews.remove(collection)
        self.assertEqual(len(sizes), 3)
        self.metrics["arc_previews"] = sizes


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
