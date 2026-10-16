"""UI reference image planes sized to match imported outfit / head / hair meshes."""

from __future__ import annotations

import os
import json
import math
from typing import Iterable

import bpy
import numpy as np
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
_IMAGE_CROP_CACHE = {}
_REFERENCE_DEFAULTS_CACHE = {}
# Measured from the user's Toolbox, Mouse, Football and Moustache cards.
_BACKPACK_HEIGHT_RATIO = 1.06908
_BACKPACK_GAP_M = 0.0113
_BACKPACK_TOP_OFFSET_M = -0.0044
_CHARM_GAP_M = 0.15925
_ATTACHMENT_GAP_M = 0.22884
_FACIAL_CARD_HEIGHT_M = 0.241069
_FACIAL_CARD_X_OFFSET_M = 0.472453
_FACIAL_CARD_Z_OFFSET_M = 0.048221


def reference_defaults(role: str) -> dict:
    path = os.path.join(os.path.dirname(__file__), "reference", "reference_card_defaults.json")
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        return {}
    if _REFERENCE_DEFAULTS_CACHE.get("stamp") != stamp:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        _REFERENCE_DEFAULTS_CACHE.clear()
        _REFERENCE_DEFAULTS_CACHE.update(stamp=stamp, roles=data.get("roles", {}))
    return _REFERENCE_DEFAULTS_CACHE["roles"].get(role, {})


def _calibrated_layout(role, objects, scene=None):
    defaults = reference_defaults(role)
    if not defaults:
        return None
    from . import import_layout
    slots = [import_layout.slot_of_object(obj) for obj in objects]
    slot = next((value for value in slots if value is not None), 0)
    x, y = import_layout.slot_world_xy(slot, scene)
    location = Vector(defaults["position_m"]) + Vector((x, y, 0.0))
    return float(defaults["height_m"]), location


def _reference_scale(role):
    return Vector(reference_defaults(role).get("scale", (1.0, 1.0, 1.0)))


def _artwork_rotation(role):
    return float(reference_defaults(role).get("artwork_rotation_deg", 0.0))


def _card_matrix(location, scale):
    matrix = Matrix.Diagonal(Vector((*scale, 1.0)))
    matrix.translation = Vector(location)
    return matrix


def image_crop_bounds(image, artwork_rotation_deg=0.0) -> tuple[float, float, float, float]:
    """UV rectangle of visible art; original pixels and files stay untouched."""
    width, height = int(image.size[0]), int(image.size[1])
    if width < 1 or height < 1:
        return (0.0, 0.0, 1.0, 1.0)
    key = (image.as_pointer(), width, height, float(artwork_rotation_deg))
    if key in _IMAGE_CROP_CACHE:
        return _IMAGE_CROP_CACHE[key]
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    rgba = pixels.reshape(height, width, 4)
    visible = rgba[:, :, 3] > (1.0 / 255.0)
    # An opaque, uniform border can be blank padding too. Only remove it when
    # all four corners agree and the outer edge is overwhelmingly that colour.
    if np.all(visible):
        corners = rgba[[0, 0, -1, -1], [0, -1, 0, -1], :3]
        background = np.median(corners, axis=0)
        edge = np.concatenate((rgba[0, :, :3], rgba[-1, :, :3], rgba[:, 0, :3], rgba[:, -1, :3]))
        tolerance = 2.0 / 255.0
        if (np.min(background) > 0.8 and np.ptp(background) < 0.02
                and np.max(np.abs(corners - background)) <= tolerance
                and np.mean(np.max(np.abs(edge - background), axis=1) <= tolerance) > 0.98):
            foreground = np.max(np.abs(rgba[:, :, :3] - background), axis=2) > tolerance
            if np.count_nonzero(foreground) >= 4:
                visible &= foreground
    rows, columns = np.nonzero(visible)
    if len(rows) and abs(artwork_rotation_deg) > 1e-8:
        angle = math.radians(artwork_rotation_deg)
        cosine, sine = math.cos(angle), math.sin(angle)
        x, y = columns + 0.5 - width * 0.5, rows + 0.5 - height * 0.5
        u = 0.5 + (cosine * x + sine * y) / width
        v = 0.5 + (-sine * x + cosine * y) / height
        # Include complete rotated pixel footprints. Bounds may extend beyond
        # the source canvas; clamping would clip the 10Mil handle tip.
        half_u = (abs(cosine) + abs(sine)) * 0.5 / width
        half_v = (abs(cosine) + abs(sine)) * 0.5 / height
        bounds = (float(u.min() - half_u), float(v.min() - half_v),
                  float(u.max() + half_u), float(v.max() + half_v))
    else:
        bounds = ((float(columns.min()) / width, float(rows.min()) / height,
                   float(columns.max() + 1) / width, float(rows.max() + 1) / height)
                  if len(rows) else (0.0, 0.0, 1.0, 1.0))
    _IMAGE_CROP_CACHE[key] = bounds
    return bounds


