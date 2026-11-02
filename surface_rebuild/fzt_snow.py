"""M_FZT_Snow_01: FrozenTrail snow (SnowBus, SnowScatter) from the compiled SM5 base pass (shader map 51400BCA,
PS 8DF77F37).

Both normal maps are projected top-down in world space: '1. Snow_NR_1' (T_FZT_SnowPlain_01_NH) at world XY /
'1. Texture Size' and the fixed T_FZT_SnowDetail_01_NH at world XY / 128 cm. The plain normal is scaled by
'3. Normal Strength' as (s x, s y, s (z - 1) + 1) and the detail adds 1.5 (RG - 0.5) to its XY. The sum lerps to flat
by vertex red x round(IsRoof) (UE binds white vertex colour, so fully flat, on meshes without a colour buffer) and goes
through the mesh tangent basis. With f = sat(17 max(|1 - max(N.V, 0)|, 1e-4)^0.1 - 16) on the vertex normal, colour is
(0.85, 0.85, 0.9) + f (0.05, 0.05, 0.1), roughness sat(detail A - f Roughness Multiplier / 2), metallic 0 and
specular 0.2 - 0.1 sat((camera distance - 950 cm) / 650 cm).
"""
from .masked import vertex, vlerp
from .snow_pile import SNOW_DETAIL

SNOW_PLAIN = 'PioneerGame/Content/Pioneer/Environment/Nature/Locations/FrozenTrail/Textures/T_FZT_SnowPlain_01_NH'
DEFAULTS = {'1. Texture Size': 1024., '3. Normal Strength': 1., 'IsRoof': 1., 'Roughness Multiplier': 1.}
DETAIL_SIZE = 128.


def build_fzt_snow(g, r):
    s, t = r['scalars'], r['textures']
    def v(k): return float(s.get(k, DEFAULTS[k]))
    world_scale = float(g.obj.get('arc_ue_unit_scale', g.obj.data.get('arc_uemodel_scale', .01)))
    geometry = g.node('ShaderNodeNewGeometry', 'World position, vertex normal and view')
    x, y, _ = g.split(geometry.outputs['Position'])
    def world_uv(size):
        # UE world XY in centimetres: Blender metres over the unit scale, Y flipped.
        return g.combine(g.div(x, world_scale * size), g.div(y, -world_scale * size), 0)
    plain = g.image(t.get('1. Snow_NR_1') or SNOW_PLAIN, world_uv(v('1. Texture Size')), srgb=False,
                    label='Snow normal RG, height A (world XY / Texture Size)', required=False)
    detail = g.image(SNOW_DETAIL, world_uv(DETAIL_SIZE), srgb=False,
                     label='Fixed snow detail normal RG, roughness A (world XY / 128 cm)', required=False)
    if plain:
        strength = g.parameter('3. Normal Strength', v('3. Normal Strength'))
        px, py = g.sub(g.mul(plain['r'], 2), 1), g.sub(g.mul(plain['g'], 2), 1)
        pz = g.math('SQRT', g.math('MAXIMUM', g.sub(1, g.add(g.mul(px, px), g.mul(py, py))), 0))
        nx, ny, nz = g.mul(px, strength), g.mul(py, strength), g.add(g.mul(g.sub(pz, 1), strength), 1)
    else:
        nx, ny, nz = 0, 0, 1
        g.report['warnings'].append('Snow normal texture missing; flat base normal used: ' + SNOW_PLAIN)
    if detail:
        nx, ny = g.add(nx, g.mul(g.sub(detail['r'], .5), 1.5)), g.add(ny, g.mul(g.sub(detail['g'], .5), 1.5))
    normal = g.combine(nx, ny, nz)
    roof = round(v('IsRoof'))
    if roof:
        normal = vlerp(g, normal, (0, 0, 1), g.mul(vertex(g)['r'], roof))
    normal = g.normal_to_world(g.vector('NORMALIZE', normal))
    ndotv = g.vector('DOT_PRODUCT', geometry.outputs['Normal'], geometry.outputs['Incoming'])
    grazing = g.math('MAXIMUM', g.math('ABSOLUTE', g.sub(1, g.math('MAXIMUM', ndotv, 0))), 1e-4)
    f = g.clamp(g.sub(g.mul(g.math('POWER', grazing, .1), 17), 16))
    grey = g.add(.85, g.mul(f, .05))
    color = g.combine(grey, grey, g.add(.9, g.mul(f, .1)))
    gloss = g.mul(f, g.mul(g.parameter('Roughness Multiplier', v('Roughness Multiplier')), .5))
    if detail:
        rough = g.clamp(g.sub(detail['a'], gloss))
    else:
        rough = g.clamp(g.sub(.5, gloss))
        g.report['warnings'].append('Snow detail texture missing; mid roughness used: ' + SNOW_DETAIL)
    camera = g.node('ShaderNodeCameraData', 'Camera distance for the specular fade')
    distance = g.div(camera.outputs['View Distance'], world_scale)
    specular = g.sub(.2, g.mul(g.clamp(g.div(g.sub(distance, 950), 650)), .1))
    g.report['implemented_stages'] = ['compiled snow colour (grazing lift)',
                                      'world-projected snow and detail normals with vertex-red roof flatten',
                                      'detail-alpha roughness with grazing gloss', 'distance specular fade']
    g.report['warnings'].append('Pixel depth offset and dithered LOD fade are not reproduced; the compiled base pass '
                                'samples no runtime virtual texture')
    g.finish(color, rough, 0, normal, specular=specular)
