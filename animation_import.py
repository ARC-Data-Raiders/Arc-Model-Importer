"""Apply one game animation (PSA) to a scene armature and spawn notify props/FX."""
from __future__ import annotations

import importlib
import math
import os
from typing import Any, Optional

import bpy
from mathutils import Euler, Vector

from . import animation_catalog as acat
from . import fmdex
from . import utils

log = utils.get_logger()

_PROP_COLLECTION_PREFIX = "AnimNotify_"


def psa_import_available() -> bool:
    for name in ("psa.import_all", "psa.import_file"):
        try:
            getattr(bpy.ops.psa, name.split(".", 1)[1]).get_rna_type()
            return True
        except Exception:
            continue
    return _get_psa_importer_api()[0] is not None


def _get_psa_importer_api():
    for base in (
        "bl_ext.blender_org.io_scene_psk_psa",
        "bl_ext.user_default.io_scene_psk_psa",
        "io_scene_psk_psa",
    ):
        try:
            mod = importlib.import_module(f"{base}.psa.importer")
            fn = getattr(mod, "import_psa", None)
            opts_cls = getattr(mod, "PsaImportOptions", None)
            if callable(fn) and opts_cls is not None:
                return fn, opts_cls
        except ImportError:
            continue
    return None, None


def _get_read_psa_from_file():
    for base in (
        "bl_ext.blender_org.io_scene_psk_psa",
        "bl_ext.user_default.io_scene_psk_psa",
        "io_scene_psk_psa",
    ):
        try:
            mod = importlib.import_module(f"{base}.psa.reader")
            reader = getattr(mod, "read_psa", None) or getattr(mod, "read_psa_from_file", None)
            if callable(reader):
                return reader
        except ImportError:
            continue
        try:
            mod = importlib.import_module(f"{base}.psa.import_.operators")
            reader = getattr(mod, "read_psa_from_file", None)
            if callable(reader):
                return reader
        except ImportError:
            continue
    return None


def resolve_target_armature(context) -> Optional[Any]:
    obj = getattr(context, "object", None)
    if obj is not None and obj.type == "ARMATURE":
        return obj
    if obj is not None:
        found = obj.find_armature()
        if found is not None:
            return found
    selected = [o for o in getattr(context, "selected_objects", []) if o.type == "ARMATURE"]
    if selected:
        return selected[0]
    last = getattr(context.scene, "arc_last_applied_anim_armature", "") or ""
    if last and last in bpy.data.objects:
        arm = bpy.data.objects[last]
        if arm.type == "ARMATURE":
            return arm
    arms = [o for o in context.scene.objects if o.type == "ARMATURE" and o.visible_get()]
    if len(arms) == 1:
        return arms[0]
    return None


def _activate_armature(context, armature) -> None:
    view = context.view_layer
    for obj in view.objects:
        obj.select_set(False)
    armature.hide_set(False)
    armature.hide_viewport = False
    armature.select_set(True)
    view.objects.active = armature


def _assign_action(armature, action, *, replace_previous: bool) -> None:
    if armature.animation_data is None:
        armature.animation_data_create()
    old_name = armature.get("arc_applied_action") or ""
    armature.animation_data.action = action
    armature["arc_applied_action"] = action.name
    if not replace_previous or not old_name or old_name == action.name:
        return
    old = bpy.data.actions.get(old_name)
    if old is None:
        return
    if old.users <= 1:
        bpy.data.actions.remove(old)


def _set_preview_range(context, action) -> None:
    try:
        start, end = action.frame_range
        scene = context.scene
        scene.frame_start = int(math.floor(start))
        scene.frame_end = max(int(math.ceil(end)), scene.frame_start + 1)
        scene.frame_current = scene.frame_start
    except Exception:
        pass


def _import_psa_direct(filepath: str, context, armature) -> bool:
    import_fn, opts_cls = _get_psa_importer_api()
    reader = _get_read_psa_from_file()
    if import_fn is None or opts_cls is None or reader is None:
        return False
    options = opts_cls()
    for name, value in (
        ("should_overwrite_existing", True),
        ("should_use_fake_user", False),
        ("should_stash", False),
    ):
        if hasattr(options, name):
            setattr(options, name, value)
    if hasattr(options, "armature"):
        options.armature = armature
    parsed = reader(filepath)
    import_fn(parsed, context, options)
    return True


