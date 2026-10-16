# Responsive previews and outfit selection — 2.19.29

Outfit search, three live results, Browse:, Load Outfit and Load All Colorways share the left half beside the outfit preview. Search uses TEXTEDIT_UPDATE. Head, hair, facial hair, backpacks, attachments, charms and raider tools use the same responsive layout. Below 430 UI-scaled pixels the preview moves below the controls, centered within 75% of the available content width. Wide previews are capped at the height of their control rows. Width calculations include sidebar zoom and UI scale.

Sidebar thumbnails trim transparent or uniform opaque padding and center the remaining artwork with a 2% safety border. Cropping is in memory; source PNGs and scene reference cards are untouched.

The outfit browse enum now reads/writes the canonical string key directly, uses numeric IDs no larger than 24 bits to avoid menu float precision loss, and never stores a competing selection. Abyss initialization is marked once per scene; subsequent startup/load initialization preserves the chosen outfit. Valid selections are checked when explicitly changed. Search registration is owned by the Outfits addon so its live-edit flag also applies when Map Importer is enabled.

Blender 5.1.2 verification passed for all 93 outfit choices, list/dropdown synchronization, repeated initialization, save/reopen, 280/380/600/900 pixel layout paths, and transparent/opaque-black thumbnail crops. The actual 2.19.29 ZIP passed fresh install/enable, disable/re-enable and overwrite-enabled checks through package_addon.ps1 before AppData sync. The existing restricted-data migration guard is retained. Evidence: Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/21929/sidebar-verification.json.
