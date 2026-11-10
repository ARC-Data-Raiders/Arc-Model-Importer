"""Zone pick tool for the Asset Shader Control sidebar section.

Raycast -> object + face -> UV -> zone mask sample -> zone index/name.
Mask location lives in :func:`find_zone_mask` only; graph changes go there.
"""
from __future__ import annotations

import math
import time

import bpy
import numpy as np
from mathutils import Vector
from mathutils.interpolate import poly_3d_calc

try:
    from .materials import wrapper_tree
except ImportError:     # loaded standalone (tests): take the file itself
    import importlib.util as _iu
    import os as _os
    _spec = _iu.spec_from_file_location("arc_wrapper_tree", _os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)), "materials", "wrapper_tree.py"))
    wrapper_tree = _iu.module_from_spec(_spec)
    _spec.loader.exec_module(wrapper_tree)

OUTFIT_ZONE_NAMES = {i: f"Colour {i}" for i in range(1, 9)}
WEAPON_ZONE_NAMES = {1: "Red", 2: "Green", 3: "Blue", 4: "Alpha"}

_IMAGE_CACHE: dict = {}   # image name -> (pointer, w, h, float32 (h, w, 4))
_MASK_CACHE: dict = {}    # material name -> MaskInfo | None
_FACE_ZONE_CACHE: dict = {}  # (obj name, npoly) -> int16 per-polygon zone (-1 = none)
_BATCH_CACHE: dict = {}   # (obj name, npoly, zone) -> gpu batch | None


class MaskInfo:
    __slots__ = ("image", "kind", "uv_map", "material")

    def __init__(self, image, kind, uv_map, material):
        self.image, self.kind, self.uv_map, self.material = image, kind, uv_map, material

    def zone_name(self, zone):
        names = OUTFIT_ZONE_NAMES if self.kind == "outfit" else WEAPON_ZONE_NAMES
        return names.get(int(zone), f"Zone {int(zone)}")


def clear_caches():
    for c in (_IMAGE_CACHE, _MASK_CACHE, _FACE_ZONE_CACHE, _BATCH_CACHE):
        c.clear()


# ---------------------------------------------------------------- mask locate
def _upstream_image(tree, sock, depth=0, seen=None):
    """First TEX_IMAGE feeding ``sock`` (walks reroutes, groups, any node)."""
    if seen is None:
        seen = set()
    if depth > 24 or not sock.is_linked:
        return None
    for link in sock.links:
        nd = link.from_node
        if nd.as_pointer() in seen:
            continue
        seen.add(nd.as_pointer())
        if nd.type == "TEX_IMAGE" and nd.image is not None:
            return nd
        if nd.type == "GROUP" and nd.node_tree is not None:
            # Look inside the group: output socket -> group output node input.
            sub = nd.node_tree
            for out in sub.nodes:
                if out.type == "GROUP_OUTPUT":
                    s = out.inputs.get(link.from_socket.name)
                    if s is not None:
                        hit = _upstream_image(sub, s, depth + 1, seen)
                        if hit is not None:
                            return hit
        if nd.type == "GROUP_INPUT":
            continue
        for s in nd.inputs:
            hit = _upstream_image(tree, s, depth + 1, seen)
            if hit is not None:
                return hit
    return None


def _uv_map_of(img_node):
    vec = img_node.inputs.get("Vector")
    if vec is None or not vec.is_linked:
        return None
    nd = vec.links[0].from_node
    for _ in range(8):
        if nd.type == "UVMAP":
            return nd.uv_map or None
        ins = [s for s in nd.inputs if s.is_linked]
        if not ins:
            return None
        nd = ins[0].links[0].from_node
    return None


def find_zone_mask(material):
    """Locate the zone mask image in ``material``. Returns MaskInfo or None.

    Outfit GT graph: ``GT_mid_x8`` (mid*8 -> +0.5 -> floor) input 0 traces back
    to the OCM texture (zone = floor(B*8+0.5)). Weapon graph: group node input
    "Weapon ID" traces to the ID texture (zone = dominant R/G/B/A channel).
    """
    if material is None or not material.use_nodes or material.node_tree is None:
        return None
    key = material.name
    if key in _MASK_CACHE:
        return _MASK_CACHE[key]
    tree = wrapper_tree.graph_tree(material.node_tree)   # setup nodes live in the DR Outfit wrapper
    info = None
    nodes = list(tree.nodes)
    # Outfit: search top level and one group level for GT_mid_x8.
    stack = [(tree, nodes)]
    for nd in nodes:
        if nd.type == "GROUP" and nd.node_tree is not None:
            stack.append((nd.node_tree, list(nd.node_tree.nodes)))
    for t, ns in stack:
        for nd in ns:
            if (nd.name.startswith("GT_mid_x8") and not nd.name.startswith("GT_mid_x8_half")) or nd.name == "DR.LayerInputs.mid.x8" or nd.get("dr.legacy_name") == "GT_mid_x8":
                img = _upstream_image(t, nd.inputs[0])
                if img is None and t is not tree:
                    # mask comes in through the group interface: resolve at top level
                    for gn in nodes:
                        if gn.type == "GROUP" and gn.node_tree is t:
                            for s in gn.inputs:
                                img = _upstream_image(tree, s)
                                if img is not None:
                                    break
                if img is not None:
                    info = MaskInfo(img.image, "outfit", _uv_map_of(img), material)
                    break
        if info:
            break
    if info is None:
        for nd in nodes:
            s = nd.inputs.get("Weapon ID") if hasattr(nd, "inputs") else None
            if s is not None and s.is_linked:
                img = _upstream_image(tree, s)
                if img is not None:
                    info = MaskInfo(img.image, "weapon", _uv_map_of(img), material)
                    break
    if info is None:
        # Independent weapon graph: the "_ID" texture sits inside the weapon group (no "Weapon ID" socket).
        try:
            from .materials.user_controls import weapon_id_node
        except ImportError:             # loaded standalone (tests)
            weapon_id_node = lambda _t: None    # noqa: E731
        for t, _ns in stack:
            img = weapon_id_node(t)
            if img is not None:
                info = MaskInfo(img.image, "weapon", _uv_map_of(img), material)
                break
    _MASK_CACHE[key] = info
    return info


