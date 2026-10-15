"""Packed PBR, concrete architecture, layered environment, and weapon V1 profiles."""
import re


LAYOUTS = {'CR': [('color', 'color'), ('a', 'rough')], 'CA': [('color', 'color'), ('a', 'alpha')],
           'CS': [('color', 'color'), ('a', 'spec')], 'CM': [('color', 'color'), ('a', 'metal')],
           'NCR': [('normal', 'normal'), ('b', 'detail_color'), ('a', 'rough')],
           'NCH': [('normal', 'normal'), ('b', 'detail_color'), ('a', 'height')],
           'NRA': [('normal', 'normal'), ('b', 'rough'), ('a', 'alpha')],
           'NXR': [('normal', 'normal'), ('a', 'rough')],
           'XEM': [('g', 'emission'), ('b', 'metal')],
           'GRM': [('g', 'rough'), ('b', 'metal')]}


def layout(code):
    if code in LAYOUTS: return LAYOUTS[code]
    cursor = 0; result = []
    for char in code:
        if char in 'CD':
            if cursor == 0: result.append(('color', 'color'))
            cursor += 3
        elif char == 'N':
            if cursor == 0: result.append(('normal', 'normal'))
            cursor += 2
        else:
            semantic = {'R': 'rough', 'M': 'metal', 'O': 'ao', 'A': 'alpha', 'S': 'spec', 'E': 'emission', 'H': 'height'}.get(char)
            if semantic and cursor < 4: result.append(('rgba'[cursor], semantic))
            cursor += 1
    return result


def texture_code(param, ref):
    suffix = ref.rsplit('_', 1)[-1].split('.')[0].upper()
    if re.fullmatch('[CDNROMAESXHG]{1,4}', suffix): return suffix
    if re.fullmatch('[CDNROMAESXHG]{1,4}', param): return param
    low = param.lower()
    if low in {'basecolor', 'base color', 'albedo', 'diffuse', 'color'}: return 'C'
    if low in {'normal', 'normals', 'normal map'}: return 'N'
    if low == 'roughness': return 'R'
    return ''


def luminance(g, color):
    return g.vector('DOT_PRODUCT', color, (.3, .59, .11))


def desaturate(g, color, value):
    if value == 0: return color
    v = luminance(g, color)
    return g.mix(color, g.combine(v, v, v), value)


def remap(g, v, low, high):
    return g.clamp(g.div(g.sub(v, low), max(high - low, 1e-6)))


def gather(g, r, prefix='', uv=None):
    result = {'color': r['colors'].get(prefix + 'Tint', r['colors'].get('Base Color', (.5, .5, .5, 1))),
              'rough': .5, 'metal': 0, 'spec': .5, 'alpha': 1, 'normal': None, 'ao': 1, 'emission': None}
    seen, assigned = set(), set()
    priority = sorted(r['textures'].items(), key=lambda kv: (kv[0].startswith('PM_') or kv[0].startswith('T_'), len(kv[0])))
    for name, ref in priority:
        if not ref or ref in seen: continue
        if name.startswith('TA_'): continue
        if prefix and not name.startswith(prefix): continue
        if not prefix and (name.startswith(('1.', '2.', '3.', '4.')) or any(word in name.lower() for word in ['overlay', 'detail', 'breakup', 'wear', 'id', 'mask', 'decal', 'dust', 'slice', 'blend', 'pattern'])): continue
        code = texture_code(name.removeprefix(prefix).strip(), ref)
        roles = layout(code)
        if not roles: continue
        seen.add(ref)
        tex = g.image(ref, uv, srgb=None if any(role == 'color' for _, role in roles) else False)
        if not tex: continue
        for channel, role in roles:
            if role in assigned: continue
            if role == 'normal':
                result['normal'] = g.decode_normal(tex, r['scalars'].get(prefix + 'Normal Strength', r['scalars'].get('Normal Strength', 1)))
                assigned.add(role)
            elif role in result:
                result[role] = tex[channel]
                assigned.add(role)
            elif role == 'height': g.report['warnings'].append('Height/displacement map exposed but not applied: ' + name)
    return result


