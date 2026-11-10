"""M_SplineThicken: the game widens 1 mm cable ribbons in the vertex shader.

Each ribbon is a pair of vertex rows about 1 mm apart. floor(UV1.v) selects the
cable type (row 0..7 -> Thickness_A..H, in cm, the full cable width). Blender
has no vertex-shader equivalent, so a per-object Geometry Nodes modifier merges
each pair into a centreline and sweeps a round tube of that width along it.
UV0.u and the UV1.v row fraction are rebuilt around the tube; the along-length
coordinates are kept. Faces of other materials pass through untouched.
"""
import bpy

GROUP = 'ARC SplineThicken Tubes v4'
PREFIX = 'ARC SplineThicken '
LETTERS = 'ABCDEFGH'
MERGE_DISTANCE = 0.0015  # pair gap measured 0.92-1.04 mm in local metres


def eligible(record):
    master = str(record.get('master', '')).rsplit('/', 1)[-1].rsplit('.', 1)[-1].casefold()
    if master != 'm_splinethicken':
        return False
    return bool((record.get('switches') or {}).get('UseSplineThicken', True))


def _group():
    g = bpy.data.node_groups.get(GROUP)
    if g is not None:
        return g
    g = bpy.data.node_groups.new(GROUP, 'GeometryNodeTree')
    it = g.interface
    it.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    it.new_socket(name='Slot', in_out='INPUT', socket_type='NodeSocketInt')
    it.new_socket(name='Material', in_out='INPUT', socket_type='NodeSocketMaterial')
    for L in LETTERS:
        s = it.new_socket(name='Thickness_' + L, in_out='INPUT', socket_type='NodeSocketFloat')
        s.default_value = 0.01
    it.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    N, Ln = g.nodes, g.links
    gi, go = N.new('NodeGroupInput'), N.new('NodeGroupOutput')

    def node(t, **props):
        n = N.new(t)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def math(op, a, b=None):
        n = node('ShaderNodeMath', operation=op)
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                Ln.new(v, n.inputs[i])
        return n.outputs[0]

    def named(name, dtype):
        n = node('GeometryNodeInputNamedAttribute', data_type=dtype)
        n.inputs['Name'].default_value = name
        return n.outputs['Attribute']

    def store(geo, name, dtype, domain, value):
        n = node('GeometryNodeStoreNamedAttribute', data_type=dtype, domain=domain)
        Ln.new(geo, n.inputs['Geometry'])
        n.inputs['Name'].default_value = name
        Ln.new(value, n.inputs['Value'])
        return n.outputs['Geometry']

    # Select by slot index: GN sees mesh-data materials, while the importer may
    # assign object-linked slot materials, so a Material Selection can miss.
    sel = node('FunctionNodeCompare', data_type='INT', operation='EQUAL')
    Ln.new(node('GeometryNodeInputMaterialIndex').outputs[0], sel.inputs[2])
    Ln.new(gi.outputs['Slot'], sel.inputs[3])
    sep = node('GeometryNodeSeparateGeometry', domain='FACE')
    Ln.new(gi.outputs['Geometry'], sep.inputs['Geometry'])
    Ln.new(sel.outputs['Result'], sep.inputs['Selection'])
    cable = sep.outputs['Selection']

    # Per-point cable facts, read before the pair is merged.
    uv1 = node('ShaderNodeSeparateXYZ'); Ln.new(named('UV1', 'FLOAT_VECTOR'), uv1.inputs[0])
    uv0 = node('ShaderNodeSeparateXYZ'); Ln.new(named('UV0', 'FLOAT_VECTOR'), uv0.inputs[0])
    row = math('FLOOR', uv1.outputs['Y'])
    row = math('MAXIMUM', math('MINIMUM', row, 7.0), 0.0)
    sw = node('GeometryNodeIndexSwitch', data_type='FLOAT')
    while len(sw.index_switch_items) < 8:
        sw.index_switch_items.new()
    tofl = node('FunctionNodeFloatToInt', rounding_mode='ROUND'); Ln.new(row, tofl.inputs[0])
    Ln.new(tofl.outputs[0], sw.inputs['Index'])
    for i, L in enumerate(LETTERS):
        Ln.new(gi.outputs['Thickness_' + L], sw.inputs[i + 1])
    cable = store(cable, 'dr_st_row', 'FLOAT', 'POINT', row)
    cable = store(cable, 'dr_st_w', 'FLOAT', 'POINT', sw.outputs['Output'])
    cable = store(cable, 'dr_st_u1', 'FLOAT', 'POINT', uv1.outputs['X'])
    cable = store(cable, 'dr_st_v0', 'FLOAT', 'POINT', uv0.outputs['Y'])

    merge = node('GeometryNodeMergeByDistance'); Ln.new(cable, merge.inputs['Geometry'])
    merge.inputs['Distance'].default_value = MERGE_DISTANCE
    m2c = node('GeometryNodeMeshToCurve'); Ln.new(merge.outputs['Geometry'], m2c.inputs['Mesh'])
    rad = node('GeometryNodeSetCurveRadius'); Ln.new(m2c.outputs['Curve'], rad.inputs['Curve'])
    Ln.new(math('MULTIPLY', named('dr_st_w', 'FLOAT'), 0.5), rad.inputs['Radius'])

    prof = node('GeometryNodeCurvePrimitiveCircle', mode='RADIUS')
    prof.inputs['Resolution'].default_value = 8
    prof.inputs['Radius'].default_value = 1.0
    sp = node('GeometryNodeSplineParameter')
    prof_geo = store(prof.outputs['Curve'], 'dr_st_pf', 'FLOAT', 'POINT', sp.outputs['Factor'])

    c2m = node('GeometryNodeCurveToMesh')
    Ln.new(rad.outputs['Curve'], c2m.inputs['Curve'])
    Ln.new(prof_geo, c2m.inputs['Profile Curve'])
    if 'Fill Caps' in c2m.inputs:
        c2m.inputs['Fill Caps'].default_value = True
    else:
        c2m.fill_caps = True
    if 'Scale' in c2m.inputs:  # Blender 4.2+: radius is not applied unless wired to Scale
        Ln.new(node('GeometryNodeInputRadius').outputs[0], c2m.inputs['Scale'])
    tube = c2m.outputs['Mesh']

    pf = named('dr_st_pf', 'FLOAT')
    v1 = math('ADD', named('dr_st_row', 'FLOAT'), math('ADD', math('MULTIPLY', pf, 0.9), 0.05))
    c1 = node('ShaderNodeCombineXYZ'); Ln.new(named('dr_st_u1', 'FLOAT'), c1.inputs[0]); Ln.new(v1, c1.inputs[1])
    c0 = node('ShaderNodeCombineXYZ'); Ln.new(pf, c0.inputs[0]); Ln.new(named('dr_st_v0', 'FLOAT'), c0.inputs[1])
    tube = store(tube, 'UV1', 'FLOAT2', 'CORNER', c1.outputs[0])
    tube = store(tube, 'UV0', 'FLOAT2', 'CORNER', c0.outputs[0])
    smooth = node('GeometryNodeSetShadeSmooth'); Ln.new(tube, smooth.inputs['Geometry'])
    # Curve to Mesh output has no material list, so Set Material Index would point at nothing and
    # Join Geometry would prepend an empty material, shifting every slot by one (tubes rendered with
    # slot 0). Setting the slot's mesh-data material lets Join map the tubes onto that same index.
    setm = node('GeometryNodeSetMaterial'); Ln.new(smooth.outputs['Geometry'], setm.inputs['Geometry'])
    Ln.new(gi.outputs['Material'], setm.inputs['Material'])

    # Join builds its material list in input order and drops duplicates. The original mesh must come
    # first so its slot order is kept; a multi-input socket lists the most recently added link first.
    join = node('GeometryNodeJoinGeometry')
    Ln.new(setm.outputs['Geometry'], join.inputs['Geometry'])
    Ln.new(sep.outputs['Inverted'], join.inputs['Geometry'])
    Ln.new(join.outputs['Geometry'], go.inputs['Geometry'])
    return g


