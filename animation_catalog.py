"""Searchable animation index — FMDex names plus on-disk PSA / notify sidecars.

The blend never holds every sequence. The catalog is a list of names; Apply
imports one PSA onto the selected armature and optionally spawns notify props.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import fmdex
from . import utils

log = utils.get_logger()

ANIM_TAGS = (
    "AnimSequence",
    "AnimMontage",
    "AnimComposite",
)

# Class-name fragments that mean "this package is a playable clip".
_CLIP_TAG_FRAGMENTS = (
    "animsequence",
    "animmontage",
    "animcomposite",
)
_SKIP_TAG_FRAGMENTS = (
    "animblueprint",
    "animnotify",
    "animinstance",
    "animationsharing",
    "animlayer",
)

_ANIM_DIR_NAMES = {
    "animation",
    "animations",
    "anims",
    "animseq",
    "animmontage",
    "animsequences",
    "locomotion",
    "emote",
    "emotes",
}
_STEM_PREFIXES = (
    "as_",
    "anim_",
    "am_",
    "ans_",
    "montage_",
)

# Skip these when walking a content root for .psa (Pioneer dumps are mostly textures).
_SKIP_WALK_DIRS = {
    "textures",
    "materials",
    "maps",
    "ui",
    "localization",
    "audio",
    "sound",
    "niagara",
    "particles",
    "vfx",
    "intermediate",
    "saved",
    "deriveddatacache",
    "__pycache__",
}

_KIND_FROM_TAG = {
    "animsequence": "AnimSequence",
    "animmontage": "AnimMontage",
    "animcomposite": "AnimComposite",
}

# Module cache — rebuilt when FMDex path or animation-cache folder changes.
_cache: Dict[str, Any] = {
    "fingerprint": None,
    "entries": None,  # list[dict]
    "by_key": None,  # stem.lower() -> dict
    "psa_index": None,  # stem.lower() -> {psa, notifies}
    "stats": None,  # dict of source counts
}


def invalidate() -> None:
    _cache["fingerprint"] = None
    _cache["entries"] = None
    _cache["by_key"] = None
    _cache["psa_index"] = None
    _cache["stats"] = None


def _type_leaf(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return text.replace("\\", "/").split("/")[-1].split(".")[-1]


def _kind_from_tags(tags: Iterable[str]) -> str:
    for tag in tags or []:
        low = (tag or "").strip().lower()
        for frag, kind in _KIND_FROM_TAG.items():
            if frag in low:
                return kind
    return "AnimSequence"


def _folder_from_package(package_key: str) -> str:
    path = (package_key or "").replace("\\", "/").strip("/")
    if not path:
        return ""
    if "/" not in path:
        return ""
    folder = path.rsplit("/", 1)[0]
    lower = folder.lower()
    for marker in ("content/pioneer/", "pioneer/"):
        idx = lower.find(marker)
        if idx >= 0:
            folder = folder[idx + len(marker) :]
            break
    return folder.strip("/")


def _path_parts(package_key: str) -> List[str]:
    path = (package_key or "").replace("\\", "/").strip("/").lower()
    if not path:
        return []
    for suf in (".uasset", ".umap", ".json", ".psa"):
        if path.endswith(suf):
            path = path[: -len(suf)]
            break
    return [p for p in path.split("/") if p]


def _tags_look_like_clip(tags: Iterable[str]) -> Optional[bool]:
    """True = playable clip, False = anim-related but not a clip, None = no anim tag."""
    found = [(t or "").strip().lower() for t in (tags or []) if t]
    if not found:
        return None
    if any(any(skip in t for skip in _SKIP_TAG_FRAGMENTS) for t in found):
        if any(any(frag in t for frag in _CLIP_TAG_FRAGMENTS) for t in found):
            return True
        return False
    if any(any(frag in t for frag in _CLIP_TAG_FRAGMENTS) for t in found):
        return True
    return None


def _looks_like_anim(stem: str, package_key: str, tags: Iterable[str]) -> bool:
    clip = _tags_look_like_clip(tags)
    if clip is True:
        return True
    if clip is False:
        return False
    stem_l = (stem or "").strip().lower()
    if stem_l.startswith(("sk_", "sm_", "mi_", "t_", "wbp_", "da_", "abp_", "ns_", "fx_")):
        return False
    if any(stem_l.startswith(p) for p in _STEM_PREFIXES):
        return True
    if "montage" in stem_l:
        return True
    parts = _path_parts(package_key)
    return any(p in _ANIM_DIR_NAMES or p.startswith("animation") for p in parts)


def resolved_cache_dir(cache_dir: str = "") -> str:
    """Animation content root: explicit cache, else PioneerGame root."""
    if cache_dir and os.path.isdir(cache_dir):
        return cache_dir
    pioneer = utils.get_pioneer_root() or ""
    if pioneer and os.path.isdir(pioneer):
        return pioneer
    return cache_dir or ""


def _index_psa_dir(root: str, out: Dict[str, Dict[str, str]], max_psa: int = 50000) -> None:
    """Walk ``root`` for ``.psa`` / ``.notifies.json``. Skips texture/map folders.

    Pioneer dumps are huge; we only count animation files toward ``max_psa``,
    not every PNG/JSON. Animation-named folders are always entered.
    """
    if not root or not os.path.isdir(root):
        return
    found = 0
    for walk_root, dirs, files in os.walk(root):
        keep = []
        for d in dirs:
            dl = d.lower()
            if dl in _ANIM_DIR_NAMES or dl.startswith("animation"):
                keep.append(d)
                continue
            if dl in _SKIP_WALK_DIRS:
                continue
            keep.append(d)
        dirs[:] = keep
        for name in files:
            lower = name.lower()
            if lower.endswith(".psa"):
                stem = os.path.splitext(name)[0]
                slot = out.setdefault(stem.lower(), {})
                if "psa" not in slot:
                    slot["psa"] = os.path.join(walk_root, name)
                    found += 1
            elif lower.endswith(".notifies.json"):
                stem = name[: -len(".notifies.json")]
                slot = out.setdefault(stem.lower(), {})
                if "notifies" not in slot:
                    slot["notifies"] = os.path.join(walk_root, name)
                    found += 1
            elif lower.endswith(".json"):
                parts = walk_root.replace("\\", "/").lower().split("/")
                in_anim = any(p in _ANIM_DIR_NAMES or p.startswith("animation") for p in parts)
                if not in_anim:
                    continue
                stem = os.path.splitext(name)[0]
                sl = stem.lower()
                if sl.startswith(("sk_", "sm_", "mi_", "t_", "wbp_", "da_", "abp_", "ns_", "fx_")):
                    continue
                slot = out.setdefault(sl, {})
                if "json" not in slot:
                    slot["json"] = os.path.join(walk_root, name)
                    found += 1
            if found >= max_psa:
                return


def _psa_index(cache_dir: str) -> Dict[str, Dict[str, str]]:
    cached = _cache.get("psa_index")
    if cached is not None:
        return cached
    out: Dict[str, Dict[str, str]] = {}
    root = resolved_cache_dir(cache_dir)
    _index_psa_dir(root, out)
    pioneer = utils.get_pioneer_root()
    if pioneer and os.path.normpath(pioneer) != os.path.normpath(root or ""):
        parent = os.path.dirname(pioneer.rstrip("\\/"))
        for name in ("animations", "Animations", "Output", "Exports"):
            _index_psa_dir(os.path.join(parent, name), out)
        _index_psa_dir(os.path.join(pioneer, "animations"), out)
        _index_psa_dir(os.path.join(pioneer, "Animations"), out)
    _cache["psa_index"] = out
    return out


def _fingerprint(cache_dir: str) -> tuple:
    fmdex.ensure_loaded()
    st = fmdex.status()
    return (
        st.get("index_path") or "",
        os.path.normpath(resolved_cache_dir(cache_dir) or ""),
        utils.get_pioneer_root() or "",
    )


def _entry_from_fmdex(stem: str, package_key: str, tags: List[str], psa_idx: Dict[str, Dict[str, str]]) -> dict:
    disk = psa_idx.get(stem.lower()) or {}
    kind = _kind_from_tags(tags)
    game_path = fmdex.package_to_game_path(package_key)
    return {
        "key": stem,
        "stem": stem,
        "kind": kind,
        "package": package_key,
        "game_path": game_path,
        "folder": _folder_from_package(package_key) or _folder_from_package(game_path),
        "psa_path": disk.get("psa") or "",
        "notify_path": disk.get("notifies") or "",
        "json_path": "",
        "skeleton": "",
        "length": None,
        "notify_count": None,
        "has_props": False,
        "has_fx": False,
        "enriched": False,
    }


def _put_entry(by_key: Dict[str, dict], entry: dict) -> None:
    key = (entry.get("key") or "").lower()
    if not key:
        return
    existing = by_key.get(key)
    if existing is None:
        by_key[key] = entry
        return
    # Prefer a tagged clip over a path-heuristic row; keep any PSA we already found.
    if existing.get("psa_path") and not entry.get("psa_path"):
        entry["psa_path"] = existing["psa_path"]
    if existing.get("notify_path") and not entry.get("notify_path"):
        entry["notify_path"] = existing["notify_path"]
    if existing.get("kind") != "AnimSequence" and entry.get("kind") == "AnimSequence":
        by_key[key] = entry
        return
    if entry.get("psa_path") and not existing.get("psa_path"):
        existing["psa_path"] = entry["psa_path"]
    if entry.get("notify_path") and not existing.get("notify_path"):
        existing["notify_path"] = entry["notify_path"]


def ensure_loaded(cache_dir: str = "") -> List[dict]:
    """Return animation catalog entries from FMDex tags/paths plus on-disk PSA."""
    fp = _fingerprint(cache_dir)
    if _cache.get("fingerprint") == fp and _cache.get("entries") is not None:
        return _cache["entries"]

    _cache["psa_index"] = None
    psa_idx = _psa_index(cache_dir)

    by_key: Dict[str, dict] = {}
    tagged = 0
    named = 0
    for stem, package_key, tags in fmdex.iter_all_assets():
        if not _looks_like_anim(stem, package_key, tags):
            continue
        entry = _entry_from_fmdex(stem, package_key, tags, psa_idx)
        if _tags_look_like_clip(tags) is True:
            tagged += 1
        else:
            named += 1
        _put_entry(by_key, entry)

    for stem_l, disk in psa_idx.items():
        if stem_l in by_key:
            if disk.get("psa"):
                by_key[stem_l]["psa_path"] = by_key[stem_l].get("psa_path") or disk["psa"]
            if disk.get("notifies"):
                by_key[stem_l]["notify_path"] = by_key[stem_l].get("notify_path") or disk["notifies"]
            if disk.get("json"):
                by_key[stem_l]["json_path"] = by_key[stem_l].get("json_path") or disk["json"]
            continue
        stem = os.path.splitext(
            os.path.basename(disk.get("psa") or disk.get("json") or disk.get("notifies") or stem_l)
        )[0]
        _put_entry(
            by_key,
            {
                "key": stem,
                "stem": stem,
                "kind": "AnimSequence",
                "package": "",
                "game_path": "",
                "folder": "",
                "psa_path": disk.get("psa") or "",
                "notify_path": disk.get("notifies") or "",
                "json_path": disk.get("json") or "",
                "skeleton": "",
                "length": None,
                "notify_count": None,
                "has_props": False,
                "has_fx": False,
                "enriched": False,
            },
        )

    entries = sorted(by_key.values(), key=lambda e: (e.get("folder") or "", e.get("stem") or "").lower())
    _cache["fingerprint"] = fp
    _cache["entries"] = entries
    _cache["by_key"] = {e["key"].lower(): e for e in entries}
    _cache["stats"] = {
        "total": len(entries),
        "fmdex_tagged": tagged,
        "fmdex_named": named,
        "psa_files": sum(1 for e in entries if e.get("psa_path")),
        "cache_dir": resolved_cache_dir(cache_dir),
        "fmdex_loaded": bool(fmdex.status().get("loaded")),
        "fmdex_entries": int(fmdex.status().get("entry_count") or 0),
        "fmdex_error": fmdex.last_error(),
    }
    return entries


def catalog_stats(cache_dir: str = "") -> dict:
    ensure_loaded(cache_dir)
    return dict(_cache.get("stats") or {})


def get_entry(key: str, cache_dir: str = "") -> Optional[dict]:
    if not key or key == "NONE":
        return None
    ensure_loaded(cache_dir)
    return (_cache.get("by_key") or {}).get(key.strip().lower())


def _query_match(entry: dict, query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    hay = " ".join(
        (
            entry.get("stem") or "",
            entry.get("folder") or "",
            entry.get("kind") or "",
            entry.get("skeleton") or "",
        )
    ).lower()
    return all(tok in hay for tok in q.split())


def filter_entries(
    query: str = "",
    *,
    kind: str = "ALL",
    cache_dir: str = "",
    limit: int = 12,
) -> List[dict]:
    entries = ensure_loaded(cache_dir)
    kind_f = (kind or "ALL").strip()
    out: List[dict] = []
    for entry in entries:
        if kind_f not in ("", "ALL") and entry.get("kind") != kind_f:
            continue
        if not _query_match(entry, query):
            continue
        out.append(entry)
        if limit and len(out) >= limit:
            break
    return out


def count_entries(query: str = "", *, kind: str = "ALL", cache_dir: str = "") -> int:
    """Count matches without the display cap (used for '... and N more')."""
    entries = ensure_loaded(cache_dir)
    kind_f = (kind or "ALL").strip()
    n = 0
    for entry in entries:
        if kind_f not in ("", "ALL") and entry.get("kind") != kind_f:
            continue
        if not _query_match(entry, query):
            continue
        n += 1
    return n


def resolve_psa_path(entry: dict, cache_dir: str = "") -> str:
    """Locate a PSA for this catalog row without importing it."""
    if not entry:
        return ""
    existing = entry.get("psa_path") or ""
    if existing and os.path.isfile(existing):
        return existing
    stem = entry.get("stem") or entry.get("key") or ""
    idx = _psa_index(cache_dir)
    hit = (idx.get(stem.lower()) or {}).get("psa") or ""
    if hit and os.path.isfile(hit):
        entry["psa_path"] = hit
        return hit
    found = fmdex.resolve_export_file(stem, ".psa", allow_basename_walk=False)
    if found and os.path.isfile(found):
        entry["psa_path"] = found
        return found
    return ""


def resolve_notify_path(entry: dict, cache_dir: str = "") -> str:
    if not entry:
        return ""
    existing = entry.get("notify_path") or ""
    if existing and os.path.isfile(existing):
        return existing
    psa = resolve_psa_path(entry, cache_dir)
    if psa:
        sibling = os.path.splitext(psa)[0] + ".notifies.json"
        if os.path.isfile(sibling):
            entry["notify_path"] = sibling
            return sibling
    stem = entry.get("stem") or ""
    idx = _psa_index(cache_dir)
    hit = (idx.get(stem.lower()) or {}).get("notifies") or ""
    if hit and os.path.isfile(hit):
        entry["notify_path"] = hit
        return hit
    found = fmdex.resolve_export_file(stem, ".notifies.json", allow_basename_walk=False)
    if found and os.path.isfile(found):
        entry["notify_path"] = found
        return found
    return ""


def _obj_name(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        text = text.replace("\\", "/")
        return text.split("/")[-1].split(".")[-1]
    if isinstance(value, dict):
        for key in ("ObjectName", "Name", "AssetName", "asset_name"):
            got = _obj_name(value.get(key))
            if got:
                return got
        path = value.get("ObjectPath") or value.get("AssetPath") or value.get("object_path") or ""
        return _obj_name(path)
    return ""


def _as_vec(value: Any) -> Tuple[float, float, float]:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return float(value[0]), float(value[1]), float(value[2])
        except (TypeError, ValueError):
            return 0.0, 0.0, 0.0
    if isinstance(value, dict):
        try:
            return (
                float(value.get("X", value.get("x", 0)) or 0),
                float(value.get("Y", value.get("y", 0)) or 0),
                float(value.get("Z", value.get("z", 0)) or 0),
            )
        except (TypeError, ValueError):
            return 0.0, 0.0, 0.0
    return 0.0, 0.0, 0.0


def _as_rot(value: Any) -> Tuple[float, float, float]:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return float(value[0]), float(value[1]), float(value[2])
        except (TypeError, ValueError):
            return 0.0, 0.0, 0.0
    if isinstance(value, dict):
        try:
            return (
                float(value.get("Pitch", value.get("pitch", 0)) or 0),
                float(value.get("Yaw", value.get("yaw", 0)) or 0),
                float(value.get("Roll", value.get("roll", 0)) or 0),
            )
        except (TypeError, ValueError):
            return 0.0, 0.0, 0.0
    return 0.0, 0.0, 0.0


def _notify_from_props(props: dict, *, class_name: str = "", notify_name: str = "") -> dict:
    mesh = (
        _obj_name(props.get("SkeletalMeshProp"))
        or _obj_name(props.get("StaticMeshProp"))
        or _obj_name(props.get("Mesh"))
        or _obj_name(props.get("SkeletalMeshTemplate"))
        or _obj_name(props.get("ActorProp"))
    )
    skel = _obj_name(props.get("SkeletalMeshProp"))
    static = _obj_name(props.get("StaticMeshProp")) or _obj_name(props.get("Mesh"))
    fx = (
        _obj_name(props.get("Template"))
        or _obj_name(props.get("NiagaraSystem"))
        or _obj_name(props.get("FXSystemAsset"))
        or _obj_name(props.get("PSTemplate"))
        or _obj_name(props.get("ParticleSystem"))
        or _obj_name(props.get("NiagaraTemplate"))
    )
    if mesh:
        kind = "prop"
    elif fx:
        kind = "fx"
    else:
        kind = "other"
    scale = _as_vec(props.get("Scale") or props.get("Scale3D") or {"X": 1, "Y": 1, "Z": 1})
    if scale == (0.0, 0.0, 0.0):
        scale = (1.0, 1.0, 1.0)
    loc = _as_vec(
        props.get("LocationOffset")
        or props.get("Location")
        or props.get("Offset")
    )
    rot = _as_rot(
        props.get("RotationOffset")
        or props.get("Rotation")
    )
    return {
        "name": notify_name or class_name,
        "class": class_name,
        "kind": kind,
        "time": float(props.get("Time") or props.get("LinkValue") or 0) or 0.0,
        "duration": float(props.get("Duration") or 0) or 0.0,
        "socket": str(props.get("SocketName") or props.get("socket") or "") or "",
        "skeletal_mesh": skel,
        "static_mesh": static,
        "mesh": mesh,
        "animation": _obj_name(props.get("SkeletalMeshPropAnimation") or props.get("Animation") or props.get("AnimToPlay")),
        "niagara": fx,
        "location": loc,
        "rotation": rot,
        "scale": scale,
    }


def _exports_from_dump(data: Any) -> List[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("Exports", "exports"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
        return [data]
    return []


def parse_notify_document(data: Any) -> dict:
    """Normalize a sidecar or FModel AnimSequence JSON dump into catalog metadata."""
    result = {
        "skeleton": "",
        "length": None,
        "notifies": [],
    }
    if data is None:
        return result
    if isinstance(data, dict) and isinstance(data.get("Notifies"), list) and (
        data.get("AnimName") or data.get("skeleton") or data.get("Skeleton")
    ):
        result["skeleton"] = _obj_name(data.get("Skeleton") or data.get("skeleton"))
        length = data.get("SequenceLength") or data.get("length")
        try:
            result["length"] = float(length) if length is not None else None
        except (TypeError, ValueError):
            result["length"] = None
        notifies = []
        for raw in data.get("Notifies") or []:
            if isinstance(raw, dict):
                if raw.get("kind") or raw.get("skeletal_mesh") or raw.get("static_mesh") or raw.get("niagara"):
                    notifies.append(raw)
                else:
                    notifies.append(_notify_from_props(raw, class_name=str(raw.get("class") or raw.get("Class") or ""), notify_name=str(raw.get("name") or raw.get("Name") or "")))
        result["notifies"] = notifies
        return result

    exports = _exports_from_dump(data)
    by_name: Dict[str, dict] = {}
    anim_props = None
    for export in exports:
        name = str(export.get("Name") or export.get("ObjectName") or "")
        props = export.get("Properties") if isinstance(export.get("Properties"), dict) else export
        if name:
            by_name[name] = props if isinstance(props, dict) else {}
        type_leaf = _type_leaf(str(export.get("Type") or export.get("ExportType") or ""))
        if type_leaf in {"AnimSequence", "AnimMontage", "AnimComposite"} and isinstance(props, dict):
            anim_props = props
            result["skeleton"] = _obj_name(props.get("Skeleton"))
            try:
                result["length"] = float(props.get("SequenceLength")) if props.get("SequenceLength") is not None else None
            except (TypeError, ValueError):
                result["length"] = None

    if anim_props is None:
        return result

    notifies = []
    for event in anim_props.get("Notifies") or []:
        if not isinstance(event, dict):
            continue
        class_ref = event.get("NotifyStateClass") or event.get("Notify") or {}
        class_name = _obj_name(class_ref)
        nested = by_name.get(class_name) or {}
        merged = dict(nested)
        merged.update({k: v for k, v in event.items() if k not in {"NotifyStateClass", "Notify"}})
        time = event.get("LinkValue")
        if time is None:
            time = event.get("TriggerTimeOffset")
        try:
            merged["Time"] = float(time or 0)
        except (TypeError, ValueError):
            merged["Time"] = 0.0
        try:
            merged["Duration"] = float(event.get("Duration") or 0)
        except (TypeError, ValueError):
            merged["Duration"] = 0.0
        notifies.append(
            _notify_from_props(
                merged,
                class_name=class_name,
                notify_name=str(event.get("NotifyName") or class_name),
            )
        )
    result["notifies"] = notifies
    return result


def load_notify_file(path: str) -> dict:
    if not path or not os.path.isfile(path):
        return {"skeleton": "", "length": None, "notifies": []}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("animation notify JSON failed (%s): %s", path, exc)
        return {"skeleton": "", "length": None, "notifies": []}
    return parse_notify_document(data)


def enrich_entry(entry: dict, cache_dir: str = "") -> dict:
    """Fill skeleton / length / notify flags from sidecar or dump JSON (lazy)."""
    if not entry:
        return entry
    if entry.get("enriched"):
        return entry
    notify_path = resolve_notify_path(entry, cache_dir)
    json_path = entry.get("json_path") or ""
    if not json_path:
        json_path = fmdex.resolve_export_file(entry.get("stem") or "", ".json", allow_basename_walk=False) or ""
        entry["json_path"] = json_path
    meta = {"skeleton": "", "length": None, "notifies": []}
    if notify_path:
        meta = load_notify_file(notify_path)
    elif json_path:
        meta = load_notify_file(json_path)
    notifies = meta.get("notifies") or []
    entry["skeleton"] = meta.get("skeleton") or entry.get("skeleton") or ""
    entry["length"] = meta.get("length")
    entry["notify_count"] = len(notifies)
    entry["has_props"] = any(n.get("kind") == "prop" for n in notifies if isinstance(n, dict))
    entry["has_fx"] = any(n.get("kind") == "fx" for n in notifies if isinstance(n, dict))
    entry["notifies"] = notifies
    entry["enriched"] = True
    return entry


def display_label(entry: dict) -> str:
    stem = entry.get("stem") or "?"
    folder = entry.get("folder") or ""
    kind = entry.get("kind") or ""
    bits = [stem]
    if folder:
        tail = folder.split("/")[-1]
        if tail and tail.lower() != stem.lower():
            bits.append(tail)
    suffix = []
    if entry.get("psa_path"):
        suffix.append("PSA")
    if entry.get("has_props"):
        suffix.append("props")
    if entry.get("has_fx"):
        suffix.append("FX")
    label = " · ".join(bits)
    if kind and kind != "AnimSequence":
        label = f"{label} [{kind}]"
    if suffix:
        label = f"{label} ({', '.join(suffix)})"
    return label
