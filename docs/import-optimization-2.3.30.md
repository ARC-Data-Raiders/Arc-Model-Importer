# Import optimization in 2.3.30

## Changes

Material texture inventories resolve paths without loading image datablocks. Opaque simple materials no longer load opacity masks, and the unresolved-material fallback no longer imports every PNG in a folder as disconnected texture nodes. After material construction, cleanup removes only newly created images with no users. Existing scene images and fake-user images remain intact.

The reviewed native catalog still validates every record digest, package identity, source path and activation context. It freezes records and projects compatibility metadata only when requested. Preset enumeration filters package keys before reading records. No validation check was removed.

ArcTexturer.blend contains immutable DR.Math.DecodeNormal.v1 and DR.Math.RNM.v1 groups. Native graphs instance these groups with per-instance inputs. Existing template groups have unchanged structural signatures. Mutable material controls and images are not shared through these groups.

Albedo sampling uses Cubic across the shared native binder and the face, body, eye, hair and fur paths audited here. Normal and data samplers retain their own sampling policy, including authored sRGB data textures. Shader-role cache keys keep albedo and data samples separate.

## Import measurements

Each row uses one fixed representative mesh in a fresh background Blender process. Times include mesh and material construction and the final dependency-graph update. Extraction and Blender startup are excluded. These are single samples, not timing distributions. The baseline and final cache conditions use the retained extraction manifest.

| Type | Representative | Baseline ms | Final ms |
| --- | --- | ---: | ---: |
| Charm | Lizzie | 5582 | 4699 |
| Backpack attachment | Antenna | 5203 | 4554 |
| Raider tool | 10 Mil Tool | 5297 | 4856 |
| Backpack | Arc Pack primary mesh | 6757 | 5790 |
| Body | SK_Body | 502 | 392 |
| Head | Face 01 Pio | 1787 | 1451 |
| Hair | Afro Fade | 616 | 471 |
| Facial hair | Beard Full | 540 | 599 |
| Outfit | Apollo jacket | 11372 | 8496 |
| ARC enemy | Boom | 5282 | 3595 |
| Weapon | Railgun primary mesh | 14368 | 13257 |

Every final representative has one mesh and no newly created zero-user images. Geometry hashes, object matrices and assigned material names match the baseline. The facial-hair sample is slower; the sample count does not establish a performance change for that type.

A separate synthetic graph benchmark builds 100 decode/RNM pairs in a growing graph. Inline construction took 23812 ms; shared groups took 121 ms. This is a graph-construction result, not an asset-import speedup.

Cold native-bank decoding and validation remain a multi-second cost. The validated catalog is reused within a session. The current changes do not make every first import instantaneous.

## Previews

All 912 canonical previews fit within 256 pixels on their longest side. New AVIF output uses quality 85, speed 6 and 4:4:4 subsampling. The previous producer used quality 60. Four retained source images were compared at qualities 60, 75 and 85. Quality 85 reduced the measured mean RGB error relative to the other two settings.

Original PNGs were available for four previews. Another 903 previews were resized once from existing lossy files; five already-bounded previews were unchanged. This includes the generated weapon compositions, whose original renders were unavailable. Every generated weapon filename now matches its encoded-content hash, and every index reference resolves.

The native pixel store and manifest were rebuilt and verified. Runtime PAK replacement remains disabled. Four separately stored source-preview derivatives are bounded to 256 pixels. Material textures and archival extracts are not resized.

## Validation and limits

The focused background Blender suite passes 25 tests for texture demand, orphan ownership, sampling roles, shared math parity and isolation, lazy catalog/metadata lookup, and Niagara material/renderer/operator behavior. Standalone preview and weapon-producer suites pass seven tests. Adjacent character fallback and authored-sRGB suites pass 15 tests.

The portable native suite has 46 failures and seven errors. Their failure identities match the source baseline; the new lazy-catalog regression is fixed. These preexisting failures are not a clean full-suite result. The separate system-Python texture-upgrade suite has six bpy-import errors on both source and staging.

No cached NS_*.json file was present for a real Niagara system import benchmark. Niagara has focused material, renderer and operator test coverage, but no measured real-system import result. No visible Blender UI, rendering, saved user blend, or hot reload was used.

## Evidence

The retained staging root is X:/Agents/DataRaiders-BlenderImporter/outfit-speed/tmp/work/browser. Evidence paths below are relative to it.

- temp/import-audit-inputs.json and temp/import-audit-baseline-*.json retain the baseline inputs and measurements.
- temp/import-audit-final-*.json and temp/import-audit-cubic-*.json retain the final imports and sampler audit.
- temp/import-audit-temp_run_release_regressions.py.log records the focused Blender suite.
- temp/native-source-tests.log and temp/native-final-tests.log record the broad source comparison.
- temp/normal-math-baseline.json and temp/normal-math-optimized.json record the synthetic benchmark.
- temp/preview-bound-report.md records AVIF measurements, lossy-source limits and native-cache verification.

Package size and installation results are recorded in the release receipt after the packaged-ZIP gate passes.