def _import_psa_operator(filepath: str, context, armature) -> None:
    _activate_armature(context, armature)
    override = {
        "active_object": armature,
        "object": armature,
        "selected_objects": [armature],
        "selected_editable_objects": [armature],
        "view_layer": context.view_layer,
        "scene": context.scene,
        "window": context.window,
    }
    try:
        with context.temp_override(**override):
            if hasattr(bpy.ops.psa, "import_all"):
                bpy.ops.psa.import_all(filepath=filepath)
            else:
                bpy.ops.psa.import_file(filepath=filepath)
    except TypeError:
        bpy.ops.psa.import_file(filepath=filepath)


def import_psa_onto_armature(
    filepath: str,
    armature,
    context=None,
    *,
    replace_previous: bool = True,
) -> tuple:
    """Import a PSA and assign the new action to ``armature``.

    Returns ``(ok, message, action_or_none)``.
    """
    context = context or bpy.context
    if not filepath or not os.path.isfile(filepath):
        return False, f"PSA not found: {filepath}", None
    if armature is None or armature.type != "ARMATURE":
        return False, "Select an armature (outfit / character) first", None
    if not utils.psk_import_available():
        utils.ensure_psk_addon()
    if not psa_import_available() and not utils.psk_import_available():
        return False, "Unreal PSK/PSA extension is not enabled", None

    before_actions = set(bpy.data.actions.keys())
    try:
        used_direct = False
        try:
            used_direct = _import_psa_direct(filepath, context, armature)
        except Exception as exc:
            log.warning("direct PSA import failed (%s); falling back to operator: %s", filepath, exc)
        if not used_direct:
            _import_psa_operator(filepath, context, armature)
    except Exception as exc:
        return False, f"PSA import failed: {exc}", None

    new_names = [n for n in bpy.data.actions.keys() if n not in before_actions]
    action = None
    if new_names:
        action = bpy.data.actions[new_names[-1]]
    else:
        stem = os.path.splitext(os.path.basename(filepath))[0]
        action = bpy.data.actions.get(stem)
        if action is None:
            for act in bpy.data.actions:
                if stem.lower() in act.name.lower():
                    action = act
                    break
    if action is None:
        return False, "PSA imported but no Action was created — bone names may not match this armature", None

    _assign_action(armature, action, replace_previous=replace_previous)
    _set_preview_range(context, action)
    armature["arc_applied_anim"] = os.path.splitext(os.path.basename(filepath))[0]
    return True, f"Applied {action.name}", action


def _bone_lookup(armature) -> dict:
    out = {}
    for bone in armature.pose.bones:
        out[bone.name.lower()] = bone
        out[bone.name.lower().replace(" ", "_")] = bone
    return out


def _find_bone(armature, socket_name: str):
    if not socket_name:
        return None
    bones = _bone_lookup(armature)
    key = socket_name.strip().lower()
    if key in bones:
        return bones[key]
    # Unreal sockets often prefix with socket_ / attach_
    for prefix in ("socket_", "ik_", ""):
        hit = bones.get(prefix + key)
        if hit is not None:
            return hit
    for name, bone in bones.items():
        if name.endswith(key) or key.endswith(name):
            return bone
    return None


def _ensure_collection(name: str):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(coll)
    return coll


def _clear_spawned(armature) -> None:
    name = armature.get("arc_anim_prop_collection") or ""
    if not name:
        return
    coll = bpy.data.collections.get(name)
    if coll is None:
        return
    objects = list(coll.objects)
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.collections.remove(coll)
    if "arc_anim_prop_collection" in armature:
        del armature["arc_anim_prop_collection"]


def _link_only(obj, coll) -> None:
    for existing in list(obj.users_collection):
        existing.objects.unlink(obj)
    if obj.name not in coll.objects:
        coll.objects.link(obj)


