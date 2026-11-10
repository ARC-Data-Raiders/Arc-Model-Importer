# Material parity coverage — 2026-10-26

This ledger distinguishes reconstructed material arithmetic from Blender rendering approximations and unresolved families. It does **not** claim 99% perceptual parity or complete game shader coverage. Native evidence uses targeted masters and static permutations extracted from installed Steam build **25979580**, with matching shader resource hashes. Legacy 2.2.0 export defaults count as corroboration only where independently compared; they do not establish full equivalence with the installed build. Lighting, camera, engine BRDF, skin scattering and display transforms require separate visual validation.

## Evidence and reporting rules

Game → shader translation → user meaning: current native material defaults/static permutations and DXBC instructions establish the equations; the addon translates those equations into Blender nodes; a Cycles bake tests numerical response. A successful import, a matching parameter name, a generic material, or a screenshot alone does not establish native parity.

Preshader field offsets require a per-material constant-buffer bias, independently checked against resource MemberOffset and UniformPreshaderBufferSize. Concrete and Facade require +112 bytes; InteriorFloors, Layered4/8, Urn and the examined Face/Skin permutations require zero. Preshader parameter labels can identify derived values rather than the original parameter: decode the program before assigning an equation.

Source routing is in `materials/classify.py` and `materials/dispatch.py`. A family route proves that a builder exists, not that its shader has been reconstructed. `surface_rebuild/materials.py` keeps `engine_parity_verified=False`; generic profiles emit explicit warnings. `revamped_bridge.py` preserves recognized native root graphs rather than overwriting them with a generic second build.

## Coverage by native profile

| Native profile / dispatch family | Reconstructed or preserved | Remaining limits | Evidence |
|---|---|---|---|
| Layered character/outfit materials; Layered4/8 and Urn sampled | Inherited palette and texture arrays; Base→Crease→Edge metallic/specular/roughness; medium normal mask and RNM; linear RGB pattern weights; native dry ShadeAsCloth angular specular; native UV1 HueVariation after wear with pre-Hue emission; independent colorway palette groups | Engine wetness/snow need primitive data; reconstructed OCM mips approximate engine filtering; native shading and all material permutations remain unverified | Current Layered4/8/Urn pixel shaders; dry-colorway and UV bakes |
| Existing DataRaiders clothing decals | Existing placement, image, authored mask and material controls preserved; retained through material rebuilding | Preserve the established ArcDecal path; current Urn has compiled surface/wear/normal overrides and must not inherit historical claims that all layered permutations lack decals | Current Urn resource `4D173095A6A3A14CFD3AA8D6B1EF73928A29B91E`; backpack preservation regression |
| Packed props `M_PropPreset_CR+NOM`, skinned variant, `M_PropPreset_CR+NOX+XEM` | Explicit packed channels, CR alpha roughness exponent, metallic strength including zero, normals/detail normals; no AO multiplication into diffuse for recognized native packed-prop graphs | Additional dust/paint/blend/waterline and other complex switches are reported as unsupported | `packed_prop_core_v2`; root preservation schema `packed-prop-v2`; surface bakes |
| XEM emissive packed props | Authored Use Emissive gate, histogram, emissive intensity/color and CR-emissive selection | Static appearance; pulsation animation/time response is not certified | Current native master defaults and emission bakes |
| Item preset CR+NOM and specialized lights/lanterns | Existing dedicated root graph retained by explicit schema; pure lights can be valid without texture maps | Profile preservation does not certify every item variant or temporal animation | Bridge preservation and untextured-light regression cases |
| Concrete_01 active dry permutation | Native range/overlay, detail albedo, detail and blend normals, global normal strength order, CR/NOH blending, local-height RGBA paint opacity/thickness/breakup, dry geometric-view Fresnel | Waterline, triplanar overlay, world-space-height paint, FullPaint, TwoTone, detail roughness, VT/intersection and other untraced permutations remain explicit warnings | `concrete_native_dry_v3`; current Concrete `70F7F9D5169546FB734883B5A88B29C50E1529C0` and enabled Facade `DAC0492F75DF53A7A47530B89A8D8E349620F8B4`; surface bakes |
| InteriorFloors and other architecture masters | Current native InteriorFloors shader extracted and inspected; generic builder remains available | **Not reconstructed.** Instance-axis/world-projected UVs, layout/flip, mask-dependent normals/PDO and a different dry roughness response prevent treating this as Concrete or a generic two-color blend | InteriorFloors resource `EEB2ED5E2D300D15D585EAA15B74D8CE4007C639`; no parity claim |
| Generic packed and layered environment surfaces | Texture resolution, packed-channel handling, UV transforms and a useful Blender preview | No master-specific reconstruction; layer masks, triplanar normals, paint and overlays may differ substantially | `generic_packed_channels_v1`; environment V1 warning in `surface_rebuild/surfaces.py` |
| Face and body skin | Current native normal-channel reconstruction, parent-aware controls, decoded contrast/color multipliers; skin luminance and micro-UV aspect corrections; tested static/facing responses | Skin SSS transport, matched game lighting, paint/weather permutations and end-result appearance remain unverified | Current M_Face/M_Skin native programs; 13 face + 5 body equation/facing probes |
| Eyes | Authored iris/pupil controls and static opaque reflection/F0 response | Native corneal refraction, multilayer eye transport, engine SSS and game-comparable appearance are approximations | 3 opaque reflection probes; explicit limitation in eye result |
| Hair and facial hair | Authored card textures/coverage, color selection, vertex-color data and available tangent controls | Blender hair/card BRDF, scattering, transmission, anti-aliasing and engine groom/card shading are not certified native parity | Existing character fidelity implementation and native parameter review; no full BRDF equivalence claim |
| Native shell fur / FurLOD | Dedicated dry material equations, authored coincident-shell index/COL0 length mask, native source-normal offsets and owned geometry modifier already reconstructed; no invented shell count/subdivision. Known 27 scalar + 6 vector defaults per examined master match legacy 2.2.0 exports | Engine BSDF/scattering, weather, animated wind, proximity fade and morph-normal deltas remain approximations/unhandled. Installed-build shader binary equivalence needs targeted extraction; matching defaults alone does not establish it. Unrecognized legacy fur fallback stays explicitly approximate | Historical 2.1.0 native resources `39527D9A…` / `E6E0456E…`; 83 geometry/material checks and 8 shader bakes in `.local/21923/fur-audit`; independently compared 2.2.0 defaults |
| Rabbit Foot hair ColorMap cards | Distinct `M_Hair_Metahuman_ColorMap` profile; authored strand-offset color UVs, native tiling/final V conversion, last-UV clamp, explicit Coverage precedence and sRGB color handling | Native hair scattering/transport remains unresolved: authored Scatter/RootScatter are not mapped; Principled anisotropy is heuristic. Existing card-shaped lighting discrepancy is not fixed by shell deformation. Revamped explicitly omits this ColorMap branch | Historical resource `7213E480…`; 4 ColorMap UV/blend checks including noninteger tiling, byte-identical fresh mesh/coverage comparison, and controlled renders recorded in `.local/21923` |
| Enemy / weapon / trim / foliage / tarp / sand / glass / water / display / flipbook | Dedicated routes and specialized builders exist; enemy decal classification avoids confusing architectural trim with enemy decals | No blanket current-build reconstruction or perceptual certification for these families; animated/display/water/transparent/light transport require per-master evidence | `materials/dispatch.py` family dispatch and `materials/classify.py`; family existence alone is not parity evidence |

