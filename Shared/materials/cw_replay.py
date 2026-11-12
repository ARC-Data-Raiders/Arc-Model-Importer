"""Flat outfit graph builds into cheap twin nodes, then one commit into the real tree.

Every node, link or value write on a big node tree re-runs the whole tree update (about
1 ms at 1500 nodes), and a flat outfit graph does tens of thousands of them. While a
replay is active the builder gets proxy ``nodes`` / ``links``:

- each proxy node has a real twin node in a small scratch material, so its writes are
  cheap and its reads return what a fresh build would return (the twin is a fresh node
  that received the same writes);
- names, frame parents and links are emulated in Python with Blender's own rules.

``commit()`` makes the real tree equal to the proxy graph and turns the proxies into
pass-throughs. With a stash (the committed flat tree of an earlier build plus its
creation log) the real tree starts as a copy of that tree: nodes pair by creation order
and type, and only differing values, nodes and links are written. A later colourway of
the same mesh then costs a few hundred writes instead of the whole build. The stash
never decides the result, only how many writes the commit needs.
"""
import os
import re

import bpy

_DIGITS = re.compile(r"^(.*)\.(\d+)$")
_MAXB = 63  # MAX_NAME 64 bytes, nul included
_SCRATCH_CAP = 40
_PTR = [1 << 40]

STATS = {"commits": 0, "stash_commits": 0, "created": 0, "removed": 0, "renamed": 0,
         "prop_writes": 0, "sock_writes": 0, "links_added": 0, "links_removed": 0,
         "fresh_fallbacks": 0, "aborts": 0}


def enabled() -> bool:
    return os.environ.get("DR_CW_REPLAY", "1") != "0"


class ReplayError(RuntimeError):
    pass


def _next_ptr():
    _PTR[0] += 16
    return _PTR[0]


def _trim(s, maxb=_MAXB):
    b = s.encode("utf-8")
    if len(b) <= maxb:
        return s
    b = b[:maxb]
    while b:
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            b = b[:-1]
    return ""


def unique_name(name, taken):
    """BLI_uniquename_cb: keep name when free, else left part + next free .NNN."""
    if not taken(name):
        return name
    m = _DIGITS.match(name)
    left, num = (m.group(1), int(m.group(2))) if m else (name, 0)
    while True:
        num += 1
        suffix = ".%03d" % num
        cand = _trim(left, _MAXB - len(suffix.encode())) + suffix
        if not taken(cand):
            return cand


def _pyval(v):
    if hasattr(v, "to_dict"):
        return v.to_dict()
    if hasattr(v, "to_list"):
        return v.to_list()
    return v


# --------------------------------------------------------------------------- proxies

class _Sock:
    def __init__(self, node, ts, out):
        d = self.__dict__
        d["_n"] = node
        d["_s"] = ts
        d["_o"] = out
        d["_real"] = None

    def __getattr__(self, a):
        r = self.__dict__["_real"]
        if r is not None:
            return getattr(r, a)
        if self.__dict__["_n"]._rp.aborted:
            raise ReplayError("replay aborted")
        if a == "node":
            return self._n
        if a == "is_linked":
            return bool(self._n._rp._sock_links(self))
        if a == "links":
            return tuple(self._n._rp._sock_links(self))
        if a == "id_data":
            return self._n._rp.tree
        if a == "is_output":
            return self._o
        return getattr(self._s, a)

    def __setattr__(self, a, v):
        r = self.__dict__["_real"]
        if r is not None:
            setattr(r, a, v)
        elif self.__dict__["_n"]._rp.aborted:
            raise ReplayError("replay aborted")
        else:
            setattr(self._s, a, v)

    def as_pointer(self):
        r = self.__dict__["_real"]
        return r.as_pointer() if r is not None else self._s.as_pointer()

    def __repr__(self):
        return "<ReplaySock %s.%s>" % (self._n.name, getattr(self._s, "identifier", "?"))


class _Socks:
    def __init__(self, node, coll, out):
        d = self.__dict__
        d["_n"] = node
        d["_c"] = coll
        d["_o"] = out

    def _w(self, ts):
        cache = self._n.__dict__["_socks"]
        key = (ts.as_pointer(), self._o)
        sp = cache.get(key)
        if sp is None or sp._s != ts:
            sp = cache[key] = _Sock(self._n, ts, self._o)
        return sp

    def __getitem__(self, k):
        if isinstance(k, slice):
            return [self._w(t) for t in self._c[k]]
        return self._w(self._c[k])

    def get(self, k, default=None):
        t = self._c.get(k)
        return default if t is None else self._w(t)

    def __iter__(self):
        for t in self._c:
            yield self._w(t)

    def __len__(self):
        return len(self._c)

    def __bool__(self):
        return len(self._c) > 0

    def __contains__(self, k):
        if isinstance(k, _Sock):
            return any(k._s == t for t in self._c)
        return k in self._c

    def keys(self):
        return self._c.keys()

    def values(self):
        return [self._w(t) for t in self._c]

    def items(self):
        return [(k, self._w(t)) for k, t in self._c.items()]

    def find(self, k):
        return self._c.find(k)

    def __getattr__(self, a):
        return getattr(self._c, a)


