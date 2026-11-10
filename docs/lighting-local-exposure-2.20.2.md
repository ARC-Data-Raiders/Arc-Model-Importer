# Lighting Look 2.20.2 — local exposure from the game's tonemapper

2.20.1 ignored the scenarios' `LocalExposure*` settings. 2.20.2 adds the
tonemapper's local-exposure stage between exposure and the log shaper, decoded
from the shipped shaders (static reads of build 26159202, Global SM5 archive).

## Shader evidence

Global SM5 holds 480 tonemapper records with the shaper literals `0x3D924925`
(1/14), `0x3F1C589A` (0.6107) and `0x3F866666` (×1.05). They dedupe to 240
containers. **80** of them declare a second `Texture3D` (the bilateral grid)
and a `Texture2D` blurred-log-luminance input in front of the LUT. All 80 use
the same registers. The shortest is `AEB6EEB6626EC11C…` (94 instructions),
compared with the non-local-exposure `9BFD64D3…` (50):

| Instr. | Shader | Meaning |
|---|---|---|
| 16–19 | `log2(max(dot(scene·cb1[156].w, cb0[38].xyz), cb0[36].w))` | LogLum: unexposed scene luminance (pre-exposure removed) |
| 20–22 | `log2(t0[0].w · cb0[45].x · 0.18)` | Middle grey MG |
| 23–27 | `t4.Sample(uv·cb0[45].zw, (LogLum·cb0[36].y + cb0[36].z)·31/32 + 0.5/32)` | 32-slice bilateral grid over the histogram log range; `.x/.y` = mean log lum |
| 29–31 | `weight < 0.001 ? t5.x : grid` | Fallback to the blurred term |
| 32–33 | `lerp(grid, blurred, cb0[44].w)` | `BlurredLuminanceBlend` |
| 34–36 | `+ log2(t0[0].x)` | Exposed LogLum and Base; Detail = LogLum − Base |
| 37–39 | `b = Base − MG; b > 0 ? cb0[44].x : cb0[44].y` | Highlight / shadow contrast scale |
| 40–54 | thresholds `cb0[46].xy`, falloff `1 − cb0[46].zw`, smoothstep | Split of `b` at the highlight/shadow thresholds |
| 55–58 | `exp2(MG + d(1−s) + (b − d(1−s))·scale + Detail·cb0[44].z − LogLum)` | Local exposure factor (`DetailStrength`) |
| 69–74 | `scene·tint·(E·vig·factor) + bloom·(E·vig)` | Applied to the scene, **not** to bloom |

With the default thresholds of 0, `d = 0` and the stage reduces to

    LocalLog = MG + (Base − MG) · scale + (LogLum − Base) · DetailStrength
    factor   = exp2(LocalLog − LogLum)

so unit scales give exactly 1 whatever the middle grey.

**Middle grey.** `t0` is the eye-adaptation buffer. Its writer, the AEM_Basic
compute shader `0FB39B0D67C81C4B…`, stores `x = w / clamp(avg/0.18, …)` and
`w = curve(EV) · cb0[2].x · cb0[2].y` (instructions 87–129, 224). So
`w = exposure × clamped white = 2^(bias + curve)`, and
`MG = log2(0.18 · 2^(bias + curve) · cb0[45].x)`. That is the clamped metered
average times `2^MiddleGreyBias`, in exposed space.

### CB uniforms set by C++ (not shader literals)

| Register | Value | Source |
|---|---|---|
| `cb0[44].xyzw` | Highlight scale, Shadow scale, DetailStrength, BlurredLuminanceBlend | `LocalExposure*` settings |
| `cb0[45].x` | `2^LocalExposureMiddleGreyBias` (assumed exp2 of the bias) | C++ |
| `cb0[45].zw` | grid UV scale | C++ (grid size) |
| `cb0[46].xy` | Highlight / Shadow threshold | settings |
| `cb0[46].zw` | threshold falloff (shader uses `1 − z`, `1 − w`); mapping from `*ThresholdStrength` not decoded | C++ |
| `cb0[36].yz` | histogram log scale/bias (grid depth range) | eye adaptation C++ |
| `cb0[36].w`, `cb0[38].xyz` | luminance floor and weights | eye adaptation C++ |
| `cb1[156].w` | `OneOverPreExposure` | view uniform |

