"""What the edit buttons act on, import ids, and the one running batch job.

Pure logic: no draw code, no file IO, importable without the sidebar or bpy.
One resolver answers status lines, polls and execute alike.

Ownership rule: a target is the NEAREST role-valid owned root above the selection. The walk
never climbs past another import's objects and never treats a user grouping empty (or its
other children) as part of an import.
"""

import itertools
import os
import re
import time
from collections import namedtuple


ROLES = ("character", "hair", "facial_hair", "backpack", "weapon", "enemy", "effect", "asset")
SOURCES = ("selection", "last_import")
JOB_STATUSES = ("completed", "cancelled", "failed")

# source: "selection" (found from the selected objects) or "last_import" (fallback).
Target = namedtuple("Target", "obj name source")

PROP_ID = "arc_import_id"
PROP_KIND = "arc_import_kind"
PROP_LABEL = "arc_import_label"

_ROLE_KEY = "arc_assembly_role"
_CHARACTER_ROLES = frozenset(("Outfit", "Body", "Head"))
_PART_ROLES = frozenset(("Hair", "FacialHair", "Backpack", "BackpackFrame", "BackpackStrap",
                         "Attachment", "Charm", "RaiderTool"))
_CHARACTER_KINDS = frozenset(("character", "outfit"))
_FALLBACK_ROLES = frozenset(("weapon", "enemy", "character", "asset"))
_ASSET_FALLBACK = ("character", "weapon", "enemy")

_POLL_MESSAGES = {
    "character": "Select an imported character first",
    "hair": "Select a character with imported hair first",
    "facial_hair": "Select a character with imported facial hair first",
    "backpack": "Select a character with an imported backpack first",
    "weapon": "Select an imported weapon first",
    "enemy": "Select an imported ARC enemy first",
    "effect": "Select an imported effect first",
    "asset": "Select an imported asset first",
}

NO_CHARACTER_MESSAGE = "Import an outfit or body first, or switch to New Character"
NOTHING_TO_REMOVE = "Nothing to remove"
NOT_CANCELLABLE = "Cannot be stopped once started"


# ---------------------------------------------------------------- object helpers

def _get(obj, key, default=None):
    try:
        return obj.get(key, default)
    except Exception:
        return default


def _alive(obj):
    if obj is None:
        return False
    try:
        obj.name
        return True
    except Exception:
        return False


def _top(obj):
    seen = 0
    while obj is not None and getattr(obj, "parent", None) is not None and seen < 256:
        obj = obj.parent
        seen += 1
    return obj


def _chain(obj):
    """obj and its parents, nearest first."""
    seen = 0
    while obj is not None and seen < 256:
        yield obj
        obj = getattr(obj, "parent", None)
        seen += 1


def _descendants(obj):
    try:
        return tuple(getattr(obj, "children_recursive", ()) or ())
    except Exception:
        return ()


def _selection(context):
    objects = []
    active = getattr(context, "active_object", None)
    if active is not None:
        objects.append(active)
    try:
        selected = getattr(context, "selected_objects", None) or ()
    except Exception:
        selected = ()
    for obj in selected:
        if obj is not None and obj not in objects:
            objects.append(obj)
    return [obj for obj in objects if _alive(obj)]


def _hair_role(obj):
    role = str(_get(obj, _ROLE_KEY) or "")
    if role in ("Hair", "FacialHair"):
        return role
    if role:
        return ""
    try:
        from ..Outfit_Accessories import appearance
        return appearance.hair_object_role(obj) or ""
    except Exception:
        return ""


def _is_weapon_root(obj):
    return bool(_get(obj, "arc_weapon_assembly"))


def _is_weapon_member(obj):
    return _is_weapon_root(obj) or bool(_get(obj, "arc_weapon_owner")) or _get(obj, PROP_KIND) == "weapon"


def _is_enemy(obj):
    if _get(obj, "arc_codex_enemy_key") or _get(obj, PROP_KIND) == "enemy":
        return True
    try:
        for slot in getattr(obj, "material_slots", ()) or ():
            mat = getattr(slot, "material", None)
            if mat is not None and "arc_alertness_live" in str(getattr(mat, "name", "")):
                return True
    except Exception:
        pass
    return False


def _is_effect(obj):
    return bool(_get(obj, "arc_prop_fx") or _get(obj, "arc_ns_asset") or _get(obj, PROP_KIND) == "effect")


def _is_part(obj):
    """A character part (hair, backpack, charm...): owned by the character it sits under."""
    if str(_get(obj, _ROLE_KEY) or "") in _PART_ROLES:
        return True
    kind = _get(obj, PROP_KIND)
    return bool(kind) and role_for_kind(kind) in ("hair", "facial_hair", "backpack")