class _Node:
    def __init__(self, rp, twin, kind, name, idx):
        d = self.__dict__
        d["_rp"] = rp
        d["_t"] = twin
        d["_k"] = kind
        d["_nm"] = name
        d["_par"] = None
        d["_dead"] = False
        d["_real"] = None
        d["_ptr"] = _next_ptr()
        d["_idx"] = idx
        d["_socks"] = {}

    def __getattr__(self, a):
        d = self.__dict__
        r = d["_real"]
        if r is not None:
            return getattr(r, a)
        if d["_rp"].aborted:
            raise ReplayError("replay aborted")
        if a == "name":
            return d["_nm"]
        if a == "parent":
            return d["_par"]
        if a == "inputs":
            return _Socks(self, d["_t"].inputs, False)
        if a == "outputs":
            return _Socks(self, d["_t"].outputs, True)
        if a == "id_data":
            return d["_rp"].tree
        if a == "internal_links":
            raise ReplayError("internal_links read during replay")
        return getattr(d["_t"], a)

    def __setattr__(self, a, v):
        d = self.__dict__
        r = d["_real"]
        if r is not None:
            setattr(r, a, v)
        elif d["_rp"].aborted:
            raise ReplayError("replay aborted")
        elif a == "name":
            d["_rp"]._rename(self, v)
        elif a == "parent":
            d["_rp"]._set_parent(self, v)
        else:
            setattr(d["_t"], a, v)

    def as_pointer(self):
        r = self.__dict__["_real"]
        return r.as_pointer() if r is not None else self.__dict__["_ptr"]

    def _o(self):
        d = self.__dict__
        if d["_real"] is not None:
            return d["_real"]
        if d["_rp"].aborted:
            raise ReplayError("replay aborted")
        return d["_t"]

    def __getitem__(self, k):
        return self._o()[k]

    def __setitem__(self, k, v):
        self._o()[k] = v

    def __delitem__(self, k):
        del self._o()[k]

    def __contains__(self, k):
        return k in self._o()

    def get(self, k, default=None):
        return self._o().get(k, default)

    def keys(self):
        return self._o().keys()

    def values(self):
        return self._o().values()

    def items(self):
        return self._o().items()

    def pop(self, k, *default):
        return self._o().pop(k, *default)

    def __repr__(self):
        return "<ReplayNode %s>" % (self.name,)


class _Link:
    def __init__(self, a, b):
        d = self.__dict__
        d["from_socket"] = a
        d["to_socket"] = b
        d["is_muted"] = False
        d["is_hidden"] = False
        d["_real"] = None
        d["_dead"] = False

    def __getattr__(self, a):
        r = self.__dict__["_real"]
        if r is not None:
            return getattr(r, a)
        if a == "from_node":
            return self.from_socket._n
        if a == "to_node":
            return self.to_socket._n
        if a == "is_valid":
            return True
        if a == "id_data":
            return self.from_socket._n._rp.tree
        raise AttributeError(a)

    def __setattr__(self, a, v):
        d = self.__dict__
        r = d["_real"]
        if r is not None:
            setattr(r, a, v)
        elif a in ("is_muted", "is_hidden"):
            d[a] = bool(v)
        else:
            raise ReplayError("link write %s during replay" % a)


