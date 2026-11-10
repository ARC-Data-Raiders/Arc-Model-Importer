# Plugin UI and loading revision draft

The implemented candidate and its verification are recorded in [UI revision 2.22.13](ui-revision-2.22.13.md). This document retains the design discussion; its source anchors describe the earlier audit state.

The UI ethos is "Dead simple with expanded advanced options". The normal path should ask what to import, show the relevant choices, and provide a clear action. File layout, extraction internals, and simulation details belong in expanded Advanced controls.

Preserve the refined Outfit Selector. Focus implementation on the other sections. Overall improvements may cover shared setup, loading states, labels, and error handling, but must not rearrange its controls, replace its selection behavior, or change its previews without separate approval.

This is a proposal, not an implemented redesign. The private-Blender audit in [import-performance-sidebar-audit.md](import-performance-sidebar-audit.md) measures cached imports and reproduces pre-action discovery in expanded panels and weapon callbacks. The exact production GUI stall, optimization speedups, and final package-size impact remain unverified.

## Agreed interaction rules

- The sidebar contains only game data and text explicitly supplied or approved by the user. Ask before adding any other visible text, including labels, subtext, descriptions, tooltips, empty-state messages, or status wording. No exception for explanatory or operational copy is implied.
- Keep all plugin sections within the main plugin panel. Do not create separate reorderable sidebar panels such as the current Shader Parity panel.
- Use nested collapsible menus. Relevant import tunings stay beside or within their workflow. Settings contains global advanced features, ordered from simple to complex.
- Prioritize a Blender artist who knows the game and Blender, but not the internal asset structure. Keep incomplete-asset investigation in FModel Vibe and guides external.
- Ship the sidebar's catalogs, game names, relationships, search data, and previews in the plugin ZIP. Missing browse data is a packaging gap, not a reason to query the game during drawing.
- Opening, searching, selecting, and expanding the sidebar must not access PAKs or invoke PakDirect. Only an explicit Import or Apply action may do that work.
- The plugin handles import and debug. After an operation finishes, the imported data belongs to the user. Do not synchronize, repair, restyle, or otherwise rewrite user edits automatically.
- Preserve the refined Outfit Selector, existing explicit scene values, import operators, and advanced import/debug capabilities.

## Shared setup

Assume the user has the game. Store installation paths, cache location, and general preferences in user configuration. Store asset choices, scene tunings, and explicit project overrides in the blend file. User edits to imported assets remain in that file and are not plugin-managed state.

Put shared configuration and global Advanced inside Settings, ordered from simple to complex. Keep guides external. Do not add an onboarding guide, readiness explanation, automatic game scan, or new sidebar text without approval.

Use packaged metadata for browsing. Validate the installation and resolve missing import data only when an explicit Import or Apply action needs it. PakDirect is the action-time fallback, not a sidebar data provider. Keep existing export support without making it an equally prominent setup path unless approved.

## Workflows by section

The following describes behavior, not approved new button labels or sidebar copy.

| Section | Normal workflow | Nested controls |
|---|---|---|
| Outfit Selector | Preserve the current refined selector and actions. | Audit shared loading dependencies without rearranging the selector or changing previews. |
| General model import | Choose an asset or folder and explicitly import it. | Import format, grouping, offsets, and source overrides. |
| Weapons | Browse the packaged weapon catalog, select game-authored choices, and import. | Relevant pre-import variants, skins, attachments, material options, batch import, and debug. |
| ARCs | Browse packaged ARC names and portraits, select an item, and import. | Variants, material import options, batch import, and debug. |
| Animations | Choose a target, select a packaged clip entry, and explicitly apply it. | Notify and prop import options, source overrides, and debug. Preserve mesh-height verification. |
| Effects | Browse the packaged effect catalog and explicitly import or apply the supported result. | Relevant import and simulation tunings. Keep incomplete-asset investigation external. |
| Lighting | Choose packaged game map or scenario data, then explicitly apply it. | Pre-apply exposure, grading, sky, and component options. No automatic scene changes on selection. |
| Materials | Build readable, editable material graphs during import. | Global reconstruction overrides and explicit debug operations belong in Settings. Artistic editing happens in the node graph after import. |
| Bridge and map tools | Keep import and debug actions appropriate to the installed addon line. | Ports, source manifests, placement stages, batch tuning, and debug. Do not merge addon lines. |

The plugin is not an asset-version manager. Do not add automatic replacement, change tracking, or special duplicate-version workflows. Blender users manage their own copies and edits. Any new wording or change to explicit debug scope needs approval.

