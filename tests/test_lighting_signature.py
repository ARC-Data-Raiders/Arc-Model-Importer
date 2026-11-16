"""Lighting state: pending clears after Apply and returns when any option changes."""
import importlib
import json
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("DATARAIDERS_TEST_REPOSITORY", Path(__file__).resolve().parents[1]))
PKG = "_dr_probe_lighting_sig"


def _load():
    if PKG not in sys.modules:
        pkg = types.ModuleType(PKG)
        pkg.__path__ = [str(ROOT)]
        sys.modules[PKG] = pkg
    return importlib.import_module(PKG + ".Lighting.lighting_looks")


class _Scene(dict):
    """Plain scene double: attributes for options, dict for ID properties, no bl_rna."""


def _scene(map_name):
    s = _Scene()
    s.arc_lighting_map = map_name
    s.arc_lighting_scenario = "DAY"
    s.arc_lighting_exposure_comp = 0.0
    s.arc_lighting_apply_bloom = True
    s.arc_lighting_look_applied = ""
    return s


class LightingSignatureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.looks = _load()

    def _stamp(self, scene):
        scene["arc_lighting_state"] = json.dumps({
            "map": scene.arc_lighting_map, "scenario": scene.arc_lighting_scenario,
            "warnings": [], "signature": self.looks.lighting_signature(scene)})

    def test_signature_changes_with_option(self):
        s = _scene("Dam")
        before = self.looks.lighting_signature(s)
        s.arc_lighting_exposure_comp = 1.5
        self.assertNotEqual(before, self.looks.lighting_signature(s))
        s.arc_lighting_exposure_comp = 0.0
        self.assertEqual(before, self.looks.lighting_signature(s))

    def test_pending_after_apply_and_change(self):
        s = _scene("Dam")
        state = self.looks.lighting_state(s)
        self.assertFalse(state["applied"])
        self.assertTrue(state["pending"])
        self._stamp(s)
        state = self.looks.lighting_state(s)
        self.assertTrue(state["applied"])
        self.assertFalse(state["pending"])
        self.assertEqual(state["applied_map"], "Dam")
        s.arc_lighting_apply_bloom = False
        self.assertTrue(self.looks.lighting_state(s)["pending"])

    def test_no_map_never_pending(self):
        s = _scene("")
        self.assertFalse(self.looks.lighting_state(s)["pending"])
        s.arc_lighting_map = self.looks.cat.NONE
        self.assertFalse(self.looks.lighting_state(s)["pending"])

    def test_warnings_exposed_after_apply(self):
        s = _scene("Dam")
        s["arc_lighting_state"] = json.dumps({
            "map": "Dam", "scenario": s.arc_lighting_scenario, "warnings": ["cloud texture missing", ""],
            "signature": self.looks.lighting_signature(s)})
        state = self.looks.lighting_state(s)
        self.assertTrue(state["applied"])
        self.assertEqual(state["warnings"], ["cloud texture missing"])

    def test_state_keys(self):
        keys = set(self.looks.lighting_state(_scene("Dam")))
        self.assertEqual(keys, {"applied", "applied_map", "applied_scenario", "applied_label",
                                "pending", "warnings"})


if __name__ == "__main__":
    unittest.main()
