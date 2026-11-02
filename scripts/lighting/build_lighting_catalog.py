"""Build assets/lighting_scenarios.json from static pak reads (no game process).

Inputs are the bounded JSON dumps written by ``scripts/lighting/probe`` (see
README.md next to this file):

  <evidence>/dump-<Map>/*.json   lighting exports per World Partition cell
  <evidence>/cells-<Map>.json    runtime cells + data layer instances
  <evidence>/dump-Home/*.json    Raider Den lighting levels
  <evidence>/curves.json         every Lighting/Curves package (CurveFloat keys)

Chain: LevelLightingScenariosDataAsset (tag -> DataLayerAsset, weight) ->
DataLayerInstanceWithAsset -> streaming cells carrying that layer -> lighting
actors in those cells. Post-process overrides keep only bOverride_* fields; a
null value means the override flag is set and the engine default applies.

Usage: python build_lighting_catalog.py <evidence dir> <out json> <build id>
"""
from __future__ import annotations

import collections
import json
import math
import pathlib
import re
import sys

MAPS = ["BuriedCity_01", "FrozenTrail_01", "MountainCompound", "RivenTides_01", "Spaceport_01",
        "TheBlueGate_01", "TheDam_02"]
LABELS = {"BuriedCity_01": "Buried City", "FrozenTrail_01": "Frozen Trail", "MountainCompound": "Mountain Compound",
          "RivenTides_01": "Riven Tides", "Spaceport_01": "Spaceport", "TheBlueGate_01": "The Blue Gate",
          "TheDam_02": "The Dam", "Home": "Raider Den"}
# Engine class defaults for fields an instance leaves at the archetype value.
DIRECTIONAL_DEFAULTS = {"Intensity": 10.0, "LightColor": {"R": 255, "G": 255, "B": 255, "A": 255},
                        "Temperature": 6500.0, "bUseTemperature": False, "LightSourceAngle": 0.5357}
SKYLIGHT_DEFAULTS = {"Intensity": 1.0, "LightColor": {"R": 255, "G": 255, "B": 255, "A": 255},
                     "IndirectLightingIntensity": 1.0}
SKY_FIELDS = ("Cloud Wisps Texture", "Cloud Wisps Opacity", "Wispy Cloud Color", "Cloud Coverage",
              "Sun Angle", "Sun Yaw", "TimeOfDay", "Stars Texture", "Stars_Color", "Sun Radius",
              "Sun Disk Intensity", "Global Sky Color", "Global Sun Color", "Moon Intensity", "Moon Color")
# Pioneer_Sky SCS template mesh for Ultra_Dynamic_Sky_Sphere (instances override it rarely).
SKY_SPHERE_MESH = "SM_Ultra_Dynamic_Sky_Sphere"


def ref(v):
    if isinstance(v, dict):
        if "ObjectName" in v:
            name = v["ObjectName"]
            return {"name": name.split("'")[1].split(".")[-1].split(":")[-1] if "'" in name else name,
                    "class": name.split("'")[0], "path": v.get("ObjectPath", "")}
        if "AssetPathName" in v:
            return {"path": v["AssetPathName"], "name": v["AssetPathName"].rsplit("/", 1)[-1].split(".")[0]} \
                if v["AssetPathName"] else None
    return v


def clean(v):
    if isinstance(v, dict):
        if "ObjectName" in v or "AssetPathName" in v:
            return ref(v)
        return {k: clean(x) for k, x in v.items() if k != "Hex"}
    if isinstance(v, list):
        return [clean(x) for x in v]
    return v


def pp_overrides(settings):
    out = {}
    for k, v in settings.items():
        if k.startswith("bOverride_") and v:
            name = k[len("bOverride_"):]
            if name in ("AutoExposureBiasBackup",):
                continue
            out[name] = clean(settings[name]) if name in settings else None
    return dict(sorted(out.items()))


