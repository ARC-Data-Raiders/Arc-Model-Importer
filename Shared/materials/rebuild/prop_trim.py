"""Traced trim tint masks for prop/enemy trim masters and concrete trim decals."""
from .masked import lerp, vlerp, overlay, range_mask, saturate_color, vertex
from .surfaces import _approximate, _first, desaturate, frost_layer, skip_textures, snow_layer
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
    if 'Tiling' in s and r.get('switches',{}).get('Turn on basic tiling'):
        # Tileable masters scale UV0 by one uniform Tiling scalar (native tileable_trim UV0*Tiling).
        t=g.parameter('Tiling',s['Tiling']);x,y=g.mul(x,t),g.mul(y,t)
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


def trim_blend_layer(g,r,color,rough,normal):
    """Second CR/NOH layer under an Overlay.R range mask (native tileable_trim equation)."""
    s,c,t,sw=r['scalars'],r['colors'],r['textures'],r['switches']
    with g.block('Trim blend textures') as b:
        tile=b.parameter('BlendTextureTile',s.get('BlendTextureTile',1));off=b.parameter('BlendTextureOffset',s.get('BlendTextureOffset',1))
        x,y,_=b.split(b.uv(0));buv=b.combine(b.add(b.mul(x,tile),off),b.add(b.mul(y,tile),off))
        bcr=b.image(t.get('CR Blend',''),buv,label='Blend colour / roughness')
        bnoh=b.image(t.get('NOH Blend',''),buv,srgb=False,label='Blend normal')
        ov=b.image(t.get('Overlay',''),b.uv(0),label='Blend mask (Overlay R)')
        if not ov or not (bcr or bnoh):
            g.report['warnings'].append('Trim blend textures enabled but CR/NOH Blend or Overlay mask missing')
            return color,rough,normal,False
        mask=range_mask(b,ov['r'],b.parameter('Blend Mask Range Low',s.get('Blend Mask Range Low',0)),b.parameter('Blend Mask Range High',s.get('Blend Mask Range High',1)))
        if sw.get('Invert Blend Mask'):mask=b.sub(1,mask)
        mask=b.clamp(mask)
        if bnoh:
            bs=b.parameter('Blend Texture Normal Strength',s.get('Blend Texture Normal Strength',1))
            bx,by,bz=b.split(b.decode_normal(bnoh))
            bn=b.combine(b.mul(bx,bs),b.mul(by,bs),b.add(b.mul(b.sub(bz,1),bs),1))
            normal=b.export(b.vector('NORMALIZE',vlerp(b,normal,bn,mask)),'Blend normal','Vector')
        if bcr:
            bcol=desaturate(b,bcr['color'],s.get('Blend Texture Desaturation',0))
            tint=b.parameter('Blend Tint',c.get('Blend Tint',(.5,.5,.5,1)),'Color')
            bcol=b.vector('SCALE',b.cmul(bcol,tint),scale=2)
            color=b.export(vlerp(b,color,bcol,mask),'Blend colour','Color')
            amount=b.mul(mask,b.parameter('BlendTextureAffectRoughness',s.get('BlendTextureAffectRoughness',1)))
            rough=b.export(lerp(b,rough,bcr['a'],b.clamp(amount)),'Blend roughness')
    g.report['warnings'].append('approximate: trim blend layer follows native tileable_trim (Overlay.R range mask on UV0, blend normal mixed after detail normal); PropTrim permutation not cooked-proven')
    return color,rough,normal,True


