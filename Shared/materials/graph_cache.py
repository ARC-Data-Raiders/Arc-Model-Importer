"""Node-group graph cache keyed by the DR graph RECIPE.

A user cache .blend holds prebuilt node groups stamped with ``dr.group.recipe``.
On a hit the groups are appended instead of rebuilt.  ``save_groups`` writes
the current recipe's groups; precompute can ship the same file as
``assets/precompute/graphs.blend`` (checked first, read-only).
"""
import os

import bpy

_ADDON_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHIPPED = os.path.join(_ADDON_DIR, "assets", "precompute", "graphs.blend")
_LOADED = set()


ENTRY_V = 3     # 3: setup nodes live in a per-material DR Outfit wrapper tree (2.3.4)


def recipe():
    from . import dr_primary_operations as ops
    return ops.RECIPE


def user_cache_path(rec=None):
    base = bpy.utils.user_resource("CONFIG", path="dataraiders_graph_cache", create=True)
    return os.path.join(base, "graphs_%s.blend" % (rec or recipe()))


def _stamped(rec):
    return [g for g in bpy.data.node_groups if g.get("dr.group.recipe") == rec]


def load_groups(rec=None):
    """Append cached groups for ``rec``. Returns names appended (empty on miss)."""
    rec = rec or recipe()
    if rec in _LOADED:
        return []
    try:
        from .graph_blocks import RECIPE as _blocks_rec, TEMPLATE_TAG as _ttag
    except Exception:
        _blocks_rec, _ttag = None, None
    out = []
    # Shipped templates first, then the user cache: both are read (the shipped file
    # used to end the loop, so a user cache was never loaded once graphs.blend existed).
    for path in (SHIPPED, user_cache_path(rec)):
        if not os.path.isfile(path) or (path == SHIPPED and os.environ.get("DR_SHIPPED_GRAPHS") == "0"):
            continue
        have = {g.name for g in bpy.data.node_groups}
        g0 = set(bpy.data.node_groups)
        try:
            with bpy.data.libraries.load(path, link=False) as (src, dst):
                dst.node_groups = [n for n in src.node_groups if n not in have]
        except Exception as exc:
            print("DataRaiders graph_cache: load failed", path, exc)
            continue
        got = [g for g in bpy.data.node_groups if g not in g0]     # requested + nested
        bad = {g for g in got if g.get("dr.group.recipe") not in (None, rec)}
        tmpls = [g for g in got if _ttag and g.get(_ttag) is not None]
        for g in tmpls:
            if g.get("dr.blocks.recipe") != _blocks_rec:
                bad.add(g)       # wrapper template from another layout recipe
        # A template whose nested group is dropped would keep an empty group node.
        changed = True
        while changed:
            changed = False
            for g in got:
                if g not in bad and any(getattr(n, "node_tree", None) in bad for n in g.nodes):
                    bad.add(g)
                    changed = True
        for g in tmpls:
            if g not in bad:
                g.use_fake_user = False
        # Shipped copies of per-file shared groups (DR.UserControls) yield to the file's own.
        for g in got:
            m = _DUP.match(g.name) if g.get("dr.ship.per_file") and g not in bad else None
            base = bpy.data.node_groups.get(m.group(1)) if m else None
            if base is not None:
                g.user_remap(base)
                bad.add(g)
        out += [g.name for g in got if g not in bad]
        if bad:
            bpy.data.batch_remove(list(bad))
        _LOADED.add(rec)
    return out


def save_groups(rec=None, path=None):
    """Write stamped groups for ``rec`` to the user cache (or ``path``)."""
    rec = rec or recipe()
    groups = set(_stamped(rec))
    if not groups:
        return None
    # Include nested groups so the file is self-contained.
    stack = list(groups)
    while stack:
        g = stack.pop()
        for n in g.nodes:
            sub = getattr(n, "node_tree", None)
            if sub is not None and sub not in groups:
                groups.add(sub)
                stack.append(sub)
    path = path or user_cache_path(rec)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.data.libraries.write(path, groups, fake_user=True, compress=False)
    _LOADED.add(rec)
    return path


