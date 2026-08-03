"""
FMDex index reader — resolve UE package basenames to paths/tags from FModel.

On-disk layout (v5+ flatUnique): root ``*`` holds same-tag basename batches;
colliding basenames keep a nested ``tree``. Indexes are ``*_FMDex.json.br``
(Brotli) or plain ``*_FMDex.json`` / legacy ``*_FDex.*``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("arc_raiders.fmdex")

FILE_GROUPS_KEY = "*"
LAYOUT_FLAT_UNIQUE = "flatUnique"

# Module-level cache — invalidated when the resolved index path changes.
_cache: Dict[str, Any] = {
    "root": None,          # directory used for discovery
    "index_path": None,    # loaded file
    "flat": None,          # package path/basename -> tags (case-insensitive keys)
    "by_stem": None,       # stem.lower() -> (package_key, tags)
    "error": None,         # last load error message
    "entry_count": 0,
    # stem+ext -> path or "" (including misses — Pioneer walks are expensive)
    "export_file": {},
}


# ---------------------------------------------------------------------------
# Brotli / JSON IO
# ---------------------------------------------------------------------------

def _decompress_brotli(raw: bytes) -> bytes:
    """Decompress Brotli bytes. Prefer ``brotli``, then ``brotlicffi``."""
    try:
        import brotli  # type: ignore
        return brotli.decompress(raw)
    except ImportError:
        pass
    try:
        import brotlicffi  # type: ignore
        return brotlicffi.decompress(raw)
    except ImportError:
        pass
    raise RuntimeError(
        "Brotli module not available in Blender's Python. "
        "Install with: Blender's python -m pip install brotli  "
        "— or place an uncompressed *_FMDex.json next to the .br file."
    )


def _read_index_text(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".br"):
        # Prefer sibling uncompressed JSON when brotli is missing
        plain = path[:-3] if lower.endswith(".br") else path
        if plain.lower().endswith(".json") and not _brotli_available():
            if os.path.isfile(plain):
                log.info("FMDex: using uncompressed sibling %s (brotli not installed)", plain)
                with open(plain, "r", encoding="utf-8") as fh:
                    return fh.read()
        with open(path, "rb") as fh:
            raw = fh.read()
        try:
            return _decompress_brotli(raw).decode("utf-8")
        except RuntimeError:
            if os.path.isfile(plain):
                with open(plain, "r", encoding="utf-8") as fh:
                    return fh.read()
            raise
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _brotli_available() -> bool:
    try:
        import brotli  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        import brotlicffi  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Tag-batch / tree expand (port of FMDexTreeConverter + FMDexFlatUnique)
# ---------------------------------------------------------------------------

def _try_digit(s: str) -> Optional[int]:
    if not s or not s.isdigit():
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _expand_digit_range(leaf: str) -> Optional[List[str]]:
    """Expand ``01-13`` into zero-padded consecutive integers (inclusive)."""
    dash = leaf.find("-")
    if dash <= 0 or dash != leaf.rfind("-") or dash >= len(leaf) - 1:
        return None
    left, right = leaf[:dash], leaf[dash + 1 :]
    start, end = _try_digit(left), _try_digit(right)
    if start is None or end is None or end < start or (end - start) > 100_000:
        return None
    width = len(left)
    return [str(n).zfill(width) for n in range(start, end + 1)]


def _append_name(names: List[str], prefix: str, leaf: str, ext: str) -> None:
    if not prefix and not ext:
        names.append(leaf)
    else:
        names.append(f"{prefix}{leaf}{ext}")


def _expand_f_items(items: list, prefix: str, ext: str, names: List[str]) -> None:
    for token in items:
        if isinstance(token, str):
            if not token.strip():
                continue
            expanded = _expand_digit_range(token)
            if expanded:
                for part in expanded:
                    _append_name(names, prefix, part, ext)
            else:
                _append_name(names, prefix, token, ext)
        elif (
            isinstance(token, list)
            and len(token) == 2
            and isinstance(token[0], str)
            and isinstance(token[1], list)
        ):
            _expand_f_items(token[1], prefix + token[0], ext, names)
        elif isinstance(token, dict) and isinstance(token.get("f"), list):
            nested_prefix = prefix + (token.get("p") or "")
            _expand_f_items(token["f"], nested_prefix, ext, names)


def _read_tag_array(token: Any, legend: Optional[Dict[str, str]] = None) -> List[str]:
    raw: List[str] = []
    if isinstance(token, list):
        raw = [s for s in token if isinstance(s, str) and s.strip()]
    elif isinstance(token, str) and token.strip():
        raw = [token]
    if not legend:
        return raw
    return [legend.get(t, t) for t in raw]


def _try_read_file_group_array(
    arr: list, legend: Optional[Dict[str, str]] = None
) -> Optional[Tuple[List[str], List[str]]]:
    """Positional ``[f,t]`` / ``[p,f,t]`` / ``[f,e,t]`` / ``[p,f,e,t]``."""
    if not isinstance(arr, list):
        return None
    prefix = ""
    ext = ""
    files_arr = None
    tags_tok = None
    n = len(arr)

    if n == 2:
        files_arr, tags_tok = arr[0], arr[1]
    elif n == 3 and isinstance(arr[0], str) and isinstance(arr[1], list):
        prefix, files_arr, tags_tok = arr[0], arr[1], arr[2]
    elif n == 3 and isinstance(arr[0], list) and isinstance(arr[1], str):
        files_arr, ext, tags_tok = arr[0], arr[1], arr[2]
    elif n == 4:
        prefix = arr[0] if isinstance(arr[0], str) else ""
        files_arr = arr[1] if isinstance(arr[1], list) else None
        ext = arr[2] if isinstance(arr[2], str) else ""
        tags_tok = arr[3]
    else:
        return None

    if not isinstance(files_arr, list) or tags_tok is None:
        return None

    names: List[str] = []
    _expand_f_items(files_arr, prefix, ext, names)
    tags = _read_tag_array(tags_tok, legend)
    if not names:
        return None
    return names, tags


def _try_read_file_group_obj(
    obj: dict, legend: Optional[Dict[str, str]] = None
) -> Optional[Tuple[List[str], List[str]]]:
    files_arr = obj.get("f")
    if not isinstance(files_arr, list) or obj.get("t") is None:
        return None
    prefix = obj.get("p") or ""
    ext = obj.get("e") or ""
    if not isinstance(prefix, str):
        prefix = ""
    if not isinstance(ext, str):
        ext = ""
    names: List[str] = []
    _expand_f_items(files_arr, prefix, ext, names)
    tags = _read_tag_array(obj.get("t"), legend)
    if not names:
        return None
    return names, tags


def _try_read_file_group_token(
    token: Any, legend: Optional[Dict[str, str]] = None
) -> Optional[Tuple[List[str], List[str]]]:
    if isinstance(token, dict):
        return _try_read_file_group_obj(token, legend)
    if isinstance(token, list):
        return _try_read_file_group_array(token, legend)
    return None


def _read_tag_batches(
    batches: list, legend: Optional[Dict[str, str]] = None
) -> List[Tuple[List[str], List[str]]]:
    result = []
    if not isinstance(batches, list):
        return result
    for item in batches:
        parsed = _try_read_file_group_token(item, legend)
        if parsed:
            result.append(parsed)
    return result


def _normalize_path(path: str) -> str:
    if not path:
        return path
    return path.replace("\\", "/").lstrip("/")


def _package_file_name(package_path: str) -> str:
    if not package_path:
        return package_path
    i = max(package_path.rfind("/"), package_path.rfind("\\"))
    return package_path if i < 0 else package_path[i + 1 :]


def _read_folder(
    obj: dict, legend: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Return nested tree: name -> {'tags': [...]} | {'children': {...}}."""
    if not isinstance(obj, dict):
        return {}

    # Whole folder collapsed to a single file-group object
    collapsed = _try_read_file_group_obj(obj, legend)
    if collapsed and "f" in obj and "t" in obj and FILE_GROUPS_KEY not in obj:
        # Only treat as collapsed group when it looks like {p?,f,e?,t} without folder children
        keys = set(obj.keys())
        if keys <= {"p", "f", "e", "t"}:
            only = {}
            names, tags = collapsed
            for name in names:
                only[name] = {"tags": tags}
            return only

    result: Dict[str, Any] = {}
    for key, value in obj.items():
        if key == FILE_GROUPS_KEY:
            if isinstance(value, list):
                for item in value:
                    parsed = _try_read_file_group_token(item, legend)
                    if not parsed:
                        continue
                    names, tags = parsed
                    for name in names:
                        result[name] = {"tags": tags}
            continue

        if isinstance(value, list):
            parsed = _try_read_file_group_array(value, legend)
            if parsed:
                names, tags = parsed
                if len(names) == 1 and names[0].lower() == key.lower():
                    result[key] = {"tags": tags}
                else:
                    for name in names:
                        result[name] = {"tags": tags}
                continue

        if not isinstance(value, dict):
            continue

        # Single file leaf: {"t": [...]}
        if (
            "t" in value
            and "f" not in value
            and "p" not in value
            and all(k == "t" for k in value.keys())
        ):
            result[key] = {"tags": _read_tag_array(value["t"], legend)}
            continue

        result[key] = {"children": _read_folder(value, legend)}

    return result


