"""Packed PBR, concrete architecture, layered environment, and weapon V1 profiles."""
import re
import math


from ..texture_layouts import LAYOUTS, UNPROVEN_CHANNELS, param_layout
from ..audit_param_rules import WORLD_STATE_LAYERS, layer_of


def layout(code, param=None):
    proven = param_layout(param, code) if param else None
    if proven: return proven
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


COLOR_PARAMS = frozenset({'basecolor', 'base color', 'albedo', 'diffuse', 'color'})


def texture_code(param, ref):
    suffix = ref.rsplit('_', 1)[-1].split('.')[0].upper()
    if re.fullmatch('[CDNROMAESXHG]{1,4}', suffix): return suffix
    if re.fullmatch('[CDNROMAESXHG]{1,4}', param): return param
    low = param.lower()
    if low in COLOR_PARAMS: return 'C'
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
    if native_packed and not master.endswith('+nox+xem'):
        _packed_mask_gate(g, r)
    priority =sorted(r['textures'].items(), key=lambda kv: (kv[0].startswith('PM_') or kv[0].startswith('T_'), len(kv[0])))
    for name, ref in priority:
        if not ref or ref in seen: continue
        if name.startswith('TA_'): continue
        if prefix and not name.startswith(prefix): continue
        packed_mask = native_packed and master.endswith('+nox+xem') and name.casefold() == 'packed mask'
        # 'BaseColor/Mask' names its channels: RGB is the base colour, alpha a mask this profile leaves unwired.
        composite_color = '/' in name and name.split('/', 1)[0].strip().lower() in COLOR_PARAMS
        if not prefix and not composite_color and (name.startswith(('1.', '2.', '3.', '4.')) or (not packed_mask and any(word in name.lower() for word in ['overlay', 'detail', 'breakup', 'wear', 'id', 'mask', 'decal', 'dust', 'slice', 'blend', 'pattern']))): continue
        # Native parameter roles outrank a default texture's filename suffix:
        # e.g. CR binds T_Default_01_CA and Packed Mask binds T_Default_01_M.
        native_role = ('XEM' if packed_mask else 'CR' if native_packed and name == 'CR'
                       else ('NOX' if master.endswith('+nox+xem') else 'NOM')
                       if native_packed and name == 'Normal' else '')
        code = native_role or ('C' if composite_color else texture_code(name.removeprefix(prefix).strip(), ref))
        roles = [('color', 'color')] if composite_color else layout(code, name.removeprefix(prefix).strip())
        if not roles: continue
        seen.add(ref)
        if code in UNPROVEN_CHANNELS:
            _approximate(g, f'{code} texture {name}: RG normal only; {UNPROVEN_CHANNELS[code]} unproven, unwired')
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


def _norm(name):
    return re.sub(r'[^a-z0-9]', '', str(name).lower())


def _first(values, names, default):
    return next((values[k] for k in names if k in values), default)


def skip_textures(g, r, word, reason, keep=None):
    """Report every texture parameter carrying `word` (and passing `keep`) as intentionally unsampled."""
    skipped = g.report.setdefault('skipped_textures', {})
    for name, ref in r['textures'].items():
        if ref and word in name.lower() and (keep is None or keep(name)):
            skipped.setdefault(name, reason)


def is_breakup_param(name):
    """'breakup' param of the breakup layer; snow/frost/waterline 'breakup' masks belong to their world-state layer."""
    return 'breakup' in str(name).lower() and layer_of(name) not in WORLD_STATE_LAYERS


def _switch_gate(sw, pattern):
    """(known, on) for the switches whose normalized name fully matches `pattern`."""
    keys = [k for k in sw if re.fullmatch(pattern, _norm(k))]
    return bool(keys), any(bool(sw[k]) for k in keys), keys


PACKED_MASK_OFF = "don't use packed mask"


def _packed_mask_gate(g, r):
    """CR+NOM masters: the rebuild never samples a Packed Mask (only +NOX+XEM does).

    Switch "Don't use Packed Mask" True is the path built here. False with a
    Packed Mask texture bound means the game reads a mask this rebuild skips.
    """
    switches = {str(k).casefold(): v for k, v in (r.get('switches') or {}).items()}
    off = switches.get(PACKED_MASK_OFF)
    has_mask = any(str(k).casefold() == 'packed mask' and v for k, v in (r.get('textures') or {}).items())
    if off is True or (off is None and not has_mask):
        g.report.setdefault('implemented_stages', []).append('packed mask off (CR+NOM)')
    elif has_mask:
        _approximate(g, 'CR+NOM Packed Mask bound with "Don\'t use Packed Mask" off; mask channels not read')


def _approximate(g, text):
    g.report.setdefault('notes', []).append('approximate: ' + text)


# Masters spell the detail-normal static switch several ways; all gate the same Detail Normal Texture stage.
DETAIL_NORMAL_SWITCHES = ('Enable DetailNormal', 'Enable Detail Normal', 'EnableDetailNormal', 'Use Detail Normal', 'Use Detail')
# Texture parameter names of that stage (Interior_Walls spells it DetailTexture).
DETAIL_NORMAL_TEXTURES = ('Detail Normal Texture', 'Detail', 'Detail Normal', 'DetailTexture')


def detail_normal_enabled(sw):
    return any(sw.get(k) for k in DETAIL_NORMAL_SWITCHES)


def detail_normal_unswitched(sw):
    # prop-audit R39: the record carries the root's full static-switch set. Masters that compile a detail-normal
    # switch list it on every MI (Concrete_01 597/597, Metal 36/36, ConcreteDamage_01 20/20 in mi_params); masters
    # without one (ArchitecturePreset_CR+NOM+Detail 0/3, Trim+CR+NAH 0/109) sample Detail Normal unconditionally.
    return not any('detail' in str(k).casefold() for k in sw)


def detail_normal_tiling(s, default=1):
    for k in ('Custom Detail Normal Tiling', 'Detail Normal Tiling', 'Detail Normal Tile', 'Detail Tiling'):
        if k in s: return s[k]
    return default


def _layer_uv(g, s, uv_channel, tile):
    # Channel 1 is only read when the master asks for it, so a one-UV mesh never logs a false UV1 clamp.
    if s.get('Use UV1 as Overlay UVs', 0):
        return g.uv(1), True
    base = g.vector('SCALE', g.uv(uv_channel), scale=.1 * tile)
    if s.get('Use UV0 as Overlay UVs', 0):
        base = g.mix(base, g.uv(uv_channel), s['Use UV0 as Overlay UVs'])
    return base, False


