"""Persistent cache for the shared backpack strap and frame.

Every outfit with a backpack needs the same strap mesh, frame mesh, strap
skeleton, and their materials. Before this cache, each import extracted those
assets again and rebuilt the meshes and the material graphs. Now the first
import keeps pristine template copies, and later imports copy from them.

There are two cache levels:

- In-file: hidden template objects (fake user, in no collection), stamped
  with ``arc_gear_template_role`` and ``arc_gear_cache_sig``.
- Per-user library: ``<role>.blend`` with a ``<role>.json`` sidecar. The
  sidecar holds the source signature, so a miss costs one ``os.stat`` and
  one small JSON read, and the blend is never opened.

Invalidation compares one cheap source signature. It covers the cache schema,
the addon version, the extracted mesh file's size and mtime, and the
extraction revision. The extraction revision already hashes the game
containers' stats plus build, depot and usmap, so a pak update changes it.

Entry points:
- ``import_cached(role, path, importer, import_slot=None)``: use it in place of
  ``import_appearance_mesh`` for the BackpackStrap/BackpackFrame jobs.
- ``capture_pending(objects)``: call it after deferred materials finish. It
  writes the library for a miss whose materials were still pending.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

import bpy

SCHEMA = 1
ROLES = frozenset({"BackpackStrap", "BackpackFrame"})
TEMPLATE_ROLE = "arc_gear_template_role"
SIG = "arc_gear_cache_sig"
PENDING_WRITE = "arc_gear_cache_pending"
LAST_EVENT: dict = {}


# Signature ---------------------------------------------------------------


def _addon_version() -> str:
    try:
        from .. import bl_info

        return ".".join(str(part) for part in bl_info.get("version", ()))
    except Exception:
        return ""


def _extract_revision(path: str) -> str:
    for sidecar in (path + ".arc-extract.json", os.path.splitext(path)[0] + ".arc-extract.json"):
        try:
            with open(sidecar, encoding="utf-8") as handle:
                return str(json.load(handle).get("revision") or "")
        except Exception:
            continue
    return ""


def source_signature(path: str, extra: str = "") -> str:
    """Return the cheap identity of one extracted gear mesh. Raises OSError if missing."""
    st = os.stat(path)
    payload = {
        "schema": SCHEMA,
        "addon": _addon_version(),
        "name": os.path.basename(path).casefold(),
        "size": st.st_size,
        "mtime": st.st_mtime_ns,
        "revision": _extract_revision(path),
        "extra": str(extra or ""),
    }
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# Library location -------------------------------------------------------


def cache_dir() -> str:
    override = os.environ.get("ARC_GEAR_CACHE_DIR")
    if override:
        root = override
    else:
        root = bpy.utils.user_resource("CONFIG", path="dataraiders_gear_cache")
    os.makedirs(root, exist_ok=True)
    return root


def _library_paths(role: str):
    root = cache_dir()
    return os.path.join(root, f"{role}.blend"), os.path.join(root, f"{role}.json")


def read_stamp(role: str) -> dict:
    _blend, meta = _library_paths(role)
    try:
        with open(meta, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


# Template helpers --------------------------------------------------------


def _alive(obj) -> bool:
    try:
        return obj is not None and obj.name in bpy.data.objects
    except ReferenceError:
        return False


def _templates(role: str, sig: str) -> list:
    return [
        obj for obj in bpy.data.objects
        if obj.get(TEMPLATE_ROLE) == role and obj.get(SIG) == sig
    ]


def _drop_stale_templates(role: str, sig: str) -> None:
    for obj in list(bpy.data.objects):
        if obj.get(TEMPLATE_ROLE) == role and obj.get(SIG) != sig:
            bpy.data.objects.remove(obj, do_unlink=True)


def _remap_refs(copies: dict) -> None:
    """Point parent and modifier targets at the copies (old object -> new object)."""
    for src, dst in copies.items():
        parent = src.parent
        if parent in copies:
            world = src.matrix_world.copy()
            dst.parent = copies[parent]
            dst.matrix_parent_inverse = src.matrix_parent_inverse.copy()
            dst.matrix_world = world
        for mod in dst.modifiers:
            target = getattr(mod, "object", None)
            if target in copies:
                mod.object = copies[target]


def _copy_set(objects: list) -> dict:
    """Copy objects and their data. Materials stay shared."""
    copies = {}
    for src in objects:
        dst = src.copy()
        if src.data is not None:
            dst.data = src.data.copy()
        copies[src] = dst
    _remap_refs(copies)
    return copies


def _mark_template(obj, role: str, sig: str) -> None:
    for coll in list(obj.users_collection):
        coll.objects.unlink(obj)
    obj[TEMPLATE_ROLE] = role
    obj[SIG] = sig
    obj.use_fake_user = True
    if obj.data is not None:
        obj.data.use_fake_user = True


def _has_pending(objects) -> bool:
    return any(
        obj.type == "MESH" and int(obj.get("arc_materials_pending") or 0)
        for obj in objects
    )


def _instances(templates: list, role: str, sig: str, import_slot) -> list:
    copies = _copy_set(templates)
    collection = bpy.context.collection or bpy.context.scene.collection
    out = []
    for template, obj in copies.items():
        for key in (TEMPLATE_ROLE, PENDING_WRITE):
            if key in obj:
                del obj[key]
        obj.use_fake_user = False
        if obj.data is not None:
            obj.data.use_fake_user = False
        obj[SIG] = sig
        obj["arc_gear_cache_role"] = role
        if obj.type == "MESH":
            obj["arc_materials_pending"] = 0
        collection.objects.link(obj)
        out.append(obj)
    if import_slot is not None:
        from . import import_layout

        import_layout.stamp_objects(out, slot=import_slot)
    return out


# Library read/write ------------------------------------------------------


def _image_key(image):
    path = image.filepath_raw or image.filepath or ""
    try:
        path = os.path.normcase(os.path.normpath(bpy.path.abspath(path)))
    except Exception:
        pass
    return (path, image.colorspace_settings.name, image.alpha_mode)


def _dedupe_images(new_images) -> None:
    """Remap appended images onto local images with the same file and colour settings."""
    local = {}
    fresh = set(new_images)
    for image in bpy.data.images:
        if image in fresh or image.library is not None or not image.filepath:
            continue
        local.setdefault(_image_key(image), image)
    for image in fresh:
        keep = local.get(_image_key(image)) if image.filepath else None
        if keep is not None and keep is not image:
            image.user_remap(keep)
            bpy.data.images.remove(image)


def write_library(role: str, sig: str, templates: list) -> bool:
    blend, meta = _library_paths(role)
    tmp = blend + ".tmp.blend"
    try:
        bpy.data.libraries.write(
            tmp, set(templates), path_remap="ABSOLUTE", fake_user=True,
        )
        os.replace(tmp, blend)
    except Exception as exc:
        print(f"Arc Raiders: gear cache write failed ({role}): {exc}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    stamp = {
        "schema": SCHEMA,
        "role": role,
        "sig": sig,
        "objects": [obj.name for obj in templates],
        "written": time.time(),
    }
    with open(meta, "w", encoding="utf-8") as handle:
        json.dump(stamp, handle, indent=1)
    for obj in templates:
        if PENDING_WRITE in obj:
            del obj[PENDING_WRITE]
    return True


def _load_library(role: str, sig: str) -> list:
    stamp = read_stamp(role)
    blend, _meta = _library_paths(role)
    if stamp.get("sig") != sig or stamp.get("schema") != SCHEMA or not os.path.isfile(blend):
        return []
    names = list(stamp.get("objects") or [])
    before_images = set(bpy.data.images)
    try:
        with bpy.data.libraries.load(blend, link=False) as (data_from, data_to):
            data_to.objects = [name for name in names if name in data_from.objects]
    except Exception as exc:
        print(f"Arc Raiders: gear cache read failed ({role}): {exc}")
        return []
    loaded = [obj for obj in data_to.objects if obj is not None]
    if len(loaded) != len(names):
        for obj in loaded:
            bpy.data.objects.remove(obj, do_unlink=True)
        return []
    _dedupe_images([image for image in bpy.data.images if image not in before_images])
    for obj in loaded:
        _mark_template(obj, role, sig)
    return loaded


# Entry points ------------------------------------------------------------


def import_cached(role: str, path: str, importer, *, import_slot=None, extra: str = ""):
    """Import one gear part through the cache. Returns ``importer()``'s ``(ok, msg, objs)``.

    ``importer`` is a zero-argument callable that performs the full import (the
    existing ``import_appearance_mesh`` call). It runs only on a cache miss.
    """
    LAST_EVENT.clear()
    LAST_EVENT.update(role=role, path=path)
    if role not in ROLES:
        LAST_EVENT["result"] = "bypass"
        return importer()
    try:
        sig = source_signature(path, extra)
    except OSError:
        LAST_EVENT["result"] = "bypass"
        return importer()
    LAST_EVENT["sig"] = sig
    templates = _templates(role, sig)
    if templates and not any(obj.get(PENDING_WRITE) for obj in templates):
        LAST_EVENT["result"] = "hit-file"
        objs = _instances(templates, role, sig, import_slot)
        return True, f"Reused cached {role}: {os.path.basename(path)}", objs
    if not templates:
        _drop_stale_templates(role, sig)
        templates = _load_library(role, sig)
        if templates:
            LAST_EVENT["result"] = "hit-library"
            objs = _instances(templates, role, sig, import_slot)
            return True, f"Loaded cached {role}: {os.path.basename(path)}", objs
    LAST_EVENT["result"] = "miss"
    ok, msg, objs = importer()
    alive = [obj for obj in (objs or []) if _alive(obj)]
    if not ok or not alive:
        return ok, msg, objs
    for obj in templates:
        if _alive(obj):
            bpy.data.objects.remove(obj, do_unlink=True)
    copies = _copy_set(alive)
    for src, template in copies.items():
        _mark_template(template, role, sig)
        src[SIG] = sig
        src["arc_gear_cache_role"] = role
    templates = list(copies.values())
    if _has_pending(alive):
        for template in templates:
            template[PENDING_WRITE] = 1
    else:
        LAST_EVENT["written"] = write_library(role, sig, templates)
    return ok, msg, objs


def capture_pending(objects=None) -> int:
    """Copy finished materials onto pending templates and write their libraries."""
    pool = objects if objects is not None else bpy.data.objects
    done = 0
    by_role = {}
    for obj in pool:
        if not _alive(obj) or obj.get(TEMPLATE_ROLE):
            continue
        role = obj.get("arc_gear_cache_role")
        sig = obj.get(SIG)
        if role in ROLES and sig and not _has_pending([obj]):
            by_role.setdefault((role, sig), []).append(obj)
    for (role, sig), sources in by_role.items():
        templates = [t for t in _templates(role, sig) if t.get(PENDING_WRITE)]
        if not templates:
            continue
        for template in templates:
            if template.type != "MESH":
                continue
            src = next(
                (s for s in sources if s.type == "MESH" and s.name.split(".")[0] == template.name.split(".")[0]),
                None,
            )
            if src is None:
                continue
            # The material step can change mesh data too, so take a fresh copy.
            mats = [slot.material for slot in src.material_slots]
            old_mesh = template.data
            template.data = src.data.copy()
            template.data.use_fake_user = True
            for index, mat in enumerate(mats):
                if index < len(template.data.materials):
                    template.data.materials[index] = mat
            for index, slot in enumerate(template.material_slots):
                slot.link = "DATA"
            if old_mesh.users == 1 and old_mesh.use_fake_user:
                bpy.data.meshes.remove(old_mesh)
        if write_library(role, sig, templates):
            done += 1
    return done
