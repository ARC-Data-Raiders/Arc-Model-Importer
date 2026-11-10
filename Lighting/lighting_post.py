"""Arc Raiders post-process chain in the Blender compositor.

Mirrors the game's tonemapper (consumer shader 10EE0D70) around the combined
32^3 LUT that ``lighting_grade`` reproduces from the combine-LUTs shader:

    X = scene * exposure            (eye adaptation, steady state)
    Y = X * local(X) + bloom(X)     (local exposure on the scene only; Glare bloom)
    H = Y * vignette                (cos^4 mask in aspect-corrected circle space)
    q = saturate(log2(H + 0.0026677) / 14 + 0.6107269)
    display = min(LUT3D(q) * 1.05, 1)   -> sRGB decode -> Standard view re-encodes

Blender pixel values are treated as Unreal nits (see ``lighting_sky``). The LUT,
exposure and local-exposure math are exact; bloom, the metering downsample and
the local-exposure base (Blur + Bilateral Blur standing in for the game's
blurred luminance and bilateral grid) are approximate.
"""
from __future__ import annotations

import math
from typing import Any

import bpy
import numpy as np

from . import lighting_grade as grade

GROUP_NAME = "ArcLightingLook"
LUT_GROUP_NAME = "ArcGameTonemapLUT"
LUT_IMAGE_NAME = "ArcLightingGameLUT"
STACK_VERSION = 13
LUT_SCHEMA = 1
REF_PASS_INDEX = 64  # reference_planes.REF_PASS_INDEX: UI art is already display-referred
# Weighted log-luminance is stored as w * (log2 L + _LOG_SHIFT) / _LOG_SPAN so the
# Levels mean never sees values outside [0, 1].
_LOG_SHIFT = 16.0
_LOG_SPAN = 40.0
_BLOOM_TINTS = (0.3465, 0.138, 0.1176, 0.066, 0.066, 0.061)


