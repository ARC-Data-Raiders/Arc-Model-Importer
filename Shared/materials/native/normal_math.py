"""Original immutable normal computations shared by native material instances."""
from pathlib import Path
import hashlib
import json

NAMES = {"decode": "DR.Math.DecodeNormal.v1", "rnm": "DR.Math.RNM.v1"}
_CACHE = {}


def signature(tree):
    nodes = list(tree.nodes)
    indices = {node.as_pointer(): index for index, node in enumerate(nodes)}
    def value(socket):
        result = getattr(socket, "default_value", None)
        return list(result) if hasattr(result, "__len__") and not isinstance(result, str) else result
    payload = {"nodes": [(node.bl_idname, getattr(node, "operation", None), [value(socket) for socket in node.inputs]) for node in nodes],
               "links": [(indices[link.from_node.as_pointer()], link.from_socket.identifier, indices[link.to_node.as_pointer()], link.to_socket.identifier) for link in tree.links],
               "interface": [(socket.name, socket.in_out, socket.socket_type) for socket in tree.interface.items_tree if socket.item_type == "SOCKET"]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def build(kind, bpy_module):
    tree = bpy_module.data.node_groups.new(NAMES[kind], "ShaderNodeTree")
    tree["dr.math.key"] = kind
    tree["dr.math.schema"] = 1
    ports = (("Red", "NodeSocketFloat", .5), ("Green", "NodeSocketFloat", .5), ("Strength", "NodeSocketFloat", 1.)) if kind == "decode" else (("Base", "NodeSocketVector", (0., 0., 1.)), ("Detail", "NodeSocketVector", (0., 0., 1.)))
    for name, socket_type, default in ports:
        socket = tree.interface.new_socket(name=name, in_out="INPUT", socket_type=socket_type)
        socket.default_value = default
        if name == "Strength":
            socket.min_value, socket.max_value = -1000., 1000.
    tree.interface.new_socket(name="Normal", in_out="OUTPUT", socket_type="NodeSocketVector")
    source = tree.nodes.new("NodeGroupInput")
    output = tree.nodes.new("NodeGroupOutput")
    def bind(socket, source):
        if hasattr(source, "is_output"):
            tree.links.new(source, socket)
        else:
            socket.default_value = source
    def scalar(operation, first, second=0):
        node = tree.nodes.new("ShaderNodeMath")
        node.operation = operation
        bind(node.inputs[0], first)
        bind(node.inputs[1], second)
        return node.outputs[0]
    def combine(first, second, third):
        node = tree.nodes.new("ShaderNodeCombineXYZ")
        for socket, source in zip(node.inputs, (first, second, third)):
            bind(socket, source)
        return node.outputs[0]
    def split(source):
        node = tree.nodes.new("ShaderNodeSeparateXYZ")
        bind(node.inputs[0], source)
        return tuple(node.outputs)
    def vector(operation, first, second=None, scale=None):
        node = tree.nodes.new("ShaderNodeVectorMath")
        node.operation = operation
        bind(node.inputs[0], first)
        if second is not None:
            bind(node.inputs[1], second)
        if scale is not None:
            bind(node.inputs["Scale"], scale)
        return node.outputs["Value" if operation == "DOT_PRODUCT" else "Vector"]
    if kind == "decode":
        strength = source.outputs["Strength"]
        horizontal = scalar("MULTIPLY", scalar("SUBTRACT", scalar("MULTIPLY", source.outputs["Red"], 2), 1), strength)
        vertical = scalar("MULTIPLY", scalar("SUBTRACT", scalar("MULTIPLY", source.outputs["Green"], 2), 1), strength)
        depth = scalar("SQRT", scalar("MAXIMUM", scalar("SUBTRACT", 1, scalar("ADD", scalar("MULTIPLY", horizontal, horizontal), scalar("MULTIPLY", vertical, vertical))), 0))
        result = vector("NORMALIZE", combine(horizontal, vertical, depth))
    elif kind == "rnm":
        horizontal, vertical, depth = split(source.outputs["Base"])
        detail_x, detail_y, detail_z = split(source.outputs["Detail"])
        translated = combine(horizontal, vertical, scalar("ADD", depth, 1))
        reflected = combine(scalar("MULTIPLY", detail_x, -1), scalar("MULTIPLY", detail_y, -1), detail_z)
        amount = scalar("DIVIDE", vector("DOT_PRODUCT", translated, reflected), scalar("ADD", depth, 1))
        result = vector("NORMALIZE", vector("SUBTRACT", vector("SCALE", translated, scale=amount), reflected))
    else:
        raise ValueError("Unknown immutable normal operation: " + str(kind))
    bind(output.inputs["Normal"], result)
    for index, node in enumerate(tree.nodes):
        node.location = (index * 180, 0)
    tree["dr.math.signature"] = signature(tree)
    return tree


def ensure(kind, bpy_module):
    cached = _CACHE.get(kind)
    if cached is not None:
        try:
            if cached.name in bpy_module.data.node_groups:
                return cached
        except ReferenceError:
            pass
        _CACHE.pop(kind, None)
    candidates = [tree for tree in bpy_module.data.node_groups if tree.get("dr.math.key") == kind and tree.get("dr.math.schema") == 1]
    template = Path(__file__).resolve().parents[3] / "ArcTexturer.blend"
    if not candidates and template.is_file():
        with bpy_module.data.libraries.load(str(template), link=False) as (source, destination):
            destination.node_groups = [name for name in NAMES.values() if name in source.node_groups and not bpy_module.data.node_groups.get(name)]
        candidates = [tree for tree in destination.node_groups if tree and tree.get("dr.math.key") == kind]
    tree = next((tree for tree in candidates if tree.get("dr.math.signature") == signature(tree)), None)
    if tree is None:
        tree = build(kind, bpy_module)
    _CACHE[kind] = tree
    return tree
