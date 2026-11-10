# Offline Niagara recovery implementation

The offline recovery pipeline now preserves cooked scripts and can disassemble both the historical and current Pioneer VM formats. The Blender importer still builds an inspectable structural scene; recovered programs are not yet connected to general particle playback.

## Available components

| File | Role |
| --- | --- |
| `niagara_ir.py` | Blender-independent evidence IR: full export preservation, stable export-slot identity, resolved outer/emitter ownership, enum normalization, exposed version GUID selection, byte payload states and SHA-256 provenance. |
| `niagara_vm.py` | Strict complete-program disassembly with `ue5.3-legacy` and `pioneer-2.1.0` profiles. The latter uses 16-bit external-function indices. Compressed carriers are bounded and validated. |
| `niagara_runtime.py` | Bounded scalar evaluator for a supported arithmetic, conversion, comparison and dataset subset. Unsupported interfaces, randomness, IDs and GPU operations fail explicitly. |
| `niagara_context.py` | Strict source-profiled constant-table and named dataset construction. Missing values read by a program are errors; unused unknown bytes remain explicitly unavailable. |
| `scripts/audit_niagara.py` | Reproducible corpus evidence, script/DI inventory, ownership/version gaps, instruction counts and decode failures. |
| `niagara_fx.py` | Retains compiled evidence in returned IR, reports program availability, handles numeric/string simulation enums, chooses exposed emitter versions, respects fractional frame rates and zero scale components. |

The runtime's authored tests do not establish equivalence with cooked Unreal execution. Dataset layouts, constant tables, scheduling and external interfaces must be validated separately.

## Verified recovery

The historical dump has 516 systems and 3456 VM payloads. All decode exactly under the legacy profile: 926127 instructions, without resynchronization or trailing padding.

The original 2.1.0 JSON corpus has 658 systems and 3081 VM payloads. All decode under the current profile: 543017 instructions. This corpus was incomplete: the stale exporter rejected valid primitive byte arrays using an overly strict tagged-property allocation budget.

Rebuilding the existing CUE4Parse source, which already contains a guarded Arc Raiders unversioned-array correction, recovered 4562 VM programs across the same 658 unique NS assets. All decode exactly: 1227470 instructions. Every system now retains SystemSpawn and SystemUpdate programs. No FModel source modification or game execution was needed. The export report counts 665 entries because seven package paths occur twice; the unique-file inventory is authoritative.

Fresh inputs came from installed Steam build 25879518 using `ArcRaiders_2.1.0_inheritance_fixed.usmap`. Treat this as a specifically identified installed-package corpus rather than inferring equivalence with another version from an asset filename. Original exports remain untouched. The fresh dump omits shader-map decoding; existing map metadata and targeted `niagara-ns-maps` extraction are separate evidence channels.

For `NS_FrontEnd_RoofSmoke_01`, targeted extraction recovered current SM5 DXBC and SM6 DXIL. SM5 disassembly exposes 471 executable instruction/control records, 64x1x1 threads, a 33-vector constant buffer, three SRVs and three UAVs. This is recovered code and layout evidence, not a ported GPU simulation. The legacy disassembler does not accept SM6 DXIL.

## SDK and engine references

The supplied 2.1.0 SDK is useful for reflected member types, enum values, parameter stores, VM records and game-specific interface discovery. It is not a source of VM opcode semantics, and reflected memory offsets are not cooked archive offsets. Ten relevant namespace blocks and the original dump hash are retained in task-local evidence; the SDK is not copied into Git.

Installed UE5.3.2 includes the actual VectorVM and Niagara source and supplied the legacy-format reference. UE5.7.4 source confirms the widened external-function operand. The offline Unreal lab uses 5.7.4; that editor version does not by itself establish compatibility with Pioneer.

## Reproduce the audit

Run from the addon repository with a Python interpreter and write results to the mandated Z: scratch tree:

```powershell
python scripts/audit_niagara.py <export-root> --game-version <identified-version> --vm-profile pioneer-2.1.0 --out <Z-scratch-output> --effect NS_LocalFogSphere
python -m unittest discover -s tests -p 'test_niagara*.py' -v
```

