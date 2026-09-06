"""Import a cooked Niagara system (NS_*) into the scene as inspectable data.

Reads one exported ``NS_*.json`` and builds what the cook actually states: a root
empty per system, one empty per emitter, one object per renderer with the real
``.pskx`` where a mesh is named, and every binding fact attached as an ``nsir_*``
custom property.

Two things are deliberately different from a Niagara playback:

*   **No invented motion.** Module inputs (spawn rate, lifetime, velocity) are
    compiled into VectorVM / DXBC and are absent from the cook, so particle
    movement cannot be recovered from JSON and is not guessed.
*   **Real curves.** Curve data interfaces carry the baked ``ShaderLUT`` the GPU
    sampled, so those become genuine F-curves (see ``niagara_curves``).

The JSON must come from a property export that resolves Niagara data-interface
classes from mappings. FModel's own export silently drops curve tables; dump with::

    CUE4Parse.Example niagara-ns-dump --out-dir <dir>

What each system is still missing is reported back to the caller rather than
papered over.
"""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import bpy

from . import niagara_curves

RENDERER_TYPES = {
    "NiagaraSpriteRendererProperties": "sprite",
    "NiagaraMeshRendererProperties": "mesh",
    "NiagaraRibbonRendererProperties": "ribbon",
    "NiagaraLightRendererProperties": "light",
    "NiagaraComponentRendererProperties": "component",
    "NiagaraDecalRendererProperties": "decal",
}

SIM_TARGET = {0: "CPUSim", 1: "GPUComputeSim"}

# Byte width, little-endian format and component count for the POD parameter
# types that appear in InstanceParamStore.ParameterData.
PARAM_CODEC = {
    "NiagaraFloat": (4, "<f", 1),
    "NiagaraInt32": (4, "<i", 1),
    "NiagaraBool": (4, "<i", 1),
    "Vector2f": (8, "<2f", 2),
    "Vector3f": (12, "<3f", 3),
    "NiagaraPosition": (12, "<3f", 3),
    "Vector4f": (16, "<4f", 4),
    "Quat4f": (16, "<4f", 4),
    "LinearColor": (16, "<4f", 4),
}

# Data-interface fields that carry real payload rather than editor state.
DI_FIELDS = (
    "Field", "bTileX", "bTileY", "bTileZ",
    "EmitterName", "AttributeName",
    "MaxNeighborsPerCell", "NumCells", "CellSize",
    "FloatData", "InternalFloatData", "BoolData",
    "IntData", "ColorData", "PositionData",
    "StaticMesh", "SourceMode", "Texture",
    "SplineUserParameter", "EmitterProperties",
    "ShaderLUT", "LUTMinTime", "LUTMaxTime", "LUTInvTimeRange",
    "LUTNumSamplesMinusOne", "bUseLUT",
    "SpriteRendererName", "MeshRendererName",
)

# Unreal centimetres to Blender metres.
UE_TO_M = 0.01


# --------------------------------------------------------------------------- #
# Cooked JSON -> intermediate representation
# --------------------------------------------------------------------------- #

def load_exports(path):
    """FModel writes either {Exports:[...], Metadata:{}} or a bare export list."""
    with open(path, encoding="utf-8") as handle:
        doc = json.load(handle)
    if isinstance(doc, list):
        return [e for e in doc if isinstance(e, dict)]
    if isinstance(doc, dict) and isinstance(doc.get("Exports"), list):
        return [e for e in doc["Exports"] if isinstance(e, dict)]
    return []


def obj_ref(value):
    """Normalise an object reference to {class, name, path}."""
    if not isinstance(value, dict):
        return None
    name = value.get("ObjectName")
    path = value.get("ObjectPath")
    if not isinstance(name, str) and not isinstance(path, str):
        return None
    cls = None
    if isinstance(name, str) and "'" in name:
        head, _, tail = name.partition("'")
        cls, name = head, tail.rstrip("'")
    return {"class": cls, "name": name, "path": path}