BREAKUP_GATE = r'(enable|use)breakup(material|texture)s?'
OVERLAY_GATE = r'(use|enable)?(color)?overlay'


def breakup_layer(g, r, surf, uv_channel, tile):
    """Second material layer (Breakup CR/NOH or Breakup BaseColor/NormalMap) lerped over the base by a breakup mask.

    Keyed on parameter names containing 'breakup' and the Enable/Use Breakup Material/Texture switch. The masters'
    expression graphs are not exported (CachedExpressionData only), so the mask range and lerp are approximate.
    """
    s, c, t, sw = r['scalars'], r['colors'], r['textures'], r['switches']
    known, on, keys = _switch_gate(sw, BREAKUP_GATE)
    if not known:
        return surf
    if not on:
        skip_textures(g, r, 'breakup', f'{"/".join(keys)} off: breakup layer not compiled', is_breakup_param)
        return surf
    layer_uv = g.transform_uv(uv_channel, tile * _first(s, ['Breakup Tiling'], 1))
    mask_uv = g.transform_uv(uv_channel, tile * _first(s, ['Mask Tiling', 'BreakupMaskTiling'], 1))
    mask, mask_ref, layer = None, None, {}
    for name, ref in sorted(t.items()):
        low = name.lower()
        if not ref or not is_breakup_param(name):
            continue
        if 'mask' in low:
            mask_ref = mask_ref or (name, ref)
            continue
        code = texture_code('', ref) or ('C' if 'color' in low else 'N' if 'normal' in low else '')
        roles = layout(code)
        if not roles:
            continue
        sample = g.image(ref, layer_uv, srgb=None if any(role == 'color' for _, role in roles) else False, label=name, required=False)
        if not sample:
            continue
        for channel, role in roles:
            if role == 'normal':
                layer.setdefault('normal', g.decode_normal(sample, _first(s, ['Breakup Normal Strength'], 1)))
            else:
                layer.setdefault(role, sample[channel])
    if not layer:
        return breakup_mask_only(g, r, surf, mask_ref, mask_uv)
    if mask_ref:
        sample = g.image(mask_ref[1], mask_uv, srgb=False, label=mask_ref[0], required=False)
        mask = sample['r'] if sample else None
    if mask is None:
        # No dedicated mask parameter (Interior_Walls): the layer's own height drives it.
        mask = layer.get('height', .5)
        _approximate(g, 'breakup mask taken from the breakup layer height')
    low = _first(s, ['Breakup MaskRange_Low', 'Blending_RangeLow', 'Mask Low'], 0)
    high = _first(s, ['Breakup MaskRange_High', 'Blending_RangeHigh', 'Mask High'], 1)
    weight = remap(g, mask, low, high)
    weight = g.clamp(g.mul(weight, _first(s, ['BlendingOpacity', 'Mask Opacity'], 1)))
    if 'color' in layer:
        color = layer['color']
        tint = _first(c, ['Breakup Tint', 'Breakup Material Tint'], None)
        tint_gate = _first(sw, ['Use Breakup Tint', 'Enable Tinting Breakup'], True)
        if tint and tint_gate:
            color = g.cmul(color, tuple(v * 2 for v in tint[:3]) + (1,), 'Breakup neutral-grey tint')
        surf['color'] = g.mix(surf['color'], color, weight, 'Breakup layer colour')
    if 'rough' in layer:
        surf['rough'] = g.mix(surf['rough'], layer['rough'], weight)
    if 'normal' in layer:
        base = surf['normal'] if surf['normal'] is not None else (0, 0, 1)
        surf['normal'] = g.vector('NORMALIZE', g.mix(base, layer['normal'], weight, 'Breakup layer normal'))
    _approximate(g, 'breakup layer lerped by remapped mask; master expression graph not exported')
    g.report.setdefault('implemented_stages', []).append('breakup second layer (approximate mask lerp)')
    return surf


MASK_LOW = ['Breakup MaskRange_Low', 'Mask Range Low', 'MASK low', 'MaskLow', 'Mask Low']
MASK_HIGH = ['Breakup MaskRange_High', 'Mask Range High', 'MASK high', 'MaskHigh', 'Mask High']
MASK_TINT = ['Tint Color 2', '3. Variation Tint', 'Breakup Tint', 'TintColor']
MASK_ROUGH = ['3. Breakup Roughness', 'Breakup Roughness']


def breakup_mask_only(g, r, surf, mask_ref, mask_uv):
    """Breakup Mask with no second material (Residential VTblend, Metal presets): the mask varies tint and roughness.

    Keyed on the mask parameter plus a tint/roughness parameter the master exposes; without one the mask has
    nothing to drive and is reported as skipped. Mask range params follow the master's own names.
    """
    s, c = r['scalars'], r['colors']
    if not mask_ref:
        return surf
    tint = _first(c, MASK_TINT, None)
    rough = _first(s, MASK_ROUGH, None)
    if tint is None and rough is None:
        skip_textures(g, r, 'breakup mask', 'Breakup Mask has no tint/roughness parameter to drive on this master',
                      is_breakup_param)
        return surf
    sample = g.image(mask_ref[1], mask_uv, srgb=False, label=mask_ref[0], required=False)
    if not sample:
        return surf
    weight = remap(g, sample['r'], _first(s, MASK_LOW, 0), _first(s, MASK_HIGH, 1))
    weight = g.clamp(g.mul(weight, _first(s, ['MaskStrength', 'Breakup Multiply', 'Mask Opacity'], 1)))
    if tint is not None:
        surf['color'] = g.mix(surf['color'], g.cmul(surf['color'], tuple(v * 2 for v in tint[:3]) + (1,), 'Breakup mask tint'),
                              weight, 'Breakup mask colour variation')
    if rough is not None:
        surf['rough'] = g.mix(surf['rough'], g.add(surf['rough'], rough), weight)
    _approximate(g, 'breakup mask drives tint/roughness variation; master expression graph not exported')
    g.report.setdefault('implemented_stages', []).append('breakup mask variation (approximate)')
    return surf


ENV_OVERLAY_GATE = r'0?(use)?(triplanar)?overlay'


