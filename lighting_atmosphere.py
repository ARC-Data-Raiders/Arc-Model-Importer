"""Map + lighting-scenario atmosphere catalog (HeightFog / PostProcess).

Resolves bloom / fog (and unused dump fields AE/vignette) for ``Map`` × ``Lighting.*``
tags from Pioneer dump lighting levels, with curated JSON fallbacks.
Lighting Look Apply uses Scenario to pick Kodak LUT + HDRI; bloom is compositor,
fog is a finite volume mesh.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from . import utils

_ATMOS_CACHE: dict[str, Any] | None = None
_MAP_ENUM: list[tuple[str, str, str, int]] = [
    ("_NONE_", "(set Pioneer root)", "Point Settings → PioneerGame Folder at the dump", 0)
]
_SCENARIO_ENUM: list[tuple[str, str, str, int]] = [
    ("_NONE_", "(pick a map)", "Select a map with lighting scenarios", 0)
]

_TAG_RE = re.compile(r"^Lighting\.[A-Za-z0-9_]+$")
_HOME_LOOK_RE = re.compile(r"^Home_Lighting_([A-Za-z0-9_]+)\.json$", re.IGNORECASE)

# Kodak LUT look id → nearest Lighting.* tag (for reverse lookup / docs).
LUT_LOOK_TO_TAG: dict[str, str] = {
    "Default": "Lighting.DefaultL",
    "BrightDay_001": "Lighting.BuriedDay",
    "BrightOvercast_001": "Lighting.GoldenOvercast",
    "BuriedDay_001": "Lighting.BuriedDay",
    "Foggy_001": "Lighting.MistyMorning",
    "GoldenOvercast_001": "Lighting.GoldenOvercast",
    "GoldenOvercast_003": "Lighting.GoldenOvercast",
    "HazyDay_002": "Lighting.MistyMorning",
    "Night_001": "Lighting.Night",
    "OrangeSunset_001": "Lighting.RedDawn",
    "PinkMorning_001": "Lighting.MistyMorning",
    "PinkSunrise_001": "Lighting.RedDawn",
    "RedDawn_001": "Lighting.RedDawn",
    "SandStorm_001": "Lighting.Sandstorm",
    "Thunderstorm_001": "Lighting.Thunderstorm",
}

# Preferred Kodak look for each Lighting.* scenario (Scenario → Look on Apply).
# Home SunnyDay dumps use base RGBTable16x1_Kodak5218 (= Default LUT); studio_small_*
# on Home are reflection-capture cubemaps, not the outdoor sky presentation.
TAG_TO_LUT_LOOK: dict[str, str] = {
    "Lighting.DefaultL": "Default",
    "Lighting.Default": "Default",
    "Lighting.BuriedDay": "BuriedDay_001",
    "Lighting.GoldenOvercast": "GoldenOvercast_001",
    "Lighting.MistyMorning": "Foggy_001",
    "Lighting.Night": "Night_001",
    "Lighting.RedDawn": "RedDawn_001",
    "Lighting.Sandstorm": "SandStorm_001",
    "Lighting.Thunderstorm": "Thunderstorm_001",
    "Lighting.WindyStorm": "Thunderstorm_001",
    "Lighting.WinterSnowBC": "BrightOvercast_001",
    "Lighting.WinterSnow.BuriedCity": "BrightOvercast_001",
}

# Preferred World HDRI filename (under Lighting/HDRI) per scenario — separate from LUT.
# DefaultL uses outdoor sky, not studio_small (studio = Home reflection probes only).
TAG_TO_HDRI: dict[str, str] = {
    "Lighting.DefaultL": "hdri_sky_013.hdr",
    "Lighting.Default": "hdri_sky_013.hdr",
    "Lighting.BuriedDay": "hdri_sky_046.hdr",
    "Lighting.GoldenOvercast": "HFD_HemiSunset04.png",
    "Lighting.MistyMorning": "hdri_sky_072_8K_Sphere.hdr",
    "Lighting.Night": "VHDRI_Twilight_Stormy02.hdr",
    "Lighting.RedDawn": "HFD_Sunset11.png",
    "Lighting.Sandstorm": "hdri_sky_124.hdr",
    "Lighting.Thunderstorm": "VHDRI_Twilight_Stormy02.hdr",
    "Lighting.WindyStorm": "VHDRI_Twilight_Stormy02.hdr",
    "Lighting.WinterSnowBC": "HFD_HemiOvercast02.png",
    "Lighting.WinterSnow.BuriedCity": "HFD_HemiOvercast02.png",
}

# Home lighting file stem → Lighting.* tag
_HOME_STEM_TO_TAG: dict[str, str] = {
    "SunnyDay": "Lighting.DefaultL",
    "Rainy": "Lighting.WindyStorm",
    "Sunset": "Lighting.RedDawn",
    "WinterEvent": "Lighting.WinterSnowBC",
}


def _pioneer_content(pioneer_root: str | None = None) -> str:
    root = (pioneer_root or "").strip() or utils.get_pioneer_root()
    if not root:
        return ""
    content = utils.find_content_dir(root)
    if content:
        pioneer = os.path.join(content, "Pioneer")
        if os.path.isdir(pioneer):
            return pioneer
    # Root may already be Content/Pioneer
    if os.path.basename(os.path.normpath(root)).lower() == "pioneer" and os.path.isdir(root):
        return root
    return ""


def _atmospheres_json_path() -> str:
    return os.path.join(os.path.dirname(__file__), "assets", "lighting_atmospheres.json")


def load_atmosphere_fallbacks(force: bool = False) -> dict[str, Any]:
    global _ATMOS_CACHE
    if _ATMOS_CACHE is not None and not force:
        return _ATMOS_CACHE
    data: dict[str, Any] = {"tags": {}, "maps": {}}
    path = _atmospheres_json_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                if isinstance(raw.get("tags"), dict):
                    data["tags"] = raw["tags"]
                if isinstance(raw.get("maps"), dict):
                    data["maps"] = raw["maps"]
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Arc Lighting Atmosphere: failed to read {path}: {exc}")
    _ATMOS_CACHE = data
    return data


def normalize_lighting_tag(tag: str) -> str:
    t = (tag or "").strip()
    if not t or t == "_NONE_":
        return ""
    if t.startswith("Lighting."):
        return t
    # Accept bare stems
    return f"Lighting.{t}"


def tag_stem(tag: str) -> str:
    t = normalize_lighting_tag(tag)
    if t.startswith("Lighting."):
        return t[len("Lighting.") :]
    return t


def scan_lighting_maps(pioneer_root: str | None = None) -> list[dict[str, Any]]:
    """Maps with ``DA_*_LightingScenarios.json``, plus Home when lighting JSONs exist."""
    pioneer = _pioneer_content(pioneer_root)
    if not pioneer:
        return []
    maps_dir = os.path.join(pioneer, "Maps")
    items: list[dict[str, Any]] = []
    if os.path.isdir(maps_dir):
        for entry in sorted(os.listdir(maps_dir)):
            map_path = os.path.join(maps_dir, entry)
            if not os.path.isdir(map_path):
                continue
            da = _find_scenarios_da(map_path, entry)
            if da:
                items.append({"id": entry, "label": entry, "da_path": da, "kind": "da"})
            elif entry.lower() == "home":
                home_jsons = _list_home_lighting_jsons(map_path)
                if home_jsons:
                    items.append(
                        {
                            "id": "Home",
                            "label": "Home",
                            "da_path": "",
                            "kind": "home",
                            "home_dir": map_path,
                        }
                    )
        # Ensure Home appears even if sorted under Maps/Home without DA
        if not any(i["id"] == "Home" for i in items):
            home_dir = os.path.join(maps_dir, "Home")
            if _list_home_lighting_jsons(home_dir):
                items.insert(
                    0,
                    {
                        "id": "Home",
                        "label": "Home",
                        "da_path": "",
                        "kind": "home",
                        "home_dir": home_dir,
                    },
                )
    return items


def _find_scenarios_da(map_dir: str, map_id: str) -> str:
    candidates = [
        os.path.join(map_dir, f"DA_{map_id}_LightingScenarios.json"),
        os.path.join(map_dir, f"DA_{map_id}_01_LightingScenarios.json"),
    ]
    # Also glob DA_*_LightingScenarios.json
    try:
        for name in os.listdir(map_dir):
            if name.startswith("DA_") and name.endswith("_LightingScenarios.json"):
                candidates.append(os.path.join(map_dir, name))
    except OSError:
        pass
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def _list_home_lighting_jsons(home_dir: str) -> list[str]:
    if not os.path.isdir(home_dir):
        return []
    out = []
    for name in sorted(os.listdir(home_dir)):
        if _HOME_LOOK_RE.match(name) and "BuiltData" not in name:
            out.append(os.path.join(home_dir, name))
    return out


def _tags_from_da(da_path: str) -> list[str]:
    try:
        with open(da_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    tags: list[str] = []
    seen: set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            tag = o.get("TagName")
            if isinstance(tag, str) and tag.startswith("Lighting.") and tag not in seen:
                if "frigate" in tag.lower():
                    return
                if tag.count(".") <= 2:
                    seen.add(tag)
                    tags.append(tag)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    # Prefer exact Lighting.X (one dot); keep Lighting.X.Map variants (not frigate).
    primary = [t for t in tags if t.count(".") == 1]
    secondary = [t for t in tags if t.count(".") == 2 and "frigate" not in t.lower()]
    ordered = sorted(primary, key=str.lower) + sorted(secondary, key=str.lower)
    return ordered or sorted(tags, key=str.lower)


def scan_scenarios(map_id: str, pioneer_root: str | None = None) -> list[dict[str, Any]]:
    mid = (map_id or "").strip()
    if not mid or mid == "_NONE_":
        return []
    maps = {m["id"]: m for m in scan_lighting_maps(pioneer_root)}
    info = maps.get(mid)
    if not info:
        # Fuzzy: BuriedCity vs BuriedCity_01
        for k, m in maps.items():
            if k.lower() == mid.lower() or mid.lower() in k.lower() or k.lower() in mid.lower():
                info = m
                mid = k
                break
    if not info:
        return []
    if info.get("kind") == "home":
        items = []
        for path in _list_home_lighting_jsons(info.get("home_dir") or ""):
            name = os.path.basename(path)
            m = _HOME_LOOK_RE.match(name)
            stem = m.group(1) if m else ""
            tag = _HOME_STEM_TO_TAG.get(stem, f"Lighting.{stem}")
            items.append(
                {
                    "id": tag,
                    "label": f"{stem} ({tag})",
                    "home_stem": stem,
                    "json_path": path,
                }
            )
        return items
    da = info.get("da_path") or ""
    return [{"id": t, "label": t, "home_stem": "", "json_path": ""} for t in _tags_from_da(da)]


def _as_float(val: Any, default: float | None = None) -> float | None:
    if val is None:
        return default
    if isinstance(val, bool):
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _color_rgb(val: Any) -> tuple[float, float, float] | None:
    if not isinstance(val, dict):
        return None
    r = _as_float(val.get("R"), val.get("X"))
    g = _as_float(val.get("G"), val.get("Y"))
    b = _as_float(val.get("B"), val.get("Z"))
    if r is None or g is None or b is None:
        return None
    # Linear-ish UE colors can be >1; clamp soft
    return (max(0.0, min(4.0, r)), max(0.0, min(4.0, g)), max(0.0, min(4.0, b)))


def _extract_fog_from_component(props: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    dens = _as_float(props.get("FogDensity"))
    fall = _as_float(props.get("FogHeightFalloff"))
    start = _as_float(props.get("FogStartDistance"))
    wh = props.get("WorldHeightFogData")
    if isinstance(wh, dict):
        dens = dens if dens is not None else _as_float(wh.get("FogDensity"))
        fall = fall if fall is not None else _as_float(wh.get("FogHeightFalloff"))
        start = start if start is not None else _as_float(wh.get("FogStartDistance"))
    if dens is not None:
        out["fog_density"] = dens
    if fall is not None:
        out["fog_height_falloff"] = fall
    if start is not None:
        out["fog_start_distance"] = start
    for key in (
        "FogInscatteringLuminance",
        "FogInscatteringColor",
        "DirectionalInscatteringLuminance",
        "VolumetricFogAlbedo",
    ):
        col = _color_rgb(props.get(key))
        if col:
            out["fog_color"] = col
            break
    return out


def _extract_pp_settings(settings: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for src, dst in (
        ("BloomIntensity", "bloom_intensity"),
        ("BloomThreshold", "bloom_threshold"),
        ("BloomSizeScale", "bloom_size_scale"),
        ("AutoExposureBias", "auto_exposure_bias"),
        ("VignetteIntensity", "vignette_intensity"),
        ("ColorGradingIntensity", "color_grading_intensity"),
    ):
        v = _as_float(settings.get(src))
        if v is not None:
            out[dst] = v
    tint = _color_rgb(settings.get("SceneColorTint"))
    if tint:
        out["scene_tint"] = tint
    lut = settings.get("ColorGradingLUT")
    if isinstance(lut, dict):
        name = str(lut.get("ObjectName") or "")
        # Texture2D'RGBTable16x1_Kodak5218_RedDawn_001'
        if "RGBTable" in name or "Kodak" in name:
            out["color_grading_lut"] = name.split("'")[1] if "'" in name else name
    return out


def parse_lighting_level_json(path: str) -> dict[str, Any] | None:
    """Parse HeightFog + PostProcess + SkyLight cubemap angle from a lighting level dump."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, list):
        return None

    fog: dict[str, Any] = {}
    pp_global: dict[str, Any] = {}
    pp_main: dict[str, Any] = {}
    cubemap_angle: float | None = None

    for obj in data:
        if not isinstance(obj, dict):
            continue
        typ = str(obj.get("Type") or "")
        name = str(obj.get("Name") or "")
        label = str(obj.get("ActorLabel") or "")
        props = obj.get("Properties") if isinstance(obj.get("Properties"), dict) else {}

        if typ == "ExponentialHeightFogComponent" or "HeightFogComponent" in name:
            fog.update(_extract_fog_from_component(props))
        elif typ == "ExponentialHeightFog":
            pass
        elif typ == "PostProcessVolume":
            settings = props.get("Settings") if isinstance(props.get("Settings"), dict) else {}
            extracted = _extract_pp_settings(settings)
            if label == "PostProcess_Global" or name == "PostProcess_Global":
                pp_global = extracted
            elif label == "PostProcess_Main" or name == "PostProcess_Main":
                pp_main = extracted
            elif not pp_main and extracted:
                # Unlabeled volume (e.g. map PersistentLevel PPV) — keep as main candidate
                pp_main = extracted
        elif typ == "SkyLightComponent":
            ang = _as_float(props.get("SourceCubemapAngle"))
            if ang is not None:
                cubemap_angle = ang

    merged: dict[str, Any] = {}
    merged.update(pp_main)
    merged.update(pp_global)  # Global wins
    merged.update(fog)
    if cubemap_angle is not None:
        merged["source_cubemap_angle"] = cubemap_angle
    if not merged:
        return None
    merged["source_path"] = os.path.basename(path)
    return merged


