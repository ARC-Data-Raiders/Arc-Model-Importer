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


def is_native_colormap_source(sources, unresolved_parent=''):
    """Require the exact native parent package, not ColorMap-like scalar names."""
    package='/pioneer/characters/heads/shared/hair/materials/m_hair_metahuman_colormap'
    for reference in [*sources, unresolved_parent]:
        name=str(reference or '').replace('\\','/').casefold()
        for suffix in ('.arc-material.json','.json','.native','.0'):
            if name.endswith(suffix):name=name[:-len(suffix)]
        if name.endswith(package):return True
    return False


def wire_dry_specular(nodes, links, attribute_r, attribute_a, params):
    """Native ColorMap pixel830FAA lines145,179-191; dry snow factor zero.

    q=max(1-abs(Attribute.R-.5)*(2/3),0) uses a literal DXBC coefficient,
    not a view-angle term or a preshader parameter. This ports material
    specular control; Principled still approximates the MSM_Hair BRDF.
    """
    def m(op,label,a,b=0):
        node=nodes.new('ShaderNodeMath');node.operation=op
        node.name='NativeColorMapSpec_'+label;node.label=label
        for socket,value in zip(node.inputs,(a,b)):
            if isinstance(value,(int,float)):socket.default_value=value
            else:links.new(value,socket)
        return node.outputs[0]
    def lerp(a,b,f,label):
        return m('ADD',label,a,m('MULTIPLY',label+' weighted difference',f,m('SUBTRACT',label+' difference',b,a)))
    strand=m('SUBTRACT','Strand R minus half',attribute_r,.5)
    q=m('MAXIMUM','Native strand response',m('SUBTRACT','One minus strand response',1,m('MULTIPLY','Literal two thirds',m('ABSOLUTE','Absolute strand',strand),2/3)),0)
    palette=lerp(float(params.get('Spec1',.75)),float(params.get('Spec0',.45)),attribute_a,'Spec1 to Spec0 by Attribute A')
    strand_spec=lerp(float(params.get('SpecEdge',.5)),float(params.get('SpecFront',.724)),q,'SpecEdge to SpecFront')
    return m('MINIMUM','Native dry specular clamp',m('MAXIMUM','Native dry specular nonnegative',m('MULTIPLY','Native dry specular',palette,strand_spec),0),1)
