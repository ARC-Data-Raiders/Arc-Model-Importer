# Import performance and sidebar isolation audit

This is the retained pre-change baseline. The implemented candidate and its verification are recorded in [UI revision 2.22.13](ui-revision-2.22.13.md).

Date: 2026-11-01.

The measured cached imports spend most of their time applying materials. Repeated weapon imports still compile a fresh graph after images are already loaded. Sidebar expansion and weapon enum callbacks also perform external discovery before an explicit action. The current implementation does not meet the requested browsing boundary.

This audit changes no runtime code, Outfit Selector controls, sidebar wording, addon version, installed addon, or user scene. The proposed optimizations remain unimplemented. No speedup or final package-size reduction is claimed.

## Evidence and scope

Evidence directory: `Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/import-performance-audit/`.

| Artifact | What it establishes |
|---|---|
| `import-baseline.json` and import phase `.prof` files | Current-checkout geometry import and material application on cached exported UEModels. |
| `sidebar-baseline.json` and sidebar phase `.prof` files | Actual registration and draw methods through a headless layout recorder. |
| `enum-backup-baseline.json` | Actual deferred weapon registration and enum callbacks, including warm calls. |
| `enum-backup-baseline.log` | A failing primary-only guard with an existing configured primary root. |
| `cached-metadata-tests.log` | The existing cached metadata preparation tests pass. |

The import probe uses FILES mode and the retained benchmark exports. It rejects helper process launches. It does not measure fresh PAK extraction, complete weapon assembly, disk-cold I/O, shader rendering, or visual correctness. Cold below means the first asset in this private Blender process, not a flushed operating-system cache. Every duration includes Python profiler overhead. These are individual runs, not distributions or speedup estimates.

## Measured import costs

| Case | Total seconds | Material application seconds | Native compile seconds | New materials | New groups | New images |
|---|---:|---:|---:|---:|---:|---:|
| Bettina base, first import | 12.061 | 11.587 | 5.189 | 2 | 1 | 9 |
| Bettina base, repeated import | 5.724 | 5.482 | 5.172 | 1 | 1 | 0 |
| Bettina charging handle, same session | 5.984 | 5.841 | 5.235 | 1 | 1 | 0 |
| Bullcrab, first import in that session | 19.287 | 16.158 | Not isolated in the retained top rows | 12 | 6 | 19 |

The repeated Bettina call still runs `compile_plan` and creates another group. Cached images alone do not eliminate the graph work. The charging-handle result is evidence of another expensive compile in the same weapon family, not proof that its full graph is interchangeable with the base graph.

Bullcrab calls `reviewed_source` repeatedly. Its inclusive time is recorded below. The implementation reads the activation manifest and hashes the manifest and catalog before its session-cache lookup. A verified action-scoped native-bank snapshot is worth measuring before attempting broad graph sharing.

| Additional profiled work | Seconds | Calls | Scope |
|---|---:|---:|---|
| Bettina first-import native preparation | 2.509 | 2 | Inclusive `prepare` time. |
| Bettina repeat native preparation | 0.154 | 2 | Inclusive `prepare` time. |
| Bullcrab reviewed native source lookup | 6.043 | 8 | Inclusive `reviewed_source` time. |
| Bullcrab directory enumeration | Not separately timed | 112 | Audit events, not unique directories. |

Do not add nested inclusive profile rows together. Repeated Bullcrab scans include the same decal metadata directories. The weapon cases also enumerate FMDex locations during import. These findings justify scoped lookup caches, not a claim that all enumeration time is removable.

Earlier outfit evidence remains relevant and is not rerun here. The StationMaster profile records graph construction as its largest component. See `../DataRaiders-Documentation/sections/blender-native-material-parity.md` and the retained `import-profile-20261030/` evidence. Selector-weight sharing was already shipped. Strict palette-only template copying had a low recorded eligibility ceiling and is not a new general solution.

