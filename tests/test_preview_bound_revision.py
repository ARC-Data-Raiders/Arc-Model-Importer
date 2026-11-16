import json
import hashlib
from pathlib import Path
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


class PreviewBoundTests(unittest.TestCase):
    def test_weapon_index_uses_content_addressed_files(self):
        folder = ROOT / 'assets/weapon_browser/generated'
        index = json.loads((folder / 'index.json').read_text(encoding='utf-8'))
        names = {name for variants in index['weapons'].values()
                 for tiers in variants.values() for stencils in tiers.values()
                 for name in stencils.values()}
        self.assertEqual(names, {path.name for path in folder.glob('*.avif')})
        for name in names:
            path = folder / name
            self.assertEqual(name, hashlib.sha256(path.read_bytes()).hexdigest() + '.avif')

    def test_all_canonical_previews_and_native_cache_are_bounded(self):
        manifest = json.loads((ROOT / 'assets/preview_thumbnails/manifest.json').read_text(encoding='utf-8'))
        entries = manifest['entries']
        pixels = manifest['pixel_cache']['entries']
        self.assertEqual(set(entries), set(pixels))
        for relative, entry in entries.items():
            self.assertEqual(entry['thumbnail'], relative)
            with Image.open(ROOT / relative) as image:
                self.assertLessEqual(max(image.size), 256, relative)
                self.assertEqual(image.size, (pixels[relative]['width'], pixels[relative]['height']))

    def test_source_preview_derivatives_are_bounded_and_lossless(self):
        source_root = ROOT / 'temp/preview-source'
        output_root = ROOT / 'temp/preview-source-256'
        if not source_root.exists():
            self.skipTest('Source extracts are not available')
        entries = json.loads((ROOT / 'assets/preview_thumbnails/manifest.json').read_text(encoding='utf-8'))['entries']
        packages = {entry['package'] for relative, entry in entries.items()
                    if entry.get('package') and '/generated/' not in relative}
        expected = {Path(package + '.png') for package in packages if (source_root / (package + '.png')).is_file()}
        self.assertTrue(expected)
        actual = {path.relative_to(output_root) for path in output_root.rglob('*.png')}
        self.assertEqual(actual, expected)
        for relative in expected:
            with Image.open(source_root / relative) as original:
                resized = original.convert('RGBA')
            resized.thumbnail((256, 256), Image.Resampling.LANCZOS)
            with Image.open(output_root / relative) as derivative:
                self.assertLessEqual(max(derivative.size), 256, str(relative))
                self.assertEqual(derivative.size, resized.size)
                self.assertEqual(resized.tobytes(), derivative.convert('RGBA').tobytes(), str(relative))


if __name__ == '__main__':
    unittest.main()