def build_surface(g, r):
    g.section('Packed surface textures')
    s, c, sw = r['scalars'], r['colors'], r['switches']
    uv_channel = 2 if sw.get('Use UV2') or sw.get('UseUV2') else 1 if sw.get('Use UV1') or sw.get('1. Use Texture Coord1 For Tiling') else 0
    tile = s.get('Tiling', s.get('UV Tiling', s.get('UVTiling', 1)))
    uv = g.transform_uv(uv_channel, tile, (s.get('UVOffset_U', 0), s.get('UVOffset_V', 0)))
    surf = gather(g, r, uv=uv)
    use_tint = next((bool(sw[k]) for k in ['Use Tinting', 'Use Tint', 'UseTint'] if k in sw), False)
    if c.get('Tint') and use_tint:
        tint = tuple(v * 2 for v in c['Tint'][:3]) + (1,)
        surf['color'] = g.cmul(surf['color'], tint, 'Authored neutral-grey tint')
        g.report['warnings'].append('Generic Tint assumes the neutral 0.5 convention; verify unusual master families')
    surf['rough'] = g.clamp(g.mul(surf['rough'], s.get('RoughnessMultiplier', 1)))
    if sw.get('Enable DetailNormal') or sw.get('Use Detail'):
        ref = r['textures'].get('Detail Normal Texture') or r['textures'].get('Detail') or r['textures'].get('Detail Normal')
        if ref:
            detail = g.image(ref, g.transform_uv(uv_channel, tile * s.get('Detail Normal Tiling', s.get('Detail Tiling', 1))), srgb=False)
            if detail: surf['normal'] = g.rnm(surf['normal'] if surf['normal'] is not None else (0, 0, 1), g.decode_normal(detail, s.get('Detail Normal Strength', 1)))
    active_complex = [k for k, v in sw.items() if v and any(w in k.lower() for w in ['dust', 'paint', 'waterline', 'slice', 'overlay', 'blend texture', 'pattern', 'tint mask'])]
    if active_complex: g.report['warnings'].append('Additional surface stages not reconstructed: ' + ', '.join(active_complex))
    g.report['implemented_stages'] = ['packed texture channels', 'Unreal UV transforms', 'DirectX normals', 'detail normals', 'linear material parameters']
    alpha = surf['alpha']
    blend = str(r['settings'].get('BlendMode', ''))
    if 'Masked' in blend or blend == '1':
        # Drop the atlas gutter (the flag sheet's green margin) instead of blending it.
        alpha = g.math('GREATER_THAN', alpha, float(r['settings'].get('OpacityMaskClipValue', 0.5) or 0.5))
    g.finish(surf['color'], surf['rough'], surf['metal'], surf['normal'], surf['spec'], surf['emission'], alpha=alpha)


