"""Current masked decal masters: channel palettes, CA tint, atlas masks and normal trims."""
from .masked import lerp, vlerp, saturate_color, vertex
from .surfaces import luminance

GRUNGE='PioneerGame/Content/Pioneer/MaterialLibrary/Textures/Breakup/T_Global_04_GRM'
ATLAS_DETAIL='PioneerGame/Content/Pioneer/MaterialLibrary/Textures/Breakup/T_Generic_02_GRM'
CA_DETAIL_GRUNGE='PioneerGame/Content/Pioneer/MaterialLibrary/Textures/Breakup/Generic_01_GRM'
# Master defaults from the compiled SM5 preshader parameters, for values an instance leaves unset.
ATLAS_CA_DEFAULTS={'Columns':2,'Rows':1,'Tiling U':1,'Tiling V':1,'Color Desaturation':0,'Color Multiply':1,
                   'Opacity Amount':1,'Roughness Contrast':0,'Roughness Bias':1}
CA_DETAIL_DEFAULTS={'Grunge Tiling':1,'Contrast':.5,'Power':.5}


def build_window(g, r):
    """Clear pane: dirt mask in the roughness, authored opacity kept visible."""
    s, t = r['scalars'], r['textures']
    mask = g.image(t.get('MaskTexture', ''), g.uv(0), srgb=False, label='Glass dirt mask')
    rough = float(s.get('Roughness', 0.15) or 0.15)
    if mask:
        dirt = min(float(s.get('DirtStrengtOutside', s.get('DirtStrengthInside', 1)) or 0), 1)
        rough = g.mix(rough, g.clamp(mask['r']), dirt)
    opacity = float(s.get('OpacityOutside', 0.35) or 0.35)
    if opacity < 0.2:
        opacity = 0.35
    g.report['implemented_stages'] = ['window pane', 'dirt mask roughness', 'visible glass opacity']
    g.finish((0.72, 0.8, 0.84, 1), rough, 0, specular=float(s.get('Specular', 0.5) or 0.5), alpha=opacity)


def build_translucent_glass(g, r):
    """M_PropPreset_Translucent+Reflection+Detail: clear glass with authored base and dirt opacity."""
    s, t = r['scalars'], r['textures']
    base = float(s.get('BaseOpacity', 0.1) or 0.1)
    dirt_opacity = float(s.get('DirtOpacity', 0.4) or 0.0)
    mask = g.image(t.get('MaskTexture', ''), g.uv(0), srgb=False, label='Glass dirt mask')
    alpha, rough, color = base, 0.05, (0.8, 0.84, 0.86, 1)
    if mask:
        dirt = g.clamp(mask['r'])
        alpha = g.clamp(g.add(base, g.mul(dirt, dirt_opacity)))
        rough = g.mix(0.05, 0.6, dirt)
    spec = min(max(float(s.get('Specular', 0.5) or 0.5), 0.0), 1.0)
    g.report['implemented_stages'] = ['translucent glass', 'authored BaseOpacity + DirtOpacity mask', 'dirt roughness']
    g.finish(color, rough, 0, specular=spec, alpha=alpha)


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
    elif master in {'m_decalpreset_masked_x+detail','m_decalpreset_masked_rgb+a'}:
        rgb=master.endswith('rgb+a')
        if not rgb and sw.get('AtlasTexture'):uv=atlas_uv(g,uv,s.get('AtlasRows',1),s.get('AtlasColumns',1),s.get('AtlasSelect',0))
        tex=g.image(t.get('Decal Sheet' if rgb else 'X',''),uv,srgb=False,label='Linear decal palette masks')
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
        if sw.get('Atlas Active'):uv=atlas_uv(g,uv,s.get('Rows',1),s.get('Columns',1),s.get('Position',0))
        mask_ref = t.get('Mask') or t.get('Alpha Mask') or ''
        tex=g.image(mask_ref,uv,srgb=False,label='Atlas opacity masks')
        if not tex:g.finish(c.get('BC Color',(.3,.3,.3,1)));return
        alpha=tex['a'] if sw.get('UseAlphaForMask') else g.add(g.add(tex['r'],tex['g']),tex['b'])
        if sw.get('Mask Invert Values'):alpha=g.sub(1,alpha)
        detail_ref=t.get('Variation Masks',ATLAS_DETAIL)
        dx,dy,_=g.split(g.uv(0))
        tile=S('Mask Detail Tile',1)
        duv=g.combine(g.mul(g.mul(dx,S('Tile U',1)),tile),g.mul(g.mul(dy,S('Tile V',1)),tile))
        # The breakup only adds (R - 1) * Mask Detail Intensity (default 0), so a missing master copy degrades.
        detail=g.image(detail_ref,duv,srgb=False,label='Atlas breakup R',required=False)
        if detail:
            breakup=g.sub(1,detail['r']) if sw.get('Mask Detail Invert') else detail['r']
            alpha=g.add(alpha,g.mul(g.sub(breakup,1),S('Mask Detail Intensity',0)))
        elif s.get('Mask Detail Intensity',0):
            g.report['warnings'].append('Atlas breakup texture missing; Mask Detail Intensity not applied: '+detail_ref)
        alpha=g.mul(alpha,S('Mask Multiply',1));contrast=S('Mask Contrast',0)
        alpha=g.clamp(g.sub(g.mul(alpha,g.add(1,g.mul(contrast,2))),contrast))
        alpha=g.clamp(g.mul(alpha,S('Mask Global Opacity',1)))
        # A+Detail_ColourNoise instances author Color, not BC Color.
        color=C('Color',(.3,.3,.3,1)) if 'BC Color' not in c and 'Color' in c else C('BC Color',(.3,.3,.3,1))
        for feature in ['Use Color Map','Use Color Overlay','World Mask','Use Randomness','Mask Channel Random','VCMask','VirtualTextureBlend']:
            if sw.get(feature):g.report['warnings'].append('Atlas branch not reconstructed: '+feature)
        stages=['compiled Atlas+Detail mask opacity','authored BC Color','mask detail, contrast and global opacity']
    g.report['implemented_stages']=stages
    g.report['warnings'].append('Mesh decals are shaded as their imported geometry; deferred projection, scene-depth intersection and normal-height effects are not recreated')
    g.finish(color,g.clamp(rough),g.clamp(metal),normal,alpha=opacity(g,r,alpha))
