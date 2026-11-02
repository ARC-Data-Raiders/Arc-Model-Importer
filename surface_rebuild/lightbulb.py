"""M_MasterLight_Lightbulb_01: light bulbs from the compiled SM5 base pass (shader map 709BC325, PS 83C059E2).

Base colour sat(GlassColor), roughness 0 (the compiled shader writes only the view roughness bias), metallic 0, and the
Normal texture on UV0. Emissive is Lightbulb.rgb * LightColor * LightIntensity + GlassTexture.g * normalize(LightColor)
* LightIntensity, both textures on UV0 * Tiling; the bulb texture's BumpOffset parallax shift is omitted.
Unlit "off" instances author LightIntensity 0 and so keep only the glass.
"""
import math

DEFAULTS = {'LightIntensity': 100., 'Tiling': 1.}
COLORS = {'LightColor': (1., 1., 1., 0.), 'GlassColor': (.354167, .354167, .354167, 0.)}


def build_lightbulb(g, r):
    s, c, t = r['scalars'], r['colors'], r['textures']
    intensity = g.parameter('LightIntensity', s.get('LightIntensity', DEFAULTS['LightIntensity']))
    light = tuple(float(x) for x in c.get('LightColor', COLORS['LightColor'])[:3])
    length = math.sqrt(sum(x * x for x in light))
    unit = tuple(x / length for x in light) if length else (0., 0., 0.)
    glass = tuple(min(max(float(x), 0.), 1.) for x in c.get('GlassColor', COLORS['GlassColor'])[:3])
    uv = g.transform_uv(0, float(s.get('Tiling', DEFAULTS['Tiling'])))
    bulb = g.image(t.get('Lightbulb', ''), uv, label='Lightbulb emissive colour', required=False)
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
    g.report['implemented_stages'] = ['glass base colour', 'bulb and glass-mask emissive scaled by LightIntensity',
                                      'normal texture']
    g.report['warnings'].append('BumpOffset parallax on the bulb texture is not reproduced')
    g.finish(glass, 0., 0, g.decode_normal(normal) if normal else None, emission=emission)
