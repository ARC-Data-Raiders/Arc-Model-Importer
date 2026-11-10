"""Authored mesh emission; keep state masks and non-emissive surfaces separate."""
from __future__ import annotations

import os
import json
import bpy


def _math(nodes, links, operation, a, b=None, label="", clamp=False):
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    node.label = label
    node.use_clamp = clamp
    for socket, value in zip(node.inputs, (a, b)):
        if value is None:
            continue
        if isinstance(value, bpy.types.NodeSocket):
            links.new(value, socket)
        else:
            socket.default_value = float(value)
    return node.outputs[0]


def _mix(nodes, links, a, b, factor, label="", operation="MIX"):
    node = nodes.new("ShaderNodeMixRGB")
    node.blend_type = operation
    node.label = label
    for socket, value in zip(node.inputs, (factor, a, b)):
        if isinstance(value, bpy.types.NodeSocket):
            links.new(value, socket)
        else:
            socket.default_value = (value, value, value, 1) if socket.type == "RGBA" and isinstance(value, (int, float)) else value
    return node.outputs[0]


def resolved_mi(path):
    """Resolve full FModel inheritance; compact Pak exports already flatten it."""
    from .common import _parse_flat_mi_json
    result = {"scalars": {}, "colours": {}, "textures": {}, "switches": {}}
    # Prefer the supplied export tree, so a full current dump cannot inherit
    # defaults from an older donor directory configured elsewhere in Blender.
    from .. import utils, textures
    chain, seen, current = [], set(), path
    for _ in range(8):
        if not current or not os.path.isfile(current):
            break
        identity = os.path.normcase(os.path.abspath(current))
        if identity in seen:
            break
        seen.add(identity)
        chain.append(current)
        with open(current, encoding="utf-8-sig") as stream:
            entries = utils.ue_export_entries(json.load(stream))
        entry = next((item for item in entries if item.get("Type") in ("MaterialInstanceConstant", "Material")), {})
        parent = ((entry.get("Properties") or {}).get("Parent") or {})
        reference = parent.get("ObjectPath", "") if isinstance(parent, dict) else ""
        if not reference:
            break
        reference = reference.rsplit(".", 1)[0]
        normalized = current.replace("\\", "/")
        candidates = [os.path.join(os.path.dirname(current), reference.rsplit("/", 1)[-1] + ".json")]
        for marker in ("/PioneerGame/Content/", "/RemappedPlugins/Content/"):
            if marker.lower() in normalized.lower() and reference.startswith("/Game/"):
                end = normalized.lower().index(marker.lower()) + len(marker)
                candidates.insert(0, normalized[:end] + reference[len("/Game/"):] + ".json")
        current = next((candidate for candidate in candidates if os.path.isfile(candidate)), "")
        if not current:
            current = textures.find_asset_from_object_path(reference, ".json")
    for source in reversed(chain or [path]):
        # Full cooked master dumps expose defaults through CachedExpressionData.
        from .. import utils
        with open(source, encoding="utf-8-sig") as stream:
            raw = json.load(stream)
        for entry in utils.ue_export_entries(raw):
            cached = entry.get("CachedExpressionData") or {}
            for target, info_key, values_key in (("scalars", "RuntimeEntries", "ScalarValues"), ("colours", "RuntimeEntries[1]", "VectorValues"), ("switches", "RuntimeEntries[8]", "StaticSwitchValues")):
                info = (cached.get(info_key) or {}).get("ParameterInfoSet") or []
                values = cached.get(values_key) or []
                for definition, value in zip(info, values):
                    if target == "colours" and isinstance(value, dict):
                        value = tuple(float(value.get(channel, 1)) for channel in "RGBA")
                    result[target][definition["Name"]] = value
        data = _parse_flat_mi_json(source)
        for key in ("scalars", "colours", "textures", "switches"):
            value = data.get(key) or {}
            result[key].update(dict(value))
        if data.get("parent"):
            result["parent"] = data["parent"]
    result["colours"] = list(result["colours"].items())
    result["textures"] = list(result["textures"].items())
    return result


def setup_light_material(material, path):
    """Untextured light masters use linear LightColor * LightIntensity."""
    from .environment import _mi_scalar, _mi_colour
    mi = resolved_mi(path)
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    color = _mi_colour(mi["colours"], "LightColor", "EmissiveColor", "Emissive Color", "Color", default=(1, 1, 1, 1))
    strength = _mi_scalar(mi["scalars"], "LightIntensity", "EmissiveIntensity", "Emissive Intensity", "Emissive", "Intensity", default=1)
    bsdf.inputs["Base Color"].default_value = (0, 0, 0, 1)
    bsdf.inputs["Specular IOR Level"].default_value = 0
    bsdf.inputs["Emission Color"].default_value = color
    bsdf.inputs["Emission Strength"].default_value = max(0, strength)
    material["arc_emissive_setup"] = "authored-light-v1"
    from .rebuild.graph import arrange
    arrange(material.node_tree)


