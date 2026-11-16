"""M_MasterLight_Emissive_01 and M_Blockout_Flickerlight: authored light panels.

Game data (master JSON): LightIntensity 100, LightColor (1, .7001, .3979), Texture default T_EyeOcclusion2, Normal default
FlatNormal; PropertyConnectedMask 1121 (Emissive, BaseColor, Metallic, Normal). No base-colour parameter or colour texture.
Emission is linear LightColor * LightIntensity, the same contract the native light path (emission.py authored-light-v1) uses.
"""

DEFAULTS = {'LightIntensity': 100.}
LIGHT_COLOR = (1., .700093, .397902, 1.)

from .light_controls import pulse_intensity


def _approx(g, text):
    g.report['warnings'].append('approximate: ' + text)


def build_light_emissive(g, r):
    s, c, t = r['scalars'], r['colors'], r['textures']
    intensity = g.parameter('LightIntensity', max(0., float(s.get('LightIntensity', DEFAULTS['LightIntensity']))))
    intensity = pulse_intensity(g, intensity, r)
    light = tuple(float(x) for x in c.get('LightColor', LIGHT_COLOR)[:3])
    normal = g.image(t.get('Normal', ''), g.transform_uv(0, 1), srgb=False, label='Normal', required=False)
    # DR_APPROX_PLACEHOLDER: BaseColor pin is connected (mask bit 5) but its expression is not decompiled; black base like
    # the native light path. The 'Texture' slot (default T_EyeOcclusion2) role is unknown and is not sampled.
    _approx(g, 'light base colour assumed black; Texture slot not sampled')
    if 'FlickerTime' in s or 'LowestBrightness' in s or 'Flickerlight' in str(r.get('master', '')):
        # DR_APPROX_PLACEHOLDER: time/noise flicker (FlickerTime, LowestBrightness, Noise Scale) is held at full brightness.
        _approx(g, 'flicker animation held at full LightIntensity')
    g.report['implemented_stages'] = ['authored LightColor * LightIntensity emission', 'normal texture']
    g.finish((0., 0., 0.), .5, 0, g.decode_normal(normal) if normal else None,
             emission=g.vector('SCALE', light, scale=intensity))
