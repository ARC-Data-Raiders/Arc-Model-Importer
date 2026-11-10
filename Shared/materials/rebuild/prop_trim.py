"""Traced trim tint masks for prop/enemy trim masters and concrete trim decals."""
from .masked import lerp, vlerp, overlay, range_mask, saturate_color, vertex
from .surfaces import desaturate
from .decals import opacity

TRIM_MASK='PioneerGame/Content/Pioneer/MaterialLibrary/Textures/Trims/T_PropTrimSheet_Mask'
DETAIL='PioneerGame/Content/Pioneer/MaterialLibrary/Textures/Props/Detail/TA_Detail_Props_02'


def trim_uv(g,r):
    s=r['scalars'];x,y,z=g.split(g.uv(0))
    mode=g.parameter('UV Mode',s.get('UV Mode',0))
    axis=s.get('Scale Axis',0)
    scales=[abs(v) for v in g.obj.matrix_world.to_scale()]
    scale=scales[2] if abs(axis-1)<.1 else scales[1] if axis>=1 else scales[0]
    choice=int(s.get('Mask on UVs',0))
    mask=g.math('LESS_THAN',x if choice==1 else y if choice>1 else 1,1.0000001)
    x=g.mul(x,g.add(1,g.mul(g.mul(mode,mask),g.sub(g.parameter('Imported object scale U',scale),1))))
    return g.combine(g.mul(x,g.parameter('Tiling U',s.get('Tiling U',1))),g.mul(y,g.parameter('Tiling V',s.get('Tiling V',1))))


def dominant_uv(g,size=350,local=False):
    geo=g.node('ShaderNodeNewGeometry','Surface projection')
    if local:
        coords=g.node('ShaderNodeTexCoord','Object detail coordinates')
        pos=coords.outputs['Object']
        n=g.node('ShaderNodeVectorTransform','Normal to object space',vector_type='NORMAL',convert_from='WORLD',convert_to='OBJECT')
        g.set(n.inputs['Vector'],geo.outputs['Normal']);normal=n.outputs['Vector']
    else:pos=geo.outputs['Position'];normal=geo.outputs['Normal']
    x,y,z=g.split(g.vector('MULTIPLY',pos,(100/size,-100/size,-100/size)))
    nx,_,nz=g.split(g.vector('ABSOLUTE',normal))
    uv=vlerp(g,g.combine(x,z),g.combine(y,z),g.math('GREATER_THAN',nx,.5))
    return vlerp(g,uv,g.combine(x,y),g.math('GREATER_THAN',nz,.5))


def trim_detail(g,r,control,uv):
    # Float array layers are selected in the shader at 8*A, clamped to the array bounds.
    ident=g.math('FLOOR',g.add(g.mul(control['a'],8),.5))
    if r['switches'].get('TintOffsetDetail'):
        override=g.parameter('TintOffsetDetailSelect',r['scalars'].get('TintOffsetDetailSelect',0))
        ident=lerp(g,ident,override,g.math('FLOOR',g.add(control['b'],.5)))
    ident=g.math('MINIMUM',g.math('MAXIMUM',ident,0),7)
    result={'r':0,'g':0,'b':0,'a':0};found=False
    for i in range(8):
        tex=g.image(r['textures'].get('Trim detail array',DETAIL),uv,layer=i,srgb=False,label='Trim detail slice '+str(i))
        if not tex:continue
        found=True;mask=g.math('COMPARE',ident,i,.1)
        for ch in result:result[ch]=g.add(result[ch],g.mul(tex[ch],mask))
    return result if found else None


