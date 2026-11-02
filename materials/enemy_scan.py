"""Enemy scan display: SM5 Loop material bytecode reconstruction.

Frozen Trail ScanDisplay has no sampled textures. Tiling/ScanSpeed/Loop and
AlertnessState are authored parameters. Circular cells, palette and Loop sweep
are recovered from B46B1692.../4131_SF_Pixel_3ADB4E4D... (current PAK).
Distance-based LED expansion/fill follows linear view depth in centimetres.
Selection highlighting and glitch/damage passes are not implemented.
"""
from __future__ import annotations

import bpy

from .common import _parse_flat_mi_json


STATE_ITEMS = (
    ("SCANNING", "Idle (0.0)", "AlertnessState 0.0: the shaders' blue window (eye display and alertness emissive)"),
    ("ALERT", "Alert (0.5)", "AlertnessState 0.5: the shaders' orange window"),
    ("AGGRO", "Combat (1.0)", "AlertnessState 1.0: the shaders' red window; the Loop display lights all LEDs"),
)
STATE_VALUES = {"SCANNING": 0.0, "ALERT": 0.5, "AGGRO": 1.0}
_PALETTES = {
    "SCANNING": ((0, 0.325897, 1, 1), (0, 0.325897, 1, 1), 0.0),
    "ALERT": ((1, 0.406525, 0.006859, 1), (1, 0.406525, 0.006859, 1), 0.5),
    "AGGRO": ((1, 0, 0, 1), (1, 0, 0, 1), 1.0),
}


def is_scan_material(mat):
    return bool(mat and (mat.get("arc_scan_display") or
                        mat.get("arc_mi_family") == "scan_display" or
                        "scandisplay" in mat.name.lower().replace("_", "")))


def set_scan_state(mat, state):
    """Set new and legacy scan graphs without replacing unrelated materials."""
    from ..material_runtime.native_families import is_user_override
    if mat and is_user_override(mat):
        return True
    if mat and mat.get('dr_native_family') == 'display':
        from ..material_runtime.native_families import set_scan_state as set_native_scan_state
        state = state if state in ('SCANNING', 'ALERT', 'AGGRO') else 'SCANNING'
        return set_native_scan_state(mat, state)
    if not is_scan_material(mat) or not mat.use_nodes:
        return False
    state = state if state in _PALETTES else "SCANNING"
    low, high, alert = _PALETTES[state]
    # Upgrade previous square-LED graphs on first interaction.
    if int(mat.get("arc_scan_reconstruction_version", 0)) < 3:
        setup_enemy_scan_display_material(mat, str(mat.get("arc_mi_path", "")), state=state)
        return True
    nodes = mat.node_tree.nodes
    nodes["ScanDimColor"].outputs[0].default_value = low
    nodes["ScanHotColor"].outputs[0].default_value = high
    nodes["ScanAlertPattern"].outputs[0].default_value = 1 if state == "ALERT" else 0
    nodes["ScanAggroPattern"].outputs[0].default_value = 1 if state == "AGGRO" else 0
    mat["arc_scan_state"] = state
    mat["arc_alertness_state"] = alert
    mat.diffuse_color = high
    return True


