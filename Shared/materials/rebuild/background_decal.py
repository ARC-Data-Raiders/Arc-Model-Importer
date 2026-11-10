"""M_BackgroundDecal: grunge overlay cards that sit on props (mesh decals, DeferredDecal domain).

Reconstructed from the SM5 translucent base-pass PS of the master's shader map (CD00082E, build 26159202);
MI_GantryCrane_01_Grunge_A's own permutation (B4ABA5B3) compiles to the same math. Preshader UB:
cb2[1] = (Grunge color.rgb, Opacity Strength). The master samples one fixed texture, T_GrungeAtlas_Props_A,
red channel at UV0 (a referenced texture, not a parameter), and lights the result through the
translucency volume. The projected-decal PS of the same map sets Roughness to that mask and Specular to 0.5.
"""
ATLAS = 'PioneerGame/Content/Pioneer/MaterialLibrary/Textures/Props/T_GrungeAtlas_Props_A'
GRUNGE = (.145833, .113201, .090767, 1.)
OPACITY = .75


def build_background_decal(g, r):
    s, c, t = r['scalars'], r['colors'], r['textures']
    g.report['shader_profile'] = 'background_decal_v1'
    reference = next((v for v in t.values() if 'grungeatlas' in str(v).casefold()), ATLAS)
    atlas = g.image(reference, g.uv(0), label='UV0 · grunge atlas mask (red)', required=False)
    if atlas is None:
        g.report['warnings'].append('T_GrungeAtlas_Props_A missing; grunge overlay hidden')
        g.finish(c.get('Grunge color', GRUNGE), 1, 0, alpha=0)
        return
    mask = atlas['r']
    with g.block('Grunge colour and opacity from the atlas mask') as b:
        tint = b.parameter('Grunge color', c.get('Grunge color', GRUNGE), 'Color')
        color = b.vector('MINIMUM', b.vector('MAXIMUM', b.vector('SCALE', tint, scale=mask), (0., 0., 0.)), (1., 1., 1.))
        alpha = b.clamp(b.mul(mask, b.parameter('Opacity Strength', s.get('Opacity Strength', OPACITY))))
        roughness = b.clamp(mask)
    g.report['implemented_stages'] = ['UV0 grunge atlas red mask', 'Grunge color x mask', 'Opacity Strength x mask',
                                      'mask roughness, specular 0.5']
    g.finish(color, roughness, 0, specular=.5, alpha=alpha)