def layered_emission(nodes, links, mi_data, colours, zone_eq, albedo, crease, edge):
    """Layer N emission follows the same crease/edge factors as its surface."""
    from .clothing import _gt_param_float, _colour_rgba
    emission = None
    for zone in range(1, 9):
        values = [_gt_param_float(mi_data, f"{zone}_{stage}EmissiveStrength", 0) for stage in ("Base", "Crease", "Edge")]
        if not any(value != 0 for value in values) or zone not in zone_eq:
            continue
        strength = values[0]
        for factor, target, stage in ((crease, values[1], "Crease"), (edge, values[2], "Edge")):
            if factor is not None:
                # mix(base, target, factor), without clipping HDR emission.
                delta = _math(nodes, links, "SUBTRACT", target, strength)
                strength = _math(nodes, links, "ADD", strength, _math(nodes, links, "MULTIPLY", factor, delta), f"Layer {zone} {stage} emission")
        strength = _math(nodes, links, "MULTIPLY", strength, zone_eq[zone], f"Layer {zone} emissive zone mask")
        strength = _math(nodes, links, "MAXIMUM", strength, 0)
        overlay = _colour_rgba(colours, f"{zone}_EmissiveColorOverlay", (0, 0, 0, 0))
        color = _mix(nodes, links, albedo, overlay, min(max(overlay[3], 0), 1), f"Layer {zone} emissive color")
        contribution = _mix(nodes, links, color, strength, 1, f"Layer {zone} emission", "MULTIPLY")
        emission = contribution if emission is None else _mix(nodes, links, emission, contribution, 1, "Sum layered emission", "ADD")
    if emission is not None:
        emission = _mix(nodes, links, emission, _gt_param_float(mi_data, "OverallEmissive", 1), 1, "OverallEmissive", "MULTIPLY")
    return emission


def is_lantern(mi):
    parent = str(mi.get("parent") or "").lower()
    if parent:
        return "m_characer_lantern" in parent
    return {"MaxSize", "Falloff", "FlickerTrack", "FlickerSpeed", "Bottom", "Top", "SmudgeIntensityMultiplier"}.issubset(mi.get("scalars") or {})


LANTERN_TEXTURES = (
    "/Game/Pioneer/MaterialLibrary/Character/CustomMaterials/Textures/T_FlickerNoise_M",
    "/Game/Pioneer/MaterialLibrary/Character/LayeredMaterials/Textures/Masks/T_Smudges_01_M",
)


