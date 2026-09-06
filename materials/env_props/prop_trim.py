"""M_PropTrimPreset_CR+NOM+AO extras: slice mask, AO dirt, detail array.

The PropTrim sheet packs several material strips into one atlas. Three preset
features decide how a given prop reads and none of them are in the MI's own
texture list, so they only work once the preset defaults are merged in:

* ``Use Slice Mask`` — ``T_PropTrimSheet_Mask`` marks which strips accept
  ``Tint Color 01``. Without it the tint floods the whole sheet, which is why
  tinted props came out uniformly coloured instead of "green painted panels
  with bare metal fittings".
* ``Use Prop AO Dirt`` — the prop's baked AO drives ``AO Dirt Colour`` into
  crevices.
* ``Use Detail`` — ``TA_Detail_Props_02`` adds micro normal detail; the array
  exports one PNG per slice and ``Detail Type (R/G/B)`` picks the slice.
"""
from __future__ import annotations

from ..common import (
    _find_env_tex,
    _mask_channel_value,
    _mi_colour,
    _mi_scalar,
    _mi_switch,
    _mix_normals_vec,
    _mix_rgba,
    _new_tex_image,
    _rgb_node,
    _wire_normal_map,
)
from .weathering import DETAIL_ARRAY, load_library_image

SLICE_MASK_TEXTURE = "/Game/Pioneer/MaterialLibrary/Textures/Trims/T_PropTrimSheet_Mask"


def slice_tint_mask(nodes, links, *, mi, tex_lookup, local_folders, loc, bind_vec=None):
    """Mask socket limiting the tint to the sheet's tintable strips, or ``None``.

    ``T_PropTrimSheet_Mask`` stores a per-strip id rather than a blend weight,
    and ``Tint Range Low`` / ``Tint Range High`` select which ids accept the
    tint. The preset ships the full 0..1 range, so an MI that only flips
    ``Use Tint`` is asking for the whole sheet — treating the raw id as a blend
    factor instead left those props almost untinted.
    """
    switches = mi.get("switches") or {}
    scalars = mi.get("scalars") or {}
    if not _mi_switch(switches, "Use Slice Mask", default=False):
        return None
    if _mi_scalar(scalars, "TintAll", default=0.0) >= 0.5:
        return None
    low = _mi_scalar(scalars, "Tint Range Low", default=0.0)
    high = _mi_scalar(scalars, "Tint Range High", default=1.0)
    if low <= 0.001 and high >= 0.999:
        return None
    _, img = _find_env_tex(tex_lookup, "Slice Mask", "SliceMask")
    if img is None:
        img = load_library_image(SLICE_MASK_TEXTURE, local_folders)
    if img is None:
        return None
    node = _new_tex_image(nodes, img, "PropTrim Slice Mask", loc, non_color=True)
    if bind_vec is not None:
        bind_vec(node, loc[1])
    slice_id = _mask_channel_value(nodes, links, node, (loc[0] + 300, loc[1]))

    ox, oy = loc[0] + 480, loc[1]
    above = nodes.new("ShaderNodeMath")
    above.operation = "GREATER_THAN"
    above.label = f"id > {low:g}"
    above.location = (ox, oy + 90)
    links.new(slice_id, above.inputs[0])
    above.inputs[1].default_value = float(low) - 0.001

    below = nodes.new("ShaderNodeMath")
    below.operation = "LESS_THAN"
    below.label = f"id < {high:g}"
    below.location = (ox, oy - 90)
    links.new(slice_id, below.inputs[0])
    below.inputs[1].default_value = float(high) + 0.001

    band = nodes.new("ShaderNodeMath")
    band.operation = "MULTIPLY"
    band.label = "Tint Slice Range"
    band.location = (ox + 200, oy)
    links.new(above.outputs["Value"], band.inputs[0])
    links.new(below.outputs["Value"], band.inputs[1])
    return band.outputs["Value"]


def apply_ao_dirt(nodes, links, *, mi, albedo_sock, ao_sock, loc):
    """Push ``AO Dirt Colour`` into baked-AO crevices. Returns the albedo socket."""
    if albedo_sock is None or ao_sock is None:
        return albedo_sock, False
    switches = mi.get("switches") or {}
    if not _mi_switch(switches, "Use Prop AO Dirt", default=False):
        return albedo_sock, False
    scalars = mi.get("scalars") or {}
    colours = mi.get("colours") or []
    strength = _mi_scalar(scalars, "AO Dirt Strength", default=1.0)
    if strength <= 0.001:
        return albedo_sock, False

    ox, oy = loc
    # Dirt collects where AO is dark, so invert the occlusion.
    inv = nodes.new("ShaderNodeMath")
    inv.operation = "SUBTRACT"
    inv.label = "1 − AO"
    inv.location = (ox, oy)
    inv.inputs[0].default_value = 1.0
    links.new(ao_sock, inv.inputs[1])

    amount = nodes.new("ShaderNodeMath")
    amount.operation = "MULTIPLY"
    amount.label = f"AO Dirt ×{strength:g}"
    amount.use_clamp = True
    amount.location = (ox + 200, oy)
    links.new(inv.outputs["Value"], amount.inputs[0])
    amount.inputs[1].default_value = min(float(strength), 1.0)

    dirt = _mi_colour(colours, "AO Dirt Colour", "AO Dirt Color", default=(0.19, 0.15, 0.09, 1.0))
    dirt_rgb = _rgb_node(nodes, (dirt[0], dirt[1], dirt[2]), "AO Dirt Colour", (ox + 200, oy + 180))
    return (
        _mix_rgba(
            nodes, links, albedo_sock, dirt_rgb, amount.outputs["Value"],
            (ox + 420, oy + 80), "AO Dirt Mix",
        ),
        True,
    )


def apply_detail_array(nodes, links, *, mi, normal_sock, local_folders, loc):
    """Blend a ``TA_Detail_Props_02`` slice into the normal. Returns the socket."""
    if normal_sock is None:
        return normal_sock, False
    switches = mi.get("switches") or {}
    if not _mi_switch(switches, "Use Detail", default=False):
        return normal_sock, False
    scalars = mi.get("scalars") or {}
    intensity = _mi_scalar(scalars, "Detail Normal Intensity", default=1.0)
    if intensity <= 0.001:
        return normal_sock, False
    slice_idx = _mi_scalar(
        scalars, "Detail Type (R)", "Detail Type (1)", "Detail Type (B)", default=0.0,
    )
    img = load_library_image(DETAIL_ARRAY, local_folders, slice_index=slice_idx)
    if img is None:
        return normal_sock, False

    ox, oy = loc
    tiling = _mi_scalar(scalars, "Detail Tiling", default=1.0)
    uv = nodes.new("ShaderNodeTexCoord")
    uv.location = (ox - 400, oy)
    mapping = nodes.new("ShaderNodeMapping")
    mapping.label = f"Detail ×{tiling:g}"
    mapping.location = (ox - 200, oy)
    scale = max(float(tiling) * 4.0, 0.01)
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    links.new(uv.outputs["UV"], mapping.inputs["Vector"])

    node = _new_tex_image(nodes, img, "Detail Array", (ox, oy), non_color=True)
    links.new(mapping.outputs["Vector"], node.inputs["Vector"])
    det_n = _wire_normal_map(
        nodes, links, node.outputs["Color"], (ox + 320, oy),
        strength=1.0, label="Detail Normal Map",
    )
    return (
        _mix_normals_vec(
            nodes, links, normal_sock, det_n,
            min(max(float(intensity) * 0.5, 0.0), 1.0),
            (ox + 520, oy), "Base ⊕ Detail Array",
        ),
        True,
    )
