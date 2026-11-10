"""Package exact-identity default/stencil renders from the Icons manifest."""
import argparse
import hashlib
import io
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def select_sources(manifest, catalog):
    selected = []
    seen = set()
    for row in manifest['images']:
        metadata = row['render_metadata']
        if metadata.get('kind') not in ('default', 'stencil'):
            continue
        if row['attachment_variation'] is not None or row.get('attachments'):
            continue
        cell = metadata['cell']
        stem = cell['stem']
        weapon = catalog['weapons'].get(stem)
        variant = row['variant']
        stencil = row['stencil_id'] or 'NONE'
        tier = int(cell['tier'])
        if weapon is None:
            raise ValueError('Unknown weapon stem: ' + stem)
        if variant not in {'OFF', *(item['id'] for item in weapon['variants'])}:
            raise ValueError('Unknown variant: ' + variant)
        if stencil not in {'NONE', *(item['id'] for item in weapon['skins'])}:
            raise ValueError('Unknown stencil: ' + stencil)
        if cell['variant'] != variant or (cell.get('skin') or 'NONE') != stencil:
            raise ValueError('Conflicting render identity: ' + stem)
        if tier not in range(4) or row['tier'] != (tier + 1 if variant == 'OFF' else 5):
            raise ValueError('Conflicting render tier: ' + stem)
        identity = (stem, variant, str(tier), stencil)
        if identity in seen:
            raise ValueError('Duplicate preview identity: ' + repr(identity))
        quarter = [item for item in row['files'] if item['level'] == '1-4']
        if len(quarter) != 1:
            raise ValueError('Expected one quarter-resolution source: ' + repr(identity))
        seen.add(identity)
        selected.append({'identity': identity, 'file': quarter[0]})
    return sorted(selected, key=lambda item: item['identity'])


def build(source, destination, catalog_path, evidence_path):
    from PIL import Image
    source = source.resolve()
    destination = destination.resolve()
    manifest_bytes = (source / 'manifest.json').read_bytes()
    catalog_bytes = catalog_path.read_bytes()
    selected = select_sources(json.loads(manifest_bytes), json.loads(catalog_bytes))
    if not selected or len(selected) > 600:
        raise ValueError('Preview set is empty or exceeds the image budget')
    prepared = []
    total = 0
    for item in selected:
        path = Path(item['file']['destination']).resolve()
        if not path.is_relative_to(source) or path.parent.name != '1-4':
            raise ValueError('Source is not a delivered quarter-resolution image: ' + str(path))
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != item['file']['sha256']:
            raise ValueError('Source hash mismatch: ' + str(path))
        with Image.open(io.BytesIO(data)) as original:
            if max(original.size) > 512:
                raise ValueError('Source exceeds quarter-resolution budget: ' + str(path))
            image = original.convert('RGBA')
            output = io.BytesIO()
            image.save(output, format='AVIF', quality=60, speed=6)
            encoded = output.getvalue()
            size = list(image.size)
        filename = hashlib.sha256(encoded).hexdigest() + '.avif'
        prepared.append((item['identity'], filename, encoded, {
            'identity': item['identity'], 'source': path.relative_to(source).as_posix(),
            'source_sha256': digest, 'preview': filename, 'size': size,
            'bytes': len(encoded),
        }))
        total += len(encoded)
    if total > 32 * 1024 * 1024:
        raise ValueError('Preview set exceeds the package byte budget')
    index = {'schema': 1, 'source_level': '1-4',
             'source_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
             'weapons': {}}
    destination.mkdir(parents=True, exist_ok=True)
    for identity, filename, encoded, record in prepared:
        (destination / filename).write_bytes(encoded)
        stem, variant, tier, stencil = identity
        index['weapons'].setdefault(stem, {}).setdefault(variant, {}).setdefault(tier, {})[stencil] = filename
    index_bytes = json.dumps(index, separators=(',', ':'), sort_keys=True).encode() + b'\n'
    (destination / 'index.json').write_bytes(index_bytes)
    evidence = {'source': str(source), 'manifest_sha256': index['source_manifest_sha256'],
                'catalog_sha256': hashlib.sha256(catalog_bytes).hexdigest(),
                'images': len(prepared), 'png_bytes': total, 'index_bytes': len(index_bytes),
                'files': [record for identity, filename, encoded, record in prepared]}
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in evidence.items() if key != 'files'}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, default=REPO / 'assets/weapon_browser/generated')
    parser.add_argument('--catalog', type=Path, default=REPO / 'assets/weapon_browser/catalog.json')
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.destination, args.catalog, args.evidence)


if __name__ == '__main__':
    main()
