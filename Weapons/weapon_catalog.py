"""ST-driven weapon / mod catalog helpers (hard list from weapon_catalog_data)."""
from __future__ import annotations

import os
from typing import Optional

from . import weapon_catalog_data as _data

MOD_TYPES = ("Muzzle", "Stock", "Magazine", "UnderBarrel", "Tech", "Other")

# Stem prefix → attachment bone on the gun armature.
MOD_BONE_RULES: tuple[tuple[str, str], ...] = (
    ("shotgunmuzzle_", "Muzzle"),
    ("muzzle_", "Muzzle"),
    ("stock_", "Stock"),
    ("magazine", "weapon_mag"),
    ("mag_", "weapon_mag"),
    ("underbarrel_", "weapon_underbarrel"),
    ("tech_", "weapon_root"),
)

# Blender EnumProperty callbacks must keep the returned (id, name, desc) strings
# alive; otherwise RNA holds dangling pointers and the UI shows mojibake.
_WEAPON_ENUM_ITEMS: list[tuple[str, str, str]] | None = None
_MOD_ENUM_ITEMS: dict[str, list[tuple[str, str, str]]] = {}
_PATTERN_ENUM_CACHE: dict = {"root": None, "items": None}
_SKIN_ENUM_CACHE: dict = {"key": None, "items": None}


def weapons() -> list[dict]:
    return list(getattr(_data, "WEAPONS", []) or [])


def mods() -> list[dict]:
    return list(getattr(_data, "MODS", []) or [])


def weapon_by_key(key: str) -> Optional[dict]:
    k = (key or "").strip()
    for entry in weapons():
        if entry.get("key") == k:
            return entry
    return None


def mod_by_key(key: str) -> Optional[dict]:
    k = (key or "").strip()
    for entry in mods():
        if entry.get("key") == k:
            return entry
    return None


def invalidate_enum_caches() -> None:
    """Drop cached enum item tuples (call after catalog / pioneer root changes)."""
    global _WEAPON_ENUM_ITEMS
    _WEAPON_ENUM_ITEMS = None
    _MOD_ENUM_ITEMS.clear()
    _PATTERN_ENUM_CACHE["root"] = None
    _PATTERN_ENUM_CACHE["items"] = None
    _SKIN_ENUM_CACHE["key"] = None
    _SKIN_ENUM_CACHE["items"] = None


def _clean_items(entries):
    """(key, label, tip) with display names only; the file stem goes in the tooltip.
    A stem is added to the label only when two entries share a display name."""
    rows = []
    for entry in entries:
        key = str(entry.get("key") or "").strip()
        if key:
            rows.append((key, str(entry.get("name") or "").strip(), str(entry.get("stem") or "").strip(), entry))
    seen = {}
    for _key, name, _stem, _entry in rows:
        seen[name.lower()] = seen.get(name.lower(), 0) + 1
    items = []
    for key, name, stem, entry in rows:
        label = name or stem or "?"
        if name and stem and seen.get(name.lower(), 0) > 1:
            label = f"{name} ({stem})"
        tip = str(entry.get("tooltip") or name or stem)
        if stem and stem not in tip:
            tip = f"{tip}. File: {stem}" if tip else f"File: {stem}"
        items.append((key, label, tip[:512]))
    return items


def display_label(key: str) -> str:
    """Clean display name for a weapon key (stream A search picker)."""
    k = (key or "").strip()
    for item in weapon_enum_items():
        if item[0] == k:
            return item[1]
    return ""


def weapon_enum_items(_self=None, _context=None):
    global _WEAPON_ENUM_ITEMS
    if _WEAPON_ENUM_ITEMS is not None:
        return _WEAPON_ENUM_ITEMS
    items: list[tuple[str, str, str]] = [
        ("NONE", "Select Weapon...", ""),
    ]
    # Identifier must stay ASCII RNA-safe; label is the display name only.
    items.extend(_clean_items(weapons()))
    _WEAPON_ENUM_ITEMS = items
    return _WEAPON_ENUM_ITEMS


