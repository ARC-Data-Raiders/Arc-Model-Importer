# Draft: import from Arc Raiders 5.7 paks

Standalone FModel keeps the full CUE4Parse tree (legacy builds, other games, audio). The Blender addon gets a second build of the same sources, `ARC_ONLY`, plus a small exe the Python addon starts. Nothing in this draft deletes `GAME_UE4_*` branches inside shared serializers. Those are not separate files.

## What the addon calls

`ArcPak.Export.exe`, self-contained `win-x64`, shipped inside the addon zip.

```
ArcPak.Export.exe extract ^
  --game-dir "<PioneerGame\Content\Paks>" ^
  --usmap "<bundled or settings override>" ^
  --out "<cache>" ^
  --path "/Game/Pioneer/Items/Firearms/.../SM_Foo"
```

Fixed inside the exe, no flags:

- `EGame.GAME_ArcRaiders` (`GAME_UE5_7 + 6`)
- AES from the existing `TheiaDecryptor` Arc constant. No settings field. A key change is a plugin hotfix.
- `TheiaFileProvider` (shipping IoStore). If the install has `oo2core_9_win64.dll` or `oodle-data-shared.dll`, that file is loaded. The current shipping client links Oodle into the game exe, so the helper downloads the OodleUE build beside itself on first extract. That dll is not stored in git.

Usmap: the addon zip contains the current merged usmap. Scene settings may point at a newer file. Empty path means the bundled file.

The exe writes texture PNG, material JSON, and `.uemodel`. Python imports the mesh with `uemodel_import.py` (already in the addon, no ActorX addon) and runs the current material path against the cache.

`.uemodel` is CUE4Parse's own mesh dump, so the helper skips the ActorX translation and the PSK scale quirks. Each material slot in the file carries the cooked object path, which is the list of MIs to extract next. `.psk` stays the on-disk fallback for an unpacked Pioneer tree. The pak path does not emit it.

`uemodel_import.py` currently reads vertices, indices, UVs, and material slots, and skips `WEIGHTS`. Static meshes (guns, props, map pieces) are covered. A skinned outfit or body still needs the reader to apply weights and the skeleton before it can replace PSK for those parts. PSA animation stays PSA either way.

## Projects

Live in this repo under `ArcPak/`. They compile the sibling `FModel-Vibe/CUE4Parse` sources with `ARC_ONLY` (no copied fork):

| Project | Role |
|---|---|
| `CUE4Parse.Arc` | Same `CUE4Parse` sources, `DefineConstants` includes `ARC_ONLY`, default compile off, explicit includes below |
| `CUE4Parse-Conversion.Arc` | Same idea if Conversion fails to build without audio types |
| `ArcPak.Export` | Exe. Mount, extract one path or a list, exit 0. Modeled on `CUE4Parse.Example` `CreateProvider` |

`CUE4Parse.csproj` does not define `ARC_ONLY`. FModel's build stays as it is.

`CUE4Parse.Arc.csproj` sketch:

- `EnableDefaultCompileItems` = false
- `Compile` `..\CUE4Parse\**\*.cs` with the excludes in the next sections
- `Compile` back the three GameTypes trees that shipping Arc uses
- Drop package refs `Fmod5Sharp` and `VGAudio`
- Drop embedded resources that exist only for other games (`ACE7Key.bin`, `PUBGNameHashMap.json`, `BL4SoundTagsMap.json`, `ProSpiDecryptor.dll.gz`)

## Keep these GameTypes

| Folder | Why |
|---|---|
| `GameTypes/ArcRaiders` | `ArcRaidersUsmapFixes`, already used by `UsmapTypeMappingsProvider` |
| `GameTypes/Embark` | `EmbarkAes.EmbarkDecrypt` for the IoStore TOC. Without it the mount returns no containers |
| `GameTypes/Theia` | `TheiaFileProvider`, TOC decrypt, AES constant |

Every other directory under `GameTypes/` is excluded. That is the identifiable other-game code (Fortnite, PUBG, FF7, Borderlands, Valorant, and the rest).

## Drop audio

Exclude these trees from `CUE4Parse.Arc`:

- `UE4/Wwise/**`
- `UE4/FMod/**`
- `UE4/CriWare/**`
- `UE4/Assets/Exports/Wwise/**`
- `UE4/Assets/Exports/Sound/**`
- `UE4/Assets/Exports/MetaSound/**`