def apply(obj, mat, record, report, slot=None):
    """Add or refresh the tube modifier for this material on obj. Returns the modifier."""
    if obj is None or obj.type != 'MESH' or not eligible(record):
        return None
    if 'UV1' not in obj.data.uv_layers:
        report.setdefault('warnings', []).append('SplineThicken: mesh has no UV1, cable type unknown; tubes skipped')
        return None
    slots = [slot] if slot is not None else [i for i, s in enumerate(obj.material_slots) if s.material == mat]
    if not slots:
        slots = [i for i, s in enumerate(obj.material_slots)
                 if s.material and s.material.name.rsplit('.', 1)[0] == mat.name.rsplit('.', 1)[0]]
    if not slots:
        return None  # material not on a slot yet; caller assigns later
    g = _group()
    name = (PREFIX + 'slot %d' % slots[0])
    mod = obj.modifiers.get(name)
    if mod is None:
        mod = obj.modifiers.new(name, 'NODES')
    mod.node_group = g
    scalars = record.get('scalars') or {}
    widths = {}
    for item in g.interface.items_tree:
        if getattr(item, 'in_out', None) != 'INPUT':
            continue
        if item.name == 'Slot':
            mod[item.identifier] = slots[0]
        elif item.name == 'Material':
            data_mats = obj.data.materials
            mod[item.identifier] = (data_mats[slots[0]] if slots[0] < len(data_mats) and data_mats[slots[0]]
                                    else obj.material_slots[slots[0]].material)
        elif item.name.startswith('Thickness_'):
            cm = float(scalars.get(item.name, 1.0))  # master default 1 cm
            mod[item.identifier] = cm * 0.01
            widths[item.name[-1]] = cm
    report.setdefault('warnings', []).append(
        'SplineThicken: vertex-shader thickening emulated as round GN tubes (width = Thickness_* cm, row = floor(UV1.v))')
    report['spline_thicken_cm'] = widths
    return mod
