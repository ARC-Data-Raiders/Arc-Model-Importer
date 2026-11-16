"""Sidebar list data, small sidebar operators and the scene inventory.

Lists are filled outside draw: draw asks for a sync with a cheap signature,
and a one-shot timer rebuilds the WindowManager collection when it changed.
"""

import json
import re

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Menu, Operator, PropertyGroup, UIList


# ---------------------------------------------------------------- pure rows

_ANIM_LATE = ("dialogue", "menu", "frontend")


def outfit_rows(items, query, pretty=lambda text: text):
    """(key, text, available) rows matching the shown name; unavailable last, order otherwise kept."""
    query = (query or "").casefold()
    rows, total = [], 0
    for key, label, *_ in items:
        if key == "NONE":
            continue
        total += 1
        text = pretty(label)
        if query and query not in text.casefold():
            continue
        rows.append((key, text, "(mesh unavailable)" not in label))
    rows.sort(key=lambda row: not row[2])
    return rows, total


def _folder(label, key):
    parts = [part.strip() for part in label.split("·")]
    if len(parts) > 1 and parts[1]:
        return parts[1]
    bits = str(key).replace("\\", "/").rstrip("/").split("/")
    return bits[-2] if len(bits) > 1 else ""


def named_rows(entries, label_of, pretty, *, late_words=(), keep_order=True):
    """(key, text, True) rows with full names; duplicate names get " (folder)"."""
    raw = []
    for entry in entries:
        key = entry.get("key") or ""
        label = label_of(entry)
        raw.append((key, pretty(label), _folder(label, key)))
    if late_words:
        raw.sort(key=lambda row: any(word in (row[2] + " " + row[0]).casefold() for word in late_words))
    counts = {}
    for _key, text, _folder_name in raw:
        counts[text] = counts.get(text, 0) + 1
    rows = []
    for key, text, folder in raw:
        if counts[text] > 1 and folder:
            text = "%s (%s)" % (text, folder)
        rows.append((key, text, True))
    return rows


def count_text(shown, total, noun):
    if not shown:
        return ""
    if total and shown < total:
        return "%d of %d match" % (shown, total)
    return "%d %s" % (shown, noun)


# ---------------------------------------------------------------- collections

class ARC_PG_list_row(PropertyGroup):
    key: StringProperty()
    label: StringProperty()
    available: BoolProperty(default=True)
    kind: StringProperty()


_SYNCING = False


def _apply_outfit(self, context):
    rows, index = self.arc_outfit_rows, self.arc_outfit_rows_index
    if _SYNCING or not 0 <= index < len(rows):
        return
    scene = context.scene
    if scene.arc_selected_outfit != rows[index].key:
        scene.arc_selected_outfit = rows[index].key


def _apply_anim(self, context):
    rows, index = self.arc_anim_rows, self.arc_anim_rows_index
    if _SYNCING or not 0 <= index < len(rows):
        return
    context.scene.arc_selected_anim = rows[index].key


def _apply_fx(self, context):
    rows, index = self.arc_fx_rows, self.arc_fx_rows_index
    if _SYNCING or not 0 <= index < len(rows):
        return
    # List selection only picks the effect; Settings > Source > Effect File stays untouched.
    context.scene.arc_ns_selected = rows[index].key


def _apply_weapon(self, context):
    rows, index = self.arc_weapon_rows, self.arc_weapon_rows_index
    if _SYNCING or not 0 <= index < len(rows):
        return
    if context.scene.arc_weapon_key != rows[index].key:
        context.scene.arc_weapon_browser_key = rows[index].key


def _apply_enemy(self, context):
    rows, index = self.arc_enemy_rows, self.arc_enemy_rows_index
    if _SYNCING or not 0 <= index < len(rows):
        return
    context.scene.arc_enemy_key = rows[index].key


_LISTS = {
    "outfit": ("arc_outfit_rows", "arc_outfit_rows_index"),
    "anim": ("arc_anim_rows", "arc_anim_rows_index"),
    "fx": ("arc_fx_rows", "arc_fx_rows_index"),
    "weapon": ("arc_weapon_rows", "arc_weapon_rows_index"),
    "enemy": ("arc_enemy_rows", "arc_enemy_rows_index"),
    "scene": ("arc_scene_rows", "arc_scene_rows_index"),
}
_LAST = {}
_PENDING = {}
_INDEX = {}
_SELECTED = {}


def rows_of(context, kind):
    wm = getattr(context, "window_manager", None)
    return getattr(wm, _LISTS[kind][0], ()) if wm is not None else ()


_CURRENT_BUILD = {}


def request_sync(kind, signature, build, selected=""):
    """Draw-safe. build() returns [(key, text, available)]; runs later in a timer."""
    selected = selected or ""
    _CURRENT_BUILD[kind] = build  # rows for the query drawn now, before the timer fills the list
    stale = _LAST.get(kind) != signature
    if stale and _PENDING.get(kind, (None,))[0] != signature:
        _PENDING[kind] = (signature, build)
    if stale or _SELECTED.get(kind) != selected:
        # Draw runs every redraw; touch the timer only when something changed.
        _INDEX[kind] = selected
        _schedule()


def _schedule():
    if not bpy.app.timers.is_registered(_flush):
        bpy.app.timers.register(_flush, first_interval=0.0)


def _fill(wm, kind, rows):
    coll_name, _index_name = _LISTS[kind]
    coll = getattr(wm, coll_name)
    coll.clear()
    for row in rows:
        key, text, available = row[:3]
        item = coll.add()
        item.key, item.label, item.available = key, text, bool(available)
        item.kind = row[3] if len(row) > 3 else ""


def _set_index(wm, kind, selected):
    coll_name, index_name = _LISTS[kind]
    coll = getattr(wm, coll_name)
    want = next((i for i, item in enumerate(coll) if item.key == selected), -1) if selected else -1
    if getattr(wm, index_name) != want:
        setattr(wm, index_name, want)
        return True
    return False


def _flush():
    global _SYNCING
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None or not hasattr(wm, "arc_outfit_rows"):
        return None
    changed = False
    _SYNCING = True
    try:
        for kind, (signature, build) in list(_PENDING.items()):
            _PENDING.pop(kind, None)
            try:
                rows = build()
            except Exception as exc:
                print("Arc lists: %s rows failed: %s" % (kind, exc))
                continue
            _fill(wm, kind, rows)
            _LAST[kind] = signature
            changed = True
        for kind, selected in list(_INDEX.items()):
            changed = _set_index(wm, kind, selected) or changed
            _SELECTED[kind] = selected
        _INDEX.clear()
    finally:
        _SYNCING = False
    if changed:
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    return None


@persistent
def _on_load(_dummy=None):
    _LAST.clear()
    _PENDING.clear()
    _INDEX.clear()
    _SELECTED.clear()
    _INVENTORY.clear()
    _IMPORTS.clear()


