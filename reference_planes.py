"""UI reference image planes sized to match imported outfit / head / hair meshes."""

from __future__ import annotations

import os
from typing import Iterable

import bpy
from mathutils import Matrix, Vector

# Outfit UI art is composed head-to-just-below-knee (not full body).
# Gap / fraction matched to the authored REF_Outfit in the working blend
# (plane left of outfit AABB, top-aligned, parented to the outfit armature).
_OUTFIT_ICON_HEIGHT_FRACTION = 0.75
_OUTFIT_REF_LEFT_GAP_M = 0.38

_REF_COLLECTION = "Arc Reference Planes"
_REF_TYPE_COLLECTIONS = {
    "Body": "Bodies", "Head": "Heads", "Hair": "Hair", "FacialHair": "Facial Hair",
    "Outfit": "Outfits", "Backpack": "Backpacks", "Attachment": "Attachments",
    "Charm": "Charms", "RaiderTool": "Raider Tools",
}
# Object Index used by Lighting Look compositor to skip Kodak LUT on UI planes.
REF_PASS_INDEX = 64
_ICON_PACKAGE_LOOKUPS = {}


def choice_icon_package(choice: dict, scene=None) -> str:
    """Find an exact authored UI package in the existing index; never export."""
    from . import appearance, pak_extract, pak_catalog
    if not choice:
        return ""
    segment, prefix = appearance._gear_icon_spec(choice)
    if not segment:
        return ""
    index = pak_extract.load_pak_index(scene or bpy.context.scene, allow_reindex=False)
    if not index:
        return ""
    stamp = index.get("stamp") or (index.get("path"), index.get("build"))
    lookup = _ICON_PACKAGE_LOOKUPS.get(stamp)
    if lookup is None:
        lookup = {pak_catalog.canonical_path(path).casefold(): pak_catalog.canonical_path(path)
                  for path in index["paths"] if "/t_ui_icon_" in path.casefold()}
        _ICON_PACKAGE_LOOKUPS.clear()
        _ICON_PACKAGE_LOOKUPS[stamp] = lookup
    stems = []
    for value in (choice.get("icon_stem"), choice.get("folder")):
        stem = str(value or "").strip()
        if not stem or any(token in stem for token in ("/", "\\", "..")):
            continue
        if stem not in stems:
            stems.append(stem)
        if not stem.upper().endswith("_A") and stem + "_A" not in stems:
            stems.append(stem + "_A")
    # Some authored packages use a base folder with an _A image suffix
    # (TechBag/T_UI_Icon_BackpackContainer_TechBag_A). Both components remain
    # exact catalog stems; do not borrow a similar name from another asset.
    for folder in stems:
        for stem in stems:
            package = f"PioneerGame/Content/Pioneer/Items/Characters/Skins/{segment}/{folder}/T_UI_Icon_{prefix}_{stem}"
            found = lookup.get(package.casefold())
            if found:
                return found
    return ""


def ensure_choice_icon(choice: dict, scene=None) -> str:
    """Resolve an icon during import, exporting only its indexed package if needed.

    Sidebar previews continue to use the read-only appearance resolver. This
    helper belongs to completed import work and must not be called while drawing.
    """
    from . import appearance, pak_extract
    path = appearance._icon_file_for_choice(choice) if choice else ""
    if path:
        return path
    sc = scene or bpy.context.scene
    game = pak_extract.configured_dir(sc)
    package = choice_icon_package(choice, sc) if game else ""
    mapping = pak_extract.resolve_usmap(sc) if package else ""
    if not package or not mapping:
        return ""
    code, log, _meshes = pak_extract.extract_paths(game, mapping, [package])
    appearance._ICON_SCAN_CACHE.clear()
    path = appearance._icon_file_for_choice(choice)
    if not path:
        print(f"Arc reference: UI icon unavailable after export ({code}): {package}")
    return path


def _ensure_collection(name: str = _REF_COLLECTION):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    try:
        if col.name not in bpy.context.scene.collection.children:
            bpy.context.scene.collection.children.link(col)
    except Exception:
        pass
    return col


def _reference_collection(role: str):
    root = _ensure_collection()
    label = _REF_TYPE_COLLECTIONS.get(role, "Other")
    # A unique datablock name avoids stealing a mesh collection called Hair.
    name = f"Arc References - {label}"
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    if col.name not in root.children:
        root.children.link(col)
    return col