def env_overlay_layer(g, r, surf):
    """'4.. Overlay' colour/roughness overlay on the blended environment layers (M_Res_Architecture_Snow_01, HouseWall).

    Gated by '0. Use Triplanar Overlay' like the audit; projected with g.triplanar at '4. Overlay Size' when the graph
    supports it, else mesh UV0 at that tiling. Overlay Low/High remap and Strength/Brightness are approximate.
    """
    s, sw, t = r['scalars'], r['switches'], r['textures']
    ref = next((v for k, v in t.items() if v and _norm(k).endswith('overlay') and _norm(k)[:1].isdigit()), None)
    if not ref:
        return surf
    known, on, keys = _switch_gate(sw, ENV_OVERLAY_GATE)
    if not known:
        return surf  # static switch default not exported; leave as audited (tex_layer_skipped info)
    if not on:
        skip_textures(g, r, 'overlay', f'{"/".join(keys)} off: overlay layer not compiled')
        return surf
    size = max(_first(s, ['4. Overlay Size'], 100), .001)
    if hasattr(g, 'triplanar'):
        overlay = g.triplanar(ref, size, label='Environment overlay')
    else:
        overlay = g.image(ref, g.transform_uv(0, 100 / size), label='Environment overlay', required=False)
    if not overlay:
        return surf
    mask = remap(g, overlay['r'], _first(s, ['4. Overlay Low'], 0), _first(s, ['4. Overlay High'], 1))
    weight = g.clamp(g.mul(mask, _first(s, ['4. Overlay Strength'], 1)))
    mod = _concrete_desaturate(g, overlay['color'], _first(s, ['4. Overlay Desaturate'], 0))
    # Authored '4. Overlay Brightness' values sit around 0 (negatives included), so it is an offset, not a gain;
    # a gain would black out every overlay at the common value 0.
    bright = float(_first(s, ['4. Overlay Brightness'], 0))
    if bright:
        mod = g.vector('ADD', mod, (bright, bright, bright))
    mod = g.vector('SCALE', mod, scale=2)
    surf['color'] = g.mix(surf['color'], g.cmul(surf['color'], mod), weight, 'Environment overlay colour')
    if '4. Overlay Roughness' in s:
        surf['rough'] = g.mix(surf['rough'], g.add(surf['rough'], s['4. Overlay Roughness']), weight)
    _approximate(g, 'environment overlay multiply; master expression graph not exported')
    g.report.setdefault('implemented_stages', []).append('environment overlay layer (approximate)')
    return surf


def overlay_layer(g, r, surf, uv_channel, tile):
    """Overlay colour texture (UseOverlay / EnableOverlay / Color Overlay) multiplied over the base colour.

    Follows the Concrete_01 overlay parameter set (OverlayTiling, OverlayRange, Overlay_*_Strength) when present;
    the per-master expression graph is not exported, so other masters are approximate.
    """
    s, c, t, sw = r['scalars'], r['colors'], r['textures'], r['switches']
    known, on, _ = _switch_gate(sw, OVERLAY_GATE)
    ref = t.get('Overlay')
    if not (known and on and ref):
        return surf, False
    base, direct = _layer_uv(g, s, uv_channel, tile)
    overlay_tile = _first(s, ['OverlayTiling', 'Overlay Tiling'], 1)
    if direct:
        ouv = base
    else:
        x, y, _ = g.split(base)
        ouv = g.combine(g.add(g.mul(x, overlay_tile), s.get('UVOffset', 0)), g.add(g.mul(y, overlay_tile), s.get('UVOffset', 0)))
    overlay = g.image(ref, ouv, label='Overlay', required=False)
    if not overlay:
        return surf, False
    if any(sw.get(k) for k in sw if 'triplanar' in _norm(k) and 'overlay' in _norm(k)):
        _approximate(g, 'triplanar overlay projected on mesh UVs')
    if 'OverlayRange Min' in s or 'OverlayRange Max' in s:
        mask = _concrete_range(g, overlay['r'], s.get('OverlayRange Min', 0), s.get('OverlayRange Max', 1))
        mask = g.mix(g.sub(1, mask), mask, s.get('OverlayRange Invert', 0))
    else:
        mask = 1
    strength = _first(s, ['Overlay_BaseColor_Strength', 'OverlayIntensity', 'Overlay Strength'], 1)
    tint = _first(c, ['Overlay Color', 'OverlayTint'], (.5, .5, .5, 1))
    mod = g.cmul(_concrete_desaturate(g, overlay['color'], s.get('OverlayDesaturation', 0)), tint)
    surf['color'] = g.mix(surf['color'], g.vector('SCALE', g.cmul(surf['color'], mod), scale=4), g.clamp(g.mul(mask, strength)), 'Overlay colour')
    if 'Overlay_Roughness_Strength' in s:
        surf['rough'] = g.mix(surf['rough'], g.mul(g.mul(mask, s['Overlay_Roughness_Strength']), g.mul(surf['rough'], 2)), g.clamp(mask))
    elif 'OverlayRoughnessMultiplier' in s:
        surf['rough'] = g.mix(surf['rough'], g.mul(surf['rough'], s['OverlayRoughnessMultiplier']), g.clamp(mask))
    _approximate(g, 'overlay uses the Concrete_01 overlay equation')
    g.report.setdefault('implemented_stages', []).append('overlay colour layer (approximate)')
    return surf, True


WATERLINE_GATE = r'(use|enable)?(waterline|puddles?|wetness)'
PUDDLE_MASK, PUDDLE_SWITCH = 'Puddles Mask - Linear Grayscale', 'Puddles'
WET_DARKEN, WET_ROUGHNESS = .45, .05


