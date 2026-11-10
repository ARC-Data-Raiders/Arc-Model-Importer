"""DataRaiders graph backend compatibility entry point.

Graph compilation, semantic stages, layout and naming are implemented by our
materials.native backend. Retained family profiles and historical third-party
metadata keep their existing provenance and attribution in their own modules.
This interface migration does not assert those retained sources are clean-room.
"""
from ..native.interop import Graph, arrange

__all__ = ['Graph', 'arrange']