class ARC_UL_rows(UIList):
    bl_idname = "ARC_UL_rows"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0, flt_flag=0):
        row = layout.row(align=True)
        text = row.row()
        text.enabled = item.available
        text.label(text=item.label)
        kind = _LIST_KIND.get(active_propname, "")
        if kind:
            draw_star(row, kind, item.key)

    def filter_items(self, context, data, propname):
        kind = _LIST_KIND.get(propname, "")
        mode = filter_mode(context, kind) if kind else "ALL"
        keys = filter_keys(kind, mode) if kind else None
        items = getattr(data, propname)
        alpha = bool(getattr(self, "use_filter_sort_alpha", False))
        if kind == "anim" and mode != "RECENT":
            alpha = True  # animations always list A-Z
        if keys is None and not alpha:
            return [], []
        flag = self.bitflag_filter_item
        rank = {key: i for i, key in enumerate(keys)} if keys is not None else {}
        flags = [flag if keys is None or item.key in rank else 0 for item in items]
        if alpha:
            order = sorted(range(len(items)), key=lambda i: items[i].label.casefold())
        elif mode == "RECENT":
            order = sorted(range(len(items)), key=lambda i: rank.get(items[i].key, len(rank) + i))
        else:
            return flags, []
        neworder = [0] * len(items)
        for pos, idx in enumerate(order):
            neworder[idx] = pos
        return flags, neworder

    def draw_filter(self, context, layout):
        # Search lives above the list; the list itself only offers A-Z order.
        row = layout.row(align=True)
        row.prop(self, "use_filter_sort_alpha", text="Sort A-Z", toggle=True, icon="SORTALPHA")


class ARC_UL_scene_rows(UIList):
    """In This Scene: one row per import with Select, Hide/Show and Remove."""
    bl_idname = "ARC_UL_scene_rows"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0, flt_flag=0):
        row = layout.row(align=True)
        name = row.row()
        name.enabled = item.available
        # The icon names the kind (same icons as the panel sections); no separate kind column.
        name.label(text=item.label, icon=kind_icon(item.kind))
        select = row.operator("arc_outfits.scene_row_select", text="", icon="RESTRICT_SELECT_OFF", emboss=False)
        select.import_id = item.key
        frame = row.operator("arc_outfits.scene_row_select", text="", icon="ZOOM_SELECTED", emboss=False)
        frame.import_id = item.key
        frame.frame = True
        eye = row.operator("arc_outfits.scene_row_visibility", text="",
                           icon="HIDE_OFF" if item.available else "HIDE_ON", emboss=False)
        eye.import_id = item.key
        row.operator("arc_outfits.scene_row_remove", text="", icon="X", emboss=False).import_id = item.key

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        needle = (getattr(self, "filter_name", "") or "").casefold()
        flag = self.bitflag_filter_item
        flags = [flag if not needle or needle in item.label.casefold() else 0 for item in items]
        if not getattr(self, "use_filter_sort_alpha", False):
            return flags, []
        order = sorted(range(len(items)), key=lambda i: items[i].label.casefold())
        neworder = [0] * len(items)
        for pos, idx in enumerate(order):
            neworder[idx] = pos
        return flags, neworder

    def draw_filter(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "filter_name", text="", icon="VIEWZOOM")
        row.prop(self, "use_filter_sort_alpha", text="", toggle=True, icon="SORTALPHA")


# ---------------------------------------------------------------- roles and inventory

_KIND_ORDER = ("outfit", "part", "weapon", "enemy", "effect")
_KIND_ICON = {"outfit": "USER", "part": "MOD_CLOTH", "weapon": "TOOL_SETTINGS",
              "enemy": "GHOST_ENABLED", "effect": "PARTICLES"}
_PART_ROLES = ("Head", "Hair", "FacialHair", "Backpack", "BackpackFrame", "BackpackStrap",
               "Attachment", "Charm", "RaiderTool")
_INVENTORY = {}


def asset_root(obj):
    while obj is not None and obj.parent is not None:
        obj = obj.parent
    return obj


def object_kind(obj):
    if obj.get("arc_weapon_assembly") or obj.get("arc_weapon_owner"):
        return "weapon"
    if obj.get("arc_codex_enemy_key"):
        return "enemy"
    if obj.get("arc_prop_fx") or obj.get("arc_ns_asset"):
        return "effect"
    role = obj.get("arc_assembly_role") or ""
    if role in _PART_ROLES and obj.parent is None:
        return "part"
    if obj.get("arc_psk_path") or role:
        return "outfit"
    return ""


def _kind_of(kind):
    """ui_targets import kinds -> list kinds."""
    kind = (kind or "").lower()
    if kind in ("character", "outfit"):
        return "outfit"
    if kind in ("hair", "facial_hair", "backpack", "part", "head", "attachment", "charm", "tool"):
        return "part"
    return kind if kind in _KIND_ICON else "outfit"


def scene_entries(scene):
    """[(row key, label, visible, kind)] newest import first; unstamped roots after. Reads only."""
    rows, seen = [], set()
    try:
        from . import ui_targets
        entries = ui_targets.import_entries(scene)
        root_of = getattr(ui_targets, "_entry_root", None)
        name_of = getattr(ui_targets, "display_name", None)
    except Exception:
        entries, root_of, name_of = [], None, None
    for entry in entries:
        objects = list(entry.get("objects") or ())
        if not objects:
            continue
        seen.update(obj.name for obj in objects)
        root = root_of(objects) if callable(root_of) else objects[0]
        label = entry.get("label") or (name_of(root) if callable(name_of) and root else "") or (root.name if root else "Import")
        visible = any(_visible(obj) for obj in objects)
        rows.append(("id:" + str(entry.get("id")), str(label), visible, _kind_of(entry.get("kind"))))
    legacy = [obj for obj in scene.objects if obj.name not in seen]
    for name, kind in scene_roots(legacy):
        root = scene.objects.get(name)
        if root is None:
            continue
        label = name_of(root) if callable(name_of) else name
        rows.append(("root:" + name, str(label or name), _visible(root), kind))
    return rows


def _visible(obj):
    try:
        return not obj.hide_get()
    except Exception:
        return not getattr(obj, "hide_viewport", False)


def entry_objects(scene, row_key):
    """Objects behind one In This Scene row key."""
    if row_key.startswith("id:"):
        try:
            from . import ui_targets
            return [obj for obj in ui_targets.objects_of(scene, row_key[3:]) if obj.name in scene.objects]
        except Exception:
            return []
    root = scene.objects.get(row_key[5:]) if row_key.startswith("root:") else None
    if root is None:
        return []
    return [root] + [obj for obj in root.children_recursive if obj.name in scene.objects]


def request_scene_sync(scene):
    """Draw-safe (callers rate-limit it): refresh In This Scene rows when imports or visibility change."""
    rows = scene_entries(scene)
    signature = (scene.name, tuple(rows))
    request_sync("scene", signature, lambda: rows)
    return len(rows)


def scene_roots(objects):
    """[(root name, kind)] in a stable order, one per imported root."""
    found = {}
    for obj in objects:
        kind = object_kind(obj)
        if not kind:
            continue
        root = asset_root(obj)
        old = found.get(root.name)
        if old is None or _KIND_ORDER.index(kind) > _KIND_ORDER.index(old):
            found[root.name] = kind
    return sorted(found.items(), key=lambda item: (_KIND_ORDER.index(item[1]), item[0].casefold()))


def _generation():
    """ui_targets.import_generation() or None; renames and replaces then refresh memos."""
    try:
        from . import ui_targets
        gen = getattr(ui_targets, "import_generation", None)
        return gen() if gen is not None else None
    except Exception:
        return None