def waterline_layer(g, r, surf, uv_channel=0, tile=1):
    """MI-enabled 'Puddles' / waterline wet layer: mask darkens base colour, pulls roughness wet, flattens the normal.

    Gated strictly by the Puddles/Use Waterline switch being True; off leaves the mask reported as skipped. The masters
    expose only the mask, 'Puddles Tiling' and 'PuddlesTriplanarSize' (no colour/roughness/darkening params), so the
    wet response is a fixed constant set and the world-height / triplanar projection uses mesh UVs at Puddles Tiling.
    """
    s, t, sw = r['scalars'], r['textures'], r['switches']
    known, on, keys = _switch_gate(sw, WATERLINE_GATE)
    if not known:
        return surf, False
    if not on:
        for word in ('puddle', 'waterline'):
            skip_textures(g, r, word, f'{"/".join(keys)} off: waterline layer not compiled')
        return surf, False
    ref = t.get(PUDDLE_MASK) or next((v for k, v in t.items() if v and re.search('puddle|waterline|wet', _norm(k))), None)
    if not ref:
        return surf, False
    puddle_tile = _first(s, ['Puddles Tiling', 'Waterline Tiling'], 1)
    mask = g.image(ref, g.transform_uv(uv_channel, tile * puddle_tile), srgb=False, label='Puddles mask', required=False)
    if not mask:
        return surf, False
    weight = g.clamp(mask['r'])
    surf['color'] = g.mix(surf['color'], g.vector('SCALE', surf['color'], scale=WET_DARKEN), weight, 'Puddles wet darkening')
    surf['rough'] = g.mix(surf['rough'], WET_ROUGHNESS, weight)
    base_normal = surf.get('normal') if surf.get('normal') is not None else (0, 0, 1)
    surf['normal'] = g.vector('NORMALIZE', g.mix(base_normal, (0, 0, 1), weight, 'Puddles flat normal'))
    _approximate(g, f'puddles wet response uses fixed darken {WET_DARKEN} / roughness {WET_ROUGHNESS}; master exposes no '
                    'wet colour params; world-height and PuddlesTriplanarSize projection use mesh UVs at Puddles Tiling')
    g.report.setdefault('implemented_stages', []).append(f'{PUDDLE_SWITCH.lower()} waterline wet layer (approximate)')
    return surf, True


FROST_GATE = r'(use|enable)?(prop)?frost'
FROST_MASK, FROST_SWITCH, FROST_COLOR = 'Prop Frost Mask', 'Use Frost', (.8, .84, .88)


def frost_layer(g, r, surf, uv):
    """MI-enabled 'Use Frost' layer: 'Prop Frost Mask' R lerps base colour to a pale frost tint and roughness to
    'Frost Roughness'. Gated strictly by the switch being True. 'Frost Fresnel Mask Breakup' (_NM suffix, normal-like
    texels, channel layout unknown) and the view-angle fresnel term are not sampled."""
    s, t, sw = r['scalars'], r['textures'], r['switches']
    known, on, keys = _switch_gate(sw, FROST_GATE)
    if not known:
        return surf, False
    if not on:
        skip_textures(g, r, 'frost', f'{"/".join(keys)} off: frost layer not compiled')
        return surf, False
    mask = g.image(t.get(FROST_MASK, ''), uv, srgb=False, label='Frost mask', required=False)
    if not mask:
        return surf, False
    weight = g.clamp(mask['r'])
    surf['color'] = g.mix(surf['color'], FROST_COLOR, weight, 'Frost colour')
    surf['rough'] = g.mix(surf['rough'], _first(s, ['Frost Roughness'], 1.0), weight)
    skip_textures(g, r, 'frost', 'frost fresnel breakup: _NM layout unknown, fresnel term not reconstructed',
                  keep=lambda name: 'fresnel' in name.lower())
    _approximate(g, f'frost colour fixed at {FROST_COLOR} (master exposes no frost colour); fresnel breakup and view-angle '
                    'response not reconstructed')
    g.report.setdefault('implemented_stages', []).append(f'prop frost mask layer (approximate, {FROST_SWITCH})')
    return surf, True


SNOW_GATE = r'(use|enable)?(perma)?snow'
SNOW_COLOR, SNOW_ROUGHNESS = (.9, .92, .95), .6


def snow_layer(g, r, surf, uv):
    """MI-enabled 'Use Snow' / 'Use Perma Snow' layer. Gated strictly by the switch being True (snow is world state:
    off leaves its textures reported skipped). On: 'Snow Breakup Mask' (R, Snow Breakup Tiling) x optional
    'Snow Breakup Mask 2' x amount, range-mapped by Snow Mask Min/Max, lerps colour to 'Snow Base Color' (or a fixed
    white), roughness to snow roughness and the normal to 'Snow Normal' (Snow Tiling)."""
    s, t, sw = r['scalars'], r['textures'], r['switches']
    known, on, keys = _switch_gate(sw, SNOW_GATE)
    if not known:
        return surf, False
    if not on:
        skip_textures(g, r, 'snow', f'{"/".join(keys)} off: snow layer not compiled')
        return surf, False
    snow_tile = _first(s, ['Snow Tiling'], 1)
    mask = g.image(t.get('Snow Breakup Mask', ''), g.vector('SCALE', uv, scale=_first(s, ['Snow Breakup Tiling'], 1)),
                   srgb=False, label='Snow breakup mask', required=False)
    weight = g.clamp(mask['r']) if mask else 1
    mask2 = g.image(t.get('Snow Breakup Mask 2', ''), g.vector('SCALE', uv, scale=_first(s, ['Snow Breakup 2 Tiling'], 1)),
                    srgb=False, label='Snow breakup mask 2', required=False)
    if mask2:
        weight = g.mul(weight, g.mix(1, mask2['r'], max(0, min(1, _first(s, ['Snow Breakup 2 Strength'], 1)))))
    low, high = _first(s, ['Snow Mask Min', '10. Snow Min'], 0), _first(s, ['Snow Mask Max', '10. Snow Max'], 1)
    span = float(high) - float(low)
    weight = g.clamp(g.div(g.sub(weight, low), span)) if span > 0 else g.math('GREATER_THAN', weight, low)
    weight = g.clamp(g.mul(weight, _first(s, ['10. Snow Amount', 'Snow Amount', 'Snow Coverage'], 1)))
    base = g.image(t.get('Snow Base Color', ''), g.vector('SCALE', uv, scale=snow_tile), srgb=True,
                   label='Snow base colour', required=False)
    surf['color'] = g.mix(surf['color'], base['color'] if base else SNOW_COLOR, weight, 'Snow colour')
    surf['rough'] = g.mix(surf['rough'], SNOW_ROUGHNESS, weight)
    nrm = g.image(t.get('Snow Normal', ''), g.vector('SCALE', uv, scale=snow_tile), srgb=False,
                  label='Snow normal', required=False)
    if nrm and 'normal' in surf:
        base_normal = surf['normal'] if surf['normal'] is not None else (0, 0, 1)
        surf['normal'] = g.vector('NORMALIZE', g.mix(base_normal, g.decode_normal(nrm, 1), weight, 'Snow normal'))
    _approximate(g, 'snow weight is breakup mask x amount (no world-up / vertex-colour term); snow roughness fixed at '
                    f'{SNOW_ROUGHNESS}')
    g.report.setdefault('implemented_stages', []).append(f'snow layer (approximate, {"/".join(keys)})')
    return surf, True


