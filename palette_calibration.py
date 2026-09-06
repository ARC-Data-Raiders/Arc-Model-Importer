"""Arc clothing ColorA/B/C vs ColorA2/B2/C2 routing and calibration.

Mirrors FModel ArcPaletteRouting / ArcPaletteCalibrationStore. Cooked MIs expose both
triples and OCM zones but no StaticSwitch selects which ColorMask section receives
primary vs secondary. AUTO uses the documented default; per-material overrides cover
ambiguous items. Override keys are material|parent identity — never object/outfit names.

``N_ColorSchemeBlend`` is the only named primary↔secondary mixer (see
docs/OUTFIT_COLOR_PROTOCOL.md). Endpoints 0 vs 1 are unproven; Blender keeps AUTO at
the authored default (usually 1.0) and only applies a provisional soft Mix when the
per-layer scalar is authored and differs from that default.

OCM MaterialID / ColorMask routing facts: docs/CURVATURE_ID_ROUTING.md.

Outfit color pipeline (D022–D044):
  Scene prop ``arc_outfit_color_pipeline`` = ``legacy`` | ``ground_truth``
  (default ``legacy`` during reverse campaign). Legacy keeps ColorMask_XYZ G/B
  assemble + D010 soft Mix; ground_truth (D044) uses ColorMask.r scheme + Swatch
  white-canvas A→B→C → Colour N (OCM/MID picks Colour N; ColorMask G/B are wear
  only — not ABC selectors). Do **not** drive Colour N from white-start
  BaseColor×Swatch fac — that washed Arc zone sockets. Revert: set Scene →
  Outfit Color Pipeline → Legacy, then Update Materials.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

SCHEMA_REPORT = "arc_palette_calibration/v1"
SCHEMA_OVERRIDES = "arc_palette_overrides/v1"
# Eight OCM ladder steps (MaterialID peaks 37..255). Not the legacy FModel 9-zone table.
ZONE_COUNT = 8

# D022–D032 plugin wiring. Legacy path remains callable for easy revert.
OUTFIT_COLOR_PIPELINE_LEGACY = "legacy"
OUTFIT_COLOR_PIPELINE_GROUND_TRUTH = "ground_truth"
# Default Legacy while reverse campaign runs (GT is experimental A/B).
OUTFIT_COLOR_PIPELINE_DEFAULT = OUTFIT_COLOR_PIPELINE_LEGACY
OUTFIT_COLOR_PIPELINES = (
    OUTFIT_COLOR_PIPELINE_LEGACY,
    OUTFIT_COLOR_PIPELINE_GROUND_TRUTH,
)

# BlurryCurvature (D025/D028): 5-tap OCM.G — stub until detail-normal hook lands.
BLUR_MIP_DEFAULT = 5.0
BLUR_OFFSET_DEFAULT = 0.0075
BLURRY_CURVATURE_STATUS = "stub_ocm_g_5tap_detail_normal_only"

# AUTO: all zones use primary ColorA/B/C (Abyss measure 2.18.147).
# Sleeves/helmet/gaiters match primary; odd/even put large A-dom zones (Helmet L2,
# Upper L2, Airbag L2) on secondary and darkened those blocks. Airbag Leather_Blue
# cyan lives in ColorA2 — handled via MEASURED_MODE_OVERRIDES → secondary.
# Match FModel ArcPaletteRouting.DefaultUsesSecondary (0-based):
# zones 1/3/5/7/8 → primary; 2/4/6 → secondary. ZONE_COUNT is 8 (zone 9 ignored).
DEFAULT_USES_SECONDARY = [False, True, False, True, False, True, False, False]

# Measured per-MI mode overrides (material|parent). Applied after user_settings
# only when the key has no stored override — never outfit/object names.
MEASURED_MODE_OVERRIDES = {
    # Vest L2 is ColorMask-A; cyan is authored on ColorA2 (primary A is grey-blue).
    "MI_Abyss_Airbag_Leather_Blue|MI_Character_Layered_6": "secondary",
}
# Non-Color (byte/255) midpoints between peaks 37,66,96,126,158,190,222,255.
BANDS = [0.0, 0.2020, 0.3176, 0.4353, 0.5569, 0.6824, 0.8078, 0.9353, 1.01]

MODES = ("auto", "primary", "secondary", "swap")

# ColorMask_XYZ: one group instance per Colour N channel (zones 1..8).
COLORMASK_ZONES = tuple(range(1, 9))

# Authored MI default for N_ColorSchemeBlend on Abyss / Western / Vanguard samples.
COLOR_SCHEME_BLEND_DEFAULT = 1.0
COLOR_SCHEME_BLEND_EPS = 1e-4
# Provisional Mix Fac semantics until MF_ColorSchemeBlend graph is recovered:
# Fac 0 → ColorA/B/C (primary), Fac 1 → ColorA2/B2/C2 (secondary). Not proven.
COLOR_SCHEME_BLEND_SEMANTICS = (
    "reserved_endpoints_provisional_0_primary_1_secondary"
)

# Legacy section labels kept for calibration report readability.
COLORMASK_SECTIONS = (
    (0, (1, 2, 3, 4, 5, 6, 7, 8), "Colour 1–8 primary ColorA/B/C"),
)

SIGNALS_SEARCHED = [
    "StaticSwitchParameters",
    "StaticParametersRuntime.UseLayerN",
    "ColorSchemeBlend",
    "ColorMaskSwatch",
    "BaseColorMaskStrength",
    "parent MI_Character_Layered_N (layer count only)",
    "per-zone PatternColorA/B/C",
    "vertex colors / UV sets",
]

_STRIP_TICK = re.compile(r"^MaterialInstanceConstant'|'$")


def normalize_parent_name(parent: str | None) -> str:
    if not parent:
        return ""
    s = str(parent).strip()
    s = _STRIP_TICK.sub("", s)
    tick = s.find("'")
    if 0 <= tick < len(s) - 1:
        s = s[tick + 1 :].rstrip("'")
    slash = max(s.rfind("/"), s.rfind("\\"))
    if slash >= 0:
        s = s[slash + 1 :]
    dot = s.find(".")
    if dot >= 0:
        s = s[:dot]
    return s


def normalize_material_name(name: str | None) -> str:
    if not name:
        return ""
    s = str(name).strip()
    if s.lower().endswith(".json"):
        s = s[:-5]
    slash = max(s.rfind("/"), s.rfind("\\"))
    if slash >= 0:
        s = s[slash + 1 :]
    return s


def stable_material_key(material_name: str | None, parent_name: str | None) -> str:
    mat = normalize_material_name(material_name)
    parent = normalize_parent_name(parent_name)
    if not mat:
        return ""
    return mat if not parent else f"{mat}|{parent}"


def parse_mode(text: str | None) -> str:
    if not text:
        return "auto"
    t = str(text).strip().lower()
    return t if t in MODES else "auto"


def resolve_uses_secondary(mode: str) -> list[bool]:
    mode = parse_mode(mode)
    result = []
    for z in range(ZONE_COUNT):
        if mode == "primary":
            result.append(False)
        elif mode == "secondary":
            result.append(True)
        elif mode == "swap":
            result.append(not DEFAULT_USES_SECONDARY[z])
        else:
            result.append(DEFAULT_USES_SECONDARY[z])
    return result


def pack_secondary_mask(uses: list[bool]) -> int:
    mask = 0
    for z, flag in enumerate(uses[:ZONE_COUNT]):
        if flag:
            mask |= 1 << z
    return mask


def active_zone(id_value: float) -> int:
    for zone in range(ZONE_COUNT):
        if BANDS[zone] <= id_value < BANDS[zone + 1]:
            return zone
    return 0


def parse_outfit_color_pipeline(text: str | None) -> str:
    """Normalize Scene / mat pipeline flag to ``legacy`` or ``ground_truth``."""
    if not text:
        return OUTFIT_COLOR_PIPELINE_DEFAULT
    t = str(text).strip().lower().replace("-", "_").replace(" ", "_")
    if t in ("legacy", "old", "d010", "colormask_xyz"):
        return OUTFIT_COLOR_PIPELINE_LEGACY
    if t in (
        "ground_truth",
        "groundtruth",
        "gt",
        "d022",
        "d032",
        "d022_d032",
    ):
        return OUTFIT_COLOR_PIPELINE_GROUND_TRUTH
    return OUTFIT_COLOR_PIPELINE_DEFAULT


def _lerp3(a, b, f: float):
    f = max(0.0, min(1.0, float(f)))
    return tuple(av + (bv - av) * f for av, bv in zip(a, b))


def mix_color_mask_legacy(x, y, z, mask):
    """Legacy emulator / ColorMask_XYZ assemble: lerp(lerp(X,Y,G), Z, B).

    Cooked BasePass does **not** use G/B as A/B/C selectors (D027/D031). Kept for
    ``outfit_color_pipeline=legacy`` revert and calibration comparisons.
    """
    return _lerp3(_lerp3(x, y, mask[1]), z, mask[2])


# Back-compat alias — callers that still import ``mix_color_mask`` get legacy.
mix_color_mask = mix_color_mask_legacy


def color_scheme_blend_legacy_d010(primary, secondary, blend: float):
    """D010 authored scalar Mix: lerp(secondary, primary, blend)."""
    return _lerp3(secondary, primary, blend)


def color_scheme_blend_ground_truth(primary, secondary, mask_r: float):
    """D022/D023: OutX = lerp(ColorX, ColorX2, ColorMask.r)."""
    return _lerp3(primary, secondary, mask_r)


def assemble_color_ground_truth(out_a, out_b, out_c, fac_rgb):
    """Cooked D027/D031 BasePass: col = lerp(1,OutA,fac.r) → OutB → OutC.

    ``fac_rgb`` is already ``amt * BaseColor * ColorMaskSwatch`` (per channel).
    ColorMask.g/b are **not** assemble factors (wear-band only when Amount/Softness≠0).

    Arc GT Colour N uses this same white-start path (``_wire_outfit_color_ground_truth``).
    D044 omitted BaseColor from Fac and nested Swatch G/B as mix weights — that
    collapsed most layers to ColorC. Spatial ABC is BaseColor RGB × Swatch, not
    ColorMask G/B.
    """
    white = (1.0, 1.0, 1.0)
    fr, fg, fb = (float(fac_rgb[0]), float(fac_rgb[1]), float(fac_rgb[2]))
    col = _lerp3(white, out_a, fr)
    col = _lerp3(col, out_b, fg)
    return _lerp3(col, out_c, fb)


def assemble_fac_ground_truth(
    amt: float,
    base_color_rgb,
    swatch_rgb,
) -> tuple[float, float, float]:
    """fac = amt × BaseColor × ColorMaskSwatch (per channel)."""
    a = max(0.0, min(1.0, float(amt)))
    return (
        a * float(base_color_rgb[0]) * float(swatch_rgb[0]),
        a * float(base_color_rgb[1]) * float(swatch_rgb[1]),
        a * float(base_color_rgb[2]) * float(swatch_rgb[2]),
    )


def layer_mask_ps_zone(mid: float) -> int:
    """Cooked LayerMask quantize: ``round(MID * 8)`` (D029/D030)."""
    try:
        return int(round(float(mid) * 8.0))
    except (TypeError, ValueError):
        return 0


def layer_mask_bit_active(ps_zone: int, layer_mask: int) -> bool:
    """``((1 << ps_zone) & LayerMask) != 0``."""
    try:
        z = int(ps_zone)
        m = int(layer_mask)
    except (TypeError, ValueError):
        return True
    if z < 0 or z > 31:
        return False
    return ((1 << z) & m) != 0


def layer_mask_zone_enabled(ps_zone: int, bits_by_layer: dict[int, int] | None) -> bool:
    """True when no masks authored, or any layer bit-includes ``ps_zone``.

    Emulator / decal helpers may use this. Outfit Colour N assemble must **not**
    call it to zero ``amt`` — Arc MID→Colour N is a different job from the cooked
    per-layer pixel gate (see ``_wire_outfit_color_ground_truth``).
    """
    if not bits_by_layer:
        return True
    return any(layer_mask_bit_active(ps_zone, m) for m in bits_by_layer.values())


def blurry_curvature_ground_truth_stub(
    curvature: float,
    *,
    mip: float = BLUR_MIP_DEFAULT,
    offset: float = BLUR_OFFSET_DEFAULT,
) -> dict[str, Any]:
    """D025/D028 stub: 5-tap OCM.**G** feeds detail/medium normals — not ColorScheme.

    Identity pass-through until a Blender blur/curvature hook exists. Safe for color.
    """
    return {
        "in_curvature_g": float(curvature),
        "BlurMipLevel": float(mip),
        "BlurOffset": float(offset),
        "out_blurred_g": float(curvature),
        "status": BLURRY_CURVATURE_STATUS,
        "feeds": "detail_medium_normal_weight_not_color",
        "evidence": "docs/OUTFIT_SHADER_GAP_CHASE.md D025/D028",
    }


def _triple_distance(a, b) -> float:
    def dist(u, v):
        return sum((x - y) ** 2 for x, y in zip(u, v)) ** 0.5

    return (dist(a[0], b[0]) + dist(a[1], b[1]) + dist(a[2], b[2])) / 3.0


def evaluate_confidence(primary, secondary, secondary_authored: bool) -> tuple[str, str]:
    if not secondary_authored:
        return "high", "only primary ColorA/B/C authored; secondary falls back to primary"
    if _triple_distance(primary, secondary) < 0.02:
        return "high", "primary and secondary triples are nearly identical"
    return (
        "ambiguous",
        "no cooked StaticSwitch selects ColorMask section→palette; "
        "N_ColorSchemeBlend is almost always authored 1.0 (AUTO=all-primary XYZ map; "
        "soft Mix only when authored != default); "
        "both ColorA/B/C and ColorA2/B2/C2 may differ — use override if wrong",
    )


def extract_color_scheme_blends(zone_scalars: dict | None) -> dict[int, float]:
    """Map zone 1..N → authored N_ColorSchemeBlend from parse_clothing_mi zone_scalars."""
    out: dict[int, float] = {}
    if not zone_scalars:
        return out
    for key, value in zone_scalars.items():
        zone_s = None
        suffix = None
        if isinstance(key, tuple) and len(key) == 2:
            zone_s, suffix = key[0], key[1]
        elif isinstance(key, str):
            m = re.match(r"^(\d+)_ColorSchemeBlend$", key)
            if m:
                zone_s, suffix = m.group(1), "ColorSchemeBlend"
        if suffix != "ColorSchemeBlend" or zone_s is None:
            continue
        try:
            out[int(zone_s)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def scheme_blend_is_default(value: float | None) -> bool:
    if value is None:
        return True
    try:
        return abs(float(value) - COLOR_SCHEME_BLEND_DEFAULT) <= COLOR_SCHEME_BLEND_EPS
    except (TypeError, ValueError):
        return True


def provisional_scheme_mix_factor(blend: float | None) -> float | None:
    """Return Mix Fac for ColorA↔ColorA2 when blend is authored and non-default.

    None → keep AUTO / override zone map (do not invent endpoint behaviour at 0 or 1
    when the value is the cooked default). Soft weight only for non-default authored
    scalars, clamped to [0, 1].
    """
    if blend is None or scheme_blend_is_default(blend):
        return None
    try:
        return max(0.0, min(1.0, float(blend)))
    except (TypeError, ValueError):
        return None


def section_colour_inputs(mode: str) -> dict[int, dict[str, str]]:
    """Build materials.py _CM_COLOUR_INPUTS keyed by zone 1..8 (one XYZ group each)."""
    uses = resolve_uses_secondary(mode)
    out = {}
    for zone in COLORMASK_ZONES:
        secondary = uses[zone - 1]
        out[zone] = {
            "X_Green": "ColorA2" if secondary else "ColorA",
            "Y_Blue": "ColorB2" if secondary else "ColorB",
            "Z_Pink": "ColorC2" if secondary else "ColorC",
        }
    return out


def base_overlay_mix_factor(rgba) -> float:
    """Legacy chroma heuristic for BaseColorOverlay Fac when strength is missing.

    Thresholds: chroma=max-min RGB; near_bw=min(‖rgb‖, ‖1-rgb‖). Pure B/W if chroma<0.02
    and near_bw<0.04 → 0; near B/W if chroma<0.12 or near_bw<0.15 → 0.5; else saturated → 1.
    Prefer :func:`overlay_mix_factor` which uses authored ``N_BaseColorMaskStrength``.
    """
    if rgba is None:
        return 0.0
    r = float(rgba[0])
    g = float(rgba[1])
    b = float(rgba[2])
    chroma = max(r, g, b) - min(r, g, b)
    dist_black = (r * r + g * g + b * b) ** 0.5
    dist_white = ((1.0 - r) ** 2 + (1.0 - g) ** 2 + (1.0 - b) ** 2) ** 0.5
    near_bw = min(dist_black, dist_white)
    if chroma < 0.02 and near_bw < 0.04:
        return 0.0
    if chroma < 0.12 or near_bw < 0.15:
        return 0.5
    return 1.0


def extract_base_color_mask_strengths(zone_scalars: dict | None) -> dict[int, float]:
    """Map MI layer 1..N → authored N_BaseColorMaskStrength from parse_clothing_mi zone_scalars.

    Keys are MI layer indices. Remap to Arc Colour / OCM zones via
    :func:`ocm_zone_for_mi_layer` / :func:`mi_layer_for_ocm_zone`.
    """
    out: dict[int, float] = {}
    if not zone_scalars:
        return out
    for key, value in zone_scalars.items():
        zone_s = None
        suffix = None
        if isinstance(key, tuple) and len(key) == 2:
            zone_s, suffix = key[0], key[1]
        elif isinstance(key, str):
            m = re.match(r"^(\d+)_BaseColorMaskStrength$", key)
            if m:
                zone_s, suffix = m.group(1), "BaseColorMaskStrength"
        if suffix != "BaseColorMaskStrength" or zone_s is None:
            continue
        try:
            out[int(zone_s)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def extract_layer_mask_bits(zone_scalars: dict | None) -> dict[int, int]:
    """Map MI layer 1..N → raw ``N_LayerMask`` bitmask (D029/D032).

    Airbag example: L1=8, L2=2 are bit flags vs ``round(MID*8)``, **not** OCM
    band indices. Values are kept as non-negative ints (0.. saturates naturally).
    """
    out: dict[int, int] = {}
    if not zone_scalars:
        return out
    for key, value in zone_scalars.items():
        layer_s = None
        suffix = None
        if isinstance(key, tuple) and len(key) == 2:
            layer_s, suffix = key[0], key[1]
        elif isinstance(key, str):
            m = re.match(r"^(\d+)_LayerMask$", key)
            if m:
                layer_s, suffix = m.group(1), "LayerMask"
        if suffix != "LayerMask" or layer_s is None:
            continue
        try:
            mask = int(round(float(value)))
            layer = int(layer_s)
        except (TypeError, ValueError):
            continue
        if layer < 1 or layer > 8 or mask < 0:
            continue
        out[layer] = mask
    return out


def extract_layer_masks(zone_scalars: dict | None) -> dict[int, int]:
    """Legacy map MI layer 1..N → OCM zone 1..8 from authored ``N_LayerMask``.

    Pre-D029 interpretation (band index). Kept for ``outfit_color_pipeline=legacy``
    inspection stamps. Ground-truth callers should use :func:`extract_layer_mask_bits`.

    Values outside 1..8 are ignored (identity fallback at call sites).
    """
    out: dict[int, int] = {}
    if not zone_scalars:
        return out
    for key, value in zone_scalars.items():
        layer_s = None
        suffix = None
        if isinstance(key, tuple) and len(key) == 2:
            layer_s, suffix = key[0], key[1]
        elif isinstance(key, str):
            m = re.match(r"^(\d+)_LayerMask$", key)
            if m:
                layer_s, suffix = m.group(1), "LayerMask"
        if suffix != "LayerMask" or layer_s is None:
            continue
        try:
            ocm = int(round(float(value)))
            layer = int(layer_s)
        except (TypeError, ValueError):
            continue
        if 1 <= ocm <= 8 and 1 <= layer <= 8:
            out[layer] = ocm
    return out


def ocm_zone_for_mi_layer(mi_layer: int, layer_masks: dict[int, int] | None) -> int:
    """OCM zone for an MI layer; identity when LayerMask is missing."""
    try:
        layer = int(mi_layer)
    except (TypeError, ValueError):
        return 1
    if not layer_masks:
        return layer
    return int(layer_masks.get(layer, layer))


def mi_layer_for_ocm_zone(ocm_zone: int, layer_masks: dict[int, int] | None) -> int:
    """MI layer that authors an OCM zone (lowest layer wins). Identity if none."""
    try:
        zone = int(ocm_zone)
    except (TypeError, ValueError):
        return 1
    if not layer_masks:
        return zone
    owners = [layer for layer, ocm in layer_masks.items() if int(ocm) == zone]
    if not owners:
        return zone
    return min(owners)


def overlay_mix_factor(strength: float | None, rgba=None) -> float:
    """Overlay Fac for Colour N from authored BaseColorMaskStrength (inverted).

    Fac 0 → ColorMask_XYZ only; Fac 1 → full BaseColorOverlay; values in between blend.

    Game semantics: ``N_BaseColorMaskStrength`` 1.0 → ColorMask drives colour;
    0.0 → ``N_BaseColorOverlay`` replaces. Arc ``Overlay Fac`` is the opposite
    pole, so Fac = ``1 - strength`` when strength is authored.

    Missing strength falls back to :func:`base_overlay_mix_factor` on the overlay RGB.
    Pure black/white overlays are MIX-replace no-ops (same as chroma→0): Fac
    stays 0 so Colour N keeps ColorMask_XYZ (e.g. Abyss lowerbody white overlay
    must not wipe ColorB gaiters).
    """
    if rgba is not None and base_overlay_mix_factor(rgba) == 0.0:
        return 0.0
    if strength is not None:
        try:
            s = max(0.0, min(1.0, float(strength)))
            return max(0.0, min(1.0, 1.0 - s))
        except (TypeError, ValueError):
            pass
    return base_overlay_mix_factor(rgba)


def parent_from_mi_props(props: dict) -> str:
    parent = props.get("Parent") or {}
    return str(parent.get("ObjectName") or parent.get("ObjectPath") or "")


def material_name_from_json_path(json_path: str) -> str:
    return normalize_material_name(os.path.basename(json_path) if json_path else "")


def build_report(
    material_name: str,
    parent_name: str,
    mode: str,
    routing_source: str,
    primary: tuple,
    secondary: tuple,
    secondary_authored: bool,
    *,
    base_mask_strength: list[float] | None = None,
    base_mask_authored: list[bool] | None = None,
    parameters: dict | None = None,
    ocm_histogram: list[int] | None = None,
    colormask_means: list[list[float]] | None = None,
    color_scheme_blends: dict[int, float] | None = None,
    soft_mix_zones: list[int] | None = None,
) -> dict[str, Any]:
    mode = parse_mode(mode)
    uses = resolve_uses_secondary(mode)
    confidence, reason = evaluate_confidence(primary, secondary, secondary_authored)
    if mode != "auto":
        confidence = "override"
        reason = f"routing forced to {mode} via {routing_source}"

    blends = {int(k): float(v) for k, v in (color_scheme_blends or {}).items()}
    soft_zones = sorted({int(z) for z in (soft_mix_zones or [])})
    signals_found = []
    if blends:
        signals_found.append("ColorSchemeBlend")

    zones = []
    for z in range(ZONE_COUNT):
        strength = (
            base_mask_strength[z]
            if base_mask_strength and z < len(base_mask_strength)
            else 1.0
        )
        authored = bool(
            base_mask_authored and z < len(base_mask_authored) and base_mask_authored[z]
        )
        zone_num = z + 1
        blend_val = blends.get(zone_num)
        zones.append(
            {
                "zone": zone_num,
                "palette": "last" if z == ZONE_COUNT - 1 else ("secondary" if uses[z] else "primary"),
                "base_color_mask_strength": round(float(strength), 6),
                "base_color_mask_strength_authored": authored,
                "color_scheme_blend": (
                    None if blend_val is None else round(float(blend_val), 6)
                ),
                "color_scheme_blend_soft_mix": zone_num in soft_zones,
            }
        )

    sections = []
    for zone in COLORMASK_ZONES:
        secondary_flag = uses[zone - 1]
        sections.append(
            {
                "instance": zone - 1,
                "zone": zone,
                "label": f"Colour {zone}",
                "zones": [zone],
                "palette": "secondary" if secondary_flag else "primary",
                "x": "ColorA2" if secondary_flag else "ColorA",
                "y": "ColorB2" if secondary_flag else "ColorB",
                "z": "ColorC2" if secondary_flag else "ColorC",
            }
        )

    cm_hist = None
    if colormask_means:
        cm_hist = []
        for z, m in enumerate(colormask_means[:ZONE_COUNT]):
            r, g, b = (m + [0, 0, 0])[:3]
            dominant = "B" if g >= r and g >= b else ("C" if b >= r and b >= g else "A")
            cm_hist.append(
                {
                    "zone": z + 1,
                    "mean_r": round(float(r), 6),
                    "mean_g": round(float(g), 6),
                    "mean_b": round(float(b), 6),
                    "dominant": dominant,
                }
            )

    return {
        "schema": SCHEMA_REPORT,
        "material_key": stable_material_key(material_name, parent_name),
        "material_name": normalize_material_name(material_name),
        "parent_name": normalize_parent_name(parent_name),
        "routing_mode": mode,
        "routing_source": routing_source,
        "uses_secondary": uses,
        "confidence": confidence,
        "confidence_reason": reason,
        "primary": {"a": list(primary[0][:3]), "b": list(primary[1][:3]), "c": list(primary[2][:3])},
        "secondary": {
            "a": list(secondary[0][:3]),
            "b": list(secondary[1][:3]),
            "c": list(secondary[2][:3]),
        },
        "secondary_authored": secondary_authored,
        "zones": zones,
        "colormask_sections": sections,
        "parameters": parameters or {},
        "ocm_histogram": ocm_histogram,
        "colormask_channel_histogram": cm_hist,
        "color_scheme_blend_default": COLOR_SCHEME_BLEND_DEFAULT,
        "color_scheme_blend_semantics": COLOR_SCHEME_BLEND_SEMANTICS,
        "color_scheme_blends": {str(k): round(float(v), 6) for k, v in sorted(blends.items())},
        "color_scheme_blend_soft_mix_zones": soft_zones,
        "signals_searched": list(SIGNALS_SEARCHED),
        "signals_found": signals_found,
    }


# ---------------------------------------------------------------------------
# Persistence (addon AppData / LOCALAPPDATA only — never Pioneer/game roots)
# ---------------------------------------------------------------------------

_SAFE_FILENAME = re.compile(r"[^\w.\-]+")


def _addon_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _user_cache_root() -> str:
    """%LOCALAPPDATA%\\<AddonFolder> (same layout as pycache_prefix)."""
    local = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("TEMP")
        or os.path.expanduser("~")
    )
    folder = os.path.basename(_addon_dir()) or "DataRaiders-Outfits"
    return os.path.join(local, folder)


def report_cache_dir() -> str:
    path = os.path.join(_user_cache_root(), "palette_reports")
    os.makedirs(path, exist_ok=True)
    return path


def store_path() -> str:
    """User overrides JSON — beside the installed addon (Blender AppData), never game content."""
    return os.path.join(_addon_dir(), "arc_palette_calibration.json")


def load_overrides() -> dict[str, str]:
    path = store_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            root = json.load(fh)
        overrides = root.get("overrides") or {}
        return {
            str(k): parse_mode(v)
            for k, v in overrides.items()
            if parse_mode(v) != "auto"
        }
    except Exception as exc:
        print(f"Arc Raiders: failed to load palette calibration: {exc}")
        return {}


def save_overrides(overrides: dict[str, str]) -> None:
    path = store_path()
    cleaned = {k: parse_mode(v) for k, v in overrides.items() if parse_mode(v) != "auto"}
    payload = {"schema": SCHEMA_OVERRIDES, "overrides": cleaned}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def resolve_mode_for_key(material_key: str, fallback: str = "auto") -> str:
    if not material_key:
        return parse_mode(fallback)
    stored = load_overrides().get(material_key)
    if stored:
        return parse_mode(stored)
    measured = MEASURED_MODE_OVERRIDES.get(material_key)
    if measured:
        return parse_mode(measured)
    return parse_mode(fallback)


def set_override(material_key: str, mode: str) -> None:
    if not material_key:
        return
    overrides = load_overrides()
    mode = parse_mode(mode)
    if mode == "auto":
        overrides.pop(material_key, None)
    else:
        overrides[material_key] = mode
    save_overrides(overrides)


def clear_overrides() -> None:
    save_overrides({})


def import_overrides_json(text: str) -> int:
    root = json.loads(text)
    overrides = root.get("overrides") if isinstance(root, dict) else None
    if overrides is None and isinstance(root, dict):
        overrides = root
    cleaned = {}
    for k, v in (overrides or {}).items():
        mode = parse_mode(v)
        if mode != "auto":
            cleaned[str(k)] = mode
    save_overrides(cleaned)
    return len(cleaned)


def export_overrides_json() -> str:
    return json.dumps({"schema": SCHEMA_OVERRIDES, "overrides": load_overrides()}, indent=2)


def load_palette_sidecar(json_path: str | None) -> dict:
    """FModel ``*.palette.json`` next to a skin MI (colors + uses_secondary + decals).

    Outfit package exports often copy the MIC JSON without the palette sidecar.
    When missing beside ``json_path``, also try the same ``PioneerGame/...`` leaf
    under ``ARC_RAIDERS_DUMP`` / ``ARC_RAIDERS_ROOT`` / ``ARC_RAIDERS_CURRENT``.
    """
    if not json_path:
        return {}
    candidates: list[str] = [os.path.splitext(json_path)[0] + ".palette.json"]
    norm = str(json_path).replace("\\", "/")
    marker = "/PioneerGame/"
    idx = norm.lower().find(marker.lower())
    if idx >= 0:
        # leaf includes PioneerGame/.../Name.json
        leaf = norm[idx + 1 :]
        leaf_pal = os.path.splitext(leaf)[0].replace("/", os.sep) + ".palette.json"
        for env_key in ("ARC_RAIDERS_DUMP", "ARC_RAIDERS_ROOT", "ARC_RAIDERS_CURRENT"):
            root = (os.environ.get(env_key) or "").strip()
            if root:
                candidates.append(os.path.join(root, leaf_pal))
    seen: set[str] = set()
    for sidecar in candidates:
        try:
            key = os.path.normcase(os.path.abspath(sidecar))
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        if not os.path.isfile(sidecar):
            continue
        try:
            with open(sidecar, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        return data if isinstance(data, dict) else {}
    return {}


def uses_secondary_from_sidecar(json_path: str | None) -> list[bool] | None:
    data = load_palette_sidecar(json_path)
    arr = data.get("uses_secondary") if data else None
    if not isinstance(arr, list) or len(arr) < ZONE_COUNT:
        return None
    return [bool(x) for x in arr[:ZONE_COUNT]]


def resolve_routing(
    material_name: str | None,
    parent_name: str | None,
    *,
    scene_mode: str | None = None,
    material_mode: str | None = None,
    manifest_mode: str | None = None,
    json_path: str | None = None,
) -> tuple[str, str, str, list[bool]]:
    """Precedence: per-material UI → stored override → measured → palette sidecar → manifest → scene → AUTO."""
    key = stable_material_key(material_name, parent_name)
    if material_mode and parse_mode(material_mode) != "auto":
        mode = parse_mode(material_mode)
        return mode, "material_prop", key, resolve_uses_secondary(mode)
    if key:
        stored = load_overrides().get(key)
        if stored:
            mode = parse_mode(stored)
            return mode, "user_settings", key, resolve_uses_secondary(mode)
        measured = MEASURED_MODE_OVERRIDES.get(key)
        if measured:
            mode = parse_mode(measured)
            return mode, "measured", key, resolve_uses_secondary(mode)
    sidecar_uses = uses_secondary_from_sidecar(json_path)
    if sidecar_uses is not None:
        return "auto", "palette_sidecar", key, sidecar_uses
    if manifest_mode and parse_mode(manifest_mode) != "auto":
        mode = parse_mode(manifest_mode)
        return mode, "manifest", key, resolve_uses_secondary(mode)
    if scene_mode and parse_mode(scene_mode) != "auto":
        mode = parse_mode(scene_mode)
        return mode, "scene", key, resolve_uses_secondary(mode)
    return "auto", "default", key, resolve_uses_secondary("auto")


def _report_filename(json_path: str, report: dict) -> str:
    key = str(report.get("material_key") or "").strip()
    if not key and json_path:
        key = os.path.splitext(os.path.basename(json_path))[0]
    if not key:
        key = "palette_report"
    return _SAFE_FILENAME.sub("_", key)[:180] + ".palette.json"


def write_report(json_path: str, report: dict) -> str:
    """Write calibration report under LOCALAPPDATA cache — never beside game assets.

    Pioneer / FModel content trees are strictly read-only for this addon.
    """
    safe = json.loads(json.dumps(report))
    for path_key in ("skin_json", "ocm_texture", "colormask_texture"):
        if path_key in safe and isinstance(safe[path_key], str):
            safe[path_key] = os.path.basename(safe[path_key])
    out = os.path.join(report_cache_dir(), _report_filename(json_path, safe))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(safe, fh, indent=2)
    return out


def write_report_beside_skin(json_path: str, report: dict) -> str:
    """Deprecated name: reports are never written beside skin/MI JSON under Pioneer."""
    return write_report(json_path, report)
