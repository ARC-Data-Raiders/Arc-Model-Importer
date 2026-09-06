"""Resolve an environment MI's parent preset and merge the preset's defaults.

Two dump shapes reach us:

* full FModel exports, which carry ``Properties.Parent``
* compact dumps (``{"Textures": ..., "Parameters": ...}``) with no parent at all

Either way an MI only stores what the artist overrode, so the authored look
depends on defaults that live in the preset. ``MI_ExtractionElevator_01_A``
never mentions ``1. Wear CR``; the rusted metal revealed by worn paint comes
from ``M_TrimMap_01``.

Merging is deliberately limited to the presets we build shaders for
(``_MERGEABLE``) so unrelated environment materials keep their current look.

Texture defaults are only merged when the switch that consumes them is on --
``M_PropTrimPreset_CR+NOM+AO`` defaults ``Prop AO Texture`` to a gas-station
bake that would be nonsense on any other prop, and ``Use Prop AO Dirt`` is off
by default precisely because of that.
"""
from __future__ import annotations

import os

from .. import preset_defaults

PRESET_TRIMMAP = "M_TrimMap_01"
PRESET_CONCRETE = "M_ArchitecturePreset_Concrete_01"
PRESET_PROPTRIM = "M_PropTrimPreset_CR+NOM+AO"
PRESET_ARCH_TRIM = "M_ArchitecturePreset_Trim+CR+NAH"

_MERGEABLE = (
    PRESET_TRIMMAP,
    PRESET_CONCRETE,
    PRESET_PROPTRIM,
    PRESET_ARCH_TRIM,
)

# Texture param -> switch that must be enabled before the preset default is
# worth binding. ``None`` means the default is always safe for the family.
_TEXTURE_GATES = {
    PRESET_TRIMMAP: {
        "1.  Material CR": None,
        "1.  Material NOH": None,
        "1. Wear CR": ("2. Wear Options", "3. Wear Options", "4. Wear Options"),
        "1. Wear NOH": ("2. Wear Options", "3. Wear Options", "4. Wear Options"),
        "2. Overlay CR": ("2. OverlayTexture", "3. OverlayTexture", "4. Overlay Texture"),
        "SignTexture": ("EnableSigns", "AtlasSign"),
        "WaterlineTexture": ("Use Waterline",),
    },
    PRESET_CONCRETE: {
        "CR": None,
        "NOH": None,
        "Overlay": ("UseOverlay",),
        "CR Blend": ("Enable Blend Textures",),
        "NOH Blend": ("Enable Blend Textures",),
        "Detail Normal Texture": ("Enable DetailNormal",),
        "WaterlineOverlay": ("Use Waterline",),
        "PaintBreakup": ("Use Paint", "UseFullPaint"),
    },
    PRESET_PROPTRIM: {
        "CR Texture": None,
        "NXX/NMX Texture": None,
        "Overlay": ("UseOverlay",),
        "CR Blend": ("Enable Blend Textures",),
        "NOH Blend": ("Enable Blend Textures",),
        "HolesNXX": ("Use Holes",),
        # Prop-specific bake — never inherit it onto a different prop.
        "Prop AO Texture": (),
        "SignTexture": ("EnableSigns", "AtlasSign"),
    },
    PRESET_ARCH_TRIM: {
        "CR": None,
        "NAO": None,
        "Detail Normal": None,
    },
}

# Parameter-name signatures for compact dumps with no Parent field.
# (preset key, required params, rejecting params)
_SIGNATURES = (
    (PRESET_TRIMMAP,
     ("1.  Material CR",), ()),
    (PRESET_TRIMMAP,
     ("2. Overlay CR",), ()),
    (PRESET_PROPTRIM,
     ("CR Texture", "NXX/NMX Texture"), ()),
    (PRESET_PROPTRIM,
     ("CR Texture", "Prop AO Texture"), ()),
    (PRESET_ARCH_TRIM,
     ("CR", "NAO"), ()),
    (PRESET_CONCRETE,
     ("CR", "NOH"), ("NAO", "CR Texture")),
)

# Scalar/switch names that only exist on one preset — used when the texture
# signature is ambiguous (e.g. a concrete MI that only overrode scalars).
_SCALAR_SIGNATURES = (
    (PRESET_TRIMMAP, ("2. Wear Stain Amount", "2. Rust Strenght", "2. Wear Tiling")),
    (PRESET_CONCRETE, ("PaintLimitUpper", "PaintLimitLower", "UVTilingPaint", "UnderPaintDarken")),
    (PRESET_PROPTRIM, ("AO Dirt Strength", "Roughness Tint", "Weathering Tiling")),
    (PRESET_ARCH_TRIM, ("UV Offset Amount", "Detail Normal Tile", "Global opacity")),
)

_PARENT_ALIASES = {
    "m_trimmap_01": PRESET_TRIMMAP,
    "m_architecturepreset_concrete_01": PRESET_CONCRETE,
    "m_proptrimpreset_cr+nom+ao": PRESET_PROPTRIM,
    "m_architecturepreset_trim+cr+nah": PRESET_ARCH_TRIM,
}


