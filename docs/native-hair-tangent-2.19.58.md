# Native hair tangent — 2.19.58

Current FrenchBraids uses `M_Hair_Metahuman_01`, a separate master from `M_Hair_Metahuman_ColorMap`. Only their independently proved tangent stage is shared. Exact source-parent recognition enables the stage; scalar-name similarity does not. ColorMap-specific specular/palette behavior stays separate.

Native Scraggle/FlattenNormal reconstruct strand direction using exact EngineRandomAngles data. Synthesized native nearest/repeat coordinates map to flipped PNG texel centers, including nativeV0; ordinary `1-v` with Repeat is wrong at this boundary. CurrentSM6 instruction replay passes20vectors per provedmaster. The actualBlender numeric atlas passes53CPU and53GPU cases for sampling, tangent, worldTBN and backface behavior.

Converted material JSON can omit Parent and flatten defaults. Validated same-folder raw companions now supply current native inheritance; package/name mismatches are rejected, both thin/raw dependencies tracked, and parent lookup stays within the original export root. Verified raw leaf companions override stale converted defaults. Ordinary explicit JSON without verified companions and later selected-palette scalar/vector overrides preserve their existing behavior, including zeros.

The real fresh FrenchBraids/Pink integration passes two public setup calls. Basevertices, topology, polygon material indices, everyUVlayer, morphcoordinates/values and objectmatrix are unchanged. PinkColorA, coverage and exact nativeNoise/Tangent bindings pass; the tie's recursive output-connected shader/source stays unchanged. Eight unlinked diagnostic texturealiases do not affect its shader. Neutral320px before/after review retains Pink, grayties and silhouette; alpha is identical.

## Portable pure tests

Run from the addon repository, choosing a task-ownedZdirectory:

```powershell
$env:DATARAIDERS_TEST_SCRATCH = 'Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/material-parity-20261026/character/hair-next-stage/frenchbraids-integration/pure-tests'
python -B tests/test_character_parameter_companions.py --scratch $env:DATARAIDERS_TEST_SCRATCH
python -B tests/test_native_hair_tangent_sources.py
```

Fourteen companion/precedence tests and four exact-family tests pass. Actual Blender tests run serially in private profiles, cappedfourthreads/affinity15/BelowNormal; preparation does not authorize parallel Blender launches. Evidence lives under the designatedZscratch `material-parity-20261026/character/hair-next-stage`.

## Release status and limits

The actual packaged ZIP passed installation, enable, disable/re-enable and enabled-overwrite checks in a private Blender 5.1.2 process, exit 0. Version 2.19.58 was synced after that gate; source/ZIP/installed critical runtime files were verified. The user’s running Blender was not restarted, so existing loaded modules may remain older until restart.

Native material-stage reconstruction does not replace the engine'sMSM_Hair closure. Principled anisotropy, fullmulti-lobeR/TT/TRT scattering, weather, PDO/temporal coverage, cookedfiltering and matchedUI lighting/tonemapping remain separate work. No perceptual99%game-look claim follows from these tests. OriginalUI PNGs remain the comparison authority.
