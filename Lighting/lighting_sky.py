"""Scenario sky + sun for the Lighting Look (world shader and sun lamp).

Units: Blender pixel values are Unreal nits. A Blender sun lamp of strength S
lights a white Lambertian surface to S/pi, the same as S lux in Unreal, so
lamp strength = lux. The physical sky (Multiple Scattering) is rescaled per
channel so its top-of-atmosphere sun equals the scenario's directional light
(illuminance x color); camera rays see that sky, every other ray sees it times
the SkyLight intensity and color (Unreal's captured sky light). The sun disc is
visible to the camera only; a sun lamp attenuated by the same model's
transmittance provides direct light.

Calibration (Blender 5.1.2, Multiple Scattering, air/aerosol/ozone 1.0):
_TOA_RGB is the sun-disc irradiance above the atmosphere at strength 1;
_TRANSMITTANCE is ground disc irradiance / _TOA_RGB by sun elevation.

Scenarios with a Pioneer_Sky actor render through the Ultra Dynamic Sky sphere
material (MID of Ultra_Dynamic_Sky_Mat_SA_NDC_NA, SM5 shader map
4A7198623FCD01A8A5C01FF45AC6ACEBEA43FC9D, base-pass PS D525701D…, VS 5B47CEF8…).
``_uds_nodes`` is that pixel shader with the physical sky standing in for the
SkyAtmosphere view LUT; docs/lighting-look-2.20.3.md lists the instructions.
"""
from __future__ import annotations

import math
from typing import Any

import bpy
import mathutils

_WORLD_TAG = "arc_lighting_sky"
_SUN_NAME = "ArcLightingSun"
_TOA_RGB = (179.315, 164.488, 150.121)
# elevation degrees -> (R, G, B) transmittance of the model's sun disc at sea level
_TRANSMITTANCE = (
    (0.0, (0.0, 0.0, 0.0)),
    (5.0, (0.392, 0.215, 0.051)),
    (15.0, (0.700, 0.562, 0.344)),
    (30.0, (0.829, 0.740, 0.573)),
    (45.0, (0.876, 0.808, 0.675)),
    (60.0, (0.897, 0.840, 0.725)),
    (90.0, (0.912, 0.861, 0.758)),
)
_UE_RAYLEIGH_SCALE = 0.0331
_UE_MIE_SCALE = 0.003996

# Cloud_Wisps_Texture UV = SM_Ultra_Dynamic_Sky_Sphere UV channel 5 (VS fetches min(5, NumTexCoords-1)).
# Upper hemisphere: stereographic, uv = c + [[a, -b], [b, a]] (-x, y) / (1 + z) in sphere-local Unreal
# axes (least squares over 1985 vertices: rms 3.7e-4, max 1.6e-3 UV). Below the horizon the mesh is a
# three-ring skirt that mirrors the image: radius piecewise linear in z, angle = azimuth + beta.
_UV5_CENTER = (0.5009417, 0.5004221)
_UV5_AB = (0.4995836, -0.0035687)
_UV5_SKIRT = ((-1.0, 0.0989422), (-0.562, 0.1358208), (-0.0981, 0.4085675), (0.0, 0.4995963))
_UV5_SKIRT_BETA = math.radians(-168.8425)
_REC709_Y = (0.2126, 0.7152, 0.0722)


def _table(table, elevation_deg: float) -> tuple[float, float, float]:
    e = max(0.0, min(90.0, float(elevation_deg)))
    for (e0, v0), (e1, v1) in zip(table, table[1:]):
        if e <= e1:
            t = (e - e0) / (e1 - e0) if e1 > e0 else 0.0
            return tuple(a + (b - a) * t for a, b in zip(v0, v1))
    return table[-1][1]


