"""Verify Kalika hero MI resolve + simple-family classify (no Blender required).

  python verify_hero_kalika_mi.py
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
KALIKA_MESHES = os.path.join(
    PIONEER,
    r"PioneerGame\Content\Pioneer\Characters\Heroes\Kalika\Resources\Base\Meshes",
)
PSK = os.path.join(KALIKA_MESHES, "SK_Kalika_Base_Body.psk")
GEAR_MI = os.path.normpath(
    os.path.join(KALIKA_MESHES, "..", "Materials", "MI_Kalika_Base_Gear.json")
)
TOP_MI = os.path.normpath(
    os.path.join(KALIKA_MESHES, "..", "Materials", "MI_Kalika_Base_Topbody.json")
)
TEX_DIR = os.path.normpath(os.path.join(KALIKA_MESHES, "..", "Textures"))


def _load_modules():
    class _Scene:
        arc_pioneer_root = PIONEER
        arc_placement_mesh_root = ""
        arc_placement_csv = ""

    bpy = types.SimpleNamespace(
        context=types.SimpleNamespace(scene=_Scene()),
        path=types.SimpleNamespace(abspath=lambda p: os.path.abspath(p) if p else ""),
        data=None,
    )
    sys.modules["bpy"] = bpy
    sys.modules["mathutils"] = types.SimpleNamespace(Vector=tuple)

    pkg_name = "arc_verify_hero_kalika"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [ADDON]
    sys.modules[pkg_name] = pkg

    def load(sub: str):
        path = os.path.join(ADDON, f"{sub}.py")
        spec = importlib.util.spec_from_file_location(f"{pkg_name}.{sub}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{sub}"] = mod
        setattr(pkg, sub, mod)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod

    utils = load("utils")
    textures = load("textures")
    load("palette_calibration")
    props = types.ModuleType(f"{pkg_name}.properties")
    props.BODY_ALBEDO = ""
    props.BODY_NORMAL = ""
    sys.modules[f"{pkg_name}.properties"] = props
    setattr(pkg, "properties", props)
    fm = types.ModuleType(f"{pkg_name}.fmdex")
    fm.resolve_export_file = lambda *a, **k: ""
    fm.lookup_asset_path = lambda *a, **k: (None, [])
    fm.package_to_game_path = lambda *a, **k: ""
    fm.fmdex_summary_for_report = lambda: "fmdex=off"
    fm.ensure_loaded = lambda: None
    sys.modules[f"{pkg_name}.fmdex"] = fm
    setattr(pkg, "fmdex", fm)
    materials = load("materials")
    return utils, textures, materials


def main() -> int:
    fails = 0

    def check(ok: bool, msg: str) -> None:
        nonlocal fails
        print(f"{'OK' if ok else 'FAIL'} {msg}")
        if not ok:
            fails += 1

    check(os.path.isfile(PSK), f"PSK exists: {PSK}")
    check(os.path.isfile(GEAR_MI), f"Gear MI exists: {GEAR_MI}")
    check(os.path.isfile(TOP_MI), f"Topbody MI exists: {TOP_MI}")

    _utils, textures, materials = _load_modules()

    model_type = textures.detect_model_type(PSK)
    check(model_type == "unknown", f"detect_model_type={model_type!r} (expect unknown)")

    for stem, expect in (
        ("MI_Kalika_Base_Gear", GEAR_MI),
        ("MI_Kalika_Base_Topbody", TOP_MI),
    ):
        found = materials._resolve_mi_json_path(stem, "", KALIKA_MESHES)
        check(
            os.path.normcase(os.path.normpath(found or ""))
            == os.path.normcase(os.path.normpath(expect)),
            f"_resolve_mi_json_path({stem}) -> {found}",
        )

    obj_path = (
        "/Game/Pioneer/Characters/Heroes/Kalika/Resources/Base/Materials/"
        "MI_Kalika_Base_Gear.0"
    )
    via_obj = materials._resolve_mi_json_path(
        "MI_Kalika_Base_Gear", obj_path, KALIKA_MESHES
    )
    check(os.path.isfile(via_obj or ""), f"ObjectPath resolve -> {via_obj}")

    slots = materials._parse_sk_material_slots(PSK)
    check(len(slots) >= 2, f"SK slots count={len(slots)}")
    resolved = [(n, s, p) for n, s, p in slots if p and os.path.isfile(p)]
    check(len(resolved) >= 2, f"SK slots with MI JSON: {[s for _n, s, _p in resolved]}")

    for path in (GEAR_MI, TOP_MI):
        mi = materials._parse_flat_mi_json(path)
        params = materials._mi_tex_params(mi)
        fam = materials.classify_mi_family(
            mi, os.path.splitext(os.path.basename(path))[0].lower()
        )
        check(
            "Color" in params,
            f"{os.path.basename(path)} has Color param (params={sorted(params)})",
        )
        check(
            fam == materials.FAMILY_SIMPLE,
            f"{os.path.basename(path)} family={fam} (expect simple)",
        )
        folders = materials._character_layout_search_folders(path, PSK)
        check(
            any(
                os.path.normcase(os.path.normpath(f)) == os.path.normcase(TEX_DIR)
                for f in folders
            ),
            f"layout folders include Textures",
        )
        # Resolve PNG paths without loading into Blender Image datablocks.
        for param, obj_path in mi.get("textures") or []:
            if param not in ("Color", "Normal", "PM_Normals", "RoughnessMetal"):
                continue
            fpath = materials._resolve_mi_texture_path(obj_path, folders)
            check(
                bool(fpath and os.path.isfile(fpath)),
                f"{os.path.basename(path)} {param} -> {fpath or '(missing)'}",
            )

    print()
    print("PASS" if fails == 0 else f"FAILED ({fails})")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
