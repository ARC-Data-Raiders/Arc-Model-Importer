# ARC Texturer Revamped 1.11.0 source review — 2026-10-28

## Scope and evidence

Read-only source comparison, without launching Blender, installing addons, rendering, committing, syncing, or copying implementation. Findings marked "inferred" require runtime reproduction. No universal or percentage game-image parity is asserted.

New reference: <UserHome>/Downloads/BETA-ARC_Texturer_Revamped_1.11.0/arc_texturer_revamped. Its __init__.py:1-6 declares version 1.11.0, author Teralix42, minimum Blender 4.2.0. SHA256 of __init__.py: 1261DDF6F666BE1A1C9A0FD28400A617E232E19B635165B1664180D791B8E764.

The requested prior <UserHome>/Downloads/ARC_Importer/arc_texturer_revamped is absent. Located actual established reference: <GitHubRoot>/ARC_Texturer_Revamped_V1.7_1.7.0/arc_texturer_revamped. Its __init__.py:1-6 declares 1.7.5 and Blender 5.1.0 despite the directory label. This report compares that real prior reference explicitly; it does not silently equate it with a nonexistent path.

Our reference: <GitHubRoot>/DataRaiders-BlenderImporter, __init__.py:9 version 2.20.0.

Mandatory documentation read/search completed before code: sibling lookup.json, keywords.tsv, catalog.json, sections/blender-import-paths.md, sections/lighting-look-blender.md and repos/DataRaiders-BlenderImporter.md, plus repository AGENTS.md. Existing documentation records native 2.20.0 limitations and warns against promoting scoped numeric checks into general visual parity.

## What changed from 1.7.5

Top-level Python hash comparison: new modules include attachments.py, backpacks.py, character_decals.py, default_codec.py, diagnostics.py, enemy_effects.py, enemy_noise.py, jsonio.py, loadout_records.py, loadout_ui.py, previews.py, prop_surface.py, requirements.py, rigging.py, routing.py, shader_inputs.py, source_surfaces.py, weapon_paint.py, weapons.py. Unchanged top-level modules include batch.py, characters.py, deformation.py, masked.py, trim.py. These file additions are evidence of scope expansion, not proof of behavior.

The new release is more than a tint patch:
- Explicit prop tint region/swatches and prop coordinate rules.
- Fixed-master texture bindings and world projection.
- Facial hair/visual-part/scalp assembly.
- Authored weapon variants/skins/mods, backpack attachments and loadouts.
- Optional combined mesh/rig with bind validation.
- Better missing-data diagnostics and source provenance.
- Wider Blender version declaration.

Its README_RELEASE_V1.11.0.md describes verification in private Blender 4.2.23 and 5.2.2, Beam6, warehouse computer, map/wall repairs, Bettina Standard/Ascended, Ice Climber tool and a 12-part character. Those are upstream release claims, not reproduced by this review. It explicitly retains limits for Unreal instance seed, RVT terrain/dirt, hair wraps, physics, bone offsets and uncommon shader branches.

## Material loading, source identity and coverage

New records.py:114-127 loads shader_defaults.json.gz, character_defaults.json.gz and fixed_master_bindings.json.gz. Read-only GZip/JSON inspection confirms:
- shader_defaults: 8,780 material entries and 14,591 texture entries, source_version 2.2.0.
- fixed_master_bindings: 639 material entries, source_version 2.2.0.

The fixed binding bundle is compact metadata, not a complete new material archive. Material count is not shader fidelity or tested coverage.

MaterialLibrary.load (records.py:140-218) gives local JSON precedence, merges inherited records, detects inheritance cycles, flags absent/empty data, records bundled fallbacks and rejects stale cached parents when an explicit current parent differs. It can infer omitted parent links from bundled metadata. Missing parents can be tolerated with a warning when instance texture/color information exists (records.py:198-205). Therefore even a successful material can be reconstructed from partial/bundled data; inspect report provenance.

assets.py:196-209 resolve: full package paths do not fall back to unrelated same-basename files. Bare names can use nearest-package resolution, with ambiguity notes in _choose (assets.py:175-194). assets.py:211 onward supports local PNG/HDR, local array slices, character templates and bundled image fallback. _build accepts JSON, PNG, PSK/PSKX and HDR only; EXR and UEModel are not indexed here. By contrast, our import architecture includes direct PAK extraction and native UEModel import. A common exported PNG/PSK dataset is needed for a fair comparison of the material pipeline.