def setup_enemy_scan_display_material(mat, mi_path, *, state=None, obj=None):
    """Build opaque regular round LEDs with the recovered state-dependent sweep.

    Without an explicit state the native graph reads the owning object's
    AlertnessState property, so the state follows the ARC, not the material.
    """
    from ..material_runtime.native_families import setup_entrypoint
    if setup_entrypoint(mat, mi_path, obj=obj, expected_family='display', cache_family='scan_display', state=state):
        return
    mi = _parse_flat_mi_json(mi_path) if mi_path else {}
    scalars = mi.get("scalars") or {}
    switches = mi.get("switches") or {}
    def scalar(name, default):
        try:
            return float(scalars.get(name, default))
        except (TypeError, ValueError):
            return default
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    counter = [0]
    def new(kind, name):
        n = nodes.new(kind)
        n.name = n.label = name
        i = counter[0]
        counter[0] += 1
        n.location = ((i % 7) * 205 - 1300, -(i // 7) * 220)
        return n
    def connect(value, sock):
        if isinstance(value, (int, float)):
            sock.default_value = value
        else:
            links.new(value, sock)
    def math(op, name, a, b=None):
        n = new("ShaderNodeMath", name)
        n.operation = op
        connect(a, n.inputs[0])
        if b is not None:
            connect(b, n.inputs[1])
        return n.outputs[0]
    def value(name, number):
        n = new("ShaderNodeValue", name)
        n.outputs[0].default_value = number
        return n.outputs[0]
    uv = new("ShaderNodeTexCoord", "Scan UV")
    xy = new("ShaderNodeSeparateXYZ", "Scan Coordinates")
    links.new(uv.outputs["UV"], xy.inputs[0])
    tiling = value("ScanTiling", max(1.0, scalar("Tiling", 64.0)))
    x = math("MULTIPLY", "LED columns", xy.outputs["X"], tiling)
    y = math("MULTIPLY", "LED rows", xy.outputs["Y"], tiling)
    # Bytecode dp2/sqrt of frac(UV*Tiling)-.5: regular circular cells.
    dx = math("SUBTRACT", "Cell X", math("FRACT", "Column fraction", x), 0.5)
    dy = math("SUBTRACT", "Cell Y", math("FRACT", "Row fraction", y), 0.5)
    radius = math("SQRT", "Circular distance", math("ADD", "Radius squared", math("MULTIPLY", "X squared", dx, dx), math("MULTIPLY", "Y squared", dy, dy)))
    camera = new("ShaderNodeCameraData", "Scan view depth")
    # UE ConvertFromDeviceZ gives linear view-axis depth, not Euclidean range.
    depth_cm = math("MULTIPLY", "View depth centimetres", camera.outputs["View Z Depth"], 100)
    depth_factor = math("MINIMUM", "Distance falloff saturation", math("MAXIMUM", "Distance falloff positive", math("DIVIDE", "Distance falloff", depth_cm, value("DistanceFalloff", max(1e-6, scalar("DistanceFalloff", 2048)))), 0), 1)
    distance_size = math("MULTIPLY", "Distance size", depth_factor, value("DistanceSize", scalar("DistanceSize", 1)))
    growth = math("MINIMUM", "Cell growth saturation", math("MAXIMUM", "Cell growth positive", math("MULTIPLY", "Cell growth factor", distance_size, 0.0625), 0), 1)
    dynamic_radius = math("ADD", "Distance expanded radius", value("ScanLEDRadius", 0.4), math("MULTIPLY", "Radius growth", growth, 0.6))
    edge = math("SUBTRACT", "LED edge", 1, math("DIVIDE", "Normalized radius", radius, dynamic_radius))
    mask = math("MINIMUM", "LED mask", math("MAXIMUM", "Inside circle", math("MULTIPLY", "Edge threshold", edge, 100000), 0), 1)
    mask = math("ADD", "Distance filled LED mask", mask, math("MULTIPLY", "Distance gap fill", depth_factor, math("SUBTRACT", "LED gaps", 1, mask)))

    seconds = new("ShaderNodeValue", "ScanTimeSeconds")
    seconds.outputs[0].default_value = 0
    scene = getattr(bpy.context, "scene", None)
    if scene:
        fps = scene.render.fps / max(scene.render.fps_base, 1e-6)
        driver = seconds.outputs[0].driver_add("default_value").driver
        driver.expression = f"frame / {fps!r}"
    speed = value("ScanSpeed", scalar("ScanSpeed", 0.5))
    loop = switches.get("Loop", "loop" in str(mi_path).lower())
    phase = math("MULTIPLY", "Sweep time", seconds.outputs[0], speed)
    if loop:
        distance = math("ABSOLUTE", "Loop sweep distance", math("SUBTRACT", "Loop sweep coordinate", math("MULTIPLY", "Loop sweep scale", math("FRACT", "Loop sweep fraction", math("ADD", "Loop UV plus time", xy.outputs["X"], phase)), 2), 1))
        flicker = math("SUBTRACT", "Alert wave offset", math("SINE", "Alert second sine", math("MULTIPLY", "Alert second phase", math("SINE", "Alert first sine", math("MULTIPLY", "Alert time", seconds.outputs[0], 6.283185)), 6.283185)), 0.9)
        alert_distance = math("SUBTRACT", "Alert wave distance", flicker, distance)
        distance = math("ADD", "State sweep", distance, math("MULTIPLY", "Alert branch", math("SUBTRACT", "Alert distance difference", alert_distance, distance), value("ScanAlertPattern", 0)))
    else:
        center = math("ADD", "Nonloop center", math("MULTIPLY", "Nonloop amplitude", math("SINE", "Nonloop sine", math("MULTIPLY", "Nonloop phase", phase, 6.283185)), 0.5), 0.5)
        distance = math("ABSOLUTE", "Nonloop sweep distance", math("MULTIPLY", "Nonloop sweep scale", math("ADD", "Nonloop offset", math("ADD", "Nonloop UV plus center", xy.outputs["X"], center), -1), 2))
        value("ScanAlertPattern", 0)
    distance = math("MULTIPLY", "Aggro full display", distance, math("SUBTRACT", "Nonaggro", 1, value("ScanAggroPattern", 0)))
    wide = math("MAXIMUM", "Broad sweep", math("MULTIPLY", "Broad falloff", math("SUBTRACT", "Broad width", 0.4, distance), 0.125), 0)
    narrow = math("MINIMUM", "Narrow sweep", math("MAXIMUM", "Narrow sweep positive", math("MULTIPLY", "Narrow falloff", math("SUBTRACT", "Narrow width", 0.1, distance), 10), 0), 1)
    band = math("MINIMUM", "Sweep saturation", math("ADD", "Broad plus narrow", wide, narrow), 1)
    band = math("MINIMUM", "Distance expanded sweep", math("MULTIPLY", "Distance sweep boost", band, math("ADD", "Distance sweep scale", 1, distance_size)), 1)
    low = new("ShaderNodeRGB", "ScanDimColor")
    hot = new("ShaderNodeRGB", "ScanHotColor")
    mix = new("ShaderNodeMixRGB", "Sweep gradient")
    links.new(band, mix.inputs[0])
    links.new(low.outputs[0], mix.inputs[1])
    links.new(hot.outputs[0], mix.inputs[2])
    led_color = new("ShaderNodeMixRGB", "Circular LED color")
    led_color.blend_type = "MULTIPLY"
    led_color.inputs[0].default_value = 1
    links.new(mix.outputs[0], led_color.inputs[1])
    links.new(mask, led_color.inputs[2])
    intensity = math("MULTIPLY", "Authored emission multiplier", band, 16)
    value("DamagePercent", min(max(scalar("DamagePercent", 0), 0), 1))
    if switches.get("Blink", "pulse" in str(mi_path).lower()):
        pulse = math("ADD", "Pulse floor", 0.5, math("MULTIPLY", "Pulse range", math("SINE", "Pulse", math("MULTIPLY", "Pulse time", seconds.outputs[0], 6.283)), 0.5))
        intensity = math("MULTIPLY", "Pulsed LEDs", intensity, pulse)
    bsdf = new("ShaderNodeBsdfPrincipled", "Opaque display backing")
    links.new(math("ADD", "Display reflectance", 0.04, math("MULTIPLY", "LED reflectance", mask, 0.02)), bsdf.inputs["Base Color"])
    links.new(math("SUBTRACT", "LED roughness", 0.6, math("MULTIPLY", "LED smoothness", mask, 0.2)), bsdf.inputs["Roughness"])
    bsdf.inputs["Metallic"].default_value = 0
    links.new(led_color.outputs[0], bsdf.inputs["Emission Color"])
    links.new(intensity, bsdf.inputs["Emission Strength"])
    out = new("ShaderNodeOutputMaterial", "Scan Display Output")
    links.new(bsdf.outputs[0], out.inputs["Surface"])
    mat["arc_mi_family"] = "scan_display"
    mat["arc_scan_display"] = True
    mat["arc_mi_path"] = str(mi_path or "")
    mat["arc_scan_reconstruction_version"] = 4
    mat["arc_scan_evidence"] = "SM5 B46B1692/3ADB4E4D bytecode: regular round LEDs, recovered blue/orange/red palette, AlertnessState full aggro zeros sweep distance (uniform red LEDs), broad+narrow scan, emission16; no hand-added aggro stripe; selection and glitch/damage omitted"
    alert = scalar("AlertnessState", 0)
    state = state or str(mat.get("arc_scan_state", "")) or (
        "AGGRO" if abs(alert - 1) <= 0.33 else
        "ALERT" if abs(alert - 0.5) <= 0.33 else "SCANNING"
    )
    set_scan_state(mat, state)
    return mat