# ---------------------------------------------------------------- sampling
def image_pixels(image):
    """Cached float32 (h, w, 4) pixel array for ``image``."""
    ent = _IMAGE_CACHE.get(image.name)
    if ent is not None and ent[0] == image.as_pointer():
        return ent[3]
    w, h = image.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(buf)
    arr = buf.reshape(h, w, 4)
    _IMAGE_CACHE[image.name] = (image.as_pointer(), w, h, arr)
    return arr


def zones_from_pixels(px, kind):
    """px: (..., 4) -> int zone array. Outfit 1..8, weapon 1..4 (0 = none)."""
    if kind == "outfit":
        return np.floor(px[..., 2] * 8.0 + 0.5).astype(np.int16)
    m = px[..., :4]
    z = np.argmax(m, axis=-1).astype(np.int16) + 1
    z[np.max(m, axis=-1) < 0.5] = 0
    return z


def sample_zones(info, uv):
    """uv: (N, 2) -> (N,) zones, repeat wrap, nearest texel."""
    px = image_pixels(info.image)
    h, w = px.shape[:2]
    uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
    x = (np.floor((uv[:, 0] % 1.0) * w).astype(np.int64)) % w
    y = (np.floor((uv[:, 1] % 1.0) * h).astype(np.int64)) % h
    return zones_from_pixels(px[y, x], info.kind)


def _uv_layer(mesh, info):
    if info is not None and info.uv_map and info.uv_map in mesh.uv_layers:
        return mesh.uv_layers[info.uv_map]
    return mesh.uv_layers.active


def get_zone_at(obj, hit):
    """hit: dict with ``face_index`` (evaluated mesh polygon) and ``location``
    (world). Returns (material, zone_index, zone_name) or (material|None, None, None).
    """
    dg = bpy.context.evaluated_depsgraph_get()
    oe = obj.evaluated_get(dg)
    me = oe.data
    fi = int(hit["face_index"])
    if fi < 0 or fi >= len(me.polygons):
        return None, None, None
    poly = me.polygons[fi]
    slot = poly.material_index
    mat = obj.material_slots[slot].material if slot < len(obj.material_slots) else None
    info = find_zone_mask(mat)
    if info is None:
        return mat, None, None
    uvl = _uv_layer(me, info)
    if uvl is None:
        return mat, None, None
    local = oe.matrix_world.inverted() @ Vector(hit["location"])
    vs = me.vertices
    cos = [vs[i].co for i in poly.vertices]
    wts = poly_3d_calc(cos, local)
    u = v = 0.0
    for wgt, li in zip(wts, poly.loop_indices):
        uv = uvl.uv[li].vector
        u += wgt * uv[0]
        v += wgt * uv[1]
    z = int(sample_zones(info, [(u, v)])[0])
    return mat, z, info.zone_name(z)


# ---------------------------------------------------------------- face zones
def face_zone_map(obj):
    """Per-polygon majority zone of the evaluated mesh (corners + 2x centroid)."""
    dg = bpy.context.evaluated_depsgraph_get()
    me = obj.evaluated_get(dg).data
    n = len(me.polygons)
    key = (obj.name, n)
    got = _FACE_ZONE_CACHE.get(key)
    if got is not None:
        return got
    out = np.full(n, -1, dtype=np.int16)
    if n == 0:
        _FACE_ZONE_CACHE[key] = out
        return out
    mi = np.empty(n, np.int32); me.polygons.foreach_get("material_index", mi)
    ls = np.empty(n, np.int32); me.polygons.foreach_get("loop_start", ls)
    lt = np.empty(n, np.int32); me.polygons.foreach_get("loop_total", lt)
    nl = len(me.loops)
    loop_poly = np.repeat(np.arange(n), lt)
    for slot_i, slot in enumerate(obj.material_slots):
        info = find_zone_mask(slot.material)
        if info is None:
            continue
        uvl = _uv_layer(me, info)
        if uvl is None:
            continue
        uv = np.empty(nl * 2, np.float32); uvl.uv.foreach_get("vector", uv)
        uv = uv.reshape(nl, 2)
        sel = mi == slot_i
        if not sel.any():
            continue
        lsel = sel[loop_poly]
        lz = sample_zones(info, uv[lsel])
        lp = loop_poly[lsel]
        cen = np.add.reduceat(uv, ls, axis=0)[sel] / lt[sel, None] if n else uv[:0]
        cz = sample_zones(info, cen)
        polys = np.nonzero(sel)[0]
        counts = np.zeros((n, 16), np.int32)
        np.add.at(counts, (lp, np.clip(lz, 0, 15)), 1)
        np.add.at(counts, (polys, np.clip(cz, 0, 15)), 2)
        out[polys] = np.argmax(counts[polys], axis=1)
    _FACE_ZONE_CACHE[key] = out
    return out


