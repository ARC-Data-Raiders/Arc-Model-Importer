# Revamped1.11.0 / DataRaiders2.20.0 benchmark — 2026-10-28

Artifact directory: `Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/benchmark-111-20261028/`. Binary game assets and renders remain there and are not committed.

Open `ARC_AB_Benchmark_2.20.0_vs_1.11.0.blend`. A is left, B right. Use the Scene selector for the26category views; `00_BENCHMARK_OVERVIEW` shows the gallery. Text blocks contain full import reports and metrics. All810bound images passed packed-file reopen verification. The blend is on Z:, and the user’s original blends and addon install were not changed.

## Evidence and limits

- Steam installed build26159202; depot4749862387538527905. Supplied mapping `ARC-Raiders-CL1430164.usmap`, SHA256 `CA12D5239EB91C21983A0105BD6CF7052D0CCA83032B9EC3E1C903965A3299EC`.
- A uses installed2.20.0 native UEModel import; B uses supplied1.11.0 bundled PSK importer. Both derive from the same fresh highest-quality CUE4Parse package selections and image resources. ActorX helper changed the mesh format only; task raw-JSON export/projection supplies current typed properties, not copied shader graphs.
- Every case imports in an isolated scene. Initial shared-scene body masking was a benchmark-context error and was superseded. Original logs remain under verification/initial-transport.
- An initial A attempt recovered older installed-cache paths; that attempt was discarded. Final passes use only the fresh export root for material lookup. Source format normalization and the vendor records reader’s lack of Exports-wrapper handling are documented, including failed preliminary attempts.
- Native Steam extraction succeeded with required_failed0. ActorX geometry also reported required_failed0, but some skeleton parsing failed; its native-only slot-metadata postcheck returns1 because it expects UEModel. Raw JSON extraction had1guessed absent item path and a partially parsed weapon skeleton. All54selected mesh sources were available; these warnings are not hidden as clean extraction.
- Final geometry-import errors: A0/B0. Material exceptions B0, but B has8generic/default-color slots. A has7surface-rebuild FAIL objects; some are verifier rejection of unsupported fallback rather than thrown construction errors. Missing already-bound image files: A0/B0. A successful import is not a material-fidelity pass.
- Final blend:138model mesh instances, including both assembled elevators. Reopen:810bound images,810packed,0missing files,0invalid named-UV bindings.29primary/detail renders passed; an additional original-coordinate weapon control passed rendering and stayed white.
- Neutral identical directional lighting, AgX,32Cycles samples, RTX5080; CPU capped4threads. No compositor/grading. Shared display scale per pair preserves relative import-scale differences, but translation/scale can change world-projected sampling phase. No matched in-game camera/lighting reference, exhaustive all-material coverage, or percentage accuracy claim.
- No production shader code, version or user-profile installation was changed. The earlier referenced1.10.1Downloads folder is absent; source delta comparisons distinguish the actual older1.7.5 folder from the new1.11.0 package.

## Visible results

