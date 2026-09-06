"""Environmental prop feature modules (trims, world-space paint, …).

Keep subtype-specific logic here instead of growing ``_setup_environment_material``.
Stage 2 / classify still own family routing; these helpers refine UV + overlays.
"""
from __future__ import annotations

from .detect import (
    ENV_PROPS_SETUP_V,
    detect_env_prop_kind,
    is_architecture_trim_kind,
    is_proptrim_atlas_mi,
    is_trim_mapper_mi,
    wants_world_paint,
)
from .parent_infer import (
    PRESET_ARCH_TRIM,
    PRESET_CONCRETE,
    PRESET_PROPTRIM,
    PRESET_TRIMMAP,
    infer_preset_key,
    merge_preset_defaults,
    tint_multiplier,
)
from .trim_map import TRIMMAP_SETUP_V, setup_trim_map_material
from .weathering import apply_top_down_dust, apply_weathering, load_library_image
from .world_paint import (
    PAINT_BREAKUP_TEXTURE,
    apply_world_space_paint,
    infer_paint_unit_scale,
    mesh_local_ue_cm_to_position_units,
    mesh_ue_cm_to_position_units,
    paint_band,
)

__all__ = (
    "ENV_PROPS_SETUP_V",
    "PAINT_BREAKUP_TEXTURE",
    "PRESET_ARCH_TRIM",
    "PRESET_CONCRETE",
    "PRESET_PROPTRIM",
    "PRESET_TRIMMAP",
    "TRIMMAP_SETUP_V",
    "apply_top_down_dust",
    "apply_weathering",
    "apply_world_space_paint",
    "detect_env_prop_kind",
    "infer_paint_unit_scale",
    "infer_preset_key",
    "is_architecture_trim_kind",
    "is_proptrim_atlas_mi",
    "is_trim_mapper_mi",
    "load_library_image",
    "merge_preset_defaults",
    "mesh_local_ue_cm_to_position_units",
    "mesh_ue_cm_to_position_units",
    "paint_band",
    "setup_trim_map_material",
    "tint_multiplier",
    "wants_world_paint",
)