def ref_leaf(value):
    """Short object name, dropping any Package:Sub prefix."""
    ref = obj_ref(value)
    if not ref or not isinstance(ref.get("name"), str):
        return None
    return ref["name"].split(":")[-1]


def owning_emitter(export):
    """Emitter export that owns this export, following one script hop.

    Data interfaces are outered to a script (``<emitter>.SpawnScript``), so the
    emitter is the segment before the last dot.
    """
    leaf = ref_leaf(export.get("Outer"))
    if not leaf:
        return None
    return leaf.rsplit(".", 1)[0] if "." in leaf else leaf


def renderer_ir(export):
    props = export.get("Properties") or {}
    kind = RENDERER_TYPES.get(export.get("Type"), "unknown")

    meshes = []
    for entry in props.get("Meshes") or []:
        if not isinstance(entry, dict):
            continue
        ref = obj_ref(entry.get("Mesh"))
        param = (((entry.get("MeshParameterBinding") or {})
                  .get("ResolvedParameter") or {}).get("Name"))
        meshes.append({
            "mesh": ref["name"] if ref else None,
            "path": ref["path"] if ref else None,
            "meshParam": None if param in (None, "None") else param,
            "scale": entry.get("Scale"),
            "pivotOffset": entry.get("PivotOffset"),
        })

    materials = []
    main = obj_ref(props.get("Material"))
    if main and main.get("name"):
        materials.append({"material": main["name"], "path": main["path"],
                          "source": "Material"})
    for entry in props.get("OverrideMaterials") or []:
        if not isinstance(entry, dict):
            continue
        ref = obj_ref(entry.get("ExplicitMat"))
        if ref and ref.get("name"):
            materials.append({"material": ref["name"], "path": ref["path"],
                              "source": "OverrideMaterials"})
        bound = (((entry.get("UserParamBinding") or {}).get("Parameter") or {})
                 .get("Name"))
        if bound and bound != "None":
            materials.append({"materialParam": bound,
                              "source": "UserParamBinding"})

    bindings = {}
    for key, value in props.items():
        if not key.endswith("Binding") or not isinstance(value, dict):
            continue
        bound = ((value.get("BoundVariable") or {}).get("Name")
                 or (value.get("ResolvedParameter") or {}).get("Name")
                 or value.get("Name"))
        if bound and bound != "None":
            bindings[key] = bound

    return {
        "kind": kind,
        "type": export.get("Type"),
        "name": export.get("Name"),
        "meshes": meshes,
        "materials": materials,
        "bindings": bindings,
        "scalars": {k: v for k, v in props.items()
                    if isinstance(v, (int, float, str, bool))
                    and not k.endswith("Binding")},
    }


def di_ir(export):
    props = export.get("Properties") or {}
    payload = {}
    for key in DI_FIELDS:
        if key not in props:
            continue
        value = props[key]
        ref = obj_ref(value) if isinstance(value, dict) else None
        if ref:
            payload[key] = {"asset": ref["name"], "path": ref["path"]}
        elif isinstance(value, dict):
            nested = ((value.get("Parameter") or {}).get("Name")
                      or value.get("Name"))
            payload[key] = nested if nested else value
        else:
            payload[key] = value
    return {
        "class": export.get("Type"),
        "name": export.get("Name"),
        "payload": payload,
    }


def emitter_ir(export, renderers_by_outer):
    props = export.get("Properties") or {}
    versions = props.get("VersionData") or []
    version = versions[0] if isinstance(versions, list) and versions else {}

    stages = [leaf for leaf in
              (ref_leaf(s) for s in version.get("SimulationStages") or []) if leaf]

    return {
        "name": props.get("UniqueEmitterName") or export.get("Name"),
        "export": export.get("Name"),
        "simTarget": SIM_TARGET.get(version.get("SimTarget"), version.get("SimTarget")),
        "localSpace": version.get("bLocalSpace", False),
        "determinism": version.get("bDeterminism", False),
        "randomSeed": version.get("RandomSeed"),
        "fixedBounds": version.get("FixedBounds"),
        "allocationMode": version.get("AllocationMode"),
        "preAllocationCount": version.get("PreAllocationCount"),
        "simulationStages": stages,
        "renderers": renderers_by_outer.get(export.get("Name"), []),
    }


