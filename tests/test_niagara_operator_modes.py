r"""Run from the repository root in PowerShell:
$env:DR_ADDON_REPO = (Get-Location).Path
$env:DR_NIAGARA_TEST_ROOT = 'X:\Agents\DataRaiders-BlenderImporter\niagara-fix'
& 'C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe' --background --factory-startup --python tests/test_niagara_operator_modes.py
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import bpy

OVERLAY = Path(__file__).resolve().parents[1]
REPO = Path(os.environ.get("DR_ADDON_REPO", str(OVERLAY)))
PACKAGE = "_niagara_operator_mode_regression"
spec = importlib.util.spec_from_file_location(
    PACKAGE, REPO / "__init__.py",
    submodule_search_locations=[str(OVERLAY), str(REPO)],
)
addon = importlib.util.module_from_spec(spec)
sys.modules[PACKAGE] = addon
spec.loader.exec_module(addon)
catalog = importlib.import_module(PACKAGE + ".niagara_catalog")
niagara = importlib.import_module(PACKAGE + ".niagara_fx")
pak = importlib.import_module(PACKAGE + ".Shared.pak_extract")


class Scene(dict):
    arc_ns_selected = "NS_Break_WindowBlinds"
    arc_ns_catalog_root = ""
    arc_ns_json_path = ""
    arc_ns_asset_root = ""
    arc_ns_bake_curves = False
    arc_usmap_path = ""
    arc_import_source = "PAK"


class NiagaraOperatorModeTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(os.environ["DR_NIAGARA_TEST_ROOT"])
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temp.name)
        self.entry = catalog.get_entry("NS_Break_WindowBlinds")
        self.assertIsNotNone(self.entry)
        self.scene = Scene()
        self.context = types.SimpleNamespace(scene=self.scene)
        self.reports = []
        self.action = types.SimpleNamespace(
            use_selected=True, filepath="",
            report=lambda level, message: self.reports.append(message),
        )
        self.operator = addon.operators.ARC_OUTFITS_OT_ImportNiagaraFX

    def tearDown(self):
        self.temp.cleanup()

    def test_pak_mode_extracts_exact_selected_package_then_imports_cache_json(self):
        real_resolve = catalog.resolve_entry_path

        def miss_only_before_extraction(entry, catalog_root="", scene=None):
            if scene is not None:
                return ""
            return real_resolve(entry, catalog_root)

        def extract(game_dir, usmap, paths):
            self.assertEqual(game_dir, "Paks")
            self.assertEqual(usmap, "mapping.usmap")
            self.assertEqual(paths, [self.entry["game_path"]])
            output = self.root / self.entry["source_json"]
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({"Exports": []}), encoding="utf-8")
            return 0, "json NS_Break_WindowBlinds", []

        imported = []
        fake_root = types.SimpleNamespace(name="NS_Break_WindowBlinds")

        def import_ns(path, **kwargs):
            imported.append((path, kwargs))
            return fake_root, {"emitters": 0, "renderers": 0}, []

        with patch.object(catalog, "resolve_entry_path", side_effect=miss_only_before_extraction), \
             patch.object(pak, "configured_dir", return_value="Paks"), \
             patch.object(pak, "resolve_usmap", return_value="mapping.usmap"), \
             patch.object(pak, "cache_dir", return_value=str(self.root)), \
             patch.object(pak, "extract_paths", side_effect=extract) as extraction, \
             patch.object(niagara, "import_ns_json", side_effect=import_ns):
            result = self.operator.execute(self.action, self.context)

        expected = self.root / self.entry["source_json"]
        self.assertEqual(result, {"FINISHED"})
        extraction.assert_called_once()
        self.assertEqual(Path(imported[0][0]), expected)
        self.assertEqual(self.scene.arc_ns_json_path, str(expected))

    def test_files_mode_skips_pak_even_when_pak_folder_is_configured(self):
        self.scene.arc_import_source = "FILES"
        self.action.report = lambda level, message: self.reports.append(message)
        with patch.object(catalog, "get_entry", return_value=self.entry), \
             patch.object(catalog, "resolve_entry_path", return_value=""), \
             patch.object(pak, "configured_dir", return_value="Paks"), \
             patch.object(pak, "resolve_usmap", return_value="mapping.usmap"), \
             patch.object(pak, "extract_paths") as extraction:
            result = self.operator.execute(self.action, self.context)
        self.assertEqual(result, {"CANCELLED"})
        extraction.assert_not_called()
        self.assertIn("use Import NS File", self.reports[-1])

    def test_explicit_file_action_does_not_resolve_selection_or_extract_pak(self):
        source = self.root / "explicit.json"
        source.write_text("{\"Exports\": []}", encoding="utf-8")
        self.scene.arc_import_source = "FILES"
        self.action.use_selected = False
        self.action.filepath = str(source)
        fake_root = types.SimpleNamespace(name="NS_Explicit")
        with patch.object(catalog, "resolve_entry_path", side_effect=AssertionError("catalog lookup")), \
             patch.object(pak, "extract_paths", side_effect=AssertionError("PAK extraction")), \
             patch.object(niagara, "import_ns_json", return_value=(fake_root, {}, [])):
            result = self.operator.execute(self.action, self.context)
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual(self.scene.arc_ns_json_path, str(source))


if __name__ == "__main__":
    sys.argv = [__file__]
    unittest.main(verbosity=2)
