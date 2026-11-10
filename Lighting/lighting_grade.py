"""Arc Raiders post-process color pipeline, ported from the game's compiled shaders.

Producer (combined 32^3 LUT): Global SM5 pixel shader 3655BA89…, the
authored-strip variant of the engine's combine-LUTs pass. This module is an
instruction-order float32 transcription; register names in comments refer to
that shader (CB0 = pass parameters, CB1 = working color space).

Consumer (tonemapper): Global SM5 pixel shader 10EE0D70…:
    H = exposure * scene
    q = saturate(log2(H + LOG_EPS) * LOG_SCALE + LOG_OFFSET)
    display = lut3d(q * (N-1)/N + 0.5/N) * 1.05

Local exposure (permutations AEB6EEB6… and 79 others with a bilateral grid and
a blurred log-luminance input) scales the exposed scene before the shaper; see
``local_exposure_factor``.

The settings layer maps PostProcessSettings fields onto those registers using
engine defaults for fields a volume does not override. No bpy import here.
"""
from __future__ import annotations

import struct
from typing import Any, Mapping

import numpy as np

F32 = np.float32


def _h(bits: int) -> np.float32:
    """Exact float32 from an instruction literal."""
    return np.frombuffer(struct.pack("<I", bits & 0xFFFFFFFF), "<f4")[0]


# Consumer constants (tonemapper literals).
LOG_EPS = _h(0x3B2ED4E7)        # 0.0026677193  (= LogToLin(0))
LOG_SCALE = _h(0x3D924925)      # 1/14
LOG_OFFSET = _h(0x3F1C589A)     # 0.61072695
CONSUMER_GAIN = _h(0x3F866666)  # 1.05

LUT_SIZE = 32  # engine default r.LUT.Size

# ---------------------------------------------------------------------------
# PostProcessSettings defaults (engine) for every field the producer reads.
# A volume's bOverride_* field replaces the default; an override recorded
# without a value means the default value itself.
# ---------------------------------------------------------------------------
_ONE4 = {"X": 1.0, "Y": 1.0, "Z": 1.0, "W": 1.0}
_ZERO4 = {"X": 0.0, "Y": 0.0, "Z": 0.0, "W": 0.0}
_GRADE_FIELDS = ("Saturation", "Contrast", "Gamma", "Gain", "Offset")
_REGIONS = ("", "Shadows", "Midtones", "Highlights")

DEFAULTS: dict[str, Any] = {
    "WhiteTemp": 6500.0,
    "WhiteTint": 0.0,
    "TemperatureType": "ETemperatureMethod::TEMP_WhiteBalance",
    "ColorCorrectionShadowsMax": 0.09,
    "ColorCorrectionHighlightsMin": 0.5,
    "ColorCorrectionHighlightsMax": 1.0,
    "BlueCorrection": 0.6,
    "ExpandGamut": 1.0,
    "ToneCurveAmount": 1.0,
    "SceneColorTint": {"R": 1.0, "G": 1.0, "B": 1.0, "A": 1.0},
    "ColorGradingIntensity": 1.0,
    "ColorGradingLUT": None,
    "VignetteIntensity": 0.4,
    "BloomIntensity": 0.675,
    "BloomThreshold": -1.0,
    "AutoExposureMethod": "EAutoExposureMethod::AEM_Histogram",
    "AutoExposureBias": 1.0,
    "AutoExposureMinBrightness": -10.0,
    "AutoExposureMaxBrightness": 20.0,
    "AutoExposureBiasCurve": None,
    "AutoExposureMeterMask": None,
    # Local exposure (UE 5.5 FPostProcessSettings defaults; the field set is
    # confirmed by the build's usmap, the values are engine source defaults).
    "LocalExposureMethod": "ELocalExposureMethod::Bilateral",
    "LocalExposureHighlightContrastScale": 1.0,
    "LocalExposureShadowContrastScale": 1.0,
    "LocalExposureDetailStrength": 1.0,
    "LocalExposureBlurredLuminanceBlend": 0.6,
    "LocalExposureBlurredLuminanceKernelSizePercent": 50.0,
    "LocalExposureMiddleGreyBias": 0.0,
    "LocalExposureHighlightThreshold": 0.0,
    "LocalExposureShadowThreshold": 0.0,
    "LocalExposureHighlightThresholdStrength": 1.0,
    "LocalExposureShadowThresholdStrength": 1.0,
}
for _region in _REGIONS:
    for _field in _GRADE_FIELDS:
        DEFAULTS["Color" + _field + _region] = dict(_ZERO4 if _field == "Offset" else _ONE4)


