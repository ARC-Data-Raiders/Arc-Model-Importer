"""Ship appearance UI icons as AVIF named by the original game texture stem.

Source: installed Steam paks through ArcPak.Export.exe (static pak reads only).
Output: assets/ui_icons/appearance/<T_UI_Icon_stem>.avif, long side <= 512, never upscaled.
Rewrites the `icons` map in assets/appearance_browse.json to stems.

Example: py -3.14 -B scripts/build_ui_icons_appearance.py --scratch "Z:/.../appearance" --remove-old
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from Outfit_Accessories.pak_catalog import read_index  # noqa: E402
from build_appearance_catalog import SEGMENTS, choose_icon, token  # noqa: E402

GAME_PAKS = "C:/Program Files (x86)/Steam/steamapps/common/Arc Raiders/PioneerGame/Content/Paks"
ADDON = Path("C:/Users/Silarious/AppData/Roaming/Blender Foundation/Blender/5.1/scripts/addons/Arc-Asset-Importer")
INDEX = ADDON / "ArcPak/cache/indexes/pak-index-26439197.bin"
EXE = ADDON / "ArcPak/ArcPak.Export.exe"
USMAP = ROOT / "ArcPak/mappings/ArcRaiders_2.3.0_UE5.7.4_CL1438640.usmap"
OUT_DIR = ROOT / "assets/ui_icons/appearance"
BROWSE = ROOT / "assets/appearance_browse.json"
REFERENCE = ROOT / 'Data/appearance_reference.csv'
OUTFIT_REFERENCE = ROOT / 'Data/outfit_reference.csv'
OLD_FOLDERS = ["assets/appearance_previews", "assets/outfit_previews", "assets/hair_previews"]
MAX_SIDE = 512
CHUNK = 100
# Choice IDs whose game icon name does not follow the label. Each target is confirmed by its DA_OI item ref.
CHOICE_STEM_OVERRIDES = {
    "9679aee4369ebe05": "T_UI_Icon_BackpackCharm_HatchlingChick_01",  # Chick Pendant 01: DA_OI_BackpackCharm_Hatchlingchick
    "bfadb79d0ebda739": "T_UI_Icon_BackpackCharm_ChatteringTeeth",  # Talking Teeth 01: DA_OI_BackpackCharm_ChatteringTeeth
}


def segment_of(path: str) -> str:
    match = re.search(r"/Skins/([^/]+)/", path)
    return match.group(1) if match else ""


def extract(paths: list[str], scratch: Path) -> dict[str, Path]:
    out = scratch / "extract"
    out.mkdir(parents=True, exist_ok=True)
    for start in range(0, len(paths), CHUNK):
        chunk = paths[start:start + CHUNK]
        cmd = [str(EXE), "extract", "--game-dir", GAME_PAKS, "--usmap", str(USMAP), "--out", str(out),
               "--index-dir", str(INDEX.parent), "--index", str(INDEX)]
        for path in chunk:
            cmd.extend(["--path", path])
        result = subprocess.run(cmd, capture_output=True, text=True, creationflags=0x08000000)
        if result.returncode != 0:
            raise SystemExit(f"ArcPak extract failed rc={result.returncode}: {result.stderr[-400:]}")
    return {path: out / (path + ".png") for path in paths}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--remove-old", action="store_true")
    args = parser.parse_args()

    index = read_index(str(INDEX))
    icon_paths = [p for p in index["paths"] if p.rsplit("/", 1)[-1].startswith("T_UI_Icon_")]
    candidates: dict[str, list[tuple[str, str]]] = {}
    for path in icon_paths:
        segment = segment_of(path)
        stem = path.rsplit("/", 1)[-1]
        prefix = "T_UI_Icon_" + segment + "_"
        if segment and stem.startswith(prefix):
            candidates.setdefault(segment, []).append((token(stem[len(prefix):]), path))

    chosen: dict[str, str] = {}
    missing: list[str] = []
    with REFERENCE.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        segment = "FacialHair" if row["segment"].casefold() == "facialhairs" else SEGMENTS.get(row["kind"])
        if not segment:
            continue
        selected = choose_icon(row, candidates.get(segment, []))
        override = CHOICE_STEM_OVERRIDES.get(row["id"])
        if not selected and override:
            selected = next((p for p in icon_paths if p.endswith("/" + override)), None)
        if selected:
            chosen[row["id"]] = selected
        else:
            missing.append(f'{row["kind"]}:{row["id"]}:{row["label"]}')

    outfit_icons: dict[str, str] = {}
    with OUTFIT_REFERENCE.open(encoding="utf-8-sig", newline="") as stream:
        outfit_rows = [r for r in csv.DictReader(stream) if (r["ST"] or "").startswith("ID_PLAYERSKIN_")]
    by_stem = {p.rsplit("/", 1)[-1]: p for p in icon_paths}
    outfit_missing: list[str] = []
    for row in outfit_rows:
        name = (row["Item/UI Folder Name"] or "").strip()
        # Some rows list several items ("Angler; Vest"); the first item names the icon.
        base = name.split(";")[0].strip()
        hit = None
        for key in (name, base):
            if key:
                hit = hit or by_stem.get("T_UI_Icon_Outfit_" + key) or by_stem.get("T_UI_Icon_RoosterOutfit_" + key)
        if hit:
            outfit_icons[name] = hit
        else:
            outfit_missing.append(row["ST"])

    needed = sorted(set(chosen.values()) | set(outfit_icons.values()))
    pngs = extract(needed, args.scratch)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    stems: dict[str, str] = {}
    for path in needed:
        stem = path.rsplit("/", 1)[-1]
        image = Image.open(pngs[path]).convert("RGBA")
        if max(image.size) > MAX_SIDE:
            image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        image.save(OUT_DIR / (stem + ".avif"), "AVIF", quality=60, speed=6)
        stems[path] = stem

    browse = json.loads(BROWSE.read_text(encoding="utf-8"))
    browse["icons"] = {choice: stems[path] for choice, path in chosen.items()}
    BROWSE.write_text(json.dumps(browse, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    before = sum(f.stat().st_size for folder in OLD_FOLDERS for f in (ROOT / folder).glob("*.png"))
    after = sum(f.stat().st_size for f in OUT_DIR.glob("*.avif"))
    counts: dict[str, int] = {}
    for row in rows:
        if row["id"] in chosen:
            counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    if args.remove_old:
        for folder in OLD_FOLDERS:
            for f in (ROOT / folder).glob("*.png"):
                f.unlink()
    print(json.dumps({
        "avif_files": len(list(OUT_DIR.glob("*.avif"))),
        "choice_icons_by_kind": counts,
        "outfit_icons": len(outfit_icons),
        "outfit_missing_rows": outfit_missing,
        "missing_choices": missing,
        "png_before_mb": round(before / 1048576, 2),
        "avif_after_mb": round(after / 1048576, 2),
    }, indent=1))


if __name__ == "__main__":
    main()
