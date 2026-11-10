"""Audit cooked Niagara evidence without importing Blender or mounting game paks."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import Niagara.niagara as niagara_ir
import Niagara.niagara as niagara_vm


def audit(root, version, output, selected=(), vm_profile="ue5.3-legacy"):
    counts = Counter()
    usage = Counter()
    external = Counter()
    interfaces = Counter()
    failures = []
    decode_failures = []
    opcodes = Counter()
    systems = []
    output.mkdir(parents=True, exist_ok=True)
    for path in sorted(root.rglob("NS_*.json")):
        try:
            ir = niagara_ir.extract_file(path, game_version=version)
            scripts = ir["scripts"]
            counts["systems"] += 1
            counts["emitters"] += len(ir["emitters"])
            counts["scripts"] += len(scripts)
            counts["renderers"] += len(ir["renderers"])
            counts["unresolvedEmitterVersions"] += sum(e["selectedVersionIndex"] is None for e in ir["emitters"])
            counts["unresolvedOwners"] += sum(r["scope"] == "unresolved" for r in scripts + ir["renderers"] + ir["dataInterfaces"])
            for script in scripts:
                state = script["bytecode"]["state"]
                counts["bytecode_" + state] += 1
                if state == "present":
                    usage[script["usage"]] += 1
                    counts["bytecodeBytes"] += script["bytecode"]["byteCount"]
                    try:
                        program = niagara_vm.vm_disassemble(niagara_vm.bytecode_from_vm(script["vm"]), external_functions=script["vm"].get("CalledVMExternalFunctions") or [], profile=vm_profile)
                        counts["decodedPrograms"] += 1
                        counts["decodedInstructions"] += len(program.instructions)
                        opcodes.update(i.name for i in program.instructions)
                    except (ValueError, TypeError) as exc:
                        decode_failures.append({"source": str(path), "exportId": script["id"], "error": str(exc)})
                    for function in script["vm"].get("CalledVMExternalFunctions") or []:
                        external[function.get("Name") or "<unnamed>"] += 1
                counts["shaderMapsPresent"] += script["shaderMapState"] == "present"
            for interface in ir["dataInterfaces"]:
                interfaces[interface["type"]] += 1
            systems.append({"source": ir["source"], "emitters": len(ir["emitters"]),
                            "scripts": [{k:s[k] for k in ("id", "name", "usage", "emitterId", "bytecode", "literals", "shaderMapState")} for s in scripts]})
            if path.stem in selected:
                relative = path.relative_to(root)
                target = output / "effects" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(ir, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        except Exception as exc:
            failures.append({"source": str(path), "error": str(exc)})
    report = {"schemaVersion": 1, "gameVersion": version, "root": str(root.resolve()),
              "counts": dict(counts), "bytecodeUsage": dict(usage),
              "externalFunctions": dict(external.most_common()),
              "dataInterfaceTypes": dict(interfaces.most_common()), "systems": systems, "failures": failures,
              "vmProfile": vm_profile, "opcodeCounts": dict(opcodes.most_common()), "decodeFailures": decode_failures}
    (output / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"counts": report["counts"], "bytecodeUsage": report["bytecodeUsage"], "failures": len(failures)}, indent=2))
    return 1 if failures or decode_failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--game-version", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--effect", action="append", default=[])
    parser.add_argument("--vm-profile", choices=("ue5.3-legacy", "pioneer-2.1.0"), default="ue5.3-legacy")
    args = parser.parse_args()
    raise SystemExit(audit(args.root, args.game_version, args.out, args.effect, args.vm_profile))
