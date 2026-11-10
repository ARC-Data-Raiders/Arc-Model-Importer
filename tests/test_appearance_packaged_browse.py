"""Real catalog callbacks must not invoke external asset providers."""
import builtins
import csv
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from section_fixture import add_parents, path_of, q

ROOT = Path(__file__).resolve().parents[1]


def load_modules():
    package = ModuleType("appearance_test_addon")
    package.__path__ = [str(ROOT)]
    modules = {package.__name__: package}
    for name in ("importing", "rig", "utils", "textures", "pak_extract", "pak_catalog"):
        module = ModuleType(package.__name__ + "." + q(name))
        modules[module.__name__] = module
    modules[package.__name__ + ".Shared.utils"].get_pioneer_root = Mock(side_effect=AssertionError("external discovery"))
    bpy = ModuleType("bpy")
    bpy.context = SimpleNamespace(scene=SimpleNamespace(arc_appearance_csv_path="X:/forbidden/custom.csv"))
    bpy.path = SimpleNamespace(abspath=lambda value: value)
    modules["bpy"] = bpy
    add_parents(modules, package.__name__)
    with patch.dict(sys.modules, modules):
        for name in ("backpack_catalog", "appearance"):
            spec = importlib.util.spec_from_file_location(package.__name__ + "." + q(name), path_of(name))
            module = importlib.util.module_from_spec(spec)
            modules[spec.name] = module
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            add_parents(sys.modules, package.__name__)
            setattr(package, name, module)
    return package, modules