def inventory(context):
    """Cached per object count; reads only."""
    scene = context.scene
    key = (scene.name, len(bpy.data.objects), _generation())
    hit = _INVENTORY.get("roots")
    if hit is None or hit[0] != key:
        hit = _INVENTORY["roots"] = (key, scene_roots(scene.objects))
    return hit[1]


def inventory_text(roots):
    if not roots:
        return "Nothing imported yet"
    counts = {}
    for _name, kind in roots:
        counts[kind] = counts.get(kind, 0) + 1
    words = {"outfit": ("outfit", "outfits"), "weapon": ("weapon", "weapons"), "enemy": ("enemy", "enemies")}
    parts = ["%d %s" % (counts[k], words[k][counts[k] != 1]) for k in _KIND_ORDER if counts.get(k)]
    return "In scene: " + ", ".join(parts)


def kind_icon(kind):
    return _KIND_ICON.get(kind, "OBJECT_DATA")


def _member_role(obj, part):
    if part in ("HAIR", "FACIAL_HAIR"):
        try:
            from ..Outfit_Accessories import appearance
            role = appearance.hair_object_role(obj)
        except Exception:
            role = ""
        return role == ("FacialHair" if part == "FACIAL_HAIR" else "Hair")
    if part == "BACKPACK":
        return str(obj.get("arc_assembly_role") or "") in ("Backpack", "BackpackStrap")
    return False


_PART_ROLES = {"HAIR": "hair", "FACIAL_HAIR": "facial_hair", "BACKPACK": "backpack"}


def _resolve(context, role):
    """ui_targets.resolve(context, role) or None; fails closed."""
    try:
        from . import ui_targets
        return ui_targets.resolve(context, role)
    except Exception:
        return None


def part_target(context, part):
    """Display name of the imported part of this kind the buttons act on, or ""."""
    target = _resolve(context, _PART_ROLES.get(part, str(part or "").lower()))
    return target.name if target is not None else ""


def outfit_target(context):
    """Display name of the character the buttons act on, or ""."""
    target = _resolve(context, "character")
    return target.name if target is not None else ""


def weapon_target(context):
    """(display name, last_imported) of the weapon the edit buttons act on."""
    target = _resolve(context, "weapon")
    if target is None:
        return "", False
    return target.name, target.source == "last_import"


# ---------------------------------------------------------------- operators and menus

class ARC_OUTFITS_OT_select_scene_root(Operator):
    bl_idname = "arc_outfits.select_scene_root"
    bl_label = "Select Imported Asset"
    bl_description = "Select this imported asset and make it active"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    name: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        obj = context.scene.objects.get(self.name)
        if obj is None:
            self.report({"WARNING"}, "This asset is no longer in the scene")
            return {"CANCELLED"}
        for other in context.selected_objects:
            other.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}


class ARC_OUTFITS_OT_cancel_all_outfits_colorways(Operator):
    bl_idname = "arc_outfits.cancel_all_outfits_colorways"
    bl_label = "Cancel"
    bl_description = "Stop importing all outfits after the current one. Finished imports stay"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        from . import operators
        active = getattr(operators, "_ALL_OUTFITS_COLORWAYS_ACTIVE", None)
        if active is None:
            return {"CANCELLED"}
        active._cancel_requested = True
        return {"FINISHED"}


class ARC_OUTFITS_MT_outfit_import_more(Menu):
    bl_idname = "ARC_OUTFITS_MT_outfit_import_more"
    bl_label = "More Import Options"

    def draw(self, context):
        layout = self.layout
        layout.operator("arc_outfits.load_all_colorways", text="Import This Outfit in All Colorways...", icon="DUPLICATE")
        try:
            op = layout.operator("arc_outfits.load_outfit", text="Choose Per-Part Skins...", icon="MATERIAL")
            op.choose_skins = True
        except Exception:
            pass
        layout.operator("arc_outfits.import_reference", text="Import Reference Image...", icon="IMAGE_DATA")


class _RowOp:
    import_id: StringProperty(options={"HIDDEN"})

    def _objects(self, context):
        return entry_objects(context.scene, self.import_id or "")


class ARC_OUTFITS_OT_scene_row_select(_RowOp, Operator):
    bl_idname = "arc_outfits.scene_row_select"
    bl_label = "Select"
    bl_description = "Select this import and make it active. Hidden parts are shown first"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    frame: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def description(cls, context, properties):
        if getattr(properties, "frame", False):
            return "Select this import and frame it in the viewport"
        return cls.bl_description

    def execute(self, context):
        objects = self._objects(context)
        if not objects:
            self.report({"WARNING"}, "This asset is no longer in the scene")
            return {"CANCELLED"}
        layer_objects = context.view_layer.objects
        objects = [obj for obj in objects if layer_objects.get(obj.name) is obj]
        if not objects:
            self.report({"WARNING"}, "This import is in a collection excluded from the view layer")
            return {"CANCELLED"}
        for other in context.selected_objects:
            other.select_set(False)
        for obj in objects:
            try:
                obj.hide_set(False)
                obj.select_set(True)
            except RuntimeError:
                continue
        try:
            from . import ui_targets
            root = ui_targets._entry_root(objects)
        except Exception:
            root = objects[0]
        layer_objects.active = root
        if self.frame:
            self._frame(context)
        return {"FINISHED"}

    @staticmethod
    def _frame(context):
        screen = getattr(context, "screen", None)
        for area in getattr(screen, "areas", ()) or ():
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is None:
                continue
            try:
                with context.temp_override(area=area, region=region):
                    bpy.ops.view3d.view_selected()
            except Exception as exc:
                print("Arc lists: frame failed:", exc)
            return


class ARC_OUTFITS_OT_scene_row_visibility(_RowOp, Operator):
    bl_idname = "arc_outfits.scene_row_visibility"
    bl_label = "Hide or Show"
    bl_description = "Hide this import in the viewport, or show it again"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def execute(self, context):
        objects = self._objects(context)
        if not objects:
            return {"CANCELLED"}
        hide = any(_visible(obj) for obj in objects)
        for obj in objects:
            try:
                obj.hide_set(hide)
            except RuntimeError:
                continue
        return {"FINISHED"}


def remove_objects(objects):
    """Delete objects plus meshes/armatures left with no users. Returns the count removed."""
    datas, removed = [], 0
    for obj in list(objects):
        try:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
            if data is not None:
                datas.append(data)
        except (ReferenceError, RuntimeError):
            continue
    for data in datas:
        try:
            if data.users == 0:
                for coll in (bpy.data.meshes, bpy.data.armatures, bpy.data.curves):
                    if data.name in coll and coll[data.name] == data:
                        coll.remove(data)
                        break
        except (ReferenceError, RuntimeError):
            continue
    return removed


def remove_summary(scene, import_id):
    """(label, object count) of one stamped import; ("", 0) when nothing is stamped."""
    try:
        from . import ui_targets
        fn = getattr(ui_targets, "remove_summary", None)
        if fn is not None:
            return fn(scene, import_id)
    except Exception:
        pass
    objects = entry_objects(scene, import_id)
    if not objects:
        return "", 0
    return str(objects[0].get("arc_import_label") or objects[0].name), len(objects)