RUST_SWITCHES = ('1. Use Rust', 'Use Rust', 'UseRust')
RUST_MASK_SWITCH = '3. Use Additional Rust Mask'
VX_SWITCH = '8  Use VX Coloring'


def _vertex_mask(g):
    """Red of the first colour attribute; uniform 1 when the mesh (or test graph) has none."""
    obj = getattr(g, 'obj', None)
    attrs = getattr(getattr(obj, 'data', None), 'color_attributes', None)
    if not attrs:
        return 1
    from .masked import vertex
    return vertex(g)['r']


def weathering_layers(g, r, surf, uv):
    """Rust (Architecture Metal / Wrh wall / VT blend), VX colouring, vegetation second and flower tints.

    approximate: masks and blend weights are inferred from parameter names; the
    master graphs are not traced. Rust without its additional mask uses the
    surface cavity (1 - AO); flower tint covers the whole surface at Tint Amount.
    """
    s, c, sw, t = r['scalars'], r['colors'], r['switches'], r['textures']
    stages = g.report.setdefault('implemented_stages', [])
    if any(sw.get(k) for k in RUST_SWITCHES):
        ref = t.get('3.1 Additional Rust Mask') if sw.get(RUST_MASK_SWITCH) else None
        tex = g.image(ref, uv, srgb=False, label='3.1 Additional Rust Mask', required=False) if ref else None
        if tex:
            mask = g.mul(tex['r'], s.get('3.2 Additional Rust Mask Opacity', 1))
        else:
            ao = surf.get('ao', 1)
            mask = g.sub(1, ao) if ao is not None else 0
        low, high = s.get('RustMask_Low', s.get('Rust Mask Low')), s.get('RustMask_High', s.get('Rust Mask High'))
        if low is not None or high is not None:
            mask = remap(g, mask, low or 0, 1 if high is None else high)
        amount = _first(s, ('Rust Multiply', 'Rust Strength'), 1)
        mask = g.clamp(g.mul(mask, amount))
        tint = _first(c, ('Rust Tint',), (.35, .14, .05, 1))
        rust = desaturate(g, surf['color'], _first(s, ('7. Rust Desaturation', 'Rust Desaturation'), 0))
        rust = g.cmul(rust, tint, 'Rust Tint')
        rust = g.vector('SCALE', rust, scale=s.get('6. Rust Luminosity', 1))
        surf['color'] = g.mix(surf['color'], rust, mask, 'Rust')
        surf['rough'] = g.mix(surf['rough'], 1, mask, 'Rust roughness')
        _approximate(g, 'rust mask from ' + ('3.1 Additional Rust Mask' if tex else 'cavity (1 - AO)')
                     + ' x Rust Multiply/Strength; colour = desaturated base x Rust Tint; roughness toward 1')
        stages.append('rust layer (approximate)')
    if sw.get(VX_SWITCH) and '8.4 VX Color' in c:
        mask = g.clamp(g.mul(_vertex_mask(g), s.get('8.3 VXOpacity', 1)))
        surf['color'] = g.mix(surf['color'], g.cmul(surf['color'], c['8.4 VX Color'], '8.4 VX Color'), mask, 'VX colouring')
        _approximate(g, 'VX colouring: vertex red x 8.3 VXOpacity multiplies base by 8.4 VX Color; value shifts not read')
        stages.append('VX colouring (approximate)')
    if sw.get('Second Tint') and '2:nd Leaves Tint' in c:
        tint = tuple(v * 2 for v in c['2:nd Leaves Tint'][:3]) + (1,)
        surf['color'] = g.mix(surf['color'], g.cmul(surf['color'], tint, '2:nd Leaves Tint'),
                              s.get('2:nd Tint Amount', s.get('2:nd Tint Leaves', 1)), 'Second tint')
        _approximate(g, 'Second Tint: neutral-grey 2:nd Leaves Tint at 2:nd Tint Amount over the whole surface')
        stages.append('vegetation second tint (approximate)')
    if sw.get('Tint Flowers') and 'Flower Tint' in c:
        tint = tuple(v * 2 for v in c['Flower Tint'][:3]) + (1,)
        surf['color'] = g.mix(surf['color'], g.cmul(surf['color'], tint, 'Flower Tint'), s.get('Tint Amount', 1), 'Flower tint')
        _approximate(g, 'Tint Flowers: neutral-grey Flower Tint at Tint Amount over the whole surface; flower mask not traced')
        stages.append('vegetation flower tint (approximate)')
    return surf


def metal_wear_layer(g, r, surf, uv_channel, tile):
    """R17: Metal's 'Masks' wear mask and 'Detail Normal Worn' worn-area detail normal."""
    s, sw, t = r['scalars'], r['switches'], r['textures']
    stages = g.report.setdefault('implemented_stages', [])
    mask = None
    if t.get('Masks'):
        tex = g.image(t['Masks'], g.transform_uv(uv_channel, tile), srgb=False, label='Masks', required=False)
        if tex:
            mask = g.clamp(g.mul(tex['r'], s.get('Wear Color Multiply', 1)))
            _approximate(g, "Metal 'Masks' channel layout not exported; R read as the wear mask")
            surf['rough'] = g.mix(surf['rough'], g.mul(surf['rough'], 0.8), mask, 'Metal wear roughness')
            stages.append('metal wear mask (approximate)')
    ref = t.get('Detail Normal Worn')
    if ref and detail_normal_enabled(sw):
        worn = g.image(ref, g.transform_uv(uv_channel, tile * s.get('4.Detail Wear Tile', 1)), srgb=False, label='Detail Normal Worn', required=False)
        if worn:
            base = surf['normal'] if surf['normal'] is not None else (0, 0, 1)
            blended = g.rnm(base, g.decode_normal(worn, s.get('2.Detail Wear Normal Intensity', 1)))
            surf['normal'] = g.vector('NORMALIZE', g.mix(base, blended, mask, 'Worn detail normal')) if mask is not None else blended
            _approximate(g, "Metal worn detail normal blended by the wear mask; compiled wear blend not traced")
            stages.append('metal worn detail normal (approximate)')
    return surf