## Actual material dispatch and graph changes

materials.py:95-101 defines explicit builders for character, generic surface, architecture, environment layers, weapon, trim, enemy tile, vehicle, decal, scan display, enemy decal, prop trim, architecture trim, hair, body skin and head subfamilies. records.classify (records.py:292-336) uses master identity/inputs. All other families fall through generic build_surface with an unknown-master warning (materials.py:134-135).

Our 2.20.0 is not simply the old Revamped build:
- materials/dispatch.py:243-260 routes exact native profiles first; unresolved exact native families are failure paths.
- material_runtime/native_families.py:13-21 includes native weapon, display, eyelashes, teeth, enemy shell, prop and blockout profiles, with permutation guards in prepare.
- surface_rebuild/native_catalog_bridge.py:26-35 requires reviewed source-complete activation and catalog SHA agreement; :102-105 and :129-150 track native provenance/content signatures and sampled resources.
- revamped_bridge.py:78-147 preserves our existing native profiles and explicit user overrides; :102-119 checks native signatures/cache staleness.

A valid comparison must record the actual selected master/family/profile in each result. Identically named visible materials do not establish identical equations, source versions or ownership.

### Prop blue tint, saturation and coordinate behavior

Prior 1.7.5 surfaces.py:85-106 used generic texture gathering and an optional entire-color Tint ×2 multiplication. New 1.11.0 surfaces.build_surface:132-158 detects m_proppreset_* masters, uses prop_trim.trim_uv and calls prop_surface.tint before packed response handling. It explicitly applies Metalness Value and Roughness Power. This is substantive new prop support.

prop_surface.py:45-79:
- Use Tinting=false returns the gathered base unchanged.
- Packed tint mask uses Mask; nonpacked uses Packed Mask.
- Packed R regions are reconstructed around .33/.66/1, using Tint (0.33), Tint(0.66), Tint(1) and optional override/swatches.
- Nonpacked tint lerps base against 2×base×chosenColor through mask R or gathered metal.
- Missing mask returns base plus warning, rather than pretending that tint is complete.
- Missing/ambiguous atlas warns; selected atlas failure can yield magenta.

atlas_colour (prop_surface.py:10-42) selects the CA_Prop_Colors identity, reads PNG dimensions, selects Swatch Curve row and supports per-instance random. Blender ObjectInfo Random supplies a clearly reported fallback when the Unreal seed is not exported. Thus two renders can differ legitimately through seed even with otherwise matching material parameters.

Unlike our documented item tint equation saturate(lerp(CR,2*CR*Tint,Normal.B)), the generic nonprop path surfaces.py:159-163 is still an unmasked Tint ×2 multiplication and warns about its neutral-grey assumption. Do not conclude that all blue/saturated tint errors are fixed just because prop presets improved. ItemPreset packed-normal-B tint and external-R tint must be benchmarked separately.

New source_surfaces.py:23-67 has fixed profiles for M_MasterLight_Emissive_01, M_BootcampTunnel_Concrete_01, M_FrontendMap and M_DebugPreset_Grid_1m. It embeds recovered shader identifiers in reports. projection (:10-15) converts Blender world positions to Unreal cm and chooses dominant axes. bootcamp (:70-110) implements independent base/breakup/overlay tiling, normal-driven UV noise, height/mask range, tint/contrast/desaturation and roughness overlay, while reporting missing RVT scene blending. These profiles are specific evidence-backed branches; they are not a universal master interpreter.

### Missing-texture handling

shader_inputs.py:5-30 explicitly uses magenta for unassigned color rather than a generic grey base.

materials.debug_material:23-40 creates a visible magenta/black checker with an export-fix report. build_material:43-60 rejects incomplete clothing palettes and absent essential BaseColor/OCMID/ColorMask/Normal inputs; :128-133 also converts character graphs with later missing images into the checker.