def _is_character_member(obj):
    kind = _get(obj, PROP_KIND)
    if kind in _CHARACTER_KINDS:
        return True
    if kind and role_for_kind(kind) != "character":
        return False
    role = str(_get(obj, _ROLE_KEY) or "")
    if role in _CHARACTER_ROLES:
        return True
    return bool(_get(obj, "arc_psk_path")) and not _is_weapon_member(obj) and not _is_enemy(obj)


def _is_arc(obj):
    return bool(_get(obj, PROP_ID) or _get(obj, _ROLE_KEY) or _get(obj, "arc_psk_path")
                or _is_weapon_member(obj) or _is_enemy(obj) or _is_effect(obj))


def _climb(item, test):
    """From a role-valid item, climb to the root of the same import only.

    Stamped: parents with the same import id. Unstamped (older scenes): parents that pass the
    role test, or an unstamped armature directly above (an armature is part of its asset; a
    user grouping empty is not).
    """
    import_id = _get(item, PROP_ID)
    seen = 0
    while seen < 256:
        parent = getattr(item, "parent", None)
        if parent is None:
            break
        parent_id = _get(parent, PROP_ID)
        if import_id:
            if parent_id != import_id:
                break
        elif parent_id:
            if not test(parent):
                break
            import_id = parent_id
        elif not (test(parent) or (getattr(parent, "type", "") == "ARMATURE" and not _is_arc(parent))):
            break
        item = parent
        seen += 1
    return item


def _owns_tagged(armature, test):
    """An unstamped rig owns the tagged meshes directly under it (older enemy imports)."""
    for child in _descendants(armature):
        if test(child) and not _get(child, PROP_ID):
            return True
    return False


def _nearest(obj, test, passable=None):
    """Nearest role-valid owned root at or above obj, or None.

    Walks parents; stops at the first item passing test. Items of another import (any Arc
    object that is not passable) end the walk with None. Plain user objects are walked past,
    so a cube under a character rig is the character's, but a mesh under a user empty that
    also holds a character is not.
    """
    for item in _chain(obj):
        if test(item):
            return _climb(item, test)
        if getattr(item, "type", "") == "ARMATURE" and not _is_arc(item) and _owns_tagged(item, test):
            return item
        if _is_arc(item) and not (passable is not None and passable(item)):
            return None
    return None


def _character_root(obj):
    """Root armature of the character import that owns obj, or None."""
    return _nearest(obj, _is_character_member, passable=_is_part)


def _role_test(role):
    if role == "hair":
        return lambda o: getattr(o, "type", "MESH") == "MESH" and _hair_role(o) == "Hair"
    if role == "facial_hair":
        return lambda o: getattr(o, "type", "MESH") == "MESH" and _hair_role(o) == "FacialHair"
    if role == "backpack":
        return lambda o: getattr(o, "type", "MESH") == "MESH" and str(_get(o, _ROLE_KEY) or "") == "Backpack"
    return None


def _weapon_root(obj):
    for item in _chain(obj):
        if _is_weapon_root(item):
            return item
        if _is_arc(item) and not _is_weapon_member(item):
            return None
    return None


def _effect_root(obj):
    found = _nearest(obj, _is_effect)
    return found


def _from_selection(obj, role):
    if role == "character":
        return _character_root(obj)
    if role == "weapon":
        return _weapon_root(obj)
    if role == "enemy":
        return _nearest(obj, _is_enemy)
    if role == "effect":
        return _effect_root(obj)
    if role == "asset":
        for finder in (_weapon_root, lambda o: _nearest(o, _is_enemy), _effect_root, _character_root):
            hit = finder(obj)
            if hit is not None:
                return hit
        for item in _chain(obj):
            if _is_arc(item):
                return _climb(item, _is_arc)
        return None
    test = _role_test(role)
    if test is None:
        return None
    for item in _chain(obj):
        if test(item):
            return item
        if _is_arc(item) and not test(item):
            break
    root = _character_root(obj)
    for child in members(root):
        if child is not root and test(child):
            return child
    return None


