"""Game-parameter character shading; renderer approximations remain explicit."""
import bpy
import os
import math

def build_face_normals():
    name='ARC Face Packed RG Alpha Masks 21920'
    if bpy.data.node_groups.get(name):return bpy.data.node_groups[name]
    t=bpy.data.node_groups.new(name,'ShaderNodeTree');n=t.nodes;l=t.links
    for label in ['Normal 1','Normal 2','Normal 3']:
        s=t.interface.new_socket(name=label,in_out='INPUT',socket_type='NodeSocketColor');s.default_value=(.5,.5,1,1)
    for label,default in [('NormalFlatness',0),('Hair Coverage',0),('Hair Mask Amount',1),('FacialHair Coverage',0),('FacialHair Mask Amount',0)]:
        s=t.interface.new_socket(name=label,in_out='INPUT',socket_type='NodeSocketFloat');s.default_value=default
    t.interface.new_socket(name='Normal',in_out='OUTPUT',socket_type='NodeSocketVector')
    inp=n.new('NodeGroupInput');out=n.new('NodeGroupOutput')
    def put(socket,value):
        if isinstance(value,bpy.types.NodeSocket):l.new(value,socket)
        else:socket.default_value=value
    def math(op,*values):
        q=n.new('ShaderNodeMath');q.operation=op
        for socket,value in zip(q.inputs,values):put(socket,value)
        return q.outputs[0]
    def vector(op,a,b=None,scale=None):
        q=n.new('ShaderNodeVectorMath');q.operation=op;put(q.inputs[0],a)
        if b is not None:put(q.inputs[1],b)
        if scale is not None:put(q.inputs['Scale'],scale)
        return q.outputs[0]
    def combine(x,y,z):
        q=n.new('ShaderNodeCombineXYZ')
        for socket,value in zip(q.inputs,[x,y,z]):put(socket,value)
        return q.outputs[0]
    def clamp(x):return math('MINIMUM',1,math('MAXIMUM',0,x))
    def decode(colour,label):
        q=n.new('ShaderNodeSeparateColor');q.label=label+' RG';put(q.inputs['Color'],colour)
        x=math('SUBTRACT',math('MULTIPLY',q.outputs['Red'],2),1)
        y=math('SUBTRACT',math('MULTIPLY',q.outputs['Green'],2),1)
        z=math('SQRT',math('MAXIMUM',0,math('SUBTRACT',1,math('ADD',math('MULTIPLY',x,x),math('MULTIPLY',y,y)))))
        return combine(x,y,z)
    def lerp(a,b,f):return vector('ADD',vector('SCALE',a,scale=math('SUBTRACT',1,f)),vector('SCALE',b,scale=f))
    macro=lerp(decode(inp.outputs['Normal 1'],'Macro'),(0,0,1),clamp(inp.outputs['NormalFlatness']))
    for image,coverage,amount in [('Normal 2','Hair Coverage','Hair Mask Amount'),('Normal 3','FacialHair Coverage','FacialHair Mask Amount')]:
        macro=lerp(macro,decode(inp.outputs[image],image),clamp(math('MULTIPLY',inp.outputs[coverage],inp.outputs[amount])))
    final=vector('NORMALIZE',macro)
    sep=n.new('ShaderNodeSeparateXYZ');put(sep.inputs[0],final)
    encoded=combine(math('ADD',.5,math('MULTIPLY',.5,sep.outputs['X'])),math('SUBTRACT',.5,math('MULTIPLY',.5,sep.outputs['Y'])),math('ADD',.5,math('MULTIPLY',.5,sep.outputs['Z'])))
    normal=n.new('ShaderNodeNormalMap');normal.uv_map='UV0';normal.label='Explicit DX green inversion, OpenGL map'
    if hasattr(normal,'convention'):normal.convention='OPENGL'
    put(normal.inputs['Color'],encoded);l.new(normal.outputs['Normal'],out.inputs['Normal']);return t


def prepare_face_shader(material, base_tree=None, previous_tree=None, mi_path=""):
    """Standalone native construction, or an explicit original default interface."""
    if mi_path and os.path.isfile(mi_path):
        from .character_bootstrap import prepare_native_face
        return prepare_native_face(material, mi_path, previous_tree)
    from .character_fallback import prepare_default_face
    return prepare_default_face(material, previous_tree)