def _image_aspect(image, bounds) -> float:
    u0, v0, u1, v1 = bounds
    return max(float(image.size[0]) * (u1 - u0)
               / max(float(image.size[1]) * (v1 - v0), 1e-6), 1e-6)


def _configure_camera_only(plane) -> None:
    """Reference art is visible to the viewer without lighting the model."""
    for name in ("visible_diffuse", "visible_glossy", "visible_transmission",
                 "visible_volume_scatter", "visible_shadow"):
        if hasattr(plane, name):
            setattr(plane, name, False)
    if hasattr(plane, "visible_camera"):
        plane.visible_camera = True
    plane.pass_index = int(REF_PASS_INDEX)


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


def bind_legacy_reference_planes(scene=None, *, legacy_linear=False, roles=None) -> int:
    """Recover old loose cards' ownership from their icon type and exact pad."""
    from . import import_layout
    sc = scene or bpy.context.scene
    meshes = [o for o in sc.objects if o.type == "MESH" and import_layout.infer_role(o)]
    count = 0
    for plane in list(sc.objects):
        if not plane.get("arc_reference_plane") or plane.parent is not None:
            continue
        role = _reference_role(plane)
        if roles is not None and role not in roles:
            continue
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


def categorize_reference_planes(scene=None, *, roles=None) -> int:
    """Move existing cards into their type collection, preserving their poses."""
    sc = scene or bpy.context.scene
    from . import asset_organization
    planes = [o for o in sc.objects if o.get("arc_reference_plane")
              and (roles is None or _reference_role(o) in roles)]
    for plane in planes:
        plane["arc_reference_role"] = _reference_role(plane)
    asset_organization.organize_objects(planes, sc)
    return len(planes)


def _depsgraph_update() -> None:
    """Ensure matrix_world reflects slot layout before measuring AABBs."""
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass


def _world_bbox(objects: Iterable, *, update=True) -> tuple[Vector, Vector] | None:
    if update:
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


def _anchor_plane(plane, anchor, *, organize=True, world_matrix=None, update=True) -> None:
    """Parent *plane* to *anchor* while keeping the current world transform."""
    if plane is None or anchor is None:
        return
    try:
        if update:
            _depsgraph_update()
        mw = world_matrix.copy() if world_matrix is not None else plane.matrix_world.copy()
        location, rotation, scale = mw.decompose()
        if abs(rotation.angle) > 1e-7:
            if plane.data.users > 1:
                plane.data = plane.data.copy()
            scaling = Matrix.Diagonal(Vector((*scale, 1.0)))
            plane.data.transform(scaling.inverted() @ rotation.to_matrix().to_4x4() @ scaling)
            mw = _card_matrix(location, scale)
        plane.parent = anchor
        plane.parent_type = 'OBJECT'
        plane.parent_bone = ''
        # Cancel the initial parent frame, preserving operative world scale
        # and zero local/world Euler rotation. Later parent movement still follows.
        plane.matrix_parent_inverse = anchor.matrix_world.inverted()
        plane.rotation_mode = 'XYZ'
        plane.delta_location = (0.0, 0.0, 0.0)
        plane.delta_rotation_euler = (0.0, 0.0, 0.0)
        plane.delta_rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        plane.delta_scale = (1.0, 1.0, 1.0)
        plane.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        plane.rotation_axis_angle = (0.0, 0.0, 0.0, 1.0)
        plane.matrix_basis = mw
        if update:
            _depsgraph_update()
        plane["arc_reference_anchor"] = anchor.name
        if "arc_import_slot" in anchor:
            plane["arc_import_slot"] = int(anchor["arc_import_slot"])
    except Exception:
        pass
    if organize:
        from . import asset_organization
        asset_organization.organize_objects([plane], bpy.context.scene)


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


