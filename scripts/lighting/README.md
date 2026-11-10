# Lighting scenario catalog + LUT producer verification

`assets/lighting_scenarios.json` is generated from static pak reads (no game
process). Regenerate it after a game update.

## 1. Dump lighting exports

Build the probe against an ArcPak.Export build folder (one that contains
`ArcPak.Export.dll`, `CUE4Parse.Arc.dll`, `Newtonsoft.Json.dll` and the native
zlib/oodle DLLs), then run it with that folder as the working directory:

```
dotnet build scripts/lighting/probe -c Release -p:ArcPakBuild=<arcpak build>
set ARCPAK_BUILD=<arcpak build>
set ARC_USMAP=<mappings .usmap for the build>
set LIGHTING_EVIDENCE=<evidence folder>
set DOTNET_PROCESSOR_COUNT=4
```

| Mode | Arguments | Output |
|---|---|---|
| `list` | – | `file-list-lighting.txt` (lighting-related pak keys) |
| `cells` | `<persistent .umap key> <out>` | persistent level + its `_Generated_` cell list |
| `types` | `<package key> <TypeSubstrings> <out>` | selected exports of one package (`cells-<Map>.json`: `WorldPartitionRuntimeLevelStreamingCell,DataLayerInstance`) |
| `dump` | `<package list> dump-<Map>` | lighting exports per cell (PostProcess, Sky, lights, fog, data layers) |
| `exports` | `<package list> curves.json` | every export of each package (Lighting/Curves CurveFloat keys) |
| `mesh` | `<static mesh key> <out>` | LOD0 positions + every UV channel (`SM_Ultra_Dynamic_Sky_Sphere` UV5 = the cloud-wisps projection, see docs/lighting-look-2.20.3.md) |

Maps: BuriedCity_01, FrozenTrail_01, MountainCompound, RivenTides_01,
Spaceport_01, TheBlueGate_01, TheDam_02, plus the Home `Home_Lighting_*` levels
(`dump-Home`).

## 2. Build the catalog

```
python -I scripts/lighting/build_lighting_catalog.py <evidence folder> assets/lighting_scenarios.json <build id>
```

Chain: `LevelLightingScenariosDataAsset` (tag → DataLayerAsset, weight) →
`DataLayerInstanceWithAsset` → streaming cells carrying the layer → lighting
actors in those cells. Only unbound post-process volumes are kept; their
`overrides` list `bOverride_*` fields (null = engine default). Disabled
components (the sky actor's `Exposure` component) are skipped. Sun/moon
`forward` is the light's world X axis in Unreal coordinates, composed through
the `AttachParent` chain (the sky actor root carries a yaw). The sky sphere MID
(`sky_material`) keeps its scalars, vectors, texture names and paths; `sky_sphere`
is the `Ultra_Dynamic_Sky_Sphere` component (world `x_axis` through the same chain,
mesh, visibility, location, scale) that orients the cloud layer.

## 3. LUT producer verification

`verify_lut_producer.py` renders the game's compiled combine-LUTs pixel shader
(Global SM5, hash `3655BA8909582BB20C69420AB81ED97B6FAB9636`) on WARP through
the parity-v2 `dr_oracle` runner and compares all 32 slices with
`lighting_grade.build_combined_lut`. 2.20.1 results (max abs error, display-coded):
no strip 9.7e-6; Kodak strips 3.5e-4–3.9e-4 (8-bit filter-weight rounding at
texel boundaries); random grading/white balance/tint 1.8e-4.