def remove_import(scene, import_id):
    """Remove one stamped import from scene -> (deleted, unlinked).

    Objects also linked in another scene are unlinked from this scene only. Any error
    fails closed: nothing is removed and (0, 0) is returned (no raw-unlink fallback).
    """
    try:
        from . import ui_targets
        return ui_targets.remove_import(scene, import_id)
    except Exception as exc:
        print("Arc lists: remove_import failed:", exc)
        _REMOVE_ERROR[0] = "Delete failed: %s" % (exc,)
        return 0, 0


_REMOVE_ERROR = [""]


def remove_refusal_text():
    """Why the last remove_import removed nothing: job running, shared collection or error."""
    if _REMOVE_ERROR[0]:
        return _REMOVE_ERROR[0]
    try:
        from . import ui_targets
        return ui_targets.last_remove_refusal()
    except Exception:
        return ""


def remove_message(deleted, unlinked):
    parts = []
    if deleted:
        parts.append("Deleted %d object%s" % (deleted, "" if deleted == 1 else "s"))
    if unlinked:
        parts.append("removed %d shared object%s from this scene only" % (unlinked, "" if unlinked == 1 else "s"))
    return ", ".join(parts) or "Nothing was removed"


def _busy_text():
    try:
        from . import ui_targets
        return ui_targets.job_busy_message()
    except Exception:
        return ""


class _ConfirmRemove:
    def invoke(self, context, event):
        busy = _busy_text()
        if busy:
            self.report({"WARNING"}, busy)
            return {"CANCELLED"}
        label, count = remove_summary(context.scene, self._import_id(context))
        if not count:
            self.report({"WARNING"}, "Nothing to remove")
            return {"CANCELLED"}
        title = 'Delete "%s" (%d object%s)?' % (label, count, "" if count == 1 else "s")
        try:
            return context.window_manager.invoke_confirm(self, event, title=title, message=(
                "Objects also used in other scenes are only removed from this scene. Undo brings them back"),
                confirm_text="Delete", icon="WARNING")
        except TypeError:
            return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        import_id = self._import_id(context)
        if not import_id:
            self.report({"WARNING"}, "Nothing to remove")
            return {"CANCELLED"}
        busy = _busy_text()
        if busy:
            self.report({"WARNING"}, busy)
            return {"CANCELLED"}
        _REMOVE_ERROR[0] = ""
        deleted, unlinked = remove_import(context.scene, import_id)
        if not (deleted or unlinked):
            # A refusal (shared collection, running job, error) is reported as itself.
            self.report({"WARNING"}, remove_refusal_text() or "This asset is no longer in the scene")
            return {"CANCELLED"}
        _INVENTORY.clear()
        _IMPORTS.clear()
        self.report({"INFO"}, remove_message(deleted, unlinked))
        return {"FINISHED"}


class ARC_OUTFITS_OT_scene_row_remove(_ConfirmRemove, _RowOp, Operator):
    bl_idname = "arc_outfits.scene_row_remove"
    bl_label = "Delete Import..."
    bl_description = "Delete this import. Objects also used in other scenes are only removed from this scene"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def _import_id(self, context):
        return self.import_id or ""


_KIND_COLLECTION = {"outfit": "Outfits", "part": "Character Parts", "weapon": "Weapons",
                    "enemy": "ARC Enemies", "effect": "Effects"}


COLLECTION_SORT_ITEMS = (
    ("KIND", "By Import Kind", "One collection per kind: Outfits, Weapons, ARC Enemies and so on"),
    ("ASSET_TYPE", "By Asset Type", "One collection per asset type"),
    ("OUTFIT_SLOT", "By Outfit Slot", "One collection per outfit slot"),
)
_COLLECTION_SORT_OPS = {"ASSET_TYPE": "group_by_asset_type", "OUTFIT_SLOT": "group_by_outfit_slot"}


class ARC_OUTFITS_OT_sort_into_collections(Operator):
    bl_idname = "arc_outfits.sort_into_collections"
    bl_label = "Sort into Collections"
    bl_description = "Move imported objects into collections, grouped as chosen in Settings > Placement"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not context.window_manager.arc_scene_rows:
            cls.poll_message_set("Nothing imported yet")
            return False
        return True

    def execute(self, context):
        scene = context.scene
        mode = getattr(scene, "arc_collection_sort", "KIND") or "KIND"
        op_name = _COLLECTION_SORT_OPS.get(mode)
        if op_name:
            op = getattr(bpy.ops.arc_outfits, op_name, None)
            if op is None:
                self.report({"ERROR"}, "This sort mode is not available")
                return {"CANCELLED"}
            try:
                result = op()
            except RuntimeError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            _INVENTORY.clear()
            return {"FINISHED"} if "FINISHED" in result else {"CANCELLED"}
        moved = 0
        for key, _label, _visible_flag, kind in scene_entries(scene):
            title = _KIND_COLLECTION.get(_kind_of(kind), "Other Imports")
            coll = bpy.data.collections.get(title)
            if coll is None:
                coll = bpy.data.collections.new(title)
            if coll.name not in scene.collection.children:
                try:
                    scene.collection.children.link(coll)
                except RuntimeError:
                    pass
            for obj in entry_objects(scene, key):
                if coll in obj.users_collection:
                    continue
                for old in list(obj.users_collection):
                    old.objects.unlink(obj)
                coll.objects.link(obj)
                moved += 1
        _INVENTORY.clear()
        self.report({"INFO"}, "Moved %d object%s" % (moved, "" if moved == 1 else "s"))
        return {"FINISHED"}


class ARC_OUTFITS_OT_copy_job_report(Operator):
    bl_idname = "arc_outfits.copy_job_report"
    bl_label = "Copy Report"
    bl_description = "Copy the finished import report as text, for pasting into a bug report"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        from . import ui_targets
        text = ui_targets.report_text()
        if not text:
            return {"CANCELLED"}
        context.window_manager.clipboard = text
        self.report({"INFO"}, "Report copied")
        return {"FINISHED"}


class ARC_OUTFITS_OT_dismiss_job_report(Operator):
    bl_idname = "arc_outfits.dismiss_job_report"
    bl_label = "Dismiss Report"
    bl_description = "Hide this finished import report"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        from . import ui_targets
        ui_targets.job_clear_report()
        return {"FINISHED"}


class ARC_OUTFITS_OT_dismiss_error(Operator):
    bl_idname = "arc_outfits.dismiss_error"
    bl_label = "Dismiss"
    bl_description = "Hide this error message"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        from . import ui_targets
        ui_targets.clear_error()
        return {"FINISHED"}


def error_text(err):
    """Plain text of the last error for Copy Error."""
    if not err:
        return ""
    title = str(err.get("title") or "Something went wrong")
    detail = str(err.get("detail") or "")
    return title + ("\n" + detail if detail else "")


class ARC_OUTFITS_OT_copy_error(Operator):
    bl_idname = "arc_outfits.copy_error"
    bl_label = "Copy Error"
    bl_description = "Copy this error as text, for pasting into a bug report"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        from . import ui_targets
        text = error_text(ui_targets.last_error())
        if not text:
            return {"CANCELLED"}
        context.window_manager.clipboard = text
        self.report({"INFO"}, "Error copied")
        return {"FINISHED"}


_DETAIL_TITLES = {"report": "Import Report", "error": "Last Error", "lighting": "Lighting Warnings"}


