"""Private Blender regression for sky activation and smooth LUT sampling."""
import importlib
import importlib.machinery
import importlib.util
import json
import math
import pathlib
import sys
import types

import bpy
import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = pathlib.Path(sys.argv[sys.argv.index("--") + 1])
OUT.mkdir(parents=True, exist_ok=True)
pkg = types.ModuleType("lighting_feedback")
source_root = pathlib.Path(sys.argv[sys.argv.index("--source-root") + 1]) if "--source-root" in sys.argv else REPO
pkg.__path__ = [str(source_root), str(REPO)]
sys.modules[pkg.__name__] = pkg
post = importlib.import_module(pkg.__name__ + ".Lighting.lighting_post")
grade = importlib.import_module(pkg.__name__ + ".Lighting.lighting_grade")
looks = importlib.import_module(pkg.__name__ + ".Lighting.lighting_looks")
report = {}
sky = importlib.import_module(pkg.__name__ + ".Lighting.lighting_sky")
# draw_sky_controls moved from Lighting/lighting_sky.py to sidebar.py (commit 5f70ddb).
draw_sky_controls = getattr(sky, "draw_sky_controls", None) or importlib.import_module(pkg.__name__ + ".sidebar").draw_sky_controls

scene = bpy.context.scene
for window in bpy.context.window_manager.windows:
    for area in window.screen.areas:
        if area.type != "VIEW_3D":
            continue
        shading = area.spaces.active.shading
        shading.type = "SOLID"
        shading.use_scene_world = False
        shading.use_scene_world_render = False
        shading.use_scene_lights = False
        shading.use_scene_lights_render = False
        shading.studiolight_background_alpha = 0.0
looks._enable_scene_world()
views = [area.spaces.active.shading for window in bpy.context.window_manager.windows
         for area in window.screen.areas if area.type == "VIEW_3D"]
report["viewport_world"] = {"ok": bool(views) and all(
    shading.type in {"MATERIAL", "RENDERED"} and shading.use_scene_world
    and shading.use_scene_world_render and shading.use_scene_lights
    and shading.use_scene_lights_render and shading.studiolight_background_alpha == 1.0
    for shading in views)}

scene.render.engine = "CYCLES"
# Global rule: Cycles renders run on GPU only (OptiX first, then CUDA), never CPU.
cycles_prefs = bpy.context.preferences.addons["cycles"].preferences
gpu_devices = []
for backend in ("OPTIX", "CUDA"):
    try:
        cycles_prefs.compute_device_type = backend
    except TypeError:
        continue
    cycles_prefs.refresh_devices()
    gpu_devices = [d for d in cycles_prefs.devices if d.type == backend]
    if gpu_devices:
        break
if not gpu_devices:
    raise SystemExit("No OptiX/CUDA GPU device found; refusing CPU render")
for device in cycles_prefs.devices:
    device.use = device in gpu_devices
scene.cycles.device = "GPU"
print("CYCLES_GPU", cycles_prefs.compute_device_type, [d.name for d in gpu_devices])
scene.cycles.samples = 1
scene.cycles.use_denoising = False
scene.render.resolution_x = 2048
scene.render.resolution_y = 8
scene.render.resolution_percentage = 100
camera = bpy.data.objects.new("RegressionCamera", bpy.data.cameras.new("RegressionCamera"))
scene.collection.objects.link(camera)
scene.camera = camera
scene.render.image_settings.file_format = "OPEN_EXR"
scene.render.image_settings.color_depth = "32"
scene.render.use_compositing = True
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.view_settings.exposure = 0.0
scene.view_settings.gamma = 1.0
scene.render.dither_intensity = 0.0
ramp = np.ones((8, 2048, 4), dtype=np.float32)
values = np.linspace(0.05, 1.5, 2048, dtype=np.float32)
ramp[..., :3] = values[None, :, None] * np.array([0.55, 0.75, 1.0], dtype=np.float32)
image = bpy.data.images.new("Sky radiance ramp", width=2048, height=8, float_buffer=True)
image.colorspace_settings.name = "Non-Color"
image.pixels.foreach_set(ramp.ravel())
image.update()
settings = grade.blend_volumes([])
lut = grade.combined_lut(settings)
scene.render.compositor_precision = "AUTO"
post.configure(scene, settings, lut=lut, lut_identity="feedback-ramp", auto_exposure=False,
               bloom=False, vignette=False, local_exposure=False)
post.configure(scene, settings, lut=lut, lut_identity="feedback-ramp", auto_exposure=False,
               bloom=False, vignette=False, local_exposure=False)
