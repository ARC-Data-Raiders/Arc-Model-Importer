"""Collapse anonymous outfit-graph math into shared stage-block node groups.

Entry point ``collapse_and_arrange(tree)`` runs once per ground-truth outfit
material after ``_dr_ops.organize``. Pinned nodes (texture, parameter, shader
and any node that code finds by name after the build) stay top level. Every
other node is grouped into one block per (stage, dependency rank). Blocks are
shared between materials when their structure, props and constant inputs
match (signature in the group name).

The pass reads the tree once, plans every block and the final link list in
Python, and mutates the tree last (links cleared, members removed, group
nodes and links created, one foreach_set for locations). A failure while
planning leaves the tree untouched.

Layout: spine nodes sit in dependency columns ordered by barycenter. Each
source (parameter, texture, UV) is stacked left of its first consumer in
socket order, so the consumer, its inputs and one frame read as one unit.

Kill switch: env DR_GRAPH_BLOCKS=0. Debug filter: DR_GRAPH_BLOCKS_ONLY=<stage[,stage]>.
"""
import ast, hashlib, heapq, json, os, re, time
from collections import defaultdict
from pathlib import Path

import bpy

RECIPE = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]

# Name, legacy-name, alias and semantic-id patterns that stay top level.
# Each entry cites the post-build lookup that needs the node at top level.
PIN_PATTERNS = (
    (r'^P:', 'Shared/materials/param_bind.py:74, Shared/shader_pick.py:582'),
    (r'^GT_Principled', 'Shared/materials/dispatch.py:450'),
    (r'^GT_NormalMap', 'graph contract, normal output lookups'),
    (r'^GT_MaterialOutput', 'graph contract, output lookups'),
    (r'^GT_mid_x8', 'Shared/shader_pick.py:111'),
    (r'^DR_DecalProj', 'Shared/shader_pick.py:630'),
    (r'^GT_ColorTex_UV_orient', 'Shared/materials/clothing.py:2004'),
    (r'^GT_decal\d+_layer', 'tests/test_windows_texture_paths_blender.py:69'),
    (r'decal\d+_use(\.\d+)?$', 'Shared/shader_control_ops.py:962'),
    (r'\.Decal\..*\.LayerGate$', 'Shared/shader_control_ops.py:968'),
    (r'_ColorMaskSwatch$', 'Shared/materials/clothing.py:1810'),
    (r'BandRamp$', 'Script_Helpers/mask_debug.py:1977'),
    (r'GT_DecalLayer|GT_DecalTint|DR\.PackedDecal|GT_DecalSlotCoverage', 'Shared/shader_control_ops.py:877,965'),
    (r'^GT_Colour', 'Shared/materials/gt_outfit_pbr.py:2414, Shared/materials/clothing.py:1529'),
    (r'^Color[ABC]2?$', 'Shared/materials/clothing.py:1529-1534, 1898-1900'),
    (r'_BaseColorOverlay$', 'Shared/materials/clothing.py:1537, 4563'),
    (r'^DecalData|^NormalAlphaMix', 'Shared/utils.py:1291-1439'),
    (r'^ArcBodyCoverage', 'Outfit_Accessories/body_mask.py:428-463'),
    (r'^ArcSky', 'sidebar.py:792-796'),
    (r'^GT_mux_', 'Shared/materials/dr_primary_graph/lookup.py _restore_selector'),
    (r'^GT_Pattern_z\d', 'Shared/materials/clothing.py:3732'),
    (r'^GT_z\d+_MediumN$', 'Shared/materials/clothing.py:3734'),
    (r'^GT_canvas_white$', 'Shared/materials/gt_outfit_pbr.py:2464'),
    (r'^GT_ZonePick', 'Shared/materials/gt_outfit_pbr.py:733'),
    (r'^DR\.UserControls$', 'Shared/materials/user_controls.py material_targets (per-material live sliders)'),
    (r'^DR\.ZoneOffsets$', 'Shared/materials/user_controls.py zone_offset_targets'),
    (r'^DR\.Look$', 'Shared/materials/user_controls.py look_linked (look values read from the object)'),
)
_PIN_RE = [re.compile(p) for p, _src in PIN_PATTERNS]

# Value and RGB nodes are pinned only as parameters (P: name or dr.parameter_key).
PIN_BL_EXACT = {
    'ShaderNodeTexImage', 'ShaderNodeUVMap',
    'ShaderNodeAttribute', 'ShaderNodeNewGeometry', 'ShaderNodeTexCoord',
    'ShaderNodeNormalMap', 'ShaderNodeOutputMaterial', 'ShaderNodeMixShader',
    'ShaderNodeAddShader', 'ShaderNodeEmission', 'ShaderNodeBsdfTransparent',
    'ShaderNodeBsdfPrincipled',
}
OUTPUT_BL = {'ShaderNodeOutputMaterial', 'NodeGroupOutput'}
HIDE_BL = {'ShaderNodeValue', 'ShaderNodeRGB', 'ShaderNodeMath', 'ShaderNodeClamp'}
SRC_BL = {'ShaderNodeValue', 'ShaderNodeRGB', 'ShaderNodeUVMap', 'ShaderNodeTexCoord',
          'ShaderNodeAttribute', 'ShaderNodeNewGeometry'}
MAX_BLOCK = 150
SLOT_W = 300.0
GAP_X = 140.0
GAP_Y = 40.0
GAP_FRAME = 110.0
SKIP_PROPS = {
    'name', 'label', 'location', 'location_absolute', 'width', 'width_hidden', 'height',
    'dimensions', 'parent', 'select', 'hide', 'inputs', 'outputs', 'internal_links', 'type',
    'rna_type', 'show_options', 'show_preview', 'show_texture', 'is_active_output',
    'warning_propagation',
}
SOCKET_TYPES = {
    'VALUE': 'NodeSocketFloat', 'INT': 'NodeSocketInt', 'BOOLEAN': 'NodeSocketBool',
    'VECTOR': 'NodeSocketVector', 'RGBA': 'NodeSocketColor', 'SHADER': 'NodeSocketShader',
    'STRING': 'NodeSocketString', 'ROTATION': 'NodeSocketRotation', 'MATRIX': 'NodeSocketMatrix',
}
_ZONE_RE = re.compile(r'(?:_z|[Zz]one)(\d+)|[Dd]ecal(\d+)|[Ll]ayer(\d+)')
_PROP_CACHE = {}


