"""Static Blender approximation for Unreal's animated CRT display material."""
from __future__ import annotations

import os

import bpy


def _parameter(mapping, *names, default=None):
    mapping = dict(mapping or {})
    wanted = {name.casefold() for name in names}
    for key, value in mapping.items():
        if str(key).casefold() in wanted:
            return value
    return default


def setup_crt_display_material(material, mi_path: str):
    """Build the authored background/noise/roughness inputs at a static time."""
    from .emission import resolved_mi
    from .environment import _mi_colour, _mi_scalar
    from .character import _find_tex_path
    from ..textures import authored_linear_sidecar, authored_srgb_sidecar

    mi = resolved_mi(mi_path)
    textures = dict(mi.get("textures") or [])
    colours = mi.get("colours") or []
    scalars = mi.get("scalars") or {}

    def find_texture(*keys):
        for key in keys:
            value = textures.get(key)
            if value:
                path = _find_tex_path(str(value))
                if path and os.path.isfile(path):
                    return path
        return ""

    background_path = find_texture("Background", "T_Visor_CRT_Background")
    noise_path = find_texture("Noise", "T_CRT_Noise")
    roughness_path = find_texture("Roughness", "T_Smudges_Visor_M")
    if not background_path:
        raise FileNotFoundError("CRT material is missing its authored Background texture")

    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (900, 0)
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.location = (620, 0)
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    shader.inputs["Metallic"].default_value = 0.0
    shader.inputs["Roughness"].default_value = max(
        0.0, min(1.0, float(_mi_scalar(scalars, "Roughness", default=0.8))),
    )
    if "Specular IOR Level" in shader.inputs:
        shader.inputs["Specular IOR Level"].default_value = 0.0

    uv = nodes.new("ShaderNodeTexCoord")
    uv.location = (-1000, 0)

    def image_node(path, label, y, non_color=False):
        # The game samples by the authored flag, not the role: T_CRT_Noise and T_Smudges_Visor_M keep the
        # default, so the sampler decodes them.
        if authored_srgb_sidecar(path):
            non_color = False
        elif authored_linear_sidecar(path):
            non_color = True
        image = bpy.data.images.load(path, check_existing=True)
        image.colorspace_settings.name = "Non-Color" if non_color else "sRGB"
        image.alpha_mode = "CHANNEL_PACKED"
        node = nodes.new("ShaderNodeTexImage")
        node.image = image
        node.label = label
        node.location = (-760, y)
        node.interpolation = "Linear"
        links.new(uv.outputs["UV"], node.inputs["Vector"])
        return node

    background = image_node(background_path, "Authored CRT background", 300)
    tint = _mi_colour(colours, "BackgroundColor", default=(1, 1, 1, 1))
    tinted = nodes.new("ShaderNodeMixRGB")
    tinted.blend_type = "MULTIPLY"
    tinted.inputs[0].default_value = 1.0
    tinted.inputs[2].default_value = tint
    tinted.label = "Authored CRT background tint"
    tinted.location = (-420, 300)
    links.new(background.outputs["Color"], tinted.inputs[1])
    screen_color = tinted.outputs["Color"]

    if noise_path:
        noise = image_node(noise_path, "Authored CRT noise (static)", 0, non_color=True)
        opacity = max(0.0, min(1.0, float(_mi_scalar(scalars, "NoiseOpacity", default=0.0))))
        mix = nodes.new("ShaderNodeMixRGB")
        mix.blend_type = "MIX"
        mix.inputs[0].default_value = opacity
        mix.label = "Authored CRT noise opacity (animation held static)"
        mix.location = (-120, 260)
        links.new(screen_color, mix.inputs[1])
        links.new(noise.outputs["Color"], mix.inputs[2])
        screen_color = mix.outputs["Color"]

    links.new(screen_color, shader.inputs["Base Color"])
    intensity = max(
        0.0,
        float(_mi_scalar(scalars, "Intensity", default=1.0)),
        float(_mi_scalar(scalars, "ScreenBacklight", default=0.0)),
    )
    from .emissive_exposure import crt_emissive_exposure, hue_preserving_knee
    emission_socket = shader.inputs.get("Emission Color") or shader.inputs.get("Emission")
    if emission_socket:
        # Strength goes into the colour so the knee can keep bright text saturated instead of clipping per channel.
        links.new(hue_preserving_knee(nodes, links, screen_color, intensity * crt_emissive_exposure(), (-120, 520)), emission_socket)
    if "Emission Strength" in shader.inputs:
        shader.inputs["Emission Strength"].default_value = 1.0

    if roughness_path:
        smudges = image_node(roughness_path, "Authored CRT visor smudges", 0, non_color=True)
        separate = nodes.new("ShaderNodeSeparateColor")
        separate.location = (-420, -220)
        links.new(smudges.outputs["Color"], separate.inputs["Color"])
        variation = nodes.new("ShaderNodeMath")
        variation.operation = "MULTIPLY"
        variation.inputs[1].default_value = 0.15
        variation.location = (-120, -220)
        links.new(separate.outputs["Red"], variation.inputs[0])
        base_roughness = nodes.new("ShaderNodeMath")
        base_roughness.operation = "MULTIPLY"
        base_roughness.inputs[0].default_value = shader.inputs["Roughness"].default_value
        base_roughness.inputs[1].default_value = 0.85
        base_roughness.location = (-120, -340)
        roughness = nodes.new("ShaderNodeMath")
        roughness.operation = "ADD"
        roughness.location = (160, -240)
        links.new(base_roughness.outputs[0], roughness.inputs[0])
        links.new(variation.outputs[0], roughness.inputs[1])
        # Keep the source scalar dominant; the smudge atlas supplies restrained
        # surface variation, not a direct replacement for roughness.
        links.new(roughness.outputs[0], shader.inputs["Roughness"])

    material["arc_crt_setup"] = "static-crt-v1"
    material["arc_crt_mi_path"] = os.path.abspath(mi_path)
    material["arc_crt_animation"] = "held-static; UV/shear/flicker animation not emulated"
    material["arc_crt_emission_strength"] = intensity
    from .rebuild.graph import arrange
    arrange(material.node_tree)
    return material