report["preview_precision"] = {"ok": scene.render.compositor_precision == "FULL"}
post.detach(scene)
report["precision_restored"] = {"ok": scene.render.compositor_precision == "AUTO"}
lut_image = post.ensure_lut_image(lut, "feedback-ramp")
lut_group = post.ensure_lut_group(lut_image)
tree = bpy.data.node_groups.new("FeedbackRamp", "CompositorNodeTree")
tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
source = tree.nodes.new("CompositorNodeImage")
source.image = image
sampler = tree.nodes.new("CompositorNodeGroup")
sampler.node_tree = lut_group
output = tree.nodes.new("NodeGroupOutput")
tree.links.new(source.outputs["Image"], sampler.inputs["Image"])
tree.links.new(sampler.outputs["Image"], output.inputs["Image"])
scene.compositing_node_group = tree
expected = grade.srgb_to_linear(np.clip(grade.tonemap(ramp[..., :3], lut), 0.0, 1.0))
for device, precision in (("CPU", "FULL"), ("GPU", "AUTO"), ("GPU", "FULL")):
    scene.render.compositor_device = device
    scene.render.compositor_precision = precision
    scene.render.filepath = str(OUT / ("ramp_" + device + "_" + precision + ".exr"))
    bpy.ops.render.render(write_still=True)
    result = bpy.data.images.load(scene.render.filepath, check_existing=False)
    pixels = np.empty(len(result.pixels), dtype=np.float32)
    result.pixels.foreach_get(pixels)
    actual = pixels.reshape(ramp.shape)[..., :3]
    error = float(np.max(np.abs(actual - expected)))
    unique = int(np.unique(actual[4, :, 2]).size)
    report["ramp_" + device + "_" + precision] = {"ok": error < (0.0001 if device == "CPU" else 0.0005) and unique > 2000,
                                  "max_error": error, "unique_blue": unique}
    bpy.data.images.remove(result)
scene.render.compositor_device = "CPU"
scene.render.compositor_precision = "FULL"
coordinates = np.clip(np.log2(ramp[..., :3] + grade.LOG_EPS) * grade.LOG_SCALE + grade.LOG_OFFSET, 0, 1) * 31
for label, delta in (("lower", 0), ("upper", 1)):
    uv = np.ones((8, 2048, 4), dtype=np.float32)
    slice_index = np.minimum(np.floor(coordinates[..., 2]) + delta, 31)
    uv[..., 0] = (slice_index * 32 + coordinates[..., 0] + 0.5) / 1024
    uv[..., 1] = (coordinates[..., 1] + 0.5) / 32
    uv = uv.astype(np.float16).astype(np.float32)
    uv_image = bpy.data.images.new("Half precision UV " + label, width=2048, height=8, float_buffer=True)
    uv_image.colorspace_settings.name = "Non-Color"
    uv_image.pixels.foreach_set(uv.ravel())
    uv_image.update()
    uv_source = lut_group.nodes.new("CompositorNodeImage")
    uv_source.image = uv_image
    lut_group.links.new(uv_source.outputs["Image"], lut_group.nodes["Bilinear " + label + " slice"].inputs["UV"])
scene.render.filepath = str(OUT / "ramp_half_uv.exr")
bpy.ops.render.render(write_still=True)
result = bpy.data.images.load(scene.render.filepath, check_existing=False)
pixels = np.empty(len(result.pixels), dtype=np.float32)
result.pixels.foreach_get(pixels)
actual = pixels.reshape(ramp.shape)[..., :3]
error = float(np.max(np.abs(actual - expected)))
steps = np.abs(np.diff(actual[4, :, 0]))
expected_steps = np.abs(np.diff(expected[4, :, 0]))
report["half_uv_reproduction"] = {"ok": error > 0.005,
    "max_error": error, "max_red_step": float(steps.max()),
    "reference_max_red_step": float(expected_steps.max())}
scene.render.use_compositing = False
scene.render.resolution_x, scene.render.resolution_y = 128, 64
for obj in scene.objects:
    obj.hide_render = obj != camera
camera.rotation_euler = (math.radians(65), 0.0, math.radians(20))
catalog = json.loads((REPO / "assets/lighting_scenarios.json").read_text(encoding="utf-8"))
entry = next(entry for map_entry in catalog["maps"].values()
             for entry in map_entry["scenarios"].values()
             if entry.get("sky_material") and entry.get("sky_sphere") and entry.get("sun"))

def render_sky(name):
    scene.render.filepath = str(OUT / (name + ".exr"))
    bpy.ops.render.render(write_still=True)
    rendered = bpy.data.images.load(scene.render.filepath, check_existing=False)
    values = np.empty(len(rendered.pixels), dtype=np.float32)
    rendered.pixels.foreach_get(values)
    bpy.data.images.remove(rendered)
    return values.reshape(64, 128, 4)

