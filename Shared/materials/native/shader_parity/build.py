"""Build cached O5.5 node groups from installed shader plans."""
from pathlib import Path
import bpy
from . import lower_blender as lb, lower_dag, xform


def uv_name(obj, k):
    names = None if obj is None else [layer.name for layer in getattr(obj.data, "uv_layers", ())]
    return xform.uv_name(names, k)


def _attributes(nt):
    memo = {}
    def get(kind, name):
        key = (kind, name)
        if key not in memo:
            node = nt.nodes.new("ShaderNodeAttribute")
            node.attribute_type = kind
            node.attribute_name = name
            split = nt.nodes.new("ShaderNodeSeparateXYZ")
            nt.links.new(node.outputs["Vector"], split.inputs[0])
            memo[key] = dict(zip("xyzw", [split.outputs[0], split.outputs[1], split.outputs[2], node.outputs["Alpha"]]))
        return memo[key]
    return get


def sources(nt, plan, uv1):
    attr = _attributes(nt)
    geom = nt.nodes.new("ShaderNodeNewGeometry")
    tangent = nt.nodes.new("ShaderNodeTangent")
    tangent.direction_type = "UV_MAP"
    tangent.uv_map = ""
    def split(socket):
        node = nt.nodes.new("ShaderNodeSeparateXYZ")
        nt.links.new(socket, node.inputs[0])
        return list(node.outputs)
    position = split(geom.outputs["Position"])
    normal = split(geom.outputs["Normal"])
    tan = split(tangent.outputs["Tangent"])
    camera = attr("VIEW_LAYER", "dr_cam")
    uv = []
    for name in ("", uv1):
        node = nt.nodes.new("ShaderNodeUVMap")
        node.uv_map = name
        values = split(node.outputs[0])
        uv.append([values[0], lb.math(nt, "SUBTRACT", 1.0, values[1])])
    sign = nt.nodes.new("ShaderNodeAttribute")
    sign.attribute_type = "GEOMETRY"
    sign.attribute_name = "dr_tangent_sign"
    values = {}
    translated = [lb.math(nt, "SUBTRACT", lb.math(nt, "MULTIPLY", position[i], (100.0, -100.0, 100.0)[i]), camera["xyz"[i]]) for i in range(3)]
    for row in plan["sources"]:
        kind, i = row["kind"], row["index"]
        if kind == "position_t":
            value = translated[i]
        elif kind == "tangent":
            value = lb.math(nt, "MULTIPLY", tan[i], -1.0 if i == 1 else 1.0)
        elif kind == "normal":
            value = lb.math(nt, "MULTIPLY", normal[i], -1.0 if i == 1 else 1.0)
        elif kind == "tangent_sign":
            value = sign.outputs["Fac"]
        elif kind in ("uv0", "uv1"):
            value = uv[int(kind[-1])][i]
        else:
            raise ValueError("source:" + kind)
        values[(row["reg"], row["comp"])] = value
    for row in plan["params"]:
        value = attr(row["attribute_type"], row["attribute"])[row["component"]]
        if row["negate"]:
            value = lb.math(nt, "MULTIPLY", value, -1.0)
        values[(row["reg"], row["comp"])] = value
    count = len({r["attribute"] for r in plan["params"] if r["attribute_type"] == "OBJECT"})
    return values, count


def images(plan):
    from .... import pak_extract; from .....Outfit_Accessories import pak_catalog
    result = {}
    all_exr = True
    for row in plan["textures"]:
        path = plan.directory / row["image"]
        exr = path.is_file()
        if not exr:
            path = Path(pak_extract.cache_path()) / (pak_catalog.canonical_path(row["object_path"]) + ".png")
            if not path.is_file():
                raise FileNotFoundError(path)
        image = bpy.data.images.load(str(path), check_existing=True)
        image.colorspace_settings.name = "Non-Color" if exr or not row["srgb"] else "sRGB"
        image.alpha_mode = "CHANNEL_PACKED"
        result[row["slot"]] = image
        all_exr = all_exr and exr
    return result, "exr" if all_exr else "png"