## Cross-cutting fixes and limits

- Material inheritance uses native sidecars and typed static-switch metadata; unset/zero values must not be replaced by truthiness defaults.
- Images used as both sRGB color and linear packed data get distinct role-compatible datablocks. Generated/dirty/packed snapshots preserve numerical RGBA when copied; colorspace is established before restoring pixels and packing.
- Native UEModel UV import stores Blender `(u,1-vUE)`. Do transforms in Unreal space, then sample PNG at `(transformedU,1-transformedV)`. Array maps, Pattern, ColorTex, BreakupMask and medium blur now follow that contract. Noninteger scale is `1-T*vUE`, not `T*(1-vUE)`.
- UV layout/rerouting must never erase scale or final PNG orientation. Existing saved materials need rebuilding to acquire node fixes.
- Palette node-group identities must include the full material/content identity. Truncating names to 28 characters caused different colorways to overwrite previously used palettes.
- References must remain a separate comparison baseline. Compositor corrections, studio lighting or reference planes do not prove a shader equation is correct, and cannot substitute for comparing the original UI image.

## Reproducible validation

Task-owned artifacts are under `Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/material-parity-20261026/`. Blender runs use private profiles and CPU limits; this documentation change launches no Blender and bumps no addon version.

| Result artifact | Latest observed result | What it establishes |
|---|---|---|
| `surface/surface-regression-results.json` | Pass, 31 checks | Packed channels/emission, native graph preservation, unknown-profile honesty, Concrete normal/paint/Fresnel arithmetic and unit-sensitive cache behavior |
| `surface/uv-regression-results.json` | Pass, 28 recorded cases | Actual asymmetric PNG samples, imported native UV convention, fixed GT vs Revamped agreement, old mirror reproduction, noninteger tiling and medium curvature orientation |
| `native-dry-colorway-results.json` | Pass, 16 checks | Native dry cloth response, Hue response, reproduced old palette-name collision and independent fixed colorways |
| `character/face-response-results.json` | Pass, 13 rows | Static/facing skin equations, not engine SSS or final visual parity |
| `character/body-response-results.json` | Pass, 5 rows | Body equations, not matched game lighting or SSS |
| `character/eye-reflection-results.json` | Pass, 3 rows | Opaque eye reflection only |
| `character/eye-aux-regression.json` | Pass, 14 rows | Eye auxiliary products/routing; native opaque scene-depth gap remains an explicit approximation |
| `inheritance/blender-role-results.json` | Pass, 15 checks after alpha-copy correction | Role isolation/reuse, packed and generated RGBA preservation |
| `backpack-material-regression-current.json` | Pass, 30 current backpacks, including repeated rebuilds | Real saved backpack meshes; geometry/UV/morph invariance, preserved decal controls, repeat-build invariance, native Urn pattern/medium/tile checks |

Passing these tests supports the listed arithmetic and regression guarantees. It does not measure a percentage of in-game appearance, exhaust every master/permutation, or certify unsupported transport. Next evidence priorities are InteriorFloors instance/world-space inputs, native hair/fur transport and installed-build shell permutation verification, eye corneal refraction, hair scattering, weather primitive inputs, and matched camera/lighting/color-management comparisons.


### Dedicated material ownership during named-slot repair

