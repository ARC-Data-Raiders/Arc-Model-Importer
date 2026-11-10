"""One exposure factor from game emissive units to Blender Emission Strength.

UE renders character cards with eye adaptation; Blender has none, so authored
emissive strengths (CRT Intensity, garment Base/Crease/EdgeEmissiveStrength)
clip to white. Every character emission builder multiplies by this factor.
DRB_EMISSIVE_EXPOSURE overrides it for calibration runs only.
"""
from __future__ import annotations

import os

# Garment zones (Base/Crease/EdgeEmissiveStrength x OverallEmissive).
CHARACTER_EMISSIVE_EXPOSURE = 1.0
# CRT screens (max(Intensity, ScreenBacklight)). 0.5 matched ANGLER and DWELLER visor L* within 1 (2026-11-08,
# fixes/lights/tools/emis_cmp.py over the pixels that respond to exposure).
CRT_EMISSIVE_EXPOSURE = 0.5


def _env(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    try:
        return max(0.0, float(raw)) if raw else default
    except ValueError:
        return default


def character_emissive_exposure() -> float:
    return _env("DRB_EMISSIVE_EXPOSURE", CHARACTER_EMISSIVE_EXPOSURE)


def crt_emissive_exposure() -> float:
    return _env("DRB_CRT_EXPOSURE", CRT_EMISSIVE_EXPOSURE)


# Highlight knee for screen emission: max channel stays linear up to KNEE, then rolls off toward 1.0.
# Blender's Standard view clips each channel at 1, so bright CRT text (teal x strength 2.5-5) turned cyan-white;
# the cards keep it green. Scaling all three channels by one ratio keeps the hue.
CRT_HIGHLIGHT_KNEE = 0.6


def hue_preserving_knee(nodes, links, colour_socket, strength: float, location=(0.0, 0.0)):
    """Return a colour socket: colour * strength, with its max channel soft-limited to 1.0 by one shared ratio."""
    x, y = location
    knee = CRT_HIGHLIGHT_KNEE
    span = 1.0 - knee

    def math(op, a=None, b=None, dx=0.0, dy=0.0):
        n = nodes.new("ShaderNodeMath")
        n.operation = op
        n.location = (x + dx, y + dy)
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = float(v)
            else:
                links.new(v, n.inputs[i])
        return n.outputs[0]

    scaled = nodes.new("ShaderNodeVectorMath")
    scaled.operation = "SCALE"
    scaled.location = (x, y)
    links.new(colour_socket, scaled.inputs[0])
    scaled.inputs["Scale"].default_value = float(strength)
    sep = nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (x + 160, y - 160)
    links.new(scaled.outputs[0], sep.inputs[0])
    peak = math("MAXIMUM", math("MAXIMUM", sep.outputs[0], sep.outputs[1], 320, -160), sep.outputs[2], 480, -160)
    over = math("MAXIMUM", math("SUBTRACT", peak, knee, 640, -160), 0.0, 800, -160)
    # knee + span * (1 - exp(-over / span))
    rolled = math("ADD", math("MULTIPLY", math("SUBTRACT", 1.0, math("EXPONENT", math("DIVIDE", over, -span, 960, -160), None, 1120, -160), 1280, -160), span, 1440, -160), knee, 1600, -160)
    target = math("ADD", math("MINIMUM", peak, knee, 960, -320), math("SUBTRACT", rolled, knee, 1760, -160), 1920, -240)
    ratio = math("DIVIDE", target, math("MAXIMUM", peak, 1e-5, 640, -320), 2080, -240)
    out = nodes.new("ShaderNodeVectorMath")
    out.operation = "SCALE"
    out.location = (x + 2240, y)
    links.new(scaled.outputs[0], out.inputs[0])
    links.new(ratio, out.inputs["Scale"])
    return out.outputs[0]