def members(target, scene=None):
    """Objects owned by a target (a Target or an object).

    Same import id (from scene, or the root's scenes), plus descendants of the root that are
    not another import's objects (sub-parts like hair added to a character belong; a weapon
    held in a hand does not). Never the siblings under a shared parent.
    """
    root = getattr(target, "obj", target)
    if not _alive(root):
        return []
    out = [root]
    seen = {id(root)}
    import_id = _get(root, PROP_ID)
    if import_id:
        pool = getattr(scene, "objects", None) if scene is not None else None
        if pool is None:
            try:
                pool = [o for sc in (getattr(root, "users_scene", ()) or ()) for o in sc.objects]
            except Exception:
                pool = ()
        for obj in pool or ():
            if id(obj) not in seen and _get(obj, PROP_ID) == import_id:
                out.append(obj)
                seen.add(id(obj))
    is_character = _is_character_member(root) and not _is_weapon_root(root)

    def foreign(child):
        child_id = _get(child, PROP_ID)
        if child_id and child_id != import_id:
            return not (is_character and _is_part(child))
        if child_id:
            return False
        if _is_weapon_root(child):
            return True
        if _is_enemy(child) and not _is_enemy(root):
            return True
        return _is_effect(child) and not _is_effect(root)

    stack = list(getattr(root, "children", ()) or ())
    guard = 0
    while stack and guard < 100000:
        guard += 1
        child = stack.pop(0)
        if foreign(child):
            continue
        if id(child) not in seen:
            out.append(child)
            seen.add(id(child))
        stack.extend(getattr(child, "children", ()) or ())
    return out


# ---------------------------------------------------------------- names

_PREFIX_RE = re.compile(r"^(SK|SKM|SM|SKEL|BP|ABP)_", re.IGNORECASE)
_SUFFIX_RE = re.compile(r"(_Skeleton|_Skel|_Rig|_Armature)$", re.IGNORECASE)
_BRACKET_RE = re.compile(r"\s*[\[(][^\])]*[\])]")
_DUP_RE = re.compile(r"\.\d{3}$")


def clean_name(text):
    text = _DUP_RE.sub("", str(text or ""))
    text = _BRACKET_RE.sub("", text).strip()
    text = _PREFIX_RE.sub("", text)
    text = _SUFFIX_RE.sub("", text)
    text = re.sub(r"[_\s]+", " ", text).strip()
    return text


def display_name(obj):
    if obj is None:
        return ""
    obj = getattr(obj, "obj", obj)
    label = _get(obj, PROP_LABEL)
    if label:
        return str(label)
    # Catalog label of an enemy (older imports stamped it on the meshes only, not the rig).
    for holder in [obj] + list(getattr(obj, "children", ()) or ())[:16]:
        value = _get(holder, "arc_codex_enemy_label")
        if value:
            return str(value).split("(", 1)[0].strip() or str(value)
    name = getattr(obj, "name", "") or ""
    return clean_name(name) or name


def poll_message(role):
    return _POLL_MESSAGES.get(role, "Select an imported asset first")


def editing_text(target, verb="Editing"):
    """Status line for a target: "Editing: X", or "Editing last imported: X" on fallback."""
    if target is None:
        return ""
    name = getattr(target, "name", "") or display_name(target)
    if getattr(target, "source", "selection") == "last_import":
        return "%s last imported: %s" % (verb, name)
    return "%s: %s" % (verb, name)


_KIND_ROLES = {
    "outfit": "character", "character": "character", "body": "character", "head": "character",
    "hair": "hair", "facialhair": "facial_hair", "facial_hair": "facial_hair",
    "backpack": "backpack", "attachment": "backpack", "charm": "backpack",
    "backpackframe": "backpack", "backpackstrap": "backpack", "raidertool": "backpack",
    "weapon": "weapon", "enemy": "enemy", "fx": "effect", "effect": "effect",
}


def role_for_kind(kind):
    """Map an import kind or assembly role ("Outfit", "FacialHair", ...) to a ROLES entry."""
    return _KIND_ROLES.get(str(kind or "").replace(" ", "").lower(), "asset")


# ---------------------------------------------------------------- import ids

_COUNTER = itertools.count()
_LAST_BY_KIND = {}
_GENERATION = [0]


def import_generation():
    """Bumped on every stamp and removal; key UI memos on it so renames and replaces show."""
    return _GENERATION[0]


def _bump():
    _GENERATION[0] += 1


def new_import_id():
    """Sortable: newer ids compare greater."""
    return "%012x%04x%04x" % (int(time.time() * 1000), next(_COUNTER) & 0xFFFF, int.from_bytes(os.urandom(2), "big"))


def stamp(objects, import_id, kind, label):
    names = []
    for obj in objects or ():
        if not _alive(obj):
            continue
        try:
            obj[PROP_ID] = str(import_id)
            obj[PROP_KIND] = str(kind or "asset")
            obj[PROP_LABEL] = str(label or "")
            names.append(obj.name)
        except Exception:
            continue
    if names:
        _LAST_BY_KIND[str(kind or "asset")] = (str(import_id), tuple(names))
        try:  # Central post-build graph hook: every builder has returned by now.
            import bpy
            from .materials import graph_layout
            graph_layout.finalize_objects([bpy.data.objects.get(n) for n in names], getattr(bpy.context, "scene", None))
        except Exception as exc:
            print("[ui_targets] graph finalize failed: %s" % exc)
    _bump()


