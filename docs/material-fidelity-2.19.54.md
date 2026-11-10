# 2.19.54 — material fidelity checkpoint

This checkpoint fixes demonstrated material/data-pipeline failures. It does not certify complete game rendering equivalence or 99% perceptual accuracy.

## Changes

- Preserve connected dedicated layered/fur shaders through final named-material repair. Previously this pass replaced valid layered backpack shaders with simple materials.
- Recover exact native material slots from UEModel and authored DA_VP bindings. Selected shell colorways no longer overwrite lantern/glass/other independent sections. Per-section rebuild transactions restore unrelated slots, face indices and selection metadata on failure.
- Resolve moved cache roots and PioneerGame/RemappedPlugins mount aliases by exact package identity. Preserve original mesh provenance and PSK unit semantics. Missing native source/colorway records stop explicitly instead of silently borrowing defaults; offline existing caches remain supported.
- Resolve named MI texture roles, including shared maps outside a mesh folder. Normalize long Windows paths before texture, JSON, sidecar and timestamp IO.
- Correct inherited zero/false values, typed cached defaults, dependency cache invalidation and sRGB/linear image-role isolation, including packed/generated RGBA preservation.
- Make palette groups immutable and content-addressed so later colorways cannot recolor earlier imports.
- Port evidenced dry layered specular/roughness/metal wear, cloth angular response and hue variation; preserve working decal placement math. Correct native PNG UV transforms and medium-normal sampling.
- Port current native body/face packed normal/roughness/cavity/tint/Fresnel controls. Distinguish eye occlusion and metallic tear-line profiles. Port Hair ColorMap dry specular controls only for the proven family; hair transport remains an approximation.
- Replace gray-diagonal grading with a full cross-channel 3D LUT. Preserve raw reference bypass through LUT, bloom and manual mesh grading.
- Remove hidden 128-package folder truncation in the exporter and deduplicate mount aliases. The Release helper passes synthetic boundaries and resolves all 1,882 current outfit data assets.
- Replace repeated whole-graph socket-link scans with linear connectivity indexes. Prevent map material repair from selecting untagged character meshes.

## Verification

See [coverage ledger](material-parity-coverage-2026-10-26.md) for per-family evidence and renderer limitations. Portable regressions live under tests/, require explicit scratch output for Blender runs, and use private profiles. Workstation runs are capped at four CPU threads and write test/cache/staging data on Z:.

The actual packaged ZIP must pass package_addon.ps1's private-profile install, enable, disable/re-enable and enabled-overwrite gate before distribution or profile sync. Source import alone is not release verification.

Saved shader trees need rebuilding to acquire these changes. Original user blends are preserved. Repairing all saved outfit splits is tracked separately; this checkpoint does not claim those files are already migrated.

## Remaining visual work

Native hair scattering, eye corneal refraction, engine subsurface transport, runtime primitive weather inputs, unresolved environment permutations and matched camera/light/exposure/tonemapping remain separate evidence requirements. Full LUT sampling does not implement Unreal's tonemapper.
