"""M_FX_Cloud_LightVector: sky cloud cards lit from eight baked light directions.

Reconstructed from the SM5 translucent base-pass PS of MI_Cloud_Frozenday_01's static
permutation (shader map A5FCE6E7, build 26159202) and its preshader UB layout. The
"Depth Texture" only scales the soft scene-depth fade; it is never colour.
"""
import math

# M_FX_Cloud_LightVector CachedExpressionData defaults; instances override them.
SCALARS = {'Ambient Scatter Curve': 1.0, 'Ambient Scatter Offset': 0.0, 'Ambient Scatter Intensity Start': .05,
           'Directional Scatter Intensity': .1, 'Directional Scatter Curve': 1.0, 'Directional Scatter Offset': .002,
           'Alpha Curve': 1.5, 'ScatterBaseColor': 0.0, 'EmissiveMultiplier': 1.0}
COLORS = {'BaseColor': (1., 1., 1., 0.), 'Ambient Scatter': (.4, .67746, 1., 1.),
          'Directional Scatter': (1., .833333, .75, 1.), 'Light World Position': (0., 0., 5000., 1.)}
# MF_FX_EvaluateMDCInternal: one channel per cube-corner light direction in
# (tangent, texture-up, normal) space, i.e. after the shader's (1, -1, 1) flip of UE's frame.
CORNERS = (('MDC1 Texture', 'r', (-1, -1, -1)), ('MDC1 Texture', 'g', (-1, -1, 1)),
           ('MDC1 Texture', 'b', (-1, 1, -1)), ('MDC1 Texture', 'a', (-1, 1, 1)),
           ('MDC2 Texture', 'r', (1, -1, -1)), ('MDC2 Texture', 'g', (1, -1, 1)),
           ('MDC2 Texture', 'b', (1, 1, -1)), ('MDC2 Texture', 'a', (1, 1, 1)))
SIGN_ATTRIBUTE = 'arc_bitangent_sign'


def _bitangent_sign(g, uv_name):
    """MikkTSpace UV0 bitangent sign: a constant on cloud cards, per corner on mirrored islands."""
    mesh = getattr(g.obj, 'data', None)
    if mesh is None or not uv_name:
        g.report['warnings'].append('Cloud light vector: no UV0 tangent frame; bitangent sign assumed +1')
        return 1.0
    try:
        mesh.calc_tangents(uvmap=uv_name)
        signs = [loop.bitangent_sign for loop in mesh.loops]
        mesh.free_tangents()
    except RuntimeError as error:
        g.report['warnings'].append(f'Cloud light vector: tangents unavailable ({error}); bitangent sign assumed +1')
        return 1.0
    if len(set(signs)) <= 1:
        return float(signs[0]) if signs else 1.0
    attribute = mesh.attributes.get(SIGN_ATTRIBUTE) or mesh.attributes.new(SIGN_ATTRIBUTE, 'FLOAT', 'CORNER')
    attribute.data.foreach_set('value', signs)
    return g.node('ShaderNodeAttribute', 'UV0 bitangent sign', attribute_name=SIGN_ATTRIBUTE).outputs['Fac']


