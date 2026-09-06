"""Architecture height paint (MF_ArchitecurePaint) — the cream band on concrete.

Two authoring modes share one set of parameters:

* ``Paint_WorldSpaceHeight`` on — the band is placed against world Z, so a whole
  street paints at the same altitude.
* ``Paint_WorldSpaceHeight`` off (the common case, and what the extraction
  elevator uses) — the band is placed against the mesh's own local Z between
  ``PaintLimitLower`` and ``PaintLimitUpper``, so every instance paints the same
  distance up from its own base.

UE authors those heights in centimetres. Mesh Position units depend on import:

* Map Stage 1 scales by ``MAP_UNIT_SCALE`` (0.01) → Position in metres
* Single-model PSK import usually leaves Unreal cm → Position in cm

Always convert Paint* scalars with the same unit scale as the geometry.
"""
from __future__ import annotations

from mathutils import Vector

from ..common import (
    _mask_channel_value,
    _mi_colour,
    _mi_scalar,
    _mi_switch,
    _mix_rgba,
    _new_tex_image,
)

# Unreal cm → metres (map Stage 1 default).
_UE_CM_TO_M = 0.01

PAINT_BREAKUP_TEXTURE = "/Game/Pioneer/MaterialLibrary/Textures/Breakup/T_Paint_Breakup_01_X"


def _span_to_unit_scale(span: float) -> float:
    # Props still in UE cm are hundreds/thousands of Blender units tall.
    return 1.0 if span > 40.0 else _UE_CM_TO_M


def mesh_ue_cm_to_position_units(obj) -> float:
    """Multiplier from UE centimetres → this mesh's world Position units."""
    if obj is None:
        return _UE_CM_TO_M
    try:
        raw = obj.get("arc_map_unit_scale")
        if raw not in (None, ""):
            v = float(raw)
            if v > 0.0:
                return v
    except Exception:
        pass
    try:
        if obj.get("arc_map"):
            return _UE_CM_TO_M
    except Exception:
        pass
    try:
        dim = max(
            float(obj.dimensions.x),
            float(obj.dimensions.y),
            float(obj.dimensions.z),
        )
        if dim > 40.0:
            return 1.0
        zs = [(obj.matrix_world @ Vector(c)).z for c in obj.bound_box]
        return _span_to_unit_scale(abs(max(zs) - min(zs)))
    except Exception:
        pass
    return _UE_CM_TO_M


def mesh_local_ue_cm_to_position_units(obj) -> float:
    """Same, for object-space coordinates (``bound_box`` is pre-transform)."""
    if obj is None:
        return _UE_CM_TO_M
    try:
        zs = [c[2] for c in obj.bound_box]
        xs = [c[0] for c in obj.bound_box]
        span = max(abs(max(zs) - min(zs)), abs(max(xs) - min(xs)))
        if span > 0.0:
            return _span_to_unit_scale(span)
    except Exception:
        pass
    return mesh_ue_cm_to_position_units(obj)


def _material_owner(mat):
    import bpy

    if mat is None:
        return None
    try:
        for obj in bpy.data.objects:
            if obj.type != "MESH":
                continue
            for slot in obj.material_slots or []:
                if slot.material == mat:
                    return obj
    except Exception:
        pass
    return None


def infer_paint_unit_scale(mat=None, psk_path: str = "", local: bool = False) -> float:
    """Resolve cm→Position scale from a material's users or the mesh path."""
    import os

    import bpy

    convert = mesh_local_ue_cm_to_position_units if local else mesh_ue_cm_to_position_units

    owner = _material_owner(mat)
    if owner is not None:
        return convert(owner)
    if psk_path:
        base = os.path.basename(psk_path).lower()
        try:
            for obj in bpy.data.objects:
                if obj.type != "MESH":
                    continue
                raw = str(obj.get("arc_psk_path") or "").replace("\\", "/").lower()
                if raw.endswith(base) or base in raw:
                    return convert(obj)
        except Exception:
            pass
        try:
            from ...asset_domain import is_environment_domain

            # Single-model env props are almost always still in UE cm.
            if is_environment_domain(psk_path):
                return 1.0
        except Exception:
            pass
    return _UE_CM_TO_M