def setup_lantern_material(material, path):
    """Static native lantern falloff; UE weather/proximity effects are omitted."""
    from .character import _find_tex_path
    from .environment import _mi_scalar, _mi_colour
    from .. import pak_extract
    mi = resolved_mi(path)
    def resolve_textures():
        if pak_extract.configured_dir(bpy.context.scene):
            from ...Outfit_Accessories.backpack_catalog import cached_file
            return [cached_file(package, ".png") for package in LANTERN_TEXTURES]
        return [_find_tex_path(package) for package in LANTERN_TEXTURES]
    paths = resolve_textures()
    missing = [package for package, found in zip(LANTERN_TEXTURES, paths) if not found or not os.path.isfile(found)]
    if missing and pak_extract.configured_dir(bpy.context.scene):
        pak_extract.ensure_rels(bpy.context.scene, missing)
        paths = resolve_textures()
    if any(not found or not os.path.isfile(found) for found in paths):
        raise RuntimeError("Lantern requires its exact FlickerNoise and Smudges textures; re-extract its material dependencies")
    s = mi["scalars"]
    scalar = lambda name, default: _mi_scalar(s, name, default=default)
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    uv = nodes.new("ShaderNodeUVMap")
    uv.uv_map = "UV0"
    separate = nodes.new("ShaderNodeSeparateXYZ")
    links.new(uv.outputs["UV"], separate.inputs[0])
    ue_v = _math(nodes, links, "SUBTRACT", 1, separate.outputs["Y"], "UE UV V")
    bottom, top = scalar("Bottom", 1), scalar("Top", .744)
    difference = top - bottom
    difference = difference if abs(difference) > 1e-8 else 1e-8
    height = _math(nodes, links, "DIVIDE", _math(nodes, links, "SUBTRACT", ue_v, bottom), difference, "Lantern authored height", True)
    geometry = nodes.new("ShaderNodeNewGeometry")
    facing = nodes.new("ShaderNodeVectorMath")
    facing.operation = "DOT_PRODUCT"
    links.new(geometry.outputs["Normal"], facing.inputs[0])
    links.new(geometry.outputs["Incoming"], facing.inputs[1])
    size, falloff = scalar("MaxSize", -.088), scalar("Falloff", 837.2357)
    difference = falloff - size
    difference = difference if abs(difference) > 1e-8 else 1e-8
    angle = _math(nodes, links, "DIVIDE", _math(nodes, links, "SUBTRACT", facing.outputs["Value"], size), difference, "Lantern authored view falloff", True)
    energy = _math(nodes, links, "MULTIPLY", _math(nodes, links, "MULTIPLY", height, angle), scalar("Intensity", 2295.102), "Lantern Intensity")
    powered = _math(nodes, links, "POWER", _math(nodes, links, "MAXIMUM", energy, 0), scalar("Power", 4.605449), "Lantern Power")
    powered = _math(nodes, links, "MULTIPLY", powered, _math(nodes, links, "GREATER_THAN", energy, 0), "Native zero-energy guard")
    # The importer stores V flipped. Convert UE tiling back to image UV space.
    tile = scalar("SmudgeTiling", .458)
    smudge_uv = nodes.new("ShaderNodeCombineXYZ")
    links.new(_math(nodes, links, "MULTIPLY", separate.outputs["X"], tile), smudge_uv.inputs["X"])
    links.new(_math(nodes, links, "SUBTRACT", 1, _math(nodes, links, "MULTIPLY", ue_v, tile)), smudge_uv.inputs["Y"])
    def image_node(file, label, vector):
        image = bpy.data.images.load(file, check_existing=True)
        image.colorspace_settings.name = "Non-Color"
        image.alpha_mode = "CHANNEL_PACKED"
        node = nodes.new("ShaderNodeTexImage")
        node.image, node.label, node.interpolation = image, label, "Linear"
        links.new(vector, node.inputs["Vector"])
        channels = nodes.new("ShaderNodeSeparateColor")
        links.new(node.outputs["Color"], channels.inputs["Color"])
        return channels.outputs["Red"]
    smudge = image_node(paths[1], "Lantern authored smudges", smudge_uv.outputs[0])
    energy = _math(nodes, links, "ADD", powered, _math(nodes, links, "MULTIPLY", smudge, scalar("SmudgeIntensityMultiplier", 2)), "Lantern smudge contribution")
    time = nodes.new("ShaderNodeValue")
    time.label = "Static emissive time (seconds)"
    time.outputs[0].default_value = 0
    flicker_uv = nodes.new("ShaderNodeCombineXYZ")
    links.new(_math(nodes, links, "MULTIPLY", time.outputs[0], scalar("FlickerSpeed", 1)), flicker_uv.inputs["X"])
    flicker_uv.inputs["Y"].default_value = 1 - scalar("FlickerTrack", .125)
    flicker = image_node(paths[0], "Lantern authored flicker at static time", flicker_uv.outputs[0])
    energy = _math(nodes, links, "MULTIPLY", energy, flicker, "Lantern flicker modulation")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (0, 0, 0, 1)
    bsdf.inputs["Emission Color"].default_value = _mi_colour(mi["colours"], "Color", default=(1, .546835, .114583, 1))
    links.new(energy, bsdf.inputs["Emission Strength"])
    links.new(_math(nodes, links, "ADD", smudge, scalar("Roughness", .104), "Lantern roughness", True), bsdf.inputs["Roughness"])
    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(bsdf.outputs[0], output.inputs["Surface"])
    material["arc_emissive_setup"] = "native-lantern-static-v1"
    material["arc_emissive_limits"] = "Static time0; UE screen-space proximity fade and weather states omitted."
    from .rebuild.graph import arrange
    arrange(material.node_tree)


def packed_emission(nodes, links, mask, mi, color):
    """Verified NEM.B / XEM.G static on-state; histogram precedes color/energy."""
    from .environment import _mi_scalar
    scalars = mi.get("scalars") or {}
    amount = _mi_scalar(scalars, "Use Histogram (0=false | 1=true)", default=0)
    if amount != 0:
        threshold = _mi_scalar(scalars, "EmissiveHistogramRange", default=.001)
        # Shader uses >=, including exact threshold equality.
        binary = _math(nodes, links, "SUBTRACT", 1, _math(nodes, links, "LESS_THAN", mask, threshold), "Emissive histogram threshold")
        mask = _math(nodes, links, "ADD", mask, _math(nodes, links, "MULTIPLY", _math(nodes, links, "SUBTRACT", binary, mask), amount), "Authored emissive histogram mix")
    intensity = _mi_scalar(scalars, "EmissiveIntensity", "Emissive Intensity", default=1)
    pulse = 1 if (mi.get("switches") or {}).get("Use CR as Emissive", False) else min(max(_mi_scalar(scalars, "PulsateAmount", default=1), 0), 1)
    energy = _math(nodes, links, "MULTIPLY", mask, intensity * pulse, "Authored emissive intensity (static pulse peak)")
    return color, energy