class _Nodes:
    def __init__(self, rp):
        self.__dict__["_rp"] = rp

    def _live(self):
        rp = self._rp
        if rp.aborted:
            raise ReplayError("replay aborted")
        return rp.real.nodes if rp.done else None

    def new(self, *a, **kw):
        r = self._live()
        if r is not None:
            return r.new(*a, **kw)
        return self._rp._new(a[0] if a else kw["type"])

    def remove(self, n):
        r = self._live()
        if r is not None:
            return r.remove(n._real if isinstance(n, _Node) else n)
        self._rp._remove(n)

    def clear(self):
        r = self._live()
        if r is not None:
            return r.clear()
        self._rp._clear()

    def get(self, k, default=None):
        r = self._live()
        if r is not None:
            return r.get(k, default)
        return self._rp.byname.get(k, default)

    def __getitem__(self, k):
        r = self._live()
        if r is not None:
            return r[k]
        if isinstance(k, str):
            return self._rp.byname[k]
        return self._rp.order[k]

    def __iter__(self):
        r = self._live()
        return iter(r) if r is not None else iter(list(self._rp.order))

    def __len__(self):
        r = self._live()
        return len(r) if r is not None else len(self._rp.order)

    def __bool__(self):
        return len(self) > 0

    def __contains__(self, k):
        r = self._live()
        if r is not None:
            return k in r
        if isinstance(k, str):
            return k in self._rp.byname
        return isinstance(k, _Node) and not k._dead and k._rp is self._rp

    def keys(self):
        r = self._live()
        return r.keys() if r is not None else list(self._rp.byname)

    def values(self):
        r = self._live()
        return r.values() if r is not None else list(self._rp.order)

    def items(self):
        r = self._live()
        return r.items() if r is not None else [(n._nm, n) for n in self._rp.order]

    def foreach_get(self, attr, seq):
        r = self._live()
        if r is not None:
            return r.foreach_get(attr, seq)
        if attr != "location":
            raise ReplayError("nodes.foreach_get(%s) during replay" % attr)
        for i, n in enumerate(self._rp.order):
            seq[2 * i], seq[2 * i + 1] = n._t.location

    def foreach_set(self, attr, seq):
        r = self._live()
        if r is not None:
            return r.foreach_set(attr, seq)
        if attr != "location":
            raise ReplayError("nodes.foreach_set(%s) during replay" % attr)
        for i, n in enumerate(self._rp.order):
            n._t.location = (seq[2 * i], seq[2 * i + 1])

    @property
    def id_data(self):
        return self._rp.tree

    def __getattr__(self, a):
        rp = self.__dict__["_rp"]
        if a == "active":
            return rp.real.nodes.active if rp.done else rp.active
        if rp.done:
            return getattr(rp.real.nodes, a)
        raise ReplayError("nodes.%s during replay" % a)

    def __setattr__(self, a, v):
        rp = self.__dict__["_rp"]
        if a == "active":
            if rp.done:
                rp.real.nodes.active = v._real if isinstance(v, _Node) else v
            else:
                rp.active = v
            return
        if rp.done:
            setattr(rp.real.nodes, a, v)
        else:
            raise ReplayError("nodes.%s write during replay" % a)


class _Links:
    def __init__(self, rp):
        self.__dict__["_rp"] = rp

    def new(self, input=None, output=None, verify_limits=True, handle_dynamic_sockets=False):
        rp = self._rp
        a, b = input, output
        if rp.done:
            a = a._real if isinstance(a, _Sock) else a
            b = b._real if isinstance(b, _Sock) else b
            return rp.real.links.new(a, b, verify_limits=verify_limits)
        return rp._link(a, b, verify_limits)

    def remove(self, lk):
        rp = self._rp
        if rp.done:
            return rp.real.links.remove(lk._real if isinstance(lk, _Link) else lk)
        rp._unlink(lk)

    def clear(self):
        rp = self._rp
        if rp.done:
            return rp.real.links.clear()
        for lk in list(rp.links):
            rp._unlink(lk)

    def __iter__(self):
        rp = self._rp
        return iter(rp.real.links) if rp.done else iter([l for l in rp.links if not l._dead])

    def __len__(self):
        rp = self._rp
        return len(rp.real.links) if rp.done else sum(1 for l in rp.links if not l._dead)

    def __bool__(self):
        return len(self) > 0

    @property
    def id_data(self):
        return self._rp.tree

    def __getattr__(self, a):
        rp = self.__dict__["_rp"]
        if rp.done:
            return getattr(rp.real.links, a)
        raise ReplayError("links.%s during replay" % a)


class _Tree:
    def __init__(self, rp):
        self.__dict__["_rp"] = rp

    @property
    def nodes(self):
        return self._rp.nodes

    @property
    def links(self):
        return self._rp.links_proxy

    @property
    def id_data(self):
        return self

    def as_pointer(self):
        return self._rp.real.as_pointer()

    def __eq__(self, other):
        o = other._rp.real if isinstance(other, _Tree) else other
        return self._rp.real == o

    def __hash__(self):
        return hash(self._rp.real.as_pointer())

    def __getattr__(self, a):
        return getattr(self.__dict__["_rp"].real, a)

    def __setattr__(self, a, v):
        setattr(self.__dict__["_rp"].real, a, v)

    def __getitem__(self, k):
        return self._rp.real[k]

    def __setitem__(self, k, v):
        self._rp.real[k] = v

    def __contains__(self, k):
        return k in self._rp.real

    def get(self, k, default=None):
        return self._rp.real.get(k, default)


# --------------------------------------------------------------------------- replay

_BASE_NAMES = {}