`operators.apply_materials_to_object` successfully built layered backpack shaders,
then its generic named-slot repair replaced them with `simple` materials. Generic
family dispatch does not implement the character layered graph. The late overwrite
was observed in the current Toolbox, WaterTank, Vessel and other backpack probes.

`materials.dispatch` now preserves output-connected Ground Truth, ArcTexturer and
fur owner graphs for the requested MI identity. Unhandled secondary slots still
use ordinary family repair. Explicit changed per-slot MI requests are rejected; a
stale single-owner selection remains pending for its dedicated builder. Objects
with multiple independently layered sections retain their separate MI identities,
rather than treating the primary object's selection as a global material.

Portable native regression: `tests/test_dedicated_material_postrepair_blender.py`.
All 16 native ownership checks passed, including the distinct-section selection case. Run with an explicit `--output` scratch
path. These checks establish dispatch ownership and source identity, not visual
parity with the game.


## Final checkpoint validation additions

- Portable character suite: 35 probes passed. Native ColorMap dry specular: 13 Blender bake cases passed, scoped to the proven family; engine hair transport remains approximate.
- Saved source/colorway recovery: 22 pure cases passed, including offline caches, exact identities, missing-native failure, and preserved mesh-format provenance.
- Native clothing section ownership: five pure cases plus actual Props migration/repeated update passed. Native Lantern remains slot 0; selected Metal owns slot 1; geometry/UV/morph/transform hashes remain unchanged.
- Windows long paths: four pure regressions plus actual TreasureHunter export passed. All four 2048px maps load, native decal 2 LayerMask 8 retains Allow4=1, and geometry remains unchanged.
- Named MI texture roles: four focused cases passed. Shared texture references supersede folder-name guesses; missing authored bindings are explicit.
- Connectivity: eight dedicated-graph cases and three UV-bus cases prove one link pass, including 2,000-node fixtures that reject socket.links access. The optimized native UV suite still passes all 28 cases.
- Full LUT: eight standalone CPU/GPU renders and 14 integrated stack cases pass. Reference pixels bypass LUT, bloom and manual grading unchanged.
- Exporter Release helper: synthetic 127/128/129/513 packages pass; actual index resolves all 1,882 requested outfit data assets. Runtime export prepared 6,652 queued assets and 2,894 JSON records for subsequent outfit repair.

The review checkpoint contains three current rebuilt backpacks with reachable textures packed. Original user blends are untouched. Saved outfit split migration remains a separate pending operation; preparing dependencies does not repair those blends.

## Verified 2.19.55 slot and mount additions

| Evidence | Result | Scope |
|---|---|---|
| `tests/test_outfit_preset_slot_bindings.py` | Five portable cases pass | Full visual-part identity, numeric/named material selectors, order-independent shell selection, explicit conflicts and plain Headgear exclusion |
| `patcher-slot-integration.json` | Nine actual Blender cases pass, all repeated updates pass | Basic/Black/White on HeadgearTech, MaskSocket and Pauldrone; exact layered slot 0 and static CRT slot 1; geometry/UV/morph/transform invariants |
| `tests/test_windows_texture_paths.py` | Eight portable cases pass | Game compatibility, plugin/Engine aliases, latest payloads, Windows long paths and rejected traversal/non-Game BFS |
| `plugin-mount-current-proof.json` | Three actual current extraction files resolve | EmbarkScript Hue noise, Engine DefaultTexture, and Game Abyss Airbag normal; filesystem resolution, not a new shading/render parity measurement |
| `release55-identity.json` / `package-2.19.55.log` | Packaged install gate passes; AppData sync verified | Actual ZIP install, enable, disable/re-enable and enabled overwrite in private Blender; local checkpoint `8fbabe1`, no public push; live GUI not restarted |

Current Patcher modifiers use MaterialSlot 1 for CRT and default slot 0 for the shell. A normalized part folder alone cannot represent all overrides: full DA_VP.Mesh identity and the material selector must survive parsing and application. Every selected slot is audited against the actual material source, rather than accepting an object-level selection stamp as proof.

The saved-split repair journal had a separate tuple/list false positive for armature pose rows after JSON reload. Actual original/checkpoint rig diagnostics matched exactly across raw/update/addon phases. JSON-stable list rows fix resume equality without any numeric tolerance; a 1e-9 pose change remains rejected (`rig-resume-container-proof.json`). This was a scratch harness correction, not a pose change in the addon.

Original blends remain untouched. The first split (rows 01-02) completed 20/20 binding groups with zero errors. Its mixed 2.19.54/55 report retains 538 optional authored-reference warning rows from earlier groups and zero required-image misses; this is not an all-textures-resolved claim. All 538 historical warning references now resolve under 2.19.55. Exact native records show active Hue variation in all 165 warned object/MI pairs and active pattern gates in 29 of 43 pattern-warning pairs; current slices resolve. The first 17 groups built under 2.19.54 still need a 2.19.55 refresh to acquire those features. This distinction prevents treating a binding-complete report as a feature-complete shader result. The remaining 15 splits are in serial migration, tracked by per-file checkpoints and reports. Read-only `summarize_split_materials.py` writes the evolving `split-material-summary.json` and `split-material-ledger.json` under the task scratch root without changing active reports or blends. This coverage page makes no all-split completion or 99% appearance claim.



## Verified 2.19.56 provenance and migration gates

