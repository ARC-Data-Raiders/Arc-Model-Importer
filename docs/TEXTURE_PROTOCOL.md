# Arc Raiders Texture Protocol (Blender Importer)

Catalog of texture / map types observed in cooked `MI_*.json` / `M_*.json` under
`PioneerGame/Content/Pioneer` (Environment, MaterialLibrary, Characters, Firearms,
Enemies), plus the connection rules used by `materials.py`.

Parameter names below are **real MI `Textures` keys** from FModel compact dumps
(and matching `TextureParameterValues.ParameterInfo.Name` in full Exports). Asset
suffixes (`_CR`, `_NOH`, …) come from the referenced texture stems.

Sample size for this catalog: ~5900 MI files under Environment + MaterialLibrary +
Interactables + Vehicles + Items (1058 unique TextureParameter signatures), plus
character/weapon paths already handled by Arc Texturer / weapon setup.

---

## 1. Texture type table

| Role | Common MI parameter names | Typical asset suffix | Packing (RGB / A) | Blender Principled target |
|------|---------------------------|----------------------|-------------------|---------------------------|
| Base colour + roughness | `CR`, `1. CR`, `0. CR 1`, `CR Texture`, `Base_Material_CR`, `Color Base`, `CR_1`, `BC` | `_CR` (also `_CS`, rarely `_C`) | **RGB = albedo**, **A = roughness** | Base Color ← RGB; Roughness ← Alpha |
| Base colour (+ alpha) | `BaseColor`, `PM_Diffuse`, `CA`, `ColorAlpha`, `1. CA` | `_CA`, `_C` | RGB = albedo; A = opacity/mask when masked | Base Color; Alpha when clipped |
| Base normal (env) | `NOH`, `1. NOH`, `0. NOH 1`, `Normal Base`, `Base_Material_NOH`, `NOH_1`, `Normal`, `PM_Normals`, `NormalMap` | `_NOH` | **RG = tangent normal**, **B = occlusion**, **A = height** | RG+Z=1 → Normal Map (DirectX + NormalFlipper); B → AO × albedo |
| Packed weapon/prop normal | `NOM`, `NXM`, `NMX`, `NXX/NMX Texture`, `NXX`, `HolesNXX` | `_NOM`, `_NXM`, `_NMX`, `_NXX` | RG = normal; B = AO (NOM) or unused (NMX/NXX); A = metallic (NOM/NXM/NMX only) | RG+Z=1 → Normal; Metallic ← A when not pure NXX |
| NAO trim/decal normal | `NAO`, `DecalTrimsheet` | `_NAO` | RG = normal; B = AO; A = opacity | RG+Z=1 → Normal; Alpha ← CLIP; AO → albedo |
| Height / parallax | `Height/HX`, `NormalRoughnessHeight` | `_HX`, `_H`, `_NRH` | Height in luminance or dedicated channel | Bump (enemy decals) / unused on env preview |
| Specular / ORM masks | `PM_SpecularMasks`, `3. Blend Mask`, packed `GRM` assets | `_GRM`, `_ORM` | Often **G/R/M** or grayscale blend; used as **layer blend** more than ORM→Principled | Blend Factor (MapRange); optional Rough/Metal later |
| Colour overlay / ColorVar | `Overlay`, `4.. Overlay`, `Color Overlay`, `WaterlineOverlay`, `PM_Diffuse` (when aliased to ColorVar) | `_C`, ColorVar stems | RGB tint / variation; sometimes A unused | Multiply into albedo, mix by `Overlay_*_Strength` / `OverlayIntensity` |
| Rust / alpha overlay | `2. Overlay CR`, rust `T_Metal_Rust_Overlay_*_A` via Overlay slots | `_A` | Alpha-heavy wear/rust | Overlay mix / multiply |
| Detail / micro normal | `Detail Normal Texture`, `Detail Normal`, `1. Detail`, `Detail Normal Worn`, `Detail NXR` | `_NOH`, `_NAOH`, `_NCR`, `_NXR` | Usually normal (NCR may pack extra) | Second Normal Map, VECTOR-mix onto base normal |
| Layer-2 albedo | `2. CR`, `0. CR 2`, `CR Blend`, `CR_Blend`, `Breakup_Material_CR`, `CR Breakup`, `Color Top`, `CR_2` | `_CR` | Same as CR | Mix Color with blend mask |
| Layer-2 normal | `2. NOH`, `0. NOH 2`, `NOH Blend`, `NOH_Blend`, `Breakup_Material_NOH`, `NOH Breakup`, `Normal Top`, `NOH_2` | `_NOH` | Same as NOH | Mix Normal with blend mask |
| Blend / breakup mask | `3. Blend Mask`, `4. Mask`, `Breakup Mask`, `Breakup Mask - Linear Grayscale`, `PaintBreakup`, `Mask`, `Variation Masks` | `_GRM`, `_X`, `_M` | Linear grayscale or packed; **R (or lum) = blend** | MapRange(Low→High) → Mix Factor |
| Prop baked AO | `Prop AO Texture` | `_AO`, `_O` | Grayscale AO | Multiply onto albedo |
| Emissive / soot pack | `EX`, `EXX`, `EmissiveMask`, `PM_Emissive` | `_EXX`, `_E` | R = emissive mask; G/B = dirt/soot (weapon path) | Emission + dirt (weapon setup) |
| Wear pack | `Wear`, `1. Wear CR`, `1. Wear NOH` | `_Wear`, wear CR/NOH | Weapon wear RGBA conventions | Weapon `_apply_wear_map` |
| Foliage | `BaseColor`/`Normals`, `CA`/`1. CA`, `NTR`/`1. NTR`, `TrunkBaseColor`/`TrunkNormal`; colours `Leaves Tint`, `Subsurface Color`; scalars `Roughness Leaves` | `_CA`/`_CS`/`_C`, `_NTR`/`_NTX`/`_N`/`_NOH` | Colour + alpha; tangent normal | Base Color + Alpha (CLIP/HASHED) + Normal; two-sided; **fallback sibling/library textures** when unresolved |
| Sand / dunes | `BaseColor`/`PM_Diffuse`/`Param`/`BaceColor/Height`/`1. CH`; normals `NormalMap`/`PM_Normals`/`Param_1`/`Param_2`/`Normal/Roughness`/`1. NR`/`NOH`; colour `BaseColor Tint`; scalars `Tiling`, `Normal Strength` | `_CH`, `_CR`, `_NR`, `_NH`, `_NOH`, `_N` | CH/CR = albedo(+H); NR = normal(+rough); **not** NOR→Displacement | Dual-scale Principled (macro+micro albedo, fine+macro normal); tint; roughness variation; **GroundPlane** sand on sandy maps |
| Water | Colours `3. Water Color`, `3. Water Color Shallow`, `1. Shore Color`, `Deep`/`Shallow`; scalars `3. Water Clarity`, `1. Shore Roughness` | optional `_NR`/`_N` (inspection) | Colour-driven water planes | Water↔Shore Mix (proximity/AO factor) + ridged world bump; Transmission + Alpha BLEND; IOR≈1.333 |
| Glass (env panes) | `CubemapInside`/`CubemapOutside`, `C`/`NXX`, `MaskTexture` | cubemap, `_NX`, `_CR`, `_M` | Special glass parents | Transmission stub + alpha blend/clip |
| Clothing arrays | `TextureArray_Normals` / `_Masks` / `_Colors`, OCM, ColorMask | TA slices | Zone IDs + tiled N/R | Arc Texturer group (not Principled) |
| Decal colour/mask | `Mask`, `Raider Mark Texture`, `Decal Mask`, `CA`, `Normal Overlay`, `SignTexture` | various | Masked decal parents | Decal / enemy-decal paths |

