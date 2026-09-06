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


def _catalog_display_label(entry: dict) -> str:
    """Match outfit importer style: ``Flavour(CodeName)``."""
    name = str(entry.get("name") or "").strip()
    stem = str(entry.get("stem") or "").strip()
    if name and stem and name.lower().replace("_", "") != stem.lower().replace("_", ""):
        return f"{name}({stem})"
    return name or stem or "?"


def weapon_enum_items(_self=None, _context=None):
    global _WEAPON_ENUM_ITEMS
    if _WEAPON_ENUM_ITEMS is not None:
        return _WEAPON_ENUM_ITEMS
    items: list[tuple[str, str, str]] = [
        ("NONE", "Select Weapon...", "Pick a firearm or launcher from the ST catalog"),
    ]
    for entry in weapons():
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        label = _catalog_display_label(entry)
        tip = str(entry.get("tooltip") or label)[:512]
        # Identifier must stay ASCII RNA-safe; label includes ST display + stem.
        items.append((key, label, tip))
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
    for entry in mods():
        if (entry.get("mod_type") or "Other") != mt:
            continue
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        label = _catalog_display_label(entry)
        tip = str(entry.get("tooltip") or label)[:512]
        items.append((key, label, tip))
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
        from . import utils

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


def pattern_enum_items(_self=None, context=None):
    pioneer = ""
    try:
        from . import utils

        pioneer = utils.get_pioneer_root() or ""
        if not pioneer and context is not None:
            pioneer = getattr(getattr(context, "scene", None), "arc_pioneer_root", "") or ""
        pioneer = os.path.normcase(os.path.normpath(pioneer)) if pioneer else ""
    except Exception:
        pioneer = ""

    if (
        _PATTERN_ENUM_CACHE["items"] is not None
        and _PATTERN_ENUM_CACHE["root"] == pioneer
    ):
        return _PATTERN_ENUM_CACHE["items"]

    items: list[tuple[str, str, str]] = [
        ("NONE", "No Pattern", "Pattern path disabled (Use Pattern = 0)"),
    ]
    try:
        pdir = patterns_dir(pioneer)
        if pdir:
            for name in sorted(os.listdir(pdir)):
                low = name.lower()
                if not low.endswith(".png"):
                    continue
                if "_c.png" not in low and not low.endswith("_c.png"):
                    # Prefer colour (_C) maps; still list ID maps with a marker
                    if "_id.png" in low:
                        label = os.path.splitext(name)[0]
                        items.append((name, f"{label} (ID)", name))
                    continue
                label = os.path.splitext(name)[0]
                # Strip common prefix for UI
                if label.lower().startswith("t_weapons_pattern_"):
                    label = label[len("T_Weapons_Pattern_") :]
                if label.endswith("_C") or label.endswith("_c"):
                    label = label[:-2]
                items.append((name, label.replace("_", " "), name))
    except Exception:
        pass

    _PATTERN_ENUM_CACHE["root"] = pioneer
    _PATTERN_ENUM_CACHE["items"] = items
    return items