def details_text(source, scene=None):
    """Full text behind a truncated sidebar box: the job report, the last error or the lighting warnings."""
    from . import ui_targets
    try:
        if source == "report":
            return ui_targets.report_text() or ""
        if source == "error":
            return error_text(ui_targets.last_error()) or ""
        if source == "lighting" and scene is not None:
            from ..Lighting import lighting_looks
            state = lighting_looks.lighting_state(scene) or {}
            return "\n".join(str(w) for w in (state.get("warnings") or ()))
    except Exception:
        return ""
    return ""


class ARC_OUTFITS_OT_view_details(Operator):
    bl_idname = "arc_outfits.view_details"
    bl_label = "View Details"
    bl_description = "Show the full text in a popup, with a button to copy it"
    bl_options = {"REGISTER", "INTERNAL"}

    source: StringProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        if not details_text(self.source, context.scene):
            self.report({"INFO"}, "Nothing to show")
            return {"CANCELLED"}
        return context.window_manager.invoke_popup(self, width=520)

    def draw(self, context):
        import textwrap
        layout = self.layout
        layout.label(text=_DETAIL_TITLES.get(self.source, "Details"), icon="INFO")
        col = layout.column(align=True)
        lines = details_text(self.source, context.scene).splitlines()
        for line in lines[:60]:
            for part in textwrap.wrap(line, 90) or [""]:
                col.label(text=part)
        if len(lines) > 60:
            col.label(text="%d more lines, copy to see all" % (len(lines) - 60))
        op = layout.operator(self.bl_idname, text="Copy", icon="COPYDOWN")
        op.source = self.source

    def execute(self, context):
        text = details_text(self.source, context.scene)
        if not text:
            return {"CANCELLED"}
        context.window_manager.clipboard = text
        self.report({"INFO"}, "Copied")
        return {"FINISHED"}


_SEARCH_PROPS = ("arc_outfit_search", "arc_anim_search", "arc_ns_search", "arc_weapon_search", "arc_enemy_search")


class ARC_OUTFITS_OT_clear_search(Operator):
    bl_idname = "arc_outfits.clear_search"
    bl_label = "Clear Search"
    bl_description = "Empty the search field and show every row"
    bl_options = {"REGISTER", "INTERNAL"}

    prop: StringProperty(options={"HIDDEN"})
    kind: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        scene = context.scene
        if self.prop in _SEARCH_PROPS and hasattr(scene, self.prop):
            setattr(scene, self.prop, "")
        if self.prop == "arc_anim_search" and getattr(scene, "arc_anim_kind_filter", "ALL") != "ALL":
            scene.arc_anim_kind_filter = "ALL"
        _reset_filter(context, self.kind)
        return {"FINISHED"}


def _reset_filter(context, kind):
    """Set only this browser's Favorites/Recent filter back to All."""
    if kind not in FAV_KINDS:
        return
    wm = context.window_manager
    if getattr(wm, "arc_filter_" + kind, "ALL") != "ALL":
        setattr(wm, "arc_filter_" + kind, "ALL")


class ARC_OUTFITS_OT_show_all_filter(Operator):
    bl_idname = "arc_outfits.show_all_filter"
    bl_label = "Show All"
    bl_description = "Show every row of this list again. The search stays as it is"
    bl_options = {"REGISTER", "INTERNAL"}

    kind: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        _reset_filter(context, self.kind)
        return {"FINISHED"}


def outfit_is_visible(context, key, rows=None):
    """True when outfit key is a row shown by the outfit search and Favorites/Recent filter.

    rows: the rows the current query matches, as (key, ...) tuples. Draw passes them so the
    answer follows the query now, not the list rows the sync timer fills in later."""
    key = str(key or "")
    if not key:
        return False
    if rows is None:
        # No rows passed (operator poll): use the rows of the query last drawn, not the
        # list the sync timer fills later.
        build = (globals().get("_CURRENT_BUILD") or {}).get("outfit")
        if build is not None:
            try:
                rows = list(build())
            except Exception:
                rows = None
    if rows is not None:
        if not any(str(r[0]) == key for r in rows):
            return False
    else:
        wm = getattr(context, "window_manager", None)
        synced = getattr(wm, "arc_outfit_rows", None)
        if synced is None:
            return True
        if not any(r.key == key for r in synced):
            return False
    mode = filter_mode(context, "outfit")
    keys = filter_keys("outfit", mode)
    return keys is None or key in keys


# What -> (operator id, part or "")
BATCH_KINDS = (
    ("OUTFITS", "Outfits and Colorways", "Every outfit in every colorway. Can take an hour or more", "arc_outfits.load_all_outfits_colorways", ""),
    ("HEADS", "Heads", "Every head", "arc_outfits.add_all_appearance", "HEAD"),
    ("HAIR", "Hair", "Every hairstyle", "arc_outfits.add_all_appearance", "HAIR"),
    ("FACIAL_HAIR", "Facial Hair", "Every beard and moustache", "arc_outfits.add_all_appearance", "FACIAL_HAIR"),
    ("BACKPACKS", "Backpacks", "Every backpack", "arc_outfits.add_all_appearance", "BACKPACK"),
    ("ATTACHMENTS", "Backpack Attachments", "Every backpack attachment", "arc_outfits.add_all_appearance", "ATTACHMENT"),
    ("CHARMS", "Charms", "Every charm", "arc_outfits.add_all_appearance", "CHARM"),
    ("TOOLS", "Raider Tools", "Every raider tool", "arc_outfits.add_all_appearance", "RAIDER_TOOL"),
    ("WEAPONS", "Weapons", "Every weapon", "arc_outfits.add_all_weapons", ""),
    ("ENEMIES", "Enemies", "Every ARC enemy. Blender is busy until it ends", "arc_outfits.add_all_arc", ""),
)
_BATCH = {row[0]: row for row in BATCH_KINDS}


_BATCH_COUNT = {}
_BATCH_PART_ITEMS = {
    "HEAD": "make_head_enum_items", "HAIR": "make_hair_enum_items", "FACIAL_HAIR": "make_facial_hair_enum_items",
    "BACKPACK": "make_backpack_enum_items", "ATTACHMENT": "make_attachment_enum_items",
    "CHARM": "make_charm_enum_items", "RAIDER_TOOL": "make_raider_tool_enum_items",
}


def batch_source_names(kind):
    """Display names of the items a batch of this kind will import; [] when unknown."""
    row = _BATCH.get(kind)
    if row is None:
        return []
    try:
        if kind == "WEAPONS":
            from ..Weapons import weapon_catalog
            items = weapon_catalog.weapon_enum_items()
        elif kind == "ENEMIES":
            from ..Arc import arc_catalog_data
            items = arc_catalog_data.enum_items(None, bpy.context)
        else:
            if kind == "OUTFITS":
                # Colorway counts are only known per outfit; the outfit count is the floor.
                from ..Outfit_Accessories import properties
                fn = getattr(properties, "make_outfit_preset_items", None)
            else:
                from ..Outfit_Accessories import appearance
                fn = getattr(appearance, _BATCH_PART_ITEMS.get(row[4], ""), None)
            if fn is None:
                return []
            items = fn(None, bpy.context)
        return [str(item[1] or item[0]) for item in items if item and item[0] not in ("", "NONE")]
    except Exception:
        return []


def _batch_source_count(kind):
    """How many imports a batch of this kind will start; 0 when unknown."""
    return len(batch_source_names(kind))


