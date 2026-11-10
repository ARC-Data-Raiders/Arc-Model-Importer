"""Native static eye occlusion and tear-line profiles, kept separate from the iris.

Current SM5: EyeOcclusion37A92F74 / PS3761, TearLine0172BCCF / PS7828.
Camera-only occlusion transmission is exact when Blur Size=0. Scene-color blur
and tear-line scene-depth sampling remain explicitly unavailable here.
"""
from __future__ import annotations
import hashlib
import json
import os

import bpy

from .character_body import ResponseGraph, read_character_parameters

_AUX_MATERIALS = {}
OCCLUSION_DEFAULTS = {"Blur Size": 0, "ShadowMultiply": 0}
for _edge in ("Outer", "Inner", "Bottom", "Top"):
    for _kind, _radius in (("Shadow", 0), ("ShadowBorder", .05), ("Blur", .3), ("BlurBorder", .05)):
        OCCLUSION_DEFAULTS[_edge + _kind + "_Radius"] = _radius
        OCCLUSION_DEFAULTS[_edge + _kind + "_Hardness"] = 0
TEAR_DEFAULTS = {"d": 1, "m": 1, "Param": 1, "roughness": .2,
                 "Exp": 1, "depthFade": .1, "cc": 1, "o": .5, "Visibility": 1}


def occlusion_transmittance(g, uv, scalars, colors):
    """Native edge products, squared border mask, tint and scalar transmission."""
    u, v, _ = g.split(uv)
    distances = {"Outer": u, "Inner": g.math("SUBTRACT", u, 1),
                 "Bottom": g.math("SUBTRACT", v, 1), "Top": v}
    def clears(kind):
        product = 1
        for edge, distance in distances.items():
            radius = g.value(scalars.get(edge + kind + "_Radius", 0), edge + kind + "_Radius")
            hardness = g.value(scalars.get(edge + kind + "_Hardness", 0), edge + kind + "_Hardness")
            # These maxima are decoded native preshader operations.
            normalized = g.math("DIVIDE", g.math("ABSOLUTE", distance), g.math("MAXIMUM", radius, 1e-5))
            inside = g.math("SUBTRACT", 1, normalized)
            inside = g.clamp(g.math("DIVIDE", inside, g.math("MAXIMUM", g.math("SUBTRACT", 1, hardness), 1e-5)))
            clear = g.math("SUBTRACT", 1, inside)
            product = g.math("MULTIPLY", product, clear)
        return product
    union = g.math("SUBTRACT", 1, clears("Shadow"))
    mask = g.math("MULTIPLY", union, clears("ShadowBorder"))
    mask = g.math("MULTIPLY", mask, mask, "Native squared eye shadow mask")
    tint = g.vlerp((1, 1, 1), g.rgb(colors.get("ShadowTint", (0, 0, 0, 1)), "ShadowTint"), mask)
    scalar = g.math("SUBTRACT", 1, g.math("MULTIPLY", mask,
                    g.value(scalars.get("ShadowMultiply", 0), "ShadowMultiply")))
    transmission = g.vector("SCALE", tint, scalar, "Native eye occlusion transmission")
    return transmission, mask


def tearline_response(g, scalars, *, depth_gap_cm=None):
    """Native static data and view opacity, with an explicit scene-depth input."""
    def scalar(name):
        return g.value(scalars.get(name, TEAR_DEFAULTS[name]), name)
    geometry = g.node("ShaderNodeNewGeometry", "Tear-line view")
    dot = g.math("MAXIMUM", g.vector("DOT_PRODUCT", geometry.outputs["Normal"], geometry.outputs["Incoming"]), 0)
    facing = g.math("MAXIMUM", g.math("ABSOLUTE", g.math("SUBTRACT", 1, dot)), .0001)
    fresnel = g.math("ADD", g.math("MULTIPLY", g.math("POWER", facing, scalar("Exp")), .96), .04,
                     "Native tear-line opacity fresnel")
    depth_fade = scalar("depthFade")
    if depth_gap_cm is None:
        # Opaque-depth sampling is not available in Blender material nodes.
        # Keep its value explicit instead of masquerading it as refraction.
        depth_gap_cm = g.value(scalars.get("depthFade", .1), "Scene depth gap UE cm (unmeasured)")
    depth = g.clamp(g.math("DIVIDE", depth_gap_cm, g.math("MAXIMUM", depth_fade, 1e-5)))
    contrast = scalar("cc")
    depth = g.clamp(g.math("SUBTRACT", g.math("MULTIPLY", depth,
                    g.math("ADD", 1, g.math("MULTIPLY", contrast, 2))), contrast))
    alpha = g.clamp(g.math("MULTIPLY", g.math("MULTIPLY", g.math("MULTIPLY", fresnel, depth), scalar("o")), scalar("Visibility")))
    return {"alpha": alpha, "color": scalar("d"), "metallic": scalar("m"),
            "specular": scalar("Param"), "roughness": scalar("roughness")}


