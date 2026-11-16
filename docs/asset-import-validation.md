# Asset import validation

## Result

The background Blender matrix passes every valid case listed here. The checks found missing backpack catalog paths and unused character images. Both fixes pass their regression checks and real import paths. The maintainer remains Silarious.

| Check | Result |
| --- | ---: |
| Valid independent import cases | 32 passed |
| Moved-outfit additions | 8 passed |
| PSA presets and A-pose restoration | 4 presets passed |
| Focused regression tests | 32 passed |
| Blender version | 5.1.2 |
| Release | 2.3.32 |

These checks use real extracted game assets in private background Blender profiles. They do not modify user scenes. No render or visible browser check was performed.

## Import coverage

| Import path | Verified assets |
| --- | --- |
| Single source mesh | Lizzie charm, Antenna attachment, 10 Mil Tool, body, Face 01 Pio head, Afro Fade hair, Beard Full facial hair, Apollo jacket, Boom, Railgun |
| Backpack assembly | Salvager/ArcPack, Scout, Treasure Chest |
| Backpack attachment | Antenna, Bird House |
| Charm assembly | Lizzie, Anchor |
| Raider tool | 10 Mil Tool, Arc Tool |
| NPC assembly | Apollo, Celeste, Juanito, Lance, Shani |
| Outfit assembly | Abyss, Sandveil/Dashwood, Wasp Hunter/ArcCore |
| ARC enemy | Boom, Wasp |
| Weapon assembly | Jupiter/Railgun, Anvil, Arpeggio |

The matrix verifies finite nonempty geometry, transforms, evaluated dimensions, material assignments, active Surface links, resolved node groups, loaded image files and the absence of newly created zero-user images. Outfit and NPC checks require the queued authored source set. Identical duplicate source geometry is rejected. Bone custom shapes and retained gear-cache templates are recorded separately from scene meshes.

A backpack legitimately contains a container, frame and straps. A charm can include its authored Karabiner connector. Paired gloves can split into separate objects. These are not duplicate imports.

| Complete assembly | Scene meshes |
| --- | ---: |
| Apollo | 4 |
| Celeste | 5 |
| Juanito | 7 |
| Lance | 1 |
| Shani | 11 |
| Abyss | 10 |
| Sandveil | 13 |
| Wasp Hunter | 12 |
| Jupiter | 5 |
| Anvil | 11 |
| Arpeggio | 3 |

## Backpack availability fix

The matching game index contains no cooked ClosedAlpha package or authored assembly. It is no longer offered as importable. Its historical catalog row remains. No substitute mesh is invented. Scout and Treasure Chest have authored mesh paths and now appear as available. Both complete assemblies imported successfully.

The enum builder filters missing meshes after assigning their historical numeric positions. Existing saved identifiers do not shift. The browse manifest now records the updated CSV digest. A guarded producer proves that only the two backpack mesh fields and the manifest provenance digest changed. Preview mappings remain identical.

## Character image fix

The moved-outfit check reproduced unused hair images. The hair builder loaded MI textures that no connected shader input consumed. New Non-Color images also left discarded sRGB copies. Replacing default scalp and beard inputs then orphaned the old image buffers.

The builder now loads only its consumed hair roles. Newly loaded images do not need a discarded color-space copy. Replaced buffers are removed only when they are addon-owned, have no consumers and are neither protected by a fake user nor dirty nor packed. Existing user images and images with other consumers remain.

The final moved-outfit run has no newly created zero-user images at any addition checkpoint. Fresh Shani, hair and facial-hair imports also pass after the final ownership change. Earlier independent receipts predate that last helper change and are retained as coverage of their recorded code state, not as separate final-byte reruns.

## Moved outfit and poses

The integration imports Apollo, then moves, rotates and nonuniformly scales the rig. It obtains the actual Add-to-outfit destination plan and imports body, head, hair, facial hair, backpack, attachment, charm and raider tool. Every addition follows a subsequent master translation.

| Measurement | Result |
| --- | ---: |
| Largest attachment following error | 0.00000243 m |
| Rest mesh height | 1.889699 m |
| Idle mesh height | 1.882109 m |
| Raider-tool mesh height | 1.882231 m |
| One-handed gun mesh height | 1.882097 m |
| Two-handed gun mesh height | 1.884900 m |
| A-pose restoration maximum vertex error | 0 m |

The PSA checks reject nonfinite geometry and unexplained scale collapse. Pose height and exact rest restoration pass. This does not prove pixel-perfect visual parity with the game.

## Checker controls

The checker deliberately rejects collapsed geometry, missing required textures, unused newly imported images, active missing pattern samplers and active missing color-array samplers. It accepts disabled placeholders only when the authored controls prove them inactive. Unknown missing samplers remain failures.

Two invalid harness identities are excluded from the coverage count. ArcTool was corrected to ArcTool_01. ID_PLAYERSKIN_ARCCORE had fallen back to Abyss and was replaced with the real ID_PLAYERSKIN_WASPHUNTER identity. The harness now rejects unknown identities and checks the returned outfit label. Tian Wen remains unavailable without a cooked mesh and is not claimed as verified.

The earlier broad native suite has documented failures. This report claims only the focused suite and matrix results above. No Niagara-system import was tested.

## Evidence and reproduction

The scripts, per-case JSON receipts, red and green checks and release receipts are retained under Q:/Agents/DataRaiders-BlenderImporter/outfit-speed/evidence/asset-matrix. Logs record single-sample elapsed times, not benchmark distributions. The original matrix ran with the staged 2.3.31 version label before the release bump.

Use temp/run_asset_matrix.ps1 with temp/asset_import_matrix.py and explicit case names to repeat an independent import. Use temp/asset_interaction_matrix.py with interaction-moved-outfit for additions and poses. Use temp/run_asset_validation_regressions.py for the focused suite. These harnesses remain local evidence rather than shipped addon commands. They admit Blender by measured CPU and available RAM headroom, without process-count or fixed worker-thread caps.

The release receipt records the actual packaged-ZIP installation gate and guarded source promotion. The installed runtime must match every immutable ZIP member. Restart Blender completely after installation because an open process can retain an older module.

