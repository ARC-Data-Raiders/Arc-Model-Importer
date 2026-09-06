"""Hard folder-path domains: Outfit / Environment / Gun / Arc (enemy).

Pioneer dumps are already segregated on disk. Prefer path segments over
heuristics (occlusion PNGs, ``*_nom`` misc, first ``MI_*.json``) so domains
never cross-wire material pipelines.
"""
from __future__ import annotations

import os

# Public domain ids (stable API).
DOMAIN_OUTFIT = "outfit"
DOMAIN_ENVIRONMENT = "environment"
DOMAIN_WEAPON = "weapon"  # guns / firearms
DOMAIN_ENEMY = "enemy"  # Arc units
DOMAIN_UNKNOWN = "unknown"

# model_type stamps used on objects / apply_materials (legacy-compatible).
MODEL_TYPE_FOR_DOMAIN = {
    DOMAIN_OUTFIT: "clothing",  # refined further by subtype helpers
    DOMAIN_ENVIRONMENT: "map",
    DOMAIN_WEAPON: "weapon",
    DOMAIN_ENEMY: "enemy",
}

# Resolve contexts in materials.common (same string values).
CTX_FOR_DOMAIN = {
    DOMAIN_OUTFIT: "outfit",
    DOMAIN_ENVIRONMENT: "map",
    DOMAIN_WEAPON: "weapon",
    DOMAIN_ENEMY: "enemy",
}

# Most-specific first. First matching domain wins.
# Arc / guns before Characters so nested odd paths cannot steal outfit.
_DOMAIN_PATH_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        DOMAIN_ENEMY,
        (
            "/enemies/",
            "/characters/enemies/",
        ),
    ),
    (
        DOMAIN_WEAPON,
        (
            "/items/firearms/",
            "/items/melee/",
            "/items/launchers/",
            "/items/weaponmod/",
            "/firearms/",
            "/weapons/",
            "/weapon/",
            "/gun/",
        ),
    ),
    (
        DOMAIN_ENVIRONMENT,
        (
            "/environment/",
            "/mapplacements/",
            "/architecture/",
            "/foliage/",
            "/landscape/",
            "/vehicles/",
            "/interactables/",
            # Top-level Props dumps (not Characters/.../Props)
            "/content/pioneer/props/",
            "/pioneer/props/",
        ),
    ),
    (
        DOMAIN_OUTFIT,
        (
            "/characters/",
            "/heroes/",
            "/outfits/",
            "/scrappy/",
            "/heads/",
            "/backpacks/",
            "/items/characters/",  # DA_OI / skin colourways next to meshes
            "/bodycosmetics/",
        ),
    ),
)


def norm_asset_path(path: str) -> str:
    return (path or "").replace("\\", "/").lower()


def classify_asset_domain(path: str) -> str:
    """Return outfit | environment | weapon | enemy | unknown from folder path."""
    pl = norm_asset_path(path)
    if not pl:
        return DOMAIN_UNKNOWN
    for domain, segments in _DOMAIN_PATH_RULES:
        if any(seg in pl for seg in segments):
            return domain
    return DOMAIN_UNKNOWN


def is_outfit_domain(path: str) -> bool:
    return classify_asset_domain(path) == DOMAIN_OUTFIT


def is_environment_domain(path: str) -> bool:
    return classify_asset_domain(path) == DOMAIN_ENVIRONMENT


def is_weapon_domain(path: str) -> bool:
    return classify_asset_domain(path) == DOMAIN_WEAPON


def is_enemy_domain(path: str) -> bool:
    return classify_asset_domain(path) == DOMAIN_ENEMY


def domain_model_type(path: str) -> str:
    """Coarse ``arc_model_type`` for the path domain (outfit refined separately)."""
    return MODEL_TYPE_FOR_DOMAIN.get(classify_asset_domain(path), "unknown")


def domain_resolve_context(path: str) -> str:
    """materials CTX_* string for discovery gates."""
    return CTX_FOR_DOMAIN.get(classify_asset_domain(path), "any")


def domains_conflict(stamped_model_type: str, path: str) -> bool:
    """True when a stamped type belongs to a different hard domain than ``path``."""
    domain = classify_asset_domain(path)
    if domain == DOMAIN_UNKNOWN:
        return False
    mt = (stamped_model_type or "").strip().lower()
    if not mt:
        return False
    outfit_types = {
        "clothing", "visor", "body", "hair", "face", "fur", "misc",
    }
    if domain == DOMAIN_OUTFIT:
        return mt in ("map", "weapon", "enemy")
    if domain == DOMAIN_ENVIRONMENT:
        return mt != "map"
    if domain == DOMAIN_WEAPON:
        return mt != "weapon"
    if domain == DOMAIN_ENEMY:
        return mt != "enemy"
    return False


def resolve_model_type_for_path(path: str, *, stamped: str = "") -> str:
    """Prefer path domain over a conflicting stamp; keep compatible stamps."""
    domain = classify_asset_domain(path)
    mt = (stamped or "").strip().lower()

    if domain == DOMAIN_ENEMY:
        return "enemy"
    if domain == DOMAIN_WEAPON:
        return "weapon"
    if domain == DOMAIN_ENVIRONMENT:
        return "map"
    if domain == DOMAIN_OUTFIT:
        # Keep refined outfit subtypes when already stamped.
        if mt in ("clothing", "visor", "body", "hair", "face", "fur", "misc"):
            return mt
        return ""  # caller runs outfit subtype detect
    # Unknown domain: honour non-conflicting stamp
    if mt and not domains_conflict(mt, path):
        return mt
    return mt or "unknown"