def _find_map_lighting_json(pioneer: str, map_id: str, tag: str) -> str:
    """Best-effort ``*_Lighting_<Stem>.json`` under Maps/<map> (rare for raid maps)."""
    stem = tag_stem(tag)
    maps_dir = os.path.join(pioneer, "Maps", map_id)
    if not os.path.isdir(maps_dir):
        return ""
    patterns = [
        f"{map_id}_Lighting_{stem}.json",
        f"{map_id}_Lighting_{stem}.json",
    ]
    # Case-insensitive search
    want = f"_Lighting_{stem}.json".lower()
    try:
        for dirpath, _dirs, files in os.walk(maps_dir):
            if "DataLayers" in dirpath or "LightingScenarios" in dirpath or "DDGI" in dirpath:
                continue
            for name in files:
                if name.lower().endswith(want) and "builtdata" not in name.lower():
                    return os.path.join(dirpath, name)
    except OSError:
        pass
    for pat in patterns:
        cand = os.path.join(maps_dir, pat)
        if os.path.isfile(cand):
            return cand
    return ""


def _find_home_json_for_tag(pioneer: str, tag: str) -> str:
    home_dir = os.path.join(pioneer, "Maps", "Home")
    tag_n = normalize_lighting_tag(tag)
    # Direct stem match via reverse map
    for stem, mapped in _HOME_STEM_TO_TAG.items():
        if mapped == tag_n:
            path = os.path.join(home_dir, f"Home_Lighting_{stem}.json")
            if os.path.isfile(path):
                return path
    # Fuzzy: Lighting.Night → any Home file containing Night
    stem = tag_stem(tag_n).lower()
    for path in _list_home_lighting_jsons(home_dir):
        m = _HOME_LOOK_RE.match(os.path.basename(path))
        if m and m.group(1).lower() == stem:
            return path
    # Tag Lighting.DefaultL → SunnyDay
    if tag_n == "Lighting.DefaultL" or tag_n == "Lighting.Default":
        path = os.path.join(home_dir, "Home_Lighting_SunnyDay.json")
        if os.path.isfile(path):
            return path
    if "sandstorm" in stem:
        # No Home SandStorm — use Rainy as dusty/stormy stand-in only via fallback JSON
        return ""
    if "night" in stem:
        # No Home Night lighting level — fallback JSON
        return ""
    return ""


