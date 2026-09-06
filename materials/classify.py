"""
Material setup — classify domain (split from materials.py monolith).
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
    _mi_switch,
    _parse_flat_mi_json,
    _principled_base_color_info,
)



def _is_enemy_decal_mi(mi: dict) -> bool:
    """True for enemy-shell Masked NAO + Height decals — not env trim NAO.

    Env materials (e.g. ``MI_Concrete_Trim_*``) also sample ``*_NAO`` trim sheets.
    Treating any ``_NAO`` path as an enemy decal previously routed wall edge
    slots through ``_setup_enemy_decal_material`` and could fall back onto
    ``T_EnemyDecals_*`` atlases.
    """
    textures = mi.get("textures") or []
    if not textures:
        return False
    params_l = {str(p).lower(): (obj or "") for p, obj in textures}
    path_blob = " ".join(params_l.values()).lower().replace("\\", "/")
    parent_l = (mi.get("parent") or "").lower()

    if "enemydecals" in path_blob or "enemydecals" in " ".join(params_l):
        return True
    if "decaltrimsheet" in params_l:
        return True
    # Classic enemy preset: NAO (or DecalTrimsheet) + Height/HX on an enemy parent
    has_nao = "nao" in params_l or "decaltrimsheet" in params_l
    has_height = any(k in params_l for k in ("height/hx", "height", "hx"))
    if has_nao and has_height and (
        "enemy" in path_blob or "enemypreset" in parent_l or "enemy" in parent_l
    ):
        return True
    return False



def _slot_implies_map_decal(slot_lower: str) -> bool:
    """True only for slots that are actually decal/sticker/leak — not *EdgeDecal* trims."""
    slot = (slot_lower or "").strip().lower()
    if not slot:
        return False
    # Structural surface names that merely contain "decal" (ConcreteEdgeDecal).
    if "edgedecal" in slot or "edge_decal" in slot:
        return False
    if any(k in slot for k in ("concrete", "metal", "rebar", "trim", "slab", "plaster", "brick")):
        if "decal" in slot:
            return False
    if slot in (
        "decals", "decal", "m_decal", "m_leak", "leak",
        "raidermark", "raider mark", "raider_mark",
        "stickerdecal", "tagdecal", "walldecal",
    ):
        return True
    if slot.startswith("decal") or slot.endswith("decal"):
        return True
    return False



def _is_character_enemy_outfit_decal_asset(mi_stem: str = "", mi_path: str = "") -> bool:
    """True when an MI belongs to enemy/character/outfit decal atlases — not map env."""
    blob = f"{mi_stem} {mi_path}".lower().replace("\\", "/")
    if "enemydecals" in blob or "/decals/enemies/" in blob:
        return True
    if re.search(r"mi_.*enemy.*decal", blob):
        return True
    if "/characters/" in blob and "decal" in blob:
        return True
    if any(k in blob for k in ("/outfits/", "/clothing/", "outfit")) and "decal" in blob:
        return True
    return False



def _is_weapon_emissive_light_mi(mi_stem_lower: str) -> bool:
    """True for pure emissive/light MIs (not surface mats whose names contain 'light').

    Weapon lights are named like MI_Weapon_Emissive_Light_01 / MI_EmissiveLight_*.
    Do NOT match MI_Lightdrone_*, MI_LightningBot_*, etc. — those are full CR/NOM
    surface materials that used to be mis-routed by a naive ``'light' in stem`` check.
    """
    if not mi_stem_lower:
        return False
    if "emissive" in mi_stem_lower:
        return True
    # Token-boundary "light": _light_ / _light / light_ at stem edges — not "lightdrone"
    return bool(re.search(r"(^|_)light(_|$)", mi_stem_lower))



# ---------------------------------------------------------------------------
# Map / weapon MI family routing (Stage 2 + SK slots)
# See docs/TEXTURE_PROTOCOL.md for packing + layering rules.
# ---------------------------------------------------------------------------

# Layered building / concrete / road markers (NOT plain BaseColor foliage).
_ENV_LAYER_MARKERS = frozenset({
    "NOH", "Overlay", "Detail Normal Texture", "Detail Normal", "Detail Normal Worn",
    "CR Blend", "NOH Blend", "CR_Blend", "NOH_Blend",
    "1. CR", "1. NOH", "2. CR", "2. NOH",
    "3. Blend Mask", "4.. Overlay", "Base_Material_CR", "Base_Material_NOH",
    "Breakup_Material_CR", "Breakup_Material_NOH", "CR Breakup", "NOH Breakup",
    "Color Base", "Normal Base", "Color Top", "Normal Top",
    "CR Texture", "NXX/NMX Texture", "PaintBreakup", "Breakup Mask",
    "Breakup Mask - Linear Grayscale",
    "0. CR 1", "0. CR 2", "0. NOH 1", "0. NOH 2", "4. Mask",
    "HolesNXX", "WaterlineOverlay",
    # M_TrimMap_01 painted/rusted metal (numbered layer groups; the double space
    # after "1." is how the preset authors them). ``1. Wear CR`` is deliberately
    # absent — weapon presets use that name too.
    "1.  Material CR", "1.  Material NOH", "1. Material CR", "1. Material NOH",
    "2. Overlay CR", "3. Overlay CR", "4. Overlay CR",
})


# M_TrimMap_01 texture params, used to route painted metal off FAMILY_SIMPLE.
_TRIM_MAP_TEX_MARKERS = frozenset({
    "1.  Material CR", "1.  Material NOH", "1. Material CR", "1. Material NOH",
    "2. Overlay CR", "3. Overlay CR", "4. Overlay CR",
})

# Scalars/switches unique to M_TrimMap_01 (compact dumps carry no Parent).
_TRIM_MAP_PARAM_MARKERS = frozenset({
    "2. Wear Stain Amount", "3. Wear Stain Amount", "4. Wear Stain Amount",
    "2. Rust Strenght", "3. Rust Strenght", "4. Rust Strenght",
    "2. Wear Large Scale Mask", "2. Wear Options", "3. Wear Options",
    "2. Material Normal Strenght", "2. Base Material Tiling",
})


_FOLIAGE_TEX_MARKERS = frozenset({
    "CA", "1. CA", "ColorAlpha", "NTR", "1. NTR",
    "TrunkBaseColor", "TrunkNormal", "Trunk CA", "Trunk NTR",
})


_FOLIAGE_NAME_KEYS = (
    "foliage", "vegetation", "leaf", "leaves", "grass", "vine", "ivy",
    "plant", "tree", "bush", "fern", "moss", "reed", "heather", "flora",
)


_GLASS_TEX_MARKERS = frozenset({
    "CubemapInside", "CubemapOutside", "MaskTexture",
})


_METAL_PACKED_NORMALS = frozenset({
    "NXX/NMX Texture", "NOM", "NXM", "NMX", "NXX", "HolesNXX",
})


_SIMPLE_ALBEDO_KEYS = (
    # Hero/character flat MIs use Color (+ _Color suffix); env packs use CR/CA/C.
    "BaseColor", "Color", "CA", "1. CA", "CR", "CR Texture", "PM_Diffuse", "BC", "C",
    # Authoring typos / non-standard param names (exact aliases, not fuzzy).
    "BaseTextrue", "SignTexture", "RIT_ColorMap", "CM", "Texture",
    "Trim sheet",  # UXR NAO sheet; parent BCH enrich supplies true albedo when present
)

_SIMPLE_NORMAL_KEYS = (
    "Normals", "Normal", "NormalMap", "NOH", "1. NTR", "NTR",
    "NXX/NMX Texture", "NOM", "NXM", "NMX", "NXX", "PM_Normals",
    # Character SimplePBR normal (T_Fringe_NMR / PM_SpecularMasks): RG normal,
    # B/A unused constants — metallic/roughness come from MI scalars.
    "NMR", "PM_SpecularMasks",
)

# Hero packed ORM-like map: R = Roughness, G = Metallic (B unused / cavity).
# Character SimplePBR (FoxHat): RoughnessMetallicSpecular — same R/G; B≈Specular.
_SIMPLE_ROUGHNESS_METAL_KEYS = (
    "RoughnessMetal", "roughnessmetal",
    "RoughnessMetallicSpecular", "roughnessmetallicspecular",
)

# Optional colourway tint mask (often empty / stub on base skins).
_SIMPLE_TINTMASK_KEYS = (
    "TintMask", "tintmask",
)


FAMILY_EMISSIVE = "emissive"

FAMILY_SCAN = "scan_display"

FAMILY_DECAL = "decal"

# Stamp on map-mask / branding / CA+NOH decal graphs after UseAlphaForMask fix.
_MAP_DECAL_MASK_SETUP_V = "v2"

# PropTrim atlas UVOffset / rotate wiring (pre-stamp graphs sample the vent cell).
_PROPTRIM_UV_SETUP_V = "v2"

# ArchitecturePreset_Trim: mesh UV by default (parent UV Mode = 0); world only
# when MI explicitly sets UV Mode >= 1. Stamp bumps when that policy changes.
# v5: NAO is base packed normal (do not steal Detail Normal *_NOH via suffix).
# v6: NAO unpack = RG→normal (Z=1), B→AO, A→opacity; never treat Alpha as AO /
#     pipe normal Color into AO Combine×3.
# v7: Alpha = NAO.A × Global opacity only (no procedural OpacityBreakup) — matches
#     enemy NAO / hand-tuned ConcreteTrim; breakup noise was punching the mask.
_TRIM_SETUP_V = "v7"

# Water graph: Water↔Shore + ridged world noise bump + proximity/AO shore factor.
_WATER_SETUP_V = "v3_shore_prox"

_WATER_SHORE_ATTR = "arc_shore_proximity"

_DEFAULT_WATER_COLOR = (0.02, 0.09, 0.11, 1.0)

_DEFAULT_SHORE_COLOR = (0.42, 0.33, 0.20, 1.0)

FAMILY_GLASS = "glass"

FAMILY_WATER = "water"

FAMILY_FOLIAGE = "foliage"

FAMILY_SAND = "sand"

FAMILY_ROAD = "road"

FAMILY_TARP = "tarp"

FAMILY_METAL = "metal"

# M_TrimMap_01 painted metal: tinted base + wear revealing rusted metal + rust overlay.
FAMILY_TRIMMAP = "trimmap"

FAMILY_ENVIRONMENT = "environment"

FAMILY_WEAPON = "weapon"

FAMILY_SIMPLE = "simple"


# World-space metres per texture tile for flat road/plane surfaces (UV-independent).
_PLANE_ROAD_METERS_PER_TILE = 4.0

_TARP_METERS_PER_TILE = 2.0

# ArchitecturePreset_Trim WorldAlignedTexture density (UV Mode ≥ 1).
# ~1 m/tile matches UE WorldAligned defaults at UV Scale 1 for T_TrimConcrete_*.
_TRIM_WORLD_METERS_PER_TILE = 1.0


# Foliage texture fallback (when MI maps fail to resolve / are empty)
_FOLIAGE_ALBEDO_SUFFIXES = ("_ca", "_cs", "_c")

_FOLIAGE_NORMAL_SUFFIXES = ("_ntx", "_ntr", "_noh", "_n")

_SAND_EXCLUDE_NAME = (
    "sandbox", "sandpaper", "sandbag", "sandwich", "sandstone_trim",
)

_WATER_EXCLUDE_NAME = (
    "watertower", "waterheater", "watertap", "watertank", "waterlily",
    "waterline", "seaweed", "seawall", "wavebreaker", "waterfall_rock",
)



def _mi_tex_params(mi: dict) -> set:
    return {p for p, _ in (mi.get("textures") or [])}



def _blend_mode_str(mi: dict) -> str:
    bm = mi.get("blend_mode")
    if bm is None:
        return ""
    return str(bm)



def _is_masked_blend(mi: dict) -> bool:
    bm = _blend_mode_str(mi).lower()
    if isinstance(mi.get("blend_mode"), int):
        # Compact dumps: 0=Opaque, 1=Masked, 2=Translucent…
        return int(mi["blend_mode"]) == 1
    return "masked" in bm or bm in ("1",)



def _is_translucent_blend(mi: dict) -> bool:
    if mi.get("is_translucent"):
        return True
    bm = _blend_mode_str(mi).lower()
    if isinstance(mi.get("blend_mode"), int):
        return int(mi["blend_mode"]) in (2, 3, 4, 5, 6)
    return "translucent" in bm or "additive" in bm



def _is_environment_surface_mi(mi: dict) -> bool:
    """True when MI uses environment packing (NOH/Overlay/layers) rather than weapon NOM/NXM."""
    parent_l = (mi.get("parent") or "").lower()
    # Enemy/weapon presets author CR+NOM (+ optional Damage NOH/NXX). Those damage
    # maps must not divert Pop/Wasp/etc. onto the layered environment builder.
    if "enemypreset" in parent_l or "weaponpreset" in parent_l:
        return False
    if "firearm" in parent_l and "enemypreset" not in parent_l:
        # Keep firearm parents on weapon path when they carry CR+NOM.
        params_early = _mi_tex_params(mi)
        if "CR" in params_early and (params_early & {"NOM", "NXM", "NMX", "EX", "EXX"}):
            return False
    params = _mi_tex_params(mi)
    if params & _ENV_LAYER_MARKERS:
        return True
    # Plain CR+NOH concrete slabs (no Overlay param) still need the env path —
    # weapon setup ignores NOH entirely.
    if "CR" in params and "NOH" in params:
        return True
    if "CR_1" in params and "NOH_1" in params:
        return True
    # Edge-trim sheets: CR + NAO (+ optional PM_Normals / Detail Normal)
    if "CR" in params and "NAO" in params:
        return True
    return False



def _is_architecture_trim_stem(stem: str = "") -> bool:
    """True for architecture edge/interior/spline trim MI names (incl. *Trim*_Decal_*)."""
    s = (stem or "").lower()
    if not s:
        return False
    if any(
        k in s
        for k in (
            "triminterior", "interiortrim", "edgedecal", "edge_decal", "edgetrim",
            "splinetrim", "spline_trim", "concretetrim", "concrete_trim",
            "architecturetrim", "architecture_trim", "concretedecal",
        )
    ):
        return True
    # MI_EdgeTrim_Decal_*, MI_Trim*_Decal_* (not MI_Decal_* map stickers)
    if "decal" in s and "trim" in s and not s.startswith("mi_decal"):
        return True
    return False



def _is_architecture_trim_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    """True for ArchitecturePreset_Trim CR+NAO edge/trim sheets (ConcreteEdgeDecal).

    Compact FModel dumps often name the NAO param ``NA`` (file still ``*_NAO``).
    Names like ``MI_TrimInteriorCeiling_Decal_01`` are trims, not map stickers.
    """
    params = _mi_tex_params(mi)
    # CR + NAO (or compact ``NA`` alias / filename marker)
    if "CR" in params and ("NAO" in params or "NA" in params):
        return True
    params_blob = " ".join(str(p) for p in params).lower()
    if "trimconcrete" in params_blob or "trimspline" in params_blob or "splinetile" in params_blob:
        return True
    parent = str(mi.get("parent") or "").lower()
    if "architecturepreset_trim" in parent or "trim+cr+nah" in parent or "trim+cr+nom" in parent:
        return True
    stem = (mi_stem_lower or "").lower()
    if _is_architecture_trim_stem(stem):
        return True
    if "concrete_trim" in stem or "concretetrim" in stem:
        return True
    return False



def _trim_wants_world_uv(mi: dict) -> bool:
    """WorldAlignedTexture only when MI explicitly sets UV Mode ≥ 1.

    Ground truth from ``M_ArchitecturePreset_Trim+CR+NAH`` / ``M_PropTrimPreset_*``:
    parent default ``UV Mode = 0.0`` = **mesh UV**. Compact MI dumps omit UV Mode
    and inherit that default — do **not** treat omission as world-aligned (that
    world-projects trim/atlas sheets and tiles vents across the whole mesh).
    """
    if not _is_architecture_trim_mi(mi):
        return False
    scalars = mi.get("scalars") or {}
    if "UV Mode" not in scalars:
        return False
    try:
        return float(scalars["UV Mode"]) >= 0.5
    except (TypeError, ValueError):
        return False



def _needs_trim_setup_rebuild(mat, mi_path: str = "") -> bool:
    """Rebuild env trims that predate world-UV / NAO alpha CLIP / setup stamp."""
    if not mi_path or not os.path.isfile(mi_path):
        return False
    fam = str(mat.get("arc_mi_family") or "")
    try:
        mi = _parse_flat_mi_json(mi_path)
    except Exception:
        return False
    if not _is_architecture_trim_mi(mi, str(mat.get("arc_mi_stem") or "")):
        return False
    # Previously misrouted through map-decal family — must leave DECAL path.
    if fam == FAMILY_DECAL:
        return True
    if fam and fam not in (FAMILY_ENVIRONMENT, FAMILY_METAL, FAMILY_ROAD):
        return False
    if str(mat.get("arc_trim_setup") or "") != _TRIM_SETUP_V:
        return True
    if _trim_wants_world_uv(mi) and not mat.get("arc_trim_world_uv"):
        return True
    use_alpha = _mi_switch(
        mi.get("switches") or {}, "Use Alpha mask", "UseAlphaMask", default=None,
    )
    if use_alpha is False:
        return False
    if (_is_masked_blend(mi) or use_alpha) and not mat.get("arc_trim_alpha"):
        return True
    return False



def _needs_env_props_rebuild(mat, mi_path: str = "") -> bool:
    """Rebuild when env_props module stamps are missing (world paint / PropTrim detect)."""
    if not mi_path or not os.path.isfile(mi_path):
        return False
    fam = str(mat.get("arc_mi_family") or "")
    if fam and fam not in (
        FAMILY_ENVIRONMENT, FAMILY_METAL, FAMILY_ROAD, FAMILY_TRIMMAP, FAMILY_SIMPLE,
    ):
        return False
    try:
        from .env_props import (
            ENV_PROPS_SETUP_V,
            TRIMMAP_SETUP_V,
            is_architecture_trim_kind,
            is_proptrim_atlas_mi,
            is_trim_mapper_mi,
            wants_world_paint,
        )
    except Exception:
        return False
    try:
        mi = _parse_flat_mi_json(mi_path)
    except Exception:
        return False

    # TrimMap metal used to fall through to the flat-grey simple builder.
    if _is_trim_map_metal_mi(mi, os.path.splitext(os.path.basename(mi_path))[0].lower()):
        return str(mat.get("arc_trimmap_setup") or "") != TRIMMAP_SETUP_V

    uses_env_props = bool(
        wants_world_paint(mi)
        or is_proptrim_atlas_mi(mi, mi_path)
        or is_architecture_trim_kind(mi, mi_path)
        or is_trim_mapper_mi(mi, mi_path)
    )
    if not uses_env_props:
        # Null / procedural MIs (elevator cables) used to stay flat-grey forever.
        if fam == FAMILY_SIMPLE and bool(mi.get("is_null")) and (mi.get("colours") or []):
            return str(mat.get("arc_simple_setup") or "") != "v2"
        return False
    if fam == FAMILY_SIMPLE:
        return True

    if str(mat.get("arc_env_props") or "") != ENV_PROPS_SETUP_V:
        return True
    # Paint unit-scale fix: centimetre meshes previously stamped with metres paint.
    if wants_world_paint(mi):
        if not mat.get("arc_env_world_paint"):
            return True
        if mat.get("arc_paint_unit_scale") in (None, ""):
            return True
        # v4 places the band in object space unless Paint_WorldSpaceHeight is set.
        if mat.get("arc_paint_space") in (None, ""):
            return True
    return False



def _needs_proptrim_or_glass_rebuild(mat, mi_path: str = "", mi_stem: str = "") -> bool:
    """Rebuild PropTrim missing UVOffset stamp / CR, or BrokenGlass misrouted."""
    from .map_stage2 import _is_prop_trim_atlas_mi_stem
    from .env_props import is_proptrim_atlas_mi

    stem = (mi_stem or str(mat.get("arc_mi_stem") or "") or "").lower()
    fam = str(mat.get("arc_mi_family") or "")
    if "brokenglass" in stem or (stem.startswith("m_") and "glass" in stem and "visor" not in stem):
        if fam != FAMILY_GLASS:
            return True
    is_atlas = (
        "proptrim" in stem
        or "prop_trim" in stem
        or _is_prop_trim_atlas_mi_stem(stem)
    )
    if not is_atlas and mi_path and os.path.isfile(mi_path):
        try:
            mi = _parse_flat_mi_json(mi_path)
            is_atlas = is_proptrim_atlas_mi(mi, mi_path)
        except Exception:
            pass
    if not is_atlas:
        return False
    if fam and fam != FAMILY_METAL:
        return True
    # Pre-v2 graphs sampled the default PropTrim atlas cell (often vent grille).
    if str(mat.get("arc_proptrim_uv") or "") != _PROPTRIM_UV_SETUP_V:
        return True
    if not mi_path or not os.path.isfile(mi_path):
        return False
    try:
        mi = _parse_flat_mi_json(mi_path)
    except Exception:
        return False
    params = _mi_tex_params(mi)
    if "CR Texture" not in params and "CR" not in params:
        return True
    _rgb, linked = _principled_base_color_info(mat)
    return not linked



def _needs_water_setup_rebuild(mat, mi_path: str = "") -> bool:
    """Rebuild water mats that predate Water↔Shore proximity + ridged world bump."""
    if mat is None:
        return False
    if str(mat.get("arc_mi_family") or "") != FAMILY_WATER:
        return False
    if str(mat.get("arc_water_setup") or "") == _WATER_SETUP_V:
        return False
    # Force-apply / Fix White always rebuild when stamp missing, even without MI path.
    return True



def _is_water_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    """Water / ocean / lagoon / river planes — prefer colour params over textures."""
    stem = (mi_stem_lower or "").lower()
    if any(x in stem for x in _WATER_EXCLUDE_NAME):
        return False

    colour_names = {str(p).lower() for p, _ in (mi.get("colours") or [])}
    water_colour_markers = {
        "1. shore color", "shore color",
        "3. water color", "water color",
        "3. water color shallow", "water color shallow",
    }
    if colour_names & water_colour_markers:
        return True
    if "deep" in colour_names and "shallow" in colour_names:
        if any(k in stem for k in ("water", "river", "ocean", "lake", "lagoon", "swamp")):
            return True

    # Empty stub MIs (inherit parent) — name routing
    if any(
        token in stem
        for token in (
            "mi_water", "m_water", "waterplane", "oceanbackdrop", "oceanlod",
            "water_river", "river_opaque", "driedriver", "shallowlake",
            "redlake", "dunelagoon", "minorswamp", "waterbluegate",
            "waterpolluted", "water_swamp", "water_buried",
        )
    ):
        return True
    if re.search(r"(^|_)(water|ocean|lagoon)(_|$)", stem):
        return True
    if "ocean" in stem and ("backdrop" in stem or "lod" in stem):
        return True
    return False



def _is_sand_dune_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    """Sand piles / dune splines / desert ground — often IsNull with textures on parent."""
    stem = (mi_stem_lower or "").lower()
    if any(x in stem for x in _SAND_EXCLUDE_NAME):
        return False
    # Explicit sand / dune / beach naming (avoid MCP *Sandbox* rooms)
    if re.search(
        r"(sanddune|sand_dune|sandpile|sand_pile|dune_spline|dunes_|_dunes|"
        r"mi_sand|m_sand|propsand|_sandy|_sand($|_))",
        stem,
    ):
        return True
    if re.search(r"(^|_)(dune|beach|desert)(_|$)", stem) and "lagoon" not in stem:
        return True

    params = _mi_tex_params(mi)
    # South Dunes landscape-style params: Param / Param_1 / Param_2 → CH / NH / NR
    if params & {"Param", "Param_1", "Param_2", "BaceColor/Height", "Normal/Roughness"}:
        tex_blob = " ".join(p for p, _ in (mi.get("textures") or [])).lower()
        path_blob = " ".join(str(t) for _, t in (mi.get("textures") or [])).lower()
        if any(k in path_blob for k in ("sand", "dune", "beach", "desert")):
            return True
        if "param" in tex_blob and any(k in stem for k in ("sand", "dune", "south")):
            return True

    # PhysMaterial stamp sometimes lands in parent string via Properties parse gaps —
    # also accept BaseColor Tint sand MIs that are IsNull (inherit M_SandDune / M_SandPile).
    if mi.get("is_null") and any(k in stem for k in ("sand", "dune")):
        return True
    return False



def _is_foliage_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    """Vegetation / leaves / grass / vines — alpha-clipped, usually two-sided."""
    params = _mi_tex_params(mi)
    parent_l = (mi.get("parent") or "").lower()
    shade_l = (mi.get("shading_model") or "").lower()
    stem = (mi_stem_lower or "").lower()

    if params & _FOLIAGE_TEX_MARKERS:
        return True
    if "twosidedfoliage" in shade_l or "vegetation" in parent_l or "foliage" in parent_l:
        return True
    if any(k in stem for k in _FOLIAGE_NAME_KEYS):
        # Name alone is weak — require a colour/normal pair typical of foliage
        has_c = bool(params & {"BaseColor", "CA", "1. CA", "PM_Diffuse", "ColorAlpha"})
        has_n = bool(params & {"Normals", "Normal", "NormalMap", "NTR", "1. NTR", "PM_Normals"})
        if has_c or has_n or mi.get("two_sided"):
            return True
    # BaseColor + Normals with Masked + TwoSided (reeds / grass) without name hit
    if (
        ("BaseColor" in params or "CA" in params)
        and ("Normals" in params or "NormalMap" in params or "NTR" in params)
        and (mi.get("two_sided") or _is_masked_blend(mi))
        and not (params & {"NOH", "CR", "CR Texture", "NOM", "NXM"})
    ):
        # Avoid gravel/scatter that is opaque + BaseColor/NormalMap only
        if mi.get("two_sided") or "foliage" in shade_l or _is_masked_blend(mi):
            # Gravel is often Masked? Usually Opaque. Prefer two_sided or foliage shade.
            if mi.get("two_sided") or "foliage" in shade_l:
                return True
    return False



def _is_glass_env_mi(mi: dict, mi_stem_lower: str = "", slot_lower: str = "") -> bool:
    """Environment window / pane glass (not clothing visor ColorA/B path)."""
    params = _mi_tex_params(mi)
    parent_l = (mi.get("parent") or "").lower()
    stem = (mi_stem_lower or "").lower()
    slot = (slot_lower or "").lower()
    if "brokenglass" in stem or "brokenglass" in slot or "brokenglass" in parent_l:
        return True
    if slot in ("glass", "windowpane") or "windowpane" in slot:
        return True
    if params & _GLASS_TEX_MARKERS:
        return True
    if "windowpane" in parent_l or "glass_" in parent_l or parent_l.endswith("glass"):
        # Clothing visors handled elsewhere; map glass parents still match
        if "character" in parent_l and "glass_opaque" in parent_l:
            return False
        return True
    if "glass" in stem and (
        params & {"C", "NXX", "MaskTexture", "CubemapInside", "CubemapOutside"}
        or _is_translucent_blend(mi)
        or _is_masked_blend(mi)
        or mi.get("two_sided")
    ):
        path_blob = " ".join(str(t) for _, t in (mi.get("textures") or [])).lower()
        if "glass" in path_blob or "window" in path_blob or _is_translucent_blend(mi):
            return True
        if "brokenglass" in path_blob or "m_brokenglass" in stem:
            return True
    return False



def _is_road_tarmac_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    params = _mi_tex_params(mi)
    parent_l = (mi.get("parent") or "").lower()
    stem = (mi_stem_lower or "").lower()
    if params & {"0. CR 1", "0. CR 2", "0. NOH 1", "0. NOH 2"}:
        return True
    if "tarmac" in parent_l or "tarmac" in stem or "asphalt" in stem:
        return True
    if ("road" in stem or "asphalt" in stem) and (params & {"NOH", "CR", "CR_Blend", "CR Blend"}):
        return True
    return False



def _is_tarp_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    stem = (mi_stem_lower or "").lower()
    parent_l = (mi.get("parent") or "").lower()
    if "tarp" in stem or "tarp" in parent_l:
        return True
    if "awning" in stem and ("tarp" in stem or "transparenttarp" in parent_l):
        return True
    params = _mi_tex_params(mi)
    # PropPreset NR+VPO+Detail tarps often only override Variation Mask + Base Color
    if "variation mask" in {p.lower() for p in params} and (
        "proppreset" in parent_l or "nr+vpo" in parent_l.replace(" ", "")
    ):
        if "tarp" in stem or "awning" in stem:
            return True
    return False



def _is_metal_prop_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    """Painted / trim metal: CR Texture + NXX/NMX (or NOM) packing."""
    params = _mi_tex_params(mi)
    stem = (mi_stem_lower or "").lower()
    parent_l = (mi.get("parent") or "").lower()
    # Enemy/weapon CR+NOM parents stay on the weapon path
    if "enemypreset" in parent_l or "weapon" in parent_l or "firearm" in parent_l:
        return False
    has_nmx = bool(params & {"NXX/NMX Texture", "NXM", "NMX", "HolesNXX"})
    has_nom = "NOM" in params
    if "CR Texture" in params and (has_nmx or has_nom or "NXX" in params):
        return True
    if any(k in stem for k in ("metal", "steel", "aluminium", "aluminum", "proptrim", "trim_metal")):
        if has_nmx or has_nom or "CR Texture" in params or "NXX" in params:
            return True
        # PropTrim children after inherit (or AO/Overlay-only compact dumps)
        if "proptrim" in stem or "prop_trim" in stem:
            if "Overlay" in params or "Prop AO Texture" in params or "proptrim" in parent_l:
                return True
    return False



def _is_trim_map_metal_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    """True for M_TrimMap_01 children (painted metal with wear + rust overlay).

    Their numbered params (``1.  Material CR``, ``2. Overlay CR``) match none of
    the generic albedo/normal keys, so without this check they fell through to
    ``FAMILY_SIMPLE`` and rendered as flat grey.
    """
    parent = str((mi or {}).get("parent") or "").lower()
    if "enemypreset" in parent or "weaponpreset" in parent or "firearm" in parent:
        return False
    if "trimmap" in parent or "trim_map" in parent:
        return True
    params = _mi_tex_params(mi)
    if params & _TRIM_MAP_TEX_MARKERS:
        return True
    names = set((mi or {}).get("scalars") or ())
    names |= set((mi or {}).get("switches") or ())
    if names & _TRIM_MAP_PARAM_MARKERS:
        return True
    stem = (mi_stem_lower or "").lower()
    return "trimmapper" in stem or "trim_mapper" in stem


def _is_simple_surface_mi(mi: dict) -> bool:
    """BaseColor/Color+Normal (or CR+Normal / hero RoughnessMetal) without layered packs."""
    params = _mi_tex_params(mi)
    if not params:
        return False
    if params & _ENV_LAYER_MARKERS:
        return False
    if params & {"NOM", "NXM", "NMX", "Wear", "1. Wear CR", "EX", "EXX"}:
        return False
    has_albedo = bool(params & set(_SIMPLE_ALBEDO_KEYS))
    has_normal = bool(params & set(_SIMPLE_NORMAL_KEYS))
    has_rm = bool(params & {"RoughnessMetal"})
    return has_albedo or has_normal or has_rm



def _is_graphic_atlas_mi(mi: dict, mi_stem_lower: str = "") -> bool:
    """Company branding / MCP poster GraphicAtlas sheets (M_Graphic_Atlas_01)."""
    stem = (mi_stem_lower or "").lower()
    if "graphicatlas" in stem or "graphic_atlas" in stem:
        return True
    parent = str(mi.get("parent") or "").lower()
    if "graphic_atlas" in parent or "graphicatlas" in parent:
        return True
    params = _mi_tex_params(mi)
    return "Graphic Atlas" in params



def classify_mi_family(mi: dict, mi_stem_lower: str = "", slot_lower: str = "") -> str:
    """Classify an MI into a setup family. Order matters — specific before general."""
    from .enemy import _is_enemy_scan_display_mi
    stem = (mi_stem_lower or "").lower()
    slot = (slot_lower or "").lower()

    if _is_weapon_emissive_light_mi(stem):
        return FAMILY_EMISSIVE
    if _is_enemy_scan_display_mi(mi, stem, slot):
        return FAMILY_SCAN
    if "screen" in stem and "sunscreen" not in stem:
        return FAMILY_SCAN
    # Glass slots first (BrokenGlass / WindowPane) — before Decal name checks.
    if _is_glass_env_mi(mi, stem, slot):
        return FAMILY_GLASS
    # Architecture edge/interior trims BEFORE "decal" substring checks.
    # MI_TrimInteriorCeiling_Decal_01 / MI_EdgeTrim_Decal_* are CR+NAO trim sheets —
    # routing them to map-decal made BuriedCity's most-common SMAs look like vents.
    if _is_architecture_trim_mi(mi, stem) or _is_architecture_trim_stem(stem):
        return FAMILY_ENVIRONMENT
    # Branding GraphicAtlas posters BEFORE PropTrim/metal (Overlay+CR Blend params).
    # PosterFrame_*PropTrim* keeps the metal path below via proptrim stem.
    if _is_graphic_atlas_mi(mi, stem):
        return FAMILY_DECAL
    # Decal family: MI_Decal_* / crack stickers, dedicated decal slots, enemy NAO+H.
    # Do NOT treat ConcreteEdgeDecal / *Trim*_Decal_* surface slots as map decals.
    if _is_enemy_decal_mi(mi):
        return FAMILY_DECAL
    if _slot_implies_map_decal(slot):
        return FAMILY_DECAL
    if "decal" in stem and not _is_architecture_trim_stem(stem):
        return FAMILY_DECAL
    # Mask-only murals / raider marks still count as decals
    params = _mi_tex_params(mi)
    if params and params <= {"Mask", "Raider Mark Texture", "Decal Mask", "X", "SignTexture"}:
        if "sign" not in stem:
            return FAMILY_DECAL
    if _is_water_mi(mi, stem):
        return FAMILY_WATER
    if _is_sand_dune_mi(mi, stem):
        return FAMILY_SAND
    if _is_foliage_mi(mi, stem):
        return FAMILY_FOLIAGE
    if _is_road_tarmac_mi(mi, stem):
        return FAMILY_ROAD
    if _is_tarp_mi(mi, stem):
        return FAMILY_TARP
    if _is_trim_map_metal_mi(mi, stem):
        return FAMILY_TRIMMAP
    if _is_metal_prop_mi(mi, stem):
        return FAMILY_METAL
    # Enemy/weapon CR+NOM (+ Damage NOH) before env — DamageDetails_NOH used to
    # force FAMILY_ENVIRONMENT and skip Tint/Emissive weapon wiring.
    parent_l = (mi.get("parent") or "").lower()
    if (
        ("enemypreset" in parent_l or "weaponpreset" in parent_l or "firearm" in parent_l)
        and "CR" in params
        and (params & {"NOM", "NXM", "NMX", "Wear", "1. Wear CR", "EXX", "EX"})
    ):
        return FAMILY_WEAPON
    if _is_environment_surface_mi(mi):
        return FAMILY_ENVIRONMENT
    # Weapon: CR + packed metallic normal / wear, no env markers
    if params & {"NOM", "NXM", "NMX", "Wear", "1. Wear CR", "EXX", "EX"} and "CR" in params:
        return FAMILY_WEAPON
    if _is_simple_surface_mi(mi):
        return FAMILY_SIMPLE
    # Default: weapon path is a reasonable Principled CR/NOM attempt
    if "CR" in params or "NOM" in params or "NXM" in params:
        return FAMILY_WEAPON
    return FAMILY_SIMPLE



def _needs_map_decal_mask_rebuild(mat, mi_path: str = "") -> bool:
    """True for pre-v2 map decals / GraphicAtlas that need a fresh graph."""
    if mat is None:
        return False
    path = (mi_path or str(mat.get("arc_mi_path") or "")).strip()
    if not path or not os.path.isfile(path):
        return False
    try:
        mi = _parse_flat_mi_json(path)
    except Exception:
        return False
    if _is_enemy_decal_mi(mi):
        return False
    stem_l = os.path.splitext(os.path.basename(path))[0].lower()
    # GraphicAtlas previously fell through to simple/metal (no Graphic Atlas wire).
    if _is_graphic_atlas_mi(mi, stem_l) and not mat.get("arc_graphic_atlas"):
        return True
    if str(mat.get("arc_mi_family") or "") != FAMILY_DECAL:
        return False
    if str(mat.get("arc_decal_mask_setup") or "") == _MAP_DECAL_MASK_SETUP_V:
        return False
    switches = mi.get("switches") or {}
    if _mi_switch(switches, "UseAlphaForMask", default=None) is True:
        return True
    params = {str(p).lower() for p, _ in (mi.get("textures") or [])}
    if params & {"mask", "decal mask", "raider mark texture", "signtexture"}:
        return True
    for _p, obj in (mi.get("textures") or []):
        leaf = (obj or "").rsplit("/", 1)[-1].split(".", 1)[0].lower()
        if leaf.endswith("_x"):
            return True
    return False