def _flatten_tree(tree: Dict[str, Any], prefix: str = "") -> Dict[str, List[str]]:
    flat: Dict[str, List[str]] = {}
    for name, node in (tree or {}).items():
        if name == FILE_GROUPS_KEY:
            continue
        path = name if not prefix else f"{prefix}/{name}"
        if not isinstance(node, dict):
            continue
        if "tags" in node:
            flat[_normalize_path(path)] = list(node["tags"] or [])
        elif "children" in node:
            flat.update(_flatten_tree(node["children"], path))
    return flat


def _deserialize_flat_unique(
    jo: dict, legend: Optional[Dict[str, str]] = None
) -> Dict[str, List[str]]:
    """Build path/basename → tags map (case-insensitive dict)."""
    flat: Dict[str, List[str]] = {}

    batches = jo.get(FILE_GROUPS_KEY)
    if isinstance(batches, list):
        for names, tags in _read_tag_batches(batches, legend):
            if not tags:
                continue
            for name in names:
                key = _normalize_path(name)
                if key:
                    flat[key] = list(tags)

    tree_obj = jo.get("tree")
    if isinstance(tree_obj, dict):
        tree = _read_folder(tree_obj, legend)
        for path, tags in _flatten_tree(tree).items():
            if path and tags:
                flat[path] = tags

    # Case-insensitive access via lower-key shadow — store with original keys
    # but lookups go through by_stem / _ci_get.
    return flat