def decode_param(data, offset, type_name):
    """Decode one exposed-parameter default out of the ParameterData blob."""
    codec = PARAM_CODEC.get(type_name)
    if codec is None or not isinstance(offset, int):
        return None
    width, fmt, count = codec
    if offset < 0 or offset + width > len(data):
        return None
    values = struct.unpack_from(fmt, data, offset)
    if type_name == "NiagaraBool":
        return bool(values[0])
    return values[0] if count == 1 else list(values)


def user_params(system_props):
    """User.* parameters with defaults decoded from the cooked byte blob."""
    store = ((system_props.get("SystemCompiledData") or {})
             .get("InstanceParamStore") or {})
    raw = store.get("ParameterData")
    data = bytes(v & 0xFF for v in raw) if isinstance(raw, list) else b""

    objects = []
    for entry in store.get("UObjects") or []:
        ref = obj_ref(entry)
        objects.append(ref["name"] if ref else None)

    params = []
    for entry in store.get("SortedParameterOffsets") or []:
        if not isinstance(entry, dict):
            continue
        type_ref = obj_ref((entry.get("TypeDef") or {}).get("ClassStructOrEnum"))
        type_name = type_ref["name"] if type_ref else None
        offset = entry.get("Offset")
        object_default = None
        if (type_name not in PARAM_CODEC and isinstance(offset, int)
                and 0 <= offset < len(objects)):
            object_default = objects[offset]
        params.append({
            "name": entry.get("Name"),
            "type": type_name,
            "default": decode_param(data, offset, type_name),
            "objectDefault": object_default,
        })
    return params


def system_ir(json_path):
    """Binding-level description of one cooked Niagara system."""
    exports = load_exports(json_path)
    if not exports:
        raise ValueError(f"no exports in {Path(json_path).name}")

    renderers_by_outer = {}
    di_by_emitter = {}
    system_export = None
    emitter_exports = []

    for export in exports:
        kind = export.get("Type") or ""
        if kind == "NiagaraSystem":
            system_export = export
        elif kind == "NiagaraEmitter":
            emitter_exports.append(export)
        elif kind in RENDERER_TYPES:
            renderers_by_outer.setdefault(ref_leaf(export.get("Outer")), []) \
                .append(renderer_ir(export))
        elif "DataInterface" in kind:
            owner = owning_emitter(export)
            if owner:
                detail = di_ir(export)
                if detail["payload"]:
                    di_by_emitter.setdefault(owner, []).append(detail)

    emitters = [emitter_ir(e, renderers_by_outer) for e in emitter_exports]
    for emitter, export in zip(emitters, emitter_exports):
        payloads = di_by_emitter.get(export.get("Name"), [])
        emitter["diPayloads"] = payloads
        emitter["readsEmitters"] = sorted({
            p["payload"]["EmitterName"] for p in payloads
            if isinstance(p["payload"].get("EmitterName"), str)
        })

    known = {e.get("Name") for e in emitter_exports}
    orphans = [r for outer, group in renderers_by_outer.items() if outer not in known
               for r in group]

    return {
        "system": Path(json_path).stem,
        "sourceJson": str(json_path),
        "emitters": emitters,
        "orphanRenderers": orphans,
        "userParams": user_params((system_export or {}).get("Properties") or {}),
    }


# --------------------------------------------------------------------------- #
# IR -> Blender scene
# --------------------------------------------------------------------------- #

