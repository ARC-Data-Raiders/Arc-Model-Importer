import json

PROP = "arc_shader_tweaks"
TOL = 1e-5
VERSION = 1


def _norm(value):
    if isinstance(value, (int, float)):
        return float(value)
    return [float(v) for v in value]


def _equal(a, b):
    a = _norm(a)
    b = _norm(b)
    if isinstance(a, float) or isinstance(b, float):
        if not (isinstance(a, float) and isinstance(b, float)):
            return False
        return abs(a - b) <= TOL
    if len(a) != len(b):
        return False
    return all(abs(x - y) <= TOL for x, y in zip(a, b))


def get(root):
    raw = root.get(PROP) if hasattr(root, "get") else None
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store(root, data):
    data = {s: v for s, v in data.items() if v}
    if data:
        root[PROP] = json.dumps(data, sort_keys=True)
    elif PROP in root:
        del root[PROP]


def record(root, scope, name, value, default):
    data = get(root)
    if _equal(value, default):
        bucket = data.get(scope)
        if bucket and name in bucket:
            del bucket[name]
    else:
        data.setdefault(scope, {})[name] = _norm(value)
    _store(root, data)


def reset(root, scope=None, name=None):
    if scope is None:
        _store(root, {})
        return
    data = get(root)
    if name is None:
        data.pop(scope, None)
    elif scope in data:
        data[scope].pop(name, None)
    _store(root, data)


def apply(root, resolve):
    skipped = []
    for scope, inputs in get(root).items():
        for _material, node in resolve(scope):
            for name, value in inputs.items():
                sock = node.inputs.get(name)
                if sock is None:
                    if name not in skipped:
                        skipped.append(name)
                    continue
                sock.default_value = value
    return skipped


def to_json(root):
    return json.dumps({"version": VERSION, "tweaks": get(root)}, indent=2, sort_keys=True)


def from_json(root, text, replace=True):
    doc = json.loads(text)
    if not isinstance(doc, dict) or doc.get("version") != VERSION:
        raise ValueError("unsupported shader tweak preset")
    tweaks = doc.get("tweaks") or {}
    if not isinstance(tweaks, dict):
        raise ValueError("invalid tweaks")
    data = {} if replace else get(root)
    for scope, inputs in tweaks.items():
        data.setdefault(scope, {}).update({k: _norm(v) for k, v in inputs.items()})
    _store(root, data)
    return data


def sync_preview(root, src_scope, part_scopes):
    data = get(root)
    src = data.get(src_scope, {})
    lost = []
    for scope in part_scopes:
        if scope == src_scope:
            continue
        names = sorted(n for n, v in data.get(scope, {}).items()
                       if n not in src or not _equal(v, src[n]))
        if names:
            lost.append((scope, names))
    return lost


def sync(root, src_scope, part_scopes):
    data = get(root)
    src = data.get(src_scope, {})
    for scope in part_scopes:
        if scope != src_scope:
            data[scope] = dict(src)
    _store(root, data)


CW_PROP = "arc_colourways"


def colourways(root):
    """{name: {input: value}} stored on the asset root."""
    raw = root.get(CW_PROP) if hasattr(root, "get") else None
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store_cw(root, data):
    if data:
        root[CW_PROP] = json.dumps(data, sort_keys=True)
    elif CW_PROP in root:
        del root[CW_PROP]


def save_colourway(root, name, values):
    """Store input values under name (replaces an existing one)."""
    if not name:
        raise ValueError("colourway needs a name")
    data = colourways(root)
    data[name] = {k: _norm(v) for k, v in values.items()}
    _store_cw(root, data)
    return data[name]


def load_colourway(root, name):
    """Input values of a saved colourway, or None."""
    return colourways(root).get(name)


def delete_colourway(root, name):
    data = colourways(root)
    found = data.pop(name, None) is not None
    _store_cw(root, data)
    return found


def colourways_to_json(root, names=None):
    data = colourways(root)
    if names is not None:
        data = {n: v for n, v in data.items() if n in names}
    return json.dumps({"version": VERSION, "colourways": data}, indent=2, sort_keys=True)


def colourways_from_json(root, text):
    """Merge colourways from JSON; returns imported names."""
    doc = json.loads(text)
    if not isinstance(doc, dict) or doc.get("version") != VERSION:
        raise ValueError("unsupported colourway file")
    incoming = doc.get("colourways") or {}
    if not isinstance(incoming, dict):
        raise ValueError("invalid colourways")
    data = colourways(root)
    for name, values in incoming.items():
        if not isinstance(values, dict):
            raise ValueError(f"invalid colourway {name}")
        data[name] = {k: _norm(v) for k, v in values.items()}
    _store_cw(root, data)
    return sorted(incoming)
