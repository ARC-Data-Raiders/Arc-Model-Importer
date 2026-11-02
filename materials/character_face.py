"""Recovered static M_Face response with independent NXA coverage sockets.

Preserves the existing material-owned Face Shader interface so the hair/scalp
bridge still works. Packed normal B supplies roughness, not normal Z. Native
lighting, body-paint/weather and UE subsurface remain unverified.
"""
from __future__ import annotations

import json
import os

from .character_body import ResponseGraph, image_for_role, read_character_parameters

SCALARS = {
    "NormalFlatness": 0, "MicroNormal_Intensity": 1, "Tile_Size": 20,
    "Hair Mask Amount": 0, "Hair Roughness": .75, "Hair Specular": .45,
    "FacialHair Mask Amount": 0, "FacialHair Roughness": .75, "FacialHair Specular": .45,
    "Cavity Color Intensity": 0, "MicroCavity Color Intensity": 0,
    "Specular": .25, "LipSpecular": .25, "Specular Cavity Amount": 0,
    "Specular MicroCavity Amount": 0, "Cavity Roughness": 0,
    "Micro Cavity Roughness": 0, "Zone_Roughness": .54,
    "RoughnessMultiplier": 1, "Lip Roughness": 0, "FadeAmount": 1.165878, "s": .477764,
    "Mask_FaceFresnel": 2, "Mask_FaceFresnel_Contrast": .5,
    "Fresnel": .5, "FresnelContrast": 1, "EarFresnel": 0,
}
COLORS = {"Cavity Color": (0, 0, 0, 1), "HairCap Root Color": (0, 0, 0, 1),
          "HairCap Tip Color": (1, 1, 1, 1), "FacialHair Root Color": (0, 0, 0, 1),
          "FacialHair Tip Color": (1, 1, 1, 1)}
FIXED_TEXTURES = {
    "Micro Normal": "/Game/Pioneer/Characters/Heads/Shared/Textures/Head_01_MicroNormal",
    "Zone Masks": "/Game/Pioneer/Characters/Heads/Shared/Face/Textures/T_ContestantHeads_ZONEMASKS",
    "Mask Texture": "/Game/Pioneer/Characters/Heads/Shared/Textures/T_Head_01_MASKS",
    "Neck Fade": "/Game/Pioneer/Characters/Heads/Shared/Textures/T_Face_NeckFade",
}


def parameters(mi_path):
    record = read_character_parameters(mi_path, SCALARS)
    record["colors"] = dict(COLORS, **record["colors"])
    for name, reference in FIXED_TEXTURES.items():
        if not record["textures"].get(name):
            record["textures"][name] = reference
    return record


def _socket(tree, name, kind, default, direction="INPUT"):
    item = next((i for i in tree.interface.items_tree if i.item_type == "SOCKET"
                 and i.in_out == direction and i.name == name), None)
    if item is None:
        item = tree.interface.new_socket(name=name, in_out=direction, socket_type=kind)
    if default is not None:
        item.default_value = default
    return item