def build_fx_cloud(g, r):
    s, c, t, sw = r['scalars'], r['colors'], r['textures'], r['switches']
    def S(name): return g.parameter(name, s.get(name, SCALARS[name]))
    def C(name): return g.parameter(name, c.get(name, COLORS[name]), 'Color')
    g.report['shader_profile'] = 'fx_cloud_lightvector_v1'
    if sw.get('Use Tangent-Space Light Direction') or sw.get('Use Directional Light Rotation'):
        g.report['warnings'].append('Cloud light-direction permutation not decoded; Light World Position path used')
    layers = getattr(getattr(g.obj, 'data', None), 'uv_layers', ())
    uv_name = layers[0].name if layers else ''
    final = g.image(t.get('Final Color Texture', ''), g.uv(0), label='UV0 · final colour and opacity', required=False)
    mdc = {name: g.image(t.get(name, ''), g.uv(0), label=f'UV0 · {name[:4]} light directions', required=False)
           for name in ('MDC1 Texture', 'MDC2 Texture')}
    if final is None:
        g.report['warnings'].append('Cloud Final Color Texture missing; flat base colour used')
        g.finish(c.get('BaseColor', COLORS['BaseColor']), 1, 0, specular=0)
        return
    for sample in (final, *mdc.values()):
        if sample is not None:
            # MDC alpha is a light direction and Final Color alpha the opacity; neither is RGB coverage.
            sample['node'].image.alpha_mode = 'CHANNEL_PACKED'
    with g.block('Light vector in UV0 tangent space') as b:
        geometry = b.node('ShaderNodeNewGeometry', 'Position and normal')
        tangent = b.node('ShaderNodeTangent', 'UV0 tangent', direction_type='UV_MAP', uv_map=uv_name).outputs['Tangent']
        # The PS reads the interpolated vertex basis with no TwoSidedSign, so undo the backface flip.
        normal = b.vector('SCALE', geometry.outputs['Normal'], scale=b.sub(1, b.mul(geometry.outputs['Backfacing'], 2)))
        bitangent = b.vector('SCALE', b.vector('CROSS_PRODUCT', normal, tangent), scale=_bitangent_sign(b, uv_name))
        x, y, z = c.get('Light World Position', COLORS['Light World Position'])[:3]
        light = b.parameter('Light World Position (Blender metres)', (x * .01, -y * .01, z * .01), 'Vector')
        to_light = b.vector('SUBTRACT', light, geometry.outputs['Position'])
        local = b.vector('NORMALIZE', b.combine(*(b.vector('DOT_PRODUCT', axis, to_light) for axis in (tangent, bitangent, normal))))
    with g.block('Directional scatter from eight baked light directions') as b:
        total = None
        for name, channel, corner in CORNERS:
            if mdc[name] is None:
                continue
            facing = b.vector('DOT_PRODUCT', local, tuple(v / math.sqrt(3) for v in corner))
            term = b.mul(mdc[name][channel], b.add(b.mul(facing, .75), .25))
            total = term if total is None else b.add(total, term)
        if total is None:
            g.report['warnings'].append('Cloud MDC light-direction textures missing; directional scatter is zero')
            total = 0.0
        # The eight weights 0.75*dot(L, corner)+0.25 always sum to 2.
        shaped = b.math('POWER', b.clamp(b.mul(total, .5)), S('Directional Scatter Curve'))
        offset = S('Directional Scatter Offset')
        directional = b.vector('ADD', b.vector('SCALE', C('Directional Scatter'), scale=b.mul(shaped, S('Directional Scatter Intensity'))),
                               b.combine(offset, offset, offset))
    with g.block('Ambient scatter, emissive and opacity') as b:
        gain, curve = S('ScatterBaseColor'), S('Ambient Scatter Curve')
        ambient = b.combine(*(b.math('POWER', b.mul(final[channel], gain), curve) for channel in 'rgb'))
        offset = S('Ambient Scatter Offset')
        ambient = b.vector('ADD', b.vector('SCALE', b.cmul(ambient, C('Ambient Scatter')), scale=S('Ambient Scatter Intensity Start')),
                           b.combine(offset, offset, offset))
        emissive = b.vector('SCALE', b.vector('ADD', directional, ambient), scale=S('EmissiveMultiplier'))
        emissive = b.vector('MAXIMUM', emissive, (0., 0., 0.))
        alpha = b.math('MINIMUM', b.math('POWER', final['a'], S('Alpha Curve')), 1)
        color = b.vector('MINIMUM', b.vector('MAXIMUM', C('BaseColor'), (0., 0., 0.)), (1., 1., 1.))
    g.report['implemented_stages'] = ['eight-direction MDC light vector from Light World Position', 'directional and ambient scatter',
                                      'emissive multiplier', 'Final Color alpha curve', 'translucency-volume diffuse BaseColor']
    g.report['warnings'].append('Depth Blend soft intersection and cloud fogging need scene depth and fog; omitted')
    # Translucency volume lighting has no specular lobe.
    g.finish(color, 1, 0, specular=0, emission=emissive, alpha=alpha)
