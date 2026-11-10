"""Cameras that match the game's scenario cameras.

Kinds
  outfit_icon  Generalised from the calibrated outfit card blends (anchor_sweep/<OUTFIT>/hh): square frame,
               61.5 mm on a 24 mm vertical filmback, 20 deg to the character's left, level, frame from just
               below the knees (0.50 m) to the top of the head (1.90 m).
  main_menu    The main menu customization camera relative to the player: CustomizationCameraOutfit, 35 mm on a
               23.76 x 13.365 mm filmback (37.5 deg horizontal), 16:9, pitch -5 deg, 3.21 m in front, 0.39 m to
               the character's left, 1.29 m above the feet.
  gun_inspect  The in-game realtime weapon scene (Void.Item stage): level side view 169 cm from the weapon,
               23.76 x 13.365 mm filmback, focal length from the ItemSizeToFocalLength curve of the weapon length.
  weapon_icon  The BigIcon capture camera of DA_UIMetaData_<weapon>: 40 deg horizontal FOV, 1024 x 512; created
               by the Weapon Icon gun position, parented to the weapon root.
"""
from __future__ import annotations

import math

import bpy
from mathutils import Matrix, Vector

PROP = "arc_scenario_camera"

OUTFIT_ICON = dict(lens=61.5, sensor_height=24.0, yaw_deg=20.0, frame_z=(0.50, 1.90), res=(1024, 1024))
MAIN_MENU = dict(lens=35.0, sensor_width=23.76, sensor_height=13.365, res=(1920, 1080),
                 loc=(0.389, -3.2065, 1.2901), yaw_dir=(-0.10495, 0.99448), pitch_deg=-5.0)
GUN_INSPECT = dict(sensor_width=23.76, sensor_height=13.365, res=(1920, 1080),
                   offset=(-0.00884, 0.0741, 1.69),            # right, up, back from the weapon centre (m)
                   size_to_focal=((10.0, 80.0), (100.0, 30.0)))  # weapon length cm -> focal mm
WEAPON_ICON = dict(fov_deg=40.0, res=(1024, 512))


def enabled(scene) -> bool:
    return bool(getattr(scene, "arc_lighting_import_camera", True))


def _look(loc: Vector, fwd: Vector) -> Matrix:
    f = fwd.normalized()
    r = f.cross(Vector((0.0, 0.0, 1.0))).normalized()
    u = r.cross(f)
    m = Matrix((r, u, -f)).transposed().to_4x4()
    m.translation = loc
    return m


def _camera(scene, kind: str, label: str, matrix: Matrix, *, res, parent=None, sensor_fit="HORIZONTAL",
            sensor_width=36.0, sensor_height=24.0, lens=None, angle=None):
    ob = next((o for o in scene.objects if o.type == "CAMERA" and o.get(PROP) == kind and o.parent == parent), None)
    if ob is None:
        name = "Arc Camera: " + label
        ob = bpy.data.objects.new(name, bpy.data.cameras.new(name))
        scene.collection.objects.link(ob)
        ob[PROP] = kind
    cd = ob.data
    cd.type = "PERSP"
    cd.sensor_fit = sensor_fit
    cd.sensor_width, cd.sensor_height = sensor_width, sensor_height
    cd.shift_x = cd.shift_y = 0.0
    cd.clip_start = 0.01
    if angle is not None:
        cd.angle = angle
    else:
        cd.lens = lens
    if parent is not None:
        ob.parent = parent
        ob.matrix_parent_inverse = Matrix.Identity(4)
        ob.matrix_basis = matrix
    else:
        ob.matrix_world = matrix
    scene.camera = ob
    r = scene.render
    r.resolution_x, r.resolution_y = res
    r.pixel_aspect_x = r.pixel_aspect_y = 1.0
    return ob


def _character(context):
    """World location and yaw of the character the camera is placed against (origin when there is none)."""
    arm = None
    try:
        from ..Animations import animation_import as aimp
        arm = aimp.resolve_target_armature(context)
    except Exception:
        arm = None
    if arm is None or arm.get("arc_weapon_mock"):
        scene = context.scene
        found = [o for o in scene.objects if o.type == "ARMATURE" and not o.get("arc_weapon_mock")
                 and o.data.bones.get("pelvis") is not None]
        arm = found[0] if len(found) == 1 else None
    if arm is None:
        return Vector((0.0, 0.0, 0.0)), 0.0, ""
    mw = arm.matrix_world
    return mw.translation.copy(), mw.to_euler("XYZ").z, arm.name


