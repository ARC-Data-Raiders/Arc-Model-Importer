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
    result = {'color': r['colors'].get(prefix + 'Tint', r['colors'].get('Base Color', r['colors'].get(prefix + 'Color', (.5, .5, .5, 1)))),
              'rough': .5, 'metal': 0, 'spec': .5, 'alpha': 1, 'normal': None, 'ao': 1, 'emission': None}
    seen, assigned = set(), set()
    master = str(r.get('master', '')).rsplit('/', 1)[-1].casefold()
    native_packed = master in {'m_proppreset_cr+nom', 'm_proppreset_cr+nom+skinned', 'm_proppreset_cr+nox+xem'}
    priority = sorted(r['textures'].items(), key=lambda kv: (kv[0].startswith('PM_') or kv[0].startswith('T_'), len(kv[0])))
    for name, ref in priority:
        if not ref or ref in seen: continue
        if name.startswith('TA_'): continue
        if prefix and not name.startswith(prefix): continue
        packed_mask = native_packed and master.endswith('+nox+xem') and name.casefold() == 'packed mask'
        if not prefix and (name.startswith(('1.', '2.', '3.', '4.')) or (not packed_mask and any(word in name.lower() for word in ['overlay', 'detail', 'breakup', 'wear', 'id', 'mask', 'decal', 'dust', 'slice', 'blend', 'pattern']))): continue
        # Native parameter roles outrank a default texture's filename suffix:
        # e.g. CR binds T_Default_01_CA and Packed Mask binds T_Default_01_M.
        native_role = ('XEM' if packed_mask else 'CR' if native_packed and name == 'CR'
                       else ('NOX' if master.endswith('+nox+xem') else 'NOM')
                       if native_packed and name == 'Normal' else '')
        code = native_role or texture_code(name.removeprefix(prefix).strip(), ref)
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


def collapsed_uv(obj, channel):
    """True when a mesh UV layer spans no area (a vertex-animation lookup row or a single point): it cannot carry
    a texture layout."""
    layers = getattr(getattr(obj, 'data', None), 'uv_layers', None)
    if not layers or channel >= len(layers):
        return False
    data = layers[channel].data
    if not len(data):
        return True
    import numpy as np
    uv = np.empty(len(data) * 2, dtype=np.float32)
    data.foreach_get('uv', uv)
    uv = uv.reshape(-1, 2)
    return bool((uv.max(0) - uv.min(0)).min() < 1e-4)