class Replay:
    def __init__(self, real_tree, stash=None):
        self.real = real_tree
        self.real_ptr = real_tree.as_pointer()
        self.stash = stash  # (log, name) of the copy real_tree started from, or None
        self.scratch = []
        self.fill = _SCRATCH_CAP
        self.made = []  # every proxy node, creation order
        self.order = []  # alive proxy nodes, tree order
        self.byname = {}
        self.links = []
        self.in_links = {}  # id(to sock proxy) -> [links]
        self.out_links = {}
        self.active = None
        self.done = False
        self.aborted = False
        self.nodes = _Nodes(self)
        self.links_proxy = _Links(self)
        self.tree = _Tree(self)

    # -- scratch twins
    def _scratch_nodes(self):
        if self.fill >= _SCRATCH_CAP:
            m = bpy.data.materials.new("~DR.ReplayScratch")
            m.use_nodes = True
            m.node_tree.nodes.clear()
            self.scratch.append(m)
            self.fill = 0
        self.fill += 1
        return self.scratch[-1].node_tree.nodes

    def _base_name(self, kind, twin):
        b = _BASE_NAMES.get(kind)
        if b is None:
            m = _DIGITS.match(twin.name)
            b = _BASE_NAMES[kind] = m.group(1) if m else twin.name
        return b

    def _taken(self, name, me=None):
        n = self.byname.get(name)
        return n is not None and n is not me

    def _new(self, kind):
        twin = self._scratch_nodes().new(kind)
        name = unique_name(_trim(self._base_name(kind, twin)), self._taken)
        p = _Node(self, twin, kind, name, len(self.made))
        self.made.append(p)
        self.order.append(p)
        self.byname[name] = p
        return p

    def _own(self, n):
        if not isinstance(n, _Node) or n._rp is not self:
            raise ReplayError("foreign node %r in replay" % (n,))
        if n._dead:
            raise ReferenceError("StructRNA of type Node has been removed")
        return n

    def _rename(self, p, v):
        self._own(p)
        v = _trim(str(v))
        if not v:
            v = self._base_name(p._k, p._t)
        new = unique_name(v, lambda s: self._taken(s, p))
        if new == p._nm:
            return
        if self.byname.get(p._nm) is p:
            del self.byname[p._nm]
        p.__dict__["_nm"] = new
        self.byname[new] = p

    def _view(self, p):
        x, y = p._t.location
        q = p._par
        while q is not None:
            qx, qy = q._t.location
            x += qx
            y += qy
            q = q._par
        return x, y

    def _set_parent(self, p, par):
        self._own(p)
        if par is not None:
            self._own(par)
            if par._k != "NodeFrame":
                return
            q = par
            while q is not None:  # nodeIsParentAndChild
                if q is p:
                    return
                q = q._par
        if p._par is not None:  # detach, keep view location
            p._t.location = self._view(p)
            p.__dict__["_par"] = None
        if par is not None:
            vx, vy = self._view(p)
            px, py = self._view(par)
            p.__dict__["_par"] = par
            p._t.location = (vx - px, vy - py)

    def _remove(self, p):
        self._own(p)
        for lk in list(self.links):
            if not lk._dead and (lk.from_socket._n is p or lk.to_socket._n is p):
                self._unlink(lk)
        for c in self.order:
            if c._par is p:
                self._set_parent(c, None)
        if self.active is p:
            self.active = None
        self.order.remove(p)
        if self.byname.get(p._nm) is p:
            del self.byname[p._nm]
        p.__dict__["_dead"] = True
        tw = p._t
        tw.id_data.nodes.remove(tw)
        p.__dict__["_t"] = None

    def _clear(self):
        """Drop every proxy node and link at once (one _remove per node scans all links)."""
        for lk in self.links:
            lk.__dict__["_dead"] = True
        for p in self.order:
            p.__dict__["_dead"] = True
            p.__dict__["_par"] = None
            p.__dict__["_t"] = None
        self.links = []
        self.in_links = {}
        self.out_links = {}
        self.order = []
        self.byname = {}
        self.active = None
        # The twins go with their scratch materials; new nodes start a new scratch tree.
        self._free_scratch()
        self.fill = _SCRATCH_CAP

    def _sock_links(self, s):
        key = id(s)
        if s._o:
            return [l for l in self.out_links.get(key, ()) if not l._dead]
        return [l for l in self.in_links.get(key, ()) if not l._dead]

    def _link(self, a, b, verify_limits=True):
        if not isinstance(a, _Sock) or not isinstance(b, _Sock):
            raise ReplayError("links.new with a foreign socket")
        self._own(a._n)
        self._own(b._n)
        if a._o == b._o:
            raise RuntimeError("Same input/output direction of sockets")
        if not a._o:
            a, b = b, a
        if verify_limits:
            for old in self._sock_links(b):
                self._unlink(old)
        lk = _Link(a, b)
        self.links.append(lk)
        self.in_links.setdefault(id(b), []).append(lk)
        self.out_links.setdefault(id(a), []).append(lk)
        return lk

    def _unlink(self, lk):
        if not isinstance(lk, _Link):
            raise ReplayError("links.remove with a foreign link")
        if lk._dead:
            raise ReferenceError("StructRNA of type NodeLink has been removed")
        lk.__dict__["_dead"] = True

    # -- commit
    def commit(self):
        """Write the proxy graph into the real tree; proxies pass through afterwards."""
        if self.done:
            return
        from . import common as _common
        real = self.real
        rnodes = real.nodes
        alive = list(self.order)
        pair = {}
        stash_log = self.stash[0] if self.stash else None
        if stash_log:
            by = {n.name: n for n in rnodes}
            for p in self.made:
                i = p._idx
                if i >= len(stash_log) or stash_log[i][0] != p._k:
                    break
                if p._dead or stash_log[i][1] is None:
                    continue
                r = by.get(stash_log[i][1])
                if r is not None and r.bl_idname == p._k:
                    pair[id(p)] = r
            # Where creation order diverges (another mesh of the outfit), the left-over
            # stash nodes still serve: same type, same name first, else any of that type.
            keep = {r.as_pointer() for r in pair.values()}
            pools = {}
            for r in rnodes:
                if r.as_pointer() not in keep:
                    pools.setdefault(r.bl_idname, []).append(r)
            if pools:
                for pool in pools.values():
                    pool.reverse()
                for p in alive:
                    if id(p) in pair:
                        continue
                    pool = pools.get(p._k)
                    if not pool:
                        continue
                    r = by.get(p._nm)
                    if r is not None and r.bl_idname == p._k and r.as_pointer() not in keep:
                        pool.remove(r)
                    else:
                        r = pool.pop()
                    keep.add(r.as_pointer())
                    pair[id(p)] = r
            for r in list(rnodes):
                if r.as_pointer() not in keep:
                    rnodes.remove(r)
                    STATS["removed"] += 1
            # Free every final name first so pairing renames never collide.
            want = {p._nm for p in alive}
            for p in alive:
                r = pair.get(id(p))
                if r is not None and r.name != p._nm and r.name in want:
                    r.name = "~drr%x" % p._ptr
                    STATS["renamed"] += 1
        def skey(s):
            coll = s._n._t.outputs if s._o else s._n._t.inputs
            for i, t in enumerate(coll):
                if t == s._s:
                    return i
            raise ReplayError("socket %r left its node" % (s,))
        # A fresh commit adds each link as soon as both its nodes exist. Every write costs a
        # tree update that grows faster than the node count, so links written while the tree
        # is still small cost less than all of them at full size. Replay order is kept: a
        # proxy link was made after both its nodes, so the pointer never has to skip ahead.
        early = []
        if not stash_log:
            early = [lk for lk in self.links if not lk._dead
                     and not lk.from_socket._n._dead and not lk.to_socket._n._dead]
        e_at = 0
        e_made = set()
        for p in alive:
            r = pair.get(id(p))
            if r is None:
                r = pair[id(p)] = rnodes.new(p._k)
                STATS["created"] += 1
            if r.name != p._nm:
                r.name = p._nm
                STATS["renamed"] += 1
                if r.name != p._nm:
                    raise ReplayError("name %r became %r" % (p._nm, r.name))
            try:
                _sync_node(p._t, r)
            except (ReplayError, ValueError, TypeError):
                if not stash_log:
                    raise
                # A stash node whose sockets differ: replace it by a fresh node.
                STATS["fresh_fallbacks"] += 1
                rnodes.remove(r)
                r = pair[id(p)] = rnodes.new(p._k)
                STATS["created"] += 1
                r.name = p._nm
                if r.name != p._nm:
                    raise ReplayError("name %r became %r" % (p._nm, r.name))
                _sync_node(p._t, r)
            if early:
                e_made.add(id(p))
                while e_at < len(early):
                    lk = early[e_at]
                    a, b = lk.from_socket, lk.to_socket
                    if id(a._n) not in e_made or id(b._n) not in e_made:
                        break
                    lk.__dict__["_real"] = real.links.new(pair[id(a._n)].outputs[skey(a)],
                                                          pair[id(b._n)].inputs[skey(b)])
                    STATS["links_added"] += 1
                    e_at += 1
        if e_at < len(early):
            raise ReplayError("early links stopped at %d of %d" % (e_at, len(early)))
        # Frames, then locations in one write (parent changes move the stored location).
        for p in alive:
            r = pair[id(p)]
            want = pair[id(p._par)] if p._par is not None else None
            if r.parent != want:
                r.parent = want
        if len(rnodes) != len(alive):
            raise ReplayError("node count %d != %d" % (len(rnodes), len(alive)))
        r_index = {r.as_pointer(): i for i, r in enumerate(rnodes)}
        flat = [0.0] * (2 * len(rnodes))
        rnodes.foreach_get("location", flat)
        for p in alive:
            i = r_index[pair[id(p)].as_pointer()]
            flat[2 * i], flat[2 * i + 1] = p._t.location
        rnodes.foreach_set("location", flat)
        # Links in replay order (a fresh commit already added them; they pair up below).
        want_links = []
        for lk in self.links:
            if lk._dead:
                continue
            a, b = lk.from_socket, lk.to_socket
            if a._n._dead or b._n._dead:
                continue
            want_links.append((lk, pair[id(a._n)], skey(a), pair[id(b._n)], skey(b)))
        def rkey(rl):
            fn, tn = rl.from_node, rl.to_node
            return (fn.as_pointer(), list(fn.outputs).index(rl.from_socket),
                    tn.as_pointer(), list(tn.inputs).index(rl.to_socket))
        have = {}
        for rl in list(real.links):
            have.setdefault(rkey(rl), []).append(rl)
        used = set()
        rlinks = real.links
        todo = []
        for lk, ra, ia, rb, ib in want_links:
            k = (ra.as_pointer(), ia, rb.as_pointer(), ib)
            hit = None
            for rl in have.get(k, ()):
                if id(rl) not in used:
                    hit = rl
                    break
            if hit is not None:
                used.add(id(hit))
                lk.__dict__["_real"] = hit
            else:
                todo.append((lk, ra, ia, rb, ib))
        for ks in have.values():
            for rl in ks:
                if id(rl) not in used:
                    rlinks.remove(rl)
                    STATS["links_removed"] += 1
        for lk, ra, ia, rb, ib in todo:
            lk.__dict__["_real"] = rlinks.new(ra.outputs[ia], rb.inputs[ib])
            STATS["links_added"] += 1
        for lk, *_ in want_links:
            rl = lk.__dict__["_real"]
            if rl.is_muted != lk.__dict__["is_muted"]:
                rl.is_muted = lk.__dict__["is_muted"]
            if rl.is_hidden != lk.__dict__["is_hidden"]:
                rl.is_hidden = lk.__dict__["is_hidden"]
        if len(rlinks) != len(want_links):
            raise ReplayError("link count %d != %d" % (len(rlinks), len(want_links)))
        # Deferred locations were keyed by proxy pointers.
        rec = _common._LOC_DEFER.get(self.real_ptr)
        if rec is not None:
            m = rec[1]
            for p in self.made:
                v = m.pop(p._ptr, None)
                if v is not None and not p._dead:
                    m[pair[id(p)].as_pointer()] = v
        if isinstance(self.active, _Node) and not self.active._dead:
            rnodes.active = pair[id(self.active)]
        # Pass-through from here on.
        for p in self.made:
            if p._dead:
                continue
            r = pair[id(p)]
            for (ptr, out), sp in p._socks.items():
                coll_t = p._t.outputs if out else p._t.inputs
                coll_r = r.outputs if out else r.inputs
                for i, t in enumerate(coll_t):
                    if t == sp._s:
                        sp.__dict__["_real"] = coll_r[i]
                        break
            p.__dict__["_real"] = r
        self.log = [(p._k, None if p._dead else p._nm) for p in self.made]
        self.done = True
        STATS["commits"] += 1
        if stash_log:
            STATS["stash_commits"] += 1
        self._free_scratch()
        _ACTIVE.pop(self.real_ptr, None)

    def _free_scratch(self):
        # One batch_remove remaps users once for all scratch materials (one remove each
        # scans the whole file: about 5 ms per material against 0.4 ms in a batch).
        live = []
        for m in self.scratch:
            try:
                m.name
                live.append(m)
            except ReferenceError:
                pass
        self.scratch = []
        if not live:
            return
        try:
            bpy.data.batch_remove(live)
        except (ReferenceError, RuntimeError):
            for m in live:
                try:
                    bpy.data.materials.remove(m)
                except (ReferenceError, RuntimeError):
                    pass

    def abort(self):
        if not self.done:
            STATS["aborts"] += 1
            self.aborted = True
        self._free_scratch()
        _ACTIVE.pop(self.real_ptr, None)


