"""Regenerate bounded canonical previews, retaining generated compositions."""
import argparse
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MAX_SIDE = 256
QUALITY = 85
SPEED = 6


def build(source_root):
    source_root = source_root.resolve()
    entries = json.loads((ROOT / 'assets/preview_thumbnails/manifest.json').read_text(encoding='utf-8'))['entries']
    counts = {'original': 0, 'lossy_fallback': 0, 'unchanged': 0}
    for relative, entry in entries.items():
        target = (ROOT / relative).resolve()
        if not target.is_relative_to(ROOT / 'assets') or target.suffix.lower() != '.avif':
            raise ValueError('Invalid canonical preview: ' + relative)
        package = entry.get('package', '')
        original = (source_root / (package + '.png')).resolve() if package else None
        if original and not original.is_relative_to(source_root):
            raise ValueError('Source escapes extraction root: ' + package)
        if '/generated/' in relative:
            original = None
        if original and original.is_file():
            source = original
            counts['original'] += 1
        else:
            source = target
            with Image.open(source) as image:
                if max(image.size) <= MAX_SIDE:
                    counts['unchanged'] += 1
                    continue
            counts['lossy_fallback'] += 1
        with Image.open(source) as opened:
            image = opened.convert('RGBA')
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        image.save(target, 'AVIF', quality=QUALITY, speed=SPEED, subsampling='4:4:4')
    print(json.dumps(counts))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True, type=Path)
    build(parser.parse_args().source_root)
