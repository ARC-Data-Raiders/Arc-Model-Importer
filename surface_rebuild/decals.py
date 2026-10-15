"""Current masked decal masters: channel palettes, CA tint, atlas masks and normal trims."""
from .masked import lerp, vlerp, saturate_color, vertex
from .surfaces import luminance

GRUNGE='PioneerGame/Content/Pioneer/MaterialLibrary/Textures/Breakup/T_Global_04_GRM'
ATLAS_DETAIL='PioneerGame/Content/Pioneer/MaterialLibrary/Textures/Breakup/T_Generic_02_GRM'


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


def opacity(g,r,value):
    blend=str(r['settings'].get('BlendMode',''))
    if 'Translucent' in blend or blend in {'2','3','4'}:return g.clamp(value)
    return g.math('GREATER_THAN',value,float(r['settings'].get('OpacityMaskClipValue',.3333)),label='Authored opacity cutoff')


def atlas_uv(g,uv,rows,columns,position):
    rows=max(1,rows);columns=max(1,columns)
    x,y,_=g.split(uv);index=int(position)
    return g.combine(g.div(g.add(x,index%columns),columns),g.div(g.add(y,index//columns),rows))


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
    elif 'atlas+ca' in master or master.endswith('translucent_ca+detail') or master.endswith('translucent_atlas+ca+detail'):
        rows, cols = max(1, int(s.get('Rows', 1) or 1)), max(1, int(s.get('Columns', 1) or 1))
        if rows > 1 or cols > 1:
            uv = atlas_uv(g, uv, rows, cols, s.get('Position', 0))
        tile_u, tile_v = s.get('Tiling U', s.get('Tile U', 1)), s.get('Tiling V', s.get('Tile V', 1))
        if tile_u != 1 or tile_v != 1:
            x, y, _ = g.split(uv)
            uv = g.combine(g.mul(x, tile_u), g.mul(y, tile_v))
        tex = g.image(t.get('CA', ''), uv, label='Decal colour and alpha')
        if not tex:
            g.finish((.3, .3, .3, 1))
            return
        color, alpha = tex['color'], tex['a']
        color = vlerp(g, color, g.combine(*([luminance(g, color)] * 3)), g.sub(1, g.clamp(S('Color Desaturation', s.get('Saturation', 1)))))
        alpha = g.mul(g.clamp(alpha), S('Opacity Amount', S('Opacity Power', 1)))
        color = saturate_color(g, g.vector('SCALE', color, scale=S('Color Multiply', 1)))
        stages = ['translucent atlas CA', 'position cell', 'color multiply and opacity']
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
        detail=g.image(detail_ref,duv,srgb=False,label='Atlas breakup R')
        if detail:
            breakup=g.sub(1,detail['r']) if sw.get('Mask Detail Invert') else detail['r']
            alpha=g.add(alpha,g.mul(g.sub(breakup,1),S('Mask Detail Intensity',0)))
        alpha=g.mul(alpha,S('Mask Multiply',1));contrast=S('Mask Contrast',0)
        alpha=g.clamp(g.sub(g.mul(alpha,g.add(1,g.mul(contrast,2))),contrast))
        alpha=g.clamp(g.mul(alpha,S('Mask Global Opacity',1)))
        color=C('BC Color',(.3,.3,.3,1))
        for feature in ['Use Color Map','Use Color Overlay','World Mask','Use Randomness','Mask Channel Random','VCMask','VirtualTextureBlend']:
            if sw.get(feature):g.report['warnings'].append('Atlas branch not reconstructed: '+feature)
        stages=['compiled Atlas+Detail mask opacity','authored BC Color','mask detail, contrast and global opacity']
    g.report['implemented_stages']=stages
    g.report['warnings'].append('Mesh decals are shaded as their imported geometry; deferred projection, scene-depth intersection and normal-height effects are not recreated')
    g.finish(color,g.clamp(rough),g.clamp(metal),normal,alpha=opacity(g,r,alpha))