def _make_emission_material(name: str, image, bounds=(0.0, 0.0, 1.0, 1.0), material=None, *, artwork_rotation_deg=0.0) -> bpy.types.Material:
    mat = (material.copy() if material is not None and material.users > 1 else material)
    mat = mat or bpy.data.materials.new(name=name)
    mat.use_nodes = True
    mat.use_backface_culling = False
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (850, 0)
    emit = nodes.new("ShaderNodeEmission")
    emit.location = (250, 0)
    emit.inputs["Strength"].default_value = 1.0
    tex = nodes.new("ShaderNodeTexImage")
    tex.location = (0, 0)
    tex.image = image
    tex.interpolation = "Closest"
    tex.extension = "CLIP"
    # A back view must read the same way as the front. Mirror U about the crop
    # centre rather than the full texture centre, which may contain padding.
    coordinates = nodes.new("ShaderNodeTexCoord")
    coordinates.location = (-850, 0)
    separate = nodes.new("ShaderNodeSeparateXYZ")
    separate.location = (-650, -80)
    links.new(coordinates.outputs["UV"], separate.inputs[0])
    mirror = nodes.new("ShaderNodeMath")
    mirror.operation = "SUBTRACT"
    mirror.inputs[0].default_value = bounds[0] + bounds[2]
    mirror.location = (-470, -100)
    links.new(separate.outputs["X"], mirror.inputs[1])
    combine = nodes.new("ShaderNodeCombineXYZ")
    combine.location = (-300, -100)
    links.new(mirror.outputs[0], combine.inputs["X"])
    links.new(separate.outputs["Y"], combine.inputs["Y"])
    geometry = nodes.new("ShaderNodeNewGeometry")
    geometry.location = (-650, 220)
    select = nodes.new("ShaderNodeMixRGB")
    select.blend_type = "MIX"
    select.location = (-150, 80)
    links.new(geometry.outputs["Backfacing"], select.inputs[0])
    links.new(coordinates.outputs["UV"], select.inputs[1])
    links.new(combine.outputs[0], select.inputs[2])
    sampled_uv = select.outputs[0]
    if abs(artwork_rotation_deg) > 1e-8:
        # First reflect the back view in the rotated card's crop space, then
        # inverse-rotate into the original texture. W/H keeps pixel angles exact.
        aspect = float(image.size[0]) / max(float(image.size[1]), 1.0)
        subtract = nodes.new("ShaderNodeVectorMath"); subtract.operation = 'SUBTRACT'
        subtract.inputs[1].default_value = (0.5, 0.5, 0.0)
        links.new(sampled_uv, subtract.inputs[0])
        pixel_scale = nodes.new("ShaderNodeVectorMath"); pixel_scale.operation = 'MULTIPLY'
        pixel_scale.inputs[1].default_value = (aspect, 1.0, 1.0)
        links.new(subtract.outputs[0], pixel_scale.inputs[0])
        rotate = nodes.new("ShaderNodeVectorRotate")
        rotate.rotation_type = 'AXIS_ANGLE'
        rotate.inputs['Axis'].default_value = (0.0, 0.0, 1.0)
        rotate.inputs['Angle'].default_value = math.radians(artwork_rotation_deg)
        links.new(pixel_scale.outputs[0], rotate.inputs['Vector'])
        uv_scale = nodes.new("ShaderNodeVectorMath"); uv_scale.operation = 'MULTIPLY'
        uv_scale.inputs[1].default_value = (1.0 / aspect, 1.0, 1.0)
        links.new(rotate.outputs[0], uv_scale.inputs[0])
        add = nodes.new("ShaderNodeVectorMath"); add.operation = 'ADD'
        add.inputs[1].default_value = (0.5, 0.5, 0.0)
        links.new(uv_scale.outputs[0], add.inputs[0])
        sampled_uv = add.outputs[0]
    links.new(sampled_uv, tex.inputs["Vector"])
    links.new(tex.outputs["Color"], emit.inputs["Color"])
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (250, -180)
    alpha_mix = nodes.new("ShaderNodeMixShader")
    alpha_mix.location = (450, 0)
    links.new(tex.outputs["Alpha"], alpha_mix.inputs[0])
    links.new(transparent.outputs[0], alpha_mix.inputs[1])
    links.new(emit.outputs[0], alpha_mix.inputs[2])
    # Also gate the shader by ray type, so the card does not occlude lights or
    # appear in glossy/transmission bounces when an engine supports Light Path.
    light_path = nodes.new("ShaderNodeLightPath")
    light_path.location = (250, -350)
    camera_mix = nodes.new("ShaderNodeMixShader")
    camera_mix.location = (650, 0)
    links.new(light_path.outputs["Is Camera Ray"], camera_mix.inputs[0])
    links.new(transparent.outputs[0], camera_mix.inputs[1])
    links.new(alpha_mix.outputs[0], camera_mix.inputs[2])
    links.new(camera_mix.outputs[0], out.inputs["Surface"])
    try:
        mat.surface_render_method = "DITHERED"
    except (AttributeError, TypeError):
        pass
    if hasattr(mat, "use_transparent_shadow"):
        mat.use_transparent_shadow = True
    mat["arc_reference_camera_only"] = True
    mat["arc_reference_double_sided"] = True
    mat["arc_reference_artwork_rotation_deg"] = float(artwork_rotation_deg)
    return mat


