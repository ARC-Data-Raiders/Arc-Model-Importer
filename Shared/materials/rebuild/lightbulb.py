"""M_MasterLight_Lightbulb_01: light bulbs from the compiled SM5 base pass (shader map 709BC325, PS 83C059E2).

Base colour sat(GlassColor), roughness 0 (the compiled shader writes only the view roughness bias), metallic 0, and the
Normal texture on UV0. Emissive is Lightbulb.rgb * LightColor * LightIntensity + GlassTexture.g * normalize(LightColor)
* LightIntensity, both textures on UV0 * Tiling. Optional Blender parallax and pulse are approximations.
Unlit "off" instances author LightIntensity 0 and so keep only the glass.
"""
import math
from .light_controls import pulse_intensity

DEFAULTS = {'LightIntensity': 100., 'Tiling': 1.}
COLORS = {'LightColor': (1., 1., 1., 0.), 'GlassColor': (.354167, .354167, .354167, 0.)}


def parallax_uv(g, uv, scalars, bulb):
    if not float(scalars.get('BumpOffset', 0.)):
        return uv
    depth = g.parameter('BumpOffset', scalars['BumpOffset'])
    geometry = g.node('ShaderNodeNewGeometry', 'Bulb view')
    tangent = g.node('ShaderNodeTangent', 'Bulb tangent', direction_type='UV_MAP')
    layers = getattr(getattr(g.obj, 'data', None), 'uv_layers', ())
    if layers:
        tangent.uv_map = layers[0].name
    normal = geometry.outputs['Normal']
    view = geometry.outputs['Incoming']
    bitangent = g.vector('CROSS_PRODUCT', normal, tangent.outputs['Tangent'])
    denominator = g.math('MAXIMUM', g.math('ABSOLUTE', g.vector('DOT_PRODUCT', view, normal)), .05)
    height = bulb['a'] if bulb else .5
    distance = g.div(g.mul(depth, g.sub(height, .5)), denominator)
    shift = g.combine(g.vector('DOT_PRODUCT', view, tangent.outputs['Tangent']), g.vector('DOT_PRODUCT', view, bitangent))
    g.report['warnings'].append('approximate: tangent-space bulb parallax uses texture alpha as height and a centred reference plane')
    return g.vector('ADD', uv, g.vector('SCALE', shift, scale=distance))


def build_lightbulb(g, r):
    s, c, t = r['scalars'], r['colors'], r['textures']
    intensity = g.parameter('LightIntensity', max(0., float(s.get('LightIntensity', DEFAULTS['LightIntensity']))))
    intensity = pulse_intensity(g, intensity, r)
    light = tuple(float(x) for x in c.get('LightColor', COLORS['LightColor'])[:3])
    length = math.sqrt(sum(x * x for x in light))
    unit = tuple(x / length for x in light) if length else (0., 0., 0.)
    glass = tuple(min(max(float(x), 0.), 1.) for x in c.get('GlassColor', COLORS['GlassColor'])[:3])
    uv = g.transform_uv(0, float(s.get('Tiling', DEFAULTS['Tiling'])))
    bulb = g.image(t.get('Lightbulb', ''), uv, label='Lightbulb emissive colour', required=False)
    if float(s.get('BumpOffset', 0.)):
        shifted = parallax_uv(g, uv, s, bulb)
        bulb = g.image(t.get('Lightbulb', ''), shifted, label='Parallax lightbulb colour', required=False)
    shell = g.image(t.get('GlassTexture', ''), uv, label='Glass texture (green: glow mask)', required=False)
    normal = g.image(t.get('Normal', ''), g.transform_uv(0, 1), srgb=False, label='Normal', required=False)
    emission = None
    if bulb:
        emission = g.vector('MULTIPLY', bulb['color'], g.vector('SCALE', light, scale=intensity))
    else:
        g.report['warnings'].append('Lightbulb texture missing; its emissive term is omitted')
    if shell:
        glow = g.vector('SCALE', g.vector('SCALE', unit, scale=intensity), scale=shell['g'])
        emission = g.vector('ADD', emission, glow) if emission is not None else glow
    base = glass
    if r.get('switches', {}).get('Use CR texture for BC') and t.get('CR'):
        # DR_APPROX_PLACEHOLDER: 'Use CR texture for BC' permutation not in the decompiled default shader map; assumed
        # base = CR.rgb (sRGB) on untiled UV0, GlassColor not multiplied, CR.a not used as roughness.
        cr = g.image(t['CR'], g.transform_uv(0, 1), label='CR', required=False)
        if cr:
            base = cr['color']
            g.report['warnings'].append("approximate: Use CR texture for BC assumed base = CR.rgb on UV0 (no GlassColor tint, roughness kept 0)")
    g.report['implemented_stages'] = ['glass base colour' if base is glass else 'CR base colour',
                                      'bulb and glass-mask emissive scaled by LightIntensity', 'normal texture']
    g.finish(base, 0., 0, g.decode_normal(normal) if normal else None, emission=emission)