Any `using` of those namespaces in a file that is still compiled gets `#if !ARC_ONLY`.

## Shared files that must grow `#if !ARC_ONLY`

Excluding `GameTypes` does not compile until the `using` lines and `switch` arms that name those types are wrapped. FModel never defines `ARC_ONLY`, so those arms stay in the standalone build.

Files outside `GameTypes/` that import other games today:

- `JsonConverters.cs` (FF7, FN)
- `FileProvider/AbstractFileProvider.cs` (RL)
- `FileProvider/InternationalizationDictionary.cs` (Aion2)
- `FileProvider/Vfs/AbstractVfsFileProvider.cs` (per-game AES). Keep the `EmbarkAes` arm. Wrap every other game arm.
- `UE4/IO/IoStoreReader.cs` (`ProSpi` only)
- `UE4/Pak/PakFileReader.cs`, `UE4/Pak/Objects/FPakInfo.cs`, `UE4/Pak/Objects/FPakEntry.cs`
- `UE4/Assets/Package.cs`
- `UE4/Assets/Readers/FKismetArchive.cs`
- `UE4/Assets/Objects/UScriptArray.cs`, `UScriptMap.cs`, `UScriptSet.cs`
- `UE4/Assets/Objects/FScriptStruct.cs`, `FInstancedStruct.cs`
- `UE4/Assets/Objects/Properties/StrProperty.cs`, `EnumProperty.cs`, `BoolProperty.cs`, `FPropertyTagType.cs`
- `UE4/Objects/UObject/FSoftObjectPath.cs`, `FField.cs`
- `UE4/Objects/UObject/BlueprintDecompiler/BlueprintDecompilerUtils.cs`
- `UE4/Objects/Core/i18N/FText.cs`
- `UE4/Localization/FTextLocalizationResource.cs`
- `UE4/Objects/Meshes/FPositionVertexBuffer.cs`
- `UE4/Assets/Exports/StaticMesh/FStaticMeshLODResources.cs`, `FStaticMeshRenderData.cs`
- `UE4/Assets/Exports/SkeletalMesh/USkeletalMesh.cs`, `FStaticLODModel.cs`
- `UE4/Assets/Exports/Material/UMaterialInstance.cs`
- `UE4/Assets/Exports/Animation/UFaceFXAnimSet.cs`, `FMorphTargetLODModel.cs`
- `UE4/Assets/Exports/Internationalization/UStringTable.cs`, `FStringTable.cs`
- `UE4/Assets/Exports/CustomizableObject/Mutable/FProgram.cs` (LotF)

`MappingsProvider/Usmap/UsmapTypeMappingsProvider.cs` stays pointed at `GameTypes.ArcRaiders.Mappings`.

Pattern for each arm:

```csharp
#if !ARC_ONLY
            GAME_SomeOtherGame => SomeOtherDecrypt,
#endif
            GAME_ArcRaiders or GAME_ArcRaiders_UE53 or GAME_ArcRaiders_TT1
                or GAME_ArcRaiders_Alpha or GAME_ArcRaidersTencent or GAME_TheFinals
                => EmbarkAes.EmbarkDecrypt,
```

The Arc 5.7 case is not wrapped. Legacy enum values can stay in the `enum` so the shared file still compiles for FModel. The exe never selects them.

## Blender side

Scene settings next to `arc_pioneer_root` in `properties.py`:

| Property | Meaning |
|---|---|
| `arc_pak_dir` | `PioneerGame/Content/Paks`. On first open this is the Steam install when that folder contains `pakchunk*.utoc`. If it does not, the field stays empty. |
| `arc_usmap_path` | Optional newer usmap. Empty uses the file shipped in the addon |

No AES property.

Operator, new module `pak_extract.py`:

1. Resolve the exe under the addon folder (`ArcPak/ArcPak.Export.exe`).
2. Resolve usmap: settings path if set and present, else `ArcPak/mappings/<bundled>.usmap`.
3. Run the exe with the object paths for the current import (one mesh, or the set a gun/outfit needs).
4. Point the existing PSK / MI / texture lookup at `--out` for that call.
5. Leave `arc_pioneer_root` working as it does now so an unpacked tree still imports with the exe absent.

`package_addon.ps1` copies `ArcPak/` into the zip (exe, bundled usmap). It does not copy the C# sources.

