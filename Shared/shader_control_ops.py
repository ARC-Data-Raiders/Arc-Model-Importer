import bpy
from bpy.props import BoolProperty, IntProperty, EnumProperty, FloatProperty, FloatVectorProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import shader_tweaks
from . import shader_upgrade
try:
    from .materials import wrapper_tree
except ImportError:     # loaded standalone (tests): take the file itself
    import importlib.util as _iu
    import os as _os
    _spec = _iu.spec_from_file_location("arc_wrapper_tree", _os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)), "materials", "wrapper_tree.py"))
    wrapper_tree = _iu.module_from_spec(_spec)
    _spec.loader.exec_module(wrapper_tree)
from .materials import param_labels
from .materials import user_controls

BASIC_GROUPS = ("Palette", "Wear", "Dirt")
GROUP_ORDER = ("Palette", "Wear", "Dirt", "Surface", "Decal", "Emission", "Normal", "Other")

_STATE = {"updates": 0, "guard": False, "self_edit": False, "synced": None}
_CACHE = {}


def asset_root(obj):
    while obj is not None and obj.parent is not None:
        obj = obj.parent
    return obj


def _nodes(mat):
    """Nodes of every per-material level: the material tree, then the DR Outfit wrapper tree."""
    for tree in wrapper_tree.graph_trees(mat):
        yield from tree.nodes


def _is_control_tree(tree):
    if tree is None:
        return False
    if "arc_gt_ver" in tree:
        return True
    return tree.name.startswith("DR.") or tree.name.startswith("Independent")


def _interface_default(tree, name):
    for item in tree.interface.items_tree:
        if getattr(item, "item_type", "") == "SOCKET" and item.in_out == "INPUT" and item.name == name:
            value = getattr(item, "default_value", None)
            if value is None:
                return None
            return tuple(value) if hasattr(value, "__len__") else float(value)
    return None


def _objects(obj, whole):
    if not whole:
        return [obj]
    root = asset_root(obj)
    return [root] + list(root.children_recursive)


def _materials(objects):
    seen = []
    for ob in objects:
        for slot in getattr(ob, "material_slots", ()):
            mat = slot.material
            if mat is not None and mat.use_nodes and mat not in seen:
                seen.append(mat)
    return seen


def discover(obj, whole):
    params = {}
    if obj is None:
        return []
    for mat in _materials(_objects(obj, whole)):
        for node in _nodes(mat):
            if node.type != "GROUP" or not _is_control_tree(node.node_tree):
                continue
            for sock in node.inputs:
                if sock.type not in {"VALUE", "RGBA"} or sock.is_linked:
                    continue
                entry = params.get(sock.name)
                if entry is None:
                    label, group = param_labels.label_for(sock.name)
                    value = sock.default_value
                    entry = {
                        "name": sock.name,
                        "label": label,
                        "group": group,
                        "kind": "COLOR" if sock.type == "RGBA" else "FLOAT",
                        "value": tuple(value) if sock.type == "RGBA" else float(value),
                        "default": _interface_default(node.node_tree, sock.name),
                        "targets": [],
                    }
                    params[sock.name] = entry
                entry["targets"].append((mat.name, node.name))
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    return sorted(params.values(), key=lambda e: (order.get(e["group"], 99), e["label"]))


def _key(scene, obj):
    whole = scene.arc_shader_scope == "WHOLE"
    return (obj.name if obj else "", whole, _STATE["updates"])


def cached_discover(context):
    obj = context.active_object
    key = _key(context.scene, obj)
    if _CACHE.get("key") != key:
        _CACHE["key"] = key
        _CACHE["params"] = discover(obj, key[1])
    return _CACHE["params"]


def edited_names(tweaks, scope):
    """Names with a stored tweak (value differs from game default) in scope."""
    return set((tweaks or {}).get(scope) or {})


def part_scope(name):
    return "part:" + name


def lost_summary(lost, limit=6):
    """Readable lines for parts losing edits: [(scope, [names])] -> [str]."""
    lines = []
    for scope, names in lost[:limit]:
        part = scope[5:] if scope.startswith("part:") else scope
        lines.append(f"{part}: {', '.join(names)}")
    if len(lost) > limit:
        lines.append(f"... and {len(lost) - limit} more parts")
    return lines


def apply_order(scopes):
    """Whole-asset scope first so part scopes override it."""
    return sorted(scopes, key=lambda s: (s != "whole", s))


def _edited(context):
    obj = context.active_object
    if obj is None:
        return set()
    return edited_names(shader_tweaks.get(asset_root(obj)), _scope(context))


def sync_items(context):
    scene = context.scene
    params = cached_discover(context)
    edited = _edited(context)
    _STATE["guard"] = True
    try:
        items = scene.arc_shader_items
        items.clear()
        for entry in params:
            item = items.add()
            item.name = entry["name"]
            item.label = entry["label"]
            item.group = entry["group"]
            item.kind = entry["kind"]
            item.edited = entry["name"] in edited
            if entry["kind"] == "COLOR":
                item.color_value = entry["value"]
            else:
                item.float_value = entry["value"]
    finally:
        _STATE["guard"] = False
    _STATE["synced"] = _CACHE.get("key")
    sync_colourways(context)


def item_matches(label, name, edited, query="", edited_only=False):
    """Panel filter: case-insensitive substring on label or raw name; optional edited-only."""
    if edited_only and not edited:
        return False
    q = (query or "").strip().lower()
    return not q or q in (label or "").lower() or q in (name or "").lower()


def collapsed_groups(scene):
    return {g for g in getattr(scene, "arc_shader_collapsed", "").split(",") if g}


def hit_to_slot(hit, obj, face_index, face_mat_indices, n_slots):
    """Pure pick mapping: ray hit + face -> (obj, material slot) or None."""
    if not hit or obj is None or face_index is None or not (0 <= face_index < len(face_mat_indices)):
        return None
    if n_slots <= 0:
        return obj, None
    return obj, max(0, min(int(face_mat_indices[face_index]), n_slots - 1))


def pick_core(context, origin, direction, hit_out=None):
    """Ray cast the scene; returns (original obj, slot) or None. No UI needed.
    ``hit_out`` (dict) receives face_index/location for zone lookup."""
    depsgraph = context.evaluated_depsgraph_get()
    hit, loc, _nor, index, obj_eval, _mat = context.scene.ray_cast(depsgraph, origin, direction)
    if not hit or obj_eval is None or obj_eval.type != "MESH":
        return None
    polys = obj_eval.data.polygons
    mats = [0] * len(polys)
    polys.foreach_get("material_index", mats)
    obj = obj_eval.original
    if hit_out is not None:
        hit_out.update(face_index=index, location=tuple(loc))
    return hit_to_slot(hit, obj, index, mats, len(obj.material_slots))


ZONE_CHANNELS = ("Red", "Green", "Blue", "Alpha")


def zone_param_match(name, zone, kind):
    """Pure: does Palette param ``name`` belong to colour zone ``zone`` (1-based)?"""
    if not zone:
        return True
    if kind == "outfit":
        return name == f"Eq{zone - 1}"
    if 1 <= zone <= 4:
        return name.endswith(ZONE_CHANNELS[zone - 1])
    return False


def pick_zone(obj, hit):
    """(zone index or None, zone name or None, kind) via shader_pick mask sample."""
    from . import shader_pick
    try:
        mat, zone, zname = shader_pick.get_zone_at(obj, hit)
    except Exception as exc:
        print(f"Arc Raiders: zone pick failed: {exc}")
        return None, None, ""
    if zone is None or zone <= 0:
        return None, None, ""
    info = shader_pick.find_zone_mask(mat)
    return zone, zname, info.kind if info else ""


