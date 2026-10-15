"""Read ArcPak's cached path index and expose meshes to Blender's file browser.

The tree contains empty .uasset selection files, never extracted asset data.
Paths are package identities from the same build index used by the exporter.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import struct

_INDEX_CACHE = {}


def _io_path(path) -> Path:
    """Blender's embedded Python may lack Windows long-path manifest support."""
    text = os.path.abspath(path)
    if os.name == 'nt' and not text.startswith('\\\\?\\'):
        text = '\\\\?\\UNC\\' + text[2:] if text.startswith('\\\\') else '\\\\?\\' + text
    return Path(text)


def display_path(path) -> str:
    text = str(path)
    if text.startswith('\\\\?\\UNC\\'):
        return '\\\\' + text[8:]
    return text[4:] if text.startswith('\\\\?\\') else text


def filesystem_path(path) -> str:
    """Keep ordinary paths readable; opt long paths into native Windows IO."""
    if os.name == 'nt' and str(path).startswith(('\\\\?\\', '//?/')):
        return str(_io_path(os.path.normpath(path)))
    text = display_path(path)
    return str(_io_path(text)) if os.name == 'nt' and len(os.path.abspath(text)) >= 248 else text


def canonical_path(path: str) -> str:
    text = str(path or '').replace('\\', '/').strip().strip('/')
    if text.casefold().endswith('.uasset'):
        text = text[:-7]
    if '.' in text.rsplit('/', 1)[-1]:
        text = text.rsplit('.', 1)[0]
    for prefix in ('RemappedPlugins/Content/', 'Game/'):
        if text.casefold().startswith(prefix.casefold()):
            text = 'PioneerGame/Content/' + text[len(prefix):]
            break
    parts = text.split('/')
    if not text or any(p in ('', '.', '..') or ':' in p or '\x00' in p for p in parts):
        raise ValueError('Invalid package path in Pak index')
    return text


def is_mesh(path: str) -> bool:
    leaf = path.rsplit('/', 1)[-1].casefold()
    return leaf.startswith(('sk_', 'sm_')) and not leaf.endswith(('_physics', '_skeleton'))


def read_index(path: str) -> dict:
    """Read the bounded format 3–6 path table; reject incomplete/unknown indexes."""
    stat = os.stat(path)
    key = (os.path.abspath(path), stat.st_mtime_ns, stat.st_size)
    if key in _INDEX_CACHE:
        return _INDEX_CACHE[key]
    if stat.st_size > 256 * 1024 * 1024:
        raise ValueError('Pak index exceeds supported size')
    data = Path(path).read_bytes()
    offset = 0

    def unpack(fmt):
        nonlocal offset
        size = struct.calcsize(fmt)
        if offset + size > len(data):
            raise ValueError('Truncated Pak index')
        result = struct.unpack_from(fmt, data, offset)
        offset += size
        return result[0] if len(result) == 1 else result

    def name():
        nonlocal offset
        size = unpack('<H')
        if offset + size > len(data):
            raise ValueError('Truncated Pak index name')
        result = data[offset:offset + size].decode('utf-8')
        offset += size
        return result

    def skip_table(record_size):
        nonlocal offset
        count = unpack('<i')
        if count < 0 or count > (len(data) - offset) // record_size:
            raise ValueError('Invalid Pak index table size')
        offset += count * record_size

    if unpack('<8s') != b'ARCPAKIX':
        raise ValueError('Invalid Pak index signature')
    version = unpack('<I')
    if version not in (3, 4, 5, 6):
        raise ValueError('Reindex Paks to refresh the mesh catalog')
    build, depot = name(), name()
    count = unpack('<i')
    if not 0 < count < 65535:
        raise ValueError('Invalid Pak container count')
    containers = []
    for _ in range(count):
        container_name = name()
        size, file_hash = unpack('<qQ')
        if not container_name or '/' in container_name or '\\' in container_name or size < 0:
            raise ValueError('Invalid Pak container entry')
        containers.append((container_name, size, file_hash))
    skip_table(10)  # package ID + container
    skip_table(18)  # path hash + container + package ID
    count = unpack('<i')
    if count < 0 or count > (len(data) - offset) // 12:
        raise ValueError('Invalid Pak path count')
    paths = {}
    for _ in range(count):
        package = name()
        container, _package_id = unpack('<HQ')
        if container == 65535:
            continue
        if container >= len(containers):
            raise ValueError('Invalid Pak path container')
        package = canonical_path(package)
        paths.setdefault(package.casefold(), package)
    if offset != len(data):
        raise ValueError('Unexpected Pak index trailing bytes')
    result = {'path': os.path.abspath(path), 'version': version, 'build': build,
              'depot': depot, 'containers': tuple(containers),
              'paths': tuple(sorted(paths.values(), key=str.casefold)), 'stamp': key}
    # Old revisions of a file need not stay in memory after a reindex.
    for old in list(_INDEX_CACHE):
        if old[0] == key[0]:
            del _INDEX_CACHE[old]
    _INDEX_CACHE[key] = result
    return result