def tint_multiplier(mi: dict, preset_key: str, *names, fallback=(1.0, 1.0, 1.0)):
    """Tint colour normalised against its preset default, so default = neutral.

    UE authors these tints around a mid-grey default (``BCBCBC``/``BBBBBB``), so
    multiplying albedo by the raw value would halve every untinted material.
    Dividing by the preset's own default makes "MI left it alone" mean 1.0 and
    ``DAE3B1`` keep its green chroma.

    Near-white overrides (elevator ``ColorTint`` ``FEFEFE``) must not become a
    ~2× brightness boost — that washes the mesh out in Eevee. After dividing by
    the default we rescale so the hottest channel is at most 1.0, which keeps
    hue/saturation and drops the bogus luminance lift.
    """
    colours = {p.lower(): rgba for p, rgba in ((mi or {}).get("colours") or [])}
    defaults = preset_defaults.preset_vectors(preset_key)
    for name in names:
        rgba = colours.get(name.lower())
        if rgba is None:
            continue
        base = defaults.get(name)
        out = []
        for i in range(3):
            val = float(rgba[i])
            ref = float(base[i]) if base and i < len(base) else 1.0
            out.append(val / ref if ref > 1e-4 else val)
        peak = max(out) if out else 1.0
        if peak > 1.0:
            out = [c / peak for c in out]
        return tuple(out)
    return tuple(fallback)


def _tex_params(mi: dict) -> set:
    return {p for p, _ in ((mi or {}).get("textures") or [])}


def _all_param_names(mi: dict) -> set:
    names = _tex_params(mi)
    names |= set((mi or {}).get("scalars") or ())
    names |= set((mi or {}).get("switches") or ())
    names |= {p for p, _ in ((mi or {}).get("colours") or [])}
    return names


def infer_preset_key(mi: dict, mi_path: str = "") -> str:
    """Preset key for *mi*, from its Parent when present, else its signature."""
    parent = str((mi or {}).get("parent") or "").strip()
    if parent:
        alias = _PARENT_ALIASES.get(parent.lower())
        if alias:
            return alias
        if preset_defaults.has_preset(parent):
            return parent

    tex = _tex_params(mi)
    for key, required, rejecting in _SIGNATURES:
        if not required:
            continue
        if all(r in tex for r in required) and not any(x in tex for x in rejecting):
            return key

    names = _all_param_names(mi)
    for key, markers in _SCALAR_SIGNATURES:
        if any(m in names for m in markers):
            return key

    stem = os.path.splitext(os.path.basename(mi_path or ""))[0].lower()
    if "trimmap" in stem or "trim_mapper" in stem:
        return PRESET_TRIMMAP
    if "proptrim" in stem:
        return PRESET_PROPTRIM
    return ""


def _switch_enabled(switches: dict, defaults: dict, names) -> bool:
    for name in names:
        if name in switches:
            if bool(switches[name]):
                return True
        elif bool(defaults.get(name)):
            return True
    return False


def merge_preset_defaults(mi: dict, mi_path: str = "") -> dict:
    """Return *mi* with its parent preset's defaults filled in underneath.

    The MI always wins. The result is a shallow copy; the original parsed MI
    stays untouched because it is shared through ``_FLAT_MI_CACHE``.
    """
    if not mi:
        return mi
    key = infer_preset_key(mi, mi_path)
    if key not in _MERGEABLE:
        return mi
    preset = preset_defaults.get_preset(key)
    if not preset.get("scalars") and not preset.get("switches"):
        return mi

    merged = dict(mi)
    merged["preset_key"] = key
    # Once defaults are folded in there is no way to tell an artist override
    # from a preset default, and some parameter pairs are mutually exclusive.
    merged["authored"] = _all_param_names(mi)
    if not merged.get("parent"):
        merged["parent"] = key

    scalars = dict(preset.get("scalars") or {})
    scalars.update(mi.get("scalars") or {})
    merged["scalars"] = scalars

    switches = dict(preset.get("switches") or {})
    switches.update(mi.get("switches") or {})
    merged["switches"] = switches

    colours = list(mi.get("colours") or [])
    have_colours = {p.lower() for p, _ in colours}
    for name, rgba in (preset.get("vectors") or {}).items():
        if name.lower() not in have_colours:
            colours.append((name, tuple(rgba)))
    merged["colours"] = colours

    textures = list(mi.get("textures") or [])
    have_tex = {p for p, _ in textures}
    gates = _TEXTURE_GATES.get(key, {})
    mi_switches = mi.get("switches") or {}
    preset_switches = preset.get("switches") or {}
    for name, obj_path in (preset.get("texture_defaults") or {}).items():
        if name in have_tex or not obj_path:
            continue
        gate = gates.get(name, ())
        if gate is not None and not _switch_enabled(mi_switches, preset_switches, gate):
            continue
        textures.append((name, obj_path))
    merged["textures"] = textures
    return merged
