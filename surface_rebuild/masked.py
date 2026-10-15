"""Small helpers shared by the explicitly traced masked material families."""

def lerp(g, a, b, factor):
    return g.add(a, g.mul(g.sub(b, a), factor))


def vlerp(g, a, b, factor):
    return g.vector('ADD', a, g.vector('SCALE', g.vector('SUBTRACT', b, a), scale=factor))


def overlay(g, base, detail):
    low = g.mul(g.mul(base, detail), 2)
    high = g.sub(1, g.mul(g.mul(g.sub(1,base),g.sub(1,detail)),2))
    return lerp(g, low, high, g.math('GREATER_THAN',base,.5))


def overlay_color(g, base, detail):
    values = [overlay(g, value, detail) for value in g.split(base)]
    return g.combine(*values)


def saturate_color(g, color):
    return g.vector('MINIMUM', g.vector('MAXIMUM', color, (0,0,0)), (1,1,1))


def range_mask(g, value, low, high, twice=False):
    # The compiled preshader clamps the range width before taking its reciprocal.
    width = g.math('MAXIMUM',g.clamp(g.sub(high, low)),1e-8)
    mask = g.div(g.sub(value,low), width)
    return g.clamp(g.mul(mask,2) if twice else mask)


def vertex(g):
    attrs = g.obj.data.color_attributes
    if not attrs: return {'r':1,'g':0,'b':0,'a':1,'color':(1,0,0,1)}
    node=g.node('ShaderNodeVertexColor','Authored vertex masks');node.layer_name=attrs[0].name
    channels=g.split(node.outputs['Color'])
    return dict(zip(['r','g','b'],channels),a=node.outputs['Alpha'],color=node.outputs['Color'])
