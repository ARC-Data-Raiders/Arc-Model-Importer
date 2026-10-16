# Native fur materials — 2.19.23

## Game evidence
Rabbit’s Foot uses M_Hair_Metahuman_ColorMap, a masked hair-card shader. Current/cached mesh VERTICES, INDICES, TEXCOORDS, NORMALS and MATERIALS are byte-identical, and the cached coverage atlas matches a fresh export. This asset stores one UV channel. The native vertex factory clamps requested TexCoord1 to min(1, NumTexCoords-1); it does not supply an empty UV1.

ColorMap SM5 resource7213E480EAE6EC0BAF0DEC10B1998F2856AF6B34, pixel1953 and vertex0236 establish:
- root = guarded_pow(Attribute.B, Root position)
- color UV = native UV1*ColorMap_Tiling + (root+Attribute.R-.5)*ColorMapStrandUVOffset
- color multiplier = saturate(texture RGB + 1 - ColorMapBlend)
- multiply by the existing authored root/palette color.
Blender V must be converted before/after native UV transforms. A PM_Diffuse alias must never replace an explicit Coverage atlas because of JSON key order.

M_FurShells and M_FurLOD are different native families. Bells has eight coincident shell copies, GWRanger six; shell index is encoded in integer tiles of original UV0.V, and COL0.G is the length mask. Native shell offset is sourceNormal*floor(originalV)*COL0.G*Length (Unreal cm). LOD uses sourceNormal*[1+(COL0.G*ShellCount*Length-1)*MaskLength]. The shell material samples fractional V, authored FurMask/Color/Flowmap/WindMask, per-shell alpha cutoff, root/tip gradient, sheen and roughness. ShellCount is not an instruction to generate additional geometry.

## Importer changes
Shared fur-card color-map branch, explicit coverage precedence and last-UV clamping. Dedicated native shell/LOD material profiles replace heuristic noise/color-driven displacement for those verified masters. An owned Geometry Nodes offset modifier runs before armature skinning and preserves topology, Basis coordinates and shape-key values. Repeated repair overwrites its own attribute rather than accumulating offsets; edited or unmatched source geometry receives a diagnostic instead of a guessed offset.

## Limits
Shader data and static authored shell deformation are reconstructed from native evidence. Blender’s hair/surface BSDF remains an approximation of UE hair scattering. Rabbit’s Foot still shows excessive card-shaped lighting contrast in controlled renders: its color mapping is corrected, but full visual parity is NOT verified. Experimental camera-facing normals, forced shadow removal and arbitrary emission were not included as fixes. Native fur weather, animated wind, proximity fade and morph normal deltas are outside this dry static pass. Existing decal construction is unchanged.

Evidence, isolated tests, renders and release package: Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/21923.

## Validation and current scene
83 native fur checks pass on Bells, GWRanger and Huntsman: exact material identities, source offsets, zero-Length LOD, topology/UV/Basis/key preservation, repeated repair, linked-data isolation and an actual armature pose. Eight independent shell/LOD shader bakes match the native formulas (maximum color error4.6e-8; alpha/specular exact). Four fur-card UV/blend cases pass;31 existing hair/facial-hair meshes preserve geometry, placement, UVs and keys. Native Eevee render compiles all six representative fur sections with complete dependencies. These are functional/data checks, not proof of game-render parity.

ZIP installation, overwrite, disable/re-enable and persisted restart pass. Live update repairs only Rabbit’s Foot’s card slot and preserves788 objects, all mesh geometry/UV/slot indices/shape-key values, selection, placement and sun settings/light linking. Saved copy: Z:/Datamining/Arc Raiders/Blends/AllAccessories-2.19.23.blend. The original AllAccessories-2.0.0.blend is not overwritten. Concurrent weapon/UI work is excluded from this release and commit.


Fresh primary-path verification: ArcPak.Export extracted Bells FurTop directly from the installed game with all eight required textures; the resulting assets imported with both exact MIs and native shell offsets, without manually supplied fixture dependencies. PSK-only secondary imports need a matching UEModel to recover authored shell normals/COL0/section correspondence; otherwise the native surface can build but shell displacement is safely diagnosed as unavailable.


## 2026-10-16 — Native outfit shell fur (2.19.23)

