"""Release line for this install (outfits vs map-importer).

Baked by package_addon.ps1 (line=outfits, folder=DataRaiders-Outfits). Do not edit AppData copies by hand;
re-run packaging with -Line / from the correct branch.

AppData paths (Blender 5.1):
  outfits      -> .../addons/DataRaiders-Outfits
  map-importer -> .../addons/DataRaiders-MapImporter
"""

from __future__ import annotations

# "outfits" | "map-importer"
ADDON_LINE = "outfits"

MAP_IMPORTER_LINE = "map-importer"
OUTFITS_LINE = "outfits"

# Blender scripts/addons folder name for this package
ADDON_FOLDER = "DataRaiders-Outfits"


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
