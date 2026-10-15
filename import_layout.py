"""
Character import slots — park each body/outfit pad on a known X spacing.

Default: 2 m between characters. Toggle off to keep everything at world origin.
Each slot allows at most one Body, Head, Hair, FacialHair, and Outfit.
"""

from __future__ import annotations

import re

import bpy
from mathutils import Vector

# Custom props
PROP_SLOT = "arc_import_slot"
PROP_ROLE = "arc_assembly_role"

ROLES = (
    "Body",
    "Head",
    "Hair",
    "FacialHair",
    "Outfit",
    "Backpack",
    "BackpackFrame",
    "BackpackStrap",
    "Attachment",
    "Charm",
    "RaiderTool",
)

_DEFAULT_SPACING_M = 2.0


def offset_enabled(scene=None) -> bool:
    sc = scene or getattr(bpy.context, "scene", None)
    if sc is None:
        return True
    return bool(getattr(sc, "arc_import_slot_offset", True))


def slot_spacing_m(scene=None) -> float:
    sc = scene or getattr(bpy.context, "scene", None)
    if sc is None:
        return _DEFAULT_SPACING_M
    try:
        v = float(getattr(sc, "arc_import_slot_spacing", _DEFAULT_SPACING_M))
    except (TypeError, ValueError):
        v = _DEFAULT_SPACING_M
    return max(v, 0.01)


def slot_world_x(slot: int, scene=None) -> float:
    if not offset_enabled(scene):
        return 0.0
    return float(int(slot)) * slot_spacing_m(scene)


def _has_strap_mesh(arm) -> bool:
    for child in getattr(arm, "children", ()):
        if not _alive(child) or getattr(child, "type", "") != "MESH":
            continue
        if infer_role(child) == "BackpackStrap":
            return True
    return False


def _alive(obj) -> bool:
    try:
        obj.name
        return True
    except ReferenceError:
        return False


def infer_role(obj) -> str:
    """Best-effort role for slot occupancy."""
    if not _alive(obj):
        return ""
    stamped = str(obj.get(PROP_ROLE) or "").strip()
    if stamped in ROLES:
        return stamped
    if obj.get("arc_outfit") or obj.get("arc_colorway") or obj.get("arc_bridge_source") == "fmodel_outfit":
        return "Outfit"
    path = str(obj.get("arc_psk_path") or obj.get("arc_source_mesh") or "").replace("\\", "/").casefold()
    name = (obj.name or "").casefold()
    if "/facialhairs/" in path or "facialhair" in name or "beard" in name or "sideburn" in name or "mutton" in name:
        return "FacialHair"
    if "/hairs/" in path or name.startswith("sk_") and "hair" in name:
        return "Hair"
    if "/heads/" in path or "head_" in name or name.startswith("head"):
        return "Head"
    if "/bodies/" in path or name in ("sk_body", "sk_body.001") or name.startswith("sk_body"):
        return "Body"
    if "/backpacks/straps/" in path or "default_strap" in name or name.startswith("sk_default_strap"):
        return "BackpackStrap"
    if "/backpacks/frames/" in path or "backpack_frame" in name or "backpackframe" in name:
        return "BackpackFrame"
    if "/backpacks/containers/" in path or "/backpackcontainer/" in path:
        return "Backpack"
    if "/backpacks/attachments/" in path or "/backpackattachment/" in path:
        return "Attachment"
    if "/backpacks/charms/" in path or "/backpackcharm/" in path:
        return "Charm"
    if "/raidertool/" in path or "/raider_tool/" in path:
        return "RaiderTool"
    model = str(obj.get("arc_model_type") or "").casefold()
    if model == "clothing":
        return "Outfit"
    return ""


def discover_slots(scene=None, *, exclude=None) -> dict[int, set[str]]:
    """slot index → set of roles already present.

    *exclude* — objects currently being placed (must not count as occupying a
    slot yet, or the first import wrongly lands at spacing instead of origin).
    """
    skip = set()
    if exclude:
        for obj in exclude:
            if obj is not None:
                skip.add(obj)
    occupied: dict[int, set[str]] = {}
    for obj in bpy.data.objects:
        if obj in skip or not _alive(obj) or obj.type not in {"MESH", "ARMATURE"}:
            continue
        role = infer_role(obj)
        if not role:
            continue
        # A strap armature with no strap mesh is only there so tools and charms
        # have slot bones. It must not push the first real backpack to slot 1.
        if role == "BackpackStrap" and obj.type == "ARMATURE" and not _has_strap_mesh(obj):
            continue
        if PROP_SLOT in obj:
            try:
                slot = int(obj.get(PROP_SLOT))
            except (TypeError, ValueError):
                slot = None
        else:
            slot = None
        if slot is None:
            # Infer from world X when offset layout was used previously without stamps.
            if offset_enabled(scene):
                spacing = slot_spacing_m(scene)
                wx = float(obj.matrix_world.translation.x)
                if spacing > 1e-6 and abs(wx) > 0.25:
                    slot = int(round(wx / spacing))
                    if slot < 0:
                        continue
                else:
                    slot = 0
            else:
                slot = 0
        occupied.setdefault(slot, set()).add(role)
    return occupied


