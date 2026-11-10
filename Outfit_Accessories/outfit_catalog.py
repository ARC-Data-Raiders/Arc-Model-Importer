"""Outfit identities and exact mesh paths shared by CSV generation and pak import.

Folders/code names are identifiers. Display names never participate in fuzzy
mesh matching; authored DA_OI visual-part references add shared outfit pieces.
"""
from __future__ import annotations

import json
import os
import re

PIONEER_ROOT = 'PioneerGame/Content/Pioneer/'


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
    if indexed:
        return sorted((path for path in indexed if any(path.casefold().startswith(prefix) for prefix in prefixes)), key=primary_first)
    recorded = unique_paths(values(row.get('Pak Mesh Paths', '')))
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


def resolve_pak_outfit_paths(scene, row):
    from ..Shared import pak_extract; from . import pak_catalog
    pak_extract.load_pak_index(scene, allow_reindex=True)
    packages = pak_extract.indexed_package_paths(scene)
    base_paths = base_item_paths(row, packages)
    parts = None
    if base_paths:
        # Metadata is tiny and is refreshed from the selected game, rather than
        # assuming an old cached DA/CSV is authoritative after a game update.
        code, log, _meshes = pak_extract.extract_paths(
            pak_extract.configured_dir(scene), pak_extract.resolve_usmap(scene), base_paths)
        if code != 0:
            print('Arc Pak outfit metadata: ' + log[-1200:])
        for path in base_paths if code == 0 else ():
            candidates = [os.path.join(pak_extract.cache_dir(), *path.split('/')) + '.json']
            candidates.append(candidates[0].replace('PioneerGame' + os.sep + 'Content', 'RemappedPlugins' + os.sep + 'Content'))
            candidates = [pak_catalog.filesystem_path(filename) for filename in candidates]
            candidates = sorted((filename for filename in candidates if os.path.isfile(filename)),
                                key=lambda filename: os.stat(filename).st_mtime_ns, reverse=True)
            for filename in candidates:
                try:
                    with open(filename, encoding='utf-8-sig') as stream:
                        document = json.load(stream)
                    if parts is None:
                        parts = []
                    parts.extend(document_visual_parts(document))
                    break
                except (OSError, ValueError):
                    continue
    return row_mesh_paths(row, packages, parts)