def build_surface(g, r):
    g.section('Packed surface textures')
    s, c, sw = r['scalars'], r['colors'], r['switches']
    master = str(r.get('master', '')).rsplit('/', 1)[-1].casefold()
    # The cloth-sim item masters' Use UV1 drives the cloth VAT lookup row: MI_WhiteFlag_01_A's base pass (PS D0B69E08,
    # local vertex fetch VS 156) samples every texture on mesh UV0.
    use_uv1 = sw.get('Use UV1') and 'clothsim' not in master
    uv_channel = 2 if sw.get('Use UV2') or sw.get('UseUV2') else 1 if use_uv1 or sw.get('1. Use Texture Coord1 For Tiling') else 0
    if uv_channel and collapsed_uv(getattr(g, 'obj', None), uv_channel):
        g.report['warnings'].append(f'UV{uv_channel} spans no area on this mesh (a lookup row, not a layout); textures use UV0')
        uv_channel = 0
    tile = s.get('Tiling', s.get('UV Tiling', s.get('UVTiling', 1)))
    uv = g.transform_uv(uv_channel, tile, (s.get('UVOffset_U', 0), s.get('UVOffset_V', 0)))
    surf = gather(g, r, uv=uv)
    use_tint = next((bool(sw[k]) for k in ['Use Tinting', 'Use Tint', 'UseTint'] if k in sw), False)
    if c.get('Tint') and use_tint:
        tint = tuple(v * 2 for v in c['Tint'][:3]) + (1,)
        surf['color'] = g.cmul(surf['color'], tint, 'Authored neutral-grey tint')
        g.report['warnings'].append('Generic Tint assumes the neutral 0.5 convention; verify unusual master families')
    packed_prop =master in {'m_proppreset_cr+nom', 'm_proppreset_cr+nom+skinned', 'm_proppreset_cr+nox+xem'}
    g.report['shader_profile'] = 'packed_prop_core_v2' if packed_prop else 'generic_packed_channels_v1'
    if not packed_prop:
        g.report['warnings'].append('Generic packed-channel profile; this master has not received an individually reconstructed material profile')
    if packed_prop:
        # Current masters author CR.A ** Roughness Power, not a linear gain.
        # Zero values are valid and must survive the parameter lookup.
        surf['rough'] = g.math('POWER', surf['rough'], s.get('Roughness Power', 1))
        surf['metal'] = g.mul(surf['metal'], s.get('Metallic Strength', s.get('Metalness Value', 1)))
    surf['rough'] = g.clamp(g.mul(surf['rough'], s.get('RoughnessMultiplier', 1)))
    emission_strength = 1
    if master == 'm_proppreset_cr+nox+xem':
        # XEM.G is a state mask, not proof the emissive permutation is enabled.
        if not sw.get('Use Emissive', False):
            surf['emission'] = None
        elif surf['emission'] is not None:
            mask = surf['emission']
            amount = s.get('Use Histogram (0=false | 1=true)', 0)
            if amount:
                binary = g.sub(1, g.math('LESS_THAN', mask, s.get('EmissiveHistogramRange', .001)))
                mask = g.add(mask, g.mul(g.sub(binary, mask), amount))
            use_cr = bool(sw.get('Use CR as Emissive', False))
            pulse = 1 if use_cr else max(0, min(1, s.get('PulsateAmount', 1)))
            emission_strength = g.mul(mask, s.get('EmissiveIntensity', s.get('Emissive Intensity', 1)) * pulse)
            surf['emission'] = surf['color'] if use_cr else c.get('EmissiveColor', (1, 1, 1, 1))
    elif surf['emission'] is not None:
        from .emission_preview import apply_preview
        surf['emission'], emission_strength = apply_preview(g, r, surf['color'], surf['emission'])
    if sw.get('Enable DetailNormal') or sw.get('Use Detail'):
        ref = r['textures'].get('Detail Normal Texture') or r['textures'].get('Detail') or r['textures'].get('Detail Normal')
        if ref:
            detail = g.image(ref, g.transform_uv(uv_channel, tile * s.get('Detail Normal Tiling', s.get('Detail Tiling', 1))), srgb=False)
            if detail: surf['normal'] = g.rnm(surf['normal'] if surf['normal'] is not None else (0, 0, 1), g.decode_normal(detail, s.get('Detail Normal Strength', 1)))
    active_complex = [k for k, v in sw.items() if v and any(w in k.lower() for w in ['dust', 'paint', 'waterline', 'slice', 'overlay', 'blend texture', 'pattern', 'tint mask'])]
    if active_complex:
        g.report['unsupported_features'] = sorted(set(active_complex))
        g.report['warnings'].append('Additional surface stages not reconstructed: ' + ', '.join(active_complex))
    g.report['implemented_stages'] = ['packed texture channels', 'Unreal UV transforms', 'DirectX normals', 'detail normals', 'linear material parameters']
    if 'emission_preview' in g.report:
        g.report['implemented_stages'].append('original generic emission mask/hue/gain preview; not native-verified')
    alpha = surf['alpha']
    blend = str(r['settings'].get('BlendMode', ''))
    if 'Masked' in blend or blend == '1':
        # Drop the atlas gutter (the flag sheet's green margin) instead of blending it.
        alpha = g.math('GREATER_THAN', alpha, float(r['settings'].get('OpacityMaskClipValue', 0.5) or 0.5))
    if packed_prop:
        g.report['implemented_stages'].extend(['native CR roughness exponent', 'native packed metallic strength', 'explicit emissive permutation gate'])
    g.finish(surf['color'], surf['rough'], surf['metal'], surf['normal'], surf['spec'], surf['emission'], alpha=alpha, emission_strength=emission_strength)