def srgb8_to_linear(rgb) -> tuple[float, float, float]:
    out = []
    for c in rgb[:3]:
        v = max(0.0, min(255.0, float(c))) / 255.0
        out.append(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
    return tuple(out)


def color_temperature(kelvin: float) -> tuple[float, float, float]:
    """FLinearColor::MakeFromColorTemperature (Planckian locus, Y = 1, linear Rec.709)."""
    t = max(1000.0, min(15000.0, float(kelvin)))
    u = (0.860117757 + 1.54118254e-4 * t + 1.28641212e-7 * t * t) / (1.0 + 8.42420235e-4 * t + 7.08145163e-7 * t * t)
    v = (0.317398726 + 4.22806245e-5 * t + 4.20481691e-8 * t * t) / (1.0 - 2.89741816e-5 * t + 1.61456053e-7 * t * t)
    x = 3.0 * u / (2.0 * u - 8.0 * v + 4.0)
    y = 2.0 * v / (2.0 * u - 8.0 * v + 4.0)
    X, Y, Z = x / y, 1.0, (1.0 - x - y) / y
    return (3.2404542 * X - 1.5371385 * Y - 0.4985314 * Z,
            -0.9692660 * X + 1.8760108 * Y + 0.0415560 * Z,
            0.0556434 * X - 0.2040259 * Y + 1.0572252 * Z)


def light_rgb(entry: dict[str, Any]) -> tuple[float, float, float]:
    """Directional light illuminance per channel (lux): intensity * color (* temperature)."""
    base = srgb8_to_linear(entry.get("light_color") or (255, 255, 255))
    if entry.get("use_temperature"):
        temp = color_temperature(entry.get("temperature") or 6500.0)
        base = tuple(a * b for a, b in zip(base, temp))
    intensity = float(entry.get("intensity") or 0.0)
    return tuple(max(0.0, c) * intensity for c in base)


def ue_to_blender_dir(v, mirror_y: bool) -> mathutils.Vector:
    x, y, z = (float(c) for c in v[:3])
    return mathutils.Vector((x, -y, z) if mirror_y else (x, y, z)).normalized()


def primary_light(entry: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """The light that drives the sky: the sun when above the horizon, else the moon."""
    for key in ("sun", "moon"):
        light = entry.get(key)
        if not light:
            continue
        fwd = light.get("forward") or (0.0, 0.0, -1.0)
        if float(fwd[2]) < -1e-4 and max(light_rgb(light)) > 0.0:
            return key, light
    return "", None


def _ue_vec(v, mirror_y: bool) -> tuple[float, float, float]:
    """Unreal vector -> Blender axes without normalizing (the shader uses raw parameter values)."""
    x, y, z = (float(c) for c in v[:3])
    return (x, -y, z) if mirror_y else (x, y, z)


class _Graph:
    """World-shader builder: scalar/vector math on sockets or constants, laid out column by column."""

    def __init__(self, nt):
        self.nt = nt
        self.count = 0

    def new(self, kind: str, label: str):
        node = self.nt.nodes.new(kind)
        node.label = node.name = label
        node.location = (-3000 + 190 * (self.count // 14), 900 - 120 * (self.count % 14))
        self.count += 1
        return node

    def put(self, sock, value) -> None:
        if isinstance(value, bpy.types.NodeSocket):
            self.nt.links.new(value, sock)
        elif isinstance(value, (tuple, list)):
            sock.default_value = tuple(float(c) for c in value[:len(sock.default_value)])
        else:
            sock.default_value = float(value)

    def math(self, op: str, a, b=None, c=None, *, clamp: bool = False, label: str = ""):
        node = self.new("ShaderNodeMath", label or "ArcSkyMath")
        node.operation = op
        node.use_clamp = clamp
        for i, v in enumerate((a, b, c)):
            if v is not None:
                self.put(node.inputs[i], v)
        return node.outputs[0]

    def vec(self, op: str, a, b=None, c=None, *, scale=None, label: str = ""):
        node = self.new("ShaderNodeVectorMath", label or "ArcSkyVec")
        node.operation = op
        for i, v in enumerate((a, b, c)):
            if v is not None:
                self.put(node.inputs[i], v)
        if scale is not None:
            self.put(node.inputs["Scale"], scale)
        return node.outputs["Value" if op in ("DOT_PRODUCT", "LENGTH", "DISTANCE") else "Vector"]

    def xyz(self, v):
        node = self.new("ShaderNodeSeparateXYZ", "ArcSkyAxes")
        self.nt.links.new(v, node.inputs[0])
        return node.outputs["X"], node.outputs["Y"], node.outputs["Z"]

    def combine(self, x, y, z, label: str = "ArcSkyCombine"):
        node = self.new("ShaderNodeCombineXYZ", label)
        for sock, v in zip(node.inputs, (x, y, z)):
            self.put(sock, v)
        return node.outputs[0]

    def mix(self, factor, a, b, label: str):
        """a*(1-factor) + b*factor on vectors (factor 0/1 picks a or b)."""
        node = self.new("ShaderNodeMix", label)
        node.data_type = "VECTOR"
        node.clamp_factor = True
        self.put(node.inputs["Factor"], factor)
        ins = {s.name: s for s in node.inputs if s.type == "VECTOR" and s.name in ("A", "B")}
        self.put(ins["A"], a)
        self.put(ins["B"], b)
        return next(s for s in node.outputs if s.type == "VECTOR")


def _wisps_uv_nodes(g: _Graph, d, sphere: dict[str, Any], mirror_y: bool, panner, cloud_time: float):
    """View direction -> Cloud_Wisps_Texture UV (Blender image space: v flipped from Unreal's top-left origin).

    UV5 of SM_Ultra_Dynamic_Sky_Sphere in sphere-local Unreal axes, then + Time * Wispy Cloud Panner.xy
    (PS 138-139). The sphere's world yaw (Pioneer_Sky Root yaw composed with the component's own yaw)
    turns world directions into the mesh frame.
    """
    cx, cy = (float(c) for c in (sphere.get("x_axis") or (1.0, 0.0, 0.0))[:2])
    sy = -1.0 if mirror_y else 1.0
    lx = g.vec("DOT_PRODUCT", d, (cx, sy * cy, 0.0), label="ArcSkyCloudLocalX")
    ly = g.vec("DOT_PRODUCT", d, (-cy, sy * cx, 0.0), label="ArcSkyCloudLocalY")
    lz = g.xyz(d)[2]
    flat = g.combine(lx, ly, 0.0, label="ArcSkyCloudLocalXY")
    (cu, cv), (a, b) = _UV5_CENTER, _UV5_AB
    # z >= 0: stereographic projection from the nadir.
    proj = g.vec("SCALE", flat, scale=g.math("DIVIDE", 1.0, g.math("ADD", lz, 1.0)), label="ArcSkyCloudStereo")
    up_u = g.math("ADD", g.vec("DOT_PRODUCT", proj, (-a, -b, 0.0)), cu)
    up_v = g.math("ADD", g.vec("DOT_PRODUCT", proj, (-b, a, 0.0)), cv)
    # z < 0: skirt rings, radius = max of the three ring-to-ring segments (convex in z).
    radius = None
    for (z0, r0), (z1, r1) in zip(_UV5_SKIRT, _UV5_SKIRT[1:]):
        k = (r1 - r0) / (z1 - z0)
        seg = g.math("MULTIPLY_ADD", lz, k, r0 - k * z0)
        radius = seg if radius is None else g.math("MAXIMUM", radius, seg)
    unit = g.vec("NORMALIZE", flat)
    cb, sb = math.cos(_UV5_SKIRT_BETA), math.sin(_UV5_SKIRT_BETA)
    lo_u = g.math("MULTIPLY_ADD", g.vec("DOT_PRODUCT", unit, (cb, -sb, 0.0)), radius, cu)
    lo_v = g.math("MULTIPLY_ADD", g.vec("DOT_PRODUCT", unit, (sb, cb, 0.0)), radius, cv)
    below = g.math("LESS_THAN", lz, 0.0)
    uv = g.mix(below, g.combine(up_u, up_v, 0.0), g.combine(lo_u, lo_v, 0.0), "ArcSkyCloudUE")
    t = float(cloud_time)
    uv = g.vec("ADD", uv, (t * float(panner[0]), t * float(panner[1]), 0.0), label="ArcSkyCloudPan")
    u, v, _ = g.xyz(uv)
    return g.combine(u, g.math("SUBTRACT", 1.0, v), 0.0, label="ArcSkyCloudUV")


def _uds_nodes(g: _Graph, entry: dict[str, Any], d, atmos, *, mirror_y: bool, cloud_image, cloud_time: float):
    """Ultra Dynamic Sky base-pass PS (D525701D...) on top of ``atmos`` (the SkyAtmosphere view luminance).

    Returns (camera color, sky-light capture color, status). Parameter values are the scenario MID's;
    defaults are Ultra_Dynamic_Sky_Mat's. Stars, the moon disc and the dither are not emulated.
    """
    mat = entry.get("sky_material") or {}
    sca, vec = mat.get("scalars") or {}, mat.get("vectors") or {}

    def scalar(name, default):
        return float(sca.get(name, default))

    def vector(name, default):
        v = vec.get(name)
        return tuple(float(c) for c in v[:3]) if v else default

    sun_p = vector("Sun Position", (0.0, 0.486726, -1.0))     # UE forward of the sun (light travels along it)
    moon_v = vector("Moon Light Vector", (0.0, 0.486726, 1.0))
    s_b = tuple(-c for c in _ue_vec(sun_p, mirror_y))           # towards the sun, Blender axes
    m_len = math.sqrt(sum(c * c for c in moon_v)) or 1.0
    m_b = _ue_vec(tuple(c / m_len for c in moon_v), mirror_y)
    sun_up = max(0.0, min(1.0, -sun_p[2]))                      # cb1[4].y
    status = []

    # Wisps (t4): texture * Wispy Cloud Color * Wispy Cloud Alpha (PS 140-142).
    tint = tuple(c * scalar("Wispy Cloud Alpha", 1.0) for c in vector("Wispy Cloud Color", (1.0, 1.0, 1.0)))
    sphere = entry.get("sky_sphere") or {}
    wisps = None
    if cloud_image is not None and max(tint) > 0.0:
        if sphere.get("mesh", "SM_Ultra_Dynamic_Sky_Sphere") == "SM_Ultra_Dynamic_Sky_Sphere":
            panner = vec.get("Wispy Cloud Panner") or (0.0, 0.0)
            uv = _wisps_uv_nodes(g, d, sphere, mirror_y, panner, cloud_time)
            tex = g.new("ShaderNodeTexImage", "ArcSkyClouds")
            tex.image = cloud_image
            tex.extension = "REPEAT"
            tex.interpolation = "Linear"
            g.nt.links.new(uv, tex.inputs["Vector"])
            wisps = g.vec("MULTIPLY", tex.outputs["Color"], tint, label="ArcSkyWisps")
            status.append(f"clouds ×{scalar('Wispy Cloud Alpha', 1.0):.2f}")
        else:
            status.append(f"clouds off ({sphere.get('mesh')} UVs are not a projection)")

    # Atmosphere + wisps (PS 136-149): m = (h*A' + W)/2, C = m + W*(A' + W - m).
    glow = vector("Night Sky Glow Color", (0.0, 0.0, 0.0))
    a1 = g.vec("ADD", atmos, glow, label="ArcSkyAtmosGlow")
    hybrid = scalar("Use Hybrid Sky", 1.0)
    if wisps is None:
        cloud = g.vec("SCALE", a1, scale=0.5 * hybrid, label="ArcSkyHybrid")
    else:
        mid = g.vec("SCALE", g.vec("MULTIPLY_ADD", a1, (hybrid,) * 3, wisps), scale=0.5)
        rest = g.vec("SUBTRACT", g.vec("ADD", a1, wisps), mid)
        cloud = g.vec("MULTIPLY_ADD", wisps, rest, mid, label="ArcSkyHybrid")

    # Sun disc (PS 150-235): F = sat((1 - |n - P| / R) / 0.7)^4 * Sun Disk Intensity,
    # color = |L| * lerp(normalize(A), normalize(L), sun height), above the horizon only.
    sun = entry.get("sun") or {}
    lum = (0.0, 0.0, 0.0)
    if sun:
        el = math.degrees(math.asin(max(-1.0, min(1.0, s_b[2] / (math.sqrt(sum(c * c for c in s_b)) or 1.0)))))
        if el > 0.0:
            trans = _table(_TRANSMITTANCE, el) if sun.get("atmosphere_sun", True) else (1.0, 1.0, 1.0)
            lum = tuple(c * t for c, t in zip(light_rgb(sun), trans))
    l_len = math.sqrt(sum(c * c for c in lum))
    color = cloud
    if l_len > 0.0:
        inv_r = 1.0 / max(1e-5, scalar("Sun Radius", 0.0003))
        dist = g.vec("DISTANCE", d, s_b)
        edge = g.math("MULTIPLY", g.math("MULTIPLY_ADD", dist, -inv_r, 1.0), 1.4285715, clamp=True)
        disc = g.math("MULTIPLY", g.math("POWER", edge, 4.0), scalar("Sun Disk Intensity", 1.0) * l_len)
        horizon = g.math("MULTIPLY", g.math("SUBTRACT", g.xyz(d)[2], 0.007), 150.0, clamp=True)
        weight = g.math("MULTIPLY", disc, horizon, label="ArcSkySunDisc")
        hue = g.vec("MULTIPLY_ADD", g.vec("NORMALIZE", atmos), (1.0 - sun_up,) * 3,
                    tuple(c / l_len * sun_up for c in lum))
        color = g.vec("MULTIPLY_ADD", hue, g.combine(weight, weight, weight), cloud, label="ArcSkySunDiscAdd")

    # Sun/moon glow gain (PS 239-260): g = (1.8*(sun + moon) + 2.3) * Lit_Intensity.
    k = (1.0 - sun_up) * 2.0 + 1.0                              # cb1[4].w
    dx, dy, dz = g.xyz(d)
    bent = g.vec("NORMALIZE", g.combine(dx, dy, g.math("MULTIPLY_ADD", g.math("SUBTRACT", dz, s_b[2]), k, s_b[2])))

    def lobe(cos):
        return g.math("MAXIMUM", g.math("MULTIPLY_ADD", g.math("ABSOLUTE", g.math("SUBTRACT", cos, 1.0)), -0.5, 1.0), 0.0)

    sun_lobe = lobe(g.vec("DOT_PRODUCT", bent, s_b))
    moon_lobe = lobe(g.math("MULTIPLY", g.vec("DOT_PRODUCT", d, m_b), -1.0))
    moon_w = max(0.0, min(1.0, sun_p[2] * 15.0))                # cb1[5].w
    lobes = g.math("MULTIPLY_ADD", moon_lobe, moon_w, sun_lobe)
    gain = g.math("MULTIPLY", g.math("MULTIPLY_ADD", lobes, 1.8, 2.3), scalar("Lit_Intensity", 1.0), label="ArcSkyGain")
    camera = g.vec("SCALE", color, scale=gain, label="ArcSkyCameraLinear")
    # Reflection / sky-light capture (RenderingReflectionCaptureMask > 0, PS 233-238): 1.35 * A', no wisps or disc.
    capture = g.vec("SCALE", g.vec("SCALE", a1, scale=1.35), scale=gain, label="ArcSkyCaptureLinear")

    # Extra_Contrast / Overall Intensity / Saturation (PS 446-465), absolute luminance.
    threshold = max(0.025, min(0.3, -sun_p[2] + 0.1))           # cb1[18].w
    controls = {}
    for name in ("Extra_Contrast", "Overall Intensity", "Saturation"):
        control = g.new("ShaderNodeValue", name)
        control.outputs[0].default_value = scalar(name, 1.0)
        controls[name] = control.outputs[0]
    ec, oi, sat = (controls[name] for name in ("Extra_Contrast", "Overall Intensity", "Saturation"))
    lift_gain = g.math("MULTIPLY", ec, 2.5)
    crush_gain = g.math("MULTIPLY", ec, 5.0)
    remaining = g.math("SUBTRACT", 1.0, sat)

    def grade(x, label):
        off = g.vec("SUBTRACT", x, (threshold,) * 3)
        lift = g.vec("POWER", g.vec("MAXIMUM", off, (0.0, 0.0, 0.0)), (1.2, 1.2, 1.2))
        boosted = g.vec("MULTIPLY_ADD", lift, g.combine(lift_gain, lift_gain, lift_gain), x)
        crush = g.vec("MULTIPLY_ADD", g.vec("MINIMUM", off, (0.0, 0.0, 0.0)), g.combine(crush_gain, crush_gain, crush_gain), (1.0, 1.0, 1.0))
        scaled = g.vec("SCALE", g.vec("MULTIPLY", boosted, crush), scale=oi)
        luma = g.vec("DOT_PRODUCT", scaled, _REC709_Y)
        gray = g.vec("SCALE", (1.0, 1.0, 1.0), scale=g.math("MULTIPLY", luma, sat))
        return g.vec("MAXIMUM", g.vec("MULTIPLY_ADD", scaled, g.combine(remaining, remaining, remaining), gray), (0.0, 0.0, 0.0), label=label)

    status.append(f"UDS grade ×{scalar('Overall Intensity', 1.0):g}")
    return grade(camera, "ArcSkyCameraOut"), grade(capture, "ArcSkyCaptureOut"), status


def _own_world(scene) -> bpy.types.World:
    """Use a dedicated world; remember the user's so the look can be removed."""
    world = scene.world
    if world is None or not world.get(_WORLD_TAG):
        if world is not None:
            scene["arc_lighting_prev_world"] = world.name
        world = bpy.data.worlds.get("ArcLightingSky") or bpy.data.worlds.new("ArcLightingSky")
        scene.world = world
    world[_WORLD_TAG] = 1
    world.use_nodes = True
    return world


def _drive_sun_rotation(sky_node, world, rotation_node, base: float) -> None:
    """sky_node.sun_rotation = base + the ArcSkyRotation Angle, as a simple-expression driver."""
    sky_node.driver_remove("sun_rotation")
    drv = sky_node.driver_add("sun_rotation").driver
    drv.type = "SCRIPTED"
    var = drv.variables.new()
    var.name = "angle"
    var.type = "SINGLE_PROP"
    var.targets[0].id_type = "WORLD"
    var.targets[0].id = world
    var.targets[0].data_path = 'node_tree.nodes["%s"].inputs["Angle"].default_value' % rotation_node.name
    drv.expression = "%r + angle" % float(base)


def ensure_scenario_sky(scene, entry: dict[str, Any], *, mirror_y: bool, cloud_image=None,
                        cloud_time: float = 0.0, strength: float = 1.0) -> str:
    """Build/refresh the world from a catalog scenario. Returns a status string."""
    world = _own_world(scene)
    if "arc_lighting_prev_film_transparent" not in scene:
        scene["arc_lighting_prev_film_transparent"] = scene.render.film_transparent
    scene.render.film_transparent = False
    nt = world.node_tree
    for n in list(nt.nodes):
        if n.bl_idname != "ShaderNodeOutputWorld":
            nt.nodes.remove(n)
    out = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputWorld"), None) or nt.nodes.new("ShaderNodeOutputWorld")
    out.location = (900, 0)
    out.is_active_output = True
    g = _Graph(nt)

    kind, light = primary_light(entry)
    atmos = entry.get("atmosphere") or {}
    air = float(atmos.get("RayleighScatteringScale") or _UE_RAYLEIGH_SCALE) / _UE_RAYLEIGH_SCALE
    aerosol = float(atmos.get("MieScatteringScale") or _UE_MIE_SCALE) / _UE_MIE_SCALE
    elevation = 0.0
    if light is not None:
        to_sun = -ue_to_blender_dir(light["forward"], mirror_y)
        elevation = math.degrees(math.asin(max(-1.0, min(1.0, to_sun.z))))
        # Blender's sky places the sun at (cos e * sin r, cos e * cos r, sin e).
        rotation = math.atan2(to_sun.x, to_sun.y)
    else:
        rotation = 0.0
    uds = bool(entry.get("sky_material")) and bool(entry.get("sky_sphere"))
    # Without a Pioneer_Sky actor the SkyAtmosphere draws itself, sun disc included.
    sky_nodes = [(g.new("ShaderNodeTexSky", "ArcSkyAtmosphere"), False)]
    if not uds:
        sky_nodes.append((g.new("ShaderNodeTexSky", "ArcSkyCamera"), True))
    for sky, disc in sky_nodes:
        sky.sky_type = "MULTIPLE_SCATTERING"
        sky.sun_disc = disc
        sky.sun_elevation = math.radians(max(elevation, 0.0))
        sky.sun_rotation = rotation
        sky.altitude = 0.0
        sky.air_density = max(0.0, min(10.0, air))
        sky.aerosol_density = max(0.0, min(10.0, aerosol))
        sky.ozone_density = 1.0
        if light is not None:
            sky.sun_size = math.radians(max(0.05, float(light.get("source_angle") or 0.5357)))

    illum = light_rgb(light) if light is not None else (0.0, 0.0, 0.0)
    scale = [float(strength) * i / t for i, t in zip(illum, _TOA_RGB)]
    lum_factor = atmos.get("SkyLuminanceFactor") or {}
    lum = (float(lum_factor.get("R", 1.0)), float(lum_factor.get("G", 1.0)), float(lum_factor.get("B", 1.0))) \
        if isinstance(lum_factor, dict) else (1.0, 1.0, 1.0)
    cam_scale = [s * l for s, l in zip(scale, lum)]
    sky_light = entry.get("sky_light") or {}
    sl_color = srgb8_to_linear(sky_light.get("light_color") or (255, 255, 255))
    sl_int = float(sky_light.get("intensity") if sky_light.get("intensity") is not None else 1.0)
    sl = tuple(c * sl_int for c in sl_color)

    coord = g.new("ShaderNodeTexCoord", "ArcSkyCoord")
    rotation_node = g.new("ShaderNodeVectorRotate", "ArcSkyRotation")
    rotation_node.rotation_type = "Z_AXIS"
    g.put(rotation_node.inputs["Vector"], coord.outputs["Generated"])
    d = g.vec("NORMALIZE", rotation_node.outputs["Vector"], label="ArcSkyDirection")
    for sky_node, disc in sky_nodes:
        vec = sky_node.inputs.get("Vector")
        if vec is not None:
            g.put(vec, d)
        else:
            # Blender 5.1 hides the Vector socket on the multiple-scattering sky: it always samples the ray
            # direction. Rotating the lookup by +Angle about Z turns the sky by -Angle, which for this sky is
            # sun_rotation + Angle (sun_rotation runs from +Y towards +X). A simple-expression driver keeps the
            # Rotation slider working without Python auto-run.
            _drive_sun_rotation(sky_node, world, rotation_node, sky_node.sun_rotation)
    atmos_col = g.vec("MULTIPLY", sky_nodes[0][0].outputs["Color"], cam_scale, label="ArcSkyAtmosphereScaled")
    status = [f"{kind or 'no light'} {elevation:.1f}° up"]
    if uds:
        camera, capture, extra = _uds_nodes(g, entry, d, atmos_col, mirror_y=mirror_y, cloud_image=cloud_image,
                                            cloud_time=cloud_time)
        status += extra
    else:
        camera = g.vec("MULTIPLY", sky_nodes[1][0].outputs["Color"], cam_scale, label="ArcSkyCameraOut")
        capture = atmos_col
    lighting = g.vec("MULTIPLY", capture, sl, label="ArcSkyLightingOut")

    path = g.new("ShaderNodeLightPath", "ArcSkyLightPath")
    split = g.mix(path.outputs["Is Camera Ray"], lighting, camera, "ArcSkyCameraSplit")
    bg = g.new("ShaderNodeBackground", "ArcSkyBackground")
    bg.location = (700, 0)
    nt.links.new(split, bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 1.0
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    world["arc_lighting_sky_scale"] = scale
    return " · ".join(status)



def ensure_sun(scene, entry: dict[str, Any], *, mirror_y: bool, enabled: bool = True) -> str:
    """Create/update the scenario sun (or moon) lamp. Strength is lux after transmittance."""
    obj = bpy.data.objects.get(_SUN_NAME)
    kind, light = primary_light(entry)
    if not enabled or light is None:
        if obj is not None:
            obj.hide_viewport = obj.hide_render = True
        return "sun: off" if not enabled else "sun: below horizon"
    if obj is not None and (obj.type != "LIGHT" or obj.data.type != "SUN"):
        obj.name = _SUN_NAME + "_old"
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(_SUN_NAME, bpy.data.lights.new(_SUN_NAME, "SUN"))
    if scene.objects.get(obj.name) is None:
        scene.collection.objects.link(obj)
    data = obj.data
    obj.hide_viewport = obj.hide_render = False
    forward = ue_to_blender_dir(light["forward"], mirror_y)
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = forward.to_track_quat("-Z", "Y")
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, -forward.z))))
    trans = _table(_TRANSMITTANCE, elevation) if light.get("atmosphere_sun", True) else (1.0, 1.0, 1.0)
    rgb = [c * t for c, t in zip(light_rgb(light), trans)]
    peak = max(rgb) if max(rgb) > 0 else 1.0
    data.color = tuple(c / peak for c in rgb)
    data.energy = peak
    data.angle = math.radians(max(0.05, float(light.get("source_angle") or 0.5357)))
    obj["arc_lighting_source"] = kind
    return f"{kind}: {peak:.2f} lux at {elevation:.1f}°"