- Five portable visor source tests and five actual fresh HydroScav outfit-variant rebuilds pass, including repeated updates and geometry/UV/morph/transform invariance (`hydroscav-visor-provenance.json`). A successfully parsed exact MI/dependency signature is recorded; missing/unparsed records cannot claim stale provenance.
- Bundled/manual Visor owner graphs are classified as `visor_approx`/`visor_approx_manual`. Their native engine parity flag remains false. This is honest source preservation and routing, not a verified native visor BRDF or refraction model.
- The packaged 2.19.56 ZIP passed the full private install/re-enable/enabled-overwrite gate and source/ZIP/installed identity verification; AppData was synced. Local checkpoint `3c2a816` preserves active branch/index, has no public push, and does not imply the live user Blender restarted.
- Seven pure scratch retry/gate tests pass. A retried group replaces only its own obsolete global audits; other completed groups are retained, and fresh retry failures remain visible. Manager pass checks reject generic-profile and required-image misses, while the first report's 538 historical optional-only rows remain accepted as binding-complete evidence.
- The remaining 15 splits resumed with manager 37756; supervisor 31024 is confirmed waiting for that manager's success before rebuilding the original first split fresh under version 2.19.55 or later. Neither worker was launched by this documentation update. The active migration and optional-feature refresh remain in progress.


## Independent opaque SM6 and verified 2.19.57 proof

All six current opaque-master SM6 pixel programs (three quality resources) are recovered and disassembled; their paired SM5 preshader data/fields/parameters/defaults are equal. Independent actual-DXIL dependency evaluation passes 192 dry fixtures, 768 output-group checks and 1,536 scalar comparisons. This is a static instruction evaluator with sample fixtures, not native GPU execution (`opaque-sm6-proof/opaque-sm6-equivalence.json`).

It caught a real shared-oracle error: native metal attenuation uses snow, not dirt. Dry metal is `sat(authored Metallic)`. Canonical/scratch code, the regeneration oracle and three dirty cases in each golden copy are corrected; authored parameters remain unchanged. Both pure suites pass six cases without relaxed tolerances. Prior CPU/GPU results using the wrong goldens are superseded. Corrected CPU and GPU rendering each pass nine cases, including constant dry metallic 0.309122. Five HydroScav and four Banshee real-asset cases were retested after the correction and pass; CRT four and fur eight repeat/geometry checks pass. The packaged 2.19.57 install/re-enable/enabled-overwrite gate exits 0, and source/ZIP/installed critical-file identity checks pass (`release57-identity.json`).

Opaque Alpha 1/Transmission 0 and DefaultLit are supported by native deferred outputs and absent opacity/refraction numeric inputs. This does not establish transparent-glass optics, arbitrary weather/vertex inputs, engine BRDF/lighting/SSR, or 99% visual parity. In particular, MaxSnowAmount 0 alone is not a proven pixel-only snow-disable condition.


The verified 2.19.57 local checkpoint is `18b4195`; active HEAD/index remain unchanged, with no public push or live user Blender restart. Manager 29520 and matched supervisor 19720 are active for the remaining migration and subsequent fresh first-split rebuild. The earlier second-split v56 output/report is safely retained under `superseded-second-v56-backup`; original inputs and the prior first-split 20/20 checkpoint remain protected. Full split completion and perceptual 99% accuracy are not claimed.


## Scratch migration durability correction

Ten tests pass for the Windows status-file sharing fix and five-successful-instance save cadence (`atomic-status-and-cadence-proof.json`). The real filesystem test holds a reader without ShareDelete, releases it after approximately0.075seconds, and confirms replacement succeeds after retry. Unique closed sibling temps, bounded WinError5/32/33 retries and explicit error propagation protect the last written status/checkpoint. Other completed groups and original blends remain untouched.

Checkpoint counts are successful outfit/colorway instances, including multiple variants of one outfit: saves occur immediately at5/10/15...successful groups, with final save required. Failed/skipped groups do not count. Manager30448 and supervisor41420 are matched on the unchanged verified2.19.57 runtime. The overall split migration/first fresh feature rebuild remain in progress.

Hair's separate Blender candidate proof is deferred until all repair workers are idle: automatic approval review rejected parallel execution under the single-private-Blender limit. Existing36 pure point fixtures and20 SM6 tangent comparisons do not become Blender validation or a version58 release claim.


## Measured layered import construction costs (2026-10-26; candidate only)

### Game

The bounded current native outfit cache contains2,572 resolved layered/clothing MIs. Current-parent traversal was used with the historical packaged material database disabled; zero resolution failures occurred in this inventory. No game shader equations were changed.

### ST

No localized outfit names establish cache equivalence. Exact master, scoped named parameters and texture package identities must match. Removing only ColorA/B/C/A2/B2/C2, including scoped keys, leaves2,559 distinct nonpalette signatures:13 repeated pairs(26MIs), only five pairs with different palettes. Thus a strict palette-only Material.copy route could avoid at most13/2572 builds(about0.5%) before mesh OCM, native slot, UV, output settings and full parser constraints; this is necessary-condition eligibility, not a measured implementation hit rate.

### Human

A20-second py-spy sample attached only to command-verified task background Blender35924, using nonblocking49Hz sampling, affinity15 and BelowNormal profiler priority:977samples, two read misses, exit0. This window was dominated by layered graph construction:finishGT95.8% inclusive; exclusive Mix creation17.91%, Math11.67%, place8.60%, linking8.29%, medium Math6.24%, Value6.04%, UV orientation4.81%, UVreroutes4.20%. Attribution includes Blender RNA/C time. One late construction window does not establish full cold-import percentages; historical props profiling separately measured about3.08/10.12seconds in extraction waiting.

