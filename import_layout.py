"""
Character import slots — one of each asset type on a ten-column grid.

Default: 2 m between columns, 8 m between rows along positive Y.
Toggle off to keep everything at world origin.
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
_DEFAULT_ROW_SPACING_M = 8.0
SLOTS_PER_ROW = 10


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
    return float(max(0, int(slot)) % SLOTS_PER_ROW) * slot_spacing_m(scene)


def slot_row_spacing_m(scene=None) -> float:
    sc = scene or getattr(bpy.context, "scene", None)
    try:
        value = float(getattr(sc, "arc_import_slot_row_spacing", _DEFAULT_ROW_SPACING_M))
    except (TypeError, ValueError):
        value = _DEFAULT_ROW_SPACING_M
    return max(value, 0.01)


def slot_world_y(slot: int, scene=None) -> float:
    if not offset_enabled(scene):
        return 0.0
    return float(max(0, int(slot)) // SLOTS_PER_ROW) * slot_row_spacing_m(scene)


def slot_world_xy(slot: int, scene=None) -> tuple[float, float]:
    return slot_world_x(slot, scene), slot_world_y(slot, scene)


def slot_from_world(position, scene=None) -> int:
    """Recover an unstamped pad from its grid position."""
    if not offset_enabled(scene):
        return 0
    column = max(0, int(round(float(position.x) / slot_spacing_m(scene))))
    row = max(0, int(round(float(position.y) / slot_row_spacing_m(scene))))
    # Coordinates past the first row may be from pre-grid imports.
    return row * SLOTS_PER_ROW + column


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
    if obj.get("arc_reference_plane"):
        return ""
    if obj.type == "MESH" and str(obj.get("arc_model_type") or "").casefold() == "body":
        return "Body"
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
    sc = scene or getattr(bpy.context, "scene", None)
    for obj in sc.objects if sc is not None else bpy.data.objects:
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
                wx = float(obj.matrix_world.translation.x)
                if wx < -0.25:
                    continue
                slot = slot_from_world(obj.matrix_world.translation, scene)
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

    Use the lowest nonnegative index that does not already have *role*.
    Other types can share that pad. Deleted gaps are filled before higher pads.

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

    # Deleted pads are as eligible as existing pads. One type can fill slot 0
    # alongside other types, independently of their import order.
    for slot in range(0, max(occupied) + 2):
        if role not in occupied.get(slot, set()):
            return slot
    return 0


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
    """Translate each assembly root once, including static-mesh EMPTY pivots."""
    alive = [o for o in objects if _alive(o)]
    obj_set = set(alive)
    return [o for o in alive if o.type in {"MESH", "ARMATURE", "EMPTY"}
            and (o.parent is None or o.parent not in obj_set)]


def place_objects_at_slot(objects: list, slot: int, *, scene=None, role: str = "") -> float:
    """Stamp slot/role and place roots on the ten-column X/Y grid.

    Returns the world X used (0 when offset is disabled).
    """
    target_x = slot_world_x(slot, scene)
    target_y = slot_world_y(slot, scene)
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
    ys = [float(o.matrix_world.translation.y) for o in roots]
    delta_y = target_y - sum(ys) / len(ys)
    if abs(delta) < 1e-6 and abs(delta_y) < 1e-6:
        return target_x

    for obj in roots:
        try:
            mw = obj.matrix_world.copy()
            mw.translation.x = float(mw.translation.x) + delta
            mw.translation.y = float(mw.translation.y) + delta_y
            obj.matrix_world = mw
        except Exception:
            try:
                obj.location.x += delta
                obj.location.y += delta_y
            except Exception:
                pass
    return target_x


def _sync_character_fit(scene=None):
    from . import character_fit
    try:
        character_fit.sync_scene(scene)
    except Exception as exc:
        from . import utils
        utils.get_logger().warning("Character fit update failed: %s", exc)


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
                prefer = slot_from_world(attach_to.matrix_world.translation, scene)
    slot = allocate_slot(role, scene=scene, prefer_slot=prefer, exclude=objects)
    # When attaching, keep world poses (parent inverse); only stamp — do not
    # shove the whole outfit. New meshes parented in merge already match.
    if attach_to is not None:
        stamp_objects(objects, slot=slot, role=role)
        stamp_objects([attach_to], slot=slot, role="")
        _sync_character_fit(scene)
        return slot
    place_objects_at_slot(objects, slot, scene=scene, role=role)
    _sync_character_fit(scene)
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


def reflow_slots(scene=None, *, legacy_linear: bool = False) -> list[dict]:
    """Compact existing assemblies by type without changing their authored pose.

    This explicit repair helper is not run during registration. For scenes from
    before the grid, pass legacy_linear=True so their old slot X is interpreted
    correctly. Parented reference cards follow their assembly; loose cards are
    matched by their recorded anchor. Vertex data, Z and socket transforms stay
    untouched.
    """
    sc = scene or bpy.context.scene
    from . import reference_planes
    reference_planes.bind_legacy_reference_planes(sc, legacy_linear=legacy_linear)
    scene_objects = set(sc.objects)
    groups = {}
    for obj in sc.objects:
        if not infer_role(obj):
            continue
        root = obj
        while root.parent is not None and root.parent in scene_objects:
            root = root.parent
        groups.setdefault(root, set()).add(obj)
    for root, objects in groups.items():
        objects.add(root)
        objects.update(o for o in root.children_recursive if o in scene_objects)
        names = {o.name for o in objects}
        objects.update(o for o in sc.objects if o.get("arc_reference_plane")
                       and str(o.get("arc_reference_anchor") or "") in names)
    support_roles = {"BackpackFrame", "BackpackStrap"}
    records = []
    for root, objects in groups.items():
        slots = [slot_of_object(o) for o in objects if infer_role(o)]
        old_slot = min((s for s in slots if s is not None), default=slot_from_world(root.matrix_world.translation, sc))
        roles = {infer_role(o) for o in objects} - {""}
        primary = roles - support_roles or roles
        records.append((old_slot, root.name, root, objects, primary))
    occupied = {}
    moved = []
    for old_slot, _name, root, objects, roles in sorted(records, key=lambda r: (r[0], r[1])):
        slot = 0
        while roles & occupied.get(slot, set()):
            slot += 1
        occupied.setdefault(slot, set()).update(roles)
        old_x, old_y = ((old_slot * slot_spacing_m(sc), 0.0) if legacy_linear
                        else slot_world_xy(old_slot, sc))
        new_x, new_y = slot_world_xy(slot, sc)
        delta = Vector((new_x - old_x, new_y - old_y, 0.0))
        # Move only top-level group members: references under the root inherit.
        for obj in objects:
            if obj.parent not in objects:
                matrix = obj.matrix_world.copy()
                matrix.translation += delta
                obj.matrix_world = matrix
        stamp_objects(objects, slot=slot)
        moved.append({"root": root.name, "old_slot": old_slot, "slot": slot,
                      "roles": sorted(roles), "delta": list(delta)})
    bpy.context.view_layer.update()
    reference_planes.categorize_reference_planes(sc)
    _sync_character_fit(sc)
    return moved
