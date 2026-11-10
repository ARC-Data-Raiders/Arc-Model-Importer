# Lighting Look 2.20.3 — Ultra Dynamic Sky sphere decoded from the game shader

2.20.1 laid the scenario cloud layer over the physical sky with an assumed
equidistant polar projection, a hand-made brightness (`opacity × mean sky`) and
a free rotation slider. 2.20.3 replaces that with the compiled sky sphere
material itself: its pixel shader is ported node for node, and the projection
comes from the sky sphere mesh. Static pak reads only.

## Evidence chain

| Step | Result |
|---|---|
| Sky MID in every scenario cell | `Pioneer_Sky.Ultra_Dynamic_Sky_Sphere.MID_Ultra_Dynamic_Sky_Mat_SA_NDC_NA_0` (FrozenTrail: `…_SA_NDC_0`) |
| Parent chain (`material-shadermaps`) | `Ultra_Dynamic_Sky_Mat_SA_NDC_NA` → `…_SA_NDC` → `…_default` → Material `Ultra_Dynamic_Sky_Mat`; each MIC has its own static-permutation resources (8 each) |
| Shader map (SM5 Epic/High) | `4A7198623FCD01A8A5C01FF45AC6ACEBEA43FC9D` (`ShaderArchive-PioneerGame_Chunk9-PCD3D_SM5`), two shaders only |
| Base-pass PS | index 5283, hash `D525701DBD2077A8ED1735B1CC907FAD948D2D60` (DXBC 17000 B, 520 instructions) |
| Base-pass VS | index 5282, hash `5B47CEF819F6180C2DCBA6A1DBE38D9E6F2997D8` |
| Texture slots (uniform expression set, 2D list order) | `Material_Texture2D_0` = `Cloud_Wisps_Texture` → `t4`, `_1` = `Stars_Texture` → `t5`, `_2` = `Moon_Texture` → `t6` (`t0`–`t3` are SkyAtmosphere LUTs and the primitive buffer) |
| `cb1` (material uniforms) | decoded from the preshaders (49 fields; Embark opcode `0x25` = 5-byte ComponentSwizzle), table below |
| Sky sphere mesh | `/Game/Pioneer/Lighting/VolumeClouds/Meshes/SM_Ultra_Dynamic_Sky_Sphere`, 2305 vertices, 7 UV channels; component scale 100 (radius ≈ 1.9·10⁸ cm) |

Scratch evidence: `Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/lighting-look/uds/`
(`shadermaps/`, `records/`, `asm/`, `cb1_fields.txt`, `mesh-uds-sphere.json`, `uv5_fit.json`, `scripts/`).

### cb1 fields used by the cloud path

| cb1 | Parameter (preshader) |
|---|---|
| `[0].xyz` / `[0].w` | `Night Sky Glow Color` / `Use Hybrid Sky` |
| `[1].xy` | `Wispy Cloud Panner.xy` |
| `[2].xyz` / `[2].w` | `Wispy Cloud Color` / `Wispy Cloud Alpha` |
| `[3].xyz` / `[3].w` | `Sun Position` / `1 / max(1e-5, Sun Radius)` |
| `[4].x` `[4].y` `[4].z` `[4].w` | `Sun Disk Intensity`, `sat(-SunPos.z)`, `SunPos.z`, `(1 - sat(-SunPos.z))·2 + 1` |
| `[5].xyz` / `[5].w` | `normalize(Moon Light Vector)` / `sat(SunPos.z · 15)` |
| `[6].x` | `Lit_Intensity` |
| `[18].w` | `clamp(-SunPos.z + 0.1, 0.025, 0.3)` |
| `[19].xyzw` `[20].xyz` | `Extra_Contrast`, `Overall Intensity`, `Saturation`, `SelectionColor.w` / `SelectionColor.xyz` |

`Sun Position` is the sun's forward vector (BuriedDay MID `(0.7214, 0.3317, −0.6079)` equals the catalog sun forward).

## Decoded math (n = −view direction, Unreal axes)