A scratch-only narrow candidate skips redundant default setters and repeated hide/image placement. Six fake-RNA tests pass identical final properties and slice-cache behavior; simulated setter counts ADD5→3, MULTIPLY5→4, RGBA Mix8→5, FLOAT Mix8→4, slice6→3. Native timing, graph render and package gates remain unrun for this candidate. Current addon2.19.57 stays unchanged. A separate immutable math-group prototype is explicitly unvalidated and lower priority.

Blanket location deferral is unsafe without a logical-position layer: Array UV reroutes group by world-Y, input placement reads host positions, a normal/UV helper reads source.location, and output framing preserves world coordinates during parenting. Duplicate exact placement can be removed without deferring semantic readers. Future background work should overlap only bounded bpy-free exact extraction/JSON/mesh decoding with current main-thread graph construction, preserving four-core limits. Do not call bpy from threads, create another Blender, weaken MI identity or reintroduce full-PNG prefetch that previously saturated disk.

Evidence: designated task scratch `performance-late57/{FINDINGS.md,ownership-before.json,worker35924.speedscope.json,function-summary.json,rna-operation-proof.json,palette-template-inventory.json}`. No measured speedup, all-split completion or99%appearance claim follows.


## Deferred hair light transport: native SM5 branch (2026-10-26; scoped proof)

### Game

Existing installed globalSM5 artifact evidence identifies descriptor4663/hash02EBD589764E1566B28C4F3A7625745D5A37B95C, mapEBE8B673B2665C0F6604D803234CAD774850437B, associationordinal2(group412,offset451120). The ordinal is not a known runtime permutation ID. GBufferalpha is decoded uint(alpha*255+.5)&15; case7 at1787–1976 is the hair response branch. Exact shader-type and uniform-field names are stripped/unrecovered.

### ST

No localized outfit or hair name establishes transport equivalence. Only exact ID7 dataflow and current material GBuffer ownership connect this branch to hair. Map/resource associations do not establish active runtime feature flags, lights or SM6 equivalence.

### Human

A bounded offline scan examined778already-saved globalASM candidates;128contain both the low4IDmask andcase7. No new dump/extraction, Blender or game process/memory access occurred. SelectedPS4663 samples t5RGB as encoded tangent(normalize(2RGB-1)), t6 as Scatter/Specular/Roughness/packedID, and t7RGB basecolor. Roughness is bounded by cb0[270].w then clamped in the hair path. Ordinary branch uses featurebits39; a special path uses cb0[272].z|32. Three bits1/2/4 gate separate anisotropic contributions structurally consistent with R/TT/TRT, while RGB powers and absorption affect later lobes. Those symbolic lobe labels remain interpretation until type/source metadata confirms them.

The first lobe contains actual tangent-angle/Gaussian math and Schlick constants.046521/.953479. Its75instructions1788–1862 replay against independently expressed geometric equations:96seeded normalizedT/V/L, roughness, specular and shadow fixtures pass288scalar comparisons below2e-6,maxerror1.92e-8. Unsupported instructions fail explicitly. This is Python arithmetic, not nativeGPUFP32 proof. The final branch also uses a sqrt(basecolor)*Scatter/pi angle-dependent contribution, so placing Scatter in generic PrincipledMetallic does not reproduce native hair closure semantics.

Remaining proof targets are full TT/TRT/Scatter numerics, exact current globalSM6 counterpart, runtimeuniforms/lighting and renderer integration. A numeric emission atlas with explicit light/view vectors can test math but cannot certify a productionBSDF; standard Blender material nodes do not expose arbitrary per-incident-light direction in the same way as this deferred shader. A calibrated PrincipledHair approximation must remain labeled approximate. No canonical source change, Blender test, package/release or99%appearance claim follows.

Evidence: designated task `hair-transport-native/{PROOF.md,hair-transport-evidence.json,4663-hair-case.annotated.asm,prove_first_lobe.py,first-lobe-equation-proof.json}` retains source/map/binary hashes and an implementation/test plan.


## Released 2.19.58 checkpoint

The actual packaged ZIP passed Blender 5.1.2 install, enable, disable/re-enable and enabled-overwrite checks, exit 0. AppData sync followed the successful gate. Fifteen critical source/ZIP/installed files were checked; only the documented staged name/description rewrite is permitted in the entry module. The canonical helper SHA256 is `296965cdf19b84b0b0223d0e7ef98f7c2e53ae11aa3fa46aef56f8ec6d0b06f1`. Canonical portable suites ran 98 tests: 97 passed, one optional real-game fixture skipped; its separate actual-game receipt roundtrip passed. The running user Blender was not restarted.

Raw material receipts now bind the captured full game/mapping/container revision and complete Parent-content hashes. Source changes during export or commit reject completion; identical shared-parent content rewrites retain readiness. Native table metadata validates 1,077 models without changing model bytes. The selective saved-file plan refreshes ten proven binding groups and performs the mandatory fresh first-file rebuild. Migration completion and matched-reference 99% perceptual accuracy are not claimed.


## Supplied Revamped 1.10.1 comparison (2026-10-26)