def import_entries(scene):
    groups = {}
    for obj in getattr(scene, "objects", ()) or ():
        import_id = _get(obj, PROP_ID)
        if not import_id:
            continue
        entry = groups.get(import_id)
        if entry is None:
            entry = groups[import_id] = {"id": str(import_id), "kind": str(_get(obj, PROP_KIND) or "asset"),
                                         "label": str(_get(obj, PROP_LABEL) or ""), "objects": []}
        entry["objects"].append(obj)
    for entry in groups.values():
        if not entry["label"]:
            entry["label"] = display_name(_entry_root(entry["objects"]))
    return sorted(groups.values(), key=lambda e: e["id"], reverse=True)


def objects_of(scene, import_id):
    import_id = str(import_id)
    return [obj for obj in getattr(scene, "objects", ()) or () if _get(obj, PROP_ID) == import_id]


def _entry_root(objects):
    objects = list(objects)
    if not objects:
        return None
    ids = {id(o) for o in objects}
    tops = [o for o in objects if getattr(o, "parent", None) is None or id(o.parent) not in ids]
    for test in (_is_weapon_root, lambda o: getattr(o, "type", "") == "ARMATURE"):
        for obj in tops:
            if test(obj):
                return obj
    return (tops or objects)[0]


def _scene_has(scene, obj):
    objects = getattr(scene, "objects", None)
    if objects is None or obj is None:
        return False
    try:
        getter = getattr(objects, "get", None)
        if getter is not None:
            return getter(obj.name) is obj
        return obj in objects
    except Exception:
        return False


def _depth(obj):
    return sum(1 for _ in _chain(obj))


def _scene_collections(scene):
    root = getattr(scene, "collection", None)
    if root is None:
        return []
    out = [root]
    try:
        out.extend(root.children_recursive)
    except Exception:
        pass
    return out


def _default_remover(obj):
    import bpy
    data = getattr(obj, "data", None)
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is None:
        return
    try:
        if data.users == 0:
            for pool in (bpy.data.meshes, bpy.data.armatures, bpy.data.curves, bpy.data.lights, bpy.data.cameras):
                if pool.get(data.name) is data:
                    pool.remove(data)
                    break
    except Exception:
        pass


SHARED_COLLECTION_MESSAGE = ("This import is in a collection shared with another scene. "
                             "Make the collection scene-local before deleting")


def _other_scenes(obj, scene):
    try:
        scenes = list(getattr(obj, "users_scene", ()) or ())
    except Exception:
        scenes = []
    return [s for s in scenes if s is not scene and getattr(s, "name", None) != getattr(scene, "name", None)]


def _in_shared_collection(obj, scene):
    """True when a collection holding obj is in this scene's tree and in another scene's tree.

    Objects linked straight into another scene's own collections stay removable (unlinked here).
    """
    others = _other_scenes(obj, scene)
    if not others:
        return False
    master = getattr(scene, "collection", None)
    mine = [c for c in _scene_collections(scene) if c is not master]
    try:
        holders = [c for c in (getattr(obj, "users_collection", ()) or ())
                   if c is not master and any(c is m for m in mine)]
    except Exception:
        holders = []
    for other in others:
        tree = _scene_collections(other)
        for coll in holders:
            if any(coll is c for c in tree):
                return True
    return False


def remove_refusal(scene, import_id):
    """Why remove_import would refuse ("" when it may run). Checked before any mutation."""
    for obj in objects_of(scene, import_id) if import_id else ():
        try:
            if _in_shared_collection(obj, scene):
                return SHARED_COLLECTION_MESSAGE
        except Exception:
            return SHARED_COLLECTION_MESSAGE
    return ""


_REMOVE_REFUSAL = [""]


def last_remove_refusal():
    """Why the last remove_import call touched nothing ("" when it ran or found nothing)."""
    return _REMOVE_REFUSAL[0]