def _set_plane_geometry(plane, width_m, height_m, bounds, facing_y=True, *, artwork_rotation_deg=0.0):
    # Fixed plane orientation, not a camera-facing constraint. UV cropping
    # removes margins geometrically and never creates or edits a PNG.
    half_w, half_h = width_m * 0.5, height_m * 0.5
    if plane.data.users > 1:
        plane.data = plane.data.copy()
    vertices = ([(-half_w, 0, -half_h), (half_w, 0, -half_h),
                 (half_w, 0, half_h), (-half_w, 0, half_h)] if facing_y else
                [(-half_w, -half_h, 0), (half_w, -half_h, 0),
                 (half_w, half_h, 0), (-half_w, half_h, 0)])
    for layer in list(plane.data.uv_layers):
        plane.data.uv_layers.remove(layer)
    plane.data.clear_geometry()
    plane.data.from_pydata(vertices, [], [(0, 1, 2, 3)])
    uv = plane.data.uv_layers.new(name="UVMap")
    u0, v0, u1, v1 = bounds
    for loop, value in zip(uv.data, [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]):
        loop.uv = value
    plane.data.update()
    plane["arc_reference_uv_bounds"] = list(bounds)
    plane["arc_reference_artwork_rotation_deg"] = float(artwork_rotation_deg)
    _configure_camera_only(plane)