def _authored_eye_material(mi_path: str, uv_name: str = "", *, allow_missing=False, previous=None):
    """Static M_EyeRefractive colour/UV response; keep the separate EyeShell.

    Shipping shader4938 and its preshaders establish the centered iris radius,
    smooth limbus and pupil remap. UE view-dependent iris refraction/concavity,
    eye subsurface lighting and runtime weather are not engine-identical here.
    """
    from .character import _mi_parent_chain_jsons, _parse_flat_mi_json, _find_tex_path
    source_present = bool(mi_path and os.path.isfile(mi_path))
    if not source_present and not allow_missing:
        return None
    scalars, colours, texture_refs = {}, {}, {}
    for path in reversed((_mi_parent_chain_jsons(mi_path) or [mi_path]) if source_present else []):
        parsed = _parse_flat_mi_json(path) or {}
        scalars.update(parsed.get("scalars") or {})
        colours.update(dict(parsed.get("colours") or []))
        texture_refs.update(dict(parsed.get("textures") or []))
    def image_path(parameter, fallback=""):
        return _find_tex_path(texture_refs.get(parameter) or fallback)
    iris_path = image_path("IrisColor", "/Game/Pioneer/Characters/Heads/Shared/Eyes/Textures/T_EyeIrisBaseColor_Example_2")
    sclera_path = image_path("ScleraColor", "/Game/Pioneer/Characters/Heads/Shared/Eyes/Textures/T_EyeScleraBaseColor_Example_2")
    normal_path = image_path("NormalMap", "/Game/Pioneer/Characters/Heads/Shared/Eyes/Textures/T_Eye_Wet_Normal")
    missing_eye = [name for name,path in (("IrisColor",iris_path),("ScleraColor",sclera_path),("NormalMap",normal_path)) if not path]
    if (not iris_path or not sclera_path) and not allow_missing:
        return None
    if allow_missing and previous is not None and previous.use_nodes:
        for control in previous.node_tree.nodes:
            if control.type == "VALUE" and control.label:
                scalars[control.label] = float(control.outputs[0].default_value)
            elif control.type == "RGB" and control.label:
                colours[control.label] = tuple(control.outputs[0].default_value)
    identity = os.path.normcase(os.path.abspath(mi_path)) if source_present else "missing-source"
    for existing in bpy.data.materials:
        if not allow_missing and existing.get("arc_eye_setup") == "authored-v2" and existing.get("arc_eye_mi_identity") == identity and existing.get("arc_eye_uv_layer", "") == uv_name:
            return existing
    mat = bpy.data.materials.new(os.path.splitext(os.path.basename(mi_path))[0] + "_Eyes")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    def node(kind, label):
        result = nodes.new(kind); result.label = label; result.name = "Eye_" + label
        return result
    def put(socket, value):
        if hasattr(value, "node"):
            links.new(value, socket)
        else:
            if isinstance(value, (tuple, list)) and len(value) == 3 and hasattr(socket.default_value, '__len__') and len(socket.default_value) == 4:
                value = tuple(value) + (1.,)
            socket.default_value = value
    def scalar(name, default):
        result = node("ShaderNodeValue", name)
        result.outputs[0].default_value = float(scalars.get(name, default))
        return result.outputs[0]
    def colour(name, default):
        result = node("ShaderNodeRGB", name)
        rgba = colours.get(name, default)
        result.outputs[0].default_value = tuple(rgba[:3]) + (1.0,)
        return result.outputs[0]
    def math_node(operation, a, b=0.0, label=""):
        result = node("ShaderNodeMath", label or operation); result.operation = operation
        put(result.inputs[0], a); put(result.inputs[1], b)
        return result.outputs[0]
    def vector(operation, a, b=(0.0, 0.0, 0.0), label=""):
        result = node("ShaderNodeVectorMath", label or operation); result.operation = operation
        put(result.inputs[0], a)
        put(result.inputs[3] if operation == "SCALE" else result.inputs[1], b)
        return result.outputs[1 if operation == "LENGTH" else 0]
    def clamp(value):
        return math_node("MINIMUM", math_node("MAXIMUM", value, 0.0), 1.0)
    def lerp(a, b, factor, label=""):
        result = node("ShaderNodeMixRGB", label or "MIX"); put(result.inputs[0], factor)
        put(result.inputs[1], a); put(result.inputs[2], b)
        return result.outputs[0]
    def texture(path, coordinates, label, non_color=False):
        if not path:
            # Original missing-image approximation. The authored coordinate and
            # tint controls remain available, without a borrowed iris template.
            if label == "Authored IrisColor":
                centered_uv = vector("SUBTRACT", coordinates, (.5,.5,0), "Procedural iris center")
                split = node("ShaderNodeSeparateXYZ", "Procedural iris polar coordinates")
                links.new(centered_uv, split.inputs[0])
                angle = math_node("ARCTAN2", split.outputs['Y'], split.outputs['X'])
                stripe = math_node("ADD", .5, math_node("MULTIPLY", .5, math_node("SINE", math_node("MULTIPLY", angle, 37.))))
                iris_color = lerp((.035,.065,.02,1.), (.18,.28,.10,1.), stripe, "Original iris striations")
                pupil_mask = math_node("LESS_THAN", vector("LENGTH", centered_uv), .20)
                result = node("ShaderNodeMixRGB", "Original procedural pupil")
                put(result.inputs[0], pupil_mask);put(result.inputs[1], iris_color);put(result.inputs[2], (.001,.001,.001,1.))
                return result
            result = node("ShaderNodeRGB", "Missing sclera texture neutral source")
            result.outputs[0].default_value = (.8,.8,.8,1.)
            return result
        image = bpy.data.images.load(path, check_existing=True)
        wanted_space = "Non-Color" if non_color else "sRGB"
        if image.colorspace_settings.name != wanted_space:
            image = image.copy()
            image.colorspace_settings.name = wanted_space
        image.alpha_mode = "CHANNEL_PACKED"
        result = node("ShaderNodeTexImage", label); result.image = image; result.interpolation = "Linear"
        links.new(coordinates, result.inputs["Vector"])
        return result
    uv = node("ShaderNodeUVMap", "Authored UV0"); uv.uv_map = uv_name
    centered = vector("SUBTRACT", uv.outputs[0], (0.5, 0.5, 0.0), "UV0 centered")
    scale = math_node("MAXIMUM", scalar("ScaleByCenter", 1.0), 1e-5)
    centered = vector("SCALE", centered, math_node("DIVIDE", 1.0, scale), "Centered eye coordinates")
    sclera_uv = vector("ADD", centered, (0.5, 0.5, 0.0), "Sclera UV")
    distance = vector("LENGTH", centered, label="Eye radius")
    radius = math_node("MAXIMUM", scalar("Iris UV Radius", 0.167), 0.001)
    width = math_node("MAXIMUM", scalar("LimbusUVWidthColor", 0.035), 1e-5)
    boundary = clamp(math_node("DIVIDE", math_node("SUBTRACT", radius, distance), width))
    mask = math_node("MULTIPLY", math_node("MULTIPLY", boundary, boundary), math_node("SUBTRACT", 3.0, math_node("MULTIPLY", 2.0, boundary)), "Authored smooth iris mask")
    radial = math_node("DIVIDE", distance, math_node("MULTIPLY", 2.0, radius))
    pupil = clamp(math_node("MULTIPLY", scalar("PupilScale", 0.7), math_node("SUBTRACT", 1.0, math_node("MULTIPLY", 2.0, radial))))
    direction = vector("SCALE", centered, math_node("DIVIDE", 1.0, math_node("MAXIMUM", distance, 1e-8)), "Iris radial direction")
    offset = vector("SCALE", direction, math_node("MULTIPLY", 0.5, math_node("SUBTRACT", 1.0, pupil)), "Authored pupil remap")
    iris_uv = vector("ADD", offset, (0.5, 0.5, 0.0), "Iris UV")
    iris = texture(iris_path, iris_uv, "Authored IrisColor")
    sclera = texture(sclera_path, sclera_uv, "Authored ScleraColor")
    rotate = node("ShaderNodeVectorRotate", "IrisHueShift"); rotate.rotation_type = "AXIS_ANGLE"
    rotate.inputs["Axis"].default_value = (0.577350269, 0.577350269, 0.577350269)
    links.new(iris.outputs["Color"], rotate.inputs["Vector"])
    put(rotate.inputs["Angle"], math_node("MULTIPLY", scalar("IrisHueShift", 0.0), math.tau))
    ic = vector("MULTIPLY", rotate.outputs[0], colour("IrisHueTint", (1, 1, 1, 1)), "Authored iris tint")
    limbus_radius = vector("LENGTH", vector("SCALE", offset, scalar("LimbusDarkScale", 2.15)))
    limbus = math_node("SUBTRACT", 1.0, math_node("POWER", limbus_radius, scalar("LimbusPow", 8.0)), "Authored limbus darkness")
    ic = vector("SCALE", ic, math_node("MULTIPLY", limbus, scalar("IrisBrightness", 1.2)))
    sc = vector("MULTIPLY", sclera.outputs["Color"], colour("ScleraTint", (1, 1, 1, 1)))
    sc = vector("SCALE", sc, scalar("ScleraBrightness", 0.9))
    shadow_radius = math_node("MAXIMUM", scalar("ShadowRadius", 0.65), 1e-5)
    corner = clamp(math_node("DIVIDE", math_node("SUBTRACT", 1.0, math_node("DIVIDE", distance, shadow_radius)), math_node("MAXIMUM", math_node("SUBTRACT", 1.0, scalar("ShadowHardness", 0.345133)), 1e-5)))
    shadow = lerp(colour("EyeCornerDarknessColor", (0.23, 0.083042, 0.031906, 1)), (1, 1, 1, 1), corner)
    base = vector("MULTIPLY", lerp(sc, ic, mask, "Iris / sclera"), shadow, "Authored eye colour")
    base = vector("MINIMUM", vector("MAXIMUM", base, (0, 0, 0)), (1, 1, 1), "Saturated eye colour")
    principled = node("ShaderNodeBsdfPrincipled", "Static eye surface")
    links.new(base, principled.inputs["Base Color"])
    sclera_rough = scalar("ScleraRoughness", 0.1)
    rough = math_node("ADD", sclera_rough, math_node("MULTIPLY", mask, math_node("SUBTRACT", scalar("IrisRoughness", 0.1), sclera_rough)))
    sclera_spec = scalar("SpecularityScelera", 1.0)
    spec = math_node("ADD", sclera_spec, math_node("MULTIPLY", mask, math_node("SUBTRACT", scalar("SpecularityIris", 1.0), sclera_spec)))
    links.new(rough, principled.inputs["Roughness"]); links.new(spec, principled.inputs["Specular IOR Level"])
    # IoR controls the game's iris refraction, not UE's independent Specular
    # F0. At IOR1.5 Blender maps Specular IOR Level directly to .08*Specular.
    principled.inputs["IOR"].default_value = 1.5
    refraction_ior = scalar("Authored refraction IoR (engine effect)", float(scalars.get("IoR", 1.336)))
    mat["arc_eye_refraction_ior"] = float(refraction_ior.default_value)
    mat["arc_eye_bsdf_ior"] = 1.5
    if normal_path:
        normal_scale = math_node("MAXIMUM", scalar("NormalUVScale", 2.4), 1e-5)
        normal_uv = vector("ADD", vector("SCALE", vector("SUBTRACT", uv.outputs[0], (0.5, 0.5, 0)), math_node("DIVIDE", 1.0, normal_scale)), (0.5, 0.5, 0), "Normal UV")
        normal = texture(normal_path, normal_uv, "Authored NormalMap", non_color=True)
        separate = node("ShaderNodeSeparateColor", "Packed normal RG"); links.new(normal.outputs["Color"], separate.inputs[0])
        from .environment import _packed_rg_normal_color
        encoded = _packed_rg_normal_color(nodes, links, separate.outputs["Red"], separate.outputs["Green"])
        flat = scalar("FlattenNormal", 0.95)
        flat = math_node("ADD", flat, math_node("MULTIPLY", mask, math_node("SUBTRACT", 1.0, flat)), "Authored normal flattening")
        encoded = lerp(encoded, (0.5, 0.5, 1.0, 1.0), flat)
        normal_map = node("ShaderNodeNormalMap", "Authored DirectX eye normal")
        normal_map.uv_map = uv_name
        try:
            normal_map.convention = "DIRECTX"
        except (AttributeError, TypeError):
            # Older Blender has only OpenGL tangent normals: flip G once.
            channels = node("ShaderNodeSeparateColor", "Legacy DirectX channels")
            links.new(encoded, channels.inputs["Color"])
            combine = node("ShaderNodeCombineColor", "Legacy DirectX green flip")
            links.new(channels.outputs["Red"], combine.inputs["Red"])
            put(combine.inputs["Green"], math_node("SUBTRACT", 1.0, channels.outputs["Green"]))
            links.new(channels.outputs["Blue"], combine.inputs["Blue"])
            encoded = combine.outputs["Color"]
        links.new(encoded, normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
    output = node("ShaderNodeOutputMaterial", "Material Output")
    links.new(principled.outputs[0], output.inputs["Surface"])
    mat["arc_head_role"] = "eyes"
    if mi_path:
        mat["arc_mi_path"] = os.path.abspath(mi_path)
    mat["arc_eye_mi_identity"] = identity
    mat["arc_eye_uv_layer"] = uv_name
    mat["arc_eye_setup"] = "fallback-original-v1" if allow_missing else "authored-v2"
    mat["arc_eye_static_approximation"] = "Authored radius/pupil/limbus/textures/colour; UE corneal refraction, concavity, eye SSS lighting and weather differ. Separate EyeShell retained."
    if allow_missing:
        from .character_fallback import finish, surface_controls, apply_controls
        apply_controls(principled, surface_controls(previous, 'eyes'))
        finish(mat, 'eyes', ['Missing eye source/resources: '+', '.join(missing_eye or ['native material lineage']), 'Original procedural iris/neutral sclera used only for absent images; corneal refraction/concavity/SSS/weather remain Blender approximations'])
    else:
        from ..surface_rebuild.graph import arrange
        arrange(mat.node_tree)
    return mat
