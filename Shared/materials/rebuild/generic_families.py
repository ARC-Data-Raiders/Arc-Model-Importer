"""Generic legacy families keyed on texture-parameter contracts, for masters with no native route.

Each builder reads parameter names and static switches only. Maths that is not traced from cooked
bytecode is labelled with an `approximate:` warning in the build report.
"""
import math

from .masked import lerp, vlerp, range_mask, vertex
from .surfaces import desaturate, detail_normal_enabled, detail_normal_tiling


def _approx(g, text):
    g.report['warnings'].append('approximate: ' + text)


def _clip(g, r, alpha, default=.3333):
    clip = r.get('settings', {}).get('OpacityMaskClipValue', default)
    return g.math('GREATER_THAN', alpha, float(clip if clip not in (None, '') else default))


def _color(r, *names, default=(1, 1, 1, 1)):
    c = r['colors']
    return next((c[n] for n in names if n in c), default)


def _tint(g, color, value, label):
    return g.cmul(color, g.parameter(label, value, 'Color'), label)


def _pick(t, *names):
    return next((t[n] for n in names if t.get(n)), None)


def build_vt_blend(g, r):
    """CR_1/NOH_1 and CR_2/NOH_2 layers (or the Tarmac '0. CR 1' naming) blended by vertex red or the '4. Mask'."""
    s, sw, t = r['scalars'], r['switches'], r['textures']
    layers, single = [], sw.get('Use Second Texture Set') is False
    # FZT_Nature ('0. CS'/'0. NOH') and ScalingPipe ('Base CR'/'Base NOH') name a single base layer.
    alias = not _pick(t, 'CR_1', '0. CR 1') and _pick(t, '0. CS', 'Base CR')
    if alias and not _pick(t, 'CR_2', '0. CR 2'):
        single = True
        _approx(g, 'single base layer from 0. CS / Base CR alias; CS alpha read as roughness, wear/unique/detail layers not reconstructed')
    for i in (('1',) if single else ('1', '2')):
        uv = g.transform_uv(0, s.get('Tiling ' + i, s.get('Tiling', 1)))
        extra_cr, extra_noh = (('0. CS', 'Base CR'), ('0. NOH', 'Base NOH')) if i == '1' and alias else ((), ())
        cr = g.image(_pick(t, 'CR_' + i, '0. CR ' + i, *extra_cr), uv, srgb=True, label='CR_' + i, required=False)
        noh = g.image(_pick(t, 'NOH_' + i, '0. NOH ' + i, *extra_noh), uv, srgb=False, label='NOH_' + i, required=False)
        layers.append({'color': cr['color'] if cr else (.5, .5, .5, 1), 'rough': cr['a'] if cr else .5,
                       'normal': g.decode_normal(noh) if noh else (0, 0, 1), 'ao': noh['b'] if noh else 1})
    mask = None if sw.get('VCMask') or not t.get('4. Mask') else g.image(t['4. Mask'], g.transform_uv(0, s.get('Mask Tiling', 1)), srgb=False,
                                                 label='4. Mask', required=False)
    factor = mask['r'] if mask else vertex(g)['r']
    breakup = g.image(t.get('Breakup Mask'), g.transform_uv(0, s.get('Mask Tiling', 1)), srgb=False,
                      label='Breakup Mask', required=False) if sw.get('Use Breakup Texture') else None
    if breakup:
        factor = range_mask(g, g.mul(factor, breakup['r']), s.get('Mask Range Low', s.get('MASK low', 0)),
                            s.get('Mask Range High', s.get('MASK high', 1)))
    if single:
        layers.append(layers[0]); factor = 0
    a, b = layers
    color = a['color'] if single else vlerp(g, a['color'], b['color'], factor)
    color = g.vector('SCALE', color, scale=a['ao'] if single else g.mix(a['ao'], b['ao'], factor))
    color = desaturate(g, color, s.get('Base Desaturation', s.get('Desaturation', 0)))
    if sw.get('Tint') or sw.get('Use Tint') or (alias and '1.  Material Color Tint' in r['colors']):
        name = next((n for n in ('Tint Color', '1. Tint', '1.  Material Color Tint') if n in r['colors']), 'Tint Color')
        color = _tint(g, color, _color(r, name), name)
    rough = g.clamp(g.mul(a['rough'] if single else lerp(g, a['rough'], b['rough'], factor), g.parameter('1. Roughness', s.get('1. Roughness', 1))))
    if 'Roughness Range Low' in s or 'Roughness Range High' in s:
        rough = lerp(g, s.get('Roughness Range Low', 0), s.get('Roughness Range High', 1), rough)
    normal = a['normal'] if single else vlerp(g, a['normal'], b['normal'], factor)
    stages = []
    if detail_normal_enabled(sw) and t.get('Detail Normal'):
        # R17: reuse the surface detail-normal RNM stage; the VTblend detail UV is not traced.
        tile = s.get('Detail Tile', detail_normal_tiling(s))
        dn = g.image(t['Detail Normal'], g.transform_uv(0, tile), srgb=False, label='Detail Normal', required=False)
        if dn:
            normal = g.rnm(normal, g.decode_normal(dn, s.get('Detail Normal Strength', 1)))
            stages.append('detail normals')
            _approx(g, 'VTblend detail normal: UV0 x Detail Tile, RNM onto the blended NOH normal')
    _approx(g, 'CR/NOH layer blend (vertex red or 4. Mask red, breakup range, roughness range); wear NOH, overlay, parallax, snow, large-scale tint and virtual-texture blend not reconstructed')
    g.report['implemented_stages'] = ['CR/NOH layer pair', 'vertex-colour or mask blend', 'breakup range mask', 'NOH ambient occlusion', 'authored tint', 'roughness range'] + stages
    g.finish(color, rough, 0, normal, s.get('Specular', .5))