def remove_import(scene, import_id, remover=None):
    """Remove one stamped import from scene -> (deleted, unlinked).

    Objects also linked directly into another scene are only unlinked from this scene's
    collections; the rest are deleted (children first). Nothing stamped with import_id: (0, 0).
    Refusal: when remove_refusal(scene, import_id) is not "" (a holding collection is shared
    with another scene) nothing is touched and (0, 0) is returned; callers show that text.
    """
    import_id = str(import_id or "")
    _REMOVE_REFUSAL[0] = ""
    if not import_id:
        return 0, 0
    refusal = job_busy_message() or remove_refusal(scene, import_id)
    if refusal:
        _REMOVE_REFUSAL[0] = refusal
        set_error("Cannot delete import", refusal)
        return 0, 0
    objects = sorted(objects_of(scene, import_id), key=_depth, reverse=True)
    remove = remover or _default_remover
    deleted = unlinked = 0
    for obj in objects:
        if _other_scenes(obj, scene):
            done = False
            for coll in _scene_collections(scene):
                try:
                    if coll.objects.get(obj.name) is obj:
                        coll.objects.unlink(obj)
                        done = True
                except Exception:
                    continue
            unlinked += 1 if done else 0
            continue
        try:
            remove(obj)
            deleted += 1
        except Exception:
            continue
    for kind, hit in list(_LAST_BY_KIND.items()):
        if hit[0] == import_id:
            _LAST_BY_KIND.pop(kind, None)
    _bump()
    return deleted, unlinked


def remove_summary(scene, import_id):
    """(label, object count) for a confirm dialog, or ("", 0) when nothing is stamped."""
    objects = objects_of(scene, import_id)
    if not objects:
        return "", 0
    label = str(_get(objects[0], PROP_LABEL) or "") or display_name(_entry_root(objects))
    return label, len(objects)


def _last_of_kind(scene, role):
    kinds = _CHARACTER_KINDS if role == "character" else (role,)
    for kind in kinds:
        hit = _LAST_BY_KIND.get(kind)
        if not hit:
            continue
        objects = getattr(scene, "objects", None)
        found = []
        for name in hit[1]:
            try:
                obj = objects.get(name) if hasattr(objects, "get") else next((o for o in objects if o.name == name), None)
            except Exception:
                obj = None
            if obj is not None and _get(obj, PROP_ID) == hit[0]:
                found.append(obj)
        if found:
            return _entry_root(found)
    if role == "weapon":
        name = _get(scene, "arc_weapon_last_assembly") or getattr(scene, "arc_weapon_last_assembly", "")
        if name:
            try:
                obj = scene.objects.get(name)
            except Exception:
                obj = None
            if obj is not None and _is_weapon_root(obj):
                return obj
    for entry in import_entries(scene):
        if entry["kind"] in kinds:
            return _entry_root(entry["objects"])
    return None


def _last_asset(scene):
    best = None
    best_id = None
    for role in _ASSET_FALLBACK:
        obj = _last_of_kind(scene, role)
        if obj is None:
            continue
        obj_id = str(_get(obj, PROP_ID) or "")
        if best is None or obj_id > best_id:
            best, best_id = obj, obj_id
    return best


def resolve(context, role):
    """Target(obj, name, source) for role, or None. source is "selection" or "last_import"."""
    for obj in _selection(context):
        found = _from_selection(obj, role)
        if found is not None:
            return Target(found, display_name(found), "selection")
    if role not in _FALLBACK_ROLES:
        return None
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    if role == "character":
        # Something Arc is selected but it is not a character: do not guess.
        if any(_is_arc(o) for o in _selection(context)):
            return None
    found = _last_asset(scene) if role == "asset" else _last_of_kind(scene, role)
    if found is None:
        return None
    return Target(found, display_name(found), "last_import")


def scene_has_character(scene):
    """Any character (outfit or body) import in scene, stamped or older."""
    if _last_of_kind(scene, "character") is not None:
        return True
    for obj in getattr(scene, "objects", ()) or ():
        if getattr(obj, "type", "") == "ARMATURE" and _is_character_member(obj):
            return True
    return False


_KIND_WORDS = {"character": "a character", "hair": "a hair part", "facial_hair": "a facial hair part",
               "backpack": "a gear part", "weapon": "a weapon", "enemy": "an enemy",
               "effect": "an effect", "asset": "an asset"}


def target_role(target):
    """ROLES entry the resolved object belongs to ("character", "weapon", "enemy", ...)."""
    obj = getattr(target, "obj", None)
    if obj is None:
        return ""
    kind = _get(obj, PROP_KIND)
    if not kind:
        for item in _chain(obj):
            kind = _get(item, PROP_KIND)
            if kind:
                break
    if kind:
        return role_for_kind(kind)
    try:
        if _is_weapon_member(obj):
            return "weapon"
        if _is_enemy(obj):
            return "enemy"
        if _is_effect(obj):
            return "effect"
        if _is_character_member(obj):
            return "character"
    except Exception:
        pass
    return "asset"


def animation_refusal(target):
    """"" when target can take animations, else "Animations need a character. Editing: X is an enemy"."""
    if target is None:
        return ""
    role = target_role(target)
    if role in ("character", "hair", "facial_hair", "backpack", ""):
        return ""
    return "Animations need a character. Editing: %s is %s" % (
        getattr(target, "name", "") or display_name(target.obj), _KIND_WORDS.get(role, "an asset"))


