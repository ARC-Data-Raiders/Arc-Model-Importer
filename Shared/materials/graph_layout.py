"""Deterministic top-level tidy pass for built material trees.

Columns, left to right: Textures | Game Logic | User Controls | Master | Output.
Each column is a labelled NodeFrame. Existing frames stay intact as blocks; every
container (top level and each frame) is first swept so no two direct items overlap,
then blocks are shelf-packed into their column. Locations go out in one foreach_set
per tree, because a per-node location write re-runs the whole node-tree update.

Background mode has no ``node.dimensions``, so box heights come from visible sockets.
"""

LAYOUT_VER = 1
GAP = 40.0
COLUMN_GAP = 160.0
SHELF_MAX = 6000.0
COLUMNS = (
    ("DR.Layout.Textures", "Textures"),
    ("DR.Layout.GameLogic", "Game Logic"),
    ("DR.Layout.UserControls", "User Controls"),
    ("DR.Layout.Master", "Master"),
    ("DR.Layout.Output", "Output"),
)
_TEXTURE_STAGES = ("DR.LayerInputs", "DR.Source", "DR.Coordinates")
_TEXTURE_TYPES = {"TEX_IMAGE", "TEX_COORD", "UVMAP", "MAPPING"}
_EXTRA = {"ShaderNodeTexImage": 170, "ShaderNodeRGB": 170, "ShaderNodeMath": 50, "ShaderNodeVectorMath": 30,
          "ShaderNodeMix": 60, "ShaderNodeMixRGB": 30, "ShaderNodeMapping": 30, "ShaderNodeSeparateColor": 30,
          "ShaderNodeCombineColor": 30, "ShaderNodeBsdfPrincipled": 60, "ShaderNodeGroup": 30,
          "ShaderNodeMapRange": 60, "ShaderNodeClamp": 30, "ShaderNodeNormalMap": 50,
          "ShaderNodeValToRGB": 130}


def estimate_height(n):
    """Drawn height estimate from visible sockets."""
    if n.hide:
        return 32.0
    rows = sum(1 for s in n.outputs if s.enabled and not s.hide)
    for s in n.inputs:
        if not s.enabled or s.hide:
            continue
        rows += 3 if (s.type == "VECTOR" and not s.is_linked and not s.hide_value) else 1
    return 36.0 + 22.0 * rows + _EXTRA.get(n.bl_idname, 20)


def tidy_image_nodes(nodes):
    """Hide unlinked sockets on image nodes and label each with its MI parameter name."""
    for n in nodes:
        if n.bl_idname != "ShaderNodeTexImage":
            continue
        for s in n.outputs:
            if not s.is_linked and not s.hide:
                s.hide = True
        for s in n.inputs:
            if not s.is_linked and not s.hide:
                s.hide = True
        param = n.get("dr.texture_param") or n.get("dr.texture_index_param")
        if not param:
            label = n.label or ""
            param = label.split(" ", 1)[0] if (" " in label and (":" in label or "/" in label or "\\" in label)) else ""
        if param and n.label != param:
            n.label = str(param)[:63]


class _Item:
    __slots__ = ("node", "x", "y", "w", "h", "dx", "dy")

    def __init__(self, node, x, y, w, h):
        self.node, self.x, self.y, self.w, self.h = node, x, y, w, h
        self.dx = self.dy = 0.0


def _sweep(items):
    """Remove overlaps among one container's direct items, keeping their reading order."""
    if not items:
        return
    items.sort(key=lambda it: (it.x, -it.y))
    clusters, cur = [], [items[0]]
    for it in items[1:]:
        if it.x - cur[0].x <= GAP:
            cur.append(it)
        else:
            clusters.append(cur)
            cur = [it]
    clusters.append(cur)
    shift, prev_right = 0.0, None
    for cl in clusters:
        x0 = cl[0].x + shift
        if prev_right is not None and x0 < prev_right + GAP:
            shift += prev_right + GAP - x0
        right, bottom = None, None
        for it in sorted(cl, key=lambda it: -it.y):
            it.dx = shift
            top = it.y
            if bottom is not None and top > bottom - GAP:
                top = bottom - GAP
            it.dy = top - it.y
            bottom = top - it.h
            r = it.x + shift + it.w
            right = r if right is None else max(right, r)
        prev_right = right


def _tidy_tree_v1(nt):
    """Frame every top-level item into labelled columns with no overlapping nodes."""
    nodes = nt.nodes
    count = len(nodes)
    if not count:
        return False
    tidy_image_nodes(nodes)
    flat = [0.0] * (2 * count)
    nodes.foreach_get("location", flat)
    index = {n.as_pointer(): i for i, n in enumerate(nodes)}
    loc = {p: [flat[2 * i], flat[2 * i + 1]] for p, i in index.items()}
    kids = {}
    top = []
    for n in nodes:
        if n.bl_idname == "NodeFrame" and n.name in _COLUMN_NAMES:
            continue
        par = n.parent
        if par is not None and par.name in _COLUMN_NAMES:
            par = None
        if par is None:
            top.append(n)
        else:
            kids.setdefault(par.as_pointer(), []).append(n)

    kinds = {}

    def box(n):
        """Return (minx, maxx, miny, maxy) of n in its parent's space, after sweeping its contents."""
        p = n.as_pointer()
        x, y = loc[p]
        if n.bl_idname != "NodeFrame":
            kinds[p] = {n.type}
            return x, x + float(n.width), y - estimate_height(n), y
        children = kids.get(p, [])
        kinds[p] = set()
        if not children:
            w = float(getattr(n, "width", 0.0) or 0.0)
            h = float(getattr(n, "height", 0.0) or 0.0)
            return x, x + w, y - h, y
        items = []
        for c in children:
            b = box(c)
            kinds[p] |= kinds[c.as_pointer()]
            items.append(_Item(c, b[0], b[3], b[1] - b[0], b[3] - b[2]))
        _sweep(items)
        lo_x = lo_y = 1e18
        hi_x = hi_y = -1e18
        for it in items:
            cp = it.node.as_pointer()
            loc[cp][0] += it.dx
            loc[cp][1] += it.dy
            lo_x = min(lo_x, it.x + it.dx); hi_x = max(hi_x, it.x + it.dx + it.w)
            hi_y = max(hi_y, it.y + it.dy); lo_y = min(lo_y, it.y + it.dy - it.h)
        if getattr(n, "shrink", True) is False:
            lo_x, lo_y = min(lo_x, 0.0), min(lo_y, -float(n.height))
        return x + lo_x, x + hi_x, y + lo_y, y + hi_y

    blocks = [[] for _ in COLUMNS]
    for n in top:
        b = box(n)
        k = kinds[n.as_pointer()]
        if n.type == "GROUP" and (n.get("arc_user_controls") or (n.node_tree and n.node_tree.name.startswith("DR.UserControls"))):
            col = 2
        elif "BSDF_PRINCIPLED" in k or (n.type == "GROUP" and n.node_tree is not None
                                          and n.node_tree.get("independent_owned_graph")):
            col = 3
        elif "OUTPUT_MATERIAL" in k and len(k) <= 2:
            col = 4
        elif n.bl_idname == "NodeFrame" and n.name.startswith(_TEXTURE_STAGES):
            col = 0
        elif k and k <= _TEXTURE_TYPES:
            col = 0
        else:
            col = 1
        blocks[col].append(_Item(n, b[0], b[3], b[1] - b[0], b[3] - b[2]))

    frames = {f.name: f for f in nodes if f.bl_idname == "NodeFrame" and f.name in _COLUMN_NAMES}
    reparent = []
    x_cursor = 0.0
    placed_cols = []
    for (fname, label), items in zip(COLUMNS, blocks):
        fr = frames.get(fname)
        if not items:
            if fr is not None:
                nodes.remove(fr)
            continue
        if fr is None:
            fr = nodes.new("NodeFrame")
            fr.name = fname
            fr.label_size = 24
            fr["dr.layout_column"] = LAYOUT_VER
        fr.label = label
        fr.shrink = True
        items.sort(key=lambda it: (it.x, -it.y))
        cap = max(SHELF_MAX, max(it.h for it in items))
        cx, cy, colw = 0.0, 0.0, 0.0
        for it in items:
            if cy and cy + it.h > cap:
                cx += colw + GAP
                cy, colw = 0.0, 0.0
            # Place the block's top-left at (cx, -cy) in column space.
            p = it.node.as_pointer()
            loc[p][0] += cx - it.x
            loc[p][1] += -cy - it.y
            cy += it.h + GAP
            colw = max(colw, it.w)
            if it.node.parent is not fr:
                reparent.append((it.node, fr))
        placed_cols.append((fr, x_cursor))
        x_cursor += cx + colw + COLUMN_GAP
    for n, fr in reparent:
        n.parent = fr
    # Column frames sit at the origin of their column; children are frame-relative.
    flat = [0.0] * (2 * len(nodes))
    nodes.foreach_get("location", flat)
    col_at = {fr.as_pointer(): x for fr, x in placed_cols}
    for i, n in enumerate(nodes):
        p = n.as_pointer()
        if p in col_at:
            flat[2 * i], flat[2 * i + 1] = col_at[p], 0.0
        elif p in loc:
            flat[2 * i], flat[2 * i + 1] = loc[p]
    nodes.foreach_set("location", flat)
    nt["dr.layout_ver"] = LAYOUT_VER
    return True


_COLUMN_NAMES = frozenset(name for name, _label in COLUMNS)


# ---------------------------------------------------------------------------
# U2 tagging API (graph design contract). Builders tag nodes; finalize_tree lays
# out tagged and untagged trees alike. Other modules guard calls with getattr.
# ---------------------------------------------------------------------------
TAG_STAGE = "arc_stage"
TAG_ZONE = "arc_zone"
TAG_ROLE = "arc_role"

# Stage name -> (column rank, role). Rank orders frames left to right.
STAGES = (
    ("Textures", "inputs"),
    ("Inputs", "inputs"),
    ("Coordinates", "inputs"),
    ("Controls", "controls"),
    ("Mask", "mask"),
    ("Selection", "mask"),
    ("Base", "colour"),
    ("Colour", "colour"),
    ("Pattern and Detail", "pattern"),
    ("Detail", "pattern"),
    ("Decals", "pattern"),
    ("Roughness/Metal", "surface"),
    ("Surface", "surface"),
    ("Normal", "surface"),
    ("Zones", "colour"),
    ("Combine Zones", "colour"),
    ("Shared Surface", "weathering"),
    ("Weathering", "weathering"),
    ("Look", "surface"),
    ("Zone Output", "output"),
    ("Output", "output"),
)
STAGE_RANK = {name: i for i, (name, _role) in enumerate(STAGES)}
STAGE_ROLE = dict(STAGES)

ROLE_COLORS = {
    "inputs": (0.20, 0.25, 0.35),
    "mask": (0.32, 0.27, 0.12),
    "colour": (0.36, 0.18, 0.18),
    "pattern": (0.30, 0.20, 0.35),
    "surface": (0.20, 0.30, 0.30),
    "weathering": (0.35, 0.25, 0.15),
    "controls": (0.15, 0.35, 0.20),
    "output": (0.25, 0.25, 0.25),
}
ZONE_SHADES = ((0.22, 0.22, 0.24), (0.28, 0.28, 0.30))


