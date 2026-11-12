# Focused runtime failure triage — benchmark 1.11 / 2026-10-28

Read-only analysis of report-A.json and report-B.json plus current source and freshly exported exact mesh/material JSONs under this task. A reports installed DataRaiders-Outfits 2.20.0, Blender 5.1.2, Steam build26159202, mapping ARC-Raiders-CL1430164.usmap. Source inspection uses <GitHubRoot>/DataRaiders-BlenderImporter, reported identical to installed runtime by the parent task. B uses new Revamped1.11. No Blender process, installation, code change, source copying or fix was performed here.

“Observed” below means recorded in the supplied benchmark JSON, not independently re-rendered. Code explanations are source-inferred unless stated otherwise. Source paths abbreviated A=our repo, B=<UserHome>/Downloads/BETA-ARC_Texturer_Revamped_1.11.0/arc_texturer_revamped.

## Main distinction: failure categories are different

Our arc_surface_report “rebuilt” count is a verification policy, not a raw count of connected shader outputs. A/revamped_bridge.py:175-225 verifies maps/preserved native profiles and explicitly rejects family=fallback at:217. Therefore a graph can build, connect to Surface and have an arc_build_key, yet count as failed. The Engine/Cables/Vending/Curtain failures chiefly demonstrate this policy plus genuinely unsupported masters. B's absence of an exception does not mean those masters received dedicated support.

The ambiguity failures on Catwalk/Glass are a separate binding-resolution problem. Painted wall is a separate eager UV1 graph-construction problem.

## Observed per-object results

| A object | Observed A status | Why it is not one generic issue |
|---|---|---|
| SM_ExtractionElevator_01_Engine_A | FAIL2/3; fallback,prop_trim | Cable slot0 is generic fallback with zero ready images; other two trim materials built. |
| SM_ExtractionElevator_01_A_Cables | FAIL0/1; fallback | MI_ExtractionElevator_Cables, unsupported M_SplineThicken. |
| SM_ExtractionElevator_01_Tower_A | FAIL3/5 | Slot2 Catwalk re-resolution ambiguity; slot4 cable fallback; native light and native packed prop are retained successes. |
| SM_VendingMachine_01_A | FAIL1/2 | Main native_packed_prop succeeds; cable slot1 fallback. |
| SM_Res_Curtain_01_A | FAIL0/1; fallback | Unknown M_FresnelCurtainTest receives generic graph, no ready images. |
| SM_WindowGlass_01_200cm_WindowSlit_A_Fbx | FAIL0/1; Equally close material identities | Existing glass graph retained; re-resolution using display name fails. |
| SM_TBG_WallConcretePainted_ServiceEntrance_A | FAIL0/1; Required authored UV channel unavailable:1 | Native material graph asks for unavailable second UV while building overlay expressions. |

A fallback cable and curtain slots are observed with surface_connected=true, node_count21 in the recursive audit, Base Color unlinked[.5,.5,.5,1], Roughness linked, Metallic0 and Alpha linked. Their arc_build_report contains fallback family and generic-profile warnings, not an error. arc_surface_report nodes=[0] counts ready images, not graph nodes. Do not report these as “no shader graph” or “import exception.”

A Catwalk and glass retain old graphs: Catwalk surface_connected=true and28nodes, linked Base Color, metallic.8, Alpha1; glass surface_connected=true and21nodes, Base Color[.75,.85,.90,1], roughness.05, linked Alpha. They lack new arc_package/arc_build_report but already have exact fresh arc_mi_path and arc_mi_stem. Their dr_native_route_diagnostic says master outside verified native family set; this is a dedicated-route limit, not proof the retained generic graph is absent.

## 1. Cables and curtain: unsupported masters plus strict verifier

A/surface_rebuild/records.py:314 onward classify has no dedicated M_SplineThicken or M_FresnelCurtainTest branch. Generic fallback builds through A/surface_rebuild/surfaces.py:93 onward, with “Generic packed-channel profile; this master has not received an individually reconstructed material profile” at:109 and “Unknown master material; generic fallback used” later in material build.

A/revamped_bridge.py:217 requires arc_build_key, a nonfallback family and at least one ready image unless a separately preserved native profile qualifies. These cable and curtain graphs fail that requirement. This explains Engine2/3, Cables0/1, Vending1/2 and one of Tower's two failed slots without inferring a thrown exception.

Observed B results:
- Elevator cable and vending cable master=M_SplineThicken, family=fallback, missing_textures=[], warnings unknown master, default_color=#FF00FFFF.
- Curtain master=M_FresnelCurtainTest, family=fallback, missing_textures=[], warning unknown master; generic graph has no dedicated Fresnel curtain profile.
- Therefore B has not demonstrated spline-thickening/cable master fidelity or curtain shader fidelity merely by building without errors. Pink is B's explicit default when an albedo/palette input is unresolved; A's grey is also a fallback.

