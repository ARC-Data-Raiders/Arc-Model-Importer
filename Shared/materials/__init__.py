"""Domain-separated materials package (public API matches former materials.py)."""
from __future__ import annotations

# Subpackages (materials.native, materials.rebuild) import without bpy for tests.
import importlib.util as _importlib_util
if _importlib_util.find_spec("bpy") is not None:
    from .common import *  # noqa: F403
    from .classify import *  # noqa: F403
    from .clothing import *  # noqa: F403
    from .character import *  # noqa: F403
    from .enemy import *  # noqa: F403
    from .weapon import *  # noqa: F403
    from .environment import *  # noqa: F403
    from .items import *  # noqa: F403
    from .map_stage2 import *  # noqa: F403
    from .dispatch import *  # noqa: F403
    # Star-imports skip names starting with `_`. Operators / map_placement still
    # call these on the package (former materials.py monolith API).
    from .common import _parse_sk_material_slots, _uemodel_section_materials  # noqa: F401
    from .map_stage2 import _preferred_mi_is_single_slot_override  # noqa: F401
    from .benchmark_graph import COLOR_BENCHMARKS, BENCHMARK_IDS, resolve_benchmark, layout_benchmark_nodes, setup_benchmark_inspection_material, import_benchmark, import_benchmarks

    from .enemy import setup_enemy_material

__all__ = [n for n in globals() if not n.startswith('__')]
