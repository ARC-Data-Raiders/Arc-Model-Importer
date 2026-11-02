"""Lighting Look: game-derived color pipeline, sky and sun for a map scenario.

Data: ``assets/lighting_scenarios.json`` (static pak read, see lighting_catalog)
supplies each scenario's post-process volume, sun/moon, sky light, sky layer
and fog. Nothing here is hand-tuned:

* Color: the combine-LUTs shader is reproduced in ``lighting_grade`` (verified
  against the game's compiled shader) and the tonemapper runs in the compositor
  (``lighting_post``): exposure -> local exposure -> bloom -> vignette -> log
  shaper -> 32^3 LUT.
* Sky: physical sky rescaled to the scenario sun, camera/sky-light split, and
  the scenario's cloud layer texture (``lighting_sky``).
* Fog: ExponentialHeightFog as a finite volume box (World Volume would black
  out the sky).
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import bpy

from . import lighting_catalog as cat

_FOG_OBJ_NAME = "ArcLightingFogVolume"
_FOG_MAT_NAME = "ArcLightingFogVolume"
_FOG_SIZE_DEFAULT = (2000.0, 2000.0, 500.0)
_SUMMARY_CACHE: dict[tuple, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Enum callbacks (registered by properties.py)
# ---------------------------------------------------------------------------
def make_lighting_look_items(self, context):
    return cat.make_lighting_look_items(self, context)


def make_lighting_hdri_items(self, context):
    return cat.make_lighting_hdri_items(self, context)


def _most_common_scenario(map_id: str) -> str:
    scen = (cat.load_catalog().get("maps", {}).get(map_id) or {}).get("scenarios") or {}
    if not scen:
        return cat.NONE
    return max(scen.items(), key=lambda kv: float(kv[1].get("weight") or 0.0))[0]


def on_lighting_map_update(scene, context) -> None:
    del context
    try:
        # Enum values are stored as indices, so a new map always starts at its
        # most common scenario rather than whatever shares the old index.
        scene.arc_lighting_scenario = _most_common_scenario(getattr(scene, "arc_lighting_map", ""))
    except Exception as exc:
        print(f"Arc Lighting Look: map change failed ({exc})")


def on_lighting_scenario_update(scene, context) -> None:
    return None

# ---------------------------------------------------------------------------
# Scenario resolution
# ---------------------------------------------------------------------------
def _selection(scene) -> tuple[str, str, dict[str, Any] | None]:
    map_id = getattr(scene, "arc_lighting_map", "") or ""
    tag = getattr(scene, "arc_lighting_scenario", "") or ""
    return map_id, tag, cat.scenario(map_id, tag)


def resolve(scene, entry: dict[str, Any]) -> dict[str, Any]:
    """Final post-process settings + texture/curve references for a scenario (with overrides)."""
    from . import lighting_grade as grade

    settings = grade.blend_volumes(entry.get("volumes") or [])
    override = bool(getattr(scene, "arc_lighting_override", False))
    lut_pick = getattr(scene, "arc_lighting_look", cat.NONE) if override else cat.NONE
    if lut_pick and lut_pick != cat.NONE:
        settings["ColorGradingLUT"] = {"name": lut_pick, "path": cat.lut_path(lut_pick)}
        settings["ColorGradingIntensity"] = float(getattr(scene, "arc_lighting_lut_intensity", 1.0))
    lut = grade.lut_name(settings)
    curve_ref = settings.get("AutoExposureBiasCurve")
    curve = curve_ref.get("name") if isinstance(curve_ref, dict) else ""
    mask_ref = settings.get("AutoExposureMeterMask")
    mask = _path_of(mask_ref)
    # The sky sphere MID is what the shader samples (it falls back to the material's Cloud_Wisps
    # where the sky actor sets no texture); older catalogs only carry the actor fields.
    sky = entry.get("sky") or {}
    mid = entry.get("sky_material") or {}
    cloud_path = (mid.get("texture_paths") or {}).get("Cloud_Wisps_Texture") or ""
    cloud_opacity = (mid.get("scalars") or {}).get("Wispy Cloud Alpha")
    if not cloud_path:
        cloud_path = _path_of(sky.get("Cloud Wisps Texture"))
    if cloud_opacity is None:
        cloud_opacity = sky.get("Cloud Wisps Opacity")
    cloud_pick = getattr(scene, "arc_lighting_hdri", cat.NONE) if override else cat.NONE
    if cloud_pick and cloud_pick != cat.NONE:
        cloud = cat.sky_texture_refs().get(cloud_pick, "")
    else:
        cloud = cloud_path.split(".", 1)[0]
    return {"settings": settings, "lut": lut, "lut_path": cat.lut_path(lut) if lut else "",
            "curve": curve, "curve_keys": cat.load_catalog().get("curves", {}).get(curve) or [],
            "mask_path": mask, "cloud_path": cloud,
            "cloud_opacity": float(cloud_opacity or 0.0)}


def _path_of(ref) -> str:
    if isinstance(ref, dict):
        return (ref.get("path") or "").split(".", 1)[0]
    return ""


def summary(scene) -> dict[str, Any] | None:
    """Cheap, cached description of the selected scenario for the panel."""
    map_id, tag, entry = _selection(scene)
    if entry is None:
        return None
    key = (map_id, tag, bool(getattr(scene, "arc_lighting_override", False)),
           getattr(scene, "arc_lighting_look", ""), getattr(scene, "arc_lighting_hdri", ""),
           round(float(getattr(scene, "arc_lighting_lut_intensity", 1.0)), 3))
    hit = _SUMMARY_CACHE.get(key)
    if hit is not None:
        return hit
    from . import lighting_grade as grade
    from . import lighting_sky as sky

    r = resolve(scene, entry)
    s = r["settings"]
    kind, light = sky.primary_light(entry)
    sun_text = "no sun/moon above the horizon"
    if light is not None:
        elev = math.degrees(math.asin(max(-1.0, min(1.0, -float(light["forward"][2])))))
        temp = f" · {light['temperature']:.0f} K" if light.get("use_temperature") else ""
        sun_text = f"{kind.title()} {light['intensity']:g} lux · {elev:.0f}° up{temp}"
    lut = r["lut"].replace("RGBTable16x1_Kodak5218", "Kodak 5218").replace("_", " ").strip() if r["lut"] else "none"
    method = str(s.get("AutoExposureMethod", "")).rsplit("_", 1)[-1]
    le = grade.local_exposure_params(s)
    le_text = ""
    if not grade.local_exposure_is_identity(le):
        le_text = (f"Local exposure: highlights ×{le['HighlightContrastScale']:g} · shadows ×{le['ShadowContrastScale']:g}"
                   + (f" · detail ×{le['DetailStrength']:g}" if le["DetailStrength"] != 1.0 else "")
                   + (f" · grey {le['MiddleGreyBias']:+g} EV" if le["MiddleGreyBias"] else ""))
    info = {
        "lut": f"{lut} · {100.0 * float(s.get('ColorGradingIntensity') or 0.0):.0f}%" if r["lut"] else "no LUT strip",
        "white": (f"{float(s['WhiteTemp']):.0f} K" + (f" tint {float(s['WhiteTint']):+.2f}" if s.get("WhiteTint") else ""))
                 if abs(float(s.get("WhiteTemp", 6500.0)) - 6500.0) > 1e-3 or s.get("WhiteTint") else "",
        "exposure": f"{method} · EV100 {float(s['AutoExposureMinBrightness']):g} to {float(s['AutoExposureMaxBrightness']):g}"
                    f" · bias {float(s['AutoExposureBias']):+g}" + (" · curve" if r["curve_keys"] else ""),
        "sun": sun_text,
        "local_exposure": le_text,
        "clouds": (r["cloud_path"].rsplit("/", 1)[-1] + f" ×{r['cloud_opacity']:g}") if r["cloud_path"] and r["cloud_opacity"] > 0 else "none",
        "weight": entry.get("weight"),
    }
    if len(_SUMMARY_CACHE) > 64:
        _SUMMARY_CACHE.clear()
    _SUMMARY_CACHE[key] = info
    return info


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
def _mirror_y(scene) -> bool:
    try:
        from . import map_placement

        return bool(map_placement.map_mirror_y(scene))
    except Exception:
        return bool(getattr(scene, "arc_map_mirror_y", True))


def _lut_identity(settings: dict[str, Any], lut: str) -> str:
    from . import lighting_grade as grade

    cb0 = grade.producer_constants(settings)
    return hashlib.sha256(cb0.tobytes() + lut.encode()).hexdigest()[:24]


def apply_lighting_look(scene: bpy.types.Scene, look_id: str | None = None, *, allow_extract: bool = True,
                        **_legacy) -> tuple[bool, str]:
    """Apply the selected scenario. Returns (ok, message)."""
    del look_id
    from . import lighting_grade as grade
    from . import lighting_post as post
    from . import lighting_sky as sky

    map_id, tag, entry = _selection(scene)
    if entry is None:
        return False, "Pick a map and a lighting scenario first"
    r = resolve(scene, entry)
    s = r["settings"]
    do_grade = bool(getattr(scene, "arc_lighting_apply_lut", True))
    do_sky = bool(getattr(scene, "arc_lighting_apply_hdri", True))
    do_clouds = do_sky and bool(getattr(scene, "arc_lighting_apply_clouds", True))
    wanted = []
    if do_grade:
        wanted += [p for p in (r["lut_path"], r["mask_path"]) if p]
    if do_clouds and r["cloud_path"]:
        wanted.append(r["cloud_path"])
    if allow_extract:
        files, note = cat.ensure_textures(scene, wanted)
    else:
        files, note = {p: cat.find_texture(p) for p in wanted}, ""
    parts = [entry.get("label") or tag]
    warnings = [note] if note else []

    if do_grade:
        strip = None
        if r["lut"]:
            png = files.get(r["lut_path"], "")
            if not png:
                return False, (f"LUT strip {r['lut']} is not available: "
                               + (note or "click Apply to fetch it from the game files"))
            strip = cat.load_strip(png)
        lut = grade.combined_lut(s, strip)
        mask_png = files.get(r["mask_path"], "") if r["mask_path"] else ""
        mask = cat.load_mask_image(mask_png) if mask_png else None
        if r["mask_path"] and mask is None:
            warnings.append("meter mask missing: metering is uniform")
        auto = getattr(scene, "arc_lighting_exposure_mode", "AUTO") == "AUTO"
        info = post.configure(scene, s, lut=lut, lut_identity=_lut_identity(s, r["lut"]), curve_keys=r["curve_keys"],
                              mask_image=mask, auto_exposure=auto,
                              manual_ev100=float(getattr(scene, "arc_lighting_meter_ev", 2.0)),
                              compensation=float(getattr(scene, "arc_lighting_exposure_comp", 0.0)),
                              bloom=bool(getattr(scene, "arc_lighting_apply_bloom", True)),
                              vignette=bool(getattr(scene, "arc_lighting_apply_vignette", True)),
                              local_exposure=bool(getattr(scene, "arc_lighting_apply_local_exposure", True)))
        post.apply_color_management(scene)
        _enable_viewport_compositor()
        parts.append(f"LUT {r['lut'] or 'none'}")
        parts.append("auto exposure" if auto else f"meter locked EV100 {scene.arc_lighting_meter_ev:g}")
    else:
        post.detach(scene)
        info = {}

    if do_sky:
        cloud_img = None
        if do_clouds and r["cloud_path"]:
            png = files.get(r["cloud_path"], "")
            if png:
                cloud_img = cat.load_sky_image(png, r["cloud_path"].rsplit("/", 1)[-1])
            else:
                warnings.append("cloud texture missing")
        mirror = _mirror_y(scene)
        parts.append(sky.ensure_scenario_sky(scene, entry, mirror_y=mirror, cloud_image=cloud_img,
                                             cloud_time=float(getattr(scene, "arc_lighting_cloud_time", 0.0)),
                                             strength=float(getattr(scene, "arc_lighting_hdri_strength", 1.0))))
        parts.append(sky.ensure_sun(scene, entry, mirror_y=mirror, enabled=bool(getattr(scene, "arc_lighting_apply_sun", True))))
        _enable_scene_world(scene)

    fog = entry.get("fog") or {}
    color = fog.get("FogInscatteringLuminance") or fog.get("FogInscatteringColor") or {}
    atmos = {"fog_density": fog.get("FogDensity", 0.02),
             "fog_color": (float(color.get("R", 0.447)), float(color.get("G", 0.638)), float(color.get("B", 1.0)))
             if isinstance(color, dict) else (0.447, 0.638, 1.0)}
    fog_on = bool(getattr(scene, "arc_lighting_apply_fog", False))
    fog_status = _ensure_world_volume_fog(scene, atmos, enabled=fog_on)
    if fog_on:
        parts.append(fog_status)

    state = {"map": map_id, "scenario": tag, "lut": r["lut"], "grade": do_grade, "sky": do_sky,
             "warnings": warnings, **{k: v for k, v in info.items() if isinstance(v, (int, float, str))}}
    scene["arc_lighting_state"] = json.dumps(state)
    status = " · ".join(p for p in parts if p)
    if hasattr(scene, "arc_lighting_look_applied"):
        scene.arc_lighting_look_applied = status[:240]
    message = f"Applied: {status}" + (f" ({'; '.join(warnings)})" if warnings else "")
    return True, message


def clear_lighting_look(scene) -> str:
    from . import lighting_post as post

    post.detach(scene)
    if "arc_lighting_state" in scene:
        del scene["arc_lighting_state"]
    sun = bpy.data.objects.get("ArcLightingSun")
    if sun is not None:
        sun.hide_viewport = sun.hide_render = True
    prev = scene.get("arc_lighting_prev_world")
    if prev and bpy.data.worlds.get(prev) is not None:
        scene.world = bpy.data.worlds[prev]
    if "arc_lighting_prev_film_transparent" in scene:
        scene.render.film_transparent = scene["arc_lighting_prev_film_transparent"]
        del scene["arc_lighting_prev_film_transparent"]
    if hasattr(scene, "arc_lighting_look_applied"):
        scene.arc_lighting_look_applied = ""
    return "Lighting Look removed"


def sync_reference_compositor_mask(scene=None):
    from . import lighting_post as post

    post.sync_reference_compositor_mask(scene)


# ---------------------------------------------------------------------------
# Viewport helpers
# ---------------------------------------------------------------------------
def _enable_viewport_compositor() -> None:
    """Turn on the viewport compositor so the grade is visible without F12."""
    try:
        from . import reference_overlay

        protected = reference_overlay.ensure_enabled()
        has_references = any(o.get("arc_reference_plane") for o in bpy.context.scene.objects)
        for window in bpy.context.window_manager.windows:
            for area in (window.screen.areas if window.screen else ()):
                if area.type != "VIEW_3D":
                    continue
                for space in area.spaces:
                    if space.type == "VIEW_3D" and hasattr(space.shading, "use_compositor"):
                        # Image overlays draw after the viewport compositor; keep the
                        # safe fallback if their setup did not succeed.
                        space.shading.use_compositor = "ALWAYS" if protected or not has_references else "DISABLED"
    except Exception as exc:
        print(f"Arc Lighting Look: could not enable viewport compositor ({exc})")


def _enable_scene_world(scene=None) -> None:
    scene = scene or bpy.context.scene
    try:
        for window in bpy.context.window_manager.windows:
            if window.scene != scene:
                continue
            for area in (window.screen.areas if window.screen else ()):
                if area.type != "VIEW_3D":
                    continue
                for space in area.spaces:
                    if space.type == "VIEW_3D" and hasattr(space.shading, "use_scene_world"):
                        space.shading.use_scene_world = True
                        space.shading.use_scene_lights = True
                        space.shading.use_scene_world_render = True
                        space.shading.use_scene_lights_render = True
                        space.shading.studiolight_background_alpha = 1.0
                        if space.shading.type in {"SOLID", "WIREFRAME"}:
                            space.shading.type = "MATERIAL"
    except Exception as exc:
        print(f"Arc Lighting Look: could not enable Scene World ({exc})")


# ---------------------------------------------------------------------------
# Fog box (ExponentialHeightFog on a finite volume; World Volume blacks the sky)
# ---------------------------------------------------------------------------
def _fog_box_sizes(scene) -> tuple[float, float, float]:
    sx = float(getattr(scene, "arc_lighting_fog_size_x", _FOG_SIZE_DEFAULT[0]) or _FOG_SIZE_DEFAULT[0])
    sy = float(getattr(scene, "arc_lighting_fog_size_y", _FOG_SIZE_DEFAULT[1]) or _FOG_SIZE_DEFAULT[1])
    sz = float(getattr(scene, "arc_lighting_fog_size_z", _FOG_SIZE_DEFAULT[2]) or _FOG_SIZE_DEFAULT[2])
    return (max(0.1, sx), max(0.1, sy), max(0.1, sz))


def _fog_volume_density(atmos: dict[str, Any], *, path_length: float) -> float:
    """UE fog density -> Principled Volume density on the box (optical depth ~ density * 4)."""
    density = float(atmos.get("fog_density") or 0.0)
    return max(0.0, min(0.05, max(0.0, density) * 4.0 / max(float(path_length), 1.0)))


def _ensure_fog_volume_material(color, dens: float) -> bpy.types.Material:
    mat = bpy.data.materials.get(_FOG_MAT_NAME) or bpy.data.materials.new(_FOG_MAT_NAME)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (300, 0)
    vol = nodes.new("ShaderNodeVolumePrincipled")
    vol.label = "ArcLightingVolumeFog"
    vol.inputs["Color"].default_value = color
    vol.inputs["Density"].default_value = dens
    vol.inputs["Anisotropy"].default_value = 0.0
    links.new(vol.outputs["Volume"], out.inputs["Volume"])
    return mat


def _tune_eevee_volumes(scene, *, max_extent: float) -> None:
    eevee = getattr(scene, "eevee", None)
    if eevee is None:
        return
    end = max(50.0, float(max_extent) * 1.5)
    for attr, value in (("volumetric_end", end), ("volume_end", end), ("volumetric_sample_distribution", 0.8)):
        if hasattr(eevee, attr):
            try:
                setattr(eevee, attr, value)
            except Exception:
                pass


def _ensure_world_volume_fog(scene, atmos: dict[str, Any], *, enabled: bool) -> str:
    size_x, size_y, size_z = _fog_box_sizes(scene)
    path_len = max(size_x, size_y, size_z)
    obj = bpy.data.objects.get(_FOG_OBJ_NAME)
    if not enabled:
        if obj is not None:
            obj.hide_viewport = obj.hide_render = True
        return "fog: off"
    fc = atmos.get("fog_color") or (0.75, 0.82, 0.9)
    dens = _fog_volume_density(atmos, path_length=path_len)
    mat = _ensure_fog_volume_material((float(fc[0]), float(fc[1]), float(fc[2]), 1.0), dens)
    if obj is None:
        mesh = bpy.data.meshes.get(_FOG_OBJ_NAME)
        if mesh is None:
            mesh = bpy.data.meshes.new(_FOG_OBJ_NAME)
            verts = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
            faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
            mesh.from_pydata(verts, [], faces)
            mesh.update()
        obj = bpy.data.objects.new(_FOG_OBJ_NAME, mesh)
        scene.collection.objects.link(obj)
    obj.hide_viewport = obj.hide_render = False
    obj.display_type = "WIRE"
    obj.visible_shadow = False
    obj.location = (0.0, 0.0, size_z * 0.5)
    obj.scale = (size_x * 0.5, size_y * 0.5, size_z * 0.5)
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    _tune_eevee_volumes(scene, max_extent=path_len)
    return f"fog box density {dens:.5f}"


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def _section(layout, panel_id: str, title: str, toggle: str | None, scene, default_closed=True):
    panel_fn = getattr(layout, "panel", None)
    if callable(panel_fn):
        header, body = panel_fn(panel_id, default_closed=default_closed)
        if toggle and hasattr(scene, toggle):
            header.prop(scene, toggle, text=title)
        else:
            header.label(text=title)
        return body
    box = layout.box()
    if toggle and hasattr(scene, toggle):
        box.prop(scene, toggle, text=title)
    else:
        box.label(text=title)
    return box


def _state(scene) -> dict[str, Any]:
    try:
        return json.loads(scene.get("arc_lighting_state") or "{}")
    except Exception:
        return {}


def draw_lighting_look_box(layout, context) -> None:
    scene = context.scene
    col = layout.column()
    if not hasattr(scene, "arc_lighting_map") or not hasattr(scene, "arc_lighting_exposure_mode"):
        col.label(text="Lighting props missing — reload the add-on", icon="ERROR")
        return
    if not cat.map_ids():
        col.label(text="Lighting catalog missing from assets/", icon="ERROR")
        return

    col.prop(scene, "arc_lighting_map", text="Map", icon="WORLD")
    col.prop(scene, "arc_lighting_scenario", text="Scenario", icon="LIGHT_SUN")
    row = col.row(align=True)
    row.scale_y = 1.4
    row.operator("arc_outfits.apply_lighting_look", text="Apply Lighting Look", icon="CHECKMARK")
    from .ui import _collapsible
    col = _collapsible(col, "arc_lighting_advanced", "Advanced", default_closed=True)
    if col is None:
        return

    body = _section(col, "arc_lighting_color", "Game Color Grade", "arc_lighting_apply_lut", scene, default_closed=True)
    if body is not None:
        body.active = bool(scene.arc_lighting_apply_lut)
        body.prop(scene, "arc_lighting_exposure_mode", text="Exposure")
        row = body.row(align=True)
        if scene.arc_lighting_exposure_mode == "MANUAL":
            row.prop(scene, "arc_lighting_meter_ev", text="Meter EV100")
        row.prop(scene, "arc_lighting_exposure_comp", text="Comp")
        row = body.row(align=True)
        row.prop(scene, "arc_lighting_apply_bloom", text="Bloom", toggle=True)
        row.prop(scene, "arc_lighting_apply_vignette", text="Vignette", toggle=True)
        row.prop(scene, "arc_lighting_apply_local_exposure", text="Local Exp", toggle=True)

    body = _section(col, "arc_lighting_sky", "Sky & Sun", "arc_lighting_apply_hdri", scene)
    if body is not None:
        body.active = bool(scene.arc_lighting_apply_hdri)
        row = body.row(align=True)
        row.prop(scene, "arc_lighting_apply_sun", text="Sun Lamp", toggle=True)
        row.prop(scene, "arc_lighting_apply_clouds", text="Clouds", toggle=True)
        row = body.row(align=True)
        row.prop(scene, "arc_lighting_hdri_strength", text="Strength")
        row.prop(scene, "arc_lighting_cloud_time", text="Cloud Time")
        from . import lighting_sky
        lighting_sky.draw_sky_controls(body, scene)

    body = _section(col, "arc_lighting_fog", "Fog Box", "arc_lighting_apply_fog", scene)
    if body is not None:
        body.active = bool(scene.arc_lighting_apply_fog)
        row = body.row(align=True)
        row.prop(scene, "arc_lighting_fog_size_x", text="X")
        row.prop(scene, "arc_lighting_fog_size_y", text="Y")
        row.prop(scene, "arc_lighting_fog_size_z", text="Z")

    body = _section(col, "arc_lighting_override_panel", "Override LUT / Clouds", "arc_lighting_override", scene)
    if body is not None:
        body.active = bool(scene.arc_lighting_override)
        body.prop(scene, "arc_lighting_look", text="LUT")
        body.prop(scene, "arc_lighting_lut_intensity", text="LUT Intensity", slider=True)
        body.prop(scene, "arc_lighting_hdri", text="Clouds")
