"""Mesh slot parsing should discover backups only after local metadata fails."""
import ast
from collections.abc import Iterator
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_mesh_material_paths_lazy"


class LazyMeshMaterialPathsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.mesh = self.root / "primary" / "SK_Test.uemodel"
        self.mesh.parent.mkdir()
        self.mesh.touch()
        self.fmdex = ModuleType(PACKAGE + ".fmdex")
        self.fmdex.resolve_export_file = Mock(return_value="")
        package = ModuleType(PACKAGE)
        package.__path__ = []
        sys.modules[PACKAGE] = package
        sys.modules[self.fmdex.__name__] = self.fmdex
        self.addCleanup(sys.modules.pop, self.fmdex.__name__, None)
        self.addCleanup(sys.modules.pop, PACKAGE, None)
        self.utils = SimpleNamespace(
            remap_path_into_content_dirs=Mock(return_value=[]),
            ue_export_entries=lambda data: data if isinstance(data, list) else [data],
            get_logger=Mock(return_value=Mock()),
        )
        self.namespace = {
            "__package__": PACKAGE + ".Shared.materials",
            "os": os, "re": re, "json": json,
            "Iterator": Iterator,
            "bpy": SimpleNamespace(path=SimpleNamespace(abspath=lambda path: path)),
            "utils": self.utils,
            "textures": SimpleNamespace(mi_stem_from_material_ref=lambda reference: reference.get("ObjectPath", "").rsplit("/", 1)[-1].split(".")[0]),
            "CTX_ANY": "any", "CTX_MAP": "map",
            "_SK_SLOTS_CACHE": {},
            "path_allowed_for_context": lambda path, context: context != "map" or "characters" not in str(path).lower(),
            "_resolve_mi_json_path": Mock(return_value="/primary/MI_Test.json"),
        }
        functions = {
            "_norm_path_key", "_effective_sm_slot_name",
            "_mesh_stem_variants", "_mesh_json_matches_requested_stem",
            "_mesh_material_json_paths", "_parse_sk_material_slots",
        }
        tree = ast.parse((ROOT / "Shared/materials/common.py").read_text(encoding="utf-8-sig"))
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in functions]
        exec(compile(ast.Module(body=selected, type_ignores=[]), "Shared/materials/common.py", "exec"), self.namespace)

    def write_slots(self, path, *, name="SK_Test", slots=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        if slots is None:
            slots = [{"MaterialSlotName": "Body", "MaterialInterface": {"ObjectPath": "/Game/Primary/MI_Test.MI_Test"}}]
        path.write_text(json.dumps([{"Type": "SkeletalMesh", "Name": name, "Properties": {"SkeletalMaterials": slots}}]), encoding="utf-8")
        return path

    def parse(self, *, context="any"):
        return self.namespace["_parse_sk_material_slots"](str(self.mesh), context=context)

    def assert_primary_only(self):
        self.assertEqual(self.parse(), [("Body", "MI_Test", "/primary/MI_Test.json")])
        self.utils.remap_path_into_content_dirs.assert_not_called()
        self.fmdex.resolve_export_file.assert_not_called()

    def test_sibling_table_does_not_discover_backups(self):
        self.write_slots(self.mesh.with_suffix(".json"))
        self.assert_primary_only()

    def test_nested_table_does_not_discover_backups(self):
        self.write_slots(self.mesh.parent / self.mesh.stem / (self.mesh.stem + ".json"))
        self.assert_primary_only()

    def test_cross_prefix_sibling_does_not_discover_backups(self):
        self.write_slots(self.mesh.with_suffix(".json"), slots=[])
        self.write_slots(self.mesh.parent / "SM_Test.json", name="SM_Test")
        self.assert_primary_only()

    def test_bake_hash_keeps_base_sibling_before_backups(self):
        self.mesh = self.mesh.with_name("SK_Test-BA270E.uemodel")
        self.write_slots(self.mesh.with_name("SK_Test.json"))
        self.assert_primary_only()

    def test_invalid_sibling_still_uses_remapped_table(self):
        self.write_slots(self.mesh.with_suffix(".json"), name="SK_Wrong")
        alternate = self.root / "remapped" / self.mesh.name
        self.write_slots(alternate.with_suffix(".json"))
        self.utils.remap_path_into_content_dirs.return_value = [str(alternate)]
        self.assertEqual(self.parse()[0][1], "MI_Test")
        self.utils.remap_path_into_content_dirs.assert_called_once()
        self.fmdex.resolve_export_file.assert_not_called()

    def test_unusable_local_tables_preserve_fmdex_fallback(self):
        for state in ("missing", "corrupt", "empty", "wrong_name"):
            with self.subTest(state=state):
                sibling = self.mesh.with_suffix(".json")
                sibling.unlink(missing_ok=True)
                self.namespace["_SK_SLOTS_CACHE"].clear()
                if state == "corrupt":
                    sibling.write_text("invalid json", encoding="utf-8")
                elif state == "empty":
                    self.write_slots(sibling, slots=[])
                elif state == "wrong_name":
                    self.write_slots(sibling, name="SK_Wrong")
                fallback = self.write_slots(self.root / "fallback" / "SK_Test.json")
                self.fmdex.resolve_export_file.reset_mock()
                self.fmdex.resolve_export_file.return_value = str(fallback)
                self.assertEqual(self.parse()[0][1], "MI_Test")
                self.fmdex.resolve_export_file.assert_called_once_with("SK_Test", ".json", context="map", allow_basename_walk=False)

    def test_rejected_map_context_keeps_fallback(self):
        self.mesh = self.root / "Characters" / "SK_Test.uemodel"
        self.write_slots(self.mesh.with_suffix(".json"))
        fallback = self.write_slots(self.root / "Environment" / "SK_Test.json")
        self.fmdex.resolve_export_file.return_value = str(fallback)
        self.assertEqual(self.parse(context="map")[0][1], "MI_Test")
        self.fmdex.resolve_export_file.assert_called_once()

    def test_slot_order_and_placeholders_are_preserved(self):
        self.write_slots(self.mesh.with_suffix(".json"), slots=[
            {"MaterialSlotName": "Material_0", "ImportedMaterialSlotName": "Authored", "MaterialInterface": {"ObjectPath": "/Game/Primary/MI_Test.MI_Test"}},
            {"MaterialSlotName": "Missing"},
            {"MaterialSlotName": "Engine", "MaterialInterface": {"ObjectPath": "/Engine/WorldGridMaterial.WorldGridMaterial"}},
        ])
        self.assertEqual(self.parse(), [("Authored", "MI_Test", "/primary/MI_Test.json"), ("Missing", "", ""), ("Engine", "", "")])
        self.fmdex.resolve_export_file.assert_not_called()

    def test_empty_source_and_missing_tables_remain_empty(self):
        self.assertEqual(self.namespace["_parse_sk_material_slots"](""), [])
        self.assertEqual(self.parse(), [])
        self.assertTrue(self.fmdex.resolve_export_file.called)
        self.fmdex.resolve_export_file.reset_mock()
        self.assertEqual(self.parse(), [])
        self.fmdex.resolve_export_file.assert_not_called()

    def test_local_only_miss_does_not_poison_fallback_cache(self):
        parse = self.namespace["_parse_sk_material_slots"]
        self.assertEqual(parse(str(self.mesh), allow_fallback=False), [])
        self.utils.remap_path_into_content_dirs.assert_not_called()
        self.fmdex.resolve_export_file.assert_not_called()
        fallback = self.write_slots(self.root / "fallback" / "SK_Test.json")
        self.fmdex.resolve_export_file.return_value = str(fallback)
        self.assertEqual(self.parse()[0][1], "MI_Test")
        self.fmdex.resolve_export_file.assert_called_once()


if __name__ == "__main__":
    unittest.main()
