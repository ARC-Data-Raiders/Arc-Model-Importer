import csv
import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
appearance = importlib.import_module(ROOT.name + '.Outfit_Accessories.appearance')


class GearAvailabilityTests(unittest.TestCase):
    def test_authored_backpacks_have_resolved_mesh_rows(self):
        with (ROOT / 'Data/appearance_reference.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = {row['folder']: row for row in csv.DictReader(stream) if row['kind'] == 'backpack'}
        expected = {
            'Scout': 'Characters/Backpacks/Containers/Scout/ScoutContainer/SK_Scout_ScoutContainer.psk',
            'TreasureChest': 'Characters/Backpacks/Containers/TreasureChest/Treasurechest/SM_TreasureChest_Treasurechest.pskx',
        }
        for folder, relative in expected.items():
            self.assertEqual(rows[folder]['has_mesh'], '1', folder)
            self.assertEqual(rows[folder]['rel_path'], relative, folder)

    def test_unavailable_enum_choice_is_not_importable(self):
        choices = [
            {'id': 'ready', 'label': 'Ready', 'has_mesh': True, 'rel_path': 'Characters/Ready.psk'},
            {'id': 'unavailable', 'label': 'Unavailable', 'has_mesh': False},
            {'id': 'second', 'label': 'Second', 'has_mesh': True, 'rel_path': 'Characters/Second.psk'},
        ]
        with patch.object(appearance, 'list_gear_choices', return_value=choices):
            items = appearance.make_backpack_enum_items(None, None)
        self.assertEqual([item[0] for item in items], ['NONE', 'ready', 'second'])
        self.assertEqual([item[3] for item in items[1:]], [1, 3])

    def test_packaged_backpacks_do_not_offer_closed_alpha(self):
        items = appearance.make_backpack_enum_items(None, None)
        identities = {item[0] for item in items}
        self.assertNotIn('2bca8eda380d9cce', identities)
        self.assertIn('d3d7482bd4d25dd2', identities)
        self.assertIn('5ae3873862d695ea', identities)


if __name__ == '__main__':
    unittest.main()
