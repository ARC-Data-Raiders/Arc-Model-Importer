"""M_EnemyPreset_OX+Tiling+Detail: current Monolith and Catnip pixel shaders."""
from .masked import lerp, vlerp, overlay, range_mask, saturate_color

METAL_MASK = 'PioneerGame/Content/Pioneer/MaterialLibrary/Textures/Enemies/T_MetalMasks_01_X'


def build_enemy_tile(g, r):
    s,c,t,sw=r['scalars'],r['colors'],r['textures'],r['switches']
    def S(name,default=0):return g.parameter(name,s.get(name,default))
    ox=g.image(t.get('OX',''),g.uv(0),srgb=False,label='Baked OX · AO / curvature / leaks / panels')
    panel=g.image(t.get('NMR',''),g.transform_uv(0,S('NMRTile',4)),srgb=False,label='Panels · RG normal, B metal, A response')
    tile=S('DetailTile',4)
    control=g.image(t.get('Metal Masks',METAL_MASK),g.transform_uv(0,g.mul(tile,.5)),srgb=False,label='Fixed metal breakup · RGB controls')
    detail=g.image(t.get('Detail',''),g.transform_uv(0,g.mul(tile,2)),srgb=False,label='Detail · B colour, A roughness')
    if not all([ox,panel,control,detail]):
        g.report['warnings'].append('Required tileable enemy maps missing; neutral fallback used')
        g.finish(c.get('Color',(.2,.2,.2,1)));return
    with g.block('Curvature, wear and leak masks') as b:
        concave=range_mask(b,b.sub(1,ox['g']),b.parameter('Concave edge sharpness',s.get('ConcaveEdgeSharpness',.6)),1,True)
        convex=range_mask(b,ox['g'],b.parameter('Convex edge sharpness',s.get('ConvexEdgeSharpness',.6)),1,True)
        convex=b.mul(b.mul(convex,ox['r']),control['r'])
        wear=b.sub(1,b.mul(ox['r'],b.sub(1,concave)))
        wear_break=b.clamp(overlay(b,wear,control['g']))
        leak=b.clamp(b.mul(overlay(b,overlay(b,ox['b'],control['g']),control['b']),.8))
        concave=b.export(concave,'Concave');convex=b.export(convex,'Convex')
        wear=b.export(wear,'Wear');wear_break=b.export(wear_break,'Wear breakup');leak=b.export(leak,'Leaks')
    with g.block('Masked colour and surface response') as b:
        # The compiled material reads Color.R as a scalar, even though JSON stores RGBA.
        base=b.mul(b.split(b.parameter('Color · game uses red channel',c.get('Color',(.2,.2,.2,1)),'Color'))[0],detail['b'])
        panel_response=b.clamp(b.add(b.mul(ox['a'],b.sub(panel['a'],.5)),.5))
        color=overlay(b,b.mul(base,2),b.sub(1,panel_response))
        color=lerp(b,color,control['b'],b.mul(b.mul(control['g'],ox['r']),.125))
        color=b.clamp(b.mul(color,ox['r']))
        color=b.mul(color,b.sub(1,concave))
        color=lerp(b,color,1,convex)
        worn=b.vector('SCALE',(.286458,.272205,.202908),scale=control['g'])
        color=vlerp(b,color,worn,wear)
        color=vlerp(b,color,b.parameter('LeaksColor',c.get('LeaksColor',(.1,.1,.1,1)),'Color'),leak)
        metal=b.mul(b.mul(panel['b'],b.sub(1,wear)),b.sub(1,leak))
        rough=lerp(b,b.mul(detail['a'],.75),control['b'],b.mul(b.mul(control['g'],ox['r']),.25))
        rough=b.mul(rough,b.sub(1,convex))
        rough=b.clamp(overlay(b,rough,panel_response))
        rough=lerp(b,lerp(b,rough,1,wear),.6,leak)
        color=b.export(saturate_color(b,color),'Base colour','Color')
        rough=b.export(b.clamp(rough),'Roughness');metal=b.export(b.clamp(metal),'Metallic')
    normal=vlerp(g,(0,0,1),g.decode_normal(panel),ox['a'])
    if sw.get('UseBakedNormal'):
        baked=g.image(t.get('N',''),g.uv(0),srgb=False,label='Baked enemy normal')
        if baked:
            base=g.vector('ADD',g.decode_normal(baked),(0,0,1))
            detail_n=g.vector('MULTIPLY',normal,(-1,-1,1))
            normal=g.vector('SUBTRACT',g.vector('SCALE',base,scale=g.vector('DOT_PRODUCT',base,detail_n)),
                            g.vector('SCALE',detail_n,scale=g.split(base)[2]))
    normal=vlerp(g,vlerp(g,normal,(0,0,1),wear_break),(0,0,1),leak)
    g.report['implemented_stages']=['compiled OX tileable enemy profile','OX AO, curvature, leak and panel masks',
        'authored panel and detail tiling','Color.R, concave and convex response','fixed master metal breakup',
        'LeaksColor through the leak mask','metallicity and roughness masks','panel and optional baked normals']
    g.report['warnings'].append('Runtime battle damage, animated glare and contact-shadow depth are not reconstructed')
    g.finish(color,rough,metal,g.vector('NORMALIZE',normal))
