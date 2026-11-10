# FModel ↔ DataRaiders bridge

**Architecture:** FModel-Vibe owns export and push. DataRaiders-BlenderImporter
is a **thin receiver** plus in-Blender helpers (outfits, materials, manual
PSK/disk import). UMap placement remains a secondary two-stage path.

## Primary: Send models from Snooper (auto materials)

1. Enable the DataRaiders add-on in Blender (listener auto-starts).
2. Set **PioneerGame Root** in the Arc Raiders panel (textures / MI JSONs).
3. Open a StaticMesh / SkeletalMesh in FModel **Snooper**.
4. Click **Send to Blender** (World panel, next to Save All).

FModel exports loaded meshes as **PSK/PSKX** into `ModelDirectory`, then
pushes absolute paths over localhost TCP. Blender imports each path and
**immediately applies materials** via the same classification / setup used
by manual import (`import_psk_with_materials` → `apply_materials_to_object`
→ `setup_face/body/hair/weapon/clothing/misc/visor`). No outfit dialog.

- Host: `127.0.0.1`
- Port: **28563** (SurfBlender uses 28562 — keep separate)
- Framing: one JSON object + `\n`
- Command: `import_models`

```json
{
  "Command": "import_models",
  "MessageId": "...",
  "ProtocolVersion": 1,
  "Data": {
    "Paths": ["C:\\\\...\\\\foo.psk"],
    "ImportMode": "psk",
    "Source": "snooper"
  }
}
```

## Secondary: UMap placements (two-stage)

FModel can write DataRaiders-compatible CSV and push `placements_ready`
via **Export Placements** in Snooper (umap worlds).

```json
{
  "Command": "placements_ready",
  "MessageId": "...",
  "ProtocolVersion": 1,
  "Data": {
    "MapName": "FrozenTrail_01",
    "CsvPath": "C:\\...\\placements.csv",
    "ManifestPath": "C:\\...\\placements_manifest.json",
    "GameplayCsvPath": "C:\\...\\GameplayExtraction\\FrozenTrail_01\\gameplay_spawns_all.csv",
    "GameplayImportMode": "instanced",
    "ImportGameplay": true,
    "Count": 1234,
    "Units": "unreal_cm",
    "Axes": "passthrough",
    "ImportMode": "instanced"
  }
}
```

**Gameplay spawns (optional):** FModel does not extract gameplay JSON — pass a
pre-built `gameplay_spawns_all.csv` from the offline batch
(`map_tools/extract_gameplay_spawns.py`). When `ImportGameplay` is true (default
when `GameplayCsvPath` exists and scene toggle **Import gameplay on FModel push**
is on), Blender copies the CSV to the placement workspace and imports instanced
markers + optional `InGameMapPlane` after Stage 1 (or immediately for CSV-only
mode).

| Field | Purpose |
|-------|---------|
| `GameplayCsvPath` | Absolute path to `gameplay_spawns_all.csv` |
| `ImportGameplay` | Run gameplay import after placements ingest (default: on when path exists) |
| `GameplayImportMode` | `instanced` (default), `empties`, or `mesh` |

### Units and orientation (Blender)

- **CSV / FModel** stay in raw Unreal centimeters, axes pass-through.
- **Blender import** applies `MAP_UNIT_SCALE = 0.01` (scene prop `arc_map_unit_scale`)
  so **1 BU = 1 m**. Mesh imports use the same factor (UEFormat `scale_factor`).
- **Y-mirror** (`arc_map_mirror_y`, default on): negate Unreal Y so Blender
  top-down matches **in-game map UI** (not the Unreal editor). Buried City
  check: dual tracks / rail spine at **bottom-left**, bridge running
  left→right angling up. Without the mirror, tracks sit top-left.
- Pose math: `(x, -y, z) * unit`, euler `(ex,ey,ez)→(-ex,ey,-ez)`, scale
  `sy→-sy` (Instance-on-Points absorbs the reflection).
- Existing cm scenes: **Fix Map Orientation** then **Scale Map to Meters**
  (idempotent via `arc_map_orientation` / `arc_map_unit_scale` tags).
- No FModel re-export needed for orientation or unit scale.

