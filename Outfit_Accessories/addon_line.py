"""Release line for this install (outfits vs map-importer).

Baked by package_addon.ps1 (line=outfits, folder=Arc-Asset-Importer). Do not edit AppData copies by hand;
re-run packaging with -Line / from the correct branch.

AppData paths (Blender 5.1):
  outfits      -> .../addons/Arc-Asset-Importer
  map-importer -> .../addons/DataRaiders-MapImporter
"""

from __future__ import annotations

# "outfits" | "map-importer"
ADDON_LINE = "outfits"

MAP_IMPORTER_LINE = "map-importer"
OUTFITS_LINE = "outfits"

# Blender scripts/addons folder name for this package
ADDON_FOLDER = "Arc-Asset-Importer"


def normalize_line(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in (MAP_IMPORTER_LINE, "map", "maps"):
        return MAP_IMPORTER_LINE
    if raw in (OUTFITS_LINE, "outfit", "pre-map", "pre-map-importer"):
        return OUTFITS_LINE
    return OUTFITS_LINE


def is_map_importer_line() -> bool:
    """True when map importer UI / operators should register."""
    return normalize_line(ADDON_LINE) == MAP_IMPORTER_LINE


def is_outfits_line() -> bool:
    return not is_map_importer_line()


def sibling_addon_module_candidates() -> tuple[str, ...]:
    """Blender module names for the other DataRaiders add-on."""
    if is_map_importer_line():
        return ("Arc-Asset-Importer",)
    return ("DataRaiders-MapImporter",)


def sibling_addon_enabled() -> bool:
    """True when the sibling add-on is enabled (do not drop shared Scene RNA)."""
    try:
        import addon_utils

        for mod in sibling_addon_module_candidates():
            _default, loaded = addon_utils.check(mod)
            if loaded:
                return True
    except Exception:
        pass
    return False