def paint_is_world_anchored(obj) -> bool:
    """True when the mesh sits at real level coordinates (map placement).

    A single-prop import lands at the origin, so ``PaintGroundHeight`` — the
    level altitude of the ground the prop was authored against — has nothing to
    anchor to and the band has to be rebased onto the mesh origin instead.
    """
    if obj is None:
        return False
    try:
        return bool(obj.get("arc_map"))
    except Exception:
        return False


def material_is_world_anchored(mat) -> bool:
    """``paint_is_world_anchored`` for the mesh that uses *mat*."""
    return paint_is_world_anchored(_material_owner(mat))


def paint_band(mi: dict, unit_scale: float, *, rebase_ground: bool = False):
    """Return ``(band_top, band_bottom, falloff)`` in mesh units, or ``None``.

    ``PaintLimitUpper`` / ``PaintLimitLower`` win over the older
    ``PaintGroundHeight`` + ``PaintHeight`` pair when the MI authored them.
    ``band_bottom`` is ``None`` when the paint simply runs down forever.
    With *rebase_ground* the ground altitude drops out, leaving ``PaintHeight``
    as an offset from the mesh origin.
    """
    scalars = (mi or {}).get("scalars") or {}
    # The two limit pairs are alternatives, so preset defaults must not override
    # the pair the artist actually authored on the instance.
    authored = (mi or {}).get("authored")
    if authored and not (authored & {"PaintLimitUpper", "PaintLimitLower"}) and (
        authored & {"PaintGroundHeight", "PaintHeight"}
    ):
        upper = lower = None
    else:
        upper = scalars.get("PaintLimitUpper")
        lower = scalars.get("PaintLimitLower")
    falloff_cm = _mi_scalar(scalars, "PaintFalloff", default=100.0)

    top_cm = None
    bottom_cm = None
    if upper is not None and lower is not None:
        try:
            up, low = float(upper), float(lower)
        except (TypeError, ValueError):
            up = low = 0.0
        # Preset default leaves both at 1.0, which describes no band at all.
        if abs(up - low) > 0.5:
            top_cm, bottom_cm = max(up, low), min(up, low)
    if top_cm is None:
        ground = _mi_scalar(scalars, "PaintGroundHeight", default=0.0)
        height = _mi_scalar(scalars, "PaintHeight", default=0.0)
        if abs(ground) < 1e-6 and abs(height) < 1e-6:
            return None
        top_cm = height if rebase_ground else ground + height

    falloff = max(abs(falloff_cm) * unit_scale, 1e-4 if unit_scale >= 1.0 else 0.05)
    return (
        float(top_cm) * unit_scale,
        None if bottom_cm is None else float(bottom_cm) * unit_scale,
        falloff,
    )


