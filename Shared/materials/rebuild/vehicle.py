"""M_VehicleStack: current hoverbike layer shader, including authored red paint."""
from .masked import lerp, vlerp, overlay, overlay_color, saturate_color


def build_vehicle(g,r):
    s,c,t=r['scalars'],r['colors'],r['textures']
    def S(name,default=0):return g.parameter(name,s.get(name,default))
    # Vertex shader 9287 clamps requested UV indices to NumTexCoords-1.
    # The exported hoverbike has two UVs: its UV2 interpolator repeats UV1.
    tiled_channel=min(2,max(0,len(g.obj.data.uv_layers)-1))
    g.report['tile_uv_channel']=tiled_channel
    masks=g.image(t.get('Masks',''),g.uv(0),srgb=False,label='UV0 · wear and dirt masks')
    masks2=g.image(t.get('Masks 2',''),g.uv(0),srgb=False,label='UV0 · paint and roughness variation')
    detail=g.image(t.get('Detail Masks',''),g.transform_uv(tiled_channel,S('Detail Masks Tile',4)),srgb=False,label=f'UV{tiled_channel} · wear and dirt breakup')
    nom=g.image(t.get('NOM',''),g.transform_uv(tiled_channel,S('NOM Tile',6)),srgb=False,label=f'UV{tiled_channel} · tiled normal and specular response')
    if not all([masks,masks2,detail,nom]):
        g.report['warnings'].append('Required vehicle mask maps missing; base colour fallback used')
        g.finish(c.get('BC/R Tint',(.3,.3,.3,1)));return
    with g.block('Vehicle paint, wear and dirt masks') as b:
        wear=b.clamp(b.add(masks['g'],b.mul(b.sub(detail['g'],1),b.parameter('Wear mask detail power',s.get('Wear Mask Detail Power',1)))))
        dirt=b.clamp(lerp(b,masks['b'],overlay(b,masks['b'],detail['b']),b.parameter('Dirt mask detail power',s.get('Dirt Mask Detail Power',1))))
        first=b.clamp(b.mul(masks2['r'],2));second=b.clamp(b.sub(b.mul(masks2['r'],2),1))
        wear=b.export(wear,'Wear');dirt=b.export(dirt,'Dirt');first=b.export(first,'Variation 1');second=b.export(second,'Variation 2')
    with g.block('Paint palette and roughness from colour alpha') as b:
        palette=[b.parameter(name,c.get(name,(.3,.3,.3,1)),'Color') for name in ['BC/R Tint','BC/R Var 1','BC/R Var 2','Wear BC/R','Dirt BC / R']]
        roughs=[b.parameter(name+' · alpha / roughness',c.get(name,(.3,.3,.3,1))[3]) for name in ['BC/R Tint','BC/R Var 1','BC/R Var 2','Wear BC/R','Dirt BC / R']]
        color=vlerp(b,vlerp(b,palette[0],palette[1],first),palette[2],second)
        color=vlerp(b,color,overlay_color(b,color,masks2['b']),b.parameter('BC detail intensity',s.get('BC Detail Intensity',.15)))
        color=vlerp(b,vlerp(b,color,palette[3],wear),palette[4],dirt)
        metal=lerp(b,b.parameter('Metalness',s.get('Metalness',0)),1,wear)
        metal=b.mul(metal,b.sub(1,dirt))
        color=b.vector('SCALE',color,scale=lerp(b,1,nom['b'],metal))
        rough=lerp(b,lerp(b,roughs[0],roughs[1],first),roughs[2],second)
        rough=lerp(b,rough,overlay(b,rough,masks2['b']),b.parameter('Rough detail intensity',s.get('Rough Detail Intensity',.4)))
        rough=lerp(b,rough,overlay(b,rough,masks2['g']),b.parameter('Rough variation intensity',s.get('Rough Var Intensity',.8)))
        rough=lerp(b,lerp(b,rough,roughs[3],wear),roughs[4],dirt)
        spec=b.clamp(b.mul(lerp(b,nom['b'],1,metal),.5))
        color=b.export(saturate_color(b,color),'Base colour','Color');rough=b.export(b.clamp(rough),'Roughness')
        metal=b.export(b.clamp(metal),'Metallic');spec=b.export(spec,'Specular')
    # Global NOM is baked; Layer0.NOM supplies the tiled layer through the legacy key.
    baked_ref=t.get('Baked Normal') or t.get('Global.NOM')
    baked=g.image(baked_ref,g.uv(1),srgb=False) if baked_ref and not baked_ref.lower().endswith('/flatnormal') else None
    base=g.decode_normal(baked)
    nx,ny,_=g.split(g.decode_normal(nom));bx,by,bz=g.split(base)
    strength=g.mul(S('Normal Intensity',.4),g.sub(1,dirt))
    normal=g.vector('NORMALIZE',g.combine(g.add(bx,g.mul(nx,strength)),g.add(by,g.mul(ny,strength)),bz))
    g.report['baked_normal_source']=baked_ref or 'not available'
    g.report['implemented_stages']=['compiled vehicle stack profile','global and layer parameter scopes preserved','UV0 masks; vertex shader clamps tiled UV2 to last mesh UV',
        'paint palette and two variation ramps','wear and dirt breakup','colour alpha controls roughness',
        'metal/specular response','baked and tiled normal XY addition']
    g.report['warnings'].append('Vehicle layered material functions beyond the recovered hoverbike stack require separate shader evidence')
    g.finish(color,rough,metal,normal,spec)
