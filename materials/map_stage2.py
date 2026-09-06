"""
Material setup — map_stage2 domain (split from materials.py monolith).
"""
from __future__ import annotations

import os
import re
import json
import struct
import math
import difflib

import bpy
from mathutils import Vector

from .. import utils
from .. import textures
from .. import palette_calibration
from ..properties import BODY_ALBEDO, BODY_NORMAL

from .common import (
    CTX_MAP,
    ENABLE_FUZZY_MI_INFER,
    _ENGINE_OR_EMPTY_MESH_RE,
    _SHARED_MI_MATERIALS,
    _ensure_mesh_material_slot_count,
    _find_mi_json_by_stem_identity,
    _is_default_white_rgb,
    _match_material_slot,
    _mesh_stem_variants,
    _mi_scalar,
    _norm_path_key,
    _parse_flat_mi_json,
    _parse_sk_material_slots,
    _principled_base_color_info,
    _proptrim_library_parent_stem,
    _resolve_mi_json_path,
    _resolve_mi_texture_path,
    _stem_tokens,
    clear_out_of_context_map_materials,
    context_from_model_type,
    material_has_map_forbidden_content,
    path_allowed_for_context,
)
from .classify import (
    FAMILY_DECAL,
    FAMILY_ENVIRONMENT,
    FAMILY_FOLIAGE,
    FAMILY_GLASS,
    FAMILY_METAL,
    FAMILY_ROAD,
    FAMILY_SAND,
    FAMILY_SIMPLE,
    FAMILY_TARP,
    FAMILY_TRIMMAP,
    FAMILY_WATER,
    FAMILY_WEAPON,
    _PLANE_ROAD_METERS_PER_TILE,
    _WATER_EXCLUDE_NAME,
    _is_architecture_trim_stem,
    _is_character_enemy_outfit_decal_asset,
    _is_enemy_decal_mi,
    _is_water_mi,
    _mi_tex_params,
    _needs_map_decal_mask_rebuild,
    _needs_proptrim_or_glass_rebuild,
    _needs_env_props_rebuild,
    _needs_trim_setup_rebuild,
    _needs_water_setup_rebuild,
    classify_mi_family,
)



def map_material_cache_key(psk_path: str) -> str:
    """Cache key so spline bake hashes ``SM_Foo-HEX.uemodel`` share one Stage 2 result."""
    if not psk_path:
        return ""
    psk_path = os.path.normpath(os.path.abspath(bpy.path.abspath(psk_path)))
    stem = os.path.splitext(os.path.basename(psk_path))[0]
    variants = _mesh_stem_variants(stem)
    base = stem
    for v in variants:
        if not re.search(r"-[0-9A-Fa-f]{4,10}$", v):
            if v.upper().startswith("SM_") or v.upper().startswith("SK_"):
                base = v
                break
            base = v
    return os.path.normcase(os.path.join(os.path.dirname(psk_path), base))



def is_engine_or_placeholder_mesh(psk_path: str = "", asset_path: str = "", object_name: str = "") -> bool:
    """True for Engine BasicShapes / nameless StaticMesh_N with no useful map MI."""
    blob = f"{psk_path} {asset_path} {object_name}".lower().replace("\\", "/")
    if "/engine/content/basicshapes/" in blob or "/engine/content/engine/" in blob:
        return True
    stem = os.path.splitext(os.path.basename(psk_path or asset_path or object_name))[0]
    stem = re.sub(r"^src_", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"^spline_", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"_[0-9a-f]{6,8}$", "", stem, flags=re.IGNORECASE)
    # Strip spline-bake hash before placeholder check
    stem = re.sub(r"-[0-9a-f]{4,10}$", "", stem, flags=re.IGNORECASE)
    return bool(_ENGINE_OR_EMPTY_MESH_RE.match(stem or ""))



def material_slot_needs_repair(mat) -> tuple[bool, str]:
    """True when a slot is empty / unstamped Arc white / default Principled white."""
    if mat is None:
        return True, "empty_slot"
    family = str(mat.get("arc_mi_family", "") or "").strip()
    mi_path = str(mat.get("arc_mi_path", "") or "").strip()
    name_l = (mat.name or "").lower()
    # Engine placeholder on DecalMesh cards — treat as unassigned
    if "worldgridmaterial" in name_l or name_l in {"material", "dots stroke"}:
        return True, "engine_placeholder"
    if family == FAMILY_WATER and _needs_water_setup_rebuild(mat, mi_path):
        return True, "stale_water_setup"
    if family and mi_path:
        # Already routed by Stage 2 / shared MI cache
        if family == FAMILY_WATER:
            return False, "water_ok"
        if family == FAMILY_DECAL and _needs_map_decal_mask_rebuild(mat, mi_path):
            return True, "stale_decal_mask"
        if family in (FAMILY_ENVIRONMENT, FAMILY_METAL, FAMILY_ROAD) and _needs_trim_setup_rebuild(
            mat, mi_path,
        ):
            return True, "stale_trim_setup"
        # FAMILY_SIMPLE is included because TrimMap metal used to land there.
        if family in (
            FAMILY_ENVIRONMENT, FAMILY_METAL, FAMILY_ROAD, FAMILY_TRIMMAP, FAMILY_SIMPLE,
        ) and _needs_env_props_rebuild(mat, mi_path):
            return True, "stale_env_props"
        if _needs_proptrim_or_glass_rebuild(mat, mi_path, str(mat.get("arc_mi_stem") or "")):
            return True, "stale_proptrim_or_glass"
        rgb, linked = _principled_base_color_info(mat)
        if linked:
            return False, "textured_ok"
        if rgb is not None and not _is_default_white_rgb(rgb):
            return False, "tinted_ok"
        # Stamped but still default white (failed soft setup)
        if _is_default_white_rgb(rgb) and not linked:
            return True, "stamped_white"
        return False, "stamped_ok"
    rgb, linked = _principled_base_color_info(mat)
    if linked:
        return False, "textured_unstamped"
    if name_l.startswith("mi_") or ".mi_" in name_l:
        # MI-named placeholder from BlenderUMap — rebuild from JSON
        return True, "mi_named_unbuilt"
    if rgb is None:
        return True, "no_principled"
    if _is_default_white_rgb(rgb):
        return True, "default_white"
    # Non-white unstamped colour — still try to upgrade via SM/MI when possible
    return False, "custom_tint"



def object_needs_material_repair(obj) -> tuple[bool, str]:
    """True when any mesh slot is empty or effectively white / unassigned."""
    if not obj or obj.type != "MESH":
        return False, "not_mesh"
    slots = list(obj.material_slots or [])
    if not slots:
        return True, "no_slots"
    reasons = []
    # Map props: character/outfit MI paths are crossover artifacts (belt on roof).
    is_map = str(obj.get("arc_model_type") or "").strip().lower() == "map"
    if not is_map:
        psk = str(obj.get("arc_psk_path") or obj.get("arc_mesh_file") or "")
        is_map = context_from_model_type("", psk) == CTX_MAP
    # SRC_* / arc_map stamps are always map Stage 1/2 meshes.
    if not is_map:
        name = getattr(obj, "name", "") or ""
        if name.startswith("SRC_") or str(obj.get("arc_map") or "").strip():
            is_map = True
    for slot in slots:
        mat = slot.material
        if is_map and mat is not None and material_has_map_forbidden_content(mat):
            reasons.append("out_of_context_outfit")
            continue
        need, why = material_slot_needs_repair(mat)
        if need:
            reasons.append(why)
    if reasons:
        return True, "+".join(sorted(set(reasons)))
    return False, "ok"



def water_mi_hint_from_actor_name(actor_name: str = "") -> str:
    """BP_WaterPlane_MinorSwamp_C_UAID_… → MI_Water_MinorSwamp.

    Known RiverTool BP suffixes map to cooked ``MI_Water_*`` beside
    ``SM_WaterPlane_32x32``. Generic ``BP_WaterPlane_C_*`` returns empty so fuzzy
    / map heuristics can choose (BuriedCity→DuneLagoon, BlueGate→BlueGate, etc.).
    """
    raw = (actor_name or "").strip()
    if not raw:
        return ""
    # Explicit known variants (order: longer first)
    known = (
        ("minorswamp", "MI_Water_MinorSwamp"),
        ("dunelagoon_b", "MI_Water_DuneLagoon_B"),
        ("dunelagoon", "MI_Water_DuneLagoon"),
        ("shallowlake_b", "MI_Water_ShallowLake_B"),
        ("shallowlake", "MI_Water_ShallowLake"),
        ("driedriver", "MI_Water_DriedRiver_01"),
        ("redlake", "MI_Water_RedLake_01"),
        ("swamp", "MI_Water_Swamp_01"),
        ("cleanwater2", "MI_Water_ShallowLake"),
        ("cleanwater", "MI_Water_ShallowLake"),
        ("buriedcity", "MI_Water_BuriedCity_01"),
        ("bluegate", "MI_WaterBlueGate_01"),
        ("floodedpowerhouse", "MI_Water_FloodedPowerhouse_01"),
        ("rockycreek", "MI_Water_River_Opaque_River_RockyCreek"),
    )
    low = raw.lower()
    if "waterplane" not in low.replace("_", "") and "sm_waterplane" not in low:
        # Still allow BP_WaterPlane_* without requiring the substring twice
        if "bp_waterplane" not in low and "waterplane" not in low:
            return ""
    for key, mi in known:
        if key in low:
            return mi
    m = re.search(
        r"BP_WaterPlane_([A-Za-z][A-Za-z0-9]*(?:_[A-Za-z][A-Za-z0-9]*)*?)(?:_C(?:_|$)|$)",
        raw,
        flags=re.IGNORECASE,
    )
    if not m:
        return ""
    variant = m.group(1)
    if not variant:
        return ""
    # Reject UAID-looking captures (hex blobs)
    if re.fullmatch(r"[0-9A-Fa-f]{8,}", variant.replace("_", "")):
        return ""
    if variant.lower() in {"c", "uaid"}:
        return ""
    return f"MI_Water_{variant}"



def _bp_decal_package_stem(actor_name: str = "") -> str:
    """``BP_Decal_AddonWall_01_C_0_…`` → ``BP_Decal_AddonWall_01``."""
    raw = (actor_name or "").strip()
    if not raw:
        return ""
    m = re.search(
        r"(BP_Decal_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*?)(?:_C(?:_|$)|$)",
        raw,
        flags=re.IGNORECASE,
    )
    return m.group(1) if m else ""



