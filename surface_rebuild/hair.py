"""Hair-card colour/roughness/coverage from the current shader's disassembly."""
from .masked import lerp,vlerp,saturate_color,vertex


def build_hair(g,r):
    s,c,t=r['scalars'],r['colors'],r['textures'];uv=g.uv(0)
    attributes=g.image(t.get('AttributeMap',''),uv,srgb=False,label='Hair attributes · R variation, B root, A palette/roughness')
    coverage=g.image(t.get('Coverage',''),uv,srgb=False,label='Hair coverage · R opacity')
    tangent=g.image(t.get('Tangent',''),uv,srgb=False,label='Hair tangent direction · RG',required=False)
    if not attributes or not coverage:g.finish(c.get('ColorA',(.04,.02,.01,1)));return
    with g.block('Strand palette and root darkening') as b:
        factor=b.math('MAXIMUM',attributes['a'],b.sub(1,b.parameter('ColorVariationInfluence',s.get('ColorVariationInfluence',1))))
        color=vlerp(b,b.parameter('ColorA',c.get('ColorA',(.04,.02,.01,1)),'Color'),
                    b.parameter('ColorB',c.get('ColorB',(.02,.01,.005,1)),'Color'),factor)
        secondary=vlerp(b,b.parameter('SecondaryColor',c.get('SecondaryColor',(.8,.6,.4,1)),'Color'),
                        b.parameter('SecondaryColorB',c.get('SecondaryColorB',(.8,.6,.4,1)),'Color'),factor)
        enable=s.get('UseSecondaryColor',float(r['switches'].get('UseSecondaryColor',False)))
        color=vlerp(b,color,secondary,b.mul(vertex(b)['g'],b.parameter('UseSecondaryColor',enable)))
        root=b.clamp(b.math('POWER',b.math('MAXIMUM',attributes['b'],0),b.parameter('Root position',s.get('Root position',4))))
        color=vlerp(b,b.cmul(color,b.parameter('Root Multiply',c.get('Root Multiply',(.38,.32,.27,1)),'Color')),color,root)
        if r['master'].lower().endswith('m_hair_metahuman_colormap'):
            # This uncommon branch is left explicit rather than pretending its UVs are ordinary card UVs.
            b.report['warnings'].append('Hair global colour-map strand-offset branch is not reconstructed')
        color=b.export(saturate_color(b,color),'Hair colour','Color')
    with g.block('Strand roughness and coverage') as b:
        rough=lerp(b,b.parameter('Roughness min',s.get('Roughness min',.3)),b.parameter('Roughness max',s.get('Roughness max',.6)),attributes['a'])
        opacity=b.math('POWER',b.math('MAXIMUM',coverage['r'],0),b.parameter('DitherPower',s.get('DitherPower',1)))
        opacity=b.clamp(b.div(opacity,b.math('MAXIMUM',b.sub(1,b.parameter('Opacity amount',s.get('Opacity amount',.2))),1e-5)))
        opacity=b.mul(opacity,b.parameter('Visibility',s.get('Visibility',1)))
        rough=b.export(b.clamp(rough),'Hair roughness');opacity=b.export(b.clamp(opacity),'Hair alpha')
    response={'Anisotropic':g.parameter('Blender strand anisotropy',.6)}
    if tangent:response['Tangent']=g.normal_to_world(g.decode_normal(tangent))
    g.report['implemented_stages']=['explicit hair-card master profile','attribute A palette and roughness','attribute B root falloff',
        'secondary palette through vertex green','coverage R and authored opacity power/density','tangent direction for Blender anisotropy']
    g.report['warnings'].append('Blender anisotropic hair cards approximate Unreal hair scattering; temporal dithering, pixel-depth offset, scraggle and runtime weather are omitted')
    g.finish(color,rough,specular=g.clamp(g.parameter('Spec0',s.get('Spec0',.45))),alpha=opacity,response=response)
