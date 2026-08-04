"""Arc clothing ColorA/B/C vs ColorA2/B2/C2 routing and calibration.

Mirrors FModel ArcPaletteRouting / ArcPaletteCalibrationStore. Cooked MIs expose both
triples and OCM zones but no StaticSwitch selects which ColorMask section receives
primary vs secondary. AUTO uses the documented default; per-material overrides cover
ambiguous items. Override keys are material|parent identity — never object/outfit names.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

SCHEMA_REPORT = "arc_palette_calibration/v1"
SCHEMA_OVERRIDES = "arc_palette_overrides/v1"
ZONE_COUNT = 9

# Default AUTO map (0-based): zones 1/3/5/7/8 → primary; 2/4/6 → secondary.
DEFAULT_USES_SECONDARY = [False, True, False, True, False, True, False, False, False]
BANDS = [0.0, 0.03, 0.10, 0.18, 0.30, 0.45, 0.60, 0.75, 0.88, 1.01]

MODES = ("auto", "primary", "secondary", "swap")

# ColorMask_XYZ: one group instance per Colour N channel (zones 1..8).
COLORMASK_ZONES = tuple(range(1, 9))

# Legacy section labels kept for calibration report readability.
COLORMASK_SECTIONS = (
    (0, (1, 3, 5), "Colour 1/3/5 (legacy shared)"),
    (1, (2, 4, 6), "Colour 2/4/6 (legacy shared)"),
    (2, (7, 8), "Colour 7/8 (legacy shared)"),
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
        if z == ZONE_COUNT - 1:
            result.append(False)
            continue
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


def mix_color_mask(x, y, z, mask):
    def lerp(a, b, f):
        return tuple(av + (bv - av) * f for av, bv in zip(a, b))

    return lerp(lerp(x, y, mask[1]), z, mask[2])


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
        "no cooked StaticSwitch/scalar selects ColorMask section→palette; "
        "both ColorA/B/C and ColorA2/B2/C2 are authored and differ — use override if wrong",
    )


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
    """Mix factor for BaseColorOverlay vs ColorMask_XYZ: 0=XYZ, 0.5=blend, 1=overlay.

    Thresholds: chroma=max-min RGB; near_bw=min(‖rgb‖, ‖1-rgb‖). Pure B/W if chroma<0.02
    and near_bw<0.04 → 0; near B/W if chroma<0.12 or near_bw<0.15 → 0.5; else saturated → 1.
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
) -> dict[str, Any]:
    mode = parse_mode(mode)
    uses = resolve_uses_secondary(mode)
    confidence, reason = evaluate_confidence(primary, secondary, secondary_authored)
    if mode != "auto":
        confidence = "override"
        reason = f"routing forced to {mode} via {routing_source}"

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
        zones.append(
            {
                "zone": z + 1,
                "palette": "last" if z == ZONE_COUNT - 1 else ("secondary" if uses[z] else "primary"),
                "base_color_mask_strength": round(float(strength), 6),
                "base_color_mask_strength_authored": authored,
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
        "signals_searched": list(SIGNALS_SEARCHED),
        "signals_found": [],
    }


# ---------------------------------------------------------------------------
# Persistence (addon-relative user settings JSON)
# ---------------------------------------------------------------------------

def _addon_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def store_path() -> str:
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
    return parse_mode(load_overrides().get(material_key, fallback))


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


def resolve_routing(
    material_name: str | None,
    parent_name: str | None,
    *,
    scene_mode: str | None = None,
    material_mode: str | None = None,
    manifest_mode: str | None = None,
) -> tuple[str, str, str, list[bool]]:
    """Precedence: per-material UI → stored override → manifest → scene default → AUTO."""
    key = stable_material_key(material_name, parent_name)
    if material_mode and parse_mode(material_mode) != "auto":
        mode = parse_mode(material_mode)
        return mode, "material_prop", key, resolve_uses_secondary(mode)
    stored = resolve_mode_for_key(key) if key else "auto"
    if stored != "auto":
        return stored, "user_settings", key, resolve_uses_secondary(stored)
    if manifest_mode and parse_mode(manifest_mode) != "auto":
        mode = parse_mode(manifest_mode)
        return mode, "manifest", key, resolve_uses_secondary(mode)
    if scene_mode and parse_mode(scene_mode) != "auto":
        mode = parse_mode(scene_mode)
        return mode, "scene", key, resolve_uses_secondary(mode)
    return "auto", "default", key, resolve_uses_secondary("auto")


def write_report_beside_skin(json_path: str, report: dict) -> str:
    if not json_path:
        return ""
    out = os.path.splitext(json_path)[0] + ".palette.json"
    # Strip any accidental absolute paths from values.
    safe = json.loads(json.dumps(report))
    for path_key in ("skin_json", "ocm_texture", "colormask_texture"):
        if path_key in safe and isinstance(safe[path_key], str):
            safe[path_key] = os.path.basename(safe[path_key])
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(safe, fh, indent=2)
    return out