def create_image_plane(
    *, name: str, image_path: str, height_m: float, location: Vector,
    facing_y: bool = True, reference_role: str = "",
) -> bpy.types.Object | None:
    """Create a cropped, double-sided card with visible art height *height_m*."""
    image = _load_image(image_path)
    if image is None:
        return None
    if not reference_role:
        reference_role = next((r for r in _REF_TYPE_COLLECTIONS if name.startswith("REF_" + r)), "")
    angle = _artwork_rotation(reference_role)
    scale = _reference_scale(reference_role)
    bounds = image_crop_bounds(image, angle)
    height_m = max(float(height_m), 1e-3)
    width_m = height_m * _image_aspect(image, bounds)
    mesh = bpy.data.meshes.new(name + "_Mesh")
    plane = bpy.data.objects.new(name, mesh)
    _set_plane_geometry(plane, width_m / scale.x, height_m / scale.z, bounds,
                        facing_y, artwork_rotation_deg=angle)
    plane.matrix_world = _card_matrix(location, scale)
    mat = _make_emission_material(f"{name}_Mat", image, bounds, artwork_rotation_deg=angle)
    mesh.materials.append(mat)
    if not reference_role:
        reference_role = next((r for r in _REF_TYPE_COLLECTIONS if name.startswith("REF_" + r)), "")
    _reference_collection(reference_role).objects.link(plane)
    plane["arc_reference_plane"] = 1
    plane["arc_reference_image"] = image_path
    plane["arc_reference_role"] = reference_role
    if reference_defaults(reference_role):
        plane["arc_reference_layout_schema"] = 2
    _depsgraph_update()
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
    _anchor_plane(plane, _outfit_anchor(objects) or _outfit_anchor(meshes)
                  or (meshes[0].parent or meshes[0] if meshes else None))
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
        _anchor_plane(plane, parent, organize=False, world_matrix=mw)
    _depsgraph_update()


def layout_head_hair_stack(objects, head_plane=None, hair_plane=None) -> None:
    """Place face and hair cards using the working-blend offsets."""
    if head_plane is not None and reference_defaults("Head"):
        head_plane = None
    if hair_plane is not None and reference_defaults(_reference_role(hair_plane)):
        hair_plane = None
    if hair_plane is not None and _reference_role(hair_plane) == "FacialHair":
        hair_plane = None  # facial portraits use the measured Moustache layout
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
    if reference_defaults("Head"):
        return place_asset_reference(objects, image_path, "Head")
    meshes = _head_meshes(objects)
    bb = _world_bbox(meshes)
    if bb is None or not image_path:
        return None
    mins, maxs = bb
    h = max(maxs.z - mins.z, maxs.x - mins.x, 0.15)
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
    from . import asset_organization
    asset_organization.organize_objects([plane], bpy.context.scene)
    return plane


def place_hair_reference(objects, image_path: str) -> bpy.types.Object | None:
    meshes = _hair_meshes(objects)
    if _meshes_with_role(objects, "FacialHair") and not _meshes_with_role(objects, "Hair"):
        return place_asset_reference(objects, image_path, "FacialHair")
    if reference_defaults("Hair"):
        return place_asset_reference(objects, image_path, "Hair")
    # Stack against the head when one is in the same import; else the hair mesh.
    anchor_meshes = _head_meshes(objects) or meshes
    bb = _world_bbox(anchor_meshes)
    if bb is None or not image_path:
        return None
    mins, maxs = bb
    h = max(maxs.z - mins.z, maxs.x - mins.x, 0.12)
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
    from . import asset_organization
    asset_organization.organize_objects([plane], bpy.context.scene)
    return plane


def place_asset_reference(objects, image_path: str, role: str) -> bpy.types.Object | None:
    """Place a type-specific cosmetic card beside its actual imported mesh."""
    meshes = _meshes_with_role(objects, role)
    bb = _world_bbox(meshes)
    if bb is None or not image_path:
        return None
    mins, maxs = bb
    image = _load_image(image_path)
    if image is None:
        return None
    height, location = (_calibrated_layout(role, objects)
                        or _asset_card_layout(mins, maxs, role,
                                              _image_aspect(image, image_crop_bounds(image, _artwork_rotation(role)))))
    plane = create_image_plane(name=f"REF_{role}", image_path=image_path,
                               height_m=height,
                               location=location,
                               reference_role=role)
    anchor = meshes[0].parent or meshes[0]
    _anchor_plane(plane, anchor)
    if plane is not None and "arc_import_slot" in meshes[0]:
        plane["arc_import_slot"] = int(meshes[0]["arc_import_slot"])
    from . import asset_organization
    asset_organization.organize_objects([plane], bpy.context.scene)
    return plane


