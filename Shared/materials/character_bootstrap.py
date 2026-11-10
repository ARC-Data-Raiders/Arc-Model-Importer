"""Standalone native character tree allocation; no binary shader dependency."""
from __future__ import annotations

SSS_INPUTS = ("Subsurface Weight", "Subsurface Radius", "Subsurface Scale",
              "Subsurface IOR", "Subsurface Anisotropy")


def sss_values(tree):
    """Preserve explicit material response values, never nodes or links."""
    if tree is None:
        return {}
    node = next((node for node in tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
    if node is None:
        return {}
    result = {}
    for name in SSS_INPUTS:
        socket = node.inputs.get(name)
        if socket is not None:
            value = socket.default_value
            result[name] = tuple(value) if hasattr(value, "__len__") else value
    return result


def prepare_native_face(material, mi_path, previous_tree=None):
    """Build the established native equations on a fresh owned interface.

    This deliberately allocates no Face Shader template and performs no datablock
    copies. Existing per-material numeric SSS controls survive migration. Shared
    trees stay untouched; every rebuild completes on a fresh tree before the caller commits it.
    """
    import bpy
    from .character_face import parameters, build_face_tree
    record = parameters(mi_path)  # Validate/read before creating a datablock.
    preserved = sss_values(previous_tree)
    tree = bpy.data.node_groups.new("DR.Face." + material.name, "ShaderNodeTree")
    if preserved:
        # The native builder already transports these controls while clearing
        # nodes. A temporary original seed conveys values without cloning code.
        seed = tree.nodes.new("ShaderNodeBsdfPrincipled")
        seed.name = "DR.Source.SubsurfaceControls"
        for name, value in preserved.items():
            socket = seed.inputs.get(name)
            if socket is not None:
                socket.default_value = value
    try:
        build_face_tree(tree, record)
    except Exception:
        bpy.data.node_groups.remove(tree)
        raise
    tree["arc_face_shader"] = True
    tree["arc_face_shader_owner"] = material.name
    tree["dr_face_bootstrap"] = "native-interface-v2"
    tree["dr_face_bootstrap_source"] = "standalone Python interface; existing native static builder"
    return tree