Auto-exported UEModels land under the same MapPlacements folder as the CSV. The
manifest lists `mesh_exports` / `mesh_by_asset`; DataRaiders prefers those paths
(and the CSV folder) over a full PioneerGame tree when present.

### Stage 1 — Import Map Geometry

`Stage 1` (`arc.import_placement_instanced` Fast, or meshes Slow) loads
UEModel/PSK geometry. **No per-object material construction** runs here.

After geometry is built, Stage 1 **automatically** (Fast, Slow, and FModel TCP
receive → Stage 1):
- Groups foliage (Trees/Vines/Bushes/Grass/Overgrowth/Other), Light Modifiers,
  Skybox/Spheres, Debris Tiles, Helpers (occluders / collision / backdrop — hidden)
- **Hides Skybox/Spheres** by default
- Shows available `SM_Landscape_*` WP tiles (vertex height baked); hides backdrop islands
- Dedupes landscape instance points to the WP grid corner (fixes ``…_H_LOD1_x2`` doubles)
- Reports cooked holes (``*_missing_landscape_cells.json``) and **removes** any legacy
  GapFill planes — never invents geometry for missing cells (Buried City ``x1_y4``)
- Skips/hides the flat `CityGroundPlane` when WP landscape tiles exist (distant PNG is
  not city ground). Full lattice needs FModel inject of all map-scoped SM_Landscape packages
- Frames the 3D view to the **city-core AABB center**

**Naming:** ``SM_Landscape_x2_y3_H_LOD1_x2`` means stem + ``_x{placement_count}`` — not LOD2.

Grouping matches `arc_map` with `_P` aliases so CSV-folder vs dropdown name
mismatches no longer skip organization.

Stamped custom properties on mesh objects:

| Prop | Purpose |
|------|---------|
| `arc_map` | Map name |
| `arc_asset_path` | Unreal asset path from CSV |
| `arc_psk_path` / `arc_mesh_file` | Absolute PSK/PSKX path |
| `arc_model_type` | Stage 1 stamps `map`; Stage 2 materials ignore outfit types |
| `arc_materials_pending` | `1` until Stage 2 finishes |
| `arc_actor_name` | Placement actor name |

### Stage 2 — Apply Map Materials

`Stage 2: Apply Map Materials` (`arc.apply_map_materials`) is a batched
modal (ESC cancels). It walks Stage 1 meshes (current map, or selection)
and applies materials **in place** via `apply_materials_to_object` /
`fix_materials_for_object`. Shared PSK paths reuse the first-built
Material datablocks so duplicate instances do not rebuild shaders.

CSV values are **raw Unreal centimeters**, axes pass-through.

## Backup / manual

- Import Single PSK / Import Folder (disk) — same material pipeline
- Advanced: empties-only, map dropdown → Extract Placements, Import Last FModel Export
- Materials → Update Materials (selected / all scene meshes)
- Map “fix scene” helper buttons were removed — **re-export from FModel** and
  re-run Stage 1 instead

### Landscape re-export (FModel)

**Proper ground (Buried City / Arc maps):**
1. **City `LandscapeStreamingProxy` heightmap** — real `LandscapeComponent` +
   `HeightmapTexture` data in `BuriedCity_01_P/_Generated_/`. Covers the whole
   playable core including **`x1_y4`** (no `SM_Landscape_x1_y4_H_LOD1` package in
   Steam/paks, but 64/64 landscape components tile that cell). After the FModel
   fix, `{Map}_heightmap.png` must be `city_aligned: true` (not the old distant
   Y≈982800 proxy from double-counted transforms).
2. **`SM_Landscape_*_H_LOD1`** — WP section LOD meshes where packages exist
   (approximate). Missing H_LOD1 ≠ missing ground.
3. Streets/sand/floors — prop meshes on top. `DA_*_HierarchicalHeightMap` — nav/AI.

Blender:
- Displaces the city ground plane when heightmap is `city_aligned: true`
- Shows package-backed `SM_Landscape_*` tiles; hides backdrop islands
- Documents missing H_LOD1 cells; strips legacy GapFill (never invent geometry)

**Heightmap-only refresh (no Stage 1 / no mesh reimport):**

