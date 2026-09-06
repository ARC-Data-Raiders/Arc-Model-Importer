"""Ground-truth UV / layering rules mined from Pioneer MaterialLibrary presets.

Sources (FModel JSON under MaterialLibrary/M_Presets + MaterialFunctions):
  - M_ArchitecturePreset_Concrete_01
  - M_ArchitecturePreset_Trim+CR+NAH / Trim+CR+NOM
  - M_ArchitecturePreset_Metal
  - M_PropTrimPreset_CR+NOM+AO
  - M_TrimMap_01

UV Mode (scalar): 0 = mesh UV (parent default), >=1 = WorldAlignedTexture.
Never treat a missing UV Mode override as world — MI inherits parent default 0.
"""
from __future__ import annotations

# Parent material → default UV policy (when MI does not override UV Mode).
PARENT_UV_DEFAULTS = {
    "architecturepreset_trim+cr+nah": "mesh",  # UV Mode default 0
    "architecturepreset_trim+cr+nom": "mesh",
    "proptrimpreset": "mesh",  # UV Mode default 0; atlas via UVOffset
    "trimmap_01": "mesh",  # layered metal; overlay tiling separate
    "architecturepreset_concrete": "mesh",  # WorldAligned available, gated by UV Mode
    "architecturepreset_metal": "mesh",  # Use World Aligned Material switch
}

# Scalar / switch names that affect BASE albedo UV (not overlay-only).
BASE_UV_OFFSET_KEYS_BY_KIND = {
    "proptrim": ("UVOffset", "UVOffset_U", "UVOffset_V"),
    "arch_trim": ("UV Offset Amount",),
    # Concrete UVOffset is paint/overlay-related — do not shift base CR.
    "concrete": (),
    "trim_mapper": (),
    "generic": (),
}