def tag(node, stage=None, zone=None, role=None, label=None):
    """Record layout intent on a node; finalize_tree reads it. Returns the node."""
    if node is None:
        return node
    try:
        if stage:
            node[TAG_STAGE] = str(stage)
        if zone:
            node[TAG_ZONE] = str(zone)
        if role:
            node[TAG_ROLE] = str(role)
        if label:
            node.label = str(label)[:63]
    except (TypeError, AttributeError):
        pass
    return node


LAYOUT_VER2 = 2
VER_KEY = "arc_layout_ver"
SIG_KEY = "arc_layout_sig"
BUS_TAG = "arc_bus"
ROW_GAP = 40.0
COL_GAP = 160.0
SUB_GAP = 60.0
WRAP_H = 3000.0
BODY_MAX_W = 6000.0
LANE_GAP = 120.0
LABEL_PAD = 50.0
BUS_ROW = 40.0
FRAME_LABEL_SIZE = 20
BUS_SPAN = 2
FALLBACK_STAGE = "Logic"
BUS_FRAME = "Shared Signals"
STUB_FRAME = "Placeholder: no game material found"
FRAME_PREFIX = "Arc Frame \u00b7 "
_HELPER_SUFFIX = ("Math", "Mix", "MixRGB", "MapRange", "Group")
_INPUT_TYPES = {"TEX_IMAGE", "TEX_COORD", "UVMAP", "MAPPING", "ATTRIBUTE", "VERTEX_COLOR", "TEX_ENVIRONMENT"}
_OUTPUT_TYPES = {"OUTPUT_MATERIAL", "GROUP_OUTPUT", "OUTPUT_WORLD", "OUTPUT_LIGHT", "OUTPUT_AOV", "COMPOSITE", "VIEWER"}
_STAGE_PREFIXES = (("DR.LayerInputs", "Textures"), ("DR.Source", "Textures"), ("DR.Coordinates", "Coordinates"),
                   ("DR.UserControls", "Controls"), ("DR.Layout.Textures", "Textures"),
                   ("DR.Layout.UserControls", "Controls"), ("DR.Layout.Output", "Output"))
_layout_cache = {}


def _clean_name(text):
    """Plain display name: no DR. prefix, no hash or .00N suffix."""
    import re
    s = str(text or "")
    for p in ("DR.", "DR \u00b7 ", "Arc "):
        if s.startswith(p):
            s = s[len(p):]
    s = re.sub(r"\.\d{3,}$", "", s)
    s = re.sub(r"[\s\u00b7._-]+[0-9a-f]{6,}$", "", s)
    s = s.replace("DR.", "").replace("_", " ").strip(" \u00b7.")
    return s


def helper_label(n):
    """Readable label for a Math/Mix/MapRange/Group helper with none."""
    bid = n.bl_idname
    if bid.endswith("Group"):
        tree = getattr(n, "node_tree", None)
        return _clean_name(tree.name if tree is not None else "") or "Group"
    if bid.endswith("MapRange"):
        return "Map Range"
    op = getattr(n, "operation", None) or getattr(n, "blend_type", None)
    if bid.endswith("Mix") and getattr(n, "data_type", "RGBA") != "RGBA":
        op = "Mix " + str(getattr(n, "data_type", "")).title()
    elif bid.endswith(("Mix", "MixRGB")):
        op = "Mix " + str(op or "").replace("_", " ").title()
    return (str(op or bid).replace("_", " ").title()).strip() or "Helper"


def _is_helper(n):
    return n.bl_idname.endswith(_HELPER_SUFFIX) and not (n.label or "").strip()


def _ui_scale():
    try:
        import bpy
        return float(bpy.context.preferences.system.ui_scale or 1.0)
    except Exception:
        return 1.0


def node_size(n, scale=1.0):
    """(width, height) in view units: drawn size when known, else the socket estimate."""
    w = h = 0.0
    try:
        d = n.dimensions
        w, h = float(d[0]) / scale, float(d[1]) / scale
    except Exception:
        pass
    if n.bl_idname == "NodeReroute":
        return 16.0, 16.0
    if w <= 0.0:
        w = float(getattr(n, "width", 140.0) or 140.0)
    if h <= 0.0:
        h = max(estimate_height(n), 100.0)
    return w, h


def content_signature(tree):
    """Hash of nodes, parents, rounded locations and links; changes when anyone edits the tree."""
    import hashlib
    nodes = tree.nodes
    flat = [0.0] * (2 * len(nodes))
    nodes.foreach_get("location", flat)
    h = hashlib.sha1()
    for i, n in enumerate(nodes):
        par = n.parent.name if n.parent is not None else ""
        h.update(("%s|%s|%s|%d|%d;" % (n.name, n.bl_idname, par, round(flat[2 * i]), round(flat[2 * i + 1]))).encode())
    for l in tree.links:
        if l.from_node is None or l.to_node is None:
            continue
        h.update(("%s.%s>%s.%s;" % (l.from_node.name, l.from_socket.identifier, l.to_node.name,
                                    l.to_socket.identifier)).encode())
    return h.hexdigest()[:16]


def is_user_edited(tree):
    """True when the tree was laid out by us and changed since."""
    sig = tree.get(SIG_KEY) if hasattr(tree, "get") else None
    return bool(sig) and sig != content_signature(tree)


def _abs_xy(n, flat, index):
    la = getattr(n, "location_absolute", None)
    if la is not None:
        return float(la[0]), float(la[1])
    i = index[n.as_pointer()]
    x, y = flat[2 * i], flat[2 * i + 1]
    p = n.parent
    while p is not None:
        j = index.get(p.as_pointer())
        if j is None:
            break
        x += flat[2 * j]
        y += flat[2 * j + 1]
        p = p.parent
    return x, y


def _dissolve_bus(tree):
    """Reconnect targets of our bus reroutes to the real source and drop the reroutes."""
    out = [0]
    for _ in _dissolve_bus_steps(tree, out):
        pass
    return out[0]


def _dissolve_bus_steps(tree, out):
    """_dissolve_bus in slices: each RNA node/link op re-runs a tree update (~3 ms on a 750-node group)."""
    nodes, links = tree.nodes, tree.links
    bus = [n for n in nodes if n.bl_idname == "NodeReroute" and n.get(BUS_TAG)]
    out[0] = len(bus)
    if not bus:
        return
    busp = {n.as_pointer() for n in bus}

    def source(r):
        seen = set()
        while r is not None and r.as_pointer() in busp and r.as_pointer() not in seen:
            seen.add(r.as_pointer())
            ls = r.inputs[0].links
            if not ls:
                return None
            sock = ls[0].from_socket
            r = ls[0].from_node
            if r.as_pointer() not in busp:
                return sock
        return None

    pairs = []
    for r in bus:
        src = source(r)
        for l in list(r.outputs[0].links):
            if l.to_node.as_pointer() not in busp:
                pairs.append((src, l.to_socket))
    for k, r in enumerate(bus):
        nodes.remove(r)
        if k % STEP_OPS == STEP_OPS - 1:
            yield
    for k, (src, dst) in enumerate(pairs):
        if src is not None:
            links.new(src, dst)
        if k % STEP_OPS == STEP_OPS - 1:
            yield


def _zone_norm(z):
    zs = str(z) if z else ""
    return zs if not zs or zs.lower().startswith("zone") else "Zone " + zs


def _own_zone(n):
    """Zone of the group tree that holds n (inside a zone group the zone tag is no lane)."""
    try:
        return _zone_norm(n.id_data.get(TAG_ZONE))
    except Exception:
        return ""


def _frame_key(n, frame_of, consumers):
    """Frame (lane) name for a node: tag, existing frame, type, name prefix; None when unknown."""
    z = n.get(TAG_ZONE) if hasattr(n, "get") else None
    if z and _zone_norm(z) != _own_zone(n):
        return str(z)
    s = n.get(TAG_STAGE) if hasattr(n, "get") else None
    if s:
        return str(s)
    fr = frame_of.get(n.as_pointer())
    if fr:
        return fr
    t = n.type
    if t == "GROUP":
        tree = getattr(n, "node_tree", None)
        if n.get("arc_user_controls") or (tree is not None and tree.name.startswith("DR.UserControls")):
            return "Controls"
        if tree is not None and tree.get("independent_owned_graph") and not consumers:
            return "Look"
    if t in _INPUT_TYPES:
        return "Textures"
    if t == "GROUP_INPUT":
        return "Inputs"
    if t in _OUTPUT_TYPES or t.startswith("BSDF_") or t in ("EMISSION", "ADD_SHADER", "MIX_SHADER"):
        return "Output"
    for pre, stage in _STAGE_PREFIXES:
        if n.name.startswith(pre) or (n.label or "").startswith(pre):
            return stage
    return None


def _tarjan(keys, adj):
    index, low, on, stack, comp, out = {}, {}, set(), [], {}, []
    counter = [0]

    def visit(v):
        work = [(v, iter(adj.get(v, ())))]
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        while work:
            u, it = work[-1]
            pushed = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(adj.get(w, ()))))
                    pushed = True
                    break
                if w in on:
                    low[u] = min(low[u], index[w])
            if pushed:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[u])
            if low[u] == index[u]:
                cid = len(out)
                members = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp[w] = cid
                    members.append(w)
                    if w == u:
                        break
                out.append(members)

    for k in keys:
        if k not in index:
            visit(k)
    return comp, out


def _longest_layers(items, preds, succs):
    """Longest-path layers over a DAG (cycle leftovers go to layer 0), then pull sources right."""
    indeg = {v: 0 for v in items}
    for v in items:
        for u in preds.get(v, ()):
            if u in indeg:
                indeg[v] += 1
    layer = {v: 0 for v in items}
    queue = [v for v in items if indeg[v] == 0]
    order = []
    while queue:
        u = queue.pop()
        order.append(u)
        for w in succs.get(u, ()):
            if w in indeg:
                layer[w] = max(layer[w], layer[u] + 1)
                indeg[w] -= 1
                if indeg[w] == 0:
                    queue.append(w)
    for v in reversed(order):
        if any(u in indeg for u in preds.get(v, ())):
            continue
        nxt = [layer[w] for w in succs.get(v, ()) if w in indeg]
        if nxt:
            layer[v] = max(layer[v], min(nxt) - 1)
    return layer


def _natural(text):
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(text))]


# RNA node/link ops per slice in the step forms: one op costs ~0.2 ms on a small tree and
# ~3 ms on a 750-node outfit group, so 24 ops stay well under a 0.1 s main-thread block.
STEP_OPS = 24


def layout_tree(tree, family=None):
    """Lay out one tree to the graph contract. Returns per-tree stats."""
    stats = {"tree": tree.name, "nodes": 0, "frames": 0, "bus_reroutes": 0, "labels": 0}
    for _ in _layout_steps(tree, family, stats):
        pass
    return stats