Noncharacter paths differ: Graph.image (graph.py:275 onward) reports missing references and returns None, and most builders continue with family defaults. Generic fallback can therefore be a partly reconstructed plausible material. Character decal missing artwork explicitly skips the decal while preserving clothing (character_decals.py:58-64). Keep those separate in benchmark scoring.

texture_objects (materials.py:156-220) creates OBJECT material-slot links to preserve other users of shared mesh data. On exceptions it replaces that slot with a checker and retains the original slot identifier for retry. Retexturing is not a preservation mechanism for hand-edited graphs; upstream release notes tell users to preserve edits first.

### Characters and hair

assembly.py:10 adds facialhairs lookup; appearance_selector :142-159 combines visual-part slots/scalp templates. __init__.py:405-450 adds facial-hair selection; assemble_character :455 onward includes facial_part.

hair.build_hair (hair.py:6-38) reconstructs attribute-A palette/roughness, attribute-B root falloff, secondary palette via vertex green, coverage R with authored opacity power/density, optional tangent texture and Blender anisotropy. It still explicitly omits global color-map strand-offset, Unreal scattering/temporal dither/pixel-depth offset, scraggle and weather. Our current FrenchBraids documentation/source includes scoped native tangent proof and current parent handling, so appearance alone cannot tell which tangent branch is right.

materials.py:106-113 deliberately makes head_occlusion transparent and records a user override. This is a systematic visual difference from any comparator with active eye-shell occlusion. It must be disclosed rather than scored as accidental image mismatch.

character.py:23 onward retains explicit layered equations; array sampling uses UV1 (:83-91), and reports omitted cloth directional specular/runtime weather (:69) and blurred-curvature normal stage (:140). New character_decals.py provides garment-specific slot/mask compositing, separate from the generic decal shader. Preserve our known-working decal paths during any later independent reconstruction; this review authorizes no integration.

### Mesh and rig assembly

rigging.combine_character :21-216 is new. It rejects animated or posed source rigs, active nonarmature modifiers and absolute shape keys (:35-44). It preserves object-linked materials, UVs, color attributes, corner normals, weights and relative shape keys; matching bind bones merge and incompatible bones receive distinct names (:46-63). It stages a result and checks evaluated bind geometry against inputs (:194-201), with a 0.0002m rejection threshold. Cleanup on failure precedes input removal (:207-216). Bind equality does not prove animation compatibility: axes change and original bind matrices are recorded for retargeting.

weapons.py:23-65 resolves Standard/Ascended variants and deduplicates visual definitions. attachments.py:36-57 interprets NAME_None as root attachment and rejects unresolved sockets rather than inventing origin placement. These are assembly/selection improvements, not material equivalence evidence.

## Concrete risks inferred from source; not reproduced in Blender

1. Recorded per-instance seed missing from material cache key. prop_surface.py:33 reads g.obj['arc_instance_random'] and uses the scalar directly to bake atlas-coordinate math. materials.py:61-85 cache keys include material records, UV/color attribute names, imported scale, root and texture file stamps, but omit arc_instance_random. Two otherwise identical objects with different recorded seeds can reuse the first material at :84-88. Blender ObjectInfo Random itself remains evaluated per shading object, so this concern specifically targets the explicitly recorded scalar branch.

2. sampler_hints omitted from material cache input. materials.py:61 includes texture_uvs/parameter_uvs/static_textures/curve_atlases, but not sampler_hints. source_surfaces.py:43-47 consumes sampler_hints for fixed detail-normal sampling_scale/channel and excludes hinted normals from base selection at :41. Altering that metadata without any other hashed change can retain a stale graph. Texture JSON colorspace/address/filter metadata is likewise not directly hashed by materials.py; the cache dependencies there stamp image files, not texture metadata JSON. Treat these as invalidation cases to test.

3. Inactive Tint can still become a fallback solid base. surfaces.gather:59-60 ranks Tint before Base Color/BaseColor/Color/Albedo/Diffuse, without consulting Use Tinting. If the CR color image is unavailable, the initial Tint survives; prop_surface.tint:46 then returns it unchanged when tinting is off. Thus the new inactive-blue-tint fix does not necessarily prevent an incomplete prop from becoming a solid inherited blue. The missing image is reported, so this is a visual-fallback risk rather than silent missing-data detection failure.