# --------------------------------------------------------------------------- RNA sync

_NODE_SKIP = frozenset((
    "rna_type", "name", "location", "location_absolute", "parent", "select", "dimensions",
    "inputs", "outputs", "internal_links", "type", "bl_idname", "bl_label", "bl_description",
    "bl_icon", "bl_static_type", "bl_width_default", "bl_width_min", "bl_width_max",
    "bl_height_default", "bl_height_min", "bl_height_max", "is_active_output",
))
_SOCK_SKIP = frozenset((
    "rna_type", "identifier", "node", "links", "is_linked", "is_output", "is_unavailable",
    "is_multi_input", "type", "bl_idname", "bl_label", "bl_subtype_label", "is_icon_visible",
    "is_inactive", "select", "label", "description", "pin_gizmo",
))
_PROPS_CACHE = {}


def _plan(rna, skip):
    key = (rna.identifier, id(skip))
    got = _PROPS_CACHE.get(key)
    if got is not None:
        return got
    first, later, structs = [], [], []
    for p in rna.properties:
        pid = p.identifier
        if pid == "rna_type" or pid in skip or p.type == "COLLECTION":
            continue
        if p.type == "POINTER":
            if p.is_readonly:
                structs.append(pid)
            else:
                first.append((pid, False))
            continue
        if p.is_readonly:
            continue
        arr = getattr(p, "is_array", False) or (getattr(p, "array_length", 0) or 0) > 0
        if p.type == "ENUM":
            first.append((pid, False))
        else:
            later.append((pid, arr))
    got = _PROPS_CACHE[key] = (tuple(first), tuple(later), tuple(structs))
    return got