def batch_count(kind, scene=None):
    """Planned import count for the batch kind, or None while unknown.

    Uses the real job plan (ui_targets.batch_plan_count) when present; it recounts when the
    source or catalog changes. The local fallback caches only a found count, never 0 or unknown."""
    try:
        from . import ui_targets
        plan_fn = getattr(ui_targets, "batch_plan_count", None)
    except Exception:
        plan_fn = None
    if callable(plan_fn):
        if scene is None:
            scene = getattr(bpy.context, "scene", None)
        return plan_fn(scene, kind)
    if _BATCH_COUNT.get(kind):
        return _BATCH_COUNT[kind]
    count = _batch_source_count(kind)
    if count:
        _BATCH_COUNT[kind] = count
        return count
    return None


def batch_hint(kind, count=None):
    row = _BATCH.get(kind)
    if not row:
        return ""
    if count is None:
        return row[2]
    if count == 0:
        return "Nothing to import. " + row[2]
    if count == 1:
        return "1 planned import. " + row[2]
    return "%d planned imports. %s" % (count, row[2])


def _job_running():
    try:
        from . import ui_targets
        if ui_targets.job_active():
            return True
    except Exception:
        pass
    try:
        from . import operators
        if getattr(operators, "_ALL_OUTFITS_COLORWAYS_ACTIVE", None) is not None:
            return True
    except Exception:
        pass
    return False


class ARC_OUTFITS_OT_review_batch_plan(Operator):
    bl_idname = "arc_outfits.review_batch_plan"
    bl_label = "Review Plan"
    bl_description = "List every item the batch will import, before you start it"
    bl_options = {"REGISTER", "INTERNAL"}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=420)

    def draw(self, context):
        kind = getattr(context.scene, "arc_batch_kind", "OUTFITS")
        row = _BATCH.get(kind)
        layout = self.layout
        names = batch_source_names(kind)
        layout.label(text="Import All %s" % (row[1] if row else "Items"), icon="IMPORT")
        if not names:
            layout.label(text="Nothing to import", icon="INFO")
            return
        layout.label(text="%d planned imports" % len(names) if len(names) != 1 else "1 planned import")
        grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
        for name in names[:80]:
            grid.label(text=name[:40], icon="DOT")
        if len(names) > 80:
            layout.label(text="and %d more" % (len(names) - 80))
        layout.operator("arc_outfits.start_batch_import", text="Start Batch Import...", icon="IMPORT")

    def execute(self, context):
        return {"FINISHED"}


class ARC_OUTFITS_OT_start_batch_import(Operator):
    bl_idname = "arc_outfits.start_batch_import"
    bl_label = "Start Batch Import..."
    bl_description = "Import every item of the chosen kind into this file"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context):
        if _job_running():
            cls.poll_message_set("Wait for the current import to finish")
            return False
        return True

    def invoke(self, context, event):
        kind = getattr(context.scene, "arc_batch_kind", "OUTFITS")
        row = _BATCH.get(kind)
        if row is None:
            return {"CANCELLED"}
        hint = batch_hint(kind, batch_count(kind))
        try:
            return context.window_manager.invoke_confirm(
                self, event, title="Import All %s?" % row[1], message=hint + ". Blender stays busy while it runs",
                confirm_text="Start", icon="WARNING")
        except TypeError:
            return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        row = _BATCH.get(getattr(context.scene, "arc_batch_kind", "OUTFITS"))
        if row is None:
            return {"CANCELLED"}
        group, name = row[3].split(".")
        op = getattr(getattr(bpy.ops, group), name)
        kwargs = {"part": row[4]} if row[4] else {}
        try:
            return op("INVOKE_DEFAULT", **kwargs)
        except (RuntimeError, TypeError) as exc:
            self.report({"ERROR"}, str(exc).strip().splitlines()[-1] if str(exc).strip() else "Batch import failed")
            return {"CANCELLED"}


# ---------------------------------------------------------------- animation tokens

_TOKENS = {"SR": "Sniper Rifle", "AR": "Assault Rifle", "UA": "Unarmed", "Wep": "Weapon"}
# A, P and G stay as they are: the catalog gives no meaning for them.
_TOKEN_RE = re.compile(r"(?<![A-Za-z])(SR|AR|UA|Wep)(?=[A-Z][a-z]|[^A-Za-z]|$)")


def display_tokens(text):
    """Spell out the weapon tokens in an animation name: ARBurst -> Assault Rifle Burst."""
    out = _TOKEN_RE.sub(lambda match: _TOKENS[match.group(1)] + " ", text or "")
    return " ".join(out.split())


# ---------------------------------------------------------------- favorites and recents

FAV_KINDS = ("outfit", "weapon", "enemy", "anim", "fx")
RECENT_MAX = 12
FILTER_ITEMS = (
    ("ALL", "All", "Show every item"),
    ("FAV", "Favorites", "Show starred items only"),
    ("RECENT", "Recent", "Show the last 12 imported items, newest first"),
)
_LIST_KIND = {"arc_outfit_rows": "outfit", "arc_anim_rows": "anim", "arc_fx_rows": "fx",
              "arc_outfit_rows_index": "outfit", "arc_anim_rows_index": "anim", "arc_fx_rows_index": "fx",
              "arc_weapon_rows": "weapon", "arc_weapon_rows_index": "weapon",
              "arc_enemy_rows": "enemy", "arc_enemy_rows_index": "enemy"}
_FAV = None


def _store():
    """Favorites and recents, read once from the add-on preferences."""
    global _FAV
    if _FAV is None:
        data = {}
        try:
            from . import prefs
            data = json.loads(prefs.get("ui_favorites", "") or "{}")
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        _FAV = {}
        for group in ("fav", "recent"):
            src = data.get(group) if isinstance(data.get(group), dict) else {}
            _FAV[group] = {kind: [str(k) for k in (src.get(kind) or []) if k] for kind in FAV_KINDS}
    return _FAV


def _save():
    try:
        from . import prefs
        prefs.set_value("ui_favorites", json.dumps(_store(), separators=(",", ":")))
    except Exception as exc:
        print("Arc lists: favorites not saved:", exc)


def is_favorite(kind, key):
    return bool(key) and key in _store()["fav"].get(kind, ())


def toggle_favorite(kind, key):
    fav = _store()["fav"].setdefault(kind, [])
    if key in fav:
        fav.remove(key)
        _save()
        return False
    fav.append(key)
    _save()
    return True


def record_recent(kind, key):
    """Call from operators on import success only."""
    if not key or key == "NONE" or kind not in FAV_KINDS:
        return
    recent = _store()["recent"].setdefault(kind, [])
    if key in recent:
        recent.remove(key)
    recent.insert(0, key)
    del recent[RECENT_MAX:]
    _save()


def filter_mode(context, kind):
    wm = getattr(context, "window_manager", None)
    return getattr(wm, "arc_filter_" + kind, "ALL") if wm is not None else "ALL"


def filter_keys(kind, mode):
    """Keys shown by a filter mode, newest first for recents; None means all."""
    if mode == "FAV":
        return list(_store()["fav"].get(kind, ()))
    if mode == "RECENT":
        return list(_store()["recent"].get(kind, ()))
    return None


def draw_filter_row(layout, context, kind):
    wm = getattr(context, "window_manager", None)
    if wm is not None and hasattr(wm, "arc_filter_" + kind):
        layout.row().prop(wm, "arc_filter_" + kind, expand=True)


