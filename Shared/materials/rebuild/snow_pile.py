"""M_FZT_SnowPiles_01: FrozenTrail snow piles from the compiled SM5 base pass (shader map 71DC0E78, PS D2E9AB60).

NOH (T_SnowPile_L_04_NH) on UV0 is the base normal. The fixed T_FZT_SnowDetail_01_NH (UV0 x 8 x Detail Tiling) and the
Blend Normal texture (half that frequency) chain onto it by unnormalised reoriented normal mapping, t dot(t, u) - u t.z.
The detail alpha sets roughness sat((A - RoughnessLow) / sat(RoughnessHihg - RoughnessLow)) and flattens the normal by
sat(2 (1 - A)). Colour is sat(lerp(2 sat(Blend A + 0.25) Snow, (0.85, 0.85, 0.9), min(top / misc, 1))), where
Snow = (0.85, 0.85, 0.9) x Miscoloration, top = sat((N.z - 0.2) / 0.3) on the vertex normal and
misc = sat((q.y - Low) / sat(High - Low)) on the unnormalised tangent normal q.
"""
from .glass_opaque import _ramp
from .masked import vlerp

SNOW_DETAIL = 'PioneerGame/Content/Pioneer/Environment/Nature/Locations/FrozenTrail/Textures/T_FZT_SnowDetail_01_NH'
SNOW_FRESH = 'PioneerGame/Content/Pioneer/Environment/Nature/Locations/FrozenTrail/Textures/T_FZT_SnowFresh_01_NH'
SNOW_WHITE = (.85, .85, .9)
DEFAULTS = {'Detail Tiling': 1.5, 'Detail Normal Strength': 1., 'Blend Normal Strength': 1.,
            'Miscoloration Range Low': -.05, 'Miscoloration Range High': .2, 'RoughnessLow': .2,
            'RoughnessHihg': 1., 'Miscoloration Roughness': 1.}
MISCOLORATION = (.885417, .832377, .79626, 1.)


def _components(g, tex):
    """Unit tangent normal from RG; the shader takes z from the unscaled XY."""
    x, y = g.sub(g.mul(tex['r'], 2), 1), g.sub(g.mul(tex['g'], 2), 1)
    return x, y, g.math('SQRT', g.math('MAXIMUM', g.sub(1, g.add(g.mul(x, x), g.mul(y, y))), 0))


def _operand(g, tex, strength):
    """The RNM u operand as compiled: (-s x, -s y, s (z - 1) + 1)."""
    x, y, z = _components(g, tex)
    negative = g.mul(strength, -1)
    return g.combine(g.mul(x, negative), g.mul(y, negative), g.add(g.mul(g.sub(z, 1), strength), 1))


def _rnm_raw(g, t, u):
    return g.vector('SUBTRACT', g.vector('SCALE', t, scale=g.vector('DOT_PRODUCT', t, u)),
                    g.vector('SCALE', u, scale=g.split(t)[2]))


def build_snow_pile(g, r):
    s, c, t = r['scalars'], r['colors'], r['textures']
    def S(k): return g.parameter(k, s.get(k, DEFAULTS[k]))
    def v(k): return float(s.get(k, DEFAULTS[k]))
    tiling = v('Detail Tiling')
    noh = g.image(t.get('NOH', ''), g.transform_uv(0, 1), srgb=False, label='NOH normal RG, height A', required=False)
    detail = g.image(SNOW_DETAIL, g.transform_uv(0, 8 * tiling), srgb=False,
                     label='Fixed snow detail normal RG, roughness and flatten mask A', required=False)
    blend = g.image(t.get('Blend Normal') or SNOW_FRESH, g.transform_uv(0, 4 * tiling), srgb=False,
                    label='Blend normal RG, colour variation A', required=False)
    flat = g.combine(0, 0, 1)
    if noh:
        x, y, z = _components(g, noh)
        base = g.combine(x, y, g.add(z, 1))
    else:
        base = g.combine(0, 0, 2)
    # A missing detail texture is the flat operand (0, 0, 1).
    p = _rnm_raw(g, base, _operand(g, detail, S('Detail Normal Strength')) if detail else flat)
    q = _rnm_raw(g, g.vector('ADD', p, (0, 0, 1)), _operand(g, blend, S('Blend Normal Strength')) if blend else flat)
    misc = _ramp(g, g.split(q)[1], v('Miscoloration Range Low'), v('Miscoloration Range High'))
    if detail:
        q = vlerp(g, q, flat, g.clamp(g.mul(g.sub(1, detail['a']), 2)))
    normal = g.normal_to_world(g.vector('NORMALIZE', q))
    geometry = g.node('ShaderNodeNewGeometry', 'Vertex normal for the top mask')
    top = g.clamp(g.div(g.sub(g.split(geometry.outputs['Normal'])[2], .2), .3))
    variation = blend['a'] if blend else .5
    if not blend:
        g.report['warnings'].append('Blend Normal texture missing; mid colour variation used')
    snow = g.vector('MULTIPLY', g.parameter('Miscoloration', c.get('Miscoloration', MISCOLORATION), 'Color'), SNOW_WHITE)
    lifted = g.vector('SCALE', snow, scale=g.mul(g.clamp(g.add(variation, .25)), 2))
    # min(top / misc, 1): a zero misc divides to infinity (or NaN), which the shader's min returns as 1.
    whiten = g.math('MAXIMUM', g.math('MINIMUM', g.div(top, g.math('MAXIMUM', misc, 1e-6)), 1),
                    g.math('LESS_THAN', misc, 1e-6))
    color = g.vector('MINIMUM', g.vector('MAXIMUM', vlerp(g, lifted, SNOW_WHITE, whiten), (0, 0, 0)), (1, 1, 1))
    rough = _ramp(g, detail['a'], v('RoughnessLow'), v('RoughnessHihg')) if detail else .375
    if not detail:
        g.report['warnings'].append('Snow detail texture missing; mid roughness used: ' + SNOW_DETAIL)
    rough = g.clamp(g.mix(rough, g.mul(rough, S('Miscoloration Roughness')), g.sub(1, top)))
    g.report['implemented_stages'] = ['compiled snow colour (top mask, miscoloration, colour variation)',
                                      'base, detail and blend normals (unnormalised RNM chain)', 'detail-alpha roughness']
    g.report['warnings'].append('Distance normal flattening, world-up normal bias, pixel depth offset and object-scale '
                                'detail tiling are not reproduced')
    g.finish(color, rough, 0, normal)