def set_pick_zone(scene, zone, kind):
    scene.arc_shader_pick_zone = int(zone or 0)
    scene.arc_shader_pick_zone_kind = kind or ""


def apply_pick(context, obj, slot):
    scene = context.scene
    for ob in context.view_layer.objects:
        if ob.select_get():
            ob.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    mat = None
    if slot is not None:
        obj.active_material_index = slot
        mat = obj.material_slots[slot].material
    scene.arc_shader_pick_material = mat.name if mat is not None else ""
    _refresh(context)
    return mat


def _picked_names(context):
    mat = getattr(context.scene, "arc_shader_pick_material", "")
    if not mat:
        return None
    return {e["name"] for e in cached_discover(context) if any(t[0] == mat for t in e["targets"])}


_OUTDATED = {"key": None, "list": []}


def outdated_groups(force=False):
    """Cached scan of bpy.data.node_groups against builder versions."""
    groups = bpy.data.node_groups
    key = shader_upgrade.cache_key(groups)
    if force or _OUTDATED["key"] != key:
        _OUTDATED["list"] = shader_upgrade.scan(groups, shader_upgrade.current_versions())
        _OUTDATED["key"] = key
    return _OUTDATED["list"]


def run_upgrade(context):
    from .materials import gt_outfit_pbr
    outdated = outdated_groups(force=True)
    done, skipped = shader_upgrade.upgrade_groups(
        bpy.data.node_groups, list(bpy.data.materials), outdated,
        gt_outfit_pbr, gt_outfit_pbr._dr_ops.find_group)
    obj = context.view_layer.objects.active
    if obj is not None:
        apply_all(obj)
    _OUTDATED["key"] = None
    _STATE["synced"] = None
    _refresh(context)
    return done, skipped


def visible_groups(context):
    scene = context.scene
    basic = scene.arc_shader_mode == "BASIC"
    query = getattr(scene, "arc_shader_search", "")
    edited_only = getattr(scene, "arc_shader_edited_only", False)
    picked = _picked_names(context)
    zone = getattr(scene, "arc_shader_pick_zone", 0) if picked is not None else 0
    zkind = getattr(scene, "arc_shader_pick_zone_kind", "")
    groups = {}
    for item in scene.arc_shader_items:
        if basic and item.group not in BASIC_GROUPS:
            continue
        if picked is not None and item.name not in picked:
            continue
        if zone and item.group == "Palette" and not zone_param_match(item.name, zone, zkind):
            continue
        if not item_matches(item.label, item.name, item.edited, query, edited_only):
            continue
        groups.setdefault(item.group, []).append(item)
    return [(g, groups[g]) for g in GROUP_ORDER if g in groups]


def _scope(context):
    if context.scene.arc_shader_scope == "WHOLE":
        return "whole"
    return "part:" + context.active_object.name


def set_param(context, name, value):
    obj = context.active_object
    entry = next((e for e in cached_discover(context) if e["name"] == name), None)
    if entry is None:
        return False
    for mat_name, node_name in entry["targets"]:
        mat = bpy.data.materials.get(mat_name)
        if mat is not None:
            wrapper_tree.own_wrapper(mat)
        node = wrapper_tree.find_node(mat, node_name) if mat else None
        if node is not None and name in node.inputs:
            node.inputs[name].default_value = value
    entry["value"] = value
    default = entry["default"] if entry["default"] is not None else value
    shader_tweaks.record(asset_root(obj), _scope(context), name, value, default)
    _STATE["self_edit"] = True
    return True


def _item_update(self, context):
    if _STATE.get("guard") or context.active_object is None:
        return
    value = tuple(self.color_value) if self.kind == "COLOR" else self.float_value
    set_param(context, self.name, value)
    _STATE["guard"] = True
    try:
        self.edited = self.name in _edited(context)
    finally:
        _STATE["guard"] = False


def _scope_update(self, context):
    _refresh(context)


def clear_zone_caches():
    """Drop shader_pick mask/pixel caches (only if it was loaded) so graph edits can't give stale zones."""
    import sys
    sp = sys.modules.get(f"{__package__}.shader_pick")
    if sp is not None and hasattr(sp, "clear_caches"):
        sp.clear_caches()
        return True
    return False


def _refresh(context):
    if context.scene is None or not hasattr(context.scene, "arc_shader_items"):
        return
    if _STATE.get("synced") != _key(context.scene, context.active_object):
        sync_items(context)
        uc_sync_scene(context)


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph=None):
    if depsgraph is not None:
        hit = any(isinstance(u.id, (bpy.types.Material, bpy.types.NodeTree)) for u in depsgraph.updates)
        if hit and not _STATE.get("self_edit"):
            _STATE["updates"] += 1
            clear_zone_caches()
    _STATE["self_edit"] = False
    try:
        _refresh(bpy.context)
    except Exception as exc:
        print(f"Arc Raiders shader controls: refresh failed: {exc}")


class ArcShaderControlItem(bpy.types.PropertyGroup):
    name: StringProperty()
    label: StringProperty()
    group: StringProperty()
    kind: StringProperty(default="FLOAT")
    edited: BoolProperty(default=False)
    float_value: FloatProperty(name="Value", update=_item_update)
    color_value: FloatVectorProperty(name="Color", subtype="COLOR", size=4, min=0.0, max=1.0, update=_item_update)


class ARC_OT_shader_control_reset(bpy.types.Operator):
    bl_idname = "arc.shader_control_reset"
    bl_label = "Reset Group"
    bl_options = {"REGISTER", "UNDO"}

    group: StringProperty()

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        for entry in cached_discover(context):
            if entry["group"] != self.group or entry["default"] is None:
                continue
            set_param(context, entry["name"], entry["default"])
        _STATE["synced"] = None
        _refresh(context)
        return {"FINISHED"}


def _part_objects(obj):
    root = asset_root(obj)
    return [ob for ob in [root] + list(root.children_recursive) if _materials([ob])]


def _resolve(obj):
    root = asset_root(obj)
    by_name = {ob.name: ob for ob in [root] + list(root.children_recursive)}

    def resolve(scope):
        if scope == "whole":
            objs = list(by_name.values())
        elif scope.startswith("part:") and scope[5:] in by_name:
            objs = [by_name[scope[5:]]]
        else:
            return []
        out = []
        for mat in _materials(objs):
            for node in _nodes(mat):
                if node.type == "GROUP" and _is_control_tree(node.node_tree):
                    out.append((mat, node))
        return out
    return resolve


def _restore_defaults(obj, whole):
    for entry in discover(obj, whole):
        if entry["default"] is None:
            continue
        for mat_name, node_name in entry["targets"]:
            mat = bpy.data.materials.get(mat_name)
            node = wrapper_tree.find_node(mat, node_name) if mat else None
            if node is not None:
                node.inputs[entry["name"]].default_value = entry["default"]


def apply_all(obj):
    """Write stored tweaks onto sockets, whole scope first, parts override."""
    root = asset_root(obj)
    tweaks = shader_tweaks.get(root)
    resolve = _resolve(obj)
    skipped = []
    for scope in apply_order(tweaks):
        def one(s, _scope=scope):
            return resolve(s) if s == _scope else []
        for name in shader_tweaks.apply(root, one):
            if name not in skipped and name not in user_controls.BY_SOCKET and _parse_zo(name) is None:
                skipped.append(name)
    uc_apply(root, uc_values(root))
    by_name = {ob.name: ob for ob in getattr(root, "children_recursive", [])}
    for scope, bucket in tweaks.items():          # parts with their own User Controls
        part = by_name.get(scope[5:]) if scope.startswith("part:") else None
        if part is not None and any(sock in bucket for sock in user_controls.SOCKETS):
            uc_apply(root, uc_values(root, part), part)
    zo_replay(root)
    seen = set()
    for ob in [root, *getattr(root, "children_recursive", [])]:
        for slot in getattr(ob, "material_slots", []):
            mat = slot.material
            if mat is not None and mat.name not in seen:
                seen.add(mat.name)
                reapply_pro_toggles(mat)
    _STATE["self_edit"] = True
    return skipped