def resolve_settings(overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    """Engine defaults + one volume's overridden fields (None = default)."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for key, value in (overrides or {}).items():
        if value is None:
            continue  # override flag set without a stored value: default applies
        out[key] = value
    return out


def _lerp_value(a, b, w: float):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(b, bool):
        return a + (b - a) * w
    if isinstance(a, Mapping) and isinstance(b, Mapping) and set(a) == set(b):
        if all(isinstance(v, (int, float)) for v in b.values()):
            return {k: a[k] + (b[k] - a[k]) * w for k in b}
    return b if w >= 0.5 else a


def blend_volumes(volumes) -> dict[str, Any]:
    """FinalPostProcessSettings from unbound volumes: ascending priority, weighted lerp.

    Each volume is ``{"priority", "blend_weight", "overrides"}`` with overrides as
    recorded by the catalog (only bOverride_* fields; None = engine default).
    """
    out = resolve_settings(None)
    ordered = sorted(volumes or (), key=lambda v: float(v.get("priority") or 0.0))
    for vol in ordered:
        weight = min(max(float(vol.get("blend_weight", 1.0) or 0.0), 0.0), 1.0)
        if weight <= 0.0:
            continue
        overrides = vol.get("overrides") or {}
        for key, value in overrides.items():
            if key == "ColorGradingIntensity":
                continue  # read together with the LUT it scales
            if value is None:
                value = DEFAULTS.get(key)
            if key == "ColorGradingLUT":
                # The volume's own intensity scales its LUT contribution.
                own = overrides.get("ColorGradingIntensity")
                intensity = DEFAULTS["ColorGradingIntensity"] if own is None else float(own)
                out["ColorGradingLUT"] = value
                out["ColorGradingIntensity"] = min(max(intensity * weight, 0.0), 1.0) if value else 0.0
                continue
            out[key] = value if weight >= 1.0 or key not in out else _lerp_value(out[key], value, weight)
    return out


def lut_name(settings: Mapping[str, Any]) -> str:
    """Asset name of the authored strip referenced by ColorGradingLUT ('' when none)."""
    ref = settings.get("ColorGradingLUT")
    if isinstance(ref, Mapping):
        ref = ref.get("name") or ref.get("path") or ""
    text = str(ref or "")
    return text.rsplit("/", 1)[-1].split(".", 1)[0]


# ---------------------------------------------------------------------------
# Eye adaptation (AEM_Basic / AEM_Histogram target exposure, steady state).
# ---------------------------------------------------------------------------
EV100_LUMINANCE = 1.2   # EV100ToLuminance(EV) = 1.2 * 2^EV
MIDDLE_GREY = 0.18      # GreyMult for the automatic methods
METER_LUMINANCE_MIN = 0.0001


def eval_rich_curve(keys, t: float) -> float:
    """FRichCurve evaluation (constant extrapolation, non-weighted cubic tangents)."""
    if not keys:
        return 0.0
    ks = sorted(keys, key=lambda k: float(k.get("Time", 0.0)))
    if t <= float(ks[0].get("Time", 0.0)):
        return float(ks[0].get("Value", 0.0))
    if t >= float(ks[-1].get("Time", 0.0)):
        return float(ks[-1].get("Value", 0.0))
    for a, b in zip(ks, ks[1:]):
        t0, t1 = float(a.get("Time", 0.0)), float(b.get("Time", 0.0))
        if t > t1:
            continue
        v0, v1 = float(a.get("Value", 0.0)), float(b.get("Value", 0.0))
        diff = t1 - t0
        if diff <= 0.0:
            return v1
        alpha = (t - t0) / diff
        mode = str(a.get("InterpMode", "RCIM_Cubic"))
        if mode.endswith("RCIM_Constant"):
            return v0
        if mode.endswith("RCIM_Linear"):
            return v0 + (v1 - v0) * alpha
        p1 = v0 + float(a.get("LeaveTangent", 0.0)) * diff / 3.0
        p2 = v1 - float(b.get("ArriveTangent", 0.0)) * diff / 3.0
        u = 1.0 - alpha
        return u * u * u * v0 + 3.0 * u * u * alpha * p1 + 3.0 * u * alpha * alpha * p2 + alpha ** 3 * v1
    return float(ks[-1].get("Value", 0.0))


def exposure_curve_samples(keys, step: float = 0.25, pad: float = 4.0) -> list[tuple[float, float]]:
    """Dense (EV100, bias EV) samples of an AutoExposureBiasCurve for a piecewise-linear node curve."""
    if not keys:
        return []
    times = [float(k.get("Time", 0.0)) for k in keys]
    lo, hi = min(times) - pad, max(times) + pad
    count = max(2, int(round((hi - lo) / step)) + 1)
    return [(lo + (hi - lo) * i / (count - 1), eval_rich_curve(keys, lo + (hi - lo) * i / (count - 1)))
            for i in range(count)]


def scene_ev100(average_luminance: float) -> float:
    """EV100 of the white point implied by a metered average (average = 18% of white)."""
    white = max(float(average_luminance), METER_LUMINANCE_MIN) / MIDDLE_GREY
    return float(np.log2(white / EV100_LUMINANCE))


def target_exposure(average_luminance: float, settings: Mapping[str, Any], curve_keys=None) -> float:
    """Exposure scale for a metered average scene luminance (nits).

    The white point (average / 0.18) is clamped to EV100ToLuminance(Min/Max
    brightness); the bias curve reads the white point's EV100.
    """
    white = max(float(average_luminance), METER_LUMINANCE_MIN) / MIDDLE_GREY
    bias = float(settings.get("AutoExposureBias", DEFAULTS["AutoExposureBias"]) or 0.0)
    bias += eval_rich_curve(curve_keys or [], scene_ev100(average_luminance))
    lmin = EV100_LUMINANCE * 2.0 ** float(settings.get("AutoExposureMinBrightness", -10.0))
    lmax = EV100_LUMINANCE * 2.0 ** float(settings.get("AutoExposureMaxBrightness", 20.0))
    lmin = min(lmin, lmax)
    return 2.0 ** bias / min(max(white, lmin), lmax)


def clamped_white(average_luminance: float, settings: Mapping[str, Any]) -> float:
    """Metered white point (average / 0.18) after the Min/Max brightness clamp."""
    white = max(float(average_luminance), METER_LUMINANCE_MIN) / MIDDLE_GREY
    lmin = EV100_LUMINANCE * 2.0 ** float(settings.get("AutoExposureMinBrightness", -10.0))
    lmax = EV100_LUMINANCE * 2.0 ** float(settings.get("AutoExposureMaxBrightness", 20.0))
    return min(max(white, min(lmin, lmax)), lmax)


# ---------------------------------------------------------------------------
# Local exposure (tonemapper permutations with the bilateral grid, e.g. AEB6EEB6…).
# All log2 values are in exposed space (scene * exposure), as in the shader:
#     LogLum   = log2(max(lum(scene), floor)) + log2(E)                 (16-19, 34-35)
#     Base     = lerp(Grid or Blurred, Blurred, BlurredBlend) + log2(E) (23-35)
#     Detail   = LogLum - Base                                          (36)
#     MG       = log2(0.18 * EyeAdaptation.w * 2^MiddleGreyBias)        (20-22)
#     b        = Base - MG;  scale = b > 0 ? Highlight : Shadow         (37-39)
#     LocalLog = MG + d (1 - s) + (e + d s) scale + Detail * DetailStrength  (40-56)
#     factor   = exp2(LocalLog - LogLum), applied to the scene but not to bloom
# EyeAdaptation.w = exposure * clamped white = 2^(bias + curve) (eye adaptation
# compute 0FB39B0D…), so MG is the clamped metered average * 2^MiddleGreyBias
# in exposed space. e/d/s split b at the highlight/shadow thresholds; with the
# default thresholds of 0, d = 0 and LocalLog = MG + b * scale + Detail * strength.
# ---------------------------------------------------------------------------
LOCAL_EXPOSURE_KEYS = ("HighlightContrastScale", "ShadowContrastScale", "DetailStrength", "BlurredLuminanceBlend",
                       "BlurredLuminanceKernelSizePercent", "MiddleGreyBias", "HighlightThreshold", "ShadowThreshold",
                       "HighlightThresholdStrength", "ShadowThresholdStrength")
GRID_MIN_WEIGHT = _h(0x3A83126F)  # 0.001: below this grid weight the blurred term is used


def local_exposure_params(settings: Mapping[str, Any]) -> dict[str, float]:
    """LocalExposure* fields (engine default where a volume leaves one unset)."""
    out = {}
    for key in LOCAL_EXPOSURE_KEYS:
        value = settings.get("LocalExposure" + key)
        out[key] = float(DEFAULTS["LocalExposure" + key] if value is None else value)
    return out


def local_exposure_is_identity(params: Mapping[str, float]) -> bool:
    """True when the stage cannot change any pixel: with unit scales MG and Base cancel out."""
    return (params["HighlightContrastScale"] == 1.0 and params["ShadowContrastScale"] == 1.0
            and params["DetailStrength"] == 1.0)


def scene_log_luminance(rgb: np.ndarray) -> np.ndarray:
    """Unexposed log2 luminance the grid and blur are built from (metering luminance and floor)."""
    lum = np.asarray(rgb, dtype=np.float32)[..., :3].mean(axis=-1)
    return np.log2(np.maximum(lum, F32(METER_LUMINANCE_MIN))).astype(np.float32)


def local_exposure_middle_grey_log(compensation_ev: float, params: Mapping[str, float]) -> float:
    """log2(0.18 * 2^(bias + curve) * 2^MiddleGreyBias); compensation_ev = log2(exposure * clamped white)."""
    return float(np.log2(MIDDLE_GREY)) + float(compensation_ev) + params["MiddleGreyBias"]


def local_exposure_base(blurred_log: np.ndarray, grid_log: np.ndarray | None, params: Mapping[str, float],
                        grid_weight: np.ndarray | None = None) -> np.ndarray:
    """Unexposed base log luminance: lerp(grid, blurred, BlurredLuminanceBlend) (27-33)."""
    blurred = np.asarray(blurred_log, dtype=np.float32)
    grid = blurred if grid_log is None else np.asarray(grid_log, dtype=np.float32)
    if grid_weight is not None:
        grid = np.where(np.asarray(grid_weight) < GRID_MIN_WEIGHT, blurred, grid)
    return (grid + F32(params["BlurredLuminanceBlend"]) * (blurred - grid)).astype(np.float32)


def local_exposure_factor(log_lum: np.ndarray, base_log: np.ndarray, middle_grey_log: float,
                          params: Mapping[str, float], *, threshold_falloff=None) -> np.ndarray:
    """Exposure multiplier per pixel from exposed-space LogLum, Base and MG (instructions 36-58).

    ``threshold_falloff`` = (CB0[46].z, CB0[46].w), C++-derived from the threshold
    strengths; the mapping is not decoded, so it defaults to the strengths themselves.
    No shipped scenario sets a threshold, and a zero threshold makes them irrelevant.
    """
    lg = np.asarray(log_lum, dtype=np.float32)
    base = np.asarray(base_log, dtype=np.float32)
    mg = F32(middle_grey_log)
    detail = lg - base
    b = base - mg
    pos = b > 0
    scale = np.where(pos, F32(params["HighlightContrastScale"]), F32(params["ShadowContrastScale"]))
    th, ts = F32(params["HighlightThreshold"]), F32(params["ShadowThreshold"])
    kh, ks = threshold_falloff or (params["HighlightThresholdStrength"], params["ShadowThresholdStrength"])
    th_t = _sat((F32(1) - F32(kh)) * (np.abs(b) - th))
    ts_t = _sat((F32(1) - F32(ks)) * (np.abs(b) - ts))
    s = np.where(pos, th_t * th_t * (F32(3) - F32(2) * th_t), ts_t * ts_t * (F32(3) - F32(2) * ts_t))
    e = np.where(pos, np.maximum(b - th, F32(0)), np.minimum(b + ts, F32(0)))
    d = b - e
    keep = F32(1) - s
    local = (mg + d * keep) + (b - d * keep) * scale + detail * F32(params["DetailStrength"])
    return np.exp2(local - lg).astype(np.float32)


def vignette_mask(width: int, height: int, intensity: float) -> np.ndarray:
    """Tonemapper vignette: cos^4 falloff in aspect-corrected circle space (rows bottom-up)."""
    aspect = float(width) / float(max(height, 1))
    ys, xs = np.meshgrid((np.arange(height) + 0.5) / height * 2.0 - 1.0,
                         (np.arange(width) + 0.5) / width * 2.0 - 1.0, indexing="ij")
    scale = np.sqrt(2.0 / (1.0 + 1.0 / (aspect * aspect)))
    vx, vy = xs * scale * intensity, ys / aspect * scale * intensity
    return (1.0 / (vx * vx + vy * vy + 1.0)) ** 2


def lut_unwrapped_rgba(lut: np.ndarray) -> np.ndarray:
    """LUT[b, g, r, rgb] -> flat RGBA for an (N*N) x N Blender float image.

    Blender rows run bottom-up: row g holds blue slice b at columns b*N .. b*N+N-1.
    """
    n = lut.shape[0]
    img = np.ones((n, n * n, 4), dtype=np.float32)
    img[:, :, :3] = lut.transpose(1, 0, 2, 3).reshape(n, n * n, 3)
    return img.ravel()


def _vec4(v) -> tuple[float, float, float, float]:
    if isinstance(v, Mapping):
        return (float(v.get("X", v.get("R", 0.0))), float(v.get("Y", v.get("G", 0.0))),
                float(v.get("Z", v.get("B", 0.0))), float(v.get("W", v.get("A", 0.0))))
    seq = list(v)
    return tuple(float(x) for x in (seq + [0.0] * 4)[:4])


# ---------------------------------------------------------------------------
# Working color space (CB1): sRGB/Rec.709 D65 working space, AP1 tone space.
# ---------------------------------------------------------------------------
_BRADFORD = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])


def _rgb_to_xyz(primaries, white) -> np.ndarray:
    def xyz(x, y):
        return np.array([x / y, 1.0, (1.0 - x - y) / y])
    m = np.stack([xyz(*p) for p in primaries], axis=1)
    s = np.linalg.solve(m, xyz(*white))
    return m * s


def _cat(src_white, dst_white) -> np.ndarray:
    def xyz(x, y):
        return np.array([x / y, 1.0, (1.0 - x - y) / y])
    src = _BRADFORD @ xyz(*src_white)
    dst = _BRADFORD @ xyz(*dst_white)
    return np.linalg.inv(_BRADFORD) @ np.diag(dst / src) @ _BRADFORD


_D65 = (0.3127, 0.3290)
_ACES_WHITE = (0.32168, 0.33767)
SRGB_TO_XYZ = _rgb_to_xyz([(0.64, 0.33), (0.30, 0.60), (0.15, 0.06)], _D65)
AP1_TO_XYZ = _rgb_to_xyz([(0.713, 0.293), (0.165, 0.830), (0.128, 0.044)], _ACES_WHITE)
SRGB_TO_AP1 = np.linalg.inv(AP1_TO_XYZ) @ _cat(_D65, _ACES_WHITE) @ SRGB_TO_XYZ
AP1_TO_SRGB = np.linalg.inv(SRGB_TO_AP1)


def working_space_constants() -> np.ndarray:
    """CB1[0..24]: rows of ToXYZ, FromXYZ, ToAP1, FromAP1 (+ padding); [24].x = bIsSRGB."""
    cb1 = np.zeros((25, 4), dtype=np.float32)
    cb1[0:3, 0:3] = SRGB_TO_XYZ
    cb1[4:7, 0:3] = np.linalg.inv(SRGB_TO_XYZ)
    cb1[8:11, 0:3] = SRGB_TO_AP1
    cb1[12:15, 0:3] = AP1_TO_SRGB
    cb1.view(np.uint32)[24, 0] = 1  # working space is sRGB: output gamut transform bypassed
    return cb1


def lut_weights(settings: Mapping[str, Any]) -> tuple[float, float]:
    """(ungraded, authored strip) blend weights for one fully weighted volume."""
    if not settings.get("ColorGradingLUT"):
        return 1.0, 0.0
    intensity = float(settings.get("ColorGradingIntensity", 1.0))
    intensity = min(max(intensity, 0.0), 1.0)
    return 1.0 - intensity, intensity


def producer_constants(settings: Mapping[str, Any], lut_size: int = LUT_SIZE) -> np.ndarray:
    """CB0[0..49] as the combine-LUTs shader reads it."""
    s = settings
    cb0 = np.zeros((50, 4), dtype=np.float32)
    w0, w1 = lut_weights(s)
    cb0[5, 0], cb0[5, 1] = w0, w1
    cb0[21] = (0.0, 0.0, 0.0, 0.0)                     # overlay (fade) color/amount
    cb0[22, 0:3] = _vec4(s["SceneColorTint"])[0:3]     # color scale
    base = 23
    for r, region in enumerate(_REGIONS):
        for f, field in enumerate(_GRADE_FIELDS):
            cb0[base + r * 5 + f] = _vec4(s["Color" + field + region])
    cb0[43] = (lut_size, s["WhiteTemp"], s["WhiteTint"], s["ColorCorrectionShadowsMax"])
    cb0[44] = (s["ColorCorrectionHighlightsMin"], s["ColorCorrectionHighlightsMax"], s["BlueCorrection"], s["ExpandGamut"])
    cb0[45, 0] = s["ToneCurveAmount"]
    white_balance = str(s.get("TemperatureType", "")).endswith("TEMP_WhiteBalance")
    cb0.view(np.uint32)[46, 3] = 1 if white_balance else 0
    cb0[47, 0:3] = (0.0, 1.0, 0.0)                     # mapping polynomial (r.Color.Min/Mid/Max defaults)
    cb0[48, 0:3] = (1.0 / 2.2, 1.0, 1.0)               # inverse gamma (display gamma 2.2)
    cb0.view(np.uint32)[49, 0] = 0                     # output gamut: sRGB
    return cb0


# ---------------------------------------------------------------------------
# Producer: instruction-order float32 port.
# ---------------------------------------------------------------------------
def _dot3(row, v):
    return (F32(row[0]) * v[0] + F32(row[1]) * v[1]) + F32(row[2]) * v[2]


def _mat3(rows, v):
    return [_dot3(r, v) for r in rows]


def _hex_rows(*rows):
    return [tuple(_h(b) for b in row) for row in rows]


_AP1_Y = _hex_rows((0x3E8B6190, 0x3F2C909F, 0x3D5BE98A))[0]
_EXPAND = _hex_rows((0x3FAF69AC, 0xBEA898FC, 0xBD826C4C), (0xBDAADF2E, 0x3F8C6D89, 0xBC31F3ED), (0xBCD34C84, 0xBDC9FC53, 0x3F9A12AD))
_BLUE = _hex_rows((0x3F704AAC, 0x2EE11750, 0x3D7B5547), (0x2EB7D719, 0x3F54AEED, 0x3E2D444E), (0x2C16046C, 0xACC63234, 0x3F800000))
_BLUE_INV = _hex_rows((0x3F885E34, 0x35C22F28, 0xBD85E13C), (0xB4B980AD, 0x3F9A11A5, 0xBE508E45), (0x32AAA495, 0x32B62CCB, 0x3F7FFFF9))
_REC709_Y = _hex_rows((0x3E59B3D0, 0x3F371759, 0x3D93DD98))[0]
_BRADFORD_ROWS = _hex_rows((0x3F652546, 0x3E886595, 0xBE25460B), (0xBF400D1B, 0x3FDB53F8, 0x3D1652BD), (0x3D1F559B, 0xBD8C49BA, 0x3F83C9EF))
_BRADFORD_INV_ROWS = _hex_rows((0x3F7CAB91, 0xBE169567, 0x3E23CD43), (0x3EDD571F, 0x3F04B343, 0x3D49E592), (0xBC0BBBF6, 0x3D2403EB, 0x3F77EEBF))


def _sat(x):
    """D3D saturate: NaN -> 0."""
    return np.clip(np.nan_to_num(x, nan=0.0, posinf=1.0, neginf=0.0), F32(0), F32(1)).astype(np.float32)


def _max(a, b):
    """D3D max: a NaN operand yields the other operand."""
    return np.fmax(a, b)


def _min(a, b):
    return np.fmin(a, b)


def _exp2(x):
    return np.exp2(x.astype(np.float32) if isinstance(x, np.ndarray) else F32(x))


def _log2(x):
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log2(x.astype(np.float32) if isinstance(x, np.ndarray) else F32(x))


def _pow(x, y):
    return _exp2(_log2(x) * y)


def _white_balance_matrix(temp: np.float32, tint: np.float32, white_balance: bool, cb1: np.ndarray):
    """Instructions 28-136: FromXYZ * CAT(src->dst) * ToXYZ, in float32."""
    with np.errstate(all="ignore"):
        inv_t = F32(1) / (_h(0x3F80123B) * temp)
        x1 = ((-_h(0x4F894C9F) * inv_t + _h(0x4A3523E0)) * inv_t + _h(0x42C63852)) * inv_t + _h(0x3E79EBA7)
        x2 = ((-_h(0x4EEF2E78) * inv_t + _h(0x49E82740)) * inv_t + _h(0x43777AE1)) * inv_t + _h(0x3E72BA9D)
        x = x1 if _h(0x45DAA0DD) >= temp else x2
        y = (x * x) * _h(0xC0400000) + x * _h(0x4037AE14)
        y = y + _h(0xBE8CCCCD)
        t2 = temp * temp
        un = temp * _h(0x39219ACE) + _h(0x3F5C30AD) + t2 * _h(0x340A20A1)
        ud_ = temp * _h(0x3A5CD5DD) + _h(0x3F800000) + t2 * _h(0x353E175D)
        vn = temp * _h(0x3831567A) + _h(0x3EA28216) + t2 * _h(0x33349873)
        u = un / ud_
        r5x = -temp * _h(0x37F30D95) + _h(0x3F800000)
        r5y = -temp * _h(0x49E9E7E2) + _h(0xCE879C39)
        r5w = -temp * _h(0x492C48A0) + _h(0x4EEB6788)
        vd = t2 * _h(0x342D5CB4) + r5x
        v = vn / vd
        den = (u + u) - v * _h(0x41000000) + _h(0x40800000)
        px, py = (u * _h(0x40400000)) / den, (v + v) / den
        if temp < _h(0x457A0000):
            x, y = px, py
        nu = -t2 * _h(0x3FC4110A) + r5y
        nv = -t2 * _h(0x439A4DB2) + r5w
        du = (temp * _h(0x4494B3D7) + t2 + _h(0x49AC615F)) ** 2
        dv = (-temp * _h(0x433374BC) + t2 + _h(0x4ABD03C8)) ** 2
        gu, gv = nu / du, nv / dv
        rs = F32(1) / np.sqrt(gu * gu + gv * gv)
        nu_, nv_ = rs * gu, rs * gv
        uu = (nv_ * tint) * _h(0x3D4CCCCD) + u
        vv = (-nu_ * tint) * _h(0x3D4CCCCD) + v
        den = (uu + uu) - vv * _h(0x41000000) + _h(0x40800000)
        tx, ty = (uu * _h(0x40400000)) / den, (vv + vv) / den
        wx, wy = (tx - px) + x, (ty - py) + y
        d65 = (_h(0x3EA01A37), _h(0x3EA872B0))
        (sx, sy), (dx, dy) = ((wx, wy), d65) if white_balance else (d65, (wx, wy))
        sy_, dy_ = max(sy, _h(0x2EDBE6FF)), max(dy, _h(0x2EDBE6FF))
        src = (sx / sy_, F32(1), ((F32(1) - sx) - sy) / sy_)
        dst = (dx / dy_, F32(1), ((F32(1) - dx) - dy) / dy_)
        lms_s = [_dot3(r, src) for r in _BRADFORD_ROWS]
        lms_d = [_dot3(r, dst) for r in _BRADFORD_ROWS]
        scale = [lms_d[i] / lms_s[i] for i in range(3)]
        db = [[scale[i] * F32(_BRADFORD_ROWS[i][j]) for j in range(3)] for i in range(3)]
        cat = [[_dot3(_BRADFORD_INV_ROWS[i], (db[0][j], db[1][j], db[2][j])) for j in range(3)] for i in range(3)]
        to_xyz = [[F32(c) for c in cb1[k, 0:3]] for k in range(3)]
        from_xyz = [[F32(c) for c in cb1[4 + k, 0:3]] for k in range(3)]
        n = [[cat[i][1] * to_xyz[1][j] + cat[i][0] * to_xyz[0][j] + cat[i][2] * to_xyz[2][j] for j in range(3)] for i in range(3)]
        m = [[from_xyz[i][1] * n[1][j] + from_xyz[i][0] * n[0][j] + from_xyz[i][2] * n[2][j] for j in range(3)] for i in range(3)]
    return m


def _grade_region(c, y, cb0, k):
    """Instructions 164-186 for region base register k (28/33/38)."""
    sat = cb0[23] * cb0[k]
    con = cb0[24] * cb0[k + 1]
    gam = cb0[25] * cb0[k + 2]
    gain = cb0[26] * cb0[k + 3]
    off = cb0[27] + cb0[k + 4]
    out = []
    for i in range(3):
        v = F32(sat[3] * sat[i]) * (c[i] - y) + y
        v = _max(v, F32(0)) * _h(0x40B1C71C)
        v = _exp2(_log2(v) * F32(con[3] * con[i])) * _h(0x3E3851EC)
        v = _exp2(_log2(v) * (F32(1) / F32(gam[3] * gam[i])))
        out.append(v * F32(gain[3] * gain[i]) + F32(off[3] + off[i]))
    return out


def _srgb_encode(x):
    lo = x * _h(0x414EB852)
    hi = _exp2(_log2(x) * _h(0x3ED55555)) * _h(0x3F870A3D) + _h(0xBD6147AE)
    return np.where(x >= _h(0x3B4D2BED), hi, lo)


def _srgb_decode(x):
    hi = _exp2(_log2(np.abs(x) * _h(0x3F72A76E) + _h(0x3D558919)) * _h(0x4019999A))
    lo = x * _h(0x3D9E8391)
    return np.where(_h(0x3D25AEE6) < x, hi, lo)


def _sample_strip(strip: np.ndarray, u, v):
    """Bilinear, clamp addressing, texel centers at (k+0.5)/size; strip[row, col, rgba]."""
    h, w = strip.shape[0], strip.shape[1]
    x = u * F32(w) - F32(0.5)
    y = v * F32(h) - F32(0.5)
    x0 = np.floor(x); y0 = np.floor(y)
    # Hardware filtering uses 8 fractional bits for the bilinear weights.
    fx = (np.floor((x - x0) * 256.0 + 0.5) / 256.0)[..., None]
    fy = (np.floor((y - y0) * 256.0 + 0.5) / 256.0)[..., None]
    xi0 = np.clip(x0.astype(np.int64), 0, w - 1); xi1 = np.clip(x0.astype(np.int64) + 1, 0, w - 1)
    yi0 = np.clip(y0.astype(np.int64), 0, h - 1); yi1 = np.clip(y0.astype(np.int64) + 1, 0, h - 1)
    a = strip[yi0, xi0] * (1 - fx) + strip[yi0, xi1] * fx
    b = strip[yi1, xi0] * (1 - fx) + strip[yi1, xi1] * fx
    return (a * (1 - fy) + b * fy).astype(np.float32)


def neutral_strip() -> np.ndarray:
    """Identity RGBTable16x1: column = red + 16 * blue, row = green (top row first)."""
    col = np.arange(256)
    row = np.arange(16)
    strip = np.zeros((16, 256, 4), dtype=np.float32)
    strip[:, :, 0] = (col % 16)[None, :] / 15.0
    strip[:, :, 1] = row[:, None] / 15.0
    strip[:, :, 2] = (col // 16)[None, :] / 15.0
    strip[:, :, 3] = 1.0
    return strip


def strip_from_bgra8(data: bytes, width: int = 256, height: int = 16) -> np.ndarray:
    """Cooked PF_B8G8R8A8 texels (top row first) -> float RGBA strip."""
    px = np.frombuffer(data, dtype=np.uint8)[: width * height * 4].reshape(height, width, 4).astype(np.float32) / 255.0
    return px[:, :, [2, 1, 0, 3]].copy()


def build_combined_lut(cb0: np.ndarray, cb1: np.ndarray, strip: np.ndarray | None = None) -> np.ndarray:
    """Return LUT[blue, green, red, rgb] (float32), display-coded and divided by 1.05.

    ``strip`` is the authored RGBTable16x1 texture as float RGBA rows (16, 256, 4),
    top row first (texture v=0), raw texel/255 values. None = neutral strip.
    """
    n = int(cb0[43, 0])
    nm1 = F32(cb0[43, 0]) - F32(1)
    idx = np.arange(n, dtype=np.float32)
    # 0-6: q from texel centers; UV = (i+0.5)/N.
    uv = (idx + F32(0.5)) / F32(n)
    half = F32(0.5) / F32(cb0[43, 0])
    q = ((uv - half) * F32(cb0[43, 0])) / nm1
    qb, qg, qr = np.meshgrid(idx / nm1, q, q, indexing="ij")
    qb = qb.astype(np.float32)
    c = [_exp2((x + _h(0xBEDE378E)) * _h(0x41600000)) * _h(0x3E3851EC) + _h(0xBB2ED4E7) for x in (qr, qg, qb)]
    temp, tint = F32(cb0[43, 1]), F32(cb0[43, 2])
    if abs(temp - F32(6500.0)) > _h(0x322BCC77) or abs(tint) > _h(0x322BCC77):
        wb = _white_balance_matrix(temp, tint, bool(cb0.view(np.uint32)[46, 3]), cb1)
        c = [_dot3(row, c) for row in wb]
    # 142-162: working -> AP1, gamut expansion.
    ap1 = _mat3([cb1[8, 0:3], cb1[9, 0:3], cb1[10, 0:3]], c)
    with np.errstate(all="ignore"):
        y = _dot3(_AP1_Y, ap1)
        d = [ap1[i] / y + F32(-1) for i in range(3)]
        dist = _dot3((1.0, 1.0, 1.0), [d[i] * d[i] for i in range(3)])
        f1 = F32(1) - _exp2(dist * F32(-4))
        f0 = F32(1) - _exp2(((y * y) * F32(cb0[44, 3])) * F32(-4))
        amount = f0 * f1
    ex = _mat3(_EXPAND, ap1)
    c = [amount * (ex[i] - ap1[i]) + ap1[i] for i in range(3)]
    # 163-247: three luminance regions.
    y = _dot3(_AP1_Y, c)
    fs = _grade_region(c, y, cb0, 28)
    t = _sat(y * (F32(1) / F32(cb0[43, 3])))
    ws = -(t * F32(-2) + F32(3)) * (t * t) + F32(1)
    fh = _grade_region(c, y, cb0, 38)
    th = _sat((F32(1) / (F32(cb0[44, 1]) - F32(cb0[44, 0]))) * (y - F32(cb0[44, 0])))
    wh = (th * th) * (th * F32(-2) + F32(3))
    fm = _grade_region(c, y, cb0, 33)
    wm = -wh + (F32(1) - ws)
    c = [fh[i] * wh + (fs[i] * ws + wm * fm[i]) for i in range(3)]
    # 248-252: blue correction.
    bc = F32(cb0[44, 2])
    b = _mat3(_BLUE, c)
    c = [bc * (b[i] - c[i]) + c[i] for i in range(3)]
    # 253-276: Embark exponential tone operator.
    x = [_max(ci, F32(0)) for ci in c]
    yy = _dot3(_REC709_Y, x)
    k = _h(0xBFB8AA3B)
    sc = (F32(1) - _exp2(yy * k)) / _max(yy, _h(0x3727C5AC))
    u_ = [sc * xi for xi in x]
    v_ = [F32(1) - _exp2(xi * k) for xi in x]
    spread = _max(_max(x[1], x[0]), x[2]) - _min(_min(x[1], x[0]), x[2])
    hmix = F32(1) - _exp2(spread * k)
    hmix = hmix * hmix
    tone = [hmix * (v_[i] - sc * x[i]) + u_[i] for i in range(3)]
    tca = F32(cb0[45, 0])
    c = [tca * (tone[i] - c[i]) + c[i] for i in range(3)]
    b = _mat3(_BLUE_INV, c)
    c = [bc * (b[i] - c[i]) + c[i] for i in range(3)]
    # 282-291: AP1 -> working (saturated), sRGB encode.
    w = [_sat(v) for v in _mat3([cb1[12, 0:3], cb1[13, 0:3], cb1[14, 0:3]], c)]
    e = [_srgb_encode(v) for v in w]
    # 292-304: authored strip at the encoded stage, blended by the LUT weights.
    pr, pg, pb = [ei * _h(0x3F700000) + _h(0x3D000000) for ei in e]
    s16 = pb * _h(0x41800000) + _h(0xBF000000)
    i0 = np.floor(s16)
    frac = s16 - i0
    u0 = (i0 + pr) * _h(0x3D800000)
    if strip is None:
        strip = neutral_strip()
    s0 = _sample_strip(strip, u0, pg)
    s1 = _sample_strip(strip, u0 + _h(0x3D800000), pg)
    sample = [frac * (s1[..., i] - s0[..., i]) + s0[..., i] for i in range(3)]
    w0, w1 = F32(cb0[5, 0]), F32(cb0[5, 1])
    c = [w0 * e[i] + sample[i] * w1 for i in range(3)]
    c = [_srgb_decode(v) for v in c]
    # 312-322: mapping polynomial, color scale/overlay, gamma.
    p = cb0[47]
    c = [F32(p[0]) * (v * v) + v * F32(p[1]) + F32(p[2]) for v in c]
    cs, ov = cb0[22], cb0[21]
    c = [F32(ov[3]) * (-c[i] * F32(cs[i]) + F32(ov[i])) + c[i] * F32(cs[i]) for i in range(3)]
    c = [_exp2(_log2(_max(v, F32(0))) * F32(cb0[48, 1])) for v in c]
    # 323-329: output gamut (bypassed for an sRGB working space).
    if not cb1.view(np.uint32)[24, 0]:
        raise NotImplementedError("Only the sRGB working space (output gamut bypass) is ported")
    # 330-337: sRGB encode, divide by the consumer gain.
    out = [_srgb_encode(v) * _h(0x3F73CF3E) for v in c]
    return np.stack(out, axis=-1).astype(np.float32)


def combined_lut(settings: Mapping[str, Any], strip: np.ndarray | None = None, lut_size: int = LUT_SIZE) -> np.ndarray:
    """Convenience: resolved settings (+ authored strip) -> LUT[b, g, r, rgb]."""
    return build_combined_lut(producer_constants(settings, lut_size), working_space_constants(), strip)


# ---------------------------------------------------------------------------
# Consumer reference (CPU), for tests and offline grading.
# ---------------------------------------------------------------------------
def sample_lut_trilinear(lut: np.ndarray, coords: np.ndarray) -> np.ndarray:
    """coords[..., (r,g,b)] in [0,1]; texel-center trilinear like a Texture3D."""
    n = lut.shape[0]
    p = np.clip(coords * F32(n) - F32(0.5), 0, n - 1)
    i0 = np.floor(p).astype(np.int64)
    i1 = np.minimum(i0 + 1, n - 1)
    f = (p - i0).astype(np.float32)
    out = np.zeros(coords.shape[:-1] + (3,), dtype=np.float32)
    for db in (0, 1):
        for dg in (0, 1):
            for dr in (0, 1):
                bi = i1[..., 2] if db else i0[..., 2]
                gi = i1[..., 1] if dg else i0[..., 1]
                ri = i1[..., 0] if dr else i0[..., 0]
                wgt = (f[..., 2] if db else 1 - f[..., 2]) * (f[..., 1] if dg else 1 - f[..., 1]) * (f[..., 0] if dr else 1 - f[..., 0])
                out += lut[bi, gi, ri] * wgt[..., None]
    return out


def tonemap(linear_rgb: np.ndarray, lut: np.ndarray, exposure: float = 1.0) -> np.ndarray:
    """Scene-linear RGB -> display-coded sRGB (game consumer, no dither/bloom/vignette)."""
    n = lut.shape[0]
    h = np.asarray(linear_rgb, dtype=np.float32) * F32(exposure)
    with np.errstate(divide="ignore", invalid="ignore"):
        q = _sat(np.log2(h + LOG_EPS) * LOG_SCALE + LOG_OFFSET)
    coords = q * F32((n - 1) / n) + F32(0.5 / n)
    return sample_lut_trilinear(lut, coords) * CONSUMER_GAIN


def srgb_to_linear(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