def build_face_tree(tree, record):
    """Replace the legacy ramp/overlay response, preserving user SSS controls."""
    old_bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    sss = {}
    for name in ("Subsurface Weight", "Subsurface Radius", "Subsurface Scale", "Subsurface IOR", "Subsurface Anisotropy"):
        if old_bsdf is not None and old_bsdf.inputs.get(name) is not None:
            value = old_bsdf.inputs[name].default_value
            sss[name] = tuple(value) if hasattr(value, "__len__") else value
    for name in ("Face Albedo", "Normal 1", "Normal 2", "Normal 3", "Blue Map", "Micro Normal", "Zone Masks"):
        default = (.5, .5, 1, 1) if "Normal" in name else (1, 1, 1, 1)
        _socket(tree, name, "NodeSocketColor", default)
    _socket(tree, "Actor Origin Local", "NodeSocketVector", (0, 0, 0))
    for name, default in (("Base Cavity", 1), ("Hair Coverage", 0), ("FacialHair Coverage", 0),
                          ("Micro Cavity", 1), ("Neck Fade", 1)):
        _socket(tree, name, "NodeSocketFloat", default)
    for name, default in SCALARS.items():
        if name != "Tile_Size":
            _socket(tree, name, "NodeSocketFloat", float(record["scalars"].get(name, default)))
    for name, default in COLORS.items():
        _socket(tree, name, "NodeSocketColor", record["colors"].get(name, default))
    _socket(tree, "BSDF", "NodeSocketShader", None, "OUTPUT")
    for name, kind in (("Native Color", "NodeSocketColor"), ("Native Roughness", "NodeSocketFloat"),
                       ("Native Specular", "NodeSocketFloat"), ("Native Alpha", "NodeSocketFloat"),
                       ("Native Tangent Normal", "NodeSocketVector"), ("Native Face Fresnel", "NodeSocketFloat")):
        _socket(tree, name, kind, None, "OUTPUT")
    tree.nodes.clear()
    g = ResponseGraph(tree.nodes, tree.links, "Face_")
    inp = g.node("NodeGroupInput", "Authored inputs")
    out = g.node("NodeGroupOutput", "Face outputs")
    I = lambda name: inp.outputs[name]
    def channel(value, index):
        node = g.node("ShaderNodeSeparateColor", "Packed channels")
        g.put(node.inputs["Color"], value)
        return node.outputs[index]
    cavity = I("Base Cavity")
    mask = channel(I("Blue Map"), 2)
    geometry = g.node("ShaderNodeNewGeometry", "Native face view and vertex normal")
    actor = g.node("ShaderNodeVectorTransform", "Actor origin local to world")
    actor.vector_type = "POINT"
    actor.convert_from, actor.convert_to = "OBJECT", "WORLD"
    g.put(actor.inputs["Vector"], I("Actor Origin Local"))
    center = g.vector("ADD", actor.outputs["Vector"], (0, 0, .05), "Native actor origin plus five centimetres")
    radial = g.vector("NORMALIZE", g.vector("SUBTRACT", geometry.outputs["Position"], center))
    radial_dot = g.clamp(g.vector("DOT_PRODUCT", radial, geometry.outputs["Incoming"]))
    vertex_dot = g.clamp(g.vector("DOT_PRODUCT", geometry.outputs["Normal"], geometry.outputs["Incoming"]))
    def safe_power(value, exponent):
        # Native log/exp guards base<=0 and returns0, including exponent0.
        positive = g.math("GREATER_THAN", value, 0)
        return g.math("MULTIPLY", positive, g.math("POWER", g.math("MAXIMUM", value, 1e-8), exponent))
    def contrast(value, amount):
        return g.clamp(g.math("SUBTRACT", g.math("MULTIPLY", value,
                       g.math("ADD", 1, g.math("MULTIPLY", 2, amount))), amount))
    radial_mask = contrast(safe_power(g.math("SUBTRACT", 1, radial_dot), I("Mask_FaceFresnel")), I("Mask_FaceFresnel_Contrast"))
    vertex_fresnel = contrast(safe_power(vertex_dot, I("Fresnel")), I("FresnelContrast"))
    ear = g.math("MULTIPLY", g.math("SUBTRACT", 1, channel(I("Zone Masks"), 2)), I("EarFresnel"))
    face_fresnel = g.math("ADD", g.math("MULTIPLY", radial_mask, g.math("SUBTRACT", 1, vertex_fresnel)), ear,
                          "Native actor radial and ear fresnel")
    extra_response = g.math("MULTIPLY", channel(I("Blue Map"), 0), face_fresnel, "Native masked face surface response")
    micro_cavity = g.math("MAXIMUM", g.math("SUBTRACT", 1, mask), I("Micro Cavity"))
    cavity_factor = g.math("ADD", 1, g.math("MULTIPLY", g.math("SUBTRACT", cavity, 1), I("Cavity Color Intensity")))
    micro_factor = g.math("ADD", 1, g.math("MULTIPLY", g.math("SUBTRACT", micro_cavity, 1), I("MicroCavity Color Intensity")))
    color = g.vlerp(I("Cavity Color"), I("Face Albedo"), g.math("MULTIPLY", cavity_factor, micro_factor), "Cavity colour")
    zone_rough = g.lerp(I("Zone_Roughness"), 1, channel(I("Zone Masks"), 1))
    rough = g.math("MINIMUM", channel(I("Normal 1"), 2), zone_rough)
    rough = g.math("MINIMUM", rough, g.math("MAXIMUM", g.math("SUBTRACT", 1, I("Lip Roughness")), mask))
    rough = g.math("ADD", rough, extra_response, "Native face fresnel roughness")
    rough = g.math("ADD", g.math("MULTIPLY", rough, I("RoughnessMultiplier")),
                   g.math("MULTIPLY", g.math("SUBTRACT", 1, cavity), I("Cavity Roughness")))
    rough = g.math("ADD", rough, g.math("MULTIPLY", g.math("SUBTRACT", 1, micro_cavity), I("Micro Cavity Roughness")))
    spec = g.lerp(I("LipSpecular"), I("Specular"), mask)
    for value, amount in ((cavity, I("Specular Cavity Amount")), (micro_cavity, I("Specular MicroCavity Amount"))):
        spec = g.math("MULTIPLY", spec, g.math("ADD", 1, g.math("MULTIPLY", g.math("SUBTRACT", value, 1), amount)))
    spec = g.math("ADD", spec, extra_response, "Native face fresnel specular")
    normal = g.vlerp(g.decode_rg(I("Normal 1")), (0, 0, 1), I("NormalFlatness"))
    micro = g.decode_rg(I("Micro Normal"))
    mx, my, mz = g.split(micro)
    micro = g.combine(g.math("MULTIPLY", mx, I("MicroNormal_Intensity")),
                      g.math("MULTIPLY", my, I("MicroNormal_Intensity")), mz)
    normal = g.rnm(normal, g.vlerp((0, 0, 1), micro, mask))
    if record.get("switches", {}).get("UseHair", True):
        for label, normal_input, coverage, amount, root, tip, rough_name, spec_name in (
            ("Scalp", "Normal 2", "Hair Coverage", "Hair Mask Amount", "HairCap Root Color", "HairCap Tip Color", "Hair Roughness", "Hair Specular"),
            ("Facial", "Normal 3", "FacialHair Coverage", "FacialHair Mask Amount", "FacialHair Root Color", "FacialHair Tip Color", "FacialHair Roughness", "FacialHair Specular")):
            factor = g.clamp(g.math("MULTIPLY", I(coverage), I(amount)))
            tint = g.vlerp(I(root), I(tip), channel(I(normal_input), 2), label + " root / tip")
            color = g.vlerp(color, tint, factor, label + " colour coverage")
            rough = g.lerp(rough, I(rough_name), factor, label + " roughness coverage")
            spec = g.lerp(spec, I(spec_name), factor, label + " specular coverage")
            normal = g.vlerp(normal, g.decode_rg(I(normal_input)), factor, label + " normal coverage")
    normal = g.vector("NORMALIZE", normal)
    color = g.vector("MINIMUM", g.vector("MAXIMUM", color, (0, 0, 0)), (1, 1, 1))
    rough, spec = g.clamp(rough), g.clamp(spec)
    fade = g.clamp(g.math("MULTIPLY", I("Neck Fade"), I("FadeAmount")))
    alpha = g.clamp(g.math("ADD", g.math("MULTIPLY", g.math("FLOOR", g.math("ADD", I("Neck Fade"), I("s"))),
                                       g.math("SUBTRACT", 1, fade)), fade))
    p = g.node("ShaderNodeBsdfPrincipled", "Recovered skin surface")
    unsupported_sss = []
    for name, value in sss.items():
        socket = p.inputs.get(name)
        if socket is not None:
            socket.default_value = value
        else:
            # Loaded older Principled nodes can retain removed sockets (e.g.
            # Subsurface IOR). Preserve the value as migration evidence; it is
            # not the new surface IOR and must not be remapped to that socket.
            unsupported_sss.append({"name": name, "value": value})
    tree["arc_face_legacy_sss_unmapped"] = json.dumps(unsupported_sss)
    p.inputs["IOR"].default_value = 1.5
    p.inputs["Specular Tint"].default_value = (1, 1, 1, 1)
    p.inputs["Coat Weight"].default_value = 0
    for name, value in (("Base Color", color), ("Roughness", rough), ("Specular IOR Level", spec), ("Alpha", alpha)):
        g.put(p.inputs[name], value)
    g.put(p.inputs["Normal"], g.normal_to_world(normal))
    g.put(out.inputs["BSDF"], p.outputs[0])
    for name, value in (("Native Color", color), ("Native Roughness", rough), ("Native Specular", spec),
                        ("Native Alpha", alpha), ("Native Tangent Normal", normal), ("Native Face Fresnel", face_fresnel)):
        g.put(out.inputs[name], value)
    tree["arc_face_native_revision"] = "static-v1"
    tree["arc_face_parameter_sources"] = json.dumps(record["sources"])
    tree["arc_face_unresolved_parent"] = record.get("unresolved_parent", "")
    return tree


