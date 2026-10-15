"""Regenerate weapon_catalog_data.py from Pioneer ST tables + DA_UIMetaData.

Usage (from repo root):
  set ARC_PIONEER_ROOT=<path-to>/PioneerGame/Content/Pioneer
  python tools/regen_weapon_catalog.py

Optional:
  ARC_ST_ROOT / ARC_LOCALIZATION_ROOT - UI/Localization when the pioneer tree
  has no ST tables (e.g. 2.0 Unpack Items + Steam ST).

Never embeds absolute machine paths into the committed catalog.

When ST display strings are junk (replacement chars / UPPER_STEM leftovers),
prefer DA_UIMetaData ItemName/Description SourceString (Frozen Trail 2.0+).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "weapon_catalog_data.py"


def _resolve_pioneer() -> Path:
    env = (os.environ.get("ARC_PIONEER_ROOT") or "").strip()
    if env and Path(env).is_dir():
        return Path(env)
    # Optional local Desktop layout - not written into catalog.
    desktop = Path.home() / "Desktop" / "Arc_Raiders_Current" / "PioneerGame" / "Content" / "Pioneer"
    if desktop.is_dir():
        return desktop
    raise SystemExit(
        "Set ARC_PIONEER_ROOT to PioneerGame/Content/Pioneer (read-only ST/DA source)."
    )


def _desktop_pioneer() -> Path | None:
    desktop = Path.home() / "Desktop" / "Arc_Raiders_Current" / "PioneerGame" / "Content" / "Pioneer"
    return desktop if desktop.is_dir() else None


def _extra_pioneer_roots(primary: Path) -> list[Path]:
    """Steam / full dump roots used when Unpack trees omit WeaponMod or ST."""
    out: list[Path] = []
    for env_name in ("ARC_PIONEER_FALLBACK", "ARC_PIONEER_STEAM"):
        env = (os.environ.get(env_name) or "").strip()
        if env and Path(env).is_dir():
            p = Path(env)
            if p.resolve() != primary.resolve():
                out.append(p)
    desk = _desktop_pioneer()
    if desk is not None and desk.resolve() != primary.resolve():
        out.append(desk)
    return out


def _resolve_localization(pioneer: Path) -> Path:
    for env_name in ("ARC_ST_ROOT", "ARC_LOCALIZATION_ROOT"):
        env = (os.environ.get(env_name) or "").strip()
        if env and Path(env).is_dir():
            cand = Path(env)
            if (cand / "ST_ItemNames_Firearms.json").is_file():
                return cand
            nested = cand / "UI" / "Localization"
            if (nested / "ST_ItemNames_Firearms.json").is_file():
                return nested
    loc = pioneer / "UI" / "Localization"
    if (loc / "ST_ItemNames_Firearms.json").is_file():
        return loc
    for extra in _extra_pioneer_roots(pioneer):
        cand = extra / "UI" / "Localization"
        if (cand / "ST_ItemNames_Firearms.json").is_file():
            return cand
    raise SystemExit(
        "No ST_ItemNames_Firearms.json under pioneer UI/Localization or ARC_ST_ROOT."
    )


def load_st(loc: Path, name: str) -> dict[str, str]:
    path = loc / name
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return dict(data["Exports"][0]["StringTable"].get("KeysToEntries") or {})


def extract_fkey(obj, field: str = "ItemName"):
    if isinstance(obj, dict):
        if field in obj and isinstance(obj[field], dict) and "Key" in obj[field]:
            yield obj[field]
        for v in obj.values():
            yield from extract_fkey(v, field)
    elif isinstance(obj, list):
        for v in obj:
            yield from extract_fkey(v, field)


def _ftext_source(ft: dict) -> str:
    if not isinstance(ft, dict):
        return ""
    for k in ("SourceString", "LocalizedString"):
        val = ft.get(k)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def scan_da_maps(pioneer: Path, root_rel: str) -> dict[str, dict]:
    root = pioneer / root_rel
    out: dict[str, dict] = {}
    if not root.is_dir():
        return out
    folder_kind = root_rel.replace("\\", "/").split("/")[-1]
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and p.name.lower() != "shared"):
        stem = folder.name
        for da in folder.glob("DA_UIMetaData_*.json"):
            try:
                data = json.loads(da.read_text(encoding="utf-8"))
            except Exception:
                continue
            name_keys = list(extract_fkey(data, "ItemName"))
            desc_keys = list(extract_fkey(data, "Description"))
            if not name_keys:
                continue
            nk = name_keys[0].get("Key") or ""
            dk = desc_keys[0].get("Key") if desc_keys else ""
            if not nk:
                continue
            out[nk] = {
                "stem": stem,
                "desc_key": dk,
                "folder_kind": folder_kind,
                "name_src": _ftext_source(name_keys[0]),
                "desc_src": _ftext_source(desc_keys[0]) if desc_keys else "",
            }
    return out


def is_junk_name(name: str) -> bool:
    if not name:
        return True
    # Bad ST encodings: U+FFFD, § (0xA7) stubs, or control chars.
    if name.startswith(("\ufffd", "?", "\xa7")):
        return True
    if any(ord(c) == 0xFFFD or ord(c) < 32 for c in name):
        return True
    if name.isupper() and "_" in name:
        return True
    low = name.lower().strip()
    # Incomplete ST placeholder copy ("… desc", "… description text.").
    if low.endswith(" desc") or "description text" in low:
        return True
    return False


def skip_gun_key(key: str) -> bool:
    ku = key.upper()
    return "ALTERNATIVE" in ku or ku.endswith("_ALT")


def mod_type_for(stem: str) -> str:
    sl = stem.lower()
    if sl.startswith("shotgunmuzzle") or sl.startswith("muzzle"):
        return "Muzzle"
    if sl.startswith("stock"):
        return "Stock"
    if sl.startswith("mag") or "magazine" in sl:
        return "Magazine"
    if sl.startswith("underbarrel"):
        return "UnderBarrel"
    if sl.startswith("tech"):
        return "Tech"
    return "Other"


def resolve_gun_stem(key: str, gun_da: dict, pioneer: Path, search_roots: list[Path] | None = None):
    if key in gun_da:
        return gun_da[key]["stem"], gun_da[key]["folder_kind"]
    residual = key
    for pref in ("ST_ITEMNAME_FIREARM_", "ST_ITEMNAME_"):
        if residual.upper().startswith(pref):
            residual = residual[len(pref) :]
            break
    if residual.upper().endswith("_ALT"):
        residual = residual[:-4]
    compact = residual.replace("_", "").lower()
    aliases = {
        "specialsniperifflerailgun01": ("Firearms", "Special_Railgun_01"),
        "specialsnipersriflerailgun01": ("Firearms", "Special_Railgun_01"),
        "specialgrenadelauncherantiarclauncher": ("Launchers", "Launcher_AntiArc_Medium_01"),
        "launcherantiarcsingleshot01": ("Launchers", "Launcher_AntiArc_SingleShot_01"),
    }
    # typo-tolerant railgun
    if "railgun" in compact:
        aliases[compact] = ("Firearms", "Special_Railgun_01")
    if compact in aliases:
        return aliases[compact][1], aliases[compact][0]
    roots = [pioneer]
    for r in search_roots or []:
        if r not in roots:
            roots.append(r)
    for root in roots:
        for sub in ("Firearms", "Launchers"):
            base = root / "Items" / sub
            if not base.is_dir():
                continue
            for p in base.iterdir():
                if p.is_dir() and p.name.lower() != "shared":
                    if p.name.replace("_", "").lower() == compact:
                        return p.name, sub
    return None, None


def _pick_display(st_val: str, da_src: str) -> str:
    if st_val and not is_junk_name(st_val):
        return st_val
    if da_src and not is_junk_name(da_src):
        return da_src
    return ""


def _esc(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("'", "\\'")


def main() -> int:
    pioneer = _resolve_pioneer()
    loc = _resolve_localization(pioneer)
    names_guns = load_st(loc, "ST_ItemNames_Firearms.json")
    names_mods = load_st(loc, "ST_ItemNames_FirearmMods.json")
    desc_guns = load_st(loc, "ST_ItemDescriptions_Firearms.json")
    desc_mods = load_st(loc, "ST_ItemDescriptions_FirearmMods.json")

    extras = _extra_pioneer_roots(pioneer)
    gun_da = {}
    gun_da.update(scan_da_maps(pioneer, "Items/Firearms"))
    gun_da.update(scan_da_maps(pioneer, "Items/Launchers"))
    # Overlay older/full dumps so launchers/mods missing from Unpack still resolve.
    for extra in extras:
        for rel in ("Items/Firearms", "Items/Launchers"):
            for k, v in scan_da_maps(extra, rel).items():
                gun_da.setdefault(k, v)
    mod_da = scan_da_maps(pioneer, "Items/WeaponMod")
    if not mod_da:
        for extra in extras:
            mod_da = scan_da_maps(extra, "Items/WeaponMod")
            if mod_da:
                break

    guns = []
    seen_keys: set[str] = set()
    # ST keys first (stable ordering by display name).
    for key, display in sorted(names_guns.items(), key=lambda kv: (kv[1].lower(), kv[0])):
        if skip_gun_key(key):
            continue
        info = gun_da.get(key) or {}
        name = _pick_display(display, info.get("name_src") or "")
        if not name:
            continue
        stem, kind = resolve_gun_stem(key, gun_da, pioneer, extras)
        if not stem:
            print("UNRESOLVED GUN", key, name)
            continue
        dk = info.get("desc_key") or key.replace("ITEMNAME", "ITEMDESCRIPTION")
        tip = _pick_display(
            desc_guns.get(dk) or desc_guns.get(key) or "",
            info.get("desc_src") or "",
        )
        guns.append(
            {
                "key": key,
                "name": name,
                "stem": stem,
                "kind": kind,
                "tooltip": tip,
            }
        )
        seen_keys.add(key)

    # DA-only firearms (present in dump, missing or junk-only in ST).
    for key, info in sorted(gun_da.items(), key=lambda kv: kv[1].get("stem") or kv[0]):
        if key in seen_keys or skip_gun_key(key):
            continue
        name = _pick_display(names_guns.get(key) or "", info.get("name_src") or "")
        if not name:
            print("SKIP DA GUN (no display)", key, info.get("stem"))
            continue
        stem = info["stem"]
        kind = info.get("folder_kind") or "Firearms"
        dk = info.get("desc_key") or ""
        tip = _pick_display(desc_guns.get(dk) or "", info.get("desc_src") or "")
        guns.append(
            {
                "key": key,
                "name": name,
                "stem": stem,
                "kind": kind,
                "tooltip": tip,
            }
        )
        seen_keys.add(key)

    guns.sort(key=lambda g: (g["name"].lower(), g["key"]))

    mods = []
    for key, display in sorted(names_mods.items(), key=lambda kv: (kv[1].lower(), kv[0])):
        info = mod_da.get(key)
        if not info:
            print("UNRESOLVED MOD", key, display)
            continue
        name = _pick_display(display, info.get("name_src") or "")
        if not name:
            continue
        stem = info["stem"]
        tip = _pick_display(
            desc_mods.get(info.get("desc_key") or "") or "",
            info.get("desc_src") or "",
        )
        mods.append(
            {
                "key": key,
                "name": name,
                "stem": stem,
                "mod_type": mod_type_for(stem),
                "tooltip": tip,
            }
        )

    lines = [
        '"""Hard ST-derived weapon / mod catalog (generated; do not hand-edit).',
        "",
        "Regenerate with: python tools/regen_weapon_catalog.py",
        "(requires ARC_PIONEER_ROOT or pioneer Content/Pioneer).",
        '"""',
        "",
        "# flake8: noqa",
        "",
        "WEAPONS = [",
    ]
    for g in guns:
        lines.append(
            "    {"
            f"'key': '{g['key']}', 'name': '{_esc(g['name'])}', "
            f"'stem': '{g['stem']}', 'kind': '{g['kind']}', "
            f"'tooltip': '{_esc(g['tooltip'])}'"
            "},"
        )
    lines.append("]")
    lines.append("")
    lines.append("MODS = [")
    for m in mods:
        lines.append(
            "    {"
            f"'key': '{m['key']}', 'name': '{_esc(m['name'])}', "
            f"'stem': '{m['stem']}', 'mod_type': '{m['mod_type']}', "
            f"'tooltip': '{_esc(m['tooltip'])}'"
            "},"
        )
    lines.append("]")
    lines.append("")
    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_PATH.relative_to(REPO_ROOT)} guns={len(guns)} mods={len(mods)}")
    print(f"  pioneer={pioneer}")
    print(f"  localization={loc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