### Game
Current 2.1.0 M_FurShells and M_FurLOD are distinct masked, default-lit material masters; they are not the M_Hair_Metahuman_ColorMap card family used by RabbitFoot. Exact masters live under /Game/Pioneer/MaterialLibrary/Character/CustomMaterials. Compact export identity is retained by StateId: Shells 0EFD3174-42A2521C-D62D71BB-C1D84A42; LOD 84A975DB-403BE39A-019F7EB3-D067328C.

Highest-LOD source geometry already contains coincident shell copies. Shell indices are floor(original Unreal UV0.V), not vertex-color ramp samples or a guessed subdivision count. COL0 green is the authored shell length mask. Native Shells vertex offset is normal * floor(V) * COL0.G * Length in Unreal centimetres. The separate FurLOD vertex offset is normal * (1 + (COL0.G*ShellCount*Length - 1)*MaskLength) centimetres. Native shell roots have index0 and stay undisplaced even when the MI Length is nonzero. LOD zero Length with MaskLength1 stays undisplaced; this must not become a fallback nonzero displacement.

Bells FurTop has 1599 base vertices and12792 shell vertices, 8 authored copies. Its shell MI has ShellCount8 and Length.5cm; a UV boundary reaches floor(V)=9, yielding the measured4.5cm maximum. GWRanger UpperBody has106 base fur vertices and636 shell vertices, 6 copies, Length.1cm and measured maximum.6cm. Huntsman ShoulderFur has456 base and2388 shell vertices, Length.238cm and measured maximum1.428cm. Source base/shell MI paths remain distinct: Bells MI_FurTop_Fur and MI_FurTop_FurTop must not be confused by prefix matching.

Shader arithmetic was traced from current SM5 pixel/vertex bytecode, matching CachedExpressionData defaults and preshader expressions. Shell root-tip factor is (1-G)+saturate(layer/(ShellCount+1)); LOD uses saturate(FurMask.R). Native alpha ratios use FurMask.R, AlphaCutoff and layer index, with the separate inherited opacity-mask clip threshold. Native albedo preserves ColorTint, RootColor/TipColor, Color texture and the sampled sheen/fresnel terms. Roughness is saturate(1-gradient.R*mask.R+Roughness); specular is mask.R squared. Flow, WindMask and UV_Noise are sampled textures, not procedural Blender noise. Wind is frozen at native time0, using source component X/Z in cm. Original Unreal V is recovered as 1-BlenderUV0.V before native math; sampled texture coordinates convert back after native math.

### ST
No asset labels, catalog aliases or rarity assignments change. Source mesh/MI package identity determines native family and exact section binding. UEModel LOD section material paths are authoritative before fuzzy fallback, including same-MI duplicate sections. Compact material exports carry native StateId identity; referenced master texture exports provide UV_Noise and Gradient dependencies.

### Human
materials/fur_shells.py builds the verified native dry static surface and one owned Geometry Nodes Set Position modifier before the armature. No shells are synthesized, no subdivision is added, and no arbitrary color-driven displacement is used. Existing ArcFurSubdiv is removed only after verified native geometry succeeds. Basis, topology, UVs, user shape targets and rig objects are preserved; linked mesh data is copied before writing owned attributes. Geometry must match source vertex/face/UV identity and an exact affine source frame. Missing source normals/COL0 or edited geometry produces an explicit diagnostic instead of guessed offsets. Stored original source component positions preserve the frozen wind lookup under origin rebasing. Morph normal deltas are not currently imported, so extreme morphs preserve source rather than re-evaluated normals.

The native material uses Blender Principled as a BSDF approximation; runtime weather, per-frame wind, screen fades/pre-exposure, antialiasing/LOD transitions and engine-specific scattering are not claimed identical. The complete-file texture fixture has no missing dependencies. No live Blender scene was changed by these tests.

83 isolated Blender checks cover three real current-game meshes, all six embedded fur sections and two masters, exact Bells section MI resolution, source geometry/UV/shape preservation, repeated repair, linked isolation, malformed geometry rejection, real posed-bone deformation, zero-Length LOD and unrelated surface slots. Native vector offsets match independent per-vertex formula within1e-7m; evaluated shape plus offsets within2e-7m, posed within6e-7m. A private Eevee render compiles and displays all six graphs without missing textures. Evidence is Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/21923/fur-audit/native-fur-results.json, native-fur-render.png and native-shell-manifest.json.

Fresh RabbitFoot mesh export is byte-identical to the cached UEModel VERTICES, INDICES, TEXCOORDS, NORMALS and MATERIALS (6299vertices,12690indices,LOD0). Its observed card bands therefore cannot be blamed on stale geometry or lower LOD in that fixture.