def build_rusty_trim(g, r):
    """Rusty trim preset: Base Texture/Base Normal trim, Overlay-driven rust ranges, HolesNXX opacity."""
    s, sw, t = r['scalars'], r['switches'], r['textures']
    uv = g.transform_uv(1 if sw.get('Use 2nd UV Channel') or sw.get('UseUV2') else 0, s.get('Base UV Scale', 1))
    base = g.image(t.get('Base Texture'), uv, srgb=True, label='Base Texture', required=False)
    color = base['color'] if base else (.5, .5, .5, 1)
    if 'TintColor' in r['colors']:
        color = vlerp(g, color, _tint(g, color, r['colors']['TintColor'], 'TintColor'), s.get('TintOpacity', 1))
    nmap = g.image(_pick(t, 'Base Normal', 'DetailNormal'), uv, srgb=False, label='Base Normal', required=False) if sw.get('UseNormalMap', True) else None
    normal = g.decode_normal(nmap) if nmap else None
    over = g.image(t.get('Overlay'), g.transform_uv(1 if sw.get('Overlay Use 2nd UV Channel') else 0, s.get('Overlay UV Scale', 1)),
                   srgb=False, label='Overlay', required=False)
    rough = g.parameter('Base Roughness', s.get('Base Roughness', .6))
    metal = 1 if sw.get('Is Metallic') else 0
    if over:
        rust = range_mask(g, over['r'], s.get('Rust Range 1 Low', 0), s.get('Rust Range 1 High', 1))
        color = vlerp(g, color, g.cmul(color, g.parameter('Rust Tint', _color(r, 'Rust Tint', default=(.45, .22, .1, 1)), 'Color'), 'Rust tint'), rust)
        rough = lerp(g, rough, s.get('Rust Roughness Opacity', .85), rust)
        if metal: metal = g.sub(1, rust)
    alpha = None
    # UseAlpha is a sign-layer scalar (EnableSigns group), not a hole switch: holes follow UseOpacityMask only.
    if sw.get('UseOpacityMask'):
        holes = g.image(t.get('HolesNXX'), uv, srgb=False, label='HolesNXX', required=False)
        if holes:
            alpha = _clip(g, r, range_mask(g, holes['b'], s.get('OpacityRangeLow', 0), s.get('OpacityRangeHigh', 1)))
            if sw.get('UseOpacityMaskNormal'):
                # DR_APPROX_PLACEHOLDER: NormalMaskLow/High edge masking and HolesMaskSize UV scale of the HolesNXX normal
                hn = g.decode_normal(holes, s.get('NormalStrengthHoles', 1))
                normal = g.rnm(normal, hn) if normal else hn
    _approx(g, 'Rusty trim: Overlay red as rust mask (range 1), HolesNXX blue as hole mask, HolesNXX RG normal without NormalMaskLow/High or HolesMaskSize; signs atlas, waterline, dirt AO, triplanar, terrain mask and vertex-colour tint channels not reconstructed')
    g.report['implemented_stages'] = ['Base Texture colour', 'Base Normal', 'authored tint', 'overlay rust range', 'metallic switch', 'hole opacity', 'hole normal']
    extra = {'alpha': alpha} if alpha is not None else {}
    g.finish(color, rough, metal, normal, **extra)


