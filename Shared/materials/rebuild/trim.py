"""M_TrimMap_01 base-pass equations recovered from the current 1.42 shaders."""
from .surfaces import luminance, desaturate

TRIM = 'PioneerGame/Content/Pioneer/MaterialLibrary/Textures/Trims/T_TrimDirectional_NMD'
NOISE = 'PioneerGame/Content/Pioneer/MaterialLibrary/Textures/BlendMask/T_Swirly_Noise'


def build_trim(g, r):
    s, c, t, sw = r['scalars'], r['colors'], r['textures'], r['switches']
    channel = 1 if sw.get('1. Use Texture Coord1 For Tiling', True) else 0
    g.report['texture_uv_channel'] = channel
    g.report['trim_uv_channel'] = 0
    trim = g.image(t.get('Trim control texture', TRIM), g.uv(0), srgb=False, label='UV0 · trim normal and wear mask')
    uv = g.transform_uv(channel, s.get('2. Base Material Tiling',1))
    cr = g.image(t.get('1.  Material CR',''), uv, label='UV1 · tiled base colour / roughness')
    noh = g.image(t.get('1.  Material NOH',''), uv, srgb=False, label='UV1 · tiled normal / AO / height')
    wear_uv = g.transform_uv(channel, s.get('2. Wear Tiling',1))
    wcr = g.image(t.get('1. Wear CR',''), wear_uv, label='Wear colour / roughness')
    wnoh = g.image(t.get('1. Wear NOH',''), wear_uv, srgb=False, label='Wear normal / AO / height')
    tile = s.get('2. Rust Breakup Tiling',.25)
    random_offset = sw.get('1. Overlay Random Offset',True)
    noise = g.image(t.get('Rust breakup texture',NOISE),g.transform_uv(channel,tile,(tile,tile) if random_offset else (0,0)),
                    srgb=False,label='Rust breakup · instance origin zero')
    if not cr or not noh:
        from .surfaces import build_surface
        build_surface(g,r); g.report['warnings'].append('Trim base maps missing; generic fallback used'); return
    if not trim: g.report['warnings'].append('Trim controls missing; neutral normal and zero trim wear mask substituted')
    tr = trim or {'r':.5,'g':.5,'b':0,'color':(.5,.5,0,1)}
    wn = wnoh or {'r':.5,'g':.5,'a':0}
    with g.block('Trim wear mask') as b:
        a,bg = tr['r'],tr['g']
        curvature = b.mul(3,b.sub(1,b.mul(b.mul(a,b.sub(1,a)),b.mul(bg,b.sub(1,bg)))))
        breakup = b.clamp(b.sub(1,b.mul(noise['g'] if noise else 0,b.parameter('Breakup strength',s.get('2. Rust Breakup Strenght',0)))))
        amount = b.div(b.mul(b.parameter('Rust strength',s.get('2. Rust Strenght',1)),2.7),b.math('MAXIMUM',breakup,1e-6))
        raw = b.mul(curvature,b.sub(1,b.mul(tr['b'],b.mul(wn['a'],amount))))
        wear = b.clamp(b.sub(b.math('POWER',b.math('MAXIMUM',raw,0),1.5),.22))
        wear = b.export(wear,'Wear','Float')
    with g.block('Base tint and rust colour') as b:
        gate = b.clamp(b.mul(b.mul(noh['a'],noh['a']),25))
        tinted = b.vector('SCALE',b.cmul(cr['color'],b.parameter('Base tint',c.get('2. ColorTint',(.5,.5,.5,1)),'Color')),scale=2)
        base = b.mix(cr['color'],tinted,gate)
        stain = b.cmul(desaturate(b,base,.8),(1.27083397,.990701973,.679543972,1))
        stain_factor = b.clamp(b.mul(b.mul(b.parameter('Wear stain amount',s.get('2. Wear Stain Amount',1)),wear),10))
        base = b.mix(base,stain,stain_factor)
        base = b.mix(base,wcr['color'] if wcr else base,wear)
        base = b.export(base,'Colour','Color')
    # Roughness is this master's nonlinear authored adjustment, not a multiplier.
    with g.block('Trim roughness response') as b:
        p = b.parameter('Base roughness',s.get('2. Roughness',1))
        v = b.mul(cr['a'],p)
        adjusted = b.sub(1,b.div(b.sub(1,v),b.math('MAXIMUM',b.mul(p,v),1e-6)))
        rough = b.mix(adjusted,v,p)
        rough = b.export(rough,'Roughness','Float')
    use_overlay = sw.get('2. OverlayTexture',False)
    strength = s.get('2. Overlay Strength',0)
    if use_overlay and strength:
        offset = s.get('2. Overlay Offset',0)
        ouv = g.transform_uv(channel,s.get('2. OverlayTiling',1),(offset,offset),s.get('2. OverlayRotation',0))
        overlay = g.image(t.get('2. Overlay CR',''),ouv,label='Overlay · authored UV1 scale')
        if overlay:
            with g.block('Overlay colour and roughness') as b:
                lum = luminance(b,overlay['color'])
                high = []
                for v in b.split(overlay['color']):
                    high.append(b.clamp(b.div(b.sub(b.mul(v,3),1),b.math('MAXIMUM',b.mul(v,2),1e-6))))
                h = luminance(b,b.combine(*high))
                tint_range = b.parameter('Tint range',s.get('2. Overlay Tint Range',1))
                gate = b.clamp(b.sub(1,b.div(b.sub(1,h),b.math('MAXIMUM',b.mul(tint_range,h),1e-6))))
                if sw.get('2. Overlay Range Invert',False): gate = b.sub(1,gate)
                tint = c.get('2. Overlay Tint Color',(.5,.5,.5,1)) if sw.get('2. OverlayTint Color',False) else (.5,.5,.5,1)
                tinted = b.cmul(desaturate(b,overlay['color'],s.get('2. Overlay Desaturate',0)),b.parameter('Overlay tint',tint,'Color'))
                mod = b.mix(b.vector('SCALE',b.cmul(base,tinted),scale=4),b.vector('SCALE',tinted,scale=2),gate)
                amount = b.parameter('Overlay strength',strength)
                color = b.mix(base,mod,amount)
                color = b.mix(color,base,b.clamp(b.mul(b.parameter('Overlay range',s.get('2. Overlay Range',0)),h)))
                color = b.mix(color,base,wear)
                # Compiled roughness response uses both luminances and no CR alpha.
                ov_r = b.sub(1,b.mul(b.sub(1,lum),b.parameter('Overlay roughness',s.get('2. Overlay Roughness',.5))))
                adjusted = b.mix(rough,b.add(b.mul(rough,h),h),.35)
                ov_r = b.sub(1,b.mul(ov_r,b.sub(1,b.mul(adjusted,.5))))
                rough = b.mix(rough,ov_r,amount)
                base = b.export(color,'Colour','Color')
                rough = b.export(rough,'Roughness','Float')
    rough = g.mix(rough,wcr['a'] if wcr else rough,wear)
    with g.block('Trim and tiled normal composition') as b:
        normal = b.decode_normal(noh)
        wear_normal = b.decode_normal(wnoh) if wnoh else (0,0,1)
        wear_normal = b.vector('MULTIPLY',wear_normal,(s.get('2. Wear Normal Strenght',1),)*2+(1,))
        normal = b.mix(normal,wear_normal,wear)
        nx,ny,nz = b.split(normal)
        a = g.obj.data.color_attributes
        if a:
            v = b.node('ShaderNodeVertexColor','Trim direction · vertex red'); v.layer_name=a[0].name
            red = b.split(v.outputs['Color'])[0]
        else: red = 1
        tx = b.mul(b.sub(b.mul(tr['r'],2),1),b.sub(b.mul(red,2),1))
        ty = b.mul(b.sub(b.mul(tr['g'],2),1),b.sub(b.mul(red,2),1))
        if not sw.get('1. Invert Red Channel Normals',True): tx=b.sub(b.mul(tr['r'],2),1); ty=b.sub(b.mul(tr['g'],2),1)
        if sw.get('2. Ignore Trim Normal',False): tx=ty=0
        scale = b.parameter('Material normal strength',s.get('2. Material Normal Strenght',1))
        normal = b.vector('NORMALIZE',b.combine(b.add(b.mul(nx,scale),tx),b.add(b.mul(ny,scale),ty),nz))
        normal = b.export(normal,'Normal','Vector')
    metal = 0
    if sw.get('UseMetalness',False) and s.get('2. Metalness',0):
        red = g.split(base)[0]
        metal = g.clamp(g.sub(g.sub(s.get('2. Metalness',0),wear),g.div(g.sub(1,red),g.math('MAXIMUM',g.mul(red,10),1e-6))))
    g.report['implemented_stages'] = ['M_TrimMap_01 compiled-shader profile','UV0 trim controls and UV1 tiled maps',
        'separate base / wear / breakup / overlay scales','curvature-height rust mask','height-gated tint and stain',
        'overlay colour and nonlinear roughness','trim plus tile normal XY composition','authored metal response']
    g.report['warnings'].append('Runtime actor random offsets, wetness, snow and view-dependent Fresnel tweaks are not reproduced; instance origin defaults to zero')
    if sw.get('1. Material Uses Vertex Colors',False): g.report['warnings'].append('Three vertex-colour material regions are not reconstructed; red-region material values used')
    for feature in ['EnableSigns','5. Weathering','Use Dust','6. Waterline Enable','2. Wear Options','2. Wear Large Scale Mask']:
        if sw.get(feature): g.report['warnings'].append('Additional trim feature omitted: '+feature)
    g.finish(base,g.clamp(rough),metal,normal)
