"""Viewport setup before an outfit import: Cycles on GPU, Rendered shading, full-body free view.

The import builds materials part by part; a Rendered Cycles viewport shows each one arriving.
Nothing here touches the scene camera, and nothing runs in background mode.
"""
import math

import bpy
from mathutils import Euler, Vector

FULL_BODY_Z = (0.0, 1.9)     # metres above the import position
VIEW_YAW_DEG = 20.0          # same front three-quarter side as the outfit icon camera
VIEW_MARGIN = 1.15
GPU_KINDS = ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")


def ensure_cycles_gpu(context=None) -> str:
    """Set the scene to Cycles on a GPU device. Returns the device kind in use, or '' when none exists.

    Preferences are only written when no compute device is configured (a fresh profile);
    a profile that already has a GPU kind keeps its own device choice.
    """
    context = context or bpy.context
    scene = context.scene
    try:
        scene.render.engine = "CYCLES"
    except Exception as exc:
        print(f"Arc Asset Importer: Cycles is not available ({exc})")
        return ""
    addon = context.preferences.addons.get("cycles")
    prefs = getattr(addon, "preferences", None)
    if prefs is None:
        return ""
    current = getattr(prefs, "compute_device_type", "NONE")
    kinds = (current,) if current != "NONE" else GPU_KINDS
    for kind in kinds:
        try:
            if prefs.compute_device_type != kind:
                prefs.compute_device_type = kind
            if hasattr(prefs, "refresh_devices"):
                prefs.refresh_devices()
            else:
                prefs.get_devices()
            devices = [d for d in prefs.devices if d.type == kind]
        except Exception:
            continue
        if not devices:
            continue
        if current == "NONE" or not any(d.use for d in devices):
            for d in prefs.devices:
                d.use = d.type == kind
        scene.cycles.device = "GPU"
        return kind
    if current == "NONE":
        try:
            prefs.compute_device_type = "NONE"
        except Exception:
            pass
    print("Arc Asset Importer: no GPU render device found; the Cycles device is unchanged")
    return ""


def _view3d_spaces(context):
    for window in context.window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    yield space, region


def view_distance(lens: float, width: int, height: int, span: float) -> float:
    """Free-view distance at which a vertical span fills the region (viewport sensor is 72 mm on the long side)."""
    half = 36.0 / max(float(lens), 1.0)
    if width > height > 0:
        half *= height / float(width)
    return (span * 0.5) / max(half, 1e-4)


def frame_full_body(context=None, base=(0.0, 0.0, 0.0)) -> int:
    """Turn every 3D viewport to a front three-quarter view that frames a standing figure at *base*."""
    context = context or bpy.context
    lo, hi = FULL_BODY_Z
    target = Vector((base[0], base[1], base[2] + (lo + hi) * 0.5))
    rot = Euler((math.radians(90.0), 0.0, math.radians(VIEW_YAW_DEG)), "XYZ").to_quaternion()
    framed = 0
    for space, region in _view3d_spaces(context):
        rv3d = space.region_3d
        if rv3d is None:
            continue
        w, h = (region.width, region.height) if region is not None else (16, 9)
        try:
            rv3d.view_perspective = "PERSP"
            rv3d.view_rotation = rot
            rv3d.view_location = target
            rv3d.view_distance = view_distance(space.lens, w, h, (hi - lo) * VIEW_MARGIN)
            framed += 1
        except Exception:
            pass
    return framed


def set_rendered(context=None) -> int:
    context = context or bpy.context
    n = 0
    for space, _region in _view3d_spaces(context):
        try:
            if space.shading.type != "RENDERED":
                space.shading.type = "RENDERED"
            n += 1
        except Exception:
            pass
    return n


def next_outfit_base(scene) -> tuple:
    """World position the next imported outfit will stand on."""
    try:
        from ..Outfit_Accessories import import_layout
        x, y = import_layout.slot_world_xy(import_layout.allocate_slot("Outfit", scene=scene), scene)
        return (float(x), float(y), 0.0)
    except Exception:
        return (0.0, 0.0, 0.0)


def prepare_outfit_import(context=None, *, frame: bool = True) -> dict:
    """Run before an outfit import starts. Returns what was done (empty in background mode)."""
    context = context or bpy.context
    if bpy.app.background or getattr(context, "window_manager", None) is None or not context.window_manager.windows:
        return {}
    done = {"gpu": ensure_cycles_gpu(context), "rendered": set_rendered(context)}
    if frame:
        done["framed"] = frame_full_body(context, next_outfit_base(context.scene))
    return done
