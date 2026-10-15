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
