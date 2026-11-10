"""Static accessory profiles recovered from the shipping material shaders."""
from __future__ import annotations

import bpy

from .. import textures
from .common import _mi_colour, _mi_scalar, _new_tex_image, _set_material_alpha_mode


def setup_static_snow_shell(mat, mi: dict, snow_path: str, obj):
    """M_SnowGlobe_Shell's frozen cylindrical snow and facing response.

    Source: current SM5 resource 5D4F5E441B4DD8AF37A7EE7764C7CBABD0199665,
    VS 1996 / PS 1981. Temporal screen-door coverage is replaced by its average
    coverage; the authored capture time is fixed, with no animation driver.
    """
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    scalars, colours = mi.get("scalars") or {}, mi.get("colours") or []

    def math(operation, a, b=0, label=""):
        node = nodes.new("ShaderNodeMath")
        node.operation, node.label = operation, label
        for i, value in enumerate((a, b)):
            if hasattr(value, "node"):
                links.new(value, node.inputs[i])
            else:
                node.inputs[i].default_value = value
        return node.outputs[0]

    def clamp(value):
        return math("MINIMUM", math("MAXIMUM", value, 0), 1)

    def colour(value):
        node = nodes.new("ShaderNodeRGB")
        node.outputs[0].default_value = value
        return node.outputs[0]

    def mix(a, b, factor, label="", blend="MIX"):
        node = nodes.new("ShaderNodeMixRGB")
        node.blend_type, node.label = blend, label
        if hasattr(factor, "node"):
            links.new(factor, node.inputs[0])
        else:
            node.inputs[0].default_value = factor
        for socket, value in [(node.inputs[1], a), (node.inputs[2], b)]:
            if hasattr(value, "node"):
                links.new(value, socket)
            else:
                socket.default_value = (value, value, value, 1)
        return node.outputs[0]

    uv = nodes.new("ShaderNodeTexCoord")
    coords = nodes.new("ShaderNodeVectorMath")
    coords.operation, coords.label = "MULTIPLY", "Pre-skinned Unreal centimetres"
    # Native UEModel stores metre vertices; PSK stores centimetres with an
    # object transform. Object coordinates preserve the corresponding unit.
    units = 100.0 if obj.data.get("arc_uemodel_lod") is not None else 1.0
    coords.inputs[1].default_value = (units, -units, units)
    links.new(uv.outputs["Object"], coords.inputs[0])
    xyz = nodes.new("ShaderNodeSeparateXYZ")
    links.new(coords.outputs["Vector"], xyz.inputs[0])
    pivot = _mi_colour(colours, "PivotOffset", default=(0, 0, -5, 1))
    x = math("ADD", xyz.outputs["X"], pivot[0])
    y = math("ADD", xyz.outputs["Y"], pivot[1])
    z = math("ADD", xyz.outputs["Z"], pivot[2])
    angle = math("MULTIPLY", math("ARCTAN2", x, y), .318471, "Authored cylindrical U")
    capture_time = _mi_scalar(scalars, "IconCaptureTime", default=0)
    vertical = math("ADD", math("MULTIPLY", z, _mi_scalar(scalars, "ZScale", default=.05)),
                    capture_time * _mi_scalar(scalars, "FallingSpeed", default=.05),
                    "Authored static capture phase")
    cyl = nodes.new("ShaderNodeCombineXYZ")
    links.new(angle, cyl.inputs["X"])
    links.new(math("SUBTRACT", 1, vertical), cyl.inputs["Y"])
    image = bpy.data.images.load(snow_path, check_existing=True)
    image.alpha_mode = "CHANNEL_PACKED"
    atlas = _new_tex_image(nodes, image, "Snow smoke · authored cylindrical projection", (-900, 0), non_color=True)
    links.new(cyl.outputs[0], atlas.inputs["Vector"])
    channels = nodes.new("ShaderNodeSeparateColor")
    links.new(atlas.outputs["Color"], channels.inputs["Color"])
    base = _new_tex_image(nodes, image, "Snow smoke · UV0 background (B)", (-900, 350), non_color=True)
    links.new(uv.outputs["UV"], base.inputs["Vector"])
    base_channels = nodes.new("ShaderNodeSeparateColor")
    links.new(base.outputs["Color"], base_channels.inputs["Color"])

    height = clamp(math("MULTIPLY", math("ADD", xyz.outputs["Z"],
                   _mi_scalar(scalars, "HeightFalloffOffset", default=2)),
                   _mi_scalar(scalars, "HeightOffsetSharpness", default=.5)))
    radius = clamp(math("MULTIPLY", math("ADD", math("MULTIPLY", x, x), math("MULTIPLY", y, y)), .2))
    gate = math("MULTIPLY", height, radius, "Authored height × radius gate")
    density = math("MULTIPLY", math("MAXIMUM", math("SUBTRACT", channels.outputs["Red"],
                   _mi_scalar(scalars, "Threshold", default=.5)), 0),
                   _mi_scalar(scalars, "SnowShadowIntensity", default=.75))
    coverage = clamp(math("MULTIPLY", math("MULTIPLY", density, gate), 1.2,
                         "Average temporal snow coverage"))
    geometry = nodes.new("ShaderNodeNewGeometry")
    dot = nodes.new("ShaderNodeVectorMath"); dot.operation = "DOT_PRODUCT"
    links.new(geometry.outputs["Normal"], dot.inputs[0])
    links.new(geometry.outputs["Incoming"], dot.inputs[1])
    facing = clamp(math("ABSOLUTE", dot.outputs["Value"]))
    grazing = math("POWER", math("SUBTRACT", 1, facing), 2, "Authored squared grazing response")
    background = mix(colour(_mi_colour(colours, "BackgroundColor", default=(.45974,.662027,.989583,1))),
                     math("MULTIPLY", base_channels.outputs["Blue"], math("POWER", facing, 4)),
                     1, "Authored background × facing^4", "MULTIPLY")
    surface_colour = mix(background, colour((1,1,1,1)), grazing, "Authored grazing colour")

    rough_path = textures.find_asset_from_object_path(
        "/Game/Pioneer/MaterialLibrary/Textures/Breakup/T_Generic_04_M", ".png",
    )
    if rough_path:
        rough = _new_tex_image(nodes, bpy.data.images.load(rough_path, check_existing=True),
                               "Authored Generic04 roughness (R)", (-900,-450), non_color=True)
        links.new(uv.outputs["UV"], rough.inputs["Vector"])
        rough_channels = nodes.new("ShaderNodeSeparateColor")
        links.new(rough.outputs["Color"], rough_channels.inputs["Color"])
        rough_value = rough_channels.outputs["Red"]
    else:
        rough_value = .5
    roughness = math("ADD", math("MULTIPLY", rough_value, .296), .144)
    roughness = math("ADD", roughness, math("MULTIPLY", grazing, math("SUBTRACT", .1, roughness)),
                     "Authored static roughness")
    emission = mix(mix(background, _mi_scalar(scalars, "BackgroundIntensity", default=12.8913),
                       1, blend="MULTIPLY"),
                   math("MULTIPLY", math("MULTIPLY", channels.outputs["Red"], gate), .1),
                   1, "Authored static snow emission", "ADD")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    links.new(surface_colour, bsdf.inputs["Base Color"])
    links.new(roughness, bsdf.inputs["Roughness"])
    links.new(grazing, bsdf.inputs["Metallic"])
    links.new(emission, bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 1
    links.new(math("MAXIMUM", geometry.outputs["Backfacing"], coverage), bsdf.inputs["Alpha"])
    out = nodes.new("ShaderNodeOutputMaterial")
    links.new(bsdf.outputs[0], out.inputs["Surface"])
    _set_material_alpha_mode(mat, mode="HASHED", two_sided=True)
    mat["arc_static_snow_setup"] = "shipping-5D4F5E44-static-v1"
    mat["arc_static_snow_capture_time"] = capture_time
    mat["arc_static_snow_coverage"] = "Average of the shipping temporal screen-door mask; Blender lighting differs."
    mat["arc_static_snow_missing_roughness"] = not bool(rough_path)