def _asset_card_layout(mins, maxs, role, aspect):
    center = (mins + maxs) * 0.5
    height = max(maxs.z - mins.z, 0.001)
    gap = 0.08
    if role == "FacialHair":
        # These icons show a full face, not an isolated beard/moustache.
        return _FACIAL_CARD_HEIGHT_M, Vector((center.x + _FACIAL_CARD_X_OFFSET_M,
                                              center.y, center.z + _FACIAL_CARD_Z_OFFSET_M))
    if role == "Backpack":
        height *= _BACKPACK_HEIGHT_RATIO
        gap = _BACKPACK_GAP_M
        z = maxs.z + _BACKPACK_TOP_OFFSET_M - height * 0.5
    else:
        gap = _CHARM_GAP_M if role == "Charm" else _ATTACHMENT_GAP_M if role == "Attachment" else gap
        z = mins.z + height * 0.5 + (0.005 if role == "Attachment" else 0.0)
    return height, Vector((maxs.x + gap + height * aspect * 0.5, center.y, z))


def _card_layout(meshes, role, aspect, *, bbox=None):
    calibrated = _calibrated_layout(role, meshes)
    if calibrated:
        return calibrated
    bb = bbox if bbox is not None else _world_bbox(meshes)
    if bb is None:
        return None
    mins, maxs = bb
    center = (mins + maxs) * 0.5
    if role == "Outfit":
        height = max(maxs.z - mins.z, 0.001) * _OUTFIT_ICON_HEIGHT_FRACTION
        return height, Vector((mins.x - _OUTFIT_REF_LEFT_GAP_M, center.y, maxs.z - height * 0.5))
    if role == "Head":
        height = max(maxs.z - mins.z, maxs.x - mins.x, 0.15)
        return height, Vector((maxs.x + height * aspect * 0.5, center.y, center.z + _HEAD_REF_Z_LIFT))
    if role == "Hair":
        height = max(maxs.z - mins.z, maxs.x - mins.x, 0.12)
        return height, Vector((center.x, center.y, maxs.z + height * 0.5))
    return _asset_card_layout(mins, maxs, role, aspect)


def _preserved_visible_layout(plane, bounds):
    bb = _world_bbox([plane], update=False)
    if bb is None:
        return None
    mins, maxs = bb
    previous = list(plane.get("arc_reference_uv_bounds") or (0.0, 0.0, 1.0, 1.0))
    old_u0, old_v0, old_u1, old_v1 = previous
    u0, v0, u1, v1 = bounds
    width, height = maxs.x - mins.x, maxs.z - mins.z
    left = mins.x + width * (u0 - old_u0) / max(old_u1 - old_u0, 1e-6)
    right = mins.x + width * (u1 - old_u0) / max(old_u1 - old_u0, 1e-6)
    bottom = mins.z + height * (v0 - old_v0) / max(old_v1 - old_v0, 1e-6)
    top = mins.z + height * (v1 - old_v0) / max(old_v1 - old_v0, 1e-6)
    return max(top - bottom, 0.001), Vector(((left + right) * 0.5, (mins.y + maxs.y) * 0.5, (bottom + top) * 0.5))