def zone_triangles(obj, zone, material=None):
    """Local-space triangle vertex array (T*3, 3) for polygons in ``zone``
    (optionally restricted to ``material``'s slot)."""
    dg = bpy.context.evaluated_depsgraph_get()
    me = obj.evaluated_get(dg).data
    fz = face_zone_map(obj)
    keep = fz == zone
    if material is not None:
        mi = np.empty(len(me.polygons), np.int32); me.polygons.foreach_get("material_index", mi)
        idx = [i for i, s in enumerate(obj.material_slots) if s.material == material]
        keep &= np.isin(mi, idx)
    me.calc_loop_triangles()
    nt = len(me.loop_triangles)
    tp = np.empty(nt, np.int32); me.loop_triangles.foreach_get("polygon_index", tp)
    tv = np.empty(nt * 3, np.int32); me.loop_triangles.foreach_get("vertices", tv)
    co = np.empty(len(me.vertices) * 3, np.float32); me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    tv = tv.reshape(nt, 3)[keep[tp]]
    return co[tv.ravel()]


def zone_batch(obj, zone, material=None):
    """Cached gpu batch of the zone's triangles (None when gpu unavailable)."""
    n = len(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.polygons)
    key = (obj.name, n, zone, material.name if material else None)
    if key in _BATCH_CACHE:
        return _BATCH_CACHE[key]
    pos = zone_triangles(obj, zone, material)
    batch = None
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        sh = gpu.shader.from_builtin("UNIFORM_COLOR")
        batch = batch_for_shader(sh, "TRIS", {"pos": pos})
    except Exception:
        batch = None
    _BATCH_CACHE[key] = batch if batch is not None else pos
    return _BATCH_CACHE[key]


# ---------------------------------------------------------------- colour hook
# Legacy weapon group inputs (materials/weapon.py:449-452); compiled
# "Independent" groups bake MI "Base - Color - <zone>" as constants.
WEAPON_ZONE_PARAMS = ("Base Color Red", "Base Color Green", "Base Color Blue", "Base Color Alpha")


def zone_param(zone_index, kind="outfit"):
    if isinstance(zone_index, bool) or not isinstance(zone_index, int):
        return None
    if kind == "weapon":
        if 0 <= zone_index < len(WEAPON_ZONE_PARAMS):
            return WEAPON_ZONE_PARAMS[zone_index]
        return None
    if 0 <= zone_index <= 7:
        return f"Eq{zone_index}"
    return None


def detect_kind(tree):
    """'weapon' or 'outfit' from a node group (or material) tree."""
    if tree is None:
        return "outfit"
    try:
        if tree.get("arc_gt_ver") is not None:
            return "outfit"
    except (AttributeError, TypeError):
        pass
    name = str(getattr(tree, "name", "") or "").lower()
    if name.startswith("independent") or "weapon" in name:
        return "weapon"
    return "outfit"


def _kind_of(material_name):
    try:
        return material_kind(bpy.data.materials.get(material_name))
    except AttributeError:
        return "outfit"


def material_kind(material):
    tree = getattr(material, "node_tree", None)
    for node in getattr(tree, "nodes", ()) or ():
        group = getattr(node, "node_tree", None)
        if group is not None and detect_kind(group) == "weapon":
            return "weapon"
    return "outfit"


def _control_ops():
    try:
        from . import shader_control_ops
    except ImportError:
        from Shared import shader_control_ops
    return shader_control_ops


def _label_for(name):
    try:
        from .materials import param_labels
    except ImportError:
        try:
            import importlib.util as iu
            import os
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Shared", "materials", "param_labels.py")
            spec = iu.spec_from_file_location("arc_param_labels", path)
            param_labels = iu.module_from_spec(spec)
            spec.loader.exec_module(param_labels)
        except (ImportError, OSError):
            return name
    return param_labels.label_for(name)[0]


def zone_label(zone, kind="outfit"):
    name = zone_param(int(zone) - 1, kind)
    if kind == "weapon" and name is not None:
        return name
    if name is None:
        return f"Zone {int(zone)}"
    return _label_for(name)


def write_zone_colour(context, zone, rgb, kind="outfit"):
    name = zone_param(int(zone) - 1, kind)
    if name is None:
        return False
    value = tuple(float(c) for c in rgb)[:3] + (1.0,)
    return bool(_control_ops().set_param(context, name, value))