### Effects

Package the effect list, game names, search fields, preview references, and known static requirements. Do not discover NS files or resolve renderer assets when the section opens or the selection changes.

| Existing input | Intended placement or behavior |
|---|---|
| Catalog Folder | Do not require a folder to populate the normal browser. Use the packaged catalog. Keep any retained override under relevant import/debug controls. |
| Asset Root | Resolve required data during an explicit operation using configured roots and PakDirect fallback. |
| Cache Folder | Use a configured output location for generated import or simulation data. Keep relocation in Settings. |
| NS JSON | Keep existing direct-file import within nested advanced controls. |
| SVML Shim | Keep its optional configuration in Settings and use it only during the requested operation. |
| Vector Fields | Resolve required data during the requested operation, not during browsing. |

A catalog entry does not establish full simulation support or fidelity. Do not silently substitute incomplete output for a complete effect. Keep detailed investigation in FModel Vibe. Handling unsupported operations and any visible wording require user approval.

### Shader parity

Shader Parity Root overrides generated material reconstruction data. Move the override into the relevant nested global Advanced menu in Settings and remove the standalone panel. It is not a normal browsing dependency.

Apply reconstruction during import or an explicit debug operation. Do not run post-import synchronization that rewrites user controls. The handoff is the material node graph stored in the blend file.

### Material node graph handoff

Build a clean, labeled, organized graph during import. Put obvious artistic controls together at the very top of the root graph, outside shared group definitions. Use simple value controls for scalar settings and suitable native controls for other data types. Keep the shader flow readable below them, with ordered sockets, frames, reroutes, and shared implementation groups where useful.

Keep per-asset control values in the asset's own material node tree. Shared shader group definitions and texture data can remain shared. External controls alone do not guarantee isolation if separate assets still reference the same material datablock. The import must preserve independent root material trees where independent tuning is promised.

After import, leave changes to the user. Do not install selection-change or scene-update behavior that resets values, relayouts the graph, or reapplies materials. Explicit debug actions remain separate and must not run merely because the UI redraws.

## Responsive loading design

The normal sidebar path reads installed, packaged metadata and prepared previews only. No PAK reads, mount checks, extraction, installed-build probing, dependency resolution, or game-directory scans occur during registration, drawing, search, selection changes, or menu expansion.

Drawing and enum callbacks consume prepared state. Load small bundled indexes once outside draw, filter in memory, and load prepared previews on demand outside draw. Precompute search keys and relationships so opening a menu does not reconstruct them. Load only what is needed for visible controls rather than decoding every image eagerly.

Use internal loading and error state without assuming permission to display any new text. Retain selections during loading. Package data must identify its source game build; validate it against the selected installation when an explicit action requires game access, not during browsing.

An explicit Import or Apply action can resolve missing data through PakDirect, create assets, and report through the approved debug path. Deduplicate action work and cancel at safe boundaries. Scheduling a blocking function later does not make it non-blocking. Keep Blender data manipulation on supported execution paths and isolate long non-Blender work where practical.

## Precomputation without package bloat

Use existing catalogs before adding another representation. Derive stable shipped metadata during packaging. Store installation-specific results in a local cache, not in the addon ZIP. Do not add a search dependency or ship a second copy of every catalog to avoid a small parse.

### Build-time candidates

| Candidate | Precompute | Size discipline |
|---|---|---|
| Catalog labels and search | Stable display labels, normalized search text, categories, and compact lookup IDs from existing metadata. | Prefer changing the existing generated catalog. Add a companion index only if measurement shows it saves worthwhile work. |
| Preview preparation | Known crop, padding, alpha composition, and display-sized derivatives for the non-outfit browsers. | Prefer replacing runtime-prepared sources with approved final assets. Avoid shipping both copies. Preserve the Outfit Selector assets in this pass. |
| Preview manifest | Map item IDs to packaged relative preview paths and dimensions. | Store only exceptional geometry or crop values when a shared default works. |
| Static relationships | Weapon attachment relationships, variants, ARC identities, and lighting preset labels that are already derivable from bundled source data. | Extend the authoritative producer. Do not introduce a parallel hand-maintained database. |
| Static capability metadata | Requirements that can be proved from the available exported data. | Store compact requirement codes. Do not ship full FX simulation inputs or advertise untested support. |

Load any shipped index lazily. A precomputed manifest that is eagerly read and expanded during addon registration can move the same delay to startup.

### Catalog-load time candidates