def wire_face_textures(nodes, links, group, record, *, obj=None, source_path=""):
    """Bind map channels explicitly and preserve the scalp bridge's old sockets."""
    from .character import _find_tex_path, _hair_source_asset
    if obj is not None:
        origin, evidence = actor_origin_local(obj, source_path)
        group.inputs["Actor Origin Local"].default_value = origin
        group["arc_face_actor_origin_evidence"] = json.dumps(evidence)
    g = ResponseGraph(nodes, links, "FaceMap_")
    uv = g.node("ShaderNodeUVMap", "UV0")
    uv.uv_map = "UV0"
    missing = []
    bindings = (("BaseColor", "Face Albedo", False), ("Normal", "Normal 1", True),
                ("Mask Texture", "Blue Map", True), ("Micro Normal", "Micro Normal", True),
                ("Zone Masks", "Zone Masks", True), ("Hair NXA", "Normal 2", True),
                ("FacialHairNXA", "Normal 3", True), ("Neck Fade", "Neck Fade", True))
    for parameter, socket, data in bindings:
        ref = record["textures"].get(parameter)
        if not ref:
            if parameter in {"BaseColor", "Normal"}:
                ref = record["textures"].get("PM_Diffuse" if parameter == "BaseColor" else "PM_Normals")
            if not ref:
                continue
        # raw_record canonicalizes /Game to PioneerGame/Content. Resolve
        # that exact package in this import's physical mount before consulting
        # global roots, which may belong to another export/cache generation.
        path = _hair_source_asset(source_path, str(ref), ".png") if source_path else ""
        if not path:
            unreal_ref = str(ref)
            if unreal_ref.startswith("PioneerGame/Content/"):
                unreal_ref = "/Game/" + unreal_ref[len("PioneerGame/Content/"):]
            image_path = _find_tex_path(unreal_ref)
            path = image_path or path
        image = image_for_role(path, non_color=data)
        if image is None:
            missing.append(parameter + ":" + str(ref))
            continue
        coords = uv.outputs[0]
        if parameter == "Micro Normal":
            coords = g.vector("SCALE", coords, g.value(record["scalars"].get("Tile_Size", 20), "Tile_Size"))
        tex = g.texture(image, coords, parameter)
        if parameter == "Neck Fade":
            sep = g.node("ShaderNodeSeparateColor", "Neck fade R")
            links.new(tex.outputs["Color"], sep.inputs["Color"])
            links.new(sep.outputs[0], group.inputs[socket])
        else:
            links.new(tex.outputs["Color"], group.inputs[socket])
        if parameter == "BaseColor":
            links.new(tex.outputs["Alpha"], group.inputs["Base Cavity"])
        elif parameter == "Hair NXA":
            links.new(tex.outputs["Alpha"], group.inputs["Hair Coverage"])
        elif parameter == "FacialHairNXA":
            links.new(tex.outputs["Alpha"], group.inputs["FacialHair Coverage"])
        elif parameter == "Micro Normal":
            sep = g.node("ShaderNodeSeparateColor", "Micro cavity B")
            links.new(tex.outputs["Color"], sep.inputs["Color"])
            links.new(sep.outputs[2], group.inputs["Micro Cavity"])
    return missing