def target_armature(target):
    """Armature of a resolved Target: the object itself or its nearest armature parent."""
    obj = getattr(target, "obj", None)
    if obj is None:
        return None
    if getattr(obj, "type", "") == "ARMATURE":
        return obj
    for item in _chain(obj):
        if getattr(item, "type", "") == "ARMATURE":
            return item
    return None


# ---------------------------------------------------------------- part destination

PART_MODES = ("NEW", "ADD", "REPLACE")


def default_part_mode(scene, has_armatures=True):
    """Part mode before the user picks one: New Character until the scene holds a character."""
    if not has_armatures:
        return "NEW"
    return "ADD" if scene_has_character(scene) else "NEW"


def part_destination(context, mode):
    """One destination plan for a Character Parts import, computed once and passed down.

    Returns dict: mode, target (Target or None), armature (object or None), replace (bool),
    standalone (bool), error (str: empty when the import may run), text (status line).
    NEW is always standalone. ADD and REPLACE need a character (last import allowed).
    """
    mode = str(mode or "ADD").upper()
    if mode not in PART_MODES:
        mode = "ADD"
    plan = {"mode": mode, "target": None, "armature": None, "replace": False, "standalone": mode == "NEW",
            "error": "", "text": ""}
    if mode == "NEW":
        plan["text"] = "Creates a new character"
        return plan
    target = resolve(context, "character")
    if target is None:
        scene = getattr(context, "scene", None)
        plan["error"] = poll_message("character") if scene is not None and scene_has_character(scene) \
            else NO_CHARACTER_MESSAGE
        return plan
    plan["target"] = target
    plan["armature"] = target_armature(target)
    if plan["armature"] is None:
        plan["error"] = poll_message("character")
        return plan
    plan["replace"] = mode == "REPLACE"
    plan["text"] = editing_text(target, "Replaces on" if plan["replace"] else "Adds to")
    return plan


def part_button_text(mode, part):
    """Button text by mode: "Import New Hair", "Add Hair to Character", "Replace Hair..."."""
    mode = str(mode or "ADD").upper()
    if mode == "NEW":
        return "Import New %s" % part
    if mode == "REPLACE":
        return "Replace %s..." % part
    return "Add %s to Character" % part


# ---------------------------------------------------------------- outfit visibility

def visible_outfit_key(context):
    """The chosen outfit key when it is visible in the outfit list (search and filter), else "".

    Uses ui_lists.outfit_is_visible when present; without it, the chosen key counts as visible.
    """
    scene = getattr(context, "scene", None)
    key = str(getattr(scene, "arc_selected_outfit", "") or "") if scene is not None else ""
    if not key:
        return ""
    try:
        from . import ui_lists
        check = getattr(ui_lists, "outfit_is_visible", None)
    except Exception:
        check = None
    if check is None:
        return key
    try:
        return key if check(context, key) else ""
    except Exception:
        return ""


OUTFIT_HIDDEN_MESSAGE = "The chosen outfit is hidden by the search or filter"


def outfit_label(context, key):
    """Outfit display name for a catalog key ("Goalie"), never the bare word "Outfit"."""
    key = str(key or "")
    if not key:
        return ""
    try:
        from . import importing
        rows = importing.load_outfit_csv(importing.get_outfit_csv_path(context))
        row = importing.find_outfit_row(rows or [], key)
    except Exception:
        row = None
    if row:
        name = str(row.get("Flavour") or "").strip()
        if name:
            return name
    return key


# ---------------------------------------------------------------- jobs

_JOB = None
_REPORT = None
_ERROR = None


def job_begin(kind, label, total, cancel_idname="", cancellable=None):
    global _JOB, _REPORT
    if _JOB is not None:
        return False
    cancel_idname = str(cancel_idname or "")
    _JOB = {"kind": str(kind), "label": str(label), "done": 0, "total": max(0, int(total or 0)),
            "current": "", "cancel_idname": cancel_idname,
            "cancellable": bool(cancel_idname) if cancellable is None else bool(cancellable),
            "started": time.time()}
    _REPORT = None
    job_owned_clear()
    return True


def job_step(done, current_label, total=None):
    if _JOB is None:
        return
    _JOB["done"] = max(0, int(done or 0))
    _JOB["current"] = str(current_label or "")
    if total is not None:
        _JOB["total"] = max(0, int(total or 0))