def set_zone_colour(material_or_outfit, zone, rgb, per_part):
    if SET_ZONE_COLOUR_HOOK is not None:
        return SET_ZONE_COLOUR_HOOK(material_or_outfit, zone, rgb, per_part)
    return write_zone_colour(bpy.context, zone, rgb, material_kind(material_or_outfit))


SET_ZONE_COLOUR_HOOK = None   # callable(material_or_outfit, zone, rgb, per_part)
ON_ZONE_PICKED = None         # callable(obj, material, zone, zone_name)


# ---------------------------------------------------------------- operators
def _ray_hit(context, event):
    from bpy_extras import view3d_utils
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None:
        return None
    co = (event.mouse_region_x, event.mouse_region_y)
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, co)
    vec = view3d_utils.region_2d_to_vector_3d(region, rv3d, co)
    dg = context.evaluated_depsgraph_get()
    ok, loc, fi, ob = cast_pick_target(context.scene, dg, origin, vec)
    if not ok:
        return None
    return ob, {"face_index": fi, "location": loc}


def is_pick_target(ob):
    """Imported game mesh (flag arc_model_type), not a reference/helper plane."""
    return (ob is not None and ob.type == "MESH" and "arc_model_type" in ob
            and "arc_reference_plane" not in ob and "arc_reference_role" not in ob)


def cast_pick_target(scene, dg, origin, vec, max_skips=16):
    """scene.ray_cast that passes through non-target objects (helpers, default cube)."""
    o = Vector(origin); v = Vector(vec).normalized()
    for _ in range(max_skips):
        ok, loc, _n, fi, ob, _m = scene.ray_cast(dg, o, v)
        if not ok:
            return False, None, -1, None
        ob = ob.original if hasattr(ob, "original") else ob
        if is_pick_target(ob):
            return True, loc, fi, ob
        o = loc + v * 1e-4
    return False, None, -1, None


class ARC_OT_set_zone_colour(bpy.types.Operator):
    bl_idname = "arc.set_zone_colour"
    bl_label = "Zone Colour"
    bl_options = {"REGISTER", "UNDO"}

    colour: bpy.props.FloatVectorProperty(name="Colour", subtype="COLOR_GAMMA", size=3, min=0.0, max=1.0, default=(1.0, 1.0, 1.0))
    material: bpy.props.StringProperty()
    zone: bpy.props.IntProperty()
    def execute(self, context):
        if SET_ZONE_COLOUR_HOOK is not None:
            mat = bpy.data.materials.get(self.material)
            per_part = getattr(context.scene, "arc_shader_scope", "WHOLE") != "WHOLE"
            SET_ZONE_COLOUR_HOOK(mat, self.zone, tuple(self.colour), per_part)
            return {"FINISHED"}
        kind = _kind_of(self.material)
        if not write_zone_colour(context, self.zone, tuple(self.colour), kind):
            self.report({"WARNING"}, f"No shader control for {zone_label(self.zone, kind)}")
            return {"CANCELLED"}
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_popup(self, event)

    def draw(self, context):
        self.layout.label(text=f"{zone_label(self.zone, _kind_of(self.material))}  ({self.material})")
        self.layout.prop(self, "colour")
        if hasattr(context.scene, "arc_shader_scope"):
            self.layout.prop(context.scene, "arc_shader_scope", expand=True)


HOVER_COLOUR = (1.0, 0.22, 0.0, 1.0)
HOVER_COLOUR_ALT = (0.0, 0.75, 1.0, 1.0)     # used when the zone is already close to HOVER_COLOUR
HOVER_TINT = {"arc_uc_tint_h": 0.06, "arc_uc_tint_a": 0.85}


class HoverColour:
    """Temporary colour change on what the pick would select. Nothing is stored as a tweak;
    clear() puts every socket back to the value it had."""

    def __init__(self):
        self.key = None
        self.saved = []

    @staticmethod
    def _read(sock):
        value = sock.default_value
        return tuple(value) if hasattr(value, "__len__") else float(value)

    def show(self, key, writes):
        """*writes*: [(socket, value)]. Same key as before: nothing to do."""
        if key == self.key:
            return len(self.saved)
        self.clear()
        for sock, value in writes:
            try:
                old = self._read(sock)
                sock.default_value = value
                self.saved.append((sock, old))
            except (AttributeError, TypeError, ValueError, ReferenceError):
                continue
        self.key = key
        if self.saved:
            _control_ops().mark_self_edit()
        return len(self.saved)

    def clear(self):
        for sock, old in reversed(self.saved):
            try:
                sock.default_value = old
            except (AttributeError, TypeError, ValueError, ReferenceError):
                continue
        if self.saved:
            _control_ops().mark_self_edit()
        self.saved = []
        self.key = None


def _far(a, b):
    return sum(abs(float(x) - float(y)) for x, y in zip(a[:3], b[:3])) > 0.6