### Filename suffix cheat sheet

| Suffix | Meaning used by importer |
|--------|--------------------------|
| `_CR` | Colour RGB + Roughness A |
| `_CA` | Colour + Alpha |
| `_CS` | Colour (+ specular/extra in A — treat like CR for albedo) |
| `_C` | Colour / ColorVar overlay (often no roughness) |
| `_NOH` | Normal RG + Occlusion(B) + Height(A) |
| `_NOM` | Normal RG + Occlusion(B) + Metallic(A) |
| `_NXM` / `_NMX` | Normal RG + Metallic(A) |
| `_NXX` / `_NX` | Normal RG only (alpha unused — never metal) |
| `_NAO` | Normal RG + AO(B) + Opacity(A) |
| `_NAOH` | Detail: Normal RG + AO(B) + Height(A) |
| `_NR` / `_NRH` | Normal RG + Roughness(B) (+ Height A on NRH) |
| `_NHM` | Normal RG + Height(B) + Metallic(A) |
| `_NTR` | Foliage / vegetation tangent normal (full RGB) |
| `_GRM` | Packed mask / blend (not always G/R/M→Principled) |
| `_ORM` | Occlusion / Roughness / Metallic (rare on env MIs) |
| `_X` | Linear grayscale mask |
| `_A` | Alpha/rust overlay |
| `_EX` / `_EXX` | Emissive + imperfection pack |
| `_NCR` | Detail normal (sometimes with colour crosstalk) |
| `_HX` / `_H` | Height |

### Packed normal channel → shader sockets (`wire_packed_normal_channels`)

