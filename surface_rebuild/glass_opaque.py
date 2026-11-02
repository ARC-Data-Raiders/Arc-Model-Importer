"""M_GlassOpaque_NX_01: opaque two-sided-foliage glass (window panes, resin) from its compiled SM5 permutations.

Pixel shaders read from the shader maps: 3904E4B1 (master: solid Color with the two-sided interior blend, NXX on UV1,
top-down mask), 13377B85 (MI_Resin_01: CR texture), 7806C5C8 (MI_Res_GlassWindowRoof_01: Color1/Color2 by C red,
C red roughness, flat normal, UV0) and 00C822AC (MI_WinExt_03_Glass_A: CR texture on UV0, no top-down mask).

Every permutation shares: dirt d = sat(sat((NXX.a - Low) / sat(High - Low)) * Dirt Amount); colour darkened by the
view fresnel F = (1 - N.V)^ExpIn * (1 - Base) + Base as lerp(c, c / 1.5, F); dirt colour by d; metallic
sat((1 - d - Metallic Low) / sat(Metallic High - Metallic Low)); roughness sat(lerp(R * Glass Roughness, dirt B, d));
specular sat(Specular); normal lerp(RNM(base, dirt detail), base, min(1.5 (1 - d), 1)).
"""
from .masked import vlerp

DIRT_NORMAL = 'PioneerGame/Content/Pioneer/MaterialLibrary/Textures/Props/Detail/T_Dirt_01_NXX'
DEFAULTS = {'Tiling': 1., 'Dirt Normal Tiling': 2., 'Dirt Normal Strength': 1., 'Dirt Mask Low': 0., 'Dirt Mask High': 1.,
            'Dirt Amount': 4., 'ShadowMask': 0., 'Fresnel ExpIn': .4, 'FresnelBaseReflect': 3., 'EmissiveAmount': 0.,
            'Interior Metallic': .4, 'Metallic Low': .6, 'Metallic High': 1.2, 'Specular': 2., 'Glass Roughness': 0.}
COLORS = {'Color': (.312929, .380208, .276024, 1.), 'Color1': (.312929, .380208, .276024, 1.),
          'Color2': (.312929, .380208, .276024, 1.), 'Dirt Color': (.161458, .147978, .133708, 1.)}
SWITCHES = {'Use solid Color instead of CR': True, 'Use Texture Coord 1': True, 'Top Down WS Mask': True,
            'Use Single Channel As Mask': False, 'Use Roughness Channel': False, 'Flat Normal Override': False}


def _ramp(g, value, low, high):
    """sat((value - low) * rcp(sat(high - low))); a closed range is the shader's infinite reciprocal, a step."""
    width = min(max(float(high) - float(low), 0.), 1.)
    if width <= 0.:
        return g.math('GREATER_THAN', value, float(low))
    return g.clamp(g.div(g.sub(value, float(low)), width))


def _unit_normal(g, tex, strength=1.):
    """Shader order: z from the unscaled XY, then XY scaled by strength."""
    x, y = g.sub(g.mul(tex['r'], 2), 1), g.sub(g.mul(tex['g'], 2), 1)
    z = g.math('SQRT', g.math('MAXIMUM', g.sub(1, g.add(g.mul(x, x), g.mul(y, y))), 0))
    return g.combine(g.mul(x, strength), g.mul(y, strength), z)


