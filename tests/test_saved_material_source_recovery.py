"""Pure exact saved-source migration checks; no Blender process required.

Run with --output <scratch/result.json>; temporary fixtures stay beside output.
"""
import ast
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest

from section_fixture import add_parents

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_saved_source_regression"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(PACKAGE + ".Outfit_Accessories.pak_catalog", ROOT / "Outfit_Accessories/pak_catalog.py")
catalog = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = catalog
spec.loader.exec_module(catalog)
namespace = {"__package__": PACKAGE + ".Shared", "os": os}
source = ast.parse((ROOT / "Shared/pak_extract.py").read_text(encoding="utf-8"))
functions = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in
             {"_normalize_asset_path", "_package_aliases", "saved_material_package", "recover_saved_material_source"}]
exec(compile(ast.Module(body=functions, type_ignores=[]), str(ROOT / "Shared/pak_extract.py"), "exec"), namespace)
recover = namespace["recover_saved_material_source"]
extractor = types.ModuleType(PACKAGE + ".Shared.pak_extract")
extractor.recover_saved_material_source = recover
extractor.saved_material_package = namespace["saved_material_package"]
extractor.configured_dir = lambda scene: ""
extractor._addon_dir = lambda: str(ROOT)
sys.modules[extractor.__name__] = extractor
add_parents(sys.modules, PACKAGE)
operator_namespace = {"__package__": PACKAGE + ".Shared", "os": os,
                      "bpy": types.SimpleNamespace(context=types.SimpleNamespace(scene=None)),
                      "utils": types.SimpleNamespace(_pak_cache_roots=[]),
                      "log": types.SimpleNamespace(info=lambda *args: None, warning=lambda *args: None, exception=lambda *args: None)}
operator_ast = ast.parse((ROOT / "Shared/operators.py").read_text(encoding="utf-8"))
operator_function = next(node for node in operator_ast.body if isinstance(node, ast.FunctionDef) and
                         node.name == "_recover_saved_material_lookup")
exec(compile(ast.Module(body=[operator_function], type_ignores=[]), str(ROOT / "Shared/operators.py"), "exec"), operator_namespace)
recover_lookup = operator_namespace["_recover_saved_material_lookup"]
SCRATCH_PARENT = None

class SavedSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="saved-source-", dir=SCRATCH_PARENT)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.current = self.base / "current"
        self.old = self.base / "old"
        self.tail = "Pioneer/Items/Characters/Backpack/Trunk/SK_Trunk"
        self.source = str(self.old / "PioneerGame/Content" / (self.tail + ".psk"))

    def put(self, root, mount="RemappedPlugins", ext=".psk", tail=None):
        path = root / mount / "Content" / ((tail or self.tail) + ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
        return path

    def test_cache_move_and_mount_alias(self):
        target = self.put(self.current)
        hit = recover(self.source, [self.current])
        self.assertEqual(hit["path"], str(target))
        self.assertTrue(hit["same_format"])
        self.assertEqual(hit["original"], self.source)

    def test_existing_stale_directory_does_not_block_current(self):
        self.put(self.old, "PioneerGame")
        target = self.put(self.current)
        self.assertEqual(recover(self.source, [self.current, self.old])["path"], str(target))

    def test_same_basename_different_package_is_rejected(self):
        self.put(self.current, tail="Pioneer/Unrelated/SK_Trunk")
        self.assertEqual(recover(self.source, [self.current]), {})

    def test_unregistered_root_is_not_searched(self):
        self.put(self.current)
        self.assertEqual(recover(self.source, [self.base / "unrelated"]), {})

    def test_fmodel_psk_does_not_change_to_uemodel(self):
        self.put(self.current, ext=".uemodel")
        self.assertEqual(recover(self.source, [self.current], native_pak=False), {})

    def test_native_uemodel_fallback_retains_psk_lookup_semantics(self):
        target = self.put(self.current, ext=".uemodel")
        hit = recover(self.source, [self.current], native_pak=True)
        self.assertFalse(hit["same_format"])
        self.assertEqual(hit["actual_path"], str(target))
        self.assertEqual(hit["path"], str(target.with_suffix(".psk")))

    def test_same_format_in_registered_root_beats_native_fallback(self):
        self.put(self.current, ext=".uemodel")
        target = self.put(self.old)
        self.assertEqual(recover(self.source, [self.current, self.old], native_pak=True)["actual_path"], str(target))

    def test_nongame_and_traversal_paths_rejected(self):
        self.put(self.current)
        for source in (str(self.base / "SK_Trunk.psk"), "/other/SK_Trunk.psk",
                       str(self.base / "unrelated/Content" / (self.tail + ".psk")),
                       str(self.old / "PioneerGame/Content/Pioneer/../" / (self.tail + ".psk"))):
            self.assertEqual(recover(source, [self.current], native_pak=True), {})

    def test_native_uemodel_same_format_preserved(self):
        target = self.put(self.current, ext=".uemodel")
        hit = recover(str(Path(self.source).with_suffix(".uemodel")), [self.current])
        self.assertTrue(hit["same_format"])
        self.assertEqual(hit["path"], str(target))

    def test_sameformat_stamps_migrate_and_keep_original_provenance(self):
        target = self.put(self.current)
        obj = {"arc_psk_path": self.source, "arc_source_mesh": self.source,
               "geometry_signature": [1, 2, 3], "matrix_world": [1, 0, 0, 1]}
        operator_namespace["utils"]._pak_cache_roots = [str(self.current)]
        self.assertEqual(recover_lookup(obj, self.source), str(target))
        self.assertEqual(obj["arc_psk_path"], str(target))
        self.assertEqual(obj["arc_original_psk_path"], self.source)
        self.assertEqual(obj["arc_original_source_mesh"], self.source)
        self.assertEqual(obj["geometry_signature"], [1, 2, 3])
        self.assertEqual(obj["matrix_world"], [1, 0, 0, 1])

    def test_native_formatfallback_does_not_migrate_geometry_stamps(self):
        self.put(self.current, ext=".uemodel")
        obj = {"arc_psk_path": self.source, "arc_source_mesh": self.source, "arc_pak_package": "exact"}
        operator_namespace["utils"]._pak_cache_roots = [str(self.current)]
        found = recover_lookup(obj, self.source)
        self.assertNotEqual(found, self.source)
        self.assertTrue(found.endswith(".psk"))
        self.assertEqual(obj["arc_psk_path"], self.source)
        self.assertEqual(obj["arc_source_mesh"], self.source)
        self.assertEqual(obj["arc_material_source_path"], found)

    def test_colorway_lookup_does_not_stamp_mesh_provenance(self):
        target = self.put(self.current, ext=".json")
        obj = {"arc_psk_path": self.source}
        operator_namespace["utils"]._pak_cache_roots = [str(self.current)]
        original = str(Path(self.source).with_suffix(".json"))
        self.assertEqual(recover_lookup(obj, original, stamp_mesh=False), str(target))
        self.assertEqual(obj, {"arc_psk_path": self.source})

    def test_missing_native_package_is_explicit_and_clears_after_exact_hit(self):
        obj = {"arc_psk_path": self.source, "arc_pak_package": "native"}
        operator_namespace["utils"]._pak_cache_roots = [str(self.current)]
        self.assertEqual(recover_lookup(obj, self.source), self.source)
        self.assertEqual(obj["arc_material_source_unresolved"], "PioneerGame/Content/" + self.tail)
        self.put(self.current)
        recover_lookup(obj, self.source)
        self.assertNotIn("arc_material_source_unresolved", obj)

    def test_missing_fmodel_retains_legacy_behavior(self):
        obj = {"arc_psk_path": self.source}
        operator_namespace["utils"]._pak_cache_roots = [str(self.current)]
        self.assertEqual(recover_lookup(obj, self.source), self.source)
        self.assertNotIn("arc_material_source_unresolved", obj)

    def test_update_missing_native_package_never_enters_fuzzy_lookup(self):
        function = next(node for node in operator_ast.body if isinstance(node, ast.FunctionDef) and
                        node.name == "fix_materials_for_object")
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(ROOT / "Shared/operators.py"), "exec"), operator_namespace)
        class Mesh(dict):
            name = "Backpack_Trunk"
            material_slots = []
        obj = Mesh(arc_psk_path=self.source, arc_pak_package="native")
        operator_namespace["_recover_saved_material_lookup"] = recover_lookup
        operator_namespace["_name_candidates_for_object"] = lambda obj: ["SK_Trunk"]
        operator_namespace["fmdex"] = types.SimpleNamespace(ensure_loaded=lambda: None, fmdex_summary_for_report=lambda: "fixture")
        operator_namespace["bpy"].path = types.SimpleNamespace(abspath=lambda path: path)
        operator_namespace["utils"]._pak_cache_roots = [str(self.current)]
        def fuzzy_forbidden(*args):
            raise AssertionError("Unresolved exact native package entered fuzzy lookup")
        operator_namespace["find_psk_for_model"] = fuzzy_forbidden
        ok, message = operator_namespace["fix_materials_for_object"](obj)
        self.assertFalse(ok)
        self.assertIn("missing exact game package", message)
        self.assertEqual(obj["arc_psk_path"], self.source)

    def apply_wrapper(self, obj, **kwargs):
        function = next(node for node in operator_ast.body if isinstance(node, ast.FunctionDef) and
                        node.name == "apply_materials_to_object")
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(ROOT / "Shared/operators.py"), "exec"), operator_namespace)
        operator_namespace["_recover_saved_material_lookup"] = recover_lookup
        operator_namespace["utils"]._pak_cache_roots = [str(self.current)]
        operator_namespace["utils"].timed = lambda label: contextlib.nullcontext()
        calls = []
        operator_namespace["_apply_materials_to_object_impl"] = lambda *args, **kw: calls.append(kw) or "fixture-built"
        result = operator_namespace["apply_materials_to_object"](obj, self.source, **kwargs)
        return result, calls

    def test_missing_native_selected_mis_stop_before_graph_changes(self):
        self.put(self.current)
        missing = str(self.old / "PioneerGame/Content/Pioneer/Items/Backpack/Skins/Blue/MI_Blue.json")
        for argument in ("skin_json", "skin_choice", "hair_mi"):
            obj = {"arc_psk_path": self.source, "arc_pak_package": "native"}
            result, calls = self.apply_wrapper(obj, **{argument: missing})
            self.assertEqual(result, "unresolved", argument)
            self.assertEqual(calls, [], argument)
            self.assertTrue(obj["arc_material_mi_unresolved"].endswith("MI_Blue"))

    def test_exact_selected_mi_recovery_clears_failure_and_preserves_variant(self):
        self.put(self.current)
        tail = "Pioneer/Items/Backpack/Skins/Blue/MI_Blue"
        target = self.put(self.current, tail=tail, ext=".json")
        original = str(self.old / "PioneerGame/Content" / (tail + ".json"))
        obj = {"arc_psk_path": self.source, "arc_pak_package": "native", "arc_material_mi_unresolved": "oldfailure"}
        result, calls = self.apply_wrapper(obj, skin_json=original, skin_choice=original)
        self.assertEqual(result, "fixture-built")
        self.assertEqual(calls[0]["skin_json"], str(target))
        self.assertEqual(calls[0]["skin_choice"], str(target))
        self.assertNotIn("arc_material_mi_unresolved", obj)

    def test_choice_literals_and_fmodel_arbitrary_paths_remain_legacy(self):
        self.put(self.current)
        for choice in ("NONE", "BASE"):
            obj = {"arc_psk_path": self.source, "arc_pak_package": "native"}
            result, calls = self.apply_wrapper(obj, skin_choice=choice)
            self.assertEqual(result, "fixture-built")
            self.assertEqual(calls[0]["skin_choice"], choice)
        obj = {"arc_psk_path": self.source}
        arbitrary = str(self.base / "user_materials/Missing.json")
        result, calls = self.apply_wrapper(obj, skin_json=arbitrary)
        self.assertEqual(result, "fixture-built")
        self.assertEqual(calls[0]["skin_json"], arbitrary)
        self.assertNotIn("arc_material_mi_unresolved", obj)

    def test_offline_valid_native_original_is_exact_final_fallback(self):
        target = self.put(self.old, "PioneerGame")
        obj = {"arc_psk_path": self.source, "arc_pak_package": "native"}
        operator_namespace["utils"]._pak_cache_roots = []
        hit = recover(self.source, [], native_pak=True)
        self.assertEqual(hit["actual_path"], str(target))
        self.assertTrue(hit["same_format"])
        self.assertEqual(recover_lookup(obj, self.source), self.source)
        self.assertNotIn("arc_material_source_unresolved", obj)

    def test_existing_original_psk_beats_native_uemodel_format_fallback(self):
        target = self.put(self.old, "PioneerGame")
        self.put(self.current, ext=".uemodel")
        hit = recover(self.source, [self.current], native_pak=True)
        self.assertEqual(hit["actual_path"], str(target))
        self.assertTrue(hit["same_format"])

    def test_stale_original_directory_without_file_still_rejects(self):
        Path(self.source).parent.mkdir(parents=True)
        obj = {"arc_psk_path": self.source, "arc_pak_package": "native"}
        operator_namespace["utils"]._pak_cache_roots = []
        self.assertEqual(recover(self.source, [], native_pak=True), {})
        self.assertEqual(recover_lookup(obj, self.source), self.source)
        self.assertEqual(obj["arc_material_source_unresolved"], "PioneerGame/Content/" + self.tail)

    def test_exact_json_can_recover_without_mesh_or_basename_scan(self):
        target = self.put(self.current, ext=".json")
        hit = recover(str(Path(self.source).with_suffix(".json")), [self.current])
        self.assertEqual(hit["path"], str(target))

if __name__ == "__main__":
    output = None
    if "--output" in sys.argv:
        position = sys.argv.index("--output")
        output = Path(sys.argv[position + 1]).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        SCRATCH_PARENT = str(output.parent)
        del sys.argv[position:position + 2]
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SavedSourceTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if output:
        output.write_text(json.dumps({"passed": result.wasSuccessful(), "tests": result.testsRun,
                                     "failures": len(result.failures), "errors": len(result.errors)}, indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)