4. Generic ItemPreset tint remains less specific than our known packed-B/external-R equations. surfaces.py:159-163 does not use either tint mask and does not saturate the result. This is a source-visible branch difference, not a proven mismatch for every item.

5. First matching texture role is a heuristic in gather (:64-99), ordered by parameter name length and PM_/T_ prefix. Unknown masters with multiple competing CR-like maps can choose a structurally plausible but incorrect base. Family profiles bypass some of this risk; score exact and generic families separately.

6. Cached reports can retain first-object details. materials.py:84-88 reuses a serialized existing report; texture_objects later overwrites object/slot (:201), but object-specific secondary report fields may still originate from the original graph. Do not assume every report field describes the current object merely because object name was updated.

## Recommended targeted benchmark matrix

Use matching exact asset references, same extracted files, geometry/UVs/tangents, same Blender version and renderer, fixed camera, neutral color management and identical lighting. Separate equation/albedo outputs from beauty render comparisons. Report missing inputs and substitutions per candidate.

1. Solid saturated tint: synthetic white/grey CR with Tint red/blue >0.5, Use Tinting off/on, mask 0/.5/1, texture present/absent. Include no actual color input, only inherited blue Tint; expect explicit diagnostic classification, and measure whether it becomes plausible solid blue.
2. Actual Beam6 and warehouse computer: inactive inherited tint/swatches, UV0/object scale, base color and roughness. These are upstream release regression cases, not locally verified assets.
3. Packed prop .33/.66/1 boundaries: mask samples 0/.32/.33/.34/.65/.66/.67/1, tint colors, swatches and override flags independently. Capture unclamped linear color; determine saturation/clamp differences.
4. Swatch identity and seed: CA_Prop_Colors present/missing/ambiguous, rows and fixed indices; two identical objects with distinct arc_instance_random, in both creation orders and after retexture. Compare cache material identity and resulting pixels.
5. Item tint: documented MI_RelaxedAustronautTool_01_A packed Normal.B branch; Fuel/Gas external-R mask; inactive Blanket/Console. These discriminate generic tint from our scoped equations.
6. M_FrontendMap: literal fixed texture bindings, base normal UV0 and hinted detail UV0×20; change only sampler metadata, retexture, verify cache invalidation.
7. Bootcamp painted concrete: 1m/2m geometry and translated/rotated/scaled objects, breakup and overlay tilings. Check cm world-projection phase/scale. Record RVT omitted in both source and visual comparison.
8. Missing clothing OCMID/ColorMask/Normal/base and missing one array slice; verify checker, missing list and retry recovery. Missing decal artwork should preserve cloth and report a skipped overlay separately.
9. FrenchBraids and facial hair: exact current parent, card-vs-opaque tie slots, vertex color channels, tangent map, scalp overrides and hair/headgear visibility. Multiple suns can expose anisotropy transport differences.
10. Head/eyes: iris controls and explicit eye-occlusion transparency override; report systematic overrides before scoring.
11. Reused mesh with OBJECT material links, two body masks/outfits and repeated material rebuild. Verify materials remain independent where their inputs differ.
12. Combined rig: mismatched accessory bind, shape keys, custom normals and hidden alternatives; measure bind geometry, corner data and retained keys. Animation requires separate retargeting validation and the repository PSA height mandate if later authorized.
13. Fallback unknown master with multiple color maps, missing current parent, changed parent vs bundled snapshot, short-name ambiguity and exact wrong-root texture. Score diagnostic truthfulness as well as visual output.

## Assessment

1.11.0 materially expands Revamped beyond the established 1.7.5 reference. The most relevant material changes are actual prop branch separation, exact fixed bindings/coordinate metadata, explicit incomplete-clothing diagnostics and expanded head/loadout handling. It also openly reports significant rendering approximations.

Our 2.20.0 already has its own native dispatch, reviewed current-source catalog, provenance/cache signatures and dedicated known shader fixes. No source review here establishes that either addon universally wins, nor that every new Revamped module is absent from our product capability. The best next step is a scoped, reproducible common-asset benchmark using the cases above, with separate missing-data and engine-feature limitations. No integration or implementation copying was performed.

