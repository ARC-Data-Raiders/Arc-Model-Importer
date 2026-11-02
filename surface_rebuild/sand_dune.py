"""M_SandDune: South dune meshes coloured by the landscape runtime virtual texture (VirtualTextureBlend).

The compiled map (849A7F5D) reads base colour from the landscape RVT, which a static import cannot sample. The
Dune_Spline copy of the master exposes only Param_1 (NR); the Dune_Prop_01 copy also binds Param (CH). The stand-in
is the landscape sand layer, T_South_Base_Sand_Dunes_04_CH, or its linear mean when that texture was not exported.
"""
SAND_LAYER = 'PioneerGame/Content/Pioneer/Environment/South/Dunes/Textures/T_South_Base_Sand_Dunes_04_CH'
SAND_MEAN = (.2992, .2114, .1346, 1.)


def build_sand_dune(g, r):
    s, t = r['scalars'], r['textures']
    uv = g.transform_uv(0, s.get('Tiling', 1))
    color_ref = t.get('Param') or SAND_LAYER
    color_tex = g.image(color_ref, uv, label='Sand colour (RVT stand-in)' if not t.get('Param') else 'Sand colour',
                        required=False)
    if not t.get('Param'):
        g.report['warnings'].append('Landscape RVT colour replaced by the sand layer texture (stand-in)')
    color = color_tex['color'] if color_tex else SAND_MEAN
    if not color_tex:
        g.report['warnings'].append('Sand layer texture not exported; its linear mean colour is used')
    nr = g.image(t.get('Param_1', ''), uv, srgb=False, label='NR normal RG, roughness B', required=False)
    normal = g.decode_normal(nr) if nr else None
    rough = nr['b'] if nr else .8
    g.report['implemented_stages'] = ['sand colour (authored Param or RVT stand-in)', 'NR normal and roughness']
    g.finish(color, g.clamp(rough), 0, normal)
