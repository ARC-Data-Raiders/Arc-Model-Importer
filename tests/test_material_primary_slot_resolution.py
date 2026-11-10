"""Exact source bindings must precede optional external material discovery."""
from section_fixture import add_parents
import ast
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_material_primary_slots"


class PrimaryMaterialSlotTests(unittest.TestCase):
    def setUp(self):
        package = ModuleType(PACKAGE)
        package.__path__ = []
        sys.modules[PACKAGE] = package
        self.addCleanup(self.remove_modules)
        self.fmdex = ModuleType(PACKAGE + ".fmdex")
        self.fmdex.lookup_asset_path = Mock(side_effect=AssertionError("Primary hit visited FMDex"))
        self.fmdex.package_to_game_path = Mock(return_value="/Game/Fallback/MI_Exact")
        self.fmdex.resolve_export_file = Mock(return_value="")
        self.fmdex.fmdex_summary_for_report = Mock(return_value="unloaded")
        self.textures = ModuleType(PACKAGE + ".Shared.textures")
        self.textures.is_hair_cards_mi = Mock(return_value=False)
        self.textures.is_hair = Mock(return_value=False)
        self.textures.find_asset_from_object_path = Mock(return_value="/primary/MI_Exact.json")
        sys.modules[self.fmdex.__name__] = self.fmdex
        sys.modules[self.textures.__name__] = self.textures
        add_parents(sys.modules, PACKAGE)
        tree = ast.parse((ROOT / "Shared/materials/dispatch.py").read_text(encoding="utf-8-sig"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "fix_object_materials_from_mi_slots")
        self.material = object()
        self.namespace = {"__package__": PACKAGE + ".Shared.materials", "CTX_MAP": "map", "os": os, "utils": SimpleNamespace(get_logger=Mock(return_value=Mock())), "_active_dedicated_outfit_profile": Mock(return_value=""), "_mi_stem_from_blender_name": Mock(return_value="MI_Exact"), "_uemodel_section_materials": Mock(return_value=["/Game/Source/MI_Exact.MI_Exact"]), "path_allowed_for_context": Mock(return_value=True), "material_has_map_forbidden_content": Mock(return_value=False), "_resolve_mi_json_path": Mock(return_value=""), "_get_or_build_shared_mi_material": Mock(return_value=self.material)}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "Shared/materials/dispatch.py", "exec"), self.namespace)
        self.obj = type("MeshObject", (dict,), {})(arc_source_mesh="/primary/mesh.uemodel")
        self.obj.type = "MESH"
        self.obj.name = "Source mesh"
        self.slot = SimpleNamespace(material=SimpleNamespace(name="MI_Exact", get=lambda key, default="": default))
        self.obj.material_slots = [self.slot]

    def remove_modules(self):
        for name in list(sys.modules):
            if name == PACKAGE or name.startswith(PACKAGE + "."):
                del sys.modules[name]

    def run_fix(self):
        return self.namespace["fix_object_materials_from_mi_slots"](self.obj, context="enemy")

    def test_exact_primary_binding_never_visits_fmdex(self):
        self.assertEqual(self.run_fix(), 1)
        self.fmdex.lookup_asset_path.assert_not_called()
        self.namespace["_resolve_mi_json_path"].assert_not_called()
        self.textures.find_asset_from_object_path.assert_called_once_with("/Game/Source/MI_Exact.MI_Exact", ".json")
        self.assertIs(self.slot.material, self.material)

    def test_missing_primary_binding_keeps_fmdex_fallback(self):
        self.fmdex.lookup_asset_path.side_effect = None
        self.fmdex.lookup_asset_path.return_value = ("fallback", ["MaterialInstanceConstant"])
        self.textures.find_asset_from_object_path.side_effect = ["", "/fallback/MI_Exact.json"]
        self.assertEqual(self.run_fix(), 1)
        self.fmdex.lookup_asset_path.assert_called_once_with("MI_Exact")
        self.assertEqual(self.textures.find_asset_from_object_path.call_args_list[0].args[0], "/Game/Source/MI_Exact.MI_Exact")
        self.assertIs(self.slot.material, self.material)

    def test_mismatched_source_slot_does_not_override_the_material(self):
        self.namespace["_uemodel_section_materials"].return_value = ["/Game/Source/MI_Other"]
        self.fmdex.lookup_asset_path.side_effect = None
        self.fmdex.lookup_asset_path.return_value = ("", [])
        self.namespace["_resolve_mi_json_path"].return_value = "/local/MI_Exact.json"
        self.assertEqual(self.run_fix(), 1)
        self.textures.find_asset_from_object_path.assert_not_called()
        self.assertEqual(self.namespace["_get_or_build_shared_mi_material"].call_args.args[1], "/local/MI_Exact.json")


if __name__ == "__main__":
    unittest.main()