def _fallback_entry(map_id: str, tag: str) -> dict[str, Any]:
    fb = load_atmosphere_fallbacks()
    tag_n = normalize_lighting_tag(tag)
    maps = fb.get("maps") if isinstance(fb.get("maps"), dict) else {}
    tags = fb.get("tags") if isinstance(fb.get("tags"), dict) else {}
    map_over = maps.get(map_id) if isinstance(maps.get(map_id), dict) else {}
    if isinstance(map_over.get(tag_n), dict):
        return dict(map_over[tag_n])
    if isinstance(tags.get(tag_n), dict):
        return dict(tags[tag_n])
    # Case-insensitive tag
    for k, v in tags.items():
        if str(k).lower() == tag_n.lower() and isinstance(v, dict):
            return dict(v)
    # Lighting.WinterSnow.BuriedCity → Lighting.WinterSnowBC / Lighting.WinterSnow
    if tag_n.count(".") >= 2:
        base = ".".join(tag_n.split(".")[:2])
        if isinstance(tags.get(base), dict):
            return dict(tags[base])
        alt = base + "BC"
        if isinstance(tags.get(alt), dict):
            return dict(tags[alt])
        stem = tag_stem(base)
        for k, v in tags.items():
            if tag_stem(str(k)).lower().startswith(stem.lower()) and isinstance(v, dict):
                return dict(v)
    return {}