def installed_build(game_dir: str) -> str:
    current = Path(game_dir).absolute()
    for folder in (current, *tuple(current.parents)[:8]):
        if folder.name.casefold() != 'steamapps':
            continue
        manifest = folder / 'appmanifest_1808500.acf'
        try:
            text = manifest.read_text(encoding='utf-8-sig')
        except OSError:
            return ''
        match = re.search(r'"buildid"\s*"(\d{1,32})"', text, re.IGNORECASE)
        return match.group(1) if match else ''
    return ''


def find_index(game_dir: str, index_dir: str) -> dict | None:
    """Select only an index matching the configured game's build/containers."""
    build = installed_build(game_dir)
    if build:
        candidates = [Path(index_dir) / ('pak-index-' + build + '.bin')]
    else:
        candidates = sorted(Path(index_dir).glob('pak-index*.bin'), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    try:
        live = {p.name.casefold(): p.stat().st_size for p in Path(game_dir).iterdir() if p.suffix.casefold() == '.utoc'}
    except OSError:
        return None
    for path in candidates:
        try:
            index = read_index(str(path))
        except (OSError, ValueError, UnicodeError, struct.error):
            continue
        if index['version'] < 6:
            continue  # Current catalogs need data-asset, preview and mount aliases.
        if build and index['build'] != build:
            continue
        if live != {name.casefold(): size for name, size, _ in index['containers']}:
            continue
        return index
    return None


def mesh_paths(index: dict) -> tuple[str, ...]:
    return tuple(path for path in index['paths'] if is_mesh(path))


def browser_tree(index: dict, cache_dir: str, game_dir: str) -> str:
    """Create a build-specific, safe mirror of mesh package paths on demand."""
    signature = hashlib.sha256(repr((index['stamp'], os.path.normcase(os.path.abspath(game_dir)))).encode()).hexdigest()[:12]
    build = index['build'] if index['build'].isdigit() else 'custom'
    root = Path(cache_dir) / 'browser' / (build + '-' + signature)
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    paths = mesh_paths(index)
    marker = root / '.arc-browser.json'
    try:
        previous = json.loads(marker.read_text(encoding='utf-8'))
        if previous.get('signature') == signature and previous.get('meshes') == len(paths):
            return str(root)
    except (OSError, ValueError):
        pass
    for path in paths:
        placeholder = root.joinpath(*path.split('/')).with_suffix('.uasset')
        if not placeholder.resolve().is_relative_to(resolved_root):
            raise ValueError('Pak browser path escapes its catalog root')
        physical = _io_path(placeholder)
        physical.parent.mkdir(parents=True, exist_ok=True)
        # Never truncate existing files. These are selectors, not cache exports.
        if not physical.exists():
            physical.touch(exist_ok=False)
    metadata = {'schema': 1, 'build': index['build'], 'index': index['path'], 'meshes': len(paths), 'signature': signature}
    marker.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return str(root)


def resolve_selection(index: dict, root: str, selections: list[str]) -> list[str]:
    """Resolve selected placeholders/folders back to current indexed packages."""
    base = Path(root).resolve()
    known = {path.casefold(): path for path in mesh_paths(index)}
    found = {}
    for selected in selections:
        path = Path(selected).resolve()
        if not path.is_relative_to(base):
            raise ValueError('Choose a mesh inside the current Pak browser')
        relative = path.relative_to(base).as_posix()
        physical = _io_path(path)
        if physical.is_dir():
            prefix = '' if relative == '.' else relative.casefold().rstrip('/') + '/'
            for key, package in known.items():
                if key.startswith(prefix):
                    found[key] = package
        else:
            if path.suffix.casefold() != '.uasset' or not physical.is_file():
                raise ValueError('Choose an indexed .uasset mesh')
            package = canonical_path(relative)
            if package.casefold() not in known:
                raise ValueError('Selected mesh is absent from the current Pak index')
            found[package.casefold()] = known[package.casefold()]
    if not found:
        raise ValueError('This folder contains no indexed meshes')
    if len(found) > 128:
        raise ValueError('Select a smaller folder or at most 128 meshes per import')
    return list(found.values())
