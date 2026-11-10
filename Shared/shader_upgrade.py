import re

STAMP = "arc_gt_ver"
_HASH = re.compile(r"^[0-9a-fA-F]{12}$")


def base_name(name):
    head, sep, tail = name.rpartition(".")
    if sep and _HASH.match(tail):
        return head
    return name


def _stamp(group):
    try:
        value = group.get(STAMP)
    except AttributeError:
        value = None
    return value


def stale_groups(node_groups, current):
    out = []
    for group in node_groups:
        base = base_name(group.name)
        if base not in current:
            continue
        ver = _stamp(group)
        if ver is None or ver < current[base]:
            out.append(group)
    return out


def _tree_input_names(tree):
    iface = getattr(tree, "interface", None)
    if iface is not None:
        return [i.name for i in iface.items_tree
                if getattr(i, "item_type", "SOCKET") == "SOCKET" and getattr(i, "in_out", "INPUT") == "INPUT"]
    return [s.name for s in tree.inputs]


def _copy(value):
    try:
        return list(value)
    except TypeError:
        return value


def plan_upgrade(materials, stale, build_new):
    stale_set = {id(g) for g in stale}
    built = {}
    plan = []
    for mat in materials:
        tree = getattr(mat, "node_tree", None)
        if tree is None:
            continue
        for node in tree.nodes:
            old = getattr(node, "node_tree", None)
            if old is None or id(old) not in stale_set:
                continue
            if id(old) not in built:
                built[id(old)] = build_new(old)
            new = built[id(old)]
            names = set(_tree_input_names(new))
            carried = {}
            dropped = []
            for sock in node.inputs:
                if not hasattr(sock, "default_value"):
                    continue
                if sock.name in names:
                    carried[sock.name] = _copy(sock.default_value)
                else:
                    dropped.append(sock.name)
            plan.append((mat, node, old, new, carried, dropped))
    return plan


def apply_upgrade(plan):
    for _mat, node, _old, new, carried, _dropped in plan:
        node.node_tree = new
        for name, value in carried.items():
            sock = node.inputs.get(name)
            if sock is not None:
                sock.default_value = value
    return len(plan)


# --- E6: version table from builder source (read-only parse) ---------------
# Follow-up for O1d: export a builder-side GT_GROUP_VERSIONS table and drop this parse.
_FUNC = re.compile(r"^def (\w+)\(([^)]*)\)", re.M)
_NAME = re.compile(r"""\bname\s*=\s*(f?)["']([^"'{]+)(\{?)""")
_FRESH = re.compile(r"""_gt_ng_fresh\(\s*(name|["']([^"']+)["'])\s*,\s*(\d+)\s*\)""")
_TABLE = {}


def parse_versions(src):
    """{key: (ver, builder, needs_args)}; f-string names give prefix keys ending '*'."""
    table = {}
    heads = list(_FUNC.finditer(src))
    for i, m in enumerate(heads):
        body = src[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(src)]
        f = _FRESH.search(body)
        if not f:
            continue
        if f.group(2):
            key = f.group(2)
        else:
            n = _NAME.search(body[: f.start()])
            if not n:
                continue
            key = n.group(2) + ("*" if n.group(3) else "")
        args = [a for a in m.group(2).split(",") if a.strip()]
        table[key] = (int(f.group(3)), m.group(1), bool(args))
    return table


def current_versions(path=None):
    import os
    path = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Shared", "materials", "gt_outfit_pbr.py")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    hit = _TABLE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path, encoding="utf-8") as fh:
        table = parse_versions(fh.read())
    _TABLE[path] = (mtime, table)
    return table


def lookup(base, table):
    if base in table:
        return base, table[base]
    for key, val in table.items():
        if key.endswith("*") and base.startswith(key[:-1]):
            return key, val
    return None, None


def scan(node_groups, table):
    """[(group, key, have_ver, want_ver, builder, needs_args)] for outdated groups."""
    out = []
    for group in node_groups:
        try:
            legacy = group.get("dr.legacy_group_key")
        except AttributeError:
            legacy = None
        key, val = lookup(legacy or base_name(group.name), table)
        if val is None:
            continue
        have = _stamp(group)
        if have is None or have < val[0]:
            out.append((group, key, have, val[0], val[1], val[2]))
    return out


def upgrade_groups(node_groups, materials, outdated, builders, find_group):
    """Rebuild each no-arg outdated group via its builder, carry inputs by name, remap users.

    Returns (upgraded_names, skipped_names). One caller op = one undo step.
    """
    built, done, skipped, olds = {}, [], [], []
    for group, key, _have, _want, builder, needs_args in outdated:
        fn = getattr(builders, builder, None)
        if needs_args or fn is None or key.endswith("*"):
            skipped.append(group.name)
            continue
        old_name = group.name
        group.name = old_name + ".arc_old"
        fn()
        new = find_group(node_groups, key)
        if new is None or new is group:
            group.name = old_name
            skipped.append(old_name)
            continue
        built[id(group)] = new
        olds.append(group)
        done.append(old_name)
    plan = plan_upgrade(materials, olds, lambda old: built[id(old)])
    apply_upgrade(plan)
    for old in olds:
        old.user_remap(built[id(old)])
        if old.users == 0:
            node_groups.remove(old)
    return done, skipped


def cache_key(node_groups):
    """(count, stamps) so any arc_gt_ver change invalidates a cached scan."""
    stamps = tuple(_stamp(g) for g in node_groups)
    return len(stamps), stamps