def mod_enum_items(mod_type: str):
    """Build EnumProperty items for one mod type collapsible."""
    mt = (mod_type or "").strip() or "Mod"
    cached = _MOD_ENUM_ITEMS.get(mt)
    if cached is not None:
        return cached
    items: list[tuple[str, str, str]] = [
        ("NONE", f"Select {mt}...", ""),
    ]
    items.extend(_clean_items(e for e in mods() if (e.get("mod_type") or "Other") == mt))
    if len(items) == 1:
        # Distinct identifier — duplicate "NONE" identifiers confuse Blender RNA.
        items.append(("NONE_EMPTY", f"(no {mt} mods)", ""))
    _MOD_ENUM_ITEMS[mt] = items
    return items


def attachment_bone_for_stem(stem: str) -> str:
    sl = (stem or "").lower()
    for prefix, bone in MOD_BONE_RULES:
        if sl.startswith(prefix) or prefix.rstrip("_") in sl:
            return bone
    return "weapon_root"


def _resolve_under_pioneer(pioneer_root: str, rel_parts: list[str]) -> Optional[str]:
    """Resolve Content/Pioneer/<rel> whether root is dump, PioneerGame, Content, or Pioneer."""
    root = (pioneer_root or "").strip()
    if not root or not rel_parts:
        return None
    # Fast path: user already pointed at Content/Pioneer (or equivalent).
    direct = os.path.join(root, *rel_parts)
    if os.path.isdir(direct):
        return direct
    try:
        from ..Shared import utils

        utils.invalidate_dir_caches_if_root_changed()
        content = utils.find_content_dir(root)
        if content:
            pioneer = os.path.join(content, "Pioneer")
            if os.path.isdir(pioneer):
                candidate = os.path.join(pioneer, *rel_parts)
                if os.path.isdir(candidate):
                    return candidate
        found = utils.find_relative_dir(root, list(rel_parts))
        return found or None
    except Exception:
        return None


def resolve_weapon_folder(pioneer_root: str, entry: dict) -> Optional[str]:
    """Return absolute Items/Firearms|Launchers/<stem> path, or None if missing."""
    if not entry:
        return None
    kind = entry.get("kind") or "Firearms"
    stem = entry.get("stem") or ""
    if not stem:
        return None
    found = _resolve_under_pioneer(pioneer_root, ["Items", kind, stem])
    if found:
        return found
    # Soft fallback: other gun bucket
    alt_kind = "Launchers" if kind == "Firearms" else "Firearms"
    return _resolve_under_pioneer(pioneer_root, ["Items", alt_kind, stem])


def resolve_mod_folder(pioneer_root: str, entry: dict) -> Optional[str]:
    stem = (entry or {}).get("stem") or ""
    if not stem:
        return None
    return _resolve_under_pioneer(pioneer_root, ["Items", "WeaponMod", stem])


def patterns_dir(pioneer_root: str) -> Optional[str]:
    return _resolve_under_pioneer(
        pioneer_root,
        ["MaterialLibrary", "Textures", "Weapons", "Patterns"],
    )


def weapon_skins_dir(pioneer_root: str, entry: dict | None = None) -> Optional[str]:
    """Return Items/Firearms|Launchers/<stem>/Skins when present."""
    folder = resolve_weapon_folder(pioneer_root, entry or {})
    if not folder:
        return None
    skins = os.path.join(folder, "Skins")
    return skins if os.path.isdir(skins) else None