def _resync(context):
    _STATE["updates"] += 1
    _STATE["synced"] = None
    clear_zone_caches()
    _refresh(context)


class ARC_OT_shader_control_reset_all(bpy.types.Operator):
    bl_idname = "arc.shader_control_reset_all"
    bl_label = "Reset All"
    bl_description = "Reset every shader control in the current scope to the game default"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        whole = context.scene.arc_shader_scope == "WHOLE"
        _restore_defaults(obj, whole)
        shader_tweaks.reset(asset_root(obj), _scope(context))
        apply_all(obj)
        _resync(context)
        return {"FINISHED"}


class ARC_OT_shader_tweaks_save(bpy.types.Operator, ExportHelper):
    bl_idname = "arc.shader_tweaks_save"
    bl_label = "Save Tweaks"
    bl_description = "Save this asset's shader tweaks to a JSON file"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        text = shader_tweaks.to_json(asset_root(context.active_object))
        with open(self.filepath, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.report({"INFO"}, f"Saved shader tweaks to {self.filepath}")
        return {"FINISHED"}


class ARC_OT_shader_tweaks_load(bpy.types.Operator, ImportHelper):
    bl_idname = "arc.shader_tweaks_load"
    bl_label = "Load Tweaks"
    bl_description = "Load shader tweaks from a JSON file, replacing this asset's tweaks"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        try:
            with open(self.filepath, encoding="utf-8") as fh:
                text = fh.read()
            _restore_defaults(obj, True)
            shader_tweaks.from_json(asset_root(obj), text, replace=True)
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, f"Load failed: {exc}")
            return {"CANCELLED"}
        skipped = apply_all(obj)
        _resync(context)
        if skipped:
            self.report({"WARNING"}, "Inputs not on this asset: " + ", ".join(skipped))
        return {"FINISHED"}


class ARC_OT_shader_tweaks_sync_parts(bpy.types.Operator):
    bl_idname = "arc.shader_tweaks_sync_parts"
    bl_label = "Copy Part to All Parts"
    bl_description = "Copy this part's shader tweaks to every other part of the asset"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.scene.arc_shader_scope == "PART"

    @staticmethod
    def _plan(context):
        obj = context.active_object
        scopes = [part_scope(ob.name) for ob in _part_objects(obj)]
        return asset_root(obj), part_scope(obj.name), scopes

    def invoke(self, context, event):
        root, src, scopes = self._plan(context)
        lost = shader_tweaks.sync_preview(root, src, scopes)
        if not lost:
            return self.execute(context)
        self._lines = lost_summary(lost)
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        col = self.layout.column()
        col.label(text="These parts lose their own edits:", icon="ERROR")
        for line in getattr(self, "_lines", ()):
            col.label(text=line)

    def execute(self, context):
        obj = context.active_object
        root, src, scopes = self._plan(context)
        for ob in _part_objects(obj):
            if ob is not obj:
                _restore_defaults(ob, False)
        shader_tweaks.sync(root, src, scopes)
        apply_all(obj)
        _resync(context)
        return {"FINISHED"}


class ArcColourwayItem(bpy.types.PropertyGroup):
    name: StringProperty()


class ARC_UL_colourways(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        layout.label(text=item.name, icon="COLOR")


def sync_colourways(context):
    scene = context.scene
    if not hasattr(scene, "arc_colourways"):
        return
    obj = context.active_object
    names = sorted(shader_tweaks.colourways(asset_root(obj))) if obj is not None else []
    if [c.name for c in scene.arc_colourways] == names:
        return
    scene.arc_colourways.clear()
    for n in names:
        scene.arc_colourways.add().name = n
    if scene.arc_colourway_index >= len(names):
        scene.arc_colourway_index = max(0, len(names) - 1)


def _selected_colourway(context):
    scene = context.scene
    idx = scene.arc_colourway_index
    if 0 <= idx < len(scene.arc_colourways):
        return scene.arc_colourways[idx].name
    return None


def capture_values(obj):
    """Current whole-asset input values (first target of each input)."""
    out = {}
    for entry in discover(obj, True):
        out[entry["name"]] = entry["value"]
    return out


def _whole_targets(context):
    """Cached whole-scope discovery: {name: [node input sockets]}."""
    obj = context.active_object
    key = ("cw", obj.name, _STATE["updates"])
    if _CACHE.get("cw_key") != key:
        socks = {}
        for entry in discover(obj, True):
            lst = socks.setdefault(entry["name"], [])
            for mat_name, node_name in entry["targets"]:
                mat = bpy.data.materials.get(mat_name)
                node = wrapper_tree.find_node(mat, node_name) if mat else None
                if node is not None:
                    lst.append(node.inputs[entry["name"]])
        _CACHE["cw_key"] = key
        _CACHE["cw_socks"] = socks
    return _CACHE["cw_socks"]


def apply_colourway_values(context, values):
    socks = _whole_targets(context)
    missing = []
    for name, value in values.items():
        targets = socks.get(name)
        if not targets:
            missing.append(name)
            continue
        for sock in targets:
            sock.default_value = value
    return missing


class ARC_OT_shader_colourway_save(bpy.types.Operator):
    bl_idname = "arc.shader_colourway_save"
    bl_label = "Save Colourway"
    bl_description = "Save the current shader input values as a named colourway on this asset"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        name = (self.name or context.scene.arc_colourway_name).strip()
        if not name:
            self.report({"ERROR"}, "Colourway needs a name")
            return {"CANCELLED"}
        obj = context.active_object
        shader_tweaks.save_colourway(asset_root(obj), name, capture_values(obj))
        sync_colourways(context)
        names = [c.name for c in context.scene.arc_colourways]
        context.scene.arc_colourway_index = names.index(name)
        return {"FINISHED"}


class ARC_OT_shader_colourway_apply(bpy.types.Operator):
    bl_idname = "arc.shader_colourway_apply"
    bl_label = "Apply Colourway"
    bl_description = "Apply the selected colourway, then reapply this asset's tweaks on top"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        import time
        t0 = time.perf_counter()
        obj = context.active_object
        name = self.name or _selected_colourway(context)
        values = shader_tweaks.load_colourway(asset_root(obj), name) if name else None
        if values is None:
            self.report({"ERROR"}, f"No colourway {name!r}")
            return {"CANCELLED"}
        missing = apply_colourway_values(context, values)
        apply_all(obj)
        _resync(context)
        ms = (time.perf_counter() - t0) * 1000.0
        print(f"Arc Raiders shader controls: colourway {name!r} applied in {ms:.1f} ms")
        if missing:
            self.report({"WARNING"}, "Inputs not on this asset: " + ", ".join(missing))
        return {"FINISHED"}


class ARC_OT_shader_colourway_delete(bpy.types.Operator):
    bl_idname = "arc.shader_colourway_delete"
    bl_label = "Delete Colourway"
    bl_description = "Delete the selected colourway from this asset"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        name = self.name or _selected_colourway(context)
        if not name or not shader_tweaks.delete_colourway(asset_root(context.active_object), name):
            return {"CANCELLED"}
        sync_colourways(context)
        return {"FINISHED"}


class ARC_OT_shader_colourway_export(bpy.types.Operator, ExportHelper):
    bl_idname = "arc.shader_colourway_export"
    bl_label = "Export Colourways"
    bl_description = "Export this asset's colourways to a JSON file"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        with open(self.filepath, "w", encoding="utf-8") as fh:
            fh.write(shader_tweaks.colourways_to_json(asset_root(context.active_object)))
        return {"FINISHED"}


class ARC_OT_shader_colourway_import(bpy.types.Operator, ImportHelper):
    bl_idname = "arc.shader_colourway_import"
    bl_label = "Import Colourways"
    bl_description = "Import colourways from a JSON file into this asset"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        try:
            with open(self.filepath, encoding="utf-8") as fh:
                shader_tweaks.colourways_from_json(asset_root(context.active_object), fh.read())
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, f"Import failed: {exc}")
            return {"CANCELLED"}
        sync_colourways(context)
        return {"FINISHED"}


