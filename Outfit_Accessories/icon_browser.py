"""Gear duplicate prompt and the icon browser popup for Character Parts.

Gear prompt: ``arc_outfits.add_gear_part`` wraps ``arc_outfits.add_appearance_part`` for
backpacks, backpack attachments, charms and raider tools. When the target character already
wears an item of that kind, it asks whether to keep, hide or remove the old one.
The import itself runs as the usual staged (modal) job; hide/remove applies once the
job ends and only when the new item arrived, so a failed import never loses the old one.

Icon browser: ``arc_outfits.icon_browser`` opens a wide popup grid of cached previews for
one part kind, filterable by name. ``arc_outfits.icon_browser_pick`` sets the scene choice.
Drawing only reads cached dictionaries; image loading stays on appearance's timer.
"""
from __future__ import annotations

import functools
import os
import time

import bpy

GEAR_PARTS = ("BACKPACK", "ATTACHMENT", "CHARM", "RAIDER_TOOL")
# First role is the item itself; the rest are helpers that belong to it.
GEAR_ROLES = {
    "BACKPACK": ("Backpack", "BackpackStrap", "BackpackFrame"),
    "ATTACHMENT": ("Attachment",),
    "CHARM": ("Charm", "CharmConnector"),
    "RAIDER_TOOL": ("RaiderTool",),
}
GEAR_LABELS = {"BACKPACK": "backpack", "ATTACHMENT": "backpack attachment", "CHARM": "charm",
               "RAIDER_TOOL": "raider tool"}
EXISTING_MODES = (
    ("KEEP", "Add and keep existing prop", "Import the new item and leave the current one as it is"),
    ("HIDE", "Add and hide existing prop", "Import the new item, then hide the current one in viewport and render"),
    ("REMOVE", "Add and remove existing prop", "Import the new item, then delete the current one (shared data stays)"),
)
_FINALIZE_TIMEOUT = 1800.0
BROWSER_COLUMNS = 5
BROWSER_ROWS = 3
BROWSER_PAGE_SIZE = BROWSER_COLUMNS * BROWSER_ROWS
BROWSER_WIDTH = 1100

def _alive(obj) -> bool:
    try:
        return obj is not None and obj.name in bpy.data.objects
    except ReferenceError:
        return False

def _role(obj) -> str:
    try:
        return str(obj.get("arc_assembly_role", "") or "")
    except Exception:
        return ""

def _chain(obj):
    seen = set()
    while obj is not None and obj.name not in seen:
        seen.add(obj.name)
        yield obj
        obj = obj.parent

def belongs_to(obj, armature) -> bool:
    """True when ``armature`` is a parent of ``obj`` or drives it through an Armature modifier."""
    if obj is None or armature is None:
        return False
    for node in _chain(obj.parent):
        if node == armature:
            return True
    for mod in getattr(obj, "modifiers", ()):
        target = getattr(mod, "object", None) if mod.type == "ARMATURE" else None
        if target is not None and any(node == armature for node in _chain(target)):
            return True
    return False

def _visible(obj) -> bool:
    try:
        return not obj.hide_viewport and not obj.hide_get()
    except Exception:
        return not getattr(obj, "hide_viewport", False)

def existing_gear(armature, part: str, *, objects=None, visible_only=True) -> list:
    """Visible items of this kind on ``armature`` (item role only, not helpers)."""
    roles = GEAR_ROLES.get(part)
    if not roles or armature is None:
        return []
    pool = bpy.data.objects if objects is None else objects
    return [o for o in pool if _role(o) == roles[0] and belongs_to(o, armature)
            and (not visible_only or _visible(o))]

def gear_with_helpers(armature, part: str, *, objects=None) -> list:
    """Item and helper objects of this kind on ``armature`` plus their untagged children."""
    roles = set(GEAR_ROLES.get(part, ()))
    pool = list(bpy.data.objects if objects is None else objects)
    found = [o for o in pool if _role(o) in roles and belongs_to(o, armature)]
    names = {o.name for o in found}
    for obj in list(found):
        for child in obj.children_recursive:
            # Another gear kind parented here (an add-on on a backpack frame) is not this item.
            if child.name not in names and not _role(child):
                names.add(child.name)
                found.append(child)
    return found

