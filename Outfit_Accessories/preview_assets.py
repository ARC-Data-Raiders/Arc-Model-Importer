"""Stable bundled AVIF previews and their native Blender pixel cache."""
from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
import zlib

_ROOT = Path(__file__).resolve().parents[1]
_FOLDER = _ROOT / 'assets/preview_thumbnails'


@lru_cache(maxsize=1)
def _manifest_data():
    try:
        return json.loads((_FOLDER / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=1)
def manifest():
    return _manifest_data().get('entries', {})


def thumbnail_pixels(thumbnail):
    path = Path(thumbnail).resolve()
    try:
        relative = path.relative_to(_ROOT.resolve()).as_posix()
    except ValueError:
        return None
    cache = _manifest_data().get('pixel_cache', {})
    entry = cache.get('entries', {}).get(relative)
    if not entry or cache.get('file') != 'pixels.bin':
        return None
    try:
        width, height = entry['width'], entry['height']
        if not (0 < width <= 4096 and 0 < height <= 4096):
            return None
        with (_FOLDER / 'pixels.bin').open('rb') as stream:
            stream.seek(entry['offset'])
            compressed = stream.read(entry['length'])
        pixels = zlib.decompress(compressed)
        if len(pixels) != width * height * 4:
            return None
        return (width, height), pixels
    except (OSError, ValueError, KeyError, TypeError, zlib.error):
        return None


@lru_cache(maxsize=4096)
def _entry(path):
    try:
        relative = os.path.relpath(os.path.abspath(path), _ROOT).replace('\\', '/')
    except (ValueError, OSError):
        return {}
    return manifest().get(relative, {})


@lru_cache(maxsize=4096)
def low_resolution_path(path):
    thumbnail = _entry(path).get('thumbnail', '')
    if not thumbnail:
        return ''
    candidate = (_ROOT / thumbnail).resolve()
    return str(candidate) if candidate.is_relative_to(_ROOT.resolve()) else ''


def source_preview_path(path, context=None):
    return path


def unregister():
    low_resolution_path.cache_clear()
    _entry.cache_clear()
    manifest.cache_clear()
    _manifest_data.cache_clear()