class ARC_OT_shader_param_info(bpy.types.Operator):
    bl_idname = "arc.shader_param_info"
    bl_label = "Shader Parameter"
    bl_options = {"INTERNAL"}

    name: StringProperty()

    @classmethod
    def description(cls, context, properties):
        return f"Game material parameter: {properties.name}"

    def execute(self, context):
        return {"FINISHED"}


class ARC_OT_shader_section_toggle(bpy.types.Operator):
    bl_idname = "arc.shader_section_toggle"
    bl_label = "Toggle Section"
    bl_description = "Collapse or expand this shader section (remembered per scene)"
    bl_options = {"INTERNAL"}

    group: StringProperty()

    def execute(self, context):
        scene = context.scene
        cur = collapsed_groups(scene)
        cur.symmetric_difference_update({self.group})
        scene.arc_shader_collapsed = ",".join(g for g in GROUP_ORDER if g in cur)
        return {"FINISHED"}


class ARC_OT_shader_pick(bpy.types.Operator):
    bl_idname = "arc.shader_pick"
    bl_label = "Pick Material"
    bl_description = "Click a mesh in the viewport to show its material's controls (Esc cancels)"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        if context.area is None or context.area.type != "VIEW_3D":
            self.report({"WARNING"}, "Pick needs a 3D Viewport")
            return {"CANCELLED"}
        context.window.cursor_modal_set("EYEDROPPER")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            context.window.cursor_modal_restore()
            return {"CANCELLED"}
        if event.type != "LEFTMOUSE" or event.value != "PRESS":
            return {"RUNNING_MODAL"}
        from bpy_extras import view3d_utils
        region = next((r for r in context.area.regions if r.type == "WINDOW"
                       and r.x <= event.mouse_x < r.x + r.width and r.y <= event.mouse_y < r.y + r.height), None)
        if region is None:
            return {"RUNNING_MODAL"}
        rv3d = context.area.spaces.active.region_3d
        co = (event.mouse_x - region.x, event.mouse_y - region.y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, co)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, co)
        context.window.cursor_modal_restore()
        hit = {}
        res = pick_core(context, origin, direction, hit)
        if res is None:
            self.report({"INFO"}, "No mesh under cursor")
            return {"CANCELLED"}
        zone, zname, kind = pick_zone(res[0], hit)
        set_pick_zone(context.scene, zone, kind)
        mat = apply_pick(context, *res)
        if zone and "Palette" in collapsed_groups(context.scene):
            context.scene.arc_shader_collapsed = ",".join(
                g for g in collapsed_groups(context.scene) if g != "Palette")
        self.report({"INFO"}, f"Picked {res[0].name}" + (f" / {mat.name}" if mat else "")
                    + (f" / {zname}" if zname else ""))
        return {"FINISHED"}


class ARC_OT_shader_upgrade(bpy.types.Operator):
    bl_idname = "arc.shader_upgrade"
    bl_label = "Upgrade Shader Groups"
    bl_description = "Rebuild outdated shader node groups with the current builder, keeping input values and tweaks"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        done, skipped = run_upgrade(context)
        msg = f"Upgraded {len(done)} group(s)"
        if skipped:
            msg += f"; {len(skipped)} need material context: {', '.join(skipped[:4])}"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class ARC_OT_shader_pick_clear(bpy.types.Operator):
    bl_idname = "arc.shader_pick_clear"
    bl_label = "Show All Materials"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        context.scene.arc_shader_pick_material = ""
        set_pick_zone(context.scene, 0, "")
        return {"FINISHED"}


# ---- Pro toggles (Decals / Detail normal / Emission) ----
# State lives on the material: mat["arc_pro_<key>"] = 0 means off (missing = on).
# Off mutes matching top-level nodes (shared groups are never edited inside) and,
# for Emission, unlinks/zeroes Principled Emission Strength. Every change is
# recorded on the node so "on" restores exactly what was there.
PRO_TOGGLES = (("decals", "Decals"), ("detail_normal", "Detail normal"), ("emission", "Emission"))
_PRO_DECAL_GROUPS = ("GT_DecalLayer", "GT_DecalTint", "DR.PackedDecal")
_PRO_SKIP_TYPES = {"BSDF_PRINCIPLED", "OUTPUT_MATERIAL", "GROUP_INPUT", "GROUP_OUTPUT", "FRAME", "REROUTE"}


def pro_classify(node):
    """Toggle key a node belongs to, or None. Pure: reads type/name/label/node_tree.name."""
    ntype = getattr(node, "type", "")
    if ntype in _PRO_SKIP_TYPES:
        return None
    tree = getattr(node, "node_tree", None)
    tname = getattr(tree, "name", "") or ""
    if ntype == "GROUP" and _is_control_tree(tree):
        return None
    text = f"{getattr(node, 'name', '')}|{getattr(node, 'label', '')}".lower()
    if tname.startswith(_PRO_DECAL_GROUPS) or "decal" in text:
        return "decals"
    if "detail normal" in text or "detail_normal" in text or "detailnormal" in text:
        return "detail_normal"
    if "emissi" in text or tname.lower().startswith("emissi"):
        return "emission"
    return None


def pro_state(mat, key):
    try:
        return bool(mat.get(f"arc_pro_{key}", 1))
    except Exception:
        return True


def pro_targets(mat):
    out = {k: [] for k, _ in PRO_TOGGLES}
    tree = getattr(mat, "node_tree", None)
    if tree is None:
        return out
    for node in _nodes(mat):
        key = pro_classify(node)
        if key:
            out[key].append(node)
        if key is None and node.type == "BSDF_PRINCIPLED":
            sock = node.inputs.get("Emission Strength")
            if sock is not None and (sock.is_linked or sock.default_value > 0.0 or node.get("arc_pro_em")):
                out["emission"].append(node)
    return out


def _pro_bsdf_off(tree, node):
    import json
    tree = node.id_data     # the level that owns the node (material tree or wrapper)
    if node.get("arc_pro_em"):
        return
    sock = node.inputs["Emission Strength"]
    rec = {"value": float(sock.default_value)}
    if sock.is_linked:
        link = sock.links[0]
        rec["from_node"] = link.from_node.name
        rec["from_socket"] = link.from_socket.identifier
        tree.links.remove(link)
    sock.default_value = 0.0
    node["arc_pro_em"] = json.dumps(rec)


def _pro_bsdf_on(tree, node):
    import json
    tree = node.id_data     # the level that owns the node (material tree or wrapper)
    raw = node.get("arc_pro_em")
    if not raw:
        return
    rec = json.loads(raw)
    sock = node.inputs["Emission Strength"]
    sock.default_value = rec.get("value", 0.0)
    src = tree.nodes.get(rec.get("from_node", "")) if rec.get("from_node") else None
    if src is not None:
        out = next((s for s in src.outputs if s.identifier == rec.get("from_socket")), None)
        if out is not None:
            tree.links.new(out, sock)
    del node["arc_pro_em"]