def _rank_bp_decal_mi_stems(stems: list[str], bp_body: str = "") -> list[str]:
    """Prefer MIs whose stem matches the BP body (AddonWall_01 → MI_AddonWall_01).

    BP_Decal_* packages often carry a leftover OverrideMaterials on a secondary
    DecalMesh1 component (parent-template residue) before the real DecalMesh slot.
    """
    body = (bp_body or "").strip().lower()
    ranked: list[tuple[tuple, str]] = []
    seen: set[str] = set()
    for stem in stems or []:
        s = (stem or "").strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        core = key
        if core.startswith("mi_"):
            core = core[3:]
        core_no_decal = core[6:] if core.startswith("decal_") else core
        exact = 0
        if body:
            if core == body or core_no_decal == body or core == f"decal_{body}":
                exact = 3
            elif body in core or body in core_no_decal or core_no_decal in body:
                exact = 2
            else:
                # Token overlap (Damage_Wall_01 ↔ Decal_Damage_Wall_01)
                body_toks = set(body.split("_"))
                mi_toks = set(core_no_decal.split("_"))
                if body_toks and body_toks <= mi_toks:
                    exact = 2
                elif body_toks and len(body_toks & mi_toks) >= max(2, len(body_toks) - 1):
                    exact = 1
        # Cooked class-looking *_C suffixes are usually the wrong leftover slot
        cooked = 1 if key.endswith("_c") else 0
        ranked.append(((-exact, cooked, len(key), key), s))
    ranked.sort(key=lambda t: t[0])
    return [s for _k, s in ranked]



