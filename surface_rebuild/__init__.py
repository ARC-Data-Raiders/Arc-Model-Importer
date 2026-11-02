"""Attributed physical material profiles with original DR graph construction.

Retained ARC Texturer profile recipes keep their authorship credit. Defaults now
come from exact current local native exports or the reviewed versioned native
catalog; explicitly uncertain FModel compatibility never opens historical banks.
"""
from .materials import texture_objects
from .assets import get_index, infer_export_root

__all__ = ["texture_objects", "get_index", "infer_export_root"]