Compared the user-supplied Revamped 1.10.1/ARC2.2 Python tree against DataRaiders 2.19.58. The supplied Python-tree SHA256 is `df54c0190e10c55a43f9fec38fb49482b3f8e14485001ab26bb0efcfc526348a`. This is a source/control-flow comparison with eight pure reproductions; no Blender scene was changed and no new matched-reference rendering accuracy is claimed.

### Game

Relevant native identifiers include `M_Character_Layered`, `M_EnemyPreset_Masked_NAO+Detail`, `M_WeaponPaint`, `M_FurShells`, `M_FurLOD`, `M_Skin`, `M_Hair_Metahuman_01`, and `T_HueNoise_01_C`. Upstream data manifest reports ARC2.2, 13,604 materials (8,780 compiled plus4,824 character),14,591 texture metadata records and2,176 loadout records. The old vendored material bundle still contains7,847 records from1.42. Our current primary Pak extraction supplies current game records separately; the old bundle finding does not mean every imported material uses old data.

### ST

No localized outfit/weapon display names establish shader equivalence. Package identity, native sections, material master, scoped parameters and texture channels determine the comparisons. The Recon shared-material issue is identified by exact section bindings, not its display name.

### Human

The primary product paths are different. Our Pak and PSK imports converge on custom domain dispatch, the GT outfit builder, native character/enemy/weapon implementations, and the vendored surface bridge for props. Revamped primarily consumes exported PSK/JSON through its MaterialLibrary and per-slot builders. Replacing only our vendored character module would not update the primary GT outfit shader.

| Area | Supplied Revamped1.10.1 | Our2.19.58 / consequence |
|---|---|---|
| Reused material in multiple sections | Per-slot assignment naturally permits identical MI in several slots. | One-owner clothing selector rejects that valid situation. This is the actual current Recon29–31 repair failure. A separate fix is staged, not installed. |
| Whole-outfit coverage | OCMID.A plus authored Masked cutoff reaches final Alpha. | GT finisher has no material Alpha connection; decal alpha is separate. Clothing parser also replaces cutoff0 with0.3333. |
| Disabled layered branches | Filters detected atlas layers using UseLayer2–8 before array loading. | Primary GT/clothing does not consume those switches. Nine current raw MI records explicitly disable layers, mostly specialized visor/CRT cases; affected outfit pixels still require testing. |
| Surface cache freshness | Material/image keys include resolved texture-file dependencies. | Vendored surface material/image keys can reuse stale PNGs after a same-path re-export. Primary native/common image caches already track freshness. |
| Source lookup and fallback data | Current2.2 data, strict full-mount misses and better standalone/plugin export layouts. | Vendored bundle is1.42 and its suffix fallback can resolve an Engine reference from a Game package. Current primary Pak metadata validation is separate and stricter. |
| Primary Pak compatibility | Upstream raw parser is built around FModel exports. | Our Exports wrapper and typed RuntimeEntries support must be retained: a pure probe shows upstream raw_record drops the ArcPak wrapper parent/scalars. |
| Weapon paint | Squared ID masks; base/mid/pattern paint, wear delays/resistances and independent wear preview. | Active native weapon path still has simpler tint/scuff logic; older large weapon-module code is not the active firearm route. Preserve our explicit lamps/screens. |
| Enemy displays | Separate Scan/Loop/Blink state equations plus damage and six-octave reconstructed glitch. | Some state rules are shared too broadly and damage/glitch are omitted. Reciprocal-vs-linear camera-distance disagreement needs current native permutation proof. |
| Enemy-shell decals | Exact masked-parent route, B=cutout/A=AO, detail/subset/parallax stages. | Generic EnemyPreset classification can shadow the decal route; reachable old decal path uses different NAO roles. This is separate from working outfit/charm decals. |
| Eyelashes and gender normals | Authored lash controls and baked gender normal deltas. | Primary lash path remains simpler, and UEModel morph normal deltas are read but discarded. Visual impact and lash R-vs-A correctness still need testing. |
| Lacrimal roles | Dedicated material routing exists. | Primary role recognition has a coverage gap; do not confuse it with already implemented tear-line/occlusion profiles. |
| Native body/face/hair | Some body, face Fresnel and strand-direction stages remain simplified or omitted. | Preserve our native Rec709 body desaturation,2:1 micro-UV, directional specular, face Fresnel/ear controls and proved tangent/ColorMap stages. Both hair closures remain approximations. |
| Fur | Supplied classifier routes Shells/LOD masters to generic surface. | Preserve our exact Shells/LOD scalar/alpha handling and fitting. This does not certify native fur light transport. |
| Medium normals/cloth/glass/concrete | Medium normal and several concrete stages are explicitly omitted or generic. | Preserve our blurred-curvature medium normal, directional cloth specular, native opaque glass and dry concrete blend/paint/Fresnel stages. |
| Emission | Generic E is an intensity mask using resolved base-colour hue, with V1/V2 controls. | Generic vendored path can feed scalar E as grey colour; exact native XEM path separately implements authored colour/UseCR, histogram and pulse. Port per master, not globally. |
| Outfit decals | Now includes eight independent decal slots, numbers, layer masks and normal/surface overrides. | Our ArcDecalUv implementation remains independent and must be preserved pending direct matched tests. The earlier premise that upstream has no decals is outdated. |
| Mostly unchanged helpers | Trim core, four prop-trim helpers and30Graph methods match our vendored AST. | Many added fallback warnings/pink markers are diagnostics, not improved native shader equations. |