# ---------------------------------------------------------------------------
# Per-user material graph cache (not shipped).  Key: material asset path +
# RECIPE + game build + colourway/preset key + Pro toggle.  Cold builds are
# queued and written at import end; warm imports append the cached material.
# DR_GRAPH_CACHE=0 disables.  Any error falls back to the live build.
# ---------------------------------------------------------------------------
import hashlib
import json
import re
import time

CAP_BYTES = 2 * 1024 ** 3
_QUEUE = []
_INDEX = {}
_LOGGED = set()
STATS = {"hits": 0, "misses": 0, "errors": 0, "written": 0}
_DUP = re.compile(r"^(.*)\.\d{3}$")


def enabled():
    return os.environ.get("DR_GRAPH_CACHE", "1") != "0"


def _log(tag, exc):
    STATS["errors"] += 1
    if tag not in _LOGGED:
        _LOGGED.add(tag)
        print("DataRaiders graph_cache %s:" % tag, repr(exc))


def _build():
    try:
        from ...Outfit_Accessories import pak_catalog
        return pak_catalog.installed_build(getattr(bpy.context.scene, "arc_pak_dir", "")) or "nobuild"
    except Exception:
        return "nobuild"


def cache_root():
    base = bpy.utils.user_resource("CONFIG", path="dataraiders_graph_cache", create=True)
    return os.path.join(base, "graph_cache")


def _dir(rec=None):
    return os.path.join(cache_root(), _build(), rec or os.environ.get("DR_GRAPH_RECIPE") or recipe())