baseline_path = OUT.parent / "lighting_sky.py.before"
baseline_pixels = None
if baseline_path.exists():
    loader = importlib.machinery.SourceFileLoader("sky_before", str(baseline_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    before = importlib.util.module_from_spec(spec)
    loader.exec_module(before)
    before.ensure_scenario_sky(scene, entry, mirror_y=True)
    baseline_pixels = render_sky("sky_before")
scene.render.film_transparent = True
sky.ensure_scenario_sky(scene, entry, mirror_y=True)
report["opaque_sky"] = {"ok": not scene.render.film_transparent}
scene.render.film_transparent = False
controls = []

class Layout:
    def prop(self, owner, prop, **kwargs):
        controls.append(kwargs.get("text"))

draw_sky_controls(Layout(), scene)
report["sky_controls"] = {"ok": controls == ["Rotation", "Extra Contrast", "Overall Intensity", "Saturation"],
                          "controls": list(controls)}
actual_sky = render_sky("sky_after")
if baseline_pixels is not None:
    relative = np.abs(actual_sky[..., :3] - baseline_pixels[..., :3]) / np.maximum(np.abs(baseline_pixels[..., :3]), 1e-4)
    report["sky_defaults_parity"] = {"ok": float(np.max(relative)) < 0.0001,
                                     "max_relative_error": float(np.max(relative))}
nodes = scene.world.node_tree.nodes
intensity = nodes.get("Overall Intensity")
if intensity is not None:
    original = intensity.outputs[0].default_value
    intensity.outputs[0].default_value = 0.0
    dark = render_sky("sky_zero_intensity")
    report["sky_intensity_effect"] = {"ok": float(dark[..., :3].max()) == 0.0 and float(actual_sky[..., :3].max()) > 0.0}
    intensity.outputs[0].default_value = original
rotation = nodes.get("ArcSkyRotation")
if rotation is not None:
    rotation.inputs["Angle"].default_value = math.pi / 2
    rotated = render_sky("sky_rotated")
    report["sky_rotation_effect"] = {"ok": not np.allclose(actual_sky, rotated, rtol=0.001, atol=0.0001)}
retained = OUT.parents[2] / "lighting-look/blender/uds_check.py"
if retained.exists():
    spec = importlib.util.spec_from_file_location("retained_uds", retained)
    uds = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(uds)
    entry = catalog["maps"]["BuriedCity_01"]["scenarios"]["Lighting.BuriedDay"]
    cloud = bpy.data.images.new("Regression cloud", width=64, height=32, float_buffer=True)
    cloud.colorspace_settings.name = "Non-Color"
    cloud_pixels = np.ones((32, 64, 4), dtype=np.float32)
    cloud_pixels[..., :3] = np.linspace(0.2, 0.8, 64, dtype=np.float32)[None, :, None]
    cloud.pixels.foreach_set(cloud_pixels.ravel())
    cloud.update()
    sky.ensure_scenario_sky(scene, entry, mirror_y=True, cloud_image=cloud)

    def check(name, ok, **info):
        report[name] = {"ok": bool(ok), **info}

    def render_retained(filename, compositing):
        scene.render.use_compositing = compositing
        scene.render.filepath = filename
        bpy.ops.render.render(write_still=True)
        rendered = bpy.data.images.load(filename, check_existing=False)
        width, height = rendered.size
        values = np.empty(len(rendered.pixels), dtype=np.float32)
        rendered.pixels.foreach_get(values)
        bpy.data.images.remove(rendered)
        return values.reshape(height, width, 4)

    uds.run(check, scene, entry, True, sky, str(OUT / "retained_uds"), render_retained)
cat = looks.cat
original_fetch = cat.ensure_textures
original_find = cat.find_texture

def forbidden(*args, **kwargs):
    raise AssertionError("Browsing must not access game textures")

cat.ensure_textures = forbidden
cat.find_texture = forbidden
try:
    lut_items = looks.make_lighting_look_items(scene, bpy.context)
    sky_items = looks.make_lighting_hdri_items(scene, bpy.context)
    draw_sky_controls(Layout(), scene)
    report["package_only_browsing"] = {"ok": bool(lut_items) and bool(sky_items)}
finally:
    cat.ensure_textures = original_fetch
    cat.find_texture = original_find
looks.clear_lighting_look(scene)
report["film_restored"] = {"ok": scene.render.film_transparent}
(OUT / "results.json").write_text(json.dumps(report, indent=2))
print("LIGHTING_FEEDBACK", json.dumps(report))
assert all(item["ok"] for item in report.values()), report
