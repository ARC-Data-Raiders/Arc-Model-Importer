#!/usr/bin/env python3
"""Validate MI texture-role inventory → family for Dam props (no Blender).

Run:
  python verify_mi_tex_inventory.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types

ADDON = os.path.dirname(os.path.abspath(__file__))
PIONEER = os.environ.get(
    "ARC_PIONEER_ROOT",
    r"C:\Users\Gideon\Desktop\Arc_Raiders_Current",
)
DAM = os.path.join(
    PIONEER,
    r"PioneerGame\Content\Pioneer\Environment\Props\Dam",
)


def _load_materials():
    bpy = types.SimpleNamespace(
        context=types.SimpleNamespace(scene=types.SimpleNamespace()),
        path=types.SimpleNamespace(abspath=lambda p: os.path.abspath(p) if p else ""),
        data=None,
    )
    sys.modules["bpy"] = bpy
    sys.modules["mathutils"] = types.SimpleNamespace(Vector=tuple)

    pkg_name = "arc_verify_inv"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [ADDON]
    sys.modules[pkg_name] = pkg

    def _load(sub):
        path = os.path.join(ADDON, f"{sub}.py")
        spec = importlib.util.spec_from_file_location(f"{pkg_name}.{sub}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{sub}"] = mod
        spec.loader.exec_module(mod)
        return mod

    props = types.ModuleType(f"{pkg_name}.properties")
    props.BODY_ALBEDO = {}
    props.BODY_NORMAL = {}
    sys.modules[f"{pkg_name}.properties"] = props

    for sub in ("utils", "textures", "palette_calibration", "fmdex"):
        try:
            _load(sub)
        except Exception:
            # Minimal stubs when optional deps fail
            stub = types.ModuleType(f"{pkg_name}.{sub}")
            sys.modules[f"{pkg_name}.{sub}"] = stub

    # Ensure utils helpers used by materials parse
    utils = sys.modules[f"{pkg_name}.utils"]
    if not hasattr(utils, "first_ue_export"):
        def first_ue_export(data, type_name=None):
            if isinstance(data, list):
                for e in data:
                    if not type_name or e.get("Type") == type_name:
                        return e
                return data[0] if data else {}
            return data if isinstance(data, dict) else {}
        utils.first_ue_export = first_ue_export
    if not hasattr(utils, "get_logger"):
        class _L:
            def info(self, *a, **k): pass
            def debug(self, *a, **k): pass
            def error(self, *a, **k): pass
            def warning(self, *a, **k): pass
        utils.get_logger = lambda: _L()

    mats = _load("materials")
    return mats


def main() -> int:
    if not os.path.isdir(DAM):
        print(f"FAIL: Dam props dir missing: {DAM}")
        return 1

    mats = _load_materials()
    expected = {
        "MI_Dam_HydroTower_01_A": "environment",
        "MI_Dam_HydroTower_01_B": "environment",
        "MI_Dam_HydroTower_01_C": "environment",
        "MI_Dam_HydroTower_01_D": "environment",
        "MI_HydroDome_Trim_01": "environment",
        "MI_Dam_Planter_01_A": "environment",
        "MI_Dam_HydroDome_01_PropTrim_A": "metal",
        "MI_Dam_HydroDome_01_Rubber": "environment",
        "MI_Dam_WaterTank_01": "simple",
        "MI_Dam_WaterTankCarrier_01": "simple",
    }
    failed = 0
    print("=== Dam prop MI inventory -> family ===")
    for dirpath, _, files in os.walk(DAM):
        for fn in sorted(files):
            if not (fn.startswith("MI_") and fn.endswith(".json")):
                continue
            stem = fn[:-5]
            path = os.path.join(dirpath, fn)
            mi = mats._parse_flat_mi_json(path)
            inv = mats.inventory_mi_textures(mi)
            fam = mats.classify_mi_family(mi, stem.lower(), "")
            roles = {r: names for r, names in (inv.get("roles") or {}).items()}
            print(f"{stem}: family={fam} maps={inv.get('map_count')} roles={roles}")
            # Sign must never be the only base_cr
            if mats._inv_has(inv, mats._ROLE_SIGN):
                sign_params = (inv.get("roles") or {}).get(mats._ROLE_SIGN) or []
                base = (inv.get("roles") or {}).get(mats._ROLE_BASE_CR) or []
                if any(s.lower() == "signtexture" for s in base):
                    print(f"  FAIL: SignTexture classified as base_cr")
                    failed += 1
                if fam == "simple" and stem.startswith("MI_Dam_HydroTower"):
                    print(f"  FAIL: HydroTower fell through to simple")
                    failed += 1
            exp = expected.get(stem)
            if exp and fam != exp:
                print(f"  FAIL: expected {exp}, got {fam}")
                failed += 1
            elif exp:
                print(f"  OK -> {exp}")

    # Spot-check: SignTexture alone -> decal
    sign_mi = {"textures": [("SignTexture", "/Game/T_RoadSign_20_X")], "switches": {}, "parent": ""}
    fam = mats.classify_mi_family(sign_mi, "mi_cmr_ilmaialedorato_01_trim_b", "")
    print(f"SignTexture-only -> {fam} (expect decal)")
    if fam != "decal":
        failed += 1

    # Clothing ColorMask must not be remapped by inventory table
    cloth = {
        "textures": [
            ("ColorMask", "/Game/T_ColorMask"),
            ("TextureArray_Colors", "/Game/TA_Colors"),
        ],
        "switches": {},
        "parent": "",
    }
    inv = mats.inventory_mi_textures(cloth)
    if not mats._inv_has(inv, mats._ROLE_CLOTHING):
        print("FAIL: ColorMask not tagged clothing")
        failed += 1
    else:
        print("OK: ColorMask -> clothing role")
    fam = mats._family_from_tex_inventory(inv, cloth, "mi_outfit")
    if fam is not None:
        print(f"FAIL: clothing inventory remapped to {fam}")
        failed += 1
    else:
        print("OK: clothing inventory leaves family unset for Arc Texturer")

    if failed:
        print(f"\n{failed} failure(s)")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
