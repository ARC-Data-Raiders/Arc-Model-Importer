import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Shared/materials"))

import param_labels


class ParamLabelTests(unittest.TestCase):
    def test_known(self):
        self.assertEqual(param_labels.label_for("DirtAmount"), ("Dirt Amount", "Dirt"))
        self.assertEqual(param_labels.label_for("Eq7"), ("Tint 8", "Palette"))

    def test_case(self):
        self.assertEqual(param_labels.label_for("OCM_WEAR"), ("Wear Mask", "Wear"))

    def test_unknown(self):
        self.assertEqual(param_labels.label_for("FooBar"), ("FooBar", "Other"))

    def test_known_vs_unknown_raw(self):
        self.assertEqual(param_labels.label_for("Emissive Power")[0], "Emission Strength")
        self.assertEqual(param_labels.label_for("Some Unseen Param"), ("Some Unseen Param", "Other"))

    def test_groups(self):
        allowed = {"Palette", "Wear", "Dirt", "Surface", "Decal", "Emission", "Normal", "Other"}
        for label, group in param_labels.LABELS.values():
            self.assertIn(group, allowed)

    def test_weapon(self):
        f = param_labels.label_for
        self.assertEqual(f("Base Color Red"), ("Zone 1 Colour", "Palette"))
        self.assertEqual(f("base - color - alpha"), ("Zone 4 Colour", "Palette"))
        self.assertEqual(f("Paintjob Patern - Blue"), ("Pattern Zone 3", "Palette"))
        self.assertEqual(f("PATTERN ID"), ("Pattern ID", "Other"))
        self.assertEqual(f("Use Separate Pattern ID"), ("Use Separate Pattern ID", "Other"))
        self.assertEqual(f("Enable Green Mask"), ("Enable Zone 2 Mask", "Other"))
        self.assertEqual(f("Base - Wear Delay"), ("Wear Delay", "Wear"))
        self.assertEqual(f("Emissive Power"), ("Emission Strength", "Emission"))


if __name__ == "__main__":
    unittest.main()
