# UI feedback update

## Scope

The search lists show at most three results. Outfit browsing uses two columns. Import and Apply actions have separate containers and spacing. The Steam versus FModel source selector is visible without opening Advanced. The gun and ARC sections expose their existing controls without an Advanced wrapper. Mods and Skin retain their own collapsible sections.

The package includes the usmap and outfit reference CSV. The install check rejects a package if either resource is missing or resolves outside the installed addon. The Settings paths remain optional overrides.

## Previews

Gun previews use the generated quarter-resolution source images, selected by exact weapon, variant, tier and stencil identity. The producer checks source hashes and writes pixel-identical PNGs for Blender. Runtime lookup uses a compact packaged index and does not search the render source directory. Missing exact combinations are not replaced with another tier or stencil. The source set has no Ascended stencil images and only its maximum-wear Ascended tier.

ARC decoding and cropping are deferred to timer work. Its previous crop is unchanged. The first draw queues the selected portrait instead of processing its pixels.

Banshee now has a packaged portrait produced from its exact game metadata Image reference and the retained matching game PNG. Its crop is unchanged. The raw PNG's original extraction build was not recorded; its source and output hashes are retained. Missing portraits can recover selected-only retained metadata and artwork in background work. PAK extraction for missing artwork is allowed only after an explicit outfit import, not from sidebar drawing or a deferred browse callback.

## Hair colors

Normal hair and facial hair use separate scene properties and role-specific Update actions. Initial imports and updates isolate shared geometry and material datablocks before the first recolor. Facial-hair updates leave normal hair, eyelashes, faces and other avatars unchanged. Normal-hair updates keep the existing eyelash option and local face pairing.

## Lighting

Applying Sky & Sun activates the scene world and lights for Material Preview and Rendered views. Solid and Wireframe views switch to Material Preview. Only windows displaying the target scene are changed. Applying the sky makes the world background visible and disables transparent film. Removing the look restores its previous film setting.

Rotation and the game's Extra_Contrast, Overall Intensity and Saturation controls operate on connected inputs in the managed world. They appear after Apply creates that world. Rotation turns the world, not the separate sun lamp. Apply rebuilds the world controls from the selected scenario.

Applying the game grade selects full compositor precision. Removing it restores the prior precision. This setting affects the scene compositor, not only the LUT. The LUT producer, shaper, interpolation and color math are unchanged. A private Blender reduced-precision UV ramp reproduces discontinuities; the full-precision CPU and GPU ramp checks pass. The native viewport in the user's scene remains unverified, and full-precision viewport memory and timing were not measured.

## Verification state

The layout regressions, packaged browse catalogs, extraction-hit preservation, cached metadata preparation and native-catalog checks pass. The weapon worker verified every generated identity, source hash, decoded pixel and PNG load in a private Blender. The lighting worker verified the failing baseline and corrected sky activation, connected controls, precision restoration and retained shader output in a private Blender.

The actual ZIP passed fresh install, enable, disable/re-enable and enabled overwrite in a private Blender profile. Packaged sidebar, FX action-boundary and per-asset control-isolation checks passed. The verified package was synced to the user's addon folder. A fresh Blender process using the saved profile confirmed the installed module path, version, enabled state and bundled resources. No blend or preferences were saved.

Archive verification checks the current source and assets, allows the packager's entrypoint metadata rewrite, compares every shipped file to its installed copy, and preserves all previously packaged outfit portraits unchanged. The new deferred-preview regression fails before the action gate and passes afterward. Its browse callback cannot construct an extraction command; an explicit import can recover the missing portrait.

| Package evidence | Value |
| --- | --- |
| Version | 2.22.14 |
| Archive SHA-256 | 72b7c3676397c2ebcc19aae61716877f84da206e844d707a773c0a8f087b67e5 |
| Archive bytes | 134847536 |
| Previous archive bytes | 108955803 |
| Additional archive bytes | 25891733 |
| Source and asset files checked | 1112 |
| Installed files matched | 1352 |
| Previous outfit portraits unchanged | 77 |
| Generated quarter-resolution gun PNGs | 479 |

The additional archive payload is mainly the exact gun images requested for tier and stencil selection. The source AVIFs are converted to PNG to avoid requiring Blender AVIF support; their decoded pixels are unchanged.

## Limits

The private headless layout recorder detects no external discovery or PAK work before explicit actions, including when the installed Map Importer registers first. It does not measure native widget rendering. The reported native first-load stall is not reproduced by that recorder. No general import speedup or live-scene visual parity is claimed. Previously recorded unrelated portable native weapon and paint failures remain outside this task.

An adjacent outfit-blocker suite still fails its textual expectation for the old decal implementation. The affected material source is byte-identical to the previously installed package. This follow-up does not change it or claim a complete test-suite pass. A running Blender may retain the old module until a full restart; the installation receipt comes from a fresh process.

No push, public upload or publication is part of this update.