def _same(a, b, arr):
    if arr:
        try:
            return tuple(a) == tuple(b)
        except TypeError:
            return a == b
    return a == b


def _sync_props(src, dst, skip, stat):
    first, later, structs = _plan(src.bl_rna, skip)
    for pid, arr in first + later:
        try:
            a = getattr(src, pid)
        except AttributeError:
            continue
        b = getattr(dst, pid)
        if not _same(a, b, arr):
            setattr(dst, pid, a)
            STATS[stat] += 1
    for pid in structs:
        a = getattr(src, pid, None)
        if a is None or isinstance(a, bpy.types.ID):
            continue
        b = getattr(dst, pid)
        if pid == "color_ramp":
            _sync_ramp(a, b)
        elif pid == "mapping" and hasattr(a, "curves"):
            _sync_curves(a, b)
        else:
            _sync_props(a, b, (), stat)


def _sync_idprops(src, dst):
    sk = set(src.keys())
    for k in list(dst.keys()):
        if k not in sk:
            del dst[k]
    for k in src.keys():
        a = _pyval(src[k])
        if k not in dst or _pyval(dst[k]) != a:
            dst[k] = a


def _sync_ramp(a, b):
    for pid in ("color_mode", "hue_interpolation", "interpolation"):
        if getattr(a, pid) != getattr(b, pid):
            setattr(b, pid, getattr(a, pid))
    ea, eb = a.elements, b.elements
    while len(eb) > len(ea):
        eb.remove(eb[-1])
    while len(eb) < len(ea):
        eb.new(1.0)
    for _ in range(4):
        if all(eb[i].position == ea[i].position for i in range(len(ea))):
            break
        for i in range(len(ea)):
            if eb[i].position != ea[i].position:
                eb[i].position = ea[i].position
    for i in range(len(ea)):
        if tuple(eb[i].color) != tuple(ea[i].color):
            eb[i].color = ea[i].color
        if eb[i].position != ea[i].position:
            raise ReplayError("color ramp positions did not settle")


