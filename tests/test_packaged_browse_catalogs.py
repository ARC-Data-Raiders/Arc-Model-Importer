import importlib.util
import logging
import gzip
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_browse_catalog_test"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
utils = types.ModuleType(PACKAGE + ".utils")
utils.get_logger = lambda: logging.getLogger(PACKAGE)
utils.get_pioneer_root = lambda: ""
sys.modules[utils.__name__] = utils


def load_module(name):
    spec = importlib.util.spec_from_file_location(PACKAGE + "." + name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fmdex = load_module("fmdex")
animation = load_module("animation_catalog")
niagara = load_module("niagara_catalog")


class PackagedBrowsingTests(unittest.TestCase):
    def setUp(self):
        animation.invalidate()
        niagara.invalidate()

    def test_browsing_never_discovers_external_data(self):
        fmdex._browse_catalog = None
        real_open = open

        def packaged_open(path, *args, **kwargs):
            self.assertEqual(os.fspath(path), fmdex._BROWSE_CATALOG_PATH)
            return real_open(path, *args, **kwargs)

        with ExitStack() as stack:
            for target in ("os.walk", "os.scandir", "os.listdir", "os.stat", "os.path.isdir", "os.path.isfile", "os.path.getmtime"):
                stack.enter_context(patch(target, side_effect=AssertionError("browse filesystem discovery: " + target)))
            stack.enter_context(patch("builtins.open", side_effect=packaged_open))
            stack.enter_context(patch.object(utils, "get_pioneer_root", side_effect=AssertionError("game root")))
            stack.enter_context(patch.object(fmdex, "ensure_loaded", side_effect=AssertionError("FMDex discovery")))
            for module, parameter in ((animation, "cache_dir"), (niagara, "catalog_root")):
                entries = module.ensure_loaded("missing/external/override")
                self.assertGreater(len(entries), 0)
                self.assertEqual(module.catalog_stats("changed/override")["total"], len(entries))
                selected = module.get_entry(entries[0]["key"], "another/override")
                self.assertEqual(selected["key"], entries[0]["key"])
                query = entries[0]["stem"]
                self.assertTrue(module.filter_entries(query, **{parameter: "missing"}))
                self.assertGreater(module.count_entries(query, **{parameter: "missing"}), 0)
                self.assertTrue(module.display_label(selected))

    def test_empty_and_short_queries_and_selected_copies(self):
        for module in (animation, niagara):
            entries = module.ensure_loaded()
            self.assertEqual(module.filter_entries("", limit=3), entries[:3])
            self.assertEqual(module.filter_entries("", limit=0), entries)
            self.assertEqual(module.count_entries(""), len(entries))
            query = entries[0]["stem"][0]
            self.assertEqual(len(module.filter_entries(query, limit=0)), module.count_entries(query))
            selected = module.get_entry(entries[0]["key"])
            selected["stem"] = "changed only for action"
            self.assertNotEqual(module.get_entry(entries[0]["key"])["stem"], selected["stem"])
            self.assertFalse(entries[0].get("path"))
            self.assertFalse(entries[0].get("psa_path"))

    def test_missing_packaged_catalog_does_not_fall_back_to_discovery(self):
        fmdex._browse_catalog = None
        try:
            with patch.object(fmdex, "_BROWSE_CATALOG_PATH", "missing-packaged-catalog.gz"), patch.object(fmdex, "ensure_loaded", side_effect=AssertionError("discovery")):
                with self.assertRaises(FileNotFoundError):
                    animation.ensure_loaded()
        finally:
            fmdex._browse_catalog = None

    def test_action_resolves_portable_ns_and_override_without_walking(self):
        entry = niagara.get_entry(niagara.ensure_loaded()[0]["key"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / entry["source_json"]
            path.parent.mkdir(parents=True)
            path.write_text("[]")
            with patch("os.walk", side_effect=AssertionError("unexpected fallback walk")), patch.object(utils, "get_pioneer_root", side_effect=AssertionError("unnecessary backup root")):
                self.assertEqual(Path(niagara.resolve_entry_path(entry, str(root))), path)
                self.assertEqual(Path(niagara.resolve_entry_path(entry, scene=types.SimpleNamespace(arc_ns_catalog_root=str(root)))), path)
            self.assertEqual(entry["path"], "")

    def test_action_preserves_flat_dump_override_and_existing_fallback(self):
        entry = niagara.get_entry(niagara.ensure_loaded()[0]["key"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / (entry["stem"] + ".json")
            path.write_text("[]")
            self.assertEqual(Path(niagara.resolve_entry_path(entry, directory)), path)
        with patch.object(fmdex, "resolve_catalog_file", return_value=""), patch.object(niagara, "resolved_catalog_root", return_value=""), patch.object(fmdex, "resolve_export_file", return_value="existing-fallback.json") as fallback:
            self.assertEqual(niagara.resolve_entry_path(entry), "existing-fallback.json")
            fallback.assert_called_once_with(entry["stem"], ".json")

    def test_animation_apply_resolves_and_enriches_without_mutating_browse_row(self):
        entry = animation.get_entry(animation.ensure_loaded()[0]["key"])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / entry["source_json"]
            source.parent.mkdir(parents=True)
            source.write_text("[]")
            psa = source.with_suffix(".psa")
            psa.write_bytes(b"retained-psa")
            sidecar = source.with_suffix(".notifies.json")
            sidecar.write_text(json.dumps({"AnimName": entry["stem"], "Skeleton": "LiveSkeleton", "SequenceLength": 7, "Notifies": []}))
            with patch.object(fmdex, "ensure_loaded", side_effect=AssertionError("unnecessary FMDex")), patch("os.walk", side_effect=AssertionError("unnecessary export walk")):
                self.assertEqual(Path(animation.resolve_psa_path(entry, directory)), psa)
                animation.enrich_entry(entry, directory)
            self.assertEqual(entry["skeleton"], "LiveSkeleton")
            self.assertEqual(entry["length"], 7)
            self.assertFalse(animation.get_entry(entry["key"])["psa_path"])

    def test_duplicate_package_keys_never_fall_back_to_ambiguous_basename(self):
        entry = {"key": "/Game/Pioneer/A/Clip", "stem": "Clip", "game_path": "/Game/Pioneer/A/Clip"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Clip.json").write_text("[]")
            (root / "Clip.psa").write_bytes(b"another-package")
            with patch.object(fmdex, "resolve_export_file", side_effect=AssertionError("ambiguous basename fallback")), patch("os.walk", side_effect=AssertionError("ambiguous walk")):
                self.assertEqual(fmdex.resolve_catalog_file(entry, ".json", directory), "")
                self.assertEqual(niagara.resolve_entry_path(entry, directory), "")
                self.assertEqual(animation.resolve_psa_path(entry, directory), "")

    def test_action_psa_fallback_cache_tracks_root_override(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first"
            second = Path(directory) / "second"
            for root in (first, second):
                path = root / "arbitrary" / "Clip.psa"
                path.parent.mkdir(parents=True)
                path.write_bytes(b"psa")
            entry = {"key": "Clip", "stem": "Clip"}
            self.assertEqual(Path(animation.resolve_psa_path(dict(entry), str(first))), first / "arbitrary/Clip.psa")
            self.assertEqual(Path(animation.resolve_psa_path(dict(entry), str(second))), second / "arbitrary/Clip.psa")


class CatalogProducerTests(unittest.TestCase):
    def test_source_identity_changes_only_with_its_export_and_roundtrips(self):
        spec = importlib.util.spec_from_file_location("_build_browse_catalogs", ROOT / "scripts" / "build_browse_catalogs.py")
        producer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(producer)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clip = root / "PioneerGame/Content/Pioneer/Characters/Animations/Clip.json"
            effect = root / "PioneerGame/Content/Pioneer/Effects/NS_Real.json"
            duplicate = root / "PioneerGame/Content/Pioneer/Enemies/Animations/Clip.json"
            for path in (clip, effect, duplicate):
                path.parent.mkdir(parents=True, exist_ok=True)
            clip.write_text(json.dumps([{"Type": "AnimSequence", "Name": "Clip", "Properties": {"SequenceLength": 1}}]))
            duplicate.write_bytes(clip.read_bytes())
            effect.write_text(json.dumps([{"Type": "NiagaraSystem", "Name": "NS_Real", "Properties": {"ShaderLUT": [1]}}]))
            (clip.parent / "AS_NotAClip.json").write_text(json.dumps([{"Type": "AnimBlueprint", "Name": "AS_NotAClip"}]))
            (effect.parent / "NS_NotASystem.json").write_text(json.dumps([{"Type": "Material", "Name": "NS_NotASystem"}]))
            before = producer.build_catalog(root, "fixture-build")
            self.assertEqual(producer.verify_catalog(before, root)["verified_references"], 3)
            after = producer.build_catalog(root, "fixture-build")
            self.assertEqual(before, after)
            self.assertEqual(len(before["animations"]["rows"]), 2)
            self.assertEqual(len(before["effects"]["rows"]), 1)
            self.assertTrue(all(row[0].startswith("/Game/") for row in before["animations"]["rows"]))
            output = root / "catalog.gz"
            producer.write_catalog(before, output)
            first_bytes = output.read_bytes()
            producer.write_catalog(before, output)
            self.assertEqual(first_bytes, output.read_bytes())
            with gzip.open(output, "rt") as source:
                self.assertEqual(json.load(source)["source_generation"], before["source_generation"])
            clip.write_text(json.dumps([{"Type": "AnimSequence", "Name": "Clip", "Properties": {"SequenceLength": 2}}]))
            after = producer.build_catalog(root, "fixture-build")
            self.assertNotEqual(before["source_generation"], after["source_generation"])
            with self.assertRaisesRegex(ValueError, "Catalog source changed"):
                producer.verify_catalog(before, root)
            self.assertEqual(producer.verify_catalog(after, root)["verified_references"], 3)
            self.assertEqual(before["effects"], after["effects"])
            self.assertNotEqual(before["animations"], after["animations"])
            for kind in ("animations", "effects"):
                columns = after[kind]["columns"]
                for row in after[kind]["rows"]:
                    entry = dict(zip(columns, row))
                    self.assertEqual(Path(fmdex.resolve_catalog_file(entry, ".json", directory)), root / entry["source_json"])


if __name__ == "__main__":
    unittest.main()