def _reference_role(plane) -> str:
    from . import import_layout
    stamped = str(plane.get("arc_reference_role") or "")
    if stamped in _REF_TYPE_COLLECTIONS:
        return stamped
    image_path = str(plane.get("arc_reference_image") or "").replace("\\", "/").casefold()
    for segment, role in (("facialhair", "FacialHair"), ("hair", "Hair"),
                          ("face", "Head"), ("backpackcontainer", "Backpack"),
                          ("backpackattachment", "Attachment"), ("backpackcharm", "Charm"),
                          ("raidertool", "RaiderTool"), ("outfit", "Outfit")):
        if f"/skins/{segment}/" in image_path:
            return role
    anchor = plane.parent or bpy.data.objects.get(str(plane.get("arc_reference_anchor") or ""))
    if anchor is not None:
        roles = {import_layout.infer_role(o) for o in [anchor, *anchor.children_recursive]}
        # Head/hair names disambiguate cards on mixed outfit armatures.
        if plane.name.startswith("REF_Hair") and "FacialHair" in roles and "Hair" not in roles:
            return "FacialHair"
    for role in _REF_TYPE_COLLECTIONS:
        if plane.name.startswith("REF_" + role):
            return role
    return ""


def bind_legacy_reference_planes(scene=None, *, legacy_linear=False) -> int:
    """Recover old loose cards' ownership from their icon type and exact pad."""
    from . import import_layout
    sc = scene or bpy.context.scene
    meshes = [o for o in sc.objects if o.type == "MESH" and import_layout.infer_role(o)]
    count = 0
    for plane in list(sc.objects):
        if not plane.get("arc_reference_plane") or plane.parent is not None:
            continue
        role = _reference_role(plane)
        if "arc_import_slot" in plane:
            slot = int(plane["arc_import_slot"])
        elif legacy_linear:
            slot = max(0, int(round(plane.matrix_world.translation.x / import_layout.slot_spacing_m(sc))))
        else:
            slot = import_layout.slot_from_world(plane.matrix_world.translation, sc)
        candidates = [o for o in meshes if import_layout.infer_role(o) == role
                      and import_layout.slot_of_object(o) == slot]
        if not candidates:
            continue
        primary = min(candidates, key=lambda o: (o.matrix_world.translation - plane.matrix_world.translation).length_squared)
        _anchor_plane(plane, primary.parent or primary)
        plane["arc_import_slot"] = slot
        plane["arc_reference_role"] = role
        count += 1
    return count


def categorize_reference_planes(scene=None) -> int:
    """Move existing cards into their type collection, preserving their poses."""
    sc = scene or bpy.context.scene
    count = 0
    for plane in list(sc.objects):
        if not plane.get("arc_reference_plane"):
            continue
        role = _reference_role(plane)
        col = _reference_collection(role)
        for old in list(plane.users_collection):
            if old != col:
                old.objects.unlink(plane)
        if plane.name not in col.objects:
            col.objects.link(plane)
        plane["arc_reference_role"] = role
        count += 1
    return count


def _depsgraph_update() -> None:
    """Ensure matrix_world reflects slot layout before measuring AABBs."""
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass


def _world_bbox(objects: Iterable) -> tuple[Vector, Vector] | None:
    _depsgraph_update()
    mins = Vector((1e9, 1e9, 1e9))
    maxs = Vector((-1e9, -1e9, -1e9))
    any_ok = False
    for obj in objects:
        if obj is None or getattr(obj, "type", "") != "MESH":
            continue
        try:
            for corner in obj.bound_box:
                w = obj.matrix_world @ Vector(corner)
                mins.x = min(mins.x, w.x)
                mins.y = min(mins.y, w.y)
                mins.z = min(mins.z, w.z)
                maxs.x = max(maxs.x, w.x)
                maxs.y = max(maxs.y, w.y)
                maxs.z = max(maxs.z, w.z)
            any_ok = True
        except Exception:
            continue
    if not any_ok:
        return None
    return mins, maxs


def _outfit_anchor(objects) -> bpy.types.Object | None:
    """Prefer the armature that parents Outfit meshes (not head/hair armatures)."""
    for obj in _outfit_meshes(objects):
        parent = getattr(obj, "parent", None)
        if parent is not None and parent.type == "ARMATURE":
            return parent
    for obj in objects or []:
        if obj is None or getattr(obj, "type", "") != "MESH":
            continue
        try:
            role = str(obj.get("arc_assembly_role") or "").casefold()
        except Exception:
            role = ""
        if role and role not in ("outfit",):
            continue
        parent = getattr(obj, "parent", None)
        if parent is not None and parent.type == "ARMATURE":
            return parent
    return None