def _group(name, plan, uv1, mode, image_map):
    ng = bpy.data.node_groups.new(name, "ShaderNodeTree")
    names = ["o3." + c for c in "xyz"] + ["o2." + c for c in "xyz"] + ["o1." + c for c in "xyz"]
    if mode == "check":
        for label in names:
            ng.interface.new_socket(name=label, in_out="OUTPUT", socket_type="NodeSocketFloat")
    else:
        for label, typ in [("Base Color", "NodeSocketColor"), ("Metallic", "NodeSocketFloat"), ("Specular IOR Level", "NodeSocketFloat"), ("Roughness", "NodeSocketFloat"), ("Normal", "NodeSocketVector")]:
            ng.interface.new_socket(name=label, in_out="OUTPUT", socket_type=typ)
    src, count = sources(ng, plan, uv1)
    output = lower_dag.lower(ng, plan.dag(), src, image_map, names, mode)
    node = ng.nodes.new("NodeGroupOutput")
    if mode == "check":
        for label in names:
            ng.links.new(output[label], node.inputs[label])
    else:
        color = ng.nodes.new("ShaderNodeCombineColor")
        for i, c in enumerate("xyz"):
            ng.links.new(output["o3." + c], color.inputs[i])
        normal = []
        for c in "xyz":
            value = lb.math(ng, "SUBTRACT", lb.math(ng, "MULTIPLY", output["o1." + c], 2.0), 1.0)
            if c == "y":
                value = lb.math(ng, "MULTIPLY", value, -1.0)
            normal.append(value)
        length = lb.sqrt(ng, lb.dp3(ng, normal, normal))
        vector = ng.nodes.new("ShaderNodeCombineXYZ")
        for i, value in enumerate(normal):
            ng.links.new(lb.div(ng, value, length), vector.inputs[i])
        for label, value in [("Base Color", color.outputs[0]), ("Metallic", output["o2.x"]), ("Specular IOR Level", output["o2.y"]), ("Roughness", output["o2.z"]), ("Normal", vector.outputs[0])]:
            ng.links.new(value, node.inputs[label])
    ng["dr_parity_object_attrs"] = count
    return ng


def setup_material(mat, plan, obj=None, mode="production"):
    uv1 = uv_name(obj, 1)
    short = plan["material_instance"].rsplit("/", 1)[-1]
    name = "DR " + short + ("" if uv1 == "UV1" else " [" + (uv1 or "UV0") + "]") + (" (check)" if mode == "check" else "")
    image_map, texture_kind = images(plan)
    group = next((g for g in bpy.data.node_groups if g.get("dr_parity_plan_sha256") == plan.stamp and g.get("dr_parity_uv1") == uv1 and g.get("dr_parity_mode") == mode), None)
    if group is None:
        group = _group(name, plan, uv1, mode, image_map)
        group["dr_parity_plan_sha256"] = plan.stamp
        group["dr_parity_uv1"] = uv1
        group["dr_parity_mode"] = mode
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    node = nt.nodes.new("ShaderNodeGroup")
    node.node_tree = group
    if mode == "production":
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.inputs["IOR"].default_value = 1.5
        for label in ("Base Color", "Metallic", "Specular IOR Level", "Roughness", "Normal"):
            nt.links.new(node.outputs[label], bsdf.inputs[label])
        output = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(bsdf.outputs[0], output.inputs["Surface"])
    mat["arc_mi_family"] = "shader_parity"
    mat["dr_parity_plan_sha256"] = plan.stamp
    mat["dr_parity_runtime"] = "parity-1"
    mat["dr_parity_textures"] = texture_kind
    mat["dr_parity_uv1"] = uv1
    mat["dr_parity_mode"] = mode
    count = int(group["dr_parity_object_attrs"])
    mat["dr_parity_object_attrs"] = count
    if count > 8:
        mat["dr_parity_eevee_warning"] = str(count) + " object attributes > 8"
    return mat
