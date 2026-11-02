"""Effect masters whose compiled base pass adds no colour, so their meshes are invisible in a static render.

M_FX_MeshRadialBlur (shader map 0921C5BD, base-pass PS 317636DA and 36160E4B): Unlit BLEND_Additive. The pixel
output is fog x max(SelectionColor.rgb x SelectionColor.a, 0) x pre-exposure with alpha 0, which is black outside the
editor; the radial T_FX_SmudgeNoise_01 mask (sampled at the polar angle) only clips. PropertyConnectedMask 4099
connects Emissive, Opacity and World Position Offset but not Refraction, and the master sets
bOutputTranslucentVelocity, so the in-game "radial blur" is motion blur from the velocity its WPO writes (dynamic
parameter BlurStrength) and has nothing a static render can show.
"""
REASONS = {'m_fx_meshradialblur': 'additive base pass adds only the editor selection colour; the in-game radial blur '
                                  'is motion blur from its world-position-offset velocity'}


def build_fx_invisible(g, r):
    reason = REASONS.get(str(r.get('master', '')).rsplit('/', 1)[-1].lower(), 'compiled base pass adds no colour')
    g.report['shader_profile'] = 'fx_invisible_sm5_v1'
    g.report['implemented_stages'] = ['compiled additive base pass with no colour contribution']
    g.report['warnings'].append('Invisible in a static render: ' + reason)
    g.finish((0., 0., 0.), 1., 0, specular=0., alpha=0.)