class _Graph:
    def __init__(self, tree):
        self.tree = tree
        self.nodes, self.links = tree.nodes, tree.links
        self.count = 0

    def node(self, kind, label, loc=None):
        node = self.nodes.new(kind)
        node.name = label
        node.label = label
        if loc is None:
            loc = ((self.count % 12) * 200, -(self.count // 12) * 180)
        node.location = loc
        self.count += 1
        return node

    def put(self, socket, value):
        if hasattr(value, "node"):
            self.links.new(value, socket)
        else:
            socket.default_value = value

    def math(self, op, a, b=0.0, c=None, label=""):
        n = self.node("ShaderNodeMath", label or op)
        n.operation = op
        self.put(n.inputs[0], a)
        self.put(n.inputs[1], b)
        if c is not None:
            self.put(n.inputs[2], c)
        return n.outputs[0]

    def clamp01(self, v):
        return self.math("MINIMUM", self.math("MAXIMUM", v, 0.0), 1.0)

    def lerp(self, a, b, t):
        return self.math("MULTIPLY_ADD", self.math("SUBTRACT", b, a), t, a)


def _rgba_in(node, name):
    return next(s for s in node.inputs if s.name == name and s.type == "RGBA")


def _rgba_out(node):
    return next(s for s in node.outputs if s.type == "RGBA")


def _find(nodes, label):
    return next((n for n in nodes if n.label == label), None)


# ---------------------------------------------------------------------------
# LUT image + sampler group
# ---------------------------------------------------------------------------
def ensure_lut_image(lut: np.ndarray, identity: str) -> bpy.types.Image:
    n = int(lut.shape[0])
    img = bpy.data.images.get(LUT_IMAGE_NAME)
    if img is not None and (tuple(img.size) != (n * n, n) or not img.is_float):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(LUT_IMAGE_NAME, width=n * n, height=n, alpha=True, float_buffer=True)
    if img.get("arc_game_lut") == identity and img.get("arc_lut_schema") == LUT_SCHEMA:
        return img
    img.colorspace_settings.name = "Non-Color"
    img.alpha_mode = "CHANNEL_PACKED"
    img.pixels.foreach_set(grade.lut_unwrapped_rgba(lut))
    img.update()
    img.pack()
    img["arc_game_lut"] = identity
    img["arc_lut_schema"] = LUT_SCHEMA
    img.use_fake_user = True
    return img


def _decode_srgb(g: _Graph, x):
    low = g.math("MULTIPLY", x, float(grade._h(0x3D9E8391)))
    high = g.math("POWER", g.math("MULTIPLY_ADD", g.math("ABSOLUTE", x), float(grade._h(0x3F72A76E)),
                                  float(grade._h(0x3D558919))), float(grade._h(0x4019999A)))
    return g.lerp(low, high, g.math("GREATER_THAN", x, float(grade._h(0x3D25AEE6))))


def ensure_lut_group(image: bpy.types.Image) -> bpy.types.NodeTree:
    """Group: straight linear RGB (H) -> linear display-referred RGB, alpha passed through."""
    n = int(image.size[1])
    tree = bpy.data.node_groups.get(LUT_GROUP_NAME)
    if tree is not None and tree.get("arc_lut_size") == n and tree.get("arc_lut_schema") == LUT_SCHEMA:
        img_node = _find(tree.nodes, "Game LUT image")
        if img_node is not None:
            img_node.image = image
            return tree
    if tree is not None:
        bpy.data.node_groups.remove(tree)
    tree = bpy.data.node_groups.new(LUT_GROUP_NAME, "CompositorNodeTree")
    tree.interface.new_socket(name="Image", in_out="INPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    g = _Graph(tree)
    gi = g.node("NodeGroupInput", "Linear exposed scene (H)")
    go = g.node("NodeGroupOutput", "Linear display-referred")
    sep = g.node("CompositorNodeSeparateColor", "H channels")
    g.put(sep.inputs["Image"], gi.outputs["Image"])
    eps, scale, offset = float(grade.LOG_EPS), float(grade.LOG_SCALE), float(grade.LOG_OFFSET)
    texel = []
    for ch in ("Red", "Green", "Blue"):
        h = g.math("MAXIMUM", sep.outputs[ch], 0.0, label=f"{ch} H>=0")
        lg = g.math("LOGARITHM", g.math("ADD", h, eps), 2.0, label=f"{ch} log2(H+eps)")
        q = g.clamp01(g.math("MULTIPLY_ADD", lg, scale, offset, label=f"{ch} shaper"))
        texel.append(g.math("MULTIPLY", q, float(n - 1), label=f"{ch} texel"))
    red, green, blue = texel
    lower = g.math("FLOOR", blue, label="blue lower slice")
    upper = g.math("MINIMUM", g.math("ADD", lower, 1.0), float(n - 1), label="blue upper slice")
    frac = g.math("SUBTRACT", blue, lower, label="blue fraction")
    v = g.math("DIVIDE", g.math("ADD", green, 0.5), float(n), label="v")
    lut = g.node("CompositorNodeImage", "Game LUT image")
    lut.image = image
    samples = []
    for label, sl in (("lower", lower), ("upper", upper)):
        u = g.math("DIVIDE", g.math("ADD", g.math("MULTIPLY_ADD", sl, float(n), red), 0.5), float(n * n), label=f"u {label}")
        uv = g.node("ShaderNodeCombineXYZ", f"UV {label} (Z = coverage)")
        g.put(uv.inputs["X"], u)
        g.put(uv.inputs["Y"], v)
        uv.inputs["Z"].default_value = 1.0
        mu = g.node("CompositorNodeMapUV", f"Bilinear {label} slice")
        g.put(mu.inputs["Image"], lut.outputs["Image"])
        g.put(mu.inputs["UV"], uv.outputs["Vector"])
        try:
            mu.inputs["Interpolation"].default_value = "Bilinear"
        except Exception:
            pass
        s = g.node("CompositorNodeSeparateColor", f"{label} sample")
        g.put(s.inputs["Image"], mu.outputs["Image"])
        samples.append(s)
    comb = g.node("CompositorNodeCombineColor", "Display-decoded RGB")
    gain = float(grade.CONSUMER_GAIN)
    for ch in ("Red", "Green", "Blue"):
        tri = g.lerp(samples[0].outputs[ch], samples[1].outputs[ch], frac)
        disp = g.clamp01(g.math("MULTIPLY", tri, gain, label=f"{ch} x1.05"))
        g.put(comb.inputs[ch], _decode_srgb(g, disp))
    g.put(comb.inputs["Alpha"], sep.outputs["Alpha"])
    g.put(go.inputs["Image"], comb.outputs["Image"])
    tree["arc_lut_size"] = n
    tree["arc_lut_schema"] = LUT_SCHEMA
    return tree


# ---------------------------------------------------------------------------
# Main stack
# ---------------------------------------------------------------------------
def _set_curve(node, samples):
    mapping = node.mapping
    curve = mapping.curves[0]
    xs = [p[0] for p in samples]
    ys = [p[1] for p in samples]
    mapping.use_clip = True
    mapping.clip_min_x, mapping.clip_max_x = min(xs), max(xs)
    mapping.clip_min_y, mapping.clip_max_y = min(ys) - 1.0, max(ys) + 1.0
    mapping.extend = "HORIZONTAL"
    while len(curve.points) > 2:
        curve.points.remove(curve.points[1])
    curve.points[0].location = samples[0]
    curve.points[-1].location = samples[-1]
    for x, y in samples[1:-1]:
        curve.points.new(x, y)
    for p in curve.points:
        p.handle_type = "VECTOR"
    mapping.update()


_LE_VALUES = ("Enable", "HighlightContrastScale", "ShadowContrastScale", "DetailStrength", "BlurredLuminanceBlend",
              "BlurredLuminanceKernelSizePercent", "MiddleGreyBias", "HighlightThreshold", "ShadowThreshold",
              "HighlightThresholdStrength", "ShadowThresholdStrength", "MiddleGreyManual")
# Bilateral grid stand-in: edge-aware box over log luminance. Range threshold =
# one grid slice (32 slices over the default -10..20 EV histogram range).
LE_GRID_THRESHOLD = 30.0 / 32.0
LE_GRID_RADIUS_DIVISOR = 64.0
LE_GRID_RADIUS_MAX = 16.0


def _local_exposure(g: _Graph, rl, *, logl, exposure, total_bias, use_auto):
    """Per-pixel local exposure factor (lighting_grade.local_exposure_factor in nodes).

    The game's Base term lerps a bilateral-grid lookup and a Gaussian-blurred log
    luminance. Here the blurred term is a Blur node on log luminance and the
    bilateral grid is approximated by a Bilateral Blur node on the same image.
    """
    v = {k: g.node("ShaderNodeValue", "ArcLocalExposure" + k, (-1400 + 120 * (i % 6), 500 + 120 * (i // 6))).outputs[0]
         for i, k in enumerate(_LE_VALUES)}
    info = g.node("CompositorNodeImageInfo", "ArcLocalExposureInfo", (-1400, 300))
    g.put(info.inputs["Image"], rl.outputs["Image"])
    res = g.node("ShaderNodeSeparateXYZ", "ArcLocalExposureResolution", (-1250, 300))
    g.put(res.inputs[0], info.outputs["Resolution"])
    radius = g.math("MULTIPLY", res.outputs["X"], g.math("MULTIPLY", v["BlurredLuminanceKernelSizePercent"], 0.005),
                    label="ArcLocalExposureBlurRadius")
    size = g.node("ShaderNodeCombineXYZ", "ArcLocalExposureBlurSize", (-1100, 300))
    g.put(size.inputs["X"], radius)
    g.put(size.inputs["Y"], radius)
    blur = g.node("CompositorNodeBlur", "ArcLocalExposureBlur", (-950, 300))
    g.put(blur.inputs["Image"], logl)
    g.put(blur.inputs["Size"], size.outputs["Vector"])
    grid = g.node("CompositorNodeBilateralblur", "ArcLocalExposureGrid", (-950, 150))
    g.put(grid.inputs["Image"], logl)
    g.put(grid.inputs["Determinator"], logl)
    g.put(grid.inputs["Size"], g.math("MINIMUM", g.math("ROUND", g.math("DIVIDE", res.outputs["X"], LE_GRID_RADIUS_DIVISOR)),
                                      LE_GRID_RADIUS_MAX, label="ArcLocalExposureGridRadius"))
    grid.inputs["Threshold"].default_value = LE_GRID_THRESHOLD
    blurred = g.node("CompositorNodeSeparateColor", "ArcLocalExposureBlurred", (-800, 300))
    g.put(blurred.inputs["Image"], blur.outputs["Image"])
    gridded = g.node("CompositorNodeSeparateColor", "ArcLocalExposureGridded", (-800, 150))
    g.put(gridded.inputs["Image"], grid.outputs["Image"])
    base0 = g.lerp(gridded.outputs["Red"], blurred.outputs["Red"], v["BlurredLuminanceBlend"])
    base0.node.label = base0.node.name = "ArcLocalExposureBaseLog"

    log_e = g.math("LOGARITHM", g.math("MAXIMUM", exposure, 1e-30), 2.0, label="ArcLocalExposureLogE")
    log_lum = g.math("ADD", logl, log_e, label="ArcLocalExposureLogLum")
    base = g.math("ADD", base0, log_e, label="ArcLocalExposureBase")
    detail = g.math("SUBTRACT", log_lum, base, label="ArcLocalExposureDetail")
    mg_auto = g.math("ADD", g.math("ADD", total_bias, math.log2(grade.MIDDLE_GREY)), v["MiddleGreyBias"])
    mg = g.lerp(v["MiddleGreyManual"], mg_auto, use_auto)
    mg.node.label = mg.node.name = "ArcLocalExposureMiddleGrey"
    b = g.math("SUBTRACT", base, mg, label="ArcLocalExposureB")
    pos = g.math("GREATER_THAN", b, 0.0, label="ArcLocalExposureHighlight")
    scale = g.lerp(v["ShadowContrastScale"], v["HighlightContrastScale"], pos)
    abs_b = g.math("ABSOLUTE", b)

    def smooth(threshold, strength):
        t = g.clamp01(g.math("MULTIPLY", g.math("SUBTRACT", 1.0, strength), g.math("SUBTRACT", abs_b, threshold)))
        return g.math("MULTIPLY", g.math("MULTIPLY", t, t), g.math("MULTIPLY_ADD", t, -2.0, 3.0))

    s = g.lerp(smooth(v["ShadowThreshold"], v["ShadowThresholdStrength"]),
               smooth(v["HighlightThreshold"], v["HighlightThresholdStrength"]), pos)
    e = g.lerp(g.math("MINIMUM", g.math("ADD", b, v["ShadowThreshold"]), 0.0),
               g.math("MAXIMUM", g.math("SUBTRACT", b, v["HighlightThreshold"]), 0.0), pos)
    dk = g.math("MULTIPLY", g.math("SUBTRACT", b, e), g.math("SUBTRACT", 1.0, s), label="ArcLocalExposureKept")
    local = g.math("MULTIPLY_ADD", detail, v["DetailStrength"],
                   g.math("MULTIPLY_ADD", g.math("SUBTRACT", b, dk), scale, g.math("ADD", mg, dk)),
                   label="ArcLocalExposureLocalLog")
    f = g.math("POWER", 2.0, g.math("SUBTRACT", local, log_lum), label="ArcLocalExposureRaw")
    factor = g.lerp(1.0, f, v["Enable"])
    factor.node.label = factor.node.name = "ArcLocalExposureFactor"
    return factor


def _build_stack(scene, tree, lut_group):
    tree.nodes.clear()
    g = _Graph(tree)
    rl = g.node("CompositorNodeRLayers", "ArcLightingRenderLayers", (-1800, 0))
    rl.scene = scene
    sep = g.node("CompositorNodeSeparateColor", "ArcLightingSceneChannels", (-1600, 0))
    g.put(sep.inputs["Image"], rl.outputs["Image"])
    alpha = sep.outputs["Alpha"]
    inv_a = g.math("DIVIDE", 1.0, g.math("MAXIMUM", alpha, 1e-8), label="ArcLightingUnpremultiply")
    straight = [g.math("MULTIPLY", sep.outputs[c], inv_a, label=f"ArcLightingStraight{c}") for c in ("Red", "Green", "Blue")]

    # --- metering (AEM_Basic steady state) ---
    lum = g.math("DIVIDE", g.math("ADD", g.math("ADD", straight[0], straight[1]), straight[2]), 3.0, label="ArcMeterLuminance")
    logl = g.math("LOGARITHM", g.math("MAXIMUM", lum, grade.METER_LUMINANCE_MIN), 2.0, label="ArcMeterLog2")
    mask = g.node("CompositorNodeImage", "ArcMeterMaskImage", (-1600, -700))
    coords = g.node("CompositorNodeImageCoordinates", "ArcMeterCoords", (-1600, -500))
    g.put(coords.inputs["Image"], rl.outputs["Image"])
    mask_uv = g.node("CompositorNodeMapUV", "ArcMeterMaskSample", (-1400, -600))
    g.put(mask_uv.inputs["Image"], mask.outputs["Image"])
    sep_uv = g.node("ShaderNodeSeparateXYZ", "ArcMeterUV", (-1500, -500))
    g.put(sep_uv.inputs[0], coords.outputs["Normalized"])
    uvz = g.node("ShaderNodeCombineXYZ", "ArcMeterUVOpaque", (-1450, -560))
    g.put(uvz.inputs["X"], sep_uv.outputs["X"])
    g.put(uvz.inputs["Y"], sep_uv.outputs["Y"])
    uvz.inputs["Z"].default_value = 1.0
    g.put(mask_uv.inputs["UV"], uvz.outputs["Vector"])
    mask_ch = g.node("CompositorNodeSeparateColor", "ArcMeterMaskChannels", (-1250, -600))
    g.put(mask_ch.inputs["Image"], mask_uv.outputs["Image"])
    use_mask = g.node("ShaderNodeValue", "ArcMeterUseMask", (-1250, -760))
    mask_w = g.lerp(1.0, mask_ch.outputs["Red"], use_mask.outputs[0])
    weight = g.math("MULTIPLY", mask_w, g.math("GREATER_THAN", alpha, 0.0), label="ArcMeterWeight")
    shifted = g.math("DIVIDE", g.math("MULTIPLY", weight, g.math("ADD", logl, _LOG_SHIFT)), _LOG_SPAN, label="ArcMeterWeightedLog")
    lv_log = g.node("CompositorNodeLevels", "ArcMeterMeanLog", (-1000, -500))
    g.put(lv_log.inputs["Image"], shifted)
    lv_w = g.node("CompositorNodeLevels", "ArcMeterMeanWeight", (-1000, -680))
    g.put(lv_w.inputs["Image"], weight)
    mean_log = g.math("SUBTRACT", g.math("DIVIDE", g.math("MULTIPLY", lv_log.outputs["Mean"], _LOG_SPAN),
                                         g.math("MAXIMUM", lv_w.outputs["Mean"], 1e-6)), _LOG_SHIFT, label="ArcMeterLogAverage")
    # White point = average / 0.18; EV100 and the brightness clamp refer to it.
    ev100 = g.math("SUBTRACT", mean_log, math.log2(grade.EV100_LUMINANCE * grade.MIDDLE_GREY), label="ArcMeterEV100")
    curve = g.node("ShaderNodeFloatCurve", "ArcExposureBiasCurve", (-800, -560))
    g.put(curve.inputs["Value"], ev100)
    curve.inputs["Factor"].default_value = 1.0
    use_curve = g.node("ShaderNodeValue", "ArcExposureUseCurve", (-800, -720))
    bias = g.node("ShaderNodeValue", "ArcExposureBias", (-800, -820))
    lmin = g.node("ShaderNodeValue", "ArcExposureMinLum", (-800, -900))
    lmax = g.node("ShaderNodeValue", "ArcExposureMaxLum", (-800, -980))
    total_bias = g.math("ADD", bias.outputs[0], g.math("MULTIPLY", curve.outputs["Value"], use_curve.outputs[0]), label="ArcExposureTotalBias")
    white = g.math("DIVIDE", g.math("POWER", 2.0, mean_log), grade.MIDDLE_GREY, label="ArcMeterWhitePoint")
    clamped = g.math("MINIMUM", g.math("MAXIMUM", white, lmin.outputs[0]), lmax.outputs[0], label="ArcExposureClampedWhite")
    auto_e = g.math("DIVIDE", g.math("POWER", 2.0, total_bias), clamped, label="ArcExposureAuto")
    manual_e = g.node("ShaderNodeValue", "ArcExposureManual", (-600, -760))
    use_auto = g.node("ShaderNodeValue", "ArcExposureUseAuto", (-600, -860))
    exposure = g.lerp(manual_e.outputs[0], auto_e, use_auto.outputs[0])
    exposure.node.label = exposure.node.name = "ArcExposureScale"

    # --- local exposure (tonemapper permutation AEB6EEB6…), scene only, not bloom ---
    factor = _local_exposure(g, rl, logl=logl, exposure=exposure, total_bias=total_bias,
                             use_auto=use_auto.outputs[0])

    # --- exposure, bloom, vignette ---
    exposed = g.node("CompositorNodeCombineColor", "ArcLightingExposed", (-400, 0))
    local = g.node("CompositorNodeCombineColor", "ArcLightingLocalExposed", (-400, 200))
    for i, c in enumerate(("Red", "Green", "Blue")):
        x = g.math("MULTIPLY", straight[i], exposure, label=f"ArcExposed{c}")
        g.put(exposed.inputs[c], x)
        g.put(local.inputs[c], g.math("MULTIPLY", x, factor, label=f"ArcLocalExposed{c}"))
    exposed.inputs["Alpha"].default_value = 1.0
    local.inputs["Alpha"].default_value = 1.0
    glare = g.node("CompositorNodeGlare", "ArcLightingBloom", (-200, 0))
    g.put(glare.inputs["Image"], exposed.outputs["Image"])
    for name, value in (("Type", "Bloom"), ("Threshold", 0.0), ("Smoothness", 0.0), ("Clamp", False),
                        ("Strength", 1.0), ("Saturation", 1.0), ("Size", 1.0), ("Quality", "High")):
        try:
            glare.inputs[name].default_value = value
        except Exception:
            pass
    bloom_amt = g.node("ShaderNodeValue", "ArcBloomAmount", (-200, -200))
    bloomed = g.node("ShaderNodeMix", "ArcLightingBloomAdd", (0, 0))
    bloomed.data_type = "RGBA"
    bloomed.blend_type = "ADD"
    bloomed.clamp_result = False
    g.put(bloomed.inputs["Factor"], bloom_amt.outputs[0])
    g.put(_rgba_in(bloomed, "A"), local.outputs["Image"])
    g.put(_rgba_in(bloomed, "B"), glare.outputs["Glare"])

    info = g.node("CompositorNodeImageInfo", "ArcVignetteInfo", (0, -300))
    g.put(info.inputs["Image"], rl.outputs["Image"])
    res = g.node("ShaderNodeSeparateXYZ", "ArcVignetteResolution", (150, -300))
    g.put(res.inputs[0], info.outputs["Resolution"])
    aspect = g.math("DIVIDE", res.outputs["X"], g.math("MAXIMUM", res.outputs["Y"], 1.0), label="ArcVignetteAspect")
    vig_int = g.node("ShaderNodeValue", "ArcVignetteIntensity", (150, -450))
    # sqrt(2 / (1 + 1/aspect^2)) * intensity
    scale = g.math("MULTIPLY", g.math("SQRT", g.math("DIVIDE", 2.0, g.math("ADD", 1.0, g.math("DIVIDE", 1.0,
                   g.math("MULTIPLY", aspect, aspect))))), vig_int.outputs[0], label="ArcVignetteScale")
    pos = g.node("ShaderNodeSeparateXYZ", "ArcVignettePos", (150, -150))
    g.put(pos.inputs[0], coords.outputs["Normalized"])
    vx = g.math("MULTIPLY", g.math("MULTIPLY_ADD", pos.outputs["X"], 2.0, -1.0), scale, label="ArcVignetteX")
    vy = g.math("MULTIPLY", g.math("DIVIDE", g.math("MULTIPLY_ADD", pos.outputs["Y"], 2.0, -1.0), aspect), scale, label="ArcVignetteY")
    tan2 = g.math("MULTIPLY_ADD", vx, vx, g.math("MULTIPLY", vy, vy))
    vig = g.math("POWER", g.math("DIVIDE", 1.0, g.math("ADD", tan2, 1.0)), 2.0, label="ArcVignetteMask")
    vig_sep = g.node("CompositorNodeSeparateColor", "ArcVignetteIn", (300, 0))
    g.put(vig_sep.inputs["Image"], _rgba_out(bloomed))
    h = g.node("CompositorNodeCombineColor", "ArcLightingH", (500, 0))
    for c in ("Red", "Green", "Blue"):
        g.put(h.inputs[c], g.math("MULTIPLY", vig_sep.outputs[c], vig, label=f"ArcVignette{c}"))
    h.inputs["Alpha"].default_value = 1.0

    lut = g.node("CompositorNodeGroup", "ArcLighting3DLUT", (700, 0))
    lut.node_tree = lut_group
    g.put(lut.inputs[0], h.outputs["Image"])
    out_sep = g.node("CompositorNodeSeparateColor", "ArcLightingGraded", (900, 0))
    g.put(out_sep.inputs["Image"], lut.outputs[0])
    premul = g.node("CompositorNodeCombineColor", "ArcLightingPremultiply", (1100, 0))
    for c in ("Red", "Green", "Blue"):
        g.put(premul.inputs[c], g.math("MULTIPLY", out_sep.outputs[c], alpha, label=f"ArcPremultiply{c}"))
    g.put(premul.inputs["Alpha"], alpha)

    id_mask = g.node("CompositorNodeCryptomatteV2", "ArcLightingRefMask", (900, -400))
    id_mask.scene = scene
    id_mask.layer_name = scene.view_layers[0].name + ".CryptoObject"
    g.put(id_mask.inputs["Image"], rl.outputs["Image"])
    hold = g.node("ShaderNodeMix", "ArcLightingRefHoldout", (1300, 0))
    hold.data_type = "RGBA"
    hold.clamp_factor = True
    hold.inputs["Factor"].default_value = 0.0
    g.put(_rgba_in(hold, "A"), premul.outputs["Image"])
    g.put(_rgba_in(hold, "B"), rl.outputs["Image"])
    go = g.node("NodeGroupOutput", "ArcLightingOutput", (1500, 0))
    g.put(go.inputs[0], _rgba_out(hold))
    viewer = g.node("CompositorNodeViewer", "ArcLightingViewer", (1500, -200))
    g.put(viewer.inputs["Image"], _rgba_out(hold))
    tree["arc_stack_version"] = STACK_VERSION


def ensure_stack(scene, lut_group) -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.get(GROUP_NAME)
    if tree is None or tree.bl_idname != "CompositorNodeTree":
        if tree is not None:
            bpy.data.node_groups.remove(tree)
        tree = bpy.data.node_groups.new(GROUP_NAME, "CompositorNodeTree")
    if not any(getattr(i, "in_out", "") == "OUTPUT" for i in tree.interface.items_tree):
        tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    if int(tree.get("arc_stack_version", 0) or 0) != STACK_VERSION or _find(tree.nodes, "ArcLighting3DLUT") is None:
        _build_stack(scene, tree, lut_group)
    rl = _find(tree.nodes, "ArcLightingRenderLayers")
    if rl is not None:
        rl.scene = scene
    return tree


def bloom_amount(settings: dict[str, Any]) -> float:
    """Gaussian chain energy: BloomIntensity * sum(tints) / stage count."""
    intensity = float(settings.get("BloomIntensity", 0.675) or 0.0)
    tints = []
    for i, default in enumerate(_BLOOM_TINTS, start=1):
        tint = settings.get(f"Bloom{i}Tint")
        if isinstance(tint, dict):
            tints.append((float(tint.get("R", default)) + float(tint.get("G", default)) + float(tint.get("B", default))) / 3.0)
        else:
            tints.append(default)
    return intensity * sum(tints) / len(_BLOOM_TINTS)


def configure(scene, settings: dict[str, Any], *, lut: np.ndarray, lut_identity: str, curve_keys=None,
              mask_image=None, auto_exposure: bool = True, manual_ev100: float = 2.0,
              compensation: float = 0.0, bloom: bool = True, vignette: bool = True,
              local_exposure: bool = True) -> dict[str, Any]:
    """Point the stack at one scenario. Returns the values it set (for status/tests)."""
    if "arc_lighting_compositor_precision" not in scene:
        scene["arc_lighting_compositor_precision"] = scene.render.compositor_precision
    scene.render.compositor_precision = "FULL"
    image = ensure_lut_image(lut, lut_identity)
    group = ensure_lut_group(image)
    tree = ensure_stack(scene, group)
    lut_node = _find(tree.nodes, "ArcLighting3DLUT")
    if lut_node.node_tree != group:
        lut_node.node_tree = group
    nodes = tree.nodes

    def value(label, v):
        node = _find(nodes, label)
        node.outputs[0].default_value = float(v)

    bias = float(settings.get("AutoExposureBias", grade.DEFAULTS["AutoExposureBias"]) or 0.0) + float(compensation)
    lmin = grade.EV100_LUMINANCE * 2.0 ** float(settings.get("AutoExposureMinBrightness", -10.0))
    lmax = grade.EV100_LUMINANCE * 2.0 ** float(settings.get("AutoExposureMaxBrightness", 20.0))
    value("ArcExposureBias", bias)
    value("ArcExposureMinLum", min(lmin, lmax))
    value("ArcExposureMaxLum", lmax)
    samples = grade.exposure_curve_samples(curve_keys or [])
    curve_node = _find(nodes, "ArcExposureBiasCurve")
    if samples:
        _set_curve(curve_node, samples)
    value("ArcExposureUseCurve", 1.0 if samples else 0.0)
    # Locked meter: the white point sits at the given EV100 (average = 18% of it).
    fixed = grade.target_exposure(grade.MIDDLE_GREY * grade.EV100_LUMINANCE * 2.0 ** float(manual_ev100),
                                  {**settings, "AutoExposureBias": bias}, curve_keys)
    value("ArcExposureManual", fixed)
    value("ArcExposureUseAuto", 1.0 if auto_exposure else 0.0)
    mask_node = _find(nodes, "ArcMeterMaskImage")
    mask_node.image = mask_image
    value("ArcMeterUseMask", 1.0 if mask_image is not None else 0.0)
    amount = bloom_amount(settings) if bloom else 0.0
    value("ArcBloomAmount", amount)
    glare = _find(nodes, "ArcLightingBloom")
    glare.mute = not bloom
    vig = float(settings.get("VignetteIntensity", 0.4) or 0.0) if vignette else 0.0
    value("ArcVignetteIntensity", vig)

    le = grade.local_exposure_params(settings)
    le_on = bool(local_exposure) and not grade.local_exposure_is_identity(le)
    for key in grade.LOCAL_EXPOSURE_KEYS:
        value("ArcLocalExposure" + key, le[key])
    locked_avg = grade.MIDDLE_GREY * grade.EV100_LUMINANCE * 2.0 ** float(manual_ev100)
    locked_ev = math.log2(fixed * grade.clamped_white(locked_avg, settings))
    value("ArcLocalExposureMiddleGreyManual", grade.local_exposure_middle_grey_log(locked_ev, le))
    value("ArcLocalExposureEnable", 1.0 if le_on else 0.0)
    for label in ("ArcLocalExposureBlur", "ArcLocalExposureGrid"):
        _find(nodes, label).mute = not le_on

    for layer in scene.view_layers:
        layer.use_pass_cryptomatte_object = True
        layer.use_pass_object_index = True
    id_mask = _find(nodes, "ArcLightingRefMask")
    if id_mask is not None:
        for name, v in (("Index", REF_PASS_INDEX), ("Anti-Alias", False)):
            if name in id_mask.inputs:
                id_mask.inputs[name].default_value = v
    scene.compositing_node_group = tree
    sync_reference_compositor_mask(scene)
    try:
        scene.render.use_compositing = True
    except Exception:
        pass
    tree["arc_lut_identity"] = lut_identity
    return {"bias": bias, "lmin": lmin, "lmax": lmax, "manual_exposure": fixed, "bloom": amount,
            "vignette": vig, "curve_points": len(samples), "local_exposure": le_on,
            "le_highlight": le["HighlightContrastScale"], "le_shadow": le["ShadowContrastScale"],
            "le_middle_grey_bias": le["MiddleGreyBias"]}


def apply_color_management(scene) -> None:
    """The stack outputs linear values that decode the game's display encoding; Standard re-encodes them."""
    vs, ds = scene.view_settings, scene.display_settings
    if "arc_lighting_cm_view" not in scene:
        scene["arc_lighting_cm_view"] = vs.view_transform
        scene["arc_lighting_cm_look"] = vs.look
        scene["arc_lighting_cm_exposure"] = vs.exposure
        scene["arc_lighting_cm_gamma"] = vs.gamma
    try:
        ds.display_device = "sRGB"
    except Exception:
        pass
    vs.view_transform = "Standard"
    vs.look = "None"
    vs.exposure = 0.0
    vs.gamma = 1.0
    vs.use_curve_mapping = False
    try:
        vs.use_white_balance = False
    except Exception:
        pass


def restore_color_management(scene) -> None:
    vs = scene.view_settings
    for key, attr in (("arc_lighting_cm_view", "view_transform"), ("arc_lighting_cm_look", "look"),
                      ("arc_lighting_cm_exposure", "exposure"), ("arc_lighting_cm_gamma", "gamma")):
        if key in scene:
            try:
                setattr(vs, attr, scene[key])
            except Exception:
                pass
            del scene[key]


def detach(scene) -> None:
    """Stop using the stack for this scene (leaves the node group for reuse)."""
    if "arc_lighting_compositor_precision" in scene:
        scene.render.compositor_precision = scene["arc_lighting_compositor_precision"]
        del scene["arc_lighting_compositor_precision"]
    if scene.compositing_node_group is not None and scene.compositing_node_group.name == GROUP_NAME:
        scene.compositing_node_group = None
    restore_color_management(scene)


def sync_reference_compositor_mask(scene=None):
    scene = scene or bpy.context.scene
    tree = scene.compositing_node_group
    if tree is None or not tree.get("arc_stack_version"):
        return
    mask = _find(tree.nodes, "ArcLightingRefMask")
    if mask is None or mask.bl_idname != "CompositorNodeCryptomatteV2":
        return
    mask.scene = scene
    mask.layer_name = scene.view_layers[0].name + ".CryptoObject"
    mask.matte_id = ", ".join(o.name for o in scene.objects if o.get("arc_reference_plane"))
    hold = _find(tree.nodes, "ArcLightingRefHoldout")
    if hold is None:
        return
    # Restore the original pixel wherever reference coverage is nonzero; a
    # fractional mix would grade translucent artwork edges a second time.
    coverage = _find(tree.nodes, "ArcLightingRefCoverage")
    if coverage is None:
        coverage = tree.nodes.new("ShaderNodeMath")
        coverage.label = coverage.name = "ArcLightingRefCoverage"
        coverage.location = (hold.location.x - 190, hold.location.y - 240)
    coverage.operation = "GREATER_THAN"
    coverage.inputs[1].default_value = 0.0
    factor = hold.inputs.get("Factor") or hold.inputs.get("Fac")
    tree.links.new(mask.outputs["Matte"], coverage.inputs[0])
    tree.links.new(coverage.outputs[0], factor)
    tree["arc_reference_coverage_revision"] = 1