def build_prop_trim(g,r):
    s,c,t,sw=r['scalars'],r['colors'],r['textures'],r['switches']
    uv=trim_uv(g,r);vc=vertex(g)
    cr=g.image(t.get('CR Texture',''),uv,label='Trim colour / roughness')
    control=g.image(t.get('NXX/NMX Texture',''),uv,srgb=False,label='Trim normal / tint-or-metal / surface mask')
    if not cr or not control:
        from .surfaces import build_surface
        build_surface(g,r);return
    with g.block('Tint masks and protected metal') as b:
        gate=range_mask(b,control['b'],b.parameter('Tint Range Low',s.get('Tint Range Low',0)),b.parameter('Tint Range High',s.get('Tint Range High',1)))
        protect=b.clamp(b.sub(1,b.mul(control['a'],10)))
        color=desaturate(b,cr['color'],s.get('DesaturateTrim',0))
        contrast=b.parameter('Color Contrast',s.get('Color Contrast',0))
        color=saturate_color(b,b.vector('SUBTRACT',b.vector('SCALE',color,scale=b.add(1,b.mul(contrast,2))),b.combine(contrast,contrast,contrast)))
        rough=cr['a']
        if sw.get('Use Tint'):
            tint=b.parameter('Tint Color 01',c.get('Tint Color 01',(1,1,1,1)),'Color')
            tinted=vlerp(b,color,b.cmul(color,tint),gate)
            if sw.get('Use Tint 02'):
                tint2=b.parameter('Tint Color 02',c.get('Tint Color 02',(1,1,1,1)),'Color')
                tinted=vlerp(b,tinted,vlerp(b,color,b.cmul(color,tint2),gate),vc['r'])
            color=vlerp(b,tinted,color,protect)
            rough=lerp(b,rough,b.mul(rough,b.parameter('Roughness Tint',s.get('Roughness Tint',1))),gate)
        metal=b.mul(control['b'] if sw.get('Use NMX') else protect,b.parameter('Master Metalness Multiply',s.get('Master Metalness Multiply',1)))
        color=b.export(color,'Base colour','Color');rough=b.export(rough,'Base roughness');metal=b.export(metal,'Metallic')
    with g.block('Trim and detail normals') as b:
        fixed=b.image(t.get('Trim concave mask',TRIM_MASK),uv,srgb=False,label='Fixed trim concavity mask')
        f=b.mul(b.mul(vc['g'],fixed['r'] if fixed else 0),b.parameter('ConcaveNormal',s.get('ConcaveNormal',0)))
        red=lerp(b,control['r'],b.sub(1,control['r']),f)
        green=lerp(b,control['g'],b.sub(1,control['g']),f)
        normal=b.decode_normal({'r':red,'g':green});detail=None
        if sw.get('Use Detail'):
            # Recovered object projection includes the sum of primitive axis scales.
            scale=sum(abs(v) for v in g.obj.matrix_world.to_scale())
            duv=b.vector('SCALE',dominant_uv(b,350,True),scale=scale)
            detail=trim_detail(b,r,control,duv)
            if detail:
                x,y,z=b.split(normal);dx,dy,_=b.split(b.decode_normal(detail))
                strength=b.parameter('Detail Normal Intensity',s.get('Detail Normal Intensity',1))
                normal=b.combine(b.add(x,b.mul(dx,strength)),b.add(y,b.mul(dy,strength)),z)
        normal=b.export(b.vector('NORMALIZE',normal),'Tangent normal','Vector')
        detail_color=b.export(detail['a'] if detail else 1,'Detail colour mask')
        detail_rough=b.export(detail['b'] if detail else .5,'Detail roughness mask')
    if sw.get('UseOverlay'):
        with g.block('Trim overlay colour and response') as b:
            tile=s.get('OverlayTiling',1);offset=s.get('UVOffset',0)
            if sw.get('TriPlanar Overlay'):
                ouv=dominant_uv(b,max(s.get('Overlay Size',200),.001))
                ouv=b.vector('ADD',ouv,(offset,offset,0))
            else:
                base=b.vector('SCALE',b.uv(0),scale=.1*tile)
                base=vlerp(b,base,b.uv(0),s.get('Use UV0 as Overlay UVs',0))
                base=vlerp(b,base,b.uv(1),s.get('Use UV1 as Overlay UVs',0))
                ouv=b.vector('ADD',b.vector('SCALE',base,scale=tile),(offset,offset,0))
            if s.get("Use Rotate UV's",0):
                rotate=b.node('ShaderNodeVectorRotate','Overlay rotation',rotation_type='AXIS_ANGLE')
                b.set(rotate.inputs['Vector'],ouv);rotate.inputs['Center'].default_value=(.5,.5,0)
                rotate.inputs['Angle'].default_value=s.get('Rotation Angle',0)*.017453292519943295;ouv=rotate.outputs[0]
            tex=b.image(t.get('Overlay',''),ouv,label='Trim overlay')
            if tex:
                mask=range_mask(b,tex['r'],b.parameter('OverlayRange Min',s.get('OverlayRange Min',0)),b.parameter('OverlayRange Max',s.get('OverlayRange Max',1)))
                mask=lerp(b,b.sub(1,mask),mask,b.parameter('OverlayRange Invert',s.get('OverlayRange Invert',0)))
                mod=b.cmul(desaturate(b,tex['color'],s.get('OverlayDesaturation',0)),b.parameter('Overlay Color',c.get('Overlay Color',(.5,.5,.5,1)),'Color'))
                mod=b.vector('SCALE',b.cmul(color,mod),scale=4)
                color=vlerp(b,color,mod,b.clamp(b.mul(mask,b.parameter('Overlay_BaseColor_Strength',s.get('Overlay_BaseColor_Strength',1)))))
                rough=lerp(b,rough,b.mul(b.mul(rough,2),b.mul(mask,b.parameter('Overlay_Roughness_Strength',s.get('Overlay_Roughness_Strength',1)))),b.clamp(mask))
                metal=lerp(b,metal,b.mul(metal,b.mul(b.parameter('Overlay_Metalness',s.get('Overlay_Metalness',.5)),2)),b.clamp(mask))
            color=b.export(color,'Overlay colour','Color');rough=b.export(rough,'Overlay roughness');metal=b.export(metal,'Overlay metallic')
    with g.block('Detail colour and roughness response') as b:
        amount=b.mul(b.sub(1,detail_color),b.parameter('Detail Color Intensity',s.get('Detail Color Intensity',1)))
        color=vlerp(b,color,b.vector('SCALE',color,scale=b.parameter('Detail Color Shift',s.get('Detail Color Shift',1))),amount)
        rough=lerp(b,rough,overlay(b,rough,b.math('POWER',detail_rough,1.5)),.65) if sw.get('Use Detail') else rough
        color=b.export(saturate_color(b,color),'Base colour','Color');rough=b.export(b.clamp(rough),'Roughness')
    for feature in ['Use Prop AO Dirt','Enable Blend Textures','EnableSigns','Use Holes','Use Dust']:
        if sw.get(feature):g.report['warnings'].append('Trim stage not reconstructed: '+feature)
    if r['master'].lower().endswith('m_enemytrimpreset_01'):
        g.report['warnings'].append('Enemy trim battle damage and animated states are not reconstructed')
    g.report['implemented_stages']=['compiled trim B tint mask','Tint Color 01/02 and vertex-red blend','protected metal mask from alpha',
        'trim contrast and roughness tint','fixed concavity mask','detail array normal, colour and roughness',
        'authored overlay colour and response']
    g.report['warnings'].append('Scene weather, runtime object transforms and view-angle specular differ from the game')
    g.finish(color,rough,g.clamp(metal),normal)