def _concrete_range(g, value, low, high):
    """Current Concrete preshader uses reciprocal(saturate(high-low))."""
    span = max(0.0, min(1.0, float(high) - float(low)))
    if span == 0:
        return g.math('GREATER_THAN', value, low)
    return g.clamp(g.div(g.sub(value, low), span))


def _concrete_dry_fresnel(g, roughness, specular, exponent, facing):
    # Current SM5 70F7F9D / DAC0492F: geometric normal, not perturbed normal.
    rim = g.math('ABSOLUTE', g.sub(1, g.math('MAXIMUM', facing, 0)))
    fresnel = g.math('POWER', g.math('MAXIMUM', rim, 1e-4), exponent)
    roughness = g.math('MAXIMUM', g.sub(roughness, g.math('MINIMUM', fresnel, .25)), 0)
    return roughness, g.clamp(g.add(specular, g.math('MINIMUM', fresnel, 1)))


def _concrete_desaturate(g, color, amount):
    # Current shader's linear-sRGB luminance coefficients, not old .3/.59/.11.
    if amount == 0:
        return color
    value = g.vector('DOT_PRODUCT', color, (.21263900399208069, .7151686549186707, .07219231873750687))
    return g.mix(color, g.combine(value, value, value), amount)


def _concrete_paint(g, r, color, roughness, normal, uv, overlay, height, camera_distance):
    """Dry local-height Paint branch from current DAC0492F SM5 pixel 8EC0E091.

    Heights are the native relative-world-to-local Z in UE centimetres. RGB/A
    packing and preshader arithmetic are reconstructed, including negative
    PaintBreakup, separate roughness opacity, and the authored thickness gate.
    """
    s, c, t = r['scalars'], r['colors'], r['textures']
    sample = g.image(t.get('PaintBreakup', ''), g.transform_uv(0, s.get('Tiling', 1) * s.get('UVTilingPaint', .25)),
                     srgb=False, label='Native paint normal / edge B / breakup A')
    if not sample:
        g.report['warnings'].append('Native Concrete paint requires its authored PaintBreakup RGBA texture')
        return color, roughness, normal
    a, blue = sample['a'], sample['b']
    breakup = s.get('PaintBreakup', 1)
    breakup_weight = g.mul(g.mul(a, s.get('PaintBreakupScale', 1)), breakup * .4000000059604645)
    # Shader overlay(height, breakup_weight), followed by inversion. No clamping
    # of PaintBreakup or the local-height input is allowed here.
    low = g.mul(g.mul(height, 2), breakup_weight)
    high = g.sub(1, g.mul(g.mul(g.sub(1, height), 2), g.sub(1, breakup_weight)))
    inverted = g.sub(1, g.mix(low, high, g.sub(1, g.math('LESS_THAN', height, .5))))
    falloff = s.get('PaintFalloff', 250)
    phase = g.div(inverted, falloff) if falloff != 0 else g.mul(inverted, 1e30)
    edge = g.clamp(g.add(g.mul(g.sub(blue, .20000000298023224), -1.7142854928970337), 1))
    denominator = g.mix(1, edge, max(0, min(.9900000095367432, breakup)))
    def band(limit):
        height_gate = g.clamp(g.add(phase, limit))
        return g.clamp(g.mul(g.sub(1, g.div(g.sub(1, height_gate), denominator)), 1.5))
    mask = g.math('MAXIMUM', g.sub(band(s.get('PaintLimitUpper', 1)), band(s.get('PaintLimitLower', 1))), 0)
    overlay_red = overlay['r'] if overlay else 1
    normal_mask = g.clamp(g.mul(mask, g.mul(overlay_red, s.get('BreakupOverlayAmount', 1))))
    thickness = s.get('Paint Thickness', 0)
    thickness_gate = g.mix(normal_mask, 1, max(0, min(1, thickness)))
    hole = g.clamp(g.mul(g.sub(blue, .8999999761581421), 9.999998092651367))
    color_mask = g.math('MAXIMUM', g.sub(mask, hole), 0)
    grain = g.mix(g.add(g.sub(1, sample['g']), sample['r']), blue, .15000000596046448)
    paint = c.get('PaintColor1', (.5, .5, .5, 1))
    amount = s.get('ColorOverlayAmount', 0)
    distance_amount = g.mix(.1, amount, g.clamp(g.mul(g.sub(camera_distance, 500), .00033333332976326346)))
    amount = g.mix(amount, distance_amount, s.get('Enable_ColorOverlayDistanceFade', 0))
    canvas = g.vector('SCALE', overlay['color'] if overlay else (1, 1, 1), scale=2)
    textured_paint = g.cmul(canvas, paint)
    textured_paint = g.mix(textured_paint, g.cmul(textured_paint, grain), thickness)
    paint_color = g.mix(paint, textured_paint, amount)
    painted = g.mix(color, paint_color, color_mask)
    painted = g.mix(painted, g.cmul(painted, s.get('UnderPaintDarken', 0)), hole)
    color = g.mix(color, painted, thickness_gate)
    paint_roughness = s.get('PaintRoughness', .65)
    opacity = s.get('PaintRoughnessOpacity', .65)
    grain_roughness = g.mix(grain, paint_roughness, opacity)
    painted_roughness = g.mix(roughness, paint_roughness, g.mul(color_mask, opacity))
    roughness = g.mix(roughness, g.mix(grain_roughness, painted_roughness, max(0, min(1, thickness))), thickness_gate)
    # Native RNM numerator and paint-thickness flattening, normalized at finish.
    nx, ny, nz = g.split(normal)
    flatten = g.sub(1, g.mul(normal_mask, thickness))
    base = g.combine(g.mul(nx, flatten), g.mul(ny, flatten), g.add(nz, 1))
    px, py = g.mul(g.sub(sample['r'], .5), -2), g.mul(g.sub(sample['g'], .5), -2)
    pz = g.math('SQRT', g.math('MAXIMUM', g.sub(1, g.add(g.mul(px, px), g.mul(py, py))), 0))
    detail = g.combine(px, py, pz)
    dot = g.vector('DOT_PRODUCT', base, detail)
    numerator = g.vector('SUBTRACT', g.vector('SCALE', base, scale=dot), g.vector('SCALE', detail, scale=g.add(nz, 1)))
    painted_normal = g.vector('SCALE', numerator, scale=normal_mask)
    normal = g.mix(normal, painted_normal, thickness_gate)
    return color, roughness, normal