_PRO_GATE_RE = None


def _pro_is_decal_gate(node):
    """Decal slot gate: GT_DecalSlotCoverage group or GT_decal<N>_use MULTIPLY (coverage x UseDecal)."""
    global _PRO_GATE_RE
    if _PRO_GATE_RE is None:
        import re
        _PRO_GATE_RE = re.compile(r"decal\d+_use(\.\d+)?$", re.I)
    if getattr(node, "type", "") == "GROUP":
        tname = getattr(getattr(node, "node_tree", None), "name", "") or ""
        if tname.startswith("GT_DecalSlotCoverage") and "Use" in node.inputs:
            return True
        nm = getattr(node, "name", "") or ""
        return ".Decal." in nm and nm.endswith(".LayerGate") and "Alpha" in node.inputs
    return getattr(node, "type", "") == "MATH" and bool(_PRO_GATE_RE.search(getattr(node, "name", "")))


def _pro_gate_socket(node):
    """Slot on/off weight: GT_DecalSlotCoverage 'Use', or input 1 of a GT_decal<N>_use MULTIPLY."""
    if node.type == "GROUP":
        return node.inputs["Use"] if "Use" in node.inputs else node.inputs["Alpha"]
    return node.inputs[1]


def _pro_gate_off(tree, node):
    import json
    tree = node.id_data     # the level that owns the node (material tree or wrapper)
    if node.get("arc_pro_gate"):
        return
    sock = _pro_gate_socket(node)
    rec = {"value": float(sock.default_value)}
    if sock.is_linked:
        link = sock.links[0]
        rec["from_node"] = link.from_node.name
        rec["from_socket"] = link.from_socket.identifier
        tree.links.remove(link)
    sock.default_value = 0.0
    node["arc_pro_gate"] = json.dumps(rec)


def _pro_gate_on(tree, node):
    import json
    tree = node.id_data     # the level that owns the node (material tree or wrapper)
    raw = node.get("arc_pro_gate")
    if not raw:
        return
    rec = json.loads(raw)
    sock = _pro_gate_socket(node)
    sock.default_value = rec.get("value", 0.0)
    src = tree.nodes.get(rec.get("from_node", "")) if rec.get("from_node") else None
    if src is not None:
        out = next((s for s in src.outputs if s.identifier == rec.get("from_socket")), None)
        if out is not None:
            tree.links.new(out, sock)
    del node["arc_pro_gate"]


def apply_pro_toggle(mat, key, on):
    """Set one toggle; returns the number of nodes changed. No rebuild."""
    tree = getattr(mat, "node_tree", None)
    if tree is None:
        return 0
    wrapper_tree.own_wrapper(mat)
    if on:
        if f"arc_pro_{key}" in mat:
            del mat[f"arc_pro_{key}"]
    else:
        mat[f"arc_pro_{key}"] = 0
    changed = 0
    targets = pro_targets(mat)[key]
    gates = [n for n in targets if _pro_is_decal_gate(n)] if key == "decals" else []
    if gates:
        # Zero the slot alpha at its UseDecal gate (save/restore like emission); muting the
        # coordinate/source nodes passes values through and leaves the decal visible.
        for node in gates:
            (_pro_gate_on if on else _pro_gate_off)(tree, node)
            changed += 1
        targets = [n for n in targets if n.get("arc_pro_muted") == key] if on else []
    for node in targets:
        if node.type == "BSDF_PRINCIPLED":
            (_pro_bsdf_on if on else _pro_bsdf_off)(tree, node)
            changed += 1
        elif on:
            if node.get("arc_pro_muted") == key:
                node.mute = False
                del node["arc_pro_muted"]
                changed += 1
        elif not node.mute:
            node.mute = True
            node["arc_pro_muted"] = key
            changed += 1
    _STATE["self_edit"] = True
    return changed


def reapply_pro_toggles(mat):
    """Re-impose stored off states (after re-import/upgrade rebuilt the tree)."""
    n = 0
    for key, _ in PRO_TOGGLES:
        if not pro_state(mat, key):
            n += apply_pro_toggle(mat, key, False)
    return n


def pro_material(context):
    scene = context.scene
    name = getattr(scene, "arc_shader_pick_material", "")
    mat = bpy.data.materials.get(name) if name else None
    if mat is None and context.active_object is not None:
        mat = context.active_object.active_material
    return mat


class ARC_OT_shader_pro_toggle(bpy.types.Operator):
    bl_idname = "arc.shader_pro_toggle"
    bl_label = "Toggle Feature"
    bl_description = "Turn this feature on or off for the material (mutes nodes, no rebuild)"
    bl_options = {"REGISTER", "UNDO"}

    key: StringProperty(default="")
    material: StringProperty(default="")

    def execute(self, context):
        import time
        t0 = time.perf_counter()
        mat = bpy.data.materials.get(self.material) if self.material else pro_material(context)
        if mat is None or self.key not in dict(PRO_TOGGLES):
            return {"CANCELLED"}
        on = not pro_state(mat, self.key)
        n = apply_pro_toggle(mat, self.key, on)
        ms = (time.perf_counter() - t0) * 1000.0
        _STATE["pro_ms"] = ms
        print(f"Arc Raiders shader controls: {self.key} {'on' if on else 'off'} on {mat.name}: {n} nodes, SYNC_MS {ms:.2f}")
        return {"FINISHED"}


classes = (
    ARC_OT_shader_pro_toggle,
    ArcShaderControlItem,
    ARC_OT_shader_pick,
    ARC_OT_shader_pick_clear,
    ARC_OT_shader_upgrade,
    ARC_OT_shader_param_info,
    ARC_OT_shader_section_toggle,
    ArcColourwayItem,
    ARC_UL_colourways,
    ARC_OT_shader_colourway_save,
    ARC_OT_shader_colourway_apply,
    ARC_OT_shader_colourway_delete,
    ARC_OT_shader_colourway_export,
    ARC_OT_shader_colourway_import,
    ARC_OT_shader_control_reset,
    ARC_OT_shader_control_reset_all,
    ARC_OT_shader_tweaks_save,
    ARC_OT_shader_tweaks_load,
    ARC_OT_shader_tweaks_sync_parts,
)
_MSGBUS_OWNER = object()


def _safe_refresh():
    try:
        ctx = bpy.context
        if isinstance(ctx, bpy.types.Context) and getattr(ctx, "scene", None) is not None:
            _STATE["synced"] = None
            _refresh(ctx)
    except Exception as exc:
        print(f"Arc Raiders shader controls: refresh failed: {exc}")
    return None


def _on_active_change(*_args):
    _safe_refresh()


def _subscribe():
    bpy.msgbus.clear_by_owner(_MSGBUS_OWNER)
    bpy.msgbus.subscribe_rna(
        key=(bpy.types.LayerObjects, "active"), owner=_MSGBUS_OWNER, args=(),
        notify=_on_active_change, options={"PERSISTENT"})


@bpy.app.handlers.persistent
def _on_load_post(*_args):
    _CACHE.clear()
    clear_zone_caches()
    _subscribe()
    _safe_refresh()


# ---- User Controls: asset-wide sliders on the shared DR.UserControls group ----
_UC_GUARD = {"on": False}


def _uc_root(name_or_obj):
    if isinstance(name_or_obj, str):
        obj = bpy.data.objects.get(name_or_obj) if name_or_obj else bpy.context.active_object
    else:
        obj = name_or_obj
    return asset_root(obj) if obj is not None else None