def _ser(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return round(v, 9)
    if isinstance(v, (set, frozenset)):
        return sorted(str(x) for x in v)
    if hasattr(v, 'bl_rna') and hasattr(v, 'name') and not hasattr(v, '__len__'):
        return 'ID:' + str(v.name)
    try:
        return tuple(_ser(x) for x in v)
    except TypeError:
        return type(v).__name__


def _props_of(node):
    bl = node.bl_idname
    cached = _PROP_CACHE.get(bl)
    if cached is None:
        cached = [p.identifier for p in node.bl_rna.properties
                  if not p.is_readonly and p.identifier not in SKIP_PROPS and p.identifier != 'rna_type']
        _PROP_CACHE[bl] = cached
    return cached


def _copy_props(src, dst):
    for pid in _props_of(src):
        try:
            setattr(dst, pid, getattr(src, pid))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass


def _copy_ids(src, dst):
    for key in list(src.keys()):
        try:
            value = src[key]
            if isinstance(value, (int, float, str, bool)):
                dst[key] = value
        except (KeyError, TypeError, ValueError):
            pass


def _ser_props(node):
    return [(pid, _ser(getattr(node, pid, None))) for pid in _props_of(node)]


def _ser_ids(node):
    out = []
    for key in sorted(node.keys()):
        if any(t in key for t in ('legacy', 'semantic', 'generated_by', 'parameter_key')):
            continue
        value = node[key]
        if isinstance(value, (int, float, str, bool)):
            out.append((key, value))
    return out


def _is_param(node):
    return node.get('dr.parameter_key') is not None or node.name.startswith('P:')


_PRO_TEXT = ('decal', 'detail normal', 'detail_normal', 'detailnormal', 'emissi')


def _is_pro_target(node):
    """Mirror of shader_control_ops.pro_classify: the Decals / Detail normal / Emission
    toggles mute matching top-level nodes by name, so those nodes must stay top level."""
    if node.bl_idname in ('NodeFrame', 'NodeReroute'):
        return False
    tree = getattr(node, 'node_tree', None)
    if tree is not None and ('arc_gt_ver' in tree or tree.name.startswith(('DR.', 'Independent'))):
        return False
    text = ('%s|%s' % (node.name, node.label)).lower()
    return any(t in text for t in _PRO_TEXT) or (tree is not None and tree.name.lower().startswith('emissi'))


def _is_pinned(node, frames_with_sel):
    bl = node.bl_idname
    if bl in PIN_BL_EXACT or bl.startswith('ShaderNodeBsdf') or bl.startswith('ShaderNodeOutput'):
        return True
    if _is_param(node):
        return True
    if any(str(k).startswith(('dr.general.', 'dr.selection.')) for k in node.keys()):
        return True
    if node.parent is not None and node.parent.as_pointer() in frames_with_sel:
        return True
    if _is_pro_target(node):
        return True
    cands = [node.name, str(node.get('dr.legacy_name', '') or ''), str(node.get('dr.semantic_id', '') or '')]
    raw = node.get('dr.legacy_aliases')
    if isinstance(raw, str):
        try:
            cands.extend(str(x) for x in json.loads(raw))
        except ValueError:
            pass
    if bl == 'ShaderNodeGroup' and node.node_tree is not None:
        cands.append(node.node_tree.name)
    return any(r.search(c) for c in cands for r in _PIN_RE)


def _identifiers(node):
    out = {node.name, str(node.get('dr.legacy_name', '') or ''), str(node.get('dr.semantic_id', '') or '')}
    raw = node.get('dr.legacy_aliases')
    if isinstance(raw, str):
        try:
            out.update(str(x) for x in json.loads(raw))
        except ValueError:
            pass
    out.discard('')
    return out


def _topo(names, preds, key):
    succs = {n: [] for n in names}
    indeg = {n: len(preds[n]) for n in names}
    for n in names:
        for p in preds[n]:
            succs[p].append(n)
    heap = [(key[n], n) for n in names if indeg[n] == 0]
    heapq.heapify(heap)
    out = []
    while heap:
        _k, n = heapq.heappop(heap)
        out.append(n)
        for s in succs[n]:
            indeg[s] -= 1
            if indeg[s] == 0:
                heapq.heappush(heap, (key[s], s))
    return out if len(out) == len(names) else None


def _is_sel_frame(node):
    return node.bl_idname == 'NodeFrame' and node.get('dr.selection.prefix') is not None


def _sel_binding_names(tree):
    out = set()

    def walk(v):
        if isinstance(v, str):
            out.add(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    for f in tree.nodes:
        if not _is_sel_frame(f):
            continue
        for key in f.keys():
            if not str(key).startswith('dr.selection.'):
                continue
            raw = f[key]
            if isinstance(raw, str):
                try:
                    walk(json.loads(raw))
                except ValueError:
                    pass
    return out


def _abs(node):
    x, y = float(node.location.x), float(node.location.y)
    p = node.parent
    while p is not None:
        x += float(p.location.x)
        y += float(p.location.y)
        p = p.parent
    return x, y


def _sock(socks, identifier):
    return next((x for x in socks if x.identifier == identifier), None)


def _stage(node):
    return str(node.get('dr.stage') or 'DR.Misc').split('.')[-1]


def _width_for(bl):
    if bl == 'ShaderNodeGroup':
        return 260.0
    if bl == 'ShaderNodeTexImage':
        return 240.0
    return 180.0


def _height(bl, hidden, vis):
    # Upper bound of graph_metrics.node_h (60 + 22/socket, +260 image, +60 value/rgb, 40 hidden).
    if hidden:
        return 45.0
    h = 80.0 + 24.0 * vis
    if bl == 'ShaderNodeTexImage':
        h += 300.0
    if bl in ('ShaderNodeValue', 'ShaderNodeRGB'):
        h += 80.0
    if bl == 'ShaderNodeBsdfPrincipled':
        h = max(h, 700.0)
    return h


def _vis(node):
    return sum(1 for s in node.inputs if s.enabled and not s.hide) + \
        sum(1 for s in node.outputs if s.enabled and not s.hide)


def _layout(names, bl, wid, hgt, edges, label=None, sources=True):
    """Pure-Python layered layout. Returns (pos, items) where pos[name] = (x, y)
    top-left and items is [(column, label, [names])] in column/stack order."""
    preds = {n: set() for n in names}
    succs = {n: set() for n in names}
    out_l = defaultdict(list)
    in_l = defaultdict(list)
    for a, fi, b, ti in edges:
        if a == b or a not in preds or b not in preds:
            continue
        preds[b].add(a)
        succs[a].add(b)
        out_l[a].append((b, fi, ti))
        in_l[b].append((a, fi, ti))
    tier1, tex = set(), set()
    if sources:
        tier1 = {n for n in names if bl[n] in SRC_BL and not preds[n] and succs[n]}
        tex = {n for n in names if bl[n] == 'ShaderNodeTexImage' and succs[n] and preds[n] <= tier1}
    srcs = tier1 | tex
    spine = sorted(n for n in names if n not in srcs)
    sp = set(spine)
    sp_pred = {n: preds[n] & sp for n in spine}
    sp_succ = {n: succs[n] & sp for n in spine}
    order = _topo(spine, sp_pred, {n: n for n in spine})
    if order is None:
        print('DR_BLOCKS_ARRANGE_CYCLE', len(spine))
        order = spine
    col = {}
    for n in order:
        col[n] = max((col[p] + 1 for p in sp_pred[n]), default=0)
    for n in reversed(order):
        if not sp_pred[n] and sp_succ[n]:
            col[n] = min(col[c] for c in sp_succ[n]) - 1
    # Move each inner node toward the median of its link ends (inside its slack)
    # so long links shorten; this cut crossings ~30% on outfit wrappers.
    for _it in range(4):
        for n in reversed(order):
            if not sp_succ[n] or not sp_pred[n]:
                continue
            lo_c = max(col[p] for p in sp_pred[n]) + 1
            hi_c = min(col[c] for c in sp_succ[n]) - 1
            if hi_c <= lo_c:
                continue
            ends = sorted([col[p] + 1 for p in sp_pred[n]] + [col[c] - 1 for c in sp_succ[n]])
            col[n] = max(lo_c, min(hi_c, ends[len(ends) // 2]))
    if spine:
        shift = -min(col.values())
        for n in spine:
            col[n] += shift
    maxc = max(col.values(), default=0)
    for n in spine:
        if bl[n] in OUTPUT_BL and not sp_succ[n]:
            col[n] = maxc + 1
    cols = defaultdict(list)
    for n in spine:
        cols[col[n]].append(n)
    nin = {n: max(1, len(in_l[n]) and max(t for _a, _f, t in in_l[n]) + 1) for n in spine}
    nout = {n: max(1, len(out_l[n]) and max(f for _b, f, _t in out_l[n]) + 1) for n in spine}
    adj_o = defaultdict(list)
    adj_i = defaultdict(list)
    vcount = 0
    for a in spine:
        for b, fi, ti in out_l[a]:
            if b not in sp:
                continue
            fa, tb = fi / (nout[a] + 1), ti / (nin[b] + 1)
            chain = [a]
            for c in range(col[a] + 1, col[b]):
                v = '\x00v%d' % vcount
                vcount += 1
                cols[c].append(v)
                chain.append(v)
            chain.append(b)
            for k in range(len(chain) - 1):
                p, q = chain[k], chain[k + 1]
                fp = fa if k == 0 else 0.0
                fq = tb if k == len(chain) - 2 else 0.0
                adj_o[p].append((q, fq, fp))
                adj_i[q].append((p, fp, fq))
    ncols = sorted(cols)
    pos = {}
    for c in ncols:
        for i, n in enumerate(cols[c]):
            pos[n] = float(i)
    for sweep in range(4):
        down = sweep % 2 == 0
        seq = list(reversed(ncols)) if down else ncols
        for c in seq:
            def key(n):
                adj = adj_o[n] if down else adj_i[n]
                nb = [pos[m] + fm for m, fm, _fs in adj]
                if not nb:
                    return (pos[n], n)
                return (sum(nb) / len(nb), n)
            cols[c].sort(key=key)
            for i, n in enumerate(cols[c]):
                pos[n] = float(i)
    for c in ncols:
        cols[c] = [n for n in cols[c] if n in sp]
    ncols = [c for c in ncols if cols[c]]
    # anchor each source at its leftmost spine consumer
    anchor, slot = {}, {}

    def rank_of(c):
        return (col[c], pos[c], c)

    for t in tex:
        anchor[t] = min(succs[t] & sp, key=rank_of)
        slot[t] = 1
    for s in tier1:
        cands = list(succs[s] & sp) + [anchor[t] for t in succs[s] & tex]
        anchor[s] = min(cands, key=rank_of)
        slot[s] = 2 if succs[s] & tex else 1
    stacks = defaultdict(lambda: ([], []))
    tidx = {}
    for s in sorted(anchor):
        a = anchor[s]
        if slot[s] == 1:
            tidx[s] = min((ti for b, _fi, ti in out_l[s] if b == a), default=999)
            stacks[a][0].append(s)
    for a, (s1, _s2) in stacks.items():
        s1.sort(key=lambda s: (tidx[s], s))
    for s in sorted(anchor):
        if slot[s] == 2:
            a = anchor[s]
            row = {t: i for i, t in enumerate(stacks[a][0])}
            k1 = min((row[b] for b in succs[s] if b in row), default=999)
            k2 = min((ti for b, _fi, ti in out_l[s] if b == a), default=999)
            tidx[s] = (k1, k2)
            stacks[a][1].append(s)
    for a, (_s1, s2) in stacks.items():
        s2.sort(key=lambda s: (tidx[s], s))

    def stack_h(lst):
        return sum(hgt[s] + 30.0 for s in lst) - 30.0 if lst else 0.0

    ih = {n: max(hgt[n], stack_h(stacks[n][0]) if n in stacks else 0.0,
                 stack_h(stacks[n][1]) if n in stacks else 0.0) for n in spine}
    lead = {c: SLOT_W * max((2 if stacks[n][1] else 1) for n in cols[c] if n in stacks) if any(n in stacks for n in cols[c]) else 0.0
            for c in ncols}
    xs = {}
    x = 0.0
    for c in ncols:
        x += lead[c]
        xs[c] = x
        x += max(wid[n] for n in cols[c]) + GAP_X
    lab = label or {}
    ys = {}
    for c in ncols:
        y = 0.0
        for n in cols[c]:
            ys[n] = y
            y -= ih[n] + GAP_Y

    def pack(c, use_out, use_in):
        desired = {}
        for n in cols[c]:
            v = []
            if use_out:
                v += [ys[m] - 22.0 * ti + 22.0 * fi for m, fi, ti in out_l[n] if m in sp]
            if use_in:
                v += [ys[m] - 22.0 * fi + 22.0 * ti for m, fi, ti in in_l[n] if m in sp]
            desired[n] = sum(v) / len(v) if v else ys[n]
        prev = None
        prev_lab = None
        for n in cols[c]:
            if prev is None:
                y = desired[n]
            else:
                gap = GAP_FRAME if lab.get(n) != prev_lab else GAP_Y
                y = min(desired[n], prev - gap)
            ys[n] = y
            prev = y - ih[n]
            prev_lab = lab.get(n)
        resid = [desired[n] - ys[n] for n in cols[c]]
        lift = sum(resid) / len(resid) if resid else 0.0
        if lift > 0:
            for n in cols[c]:
                ys[n] += lift

    pack(ncols[0], False, False) if ncols else None
    for c in reversed(ncols):
        pack(c, True, False)
    for c in ncols:
        pack(c, True, True)
    for c in reversed(ncols):
        pack(c, True, True)

    # Rank sweeps above order by row index; re-sort each column by the pixel y its
    # links want (label runs kept together), then re-pack.
    def _want(n):
        v = [ys[m] - 22.0 * ti + 22.0 * fi for m, fi, ti in out_l[n] if m in sp]
        v += [ys[m] - 22.0 * fi + 22.0 * ti for m, fi, ti in in_l[n] if m in sp]
        return -(sum(v) / len(v)) if v else -ys[n]

    for r in range(8):
        for c in (ncols if r % 2 == 0 else list(reversed(ncols))):
            runs = {}
            for n in cols[c]:
                runs.setdefault(lab.get(n), []).append(n)
            want = {n: _want(n) for n in cols[c]}
            rk = {k: sum(want[n] for n in v) / len(v) for k, v in runs.items()}
            cols[c] = [n for k in sorted(runs, key=lambda k: (rk[k], str(k)))
                       for n in sorted(runs[k], key=lambda n: (want[n], n))]
            pack(c, True, True)
    out = {}
    items = []
    for c in ncols:
        run = None
        for n in cols[c]:
            out[n] = (xs[c], ys[n])
            members = [n]
            if n in stacks:
                for k, lst in enumerate(stacks[n]):
                    y = ys[n]
                    for s in lst:
                        out[s] = (xs[c] - SLOT_W * (k + 1), y)
                        y -= hgt[s] + 30.0
                        members.append(s)
            if run is not None and run[1] == lab.get(n):
                run[2].extend(members)
            else:
                run = [c, lab.get(n), members]
                items.append(run)
    for n in names:
        if n not in out:
            out[n] = (0.0, 0.0)
    return out, items


def _tree_layout(gt):
    """Lay out a small group tree in place (sources=False, no frames)."""
    nodes = [n for n in gt.nodes if n.bl_idname != 'NodeFrame']
    names = [n.name for n in nodes]
    bl = {n.name: n.bl_idname for n in nodes}
    for n in nodes:
        w = _width_for(n.bl_idname)
        if abs(n.width - w) > 0.5:
            n.width = w
    wid = {n.name: float(n.width) for n in nodes}
    hgt = {n.name: _height(n.bl_idname, n.hide, _vis(n)) for n in nodes}
    op, ip = {}, {}
    for n in nodes:
        for i, s in enumerate(n.outputs):
            op[s.as_pointer()] = (n.name, i)
        for i, s in enumerate(n.inputs):
            ip[s.as_pointer()] = (n.name, i)
    edges = []
    for lk in gt.links:
        a = op.get(lk.from_socket.as_pointer())
        b = ip.get(lk.to_socket.as_pointer())
        if a and b:
            edges.append((a[0], a[1], b[0], b[1]))
    pos, _items = _layout(names, bl, wid, hgt, edges, sources=False)
    flat = []
    for n in gt.nodes:
        x, y = pos.get(n.name, (float(n.location.x), float(n.location.y)))
        flat += [x, y]
    gt.nodes.foreach_set('location', flat)


def _material_name(tree):
    for m in bpy.data.materials:
        if m.node_tree is not None and m.node_tree.as_pointer() == tree.as_pointer():
            return m.name
    return tree.name


def _iface_out_names(gt):
    return [it.name for it in gt.interface.items_tree
            if getattr(it, 'item_type', '') == 'SOCKET' and it.in_out == 'OUTPUT']


def _build_group(stage, sig, members, byn, plan, linked):
    base = f'DR · {stage} · {sig[:10]}'
    gt = bpy.data.node_groups.new(base, 'ShaderNodeTree')
    gt['dr.block.signature'] = sig
    gt['dr.block.stage'] = stage
    used = set()
    for side, key in (('INPUT', 'ins'), ('OUTPUT', 'outs')):
        for label, sock_type in plan[key]:
            name = label
            k = 2
            while name in used:
                name = f'{label} {k}'
                k += 1
            used.add(name)
            gt.interface.new_socket(name=name, in_out=side, socket_type=sock_type)
    gin = gt.nodes.new('NodeGroupInput')
    gout = gt.nodes.new('NodeGroupOutput')
    new = {}
    for n in members:
        src = byn[n]
        dst = gt.nodes.new(src.bl_idname)
        dst.name = n
        _copy_props(src, dst)
        _copy_ids(src, dst)
        new[n] = dst
        for si, s in enumerate(src.inputs):
            d = _sock(dst.inputs, s.identifier)
            if d is None:
                continue
            try:
                d.hide = s.hide
            except (AttributeError, TypeError):
                pass
            if (n, si) not in linked and hasattr(s, 'default_value'):
                try:
                    d.default_value = s.default_value
                except (TypeError, ValueError, AttributeError):
                    pass
        for oi, o in enumerate(src.outputs):
            if oi < len(dst.outputs):
                try:
                    dst.outputs[oi].hide = o.hide
                except (AttributeError, TypeError):
                    pass
    for (ln, si, lf, fi) in plan['internal']:
        a = _sock(new[members[lf]].outputs, fi)
        b = _sock(new[members[ln]].inputs, si)
        if a is None or b is None:
            raise RuntimeError(f'DR_BLOCKS socket missing {members[lf]}.{fi} -> {members[ln]}.{si}')
        gt.links.new(a, b)
    for (ext_i, ln, si) in plan['links_in']:
        b = _sock(new[members[ln]].inputs, si)
        if b is None:
            raise RuntimeError(f'DR_BLOCKS socket missing {members[ln]}.{si}')
        gt.links.new(gin.outputs[ext_i], b)
    for j, (ln, oi) in enumerate(plan['ext_out']):
        gt.links.new(new[members[ln]].outputs[oi], gout.inputs[j])
    _tree_layout(gt)
    return gt


def _merge_blocks(blocks, order, preds, stage_of, pinned, cap=MAX_BLOCK, small=3):
    """Merge same-stage (stage, rank) blocks while the quotient graph stays acyclic,
    then fold blocks smaller than ``small`` into a linked neighbour block.

    Merging A and B is safe when no path leaves A and reaches B (or the reverse)
    through a node outside A and B. Blocks are visited in rank order, so the
    result is deterministic for a given graph.
    """
    gid = {}
    groups = {}
    for k in sorted(blocks, key=lambda k: (k[1], k[0])):
        groups[k] = list(blocks[k])
        for n in blocks[k]:
            gid[n] = k
    succ = defaultdict(set)
    for b, ps in preds.items():
        for a in ps:
            succ[a].add(b)

    def unit(n):
        return gid.get(n, ('#', n))

    def reaches(src, dst):
        start = set()
        for n in groups[src]:
            for m in succ[n]:
                u = unit(m)
                if u != src and u != dst:
                    start.add(u)
        seen, stack = set(start), list(start)
        while stack:
            u = stack.pop()
            members = groups[u] if u in groups else [u[1]]
            for n in members:
                for m in succ[n]:
                    v = unit(m)
                    if v == dst:
                        return True
                    if v != src and v not in seen:
                        seen.add(v)
                        stack.append(v)
        return False

    def fold(k, target):
        for n in groups[k]:
            gid[n] = target
        groups[target].extend(groups.pop(k))

    by_stage = defaultdict(list)
    for k in list(groups):
        by_stage[k[0]].append(k)
    for stage in sorted(by_stage):
        kept = []
        for k in by_stage[stage]:
            target = None
            for t in kept:
                if len(groups[t]) + len(groups[k]) > cap:
                    continue
                if not reaches(t, k) and not reaches(k, t):
                    target = t
                    break
            if target is None:
                kept.append(k)
                continue
            fold(k, target)
    for k in sorted([k for k in groups if len(groups[k]) < small], key=lambda k: (k[1], k[0])):
        if k not in groups or len(groups[k]) >= small:
            continue
        nb = set()
        for n in groups[k]:
            for m in list(succ[n]) + list(preds.get(n, ())):
                u = gid.get(m)
                if u is not None and u != k:
                    nb.add(u)
        for t in sorted(nb, key=lambda t: (t[0] != k[0], -len(groups[t]), t[1], t[0])):
            if len(groups[t]) + len(groups[k]) > cap:
                continue
            if not reaches(t, k) and not reaches(k, t):
                fold(k, t)
                break
    pos = {n: i for i, n in enumerate(order)}
    return {k: sorted(v, key=pos.get) for k, v in groups.items()}


def _frame_label(stage, name):
    m = _ZONE_RE.search(name)
    if not m:
        return stage
    if m.group(1):
        return f'{stage} · zone {m.group(1)}'
    if m.group(2):
        return f'{stage} · decal {m.group(2)}'
    return f'{stage} · layer {m.group(3)}'


SLICE_S = 0.15

# ---- per-material wrapper ("DR Outfit") ----
# The material tree keeps only the shader nodes and one wrapper group node. The wrapper tree is
# one per material (it holds that material's values); the DR · <stage> · <sig> blocks inside it
# stay shared. param_bind carries the same two names.
WRAPPER_NODE = 'DR Outfit'
WRAPPER_TAG = 'dr.wrapper'

# The parameters a person adjusts to change the look: (key pattern, panel). One list for every
# outfit. A matching parameter becomes an input of the wrapper node; all others stay inside.
LOOK_PANELS = ('Colours', 'Surface', 'Wear and dirt', 'Pattern and detail', 'Decals', 'Emission')
LOOK_PATTERNS = (
    (r'^\d+_PatternColor[ABC]$', 0),
    (r'^HueVariationStrength$', 0),
    (r'^\d+_Base(Roughness|Metallicity|Specular)$', 1),
    (r'^Specular$', 1),
    (r'^(Edge|Crease) (amount|softness|Strength)$', 2),
    (r'^\d+_(Edge|Crease)ColorOverlay$', 2),
    (r'^\d+_(PatternTiling|MediumNormalStrength|DetailTiling)$', 3),
    (r'^\d+_UseDecal$', 4),
    (r'^OverallEmissive$', 5),
)
_LOOK_RE = [(re.compile(p), panel) for p, panel in LOOK_PATTERNS]
_CURVE_BL = {'ShaderNodeRGBCurve', 'ShaderNodeVectorCurve', 'ShaderNodeFloatCurve'}


def _look_panel(key):
    for rx, panel in _LOOK_RE:
        if rx.search(key):
            return panel
    return None


def _look_label(key):
    m = re.match(r'^(\d+)_(.+)$', key)
    base = m.group(2) if m else key
    base = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', base)
    return (f'{base} · layer {m.group(1)}', base, int(m.group(1))) if m else (base, base, -1)


def _plain(v):
    if isinstance(v, (set, frozenset)):
        return set(v)
    if isinstance(v, (str, bytes)) or hasattr(v, 'bl_rna'):
        return v
    if hasattr(v, '__len__'):
        return tuple(_plain(x) for x in v)
    return v


def _snap_node(src, linked=()):
    """Plain-data copy of a node, valid after the source node is freed."""
    nm = src.name
    bl = src.bl_idname
    d = {'bl': bl, 'name': nm, 'label': src.label, 'hide': bool(src.hide), 'width': float(src.width),
         'loc': (float(src.location.x), float(src.location.y)), 'props': [], 'ids': [], 'ins': [], 'outs': []}
    for pid in _props_of(src):
        try:
            d['props'].append((pid, _plain(getattr(src, pid))))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass
    for key in list(src.keys()):
        try:
            v = src[key]
        except (KeyError, TypeError):
            continue
        if hasattr(v, 'to_dict'):
            v = v.to_dict()
        elif hasattr(v, 'to_list'):
            v = v.to_list()
        d['ids'].append((key, v))
    for si, s in enumerate(src.inputs):
        dv = None
        if (nm, si) not in linked and hasattr(s, 'default_value'):
            dv = _plain(s.default_value)
        d['ins'].append((s.identifier, bool(s.hide), dv))
    keep_out = bl in ('ShaderNodeValue', 'ShaderNodeRGB')
    for o in src.outputs:
        d['outs'].append((bool(o.hide), _plain(o.default_value) if keep_out and hasattr(o, 'default_value') else None))
    cr = getattr(src, 'color_ramp', None)
    if cr is not None:
        d['ramp'] = (cr.color_mode, cr.interpolation, cr.hue_interpolation,
                     [(float(e.position), tuple(e.color)) for e in cr.elements])
    if bl in _CURVE_BL:
        mp = src.mapping
        d['curves'] = [[(float(p.location[0]), float(p.location[1]), p.handle_type) for p in c.points]
                       for c in mp.curves]
    return d


def _make_node(tree, d, loc=False):
    """loc=False: the caller's _layout assigns the location, so skip that tree-update write."""
    dst = tree.nodes.new(d['bl'])
    dst.name = d['name']
    dst.label = d['label']
    for pid, v in d['props']:
        try:
            setattr(dst, pid, v)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass
    for key, v in d['ids']:
        try:
            dst[key] = v
        except (KeyError, TypeError, ValueError):
            pass
    by_id = {s.identifier: s for s in dst.inputs}
    for ident, hide, dv in d['ins']:
        s = by_id.get(ident)
        if s is None:
            continue
        if dv is not None:
            try:
                s.default_value = dv
            except (TypeError, ValueError, AttributeError):
                pass
        if hide:
            try:
                s.hide = True
            except (AttributeError, TypeError):
                pass
    for oi, (hide, dv) in enumerate(d['outs']):
        if oi >= len(dst.outputs):
            break
        o = dst.outputs[oi]
        if dv is not None:
            try:
                o.default_value = dv
            except (TypeError, ValueError, AttributeError):
                pass
        if hide:
            try:
                o.hide = True
            except (AttributeError, TypeError):
                pass
    if 'ramp' in d:
        cr = dst.color_ramp
        cr.color_mode, cr.interpolation, cr.hue_interpolation, els = d['ramp']
        while len(cr.elements) > 1:
            cr.elements.remove(cr.elements[-1])
        for i, (p, c) in enumerate(els):
            e = cr.elements[0] if i == 0 else cr.elements.new(p)
            e.position = p
            e.color = c
    if 'curves' in d:
        mp = dst.mapping
        for c, pts in zip(mp.curves, d['curves']):
            while len(c.points) < len(pts):
                c.points.new(0.0, 0.0)
            for p, (x, y, h) in zip(c.points, pts):
                p.location = (x, y)
                p.handle_type = h
        mp.update()
    dst.width = d['width']
    if loc:
        dst.location = d['loc']
    dst.hide = d['hide']
    return dst


def _wrap_plan(names, byn, outs, ins, final, gnodes, gone, look):
    """Which nodes stay in the material tree (shader nodes, outputs and what they feed) and
    which sockets the wrapper exposes. None when a block would have to stay top level."""
    succ = defaultdict(set)
    for a, _fi, b, _ti, _m in final:
        succ[a].add(b)
    topset = {n for n in names if n not in gone and (
        byn[n].bl_idname.startswith('ShaderNodeOutput') or any(o.type == 'SHADER' for o in outs[n]))}
    stack = list(topset)
    while stack:
        for s in succ[stack.pop()]:
            if s not in topset:
                topset.add(s)
                stack.append(s)
    if not topset or any(n in gnodes or n in look for n in topset):
        return None
    out_idx, out_plan, top_links = {}, [], []
    used = set()
    for a, fi, b, ti, mu in sorted((x for x in final if x[2] in topset), key=lambda x: (x[2], x[3])):
        if a in topset:
            top_links.append((None, a, fi, b, ti, mu))
            continue
        j = out_idx.get((a, fi))
        if j is None:
            j = len(out_plan)
            out_idx[(a, fi)] = j
            stype = gnodes[a]['out_types'][fi] if a in gnodes else outs[a][fi].type
            label = ins[b][ti].name
            name, k = label, 2
            while name in used:
                name = f'{label} {k}'
                k += 1
            used.add(name)
            out_plan.append((name, SOCKET_TYPES.get(stype, 'NodeSocketFloat')))
        top_links.append((j, None, 0, b, ti, mu))
    return {'topset': topset, 'out_idx': out_idx, 'out_plan': out_plan, 'top_links': top_links}


def _swap_top(top, w, snaps, plan, look_inputs):
    """Replace the flat material tree by its shader nodes plus the wrapper node."""
    top.nodes.clear()
    new = {d['name']: _make_node(top, d) for d in snaps}
    wn = top.nodes.new('ShaderNodeGroup')
    wn.node_tree = w
    wn.name = WRAPPER_NODE
    wn.label = 'Outfit look'
    wn.width = 320.0
    reg = {}
    by_id = {s.identifier: s for s in wn.inputs}
    for key, ident, value, _label in look_inputs:
        s = by_id.get(ident)
        if s is None:
            continue
        s.default_value = value
        reg[ident] = key
    wn[PARAM_INPUTS] = json.dumps(reg, sort_keys=True)
    edges = []
    for j, a, fi, b, ti, mu in plan['top_links']:
        src = wn.outputs[j] if a is None else new[a].outputs[fi]
        lk = top.links.new(src, new[b].inputs[ti])
        if mu:
            lk.is_muted = True
        edges.append((WRAPPER_NODE if a is None else a, j if a is None else fi, b, ti))
    nodes = dict(new)
    nodes[WRAPPER_NODE] = wn
    lnames = sorted(nodes)
    bl = {n: nodes[n].bl_idname for n in lnames}
    wid = {n: float(nodes[n].width) for n in lnames}
    hgt = {n: _height(bl[n], nodes[n].hide, _vis(nodes[n])) for n in lnames}
    pos, _items = _layout(lnames, bl, wid, hgt, edges, sources=False)
    for n in lnames:
        nodes[n].location = pos[n]
    return wn


PARAM_INPUTS = 'dr.param_inputs'


_WRAP_TEMPLATES = {}    # structure signature -> template record (session only)
TEMPLATE_TAG = 'dr.wrapper.template'
TEMPLATE_REC = 'dr.wrapper.template.rec'   # repr of the record, so a saved or shipped template is reusable


def _template_record(wsig):
    """Session record for wsig, else one restored from a loaded template tree."""
    rec = _WRAP_TEMPLATES.get(wsig)
    if rec is not None:
        return rec
    tt = bpy.data.node_groups.get(f'.DR Outfit template {wsig[:12]}')
    if tt is None or tt.get(TEMPLATE_TAG) != wsig or tt.get('dr.blocks.recipe') != RECIPE:
        return None
    try:
        rec = ast.literal_eval(tt.get(TEMPLATE_REC) or '')
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None
    if not isinstance(rec, dict) or not {'snaps', 'gparams', 'look', 'links', 'frames'} <= set(rec):
        return None
    rec['tree'] = tt.name
    _WRAP_TEMPLATES[wsig] = rec
    return rec


def _norm(v):
    if hasattr(v, 'bl_rna'):
        try:
            return ('ID', type(v).__name__, getattr(v, 'name_full', None) or str(v))
        except ReferenceError:
            return ('ID', None, None)
    if isinstance(v, tuple):
        return tuple(_norm(x) for x in v)
    return v


def _norm_snap(d):
    o = {k: d[k] for k in ('label', 'hide', 'ids', 'ins', 'outs') if k in d}
    o['props'] = {pid: _norm(v) for pid, v in d['props']}
    o['ramp'] = d.get('ramp')
    o['curves'] = d.get('curves')
    return o


def _apply_ramp_curves(dst, d):
    if d.get('ramp') is not None:
        cr = dst.color_ramp
        cr.color_mode, cr.interpolation, cr.hue_interpolation, els = d['ramp']
        while len(cr.elements) > 1:
            cr.elements.remove(cr.elements[-1])
        for i, (p, c) in enumerate(els):
            e = cr.elements[0] if i == 0 else cr.elements.new(p)
            e.position = p
            e.color = c
    if d.get('curves') is not None:
        mp = dst.mapping
        for c, pts in zip(mp.curves, d['curves']):
            while len(c.points) < len(pts):
                c.points.new(0.0, 0.0)
            for p, (x, y, h) in zip(c.points, pts):
                p.location = (x, y)
                p.handle_type = h
        mp.update()


def _fill_diff(nd, d, o):
    """Write onto a template copy only what differs from the template's source node. Returns writes."""
    k = 0
    if d['label'] != o['label']:
        nd.label = d['label']
    if d['hide'] != o['hide']:
        nd.hide = d['hide']
    op = o['props']
    for pid, v in d['props']:
        if _norm(v) != op.get(pid):
            try:
                setattr(nd, pid, v)
                k += 1
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass
    if d['ids'] != o['ids']:
        for key, v in d['ids']:
            try:
                nd[key] = v
            except (KeyError, TypeError, ValueError):
                pass
    if d['ins'] != o['ins']:
        for (ident, hide, dv), (_i2, h2, dv2) in zip(d['ins'], o['ins']):
            if (dv is not None and dv != dv2) or hide != h2:
                s = _sock(nd.inputs, ident)
                if s is None:
                    continue
                try:
                    if dv is not None and dv != dv2:
                        s.default_value = dv
                        k += 1
                    if hide != h2:
                        s.hide = hide
                except (TypeError, ValueError, AttributeError):
                    pass
    if d['outs'] != o['outs']:
        for oi, ((hide, dv), (h2, dv2)) in enumerate(zip(d['outs'], o['outs'])):
            if oi >= len(nd.outputs):
                break
            try:
                if dv is not None and dv != dv2:
                    nd.outputs[oi].default_value = dv
                    k += 1
                if hide != h2:
                    nd.outputs[oi].hide = hide
            except (TypeError, ValueError, AttributeError):
                pass
    if d.get('ramp') != o['ramp'] or d.get('curves') != o['curves']:
        _apply_ramp_curves(nd, d)
        k += 1
    return k


def _wrap_sig(inner, byn, gnodes, final, look_keys, plan, sel_parent, nested=False):
    """Identity of a wrapper's structure and layout inputs, values excluded."""
    src = {
        'r': RECIPE,
        'nested': bool(nested),
        'nodes': [(n, byn[n].bl_idname, bool(byn[n].hide), _vis(byn[n]), _stage(byn[n]),
                   sel_parent[n].name if n in sel_parent else '') for n in inner],
        'groups': sorted((gn, g['stage'], len(g['ext_src']), len(g['outs']), sorted(g['params']))
                         for gn, g in gnodes.items()),
        'links': sorted(final),
        'look': sorted((k, isinstance(v, tuple)) for k, v in look_keys.items()),
        'outs': plan['out_plan'],
        'top': sorted(plan['topset']),
    }
    return hashlib.sha256(json.dumps(src, sort_keys=True, default=str).encode('utf-8')).hexdigest()


def _set_group_params(nd, g):
    reg = {}
    for i, (key, value) in g['params'].items():
        sock = nd.inputs[i]
        sock.default_value = value
        reg[sock.identifier] = key
    if reg:
        nd[PARAM_INPUTS] = json.dumps(reg, sort_keys=True)


def _from_template(wsig, wname, snaps, gnodes, look_keys):
    """Copy the stored wrapper of the same structure and write only the differing values.
    Generator: yields 'blocks' between the tree copies and the value writes so a staged
    import can redraw. Returns (tree, look_inputs, links, frames, writes) or None."""
    rec = _template_record(wsig)
    if rec is None:
        return None
    tt = bpy.data.node_groups.get(rec['tree'])
    if tt is None or tt.get(TEMPLATE_TAG) != wsig:
        _WRAP_TEMPLATES.pop(wsig, None)
        return None
    w = tt.copy()
    made = []
    try:
        w.name = wname
        del w[TEMPLATE_TAG]
        w[WRAPPER_TAG] = 1
        writes = 0
        trees = [w]
        mat = wname.split(' · ', 1)[-1]
        for snode, st in _stage_trees_of(w):
            c = st.copy()
            made.append(c)
            c.name = f'DR Stage · {snode.label} · {mat}'
            snode.node_tree = c
            trees.append(c)
        yield 'blocks'

        def find(name):
            for t in trees:
                x = t.nodes.get(name)
                if x is not None:
                    return x
            return None

        for n, d in snaps.items():
            nd = find(n)
            o = rec['snaps'].get(n)
            if nd is None or o is None:
                raise LookupError(n)
            writes += _fill_diff(nd, d, o)
        for gname, g in gnodes.items():
            nd = find(gname)
            tname, tparams = rec['gparams'][gname]
            if nd is None:
                raise LookupError(gname)
            if g['tree'].name != tname:
                nd.node_tree = g['tree']
                _set_group_params(nd, g)
                writes += 1 + len(g['params'])
            else:
                for i, (key, value) in g['params'].items():
                    if tparams.get(i) != (key, value):
                        nd.inputs[i].default_value = value
                        writes += 1
        total = sum(len(t.links) for t in trees)
        if total != rec['links']:
            raise LookupError(f'links {total} != {rec["links"]}')
        look_inputs = []
        for key, ident, label in rec['look']:
            look_inputs.append((key, ident, look_keys[key], label))
    except BaseException as exc:
        bpy.data.node_groups.remove(w)
        for c in made:
            if c.users == 0:
                bpy.data.node_groups.remove(c)
        if isinstance(exc, LookupError):
            print(f'DR_BLOCKS_TEMPLATE_MISS {wname} {exc}', flush=True)
            return None
        raise
    return w, look_inputs, rec['links'], rec['frames'], writes


def _store_template(wsig, w, snaps, gnodes, look_inputs, nlinks, nframes):
    tt = w.copy()
    tt.name = f'.DR Outfit template {wsig[:12]}'
    tt[TEMPLATE_TAG] = wsig
    if tt.get(WRAPPER_TAG) is not None:
        del tt[WRAPPER_TAG]
    # The copy still points at this material's stage trees; the template gets its own.
    subs = []
    for i, (snode, st) in enumerate(_stage_trees_of(tt)):
        c = st.copy()
        c.name = f'.DR Stage template {wsig[:12]} {i}'
        snode.node_tree = c
        subs.append(c)
    if subs and len(tt.links) + sum(len(c.links) for c in subs) != nlinks:
        print(f'DR_BLOCKS_TEMPLATE_SKIP {tt.name} links', flush=True)
        bpy.data.node_groups.remove(tt)
        for c in subs:
            if c.users == 0:
                bpy.data.node_groups.remove(c)
        return
    rec = {
        'tree': tt.name,
        'snaps': {n: _norm_snap(d) for n, d in snaps.items()},
        'gparams': {gn: (g['tree'].name, dict(g['params'])) for gn, g in gnodes.items()},
        'look': [(key, ident, label) for key, ident, _v, label in look_inputs],
        'links': nlinks, 'frames': nframes,
    }
    _WRAP_TEMPLATES[wsig] = rec
    text = repr(rec)
    try:
        if ast.literal_eval(text) == rec:   # only records that survive a reload
            tt[TEMPLATE_REC] = text
            tt['dr.blocks.recipe'] = RECIPE
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        pass


# ---- stage trees: one group node per build stage inside the wrapper ----
# A flat wrapper holds about 650 nodes and its links cross more than 13,000 times, which no
# column order fixes. So the wrapper's nodes and blocks are split into a few acyclic stage
# clusters; each cluster becomes its own per-material tree and the wrapper shows one node per stage.
STAGE_TAG = 'dr.stage_tree'
STAGE_SMALL = 20        # clusters under this many units fold into a linked neighbour


def _stage_trees_of(w):
    """(group node, stage tree) pairs of a wrapper tree."""
    return [(nd, nd.node_tree) for nd in w.nodes
            if nd.bl_idname == 'ShaderNodeGroup' and nd.node_tree is not None and nd.node_tree.get(STAGE_TAG)]


def _remove_wrapper(w):
    """Remove a wrapper tree and the stage trees only it used."""
    subs = []
    for _nd, t in _stage_trees_of(w):
        if t not in subs:
            subs.append(t)
    bpy.data.node_groups.remove(w)
    for t in subs:
        if t.users == 0:
            bpy.data.node_groups.remove(t)


def _stage_clusters(inner, byn, gnodes, final, sel_parent, out_srcs):
    """Split the wrapper's nodes and blocks into stage clusters whose quotient graph is acyclic.
    Returns [{'label', 'stage', 'ents'}] in flow order, or None when nesting does not apply."""
    ents = list(inner) + list(gnodes)
    eset = set(ents)
    stage = {n: _stage(byn[n]) for n in inner}
    for gn, g in gnodes.items():
        stage[gn] = g['stage']
    # A selection frame cannot span trees: its members form one unit.
    unit = {}
    members = defaultdict(list)
    for n in ents:
        f = sel_parent.get(n)
        unit[n] = n if f is None else 'F:' + f.name
        members[unit[n]].append(n)
    ustage = {}
    for u, ms in members.items():
        count = defaultdict(int)
        for m in ms:
            count[stage[m]] += 1
        ustage[u] = min(count, key=lambda s: (-count[s], s))
    preds = {u: set() for u in members}
    succ = {u: set() for u in members}
    for a, _fi, b, _ti, _mu in final:
        if a in eset and b in eset and unit[a] != unit[b]:
            preds[unit[b]].add(unit[a])
            succ[unit[a]].add(unit[b])
    # A constant that feeds one other stage only belongs to that stage.
    for u, ms in members.items():
        n = ms[0]
        if (len(ms) != 1 or preds[u] or not succ[u] or n in gnodes or n in sel_parent or n in out_srcs
                or byn[n].bl_idname not in ('ShaderNodeValue', 'ShaderNodeRGB')):
            continue
        ss = {ustage[s] for s in succ[u]}
        if len(ss) == 1:
            ustage[u] = next(iter(ss))
    names = sorted(members)
    key = {u: ('ShaderNodeGroup' if members[u][0] in gnodes else byn[members[u][0]].bl_idname, u) for u in names}
    order = _topo(names, preds, key)
    if order is None:
        return None
    rank = {}
    for u in order:
        rank[u] = max((rank[p] + (1 if ustage[p] != ustage[u] else 0) for p in preds[u]), default=0)
    raw = defaultdict(list)
    for u in order:
        raw[(ustage[u], rank[u])].append(u)
    merged = _merge_blocks(raw, order, preds, None, None, cap=10 ** 9, small=STAGE_SMALL)
    cl = {u: k for k, v in merged.items() for u in v}
    keys = sorted(merged, key=lambda k: (k[1], k[0]))
    qpreds = {k: set() for k in keys}
    for u, ps in preds.items():
        for p in ps:
            if cl[p] != cl[u]:
                qpreds[cl[u]].add(cl[p])
    qorder = _topo(keys, qpreds, {k: (k[1], k[0]) for k in keys})
    if qorder is None or len(qorder) < 2:
        return None
    total = defaultdict(int)
    for k in qorder:
        total[k[0]] += 1
    seen = defaultdict(int)
    epos = {n: i for i, n in enumerate(ents)}
    out = []
    for k in qorder:
        seen[k[0]] += 1
        label = k[0] if total[k[0]] == 1 else f'{k[0]} {seen[k[0]]}'
        es = sorted((m for u in merged[k] for m in members[u]), key=epos.get)
        out.append({'label': label, 'stage': k[0], 'ents': es})
    return out


def _arrange(t, new, edges, sel_parent, fmap, lab, as_source):
    """Lay out the nodes ``new`` (name -> node) of tree ``t`` and frame them by label.
    ``as_source``: Group Input names placed like a parameter value, beside their first consumer.
    Returns the number of frames made."""
    lnames = sorted(new)
    bl = {n: new[n].bl_idname for n in lnames}
    for n in lnames:
        nd = new[n]
        b = bl[n]
        if b in HIDE_BL and not nd.hide:
            nd.hide = True
        if b in ('ShaderNodeValue', 'ShaderNodeRGB') and not nd.label:
            pk = nd.get('dr.parameter_key')
            nd.label = str(pk) if pk else (n[2:] if n.startswith('P:') else n)
        wd = _width_for(b)
        if abs(nd.width - wd) > 0.5:
            nd.width = wd
    wid = {n: _width_for(bl[n]) for n in lnames}
    hgt = {n: _height(bl[n], new[n].hide, _vis(new[n])) for n in lnames}
    lbl = {n: ('ShaderNodeValue' if n in as_source else b) for n, b in bl.items()}
    pos, items = _layout(lnames, lbl, wid, hgt, edges, label=lab, sources=True)
    nframes = 0
    for _c, label, members in items:
        members = [m for m in members if m not in sel_parent]
        if not members:
            continue
        fr = t.nodes.new('NodeFrame')
        fr.label = label or 'Misc'
        fr.label_size = 20
        fr['dr.blocks.frame'] = 1
        nframes += 1
        for m in members:
            new[m].parent = fr
    sel_abs = {}
    for n, p in sel_parent.items():
        if n in new and p.as_pointer() in fmap:
            new[n].parent = fmap[p.as_pointer()]
            sel_abs[n] = _abs(p)
    flat = []
    for nd in t.nodes:
        nm = nd.name
        if nd.bl_idname == 'NodeFrame':
            flat += [0.0, 0.0] if nd.get('dr.blocks.frame') else [float(nd.location.x), float(nd.location.y)]
            continue
        x, y = pos.get(nm, (float(nd.location.x), float(nd.location.y)))
        off = sel_abs.get(nm)
        if off is not None:
            x -= off[0]
            y -= off[1]
        flat += [x, y]
    t.nodes.foreach_set('location', flat)
    return nframes


def _look_interface(w, look_keys):
    """Create the wrapper's look inputs in panels. Returns [(key, identifier, value, label)]."""
    look_inputs = []
    labels = {k: _look_label(k) for k in look_keys}
    panels = {}
    used = set()
    for key in sorted(look_keys, key=lambda k: (_look_panel(k), labels[k][1], labels[k][2])):
        pi = _look_panel(key)
        if pi not in panels:
            panels[pi] = w.interface.new_panel(LOOK_PANELS[pi], default_closed=pi != 0)
        value = look_keys[key]
        label, k = labels[key][0], 2
        while label in used:
            label = f'{labels[key][0]} {k}'
            k += 1
        used.add(label)
        sock = w.interface.new_socket(
            name=label, in_out='INPUT', parent=panels[pi],
            socket_type='NodeSocketColor' if isinstance(value, tuple) else 'NodeSocketFloat')
        sock.default_value = value
        look_inputs.append((key, sock.identifier, value, label))
    return look_inputs


def _iter_build_nested(w, mat, clusters, *, byn, outs, gnodes, final, topset, look_nodes, look_keys,
                       plan, snaps_in, frames, sel_frames, sel_parent):
    """Build one stage tree per cluster and the wrapper that links them. Generator: yields
    'blocks' about every SLICE_S; returns (look_inputs, links, frames, wrapper nodes)."""
    tick = time.perf_counter()
    nc = len(clusters)
    cl_of = {}
    epos = {}
    for ci, c in enumerate(clusters):
        for i, e in enumerate(c['ents']):
            cl_of[e] = ci
            epos[e] = i
    c_links = [[] for _ in clusters]        # links inside a cluster
    c_in = [set() for _ in clusters]        # ('L', look key) or ('X', source, output index)
    c_in_links = [[] for _ in clusters]     # (input key, node, input index, muted)
    c_out = [{} for _ in clusters]          # (source, output index) -> farthest consumer cluster
    for a, fi, b, ti, mu in final:
        if b in topset:
            continue
        cb = cl_of[b]
        if a in look_nodes:
            k = ('L', look_nodes[a])
        else:
            ca = cl_of[a]
            if ca == cb:
                c_links[cb].append((a, fi, b, ti, mu))
                continue
            k = ('X', a, fi)
            c_out[ca][(a, fi)] = max(cb, c_out[ca].get((a, fi), -1))
        c_in[cb].add(k)
        c_in_links[cb].append((k, b, ti, mu))
    direct = []                             # look input straight to a wrapper output
    for (a, fi), j in plan['out_idx'].items():
        if a in look_nodes:
            direct.append((look_nodes[a], j))
        else:
            c_out[cl_of[a]][(a, fi)] = nc
    # Far consumers first: a long link leaves and arrives near the top, short ones nest below it.
    out_order = [sorted(d, key=lambda k, d=d: (-d[k], epos[k[0]], k[1])) for d in c_out]
    out_pos = [{k: j for j, k in enumerate(lst)} for lst in out_order]
    look_labels = {k: _look_label(k) for k in look_keys}
    look_rank = {k: i for i, k in enumerate(sorted(
        look_keys, key=lambda k: (_look_panel(k), look_labels[k][1], look_labels[k][2])))}

    def in_key(k):
        if k[0] == 'L':
            return (-1, look_rank[k[1]], 0)
        ca = cl_of[k[1]]
        return (ca, out_pos[ca][(k[1], k[2])], 0)

    in_order = [sorted(s, key=in_key) for s in c_in]
    in_pos = [{k: j for j, k in enumerate(lst)} for lst in in_order]

    def src_type(a, fi):
        stype = gnodes[a]['out_types'][fi] if a in gnodes else outs[a][fi].type
        return SOCKET_TYPES.get(stype, 'NodeSocketFloat')

    def src_name(a, fi):
        if a in gnodes:
            names = gnodes[a]['out_names']
            return names[fi] if fi < len(names) else gnodes[a]['stage']
        base = byn[a].label or a
        return base if len(outs[a]) == 1 else f'{base} · {outs[a][fi].name}'

    frame_cl = {}
    for n, p in sel_parent.items():
        if n in cl_of:
            frame_cl.setdefault(p.as_pointer(), cl_of[n])
    trees = []
    nlinks = 0
    nframes = 0
    try:
        for ci, c in enumerate(clusters):
            t = bpy.data.node_groups.new(f'DR Stage · {c["label"]} · {mat}', 'ShaderNodeTree')
            trees.append(t)
            # Same order as the flat build: plain nodes and links, then blocks, then the interface.
            new = {}
            fmap = {}
            for f in frames:
                if f.as_pointer() in sel_frames and frame_cl.get(f.as_pointer()) == ci:
                    d = dict(snaps_in[f.name])
                    d['loc'] = _abs(f)
                    fmap[f.as_pointer()] = _make_node(t, d, loc=True)
            for n in c['ents']:
                if n in gnodes:
                    continue
                new[n] = _make_node(t, snaps_in[n])
                if time.perf_counter() - tick > SLICE_S:
                    yield 'blocks'
                    tick = time.perf_counter()
            wouts = {n: list(nd.outputs) for n, nd in new.items()}
            wins = {n: list(nd.inputs) for n, nd in new.items()}
            edges = []
            later = []
            for x in c_links[ci]:
                a, fi, b, ti, mu = x
                if a in gnodes or b in gnodes:
                    later.append(x)
                    continue
                lk = t.links.new(wouts[a][fi], wins[b][ti])
                if mu:
                    lk.is_muted = True
                nlinks += 1
                edges.append((a, fi, b, ti))
                if time.perf_counter() - tick > SLICE_S:
                    yield 'blocks'
                    tick = time.perf_counter()
            for gname in c['ents']:
                g = gnodes.get(gname)
                if g is None:
                    continue
                nd = t.nodes.new('ShaderNodeGroup')
                nd.node_tree = g['tree']
                nd.label = g['stage']
                nd.name = gname
                nd['dr.stage'] = 'DR.' + g['stage']
                _set_group_params(nd, g)
                new[gname] = nd
                wouts[gname] = list(nd.outputs)
                wins[gname] = list(nd.inputs)
                if time.perf_counter() - tick > SLICE_S:
                    yield 'blocks'
                    tick = time.perf_counter()
            for a, fi, b, ti, mu in later:
                lk = t.links.new(wouts[a][fi], wins[b][ti])
                if mu:
                    lk.is_muted = True
                nlinks += 1
                edges.append((a, fi, b, ti))
                if time.perf_counter() - tick > SLICE_S:
                    yield 'blocks'
                    tick = time.perf_counter()
            for k in in_order[ci]:
                if k[0] == 'L':
                    value = look_keys[k[1]]
                    t.interface.new_socket(
                        name=look_labels[k[1]][0], in_out='INPUT',
                        socket_type='NodeSocketColor' if isinstance(value, tuple) else 'NodeSocketFloat')
                else:
                    t.interface.new_socket(name=src_name(k[1], k[2]), in_out='INPUT',
                                           socket_type=src_type(k[1], k[2]))
            for a, fi in out_order[ci]:
                t.interface.new_socket(name=src_name(a, fi), in_out='OUTPUT', socket_type=src_type(a, fi))
            yield 'blocks'
            tick = time.perf_counter()
            # One Group Input per source (look inputs, then each upstream stage), so every bundle
            # of links starts from one place.
            lab = {n: _frame_label(_stage(nd), n) for n, nd in new.items()}
            by_src = defaultdict(list)
            for k in in_order[ci]:
                by_src[-1 if k[0] == 'L' else cl_of[k[1]]].append(k)
            gi_sock = {}
            gi_name = {}
            for src in sorted(by_src):
                title = 'Look inputs' if src < 0 else f'From {clusters[src]["label"]}'
                gi = t.nodes.new('NodeGroupInput')
                gi.name = f'In:{title}'
                gi.label = title
                keep = {in_pos[ci][k] for k in by_src[src]}
                for j, o in enumerate(gi.outputs):
                    if j not in keep:
                        o.hide = True
                for k in by_src[src]:
                    gi_sock[k] = gi.outputs[in_pos[ci][k]]
                    gi_name[k] = gi.name
                new[gi.name] = gi
                lab[gi.name] = title
            for k, b, ti, mu in c_in_links[ci]:
                lk = t.links.new(gi_sock[k], wins[b][ti])
                if mu:
                    lk.is_muted = True
                nlinks += 1
                edges.append((gi_name[k], in_pos[ci][k], b, ti))
                if time.perf_counter() - tick > SLICE_S:
                    yield 'blocks'
                    tick = time.perf_counter()
            if out_order[ci]:
                gout = t.nodes.new('NodeGroupOutput')
                gout.name = 'Stage Out'
                new[gout.name] = gout
                lab[gout.name] = 'Outputs'
                for j, (a, fi) in enumerate(out_order[ci]):
                    t.links.new(wouts[a][fi], gout.inputs[j])
                    nlinks += 1
                    edges.append((a, fi, gout.name, j))
            nframes += _arrange(t, new, edges, sel_parent, fmap, lab, ())
            t[STAGE_TAG] = 1
            t['dr.blocks.recipe'] = RECIPE
            yield 'blocks'
            tick = time.perf_counter()

        # ---- the wrapper: one node per stage ----
        new = {}
        fmap = {}
        for f in frames:
            if f.as_pointer() in sel_frames and f.as_pointer() not in frame_cl:
                d = dict(snaps_in[f.name])
                d['loc'] = _abs(f)
                fmap[f.as_pointer()] = _make_node(w, d, loc=True)
        snode = []
        lab = {}
        for ci, c in enumerate(clusters):
            nd = w.nodes.new('ShaderNodeGroup')
            nd.node_tree = trees[ci]
            nd.name = f'DR.Stage.{c["label"]}'
            nd.label = c['label']
            nd['dr.stage'] = 'DR.' + c['stage']
            snode.append(nd)
            new[nd.name] = nd
            lab[nd.name] = c['label']
        edges = []
        for ci in range(nc):
            for k in in_order[ci]:
                if k[0] != 'X':
                    continue
                ca = cl_of[k[1]]
                j = out_pos[ca][(k[1], k[2])]
                w.links.new(snode[ca].outputs[j], snode[ci].inputs[in_pos[ci][k]])
                nlinks += 1
                edges.append((snode[ca].name, j, snode[ci].name, in_pos[ci][k]))
        yield 'blocks'
        look_inputs = _look_interface(w, look_keys)
        ident = {key: idn for key, idn, _v, _l in look_inputs}
        gins = set()

        def look_input(title, keys, label):
            gi = w.nodes.new('NodeGroupInput')
            gi.name = f'In:{title}'
            gi.label = 'Look inputs'
            want = {ident[k] for k in keys}
            socks = {}
            for o in gi.outputs:
                if o.identifier in want:
                    socks[o.identifier] = o
                else:
                    o.hide = True
            new[gi.name] = gi
            lab[gi.name] = label
            gins.add(gi.name)
            return gi, socks

        for ci, c in enumerate(clusters):
            keys = [k[1] for k in in_order[ci] if k[0] == 'L']
            if not keys:
                continue
            gi, socks = look_input(c['label'], keys, c['label'])
            for row, key in enumerate(keys):
                w.links.new(socks[ident[key]], snode[ci].inputs[in_pos[ci][('L', key)]])
                nlinks += 1
                edges.append((gi.name, row, snode[ci].name, in_pos[ci][('L', key)]))
        for name, sock_type in plan['out_plan']:
            w.interface.new_socket(name=name, in_out='OUTPUT', socket_type=sock_type)
        gout = w.nodes.new('NodeGroupOutput')
        gout.name = 'DR Outfit Out'
        new[gout.name] = gout
        lab[gout.name] = 'Outputs'
        if direct:
            gi, socks = look_input('Outputs', [key for key, _j in direct], 'Outputs')
            for row, (key, j) in enumerate(direct):
                w.links.new(socks[ident[key]], gout.inputs[j])
                nlinks += 1
                edges.append((gi.name, row, gout.name, j))
        for (a, fi), j in plan['out_idx'].items():
            if a in look_nodes:
                continue
            ca = cl_of[a]
            w.links.new(snode[ca].outputs[out_pos[ca][(a, fi)]], gout.inputs[j])
            nlinks += 1
            edges.append((snode[ca].name, out_pos[ca][(a, fi)], gout.name, j))
        yield 'blocks'
        nframes += _arrange(w, new, edges, {}, fmap, lab, gins)
    except BaseException:
        for t in trees:
            if t.users == 0:
                bpy.data.node_groups.remove(t)
        raise
    return look_inputs, nlinks, nframes, len(new)


def collapse_and_arrange(tree, *, material_name=''):
    """Sync form: drain iter_collapse_and_arrange and return its result."""
    gen = iter_collapse_and_arrange(tree, material_name=material_name)
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


def iter_collapse_and_arrange(tree, *, material_name=''):
    """Generator form: yields 'blocks' about every SLICE_S so a staged import can redraw."""
    t0 = time.perf_counter()
    tick = t0
    if os.environ.get('DR_GRAPH_BLOCKS') == '0':
        return {'skipped': 'env'}
    mat = material_name or _material_name(tree)
    # A rebuild clears the nodes but keeps the tree's custom properties, so the recipe alone
    # does not prove the tree is still collapsed.
    if tree.get('dr.blocks.recipe') and any(
            n.bl_idname == 'ShaderNodeGroup' and n.node_tree is not None
            and (n.node_tree.get(WRAPPER_TAG) or n.node_tree.get('dr.block.signature')) for n in tree.nodes):
        print(f'DR_BLOCKS_SKIP mat={mat} recipe={tree.get("dr.blocks.recipe")}', flush=True)
        return {'skipped': 'recipe'}
    only = {s for s in (os.environ.get('DR_GRAPH_BLOCKS_ONLY') or '').split(',') if s}
    tm = {}

    # ---- snapshot (read only) ----
    all_nodes = list(tree.nodes)
    frames = [n for n in all_nodes if n.bl_idname == 'NodeFrame']
    sel_frames = {f.as_pointer() for f in frames if _is_sel_frame(f)}
    reroutes = [n for n in all_nodes if n.bl_idname == 'NodeReroute']
    rr = {n.name for n in reroutes}
    byn = {n.name: n for n in all_nodes if n.bl_idname not in ('NodeFrame', 'NodeReroute')}
    names = sorted(byn)
    nodes_a = len(byn) + len(reroutes)
    outs = {n.name: list(n.outputs) for n in all_nodes if n.bl_idname != 'NodeFrame'}
    ins = {n.name: list(n.inputs) for n in all_nodes if n.bl_idname != 'NodeFrame'}
    op, ip = {}, {}
    for nm, socks in outs.items():
        for i, s in enumerate(socks):
            op[s.as_pointer()] = (nm, i)
    for nm, socks in ins.items():
        for i, s in enumerate(socks):
            ip[s.as_pointer()] = (nm, i)
    raw = []
    for lk in tree.links:
        a = op.get(lk.from_socket.as_pointer())
        b = ip.get(lk.to_socket.as_pointer())
        if a and b:
            raw.append((a[0], a[1], b[0], b[1], bool(lk.is_muted)))
    links_a = len(raw)
    feed = {b: (a, fi, mu) for a, fi, b, ti, mu in raw if b in rr}
    links = []
    for a, fi, b, ti, mu in raw:
        if b in rr:
            continue
        guard = 0
        while a in rr and guard < 10000:
            f = feed.get(a)
            if f is None:
                a = None
                break
            a, fi, mu2 = f
            mu = mu or mu2
            guard += 1
        if a is not None and a in byn:
            links.append((a, fi, b, ti, mu))
    linked = {(b, ti) for _a, _fi, b, ti, _m in links}
    tm['snap'] = time.perf_counter() - t0
    bound = _sel_binding_names(tree)
    pinned = {n: _is_pinned(byn[n], sel_frames) or bool(_identifiers(byn[n]) & bound) for n in names}
    sel_parent = {n: byn[n].parent for n in names
                  if byn[n].parent is not None and byn[n].parent.as_pointer() in sel_frames}
    preds = {n: set() for n in names}
    for a, _fi, b, _ti, _m in links:
        if a != b:
            preds[b].add(a)
    stage_of = {n: _stage(byn[n]) for n in names}
    # Order key: node type, then name. Values, images and tags are left out, so two materials
    # with the same structure get the same member and socket order, which is what lets them
    # share blocks and the wrapper template.
    order = _topo(names, preds, {n: (byn[n].bl_idname, n) for n in names})
    tm['pin'] = time.perf_counter() - t0
    yield 'blocks'
    cycles, small_left = 0, 0
    keys, block_of, blocks = [], {}, {}
    if order is None:
        print('DR_BLOCKS_CYCLE', mat, 'top-level cycle; no collapse')
        cycles = 1
    else:
        rank = {}
        for n in order:
            if pinned[n]:
                rank[n] = max((rank[p] + 1 for p in preds[n]), default=0)
            else:
                # A pinned node already sits one rank above its own inputs, so a
                # pinned source (P: value, texture) does not split the block it feeds.
                rank[n] = max((rank[p] + (1 if (not pinned[p] and stage_of.get(p) != stage_of[n]) else 0) for p in preds[n]), default=0)
        raw_blocks = defaultdict(list)
        for n in order:
            if not pinned[n]:
                raw_blocks[(stage_of[n], rank[n])].append(n)
        blocks = _merge_blocks(raw_blocks, order, preds, stage_of, pinned)
        keys = [k for k in blocks if len(blocks[k]) >= 3 and (not only or k[0] in only)]
        small_left = sum(len(v) for k, v in blocks.items() if len(v) < 3)
        block_of = {n: k for k in keys for n in blocks[k]}
        bpreds = {k: set() for k in keys}
        for a, _fi, b, _ti, _m in links:
            if a in block_of and b in block_of and block_of[a] != block_of[b]:
                bpreds[block_of[b]].add(block_of[a])
        korder = _topo([str(k) for k in keys], {str(k): {str(p) for p in bpreds[k]} for k in keys},
                       {str(k): (k[1], k[0]) for k in keys}) if keys else []
        if korder is None:
            print('DR_BLOCKS_CYCLE', mat, 'block graph cycle; no collapse')
            cycles = 1
            keys, block_of = [], {}
        else:
            kmap = {str(k): k for k in keys}
            keys = [kmap[s] for s in korder]

    # ---- parameters that only feed blocks move onto the block's inputs ----
    # The value then lives on the unlinked group-node input (param_bind finds it through the
    # node's dr.param_inputs registry), so the P: node and its links leave the top level.
    # A parameter with any top-level consumer, a selection binding or a Pro-toggle name stays a node.
    movable = {}        # param node name -> (key, value)
    look_nodes = {}     # param node name -> key (becomes a wrapper input)
    look_keys = {}      # key -> value
    uses = defaultdict(list)
    for a, _fi, b, _ti, mu in links:
        uses[a].append((b, mu))
    free = {}
    for n in names:
        nd = byn[n]
        if nd.bl_idname not in ('ShaderNodeValue', 'ShaderNodeRGB') or not _is_param(nd):
            continue
        pkey = str(nd.get('dr.parameter_key') or n[2:])
        # A look parameter is found by its key (shader_control_ops Pro toggles), so it may move.
        if n in sel_parent or (_identifiers(nd) & bound) or (_is_pro_target(nd) and _look_panel(pkey) is None):
            continue
        if any(str(k).startswith(('dr.general.', 'dr.selection.')) for k in nd.keys()):
            continue
        if not uses[n] or any(mu for _b, mu in uses[n]):
            continue
        dv = nd.outputs[0].default_value
        free[n] = (str(nd.get('dr.parameter_key') or n[2:]),
                   tuple(dv) if nd.bl_idname == 'ShaderNodeRGB' else float(dv))
    if os.environ.get('DR_GRAPH_LOOK') != '0':
        by_key = defaultdict(list)
        for n, (key, _v) in free.items():
            if _look_panel(key) is not None:
                by_key[key].append(n)
        for key, group in by_key.items():
            vals = {free[n][1] for n in group}
            if len(vals) != 1:      # same key, different values: not one control
                continue
            look_keys[key] = free[group[0]][1]
            for n in group:
                look_nodes[n] = key
    if block_of and os.environ.get('DR_GRAPH_BLOCKS_PARAMS') != '0':
        for n, kv in free.items():
            if n not in look_nodes and all(b in block_of for b, _mu in uses[n]):
                movable[n] = kv

    # ---- plan blocks (bpy reads and new group datablocks only) ----
    out_links = defaultdict(list)
    in_links = defaultdict(list)
    for a, fi, b, ti, mu in links:
        out_links[(a, fi)].append((b, ti))
        in_links[b].append((ti, a, fi))
    redirect = {}       # (member, oi) -> (group node name, j)
    gnodes = {}         # group node name -> dict
    collapsed_ids = set()
    reused_n = created_n = 0
    pos_in = {n: i for i, n in enumerate(order or names)}
    for k in keys:
        stage, rk = k
        members = sorted(blocks[k], key=pos_in.get)
        mset = set(members)
        loc = {n: i for i, n in enumerate(members)}
        gname = f'DR.Block.{stage}.{rk}'
        ext_key, ext_src = {}, []
        internal, links_in = [], []
        ext_out = []
        ser_nodes = []
        for n in members:
            nd = byn[n]
            by_in = defaultdict(list)
            for ti, a, fi in in_links[n]:
                by_in[ti].append((a, fi))
            ins_ser = []
            for si, s in enumerate(ins[n]):
                if si in by_in:
                    for a, fi in by_in[si]:
                        if a in mset:
                            internal.append((loc[n], s.identifier, loc[a], outs[a][fi].identifier))
                        else:
                            src = redirect.get((a, fi), (a, fi))
                            if src not in ext_key:
                                ext_key[src] = len(ext_src)
                                ext_src.append(src)
                            links_in.append((ext_key[src], loc[n], s.identifier))
                else:
                    dv = _ser(s.default_value) if hasattr(s, 'default_value') else None
                    ins_ser.append((s.identifier, s.type, bool(s.hide), dv))
            outs_ser = [(oi, o.type, bool(o.hide)) for oi, o in enumerate(outs[n])]
            tree_name = nd.node_tree.name if nd.bl_idname == 'ShaderNodeGroup' and nd.node_tree else ''
            ser_nodes.append([nd.bl_idname, _ser_props(nd), _ser_ids(nd), tree_name, ins_ser, outs_ser])
            for oi, o in enumerate(outs[n]):
                if any(b not in mset for b, _ti in out_links.get((n, oi), ())):
                    ext_out.append((loc[n], oi))
        internal.sort()
        links_in.sort()
        ins_plan = []
        params_in = {}
        for ext_i, (a, fi) in enumerate(ext_src):
            if a in movable:
                params_in[ext_i] = movable[a][0]
                ins_plan.append((movable[a][0], SOCKET_TYPES.get(outs[a][fi].type, 'NodeSocketFloat')))
                continue
            if a in gnodes:
                g = gnodes[a]
                srcname = g['stage']
                many = len(g['outs']) > 1
                sname = g['out_names'][fi] if fi < len(g['out_names']) else ''
                stype = g['out_types'][fi]
            else:
                nd = byn[a]
                srcname = nd.label or a
                many = len(outs[a]) > 1
                sname = outs[a][fi].name
                stype = outs[a][fi].type
            ins_plan.append((f'{srcname} · {sname}' if many else srcname, SOCKET_TYPES.get(stype, 'NodeSocketFloat')))
        outs_plan = []
        for ln, oi in ext_out:
            n = members[ln]
            nl = byn[n].label or n
            o = outs[n][oi]
            outs_plan.append((f'{nl} · {o.name}' if len(outs[n]) > 1 else nl, SOCKET_TYPES.get(o.type, 'NodeSocketFloat')))
        sig_src = {
            'v': 3, 'nodes': ser_nodes, 'internal': internal, 'links_in': links_in,
            'ext_out': ext_out,
            # Parameter inputs carry the key as the socket name, so it is part of the identity.
            'params': sorted(params_in.items()),
            'ins': [t for _l, t in ins_plan], 'outs': [t for _l, t in outs_plan],
        }
        sig = hashlib.sha256(json.dumps(sig_src, sort_keys=True, default=str).encode('utf-8')).hexdigest()
        base = f'DR · {stage} · {sig[:10]}'
        gt = bpy.data.node_groups.get(base)
        reused = gt is not None and gt.get('dr.block.signature') == sig
        if not reused:
            alt = bpy.data.node_groups.get(f'{base} {sig[10:14]}')
            if alt is not None and alt.get('dr.block.signature') == sig:
                gt, reused = alt, True
        if reused:
            reused_n += 1
        else:
            name = base if gt is None else f'{base} {sig[10:14]}'
            plan = {'ins': ins_plan, 'outs': outs_plan, 'internal': internal,
                    'links_in': links_in, 'ext_out': ext_out}
            gt = _build_group(stage, sig, members, byn, plan, linked)
            gt.name = name
            created_n += 1
        for j, (ln, oi) in enumerate(ext_out):
            redirect[(members[ln], oi)] = (gname, j)
        gnodes[gname] = {'stage': stage, 'tree': gt, 'ext_src': ext_src, 'ext_key': ext_key,
                         'outs': ext_out, 'out_names': _iface_out_names(gt),
                         'out_types': [outs[members[ln]][oi].type for ln, oi in ext_out],
                         'members': members,
                         'params': {i: movable[ext_src[i][0]] for i in params_in}}
        collapsed_ids |= {str(byn[n].get('dr.legacy_name', '') or n) for n in members}
        if time.perf_counter() - tick > SLICE_S:
            yield 'blocks'
            tick = time.perf_counter()
    tm['plan'] = time.perf_counter() - t0

    # final link list over surviving nodes + group nodes
    final, seen_in = [], set()
    for a, fi, b, ti, mu in links:
        ka, kb = block_of.get(a), block_of.get(b)
        if (ka is not None and ka == kb) or a in movable:
            continue
        src = redirect.get((a, fi), (a, fi))
        if kb is not None:
            g = gnodes[f'DR.Block.{kb[0]}.{kb[1]}']
            dst = (f'DR.Block.{kb[0]}.{kb[1]}', g['ext_key'][src])
        else:
            dst = (b, ti)
        if dst in seen_in:
            continue
        seen_in.add(dst)
        final.append((src[0], src[1], dst[0], dst[1], mu))

    # ---- wrapper plan: what stays in the material tree ----
    gone = set(block_of) | set(movable) | set(look_nodes)
    plan = _wrap_plan(names, byn, outs, ins, final, gnodes, gone, look_nodes)
    if plan is None:
        print(f'DR_BLOCKS_NOWRAP mat={mat} blocks={len(gnodes)}', flush=True)
        return {'skipped': 'nowrap'}
    topset = plan['topset']
    inner = [n for n in names if n not in gone and n not in topset]
    tm['wrapplan'] = time.perf_counter() - t0
    yield 'blocks'
    tick = time.perf_counter()

    # ---- build the wrapper tree. The material tree is not touched until the swap, so the
    # material keeps rendering while this runs in slices and an error leaves it as built. ----
    wname = f'{WRAPPER_NODE} · {mat}'
    old = bpy.data.node_groups.get(wname)
    if old is not None and old.get(WRAPPER_TAG) and old.users == 0:
        _remove_wrapper(old)
    snaps_in = {}
    for f in frames:
        if f.as_pointer() in sel_frames:
            snaps_in[f.name] = _snap_node(f)
    for n in inner:
        snaps_in[n] = _snap_node(byn[n], linked)
    clusters = None
    if os.environ.get('DR_GRAPH_STAGES', '1') != '0':
        clusters = _stage_clusters(inner, byn, gnodes, final, sel_parent,
                                   {a for a, _fi in plan['out_idx']})
        if clusters is None:
            print(f'DR_BLOCKS_FLAT mat={mat} (no acyclic stage split)', flush=True)
    wsig = _wrap_sig(inner, byn, gnodes, final, look_keys, plan, sel_parent, nested=clusters is not None)
    collapsed = json.dumps(sorted(collapsed_ids))
    tm['sig'] = time.perf_counter() - t0
    yield 'blocks'
    tick = time.perf_counter()
    writes = -1
    hit = yield from _from_template(wsig, wname, snaps_in, gnodes, look_keys)
    if hit is not None:
        w, look_inputs, nlinks, nframes, writes = hit
        nodes_w = sum(1 for nd in w.nodes if nd.bl_idname != 'NodeFrame')
        tm['copy'] = time.perf_counter() - t0
        yield 'blocks'
    else:
        w = bpy.data.node_groups.new(wname, 'ShaderNodeTree')
    try:
        if hit is None and clusters is not None:
            look_inputs, nlinks, nframes, nodes_w = yield from _iter_build_nested(
                w, mat, clusters, byn=byn, outs=outs, gnodes=gnodes, final=final, topset=topset,
                look_nodes=look_nodes, look_keys=look_keys, plan=plan, snaps_in=snaps_in,
                frames=frames, sel_frames=sel_frames, sel_parent=sel_parent)
            tm['nested'] = time.perf_counter() - t0
            w[WRAPPER_TAG] = 1
            w['dr.blocks.recipe'] = RECIPE
            _store_template(wsig, w, snaps_in, gnodes, look_inputs, nlinks, nframes)
        elif hit is None:
            # Order matters for speed: every node or link added costs a tree update whose price
            # grows with group nodes, interface sockets and Group Input nodes already in the tree.
            # So: plain nodes and their links first, then blocks, then the interface.
            new = {}
            fmap = {}
            for f in frames:
                if f.as_pointer() in sel_frames:
                    d = dict(snaps_in[f.name])
                    d['loc'] = _abs(f)
                    fmap[f.as_pointer()] = _make_node(w, d, loc=True)
            for n in inner:
                new[n] = _make_node(w, snaps_in[n])
                if time.perf_counter() - tick > SLICE_S:
                    yield 'blocks'
                    tick = time.perf_counter()
            tm['build'] = time.perf_counter() - t0
            wouts = {n: list(nd.outputs) for n, nd in new.items()}
            wins = {n: list(nd.inputs) for n, nd in new.items()}
            edges = []
            nlinks = 0
            later = []
            for x in final:
                a, fi, b, ti, mu = x
                if b in topset:
                    continue
                if a in look_nodes or a in gnodes or b in gnodes:
                    later.append(x)
                    continue
                lk = w.links.new(wouts[a][fi], wins[b][ti])
                if mu:
                    lk.is_muted = True
                nlinks += 1
                edges.append((a, fi, b, ti))
                if time.perf_counter() - tick > SLICE_S:
                    yield 'blocks'
                    tick = time.perf_counter()
            tm['links1'] = time.perf_counter() - t0
            for gname, g in gnodes.items():
                nd = w.nodes.new('ShaderNodeGroup')
                nd.node_tree = g['tree']
                nd.label = g['stage']
                nd.name = gname
                nd['dr.stage'] = 'DR.' + g['stage']
                _set_group_params(nd, g)
                new[gname] = nd
                wouts[gname] = list(nd.outputs)
                wins[gname] = list(nd.inputs)
                if time.perf_counter() - tick > SLICE_S:
                    yield 'blocks'
                    tick = time.perf_counter()
            last = []
            for x in later:
                a, fi, b, ti, mu = x
                if a in look_nodes:
                    last.append(x)
                    continue
                lk = w.links.new(wouts[a][fi], wins[b][ti])
                if mu:
                    lk.is_muted = True
                nlinks += 1
                edges.append((a, fi, b, ti))
                if time.perf_counter() - tick > SLICE_S:
                    yield 'blocks'
                    tick = time.perf_counter()
            tm['links2'] = time.perf_counter() - t0
            # look inputs, grouped in panels
            look_inputs = []        # (key, identifier, value, label)
            labels = {k: _look_label(k) for k in look_keys}
            panels = {}
            used = set()
            for key in sorted(look_keys, key=lambda k: (_look_panel(k), labels[k][1], labels[k][2])):
                pi = _look_panel(key)
                if pi not in panels:
                    panels[pi] = w.interface.new_panel(LOOK_PANELS[pi], default_closed=pi != 0)
                value = look_keys[key]
                label, k = labels[key][0], 2
                while label in used:
                    label = f'{labels[key][0]} {k}'
                    k += 1
                used.add(label)
                sock = w.interface.new_socket(
                    name=label, in_out='INPUT', parent=panels[pi],
                    socket_type='NodeSocketColor' if isinstance(value, tuple) else 'NodeSocketFloat')
                sock.default_value = value
                look_inputs.append((key, sock.identifier, value, label))
            gin_sock = {}
            gin_name = {}
            for key, ident, _value, label in look_inputs:
                gi = w.nodes.new('NodeGroupInput')
                gi.name = f'In:{key}'
                gi.label = label
                for o in gi.outputs:
                    if o.identifier == ident:
                        gin_sock[key] = o
                    else:
                        o.hide = True
                gi.hide = True
                new[gi.name] = gi
                gin_name[key] = gi.name
            yield 'blocks'
            tick = time.perf_counter()
            for a, fi, b, ti, mu in last:
                lk = w.links.new(gin_sock[look_nodes[a]], wins[b][ti])
                if mu:
                    lk.is_muted = True
                nlinks += 1
                edges.append((gin_name[look_nodes[a]], 0, b, ti))
                if time.perf_counter() - tick > SLICE_S:
                    yield 'blocks'
                    tick = time.perf_counter()
            for name, sock_type in plan['out_plan']:
                w.interface.new_socket(name=name, in_out='OUTPUT', socket_type=sock_type)
            gout = w.nodes.new('NodeGroupOutput')
            gout.name = 'DR Outfit Out'
            new[gout.name] = gout
            for (a, fi), j in plan['out_idx'].items():
                src = gin_sock[look_nodes[a]] if a in look_nodes else wouts[a][fi]
                w.links.new(src, gout.inputs[j])
                nlinks += 1
                edges.append((gin_name[look_nodes[a]], 0, gout.name, j) if a in look_nodes else (a, fi, gout.name, j))
            tm['links'] = time.perf_counter() - t0
            yield 'blocks'

            # ---- layout inside the wrapper ----
            lnames = sorted(new)
            bl = {n: new[n].bl_idname for n in lnames}
            for n in lnames:
                nd = new[n]
                b = bl[n]
                if b in HIDE_BL and not nd.hide:
                    nd.hide = True
                if b in ('ShaderNodeValue', 'ShaderNodeRGB') and not nd.label:
                    pk = nd.get('dr.parameter_key')
                    nd.label = str(pk) if pk else (n[2:] if n.startswith('P:') else n)
                wd = _width_for(b)
                if abs(nd.width - wd) > 0.5:
                    nd.width = wd
            wid = {n: _width_for(bl[n]) for n in lnames}
            hgt = {n: _height(bl[n], new[n].hide, _vis(new[n])) for n in lnames}
            lab = {n: ('Look inputs' if bl[n] == 'NodeGroupInput' else _frame_label(_stage(new[n]), n)) for n in lnames}
            # A look input is laid out like a parameter value: beside its first consumer.
            lbl = {n: ('ShaderNodeValue' if b == 'NodeGroupInput' else b) for n, b in bl.items()}
            pos, items = _layout(lnames, lbl, wid, hgt, edges, label=lab, sources=True)
            nframes = 0
            for _c, label, members in items:
                members = [m for m in members if m not in sel_parent]
                if not members:
                    continue
                fr = w.nodes.new('NodeFrame')
                fr.label = label or 'Misc'
                fr.label_size = 20
                fr['dr.blocks.frame'] = 1
                nframes += 1
                for m in members:
                    new[m].parent = fr
            sel_abs = {}
            for n, p in sel_parent.items():
                if n in new and p.as_pointer() in fmap:
                    new[n].parent = fmap[p.as_pointer()]
                    sel_abs[n] = _abs(p)
            flat = []
            for nd in w.nodes:
                nm = nd.name
                if nd.bl_idname == 'NodeFrame':
                    flat += [0.0, 0.0] if nd.get('dr.blocks.frame') else [float(nd.location.x), float(nd.location.y)]
                    continue
                x, y = pos.get(nm, (float(nd.location.x), float(nd.location.y)))
                off = sel_abs.get(nm)
                if off is not None:
                    x -= off[0]
                    y -= off[1]
                flat += [x, y]
            w.nodes.foreach_set('location', flat)
            nodes_w = len(new)
            tm['layout'] = time.perf_counter() - t0
            w[WRAPPER_TAG] = 1
            w['dr.blocks.recipe'] = RECIPE
            _store_template(wsig, w, snaps_in, gnodes, look_inputs, nlinks, nframes)
        w['dr.blocks.collapsed'] = collapsed
        w['dr.wrapper.sig'] = wsig[:12]     # census + shipped-template lookup; ID props are free writes
        yield 'blocks'

        # ---- swap: the material tree becomes shader nodes + the wrapper node ----
        snaps = [_snap_node(byn[n], linked) for n in sorted(topset)]
    except BaseException:
        _remove_wrapper(w)
        raise
    _swap_top(tree, w, snaps, plan, look_inputs)
    tree['dr.blocks.collapsed'] = collapsed
    tree['dr.blocks.recipe'] = RECIPE
    tm['swap'] = time.perf_counter() - t0
    nodes_b = len(tree.nodes)
    secs = time.perf_counter() - t0
    phases = ' '.join(f'{k}={v:.2f}' for k, v in tm.items())
    print(f'DR_BLOCKS mat={mat} nodes {nodes_a}->{nodes_b} wrapper={nodes_w} links {links_a}->{nlinks} '
          f'blocks={created_n + reused_n} reused={reused_n} created={created_n} cycles={cycles} '
          f'frames={nframes} small_left={small_left} params_in={len(movable)} look={len(look_inputs)} '
          f'stages={len(_stage_trees_of(w))} template_writes={writes} s={secs:.2f} [{phases}]', flush=True)
    return {'nodes_before': nodes_a, 'nodes_after': nodes_b, 'nodes_wrapper': nodes_w,
            'links_before': links_a, 'links_after': nlinks,
            'blocks': created_n + reused_n, 'reused': reused_n, 'created': created_n, 'cycles': cycles,
            'frames': nframes, 'small_left': small_left, 'params_in': len(movable),
            'look': len(look_inputs), 'template': writes >= 0, 's': secs}
