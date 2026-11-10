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


def tidy_tree(nt):
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