def _anchor_plane(plane, anchor) -> None:
    """Parent *plane* to *anchor* while keeping the current world transform."""
    if plane is None or anchor is None:
        return
    try:
        _depsgraph_update()
        mw = plane.matrix_world.copy()
        plane.parent = anchor
        # Identity parent-inverse + local = parent^-1 * world (handles scale 0.01).
        plane.matrix_parent_inverse = Matrix.Identity(4)
        plane.matrix_local = anchor.matrix_world.inverted() @ mw
        _depsgraph_update()
        plane["arc_reference_anchor"] = anchor.name
        if "arc_import_slot" in anchor:
            plane["arc_import_slot"] = int(anchor["arc_import_slot"])
    except Exception:
        pass


def _load_image(path: str):
    """Load a dedicated reference copy so sidebar previews cannot change filter/CM."""
    if not path or not os.path.isfile(path):
        return None
    abspath = bpy.path.abspath(path)
    stem = os.path.splitext(os.path.basename(abspath))[0]
    ref_name = f"REF_{stem}"[:63]
    existing = bpy.data.images.get(ref_name)
    if existing is not None:
        try:
            if os.path.normcase(bpy.path.abspath(existing.filepath_raw or existing.filepath or "")) == os.path.normcase(abspath):
                _configure_ref_image(existing)
                return existing
        except Exception:
            pass
    try:
        img = bpy.data.images.load(abspath, check_existing=False)
    except Exception:
        return None
    try:
        if img.name != ref_name and ref_name not in bpy.data.images:
            img.name = ref_name
    except Exception:
        pass
    _configure_ref_image(img)
    return img


def _configure_ref_image(img) -> None:
    """UI icons are display-referred; keep sRGB once (view transform encodes once)."""
    if img is None:
        return
    try:
        img.colorspace_settings.name = "sRGB"
    except Exception:
        pass


def _make_emission_material(name: str, image) -> bpy.types.Material:
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (300, 0)
    emit = nodes.new("ShaderNodeEmission")
    emit.location = (80, 0)
    try:
        emit.inputs["Strength"].default_value = 1.0
    except Exception:
        pass
    tex = nodes.new("ShaderNodeTexImage")
    tex.location = (-220, 0)
    tex.image = image
    # Pixel UI art: one nearest sample — Linear/Cubic looks like a second blur pass.
    try:
        tex.interpolation = "Closest"
    except Exception:
        pass
    try:
        tex.extension = "CLIP"
    except Exception:
        pass
    links.new(tex.outputs["Color"], emit.inputs["Color"])
    try:
        links.new(tex.outputs["Alpha"], emit.inputs["Alpha"] if "Alpha" in emit.inputs else emit.inputs[1])
    except Exception:
        pass
    # Transparent where icon alpha is 0.
    bsdf = nodes.new("ShaderNodeBsdfTransparent")
    bsdf.location = (80, -160)
    mix = nodes.new("ShaderNodeMixShader")
    mix.location = (200, 0)
    try:
        links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
    except Exception:
        mix.inputs[0].default_value = 1.0
    links.new(bsdf.outputs[0], mix.inputs[1])
    links.new(emit.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], out.inputs["Surface"])
    try:
        mat.blend_method = "BLEND"
    except Exception:
        pass
    try:
        mat.shadow_method = "NONE"
    except Exception:
        pass
    return mat