def hide_gear(objs) -> int:
    count = 0
    for obj in objs:
        if not _alive(obj):
            continue
        obj.hide_viewport = True
        obj.hide_render = True
        try:
            obj.hide_set(True)
        except Exception:
            pass
        count += 1
    return count

def _protected(new_objs) -> set:
    """Objects the new item still uses (parents, armature modifier targets)."""
    keep = set()
    for obj in new_objs:
        for node in _chain(obj):
            keep.add(node.name)
        for mod in getattr(obj, "modifiers", ()):
            target = getattr(mod, "object", None) if mod.type == "ARMATURE" else None
            for node in _chain(target):
                keep.add(node.name)
    return keep

def remove_gear(objs, *, keep=()) -> int:
    """Delete objects, then only those datablocks left with zero users."""
    keep = set(keep)
    victims = [o for o in objs if _alive(o) and o.name not in keep]
    data, materials, images = [], [], []
    for obj in victims:
        block = obj.data
        if block is not None and block not in data:
            data.append(block)
        for slot in getattr(obj, "material_slots", ()):
            mat = slot.material
            if mat is not None and mat not in materials:
                materials.append(mat)
    for mat in materials:
        tree = getattr(mat, "node_tree", None)
        for node in getattr(tree, "nodes", ()):
            image = getattr(node, "image", None)
            if image is not None and image not in images:
                images.append(image)
    removed = 0
    for obj in victims:
        bpy.data.objects.remove(obj, do_unlink=True)
        removed += 1
    collections = {bpy.types.Mesh: bpy.data.meshes, bpy.types.Armature: bpy.data.armatures,
                   bpy.types.Curve: bpy.data.curves}
    for groups in ((data, None), (materials, bpy.data.materials), (images, bpy.data.images)):
        blocks, store = groups
        for block in blocks:
            try:
                if block.users or block.use_fake_user:
                    continue
            except ReferenceError:
                continue
            target = store
            if target is None:
                target = next((c for t, c in collections.items() if isinstance(block, t)), None)
            if target is not None:
                target.remove(block)
    return removed

def finalize_existing(armature_name: str, part: str, mode: str, old_names, before_names) -> str:
    """After the import: hide/remove the old item only if a new one of that kind arrived."""
    arm = bpy.data.objects.get(armature_name)
    if arm is None or mode not in ("HIDE", "REMOVE"):
        return "skipped"
    before = set(before_names)
    role = GEAR_ROLES[part][0]
    new = [o for o in bpy.data.objects if o.name not in before and _role(o) == role and belongs_to(o, arm)]
    if not new:
        return "no-new-item"
    old = [bpy.data.objects[n] for n in old_names if n in bpy.data.objects and n in before]
    if mode == "HIDE":
        return "hid %d" % hide_gear(old)
    new_all = [o for o in bpy.data.objects if o.name not in before]
    return "removed %d" % remove_gear(old, keep=_protected(new_all))

def _job_busy() -> bool:
    try:
        from ..Shared import ui_targets
        if ui_targets.job_busy_message():
            return True
    except Exception:
        pass
    try:
        from ..Maps import staged_import
        return getattr(staged_import, "_PENDING_GEN", None) is not None
    except Exception:
        return False

def _finalize_timer(state):
    if _job_busy() and time.monotonic() < state["deadline"]:
        return 0.5
    if not _job_busy():
        try:
            result = finalize_existing(state["armature"], state["part"], state["mode"],
                                       state["old"], state["before"])
            print("[ARC] gear prompt:", state["part"], state["mode"], result)
        except Exception as exc:
            print("[ARC] gear prompt finalize failed:", exc)
    return None

def _destination(context):
    try:
        from ..Shared import ui_targets
        return ui_targets.part_destination(context, getattr(context.scene, "arc_part_mode", "ADD")) or {}
    except Exception:
        return {}

def prompt_needed(context, part: str):
    """(armature, existing objects) when the popup should ask, else (armature, [])."""
    plan = _destination(context)
    arm = plan.get("armature")
    if part not in GEAR_PARTS or plan.get("error") or plan.get("standalone") or plan.get("replace") or arm is None:
        return arm, []
    return arm, existing_gear(arm, part)

