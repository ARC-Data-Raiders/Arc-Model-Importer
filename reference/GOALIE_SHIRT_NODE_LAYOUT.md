# GoalieShirt NodeConnectionTest layout

Captured from `NodeConnectionTest-1.blend` → object `SK_Goalie_Shirt.001` → material `SK_Goalie_Shirt.001_Mat`.

Full dump: [`goalie_shirt_nodeconnectiontest_layout.json`](goalie_shirt_nodeconnectiontest_layout.json)

## What the clothing builder replicates

- ColorMask_XYZ per zone → `Colour N` / Rough / Metal on **Arc Texturer**
- TA mask/normal slices wired as authored **Color** (no RGB split into separate images)
- Decal UV → dual Mapping → Color (+ Data) textures → colour ramps → Arc sockets
- Layered PBR Mix Shader stack for decals that carry DecalData roughness/metal

## Internalized into ArcTexturer (2.18.26+)

| Was outside (NCT clutter) | Now |
| --- | --- |
| `Base Overlay ↔ XYZ zone N` Mix | `Overlay N` + `Overlay Fac N` inputs; Mix hidden inside Arc Texturer |
| `Decal N LayerMask × Alpha` Multiply | `Decal LayerGate N` × `Decal Alpha N` Multiply hidden inside Arc Texturer |
| Inline Nx/Ny/Nz / Normal RG+Z chain | **Decal Data** group (see below) |

LayerMask **ColorRamp** stays outside (per-material bitmask); its Alpha feeds `Decal LayerGate N`.

## Decal Data channels

UE DecalData / `*_M` textures pack:

| Channel | Meaning |
| --- | --- |
| **R / G** | Tangent-space normal X/Y in 0–1 (`Nx = 2R−1`, `Ny = 2G−1`) |
| **Nz** | Reconstructed as `sqrt(1 − Nx² − Ny²)` so the normal is unit length |
| **Normal RG+Z** | Packed back to 0–1 as `(R, G, Nz*0.5+0.5)` for ArcTexturer `DN N` |
| **B (Roughness)** | Decal roughness for the layered Principled sticker |
| **A (Metallic)** | Decal metallic / specular weight |

Can we simplify further? The math is already the minimum for RG-packed normals. The **Decal Data** group is that simplification for the material graph: one node instead of Separate + 8 Math + Combine. Nx/Ny/Nz outputs remain for debugging; clothing wiring only needs **Normal**, **Roughness**, **Metallic**.

## How to verify GoalieShirt ≈ NodeConnectionTest

1. Install/sync addon **2.18.26+** (includes updated `ArcTexturer.blend`).
2. Import Goalie Shirt outfit (clothing path on `pre-map-importer` / `outfits-stable`).
3. Open the generated `*_Mat` node tree and check:
   - No external `Base Overlay ↔ XYZ` Mix nodes; RGB overlays plug into Arc `Overlay N`.
   - Decal rows use a **Decal Data** group into `DN N` (not a long Math chain).
   - LayerMask ramps feed `Decal LayerGate N`; raw texture Alpha → `Decal Alpha N`.
   - TA `*_Normals_*` / mask slices connect **Color** whole (no Separate RGB into Mix).
4. Side-by-side with NCT: same decal placement, zone tints, and sticker PBR read.