def _sync_curves(a, b):
    changed = False
    for pid in ("use_clip", "clip_min_x", "clip_min_y", "clip_max_x", "clip_max_y", "extend", "tone"):
        if hasattr(a, pid) and getattr(a, pid) != getattr(b, pid):
            setattr(b, pid, getattr(a, pid))
            changed = True
    for pid in ("black_level", "white_level"):
        if hasattr(a, pid) and tuple(getattr(a, pid)) != tuple(getattr(b, pid)):
            setattr(b, pid, getattr(a, pid))
            changed = True
    for ca, cb in zip(a.curves, b.curves):
        pa, pb = ca.points, cb.points
        while len(pb) > len(pa):
            pb.remove(pb[-1])
        while len(pb) < len(pa):
            pb.new(1.0, 1.0)
        for i in range(len(pa)):
            if tuple(pb[i].location) != tuple(pa[i].location):
                pb[i].location = pa[i].location
                changed = True
            if pb[i].handle_type != pa[i].handle_type:
                pb[i].handle_type = pa[i].handle_type
                changed = True
    if changed:
        b.update()


def _sync_node(t, r):
    _sync_props(t, r, _NODE_SKIP, "prop_writes")
    for out in (False, True):
        ct = t.outputs if out else t.inputs
        cr = r.outputs if out else r.inputs
        if len(ct) != len(cr):
            raise ReplayError("%s %s socket count %d != %d" % (r.name, "out" if out else "in", len(ct), len(cr)))
        for st, sr in zip(ct, cr):
            if st.identifier != sr.identifier or st.bl_idname != sr.bl_idname:
                raise ReplayError("%s socket %s != %s" % (r.name, st.identifier, sr.identifier))
            _sync_props(st, sr, _SOCK_SKIP, "sock_writes")
    _sync_idprops(t, r)


# --------------------------------------------------------------------------- registry

_ACTIVE = {}  # real tree pointer -> Replay
_STASH = {}  # key -> (material copy, log)
STASH_ON = [False]
# One flat graph shared by every mesh of an import: a material with no stash of its own
# starts from the last committed graph of a similar shape instead of an empty tree.
SHARE_ON = [False]
_SHARED = [None]  # (material copy, log)
_SHARE_REPLACE = 0.25  # share of nodes created fresh that makes the newer graph the shared one


def _share() -> bool:
    return SHARE_ON[0] and os.environ.get("DR_CW_SHARE", "1") != "0"