The exe is published self-contained so enabling the addon does not require a .NET install. One process per extract.

## Pak index

`ArcPak/mappings/pak-index.bin` maps each package path to the `.utoc` that contains it. Extract mounts `global.utoc`, the asset's container, and the IoStore import closure (materials, then the packages those import). It does not mount the other chunks.

Each container is stored as file name, length, and a hash of the `.utoc` bytes. The index also stores the Steam `buildid` and content-depot manifest id from `appmanifest_1808500.acf`.

If those hashes do not match the installed `.utoc` files, `reindex` mounts every container, reads that build id, and writes `ArcPak/cache/indexes/pak-index-<buildid>.bin`. The next import of the same build uses that file and does not mount every container. Settings has a Reindex button that runs the same command.

A path that is a folder (`.../Characters/Assets/Abyss/Airbag`) is not a package. The index stores `SK_` / `SM_` and `DA_OI_` paths so that folder resolves to the meshes and outfit colourway assets inside it and mounts only those containers. A folder miss does not rebuild the index when the build hashes already match and the index already lists colourway assets. An older index is rebuilt once. Colourway JSON is the full package document, and the material instances those colourways name are extracted into the pak cache so the outfit selector works with no Pioneer root.

The same index pass stores `T_UI_Icon_` preview textures under `Items/Characters/Skins` and writes their PNGs into the pak cache. Sidebar previews use that cache when the Pioneer root is empty. An index from before that list is rebuilt once. Outfit icons from that cache are cropped the same way as the shipped portraits: a 326px square from the top of the 512px icon, centered on the opaque pixels, resized to 256. The full icon stays in the cache for the reference card.

BC7 item maps (CR, NOM, ColorMask) need Detex. The helper writes `Detex.dll` beside itself before the first texture export. A mesh extract also writes every `T_` texture in that mesh's folder, plus `Hair_` / `Beard_` card atlases, so a normal or color mask still exports when the material instance only referenced the diffuse. Some containers store the same asset under `RemappedPlugins/` instead of `PioneerGame/`; the importer accepts that output.

Attachment, charm, raider tool, and backpack dropdowns use the string-table flavour when the localization JSON is in the Pioneer folder or the pak cache. A name with no flavour drops a trailing index when that shorter name is unique. `10 Mil Tool` keeps its leading number. Gear imported with no outfit in the scene is parented to the default strap skeleton (`slot_container`, `slot_frame`, `slot_backpackattachment`, `slot_backpackcharm`, `ToolStowSlot_1`) instead of the world origin. Those shells use their CR or BaseColor texture directly.

Appearance, outfit, weapon, and weapon-mod imports use that extract when Pioneer root is empty and the paks folder is set. FModel Bridge sits in the Advanced section. The FMDex folder picker is not in Settings.

`ArcPak.Export reindex --game-dir <Paks> --index-dir <dir> --out <cache>` rebuilds the map after a game update and caches preview PNGs. The AES key is not in the file.

## Build order

1. `CUE4Parse.Arc` compiles with the excludes and `#if !ARC_ONLY` wraps. FModel's `CUE4Parse` still builds without those defines.
2. `ArcPak.Export` mounts a local 5.7 pak dir and writes one static mesh as `.uemodel` plus its textures.
3. Blender operator runs that exe, imports the `.uemodel`, and applies the current material path. Skinned meshes stay on PSK until `WEIGHTS` are read.
4. Self-contained publish, then `package_addon.ps1` picks up `ArcPak/`.

## Out of scope

- UE 5.3, TT1, Tencent, Alpha. Those stay on standalone FModel.
- Deleting `GAME_UE4_*` branches inside mesh and property readers.
- Audio banks.
- A usmap editor. Settings only select a file.


## Mesh browser and exact outfit catalog (2.19.10)

Import from Pak now opens Blender's file browser. Load/Refresh Paks creates
empty .uasset selectors under ArcPak/cache/browser/<build>-<index-stamp>/,
mirroring the indexed mesh paths. Select meshes or a folder (up to128 per
import); extraction remains on demand. Reopening an unchanged tree reuses it.
The browser validates build/container identity and rejects selections outside
the current catalog. Windows long package paths are supported.