**Cloud UV.** VS 150–155 and 192: `TEXCOORD2.zw` (PS `v4.zw`) is the mesh UV
channel `min(5, NumTexCoords−1)`; PS 138–139 adds `View.GameTime · Wispy Cloud Panner.xy`.
UV5 of `SM_Ultra_Dynamic_Sky_Sphere` in sphere-local axes is a stereographic
projection from the nadir:

```
z ≥ 0:  uv = c + [[a, −b], [b, a]] · (−x, y) / (1 + z)
        c = (0.5009417, 0.5004221), a = 0.4995836, b = −0.0035687   (scale 0.49960, rotation −0.41°)
        least squares over 1985 vertices: rms 3.7e-4, max 1.6e-3 UV (< 1 texel of the 2048² layers)
z < 0:  three-ring skirt that mirrors the image: radius piecewise linear in z through
        (−1, 0.09894) (−0.562, 0.13582) (−0.0981, 0.40857) (0, 0.49960), angle = azimuth − 168.84°
        (max 6.5e-3 UV)
```

So the horizon is the edge of the inscribed circle, and a photo layer whose own
horizon sits at 0.9 of the radius appears about 6° above the game horizon. The
equidistant guess was off by up to 0.99 of the radius in places.
Sphere-local axes = world axes turned by the sphere's world yaw: the `Pioneer_Sky`
Root yaw composed with the `Ultra_Dynamic_Sky_Sphere` component's own yaw (BuriedDay:
65° − 110° = −45°). Textures use the default Wrap address mode.

**Wisps and atmosphere (PS 117–149).** `A` = SkyAtmosphere view LUT ·
`SkyLuminanceFactor` · `OneOverPreExposure` (the output is multiplied back by
`PreExposure`, so the material works in absolute nits).

```
W  = tex(t4, uv).rgb · Wispy Cloud Color · Wispy Cloud Alpha
A' = A + Night Sky Glow Color
m  = (Use Hybrid Sky · A' + W) / 2
C  = m + W · (A' + W − m)
```

**Sun disc (PS 150–235).** `F = sat((1 − |n − P| / R) · 1.4285715)⁴ · Sun Disk Intensity`
with `P = Sun Position`, `R = Sun Radius` (a chord length); colour
`|L| · lerp(normalize(A), normalize(L), sat(−P.z))`, `L` = transmittance · sun
illuminance; masked by `sat(−(n.z + 0.007) · 150)` (above the horizon).

**Glow gain (PS 239–260).**
`n' = normalize(n.x, n.y, (n.z − P.z)·k + P.z)`, `k = (1 − sat(−P.z))·2 + 1`;
`G = (1.8 · (max(0, 1 − |n'·P − 1|/2) + max(0, 1 − |n·M̂ − 1|/2) · sat(15·P.z)) + 2.3) · Lit_Intensity`.
The whole sky (atmosphere, wisps, disc) is multiplied by `G` ≥ 2.3.

**Reflection / sky-light capture (PS 233–238).** When `RenderingReflectionCaptureMask > 0`
(`cb0[205].w`, next to `RealTimeReflectionCapture` / `…PreExposure` in `cb0[206]`)
the colour is replaced by `1.35 · A'`: no wisps, no disc, still × `G` and graded.

**Grade (PS 446–465).** With `T = clamp(−P.z + 0.1, 0.025, 0.3)`, `x = X − T`:
`X ← (X + 2.5·EC·max(x,0)^1.2) · (1 + 5·EC·min(x,0))`, `X ← X · Overall Intensity`,
`X ← X + (Rec.709 luma(X) − X) · Saturation`, `max(X, 0)`.

Not ported: procedural and textured stars (`t5` via UV6), the moon disc/glow
(`t6`), the per-pixel dither (PS 120–132), and the primitive debug branch.

## Blender (2.20.3)

- `lighting_sky._uds_nodes` builds the PS graph on top of the Multiple Scattering
  sky (sun disc off) standing in for `A`; `_wisps_uv_nodes` is the UV5 closed form,
  rotated by the catalog's `sky_sphere.x_axis`, plus `Cloud Time · Wispy Cloud Panner`.