def filtered_count(context, kind, rows):
    """(rows the filter shows, filter mode). rows are (key, ...) tuples or list items."""
    mode = filter_mode(context, kind)
    keys = filter_keys(kind, mode)
    if keys is None:
        return len(rows), mode
    wanted = set(keys)
    return sum(1 for row in rows if (row[0] if isinstance(row, tuple) else getattr(row, "key", "")) in wanted), mode


_MODE_NOUN = {"FAV": ("favorite", "favorites"), "RECENT": ("recent import", "recent imports")}


def draw_list_header(layout, context, kind, rows, total, noun, search_prop="", query=""):
    """Filter row (always), then a count or empty state that follows the filter. Returns shown count."""
    draw_filter_row(layout, context, kind)
    shown, mode = filtered_count(context, kind, rows)
    if shown:
        if mode in _MODE_NOUN:
            one, many = _MODE_NOUN[mode]
            layout.label(text="%d %s" % (shown, one if shown == 1 else many))
        else:
            layout.label(text=count_text(shown, total if query else 0, noun))
        return shown
    if mode == "FAV" and not query:
        layout.label(text="No favorites yet", icon="SOLO_OFF")
        hint = layout.row()
        hint.enabled = False
        hint.label(text="Click the star on a row")
    elif mode == "RECENT" and not query:
        layout.label(text="No recent imports yet", icon="TIME")
    else:
        layout.label(text="No matching " + noun, icon="INFO")
    if query or mode != "ALL":
        row = layout.row(align=True)
        if query:
            op = row.operator("arc_outfits.clear_search", text="Clear Search", icon="X")
            op.prop = search_prop
        if mode != "ALL":
            row.operator("arc_outfits.show_all_filter", text="Show All", icon="FILTER").kind = kind
    return 0


def draw_filter_empty(layout, context, kind, count):
    """Inline empty state for a picker whose Favorites/Recent filter shows nothing. True when drawn."""
    mode = filter_mode(context, kind)
    if count or mode == "ALL":
        return False
    if mode == "FAV":
        layout.label(text="No favorites yet", icon="SOLO_OFF")
        hint = layout.row()
        hint.enabled = False
        hint.label(text="Click the star beside the picker")
    else:
        layout.label(text="No recent imports yet", icon="TIME")
    layout.operator("arc_outfits.show_all_filter", text="Show All", icon="FILTER").kind = kind
    return True


def draw_star(layout, kind, key):
    if not key or key == "NONE":
        return
    op = layout.operator("arc_outfits.toggle_favorite", text="",
                         icon="SOLO_ON" if is_favorite(kind, key) else "SOLO_OFF", emboss=False)
    op.kind, op.key = kind, key


class ARC_OUTFITS_OT_toggle_favorite(Operator):
    bl_idname = "arc_outfits.toggle_favorite"
    bl_label = "Favorite"
    bl_options = {"INTERNAL"}

    kind: StringProperty(options={"HIDDEN"})
    key: StringProperty(options={"HIDDEN"})

    @classmethod
    def description(cls, context, props):
        action = "Remove from favorites" if is_favorite(props.kind, props.key) else "Add to favorites"
        return action

    def execute(self, context):
        if self.kind not in FAV_KINDS or not self.key or self.key == "NONE":
            return {"CANCELLED"}
        toggle_favorite(self.kind, self.key)
        for area in getattr(context.screen, "areas", ()):
            if area.type == "VIEW_3D":
                area.tag_redraw()
        return {"FINISHED"}


# ---------------------------------------------------------------- search pickers

_SEARCH_ITEMS = {}


def _search_items(kind, source, context):
    try:
        items = [item for item in source(None, context) if item[0] not in ("", "NONE")]
    except Exception as exc:
        print("Arc lists: %s search items failed: %s" % (kind, exc))
        items = []
    keys = filter_keys(kind, filter_mode(context, kind))
    if keys is not None:
        by_key = {item[0]: item for item in items}
        items = [by_key[key] for key in keys if key in by_key]
    out = [(item[0], item[1], item[2] if len(item) > 2 else "", i) for i, item in enumerate(items)]
    if not out:
        out = [("NONE", "Nothing to show", "No items match the filter", 0)]
    # Blender keeps no reference to returned enum strings; keep them alive here.
    _SEARCH_ITEMS[kind] = out
    return out


def _weapon_search_items(self, context):
    from ..Weapons import weapon_catalog
    return _search_items("weapon", lambda _s, _c: weapon_catalog.weapon_enum_items(), context)


def _enemy_search_items(self, context):
    from ..Arc import arc_catalog_data
    return _search_items("enemy", arc_catalog_data.enum_items, context)