SELECT_MODES = (
    ("WHOLE", "Whole Outfit", "Edits change every part of the asset"),
    ("PART", "Part", "Edits change the active part only"),
    ("ZONE", "Zone/Decal", "Edits change the picked colour zone or decal"),
)


def select_mode(scene):
    return getattr(scene, "arc_shader_select_mode", "WHOLE")


def uc_part(context):
    """The active part when the selection mode is Part (edits then stay on that part), else None."""
    obj = context.active_object
    if obj is None or select_mode(context.scene) not in ("PART", "ZONE") or asset_root(obj) is obj:
        return None
    return obj


def param_sockets(objects, name):
    """Unlinked control sockets called *name* on every material of *objects*."""
    out = []
    for mat in _materials(objects):
        for node in _nodes(mat):
            if node.type != "GROUP" or not _is_control_tree(node.node_tree):
                continue
            sock = node.inputs.get(name)
            if sock is not None and not sock.is_linked and sock.type in {"VALUE", "RGBA"}:
                out.append(sock)
    return out


def mark_self_edit():
    """A socket write made by the addon itself: keeps the zone caches and the control list."""
    _STATE["self_edit"] = True


def zone_item(scene):
    """Shader control item of the picked colour zone, or None."""
    zone = getattr(scene, "arc_shader_pick_zone", 0)
    if not zone:
        return None
    kind = getattr(scene, "arc_shader_pick_zone_kind", "") or "outfit"
    for item in scene.arc_shader_items:
        if item.kind == "COLOR" and item.group == "Palette" and zone_param_match(item.name, zone, kind):
            return item
    return None


def select_from_pick(context, obj, material, zone, kind, decal=0):
    """Pick click: make the part active and, in Zone/Decal mode, remember the zone and decal."""
    scene = context.scene
    slot = next((i for i, s in enumerate(obj.material_slots) if s.material == material), None)
    apply_pick(context, obj, slot)
    if select_mode(scene) == "ZONE":
        set_pick_zone(scene, zone, kind)
        scene.arc_shader_pick_decal = int(decal or 0)
    else:
        scene.arc_shader_pick_material = ""
        set_pick_zone(scene, 0, "")
        scene.arc_shader_pick_decal = 0
    _STATE["synced"] = None
    _refresh(context)
    uc_sync_scene(context)
    zo_sync_scene(context)


def _select_mode_update(self, context):
    scope = "PART" if select_mode(self) in ("PART", "ZONE") else "WHOLE"
    if self.arc_shader_scope != scope:
        self.arc_shader_scope = scope
    if select_mode(self) != "ZONE":
        self.arc_shader_pick_material = ""
        set_pick_zone(self, 0, "")
        self.arc_shader_pick_decal = 0
    _STATE["synced"] = None
    _refresh(context)
    uc_sync_scene(context)
    zo_sync_scene(context)


class ARC_OT_shader_zone_reset(bpy.types.Operator):
    bl_idname = "arc.shader_zone_reset"
    bl_label = "Reset Zone"
    bl_description = "Return the picked zone of this part to the game colour and material"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        t = zone_target(context)
        if t is not None:
            zo_reset(t[0], t[1], t[2])
            zo_sync_scene(context)
        item = zone_item(context.scene)
        entry = next((e for e in cached_discover(context) if item is not None and e["name"] == item.name), None)
        if entry is None or entry["default"] is None:
            if t is None:
                return {"CANCELLED"}
        else:
            set_param(context, entry["name"], entry["default"])
        _STATE["synced"] = None
        _refresh(context)
        return {"FINISHED"}


def uc_values(root, part=None):
    """Stored User Controls values for one asset root, defaults for the rest ({scene prop: value}).
    With *part*, that part's own values override the whole-asset ones."""
    stored = dict(shader_tweaks.get(root).get("whole", {})) if root is not None else {}
    if root is not None and part is not None:
        stored.update(shader_tweaks.get(root).get(part_scope(part.name), {}))
    values = {prop: float(stored.get(sock, default)) for sock, prop, _l, default, _lo, _hi in user_controls.CONTROLS}
    if _uc_weapon(root):
        values[_UC_WEAR] = float(root.get("arc_weapon_wear", 0.0))
    return values


_UC_WEAR = "arc_uc_wear"
_UC_TINT = "arc_uc_tint"


def _uc_weapon(root):
    """Weapon roots carry the game's WeaponQuality input; the Wear slider drives that, not the group."""
    try:
        return root is not None and "arc_weapon_wear" in root.keys()
    except Exception:
        return False


def _uc_group_values(root, values):
    if not _uc_weapon(root):
        return values
    out = dict(values)
    out[_UC_WEAR] = 0.0
    return out


def uc_apply(root, values, part=None):
    if root is None:
        return 0
    objects = [part] if part is not None else _objects(root, True)
    n = user_controls.apply(_materials(objects), _uc_group_values(root, values))
    _STATE["self_edit"] = True
    return n


def uc_set(root, values, part=None):
    """Record non-default values as whole-asset tweaks and write them onto every material.
    With *part*, the changed values are stored for that part and written onto its materials only."""
    if part is not None:
        merged = uc_values(root, part)
        for prop, value in values.items():
            if prop in user_controls.BY_PROP and not (prop == _UC_WEAR and _uc_weapon(root)):
                merged[prop] = float(value)
                # default NaN: a part value always stays stored, also when it equals the game default
                shader_tweaks.record(root, part_scope(part.name), user_controls.BY_PROP[prop][0], float(value), float("nan"))
        return uc_apply(root, merged, part)
    merged = uc_values(root)
    for prop, value in values.items():
        if prop in user_controls.BY_PROP:
            merged[prop] = float(value)
    weapon = _uc_weapon(root)
    if weapon and _UC_WEAR in values:
        from ..Weapons import weapon_import
        weapon_import.set_assembly_wear(root, wear=min(max(float(values[_UC_WEAR]), 0.0), 1.0))
    for prop, value in merged.items():
        if weapon and prop == _UC_WEAR:
            continue
        sock, _p, _l, default, _lo, _hi = user_controls.BY_PROP[prop]
        shader_tweaks.record(root, "whole", sock, value, default)
    return uc_apply(root, merged)


def uc_sync_scene(context):
    import colorsys
    scene = context.scene
    root = _uc_root(context.active_object) if context.active_object is not None else None
    values = uc_values(root, uc_part(context))
    _UC_GUARD["on"] = True
    try:
        for prop, value in values.items():
            if hasattr(scene, prop) and abs(getattr(scene, prop) - value) > 1e-7:
                setattr(scene, prop, value)
        if hasattr(scene, _UC_TINT):
            rgb = colorsys.hsv_to_rgb(values["arc_uc_tint_h"] % 1.0, min(max(values["arc_uc_tint_a"], 0.0), 1.0), 1.0)
            if max(abs(a - b) for a, b in zip(rgb, getattr(scene, _UC_TINT))) > 1e-5:
                setattr(scene, _UC_TINT, rgb)
    finally:
        _UC_GUARD["on"] = False


def _uc_tint_update(self, context):
    """Colour wheel: hue and saturation of the picked colour become Tint Hue and Tint Amount."""
    import colorsys
    if _UC_GUARD["on"] or context.active_object is None:
        return
    h, s, _v = colorsys.rgb_to_hsv(*[min(max(float(c), 0.0), 1.0) for c in getattr(self, _UC_TINT)])
    uc_set(_uc_root(context.active_object), {"arc_uc_tint_h": h, "arc_uc_tint_a": s}, uc_part(context))
    uc_sync_scene(context)