Arc exports tangent normals in **RG** (UE BC5 / packed-sheet convention). Blue is
**never** Normal Z on NOH/NAO/NOM/NMX packs — rebuild `Combine(R,G,Z=1)` →
NormalFlipper → Normal Map (DirectX). Clothing **ColorMask** / TextureArray
paths are unchanged (Arc Texturer).

| Pack | Detect from | Normal | Blue → | Alpha → | Principled |
|------|-------------|--------|--------|---------|------------|
| **NOH** | param `NOH` / stem `_NOH` | RG+Z=1 | Occlusion | Height (inspect) | Normal; B × albedo AO |
| **NAO** | `NAO` / `_NAO` | RG+Z=1 | AO | Opacity | Normal; B × AO; A → Alpha CLIP (trim) |
| **NAOH** | `_NAOH` (detail) | RG+Z=1 | AO | Height | Detail Normal; soft AO |
| **NOM** | `NOM` / `_NOM` | RG+Z=1 | Occlusion | Metallic | Normal; A → Metallic; B × AO |
| **NXM / NMX** | `NXX/NMX Texture` + stem | RG+Z=1 | unused | Metallic | Normal; A → Metallic |
| **NXX / NX** | `NXX` / `HolesNXX` / `_NXX` | RG+Z=1 | unused | unused | Normal only (no metal) |
| **NR / NRH** | `NormalRoughness` / `_NR*` | RG+Z=1 | Roughness | Height (NRH) | Normal; B → Roughness if no CR.A |
| **NHM** | `_NHM` | RG+Z=1 | Height | Metallic | Normal; A → Metallic |
| **NAM / NHA / NOA / NAA** | stem | RG+Z=1 | AO/Height/Mask | Metal or Alpha | per-kind extras |
| **N / NTR / RGB** | plain `Normal` / foliage | full RGB | — | — | Normal Map as authored |

Setup stamp: `arc_env_setup=v5` + `arc_normal_pack=<kind>` on env/metal/road graphs.
Force All / Fix White Materials rebuilds stale stamps.
---

## 2. Material families (routing)

`classify_mi_family(mi, stem, slot)` → handler. Order is specific → general.
Every shared MI datablock is stamped with `mat["arc_mi_family"]`.

Surface families (environment / metal / weapon / simple / mask-decal) prefer
**MI texture-role inventory** (`inventory_mi_textures`) over mesh-name heuristics:
count + role of authored `TextureParameterValues` (Material/Wear/Overlay CR+NOH,
packed ORM, SignTexture, ColorMask, …). SignTexture is never base albedo;
`EnableSigns` only gates decal overlay mix. Clothing ColorMask / TextureArray
markers are left for Arc Texturer.

| Family | Detection (summary) | Setup | Key Blender connections |
|--------|---------------------|-------|-------------------------|
| **emissive** | Stem has `emissive` or token-boundary `_light_` | `_setup_weapon_emissive_material` | Emission only |
| **scan_display** | `scandisplay` / `screen` in stem or slot | `_setup_enemy_scan_display_material` | Screen / scan shader |
| **decal** | `decal` in **MI stem**, dedicated slots (`decals` / `M_Leak` / sticker), true enemy NAO+Height (`DecalTrimsheet` / `enemydecals`), or mask-only murals. **Not** `ConcreteEdgeDecal` / env trim NAO | `_setup_map_decal_material` → enemy NAO only for real enemy-shell MIs | Alpha CLIP; NAO or Mask→Alpha |
| **glass** | Cubemaps, `M_WindowPane_*`, `glass` + `C`/`NXX`, translucent blend | `_setup_glass_env_material` | Transmission≈0.9; C→Base; NXX→Normal; BLEND/CLIP; two-sided |
| **water** | `MI_Water_*` / ocean / lagoon / river; or colours `3. Water Color` / `1. Shore Color` / `Deep`+`Shallow` | `_setup_water_material` | Water/Shore colours → Mix (proximity/AO) → Base Color; ridged world bump; Transmission + Alpha |
| **sand** | `MI_Sand*` / `M_SandDune` / dune spline / `*_Sand` / `Param`+sand paths; often `IsNull` empty Textures | `_setup_sand_material` (+ `setup_landscape_sand_material` for heightmap) | Dual-scale CH/CR + NR/N; Noise breakup; tint; clamped Tiling; **no NOR→Displacement**; GroundPlane auto on RivenTides etc. |
| **foliage** | `CA`/`1. CA`/`NTR`/`Trunk*`, `M_Vegetation_*`, TwoSidedFoliage, leaf/grass/vine names + masked/two-sided | `_setup_foliage_material` | BaseColor/CA→Color+Alpha; NTR/Normals→Normal; CLIP or HASHED; **no backface cull**; Leaves Tint / Subsurface Color / Roughness Leaves; **tex fallback** |
| **road** | `0. CR 1`/`0. NOH *`, `M_Tarmac_*`, tarmac/asphalt stem | `_setup_environment_material` (`family=road`) | Dual CR/NOH + `4. Mask` blend (same layered graph) |
| **metal** | `CR Texture` + `NXX/NMX Texture` (not enemy/weapon parents) | `_setup_environment_material` (`family=metal`) | CR→Color/Rough; NMX A→Metallic; Overlay + Prop AO |
| **environment** | Texture inventory: Material/Wear/Overlay CR+NOH, breakup layers, CR+NOH/NAO, Overlay+base | `_setup_environment_material` | Layered CR/NOH + Overlay + Detail Normal; Sign only if EnableSigns |
| **weapon** | `CR`+`NOM`/`NXM`/`Wear`/`EXX` (enemies, firearms, props on enemy preset) | `_setup_weapon_main_material` | Existing weapon graph |
| **simple** | Inventory: 1–2 maps albedo±normal, or albedo+normal+RoughnessMetal/ORM/mask (no layered packs) | `_setup_simple_material` | Best-effort Principled + log |