def mural_mask_alpha(g, r, surf, uv_channel):
    """R17: Mural masked opacity multiplies the tiled 'Mask' texture into alpha."""
    s, t = r['scalars'], r['textures']
    ref = t.get('Mask')
    if not ref:
        return surf
    scale = (s.get('MaskTiling_U', 1), s.get('MaskTiling_V', s.get('MaskTiling_U', 1)))
    tex = g.image(ref, g.transform_uv(uv_channel, scale, (s.get('Offset_U', 0), s.get('Offset_V', 0))), srgb=False, label='Mask', required=False)
    if tex:
        surf['alpha'] = tex['r'] if surf.get('alpha') is None else g.mul(surf['alpha'], tex['r'])
        _approximate(g, "Mural 'Mask' R multiplied into opacity; breakup/noise opacity terms not reconstructed")
        g.report.setdefault('implemented_stages', []).append('mural opacity mask (approximate)')
    return surf


def nature_detail_array(g, r, surf):
    master = str(r.get('master', '')).rsplit('/', 1)[-1].casefold()
    switches = r['switches']
    if master != 'm_nature_01_tbg' or switches.get('2. Detail Texture') is not True:
        return surf
    unproven = ('1. Tint', '3. Use Moss', '3.Top Down Moss', '6. Vt Blend Mask', 'BaseColorOnly',
                'Enable BlueGate Custom Masks', 'EnableLIDAR', 'EnableQuest', 'ExcludeMossMask',
                'UseOpacityMask', 'VCMask', 'VirtualTextureBlend')
    if any(switches.get(name) for name in unproven):
        g.report['warnings'].append('Nature detail array permutation not reconstructed: enabled mask, tint or scene layer')
        return surf
    reference = r['textures'].get('2. Detail Array')
    index = getattr(g, 'index', None)
    info = index.texture_info(reference) if reference and hasattr(index, 'texture_info') else None
    info = info or {}
    depths = [mip.get('SizeZ') for mip in info.get('Mips', ()) if isinstance(mip, dict) and 'SizeZ' in mip]
    if ((info.get('NativeTextureKind') or info.get('Type')) != 'Texture2DArray' or not depths
            or any(isinstance(depth, bool) or not isinstance(depth, int) or not 0 < depth <= 2048 for depth in depths)
            or len(set(depths)) != 1):
        g.report['warnings'].append('Nature detail array needs exact native mip depth; PNG count is not authority')
        return surf
    scalars = r['scalars']
    base_uv = g.transform_uv(0, 1)
    base_color = g.image(r['textures'].get('CR', ''), base_uv)
    base_normal = g.image(r['textures'].get('NOH', ''), base_uv, srgb=False)
    if not base_color or not base_normal:
        return surf
    detail_uv = g.transform_uv(0, (scalars.get('2. Detail Tile U', 8), scalars.get('2. Detail Tile V', 8)))
    slice_coordinate = g.add(g.math('FLOOR', g.mul(base_color['a'], scalars.get('2. Number of Slices', 2))),
                             scalars.get('2. Starting Slice', 0))
    samples = [g.image(reference, detail_uv, layer=layer, srgb=False, label='Nature detail layer ' + str(layer))
               for layer in range(depths[0])]
    if any(sample is None for sample in samples):
        raise FileNotFoundError('Incomplete active Nature detail array: ' + str(reference))
    detail = {channel: samples[0][channel] for channel in ('r', 'g', 'b', 'a')}
    for layer, sample in enumerate(samples[1:], 1):
        choose = (g.math('GREATER_THAN', slice_coordinate, layer - .5) if layer % 2
                  else g.sub(1, g.math('LESS_THAN', slice_coordinate, layer - .5)))
        detail = {channel: g.mix(detail[channel], sample[channel], choose) for channel in detail}
    camera = g.node('ShaderNodeCameraData', 'Nature detail camera distance')
    world_scale = float(g.obj.get('arc_ue_unit_scale', g.obj.data.get('arc_uemodel_scale', .01)))
    from math import isfinite
    if not isfinite(world_scale) or world_scale <= 0:
        raise ValueError('Nature detail camera distance requires a positive Unreal unit scale')
    far = g.math('GREATER_THAN', g.div(camera.outputs['View Distance'], world_scale), 5000)
    encoded_x = g.mix(g.mul(g.mul(base_normal['r'], detail['r']), 2), base_normal['r'], far)
    encoded_y = g.mix(g.mul(g.mul(base_normal['g'], detail['g']), 2), base_normal['g'], far)
    normal_x = g.sub(g.mul(encoded_x, 2), 1)
    normal_y = g.sub(g.mul(encoded_y, 2), 1)
    normal_z = g.math('SQRT', g.math('MAXIMUM', g.sub(1, g.add(g.mul(normal_x, normal_x), g.mul(normal_y, normal_y))), 0))
    strength = scalars.get('1. Normal Strength', 1)
    surf['normal'] = g.combine(g.mul(normal_x, strength), g.mul(normal_y, strength),
                               g.add(1, g.mul(g.sub(normal_z, 1), strength)))
    detail_gain = g.sub(g.mul(g.add(g.mul(detail['b'], detail['b']), .2), 2), 1)
    near_color = g.vector('SCALE', base_color['color'],
                          scale=g.add(1, g.mul(detail_gain, scalars.get('2. Detail Color', 1))))
    surf['color'] = g.mix(near_color, base_color['color'], far)
    g.report.setdefault('implemented_stages', []).append('Nature native detail array normal/color and distance switch')
    g.report['warnings'].append('Nature local detail stage traced from SM5; runtime VT, snow, final roughness and PDO remain unreconstructed')
    return surf


