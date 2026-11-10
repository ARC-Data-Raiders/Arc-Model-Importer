"""Scout-drone light beam and fog sheet meshes from their compiled SM5 translucent base passes.

Both masters are Unlit BLEND_Translucent (grey transmittance): the pixel is emissive * alpha over the scene, so the
graph is a black, specular-free surface with emission and alpha.

M_FX_Lightbeam_Master_01 (shader map BAEF12EA, PS FEF1C128):
  emissive white * (1 + Scan Intensity * (line + ring)) * Overall Intensity, line = max((1 - p)^Scan Line Exp,
  band(p)) at p = frac(U), ring = max(band(q), q^Scan Ring Exp) at q = frac(V * Scan Ring Amount), where band is the
  smoothstep of sat((|.5 - x| - (.5 - Scan Thickness)) / Scan Thickness);
  alpha sat(Opacity * Falloff_02.r(Rt.xy / m + .5) * FalloffTexture.g(Rt.x / m + .5, V)^FalloffExponent
  * lerp(1, dust, TextureContrast) * sat(depth * .0025 / NearFadeDistance) * sat(10 (1 - V))), with Rt the view
  reflection vector in tangent space, m = sqrt(.8 (Rt.z + 8)) and dust MultiplyTexture1.r blended across the U seam.
M_FX_FogSheet_Master_01 (shader map D93C73C8, PS 6821CB6E):
  emissive white; alpha sat(sat((depth - StartOffset) * .0005 / Distance) * |N.V|^FalloffExponent
  * FalloffTexture.g * Opacity)^2.

Time is frozen: pans sit at 0 and the scan ring at its resting phase. Scene-depth soft intersections, the
eye-adaptation normalisation and Niagara particle colour have no Blender input and are reported.
"""
UTILS = 'PioneerGame/Content/Pioneer/Effects/Textures/Utils/'
BEAM_FALLOFF = UTILS + 'T_FX_LightBeam_Falloff_01'
BEAM_RADIAL = UTILS + 'T_FX_LightBeam_Falloff_02'
BEAM_DUST = UTILS + 'T_FX_DustPanner_01'
FOG_FALLOFF = UTILS + 'T_FX_FogSheet_Falloff_01'
BEAM_DEFAULTS = {'Scan Line Exp': 8., 'Scan Thickness': .01, 'Scan Intensity': 2., 'Scan Ring Amount': 0.,
                 'Scan Ring Exp': 4., 'Overall Intensity': 1., 'TextureScaleX': 1., 'TextureScaleY': 1.,
                 'TextureContrast': .8, 'FalloffExponent': 1., 'Opacity': .25, 'NearFadeDistance': 1.}
FOG_DEFAULTS = {'StartOffset': 0., 'Distance': 1., 'FalloffExponent': 3., 'Opacity': .25}
UNREPRODUCED = 'Scene-depth soft intersection, eye-adaptation normalisation and particle colour are not reproduced'


def _depth_cm(g):
    """Linear view depth in Unreal centimetres (the shader's SvPosition depth)."""
    scale = float(g.obj.get('arc_ue_unit_scale', g.obj.data.get('arc_uemodel_scale', .01)))
    return g.div(g.node('ShaderNodeCameraData', 'Camera depth').outputs['View Z Depth'], scale)


def _band(g, x, thickness):
    t = max(float(thickness), 1e-6)
    f = g.clamp(g.div(g.sub(g.math('ABSOLUTE', g.sub(.5, x)), .5 - t), t))
    return g.mul(g.mul(f, f), g.sub(3, g.mul(f, 2)))


def _finish_unlit(g, emission, alpha):
    g.finish((0., 0., 0.), 1., 0, specular=0., emission=emission, alpha=alpha)