class AppearanceBrowseTests(unittest.TestCase):
    def setUp(self):
        self.addon, self.modules = load_modules()
        self.appearance = self.addon.appearance
        self.appearance._PREVIEW_COLLECTION = {}
        self.timers = []
        self.modules['bpy'].app = SimpleNamespace(timers=SimpleNamespace(
            is_registered=lambda callback: callback in self.timers,
            register=lambda callback, **kwargs: self.timers.append(callback),
            unregister=lambda callback: self.timers.remove(callback)))

    def guard(self):
        original = builtins.open
        def package_open(filename, *args, **kwargs):
            if not Path(filename).resolve().is_relative_to(ROOT):
                raise AssertionError("external file: " + str(filename))
            return original(filename, *args, **kwargs)
        return patch("builtins.open", side_effect=package_open)

    def test_cold_and_warm_callbacks_are_package_only(self):
        appearance = self.appearance
        scene = SimpleNamespace(arc_appearance_backpack="NONE")
        with patch.dict(sys.modules, self.modules), self.guard(), patch.object(os, "walk", side_effect=AssertionError("export walk")), patch.object(os, "listdir", side_effect=AssertionError("export listdir")), patch.object(appearance, "_resolve_csv_rel_path", side_effect=AssertionError("external resolve")), patch.object(appearance, "_icon_file_for_choice", side_effect=AssertionError("action icon resolver")), patch.object(self.addon.backpack_catalog, "_paths", side_effect=AssertionError("PAK paths")):
            for repeat in range(2):
                for callback in (appearance.make_head_enum_items, appearance.make_hair_enum_items, appearance.make_facial_hair_enum_items, appearance.make_hair_color_enum_items, appearance.make_backpack_colorway_enum_items, appearance.make_backpack_enum_items, appearance.make_attachment_enum_items, appearance.make_charm_enum_items, appearance.make_raider_tool_enum_items):
                    self.assertTrue(callback(scene, None))
                for kind in appearance._GEAR_KINDS:
                    for choice in appearance.list_gear_choices(kind):
                        appearance.preview_icon_id(choice["id"])
                        if kind == "backpack":
                            self.assertTrue(self.addon.backpack_catalog.list_colorways(choice))
                for choice in appearance.list_head_choices() + appearance._list_all_hair_choices():
                    appearance.preview_icon_id(choice["id"])
                appearance.bald_preview_icon_id()

    def test_missing_catalogs_and_previews_do_not_discover(self):
        appearance = self.appearance
        with patch.dict(sys.modules, self.modules), patch.object(os.path, "isfile", return_value=False), patch.object(os, "walk", side_effect=AssertionError("export walk")), patch.object(appearance, "_resolve_csv_rel_path", side_effect=AssertionError("external resolve")), patch.object(self.addon.backpack_catalog, "_paths", side_effect=AssertionError("PAK paths")):
            self.assertEqual(appearance.list_head_choices(), [])
            self.assertEqual(appearance._list_all_hair_choices(), [])
            self.assertEqual(appearance.list_hair_color_choices(), [])
            for kind in appearance._GEAR_KINDS:
                self.assertEqual(appearance.list_gear_choices(kind), [])
            self.assertEqual(appearance.bald_preview_icon_id(), 0)
            self.assertEqual(appearance._browse_icon_file_for_choice({"kind": "head", "folder": "missing"}), "")
            self.assertEqual(len(self.addon.backpack_catalog.list_colorways({"folder": "missing"})), 1)

    def test_action_resolves_relative_csv_path(self):
        choice = self.appearance.list_head_choices()[0]
        with patch.object(self.appearance, "_resolve_csv_rel_path", return_value="fixture.psk") as resolve, patch.object(os.path, "isfile", return_value=True):
            self.assertEqual(self.appearance.resolve_choice_path(choice["id"]), "fixture.psk")
        resolve.assert_called_once_with(choice["rel_path"])

    def test_packaged_localization_matches_export_names(self):
        choices = self.appearance.list_gear_choices("backpack")
        salvager = next(choice for choice in choices if choice["folder"] == "ArcPack")
        self.assertEqual(salvager["label"], "Salvager")

    def test_action_mesh_miss_reaches_extraction(self):
        pak = self.modules["appearance_test_addon.Shared.pak_extract"]
        pak.configured_dir = Mock(return_value="fixture-paks")
        pak.resolve_usmap = Mock(return_value="fixture.usmap")
        pak.ensure_rels = Mock(return_value=["extracted.psk"])
        with patch.dict(sys.modules, self.modules), patch.object(self.appearance, "_cached_mesh_from_rel", return_value=""), patch.object(self.appearance, "_mesh_file_usable", side_effect=lambda value: value == "extracted.psk"):
            self.assertEqual(self.appearance.locate_appearance_mesh("Characters/Heads/fixture.psk"), "extracted.psk")
        pak.ensure_rels.assert_called_once_with(self.modules["bpy"].context.scene, ["Characters/Heads/fixture.psk"])

    def test_missing_outfit_preview_has_no_external_fallback(self):
        importing = self.modules["appearance_test_addon.Shared.importing"]
        importing.get_outfit_csv_path = Mock(return_value="packaged.csv")
        importing.load_outfit_csv = Mock(return_value=[{}])
        importing.find_outfit_row = Mock(return_value={"outfit": "fixture"})
        context = SimpleNamespace(scene=SimpleNamespace(arc_selected_outfit="fixture"))
        with patch.dict(sys.modules, self.modules), patch.object(self.appearance, "_outfit_preview_stems", return_value=["fixture"]), patch.object(self.appearance, "_shipped_outfit_preview_path", return_value=""), patch.object(self.appearance, "_resolve_csv_rel_path", side_effect=AssertionError("external outfit icon")):
            self.assertEqual(self.appearance.outfit_preview_icon_id(context), 0)

    def test_packaged_colorways_and_icon_paths(self):
        manifest = self.appearance._appearance_browse_manifest()
        self.assertEqual(manifest["schema"], 1)
        choices = self.appearance.list_gear_choices("backpack")
        with patch.dict(sys.modules, self.modules):
            for choice in choices:
                variants = self.addon.backpack_catalog.list_colorways(choice)
                self.assertEqual(variants[0]["id"], "BASE")
                for variant in variants[1:]:
                    self.assertTrue(variant["package"].startswith("PioneerGame/Content/Pioneer/Items/Characters/Skins/BackpackContainer/"))
                variants[0]["label"] = "mutation"
                self.assertEqual(self.addon.backpack_catalog.list_colorways(choice)[0]["label"], "Game Default")
        for filename in manifest["icons"].values():
            self.assertTrue(self.appearance._packaged_appearance_preview(filename))
        for filename in ("../outside.png", "..\\outside.png", "X:/outside.png"):
            self.assertEqual(self.appearance._packaged_appearance_preview(filename), "")

    def test_hair_palette_action_retains_export_resolution(self):
        choice = self.appearance.list_hair_color_choices()[0]
        pak = self.modules["appearance_test_addon.Shared.pak_extract"]
        pak.configured_dir = Mock(return_value="")
        with patch.dict(sys.modules, self.modules), patch.object(self.appearance, "_resolve_csv_rel_path", return_value="palette.json") as resolve, patch.object(os.path, "isfile", return_value=True):
            self.assertEqual(self.appearance.resolve_hair_color_path(choice["id"]), "palette.json")
        resolve.assert_called_once_with(choice["rel_path"])

    def test_action_reference_icon_retains_source_resolution(self):
        choice = self.appearance.list_head_choices()[0]
        with patch.object(self.appearance, "_resolve_csv_rel_path", return_value="source-art.png") as resolve:
            self.assertEqual(self.appearance._icon_file_for_choice(choice), "source-art.png")
        self.assertTrue(resolve.called)
        self.appearance._ICON_SCAN_CACHE.clear()

    def test_preview_loader_uses_only_packaged_art_and_reuses_it(self):
        choice_id = next(iter(self.appearance._appearance_browse_manifest()["icons"]))
        class Previews(dict):
            def load(self, key, path, kind):
                self[key] = SimpleNamespace(icon_id=37)
                return self[key]
        previews = Previews()
        with patch.object(self.appearance, "_PREVIEW_COLLECTION", previews), patch.object(self.appearance, "_crop_sidebar_preview") as crop, patch.object(self.appearance, "_icon_file_for_choice", side_effect=AssertionError("action icon resolver")):
            with patch.dict(sys.modules, self.modules):
                self.assertEqual(self.appearance.preview_icon_id(choice_id), 0)
                self.assertEqual(self.appearance.preview_icon_id(choice_id), 0)
                self.assertEqual(len(previews), 0)
                crop.assert_not_called()
                self.assertEqual(len(self.timers), 1)
                self.assertIsNone(self.appearance._prepare_sidebar_previews())
            self.assertEqual(self.appearance.preview_icon_id(choice_id), 37)
        self.assertEqual(len(previews), 1)
        crop.assert_called_once()

    def test_preview_timer_processes_only_one_image_per_tick(self):
        class Previews(dict):
            def load(self, key, path, kind):
                self[key] = SimpleNamespace(icon_id=37)
                return self[key]
        previews = Previews()
        with patch.dict(sys.modules, self.modules), patch.object(self.appearance, '_PREVIEW_COLLECTION', previews), patch.object(self.appearance, '_crop_sidebar_preview'), patch.object(os.path, 'isfile', return_value=True):
            self.appearance._preview_icon_id_for_path('first.png')
            self.appearance._preview_icon_id_for_path('second.png')
            self.assertEqual(len(previews), 0)
            self.assertGreater(self.appearance._prepare_sidebar_previews(), 0)
            self.assertEqual(len(previews), 1)
            self.assertIsNone(self.appearance._prepare_sidebar_previews())
            self.assertEqual(len(previews), 2)

    def test_failed_preview_is_not_requeued_and_unregister_cancels_work(self):
        class Previews(dict):
            def load(self, key, path, kind):
                raise RuntimeError('unreadable preview')
        with patch.dict(sys.modules, self.modules), patch.object(self.appearance, '_PREVIEW_COLLECTION', Previews()), patch.object(os.path, 'isfile', return_value=True):
            self.appearance._preview_icon_id_for_path('broken.png')
            self.assertIsNone(self.appearance._prepare_sidebar_previews())
            self.assertEqual(self.appearance._preview_icon_id_for_path('broken.png'), 0)
            self.assertFalse(self.appearance._PREVIEW_PENDING)
            self.appearance._preview_icon_id_for_path('pending.png')
            self.assertTrue(self.appearance._PREVIEW_PENDING)
            self.appearance.unregister_previews()
            self.assertFalse(self.appearance._PREVIEW_PENDING)
            self.assertFalse(self.appearance._PREVIEW_FAILED)
            self.assertNotIn(self.appearance._prepare_sidebar_previews, self.timers)

    def test_missing_manifest_is_an_empty_package_gap(self):
        original = builtins.open
        def without_manifest(filename, *args, **kwargs):
            if Path(filename).name == "appearance_browse.json":
                raise FileNotFoundError(filename)
            return original(filename, *args, **kwargs)
        with patch.dict(sys.modules, self.modules), patch("builtins.open", side_effect=without_manifest), patch.object(self.appearance, "_resolve_csv_rel_path", side_effect=AssertionError("external resolution")), patch.object(self.addon.backpack_catalog, "_paths", side_effect=AssertionError("PAK discovery")):
            choices = self.appearance.list_gear_choices("backpack")
            self.assertTrue(choices)
            self.assertEqual(self.appearance.preview_icon_id(choices[0]["id"]), 0)
            self.assertEqual(len(self.addon.backpack_catalog.list_colorways(choices[0])), 1)
            self.assertEqual(self.appearance._appearance_browse_manifest(), {})

    def test_backpack_plan_still_queries_action_provider(self):
        with patch.object(self.addon.backpack_catalog, "_index", return_value={"paths": []}) as index:
            self.assertEqual(self.addon.backpack_catalog.plan({"folder": "Fixture"}), [])
        index.assert_called_once()

    def test_csv_identity_is_preserved(self):
        with (ROOT / 'Data/appearance_reference.csv').open(encoding="utf-8-sig") as stream:
            rows = {row["id"]: row for row in csv.DictReader(stream)}
        choices = self.appearance.list_head_choices() + self.appearance._list_all_hair_choices() + self.appearance.list_hair_color_choices()
        for kind in self.appearance._GEAR_KINDS:
            choices += self.appearance.list_gear_choices(kind)
        self.assertTrue(choices)
        for choice in choices:
            self.assertEqual(choice["rel_path"], rows[choice["id"]]["rel_path"])
            self.assertEqual(choice["key"], rows[choice["id"]]["key"])


if __name__ == "__main__":
    unittest.main()