Clothing / outfits stay on **Arc Texturer** (`setup_arc_texturer_material`) and are not routed here.
Visor **clothing glass** stays on `_setup_visor_material` (ColorA/B); map panes use **glass** family above.

High-frequency signatures from the Environment scan (examples):

| Count (approx) | Signature | Family |
|----------------|-----------|--------|
| 489 | `CR` + `Normal` + `PM_Normals` | simple |
| 116 | `BaseColor` + `NormalMap` (+ PM aliases) | simple |
| 94 | `BaseColor` + `Normals` | foliage (when masked/two-sided / vegetation) |
| 79 | `CR Texture` + `NXX/NMX Texture` + Overlay + AO | metal |
| 66 | `CR` + `NOH` | environment |
| 65 | `CR` + `NOH` + `Overlay` | environment |
| 53 | `CA` only | foliage |
| 4+ | `0. CR 1/2` + `0. NOH 1/2` + `4. Mask` | road |
| 11+ | Cubemap / `C`+`NXX` glass | glass |

---

## 3. Layering rules (environment / road / metal)

Environment materials rarely use a single CR+NOH pair. Observed stack, bottom → top:

```
[1] Base CR + NOH          (or Color Base / Normal Base / 0. CR 1)
[2] Layer-2 CR + NOH       blended by mask (brick↔stucco, dust↔floor, paint breakup, tarmac)
[3] Overlay / ColorVar     tint variation (UseOverlay / EnableOverlay)
[4] Detail Normal          micro-structure (Enable DetailNormal)
[5] Global Tint / Paint    vector Tint × albedo
[6] Prop AO / NOH.B        darken albedo
```

### Pattern A — Single slab + overlay + detail (most concrete walls/floors)

Example: `MI_PR_Concrete_Wall_Slab_01_A`, `MI_Concrete_Fountain_01_A`

| Slot | Params |
|------|--------|
| Base | `CR` + `NOH` |
| Overlay | `Overlay` (= ColorVar `_C`), often also bound as `PM_Diffuse` |
| Detail | `Detail Normal Texture` / `PM_Normals` (may duplicate) |
| Scalars | `Overlay_BaseColor_Strength`, `Overlay_Roughness_Strength`, `OverlayTiling`, `Tiling`, `Normal Strength` |
| Switches | `UseOverlay`, `Enable DetailNormal` |

### Pattern B — Numbered dual layer (buildings / brick / stucco)

Example: `MI_Res_BrickStucco_01_A`, `MI_BC_Mall_Facade_01_A`

| Slot | Params |
|------|--------|
| Layer 1 | `1. CR`, `1. NOH`, optional `1. Detail` |
| Layer 2 | `2. CR`, `2. NOH` |
| Mask | `3. Blend Mask` (often `_GRM`, also aliased as `PM_SpecularMasks`) |
| Overlay | `4.. Overlay` |
| Scalars | `3. Blend Mask Low/High/Size`, `4. Overlay Strength/Low/High/Size`, `1. Detail Normal Strength`, `Blend Normal Strength`, `Normal Strength` |

### Pattern C — Named blend pair (facades / VT concrete)

Example: `MI_BC_Commercial_03_Facade_01_B1`, `MI_ParkingGarage_Ceiling_01_A`

| Slot | Params |
|------|--------|
| Base | `CR`, `NOH` |
| Blend | `CR Blend`, `NOH Blend` |
| Mask | `PaintBreakup` (`_X`) |
| Overlay | `Overlay` |
| Detail | `Detail Normal Texture` |

### Pattern D — Base + Breakup material

Example: `MI_BC_Hospital_Floor_01_A`, `MI_Railway_01_Concrete_02`

