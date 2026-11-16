"""Blender background check for the Outfit Inspect/Customization light rig.

Run: blender --background --factory-startup --python tests/test_inspect_lighting.py -- <out_dir> [--render]
Builds a stand-in character (armature with a pelvis bone + 1.8 m mesh), applies the rig through
studio_lights.import_rig, prints every light, and checks the rig frame against the Main Menu camera.
"""
import importlib
import json
import math
import pathlib
import sys
import types

import bpy
from mathutils import Matrix, Vector

REPO = pathlib.Path(__file__).resolve().parents[1]
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = pathlib.Path(ARGS[0]) if ARGS else REPO / ".local" / "inspect_lighting"
OUT.mkdir(parents=True, exist_ok=True)
pkg = types.ModuleType("inspect_lighting_pkg")
pkg.__path__ = [str(REPO)]
sys.modules[pkg.__name__] = pkg
inspect_lights = importlib.import_module(pkg.__name__ + ".Lighting.inspect_lights")
studio = importlib.import_module(pkg.__name__ + ".Lighting.studio_lights")
cams = importlib.import_module(pkg.__name__ + ".Lighting.scenario_cameras")

failures = []


def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok:
        failures.append(msg)


scene = bpy.context.scene
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)

# Stand-in character.
arm_data = bpy.data.armatures.new("StandIn")
arm = bpy.data.objects.new("StandIn", arm_data)
scene.collection.objects.link(arm)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="EDIT")
b = arm_data.edit_bones.new("pelvis")
b.head, b.tail = (0, 0, 0.95), (0, 0, 1.05)
bpy.ops.object.mode_set(mode="OBJECT")
bpy.ops.mesh.primitive_cylinder_add(radius=0.25, depth=1.8, location=(0, 0, 0.9))
body = bpy.context.active_object
body.name = "StandInBody"
body.parent = arm

# 1. Frame: the cooked outfit camera mapped by the rig transform must land on scenario_cameras.MAIN_MENU.
cam_m = inspect_lights.local_matrix((281.61877, 158.2594, 129.01427), (-5.0, -149.77165, 0.0))
loc_err = (cam_m.translation - Vector(cams.MAIN_MENU["loc"])).length
fwd = cam_m.to_3x3() @ Vector((0, 0, -1))
yaw_dir = Vector((fwd.x, fwd.y)).normalized()
dir_err = (yaw_dir - Vector(cams.MAIN_MENU["yaw_dir"])).length
pitch = math.degrees(math.asin(max(-1.0, min(1.0, fwd.z))))
check(loc_err < 0.005, f"cooked camera -> MAIN_MENU loc err {loc_err * 1000:.2f} mm")
check(dir_err < 0.002, f"cooked camera -> MAIN_MENU yaw_dir err {dir_err:.5f}")
check(abs(pitch - cams.MAIN_MENU["pitch_deg"]) < 0.05, f"camera pitch {pitch:.3f}")

# 2. Rig registered and applied through the studio rig path.
check(inspect_lights.RIG_ID in studio.rigs(), "outfit_inspect listed in studio_lights.rigs()")
rows = []
for yaw_deg in (0.0, 30.0):
    arm.rotation_euler = (0.0, 0.0, math.radians(yaw_deg))
    arm.location = (1.0, 2.0, 0.0) if yaw_deg else (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    ok, msg = studio.import_rig(scene, inspect_lights.RIG_ID, camera=True, context=bpy.context)
    print(msg)
    check(ok, f"import_rig ok at yaw {yaw_deg}")
    coll = bpy.data.collections.get(inspect_lights.COLLECTION)
    lights = sorted((o for o in coll.objects if o.type == "LIGHT"), key=lambda o: o.name)
    check(len(lights) == 9, f"9 lights at yaw {yaw_deg} (got {len(lights)})")
    inv = arm.matrix_world.inverted()
    rel = {o.name: (inv @ o.matrix_world) for o in lights}
    if yaw_deg == 0.0:
        base_rel = rel
        for o in lights:
            m = o.matrix_world
            e = m.to_euler("XYZ")
            d = o.data
            rows.append(dict(name=o.name, type=d.type, loc=[round(v, 4) for v in m.translation],
                             rot_deg=[round(math.degrees(v), 2) for v in e], energy_w=round(d.energy, 2),
                             temperature=d.temperature if getattr(d, "use_temperature", False) else None,
                             color=[round(v, 3) for v in d.color], size_m=round(d.shadow_soft_size, 3),
                             spot_deg=round(math.degrees(d.spot_size), 3), blend=round(d.spot_blend, 3),
                             shadow=d.use_shadow, aim=[round(v, 3) for v in (m.to_3x3() @ Vector((0, 0, -1)))]))
    else:
        worst = max(max(abs(a - b) for ra, rb in zip(rel[n], base_rel[n]) for a, b in zip(ra, rb)) for n in rel)
        check(worst < 1e-4, f"rig follows character yaw/location (max matrix delta {worst:.2e})")
    cam = next((o for o in scene.objects if o.type == "CAMERA" and o.get(cams.PROP) == "main_menu"), None)
    check(cam is not None, f"Main Menu camera present at yaw {yaw_deg}")

for r in rows:
    print("LIGHT", json.dumps(r))

# 3. Key light faces the character: every spot's cone axis passes near the body.
arm.rotation_euler = (0, 0, 0)
arm.location = (0, 0, 0)
bpy.context.view_layer.update()
studio.import_rig(scene, inspect_lights.RIG_ID, camera=True, context=bpy.context)
for o in bpy.data.collections[inspect_lights.COLLECTION].objects:
    p = o.matrix_world.translation
    a = (o.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
    # closest approach of the cone axis to the body centre line (x=y=0, 0<z<1.8)
    best = min(((p + a * t) - Vector((0, 0, min(1.8, max(0.0, (p + a * t).z))))).length
               for t in [i * 0.02 for i in range(300)])
    check(best < 0.6, f"{o.name} axis passes {best:.3f} m from the body")

(OUT / "lights.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")

if "--render" in ARGS:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    try:
        prefs.compute_device_type = "OPTIX"
    except TypeError:
        prefs.compute_device_type = "CUDA"
    prefs.refresh_devices()
    gpus = [d for d in prefs.devices if d.type == prefs.compute_device_type]
    for d in prefs.devices:
        d.use = d in gpus
    if not gpus:
        print("FAIL no GPU device")
        sys.exit(2)
    print("DEVICES", prefs.compute_device_type, [d.name for d in gpus])
    scene.render.engine = "CYCLES"
    scene.cycles.device = "GPU"
    scene.cycles.samples = 16
    scene.render.resolution_x, scene.render.resolution_y = 640, 360
    scene.camera = cam
    scene.render.filepath = str(OUT / "inspect_render.png")
    bpy.ops.render.render(write_still=True)
    print("RENDER", scene.render.filepath)

print("RESULT", "FAIL" if failures else "PASS", len(failures))
sys.exit(1 if failures else 0)
