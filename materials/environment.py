"""
Material setup — environment domain (split from materials.py monolith).
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
    _GROUND_MAP_REFS_DIR,
    _IMAGE_FILE_EXTS,
    _INGAME_MAP_DIR_REL,
    _SANDY_MAP_TOKENS,
    _avg_rgb,
    _character_layout_search_folders,
    _contrast_mask,
    _dump_unconnected_tex,
    _env_tex_vector,
    _find_env_tex,
    _fix_unusable_image_alpha,
    _image_alpha_usable,
    _invert_mask,
    _list_image_files,
    _load_fallback_tex_image,
    _mapping_tiled,
    _mask_channel_value,
    _mi_colour,
    _mi_scalar,
    _mi_switch,
    _mix_float,
    _mix_normals_vec,
    _mix_rgba,
    _new_tex_image,
    _nao_from_tex,
    _noh_ao_from_tex,
    _parse_flat_mi_json,
    _rgb_node,
    _set_material_alpha_mode,
    _stamp_mi_family,
    _stamp_tex_fallback,
    _stem_tokens,
    _tex_lookup_from_flat_mi,
    _unlink_input,
    _wire_normal_map,
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
    FAMILY_WATER,
    _DEFAULT_SHORE_COLOR,
    _DEFAULT_WATER_COLOR,
    _FOLIAGE_ALBEDO_SUFFIXES,
    _FOLIAGE_NORMAL_SUFFIXES,
    _MAP_DECAL_MASK_SETUP_V,
    _PLANE_ROAD_METERS_PER_TILE,
    _PROPTRIM_UV_SETUP_V,
    _SIMPLE_ALBEDO_KEYS,
    _SIMPLE_NORMAL_KEYS,
    _SIMPLE_ROUGHNESS_METAL_KEYS,
    _SIMPLE_TINTMASK_KEYS,
    _TRIM_SETUP_V,
    _TRIM_WORLD_METERS_PER_TILE,
    _WATER_SETUP_V,
    _WATER_SHORE_ATTR,
    _is_architecture_trim_mi,
    _is_enemy_decal_mi,
    _is_graphic_atlas_mi,
    _is_masked_blend,
    _is_translucent_blend,
    _mi_tex_params,
    _trim_wants_world_uv,
)
from .enemy import (
    _setup_enemy_decal_material,
)
from . import env_props

_FOLIAGE_TRUNK_ALBEDO_SUFFIXES = ("_cr", "_ca", "_cs", "_c")

_FOLIAGE_TRUNK_NORMAL_SUFFIXES = ("_noh", "_ntx", "_ntr", "_n")


# Sand / dune packs (South Dunes): CH = colour+height, NR = normal+roughness, NH/NOH normals
_SAND_ALBEDO_KEYS = (
    "BaseColor", "PM_Diffuse", "BaceColor/Height", "1. CH", "CH", "CR", "CR Texture",
    "Param", "Color Base", "Base_Material_CR",
)

_SAND_NORMAL_KEYS = (
    "NormalMap", "PM_Normals", "Normal/Roughness", "1. NR", "NR", "NOH", "NH",
    "Param_1", "Param_2", "Normal Base", "Base_Material_NOH", "Normals", "Normal",
)

_SAND_ALBEDO_SUFFIXES = ("_ch", "_cr", "_ca", "_c")

_SAND_NORMAL_SUFFIXES = ("_nr", "_nh", "_noh", "_n")

# Preferred South/Dunes (+ Base) stems for dual-scale / landscape sand (2.13+)
_SAND_MACRO_ALBEDO_STEMS = (
    "T_South_Base_Sand_Dunes_05_CR",
    "T_South_Base_Sand_Dunes_05_CH",
    "T_South_Base_Sand_Dunes_04_CH",
    "T_South_Base_Sand_Dunes_01_CR",
)

_SAND_MICRO_ALBEDO_STEMS = (
    "T_South_Base_Ground_Sand_02_CH",
    "T_South_Base_Sand_Twigs_01_CH",
    "T_South_Dunes_Detail_01_NCH",
    "T_Sand_01_A_CR",
)

_SAND_FINE_NORMAL_STEMS = (
    "T_South_Base_Sand_Dunes_04_NR",
    "T_South_Base_Ground_Sand_02_NR",
    "T_South_Dunes_Sand_01_NOH",
    "T_South_Base_Sand_Twigs_01_NR",
    "T_Sand_01_A_NOR",
    "T_Sand_01_A_NR",
)

_SAND_MACRO_NORMAL_STEMS = (
    "T_South_Base_Sand_Dunes_01_N",
    "T_South_Base_Sand_Dunes_03_NH",
    "T_South_Base_Sand_Dunes_Slope_01_N",
)


# Default HLOD-inspired ground palette (Spaceport cream / gray rock / pink sediment)
_DEFAULT_HLOD_PALETTE = {
    "cream": (0.885, 0.860, 0.826),
    "rock": (0.741, 0.726, 0.709),
    "pink": (0.866, 0.785, 0.751),
    "dark": (0.42, 0.40, 0.38),
}

_HLOD_COLOR_TILE_RE = re.compile(
    r"_color_x\d+_y\d+$", re.IGNORECASE,
)


# Water plane colour / scalar aliases (real MI keys from RiverTool / lagoon / river)
_WATER_COLOR_KEYS = (
    "3. Water Color", "Water Color", "WaterColour", "WaterColor",
)

_WATER_SHALLOW_KEYS = (
    "3. Water Color Shallow", "Water Color Shallow", "Shallow",
)

_WATER_DEEP_KEYS = (
    "Deep", "Water Color Deep", "3. Water Color Deep",
)

_WATER_SHORE_KEYS = (
    "1. Shore Color", "Shore Color", "ShoreColour",
)



def _setup_environment_material(mat, mi_path: str, psk_path: str = "", family: str = FAMILY_ENVIRONMENT):
    """Principled setup for Arc environment MIs (concrete, buildings, roads, metal trims).

    Implements the layered protocol in docs/TEXTURE_PROTOCOL.md:
      base CR/NOH → optional layer-2 blend → Overlay tint → Detail Normal → Tint

    Road / tarmac families sample **world Position** so differently scaled plane
    meshes keep a consistent texel density (see ``_PLANE_ROAD_METERS_PER_TILE``).
    """
    mi = _parse_flat_mi_json(mi_path)
    # Fill in the parent preset's authored defaults (compact dumps only carry
    # overrides, and even full dumps omit everything the artist left alone).
    mi = env_props.merge_preset_defaults(mi, mi_path)
    preset_key = str(mi.get("preset_key") or "")
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    fam = family or FAMILY_ENVIRONMENT
    _stamp_mi_family(mat, fam)
    scalars = mi.get("scalars") or {}
    switches = mi.get("switches") or {}
    colours = mi.get("colours") or []
    is_trim = _is_architecture_trim_mi(mi)
    world_meters_per_tile = None
    if fam == FAMILY_ROAD:
        world_meters_per_tile = _PLANE_ROAD_METERS_PER_TILE
        try:
            mat["arc_world_tile_m"] = float(world_meters_per_tile)
        except Exception:
            pass
    elif is_trim and _trim_wants_world_uv(mi):
        # WorldAlignedTexture: continuous weathering across modular edge seams.
        uv_scale = _mi_scalar(scalars, "UV Scale", default=1.0)
        world_meters_per_tile = _TRIM_WORLD_METERS_PER_TILE / max(float(uv_scale), 0.05)
        world_meters_per_tile = max(0.25, min(world_meters_per_tile, 16.0))
        try:
            mat["arc_world_tile_m"] = float(world_meters_per_tile)
            mat["arc_trim_world_uv"] = 1
        except Exception:
            pass

    COL_TEX, COL_UTIL, COL_MIX, COL_BSDF, COL_OUT = -1800, -1100, -350, 450, 750

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    # ── Resolve layer-1 albedo / roughness (CR family) ─────────────────────
    cr1_keys = (
        "1. CR", "0. CR 1", "CR", "CR Texture", "Base_Material_CR", "Color Base",
        "CR_1", "B CR", "BC", "1.  Material CR", "1. Wear CR", "BaseColor",
        "BaseTextrue", "SignTexture", "RIT_ColorMap", "CM", "Texture", "Trim sheet",
    )
    cr2_keys = (
        "2. CR", "0. CR 2", "CR Blend", "CR_Blend", "Breakup_Material_CR",
        "CR Breakup", "Color Top", "CR_2", "B CR", "2. Overlay CR",
    )
    noh1_keys = (
        "1. NOH", "0. NOH 1", "NOH", "Normal Base", "NOH_1", "Base_Material_NOH",
        "1.  Material NOH", "1. Wear NOH", "Normal", "Normals", "NormalMap",
    )
    noh2_keys = (
        "2. NOH", "0. NOH 2", "NOH Blend", "NOH_Blend", "Breakup_Material_NOH",
        "NOH Breakup", "Normal Top", "NOH_2", "B NOH",
    )
    # PaintBreakup is deliberately absent: it belongs to the height-paint pass,
    # and letting it win the layer-2 blend mask swapped concrete's breakup sheet
    # for the paint blotch mask.
    mask_keys = (
        "3. Blend Mask", "4. Mask", "Breakup Mask", "Breakup Mask - Linear Grayscale",
        "Mask", "Variation Masks", "Variation Mask",
        "Packed Mask", "PM_SpecularMasks",
    )
    overlay_keys = (
        "4.. Overlay", "Overlay", "Color Overlay", "WaterlineOverlay",
        "2. Overlay CR", "Global Color Overlay",
    )
    detail_n_keys = (
        "1. Detail", "Detail Normal Texture", "Detail Normal",
        "Detail Normal Worn", "Detail NXR",
    )
    # Weapon-style packed normals used on some prop trims / painted metal
    packed_n_keys = (
        "NXX/NMX Texture", "NOM", "NXM", "NMX", "NXX", "HolesNXX",
        "NAO", "NA", "PM_Normals", "NormalMap",
    )

    _, cr1_img = _find_env_tex(tex_lookup, *cr1_keys)
    cr1_param = next((p for p, (_fp, im) in tex_lookup.items() if im == cr1_img), "") if cr1_img else ""
    _, cr2_img = _find_env_tex(tex_lookup, *cr2_keys)
    # Avoid treating the same CR as both layers
    if cr2_img is not None and cr1_img is not None and cr2_img == cr1_img:
        cr2_img = None
        for key in cr2_keys:
            hit, img = _find_env_tex(tex_lookup, key)
            if img is not None and img != cr1_img:
                cr2_img = img
                break

    _, noh1_img = _find_env_tex(tex_lookup, *noh1_keys)
    if not noh1_img:
        _, noh1_img = _find_env_tex(tex_lookup, *packed_n_keys)
    _, noh2_img = _find_env_tex(tex_lookup, *noh2_keys)
    if noh2_img is not None and noh1_img is not None and noh2_img == noh1_img:
        noh2_img = None

    _, mask_img = _find_env_tex(tex_lookup, *mask_keys)
    _, overlay_img = _find_env_tex(tex_lookup, *overlay_keys)
    # PM_Diffuse often aliases Overlay color-var; only use when Overlay missing
    if not overlay_img:
        _, pm_diff = _find_env_tex(tex_lookup, "PM_Diffuse")
        if pm_diff is not None and pm_diff != cr1_img:
            # Prefer when stem looks like color-var / overlay (_C / ColorVar)
            for param, (fpath, img) in tex_lookup.items():
                if img is pm_diff:
                    stem = os.path.splitext(os.path.basename(fpath))[0].lower()
                    if any(t in stem for t in ("colorvar", "color_var", "overlay", "_c")):
                        overlay_img = pm_diff
                    break

    _, detail_img = _find_env_tex(tex_lookup, *detail_n_keys)
    _, ao_img = _find_env_tex(tex_lookup, "Prop AO Texture", "AO")

    # Architecture trim packs base normal into NAO (N/A/O). Never let Detail
    # Normal's ``*_NOH`` win the base NOH slot via filename-suffix fallback —
    # that made ConcreteTrim look like wall concrete instead of the trim sheet.
    if is_trim:
        _, nao_n = _find_env_tex(tex_lookup, "NAO", "NA", "Trim sheet")
        if nao_n is not None:
            noh1_img = nao_n
            if noh2_img is not None and noh2_img == noh1_img:
                noh2_img = None

    base_tiling = _mi_scalar(scalars, "Tiling", "Tile", default=1.0)
    # PropTrim atlases + ArchitecturePreset_Trim shift UV (vent grille vs panel cells /
    # trim sheet offsets). Detect PropTrim by parent/stem — NOT all FAMILY_METAL
    # (TrimMapper / painted metal must keep normal mesh UVs).
    mi_stem_l = os.path.splitext(os.path.basename(mi_path or ""))[0].lower()
    parent_l = str(mi.get("parent") or "").lower()
    env_kind = env_props.detect_env_prop_kind(mi, mi_path, family=fam)
    is_proptrim_atlas = env_props.is_proptrim_atlas_mi(mi, mi_path)
    is_trim_mapper = env_props.is_trim_mapper_mi(mi, mi_path)
    # UV offset is family-specific (game presets):
    #   PropTrim → UVOffset / UVOffset_U (atlas cell)
    #   Arch trim → UV Offset Amount (gated by OffsetUVs)
    #   Concrete  → UVOffset is paint/overlay-related — do NOT shift base CR
    if is_proptrim_atlas:
        uv_offset = _mi_scalar(
            scalars, "UVOffset", "UV Offset", "UVOffset_U", default=0.0,
        )
    elif is_trim:
        uv_offset = _mi_scalar(scalars, "UV Offset Amount", default=0.0)
    else:
        uv_offset = 0.0
    # Architecture trim: OffsetUVs switch gates UV Offset Amount (scalar alone can
    # be leftover from a parent when the switch is off).
    offset_uvs_sw = _mi_switch(switches, "OffsetUVs", "Offset UVs", default=None)
    if is_trim and offset_uvs_sw is False:
        uv_offset = 0.0
    rotate_uv = bool(_mi_switch(
        switches, "Use Rotate UV's", "Use Rotate UVs", "Rotate UVs", default=False,
    ))
    # Scalar form of rotate (some compact dumps store 0/1 under Scalars).
    if not rotate_uv and _mi_scalar(scalars, "Use Rotate UV's", "Use Rotate UVs", default=0.0) >= 0.5:
        rotate_uv = True
    # Only PropTrim atlas cells and arch-trim UV Offset Amount need Mapping nodes.
    apply_uv_xform = bool(
        (is_proptrim_atlas and (abs(float(uv_offset)) > 1e-5 or rotate_uv))
        or (is_trim and abs(float(uv_offset)) > 1e-5)
    )
    y_cr, y_n, y_mask, y_ov, y_det, y_extra = 700, 200, -250, -700, -1100, -1600

    def _bind_tex_vec(tex_node, y: float, tiling: float = base_tiling):
        off = float(uv_offset) if apply_uv_xform else 0.0
        rot = bool(rotate_uv) if apply_uv_xform else False
        vec = _env_tex_vector(
            nodes,
            links,
            loc=(COL_TEX - 350, y),
            tiling=tiling,
            world_meters_per_tile=world_meters_per_tile,
            uv_offset=off,
            rotate_uv=rot,
            force_mapping=bool(apply_uv_xform and (abs(off) > 1e-5 or rot or is_proptrim_atlas)),
        )
        if vec is not None and tex_node is not None:
            links.new(vec, tex_node.inputs["Vector"])
        elif tex_node is not None and apply_uv_xform and (
            abs(float(uv_offset)) > 1e-5 or rotate_uv or is_proptrim_atlas
        ):
            # Even at tiling=1 / no world UV, PropTrim needs UVOffset / rotate.
            vec = _env_tex_vector(
                nodes,
                links,
                loc=(COL_TEX - 350, y),
                tiling=1.0,
                world_meters_per_tile=None,
                uv_offset=uv_offset,
                rotate_uv=rotate_uv,
                force_mapping=True,
            )
            if vec is not None:
                links.new(vec, tex_node.inputs["Vector"])

    albedo_sock = None
    rough_sock = None
    normal_sock = None
    ao_sock = None

    cr1_node = None
    if cr1_img:
        cr1_node = _new_tex_image(nodes, cr1_img, "CR / Layer1", (COL_TEX, y_cr))
        _bind_tex_vec(cr1_node, y_cr)
        albedo_sock = cr1_node.outputs["Color"]
        # BaseColor / CA alphas are opacity, not roughness
        cr1_pl = (cr1_param or "").lower()
        if cr1_pl not in ("basecolor", "ca", "1. ca", "coloralpha", "pm_diffuse"):
            rough_sock = cr1_node.outputs["Alpha"]
        else:
            rough_sock = None
            # Soft default; scalar Roughness may override below
            principled.inputs["Roughness"].default_value = _mi_scalar(
                scalars, "3. Roughness", "Roughness", "Glass Roughness", default=0.55,
            )

    noh1_node = None
    nao_alpha_sock = None
    if noh1_img:
        # Architecture trim uses NAO (N/A/O), not NOH — different channel packing.
        nao_param = next((p for p, (_fp, im) in tex_lookup.items() if im == noh1_img), "")
        nao_stem = os.path.splitext(os.path.basename(
            next((fp for _p, (fp, im) in tex_lookup.items() if im == noh1_img), "")
        ))[0].lower()
        is_nao_pack = bool(is_trim) or (
            "nao" in (nao_param or "").lower()
            or nao_stem.endswith("_nao")
            or nao_stem.endswith("_naoh")
        )
        noh1_node = _new_tex_image(
            nodes,
            noh1_img,
            "NAO / Trim Normal" if is_nao_pack else "NOH / Normal L1",
            (COL_TEX, y_n),
            non_color=True,
        )
        _bind_tex_vec(noh1_node, y_n)
        n_str = _mi_scalar(
            scalars,
            "Normal intensity", "Normal Strength", "Base Normal Strength", "NormalStrength",
            default=1.0,
        )
        # UE often authors strengths > 1; Principled Normal Map Strength stays usable ≤ 2
        n_str = min(max(n_str, 0.0), 2.5)
        if is_nao_pack:
            n_color, ao_from_nao, nao_alpha_sock = _nao_from_tex(
                nodes, links, noh1_node, (COL_UTIL, y_n),
            )
            normal_sock = _wire_normal_map(
                nodes, links, n_color, (COL_MIX - 200, y_n),
                strength=n_str, label="Base Normal",
            )
            ao_sock = ao_from_nao
        else:
            n_color, ao_from_noh = _noh_ao_from_tex(
                nodes, links, noh1_node, (COL_UTIL, y_n),
            )
            normal_sock = _wire_normal_map(
                nodes, links, n_color, (COL_MIX - 200, y_n),
                strength=n_str, label="Base Normal",
            )
            ao_sock = ao_from_noh
        # NXX/NMX / NOM alpha → Metallic when present on the packed normal
        stem = nao_stem
        param_hit = nao_param
        param_l = (param_hit or "").lower()
        is_metallic_pack = (
            any(stem.endswith(s) for s in ("_nxm", "_nmx", "_nom", "_nhm"))
            or "nmx" in param_l
            or "nxm" in param_l
            or param_l == "nom"
            or "nxx/nmx" in param_l
        )
        is_pure_nxx = (
            stem.endswith("_nxx")
            or stem.endswith("_nx")
            or param_l in ("nxx", "holesnxx")
        )
        if is_metallic_pack and not is_pure_nxx and not is_nao_pack:
            links.new(noh1_node.outputs["Alpha"], principled.inputs["Metallic"])
        elif fam == FAMILY_METAL and not is_nao_pack:
            principled.inputs["Metallic"].default_value = 0.8

    # ── Layer-2 blend (concrete / brick / stucco dual materials) ────────────
    use_breakup = _mi_switch(
        switches,
        "Enable Breakup Material", "Use Breakup Material", "Use Breakup Texture",
        "Enable Blend Textures", "Vertex color breakup",
        default=None,
    )
    has_layer2 = bool(cr2_img or noh2_img)
    if has_layer2 and use_breakup is False and mask_img is None:
        has_layer2 = False

    blend_fac = None
    if has_layer2 and mask_img:
        mask_node = _new_tex_image(
            nodes, mask_img, "Blend / Breakup Mask", (COL_TEX, y_mask), non_color=True,
        )
        mask_tile = _mi_scalar(
            scalars, "BreakupMaskTiling", "Breakup Tiling", "3. Blend Mask Size",
            "Blend Texture Tiling", default=1.0,
        )
        # Huge authored values (e.g. 3000) are world-scale; clamp for mesh UV preview
        if mask_tile > 64.0:
            mask_tile = 4.0
        _bind_tex_vec(mask_node, y_mask, tiling=mask_tile)
        raw = _mask_channel_value(nodes, links, mask_node, (COL_UTIL, y_mask))
        lo = _mi_scalar(
            scalars,
            "3. Blend Mask Low", "Breakup MaskRange_Low", "Blending_RangeLow",
            "Blend Bias", default=0.35,
        )
        hi = _mi_scalar(
            scalars,
            "3. Blend Mask High", "Breakup MaskRange_High", "Blending_RangeHigh",
            default=0.65,
        )
        # Blend Bias on VT materials is often negative; remap softly
        if lo < 0.0:
            lo = 0.2
        blend_fac = _contrast_mask(
            nodes, links, raw, lo, hi, (COL_UTIL + 220, y_mask), "Blend Contrast",
        )
    elif has_layer2:
        # No mask → soft 50/50 so the second layer still contributes
        blend_fac = 0.35

    if has_layer2 and cr2_img and albedo_sock is not None and blend_fac is not None:
        cr2_node = _new_tex_image(nodes, cr2_img, "CR / Layer2", (COL_TEX - 500, y_cr - 320))
        layer_tile = _mi_scalar(scalars, "Blend Texture Tiling", "2. Base Material Tiling", default=base_tiling)
        _bind_tex_vec(cr2_node, y_cr - 320, tiling=layer_tile)
        albedo_sock = _mix_rgba(
            nodes, links, albedo_sock, cr2_node.outputs["Color"], blend_fac,
            (COL_MIX - 80, y_cr), "Layer1↔2 Albedo",
        )
        if rough_sock is not None:
            rough_sock = _mix_float(
                nodes, links, rough_sock, cr2_node.outputs["Alpha"], blend_fac,
                (COL_MIX - 80, y_cr - 180), "Layer1↔2 Roughness",
            )

    if has_layer2 and noh2_img and normal_sock is not None and blend_fac is not None:
        noh2_node = _new_tex_image(
            nodes, noh2_img, "NOH / Normal L2", (COL_TEX - 500, y_n - 320), non_color=True,
        )
        _bind_tex_vec(noh2_node, y_n - 320)
        blend_n_str = _mi_scalar(scalars, "Blend Normal Strength", default=1.0)
        blend_n_str = min(max(blend_n_str, 0.0), 2.5)
        n2 = _wire_normal_map(
            nodes, links, noh2_node.outputs["Color"],
            (COL_MIX - 200, y_n - 280), strength=blend_n_str, label="Layer2 Normal",
        )
        # Fac: how much layer2 replaces layer1
        fac = blend_fac
        if isinstance(blend_fac, (int, float)):
            fac = min(max(float(blend_fac) * 0.85, 0.0), 1.0)
        normal_sock = _mix_normals_vec(
            nodes, links, normal_sock, n2, fac,
            (COL_MIX + 80, y_n - 100), "Layer1↔2 Normal",
        )

    # ── Overlay color variation (concrete ColorVar / rust overlays) ────────
    use_overlay = _mi_switch(
        switches, "UseOverlay", "EnableOverlay", "Color Overlay", default=None,
    )
    ov_str = _mi_scalar(
        scalars,
        "Overlay_BaseColor_Strength", "Overlay Strength", "4. Overlay Strength",
        "OverlayIntensity", default=0.5,
    )
    if overlay_img and use_overlay is not False and ov_str > 0.001 and albedo_sock is not None:
        ov_node = _new_tex_image(nodes, overlay_img, "Overlay", (COL_TEX, y_ov))
        ov_tile = _mi_scalar(
            scalars, "OverlayTiling", "2. OverlayTiling", "4. Overlay Size", default=2.0,
        )
        # Overlays can ride their own UV channel (concrete uses UV1 for colour
        # variation so it stays continuous across a modular wall).
        ov_uv_channel = 0
        if _mi_switch(switches, "UseUV2", default=False):
            ov_uv_channel = 2
        if _mi_switch(switches, "Use UV1 as Overlay UVs", "Use UV1 as Overlay UVs1", default=False):
            ov_uv_channel = 1
        if _mi_switch(switches, "Use UV0 as Overlay UVs", default=False):
            ov_uv_channel = 0
        if ov_uv_channel > 0:
            # UV layers are normalised to UV0/UV1/... on import.
            uv_node = nodes.new("ShaderNodeUVMap")
            uv_node.label = f"Overlay UV{ov_uv_channel}"
            uv_node.location = (COL_TEX - 700, y_ov)
            uv_node.uv_map = f"UV{ov_uv_channel}"
            ov_vec = _mapping_tiled(
                nodes, links, ov_tile, (COL_TEX - 350, y_ov), from_uv=uv_node.outputs["UV"],
            )
            links.new(ov_vec, ov_node.inputs["Vector"])
        else:
            _bind_tex_vec(ov_node, y_ov, tiling=ov_tile)
        ov_colour = ov_node.outputs["Color"]

        # OverlayRange Min/Max/Invert + brightness/desaturation shape how much of
        # the variation sheet actually shows (concrete authors all four).
        ov_desat = _mi_scalar(scalars, "OverlayDesaturation", default=0.0)
        if ov_desat > 0.001:
            hsv = nodes.new("ShaderNodeHueSaturation")
            hsv.label = "Overlay Desaturation"
            hsv.location = (COL_UTIL, y_ov + 240)
            hsv.inputs["Saturation"].default_value = max(1.0 - min(ov_desat, 1.0), 0.0)
            links.new(ov_colour, hsv.inputs["Color"])
            ov_colour = hsv.outputs["Color"]
        ov_bright = _mi_scalar(scalars, "OverlayBrightness", default=1.0)
        if abs(ov_bright - 1.0) > 0.01:
            bright = nodes.new("ShaderNodeMix")
            bright.data_type = "RGBA"
            bright.blend_type = "MULTIPLY"
            bright.label = f"OverlayBrightness ×{ov_bright:g}"
            bright.location = (COL_UTIL + 200, y_ov + 240)
            bright.inputs["Factor"].default_value = 1.0
            links.new(ov_colour, bright.inputs[6])
            b = max(float(ov_bright), 0.0)
            bright.inputs[7].default_value = (b, b, b, 1.0)
            ov_colour = bright.outputs[2]

        ov_amount = _mi_scalar(
            scalars, "ColorOverlayAmount", "TextureOverlayStrength", default=1.0,
        )
        ov_fac_val = min(max(ov_str * ov_amount, 0.0), 1.0)
        ov_fac = ov_fac_val
        ov_min = _mi_scalar(scalars, "OverlayRange Min", default=0.0)
        ov_max = _mi_scalar(scalars, "OverlayRange Max", default=1.0)
        ov_invert = _mi_scalar(scalars, "OverlayRange Invert", default=0.0)
        if abs(ov_min) > 0.001 or abs(ov_max - 1.0) > 0.001 or ov_invert >= 0.5:
            ov_lum = _mask_channel_value(nodes, links, ov_node, (COL_UTIL, y_ov - 240))
            shaped = _contrast_mask(
                nodes, links, ov_lum, ov_min, ov_max,
                (COL_UTIL + 200, y_ov - 240), "Overlay Range",
            )
            if ov_invert >= 0.5:
                shaped = _invert_mask(
                    nodes, links, shaped, (COL_UTIL + 400, y_ov - 240), "Overlay Invert",
                )
            scaled = nodes.new("ShaderNodeMath")
            scaled.operation = "MULTIPLY"
            scaled.label = "Overlay Amount"
            scaled.use_clamp = True
            scaled.location = (COL_UTIL + 600, y_ov - 240)
            links.new(shaped, scaled.inputs[0])
            scaled.inputs[1].default_value = ov_fac_val
            ov_fac = scaled.outputs["Value"]

        # Multiply overlay into albedo, then mix by strength (keeps ColorVar readable)
        mul = nodes.new("ShaderNodeMix")
        mul.data_type = "RGBA"
        mul.blend_type = "MULTIPLY"
        mul.label = "Overlay × Albedo"
        mul.location = (COL_MIX - 80, y_ov + 80)
        mul.inputs["Factor"].default_value = 1.0
        links.new(albedo_sock, mul.inputs[6])
        links.new(ov_colour, mul.inputs[7])
        albedo_sock = _mix_rgba(
            nodes, links, albedo_sock, mul.outputs[2], ov_fac,
            (COL_MIX + 160, y_ov + 80), "Overlay Mix",
        )
        # Optional roughness lift from overlay alpha / strength
        ov_rough = _mi_scalar(scalars, "Overlay_Roughness_Strength", default=0.0)
        if ov_rough > 0.001 and rough_sock is not None:
            rough_sock = _mix_float(
                nodes, links, rough_sock, ov_node.outputs["Alpha"],
                min(ov_rough, 1.0), (COL_MIX + 160, y_ov - 80), "Overlay → Rough",
            )

        # TopProjectedOverlay: the same sheet settles on upward faces as dirt.
        top_dirt = _mi_scalar(scalars, "TopProjectedOverlay", default=0.0)
        if top_dirt > 0.001:
            geo_n = nodes.new("ShaderNodeNewGeometry")
            geo_n.location = (COL_UTIL - 200, y_ov - 520)
            sep_n = nodes.new("ShaderNodeSeparateXYZ")
            sep_n.label = "Normal Z"
            sep_n.location = (COL_UTIL, y_ov - 520)
            links.new(geo_n.outputs["Normal"], sep_n.inputs["Vector"])
            top_fac = nodes.new("ShaderNodeMath")
            top_fac.operation = "MULTIPLY"
            top_fac.label = f"TopProjectedOverlay ×{top_dirt:g}"
            top_fac.use_clamp = True
            top_fac.location = (COL_UTIL + 220, y_ov - 520)
            links.new(sep_n.outputs["Z"], top_fac.inputs[0])
            top_fac.inputs[1].default_value = min(float(top_dirt), 1.0)
            top_mul = nodes.new("ShaderNodeMix")
            top_mul.data_type = "RGBA"
            top_mul.blend_type = "MULTIPLY"
            top_mul.label = "Top Dirt × Albedo"
            top_mul.location = (COL_MIX - 80, y_ov - 520)
            top_mul.inputs["Factor"].default_value = 1.0
            links.new(albedo_sock, top_mul.inputs[6])
            links.new(ov_colour, top_mul.inputs[7])
            albedo_sock = _mix_rgba(
                nodes, links, albedo_sock, top_mul.outputs[2], top_fac.outputs["Value"],
                (COL_MIX + 160, y_ov - 520), "Top Dirt Mix",
            )

    # ── Waterline stain (concrete presets) ─────────────────────────────────
    _, waterline_img = _find_env_tex(tex_lookup, "WaterlineOverlay", "WaterlineTexture")
    if (
        waterline_img is not None
        and albedo_sock is not None
        and _mi_switch(switches, "Use Waterline", default=False)
    ):
        wl_tile = _mi_scalar(scalars, "WaterlineOverlayTiling", default=1.0)
        wl_node = _new_tex_image(nodes, waterline_img, "Waterline", (COL_TEX, y_ov - 900))
        _bind_tex_vec(wl_node, y_ov - 900, tiling=wl_tile)
        wl_mask = _mask_channel_value(nodes, links, wl_node, (COL_UTIL, y_ov - 900))
        harsh = _mi_scalar(scalars, "WaterLineHarshness", default=0.2)
        wl_fac = _contrast_mask(
            nodes, links, wl_mask, 0.5 - harsh, 0.5 + harsh,
            (COL_UTIL + 220, y_ov - 900), "Waterline Harshness",
        )
        wl_tint = _mi_colour(colours, "Waterline_Tint", default=(1.0, 1.0, 1.0, 1.0))
        wl_rgb = _rgb_node(
            nodes, (wl_tint[0], wl_tint[1], wl_tint[2]), "Waterline Tint",
            (COL_UTIL + 220, y_ov - 1080),
        )
        stained = _mix_rgba(
            nodes, links, albedo_sock, wl_rgb, 1.0,
            (COL_MIX - 80, y_ov - 900), "Albedo × Waterline", blend="MULTIPLY",
        )
        albedo_sock = _mix_rgba(
            nodes, links, albedo_sock, stained, wl_fac,
            (COL_MIX + 160, y_ov - 900), "Waterline Mix",
        )

    # ── Base desaturation (concrete Desaturation scalar) ───────────────────
    base_desat = _mi_scalar(scalars, "Desaturation", "BaseDesaturation", default=0.0)
    if base_desat > 0.001 and albedo_sock is not None:
        desat = nodes.new("ShaderNodeHueSaturation")
        desat.label = f"Desaturation {base_desat:g}"
        desat.location = (COL_MIX + 340, y_cr - 320)
        desat.inputs["Saturation"].default_value = max(1.0 - min(base_desat, 1.0), 0.0)
        links.new(albedo_sock, desat.inputs["Color"])
        albedo_sock = desat.outputs["Color"]

    # ── Detail / micro normal (often overlaid on concrete & metals) ─────────
    use_detail = _mi_switch(
        switches,
        "Enable DetailNormal", "Enable Detail Normal", "EnableDetailNormal",
        "Use Detail", default=None,
    )
    det_str = _mi_scalar(
        scalars,
        "Detail Normal Strength", "1. Detail Normal Strength",
        "Detail Normal Intensity", "DetailNormalIntensity",
        default=0.5,
    )
    if detail_img and use_detail is not False and det_str > 0.001 and normal_sock is not None:
        det_node = _new_tex_image(
            nodes, detail_img, "Detail Normal", (COL_TEX, y_det), non_color=True,
        )
        det_tile = _mi_scalar(
            scalars,
            "Detail Normal Tiling", "DetailNormalTiling", "1. Detail Tiling",
            "Detail Normal Tile", "Detail Tile", "Custom Detail Normal Tiling",
            default=4.0,
        )
        _bind_tex_vec(det_node, y_det, tiling=det_tile)
        # NCR detail packs may carry albedo in RGB — still treat as normal for strength path
        det_n = _wire_normal_map(
            nodes, links, det_node.outputs["Color"],
            (COL_MIX - 200, y_det), strength=1.0, label="Detail Normal Map",
        )
        fac = min(max(det_str if det_str <= 1.0 else det_str / 2.0, 0.0), 1.0)
        normal_sock = _mix_normals_vec(
            nodes, links, normal_sock, det_n, fac,
            (COL_MIX + 120, y_det), "Base ⊕ Detail Normal",
        )

    # ── Prop AO / NOH occlusion → darken albedo ────────────────────────────
    if ao_img:
        ao_node = _new_tex_image(
            nodes, ao_img, "Prop AO", (COL_TEX - 500, y_extra), non_color=True,
        )
        ao_sock = _mask_channel_value(nodes, links, ao_node, (COL_UTIL, y_extra))

    if albedo_sock is not None and ao_sock is not None:
        ao_mul = nodes.new("ShaderNodeMix")
        ao_mul.data_type = "RGBA"
        ao_mul.blend_type = "MULTIPLY"
        ao_mul.label = "AO → Albedo"
        ao_mul.location = (COL_MIX + 280, y_cr - 40)
        ao_mul.inputs["Factor"].default_value = 0.7
        # Grayscale from the AO *value* socket (NAO.B or NOH.A) — never the
        # normal Color output (that was the ConcreteTrim Combine×3 bug).
        ao_rgb = nodes.new("ShaderNodeCombineColor")
        ao_rgb.label = "AO Gray"
        ao_rgb.location = (COL_MIX + 80, y_cr - 200)
        links.new(ao_sock, ao_rgb.inputs["Red"])
        links.new(ao_sock, ao_rgb.inputs["Green"])
        links.new(ao_sock, ao_rgb.inputs["Blue"])
        links.new(albedo_sock, ao_mul.inputs[6])
        links.new(ao_rgb.outputs["Color"], ao_mul.inputs[7])
        albedo_sock = ao_mul.outputs[2]

    # ── PropTrim sheet extras (slice mask, AO dirt, detail array) ──────────
    from .env_props import prop_trim as _prop_trim

    tint_mask_sock = None
    proptrim_extras = False
    if is_proptrim_atlas or preset_key == env_props.PRESET_PROPTRIM:
        tint_mask_sock = _prop_trim.slice_tint_mask(
            nodes, links, mi=mi, tex_lookup=tex_lookup,
            local_folders=local_folders, loc=(COL_TEX, y_extra - 500),
            bind_vec=_bind_tex_vec,
        )
        albedo_sock, dirt_on = _prop_trim.apply_ao_dirt(
            nodes, links, mi=mi, albedo_sock=albedo_sock, ao_sock=ao_sock,
            loc=(COL_UTIL, y_extra - 900),
        )
        normal_sock, detail_on = _prop_trim.apply_detail_array(
            nodes, links, mi=mi, normal_sock=normal_sock,
            local_folders=local_folders, loc=(COL_TEX, y_extra - 1300),
        )
        proptrim_extras = bool(tint_mask_sock is not None or dirt_on or detail_on)

    # ── Global Tint / Color Multiply ───────────────────────────────────────
    # ``RT Base Color`` is the ray-tracing proxy colour (it pairs with
    # "Use Flat Base Color for RT"), never an albedo tint — multiplying by it
    # flattened and mis-tinted every prop that authored one. Use the real tint
    # params, normalised against their preset default so an untouched MI stays
    # neutral instead of being halved by the mid-grey default.
    _TINT_PARAMS = (
        "2. ColorTint", "ColorTint", "Tint Color 01", "Color", "Tint",
        "BaseColor Tint", "GlobalTint", "1. Tint",
    )
    tint = _mi_colour(colours, *_TINT_PARAMS, default=None)
    tint_rgb = env_props.tint_multiplier(mi, preset_key, *_TINT_PARAMS)
    tint_on = _mi_switch(switches, "Use Tint", "Enable Tinting", default=True)
    if albedo_sock is not None and tint_on and any(abs(c - 1.0) > 0.01 for c in tint_rgb):
        rgb = nodes.new("ShaderNodeRGB")
        rgb.label = "Tint"
        rgb.outputs[0].default_value = (tint_rgb[0], tint_rgb[1], tint_rgb[2], 1.0)
        rgb.location = (COL_MIX + 280, y_cr + 220)
        tinted = _mix_rgba(
            nodes, links, albedo_sock, rgb.outputs[0], 1.0,
            (COL_MIX + 480, y_cr + 120), "Tint × Albedo", blend="MULTIPLY",
        )
        if tint_mask_sock is not None:
            # Slice mask keeps the tint on the sheet's painted strips only.
            albedo_sock = _mix_rgba(
                nodes, links, albedo_sock, tinted, tint_mask_sock,
                (COL_MIX + 660, y_cr + 120), "Tint × Slice Mask",
            )
        else:
            albedo_sock = tinted
    # PropTrim's second tint targets the sheet's secondary slices.
    if albedo_sock is not None and _mi_switch(switches, "Use Tint 02", default=False):
        tint2 = env_props.tint_multiplier(mi, preset_key, "Tint Color 02")
        if any(abs(c - 1.0) > 0.01 for c in tint2):
            rgb2 = nodes.new("ShaderNodeRGB")
            rgb2.label = "Tint Color 02"
            rgb2.outputs[0].default_value = (tint2[0], tint2[1], tint2[2], 1.0)
            rgb2.location = (COL_MIX + 280, y_cr + 380)
            albedo_sock = _mix_rgba(
                nodes, links, albedo_sock, rgb2.outputs[0], 0.5,
                (COL_MIX + 480, y_cr + 300), "Tint 02 × Albedo", blend="MULTIPLY",
            )

    color_mul = _mi_scalar(scalars, "Color Multiply", "ColorMultiply", default=1.0)
    if albedo_sock is not None and abs(float(color_mul) - 1.0) > 0.01:
        mul_n = nodes.new("ShaderNodeMix")
        mul_n.data_type = "RGBA"
        mul_n.blend_type = "MULTIPLY"
        mul_n.label = f"Color Multiply ×{color_mul:g}"
        mul_n.location = (COL_MIX + 480, y_cr - 40)
        mul_n.inputs["Factor"].default_value = 1.0
        links.new(albedo_sock, mul_n.inputs[6])
        c = max(0.0, float(color_mul))
        mul_n.inputs[7].default_value = (c, c, c, 1.0)
        albedo_sock = mul_n.outputs[2]

    # ── Height paint (concrete / architecture MF_ArchitecurePaint) ─────────
    paint_applied = False
    if albedo_sock is not None and env_props.wants_world_paint(mi):
        from .env_props.world_paint import (
            infer_paint_unit_scale,
            material_is_world_anchored,
        )

        paint_world = bool(_mi_switch(switches, "Paint_WorldSpaceHeight", default=False))
        if paint_world and not material_is_world_anchored(mat):
            paint_world = False
        paint_unit = infer_paint_unit_scale(mat, psk_path, local=not paint_world)
        # Real breakup sheet beats the procedural noise fallback.
        _, breakup_img = _find_env_tex(tex_lookup, "PaintBreakup")
        if breakup_img is None:
            breakup_img = env_props.load_library_image(
                env_props.PAINT_BREAKUP_TEXTURE, local_folders,
            )
        albedo_sock, rough_sock, paint_applied = env_props.apply_world_space_paint(
            nodes,
            links,
            mi=mi,
            albedo_sock=albedo_sock,
            rough_sock=rough_sock,
            loc=(COL_MIX + 200, y_extra - 200),
            unit_scale=paint_unit,
            mat=mat,
            breakup_img=breakup_img,
        )
        try:
            mat["arc_paint_unit_scale"] = float(paint_unit)
            mat["arc_paint_space"] = "world" if paint_world else "object"
        except Exception:
            pass

    # ── Weathering streaks + settled dust (PropTrim / prop presets) ────────
    albedo_sock, rough_sock, weather_applied = env_props.apply_weathering(
        nodes, links, mi=mi, albedo_sock=albedo_sock, rough_sock=rough_sock,
        local_folders=local_folders, loc=(COL_TEX - 700, y_extra - 1800),
    )
    albedo_sock, rough_sock, dust_applied = env_props.apply_top_down_dust(
        nodes, links, mi=mi, albedo_sock=albedo_sock, rough_sock=rough_sock,
        local_folders=local_folders, loc=(COL_TEX - 700, y_extra - 2600),
    )

    # ── Bind Principled ────────────────────────────────────────────────────
    if albedo_sock is not None:
        links.new(albedo_sock, principled.inputs["Base Color"])
    else:
        # Fallback flat tint so empty MIs aren't pure pink
        fallback = tint or (0.45, 0.45, 0.45, 1.0)
        principled.inputs["Base Color"].default_value = fallback

    if rough_sock is not None:
        # Soft remap UE roughness alphas that sit very dark/bright
        links.new(rough_sock, principled.inputs["Roughness"])
    else:
        principled.inputs["Roughness"].default_value = 0.55

    if normal_sock is not None:
        links.new(normal_sock, principled.inputs["Normal"])

    # Architecture trim: NAO.A = opacity mask (Masked + Use Alpha mask).
    # OpacityBreakup softens the mask with world-scale noise (elevator ConcreteTrim).
    use_alpha_mask = _mi_switch(
        switches, "Use Alpha mask", "UseAlphaMask", default=None,
    )
    if is_trim and use_alpha_mask is not False and (
        _is_masked_blend(mi) or use_alpha_mask
    ):
        if nao_alpha_sock is None:
            _, nao_img = _find_env_tex(tex_lookup, "NAO", "NA", "nao", "Trim sheet")
            if nao_img is not None and noh1_node is not None and noh1_img is nao_img:
                nao_alpha_sock = noh1_node.outputs["Alpha"]
            elif nao_img is not None:
                nao_clip = _new_tex_image(
                    nodes, nao_img, "NAO Opacity", (COL_TEX - 500, y_extra - 100),
                    non_color=True,
                )
                _bind_tex_vec(nao_clip, y_extra - 100)
                nao_alpha_sock = nao_clip.outputs["Alpha"]
        if nao_alpha_sock is not None:
            # Match enemy NAO / hand-tuned ConcreteTrim: Alpha → CLIP opacity,
            # optionally × Global opacity. Do NOT invent OpacityBreakup noise into
            # the mask — that punched holes and fought the user's graph.
            alpha_sock = nao_alpha_sock
            g_op = _mi_scalar(scalars, "Global opacity", "Global Opacity", default=1.0)
            if abs(float(g_op) - 1.0) > 1e-4:
                mul_g = nodes.new("ShaderNodeMath")
                mul_g.operation = "MULTIPLY"
                mul_g.label = f"Global opacity ×{g_op:g}"
                mul_g.use_clamp = True
                mul_g.location = (COL_MIX + 80, y_extra - 100)
                links.new(alpha_sock, mul_g.inputs[0])
                mul_g.inputs[1].default_value = min(max(float(g_op), 0.0), 1.0)
                alpha_sock = mul_g.outputs["Value"]
            links.new(alpha_sock, principled.inputs["Alpha"])
            _set_material_alpha_mode(
                mat,
                mode="CLIP",
                threshold=float(mi.get("opacity_clip") or 0.3333),
                two_sided=False,
            )
            try:
                mat["arc_trim_alpha"] = 1
            except Exception:
                pass

    # Setup stamps so Force All / Fix White rebuild stale vent-cell / pre-world-UV graphs.
    try:
        mat["arc_env_props"] = env_props.ENV_PROPS_SETUP_V
        mat["arc_env_prop_kind"] = env_kind
        if preset_key:
            mat["arc_env_preset"] = preset_key
        if paint_applied:
            mat["arc_env_world_paint"] = 1
        if proptrim_extras:
            mat["arc_proptrim_extras"] = 1
        if weather_applied:
            mat["arc_env_weathering"] = 1
        if dust_applied:
            mat["arc_env_dust"] = 1
        if is_proptrim_atlas:
            mat["arc_proptrim_uv"] = _PROPTRIM_UV_SETUP_V
            # Audit hint: PropTrim atlas without authored UVOffset often samples the
            # default cell (vent grille on Painted sheets / full-slat Wood sheets).
            try:
                mat["arc_proptrim_uv_offset"] = float(uv_offset)
                mat["arc_proptrim_uv_rotate"] = 1 if rotate_uv else 0
                if abs(float(uv_offset)) < 1e-5 and not rotate_uv:
                    mat["arc_proptrim_uv_audit"] = (
                        "no_UVOffset_in_MI — mesh UVs must select atlas cell; "
                        "if this looks like a vent grille, check UVMap / wrong MI"
                    )
                else:
                    mat["arc_proptrim_uv_audit"] = (
                        f"UVOffset={float(uv_offset):.4g} rotate={bool(rotate_uv)}"
                    )
            except Exception:
                pass
        if is_trim:
            mat["arc_trim_setup"] = _TRIM_SETUP_V
        if is_trim_mapper:
            mat["arc_trim_mapper"] = "v1"
    except Exception:
        pass

    # Unconnected leftovers for inspection
    handled = {
        node.image.filepath
        for node in nodes
        if getattr(node, "image", None) is not None
    }
    row_u = y_extra - 200
    for param, (fpath, img) in tex_lookup.items():
        if fpath in handled:
            continue
        # Skip duplicate texture-name aliases (T_Concrete_Wall_05_CR etc.)
        if param.startswith("T_") and any(
            param.endswith(suf) for suf in ("_CR", "_NOH", "_C", "_A", "_X", "_GRM", "_NOM", "_NXX", "_NMX", "_NAO",
                                           "_Color", "_Normal", "_Roughnessmetal", "_Tintmask")
        ):
            continue
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param
        node.interpolation = "Cubic"
        node.location = (COL_TEX - 700, row_u)
        row_u -= 280



def _foliage_role_from_stem(stem: str) -> str:
    s = (stem or "").lower()
    if "trunk" in s or "bark" in s:
        return "trunk"
    if "billboard" in s or "impostor" in s or "imposter" in s:
        return "billboard"
    if any(k in s for k in ("branch", "leaf", "leaves", "foliage", "needle")):
        return "leaves"
    return "foliage"



def _collect_foliage_search_dirs(mi_path: str, psk_path: str = "") -> list[str]:
    """Search order: MI/mesh dir → parent + sibling asset folders → remapped Content → MaterialLibrary."""
    folders: list[str] = []
    seen: set[str] = set()

    def _add(path: str):
        if not path:
            return
        norm = os.path.normcase(os.path.normpath(path))
        if norm in seen or not os.path.isdir(path):
            return
        seen.add(norm)
        folders.append(os.path.normpath(path))

    seeds = []
    for base in (os.path.dirname(mi_path or ""), os.path.dirname(psk_path or "")):
        if base:
            seeds.append(base)
    for seed in seeds:
        _add(seed)
        parent = os.path.dirname(seed)
        if parent:
            _add(parent)
            try:
                for name in os.listdir(parent):
                    sib = os.path.join(parent, name)
                    if os.path.isdir(sib):
                        _add(sib)
            except OSError:
                pass

    # Remap into alternate Content trees (Jsons → Unpack / Desktop dump)
    for seed in list(folders):
        try:
            for alt in utils.remap_path_into_content_dirs(seed):
                _add(alt)
                alt_parent = os.path.dirname(alt)
                if alt_parent:
                    _add(alt_parent)
                    try:
                        for name in os.listdir(alt_parent):
                            sib = os.path.join(alt_parent, name)
                            if os.path.isdir(sib):
                                _add(sib)
                    except OSError:
                        pass
        except Exception:
            pass

    # Material Library textures (bounded top-level + Vegetation-ish)
    try:
        for content_dir in utils.get_content_dirs():
            ml = os.path.join(content_dir, "Pioneer", "MaterialLibrary", "Textures")
            _add(ml)
            if os.path.isdir(ml):
                try:
                    for name in os.listdir(ml):
                        low = name.lower()
                        if any(k in low for k in (
                            "veg", "foliage", "plant", "tree", "leaf", "nature", "decal",
                        )):
                            _add(os.path.join(ml, name))
                except OSError:
                    pass
            # Shared vegetation under Environment is often beside the MI already;
            # also peek MaterialLibrary/Material_Instances Themes if present.
            themes = os.path.join(
                content_dir, "Pioneer", "MaterialLibrary", "Material_Instances", "Themes",
            )
            _add(themes)
    except Exception:
        pass

    return folders



def _score_vegetation_tex(
    tex_stem: str,
    *,
    role: str,
    mi_role: str,
    hint_tokens: set[str],
    suffixes: tuple[str, ...],
) -> float:
    low = tex_stem.lower()
    # Must match packing suffix
    matched_suf = ""
    for suf in suffixes:
        if low.endswith(suf):
            matched_suf = suf
            break
    if not matched_suf:
        return -1.0

    score = 10.0
    # Prefer first / more specific packing suffixes
    score += max(0, 4 - suffixes.index(matched_suf)) * 0.5

    base = low[: -len(matched_suf)]
    tex_tokens = _stem_tokens(base)
    if hint_tokens and tex_tokens:
        overlap = hint_tokens & tex_tokens
        score += 3.0 * len(overlap)
        # Fuzzy leftover
        ratio = difflib.SequenceMatcher(
            None, "".join(sorted(hint_tokens)), "".join(sorted(tex_tokens)),
        ).ratio()
        score += 2.0 * ratio

    billboard = "billboard" in low or "impostor" in low or "imposter" in low
    trunkish = "trunk" in low or "bark" in low or low.endswith("_cr") or "_cr_" in low
    leafish = any(k in low for k in ("leaf", "leaves", "branch", "branches", "needle", "foliage"))

    if mi_role == "leaves":
        if leafish:
            score += 6.0
        if billboard:
            score -= 4.0
        if trunkish and not leafish:
            score -= 3.0
    elif mi_role == "trunk":
        if trunkish:
            score += 6.0
        if leafish and not trunkish:
            score -= 3.0
        if billboard:
            score -= 5.0
    elif mi_role == "billboard":
        if billboard:
            score += 6.0
    else:
        if leafish:
            score += 2.0
        if billboard:
            score -= 1.5

    if role == "albedo" and matched_suf == "_ca":
        score += 1.5
    if role == "normal" and matched_suf in ("_ntx", "_ntr"):
        score += 1.5

    return score



def infer_vegetation_texture(
    role: str,
    mi_path: str,
    psk_path: str = "",
    mi_stem: str = "",
    exclude_paths: set | None = None,
) -> tuple[str, str]:
    """Find a plausible vegetation albedo/normal when MI maps are missing.

    Returns ``(abs_path, reason)`` or ``("", "")``. Soft-fails with empty strings.
    """
    log = utils.get_logger()
    role = (role or "albedo").lower()
    stem = mi_stem or os.path.splitext(os.path.basename(mi_path or ""))[0]
    mi_role = _foliage_role_from_stem(stem)
    hint = _stem_tokens(stem) | _stem_tokens(os.path.basename(psk_path or ""))
    if mi_role == "trunk":
        suffixes = (
            _FOLIAGE_TRUNK_ALBEDO_SUFFIXES if role == "albedo"
            else _FOLIAGE_TRUNK_NORMAL_SUFFIXES
        )
    else:
        suffixes = (
            _FOLIAGE_ALBEDO_SUFFIXES if role == "albedo"
            else _FOLIAGE_NORMAL_SUFFIXES
        )

    exclude = {os.path.normcase(os.path.normpath(p)) for p in (exclude_paths or set()) if p}
    best_path = ""
    best_score = 0.0
    best_reason = ""

    for folder in _collect_foliage_search_dirs(mi_path, psk_path):
        for tex_stem, fpath in _list_image_files(folder):
            key = os.path.normcase(os.path.normpath(fpath))
            if key in exclude:
                continue
            score = _score_vegetation_tex(
                tex_stem, role=role, mi_role=mi_role, hint_tokens=hint, suffixes=suffixes,
            )
            if score < 8.0:
                continue
            if score > best_score:
                best_score = score
                best_path = fpath
                best_reason = f"{os.path.basename(folder)}/{os.path.basename(fpath)} score={score:.1f}"

    if best_path:
        log.info(
            "Foliage tex fallback (%s) for '%s' → %s",
            role, stem, best_reason,
        )
        return best_path, best_reason

    log.warning(
        "Foliage tex fallback (%s) found nothing for '%s' (searched adjacent/library)",
        role, stem,
    )
    return "", ""



def _resolve_foliage_maps(
    mi: dict,
    mi_path: str,
    psk_path: str = "",
    tex_lookup: dict | None = None,
) -> tuple:
    """Return (albedo_img, normal_img, trunk_c_img, trunk_n_img, fallback_meta)."""
    tex_lookup = tex_lookup if tex_lookup is not None else {}
    albedo_keys = (
        "1. CA", "CA", "ColorAlpha", "BaseColor", "PM_Diffuse", "CR", "CR Texture",
    )
    normal_keys = (
        "1. NTR", "NTR", "Normals", "NormalMap", "Normal", "NOH", "PM_Normals",
        "NTX", "1. NTX",
    )
    trunk_c_keys = ("TrunkBaseColor", "Trunk CA", "TrunkCR")
    trunk_n_keys = ("TrunkNormal", "Trunk NTR", "TrunkNOH")

    _, albedo_img = _find_env_tex(tex_lookup, *albedo_keys)
    _, normal_img = _find_env_tex(tex_lookup, *normal_keys)
    _, trunk_c_img = _find_env_tex(tex_lookup, *trunk_c_keys)
    _, trunk_n_img = _find_env_tex(tex_lookup, *trunk_n_keys)
    if trunk_n_img is not None and trunk_n_img == normal_img:
        trunk_n_img = None

    meta: dict[str, str] = {}
    stem = os.path.splitext(os.path.basename(mi_path or ""))[0]
    used = set()
    for _p, (fp, _im) in tex_lookup.items():
        if fp:
            used.add(fp)

    if albedo_img is None:
        path, reason = infer_vegetation_texture(
            "albedo", mi_path, psk_path, mi_stem=stem, exclude_paths=used,
        )
        img = _load_fallback_tex_image(path)
        if img is not None:
            albedo_img = img
            meta["albedo"] = path
            meta["albedo_src"] = reason
            used.add(path)

    if normal_img is None:
        path, reason = infer_vegetation_texture(
            "normal", mi_path, psk_path, mi_stem=stem, exclude_paths=used,
        )
        img = _load_fallback_tex_image(path)
        if img is not None:
            normal_img = img
            meta["normal"] = path
            meta["normal_src"] = reason
            used.add(path)

    # Trunk role: if this is a trunk MI routed as foliage and still missing, try trunk pack
    if _foliage_role_from_stem(stem) == "trunk":
        if albedo_img is None:
            path, reason = infer_vegetation_texture(
                "albedo", mi_path, psk_path, mi_stem=stem, exclude_paths=used,
            )
            img = _load_fallback_tex_image(path)
            if img is not None:
                albedo_img = img
                meta["albedo"] = path
                meta["albedo_src"] = reason

    return albedo_img, normal_img, trunk_c_img, trunk_n_img, meta



def _setup_foliage_material(mat, mi_path: str, psk_path: str = ""):
    """Leaves / grass / vines / trees — BaseColor/CA + Normals/NTR, alpha clip, two-sided."""
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_FOLIAGE)

    COL_TEX, COL_UTIL, COL_MIX, COL_BSDF, COL_OUT = -1400, -800, -200, 400, 700
    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []
    switches = mi.get("switches") or {}
    log = utils.get_logger()

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    albedo_img, normal_img, trunk_c_img, trunk_n_img, fb_meta = _resolve_foliage_maps(
        mi, mi_path, psk_path, tex_lookup=tex_lookup,
    )
    for role in ("albedo", "normal"):
        if role in fb_meta:
            _stamp_tex_fallback(mat, role, fb_meta[role], fb_meta.get(f"{role}_src", ""))

    albedo_sock = None
    alpha_sock = None
    if albedo_img:
        label = "Foliage Color/CA"
        if "albedo" in fb_meta:
            label = "Foliage Color/CA (fallback)"
        alb = _new_tex_image(nodes, albedo_img, label, (COL_TEX, 400))
        albedo_sock = alb.outputs["Color"]
        alpha_sock = alb.outputs["Alpha"]
        links.new(albedo_sock, principled.inputs["Base Color"])
        links.new(alpha_sock, principled.inputs["Alpha"])
    else:
        # Prefer authored leaf tint over pure white when no texture exists
        tint = _mi_colour(
            colours,
            "Leaves Tint", "1. Tint", "Tint", "MossTint", "Raytrace Color",
            default=(0.25, 0.4, 0.15, 1.0),
        )
        principled.inputs["Base Color"].default_value = tint
        log.warning(
            "Foliage MI '%s' has no resolvable/fallback albedo — using tint colour",
            os.path.basename(mi_path),
        )

    if normal_img:
        label = "Foliage Normal/NTR"
        if "normal" in fb_meta:
            label = "Foliage Normal/NTR (fallback)"
        n_node = _new_tex_image(
            nodes, normal_img, label, (COL_TEX, 50), non_color=True,
        )
        n_str = _mi_scalar(
            scalars, "4. Normal Strength", "Normal Strength", default=1.0,
        )
        n_str = min(max(n_str, 0.0), 2.5)
        n_sock = _wire_normal_map(
            nodes, links, n_node.outputs["Color"], (COL_MIX, 50), strength=n_str,
            label="Foliage Normal",
        )
        links.new(n_sock, principled.inputs["Normal"])

    if trunk_c_img and albedo_sock is not None:
        trunk = _new_tex_image(nodes, trunk_c_img, "Trunk BaseColor", (COL_TEX - 400, -350))
        fac = _mi_scalar(scalars, "BlendRange", "Trunk Blend", default=0.0)
        fac = min(max(float(fac), 0.0), 0.35) if fac else 0.0
        if fac > 0.001:
            mixed = _mix_rgba(
                nodes, links, albedo_sock, trunk.outputs["Color"], fac,
                (COL_MIX, 250), "Leaf↔Trunk Albedo",
            )
            _unlink_input(links, principled.inputs["Base Color"])
            links.new(mixed, principled.inputs["Base Color"])
    elif trunk_c_img:
        _new_tex_image(nodes, trunk_c_img, "Trunk BaseColor", (COL_TEX - 400, -350))
    if trunk_n_img:
        _new_tex_image(nodes, trunk_n_img, "Trunk Normal", (COL_TEX - 400, -650), non_color=True)

    # Subsurface stub (StonePine: Subsurface Color; also 1. SS / SS Color)
    ss = _mi_colour(
        colours, "Subsurface Color", "1. SS", "Subsurface", "SS Color", default=None,
    )
    if ss is not None:
        # HDR subsurface colours (e.g. 5,6,2) → normalize for radius, keep weight soft
        mx = max(ss[0], ss[1], ss[2], 0.01)
        radius = (
            max(ss[0] / mx, 0.05),
            max(ss[1] / mx, 0.05),
            max(ss[2] / mx, 0.05),
        )
        try:
            principled.inputs["Subsurface Weight"].default_value = 0.15
            principled.inputs["Subsurface Radius"].default_value = radius
        except Exception:
            try:
                principled.inputs["Subsurface"].default_value = 0.15
            except Exception:
                pass

    rough = _mi_scalar(
        scalars,
        "Roughness Leaves", "Roughness Twigs", "3. Roughness", "Roughness",
        default=0.65,
    )
    principled.inputs["Roughness"].default_value = min(max(abs(float(rough)), 0.05), 1.0)

    clip = float(mi.get("opacity_clip") or 0.3333)
    use_hashed = bool(mi.get("dithered_lod")) or clip >= 0.55
    _set_material_alpha_mode(
        mat,
        mode="HASHED" if use_hashed else "CLIP",
        threshold=clip,
        two_sided=True,
    )

    # Leaves Tint / Tint — StonePine authors Use Tint + Leaves Tint
    tint = _mi_colour(
        colours,
        "Leaves Tint", "1. Tint", "Tint", "BlueGate Custom Mask Grass Tint",
        default=None,
    )
    use_tint = _mi_switch(switches, "1. Use Tint", "Use Tint", default=None)
    tint_amt = _mi_scalar(
        scalars, "Tint Leaves Amount", "Tint Amount", "Tint Branches Amount", default=0.0,
    )
    if tint is not None and albedo_sock is not None and (
        use_tint is True or (use_tint is None and tint_amt > 0.01)
    ):
        rgb = nodes.new("ShaderNodeRGB")
        rgb.label = "Leaves Tint"
        rgb.outputs[0].default_value = tint
        rgb.location = (COL_UTIL, 500)
        fac = 1.0 if use_tint else min(max(tint_amt, 0.0), 1.0)
        mul = _mix_rgba(
            nodes, links, albedo_sock, rgb.outputs[0], fac,
            (COL_MIX, 400), "Tint × Leaf", blend="MULTIPLY",
        )
        _unlink_input(links, principled.inputs["Base Color"])
        links.new(mul, principled.inputs["Base Color"])

    handled = {
        node.image.filepath
        for node in nodes
        if getattr(node, "image", None) is not None
    }
    _dump_unconnected_tex(nodes, tex_lookup, handled, COL_TEX - 700, -900)



def _collect_sand_search_dirs(mi_path: str, psk_path: str = "") -> list[str]:
    """MI/mesh dir → parent/siblings → **/Dunes/Textures → remapped Content roots."""
    folders: list[str] = []
    seen: set[str] = set()

    def _add(path: str):
        if not path:
            return
        norm = os.path.normcase(os.path.normpath(path))
        if norm in seen or not os.path.isdir(path):
            return
        seen.add(norm)
        folders.append(os.path.normpath(path))

    seeds = []
    for base in (os.path.dirname(mi_path or ""), os.path.dirname(psk_path or "")):
        if base:
            seeds.append(base)
    for seed in seeds:
        _add(seed)
        parent = os.path.dirname(seed)
        if parent:
            _add(parent)
            # Sibling mesh folders under Dunes/Meshes
            try:
                for name in os.listdir(parent):
                    sib = os.path.join(parent, name)
                    if os.path.isdir(sib):
                        _add(sib)
            except OSError:
                pass
            # Climb toward South/Dunes and add Textures/
            climb = parent
            for _ in range(6):
                if not climb:
                    break
                leaf = os.path.basename(climb).lower()
                if leaf in ("dunes", "south", "environment"):
                    _add(os.path.join(climb, "Textures"))
                    if leaf == "dunes":
                        _add(os.path.join(climb, "Textures"))
                        break
                climb = os.path.dirname(climb)

    for seed in list(folders):
        try:
            for alt in utils.remap_path_into_content_dirs(seed):
                _add(alt)
                alt_parent = os.path.dirname(alt)
                if alt_parent:
                    _add(alt_parent)
                    _add(os.path.join(alt_parent, "Textures"))
        except Exception:
            pass

    try:
        for content_dir in utils.get_content_dirs():
            dunes_tex = os.path.join(
                content_dir, "Pioneer", "Environment", "South", "Dunes", "Textures",
            )
            _add(dunes_tex)
            base_tex = os.path.join(
                content_dir, "Pioneer", "Environment", "South", "Base", "Textures",
            )
            _add(base_tex)
    except Exception:
        pass

    return folders



def _score_sand_tex(
    tex_stem: str,
    *,
    role: str,
    hint_tokens: set[str],
    suffixes: tuple[str, ...],
) -> float:
    low = tex_stem.lower()
    matched_suf = ""
    for suf in suffixes:
        if low.endswith(suf):
            matched_suf = suf
            break
    if not matched_suf:
        return 0.0

    score = 8.0
    if "sand" in low:
        score += 4.0
    if "dune" in low:
        score += 3.5
    if "beach" in low or "desert" in low:
        score += 2.0
    if "twig" in low:
        score += 1.0
    # Prefer base dune packs over slide/detail for macro albedo
    if "base_sand_dunes" in low or "south_base_sand" in low:
        score += 2.5
    if "ground_sand" in low:
        score += 2.0 if role in ("micro_albedo", "albedo") else 0.5
    if "detail" in low or "slide" in low or "breakup" in low:
        if role in ("micro_albedo", "fine_normal"):
            score += 1.5
        else:
            score -= 2.0
    if "white desert" in low.replace("_", " ") or "whitedesert" in low:
        score -= 3.0

    tokens = set(re.findall(r"[a-z0-9]+", low))
    overlap = len(tokens & hint_tokens)
    score += min(overlap, 4) * 0.75

    if role in ("albedo", "macro_albedo", "micro_albedo") and matched_suf in ("_ch", "_cr"):
        score += 1.5
    if role in ("normal", "fine_normal") and matched_suf == "_nr":
        score += 2.0
    elif role in ("normal", "fine_normal", "macro_normal") and matched_suf in ("_nh", "_noh", "_n"):
        score += 1.2
    if role == "macro_normal" and matched_suf in ("_n", "_nh"):
        score += 1.5
    if role == "micro_albedo" and ("ground" in low or "detail" in low or "twig" in low):
        score += 2.0
    return score



def map_prefers_sand_ground(map_name: str = "") -> bool:
    """True when CityGroundPlane should use sand BRDF (RivenTides / dunes / desert)."""
    low = (map_name or "").lower().replace(" ", "_")
    if not low:
        return False
    return any(tok in low for tok in _SANDY_MAP_TOKENS)



def _clamp_sand_uv_tiling(raw: float, default: float = 1.0) -> float:
    """MI Tiling for mesh UV — reject absurd values (e.g. 500) that scream-tile."""
    try:
        t = float(raw or 0.0)
    except (TypeError, ValueError):
        t = 0.0
    if t <= 0.0:
        return float(default)
    if t > 32.0:
        # Likely world-cm / mistaken Mapping Scale — keep a readable UV tile
        return 4.0
    return max(0.15, min(16.0, t))



def _find_sand_stem_image(
    preferred_stems: tuple[str, ...],
    search_dirs: list[str],
    exclude_paths: set | None = None,
    *,
    load: bool = True,
):
    """Find first matching preferred stem (order preserved) from sand search dirs.

    When ``load`` is False (or bpy.data unavailable), returns ``(None, path, reason)``.
    """
    exclude = {os.path.normcase(os.path.normpath(p)) for p in (exclude_paths or set()) if p}
    can_load = load and getattr(bpy, "data", None) is not None

    # Index stems once per folder for ordered preference walk
    by_stem: dict[str, tuple[str, str]] = {}
    for folder in search_dirs:
        for tex_stem, fpath in _list_image_files(folder):
            key = os.path.normcase(os.path.normpath(fpath))
            if key in exclude:
                continue
            low = tex_stem.lower()
            if low not in by_stem:
                by_stem[low] = (fpath, folder)

    for stem in preferred_stems:
        target = stem.lower()
        hit = by_stem.get(target)
        if hit is None:
            # Prefix fallback (e.g. stem without exact export)
            for low, (fpath, folder) in by_stem.items():
                if low.startswith(target):
                    hit = (fpath, folder)
                    break
        if hit is None:
            continue
        fpath, folder = hit
        reason = f"{os.path.basename(folder)}/{os.path.basename(fpath)}"
        if not can_load:
            return None, fpath, reason
        img = _load_fallback_tex_image(fpath)
        if img is not None:
            return img, fpath, reason
    return None, "", ""



def infer_sand_texture(
    role: str,
    mi_path: str,
    psk_path: str = "",
    mi_stem: str = "",
    exclude_paths: set | None = None,
) -> tuple[str, str]:
    """Find sand/dune albedo or normal when MI Texture params are empty / unresolved.

    Returns ``(abs_path, reason)`` or ``("", "")``.
    """
    log = utils.get_logger()
    role = (role or "albedo").lower()
    stem = mi_stem or os.path.splitext(os.path.basename(mi_path or ""))[0]
    hint = _stem_tokens(stem) | _stem_tokens(os.path.basename(psk_path or ""))
    hint |= {"sand", "dune", "south"}
    if role in ("albedo", "macro_albedo", "micro_albedo"):
        suffixes = _SAND_ALBEDO_SUFFIXES
    else:
        suffixes = _SAND_NORMAL_SUFFIXES

    # Prefer curated stems first for dual-scale roles
    dirs = _collect_sand_search_dirs(mi_path, psk_path)
    exclude = {os.path.normcase(os.path.normpath(p)) for p in (exclude_paths or set()) if p}
    preferred = ()
    if role in ("albedo", "macro_albedo"):
        preferred = _SAND_MACRO_ALBEDO_STEMS
    elif role == "micro_albedo":
        preferred = _SAND_MICRO_ALBEDO_STEMS
    elif role in ("normal", "fine_normal"):
        preferred = _SAND_FINE_NORMAL_STEMS
    elif role == "macro_normal":
        preferred = _SAND_MACRO_NORMAL_STEMS
    if preferred:
        _img, path, reason = _find_sand_stem_image(
            preferred, dirs, exclude, load=False,
        )
        if path:
            log.info("Sand tex preferred (%s) for '%s' → %s", role, stem, reason)
            return path, reason

    best_path = ""
    best_score = 0.0
    best_reason = ""

    for folder in dirs:
        for tex_stem, fpath in _list_image_files(folder):
            key = os.path.normcase(os.path.normpath(fpath))
            if key in exclude:
                continue
            score = _score_sand_tex(
                tex_stem, role=role, hint_tokens=hint, suffixes=suffixes,
            )
            if score < 8.0:
                continue
            if score > best_score:
                best_score = score
                best_path = fpath
                best_reason = f"{os.path.basename(folder)}/{os.path.basename(fpath)} score={score:.1f}"

    if best_path:
        log.info(
            "Sand tex fallback (%s) for '%s' → %s",
            role, stem, best_reason,
        )
        return best_path, best_reason

    log.warning(
        "Sand tex fallback (%s) found nothing for '%s' (searched Dunes/Textures)",
        role, stem,
    )
    return "", ""



def _resolve_sand_maps(
    mi: dict,
    mi_path: str,
    psk_path: str = "",
    tex_lookup: dict | None = None,
) -> dict:
    """Resolve dual-scale sand maps.

    Returns dict with keys: macro_albedo, micro_albedo, fine_normal, macro_normal,
    albedo_param, meta (fallback paths/reasons).
    """
    tex_lookup = tex_lookup if tex_lookup is not None else {}
    albedo_param, albedo_img = _find_env_tex(tex_lookup, *_SAND_ALBEDO_KEYS)
    normal_param, normal_img = _find_env_tex(tex_lookup, *_SAND_NORMAL_KEYS)
    if normal_img is not None and normal_img == albedo_img:
        for key in _SAND_NORMAL_KEYS:
            if key == albedo_param:
                continue
            hit, img = _find_env_tex(tex_lookup, key)
            if img is not None and img != albedo_img:
                normal_param, normal_img = hit, img
                break

    meta: dict[str, str] = {}
    stem = os.path.splitext(os.path.basename(mi_path or ""))[0]
    used = {fp for _p, (fp, _im) in tex_lookup.items() if fp}

    macro_alb = albedo_img
    if macro_alb is None:
        path, reason = infer_sand_texture(
            "macro_albedo", mi_path, psk_path, mi_stem=stem, exclude_paths=used,
        )
        img = _load_fallback_tex_image(path)
        if img is not None:
            macro_alb = img
            meta["macro_albedo"] = path
            meta["macro_albedo_src"] = reason
            used.add(path)

    micro_alb = None
    path, reason = infer_sand_texture(
        "micro_albedo", mi_path, psk_path, mi_stem=stem, exclude_paths=used,
    )
    img = _load_fallback_tex_image(path)
    if img is not None and img != macro_alb:
        micro_alb = img
        meta["micro_albedo"] = path
        meta["micro_albedo_src"] = reason
        used.add(path)

    fine_n = normal_img
    if fine_n is None:
        path, reason = infer_sand_texture(
            "fine_normal", mi_path, psk_path, mi_stem=stem, exclude_paths=used,
        )
        img = _load_fallback_tex_image(path)
        if img is not None:
            fine_n = img
            meta["fine_normal"] = path
            meta["fine_normal_src"] = reason
            used.add(path)

    macro_n = None
    path, reason = infer_sand_texture(
        "macro_normal", mi_path, psk_path, mi_stem=stem, exclude_paths=used,
    )
    img = _load_fallback_tex_image(path)
    if img is not None and img != fine_n:
        macro_n = img
        meta["macro_normal"] = path
        meta["macro_normal_src"] = reason

    # Back-compat stamps for verify / UI
    if "macro_albedo" in meta:
        meta["albedo"] = meta["macro_albedo"]
        meta["albedo_src"] = meta.get("macro_albedo_src", "")
    if "fine_normal" in meta:
        meta["normal"] = meta["fine_normal"]
        meta["normal_src"] = meta.get("fine_normal_src", "")

    return {
        "macro_albedo": macro_alb,
        "micro_albedo": micro_alb,
        "fine_normal": fine_n,
        "macro_normal": macro_n,
        "albedo_param": albedo_param or "",
        "normal_param": normal_param or "",
        "meta": meta,
    }



def _sand_img_stem(img, tex_lookup: dict, meta: dict, meta_key: str) -> str:
    path = ""
    if img is not None:
        path = next((fp for _p, (fp, im) in tex_lookup.items() if im == img), "") or ""
    if not path:
        path = meta.get(meta_key, "") or ""
    return os.path.splitext(os.path.basename(path))[0].lower()



def _wire_sand_breakup_noise(nodes, links, vector_sock, loc, scale: float = 2.5):
    """Noise Fac for dual-albedo / roughness variation (Musgrave replacement)."""
    noise = nodes.new("ShaderNodeTexNoise")
    noise.label = "Sand Breakup"
    noise.location = loc
    try:
        noise.noise_dimensions = "2D"
    except Exception:
        pass
    try:
        noise.inputs["Scale"].default_value = float(scale)
        noise.inputs["Detail"].default_value = 6.0
        noise.inputs["Roughness"].default_value = 0.55
        if "Distortion" in noise.inputs:
            noise.inputs["Distortion"].default_value = 0.15
    except Exception:
        pass
    if vector_sock is not None:
        links.new(vector_sock, noise.inputs["Vector"])
    return noise.outputs["Fac"]



def _wire_dual_scale_sand(
    mat,
    *,
    maps: dict,
    scalars: dict | None = None,
    colours: list | None = None,
    tex_lookup: dict | None = None,
    coord_mode: str = "uv",
    uv_tiling: float = 1.0,
    macro_meters: float = 12.0,
    micro_meters: float = 1.5,
    apply_tint: bool = True,
    masked_alpha: bool = False,
    opacity_clip: float = 0.3333,
):
    """Build dual-scale sand Principled graph. Macro dunes = geometry; micro = BRDF.

    coord_mode:
      - ``uv``: mesh UV × uv_tiling (props / piles)
      - ``object``: Object coords in meters (CityGroundPlane / landscape)
    Never plugs normals into Displacement — macro relief comes from heightmap mesh.
    """
    scalars = scalars or {}
    colours = colours or []
    tex_lookup = tex_lookup or {}
    meta = maps.get("meta") or {}
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_SAND)

    COL_COORD, COL_TEX, COL_MIX, COL_BSDF, COL_OUT = -1800, -1100, -250, 450, 750

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    tc = nodes.new("ShaderNodeTexCoord")
    tc.location = (COL_COORD, 100)
    if coord_mode == "object":
        base_vec = tc.outputs["Object"]
        macro_scale = 1.0 / max(float(macro_meters), 0.25)
        micro_scale = 1.0 / max(float(micro_meters), 0.05)
    else:
        base_vec = tc.outputs["UV"]
        tile = _clamp_sand_uv_tiling(uv_tiling, default=1.0)
        macro_scale = tile
        micro_scale = tile * 6.0

    macro_map = nodes.new("ShaderNodeMapping")
    macro_map.label = f"Macro ×{macro_scale:g}"
    macro_map.location = (COL_COORD + 220, 220)
    macro_map.inputs["Scale"].default_value = (macro_scale, macro_scale, macro_scale)
    links.new(base_vec, macro_map.inputs["Vector"])
    macro_vec = macro_map.outputs["Vector"]

    micro_map = nodes.new("ShaderNodeMapping")
    micro_map.label = f"Micro ×{micro_scale:g}"
    micro_map.location = (COL_COORD + 220, -80)
    micro_map.inputs["Scale"].default_value = (micro_scale, micro_scale, micro_scale)
    links.new(base_vec, micro_map.inputs["Vector"])
    micro_vec = micro_map.outputs["Vector"]

    breakup = _wire_sand_breakup_noise(
        nodes, links, macro_vec, (COL_TEX - 200, 520), scale=2.2,
    )

    macro_alb = maps.get("macro_albedo")
    micro_alb = maps.get("micro_albedo")
    fine_n = maps.get("fine_normal")
    macro_n = maps.get("macro_normal")

    albedo_sock = None
    rough_sock = None
    macro_a_node = None

    if macro_alb is not None:
        label = "Sand Macro (dune CR/CH)"
        if "macro_albedo" in meta or "albedo" in meta:
            label = "Sand Macro (fallback)"
        macro_a_node = _new_tex_image(nodes, macro_alb, label, (COL_TEX, 280))
        links.new(macro_vec, macro_a_node.inputs["Vector"])
        albedo_sock = macro_a_node.outputs["Color"]
        m_stem = _sand_img_stem(macro_alb, tex_lookup, meta, "macro_albedo")
        pl = (maps.get("albedo_param") or "").lower()
        if m_stem.endswith("_cr") or pl in ("cr", "cr texture"):
            rough_sock = macro_a_node.outputs["Alpha"]

    if micro_alb is not None and albedo_sock is not None:
        micro_a_node = _new_tex_image(
            nodes, micro_alb, "Sand Micro (grain CR/CH)", (COL_TEX, 80),
        )
        links.new(micro_vec, micro_a_node.inputs["Vector"])
        albedo_sock = _mix_rgba(
            nodes, links, albedo_sock, micro_a_node.outputs["Color"], breakup,
            (COL_MIX - 80, 220), "Macro↔Micro Albedo",
        )
        if rough_sock is None:
            mi_stem = _sand_img_stem(micro_alb, tex_lookup, meta, "micro_albedo")
            if mi_stem.endswith("_cr"):
                rough_sock = micro_a_node.outputs["Alpha"]
    elif micro_alb is not None and albedo_sock is None:
        micro_a_node = _new_tex_image(
            nodes, micro_alb, "Sand Micro (grain)", (COL_TEX, 80),
        )
        links.new(micro_vec, micro_a_node.inputs["Vector"])
        albedo_sock = micro_a_node.outputs["Color"]
        macro_a_node = micro_a_node

    if albedo_sock is not None:
        links.new(albedo_sock, principled.inputs["Base Color"])
    else:
        tint = _mi_colour(
            colours,
            "BaseColor Tint", "Tint", "Color", "BaseColor",
            default=(0.72, 0.62, 0.45, 1.0),
        )
        principled.inputs["Base Color"].default_value = tint

    if apply_tint and albedo_sock is not None:
        tint = _mi_colour(colours, "BaseColor Tint", "Tint", default=None)
        if tint is not None:
            tr = min(max(float(tint[0]), 0.0), 2.0)
            tg = min(max(float(tint[1]), 0.0), 2.0)
            tb = min(max(float(tint[2]), 0.0), 2.0)
            rgb = nodes.new("ShaderNodeRGB")
            rgb.label = "BaseColor Tint"
            rgb.outputs[0].default_value = (tr, tg, tb, 1.0)
            rgb.location = (COL_MIX - 200, 450)
            mul = _mix_rgba(
                nodes, links, albedo_sock, rgb.outputs[0], 1.0,
                (COL_MIX, 380), "Tint × Sand", blend="MULTIPLY",
            )
            _unlink_input(links, principled.inputs["Base Color"])
            links.new(mul, principled.inputs["Base Color"])
            albedo_sock = mul
            try:
                mat["arc_sand_tint"] = [tr, tg, tb, 1.0]
            except Exception:
                pass

    # Normals: fine grain + optional larger dune normal — never Displacement
    n_str = _mi_scalar(scalars, "Normal Strength", "Slope Normal Strength", default=1.0)
    n_str = min(max(float(n_str), 0.0), 2.5)
    normal_sock = None
    fine_n_node = None
    if fine_n is not None:
        fine_n_node = _new_tex_image(
            nodes, fine_n, "Sand Fine Normal (NR/NOH)", (COL_TEX, -200), non_color=True,
        )
        links.new(micro_vec, fine_n_node.inputs["Vector"])
        normal_sock = _wire_normal_map(
            nodes, links, fine_n_node.outputs["Color"], (COL_MIX - 40, -120),
            strength=n_str, label="Fine Sand N",
        )
        n_stem = _sand_img_stem(fine_n, tex_lookup, meta, "fine_normal")
        if n_stem.endswith("_nr") and rough_sock is None:
            rough_sock = fine_n_node.outputs["Alpha"]

    if macro_n is not None:
        macro_n_node = _new_tex_image(
            nodes, macro_n, "Sand Macro Normal (N/NH)", (COL_TEX, -480), non_color=True,
        )
        links.new(macro_vec, macro_n_node.inputs["Vector"])
        macro_n_sock = _wire_normal_map(
            nodes, links, macro_n_node.outputs["Color"], (COL_MIX - 40, -360),
            strength=min(n_str * 0.55, 1.2), label="Macro Sand N",
        )
        if normal_sock is not None:
            normal_sock = _mix_normals_vec(
                nodes, links, normal_sock, macro_n_sock, 0.35,
                (COL_MIX + 160, -200), "Fine+Macro N",
            )
        else:
            normal_sock = macro_n_sock

    # Optional subtle bump from CH luminance only (not NOR / not Displacement Height)
    if macro_alb is not None and macro_a_node is not None:
        m_stem = _sand_img_stem(macro_alb, tex_lookup, meta, "macro_albedo")
        if m_stem.endswith("_ch"):
            bump = nodes.new("ShaderNodeBump")
            bump.label = "CH micro-bump (subtle)"
            bump.location = (COL_MIX + 160, -420)
            bump.inputs["Strength"].default_value = 0.08
            bump.inputs["Distance"].default_value = 0.04
            links.new(macro_a_node.outputs["Color"], bump.inputs["Height"])
            if normal_sock is not None:
                links.new(normal_sock, bump.inputs["Normal"])
            normal_sock = bump.outputs["Normal"]

    if normal_sock is not None:
        links.new(normal_sock, principled.inputs["Normal"])

    # Roughness: packed alpha + slight noise variation (avoid fixed 1.0 / washed Brightness)
    if rough_sock is not None:
        rough_var = _mix_float(
            nodes, links, rough_sock, breakup, 0.12,
            (COL_MIX + 80, 40), "Rough + noise",
        )
        # Keep sand fairly rough — MapRange soft clamp
        ramp = nodes.new("ShaderNodeMapRange")
        ramp.label = "Sand Rough Clamp"
        ramp.location = (COL_MIX + 260, 40)
        ramp.clamp = True
        ramp.inputs["From Min"].default_value = 0.0
        ramp.inputs["From Max"].default_value = 1.0
        ramp.inputs["To Min"].default_value = 0.55
        ramp.inputs["To Max"].default_value = 0.95
        links.new(rough_var, ramp.inputs["Value"])
        links.new(ramp.outputs["Result"], principled.inputs["Roughness"])
    else:
        principled.inputs["Roughness"].default_value = 0.82

    if masked_alpha and macro_a_node is not None:
        links.new(macro_a_node.outputs["Alpha"], principled.inputs["Alpha"])
        _set_material_alpha_mode(
            mat, mode="CLIP", threshold=float(opacity_clip), two_sided=False,
        )

    try:
        mat["arc_sand_dual_scale"] = 1
        mat["arc_sand_coord"] = coord_mode
        mat["arc_sand_macro_scale"] = float(macro_scale)
        mat["arc_sand_micro_scale"] = float(micro_scale)
        if coord_mode == "object":
            mat["arc_sand_macro_meters"] = float(macro_meters)
            mat["arc_sand_micro_meters"] = float(micro_meters)
        else:
            mat["arc_sand_tiling"] = float(uv_tiling)
    except Exception:
        pass

    return principled, out_node



def _setup_sand_material(mat, mi_path: str, psk_path: str = ""):
    """Sand piles / dune splines / desert ground — dual-scale CH/CR + NR with fallback."""
    mi = _parse_flat_mi_json(mi_path)
    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []
    log = utils.get_logger()

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    for folder in _collect_sand_search_dirs(mi_path, psk_path)[:8]:
        if folder not in local_folders:
            local_folders.append(folder)

    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)
    maps = _resolve_sand_maps(mi, mi_path, psk_path, tex_lookup=tex_lookup)
    fb_meta = maps.get("meta") or {}
    for role in ("albedo", "normal", "macro_albedo", "micro_albedo", "fine_normal"):
        if role in fb_meta:
            _stamp_tex_fallback(mat, role, fb_meta[role], fb_meta.get(f"{role}_src", ""))

    raw_tile = _mi_scalar(scalars, "Tiling", default=1.0)
    uv_tiling = _clamp_sand_uv_tiling(raw_tile, default=1.0)

    if maps.get("macro_albedo") is None and maps.get("micro_albedo") is None:
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()
        _stamp_mi_family(mat, FAMILY_SAND)
        principled = nodes.new("ShaderNodeBsdfPrincipled")
        principled.location = (400, 0)
        out_node = nodes.new("ShaderNodeOutputMaterial")
        out_node.location = (700, 0)
        links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])
        tint = _mi_colour(
            colours,
            "BaseColor Tint", "Tint", "Color", "BaseColor",
            default=(0.72, 0.62, 0.45, 1.0),
        )
        principled.inputs["Base Color"].default_value = tint
        principled.inputs["Roughness"].default_value = 0.85
        log.warning(
            "Sand MI '%s' has no resolvable/fallback albedo — using tint",
            os.path.basename(mi_path),
        )
        return

    _wire_dual_scale_sand(
        mat,
        maps=maps,
        scalars=scalars,
        colours=colours,
        tex_lookup=tex_lookup,
        coord_mode="uv",
        uv_tiling=uv_tiling,
        apply_tint=True,
        masked_alpha=_is_masked_blend(mi),
        opacity_clip=float(mi.get("opacity_clip") or 0.3333),
    )

    if _is_masked_blend(mi):
        try:
            already_clip = str(getattr(mat, "blend_method", "") or "").upper() == "CLIP"
        except Exception:
            already_clip = False
        if not already_clip:
            _set_material_alpha_mode(
                mat,
                mode="CLIP",
                threshold=float(mi.get("opacity_clip") or 0.3333),
                two_sided=False,
            )

    handled = {
        node.image.filepath
        for node in mat.node_tree.nodes
        if getattr(node, "image", None) is not None
    }
    _dump_unconnected_tex(mat.node_tree.nodes, tex_lookup, handled, -1600, -700)



def apply_heightmap_void_mask_to_material(
    mat: bpy.types.Material,
    mask_img: bpy.types.Image | None,
) -> bool:
    """Wire void mask → Principled Alpha (CLIP). No-op when mask is None.

    Used by CityGroundPlane create/reload and Apply Sand so missing-data
    regions stay clipped after the sand node tree is rebuilt.
    """
    if mat is None or mask_img is None or not getattr(mat, "use_nodes", False):
        return False
    nt = mat.node_tree
    if nt is None:
        return False
    nodes, links = nt.nodes, nt.links

    for node in list(nodes):
        if str(getattr(node, "label", "") or "").startswith("ARC Height Void"):
            nodes.remove(node)

    bsdf = None
    for node in nodes:
        if node.type == "BSDF_PRINCIPLED":
            bsdf = node
            break
    if bsdf is None or "Alpha" not in bsdf.inputs:
        return False

    tex = nodes.new("ShaderNodeTexImage")
    tex.label = "ARC Height Void Mask"
    tex.image = mask_img
    tex.interpolation = "Closest"
    tex.location = (bsdf.location.x - 360, bsdf.location.y - 280)

    alpha_in = bsdf.inputs["Alpha"]
    existing = None
    try:
        if alpha_in.is_linked:
            existing = alpha_in.links[0].from_socket
            links.remove(alpha_in.links[0])
    except Exception:
        existing = None

    if existing is not None:
        mul = nodes.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        mul.label = "ARC Height Void × Alpha"
        mul.location = (bsdf.location.x - 160, bsdf.location.y - 280)
        links.new(tex.outputs["Color"], mul.inputs[0])
        links.new(existing, mul.inputs[1])
        links.new(mul.outputs["Value"], alpha_in)
    else:
        links.new(tex.outputs["Color"], alpha_in)

    _set_material_alpha_mode(mat, mode="CLIP", threshold=0.5, two_sided=False)
    try:
        mat["arc_heightmap_void_masked"] = 1
    except Exception:
        pass
    return True



def apply_sand_to_heightmap_object(
    obj,
    *,
    map_name: str = "",
    force: bool = False,
    macro_meters: float = 12.0,
    micro_meters: float = 1.5,
    ingame_map_path: str = "",
    hlod_color_path: str = "",
    use_map_texturing: bool = True,
) -> bool:
    """Assign landscape sand BRDF to a CityGroundPlane / heightmap object.

    Preserves Displace modifier (heightmap PNG). Replaces surface material only.
    When ``use_map_texturing``, also wires In-Game Map masks + HLOD color palette.
    Returns True when sand was applied.
    """
    if obj is None or getattr(obj, "type", "") != "MESH":
        return False
    name = map_name or str(obj.get("arc_map") or "")
    if not force and not map_prefers_sand_ground(name):
        # Still allow when object already tagged or name empty but user forced via op
        if not obj.get("arc_heightmap_plane"):
            return False
        # Heuristic: displaced heightmap on unknown map — skip unless force
        if not force:
            return False

    mat = setup_landscape_sand_material(
        None,
        map_name=name or "Map",
        macro_meters=macro_meters,
        micro_meters=micro_meters,
        ingame_map_path=ingame_map_path,
        hlod_color_path=hlod_color_path,
        use_map_texturing=use_map_texturing,
    )
    mesh = obj.data
    if mesh.materials:
        mesh.materials[0] = mat
    else:
        mesh.materials.append(mat)
    try:
        obj["arc_sand_ground"] = 1
        if mat.get("arc_ground_map_textured"):
            obj["arc_ground_map_textured"] = 1
    except Exception:
        pass
    # Preserve void alpha clip after sand rebuilds the node tree.
    try:
        mask_name = str(obj.get("arc_heightmap_void_mask") or "")
        mask_img = bpy.data.images.get(mask_name) if mask_name else None
        if mask_img is not None:
            apply_heightmap_void_mask_to_material(mat, mask_img)
    except Exception:
        pass
    return True



# ---------------------------------------------------------------------------
# Heightmap ground: In-Game Map masks + HLOD color palette
# ---------------------------------------------------------------------------

def _map_name_stems(map_name: str) -> list[str]:
    """Return ordered stem candidates for Content lookups (Spaceport_01_P → …)."""
    raw = (map_name or "").strip()
    if not raw:
        return []
    base = os.path.splitext(os.path.basename(raw))[0]
    stems: list[str] = []
    seen: set[str] = set()

    def _add(s: str):
        s = (s or "").strip()
        if not s:
            return
        key = s.lower()
        if key in seen:
            return
        seen.add(key)
        stems.append(s)

    _add(base)
    # Drop common UE map suffixes
    cur = base
    for _ in range(3):
        low = cur.lower()
        stripped = cur
        for suf in ("_p", "_wp", "_terrain", "_landscape", "_main"):
            if low.endswith(suf):
                stripped = cur[: -len(suf)]
                break
        if stripped == cur:
            break
        _add(stripped)
        cur = stripped
    # Spaceport_01_P → Spaceport_01 → Spaceport
    if "_" in cur:
        head = cur.rsplit("_", 1)[0]
        if head and not head[-1:].isdigit():
            _add(head)
        elif head:
            _add(head)
    return stems



def _iter_image_files(folder: str):
    if not folder or not os.path.isdir(folder):
        return
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        low = name.lower()
        if not low.endswith(_IMAGE_FILE_EXTS):
            continue
        yield name, os.path.join(folder, name)



def _ground_search_roots(map_name: str = "") -> list[str]:
    """Folders to search for InGameMap / HLOD Color textures."""
    roots: list[str] = []
    seen: set[str] = set()

    def _add(path: str):
        if not path:
            return
        path = os.path.abspath(os.path.normpath(path))
        key = os.path.normcase(path)
        if key in seen:
            return
        if os.path.isdir(path):
            seen.add(key)
            roots.append(path)

    # Scene / workspace hints
    try:
        scene = bpy.context.scene
    except Exception:
        scene = None
    if scene is not None:
        for attr in (
            "arc_placement_mesh_root",
            "arc_placement_workspace",
        ):
            hint = getattr(scene, attr, "") or ""
            if hint:
                try:
                    _add(bpy.path.abspath(hint))
                except Exception:
                    _add(hint)
        try:
            from .. import map_placement as mp

            name = (map_name or getattr(scene, "arc_placement_map", "") or "").strip()
            if name and name != "NONE":
                _add(mp.map_output_dir(name, scene))
        except Exception:
            pass

    pioneer = utils.get_pioneer_root()
    content = utils.find_content_dir(pioneer) if pioneer else ""
    if content:
        _add(os.path.join(content, _INGAME_MAP_DIR_REL))
        maps_root = os.path.join(content, "Pioneer", "Maps")
        _add(maps_root)
        for stem in _map_name_stems(map_name):
            map_dir = os.path.join(maps_root, stem)
            _add(map_dir)
            # Common landscape texture folders
            for sub in (
                os.path.join(f"L_{stem}", "Textures"),
                "Textures",
                os.path.join(stem, "Textures"),
            ):
                _add(os.path.join(map_dir, sub))
            # Also scan one level for L_* / Textures
            if os.path.isdir(map_dir):
                try:
                    for child in os.listdir(map_dir):
                        child_path = os.path.join(map_dir, child)
                        if not os.path.isdir(child_path):
                            continue
                        if child.lower().startswith("l_") or child.lower() == "textures":
                            _add(child_path)
                            _add(os.path.join(child_path, "Textures"))
                except OSError:
                    pass

    _add(_GROUND_MAP_REFS_DIR)
    return roots



def resolve_ingame_map_texture(
    map_name: str = "",
    hint: str = "",
) -> tuple[str, str]:
    """Find T_InGameMap_* PNG for the map. Returns ``(path, reason)`` or ``("", "")``."""
    hint = (hint or "").strip()
    if hint:
        try:
            hint = bpy.path.abspath(hint)
        except Exception:
            pass
        if os.path.isfile(hint):
            return hint, "manual"

    stems = _map_name_stems(map_name)
    preferred: list[str] = []
    for stem in stems:
        preferred.append(f"T_InGameMap_{stem}")
        # Spaceport_01 → keep; also try without trailing _01 style already in stems
    preferred.append("T_InGameMap")

    best = ("", "", -1.0)
    for folder in _ground_search_roots(map_name):
        for name, fpath in _iter_image_files(folder):
            stem = os.path.splitext(name)[0]
            low = stem.lower()
            if "ingamemap" not in low.replace("_", "").replace("-", ""):
                # Accept exact T_InGameMap_* naming
                if not low.startswith("t_ingamemap"):
                    continue
            # Skip underground / placeholder variants unless that's all we have
            score = 10.0
            if "underground" in low or "placeholder" in low or "bottom" in low:
                score -= 4.0
            for i, pref in enumerate(preferred):
                if low == pref.lower():
                    score += 20.0 - i
                    break
                if low.startswith(pref.lower()):
                    score += 12.0 - i * 0.5
                    break
            for stem_i, stem in enumerate(stems):
                if stem.lower() in low:
                    score += 8.0 - stem_i
            if score > best[2]:
                best = (fpath, f"{os.path.basename(folder)}/{name}", score)

    if best[0]:
        return best[0], best[1]
    return "", ""



def resolve_hlod_color_texture(
    map_name: str = "",
    hint: str = "",
) -> tuple[str, str, list[str]]:
    """Find HLOD / landscape Color texture(s) for palette sampling.

    Returns ``(primary_path, reason, all_tile_paths)``.
    Single ``_Color_xN_yM`` tiles are palette sources (not full-map UV albedos).
    """
    hint = (hint or "").strip()
    if hint:
        try:
            hint = bpy.path.abspath(hint)
        except Exception:
            pass
        if os.path.isfile(hint):
            return hint, "manual", [hint]

    stems = _map_name_stems(map_name)
    tiles: list[tuple[float, str, str]] = []
    full: list[tuple[float, str, str]] = []

    for folder in _ground_search_roots(map_name):
        for name, fpath in _iter_image_files(folder):
            stem = os.path.splitext(name)[0]
            low = stem.lower()
            if "color" not in low:
                continue
            # Prefer T_{Map}_Color* landscape tiles; skip unrelated BackdropMesh city colors
            score = 0.0
            matched_stem = False
            for i, ms in enumerate(stems):
                if ms.lower() in low:
                    score += 15.0 - i
                    matched_stem = True
                    break
            if not matched_stem and stems:
                # Bundled Spaceport fallback without map name match only if folder is refs
                if os.path.normcase(folder) != os.path.normcase(_GROUND_MAP_REFS_DIR):
                    continue
                score += 2.0
            if "hlod" in low:
                score += 3.0
            if low.startswith("t_") and "_color" in low:
                score += 4.0
            if "buriedcity" in low and not any("buried" in s.lower() for s in stems):
                score -= 20.0
            if _HLOD_COLOR_TILE_RE.search(stem):
                score += 5.0
                tiles.append((score, fpath, f"{os.path.basename(folder)}/{name}"))
            elif low.endswith("_color") or "_color_" in low:
                full.append((score, fpath, f"{os.path.basename(folder)}/{name}"))

    tiles.sort(key=lambda t: -t[0])
    full.sort(key=lambda t: -t[0])
    if full and full[0][0] >= 10.0:
        path, reason = full[0][1], full[0][2]
        extras = [t[1] for t in tiles[:8]]
        return path, reason + " (full)", [path] + extras
    if tiles:
        # Prefer a mid tile for diversity when several exist; else first
        paths = [t[1] for t in tiles[:12]]
        pick = tiles[len(tiles) // 2] if len(tiles) > 2 else tiles[0]
        return pick[1], pick[2] + " (tile/palette)", paths
    return "", "", []



def extract_hlod_palette(
    image_paths: str | list[str] | None,
    *,
    max_side: int = 96,
) -> dict[str, tuple[float, float, float]]:
    """Sample dominant cream / rock / pink / dark tones from HLOD Color image(s).

    Uses PIL when available; otherwise returns Spaceport defaults. Tile paths are
    palette-only — spatial UV alignment is not assumed.
    """
    palette = dict(_DEFAULT_HLOD_PALETTE)
    paths: list[str] = []
    if isinstance(image_paths, str) and image_paths:
        paths = [image_paths]
    elif isinstance(image_paths, (list, tuple)):
        paths = [p for p in image_paths if p]

    existing = [p for p in paths if p and os.path.isfile(p)]
    if not existing:
        return palette

    try:
        from PIL import Image
        import colorsys
    except Exception:
        return palette

    cream_s: list[tuple[float, float, float]] = []
    rock_s: list[tuple[float, float, float]] = []
    pink_s: list[tuple[float, float, float]] = []
    dark_s: list[tuple[float, float, float]] = []

    for fpath in existing[:8]:
        try:
            im = Image.open(fpath).convert("RGB")
        except Exception:
            continue
        w, h = im.size
        scale = max(w, h) / float(max(max_side, 16))
        if scale > 1.0:
            im = im.resize(
                (max(1, int(w / scale)), max(1, int(h / scale))),
                getattr(Image, "BILINEAR", 2),
            )
        for r, g, b in im.getdata():
            rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
            hh, ss, vv = colorsys.rgb_to_hsv(rf, gf, bf)
            if vv < 0.38:
                dark_s.append((rf, gf, bf))
                continue
            # Dusty pink / rose sediment
            if ss > 0.06 and rf >= gf and (hh < 0.12 or hh > 0.85 or (bf >= gf * 0.9 and rf > bf)):
                if rf - gf > 0.02 or bf - gf > 0.015:
                    pink_s.append((rf, gf, bf))
                    continue
            if ss < 0.14 and vv < 0.82:
                rock_s.append((rf, gf, bf))
                continue
            if vv >= 0.62:
                cream_s.append((rf, gf, bf))

    if cream_s:
        palette["cream"] = _avg_rgb(cream_s)
    if rock_s:
        palette["rock"] = _avg_rgb(rock_s)
    if pink_s:
        palette["pink"] = _avg_rgb(pink_s)
    if dark_s:
        palette["dark"] = _avg_rgb(dark_s)
    return palette



def _wire_heightmap_map_texturing(
    mat,
    *,
    ingame_img=None,
    palette: dict | None = None,
    mix_strength: float = 0.72,
    structure_darken: float = 0.22,
):
    """Overlay In-Game Map luminance masks + HLOD palette onto landscape sand Base Color.

    Macro relief stays on the Displace modifier. Sand dual-scale normals/roughness
    are left intact; only Base Color is remixed.
    """
    if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return False
    palette = palette or dict(_DEFAULT_HLOD_PALETTE)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    principled = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if principled is None:
        return False

    base_in = principled.inputs.get("Base Color")
    if base_in is None:
        return False

    if base_in.is_linked:
        sand_sock = base_in.links[0].from_socket
    else:
        flat = nodes.new("ShaderNodeRGB")
        flat.label = "Ground Base"
        flat.location = (-200, 200)
        dv = tuple(base_in.default_value)
        flat.outputs[0].default_value = (dv[0], dv[1], dv[2], 1.0)
        sand_sock = flat.outputs[0]

    COL = 200
    tc = nodes.new("ShaderNodeTexCoord")
    tc.label = "Ground Map UV"
    tc.location = (COL - 1600, 700)
    uv_vec = tc.outputs["UV"]

    if ingame_img is not None:
        map_tex = _new_tex_image(
            nodes, ingame_img, "In-Game Map (spatial)", (COL - 1300, 700),
        )
        try:
            map_tex.extension = "CLIP"
        except Exception:
            pass
        links.new(uv_vec, map_tex.inputs["Vector"])
        sep = nodes.new("ShaderNodeSeparateColor")
        sep.label = "Map RGB"
        sep.location = (COL - 1000, 700)
        links.new(map_tex.outputs["Color"], sep.inputs["Color"])
        mul_r = nodes.new("ShaderNodeMath")
        mul_r.operation = "MULTIPLY"
        mul_r.label = "0.30 R"
        mul_r.location = (COL - 780, 820)
        mul_r.inputs[1].default_value = 0.30
        links.new(sep.outputs["Red"], mul_r.inputs[0])
        mul_g = nodes.new("ShaderNodeMath")
        mul_g.operation = "MULTIPLY"
        mul_g.label = "0.59 G"
        mul_g.location = (COL - 780, 700)
        mul_g.inputs[1].default_value = 0.59
        links.new(sep.outputs["Green"], mul_g.inputs[0])
        mul_b = nodes.new("ShaderNodeMath")
        mul_b.operation = "MULTIPLY"
        mul_b.label = "0.11 B"
        mul_b.location = (COL - 780, 580)
        mul_b.inputs[1].default_value = 0.11
        links.new(sep.outputs["Blue"], mul_b.inputs[0])
        add1 = nodes.new("ShaderNodeMath")
        add1.operation = "ADD"
        add1.location = (COL - 560, 780)
        links.new(mul_r.outputs["Value"], add1.inputs[0])
        links.new(mul_g.outputs["Value"], add1.inputs[1])
        add2 = nodes.new("ShaderNodeMath")
        add2.operation = "ADD"
        add2.label = "Map Luminance"
        add2.location = (COL - 380, 740)
        links.new(add1.outputs["Value"], add2.inputs[0])
        links.new(mul_b.outputs["Value"], add2.inputs[1])
        luma_sock = add2.outputs["Value"]
    else:
        val = nodes.new("ShaderNodeValue")
        val.label = "Flat Mid (no InGameMap)"
        val.location = (COL - 380, 740)
        val.outputs[0].default_value = 0.55
        luma_sock = val.outputs[0]

    # Dark map → rock; bright → cream flats; mid band → pink sediment; very dark → structure
    rock_bright = _contrast_mask(
        nodes, links, luma_sock, 0.05, 0.32, (COL - 160, 860), "Rock luma window",
    )
    rock_mask = _invert_mask(
        nodes, links, rock_bright, (COL + 40, 860), "Rock (dark map)",
    )
    cream_mask = _contrast_mask(
        nodes, links, luma_sock, 0.40, 0.88, (COL - 160, 700), "Cream / flats",
    )
    pink_center = _contrast_mask(
        nodes, links, luma_sock, 0.30, 0.55, (COL - 160, 540), "Pink mid",
    )
    pink_edge = _contrast_mask(
        nodes, links, luma_sock, 0.55, 0.75, (COL - 160, 420), "Pink edge",
    )
    pink_mask = _mix_float(
        nodes, links, pink_center, pink_edge, 0.45, (COL + 40, 480), "Pink sediment",
    )
    # Soften pink
    pink_amt = nodes.new("ShaderNodeMath")
    pink_amt.operation = "MULTIPLY"
    pink_amt.label = "Pink ×0.35"
    pink_amt.location = (COL + 220, 480)
    pink_amt.inputs[1].default_value = 0.35
    links.new(pink_mask, pink_amt.inputs[0])
    pink_mask = pink_amt.outputs["Value"]

    struct_bright = _contrast_mask(
        nodes, links, luma_sock, 0.02, 0.16, (COL - 160, 300), "Structure luma",
    )
    struct_mask = _invert_mask(
        nodes, links, struct_bright, (COL + 40, 300), "Structure dark",
    )

    cream_rgb = _rgb_node(
        nodes, palette.get("cream", _DEFAULT_HLOD_PALETTE["cream"]),
        "HLOD Cream", (COL + 40, 980),
    )
    rock_rgb = _rgb_node(
        nodes, palette.get("rock", _DEFAULT_HLOD_PALETTE["rock"]),
        "HLOD Rock", (COL + 40, 780),
    )
    pink_rgb = _rgb_node(
        nodes, palette.get("pink", _DEFAULT_HLOD_PALETTE["pink"]),
        "HLOD Pink", (COL + 40, 620),
    )
    dark_rgb = _rgb_node(
        nodes, palette.get("dark", _DEFAULT_HLOD_PALETTE["dark"]),
        "HLOD Dark", (COL + 40, 360),
    )

    base_pal = _mix_rgba(
        nodes, links, cream_rgb, rock_rgb, rock_mask,
        (COL + 280, 820), "Cream↔Rock",
    )
    # Mild cream pull on bright dunes/cliffs
    cream_pull = nodes.new("ShaderNodeMath")
    cream_pull.operation = "MULTIPLY"
    cream_pull.label = "Cream pull ×0.4"
    cream_pull.location = (COL + 280, 680)
    cream_pull.inputs[1].default_value = 0.40
    links.new(cream_mask, cream_pull.inputs[0])
    base_pal = _mix_rgba(
        nodes, links, base_pal, cream_rgb, cream_pull.outputs["Value"],
        (COL + 500, 780), "Reinforce flats",
    )
    with_pink = _mix_rgba(
        nodes, links, base_pal, pink_rgb, pink_mask,
        (COL + 720, 700), "Pink veins",
    )
    struct_amt = nodes.new("ShaderNodeMath")
    struct_amt.operation = "MULTIPLY"
    struct_amt.label = f"Struct ×{structure_darken:g}"
    struct_amt.location = (COL + 720, 520)
    struct_amt.inputs[1].default_value = float(structure_darken)
    links.new(struct_mask, struct_amt.inputs[0])
    darkened = _mix_rgba(
        nodes, links, with_pink, dark_rgb, struct_amt.outputs["Value"],
        (COL + 960, 620), "Structure silhouette",
    )

    combined = _mix_rgba(
        nodes, links, sand_sock, darkened, float(mix_strength),
        (COL + 1200, 400), "Sand × HLOD palette",
    )
    combined = _mix_rgba(
        nodes, links, combined, sand_sock, 0.28,
        (COL + 1420, 320), "Grain multiply", blend="MULTIPLY",
    )

    _unlink_input(links, base_in)
    links.new(combined, base_in)

    try:
        mat["arc_ground_map_textured"] = 1
        mat["arc_ground_palette_cream"] = list(palette.get("cream", (0, 0, 0)))
        mat["arc_ground_palette_rock"] = list(palette.get("rock", (0, 0, 0)))
        mat["arc_ground_palette_pink"] = list(palette.get("pink", (0, 0, 0)))
        mat["arc_ground_mix_strength"] = float(mix_strength)
    except Exception:
        pass
    return True



def setup_landscape_sand_material(
    mat=None,
    *,
    map_name: str = "",
    macro_meters: float = 12.0,
    micro_meters: float = 1.5,
    ingame_map_path: str = "",
    hlod_color_path: str = "",
    use_map_texturing: bool = True,
):
    """Build / rebuild a dual-scale sand material for CityGroundPlane.

    Macro dune shape must come from displaced heightmap geometry — this shader only
    supplies micro BRDF (albedo breakup + detail normals). Does not use NOR as height.

    Optional In-Game Map + HLOD Color paths drive spatial masks and albedo palette.
    """
    name = f"{(map_name or 'Map').strip() or 'Map'}_LandscapeSand"
    if mat is None:
        mat = bpy.data.materials.get(name)
        if mat is None:
            mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    _stamp_mi_family(mat, FAMILY_SAND)

    dirs = _collect_sand_search_dirs("", "")
    used: set[str] = set()
    maps = {
        "macro_albedo": None,
        "micro_albedo": None,
        "fine_normal": None,
        "macro_normal": None,
        "albedo_param": "",
        "normal_param": "",
        "meta": {},
    }
    for role, stems, key in (
        ("macro_albedo", _SAND_MACRO_ALBEDO_STEMS, "macro_albedo"),
        ("micro_albedo", _SAND_MICRO_ALBEDO_STEMS, "micro_albedo"),
        ("fine_normal", _SAND_FINE_NORMAL_STEMS, "fine_normal"),
        ("macro_normal", _SAND_MACRO_NORMAL_STEMS, "macro_normal"),
    ):
        img, path, reason = _find_sand_stem_image(stems, dirs, used)
        if img is None:
            path2, reason2 = infer_sand_texture(role, "", "", mi_stem="landscape_sand", exclude_paths=used)
            img = _load_fallback_tex_image(path2)
            path, reason = path2, reason2
        if img is not None:
            maps[key] = img
            if path:
                used.add(path)
                maps["meta"][key] = path
                maps["meta"][f"{key}_src"] = reason

    _wire_dual_scale_sand(
        mat,
        maps=maps,
        scalars={},
        colours=[],
        tex_lookup={},
        coord_mode="object",
        macro_meters=macro_meters,
        micro_meters=micro_meters,
        apply_tint=False,
    )
    try:
        mat["arc_landscape_sand"] = 1
        if map_name:
            mat["arc_map"] = map_name
    except Exception:
        pass
    for role in ("macro_albedo", "micro_albedo", "fine_normal", "macro_normal"):
        if role in maps["meta"]:
            _stamp_tex_fallback(mat, role, maps["meta"][role], maps["meta"].get(f"{role}_src", ""))

    if use_map_texturing:
        apply_heightmap_map_texturing(
            mat,
            map_name=map_name or "",
            ingame_map_path=ingame_map_path,
            hlod_color_path=hlod_color_path,
        )
    return mat



def apply_heightmap_map_texturing(
    mat,
    *,
    map_name: str = "",
    ingame_map_path: str = "",
    hlod_color_path: str = "",
) -> dict[str, str]:
    """Resolve refs (if needed) and wire In-Game Map + HLOD palette onto ``mat``.

    Returns a small status dict: ingame, hlod, palette_mode, note.
    """
    status = {
        "ingame": "",
        "hlod": "",
        "palette_mode": "default",
        "note": "",
    }
    if mat is None:
        status["note"] = "no material"
        return status

    ingame_path, ingame_why = resolve_ingame_map_texture(map_name, ingame_map_path)
    hlod_path, hlod_why, hlod_tiles = resolve_hlod_color_texture(map_name, hlod_color_path)

    ingame_img = _load_fallback_tex_image(ingame_path) if ingame_path else None
    sample_paths = list(hlod_tiles) if hlod_tiles else ([hlod_path] if hlod_path else [])
    if hlod_color_path and os.path.isfile(
        bpy.path.abspath(hlod_color_path) if hlod_color_path else ""
    ):
        # Manual pick wins as primary sample; still allow discovered tiles for richness
        manual = bpy.path.abspath(hlod_color_path)
        if manual not in sample_paths:
            sample_paths.insert(0, manual)

    palette = extract_hlod_palette(sample_paths)
    palette_mode = "default"
    if sample_paths:
        tileish = any(_HLOD_COLOR_TILE_RE.search(os.path.splitext(os.path.basename(p))[0])
                      for p in sample_paths)
        palette_mode = "tile_palette" if tileish else "hlod_palette"

    ok = _wire_heightmap_map_texturing(
        mat,
        ingame_img=ingame_img,
        palette=palette,
        mix_strength=0.72 if ingame_img is not None else 0.55,
    )
    try:
        if ingame_path:
            mat["arc_ingame_map_path"] = ingame_path
            mat["arc_ingame_map_src"] = ingame_why
        if hlod_path:
            mat["arc_hlod_color_path"] = hlod_path
            mat["arc_hlod_color_src"] = hlod_why
        mat["arc_hlod_palette_mode"] = palette_mode
        mat["arc_ground_map_textured"] = 1 if ok else 0
    except Exception:
        pass

    status["ingame"] = ingame_path
    status["hlod"] = hlod_path
    status["palette_mode"] = palette_mode
    if ok:
        bits = []
        if ingame_path:
            bits.append(f"map={os.path.basename(ingame_path)}")
        if hlod_path:
            bits.append(f"hlod={os.path.basename(hlod_path)}/{palette_mode}")
        status["note"] = " · ".join(bits) if bits else "palette defaults (no refs found)"
    else:
        status["note"] = "wire failed"
    return status



def _scene_water_shore_settings():
    """Read shore proximity tunables from the active scene (safe defaults)."""
    dist = 2.0
    strength = 1.0
    invert = False
    try:
        scene = bpy.context.scene
        dist = float(getattr(scene, "arc_water_shore_distance", dist) or dist)
        strength = float(getattr(scene, "arc_water_shore_strength", strength) or strength)
        invert = bool(getattr(scene, "arc_water_shore_invert", False))
    except Exception:
        pass
    return max(dist, 0.05), min(max(strength, 0.0), 4.0), invert



def _wire_water_shore_factor(nodes, links, col_mix: float, y: float = 420):
    """Attribute proximity + Cycles AO → Mix Factor (1 = Shore Color).

    * ``arc_shore_proximity`` — vertex float baked by Refresh / Stage 2 (robust on maps).
    * Ambient Occlusion (Inside) — shading-point fallback; strong in Cycles, weak in Eevee.
    """
    shore_dist, shore_strength, shore_invert = _scene_water_shore_settings()

    attr = nodes.new("ShaderNodeAttribute")
    attr.label = "Shore Proximity"
    attr.attribute_name = _WATER_SHORE_ATTR
    try:
        attr.attribute_type = "GEOMETRY"
    except Exception:
        pass
    attr.location = (col_mix - 520, y + 80)

    ao = nodes.new("ShaderNodeAmbientOcclusion")
    ao.label = "Shore AO (Cycles)"
    ao.inside = True
    try:
        ao.only_local = False
    except Exception:
        pass
    try:
        ao.samples = 16
    except Exception:
        pass
    ao.location = (col_mix - 520, y - 120)

    dist_val = nodes.new("ShaderNodeValue")
    dist_val.label = "Shore Dist (m)"
    dist_val.outputs[0].default_value = shore_dist
    dist_val.location = (col_mix - 760, y - 80)
    try:
        links.new(dist_val.outputs[0], ao.inputs["Distance"])
    except Exception:
        pass

    # AO: 1 = open water, 0 = near geometry → invert to shore factor
    ao_inv = nodes.new("ShaderNodeMath")
    ao_inv.operation = "SUBTRACT"
    ao_inv.label = "1−AO"
    ao_inv.inputs[0].default_value = 1.0
    ao_inv.location = (col_mix - 280, y - 100)
    links.new(ao.outputs["AO"], ao_inv.inputs[1])

    # max(attribute, ao_shore)
    mx = nodes.new("ShaderNodeMath")
    mx.operation = "MAXIMUM"
    mx.label = "Prox∨AO"
    mx.location = (col_mix - 80, y)
    links.new(attr.outputs["Fac"], mx.inputs[0])
    links.new(ao_inv.outputs["Value"], mx.inputs[1])

    # Strength scale + clamp
    mul = nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    mul.label = "Shore Strength"
    mul.inputs[1].default_value = shore_strength
    mul.location = (col_mix + 100, y)
    links.new(mx.outputs["Value"], mul.inputs[0])

    clamp = nodes.new("ShaderNodeClamp")
    clamp.location = (col_mix + 280, y)
    links.new(mul.outputs["Value"], clamp.inputs["Value"])

    fac_out = clamp.outputs["Result"]
    if shore_invert:
        inv = nodes.new("ShaderNodeMath")
        inv.operation = "SUBTRACT"
        inv.label = "Invert Shore"
        inv.inputs[0].default_value = 1.0
        inv.location = (col_mix + 460, y)
        links.new(clamp.outputs["Result"], inv.inputs[1])
        fac_out = inv.outputs["Value"]

    return fac_out



def _wire_water_world_bump(nodes, links, principled, col_tex: float, col_mix: float):
    """World-space ridged noise → Bump → Principled Normal (target Shader Editor graph)."""
    geom = nodes.new("ShaderNodeNewGeometry")
    geom.label = "Plane World Pos"
    geom.location = (col_tex - 400, -320)

    mapping = nodes.new("ShaderNodeMapping")
    mapping.label = "World 4m/tile"
    mapping.vector_type = "POINT"
    mapping.inputs["Scale"].default_value = (0.25, 0.25, 0.25)
    mapping.location = (col_tex - 160, -320)
    links.new(geom.outputs["Position"], mapping.inputs["Vector"])

    noise = nodes.new("ShaderNodeTexNoise")
    noise.label = "Ridged Water"
    try:
        noise.noise_dimensions = "3D"
    except Exception:
        pass
    try:
        noise.noise_type = "RIDGED_MULTIFRACTAL"
    except Exception:
        try:
            noise.noise_type = "MULTIFRACTAL"
        except Exception:
            pass
    noise.inputs["Scale"].default_value = 2.0
    try:
        noise.inputs["Detail"].default_value = 0.0
    except Exception:
        pass
    try:
        noise.inputs["Roughness"].default_value = 1.0
    except Exception:
        pass
    try:
        noise.inputs["Lacunarity"].default_value = 74.0
    except Exception:
        pass
    try:
        noise.inputs["Distortion"].default_value = 1.3
    except Exception:
        pass
    noise.location = (col_mix - 200, -320)
    links.new(mapping.outputs["Vector"], noise.inputs["Vector"])

    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.5
    try:
        bump.inputs["Distance"].default_value = 0.1
    except Exception:
        pass
    bump.location = (col_mix + 80, -280)
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], principled.inputs["Normal"])



def _setup_water_material(mat, mi_path: str, psk_path: str = ""):
    """Water / ocean / lagoon / river — Water↔Shore + ridged bump + proximity/AO factor."""
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_WATER)
    try:
        mat["arc_water_setup"] = _WATER_SETUP_V
    except Exception:
        pass

    COL_TEX, COL_MIX, COL_BSDF, COL_OUT = -1200, -200, 400, 700
    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []
    log = utils.get_logger()

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    water = _mi_colour(colours, *_WATER_COLOR_KEYS, default=None)
    shallow = _mi_colour(colours, *_WATER_SHALLOW_KEYS, default=None)
    deep = _mi_colour(colours, *_WATER_DEEP_KEYS, default=None)
    shore = _mi_colour(colours, *_WATER_SHORE_KEYS, default=None)

    # Resolve Water Color / Shore Color for the target Mix graph.
    # RockyCreek-style Deep+Shallow maps onto Water↔Shore when Shore/Water keys absent.
    if water is not None:
        water_col = water
    elif deep is not None:
        water_col = deep
    elif shallow is not None and shore is None:
        water_col = shallow
    else:
        water_col = _DEFAULT_WATER_COLOR
        if water is None and deep is None and shallow is None and shore is None:
            log.warning(
                "Water MI '%s' has no Water/Shore colour params — using default teal/sand",
                os.path.basename(mi_path),
            )

    if shore is not None:
        shore_col = shore
    elif shallow is not None and (water is not None or deep is not None):
        shore_col = shallow
    else:
        shore_col = _DEFAULT_SHORE_COLOR

    w_rgb = nodes.new("ShaderNodeRGB")
    w_rgb.label = "Water Color"
    w_rgb.outputs[0].default_value = water_col
    w_rgb.location = (COL_TEX, 280)

    s_rgb = nodes.new("ShaderNodeRGB")
    s_rgb.label = "Shore Color"
    s_rgb.outputs[0].default_value = shore_col
    s_rgb.location = (COL_TEX, 80)

    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MIX"
    mix.label = "Water↔Shore"
    mix.location = (COL_MIX, 200)
    try:
        mix.clamp_factor = True
    except Exception:
        pass
    mix.inputs["Factor"].default_value = 0.0  # open water until proximity/AO drives shore
    links.new(w_rgb.outputs[0], mix.inputs[6])
    links.new(s_rgb.outputs[0], mix.inputs[7])
    links.new(mix.outputs[2], principled.inputs["Base Color"])

    fac_sock = _wire_water_shore_factor(nodes, links, COL_MIX, y=420)
    links.new(fac_sock, mix.inputs["Factor"])

    # Stamp authored colours on the datablock for debugging / later shore blends
    try:
        mat["arc_water_color"] = list(water_col)
        if shallow is not None:
            mat["arc_water_color_shallow"] = list(shallow)
        if deep is not None:
            mat["arc_water_color_deep"] = list(deep)
        mat["arc_shore_color"] = list(shore_col)
    except Exception:
        pass

    # Target Principled: glossy translucent water (opaque rivers stay denser).
    opaque_river = (
        "opaque" in os.path.basename(mi_path or "").lower()
        or (not _is_translucent_blend(mi) and not mi.get("is_translucent"))
    )
    clarity = max(float(_mi_scalar(scalars, "3. Water Clarity", "Water Clarity", default=1.0)), 0.0)
    if opaque_river and (deep is not None or water is not None):
        transmission = min(0.25 + 0.15 * min(clarity, 2.0), 0.55)
        alpha = 0.85
    else:
        transmission = 1.0
        alpha = 0.45
    alpha_mode = "BLEND"

    principled.inputs["Metallic"].default_value = 0.0
    principled.inputs["Roughness"].default_value = 0.0
    try:
        principled.inputs["Transmission Weight"].default_value = transmission
    except Exception:
        try:
            principled.inputs["Transmission"].default_value = transmission
        except Exception:
            pass
    principled.inputs["Alpha"].default_value = alpha
    try:
        principled.inputs["IOR"].default_value = 1.333
    except Exception:
        pass
    try:
        principled.inputs["Specular IOR Level"].default_value = 0.5
    except Exception:
        pass

    _wire_water_world_bump(nodes, links, principled, COL_TEX, COL_MIX)

    # Leave authored NR textures as inspection nodes (procedural bump is the live normal).
    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    _set_material_alpha_mode(mat, mode=alpha_mode, two_sided=False)

    handled = {
        node.image.filepath
        for node in nodes
        if getattr(node, "image", None) is not None
    }
    _dump_unconnected_tex(nodes, tex_lookup, handled, COL_TEX - 500, -700)



# ---------------------------------------------------------------------------
# Water shore proximity (vertex attribute → shader Attribute node)
# ---------------------------------------------------------------------------

def object_has_water_material(obj) -> bool:
    """True when any material slot is stamped water family."""
    if obj is None or getattr(obj, "type", "") != "MESH":
        return False
    for slot in obj.material_slots or []:
        mat = slot.material
        if mat is not None and str(mat.get("arc_mi_family") or "") == FAMILY_WATER:
            return True
    return False



def _shore_proximity_exclude_object(obj) -> bool:
    """Skip water / glass / decal / helper / non-mesh as proximity targets."""
    if obj is None or getattr(obj, "type", "") != "MESH":
        return True
    if obj.get("arc_plane_mesh") and object_has_water_material(obj):
        return True
    if object_has_water_material(obj):
        return True
    group = str(obj.get("arc_map_group") or "").lower()
    if group in ("helpers", "light", "sky", "debris"):
        return True
    name_l = (obj.name or "").lower()
    if any(t in name_l for t in ("helper", "occluder", "skybox", "skydome", "lightcard")):
        return True
    # Pure glass / decal cards — exclude; mixed SMA with glass slots still count via other mats
    water_glass_decal_only = True
    any_mat = False
    for slot in obj.material_slots or []:
        mat = slot.material
        if mat is None:
            continue
        any_mat = True
        fam = str(mat.get("arc_mi_family") or "")
        if fam not in (FAMILY_GLASS, FAMILY_DECAL, FAMILY_WATER, ""):
            water_glass_decal_only = False
            break
        if fam == "":
            # Unstamped opaque mesh — keep as target
            water_glass_decal_only = False
            break
    if any_mat and water_glass_decal_only:
        # All slots glass/decal/water
        fams = {
            str(slot.material.get("arc_mi_family") or "")
            for slot in obj.material_slots
            if slot.material is not None
        }
        if fams and fams <= {FAMILY_GLASS, FAMILY_DECAL, FAMILY_WATER}:
            return True
    return False



def _shore_proximity_include_object(obj, target_coll=None) -> bool:
    """Prefer heightmap + StaticMeshActors; optional explicit collection override."""
    if _shore_proximity_exclude_object(obj):
        return False
    if target_coll is not None:
        try:
            return obj.name in target_coll.objects
        except Exception:
            return False
    if obj.get("arc_heightmap_plane") or obj.get("arc_heightmap"):
        return True
    group = str(obj.get("arc_map_group") or "")
    if group == "StaticMeshActors":
        return True
    name_l = (obj.name or "").lower()
    if name_l.startswith("staticmeshactor") or "cityground" in name_l or "heightmap" in name_l:
        return True
    # SMA collection membership (group stamp may be missing before Group Collections)
    try:
        for coll in obj.users_collection:
            cname = (coll.name or "").lower()
            if cname.endswith("_staticmeshactors") or cname == "staticmeshactors":
                return True
    except Exception:
        pass
    return False



def _ensure_water_mesh_density(obj, max_edge_m: float = 1.25, max_cuts: int = 4) -> bool:
    """Subdivide sparse unique water planes so shore proximity has mid-face samples."""
    import bmesh

    mesh = obj.data
    if mesh is None or len(mesh.vertices) >= 200:
        return False
    if mesh.users > 1:
        # Don't densify shared datablocks — Stage 1 unique planes should be users==1
        try:
            obj.data = mesh.copy()
            mesh = obj.data
        except Exception:
            return False
    try:
        dims = obj.dimensions
        span = max(float(dims.x), float(dims.y), 0.01)
    except Exception:
        span = 32.0
    if span < max_edge_m * 2:
        return False
    # cuts needed so edge ≈ max_edge_m
    cuts = 0
    edge = span
    while edge > max_edge_m and cuts < max_cuts:
        cuts += 1
        edge *= 0.5
    if cuts <= 0:
        return False
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        for _ in range(cuts):
            bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1)
        bm.to_mesh(mesh)
        mesh.update()
    finally:
        bm.free()
    return True



def _build_shore_target_bvhs(objects, depsgraph):
    """Build world-space BVH trees for proximity targets."""
    from mathutils.bvhtree import BVHTree
    import bmesh

    trees = []
    for obj in objects:
        if obj is None or obj.type != "MESH" or obj.data is None:
            continue
        try:
            if obj.hide_get() and obj.hide_render:
                continue
        except Exception:
            pass
        bm = bmesh.new()
        try:
            bm.from_object(obj, depsgraph)
            bm.transform(obj.matrix_world)
            bmesh.ops.triangulate(bm, faces=bm.faces[:])
            if not bm.faces:
                continue
            tree = BVHTree.FromBMesh(bm, epsilon=0.0)
            trees.append(tree)
        except Exception:
            continue
        finally:
            bm.free()
    return trees



def _closest_shore_distance(co_world, trees):
    best = None
    for tree in trees:
        try:
            loc, _normal, _idx, dist = tree.find_nearest(co_world)
        except Exception:
            continue
        if loc is None:
            continue
        d = float(dist)
        if best is None or d < best:
            best = d
    return best



def refresh_water_shore_proximity(context=None, objects=None, densify: bool = True) -> dict:
    """Bake ``arc_shore_proximity`` (0 open … 1 shore) onto water mesh vertices.

    Measures distance to nearby opaque meshes (heightmap, StaticMeshActors, etc.).
    Call after Stage 2 / Fix White, or via ``arc_outfits.refresh_water_shore_proximity``.
    Not per-frame — static map bake.
    """
    context = context or bpy.context
    scene = context.scene
    shore_dist, _strength, invert = _scene_water_shore_settings()
    target_coll = None
    try:
        ptr = getattr(scene, "arc_water_shore_target_collection", None)
        if ptr is not None:
            target_coll = ptr
    except Exception:
        pass

    if objects is None:
        candidates = list(bpy.data.objects)
    else:
        candidates = list(objects)

    water_objs = [o for o in candidates if object_has_water_material(o)]
    if not water_objs:
        # Also scan full scene when callers pass Stage 2 targets that may miss SRC
        water_objs = [o for o in bpy.data.objects if object_has_water_material(o)]

    target_objs = [
        o for o in bpy.data.objects
        if _shore_proximity_include_object(o, target_coll=target_coll)
    ]
    # Never measure a water plane against itself
    water_set = set(water_objs)
    target_objs = [o for o in target_objs if o not in water_set]

    stats = {
        "water": 0,
        "targets": len(target_objs),
        "verts": 0,
        "densified": 0,
        "skipped": 0,
    }
    if not water_objs or not target_objs:
        return stats

    depsgraph = context.evaluated_depsgraph_get()
    trees = _build_shore_target_bvhs(target_objs, depsgraph)
    if not trees:
        return stats

    for obj in water_objs:
        mesh = obj.data
        if mesh is None:
            stats["skipped"] += 1
            continue
        if densify:
            try:
                if _ensure_water_mesh_density(obj):
                    stats["densified"] += 1
                    mesh = obj.data
            except Exception:
                pass
        attr = mesh.attributes.get(_WATER_SHORE_ATTR)
        if attr is None:
            attr = mesh.attributes.new(_WATER_SHORE_ATTR, "FLOAT", "POINT")
        elif attr.domain != "POINT" or attr.data_type != "FLOAT":
            try:
                mesh.attributes.remove(attr)
            except Exception:
                pass
            attr = mesh.attributes.new(_WATER_SHORE_ATTR, "FLOAT", "POINT")

        mw = obj.matrix_world
        n = len(mesh.vertices)
        values = [0.0] * n
        for i, v in enumerate(mesh.vertices):
            co = mw @ v.co
            d = _closest_shore_distance(co, trees)
            if d is None:
                fac = 0.0
            else:
                fac = 1.0 - min(max(d / shore_dist, 0.0), 1.0)
            if invert:
                fac = 1.0 - fac
            values[i] = fac
        try:
            attr.data.foreach_set("value", values)
        except Exception:
            for i, fac in enumerate(values):
                attr.data[i].value = fac
        mesh.update()
        try:
            obj["arc_shore_proximity_baked"] = 1
            obj["arc_shore_proximity_distance"] = shore_dist
        except Exception:
            pass
        stats["water"] += 1
        stats["verts"] += n

    return stats



def _setup_glass_env_material(mat, mi_path: str, psk_path: str = ""):
    """Window / pane glass — transmission stub (not clothing visor ColorA/B)."""
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_GLASS)

    COL_TEX, COL_MIX, COL_BSDF, COL_OUT = -1200, -200, 400, 700
    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    _, color_img = _find_env_tex(
        tex_lookup,
        "C", "CR", "BaseColor", "PM_Diffuse", "CA",
        "T_BrokenGlassOverlay_01_NC", "Overlay",
    )
    if not color_img:
        # BrokenGlassSDF / pane overlays often only expose NC / shard atlas stems
        for param, (_fp, img) in tex_lookup.items():
            stem = os.path.splitext(os.path.basename(_fp))[0].lower()
            if stem.endswith("_nc") or "overlay" in stem or "shard" in stem:
                if "noise" in stem or "engine" in stem:
                    continue
                color_img = img
                break
    _, normal_img = _find_env_tex(
        tex_lookup,
        "NXX", "NormalMap", "Normals", "Normal", "PM_Normals", "NXX/NMX Texture",
        "T_BrokenGlassSDF_NDD",
    )
    if not normal_img:
        for param, (_fp, img) in tex_lookup.items():
            stem = os.path.splitext(os.path.basename(_fp))[0].lower()
            if stem.endswith("_ndd") or stem.endswith("_n") or "sdf" in stem:
                if "noise" in stem:
                    continue
                normal_img = img
                break
    _, mask_img = _find_env_tex(tex_lookup, "MaskTexture", "Mask", "PM_SpecularMasks")

    if color_img:
        c_node = _new_tex_image(nodes, color_img, "Glass Color", (COL_TEX, 300))
        links.new(c_node.outputs["Color"], principled.inputs["Base Color"])
    else:
        col = _mi_colour(colours, "Color", "Tint", "Glass Color", default=(0.75, 0.85, 0.9, 1.0))
        principled.inputs["Base Color"].default_value = col

    if normal_img:
        n_node = _new_tex_image(
            nodes, normal_img, "Glass Normal", (COL_TEX, 0), non_color=True,
        )
        n_sock = _wire_normal_map(
            nodes, links, n_node.outputs["Color"], (COL_MIX, 0), strength=0.5,
            label="Glass Normal",
        )
        links.new(n_sock, principled.inputs["Normal"])

    if mask_img:
        m_node = _new_tex_image(
            nodes, mask_img, "Glass Mask", (COL_TEX, -350), non_color=True,
        )
        # Mask often dirt/opacity — feed alpha softly
        links.new(m_node.outputs["Color"], principled.inputs["Alpha"])

    rough = _mi_scalar(scalars, "Glass Roughness", "Roughness", default=0.05)
    principled.inputs["Roughness"].default_value = min(max(rough, 0.0), 0.5)
    try:
        principled.inputs["Transmission Weight"].default_value = 0.92
    except Exception:
        try:
            principled.inputs["Transmission"].default_value = 0.92
        except Exception:
            pass
    try:
        principled.inputs["IOR"].default_value = 1.45
    except Exception:
        pass
    metallic = _mi_scalar(scalars, "Interior Metallic", "Metallic High", default=0.0)
    if metallic > 0.01:
        principled.inputs["Metallic"].default_value = min(metallic, 0.4)

    translucent = _is_translucent_blend(mi) or bool(
        _mi_tex_params(mi) & {"CubemapInside", "CubemapOutside"}
    )
    if translucent and not mask_img:
        principled.inputs["Alpha"].default_value = 0.35
        _set_material_alpha_mode(mat, mode="BLEND", two_sided=True)
    elif mask_img or _is_masked_blend(mi):
        _set_material_alpha_mode(
            mat, mode="CLIP", threshold=float(mi.get("opacity_clip") or 0.3333), two_sided=True,
        )
    else:
        _set_material_alpha_mode(mat, mode="BLEND", two_sided=bool(mi.get("two_sided")))

    # Cubemaps left unconnected for inspection (env reflection stub)
    handled = {
        node.image.filepath
        for node in nodes
        if getattr(node, "image", None) is not None
    }
    _dump_unconnected_tex(nodes, tex_lookup, handled, COL_TEX - 500, -700)



def _setup_simple_material(mat, mi_path: str, psk_path: str = ""):
    """Best-effort Principled for unknown / BaseColor+Normal MIs."""
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_SIMPLE)

    log = utils.get_logger()
    params = sorted(_mi_tex_params(mi))
    log.info(
        "Simple/fallback MI setup for '%s' params=%s",
        os.path.basename(mi_path), params,
    )

    COL_TEX, COL_MIX, COL_BSDF, COL_OUT = -1200, -200, 400, 700
    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    # Procedural / null MIs (elevator cables): colour parameters only.
    cable1 = _mi_colour(colours, "Color_Cable1", default=None)
    cable2 = _mi_colour(colours, "Color_Cable2", default=None)
    if mi.get("is_null") and (cable1 is not None or cable2 is not None):
        c = cable1 or cable2 or (0.37, 0.37, 0.37, 1.0)
        principled.inputs["Base Color"].default_value = (
            float(c[0]), float(c[1]), float(c[2]), 1.0,
        )
        rough = _mi_scalar(scalars, "Roughness_Cable2", "Roughness", default=0.65)
        metal = _mi_scalar(scalars, "Metal_Cable2", "Metallic", default=0.0)
        principled.inputs["Roughness"].default_value = min(max(float(rough), 0.0), 1.0)
        principled.inputs["Metallic"].default_value = min(max(float(metal), 0.0), 1.0)
        _set_material_alpha_mode(mat, mode="OPAQUE", threshold=1.0, two_sided=True)
        try:
            mat["arc_simple_setup"] = "v2"
        except Exception:
            pass
        return

    local_folders = _character_layout_search_folders(
        mi_path, psk_path if psk_path else "",
    )
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    _, albedo_img = _find_env_tex(tex_lookup, *_SIMPLE_ALBEDO_KEYS)
    _, normal_img = _find_env_tex(tex_lookup, *_SIMPLE_NORMAL_KEYS)
    _, rm_img = _find_env_tex(tex_lookup, *_SIMPLE_ROUGHNESS_METAL_KEYS)
    _, tintmask_img = _find_env_tex(tex_lookup, *_SIMPLE_TINTMASK_KEYS)

    # Tree trunks that fall through to simple with missing maps — infer vegetation textures
    stem_l = os.path.splitext(os.path.basename(mi_path or ""))[0].lower()
    clearly_trunk = "trunk" in stem_l or "bark" in stem_l
    if (clearly_trunk or (
        any(k in stem_l for k in ("pine", "cypress", "juniper", "aleppo", "stonepine"))
        and "billboard" not in stem_l
        and "branch" not in stem_l
        and "leaf" not in stem_l
    )) and (albedo_img is None or normal_img is None):
        used = {fp for _p, (fp, _im) in tex_lookup.items() if fp}
        if albedo_img is None:
            path, reason = infer_vegetation_texture(
                "albedo", mi_path, psk_path, mi_stem=stem_l, exclude_paths=used,
            )
            img = _load_fallback_tex_image(path)
            if img is not None:
                albedo_img = img
                _stamp_tex_fallback(mat, "albedo", path, reason)
                used.add(path)
        if normal_img is None:
            path, reason = infer_vegetation_texture(
                "normal", mi_path, psk_path, mi_stem=stem_l, exclude_paths=used,
            )
            img = _load_fallback_tex_image(path)
            if img is not None:
                normal_img = img
                _stamp_tex_fallback(mat, "normal", path, reason)

    switches = mi.get("switches") or {}
    use_color_scheme = bool(
        switches.get("bUseColorScheme")
        or switches.get("UseColorScheme")
        or _mi_colour(colours, "ColorA", default=None)
    )
    color_a = _mi_colour(colours, "ColorA", default=None)
    color_b = _mi_colour(colours, "ColorB", default=None)
    color_c = _mi_colour(colours, "ColorC", default=None)

    albedo_param = ""
    a_node = None
    if albedo_img:
        albedo_param = next((p for p, (_fp, im) in tex_lookup.items() if im == albedo_img), "")
        # Probe opacity usefulness BEFORE neutralizing dead alpha (NONE rewrites A→1).
        opacity_clip = float(mi.get("opacity_clip") or 0.3333)
        alpha_ok = _image_alpha_usable(albedo_img, clip=opacity_clip)
        # Dead alpha (FoxHat default BaseColor): keep RGB on Color, never a black stub.
        if not alpha_ok:
            _fix_unusable_image_alpha(albedo_img)
        a_node = _new_tex_image(nodes, albedo_img, f"Albedo ({albedo_param})", (COL_TEX, 300))
        # CR alpha → roughness; CA/BaseColor/Color alpha → opacity when masked
        pl = albedo_param.lower()
        stem = os.path.splitext(os.path.basename(
            next((fp for _p, (fp, im) in tex_lookup.items() if im == albedo_img), "")
        ))[0].lower()
        if stem.endswith("_cr") or pl in ("cr", "cr texture", "bc"):
            links.new(a_node.outputs["Alpha"], principled.inputs["Roughness"])
        elif (
            stem.endswith(("_ca", "_color", "_c"))
            or pl in ("ca", "1. ca", "basecolor", "coloralpha", "color", "pm_diffuse")
        ):
            # FoxHat default BaseColor exports with alpha=0 everywhere — wiring CLIP
            # would hide the mesh. Only mask when alpha actually carries coverage.
            if _is_masked_blend(mi) or mi.get("two_sided"):
                if alpha_ok:
                    links.new(a_node.outputs["Alpha"], principled.inputs["Alpha"])
                    _set_material_alpha_mode(
                        mat,
                        mode="CLIP",
                        threshold=opacity_clip,
                        two_sided=bool(mi.get("two_sided")),
                    )
                else:
                    _set_material_alpha_mode(
                        mat, mode="OPAQUE", two_sided=bool(mi.get("two_sided")),
                    )

        # Shared SimplePBR colourways (PonchoFringe etc.): BaseColor RGB is the
        # ColorMask — ColorA/B/C via ColorMask_XYZ when available (matches authored
        # fringe graphs). Roughness/Metallic stay on Principled as MI scalars.
        if use_color_scheme and color_a and (color_b or color_c):
            ca = nodes.new("ShaderNodeRGB")
            ca.label = "ColorA"
            ca.location = (COL_MIX - 280, 520)
            ca.outputs[0].default_value = color_a
            cb = nodes.new("ShaderNodeRGB")
            cb.label = "ColorB"
            cb.location = (COL_MIX - 280, 400)
            cb.outputs[0].default_value = color_b or color_a
            cc = nodes.new("ShaderNodeRGB")
            cc.label = "ColorC"
            cc.location = (COL_MIX - 280, 280)
            cc.outputs[0].default_value = color_c or color_b or color_a
            cm_ok = utils.ensure_colormask_node_group()
            cm_ng = utils.find_node_group(utils._COLORMASK_GROUP) if cm_ok else None
            if cm_ng is not None:
                cm = nodes.new("ShaderNodeGroup")
                cm.node_tree = cm_ng
                cm.label = "ColorMask_XYZ"
                cm.location = (COL_MIX, 360)
                if "ColorMask" in cm.inputs:
                    links.new(a_node.outputs["Color"], cm.inputs["ColorMask"])
                elif cm.inputs:
                    links.new(a_node.outputs["Color"], cm.inputs[0])
                if "X_Green" in cm.inputs:
                    links.new(ca.outputs[0], cm.inputs["X_Green"])
                if "Y_Blue" in cm.inputs:
                    links.new(cb.outputs[0], cm.inputs["Y_Blue"])
                if "Z_Pink" in cm.inputs:
                    links.new(cc.outputs[0], cm.inputs["Z_Pink"])
                # Mask_Roughness / Mask_Metal are zone-ID paths for clothing —
                # SimplePBR uses uniform MI scalars on Principled instead.
                out_col = cm.outputs.get("Mask_Color") or (
                    cm.outputs[0] if cm.outputs else None
                )
                if out_col is not None:
                    links.new(out_col, principled.inputs["Base Color"])
            else:
                # Fallback when ArcTexturer.blend ColorMask_XYZ is unavailable.
                sep = nodes.new("ShaderNodeSeparateColor")
                sep.label = "ColorScheme Mask"
                sep.location = (COL_MIX - 200, 300)
                links.new(a_node.outputs["Color"], sep.inputs["Color"])
                mul_a = nodes.new("ShaderNodeMix")
                mul_a.data_type = "RGBA"
                mul_a.blend_type = "MULTIPLY"
                mul_a.inputs["Factor"].default_value = 1.0
                mul_a.location = (COL_MIX, 520)
                links.new(ca.outputs[0], mul_a.inputs[6])
                links.new(sep.outputs["Red"], mul_a.inputs[7])
                mul_b = nodes.new("ShaderNodeMix")
                mul_b.data_type = "RGBA"
                mul_b.blend_type = "MULTIPLY"
                mul_b.inputs["Factor"].default_value = 1.0
                mul_b.location = (COL_MIX, 400)
                links.new(cb.outputs[0], mul_b.inputs[6])
                links.new(sep.outputs["Green"], mul_b.inputs[7])
                mul_c = nodes.new("ShaderNodeMix")
                mul_c.data_type = "RGBA"
                mul_c.blend_type = "MULTIPLY"
                mul_c.inputs["Factor"].default_value = 1.0
                mul_c.location = (COL_MIX, 280)
                links.new(cc.outputs[0], mul_c.inputs[6])
                links.new(sep.outputs["Blue"], mul_c.inputs[7])
                add_ab = nodes.new("ShaderNodeMix")
                add_ab.data_type = "RGBA"
                add_ab.blend_type = "ADD"
                add_ab.inputs["Factor"].default_value = 1.0
                add_ab.location = (COL_MIX + 180, 460)
                links.new(mul_a.outputs[2], add_ab.inputs[6])
                links.new(mul_b.outputs[2], add_ab.inputs[7])
                add_abc = nodes.new("ShaderNodeMix")
                add_abc.data_type = "RGBA"
                add_abc.blend_type = "ADD"
                add_abc.inputs["Factor"].default_value = 1.0
                add_abc.label = "ColorScheme A+B+C"
                add_abc.location = (COL_MIX + 360, 360)
                links.new(add_ab.outputs[2], add_abc.inputs[6])
                links.new(mul_c.outputs[2], add_abc.inputs[7])
                links.new(add_abc.outputs[2], principled.inputs["Base Color"])
            try:
                mat["arc_color_scheme"] = 1
            except Exception:
                pass
        else:
            links.new(a_node.outputs["Color"], principled.inputs["Base Color"])
    else:
        tint = _mi_colour(
            colours, "ColorA", "Tint", "Color", "BaseColor",
            default=(0.45, 0.45, 0.45, 1.0),
        )
        if tint:
            principled.inputs["Base Color"].default_value = tint
        log.warning("Simple MI '%s' has no resolvable albedo texture", os.path.basename(mi_path))

    if normal_img:
        _fix_unusable_image_alpha(normal_img)
        n_param = next((p for p, (_fp, im) in tex_lookup.items() if im == normal_img), "")
        n_node = _new_tex_image(
            nodes, normal_img, f"Normal ({n_param})", (COL_TEX, -50), non_color=True,
        )
        n_str = min(max(_mi_scalar(scalars, "Normal Strength", default=1.0), 0.0), 2.5)
        n_sock = _wire_normal_map(
            nodes, links, n_node.outputs["Color"], (COL_MIX, -50), strength=n_str,
        )
        links.new(n_sock, principled.inputs["Normal"])
        stem = os.path.splitext(os.path.basename(
            next((fp for _p, (fp, im) in tex_lookup.items() if im == normal_img), "")
        ))[0].lower()
        npl = (n_param or "").lower()
        # NXM/NMX/NOM may pack metallic in Alpha. NMR / PM_SpecularMasks do not —
        # fringe etc. author Metallic/Roughness as MI scalars on Principled.
        authored_metal = any(
            k in scalars and scalars[k] is not None
            for k in ("Metallic", "3. Metallic")
        )
        pack_metal_a = any(stem.endswith(s) for s in ("_nxm", "_nmx", "_nom")) or npl in (
            "nxm", "nmx", "nom",
        )
        if (
            pack_metal_a
            and not authored_metal
            and "Alpha" in n_node.outputs
            and not principled.inputs["Metallic"].links
        ):
            links.new(n_node.outputs["Alpha"], principled.inputs["Metallic"])

    if rm_img is not None:
        # Hero RoughnessMetal / SimplePBR RoughnessMetallicSpecular:
        # R → Roughness, G → Metallic (B often Specular / unused).
        # FoxHat RMS also ships alpha=0 — neutralize so R/G aren't premul-black.
        _fix_unusable_image_alpha(rm_img)
        rm_label = "RoughnessMetallicSpecular" if any(
            k.lower() == "roughnessmetallicspecular"
            for k in _mi_tex_params(mi)
        ) else "RoughnessMetal"
        rm_node = _new_tex_image(
            nodes, rm_img, rm_label, (COL_TEX, -350), non_color=True,
        )
        sep = nodes.new("ShaderNodeSeparateColor")
        sep.label = "Rough / Metal"
        sep.location = (COL_MIX - 80, -350)
        links.new(rm_node.outputs["Color"], sep.inputs["Color"])
        rough_mul = _mi_scalar(
            scalars, "RoughnessMultiplier", "Roughness Multiplier", default=1.0,
        )
        if not principled.inputs["Roughness"].links:
            if abs(rough_mul - 1.0) > 1e-4:
                mul = nodes.new("ShaderNodeMath")
                mul.operation = "MULTIPLY"
                mul.label = "RoughnessMultiplier"
                mul.location = (COL_MIX + 80, -350)
                mul.inputs[1].default_value = max(0.0, min(2.0, rough_mul))
                links.new(sep.outputs["Red"], mul.inputs[0])
                links.new(mul.outputs[0], principled.inputs["Roughness"])
            else:
                links.new(sep.outputs["Red"], principled.inputs["Roughness"])
        if not principled.inputs["Metallic"].links:
            links.new(sep.outputs["Green"], principled.inputs["Metallic"])

    if tintmask_img is not None:
        # Colourway tint mask — keep as inspectable Image when no tint vectors present.
        _new_tex_image(
            nodes, tintmask_img, "TintMask", (COL_TEX - 280, -650), non_color=True,
        )

    # SimplePBR / fringe: put MI Roughness + Metallic directly on Principled BSDF
    # (not via ColorMask_XYZ Mask_Roughness/Metal or NMR Alpha).
    if not principled.inputs["Roughness"].links:
        base_r = _mi_scalar(scalars, "Roughness", "3. Roughness", default=0.55)
        rough_mul = _mi_scalar(
            scalars, "RoughnessMultiplier", "Roughness Multiplier", default=1.0,
        )
        principled.inputs["Roughness"].default_value = max(
            0.0, min(1.0, base_r * rough_mul),
        )
    if not principled.inputs["Metallic"].links:
        principled.inputs["Metallic"].default_value = _mi_scalar(
            scalars, "Metallic", "3. Metallic", default=0.0,
        )
    # FoxHat-style SimplePBR: SpecularMultiplier is an authored scalar (0–1-ish).
    if "SpecularMultiplier" in scalars or "Specular Multiplier" in scalars:
        spec_mul = _mi_scalar(
            scalars, "SpecularMultiplier", "Specular Multiplier", default=0.5,
        )
        for sock_name in ("Specular IOR Level", "Specular"):
            if sock_name in principled.inputs and not principled.inputs[sock_name].links:
                principled.inputs[sock_name].default_value = max(
                    0.0, min(1.0, spec_mul),
                )
                break

    handled = {
        node.image.filepath
        for node in nodes
        if getattr(node, "image", None) is not None
    }
    _dump_unconnected_tex(nodes, tex_lookup, handled, COL_TEX - 500, -500)



def _map_decal_mask_from_alpha(mi: dict, mask_fpath: str = "") -> bool:
    """Choose Alpha vs Color for opacity. Branding ``*_X`` masks store the logo in A."""
    switches = mi.get("switches") or {}
    flagged = _mi_switch(switches, "UseAlphaForMask", default=None)
    if flagged is True:
        return True
    if flagged is False:
        return False
    stem = os.path.splitext(os.path.basename(mask_fpath or ""))[0].lower()
    return stem.endswith("_x")



def _graphic_atlas_uv_layer_name(use_uv1: bool, mesh=None) -> str | None:
    """Pick the Blender UV layer name for GraphicAtlas sampling.

    Authored ``Use UV1`` means UE TexCoord[1]. After
    :func:`utils.normalize_ue_uv_layer_names` that is ``UV1``. Fall back through
    PSK's ``EXTRAUV0`` and index-1 for meshes that were not renormalized yet.
    Returns None to use the active UV (TexCoord node).
    """
    if not use_uv1:
        return None
    # Prefer UEFormat / normalized names; PSK EXTRAUV0 is TexCoord1 pre-normalize.
    candidates = ("UV1", "EXTRAUV0", "UVMap.001")
    if mesh is not None:
        names = [uv.name for uv in (getattr(mesh, "uv_layers", None) or [])]
        for name in candidates:
            if name in names:
                return name
        if len(names) > 1:
            return names[1]
        # Single UV channel (some poster cards): Use UV1 still authored, but only UV0 exists.
        return names[0] if names else None
    return "UV1"



def _graphic_atlas_uv_vector(nodes, links, mi: dict, loc, use_uv1: bool = False, mesh=None):
    """AtlasPosition cell + GraphicTiling for company branding poster sheets."""
    scalars = mi.get("scalars") or {}
    cols = max(float(_mi_scalar(scalars, "Number of columns", default=1.0) or 1.0), 1.0)
    rows = max(float(_mi_scalar(scalars, "Number of rows", default=1.0) or 1.0), 1.0)
    pos = float(_mi_scalar(scalars, "AtlasPosition", default=0.0) or 0.0)
    tiling = float(_mi_scalar(scalars, "GraphicTiling", "Tiling", default=1.0) or 1.0)
    # Linear atlas index → (col, row)
    idx = int(round(pos)) if abs(pos - round(pos)) < 1e-4 else int(pos)
    col_i = idx % int(cols)
    row_i = idx // int(cols)
    if row_i >= int(rows):
        row_i = int(rows) - 1
    scale_u = (1.0 / cols) * max(tiling, 0.001)
    scale_v = (1.0 / rows) * max(tiling, 0.001)
    loc_u = float(col_i) / cols
    # UE atlas rows typically grow downward in V; flip so row 0 is top.
    loc_v = 1.0 - (float(row_i) + 1.0) / rows

    # M_Graphic_Atlas_01 samples TexCoord[1] when Use UV1 is on (poster cards).
    uv_layer = _graphic_atlas_uv_layer_name(use_uv1, mesh=mesh)
    if uv_layer:
        uv_map = nodes.new("ShaderNodeUVMap")
        uv_map.location = (loc[0] - 220, loc[1])
        uv_map.uv_map = uv_layer
        uv_map.label = f"UE TexCoord[{1 if use_uv1 else 0}] ({uv_layer})"
        from_uv = uv_map.outputs["UV"]
    else:
        uv = nodes.new("ShaderNodeTexCoord")
        uv.location = (loc[0] - 220, loc[1])
        from_uv = uv.outputs["UV"]
    mapping = nodes.new("ShaderNodeMapping")
    mapping.label = f"Atlas {col_i},{row_i} ({int(cols)}x{int(rows)})"
    mapping.location = loc
    mapping.inputs["Scale"].default_value = (scale_u, scale_v, 1.0)
    mapping.inputs["Location"].default_value = (loc_u, loc_v, 0.0)
    links.new(from_uv, mapping.inputs["Vector"])
    return mapping.outputs["Vector"]



def _setup_graphic_atlas_material(mat, mi_path: str, psk_path: str = "", mesh=None):
    """MCP / company branding posters — Graphic Atlas albedo + AtlasPosition cell."""
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_DECAL)
    try:
        mat["arc_decal_mask_setup"] = _MAP_DECAL_MASK_SETUP_V
        mat["arc_graphic_atlas"] = 1
    except Exception:
        pass

    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []
    switches = mi.get("switches") or {}
    COL_TEX, COL_BSDF, COL_OUT = -1000, 350, 650
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    brightness = _mi_scalar(scalars, "Atlas Brightness", default=1.0)
    bc = _mi_colour(
        colours, "BC Color", "Base Color", "BaseColor", "Color", "Tint",
        default=(0.85, 0.85, 0.85, 1.0),
    )
    if bc and len(bc) >= 3:
        principled.inputs["Base Color"].default_value = (
            float(bc[0]) * float(brightness or 1.0),
            float(bc[1]) * float(brightness or 1.0),
            float(bc[2]) * float(brightness or 1.0),
            1.0,
        )

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    atlas_param, atlas_img = _find_env_tex(
        tex_lookup, "Graphic Atlas", "Atlas", "BaseColor", "CR", "CA",
    )
    _, normal_img = _find_env_tex(
        tex_lookup, "NX", "Normal Overlay", "Normal", "Normals", "NOH", "NXX",
    )
    # Parent M_Graphic_Atlas_01 defaults Use UV1 on; missing switch ⇒ UV1.
    use_uv1 = _mi_switch(switches, "Use UV1", "UseUV1", default=True) is True
    atlas_vec = _graphic_atlas_uv_vector(
        nodes, links, mi, (COL_TEX - 280, 250), use_uv1=use_uv1, mesh=mesh,
    )

    if atlas_img:
        a_node = _new_tex_image(
            nodes, atlas_img, f"Graphic Atlas ({atlas_param})", (COL_TEX, 250),
        )
        links.new(atlas_vec, a_node.inputs["Vector"])
        links.new(a_node.outputs["Color"], principled.inputs["Base Color"])

    if normal_img and normal_img != atlas_img:
        n_str = min(max(_mi_scalar(scalars, "Normal Strength", "Blend Texture Normal Strength", default=1.0), 0.0), 2.5)
        n_node = _new_tex_image(nodes, normal_img, "Poster NX", (COL_TEX, -100), non_color=True)
        n_vec = _graphic_atlas_uv_vector(
            nodes, links, mi, (COL_TEX - 280, -100), use_uv1=use_uv1, mesh=mesh,
        )
        links.new(n_vec, n_node.inputs["Vector"])
        n_sock = _wire_normal_map(
            nodes, links, n_node.outputs["Color"], (-100, -100), strength=n_str,
        )
        links.new(n_sock, principled.inputs["Normal"])

    # Opaque paper card by default (frame PropTrim handles the metal border).
    _set_material_alpha_mode(mat, mode="OPAQUE", two_sided=True)
    principled.inputs["Roughness"].default_value = min(
        max(_mi_scalar(scalars, "Roughness", "Roughness Bias", default=0.65), 0.05), 1.0,
    )
    handled = {
        node.image.filepath
        for node in nodes
        if getattr(node, "image", None) is not None
    }
    _dump_unconnected_tex(nodes, tex_lookup, handled, COL_TEX - 500, -500)



def _setup_map_decal_material(mat, mi_path: str, psk_path: str = "", mesh=None):
    """Map murals / branding / CA+NOH crack decals; fall back to enemy NAO path."""
    mi = _parse_flat_mi_json(mi_path)
    if _is_enemy_decal_mi(mi):
        _setup_enemy_decal_material(mat, mi_path, psk_path)
        _stamp_mi_family(mat, FAMILY_DECAL)
        return
    stem_l = os.path.splitext(os.path.basename(mi_path or ""))[0].lower()
    if _is_graphic_atlas_mi(mi, stem_l):
        _setup_graphic_atlas_material(mat, mi_path, psk_path, mesh=mesh)
        return

    # Mask / Sign / Raider mark / CA+NOH — CLIP alpha
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_DECAL)
    try:
        mat["arc_decal_mask_setup"] = _MAP_DECAL_MASK_SETUP_V
    except Exception:
        pass

    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []
    COL_TEX, COL_BSDF, COL_OUT = -1000, 350, 650
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    bc = _mi_colour(
        colours, "BC Color", "Base Color", "BaseColor", "Color", "Tint",
        default=(0.85, 0.85, 0.85, 1.0),
    )
    if bc and len(bc) >= 3:
        principled.inputs["Base Color"].default_value = (
            float(bc[0]), float(bc[1]), float(bc[2]), 1.0,
        )

    tile_u = _mi_scalar(scalars, "Tile U", "Tile", "Tiling", default=1.0)
    tile_v = _mi_scalar(scalars, "Tile V", "Tile", "Tiling", default=tile_u)

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    mask_param, mask_img = _find_env_tex(
        tex_lookup, "Mask", "Raider Mark Texture", "Decal Mask", "SignTexture", "X",
    )
    color_param, color_img = _find_env_tex(
        tex_lookup,
        "BaseColor", "CR", "CA", "PM_Diffuse", "Color Overlay", "Graphic Atlas",
    )
    _, normal_img = _find_env_tex(
        tex_lookup, "Normal Overlay", "Normal", "Normals", "NOH", "NAO", "PM_Normals", "NX",
    )

    def _apply_tile(tex_node, row_y: float) -> None:
        if abs(float(tile_u) - 1.0) < 0.01 and abs(float(tile_v) - 1.0) < 0.01:
            return
        uv = nodes.new("ShaderNodeTexCoord")
        uv.location = (COL_TEX - 480, row_y)
        mapping = nodes.new("ShaderNodeMapping")
        mapping.label = f"Tile U×{tile_u:g} V×{tile_v:g}"
        mapping.location = (COL_TEX - 280, row_y)
        mapping.inputs["Scale"].default_value = (
            max(float(tile_u), 0.001),
            max(float(tile_v), 0.001),
            1.0,
        )
        links.new(uv.outputs["UV"], mapping.inputs["Vector"])
        links.new(mapping.outputs["Vector"], tex_node.inputs["Vector"])

    if color_img and color_img != mask_img:
        c_node = _new_tex_image(nodes, color_img, "Decal Color", (COL_TEX, 250))
        _apply_tile(c_node, 250)
        links.new(c_node.outputs["Color"], principled.inputs["Base Color"])
        links.new(c_node.outputs["Alpha"], principled.inputs["Alpha"])
    elif mask_img:
        mask_fpath = ""
        for _p, (fp, im) in tex_lookup.items():
            if im == mask_img:
                mask_fpath = fp
                break
        m_node = _new_tex_image(nodes, mask_img, "Decal Mask", (COL_TEX, 250), non_color=True)
        _apply_tile(m_node, 250)
        # Branding T_*_X: RGB is solid white, logo lives in Alpha (UseAlphaForMask).
        if _map_decal_mask_from_alpha(mi, mask_fpath):
            links.new(m_node.outputs["Alpha"], principled.inputs["Alpha"])
        else:
            links.new(m_node.outputs["Color"], principled.inputs["Alpha"])
    elif color_img:
        # CA used as both albedo+mask when no separate Mask param
        c_node = _new_tex_image(nodes, color_img, "Decal Color", (COL_TEX, 250))
        _apply_tile(c_node, 250)
        links.new(c_node.outputs["Color"], principled.inputs["Base Color"])
        links.new(c_node.outputs["Alpha"], principled.inputs["Alpha"])

    if normal_img and normal_img != mask_img and normal_img != color_img:
        n_str = min(max(_mi_scalar(scalars, "Normal Strength", default=1.0), 0.0), 2.5)
        n_node = _new_tex_image(nodes, normal_img, "Decal Normal", (COL_TEX, -100), non_color=True)
        _apply_tile(n_node, -100)
        n_sock = _wire_normal_map(
            nodes, links, n_node.outputs["Color"], (-100, -100), strength=n_str,
        )
        links.new(n_sock, principled.inputs["Normal"])

    _set_material_alpha_mode(
        mat,
        mode="CLIP",
        threshold=float(mi.get("opacity_clip") or 0.3333),
        two_sided=True,
    )
    principled.inputs["Roughness"].default_value = min(
        max(_mi_scalar(scalars, "Roughness", "Roughness Bias", default=0.7), 0.05), 1.0,
    )
    _ = mask_param, color_param



def _setup_tarp_material(mat, mi_path: str, psk_path: str = ""):
    """Fabric / tarp / awning — Base Color + Variation Mask, two-sided masked.

    Real example: ``MI_Cmr_Awning_02_Tarp_A`` (``M_PropPreset_NR+VPO+Detail``) with
    ``Variation Mask`` → ``_CR``, ``Base Color`` / ``Subsurface Color`` vectors,
    ``Tile``, ``TwoSided`` + ``BLEND_Masked``.
    """
    mi = _parse_flat_mi_json(mi_path)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    _stamp_mi_family(mat, FAMILY_TARP)

    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []
    COL_TEX, COL_BSDF, COL_OUT = -900, 200, 500

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (COL_BSDF, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (COL_OUT, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    local_folders = []
    for folder in (os.path.dirname(mi_path), os.path.dirname(psk_path) if psk_path else ""):
        if folder and folder not in local_folders:
            local_folders.append(folder)
    tex_lookup = _tex_lookup_from_flat_mi(mi, local_folders=local_folders)

    # Prefer authored Base Color vector so tarps aren't white when textures thin
    base_rgba = _mi_colour(
        colours, "Base Color", "RT Base Color", "Base Color Variation",
        default=(0.45, 0.42, 0.35, 1.0),
    )
    if base_rgba and len(base_rgba) >= 3:
        principled.inputs["Base Color"].default_value = (
            float(base_rgba[0]), float(base_rgba[1]), float(base_rgba[2]), 1.0,
        )

    tile = _mi_scalar(scalars, "Tile", "Tiling", "Variation Mask Tile", default=1.0)
    _, var_img = _find_env_tex(
        tex_lookup, "Variation Mask", "CR", "BaseColor", "PM_Diffuse", "CA",
    )
    _, n_img = _find_env_tex(
        tex_lookup, "Normal", "Normals", "NormalMap", "NOH", "NR", "PM_Normals",
    )

    albedo = None
    if var_img:
        v_node = _new_tex_image(nodes, var_img, "Variation / CR", (COL_TEX, 200))
        if abs(tile - 1.0) > 0.01:
            vec = _mapping_tiled(nodes, links, tile, (COL_TEX - 280, 200))
            links.new(vec, v_node.inputs["Vector"])
        # Multiply variation into base colour
        mul = nodes.new("ShaderNodeMix")
        mul.data_type = "RGBA"
        mul.blend_type = "MULTIPLY"
        mul.label = "Base × Variation"
        mul.location = (-200, 200)
        strength = _mi_scalar(
            scalars, "Bc Variation Strength", "Variation Mask Intensity", default=0.5,
        )
        mul.inputs["Factor"].default_value = min(max(float(strength), 0.0), 1.0)
        # Solid colour as A
        rgb = nodes.new("ShaderNodeRGB")
        rgb.location = (COL_TEX, 420)
        rgb.outputs[0].default_value = principled.inputs["Base Color"].default_value
        links.new(rgb.outputs[0], mul.inputs[6])
        links.new(v_node.outputs["Color"], mul.inputs[7])
        albedo = mul.outputs[2]
        links.new(albedo, principled.inputs["Base Color"])
        # Masked opacity from CR alpha when present
        links.new(v_node.outputs["Alpha"], principled.inputs["Alpha"])

    if n_img:
        n_node = _new_tex_image(nodes, n_img, "Tarp Normal", (COL_TEX, -120), non_color=True)
        if abs(tile - 1.0) > 0.01:
            vec = _mapping_tiled(nodes, links, tile, (COL_TEX - 280, -120))
            links.new(vec, n_node.inputs["Vector"])
        n_str = _mi_scalar(scalars, "Normal Strength", default=0.5)
        n_sock = _wire_normal_map(
            nodes, links, n_node.outputs["Color"], (-100, -120),
            strength=min(max(n_str, 0.0), 2.0),
        )
        links.new(n_sock, principled.inputs["Normal"])

    rough_mul = _mi_scalar(scalars, "Rough Multiplier", "Roughness", default=1.0)
    principled.inputs["Roughness"].default_value = min(max(0.35 * rough_mul, 0.2), 1.0)

    # Soft subsurface stub (MSM_Subsurface on cooked tarps)
    ss = _mi_colour(colours, "Subsurface Color", default=None)
    if ss and len(ss) >= 3:
        try:
            principled.inputs["Subsurface Weight"].default_value = min(
                max(_mi_scalar(scalars, "Subsurface Strength", default=0.15), 0.0), 0.5,
            )
            principled.inputs["Subsurface Radius"].default_value = (
                float(ss[0]), float(ss[1]), float(ss[2]),
            )
        except Exception:
            pass

    two_sided = bool(mi.get("two_sided"))
    blend = str(mi.get("blend_mode") or "").upper()
    clip = float(mi.get("opacity_clip") or 0.3333)
    if "TRANSLUCENT" in blend or "transparent" in (mi_path or "").lower():
        _set_material_alpha_mode(mat, mode="BLEND", threshold=clip, two_sided=True)
    else:
        _set_material_alpha_mode(mat, mode="CLIP", threshold=clip, two_sided=True or two_sided)