| Slot | Params |
|------|--------|
| Base | `Base_Material_CR` / `Base_Material_NOH` **or** `CR` / `NOH` |
| Breakup | `Breakup_Material_CR` / `Breakup_Material_NOH` **or** `CR Breakup` / `NOH Breakup` |
| Mask | `Breakup Mask - Linear Grayscale` / `Breakup Mask` |
| Switches | `Enable Breakup Material`, `Use Breakup Material` |

### Pattern E — Top / Base debris

Example: `MI_BrokenWall_01`, `MI_ConcreteDebris_Pile_01`

| Slot | Params |
|------|--------|
| Base | `Color Base`, `Normal Base` |
| Top | `Color Top`, `Normal Top` |

### Pattern F — Prop trim metal

Example: `MI_CableTray_01_PropTrim_Metal_01_A`

| Slot | Params |
|------|--------|
| Albedo | `CR Texture` |
| Normal | `NXX/NMX Texture` (`_NMX` → Metallic A) |
| Overlay | `Overlay` |
| AO | `Prop AO Texture` |

**Parent inherit (2.18.6+):** Compact child MIs such as
`MI_Wrh_Ceiling_01_PropTrim_PaintedBeams_01_A` often only override Prop AO + Overlay.
Missing `CR Texture` / `NXX/NMX` are filled deterministically from the exact library
parent (`MI_PropTrim_Painted_01_A`, …) or Beams sibling (`MI_Wrh_Beams_01_PropTrim_*`)
— never by fuzzy name similarity. Soft-match folder MI inference is **disabled**
(`ENABLE_FUZZY_MI_INFER = False`); Stage 2 / Fix White use SM `StaticMaterials` only
(+ water/decal preferred_mi when already restricted).

**Glass vs trim:** Win ceilings keep `M_BrokenGlassSDF` (glass family) and PropTrim
(metal) as separate slots. Group Map Collections moves glass refs into `{Map}_Glass`.

### Pattern F2 — Architecture concrete edge trim

Example: `MI_Concrete_Trim_02_A` on `SM_POI09_ControlTower_01_Edges_01_A` / `ConcreteEdgeDecal`

Parent: `M_ArchitecturePreset_Trim+CR+NAH` (Masked).

| Slot | Params |
|------|--------|
| Albedo | `CR` → `T_TrimConcrete_*_CR` |
| Normal + AO + Opacity | `NAO` → `T_TrimConcrete_*_NAO` (A → CLIP) |
| Detail | `Detail Normal` / `PM_Normals` (optional; honour `Detail Normal Intensity`) |
| Scalars | `UV Mode` (≥1 = **WorldAlignedTexture**), `UV Scale`, `Color Multiply` |
| Switches | `Use Alpha mask`, `OffsetUVs` |

When `UV Mode ≥ 1`, sample CR/NAO in **world metres** (`_TRIM_WORLD_METERS_PER_TILE`) so weathering is continuous across modular edge seams. Mesh-UV sampling caused abrupt stain breaks and apparent trim-width steps at horizontal module joints (e.g. Control Tower pillars).

### Pattern G — Tarmac / road


Example: `MI_Tarmac_01_C` (`M_Tarmac_02`)

| Slot | Params |
|------|--------|
| Layer 1 | `0. CR 1`, `0. NOH 1` |
| Layer 2 | `0. CR 2`, `0. NOH 2` |
| Mask | `4. Mask` |

### Pattern H — Foliage

Examples: `MI_TBG_Grass_01_A`, `MI_Swamp_Reeds_01`, `MI_South_Swamp_Vines_Backdrop`, tree MIs with Trunk*

| Slot | Params |
|------|--------|
| Colour | `1. CA` / `CA` / `BaseColor` |
| Normal | `1. NTR` / `NTR` / `Normals` / `NormalMap` / `NTX` |
| Tint / SSS | `Leaves Tint`, `Subsurface Color`, `Roughness Leaves` |
| Trunk (optional) | `TrunkBaseColor`, `TrunkNormal` |
| BPO | Masked, often `TwoSided`, `MSM_TwoSidedFoliage`, `DitheredLODTransition` |

When maps fail to resolve: infer `_CA`/`_NTX` from sibling / parent vegetation / MaterialLibrary (e.g. StonePine Branches → Aleppo `T_South_Forest_Tree_PineBranches_*`).

### Pattern I — Sand / dunes

Examples: `MI_SandPile_01_A` (IsNull), `MI_Dune_Spline_01` (IsNull), `MI_South_Dunes_Rock_*_Sand`, `MI_South_Dunes_Rock_Dune_Prop_*`, `M_SandDune`