def apply_world_space_paint(
    nodes,
    links,
    *,
    mi: dict,
    albedo_sock,
    rough_sock=None,
    loc=(-200, -1400),
    unit_scale: float | None = None,
    obj=None,
    mat=None,
    breakup_img=None,
):
    """Mix PaintColor1 over *albedo_sock* using a height mask.

    Returns ``(albedo_sock, rough_sock, applied: bool)``.
    """
    from .detect import wants_world_paint

    if albedo_sock is None or not wants_world_paint(mi):
        return albedo_sock, rough_sock, False

    scalars = mi.get("scalars") or {}
    switches = mi.get("switches") or {}
    colours = mi.get("colours") or []

    world_space = bool(_mi_switch(switches, "Paint_WorldSpaceHeight", default=False))
    owner = obj if obj is not None else _material_owner(mat)
    rebase_ground = world_space and not paint_is_world_anchored(owner)
    if rebase_ground:
        world_space = False
    breakup = _mi_scalar(scalars, "PaintBreakup", default=0.0)
    breakup_scale = _mi_scalar(scalars, "PaintBreakupScale", default=1.0)
    paint_tiling = _mi_scalar(scalars, "UVTilingPaint", default=0.25)
    paint_rough = _mi_scalar(scalars, "PaintRoughness", default=-1.0)
    under_darken = _mi_scalar(scalars, "UnderPaintDarken", default=0.0)

    if unit_scale is None:
        if owner is not None:
            unit_scale = (
                mesh_ue_cm_to_position_units(owner)
                if world_space
                else mesh_local_ue_cm_to_position_units(owner)
            )
        else:
            unit_scale = _UE_CM_TO_M
    unit_scale = float(unit_scale) if unit_scale and unit_scale > 0.0 else _UE_CM_TO_M

    band = paint_band(mi, unit_scale, rebase_ground=rebase_ground)
    if band is None:
        return albedo_sock, rough_sock, False
    band_top, band_bottom, falloff = band

    paint_c1 = _mi_colour(
        colours, "PaintColor1", "Paint Color 1", "PaintColour1", default=None,
    )
    if paint_c1 is None:
        paint_c1 = (0.95, 0.90, 0.82, 1.0)

    ox, oy = loc

    coord = nodes.new("ShaderNodeTexCoord")
    coord.label = "World Pos (Paint)" if world_space else "Object Pos (Paint)"
    coord.location = (ox - 400, oy)
    pos_sock = coord.outputs["Object"]
    if world_space:
        geo = nodes.new("ShaderNodeNewGeometry")
        geo.label = "World Pos (Paint)"
        geo.location = (ox - 400, oy - 200)
        pos_sock = geo.outputs["Position"]

    sep = nodes.new("ShaderNodeSeparateXYZ")
    sep.label = "Height"
    sep.location = (ox - 220, oy)
    links.new(pos_sock, sep.inputs["Vector"])

    # fac = saturate((band_top + falloff - Z) / falloff)
    # → 1 below band_top, 0 above band_top+falloff (paint on lower surfaces).
    add_top = nodes.new("ShaderNodeMath")
    add_top.operation = "ADD"
    add_top.label = "band_top+falloff"
    add_top.location = (ox - 40, oy + 40)
    add_top.inputs[0].default_value = band_top
    add_top.inputs[1].default_value = falloff

    sub = nodes.new("ShaderNodeMath")
    sub.operation = "SUBTRACT"
    sub.label = "top−Z"
    sub.location = (ox + 140, oy)
    links.new(add_top.outputs[0], sub.inputs[0])
    links.new(sep.outputs["Z"], sub.inputs[1])

    div = nodes.new("ShaderNodeMath")
    div.operation = "DIVIDE"
    div.label = "÷falloff"
    div.use_clamp = True
    div.location = (ox + 320, oy)
    links.new(sub.outputs[0], div.inputs[0])
    div.inputs[1].default_value = falloff
    fac_sock = div.outputs[0]

    # PaintLimitLower stops the band before it runs off the bottom of the mesh.
    if band_bottom is not None:
        up = nodes.new("ShaderNodeMath")
        up.operation = "SUBTRACT"
        up.label = "Z−bottom"
        up.location = (ox + 140, oy - 460)
        links.new(sep.outputs["Z"], up.inputs[0])
        up.inputs[1].default_value = band_bottom - falloff

        up_div = nodes.new("ShaderNodeMath")
        up_div.operation = "DIVIDE"
        up_div.label = "÷falloff"
        up_div.use_clamp = True
        up_div.location = (ox + 320, oy - 460)
        links.new(up.outputs[0], up_div.inputs[0])
        up_div.inputs[1].default_value = falloff

        band_m = nodes.new("ShaderNodeMath")
        band_m.operation = "MULTIPLY"
        band_m.label = "Paint Band"
        band_m.use_clamp = True
        band_m.location = (ox + 500, oy - 460)
        links.new(fac_sock, band_m.inputs[0])
        links.new(up_div.outputs[0], band_m.inputs[1])
        fac_sock = band_m.outputs[0]

    # Blotchy paint edge. UE samples T_Paint_Breakup_01_X; fall back to noise
    # only when the library texture cannot be resolved.
    if breakup > 0.02:
        if breakup_img is not None:
            b_map = nodes.new("ShaderNodeMapping")
            b_map.label = "Paint Breakup UV"
            b_map.location = (ox - 220, oy - 220)
            b_scale = max(float(paint_tiling) * max(float(breakup_scale), 0.05), 0.01)
            b_scale = b_scale if unit_scale >= 0.5 else b_scale * 100.0
            b_map.inputs["Scale"].default_value = (b_scale, b_scale, b_scale)
            links.new(pos_sock, b_map.inputs["Vector"])
            b_node = _new_tex_image(
                nodes, breakup_img, "Paint Breakup", (ox - 40, oy - 220), non_color=True,
            )
            links.new(b_map.outputs["Vector"], b_node.inputs["Vector"])
            noise_sock = _mask_channel_value(nodes, links, b_node, (ox + 140, oy - 220))
        else:
            noise = nodes.new("ShaderNodeTexNoise")
            noise.label = "Paint Breakup (fallback)"
            noise.location = (ox - 40, oy - 220)
            noise.inputs["Scale"].default_value = max(
                float(breakup_scale) * (0.35 if unit_scale < 0.5 else 0.0035),
                0.0005,
            )
            noise.inputs["Detail"].default_value = 6.0
            links.new(pos_sock, noise.inputs["Vector"])
            noise_sock = noise.outputs["Fac"]

        n_mul = nodes.new("ShaderNodeMath")
        n_mul.operation = "MULTIPLY"
        n_mul.label = "noise×breakup"
        n_mul.location = (ox + 320, oy - 220)
        links.new(noise_sock, n_mul.inputs[0])
        n_mul.inputs[1].default_value = min(max(float(breakup), 0.0), 1.0)
        n_one = nodes.new("ShaderNodeMath")
        n_one.operation = "SUBTRACT"
        n_one.label = "1−breakup"
        n_one.location = (ox + 320, oy - 360)
        n_one.inputs[0].default_value = 1.0
        n_one.inputs[1].default_value = min(max(float(breakup), 0.0), 1.0)
        n_add = nodes.new("ShaderNodeMath")
        n_add.operation = "ADD"
        n_add.label = "breakup mask"
        n_add.use_clamp = True
        n_add.location = (ox + 500, oy - 280)
        links.new(n_one.outputs[0], n_add.inputs[0])
        links.new(n_mul.outputs[0], n_add.inputs[1])
        fac_m = nodes.new("ShaderNodeMath")
        fac_m.operation = "MULTIPLY"
        fac_m.label = "height×breakup"
        fac_m.use_clamp = True
        fac_m.location = (ox + 680, oy)
        links.new(fac_sock, fac_m.inputs[0])
        links.new(n_add.outputs[0], fac_m.inputs[1])
        fac_sock = fac_m.outputs[0]

    # Under the paint line the substrate reads darker even where paint is worn.
    if under_darken > 0.001:
        dark = nodes.new("ShaderNodeMix")
        dark.data_type = "RGBA"
        dark.blend_type = "MULTIPLY"
        dark.label = f"UnderPaintDarken ×{under_darken:g}"
        dark.location = (ox + 500, oy + 320)
        dark.inputs["Factor"].default_value = min(float(under_darken), 1.0)
        links.new(albedo_sock, dark.inputs[6])
        shade = max(1.0 - float(under_darken), 0.0)
        dark.inputs[7].default_value = (shade, shade, shade, 1.0)
        albedo_sock = dark.outputs[2]

    paint_rgb = nodes.new("ShaderNodeRGB")
    paint_rgb.label = "PaintColor1"
    paint_rgb.outputs[0].default_value = (
        float(paint_c1[0]),
        float(paint_c1[1]),
        float(paint_c1[2]),
        1.0,
    )
    paint_rgb.location = (ox + 680, oy + 180)

    # Optional second tint (subtle mix into paint color).
    paint_c2 = _mi_colour(colours, "PaintColor2", "Paint Color 2", default=None)
    paint_col = paint_rgb.outputs[0]
    if paint_c2 is not None:
        c2 = nodes.new("ShaderNodeRGB")
        c2.label = "PaintColor2"
        c2.outputs[0].default_value = (
            float(paint_c2[0]),
            float(paint_c2[1]),
            float(paint_c2[2]),
            1.0,
        )
        c2.location = (ox + 680, oy + 320)
        paint_col = _mix_rgba(
            nodes, links, paint_rgb.outputs[0], c2.outputs[0], 0.35,
            (ox + 860, oy + 240), "Paint C1↔C2",
        )

    albedo_out = _mix_rgba(
        nodes, links, albedo_sock, paint_col, fac_sock,
        (ox + 900, oy), "Height Paint → Albedo",
    )

    rough_out = rough_sock
    if rough_sock is not None and paint_rough >= 0.0:
        from ..common import _mix_float

        pr = nodes.new("ShaderNodeValue")
        pr.label = "PaintRoughness"
        pr.outputs[0].default_value = float(paint_rough)
        pr.location = (ox + 680, oy - 160)
        rough_out = _mix_float(
            nodes, links, rough_sock, pr.outputs[0], fac_sock,
            (ox + 900, oy - 160), "Height Paint → Rough",
        )

    return albedo_out, rough_out, True
