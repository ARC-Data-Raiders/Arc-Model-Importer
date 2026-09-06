"""Parent-preset parameter defaults mined from the cooked ``M_Presets`` dumps.

Compact MI dumps only record the overrides an artist changed, so a shader built
from the MI alone is missing most of the preset's authored behaviour (the
elevator's ``1. Wear CR`` rusted-metal texture, for instance, lives only in
``M_TrimMap_01``). ``assets/preset_defaults.json`` is generated offline by
``map_tools/mine_preset_defaults.py`` and read here.

Table shape::

    {"format": 1, "presets": {"M_TrimMap_01": {
        "scalars": {name: float}, "vectors": {name: [r,g,b,a]},
        "switches": {name: bool}, "texture_params": [name],
        "texture_defaults": {name: "/Game/..."},
        "referenced_textures": ["/Game/..."]}}}
"""
from __future__ import annotations

import json
import os

_TABLE_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "assets", "preset_defaults.json",
))

# None = not loaded yet, {} = loaded but unavailable/empty (do not retry).
_TABLE_CACHE: dict | None = None
_ALIAS_CACHE: dict | None = None

_EMPTY_PRESET = {
    "scalars": {},
    "vectors": {},
    "switches": {},
    "texture_params": [],
    "texture_defaults": {},
    "referenced_textures": [],
}


def _load_table() -> dict:
    global _TABLE_CACHE
    if _TABLE_CACHE is not None:
        return _TABLE_CACHE
    presets = {}
    try:
        with open(_TABLE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        raw = data.get("presets") if isinstance(data, dict) else None
        if isinstance(raw, dict):
            presets = raw
    except (OSError, ValueError):
        presets = {}
    _TABLE_CACHE = presets
    return presets


def _alias_index() -> dict:
    """Lowercase preset key/name -> canonical key, for loose parent lookups."""
    global _ALIAS_CACHE
    if _ALIAS_CACHE is not None:
        return _ALIAS_CACHE
    index = {}
    for key, preset in _load_table().items():
        index.setdefault(key.lower(), key)
        name = str((preset or {}).get("name") or "")
        if name:
            index.setdefault(name.lower(), key)
    _ALIAS_CACHE = index
    return index


def clear_preset_cache() -> None:
    """Drop the in-memory table (used by the addon's session cache reset)."""
    global _TABLE_CACHE, _ALIAS_CACHE
    _TABLE_CACHE = None
    _ALIAS_CACHE = None


def have_preset_defaults() -> bool:
    return bool(_load_table())


def preset_keys() -> list:
    return sorted(_load_table().keys())


def get_preset(key: str) -> dict:
    """Preset default block by key or parent material name; empty when absent."""
    if not key:
        return _EMPTY_PRESET
    table = _load_table()
    preset = table.get(key)
    if preset is None:
        canon = _alias_index().get(str(key).strip().lower())
        preset = table.get(canon) if canon else None
    if not isinstance(preset, dict):
        return _EMPTY_PRESET
    return preset


def has_preset(key: str) -> bool:
    if not key:
        return False
    table = _load_table()
    return key in table or str(key).strip().lower() in _alias_index()


def preset_scalars(key: str) -> dict:
    return get_preset(key).get("scalars") or {}


def preset_vectors(key: str) -> dict:
    return get_preset(key).get("vectors") or {}


def preset_switches(key: str) -> dict:
    return get_preset(key).get("switches") or {}


def preset_texture_defaults(key: str) -> dict:
    return get_preset(key).get("texture_defaults") or {}


def preset_texture_params(key: str) -> list:
    return get_preset(key).get("texture_params") or []


def preset_referenced_textures(key: str) -> list:
    return get_preset(key).get("referenced_textures") or []


def preset_has_params(key: str, *names: str) -> bool:
    """True when every name exists in the preset's scalar/vector/switch/texture set."""
    preset = get_preset(key)
    if not preset:
        return False
    pool = set(preset.get("scalars") or ())
    pool |= set(preset.get("vectors") or ())
    pool |= set(preset.get("switches") or ())
    pool |= set(preset.get("texture_params") or ())
    return all(n in pool for n in names)


def find_referenced_texture(key: str, *tokens: str) -> str:
    """First referenced texture whose stem contains all *tokens* (case-insensitive)."""
    if not tokens:
        return ""
    want = [t.lower() for t in tokens if t]
    for path in preset_referenced_textures(key):
        stem = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if all(tok in stem for tok in want):
            return path
    return ""