def slot_of_object(obj) -> int | None:
    if not _alive(obj):
        return None
    if PROP_SLOT in obj:
        try:
            return int(obj.get(PROP_SLOT))
        except (TypeError, ValueError):
            return None
    return None


def allocate_slot(
    role: str,
    *,
    scene=None,
    prefer_slot: int | None = None,
    exclude=None,
) -> int:
    """Pick a slot for *role*.

    1. Lowest existing slot that does not already have *role* (fill slot 0 before slot 1).
    2. Otherwise reuse the lowest empty pad index (gaps from deleted characters).
    3. Else append after the highest occupied slot.

    Pass *exclude* as the objects being placed so they are not mistaken for an
    already-occupied pad (empty scene → slot 0 at origin).
    """
    role = (role or "").strip()
    if role not in ROLES:
        role = "Outfit"
    if prefer_slot is not None:
        return max(0, int(prefer_slot))
    if not offset_enabled(scene):
        return 0

    occupied = discover_slots(scene, exclude=exclude)
    if not occupied:
        return 0

    # Lowest slot that is still missing this piece (slot 0, then 1, ...).
    for slot in sorted(occupied.keys()):
        if role not in occupied[slot]:
            return slot

    # Fill gaps: if pad 2 was deleted while 0/1/3 remain, place at 2.
    max_slot = max(occupied.keys())
    for slot in range(0, max_slot + 1):
        if slot not in occupied:
            return slot

    return max_slot + 1


def stamp_objects(objects, *, slot: int, role: str = "") -> None:
    for obj in objects:
        if not _alive(obj):
            continue
        try:
            obj[PROP_SLOT] = int(slot)
            if role and not obj.get(PROP_ROLE):
                obj[PROP_ROLE] = role
        except Exception:
            pass


def _iter_roots(objects: list) -> list:
    """Objects to translate: armatures first, else unparented meshes."""
    alive = [o for o in objects if _alive(o)]
    arms = [o for o in alive if o.type == "ARMATURE"]
    if arms:
        return arms
    obj_set = set(alive)
    return [o for o in alive if o.type == "MESH" and (o.parent is None or o.parent not in obj_set)]


def place_objects_at_slot(objects: list, slot: int, *, scene=None, role: str = "") -> float:
    """Stamp slot/role and move roots so the group sits at slot * spacing on X.

    Returns the world X used (0 when offset is disabled).
    """
    target_x = slot_world_x(slot, scene)
    stamp_objects(objects, slot=slot, role=role)
    if not objects:
        return target_x

    roots = _iter_roots(objects)
    if not roots:
        return target_x

    # Shift by delta from current mean root X → target (keeps relative layout).
    xs = [float(o.matrix_world.translation.x) for o in roots]
    cur = sum(xs) / len(xs)
    delta = target_x - cur
    if abs(delta) < 1e-6:
        return target_x

    for obj in roots:
        try:
            mw = obj.matrix_world.copy()
            mw.translation.x = float(mw.translation.x) + delta
            obj.matrix_world = mw
        except Exception:
            try:
                obj.location.x += delta
            except Exception:
                pass
    return target_x


def place_imported(
    objects: list,
    role: str,
    *,
    scene=None,
    prefer_slot: int | None = None,
    attach_to=None,
) -> int:
    """Allocate (or reuse attach target's) slot and place *objects*. Returns slot index."""
    prefer = prefer_slot
    if prefer is None and attach_to is not None:
        prefer = slot_of_object(attach_to)
        if prefer is None and _alive(attach_to):
            # Infer from armature world X
            if offset_enabled(scene):
                spacing = slot_spacing_m(scene)
                wx = float(attach_to.matrix_world.translation.x)
                if spacing > 1e-6:
                    prefer = max(0, int(round(wx / spacing)))
    slot = allocate_slot(role, scene=scene, prefer_slot=prefer, exclude=objects)
    # When attaching, keep world poses (parent inverse); only stamp — do not
    # shove the whole outfit. New meshes parented in merge already match.
    if attach_to is not None:
        stamp_objects(objects, slot=slot, role=role)
        stamp_objects([attach_to], slot=slot, role="")
        return slot
    place_objects_at_slot(objects, slot, scene=scene, role=role)
    return slot


def normalize_role_name(role: str, *, hair_segment: str = "") -> str:
    r = (role or "").strip()
    if r in ROLES:
        return r
    seg = (hair_segment or "").casefold()
    rl = r.casefold().replace(" ", "").replace("_", "")
    if seg == "facialhairs" or rl in ("facial", "facialhair"):
        return "FacialHair"
    if rl == "hair":
        return "Hair"
    if rl == "head":
        return "Head"
    if rl == "body":
        return "Body"
    if rl == "outfit":
        return "Outfit"
    if rl == "backpack":
        return "Backpack"
    if rl in ("backpackframe", "frame"):
        return "BackpackFrame"
    if rl in ("backpackstrap", "strap"):
        return "BackpackStrap"
    if rl == "attachment":
        return "Attachment"
    if rl == "charm":
        return "Charm"
    if rl in ("raidertool", "raidertools"):
        return "RaiderTool"
    return r if r else "Outfit"