Outfits resolve exact live mesh paths and current DA_OI shared-part references.
Both normal and RemappedPlugins cache mounts are collected, fixing Abyss and
other successful extractions that previously appeared empty. The reference CSV
records exact visual-part/mesh paths and build availability. Own-model meshes
queue before borrowed pieces. Stable enum IDs synchronize list, Browse All,
preview and import; Abyss is first/default. String-only uncooked entries report
their availability explicitly. Display labels are never mesh identities.

Accessory materials use authored MI slots, masks, channel packing and material
families before basename fallbacks. The ticket artwork, body alpha .01, proper
UEModel bone reflection and shared charm connector are covered by the 2.19.10
regression reports under dist/.local. Shared docs sections/blender-import-paths.md
and sections/backpack-frame-slots.md record the detailed evidence and limits.

## Material fidelity (2.19.11)

Array slices support both ArcPak `_LAYERN` and FModel `_N` filenames. This
restores clothing roughness masks and detail normals without changing the
authored roughness math or working decal placement. Exact inherited array
references can span material folders, including Goalie HelmetUp's shared
Helmet/Plastic bank. Old preview-only helmet stamps repair from the mesh's
DA_VP MaterialOverrides; no similar-name donor is used.

Packed prop materials honor disabled tint/overlay stages and their authored
roughness exponent and NOM/NOX/XEM channels. Existing environment graphs
repair through Update Materials. Hair Color extracts its selected palette MI
on demand, applies it at import/update and supports resetting to From style MI.
Detailed evidence and limits are in the shared blender-import-paths section.

## Gender shape inheritance and body coverage (2.19.12)

Inherit Shape Key is enabled by default below Offset Characters. Body imports
apply their authored gender morph; outfit male/female keys follow the body on
the same character, in either import order. Turning inheritance off releases
the importer drivers for manual outfit editing. Saved-file loading and body
replacement reconcile the association without mixing character pads. Secondary
single-body imports stamp the mesh and armature as Body to share the outfit pad.

ArcPak now exports morph targets, the native UEModel reader imports them, and
PSK shape-key import is enabled. Cache revision 2.19.12 refreshes older exports.
Body material updates preserve topology and live morph values. Existing meshes
hydrate missing morphs only with verified correspondence, including old
coincident-welded bodies with matching faces and equal authored deltas.

Gender fit is supplemented by the game's exact Body material templates and
UV1 region coverage, plus Body.Hide/Show rules. Coverage multiplies the existing
skin alpha without changing geometry. Goalie retains its authored exposed
left-hand regions; Abyss covers the whole shared body. Ambiguous definitions
produce diagnostics. The shared character-cosmetic-clipping section records
the decoded equations, old-dump correction, validation evidence and limits.


## Add All appearance queues (2.19.13)

Each Add Body, Head, Hair, Backpack, Attachment, Charm and Raider Tool button
has an Add All button immediately below it. Hair includes facial styles;
Bodies includes the four skin/gender variants. Every entry gets a separate
character slot, with replacement and attachment to selected outfits disabled
only for that entry. Existing choices and import settings are restored.

Queues process at most four entries per batch. Missing Pak exports run in a
worker subprocess; Blender mesh, material and reference work stays on the main
thread. All four entries finish or report failure before the next batch starts.
The same import/material paths serve single imports and exported-file mode.
Each gear entry gets independent support geometry/rigs; early slot stamps
prevent new heads from changing existing bodies. Exact mount aliases resolve
cached meshes without redundant extraction. Cancel retains completed entries,
waits for an active extraction to finish and prevents subsequent batches.
Failed entries roll back only their new objects; the final UI reports failures.
Changing source or loading another blend cancels the active queue.

The older Pak index omits Head_* entries, so authored head paths are attempted
rather than silently removed. Missing uncooked entries (Celeste Cin observed)
report their exact path and do not stop the queue. No new extraction-cache
revision is needed; morph revision remains 2.19.12.


## Shared type slots, rows, reference recovery and backpack variants (2.19.14)

Game evidence: skeletal Hiking/Duffelbag/Seasack/etc. vertices already contain
character component-space height; static containers are socket-local. Placement
uses target socket times inverse authored source socket bind for skeletal gear,
without adding height twice. DA_OI Parts resolve every DA_VP Mesh; Survival has
Backpack and Props, Barrel also has Extras. Color options use exact DA_OI
CustomizationVisualPartSetMaterialModifier Part and MaterialSlot bindings;
Default follows the Color setting's DefaultItemQuery. Selecting a single
section updates its existing same-host companions rather than duplicating them.