For spline materials, geometry/world-position deformation and complete appearance require separate consideration. This source review does not establish whether either imported cable mesh already bakes all original deformation.

## 2. Catwalk and glass ambiguity: duplicate representations and lost exact identity

Observed exports contain exactly these logical target packages, each in BOTH ordinary JSON and native .arc-material.json form:
- /Game/Pioneer/Environment/Props/_Generic/Catwalk_01/MI_Catwalk01_SteelGrates_NoVTDirt
- /Game/Pioneer/MaterialLibrary/Material_Instances/Themes/Generic/MI_Glass_01_A

A/surface_rebuild/assets.py:95-115 indexes every JSON stem normally, then also aliases a .arc-material.json under its original native_stem. So the ordinary MI.json and its native sidecar both occur in self.names[(MI,'.json')].

A/surface_rebuild/assets.py:132-179 resolve first prefers an exact package. But the retexture call has a short name, not a package. It strips Blender .001 suffix, collects candidates and ranks nearest directory. At:174, multiple tied entries immediately raise “Equally close material identities require an explicit package” before collapsing equal logical identities or choosing preferred representations.

This is a concrete source-inferred explanation for observed ambiguity: ordinary/native JSONs in the same directory tie even though they describe one logical material package. It is not evidence that two different equally close game packages exist. Full index invocation was not rerun in Blender during this review, so label this duplicate-representation diagnosis source-inferred, supported by exact on-disk paired files and the observed error.

A/surface_rebuild/materials.py:143-148 uses arc_package if present; otherwise old arc_original_slot_name/old.name and lib.slot(name,mesh_source). A/surface_rebuild/records.py:227-257 accepts no slot_index and does not consult the mesh's StaticMaterials list. Existing exact arc_mi_path is not used by this branch to supply the full package. Thus already-bound native/legacy material identity can be lost during generic refresh.

On exceptions, A/surface_rebuild/materials.py:176-178 merely appends an error report. It does not replace that existing slot. This matches retained visible graphs and existing fresh exact arc_mi_path in the audit.

By contrast B/materials.py:177 calls lib.slot(name,effective_source,i); B/records.py:218-232 reads exact current mesh StaticMaterials/Materials by index through loadout_records.asset. It resolves MaterialInterface and loads the full package. B/loadout_records.py:20-29 uses local raw mesh JSON before bundled loadout records. The fresh exported raw meshes supply:
- Tower slot2, slotname Grates => /Game/Pioneer/Environment/Props/_Generic/Catwalk_01/MI_Catwalk01_SteelGrates_NoVTDirt.0.
- Window glass slot0, slotname Glass => /Game/Pioneer/MaterialLibrary/Material_Instances/Themes/Generic/MI_Glass_01_A.0.

These exact references are observed from the actual exported Exports/StaticMaterials payloads, not guessed by basename. B's successful binding is consistent with this improved index-aware route.

However B window glass still reports fallback, generic V1 translucent/refraction approximation, default magenta and height/displacement omission on NormalMap. Binding improvement does not imply good glass rendering.

One additional status nuance: A/revamped_bridge.texture_environment_object:57 appends report errors to detail text, but status at:59 depends solely on verification check['ok']. Observed elevator base A has “ok6/6 ... errors=Equally close ... MI_Beacon_01.001”; beacon postrepair can satisfy verification even when an earlier re-resolution error remains. An “ok” prefix therefore cannot be equated with zero earlier slot errors.

## 3. Painted wall: eager UV1 input construction, not demonstrated lost UV channel

Observed A object has one UV layer[UV0]. B has one UV layer[UVMap]. Elevator objects with two channels have A[UV0,UV1] vs B[UVMap,EXTRAUV0]; door pistons have one on both sides. Vending two on both, Curtain and Glass one on both. Thus the channel counts in this focused group agree. Different names alone do not demonstrate sampler mismatch because both graph wrappers select channel by layer index.

A/surface_rebuild/surfaces.py:246-290 native Concrete builder:
- UseOverlay OR Enable Blend Textures OR Use Paint activates overlay-coordinate construction at:279.
- It creates UV0 base, then at:283 executes g.mix(base,g.uv(1),s.get('Use UV1 as Overlay UVs',0)).
- Python eagerly evaluates g.uv(1) before g.mix can see whether the scalar is zero.
- A/material_runtime/interop.py:111-123 maps actual layer names by index and raises on missing nonzero channel at:119.