| Slot | Params (real names) |
|------|---------------------|
| Albedo (macro) | `BaseColor`, `PM_Diffuse`, `Param`, `BaceColor/Height`, `1. CH`, `CR` |
| Albedo (micro) | Inferred second pack — `T_South_Base_Ground_Sand_02_CH`, twigs/detail CR/CH |
| Normal (fine) | `NormalMap`, `PM_Normals`, `Param_1`/`Param_2`, `Normal/Roughness`, `1. NR`, `NOH` / `NH` |
| Normal (macro, optional) | `T_South_Base_Sand_Dunes_01_N` / `_03_NH` / Slope `_N` |
| Tint | `BaseColor Tint` (often HDR >1) |
| Scalars | `Tiling` (clamped; **never** use 100–500 as Mapping Scale), `Normal Strength` |
| Typical assets | `T_South_Base_Sand_Dunes_05_CR` / `_04_CH` / `_04_NR`, `T_South_Base_Ground_Sand_02_CH`/`_NR` |

**Look split:** large dunes = displaced heightmap / mesh geometry; shader = micro sand BRDF only. Dual albedo mixes via Noise Fac (world/Object or UV). Roughness from CR/NR alpha + slight noise. Optional subtle Bump from **CH** luminance only — **never** plug `_NOR`/`_NR` into Displacement Height (NOR is not a heightfield).

Many dune MIs export with empty `Textures` (`IsNull: true`) — parent `M_SandDune` / `M_SandPile_01` holds the graph. Importer uses **adjacent** `South/Dunes/Textures` (+ `South/Base/Textures`) fallback.

**GroundPlane:** sandy maps (`RivenTides`, `*Dunes*`, `*Desert*`) auto-apply landscape sand on displace create / **Reload Heightmap**. Any map: operator **Apply Sand to Heightmap** (`arc.apply_heightmap_sand_material`) — no Stage 1.

**Ground map texturing (2.18+):** optional **In-Game Map** (`T_InGameMap_*`) UV masks (rock / cream flats / soft structure darkening) + **HLOD Color** (`T_*_Color_*`) palette (cream / gray rock / pink sediment). Single `xN_yM` tiles are **palette-sampled only** (not UV-aligned to the full plane). Auto-resolve from `Pioneer/UI/Ingame/HUD/Map/Assets` + map `Textures/`; panel pickers override. Displace remains the heightfield — never NOR→Displacement.

### Pattern J — Water planes

Examples: `MI_Water_DuneLagoon`, `MI_Water_River_Opaque_River_RockyCreek`, `MI_OceanBackdrop`, `MI_Water_*` stubs

| Slot | Params (real names) |
|------|---------------------|
| Water body | `3. Water Color` |
| Shallow | `3. Water Color Shallow` or `Shallow` |
| Deep | `Deep` |
| Shore | `1. Shore Color` |
| Clarity / rough | `3. Water Clarity`, `1. Shore Roughness`, shoreline High/Low |
| Optional normal | `1. NR` (shore/ground pack on some lagoons) |

Colour-driven: missing textures are fine — never leave Principled white when Water/Shore/Deep/Shallow exist. Shore tint near mesh intersections via baked `arc_shore_proximity` + Cycles AO (see Water algorithm).

### Clothing / weapons (out of env stack)

- **Clothing**: Arc Texturer — OCM + ColorMask + Texture2DArrays + decals.
- **Weapons/enemies**: CR + NOM/NXM + Wear + EXX (`_setup_weapon_main_material`).
- **Enemy decals**: NAO + Height on **enemy-shell** MIs only (`DecalTrimsheet` / `enemydecals` paths). Env trim `*_NAO` (e.g. `MI_Concrete_Trim_*` on `ConcreteEdgeDecal`) stays **environment**, not enemy-decal.

---

## 4. Plugin connection protocol (step-by-step)

Routing lives in `_dispatch_weapon_slot_material` (shared by map Stage 2 and weapon SK slots):

1. Parse flat MI (`_parse_flat_mi_json`) — Textures + Scalars/Switches/Colors + Parent + TwoSided/BlendMode from BPO/Exports.
2. `classify_mi_family` → family string.
3. Dispatch to the family setup in the table above.
4. Soft-fail: on exception, log and try `_setup_simple_material` so Stage 2 continues.

### Environment / road / metal algorithm (`_setup_environment_material`)

