"""Native M_Hair_Metahuman_ColorMap branch shared by fur card materials.

SM5 7213E480... pixel 1953 and local vertex factory 0236: sample UV1
(clamped to last UV), scaled and offset by root + Attribute.R - 0.5.
UE V is opposite Blender V. Blend is saturate(map + 1 - ColorMapBlend).
Shell fur is a separate material family; it must not use this branch.
"""


def wire_color_map(nodes, links, image, strand, root, params):
    def math(op, label, first=None, second=None):
        node = nodes.new("ShaderNodeMath")
        node.operation = op
        node.label = label
        for index, value in enumerate((first, second)):
            if value is None:
                continue
            if isinstance(value, (int, float)):
                node.inputs[index].default_value = value
            else:
                links.new(value, node.inputs[index])
        return node.outputs[0]

    tiling = float(params.get("ColorMap_Tiling", 1.0))
    offset = float(params.get("ColorMapStrandUVOffset", 0.0))
    strand_center = math("SUBTRACT", "ColorMap strand center", strand, 0.5)
    phase = math("ADD", "ColorMap root + strand", root, strand_center)
    shift = math("MULTIPLY", "Authored ColorMapStrandUVOffset", phase, offset)
    negative = math("MULTIPLY", "UE to Blender V offset", shift, -1.0)
    vector = nodes.new("ShaderNodeCombineXYZ")
    vector.label = "Native strand UV offset"
    links.new(shift, vector.inputs["X"])
    links.new(negative, vector.inputs["Y"])
    source = image.inputs["Vector"].links[0].from_socket
    scale = nodes.new("ShaderNodeVectorMath")
    scale.operation = "SCALE"
    scale.label = "Authored ColorMap_Tiling"
    links.new(source, scale.inputs[0])
    scale.inputs[3].default_value = tiling
    flip = nodes.new("ShaderNodeVectorMath")
    flip.operation = "ADD"
    flip.label = "Tiling in native UE UV space"
    links.new(scale.outputs[0], flip.inputs[0])
    flip.inputs[1].default_value = (0.0, 1.0 - tiling, 0.0)
    final = nodes.new("ShaderNodeVectorMath")
    final.operation = "ADD"
    final.label = "Native fur color-map UV"
    links.new(flip.outputs[0], final.inputs[0])
    links.new(vector.outputs[0], final.inputs[1])
    links.new(final.outputs[0], image.inputs["Vector"])
    blend = nodes.new("ShaderNodeMixRGB")
    blend.blend_type = "ADD"
    blend.use_clamp = True
    blend.label = "Native saturate(map + 1 - ColorMapBlend)"
    blend.inputs[0].default_value = 1.0
    links.new(image.outputs["Color"], blend.inputs[1])
    whiten = 1.0 - float(params.get("ColorMapBlend", 1.0))
    blend.inputs[2].default_value = (whiten, whiten, whiten, 1.0)
    return blend.outputs["Color"]
