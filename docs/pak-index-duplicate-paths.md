# Pak index: packages cooked into two containers (2026-10-28)

## Symptom

Lighting Look could not fetch two cloud-layer textures (10 scenarios fell back
to a clear sky):

- `PioneerGame/Content/Pioneer/Lighting/HDRI/hdri_sky_072_8K_Sphere`
- `PioneerGame/Content/Pioneer/Lighting/Materials/Lightfunctions/T_CloudsCirrus_01_M`

`ArcPak.Export.exe extract` logged `No mesh or colourway package under …`,
then `load failed … project=` and `Nothing queued.` It failed only once a
current-build index existed: the first run after a game update reindexes with a
full mount and succeeds.

## Cause

`PakIndex.Write` → `AddPath` stores one container per path hash (and its
`game/`, `pioneergame/content/`, `remappedplugins/content/` aliases). When the
same path appears in a second container, it replaces the entry with the
sentinel `PathHit(ushort.MaxValue, 0)`. `TryExact` treated that sentinel as
"not found". Mesh and colourway packages still resolved through the stored-path
list (`AppendMeshes`), but textures and other unlisted packages ended up with
nothing prepared. `OpenProvider` then returned an unmounted shell, so
`FindPackage` had nothing to load. Build 26159202's index has 13,875 sentinel
hashes. Both cloud textures are among them.

## Fix

`PakIndex.TryExact` → `TryDuplicated`: for a `/Game` path (`PioneerGame/Content/`
or `Game/`) whose hash is the sentinel, derive `FPackageId.FromName("/Game/…")`.
If that id is in the package→container map, use the container from the map.
A duplicated path has the same package id in both containers, so either one
serves it. The fix works with existing format-6 indexes, so there is no index
version bump and no forced reindex. Explicit `RemappedPlugins/Content/` requests
and genuine path collisions that have no indexed `/Game` id behave as before.

## Verification (scratch build, `.local/pak-dup-fix/`)

- Both textures extract on a selective mount (`global.utoc`,
  `pakchunk13-WindowsClient.utoc`, about 1 s). The PNGs are byte-identical to a
  full-mount extraction.
- The 15 `Lighting/LUTs/RGBTable16x1_*` strips: PNGs byte-identical to the
  shipped exe and to a control build from the same source without the fix.
  Only the `.arc-texture.json` provenance hashes differ.
- Mesh smoke test (`SK_AgileAstronaut_Bags` and `SM_Wrh_Computer_01_A`): exit 0,
  the same selective containers, the same 46 queued items and the same 110
  files. Every PNG matches the control build.

## Known, pre-existing (not caused by this fix)

Output root flips between `PioneerGame/Content/` and `RemappedPlugins/Content/`
from run to run, in the control build too (3 of 8 runs). Runs written under the
RemappedPlugins root produce an `MI_*.json` that is missing 7 lines of
`/Game/...` texture-parameter entries. Consumers that look up both roots (for
example `lighting_catalog._candidates`) tolerate the root change. The JSON
difference is worth its own investigation.

## Shipping

`ArcPak/publish/ArcPak.Export.exe` is not republished here. Ship the fix through
`package_addon.ps1` and its Blender install gate on the next version bump.