def hover_writes(context, obj, material, zone):
    """(key, [(socket, value)]) for the current selection mode, or (None, []) when nothing can be shown.

    Zone/Decal: the hovered colour zone on the whole asset. Part: a tint on the hovered part.
    Whole Outfit: a tint on every part of the hovered asset."""
    sco = _control_ops()
    mode = sco.select_mode(context.scene)
    root = sco.asset_root(obj)
    if mode == "ZONE":
        if not zone:
            return None, []
        info = find_zone_mask(material)
        name = zone_param(int(zone) - 1, info.kind if info else "outfit")
        if name is None:
            return None, []
        writes = []
        for sock in sco.param_sockets(sco._objects(obj, True), name):
            if sock.type != "RGBA":
                continue
            writes.append((sock, HOVER_COLOUR if _far(sock.default_value, HOVER_COLOUR) else HOVER_COLOUR_ALT))
        return ("ZONE", root.name, name), writes
    objects = [obj] if mode == "PART" else sco._objects(obj, True)
    from .materials import user_controls
    writes = []
    for mat in sco._materials(objects):
        for prop, sock in user_controls.material_targets(mat):
            if prop in HOVER_TINT:
                writes.append((sock, HOVER_TINT[prop]))
    return (mode, obj.name if mode == "PART" else root.name), writes


def pick_commit(context, obj, hit, cur=None):
    """Click core (no event): select the part and, on the Zone tab, its zone and decal.
    hit = {"face_index", "location"}. Returns (material, zone, decal)."""
    if cur is None:
        mat, z, name = get_zone_at(obj, hit)
    else:
        _o, mat, z, name = cur
    if mat is None:
        return None, None, 0
    wm = context.window_manager
    if hasattr(wm, "arc_picked_zone") and z is not None:
        wm.arc_picked_zone = z
        wm.arc_picked_zone_material = mat.name
    if ON_ZONE_PICKED is not None and z is not None:
        ON_ZONE_PICKED(obj, mat, z, name)
    decal = 0
    try:
        handle = decal_at(obj, hit)[0] if hit is not None else None
        decal = int(handle.idx) if handle is not None else 0
    except Exception as exc:
        print(f"Arc Raiders: decal pick failed: {exc}")
    info = find_zone_mask(mat)
    _control_ops().select_from_pick(context, obj, mat, z or 0, info.kind if info else "", decal)
    return mat, z, decal


class ARC_OT_pick_shader_zone(bpy.types.Operator):
    """Hover changes the colour of what a click would select (zone, part or whole outfit); click selects it"""
    bl_idname = "arc.pick_shader_zone"
    bl_label = "Pick"

    _handle = None

    def _update(self, context, event):
        hit = _ray_hit(context, event)
        cur = None
        zone_mode = _control_ops().select_mode(context.scene) == "ZONE"
        if hit is not None:
            obj, h = hit
            mat, z, name = get_zone_at(obj, h)
            if z is not None or (not zone_mode and mat is not None):
                cur = (obj, mat, z, name)
            if z is not None and zone_mode:
                zone_batch(obj, z, mat)
        self._cur = cur
        self._hit = hit[1] if (hit is not None and cur) else None
        key, writes = hover_writes(context, cur[0], cur[1], cur[2]) if cur else (None, [])
        self._shown = self._hover.show(key, writes) if key is not None else (self._hover.clear() or 0)
        what = ""
        if cur:
            what = f"Zone: {cur[3]}  ({cur[1].name})" if zone_mode else f"Part: {cur[0].name}"
        context.area.header_text_set(
            f"{what}  |  Click select, Esc/RMB exit" if cur else
            "Pick: hover a part  |  Esc/RMB exit")
        context.area.tag_redraw()

    def _draw(self, context):
        cur = getattr(self, "_cur", None)
        if not cur or cur[2] is None or getattr(self, "_shown", 0):
            return      # the colour change on the material already shows the selection
        obj, mat, z, _ = cur
        b = zone_batch(obj, z, mat)
        if b is None or isinstance(b, np.ndarray):
            return
        import gpu
        sh = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.matrix.push()
        gpu.matrix.multiply_matrix(obj.evaluated_get(context.evaluated_depsgraph_get()).matrix_world)
        sh.uniform_float("color", (1.0, 0.55, 0.1, 0.35))
        b.draw(sh)
        gpu.matrix.pop()
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("NONE")

    def _finish(self, context):
        self._hover.clear()
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, "WINDOW")
            self._handle = None
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._finish(context)
            return {"CANCELLED"}
        if event.type == "MOUSEMOVE":
            self._update(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            self._update(context, event)
            cur, hit = self._cur, self._hit
            self._hover.clear()             # the selection must see the real colours
            if cur:
                pick_commit(context, cur[0], hit, cur)
            self._finish(context)
            if cur:
                bpy.ops.ed.undo_push(message="Pick")
            return {"FINISHED"}
        return {"PASS_THROUGH"}

    def invoke(self, context, event):
        if context.area is None or context.area.type != "VIEW_3D":
            self.report({"WARNING"}, "Pick zone needs a 3D viewport")
            return {"CANCELLED"}
        self._cur = None
        self._hit = None
        self._shown = 0
        self._hover = HoverColour()
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw, (context,), "WINDOW", "POST_VIEW")
        context.window_manager.modal_handler_add(self)
        self._update(context, event)
        return {"RUNNING_MODAL"}