def _uc_prop_update(prop):
    def update(self, context):
        if _UC_GUARD["on"] or context.active_object is None:
            return
        uc_set(_uc_root(context.active_object), {prop: getattr(self, prop)}, uc_part(context))
    return update


class ARC_OT_user_controls_set(bpy.types.Operator):
    bl_idname = "arc.user_controls_set"
    bl_label = "Set User Controls"
    bl_description = "Set User Controls values on every material of the asset (input values only, no rebuild)"
    bl_options = {"REGISTER", "UNDO"}
    root: StringProperty(default="", options={"SKIP_SAVE"})
    values: StringProperty(default="{}", options={"SKIP_SAVE"})

    def execute(self, context):
        import json
        root = _uc_root(self.root)
        if root is None:
            self.report({"WARNING"}, "No asset picked")
            return {"CANCELLED"}
        n = uc_set(root, json.loads(self.values or "{}"))
        uc_sync_scene(context)
        self.report({"INFO"}, f"User Controls: {n} input(s) set")
        return {"FINISHED"}


class ARC_OT_user_controls_reset(bpy.types.Operator):
    bl_idname = "arc.user_controls_reset"
    bl_label = "Reset User Controls"
    bl_description = "Return the asset's User Controls to neutral (game look)"
    bl_options = {"REGISTER", "UNDO"}
    root: StringProperty(default="", options={"SKIP_SAVE"})

    def execute(self, context):
        root = _uc_root(self.root)
        if root is None:
            return {"CANCELLED"}
        part = uc_part(context)
        if part is not None and asset_root(part) is root:
            for sock in user_controls.SOCKETS:
                shader_tweaks.reset(root, part_scope(part.name), sock)
            uc_apply(root, uc_values(root), part)       # the part follows the whole asset again
            uc_sync_scene(context)
            return {"FINISHED"}
        for scope in list(shader_tweaks.get(root)):
            for sock in user_controls.SOCKETS:
                shader_tweaks.reset(root, scope, sock)
        if _uc_weapon(root):
            from ..Weapons import weapon_import
            weapon_import.set_assembly_wear(root)        # back to the tier's game wear
        uc_apply(root, uc_values(root))
        uc_sync_scene(context)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Zone/Decal tab: per-zone offsets on the picked part (user_controls.ZO_CHANNELS)
# ---------------------------------------------------------------------------
ZO_PREFIX = "ZO z"
_ZO_GUARD = {"on": False}


def zo_prop(key):
    return "arc_zo_" + key


def zo_tweak_name(zone, key):
    return f"{ZO_PREFIX}{int(zone)} {key}"


def _parse_zo(name):
    """'ZO z3 rough' -> (3, 'rough'), else None."""
    if not isinstance(name, str) or not name.startswith(ZO_PREFIX):
        return None
    head, _sp, key = name[len(ZO_PREFIX):].partition(" ")
    if not head.isdigit() or key not in user_controls.ZO_BY_KEY:
        return None
    return int(head), key


def zone_target(context):
    """(root, part, zone) of the Zone/Decal tab, or None when no part zone is picked."""
    scene = context.scene
    obj = context.active_object
    zone = int(getattr(scene, "arc_shader_pick_zone", 0) or 0)
    if obj is None or select_mode(scene) != "ZONE" or not zone or asset_root(obj) is obj:
        return None
    if (getattr(scene, "arc_shader_pick_zone_kind", "") or "outfit") not in ("outfit", "weapon"):
        return None
    return asset_root(obj), obj, zone


def zone_offsets_supported(context):
    t = zone_target(context)
    return t is not None and any(user_controls.zone_supported(m) for m in _materials([t[1]]))


def zo_set(context, values):
    """Write {key: value} on the picked part's zone and record them as part tweaks."""
    t = zone_target(context)
    if t is None:
        return 0
    root, part, zone = t
    n = user_controls.zone_offsets_set(_materials([part]), zone, values)
    for key, value in values.items():
        default = user_controls.ZO_BY_KEY[key][4]
        v = list(value)[:3] if isinstance(default, tuple) else float(value)
        d = list(default)[:3] if isinstance(default, tuple) else default
        shader_tweaks.record(root, part_scope(part.name), zo_tweak_name(zone, key), v, d)
    _STATE["self_edit"] = True
    return n


def zo_replay(root):
    """Write every stored zone offset back onto its part (apply_all, load, upgrade)."""
    by_name = {ob.name: ob for ob in [root] + list(getattr(root, "children_recursive", []))}
    for scope, bucket in shader_tweaks.get(root).items():
        if not scope.startswith("part:") or scope[5:] not in by_name:
            continue
        zones = {}
        for name, value in bucket.items():
            hit = _parse_zo(name)
            if hit:
                zones.setdefault(hit[0], {})[hit[1]] = tuple(value) + (1.0,) if isinstance(value, list) else value
        for zone, values in zones.items():
            user_controls.zone_offsets_set(_materials([by_name[scope[5:]]]), zone, values)


def zo_reset(root, part=None, zone=None):
    """Drop zone offset tweaks (all, one part, or one part zone) and set the nodes back to neutral."""
    neutral = {c[0]: c[4] for c in user_controls.ZO_CHANNELS}
    objs = [part] if part is not None else [ob for ob in [root] + list(root.children_recursive)]
    for scope, bucket in list(shader_tweaks.get(root).items()):
        if part is not None and scope != part_scope(part.name):
            continue
        for name in list(bucket):
            hit = _parse_zo(name)
            if hit and (zone is None or hit[0] == zone):
                shader_tweaks.reset(root, scope, name)
    for mat in _materials(objs):
        for z in ([zone] if zone else range(1, 9)):
            if user_controls.zone_node(mat, z) is not None:
                user_controls.zone_offsets_set([mat], z, neutral)
    _STATE["self_edit"] = True


def zo_sync_scene(context):
    """Scene arc_zo_* props follow the picked part zone (neutral when nothing is picked)."""
    scene = context.scene
    t = zone_target(context)
    values = {c[0]: c[4] for c in user_controls.ZO_CHANNELS}
    if t is not None:
        mat = next((m for m in _materials([t[1]]) if user_controls.zone_node(m, t[2]) is not None), None)
        if mat is not None:
            values = user_controls.zone_offsets_get(mat, t[2])
    _ZO_GUARD["on"] = True
    try:
        for key, value in values.items():
            prop = zo_prop(key)
            if not hasattr(scene, prop):
                continue
            if isinstance(value, tuple):
                if max(abs(a - b) for a, b in zip(value[:3], getattr(scene, prop))) > 1e-6:
                    setattr(scene, prop, value[:3])
            elif abs(getattr(scene, prop) - value) > 1e-7:
                setattr(scene, prop, value)
    finally:
        _ZO_GUARD["on"] = False


def _zo_prop_update(key):
    def update(self, context):
        if _ZO_GUARD["on"] or context.active_object is None:
            return
        v = getattr(self, zo_prop(key))
        zo_set(context, {key: (tuple(v) + (1.0,)) if user_controls.ZO_BY_KEY[key][3] in user_controls.ZO_COLOR_KINDS else float(v)})
    return update


def _register_zo_props():
    for key, _b, label, kind, default, lo, hi, _c in user_controls.ZO_CHANNELS:
        if kind in user_controls.ZO_COLOR_KINDS:
            prop = bpy.props.FloatVectorProperty(
                name=label, description=("Multiplies the zone's base colour; white is no change" if kind == "tint" else "Added to the zone's emission colour; black is no change"),
                subtype="COLOR", size=3, min=0.0, max=1.0, default=default[:3], update=_zo_prop_update(key))
        else:
            what = "Multiplies" if kind == "mul" else "Added to"
            prop = FloatProperty(
                name=label, description=f"{what} the zone's {label.lower()}; {default:g} is no change",
                default=default, soft_min=lo, soft_max=hi, update=_zo_prop_update(key))
        setattr(bpy.types.Scene, zo_prop(key), prop)


