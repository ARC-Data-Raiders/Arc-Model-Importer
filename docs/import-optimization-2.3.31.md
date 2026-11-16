# Cold material-bank validation

The catalog reuses canonical JSON encoders during cold bank validation. It keeps every record digest, package identity, source-path check and reviewed-limit check. The bank format, bank bytes, activation manifest and validation cache policy are unchanged.

## Implementation

Arbitrary caller input uses an encoder with circular-reference tracking. Only internally owned JSON-decoded trees use an encoder without that tracking. JSON decoding cannot produce cycles. Both encoders preserve the existing sorted-key, compact, finite-number-only serialization contract.

The runtime change is in Shared/materials/native/catalog.py. The regression is tests/native_runtime/test_native_catalog_hash_revision.py. The maintainer remains Silarious.

## Measurements

The probes use the same bank and matched source dependencies in separate fresh background Blender processes. Blender startup is excluded. These are single samples, not a timing distribution or a universal import-time guarantee.

| Phase | Baseline ms | Candidate ms |
| --- | ---: | ---: |
| Decode | 779.421 | 762.407 |
| Construct and validate | 1227.988 | 1112.456 |
| Reviewed limits | 152.466 | 147.682 |
| Digest hashing, included in construction | 936.392 | 845.003 |

| Input | Value |
| --- | --- |
| Material records | 13,343 |
| Texture metadata entries | 17,706 |
| Materials affected by reviewed limits | 11 |
| Bank SHA-256 | 40ebeb10ddb8aaa697e104b0815b7ce0cdafd248bae2148c39b07d6094aea0de |
| Blender | 5.1.2 |
| Blender Python | 3.13.9 |
| Focused regression Python | 3.14.4 |

## Verification

The focused suite passes all 30 tests. Coverage includes encoder reuse, canonical-byte parity, Unicode and control values, nonfinite refusal, cyclic caller-input refusal, concurrent encoder use, hashing of unused records, tampering, package identity, unsafe source paths and ownership-specific encoder selection.

The encoder-allocation regression failed on the original implementation with AssertionError: 5 != 0. The release preflight also rejected unaccounted package differences before the verified packaging-generated metadata was classified separately. The fresh guards preserve concurrent source and installed changes rather than overwriting them.

The safety condition is unchanged canonical bytes and unchanged validation coverage. The focused tests execute both checks. The actual packaged-ZIP gate must pass before source promotion and installation.

The exact release ZIP passed the private-profile Blender installation gate. Installation, enabling, disable/re-enable and overwrite of an enabled install succeeded. The sidebar, FX, control-isolation and idle-work boundary checks passed. The workflow verifies every installed immutable ZIP member before recording completion. The final receipt records the installation result. A full Blender restart is required. Hot reload and visible UI behavior remain unverified.

## Scope and limitations

This change does not alter asset selection, mesh construction, texture loading, preview encoding or material node graphs. No new representative asset imports, renders, visible UI checks or user blend saves were performed. The broader native suite retains the previously documented failures in import-optimization-2.3.30.md. A real Niagara-system import benchmark remains unavailable because no cached NS JSON input was present.

Blender admission remains a CPU/RAM headroom check. GPU-required callers can qualify either GPU independently. There is no fixed Blender process count or thread cap.

## Evidence

Retained evidence is under Q:/Agents/DataRaiders-BlenderImporter/outfit-speed/evidence. The release ZIP is builds/Arc-Asset-Importer-2.3.31.zip under the same outfit-speed root. Active scratch remains under X:/Agents/DataRaiders-BlenderImporter/outfit-speed/tmp/work/browser/temp.

- native_cold_probe.py records the coarse probe and input identities.
- native-cold-blender-baseline.json and native-cold-blender-candidate.json contain the matched-bank timings.
- native-cold-regressions-release.log records the focused test result.
- native-cold-release-guards.json captures source, installed and documentation hashes before promotion.
- native-cold-release-receipt.json records packaging, promotion and installed hashes after completion.
- native-cold-promotion-plan.json records the exact owned writes and their destination guards.
- native-cold-rollback.zip preserves the replaced source, documentation and installed files.
- package-2.3.31.log records the headroom admission and actual-ZIP Blender gate.