def job_end(imported, failed, skipped, lines, status="completed", left=None):
    """Close the running job and store its report.

    skipped = items deliberately not imported (already present, unavailable in the catalog).
    left = items never attempted (stopped early); never pass the remaining queue as skipped.
    The planned total from job_begin is kept. left=None: planned total minus the rest.
    """
    global _JOB, _REPORT
    job = _JOB or {"kind": "", "label": "", "total": 0}
    lines = [str(line) for line in (lines or ())]
    status = status if status in JOB_STATUSES else "completed"
    # Failure lines read "Failed: <key>: <reason>".
    failed_keys = [line.split(":", 2)[1].strip() for line in lines
                   if line.startswith("Failed:") and line.count(":") >= 2]
    imported, failed, skipped = int(imported or 0), int(failed or 0), int(skipped or 0)
    planned = int(job.get("total", 0) or 0)
    if left is None:
        total = max(planned, imported + failed + skipped)
        left = max(0, total - imported - failed - skipped)
    else:
        left = max(0, int(left or 0))
        total = max(planned, imported + failed + skipped + left)
    _REPORT = {"kind": job.get("kind", ""), "label": job.get("label", ""), "imported": imported,
               "failed": failed, "skipped": skipped, "lines": lines, "failed_keys": failed_keys,
               "status": status, "total": total, "left": left}
    _JOB = None


_OWNED = {"objects": {}, "meshes": {}}


def _pointer(item):
    try:
        return int(item.as_pointer())
    except Exception:
        return id(item)


def job_own(objects=(), meshes=()):
    """Record datablocks the running job created. Cancel cleanup and stamping use only these."""
    for pool, items in (("objects", objects), ("meshes", meshes)):
        for item in items or ():
            try:
                _OWNED[pool][_pointer(item)] = item.name
            except Exception:
                continue


def job_owned(pool="objects"):
    """Live datablocks recorded by job_own, matched by pointer and name (renamed or freed ones drop out)."""
    import bpy
    data = bpy.data.objects if pool == "objects" else bpy.data.meshes
    out = []
    for ptr, name in list(_OWNED.get(pool, {}).items()):
        item = data.get(name)
        if item is None:
            # Renamed by the importer after it was recorded: look it up by pointer.
            item = next((d for d in data if _pointer(d) == ptr), None)
        if item is not None and _pointer(item) == ptr:
            out.append(item)
    return out


def job_owned_clear():
    _OWNED["objects"].clear()
    _OWNED["meshes"].clear()


class job_track:
    """Context manager: objects and meshes created inside the block are owned by the job.

    Only the synchronous block is measured, so objects a user adds between timer ticks
    are never claimed.
    """

    def __enter__(self):
        import bpy
        self._objects = {_pointer(o) for o in bpy.data.objects}
        self._meshes = {_pointer(m) for m in bpy.data.meshes}
        return self

    def __exit__(self, *_exc):
        import bpy
        job_own([o for o in bpy.data.objects if _pointer(o) not in self._objects],
                [m for m in bpy.data.meshes if _pointer(m) not in self._meshes])
        return False


def job_active():
    if _JOB is None:
        return None
    return {k: _JOB[k] for k in ("kind", "label", "done", "total", "current", "cancel_idname", "cancellable")}


def job_busy_message():
    """Refusal text while a job runs ("" when free): every import entry point checks it."""
    if _JOB is None:
        return ""
    return "Wait for %s to finish" % (_JOB.get("label") or "the running import")


def job_report():
    return dict(_REPORT) if _REPORT is not None else None


def job_clear_report():
    global _REPORT
    _REPORT = None


def report_title(report=None):
    """Card title: "Finished", "Stopped: N imported, M left", "Finished with N failures"."""
    report = report or _REPORT
    if not report:
        return ""
    if report.get("status") == "cancelled":
        return "Stopped: %d imported, %d left" % (report.get("imported", 0), report.get("left", 0))
    failed = int(report.get("failed", 0) or 0)
    if failed or report.get("status") == "failed":
        return "Finished with %d failure%s" % (failed, "" if failed == 1 else "s")
    return "Finished"


def report_is_error(report=None):
    """Red only when everything failed."""
    report = report or _REPORT
    if not report:
        return False
    failed = int(report.get("failed", 0) or 0)
    imported = int(report.get("imported", 0) or 0)
    return imported == 0 and (failed > 0 or report.get("status") == "failed")


def report_text(report=None):
    """Plain text of a finished job for Copy Report."""
    report = report or _REPORT
    if not report:
        return ""
    head = "%s: %d imported, %d failed, %d skipped" % (
        report.get("label") or "Batch import", report.get("imported", 0), report.get("failed", 0), report.get("skipped", 0))
    if int(report.get("left", 0) or 0):
        head += ", %d left" % int(report.get("left", 0))
    title = report_title(report)
    lines = [head] + ([title] if title and title != "Finished" else []) + list(report.get("lines") or ())
    return "\n".join(lines)


