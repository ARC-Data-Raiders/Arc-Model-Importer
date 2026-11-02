# UI revision candidate 2.22.13

Date: 2026-11-01. The candidate ZIP passed the private Blender install gate. It was not installed into the user's profile, and no live scene was changed.

## Workflow changes

The main panel keeps the existing Outfit Selector and preview layout. Weapons and ARCs show selection, preview and import first. Their batch actions and tuning are inside Advanced. Animations and Niagara FX show search, results and an explicit action. Lighting shows Map, Scenario and Apply before its tuning.

Settings contains global advanced controls, including Shader Parity, Mask Debug and FX tool/cache overrides. Shader Parity no longer registers a separate panel. Its root is an override for the build-specific parity-plan cache, not a required folder for ordinary browsing. Existing labels were reused rather than adding instructional sidebar copy. Explanatory property descriptions and static instructional enum tooltips were removed from the shared properties and playback settings. Game-sourced catalog metadata remains intact.

Niagara catalog selection resolves its source only when Import or playback runs. Import NS File uses its explicit file rather than the selected catalog entry. A missing or stale selection cannot silently import a previously used file. This revision does not add missing Niagara simulation or game-extraction capabilities.

Lighting and pose selection no longer apply changes automatically. Explicit import, apply and debug actions remain available. Shader parity does not install scene synchronization handlers. Native compiler materials expose their numeric preview controls and neutral surface multipliers as labeled root Value nodes above the shader group. Imported objects get independent controlled material roots while retaining their shared group definitions.

## Browsing and caches

Browsing reads packaged animation, FX, weapon, ARC and appearance metadata. It does not enumerate external folders, discover game installations, inspect PAK indexes or launch extractors. Backup roots are lazy action-time providers. Missing packaged previews remain missing instead of triggering a source search.

The catalog producers retain source identity and provenance. Animation/FX data uses typed retained 2.3.0 exports. Appearance labels use 2.3.0 localization, while authored backpack colorways and prepared non-outfit previews use the explicitly supplied 2.0.0 export. These sources are not claimed to cover every package in the installed game.

Complete validated extraction hits preserve downstream caches. Changed, partial and failed extraction invalidates them. Native-bank reuse is bounded and checks file identity before avoiding repeated hashing and parsing. Rewrites and incompatible schema/build/mapping contexts still require full validation. This is not an unchecked persistent material cache.

## Verification

Evidence root: `Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/ui-revision-implementation`.

| Check | Result | Evidence |
|---|---|---|
| Actual ZIP fresh install, enable, disable/re-enable and enabled overwrite | Passed | package.log |
| Packaged default/expanded sidebar and enum boundary | Passed | SIDEBAR_BOUNDARY_CHECK_OK in package.log |
| Packaged FX selected/direct-file action safety | Passed | FX_ACTION_BOUNDARY_CHECK_OK in package.log |
| Packaged production per-object control isolation and no parity watchers | Passed | CONTROL_ISOLATION_CHECK_OK in package.log |
| Required ZIP files match final source | Passed | package-verification.json |
| Outfit Selector preview drawing and artwork | Unchanged | coordinator-syntax.log; package-verification.json |
| Cached UEModel fixture imports with helper launches forbidden | Passed | import-revised.json; import-measured.log |
| Focused catalog, appearance, lazy-backup and cache safety checks | Passed | Worker evidence referenced below |

The package gate ran through package_addon.ps1 without SyncAppData. Blender reported INSTALL_CHECK_OK DataRaiders-Outfits 2.22.13 and exited successfully. The package check rejects Traceback or Error: output.

The measured real-code sidebar uses a headless layout recorder, not native widget rendering. Profiling includes its own overhead.

| Phase | Seconds |
|---|---:|
| Registration | 0.0207 |
| Default draw, first in process | 0.0517 |
| Default draw, repeated | 0.0009 |
| Expanded draw | 0.1528 |
| Deferred weapon registration | 0.0002 |

The measured registration/draw/timer phases recorded no directory enumeration or forbidden discovery/index calls. Native interactive latency and visual layout review remain unverified.

| Cached fixture | Earlier baseline seconds | Revised run seconds |
|---|---:|---:|
| Bettina base, first in process | 12.061 | 6.321 |
| Bettina base, repeat | 5.724 | 8.218 |
| Bettina charging handle | 5.984 | 5.771 |
| Bullcrab | 19.287 | 11.923 |

These are individual profiled runs, not speedup estimates. The repeat regressed in this sample. The revised imports retain image reuse but still compile new graphs. No general graph-construction speedup, cold PAK benchmark, visual parity pass or full-suite pass is claimed. The existing portable native suite still has unrelated weapon/paint failures, reproduced without importing either changed cache module.

```text
FAILED (failures=46)
```

## Package cost

| Item | Bytes |
|---|---:|
| Previous ZIP | 96,916,448 |
| Candidate ZIP | 108,955,803 |
| ZIP increase | 12,039,355 |
| Animation/FX metadata, compressed ZIP payload | 211,804 |
| Appearance metadata, compressed ZIP payload | 31,284 |
| Prepared non-outfit previews, compressed ZIP payload | 11,752,038 |

The increase is mainly the packaged non-outfit previews needed to avoid runtime export discovery. No per-asset blend library or full source JSON corpus was added. Outfit preview bytes match the previous ZIP.

Candidate: dist/outfits/DataRaiders-Outfits-2.22.13.zip.

SHA-256: b71de1fb3005d4b1e49b356b16937b810bae50f61b069aad3478b9cfbc3f226c.

## Remaining limits

Some retained appearance choices and Tech_ProjectileSplitter lack sourced previews. ClosedAlpha has no sourced backpack colorways. The manifests retain these gaps. Missing or incomplete asset investigation remains in FModel-Vibe.

Compiled recipe/template reuse remains future measured work. It must preserve resource bindings, source/build identities and independent root controls. This candidate removes browsing discovery and redundant validated-cache work, but it does not remove Blender's main-thread graph-build cost or promise responsive interaction during every explicit import.

Worker evidence is under the evidence root: catalogs/HANDOFF.md, weapons/HANDOFF.md, import-cache/RESULTS.md and the appearance implementation section in the sibling import-performance-audit/HANDOFF.md. Existing unrelated checkout edits were preserved. No commit or branch was created.
