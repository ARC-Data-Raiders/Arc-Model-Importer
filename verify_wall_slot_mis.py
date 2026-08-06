#!/usr/bin/env python3
"""Verify per-slot wall MI stem strip + family classification (no Blender).

Run:
  python verify_wall_slot_mis.py
"""
from __future__ import annotations

import os
import sys

ADDON = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ADDON)

from verify_mi_tex_inventory import _load_materials  # noqa: E402

PIONEER = os.environ.get(
    "ARC_PIONEER_ROOT",
    r"C:\Users\Gideon\Desktop\Arc_Raiders_Current",
)
CONTENT = os.path.join(PIONEER, r"PioneerGame\Content")

WALL_MIS = [
    (
        "MI_Concrete_Trim_02_A",
        os.path.join(
            CONTENT,
            r"Pioneer\MaterialLibrary\Material_Instances\Themes\Generic\MI_Concrete_Trim_02_A.json",
        ),
        "environment",
        {"CR", "NAO"},
    ),
    (
        "MI_Concrete_Wall_Dam_03_Leaks",
        os.path.join(
            CONTENT,
            r"Pioneer\Environment\POI\POI16\WaterControl\MI_Concrete_Wall_Dam_03_Leaks.json",
        ),
        "environment",
        {"CR", "CR Blend", "NOH", "Overlay"},
    ),
    (
        "MI_POI16_Concrete_Wall_Tower_01_NoLeaks",
        os.path.join(
            CONTENT,
            r"Pioneer\Environment\POI\POI16\MainControl\MI_POI16_Concrete_Wall_Tower_01_NoLeaks.json",
        ),
        "environment",
        {"CR", "CR Blend", "Overlay"},
    ),
    (
        "MI_POI16_Rsr_Walls_01_White",
        os.path.join(
            CONTENT,
            r"Pioneer\Environment\POI\POI16\Materials\MI_POI16_Rsr_Walls_01_White.json",
        ),
        "environment",
        {"CR", "PaintBreakup", "Overlay"},
    ),
    (
        "MI_POI16_InteriorWalls_White_01",
        os.path.join(
            CONTENT,
            r"Pioneer\Environment\POI\POI16\Materials\MI_POI16_InteriorWalls_White_01.json",
        ),
        "environment",
        {"CR", "NOH", "Overlay"},
    ),
    (
        "MI_POI16_Roof_Tower_01",
        os.path.join(
            CONTENT,
            r"Pioneer\Environment\POI\POI16\MainControl\MI_POI16_Roof_Tower_01.json",
        ),
        "environment",
        {"Base_Material_CR", "Breakup_Material_CR", "Breakup Mask - Linear Grayscale"},
    ),
]


def main() -> int:
    m = _load_materials()
    errors = []

    print("=== stem strip ===")
    cases = {
        "MI_Foo.001": "MI_Foo",
        "MI_Foo_force_rebuild": "MI_Foo",
        "MI_Foo_force_rebuild.001": "MI_Foo",
        "MI_Foo_force_rebuild_force_rebuild": "MI_Foo",
        "MI_Foo.mat": "MI_Foo",
        "MI_Foo_stale_trim": "MI_Foo",
        "MI_Foo_stale_env": "MI_Foo",
    }
    for raw, expect in cases.items():
        got = m._mi_stem_from_blender_name(raw)
        ok = got == expect
        print(f"  {'OK' if ok else 'FAIL'} {raw!r} -> {got!r} (expect {expect!r})")
        if not ok:
            errors.append(f"stem {raw}")

    print("\n=== wall MI families ===")
    for stem, path, expect_fam, need_params in WALL_MIS:
        if not os.path.isfile(path):
            errors.append(f"missing {path}")
            print(f"FAIL missing {stem}")
            continue
        mi = m._parse_flat_mi_json(path)
        fam = m.classify_mi_family(mi, stem.lower(), stem.lower())
        params = m._mi_tex_params(mi)
        missing = sorted(p for p in need_params if p not in params)
        ok = fam == expect_fam and not missing
        print(f"{'OK' if ok else 'FAIL'} {stem}: family={fam} missing={missing}")
        if fam != expect_fam:
            errors.append(f"family {stem}={fam}")
        if missing:
            errors.append(f"params {stem} missing {missing}")
        # Paint / dual-blend MIs must trigger env v4 rebuild when unstamped
        if any(k in ("PaintBreakup", "CR Blend", "Breakup_Material_CR") for k in need_params):
            fake = type("M", (), {"get": lambda self, k, d=None: ""})()
            if not m._needs_env_setup_rebuild(fake, path):
                errors.append(f"env rebuild not required for {stem}")
                print(f"  FAIL env rebuild gate for {stem}")
            else:
                print(f"  OK env v4 rebuild gate")

    if errors:
        print(f"\nFAILED ({len(errors)}): {errors}")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