def _default_atmosphere() -> dict[str, Any]:
    return {
        "fog_density": 0.05,
        "fog_height_falloff": 10.0,
        "fog_start_distance": 2000.0,
        "fog_color": (0.75, 0.82, 0.9),
        "bloom_intensity": 1.0,
        "bloom_threshold": 1.0,
        "bloom_size_scale": 10.0,
        "auto_exposure_bias": 0.0,
        "vignette_intensity": 0.4,
        "source": "default",
    }


def resolve_atmosphere(
    map_id: str,
    tag: str,
    pioneer_root: str | None = None,
) -> dict[str, Any]:
    """Resolve atmosphere params. ``source`` is map_json | home_json | fallback | default."""
    result = _default_atmosphere()
    mid = (map_id or "").strip()
    tag_n = normalize_lighting_tag(tag)
    if not mid or mid == "_NONE_" or not tag_n:
        result["source"] = "default"
        return result

    pioneer = _pioneer_content(pioneer_root)
    parsed: dict[str, Any] | None = None
    source = "default"

    if pioneer:
        if mid == "Home":
            # Prefer scenario's own home json from scan
            for sc in scan_scenarios("Home", pioneer_root):
                if sc["id"] == tag_n and sc.get("json_path"):
                    parsed = parse_lighting_level_json(sc["json_path"])
                    source = "home_json"
                    break
            if parsed is None:
                path = _find_home_json_for_tag(pioneer, tag_n)
                if path:
                    parsed = parse_lighting_level_json(path)
                    source = "home_json"
        else:
            path = _find_map_lighting_json(pioneer, mid, tag_n)
            if path:
                parsed = parse_lighting_level_json(path)
                source = "map_json"
            if parsed is None:
                path = _find_home_json_for_tag(pioneer, tag_n)
                if path:
                    parsed = parse_lighting_level_json(path)
                    source = "home_json"

    fb = _fallback_entry(mid, tag_n)
    if parsed:
        result.update({k: v for k, v in parsed.items() if k != "source_path"})
        result["source"] = source
        result["source_file"] = parsed.get("source_path", "")
        # Fill missing from fallback
        for k, v in fb.items():
            if k not in result or result.get(k) is None:
                result[k] = v
    elif fb:
        result.update(fb)
        result["source"] = "fallback"
    else:
        result["source"] = "default"

    # Normalize fog_color to tuple
    fc = result.get("fog_color")
    if isinstance(fc, (list, tuple)) and len(fc) >= 3:
        result["fog_color"] = (float(fc[0]), float(fc[1]), float(fc[2]))
    elif not isinstance(result.get("fog_color"), tuple):
        tint = result.get("scene_tint")
        if isinstance(tint, (list, tuple)) and len(tint) >= 3:
            result["fog_color"] = (float(tint[0]), float(tint[1]), float(tint[2]))
        else:
            result["fog_color"] = (0.75, 0.82, 0.9)

    result["map_id"] = mid
    result["tag"] = tag_n
    return result