1. **Load** flat MI + texture cache (`_tex_lookup_from_flat_mi` / `_load_image_cached`).
2. **Pick layer-1 CR** in order: `1. CR` → `0. CR 1` → `CR` → `CR Texture` → `Base_Material_CR` → `Color Base` → `BaseColor` → …
3. **Pick layer-1 normal** in order: `1. NOH` → `0. NOH 1` → `NOH` → … → packed `NXX/NMX` / `NOM` / **`NAO`** (trim).
4. Wire CR → Base Color + Roughness (skip roughness when albedo param is BaseColor/CA).
5. NOH/NAO → Normal Map (DirectX, NormalFlipper); B as AO candidate; NMX/NOM A → Metallic.
6. **Layer-2** if present and switch not off — mask contrast → mix albedo/rough/normal.
7. **Overlay** if enabled — multiply ColorVar into albedo by strength.
8. **Detail normal** VECTOR-mix by strength (`Detail Normal Intensity` / `Detail Normal Tile` aliases).
9. **Tint** + **Color Multiply** + Prop AO (trims prefer `Color`, not RT Base Color).
10. **Architecture trim**: if Masked / `Use Alpha mask`, NAO.A → Principled Alpha + CLIP; if `UV Mode ≥ 1`, world-density UVs.
11. Unused texture params left as unconnected Image nodes (skip `T_*` alias duplicates).
12. Stale trim rebuilds (`arc_trim_world_uv` / `arc_trim_alpha` missing) on next Stage 2 / Fix White.

### Foliage algorithm (`_setup_foliage_material`)

1. Albedo from `1. CA` / `CA` / `BaseColor` → Base Color **and** Alpha.
2. Normal from `1. NTR` / `Normals` / `NormalMap` / `NTX`.
3. If albedo or normal is missing/unresolved → **`infer_vegetation_texture`**: same dir → parent/sibling vegetation folders (e.g. AleppoPine for StonePine) → remapped Content roots → MaterialLibrary Textures. Prefer `_CA`/`_C` and `_NTX`/`_NTR`/`_NOH`/`_N`. Stamp `arc_tex_fallback` + path.
4. Optional trunk maps (soft mix stub / inspection nodes).
5. `Leaves Tint` / `Subsurface Color` / `Roughness Leaves` (StonePine-style aliases) when present.
6. `blend_method` CLIP or HASHED (HASHED when dithered LOD / high clip); **`use_backface_culling = False`**.
7. Soft-fail: if truly nothing found, log and use Leaves Tint / green default — never leave pure white when a tint exists.

### Water algorithm (`_setup_water_material`)

1. Detect via name (`MI_Water_*`, ocean/lagoon/river) or colours (`3. Water Color`, `1. Shore Color`, `Deep`+`Shallow`).
2. **Water Color** RGB + **Shore Color** RGB → Mix **Water↔Shore** → Principled Base Color.
   - Authored `3. Water Color` / `1. Shore Color` when present; else Deep→Water / Shallow→Shore; else teal/sand defaults.
3. Mix **Factor** = max(`arc_shore_proximity` attribute, inverted Inside-AO) × strength — Shore near mesh intersections, open water stays Water Color.
4. Geometry Position → Mapping **World 4m/tile** → Noise (3D Ridged Multifractal) → Bump → Principled Normal.
5. Principled: Metallic 0, Roughness 0, IOR 1.333, Alpha ≈0.45, Transmission 1.0 (opaque rivers denser).
6. Stamp `arc_water_setup=v3_shore_prox` / colour props; stale water rebuilds on Stage 2 / Fix White.
7. **Refresh Water Shore Proximity** (also auto after Stage 2): BVH distance bake onto vertices; optional densify for sparse planes. Cycles AO is a soft fallback without bake; Eevee AO is weak.

### Sand algorithm (`_setup_sand_material` / landscape)

1. Detect via name (`MI_Sand*`, `M_SandDune`, dune spline, `*_Sand`) or Param/CH packs pointing at sand/dune textures. Exclude `Sandbox` room MIs.
2. Resolve **macro** albedo (`BaseColor` / `Param` / `CH` / `CR`) + **micro** albedo (Ground_Sand / twigs / detail) + fine NR + optional macro N/NH.
3. If missing → **`infer_sand_texture`** with preferred stems (`T_South_Base_Sand_Dunes_05_CR`, `T_South_Base_Ground_Sand_02_CH`, `…_04_NR`, `…_01_N`) under `South/Dunes/Textures` + `South/Base/Textures`.
4. Wire dual Mapping (UV×clamped Tiling, or Object meters on landscape) → Mix albedo by Noise Fac → Principled.
5. Fine normal (+ optional macro normal mix); CR/NR alpha → Roughness with soft clamp + noise; optional **CH bump only** (strength ~0.08). **Do not** connect normals to Displacement.
6. `BaseColor Tint` multiply when authored; Masked piles → CLIP.
7. **GroundPlane:** `map_prefers_sand_ground` (RivenTides / Dunes / Desert) on displace create & Reload; operator `arc.apply_heightmap_sand_material` anytime. Optional In-Game Map + HLOD Color via `apply_heightmap_map_texturing` (UV masks + palette Mix into Base Color).

### Glass algorithm (`_setup_glass_env_material`)

1. `C` / colour → Base Color; `NXX` → Normal.
2. Transmission Weight ≈ 0.92; low roughness (`Glass Roughness`).
3. Mask → Alpha when present; else translucent blend alpha.
4. Cubemaps left unconnected for inspection (reflection stub later).

