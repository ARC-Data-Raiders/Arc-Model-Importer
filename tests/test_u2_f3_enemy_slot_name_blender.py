"""Enemy slot wiring drops the importer's unused default material so the MI keeps its name (needs bpy)."""
import importlib
import sys
import unittest
from pathlib import Path

try:
    import bpy
except ImportError:
    bpy = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(bpy is None, "needs Blender")
class EnemySlotNameTests(unittest.TestCase):
    def setUp(self):
        if str(ROOT.parent) not in sys.path:
            sys.path.insert(0, str(ROOT.parent))
        self.enemy = importlib.import_module(ROOT.name + ".Shared.materials.enemy")
        self.made = []

    def tearDown(self):
        for m in self.made:
            try:
                bpy.data.materials.remove(m)
            except (ReferenceError, RuntimeError):
                pass

    def _mat(self, name):
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        self.made.append(m)
        return m

    def test_unused_stub_removed_and_name_adopted(self):
        stub = self._mat("MI_U2F3_Armor_01")
        built = self._mat("MI_U2F3_Armor_01")
        self.assertEqual(built.name, "MI_U2F3_Armor_01.001")
        self.assertTrue(self.enemy._adopt_slot_name(stub, built))
        self.assertEqual(built.name, "MI_U2F3_Armor_01")
        self.assertIsNone(bpy.data.materials.get("MI_U2F3_Armor_01.001"))

    def test_used_or_built_stub_kept(self):
        stub = self._mat("MI_U2F3_Trim_01")
        built = self._mat("MI_U2F3_Trim_01")
        stub.use_fake_user = True
        self.assertFalse(self.enemy._adopt_slot_name(stub, built))
        stub.use_fake_user = False
        stub["arc_mi_path"] = "/Game/X/MI_U2F3_Trim_01"
        self.assertFalse(self.enemy._adopt_slot_name(stub, built))
        self.assertEqual(built.name, "MI_U2F3_Trim_01.001")

    def test_other_base_name_kept(self):
        stub = self._mat("MI_U2F3_Other")
        built = self._mat("MI_U2F3_Body.001")
        self.assertFalse(self.enemy._adopt_slot_name(stub, built))


if __name__ == "__main__":
    unittest.main()
