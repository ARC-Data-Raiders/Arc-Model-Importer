"""Display reference cards as native viewport overlays, preserving render planes."""
import bpy
import json
import math
import hashlib
import numpy as np
from mathutils import Matrix, Vector

_HELPER = "arc_reference_overlay_helper"
_HIDDEN = "_arc_reference_overlay_hidden_layers"
_IMAGE = "arc_reference_overlay_generated"
_records = {}
_enabled = False
_busy = False


def _layer_key(scene, layer):
    return scene.name + "|" + layer.name


def _restore_states(plane):
    try:
        return json.loads(str(plane.get(_HIDDEN, "{}")))
    except (TypeError, ValueError):
        return {}


def _quad(plane):
    mesh = plane.data
    if len(mesh.polygons) != 1 or len(mesh.polygons[0].loop_indices) != 4:
        return None
    indices = mesh.polygons[0].loop_indices
    vertices = [mesh.vertices[mesh.loops[i].vertex_index].co.copy() for i in indices]
    if mesh.uv_layers.active is None:
        return None
    uvs = [tuple(mesh.uv_layers.active.data[i].uv) for i in indices]
    return vertices, uvs


def _display_image(plane, quad):
    path = str(plane.get("arc_reference_image") or "")
    source = None
    if path:
        try:
            source = bpy.data.images.load(path, check_existing=True)
        except (RuntimeError, OSError):
            pass
    if source is None:
        # Saved reference materials can retain a packed image after their
        # original cache path has moved or become unavailable.
        pending = [m.node_tree for m in plane.data.materials if m and m.use_nodes]
        visited = set()
        while pending and source is None:
            tree = pending.pop()
            if tree.as_pointer() in visited:
                continue
            visited.add(tree.as_pointer())
            for n in tree.nodes:
                if n.type == "TEX_IMAGE" and n.image is not None and n.image.has_data:
                    source = n.image
                    break
                if n.type == "GROUP" and n.node_tree is not None:
                    pending.append(n.node_tree)
    if source is None:
        return None, ""
    width, height = source.size[:]
    if not width or not height:
        return None, ""
    uvs = quad[1]
    bounds = (min(u[0] for u in uvs), min(u[1] for u in uvs),
              max(u[0] for u in uvs), max(u[1] for u in uvs))
    angle = float(plane.get("arc_reference_artwork_rotation_deg", 0.0))
    identity = (path, source.name, width, height, bounds, angle)
    key = hashlib.sha256(repr(identity).encode()).hexdigest()[:20]
    existing = next((i for i in bpy.data.images if i.get(_IMAGE) == key), None)
    if existing is not None:
        return existing, key
    out_width = max(1, int(math.ceil((bounds[2] - bounds[0]) * width)))
    out_height = max(1, int(math.ceil((bounds[3] - bounds[1]) * height)))
    pixels = np.empty(width * height * 4, dtype=np.float32)
    source.pixels.foreach_get(pixels)
    pixels = pixels.reshape(height, width, 4)
    x = bounds[0] + (np.arange(out_width) + 0.5) * (bounds[2] - bounds[0]) / out_width
    y = bounds[1] + (np.arange(out_height) + 0.5) * (bounds[3] - bounds[1]) / out_height
    u, v = np.meshgrid(x, y)
    if abs(angle) > 1e-8:
        aspect = width / height
        px, py = (u - 0.5) * aspect, v - 0.5
        a = math.radians(angle)
        u = (math.cos(a) * px - math.sin(a) * py) / aspect + 0.5
        v = math.sin(a) * px + math.cos(a) * py + 0.5
    inside = (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)
    xx = np.clip((u * width).astype(np.int32), 0, width - 1)
    yy = np.clip((v * height).astype(np.int32), 0, height - 1)
    result = pixels[yy, xx].copy()
    result[~inside] = 0
    image = bpy.data.images.new("ARC Reference Overlay " + key, width=out_width,
                                height=out_height, alpha=True, float_buffer=False)
    image.colorspace_settings.name = source.colorspace_settings.name
    image.alpha_mode = "STRAIGHT"
    image.pixels.foreach_set(result.ravel())
    image.update()
    image[_IMAGE] = key
    image["arc_reference_original_image"] = path
    image.pack()
    return image, key


def _matrix(vertices, image, back=False):
    p0, p1, p2, p3 = vertices
    x = (p1 - p0) * 0.5
    y = (p3 - p0) * 0.5
    center = (p0 + p1 + p2 + p3) * 0.25
    width, height = image.size[:]
    aspect_x, aspect_y = ((1.0, height / width) if width >= height else (width / height, 1.0))
    x /= aspect_x
    y /= aspect_y
    if back:
        x = -x
    z = x.cross(y).normalized()
    matrix = Matrix.Identity(4)
    for axis, value in enumerate((x, y, z)):
        for component in range(3):
            matrix[component][axis] = value[component]
    matrix.translation = center
    return matrix