def build_architecture(g, r):
    # This profile is traced against the current Concrete_01 base-pass shader.
    if not r.get('master', '').endswith('M_ArchitecturePreset_Concrete_01'):
        build_surface(g, r)
        g.report['warnings'].append('This architecture master uses the generic V1 profile')
        return
    g.section('Concrete_01 — recovered UV and overlay equations')
    s, c, t, sw = r['scalars'], r['colors'], r['textures'], r['switches']
    tile = s.get('Tiling', 1)
    uv = g.transform_uv(0, tile)
    cr = g.image(t.get('CR', ''), uv)
    normal = g.image(t.get('NOH', ''), uv, srgb=False)
    if not cr:
        build_surface(g, r); return
    color = cr['color']
    contrast = s.get('BC Contrast', 0)
    color = g.vector('ADD', g.vector('SCALE', color, scale=1 + 2 * contrast), (-contrast,) * 3)
    color = g.vector('MINIMUM', g.vector('MAXIMUM', color, (0, 0, 0)), (1, 1, 1))
    rough = remap(g, cr['a'], s.get('RoughnessRangeLow', 0), s.get('RoughnessRangeHigh', 1))
    if sw.get('UseOverlay', False):
        overlay_tile = s.get('OverlayTiling', 1)
        # Recovered path first blends 0.1*OverlayTiling*UV0 with UV0 and UV1,
        # then applies OverlayTiling again. This is intentionally not a generic scale.
        use0, use1 = s.get('Use UV0 as Overlay UVs', 0), s.get('Use UV1 as Overlay UVs', 0)
        base = g.vector('SCALE', g.uv(0), scale=.1 * overlay_tile)
        base = g.mix(base, g.uv(0), use0)
        base = g.mix(base, g.uv(1), use1)
        x, y, _ = g.split(base)
        ouv = g.combine(g.add(g.mul(x, overlay_tile), s.get('UVOffset', 0)), g.add(g.mul(y, overlay_tile), s.get('UVOffset', 0)))
        if s.get("Use Rotate UV's", 0):
            from math import sin, cos, radians
            theta = radians(s.get('Rotation Angle', 0))
            x, y, _ = g.split(ouv); x, y = g.sub(x, .5), g.sub(y, .5)
            ouv = g.combine(g.add(g.sub(g.mul(x, cos(theta)), g.mul(y, sin(theta))), .5),
                             g.add(g.add(g.mul(x, sin(theta)), g.mul(y, cos(theta))), .5))
        overlay = g.image(t.get('Overlay', ''), ouv)
        if overlay:
            lum = luminance(g, overlay['color'])
            f = remap(g, lum, s.get('OverlayRange Min', 0), s.get('OverlayRange Max', 1))
            f = g.mix(g.sub(1, f), f, s.get('OverlayRange Invert', 0))
            strength = g.clamp(g.mul(f, s.get('Overlay_BaseColor_Strength', 1)))
            mod = g.cmul(desaturate(g, overlay['color'], s.get('OverlayDesaturation', 0)), c.get('Overlay Color', (.5, .5, .5, 1)))
            mod = g.vector('SCALE', g.cmul(mod, color), scale=4)
            color = g.mix(color, mod, strength)
            rough = g.mix(rough, g.mul(g.mul(f, s.get('Overlay_Roughness_Strength', 1)), g.mul(rough, 2)), g.clamp(f))
    color = desaturate(g, color, s.get('Desaturation', 0))
    color = g.vector('SCALE', g.cmul(color, c.get('Tint', (.5, .5, .5, 1))), scale=2)
    n = g.decode_normal(normal)
    if sw.get('Use Detail', False) and t.get('Detail Normal Texture'):
        detail = g.image(t['Detail Normal Texture'], g.transform_uv(0, tile * s.get('Detail Normal Tiling', 1)), srgb=False)
        if detail:
            x, y, z = g.split(n); dx, dy, _ = g.split(g.decode_normal(detail, s.get('Detail Normal Strength', 1)))
            n = g.combine(g.add(g.mul(x, s.get('Normal Strength', 1)), dx),
                          g.add(g.mul(y, s.get('Normal Strength', 1)), dy), z)
            n = g.vector('NORMALIZE', n)
    g.report['implemented_stages'] = ['Concrete_01 compiled-shader profile', 'base UV0', 'overlay UV selection, double scale, offset, rotation',
        'overlay luminance range/inversion', 'overlay colour and roughness', 'contrast/desaturation', 'neutral-grey tint ×2', 'normal XY addition']
    for feature in ['Use Paint', 'Use Waterline', 'TriPlanar Overlay', 'Enable Blend Textures']:
        if sw.get(feature): g.report['warnings'].append('Concrete feature omitted in V1: ' + feature)
    g.report['warnings'].append('Scene wetness, snow, custom primitive data, and distance response are not reconstructed')
    g.finish(color, g.clamp(rough), 0, n)