def make_lighting_map_items(self, context):
    del self
    root = ""
    if context and getattr(context, "scene", None):
        root = getattr(context.scene, "arc_pioneer_root", "") or ""
    maps = scan_lighting_maps(root) if root or utils.get_pioneer_root() else scan_lighting_maps()
    items: list[tuple[str, str, str, int]] = []
    if not maps:
        items.append(
            (
                "_NONE_",
                "(no lighting maps)",
                "Need Maps/*/DA_*_LightingScenarios.json or Home_Lighting_*.json",
                0,
            )
        )
    else:
        for i, m in enumerate(maps):
            tip = m.get("da_path") or m.get("kind") or ""
            items.append((m["id"], m["label"], str(tip)[:60], i))
    _MAP_ENUM[:] = items
    return _MAP_ENUM


def make_lighting_scenario_items(self, context):
    del self
    map_id = "_NONE_"
    root = ""
    if context and getattr(context, "scene", None):
        map_id = getattr(context.scene, "arc_lighting_map", "") or "_NONE_"
        root = getattr(context.scene, "arc_pioneer_root", "") or ""
        # Prefer placement map when lighting map empty
        if map_id in ("", "_NONE_") and getattr(context.scene, "arc_placement_map", ""):
            map_id = context.scene.arc_placement_map
    scenarios = scan_scenarios(map_id, root)
    items: list[tuple[str, str, str, int]] = []
    if not scenarios:
        items.append(
            (
                "_NONE_",
                "(no scenarios)",
                "Pick a map with DA_*_LightingScenarios or Home lighting looks",
                0,
            )
        )
    else:
        for i, sc in enumerate(scenarios):
            items.append((sc["id"], sc["label"], sc["id"], i))
    _SCENARIO_ENUM[:] = items
    return _SCENARIO_ENUM