def _parent_to_bone(obj, armature, bone_name: str, location, rotation, scale) -> None:
    obj.parent = armature
    obj.parent_type = "BONE"
    obj.parent_bone = bone_name
    obj.location = Vector(location)
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = Euler(
        (math.radians(rotation[0]), math.radians(rotation[1]), math.radians(rotation[2])),
        "XYZ",
    )
    obj.scale = Vector(scale if scale != (0.0, 0.0, 0.0) else (1.0, 1.0, 1.0))


def _resolve_mesh_psk(stem: str) -> str:
    if not stem:
        return ""
    for ext in (".psk", ".pskx"):
        found = fmdex.resolve_export_file(stem, ext)
        if found and os.path.isfile(found):
            return found
    folder = fmdex.resolve_mesh_asset_folder(stem)
    if not folder:
        return ""
    for ext in (".psk", ".pskx"):
        candidate = os.path.join(folder, stem + ext)
        if os.path.isfile(candidate):
            return candidate
    try:
        names = os.listdir(folder)
    except OSError:
        return ""
    for name in names:
        low = name.lower()
        if low.startswith(stem.lower()) and (low.endswith(".psk") or low.endswith(".pskx")):
            return os.path.join(folder, name)
    return ""


def _spawn_mesh_prop(notify: dict, armature, coll, context) -> list:
    from . import operators

    stem = notify.get("mesh") or notify.get("skeletal_mesh") or notify.get("static_mesh") or ""
    psk = _resolve_mesh_psk(stem)
    if not psk:
        empty = bpy.data.objects.new(f"MissingProp_{stem or notify.get('name') or 'prop'}", None)
        empty.empty_display_type = "CUBE"
        empty.empty_display_size = 0.08
        empty["arc_anim_notify_kind"] = "prop_missing"
        empty["arc_anim_notify_mesh"] = stem
        _link_only(empty, coll)
        bone = _find_bone(armature, notify.get("socket") or "")
        if bone is not None:
            _parent_to_bone(empty, armature, bone.name, notify.get("location") or (0, 0, 0), notify.get("rotation") or (0, 0, 0), notify.get("scale") or (1, 1, 1))
        return [empty]

    ok, msg, new_objects = operators.import_psk_with_materials(psk)
    if not ok:
        log.warning("notify prop import failed (%s): %s", stem, msg)
        return []
    bone = _find_bone(armature, notify.get("socket") or "")
    spawned = []
    for obj in new_objects:
        _link_only(obj, coll)
        obj["arc_anim_notify_kind"] = "prop"
        obj["arc_anim_notify_mesh"] = stem
        if bone is not None and obj.type in {"MESH", "ARMATURE", "EMPTY"}:
            if obj.parent is None or obj.type == "ARMATURE":
                _parent_to_bone(
                    obj,
                    armature,
                    bone.name,
                    notify.get("location") or (0, 0, 0),
                    notify.get("rotation") or (0, 0, 0),
                    notify.get("scale") or (1, 1, 1),
                )
        spawned.append(obj)
    anim_stem = notify.get("animation") or ""
    if anim_stem:
        for obj in spawned:
            if obj.type == "ARMATURE":
                entry = {"stem": anim_stem, "key": anim_stem}
                psa = acat.resolve_psa_path(entry)
                if psa:
                    import_psa_onto_armature(psa, obj, context, replace_previous=True)
    return spawned


def _spawn_fx_empty(notify: dict, armature, coll) -> Any:
    name = notify.get("niagara") or notify.get("name") or "FX"
    empty = bpy.data.objects.new(f"FX_{name}", None)
    empty.empty_display_type = "SPHERE"
    empty.empty_display_size = 0.06
    empty.show_name = True
    empty["arc_anim_notify_kind"] = "fx"
    empty["arc_anim_notify_niagara"] = notify.get("niagara") or ""
    empty["arc_anim_notify_class"] = notify.get("class") or ""
    empty["arc_anim_notify_time"] = float(notify.get("time") or 0)
    empty["arc_anim_notify_duration"] = float(notify.get("duration") or 0)
    _link_only(empty, coll)
    bone = _find_bone(armature, notify.get("socket") or "")
    if bone is not None:
        _parent_to_bone(
            empty,
            armature,
            bone.name,
            notify.get("location") or (0, 0, 0),
            notify.get("rotation") or (0, 0, 0),
            notify.get("scale") or (1, 1, 1),
        )
    return empty