class ARC_OUTFITS_OT_AddGearPart(bpy.types.Operator):
    """Add the chosen gear item; asks first when the character already has one"""
    bl_idname = "arc_outfits.add_gear_part"
    bl_label = "Add Gear"
    bl_options = {'REGISTER'}

    part: bpy.props.EnumProperty(
        name="Part",
        items=[(p, p.replace("_", " ").title(), "") for p in GEAR_PARTS],
        default="BACKPACK",
    )
    existing_mode: bpy.props.EnumProperty(
        name="Existing Item",
        description="What to do with the item of this kind the character already has",
        items=EXISTING_MODES,
        default="KEEP",
    )

    @classmethod
    def poll(cls, context):
        try:
            return bool(bpy.ops.arc_outfits.add_appearance_part.poll())
        except Exception:
            return False

    def invoke(self, context, event):
        _arm, existing = prompt_needed(context, self.part)
        if not existing:
            self.existing_mode = "KEEP"
            return self.execute(context)
        self._existing_names = ", ".join(o.name for o in existing[:3])
        return context.window_manager.invoke_props_dialog(
            self, width=360, title="Character already has a " + GEAR_LABELS[self.part],
            confirm_text="Add")

    def draw(self, context):
        layout = self.layout
        names = getattr(self, "_existing_names", "")
        if names:
            layout.label(text="Current: " + names, icon="INFO")
        layout.prop(self, "existing_mode", expand=True)

    def execute(self, context):
        arm, existing = prompt_needed(context, self.part)
        state = None
        if existing and self.existing_mode in ("HIDE", "REMOVE"):
            old = {o.name for o in gear_with_helpers(arm, self.part)}
            state = {"armature": arm.name, "part": self.part, "mode": self.existing_mode,
                     "old": sorted(old), "before": [o.name for o in bpy.data.objects],
                     "deadline": time.monotonic() + _FINALIZE_TIMEOUT}
        try:
            bpy.ops.arc_outfits.add_appearance_part('EXEC_DEFAULT', part=self.part)
        except RuntimeError as exc:
            self.report({'WARNING'}, str(exc).split("\n")[-1] or "Import could not start")
            return {'CANCELLED'}
        if state is not None:
            bpy.app.timers.register(functools.partial(_finalize_timer, state), first_interval=0.5)
        # The staged modal run owns the undo step, like add_appearance_part.
        return {'CANCELLED'}

# ---------------------------------------------------------------------------
# Icon browser
# ---------------------------------------------------------------------------

KIND_ITEMS = (
    ("outfit", "Outfits", "", "MOD_CLOTH", 0),
    ("head", "Heads", "", "USER", 1),
    ("hair", "Hair", "", "PARTICLE_DATA", 2),
    ("facial_hair", "Facial Hair", "", "PARTICLE_DATA", 3),
    ("backpack", "Backpacks", "", "OUTLINER_OB_ARMATURE", 4),
    ("attachment", "Backpack Attachments", "", "MODIFIER", 5),
    ("charm", "Charms", "", "LINKED", 6),
    ("raider_tool", "Raider Tools", "", "TOOL_SETTINGS", 7),
    ("weapon", "Weapons", "", "MOD_PHYSICS", 8),
    ("enemy", "ARC Enemies", "", "GHOST_ENABLED", 9),
    ("effect", "Niagara Effects", "", "PARTICLES", 10),
)
KIND_PROP = {
    "outfit": "arc_selected_outfit",
    "head": "arc_appearance_head",
    "hair": "arc_appearance_hair",
    "facial_hair": "arc_appearance_facial_hair",
    "backpack": "arc_appearance_backpack",
    "attachment": "arc_appearance_attachment",
    "charm": "arc_appearance_charm",
    "raider_tool": "arc_appearance_raider_tool",
    "weapon": "arc_weapon_key",
    "enemy": "arc_enemy_key",
    "effect": "arc_ns_selected",
}
_ENUM_FN = {
    "head": "make_head_enum_items",
    "hair": "make_hair_enum_items",
    "facial_hair": "make_facial_hair_enum_items",
    "backpack": "make_backpack_enum_items",
    "attachment": "make_attachment_enum_items",
    "charm": "make_charm_enum_items",
    "raider_tool": "make_raider_tool_enum_items",
}
_MULTI_SELECTION = {}
_BROWSER_IMPORT_ACTIVE = None
_BROWSER_DIALOG = None
_BROWSER_PREFETCH = None
_ITEMS_MEMO: dict = {}
_PATH_MEMO: dict = {}