def look_id_from_color_grading_lut(lut_ref: str) -> str:
    """``RGBTable16x1_Kodak5218_RedDawn_001`` / ObjectName → look id ``RedDawn_001``."""
    raw = (lut_ref or "").strip()
    if not raw:
        return ""
    # Texture2D'RGBTable16x1_Kodak5218_RedDawn_001' or bare name
    if "'" in raw:
        parts = raw.split("'")
        raw = parts[1] if len(parts) > 1 else raw
    raw = raw.replace("\\", "/").split("/")[-1]
    if raw.lower().endswith(".png"):
        raw = raw[:-4]
    m = re.match(r"^RGBTable16x1_Kodak5218(?:_(.+))?$", raw, re.IGNORECASE)
    if not m:
        return ""
    stem = (m.group(1) or "").strip()
    return stem if stem else "Default"


def suggest_lut_for_tag(tag: str) -> str:
    """Best Kodak look id for a Lighting.* tag (empty if none)."""
    tag_n = normalize_lighting_tag(tag)
    if not tag_n:
        return ""
    preferred = TAG_TO_LUT_LOOK.get(tag_n)
    if preferred:
        return preferred
    # Case-insensitive / WinterSnow.* variants
    for k, look_id in TAG_TO_LUT_LOOK.items():
        if k.lower() == tag_n.lower():
            return look_id
    if tag_n.count(".") >= 2:
        base = ".".join(tag_n.split(".")[:2])
        if base in TAG_TO_LUT_LOOK:
            return TAG_TO_LUT_LOOK[base]
    stem = tag_stem(tag_n).lower()
    for look_id, mapped in LUT_LOOK_TO_TAG.items():
        if tag_stem(mapped).lower() == stem:
            return look_id
        if stem and stem in look_id.lower():
            return look_id
    return ""


def suggest_hdri_for_tag(tag: str) -> str:
    """Preferred HDRI filename under Lighting/HDRI for a Lighting.* tag (empty if none)."""
    tag_n = normalize_lighting_tag(tag)
    if not tag_n:
        return ""
    if tag_n in TAG_TO_HDRI:
        return TAG_TO_HDRI[tag_n]
    for k, name in TAG_TO_HDRI.items():
        if k.lower() == tag_n.lower():
            return name
    if tag_n.count(".") >= 2:
        base = ".".join(tag_n.split(".")[:2])
        if base in TAG_TO_HDRI:
            return TAG_TO_HDRI[base]
    return ""
