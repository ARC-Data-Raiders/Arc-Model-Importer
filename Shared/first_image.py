"""First Image: the five steps from an empty scene to a rendered character.

1 Import an outfit, 2 choose a pose, 3 apply lighting, 4 frame the camera on the character,
5 render on the GPU (Cycles, 128 samples, denoised). Renders never fall back to the CPU.
"""
from __future__ import annotations

import math

import bpy
from mathutils import Matrix, Vector

CAMERA_KIND = "first_image"
CAMERA_LENS = 60.0
CAMERA_RES = (1080, 1440)
RENDER_SAMPLES = 128
_RENDERED = [""]          # scene name of the last started First Image render


def _ui_targets():
    try:
        from . import ui_targets
        return ui_targets
    except Exception:
        return None


def character_root(context):
    """(root object, display name) of the character the steps act on, or (None, "")."""
    targets = _ui_targets()
    if targets is None:
        return None, ""
    try:
        target = targets.resolve(context, "character")
    except Exception:
        return None, ""
    if target is None:
        return None, ""
    return target.obj, str(target.name or "")


def _pose_done(context):
    scene = context.scene
    if (getattr(scene, "arc_pose_preset", "APOSE") or "APOSE") != "APOSE":
        return True
    root, _name = character_root(context)
    targets = _ui_targets()
    try:
        arm = targets.target_armature(targets.resolve(context, "character")) if root is not None else None
    except Exception:
        arm = None
    if arm is None:
        return False
    return bool(arm.get("arc_applied_anim")) or (arm.get("arc_pose_preset") not in (None, "", "APOSE"))


def _camera_done(scene):
    cam = getattr(scene, "camera", None)
    try:
        from ..Lighting import scenario_cameras
        prop = scenario_cameras.PROP
    except Exception:
        prop = "arc_scenario_camera"
    return cam is not None and cam.get(prop) == CAMERA_KIND


def step_states(context):
    """[(step key, done)] in order; cheap enough for a memoised draw."""
    scene = context.scene
    targets = _ui_targets()
    try:
        has_character = bool(targets.scene_has_character(scene)) if targets is not None else False
    except Exception:
        has_character = False
    return [
        ("import", has_character),
        ("pose", has_character and _pose_done(context)),
        ("lighting", bool(getattr(scene, "arc_lighting_look_applied", "") or "")),
        ("camera", _camera_done(scene)),
        ("render", _RENDERED[0] == getattr(scene, "name", "")),
    ]


def _character_bounds(context, root):
    """World (min, max) of the character's rendered meshes, or None."""
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    objects = [root] + list(getattr(root, "children_recursive", ()) or ())
    points = []
    for obj in objects:
        if obj.type != "MESH" or obj.hide_render:
            continue
        matrix = obj.evaluated_get(depsgraph).matrix_world
        points += [matrix @ Vector(corner) for corner in obj.bound_box]
    if not points:
        return None
    lo = Vector([min(p[i] for p in points) for i in range(3)])
    hi = Vector([max(p[i] for p in points) for i in range(3)])
    return lo, hi


def frame_camera(context, root):
    """Point a portrait camera at the character's front (Blender -Y side); return the camera."""
    bounds = _character_bounds(context, root)
    if bounds is None:
        raise RuntimeError("The character has no visible meshes to frame")
    lo, hi = bounds
    centre = (lo + hi) / 2.0
    height = max(0.1, hi.z - lo.z)
    width = max(0.1, hi.x - lo.x)
    sensor_h = 24.0
    sensor_w = sensor_h * CAMERA_RES[0] / CAMERA_RES[1]
    half_v = math.atan(sensor_h / 2.0 / CAMERA_LENS)
    half_h = math.atan(sensor_w / 2.0 / CAMERA_LENS)
    margin = 1.1
    dist = max(height * margin / 2.0 / math.tan(half_v), width * margin / 2.0 / math.tan(half_h))
    dist += (hi.y - lo.y) / 2.0
    yaw = root.matrix_world.to_euler("XYZ").z
    back = Matrix.Rotation(yaw, 3, "Z") @ Vector((0.0, -1.0, 0.0))
    location = centre + back * dist
    forward = (centre - location).normalized()
    right = forward.cross(Vector((0.0, 0.0, 1.0))).normalized()
    up = right.cross(forward)
    matrix = Matrix((right, up, -forward)).transposed().to_4x4()
    matrix.translation = location
    scene = context.scene
    try:
        from ..Lighting import scenario_cameras
        cam = scenario_cameras._camera(scene, CAMERA_KIND, "First Image", matrix, res=CAMERA_RES,
                                       sensor_fit="VERTICAL", sensor_height=sensor_h, lens=CAMERA_LENS)
    except ImportError:
        cam = None
    if cam is None:
        raise RuntimeError("Camera tools are not available")
    return cam


def gpu_setup(scene):
    """Cycles on the GPU only: OptiX, else CUDA; CPU devices off. Returns device names; raises when none."""
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except Exception:
        raise RuntimeError("Cycles is not enabled in Blender")
    chosen = ""
    for backend in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = backend
        except Exception:
            continue
        try:
            prefs.refresh_devices()
        except Exception:
            prefs.get_devices()
        if any(d.type == backend for d in prefs.devices):
            chosen = backend
            break
    if not chosen:
        raise RuntimeError("No NVIDIA GPU found for Cycles (OptiX or CUDA); the image is not rendered on the CPU")
    names = []
    for device in prefs.devices:
        device.use = device.type == chosen
        if device.use:
            names.append(device.name)
    scene.render.engine = "CYCLES"
    scene.cycles.device = "GPU"
    scene.cycles.samples = RENDER_SAMPLES
    scene.cycles.use_denoising = True
    print("Arc Raiders: First Image render on %s: %s" % (chosen, ", ".join(names)))
    return names


class ARC_OUTFITS_OT_frame_camera_on_character(bpy.types.Operator):
    bl_idname = "arc_outfits.frame_camera_on_character"
    bl_label = "Frame Camera on Character"
    bl_description = "Place a portrait camera in front of the character so the whole body is in view"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        root, _name = character_root(context)
        if root is None:
            cls.poll_message_set("Import an outfit first")
            return False
        return True

    def execute(self, context):
        root, name = character_root(context)
        if root is None:
            self.report({"ERROR"}, "Import an outfit first")
            return {"CANCELLED"}
        try:
            frame_camera(context, root)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Camera framed on %s" % name)
        return {"FINISHED"}


class ARC_OUTFITS_OT_first_image_render(bpy.types.Operator):
    bl_idname = "arc_outfits.first_image_render"
    bl_label = "Render Image"
    bl_description = "Render the scene camera with Cycles on the GPU (128 samples, denoised)"

    @classmethod
    def poll(cls, context):
        if getattr(context.scene, "camera", None) is None:
            cls.poll_message_set("Frame the camera first")
            return False
        return True

    def execute(self, context):
        scene = context.scene
        try:
            gpu_setup(scene)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _RENDERED[0] = scene.name
        if bpy.app.background:
            bpy.ops.render.render(write_still=False)
        else:
            bpy.ops.render.render("INVOKE_DEFAULT")
        return {"FINISHED"}


CLASSES = (ARC_OUTFITS_OT_frame_camera_on_character, ARC_OUTFITS_OT_first_image_render)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