def build_fx_lightbeam(g, r):
    s, t = {**BEAM_DEFAULTS, **r['scalars']}, r['textures']
    g.report['shader_profile'] = 'fx_lightbeam_sm5_v1'
    u, v, _ = g.split(g.uv(0))
    thickness = s['Scan Thickness']
    phase = g.math('FRACT', u)
    line = g.math('MAXIMUM', g.math('POWER', g.sub(1, phase), s['Scan Line Exp']), _band(g, phase, thickness))
    ring_phase = g.math('FRACT', g.mul(v, s['Scan Ring Amount']))
    ring = g.math('MAXIMUM', _band(g, ring_phase, thickness), g.math('POWER', ring_phase, s['Scan Ring Exp']))
    glow = g.mul(g.add(g.mul(g.add(line, ring), s['Scan Intensity']), 1), s['Overall Intensity'])
    emission = g.combine(glow, glow, glow)

    geometry = g.node('ShaderNodeNewGeometry', 'View and normal')
    view, normal = geometry.outputs['Incoming'], geometry.outputs['Normal']
    layers = getattr(getattr(g.obj, 'data', None), 'uv_layers', ())
    attrs = {'direction_type': 'UV_MAP', **({'uv_map': layers[0].name} if layers else {})}
    tangent = g.vector('NORMALIZE', g.node('ShaderNodeTangent', 'UV0 tangent', **attrs).outputs['Tangent'])
    bitangent = g.vector('CROSS_PRODUCT', normal, tangent)
    reflect = g.vector('SUBTRACT', g.vector('SCALE', normal, scale=g.mul(g.vector('DOT_PRODUCT', normal, view), 2)), view)
    rx, ry, rz = (g.vector('DOT_PRODUCT', reflect, axis) for axis in (tangent, bitangent, normal))
    m = g.math('SQRT', g.mul(g.add(rz, 8), .8))
    mu, mv = g.add(g.div(rx, m), .5), g.add(g.div(ry, m), .5)
    falloff = g.image(t.get('FalloffTexture') or BEAM_FALLOFF, g.combine(mu, v), srgb=False,
                      label='Beam falloff (green)', required=False)
    radial = g.image(BEAM_RADIAL, g.combine(mu, mv), srgb=False, label='Reflection radial mask (red)', required=False)
    alpha = s['Opacity']
    if falloff:
        alpha = g.mul(alpha, g.math('POWER', falloff['g'], s['FalloffExponent']))
    if radial:
        alpha = g.mul(alpha, radial['r'])
    scale = (s['TextureScaleX'], s['TextureScaleY'], 1.)
    dust_ref = t.get('MultiplyTexture1') or BEAM_DUST
    first = g.image(dust_ref, g.vector('MULTIPLY', g.combine(u, v), scale), srgb=False, label='Dust', required=False)
    second = g.image(dust_ref, g.vector('MULTIPLY', g.combine(g.math('FRACT', g.add(u, .5)), g.math('FRACT', v)), scale),
                     srgb=False, label='Dust across the U seam', required=False)
    if first and second:
        dust = g.mix(first['r'], second['r'], g.math('ABSOLUTE', g.mul(g.sub(u, .5), 2)))
        alpha = g.mul(alpha, g.add(g.mul(g.sub(dust, 1), s['TextureContrast']), 1))
    near = g.clamp(g.mul(_depth_cm(g), .0025 / max(s['NearFadeDistance'], 1e-6)))
    alpha = g.clamp(g.mul(g.mul(alpha, near), g.clamp(g.mul(g.sub(1, v), 10))))
    missing = [name for name, sample in (('FalloffTexture', falloff), ('Falloff_02', radial), ('MultiplyTexture1', first))
               if not sample]
    if missing:
        g.report['warnings'].append('Light beam textures missing; their alpha terms are omitted: ' + ', '.join(missing))
    g.report['implemented_stages'] = ['scan line and ring emissive', 'tangent-space reflection falloff opacity',
                                      'dust seam blend', 'near and far-end fades']
    g.report['warnings'].append(UNREPRODUCED)
    _finish_unlit(g, emission, alpha)


def build_fx_fogsheet(g, r):
    s, t = {**FOG_DEFAULTS, **r['scalars']}, r['textures']
    g.report['shader_profile'] = 'fx_fogsheet_sm5_v1'
    geometry = g.node('ShaderNodeNewGeometry', 'View and normal')
    facing = g.math('ABSOLUTE', g.vector('DOT_PRODUCT', geometry.outputs['Incoming'], geometry.outputs['Normal']))
    distance = g.clamp(g.mul(g.sub(_depth_cm(g), s['StartOffset']), .0005 / s['Distance'] if s['Distance'] else 1e30))
    alpha = g.mul(distance, g.math('POWER', facing, s['FalloffExponent']))
    falloff = g.image(t.get('FalloffTexture') or FOG_FALLOFF, g.uv(0), srgb=False, label='Fog falloff (green)',
                      required=False)
    if falloff:
        alpha = g.mul(alpha, falloff['g'])
    else:
        g.report['warnings'].append('Fog sheet falloff texture missing; its alpha term is omitted')
    alpha = g.clamp(g.mul(alpha, s['Opacity']))
    g.report['implemented_stages'] = ['distance fade-in', 'facing falloff', 'falloff texture', 'squared opacity']
    g.report['warnings'].append(UNREPRODUCED)
    _finish_unlit(g, (1., 1., 1.), g.mul(alpha, alpha))