def build_glass_opaque(g, r):
    s, c, t, sw = r['scalars'], r['colors'], r['textures'], r['switches']
    def S(k): return g.parameter(k, s.get(k, DEFAULTS[k]))
    def C(k): return g.parameter(k, c.get(k, COLORS[k]), 'Color')
    def on(k): return bool(sw.get(k, SWITCHES[k]))
    def v(k): return float(s.get(k, DEFAULTS[k]))
    solid = on('Use solid Color instead of CR')
    # TwoSidedSign is a constant +1 in one-sided permutations (MI_HighwayAddon_Window_01 overrides TwoSided off: PS
    # BC8D5764 has no interior blend and no Interior Metallic), so only a two-sided material shades its back faces.
    settings = r.get('settings', {})
    interior = solid and bool(settings.get('TwoSided', settings.get('bTwoSided', settings.get('two_sided', False))))
    nxx = g.image(t.get('NXX', ''), g.transform_uv(1 if on('Use Texture Coord 1') else 0, v('Tiling')), srgb=False,
                  label='NXX normal RG, roughness B, dirt mask A', required=False)
    dirt_tex = g.image(DIRT_NORMAL, g.transform_uv(0, v('Dirt Normal Tiling')), srgb=False,
                       label='Fixed dirt normal RG, roughness B', required=False)
    mask = None if solid else g.image(t.get('C', ''), g.transform_uv(0, v('Tiling')),
                                      srgb=False if on('Use Single Channel As Mask') else None,
                                      label='C colour or single-channel mask', required=False)
    d = 0.
    if nxx:
        d = g.clamp(g.mul(_ramp(g, nxx['a'], v('Dirt Mask Low'), v('Dirt Mask High')), S('Dirt Amount')))
    base = None if on('Flat Normal Override') or not nxx else _unit_normal(g, nxx)
    flat = g.combine(0, 0, 1)
    normal = base or flat
    if dirt_tex:
        detail = g.rnm(normal, _unit_normal(g, dirt_tex, S('Dirt Normal Strength')))
        normal = g.vector('NORMALIZE', vlerp(g, detail, normal, g.math('MINIMUM', g.mul(g.sub(1, d), 1.5), 1)))
    world = g.normal_to_world(normal)
    geometry = g.node('ShaderNodeNewGeometry', 'View, facing and up')
    facing = g.math('MAXIMUM', g.vector('DOT_PRODUCT', world, geometry.outputs['Incoming']), 0)
    grazing = g.math('POWER', g.math('MAXIMUM', g.sub(1, facing), 1e-4), S('Fresnel ExpIn'))
    reflect = S('FresnelBaseReflect')
    fresnel = g.add(g.mul(grazing, g.sub(1, reflect)), reflect)
    if solid:
        color = C('Color')
    elif on('Use Single Channel As Mask') and mask:
        color = vlerp(g, C('Color1'), C('Color2'), mask['r'])
    elif mask:
        color = mask['color']
    else:
        color = COLORS['Color']
        g.report['warnings'].append('Opaque glass C texture missing; master Color used')
    # Unclamped lerps: the fresnel factor reaches FresnelBaseReflect (> 1) and the face sign is -1 on back faces.
    color = vlerp(g, color, g.vector('SCALE', color, scale=1 / 1.5), fresnel)
    sign = g.sub(1, g.mul(geometry.outputs['Backfacing'], 2)) if interior else 1.
    shaded = color
    if interior:
        # Two-sided solid-colour permutation: TwoSidedSign (+1 front, -1 back) blends towards Color / 8 inside.
        shaded = vlerp(g, g.vector('SCALE', C('Color'), scale=1 / 8), color, sign)
    if on('Top Down WS Mask'):
        up = g.math('MAXIMUM', g.split(geometry.outputs['Normal'])[2], .2)
        shaded = vlerp(g, shaded, g.vector('SCALE', color, scale=2), up)
    color = vlerp(g, shaded, C('Dirt Color'), d)
    emission = None
    if v('EmissiveAmount'):
        lit = g.math('MAXIMUM', g.sub(1, g.mul(d, S('ShadowMask'))), 0)
        emission = g.vector('SCALE', color, scale=g.mul(lit, S('EmissiveAmount')))
    metallic = _ramp(g, g.sub(1, d), v('Metallic Low'), v('Metallic High'))
    if interior:
        metallic = g.clamp(g.mix(S('Interior Metallic'), metallic, sign))
    channel = nxx['b'] if nxx else 0.
    if on('Use Roughness Channel'):
        if mask:
            channel = mask['r']
        else:
            g.report['warnings'].append('Opaque glass roughness channel needs the C texture; NXX blue used')
    rough = g.mul(channel, S('Glass Roughness'))
    if dirt_tex:
        rough = g.mix(rough, dirt_tex['b'], d)
    g.report['implemented_stages'] = ['compiled opaque-glass permutation switches', 'NXX dirt mask, normal and roughness',
                                      'fixed dirt detail normal (RNM)', 'view fresnel darkening',
                                      'top-down and interior blends', 'dirt colour, metallic ramp and emissive']
    g.report['warnings'].append('TwoSidedFoliage back-light transmission (subsurface colour) is not reproduced; pixel depth offset omitted')
    g.finish(g.vector('MINIMUM', g.vector('MAXIMUM', color, (0, 0, 0)), (1, 1, 1)), g.clamp(rough), metallic, world,
             specular=min(max(v('Specular'), 0.), 1.), emission=emission)