Normal Hair and Facial Hair have independent selectors, Add and Add All buttons,
occupancy and replacement roles. Saved normal enum numbers remain stable; old
facial selections migrate to the separate selector. Source hair sections dispatch
strands and opaque ties separately; static Buzzcut uses a verified head rest-bone
frame. See appearance-hair-head-textures.md for shader and attachment evidence.

Each type fills its first available slot starting at zero, sharing pads with
other types. Ten columns wrap into positive Y rows. Defaults are X=2m, Y=8m;
both controls sit beside Offset Characters. Four-entry batch completion includes
all mesh/material/reference work, including a second worker extraction phase for
newly resolved authored materials. Blender work stays on its main thread.

Arc Reference Planes contains children per type. Cards follow their assets,
including recovered loose legacy cards during explicit scene reflow. Imported
cards preserve their orientation when static hair receives a head attachment.
Exact indexed UI packages recover missing PNGs at import time; sidebar drawing
stays read-only. Direct UTexture export now supports cold caches without a full
preview reindex. Folder and filename suffixes are independent (TechBag folder,
T_UI_Icon_BackpackContainer_TechBag_A filename). ShortTidy's exact icon is the
DA-proven MoustacheRegular. Canonical and RemappedPlugins mounts and extended
Windows paths are accepted for meshes, images, icons and cache metadata sidecars.

Known limit: Survival Props' lantern remains bound to its own MI_Props_Lantern.
The simple fallback does not reconstruct its authored procedural lantern effect;
Intensity alone is insufficient evidence for inventing uniform emission.

Final 2.19.14 live verification: the original185-object scene is repaired to240
objects, recovering allsix failed backpack entries plus the missing Survival bag
and Barrel Extras. There are28Backpack,18Head,26Hair and5FacialHair cards (77).
All163original meshes retain identical vertex/face hashes; all48original cards
retain their world orientation. Shared slots begin atzero for each type, settings,
selection, otheraddons andtheoriginalfilepath arepreserved. LiveMCPpasses288
checks; the integrated private scene repair passes105. Before/after scene copies
are dist/.local/scene-live-before-21914.blend andscene-live-repaired-21914.blend.

The final real-AppData path test also covers Basket's269-character Wood_Red MI:
folder ranking must normalize each joined child, even when the folder itself is
short. Default/Red imports and the layered Red graph pass. Blender-relative //
paths and native UNC paths remain valid. Four colorway-enum checks verify that
explicit options retain stable numeric identities before/after default metadata
loads; Game Default remains distinct from explicitly named variants. Final ZIP
runtime/source/installed payload checks pass, alongside release GUI queue checks.

## Collection organization and asset names (2.19.15)

Group by Asset Type and Group by Outfit Slot organize imported meshes, rigs and
reference cards under Arc Assets. Type groups contain matching reference
subcollections; slot groups contain all types on that numbered character slot
plus its reference cards. Slot labels start at001 while the internal slot IDs
remain zero-based. Props without a character slot use Unassigned Assets.
Regrouping changes collections only after the initial legacy name upgrade;
subsequent switches preserve custom display names. New imports follow the last
selected scene grouping mode, with Asset Type as the default. Both buttons are
disabled while a bulk queue is active. The queue imports through the scene root
so removing an emptied grouping collection cannot invalidate its context.

Names start with the asset type: SK_CurlyShort becomes Hair_CurlyShort and
Head_Face_20_Pio_Base becomes Head_Face_20. Frames, straps and facial hair retain
independent type prefixes. Rigs/pivots/cards receive Rig/Pivot/Reference suffixes.
Original names are kept in arc_source_object_name / arc_source_data_name and
original source file paths stay intact. Material repair includes the source
identity; reference-anchor and body-inheritance strings, scene animation/weapon
targets and queued object-name lists are updated. Bones and material identities
are unchanged. Shared mesh data is not renamed. Empty former collection trees
are removed while unrelated objects and shared-scene collection links remain.

