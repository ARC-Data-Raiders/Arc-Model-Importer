"""Lossless, bpy-free evidence IR for cooked Niagara JSON exports.

This module preserves compiled evidence; it does not claim executable playback.
Export identity includes package, outer chain and export slot so repeated names
cannot overwrite one another. Unknown serialized fields remain in rawExport.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re

SCHEMA_VERSION = 1
SCRIPT_USAGES = {
    0: "Function", 1: "Module", 2: "DynamicInput", 3: "ParticleSpawnScript",
    4: "ParticleSpawnScriptInterpolated", 5: "ParticleUpdateScript",
    6: "ParticleEventScript", 7: "ParticleSimulationStageScript",
    8: "ParticleGPUComputeScript", 9: "EmitterSpawnScript",
    10: "EmitterUpdateScript", 11: "SystemSpawnScript", 12: "SystemUpdateScript",
}
SIM_TARGETS = {0: "CPUSim", 1: "GPUComputeSim"}


def normalize_enum(value, names):
    """Normalize known integers and qualified names; retain unknown values."""
    if isinstance(value, str):
        short = value.rsplit("::", 1)[-1]
        if short in names.values():
            return short
        try:
            return names.get(int(value), value)
        except ValueError:
            return value
    return names.get(value, value) if isinstance(value, int) else value


def exports_from_document(document):
    if isinstance(document, list):
        exports = document
    elif isinstance(document, dict) and isinstance(document.get("Exports"), list):
        exports = document["Exports"]
    else:
        raise ValueError("expected an export list or an object containing Exports")
    if any(not isinstance(e, dict) for e in exports):
        raise ValueError("export list contains a non-object entry")
    return exports


def reference_name(reference):
    if isinstance(reference, str):
        return reference
    if not isinstance(reference, dict):
        return None
    name = reference.get("ObjectName")
    if not isinstance(name, str):
        return None
    if "'" in name:
        name = name.split("'", 1)[1].rstrip("'")
    return name


def export_object_name(export):
    outer = reference_name(export.get("Outer"))
    name = export.get("Name") or "<unnamed>"
    if not outer:
        return name
    # The outer of a top-level owned export is the system itself.
    separator = ":" if ":" not in outer and "." not in outer else "."
    return outer + separator + name


def package_path(exports, fallback):
    candidates = []
    for export in exports:
        outer = export.get("Outer")
        path = outer.get("ObjectPath") if isinstance(outer, dict) else None
        if isinstance(path, str) and path.startswith("/Game/"):
            candidates.append(re.sub(r"\.\d+$", "", path))
    return max(set(candidates), key=candidates.count) if candidates else fallback


def byte_payload(value):
    """Read the existing JSON byte forms without coercing corrupt values."""
    if isinstance(value, dict):
        return byte_payload(value.get("Data"))
    if value is None:
        return b""
    if isinstance(value, list):
        if any(type(x) is not int or not 0 <= x <= 255 for x in value):
            raise ValueError("byte array contains an invalid byte")
        return bytes(value)
    if isinstance(value, str):
        return base64.b64decode(value, validate=True)
    raise ValueError("unsupported byte payload representation")


def payload_state(container, key):
    if key not in container:
        return {"state": "missing", "byteCount": 0}
    try:
        data = byte_payload(container[key])
    except (ValueError, TypeError) as exc:
        return {"state": "invalid", "byteCount": 0, "error": str(exc)}
    return {"state": "present" if data else "empty", "byteCount": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def select_emitter_version(properties):
    versions = properties.get("VersionData") or []
    if not isinstance(versions, list):
        return None, "invalid-version-list"
    exposed = properties.get("ExposedVersion")
    for index, version in enumerate(versions):
        if isinstance(version, dict) and (version.get("Version") or {}).get("VersionGuid") == exposed:
            return index, "exposed-guid"
    if len(versions) == 1 and isinstance(versions[0], dict):
        return 0, "only-version"
    return None, "ambiguous" if versions else "missing"


def extract_document(document, *, source_path="", game_version=None, source_sha256=None):
    exports = exports_from_document(document)
    package = package_path(exports, Path(source_path).stem or "<unknown-package>")
    records = []
    by_object = {}
    for index, export in enumerate(exports):
        name = export_object_name(export)
        record = {"id": f"{package}#{index}", "exportIndex": index,
                  "objectName": name, "type": export.get("Type"),
                  "name": export.get("Name"), "outer": export.get("Outer"),
                  "rawExport": export}
        records.append(record)
        by_object.setdefault(name, []).append(record)
    emitters, scripts, renderers, interfaces = [], [], [], []
    for record in records:
        export = record["rawExport"]
        kind = record["type"] or ""
        properties = export.get("Properties") or {}
        outer_name = reference_name(record["outer"])
        owners = by_object.get(outer_name, [])
        record["outerId"] = owners[0]["id"] if len(owners) == 1 else None
        if kind == "NiagaraEmitter":
            selected, reason = select_emitter_version(properties)
            version = properties["VersionData"][selected] if selected is not None else {}
            record.update({"selectedVersionIndex": selected, "versionSelection": reason,
                           "simTarget": normalize_enum(version.get("SimTarget"), SIM_TARGETS)})
            emitters.append(record)
        elif kind == "NiagaraScript":
            vm = properties.get("CachedScriptVM")
            vm = vm if isinstance(vm, dict) else {}
            maps = export.get("LoadedScriptResources")
            record.update({"usage": normalize_enum(properties.get("Usage"), SCRIPT_USAGES),
                           "usageRaw": properties.get("Usage"), "vm": vm,
                           "bytecode": payload_state(vm, "ByteCode"),
                           "literals": payload_state(vm, "ScriptLiterals"),
                           "shaderMaps": maps,
                           "shaderMapState": "present" if maps else (
                               "empty" if "LoadedScriptResources" in export else "missing"),
                           "shaderTailBytes": export.get("ShaderMapLeftoverBytes"),
                           "shaderReadError": export.get("ShaderMapReadError")})
            scripts.append(record)
        elif kind.endswith("RendererProperties"):
            renderers.append(record)
        elif "DataInterface" in kind:
            interfaces.append(record)
    by_id = {r["id"]: r for r in records}
    # Parent references resolve using full object names, never a short-name dict.
    def emitter_owner(record):
        seen = set()
        while record and record["id"] not in seen:
            seen.add(record["id"])
            if record["type"] == "NiagaraEmitter":
                return record["id"]
            parent_id = record.get("outerId")
            record = by_id.get(parent_id)
        return None
    for record in scripts + renderers + interfaces:
        record["emitterId"] = emitter_owner(record)
        ancestor = record
        seen = set()
        while ancestor and ancestor["id"] not in seen and ancestor["type"] != "NiagaraSystem":
            seen.add(ancestor["id"])
            ancestor = by_id.get(ancestor.get("outerId"))
        record["scope"] = "emitter" if record["emitterId"] else ("system" if ancestor else "unresolved")
    systems = [r for r in records if r["type"] == "NiagaraSystem"]
    gaps = []
    for script in scripts:
        if script["bytecode"]["state"] != "present" and script["usage"] != "ParticleGPUComputeScript":
            gaps.append({"exportId": script["id"], "kind": "bytecode-" + script["bytecode"]["state"],
                         "usage": script["usage"]})
    if any(e["selectedVersionIndex"] is None for e in emitters):
        gaps.append({"kind": "unresolved-emitter-version"})
    classified_ids = {r["id"] for group in (systems, emitters, scripts, renderers, interfaces) for r in group}
    return {"schemaVersion": SCHEMA_VERSION, "source": {"path": str(source_path),
            "packagePath": package, "gameVersion": game_version, "sha256": source_sha256,
            "metadata": document.get("Metadata") if isinstance(document, dict) else None},
            "systems": systems, "emitters": emitters, "scripts": scripts,
            "renderers": renderers, "dataInterfaces": interfaces,
            "otherExports": [r for r in records if r["id"] not in classified_ids],
            "coverage": {"structural": "retained", "simulation": "not-executed",
                         "rendering": "not-evaluated", "gaps": gaps}}


def extract_file(path, *, game_version=None):
    path = Path(path)
    raw = path.read_bytes()
    return extract_document(json.loads(raw.decode("utf-8-sig")), source_path=str(path.resolve()),
                            game_version=game_version, source_sha256=hashlib.sha256(raw).hexdigest())