def _coded_label(label, codename):
    codename = str(codename or '').strip()
    if not codename or f'({codename})'.casefold() in label.casefold():
        return label
    return f'{label} ({codename})'

def _outfit_codename(row):
    folder = (row.get('Model Folder Name') or row.get('Item/UI Folder Name') or '').split(';')[0].strip()
    if folder:
        return folder
    mesh = (row.get('Pak Mesh Paths') or '').split(';')[0].strip().replace('\\', '/')
    if mesh:
        return os.path.splitext(os.path.basename(mesh))[0]
    return (row.get('ST') or '').strip()

def _raw_items(context, kind):
    scene = context.scene
    if kind == "outfit":
        from . import properties
        from ..Shared import importing
        items = properties.make_outfit_selector_items(scene, context) or ()
        csv_path, _mtime = properties.outfit_csv_state(context)
        rows = properties._CSV_ROWS_MEMO.get(csv_path, ())
        rows_by_key = {}
        for row in rows:
            rows_by_key.setdefault(importing.outfit_row_key(row), row)
        result = []
        for item in items:
            row = rows_by_key.get(item[0]) or {}
            codename = _outfit_codename(row)
            result.append((item[0], _coded_label(item[1], codename), *item[2:]))
        return tuple(result)
    if kind == "weapon":
        from ..Weapons import weapon_catalog
        return tuple((item[0], _coded_label(item[1], (weapon_catalog.weapon_by_key(item[0]) or {}).get('stem')), *item[2:])
                     for item in weapon_catalog.weapon_enum_items() or ())
    if kind == "enemy":
        from ..Arc import arc_catalog_data
        return tuple((item[0], _coded_label(item[1], item[0] if arc_catalog_data.by_key(item[0]) else ''), *item[2:])
                     for item in arc_catalog_data.enum_items() or ())
    if kind == "effect":
        from ..Niagara import niagara
        found = niagara.filter_entries("", catalog_root=getattr(scene, "arc_ns_catalog_root", "") or "",
                                       lut_only=bool(getattr(scene, "arc_ns_lut_only", False)), limit=0)
        return tuple((entry.get("key") or "", _coded_label(niagara.display_label(entry), entry.get('stem')), "") for entry in found or ())
    from . import appearance
    fn = getattr(appearance, _ENUM_FN[kind], None)
    items = (fn(scene, context) or ()) if fn else ()
    result = []
    for item in items:
        choice = appearance.resolve_choice(item[0]) or {}
        codename = choice.get('folder') or os.path.splitext(os.path.basename((choice.get('rel_path') or '').replace('\\', '/')))[0]
        result.append((item[0], _coded_label(item[1], codename), *item[2:]))
    return tuple(result)

def collect_items(context, kind: str, query: str = "") -> list:
    """[(id, label)] for ``kind`` whose label or id contains ``query`` (case-insensitive)."""
    if kind not in KIND_PROP:
        return []
    raw = _raw_items(context, kind)
    query = (query or "").strip().casefold()
    key = (kind, query)
    hit = _ITEMS_MEMO.get(key)
    # Enum callbacks may rebuild their list each call; equal lists reuse the filtered result.
    if hit is not None and (hit[0] is raw or hit[0] == raw):
        return hit[1]
    out = []
    for item in raw:
        if not item or item[0] in ("", "NONE"):
            continue
        ident, label = str(item[0]), str(item[1])
        if query and query not in label.casefold() and query not in ident.casefold():
            continue
        out.append((ident, label))
    if len(_ITEMS_MEMO) > 64:
        _ITEMS_MEMO.clear()
    _ITEMS_MEMO[key] = (raw, out)
    return out

def _outfit_icon_path(context, key):
    from . import appearance, properties
    from ..Shared import importing
    csv_path, _mtime = properties.outfit_csv_state(context)
    rows = properties._CSV_ROWS_MEMO.get(csv_path, ())
    row = importing.find_outfit_row(rows, key) if rows else None
    if not row:
        return ""
    return appearance._bundled_outfit_preview(row) or appearance._queue_missing_outfit_preview(context, row)

