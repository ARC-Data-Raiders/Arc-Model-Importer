"""Explicit authored CRT/other slots must not be reclassified by lens aliases."""
import ast
import json
import os
from pathlib import Path
import tempfile
import types
import unittest

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "materials" / "clothing.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))

def load(name, namespace):
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace[name]

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.crt = str(Path(self.temp.name) / "MI_CRT_Screen_Blue.json")
        self.glass = str(Path(self.temp.name) / "MI_StationMaster_HelmetVisor_Glass.json")
        self.simple = str(Path(self.temp.name) / "M_SimplePBR.json")
        for path in (self.crt, self.glass, self.simple):
            Path(path).write_text("{}")
        records = {
            self.crt: {"parent": "Material'M_CRT_Screen'", "found": True},
            self.glass: {"parent": "Material'M_Character_Glass_Opaque'", "found": True},
            self.simple: {"parent": "Material'M_SimplePBR'", "found": True},
        }
        self.ns = {"os": os, "_parse_visor_mi": lambda p: records.get(p, {}),
                   "_is_simple_pbr_material_name": lambda n: "m_simplepbr" in n.lower(),
                   "_is_visor_shell_slot_name": lambda n: False,
                   "_name_has_glass_lens_token": lambda n: "glass" in n.lower(),
                   "_should_route_simple_pbr_to_visor": lambda names, path: any("simplepbr" in n.lower() for n in names),
                   "_names_suggest_visor_glass_slot": lambda names: any("visor" in n.lower() for n in names)}
        load("_is_visor_glass_mi", self.ns)
        load("_explicit_nonvisor_mi", self.ns)
        self.route = load("_slot_is_visor_lens", self.ns)

    def test_explicit_crt_beats_glass_alias_and_visor_folder(self):
        self.assertFalse(self.route(("Helmet_Visor", "HelmetVisor_Glass"), "/StationMaster/HelmetVisor/SK_Visor.uemodel", self.crt))

    def test_real_glass_and_legacy_simple_placeholder_still_route(self):
        self.assertTrue(self.route(("HelmetVisor_Glass",), "", self.glass))
        self.assertTrue(self.route(("M_SimplePBR",), "/Helmet/SK_Helmet.psk", self.simple))

    def test_unreadable_record_does_not_assert_a_nonvisor_family(self):
        self.assertFalse(self.ns["_explicit_nonvisor_mi"]("missing.json"))
        self.assertTrue(self.route(("HelmetVisor_Glass",), "", "missing.json"))

    def test_zero_applied_cannot_trigger_neighbor_glass_whole_mesh_fallback(self):
        self.ns.update({"apply_embedded_visor_slots": lambda *a, **kw: 0,
                        "_native_clothing_sections": lambda p: [{"index": 0}],
                        "_native_clothing_section_paths": lambda *a: {0: self.crt},
                        "_resolve_visor_glass_mi_path": lambda *a, **kw: self.fail("Neighbor glass resolver reached")})
        setup = load("setup_visor_material", self.ns)
        self.assertEqual(setup(object(), "/StationMaster/HelmetVisor/model.uemodel"), 0)

    def test_native_order_and_bindings_override_stale_sk_and_slot_names(self):
        slots = [types.SimpleNamespace(material=types.SimpleNamespace(name="HelmetVisor_Glass")),
                 types.SimpleNamespace(material=types.SimpleNamespace(name="CRT_StaleName"))]
        obj = types.SimpleNamespace(material_slots=slots, name="StationMaster")
        native = [{"index": 0, "slot": "Helmet_Visor", "reference": "/Game/CRT.MI_CRT_Screen_Blue"},
                  {"index": 1, "slot": "Glass", "reference": "/Game/Lens.MI_Glass"}]
        applied = []
        self.ns.update({"_parse_sk_material_slots": lambda p: [("Glass", "MI_Glass", self.glass)],
            "_native_clothing_sections": lambda p: native,
            "_native_clothing_section_paths": lambda *a: {0: self.crt, 1: self.glass},
            "_ensure_clothing_sk_slot_count": lambda *a: None,
            "_find_glass_mi_in_dir": lambda *a: self.glass,
            "_glass_slot_prefers_arc_override": lambda *a, **kw: False,
            "_find_clothing_shell_slot_index": lambda *a: -1,
            "_ensure_unique_visor_slot_material": lambda *a, **kw: None,
            "_apply_visor_manual_fallback": lambda *a, **kw: applied.append(kw["mi_path"]),
            "_match_material_slot": lambda *a: self.fail("Fuzzy slot matching used for native sections")})
        hook = load("apply_embedded_visor_slots", self.ns)
        self.assertEqual(hook(obj, "/StationMaster/SK_HelmetVisor.uemodel"), 1)
        self.assertEqual(applied, [self.glass])
        self.assertEqual(slots[0].material.name, "HelmetVisor_Glass")

class FamilyConsistencyTests(unittest.TestCase):
    def test_matching_identity_does_not_preserve_wrong_layered_crt_or_glass(self):
        tree = ast.parse((REPO / 'materials' / 'dispatch.py').read_text(encoding='utf-8-sig'))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_dedicated_outfit_material_current')
        namespace = {'_active_dedicated_outfit_profile': lambda mat: 'layered',
                     '_mi_uses_layered_character_profile': lambda path: path == 'layered.json',
                     '_material_identity_path': lambda path: path}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'dispatch.py', 'exec'), namespace)
        current = namespace[function.name]
        self.assertFalse(current({'arc_mi_path': 'crt.json'}, 'crt.json'))
        self.assertFalse(current({'arc_mi_path': 'glass.json'}, 'glass.json'))
        self.assertTrue(current({'arc_mi_path': 'layered.json'}, 'layered.json'))
        self.assertFalse(current({'arc_mi_path': 'layered.json'}, 'different-layered.json'))

if __name__ == '__main__':
    unittest.main()