The material's raw .arc-material.json explicitly enables Use Paint and Enable Blend Textures; OverlayTiling=.5. Therefore the overlay block runs. Importantly, the exception text calls the UV channel authored-required, but the construction requests it even when the UV1 selector is zero or absent (default0). This review has not independently evaluated the complete merged selector value from cached expression defaults. The safe conclusion is that A's builder demands UV1 eagerly; the error alone does not prove the active game equation needs UV1. It must not automatically be blamed on UEModel dropping a UV channel.

B/graph.py:168-179 handles missing UV1 by substituting UV0 and adds a warning. B's actual wall report includes “Missing UV channel1; UV0 substituted,” “Concrete feature omitted in V1: Use Paint” and “Concrete feature omitted in V1: Enable Blend Textures.” It builds its simpler architecture graph but omits exactly two active effects needed for this painted-wall material. This is a useful fail-versus-approximate distinction: B's no-error outcome is not evidence of matching paint or blends.

A's failed refresh preserves its previously assigned packed-prop-v2/concrete graph, arc_env_world_paint=1, arc_paint_space=object, arc_env_overlay_enabled=True and arc_env_tint_enabled=True in audit properties. Those are observations of retained setup, not proof of equivalent current native dry output.

## 4. Goalie glass: B real fallback remains after standard arrays

Observed B01_outfit SK_Goalie_Visor.001, material MI_Goalie_Visor_Glass_FrontEnd.001:
- master /Game/Pioneer/MaterialLibrary/Character/CustomMaterials/M_Character_Glass_Transparent
- family=fallback
- missing_textures=[]
- warnings unknown master and generic V1 translucent/refraction approximation
- default_color #FF00FFFF
- sampled Roughness=T_Smudges_01_M and DirtNormal=T_Terracotta_01_N
- recursive unique graph nodes49.

B/records.classify lacks this exact glass family. It falls through generic surfaces, whose base-color discovery cannot infer a proper glass closure from those data textures. B/shader_inputs.default_color produces magenta when recognized base-color/palette parameters are absent. This is an observed unsupported material/fallback result, not an outstanding array-export failure. Earlier clothing-array setup problems should not be reused to explain this final visor result.

## 5. Old installed cache source recovery: concrete order of precedence

Parent observed that normal configuration could route an explicitly fresh source to the old installed ArcPak/cache; isolated benchmark avoided it by clearing configured pak and registering fresh roots. The code supports that diagnosis:

A/operators.py:302-325:
1. Registered utils._pak_cache_roots are reversed into roots.
2. If pak_extract.configured_dir(scene) exists, installed addon ArcPak/cache is inserted at index0.
3. _recover_saved_material_lookup always calls pak_extract.recover_saved_material_source, even when the supplied source is a valid fresh file.

A/pak_extract.py:695-756:
1. Extract exact package identity from source.
2. Search format-preserving candidates in roots in order.
3. Return the first root with a match, using newest mtime only within that root's aliases.
4. Only after exhausting explicit roots for same extension does it check the original supplied file.

Thus a valid fresh source is not prioritized ahead of an existing same-format installed-cache package. Identity is exact, not arbitrary basename borrowing, but package identity alone does not establish equal current contents. A stale cache can win through root order. The function can also restamp arc_source_mesh/arc_psk_path for same-format recovery, while retaining original source provenance (operators.py:337-356).

This is a source-order explanation supporting the parent's earlier runtime observation. The final isolated A audit's source_json/arc_mi_path values in the focused objects point into task-local exports, so these final failures should not be attributed to old-cache contamination. Configuration matters for reproducing the original issue.

## Assessment boundaries

- A failure counts mix deliberate fallback rejection, actual refresh exceptions, and retained working-or-approximate prior graphs.
- B success/no-error counts include explicit unsupported fallback and magenta defaults.
- New B index-aware mesh binding is a concrete advantage in these ambiguity cases.
- Current A duplicate-sidecar ambiguity and eager UV1 construction are actionable source-level suspects; no code fix was authorized or made.
- Matching UV layer counts reduce evidence for a mesh UV transport defect in the selected group.
- Neither shader graphs, loaded texture counts nor absent exceptions establish visual parity. The parent task's final screenshots/renders remain the visual evidence.

No recommendations here require copying B implementation. All code reasoning above can be assessed against our own records, exact exported game references and source contracts.

## 6. Isolated female body: residual alpha and albedo selection differences

Parent's final isolated screenshot observation: A has a transparent hip/upper-thigh gap and brown bra; B has continuous body coverage with black shorts and no visible bra. This visual observation is supplied by the parent task; this subagent did not independently open the PNG.

Observed audit rules out continuing outfit-mask contamination in this final A setup: arc_body_mask_sources=[], arc_body_hidden=False, every named RGB body mask[1,1,1]; dr_body_mask_response_mode=alpha. A uses female_white; B uses female gender morph/MI_Skin_Female.