def icon_path(context, kind, ident) -> str:
    """Packaged preview path, memoised once found (lookups are dictionary reads)."""
    from . import appearance
    key = (kind, ident, appearance._PREVIEW_PATH_GENERATION, getattr(context.scene, "arc_pioneer_root", ""))
    if key in _PATH_MEMO:
        return _PATH_MEMO[key]
    try:
        from . import appearance
        if kind == "outfit":
            path = _outfit_icon_path(context, ident)
        elif kind == "weapon":
            helper = getattr(appearance, 'browser_preview_path', None)
            if helper:
                path = helper(context, kind, ident) or ''
            else:
                from ..Weapons import weapon_assets, weapon_catalog
                entry = weapon_catalog.weapon_by_key(ident)
                path = str(weapon_assets.bundled_preview(entry, 'OFF', tier='3') or '') if entry else ''
        elif kind == "enemy":
            from ..Arc import arc_catalog_data
            from ..Arc.arc_ui import _pioneer_image_path
            row = arc_catalog_data.by_key(ident)
            path = str(_pioneer_image_path(row[2]) or "") if row else ""
        elif kind == "effect":
            path = ""
        else:
            path = appearance._browse_icon_file_for_choice(appearance.resolve_choice(ident))
    except Exception:
        path = ""
    _PATH_MEMO[key] = path or ""
    return path or ""

def icon_id(context, kind, ident) -> int:
    path = icon_path(context, kind, ident)
    if not path:
        return 0
    try:
        from . import appearance
        return int(appearance._preview_icon_id_for_path(path, priority=True) or 0)
    except Exception:
        return 0

def current_selection(context, kind) -> str:
    return str(getattr(context.scene, KIND_PROP.get(kind, ""), "") or "")

def apply_selection(context, kind: str, item_id: str) -> bool:
    prop = KIND_PROP.get(kind)
    scene = context.scene
    if not prop or not hasattr(scene, prop):
        return False
    if kind == "effect" and item_id not in {ident for ident, _label in collect_items(context, kind)}:
        return False
    try:
        if current_selection(context, kind) != item_id:
            setattr(scene, prop, item_id)
    except (TypeError, ValueError):
        return False
    if current_selection(context, kind) != item_id:
        return False
    _reveal_browser_selection(context, kind, item_id)
    return True

def _reveal_browser_selection(context, kind, item_id):
    list_kind = {"outfit": "outfit", "weapon": "weapon", "enemy": "enemy", "effect": "fx"}.get(kind)
    if list_kind is None:
        return
    from ..Shared import ui_lists
    search = "arc_ns_search" if kind == "effect" else "arc_" + kind + "_search"
    if getattr(context.scene, search, ""):
        setattr(context.scene, search, "")
    ui_lists._reset_filter(context, list_kind)
    raw = _raw_items(context, kind)
    rows, _total = ui_lists.outfit_rows(raw, "")
    ui_lists.request_sync(list_kind, ("browse", kind, id(raw)), lambda: rows, item_id)

def selected_items(context, kind):
    return _MULTI_SELECTION.setdefault((context.scene.as_pointer(), kind), set())

def on_multiple_changed(context):
    if not context.window_manager.arc_browser_multiple:
        return
    for kind in KIND_PROP:
        current = current_selection(context, kind)
        if current and current != "NONE":
            selected_items(context, kind).add(current)

def _tag_browser_redraw(context):
    browser = _BROWSER_DIALOG
    region = getattr(browser, "_popup_region", None)
    if region is not None:
        try:
            region.tag_redraw()
        except ReferenceError:
            pass
    if getattr(context, "area", None) is not None:
        context.area.tag_redraw()

