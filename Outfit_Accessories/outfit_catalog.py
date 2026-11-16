"""Outfit identities and exact mesh paths shared by CSV generation and pak import.

Folders/code names are identifiers. Display names never participate in fuzzy
mesh matching; authored DA_OI visual-part references add shared outfit pieces.
"""
from __future__ import annotations

import json
import os
import re

PIONEER_ROOT = 'PioneerGame/Content/Pioneer/'
NPC_PREVIEW_STEMS = {
    'ID_APOLLO_NAME': 'T_UI_TRADER_Apollo',
    'ID_CELESTE_NAME': 'T_UI_TRADER_Celeste',
    'ID_JUANITO_NAME': 'T_UI_TRADER_Ermal',
    'ID_LANCE_NAME': 'T_UI_TRADER_Lance',
    'ID_SHANI_NAME': 'T_UI_TRADER_Shani',
    'ID_TIANWEN_NAME': 'T_UI_TRADER_TianWen',
}


def is_outfit_option(row):
    if row.get('ST') in NPC_PREVIEW_STEMS:
        return True
    return any(row.get(field) for field in ('Model Folder Name', 'Item/UI Folder Name', 'Pak Mesh Paths', 'Visual Part Paths'))



def values(text):
    return [value.strip() for value in (text or '').split(';') if value.strip()]


def canonical_package(path):
    text = str(path or '').replace('\\', '/').strip().strip("'")
    if "'" in text:
        text = text.split("'", 1)[1].rstrip("'")
    text = text.lstrip('/')
    text = text.rsplit('.', 1)[0] if '.' in text.rsplit('/', 1)[-1] else text
    low = text.casefold()
    if low.startswith('game/'):
        return 'PioneerGame/Content/' + text[5:]
    if low.startswith('remappedplugins/content/'):
        return 'PioneerGame/Content/' + text[len('RemappedPlugins/Content/'):]
    if low.startswith('pioneergame/content/'):
        return 'PioneerGame/Content/' + text[len('PioneerGame/Content/'):]
    if low.startswith('characters/') or low.startswith('items/'):
        return PIONEER_ROOT + text
    return text


def is_mesh(path):
    leaf = path.rsplit('/', 1)[-1].casefold()
    return leaf.startswith(('sk_', 'sm_')) and not leaf.endswith(('_skeleton', '_physics'))


def unique_paths(paths):
    result = {}
    for path in paths:
        canonical = canonical_package(path)
        if canonical:
            result.setdefault(canonical.casefold(), canonical)
    return sorted(result.values(), key=str.casefold)


def document_visual_parts(document):
    entries = document.get('Exports', [document]) if isinstance(document, dict) else document
    parts = []
    for entry in entries or []:
        if not isinstance(entry, dict) or entry.get('Type') != 'CharacterVisualSkinOnlineItemDataAsset':
            continue
        for value in entry.get('Properties', {}).get('Parts', []) or []:
            if isinstance(value, dict):
                value = value.get('AssetPathName') or value.get('ObjectPath') or ''
            path = canonical_package(value)
            if '/characters/assets/' in path.casefold():
                parts.append(path)
    return unique_paths(parts)


def row_visual_parts(row):
    """Exact DA_VP paths audited from the current cook, including borrowed parts."""
    return unique_paths(values(row.get('Visual Part Paths', '')))


def row_model_prefixes(row, *, include_legacy_parts=True):
    prefixes = [PIONEER_ROOT + 'Characters/Assets/' + model for model in values(row.get('Model Folder Name'))]
    # Older/custom reference CSVs describe shared parts as Cap[CasualPilot].
    for segment in ((row.get('DA_OI Parts') or '') if include_legacy_parts else '').split('|'):
        content = segment.split(':', 1)[-1]
        for part, model in re.findall(r'([^,\[\]]+)\[([^\]]+)\]', content):
            prefixes.append(PIONEER_ROOT + 'Characters/Assets/' + model.strip() + '/' + part.strip())
    return unique_paths(prefixes)


def row_mesh_paths(row, indexed_paths=(), visual_parts=None):
    """Resolve all model/toggle pieces and authored shared parts to exact packages.

    Prefer the live index, so newly cooked pieces are discovered and removed
    packages do not survive from the bundled reference. A reference without an
    index can still use its recorded exact package paths or folder prefixes.
    """
    prefixes = row_model_prefixes(row, include_legacy_parts=visual_parts is None)
    parts = row_visual_parts(row) if visual_parts is None else unique_paths(visual_parts)
    prefixes.extend(path.rsplit('/', 1)[0] for path in parts)
    prefixes = [path.casefold().rstrip('/') + '/' for path in unique_paths(prefixes)]
    indexed = unique_paths(path for path in indexed_paths if is_mesh(path))
    primary = tuple((PIONEER_ROOT + 'Characters/Assets/' + model).casefold().rstrip('/') + '/'
                    for model in values(row.get('Model Folder Name')))
    def primary_first(path):
        # Batch import derives the outfit character/colourway from its first
        # mesh. Borrowed pieces must not replace the outfit's own identity.
        return (not path.casefold().startswith(primary), path.casefold())
    recorded = unique_paths(values(row.get('Pak Mesh Paths', '')))
    if indexed:
        recorded_keys = {path.casefold() for path in recorded} if visual_parts is None else set()
        return sorted((path for path in indexed if path.casefold() in recorded_keys or any(path.casefold().startswith(prefix) for prefix in prefixes)), key=primary_first)
    result = recorded or unique_paths([*row_model_prefixes(row, include_legacy_parts=visual_parts is None), *(path.rsplit('/', 1)[0] for path in parts)])
    return sorted(result, key=primary_first)