def _mi_stems_from_bp_decal_override_json(
    bp_stem: str, psk_folder: str = "", bp_body: str = ""
) -> list[str]:
    """Read ``OverrideMaterials`` from ``BP_Decal_*.json`` (authoritative projector MI).

    Name-guess alone misses retargets (e.g. ``BP_Decal_CrackPlaster_01`` →
    ``MI_CrackWall_01``, ``BP_Decal_Drain_01`` → ``MI_Drain_02``).
    """
    stem = (bp_stem or "").strip()
    if not stem:
        return []
    # BP JSONs live beside MIs under MaterialLibrary/.../Decals/
    bp_path = _resolve_mi_json_path(stem, "", psk_folder or "")
    if not bp_path or not os.path.isfile(bp_path):
        return []
    try:
        with open(bp_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    try:
        for entry in utils.ue_export_entries(data):
            props = entry.get("Properties") or {}
            overrides = props.get("OverrideMaterials") or []
            if not isinstance(overrides, list):
                continue
            # Prefer the primary DecalMesh component over DecalMesh1 leftovers
            entry_name = str(entry.get("Name") or "").lower()
            primary = "decalmesh1" not in entry_name and (
                entry_name.startswith("decalmesh") or "decalmesh_gen" in entry_name
            )
            for ref in overrides:
                if not isinstance(ref, dict):
                    continue
                mi_stem = textures.mi_stem_from_material_ref(ref)
                if not mi_stem:
                    continue
                key = mi_stem.lower()
                if key in seen:
                    continue
                seen.add(key)
                if primary:
                    out.insert(0, mi_stem)
                else:
                    out.append(mi_stem)
    except Exception:
        pass
    body = bp_body
    if not body and stem.lower().startswith("bp_decal_"):
        body = stem[len("BP_Decal_") :]
    return _rank_bp_decal_mi_stems(out, body)



def _mi_json_is_map_decal_library(path: str = "") -> bool:
    """True when MI/BP JSON lives under MaterialLibrary Decals (map projector cards)."""
    pl = (path or "").replace("\\", "/").lower()
    if not pl:
        return False
    if "/materiallibrary/" in pl and "/decals/" in pl:
        return True
    if "/material_instances/decals/" in pl:
        return True
    return False



def decal_mi_stem_candidates_from_actor(actor_name: str = "") -> list[str]:
    """BP_Decal_CrackTarmac_01_C_… → [MI_CrackTarmac_01, MI_Decal_CrackTarmac_01].

    Also ``SM_Decal_AstraVenturo_01_A`` → ``MI_Decal_AstraVenturo_01``.
    Shared projector mesh is always ``SM_DecalMesh_*``; the BP/actor name carries the MI.

    Prefer ``OverrideMaterials`` from the BP JSON when present (authoritative), then
    name-guess ``MI_<body>`` / ``MI_Decal_<body>``.
    """
    raw = (actor_name or "").strip()
    if not raw:
        return []
    out: list[str] = []

    def _add(stem: str) -> None:
        stem = (stem or "").strip()
        if stem and stem not in out:
            out.append(stem)

    bp_stem = _bp_decal_package_stem(raw)
    if bp_stem:
        body = bp_stem[len("BP_Decal_") :] if bp_stem.lower().startswith("bp_decal_") else ""
        for ov in _mi_stems_from_bp_decal_override_json(bp_stem, "", body):
            _add(ov)
        if body:
            _add(f"MI_{body}")
            _add(f"MI_Decal_{body}")
        return out

    m = re.search(
        r"SM_Decal_([A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)",
        raw,
        flags=re.IGNORECASE,
    )
    if m:
        body = m.group(1)
        # Mesh letter variants: AstraVenturo_01_A → AstraVenturo_01
        body = re.sub(r"_[A-Za-z]$", "", body)
        joined = body.replace("_", "").lower()
        if joined.startswith("mesh") or joined in {"mesh01", "mesh02"}:
            return out
        _add(f"MI_Decal_{body}")
        _add(f"MI_{body}")
    return out



def preferred_mi_hint_from_actor_name(actor_name: str = "", psk_folder: str = "") -> str:
    """Water BP or BP_Decal/SM_Decal → preferred MI stem (resolved when possible).

    Only actor / BP / SM_Decal *names* are valid input — never pass asset paths or
    full object blobs (those can accidentally match ``SM_Decal_*`` / water tokens).
    """
    # Guard: callers sometimes concatenated asset paths; keep the actor-like token only.
    raw = (actor_name or "").strip()
    if not raw:
        return ""
    # If a path leaked in, prefer the last path segment / first whitespace token that
    # looks like BP_/SM_Decal_/StaticMeshActor_/waterplane.
    if "/" in raw or "\\" in raw or " " in raw:
        parts = re.split(r"[\s/\\]+", raw)
        pick = ""
        for p in parts:
            pl = p.lower()
            if (
                pl.startswith("bp_")
                or pl.startswith("sm_decal_")
                or "waterplane" in pl.replace("_", "")
                or pl.startswith("staticmeshactor")
            ):
                pick = p
                break
        raw = pick or parts[0]
    water = water_mi_hint_from_actor_name(raw)
    if water:
        return water
    # BP OverrideMaterials first (needs folder for resolve)
    bp_stem = _bp_decal_package_stem(raw)
    if bp_stem:
        body = bp_stem[len("BP_Decal_") :] if bp_stem.lower().startswith("bp_decal_") else ""
        for ov in _mi_stems_from_bp_decal_override_json(bp_stem, psk_folder or "", body):
            path = _resolve_mi_json_path(ov, "", psk_folder or "")
            if path:
                leaf = os.path.splitext(os.path.basename(path))[0].lower()
                if leaf.startswith("bp_"):
                    continue
                return ov
            # Authoritative override stem even if path resolve lags
            return ov
    cands = decal_mi_stem_candidates_from_actor(raw)
    if not cands:
        return ""
    for stem in cands:
        path = _resolve_mi_json_path(stem, "", psk_folder or "")
        if path and not os.path.basename(path).lower().startswith("bp_"):
            return stem
        if path and _mi_json_is_map_decal_library(path):
            # Skip BP JSON collision (stem resolved to blueprint package)
            leaf = os.path.splitext(os.path.basename(path))[0].lower()
            if leaf.startswith("bp_"):
                continue
            return stem
        if path:
            leaf = os.path.splitext(os.path.basename(path))[0].lower()
            if not leaf.startswith("bp_"):
                return stem
    return cands[0]



def _preferred_mi_is_single_slot_override(preferred_stem: str = "") -> bool:
    """True when preferred MI may replace slot 0 (water / map-decal cards only).

    Regular StaticMeshActor props must keep SM JSON StaticMaterials — never let a
    leaked preferred stem (or PropTrim/Vent atlas) clobber every slot.

    Architecture ``*Trim*_Decal_*`` / EdgeTrim names are NOT map decals even though
    they contain ``_decal_`` (that false positive wiped SMA slots / wrong family).

    Many map-decal MIs omit the ``MI_Decal_`` prefix (``MI_AddonWall_01``,
    ``MI_BrokenTile_01``, ``MI_Drain_02``). Accept those when the JSON resolves
    under MaterialLibrary Decals.
    """
    stem_raw = (preferred_stem or "").strip()
    stem = stem_raw.lower()
    if not stem:
        return False
    if stem.startswith("mi_water") or stem.startswith("m_water") or "waterplane" in stem:
        return True
    if "mi_water" in stem:
        return True
    # Architecture trim sheets that happen to include "Decal" in the MI name
    if _is_architecture_trim_stem(stem):
        return False
    if _is_prop_trim_atlas_mi_stem(stem) or _is_rebar_mi_stem(stem):
        return False
    if stem.startswith("mi_decal") or stem.startswith("mi_crack"):
        return True
    # Narrow: only leading map-decal patterns, not arbitrary *_decal_* mid-tokens
    if stem.startswith("m_decal") or stem.startswith("mi_cracktarmac"):
        return True
    # Decals library MIs without MI_Decal_ prefix (AddonWall, BrokenTile, …)
    if stem.startswith("mi_") or stem.startswith("m_"):
        path = _resolve_mi_json_path(stem_raw, "", "")
        if path and _mi_json_is_map_decal_library(path):
            leaf = os.path.splitext(os.path.basename(path))[0].lower()
            if not leaf.startswith("bp_"):
                return True
    return False



def _is_prop_trim_atlas_mi_stem(stem: str = "") -> bool:
    """Vent / HVAC / PropTrim sheet atlases — must not fuzzy-default onto unrelated props."""
    s = (stem or "").lower()
    if not s:
        return False
    if "proptrim" in s or "prop_trim" in s:
        return True
    if "ventilation" in s or "ventwall" in s or "ventspace" in s:
        return True
    # Token-boundary vent (avoid RivenTides / AstraVenturo false positives via path joins)
    return bool(re.search(r"(^|_)vent(ilation)?(_|$)", s))



def _is_rebar_mi_stem(stem: str = "") -> bool:
    """Metal rebar MIs — slot-local only; must not fuzzy-default onto whole props."""
    s = (stem or "").lower()
    if not s:
        return False
    return "rebar" in s



def preferred_mi_from_placement_rows(rows: list | None) -> str:
    """Majority BP water / BP_Decal MI hint shared by an instancer's placements."""
    from collections import Counter

    counts: Counter[str] = Counter()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        actor = str(row.get("actor_name") or "")
        hint = preferred_mi_hint_from_actor_name(actor)
        if hint and _preferred_mi_is_single_slot_override(hint):
            counts[hint] += 1
    if not counts:
        return ""
    stem, n = counts.most_common(1)[0]
    if n >= max(1, sum(counts.values()) // 2):
        return stem
    return ""



def _object_name_blob(obj, psk_path: str = "", asset_path: str = "") -> str:
    parts = [
        getattr(obj, "name", "") if obj is not None else "",
        getattr(getattr(obj, "data", None), "name", "") if obj is not None else "",
        str((obj.get("arc_asset_path") if obj is not None else "") or ""),
        str((obj.get("arc_actor_name") if obj is not None else "") or ""),
        str((obj.get("arc_preferred_mi") if obj is not None else "") or ""),
        asset_path or "",
        os.path.basename(psk_path or ""),
    ]
    if obj is not None and obj.parent is not None:
        parts.append(obj.parent.name)
    return " ".join(p for p in parts if p).lower()



def _family_hint_from_name_blob(blob: str) -> str:
    b = (blob or "").lower().replace("\\", "/")
    if any(x in b for x in _WATER_EXCLUDE_NAME):
        pass
    elif any(
        k in b
        for k in (
            "waterplane", "sm_waterplane", "/rivertool/", "mi_water",
            "oceanbackdrop", "oceanlod", "oceantile", "dunelagoon",
            "minorswamp", "shallowlake", "driedriver",
        )
    ):
        return FAMILY_WATER
    if any(k in b for k in ("sanddune", "sandpile", "sand_pile", "/dunes/", "mi_sand")):
        return FAMILY_SAND
    if any(
        k in b
        for k in (
            "/vegetation/", "/foliage/", "houseweeds", "statelessleaves",
            "mi_leaf", "mi_leaves", "sm_tree", "sm_bush", "sm_grass",
        )
    ):
        return FAMILY_FOLIAGE
    if any(
        k in b
        for k in (
            "decalmesh",
            "/textures/decals/",
            "bp_decal",
            "sm_decal_",
            "brandingdecals",
            "brandingposter",
            "graphicatlas",
            "/props/branding/",
            "mi_decal_",
            "mi_crack",
            "deferreddecal",
            "defereddecal",
            "cracktarmac",
            "crackconcrete",
            "crackplaster",
        )
    ):
        return FAMILY_DECAL
    if "tarp" in b or "awning" in b:
        return FAMILY_TARP
    # Road/asphalt surfaces — exclude crack-* decals already handled above
    if ("tarmac" in b or "asphalt" in b) and "decal" not in b and "crack" not in b:
        return FAMILY_ROAD
    if "glass" in b or "windowpane" in b:
        return FAMILY_GLASS
    return ""



def _list_mi_jsons_in_folders(folders: list[str]) -> list[tuple[str, str]]:
    """[(stem, abs_path), ...] for MI_/M_ JSON beside remapped mesh folders."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        try:
            for fname in os.listdir(folder):
                low = fname.lower()
                if not low.endswith(".json"):
                    continue
                if not (low.startswith("mi_") or low.startswith("m_")):
                    continue
                stem = os.path.splitext(fname)[0]
                path = os.path.join(folder, fname)
                key = os.path.normcase(os.path.normpath(path))
                if key in seen:
                    continue
                seen.add(key)
                out.append((stem, path))
        except OSError:
            continue
    return out



def _mesh_search_folders(psk_path: str = "", asset_folder: str = "") -> list[str]:
    folders: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        if not path:
            return
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            return
        if os.path.isdir(path):
            seen.add(key)
            folders.append(os.path.normpath(path))

    for seed in (asset_folder, os.path.dirname(psk_path or "")):
        _add(seed)
        if seed:
            for alt in utils.remap_path_into_content_dirs(os.path.join(seed, "__probe__")):
                _add(os.path.dirname(alt))
    if psk_path:
        for alt in utils.remap_path_into_content_dirs(psk_path):
            _add(os.path.dirname(alt))
    return folders



def _score_mi_candidate(
    mi_stem: str,
    mi_path: str,
    *,
    hint_tokens: set[str],
    family_hint: str,
    preferred_stem: str = "",
    allow_enemy_character_decals: bool = False,
) -> float:
    stem_l = (mi_stem or "").lower()
    if (
        not allow_enemy_character_decals
        and _is_character_enemy_outfit_decal_asset(mi_stem, mi_path)
    ):
        return -1000.0

    score = 0.0
    pref = (preferred_stem or "").lower()
    if pref and stem_l == pref:
        score += 40.0
    elif pref and pref in stem_l:
        score += 20.0

    mi = {}
    try:
        mi = _parse_flat_mi_json(mi_path) if mi_path else {}
    except Exception:
        mi = {}
    family = classify_mi_family(mi, stem_l)
    if family_hint and family == family_hint:
        score += 18.0
    elif family_hint == FAMILY_WATER and _is_water_mi(mi, stem_l):
        score += 18.0

    # Env meshes must not soft-match into character/enemy Decal family MIs
    if not allow_enemy_character_decals and family == FAMILY_DECAL and family_hint != FAMILY_DECAL:
        if _is_enemy_decal_mi(mi) or "enemy" in stem_l:
            return -1000.0
        # Gate/sticker Decals in the same folder are OK only with a decal hint
        if "decal" in stem_l and family_hint != FAMILY_DECAL:
            score -= 12.0

    tokens = _stem_tokens(mi_stem)
    if hint_tokens and tokens:
        overlap = hint_tokens & tokens
        score += 4.0 * len(overlap)
        ratio = difflib.SequenceMatcher(
            None, "".join(sorted(hint_tokens)), "".join(sorted(tokens)),
        ).ratio()
        score += 6.0 * ratio

    # Soft boosts for waterplane ↔ MI_Water_*
    if family_hint == FAMILY_WATER:
        if stem_l.startswith("mi_water") or stem_l.startswith("m_water"):
            score += 8.0
        if "ocean" in stem_l and ("backdrop" in stem_l or "lod" in stem_l):
            score += 8.0
    return score



def infer_map_mi_candidates(
    obj,
    psk_path: str = "",
    *,
    asset_path: str = "",
    limit: int = 5,
) -> list[tuple[str, str, float, str]]:
    """Fuzzy MI JSON candidates for a map mesh.

    Returns ``[(mi_stem, mi_path, score, reason), ...]`` best-first.
    Never ranks character/enemy/outfit Decal atlases for ordinary env meshes;
    Decal-family fuzzy is reserved for DecalMesh / sticker assets.
    """
    blob = _object_name_blob(obj, psk_path, asset_path)
    family_hint = _family_hint_from_name_blob(blob)
    preferred = ""
    if obj is not None:
        preferred = str(obj.get("arc_preferred_mi") or "").strip()
    if not preferred:
        # Actor / object name ONLY — never asset paths (false SM_Decal_* / water hits).
        actor_blob = " ".join(
            p for p in (
                str(obj.get("arc_actor_name") or "") if obj is not None else "",
                getattr(obj, "name", "") or "" if obj is not None else "",
            ) if p
        )
        preferred = preferred_mi_hint_from_actor_name(
            actor_blob, os.path.dirname(psk_path or ""),
        )

    # DecalMesh / sticker cards may fuzzy toward MI_*Decal*; walls/POI must not.
    allow_char_enemy_decals = False
    blob_path = blob.replace("\\", "/")
    allow_decal_family = bool(
        family_hint == FAMILY_DECAL
        or "decalmesh" in blob_path
        or "/textures/decals/" in blob_path
        or "bp_decal" in blob_path
        or "brandingposter" in blob_path
        or "graphicatlas" in blob_path
        or "/props/branding/" in blob_path
        or (obj is not None and obj.get("arc_decal_mesh"))
        or (obj is not None and obj.get("arc_poster_mesh"))
    )

    hint_tokens = _stem_tokens(blob) | _stem_tokens(os.path.basename(psk_path or ""))
    if preferred:
        hint_tokens |= _stem_tokens(preferred)
    if family_hint == FAMILY_WATER:
        hint_tokens |= {"water", "plane", "river", "lagoon", "swamp", "ocean"}
    if family_hint == FAMILY_DECAL:
        hint_tokens |= {"decal", "crack", "tarmac", "mask"}

    candidates: list[tuple[str, str, float, str]] = []
    seen: set[str] = set()

    def _add(stem: str, path: str, score: float, reason: str) -> None:
        if not stem or not path or not os.path.isfile(path):
            return
        if score < 0:
            return
        if (
            not allow_decal_family
            and _is_character_enemy_outfit_decal_asset(stem, path)
        ):
            return
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            return
        seen.add(key)
        candidates.append((stem, path, score, reason))

    # 1) Authoritative SM/SK StaticMaterials (with MapPlacements → Content remap)
    for slot_name, mi_stem, mi_path in _parse_sk_material_slots(psk_path, context=CTX_MAP) if psk_path else []:
        if not mi_stem or not mi_path:
            continue
        score = 50.0
        if preferred and mi_stem.lower() == preferred.lower():
            score += 25.0
            reason = f"sm_json+preferred:{slot_name or mi_stem}"
        else:
            reason = f"sm_json:{slot_name or mi_stem}"
        # When BP override prefers another water MI, boost that file if present
        if preferred and preferred.lower() != mi_stem.lower():
            pref_path = _resolve_mi_json_path(
                preferred, "", os.path.dirname(mi_path) or os.path.dirname(psk_path or ""),
            )
            if pref_path:
                _add(
                    preferred,
                    pref_path,
                    70.0,
                    f"bp_override:{preferred}",
                )
        _add(mi_stem, mi_path, score, reason)

    # 2) Adjacent / remapped folder MI_* fuzzy — HARD DISABLED.
    # Soft-matching Vent/PropTrim/Rebar/etc. by name similarity caused wrong
    # materials on multi-slot props. Use SM JSON only (+ water/decal preferred).
    if ENABLE_FUZZY_MI_INFER:
        mesh_stem_tokens = (
            _stem_tokens(os.path.basename(psk_path or ""))
            | _stem_tokens(os.path.basename((asset_path or "").replace("\\", "/")))
        )
        mesh_stem_tokens -= {
            "src", "static", "mesh", "actor", "staticmeshactor", "uaid", "spline",
        }
        folders = _mesh_search_folders(psk_path, os.path.dirname(psk_path or ""))
        for stem, path in _list_mi_jsons_in_folders(folders):
            score = _score_mi_candidate(
                stem, path,
                hint_tokens=hint_tokens,
                family_hint=family_hint,
                preferred_stem=preferred,
                allow_enemy_character_decals=allow_char_enemy_decals,
            )
            if score < 10.0:
                continue
            if (
                not allow_decal_family
                and "decal" in stem.lower()
                and family_hint != FAMILY_DECAL
            ):
                continue
            mi_tokens = _stem_tokens(stem)
            mesh_overlap = mesh_stem_tokens & mi_tokens
            if not mesh_overlap and not (
                preferred and preferred.lower() in stem.lower()
            ) and family_hint not in (FAMILY_WATER, FAMILY_DECAL, FAMILY_SAND, FAMILY_FOLIAGE):
                if score < 25.0:
                    continue
            if _is_prop_trim_atlas_mi_stem(stem):
                evidence = mesh_overlap | (hint_tokens & mi_tokens)
                trim_keys = {
                    "proptrim", "trim", "vent", "ventilation", "ventwall", "metal",
                    "painted", "worn", "rust",
                }
                if not (evidence & trim_keys) and not (
                    preferred and preferred.lower() == stem.lower()
                ):
                    continue
            if _is_rebar_mi_stem(stem):
                evidence = mesh_overlap | (hint_tokens & mi_tokens)
                rebar_keys = {"rebar", "rebars"}
                if not (evidence & rebar_keys) and not (
                    preferred and preferred.lower() == stem.lower()
                ):
                    continue
            _add(stem, path, score, f"fuzzy_folder:{os.path.basename(path)}")

    # 3) Preferred stem via global resolve (water / map-decal only when restricted)
    if preferred and _preferred_mi_is_single_slot_override(preferred):
        pref_path = _resolve_mi_json_path(
            preferred, "", os.path.dirname(psk_path or ""),
        )
        if pref_path:
            _add(preferred, pref_path, 65.0, f"preferred_resolve:{preferred}")

    candidates.sort(key=lambda t: (-t[2], t[0].lower()))
    return candidates[: max(1, int(limit or 5))]



def _fuzzy_fill_white_slots_only(obj, psk_path: str = "", asset_path: str = "") -> int:
    """Fill empty/white slots from SM JSON StaticMaterials (exact).

    When ``ENABLE_FUZZY_MI_INFER`` is False (default), never invent an MI from
    folder name similarity. Water/decal preferred_mi may still fill slot 0 when
    restricted by ``_preferred_mi_is_single_slot_override``.
    """
    from .dispatch import _mi_stem_from_blender_name
    if not obj or obj.type != "MESH":
        return 0
    fixed = 0
    slots = _parse_sk_material_slots(psk_path, context=CTX_MAP) if psk_path else []
    if slots:
        _ensure_mesh_material_slot_count(obj, len(slots))
        used = set()
        has_mi_named = any(
            _mi_stem_from_blender_name(s.material.name)
            for s in (obj.material_slots or [])
            if s.material
        )
        for idx, (slot_name, mi_stem, mi_path) in enumerate(slots):
            if not mi_stem or not mi_path:
                continue
            if not path_allowed_for_context(mi_path, CTX_MAP):
                continue
            target_slot, slot_i = _match_material_slot(
                obj, slot_name, idx, used, mi_stem,
                allow_index_fallback=not has_mi_named,
            )
            if target_slot is None:
                if has_mi_named:
                    continue
                if idx < len(obj.material_slots) and idx not in used:
                    slot_i = idx
                else:
                    continue
            need, _why = material_slot_needs_repair(
                obj.material_slots[slot_i].material
                if slot_i < len(obj.material_slots) else None
            )
            if not need:
                used.add(slot_i)
                continue
            if _assign_shared_mi_to_object(
                obj, mi_stem, mi_path, psk_path,
                slot_index=slot_i, slot_name=slot_name or mi_stem,
            ):
                fixed += 1
                used.add(slot_i)
        return fixed

    # No SM slots — exact preferred water/decal only (never folder fuzzy).
    preferred = ""
    if obj is not None:
        preferred = str(obj.get("arc_preferred_mi") or "").strip()
    if not preferred:
        preferred = preferred_mi_hint_from_actor_name(
            " ".join(
                p for p in (
                    str(obj.get("arc_actor_name") or ""),
                    getattr(obj, "name", "") or "",
                ) if p
            ),
            os.path.dirname(psk_path or ""),
        )
    if preferred and _preferred_mi_is_single_slot_override(preferred):
        pref_path = _resolve_mi_json_path(
            preferred, "", os.path.dirname(psk_path or ""),
        )
        if pref_path:
            for idx, slot in enumerate(obj.material_slots or []):
                need, _why = material_slot_needs_repair(slot.material)
                if not need:
                    continue
                return 1 if _assign_shared_mi_to_object(
                    obj, preferred, pref_path, psk_path,
                    slot_index=idx, slot_name=preferred,
                ) else 0
            if not obj.material_slots:
                return 1 if _assign_shared_mi_to_object(
                    obj, preferred, pref_path, psk_path, slot_name=preferred,
                ) else 0

    if not ENABLE_FUZZY_MI_INFER:
        return 0

    # Legacy fuzzy path (disabled by default)
    cands = infer_map_mi_candidates(obj, psk_path, asset_path=asset_path, limit=4)
    if not cands:
        return 0
    mi_stem, mi_path, _score, _reason = cands[0]
    for idx, slot in enumerate(obj.material_slots or []):
        need, _why = material_slot_needs_repair(slot.material)
        if not need:
            continue
        return 1 if _assign_shared_mi_to_object(
            obj, mi_stem, mi_path, psk_path, slot_index=idx, slot_name=mi_stem,
        ) else 0
    if not obj.material_slots:
        return 1 if _assign_shared_mi_to_object(
            obj, mi_stem, mi_path, psk_path, slot_name=mi_stem,
        ) else 0
    return 0



def _assign_shared_mi_to_object(
    obj,
    mi_stem: str,
    mi_path: str,
    psk_path: str = "",
    *,
    slot_index: int = 0,
    slot_name: str = "",
) -> bool:
    from .dispatch import _get_or_build_shared_mi_material
    if not obj or obj.type != "MESH" or not mi_path:
        return False
    _ensure_mesh_material_slot_count(obj, max(slot_index + 1, 1))
    mat = _get_or_build_shared_mi_material(
        mi_stem, mi_path, psk_path, (slot_name or mi_stem).lower(),
    )
    if mat is None:
        return False
    try:
        obj.material_slots[slot_index].material = mat
    except Exception:
        try:
            obj.data.materials[slot_index] = mat
        except Exception:
            return False
    try:
        obj["arc_materials_pending"] = 0
        obj["arc_mi_inferred"] = mi_stem
    except Exception:
        pass
    return True



def clear_leaked_preferred_mi(obj) -> bool:
    """Strip ``arc_preferred_mi`` unless it is a water/decal single-slot override.

    Opaque StaticMeshActors previously kept PropTrim/Vent preferred stamps that
    wiped every SM JSON slot with one atlas cell.
    """
    if obj is None:
        return False
    preferred = str(obj.get("arc_preferred_mi") or "").strip()
    if not preferred:
        return False
    if _preferred_mi_is_single_slot_override(preferred):
        return False
    try:
        if obj.get("arc_preferred_mi"):
            del obj["arc_preferred_mi"]
    except Exception:
        try:
            obj["arc_preferred_mi"] = ""
        except Exception:
            return False
    return True



def invalidate_shared_mi_on_object(obj) -> int:
    """Drop shared-cache entries + setup stamps so Stage 2 rebuilds graphs."""
    if not obj or obj.type != "MESH":
        return 0
    n = 0
    for slot in obj.material_slots or []:
        mat = slot.material
        if mat is None:
            continue
        key = str(mat.get("arc_mi_path") or "").strip()
        if key:
            _SHARED_MI_MATERIALS.pop(_norm_path_key(key), None)
        for stamp in (
            "arc_proptrim_uv", "arc_trim_setup", "arc_trim_world_uv",
            "arc_trim_alpha", "arc_water_setup", "arc_decal_mask_setup",
        ):
            try:
                if mat.get(stamp) is not None:
                    del mat[stamp]
            except Exception:
                pass
        n += 1
    return n



def clear_leaked_map_cosmetics(obj) -> int:
    """Force All / Fix White: wipe Characters/Heroes TEX + MI stamps on map meshes."""
    return clear_out_of_context_map_materials(obj)



def repair_sma_trim_materials(obj, psk_path: str = "") -> dict:
    """Force-rebuild all slots from SM JSON; clear leaked preferred stamps.

    Used by the Repair SMA / Trim Materials operator and Force All Stage 2.
    """
    from .dispatch import fix_object_materials_from_mi_slots
    result = {
        "ok": False,
        "reason": "",
        "fixed": 0,
        "cleared_preferred": False,
    }
    if not obj or obj.type != "MESH":
        result["reason"] = "not_mesh"
        return result

    result["cleared_preferred"] = clear_leaked_preferred_mi(obj)
    invalidate_shared_mi_on_object(obj)
    wiped = clear_out_of_context_map_materials(obj)

    if not psk_path:
        for key in ("arc_psk_path", "arc_mesh_file"):
            raw = obj.get(key) or ""
            if raw:
                psk_path = bpy.path.abspath(str(raw))
                break

    try:
        obj["arc_force_material_rebuild"] = 1
        obj["arc_materials_pending"] = 1
    except Exception:
        pass

    fixed = 0
    if psk_path:
        fixed = _setup_map_material_from_slots(obj, psk_path)
        if not fixed:
            fixed = setup_map_material(obj, psk_path)
    else:
        folder = ""
        for key in ("arc_asset_path",):
            raw = obj.get(key) or ""
            if raw:
                folder = os.path.dirname(bpy.path.abspath(str(raw)))
                break
        fixed = fix_object_materials_from_mi_slots(obj, folder)

    try:
        if obj.get("arc_force_material_rebuild"):
            del obj["arc_force_material_rebuild"]
        obj["arc_materials_pending"] = 0
    except Exception:
        pass

    result["fixed"] = int(fixed or 0)
    result["ok"] = bool(fixed) or result["cleared_preferred"] or bool(wiped)
    result["reason"] = (
        f"rebuilt:{fixed}"
        if fixed
        else (f"wiped_cosmetics:{wiped}" if wiped else "no_slots_resolved")
    )
    return result



def fix_white_unassigned_materials(obj, psk_path: str = "") -> dict:
    """Repair empty / default-white map materials via SM JSON (exact slots).

    Returns a result dict::
      {ok, reason, matched, mi_stem, mi_path, why_white}

    Soft-match folder MI inference is disabled (``ENABLE_FUZZY_MI_INFER``).
    """
    result = {
        "ok": False,
        "reason": "",
        "matched": "",
        "mi_stem": "",
        "mi_path": "",
        "why_white": "",
    }
    if not obj or obj.type != "MESH":
        result["reason"] = "not_mesh"
        return result

    need, why = object_needs_material_repair(obj)
    result["why_white"] = why
    if not need:
        result["ok"] = True
        result["reason"] = "already_ok"
        return result

    # Always strip cosmetic TEX/MI before rebuild — corrupt SM JSON may leave
    # no replacement, but the belt/helmet images must not remain visible.
    clear_out_of_context_map_materials(obj)

    if not psk_path:
        for key in ("arc_psk_path", "arc_mesh_file"):
            raw = obj.get(key) or ""
            if raw:
                psk_path = bpy.path.abspath(str(raw))
                break
    asset_path = str(obj.get("arc_asset_path") or "")

    # Fast path: SM/SK slots + MI-named slots (no fuzzy recurse)
    if psk_path:
        fixed = _setup_map_material_from_slots(obj, psk_path)
        if fixed:
            need2, why2 = object_needs_material_repair(obj)
            if not need2:
                result["ok"] = True
                result["reason"] = f"setup_map:{fixed}"
                result["why_white"] = why2
                for slot in obj.material_slots:
                    mat = slot.material
                    if mat is not None and mat.get("arc_mi_path"):
                        result["mi_stem"] = str(mat.get("arc_mi_stem") or mat.name or "")
                        result["mi_path"] = str(mat.get("arc_mi_path") or "")
                        result["matched"] = "sm_json"
                        break
                return result
            # Partial SM assign — fill remaining white slots from SM JSON only.
            filled = _fuzzy_fill_white_slots_only(obj, psk_path, asset_path)
            need3, why3 = object_needs_material_repair(obj)
            result["ok"] = bool(fixed or filled)
            result["reason"] = f"setup_map_partial:{fixed}+fill:{filled}"
            result["why_white"] = why3
            result["matched"] = "sm_json_partial"
            return result

    # No SM success — SM-slot fill / water-decal preferred only (no folder fuzzy)
    filled = _fuzzy_fill_white_slots_only(obj, psk_path, asset_path)
    if filled:
        cands = infer_map_mi_candidates(obj, psk_path, asset_path=asset_path, limit=1)
        if cands:
            result["matched"] = cands[0][3]
            result["mi_stem"] = cands[0][0]
            result["mi_path"] = cands[0][1]
        result["ok"] = True
        result["reason"] = f"sm_fill:{filled}"
        return result

    result["reason"] = "no_sm_json" if not psk_path else "sm_unresolved"
    return result



def _setup_map_material_from_slots(obj, psk_path: str) -> int:
    """Assign from MI-named slots + SM/SK JSON + preferred BP MI (no fuzzy)."""
    from .dispatch import _get_or_build_shared_mi_material, _mi_stem_from_blender_name, fix_object_materials_from_mi_slots
    if not obj or obj.type != "MESH":
        return 0

    # Drop leaked Characters/Heroes Image Textures before any reuse/resolve.
    clear_out_of_context_map_materials(obj)

    folder = os.path.dirname(psk_path) if psk_path else ""
    # Prefer remapped Content folder when MapPlacements only has .uemodel.
    for alt in utils.remap_path_into_content_dirs(
        os.path.join(folder, "__probe__") if folder else ""
    ):
        alt_dir = os.path.dirname(alt)
        if alt_dir and os.path.isdir(alt_dir):
            folder = alt_dir
            break

    # UEModel often embeds the real MI_* names while SM StaticMaterials point at
    # shared PropTrim / wrong refs. Resolve MI-named Blender slots first (with
    # MapPlacements → Content remap), then fill leftovers from SM JSON.
    # Context=map: never Characters/Heroes via FMDex basename.
    fixed_named = fix_object_materials_from_mi_slots(obj, folder, context=CTX_MAP)
    slots = _parse_sk_material_slots(psk_path, context=CTX_MAP) if psk_path else []
    fixed = fixed_named
    force_rebuild = bool(obj.get("arc_force_material_rebuild"))

    # UEModel MI_* slot names are authoritative when present — do not
    # index-clobber them with unrelated SM StaticMaterials (belt/helmet from
    # corrupt dumps, PropTrim atlas, StreetSign refs on Aircon, etc.).
    has_mi_named = any(
        _mi_stem_from_blender_name(s.material.name)
        for s in (obj.material_slots or [])
        if s.material
    )

    preferred = str(obj.get("arc_preferred_mi") or "").strip()
    # Do not invent preferred_mi from actor name when SM/UEModel slots already
    # define materials — actor heuristics caused Vent/PropTrim leaks.
    if not preferred and not slots and not has_mi_named:
        preferred = preferred_mi_hint_from_actor_name(
            " ".join(
                p for p in (
                    str(obj.get("arc_actor_name") or ""),
                    getattr(obj, "name", "") or "",
                ) if p
            ),
            folder,
        )
        if preferred:
            try:
                obj["arc_preferred_mi"] = preferred
            except Exception:
                pass
    # Drop leaked preferred stems that are not water/decal single-slot overrides
    # (e.g. PropTrim/Vent atlas stamped onto every StaticMeshActor SRC).
    if preferred and not _preferred_mi_is_single_slot_override(preferred):
        try:
            if obj.get("arc_preferred_mi"):
                del obj["arc_preferred_mi"]
        except Exception:
            try:
                obj["arc_preferred_mi"] = ""
            except Exception:
                pass
        preferred = ""

    # Water / map-decal BP override: single MI on slot 0. Never use preferred to
    # wipe multi-slot StaticMeshActor props (that was assigning Vent/PropTrim atlases
    # onto every SMA when a leaked preferred stem resolved).
    if preferred and _preferred_mi_is_single_slot_override(preferred):
        real_slots = [(sn, st, mp) for sn, st, mp in slots if st and mp]
        pref_folder = ""
        for _sn, _st, mp in real_slots:
            pref_folder = os.path.dirname(mp)
            break
        pref_path = _resolve_mi_json_path(
            preferred, "", pref_folder or folder, context=CTX_MAP,
        )
        if not pref_path:
            for alt in decal_mi_stem_candidates_from_actor(
                str(obj.get("arc_actor_name") or obj.name or "")
            ):
                if alt.lower() == preferred.lower():
                    continue
                pref_path = _resolve_mi_json_path(
                    alt, "", pref_folder or folder, context=CTX_MAP,
                )
                if pref_path:
                    preferred = alt
                    break
        if pref_path:
            _ensure_mesh_material_slot_count(obj, max(1, len(real_slots) or 1))
            mat = _get_or_build_shared_mi_material(
                preferred, pref_path, psk_path, preferred.lower(),
            )
            if mat is not None:
                try:
                    obj.material_slots[0].material = mat
                    # DecalMesh / water cards are single-slot — done.
                    if len(real_slots) <= 1:
                        return 1
                    fixed = 1
                except Exception:
                    pass

    if slots:
        _ensure_mesh_material_slot_count(obj, len(slots))
        used_indices = set()
        # Keep BP water/decal preferred on slot 0 when it already won above.
        if fixed and preferred and _preferred_mi_is_single_slot_override(preferred):
            used_indices.add(0)
        allow_idx = not has_mi_named
        for idx, (slot_name, mi_stem, mi_path) in enumerate(slots):
            if not mi_stem or not mi_path:
                continue
            if not path_allowed_for_context(mi_path, CTX_MAP):
                continue
            if idx in used_indices:
                continue
            target_slot, slot_i = _match_material_slot(
                obj, slot_name, idx, used_indices, mi_stem,
                allow_index_fallback=allow_idx,
            )
            if target_slot is None:
                if has_mi_named:
                    continue
                if allow_idx and idx < len(obj.material_slots) and idx not in used_indices:
                    target_slot = obj.material_slots[idx]
                    slot_i = idx
                else:
                    continue
            used_indices.add(slot_i)
            cur = target_slot.material
            if (
                not force_rebuild
                and cur is not None
                and str(cur.get("arc_mi_path", "") or "") == _norm_path_key(mi_path)
            ):
                # Rebuild if this datablock was a stale enemy-decal misclass
                # or pre-v2 branding mask wiring (Color→Alpha).
                fam = str(cur.get("arc_mi_family") or "")
                if _needs_map_decal_mask_rebuild(cur, mi_path):
                    _SHARED_MI_MATERIALS.pop(_norm_path_key(mi_path), None)
                    try:
                        cur.name = f"{cur.name}_stale_decal_mask"
                    except Exception:
                        pass
                    # fall through to rebuild
                elif fam == FAMILY_DECAL:
                    try:
                        mi_probe = _parse_flat_mi_json(mi_path)
                        expected = classify_mi_family(
                            mi_probe, mi_stem.lower(), (slot_name or mi_stem).lower(),
                        )
                    except Exception:
                        expected = fam
                    if expected == FAMILY_DECAL:
                        fixed += 1
                        continue
                    # fall through to rebuild
                elif _needs_trim_setup_rebuild(cur, mi_path):
                    _SHARED_MI_MATERIALS.pop(_norm_path_key(mi_path), None)
                    try:
                        cur.name = f"{cur.name}_stale_trim"
                    except Exception:
                        pass
                    # fall through to rebuild
                elif _needs_env_props_rebuild(cur, mi_path):
                    _SHARED_MI_MATERIALS.pop(_norm_path_key(mi_path), None)
                    try:
                        cur.name = f"{cur.name}_stale_env_props"
                    except Exception:
                        pass
                    # fall through to rebuild
                elif _needs_proptrim_or_glass_rebuild(cur, mi_path, mi_stem):
                    _SHARED_MI_MATERIALS.pop(_norm_path_key(mi_path), None)
                    try:
                        cur.name = f"{cur.name}_stale_proptrim"
                    except Exception:
                        pass
                    # fall through to rebuild
                elif _needs_water_setup_rebuild(cur, mi_path):
                    _SHARED_MI_MATERIALS.pop(_norm_path_key(mi_path), None)
                    try:
                        cur.name = f"{cur.name}_stale_water"
                    except Exception:
                        pass
                    # fall through to rebuild
                else:
                    fixed += 1
                    continue
            elif force_rebuild and mi_path:
                _SHARED_MI_MATERIALS.pop(_norm_path_key(mi_path), None)
                if cur is not None:
                    try:
                        base_n = re.sub(r"(_force_rebuild)+$", "", cur.name or "")
                        cur.name = f"{base_n}_force_rebuild"
                    except Exception:
                        pass
            mat = _get_or_build_shared_mi_material(
                mi_stem, mi_path, psk_path, (slot_name or mi_stem).lower(),
            )
            if mat is None:
                continue
            target_slot.material = mat
            fixed += 1
        # Barrier_02 SM order is Baked then C1 — select the TrimMap slot so the
        # shader editor matches Barrier_01's full graph instead of the sparse bake.
        _prefer_trimmap_active_slot(obj)
        if fixed:
            return fixed

    return fixed if fixed else fix_object_materials_from_mi_slots(
        obj, folder, context=CTX_MAP,
    )



def _prefer_trimmap_active_slot(obj) -> None:
    """Select the TrimMap / C1 slot when present (Barrier_02 defaults to Baked)."""
    if obj is None or not getattr(obj, "material_slots", None):
        return
    for i, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat is None:
            continue
        fam = str(mat.get("arc_mi_family") or "")
        stem = str(mat.get("arc_mi_stem") or mat.name or "").lower()
        if fam == FAMILY_TRIMMAP or "trimmap" in stem or stem.endswith("_c1") or "_c1" in stem:
            try:
                obj.active_material_index = i
            except Exception:
                pass
            return


def setup_map_material(obj, psk_path: str) -> int:
    """Fast map-prop materials: SK/SM slots + shared MI cache (no outfit path).

    Returns the number of slots assigned. Prefer mesh JSON slot lists (index-
    aligned); fall back to MI-named Blender slots. Creates missing material
    slots when UEModel imports left the mesh with none (water planes etc.).
    Soft-match folder MI inference is disabled.
    """
    if not obj or obj.type != "MESH":
        return 0

    # Ensure Use UV1 posters bind UV1 (PSK EXTRAUV0 → UV1) before Stage 2 setup.
    try:
        utils.normalize_object_ue_uv_layers(obj)
    except Exception:
        pass

    fixed = _setup_map_material_from_slots(obj, psk_path)
    if fixed:
        # Rebuild any remaining white slots from SM JSON only
        need, _why = object_needs_material_repair(obj)
        if need:
            fixed += _fuzzy_fill_white_slots_only(
                obj, psk_path, str(obj.get("arc_asset_path") or ""),
            )
        _ensure_plane_world_tiling(obj)
        _prefer_trimmap_active_slot(obj)
        return fixed

    # Last resort: SM-slot fill / water-decal preferred (no folder fuzzy)
    need, _why = object_needs_material_repair(obj)
    if not need:
        _ensure_plane_world_tiling(obj)
        _prefer_trimmap_active_slot(obj)
        return 0
    asset_path = str(obj.get("arc_asset_path") or "")
    fixed = _fuzzy_fill_white_slots_only(obj, psk_path, asset_path)
    _ensure_plane_world_tiling(obj)
    _prefer_trimmap_active_slot(obj)
    return fixed



def _mesh_type_stem_from_obj(obj, psk_path: str = "") -> str:
    """Unique mesh type key: SM_/mesh basename without SRC_/hash/LOD noise."""
    for raw in (
        psk_path,
        str(obj.get("arc_psk_path") or "") if obj else "",
        str(obj.get("arc_mesh_file") or "") if obj else "",
        str(obj.get("arc_asset_path") or "") if obj else "",
        getattr(obj, "name", "") if obj else "",
    ):
        if not raw:
            continue
        leaf = os.path.basename(str(raw).replace("\\", "/"))
        stem = os.path.splitext(leaf)[0]
        stem = re.sub(r"^SRC_", "", stem, flags=re.IGNORECASE)
        stem = re.sub(r"-[0-9A-Fa-f]{4,10}$", "", stem)
        stem = re.sub(r"_LOD\d+$", "", stem, flags=re.IGNORECASE)
        if stem:
            return stem
    return "unknown"



def mesh_references_glass(obj, psk_path: str = "") -> bool:
    """True when SM JSON or stamped materials include a glass / BrokenGlass slot."""
    if not obj or obj.type != "MESH":
        return False
    for slot in obj.material_slots or []:
        mat = slot.material
        if mat is None:
            continue
        fam = str(mat.get("arc_mi_family") or "")
        if fam == FAMILY_GLASS:
            return True
        name_l = (mat.name or "").lower()
        stem_l = str(mat.get("arc_mi_stem") or "").lower()
        if "brokenglass" in name_l or "brokenglass" in stem_l:
            return True
        if fam == FAMILY_GLASS or (
            "glass" in name_l
            and "visor" not in name_l
            and any(k in name_l for k in ("broken", "window", "pane", "sdf"))
        ):
            return True
    path = psk_path or ""
    if not path:
        for key in ("arc_psk_path", "arc_mesh_file"):
            raw = obj.get(key) or ""
            if raw:
                path = bpy.path.abspath(str(raw))
                break
    for slot_name, mi_stem, mi_path in _parse_sk_material_slots(path) if path else []:
        sl = (slot_name or "").lower()
        ms = (mi_stem or "").lower()
        if "brokenglass" in sl or "brokenglass" in ms:
            return True
        if "glass" in sl or (ms.startswith("m_") and "glass" in ms and "visor" not in ms):
            return True
        if not mi_path:
            continue
        try:
            mi = _parse_flat_mi_json(mi_path)
            if classify_mi_family(mi, ms, sl) == FAMILY_GLASS:
                return True
        except Exception:
            pass
    return False



def _analyze_broken_mesh_type(
    stem: str,
    representative,
    psk_path: str,
    why: str,
) -> dict:
    """JSON / library / similar-mesh notes for one unique broken mesh type."""
    slots = _parse_sk_material_slots(psk_path) if psk_path else []
    slot_rows = []
    glass_slots = []
    trim_slots = []
    for slot_name, mi_stem, mi_path in slots:
        row = {
            "slot": slot_name or "",
            "mi": mi_stem or "",
            "mi_json": mi_path or "",
            "mi_exists": bool(mi_path and os.path.isfile(mi_path)),
            "family": "",
            "tex_params": [],
            "missing_cr": False,
            "notes": "",
        }
        if mi_path and os.path.isfile(mi_path):
            try:
                mi = _parse_flat_mi_json(mi_path)
                fam = classify_mi_family(mi, (mi_stem or "").lower(), (slot_name or "").lower())
                row["family"] = fam
                row["tex_params"] = sorted(_mi_tex_params(mi))
                local = [os.path.dirname(mi_path)]
                missing_png = []
                found_png = []
                for param, obj in mi.get("textures") or []:
                    if not obj or str(obj).startswith("/Engine/"):
                        continue
                    png = _resolve_mi_texture_path(obj, local)
                    if png:
                        found_png.append(param)
                    else:
                        missing_png.append(f"{param}={obj}")
                row["expected_textures"] = "|".join(row["tex_params"])
                row["found_textures"] = "|".join(found_png) if found_png else "none"
                row["missing_pngs"] = "|".join(missing_png[:8])
                if not row["tex_params"] and fam != FAMILY_WATER:
                    row["tex_reason"] = "mi_no_texture_params"
                    row["suggested_fix"] = (
                        "Re-export MI JSON Textures / parent Material; "
                        "or rely on BrokenGlass/parent-ref enrich"
                    )
                elif missing_png and not found_png:
                    row["tex_reason"] = "all_pngs_missing"
                    row["suggested_fix"] = "Re-export referenced PNGs from FModel"
                elif missing_png:
                    row["tex_reason"] = "some_pngs_missing"
                    row["suggested_fix"] = "Re-export missing texture PNGs from FModel"
                elif row["tex_params"] and not found_png:
                    row["tex_reason"] = "textures_unresolved"
                    row["suggested_fix"] = "Check ObjectPath → Content PNG resolve"
                else:
                    row["tex_reason"] = ""
                    row["suggested_fix"] = ""
                if "proptrim" in (mi_stem or "").lower():
                    row["missing_cr"] = "CR Texture" not in row["tex_params"] and "CR" not in row["tex_params"]
                    sc = (mi.get("scalars") or {}) if mi_path else {}
                    uv_off = _mi_scalar(
                        sc, "UVOffset", "UV Offset", "OffsetUVs", "UV Offset Amount",
                        default=0.0,
                    )
                    if abs(float(uv_off)) < 1e-5:
                        row["notes"] = (
                            (row["notes"] + "; " if row["notes"] else "")
                            + "PropTrim has no UVOffset — mesh UVs select atlas cell; "
                              "vent-looking = wrong cell or wrong MI"
                        )
                    if not row["missing_cr"]:
                        row["notes"] = (
                            (row["notes"] + "; " if row["notes"] else "")
                            + "PropTrim CR/NXX resolved (library/sibling inherit ok)"
                        )
                    else:
                        lib = _proptrim_library_parent_stem(mi_stem or "")
                        row["notes"] = (
                            (row["notes"] + "; " if row["notes"] else "")
                            + f"PropTrim child lacks CR — expect inherit from {lib or 'library parent'}"
                        )
                        row["tex_reason"] = row.get("tex_reason") or "proptrim_missing_cr_after_inherit"
                if fam == FAMILY_ENVIRONMENT and _is_architecture_trim_stem(mi_stem or ""):
                    row["notes"] = (
                        (row["notes"] + "; " if row["notes"] else "")
                        + "architecture trim (not map-decal)"
                    )
                if fam == FAMILY_GLASS:
                    glass_slots.append(mi_stem or slot_name)
                    row["notes"] = (row["notes"] + "; " if row["notes"] else "") + "glass family"
                    if not found_png:
                        row["tex_reason"] = row.get("tex_reason") or "glass_textures_missing"
                if "proptrim" in (mi_stem or "").lower() or "proptrim" in (slot_name or "").lower():
                    trim_slots.append(mi_stem or slot_name)
                if fam == FAMILY_DECAL and _is_architecture_trim_stem(mi_stem or ""):
                    row["notes"] = (
                        (row["notes"] + "; " if row["notes"] else "")
                        + "BUG: architecture trim still classified as map-decal"
                    )
            except Exception as exc:
                row["notes"] = f"parse_error:{exc}"
        elif mi_stem:
            alt = _find_mi_json_by_stem_identity(mi_stem, context=CTX_MAP)
            if alt:
                row["notes"] = f"MI JSON path unresolved (but identity-good copy at {alt})"
                row["tex_reason"] = "mi_json_unresolved_has_alt"
                row["suggested_fix"] = "Stage 2 identity fallback should pick this path — re-run Force"
                row["expected_textures"] = f"{mi_stem}.json"
                row["found_textures"] = alt
            else:
                row["notes"] = (
                    "MI JSON unresolved — corrupt FModel dump (wrong Name/Package body) "
                    "or file absent; re-export from FModel"
                )
                row["tex_reason"] = "mi_json_corrupt_or_missing"
                row["suggested_fix"] = (
                    "Re-export MI from FModel (MaterialLibrary body Name/Package mismatch is common)"
                )
                row["expected_textures"] = f"{mi_stem}.json"
                row["found_textures"] = "missing"
        else:
            row["notes"] = "empty/engine slot"
            row["tex_reason"] = "empty_or_worldgrid_slot"
            row["suggested_fix"] = "SM slot has no Material — DecalMesh/WorldGrid or empty export"
            row["expected_textures"] = "real MI"
            row["found_textures"] = "empty/WorldGrid"
        slot_rows.append(row)

    # Exact library MI match for first missing stem
    library_hits = []
    for row in slot_rows:
        mi = row["mi"]
        if not mi or row["mi_exists"]:
            continue
        found = _resolve_mi_json_path(mi, "", os.path.dirname(psk_path or ""))
        if found:
            library_hits.append(found)
            row["mi_json"] = found
            row["mi_exists"] = True
            row["notes"] = (row["notes"] + "; " if row["notes"] else "") + "found via MaterialLibrary resolve"

    # Similar mesh template: sibling stem that Stage 2 would set up from SM JSON
    similar = []
    folder = os.path.dirname(psk_path or "")
    if folder and os.path.isdir(folder):
        base = re.sub(r"(?i)_x\d+y\d+.*$", "", stem)
        base = re.sub(r"(?i)_win.*$", "", base)
        try:
            for fname in os.listdir(folder):
                if not fname.lower().endswith((".json",)):
                    continue
                if not fname.upper().startswith(("SM_", "SK_")):
                    continue
                other = os.path.splitext(fname)[0]
                if other == stem:
                    continue
                if base and base.lower() in other.lower():
                    similar.append(other)
                if len(similar) >= 5:
                    break
        except OSError:
            pass

    sig = "|".join(f"{r['slot']}:{r['mi']}" for r in slot_rows) or "(no_sm_slots)"
    return {
        "stem": stem,
        "why": why,
        "representative": getattr(representative, "name", "") if representative else "",
        "psk_path": psk_path or "",
        "slot_signature": sig,
        "slots": slot_rows,
        "glass_slots": glass_slots,
        "trim_slots": trim_slots,
        "library_hits": library_hits,
        "similar_meshes": similar,
        "recommendation": _audit_recommendation(slot_rows, glass_slots, trim_slots, why),
    }



def _audit_recommendation(slot_rows, glass_slots, trim_slots, why: str) -> str:
    if glass_slots and trim_slots:
        return (
            "Multi-slot glass+PropTrim: keep BrokenGlass on glass family and "
            "PropTrim on metal family with inherited CR/NXX; do not share one material."
        )
    if trim_slots and any(r.get("missing_cr") for r in slot_rows):
        return (
            "PropTrim compact MI missing CR/NXX — deterministic library/sibling inherit "
            "(MI_PropTrim_* / MI_Wrh_Beams_*_PropTrim_*)."
        )
    if any(not r.get("mi_exists") and r.get("mi") for r in slot_rows):
        return "Resolve missing MI JSON via ObjectPath / MaterialLibrary (exact stem)."
    if "worldgrid" in (why or "") or "empty" in (why or ""):
        return "SM StaticMaterials empty or WorldGrid — no fuzzy invent; fix export or preferred water/decal only."
    if "stamped_white" in (why or "") or "default_white" in (why or ""):
        return "Re-run Stage 2 after deterministic fixes; check texture PNG paths beside MI."
    return "Inspect SM JSON slots and MI texture ObjectPaths; no soft-match invent."



def audit_map_materials(
    context=None,
    map_name: str = "",
    *,
    only_selected: bool = False,
    write_report: bool = True,
) -> dict:
    """Scan map meshes for broken materials; group by unique mesh type.

    Writes markdown + CSV under addon ``docs/`` (and MapPlacement workspace when set).
    """
    try:
        from .. import map_placement as mp
    except Exception:
        mp = None

    ctx = context or bpy.context
    scene = getattr(ctx, "scene", None)
    map_name = (map_name or "").strip()
    if (not map_name or map_name == "NONE") and scene is not None:
        map_name = (getattr(scene, "arc_placement_map", "") or "").strip()
        if not map_name or map_name == "NONE":
            map_name = (getattr(scene, "arc_placement_map_name", "") or "").strip()

    if mp is not None:
        targets = mp.collect_map_mesh_targets(ctx, map_name, only_selected=only_selected)
    else:
        targets = [o for o in bpy.data.objects if o.type == "MESH"]

    groups: dict[str, dict] = {}
    ok_count = 0
    broken_count = 0
    for obj in targets:
        need, why = object_needs_material_repair(obj)
        psk = ""
        for key in ("arc_psk_path", "arc_mesh_file"):
            raw = obj.get(key) or ""
            if raw:
                psk = bpy.path.abspath(str(raw))
                break
        stem = _mesh_type_stem_from_obj(obj, psk)
        # Slot signature for grouping even when healthy (glass vs trim called out)
        slots = _parse_sk_material_slots(psk) if psk else []
        sig = "|".join(f"{sn}:{ms}" for sn, ms, _mp in slots) or "(no_sm)"
        key = f"{stem}::{sig}"
        if not need:
            ok_count += 1
            if key not in groups:
                groups[key] = {
                    "stem": stem,
                    "ok": True,
                    "why": "ok",
                    "count": 0,
                    "representative": obj.name,
                    "psk_path": psk,
                    "slot_signature": sig,
                    "analysis": None,
                }
            groups[key]["count"] += 1
            continue
        broken_count += 1
        if key not in groups or groups[key].get("ok"):
            analysis = _analyze_broken_mesh_type(stem, obj, psk, why)
            groups[key] = {
                "stem": stem,
                "ok": False,
                "why": why,
                "count": 0,
                "representative": obj.name,
                "psk_path": psk,
                "slot_signature": sig,
                "analysis": analysis,
            }
        groups[key]["count"] += 1

    broken_types = [g for g in groups.values() if not g.get("ok")]
    broken_types.sort(key=lambda g: (-int(g.get("count") or 0), g.get("stem") or ""))

    report = {
        "map": map_name or "",
        "scanned": len(targets),
        "ok_meshes": ok_count,
        "broken_meshes": broken_count,
        "unique_types": len(groups),
        "unique_broken_types": len(broken_types),
        "broken": broken_types,
        "fuzzy_enabled": bool(ENABLE_FUZZY_MI_INFER),
        "paths": [],
    }

    if write_report:
        report["paths"] = _write_material_audit_report(report, scene)

    return report



def _write_material_audit_report(report: dict, scene=None) -> list[str]:
    """Write markdown + CSV; return written paths."""
    written: list[str] = []
    addon_dir = os.path.dirname(os.path.abspath(__file__))
    docs_dir = os.path.join(addon_dir, "docs")
    try:
        os.makedirs(docs_dir, exist_ok=True)
    except OSError:
        pass
    map_tag = re.sub(r"[^\w\-]+", "_", report.get("map") or "Map") or "Map"
    md_name = f"MATERIAL_AUDIT_{map_tag}.md"
    csv_name = f"MATERIAL_AUDIT_{map_tag}.csv"
    destinations = [docs_dir]
    if scene is not None:
        ws = getattr(scene, "arc_placement_workspace", "") or ""
        if ws:
            ws_abs = bpy.path.abspath(str(ws))
            if os.path.isdir(ws_abs):
                destinations.append(ws_abs)

    lines = [
        f"# Map Material Audit — {report.get('map') or '(any)'}",
        "",
        f"- Scanned meshes: **{report.get('scanned', 0)}**",
        f"- OK: **{report.get('ok_meshes', 0)}** · Broken: **{report.get('broken_meshes', 0)}**",
        f"- Unique types: **{report.get('unique_types', 0)}** · Unique broken: **{report.get('unique_broken_types', 0)}**",
        f"- Fuzzy soft-match: **{'ON' if report.get('fuzzy_enabled') else 'OFF'}**",
        "",
        "## Unique broken types",
        "",
    ]
    csv_rows = ["stem,count,why,slot_signature,representative,recommendation"]

    for g in report.get("broken") or []:
        a = g.get("analysis") or {}
        lines.append(f"### `{g.get('stem')}` ×{g.get('count', 0)}")
        lines.append(f"- Why: `{g.get('why')}`")
        lines.append(f"- Representative: `{g.get('representative')}`")
        lines.append(f"- Slot signature: `{g.get('slot_signature')}`")
        if a.get("glass_slots") or a.get("trim_slots"):
            lines.append(
                f"- Glass slots: `{', '.join(a.get('glass_slots') or []) or '—'}` · "
                f"PropTrim slots: `{', '.join(a.get('trim_slots') or []) or '—'}`"
            )
        lines.append(f"- Recommendation: {a.get('recommendation') or g.get('recommendation') or '—'}")
        for row in a.get("slots") or []:
            lines.append(
                f"  - slot `{row.get('slot')}` → `{row.get('mi')}` "
                f"family=`{row.get('family')}` exists={row.get('mi_exists')} "
                f"params={row.get('tex_params')} notes={row.get('notes')}"
            )
        if a.get("similar_meshes"):
            lines.append(f"- Similar meshes: {', '.join(a['similar_meshes'][:8])}")
        lines.append("")
        rec = (a.get("recommendation") or "").replace(",", ";")
        csv_rows.append(
            f"{g.get('stem')},{g.get('count')},{g.get('why')},"
            f"\"{g.get('slot_signature')}\",{g.get('representative')},\"{rec}\""
        )

    # Always document Wrh_Ceiling glass vs trim when present in content
    lines.extend([
        "## Reference — Warehouse ceiling (glass vs PropTrim)",
        "",
        "Win variants of `SM_Wrh_Ceiling_*` use separate StaticMaterials:",
        "1. `BrokenGlass` / `M_BrokenGlassSDF` → **glass** family (transmission)",
        "2. `M_PropTrims` / `MI_Wrh_Ceiling_01_PropTrim_PaintedBeams_01_A` → **metal** "
        "(inherits `CR Texture` + `NXX/NMX` from `MI_PropTrim_Painted_01_A` / Beams sibling)",
        "",
        "Non-win ceilings add concrete roof + `PaintedWorn` PropTrim slots — still multi-slot, no fuzzy.",
        "",
    ])

    md_body = "\n".join(lines)
    csv_body = "\n".join(csv_rows) + "\n"
    for dest in destinations:
        md_path = os.path.join(dest, md_name)
        csv_path = os.path.join(dest, csv_name)
        try:
            with open(md_path, "w", encoding="utf-8") as fh:
                fh.write(md_body)
            written.append(md_path)
        except OSError as exc:
            print(f"Arc Raiders material audit: failed MD {md_path}: {exc}")
        try:
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write(csv_body)
            written.append(csv_path)
        except OSError as exc:
            print(f"Arc Raiders material audit: failed CSV {csv_path}: {exc}")

    # Also emit missing-texture focused report (unique MI ∷ reason).
    written.extend(_write_missing_textures_audit(report, destinations))
    return written



def _write_missing_textures_audit(report: dict, destinations: list) -> list[str]:
    """Write docs/MISSING_TEXTURES_AUDIT.md + CSV from map audit slot analysis."""
    written: list[str] = []
    # Group by mi + tex_reason across broken types
    by_key: dict[str, dict] = {}
    for g in report.get("broken") or []:
        a = g.get("analysis") or {}
        for row in a.get("slots") or []:
            reason = (row.get("tex_reason") or "").strip()
            if not reason and row.get("mi_exists") and not row.get("missing_pngs"):
                continue
            if not reason:
                if not row.get("mi_exists") and row.get("mi"):
                    reason = "mi_json_corrupt_or_missing"
                elif not row.get("mi"):
                    reason = "empty_or_worldgrid_slot"
                else:
                    continue
            mi = row.get("mi") or "(no MI)"
            ukey = f"{mi}::{reason}"
            if ukey not in by_key:
                by_key[ukey] = {
                    "asset": g.get("stem") or "",
                    "mi": mi,
                    "expected": row.get("expected_textures") or "|".join(row.get("tex_params") or []),
                    "found": row.get("found_textures") or "",
                    "reason": reason,
                    "suggested_fix": row.get("suggested_fix") or a.get("recommendation") or "",
                    "count": 0,
                    "samples": [],
                }
            by_key[ukey]["count"] += int(g.get("count") or 1)
            stem = g.get("stem") or ""
            if stem and stem not in by_key[ukey]["samples"] and len(by_key[ukey]["samples"]) < 6:
                by_key[ukey]["samples"].append(stem)

    unique = sorted(by_key.values(), key=lambda x: (-x["count"], x["reason"], x["mi"]))
    reason_counts: dict[str, int] = {}
    for u in unique:
        reason_counts[u["reason"]] = reason_counts.get(u["reason"], 0) + u["count"]

    md_lines = [
        f"# Missing Textures Audit — {report.get('map') or '(any)'}",
        "",
        f"- From **Audit Map Materials** (live scene)",
        f"- Unique missing-texture types: **{len(unique)}**",
        f"- Fuzzy soft-match: **{'ON' if report.get('fuzzy_enabled') else 'OFF'}**",
        "",
        "## Counts by failure reason",
        "",
        "| Reason | Rows |",
        "|--------|------|",
    ]
    for reason, n in sorted(reason_counts.items(), key=lambda t: -t[1]):
        md_lines.append(f"| `{reason}` | {n} |")
    md_lines.extend(["", "## Unique types", ""])
    for u in unique[:100]:
        md_lines.append(f"### `{u['mi']}` — `{u['reason']}` ×{u['count']}")
        md_lines.append(f"- Expected: `{u['expected'] or '—'}`")
        md_lines.append(f"- Found: `{u['found'] or '—'}`")
        md_lines.append(f"- Samples: {', '.join(f'`{s}`' for s in u['samples'])}")
        md_lines.append(f"- Suggested fix: {u['suggested_fix'] or '—'}")
        md_lines.append("")
    md_lines.extend([
        "## Regenerate",
        "",
        "1. **Audit Map Materials (unique types)** in the addon panel",
        "2. Or offline: `python audit_missing_textures.py`",
        "3. After fixes: Stage 2 **Force** materials rebuild",
        "",
    ])
    csv_lines = [
        "asset,mi,expected textures,found/missing,reason,suggested fix,count,sample_assets"
    ]
    for u in unique:
        csv_lines.append(
            f"\"{u['samples'][0] if u['samples'] else u['asset']}\","
            f"\"{u['mi']}\","
            f"\"{(u['expected'] or '').replace(chr(34), '')}\","
            f"\"{(u['found'] or '').replace(chr(34), '')}\","
            f"\"{u['reason']}\","
            f"\"{(u['suggested_fix'] or '').replace(chr(34), '').replace(',', ';')}\","
            f"{u['count']},"
            f"\"{';'.join(u['samples'])}\""
        )

    md_body = "\n".join(md_lines)
    csv_body = "\n".join(csv_lines) + "\n"
    for dest in destinations:
        for name, body in (
            ("MISSING_TEXTURES_AUDIT.md", md_body),
            ("MISSING_TEXTURES_AUDIT.csv", csv_body),
        ):
            path = os.path.join(dest, name)
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(body)
                written.append(path)
            except OSError as exc:
                print(f"Arc Raiders missing-textures audit: failed {path}: {exc}")
    return written



def _object_looks_like_flat_plane(obj) -> bool:
    if not obj:
        return False
    if obj.get("arc_plane_mesh"):
        return True
    asset = str(obj.get("arc_asset_path") or "")
    try:
        from .. import map_placement as mp

        return bool(mp.is_plane_mesh_asset(asset, obj.name))
    except Exception:
        blob = f"{asset} {obj.name}".lower()
        return "plane" in blob or "tarmacpatch" in blob.replace("_", "")



def _ensure_plane_world_tiling(obj, meters_per_tile: float | None = None) -> int:
    """For flat plane / tarmac-patch objects, force world-space density on materials.

    Shared road MIs already bake world tiling. Unique planes that landed on env/simple
    materials still use UV — rewire those Image Texture vectors to world Position so
    huge actor scales don't create wallpaper-sized grain.
    """
    if not _object_looks_like_flat_plane(obj):
        return 0
    mpt = float(meters_per_tile or _PLANE_ROAD_METERS_PER_TILE)
    touched = 0
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or not mat.use_nodes or mat.node_tree is None:
            continue
        fam = str(mat.get("arc_mi_family") or "")
        # Tarps / foliage keep UV; roads already world-tiled at build
        if fam in (FAMILY_TARP, FAMILY_FOLIAGE, FAMILY_WEAPON, FAMILY_DECAL):
            continue
        if mat.get("arc_world_tile_m") and fam == FAMILY_ROAD:
            continue
        # Per-object material copy so we don't mutate shared wall concrete MIs
        # used by non-plane props.
        if mat.users > 1 and not mat.get("arc_plane_world_tiling"):
            mat = mat.copy()
            mat.name = (mat.name + "_PlaneWorld")[:63]
            slot.material = mat
        if _rewire_material_world_tiling(mat, mpt):
            try:
                mat["arc_world_tile_m"] = mpt
                mat["arc_plane_world_tiling"] = 1
            except Exception:
                pass
            touched += 1
    return touched



def _rewire_material_world_tiling(mat, meters_per_tile: float) -> bool:
    """Point every Image Texture Vector at world-density Mapping. Returns True if changed."""
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return False
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    tex_nodes = [n for n in nodes if n.type == "TEX_IMAGE"]
    if not tex_nodes:
        return False
    # Shared world mapping
    geo = None
    mapping = None
    for n in nodes:
        if n.type == "NEW_GEOMETRY" and n.label == "Plane World Pos":
            geo = n
        if n.type == "MAPPING" and str(n.label).startswith("World "):
            mapping = n
    if geo is None:
        geo = nodes.new("ShaderNodeNewGeometry")
        geo.label = "Plane World Pos"
        geo.location = (-2200, 0)
    if mapping is None:
        mapping = nodes.new("ShaderNodeMapping")
        mapping.location = (-1950, 0)
    mpt = max(float(meters_per_tile), 0.25)
    mapping.label = f"World {mpt:g}m/tile"
    mapping.inputs["Scale"].default_value = (1.0 / mpt, 1.0 / mpt, 1.0 / mpt)
    # Ensure Position → Mapping
    if not mapping.inputs["Vector"].is_linked:
        links.new(geo.outputs["Position"], mapping.inputs["Vector"])
    vec_out = mapping.outputs["Vector"]
    changed = False
    for tex in tex_nodes:
        # Disconnect old UV mapping
        for link in list(tex.inputs["Vector"].links):
            links.remove(link)
        links.new(vec_out, tex.inputs["Vector"])
        changed = True
    return changed