def create_image_plane(
    *,
    name: str,
    image_path: str,
    height_m: float,
    location: Vector,
    facing_y: bool = True,
    reference_role: str = "",
) -> bpy.types.Object | None:
    """Create an XY (or XZ) image plane of world height *height_m* at *location*."""
    image = _load_image(image_path)
    if image is None:
        return None
    try:
        iw = float(image.size[0]) or 1.0
        ih = float(image.size[1]) or 1.0
    except Exception:
        iw = ih = 1.0
    aspect = max(iw / ih, 1e-6)
    height_m = max(float(height_m), 1e-3)
    width_m = height_m * aspect

    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0.0, 0.0, 0.0))
    plane = bpy.context.active_object
    if plane is None:
        return None
    plane.name = name
    # Default plane is XY; rotate to stand in XZ facing +Y (character front).
    if facing_y:
        plane.rotation_euler = (1.57079632679, 0.0, 0.0)
    plane.scale = (width_m, height_m, 1.0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    # Write world matrix (location alone can lag matrix_world until depsgraph update,
    # which then breaks parenting math on 0.01-scale UE armatures).
    plane.matrix_world = Matrix.Translation(Vector(location))
    _depsgraph_update()

    mat = _make_emission_material(f"{name}_Mat", image)
    if plane.data.materials:
        plane.data.materials[0] = mat
    else:
        plane.data.materials.append(mat)

    if not reference_role:
        reference_role = next((r for r in _REF_TYPE_COLLECTIONS if name.startswith("REF_" + r)), "")
    col = _reference_collection(reference_role)
    try:
        for c in list(plane.users_collection):
            c.objects.unlink(plane)
        col.objects.link(plane)
    except Exception:
        pass
    try:
        plane["arc_reference_plane"] = 1
        plane["arc_reference_image"] = image_path
        plane["arc_reference_role"] = reference_role
        plane.pass_index = int(REF_PASS_INDEX)
    except Exception:
        pass
    return plane


def _meshes_with_role(objects, role: str) -> list:
    role_cf = (role or "").casefold()
    hits = []
    for obj in objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        try:
            r = str(obj.get("arc_assembly_role") or "")
        except Exception:
            r = ""
        if r.casefold() == role_cf:
            hits.append(obj)
    return hits


def _outfit_meshes(objects) -> list:
    """All clothing-ish meshes in the batch (exclude head/hair/body/ref)."""
    skip = {"head", "hair", "facialhair", "body"}
    hits = []
    for obj in objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        try:
            if obj.get("arc_reference_plane"):
                continue
            r = str(obj.get("arc_assembly_role") or "Outfit").casefold()
        except Exception:
            r = "outfit"
        if r in skip:
            continue
        hits.append(obj)
    if hits:
        return hits
    return [o for o in objects if getattr(o, "type", "") == "MESH" and not o.get("arc_reference_plane")]


def place_outfit_reference(objects, image_path: str) -> bpy.types.Object | None:
    meshes = _outfit_meshes(objects)
    bb = _world_bbox(meshes)
    if bb is None or not image_path:
        return None
    mins, maxs = bb
    full_h = max(maxs.z - mins.z, 1e-3)
    plane_h = full_h * _OUTFIT_ICON_HEIGHT_FRACTION
    center = (mins + maxs) * 0.5
    # Align tops; sit left of the outfit AABB (gap from authored blend).
    loc = Vector((mins.x - _OUTFIT_REF_LEFT_GAP_M, center.y, maxs.z - plane_h * 0.5))
    plane = create_image_plane(
        name="REF_Outfit",
        image_path=image_path,
        height_m=plane_h,
        location=loc,
    )
    _anchor_plane(plane, _outfit_anchor(objects) or _outfit_anchor(meshes))
    return plane


# Offsets copied from the working blend's REF_Head / REF_Hair.
# Face card: left edge flush with the head's right side, center 6.4 cm above the head center.
# Hair card: same X/Y as the hair mesh, sitting on top of it.
_HEAD_REF_Z_LIFT = 0.064


def _head_meshes(objects) -> list:
    meshes = _meshes_with_role(objects, "Head")
    if meshes:
        return meshes
    return [
        o for o in objects
        if getattr(o, "type", "") == "MESH" and "head" in o.name.casefold()
        and not o.get("arc_reference_plane")
    ]


def _hair_meshes(objects) -> list:
    meshes = _meshes_with_role(objects, "Hair") or _meshes_with_role(objects, "FacialHair")
    if meshes:
        return meshes
    return [
        o for o in objects
        if getattr(o, "type", "") == "MESH"
        and ("hair" in o.name.casefold() or "beard" in o.name.casefold())
        and not o.get("arc_reference_plane")
    ]


def _plane_world_size(plane) -> tuple[float, float]:
    """World width (X) and height (Z) of a reference plane."""
    bb = _world_bbox([plane])
    if bb is None:
        return 0.2, 0.2
    mins, maxs = bb
    return max(maxs.x - mins.x, 1e-3), max(maxs.z - mins.z, 1e-3)


def _move_ref_plane(plane, location: Vector) -> None:
    if plane is None:
        return
    _depsgraph_update()
    mw = plane.matrix_world.copy()
    mw.translation = Vector(location)
    plane.matrix_world = mw
    parent = plane.parent
    if parent is not None:
        try:
            plane.matrix_parent_inverse = Matrix.Identity(4)
            plane.matrix_local = parent.matrix_world.inverted() @ plane.matrix_world
        except Exception:
            pass
    _depsgraph_update()


def layout_head_hair_stack(objects, head_plane=None, hair_plane=None) -> None:
    """Place face and hair cards using the working-blend offsets."""
    if head_plane is not None:
        bb = _world_bbox(_head_meshes(objects))
        if bb is not None:
            mins, maxs = bb
            center = (mins + maxs) * 0.5
            head_w, _head_h = _plane_world_size(head_plane)
            _move_ref_plane(
                head_plane,
                Vector((maxs.x + head_w * 0.5, center.y, center.z + _HEAD_REF_Z_LIFT)),
            )
    if hair_plane is not None:
        bb = _world_bbox(_hair_meshes(objects)) or _world_bbox(_head_meshes(objects))
        if bb is not None:
            mins, maxs = bb
            center = (mins + maxs) * 0.5
            _hair_w, hair_h = _plane_world_size(hair_plane)
            mesh_h = max(maxs.z - mins.z, 1e-3)
            _move_ref_plane(
                hair_plane,
                Vector((center.x, center.y, center.z + (mesh_h + hair_h) * 0.5)),
            )


def place_head_reference(objects, image_path: str) -> bpy.types.Object | None:
    meshes = _head_meshes(objects)
    bb = _world_bbox(meshes)
    if bb is None or not image_path:
        return None
    mins, maxs = bb
    h = max(maxs.z - mins.z, maxs.x - mins.x, 0.15) * 0.65
    center = (mins + maxs) * 0.5
    loc = Vector((maxs.x + 0.25, center.y, center.z))
    plane = create_image_plane(
        name="REF_Head",
        image_path=image_path,
        height_m=h,
        location=loc,
    )
    anchor = None
    for obj in meshes:
        if obj.parent and obj.parent.type == "ARMATURE":
            anchor = obj.parent
            break
    _anchor_plane(plane, anchor or (meshes[0].parent or meshes[0] if meshes else None)
                  or _outfit_anchor(objects))
    return plane


def place_hair_reference(objects, image_path: str) -> bpy.types.Object | None:
    meshes = _hair_meshes(objects)
    # Stack against the head when one is in the same import; else the hair mesh.
    anchor_meshes = _head_meshes(objects) or meshes
    bb = _world_bbox(anchor_meshes)
    if bb is None or not image_path:
        return None
    mins, maxs = bb
    h = max(maxs.z - mins.z, maxs.x - mins.x, 0.12) * 0.65
    center = (mins + maxs) * 0.5
    loc = Vector((maxs.x + 0.25, center.y, maxs.z + h * 0.5))
    plane = create_image_plane(
        name="REF_FacialHair" if _meshes_with_role(objects, "FacialHair") and not _meshes_with_role(objects, "Hair") else "REF_Hair",
        image_path=image_path,
        height_m=h,
        location=loc,
    )
    anchor = None
    for obj in meshes:
        if obj.parent and obj.parent.type == "ARMATURE":
            anchor = obj.parent
            break
    _anchor_plane(plane, anchor or (meshes[0].parent or meshes[0] if meshes else None)
                  or _outfit_anchor(objects))
    return plane


def place_asset_reference(objects, image_path: str, role: str) -> bpy.types.Object | None:
    """Place a type-specific cosmetic card beside its actual imported mesh."""
    meshes = _meshes_with_role(objects, role)
    bb = _world_bbox(meshes)
    if bb is None or not image_path:
        return None
    mins, maxs = bb
    height = max(maxs.z - mins.z, maxs.x - mins.x, 0.15) * 0.75
    center = (mins + maxs) * 0.5
    plane = create_image_plane(name=f"REF_{role}", image_path=image_path,
                               height_m=height,
                               location=Vector((maxs.x + height * 0.6 + 0.08,
                                                center.y, center.z)),
                               reference_role=role)
    anchor = meshes[0].parent or meshes[0]
    _anchor_plane(plane, anchor)
    if plane is not None and "arc_import_slot" in meshes[0]:
        plane["arc_import_slot"] = int(meshes[0]["arc_import_slot"])
    return plane


def resolve_outfit_icon_path(context) -> str:
    """Full (uncropped) ``T_UI_Icon_Outfit_*`` for the selected outfit."""
    try:
        from . import appearance
        from . import importing as _importing
    except Exception:
        return ""
    scene = getattr(context, "scene", None)
    if scene is None:
        return ""
    key = (
        getattr(scene, "arc_selected_outfit", "")
        or getattr(scene, "arc_selected_outfit_browse", "")
        or ""
    )
    if not key or key == "NONE":
        return ""
    try:
        rows = _importing.load_outfit_csv(_importing.get_outfit_csv_path(context))
        row = _importing.find_outfit_row(rows, key)
    except Exception:
        return ""
    if not row:
        return ""
    for stem in appearance._outfit_preview_stems(row):
        rel = f"Items/Characters/Skins/Outfit/{stem}/T_UI_Icon_Outfit_{stem}.png"
        path = appearance._resolve_csv_rel_path(rel)
        if path:
            return path
    return ""


def import_references_for_objects(context, objects, *, roles: set[str] | None = None) -> list[str]:
    """Create reference planes for the given roles among *objects*. Returns log lines."""
    from . import appearance

    msgs: list[str] = []
    from . import import_layout
    want = {import_layout.normalize_role_name(str(r)) for r in (roles or {"Outfit", "Head", "Hair"})}
    objs = [o for o in objects if o and o.name in bpy.data.objects]
    head_plane = None
    hair_plane = None

    if "Outfit" in want:
        path = resolve_outfit_icon_path(context)
        plane = place_outfit_reference(objs, path) if path else None
        if plane:
            msgs.append(f"Reference outfit plane ← {os.path.basename(path)}")
        elif path:
            msgs.append("Reference outfit plane skipped (no outfit meshes)")
        else:
            msgs.append("Reference outfit: no UI icon for selected outfit")

    if "Head" in want:
        head_id = getattr(context.scene, "arc_appearance_head", "NONE") or "NONE"
        choice = appearance.resolve_choice(head_id) if head_id != "NONE" else None
        path = ensure_choice_icon(choice, context.scene) if choice else ""
        head_plane = place_head_reference(objs, path) if path else None
        if head_plane:
            msgs.append(f"Reference head plane ← {os.path.basename(path)}")

    for role, selector in (("Hair", "arc_appearance_hair"), ("FacialHair", "arc_appearance_facial_hair")):
        if role not in want:
            continue
        hair_id = getattr(context.scene, selector, "NONE") or "NONE"
        choice = appearance.resolve_choice(hair_id) if hair_id != "NONE" else None
        path = ensure_choice_icon(choice, context.scene) if choice else ""
        role_objects = [o for o in objs if o.get("arc_assembly_role") == role]
        hair_plane = place_hair_reference(role_objects, path) if path else None
        if hair_plane:
            layout_head_hair_stack(role_objects, None, hair_plane)
            msgs.append(f"Reference {role} plane ← {os.path.basename(path)}")

    for role, selector in (("Backpack", "arc_appearance_backpack"),
                           ("Attachment", "arc_appearance_attachment"),
                           ("Charm", "arc_appearance_charm"),
                           ("RaiderTool", "arc_appearance_raider_tool")):
        if role not in want:
            continue
        choice_id = getattr(context.scene, selector, "NONE") or "NONE"
        choice = appearance.resolve_choice(choice_id) if choice_id != "NONE" else None
        path = ensure_choice_icon(choice, context.scene) if choice else ""
        plane = place_asset_reference(objs, path, role) if path else None
        if plane:
            msgs.append(f"Reference {role} plane ← {os.path.basename(path)}")
        else:
            msgs.append(f"Reference {role}: no UI icon for selected asset" if not path
                        else f"Reference {role} plane skipped (no {role} meshes)")

    if head_plane is not None:
        layout_head_hair_stack(objs, head_plane, None)

    return msgs


def import_references_from_scene(context) -> tuple[int, list[str]]:
    """Button entry: build planes for selected / recent outfit + appearance parts."""
    selected = [o for o in context.selected_objects if o.type == "MESH"]
    # Expand: include sibling meshes under same armature parents.
    objs = list(selected)
    for obj in list(selected):
        arm = obj if obj.type == "ARMATURE" else obj.parent
        if arm and arm.type == "ARMATURE":
            for c in arm.children:
                if c.type == "MESH" and c not in objs:
                    objs.append(c)
    if not objs:
        objs = [
            o for o in context.scene.objects
            if o.type == "MESH" and o.get("arc_assembly_role")
        ]
    if not objs:
        return 0, ["Select an imported outfit / head / hair mesh first"]
    from . import import_layout
    roles = {import_layout.infer_role(o) for o in objs} & set(_REF_TYPE_COLLECTIONS)
    msgs = import_references_for_objects(context, objs, roles=roles or {"Outfit"})
    made = sum(1 for m in msgs if "plane ←" in m)
    return made, msgs