def _is_flat_unique(jo: dict) -> bool:
    layout = (jo.get("layout") or "").lower()
    if layout == LAYOUT_FLAT_UNIQUE.lower():
        return True
    ver = jo.get("version")
    try:
        ver_i = int(ver) if ver is not None else 0
    except (TypeError, ValueError):
        ver_i = 0
    return ver_i >= 5 and isinstance(jo.get(FILE_GROUPS_KEY), list)


def _deserialize_legacy(jo: dict, legend: Optional[Dict[str, str]] = None) -> Dict[str, List[str]]:
    flat: Dict[str, List[str]] = {}
    tree_obj = jo.get("tree")
    if isinstance(tree_obj, dict):
        tree = _read_folder(tree_obj, legend)
        flat.update(_flatten_tree(tree))
    entries = jo.get("entries")
    if isinstance(entries, dict):
        for path, entry in entries.items():
            key = _normalize_path(path)
            tags = None
            if isinstance(entry, dict):
                tags = entry.get("tags") or entry.get("Tags")
            if key and isinstance(tags, list) and tags:
                flat[key] = _read_tag_array(tags, legend)
    return flat


def _stem_from_package_key(key: str) -> str:
    base = _package_file_name(key)
    lower = base.lower()
    for suf in (".uasset", ".umap", ".json", ".png"):
        if lower.endswith(suf):
            base = base[: -len(suf)]
            break
    return base


def _build_by_stem(flat: Dict[str, List[str]]) -> Dict[str, Tuple[str, List[str]]]:
    """stem.lower() → (best package key, tags). Prefer full paths over basename-only."""
    by_stem: Dict[str, Tuple[str, List[str]]] = {}
    for key, tags in flat.items():
        stem = _stem_from_package_key(key)
        if not stem:
            continue
        sl = stem.lower()
        existing = by_stem.get(sl)
        if existing is None:
            by_stem[sl] = (key, tags)
            continue
        # Prefer key that contains a directory separator
        old_key = existing[0]
        if "/" not in old_key and "/" in key:
            by_stem[sl] = (key, tags)
    return by_stem