def build_surface(g, r):
    g.section('Packed surface textures')
    s, c, sw = r['scalars'], r['colors'], r['switches']
    master = str(r.get('master', '')).rsplit('/', 1)[-1].casefold()
    # The cloth-sim item masters' Use UV1 drives the cloth VAT lookup row: MI_WhiteFlag_01_A's base pass (PS D0B69E08,
    # local vertex fetch VS 156) samples every texture on mesh UV0.
    use_uv1 = sw.get('Use UV1') and 'clothsim' not in master
    # approximate: Metal's numbered '4. Use UV2' is read as the same UV2 select as 'Use UV2'.
    uv_channel = 2 if sw.get('Use UV2') or sw.get('UseUV2') or sw.get('4. Use UV2') else 1 if use_uv1 or sw.get('1. Use Texture Coord1 For Tiling') else 0
    if uv_channel and collapsed_uv(getattr(g, 'obj', None), uv_channel):
        g.report['warnings'].append(f'UV{uv_channel} spans no area on this mesh (a lookup row, not a layout); textures use UV0')
        uv_channel = 0
    tile = s.get('Tiling', s.get('UV Tiling', s.get('UVTiling', 1)))
    uv = g.transform_uv(uv_channel, tile, (s.get('UVOffset_U', 0), s.get('UVOffset_V', 0)))
    if sw.get('Orientation is Y'):
        # Cable/wire masters lay the strand along V when set: swap U and V before sampling.
        g.report['warnings'].append('approximate: Orientation is Y swaps U and V; compiled cable UV path not traced')
        ux, uy, _ = g.split(uv)
        uv = g.combine(uy, ux)
    surf = gather(g, r, uv=uv)
    surf = nature_detail_array(g, r, surf)
    use_tint = next((bool(sw[k]) for k in ['Use Tinting', 'Use Tint', 'UseTint'] if k in sw), False)
    if c.get('Tint') and use_tint:
        tint = tuple(v * 2 for v in c['Tint'][:3]) + (1,)
        surf['color'] = g.cmul(surf['color'], tint, 'Authored neutral-grey tint')
        g.report['warnings'].append('Generic Tint assumes the neutral 0.5 convention; verify unusual master families')
    surf = weathering_layers(g, r, surf, uv)
    surf = breakup_layer(g, r, surf, uv_channel, tile)
    surf, overlay_built = overlay_layer(g, r, surf, uv_channel, tile)
    surf, waterline_built = waterline_layer(g, r, surf, uv_channel, tile)
    surf, _ = snow_layer(g, r, surf, uv)
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
    unswitched = not detail_normal_enabled(sw) and detail_normal_unswitched(sw)
    if detail_normal_enabled(sw) or unswitched:
        ref = next((r['textures'][k] for k in DETAIL_NORMAL_TEXTURES if r['textures'].get(k)), None)
        if ref:
            if unswitched:
                _approximate(g, 'switch-less master: Detail Normal read as an always-on RNM stage (no static switch in root set)')
            elif not (sw.get('Enable DetailNormal') or sw.get('Use Detail')):
                _approximate(g, 'detail normal switch spelling variant read as the traced Enable DetailNormal RNM stage')
            detail = g.image(ref, g.transform_uv(uv_channel, tile * detail_normal_tiling(s)), srgb=False)
            if detail: surf['normal'] = g.rnm(surf['normal'] if surf['normal'] is not None else (0, 0, 1), g.decode_normal(detail, s.get('Detail Normal Strength', 1)))
    if master == 'm_architecturepreset_metal':
        surf = metal_wear_layer(g, r, surf, uv_channel, tile)
    if master.startswith('m_muralpreset'):
        surf = mural_mask_alpha(g, r, surf, uv_channel)
    complex_words = (['dust', 'paint', 'slice', 'blend texture', 'pattern', 'tint mask'] + ([] if overlay_built else ['overlay'])
                     + ([] if waterline_built else ['waterline']))
    active_complex = [k for k, v in sw.items() if v and any(w in k.lower() for w in complex_words)
                      and not (overlay_built and re.fullmatch(OVERLAY_GATE, _norm(k)))]
    if active_complex:
        g.report['unsupported_features'] = sorted(set(active_complex))
        g.report['warnings'].append('Additional surface stages not reconstructed: ' + ', '.join(active_complex))
    g.report['implemented_stages'] = ['packed texture channels', 'Unreal UV transforms', 'DirectX normals', 'detail normals', 'linear material parameters'] + g.report.get('implemented_stages', [])
    if 'emission_preview' in g.report:
        g.report['implemented_stages'].append('original generic emission mask/hue/gain preview; not native-verified')
    alpha = surf['alpha']
    blend = str(r['settings'].get('BlendMode', ''))
    if blend.rsplit('::', 1)[-1].casefold() in {'blend_opaque', 'opaque', '0'}:
        alpha = 1
    elif 'Masked' in blend or blend == '1':
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
    fade = s.get('Enable_ColorOverlayDistanceFade', 0)
    if amount == 0 and not fade:
        # Zero-weight lerp: the Overlay RGB canvas cannot reach paint colour, so it is not wired in.
        paint_color = paint
    else:
        distance_amount = g.mix(.1, amount, g.clamp(g.mul(g.sub(camera_distance, 500), .00033333332976326346)))
        amount = g.mix(amount, distance_amount, fade)
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
    detail_enabled = detail_normal_enabled(sw)
    detail = None
    if detail_enabled or sw.get('Enable Detail Albedo') or sw.get('Enable Detail Roughness'):
        detail = g.image(t.get('Detail Normal Texture', ''), g.transform_uv(0, tile * s.get('Detail Normal Tiling', 1)), srgb=False)
    if detail_enabled and detail:
        dx, dy, _ = g.split(g.decode_normal(detail))
        nx = g.add(nx, g.mul(dx, s.get('Detail Normal Strength', 1)))
        ny = g.add(ny, g.mul(dy, s.get('Detail Normal Strength', 1)))
    if sw.get('Enable Detail Albedo') and detail:
        color = g.mix(color, g.cmul(color, g.mul(detail['b'], 2)), s.get('Detail Albedo Strength', 1))
    if sw.get('Enable Detail Roughness') and detail:
        rough = g.mix(rough, g.mul(rough, g.mul(detail['a'], 2)), s.get('Detail Roughness Strength', 1))
    overlay = None
    if sw.get('UseOverlay') or sw.get('Enable Blend Textures') or sw.get('Use Paint'):
        overlay_tile = s.get('OverlayTiling', 1)
        base = g.vector('SCALE', g.uv(0), scale=.1 * overlay_tile)
        base = g.mix(base, g.uv(0), s.get('Use UV0 as Overlay UVs', 0))
        if s.get('Use UV1 as Overlay UVs', 0):
            # Only read UV1 when the master selects it; a zero-weight mix still logged a false UV1 clamp.
            base = g.mix(base, g.uv(1), s['Use UV1 as Overlay UVs'])
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
    if not sw.get('Enable Blend Textures'):
        reason = 'Enable Blend Textures off'
        if sw.get('IntersectionBlending') or sw.get('VirtualTextureBlend'):
            reason += '; IntersectionBlending/VirtualTextureBlend need scene depth or runtime VT data, not reconstructed'
        for name in ('CR Blend', 'NOH Blend'):
            if t.get(name):
                g.report.setdefault('skipped_textures', {}).setdefault(name, reason)
    if not sw.get('Use Paint') and t.get('PaintBreakup'):
        g.report.setdefault('skipped_textures', {}).setdefault('PaintBreakup', 'Use Paint off: paint layer not compiled')
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
        'detail-B albedo', 'detail-A roughness', 'authored blend material colour/roughness/normal', 'RGBA local-height paint, thickness and roughness opacity',
        'geometric-view Fresnel roughness and specular']
    g.report['native_equation_references'] = ['70F7F9D5169546FB734883B5A88B29C50E1529C0 / 6B87D251919FEB7964A36C15D35BA6F545C8FCD5',
        'DAC0492F75DF53A7A47530B89A8D8E349620F8B4 / 8EC0E091472884FE4C5BDA1418C149BBFC7DA096',
        '6EBBF2F49541AC4CF4E8BB56A40B426ED05956E9 / 2DD4B24DE327A2686F8AE87F54356D9D5DE7A69B']
    unsupported = [k for k in ['Use Waterline', 'TriPlanar Overlay', 'Paint_WorldSpaceHeight', 'UseFullPaint', 'TwoTone',
        'Flip Overlay on Paint', 'UseVertexColorDestructionBlend', 'VirtualTextureBlend', 'IntersectionBlending'] if sw.get(k)]
    if unsupported:
        g.report['unsupported_features'] = unsupported
        g.report['warnings'].append('Concrete permutations still untraced: ' + ', '.join(unsupported))
    g.report['warnings'].append('Dry static material: native weather, snow, distance fade, VT intersections and engine lighting are not reconstructed')
    color = g.vector('MINIMUM', g.vector('MAXIMUM', color, (0, 0, 0)), (1, 1, 1))
    g.finish(color, g.clamp(rough), 0, g.vector('NORMALIZE', n), specular=spec)