def set_props(obj, mapping, prefix="nsir"):
    for key, value in mapping.items():
        if value is None:
            continue
        name = f"{prefix}_{key}"
        if isinstance(value, (int, float, str, bool)):
            obj[name] = value
        else:
            obj[name] = json.dumps(value)


def new_empty(name, parent=None, display="PLAIN_AXES", size=0.25):
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = display
    obj.empty_display_size = size
    bpy.context.scene.collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent
    return obj


def curve_prop_name(index, payload):
    """Custom-property name for one curve payload slot.

    Indexed by slot because data-interface export names repeat within a single
    emitter's payload list; keying on the bare name overwrites earlier curves.
    """
    return f"nsir_curve_{index:03d}_{payload.get('name') or payload.get('class')}"


def action_fcurves(action):
    """F-curves of an action across legacy and slotted-action APIs.

    Blender 4.4+ moved them into layers/strips/channelbags; 5.x dropped
    ``Action.fcurves`` entirely.
    """
    if action is None:
        return []
    legacy = getattr(action, "fcurves", None)
    if legacy is not None:
        return list(legacy)
    found = []
    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            for bag in getattr(strip, "channelbags", ()):
                found.extend(bag.fcurves)
    return found


def bake_curves(obj, payloads, stats):
    """Bake cooked curve LUTs onto obj as keyframed custom properties.

    Interpolation is LINEAR: the LUT is already a dense resample of the source
    curve, so Bezier smoothing would invent overshoot the GPU never produced.
    """
    fps = bpy.context.scene.render.fps or 24
    baked = 0

    for index, payload in enumerate(payloads or ()):
        cls = payload.get("class")
        if not niagara_curves.is_curve_interface(cls):
            continue
        curve = niagara_curves.decode_curve(cls, payload.get("payload") or {})
        if curve is None:
            continue

        prop = curve_prop_name(index, payload)
        channels = curve["channels"]
        obj[f"{prop}_info"] = niagara_curves.curve_summary(curve)

        if curve["constant"]:
            flat = [ch[0] for ch in curve["values"]]
            obj[prop] = flat if channels > 1 else flat[0]
            stats["curvesConstant"] = stats.get("curvesConstant", 0) + 1
            baked += 1
            continue

        obj[prop] = [0.0] * channels if channels > 1 else 0.0
        path = f'["{prop}"]'
        for i, t in enumerate(curve["times"]):
            if channels > 1:
                obj[prop] = [curve["values"][c][i] for c in range(channels)]
            else:
                obj[prop] = curve["values"][0][i]
            obj.keyframe_insert(data_path=path, frame=1.0 + t * fps)

        for fcurve in action_fcurves(getattr(obj.animation_data, "action", None)):
            if fcurve.data_path != path:
                continue
            for point in fcurve.keyframe_points:
                point.interpolation = "LINEAR"

        stats["curveKeys"] = stats.get("curveKeys", 0) + curve["samples"] * channels
        stats["curvesAnimated"] = stats.get("curvesAnimated", 0) + 1
        baked += 1

    stats["curves"] = stats.get("curves", 0) + baked
    return baked


def find_mesh_file(asset_root, mesh_name, cache):
    """Locate <mesh_name>.pskx/.psk under the export root."""
    if not asset_root or not mesh_name:
        return None
    if mesh_name in cache:
        return cache[mesh_name]
    hit = None
    for ext in (".pskx", ".psk"):
        for candidate in Path(asset_root).rglob(f"{mesh_name}{ext}"):
            hit = candidate
            break
        if hit:
            break
    cache[mesh_name] = hit
    return hit


