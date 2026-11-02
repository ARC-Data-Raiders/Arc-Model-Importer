"""Current-native parent preset facts through a reviewed versioned catalog.

The public compatibility API remains, but no historical preset table is read.
Bare preset keys denote only the declared /Game/.../M_Presets namespace. Exact
full references never borrow a matching basename from another mount.
"""
from __future__ import annotations

import json
import os

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
    from ..material_runtime.native_data import current_presets
    return current_presets()


def _alias_index() -> dict:
    """Lowercase preset key/name -> canonical key, for loose parent lookups."""
    global _ALIAS_CACHE
    index = {}
    for key, preset in _load_table().items():
        index.setdefault(key.lower(), key)
        full=str((preset or {}).get('package') or '')
        if full:
            from ..surface_rebuild.assets import package
            index.setdefault(full.casefold(),key);index.setdefault(package(full).casefold(),key)
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
    if isinstance(preset,dict) and preset.get('_native_render_limit'):
        from ..surface_rebuild.native_catalog_bridge import NativeRenderLimitationError
        raise NativeRenderLimitationError('Current native preset bindings unresolved: '+str(key))
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


def source_signature():
    """Current reviewed bank identity for retained profile cache invalidation."""
    from ..material_runtime.native_data import reviewed_source
    source=reviewed_source()
    return None if source is None else source['provenance']
