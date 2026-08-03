#!/usr/bin/env python3
"""
Overlay map placements on the heightmap / world footprint.

Loads placements CSV + `{map}_world_bounds.json`, projects XY via
HeightmapInfo.world_to_pixel, and writes `{map}_placements_overlay.png`.

Prefer writing into a central workspace folder:
  {workspace}/{MapName}/{MapName}_placements_overlay.png

Example:
  python map_placement_overlay.py --map-dir "<PioneerGame Root>/Content/Pioneer/Maps/FrozenTrail_01" --workspace "<addon>/MapPlacement"
  python map_placement_overlay.py --csv placements_buildings.csv --bounds FrozenTrail_01_world_bounds.json --out overlay.png
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw


KIND_COLORS = {
    "architecture": (255, 80, 60, 220),
    "poi": (80, 180, 255, 220),
    "blueprint": (255, 200, 40, 220),
    "prop": (120, 220, 120, 200),
    "staticmesh": (180, 180, 255, 200),
    "other": (220, 220, 220, 180),
}


@dataclass
class Vec3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class HeightmapInfo:
    name: str = ""
    base_cell_size: float = 50.0
    base_dimensions: int = 4096
    min_height: float | None = None
    granularity: float | None = None
    location: Vec3 = field(default_factory=Vec3)
    scale: Vec3 = field(default_factory=lambda: Vec3(1.0, 1.0, 1.0))

    @property
    def cell_x(self) -> float:
        return self.base_cell_size * self.scale.x

    @property
    def cell_y(self) -> float:
        return self.base_cell_size * self.scale.y

    @property
    def size_xy(self) -> tuple[float, float]:
        d = self.base_dimensions
        return d * self.cell_x, d * self.cell_y

    def world_to_pixel(self, wx: float, wy: float) -> tuple[float, float]:
        col = (wx - self.location.x) / self.cell_x
        row = (wy - self.location.y) / self.cell_y
        return col, row


def load_bounds(path: Path) -> HeightmapInfo:
    data = json.loads(path.read_text(encoding="utf-8"))
    hm = data.get("heightmap") or data
    loc = hm.get("location") or {}
    scale = hm.get("scale") or {"x": 1.0, "y": 1.0, "z": 1.0}
    return HeightmapInfo(
        name=hm.get("name") or "",
        base_cell_size=float(hm.get("base_cell_size", 50.0)),
        base_dimensions=int(hm.get("base_dimensions", 4096)),
        min_height=hm.get("min_height"),
        granularity=hm.get("granularity"),
        location=Vec3(float(loc.get("x", 0)), float(loc.get("y", 0)), float(loc.get("z", 0))),
        scale=Vec3(float(scale.get("x", 1)), float(scale.get("y", 1)), float(scale.get("z", 1))),
    )


def _search_dirs(
    map_name: str,
    map_dir: Path | None,
    workspace: Path | None,
    scripts_dir: Path,
) -> list[Path]:
    dirs: list[Path] = []
    if workspace is not None:
        dirs.append(workspace / map_name)
        dirs.append(workspace)
    if map_dir is not None:
        dirs.extend([map_dir / "_PropHarvest", map_dir / "_Placement", map_dir])
    dirs.append(scripts_dir)
    # Dedupe while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        key = str(d.resolve()) if d.exists() else str(d)
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def find_bounds_json(
    map_name: str,
    map_dir: Path | None,
    scripts_dir: Path,
    explicit: Path | None,
    workspace: Path | None = None,
) -> Path | None:
    if explicit and explicit.is_file():
        return explicit
    name = f"{map_name}_world_bounds.json"
    for d in _search_dirs(map_name, map_dir, workspace, scripts_dir):
        p = d / name
        if p.is_file():
            return p
    return None


def find_csv(
    map_dir: Path | None,
    explicit: Path | None,
    workspace: Path | None = None,
    map_name: str = "",
) -> Path | None:
    if explicit and explicit.is_file():
        return explicit
    names = ("placements_buildings.csv", "placements.csv")
    dirs: list[Path] = []
    if workspace is not None and map_name:
        dirs.append(workspace / map_name)
    if map_dir is not None:
        dirs.extend([map_dir / "_PropHarvest", map_dir / "_Placement", map_dir])
    for d in dirs:
        for name in names:
            p = d / name
            if p.is_file():
                return p
    return None


def find_heightmap_png(
    map_name: str,
    map_dir: Path | None,
    scripts_dir: Path,
    explicit: Path | None,
    workspace: Path | None = None,
) -> Path | None:
    if explicit and explicit.is_file():
        return explicit
    names = [
        f"{map_name}_heightmap.png",
        f"{map_name}_terrain.png",
        f"{map_name}_world_bounds.png",
        f"{map_name}_heightmap_bounds.png",
        f"{map_name}_heightmap_bounds_preview2048.png",
        "heightmap.png",
        "terrain.png",
    ]
    for d in _search_dirs(map_name, map_dir, workspace, scripts_dir):
        for n in names:
            p = d / n
            if p.is_file():
                return p
    return None


def blank_canvas(hm: HeightmapInfo) -> Image.Image:
    d = hm.base_dimensions
    arr = np.full((d, d, 3), 28, dtype=np.uint8)
    return Image.fromarray(arr, mode="RGB").convert("RGBA")


def load_base_image(hm: HeightmapInfo, png: Path | None) -> Image.Image:
    if png and png.is_file():
        return Image.open(png).convert("RGBA")
    return blank_canvas(hm)


def load_rows(csv_path: Path) -> list[dict[str, Any]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def draw_placements(
    base: Image.Image,
    hm: HeightmapInfo,
    rows: list[dict[str, Any]],
) -> Image.Image:
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    w, h = base.size
    dim = hm.base_dimensions
    sx = w / float(dim)
    sy = h / float(dim)
    r = max(2, min(w, h) // 400)

    for row in rows:
        try:
            wx = float(row["x"])
            wy = float(row["y"])
        except (KeyError, ValueError, TypeError):
            continue
        col, row_y = hm.world_to_pixel(wx, wy)
        px = col * sx
        py = (dim - 1 - row_y) * sy
        if px < -10 or py < -10 or px > w + 10 or py > h + 10:
            continue
        color = KIND_COLORS.get((row.get("asset_kind") or "other").lower(), KIND_COLORS["other"])
        draw.ellipse((px - r, py - r, px + r, py + r), fill=color)

    ox, oy = hm.location.x, hm.location.y
    sw, sh = hm.size_xy
    corners = [
        (ox, oy),
        (ox + sw, oy),
        (ox + sw, oy + sh),
        (ox, oy + sh),
        (ox, oy),
    ]
    pts = []
    for wx, wy in corners:
        col, row_y = hm.world_to_pixel(wx, wy)
        pts.append((col * sx, (dim - 1 - row_y) * sy))
    draw.line(pts, fill=(255, 255, 0, 160), width=max(2, w // 800))

    return Image.alpha_composite(base, overlay)


def resolve_out_path(
    map_name: str,
    map_dir: Path | None,
    workspace: Path | None,
    explicit: Path | None,
    scripts_dir: Path,
) -> Path:
    if explicit is not None:
        return explicit
    if workspace is not None:
        dest = workspace / map_name
        dest.mkdir(parents=True, exist_ok=True)
        return dest / f"{map_name}_placements_overlay.png"
    if map_dir is not None:
        # Legacy beside-map fallback
        for sub_name in ("_PropHarvest", "_Placement"):
            out_sub = map_dir / sub_name
            if out_sub.is_dir() or sub_name == "_Placement":
                out_sub.mkdir(parents=True, exist_ok=True)
                return out_sub / f"{map_name}_placements_overlay.png"
    return scripts_dir / f"{map_name}_placements_overlay.png"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Overlay placements on heightmap footprint")
    ap.add_argument("--map-dir", type=Path, default=None, help="Map folder")
    ap.add_argument("--csv", type=Path, default=None, help="placements CSV")
    ap.add_argument("--bounds", type=Path, default=None, help="world_bounds JSON")
    ap.add_argument("--heightmap-png", type=Path, default=None, help="Optional heightmap image")
    ap.add_argument("--workspace", type=Path, default=None, help="Central MapPlacement folder")
    ap.add_argument("--out-dir", type=Path, default=None, help="Exact output folder (overrides workspace)")
    ap.add_argument("--out", type=Path, default=None, help="Exact output PNG path")
    args = ap.parse_args(argv)

    scripts_dir = Path(__file__).resolve().parent
    map_dir: Path | None = args.map_dir
    map_name = map_dir.name if map_dir else "map"
    workspace = args.out_dir.parent if args.out_dir else args.workspace
    if args.out_dir is not None:
        # Treat exact out-dir as the per-map folder; workspace is its parent
        map_name = args.out_dir.name if not map_dir else map_name
        workspace = args.out_dir.parent
        # Prefer writing into out_dir directly via --out below

    csv_path = find_csv(map_dir, args.csv, workspace=workspace, map_name=map_name)
    if csv_path is None or not Path(csv_path).is_file():
        # If --out-dir given, also look there
        if args.out_dir is not None:
            for name in ("placements_buildings.csv", "placements.csv"):
                p = args.out_dir / name
                if p.is_file():
                    csv_path = p
                    break
    if csv_path is None or not Path(csv_path).is_file():
        print("ERROR: placements CSV not found (pass --csv or --map-dir / --workspace)", file=sys.stderr)
        return 1
    csv_path = Path(csv_path)

    # Infer map name from workspace layout: .../MapPlacement/FrozenTrail_01/placements.csv
    if map_dir is None and csv_path.parent.name not in ("_PropHarvest", "_Placement"):
        map_name = csv_path.parent.name

    bounds_path = find_bounds_json(map_name, map_dir, scripts_dir, args.bounds, workspace=workspace)
    if bounds_path is None and args.out_dir is not None:
        cand = args.out_dir / f"{map_name}_world_bounds.json"
        if cand.is_file():
            bounds_path = cand
    if bounds_path is None:
        if map_dir is None:
            parent = csv_path.parent
            if parent.name in ("_PropHarvest", "_Placement"):
                map_name = parent.parent.name
                map_dir = parent.parent
                bounds_path = find_bounds_json(map_name, map_dir, scripts_dir, args.bounds, workspace)
    if bounds_path is None:
        print("ERROR: world_bounds JSON not found (pass --bounds)", file=sys.stderr)
        return 1

    hm = load_bounds(bounds_path)
    if map_dir is None and csv_path.parent.name in ("_PropHarvest", "_Placement"):
        map_dir = csv_path.parent.parent
        map_name = map_dir.name

    png = find_heightmap_png(map_name, map_dir, scripts_dir, args.heightmap_png, workspace=workspace)
    if png is None and args.out_dir is not None:
        for n in (
            f"{map_name}_heightmap.png",
            f"{map_name}_world_bounds.png",
            f"{map_name}_heightmap_bounds.png",
        ):
            p = args.out_dir / n
            if p.is_file():
                png = p
                break

    base = load_base_image(hm, png)
    rows = load_rows(csv_path)
    print(f"CSV rows: {len(rows)} from {csv_path}")
    print(f"Bounds: {bounds_path}")
    print(f"Base image: {png if png else '(blank canvas)'}")

    result = draw_placements(base, hm, rows)

    if args.out is not None:
        out_path = args.out
    elif args.out_dir is not None:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        out_path = args.out_dir / f"{map_name}_placements_overlay.png"
    else:
        out_path = resolve_out_path(map_name, map_dir, workspace, None, scripts_dir)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.convert("RGB").save(out_path)
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