def placeholder(name, kind):
    """Named stand-in so a renderer is visible without inventing geometry."""
    mesh = bpy.data.meshes.new(f"{name}_placeholder")
    if kind == "sprite":
        verts = [(-0.05, -0.05, 0), (0.05, -0.05, 0), (0.05, 0.05, 0), (-0.05, 0.05, 0)]
        mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    elif kind == "ribbon":
        mesh.from_pydata([(0, 0, 0), (0.2, 0, 0)], [(0, 1)], [])
    else:
        mesh.from_pydata([(0, 0, 0)], [], [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def ensure_material(name, path):
    """Material named after the cooked MI, with its source path recorded.

    Not a shader rebuild: FX materials are a separate pixel path, so this only
    carries identity so the right MI can be resolved later.
    """
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing
    mat = bpy.data.materials.new(name)
    mat["nsir_source_path"] = path or ""
    return mat


def build_renderer(rend, emitter_obj, asset_root, cache, stats):
    kind = rend["kind"]
    base = f"{emitter_obj.name}.{rend['name'] or kind}"

    if kind == "light":
        data = bpy.data.lights.new(base, type="POINT")
        obj = bpy.data.objects.new(base, data)
        bpy.context.scene.collection.objects.link(obj)
        obj.parent = emitter_obj
        set_props(obj, {"kind": kind, "bindings": rend["bindings"],
                        "scalars": rend["scalars"]})
        stats["light"] = stats.get("light", 0) + 1
        return obj

    obj = None
    if kind == "mesh":
        for entry in rend["meshes"]:
            path = find_mesh_file(asset_root, entry.get("mesh"), cache)
            if path is None:
                continue
            try:
                imported = [o for o in importing_import_psk(str(path))
                            if o and o.type == "MESH"]
            except Exception as exc:  # noqa: BLE001 - report, keep building
                stats["pskFailed"] = stats.get("pskFailed", 0) + 1
                print(f"Arc Raiders: psk import failed for {path.name}: {exc}")
                continue
            if not imported:
                continue
            obj = imported[0]
            obj.name = base
            for extra in imported[1:]:
                extra.parent = emitter_obj
            scale = entry.get("scale") or {}
            if isinstance(scale, dict):
                obj.scale = (scale.get("X", 1.0) or 1.0,
                             scale.get("Y", 1.0) or 1.0,
                             scale.get("Z", 1.0) or 1.0)
            pivot = entry.get("pivotOffset") or {}
            if isinstance(pivot, dict):
                obj.location = (pivot.get("X", 0.0) * UE_TO_M,
                                pivot.get("Y", 0.0) * UE_TO_M,
                                pivot.get("Z", 0.0) * UE_TO_M)
            stats["meshImported"] = stats.get("meshImported", 0) + 1
            break

    if obj is None:
        obj = placeholder(base, kind)
        stats[f"{kind}Placeholder"] = stats.get(f"{kind}Placeholder", 0) + 1

    obj.parent = emitter_obj
    for entry in rend["materials"]:
        if entry.get("material"):
            obj.data.materials.append(
                ensure_material(entry["material"], entry.get("path")))
    set_props(obj, {
        "kind": kind,
        "rendererType": rend["type"],
        "bindings": rend["bindings"],
        "scalars": rend["scalars"],
        "materials": rend["materials"],
        "meshes": rend["meshes"],
    })
    return obj


def importing_import_psk(filepath):
    """Import a PSK via the addon's own importer."""
    from . import importing
    return importing.import_psk(filepath)


def psk_available():
    try:
        from . import importing
        return importing.psk_reader_available()
    except Exception:  # noqa: BLE001
        return False


def bounds_box(name, fixed_bounds, parent):
    """Wireframe box for the emitter's cooked FixedBounds."""
    if not isinstance(fixed_bounds, dict):
        return None
    lo = fixed_bounds.get("Min") or {}
    hi = fixed_bounds.get("Max") or {}
    try:
        x0, y0, z0 = (float(lo["X"]) * UE_TO_M, float(lo["Y"]) * UE_TO_M,
                      float(lo["Z"]) * UE_TO_M)
        x1, y1, z1 = (float(hi["X"]) * UE_TO_M, float(hi["Y"]) * UE_TO_M,
                      float(hi["Z"]) * UE_TO_M)
    except (KeyError, TypeError, ValueError):
        return None
    if not all(map(math.isfinite, (x0, y0, z0, x1, y1, z1))):
        return None

    verts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
             (0, 4), (1, 5), (2, 6), (3, 7)]
    mesh = bpy.data.meshes.new(f"{name}_bounds")
    mesh.from_pydata(verts, edges, [])
    mesh.update()
    obj = bpy.data.objects.new(f"{name}_bounds", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    obj.display_type = "WIRE"
    obj.hide_render = True
    return obj


def build_system(ir, asset_root=None, bake=True, stats=None):
    """Build one system's IR into the scene. Returns (root object, missing)."""
    stats = stats if stats is not None else {}
    cache = {}
    name = ir["system"]
    root = new_empty(name, display="ARROWS", size=1.0)

    defaults = {p["name"]: p["default"] for p in ir.get("userParams") or []
                if p.get("default") is not None}
    set_props(root, {
        "sourceJson": ir.get("sourceJson"),
        "userParamDefaults": defaults,
        "userParamNames": [p["name"] for p in ir.get("userParams") or []],
    })

    for emitter in ir["emitters"]:
        obj = new_empty(f"{name}.{emitter['name']}", parent=root, display="SPHERE")
        set_props(obj, {
            "simTarget": emitter.get("simTarget"),
            "localSpace": emitter.get("localSpace"),
            "determinism": emitter.get("determinism"),
            "randomSeed": emitter.get("randomSeed"),
            "allocationMode": emitter.get("allocationMode"),
            "preAllocationCount": emitter.get("preAllocationCount"),
            "simulationStages": emitter.get("simulationStages"),
            "readsEmitters": emitter.get("readsEmitters"),
        })
        bounds_box(obj.name, emitter.get("fixedBounds"), obj)
        if bake:
            bake_curves(obj, emitter.get("diPayloads"), stats)
        for rend in emitter.get("renderers") or []:
            build_renderer(rend, obj, asset_root, cache, stats)
            stats["renderers"] = stats.get("renderers", 0) + 1
        stats["emitters"] = stats.get("emitters", 0) + 1

    for rend in ir.get("orphanRenderers") or []:
        build_renderer(rend, root, asset_root, cache, stats)
        stats["renderers"] = stats.get("renderers", 0) + 1

    missing = ["module inputs: spawn rate / lifetime / velocity "
               "(compiled into VectorVM / DXBC, absent from the cook)"]
    if not defaults:
        missing.append("no decoded User.* defaults")
    if any(e.get("simulationStages") for e in ir["emitters"]):
        missing.append("simulation stages (GPU permutations, need DXBC disassembly)")
    if not bake:
        missing.append("curve LUTs not baked")
    return root, missing


def import_ns_json(json_path, asset_root=None, bake=True):
    """Read a cooked NS json and build it. Returns (root, stats, missing)."""
    ir = system_ir(json_path)
    stats = {}
    root, missing = build_system(ir, asset_root=asset_root, bake=bake, stats=stats)
    return root, stats, missing


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #

def draw_niagara_fx_box(layout, context) -> None:
    scene = context.scene
    col = layout.column(align=True)

    col.prop(scene, "arc_ns_json_path", text="NS JSON")
    col.prop(scene, "arc_ns_asset_root", text="Asset Root")
    col.prop(scene, "arc_ns_bake_curves")

    col.separator()
    col.operator("arc_outfits.import_niagara_fx", text="Import NS Effect",
                 icon="PARTICLES")

    if not psk_available():
        col.label(text="PSK reader unavailable - meshes become placeholders",
                  icon="INFO")

    info = scene.get("arc_ns_last_report")
    if info:
        box = layout.box()
        box.label(text="Last import", icon="INFO")
        for line in str(info).splitlines():
            box.label(text=line)