def build_architecture_trim(g,r):
    s,c,t,sw=r['scalars'],r['colors'],r['textures'],r['switches'];uv=trim_uv(g,r);base_uv=uv
    x,y,_=g.split(uv);uv=g.combine(x,g.add(g.mul(y,s.get('UV Scale',1)),s.get('UV Offset Amount',0)))
    cr=g.image(t.get('CR',''),uv,label='Concrete trim colour and roughness')
    n=g.image(t.get('NAO',''),uv,srgb=False,label='Concrete trim RG normal / B opacity')
    if not cr:g.finish(c.get('Color',(.3,.3,.3,1)));return
    color=saturate_color(g,g.vector('SCALE',g.cmul(cr['color'],g.parameter('Color',c.get('Color',(1,1,1,1)),'Color')),scale=g.parameter('Color Multiply',s.get('Color Multiply',1))))
    normal=g.decode_normal(n)
    detail=g.image(t.get('Detail Normal',''),g.vector('SCALE',base_uv,scale=s.get('Detail Normal Tile',1)),srgb=False,required=False)
    if detail:
        nx,ny,nz=g.split(normal);dx,dy,_=g.split(g.decode_normal(detail))
        ni=g.parameter('Normal intensity',s.get('Normal intensity',1));di=g.parameter('Detail Normal Intensity',s.get('Detail Normal Intensity',.5))
        normal=g.combine(g.add(g.mul(nx,ni),g.mul(dx,di)),g.add(g.mul(ny,ni),g.mul(dy,di)),nz)
    alpha=n['b'] if n and sw.get('Use Alpha mask') else 1
    if sw.get('Vertex color breakup'):alpha=g.mul(alpha,vertex(g)['r'])
    alpha=g.mul(alpha,g.parameter('Global opacity',s.get('Global opacity',1)))
    g.report['implemented_stages']=['compiled concrete trim colour multiplication','CR roughness','NAO B opacity','vertex-red breakup','detail normal XY addition']
    g.report['warnings'].append('Concrete trim weather, world-space opacity breakup and runtime object scale are not reconstructed')
    g.finish(color,cr['a'],normal=g.vector('NORMALIZE',normal),alpha=opacity(g,r,alpha))