The blurred texture `t5` is a Gaussian blur of log luminance sized by
`LocalExposureBlurredLuminanceKernelSizePercent`. The grid `t4` comes from
compute passes, and its cell size is not decoded.

## Scenario data

The build's usmap lists these `FPostProcessSettings` fields:
HighlightContrastScale, ShadowContrastScale, DetailStrength,
BlurredLuminanceBlend, BlurredLuminanceKernelSizePercent, MiddleGreyBias,
Highlight/ShadowThreshold, Highlight/ShadowThresholdStrength, the contrast
curves and Method. Scenarios override only the first six. 51 of the 65
scenarios set at least one value, for example BuriedDay H/S 0.9 with MiddleGreyBias −1, FrozenTrail Night
H 0.8 / S 0.5 / detail 1.05, and BlizzardDay H 1.04 / S 1.4. Engine defaults (UE
5.5 source values, in `lighting_grade.DEFAULTS`): scales 1, detail 1, blend 0.6,
kernel 50 %, middle-grey bias 0, thresholds 0, threshold strengths 1, method
Bilateral. An override recorded without a value means the default.

## Blender (compositor stack v13)

`lighting_post._local_exposure` builds the same math from math nodes:

- **Exact:** LogLum (metering luminance `mean(RGB)` with the 1e-4 floor, the
  same as the meter), exposed Base and Detail, middle grey (auto:
  `log2 0.18 + ArcExposureTotalBias + MiddleGreyBias`; locked meter: from the
  locked white point), the contrast split with thresholds, the detail term,
  exp2. Bloom stays unscaled, as in the shader.
- **Approximated:** the blurred term is a Gaussian **Blur** node on log
  luminance with radius `KernelSizePercent/100 · width / 2`. The **bilateral
  grid is approximated** by a **Bilateral Blur** node on the same image (range
  threshold 30/32 EV = one grid slice of the default histogram range, radius
  `width/64` capped at 16 px). The game builds both at reduced resolution.
- Toggle: *Local Exp* in Game Color Grade (`arc_lighting_apply_local_exposure`).
  The stage is skipped (blur nodes muted) when the scenario's scales are all 1.

Numpy reference: `lighting_grade.local_exposure_params`, `local_exposure_base`,
`local_exposure_factor`, `local_exposure_middle_grey_log`.

## Verification (background Blender 5.1.2, private profile, 2.20.2 ZIP)

| Check | Result |
|---|---|
| Local exposure, locked meter (BuriedDay: H/S 0.9, grey −1 EV) | max 3.1e-6 display (factor 0.79–1.49) |
| Local exposure, auto metering | max 1.0e-4 display (p99 9.5e-5) |
| Unit scales → identity (numpy) | 2.4e-7 |
| Highlights compressed / shadows lifted | top-10 % factor 0.86, bottom-10 % 1.32 |
| Existing checks (locked / vignette / auto metering) | 3.2e-6 / 3.5e-6 / 7.4e-5, unchanged |
| All 65 scenarios apply + panel draw (Local Exp toggle drawn) | 65/65, 0 errors |
| `package_addon.ps1` install gate (2.20.2 ZIP) | `INSTALL_CHECK_OK` |

The local-exposure checks take the compositor's base term from a debug render
and compare the full graded image with `lighting_grade` given that base, so
they check the exact math, not the blur approximation. Test:
`.local/lighting-look/blender/test_lighting.py` (Z: scratch).

## Remaining gaps

- The base term (blur + bilateral) is approximate, so halo width and edge
  behaviour differ from the game's grid.
- The `*ThresholdStrength` → `cb0[46].zw` mapping is assumed. No scenario sets
  a threshold.
- Contrast curves (`Local Exposure Highlight/Shadow Contrast Curve`) and the
  Fusion method are not ported. No scenario uses them.
