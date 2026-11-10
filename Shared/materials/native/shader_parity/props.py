"""Packed live properties and demand-driven parity handlers."""
import bpy
import numpy as np
from bpy.app.handlers import persistent
from . import plans, xform

HANDLER_CALLS = 0
_BUSY = False
_CAMERAS = {}


def _write(owner, key, value):
    if isinstance(value, (list, tuple, np.ndarray)):
        wanted = np.asarray(value, np.float32)
        old = owner.get(key)
        if old is not None:
            try:
                if np.array_equal(np.asarray(old, np.float32), wanted):
                    return
            except (ValueError, TypeError):
                pass
        owner[key] = wanted.tolist()
    elif owner.get(key) != value:
        owner[key] = value


def _world(obj):
    # matrix_world is only refreshed by depsgraph evaluation; rebuild it from the
    # always-current basis so handlers that run before evaluation see the new transform.
    matrix = obj.matrix_basis.copy()
    while obj.parent is not None:
        matrix = obj.matrix_parent_inverse @ matrix
        obj = obj.parent
        matrix = obj.matrix_basis @ matrix
    return matrix


def has_parity(obj):
    return obj.type == "MESH" and any(slot.material and slot.material.get("arc_mi_family") == "shader_parity" for slot in obj.material_slots)


def sync_camera(scene):
    camera = scene.camera
    if camera is None:
        position, forward = [0.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]
    else:
        position, forward = xform.camera_props(_world(camera))
    _write(scene, "dr_cam", position)
    _write(scene, "dr_camfwd", forward)
    _CAMERAS[scene.as_pointer()] = camera.as_pointer() if camera else 0


def ensure_scene(scene):
    defaults = plans.defaults()
    if defaults is not None:
        for name, value in defaults["params"].items():
            if name not in scene:
                _write(scene, name, float(np.float32(value)))
    sync_camera(scene)


def sync_transform(obj):
    l2w, w2l = xform.ue_affine(_world(obj))
    for r in range(3):
        _write(obj, "dr_l2w_" + str(r), l2w[r])
        _write(obj, "dr_w2l_" + str(r), w2l[r])


def ensure_mesh(mesh):
    uv = next((layer for layer in mesh.uv_layers if layer.active_render), None)
    name = uv.name if uv else ""
    key = str(len(mesh.loops)) + ":" + str(len(mesh.polygons)) + ":" + name
    if mesh.get("dr_tangent_sign_key") == key:
        return
    try:
        if uv is None:
            raise ValueError("missing_uv")
        mesh.calc_tangents(uvmap=name)
        # UE TangentZ.w equals Blender's bitangent_sign (99.5% of loops agree with the
        # NORMALS W stored in SM_OutpostModules_01_B; the rest are MikkTSpace degeneracies).
        values = [loop.bitangent_sign for loop in mesh.loops]
        mesh.free_tangents()
    except Exception:
        values = [1.0] * len(mesh.loops)
        key += ":fallback"
    attr = mesh.attributes.get("dr_tangent_sign")
    if attr is None:
        attr = mesh.attributes.new("dr_tangent_sign", "FLOAT", "CORNER")
    attr.data.foreach_set("value", values)
    _write(mesh, "dr_tangent_sign_key", key)


def object_plans(obj):
    result = []
    for slot in obj.material_slots:
        if slot.material and slot.material.get("arc_mi_family") == "shader_parity":
            plan = plans.for_material(slot.material)
            if plan is not None and plan not in result:
                result.append(plan)
    return result


def ensure_object(obj):
    if not has_parity(obj):
        return
    from . import build
    for i, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat is None or mat.get("arc_mi_family") != "shader_parity":
            continue
        uv1 = build.uv_name(obj, 1)
        if mat.get("dr_parity_uv1") == uv1:
            continue
        plan = plans.for_material(mat)
        if plan is None:
            raise ValueError("parity plan unavailable: " + mat.name)
        old_uv = mat.get("dr_parity_uv1", "")
        suffix = " [" + (old_uv or "UV0") + "]"
        base = mat.name[:-len(suffix)] if mat.name.endswith(suffix) else mat.name
        name = base + " [" + (uv1 or "UV0") + "]"
        variant = bpy.data.materials.get(name)
        if variant is None:
            variant = bpy.data.materials.new(name)
        build.setup_material(variant, plan, obj)
        if slot.link == "OBJECT":
            slot.material = variant
        else:
            obj.data.materials[i] = variant
    used = object_plans(obj)
    defaults = {}
    for plan in used:
        for row in plan.entry["cpd_slots"]:
            if row["index"] in defaults and defaults[row["index"]] != row["default"]:
                raise ValueError("conflicting CPD defaults")
            defaults[row["index"]] = row["default"]
    cpd_slots = [{"index": k, "default": v} for k, v in sorted(defaults.items())]
    for plan in used:
        for row in plan["params"]:
            name = row["attribute"]
            if row["attribute_type"] == "OBJECT" and name.startswith("dr_cpd_") and name not in obj:
                _write(obj, name, xform.cpd_vector(int(name.rsplit("_", 1)[-1]), cpd_slots))
    sync_transform(obj)
    _write(obj, "dr_parity", 1)
    ensure_mesh(obj.data)


def _remove(collection):
    for handler in list(collection):
        if getattr(handler, "__module__", "") == __name__:
            collection.remove(handler)


def activate():
    deactivate()


def deactivate():
    for collection in (bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.render_pre, bpy.app.handlers.frame_change_post):
        _remove(collection)


@persistent
def depsgraph_update_post(scene, depsgraph):
    global HANDLER_CALLS, _BUSY
    HANDLER_CALLS += 1
    if _BUSY:
        return
    _BUSY = True
    try:
        camera = scene.camera
        pointer = camera.as_pointer() if camera else 0
        if _CAMERAS.get(scene.as_pointer()) != pointer:
            sync_camera(scene)
        for update in tuple(depsgraph.updates):
            obj = update.id
            if not isinstance(obj, bpy.types.Object):
                continue
            # Updates carry evaluated copies; props must be read and written on the original.
            obj = obj.original
            if obj == camera:
                sync_camera(scene)
            if obj.type != "MESH":
                continue
            if obj.get("dr_parity"):
                if update.is_updated_transform:
                    sync_transform(obj)
                if update.is_updated_geometry:
                    ensure_mesh(obj.data)
            elif has_parity(obj):
                ensure_object(obj)
    finally:
        _BUSY = False


def _render_sync(scene):
    sync_camera(scene)
    for obj in scene.objects:
        if obj.get("dr_parity"):
            sync_transform(obj)


@persistent
def render_pre(scene, *args):
    _render_sync(scene)


@persistent
def frame_change_post(scene, *args):
    _render_sync(scene)


@persistent
def load_post(*args):
    if any(mat.get("arc_mi_family") == "shader_parity" for mat in bpy.data.materials):
        activate()
    else:
        deactivate()


def register():
    deactivate()
    _remove(bpy.app.handlers.load_post)


def unregister():
    deactivate()
    _remove(bpy.app.handlers.load_post)
    _CAMERAS.clear()