| Case | Observed result |
|---|---|
| Full outfit - Goalie | Broad clothing appearance is close in this view. B visor glass is magenta: unknown transparent-glass master takes its explicit pink generic fallback. |
| Body - shared SK_Body | A clips the hips/underwear through its RGB-based alpha ramp. B keeps the hips. Color inputs also differ: A female_white selects Female_C; authored B female MI inherits Male_C and overrides Female normal. This is a product-default comparison, not identical-albedo shader isolation. |
| Face - Face03 with eye, teeth and lashes | Face geometry agrees; scalp/hairline coverage and skin/eye response differ. A paints a hairline; B default is bald. Matched eye/skin closeups provided; no matched in-game eye-size accuracy claim. |
| Hair - French Braids | Both import textured French Braids. UEModel A has50 additional triangles (50,128 vs50,078); source/ActorX degeneration or transport needs checking before blaming LOD. No orders-of-magnitude scale difference observed. |
| Facial hair - Full Beard | Both produce textured facial hair; isolated-card appearance remains coarse. Inspect full-size screenshot for edge/coverage differences. |
| Backpack - Urn | Urn terracotta palette appears broadly comparable; neither displays the earlier uniform blue tint in this chosen default. |
| Backpack frame | Default frame is metallic grey on both sides. No obvious whole-frame saturated blue seen for this selected default; color variants not exhaustively tested. |
| Backpack straps | Both straps textured; modest dark/roughness response differences. Generic raw component display does not test final character attachment placement. |
| Charm decal - Soccer Finals Ticket | A retains ticket artwork; B front is blank metal. Default gallery presentation turns both models180degrees to expose the artwork side; original opposite capture retained. |
| Charm fur - Rabbit Foot | Both Rabbit Foot results look like harsh/coarse strips rather than convincing fur. Similarity between plugins is not evidence of in-game correctness. |
| Charm connector | Both connector meshes render metallic; distinct roughness/brightness visible at full size. |
| Backpack attachment - Briefcase | Briefcase leather appearance broadly similar; inspect roughness and tint at full size. |
| Raider tool - Ice Climber | Both Ice Climber tool imports textured. No animation applied. |
| Weapon - Bettina | A Bettina renders near-solid white despite4bound texture roles and no missing files. B is textured. A source-origin/scale control also renders white, ruling out the gallery transform as the sole explanation. Root shader cause remains unresolved. |
| Gadget - Tesla Coil | Tesla Coil is textured on both sides; emission/static response can be inspected. No scene bloom or compositor was added. |
| Quickuse - Bandage | Bandage textured on both; small tone/roughness differences. |
| Throwable - HE Grenade | HE Grenade textured on both; small surface response differences. |
| Salvage - ARC Powercell | ARC Powercell textured on both; similar presentation under neutral light. |
| Enemy - Leaper Bullcrab | A Leaper/Bullcrab renders white; B is textured. A source maps remain bound; this is an end-result failure, not a missing-PNG diagnosis. |
| Extraction elevator | All13source meshes yield28visible BP/socket instances per side with0unresolved attachments and preserved unit bases. A is largely white and has3partial/rejected surface rebuild objects. B is visibly textured but includes generic fallback slots. Common authored placement is benchmark presentation, not evidence B has an elevator-assembly feature. |
| Environment - warehouse style Computer | Both computer imports textured. Static screen state is sampled, not animated. |
| Environment - Vending Machine | Vending surface tone/detail differs; A flags1partial rebuild. Both have bound textures. This flag is not equivalent to a missing file. |
| Environment cloth - Curtain | A curtain is flat white; B retains cloth color/striping. Both routes include unsupported/fallback treatment; B success alone does not prove the native cloth shader. |
| Environment glass - Window | A is effectively invisible in this view; B becomes magenta/textured fallback. A resolver reports an ambiguous material identity. Neither is established as accurate game glass. |
| Environment - Painted Concrete Wall | Both views lack proof of the authored paint/blend behavior. A aborts an eager UV1 request even though both meshes have only1UV channel and the switch may be inactive. B substitutes UV0/omits some controls; successful construction is not full parity. |
| Environment - metal barrel | A barrel is strongly magenta; B is rusty grey. This is an obvious whole-mesh color failure for A. No missing image or invalid named-UV binding was found; tint/recipe cause remains open. |

## Priority follow-up

1. Debug A’s solid-white weapon/enemy/elevator and magenta barrel using these saved bindings and source-origin control. Bound images and valid UV names do not establish correct sampling/composition.
2. Correct exact logical-package resolution when ordinary and native sidecar JSON are representations of the same material; preserve explicit StaticMaterials references. Confirm ties before changing a resolver.
3. Avoid constructing inactive UV branches; implement the actual painted-wall controls rather than substituting UV0 or ignoring paint.
4. Revisit RGB-luminance body alpha and explicit gender/albedo selection; use controlled identical texture inputs before ranking shader math.
5. Preserve our working ticket decals. Implement native glass/fur behavior and treat fallback pink/grey as a diagnostic, not success.

## Files

- `benchmark-contact-sheet.jpg`: all26pairs.
- `renders/`: full-resolution category views, ticket front/opposite, eye/skin closeups and source-origin weapon control.
- `benchmark-cases.csv` and `benchmark-audit.json`: per-case metrics and slot-level fallback details.
- `analysis-revamped111.md`: detailed source comparison.
- `analysis-runtime-failures.md`: source triage with exact code references and inference boundaries.
- `verification/final-blend-reopen.json`: packed-resource and UV checks.
- `benchmark-manifest.json` and `verification/`: package selection, mapping/build identities, original data hashes, logs and reproducible scripts.