Deserialize compact bundled indexes once and create lightweight lookup maps only where storing them would duplicate significant data. Search, filtering, selection, and enum callbacks operate on those prepared structures. Compute page results and total matches together.

Memoize the lighting lists derived from the packaged catalog. Selection changes prepare import choices only; they do not call scene-application routines. The current automatic lighting callback is a removal candidate under the hands-off requirement.

### Local cache candidates

Local cache is for action-time asset preparation and results, not a substitute for missing packaged sidebar data.

| Data | Allowed computation point | Invalidation |
|---|---|---|
| Extracted meshes, textures, and animation payloads | Explicit Import or Apply. | Source build, extraction schema, and action-time validation. |
| Material reconstruction plans and resolved asset paths | Explicit Import, Apply, or a separately approved debug action. | Source build, relevant content signatures, producer version, and configuration. |
| Simulation output and required vector-field data | The explicitly requested effect operation. | Effect inputs, simulation settings, source build, and producer version. |
| Import/debug reports | The explicit operation. | Replace or retain according to the approved debug workflow. |

Package missing UI metadata and preview assets in the next addon artifact. Do not mutate the installed plugin ZIP at runtime, silently fall back to PAK discovery during browsing, or persist user edits into the addon cache.

Keep cache writes atomic and keyed to source build and schema. Validate action-critical inputs at operation time. Clean up only owned generated files, never game files, user exports, or blend-file edits.

For invalidation across tools, the safety fact is that a published manifest identifies the same source generation as the files it describes. Any producer change must include a script proving that fact against changed and unchanged exports.

### Proposed budgets

These are review targets, not measured savings or approved limits.

| Item | Proposed target |
|---|---|
| New bundled metadata | At most 1 MiB additional compressed ZIP data. Prefer no increase by replacing redundant existing data. |
| Preview changes | No additional full-resolution preview set. Compare total compressed asset size and inspect visible quality before replacement. |
| Sidebar first response | 100 ms target, measured separately for cold and warm state. |
| Work scheduled on the main loop | 8 ms target per slice. Report the longest slice, not only an average. |
| Package measurement | Compare before and after ZIPs using identical packaging inputs and compression settings. Report changed entries and bytes. |

Do not bundle baked particle caches, generated material graphs for every game material, extracted game data, full shader archives, or a thumbnail for every possible user export. Do not compress already compressed images again without a measured benefit. Do not treat moving data into Python literals as eliminating its size or load cost.

### Existing package evidence

The inspected existing archive is dist/outfits/DataRaiders-Outfits-2.22.12.zip. These measurements describe that artifact, not a newly built candidate. ZIP payload sizes exclude archive headers. The weapon catalog is already included in the weapon-browser total and must not be counted again.

| Existing item | Uncompressed bytes | Compressed ZIP payload bytes |
|---|---:|---:|
| Weapon browser assets, including its catalog | 12,908,362 | 12,656,063 |
| ARC browser assets | 12,916,540 | 12,919,257 |
| Outfit previews, protected in this revision | 7,626,953 | 7,628,811 |
| Weapon catalog JSON alone | 116,156 | 5,716 |
| Lighting scenario JSON | 635,697 | 54,887 |

| Existing archive | Bytes on disk |
|---|---:|
| DataRaiders-Outfits-2.22.12.zip | 96,916,448 |

The existing browser images occupy much more packaged space than the catalog metadata. Test prepared weapon and ARC previews before adding any new preview set. Preserve visual quality, alpha behavior, and the protected outfit previews. No replacement size or speedup has been measured.

### Source-backed priorities

| Existing repeated work | Proposed producer or consumer change |
|---|---|
| Ascended variant callbacks discover export variants before checking the enum cache. | Package the stable variant relationships. Defer actual asset resolution to the import action. |
| Weapon and ARC previews resolve local export paths while drawing. | Package a direct item-to-preview manifest and prepared preview assets. Resolve missing import payloads only during the action. |
| Animation discovery and search rebuild derived metadata. | Generate the shipped clip catalog and search fields before packaging. Search the bundled records in memory. |
| Lighting callbacks rebuild lists and can reapply a look after selection. | Memoize bundled lists and remove automatic scene reapplication. |
| Shader cache and FX status paths can access external data while drawing. | Remove external readiness work from browsing. Keep action-time validation and existing content checks. |

This is an investigation order, not a measured latency ranking. Preserve the refined Outfit Selector while eliminating shared paths that access the game during browsing.

### Measured import caching priorities