1. **Fastest (already have a PNG):**  
   `python map_tools/prepare_blender_heightmap.py --map-dir "…/MapPlacements/BuriedCity_01_P"`  
   → rewrites `{Map}_heightmap.png` + `*_landscape_bounds.json` (crop/flip/fill).
2. **From FModel (needs umap loaded):** right-click umap → **Export Landscape Heightmap**  
   (no mesh export, no CSV rewrite).
3. **In Blender:** Arc Raiders → **Reload Heightmap Only**  
   (or F3 → `arc.reload_placement_heightmap`). Swaps CityGroundPlane displace only.

**Full re-export:** Rebuild FModel-Vibe → **Export Map Placements + Meshes** on
`BuriedCity_01_P` → confirm `{Map}_landscape_bounds.json` has
`city_aligned: true`, `image_row0_is_max_y: true`, and location near city
(e.g. Y≈420k after city crop — not the padded full-proxy AABB, and not the old
distant Y≈982800) → Blender Stage 1 Fast.

Heightmap PNG is 16-bit grayscale, cropped to the city AABB, vertically flipped
so Blender `mirror_y` UVs match placements, with unwritten holes filled to mid
height (32768).

## Units / axes (placements)

- CSV values are **raw Unreal centimeters**.
- Axes are **pass-through** (X, Y, Z) — no Y-flip.
- In Blender: **1 Blender unit = 1 Unreal cm** (same as PSK import at scale 1.0).

## Outfit import (`import_outfit`)

FModel **Outfit Composer** resolves DA_OI parts + colorways, exports PSK once
per part, and pushes a manifest. Blender is a thin receiver — no outfit
dialog, no DA_OI folder rescan.

```json
{
  "Command": "import_outfit",
  "MessageId": "...",
  "ProtocolVersion": 1,
  "Data": {
    "OutfitName": "ID_…",
    "DisplayName": "Flavour (Model)",
    "Units": "unreal_cm",
    "Axes": "passthrough",
    "Colorways": [
      {
        "Name": "Default",
        "CollectionName": "Outfit_Default",
        "Parts": [
          {
            "PartKey": "Celeste/Jacket",
            "PartName": "Jacket",
            "AssetPath": "/Game/…",
            "PskPath": "C:\\\\…\\\\Jacket.psk",
            "SkinJsonPath": "C:\\\\…\\\\MI_….json",
            "ModelType": "clothing"
          }
        ]
      }
    ]
  }
}
```

- One Blender collection per selected colorway
- Explicit `SkinJsonPath` → `apply_materials_to_object` (no rediscovery)
- Shared material datablocks reused across colorways when `(PskPath, SkinJsonPath)` match
- Bulk colorways laid out along **+X** (same spacing as the offline outfit importer)
- Manual Single PSK / Folder import remains available as backup

Bridge `import_models` still never opens the outfit dialog (single-model auto materials).

## Animations (on-demand PSA)

Snooper **Send to Blender** also exports any animations currently loaded on the
timeline as **PSA** into `ModelDirectory/animations/`, writes a
`{name}.notifies.json` sidecar, and pushes `import_animation`.

Blender applies **one** Action to the selected armature (replacing the previous
applied Action by default) instead of importing the whole animation library.

```json
{
  "Command": "import_animation",
  "MessageId": "...",
  "ProtocolVersion": 1,
  "Data": {
    "PsaPath": "C:\\\\...\\\\AS_Character_Emote.psa",
    "NotifyPath": "C:\\\\...\\\\AS_Character_Emote.notifies.json",
    "AnimName": "AS_Character_Emote",
    "ReplaceAction": true,
    "SpawnNotifies": true,
    "Source": "snooper"
  }
}
```

Notify sidecar `kind` values:

| kind | Blender |
|------|---------|
| `prop` | Import `SkeletalMeshProp` / `StaticMeshProp` PSK and bone-parent to `socket` |
| `fx` | Empty marker named after the Niagara / particle template |
| `other` | Skipped unless **Spawn Other Notifies** is on |

The N-panel **Animations** picker lists FMDex `AnimSequence` / `AnimMontage`
names without importing them. Apply looks up a cached `.psa` (Animation Cache
folder, typically FModel `Save → animations`) or uses the TCP path above.

