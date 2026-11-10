"""Shared outfit precompute loader (assets/precompute/). No bpy.

Every miss returns None so callers fall back to live ArcPak extraction.
"""
import json
import os

_ADDON = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_root = os.path.join(_ADDON, "assets", "precompute")
_state = {}


def set_root(path):
    """Point the loader at another precompute folder (tests)."""
    global _root
    _root = path
    _state.clear()


def _load(name):
    if name not in _state:
        try:
            with open(os.path.join(_root, name), encoding="utf-8") as fh:
                _state[name] = json.load(fh)
        except (OSError, ValueError):
            _state[name] = None
    return _state[name]


def _key(path):
    return (path or "").split(".")[0]


def _detect_build():
    cache = os.path.join(_ADDON, "ArcPak", "cache", "browser")
    best = None
    try:
        names = sorted(os.listdir(cache))
    except OSError:
        return None
    for name in names:
        try:
            with open(os.path.join(cache, name, ".arc-browser.json"), encoding="utf-8") as fh:
                d = json.load(fh)
            k = (int(d.get("build", 0)), d.get("signature", ""))
        except (OSError, ValueError, TypeError):
            continue
        if best is None or k > best:
            best = k
    return (str(best[0]), best[1]) if best else None


def manifest_ok(build=None, mapping_sha=None):
    """True when the manifest exists and matches the game build (and mapping sha if given)."""
    m = _load("manifest.json")
    if not m:
        return False
    if build is None:
        det = _detect_build()
        if det is None:
            return False
        build, mapping_sha = det
    if str(m.get("build")) != str(build):
        return False
    return mapping_sha is None or m.get("mapping_sha") == mapping_sha


def parent_info(path, **build):
    if not manifest_ok(**build):
        return None
    parents = _load("parents.json") or {}
    return parents.get(_key(path))


def shared_texture_path(game_path, build=None, mapping_sha=None):
    if isinstance(build, dict) or (build is not None and mapping_sha is None and not isinstance(build, str)):
        return shared_texture(game_path, build)
    if not manifest_ok(build=build, mapping_sha=mapping_sha):
        return None
    rec = ((_load("manifest.json") or {}).get("textures") or {}).get(_key(game_path))
    if not rec:
        return None
    p = os.path.join(_root, *rec["file"].split("/"))
    return p if os.path.isfile(p) else None


def _build_kw(build):
    if build is None:
        return {}
    if isinstance(build, dict):
        return {"build": build.get("build"), "mapping_sha": build.get("mapping_sha")}
    return {"build": str(build)}


def lookup_parent(path, build=None):
    """Parent table for an M_*/library parent path, or None (absent folder, unknown, build mismatch)."""
    return parent_info(path, **_build_kw(build))


def shared_texture(tex_path, build=None):
    return shared_texture_path(tex_path, **_build_kw(build))
