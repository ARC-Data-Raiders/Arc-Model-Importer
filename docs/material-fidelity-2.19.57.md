# 2.19.57 — native opaque glass and exact fur/CRT colorways

Exact native/preset section bindings now govern fur variants and visor routing. StationMaster and Bells fur no longer revert to neighboring default MIs. Explicit CRT bindings survive visor aliases and sibling glass files; correctly named materials with the wrong clothing shader rebuild through native family dispatch.

The current M_Character_Glass_Opaque dry response replaces the bundled transparent approximation: authored view tint, circular dirt mask, roughness, normals, opaque alpha and metallic. Independent SM5/SM6 tracing corrected an initial mask error: dry metallic is the saturated authored value; snow attenuates it, dirt does not. Transparent glass remains a separate approximation. Runtime weather, renderer transport and matched lighting are not certified.

Verification: six fur routing tests, six visor/family routing tests, six opaque graph tests, nine corrected CPU render cases, nine corrected GPU cases, 192 independent SM6 fixtures/1,536 scalar checks, eight fresh fur variants and four fresh CRT variants applied twice with unchanged geometry. Real opaque-glass public imports are tracked in the release evidence. Existing decal math is unchanged.

Every bumped addon must pass the actual packaged-ZIP install/re-enable/overwrite gate before AppData sync. Saved split migration is separate; the first older checkpoint and second split need fresh rebuilds to acquire all current shader changes. No 99% perceptual parity claim is made.
