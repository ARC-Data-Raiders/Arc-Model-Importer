"""Native ColorMap strand tangent stage.

PS1939/830FAA... lines97-126; actual SM6 PS2371/2374 replay corroborates this stage.
RandomAngles metadata authors TF_Nearest, SRGBfalse and defaultWrap.
"""
RANDOM_ANGLES = '/Engine/EngineMaterials/RandomAngles'


def is_native_hair_tangent_source(sources, unresolved_parent=''):
    """Exact current parents with independently proved tangent stages only.

    This does not classify the whole material as the ColorMap family: the
    Metahuman_01 master has separate color/specular/scattering behavior.
    """
    parents = ('/pioneer/characters/heads/shared/hair/materials/m_hair_metahuman_colormap',
               '/pioneer/characters/heads/shared/hair/materials/m_hair_metahuman_01')
    for reference in [*sources, unresolved_parent]:
        name = str(reference or '').replace('\\', '/').casefold()
        for suffix in ('.arc-material.json', '.json', '.native', '.0'):
            if name.endswith(suffix):
                name = name[:-len(suffix)]
        if any(name.endswith(parent) for parent in parents):
            return True
    return False


def native_point_png_uv(g, u, v, width, height):
    """D3D top-down nearest/repeat texel -> Blender bottom-up PNG texel center.

    Explicit centers avoid V=1 wrapping back onto the wrong row at native V=0.
    This correction is for synthesized native point coordinates, not imported UVs.
    """
    if width <= 0 or height <= 0:
        raise ValueError('Native point texture dimensions unresolved')
    col=g.math('FLOOR',g.math('MULTIPLY',g.math('FRACT',u),width))
    row=g.math('FLOOR',g.math('MULTIPLY',g.math('FRACT',v),height))
    bx=g.math('DIVIDE',g.math('ADD',col,.5),width)
    by=g.math('DIVIDE',g.math('SUBTRACT',height-.5,row),height)
    return g.combine(bx,by,0)


def wire_native_colormap_tangent(g, tangent_r, tangent_g, attribute_r,
                                 attribute_a, noise_image, params):
    if 'Scraggle' not in params or 'FlattenNormal' not in params:
        raise ValueError('Current native tangent parameters unresolved')
    phase=g.math('ADD',attribute_r,attribute_a,'Native RandomAngles strand phase')
    size=tuple(noise_image.size)
    noise_uv=native_point_png_uv(g,phase,0,size[0],size[1])
    noise=g.texture(noise_image,noise_uv,'Exact Engine RandomAngles data RB')
    noise.interpolation='Closest'
    noise.extension='REPEAT'
    random_r,_,random_b=g.split(noise.outputs['Color'])
    x=g.math('SUBTRACT',g.math('MULTIPLY',tangent_r,2),1)
    y=g.math('SUBTRACT',g.math('MULTIPLY',tangent_g,2),1)
    z=g.math('SQRT',g.math('MAXIMUM',g.math('SUBTRACT',1,g.math('ADD',
                      g.math('MULTIPLY',x,x),g.math('MULTIPLY',y,y))),0))
    decoded=g.combine(x,y,z)
    jitter=g.combine(g.math('SUBTRACT',random_r,.5),-.5,random_b)
    jitter=g.vector('SCALE',jitter,g.value(params['Scraggle'],'Authored Scraggle'))
    perturbed=g.vector('ADD',decoded,jitter,'Native signed strand plus scraggle')
    flatten=g.value(params['FlattenNormal'],'Authored FlattenNormal')
    signed=g.vector('NORMALIZE',g.vlerp(perturbed,(0,0,1),flatten),label='Native normalized strand tangent')
    encoded=g.vector('ADD',g.vector('SCALE',signed,.5),(.5,.5,.5),'Native strand tangent encoded RGB')
    return {'encoded':encoded,'signed':signed,'phase':phase,'noise':noise}