The [private-Blender audit](import-performance-sidebar-audit.md) separates the browsing boundary from import throughput. Its primary-only guard fails even with a valid configured root and populated weapon enums. Fix provider routing and package browse metadata before treating the sidebar as isolated.

For import throughput, measure exact-recipe compiled graph reuse first on repeated weapons. The current repeated import still compiles a fresh group after image reuse. Share an action-validated native-bank snapshot across material work, preserve downstream caches on unchanged extraction hits, and memoize dependency-resolved records before repeated parsing. Keep full signatures and reject stale data.

Prefer action/session caches and generated local templates over a shipped per-asset blend library. A precomputed plan does not remove Blender node creation. Share immutable helper computation only after checking resource bindings and independent root controls. Do not copy user-edited materials into the cache or change their graphs after import.

## Acceptance and implementation order

Start with a timing reproduction for the actual sidebar and section-open paths. Measure addon enable time separately so a faster sidebar cannot conceal a slower registration path. Profile before choosing which precomputation to implement.

| Check | Required result |
|---|---|
| Empty or missing setup | The sidebar draws usable navigation and a local setup action. Unrelated sections remain available. |
| Cold and warm opening | Record sidebar response, section readiness, addon enable time, and longest main-loop stall against the proposed budgets. |
| Drawing contract | Real panel and enum paths do not initiate filesystem scans, JSON parsing, extraction, or preview decoding. A targeted guard catches regressions. |
| Failure isolation | A missing folder, corrupt index, unavailable tool, or failed preview does not prevent other sections from drawing. |
| Source changes | Old asynchronous results cannot replace the new source state. Imports reject mismatched entries. |
| Packaged browse coverage | Every displayed item resolves its game data and preview from the installed package. Missing browse records fail the packaging coverage check. |
| PAK isolation | Opening, expanding, searching, and selecting work with the PAK backend configured to throw on any access. Import and Apply remain the explicit game-data boundary. |
| Cache lifecycle | Explicit operations validate generated payloads. Interrupted writes and incompatible schemas recover without touching blend-file edits. |
| Outfit protection | Existing selection behavior, controls, preview appearance, and actions remain unchanged. Measure shared loading changes without redesigning the selector. |
| Workflow coverage | A user can complete the supported normal action in each section without opening Advanced or supplying internal cache paths. |
| Text provenance | Every visible addon string is game data or explicitly approved by the user. No new label, description, tooltip, status, or empty-state copy bypasses review. Guides remain external. |
| Panel hierarchy | All plugin sections remain within the main panel. Settings contains global advanced features, ordered from simple to complex. |
| Hands-off handoff | Imported node graphs have independent per-asset root controls outside shared groups. No automatic plugin behavior rewrites subsequent user edits. |
| Size | Compare ZIP entries and compressed bytes. Identify every added generated artifact and its source. |
| Release | Any version bump must pass package_addon.ps1 and the packaged Blender install gate before distribution or installation. |

Implement the loading-state and measurement foundation first. Then simplify shared setup and the non-outfit sections. Add only the precomputed data that removes demonstrated work. Finish with compatibility, workflow, visual, package-size, and install checks.

## Source anchors

- ui.py:668 draws the main sidebar and its sections. ui.py:407 contains the animation section's catalog setup.
- niagara_fx.py:665 draws FX paths, catalog stats, selection, and playback controls.
- niagara_playback.py:35 defines simulation output and optional data/tool inputs.
- properties.py:1229 defines FX source controls and their shared-root fallbacks.
- material_runtime/shader_parity/plans.py:39 resolves the cache root. material_runtime/shader_parity/ui.py:29 draws cache status and scene controls.
- animation_catalog.py:182 indexes animation files. niagara_catalog.py:108 indexes Niagara definitions.
- weapon_ui.py:178 prepares gun previews on demand. lighting_catalog.py:35 already caches its loaded catalog.
- weapon_ui.py:32 and weapon_assets.py:96 cover variant discovery before enum caching. weapon_assets.py:443 resolves preview paths.
- arc_ui.py:107 resolves portrait candidates. animation_catalog.py:390 and animation_catalog.py:405 cover search matching and filtering.
- lighting_catalog.py:64 and lighting_catalog.py:90 derive catalog lists. lighting_looks.py:61 contains scenario-update behavior.
- package_addon.ps1:324 stages runtime assets. Prepared replacement previews need a producer step rather than a packaging-only exclusion.

The concurrency design must follow Blender's official Python threading guidance and application timer documentation. Timer scheduling alone is not a guarantee of responsiveness.
