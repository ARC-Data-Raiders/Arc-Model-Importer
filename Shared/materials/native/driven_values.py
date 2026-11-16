"""Move driven Value nodes out of a flow tree into one shared group per driver.

Stage grouping leaves trees with drivers alone, because a driver path names a node in
its own tree. Each driven Value node becomes a group node of a small shared tree that
holds the same driver, so the flow tree has no drivers and can fold into stage groups.
"""
import re

SIG_KEY = "arc_driver_sig"
_PATH = re.compile(r'^nodes\["((?:[^"\\]|\\.)*)"\]\.outputs\[0\]\.default_value$')


def _target_sig(t):
    ident = getattr(t, "id", None)
    return (t.id_type, getattr(ident, "name", ""), t.data_path, t.bone_target,
            t.transform_type, t.transform_space)


def _driver_sig(driver):
    vars_ = tuple((v.name, v.type, tuple(_target_sig(t) for t in v.targets)) for v in driver.variables)
    return repr((driver.type, driver.expression, bool(driver.use_self), vars_))


def _copy_driver(src, dst):
    dst.type = src.type
    dst.expression = src.expression
    dst.use_self = src.use_self
    while dst.variables:
        dst.variables.remove(dst.variables[0])
    for v in src.variables:
        nv = dst.variables.new()
        nv.name, nv.type = v.name, v.type
        for a, b in zip(v.targets, nv.targets):
            if nv.type == "SINGLE_PROP":
                b.id_type = a.id_type
            b.id = a.id
            b.data_path = a.data_path
            b.bone_target = a.bone_target
            b.transform_type = a.transform_type
            b.transform_space = a.transform_space


def _title(node):
    text = (node.label or node.name).split("·")[0].strip()
    text = re.sub(r"\.\d{3}$", "", text)
    return text or "Driven Value"


def _shared_group(bpy, node, driver, sig):
    for group in bpy.data.node_groups:
        if group.get(SIG_KEY) == sig and group.bl_idname == "ShaderNodeTree":
            return group
    base = "Arc " + _title(node)
    name, n = base, 2
    while name in bpy.data.node_groups:
        name, n = "%s %d" % (base, n), n + 1
    group = bpy.data.node_groups.new(name, "ShaderNodeTree")
    group.interface.new_socket("Value", in_out="OUTPUT", socket_type="NodeSocketFloat")
    out = group.nodes.new("NodeGroupOutput")
    out.location = (200, 0)
    value = group.nodes.new("ShaderNodeValue")
    value.name = _title(node)
    value.label = node.label or value.name
    value.outputs[0].default_value = node.outputs[0].default_value
    group.links.new(value.outputs[0], out.inputs[0])
    _copy_driver(driver, value.outputs[0].driver_add("default_value").driver)
    group[SIG_KEY] = sig
    try:
        from .. import graph_layout
        graph_layout.finalize_tree(group, force=True)
    except Exception as exc:
        print("[driven_values] layout skipped on %s: %s" % (group.name, exc))
    return group


def drop_reroutes(tree):
    """Link every reroute's source straight to its targets, then remove the reroutes.

    Reroutes only pass a value through, so the computed graph is unchanged. Returns the
    number removed."""
    reroutes = [n for n in tree.nodes if n.bl_idname == "NodeReroute"]
    ids = {n.as_pointer() for n in reroutes}

    def source(node):
        seen = set()
        while node.as_pointer() in ids and node.as_pointer() not in seen:
            seen.add(node.as_pointer())
            links = node.inputs[0].links
            if not links:
                return None
            sock = links[0].from_socket
            node = sock.node
        return None if node.as_pointer() in ids else sock

    pairs = []
    for r in reroutes:
        src = source(r)
        if src is None:
            continue
        for link in r.outputs[0].links:
            if link.to_node.as_pointer() not in ids:
                pairs.append((src, link.to_socket, link.is_muted))
    for r in reroutes:
        tree.nodes.remove(r)
    for src, dst, muted in pairs:
        link = tree.links.new(src, dst)
        if muted:
            link.is_muted = True
    return len(reroutes)


def hoist_driven_values(tree):
    """Replace every driven Value node in tree with a shared driven group node.

    Returns the number of nodes replaced. Drivers on anything other than a Value node
    output stay where they are, so such a tree still skips stage grouping.
    """
    import bpy
    ad = getattr(tree, "animation_data", None)
    if ad is None or ad.action is not None or not len(ad.drivers):
        return 0
    moved = 0
    for fc in list(ad.drivers):
        m = _PATH.match(fc.data_path)
        node = tree.nodes.get(m.group(1).replace('\\"', '"')) if m else None
        if node is None or node.bl_idname != "ShaderNodeValue":
            continue
        group = _shared_group(bpy, node, fc.driver, _driver_sig(fc.driver))
        gnode = tree.nodes.new("ShaderNodeGroup")
        gnode.node_tree = group
        gnode.location = node.location
        gnode.parent = node.parent
        gnode.label = node.label or node.name
        for key in node.keys():
            gnode[key] = node[key]
        targets = [l.to_socket for l in node.outputs[0].links]
        name = node.name
        ad.drivers.remove(fc)
        tree.nodes.remove(node)
        gnode.name = name
        for sock in targets:
            tree.links.new(gnode.outputs[0], sock)
        moved += 1
    if not len(ad.drivers) and ad.action is None:
        tree.animation_data_clear()
    return moved
