"""Current masked decal masters: channel palettes, CA tint, atlas masks and normal trims."""
from .masked import lerp, vlerp, saturate_color, vertex
from .surfaces import luminance, waterline_layer, detail_normal_enabled, detail_normal_tiling

GRUNGE='PioneerGame/Content/Pioneer/MaterialLibrary/Textures/Breakup/T_Global_04_GRM'
ATLAS_DETAIL='PioneerGame/Content/Pioneer/MaterialLibrary/Textures/Breakup/T_Generic_02_GRM'
CA_DETAIL_GRUNGE='PioneerGame/Content/Pioneer/MaterialLibrary/Textures/Breakup/Generic_01_GRM'
# Master defaults from the compiled SM5 preshader parameters, for values an instance leaves unset.
ATLAS_CA_DEFAULTS={'Columns':2,'Rows':1,'Tiling U':1,'Tiling V':1,'Color Desaturation':0,'Color Multiply':1,
                   'Opacity Amount':1,'Roughness Contrast':0,'Roughness Bias':1}
CA_DETAIL_DEFAULTS={'Grunge Tiling':1,'Contrast':.5,'Power':.5}


def build_window(g, r):
    """Two-sided pane with authored opacity and a physical Blender glass response."""
    s, t = r['scalars'], r['textures']
    mask = g.image(t.get('MaskTexture', ''), g.uv(0), srgb=False, label='Glass dirt mask', required=False)
    rough = g.clamp(g.parameter('Roughness', float(s.get('Roughness', .15))))
    outside = g.clamp(g.parameter('OpacityOutside', float(s.get('OpacityOutside', .35))))
    inside = g.clamp(g.parameter('OpacityInside', float(s.get('OpacityInside', s.get('OpacityOutside', .35)))))
    geometry = g.node('ShaderNodeNewGeometry', 'Pane sides')
    facing = geometry.outputs['Backfacing']
    opacity = g.mix(outside, inside, facing)
    if mask:
        front = s.get('DirtStrengthOutside', s.get('DirtStrengtOutside', 1.))
        back = s.get('DirtStrengthInside', front)
        strength = g.mix(g.clamp(g.parameter('DirtStrengthOutside', front)), g.clamp(g.parameter('DirtStrengthInside', back)), facing)
        dirt = g.mul(g.clamp(mask['r']), strength)
        rough = g.mix(rough, g.clamp(g.parameter('Dirt Roughness', s.get('DirtRoughness', .7))), dirt)
    normal = g.image(t.get('Normal', t.get('NormalMap', '')), g.transform_uv(0, s.get('NormalTiling', 1.)), srgb=False, label='Pane normal', required=False)
    color = r['colors'].get('GlassColor', r['colors'].get('Color', (.72, .8, .84, 1)))
    response = _glass_response(g, s)
    g.report['implemented_stages'] = ['window pane', 'inside/outside opacity and dirt', 'physical glass transmission and IOR', 'authored normal']
    g.report['warnings'].append('approximate: Blender transmission replaces native glass reflection and refraction transport')
    g.finish(color, rough, 0, g.decode_normal(normal) if normal else None, specular=min(max(float(s.get('Specular', .5)), 0.), 1.), alpha=opacity, response=response)


def _glass_response(g, scalars):
    transmission = g.clamp(g.parameter('Transmission', scalars.get('Transmission', 1.)))
    ior = g.math('MAXIMUM', g.parameter('IOR', scalars.get('IOR', scalars.get('Refraction', 1.45))), 1.)
    return {'Transmission Weight': transmission, 'IOR': ior}


def build_translucent_glass(g, r):
    """M_PropPreset_Translucent+Reflection+Detail: clear glass with authored base and dirt opacity."""
    s, t = r['scalars'], r['textures']
    base = g.clamp(g.parameter('BaseOpacity', float(s.get('BaseOpacity', .1))))
    dirt_opacity = g.clamp(g.parameter('DirtOpacity', float(s.get('DirtOpacity', .4))))
    mask = g.image(t.get('MaskTexture', ''), g.uv(0), srgb=False, label='Glass dirt mask', required=False)
    rough = g.clamp(g.parameter('Roughness', s.get('Roughness', .05)))
    alpha = base
    color = r['colors'].get('GlassColor', r['colors'].get('Color', (.8, .84, .86, 1)))
    if mask:
        dirt = g.clamp(mask['r'])
        alpha = g.clamp(g.add(base, g.mul(dirt, dirt_opacity)))
        rough = g.mix(rough, g.clamp(g.parameter('Dirt Roughness', s.get('DirtRoughness', .6))), dirt)
    spec = min(max(float(s.get('Specular', .5)), 0.), 1.)
    normal = g.image(t.get('Normal', t.get('NormalMap', '')), g.transform_uv(0, s.get('NormalTiling', 1.)), srgb=False, label='Glass normal', required=False)
    g.report['implemented_stages'] = ['translucent glass', 'authored BaseOpacity + DirtOpacity mask', 'dirt roughness', 'physical transmission and IOR', 'authored normal']
    g.report['warnings'].append('approximate: Blender transmission replaces native glass reflection and refraction transport')
    g.finish(color, rough, 0, g.decode_normal(normal) if normal else None, specular=spec, alpha=alpha, response=_glass_response(g, s))


