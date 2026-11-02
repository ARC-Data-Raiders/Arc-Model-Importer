"""Runtime Niagara parameters the cook does not hold, read offline from the user-made 2.3.0 minidump
(assets/niagara_dump_params_2.3.0.json): NPC_Global_* parameter collection values and the User. overrides
that placed components and gameplay cues set on each system. Returns BakeInputs.parameter_overrides dicts."""
import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'assets' / 'niagara_dump_params_2.3.0.json'


@lru_cache(maxsize=1)
def load(path=None):
    p = Path(path) if path else DATA
    return json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}


def _stem(system):
    return Path(str(system).split('.')[0]).stem


def system_overrides(system, data=None):
    data = data or load()
    stem = _stem(system)
    for key, entry in (data.get('systems') or {}).items():
        if _stem(key) == stem:
            return dict(entry['overrides'])
    return {}


def collection_values(data=None, live=True):
    """live: the in-map snapshot (wind, FX_Emissive_Intensity, exclusion volumes) minus player-dependent values."""
    data = data or load()
    skip = set(data.get('player_dependent') or ())
    out = {}
    for sets in (data.get('collections') or {}).values():
        values = sets.get('live' if live else 'asset') or sets.get('asset') or {}
        out.update({k: v for k, v in values.items() if not (live and k in skip)})
    if live:
        for sets in (data.get('collections') or {}).values():
            out.update({k: v for k, v in (sets.get('asset') or {}).items() if k in skip})
    return out


def overrides_for(system, live_globals=True, data=None):
    data = data or load()
    out = collection_values(data, live_globals)
    out.update(system_overrides(system, data))
    return out