- Camera rays see the camera colour; all other rays see the capture colour × SkyLight
  intensity × colour. The UDS disc replaces Blender's physical disc; scenarios without a
  `Pioneer_Sky` actor (the five `Default*` cells) keep the plain atmosphere with its disc.
- All parameters come from the scenario MID: `Wispy Cloud Alpha/Color`, `Use Hybrid Sky`,
  `Sun Position`, `Moon Light Vector`, `Lit_Intensity`, `Extra_Contrast`, `Overall Intensity`,
  `Saturation`, `Sun Radius`, `Sun Disk Intensity`, `Night Sky Glow Color`, `Wispy Cloud Panner`.
- The cloud texture is the MID's `Cloud_Wisps_Texture`. Where the actor sets none, the MID
  keeps the material default `/Game/Pioneer/Lighting/VolumeClouds/Textures/Sky/Cloud_Wisps`,
  so e.g. BuriedCity Sandstorm and the WinterSnow cells now have wisps (41 of 65 scenarios).
- "Cloud Rotation" is gone (the game never rotates the layer). "Cloud Time" (seconds) feeds the
  panner; seven scenarios pan (0.0005–0.01 UV/s per axis), all of them `T_CloudsCirrus_01_M`
  night/storm/fog cells; the rest are static.
- Visible consequence: the sky light is several times stronger than in 2.20.1
  (capture = 1.35·A'·G with G ≥ 2.3, then graded), so shadows are softer and bluer.

Catalog (`scripts/lighting/build_lighting_catalog.py`): `sky_material` gains `vectors`
and `texture_paths`; new `sky_sphere` = {`x_axis` (world X axis, Unreal), `mesh`,
`visible`, `location`, `scale`}. `scripts/lighting/probe` gains a `mesh` mode (LOD0
positions + every UV channel).

## Verification (background Blender 5.1.2, private profile, 2.20.3 ZIP)

| Check | Result |
|---|---|
| Addon UV5 constants vs the mesh's UV5 | upper rms 3.7e-4, max 1.6e-3; skirt max 6.5e-3 (2113 vertices) |
| World graph vs numpy port of the PS (192×96 panorama, BuriedDay, `hdri_sky_029_8K_Sphere`) | camera median 9.1e-8, p99 1.4e-5, max 2.9e-4 relative |
| Capture branch vs `1.35·A'·G` graded × SkyLight | max 3.4e-7 relative |
| Orientation sensitivity (sphere turned 90°) | 0.24 mean relative change vs 5.2e-7 error |
| Sun disc at the lamp direction | peak 16181 nits vs ≤ 17.6 at mirrored directions |
| Whole `test_lighting.py` (grade, local exposure, vignette, exposure, sun, UDS) | 21/21 pass |
| All scenarios apply + panel draw | 65/65, renders finite |
| `package_addon.ps1` install gate | `INSTALL_CHECK_OK DataRaiders-Outfits 2.20.3`, no Traceback/Error |

The numpy port (`.local/lighting-look/blender/uds_check.py`) is written from the
instructions in Unreal axes and shares no node code with the addon; the atmosphere
stand-in and the view directions are read back from Cycles through debug taps.

## Known gaps

- `A` is Blender's Multiple Scattering sky, not UE's SkyAtmosphere LUT.
- Scenarios whose sky sphere is the engine `SM_SkySphere` (night/storm cells) take the
  wisps UV from its UV1, a lightmap-style chart layout, not a projection; the wisps are
  left off there (all of them use `T_CloudsCirrus_01_M` or a zero tint).
- `hdri_sky_072_8K_Sphere` and `T_CloudsCirrus_01_M` still fail in the bundled extractor
  (duplicate package entries; fixed in ArcPak source by the pak-dup-fix session, not yet shipped).
- BC6H layers (`hdri_sky_*`, `VHDRI_*`) are extracted as 8-bit PNG: 0.3–0.8 % of texels
  exceed 1.0 (max 1.56–4.86) and are clipped.
- Stars, the moon disc and moon glow are not emulated.
