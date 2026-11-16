"""Artist controls layered over authored fur offsets and surface values."""

LENGTH = 'Length Scale'
TAG = 'arc_fur_control'
DEFAULT = 'arc_fur_control_default'
MODIFIERS = ('ARC Native Fur Shell Offset', 'ARC Weapon Fur Shell Offset')


def ensure_length_control(modifier):
    group = modifier.node_group
    if modifier.type != 'NODES' or group is None:
        raise ValueError('A fur Geometry Nodes modifier is required')
    socket = next((item for item in group.interface.items_tree if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == LENGTH), None)
    created = socket is None
    if socket is None:
        positions = [node for node in group.nodes if node.bl_idname == 'GeometryNodeSetPosition']
        if len(positions) != 1 or len(positions[0].inputs['Offset'].links) != 1:
            raise ValueError('Expected one authored fur offset binding')
        position = positions[0]
        source = position.inputs['Offset'].links[0].from_socket
        socket = group.interface.new_socket(name=LENGTH, in_out='INPUT', socket_type='NodeSocketFloat')
        socket.default_value = 1.
        socket.min_value = 0.
        socket.max_value = 5.
        inp = next(node for node in group.nodes if node.bl_idname == 'NodeGroupInput')
        scale = group.nodes.new('ShaderNodeVectorMath')
        scale.operation = 'SCALE'
        scale.label = LENGTH
        group.links.new(source, scale.inputs[0])
        group.links.new(inp.outputs[LENGTH], scale.inputs['Scale'])
        group.links.new(scale.outputs['Vector'], position.inputs['Offset'])
    if created or socket.identifier not in modifier:
        modifier[socket.identifier] = 1.
    return socket.identifier


def material_overrides(material):
    if not material.use_nodes or material.node_tree is None:
        return {}
    result = {}
    for node in material.node_tree.nodes:
        key = node.get(TAG)
        if not key or not node.outputs:
            continue
        value = node.outputs[0].default_value
        default = node.get(DEFAULT)
        if default is None:
            continue
        if isinstance(default, (int, float)):
            if abs(float(value) - default) > 1e-6:
                result[key] = float(value)
        elif any(abs(left - right) > 1e-6 for left, right in zip(value, default)):
            result[key] = tuple(value)
    return result


def color_control(graph, label, authored, saved):
    node = graph.nodes.new('ShaderNodeRGB')
    node.label = label
    node[TAG] = label
    node[DEFAULT] = tuple(authored)
    node.outputs[0].default_value = saved.get(label, authored)
    return node.outputs[0]


def value_control(graph, label, authored, saved):
    node = graph.nodes.new('ShaderNodeValue')
    node.label = label
    node[TAG] = label
    node[DEFAULT] = float(authored)
    node.outputs[0].default_value = saved.get(label, authored)
    return node.outputs[0]


def fur_objects(context):
    candidates = list(getattr(context, 'selected_objects', ()))
    active = getattr(context, 'active_object', None)
    if active is not None:
        candidates.extend((active, *active.children_recursive))
    found = set()
    for obj in candidates:
        if obj.type != 'MESH' or obj.as_pointer() in found:
            continue
        found.add(obj.as_pointer())
        if any(mod.name in MODIFIERS for mod in obj.modifiers) or any(slot.material and (slot.material.get('arc_fur_setup') or slot.material.get('arc_weapon_fur')) for slot in obj.material_slots):
            yield obj


def draw_controls(layout, context):
    seen_materials = set()
    for obj in fur_objects(context):
        box = layout.box()
        box.label(text=obj.name, icon='OUTLINER_OB_MESH')
        for modifier in obj.modifiers:
            if modifier.name not in MODIFIERS or modifier.type != 'NODES' or modifier.node_group is None:
                continue
            socket = next((item for item in modifier.node_group.interface.items_tree if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == LENGTH), None)
            if socket is not None:
                box.prop(modifier, '["' + socket.identifier + '"]', text=LENGTH)
            row = box.row(align=True)
            row.prop(modifier, 'show_viewport', text='Viewport')
            row.prop(modifier, 'show_render', text='Render')
        for slot in obj.material_slots:
            material = slot.material
            if material is None or material.as_pointer() in seen_materials or material.node_tree is None:
                continue
            controls = [node for node in material.node_tree.nodes if node.get(TAG)]
            if not controls:
                continue
            seen_materials.add(material.as_pointer())
            surface = box.box()
            surface.label(text=material.name, icon='MATERIAL')
            for node in controls:
                surface.prop(node.outputs[0], 'default_value', text=node[TAG])
