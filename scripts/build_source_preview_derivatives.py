"""Create bounded lossless PNG preview derivatives from manifest-listed extracts."""
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'temp/preview-source'
OUTPUT = ROOT / 'temp/preview-source-256'
MAX_SIDE = 256


def build():
    source = SOURCE.resolve()
    output = OUTPUT.resolve()
    if not source.is_relative_to(ROOT / 'temp') or not output.is_relative_to(ROOT / 'temp'):
        raise ValueError('Preview paths must stay inside temp')
    entries = json.loads((ROOT / 'assets/preview_thumbnails/manifest.json').read_text(encoding='utf-8'))['entries']
    packages = {entry['package'] for relative, entry in entries.items()
                if entry.get('package') and '/generated/' not in relative}
    results = []
    for package in sorted(packages):
        if not package.startswith('PioneerGame/'):
            raise ValueError('Unexpected preview package: ' + package)
        original = (source / (package + '.png')).resolve()
        destination = (output / (package + '.png')).resolve()
        if not original.is_relative_to(source) or not destination.is_relative_to(output):
            raise ValueError('Preview package escapes its root: ' + package)
        if not original.is_file():
            continue
        with Image.open(original) as opened:
            image = opened.convert('RGBA')
        original_size = image.size
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, format='PNG', compress_level=9)
        results.append({'package': package, 'original_size': original_size,
                        'derivative_size': image.size, 'output': str(destination)})
        print('WROTE ' + str(destination))
    print(json.dumps(results))


if __name__ == '__main__':
    build()