def build_graphic_atlas(g, r):
    """Graphic atlas sticker/poster: AtlasPosition cell of a columns x rows atlas, NX normal, overlay wear."""
    s, sw, t = r['scalars'], r['switches'], r['textures']
    cols, rows = max(1, int(s.get('Number of columns', 1))), max(1, int(s.get('Number of rows', 1)))
    pos = int(s.get('AtlasPosition', 0)) % (cols * rows)
    uv = g.transform_uv(1 if sw.get('Use UV1') else 0, (1 / cols, 1 / rows), ((pos % cols) / cols, (pos // cols) / rows))
    flip = not t.get('Graphic Atlas') and t.get('Flipbook')
    if flip:
        # Monitor image sequence: Rows x Rows flipbook frozen on its first frame.
        n = max(1, int(s.get('Rows', 1) or 1))
        uv = g.transform_uv(0, (1 / n, 1 / n))
        _approx(g, 'Flipbook monitor: first frame of a Rows x Rows sheet, Tint x frame, emissive x Emissive; animation, scanlines and backlight not reconstructed')
    atlas = g.image(t.get('Graphic Atlas') or flip, uv, srgb=True, label='Graphic Atlas', required=False)
    color = atlas['color'] if atlas else (.5, .5, .5, 1)
    if flip:
        if 'Tint' in r['colors']: color = _tint(g, color, r['colors']['Tint'], 'Tint')
        g.report['implemented_stages'] = ['flipbook first frame', 'authored tint', 'emissive strength']
        g.finish(color, g.parameter('Roughness', s.get('Roughness', .3)), 0,
                 emission=g.vector('SCALE', color, scale=g.parameter('Emissive', s.get('Emissive', 1))))
        return
    color = g.vector('SCALE', color, scale=g.parameter('Atlas Brightness', s.get('Atlas Brightness', 1)))
    color = desaturate(g, color, 1 - float(s.get('Atlas Saturation', 1)))
    nx = g.image(t.get('NX'), g.transform_uv(0, s.get('Normal Tiling', 1)), srgb=False, label='NX', required=False) if sw.get('Use Normal (NX)', True) else None
    if sw.get('UseOverlay'):
        over = g.image(t.get('Overlay'), g.transform_uv(0, s.get('OverlayTiling', 1)), srgb=True, label='Overlay', required=False)
        if over:
            color = vlerp(g, color, g.cmul(color, desaturate(g, over['color'], s.get('OverlayDesaturation', 0)), 'Overlay'),
                          s.get('Overlay_BaseColor_Strength', 1))
    alpha = None
    if sw.get('Use Opacity') and atlas:
        alpha = _clip(g, r, atlas['a'])
    _approx(g, 'Graphic atlas: cell offset from AtlasPosition/columns/rows assumes row-major from UV origin; contrast, torn paper, dust, blend textures and prim-data weathering not reconstructed')
    g.report['implemented_stages'] = ['atlas cell UV', 'atlas brightness and saturation', 'NX normal', 'overlay colour', 'atlas opacity']
    extra = {'alpha': alpha} if alpha is not None else {}
    g.finish(color, .6, 0, g.decode_normal(nx, s.get('Normal Intensity', 1)) if nx else None, **extra)


def build_blockout(g, r):
    """Blockout preset: authored BaseColor/Color constant, optional DamageMask lerp to DamageColor."""
    s, sw, t = r['scalars'], r['switches'], r['textures']
    name = next((n for n in ('BaseColor', 'Color') if n in r['colors']), None)
    color = g.parameter(name, r['colors'][name], 'Color') if name else (.5, .5, .5, 1)
    normal = None
    if sw.get('EnableDamage'):
        mask = g.image(t.get('DamageMask'), g.uv(0), srgb=False, label='DamageMask', required=False)
        if mask:
            color = vlerp(g, color, g.parameter('DamageColor', _color(r, 'DamageColor', default=(.2, .2, .2, 1)), 'Color'), mask['r'])
        dn = g.image(t.get('DamageNormal'), g.uv(0), srgb=False, label='DamageNormal', required=False)
        normal = g.decode_normal(dn) if dn else None
    _approx(g, 'Blockout preset: constant authored colour with damage mask; fake cubemap, glare, rimlight, laser and pulsing emissive not reconstructed')
    g.report['implemented_stages'] = ['authored base colour', 'damage mask colour', 'damage normal']
    g.finish(color, s.get('Roughness', .5), s.get('Metallic', 0), normal)


def _build_snow_skirt(g, r):
    scalars = r['scalars']
    source_uv = g.uv(0)
    source_u, source_v, _ = g.split(source_uv)
    mode = g.parameter('UV Mode', scalars.get('UV Mode', 0))
    axis = float(scalars.get('Scale Axis', 0))
    mask_axis = math.floor(float(scalars.get('Mask on UVs', 0)))
    scales = g.obj.matrix_world.to_scale()
    axis_index = 1 if abs(axis - 1) <= .1 else (2 if axis >= 1 else 0)
    mask_coordinate = source_u if mask_axis == 1 else (source_v if mask_axis >= 1 else 1)
    mask = g.sub(1, g.math('GREATER_THAN', mask_coordinate, 1))
    scale = g.add(1, g.mul(mode, g.mul(mask, abs(scales[axis_index]) - 1)))
    uv = g.combine(g.mul(g.mul(source_u, scale), g.parameter('Tiling U', scalars.get('Tiling U', 1))),
                   g.mul(source_v, g.parameter('Tiling V', scalars.get('Tiling V', 1))), 0)
    sample = g.image(r['textures'].get('NX'), uv, srgb=False, label='NX')
    color = g.color((.854993, .846873, .887923, 1))
    if not sample:
        g.finish(color, .5, 0)
        return
    normal_x = g.sub(g.mul(sample['r'], 2), 1)
    normal_y = g.sub(g.mul(sample['g'], 2), 1)
    normal_z = g.math('SQRT', g.math('MAXIMUM', g.sub(1, g.add(g.mul(normal_x, normal_x), g.mul(normal_y, normal_y))), 0))
    strength = g.parameter('Normal Strength', scalars.get('Normal Strength', .5))
    normal = g.vector('NORMALIZE', g.combine(g.mul(normal_x, strength), g.mul(normal_y, strength),
                                            g.add(1, g.mul(g.sub(normal_z, 1), strength))))
    opacity = g.parameter('Opacity', scalars.get('Opacity', 1))
    opacity_min = g.parameter('Opacity Min', scalars.get('Opacity Min', .2))
    opacity_max = g.parameter('Opacity Max', scalars.get('Opacity Max', .9))
    coverage = g.clamp(g.div(g.sub(g.mul(sample['b'], opacity), opacity_min), g.clamp(g.sub(opacity_max, opacity_min))))
    fade_min = g.parameter('Fade Min', scalars.get('Fade Min', .2))
    fade_max = g.parameter('Fade Max', scalars.get('Fade Max', .9))
    fade = g.clamp(g.div(g.sub(g.sub(1, source_v), fade_min), g.clamp(g.sub(fade_max, fade_min))))
    breakup_scale = g.parameter('Breakup Scale', scalars.get('Breakup Scale', 1))
    breakup_range = g.parameter('BreakupRange', scalars.get('BreakupRange', 1))
    breakup = []
    for divisor in (2048, 512):
        breakup_uv = g.vector('SCALE', uv, scale=g.div(breakup_scale, divisor))
        breakup_sample = g.image(r['textures'].get('NX'), breakup_uv, srgb=False, label='NX breakup ' + str(divisor))
        breakup.append(g.clamp(g.mul(breakup_sample['b'], breakup_range)))
    analytic_mask = g.sub(g.add(fade, g.mul(coverage, fade)), g.mul(breakup[0], breakup[1]))
    _approx(g, 'SnowSkirt: cooked NX normal, fixed base colour and analytic opacity; temporal screen dither, view overrides and selection emissive are not reconstructed')
    g.report['implemented_stages'] = ['NX normal', 'object-scale UV mode', 'fixed snow base colour', 'blue opacity ramp', 'UV fade', 'two-scale blue breakup']
    g.finish(color, .5, 0, normal, alpha=_clip(g, r, analytic_mask))


def build_snow_pile_basic(g, r):
    """M_SnowPile_01: BaseTextrue is T_Snow_NRH (RG normal via MF_NormalReconstructZ, B roughness, A height).

    Height (MaskLow..Mask High) lerps ColorA to ColorB; alpha cutout only when Use SnowPatch Alpha is on
    (master default false)."""
    master = str(r.get('master', '')).rsplit('/', 1)[-1].split('.', 1)[0].casefold()
    if master == 'm_snowskirt_decal_01':
        return _build_snow_skirt(g, r)
    s, sw, t = r['scalars'], r['switches'], r['textures']
    tile = s.get('Tilling Base') or 1
    uv = g.transform_uv(0, tile) if tile != 1 else g.uv(0)
    tex = g.image(_pick(t, 'BaseTextrue', 'BaseTexture'), uv, srgb=False, label='BaseTextrue', required=False)
    a = g.parameter('ColorA', _color(r, 'ColorA', default=(.85, .88, .92, 1)), 'Color')
    b = g.parameter('ColorB', _color(r, 'ColorB', default=(.95, .97, 1, 1)), 'Color')
    # DR_APPROX_PLACEHOLDER: ColorA/ColorB mask taken from NRH alpha (height) remapped by MaskLow/Mask High
    color = vlerp(g, a, b, range_mask(g, tex['a'], s.get('MaskLow', 0), s.get('Mask High', 1))) if tex else a
    normal = g.decode_normal(tex, s.get('Normal Strength', 1)) if tex else None
    alpha = _clip(g, r, tex['a']) if tex and sw.get('Use SnowPatch Alpha', False) else None
    # DR_APPROX_PLACEHOLDER: roughness from BaseRougness scalar; NRH blue roughness modulation untraced
    rough = s.get('BaseRougness', s.get('Roughness', .7))
    _approx(g, 'SnowPile_01: NRH alpha as ColorA/ColorB height mask, BaseRougness constant; fake SSS and contact shadows not reconstructed')
    g.report['implemented_stages'] = ['NRH RG normal', 'ColorA/ColorB height mix', 'snow patch alpha']
    extra = {'alpha': alpha} if alpha is not None else {}
    g.finish(color, rough, 0, normal, **extra)


_NR_VPO_FABRIC_MASTERS = ('m_proppreset_nr+vpo+detail', 'm_nomadtarp_nr+vpo+detail')
_NR_VPO_FABRIC_CR = '/Game/Pioneer/MaterialLibrary/Textures/Architecture/T_Tarp_03_CR'


def build_nr_vpo_detail(g, r):
    """NR+VPO+Detail foliage/cloth prop: Base Color constant varied by Variation Mask, decal overlay, masked."""
    s, c, sw, t = r['scalars'], r['colors'], r['switches'], r['textures']
    uv = g.transform_uv(0, s.get('Tile', 1))
    base_ref = t.get('Base Texture')
    base = g.image(base_ref, uv, srgb=True, label='Base Texture', required=False) if base_ref else None
    color = base['color'] if base else g.parameter('Base Color', _color(r, 'Base Color', default=(.5, .5, .5, 1)), 'Color')
    variation_ref = t.get('Variation Mask')
    var = g.image(variation_ref, uv, srgb=False, label='Variation Mask', required=False) if variation_ref else None
    if var and 'Base Color Variation' in c:
        # Variation Mask Channel vector (master default R) selects the GRM channel.
        ch = c.get('Variation Mask Channel') or (1, 0, 0, 0)
        picks = [(w, var[k]) for w, k in zip(ch[:3], 'rgb') if w]
        if not picks:
            mask = var['r']
        elif len(picks) == 1 and picks[0][0] == 1:
            mask = picks[0][1]
        else:
            mask = None
            for w, sock in picks:
                term = g.mul(sock, w)
                mask = term if mask is None else g.add(mask, term)
        color = vlerp(g, color, g.parameter('Base Color Variation', c['Base Color Variation'], 'Color'), mask)
    master = str(r.get('master', '')).rsplit('/', 1)[-1].split('.', 1)[0].casefold()
    fabric = None
    if not base and master in _NR_VPO_FABRIC_MASTERS:
        # Master hard-codes T_Tarp_03_CR (ReferencedTextures, UV0) as fabric albedo under the authored colour.
        # DR_APPROX_PLACEHOLDER: exact master blend of fabric CR with Base Color (multiply assumed)
        fabric = g.image(_NR_VPO_FABRIC_CR, uv, srgb=True, label='Tarp CR', required=False)
        if fabric:
            color = g.cmul(color, fabric['color'], 'Fabric Tint')
    if sw.get('EnableDecal'):
        decal = g.image(t.get('Decal'), g.uv(1), srgb=True, label='Decal', required=False)
        if decal:
            color = vlerp(g, color, g.cmul(decal['color'], g.parameter('DecalColor', _color(r, 'DecalColor'), 'Color'), 'Decal'), decal['a'])
    folds_ref = t.get('Folds Normal Texture')
    folds = g.image(folds_ref, uv, srgb=False, label='Folds Normal Texture', required=False) if folds_ref else None
    alpha = _clip(g, r, base['a']) if base and 'Masked' in str(r.get('settings', {}).get('BlendMode', '')) else None
    _approx(g, 'NR+VPO+Detail: Variation Mask channel (by Variation Mask Channel) as base/variation mix, master fabric T_Tarp_03_CR multiplied into base colour (blend approximated), decal on UV1 with alpha; vertex position offset, large-scale variation, perma-snow, prop dust, damage and subsurface not reconstructed')
    g.report['implemented_stages'] = ['base colour', 'variation mask colour', 'decal overlay', 'folds normal', 'masked opacity']
    extra = {'alpha': alpha} if alpha is not None else {}
    g.finish(color, g.clamp(g.mul(.6, s.get('Rough Multiplier', 1))), 0,
             g.decode_normal(folds, s.get('Normal Strength', 1)) if folds else None, **extra)


def build_nao_decal(g, r):
    """NAO trimsheet shell decal: negative-V zones select V0/V1/V2 palettes, blue cutout, alpha AO."""
    s, c, t = r['scalars'], r['colors'], r['textures']
    uv = g.uv(0)
    sample = g.image(t.get('DecalTrimsheet'), uv, srgb=False, label='DecalTrimsheet')
    v = g.split(uv)[1]
    negative, lower, lowest = (g.math('LESS_THAN', v, x) for x in (0, -1, -2))
    v1, v2 = g.sub(negative, lower), g.sub(lower, lowest)
    def zones(kind, default):
        values = [g.parameter(f'V{i}_{kind}', s.get(f'V{i}_{kind}', default)) for i in range(3)]
        return lerp(g, lerp(g, values[0], values[1], v1), values[2], v2)
    cols = [g.parameter(f'V{i}_Color', c.get(f'V{i}_Color', (.5, .5, .5, 1)), 'Color') for i in range(3)]
    color = vlerp(g, vlerp(g, cols[0], cols[1], v1), cols[2], v2)
    color = g.vector('SCALE', color, scale=sample['a'])
    normal = g.decode_normal(sample)
    alpha = _clip(g, r, sample['b'])
    _approx(g, 'NAO shell decal without native compile: detail texture, parallax, damage hide and frost/snow layers not reconstructed')
    g.report['implemented_stages'] = ['NAO trimsheet normal', 'negative-V palette zones', 'blue cutout', 'alpha AO']
    g.finish(color, g.clamp(zones('Roughness', .5)), g.clamp(zones('Metalness', 0)), normal, alpha=alpha)


def build_canr_decal(g, r):
    """ColorAlpha + NormalRoughness decal sheet."""
    s, sw, t = r['scalars'], r['switches'], r['textures']
    uv = g.transform_uv(0, s.get('Tiling', s.get('UV Tiling', 1)))
    ca = g.image(t.get('ColorAlpa') or t.get('ColorAlpha'), uv, srgb=True, label='ColorAlpha')
    normal_name = 'NormalRoughness' if t.get('NormalRoughness') else 'NormalRoughnessHeight'
    nr = g.image(t.get(normal_name), uv, srgb=False, label=normal_name, required=False)
    color = ca['color']
    if sw.get('Color Tint'):
        name = next((k for k in r['colors'] if 'tint' in k.lower() or 'color' in k.lower()), 'Color Tint')
        color = _tint(g, color, _color(r, name), name)
    alpha = g.clamp(g.mul(ca['a'], g.parameter('Opacity', s.get('Opacity', 1))))
    _approx(g, 'CA/NR decal sheet: atlas, tileable UVs and edge fade not reconstructed')
    g.report['implemented_stages'] = ['CA colour and opacity', 'NR normal and roughness', 'authored tint']
    if nr and normal_name == 'NormalRoughnessHeight':
        _approx(g, 'NormalRoughnessHeight RG normal reconstructed; roughness and height channels not traced')
    g.finish(color, nr['b'] if nr and normal_name == 'NormalRoughness' else .5,
             s.get('Metalness', 0), g.decode_normal(nr) if nr else None, alpha=alpha)


# Later names: R7 named-texture roots (BaseFlatten, door CM, post-its, mesh terrain layer 0/1, backdrop, decals,
# moss, ground debris). approximate: first layer only for multi-layer masters.
CA_COLOR = ('CXA', 'CA', 'NCA', '1. CA', 'Color Base', 'DiffuseTexture', 'CM', 'PostItTex', 'Layer00_Color',
            'Layer01_Color', 'Base Color', 'Decal texture', 'MainDecalTexture', 'Base_Material_CR', 'Texture', 'col', 'WIndow Frame ')
# DR_APPROX_PLACEHOLDER: 'col' (M_base_A cross_col) and 'WIndow Frame '/'Window Frame NOM ' (M_FakeInterior_01) bound as plain CA colour/normal; FakeInterior room/prop-layer parallax atlas and glass layers not reconstructed
CA_NORMAL = ('NN', 'NRM', 'N', '1. NTR', 'Normal Base', 'NormalTexture', 'NRH', 'NormalMap', 'Layer00_Normal',
             'Layer01_Normal', 'Normal Map', 'Base_Material_NOH', 'NX', 'Window Frame NOM ')


def _blend_alpha(g, r, alpha):
    blend = str(r.get('settings', {}).get('BlendMode', ''))
    if 'Translucent' in blend or blend in {'2', '3', '4'}:
        return g.clamp(alpha)
    if 'Masked' in blend or blend == '1':
        return _clip(g, r, alpha)
    return None


def _build_masked_ca(g, r):
    """Colour+alpha (CA/CXA/NCA) + normal foliage, cable and window masters routed by root base."""
    s, t, c = r['scalars'], r['textures'], r['colors']
    uv = g.transform_uv(0, s.get('Tiling', s.get('Tile', s.get('1. Tiling', 1))))
    ca = g.image(_pick(t, *CA_COLOR), uv, srgb=True, label='CA', required=False)
    normal_ref = _pick(t, *CA_NORMAL)
    nrm = g.image(normal_ref, uv, srgb=False, label='Normal', required=False) if normal_ref else None
    color = ca['color'] if ca else (.5, .5, .5, 1)
    name = next((n for n in ('Tint', '1. Tint', 'Color') if n in c), None)
    if name: color = _tint(g, color, c[name], name)
    alpha = _blend_alpha(g, r, ca['a']) if ca else None
    _approx(g, 'CA masked surface: colour from CA rgb, alpha from CA last channel, tint from Tint/1. Tint/Color; wind WPO, top layer, snow and detail maps not reconstructed')
    g.report['implemented_stages'] = ['CA colour and alpha', 'normal map', 'authored tint', 'blend-mode opacity']
    rough = g.parameter('Roughness', s.get('Roughness', s.get('3. Roughness', s.get('rough', s.get('Window Frame Rough ', .6)))))
    extra = {'alpha': alpha} if alpha is not None else {}
    g.finish(color, rough, g.parameter('Metalness', s.get('Metalness', 0)), g.decode_normal(nrm) if nrm else None, **extra)


def build_masked_na(g, r):
    """BC colour + NA normal/alpha masked prop (chain-link style)."""
    s, t = r['scalars'], r['textures']
    if not (t.get('BC') or t.get('NA')) and _pick(t, *(CA_COLOR + CA_NORMAL)):
        return _build_masked_ca(g, r)
    bc = g.image(t.get('BC'), g.transform_uv(0, s.get('Tiling', 1)), srgb=True, label='BC')
    na = g.image(t.get('NA'), g.transform_uv(0, s.get('NA Tile', 1)), srgb=False, label='NA')
    color = g.vector('SCALE', bc['color'], scale=s.get('BC Brightness', 1))
    color = _tint(g, color, _color(r, 'Tint'), 'Tint')
    _approx(g, 'BC/NA masked prop: NA alpha read from the last channel, detail normal not reconstructed')
    g.report['implemented_stages'] = ['BC colour and brightness', 'NA normal', 'masked opacity', 'authored roughness and metalness']
    g.finish(color, g.parameter('Roughness', s.get('Roughness', .5)), g.parameter('Metalness', s.get('Metalness', 0)),
             g.decode_normal(na), alpha=_clip(g, r, na['a']))


def build_detail_ncr_trim(g, r):
    """Detail NCR (normal, detail colour, roughness) + Overlay colour architecture trim."""
    s, t = r['scalars'], r['textures']
    ncr = g.image(t.get('Detail NCR'), g.uv(0), srgb=False, label='Detail NCR', required=False)
    over = g.image(t.get('Overlay'), g.transform_uv(0, s.get('Overlay Tiling', 1)), srgb=True, label='Overlay', required=False)
    color = g.parameter('Color Tint', _color(r, 'Color Tint', default=(.5, .5, .5, 1)), 'Color')
    if ncr: color = g.vector('SCALE', color, scale=g.mul(ncr['b'], 2))
    if over: color = vlerp(g, color, g.cmul(color, over['color'], 'Overlay colour'), s.get('Overlay Strength', 1))
    rough = g.parameter('Roughness', s.get('Roughness', .5))
    if ncr: rough = g.clamp(g.mul(ncr['a'], g.mul(rough, 2)))
    _approx(g, 'Detail NCR architecture trim: triplanar overlay/wear, breakup and fresnel not reconstructed')
    g.report['implemented_stages'] = ['authored colour tint', 'NCR detail colour, normal and roughness', 'overlay colour']
    g.finish(color, rough, 0, g.decode_normal(ncr) if ncr else None)


_PIONEER = 'PioneerGame/Content/Pioneer/'
_LAUNCH_TOWER = _PIONEER + 'Environment/Architecture/SucessfulLaunchPadFacility/Modules/LaunchTower/Textures/'
# Fixed texture samples (master ReferencedTextures; no texture parameter binds them): base -> (colour, normal, opacity channel).
FIXED_MAPS = {
    'm_railguncables': (_LAUNCH_TOWER + 'T_Cables_BC', _LAUNCH_TOWER + 'T_Cables_N', None),
    'm_debrispile_07_groundplane': (_PIONEER + 'MaterialLibrary/Textures/Architecture/T_Interior_FloorDust_01_CR',
                                    _PIONEER + 'MaterialLibrary/Textures/Architecture/T_Interior_FloorDust_01_NOH', None),
    'm_wires_01': (_PIONEER + 'Environment/Props/_Generic/Wires_01/T_Wires_01_CR', _PIONEER + 'Environment/Props/_Generic/Wires_01/T_Wires_01_NOH', None),
    # M_SwampGrass_01 ReferencedTextures: T_Grass_01_C (alpha = opacity mask, mean .24 std .42) + T_Grass_01_N.
    'm_swampgrass_01': (_PIONEER + 'Environment/South/Swamp/Scatter/Grass_Swamp_01/Textures/T_Grass_01_C',
                        _PIONEER + 'Environment/South/Swamp/Scatter/Grass_Swamp_01/Textures/T_Grass_01_N', 'ca'),
    'm_mcp_floorlines_01_a': (None, _PIONEER + 'Environment/POI/MountainCompound/Lobby/Textures/MCP_FloorLines_MCP_FloorLines_NXX', 'b'),
}


def _build_fixed_maps(g, r, base):
    """Masters that sample fixed maps: colour texture (or authored Color vector), fixed normal, fixed-mask opacity."""
    s, c = r['scalars'], r['colors']
    color_ref, normal_ref, mask_channel = FIXED_MAPS[base]
    uv = g.transform_uv(0, s.get('Tiling', s.get('Tilling Base', 1)))
    col = g.image(color_ref, uv, srgb=True, label=color_ref.rsplit('/', 1)[-1], required=False) if color_ref else None
    nrm = g.image(normal_ref, uv, srgb=False, label=normal_ref.rsplit('/', 1)[-1], required=False)
    color = col['color'] if col else g.parameter('Color', _color(r, 'Color', default=(.5, .5, .5, 1)), 'Color')
    rough = g.parameter('Roughness', s.get('Roughness', .5))
    if col and 'Tint' in c:
        # DR_APPROX_PLACEHOLDER: Tint multiply / TintMask range (Wires) and Desaturation (DebrisPile) graph untraced
        mask = range_mask(g, col['r'], s.get('TintMask_Low', 0), s.get('TintMask_High', 1)) if 'TintMask_High' in s else 1
        color = vlerp(g, color, _tint(g, color, c['Tint'], 'Tint'), mask)
    if col and 'RoughnessHigh' in s:
        # DR_APPROX_PLACEHOLDER: CR alpha remapped into RoughnessLow..RoughnessHigh
        rough = lerp(g, g.parameter('RoughnessLow', s.get('RoughnessLow', .5)), g.parameter('RoughnessHigh', s['RoughnessHigh']), col['a'])
    elif col and color_ref.endswith('_CR'):
        rough = col['a']
    alpha = None
    if mask_channel == 'ca' and col:
        # DR_APPROX_PLACEHOLDER: opacity mask read from fixed colour-map alpha (SwampGrass; foliage SSS not reconstructed)
        alpha = _blend_alpha(g, r, col['a'])
    elif mask_channel and mask_channel != 'ca':
        # DR_APPROX_PLACEHOLDER: opacity mask read from fixed NXX blue (19% texels above the 0.8 clip)
        alpha = _blend_alpha(g, r, nrm[mask_channel])
    _approx(g, 'fixed-map master: colour/normal/opacity from the master\'s fixed texture samples; tint, wobble WPO, puddles, PDO and bump offset not reconstructed')
    g.report['implemented_stages'] = ['fixed colour texture or authored Color', 'fixed normal map', 'fixed-mask opacity', 'authored tint and roughness']
    extra = {'alpha': alpha} if alpha is not None else {}
    g.finish(color, rough, g.parameter('Metalness', s.get('Metalness', 0)), g.decode_normal(nrm, s.get('Normal Strength', 1)), **extra)


_TARP_CONSTRUCTION = _PIONEER + 'Environment/Props/_Generic/TarpConstruction_01/'


def _build_tarp_construction(g, r):
    """M_TarpConstruction_01: authored Color, fixed NXR base normal (Tile U), MI Wind Folds N at Large Wind Tiling,
    fixed T_TarpDetail_01_NX at Detail Tiling; the three normals are RNM-combined."""
    s, t = r['scalars'], r['textures']
    uv = g.transform_uv(0, s.get('Tile U', 1))
    nxr = g.image(_TARP_CONSTRUCTION + 'T_TarpConstruction_01_NXR', uv, srgb=False, label='T_TarpConstruction_01_NXR', required=False)
    folds = g.image(t.get('Wind Folds N'), g.transform_uv(0, s.get('Large Wind Tiling', .35)), srgb=False, label='Wind Folds N', required=False)
    detail = g.image(_TARP_CONSTRUCTION + 'T_TarpDetail_01_NX', g.transform_uv(0, s.get('Detail Tiling', 8)), srgb=False, label='T_TarpDetail_01_NX', required=False)
    normal = None
    for tex, strength in ((nxr, s.get('Base Normal Strenght', .5)), (folds, s.get('Normal Strenght', 1)), (detail, s.get('Detail Normal Intensity', 1))):
        if tex:
            n = g.decode_normal(tex, strength)
            normal = g.rnm(normal, n) if normal else n
    color = g.parameter('Color', _color(r, 'Color', default=(.38, .4, .44, 1)), 'Color')
    # DR_APPROX_PLACEHOLDER: roughness from NXR alpha; Roughness scalar (8.0) math untraced
    rough = g.clamp(nxr['a']) if nxr else .7
    # DR_APPROX_PLACEHOLDER: masked dither opacity left opaque
    _approx(g, 'TarpConstruction_01: NXR alpha roughness; wind WPO/direction, foliage SSS and dither opacity not reconstructed')
    g.report['implemented_stages'] = ['authored Color', 'NXR base normal', 'wind folds normal', 'detail normal RNM']
    g.finish(color, rough, 0, normal)


def build_parameterless(g, r):
    """Master with no texture parameters: a constant surface from its own scalars and vectors."""
    base = str(r.get('master', r.get('parent', ''))).rsplit('/', 1)[-1].split('.', 1)[0].casefold()
    if base == 'm_tarpconstruction_01':
        return _build_tarp_construction(g, r)
    if base in FIXED_MAPS:
        return _build_fixed_maps(g, r, base)
    s, c = r['scalars'], r['colors']
    name = next((k for k in c if 'water color' in k.lower()), None) or next((k for k in c if any(w in k.lower() for w in ('base', 'color', 'colour', 'tint', 'albedo'))), next(iter(c), None))
    if r.get('textures'):
        _approx(g, 'textures present on a constant-surface route (tarmac patches / water); texture layers not reconstructed')
    color = g.parameter(name, c[name], 'Color') if name else (.5, .5, .5, 1)
    def scalar(word, default):
        key = next((k for k in s if word in k.lower()), None)
        return g.parameter(key, s[key]) if key else default
    alpha = 1
    blend = str(r.get('settings', {}).get('BlendMode', ''))
    if 'Translucent' in blend or blend == '2':
        alpha = scalar('opacity', 1)
    g.report['warnings'].append('no_texture_master: master has no texture parameters; constant surface from authored scalars/vectors')
    g.report['implemented_stages'] = ['constant colour from authored vector', 'authored roughness/metalness scalars', 'blend mode opacity']
    g.finish(color, scalar('rough', .5), scalar('metal', 0), alpha=alpha)


def build_broken_glass(g, r):
    """Broken glass SDF: NDD normal + distance field cut out by the Cutout range, NC crack overlay.

    NC is a tangent normal (RG, B~1) with the crack overlay mask in alpha, not a colour map; its RGB
    multiplied into the albedo turned every pane violet-blue. Only its alpha drives the overlay."""
    s, t = r['scalars'], r['textures']
    uv = g.transform_uv(0, s.get('tile', 1))
    nc = g.image(t.get('NC'), uv, srgb=False, label='NC', required=False)
    ndd = g.image(t.get('NDD'), uv, srgb=False, label='NDD', required=False)
    color = (.8, .85, .9, 1)
    if nc:
        # DR_APPROX_PLACEHOLDER: crack overlay brightens toward frosted white; real overlay math unread.
        color = vlerp(g, color, (.95, .95, .95, 1), nc['a'])
    alpha, normal = 1, None
    if ndd:
        normal = g.decode_normal(ndd)
        sdf = ndd['b']
        low, high = g.parameter('CutoutLow', s.get('CutoutLow', 0)), g.parameter('CutoutHigh', s.get('CutoutHigh', 1))
        alpha = range_mask(g, sdf, low, high)
        if 'Masked' in str(r.get('settings', {}).get('BlendMode', 'Masked')): alpha = _clip(g, r, alpha)
        if 'EdgeLow' in s or 'EdgeHigh' in s:
            edge = range_mask(g, sdf, g.parameter('EdgeLow', s.get('EdgeLow', 0)), g.parameter('EdgeHigh', s.get('EdgeHigh', 1)))
            color = g.vector('SCALE', color, scale=g.add(.5, g.mul(edge, .5)))
    _approx(g, 'broken glass SDF: NDD blue read as the distance field, Cutout/Edge ranges as mask; unknown vector parameters are not applied as tint; random per-instance offset, refraction and emissive not reconstructed')
    g.report['implemented_stages'] = ['NDD normal', 'SDF cutout range', 'edge range shading', 'NC alpha crack overlay']
    g.finish(color, .05, 0, normal, alpha=alpha)


def build_concrete_trim(g, r):
    """UXR concrete trim: Trim sheet (normal RG, AO B, alpha A) + CH colour/height + detail normal."""
    s, c, sw, t = r['scalars'], r['colors'], r['switches'], r['textures']
    uv = g.uv(0)
    trim = g.image(t.get('Trim sheet'), uv, srgb=False, label='Trim sheet', required=False)
    ch = g.image(t.get('CH'), uv, srgb=True, label='CH', required=False)
    color = ch['color'] if ch else (.5, .5, .5, 1)
    if 'Color' in c: color = _tint(g, color, c['Color'], 'Color')
    elif 'Color' in s: color = g.vector('SCALE', color, scale=g.parameter('Color', s['Color']))
    normal = None
    if trim:
        normal = g.decode_normal(trim, s.get('Normal intensity', 1))
        color = g.vector('SCALE', color, scale=trim['b'])
    detail = g.image(t.get('Detail Normal'), g.transform_uv(0, s.get('Detail Normal Tile', 1)), srgb=False,
                     label='Detail Normal', required=False)
    if detail:
        dn = g.decode_normal(detail, s.get('Detail Normal Intensity', 1))
        normal = g.vector('NORMALIZE', g.vector('ADD', normal, dn)) if normal else dn
    alpha = None
    if trim and sw.get('Use Alpha mask'):
        alpha = _clip(g, r, g.clamp(g.mul(trim['a'], g.parameter('Global opacity', s.get('Global opacity', 1)))))
    _approx(g, 'UXR concrete trim: Trim sheet channel layout (normal RG, AO B, alpha A) and CH height unverified; dirt, virtual-texture blend and height parallax not reconstructed')
    g.report['implemented_stages'] = ['CH colour', 'authored colour tint', 'Trim sheet normal and AO', 'detail normal', 'alpha mask switch']
    extra = {'alpha': alpha} if alpha is not None else {}
    g.finish(color, s.get('Roughness', .7), 0, normal, **extra)


def build_engine_default(g, r):
    """Engine placeholder master (WorldGridMaterial/DefaultMaterial): neutral grey, info flag."""
    g.report['warnings'].append('engine_default: engine placeholder master ' + str(r.get('master', '')) + '; neutral grey surface')
    g.report['implemented_stages'] = ['neutral grey engine default']
    g.finish((.5, .5, .5, 1), .5, 0)
