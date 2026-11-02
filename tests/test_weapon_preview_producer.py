import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WeaponPreviewProducerTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('weapon_preview_producer', ROOT / 'tools/build_weapon_previews.py')
        self.producer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.producer)
        self.catalog = {'weapons': {'ExactStem': {'variants': [{'id': 'Ascended/exact.json'}], 'skins': [{'id': 'CommonBlue'}]}}}
        self.row = {'attachment_variation': None, 'variant': 'OFF', 'stencil_id': 'CommonBlue', 'tier': 2, 'attachments': {}, 'render_metadata': {'kind': 'stencil', 'cell': {'stem': 'ExactStem', 'tier': 1, 'variant': 'OFF', 'skin': 'CommonBlue'}}, 'files': [{'level': '1-4', 'destination': 'quarter.avif', 'sha256': 'hash'}]}

    def test_exact_identity_uses_render_tier_and_quarter_only(self):
        selected = self.producer.select_sources({'images': [self.row]}, self.catalog)
        self.assertEqual(selected[0]['identity'], ('ExactStem', 'OFF', '1', 'CommonBlue'))
        self.assertEqual(selected[0]['file']['destination'], 'quarter.avif')

    def test_attachment_and_unrelated_display_names_are_not_candidates(self):
        self.row['render_metadata']['kind'] = 'attachment'
        self.assertEqual(self.producer.select_sources({'images': [self.row]}, self.catalog), [])
        self.row['render_metadata']['kind'] = 'stencil'
        self.row['render_metadata']['cell']['stem'] = 'ExactSte'
        with self.assertRaises(ValueError):
            self.producer.select_sources({'images': [self.row]}, self.catalog)

    def test_duplicate_identity_and_missing_quarter_fail(self):
        with self.assertRaises(ValueError):
            self.producer.select_sources({'images': [self.row, self.row]}, self.catalog)
        self.row['files'][0]['level'] = 'full'
        with self.assertRaises(ValueError):
            self.producer.select_sources({'images': [self.row]}, self.catalog)

    def test_variant_and_stencil_are_not_fuzzy_matched(self):
        for field, wrong in [('variant', 'Ascended/exac.json'), ('stencil_id', 'CommonBl')]:
            with self.subTest(field=field):
                changed = dict(self.row, **{field: wrong})
                with self.assertRaises(ValueError):
                    self.producer.select_sources({'images': [changed]}, self.catalog)


if __name__ == '__main__':
    unittest.main()