# ---------------------------------------------------------------- last error

def last_error():
    return dict(_ERROR) if _ERROR is not None else None


def set_error(title, detail):
    global _ERROR
    _ERROR = {"title": str(title or "Import failed"), "detail": str(detail or ""), "time": time.time()}


def clear_error():
    global _ERROR
    _ERROR = None


# ---------------------------------------------------------------- batch plan counts
# How many items "Import All" would really queue, from the same planners the batch
# operators run. None = not counted yet (a timer counts it, then redraws). The cache key
# holds the source settings and the import generation, so a source/catalog change or a
# new/removed import recounts.

_BATCH_SOURCE_PROPS = ("arc_pak_dir", "arc_usmap_path", "arc_pioneer_root",
                       "arc_outfit_csv_path", "arc_weapon_include_ascended")
_BATCH_PLANNERS = {}
_BATCH_COUNTS = {}
_BATCH_PENDING = set()
_BATCH_GENERATION = [0]


def register_batch_planner(kind, planner):
    """planner(scene) -> int (items the batch would queue). Replaces the built-in planner."""
    _BATCH_PLANNERS[str(kind).upper()] = planner
    batch_plan_invalidate()


def batch_plan_invalidate():
    _BATCH_GENERATION[0] += 1
    _BATCH_COUNTS.clear()


def _batch_signature(scene, kind):
    values = []
    for prop in _BATCH_SOURCE_PROPS:
        try:
            values.append(str(getattr(scene, prop, "")))
        except Exception:
            values.append("")
    return (str(kind).upper(), getattr(scene, "name", ""), tuple(values),
            import_generation(), _BATCH_GENERATION[0])


def _builtin_planner(kind):
    kind = str(kind).upper()
    if kind in _BATCH_PLANNERS:
        return _BATCH_PLANNERS[kind]
    return _default_planners().get(kind)


def _default_planners():
    def outfits(scene):
        from . import operators
        return len(operators.all_outfit_jobs(scene)[0])

    def part(name):
        def plan(scene):
            from ..Outfit_Accessories import appearance_queue
            return len(appearance_queue.catalog_jobs(name, scene)[0])
        return plan

    def weapons(scene):
        from ..Weapons import weapon_catalog as catalog, weapon_assets as assets
        entries = list(catalog.weapons())
        count = len(entries)
        if getattr(scene, "arc_weapon_include_ascended", False):
            roots = assets.pioneer_roots(scene)
            count += sum(len(list(assets.variants(entry, roots) or ())) for entry in entries)
        return count

    def enemies(scene):
        from ..Arc import arc_catalog_data
        return len(arc_catalog_data.ARC_ENEMIES)

    planners = {"OUTFITS": outfits, "WEAPONS": weapons, "ENEMIES": enemies}
    for kind, name in (("HEADS", "HEAD"), ("HAIR", "HAIR"), ("FACIAL_HAIR", "FACIAL_HAIR"),
                       ("BACKPACKS", "BACKPACK"), ("ATTACHMENTS", "ATTACHMENT"),
                       ("CHARMS", "CHARM"), ("TOOLS", "RAIDER_TOOL")):
        planners[kind] = part(name)
    return planners


def _count_now(scene, kind, key):
    _BATCH_PENDING.discard(key)
    planner = _builtin_planner(kind)
    if planner is None:
        return None
    try:
        count = max(0, int(planner(scene)))
    except Exception:
        count = None  # unresolved source: stay None, recount when the source changes
    _BATCH_COUNTS[key] = count
    return count


def batch_plan_count(scene, kind, now=False):
    """Items "Import All" for kind would queue, or None while not counted yet.

    The first call schedules the count on a timer (never inside draw) and returns None;
    now=True counts immediately (tests, operators).
    """
    if scene is None:
        return None
    key = _batch_signature(scene, kind)
    if key in _BATCH_COUNTS:
        return _BATCH_COUNTS[key]
    if now:
        return _count_now(scene, kind, key)
    if key not in _BATCH_PENDING:
        _BATCH_PENDING.add(key)
        scene_name = getattr(scene, "name", "")

        def run():
            try:
                import bpy
                target = bpy.data.scenes.get(scene_name)
            except Exception:
                target = None
            if target is None:
                _BATCH_PENDING.discard(key)
                return None
            _count_now(target, kind, key)
            try:
                for window in bpy.context.window_manager.windows:
                    for area in window.screen.areas:
                        area.tag_redraw()
            except Exception:
                pass
            return None

        try:
            import bpy
            bpy.app.timers.register(run, first_interval=0.0)
        except Exception:
            _BATCH_PENDING.discard(key)
            return _count_now(scene, kind, key)
    return None
