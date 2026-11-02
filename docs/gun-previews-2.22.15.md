# Gun preview update

The sidebar retains the previous gun image until the selected replacement has decoded. A cold or unavailable image reserves the same preview area. Failed loads clear the old artwork instead of showing it indefinitely. Each sidebar region keeps its own displayed-image reference. Drawing does not decode images or check files.

Ascended selections use their packaged variant render if the requested tier or stencil render is unavailable. The current source has only the tier 3, unstenciled Ascended renders. This fallback does not change the imported tier or stencil, choose a different variant, or use the base gun image. Exact generated combinations still win. No images or package dependencies were added.

## Verification

The focused weapon tests reproduce the disappearing row and missing Ascended icons on the previous implementation. The private Blender probe checks retained textures during a pending replacement, changed pixel buffers after replacement, reserved space on cold and failed loads, and packaged Ascended coverage. It exercises real decoding and texture creation. Native mouse-driven sidebar appearance remains unverified.

The actual ZIP passed fresh install, enable, disable/re-enable, and enabled-overwrite checks before profile sync. The packaged sidebar, FX action-boundary, control-isolation, and idle-work guards also passed. The private transition probe passed again against the installed addon. The full source weapon suite passed; installed runtime tests passed with the source-only catalog-producer test excluded because tools are not shipped.

Evidence is retained under the task's gun-preview-20261102 scratch directory. The update is installed locally. Nothing was pushed or published. An open Blender process can retain the previous module until Blender restarts.
