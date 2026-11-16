"""R6: weapon edits touch only the one resolved weapon. R5: cancelled batches leave unattempted items as left."""
import importlib
import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(os.environ.get("DATARAIDERS_TEST_REPOSITORY", Path(__file__).resolve().parents[1]))
PKG = "_dr_probe_c_t4"


def _pkg(name):
    full = PKG + ("." + name if name else "")
    if full not in sys.modules:
        mod = types.ModuleType(full)
        mod.__path__ = [str(ROOT / name) if name else str(ROOT)]
        sys.modules[full] = mod
        if name:
            setattr(sys.modules[PKG], name, mod)
    return sys.modules[full]


def _load(name):
    _pkg("")
    _pkg("Shared")
    _pkg(name.split(".")[0])
    return importlib.import_module(PKG + "." + name)


class _Report:
    def __init__(self):
        self.lines = []

    def __call__(self, kind, text):
        self.lines.append((set(kind), text))


class WeaponSingleTargetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pos = _load("Weapons.weapon_position")
        cls.ui = _load("Weapons.weapon_ui")

    def setUp(self):
        self.objs = []
        for name in ("_dr_t4_gun_a", "_dr_t4_gun_b"):
            o = bpy.data.objects.new(name, None)
            o["arc_weapon_assembly"] = True
            o["arc_weapon_key"] = name
            self.objs.append(o)
        self.part = bpy.data.objects.new("_dr_t4_gun_b_part", None)
        self.part.parent = self.objs[1]

    def tearDown(self):
        for o in [self.part, *self.objs]:
            bpy.data.objects.remove(o, do_unlink=True)

    def _context(self, active, selected):
        scene = SimpleNamespace(objects={}, arc_weapon_position="AIMED", arc_weapon_last_assembly="")
        return SimpleNamespace(scene=scene, active_object=active, selected_objects=selected,
                               view_layer=SimpleNamespace(objects=SimpleNamespace(active=active)))

    def test_two_selected_weapons_resolve_to_active_only(self):
        a, b = self.objs
        context = self._context(self.part, [a, self.part])
        self.assertEqual(self.pos.roots_for_selection(context), [b])

    def test_position_edits_one_weapon(self):
        a, b = self.objs
        context = self._context(a, [a, b])
        moved = []
        fake = SimpleNamespace(report=_Report())

        def apply(root, mode):
            moved.append(root)
            root["arc_weapon_position"] = mode

        with patch.object(self.pos, "apply", apply):
            result = self.ui.ARC_OT_update_weapon_position.execute(fake, context)
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual(moved, [a])
        self.assertNotIn("arc_weapon_position", b)
        self.assertEqual(fake.report.lines[-1][1], "Updated position of " + a.name)


class _WM:
    def event_timer_remove(self, timer):
        pass

    def progress_end(self):
        pass


class BatchCancelAccountingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets = _load("Shared.ui_targets")
        cls.ui = _load("Weapons.weapon_ui")
        cls.arc = _load("Arc.arc_ui")

    def tearDown(self):
        if self.targets.job_active():
            self.targets.job_end(0, 0, 0, [], status="cancelled")

    def _check(self, report, imported, failed, left):
        self.assertEqual(report["status"], "cancelled")
        self.assertEqual((report["imported"], report["failed"], report["skipped"]), (imported, failed, 0))
        self.assertEqual(report["total"], imported + failed + left)
        self.assertEqual(report["left"], left)
        self.assertIn("Stopped with %d left" % left, report["lines"])

    def test_weapon_batch_cancel_leaves_unattempted(self):
        self.assertTrue(self.targets.job_begin("weapon", "Importing all weapons", 40, ""))
        fake = SimpleNamespace(_timer=None, _queue=[({}, "OFF")] * 27, _added=["w"] * 12,
                               _errors=[{"weapon": "Bad", "variant": "OFF", "error": "boom"}],
                               _snap=None, report=_Report())
        self.ui._ACTIVE_BATCH = fake
        context = SimpleNamespace(window_manager=_WM())
        self.ui.ARC_OT_add_all_weapons._finish(fake, context, cancelled=True)
        self._check(self.targets.job_report(), 12, 1, 27)

    def test_enemy_batch_cancel_leaves_unattempted(self):
        self.assertTrue(self.targets.job_begin("enemy", "Importing all enemies", 40, ""))
        fake = SimpleNamespace(_stopped=False, _queue=[("K", "L", "")] * 28, _imported=12, _failed=0, _errors=[])
        with patch.object(self.arc, "_targets", lambda: self.targets):
            self.arc.ARC_OT_add_all._end_job(fake, True)
        self._check(self.targets.job_report(), 12, 0, 28)


if __name__ == "__main__":
    unittest.main()