Two independent source-visible differences explain why “same female body” is not currently an equal-shader-input comparison:

1. A's pre-existing luminance alpha cut remains after the new body response. A/materials/character.py:83-95 builds a CONSTANT Body Alpha Ramp: albedo RGB is linked to ramp Fac (:92), black@0 has alpha0 and white@.01 has alpha1. Ramp Alpha drives Principled Alpha (:94). This is not using the texture's alpha channel; it clips very dark RGB regions. build_body_response later updates color/normal/rough/specular but leaves that alpha path intact. A/body_mask.py:421-436 preserves any pre-existing alpha source and multiplies coverage into it. All-one outfit masks therefore cannot restore already transparent black/dark underwear pixels. This is a concrete source-inferred mechanism for the observed hip holes; per-pixel geometry/UV confirmation would require a further runtime or image test.

B/body_skin.py:62 and:19-38 derives alpha from UV1 body_coverage alone, hidden flag and region controls. It never multiplies albedo luminance into coverage. With default controls(1,1,1), each selected region covers its full0..1 span. This explains why equally dark shorts can remain opaque on B.

2. The actual body color textures differ. A audit loads T_Body_Female_C.png, B audit loads T_Body_Male_C.png; both2048×1024sRGB from fresh task exports. A/properties.py:65-72 explicitly maps female_white to female color/normal filenames, and materials/character.py:36-44 chooses those filenames. B uses material references: current exported MI_Skin_Female.json:9-12 inherits M_Skin; :40-54 overrides only BodyNormalMap to T_Body_Female_N. Its parent M_Skin's raw references/defaults include T_Body_Male_C (M_Skin.json:639-640 and:809-810); B's actual loaded image agrees with that inheritance. Thus B is not simply mis-morphing into male geometry; it uses a shared inherited color map with the female normal map.

The bra/shorts appearance difference can therefore involve distinct albedo artwork as well as the alpha ramp. Do not attribute all underwear differences to shading equations or declare one result game-correct from these two renders alone. Current raw MI parent data supports B's inherited color selection, while A exposes an explicit legacy female_white texture preset. The benchmark should record this mismatch rather than claiming identical inputs.

This body disparity remains real after isolation, with no evidence in the final audit that another outfit supplied hidden-region controls. No fix or material rewrite was made.

## 7. Urgent contract audit: raw export arrays do not automatically parse empty

The parent raised a possible harness confound after canonical MI.json changed from flattened ArcPak summaries to standard raw export arrays for B. Current A source does NOT support a blanket explanation that _parse_flat_mi_json treats raw lists as empty:

- A/materials/common.py:909-964 delegates to textures.load_mi_properties_local and _flat_named_mi_parameters.
- A/textures.py:1168-1203 _material_source iterates utils.ue_export_entries over raw material exports.
- A/utils.py:432-447 ue_export_entries supports bare lists, single exports and Exports wrappers.
- A/textures.py:1335-1403 _material_lineage loads current named/cached defaults and source-local parent inheritance.
- A/textures.py:1355-1368 explicitly prefers a matching adjacent .arc-material.json native sidecar, retaining canonical data only as fallback.
- A/materials/enemy_surface.py:52-66 also explicitly chooses an existing native sidecar before canonicalJSON; effective_enemy_record parses it through raw_record.
- A/materials/weapon_surface.py:100-105 tries native family then MaterialLibrary, whose raw_session/catalog routes also consume native metadata.

Read-only audit comparison of verification/initial-transport/report-A.json versus final report-A.json:
- Bettina first skeletal mesh keeps the same fourCR/NXM/ID/Wear images, native sidecar source, master and warnings.
- Bullcrab Body/Legs/Armor each keep the same fourCR/NOM/EXX/HDR images.
- Final enemy adds nine decal images and eight trim images where the earlier report had none.
- Barrel earlier has14image uses; final has15, with additional T_ConcreteBuildingWorld_01_GRM.

Thus any newly observed white/pink weapon/enemy/environment renders are not yet explained by a wholesale raw-array parser failure or loss of all map bindings. Image presence does not prove those maps reach the active closure or that current defaults/branches match earlier summaries. The canonical format change may still alter fallback observations, evaluated defaults, cache signatures or selected branches, so a separate original-flat/native-primary-root retest remains a valid controlled experiment. Preserve both results and record the source contracts; do not preclassify the later visual difference as a harness-only fault or a genuine shader defect until that experiment resolves it.

No runtime retest or code mutation was performed by this subagent. The existing failure triage above describes the supplied isolated A4 report and should not be silently relabeled as a native-primary-contract result.