def build_architecture(g, r):
    if str(r.get('master', '')).rsplit('/', 1)[-1].casefold() != 'm_architecturepreset_concrete_01':
        build_surface(g, r)
        g.report['warnings'].append('This architecture master uses the generic packed-channel profile')
        return
    g.report['shader_profile'] = 'concrete_native_dry_v3'
    g.section('Concrete_01 — current native dry surface')
    s, c, t, sw = r['scalars'], r['colors'], r['textures'], r['switches']
    tile = s.get('Tiling', 1)
    uv = g.transform_uv(0, tile)
    cr = g.image(t.get('CR', ''), uv)
    macro = g.image(t.get('NOH', ''), uv, srgb=False)
    if not cr:
        build_surface(g, r)
        return
    color = cr['color']
    contrast = s.get('BC Contrast', 0)
    color = g.vector('ADD', g.vector('SCALE', color, scale=1 + 2 * contrast), (-contrast,) * 3)
    color = g.vector('MINIMUM', g.vector('MAXIMUM', color, (0, 0, 0)), (1, 1, 1))
    rough = _concrete_range(g, cr['a'], s.get('RoughnessRangeLow', 0), s.get('RoughnessRangeHigh', 1))
    n = g.decode_normal(macro)
    nx, ny, nz = g.split(n)
    detail_enabled = next((bool(sw[k]) for k in ('Enable DetailNormal', 'Use Detail') if k in sw), False)
    detail = None
    if detail_enabled or sw.get('Enable Detail Albedo'):
        detail = g.image(t.get('Detail Normal Texture', ''), g.transform_uv(0, tile * s.get('Detail Normal Tiling', 1)), srgb=False)
    if detail_enabled and detail:
        dx, dy, _ = g.split(g.decode_normal(detail))
        nx = g.add(nx, g.mul(dx, s.get('Detail Normal Strength', 1)))
        ny = g.add(ny, g.mul(dy, s.get('Detail Normal Strength', 1)))
    if sw.get('Enable Detail Albedo') and detail:
        color = g.mix(color, g.cmul(color, g.mul(detail['b'], 2)), s.get('Detail Albedo Strength', 1))
    overlay = None
    if sw.get('UseOverlay') or sw.get('Enable Blend Textures') or sw.get('Use Paint'):
        overlay_tile = s.get('OverlayTiling', 1)
        base = g.vector('SCALE', g.uv(0), scale=.1 * overlay_tile)
        base = g.mix(base, g.uv(0), s.get('Use UV0 as Overlay UVs', 0))
        base = g.mix(base, g.uv(1), s.get('Use UV1 as Overlay UVs', 0))
        x, y, _ = g.split(base)
        ouv = g.combine(g.add(g.mul(x, overlay_tile), s.get('UVOffset', 0)), g.add(g.mul(y, overlay_tile), s.get('UVOffset', 0)))
        if s.get("Use Rotate UV's", 0):
            from math import sin, cos, radians
            angle = radians(s.get('Rotation Angle', 0))
            x, y, _ = g.split(ouv);x, y = g.sub(x, .5), g.sub(y, .5)
            rotated = g.combine(g.add(g.sub(g.mul(x, cos(angle)), g.mul(y, sin(angle))), .5),
                                g.add(g.add(g.mul(x, sin(angle)), g.mul(y, cos(angle))), .5))
            ouv = g.mix(ouv, rotated, s.get("Use Rotate UV's", 0))
        overlay = g.image(t.get('Overlay', ''), ouv)
    if sw.get('UseOverlay') and overlay:
        mask = _concrete_range(g, overlay['r'], s.get('OverlayRange Min', 0), s.get('OverlayRange Max', 1))
        mask = g.mix(g.sub(1, mask), mask, s.get('OverlayRange Invert', 0))
        mod = g.cmul(_concrete_desaturate(g, overlay['color'], s.get('OverlayDesaturation', 0)), c.get('Overlay Color', (.5, .5, .5, 1)))
        color = g.mix(color, g.vector('SCALE', g.cmul(color, mod), scale=4), g.clamp(g.mul(mask, s.get('Overlay_BaseColor_Strength', 1))))
        rough = g.mix(rough, g.mul(g.mul(mask, s.get('Overlay_Roughness_Strength', 1)), g.mul(rough, 2)), g.clamp(mask))
    if sw.get('Enable Blend Textures') and overlay:
        blend_mask = _concrete_range(g, overlay['r'], s.get('Blend Mask Range Low', 0), s.get('Blend Mask Range High', 1))
        blend_color = g.image(t.get('CR Blend', ''), uv)
        blend_normal = g.image(t.get('NOH Blend', ''), uv, srgb=False)
        if blend_color:
            target = g.vector('SCALE', g.cmul(_concrete_desaturate(g, blend_color['color'], s.get('Blend Texture Desaturation', 0)), c.get('Blend Tint', (.5, .5, .5, 1))), scale=2)
            color = g.mix(color, target, blend_mask)
            rough = g.mix(rough, blend_color['a'], g.mul(blend_mask, s.get('BlendTextureAffectRoughness', 1)))
        if blend_normal:
            bx, by, bz = g.split(g.decode_normal(blend_normal))
            strength = s.get('Blend Texture Normal Strength', 1)
            nx = g.mix(nx, g.mul(bx, strength), blend_mask)
            ny = g.mix(ny, g.mul(by, strength), blend_mask)
            nz = g.mix(nz, g.add(1, g.mul(g.sub(bz, 1), strength)), blend_mask)
    # Global strength is after base+detail and the optional blend material.
    n = g.combine(g.mul(nx, s.get('Normal Strength', 1)), g.mul(ny, s.get('Normal Strength', 1)), nz)
    color = _concrete_desaturate(g, color, s.get('Desaturation', 0))
    color = g.vector('SCALE', g.cmul(color, c.get('Tint', (.5, .5, .5, 1))), scale=2)
    geometry = g.node('ShaderNodeNewGeometry', 'Concrete geometric facing')
    facing = g.vector('DOT_PRODUCT', geometry.outputs['Normal'], geometry.outputs['Incoming'])
    if sw.get('Use Paint'):
        coords = g.node('ShaderNodeTexCoord', 'Native paint object coordinates')
        local_scale = float(g.obj.data.get('arc_uemodel_scale', 1))
        height = g.div(g.split(coords.outputs['Object'])[2], local_scale)
        distance = g.node('ShaderNodeCameraData', 'Native paint camera distance')
        world_scale = float(g.obj.get('arc_ue_unit_scale', g.obj.data.get('arc_uemodel_scale', .01)))
        camera_cm = g.div(distance.outputs['View Distance'], world_scale)
        color, rough, n = _concrete_paint(g, r, color, rough, n, uv, overlay, height, camera_cm)
    rough, spec = _concrete_dry_fresnel(g, rough, s.get('Specular', .5), s.get('Fresnel Exponent', 5), facing)
    g.report['implemented_stages'] = ['current Concrete dry SM5 core', 'linear-sRGB desaturation', 'native packed-R overlay mask',
        'separate base/detail/blend normals and global strength', 'current Enable DetailNormal switch',
        'detail-B albedo', 'authored blend material colour/roughness/normal', 'RGBA local-height paint, thickness and roughness opacity',
        'geometric-view Fresnel roughness and specular']
    g.report['native_equation_references'] = ['70F7F9D5169546FB734883B5A88B29C50E1529C0 / 6B87D251919FEB7964A36C15D35BA6F545C8FCD5',
        'DAC0492F75DF53A7A47530B89A8D8E349620F8B4 / 8EC0E091472884FE4C5BDA1418C149BBFC7DA096']
    unsupported = [k for k in ['Use Waterline', 'TriPlanar Overlay', 'Paint_WorldSpaceHeight', 'UseFullPaint', 'TwoTone',
        'Flip Overlay on Paint', 'Enable Detail Roughness', 'UseVertexColorDestructionBlend', 'VirtualTextureBlend', 'IntersectionBlending'] if sw.get(k)]
    if unsupported:
        g.report['unsupported_features'] = unsupported
        g.report['warnings'].append('Concrete permutations still untraced: ' + ', '.join(unsupported))
    g.report['warnings'].append('Dry static material: native weather, snow, distance fade, VT intersections and engine lighting are not reconstructed')
    color = g.vector('MINIMUM', g.vector('MAXIMUM', color, (0, 0, 0)), (1, 1, 1))
    g.finish(color, g.clamp(rough), 0, g.vector('NORMALIZE', n), specular=spec)


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