### Priority / fallback when missing

| Missing | Fallback |
|---------|----------|
| No CR / BaseColor family | Flat `Tint` / mid-grey Base Color |
| No foliage albedo/normal | Sibling/parent/library `_CA`/`_NTX` inference (`arc_tex_fallback`); else Leaves Tint |
| Water without colours | Default teal Base Color + log; name still routes to water family |
| No normal | Principled default normal |
| Overlay texture but `UseOverlay=false` | Skip overlay |
| Detail texture but `Enable DetailNormal=false` | Skip detail |
| Layer-2 without mask | Mix factor ≈ 0.35 |
| Mask tiling absurdly large (e.g. 3000) | Clamp to ~4 for mesh UV preview |
| `Normal Strength` > 1 | Clamp to ≤ 2.5 on Normal Map node |
| Weapon NOM/NXM only (no env markers) | Weapon path |
| Unknown params | Simple path + log |
| Setup exception | Soft-fail → simple |

### Normals overlaid (concrete / buildings)

Always composite in this order:

1. Base NOH (macro surface)
2. Mix in layer-2 NOH by blend/breakup mask
3. Mix in Detail Normal by detail strength

Do **not** replace the base normal with the detail map. Do **not** feed Overlay `_C` ColorVars into the Normal socket.

### Diffuse overlaid

1. Base CR albedo  
2. Mix layer-2 CR by the same blend factor as normals  
3. Multiply/mix Overlay ColorVar  
4. Multiply Tint  
5. Multiply AO  

Roughness follows the same layer mix on CR alphas, then optional overlay roughness strength.

---

## 5. Code map

| Concern | Location |
|---------|----------|
| Family classify | `materials.py` → `classify_mi_family` |
| Dispatch | `_dispatch_weapon_slot_material` |
| Env / road / metal Principled | `_setup_environment_material` |
| Foliage | `_setup_foliage_material` + `infer_vegetation_texture` |
| Sand / dunes | `_setup_sand_material` + `infer_sand_texture` + `setup_landscape_sand_material` / `apply_sand_to_heightmap_object` + `apply_heightmap_map_texturing` (InGameMap masks + HLOD palette) |
| Water planes | `_setup_water_material` |
| Env glass panes | `_setup_glass_env_material` |
| Simple / fallback | `_setup_simple_material` |
| Map / enemy decals | `_setup_map_decal_material` / `_setup_enemy_decal_material` |
| Weapon CR/NOM/Wear/EXX | `_setup_weapon_main_material` |
| Clothing | `setup_arc_texturer_material` + `ArcMaterial.cs` |
| Flat MI parse (+ parent / TwoSided) | `_parse_flat_mi_json` |
| Map Stage 2 entry | `setup_map_material` |
| White / SM-slot repair (no fuzzy invent) | `fix_white_unassigned_materials` / `infer_map_mi_candidates` (SM JSON + water/decal preferred only) |
| Material audit (unique broken types) | `audit_map_materials` / `arc.audit_map_materials` |
| Offline missing-texture audit | `audit_missing_textures.py` → `docs/MISSING_TEXTURES_AUDIT.md` |
| Texture load cache | `_load_image_cached` / `_SHARED_MI_MATERIALS` |

### Map Stage 2 / fuzzy repair rules

1. Prefer SM/SK `StaticMaterials` ObjectPath MIs (index-aligned) over folder fuzzy.
2. Never assign character / enemy / outfit Decal atlases (`MI_EnemyDecals_*`, `/Decals/Enemies/`, clothing) to ordinary env meshes.
3. Decal-family fuzzy only for DecalMesh / sticker assets (or an explicit decal family hint).
4. Partial SM assigns fill remaining white slots from SM JSON only — do **not** invent an MI from folder name similarity (`ENABLE_FUZZY_MI_INFER = False`).
5. Shared MI cache is keyed by MI JSON path; stale enemy-decal misbuilds of env trims (e.g. `ConcreteEdgeDecal` → `MI_Concrete_Trim_*`) rebuild on the next Stage 2 / Fix White.

---

## 6. Known gaps / non-goals (this pass)

- Full GRM → Metallic/Roughness channel split (masks used as blend factors first).
- Triplanar / world-aligned overlays (metal often authors `TriPlanar Overlay=true`).
- Vertex-colour destruction blend, VT blend, terrain height blend.
- Waterline, puddles, moss layers beyond Overlay slots.
- True height parallax from NOH.A on environment meshes.
- Cubemap reflection sampling on env glass (nodes left for inspection).
- Perfect foliage SSS / wind / trunk world-blend (SS stub + trunk nodes only).

These remain documented so a later pass can extend family handlers without re-cataloguing.