# ---------------------------------------------------------------- decal drag
# Isolated graph adapter. Graph inputs it needs (see integration.txt):
#   Mapping nodes tagged nd["dr.decal_slot"]=idx, nd["dr.decal_role"] in rot/place/flip;
#   TexImage tagged role "color" (its Vector input ends the UV chain);
#   value node "P:{idx}_UseDecal" (opacity; multiplies slot alpha).
DECAL_UNDO_PUSH = None   # callable(message); default bpy.ops.ed.undo_push


def _map_apply(nd, uv):
    """Blender Mapping node on a 2D point (z = 0)."""
    loc = Vector(nd.inputs["Location"].default_value) if "Location" in nd.inputs else Vector()
    rz = nd.inputs["Rotation"].default_value[2] if "Rotation" in nd.inputs else 0.0
    sc = Vector(nd.inputs["Scale"].default_value) if "Scale" in nd.inputs else Vector((1, 1, 1))
    x, y = uv
    c, s = math.cos(rz), math.sin(rz)
    t = nd.vector_type
    if t == "POINT":
        x, y = x * sc.x, y * sc.y
        return (c * x - s * y + loc.x, s * x + c * y + loc.y)
    if t == "TEXTURE":
        x, y = x - loc.x, y - loc.y
        x, y = c * x + s * y, -s * x + c * y
        return (x / (sc.x or 1e-12), y / (sc.y or 1e-12))
    if t == "VECTOR":
        x, y = x * sc.x, y * sc.y
        return (c * x - s * y, s * x + c * y)
    return (x, y)


class DecalHandle:
    """One decal slot. offset = place Location xy; rotation = rot Rotation z;
    scale = place Scale x (uniform); opacity = UseDecal; mode UV|PROJECTED."""

    def __init__(self, material, idx, roles, aspect=1.0):
        self.material, self.idx, self.roles, self.aspect = material, idx, roles, aspect
        self._use = wrapper_tree.find_node(material, f"P:{idx}_UseDecal")
        self.chain = []   # Mapping nodes upstream of the colour texture, applied first..last
        col = roles.get("color")
        sock = col.inputs["Vector"] if col is not None else None
        while sock is not None and sock.is_linked:
            nd = sock.links[0].from_node
            if nd.type != "MAPPING":
                break
            self.chain.insert(0, nd)
            sock = nd.inputs["Vector"]
        self._place = roles.get("place") or (self.chain[-1] if self.chain else None)
        self._rot = roles.get("rot") or self._place

    # values
    def get_offset(self):
        v = self._place.inputs["Location"].default_value
        return (v[0], v[1])

    def set_offset(self, xy):
        v = self._place.inputs["Location"].default_value
        self._place.inputs["Location"].default_value = (xy[0], xy[1], v[2])

    def get_rotation(self):
        return self._rot.inputs["Rotation"].default_value[2]

    def set_rotation(self, r):
        v = self._rot.inputs["Rotation"].default_value
        self._rot.inputs["Rotation"].default_value = (v[0], v[1], r)

    def get_scale(self):
        return self._place.inputs["Scale"].default_value[0]

    def set_scale(self, s):
        v = self._place.inputs["Scale"].default_value
        r = v[1] / v[0] if v[0] else 1.0
        self._place.inputs["Scale"].default_value = (s, s * r, v[2])

    def get_opacity(self):
        return self._use.outputs[0].default_value if self._use else 1.0

    def set_opacity(self, a):
        if self._use:
            self._use.outputs[0].default_value = max(0.0, min(1.0, a))

    def get_mode(self):
        return self.material.get(f"dr.decal{self.idx}.mode", "UV")

    def set_mode(self, mode):
        first = self.chain[0] if self.chain else None
        if first is None:
            return
        nt = first.id_data      # the level that owns the decal chain (material tree or wrapper)
        name = f"DR_DecalProj{self.idx}"
        tc = nt.nodes.get(name)
        if mode == "PROJECTED":
            if tc is None:
                tc = nt.nodes.new("ShaderNodeTexCoord"); tc.name = name
            if tc.object is None:
                emp = bpy.data.objects.new(f"DecalProjector{self.idx}", None)
                emp.empty_display_type = "CUBE"
                bpy.context.scene.collection.objects.link(emp)
                tc.object = emp
            src = first.inputs["Vector"].links[0].from_socket if first.inputs["Vector"].is_linked else None
            if src is not None and src.node != tc:
                self.material[f"dr.decal{self.idx}.uv_src"] = src.node.name + "|" + src.identifier
            nt.links.new(tc.outputs["Object"], first.inputs["Vector"])
        else:
            ref = self.material.get(f"dr.decal{self.idx}.uv_src")
            if ref:
                nn, ident = ref.split("|", 1)
                nd = nt.nodes.get(nn)
                out = next((o for o in nd.outputs if o.identifier == ident), None) if nd else None
                if out is not None:
                    nt.links.new(out, first.inputs["Vector"])
        self.material[f"dr.decal{self.idx}.mode"] = mode

    def snapshot(self):
        return {"offset": self.get_offset(), "rotation": self.get_rotation(),
                "scale": tuple(self._place.inputs["Scale"].default_value),
                "opacity": self.get_opacity()}

    def restore(self, snap):
        self.set_offset(snap["offset"]); self.set_rotation(snap["rotation"])
        self._place.inputs["Scale"].default_value = snap["scale"]; self.set_opacity(snap["opacity"])

    # geometry
    def fold(self, uv):
        return (abs(uv[0]) % 1.0, (uv[1] - 0.5) * self.aspect + 0.5)

    def tex_coord(self, mesh_uv):
        p = self.fold(mesh_uv)
        for nd in self.chain:
            p = _map_apply(nd, p)
        return p

    def contains(self, mesh_uv):
        u, v = self.tex_coord(mesh_uv)
        return 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0

    def place_centre(self, mesh_uv):
        """Set offset so the texture centre (0.5,0.5) lands on mesh_uv (affine solve)."""
        L0 = self.get_offset()
        t0 = self.tex_coord(mesh_uv)
        self.set_offset((L0[0] + 1.0, L0[1])); tx = self.tex_coord(mesh_uv)
        self.set_offset((L0[0], L0[1] + 1.0)); ty = self.tex_coord(mesh_uv)
        a, b = tx[0] - t0[0], ty[0] - t0[0]
        c, d = tx[1] - t0[1], ty[1] - t0[1]
        det = a * d - b * c
        if abs(det) < 1e-12:
            self.set_offset(L0); return False
        ru, rv = 0.5 - t0[0], 0.5 - t0[1]
        dx, dy = (d * ru - b * rv) / det, (-c * ru + a * rv) / det
        self.set_offset((L0[0] + dx, L0[1] + dy))
        return True