def trim_ao_dirt(g,r,color):
    """Use Prop AO Dirt: AO Dirt Colour pushed into baked-AO crevices."""
    s,c,t=r['scalars'],r['colors'],r['textures']
    strength=s.get('AO Dirt Strength',1)
    if strength<=.001:return color,False
    with g.block('Prop AO dirt') as b:
        ao=b.image(t.get('Prop AO Texture',''),b.uv(0),srgb=False,label='Prop AO')
        if not ao:
            g.report['warnings'].append('Use Prop AO Dirt enabled but Prop AO Texture missing')
            return color,False
        amount=b.clamp(b.mul(b.sub(1,ao['r']),b.parameter('AO Dirt Strength',strength)))
        dirt=b.parameter('AO Dirt Colour',c.get('AO Dirt Colour',(.19,.15,.09,1)),'Color')
        color=b.export(vlerp(b,color,dirt,amount),'AO dirt colour','Color')
    g.report['warnings'].append('approximate: prop AO dirt = lerp(albedo, AO Dirt Colour, saturate((1-AO.R)*AO Dirt Strength)); equation from env_props builder, not cooked-proven')
    return color,True


def trim_dust_normal(g,r,normal,uv):
    """Use Dust: Dirt Normal mixed in on up-facing surfaces (Top Down Dust Bias/Sharpness)."""
    s,t=r['scalars'],r['textures']
    if not t.get('Dirt Normal'):return normal,False
    with g.block('Top-down dust normal') as b:
        tile=max(s.get('Dust Detail Breakup Tiling',1),.001)
        dn=b.image(t.get('Dirt Normal',''),b.vector('SCALE',uv,scale=tile),srgb=False,label='Dirt Normal',required=False)
        if not dn:return normal,False
        geometry=b.node('ShaderNodeNewGeometry','Up axis for top-down dust')
        up=b.split(geometry.outputs['Normal'])[2]
        mask=b.clamp(b.mul(b.sub(up,b.parameter('Top Down Dust Bias',s.get('Top Down Dust Bias',.5))),
                            b.parameter('Top Down Dust Sharpness',s.get('Top Down Dust Sharpness',4))))
        normal=b.export(vlerp(b,normal,b.decode_normal(dn),mask),'Dust normal','Vector')
    _approximate(g,'top-down dust normal = lerp(normal, Dirt Normal, saturate((N.z-Top Down Dust Bias)*Top Down Dust Sharpness)); object normal stands in for world up')
    return normal,True


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
        # Tileable Metal root: most MIs leave MaskTintFromMetalness at the root default 1.
        base=str(r.get('master','')).rsplit('/',1)[-1].split('.')[0].lower()
        tileable='MaskTintFromMetalness' in s or base.startswith('m_tileable_metal')
        if tileable:
            # Tileable Metal contract (native tileable_trim): metal = B^2.2 with NMX, else saturate(1-8A).
            protect=b.math('POWER',control['b'],2.2) if sw.get('Use NMX') else b.clamp(b.sub(1,b.mul(control['a'],8)))
            protect_mix=b.mul(protect,b.parameter('MaskTintFromMetalness',s.get('MaskTintFromMetalness',1)))
        else:protect=protect_mix=b.clamp(b.sub(1,b.mul(control['a'],10)))
        color=desaturate(b,cr['color'],s.get('Desaturate',0) if tileable else s.get('DesaturateTrim',0))
        contrast=b.parameter('Color Contrast',s.get('Color Contrast',0))
        color=saturate_color(b,b.vector('SUBTRACT',b.vector('SCALE',color,scale=b.add(1,b.mul(contrast,2))),b.combine(contrast,contrast,contrast)))
        rough=cr['a']
        if sw.get('Use Tint'):
            tint=b.parameter('Tint Color 01',c.get('Tint Color 01',(1,1,1,1)),'Color')
            if tileable:tint=b.vector('SCALE',tint,scale=2)
            tinted=vlerp(b,color,b.cmul(color,tint),gate)
            if sw.get('Use Tint 02'):
                tint2=b.parameter('Tint Color 02',c.get('Tint Color 02',(1,1,1,1)),'Color')
                tinted=vlerp(b,tinted,vlerp(b,color,b.cmul(color,tint2),gate),vc['r'])
            color=vlerp(b,tinted,color,protect_mix)
            rough=lerp(b,rough,b.mul(rough,b.parameter('Roughness Tint',s.get('Roughness Tint',1))),gate)
        if tileable:
            metal=lerp(b,protect,b.math('MINIMUM',b.add(protect,gate),1),b.parameter('TintMetalness',s.get('TintMetalness',0)))
            g.report['warnings'].append('approximate: Tileable Metal switch permutation outside cooked native proof; PropTrim graph with Tileable tint x2, metal B^2.2 and MaskTintFromMetalness')
        else:metal=control['b'] if sw.get('Use NMX') else protect
        metal=b.mul(metal,b.parameter('Master Metalness Multiply',s.get('Master Metalness Multiply',1)))
        color=b.export(color,'Base colour','Color');rough=b.export(rough,'Base roughness');metal=b.export(metal,'Metallic')
    with g.block('Trim and detail normals') as b:
        fixed=b.image(t.get('Trim concave mask',TRIM_MASK),uv,srgb=False,label='Fixed trim concavity mask') if s.get('ConcaveNormal',0)!=0 else None
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
    # Newer PropTrim MIs author the overlay switch as 'Color Overlay' (UseOverlay inherited False, never overridden).
    overlay_keys=[k for k in ('UseOverlay','Color Overlay') if k in sw]
    if overlay_keys and not any(sw.get(k) for k in overlay_keys):
        skip_textures(g,r,'overlay','/'.join(overlay_keys)+' off: overlay layer not compiled')
    if any(sw.get(k) for k in overlay_keys):
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
            distort=b.image(t['OverlayDistortion'],ouv,srgb=False,label='Overlay distortion',required=False) if t.get('OverlayDistortion') else None
            if distort:
                amount=_first(s,['Overlay Distort','Overlay Distortion'],0)
                ouv=b.vector('ADD',ouv,b.vector('SCALE',b.combine(b.sub(distort['r'],.5),b.sub(distort['g'],.5),0),scale=amount))
                _approximate(g,'overlay distortion offsets overlay UV by (RG-0.5) x Overlay Distort')
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
    stages=[]
    if sw.get('Enable Blend Textures'):
        color,rough,normal,done=trim_blend_layer(g,r,color,rough,normal)
        if done:stages.append('second-layer CR/NOH Blend textures')
    with g.block('Detail colour and roughness response') as b:
        amount=b.mul(b.sub(1,detail_color),b.parameter('Detail Color Intensity',s.get('Detail Color Intensity',1)))
        color=vlerp(b,color,b.vector('SCALE',color,scale=b.parameter('Detail Color Shift',s.get('Detail Color Shift',1))),amount)
        rough=lerp(b,rough,overlay(b,rough,b.math('POWER',detail_rough,1.5)),.65) if sw.get('Use Detail') else rough
        color=b.export(saturate_color(b,color),'Base colour','Color');rough=b.export(b.clamp(rough),'Roughness')
    if sw.get('Use Prop AO Dirt'):
        color,done=trim_ao_dirt(g,r,color)
        if done:stages.append('prop AO dirt tint')
    if sw.get('Use Dust'):
        normal,done=trim_dust_normal(g,r,normal,uv)
        if done:stages.append('top-down dust normal (approximate)')
        g.report['warnings'].append('Trim stage not reconstructed: Use Dust colour and world breakup')
    elif 'Use Dust' in sw and r['textures'].get('Dirt Normal'):
        skip_textures(g,r,'dirt normal','Use Dust off: dust layer not compiled')
    for feature in ['EnableSigns','Use Holes']:
        if sw.get(feature):g.report['warnings'].append('Trim stage not reconstructed: '+feature)
    if r['master'].lower().endswith('m_enemytrimpreset_01'):
        g.report['warnings'].append('Enemy trim battle damage and animated states are not reconstructed')
    g.report['implemented_stages']=['compiled trim B tint mask','Tint Color 01/02 and vertex-red blend','protected metal mask from alpha',
        'trim contrast and roughness tint','fixed concavity mask','detail array normal, colour and roughness',
        'authored overlay colour and response']+stages
    surf, _ = frost_layer(g, r, {'color': color, 'rough': rough}, uv)
    surf['normal'] = normal
    surf, _ = snow_layer(g, r, surf, uv)
    color, rough, normal = surf['color'], surf['rough'], surf['normal']
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
