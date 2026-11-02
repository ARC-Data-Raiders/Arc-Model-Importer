"""Packaged Niagara browse metadata with explicit-action source resolution."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import utils
from . import fmdex

_cache: Dict[str, Any] = {
    "fingerprint": None,
    "entries": None,
    "by_key": None,
    "stats": None,
}

# Cheap LUT probe: read this many bytes looking for the ShaderLUT token.
_LUT_PROBE_BYTES = 131072


def invalidate() -> None:
    _cache["fingerprint"] = None
    _cache["entries"] = None
    _cache["by_key"] = None
    _cache["stats"] = None


def resolved_catalog_root(catalog_root: str = "") -> str:
    """Folder to scan for ``NS_*.json``.

    Order: explicit catalog root → Pioneer ``…/Effects`` → empty.
    """
    if catalog_root:
        path = os.path.abspath(catalog_root)
        if os.path.isdir(path):
            return path

    pioneer = utils.get_pioneer_root() or ""
    if not pioneer or not os.path.isdir(pioneer):
        return catalog_root or ""

    candidates = (
        os.path.join(pioneer, "Content", "Pioneer", "Effects"),
        os.path.join(pioneer, "PioneerGame", "Content", "Pioneer", "Effects"),
        os.path.join(pioneer, "Pioneer", "Effects"),
        os.path.join(pioneer, "Effects"),
    )
    for cand in candidates:
        if os.path.isdir(cand):
            return os.path.abspath(cand)
    return ""


def resolved_asset_root(asset_root: str = "") -> str:
    """Mesh search root: explicit asset root, else Pioneer root."""
    if asset_root:
        path = os.path.abspath(asset_root)
        if os.path.isdir(path):
            return path
    pioneer = utils.get_pioneer_root() or ""
    if pioneer and os.path.isdir(pioneer):
        return os.path.abspath(pioneer)
    return ""




def _folder_label(path: str, root: str) -> str:
    """Relative folder under the catalog root (Effects-relative when possible)."""
    try:
        rel = os.path.relpath(os.path.dirname(path), root)
    except ValueError:
        return ""
    if rel in (".", ""):
        return ""
    parts = rel.replace("\\", "/").split("/")
    # Drop a leading PioneerGame/Content/Pioneer/Effects prefix if present.
    lower = [p.lower() for p in parts]
    if "effects" in lower:
        i = lower.index("effects")
        parts = parts[i + 1 :]
    return "/".join(parts)


def _probe_has_lut(path: str) -> bool:
    """True when the JSON blob mentions ShaderLUT in the first probe window."""
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(_LUT_PROBE_BYTES)
    except OSError:
        return False
    return b"ShaderLUT" in chunk


def _index_ns_dir(root: str) -> List[dict]:
    if not root or not os.path.isdir(root):
        return []
    out: List[dict] = []
    for walk_root, _dirs, files in os.walk(root):
        for name in files:
            if not name.startswith("NS_") or not name.lower().endswith(".json"):
                continue
            if name.startswith("_"):
                continue
            path = os.path.join(walk_root, name)
            stem = name[:-5] if name.lower().endswith(".json") else name
            folder = _folder_label(path, root)
            out.append(
                {
                    "key": stem,
                    "stem": stem,
                    "path": path,
                    "folder": folder,
                    "has_lut": _probe_has_lut(path),
                    "size": os.path.getsize(path) if os.path.isfile(path) else 0,
                }
            )
    out.sort(key=lambda e: ((e.get("folder") or "").lower(), (e.get("stem") or "").lower()))
    return out


def ensure_loaded(catalog_root: str = "") -> List[dict]:
    """Return shipped systems; the root override is used only when importing."""
    if _cache.get("entries") is not None:
        return _cache["entries"]
    entries, stats = fmdex.packaged_browser("effects")
    for entry in entries:
        entry["path"] = ""
    _cache["entries"] = entries
    _cache["by_key"] = {entry["key"].lower(): entry for entry in entries}
    _cache["stats"] = stats
    return entries


def resolve_entry_path(entry: dict, catalog_root: str = "", scene=None) -> str:
    """Resolve an actual NS JSON only for an explicit import or playback action."""
    if not entry:
        return ""
    if not catalog_root and scene is not None:
        catalog_root = getattr(scene, "arc_ns_catalog_root", "") or ""
    try:
        import bpy
        catalog_root = bpy.path.abspath(catalog_root) if catalog_root else ""
    except ImportError:
        pass
    existing = entry.get("path") or ""
    if existing and os.path.isfile(existing):
        return existing
    found = fmdex.resolve_catalog_file(entry, ".json", catalog_root)
    if found:
        return found
    if (entry.get("key") or entry.get("stem")) != entry.get("stem"):
        return ""
    root = resolved_catalog_root(catalog_root)
    matches = [candidate for candidate in _index_ns_dir(root)
               if candidate["stem"].lower() == (entry.get("stem") or "").lower()]
    if len(matches) == 1:
        return matches[0]["path"]
    return fmdex.resolve_export_file(entry.get("stem") or "", ".json") or ""


def catalog_stats(catalog_root: str = "") -> dict:
    ensure_loaded(catalog_root)
    return dict(_cache.get("stats") or {})


def get_entry(key: str, catalog_root: str = "") -> Optional[dict]:
    if not key or key == "NONE":
        return None
    ensure_loaded(catalog_root)
    entry = (_cache.get("by_key") or {}).get(key.strip().lower())
    return dict(entry) if entry is not None else None


def display_label(entry: dict) -> str:
    stem = entry.get("stem") or entry.get("key") or "?"
    folder = entry.get("folder") or ""
    lut = "LUT" if entry.get("has_lut") else "no-LUT"
    if folder:
        return f"{stem}  ·  {folder}  ·  {lut}"
    return f"{stem}  ·  {lut}"


def _query_match(entry: dict, query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    hay = " ".join(
        (
            entry.get("stem") or "",
            entry.get("folder") or "",
            "lut" if entry.get("has_lut") else "nolut",
        )
    ).lower()
    return all(tok in hay for tok in q.split())


def filter_entries(
    query: str = "",
    *,
    catalog_root: str = "",
    lut_only: bool = False,
    limit: int = 12,
) -> List[dict]:
    entries = ensure_loaded(catalog_root)
    out: List[dict] = []
    for entry in entries:
        if lut_only and not entry.get("has_lut"):
            continue
        if not _query_match(entry, query):
            continue
        out.append(entry)
        if limit and len(out) >= limit:
            break
    return out


def count_entries(
    query: str = "",
    *,
    catalog_root: str = "",
    lut_only: bool = False,
) -> int:
    entries = ensure_loaded(catalog_root)
    n = 0
    for entry in entries:
        if lut_only and not entry.get("has_lut"):
            continue
        if _query_match(entry, query):
            n += 1
    return n