def authored_eye_aux_material(mi_path, role, uv_name="UV0", *, allow_missing=False):
    source_present = bool(mi_path and os.path.isfile(mi_path))
    if not source_present and not allow_missing:
        return None
    if role not in {"eyeshell", "eyeedge"}:
        return None
    family = "eye_occlusion" if role == "eyeshell" else "tear_line"
    defaults = OCCLUSION_DEFAULTS if role == "eyeshell" else TEAR_DEFAULTS
    record = read_character_parameters(mi_path, defaults) if source_present else {"scalars":dict(defaults), "colors":{}, "sources":[]}
    key = hashlib.sha256(json.dumps({"record": record, "family": family, "uv": uv_name,
                                    "schema": "fallback-eye-aux-v1" if allow_missing else "native-eye-aux-v1"}, sort_keys=True).encode()).hexdigest()
    existing = _AUX_MATERIALS.get(key)
    if existing is not None:
        try:
            _ = existing.name
            return existing
        except ReferenceError:
            _AUX_MATERIALS.pop(key, None)
    mat = bpy.data.materials.new(os.path.splitext(os.path.basename(mi_path))[0] + "_" + family)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    g = ResponseGraph(nodes, links, "EyeAux_")
    out = g.node("ShaderNodeOutputMaterial", "Eye auxiliary surface")
    unsupported = []
    if family == "eye_occlusion":
        uv = g.node("ShaderNodeUVMap", "UV0")
        uv.uv_map = uv_name
        transmission, mask = occlusion_transmittance(g, uv.outputs[0], record["scalars"], record["colors"])
        transparent = g.node("ShaderNodeBsdfTransparent", "Native unlit eye shadow transmission")
        g.put(transparent.inputs["Color"], transmission)
        # Native scene-color overlay does not add reflected highlights or affect
        # skin illumination; keep the color multiply on primary camera paths.
        clear = g.node("ShaderNodeBsdfTransparent", "Other rays remain clear")
        clear.inputs["Color"].default_value = (1, 1, 1, 1)
        path = g.node("ShaderNodeLightPath", "Eye occlusion camera paths")
        choose = g.node("ShaderNodeMixShader", "Camera-only native occlusion")
        g.put(choose.inputs[0], path.outputs["Is Camera Ray"])
        g.put(choose.inputs[1], clear.outputs[0])
        g.put(choose.inputs[2], transparent.outputs[0])
        g.put(out.inputs["Surface"], choose.outputs[0])
        if float(record["scalars"].get("Blur Size", 0)) != 0:
            unsupported.append("native scene-color blur")
        mat["arc_eye_aux_transmission_node"] = transmission.node.name
        mat["arc_eye_aux_shadow_mask_node"] = mask.node.name
        mat["arc_eye_aux_native_identity"] = bool(all(float(record["scalars"].get(e + "Shadow_Radius", 0)) == 0
                                                      for e in ("Outer", "Inner", "Bottom", "Top")))
    else:
        response = tearline_response(g, record["scalars"])
        principled = g.node("ShaderNodeBsdfPrincipled", "Native metallic tear-line surface")
        for name, value in (("Base Color", response["color"]), ("Metallic", response["metallic"]),
                            ("Specular IOR Level", response["specular"]), ("Roughness", response["roughness"]),
                            ("Alpha", response["alpha"])):
            g.put(principled.inputs[name], value)
        principled.inputs["IOR"].default_value = 1.5
        principled.inputs["Transmission Weight"].default_value = 0
        principled.inputs["Coat Weight"].default_value = 0
        g.put(out.inputs["Surface"], principled.outputs[0])
        unsupported.append("native opaque scene-depth sampling; explicit depth-gap value defaults to full depth fade")
        mat["arc_eye_aux_alpha_node"] = response["alpha"].node.name
    mat.surface_render_method = "DITHERED"
    mat.use_backface_culling = True
    mat["arc_head_role"] = role
    if mi_path:
        mat["arc_mi_path"] = os.path.abspath(mi_path)
    mat["arc_eye_aux_family"] = family
    mat["arc_eye_aux_setup"] = "native-eye-aux-v1"
    mat["arc_eye_aux_key"] = key
    mat["arc_eye_aux_unsupported"] = json.dumps(unsupported)
    mat["arc_eye_aux_parameter_sources"] = json.dumps(record["sources"])
    if allow_missing:
        from .character_fallback import finish
        finish(mat, role, ["Native eye auxiliary MI unavailable; verified equations evaluated at explicit master defaults; missing scene-color blur/depth transport remains an approximation"])
        mat["arc_eye_aux_setup"] = "fallback-eye-aux-v2"
    _AUX_MATERIALS[key] = mat
    return mat