def outfit_icon(scene, context=None):
    s = OUTFIT_ICON
    base, _yaw, who = _character(context or bpy.context)
    lo, hi = s["frame_z"]
    dist = (hi - lo) / 2.0 / (s["sensor_height"] / 2.0 / s["lens"])
    yaw = math.radians(s["yaw_deg"])
    off = Vector((math.sin(yaw) * dist, -math.cos(yaw) * dist, (lo + hi) / 2.0))
    m = _look(base + off, Vector((-off.x, -off.y, 0.0)))
    _camera(scene, "outfit_icon", "Outfit Icon", m, res=s["res"], sensor_fit="VERTICAL",
            sensor_height=s["sensor_height"], lens=s["lens"])
    return "camera Outfit Icon" + (f" on {who}" if who else "")


def main_menu(scene, context=None):
    s = MAIN_MENU
    base, yaw, who = _character(context or bpy.context)
    rot = Matrix.Rotation(yaw, 3, "Z")
    pitch = math.radians(s["pitch_deg"])
    d = Vector((s["yaw_dir"][0], s["yaw_dir"][1], 0.0)).normalized() * math.cos(pitch)
    d.z = math.sin(pitch)
    m = _look(base + rot @ Vector(s["loc"]), rot @ d)
    _camera(scene, "main_menu", "Main Menu", m, res=s["res"], sensor_width=s["sensor_width"],
            sensor_height=s["sensor_height"], lens=s["lens"])
    return "camera Main Menu" + (f" on {who}" if who else "")


def _weapon_root(context):
    from ..Weapons import weapon_position
    roots = weapon_position.roots_for_selection(context)
    if roots:
        return roots[0]
    scene = context.scene
    found = [o for o in scene.objects if o.get("arc_weapon_assembly")]
    return found[-1] if found else None


def focal_for(size_cm: float) -> float:
    (t0, v0), (t1, v1) = GUN_INSPECT["size_to_focal"]
    u = min(1.0, max(0.0, (size_cm - t0) / (t1 - t0)))
    return v0 + (v1 - v0) * u * u * (3.0 - 2.0 * u)


def gun_inspect(scene, context=None, root=None):
    context = context or bpy.context
    root = root or _weapon_root(context)
    if root is None:
        return "camera skipped (import a weapon first)"
    frame = next((o for o in root.children if o.get("arc_weapon_position_anchor")), None) or root
    context.view_layer.update()
    dg = context.evaluated_depsgraph_get()
    inv = frame.matrix_world.inverted_safe()
    pts = []
    for o in root.children_recursive:
        if o.type == "MESH" and not o.hide_render:
            mw = o.evaluated_get(dg).matrix_world
            pts += [inv @ (mw @ Vector(c)) for c in o.bound_box]
    if not pts:
        return "camera skipped (weapon has no meshes)"
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    order = sorted(range(3), key=lambda k: -(hi - lo)[k])   # long, height, thin
    rw = frame.matrix_world.to_3x3()
    up, back = rw.col[order[1]].normalized(), rw.col[order[2]].normalized()
    if up.z < 0:
        up = -up
    if back.y > 0:        # view the side that faces the Blender front view
        back = -back
    right = up.cross(back).normalized()
    centre = frame.matrix_world @ ((lo + hi) / 2.0)
    size_cm = (hi - lo)[order[0]] * rw.col[order[0]].length * 100.0
    r, u, b = GUN_INSPECT["offset"]
    m = Matrix((right, up, back)).transposed().to_4x4()
    m.translation = centre + right * r + up * u + back * b
    s = GUN_INSPECT
    lens = focal_for(size_cm)
    _camera(scene, "gun_inspect", "Gun Inspect", m, res=s["res"], sensor_width=s["sensor_width"],
            sensor_height=s["sensor_height"], lens=lens)
    return f"camera Gun Inspect ({size_cm:.0f} cm, {lens:.0f} mm)"


def weapon_icon(scene, root, local_matrix: Matrix):
    s = WEAPON_ICON
    _camera(scene, "weapon_icon", "Weapon Icon", local_matrix, res=s["res"], parent=root,
            angle=math.radians(s["fov_deg"]))
    return "camera Weapon Icon"


_KINDS = {"outfit_icon": outfit_icon, "main_menu": main_menu, "gun_inspect": gun_inspect}


def create(scene, kind: str, context=None) -> str:
    fn = _KINDS.get(kind)
    return fn(scene, context) if fn else ""