def _update_card(plane, meshes, role, *, preserve_calibration=True, model_bbox=None):
    path = str(plane.get("arc_reference_image") or "")
    image = _load_image(path)
    if image is None:
        return False
    angle = _artwork_rotation(role)
    scale = _reference_scale(role)
    bounds = image_crop_bounds(image, angle)
    aspect = _image_aspect(image, bounds)
    layout = _card_layout(meshes, role, aspect, bbox=model_bbox)
    calibration = os.path.basename(path).casefold() in {
        "t_ui_icon_backpackcontainer_toolbox.png",
        "t_ui_icon_facialhair_moustacheregular.png",
        "t_ui_icon_backpackcharm_computermouse_01_a.png",
    }
    if preserve_calibration and calibration and not reference_defaults(role):
        layout = _preserved_visible_layout(plane, bounds)
    if layout is None:
        return False
    height, location = layout
    anchor = plane.parent or (meshes[0].parent or meshes[0])
    _set_plane_geometry(plane, height * aspect / scale.x, height / scale.z, bounds,
                        artwork_rotation_deg=angle)
    material = plane.data.materials[0] if plane.data.materials else None
    material = _make_emission_material(plane.name + "_Mat", image, bounds, material,
                                       artwork_rotation_deg=angle)
    if plane.data.materials:
        plane.data.materials[0] = material
    else:
        plane.data.materials.append(material)
    world = _card_matrix(location, scale)
    plane.matrix_world = world
    _anchor_plane(plane, anchor, organize=False,
                  world_matrix=world, update=False)
    plane["arc_reference_role"] = role
    if reference_defaults(role):
        plane["arc_reference_layout_schema"] = 2
    return True


def _backpack_choice_for_meshes(meshes):
    """Recover older imports by exact source package when their choice ID is absent."""
    from . import appearance
    for obj in meshes:
        cid = str(obj.get("arc_backpack_choice") or "")
        choice = appearance.resolve_choice(cid) if cid else None
        if choice:
            return choice
    sources = set()
    for obj in meshes:
        path = str(obj.get("arc_source_mesh") or obj.get("arc_psk_path") or "").replace("\\", "/")
        marker = "/content/pioneer/"
        pos = path.casefold().rfind(marker)
        if pos >= 0:
            sources.add(os.path.splitext(path[pos + len(marker):])[0].casefold())
    for choice in appearance.list_gear_choices("backpack"):
        rel = str(choice.get("rel_path") or "").replace("\\", "/")
        if os.path.splitext(rel)[0].casefold() in sources:
            return choice
    return None


def refresh_reference_cards(context=None, *, roles=None, add_missing=True, preserve_calibration=True) -> dict:
    """Explicitly repair the current scene's cards; never called on registration.

    Preserve the user's measured Toolbox/Moustache/Mouse visible placement,
    update other cards to those defaults, and add missing backpack cards from
    their stamped exact catalog identity. Original model geometry is untouched.
    """
    from . import appearance, import_layout
    ctx = context or bpy.context
    scene = ctx.scene
    wanted = {import_layout.normalize_role_name(role) for role in (roles or _REF_TYPE_COLLECTIONS)}
    bind_legacy_reference_planes(scene, roles=wanted)
    _depsgraph_update()
    groups = {}
    for obj in scene.objects:
        role = import_layout.infer_role(obj)
        if obj.type != "MESH" or role not in wanted:
            continue
        slot = import_layout.slot_of_object(obj)
        groups.setdefault((role, slot), []).append(obj)
    model_bounds = {key: _world_bbox(meshes, update=False) for key, meshes in groups.items()}
    report = {"updated": [], "added": [], "missing": []}
    cards = [o for o in scene.objects if o.get("arc_reference_plane")]
    covered = set()
    for plane in cards:
        role = _reference_role(plane)
        if role not in wanted:
            continue
        key = (role, import_layout.slot_of_object(plane))
        meshes = groups.get(key, [])
        if not meshes:
            continue
        if _update_card(plane, meshes, role, preserve_calibration=preserve_calibration,
                        model_bbox=model_bounds[key]):
            report["updated"].append(plane.name)
            covered.add(key)
    if add_missing:
        for key, meshes in groups.items():
            role, slot = key
            if key in covered or role != "Backpack":
                continue
            choice = _backpack_choice_for_meshes(meshes)
            cid = str((choice or {}).get("id") or "")
            path = ensure_choice_icon(choice, scene) if choice else ""
            plane = place_asset_reference(meshes, path, role) if path else None
            if plane is None:
                report["missing"].append({"role": role, "slot": slot, "choice": cid})
                continue
            report["added"].append(plane.name)
    categorize_reference_planes(scene, roles=wanted)
    _depsgraph_update()
    return report


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