def build_environment_layers(g, r):
    g.section('Environment layers — separate texture scales')
    s, sw = r['scalars'], r['switches']
    triplanar = bool(sw.get('0. Use Triplanar'))
    layers = []
    for i in [1, 2]:
        prefix = f'{i}. '
        if triplanar:
            # World dimensions are authored in Unreal centimetres.
            cr = g.triplanar(r['textures'].get(prefix + 'CR', ''), max(s.get(prefix + 'Texture Size', 100), .001))
            surf = {'color': cr['color'] if cr else (.5, .5, .5, 1), 'rough': cr['a'] if cr else .5, 'normal': None}
        else:
            uv = g.transform_uv(0, s.get(prefix + 'Tiling', s.get('Tiling', 1)), (s.get('U offset', 0), s.get('V offset', 0)))
            surf = gather(g, r, prefix, uv)
        surf['rough'] = g.clamp(g.add(surf['rough'], s.get(prefix + 'Roughness', 0)))
        layers.append(surf)
    attr = g.obj.data.color_attributes
    if attr:
        n = g.node('ShaderNodeVertexColor', 'Vertex layer mask')
        n.layer_name = attr[0].name
        mask = g.split(n.outputs['Color'])[0]
    else:
        mask = 0
        g.report['warnings'].append('No vertex blend mask; first environment layer used')
    ref = r['textures'].get('3. Blend Mask') or r['textures'].get('Blend Mask')
    if ref:
        sample = g.image(ref, g.transform_uv(0, s.get('3. Blend Mask Tiling', 1)), srgb=False)
        if sample: mask = sample['r']
    mask = remap(g, mask, s.get('3. Blend Mask Low', 0), s.get('3. Blend Mask High', 1))
    color = g.mix(layers[0]['color'], layers[1]['color'], mask)
    rough = g.mix(layers[0]['rough'], layers[1]['rough'], mask)
    normal = g.mix(layers[0]['normal'] if layers[0]['normal'] is not None else (0, 0, 1),
                   layers[1]['normal'] if layers[1]['normal'] is not None else (0, 0, 1), mask)
    g.report['implemented_stages'] = ['two surface layers', 'vertex/texture blend masks', 'separate UV scales', 'world colour projection where selected']
    g.report['warnings'].append('Environment layer mask selection, triplanar normals, paint and overlays remain a V1 approximation')
    g.finish(color, g.clamp(rough), normal=normal)


def build_weapon(g, r):
    g.section('Weapon — ID zones and authored surface overrides')
    s, c, sw, t = r['scalars'], r['colors'], r['switches'], r['textures']
    surf = gather(g, r, uv=g.transform_uv(0, s.get('Tiling', 1)))
    ids = g.image(t.get('Weapon ID', ''), srgb=False)
    if ids:
        for channel, name in [('r', 'Red'), ('g', 'Green'), ('b', 'Blue'), ('a', 'Alpha')]:
            if not sw.get('Enable ' + name + ' Mask', False): continue
            f = ids[channel]
            color = c.get('Base - Color - ' + name)
            if color:
                surf['color'] = g.mix(surf['color'], g.cmul(surf['color'], color), f)
            metal = s.get('Base - Metalness Tweak - ' + name)
            if metal is not None: surf['metal'] = g.mix(surf['metal'], g.clamp(g.add(surf['metal'], metal)), f)
            rough = c.get('Base - Roughness Tweak - ' + name)
            if rough:
                surf['rough'] = g.mix(surf['rough'], g.clamp(g.add(surf['rough'], rough[1])), f)
    g.report['implemented_stages'] = ['packed base channels', 'weapon ID RGBA', 'authored zone colours and roughness/metal adjustments']
    g.report['warnings'].append('Weapon quality wear, patterns, glare, custom surface type and rust are not fully reconstructed in V1')
    g.finish(surf['color'], g.clamp(surf['rough']), g.clamp(surf['metal']), surf['normal'], surf['spec'], surf['emission'])