## Sidebar and backup boundary

| Actual path exercised | Recorded seconds | Observation |
|---|---:|---|
| Source module import | 1.915 | Synchronous addon-tree work remains. This is separate from panel drawing. |
| Registration | 0.292 | Does not include the deferred weapon timer in this run. |
| Default draw, first call | 0.078 | No directory enumeration in this recorded draw. Preview preparation is its main measured cost. |
| Default draw, warm call | 0.001 | No directory enumeration in this recorded draw. |
| Expanded draw | 0.623 | Animation and FX export-directory indexing plus configured-game/build checks. |
| Deferred weapon registration, separate run | 0.006 | Directory enumeration occurs before a user action. |
| Ascended enum, selected and warm | 0.006 | Still enumerates directories despite its populated enum cache. |
| Skin enum, selected and warm | 0.003 | Still enumerates directories despite its populated enum cache. |

The layout recorder does not emulate native widget rendering or GUI event-loop latency. The separate enum probe calls real callbacks and explicitly invokes the timer body, but does not prove event-loop scheduling behavior. The recorded default draw is not proof of an instant production sidebar. Saved expanded sections can reach the failing paths immediately.

The primary-only guard supplies an existing configured export root, warms the selected-weapon enums, then records any general-root or supplied-unpack checks. It returns unavailable for guarded backup paths without reading that unpack.

| Guarded path | General discovery attempted | Supplied older unpack tested | Result |
|---|---|---|---|
| `pioneer_roots` with configured primary | Yes | Yes | FAIL |
| Warm Ascended enum | Yes | Yes | FAIL |
| Warm skin enum | Yes | Yes | FAIL |

Observed failure:

```text
AssertionError: Primary data is present but backup discovery still runs
BLENDER_EXIT=1
```

The sidebar probe also fails its no-discovery assertion. Export-directory indexing and game configuration checks were observed. PAK reindexing or a helper launch from the default panel draw was not observed and must not be inferred.

## Implementation priorities

| Priority | Change | Where to compute or store it | Acceptance check |
|---|---|---|---|
| First | Remove discovery from registration, timers, draws, enum callbacks, search, and selection. | Existing package catalog producers. Package missing weapon relationships, animation and FX lists, and preview mappings. | Both browsing probes pass with game and backup access forbidden, including saved expanded sections. |
| First | Separate configured/current roots from fallback providers. | Lazy action-time provider chain. Resolve exact current candidates before discovering other roots. | A complete primary hit does not call general discovery or inspect the supplied unpack. A real miss still reaches the intended fallback. |
| Next | Stop invalidating material, path, and surface caches when extraction reused unchanged data. | `pak_extract.ensure_rels` and the extraction completion producer. | A reused complete extraction retains downstream hits. A changed extraction invalidates affected entries. |
| Next | Validate the reviewed native bank once per explicit operation and share the verified snapshot across its material work. | Action-local state first. Consider a session hit only with safe change detection. | Modified or incompatible banks are rejected. Repeat consumers avoid redundant full hashing within the same validated operation. |
| Next | Test compiled templates for exact recipe matches, starting with repeated weapon imports. | Session cache, then a generated local library only if measured worthwhile. | Compare total import time and graph-copy time; prove bindings, material behavior, and independent root controls. |
| Later | Cache resolved records and typed texture metadata before repeated parsing and parent traversal. | Action/session records keyed by complete dependency identity. | Warm hits skip ordinary fallback metadata; source or dependency changes invalidate the record. |
| Later | Reuse one verified package-index handle per action and one exact path map for repeated decal/FMDex lookups. | Action-scoped index and local cache. | Repeated lookups do not rescan the same roots. Build/mapping changes and genuine misses still work. |

The first rows are correctness requirements for browsing, not a ranking by import time. Do not fix enum latency by merely moving directory walks to a registration timer. Do not make backups permanently unavailable to obtain a passing hit-path test.

