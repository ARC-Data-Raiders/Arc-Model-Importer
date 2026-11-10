# 2.19.55 — outfit material-slot colorways

Preserve all authored per-part material-slot overrides through preset selection, initial import, batch colorways and saved-object material updates. A Patcher variant now keeps its selected metal shell and its separate blue, red or yellow CRT screen; multiple bindings no longer collapse to the last material. Conflicting author records remain explicit errors.

Resolve non-Game virtual texture mounts by exact package path in current caches, including EmbarkScript shared hue noise and Engine defaults. Game resolution and long-path handling are preserved. No basename or broad directory search is added for plugin mounts. The GT cache generation changes so rebuilt materials acquire these bindings.

Five portable preset tests and eight path tests pass. A fresh Blender integration covers all three Patcher variants across HeadgearTech, MaskSocket and Pauldrone (nine cases), twice each, with exact per-slot MIs and unchanged geometry, polygon material indices, UVs, morphs and transforms. Package install verification remains mandatory before profile sync.

The repaired split checkpoint retains its original 17 completed variants and will retry the three Patcher variants with full slot-aware audits. This release does not claim that all saved splits have completed repair, or that engine rendering transport has reached 99% perceptual parity.
