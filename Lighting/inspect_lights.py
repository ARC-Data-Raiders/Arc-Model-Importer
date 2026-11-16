"""Outfit Inspect/Customization light rig, rebuilt from cooked MainMenu data.

Source (static, cooked map data): /Game/Pioneer/Maps/MainMenu, actor PioneerCustomizationLocation
(CustomizationLocationComponent, export 356, tag FE.Actors.LocalPlayer) with nine attached
SpotLightComponents (exports 510-513, 526-530). Values below are the cooked RelativeLocation /
RelativeRotation and light properties, copied unchanged.

Frame: the cooked table is anchor-relative. A saved minidump of the menu (static file read, documented in
DataRaiders-Documentation sections/ui-preview-lighting.md) shows the runtime rig is the cooked table rotated
+50 deg about Z in anchor space and the character actor at yaw 46.2544 against anchor yaw 50. The existing
Main Menu camera (scenario_cameras.MAIN_MENU) is expressed in that character frame, so lights use the same
frame: rotate the cooked table by CHARACTER_YAW_DEG about UE Z, then convert UE -> Blender (cm -> m, flip Y).

Intensity: every light is IntensityUnits=Unitless with bUseInverseSquaredFalloff=False and
InverseExposureBlend=1, so the authored value is the irradiance the light delivers (times the radial
falloff) independent of exposure. Blender point/spot lights are inverse-square, so energy is matched at
one reference point (REF_TARGET_M, character chest): W = Intensity * falloff(d) * 4*pi*d^2. Nearer or
farther surfaces get inverse-square falloff instead of UE's (1 - (d/R)^2)^exp curve.
"""
from __future__ import annotations

import math

import bpy
from mathutils import Matrix, Vector

RIG_ID = "outfit_inspect"
LABEL = "Outfit Inspect/Customization"
COLLECTION = f"Arc Studio Lights: {LABEL}"
CHARACTER_YAW_DEG = 53.7456  # cooked anchor -> character frame (+50 anchor, +3.7456 character offset)
REF_TARGET_M = (0.0, 0.0, 1.0)  # character-local point the intensity match is made at

# name, export, loc cm, (pitch, yaw, roll), intensity, temperature K (None = UE default 6500 unused),
# inner, outer, attenuation cm, source radius cm, soft source radius cm, source length cm,
# falloff exponent, cast shadows, specular scale
LIGHTS = (
    ("SpotLight_510", 510, (-77.75058, -90.56067, 244.64317), (-44.479725, 47.871094, -1.063359),
     5.0, 7500.0, 10.0, 30.0, 220.0, 10.0, 5.0, 20.0, 0.5, True, 0.5),
    ("SpotLight_511", 511, (-13.6249695, -159.09006, 172.74654), (-17.544777, 82.88644, -0.996727),
     4.0, 5500.0, 20.0, 32.0, 240.0, 10.0, 5.0, 20.0, 1.0, True, 0.5),
    ("SpotLight_512", 512, (-53.236923, -126.01523, 66.283264), (24.964983, 65.47109, 0.0),
     4.0, 9000.0, 5.0, 35.0, 250.0, 10.0, 0.0, 20.0, 0.5, True, 1.0),
    ("SpotLight_513", 513, (-143.30605, 73.08127, 173.49344), (-19.679724, -23.728909, 0.0),
     4.0, 5500.0, 20.0, 30.000484, 250.0, 10.0, 5.0, 20.0, 1.0, True, 0.5),
    ("SpotLight_526", 526, (18.180382, 91.73917, 4.693144), (11.782844, -107.53705, 0.0),
     2.0, 5800.0, 15.0, 26.0, 150.0, 10.0, 0.0, 20.0, 1.0, False, 1.0),
    ("SpotLight_527", 527, (161.41016, -74.00058, 193.70154), (-25.72113, 152.55255, 0.0),
     0.7, 5000.0, 20.0, 30.0, 250.0, 20.0, 0.0, 5.0, 1.0, True, 1.0),
    ("SpotLight_528", 528, (18.922934, 163.4071, 204.99622), (-30.217157, -94.22746, 0.0),
     4.25, None, 20.0, 45.000484, 250.0, 25.0, 0.0, 3.0, 1.0, True, 1.0),
    ("SpotLight_529", 529, (120.85163, 141.883, 173.62346), (-19.617155, -129.33705, 0.0),
     1.0, 8000.0, 20.0, 40.000484, 250.0, 10.0, 0.0, 20.0, 1.0, False, 1.0),
    ("SpotLight_530", 530, (103.54846, -158.94843, 128.6826), (-4.617156, 137.66295, 0.0),
     0.6, 8000.0, 20.0, 40.000484, 250.0, 10.0, 0.0, 20.0, 1.0, False, 1.0),
)