# ---------------------------------------------------------------------------
# Directory discovery
# ---------------------------------------------------------------------------

def _candidate_fmdex_roots() -> List[str]:
    """FMDex folders near this add-on / sibling FModel checkouts (no hardcoded user paths)."""
    roots: List[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        if not path:
            return
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return
        seen.add(key)
        roots.append(path)

    addon_dir = os.path.dirname(os.path.abspath(__file__))
    cur = addon_dir
    for _ in range(6):
        parent = os.path.dirname(cur)
        if not parent or parent == cur:
            break
        for repo_name in ("FModel-Vibe", "FModel"):
            repo = os.path.join(parent, repo_name)
            if not os.path.isdir(repo):
                continue
            _add(os.path.join(repo, "FMDex"))
            fmodel = os.path.join(repo, "FModel")
            if not os.path.isdir(fmodel):
                fmodel = repo
            _add(os.path.join(fmodel, "FMDex"))
            for cfg in ("Release", "Debug"):
                bin_root = os.path.join(fmodel, "bin", cfg, "net10.0-windows")
                _add(os.path.join(bin_root, "win-x64", "publish", "FMDex"))
                _add(os.path.join(bin_root, "win-x64", "FMDex"))
                _add(os.path.join(bin_root, "FMDex"))
        cur = parent
    return roots


def _index_name_match(filename: str) -> bool:
    n = filename.lower()
    return (
        n.endswith("_fmdex.json.br")
        or n.endswith("_fmdex.json")
        or n.endswith("_fdex.json.br")
        or n.endswith("_fdex.json")
    )


def find_fmdex_index_files(root: str, max_depth: int = 4) -> List[str]:
    """Glob ``*_FMDex.json.br`` / plain / legacy under root (bounded depth)."""
    if not root or not os.path.isdir(root):
        return []
    found: List[str] = []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > max_depth:
            dirnames[:] = []
            continue
        # Skip huge/irrelevant trees
        low = dirpath.replace("\\", "/").lower()
        if any(s in low for s in ("/node_modules/", "/.git/", "/obj/", "/packages/")):
            dirnames[:] = []
            continue
        for fname in filenames:
            if _index_name_match(fname):
                found.append(os.path.join(dirpath, fname))
    # Prefer .br over plain twin; de-dupe by stem
    by_stem: Dict[str, str] = {}
    for path in found:
        base = os.path.basename(path)
        stem = base.lower()
        for suf in (".br",):
            if stem.endswith(suf):
                stem = stem[: -len(suf)]
        # Prefer brotli when both exist
        prev = by_stem.get(stem)
        if prev is None:
            by_stem[stem] = path
        elif path.lower().endswith(".br") and not prev.lower().endswith(".br"):
            by_stem[stem] = path
    return sorted(by_stem.values(), key=lambda p: os.path.getmtime(p), reverse=True)


def _pick_best_index(files: List[str]) -> str:
    if not files:
        return ""
    pioneer = [f for f in files if "pioneergame" in os.path.basename(f).lower()]
    pool = pioneer or files
    return max(pool, key=lambda p: os.path.getmtime(p))


def get_fmdex_directory() -> str:
    """Return the FMDex folder to search (scene prop or auto-detect)."""
    # 1) User-set scene property
    try:
        import bpy

        prop = getattr(bpy.context.scene, "arc_fmdex_root", "") or ""
        if prop:
            path = bpy.path.abspath(prop)
            if os.path.isdir(path):
                return path
    except Exception:
        pass

    # 2) Sibling FModel checkouts / build outputs near this add-on
    candidates = _candidate_fmdex_roots()
    for cand in candidates:
        if os.path.isdir(cand) and find_fmdex_index_files(cand, max_depth=3):
            return cand

    # 3) Near Pioneer root
    try:
        from . import utils

        pioneer = utils.get_pioneer_root()
        if pioneer:
            for rel in ("FMDex", os.path.join("..", "FMDex"), os.path.join("..", "..", "FMDex")):
                cand = os.path.abspath(os.path.join(pioneer, rel))
                if os.path.isdir(cand) and find_fmdex_index_files(cand, max_depth=3):
                    return cand
            # Walk up a few parents looking for FMDex
            cur = pioneer
            for _ in range(5):
                parent = os.path.dirname(cur)
                if not parent or parent == cur:
                    break
                cand = os.path.join(parent, "FMDex")
                if os.path.isdir(cand) and find_fmdex_index_files(cand, max_depth=3):
                    return cand
                cur = parent
    except Exception:
        pass

    # 4) First sibling candidate that exists (even without indexes yet)
    for cand in candidates:
        if os.path.isdir(cand):
            return cand
    return ""


# ---------------------------------------------------------------------------
# Load / cache / lookup
# ---------------------------------------------------------------------------

def invalidate_cache() -> None:
    _cache["root"] = None
    _cache["index_path"] = None
    _cache["flat"] = None
    _cache["by_stem"] = None
    _cache["error"] = None
    _cache["entry_count"] = 0
    _cache["export_file"] = {}


def load_fmdex(path: str) -> Dict[str, List[str]]:
    """Load one index file into a flat path/basename → tags dict."""
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"FMDex index not found: {path}")

    text = _read_index_text(path)
    jo = json.loads(text)
    legend = jo.get("L") if isinstance(jo.get("L"), dict) else None

    if _is_flat_unique(jo):
        flat = _deserialize_flat_unique(jo, legend)
    else:
        flat = _deserialize_legacy(jo, legend)

    log.info(
        "FMDex loaded %s (%d entries, layout=%s, game=%s)",
        path,
        len(flat),
        jo.get("layout") or "?",
        jo.get("game") or "?",
    )
    return flat