def _unregister_zo_props():
    for key in user_controls.ZO_BY_KEY:
        if hasattr(bpy.types.Scene, zo_prop(key)):
            delattr(bpy.types.Scene, zo_prop(key))


def _uc_reset_whole(root):
    for scope in list(shader_tweaks.get(root)):
        for sock in user_controls.SOCKETS:
            shader_tweaks.reset(root, scope, sock)
    if _uc_weapon(root):
        from ..Weapons import weapon_import
        weapon_import.set_assembly_wear(root)
    uc_apply(root, uc_values(root))


class ARC_OT_shader_reset_outfit(bpy.types.Operator):
    bl_idname = "arc.shader_reset_outfit"
    bl_label = "Reset Outfit"
    bl_description = "Return every part, zone and slider of the asset to the game look"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        root = asset_root(obj)
        zo_reset(root)
        _restore_defaults(obj, True)
        _uc_reset_whole(root)
        shader_tweaks.reset(root)
        apply_all(obj)
        _resync(context)
        uc_sync_scene(context)
        zo_sync_scene(context)
        return {"FINISHED"}


class ARC_OT_shader_reset_part(bpy.types.Operator):
    bl_idname = "arc.shader_reset_part"
    bl_label = "Reset Part"
    bl_description = "Return the active part to the whole-outfit look (its zones, sliders and colours)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and asset_root(obj) is not obj

    def execute(self, context):
        obj = context.active_object
        root = asset_root(obj)
        zo_reset(root, obj)
        _restore_defaults(obj, False)
        shader_tweaks.reset(root, part_scope(obj.name))
        apply_all(obj)
        _resync(context)
        uc_sync_scene(context)
        zo_sync_scene(context)
        return {"FINISHED"}


def _cold_snap_update(self, context):
    """Cold Snap exposure time -> the game's Snow and Wetness values on the whole asset."""
    if _UC_GUARD["on"] or context.active_object is None:
        return
    from . import cold_snap
    snow, wet = cold_snap.state(self.arc_cold_snap_outdoors, self.arc_cold_snap_sheltered)
    uc_set(_uc_root(context.active_object), {"arc_uc_snow": snow, "arc_uc_wet": wet})
    uc_sync_scene(context)


def _register_uc_props():
    from . import cold_snap
    bpy.types.Scene.arc_cold_snap_outdoors = FloatProperty(
        name="Outdoors", description="Seconds spent outdoors in a Cold Snap; snow is full after 45 s",
        default=0.0, min=0.0, soft_max=cold_snap.COVERED_IN_SNOW_DURATION, subtype="TIME_ABSOLUTE", unit="TIME_ABSOLUTE",
        update=_cold_snap_update)
    bpy.types.Scene.arc_cold_snap_sheltered = FloatProperty(
        name="Sheltered", description="Seconds spent indoors afterwards; snow melts in 30 s, then the outfit dries",
        default=0.0, min=0.0, soft_max=cold_snap.SNOW_MELT_DURATION + cold_snap.WARMUP_DURATION,
        subtype="TIME_ABSOLUTE", unit="TIME_ABSOLUTE", update=_cold_snap_update)
    for sock, prop, label, default, lo, hi in user_controls.CONTROLS:
        setattr(bpy.types.Scene, prop, FloatProperty(
            name=label, default=default, soft_min=lo, soft_max=hi, update=_uc_prop_update(prop)))
    setattr(bpy.types.Scene, _UC_TINT, bpy.props.FloatVectorProperty(
        name="Tint", description="Colour wheel tint: white is no tint, the rim is a full recolour",
        subtype="COLOR", size=3, min=0.0, max=1.0, default=(1.0, 1.0, 1.0), update=_uc_tint_update))


def _unregister_uc_props():
    for prop in tuple(user_controls.BY_PROP) + (_UC_TINT, "arc_cold_snap_outdoors", "arc_cold_snap_sheltered"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)


def _pick_module():
    """shader_pick (pick_shader_zone, drag_decal, set_zone_colour): registered with the Shader Control panel."""
    from . import shader_pick
    return shader_pick


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            bpy.utils.unregister_class(cls)
            bpy.utils.register_class(cls)
    bpy.types.Scene.arc_shader_mode = EnumProperty(
        name="Mode", items=(("BASIC", "Basic", ""), ("PRO", "Pro", "")), default="BASIC")
    bpy.types.Scene.arc_shader_scope = EnumProperty(
        name="Scope", items=(("WHOLE", "Whole outfit", ""), ("PART", "This part", "")), default="WHOLE",
        update=_scope_update)
    bpy.types.Scene.arc_shader_items = bpy.props.CollectionProperty(type=ArcShaderControlItem)
    bpy.types.Scene.arc_colourways = bpy.props.CollectionProperty(type=ArcColourwayItem)
    bpy.types.Scene.arc_colourway_index = IntProperty(default=0)
    bpy.types.Scene.arc_colourway_name = StringProperty(name="Name", default="Colourway")
    bpy.types.Scene.arc_shader_search = StringProperty(name="Search", default="", options={"TEXTEDIT_UPDATE"})
    bpy.types.Scene.arc_shader_edited_only = BoolProperty(name="Edited only", default=False)
    bpy.types.Scene.arc_shader_collapsed = StringProperty(default="")
    bpy.types.Scene.arc_shader_pick_material = StringProperty(default="")
    bpy.types.Scene.arc_shader_pick_zone = IntProperty(default=0)
    bpy.types.Scene.arc_shader_pick_zone_kind = StringProperty(default="")
    bpy.types.Scene.arc_shader_pick_decal = IntProperty(default=0)
    bpy.types.Scene.arc_shader_select_mode = EnumProperty(
        name="Edit", description="What the colour edits below change", items=SELECT_MODES, default="WHOLE",
        update=_select_mode_update)
    for cls in (ARC_OT_user_controls_set, ARC_OT_user_controls_reset, ARC_OT_shader_zone_reset,
                ARC_OT_shader_reset_outfit, ARC_OT_shader_reset_part):
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            bpy.utils.unregister_class(cls)
            bpy.utils.register_class(cls)
    _register_uc_props()
    _register_zo_props()
    _pick_module().register()
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    _subscribe()
    if not bpy.app.timers.is_registered(_safe_refresh):
        bpy.app.timers.register(_safe_refresh, first_interval=0.0)
    _safe_refresh()


def unregister():
    bpy.msgbus.clear_by_owner(_MSGBUS_OWNER)
    if bpy.app.timers.is_registered(_safe_refresh):
        bpy.app.timers.unregister(_safe_refresh)
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    for attr in ("arc_shader_select_mode", "arc_shader_pick_decal", "arc_shader_pick_zone", "arc_shader_pick_zone_kind", "arc_shader_pick_material", "arc_shader_search", "arc_shader_edited_only", "arc_shader_collapsed", "arc_colourways", "arc_colourway_index", "arc_colourway_name", "arc_shader_items", "arc_shader_mode", "arc_shader_scope"):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
    try:
        _pick_module().unregister()
    except RuntimeError:
        pass
    _unregister_uc_props()
    _unregister_zo_props()
    for cls in (ARC_OT_shader_reset_part, ARC_OT_shader_reset_outfit, ARC_OT_shader_zone_reset,
                                   ARC_OT_user_controls_reset, ARC_OT_user_controls_set):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _CACHE.clear()