def base_item_paths(row, indexed_packages):
    folders = {folder.casefold() for folder in values(row.get('Item/UI Folder Name'))}
    result = []
    for path in indexed_packages:
        path = canonical_package(path)
        if '/items/characters/skins/outfit/' not in path.casefold():
            continue
        folder, leaf = path.rsplit('/', 2)[-2:]
        if folder.casefold() in folders and leaf.casefold() == 'da_oi_outfit_' + folder.casefold():
            result.append(path)
    return unique_paths(result)


_DA_RECEIPT_SUFFIX = '.arc-da.json'


def _da_candidates(pak_extract, pak_catalog, path):
    candidates = [os.path.join(pak_extract.cache_dir(), *path.split('/')) + '.json']
    candidates.append(candidates[0].replace('PioneerGame' + os.sep + 'Content', 'RemappedPlugins' + os.sep + 'Content'))
    candidates = [pak_catalog.filesystem_path(filename) for filename in candidates]
    return sorted((filename for filename in candidates if os.path.isfile(filename)),
                  key=lambda filename: os.stat(filename).st_mtime_ns, reverse=True)


def _da_receipt_record(filename, revision, producer):
    stat = os.stat(filename)
    return {'revision': revision, 'producer': producer, 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def _da_receipts_current(pak_extract, pak_catalog, base_paths, revision, producer):
    """True when every DA_OI JSON was written by this exporter for this game build/containers/usmap."""
    if not revision or producer is None:
        return False
    for path in base_paths:
        candidates = _da_candidates(pak_extract, pak_catalog, path)
        if not candidates:
            return False
        try:
            with open(candidates[0] + _DA_RECEIPT_SUFFIX, encoding='utf-8') as stream:
                receipt = json.load(stream)
            if receipt != _da_receipt_record(candidates[0], revision, producer):
                return False
        except (OSError, ValueError):
            return False
    return True


def resolve_pak_outfit_paths(scene, row, *, force=False):
    from ..Shared import pak_extract; from . import pak_catalog
    pak_extract.load_pak_index(scene, allow_reindex=True)
    packages = pak_extract.indexed_package_paths(scene)
    base_paths = base_item_paths(row, packages)
    parts = None
    colour_mis = []
    if base_paths:
        # Metadata is refreshed from the selected game unless its receipt names
        # this game revision (containers, build, depot, usmap) and this exporter.
        force = force or os.environ.get('DATARAIDERS_FORCE_DA_REFRESH') == '1'
        revision = pak_extract._extraction_revision(scene)
        producer = pak_extract._extraction_producer()
        colour_folders = _colourway_cache_folders(pak_extract, pak_catalog, base_paths)
        if not force and _da_receipts_current(pak_extract, pak_catalog, base_paths, revision, producer):
            code = 0
            pak_extract.utils.set_pak_cache_root(pak_extract.cache_dir())
        else:
            # The colourway DA_OI folder rides the same mount. Extracting it later
            # rewrites the base DA_OI JSON and voids the receipt written below.
            extra = [package for package, folder in colour_folders if not _has_colourway_json(folder)]
            code, log, _meshes = pak_extract.extract_paths(
                pak_extract.configured_dir(scene), pak_extract.resolve_usmap(scene), base_paths + extra)
            if code != 0:
                print('Arc Pak outfit metadata: ' + log[-1200:])
            if extra:
                from ..Shared import importing
                for _package, folder in colour_folders:
                    importing.invalidate_colourway_index(folder)
        for path in base_paths if code == 0 else ():
            candidates = _da_candidates(pak_extract, pak_catalog, path)
            for filename in candidates:
                try:
                    with open(filename, encoding='utf-8-sig') as stream:
                        document = json.load(stream)
                    if parts is None:
                        parts = []
                    parts.extend(document_visual_parts(document))
                    if filename == candidates[0] and revision and producer is not None:
                        try:
                            with open(filename + _DA_RECEIPT_SUFFIX, 'w', encoding='utf-8') as stream:
                                json.dump(_da_receipt_record(filename, revision, producer), stream)
                        except OSError:
                            pass
                    break
                except (OSError, ValueError):
                    continue
        if code == 0:
            colour_mis = _missing_colourway_materials(colour_folders)
    rels = row_mesh_paths(row, packages, parts)
    # Missing colourway MIs join the mesh extract (one mount, one closure) and
    # the metadata prefetch; a warm cache adds nothing, so mesh reuse still holds.
    if not rels or not colour_mis:
        return rels
    have = {canonical_package(path).casefold() for path in rels}
    return list(rels) + [path for path in unique_paths(colour_mis) if path.casefold() not in have]


def _colourway_cache_folders(pak_extract, pak_catalog, base_paths):
    """(cooked folder package, cache folder) for each DA_OI base path's outfit folder."""
    out = []
    for path in base_paths:
        package = path.rsplit('/', 1)[0]
        folder = pak_catalog.filesystem_path(os.path.join(pak_extract.cache_dir(), *package.split('/')))
        out.append((package, folder))
    return out


def _has_colourway_json(folder):
    from ..Shared import importing
    return importing._folder_has_colourway_json(folder)


def _missing_colourway_materials(colour_folders):
    from ..Shared import importing
    out = []
    for _package, folder in colour_folders:
        if _has_colourway_json(folder):
            out.extend(importing.colourway_material_packages(folder))
    return out