def status() -> Dict[str, Any]:
    """Snapshot of cache state for error reports."""
    return {
        "root": _cache.get("root") or "",
        "index_path": _cache.get("index_path") or "",
        "loaded": _cache.get("flat") is not None,
        "entry_count": _cache.get("entry_count") or 0,
        "error": _cache.get("error") or "",
        "brotli": _brotli_available(),
    }


def ensure_loaded(force: bool = False) -> bool:
    """Discover + load the best FMDex index. Returns True if usable."""
    root = get_fmdex_directory()
    files = find_fmdex_index_files(root) if root else []
    best = _pick_best_index(files)

    if (
        not force
        and _cache["flat"] is not None
        and _cache["index_path"] == best
        and _cache["root"] == root
    ):
        return True

    invalidate_cache()
    _cache["root"] = root

    if not best:
        msg = (
            f"No *_FMDex.json.br (or .json) under '{root or '(none)'}'. "
            "Set FMDex Folder in Settings to FModel's FMDex/<Profile> output "
            "(not the FMDex source code folder)."
        )
        if root and not _brotli_available():
            msg += " Note: brotli is not installed; uncompressed .json also works."
        _cache["error"] = msg
        log.warning(msg)
        return False

    try:
        flat = load_fmdex(best)
    except Exception as exc:
        msg = f"FMDex load failed ({best}): {exc}"
        _cache["error"] = msg
        log.error(msg)
        return False

    _cache["index_path"] = best
    _cache["flat"] = flat
    _cache["by_stem"] = _build_by_stem(flat)
    _cache["entry_count"] = len(flat)
    _cache["error"] = None
    return True


def lookup_asset_path(stem: str) -> Tuple[str, List[str]]:
    """Given ``MI_CatBed_01_A`` / ``SM_CatBed_01_A``, return (package_key, tags).

    ``package_key`` may be a full path (``PioneerGame/Content/Pioneer/.../X.uasset``)
    or a basename-only key from flatUnique (``MI_CatBed_01_A.uasset``).
    Returns ``("", [])`` when not found / index missing.
    """
    if not stem:
        return "", []
    stem = stem.strip()
    stem = re.sub(r"\.mat$", "", stem, flags=re.IGNORECASE)
    stem = stem.split(".")[0].strip()
    # Strip package extension if caller passed one
    for suf in (".uasset", ".umap"):
        if stem.lower().endswith(suf):
            stem = stem[: -len(suf)]
            break

    if not ensure_loaded():
        return "", []

    by_stem: Dict[str, Tuple[str, List[str]]] = _cache.get("by_stem") or {}
    hit = by_stem.get(stem.lower())
    if hit:
        return hit[0], list(hit[1] or [])

    # Also try with common prefixes if bare body was given
    if not re.match(r"^(SM|SK|MI|T)_", stem, flags=re.IGNORECASE):
        for prefix in ("SM_", "SK_", "MI_", "T_"):
            hit = by_stem.get((prefix + stem).lower())
            if hit:
                return hit[0], list(hit[1] or [])

    return "", []