def _clamp_page(self, context):
    if context is None or getattr(context, 'scene', None) is None:
        return
    count = len(collect_items(context, self.kind, self.filter))
    page_size = BROWSER_PAGE_SIZE
    pages = max(1, (count + page_size - 1) // page_size)
    bounded = min(pages, max(1, self.page))
    if self.page != bounded:
        self.page = bounded

def _warm_browser_thumbnails(operator, context):
    global _BROWSER_PREFETCH
    from . import appearance
    start = (max(1, operator.page) - 1) * BROWSER_PAGE_SIZE
    items = collect_items(context, operator.kind, operator.filter)
    appearance._LOW_RESOLUTION_PRIORITY.clear()
    deadline = time.perf_counter() + 0.04
    for ident, label in items[start:start + BROWSER_PAGE_SIZE]:
        path = icon_path(context, operator.kind, ident)
        if path:
            appearance._low_resolution_icon_id(path, prepare=time.perf_counter() < deadline, priority=True)
    operator._thumbnail_page = (operator.kind, operator.filter, operator.page)
    if operator is _BROWSER_DIALOG:
        following = items[start + BROWSER_PAGE_SIZE:start + 2 * BROWSER_PAGE_SIZE]
        _BROWSER_PREFETCH = {'browser': operator, 'page': operator._thumbnail_page,
                             'items': following, 'index': 0}
        if following and not bpy.app.timers.is_registered(_prepare_browser_prefetch):
            bpy.app.timers.register(_prepare_browser_prefetch, first_interval=0.025)


def _prepare_browser_prefetch():
    global _BROWSER_PREFETCH
    from . import appearance
    state = _BROWSER_PREFETCH
    if state is None:
        return None
    browser = _BROWSER_DIALOG
    try:
        active = browser is state['browser'] and (browser.kind, browser.filter, browser.page) == state['page']
    except (AttributeError, ReferenceError):
        active = False
    if not active:
        _BROWSER_PREFETCH = None
        return None
    if appearance._LOW_RESOLUTION_PRIORITY:
        return 0.01
    deadline = time.perf_counter() + 0.008
    prepared = 0
    entries = state['items']
    while state['index'] < len(entries):
        entry = entries[state['index']]
        state['index'] += 1
        path = icon_path(bpy.context, browser.kind, entry[0])
        if path:
            appearance._low_resolution_icon_id(path, prepare=True)
        prepared += 1
        if prepared >= 2 or time.perf_counter() >= deadline:
            break
    if prepared:
        appearance._tag_preview_redraw()
    if state['index'] < len(entries):
        return 0.01
    _BROWSER_PREFETCH = None
    return None


@functools.lru_cache(maxsize=1)
def _placeholder_icon_id():
    return bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items["IMAGE_DATA"].value

def _update_page(self, context):
    _clamp_page(self, context)
    if (self.kind, self.filter, self.page) != getattr(self, "_thumbnail_page", None):
        _warm_browser_thumbnails(self, context)
    _tag_browser_redraw(context)


def _reset_page(self, context):
    if self.page != 1:
        self.page = 1
    _update_page(self, context)

def _dispatch_browser_import(kind):
    if kind == "outfit":
        return bpy.ops.arc_outfits.load_outfit('EXEC_DEFAULT')
    if kind == "weapon":
        return bpy.ops.arc_outfits.import_weapon('EXEC_DEFAULT')
    if kind == "enemy":
        return bpy.ops.arc_outfits.import_codex_enemy('EXEC_DEFAULT')
    if kind == "effect":
        return bpy.ops.arc_outfits.import_niagara_fx('EXEC_DEFAULT')
    return bpy.ops.arc_outfits.add_appearance_part('EXEC_DEFAULT', part=kind.upper())

class ARC_OUTFITS_OT_IconBrowserPick(bpy.types.Operator):
    """Use this item"""
    bl_idname = "arc_outfits.icon_browser_pick"
    bl_label = "Pick Item"
    bl_options = {'INTERNAL'}

    kind: bpy.props.EnumProperty(items=[k[:3] for k in KIND_ITEMS], default="outfit")
    item_id: bpy.props.StringProperty()

    def execute(self, context):
        selected = selected_items(context, self.kind)
        if getattr(context.window_manager, "arc_browser_multiple", False) and self.item_id in selected:
            selected.remove(self.item_id)
            return {'FINISHED'}
        if not apply_selection(context, self.kind, self.item_id):
            self.report({'WARNING'}, "That item is no longer in the list")
            return {'CANCELLED'}
        if getattr(context.window_manager, "arc_browser_multiple", False):
            selected.add(self.item_id)
        return {'FINISHED'}

class ARC_OUTFITS_OT_ImportBrowserSelection(bpy.types.Operator):
    bl_idname = "arc_outfits.import_browser_selection"
    bl_label = "Import"
    bl_options = {'REGISTER'}

    kind: bpy.props.EnumProperty(items=[entry[:3] for entry in KIND_ITEMS], default="outfit")

    @classmethod
    def poll(cls, context):
        return _BROWSER_IMPORT_ACTIVE is None and not _job_busy()

    def _step(self, context):
        ident = self._pending.pop(0)
        if not apply_selection(context, self.kind, ident):
            self._failed.append(ident)
            return
        try:
            result = _dispatch_browser_import(self.kind)
            if 'CANCELLED' in result:
                self._failed.append(ident)
        except Exception as exc:
            self._failed.append(ident)
            self.report({'WARNING'}, str(exc))

    def execute(self, context):
        global _BROWSER_IMPORT_ACTIVE, _BROWSER_DIALOG
        _BROWSER_DIALOG = None
        multiple = context.window_manager.arc_browser_multiple
        chosen = selected_items(context, self.kind) if multiple else {current_selection(context, self.kind)}
        self._pending = [ident for ident, label in collect_items(context, self.kind) if ident in chosen]
        if not self._pending:
            return {'CANCELLED'}
        self._failed = []
        self._scene = context.scene
        self._wm = context.window_manager
        self._timer = None
        _BROWSER_IMPORT_ACTIVE = self
        if bpy.app.background:
            try:
                while self._pending:
                    if _job_busy():
                        self.report({'ERROR'}, "An import is still running")
                        return {'CANCELLED'}
                    self._step(context)
            finally:
                self._finish()
            return {'CANCELLED'} if self._failed else {'FINISHED'}
        self._timer = self._wm.event_timer_add(0.1, window=context.window)
        self._wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC' or context.scene != self._scene:
            self._finish()
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        if _job_busy():
            return {'RUNNING_MODAL'}
        if self._pending:
            self._step(context)
            return {'RUNNING_MODAL'}
        self._finish()
        return {'CANCELLED'} if self._failed else {'FINISHED'}

    def _finish(self):
        global _BROWSER_IMPORT_ACTIVE
        if self._timer is not None:
            self._wm.event_timer_remove(self._timer)
            self._timer = None
        _BROWSER_IMPORT_ACTIVE = None

    def cancel(self, context):
        self._finish()

class ARC_OUTFITS_OT_BrowserPage(bpy.types.Operator):
    bl_idname = "arc_outfits.browser_page"
    bl_label = "Change Page"
    bl_options = {'INTERNAL'}

    direction: bpy.props.IntProperty(default=1)

    def execute(self, context):
        browser = _BROWSER_DIALOG
        if browser is None:
            return {'CANCELLED'}
        count = len(collect_items(context, browser.kind, browser.filter))
        pages = max(1, (count + BROWSER_PAGE_SIZE - 1) // BROWSER_PAGE_SIZE)
        browser.page = min(pages, max(1, browser.page + self.direction))
        if (browser.kind, browser.filter, browser.page) != getattr(browser, "_thumbnail_page", None):
            _warm_browser_thumbnails(browser, context)
        _tag_browser_redraw(context)
        return {'FINISHED'}

class ARC_OUTFITS_OT_IconBrowser(bpy.types.Operator):
    """Browse items by preview icon"""
    bl_idname = "arc_outfits.icon_browser"
    bl_label = "Browse Icons"
    bl_options = {'REGISTER', 'INTERNAL'}

    kind: bpy.props.EnumProperty(name="Type", items=KIND_ITEMS, default="outfit", update=_reset_page)
    filter: bpy.props.StringProperty(name="Filter", options={'TEXTEDIT_UPDATE', 'SKIP_SAVE'}, update=_reset_page)
    page: bpy.props.IntProperty(name="Page", min=1, default=1, options={'SKIP_SAVE'}, update=_update_page)

    @classmethod
    def unregister(cls):
        global _BROWSER_DIALOG, _BROWSER_PREFETCH
        _BROWSER_DIALOG = None
        _BROWSER_PREFETCH = None
        if bpy.app.timers.is_registered(_prepare_browser_prefetch):
            bpy.app.timers.unregister(_prepare_browser_prefetch)

    def check(self, context):
        from . import appearance
        _clamp_page(self, context)
        thumbnail_page = (self.kind, self.filter, self.page)
        if thumbnail_page != getattr(self, "_thumbnail_page", None):
            _warm_browser_thumbnails(self, context)
            self._thumbnail_page = thumbnail_page
        state = (self.kind, self.filter, self.page,
                 context.window_manager.arc_browser_multiple, appearance._PREVIEW_GENERATION)
        changed = state != getattr(self, "_last_check_state", None)
        self._last_check_state = state
        return changed

    def invoke(self, context, event):
        global _BROWSER_DIALOG
        _BROWSER_DIALOG = self
        on_multiple_changed(context)
        _clamp_page(self, context)
        _warm_browser_thumbnails(self, context)
        self._thumbnail_page = (self.kind, self.filter, self.page)
        return context.window_manager.invoke_props_dialog(self, width=BROWSER_WIDTH, title="Browse", confirm_text="Import")

    def draw(self, context):
        self._popup_region = context.region
        layout = self.layout
        layout.ui_units_x = BROWSER_WIDTH / 20.0
        head = layout.row(align=True)
        head.prop(self, "kind", text="")
        head.prop(self, "filter", text="", icon="VIEWZOOM", placeholder="Search")
        options = layout.row(align=True)
        options.prop(context.window_manager, "arc_browser_multiple")
        multiple = context.window_manager.arc_browser_multiple
        selected = selected_items(context, self.kind)
        items = collect_items(context, self.kind, self.filter)
        columns = BROWSER_COLUMNS
        page_size = BROWSER_PAGE_SIZE
        pages = max(1, (len(items) + page_size - 1) // page_size)
        if self.page > pages:
            self.page = pages
        page = min(self.page, pages)
        if pages > 1:
            navigation = layout.row(align=True)
            previous = navigation.row(align=True)
            previous.enabled = page > 1
            previous.operator("arc_outfits.browser_page", text="", icon="TRIA_LEFT").direction = -1
            navigation.prop(self, "page", text="", slider=False)
            following = navigation.row(align=True)
            following.enabled = page < pages
            following.operator("arc_outfits.browser_page", text="", icon="TRIA_RIGHT").direction = 1
        current = current_selection(context, self.kind)
        grid = layout.grid_flow(row_major=True, columns=columns, even_columns=True, even_rows=False)
        for ident, label in items[(page - 1) * page_size:page * page_size]:
            cell = grid.column(align=True)
            preview = icon_id(context, self.kind, ident)
            image = cell.row(align=True)
            image.template_icon(icon_value=preview or _placeholder_icon_id(), scale=max(1.0, (BROWSER_WIDTH - 40.0) / columns / 20.0))
            op = cell.operator("arc_outfits.icon_browser_pick", text=label, depress=ident in selected if multiple else ident == current)
            op.kind, op.item_id = self.kind, ident
        footer = layout.row(align=True)
        confirm = footer.row(align=True)
        confirm.enabled = bool(selected if multiple else current) and not _job_busy() and _BROWSER_IMPORT_ACTIVE is None
        confirm.template_popup_confirm("arc_outfits.import_browser_selection", text="Import", icon="IMPORT", cancel_text="").kind = self.kind

    def execute(self, context):
        global _BROWSER_DIALOG
        _BROWSER_DIALOG = None
        return bpy.ops.arc_outfits.import_browser_selection('EXEC_DEFAULT', kind=self.kind)

    def cancel(self, context):
        global _BROWSER_DIALOG
        if _BROWSER_DIALOG is self:
            _BROWSER_DIALOG = None

def draw_browse_button(layout, kind: str):
    op = layout.operator("arc_outfits.icon_browser", text="", icon="IMGDISPLAY")
    op.kind = kind
    return op

classes = (
    ARC_OUTFITS_OT_AddGearPart,
    ARC_OUTFITS_OT_IconBrowserPick,
    ARC_OUTFITS_OT_ImportBrowserSelection,
    ARC_OUTFITS_OT_BrowserPage,
    ARC_OUTFITS_OT_IconBrowser,
)
