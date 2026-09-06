"""Regenerate weapon_catalog_data.py from Pioneer ST tables + DA_UIMetaData.

Usage (from repo root):
  set ARC_PIONEER_ROOT=<path-to>/PioneerGame/Content/Pioneer
  python tools/regen_weapon_catalog.py

Never embeds absolute machine paths into the committed catalog.
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
    # Optional local Desktop layout — not written into catalog.
    desktop = Path.home() / "Desktop" / "Arc_Raiders_Current" / "PioneerGame" / "Content" / "Pioneer"
    if desktop.is_dir():
        return desktop
    raise SystemExit(
        "Set ARC_PIONEER_ROOT to PioneerGame/Content/Pioneer (read-only ST/DA source)."
    )


def load_st(loc: Path, name: str) -> dict[str, str]:
    with open(loc / name, encoding="utf-8") as fh:
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
            if nk:
                out[nk] = {
                    "stem": stem,
                    "desc_key": dk,
                    "folder_kind": folder_kind,
                }
    return out


def is_junk_name(name: str) -> bool:
    if not name or name.startswith("\ufffd") or name.startswith("?"):
        return True
    if name.isupper() and "_" in name:
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


def resolve_gun_stem(key: str, gun_da: dict, pioneer: Path):
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
    for sub in ("Firearms", "Launchers"):
        base = pioneer / "Items" / sub
        if not base.is_dir():
            continue
        for p in base.iterdir():
            if p.is_dir() and p.name.lower() != "shared":
                if p.name.replace("_", "").lower() == compact:
                    return p.name, sub
    return None, None


def _esc(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("'", "\\'")


def main() -> int:
    pioneer = _resolve_pioneer()
    loc = pioneer / "UI" / "Localization"
    names_guns = load_st(loc, "ST_ItemNames_Firearms.json")
    names_mods = load_st(loc, "ST_ItemNames_FirearmMods.json")
    desc_guns = load_st(loc, "ST_ItemDescriptions_Firearms.json")
    desc_mods = load_st(loc, "ST_ItemDescriptions_FirearmMods.json")

    gun_da = {}
    gun_da.update(scan_da_maps(pioneer, "Items/Firearms"))
    gun_da.update(scan_da_maps(pioneer, "Items/Launchers"))
    mod_da = scan_da_maps(pioneer, "Items/WeaponMod")

    guns = []
    for key, display in sorted(names_guns.items(), key=lambda kv: (kv[1].lower(), kv[0])):
        if skip_gun_key(key) or is_junk_name(display):
            continue
        stem, kind = resolve_gun_stem(key, gun_da, pioneer)
        if not stem:
            print("UNRESOLVED GUN", key, display)
            continue
        dk = gun_da.get(key, {}).get("desc_key") or key.replace("ITEMNAME", "ITEMDESCRIPTION")
        tip = desc_guns.get(dk) or desc_guns.get(key) or ""
        guns.append(
            {
                "key": key,
                "name": display,
                "stem": stem,
                "kind": kind,
                "tooltip": tip,
            }
        )

    mods = []
    for key, display in sorted(names_mods.items(), key=lambda kv: (kv[1].lower(), kv[0])):
        info = mod_da.get(key)
        if not info:
            print("UNRESOLVED MOD", key, display)
            continue
        stem = info["stem"]
        tip = desc_mods.get(info.get("desc_key") or "") or ""
        mods.append(
            {
                "key": key,
                "name": display,
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