def spawn_notifies(
    armature,
    notifies: list,
    *,
    anim_stem: str,
    context=None,
    include_other: bool = False,
) -> tuple:
    """Spawn mesh props and FX placeholders for one applied animation.

    Returns ``(prop_count, fx_count, missing)``.
    """
    context = context or bpy.context
    if armature is None:
        return 0, 0, []
    _clear_spawned(armature)
    events = [n for n in (notifies or []) if isinstance(n, dict)]
    if not events:
        return 0, 0, []
    coll = _ensure_collection(f"{_PROP_COLLECTION_PREFIX}{anim_stem}")
    armature["arc_anim_prop_collection"] = coll.name
    props = 0
    fx = 0
    missing = []
    for notify in events:
        kind = (notify.get("kind") or "other").lower()
        if kind == "prop":
            spawned = _spawn_mesh_prop(notify, armature, coll, context)
            if not spawned:
                missing.append(notify.get("mesh") or notify.get("name") or "prop")
            elif spawned[0].get("arc_anim_notify_kind") == "prop_missing":
                missing.append(notify.get("mesh") or notify.get("name") or "prop")
            else:
                props += 1
        elif kind == "fx":
            _spawn_fx_empty(notify, armature, coll)
            fx += 1
        elif include_other:
            _spawn_fx_empty(notify, armature, coll)
            fx += 1
    return props, fx, missing


def apply_animation(
    context,
    *,
    entry: dict | None = None,
    psa_path: str = "",
    notify_path: str = "",
    notifies: list | None = None,
    spawn_notifies_flag: bool = True,
    replace_action: bool = True,
) -> tuple:
    """Apply one animation to the target armature. Returns (ok, message)."""
    armature = resolve_target_armature(context)
    if armature is None:
        return False, "Select a character armature first"
    cache_dir = getattr(context.scene, "arc_animation_cache", "") or ""
    if entry is not None:
        acat.enrich_entry(entry, cache_dir)
        psa_path = psa_path or acat.resolve_psa_path(entry, cache_dir)
        notify_path = notify_path or acat.resolve_notify_path(entry, cache_dir)
        if notifies is None:
            notifies = entry.get("notifies") or []
        stem = entry.get("stem") or os.path.splitext(os.path.basename(psa_path or ""))[0]
    else:
        stem = os.path.splitext(os.path.basename(psa_path or notify_path or "anim"))[0]
        if notifies is None and notify_path:
            notifies = acat.load_notify_file(notify_path).get("notifies") or []

    if not psa_path:
        hint = stem or (entry.get("key") if entry else "this animation")
        return False, (
            f"No PSA on disk for {hint}. In FModel: load it in Snooper and Send to Blender, "
            f"or Save Animations into the Animation Cache folder."
        )

    ok, msg, _action = import_psa_onto_armature(
        psa_path, armature, context, replace_previous=replace_action
    )
    if not ok:
        return False, msg

    context.scene.arc_last_applied_anim = stem
    context.scene.arc_last_applied_anim_armature = armature.name
    extra = ""
    if spawn_notifies_flag:
        if not notifies and notify_path:
            notifies = acat.load_notify_file(notify_path).get("notifies") or []
        props, fx, missing = spawn_notifies(
            armature,
            notifies or [],
            anim_stem=stem,
            context=context,
            include_other=bool(getattr(context.scene, "arc_anim_spawn_other_notifies", False)),
        )
        bits = []
        if props:
            bits.append(f"{props} prop(s)")
        if fx:
            bits.append(f"{fx} FX marker(s)")
        if missing:
            bits.append(f"{len(missing)} missing mesh(es)")
        if bits:
            extra = " · " + ", ".join(bits)
    return True, msg + extra