def _layout_steps(tree, family, stats):
    """layout_tree as a generator that yields between slices of RNA ops (modal finishing)."""
    nodes, links = tree.nodes, tree.links
    if not len(nodes):
        return
    try:
        tidy_image_nodes(nodes)
    except Exception:
        pass
    yield from _dissolve_bus_steps(tree, [0])
    yield
    scale = _ui_scale()
    all_nodes = list(nodes)
    count = len(all_nodes)
    flat = [0.0] * (2 * count)
    nodes.foreach_get("location", flat)
    index = {n.as_pointer(): i for i, n in enumerate(all_nodes)}
    absxy = {n.as_pointer(): _abs_xy(n, flat, index) for n in all_nodes}
    old_frames = [n for n in all_nodes if n.bl_idname == "NodeFrame"]
    body = [n for n in all_nodes if n.bl_idname != "NodeFrame"]
    frame_of = {}
    for n in body:
        p = n.parent
        while p is not None and (p.name in _COLUMN_NAMES or p.name == BUS_FRAME):
            p = p.parent
        if p is not None:
            lab = (p.label or "").strip() or _clean_name(p.name)
            if lab.startswith(FRAME_PREFIX):
                lab = lab[len(FRAME_PREFIX):]
            frame_of[n.as_pointer()] = _clean_name(lab) if lab.startswith("DR") else lab
    ptr = [n.as_pointer() for n in body]
    pos_of = {p: i for i, p in enumerate(ptr)}
    preds = {i: set() for i in range(len(body))}
    succs = {i: set() for i in range(len(body))}
    for l in links:
        a = pos_of.get(l.from_node.as_pointer()) if l.from_node is not None else None
        b = pos_of.get(l.to_node.as_pointer()) if l.to_node is not None else None
        if a is None or b is None or a == b:
            continue
        succs[a].add(b)
        preds[b].add(a)
    keys = [_frame_key(n, frame_of, succs[i]) for i, n in enumerate(body)]
    # Unknown nodes: reroutes and constants follow their consumer; the rest join one plain lane.
    for _pass in range(3):
        for i, n in enumerate(body):
            if keys[i] is not None:
                continue
            if n.bl_idname == "NodeReroute" or not preds[i]:
                near = [keys[j] for j in succs[i] if keys[j] is not None] or \
                       [keys[j] for j in preds[i] if keys[j] is not None]
                if near:
                    keys[i] = max(set(near), key=near.count)
    keys = [k or FALLBACK_STAGE for k in keys]
    if len(body) <= 2 and getattr(tree, "is_embedded_data", False) and any(n.type == "OUTPUT_MATERIAL" for n in body):
        keys = [STUB_FRAME] * len(body)
    yield
    for i, n in enumerate(body):
        if _is_helper(n):
            n.label = helper_label(n)[:63]
            stats["labels"] += 1
            if stats["labels"] % STEP_OPS == 0:
                yield
    yield
    # Frame graph -> strongly connected components -> bands by longest path.
    fkeys = sorted(set(keys), key=lambda k: (STAGE_RANK.get(k, len(STAGES) // 2), _natural(k)))
    fadj = {k: set() for k in fkeys}
    for a in range(len(body)):
        for b in succs[a]:
            if keys[a] != keys[b]:
                fadj[keys[a]].add(keys[b])
    comp, comps = _tarjan(fkeys, fadj)
    cadj, cpred = {}, {}
    for a, outs in fadj.items():
        for b in outs:
            if comp[a] != comp[b]:
                cadj.setdefault(comp[a], set()).add(comp[b])
                cpred.setdefault(comp[b], set()).add(comp[a])
    clayer = _longest_layers(list(range(len(comps))), cpred, cadj)
    # Pure stage order where the topology says nothing: Output never left of its feeders.
    band_of = {k: clayer[comp[k]] for k in fkeys}
    nbands = max(band_of.values()) + 1 if band_of else 1
    lanes = [[k for k in fkeys if band_of[k] == b] for b in range(nbands)]
    # Node layers inside each band, over that band's own edges.
    layer = {}
    for b in range(nbands):
        members = [i for i in range(len(body)) if band_of[keys[i]] == b]
        mset = set(members)
        lp = {i: [j for j in preds[i] if j in mset] for i in members}
        ls = {i: [j for j in succs[i] if j in mset] for i in members}
        layer.update(_longest_layers(members, lp, ls))
    ncols = [max([layer[i] for i in range(len(body)) if band_of[keys[i]] == b] or [0]) + 1 for b in range(nbands)]
    cells = {}
    for i in range(len(body)):
        cells.setdefault((band_of[keys[i]], keys[i], layer[i]), []).append(i)
    for c in cells.values():
        c.sort(key=lambda i: -absxy[ptr[i]][1])
    lane_rank = {}
    for b in range(nbands):
        for r, k in enumerate(lanes[b]):
            lane_rank[k] = r
    rank = {}

    def refresh():
        for (b, k, _l), c in cells.items():
            base = lane_rank[k] * 100000.0
            for r, i in enumerate(c):
                rank[i] = base + r

    refresh()
    order = sorted(cells, key=lambda ck: (ck[0], ck[2]))
    for _sweep_no in range(4):
        yield
        for direction in (1, -1):
            for ck in (order if direction == 1 else list(reversed(order))):
                c = cells[ck]
                nb = preds if direction == 1 else succs

                def bary(i, nb=nb):
                    v = [rank[j] for j in nb[i]]
                    return sum(v) / len(v) if v else rank[i]
                c.sort(key=bary)
                base = lane_rank[ck[1]] * 100000.0
                for r, i in enumerate(c):
                    rank[i] = base + r
        # Lanes inside a band follow their inputs too (zones keep natural order on ties).
        for b in range(nbands):
            def lane_key(k, b=b):
                v = [rank[j] for i in range(len(body)) if keys[i] == k for j in preds[i] if band_of[keys[j]] != b]
                return (STAGE_RANK.get(k, len(STAGES) // 2), sum(v) / len(v) if v else 0.0, _natural(k))
            lanes[b].sort(key=lane_key)
            for r, k in enumerate(lanes[b]):
                lane_rank[k] = r
        refresh()
    sizes = [node_size(n, scale) for n in body]
    # Wrap tall cells into sub-columns, then size columns and lanes.
    sub = {}
    for ck, c in cells.items():
        cols, cur, h = [], [], 0.0
        for i in c:
            hh = sizes[i][1]
            if cur and h + hh > WRAP_H:
                cols.append(cur)
                cur, h = [], 0.0
            cur.append(i)
            h += hh + ROW_GAP
        cols.append(cur)
        sub[ck] = cols
    colw = {}
    for (b, k, l), cols in sub.items():
        w = sum(max(sizes[i][0] for i in sc) for sc in cols) + SUB_GAP * (len(cols) - 1)
        colw[(b, l)] = max(colw.get((b, l), 0.0), w)
    # Narrow the column gap when a long tree would pass BODY_MAX_W (pinned builder groups).
    ngaps = sum(ncols[b] + 1 for b in range(nbands))
    gap = COL_GAP
    span = sum(colw.values()) + gap * ngaps
    if span > BODY_MAX_W and ngaps:
        gap = max(SUB_GAP, (BODY_MAX_W - 200.0 - sum(colw.values())) / ngaps)
    colx, gcol, x, g = {}, {}, 0.0, 0
    for b in range(nbands):
        for l in range(ncols[b]):
            colx[(b, l)] = x
            gcol[(b, l)] = g
            x += colw.get((b, l), 0.0) + gap
            g += 1
        x += gap
    lane_top, pos = {}, {}
    for b in range(nbands):
        y = 0.0
        for k in lanes[b]:
            lane_top[k] = y
            top = y - LABEL_PAD
            bottom = top
            for l in range(ncols[b]):
                cols = sub.get((b, k, l))
                if not cols:
                    continue
                cx = colx[(b, l)]
                for sc in cols:
                    cy = top
                    for i in sc:
                        pos[i] = (cx, cy)
                        cy -= sizes[i][1] + ROW_GAP
                    bottom = min(bottom, cy)
                    cx += max(sizes[i][0] for i in sc) + SUB_GAP
            y = bottom - LANE_GAP
    yield
    # Frames: one per lane, flat, role colour or alternating zone shade.
    by_name = {f.name: f for f in old_frames}
    zone_keys = sorted({str(n.get(TAG_ZONE)) for n in body if n.get(TAG_ZONE)}, key=_natural)
    roles = {}
    for i, n in enumerate(body):
        r = n.get(TAG_ROLE)
        if r and keys[i] not in roles:
            roles[keys[i]] = str(r)
    frames = {}
    for k in fkeys:
        name = (FRAME_PREFIX + k)[:63]
        fr = by_name.pop(name, None)
        if fr is None:
            fr = nodes.new("NodeFrame")
            fr.name = name
        fr.parent = None
        fr.label = k[:63]
        fr.label_size = FRAME_LABEL_SIZE
        fr.shrink = True
        fr.use_custom_color = True
        if k in zone_keys:
            fr.color = ZONE_SHADES[zone_keys.index(k) % 2]
        else:
            fr.color = ROLE_COLORS.get(roles.get(k) or STAGE_ROLE.get(k, "surface"), ROLE_COLORS["surface"])
        fr[TAG_STAGE] = k
        frames[k] = fr
    yield
    moved = 0
    for i, n in enumerate(body):
        if n.parent is not frames[keys[i]]:
            n.parent = frames[keys[i]]
            moved += 1
            if moved % STEP_OPS == 0:
                yield
    yield
    # Bus: links spanning more than BUS_SPAN columns run through labelled reroutes above the lanes.
    long_links = {}
    for l in list(links):
        a = pos_of.get(l.from_node.as_pointer()) if l.from_node is not None else None
        b = pos_of.get(l.to_node.as_pointer()) if l.to_node is not None else None
        if a is None or b is None or getattr(l, "is_muted", False):
            continue
        ga, gb = gcol[(band_of[keys[a]], layer[a])], gcol[(band_of[keys[b]], layer[b])]
        if gb - ga > BUS_SPAN:
            long_links.setdefault(l.from_socket.as_pointer(), [l.from_socket, a, []])[2].append((gb, b, l.to_socket, l))
    bus_frame = None
    bus_pos = []
    rows_end = []
    long_links = _bus_worth_it(long_links, links, pos_of, pos, sizes, max(1, int(0.15 * len(body))))
    for sp, (fsock, a, targets) in sorted(long_links.items(), key=lambda kv: pos[kv[1][1]][0]):
        x0 = pos[a][0] + sizes[a][0] + 40.0
        x1 = max(pos[b][0] for _g, b, _s, _l in targets) - 80.0
        row = next((r for r, end in enumerate(rows_end) if end + 60.0 < x0), None)
        if row is None:
            row = len(rows_end)
            rows_end.append(x1)
        else:
            rows_end[row] = x1
        ybus = BUS_ROW * (row + 2)
        if bus_frame is None:
            bus_frame = by_name.pop(BUS_FRAME, None)
            if bus_frame is None:
                bus_frame = nodes.new("NodeFrame")
            bus_frame.name = BUS_FRAME
            bus_frame.label = BUS_FRAME
            bus_frame.label_size = FRAME_LABEL_SIZE
            bus_frame.shrink = True
            bus_frame.use_custom_color = True
            bus_frame.color = ROLE_COLORS["output"]
        label = (fsock.name or "Signal")[:63]
        prev = nodes.new("NodeReroute")
        prev[BUS_TAG] = 1
        prev.label = label
        prev.parent = bus_frame
        links.new(fsock, prev.inputs[0])
        bus_pos.append((prev, x0, ybus))
        made = 1
        ops = 2
        by_col = {}
        for gb, b, tsock, l in targets:
            by_col.setdefault(gb, []).append((b, tsock, l))
        for gb in sorted(by_col):
            group = by_col[gb]
            r = nodes.new("NodeReroute")
            r[BUS_TAG] = 1
            r.label = label
            r.parent = bus_frame
            links.new(prev.outputs[0], r.inputs[0])
            bus_pos.append((r, min(pos[b][0] for b, _s, _l in group) - 80.0, ybus))
            made += 1
            ops += 2
            for b, tsock, l in group:
                # links.new replaces the link on a single-input socket; a separate remove
                # doubled the ops (each re-runs the tree update, ~3 ms on big groups).
                if tsock.is_multi_input:
                    links.remove(l)
                    ops += 1
                links.new(r.outputs[0], tsock)
                ops += 1
            prev = r
            if ops >= STEP_OPS:
                ops = 0
                yield
        stats["bus_reroutes"] += made
        yield
    for k, f in enumerate(by_name.values()):
        nodes.remove(f)
        if k % STEP_OPS == STEP_OPS - 1:
            yield
    yield
    # One write for every location; frames sit at the origin so child locations are absolute.
    all_nodes = list(nodes)
    flat = [0.0] * (2 * len(all_nodes))
    nodes.foreach_get("location", flat)
    where = {ptr[i]: xy for i, xy in pos.items()}
    for r, rx, ry in bus_pos:
        where[r.as_pointer()] = (rx, ry)
    for j, n in enumerate(all_nodes):
        p = n.as_pointer()
        if n.bl_idname == "NodeFrame":
            flat[2 * j], flat[2 * j + 1] = 0.0, 0.0
        elif p in where:
            flat[2 * j], flat[2 * j + 1] = where[p]
    nodes.foreach_set("location", flat)
    stats["nodes"] = len(body)
    stats["frames"] = len(frames) + (1 if bus_frame is not None else 0)
    stats["width"] = int(x)
    if x > BODY_MAX_W:
        stats["wide"] = True


def _segments_crossed(p, q, A, B):
    """How many segments A[i]-B[i] the segment p-q properly crosses (shared ends do not count)."""
    import numpy as np
    if not len(A):
        return 0
    p, q = np.asarray(p, float), np.asarray(q, float)

    def orient(a, b, c):
        return (b[..., 0] - a[..., 0]) * (c[..., 1] - a[..., 1]) - (b[..., 1] - a[..., 1]) * (c[..., 0] - a[..., 0])
    d1 = orient(A, B, p[None, :])
    d2 = orient(A, B, q[None, :])
    d3 = orient(p[None, :], q[None, :], A)
    d4 = orient(p[None, :], q[None, :], B)
    hit = (d1 * d2 < 0) & (d3 * d4 < 0)
    return int(hit.sum())


def _bus_worth_it(long_links, links, pos_of, pos, sizes, cap):
    """Keep only long links whose bus route crosses fewer links than the direct line, best first,
    while the reroutes they add stay within cap (15% of the node count)."""
    import numpy as np
    if not long_links:
        return {}
    segs, owner = [], []
    for l in links:
        a = pos_of.get(l.from_node.as_pointer()) if l.from_node is not None else None
        b = pos_of.get(l.to_node.as_pointer()) if l.to_node is not None else None
        if a is None or b is None or a not in pos or b not in pos:
            continue
        segs.append((pos[a][0] + sizes[a][0], pos[a][1] - sizes[a][1] * 0.5,
                     pos[b][0], pos[b][1] - sizes[b][1] * 0.5))
        owner.append(l.from_socket.as_pointer())
    if not segs:
        return {}
    S = np.asarray(segs, float)
    own = np.asarray(owner, dtype=np.int64)
    ybus = BUS_ROW * 2
    scored = []
    for sp, (fsock, a, targets) in long_links.items():
        mask = own != np.int64(sp)
        A, B = S[mask, :2], S[mask, 2:]
        src = (pos[a][0] + sizes[a][0], pos[a][1] - sizes[a][1] * 0.5)
        direct = sum(_segments_crossed(src, (pos[b][0], pos[b][1] - sizes[b][1] * 0.5), A, B)
                     for _g, b, _s, _l in targets)
        x0 = src[0] + 40.0
        bus = _segments_crossed(src, (x0, ybus), A, B)
        cols = {}
        for gb, b, _s, _l in targets:
            cols.setdefault(gb, []).append(b)
        for gb, bs in cols.items():
            xr = min(pos[b][0] for b in bs) - 80.0
            for b in bs:
                bus += _segments_crossed((xr, ybus), (pos[b][0], pos[b][1] - sizes[b][1] * 0.5), A, B)
        scored.append((direct - bus, -pos[a][0], sp, 1 + len(cols)))
    scored.sort(key=lambda t: (-t[0], t[1]))
    keep, used = {}, 0
    for gain, _x, sp, cost in scored:
        if gain <= 0 or used + cost > cap:
            continue
        keep[sp] = long_links[sp]
        used += cost
    return keep


def _walk_trees(tree, recursive):
    seen, out, stack = set(), [], [tree]
    while stack:
        t = stack.pop()
        if t is None or t.as_pointer() in seen:
            continue
        seen.add(t.as_pointer())
        out.append(t)
        if recursive:
            for n in t.nodes:
                sub_tree = getattr(n, "node_tree", None) if n.bl_idname.endswith("Group") else None
                if sub_tree is not None and getattr(sub_tree, "library", None) is None:
                    stack.append(sub_tree)
    return out


def finalize_tree(tree, family=None, *, recursive=True, force=False, remove_dead=False, collapse=None):
    """Lay out tree (and its groups) once per content; returns a stats dict.

    Trees already laid out at LAYOUT_VER2 with an unchanged content signature are skipped,
    so shared groups cost one layout per session. ``force`` re-lays out edited trees.
    ``collapse`` moves chunks of a large root tree into child groups first; None means
    "material and world trees only" (embedded trees), True also collapses a group root.
    """
    stats = {}
    for _ in _finalize_steps(tree, family, stats, recursive=recursive, force=force,
                             remove_dead=remove_dead, collapse=collapse):
        pass
    return stats


def _finalize_steps(tree, family, stats, *, recursive=True, force=False, remove_dead=False, collapse=None):
    """finalize_tree as a generator: yields between trees and inside each layout."""
    import time
    t0 = time.perf_counter()
    stats.update({"family": family, "trees": 0, "laid_out": 0, "cached": 0, "nodes": 0, "frames": 0,
                  "bus_reroutes": 0, "labels": 0, "removed": 0, "edited": 0, "violations": 0, "per_tree": [],
                  "collapse": None, "collapse_ms": 0.0})
    if tree is None:
        return
    fresh = set()
    if collapse is None:
        collapse = bool(getattr(tree, "is_embedded_data", False))
    if collapse and recursive:
        tc = time.perf_counter()
        try:
            cs = {}
            yield from _collapse_steps(tree, None, cs, force=force)
            stats["collapse"] = cs
            if cs.get("changed"):
                fresh = {t.as_pointer() for t in _walk_trees(tree, True) if t.get(COLLAPSED_TAG) or t is tree}
        except Exception as exc:
            print("[graph_layout] collapse failed on %s: %s" % (getattr(tree, "name", "?"), exc))
        stats["collapse_ms"] = round((time.perf_counter() - tc) * 1000.0, 2)
        yield
    for t in _walk_trees(tree, recursive):
        stats["trees"] += 1
        try:
            old_sig = t.get(SIG_KEY) if t.get(VER_KEY) == LAYOUT_VER2 else None
            same = bool(old_sig) and old_sig == content_signature(t)
        except Exception:
            old_sig, same = None, False
        if old_sig and not force and t.as_pointer() not in fresh:
            # Laid out before: unchanged trees are cached; hand-edited ones wait for Tidy Graph.
            stats["cached" if same else "edited"] += 1
            continue
        if same and force and t is not tree and t.as_pointer() not in fresh:
            # force re-lays out the root; an unchanged shared group stays cached. Re-laying it
            # out per material that uses it cost ~10 layouts per group (W3: 579 for 165 trees).
            stats["cached"] += 1
            continue
        if remove_dead:
            stats["removed"] += remove_unused(t)
        s = {"tree": t.name, "nodes": 0, "frames": 0, "bus_reroutes": 0, "labels": 0}
        yield from _layout_steps(t, family, s)
        if family and t is tree:
            t["arc_family"] = str(family)
        t[VER_KEY] = LAYOUT_VER2
        t[SIG_KEY] = content_signature(t)
        v = assert_clean(t)
        s["violations"] = len(v)
        stats["violations"] += len(v)
        stats["laid_out"] += 1
        for k in ("nodes", "frames", "bus_reroutes", "labels"):
            stats[k] += s.get(k, 0)
        stats["per_tree"].append(s)
        yield
    stats["ms"] = round((time.perf_counter() - t0) * 1000.0, 2)


def _output_nodes(tree):
    return [n for n in tree.nodes if n.type in _OUTPUT_TYPES]


def remove_unused(tree):
    """Delete nodes that feed no output. Group input/output nodes and frames stay."""
    keep, stack = set(), list(_output_nodes(tree))
    if not stack:
        return 0
    feeds = {}
    for l in tree.links:
        if l.from_node is not None and l.to_node is not None:
            feeds.setdefault(l.to_node.as_pointer(), []).append(l.from_node)
    while stack:
        n = stack.pop()
        p = n.as_pointer()
        if p in keep:
            continue
        keep.add(p)
        stack.extend(feeds.get(p, ()))
    dead = [n for n in tree.nodes if n.as_pointer() not in keep
            and n.bl_idname not in ("NodeFrame", "NodeGroupInput", "NodeGroupOutput")]
    for n in dead:
        tree.nodes.remove(n)
    return len(dead)


def assert_clean(tree):
    """Contract violations as plain strings (empty list when clean)."""
    out = []
    nodes = list(tree.nodes)
    if not nodes:
        return out
    flat = [0.0] * (2 * len(nodes))
    tree.nodes.foreach_get("location", flat)
    index = {n.as_pointer(): i for i, n in enumerate(nodes)}
    scale = _ui_scale()
    body = [n for n in nodes if n.bl_idname != "NodeFrame"]
    absxy = {n.as_pointer(): _abs_xy(n, flat, index) for n in body}
    for l in tree.links:
        a, b = l.from_node, l.to_node
        if a is None or b is None or a.bl_idname == "NodeFrame" or b.bl_idname == "NodeFrame":
            continue
        if absxy[a.as_pointer()][0] > absxy[b.as_pointer()][0]:
            out.append("right-to-left link: %s -> %s" % (a.name, b.name))
    rects = []
    for n in body:
        if n.bl_idname == "NodeReroute":
            continue
        x, y = absxy[n.as_pointer()]
        w, h = node_size(n, scale)
        rects.append((x, y - h, x + w, y, n.name))
    rects.sort()
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            if b[0] >= a[2] - 2:
                break
            if b[1] < a[3] - 2 and b[3] > a[1] + 2:
                out.append("overlap: %s / %s" % (a[4], b[4]))
    for n in body:
        if n.parent is None and n.bl_idname != "NodeReroute":
            out.append("not in a frame: %s" % n.name)
        if _is_helper(n):
            out.append("helper without label: %s" % n.name)
    return out


def partition_into_groups(tree, key):
    """Plan only: map each group name to the node names it would hold. Nothing moves.

    ``key`` is a tag name (for example "arc_zone") or a callable(node) -> name or None.
    Moving nodes is left to builders, which own the group interfaces.
    """
    plan = {}
    for n in tree.nodes:
        if n.bl_idname in ("NodeFrame", "NodeGroupInput", "NodeGroupOutput"):
            continue
        k = key(n) if callable(key) else n.get(key)
        if k:
            plan.setdefault(str(k), []).append(n.name)
    return plan


# Collapse: move zone, stage and dataflow chunks of a large tree into child groups.
COLLAPSE_VER = 1
COLLAPSE_KEY = "arc_collapse_ver"
COLLAPSE_SIG = "arc_collapse_sig"
COLLAPSED_TAG = "arc_collapsed"
TOP_MAX = 12
GROUP_MAX = 60
GROUP_HARD = 90
DEPTH_MAX = 4
_COLLAPSE_TREES = ("ShaderNodeTree", "CompositorNodeTree")
_PIN_TYPES = {"GROUP_INPUT", "GROUP_OUTPUT", "FRAME", "EMISSION", "MIX_SHADER", "ADD_SHADER", "BACKGROUND",
              "HOLDOUT", "SCRIPT", "R_LAYERS"} | _OUTPUT_TYPES
_PIN_PREFIX = ("DR.ZoneOffsets", "DR.Control", "DR.WeaponZoneIdx", "DR.LayerInputs.mid", "GT_mid_x8_half",
               "ArcSky", "P:", "DR_Decal", "DR Outfit")
_PIN_EXACT = {"Extra_Contrast", "Overall Intensity", "Saturation"}
_PIN_TREE_PREFIX = ("DR.UserControls", "DR.ZoneOffsets", "DR.Look", "DR.WeaponZoneIdx", "DR Outfit")
_PIN_TREE_KEYS = ("arc_control_keys", "independent_owned_graph", "dr.wrapper", "dr.stage_tree")
_ROLE_TITLE = {"inputs": "Inputs", "mask": "Mask", "colour": "Colour", "pattern": "Pattern and Detail",
               "surface": "Surface", "weathering": "Weathering", "controls": "Controls", "output": "Output"}
_WORD_STAGE = (("normal", "Normal"), ("rough", "Roughness/Metal"), ("metal", "Roughness/Metal"),
               ("decal", "Decals"), ("wear", "Weathering"), ("dirt", "Weathering"), ("weather", "Weathering"),
               ("mask", "Mask"), ("tint", "Colour"), ("colour", "Colour"), ("color", "Colour"),
               ("detail", "Detail"), ("pattern", "Pattern and Detail"))
_IFACE_TYPES = {"VALUE": "NodeSocketFloat", "INT": "NodeSocketInt", "BOOLEAN": "NodeSocketBool",
                "VECTOR": "NodeSocketVector", "RGBA": "NodeSocketColor", "SHADER": "NodeSocketShader",
                "STRING": "NodeSocketString", "ROTATION": "NodeSocketRotation", "MATRIX": "NodeSocketMatrix"}
_FP_SKIP = {"rna_type", "name", "label", "location", "location_absolute", "width", "height", "dimensions",
            "select", "parent", "inputs", "outputs", "internal_links", "type", "color", "use_custom_color",
            "hide", "show_options", "show_preview", "show_texture", "warning_propagation", "color_tag",
            "is_active_output", "bl_idname", "bl_label", "bl_description", "bl_icon", "bl_static_type",
            "bl_width_default", "bl_width_min", "bl_width_max", "bl_height_default", "bl_height_min",
            "bl_height_max"}
_COPY_SKIP = {"rna_type", "name", "location", "location_absolute", "dimensions", "select", "parent", "inputs",
              "outputs", "internal_links", "type", "node_tree"}
_WEAPON_ID = None


class CollapseError(Exception):
    pass


def _weapon_id_re():
    global _WEAPON_ID
    if _WEAPON_ID is None:
        import re
        _WEAPON_ID = re.compile(r"_ID(\.\d{3})?(\.(png|tga|exr|jpe?g|dds|tiff?))?(\.\d{3})?$", re.I)
    return _WEAPON_ID


def _pinned(n, probe=None):
    """Nodes runtime code finds by name or type at this level, and nodes a group cannot hold."""
    t = n.type
    if t in _PIN_TYPES or t.startswith("BSDF_") or n.bl_idname == "NodeFrame":
        return True
    if any(s.type == "SHADER" for s in n.outputs):
        return True
    name = n.name
    if name in _PIN_EXACT or name.startswith(_PIN_PREFIX) or n.get("dr.legacy_name") or n.get("arc_user_controls"):
        return True
    if n.get(TAG_STAGE) == "Controls" or n.get(TAG_ROLE) == "controls":
        return True
    if t == "GROUP":
        sub = getattr(n, "node_tree", None)
        if sub is None or sub.library is not None:
            return True
        if sub.name.startswith(_PIN_TREE_PREFIX) or sub.name.endswith("Look") or sub.name.startswith("Arc "):
            return True
        if any(sub.get(k) for k in _PIN_TREE_KEYS):
            return True
    if t == "TEX_IMAGE":
        img = getattr(n, "image", None)
        if img is not None and _weapon_id_re().search(img.name):
            return True
    if probe is not None:
        try:
            if not n.poll_instance(probe):
                return True
        except Exception:
            return True
    return False


def _fpv(v, depth=0):
    """Stable text for one property value (IDs by name, floats rounded)."""
    import bpy
    if v is None or isinstance(v, (bool, int, str)):
        return repr(v)
    if isinstance(v, float):
        return "%.5g" % v
    if isinstance(v, bpy.types.ID):
        return "ID:" + v.name
    if hasattr(v, "bl_rna"):
        if depth > 2:
            return "<s>"
        parts = []
        for p in v.bl_rna.properties:
            k = p.identifier
            if k in ("rna_type",) or (p.type == "POINTER" and not p.is_readonly and k not in ("image",)):
                continue
            try:
                val = getattr(v, k)
            except Exception:
                continue
            if p.type == "COLLECTION":
                parts.append(k + "=[" + ",".join(_fpv(e, depth + 1) for e in val) + "]")
            elif p.type == "POINTER":
                parts.append(k + "=" + _fpv(val, depth + 1))
            else:
                parts.append(k + "=" + _fpv(val, depth + 1))
        return "{" + ";".join(parts) + "}"
    try:
        return "(" + ",".join(_fpv(x, depth) for x in v) + ")"
    except TypeError:
        return repr(v)


def _node_fp(n):
    """Fingerprint of everything that changes what a node computes; no names or positions."""
    parts = [n.bl_idname]
    if n.bl_idname == "NodeReroute":
        # A reroute passes its input through; its socket type follows links (checked after linking).
        return parts[0]
    for p in n.bl_rna.properties:
        k = p.identifier
        if k in _FP_SKIP or k.startswith("bl_"):
            continue
        try:
            parts.append(k + "=" + _fpv(getattr(n, k)))
        except Exception:
            continue
    for io in (n.inputs, n.outputs):
        for s in io:
            dv = getattr(s, "default_value", None) if s.bl_rna.properties.get("default_value") else None
            parts.append("%s:%s:%s" % (s.identifier, int(bool(getattr(s, "enabled", True))), _fpv(dv)))
    return "|".join(parts)


def _copy_ramp(src, dst):
    for k in ("color_mode", "interpolation", "hue_interpolation"):
        try:
            setattr(dst, k, getattr(src, k))
        except Exception:
            pass
    els = dst.elements
    while len(els) > 1:
        els.remove(els[-1])
    els[0].position = src.elements[0].position
    els[0].color = src.elements[0].color
    for e in list(src.elements)[1:]:
        ne = els.new(e.position)
        ne.color = e.color


def _copy_curves(src, dst):
    for p in src.bl_rna.properties:
        if not p.is_readonly and p.type != "POINTER":
            try:
                setattr(dst, p.identifier, getattr(src, p.identifier))
            except Exception:
                pass
    for k in ("black_level", "white_level"):
        try:
            setattr(dst, k, getattr(src, k))
        except Exception:
            pass
    for sc, dc in zip(src.curves, dst.curves):
        pts = dc.points
        while len(pts) > max(2, len(sc.points)):
            pts.remove(pts[-1])
        while len(pts) < len(sc.points):
            pts.new(0.5, 0.5)
        for sp, dp in zip(sc.points, pts):
            dp.location = sp.location
            dp.handle_type = sp.handle_type
    try:
        dst.update()
    except Exception:
        pass


def _copy_struct(src, dst, depth=0):
    import bpy
    if src is None or dst is None or depth > 2:
        return
    if isinstance(src, bpy.types.ColorRamp):
        return _copy_ramp(src, dst)
    if isinstance(src, bpy.types.CurveMapping):
        return _copy_curves(src, dst)
    for p in src.bl_rna.properties:
        k = p.identifier
        if k == "rna_type":
            continue
        if not p.is_readonly:
            try:
                setattr(dst, k, getattr(src, k))
            except Exception:
                pass
        elif p.type == "POINTER":
            v = getattr(src, k, None)
            if v is not None and not isinstance(v, bpy.types.ID):
                _copy_struct(v, getattr(dst, k, None), depth + 1)


def _copy_node(src, nodes):
    """Recreate src in nodes: same type and name, every writable property, sockets and ID props."""
    import bpy
    dst = nodes.new(src.bl_idname)
    dst.name = src.name
    if src.bl_idname == "NodeReroute":
        # A new reroute is Color; keep the source socket type (FloatFactor, Vector, ...).
        sid = getattr(src, "socket_idname", "") or (src.outputs[0].bl_idname if len(src.outputs) else "")
        if sid:
            try:
                dst.socket_idname = sid
            except Exception:
                pass
    if hasattr(src, "node_tree"):
        dst.node_tree = src.node_tree
    for p in src.bl_rna.properties:
        k = p.identifier
        if k in _COPY_SKIP or k.startswith("bl_"):
            continue
        if not p.is_readonly:
            try:
                setattr(dst, k, getattr(src, k))
            except Exception:
                pass
        elif p.type == "POINTER":
            v = getattr(src, k, None)
            if v is not None and not isinstance(v, bpy.types.ID):
                _copy_struct(v, getattr(dst, k, None))
    for sio, dio in ((src.inputs, dst.inputs), (src.outputs, dst.outputs)):
        same = len(sio) == len(dio)
        by_id = {s.identifier: s for s in dio}
        for i, s in enumerate(sio):
            d = dio[i] if same and dio[i].identifier == s.identifier else by_id.get(s.identifier)
            if d is None:
                continue
            if hasattr(s, "default_value") and hasattr(d, "default_value"):
                try:
                    d.default_value = s.default_value
                except Exception:
                    pass
            for k in ("hide", "enabled", "hide_value"):
                try:
                    setattr(d, k, getattr(s, k))
                except Exception:
                    pass
    for k in src.keys():
        v = src[k]
        try:
            dst[k] = v.to_dict() if hasattr(v, "to_dict") else (v.to_list() if hasattr(v, "to_list") else v)
        except Exception:
            pass
    return dst


def _sock_index(sock, io):
    p = sock.as_pointer()
    for i, s in enumerate(io):
        if s.as_pointer() == p:
            return i
    raise CollapseError("socket %s not on its node" % sock.identifier)


def _iface_sub(item, sock):
    """Give an interface socket the source socket's subtype (FloatFactor -> FACTOR), which the
    plain socket_type drops; links through the group then keep their type."""
    import re
    sid =getattr(sock, "bl_idname", "") or ""
    base = _IFACE_TYPES.get(getattr(sock, "type", ""), "")
    if not base or not sid.startswith(base) or sid == base or not hasattr(item, "subtype"):
        return item
    want = re.sub(r"(?<!^)(?=[A-Z])", "_", sid[len(base):]).upper()
    try:
        item.subtype = want
    except Exception:
        pass
    return item


def _iface_name(sock, used):
    """Plain interface name from the source socket and its node."""
    node = sock.node
    base = sock.name or "Value"
    if node.type == "GROUP_INPUT":
        name = base
    elif len([s for s in node.outputs if s.enabled]) <= 1:
        name = (node.label or helper_label(node) if _is_helper(node) else node.label or _clean_name(node.name)) or base
    else:
        name = "%s %s" % (node.label or _clean_name(node.name), base)
    name = name.strip()[:60] or "Value"
    out, k = name, 2
    while out in used:
        out = "%s %d" % (name, k)
        k += 1
    used.add(out)
    return out


def _data_name(title):
    """Group datablock name that is free: plain title, else 'title 2', 'title 3' (never .00N)."""
    import bpy
    base = title.strip()[:63] or "Group"
    out, k = base, 2
    while bpy.data.node_groups.get(out) is not None:
        sfx = " %d" % k
        out = base[:63 - len(sfx)].rstrip() + sfx
        k += 1
    return out


def _move_into_group(tree, members, title, role, stage=None, zone=None, data_name=None, max_depth=None):
    """Move members of tree into one child group (reused when an identical one exists).

    Returns (group node, created). Raises CollapseError before anything changes when a node
    cannot be recreated exactly.
    """
    import bpy
    import hashlib
    nodes, links = tree.nodes, tree.links
    order = {n.as_pointer(): i for i, n in enumerate(nodes)}
    S = sorted(members, key=lambda n: order[n.as_pointer()])
    sp = {n.as_pointer(): k for k, n in enumerate(S)}
    internal, ins, outs = [], {}, {}
    for l in links:
        a, b = l.from_node, l.to_node
        if a is None or b is None:
            continue
        ia, ib = sp.get(a.as_pointer()), sp.get(b.as_pointer())
        if ia is None and ib is None:
            continue
        muted = bool(getattr(l, "is_muted", False))
        if ia is not None and ib is not None:
            internal.append((ia, _sock_index(l.from_socket, a.outputs), ib, _sock_index(l.to_socket, b.inputs), muted))
        elif ib is not None:
            ent = ins.setdefault(l.from_socket.as_pointer(), [l.from_socket, []])
            ent[1].append((ib, _sock_index(l.to_socket, b.inputs), muted))
        else:
            ent = outs.setdefault(l.from_socket.as_pointer(), [l.from_socket, ia, _sock_index(l.from_socket, a.outputs), []])
            ent[3].append((l.to_socket, muted))
    for ent in list(ins.values()) + list(outs.values()):
        if ent[0].type not in _IFACE_TYPES:
            raise CollapseError("socket type %s cannot cross a group" % ent[0].type)
    ins_l = sorted(ins.values(), key=lambda e: min((c[0], c[1]) for c in e[1]))
    outs_l = sorted(outs.values(), key=lambda e: (e[1], e[2]))
    fps = [_node_fp(n) for n in S]
    h = hashlib.sha1()
    h.update(tree.bl_idname.encode())
    for f in fps:
        h.update(f.encode())
    h.update(repr(sorted(internal)).encode())
    h.update(repr([(e[0].type, sorted((c[0], c[1]) for c in e[1])) for e in ins_l]).encode())
    h.update(repr([(e[0].type, e[1], e[2]) for e in outs_l]).encode())
    sig = h.hexdigest()[:20]
    group = None
    for g in bpy.data.node_groups:
        if g.get(COLLAPSE_SIG) == sig and g.bl_idname == tree.bl_idname and g.library is None:
            # Never reuse the tree itself or a group that contains it (recursive node_tree).
            if g == tree or any(t == tree for t in _walk_trees(g, True)):
                continue
            # A group split further after it was made may be too deep for this level.
            if max_depth is not None and tree_depth(g) > max_depth:
                continue
            group = g
            break
    created = group is None
    if created:
        group = bpy.data.node_groups.new(_data_name(data_name or title), tree.bl_idname)
        try:
            gi = group.nodes.new("NodeGroupInput")
            go = group.nodes.new("NodeGroupOutput")
            used = set()
            for e in ins_l:
                _iface_sub(group.interface.new_socket(name=_iface_name(e[0], used), in_out="INPUT",
                                                      socket_type=_IFACE_TYPES[e[0].type]), e[0])
            used = set()
            for e in outs_l:
                tos = {t.name for t, _m in e[3]}
                name = next(iter(tos)) if len(tos) == 1 else None
                if name and name not in used:
                    used.add(name)
                else:
                    name = _iface_name(e[0], used)
                _iface_sub(group.interface.new_socket(name=name, in_out="OUTPUT", socket_type=_IFACE_TYPES[e[0].type]), e[0])
            copies = [_copy_node(n, group.nodes) for n in S]
            for n, c, f in zip(S, copies, fps):
                if _node_fp(c) != f:
                    raise CollapseError("copy differs: %s (%s)" % (n.name, n.bl_idname))
            for ia, si, ib, ti, muted in internal:
                l = group.links.new(copies[ia].outputs[si], copies[ib].inputs[ti])
                if muted:
                    l.is_muted = True
            for k, e in enumerate(ins_l):
                for ib, ti, muted in e[1]:
                    l = group.links.new(gi.outputs[k], copies[ib].inputs[ti])
                    if muted:
                        l.is_muted = True
            for k, e in enumerate(outs_l):
                group.links.new(copies[e[1]].outputs[e[2]], go.inputs[k])
            for n, c in zip(S, copies):
                if n.bl_idname == "NodeReroute" and c.socket_idname != n.socket_idname:
                    try:
                        c.socket_idname = n.socket_idname
                    except Exception:
                        pass
        except Exception:
            bpy.data.node_groups.remove(group)
            raise
        group[COLLAPSED_TAG] = 1
        group[COLLAPSE_SIG] = sig
        group[COLLAPSE_KEY] = COLLAPSE_VER
        group[TAG_ROLE] = role
        if zone:
            group[TAG_ZONE] = _zone_norm(zone)
        elif tree.get(TAG_ZONE):
            group[TAG_ZONE] = str(tree.get(TAG_ZONE))
    flat = [0.0] * (2 * len(nodes))
    nodes.foreach_get("location", flat)
    idx = [order[n.as_pointer()] for n in S]
    cx = sum(flat[2 * i] for i in idx) / len(idx)
    cy = sum(flat[2 * i + 1] for i in idx) / len(idx)
    gn = nodes.new("CompositorNodeGroup" if tree.bl_idname == "CompositorNodeTree" else "ShaderNodeGroup")
    gn.node_tree = group
    if gn.node_tree != group or len(gn.inputs) != len(ins_l) or len(gn.outputs) != len(outs_l):
        nodes.remove(gn)
        if created:
            bpy.data.node_groups.remove(group)
        raise CollapseError("group sockets do not match: %s" % title)
    gn.name = title[:63]
    gn.label = title[:63]
    gn.width = 220.0
    gn.use_custom_color = True
    gn.color = ROLE_COLORS.get(role, ROLE_COLORS["surface"])
    gn.location = (cx, cy)
    tag(gn, stage=stage, zone=zone, role=role)
    gn[COLLAPSED_TAG] = 1
    for k, e in enumerate(ins_l):
        l = links.new(e[0], gn.inputs[k])
        if all(c[2] for c in e[1]):
            l.is_muted = True
    for k, e in enumerate(outs_l):
        for tsock, muted in e[3]:
            l = links.new(gn.outputs[k], tsock)
            if muted:
                l.is_muted = True
    for n in S:
        nodes.remove(n)
    return gn, created


def _infer_keys(tree, movable, preds, succs):
    """Zone, stage tag, frame, type or name per movable node; then consumers, label words,
    distance to the output."""
    body = [n for n in tree.nodes if n.bl_idname != "NodeFrame"]
    frame_of = {}
    for n in body:
        p = n.parent
        if p is not None:
            lab = (p.label or "").strip() or _clean_name(p.name)
            if lab.startswith(FRAME_PREFIX):
                lab = lab[len(FRAME_PREFIX):]
            if lab and lab != BUS_FRAME:
                frame_of[n.as_pointer()] = _clean_name(lab) if lab.startswith("DR") else lab
    keys = {}
    for n in movable:
        p = n.as_pointer()
        z = n.get(TAG_ZONE)
        if z and _zone_norm(z) != _own_zone(n):
            keys[p] = _zone_norm(z)
            continue
        k = _frame_key(n, frame_of, succs.get(p, ()))
        keys[p] = k if k not in ("Output",) else None
    mp = [n.as_pointer() for n in movable]
    for _pass in range(3):
        for p in mp:
            if keys[p] is None:
                near = [keys[q] for q in succs.get(p, ()) if keys.get(q)] or \
                       [keys[q] for q in preds.get(p, ()) if keys.get(q)]
                if near:
                    keys[p] = max(sorted(set(near), key=_natural), key=near.count)
    words = {n.as_pointer(): ((n.label or "") + " " + n.name).lower() for n in movable}
    for p in mp:
        if keys[p] is None:
            keys[p] = next((st for w, st in _WORD_STAGE if w in words[p]), None)
    sinks = [n.as_pointer() for n in tree.nodes if n.type in _OUTPUT_TYPES or n.type.startswith("BSDF_")]
    dist, frontier = {s: 0 for s in sinks}, list(sinks)
    while frontier:
        nxt = []
        for q in frontier:
            for r in preds.get(q, ()):
                if r not in dist:
                    dist[r] = dist[q] + 1
                    nxt.append(r)
        frontier = nxt
    for p in mp:
        if keys[p] is None:
            d = dist.get(p)
            keys[p] = FALLBACK_STAGE if d is None else ("Surface" if d <= 1 else "Colour" if d <= 3
                                                         else "Pattern and Detail" if d <= 6 else "Textures")
    return keys


def _graph(tree):
    preds, succs = {}, {}
    for l in tree.links:
        if l.from_node is None or l.to_node is None:
            continue
        a, b = l.from_node.as_pointer(), l.to_node.as_pointer()
        if a != b:
            succs.setdefault(a, set()).add(b)
            preds.setdefault(b, set()).add(a)
    return preds, succs


def _topo(members, preds, succs):
    mset = {n.as_pointer() for n in members}
    pos = {n.as_pointer(): i for i, n in enumerate(members)}
    indeg = {p: len([q for q in preds.get(p, ()) if q in mset]) for p in mset}
    ready = sorted([p for p in mset if indeg[p] == 0], key=pos.get)
    out = []
    import heapq
    heap = [(pos[p], p) for p in ready]
    heapq.heapify(heap)
    while heap:
        _i, p = heapq.heappop(heap)
        out.append(p)
        for q in succs.get(p, ()):
            if q in indeg:
                indeg[q] -= 1
                if indeg[q] == 0:
                    heapq.heappush(heap, (pos[q], q))
    left = sorted(mset - set(out), key=pos.get)
    return out + left


def _dataflow_chunks(tree, members, preds, succs, max_nodes):
    """Split members into chunks of at most max_nodes along dataflow: each node joins the
    sink it feeds; sinks pack in topological order. Returns [(nodes, label)]."""
    byp = {n.as_pointer(): n for n in members}
    order = _topo(members, preds, succs)
    rank = {p: i for i, p in enumerate(order)}
    owner = {}
    for p in reversed(order):
        inner = sorted([q for q in succs.get(p, ()) if q in byp], key=rank.get)
        outer = [q for q in succs.get(p, ()) if q not in byp]
        owner[p] = p if (outer or not inner) else owner.get(inner[0], p)
    clusters = {}
    for p in order:
        clusters.setdefault(owner[p], []).append(p)
    seq = sorted(clusters.items(), key=lambda kv: rank[kv[0]])
    chunks, cur, cur_sinks = [], [], []
    for sink, ps in seq:
        while len(ps) > max_nodes:
            if cur:
                chunks.append((cur, cur_sinks))
                cur, cur_sinks = [], []
            chunks.append((ps[:max_nodes], [ps[max_nodes - 1]]))
            ps = ps[max_nodes:]
        if cur and len(cur) + len(ps) > max_nodes:
            chunks.append((cur, cur_sinks))
            cur, cur_sinks = [], []
        cur = cur + ps
        cur_sinks.append(sink)
    if cur:
        chunks.append((cur, cur_sinks))
    if len(chunks) == 1 and len(members) > max_nodes:
        ps = chunks[0][0]
        half = len(ps) // 2
        chunks = [(ps[:half], [ps[half - 1]]), (ps[half:], chunks[0][1])]
    out = []
    for ps, snk in chunks:
        sink = byp[max(snk, key=lambda p: len(clusters.get(p, ())))]
        label = ""
        for s in sink.outputs:
            for l in s.links:
                if l.to_node is not None and l.to_node.as_pointer() not in byp:
                    label = l.to_socket.name if l.to_node.type in ("GROUP_OUTPUT", "OUTPUT_MATERIAL") or \
                        l.to_node.type.startswith("BSDF_") else (l.to_node.label or _clean_name(l.to_node.name))
                    break
            if label:
                break
        if not label:
            label = sink.label or (helper_label(sink) if _is_helper(sink) else _clean_name(sink.name))
        out.append(([byp[p] for p in ps], label or "Signal"))
    return out


def _unique(title, used):
    title = title.strip()[:63]
    out, k = title, 2
    while out in used:
        sfx = " %d" % k
        out = title[:63 - len(sfx)].rstrip() + sfx
        k += 1
    used.add(out)
    return out


def _role_of(key):
    if key.startswith("Zone") or key == "Zones":
        return "colour"
    return STAGE_ROLE.get(key) or next((r for r, t in _ROLE_TITLE.items() if t == key), "surface")


COL_MAX = 15
_INPUT_KEYS = ("Textures", "Inputs", "Coordinates")


def _convex(tree, members, probe=None, may_grow=None, cap=None):
    """Grow members so no outside path leaves and re-enters them (that would make the group
    feed itself). Outside nodes on such paths join; when one is pinned, may not join
    (``may_grow``) or growth would pass ``cap`` nodes, the members it feeds drop out instead.
    Returns the members in tree order."""
    preds, succs = _graph(tree)
    byp = {n.as_pointer(): n for n in tree.nodes}
    mset = {n.as_pointer() for n in members}

    def reach(start, nxt):
        seen, stack = set(), [q for p in start for q in nxt.get(p, ()) if q not in mset]
        while stack:
            p = stack.pop()
            if p in seen:
                continue
            seen.add(p)
            stack.extend(q for q in nxt.get(p, ()) if q not in mset and q not in seen)
        return seen

    for _ in range(64):
        bad = reach(mset, succs) & reach(mset, preds)
        if not bad:
            break
        stuck = {p for p in bad if _pinned(byp[p], probe) or (may_grow is not None and not may_grow(p))}
        if not stuck and cap is not None and len(mset | bad) > cap:
            stuck = bad
        if not stuck:
            mset |= bad
            continue
        drop, stack = set(), list(stuck)
        while stack:
            p = stack.pop()
            for q in succs.get(p, ()):
                if q not in drop:
                    drop.add(q)
                    stack.append(q)
        mset -= drop
    else:
        return []
    return [n for n in tree.nodes if n.as_pointer() in mset]


def _layers(members, preds):
    """Longest-path layer of each member over links inside members (pointer -> layer)."""
    mset = {n.as_pointer() for n in members}
    layer = {}
    succs = {}
    for p in mset:
        for q in preds.get(p, ()):
            if q in mset:
                succs.setdefault(q, set()).add(p)
    order = _topo(members, preds, succs)
    for p in order:
        layer[p] = 1 + max([layer[q] for q in preds.get(p, ()) if q in layer] or [-1])
    return layer


def _bands(ms, preds, title):
    """Split a convex set into bands of at most COL_MAX layers (each band stays convex)."""
    layer = _layers(ms, preds)
    top = max(layer.values() or [0])
    if top < COL_MAX:
        return [(ms, title)]
    out = {}
    for n in ms:
        out.setdefault(layer[n.as_pointer()] // COL_MAX, []).append(n)
    keys = sorted(out)
    return [(out[k], "%s %d" % (title, i + 1)) for i, k in enumerate(keys)]


def _topo_slices(tree, movable, max_nodes):
    """Contiguous runs of movable nodes in a topological order of the whole tree, cut at
    every other node; each run is convex by construction."""
    preds, succs = _graph(tree)
    allb = [n for n in tree.nodes if n.bl_idname != "NodeFrame"]
    mv = {n.as_pointer() for n in movable}
    byp = {n.as_pointer(): n for n in allb}
    out, cur = [], []
    for p in _topo(allb, preds, succs):
        if p in mv and len(cur) < max_nodes:
            cur.append(byp[p])
            continue
        if len(cur) >= 2:
            out.append(cur)
        cur = [byp[p]] if p in mv else []
    if len(cur) >= 2:
        out.append(cur)
    return out


def _zone_split(tree, movable, keys, preds, succs, lanes):
    """Zones first: every node joins the zone whose chain it sits in; nodes before all zones
    form 'Shared Surface' (inputs keep their stage), nodes after the zones 'Combine Zones'.
    Classes run Inputs < Shared Surface < zones < Combine Zones, so the groups form no loop
    except where zones really feed each other. Returns False when the tree has no zones."""
    zones = {}
    for n in movable:
        k = keys[n.as_pointer()]
        if k in lanes:
            zones.setdefault(k, set()).add(n.as_pointer())
    if not zones:
        return False

    def flood(start, nxt, acc, z):
        seen, stack = set(start), list(start)
        while stack:
            p = stack.pop()
            for q in nxt.get(p, ()):
                if q not in seen:
                    seen.add(q)
                    stack.append(q)
        for q in seen:
            acc.setdefault(q, set()).add(z)

    za, zd = {}, {}
    for z, ps in zones.items():
        flood(ps, succs, za, z)
        flood(ps, preds, zd, z)
    mv = {n.as_pointer() for n in movable}
    for p in _topo(movable, preds, succs):
        k = keys[p]
        if k in lanes:
            continue
        a, d = za.get(p, set()), zd.get(p, set())
        if a & d:
            keys[p] = min(a & d, key=_natural)
        elif a and d:
            keys[p] = min(d, key=_natural)
        elif a:
            keys[p] = "Combine Zones"
        elif k in _INPUT_KEYS and all(keys.get(q) in _INPUT_KEYS for q in preds.get(p, ()) if q in mv):
            continue
        else:
            keys[p] = "Shared Surface"
    return True


def _split_tree(tree, level, max_nodes, top_max, probe, stats, prefix="", root=None):
    """Collapse one tree in place; recurse into the groups it makes."""
    for _ in _split_steps(tree, level, max_nodes, top_max, probe, stats, prefix, root):
        pass


def _split_steps(tree, level, max_nodes, top_max, probe, stats, prefix="", root=None):
    """_split_tree as a generator: yields after each group move (~50 ms on outfit trees)."""
    # NOTE: chunks are made convex in _convex() so a group never feeds itself through outside nodes.
    if root is None:
        root = level == 1
    nodes = list(tree.nodes)
    body = [n for n in nodes if n.bl_idname not in ("NodeFrame", "NodeReroute")]
    limit = top_max if root else max_nodes
    if level >= DEPTH_MAX:
        return
    movable = [n for n in nodes if n.bl_idname != "NodeFrame" and not _pinned(n, probe)]
    # A group moved one level down must still fit DEPTH_MAX with its own nesting.
    depth = {}
    for n in movable:
        if n.type == "GROUP" and n.node_tree is not None and n.node_tree.as_pointer() not in depth:
            depth[n.node_tree.as_pointer()] = tree_depth(n.node_tree)
    movable = [n for n in movable if not (n.type == "GROUP" and level + 1 + depth.get(getattr(n.node_tree, "as_pointer", lambda: 0)(), 0) > DEPTH_MAX)]
    if len(movable) < 2:
        return
    preds, succs = _graph(tree)
    wide = False
    if len(body) <= limit:
        layer = _layers([n for n in nodes if n.bl_idname != "NodeFrame"], preds)
        wide = max(layer.values() or [0]) >= COL_MAX and len(body) > 2
        if not wide:
            return
    pinned = len(body) - len([n for n in movable if n.bl_idname != "NodeReroute"])
    keys = _infer_keys(tree, movable, preds, succs)
    # Lanes are zone-tagged chains (or "Zone <n>" frames); a stage only named "Zone ..." is no lane.
    lanes = {_zone_norm(n.get(TAG_ZONE)) for n in movable if n.get(TAG_ZONE) and _zone_norm(n.get(TAG_ZONE)) != _own_zone(n)}
    import re
    lanes |= {k for k in set(keys.values()) if k and re.match(r"Zone \d", k)}
    zoned = _zone_split(tree, movable, keys, preds, succs, lanes)
    byp = {n.as_pointer(): n for n in movable}
    buckets = {}
    for n in movable:
        buckets.setdefault(keys[n.as_pointer()], []).append(n)
    room = max(2, limit - pinned)
    if zoned:
        nz = len([k for k in buckets if k in lanes])
        room = max(room, nz + 3)
    elif len(buckets) > room:
        coarse = {}
        for k, ms in buckets.items():
            ck = "Zones" if k.startswith("Zone") else _ROLE_TITLE.get(STAGE_ROLE.get(k, ""), "Logic")
            coarse.setdefault(ck, []).extend(ms)
        buckets = coarse
        for k, ms in buckets.items():
            for n in ms:
                keys[n.as_pointer()] = k
    while len([k for k in buckets if not (zoned and k in lanes)]) > 1 and len(buckets) > room:
        # Merge the smallest bucket into the bucket it feeds most (zone buckets never merge).
        cand = [k for k in buckets if not (zoned and k in lanes)]
        small = min(cand, key=lambda k: (len(buckets[k]), _natural(k)))
        feeds = {}
        for n in buckets.pop(small):
            for q in succs.get(n.as_pointer(), ()):
                if q in byp:
                    feeds[keys[q]] = feeds.get(keys[q], 0) + 1
        pool = [k for k in buckets if not (zoned and k in lanes)]
        target = max((k for k in feeds if k in pool), key=lambda k: (feeds[k], len(buckets[k])), default=None)
        target = target or max(pool, key=lambda k: len(buckets[k]))
        for n in byp.values():
            if keys[n.as_pointer()] == small:
                keys[n.as_pointer()] = target
        buckets[target] = [n for n in movable if keys[n.as_pointer()] == target]
    if wide:
        chunks = [(ms, t, "Logic") for ms, t in _bands(movable, preds, prefix or "Step")]
        if len(chunks) < 2:
            return
    elif len(buckets) == 1:
        (only, ms), = buckets.items()
        chunks = [(c, "%s \u00b7 %s" % (only, lab)) for c, lab in _dataflow_chunks(tree, ms, preds, succs, max_nodes)]
        chunks = [(c, t, only) for c, t in chunks]
    else:
        zr = STAGE_RANK["Zones"]

        def rank(kv):
            k = kv[0]
            if k in lanes:
                return (zr, _natural(k))
            if k == "Shared Surface":
                return (zr - 0.5, k)
            if k == "Combine Zones":
                return (zr + 0.5, k)
            return (STAGE_RANK.get(k, 99), _natural(k))
        chunks = [(ms, k, k) for k, ms in sorted(buckets.items(), key=rank)]
    used = set()
    made = []
    moved = 0
    chunks = [([n.name for n in ms], title, key) for ms, title, key in chunks]
    cap = max(GROUP_HARD, 1)
    for names, title, key in chunks:
        ms = [tree.nodes[nm] for nm in names if nm in tree.nodes]
        if len(ms) < 2:
            continue
        own = key
        may_grow = None
        if zoned:
            def may_grow(p, own=own):
                k = keys.get(p)
                return not (k and k != own and k in lanes)
        cur_body = len([n for n in tree.nodes if n.bl_idname not in ("NodeFrame", "NodeReroute") and not _pinned(n, probe)])
        ms = _convex(tree, ms, probe, may_grow=may_grow, cap=min(max(cap, 2 * len(ms)), max(2, cur_body - 2)))
        if len(ms) < 2:
            continue
        parts = [(ms, title)]
        if level + 1 >= DEPTH_MAX and not wide:
            parts = _bands(ms, preds_now(tree), title)
        for pms, ptitle in parts:
            if len(pms) < 2:
                continue
            name = _unique(ptitle, used)
            zone = key if key in lanes else None
            stage = None if zone else key
            dname = name if not prefix or name.startswith(prefix) else "%s \u00b7 %s" % (prefix, name)
            gn, created = _move_into_group(tree, pms, name, _role_of(key), stage=stage, zone=zone, data_name=dname,
                                           max_depth=DEPTH_MAX - level)
            stats["moved"] += len(pms)
            moved += len(pms)
            stats["groups_made" if created else "groups_reused"] += 1
            if created:
                made.append((gn.node_tree, zone or prefix or ""))
            yield
    if not moved and len(body) > limit:
        for i, ms in enumerate(_topo_slices(tree, [n for n in tree.nodes if n.bl_idname != "NodeFrame" and not _pinned(n, probe)], max_nodes)):
            if len(ms) >= len(movable) - 1 and len(body) <= max_nodes:
                break
            name = _unique("%s %d" % (prefix or "Part", i + 1), used)
            gn, created = _move_into_group(tree, ms, name, "surface", stage="Logic", data_name=name,
                                           max_depth=DEPTH_MAX - level)
            stats["moved"] += len(ms)
            stats["groups_made" if created else "groups_reused"] += 1
            if created:
                made.append((gn.node_tree, prefix or ""))
            yield
    for g, pre in made:
        yield from _split_steps(g, level + 1, max_nodes, top_max, probe, stats, prefix=pre, root=False)


def preds_now(tree):
    return _graph(tree)[0]

def _nest_of(tree):
    """How many group levels sit above tree: 0 for a material/world tree, 1 for a group used
    directly by one, and so on (the deepest user path wins; unused groups count as 1)."""
    import bpy
    if tree is None or getattr(tree, "is_embedded_data", False):
        return 0
    parents = {}
    owners = [m.node_tree for m in bpy.data.materials if m.node_tree is not None]
    owners += [w.node_tree for w in bpy.data.worlds if w.node_tree is not None]
    for sc in bpy.data.scenes:
        for attr in ("compositing_node_group", "node_tree"):
            t = getattr(sc, attr, None)
            if t is not None:
                owners.append(t)
    owners += list(bpy.data.node_groups)
    for t in owners:
        for n in t.nodes:
            g = getattr(n, "node_tree", None)
            if g is not None and n.bl_idname.endswith("Group"):
                parents.setdefault(g.as_pointer(), []).append(t)
    memo = {}

    def nest(t, seen):
        if getattr(t, "is_embedded_data", False):
            return 0
        k = t.as_pointer()
        if k in memo:
            return memo[k]
        if k in seen:
            return 1
        ps = parents.get(k, [])
        v = 1 + max([nest(p, seen | {k}) for p in ps] or [0])
        memo[k] = v
        return v

    return nest(tree, frozenset())


def collapse_into_groups(tree, plan=None, *, max_nodes=GROUP_MAX, top_max=TOP_MAX, force=False):
    """Move chunks of a large tree into child groups without changing what it computes.

    Chunks follow arc_zone, then arc_stage (inferred for untagged nodes), then dataflow while
    a tree holds more than max_nodes. Nodes that runtime code finds by name stay at the top.
    Stamped trees, user-edited trees, library trees and trees with drivers are left alone.
    ``plan`` maps a group title to node names and overrides the automatic chunks at the top.
    Returns stats; ``stats["changed"]`` is True when nodes moved.
    """
    stats = {}
    for _ in _collapse_steps(tree, plan, stats, max_nodes=max_nodes, top_max=top_max, force=force):
        pass
    return stats


def _collapse_steps(tree, plan, stats, *, max_nodes=GROUP_MAX, top_max=TOP_MAX, force=False):
    """collapse_into_groups as a generator that yields after each group move; fills stats."""
    import bpy
    stats.update({"tree": getattr(tree, "name", "?"), "moved": 0, "groups_made": 0, "groups_reused": 0,
                  "changed": False, "skipped": None})
    if tree is None:
        stats["skipped"] = "none"
        return
    if tree.bl_idname not in _COLLAPSE_TREES:
        stats["skipped"] = "tree type"
        return
    if getattr(tree, "library", None) is not None:
        stats["skipped"] = "library"
        return
    if tree.get(COLLAPSE_KEY) == COLLAPSE_VER and not force:
        stats["skipped"] = "stamped"
        return
    if is_user_edited(tree) and not force:
        stats["skipped"] = "user edited"
        return
    ad = getattr(tree, "animation_data", None)
    if ad is not None and (ad.action is not None or len(ad.drivers)):
        stats["skipped"] = "animated"
        return
    probe = bpy.data.node_groups.new("Arc Collapse Probe", tree.bl_idname)
    try:
        if plan:
            used = set()
            for title, names in plan.items():
                ms = [tree.nodes[nm] for nm in names if nm in tree.nodes and not _pinned(tree.nodes[nm], probe)]
                if len(ms) >= 2:
                    _move_into_group(tree, ms, _unique(str(title), used), _role_of(str(title)), stage=str(title))
                    stats["moved"] += len(ms)
                    stats["groups_made"] += 1
                    yield
        else:
            # Start one level lower per group above this tree so nesting stays within DEPTH_MAX.
            yield from _split_steps(tree, 1 + max(0, _nest_of(tree) - 1), max_nodes, top_max, probe, stats, root=True)
    except CollapseError as exc:
        stats["skipped"] = str(exc)
        print("[graph_layout] collapse stopped on %s: %s" % (tree.name, exc))
    finally:
        bpy.data.node_groups.remove(probe)
    tree[COLLAPSE_KEY] = COLLAPSE_VER
    stats["changed"] = stats["moved"] > 0


def tree_depth(tree, _seen=None):
    """Group nesting depth: 1 for a tree without groups."""
    seen = _seen or set()
    if tree is None or tree.as_pointer() in seen:
        return 0
    seen = seen | {tree.as_pointer()}
    sub = {t.as_pointer(): t for t in (getattr(n, "node_tree", None) for n in tree.nodes if n.bl_idname.endswith("Group")) if t is not None}
    return 1 + max([tree_depth(t, seen) for t in sub.values()] or [0])


def tidy_tree_columns(nt):
    """Version-1 column tidy; kept as the fallback when the contract layout fails."""
    return _tidy_tree_v1(nt)


def tidy_tree(nt, family=None, collapse=None):
    """Lay out a built tree and its groups to the graph contract; v1 columns on failure."""
    if nt is None or not len(nt.nodes):
        return False
    try:
        stats = finalize_tree(nt, family, recursive=True, collapse=collapse)
        return bool(stats["laid_out"] or stats["cached"])
    except Exception as exc:
        print("[graph_layout] contract layout failed on %s: %s" % (getattr(nt, "name", "?"), exc))
        return _tidy_tree_v1(nt)


HOOK_KEY = "arc_finalized"


def _hook_tree(tree, family, collapse, totals):
    for _ in _hook_steps(tree, family, collapse, totals):
        pass


def _hook_steps(tree, family, collapse, totals):
    if tree is None or not len(tree.nodes):
        return
    # First pass after a build: the builder may have touched nodes after its own layout.
    first = tree.get(HOOK_KEY) is None
    if collapse is False and not first:
        # Scene trees (world, compositor) are rebuilt in place by lighting; lay out on any change.
        first = tree.get(SIG_KEY) != content_signature(tree)
    s = {}
    yield from _finalize_steps(tree, family or tree.get("arc_family"), s, recursive=True, force=first,
                               collapse=collapse)
    tree[HOOK_KEY] = 1
    tree[SIG_KEY] = content_signature(tree)
    totals["trees"] += 1
    totals["laid_out"] += s["laid_out"]
    totals["violations"] += s["violations"]
    totals["collapse_ms"] += s["collapse_ms"]
    cs = s.get("collapse") or {}
    totals["groups_made"] += cs.get("groups_made", 0)
    totals["groups_reused"] += cs.get("groups_reused", 0)


def finalize_objects(objects, scene=None, family=None):
    """Central post-build hook: finalize every material of objects, plus the scene world and compositor."""
    gen = iter_finalize_objects(objects, scene, family)
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


def iter_finalize_objects(objects, scene=None, family=None):
    """finalize_objects as a generator for modal callers (staged import): it yields between slices
    of RNA ops so no main-thread block reaches 0.5 s; StopIteration.value is the totals dict."""
    import time
    t0 = time.perf_counter()
    totals = {"trees": 0, "laid_out": 0, "violations": 0, "groups_made": 0, "groups_reused": 0,
              "collapse_ms": 0.0, "errors": 0}
    seen = set()
    trees = []
    for obj in objects or ():
        try:
            mats = [s.material for s in obj.material_slots]
        except Exception:
            continue
        for m in mats:
            nt = getattr(m, "node_tree", None) if m is not None else None
            if nt is not None and nt.as_pointer() not in seen and getattr(m, "library", None) is None:
                seen.add(nt.as_pointer())
                trees.append((nt, None))
    if scene is not None:
        world = getattr(scene, "world", None)
        nt = getattr(world, "node_tree", None) if world is not None else None
        if nt is not None and nt.as_pointer() not in seen:
            seen.add(nt.as_pointer())
            trees.append((nt, False))
        comp = getattr(scene, "compositing_node_group", None)
        if comp is not None and comp.as_pointer() not in seen:
            seen.add(comp.as_pointer())
            # Layout only: lighting finds world and compositor nodes by name at the top level.
            trees.append((comp, False))
    yield
    for nt, collapse in trees:
        try:
            yield from _hook_steps(nt, family, collapse, totals)
        except Exception as exc:
            totals["errors"] += 1
            print("[graph_layout] finalize failed on %s: %s" % (getattr(nt, "name", "?"), exc))
        yield
    totals["ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
    return totals


def find_node(tree, name, recursive=True):
    """Node *name* on *tree*, else (recursive) inside the group trees it uses, depth first.
    Collapse moves nodes into zone/stage groups, so a name lookup on the top level alone misses."""
    if tree is None or not name:
        return None
    node = tree.nodes.get(name)
    if node is not None or not recursive:
        return node
    seen, stack = {tree.name}, [tree]
    while stack:
        cur = stack.pop()
        for n in cur.nodes:
            sub = getattr(n, "node_tree", None) if n.type == "GROUP" else None
            if sub is None or sub.name in seen:
                continue
            seen.add(sub.name)
            hit = sub.nodes.get(name)
            if hit is not None:
                return hit
            stack.append(sub)
    return None