def _housewall_normals(g, r, mask):
    master = str(r.get('master', '')).rsplit('/', 1)[-1].split('.')[0]
    if master != 'M_Res_HouseWallPreset' or r['switches'].get('0. Use Triplanar') is not True:
        return None
    textures, scalars = r['textures'], r['scalars']
    if not all(textures.get(name) for name in ('1. NOH', '2. NOH', '1. Detail')):
        return None
    defaults = {'Normal Strength': 1, 'Blend Normal Strength': 1, '1. Detail Normal Strength': 1,
                '1. Texture Size': 512, '2. Texture Size': 512, '1. Detail Tiling': 1}
    if not all(isinstance(scalars.get(name, default), (int, float)) and math.isfinite(scalars.get(name, default))
               for name, default in defaults.items()):
        return None
    first = g.triplanar(textures['1. NOH'], max(scalars.get('1. Texture Size', 512), .001), srgb=False)
    second = g.triplanar(textures['2. NOH'], max(scalars.get('2. Texture Size', 512), .001), srgb=False)
    detail = g.image(textures['1. Detail'], g.transform_uv(0, scalars.get('1. Detail Tiling', 1)), srgb=False)
    if not first or not second or not detail:
        return None
    def decode(sample, strength):
        horizontal = g.sub(g.mul(sample['r'], 2), 1)
        vertical = g.sub(g.mul(sample['g'], 2), 1)
        squared = g.add(g.mul(horizontal, horizontal), g.mul(vertical, vertical))
        depth = g.math('SQRT', g.math('MAXIMUM', g.sub(1, squared), 0))
        return g.combine(g.mul(horizontal, strength), g.mul(vertical, strength), depth)
    base = g.mix(decode(first, scalars.get('Normal Strength', 1)),
                 decode(second, scalars.get('Blend Normal Strength', 1)), mask)
    normal = g.rnm(base, decode(detail, scalars.get('1. Detail Normal Strength', 1)))
    g.report.setdefault('implemented_stages', []).append('HouseWall sampled normal composition (SM5-conditional)')
    g.report['warnings'].append('HouseWall normal arithmetic is SM5-conditional; projection, blend-mask selection and tangent basis remain approximate')
    return normal


def _housewall_roughness(g, r, samples, mask):
    master = str(r.get('master', '')).rsplit('/', 1)[-1].split('.')[0]
    if master != 'M_Res_HouseWallPreset' or r['switches'].get('0. Use Triplanar') is not True:
        return None
    if len(samples) != 2 or not all(samples):
        return None
    scales = [r['scalars'].get(name) for name in ('1. Roughness', '2. Roughness')]
    if not all(type(value) in (int, float) and math.isfinite(value) for value in scales):
        return None
    g.report.setdefault('implemented_stages', []).append('HouseWall sampled roughness scaling')
    g.report['warnings'].append('approximate: HouseWall roughness uses the pre-Fresnel sampled stage; weather, Fresnel and View adjustments remain unverified')
    return g.mix(g.mul(samples[0]['a'], scales[0]), g.mul(samples[1]['a'], scales[1]), mask)


def build_environment_layers(g, r):
    g.section('Environment layers — separate texture scales')
    s, sw = r['scalars'], r['switches']
    triplanar = bool(sw.get('0. Use Triplanar'))
    layers = []
    roughness_samples = []
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
        roughness_samples.append(cr if triplanar else None)
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
    housewall_roughness = _housewall_roughness(g, r, roughness_samples, mask)
    if housewall_roughness is not None:
        rough = housewall_roughness
    normal = g.mix(layers[0]['normal'] if layers[0]['normal'] is not None else (0, 0, 1),
                   layers[1]['normal'] if layers[1]['normal'] is not None else (0, 0, 1), mask)
    housewall_normal = _housewall_normals(g, r, mask)
    if housewall_normal is not None:
        normal = housewall_normal
    tint = r['colors'].get('Tint Texture')
    if sw.get('Use Color Tint') and tint:
        # HouseWall 'Use Color Tint' with the exported 'Tint Texture' colour.
        color = g.cmul(color, tint, 'Use Color Tint')
        g.report['warnings'].append('approximate: Use Color Tint multiplies blended colour by Tint Texture; master graph not exported')
    surf = env_overlay_layer(g, r, {'color': color, 'rough': rough})
    color, rough = surf['color'], surf['rough']
    g.report['implemented_stages'] = (['two surface layers', 'vertex/texture blend masks', 'separate UV scales', 'world colour projection where selected']
                                      + g.report.get('implemented_stages', []))
    g.report['warnings'].append('Environment layer mask selection, triplanar normals and paint remain a V1 approximation')
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