def _mask_aspect(material):
    info = find_zone_mask(material)
    if info is not None and info.image is not None and info.image.size[0]:
        return info.image.size[1] / info.image.size[0]
    return 1.0


def get_decals(material):
    """Adapter: list of DecalHandle for every tagged decal slot of ``material``."""
    if material is None or not material.use_nodes or material.node_tree is None:
        return []
    slots = {}
    wrapper_tree.own_wrapper(material)
    for nd in (n for t in wrapper_tree.graph_trees(material) for n in t.nodes):
        idx = nd.get("dr.decal_slot")
        if idx is not None:
            slots.setdefault(int(idx), {})[nd.get("dr.decal_role")] = nd
    aspect = _mask_aspect(material) if slots else 1.0
    return [DecalHandle(material, i, r, aspect) for i, r in sorted(slots.items())
            if r.get("color") is not None]


def hit_uv(obj, hit, uv_map=None):
    """(material, uv) at a ray hit on obj (evaluated mesh face)."""
    dg = bpy.context.evaluated_depsgraph_get()
    oe = obj.evaluated_get(dg)
    me = oe.data
    fi = int(hit["face_index"])
    if fi < 0 or fi >= len(me.polygons):
        return None, None
    poly = me.polygons[fi]
    slot = poly.material_index
    mat = obj.material_slots[slot].material if slot < len(obj.material_slots) else None
    uvl = me.uv_layers.get(uv_map) if uv_map else None
    uvl = uvl or me.uv_layers.get("UV0") or me.uv_layers.active
    if uvl is None:
        return mat, None
    local = oe.matrix_world.inverted() @ Vector(hit["location"])
    cos = [me.vertices[i].co for i in poly.vertices]
    wts = poly_3d_calc(cos, local)
    u = v = 0.0
    for wgt, li in zip(wts, poly.loop_indices):
        q = uvl.uv[li].vector
        u += wgt * q[0]; v += wgt * q[1]
    return mat, (u, v)


def decal_at(obj, hit, decals_cache=None):
    """Topmost decal whose footprint covers the hit; returns (handle|None, mat, uv)."""
    mat, uv = hit_uv(obj, hit)
    if uv is None:
        return None, mat, None
    if decals_cache is not None:
        ds = decals_cache.get(mat.name if mat else None)
        if ds is None:
            ds = decals_cache[mat.name if mat else None] = get_decals(mat)
    else:
        ds = get_decals(mat)
    for d in reversed(ds):
        if d.get_opacity() > 0.0 and d.contains(uv):
            return d, mat, uv
    return None, mat, uv


def build_decal_cache(objs):
    """Pre-resolve decal handles for every target material (run at modal invoke)."""
    cache = {}
    for ob in objs:
        if not is_pick_target(ob):
            continue
        for sl in ob.material_slots:
            m = sl.material
            if m is not None and m.name not in cache:
                cache[m.name] = get_decals(m)
    return cache


class DecalDrag:
    """Pure drag controller (headless-testable). One undo push on confirm."""
    ROT_STEP = math.radians(5.0)
    SCALE_STEP = 1.1
    OPACITY_STEP = 0.05

    def __init__(self, handle):
        self.handle = handle
        self.start = handle.snapshot()
        self.done = False

    def move(self, mesh_uv):
        self.handle.place_centre(mesh_uv)

    def wheel(self, direction, ctrl=False, alt=False):
        h = self.handle
        if alt:
            h.set_opacity(h.get_opacity() + direction * self.OPACITY_STEP)
        elif ctrl:
            h.set_scale(h.get_scale() * (self.SCALE_STEP ** direction))
        else:
            h.set_rotation(h.get_rotation() + direction * self.ROT_STEP)

    def confirm(self):
        if self.done:
            return
        self.done = True
        push = DECAL_UNDO_PUSH
        if push is None:
            try:
                bpy.ops.ed.undo_push(message="Drag Decal")
            except Exception:
                pass
        else:
            push("Drag Decal")

    def cancel(self):
        if not self.done:
            self.handle.restore(self.start)
            self.done = True