def _make_helper(plane, back):
    helper = bpy.data.objects.new(plane.name + (" [Viewport Back]" if back else " [Viewport Front]"), None)
    helper.empty_display_type = "IMAGE"
    helper.empty_display_size = 2.0
    helper.empty_image_offset = (-0.5, -0.5)
    helper.empty_image_depth = "DEFAULT"
    helper.empty_image_side = "FRONT"
    helper.show_empty_image_perspective = True
    helper.show_empty_image_orthographic = True
    helper.show_empty_image_only_axis_aligned = False
    helper.use_empty_image_alpha = True
    helper.color = (1, 1, 1, 1)
    helper.hide_render = True
    helper.lock_location = (True, True, True)
    helper.lock_rotation = (True, True, True)
    helper.lock_scale = (True, True, True)
    helper[_HELPER] = "BACK" if back else "FRONT"
    for collection in plane.users_collection:
        collection.objects.link(helper)
    helper.parent = plane
    helper.parent_type = "OBJECT"
    helper.matrix_parent_inverse = Matrix.Identity(4)
    return helper


def refresh(scene=None):
    """Reconcile cards and native helpers. Original geometry/materials stay intact."""
    global _busy
    if _busy or not hasattr(bpy.data, "objects"):
        return 0
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return 0
    _busy = True
    count = 0
    try:
        planes = [o for o in scene.objects if o.type == "MESH" and o.get("arc_reference_plane")]
        current = {o.as_pointer() for o in planes}
        for plane in planes:
            try:
                quad = _quad(plane)
                if quad is None:
                    continue
                pointer = plane.as_pointer()
                record = _records.get(pointer)
                signature = (plane.get("arc_reference_image"), plane.get("arc_reference_artwork_rotation_deg", 0),
                             tuple(tuple(v) for v in quad[0]), tuple(quad[1]))
                if record is None:
                    helpers = {o.get(_HELPER): o for o in plane.children if o.get(_HELPER)}
                    helpers = [helpers.get("FRONT") or _make_helper(plane, False),
                               helpers.get("BACK") or _make_helper(plane, True)]
                    record = _records[pointer] = {"plane": plane, "helpers": helpers, "signature": None, "hidden": {}}
                wanted_collections = set(plane.users_collection)
                for helper in record["helpers"]:
                    for collection in wanted_collections - set(helper.users_collection):
                        collection.objects.link(helper)
                    for collection in set(helper.users_collection) - wanted_collections:
                        collection.objects.unlink(helper)
                if record["signature"] != signature:
                    image, key = _display_image(plane, quad)
                    if image is None:
                        continue
                    for helper, back in zip(record["helpers"], (False, True)):
                        helper.data = image
                        helper.matrix_basis = _matrix(quad[0], image, back)
                    record["signature"] = signature
                hidden = _restore_states(plane)
                for layer in scene.view_layers:
                    key = _layer_key(scene, layer)
                    if key not in hidden:
                        hidden[key] = bool(plane.hide_get(view_layer=layer))
                        for helper in record["helpers"]:
                            helper.hide_set(hidden[key], view_layer=layer)
                    # The render plane is hidden only for viewport editing. Native
                    # helper visibility is independently user-editable; synchronize
                    # the front/back pair when either helper is explicitly hidden.
                    flags = tuple(h.hide_get(view_layer=layer) for h in record["helpers"])
                    previous = record["hidden"].get(key)
                    if previous is not None and flags != previous:
                        changed = next((flag for flag, old in zip(flags, previous) if flag != old), flags[0])
                        for helper in record["helpers"]:
                            helper.hide_set(changed, view_layer=layer)
                        flags = (changed, changed)
                        hidden[key] = changed
                    record["hidden"][key] = flags
                    plane.hide_set(True, view_layer=layer)
                serialized_hidden = json.dumps(hidden)
                if plane.get(_HIDDEN) != serialized_hidden:
                    plane[_HIDDEN] = serialized_hidden
                count += 1
            except (AttributeError, ReferenceError, RuntimeError, OSError, ValueError) as exc:
                print("ARC reference overlay card unavailable:", getattr(plane, "name", "?"), exc)
        # Deleted original objects release their orphan helpers. Scene switching
        # does not delete helpers owned by another scene.
        for pointer, record in list(_records.items()):
            try:
                record["plane"].name
            except ReferenceError:
                for helper in record["helpers"]:
                    try:
                        bpy.data.objects.remove(helper, do_unlink=True)
                    except (ReferenceError, RuntimeError):
                        pass
                del _records[pointer]
        return count
    finally:
        _busy = False


def ensure_enabled():
    register()
    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return False
    eligible = sum(1 for o in scene.objects if o.type == "MESH" and o.get("arc_reference_plane"))
    return refresh(scene) == eligible


def register():
    global _enabled
    _enabled = True
    # Cards refresh only when the user invokes the overlay operator.


def unregister():
    global _enabled
    _enabled = False
    if not hasattr(bpy.data, "objects") or not hasattr(bpy.data, "scenes"):
        _records.clear()
        return
    restored_planes = set()
    for scene in bpy.data.scenes:
        for plane in list(scene.objects):
            if plane.type != "MESH" or not plane.get("arc_reference_plane"):
                continue
            hidden = _restore_states(plane)
            for layer in scene.view_layers:
                key = _layer_key(scene, layer)
                if key in hidden:
                    plane.hide_set(bool(hidden[key]), view_layer=layer)
            restored_planes.add(plane)
    for plane in restored_planes:
        if _HIDDEN in plane:
            del plane[_HIDDEN]
    for obj in list(bpy.data.objects):
        if obj.get(_HELPER):
            bpy.data.objects.remove(obj, do_unlink=True)
    for image in list(bpy.data.images):
        if image.get(_IMAGE) and image.users == 0:
            bpy.data.images.remove(image)
    _records.clear()
