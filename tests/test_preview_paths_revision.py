import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


class PreviewPathsRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT.parent))
        cls.appearance = importlib.import_module(ROOT.name + '.Outfit_Accessories.appearance')
        cls.cache = importlib.import_module(ROOT.name + '.Outfit_Accessories.preview_cache')
        cls.assets = importlib.import_module(ROOT.name + '.Weapons.weapon_assets')
        cls.catalog = importlib.import_module(ROOT.name + '.Weapons.weapon_catalog')

    def test_weapon_falls_back_to_sidebar_preview(self):
        entry = {'stem': 'example'}
        with patch.object(self.catalog, 'weapon_by_key', return_value=entry), patch.object(self.assets, 'bundled_preview', side_effect=[None, Path('sidebar.png')]):
            self.assertEqual(self.appearance.browser_preview_path(None, 'weapon', 'example'), 'sidebar.png')

    def test_real_weapon_paths_match_available_bundled_art(self):
        available = [entry for entry in self.catalog.weapons() if self.assets.bundled_preview(entry, 'OFF')]
        self.assertTrue(available)
        for entry in available:
            path = self.appearance.browser_preview_path(None, 'weapon', entry['key'])
            self.assertTrue(Path(path).is_file(), entry['key'])

    def test_crop_retains_alpha_one_extremities(self):
        rgba = np.zeros((12, 20, 4), dtype=np.uint8)
        rgba[4:8, 5:15] = (100, 100, 100, 255)
        rgba[6, 2] = (1, 1, 1, 1)
        rgba[6, 18] = (1, 1, 1, 1)
        result = self.cache.thumbnail(rgba.view(np.int32).ravel(), 20, 12)
        self.assertEqual(result.shape, (4, 17, 4))
        self.assertEqual(int(result[2, 0, 3]), 1)
        self.assertEqual(int(result[2, -1, 3]), 1)

    def test_unknown_preview_size(self):
        self.assertEqual(self.appearance.preview_image_size(0), (0, 0))
        with patch.object(self.appearance, '_PREVIEW_COLLECTION', {'sample': SimpleNamespace(icon_id=42, image_size=(30, 12))}):
            self.assertEqual(self.appearance.preview_image_size(42), (30, 12))
            self.assertEqual(self.appearance.preview_image_size(43), (0, 0))