def opacity(g,r,value):
    blend=str(r['settings'].get('BlendMode',''))
    if 'Translucent' in blend or blend in {'2','3','4'}:return g.clamp(value)
    clip=float(r['settings'].get('OpacityMaskClipValue',.3333))
    if r['settings'].get('DitherOpacityMask'):
        # DitherOpacityMask keeps a pixel when mask - clip + dither >= 0.5 with dither spread over [0, 5/6) (compiled
        # D3392735/F30C875C), so mean coverage is (mask - clip + 1/3) * 6/5; Blender dithers fractional alpha alike.
        # The 1e-4 floor drops the ~4e-5 coverage the authored 0.3333 (not 1/3) leaves on empty texels.
        return g.clamp(g.mul(g.add(value,1/3-clip-1e-4),1.2))
    return g.math('GREATER_THAN',value,clip,label='Authored opacity cutoff')


def luma709(g,color):
    return g.vector('DOT_PRODUCT',color,(.2126,.7152,.0722))


def atlas_uv(g,uv,rows,columns,position):
    rows=max(1,rows);columns=max(1,columns)
    x,y,_=g.split(uv);index=int(position)
    return g.combine(g.div(g.add(x,index%columns),columns),g.div(g.add(y,index//columns),rows))


def actor_atlas_uv(g,uv,rows,columns):
    """Use Randomness cell: floor(5 * (actor x + y + z) in Unreal centimetres) mod Columns*Rows, read from the object
    origin (Blender Y is Unreal -Y), with frac keeping tiled UVs inside the cell."""
    info=g.node('ShaderNodeObjectInfo','Actor position')
    px,py,pz=g.split(info.outputs['Location'])
    cell=g.math('FLOORED_MODULO',g.math('FLOOR',g.mul(g.add(g.sub(px,py),pz),500)),rows*columns)
    column=g.math('FLOORED_MODULO',cell,columns);row=g.math('FLOOR',g.div(cell,columns))
    x,y,_=g.split(uv)
    return g.combine(g.div(g.add(g.math('FRACT',x),column),columns),g.div(g.add(g.math('FRACT',y),row),rows))


def atlas_mask_opacity_ready(record):
    scalars,switches=record['scalars'],record['switches']
    required=('Mask Detail Intensity','Mask Multiply','Mask Contrast','Mask Global Opacity')
    return (record['master'].rsplit('/',1)[-1].lower()=='m_decalpreset_masked_atlas+detail_nocontact'
            and all(switches.get(name) is False for name in ('World Mask','Use Randomness','Atlas Active','Mask Invert Values'))
            and switches.get('Mask Detail Invert') is True
            and not any(switches.get(name) for name in ('UseAlphaForMask','TileableUVs','Use Atlas'))
            and all(name in scalars and isinstance(scalars[name],(int,float)) and not isinstance(scalars[name],bool)
                    and abs(scalars[name])<float('inf') for name in required)
            and bool(record['textures'].get('Variation Masks')))


def atlas_bc_variation_ready(record):
    scalars,colors,switches=record['scalars'],record['colors'],record['switches']
    required=['Tile V','Variation Mask Gradient Power','Variation Mask Intensity','Variation Mask Detail Overlay',
              'BC Variation Opacity']+[f'BC Variation {index} {field}' for index in (1,2) for field in ('Range','Position','Strength')]
    return (record['master'].rsplit('/',1)[-1].lower()=='m_decalpreset_masked_atlas+detail_nocontact'
            and switches.get('World Mask') is False and switches.get('Use Randomness') is False
            and not switches.get('Use Color Map') and all(name in scalars for name in required)
            and all(isinstance(scalars[name],(int,float)) and not isinstance(scalars[name],bool)
                    and abs(scalars[name])<float('inf') for name in required)
            and all(name in colors for name in ('BC Color','BC Variation 1 Color','BC Variation 2'))
            and scalars['Variation Mask Detail Overlay']==0
            and all(scalars[f'BC Variation {index} Range']>0 for index in (1,2))
            and bool(record['textures'].get('Variation Masks')))


def atlas_bc_variation(graph,record,mask,detail,color):
    scalars,colors=record['scalars'],record['colors']
    def scalar(name):return graph.parameter(name,scalars[name])
    uv_y=graph.split(graph.uv(0))[1]
    gradient=graph.sub(1,graph.mul(graph.mul(uv_y,scalar('Tile V')),scalar('Variation Mask Gradient Power')))
    varied_mask=graph.mul(mask['r'],gradient)
    varied_mask=graph.clamp(graph.mul(varied_mask,graph.add(1,graph.mul(graph.sub(detail['r'],1),scalar('Variation Mask Intensity')))))
    def band(index):
        extent=scalar(f'BC Variation {index} Range')
        position=scalar(f'BC Variation {index} Position')
        ramp=graph.clamp(graph.div(graph.sub(varied_mask,graph.sub(position,extent)),graph.clamp(graph.mul(extent,2))))
        return graph.clamp(graph.mul(graph.mul(graph.mul(ramp,graph.sub(1,ramp)),2),scalar(f'BC Variation {index} Strength')))
    first=graph.parameter('BC Variation 1 Color',colors['BC Variation 1 Color'],'Color')
    second=graph.parameter('BC Variation 2',colors['BC Variation 2'],'Color')
    varied=vlerp(graph,color,first,band(1))
    varied=vlerp(graph,varied,second,band(2))
    graph.report['warnings'].append('SM5-conditional NoContact BC variation: positive ranges, zero detail overlay, World Mask and randomness disabled; sampling and active SM6 draw remain unverified')
    return vlerp(graph,color,varied,scalar('BC Variation Opacity'))


def atlas_detail_normal_tiling(record):
    scalars,switches=record['scalars'],record['switches']
    value=scalars.get('UV_TilingDetailNormal')
    if (record['master'].rsplit('/',1)[-1].lower()=='m_decalpreset_masked_atlas+detail_nocontact'
            and all(switches.get(name) is True for name in ('Enable Detail Normal','UseUVForDetailNormal'))
            and all(switches.get(name) is False for name in ('World Mask','Use Randomness','Atlas Active'))
            and isinstance(value,(int,float)) and not isinstance(value,bool) and abs(value)<float('inf')):
        return value
    return detail_normal_tiling(scalars)


def atlas_color_overlay(g,s,t,color,compiled_scalar=False):
    """Use the conditional SM5 scalar-red overlay for NoContact, retaining the legacy RGB fallback elsewhere.
    Upstream BC Variation bands and spatial sampling remain unverified."""
    ov=g.image(t.get('Color Overlay',''),g.transform_uv(0,s.get('Overlay_Tiling',1)),label='Color Overlay',required=False)
    if not ov:
        return color,False
    if compiled_scalar:
        mod=g.vector('SCALE',g.vector('SCALE',color,scale=ov['r']),scale=2)
        color=vlerp(g,color,mod,g.parameter('Color Overlay Amount',s.get('Color Overlay Amount',1)))
        g.report['warnings'].append('SM5-conditional NoContact Color Overlay: scalar R, unclamped Color Overlay Amount; SM6, BC Variation, World Mask and sampling remain unverified')
    else:
        mod=g.vector('SCALE',g.cmul(color,ov['color']),scale=2)
        color=vlerp(g,color,mod,g.clamp(s.get('Color Overlay Amount',1)))
        g.report['warnings'].append('approximate: Atlas Color Overlay = 2x multiply by Color Overlay at UV0 x Overlay_Tiling, lerp by Color Overlay Amount; BC Variation bands not rebuilt')
    return color,True


def damage_color(g,color,alpha,breakup,damage):
    scaled=g.vector('SCALE',color,scale=g.mul(alpha,damage))
    color=vlerp(g,color,scaled,g.sub(1,g.clamp(alpha)))
    tint=g.vector('ADD',scaled,g.combine(g.mul(breakup,.5),g.mul(breakup,.5),g.mul(breakup,.5)))
    return saturate_color(g,vlerp(g,color,tint,g.clamp(g.sub(damage,.5))))


def build_decal(g,r):
    s,c,t,sw=r['scalars'],r['colors'],r['textures'],r['switches']
    master=r['master'].rsplit('/',1)[-1].lower()
    def S(k,d=0):return g.parameter(k,s.get(k,d))
    def C(k,d=(1,1,1,1)):return g.parameter(k,c.get(k,d),'Color')
    normal=None;rough=S('Roughness',.5);metal=S('Metalness',0)
    uv=g.uv(0)
    if master=='m_decalpreset_masked_na':
        tex=g.image(t.get('Trim sheet',''),uv,srgb=False,label='Trim sheet · RG normal / B opacity')
        if not tex:g.finish(c.get('Color',(.3,.3,.3,1)));return
        normal=vlerp(g,(0,0,1),g.decode_normal(tex),S('Normal intensity',1))
        color=saturate_color(g,C('Color',(.3,.3,.3,1)));alpha=tex['b']
        stages=['normal-trim B opacity','authored Color, Roughness, Metalness','normal intensity after Z reconstruction']
    elif master=='m_raidermarks_01_a':
        tex=g.image(t.get('Raider Mark Texture',''),uv,label='Raider mark colour and alpha',required=False)
        if not tex:g.finish(c.get('Raider Mark Color',(.3,.3,.3,1)));return
        color=g.cmul(tex['color'],C('Raider Mark Color'),'Raider Mark Color');alpha=tex['a']
        g.report['warnings'].append('approximate: Raider mark: colour = mark colour x texture, alpha = texture alpha; not traced from compiled bytecode')
        stages=['raider mark texture colour and alpha','authored Raider Mark Color']
    elif master=='m_meshdecal_colorsimple_deffered_01':
        if sw.get('Use Atlas Functionality'):
            uv=atlas_uv(g,uv,s.get('Number of rows',1),s.get('Number of columns',1),s.get('AtlasPosition',0))
        tex=g.image(t.get('CA Texture',''),uv,label='Decal colour and alpha',required=False) if sw.get('UseDecalTexture_CA',True) else None
        color=C('Colour');alpha=S('Opacity',1)
        if tex:
            color=g.cmul(tex['color'],color,'Colour');alpha=g.mul(alpha,g.mul(tex['a'],S('TextureAlphaStrength',1)))
        g.report['warnings'].append('approximate: ColorSimple mesh decal: Colour x CA, CA alpha x TextureAlphaStrength x Opacity; two-colour breakup, grunge breakup and vertex blend not reconstructed')
        stages=['CA colour and alpha','authored Colour tint','atlas cell UV','opacity and texture alpha strength']
    elif master=='m_decalpreset_masked_ca+detail':
        if sw.get('UseAtlas'):uv=atlas_uv(g,uv,s.get('Rows',1),s.get('Columns',1),s.get('Position',0))
        tex=g.image(t.get('CA',''),uv,label='Decal colour and alpha')
        if not tex:g.finish((.3,.3,.3,1));return
        color=tex['color'];alpha=tex['a']
        if sw.get('Tint Color'):color=g.cmul(color,C('Tint Color'))
        color=vlerp(g,color,g.combine(*([luminance(g,color)]*3)),g.sub(1,g.clamp(S('Saturation',1))))
        if sw.get('Grunge opacity breakup') or sw.get('GrungColor'):
            grunge=g.image(t.get('Grunge texture') or t.get('Grunge') or GRUNGE,g.transform_uv(0,s.get('Tiling',1)),srgb=False)
            if grunge:
                if sw.get('Grunge opacity breakup'):alpha=g.mul(alpha,g.math('POWER',grunge['g'],S('Grunge Power',.5)))
                if sw.get('GrungColor'):
                    g.report['warnings'].append('CA grunge colour branch requires separate compiled evidence; base tint is preserved')
        alpha=g.math('POWER',g.clamp(alpha),S('Opacity Power',1))
        color=saturate_color(g,color)
        stages=['CA colour and alpha','authored Tint Color switch','saturation','opacity power and grunge mask']
    elif master.endswith('translucent_ca+detail'):
        # Compiled SM5 map FF3F713F: the fixed master breakup erodes the CA alpha (R) and averages into roughness (G).
        d = CA_DETAIL_DEFAULTS
        tex = g.image(t.get('CA', ''), uv, label='Decal colour and alpha')
        if not tex:
            g.finish((.3, .3, .3, 1))
            return
        color, alpha = saturate_color(g, tex['color']), tex['a']
        grunge = g.image(CA_DETAIL_GRUNGE, g.transform_uv(0, s.get('Grunge Tiling', d['Grunge Tiling'])), srgb=False,
                         label='Fixed master breakup', required=False)
        if grunge:
            contrast = S('Contrast', d['Contrast'])
            erosion = g.mul(g.math('POWER', grunge['r'], S('Power', d['Power'])), g.add(1, g.mul(contrast, 2)))
            alpha = g.clamp(g.sub(alpha, g.clamp(g.sub(erosion, contrast))))
            rough = g.mul(g.add(grunge['g'], rough), .5)
        stages = ['CA colour and alpha', 'fixed breakup R alpha erosion (power, contrast)', 'breakup G roughness average']
    elif 'atlas+ca' in master:
        # Compiled SM5 maps D3392735 (Translucent_Atlas+CA+Detail) and F30C875C (Masked_Atlas+CA+NOR+Detail_01).
        d = ATLAS_CA_DEFAULTS
        nor = 'nor' in master
        if float(s.get('UV Mode', 0) or 0):
            g.report['warnings'].append('Atlas CA UV Mode axis scaling uses unscaled UVs; object scale is not read')
        # Simpel Tilling (default on) is the plain uv * (Tiling U, Tiling V) path built here.
        if not sw.get('Simpel Tilling', True):
            g.report['warnings'].append('approximate: Simpel Tilling off: the non-simple tiling path is not traced; uv * (Tiling U, Tiling V) used')
        x, y, _ = g.split(uv)
        uv = g.combine(g.mul(x, s.get('Tiling U', d['Tiling U'])), g.mul(y, s.get('Tiling V', d['Tiling V'])))
        rows, cols = max(1, int(s.get('Rows', d['Rows']) or 1)), max(1, int(s.get('Columns', d['Columns']) or 1))
        if rows > 1 or cols > 1:
            uv = actor_atlas_uv(g, uv, rows, cols)
        tex = g.image(t.get('CA', ''), uv, label='Decal colour and alpha')
        if not tex:
            g.finish((.3, .3, .3, 1))
            return
        color = vlerp(g, tex['color'], g.combine(*([luma709(g, tex['color'])] * 3)), S('Color Desaturation', d['Color Desaturation']))
        color = saturate_color(g, g.vector('SCALE', color, scale=S('Color Multiply', d['Color Multiply'])))
        alpha = g.mul(tex['a'], S('Opacity Amount', d['Opacity Amount']))
        packed = g.image(t.get('NOR', ''), uv, srgb=False, label='Normal RG, occlusion B, roughness A', required=False) if nor else None
        if packed:
            normal, rough = g.decode_normal(packed), packed['a']
        elif not nor:
            contrast = S('Roughness Contrast', d['Roughness Contrast'])
            shifted = g.sub(1, g.add(tex['r'], S('Roughness Bias', d['Roughness Bias'])))
            rough = g.add(g.sub(1, contrast), g.mul(g.math('MINIMUM', g.mul(shifted, shifted), 1), g.sub(g.mul(contrast, 2), 1)))
        stages = ['atlas CA: frac(uv * tiling) in the actor-position cell', 'Rec.709 desaturation and colour multiply',
                  'NOR normal and roughness' if nor else 'CA red roughness bias and contrast', 'opacity amount']
    elif 'parallax_ca+noh' in master and t.get('BaseColor'):
        # R17: Parallax_CA+NOH binds BaseColor (CA) and Normal (NOH); the parallax occlusion offset is not exported.
        if sw.get('Use Atlas'):
            uv=atlas_uv(g,uv,s.get('Rows',1),s.get('Columns',1),s.get('Position',0))
        tex=g.image(t['BaseColor'],uv,label='Decal BaseColor and alpha')
        if not tex:g.finish((.3,.3,.3,1));return
        color=saturate_color(g,tex['color'])
        alpha=g.math('POWER',g.clamp(g.mul(tex['a'],S('Opacity',1))),S('Opacity Power',1))
        contrast=S('Roughness Contrast',0)
        shifted=g.sub(1,g.add(tex['r'],S('Roughness Bias',1)))
        rough=g.add(g.sub(1,contrast),g.mul(g.math('MINIMUM',g.mul(shifted,shifted),1),g.sub(g.mul(contrast,2),1)))
        stages=['parallax decal BaseColor colour and alpha','opacity and opacity power']
        g.report['warnings'].append('approximate: roughness = atlas CA red bias/contrast curve on BaseColor red (not traced for Parallax_CA+NOH)')
        if sw.get('Use Parallax') or sw.get('UseParallaxOnHigh'):
            g.report['warnings'].append('approximate:parallax flat: parallax occlusion offset (Height Ratio, steps) not recreated; BaseColor/Normal sampled at the unshifted UV')
        nmap=g.image(t.get('Normal',''),uv,srgb=False,label='Decal NOH normal',required=False) if sw.get('Use NormalMap',True) else None
        if nmap:
            normal=g.decode_normal(nmap,S('Normal Strength',1));stages.append('NOH normal')
        if detail_normal_enabled(sw) and t.get('Detail Normal Texture'):
            dn=g.image(t['Detail Normal Texture'],g.transform_uv(0,detail_normal_tiling(s)),srgb=False,label='Detail normal')
            if dn:
                dnorm=g.decode_normal(dn,S('Detail Normal Strength',1))
                normal=g.rnm(normal,dnorm) if normal is not None else dnorm;stages.append('detail normals')
    elif (master=='m_defereddecal_x_01'
          and {key for key,value in sw.items() if value}=={'Grunge opacity breakup'}
          and not t.get('X') and not t.get('Grunge')):
        tex=g.image(t.get('Decal Mask',''),uv,srgb=False,label='Linear decal palette masks')
        if not tex:g.finish((.3,.3,.3,1));return
        colors=[C(key) for key in 'RGB']
        color=g.vector('SCALE',colors[0],scale=tex['r'])
        color=vlerp(g,vlerp(g,color,colors[1],tex['g']),colors[2],tex['b'])
        alpha=tex['a'];damage=S('Damage',0)
        grunge=g.image(GRUNGE,g.transform_uv(0,s.get('Damage Tiling',1),(1,1)),srgb=False,label='Fixed master grunge')
        if grunge:
            retention=g.sub(1,g.mul(g.mul(damage,damage),g.mul(
                g.clamp(g.mul(8,g.math('POWER',grunge['g'],5))),
                g.math('GREATER_THAN',grunge['g'],2.980232949312267e-08))))
            alpha=g.mul(alpha,retention)
            palette_red,_,_=g.split(color)
            rough=g.mul(g.math('GREATER_THAN',palette_red,2.980232949312267e-08),
                        g.math('POWER',g.math('MAXIMUM',palette_red,2.980232949312267e-08),rough))
            rough=g.math('MAXIMUM',g.sub(1,rough),0)
            scaled=g.vector('SCALE',color,scale=g.mul(alpha,damage))
            color=saturate_color(g,vlerp(g,color,scaled,g.sub(1,g.clamp(alpha))))
            alpha=g.clamp(g.mul(g.clamp(g.mul(g.add(alpha,-0.8999999761581421),9.999998092651367)),S('Global Opacity',1)))
            stages=['JKV compiled deferred palette','breakup green fifth-power damage retention',
                    'independent damage colour','alpha ramp then Global Opacity','palette roughness diagnostic']
        else:
            color=g.vector('SCALE',color,scale=g.add(g.add(tex['r'],tex['g']),tex['b']))
            alpha=g.mul(alpha,S('Global Opacity',1))
            stages=['Deferred X legacy fallback: missing fixed grunge']
            g.report['warnings'].append('Deferred X fixed grunge unavailable; prior unverified palette/opacity fallback retained')
        g.report['warnings'].append('JKV compiled colour/opacity applies only to the grunge-only switch branch; UV/random offsets, Normal Overlay, projection fade and renderer blending remain unverified')
    elif master in {'m_decalpreset_masked_x+detail','m_decalpreset_masked_rgb+a','m_defereddecal_x_01'}:
        rgb=master.endswith('rgb+a')
        deferred=master=='m_defereddecal_x_01'
        if not rgb and sw.get('AtlasTexture'):uv=atlas_uv(g,uv,s.get('AtlasRows',1),s.get('AtlasColumns',1),s.get('AtlasSelect',0))
        if deferred and sw.get('Use Texture Atlas'):uv=atlas_uv(g,uv,s.get('Rows',1),s.get('Columns',1),s.get('Position',0))
        if deferred:
            g.report['warnings'].append('approximate: Deferred X decal uses the Masked_X palette composition on Decal Mask; Normal Overlay and Global Opacity semantics not traced')
        tex=g.image(t.get('Decal Sheet' if rgb else 'X','') or t.get('Decal Mask',''),uv,srgb=False,label='Linear decal palette masks')
        if not tex:g.finish((.3,.3,.3,1));return
        with g.block('RGB palette masks and opacity') as b:
            colors=[b.parameter(k,c.get(k,(1,1,1,1)),'Color') for k in 'RGB']
            channels=[b.sub(tex[k],tex['a']) if rgb else tex[k] for k in 'rgb']
            color=b.vector('SCALE',colors[0],scale=channels[0])
            color=vlerp(b,vlerp(b,color,colors[1],channels[1]),colors[2],channels[2])
            total=b.add(b.add(channels[0],channels[1]),channels[2])
            if rgb:
                total=b.clamp(total)
                white=vlerp(b,(1,1,1),tex['color'],tex['a'])
                color=vlerp(b,white,color,total);alpha=b.add(total,tex['a'])
            else:
                color=b.vector('SCALE',color,scale=total);alpha=tex['a']
            color=b.export(color,'Palette colour','Color');alpha=b.export(alpha,'Sheet opacity')
        if deferred:alpha=g.mul(alpha,S('Global Opacity',1))
        damage=S('Damage',0)
        grunge=None
        if sw.get('Grunge opacity breakup'):
            # Fixed master texture. Object-position randomness is left at its zero-transform offset.
            grunge=g.image(t.get('Grunge',GRUNGE),g.transform_uv(0,s.get('Tiling',1),(1,1)),srgb=False,label='Fixed master grunge')
            if grunge:
                alpha=g.mul(alpha,g.math('POWER',grunge['g'],damage))
                if not rgb:
                    rough=g.math('POWER',g.clamp(g.mul(g.add(g.add(grunge['r'],grunge['g']),1),rough)),g.sub(1,g.div(damage,2)))
            g.report['warnings'].append('Decal object-position random grunge offsets use the imported object origin')
        if rgb:
            contrast=S('Contrast',0)
            alpha=g.clamp(g.sub(g.mul(alpha,g.add(1,g.mul(contrast,2))),contrast))
        if grunge:color=damage_color(g,color,alpha,grunge['b'],damage)
        if not rgb and sw.get('Enable Detail Normal'):
            detail=g.image(t.get('Detail Normal Texture',''),g.transform_uv(0,s.get('Detail Normal Tiling',1)),srgb=False)
            if detail:normal=g.decode_normal(detail,S('Detail Normal Strength',1))
        stages=['compiled RGB palette composition','linear mask channels','sheet alpha and authored cutoff',
                'fixed master grunge, damage colour and roughness','detail normals']
    else:
        # Atlas+Detail: masks affect opacity; BC Color is independent in the traced branch.
        uv=g.transform_uv(0,1)
        if sw.get('TileableUVs'):
            x,y,_=g.split(uv);uv=g.combine(g.mul(x,S('Tile U',1)),g.mul(y,S('Tile V',1)))
        # Parallax_CA+NOH names the same Rows/Columns/Position atlas select 'Use Atlas' (approximate: same cell math assumed).
        if sw.get('Atlas Active') or sw.get('Use Atlas'):uv=atlas_uv(g,uv,s.get('Rows',1),s.get('Columns',1),s.get('Position',0))
        if sw.get('Use Atlas') and not sw.get('Atlas Active'):
            g.report['warnings'].append('approximate: Use Atlas cell select assumed equal to the traced Atlas Active Rows/Columns/Position math')
        mask_ref = t.get('Mask') or t.get('Alpha Mask') or ''
        tex=g.image(mask_ref,uv,srgb=False,label='Atlas opacity masks')
        if not tex:g.finish(c.get('BC Color',(.3,.3,.3,1)));return
        detail_ref=t.get('Variation Masks',ATLAS_DETAIL)
        dx,dy,_=g.split(g.uv(0))
        tile=S('Mask Detail Tile',1)
        duv=g.combine(g.mul(g.mul(dx,S('Tile U',1)),tile),g.mul(g.mul(dy,S('Tile V',1)),tile))
        # The breakup only adds (R - 1) * Mask Detail Intensity (default 0), so a missing master copy degrades.
        variation_ready=atlas_bc_variation_ready(r)
        detail=g.image(detail_ref,duv,srgb=False,label='Atlas breakup R',required=False) if t.get('Variation Masks') or s.get('Mask Detail Intensity',0) != 0 else None
        compiled_opacity=bool(detail) and atlas_mask_opacity_ready(r)
        alpha=tex['r'] if compiled_opacity else tex['a'] if sw.get('UseAlphaForMask') else g.add(g.add(tex['r'],tex['g']),tex['b'])
        if sw.get('Mask Invert Values'):alpha=g.sub(1,alpha)
        if detail:
            breakup_channel=detail['b'] if compiled_opacity else detail['r']
            breakup=g.sub(1,breakup_channel) if sw.get('Mask Detail Invert') else breakup_channel
            alpha=g.add(alpha,g.mul(g.sub(breakup,1),S('Mask Detail Intensity',0)))
        elif s.get('Mask Detail Intensity',0):
            g.report['warnings'].append('Atlas breakup texture missing; Mask Detail Intensity not applied: '+detail_ref)
        alpha=g.mul(alpha,S('Mask Multiply',1));contrast=S('Mask Contrast',0)
        alpha=g.clamp(g.sub(g.mul(alpha,g.add(1,g.mul(contrast,2))),contrast))
        alpha=g.clamp(g.mul(alpha,S('Mask Global Opacity',1)))
        if compiled_opacity:
            g.report['warnings'].append('SM5-conditional NoContact opacity: mask R and inverted variation B; distance blending, dithering, sampling and active SM6 draw remain unverified')
        # A+Detail_ColourNoise instances author Color, not BC Color.
        color=C('Color',(.3,.3,.3,1)) if 'BC Color' not in c and 'Color' in c else C('BC Color',(.3,.3,.3,1))
        stages_extra=[]
        base_color_stage = 'authored BC Color'
        if variation_ready and detail:
            color=atlas_bc_variation(g,r,tex,detail,color)
            base_color_stage='SM5-conditional BC variation bands'
        if master == 'm_decalpreset_translucent_atlas+detail':
            ca = g.image(t.get('CA', ''), g.transform_uv(0, (1.2, 1)), label='Atlas colour and alpha')
            if ca:
                gray = g.vector('DOT_PRODUCT', ca['color'], (.212639, .715169, .072192))
                color = vlerp(g, ca['color'], g.combine(gray, gray, gray), S('Color Desaturation', 0))
                color = g.vector('SCALE', color, scale=S('Color Multiply', 1))
                base_color_stage = 'CA colour'
                stages_extra.append('compiled CA RGB, Rec.709 desaturation and colour multiply')
                g.report['warnings'].append('approximate: CA uses the traced Grunge Atlas UV scale (1.2, 1); compiled fixed colour overlay and variation tint are not reconstructed')
            else:
                g.report['warnings'].append('Atlas CA colour texture missing; authored BC Color fallback retained')
        overlay_built = False
        if sw.get('Use Color Overlay') and t.get('Color Overlay'):
            color,overlay_built=atlas_color_overlay(g,s,t,color,master=='m_decalpreset_masked_atlas+detail_nocontact')
            if overlay_built:stages_extra.append('Color Overlay multiply (approximate)')
        for feature in ['Use Color Map']+(['Use Color Overlay'] if not overlay_built else [])+['World Mask','Use Randomness','Mask Channel Random','VCMask','VirtualTextureBlend']:
            if sw.get(feature):g.report['warnings'].append('Atlas branch not reconstructed: '+feature)
        stages=['compiled Atlas+Detail mask opacity',base_color_stage,'mask detail, contrast and global opacity']+stages_extra
        if detail_normal_enabled(sw) and t.get('Detail Normal Texture'):
            dn=g.image(t['Detail Normal Texture'],g.transform_uv(0,atlas_detail_normal_tiling(r)),srgb=False,label='Detail normal')
            if dn:
                normal=g.decode_normal(dn,S('Detail Normal Strength',1));stages.append('detail normals')
                g.report['warnings'].append('approximate: atlas decal detail normal = decoded Detail Normal Texture at UV0 x detail tiling, scaled by Detail Normal Strength (no base normal to blend)')
    surf,_=waterline_layer(g,r,{'color':color,'rough':rough,'normal':normal},0,1)
    color,rough,normal=surf['color'],surf['rough'],surf['normal']
    g.report['implemented_stages']=stages+g.report.get('implemented_stages',[])
    g.report['warnings'].append('Mesh decals are shaded as their imported geometry; deferred projection, scene-depth intersection and normal-height effects are not recreated')
    g.finish(color,g.clamp(rough),g.clamp(metal),normal,alpha=opacity(g,r,alpha))
