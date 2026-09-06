"""Domain-separated materials package (public API matches former materials.py)."""
from __future__ import annotations

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
from .common import _parse_sk_material_slots  # noqa: F401
from .map_stage2 import _preferred_mi_is_single_slot_override  # noqa: F401
from .benchmark_graph import (  # noqa: F401
    COLOR_BENCHMARKS,
    BENCHMARK_IDS,
    resolve_benchmark,
    layout_benchmark_nodes,
    setup_benchmark_inspection_material,
    import_benchmark,
    import_benchmarks,
)

from .enemy import setup_enemy_material

__all__ = [n for n in globals() if not n.startswith('__')]