def material_key(json_path, colours, preset_key="", extra=None):
    sc = bpy.context.scene
    pro = getattr(sc, "arc_pro_mode", getattr(sc, "arc_pro", ""))
    # Graph-shape switches must key the cache, or a toggle silently reuses the other shape.
    modes = [k + "=" + v for k, v, default in (("DR_MUX_CHAIN", "1", False), ("DR_GRAPH_BLOCKS", "0", False))
             if os.environ.get(k) == v]
    if modes:
        extra = list(extra or []) + modes
    blob = json.dumps([os.path.normcase(os.path.normpath(json_path or "")),
                       os.environ.get("DR_GRAPH_RECIPE") or recipe(), _build(),
                       colours or {}, preset_key or "", str(pro), repr(extra or [])],
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _index(d):
    if d not in _INDEX:
        try:
            with open(os.path.join(d, "index.json"), encoding="utf-8") as f:
                _INDEX[d] = json.load(f)
        except Exception:
            _INDEX[d] = {}
    return _INDEX[d]


def _norm(p):
    try:
        return os.path.normcase(os.path.normpath(bpy.path.abspath(p or "")))
    except Exception:
        return ""


_PRELOADED = {}


def _img_key(im):
    if im.source != "FILE" or not im.filepath or im.packed_file is not None:
        return None
    return (_norm(im.filepath), im.colorspace_settings.name, im.alpha_mode)


_SKIP = {"rna_type", "name", "label", "location", "width", "height", "select", "dimensions",
         "color", "use_custom_color", "show_options", "show_preview", "hide", "mute_tag",
         "show_texture", "parent", "internal_links", "inputs", "outputs", "width_hidden",
         "location_absolute", "warning_propagation", "bl_width_default", "bl_height_default"}


def _val(v):
    if hasattr(v, "name") and hasattr(v, "bl_rna"):
        # An appended copy points at "<name>.001" until its own references are remapped;
        # hashing that suffix made every nested group look different from its base.
        m = _DUP.match(v.name)
        return "ID:" + (m.group(1) if m else v.name)
    try:
        return repr(tuple(round(x, 5) for x in v))
    except Exception:
        return repr(round(v, 5) if isinstance(v, float) else v)


def _node_sig(n):
    parts = [n.bl_idname]
    for pr in n.bl_rna.properties:
        if pr.identifier in _SKIP or pr.identifier.startswith("bl_") or pr.type == "COLLECTION":
            continue
        try:
            parts.append(pr.identifier + "=" + _val(getattr(n, pr.identifier)))
        except Exception:
            pass
    for so in n.inputs:
        dv = getattr(so, "default_value", None)
        if dv is not None:
            parts.append(so.identifier + ":" + _val(dv))
    return "|".join(parts)


def group_hash(g):
    """Content hash: node types, node props, input defaults, links."""
    try:
        nt = sorted(_node_sig(n) for n in g.nodes)
        lk = sorted("%s.%s>%s.%s" % (l.from_node.name, l.from_socket.identifier,
                                     l.to_node.name, l.to_socket.identifier) for l in g.links)
        return hashlib.sha1(("%d|%s|%s" % (len(nt), ";".join(nt), ",".join(lk))).encode()).hexdigest()[:12]
    except Exception:
        return ""


def _load_file(d, fn):
    """Append every cached material of one blend in a single library read."""
    idx = _index(d)
    want = {v["mat"]: k for k, v in idx.items() if v.get("file") == fn}
    tl = time.perf_counter()
    g0 = set(bpy.data.node_groups.keys())
    i0 = set(bpy.data.images.keys())
    with bpy.data.libraries.load(os.path.join(d, fn), link=False) as (src, dst):
        req = [n for n in src.materials if n in want]
        dst.materials = list(req)
    gall = {g.name: g for g in bpy.data.node_groups}
    hcache = {}
    t0 = time.perf_counter()
    tl = t0 - tl
    gmap, imap = {}, {}
    new = sorted(set(gall) - g0)
    target = {}

    def _hash_of(g):
        h = hcache.get(g.name)
        if h is None:
            h = hcache[g.name] = group_hash(g)
        return h

    def _resolve(n, depth=0):
        """Existing group that the appended group n duplicates, else None."""
        if n in target:
            return target[n]
        target[n] = None
        g = gall[n]
        m = _DUP.match(n)
        if m is None or m.group(1) not in g0 or depth > 16 or g.get("dr.wrapper") or g.get("dr.stage_tree"):
            return None     # a wrapper holds one material's values: never merged with another
        h = _hash_of(g)
        # group_hash ignores the .NNN suffix of nested groups, so a parent only matches
        # when every appended child it holds resolves to an existing group as well.
        kids = {nd.node_tree.name for nd in g.nodes
                if getattr(nd, "node_tree", None) is not None and nd.node_tree.name not in g0}
        if h and all(_resolve(k, depth + 1) is not None for k in kids if k in gall):
            for cand in (m.group(1), m.group(1) + "_h" + h[:6]):
                if cand in g0 and _hash_of(gall[cand]) == h:
                    target[n] = gall[cand]
                    break
        return target[n]

    for n in new:
        g = gall[n]
        m = _DUP.match(n)
        if m is None or m.group(1) not in g0:
            continue
        hit = _resolve(n)
        STATS.setdefault("grp", [0, 0])[0 if hit is not None else 1] += 1
        if hit is not None:
            gmap[g] = hit
    for n in new:
        g = gall[n]
        m = _DUP.match(n)
        if m is None or m.group(1) not in g0 or g in gmap:
            continue
        try:
            g.name = m.group(1) + "_h" + _hash_of(g)[:6]
        except Exception:
            pass
    iall = {im.name: im for im in bpy.data.images}
    by_key, by_path = {}, {}
    for n in i0:
        k = _img_key(iall[n]) or _gen_key(iall[n])
        if k:
            by_key.setdefault(k, iall[n])
        pp = iall[n].get("arc_shader_prefetch_path") if iall[n].source == "FILE" else None
        if pp:
            by_path.setdefault(_norm(iall[n].filepath), []).append(iall[n])
    inew = [iall[n] for n in sorted(set(iall) - i0)]
    for im in inew:
        k = _img_key(im) or _gen_key(im)
        hit = by_key.get(k) if k else None
        if hit is None and k and k[0] != "GEN":
            # The live build adopts an unconsumed prefetch buffer of the same file and
            # sets its role (colour space, alpha mode); do the same instead of keeping
            # the cached copy beside the 0-user base image.
            hit = next((c for c in by_path.get(k[0], ())
                        if c.users == 0 and not c.is_dirty and c.packed_file is None
                        and "arc_shader_prefetch_path" in c), None)
            if hit is not None:
                by_path[k[0]].remove(hit)
                del hit["arc_shader_prefetch_path"]
                hit.colorspace_settings.name = im.colorspace_settings.name
                hit.alpha_mode = im.alpha_mode
                if im.get("arc_shader_image_key"):
                    hit["arc_shader_image_key"] = im["arc_shader_image_key"]
                by_key[k] = hit
        if hit is not None:
            diff = [a for a in ("source", "use_view_as_render", "alpha_mode") if getattr(im, a, None) != getattr(hit, a, None)]
            STATS.setdefault("img_remaps", []).append((fn[:8], im.name, hit.name, diff))
            imap[im] = hit
        elif k:
            by_key[k] = im
    # Remap only inside the appended trees: ID.user_remap scans all of bpy.data per call (O(n^2) on big scenes).
    td = time.perf_counter() - t0
    trees = [m.node_tree for m in dst.materials if m is not None and m.node_tree is not None]
    trees += [g for n, g in gall.items() if n not in g0 and g not in gmap]
    for tr in trees:
        for nd in tr.nodes:
            gt = getattr(nd, "node_tree", None)
            if gt is not None and gt in gmap:
                nd.node_tree = gmap[gt]
            im = getattr(nd, "image", None)
            if im is not None and im in imap:
                nd.image = imap[im]
    dead = [g for g in gmap if g.users == 0] + [im for im in imap if im.users == 0]
    # Images this load appended that nothing uses (users of removed duplicates only).
    dead += [im for im in inew if im not in imap and im.users == 0]
    # Duplicates still used only by other duplicates were left behind before, so
    # bpy.data grew by ~15 groups per load and each later libraries.load got slower.
    # Remove them too once every user is itself a removed duplicate.
    rest = [x for x in list(gmap) + list(imap) if x.users > 0]
    if rest:
        doomed = set(gmap) | set(imap)
        users = bpy.data.user_map(subset=rest)
        dead += [x for x in rest if users.get(x) and users[x] <= doomed]
    gone = set(dead)
    left = [x.name for x in list(gmap) + list(imap) if x not in gone]
    if dead:
        bpy.data.batch_remove(dead)
    STATS.setdefault("load_s", []).append((fn[:8], "lib", round(tl, 2), "dedupe", round(td, 2), "all", round(time.perf_counter() - t0, 2), len(gmap), len(imap), len(left)))
    # Map by request order: a cached name that already ends in ".001" is renamed ".002"
    # on append when ".001" exists, so a name/base lookup missed it and the import fell
    # back to a live build on every run.
    for n, m in zip(req, dst.materials):
        if m is not None:
            m.use_fake_user = False
            _PRELOADED[want[n]] = m.name


def _gen_key(im):
    """Key of a generated (not file) image: name stem, size, type, colour, role."""
    if im.source != "GENERATED" or im.packed_file is not None:
        return None
    m = _DUP.match(im.name)
    try:
        return ("GEN", m.group(1) if m else im.name, tuple(im.size), im.generated_type,
                tuple(round(c, 4) for c in im.generated_color), bool(im.use_generated_float),
                im.colorspace_settings.name, im.alpha_mode)
    except Exception:
        return None


def _drop_entry(d, key, fn):
    """Drop one index entry whose material is not in its blend (entry only, blend untouched)."""
    idx = _index(d)
    if idx.pop(key, None) is None:
        return
    print("WARNING: DataRaiders graph_cache: material for entry %s missing in %s; entry dropped" % (key[:8], fn))
    try:
        tmp = os.path.join(d, "index.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(idx, f)
        os.replace(tmp, os.path.join(d, "index.json"))
    except Exception as exc:
        _log("drop", exc)


def try_load(key, mat):
    """Use the cached material for ``key``; return it, or None (live build)."""
    if not enabled() or not key:
        return None
    d = _dir()
    ent = _index(d).get(key)
    if not ent or ent.get("v") != ENTRY_V:  # older layouts just miss; flush rewrites them
        STATS["misses"] += 1
        return None
    try:
        if key not in _PRELOADED:
            _load_file(d, ent["file"])
        new = bpy.data.materials.get(_PRELOADED.pop(key, "") or "")
        if new is None:
            STATS["misses"] += 1
            _drop_entry(d, key, ent["file"])     # next import rebuilds live and re-caches it
            return None
        name = mat.name
        # A batch file appends later materials before their placeholders exist, so the
        # placeholder gets ".001"; keep the cached base name instead.
        dup = _DUP.match(name)
        if dup and dup.group(1) == ent.get("mat"):
            name = dup.group(1)
        mat.name = name + "__drgc_old"
        new.name = name
        mat.user_remap(new)
        STATS["hits"] += 1
        return new
    except Exception as exc:
        _log("load", exc)
        return None


def drop_unused():
    """Remove preloaded cache materials no import asked for."""
    for name in list(_PRELOADED.values()):
        m = bpy.data.materials.get(name)
        if m is not None and m.users == 0:
            bpy.data.materials.remove(m)
    _PRELOADED.clear()


def queue(key, mat):
    if enabled() and key and mat is not None:
        _QUEUE.append((key, mat.name))
        try:
            if not bpy.app.timers.is_registered(_timer_flush):
                bpy.app.timers.register(_timer_flush, first_interval=2.0)
        except Exception:
            pass


def _timer_flush():
    flush()
    return None


def flush():
    """Write queued materials, one blend each; update index. Returns the last file name or None."""
    gen = iter_flush()
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


def iter_flush():
    """flush() in steps: yields after each material file so a staged import can redraw."""
    drop_unused()
    if not _QUEUE:
        return None
    yield "unused"
    t = time.perf_counter()
    try:
        d = _dir()
        os.makedirs(d, exist_ok=True)
        idx = _index(d)
        mats, rows = set(), {}
        for key, name in _QUEUE:
            m = bpy.data.materials.get(name)
            if m is not None and idx.get(key, {}).get("v") != ENTRY_V:
                mats.add(m)
                rows[key] = name
        _QUEUE.clear()
        if not mats:
            return None
        th = time.perf_counter()
        for g in list(bpy.data.node_groups):
            try:
                g["drgc_h"] = group_hash(g)
            except Exception:
                pass
            if time.perf_counter() - th > 0.15:     # staged import: redraw between hash batches
                yield "hash"
                th = time.perf_counter()
        yield "hash"
        # One blend per import batch: each libraries.load costs time in proportion to
        # every node already in bpy.data, so nine per-material loads cost ~4x one load.
        tw = []
        live = {key: bpy.data.materials.get(name) for key, name in rows.items()}
        live = {k: m for k, m in live.items() if m is not None}
        if live:
            t1 = time.perf_counter()
            fn = hashlib.sha1("|".join(sorted(live)).encode()).hexdigest()[:16] + "_b.blend"
            bpy.data.libraries.write(os.path.join(d, fn), set(live.values()), fake_user=True, compress=True)
            now = time.time()
            for key, m in live.items():
                idx[key] = {"file": fn, "mat": m.name, "t": now, "v": ENTRY_V}
            tw.append(time.perf_counter() - t1)
            yield fn
        STATS.setdefault("write_s", []).extend(round(x, 3) for x in tw)
        _evict(d, idx)
        tmp = os.path.join(d, "index.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(idx, f)
        os.replace(tmp, os.path.join(d, "index.json"))
        STATS["written"] += len(rows)
        print("DataRaiders graph_cache: wrote %d materials %.2fs" % (len(rows), time.perf_counter() - t))
        return fn
    except Exception as exc:
        _QUEUE.clear()
        _log("write", exc)
        return None


def _evict(d, idx):
    root = cache_root()
    files = []
    for dp, _dn, fns in os.walk(root):
        for f in fns:
            if f.endswith(".blend"):
                p = os.path.join(dp, f)
                st = os.stat(p)
                files.append((st.st_mtime, st.st_size, p))
    total = sum(s for _m, s, _p in files)
    for _m, s, p in sorted(files):
        if total <= CAP_BYTES:
            break
        try:
            os.remove(p)
            total -= s
            if os.path.dirname(p) == d:
                fn = os.path.basename(p)
                for k in [k for k, v in idx.items() if v.get("file") == fn]:
                    del idx[k]
        except OSError:
            pass


def report():
    n = STATS["hits"] + STATS["misses"]
    return dict(STATS, hit_rate=(STATS["hits"] / n if n else 0.0))


def clear_material_cache():
    """Delete the on-disk material graph cache and forget loaded entries."""
    import shutil
    root = cache_root()
    removed = os.path.isdir(root)
    if removed:
        shutil.rmtree(root, ignore_errors=True)
    _INDEX.clear()
    _PRELOADED.clear()
    return removed
