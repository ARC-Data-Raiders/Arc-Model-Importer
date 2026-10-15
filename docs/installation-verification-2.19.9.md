# Installation verification — 2.19.9

The 2.19.8 installer failed during Blender's restricted registration context:
`'_RestrictData' object has no attribute 'scenes'`. Version 2.19.9 defers Scene
defaults and data-dependent PSK/shader initialization until the event loop can
access normal bpy.data. Unregister cleans up pending initialization timers.

PSK discovery respects the active Blender profile. The dependency helper skips
missing modules and does not explicitly save user preferences unless requested.

Verified with the shipped ZIP on Blender 5.1.2:

- Fresh-profile installation, real restricted enable and bundled PSK setup.
- Disable/re-enable, overwrite an enabled install, and handler/timer cleanup.
- Restart with saved preferences; automatic initialization in a GUI event loop.
- Save/open a .blend, valid Steam default, and shader loading on demand.
- Live Blender MCP enable/re-enable alongside MapImporter and existing addons.
- Existing 62 asset regression checks.

One-off verification scripts, JSON results, logs, and installed-file backups
remain in dist/.local/ (gitignored). Run installation tests against private,
pre-created Blender config/scripts/extensions directories. Also redirect
APPDATA, LOCALAPPDATA, TEMP and TMP; an absent config directory can cause
Blender's preference writer to fall back to the normal profile.

The tests cover these installation paths on Blender 5.1.2. Other Blender
versions and complete asset shader parity require separate verification.

Local baseline: importer b40ae22; related CUE4Parse serializer changes b6e3adec.
Local commits do not publish changes; no push was performed.