Hue variation has a concrete unresolved disagreement: upstream samples hue noise as Non-Color; our GT samples sRGB. The current2.2 HueNoise JSON omits an explicit SRGB field, and the new bundle does not supply that texture metadata. Do not choose a side from filename or omission alone.

Priorities: fix the proven shared-section assignment blocker; add coverage/static-layer handling with current shader verification; update the bridge's dependency keys, strict lookup and current metadata while preserving Pak wrapper parsing; then validate individual weapon/enemy/lash profiles. Retain the more complete native families and working decals. No global compositor adjustment can repair a wrong slot, channel or stale texture.

Detailed exact-line evidence, source hashes, AST inventory, current disabled-layer inventory and pure probes are retained in the designated scratch folder `new-revamped-comparison-20261026`, including aggregate-comparison.json and the character/effects/inheritance reports. Runtime addon code and installed version were unchanged during this comparison.


## Original graph backend: native verification boundary (2026-10-26)

Game evidence: `MI_Harvester_01_Weakpoints`, `MI_ChonkMortar_01_Decals_01` and `MI_Urn_Urn_Terracotta` compiled with actual PNGs in private Blender 5.1.2. The fresh backend uses a shared expression DAG and semantic `DR.*` stages with a private `DR.Material` graph. This is structural verification, not matched-reference visual parity. The installed addon remains 2.19.58.

Current native corrections supersede unverified comparison descriptions above: enemy-shell NAO A influences detail, rather than being a general AO multiplier; weapon detail `TA_Weapon_Detail_01` has three native slices and coordinate 3 clamps to slice 2; native UEModel v10 morph names use byte-length UTF8 without a trailing NUL. Actual Yeti LOD0 has 14,643 vertices and authored female/male position and normal deltas.

The real Blender run exposed UV vector-width lowering and Windows paths over 260 characters. Correct coordinate-aware promotion and explicit extended Windows paths fixed the three surface cases. A subsequent character/effects pass reached real morph normal storage but failed a 0.05-degree readback tolerance (0.3428 degrees observed); normal codec/control investigation remains open. This must not be reported as a full verification pass.

Primary outfit replacement remains unsafe: Goalie has five base material layers but eight independent garment decal slots, including nonzero normal/surface overrides in slots 6 and 7. Preserve current working garment/charm decals, medium-normal mip behavior and native cloth world-space inputs until replacements are verified. The user clarified that original node naming, structure and flow are the immediate requirement; changing graph presentation does not make retained source formally clean-room, and attribution stays accurate.


## Native runtime integration verification update (2026-10-26)

The earlier morph readback failure is resolved. Blender 5.1 legacy custom-normal setters quantize to SHORT2; direct CORNER FLOAT_VECTOR storage preserved the authored native normals. Seven canonical Yeti female/male/reset/save-reopen checks pass, with worst position difference 5.722e-8 metres and angular difference 2.832e-6 degrees. Source reads were blocked after reopen and persisted attributes remained sufficient. Character-fit synchronization explicitly updates active normal deltas; no idle watcher was introduced.

Three Recon visor variants, each imported twice, bind their shared Plastic MI to every exact intended section while preserving geometry, UVs, morphs and transforms. The shared material runtime passes58 source tests; native blend settings, explicit opaque alpha and tangent-view transport have separate actual Blender checks.

The compact native texture metadata exporter passed actual game extraction8996 (31 queued, required_failed0, no parse/decode errors). Seven native material cases and state/color callbacks pass in production-sidecar run025 without archive metadata or verification overlays. The exporter source and its verified self-contained repository publish are integrated. Native-family public caller integration and full fallback catalog remain pending. The original balanced selector matched24 isolated render pairs exactly, but full GT run026 failed a palette-name assertion under investigation; it is not counted as a full native-material pass. Installed addon remains2.19.58 and the current source still requires the actual packaged ZIP gate before distribution. No measured game-image99% claim.


## Public native routes and original operation layout (2026-10-26)

Seven native public importer routes and scan/eyelash callbacks passed actual Blender run031 using emitted production metadata and images, including complete active-resource signatures. Missing exact metadata is a diagnosed boundary; existing stronger profiles and explicit user overrides are preserved. Canonical cache run029 proves warm reuse checks actual sampled resource hashes: active image or parent changes rebuild, unused texture changes reuse. Native catalog activation requires a reviewed complete versioned manifest; the old bank is still retained until full generation is reviewed.

Primary stages run032 compared three real Recon variants with the actual baseline. All normalized executable graph, embedded controls, shared section, geometry and repeat-cache invariants passed. The original layout owns461operation-factory nodes and774DR nodes per example, using semantic stages and dependency depths, alongside the previously verified23balanced selections. Direct constructors and some retained composition remain pending; no whole-source clean-room claim. Node identity aliases preserve native semantic keys and compatibility.

Local source checkpoint9e604ecacbef422607c99d800a11130bb6c79862 is on codex/original-material-graph-backend without changing active PakDirect or its index. Later primary-stage source is verified separately. The current source ZIP passed real install, enable, disable/re-enable and overwrite-enabled checks in a private Blender profile. SHA25637aab847e40645a9a7c74eb66c7a6c92760083ef07be6fa1adebd3bf71f3ca52;29selected runtime/publish files match source bytes. It is still a source candidate at2.19.58, with no user profile sync or public push. Future source/version changes require a fresh release gate.