Verification:1220 checks across all240actualsceneobjects andanewCurlyShort
import;12driver/shared-data/legacy-tree/custom-name/queuepoll checks;62existing
Pak regressions;ZIP install/overwrite/disable/re-enable and16packaged GUI queue
checks. LiveMCP verifies806checks across240objects with geometry,transforms,
materials,parenting,visibility,selection andsourcepaths unchanged. Current queue
summary is28/28backpacks with0failures; the supplied screenshot showedtheearlier
summary. Backup:dist/.local/scene-live-before-21915.blend; organized copy:
dist/.local/scene-live-organized-21915.blend. Reports:dist/.local/*21915*.


## 2.19.16 — Source codes and sidebar order

Game: SK_CurlyShort / Head_Face_20_Pio_Base retain their exact exported identifiers.
ST: no additional localization mapping is needed for these internal code names.
Human: asset names now append source code names in parentheses, e.g.
Hair_CurlyShort (SK_CurlyShort). Rigs and reference cards also retain their exact
source identity, and arc_asset_code_name stores it separately. Naming schema 2
upgrades existing imported objects once; later collection regrouping preserves
custom names. Source paths, driver IDs and association strings remain valid.

The Add All progress/last report is at the top of the sidebar. The plugin name
and panel header include the version, replacing the separate version row.
Settings no longer displays the Nonstandard folder layout section.

Verification: the actual 779-object scene passes 3715 naming/UI checks without
changing transforms, parenting, mesh data or material slots. Twelve existing
driver/shared-scene/grouping edge checks and 62 Pak regressions also pass.


### Reference cards, geometry sharing and authored head fitting

Reference cards crop visible art through UV/geometry bounds without editing PNGs.
Backpack defaults follow the measured Toolbox card (1.06908 times mesh height,
0.0113 m right gap, top offset -0.0044 m). Charm and attachment defaults use the
Computer Mouse and Football spacing with approximately 1:1 visible mesh height.
Facial icons show a full-face portrait, so their measured Moustache Regular size
and placement are retained. Cards read identically from either side and are
camera-only: Cycles and Eevee tests measure no shadow or indirect-light change.
Missing backpack cards recover from exact catalog identity or source packages.

Triple audit found one default frame and strap geometry, but authored frame
colour variants and conditional strap bone offsets. Identical geometry shares
mesh data with OBJECT-linked materials and separate rigs. Edited/morph-bearing
meshes remain independent; shader graphs only merge on an exact match. The
actual scene's 56 frame/strap meshes use two mesh datablocks after sharing.

Heads inherit body gender, and normal/facial hair inherit their same-slot head's
authored gender keys. Facial fitting additionally uses exact cooked cloth cages
and barycentric bindings, emitted as .arc-cloth.json sidecars by the Pak exporter.
An importer-owned corrective shape key preserves the original basis, topology
and user drivers. Target cage morphs apply once, including gender blends; copied
source physical morph indices are never inferred from facial render morph order.
All 18 heads and five facial styles pass 270 head/style/gender pairings. This is
static appearance fitting, not cloth simulation. All 26 normal-hair entries are
cloth-free; gender and existing headgear keys are retained, with no invented
head-specific cloth deformation. Static Buzzcut retains its authored placement.

Metadata refresh belongs to user-requested imports/repairs, not registration or
file-load handlers. Cached meshes refresh under revision 2.19.16; failed exports
invalidate old current stamps so a retry can recover. Existing offline blends
retain a valid saved fit for the same head. Mesh data is forked before per-head
key edits when linked instances need independent fits.


Mounted alias recovery: visual-part and sibling-texture directory discovery now
compare canonical package paths across PioneerGame/Content, RemappedPlugins/Content
and /Game while retaining original provider paths for loading and output. This
fixes a real live-cache case where head meshes exported successfully but their
wrap dependency was missed. Existing sidecars with a null wrap_mesh or missing
exact target dependencies are treated as incomplete. metadata_paths(objects)
plans the exact refresh for asynchronous callers. Cold Remapped head extraction
and nine null-sidecar recovery checks pass, including an empty repeat plan.

Cached material repair also respects shared frame/strap OBJECT overrides. A
different material-slot count first forks that instance mesh, preventing changes
to sibling variants; 22 compatibility checks pass.

### 2.19.17 reference defaults and charm connector sharing

Reference defaults now come from the user's seven calibrated cards in slot 001.
Positions and operative scales persist across imports, while local/world rotations
remain zero. Raider Tool artwork rotates 23 degrees clockwise before UV cropping,
keeping the original PNG unchanged and both sides readable. Head/facial fitting
preserves those fixed defaults. Exact charm connector geometry shares automatically
while object socket placement and material overrides remain independent.