class ARC_OT_drag_decal(bpy.types.Operator):
    """Grab a decal (or place the active one on empty surface) and drag it"""
    bl_idname = "arc.drag_decal"
    bl_label = "Drag Decal"
    bl_options = {"REGISTER"}

    active_slot: bpy.props.IntProperty(name="Active Decal Slot", default=1)

    def _redraw(self, context):
        if context.area:
            context.area.tag_redraw()

    def invoke(self, context, event):
        if context.area is None or context.area.type != "VIEW_3D":
            return {"CANCELLED"}
        self._drag = None
        self._hover = None
        self._cache = build_decal_cache(context.visible_objects)
        self._obj = None
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Decal: click grab/place, wheel rot, Ctrl scale, Alt opacity, Esc cancel")
        return {"RUNNING_MODAL"}

    def _end(self, context):
        if context.area:
            context.area.header_text_set(None)

    def modal(self, context, event):
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            r = _ray_hit(context, event)
            if r is not None:
                ob, hit = r
                if self._drag is not None:
                    if ob == self._obj:
                        _m, uv = hit_uv(ob, hit)
                        if uv is not None:
                            self._drag.move(uv)
                else:
                    self._hover = decal_at(ob, hit, self._cache)[0]
            self._redraw(context)
            return {"RUNNING_MODAL"}
        if event.type == "WHEELUPMOUSE" or event.type == "WHEELDOWNMOUSE":
            if self._drag is not None:
                self._drag.wheel(1 if event.type == "WHEELUPMOUSE" else -1, event.ctrl, event.alt)
                return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if self._drag is not None:
                self._drag.confirm(); self._end(context); return {"FINISHED"}
            r = _ray_hit(context, event)
            if r is None:
                return {"RUNNING_MODAL"}
            ob, hit = r
            h, mat, uv = decal_at(ob, hit, self._cache)
            if h is None and mat is not None and uv is not None:
                h = next((d for d in get_decals(mat) if d.idx == self.active_slot), None)
            if h is None:
                return {"RUNNING_MODAL"}
            self._obj = ob
            self._drag = DecalDrag(h)
            if uv is not None:
                self._drag.move(uv)
            return {"RUNNING_MODAL"}
        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS" and self._drag:
            self._drag.confirm(); self._end(context); return {"FINISHED"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            if self._drag:
                self._drag.cancel()
            self._end(context)
            return {"CANCELLED"}
        return {"PASS_THROUGH"}


def bench_decal_hover(obj_list, rays, depsgraph=None):
    sc = bpy.context.scene
    dg = depsgraph or bpy.context.evaluated_depsgraph_get()
    t0 = time.perf_counter()
    cache = build_decal_cache(obj_list)
    bench_decal_hover.invoke_ms = (time.perf_counter() - t0) * 1000.0
    times, hits = [], 0
    for o, d in rays:
        t = time.perf_counter()
        ok, loc, fi, ob = cast_pick_target(sc, dg, o, d)
        if ok:
            h = decal_at(ob, {"face_index": fi, "location": loc}, cache)[0]
            hits += h is not None
        times.append((time.perf_counter() - t) * 1000.0)
    return times, hits


CLASSES = (ARC_OT_set_zone_colour, ARC_OT_pick_shader_zone, ARC_OT_drag_decal)


def register():
    for c in CLASSES:
        try:
            bpy.utils.register_class(c)
        except ValueError:
            bpy.utils.unregister_class(c)
            bpy.utils.register_class(c)
    wm = bpy.types.WindowManager
    wm.arc_picked_zone = bpy.props.IntProperty(name="Picked Zone", default=0)
    wm.arc_picked_zone_material = bpy.props.StringProperty(name="Picked Zone Material")


def unregister():
    wm = bpy.types.WindowManager
    for p in ("arc_picked_zone", "arc_picked_zone_material"):
        if hasattr(wm, p):
            delattr(wm, p)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    clear_caches()


def bench_hover(obj_list, rays, depsgraph=None):
    """Headless benchmark helper: rays = [(origin, dir)]; returns per-update ms list."""
    sc = bpy.context.scene
    dg = depsgraph or bpy.context.evaluated_depsgraph_get()
    times = []
    hits = 0
    for o, d in rays:
        t = time.perf_counter()
        ok, loc, fi, ob = cast_pick_target(sc, dg, o, d)
        if ok:
            mat, z, _name = get_zone_at(ob, {"face_index": fi, "location": loc})
            if z is not None:
                hits += 1
                zone_batch(ob, z, mat)
        times.append((time.perf_counter() - t) * 1000.0)
    return times, hits
