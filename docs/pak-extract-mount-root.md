# Extract output root: CUE4Parse project name (2026-10-28)

## Symptom

`ArcPak.Export.exe extract` wrote the same request under `PioneerGame/Content/`
on some runs and under `RemappedPlugins/Content/` on others. The index and the
selective containers were the same every time. Repro: `SK_AgileAstronaut_Bags`
and `SM_Wrh_Computer_01_A`, with a fresh `--out` for each run. The shipped exe
wrote under the remapped root in 5 of 8 runs. A control build from current
source did so for the LUT set (15 `Lighting/LUTs/RGBTable16x1_*` strips and the
two cloud textures) in 6 of 6 runs.

Remapped runs:

- `MI_Wrh_Computer_01_A.json` drops 7 `Textures` entries (364 → 357 lines):
  `Pattern Atlas`, `Overlay`, `RGB Tint Mask Input`, `Mask`, `SignTexture`,
  `Frost Fresnel Mask Breakup` and `Snow Breakup Mask`. All of them are
  `/Game/Pioneer/MaterialLibrary/Textures/...` defaults. The `/Engine` defaults,
  the MI's own textures, the scalars and the colours stay.
  `MI_Bags_Bags.json` keeps every entry; only the order changes.
- The tree splits. The `.uemodel`, PNG, `MI_*.json`, `.arc-texture.json` and
  `.arc-material-slots.json` files go under `RemappedPlugins/Content/`. The
  `.arc-material.json`, `.arc-cloth.json` and `DA_VP_*.json` files that ArcPak
  writes itself stay under `PioneerGame/Content/`.
- The mesh geometry is the same. The `.uemodel` files differ only in the embedded
  object path.

## Cause

CUE4Parse's `AbstractFileProvider.FixPath` maps `/Game/...` to
`<ProjectName>/Content/...`. Exporter output paths and every soft object path
load go through it. `ProjectName` is computed on first use (`PostMount` →
`LoadIniConfigs`) and then cached. It takes the root of the first `.uproject`
key. If no `.uproject` is mounted, it takes the root of the first `Files.Keys`
entry whose root does not end in `Engine`.

- Arc's paks contain no `.uproject`, in a selective mount or in a full mount.
- `SubmitKeysAsync` mounts each container in its own `Task.Run`, and each
  container adds its file table to a `ConcurrentBag`. Every IoStore container
  has read order 3, so `Files.Keys` follows bag order. Bag order depends on
  thread scheduling.
- `pakchunk0-WindowsClient.utoc` is mounted at the root, and its first key is
  `RemappedPlugins/AIScript/Content/MLViewer/WBP_ObservationDebugWidget.uasset`.
  When that table is enumerated first, `ProjectName` becomes `RemappedPlugins`.

No container has a `RemappedPlugins/Content/` path. A full mount has 168
`RemappedPlugins` paths, all of them in pakchunk0:
`RemappedPlugins/AIScript/Content` (6) and `RemappedPlugins/EmbarkScript/Content`
(162). Every `RemappedPlugins/Content/` file in a cache came from this race.

Why the textures were lost: hard imports (`TextureParameterValues`) resolve by
package id, so they survive. Master-material defaults are
`CachedExpressionData.TextureValues`. These are `FSoftObjectPath` values, and
`UMaterialInterface.ParseCachedData` loads them by path. With the wrong project
name, a `/Game/...` path turns into `RemappedPlugins/Content/...`, which does
not exist, so the load fails and the parameter is skipped. `/Engine/...` paths
resolve correctly either way.

Ruled out:

- Index or container selection: the same 7 containers mounted on every run.
- The "1.45 cook-layout remaps" usmap step: it logs the same line on every run
  and only rewrites type layouts.
- The export session's 4 workers: once the project name is fixed, runs are
  byte-identical.

Full mounts (reindex, `material-metadata`) race the same way over 35
containers. They came out as `PioneerGame` in every test run, but nothing
guarantees that.

## Fix

`Program.CreateShell` creates every ArcPak provider, including the ones for the
`ArcVerbCommon` verbs. It now registers an empty
`PioneerGame/PioneerGame.uproject` (`StreamedGameFile`) before anything mounts.
`ProjectName` then finds the `.uproject` and is always `PioneerGame`.

- Nothing reads the entry.
- ArcPak code that walks `Files` filters on `FIoStoreEntry`, `.uasset`, `.umap`
  or shader extensions, so it skips the entry. The pak index skips it too.
- File counts in the logs are one higher.

`PakIndex.cs` is unchanged. Its `remappedplugins/content/` aliases still resolve
paths from old caches.

### Existing caches

Caches from older exporters can still hold `RemappedPlugins/Content/` trees
with incomplete `MI_*.json` files. `pak_extract._extraction_revision` is pinned
on purpose (it ignores exporter changes), so those stamps would still look
current. `pak_extract.mesh_cache_is_current` now reports any cached mesh under
`RemappedPlugins/Content/` as stale. The next import re-extracts it once, and
`_expected_uemodel` then picks the newer `PioneerGame/Content/` copy.
`material_metadata_plan` passes `allow_legacy_root=True`, so metadata warmup
does not fail for meshes already in a scene. Until the fixed exe ships, the old
exe can write under `RemappedPlugins` again, which costs an extra re-extract
for that mesh.

## Verification (scratch builds, `.local/mount-root-flip/`)

- Fixed build, 12 runs of `SK_AgileAstronaut_Bags` and `SM_Wrh_Computer_01_A`:
  every run wrote under `PioneerGame/Content/`. Each run wrote 110 files, with a
  364-line `MI_Wrh_Computer_01_A.json`, and all runs were byte-identical.
- A diagnostic build hit the race in 2 of 8 runs (pakchunk0 enumerated first).
  `ProjectName` stayed `PioneerGame` in both.
- The same binary with the fix switched off served as the control:
  - Its `PioneerGame` runs match the fixed runs byte for byte (110 of 110
    files, 4 runs).
  - Its remapped runs differ only in the 7 missing textures, root-path strings,
    `MI_Bags_Bags.json` key order, and the `.uemodel` size and hash recorded in
    the slot metadata.
- LUT set (17 textures): the 6 fixed runs are identical, and all 17 PNGs match
  the control. In the `.arc-texture.json` files, only `physical_package_path`
  changes, from `RemappedPlugins/Content/` to `PioneerGame/Content/`.
- `material-metadata` (full mount) and `mesh-metadata` (selective mount): the
  output is identical with the fix on or off.
- Reindex: the path-hash table is identical, and so are the package and
  stored-path key sets. Their containers differ only for duplicated packages,
  and they differ the same way between two control runs.

## Known, not changed

- Reindex depends on mount order for duplicated packages. `PakIndex.Write` keeps
  the first container it sees (`TryAdd`), and two control reindexes differed in
  2,454 package→container entries. Either container serves a duplicated
  package (see `pak-index-duplicate-paths.md`). The selective container set can
  therefore change from one index build to the next, but not between runs that
  use the same index.
- CUE4Parse has no way for a caller to set the project name. If it gains one,
  drop the `.uproject` entry.

## Shipping

`ArcPak/publish/ArcPak.Export.exe` is not republished. On the next version bump,
ship this together with the `PakIndex.TryDuplicated` change, through
`package_addon.ps1` and its Blender install gate.
