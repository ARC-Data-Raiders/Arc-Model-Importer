# Blender addon install failures and release gate

The `_RestrictData` failure has recurred across releases. Blender runs addon
`register()` with restricted `bpy.data`, so code that touches `bpy.data.scenes`
there can fail even when it works from the Python console or after startup.

- **2.19.8 → 2.19.9:** install failed with `'_RestrictData' object has no
  attribute 'scenes'`. Scene defaults and other data-dependent setup were
  deferred to the event loop; see [installation-verification-2.19.9.md](installation-verification-2.19.9.md).
- **2.19.26:** weapon ascended enum registration introduced another direct
  `bpy.data.scenes` read. It was moved to a timer so saved selections are
  migrated only after Blender restores normal data access.
- **2.19.27:** a remaining property migration was guarded. A fresh
  `addon_utils.enable()` test and private-profile install/enable tests passed,
  but the user still saw an error from Blender's UI ZIP install path. That
  report is not explained by the clean-profile tests; an older module retained
  in an open Blender process is one possible cause, not a confirmed diagnosis.
- **2.19.28:** the package script gained a mandatory ZIP install gate. Its
  first run correctly blocked release because the private profile path exceeded
  Windows' ZIP extraction path limit; shortening the path let the complete gate
  pass before AppData sync. A private-profile 2.19.27 → 2.19.28 enabled upgrade
  also passed. These checks do not reveal the state of an already-open Blender
  window.

Blender's UI traceback for this case ends in
`bl_extension_ops.py → PREFERENCES_OT_addon_install.execute() →
bpy.ops.preferences.addon_enable()` and may omit the addon's own source line.
Capture the full Blender console traceback and the loaded addon version/path
when the live error differs from a private-profile test.

## Mandatory check on every version bump

Run `package_addon.ps1` for the release line. It now tests the exact staged ZIP
in a private Blender profile **before** syncing to AppData. The check calls
`bpy.ops.preferences.addon_install()`, then `addon_enable()`, disable/re-enable,
and install-over-enabled. It requires `{'FINISHED'}` from each operator, an
enabled addon with the expected version, no traceback/error output, and a zero
Blender exit code. Any failure blocks the sync. `-BlenderExe` or `BLENDER_EXE`
selects Blender when it is not in the usual location.

The private profile also keeps Blender config, scripts, extensions, bytecode,
and temporary files out of the user's live profile. Set `TEMP`/`TMP` to the
designated Z: scratch root before packaging on this workstation. The script
creates a short `ic-*` profile directly under that root to avoid Windows path
length failures when extracting long asset filenames.

## Compute headroom

Blender launches are admitted by current resource headroom, not the number of
running instances. The package check requires CPU usage below 90% and at least
4 GiB of free physical RAM. It has no fixed Blender process or worker-thread
cap. Existing processes are left running.

`Get-BlenderComputeHeadroom` reports CPU usage, free RAM and each NVIDIA GPU
separately. The install check does not require a GPU. GPU workloads can call
`Assert-BlenderComputeHeadroom -RequireGpu`; either GPU qualifies when its
utilization is below 90% and at least 1024 MiB of VRAM is free. An occupied
GPU does not exclude another available GPU. Missing GPU telemetry is unknown,
not zero usage, and blocks only GPU-required admission.

`tests/test_blender_compute_headroom.ps1` checks admission with synthetic
resource readings. Add `-Live` to check the current CPU, RAM and both GPUs.

This gate catches basic install/registration errors. It does not prove that an
already-running Blender process has unloaded an older Python module. When a
live UI install still fails after the gate passes, record that session's module
version/path and restart Blender before retrying the newly installed package.
Do not claim the live problem is fixed from the private test alone.


### 2.20.0 packaging and preservation

`-InstallCheckRoot` (or `DATARAIDERS_INSTALL_CHECK_ROOT`) selects an existing short scratch root independently of the deeper staging TEMP path. The script checks the longest extraction path against Windows MAX_PATH before creating the private profile. A deep-profile failure was corrected and the exact rebuilt ZIP passed the complete gate.

Sync copies shipped files without mirroring deletions into the user addon. Custom files, blends and persistent caches survive; only generated runtime bytecode is pruned, with realpath boundary checks. `tests/test_package_sync_preservation.ps1` verifies this behavior. Fresh installed verification hashes immutable ZIP files and validates `ADDON_SYNC.txt` semantically because sync intentionally rewrites its timestamp.