class _SearchPick:
    bl_options = {"REGISTER", "INTERNAL"}
    bl_property = "choice"
    target = ""

    def invoke(self, context, _event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if self.choice in ("", "NONE"):
            return {"CANCELLED"}
        setattr(context.scene, self.target, self.choice)
        return {"FINISHED"}


class ARC_OUTFITS_OT_search_weapon(_SearchPick, Operator):
    bl_idname = "arc_outfits.search_weapon"
    bl_label = "Search Weapons"
    bl_description = "Find a weapon by name. The All, Favorites and Recent filter applies"
    target = "arc_weapon_browser_key"

    choice: EnumProperty(name="Weapon", items=_weapon_search_items)


class ARC_OUTFITS_OT_search_enemy(_SearchPick, Operator):
    bl_idname = "arc_outfits.search_enemy"
    bl_label = "Search Enemies"
    bl_description = "Find an enemy by name. The All, Favorites and Recent filter applies"
    target = "arc_enemy_key"

    choice: EnumProperty(name="Enemy", items=_enemy_search_items)


# ---------------------------------------------------------------- import history

_IMPORTS = []


def snapshot():
    """Names of current objects and collections; call before an import starts."""
    return ({obj.name for obj in bpy.data.objects}, {col.name for col in bpy.data.collections})


def record_import(label, before):
    """Remember what an import added so Remove Last Import can take it out again."""
    try:
        objs_before, cols_before = before
        new_objs = [obj for obj in bpy.data.objects if obj.name not in objs_before]
        new_cols = [col.name for col in bpy.data.collections if col.name not in cols_before]
        if not new_objs and not new_cols:
            return
        new_names = {obj.name for obj in new_objs}
        tops = [obj.name for obj in new_objs if obj.parent is None or obj.parent.name not in new_names]
        _IMPORTS.append({"label": label or "Import", "objects": sorted(new_names), "tops": tops,
                         "collections": new_cols})
        del _IMPORTS[:-20]
    except Exception as exc:
        print("Arc lists: import not recorded:", exc)
        return
    _stamp_new(label, new_objs)


def _stamp_new(label, new_objs):
    """Tag the new objects with one import id so In This Scene and Remove can find them after reload."""
    if not new_objs:
        return
    try:
        from . import ui_targets
        unstamped = [obj for obj in new_objs if not obj.get(ui_targets.PROP_ID)]
        if not unstamped:
            return
        tops = [obj for obj in unstamped if obj.parent is None] or unstamped
        kind = ""
        for obj in tops:
            kind = object_kind(obj) or ""
            if kind:
                break
        ui_targets.stamp(unstamped, ui_targets.new_import_id(), kind or "part", label or "Import")
    except Exception as exc:
        print("Arc lists: import not stamped:", exc)
    _INVENTORY.clear()


def last_import():
    return _IMPORTS[-1] if _IMPORTS else None


class ARC_OUTFITS_OT_remove_last_import(_ConfirmRemove, Operator):
    bl_idname = "arc_outfits.remove_last_import"
    bl_label = "Delete Last Import..."
    bl_description = "Delete the newest import in this scene. Objects also used in other scenes are only removed from this scene"
    bl_options = {"REGISTER", "UNDO"}

    @staticmethod
    def _newest(scene):
        try:
            from . import ui_targets
            entries = ui_targets.import_entries(scene)
        except Exception:
            entries = []
        return entries[0] if entries else None

    @classmethod
    def poll(cls, context):
        if cls._newest(context.scene) is None:
            cls.poll_message_set("Nothing to remove")
            return False
        return True

    def _import_id(self, context):
        entry = self._newest(context.scene)
        return str(entry.get("id") or "") if entry else ""


def diagnostics_text(scene):
    """Short support text: versions and source state, never user paths."""
    import sys
    root = sys.modules.get((__package__ or "").split(".")[0])
    version = ".".join(str(part) for part in ((getattr(root, "bl_info", {}) or {}).get("version") or ()))
    state, message = "unknown", ""
    try:
        from . import prefs
        state, message = prefs.source_status(scene)
    except Exception:
        pass
    folder = bool(str(getattr(scene, "arc_pak_dir", "") or "").strip())
    lines = [
        "Arc Asset Importer " + (version or "unknown"),
        "Blender " + getattr(bpy.app, "version_string", "unknown"),
        "Game source: " + str(state) + ((" (" + str(message) + ")") if message and not any(ch in str(message) for ch in "\\/:") else ""),
        "Game folder set: " + ("yes" if folder else "no"),
        "Import from: " + str(getattr(scene, "arc_import_source", "") or "unknown"),
    ]
    return "\n".join(lines)


class ARC_OUTFITS_OT_copy_diagnostics(Operator):
    bl_idname = "arc_outfits.copy_diagnostics"
    bl_label = "Copy Diagnostics"
    bl_description = "Copy the add-on and Blender versions and the game source state, for a bug report. No folder paths are included"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        context.window_manager.clipboard = diagnostics_text(context.scene)
        self.report({"INFO"}, "Diagnostics copied")
        return {"FINISHED"}


_GEAR_ITEMS = (
    ("BACKPACK", "Backpack", "Backpacks"),
    ("ATTACHMENT", "Backpack Attachment", "Backpack add-ons"),
    ("CHARM", "Charm", "Charms"),
    ("RAIDER_TOOL", "Raider Tool", "Raider tools"),
)

classes = (
    ARC_PG_list_row,
    ARC_UL_rows,
    ARC_OUTFITS_OT_select_scene_root,
    ARC_OUTFITS_OT_cancel_all_outfits_colorways,
    ARC_OUTFITS_MT_outfit_import_more,
    ARC_OUTFITS_OT_toggle_favorite,
    ARC_OUTFITS_OT_search_weapon,
    ARC_OUTFITS_OT_search_enemy,
    ARC_OUTFITS_OT_remove_last_import,
    ARC_UL_scene_rows,
    ARC_OUTFITS_OT_scene_row_select,
    ARC_OUTFITS_OT_scene_row_visibility,
    ARC_OUTFITS_OT_scene_row_remove,
    ARC_OUTFITS_OT_sort_into_collections,
    ARC_OUTFITS_OT_copy_job_report,
    ARC_OUTFITS_OT_dismiss_job_report,
    ARC_OUTFITS_OT_dismiss_error,
    ARC_OUTFITS_OT_copy_error,
    ARC_OUTFITS_OT_view_details,
    ARC_OUTFITS_OT_review_batch_plan,
    ARC_OUTFITS_OT_clear_search,
    ARC_OUTFITS_OT_show_all_filter,
    ARC_OUTFITS_OT_copy_diagnostics,
    ARC_OUTFITS_OT_start_batch_import,
)

_HAIR_TABS = (
    ("HAIR", "Hair", "Hairstyles"),
    ("FACIAL_HAIR", "Facial Hair", "Beards and moustaches"),
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            # Already registered after a script reload.
            pass
    wm =bpy.types.WindowManager
    wm.arc_outfit_rows = CollectionProperty(type=ARC_PG_list_row)
    wm.arc_outfit_rows_index = IntProperty(default=-1, update=_apply_outfit)
    wm.arc_anim_rows = CollectionProperty(type=ARC_PG_list_row)
    wm.arc_anim_rows_index = IntProperty(default=-1, update=_apply_anim)
    wm.arc_fx_rows = CollectionProperty(type=ARC_PG_list_row)
    wm.arc_fx_rows_index = IntProperty(default=-1, update=_apply_fx)
    wm.arc_weapon_rows = CollectionProperty(type=ARC_PG_list_row)
    wm.arc_weapon_rows_index = IntProperty(default=-1, update=_apply_weapon)
    wm.arc_enemy_rows = CollectionProperty(type=ARC_PG_list_row)
    wm.arc_enemy_rows_index = IntProperty(default=-1, update=_apply_enemy)
    bpy.types.Scene.arc_weapon_search = StringProperty(name="Search Weapons")
    bpy.types.Scene.arc_enemy_search = StringProperty(name="Search Enemies")
    wm.arc_scene_rows = CollectionProperty(type=ARC_PG_list_row)
    wm.arc_scene_rows_index = IntProperty(default=-1)
    bpy.types.Scene.arc_hair_tab = EnumProperty(
        name="Hair Type", description="Which hair list to show", items=_HAIR_TABS, default="HAIR")
    bpy.types.Scene.arc_batch_kind = EnumProperty(
        name="Import", description="What to import in one batch",
        items=[row[:3] for row in BATCH_KINDS], default="OUTFITS")
    for kind in FAV_KINDS:
        setattr(wm, "arc_filter_" + kind, EnumProperty(
            name="Show", description="Which items the list and search show", items=FILTER_ITEMS, default="ALL"))
    bpy.types.Scene.arc_gear_slot = EnumProperty(
        name="Gear", description="Which kind of gear to add",
        items=_GEAR_ITEMS, default="BACKPACK")
    bpy.types.Scene.arc_collection_sort = EnumProperty(
        name="Collections", description="How Sort into Collections groups imported objects",
        items=COLLECTION_SORT_ITEMS, default="KIND")
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if bpy.app.timers.is_registered(_flush):
        bpy.app.timers.unregister(_flush)
    for name in ("arc_outfit_rows", "arc_outfit_rows_index", "arc_anim_rows", "arc_anim_rows_index",
                 "arc_weapon_rows", "arc_weapon_rows_index", "arc_enemy_rows", "arc_enemy_rows_index",
                 "arc_fx_rows", "arc_fx_rows_index", "arc_scene_rows", "arc_scene_rows_index",
                 *("arc_filter_" + kind for kind in FAV_KINDS)):
        if hasattr(bpy.types.WindowManager, name):
            delattr(bpy.types.WindowManager, name)
    for name in ("arc_gear_slot", "arc_hair_tab", "arc_batch_kind", "arc_collection_sort", "arc_weapon_search", "arc_enemy_search"):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _on_load()