def package_to_game_path(package_key: str) -> str:
    """Convert index key to a ``/Game/...`` ObjectPath (no extension).

    Handles:
      - ``PioneerGame/Content/Pioneer/Items/.../MI_X.uasset``
      - ``Pioneer/Items/.../MI_X.uasset``
      - basename-only ``MI_X.uasset`` → empty (no folder info)
    """
    if not package_key:
        return ""
    key = _normalize_path(package_key)
    # Drop extension
    for suf in (".uasset", ".umap"):
        if key.lower().endswith(suf):
            key = key[: -len(suf)]
            break

    # Basename only — cannot form a Game path
    if "/" not in key:
        return ""

    lower = key.lower()
    for marker in ("content/",):
        idx = lower.find(marker)
        if idx >= 0:
            rel = key[idx + len(marker) :]
            return "/Game/" + rel.lstrip("/")

    if lower.startswith("game/"):
        return "/" + key
    if lower.startswith("pioneer/"):
        return "/Game/" + key
    # Already looks like PioneerGame/... without Content — keep last meaningful tail
    return "/Game/" + key


def resolve_export_file(stem: str, extension: str = ".json") -> str:
    """Resolve an exported file on disk via FMDex path + Pioneer content root."""
    from . import textures

    if not stem:
        return ""
    if not extension.startswith("."):
        extension = "." + extension
    cache_key = f"{stem.strip().lower()}|{extension.lower()}"
    export_cache: Dict[str, str] = _cache.setdefault("export_file", {})
    if cache_key in export_cache:
        return export_cache[cache_key]

    def _remember(path: str) -> str:
        export_cache[cache_key] = path or ""
        return path or ""

    pkg, tags = lookup_asset_path(stem)
    if not pkg:
        return _remember("")

    game_path = package_to_game_path(pkg)
    if game_path:
        found = textures.find_asset_from_object_path(game_path, extension)
        if found:
            return _remember(found)

    # Basename-only (flatUnique unique names): search Pioneer for stem+ext
    return _remember(
        _search_content_by_basename(_stem_from_package_key(pkg) or stem, extension)
    )


def resolve_mesh_asset_folder(stem: str) -> str:
    """Return folder containing mesh JSON/PSK for an SM_/SK_ stem, or \"\"."""
    for ext in (".json", ".psk", ".pskx"):
        found = resolve_export_file(stem, ext)
        if found:
            return os.path.dirname(found)
    return ""


def _search_content_by_basename(stem: str, extension: str, max_visited: int = 40000) -> str:
    from . import utils

    if not stem:
        return ""
    if not extension.startswith("."):
        extension = "." + extension
    target = (stem + extension).lower()
    # Separate walk cache so resolve_export_file can short-circuit before walking
    walk_key = f"walk|{target}"
    export_cache: Dict[str, str] = _cache.setdefault("export_file", {})
    if walk_key in export_cache:
        return export_cache[walk_key]

    root = utils.get_pioneer_root()
    if not root:
        export_cache[walk_key] = ""
        return ""
    content_dir = utils.find_content_dir(root)
    pioneer = os.path.join(content_dir, "Pioneer") if content_dir else ""
    if not pioneer or not os.path.isdir(pioneer):
        export_cache[walk_key] = ""
        return ""

    visited = 0
    for walk_root, dirs, files in os.walk(pioneer):
        visited += 1
        if visited > max_visited:
            break
        low = walk_root.replace("\\", "/").lower()
        if any(s in low for s in ("/saved/", "/intermediate/", "/deriveddatacache/")):
            dirs[:] = []
            continue
        for fname in files:
            if fname.lower() == target:
                path = os.path.join(walk_root, fname)
                export_cache[walk_key] = path
                return path
    export_cache[walk_key] = ""
    return ""


def fmdex_summary_for_report() -> str:
    """Short status string for operator error reports."""
    st = status()
    if st["loaded"]:
        base = os.path.basename(st["index_path"]) if st["index_path"] else "?"
        return f"FMDex=yes ({st['entry_count']} ents, {base})"
    err = (st["error"] or "not loaded")[:80]
    return f"FMDex=no ({err})"
