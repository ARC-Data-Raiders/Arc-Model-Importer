"""Environment material reconstruction (architecture, trim, prop trim, surfaces).

Vendored from the ARC Texturer material profiles and the 1.42 parent-material
database. This package does not import any other Blender addon.
"""
from .materials import texture_objects
from .assets import get_index, infer_export_root

__all__ = ["texture_objects", "get_index", "infer_export_root"]
