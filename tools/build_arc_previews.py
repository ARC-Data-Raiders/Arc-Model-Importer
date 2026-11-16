"""Build assets/arc_browser AVIF previews from game-original DDS exports.

Source: texture-raw output of ArcPak.Export.exe, one DDS per catalog row.
Install path: %APPDATA%/Blender Foundation/Blender/5.1/scripts/addons/Arc-Asset-Importer/ArcPak/ArcPak.Export.exe
Command (per run, all catalog textures listed in a texture file):
  ArcPak.Export.exe texture-raw --game-dir "<Steam>/steamapps/common/Arc Raiders/PioneerGame/Content/Paks"
    --usmap ArcPak/mappings/ArcRaiders_2.3.0_UE5.7.4_CL1438640.usmap
    --out <dds_root> --texture-file <list>
  list lines: /Game/Pioneer/UI/Assets/Images/ARC/<stem>

Encode: RGBA kept, LANCZOS fit to long side 256, never upscale, AVIF quality 85 speed 6.
Output: assets/arc_browser/<stem>.avif. Old PNGs are removed after all AVIFs are written.

Usage: python tools/build_arc_previews.py --dds-root <dir containing Game/Pioneer/UI/Assets/Images/ARC>
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "assets" / "arc_browser"
CATALOG = REPO / "arc_catalog_data.py"
MAX_SIDE = 256
AVIF_QUALITY = 85
AVIF_SPEED = 6
DDS_SUBDIR = Path("Game/Pioneer/UI/Assets/Images/ARC")


def catalog_stems() -> list[str]:
    text = CATALOG.read_text(encoding="utf-8")
    stems = []
    for name in re.findall(r'"(T_UI_[A-Za-z0-9_]+)\.(?:png|avif)"', text):
        if name not in stems:
            stems.append(name)
    return stems


DXGI_B8G8R8A8_UNORM = 87


def read_dds(dds: Path) -> Image.Image:
    """Pillow cannot open DXGI 87 (B8G8R8A8 with DX10 header); read that layout directly."""
    try:
        with Image.open(dds) as src:
            return src.convert("RGBA")
    except NotImplementedError:
        pass
    data = dds.read_bytes()
    if data[:4] != b"DDS " or data[84:88] != b"DX10":
        raise ValueError(f"unsupported DDS layout: {dds}")
    height = int.from_bytes(data[12:16], "little")
    width = int.from_bytes(data[16:20], "little")
    dxgi = int.from_bytes(data[128:132], "little")
    if dxgi != DXGI_B8G8R8A8_UNORM:
        raise ValueError(f"unsupported DXGI format {dxgi}: {dds}")
    pixels = data[148:148 + width * height * 4]
    return Image.frombuffer("RGBA", (width, height), pixels, "raw", "BGRA", 0, 1)


def encode(dds: Path, dest: Path) -> tuple[int, int]:
    img = read_dds(dds)
    width, height = img.size
    scale = min(1.0, MAX_SIDE / max(width, height))
    if scale < 1.0:
        img = img.resize((round(width * scale), round(height * scale)), Image.LANCZOS)
    img.save(dest, format="AVIF", quality=AVIF_QUALITY, speed=AVIF_SPEED, subsampling="4:4:4")
    return img.size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dds-root", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    src_dir = args.dds_root / DDS_SUBDIR
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    stems = catalog_stems()
    if not stems:
        print("no catalog rows found", file=sys.stderr)
        return 1

    missing = [s for s in stems if not (src_dir / f"{s}.dds").is_file()]
    if missing:
        print("missing DDS: " + ", ".join(missing), file=sys.stderr)
        return 1

    for stem in stems:
        dest = out_dir / f"{stem}.avif"
        size = encode(src_dir / f"{stem}.dds", dest)
        print(f"{stem}.avif {size[0]}x{size[1]}")

    removed = 0
    for png in out_dir.glob("*.png"):
        png.unlink()
        removed += 1
    print(f"encoded {len(stems)} avif, removed {removed} png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
