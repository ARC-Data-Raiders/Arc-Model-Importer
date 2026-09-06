"""Detect environmental prop subtypes from flat MI JSON + path stem."""
from __future__ import annotations

import os

# Bump when trim UV / height-paint wiring changes → Force All / repair rebuilds.
# v4: preset defaults merged in, object-space paint band, weathering + dust.
# v5: world-space paint rebased onto the mesh origin for unplaced single props.
# v6: tint washout clamp + dust contrast=0 no longer forces solid coverage.
# v7: weathering coverage probe-calibrated (ENV_TRIMMAP_PROTOCOL).
ENV_PROPS_SETUP_V = "v8"

KIND_GENERIC = "generic"
KIND_ARCH_TRIM = "arch_trim"
KIND_PROPTRIM = "proptrim"
KIND_TRIM_MAPPER = "trim_mapper"
KIND_CONCRETE = "concrete"


def _stem(mi_path: str = "", mi: dict | None = None) -> str:
    if mi_path:
        return os.path.splitext(os.path.basename(mi_path))[0].lower()
    if mi:
        return str(mi.get("name") or mi.get("stem") or "").lower()
    return ""


def _parent(mi: dict) -> str:
    return str((mi or {}).get("parent") or "").lower()


def _params(mi: dict) -> set:
    return {p for p, _ in ((mi or {}).get("textures") or [])}


def is_proptrim_atlas_mi(mi: dict, mi_path: str = "") -> bool:
    """True for PropTrim atlas sheets (mesh UV + UVOffset cell), not all metal.

    IMPORTANT: do **not** treat every FAMILY_METAL as PropTrim — TrimMapper and
    painted-metal overlays share the metal family but use different UV rules.
    """
    parent = _parent(mi)
    stem = _stem(mi_path, mi)
    if "proptrimpreset" in parent or "prop_trim_preset" in parent:
        return True
    if "proptrim" in parent or "prop_trim" in parent:
        return True
    if "proptrim" in stem or "prop_trim" in stem:
        return True
    params = _params(mi)
    # Compact dumps: CR Texture + NXX/NMX + Overlay/Prop AO without TrimMap parent
    if "CR Texture" in params and (params & {"NXX/NMX Texture", "NXX", "NMX", "NOM"}):
        if "Prop AO Texture" in params or "Overlay" in params:
            if "trimmap" not in parent and "trim_map" not in parent:
                # Prefer stem/parent signal; allow library MI_PropTrim_* names
                if stem.startswith("mi_proptrim") or "proptrim" in stem:
                    return True
    return False


def is_trim_mapper_mi(mi: dict, mi_path: str = "") -> bool:
    """True for M_TrimMap_01 children, by parent name or parameter signature.

    Compact dumps carry no ``Parent``, so the numbered TrimMap param groups are
    the only reliable signal there.
    """
    parent = _parent(mi)
    stem = _stem(mi_path, mi)
    if "trimmap" in parent or "m_trimmap" in parent or "trim_map" in parent:
        return True
    if "trimmapper" in stem or "trim_mapper" in stem:
        return True
    from ..classify import _is_trim_map_metal_mi

    return bool(_is_trim_map_metal_mi(mi, stem))


def is_architecture_trim_kind(mi: dict, mi_path: str = "") -> bool:
    """Delegate-compatible check; classify owns the authoritative helpers."""
    from ..classify import _is_architecture_trim_mi

    return bool(_is_architecture_trim_mi(mi, _stem(mi_path, mi)))


def wants_world_paint(mi: dict) -> bool:
    """True when MI enables MF_ArchitecurePaint height paint.

    ``Paint_WorldSpaceHeight`` picks world Z over the mesh's own local Z; it is
    *not* a second on/off switch. Requiring it was why the elevator's cream band
    never appeared — its concrete sets ``Use Paint`` with the switch off and
    places the band with ``PaintLimitUpper`` / ``PaintLimitLower``.
    """
    switches = (mi or {}).get("switches") or {}
    use_paint = switches.get("Use Paint")
    if use_paint is False and not switches.get("UseFullPaint"):
        return False
    if use_paint is not True and not switches.get("UseFullPaint"):
        if switches.get("Paint_WorldSpaceHeight") is not True:
            return False
    # Needs a band to place: either PaintLimit pair or the ground/height pair.
    from .world_paint import paint_band

    return paint_band(mi, 1.0) is not None


def detect_env_prop_kind(mi: dict, mi_path: str = "", family: str = "") -> str:
    """Return subtype token for logging / stamps."""
    if is_architecture_trim_kind(mi, mi_path):
        return KIND_ARCH_TRIM
    if is_proptrim_atlas_mi(mi, mi_path):
        return KIND_PROPTRIM
    if is_trim_mapper_mi(mi, mi_path):
        return KIND_TRIM_MAPPER
    stem = _stem(mi_path, mi)
    parent = _parent(mi)
    if wants_world_paint(mi) or "concrete" in stem or "architecturepreset_concrete" in parent:
        return KIND_CONCRETE
    return KIND_GENERIC
