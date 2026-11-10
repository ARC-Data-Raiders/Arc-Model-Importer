#!/usr/bin/env python3
"""Prepare a Blender-ready city heightmap from an existing FModel export.

Does **not** need Blender or a full map reimport. Reads:

- ``{Map}_heightmap.png`` (or ``*_heightmap_pre_fix.png`` / raw LandscapeExporter PNG)
- ``{Map}_city_core_bounds.json`` and/or ``placements.csv`` for the city AABB
- optional ``{Map}_landscape_bounds.json`` for the pre-crop landscape footprint

Writes:

- ``{Map}_heightmap.png`` — city-cropped, Y-flipped (row0 = UE max Y), voids → valid median height
- ``{Map}_landscape_bounds.json`` / ``{Map}_world_bounds.json`` with matching footprint
- ``{Map}_heightmap_pre_fix.png`` backup of the uncropped source (once)

Example::

    python map_tools/prepare_blender_heightmap.py ^
      --map-dir "$ARC_RAIDERS_ROOT/MapPlacements/BuriedCity_01_P"

Then in Blender: **Reload Heightmap Only** (no Stage 1).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
from typing import Any


def _load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = (len(sorted_vals) - 1) * p
    lo = int(math.floor(idx))
    hi = int(math.ceiling(idx))
    if lo == hi:
        return sorted_vals[lo]
    t = idx - lo
    return sorted_vals[lo] * (1.0 - t) + sorted_vals[hi] * t


def aabb_from_city_core(path: str) -> tuple[float, float, float, float] | None:
    data = _load_json(path)
    hm = data.get("heightmap") or data
    loc = hm.get("location") or {}
    size = hm.get("size_xy") or [0, 0]
    try:
        x = float(loc.get("x", 0))
        y = float(loc.get("y", 0))
        sx = float(size[0])
        sy = float(size[1])
    except (TypeError, ValueError, IndexError):
        return None
    if sx <= 0 or sy <= 0:
        return None
    return x, y, x + sx, y + sy


def aabb_from_placements_csv(path: str) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                x = float(row.get("x") or 0)
                y = float(row.get("y") or 0)
            except (TypeError, ValueError):
                continue
            if abs(x) > 5_000_000 or abs(y) > 5_000_000:
                continue
            xs.append(x)
            ys.append(y)
    if len(xs) < 8:
        return None
    xs.sort()
    ys.sort()
    return (
        _percentile(xs, 0.01),
        _percentile(ys, 0.01),
        _percentile(xs, 0.99),
        _percentile(ys, 0.99),
    )


def landscape_footprint_from_bounds(
    path: str,
) -> tuple[float, float, float, float, float, dict[str, Any]] | None:
    """Return (loc_x, loc_y, size_x, size_y, scale_z_cm, scale_dict) or None."""
    data = _load_json(path)
    hm = data.get("heightmap") or data
    loc = hm.get("location") or {}
    size = hm.get("size_xy") or [0, 0]
    scale = hm.get("scale") or {"x": 1.0, "y": 1.0, "z": 1.0}
    try:
        lx = float(loc.get("x", 0))
        ly = float(loc.get("y", 0))
        sx = float(size[0])
        sy = float(size[1])
        z_cm = float(hm.get("scale_z_cm") or (float(scale.get("z", 1.0) or 1.0) * 100.0))
    except (TypeError, ValueError, IndexError):
        return None
    if sx <= 0 or sy <= 0:
        return None
    return lx, ly, sx, sy, z_cm, dict(scale)


def footprint_from_raw_png(
    width: int,
    height: int,
    *,
    cell_cm: float = 100.0,
    loc_x: float = 0.0,
    loc_y: float = 403200.0,
) -> tuple[float, float, float, float]:
    """Guess landscape footprint when bounds JSON was already city-cropped."""
    return loc_x, loc_y, (width - 1) * cell_cm, (height - 1) * cell_cm


def prepare_heightmap(
    map_dir: str,
    *,
    map_name: str | None = None,
    pad_frac: float = 0.02,
    pad_min_cm: float = 500.0,
    cell_cm: float = 100.0,
) -> dict[str, Any]:
    from PIL import Image
    import numpy as np

    map_dir = os.path.abspath(map_dir)
    if not os.path.isdir(map_dir):
        raise FileNotFoundError(map_dir)

    map_name = (map_name or os.path.basename(map_dir.rstrip("\\/"))).strip() or "Map"
    out_png = os.path.join(map_dir, f"{map_name}_heightmap.png")
    pre_fix = os.path.join(map_dir, f"{map_name}_heightmap_pre_fix.png")
    city_core = os.path.join(map_dir, f"{map_name}_city_core_bounds.json")
    land_bounds = os.path.join(map_dir, f"{map_name}_landscape_bounds.json")
    world_bounds = os.path.join(map_dir, f"{map_name}_world_bounds.json")
    csv_path = os.path.join(map_dir, "placements.csv")

    # Prefer an uncropped backup; else current PNG (may already be cropped — still OK to re-run).
    src_png = pre_fix if os.path.isfile(pre_fix) else out_png
    if not os.path.isfile(src_png):
        raise FileNotFoundError(
            f"No heightmap PNG in {map_dir} (expected {map_name}_heightmap.png "
            f"or {map_name}_heightmap_pre_fix.png)"
        )

    already_flipped = False
    if os.path.isfile(land_bounds):
        try:
            already_flipped = bool(
                (_load_json(land_bounds).get("heightmap") or {}).get("image_row0_is_max_y")
            )
        except (OSError, json.JSONDecodeError, TypeError):
            already_flipped = False

    if not os.path.isfile(pre_fix) and os.path.isfile(out_png):
        # First run: keep an uncropped/unflipped backup when possible.
        shutil.copy2(out_png, pre_fix)
        src_png = pre_fix
        # If the only PNG was already Blender-prepared, undo the flip for the backup.
        if already_flipped:
            from PIL import Image
            import numpy as np

            tmp = np.flipud(np.array(Image.open(pre_fix)))
            Image.fromarray(tmp).save(pre_fix)
            already_flipped = False

    # City AABB
    city = None
    if os.path.isfile(city_core):
        city = aabb_from_city_core(city_core)
    if city is None and os.path.isfile(csv_path):
        city = aabb_from_placements_csv(csv_path)
    if city is None:
        raise RuntimeError(
            "Need city_core_bounds.json or placements.csv to crop to the city footprint"
        )
    cminx, cminy, cmaxx, cmaxy = city
    pad_x = max(pad_min_cm, (cmaxx - cminx) * pad_frac)
    pad_y = max(pad_min_cm, (cmaxy - cminy) * pad_frac)
    crop_min_x, crop_min_y = cminx - pad_x, cminy - pad_y
    crop_max_x, crop_max_y = cmaxx + pad_x, cmaxy + pad_y

    # Landscape footprint for the *source* PNG (pre_fix = full LandscapeExporter AABB).
    land_loc_x = land_loc_y = 0.0
    land_sx = land_sy = 0.0
    scale_z_cm = 250.0
    scale: dict[str, Any] = {"x": 1.0, "y": 1.0, "z": 2.5}
    raw = Image.open(src_png)
    a = np.array(raw)
    if a.ndim != 2:
        raise RuntimeError(f"Expected 16-bit grayscale heightmap, got shape {a.shape}")
    h, w = a.shape

    sidecar = os.path.join(map_dir, f"{map_name}_heightmap_raw_meta.json")
    used_meta = False
    if os.path.isfile(sidecar):
        sm = _load_json(sidecar)
        land_loc_x = float(sm.get("loc_x", 0))
        land_loc_y = float(sm.get("loc_y", 403200))
        land_sx = float(sm.get("size_x", (w - 1) * cell_cm))
        land_sy = float(sm.get("size_y", (h - 1) * cell_cm))
        scale_z_cm = float(sm.get("scale_z_cm", scale_z_cm))
        if isinstance(sm.get("scale"), dict):
            scale = sm["scale"]
        used_meta = True
    else:
        # Try landscape/world bounds only when they match the source PNG pixel size.
        for meta_src in (land_bounds, world_bounds):
            if not os.path.isfile(meta_src):
                continue
            fp = landscape_footprint_from_bounds(meta_src)
            if fp is None:
                continue
            cand_lx, cand_ly, cand_sx, cand_sy, cand_z, cand_scale = fp
            expect_w = int(round(cand_sx / cell_cm)) + 1
            expect_h = int(round(cand_sy / cell_cm)) + 1
            if abs(expect_w - w) <= 2 and abs(expect_h - h) <= 2:
                land_loc_x, land_loc_y = cand_lx, cand_ly
                land_sx, land_sy = cand_sx, cand_sy
                scale_z_cm, scale = cand_z, cand_scale
                used_meta = True
                break

    if not used_meta:
        land_loc_x, land_loc_y, land_sx, land_sy = footprint_from_raw_png(
            w, h, cell_cm=cell_cm, loc_x=0.0, loc_y=403200.0
        )
        # Prefer Z scale from any existing bounds JSON even when XY was city-cropped.
        for meta_src in (land_bounds, world_bounds):
            if not os.path.isfile(meta_src):
                continue
            fp = landscape_footprint_from_bounds(meta_src)
            if fp is not None:
                scale_z_cm = fp[4]
                scale = fp[5]
                break

    # Persist raw footprint so later runs (after bounds were city-cropped) stay correct.
    if src_png == pre_fix or not os.path.isfile(sidecar):
        with open(sidecar, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "loc_x": land_loc_x,
                    "loc_y": land_loc_y,
                    "size_x": land_sx,
                    "size_y": land_sy,
                    "scale_z_cm": scale_z_cm,
                    "scale": scale,
                    "resolution": [w, h],
                    "note": "Raw LandscapeExporter footprint for prepare_blender_heightmap",
                },
                fh,
                indent=2,
            )

    # Intersect crop with landscape
    iminx = max(land_loc_x, crop_min_x)
    iminy = max(land_loc_y, crop_min_y)
    imaxx = min(land_loc_x + land_sx, crop_max_x)
    imaxy = min(land_loc_y + land_sy, crop_max_y)
    if imaxx - iminx < 1000 or imaxy - iminy < 1000:
        raise RuntimeError(
            f"City crop does not overlap landscape footprint "
            f"(land={land_loc_x},{land_loc_y} {land_sx}x{land_sy}; "
            f"city={cminx},{cminy}→{cmaxx},{cmaxy})"
        )

    cell_x = land_sx / (w - 1)
    cell_y = land_sy / (h - 1)
    px0 = int(math.floor((iminx - land_loc_x) / cell_x))
    px1 = int(math.ceil((imaxx - land_loc_x) / cell_x))
    py0 = int(math.floor((iminy - land_loc_y) / cell_y))
    py1 = int(math.ceil((imaxy - land_loc_y) / cell_y))
    px0 = max(0, min(px0, w - 1))
    px1 = max(px0 + 1, min(px1, w - 1))
    py0 = max(0, min(py0, h - 1))
    py1 = max(py0 + 1, min(py1, h - 1))

    crop = a[py0 : py1 + 1, px0 : px1 + 1].copy()
    zeros = crop == 0
    zero_frac = float(zeros.mean()) if crop.size else 0.0
    # Fill missing samples with the median of valid heights — NOT 32768 mid.
    # Mid-fill sits at Displace mid_level=0.5 while real terrain is lower, which
    # creates rectangular wall spikes in Blender CityGroundPlane.
    valid = ~zeros
    if valid.any():
        fill_u16 = int(np.median(crop[valid]))
    else:
        fill_u16 = 32768
    crop[zeros] = fill_u16
    # Flip so PNG row0 = UE max Y (Blender mirror_y UV convention).
    crop = np.flipud(crop)

    Image.fromarray(crop).save(out_png)

    new_loc_x = land_loc_x + px0 * cell_x
    new_loc_y = land_loc_y + py0 * cell_y
    new_sx = (crop.shape[1] - 1) * cell_x
    new_sy = (crop.shape[0] - 1) * cell_y

    hm_block = {
        "name": map_name,
        "base_cell_size": float(cell_cm),
        "base_dimensions": int(max(crop.shape)),
        "min_height": None,
        "granularity": None,
        "height_mid": 32768,
        "height_zscale": 1.0 / 128.0,
        "scale_z_cm": float(scale_z_cm),
        "image_row0_is_max_y": True,
        "location": {"x": new_loc_x, "y": new_loc_y, "z": 0.0},
        "scale": scale,
        "size_xy": [new_sx, new_sy],
        "image": f"{map_name}_heightmap.png",
        "resolution": [int(crop.shape[1]), int(crop.shape[0])],
    }
    payload = {
        "map_name": map_name,
        "source": "landscape",
        "units": "unreal_cm",
        "axes": "passthrough",
        "city_aligned": True,
        "city_core_bounds_path": city_core if os.path.isfile(city_core) else "",
        "heightmap": hm_block,
    }
    for path in (land_bounds, world_bounds):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    return {
        "map_name": map_name,
        "png": out_png,
        "pre_fix": pre_fix,
        "landscape_bounds": land_bounds,
        "resolution": [int(crop.shape[1]), int(crop.shape[0])],
        "location": hm_block["location"],
        "size_xy": hm_block["size_xy"],
        "zero_fill_frac": zero_frac,
        "zero_fill_value": int(fill_u16),
        "image_row0_is_max_y": True,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Crop/flip/fill FModel heightmap for Blender (no Stage 1 reimport)"
    )
    ap.add_argument(
        "--map-dir",
        required=True,
        help="MapPlacements/{Map} folder containing heightmap PNG + bounds/CSV",
    )
    ap.add_argument("--map-name", default=None, help="Defaults to folder basename")
    ap.add_argument("--cell-cm", type=float, default=100.0, help="Landscape XY scale cm")
    args = ap.parse_args(argv)
    try:
        result = prepare_heightmap(
            args.map_dir, map_name=args.map_name, cell_cm=args.cell_cm
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Prepared Blender heightmap:")
    for k in (
        "png",
        "landscape_bounds",
        "resolution",
        "location",
        "size_xy",
        "zero_fill_frac",
        "zero_fill_value",
    ):
        print(f"  {k}: {result[k]}")
    print(
        "\nNext: In Blender -> Arc Raiders -> Reload Heightmap Only "
        "(or search F3: arc.reload_placement_heightmap)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