## Shared precomputation without a large ZIP

| Candidate | Package effect | Guardrail |
|---|---|---|
| Existing catalog enrichment | Compact labels, normalized search terms, relationships, requirements, and preview references. Measure the resulting ZIP delta. | Change the authoritative producer. Do not ship duplicate full JSON representations. |
| Prepared non-outfit previews | Replace runtime crop sources with approved display assets where possible. | Measure alpha and visual quality. Do not keep both image sets by default or change Outfit Selector assets. |
| Parsed inheritance and texture records | No shipped asset library required. Build during Import or Apply and retain validated local records. | Preserve source, build, mapping, producer, texture role, arrays, and dependency identity. |
| Compiled graph templates | Prefer a generated user cache over a shipped full per-asset blend library. | Key by complete recipe and compiler/Blender compatibility. Never use a user's edited material as the pristine template. |
| Immutable helper groups | Reuse existing helpers before adding more. Ship a small library only after measuring construction cost, reuse, and compressed size. | Share only identical computation. Keep per-asset parameter values and any differing resource bindings outside shared definitions. |

A serialized material plan can save parsing and preparation but cannot, by itself, remove Blender node creation. The compiler creates an asset-owned graph and binds resources. Whole-family sharing is unsafe without proof that the graph, resources, attributes, settings, and exposed controls are compatible.

Keep independent root material trees for assets that need independent sliders. Group reuse must not cause one asset's edits to change another. Fresh imports can use a pristine internal template. Never rebuild or relayout an existing user-edited graph because a cache entry changed.

Do bpy-free discovery, decoding, and plan preparation outside Blender's main-thread work where practical. Keep Blender node and image API calls on supported execution paths. A timer that runs a long synchronous function still blocks the UI. Measure the longest main-thread slice as well as total operation time.

## Reproduction and checks

Run the probes only in a private Blender profile. Put `TEMP`, `TMP`, Blender user directories, and Python bytecode on the task's Z scratch path. The retained fixture directory must exist. Each script imports this checkout directly, rather than the user's installed addon.

```powershell
$Audit = 'Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/import-performance-audit'
$Blender = 'C:/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe'
$env:TEMP = "$Audit/tmp"
$env:TMP = $env:TEMP
$env:BLENDER_USER_CONFIG = "$Audit/repro-profile/config"
$env:BLENDER_USER_SCRIPTS = "$Audit/repro-profile/scripts"
$env:BLENDER_USER_DATAFILES = "$Audit/repro-profile/datafiles"
$env:PYTHONPYCACHEPREFIX = "$Audit/pycache"
New-Item -ItemType Directory -Force $env:TEMP,$env:BLENDER_USER_CONFIG,$env:BLENDER_USER_SCRIPTS,$env:BLENDER_USER_DATAFILES | Out-Null
& $Blender --background --factory-startup --disable-autoexec --threads 4 --python-exit-code 1 --python "$Audit/enum_backup_probe.py"
```

Use `sidebar_probe.py` for the draw guard or `import_probe.py` for the cached import baseline. The latter is a measurement, not a graph-equivalence or speed-regression test. The enum command ran and failed as quoted. The import process exited successfully. The retained cached metadata log reports a passing focused suite. A separate earlier combined test invocation exposed the unrelated existing `test_zero_colour_override_keeps_the_decal_artwork` source-string assertion. No full-suite pass is claimed.

The shared documentation catalog rebuild and validation passed. The rebuild used `--skip-folder-index`, so it did not regenerate the game folder index. Wiki generation completed with an optional dependency warning. Full Markdown-to-HTML conversion remains unverified in this environment.

```text
Warning: pip install markdown for full HTML conversion
```

No release or install verification was needed because no runtime change or version bump was made. Runtime optimization, native GUI latency, fresh PAK import behavior, graph equivalence, per-asset edit isolation, and package-size changes remain unverified.