def begin(mat, stash_key=None):
    """Replay for mat's tree; returns (nodes, links) proxies, or the real ones when off."""
    tree = mat.node_tree
    if not enabled():
        return tree.nodes, tree.links
    src = _FROM.pop(tree.as_pointer(), None)
    rp = Replay(tree, (src[1], src[0]) if src is not None and src[1] else None)
    rp.key = stash_key
    rp.mat = mat
    _ACTIVE[rp.real_ptr] = rp
    return rp.nodes, rp.links_proxy


def replay_of(nodes):
    rp = getattr(nodes, "_rp", None) if isinstance(nodes, _Nodes) else None
    return rp


def commit(nodes, links, collapsed=False):
    """Commit the replay behind nodes; returns the real (nodes, links). Stashes when batching.

    collapsed=True: the proxies already hold the collapsed graph (wrapper node and a few
    others). The real tree is emptied first and no flat stash or shared copy is kept.
    """
    rp = replay_of(nodes)
    if rp is None:
        return nodes, links
    if rp.done:
        return rp.real.nodes, rp.real.links
    made0 = STATS["created"]
    if collapsed:
        if len(rp.real.nodes):
            rp.real.nodes.clear()
        rp.stash = None
    try:
        rp.commit()
    except Exception:
        import traceback
        traceback.print_exc()
        rp.abort()
        raise
    key = None if collapsed else getattr(rp, "key", None)
    if _share() and key is not None:
        fresh = STATS["created"] - made0
        if _SHARED[0] is None or fresh > _SHARE_REPLACE * max(1, len(rp.log)):
            if _SHARED[0] is not None:
                _free_mat(_SHARED[0][0])
            cp = rp.mat.copy()
            cp.name = "~DR.CwShared"
            cp.use_fake_user = False
            _SHARED[0] = (cp, rp.log)
    if STASH_ON[0] and key is not None:
        old = _STASH.pop(key, None)
        if old is not None:
            _free_mat(old[0])
        mat = rp.mat
        cp = mat.copy()
        cp.name = "~DR.CwStash"
        cp.use_fake_user = False
        _STASH[key] = (cp, rp.log)
    return rp.real.nodes, rp.real.links


def abort_for(tree):
    try:
        rp = _ACTIVE.get(tree.as_pointer())
    except ReferenceError:
        return
    if rp is not None:
        rp.abort()


def key_for(obj, mat, psk_path):
    """Stash key: same mesh file and material slot. Speed only; any key stays exact."""
    if not enabled():
        return None
    slot = -1
    for i, ms in enumerate(getattr(obj, "material_slots", ())):
        if ms.material == mat:
            slot = i
            break
    return (os.path.normcase(os.path.abspath(str(psk_path or getattr(obj, "name", "")))), slot)


def material_from_stash(stash_key, fresh):
    """Replace the just-made material fresh by a copy of the stash, reset to fresh's state.

    Returns the copy (users of fresh now point at it; the caller removes fresh), or None.
    """
    if not enabled() or stash_key is None:
        return None
    st = _STASH.get(stash_key)
    shared = st is None and _share()
    if shared:
        st = _SHARED[0]
    if st is None:
        return None
    src = st[0]
    try:
        src.name
    except ReferenceError:
        if shared:
            _SHARED[0] = None
        else:
            _STASH.pop(stash_key, None)
        return None
    mat = src.copy()
    try:
        _sync_props(fresh, mat, _MAT_SKIP, "prop_writes")
        _sync_idprops(fresh, mat)
        for k in list(mat.node_tree.keys()):
            del mat.node_tree[k]
        want = fresh.name
        fresh.name = want + "~drcw_old"
        mat.name = want
        fresh.user_remap(mat)
    except Exception:
        bpy.data.materials.remove(mat)
        raise
    _FROM[mat.node_tree.as_pointer()] = (src.name, st[1])
    return mat


_FROM = {}  # real tree pointer -> (stash material name it was copied from, its log)


_MAT_SKIP = frozenset(("rna_type", "name", "name_full", "node_tree", "use_nodes", "original",
                       "users", "use_fake_user", "use_extra_user", "is_embedded_data",
                       "is_evaluated", "is_library_indirect", "library", "library_weak_reference",
                       "override_library", "preview", "tag", "is_missing", "is_runtime_data",
                       "asset_data", "session_uid", "id_type", "paint_active_slot",
                       "texture_paint_images", "texture_paint_slots", "grease_pencil",
                       "lineart", "animation_data", "id_properties_ensure", "is_editmode",
                       "library_hint", "is_grease_pencil"))


def _free_mat(m):
    try:
        bpy.data.materials.remove(m)
    except (ReferenceError, RuntimeError):
        pass


def clear_stashes():
    for cp, _log in list(_STASH.values()):
        _free_mat(cp)
    _STASH.clear()
    if _SHARED[0] is not None:
        _free_mat(_SHARED[0][0])
        _SHARED[0] = None


def clear_all():
    for rp in list(_ACTIVE.values()):
        rp.abort()
    _ACTIVE.clear()
    clear_stashes()