_S = Matrix(((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))
_LIGHT_FROM_X = Matrix.Rotation(math.radians(-90.0), 3, "Y")  # Blender light -Z onto local +X


def _ue_rot(pitch: float, yaw: float, roll: float) -> Matrix:
    return (Matrix.Rotation(math.radians(yaw), 3, "Z")
            @ Matrix.Rotation(math.radians(-pitch), 3, "Y")
            @ Matrix.Rotation(math.radians(-roll), 3, "X"))


def local_matrix(loc_cm, pyr) -> Matrix:
    """Character-local Blender matrix (metres) for one cooked anchor-relative light."""
    frame = Matrix.Rotation(math.radians(CHARACTER_YAW_DEG), 3, "Z")
    loc = _S @ (frame @ Vector(loc_cm)) * 0.01
    rot = _S @ (frame @ _ue_rot(*pyr)) @ _S @ _LIGHT_FROM_X
    m = rot.to_4x4()
    m.translation = loc
    return m


def ue_falloff(d_cm: float, radius_cm: float, exponent: float) -> float:
    """UE non-inverse-square radial attenuation."""
    t = min(1.0, (d_cm * d_cm) / (radius_cm * radius_cm))
    return (1.0 - t) ** exponent


def energy_w(loc_m: Vector, intensity: float, radius_cm: float, exponent: float) -> tuple[float, float]:
    d = (loc_m - Vector(REF_TARGET_M)).length
    f = ue_falloff(d * 100.0, radius_cm, exponent)
    return intensity * f * 4.0 * math.pi * d * d, d


def specs() -> list[dict]:
    out = []
    for (name, export, loc, pyr, inten, temp, inner, outer, atten, sr, ssr, sl, exp_, shadow,
         spec) in LIGHTS:
        m = local_matrix(loc, pyr)
        watts, d = energy_w(m.translation, inten, atten, exp_)
        out.append(dict(name=name, export=export, matrix=m, energy=watts, ref_dist=d,
                        temperature=temp, spot_size=math.radians(2.0 * outer),
                        spot_blend=max(0.0, min(1.0, (outer - inner) / outer)),
                        radius=sr * 0.01, cutoff=atten * 0.01, shadow=shadow, specular=spec,
                        ue=dict(intensity=inten, inner=inner, outer=outer, attenuation=atten,
                                source_radius=sr, soft_source_radius=ssr, source_length=sl,
                                falloff_exponent=exp_)))
    return out


def rig_stub() -> dict:
    """Entry for studio_lights.rigs(); the lights are built against the character, not from JSON."""
    return {"schema": "arc-studio-lights-v1", "id": RIG_ID, "label": LABEL, "builder": "inspect",
            "lights": [s[0] for s in LIGHTS]}


def _clear():
    old = bpy.data.collections.get(COLLECTION)
    if old is None:
        return
    for ob in list(old.objects):
        data = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.lights.remove(data)
    bpy.data.collections.remove(old)


def build(scene, *, camera: bool = True, context=None) -> tuple[bool, str]:
    from . import scenario_cameras
    context = context or bpy.context
    base, yaw, who = scenario_cameras._character(context)
    root = Matrix.Translation(base) @ Matrix.Rotation(yaw, 4, "Z")
    _clear()
    coll = bpy.data.collections.new(COLLECTION)
    scene.collection.children.link(coll)
    for s in specs():
        light = bpy.data.lights.new(s["name"], "SPOT")
        light.energy = s["energy"]
        light.color = (1.0, 1.0, 1.0)
        if hasattr(light, "use_temperature"):
            light.use_temperature = s["temperature"] is not None
            light.temperature = s["temperature"] or 6500.0
        light.spot_size = s["spot_size"]
        light.spot_blend = s["spot_blend"]
        light.shadow_soft_size = s["radius"]
        light.use_shadow = s["shadow"]
        if hasattr(light, "specular_factor"):
            light.specular_factor = s["specular"]
        if hasattr(light, "use_custom_distance"):
            light.use_custom_distance = True
            light.cutoff_distance = s["cutoff"]
        if hasattr(light, "show_cone"):
            light.show_cone = False
        ob = bpy.data.objects.new(s["name"], light)
        coll.objects.link(ob)
        ob.matrix_world = root @ s["matrix"]
        ob.visible_camera = False
        ob["arc_studio_rig"] = RIG_ID
        ob["arc_ue_export"] = s["export"]
        ob["arc_ue_intensity_unitless"] = s["ue"]["intensity"]
    notes = [f"{len(LIGHTS)} spot lights" + (f" on {who}" if who else " at origin")]
    if camera:
        notes.append(scenario_cameras.main_menu(scene, context))
    return True, f"Imported studio lights '{LABEL}': " + ", ".join(notes)
