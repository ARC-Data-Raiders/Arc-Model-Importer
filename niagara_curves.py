"""Decode cooked Niagara curve data interfaces into plain sample tables.

A cooked ``NiagaraDataInterface*Curve`` export carries the baked ``ShaderLUT``
that the GPU sampled at runtime, so size/colour/velocity-over-life curves are
recoverable without touching compute bytecode.

The tables only survive a property export that resolves those classes from
mappings; FModel's own export drops them. Dump with::

    CUE4Parse.Example niagara-ns-dump --out-dir <dir>

No ``bpy`` import here on purpose: the NSIR extractor runs outside Blender.

Layout, verified against 35409 curve interfaces in the Pioneer corpus (100%):
``len(ShaderLUT) == (LUTNumSamplesMinusOne + 1) * channels``, with channels
interleaved inside each sample (sample-major, channel-minor).

Timing comes from ``LUTMinTime`` and ``LUTInvTimeRange``. ``LUTMaxTime`` is
**not** trustworthy in this cook: interfaces reading ``min=0.8, inv=5.0``
(a 0.2 span, so max 1.0) report ``LUTMaxTime`` as 0.0, and 20828 interfaces
report all three as zero while still holding a varying table. Min and inverse
range agree with each other on 9527 of 9769 interfaces that state a real range,
so span is taken as ``1 / LUTInvTimeRange`` and falls back to a normalised
0..1 curve when the inverse range is absent.
"""

CURVE_CHANNELS = {
    "NiagaraDataInterfaceCurve": 1,
    "NiagaraDataInterfaceVector2DCurve": 2,
    "NiagaraDataInterfaceVectorCurve": 3,
    "NiagaraDataInterfaceColorCurve": 4,
}

# Channel names per interface class, used for Blender F-curve / socket naming.
CURVE_CHANNEL_NAMES = {
    1: ("value",),
    2: ("x", "y"),
    3: ("x", "y", "z"),
    4: ("r", "g", "b", "a"),
}

DEFAULT_SPAN = 1.0


def is_curve_interface(class_name):
    return class_name in CURVE_CHANNELS


def _as_float(value, fallback=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def decode_curve(class_name, props):
    """Decode one curve data interface into a sample table.

    Returns ``None`` when the export carries no usable table, otherwise::

        {
          "class": str, "channels": int, "channelNames": (str, ...),
          "samples": int, "minTime": float, "maxTime": float, "span": float,
          "normalised": bool,          # True when the cook stated no time range
          "times": [float, ...],       # len == samples
          "values": [[float, ...], ...],   # one list per channel
          "constant": bool,            # every channel flat
        }
    """
    channels = CURVE_CHANNELS.get(class_name)
    if not channels:
        return None

    lut = props.get("ShaderLUT")
    if not isinstance(lut, list) or not lut:
        return None

    count = props.get("LUTNumSamplesMinusOne")
    if count is None:
        # Fall back to the array length; only safe because channels is known.
        samples = len(lut) // channels
    else:
        samples = int(round(_as_float(count))) + 1

    if samples < 1 or samples * channels != len(lut):
        return None

    try:
        flat = [float(x) for x in lut]
    except (TypeError, ValueError):
        return None

    values = [flat[c::channels] for c in range(channels)]

    min_time = _as_float(props.get("LUTMinTime"))
    inv_range = _as_float(props.get("LUTInvTimeRange"))
    normalised = inv_range <= 0.0
    span = DEFAULT_SPAN if normalised else 1.0 / inv_range

    if samples == 1:
        times = [min_time]
    else:
        step = span / (samples - 1)
        times = [min_time + step * i for i in range(samples)]

    constant = all(max(ch) - min(ch) <= 1e-6 for ch in values)

    return {
        "class": class_name,
        "channels": channels,
        "channelNames": CURVE_CHANNEL_NAMES[channels],
        "samples": samples,
        "minTime": min_time,
        "maxTime": min_time + span,
        "span": span,
        "normalised": normalised,
        "times": times,
        "values": values,
        "constant": constant,
    }


def curve_summary(curve):
    """Compact, loggable description of a decoded curve."""
    if not curve:
        return "none"
    rng = ", ".join(f"{min(ch):.4g}..{max(ch):.4g}" for ch in curve["values"])
    span = "0..1 normalised" if curve["normalised"] else \
        f"{curve['minTime']:.4g}..{curve['maxTime']:.4g}"
    flat = " flat" if curve["constant"] else ""
    return f"{curve['channels']}ch x {curve['samples']} over {span} [{rng}]{flat}"


def decode_curves_from_exports(exports):
    """Decode every curve interface in a cooked export list.

    Yields ``(export_name, owner, curve)`` where owner is the raw ``Outer``
    name, left for the caller to map onto an emitter.
    """
    for export in exports or ():
        if not isinstance(export, dict):
            continue
        class_name = export.get("Type")
        if not is_curve_interface(class_name):
            continue
        curve = decode_curve(class_name, export.get("Properties") or {})
        if curve is None:
            continue
        outer = export.get("Outer")
        if isinstance(outer, dict):
            outer = outer.get("ObjectName")
        yield export.get("Name"), outer, curve