## Original construction and source boundaries: verification update (2026-10-26)

Game: current typed material inheritance now bypasses the old fallback bank when complete, and refuses partial chains rather than mixing legacy flattened values. Native texture metadata covers CurveLinearColorAtlas as concrete native class with proved Texture2D inheritance, preserving HDR bytes. Actual Soccer cold export queued24 with zero required failures; real v1-to-v2 warm metadata upgrade preserves three PNGs, one HDR and mesh bytes/timestamps and launches no helper on the next warm request.

ST: native package identity remains the lookup authority. Original DR node identities keep compatibility aliases for parameter callbacks; display labels do not select shader equations.

Human: canonical source checkpoint531185a8d7863134e531b81709dd8a7bce579760 records the original runtime integration without changing active PakDirect/index or publishing. Three native Recon variants pass direct-constructor run041, and broader Goalie/Cotton/Urn outer-graph matrix048 passes. Private body coverage042 and later surface-pass preservation040 pass. Loaded execution identity038 prevents stale modules receiving new native-cache claims. Current portable suite134 passes after native nonimage/deferred-binding integration.

The original packed-decal DAG passes all80 actual paired numeric/render cases051 with zero measured pairwise error. Native integration052 passes Goalie CyanWhite but fails the enabled-decoder-instance assertion for another fixture; it is NOT integrated or declared fully verified. Soccer charm053 independently preserves actual artwork/normal/opacity bindings and geometry through authored-MI slots. Native source-limit gate054 passes five canonical-only Blender checks: unaffected builds avoid legacy data, affected hierarchies refuse before mutation, and unknown errors reject.

The source-complete bank contains13343 material records and17706 resource facts, with11 explicitly deferred rendering limitations across six ambiguous binding sources. It remains unactivated pending exact review; source completeness does not establish rendering completeness. The retained character/eye/teeth library boundaries still require replacement or accurate attribution. The user requests original naming, structure and flow; cosmetic renaming alone is not evidence of independent construction. Installed addon remains2.19.58, no public push/profile sync, and current source needs a fresh actual ZIP install gate before release. No measured99% in-game image parity claim.


### Integrated original decoder and native face allocation

Run056 corrects the052 fixture assumption using exact authored packed-data slot sets: CyanWhite2/3/4/5, Cotton1/2, Urn none (both native DecalData references are null stubs). All three strict native cases pass; Urn requires complete executable graph equality with no decoder boundary exemption. The original decoder is integrated, and canonical-only058 rechecks all four integrated hashes, pinned pre-integration source, authored controls/images, garment callbacks, geometry and cache reuse. Full05180case paired output remains zero error. Native face allocation is integrated:055 proves fresh allocation/rollback;057 proves the public setup path resolves all real Face03 textures, imports no retained FaceShader template/library and preserves the complete working graph on injected failure. Portable native138, face9, warm-cache7 and GT4 tests pass. These prove selected contracts and routes, not matched game rendering or complete replacement of all retained recipes/fallbacks. Native Teeth construction remains staged. Full bank activation and current ZIP release gate remain pending; installed addon2.19.58 is unchanged.


### Original composition and complete character route checkpoint

Game: current Face03 real mesh/material and native Teeth source underpin character acceptance078. The original primary composer replaces palette fold, wear response, RG decoding/RNM, occlusion, surface and emission composition while preserving authored controls and working decals. Native GoalieCyanWhite/Cotton/Urn material closures under three sun directions match the pinned baseline exactly in076. Native signed pre-skin normal rendering067 has worst encoded error7.15e-8; actual Teeth RGB remains finite and unclipped under scale-appropriate light.

ST: package/native parameter identities remain unchanged; original DR generated names, groups and scheduling retain explicit compatibility aliases for controls.

Human: original character missing-source fallbacks now cover face, eyes, teeth, lashes and eye auxiliary surfaces without loading the retained template.078 proves native→fallback→native transitions, explicitSSS.373 preservation, failure retention, scoped hair-color callbacks, six fallback routes and unchanged real geometry. Missing-source graphs are explicitly approximations; they do not claim native renderer or temporal behavior. Native157, character34, warm7, GT4 portable checks pass. Shared-helper/legacy168-port compatibility and current-data activation remain pending; installed2.19.58 unchanged and no push. A new actual packaged ZIP gate is still required before2.20.0 release.


## 2.20.0 final composition and installed release verification

The remaining shared helper registry, general selector, original legacy layered compatibility composer, current native-data/FModel fallback, character fallback transitions and generic emission preview are integrated. Canonical088 compares three complete native materials under three light directions with zero measured pairwise closure error. Current catalog activation contains13,343 material and17,706 resource facts;11 unresolved native binding limitations refuse before graph mutation. Compatibility preview routes remain explicitly labelled.

267 Python cases passed, plus updater preservation. The exact 2.20.0 ZIP passed Blender5.1.2 install/enable/disable-re-enable/overwrite-enabled gates. Fresh installed run090 passed module/version identity,594 immutable file hashes, semantic sync marker validation, original helper construction with the old library unavailable, and lifecycle checks. User preferences were not saved by verification. ZIP SHA25648abf8a8435629e0f99a5bca016e8df54e98a53c57622c660bc228d99936b0d4. The user addon was updated after a runtime safety checkpoint on Z:. No public push or measured99% game-image claim.