def rot_matrix(r):
    """FRotationMatrix rows (X, Y, Z axes) for a UE rotator in degrees."""
    p, y, ro = (math.radians(float((r or {}).get(k) or 0.0)) for k in ("Pitch", "Yaw", "Roll"))
    sp, cp, sy, cy, sr, cr = math.sin(p), math.cos(p), math.sin(y), math.cos(y), math.sin(ro), math.cos(ro)
    return [[cp * cy, cp * sy, sp],
            [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
            [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp]]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def world_forward(item, by_name):
    """Compose RelativeRotation through the AttachParent chain; return the world X axis."""
    m = rot_matrix(item["Properties"].get("RelativeRotation"))
    parent = item["Properties"].get("AttachParent")
    seen = set()
    while isinstance(parent, dict) and parent.get("ObjectName"):
        name = ref(parent)["name"]
        node = by_name.get(name)
        if node is None or name in seen:
            break
        seen.add(name)
        m = mat_mul(m, rot_matrix(node["Properties"].get("RelativeRotation")))
        parent = node["Properties"].get("AttachParent")
    return [round(c, 7) for c in m[0]]


def light_entry(item, by_name, template):
    p = dict(DIRECTIONAL_DEFAULTS)
    p.update({k: v for k, v in (template or {}).items() if v is not None})
    p.update({k: v for k, v in item["Properties"].items() if v is not None})
    color = p.get("LightColor") or DIRECTIONAL_DEFAULTS["LightColor"]
    return {"forward": world_forward(item, by_name),
            "intensity": float(p["Intensity"]),
            "light_color": [int(color.get(c, 255)) for c in "RGB"],
            "use_temperature": bool(p.get("bUseTemperature")),
            "temperature": float(p.get("Temperature") or 6500.0),
            "source_angle": float(p.get("LightSourceAngle") or 0.5357),
            "atmosphere_sun": bool(p.get("bAtmosphereSunLight", True))}


def sky_light_entry(props):
    p = dict(SKYLIGHT_DEFAULTS)
    p.update({k: v for k, v in props.items() if v is not None})
    color = p.get("LightColor") or SKYLIGHT_DEFAULTS["LightColor"]
    return {"intensity": float(p["Intensity"]), "light_color": [int(color.get(c, 255)) for c in "RGB"],
            "indirect": float(p.get("IndirectLightingIntensity") or 1.0),
            "cubemap": (clean(p.get("Cubemap")) or {}).get("name"), "cubemap_angle": p.get("SourceCubemapAngle")}


def collect_cell(items, templates):
    by_name = {it.get("Name"): it for it in items}
    res = collections.defaultdict(list)
    for it in items:
        t = it.get("Type", "")
        p = it.get("Properties", {})
        name = it.get("Name", "")
        if t == "PostProcessVolume":
            if p.get("bEnabled") is False:
                continue
            res["volumes"].append({"name": name, "unbound": bool(p.get("bUnbound", False)),
                                   "priority": float(p.get("Priority", 0.0) or 0.0),
                                   "blend_weight": float(p.get("BlendWeight", 1.0) if p.get("BlendWeight") is not None else 1.0),
                                   "overrides": pp_overrides(p.get("Settings", {}))})
        elif t == "PostProcessComponent":
            if p.get("bEnabled") is False:
                continue
            st = p.get("Settings", {})
            if any(k.startswith("bOverride_") and v for k, v in st.items()):
                res["volumes"].append({"name": name, "unbound": bool(p.get("bUnbound", True)),
                                       "priority": float(p.get("Priority", 0.0) or 0.0),
                                       "blend_weight": float(p.get("BlendWeight", 1.0) if p.get("BlendWeight") is not None else 1.0),
                                       "overrides": pp_overrides(st)})
        elif t == "DirectionalLightComponent" and not name.endswith("_GEN_VARIABLE"):
            key = "moon" if name.lower().startswith("moon") else "sun"
            res[key].append(light_entry(it, by_name, templates.get(name)))
        elif t == "SkyLightComponent" and not name.endswith("_GEN_VARIABLE"):
            res["sky_light"].append(sky_light_entry(p))
        elif t.startswith("Pioneer_Sky") and not name.startswith("Default__"):
            res["sky"].append({k: clean(p.get(k)) for k in SKY_FIELDS if p.get(k) is not None})
        elif t == "ExponentialHeightFogComponent":
            res["fog"].append({k: clean(v) for k, v in p.items()
                               if k not in ("AttachParent", "UCSSerializationIndex", "bNetAddressable", "CreationMethod")})
        elif t == "SkyAtmosphereComponent":
            res["atmosphere"].append({k: clean(v) for k, v in p.items()
                                      if k not in ("AttachParent", "UCSSerializationIndex", "bNetAddressable", "CreationMethod")})
        elif t == "MaterialInstanceDynamic" and "Ultra_Dynamic_Sky" in name:
            tex = {x["ParameterInfo"]["Name"]: (clean(x.get("ParameterValue")) or {}).get("name")
                   for x in p.get("TextureParameterValues", [])}
            sca = {x["ParameterInfo"]["Name"]: x.get("ParameterValue") for x in p.get("ScalarParameterValues", [])}
            vec = {x["ParameterInfo"]["Name"]: [float((x.get("ParameterValue") or {}).get(c, 0.0)) for c in "RGBA"]
                   for x in p.get("VectorParameterValues", [])}
            paths = {x["ParameterInfo"]["Name"]: (clean(x.get("ParameterValue")) or {}).get("path")
                     for x in p.get("TextureParameterValues", [])}
            res["sky_material"].append({"textures": tex, "texture_paths": paths, "scalars": sca, "vectors": vec})
        elif t == "StaticMeshComponent" and name == "Ultra_Dynamic_Sky_Sphere":
            # The sky sphere's UV channel 5 carries the cloud-wisps projection; its world X axis orients it.
            res["sky_sphere"].append({"x_axis": world_forward(it, by_name),
                                      "mesh": (clean(p.get("StaticMesh")) or {}).get("name") or SKY_SPHERE_MESH,
                                      "visible": bool(p.get("bVisible", False)),
                                      "location": clean(p.get("RelativeLocation")),
                                      "scale": clean(p.get("RelativeScale3D"))})
    return res


def load_dump(folder):
    cells = {}
    for f in sorted(folder.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        cells[d["package"]] = d["items"]
    return cells


def templates_from(cells):
    """SCS template overrides (e.g. Sun_GEN_VARIABLE) keyed by component name."""
    out = {}
    for items in cells.values():
        for it in items:
            name = it.get("Name", "")
            if name.endswith("_GEN_VARIABLE") and it.get("Type") == "DirectionalLightComponent":
                out[name[: -len("_GEN_VARIABLE")]] = it.get("Properties", {})
    return out


def finish(payload):
    """One entry per scenario: first non-empty record per kind, volumes kept whole."""
    entry = {"volumes": [v for v in payload.get("volumes", []) if v["unbound"]]}
    for key in ("sun", "moon", "sky_light", "sky", "fog", "atmosphere", "sky_material", "sky_sphere"):
        if payload.get(key):
            entry[key] = payload[key][0]
    return entry


def label_for(tag):
    stem = tag.split(".", 1)[-1]
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", stem.replace(".", " ").replace("_", " "))
    return " ".join(w[:1].upper() + w[1:] for w in words.split())


def main():
    evidence = pathlib.Path(sys.argv[1])
    out_path = pathlib.Path(sys.argv[2])
    build = sys.argv[3] if len(sys.argv) > 3 else ""
    curves_raw = json.loads((evidence / "curves.json").read_text(encoding="utf-8"))
    curves = {}
    for pkg, exports in curves_raw.items():
        for ex in exports:
            if ex.get("Type") == "CurveFloat":
                keys = ex.get("Properties", {}).get("FloatCurve", {}).get("Keys", [])
                curves[ex["Name"]] = [{k: key.get(k) for k in ("InterpMode", "Time", "Value", "ArriveTangent", "LeaveTangent")}
                                      for key in keys]
    catalog = {"schema": "dr.lighting-scenarios/2", "build_id": build,
               "source": "Static pak read: LevelLightingScenariosDataAsset -> data layer -> World Partition cells -> lighting actors.",
               "notes": "volumes: unbound PostProcessVolumes/Components only; overrides list bOverride_* fields, null = engine default. "
                        "sun/moon forward = world X axis of the light in Unreal coordinates (light travels along it).",
               "curves": {}, "maps": {}}
    used_curves = set()

    def note_curves(entry):
        for vol in entry.get("volumes", []):
            c = (vol["overrides"].get("AutoExposureBiasCurve") or {})
            if isinstance(c, dict) and c.get("name"):
                used_curves.add(c["name"])

    for m in MAPS:
        cells = load_dump(evidence / f"dump-{m}")
        templates = templates_from(cells)
        table = json.loads((evidence / f"cells-{m}.json").read_text(encoding="utf-8"))
        inst_asset = {x["Name"]: (ref((x.get("Properties") or {}).get("DataLayerAsset")) or {}).get("name", "")
                      for x in table if x.get("Type") == "DataLayerInstanceWithAsset"}
        cell_layers = {}
        for x in table:
            if x.get("Type") == "WorldPartitionRuntimeLevelStreamingCell":
                dl = ((x.get("Properties") or {}).get("DataLayers") or {}).get("DataLayers") or []
                cell_layers[x["Name"]] = [inst_asset.get(n, n) for n in dl]
        da = next((it for items in cells.values() for it in items if it.get("Type") == "LevelLightingScenariosDataAsset"), None)
        scen, layer_to_tag = {}, {}
        for e in (da or {}).get("Properties", {}).get("LightingScenarios", []):
            tag = e["Key"]["TagName"]
            val = e["Value"]
            layer = (ref(val.get("DataLayer")) or {}).get("name", "")
            scen[tag] = {"label": label_for(tag), "weight": val.get("Weight"),
                         "filter_tags": [t.get("TagName") if isinstance(t, dict) else t for t in (val.get("FilterTags") or [])],
                         "payload": collections.defaultdict(list)}
            layer_to_tag[layer] = tag
        for pkg, items in cells.items():
            cell = pkg.split("/")[-1]
            payload = collect_cell(items, templates)
            tags = [layer_to_tag[l] for l in cell_layers.get(cell, []) if l in layer_to_tag]
            for t in tags:
                for k, v in payload.items():
                    scen[t]["payload"][k].extend(v)
        entries = {}
        for tag, s in sorted(scen.items()):
            entry = finish(s["payload"])
            if not entry.get("volumes") and "sun" not in entry:
                continue  # scenario without a streaming cell in this build
            entry = {"label": s["label"], "weight": s["weight"], "filter_tags": s["filter_tags"], **entry}
            note_curves(entry)
            entries[tag] = entry
        catalog["maps"][m] = {"label": LABELS[m], "scenarios": entries}

    home = load_dump(evidence / "dump-Home")
    templates = templates_from(home)
    entries = {}
    for pkg, items in sorted(home.items()):
        level = pkg.split("/")[-1]
        entry = finish(collect_cell(items, templates))
        if not entry.get("volumes"):
            continue  # interior base level: only bounded volumes
        name = level.replace("Home_Lighting_", "")
        entry = {"label": label_for(name), "weight": None, "filter_tags": [], "level": level, **entry}
        note_curves(entry)
        entries[level] = entry
    catalog["maps"]["Home"] = {"label": LABELS["Home"], "scenarios": entries}
    catalog["curves"] = {k: curves[k] for k in sorted(used_curves) if k in curves}
    missing = sorted(used_curves - set(curves))
    if missing:
        catalog["missing_curves"] = missing
    out_path.write_text(json.dumps(catalog, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    for m, d in catalog["maps"].items():
        print(m, len(d["scenarios"]), sorted(d["scenarios"]))
    print("curves", len(catalog["curves"]), "missing", missing, "bytes", out_path.stat().st_size)


if __name__ == "__main__":
    main()
