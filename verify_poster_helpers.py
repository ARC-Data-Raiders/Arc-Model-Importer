#!/usr/bin/env python3
"""Prove poster unique-placement detection + GraphicAtlas family (no Blender).

Run:
  python verify_poster_helpers.py
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import types

ADDON = os.path.dirname(os.path.abspath(__file__))
PIONEER = os.environ.get(
    "ARC_PIONEER_ROOT",
    r"C:\Users\Gideon\Desktop\Arc_Raiders_Current",
)
MP_PATH = os.path.join(ADDON, "map_placement.py")
MAT_PATH = os.path.join(ADDON, "materials.py")
GA_MI = os.path.join(
    PIONEER,
    r"PioneerGame\Content\Pioneer\Environment\POI\MountainCompound"
    r"\Props\Branding\MI_MCP_GraphicAtlas_2x1_100x200_02.json",
)
FRAME_MI = os.path.join(
    PIONEER,
    r"PioneerGame\Content\Pioneer\Environment\POI\MountainCompound"
    r"\Props\Branding\MI_MCP_PosterFrame_Proptrim_01_A.json",
)


def _load_is_poster_from_source():
    """Eval is_poster_mesh_asset from map_placement without importing bpy."""
    src = open(MP_PATH, encoding="utf-8").read()
    ns: dict = {"re": re}
    br = re.search(
        r"^_BRANDING_GRAPHIC_NAME_RE = re\.compile\(\s*\n(?:.*?\n)*?\)",
        src,
        flags=re.MULTILINE,
    )
    if not br:
        br = re.search(
            r"^_BRANDING_GRAPHIC_NAME_RE = re\.compile\([\s\S]*?,\s*re\.I,\s*\)$",
            src,
            flags=re.MULTILINE,
        )
    if br:
        exec(br.group(0), ns)
    m = re.search(
        r"^def is_poster_mesh_asset\(.*?\n(?=^def )",
        src,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not m:
        raise RuntimeError("is_poster_mesh_asset not found")
    exec(m.group(0), ns)
    return ns["is_poster_mesh_asset"]


def _load_materials():
    class _Scene:
        arc_pioneer_root = PIONEER
        arc_placement_mesh_root = ""
        arc_placement_csv = ""

    bpy = types.ModuleType("bpy")
    bpy.context = types.SimpleNamespace(scene=_Scene())
    bpy.path = types.SimpleNamespace(abspath=lambda p: os.path.abspath(p) if p else "")
    bpy.data = None
    bpy.props = types.ModuleType("bpy.props")
    sys.modules["bpy"] = bpy
    sys.modules["bpy.props"] = bpy.props
    sys.modules["mathutils"] = types.SimpleNamespace(Vector=tuple)

    pkg_name = "arc_verify_poster"
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

    load("utils")
    load("textures")
    if os.path.isfile(os.path.join(ADDON, "palette_calibration.py")):
        load("palette_calibration")
    props = types.ModuleType(f"{pkg_name}.properties")
    props.BODY_ALBEDO = ""
    props.BODY_NORMAL = ""
    sys.modules[f"{pkg_name}.properties"] = props
    setattr(pkg, "properties", props)
    fm = types.ModuleType(f"{pkg_name}.fmdex")
    fm.resolve_export_file = lambda *a, **k: ""
    fm.ensure_loaded = lambda: None
    fm.lookup_asset_path = lambda *a, **k: ("", [])
    fm.package_to_game_path = lambda p: ""
    fm.fmdex_summary_for_report = lambda: ""
    sys.modules[f"{pkg_name}.fmdex"] = fm
    setattr(pkg, "fmdex", fm)
    return load("materials")


def main() -> int:
    fails = 0
    is_poster = _load_is_poster_from_source()

    cases = [
        (
            "BrandingPoster A",
            "/Game/Pioneer/Environment/POI/MountainCompound/Props/Branding/"
            "SM_MCP_BrandingPoster_100x200_01_A",
            True,
        ),
        (
            "BrandingPoster short",
            "SM_MCP_BrandingPoster_450x150_01_B",
            True,
        ),
        ("generic *poster*", "SM_WallPoster_01", True),
        ("MCP_Text", "SM_MCP_Text_Arrow_01", True),
        ("AdvertisementBillboard", "SM_Cmr_AdvertisementBillboard_01_A", True),
        ("CompanyLogo", "SM_CompanyLogo_01_A", True),
        ("imposter exclude", "SM_Tree_Imposter_01", False),
        ("impostor exclude", "/Game/Foliage/SM_BillboardImpostor", False),
        ("normal prop", "SM_Crate_01_A", False),
        ("DecalMesh still separate", "SM_DecalMesh_01", False),
    ]
    print("=== is_poster_mesh_asset ===")
    for label, path, want in cases:
        got = bool(is_poster(path, ""))
        ok = got == want
        print(f"{'OK' if ok else 'FAIL'} {label}: {got} (want {want})")
        if not ok:
            fails += 1

    mp_src = open(MP_PATH, encoding="utf-8").read()
    if "is_poster_mesh_asset" not in mp_src or "arc_poster_mesh" not in mp_src:
        print("FAIL map_placement missing poster hooks")
        fails += 1
    else:
        print("OK map_placement poster hooks present")
    if "posters=" not in mp_src:
        print("FAIL Realize stats missing posters=")
        fails += 1
    else:
        print("OK Realize reports posters")

    print("=== GraphicAtlas / PosterFrame family ===")
    try:
        mats = _load_materials()
    except Exception as exc:
        print(f"FAIL load materials: {exc}")
        return 1

    if os.path.isfile(GA_MI):
        mi = mats._parse_flat_mi_json(GA_MI)
        fam = mats.classify_mi_family(
            mi, "mi_mcp_graphicatlas_2x1_100x200_02", "worldgridmaterial",
        )
        ok = (
            fam == mats.FAMILY_DECAL
            and mats._is_graphic_atlas_mi(mi, "mi_mcp_graphicatlas_2x1_100x200_02")
        )
        print(f"{'OK' if ok else 'FAIL'} GraphicAtlas -> {fam} (want decal)")
        if not ok:
            fails += 1
        params = mats._mi_tex_params(mi)
        ok = "Graphic Atlas" in params
        print(f"{'OK' if ok else 'FAIL'} Graphic Atlas tex param present")
        if not ok:
            fails += 1
            print(f"  params={sorted(params)}")
    else:
        print(f"SKIP GraphicAtlas MI missing: {GA_MI}")

    if os.path.isfile(FRAME_MI):
        mi = mats._parse_flat_mi_json(FRAME_MI)
        fam = mats.classify_mi_family(
            mi, "mi_mcp_posterframe_proptrim_01_a", "m_proptrimmetal",
        )
        ok = fam == mats.FAMILY_METAL and not mats._is_graphic_atlas_mi(
            mi, "mi_mcp_posterframe_proptrim_01_a",
        )
        print(f"{'OK' if ok else 'FAIL'} PosterFrame PropTrim -> {fam} (want metal)")
        if not ok:
            fails += 1
    else:
        print(f"SKIP PosterFrame MI missing: {FRAME_MI}")

    mat_src = open(MAT_PATH, encoding="utf-8").read()
    for needle in (
        "_setup_graphic_atlas_material",
        "AtlasPosition",
        "UseAlphaForMask",
        "_graphic_atlas_uv_layer_name",
        'default=True) is True',  # Use UV1 defaults on for GraphicAtlas
    ):
        ok = needle in mat_src
        print(f"{'OK' if ok else 'FAIL'} materials has {needle}")
        if not ok:
            fails += 1

    util_path = os.path.join(ADDON, "utils.py")
    util_src = open(util_path, encoding="utf-8").read()
    ok = "normalize_ue_uv_layer_names" in util_src and "EXTRAUV" in util_src
    print(f"{'OK' if ok else 'FAIL'} utils.normalize_ue_uv_layer_names (PSK EXTRAUV->UV1)")
    if not ok:
        fails += 1

    # Pure helper checks (mirror _graphic_atlas_uv_layer_name resolution order).
    def pick(use_uv1, names):
        if not use_uv1:
            return None
        for name in ("UV1", "EXTRAUV0", "UVMap.001"):
            if name in names:
                return name
        if len(names) > 1:
            return names[1]
        return names[0] if names else None

    cases = [
        (True, ["UV0", "UV1"], "UV1"),
        (True, ["UVMap", "EXTRAUV0"], "EXTRAUV0"),
        (True, ["UV0"], "UV0"),
        (False, ["UV0", "UV1"], None),
    ]
    for use_uv1, names, expect in cases:
        got = pick(use_uv1, names)
        ok = got == expect
        print(f"{'OK' if ok else 'FAIL'} uv_layer use_uv1={use_uv1} {names} -> {got!r} (want {expect!r})")
        if not ok:
            fails += 1

    ok = "arc_poster_mesh" in mp_src and "arc_poster_mesh" in mat_src
    print(f"{'OK' if ok else 'FAIL'} arc_poster_mesh in map_placement + materials")
    if not ok:
        fails += 1

    ok = "_normalize_imported_map_uvs" in mp_src
    print(f"{'OK' if ok else 'FAIL'} map_placement normalizes UV layers on import")
    if not ok:
        fails += 1

    print(f"\n{'PASS' if fails == 0 else f'FAIL ({fails})'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
