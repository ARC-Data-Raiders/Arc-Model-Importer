"""Index bounded canonical AVIFs, then build_preview_pixel_cache.py in Blender."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from Outfit_Accessories.pak_catalog import read_index
from Outfit_Accessories.outfit_catalog import NPC_PREVIEW_STEMS

PREVIEW_FOLDERS = ('assets/ui_icons/appearance', 'assets/arc_browser', 'assets/weapon_browser')
THUMBNAIL_SCALE = 1.0


def write_manifest(output, entries):
    manifest = {'schema': 2, 'scale': THUMBNAIL_SCALE, 'compression': 'bounded-avif',
                'entries': entries}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'previews': len(entries), 'pak_sources': sum(bool(entry['package']) for entry in entries.values()),
                      'bytes': sum((ROOT / relative).stat().st_size for relative in entries)}))


def refresh(output):
    output = output.resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError('Preview output escapes the addon')
    entries = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))['entries']
    obsolete = []
    for relative, entry in entries.items():
        source = (ROOT / relative).resolve()
        destination = (output / entry['thumbnail']).resolve()
        if not source.is_relative_to(ROOT) or source.suffix.lower() != '.avif' or not source.is_file():
            raise ValueError('Preview path escapes its asset directory')
        if destination.parent == output and destination.suffix == '.avif':
            obsolete.append(destination)
        entry['thumbnail'] = relative
    write_manifest(output, entries)
    for destination in obsolete:
        destination.unlink(missing_ok=True)
    print('REMOVED duplicate AVIFs: ' + str(len(obsolete)))


def collect_references(json_root):
    references = {}
    if not json_root:
        return references

    def visit(value):
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str) and value.startswith('/Game/'):
            package = 'PioneerGame/Content/' + value[6:].split('.', 1)[0]
            stem = package.rsplit('/', 1)[-1]
            if stem.startswith('T_'):
                references.setdefault(stem.casefold(), set()).add(package)

    pioneer = json_root / 'PioneerGame/Content/Pioneer'
    for relative, pattern in (('Enemies', 'DA_EnemyMetaData*.json'),
                              ('Items', 'DA_UIMetaData*.json')):
        for source in (pioneer / relative).rglob(pattern):
            visit(json.loads(source.read_text(encoding='utf-8-sig')))
    return references


def build(index_path, output, json_root=None):
    indexed = {}
    for package in read_index(str(index_path))['paths']:
        stem = package.rsplit('/', 1)[-1]
        if stem.startswith('T_'):
            indexed.setdefault(stem.casefold(), []).append(package)
    for stem, packages in collect_references(json_root).items():
        indexed[stem] = sorted(set(indexed.get(stem, ())) | packages)
    for stem in NPC_PREVIEW_STEMS.values():
        indexed[stem.casefold()] = ['PioneerGame/Content/Pioneer/UI/Assets/Images/NPC/miniature_portraits/' + stem]
    weapon_catalog = json.loads((ROOT / 'assets/weapon_browser/catalog.json').read_text(encoding='utf-8'))
    generated = json.loads((ROOT / 'assets/weapon_browser/generated/index.json').read_text(encoding='utf-8'))
    generated_sources = {}
    for weapon, variants in generated['weapons'].items():
        catalog = weapon_catalog['weapons'].get(weapon, {})
        for variant, tiers in variants.items():
            original = catalog.get('preview') if variant == 'OFF' else next(
                (entry.get('preview') for entry in catalog.get('variants', ()) if entry['id'] == variant), None)
            if not original:
                continue
            for tier, stencils in tiers.items():
                filename = stencils.get('NONE')
                stem = re.sub(r'_T[0-3]$', '_T' + str(tier), Path(original).stem)
                matches = indexed.get(stem.casefold(), ())
                if filename and len(matches) == 1:
                    generated_sources[filename] = matches[0]
    output.mkdir(parents=True, exist_ok=True)
    entries = {}
    for folder in PREVIEW_FOLDERS:
        for source in sorted((ROOT / folder).rglob('*')):
            if source.suffix.lower() not in ('.avif', '.png', '.webp', '.jpg', '.jpeg'):
                continue
            relative = source.relative_to(ROOT).as_posix()
            if source.suffix.lower() != '.avif':
                raise ValueError('Convert canonical previews to AVIF before indexing: ' + relative)
            matches = indexed.get(source.stem.casefold(), [])
            package = generated_sources.get(source.name, '') if '/generated/' in relative else matches[0] if len(matches) == 1 else ''
            entries[relative] = {'thumbnail': relative, 'package': package}
    write_manifest(output, entries)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--json-root', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'assets/preview_thumbnails')
    arguments = parser.parse_args()
    if arguments.refresh:
        refresh(arguments.output)
    elif arguments.index:
        build(arguments.index, arguments.output, arguments.json_root)
    else:
        parser.error('--index or --refresh is required')