def actor_origin_local(obj, source_path=""):
    """Recover native actor origin exactly after a translated mesh-origin change.

    Uniform source→basis translation is proved over every vertex. Shader POINT
    transformation then follows the object's live world transform automatically.
    An edited/mismatched mesh is reported explicitly; no skull/foot guess occurs.
    """
    if obj is None or getattr(obj, "type", "") != "MESH":
        return (0, 0, 0), {"exact": False, "reason": "No mesh context"}
    override = obj.get("arc_face_actor_origin_local")
    if override is not None and len(override) == 3:
        return tuple(override), {"exact": False, "reason": "Explicit user actor-origin override"}
    path = source_path or str(obj.get("arc_source_mesh") or obj.get("arc_psk_path") or "")
    if not path.lower().endswith(".uemodel"):
        path = os.path.splitext(path)[0] + ".uemodel"
    if not os.path.isfile(path):
        return (0, 0, 0), {"exact": not bool(obj.get("arc_origin_recentered")),
                           "reason": "Unrecentered object origin" if not obj.get("arc_origin_recentered") else "Native source mesh unavailable"}
    try:
        import numpy as np
        from .. import uemodel_import
        scale = float(obj.data.get("arc_uemodel_scale", 1))
        _name, _version, lods = uemodel_import.parse_uemodel(path, scale=scale)
        count = len(obj.data.vertices)
        candidates = [lod for lod in lods if len(lod.vertices) == count]
        if not candidates:
            return (0, 0, 0), {"exact": False, "reason": "Source LOD topology mismatch", "source": path}
        lod_name = str(obj.data.get("arc_uemodel_lod") or "")
        lod = next((lod for lod in candidates if lod.name == lod_name), candidates[0])
        current = np.empty(count * 3, np.float32)
        obj.data.vertices.foreach_get("co", current)
        delta = current.reshape(count, 3) - np.asarray(lod.vertices)
        origin = delta[0]
        error = float(np.max(np.abs(delta - origin)))
        if error > 1e-5:
            return (0, 0, 0), {"exact": False, "reason": "Source basis is not a uniform translation", "max_error": error, "source": path}
        return tuple(float(x) for x in origin), {"exact": True, "source": path, "vertices": count, "translation_error": error}
    except (OSError, ValueError, RuntimeError) as exc:
        return (0, 0, 0), {"exact": False, "reason": str(exc), "source": path}
