# Friendly mesh selector names - 2.19.32

Pak browser files now use Flavour (Original File Name), without a folder/type prefix or a .uasset extension. Example: Leaper (SK_BullCrab_01). Original folder structure is preserved. The original SK/SM code stays intact in parentheses, as requested in the naming format.

The browser snapshots the same enemy, appearance and outfit display-name catalogs once for a tree build. Unknown labels fall back to the readable source stem. Windows-invalid filename characters in flavour labels are replaced; original package identity is never inferred from the label.

A cached explicit map resolves each exact display filename back to its original cooked package. Files must exist inside the current browser tree and match that map; spoofed friendly labels are rejected. Folder selection still resolves from original package directory prefixes. The picker shows all selectors with no extension filter or extension appending.

Browser naming schema 2 and a display-catalog content revision participate in tree signatures, preventing old and new names from mixing and refreshing after display data changes. Update sync now explicitly excludes the runtime asset/index/browser cache from mirroring and cleanup.

Private Blender 5.1.2 verified 9670 unique selectors, exact Leaper name and reverse mapping, folder resolution, spoof/outside-root rejection, the longest selector path and Windows component lengths. First Z: tree build measured 12.07 seconds; warm tree access was 8.5 ms. Actual packaged ZIP installation must pass the repository gate before sync. Evidence: Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/21932/import-verification.json.


## Update cache preservation

The existing robocopy /MIR sync removed ArcPak/cache because the cache is intentionally absent from a clean package. That deleted indexes and generated assets on each update and defeated browser prebuilding. The sync now excludes ArcPak/cache at both source and destination paths, and excludes its contents from the post-sync bytecode/backup scrub. An isolated real package/sync test retained cache sentinels, including a .blend1 and a nested __pycache__ file, with exact contents. The current Leaper's generated mesh/texture dependencies and index were re-extracted from the game; the live scene now has zero missing cache-backed image files, and the friendly selector tree is ready. Source game files and Blender scene geometry were unchanged.
