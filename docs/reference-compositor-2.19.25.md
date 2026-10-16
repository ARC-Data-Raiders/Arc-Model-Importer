# Reference compositor separation — 2.19.25

The live scene used viewport compositing ALWAYS with mesh reference planes. Its Cryptomatte holdout works in final rendering but the viewport compositor has no object Cryptomatte pass. Enabling viewport compositing therefore graded the reference artwork along with the models.

## Display paths

Reference mesh planes and their emission materials remain the final-render representation. Native image-empty overlays provide viewport artwork after compositing and outside the scene view transform. Front/back helpers keep artwork readable from both sides. Derived cropped/rotated images are packed into the blend; source PNG files are unchanged. Helper transforms follow the original cards; original geometry, UVs and placement remain intact. Helpers are viewport-only and cannot cast shadows or enter render light paths. Hiding their collection still hides the references. Move original cards through the Outliner; viewport helper transforms are locked.

Addon initialization is deferred until unrestricted Blender data is available. Load/disable/re-enable reconcile helpers and restore the user's original viewport visibility. Viewport compositing is enabled only if all reference cards can be protected. Reference images appear through viewport overlays, so Show Overlays must remain enabled to see them.

For F12, reference coverage restores the complete raw pixel wherever the Cryptomatte coverage is nonzero. The former second fractional blend graded translucent artwork again. This intentionally bypasses grading for any background contribution sharing a translucent reference pixel too; it is a conservative pixel holdout, not separate-layer reconstruction. Scene display transforms still apply to F12 output: Standard/None, exposure0, gamma1 preserves the current comparison workflow.

## Verification

An isolated Blender5.1 Cycles regression with saturation1.8 gives zero linear RGB change for opaque and 50%-transparent reference samples while an unprotected surface changes substantially. Repeated synchronization produces one coverage node. The actual Urn UI image has zero compositor delta on every visible PNG pixel versus its identically rendered ungraded baseline; only invisible RGB in106 alpha-zero pixels differs. Comparisons use the original UI PNG as the source, rather than another Blender card. Texture sampling/display encoding is distinct from compositor contamination; an image plane is not a byte-for-byte screenshot viewer.

Evidence and tests: Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/21925.

## Remaining appearance limits

The current scene uses Standard/None, saturation1.04, red1.06, yellow1.04, blue1.02, LUT strength0 and viewport studio-world lighting. These settings do not reconstruct the game's final pipeline. Native customization lighting uses nine finite spotlights with authored falloff, exposure and specular controls; the current scene's SUN groups are a different rig. The exact offline scene used to bake inventory icons remains unidentified. See shared sections/ui-preview-lighting.md. Existing gray-axis Kodak curves are not a full UE tonemapper/3D LUT. Fur scattering/PDO, cooked mip filtering, material-specific normals/roughness and lighting remain separate fidelity work. No arbitrary saturation increase or scene light replacement is made by this fix.

## Release checks and live update

21 isolated lifecycle/state checks pass, including independent hiding in two scenes sharing one card, save/reload without duplicate helpers, collection regrouping, transforms, source PNG bytes and disable cleanup. Actual GUI desaturation leaves16,077 opaque reference pixels unchanged while the test mesh changes by mean108.12RGB levels. Clean install, enabled overwrite, disable/re-enable and restart pass without registration errors.

Installed ZIP and source runtime files match. Live repair preserves all788 original objects and their geometry/UVs/shape-key values, original material assignments, nine lights/linking and user color settings;452 viewport-only helpers represent226 cards. Saved Z:/Datamining/Arc Raiders/Blends/AllAccessories-2.19.25.blend. The original AllAccessories-2.0.0.blend is untouched. ZIP: Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/21925/DataRaiders-Outfits-2.19.25.zip. Concurrent weapon/UI work is excluded from this release.

Display-space artwork over a transparent background still composites over the scene behind it; changing that background can change filtered edge pixels. Viewport GPU display/filtering precision is distinct from compositor exclusion. The native overlay is not a promise of bit-identical source-image display at arbitrary zoom or alpha coverage.

A fully opaque RGB control verifies AgX plus exposure2 leaves all232,806 interior RGBA pixels unchanged. Native quad-edge antialiasing still blends against the changed scene background. Packed cropped-image RGBA bytes match the original sampled PNG pixels exactly; no color-precision conversion patch is needed.

Final six-check color proof: native-overlay-final-color-proof.json. Generated cropped artwork versus a direct original-PNG native image empty at the same position matches all236,682 displayed quad RGBA pixels exactly. Small native screenshot-versus-file RGB offsets occur equally on the direct original image; the addon adds none.
