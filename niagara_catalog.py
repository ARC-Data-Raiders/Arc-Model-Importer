"""Searchable catalog of cooked Niagara systems (NS_*.json).

Indexes files under an explicit dump folder or the Pioneer Effects tree.
Marks whether each file carries ShaderLUT data (headless ``niagara-ns-dump``
property export) so the UI can prefer curve-complete dumps over FModel's
Exports+Metadata JSON, which silently drops the LUTs.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import utils

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


def _fingerprint(catalog_root: str) -> tuple:
    root = resolved_catalog_root(catalog_root)
    try:
        mtime = os.path.getmtime(root) if root and os.path.isdir(root) else 0.0
    except OSError:
        mtime = 0.0
    return (root, mtime)


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
    fp = _fingerprint(catalog_root)
    if _cache.get("fingerprint") == fp and _cache.get("entries") is not None:
        return _cache["entries"]

    root = resolved_catalog_root(catalog_root)
    entries = _index_ns_dir(root)
    _cache["fingerprint"] = fp
    _cache["entries"] = entries
    _cache["by_key"] = {e["key"].lower(): e for e in entries}
    _cache["stats"] = {
        "total": len(entries),
        "with_lut": sum(1 for e in entries if e.get("has_lut")),
        "without_lut": sum(1 for e in entries if not e.get("has_lut")),
        "catalog_root": root,
    }
    return entries


def catalog_stats(catalog_root: str = "") -> dict:
    ensure_loaded(catalog_root)
    return dict(_cache.get("stats") or {})


def get_entry(key: str, catalog_root: str = "") -> Optional[dict]:
    if not key or key == "NONE":
        return None
    ensure_loaded(catalog_root)
    return (_cache.get("by_key") or {}).get(key.strip().lower())


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
        if len(out) >= limit:
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
