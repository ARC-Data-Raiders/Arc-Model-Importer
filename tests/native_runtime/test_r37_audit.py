"""R37: blown_out checker threshold (lit-albedo ceiling) and lightbulb-master routing ahead of the _Light_ name rule."""
import ast
import importlib.util
import re
import unittest
from pathlib import Path

MAT = Path(__file__).resolve().parents[2] / "Shared" / "materials"


def _load_audit():
    import sys
    if str(MAT) not in sys.path:
        sys.path.insert(0, str(MAT))  # material_audit imports audit_param_rules flat, as r21_reclass loads it
    spec = importlib.util.spec_from_file_location("r37_material_audit", MAT / "material_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Stub:
    def __getattr__(self, name):
        if name.startswith("FAMILY_"):
            return name.lower()
        return lambda *a, **k: False


def _classify_ns():
    """Exec classify_mi_family and the routing helpers from classify.py; every other predicate is a False stub."""
    src = (MAT / "classify.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    keep = {"_mi_tex_params", "_is_lightbulb_master_mi", "_is_weapon_emissive_light_mi", "classify_mi_family"}
    consts = {"LIGHTBULB_MASTERS", "_FIREARM_LIGHT_FALSE_POSITIVE"}
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in keep:
            body.append(node)
        elif isinstance(node, ast.Assign) and any(getattr(t, "id", "") in consts or getattr(t, "id", "").startswith("FAMILY_")
                                                   for t in node.targets):
            body.append(node)
    for fn in body:
        if isinstance(fn, ast.FunctionDef) and fn.name == "classify_mi_family":
            fn.body = [s for s in fn.body if not isinstance(s, ast.ImportFrom)]
    stub = _Stub()

    class NS(dict):
        def __missing__(self, key):
            if key in __builtins__ if isinstance(__builtins__, dict) else hasattr(__builtins__, key):
                raise KeyError(key)
            return getattr(stub, key)

    ns = NS(re=re)
    exec(compile(ast.Module(body=body, type_ignores=[]), "classify_slice", "exec"), ns)
    return ns


class BlownOut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.MA = _load_audit()

    def test_seeded_x50_emissive_flags(self):
        hot = self.MA.blown_fraction([(50.0, 50.0, 50.0, 1.0)] * 9 + [(0.2, 0.2, 0.2, 1.0)])
        self.assertGreater(hot, 0.25)
        self.assertIn("blown_out", self.MA.render_stats_flags({"coverage_px": 10, "blown": 0.9, "hot": hot}))

    def test_white_albedo_lit_not_blown(self):
        lit = 0.9 * (3.0 + 0.5)  # albedo 0.9 under sun 3 + world 0.5 = 3.15 linear
        hot = self.MA.blown_fraction([(lit, lit, lit, 1.0)] * 10)
        self.assertEqual(hot, 0.0)
        fl = self.MA.render_stats_flags({"coverage_px": 10, "blown": 1.0, "hot": hot})
        self.assertNotIn("blown_out", fl)

    def test_legacy_blown_is_info(self):
        fl = self.MA.render_stats_flags({"coverage_px": 10, "blown": 0.8})
        self.assertNotIn("blown_out", fl)
        self.assertIn("bright_clipped", fl)

    def test_alpha_masked_pixels_ignored(self):
        self.assertEqual(self.MA.blown_fraction([(50.0, 50.0, 50.0, 0.0), (0.1, 0.1, 0.1, 1.0)]), 0.0)

    def test_no_coverage_no_flags(self):
        self.assertEqual(self.MA.render_stats_flags({"blown": 1.0, "hot": 1.0}), {})


class LightbulbRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = _classify_ns()

    def test_lightbulb_master_light_stem_not_emissive(self):
        mi = {"parent": "/Game/Pioneer/Lighting/Materials/Lightfixtures/M_MasterLight_Lightbulb_01.M_MasterLight_Lightbulb_01",
              "textures": [("Lightbulb", "T_a"), ("GlassTexture", "T_b")]}
        self.assertNotEqual(self.ns["classify_mi_family"](mi, "mi_frigate_01_ext_light_a"), self.ns["FAMILY_EMISSIVE"])

    def test_lightbulb_params_without_parent_not_emissive(self):
        mi = {"parent": "", "textures": [("Lightbulb", "T_a"), ("GlassTexture", "T_b")]}
        self.assertNotEqual(self.ns["classify_mi_family"](mi, "mi_lamp_light_01"), self.ns["FAMILY_EMISSIVE"])

    def test_weapon_emissive_light_still_emissive(self):
        mi = {"parent": "/Game/Weapons/M_WeaponPreset_Emissive.M_WeaponPreset_Emissive", "textures": [("CR", "T_a")]}
        self.assertEqual(self.ns["classify_mi_family"](mi, "mi_weapon_emissive_light_01"), self.ns["FAMILY_EMISSIVE"])


if __name__ == "__main__":
    unittest.main()