Use `ue5.3-legacy` for the historical corpus. The audit exits nonzero on a parse or disassembly failure. It never mounts packages or launches the game. Selected full evidence IRs are written under the output's `effects` tree; `manifest.json` contains counts and gaps.

## Unreal lab and MCP

Task-local root: `Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/niagara-offline/`.

The lab includes `NiagaraOfflineLab57`, a pinned BoldPhoenix UnrealMCP checkout, an isolated Python environment, project-bound loopback Remote Execution, launcher, client and probe scripts. The Codex server name is `unreal_niagara_offline`. See its local README and smoke-test evidence for current connection and fixture status. Native Codex tool visibility may require reconnecting the chat; the standalone MCP client works now. Final initialize/list/execute_python verification passed against the active UE5.7.4 editor and exact lab project. The active editor uses NullRHI for asset automation. NiagaraComponent activation is disabled in that mode; actual SimCache capture remains unverified. Graphics startup reached lengthy default-material shader compilation during bounded retries. A deterministic template-derived DirectionalBurst fixture is saved in the lab, with its source and settings recorded.

## Blender validation

The working ZIP was installed into a private Blender 5.1.2 profile and passed fresh install, enable, disable/re-enable, and overwrite-enabled-install checks. A separate actual-ZIP import of recovered LocalFogSphere passed: one emitter, one sprite placeholder, four retained scripts and four retained VM programs, correct CPUSim normalization and source hash. The packaged VM/context helpers also executed the real recovered particle-spawn program inside Blender and returned IDs 100, 101 and 102. No addon version bump or user-profile sync was performed for this work.

## Controlled execution of recovered programs

All four fresh LocalFogSphere programs execute with explicit inputs and strict constant contexts. Particle spawn outputs IDs 100, 101 and 102; particle update preserves them. System spawn followed by update at dt=1/60 yields age 0.0166666675, loop duration 5, normalized age about 0.00333333, white RGBA and 400x400 sprite size. Engine-owner and emitter inputs are caller-provided; user defaults come from the cooked parameter store. This is a controlled execution demonstration with provenance, not proof of engine-equivalent playback.


### Reproduce the particle spawn probe

This example deliberately chooses inputs; it does not infer gameplay state:

```python
import json
from pathlib import Path
from niagara_context import build_constant_context
from niagara_runtime import Dataset, execute

exports = json.loads(Path("<recovered NS_LocalFogSphere.json>").read_text(encoding="utf-8"))
spawn = next(e for e in exports if e.get("Type") == "NiagaraScript"
             and e.get("Name") == "SpawnScript")
context = build_constant_context(
    spawn, runtime_values={"emitter.EmitterTotalSpawnedParticles": 100},
    layout_profile="ue5.3.2", bytecode_profile="pioneer-2.1.0")
result = execute(context.bytecode, num_instances=3,
    constant_tables=context.constant_tables,
    datasets={0: Dataset(output_capacities={("int32", 0): 3})},
    profile="pioneer-2.1.0")
assert result.outputs[0, ("int32", 0)] == (100, 101, 102)
```

Use the verified fresh export and the explicit layout profile. Arbitrary systems may require other constants, datasets, operations or interfaces; missing context is an error.

## Engine reference comparison

A task-local UE5.3.2 editor helper called the real VectorVM::Exec on a fixed authored five-lane velocity/compaction program. It exited successfully with no engine errors after the test context and run-only editor settings were configured. Engine binary32 output bits and dataset access index match the offline evaluator exactly: live outputs 10.5, 31.5 and42, access index2. Unwritten tail sentinels remained unchanged.

The authored program, inputs, engine version and expected outputs are retained as a repository regression fixture. This validates the selected arithmetic/dataset path. It does not establish numerical equivalence for every supported instruction, data interface, or complete Niagara system.

## Remaining work

Recover and validate execution context, compile and capture known Unreal fixtures, implement deterministic scheduling and required data interfaces, produce particle caches, bind renderer attributes and reconstruct FX materials. The small evaluator is infrastructure for that work; the importer continues to state when simulation was not executed.
