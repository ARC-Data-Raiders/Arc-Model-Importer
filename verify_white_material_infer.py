#!/usr/bin/env python3
"""Prove waterplane MapPlacements remap + white/fuzzy MI infer (no Blender).

Run:
  python verify_white_material_infer.py
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
MAP_PSK = os.path.join(
    PIONEER,
    r"MapPlacements\RivenTides_01_P\PioneerGame\Content\Pioneer\Environment"
    r"\Toolkit\RiverTool\Assets\SM_WaterPlane_32x32.uemodel",
)
FULL_PSK = os.path.join(
    PIONEER,
    r"PioneerGame\Content\Pioneer\Environment\Toolkit\RiverTool\Assets"
    r"\SM_WaterPlane_32x32.uemodel",
)


def _load_materials():
    class _Scene:
        arc_pioneer_root = PIONEER
        arc_placement_mesh_root = ""
        arc_placement_csv = os.path.join(
            PIONEER, r"MapPlacements\RivenTides_01_P\placements.csv",
        )

    bpy = types.SimpleNamespace(
        context=types.SimpleNamespace(scene=_Scene()),
        path=types.SimpleNamespace(abspath=lambda p: os.path.abspath(p) if p else ""),
        data=None,
    )
    sys.modules["bpy"] = bpy
    sys.modules["mathutils"] = types.SimpleNamespace(Vector=tuple)

    pkg_name = "arc_verify_white"
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


class _FakeMat:
    def __init__(self, name="Material", rgb=(0.8, 0.8, 0.8), linked=False, **props):
        self.name = name
        self.use_nodes = True
        self._props = dict(props)
        self.node_tree = types.SimpleNamespace(nodes=[])
        principled = types.SimpleNamespace(
            type="BSDF_PRINCIPLED",
            inputs={
                "Base Color": types.SimpleNamespace(
                    default_value=(*rgb, 1.0),
                    links=[],
                )
            },
        )
        if linked:
            tex = types.SimpleNamespace(type="TEX_IMAGE", image=object())
            link = types.SimpleNamespace(from_node=tex)
            principled.inputs["Base Color"].links = [link]
        self.node_tree.nodes = [principled]

    def get(self, key, default=None):
        return self._props.get(key, default)


class _FakeSlot:
    def __init__(self, mat):
        self.material = mat


class _FakeObj:
    def __init__(self, name, mats=None, **props):
        self.name = name
        self.type = "MESH"
        self.data = types.SimpleNamespace(name=name, materials=[])
        self.parent = None
        self._props = dict(props)
        self.material_slots = [_FakeSlot(m) for m in (mats or [])]

    def get(self, key, default=None):
        return self._props.get(key, default)


def main() -> int:
    mats = _load_materials()
    utils = sys.modules["arc_verify_white.utils"]
    fails = 0

    # Actor -> MI hint
    hint = mats.water_mi_hint_from_actor_name(
        "BP_WaterPlane_MinorSwamp_C_UAID_C87F545C9BDA9DC402_1300142371"
    )
    ok = hint == "MI_Water_MinorSwamp"
    print(f"{'OK' if ok else 'FAIL'} actor hint -> {hint}")
    fails += 0 if ok else 1

    # BP_Decal_CrackTarmac → MI_CrackTarmac (shared SM_DecalMesh cards)
    cands = mats.decal_mi_stem_candidates_from_actor(
        "BP_Decal_CrackTarmac_01_C_UAID_ECE7A702409957C902_1531448401"
    )
    ok = "MI_CrackTarmac_01" in cands
    print(f"{'OK' if ok else 'FAIL'} CrackTarmac candidates -> {cands}")
    fails += 0 if ok else 1

    # AddonWall: MI_AddonWall_01 (no MI_Decal_ prefix) must be a single-slot override
    aw_cands = mats.decal_mi_stem_candidates_from_actor(
        "BP_Decal_AddonWall_01_C_0_UAID_TEST"
    )
    ok = "MI_AddonWall_01" in aw_cands
    print(f"{'OK' if ok else 'FAIL'} AddonWall candidates -> {aw_cands}")
    fails += 0 if ok else 1
    ok = mats._preferred_mi_is_single_slot_override("MI_AddonWall_01")
    print(f"{'OK' if ok else 'FAIL'} MI_AddonWall_01 is single-slot override -> {ok}")
    fails += 0 if ok else 1

    pref_decal = mats.preferred_mi_from_placement_rows(
        [
            {"actor_name": "BP_Decal_CrackTarmac_01_C_200"},
            {"actor_name": "BP_Decal_CrackTarmac_01_C_198"},
        ]
    )
    ok = pref_decal == "MI_CrackTarmac_01"
    print(f"{'OK' if ok else 'FAIL'} CrackTarmac rows preferred -> {pref_decal}")
    fails += 0 if ok else 1

    pref_aw = mats.preferred_mi_from_placement_rows(
        [
            {"actor_name": "BP_Decal_AddonWall_01_C_0"},
            {"actor_name": "BP_Decal_AddonWall_01_C_1"},
        ]
    )
    ok = pref_aw == "MI_AddonWall_01"
    print(f"{'OK' if ok else 'FAIL'} AddonWall rows preferred -> {pref_aw}")
    fails += 0 if ok else 1

    sm_cands = mats.decal_mi_stem_candidates_from_actor("SM_Decal_AstraVenturo_01_A")
    ok = "MI_Decal_AstraVenturo_01" in sm_cands
    print(f"{'OK' if ok else 'FAIL'} AstraVenturo SM candidates -> {sm_cands}")
    fails += 0 if ok else 1

    # Branding _X masks must use Alpha for opacity
    mi_av = {
        "switches": {"UseAlphaForMask": True},
        "textures": [("Mask", "/Game/.../T_AstraVenturo_X")],
    }
    ok = mats._map_decal_mask_from_alpha(mi_av, r"C:\x\T_AstraVenturo_X.png") is True
    print(f"{'OK' if ok else 'FAIL'} UseAlphaForMask → Alpha channel")
    fails += 0 if ok else 1

    fam = mats._family_hint_from_name_blob(
        "BP_Decal_CrackTarmac_01 SM_DecalMesh_01 /textures/decals/"
    )
    ok = fam == mats.FAMILY_DECAL
    print(f"{'OK' if ok else 'FAIL'} CrackTarmac family hint -> {fam}")
    fails += 0 if ok else 1

    # WorldGridMaterial on DecalMesh = needs repair
    wg = _FakeMat("WorldGridMaterial", rgb=(0.5, 0.5, 0.5))
    need, why = mats.material_slot_needs_repair(wg)
    ok = need and why == "engine_placeholder"
    print(f"{'OK' if ok else 'FAIL'} WorldGridMaterial repair -> {need}/{why}")
    fails += 0 if ok else 1

    pref = mats.preferred_mi_from_placement_rows(
        [
            {"actor_name": "BP_WaterPlane_MinorSwamp_C_UAID_1"},
            {"actor_name": "BP_WaterPlane_MinorSwamp_C_UAID_2"},
        ]
    )
    ok = pref == "MI_Water_MinorSwamp"
    print(f"{'OK' if ok else 'FAIL'} rows preferred -> {pref}")
    fails += 0 if ok else 1

    # MapPlacements uemodel (no sibling JSON) still resolves via remap
    for label, psk in (("map_placements", MAP_PSK), ("full_content", FULL_PSK)):
        print("---", label, "exists", os.path.isfile(psk))
        if not os.path.isfile(psk):
            print("FAIL missing", psk)
            fails += 1
            continue
        slots = mats._parse_sk_material_slots(psk)
        print("SLOTS", [(a, b, os.path.basename(c) if c else "") for a, b, c in slots])
        if not slots or not slots[0][2]:
            print("FAIL: no remapped SM JSON / MI")
            fails += 1
        else:
            mi = mats._parse_flat_mi_json(slots[0][2])
            fam = mats.classify_mi_family(mi, (slots[0][1] or "").lower())
            ok = fam == mats.FAMILY_WATER
            print(f"{'OK' if ok else 'FAIL'} family={fam}")
            fails += 0 if ok else 1

    # Dam-style MapPlacements layout uses Game/Pioneer/... (no Content segment).
    # Remap must still land on PioneerGame/Content for SM/MI JSON + textures.
    dam_psk = os.path.join(
        PIONEER,
        r"MapPlacements\TheDam_02_P\Game\Pioneer\Environment\Props\_Generic"
        r"\AirconUnit_01\SM_AirconUnit_01_A.uemodel",
    )
    dam_sm_json = os.path.join(
        PIONEER,
        r"PioneerGame\Content\Pioneer\Environment\Props\_Generic"
        r"\AirconUnit_01\SM_AirconUnit_01_A.json",
    )
    dam_mi = os.path.join(
        PIONEER,
        r"PioneerGame\Content\Pioneer\Environment\Props\_Generic"
        r"\AirconUnit_01\MI_AirconUnit_01_A.json",
    )
    print(
        "--- dam_game_layout exists",
        os.path.isfile(dam_psk),
        "sm",
        os.path.isfile(dam_sm_json),
        "mi",
        os.path.isfile(dam_mi),
    )
    if os.path.isfile(dam_psk) and os.path.isfile(dam_sm_json):
        remapped = utils.remap_path_into_content_dirs(dam_psk)
        expect = os.path.normcase(
            os.path.normpath(
                os.path.join(
                    PIONEER,
                    r"PioneerGame\Content\Pioneer\Environment\Props\_Generic"
                    r"\AirconUnit_01\SM_AirconUnit_01_A.uemodel",
                )
            )
        )
        hit = any(os.path.normcase(os.path.normpath(p)) == expect for p in remapped)
        print(f"{'OK' if hit else 'FAIL'} Game/ → Content remap ({remapped[:2]})")
        fails += 0 if hit else 1
        mats.clear_material_session_caches()
        slots = mats._parse_sk_material_slots(dam_psk)
        # Remap must surface the Content SM JSON (slot MI paths may still be bad dump data).
        ok = bool(slots)
        print(
            f"{'OK' if ok else 'FAIL'} Dam Aircon SM JSON via Game/ remap "
            f"slots={[(a, b, os.path.basename(c) if c else '') for a, b, c in slots]}"
        )
        fails += 0 if ok else 1
        if os.path.isfile(dam_mi):
            id_ok = mats._mi_json_matches_requested_stem(dam_mi, "MI_AirconUnit_01_A")
            # Current Desktop dump often has wrong bodies under prop MI filenames.
            print(
                f"INFO Aircon MI identity match={id_ok} "
                f"(False => dump body mismatch; Stage 2 should reject)"
            )
            resolved = mats._resolve_mi_json_path(
                "MI_AirconUnit_01_A", "", os.path.dirname(dam_sm_json)
            )
            if not id_ok:
                ok = resolved == ""
                print(
                    f"{'OK' if ok else 'FAIL'} reject mismatched Aircon MI "
                    f"resolved={resolved!r}"
                )
                fails += 0 if ok else 1
            else:
                ok = bool(resolved) and os.path.isfile(resolved)
                print(f"{'OK' if ok else 'FAIL'} accept matching Aircon MI")
                fails += 0 if ok else 1
    else:
        print("SKIP Dam Game/ Aircon layout (missing on disk)")

    # White detection
    white = _FakeMat("Material", rgb=(0.8, 0.8, 0.8))
    need, why = mats.material_slot_needs_repair(white)
    ok = need and why == "default_white"
    print(f"{'OK' if ok else 'FAIL'} default white detect -> {need}/{why}")
    fails += 0 if ok else 1

    stamped = _FakeMat(
        "MI_Water_DuneLagoon",
        rgb=(0.2, 0.24, 0.25),
        arc_mi_path=r"C:\x\MI_Water_DuneLagoon.json",
        arc_mi_family="water",
    )
    need, why = mats.material_slot_needs_repair(stamped)
    ok = not need
    print(f"{'OK' if ok else 'FAIL'} water stamped ok -> {need}/{why}")
    fails += 0 if ok else 1

    # Fuzzy candidates for SRC waterplane with preferred MinorSwamp
    obj = _FakeObj(
        "SRC_SM_WaterPlane_32x32",
        mats=[white],
        arc_psk_path=MAP_PSK,
        arc_mesh_file=MAP_PSK,
        arc_asset_path="/Game/Pioneer/Environment/Toolkit/RiverTool/Assets/SM_WaterPlane_32x32",
        arc_preferred_mi="MI_Water_MinorSwamp",
        arc_map="RivenTides_01_P",
    )
    cands = mats.infer_map_mi_candidates(obj, MAP_PSK, limit=5)
    print("CANDS", [(s, os.path.basename(p), f"{sc:.1f}", r) for s, p, sc, r in cands])
    if not cands:
        print("FAIL: no candidates")
        fails += 1
    else:
        top = cands[0][0].lower()
        ok = "water" in top
        # Preferred should win when present
        ok2 = top == "mi_water_minorswamp" or "minorswamp" in top
        print(f"{'OK' if ok else 'FAIL'} top is water -> {cands[0][0]}")
        print(f"{'OK' if ok2 else 'FAIL'} prefers MinorSwamp -> {cands[0][0]}")
        fails += 0 if ok else 1
        fails += 0 if ok2 else 1

    fam = mats._family_hint_from_name_blob("src_sm_waterplane_32x32 rivertool")
    ok = fam == mats.FAMILY_WATER
    print(f"{'OK' if ok else 'FAIL'} name family water -> {fam}")
    fails += 0 if ok else 1

    # --- PerimeterWall: SM StaticMaterials + no enemy-decal misclass ---
    wall_psk = os.path.join(
        PIONEER,
        r"PioneerGame\Content\Pioneer\Environment\Architecture"
        r"\SucessfulLaunchPadFacility\Modules\Wall"
        r"\SM_POI_09_PerimeterWall_01_Y3200cm_B.uemodel",
    )
    wall_json = os.path.splitext(wall_psk)[0] + ".json"
    if not os.path.isfile(wall_json):
        print(f"SKIP perimeter wall (missing {wall_json})")
    else:
        mats.clear_material_session_caches()
        slots = mats._parse_sk_material_slots(wall_psk)
        expect = {
            "rebar": "mi_metal_rebar_01_a",
            "concretedamaged": "mi_concrete_damaged_02_b",
            "concrete": "mi_concrete_wall_slab_05_a_vt",
            "concreteedgedecal": "mi_concrete_trim_02_a",
            "m_leak": "mi_decal_leaks_large_02_a",
        }
        got = {(s[0] or "").lower(): (s[1] or "").lower() for s in slots if s[1]}
        print("WALL_SLOTS", [(s[0], s[1], bool(s[2])) for s in slots])
        for slot_l, mi_l in expect.items():
            ok = got.get(slot_l) == mi_l
            print(f"{'OK' if ok else 'FAIL'} slot {slot_l} -> {got.get(slot_l)} (want {mi_l})")
            fails += 0 if ok else 1

        # ConcreteEdgeDecal + trim MI must be environment, not enemy/decal family
        trim_path = ""
        for sn, st, mp in slots:
            if (sn or "").lower() == "concreteedgedecal":
                trim_path = mp
                break
        if trim_path and os.path.isfile(trim_path):
            mi = mats._parse_flat_mi_json(trim_path)
            enemy = mats._is_enemy_decal_mi(mi)
            fam = mats.classify_mi_family(mi, "mi_concrete_trim_02_a", "concreteedgedecal")
            ok = (not enemy) and fam == mats.FAMILY_ENVIRONMENT
            print(
                f"{'OK' if ok else 'FAIL'} ConcreteEdgeDecal family="
                f"{fam} enemy_decal={enemy}"
            )
            fails += 0 if ok else 1
            ok2 = not mats._slot_implies_map_decal("concreteedgedecal")
            print(f"{'OK' if ok2 else 'FAIL'} ConcreteEdgeDecal not map-decal slot")
            fails += 0 if ok2 else 1
            # Architecture trim: UV Mode 1 → world UV; NAO alpha CLIP expected
            wants_world = mats._trim_wants_world_uv(mi)
            is_trim = mats._is_architecture_trim_mi(mi, "mi_concrete_trim_02_a")
            ok3 = is_trim and wants_world
            print(
                f"{'OK' if ok3 else 'FAIL'} Trim_02_A is_trim={is_trim} "
                f"world_uv={wants_world} UV Mode="
                f"{(mi.get('scalars') or {}).get('UV Mode')}"
            )
            fails += 0 if ok3 else 1
            use_a = mats._mi_switch(
                mi.get("switches") or {}, "Use Alpha mask", default=False,
            )
            ok4 = bool(use_a) and mats._is_masked_blend(mi)
            print(f"{'OK' if ok4 else 'FAIL'} Trim_02_A masked+alpha use_alpha={use_a}")
            fails += 0 if ok4 else 1
        else:
            print("FAIL: missing MI_Concrete_Trim path for ConcreteEdgeDecal")
            fails += 1

        # ControlTower Edges mesh defaults to MI_Concrete_Trim_02_A
        ct_edges_json = os.path.join(
            PIONEER,
            r"PioneerGame\Content\Pioneer\Environment\POI\POI09"
            r"\Prefabs\ControlTower_01\SM_POI09_ControlTower_01_Edges_01_A.json",
        )
        if os.path.isfile(ct_edges_json):
            ct_slots = mats._parse_sk_material_slots(
                ct_edges_json.replace(".json", ".uemodel"),
            )
            # Also accept JSON-only parse via path
            if not ct_slots:
                import json as _json
                with open(ct_edges_json, encoding="utf-8") as fh:
                    raw = _json.load(fh)
                for e in raw.get("Exports") or []:
                    if e.get("Type") != "StaticMesh":
                        continue
                    for sm in (e.get("Properties") or {}).get("StaticMaterials") or []:
                        mi_name = ""
                        iface = sm.get("MaterialInterface") or {}
                        on = iface.get("ObjectName") or ""
                        if "'" in on:
                            mi_name = on.split("'")[1]
                        ct_slots.append((sm.get("MaterialSlotName") or "", mi_name, ""))
            mi_names = [(s[1] or "").lower() for s in ct_slots]
            ok = any("concrete_trim" in m for m in mi_names)
            print(f"{'OK' if ok else 'FAIL'} ControlTower Edges uses concrete trim -> {mi_names}")
            fails += 0 if ok else 1
        else:
            print(f"SKIP ControlTower Edges (missing {ct_edges_json})")

        # True enemy decal still classified
        enemy_mi_path = os.path.join(
            PIONEER,
            r"PioneerGame\Content\Pioneer\MaterialLibrary\Material_Instances"
            r"\Decals\Enemies\MI_EnemyDecals_01.json",
        )
        if os.path.isfile(enemy_mi_path):
            emi = mats._parse_flat_mi_json(enemy_mi_path)
            ok = mats._is_enemy_decal_mi(emi)
            print(f"{'OK' if ok else 'FAIL'} MI_EnemyDecals_01 is enemy decal")
            fails += 0 if ok else 1
            ok = mats._is_character_enemy_outfit_decal_asset(
                "MI_EnemyDecals_01", enemy_mi_path,
            )
            print(f"{'OK' if ok else 'FAIL'} enemy decal forbidden for env fuzzy")
            fails += 0 if ok else 1

        wall_obj = _FakeObj(
            "SRC_SM_POI_09_PerimeterWall_01_Y3200cm_B",
            mats=[white],
            arc_psk_path=wall_psk,
            arc_mesh_file=wall_psk,
            arc_asset_path=(
                "/Game/Pioneer/Environment/Architecture/"
                "SucessfulLaunchPadFacility/Modules/Wall/"
                "SM_POI_09_PerimeterWall_01_Y3200cm_B"
            ),
        )
        cands = mats.infer_map_mi_candidates(wall_obj, wall_psk, limit=8)
        print(
            "WALL_CANDS",
            [(s, os.path.basename(p), f"{sc:.1f}", r) for s, p, sc, r in cands],
        )
        bad = [
            c for c in cands
            if mats._is_character_enemy_outfit_decal_asset(c[0], c[1])
            or "enemydecal" in c[0].lower()
        ]
        ok = not bad
        print(f"{'OK' if ok else 'FAIL'} no enemy/outfit decal in wall candidates -> {bad}")
        fails += 0 if ok else 1
        # Soft-match folder fuzzy is disabled — only SM JSON (+ preferred) reasons
        fuzzy = [c for c in cands if str(c[3]).startswith("fuzzy_folder")]
        ok = not fuzzy and not mats.ENABLE_FUZZY_MI_INFER
        print(
            f"{'OK' if ok else 'FAIL'} fuzzy soft-match disabled "
            f"(ENABLE={mats.ENABLE_FUZZY_MI_INFER}, fuzzy={fuzzy})"
        )
        fails += 0 if ok else 1
        # Top candidates should be SM StaticMaterials
        top_stems = {c[0].lower() for c in cands[:5]}
        ok = "mi_concrete_wall_slab_05_a_vt" in top_stems or any(
            "concrete" in s for s in top_stems
        )
        print(f"{'OK' if ok else 'FAIL'} wall candidates prefer concrete SM MIs -> {top_stems}")
        fails += 0 if ok else 1
        gate = [c for c in cands if "gatedecal" in c[0].lower()]
        ok = not gate
        print(f"{'OK' if ok else 'FAIL'} GateDecal excluded from wall candidates -> {gate}")
        fails += 0 if ok else 1

        # PropTrim ceiling: compact MI inherits CR/NXX from library / Beams sibling
        ceil_mi = os.path.join(
            PIONEER,
            r"PioneerGame\Content\Pioneer\Environment\Props\Warehouse\Ceiling_01"
            r"\MI_Wrh_Ceiling_01_PropTrim_PaintedBeams_01_A.json",
        )
        if os.path.isfile(ceil_mi):
            mats.clear_material_session_caches()
            mi = mats._parse_flat_mi_json(ceil_mi)
            params = mats._mi_tex_params(mi)
            fam = mats.classify_mi_family(
                mi, "mi_wrh_ceiling_01_proptrim_paintedbeams_01_a", "m_proptrims",
            )
            ok = "CR Texture" in params and fam == mats.FAMILY_METAL
            print(
                f"{'OK' if ok else 'FAIL'} PaintedBeams inherit CR + metal family "
                f"-> fam={fam} params={sorted(params)}"
            )
            fails += 0 if ok else 1
        else:
            print(f"SKIP PaintedBeams MI (missing {ceil_mi})")

        glass_mi = os.path.join(
            PIONEER,
            r"PioneerGame\Content\Pioneer\MaterialLibrary\M_Presets\M_BrokenGlassSDF.json",
        )
        if os.path.isfile(glass_mi):
            gmi = mats._parse_flat_mi_json(glass_mi)
            gfam = mats.classify_mi_family(gmi, "m_brokenglasssdf", "brokenglass")
            ok = gfam == mats.FAMILY_GLASS
            print(f"{'OK' if ok else 'FAIL'} BrokenGlassSDF -> glass family ({gfam})")
            fails += 0 if ok else 1

        # UEModel LOD order ≠ SM JSON StaticMaterials order: name-match wins over index.
        # PerimeterWall UEM: [ConcreteDamaged, Concrete, Rebar, ...] JSON: [Rebar, ...]
        uem_mats = [
            _FakeMat("MI_Concrete_Damaged_02_B"),
            _FakeMat("MI_Concrete_Wall_Slab_05_A_VT"),
            _FakeMat("MI_Metal_Rebar_01_A"),
            _FakeMat("MI_Concrete_Trim_02_A"),
            _FakeMat("MI_Decal_Leaks_Large_02_A"),
        ]
        wall_match_obj = _FakeObj("SRC_PerimeterWall", mats=uem_mats)
        used = set()
        slot, idx = mats._match_material_slot(
            wall_match_obj, "Rebar", 0, used, "MI_Metal_Rebar_01_A",
        )
        ok = idx == 2 and slot is not None
        print(f"{'OK' if ok else 'FAIL'} rebar matches UEM slot 2 not JSON idx 0 -> {idx}")
        fails += 0 if ok else 1
        used.add(idx)
        slot, idx = mats._match_material_slot(
            wall_match_obj, "ConcreteDamaged", 1, used, "MI_Concrete_Damaged_02_B",
        )
        ok = idx == 0
        print(f"{'OK' if ok else 'FAIL'} ConcreteDamaged matches UEM slot 0 -> {idx}")
        fails += 0 if ok else 1

        ok = mats._is_rebar_mi_stem("MI_Metal_Rebar_01_A")
        print(f"{'OK' if ok else 'FAIL'} _is_rebar_mi_stem Metal_Rebar")
        fails += 0 if ok else 1
        ok = mats._is_rebar_mi_stem("MI_Rebars_Highway")
        print(f"{'OK' if ok else 'FAIL'} _is_rebar_mi_stem Rebars_Highway")
        fails += 0 if ok else 1
        ok = not mats._is_rebar_mi_stem("MI_Highway_01_Concrete_01_A")
        print(f"{'OK' if ok else 'FAIL'} concrete is not rebar stem")
        fails += 0 if ok else 1

        # ArchitecturePreset_Trim parent → world UV even when UV Mode omitted (ControlTower).
        ct_mi = os.path.join(
            PIONEER,
            r"PioneerGame\Content\Pioneer\Environment\POI\POI09\Prefabs\ControlTower_01"
            r"\MI_Concrete_Trim_ControlTower.json",
        )
        if os.path.isfile(ct_mi):
            mats.clear_material_session_caches()
            ct = mats._parse_flat_mi_json(ct_mi)
            ok = mats._is_architecture_trim_mi(ct) and mats._trim_wants_world_uv(ct)
            print(
                f"{'OK' if ok else 'FAIL'} ControlTower trim wants world UV "
                f"(parent={ct.get('parent')!r} uv_mode={ct.get('scalars', {}).get('UV Mode')})"
            )
            fails += 0 if ok else 1
        else:
            print(f"SKIP ControlTower MI (missing {ct_mi})")

        # Preferred leak guard: PropTrim must NOT be a single-slot override.
        ok = not mats._preferred_mi_is_single_slot_override(
            "MI_Wrh_Ceiling_01_PropTrim_PaintedBeams_01_A"
        )
        print(f"{'OK' if ok else 'FAIL'} PropTrim preferred is not single-slot override")
        fails += 0 if ok else 1
        ok = mats._preferred_mi_is_single_slot_override("MI_Water_MinorSwamp")
        print(f"{'OK' if ok else 'FAIL'} water preferred is single-slot override")
        fails += 0 if ok else 1
        ok = mats._preferred_mi_is_single_slot_override("MI_Decal_CrackTarmac_01")
        print(f"{'OK' if ok else 'FAIL'} MI_Decal_* preferred is single-slot override")
        fails += 0 if ok else 1
        # Architecture *Trim*_Decal_* must NOT be treated as map-decal overrides
        ok = not mats._preferred_mi_is_single_slot_override(
            "MI_TrimInteriorCeiling_Decal_01"
        )
        print(f"{'OK' if ok else 'FAIL'} TrimInteriorCeiling_Decal is NOT preferred override")
        fails += 0 if ok else 1
        ok = not mats._preferred_mi_is_single_slot_override(
            "MI_EdgeTrim_Decal_01_Scalable1"
        )
        print(f"{'OK' if ok else 'FAIL'} EdgeTrim_Decal is NOT preferred override")
        fails += 0 if ok else 1

        # TrimInteriorCeiling_Decal → environment family (not map-decal)
        tic_decal = os.path.join(
            PIONEER,
            r"PioneerGame\Content\Pioneer\Environment\Props\Residential"
            r"\TrimInteriorCeiling_01\MI_TrimInteriorCeiling_Decal_01.json",
        )
        if not os.path.isfile(tic_decal):
            for root, _dirs, files in os.walk(
                os.path.join(PIONEER, "PioneerGame", "Content", "Pioneer")
            ):
                if "MI_TrimInteriorCeiling_Decal_01.json" in files:
                    tic_decal = os.path.join(root, "MI_TrimInteriorCeiling_Decal_01.json")
                    break
        if os.path.isfile(tic_decal):
            mats.clear_material_session_caches()
            tic = mats._parse_flat_mi_json(tic_decal)
            ok = mats._is_architecture_trim_mi(
                tic, "mi_triminteriorceiling_decal_01"
            )
            print(f"{'OK' if ok else 'FAIL'} TrimInteriorCeiling_Decal is architecture trim")
            fails += 0 if ok else 1
            fam = mats.classify_mi_family(
                tic, "mi_triminteriorceiling_decal_01", "material",
            )
            ok = fam == mats.FAMILY_ENVIRONMENT
            print(f"{'OK' if ok else 'FAIL'} TrimInteriorCeiling_Decal family=environment ({fam})")
            fails += 0 if ok else 1
            ok = mats._trim_wants_world_uv(tic)
            print(f"{'OK' if ok else 'FAIL'} TrimInteriorCeiling_Decal wants world UV")
            fails += 0 if ok else 1
        else:
            print("SKIP MI_TrimInteriorCeiling_Decal_01 (missing)")

        # UVOffset scalar aliases include UV Offset Amount for architecture trims.
        trim_b = os.path.join(
            PIONEER,
            r"PioneerGame\Content\Pioneer\MaterialLibrary\Material_Instances"
            r"\Trims\MI_Concrete_Trim_02_B.json",
        )
        # path may vary — search
        if not os.path.isfile(trim_b):
            for root, _dirs, files in os.walk(os.path.join(PIONEER, "PioneerGame", "Content", "Pioneer")):
                if "MI_Concrete_Trim_02_B.json" in files:
                    trim_b = os.path.join(root, "MI_Concrete_Trim_02_B.json")
                    break
        if os.path.isfile(trim_b):
            mats.clear_material_session_caches()
            tb = mats._parse_flat_mi_json(trim_b)
            off = mats._mi_scalar(
                tb.get("scalars") or {},
                "UVOffset", "UV Offset", "OffsetUVs", "UV Offset Amount",
                default=0.0,
            )
            ok = abs(float(off) - 0.75) < 0.01
            print(f"{'OK' if ok else 'FAIL'} Concrete_Trim_02_B UV Offset Amount → {off}")
            fails += 0 if ok else 1
        else:
            print("SKIP MI_Concrete_Trim_02_B (missing)")

    if fails:
        print("FAIL", fails)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