def list_weapon_skins(pioneer_root: str, entry: dict | None = None) -> list[tuple[str, str]]:
    """Return [(skin_folder_name, mi_json_path), ...] for a gun's Skins/ tree."""
    from ..Shared import textures

    folder = resolve_weapon_folder(pioneer_root, entry or {})
    if not folder:
        return []
    skins_dir = os.path.join(folder, "Skins")
    if not os.path.isdir(skins_dir):
        return []

    # Prefer scan_skins via any SK_/SM_ mesh in the gun folder.
    mesh = ""
    try:
        for fname in sorted(os.listdir(folder)):
            fl = fname.lower()
            if fl.endswith(".psk") or fl.endswith(".pskx"):
                mesh = os.path.join(folder, fname)
                break
    except OSError:
        mesh = ""
    if mesh:
        seen: set[str] = set()
        out: list[tuple[str, str]] = []
        for name, path in textures.scan_skins(mesh):
            if not name or name in seen or not path or not os.path.isfile(path):
                continue
            # scan_skins can also return main-folder MIs; keep Skins/ colourways only.
            if "skins" not in os.path.normcase(path).replace("\\", "/"):
                continue
            seen.add(name)
            out.append((name, os.path.abspath(path)))
        if out:
            return sorted(out, key=lambda t: t[0].lower())

    # Fallback: walk Skins/<Name>/MI_*.json directly.
    out = []
    try:
        names = sorted(
            d
            for d in os.listdir(skins_dir)
            if os.path.isdir(os.path.join(skins_dir, d))
            and "persistence" not in d.lower()
        )
    except OSError:
        return []
    for name in names:
        skin_dir = os.path.join(skins_dir, name)
        path = ""
        try:
            for fname in sorted(os.listdir(skin_dir)):
                if fname.lower().startswith("mi_") and fname.lower().endswith(".json"):
                    path = os.path.join(skin_dir, fname)
                    break
        except OSError:
            path = ""
        if path and os.path.isfile(path):
            out.append((name, os.path.abspath(path)))
    return out


def resolve_weapon_skin_mi(
    pioneer_root: str | list[str] | tuple[str,...], entry: dict | None, skin_name: str,
) -> str:
    """Resolve Skins/<skin_name>/MI_*.json for a catalog gun, or ''."""
    name = (skin_name or "").strip()
    if not name or name == "NONE":
        return ""
    roots=list(pioneer_root) if isinstance(pioneer_root,(list,tuple)) else [pioneer_root]
    def sources():
        yield from roots
        from . import weapon_assets
        yield from (str(root) for root in weapon_assets.pioneer_roots())
    seen=set()
    for root in sources():
        if not root or root in seen:continue
        seen.add(root)
        for folder_name, mi_path in list_weapon_skins(root, entry):
            if folder_name == name:return mi_path
    return ""


def skin_enum_items(_self=None, context=None):
    scene = _self if hasattr(_self, "arc_weapon_key") else getattr(context, "scene", None)
    weapon_key = getattr(scene, "arc_weapon_key", "NONE") or "NONE"
    from . import weapon_assets

    cache_key = (weapon_key, weapon_assets._REVISION)
    if _SKIN_ENUM_CACHE["items"] is not None and _SKIN_ENUM_CACHE["key"] == cache_key:
        return _SKIN_ENUM_CACHE["items"]

    items: list[tuple[str, str, str]] = [("NONE", "Default", "")]
    entry = weapon_by_key(weapon_key) if weapon_key != "NONE" else None
    if entry:
        for skin in weapon_assets.bundled_skins(entry):
            items.append((skin["id"], skin["label"], ""))

    _SKIN_ENUM_CACHE["key"] = cache_key
    _SKIN_ENUM_CACHE["items"] = items
    return items


def pattern_enum_items(_self=None, context=None):
    from . import weapon_assets

    revision = weapon_assets._REVISION
    if _PATTERN_ENUM_CACHE["items"] is not None and _PATTERN_ENUM_CACHE["root"] == revision:
        return _PATTERN_ENUM_CACHE["items"]

    items: list[tuple[str, str, str]] = [("NONE", "No Pattern", "")]
    for pattern in weapon_assets.bundled().get("patterns", []):
        items.append((pattern["id"], pattern["label"], ""))

    _PATTERN_ENUM_CACHE["root"] = revision
    _PATTERN_ENUM_CACHE["items"] = items
    return items
