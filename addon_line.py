"""Release line for this install (outfits vs map-importer).

``package_addon.ps1`` overwrites ``ADDON_LINE`` / ``ADDON_FOLDER`` in the
staged/AppData copy so each zip matches ``-Line`` / the git branch.

Source-tree default on ``outfits-stable`` / ``pre-map-importer`` is ``outfits``.

Hard separation (two Blender addons):
  outfits      -> AppData .../addons/DataRaiders-Outfits
  map-importer -> AppData .../addons/DataRaiders-MapImporter
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
