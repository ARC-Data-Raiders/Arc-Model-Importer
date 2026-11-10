"""Skip authored slot discovery only when no overrides consume slot names."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_weapon_slot_resolution"


class WeaponSlotResolutionTests(unittest.TestCase):
    def setUp(self):
        package = ModuleType(PACKAGE)
        package.__path__ = [str(ROOT)]
        self.addCleanup(self.remove_modules)
        sys.modules[PACKAGE] = package
        self.lod = SimpleNamespace(vertices=[1], materials=[("ExportName", "/Game/MI_Base.MI_Base", 0, 3)])
        parser = ModuleType(PACKAGE + ".Shared.uemodel_import")
        parser.parse_uemodel = Mock(return_value=("Mesh", 1, [self.lod]))
        sys.modules[parser.__name__] = parser
        spec = importlib.util.spec_from_file_location(PACKAGE + ".Weapons.weapon_assets", ROOT / "Weapons/weapon_assets.py")
        self.assets = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.assets
        spec.loader.exec_module(self.assets)

    def remove_modules(self):
        for name in list(sys.modules):
            if name == PACKAGE or name.startswith(PACKAGE + "."):
                del sys.modules[name]

    def test_unneeded_names_never_consult_sidecars_or_json(self):
        with patch.object(self.assets, "_native_slot_names", side_effect=AssertionError("Unneeded sidecar")), patch.object(self.assets, "_json_slot_names", side_effect=AssertionError("Unneeded backup")):
            self.assertEqual(self.assets.mesh_slots("mesh.uemodel", resolve_slot_names=False), [("ExportName", "/Game/MI_Base")])

    def test_default_call_keeps_authored_slot_names(self):
        with patch.object(self.assets, "_native_slot_names", return_value=["AuthoredSlot"]), patch.object(self.assets, "_json_slot_names", side_effect=AssertionError("Unneeded backup")):
            self.assertEqual(self.assets.mesh_slots("mesh.uemodel"), [("AuthoredSlot", "/Game/MI_Base")])

    def test_required_names_keep_missing_sidecar_fallback(self):
        with patch.object(self.assets, "_native_slot_names", return_value=None), patch.object(self.assets, "_json_slot_names", return_value=["JsonSlot"]) as fallback:
            self.assertEqual(self.assets.mesh_slots("mesh.uemodel", resolve_slot_names=True), [("JsonSlot", "/Game/MI_Base")])
            fallback.assert_called_once_with("mesh.uemodel", self.lod)

    def test_material_requests_names_only_for_overrides(self):
        tree = ast.parse((ROOT / "Weapons/weapon_import.py").read_text(encoding="utf-8-sig"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_material")
        materials = ModuleType(PACKAGE + ".Shared.materials")
        materials.weapon_surface = SimpleNamespace()
        materials.weapon_fur = SimpleNamespace(apply_shell_offsets=Mock())
        sys.modules[materials.__name__] = materials
        source_path = Path("mesh.uemodel")
        material_path = Path("MI_Base.json")
        assets = SimpleNamespace(pioneer_roots=Mock(return_value=[]), mesh_slots=Mock(return_value=[("AuthoredSlot", "/Game/MI_Base")]), resolve=Mock(return_value=material_path))
        namespace = {"__package__": PACKAGE + ".Weapons", "assets": assets, "bpy": SimpleNamespace(context=SimpleNamespace(scene=object())), "Path": Path, "json": json}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "Weapons/weapon_import.py", "exec"), namespace)
        for overrides in ({}, {"authoredslot": "/Game/MI_Skin"}):
            with self.subTest(overrides=overrides):
                assets.mesh_slots.reset_mock()
                obj = type("MeshObject", (dict,), {})()
                obj.data = SimpleNamespace(materials=[None], uv_layers=[])
                obj.material_slots = [SimpleNamespace(link="", material=None)]
                material = object()
                namespace["_material"](obj, source_path, overrides, cache={(str(material_path).casefold(), ()): material})
                assets.mesh_slots.assert_called_once_with(source_path, resolve_slot_names=bool(overrides))
                assets.resolve.assert_called_with(overrides.get("authoredslot", "/Game/MI_Base"), [])
                self.assertIs(obj.material_slots[0].material, material)


if __name__ == "__main__":
    unittest.main()
