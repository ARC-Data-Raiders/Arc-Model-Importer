"""Niagara FX material translation (N-O6 lite): cooked FX materials -> node graphs for the GN particle rigs (niagara_rigs).

A renderer's material is resolved from extracted JSON (ArcPak .arc-material.json exports, CUE4Parse raw export lists):
the MI chain is walked to its master, parameters merge root defaults (CachedExpressionData) under MI overrides, and the
blend mode comes from the nearest BasePropertyOverrides. Textures are the extracted PNGs; a .arc-texture.json sidecar
decides sRGB decoding (textures.authored_srgb_sidecar), otherwise FX masks sample raw.

The graph is a per-master approximation (the expression graphs are not cooked), keyed by master name with a blend-mode
fallback. Particle inputs come from the instancer attributes the rigs store (nx_color, nx_dyn0..3, nx_misc, nx_uvx);
sprites sample the SubUV frame pair the renderer selects, mesh particles their own UVs. Emissive values are UE scene
units scaled by one shared exposure node group (DR_NX_FXExposure, EV100 default below) so lit smoke and HDR fire keep
their authored ratio.
"""
import json
import math
import os
from pathlib import Path

EV100 = 4.0
EXPOSURE_GROUP = 'DR_NX_FXExposure'
MATERIAL_TYPES = ('Material', 'MaterialInstanceConstant', 'MaterialInstanceDynamic')
FAMILIES = {
    'M_FX_MonolithCloud': 'six_point', 'M_FX_MonolithFlame': 'flame', 'M_FX_Fire_Simple': 'fire',
    'M_FX_Simple_Additive_Master': 'additive', 'M_FX_JetEngine_Frigate': 'jet', 'M_JetEngineMat': 'jet',
    'M_FX_Frigate_JetCone': 'jetcone', 'M_FX_Frigate_ThrusterShockwave': 'shockwave', 'M_FX_ArcPlaneMaster': 'arcplane',
    'M_FX_Smoke_Cheap_Master': 'smoke', 'M_FX_Smoke_Cheap_Master_DOF': 'smoke', 'M_FX_Smoke_Cheap_NoCollapse_CloudFogging_Master': 'smoke',
    'M_FX_Debris_Masked_Master': 'debris',
}
BLEND_FAMILY = {'BLEND_Additive': 'additive', 'BLEND_Masked': 'debris', 'BLEND_Opaque': 'debris'}
SKIP_TEXTURES = ('DefaultTexture',)


def _short(value):
    return str(value).rsplit('::', 1)[-1] if value else ''


def _leaf(object_path):
    return str(object_path).rsplit('/', 1)[-1].split('.')[0]


def content_rel(object_path):
    """'/Game/Pioneer/X/Y.Y' -> 'PioneerGame/Content/Pioneer/X/Y' (Engine -> Engine/Content); None for other mounts."""
    path = str(object_path or '').split('.')[0]
    for mount, prefix in (('/Game/', 'PioneerGame/Content/'), ('/Engine/', 'Engine/Content/')):
        if path.startswith(mount):
            return prefix + path[len(mount):]
    return None


def _material_export(doc, name):
    items = doc.get('Exports') if isinstance(doc, dict) and 'Exports' in doc else doc if isinstance(doc, list) else [doc]
    hits = [e for e in items or [] if isinstance(e, dict) and e.get('Type') in MATERIAL_TYPES]
    return next((e for e in hits if e.get('Name') == name), hits[0] if hits else None)


def _cached(exp):
    return exp.get('CachedExpressionData') or (exp.get('Properties') or {}).get('CachedExpressionData') or {}


def _root_defaults(exp):
    """Master parameter defaults: RuntimeEntries[k].ParameterInfoSet names pair with Scalar/Vector/Texture values."""
    ced = _cached(exp)
    names = [[x.get('Name') for x in (ced.get('RuntimeEntries' if k == 0 else 'RuntimeEntries[%d]' % k) or {}).get('ParameterInfoSet') or []]
             for k in range(4)]
    scalars = dict(zip(names[0], ced.get('ScalarValues') or []))
    vectors = {n: tuple(float(v.get(c, 0.0)) for c in 'RGBA') for n, v in zip(names[1], ced.get('VectorValues') or []) if isinstance(v, dict)}
    textures = {n: v.get('AssetPathName') for n, v in zip(names[3], ced.get('TextureValues') or []) if isinstance(v, dict)}
    return scalars, vectors, textures


class Resolver:
    """Finds material exports and texture PNGs under extraction roots (package layout first, then a basename index)."""

    def __init__(self, roots):
        self.roots = [Path(r) for r in roots or [] if r and os.path.isdir(r)]
        self._index = None
        self._exports = {}
        self._infos = {}

    def _by_name(self, filename):
        if self._index is None:
            self._index = {}
            for root in self.roots:
                for base, _dirs, files in os.walk(root):
                    for f in files:
                        self._index.setdefault(f.lower(), os.path.join(base, f))
        return self._index.get(filename.lower())

    def _candidates(self, object_path, exts):
        rel, name = content_rel(object_path), _leaf(object_path)
        for root in self.roots:
            for ext in exts:
                if rel:
                    yield root / (rel + ext)
                yield root / (name + ext)
        for ext in exts:
            hit = self._by_name(name + ext)
            if hit:
                yield Path(hit)

    def export(self, object_path):
        key = str(object_path).split('.')[0]
        if key not in self._exports:
            self._exports[key] = None
            for cand in self._candidates(object_path, ('.arc-material.json', '.json')):
                if not cand.is_file():
                    continue
                try:
                    exp = _material_export(json.loads(cand.read_text(encoding='utf-8')), _leaf(object_path))
                except (OSError, ValueError):
                    continue
                if exp is not None:
                    self._exports[key] = exp
                    break
        return self._exports[key]

    def texture(self, object_path):
        if not object_path or _leaf(object_path) in SKIP_TEXTURES:
            return None
        return next((str(c) for c in self._candidates(object_path, ('.png', '.tga')) if c.is_file()), None)

    def material_info(self, object_path):
        """Merged view of one material: name, master, blend, unlit, two_sided, scalars/vectors/textures/switches, dyn names."""
        key = str(object_path or '').split('.')[0]
        if not key:
            return None
        if key in self._infos:
            return self._infos[key]
        chain, path, seen = [], object_path, set()
        while path and str(path).split('.')[0] not in seen and len(chain) < 12:
            seen.add(str(path).split('.')[0])
            exp = self.export(path)
            if exp is None:
                break
            chain.append(exp)
            parent = (exp.get('Properties') or {}).get('Parent')
            path = parent.get('ObjectPath') if isinstance(parent, dict) else None
        info = None
        if chain:
            root = chain[-1]
            scalars, vectors, textures = _root_defaults(root) if root.get('Type') == 'Material' else ({}, {}, {})
            switches = {}
            for exp in reversed(chain):
                p = exp.get('Properties') or {}
                for x in p.get('ScalarParameterValues') or []:
                    scalars[x['ParameterInfo']['Name']] = float(x.get('ParameterValue', 0.0))
                for x in p.get('VectorParameterValues') or []:
                    v = x.get('ParameterValue') or {}
                    vectors[x['ParameterInfo']['Name']] = tuple(float(v.get(c, 0.0)) for c in 'RGBA')
                for x in p.get('TextureParameterValues') or []:
                    v = x.get('ParameterValue')
                    textures[x['ParameterInfo']['Name']] = v.get('ObjectPath') if isinstance(v, dict) else v
                for x in (p.get('StaticParametersRuntime') or p.get('StaticParameters') or {}).get('StaticSwitchParameters') or []:
                    switches[x['ParameterInfo']['Name']] = bool(x.get('Value'))
            rp = root.get('Properties') or {}
            blend = next((_short((e.get('Properties') or {}).get('BasePropertyOverrides', {}).get('BlendMode')) for e in chain
                          if (e.get('Properties') or {}).get('BasePropertyOverrides', {}).get('BlendMode')), '') or _short(rp.get('BlendMode')) or 'BLEND_Opaque'
            two = any((e.get('Properties') or {}).get('TwoSided') or (e.get('Properties') or {}).get('BasePropertyOverrides', {}).get('TwoSided') for e in chain)
            refs = [r.get('ObjectPath') for r in root.get('ReferencedTextures') or _cached(root).get('ReferencedTextures') or [] if isinstance(r, dict)]
            clip = next(((e.get('Properties') or {}).get('BasePropertyOverrides', {}).get('OpacityMaskClipValue') for e in chain
                         if 'OpacityMaskClipValue' in (e.get('Properties') or {}).get('BasePropertyOverrides', {})), rp.get('OpacityMaskClipValue', 0.3333))
            info = {'name': chain[0].get('Name') or _leaf(object_path), 'master': root.get('Name') or _leaf(path or object_path), 'blend': blend,
                    'unlit': _short(rp.get('ShadingModel')) == 'MSM_Unlit', 'two_sided': bool(two), 'scalars': scalars, 'vectors': vectors,
                    'textures': {k: v for k, v in textures.items() if v and _leaf(v) not in SKIP_TEXTURES}, 'switches': switches,
                    'referenced': [r for r in refs if r], 'dyn_names': list(_cached(root).get('DynamicParameterNames') or []),
                    'clip': float(clip), 'complete': chain[-1].get('Type') == 'Material'}
        self._infos[key] = info
        return info

    def main_texture(self, info, *names):
        """First named texture parameter that resolves, else the first non-normal referenced texture."""
        for n in names:
            path = self.texture(info['textures'].get(n))
            if path:
                return path
        for ref in list(info['textures'].values()) + info['referenced']:
            leaf = _leaf(ref)
            if leaf.endswith(('_N', '_NM')) or leaf.startswith('VT_'):
                continue
            path = self.texture(ref)
            if path:
                return path
        return None


def family(info):
    """Graph family: the master's own translation, else one by blend mode (translucent -> lit smoke card)."""
    if info is None:
        return None
    return FAMILIES.get(info['master']) or BLEND_FAMILY.get(info['blend'], 'smoke')


def channel_index(info):
    """Smoke_Cheap 'Channel select' (0..3 = R, G, B, A); defaults to alpha."""
    v = info['scalars'].get('Channel select', info['scalars'].get('Channel Select'))
    return 3 if v is None else max(0, min(3, int(round(float(v)))))


def cheap_contrast(x, c):
    """UE CheapContrast: saturate(lerp(-c, 1 + c, x))."""
    return min(1.0, max(0.0, -c + x * (1.0 + 2.0 * c)))


# ---------------------------------------------------------------- Blender graph construction (bpy only below)

class _S:
    def __init__(self, mat):
        import bpy
        self.bpy = bpy
        self.N, self.L = mat.node_tree.nodes, mat.node_tree.links

    def node(self, idname, **props):
        n = self.N.new(idname)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def put(self, sock, value):
        if isinstance(value, self.bpy.types.NodeSocket):
            self.L.new(value, sock)
        else:
            sock.default_value = value

    def math(self, op, a, b=0.0, clamp=False):
        n = self.node('ShaderNodeMath', operation=op, use_clamp=clamp)
        self.put(n.inputs[0], a)
        self.put(n.inputs[1], b)
        return n.outputs[0]

    def attr(self, name, kind='INSTANCER'):
        return self.node('ShaderNodeAttribute', attribute_type=kind, attribute_name=name)

    def sep(self, vec):
        n = self.node('ShaderNodeSeparateColor')
        self.put(n.inputs[0], vec)
        return n.outputs

    def rgb(self, op, a, b, fac=1.0):
        n = self.node('ShaderNodeMix', data_type='RGBA', blend_type=op, clamp_result=False)
        self.put(n.inputs['Factor'], fac)
        self.put(n.inputs['A'], a)
        self.put(n.inputs['B'], b)
        return n.outputs['Result']

    def scale(self, color, s):
        n = self.node('ShaderNodeVectorMath', operation='SCALE')
        self.put(n.inputs[0], color)
        self.put(n.inputs['Scale'], s)
        return n.outputs[0]

    def color(self, rgba):
        n = self.node('ShaderNodeRGB')
        n.outputs[0].default_value = tuple(rgba[:3]) + (1.0,)
        return n.outputs[0]

    def image(self, path, non_color):
        bpy = self.bpy
        img = bpy.data.images.load(path, check_existing=True)
        decoded = _authored(path)
        img.colorspace_settings.name = 'sRGB' if (decoded if decoded is not None else not non_color) else 'Non-Color'
        img.alpha_mode = 'CHANNEL_PACKED'
        return img

    def tex(self, img, vector, extension='REPEAT'):
        n = self.node('ShaderNodeTexImage', image=img, interpolation='Linear', extension=extension)
        self.L.new(vector, n.inputs['Vector'])
        return n


def _authored(path):
    """Sidecar sampling: True = sRGB decode, False = raw, None = no sidecar."""
    try:
        from . import textures
    except ImportError:
        return None
    if textures.authored_srgb_sidecar(path):
        return True
    return False if textures.authored_linear_sidecar(path) else None


def image_stats(img):
    """Per-channel max and whether alpha is constant (cached on the image)."""
    if 'dr_nx_cmax' in img:
        return list(img['dr_nx_cmax']), bool(img['dr_nx_alpha_const'])
    import numpy as np
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(-1, 4)[::7]
    cmax = [float(x) for x in px.max(0)]
    const = bool(px[:, 3].max() - px[:, 3].min() < 1.0 / 255.0)
    img['dr_nx_cmax'], img['dr_nx_alpha_const'] = cmax, const
    return cmax, const


def exposure_group():
    import bpy
    grp = bpy.data.node_groups.get(EXPOSURE_GROUP)
    if grp is None:
        grp = bpy.data.node_groups.new(EXPOSURE_GROUP, 'ShaderNodeTree')
        grp.interface.new_socket('Scale', in_out='OUTPUT', socket_type='NodeSocketFloat')
        out = grp.nodes.new('NodeGroupOutput')
        val = grp.nodes.new('ShaderNodeValue')
        val.name = val.label = 'UE emissive to scene (1 / (1.2 * 2^EV100))'
        grp.links.new(val.outputs[0], out.inputs[0])
        set_exposure(EV100, grp)
    return grp


def set_exposure(ev100, grp=None):
    grp = grp or exposure_group()
    next(n for n in grp.nodes if n.type == 'VALUE').outputs[0].default_value = 1.0 / (1.2 * 2.0 ** float(ev100))
    grp['ev100'] = float(ev100)


def _particle(s):
    col = s.attr('nx_color')
    dyn = s.attr('nx_dyn0')
    d = s.sep(dyn.outputs['Color'])
    return {'rgb': col.outputs['Color'], 'a': col.outputs['Alpha'], 'd0': [d[0], d[1], d[2], dyn.outputs['Alpha']]}


def _sprite_sample(s, img):
    """SubUV frame pair (renderer SubImageSize, nx_misc.x index, blend flag nx_misc.w) blended like the game sampler."""
    misc, uvx, quv = s.attr('nx_misc'), s.attr('nx_uvx'), s.attr('nx_quad_uv', 'GEOMETRY')
    m, u, q = s.sep(misc.outputs['Color']), s.sep(uvx.outputs['Color']), s.sep(quv.outputs['Vector'])
    idx, sx, sy = m[0], u[2], uvx.outputs['Alpha']
    frac = s.math('SUBTRACT', idx, s.math('FLOOR', idx))
    a = s.math('ADD', s.math('SUBTRACT', idx, frac), 0.5)
    ut = s.math('ADD', 0.5, s.math('MULTIPLY', s.math('SUBTRACT', q[0], 0.5), u[0]))
    vt = s.math('ADD', 0.5, s.math('MULTIPLY', s.math('SUBTRACT', q[1], 0.5), u[1]))
    out = []
    for k in (a, s.math('ADD', a, 1.0)):
        h = s.math('FLOOR', s.math('MODULO', k, sx))
        v = s.math('FLOOR', s.math('DIVIDE', k, sx))
        comb = s.node('ShaderNodeCombineXYZ')
        s.put(comb.inputs[0], s.math('DIVIDE', s.math('ADD', h, ut), sx))
        s.put(comb.inputs[1], s.math('SUBTRACT', 1.0, s.math('DIVIDE', s.math('ADD', v, vt), sy)))
        out.append(s.tex(img, comb.outputs[0]))
    fac = s.math('MULTIPLY', frac, misc.outputs['Alpha'])
    rgb = s.rgb('MIX', out[0].outputs['Color'], out[1].outputs['Color'], fac)
    alpha = s.math('ADD', s.math('MULTIPLY', out[0].outputs['Alpha'], s.math('SUBTRACT', 1.0, fac)), s.math('MULTIPLY', out[1].outputs['Alpha'], fac))
    return rgb, alpha


def _mesh_sample(s, img, tiling=(1.0, 1.0)):
    uv = s.node('ShaderNodeTexCoord').outputs['UV']
    if tuple(tiling) != (1.0, 1.0):
        mp = s.node('ShaderNodeMapping')
        s.L.new(uv, mp.inputs['Vector'])
        mp.inputs['Scale'].default_value = (float(tiling[0]), float(tiling[1]), 1.0)
        uv = mp.outputs[0]
    t = s.tex(img, uv)
    return t.outputs['Color'], t.outputs['Alpha']


def _sample(s, kind, img, tiling=(1.0, 1.0)):
    return _sprite_sample(s, img) if kind == 'sprite' else _mesh_sample(s, img, tiling)


def _uv(s):
    return s.sep(s.node('ShaderNodeTexCoord').outputs['UV'])


def _lum(s, rgb):
    c = s.sep(rgb)
    return s.math('DIVIDE', s.math('ADD', s.math('ADD', c[0], c[1]), c[2]), 3.0)


def _channel(s, rgb, alpha, k):
    return alpha if k == 3 else s.sep(rgb)[k]


def _facing(s):
    """1 where the surface faces the camera, 0 at grazing angles."""
    lw = s.node('ShaderNodeLayerWeight')
    return s.math('SUBTRACT', 1.0, lw.outputs['Facing'], clamp=True)


def _emission(s, color, strength=1.0):
    exp = s.node('ShaderNodeGroup')
    exp.node_tree = exposure_group()
    em = s.node('ShaderNodeEmission')
    s.put(em.inputs['Color'], color)
    # UE translucent FX emissive is seen, never a light source: camera rays only (transparent bounces keep the camera
    # flag), so Cycles does not light hull/smoke with it. Particle light renderers do the lighting.
    camera = s.node('ShaderNodeLightPath').outputs['Is Camera Ray']
    s.put(em.inputs['Strength'], s.math('MULTIPLY', s.math('MULTIPLY', exp.outputs[0], strength), camera))
    return em.outputs[0]


def _additive(s, color, strength=1.0):
    add = s.node('ShaderNodeAddShader')
    s.L.new(s.node('ShaderNodeBsdfTransparent').outputs[0], add.inputs[0])
    s.L.new(_emission(s, color, strength), add.inputs[1])
    return add.outputs[0]


def _lit(s, albedo, opacity, emissive=None):
    """Lit translucent card: half diffuse, half back-lit, over transparency by opacity (+ UE-unit emissive)."""
    mix = s.node('ShaderNodeMixShader')
    mix.inputs[0].default_value = 0.5
    dif, tra = s.node('ShaderNodeBsdfDiffuse'), s.node('ShaderNodeBsdfTranslucent')
    s.put(dif.inputs['Color'], albedo)
    s.put(tra.inputs['Color'], albedo)
    s.L.new(dif.outputs[0], mix.inputs[1])
    s.L.new(tra.outputs[0], mix.inputs[2])
    surf = mix.outputs[0]
    if emissive is not None:
        add = s.node('ShaderNodeAddShader')
        s.L.new(surf, add.inputs[0])
        s.L.new(_emission(s, emissive), add.inputs[1])
        surf = add.outputs[0]
    out = s.node('ShaderNodeMixShader')
    s.put(out.inputs[0], s.math('MINIMUM', s.math('MAXIMUM', opacity, 0.0), 1.0))
    s.L.new(s.node('ShaderNodeBsdfTransparent').outputs[0], out.inputs[1])
    s.L.new(surf, out.inputs[2])
    return out.outputs[0]


def _contrast(s, x, c):
    return s.math('SUBTRACT', s.math('MULTIPLY', x, s.math('ADD', 1.0, s.math('MULTIPLY', c, 2.0))), c, clamp=True)


def _build_six_point(s, info, kind, res, P):
    img = s.image(res.main_texture(info, '6Point'), True)
    rgb, a = _sample(s, kind, img)
    light = _lum(s, rgb)
    v = info['vectors']
    tint = s.rgb('MIX', s.scale(s.color(v.get('Shadow Color', (0.73, 0.8, 1.0, 1.0))), 0.35), s.color(v.get('Light Tint', (1.0, 1.0, 1.0, 1.0))), light)
    albedo = s.rgb('MULTIPLY', P['rgb'], tint)
    return _lit(s, albedo, s.math('MULTIPLY', _contrast(s, a, s.math('MAXIMUM', P['d0'][0], 0.0)), P['a']))


def _build_smoke(s, info, kind, res, P):
    img = s.image(res.main_texture(info, 'Texture'), True)
    rgb, a = _sample(s, kind, img)
    k = channel_index(info)
    _cmax, alpha_const = image_stats(img)
    mask = _lum(s, rgb) if (k == 3 and alpha_const) else _channel(s, rgb, a, k)
    packed = info['switches'].get('bUsePackedTexture', False)
    albedo = P['rgb'] if packed else s.rgb('MULTIPLY', P['rgb'], rgb)
    add = info['vectors'].get('Color Add')
    if add and any(add[:3]):
        albedo = s.rgb('ADD', albedo, s.color(add))
    emissive = s.scale(albedo, P['d0'][0]) if info['dyn_names'][:1] == ['Emissive Multiplier'] else None
    return _lit(s, albedo, s.math('MULTIPLY', mask, P['a']), emissive)


def _build_fire(s, info, kind, res, P):
    img = s.image(res.main_texture(info, 'Texture'), True)
    rgb, _a = _sample(s, kind, img)
    cs = info['vectors'].get('ChannelSelect', (1.0, 0.0, 0.0, 1.0))
    c = s.sep(rgb)
    mask = s.math('ADD', s.math('ADD', s.math('MULTIPLY', c[0], cs[0]), s.math('MULTIPLY', c[1], cs[1])), s.math('MULTIPLY', c[2], cs[2]))
    return _additive(s, s.scale(P['rgb'], s.math('MULTIPLY', mask, P['a'])))


def _build_flame(s, info, kind, res, P):
    img = s.image(res.main_texture(info), True)
    rgb, a = _sample(s, kind, img)
    light = _lum(s, rgb)
    mask = light if image_stats(img)[1] else a
    v = info['vectors']
    col = s.rgb('MIX', s.color(v.get('EdgeColor', (0.0, 0.07, 2.0, 1.0))), s.color(v.get('MainColor', (20.0, 4.85, 1.08, 1.0))), light)
    return _additive(s, s.scale(s.rgb('MULTIPLY', col, P['rgb']), s.math('MULTIPLY', mask, P['a'])))


def _build_additive(s, info, kind, res, P):
    img = s.image(res.main_texture(info, 'Texture'), True)
    rgb, a = _sample(s, kind, img)
    mask = s.sep(rgb)[0]
    curve = float(info['scalars'].get('Gradient Curve', 0.0))
    if curve > 0.0 and info['switches'].get('bUseGradientMap', True):
        mask = s.math('POWER', s.math('MAXIMUM', mask, 0.0), curve)
    if not image_stats(img)[1]:
        mask = s.math('MULTIPLY', mask, a)
    return _additive(s, s.scale(P['rgb'], s.math('MULTIPLY', mask, P['a'])))


def _build_jet(s, info, kind, res, P):
    sc, v = info['scalars'], info['vectors']
    uv = _uv(s)
    grad = s.math('MINIMUM', s.math('MAXIMUM', uv[1], 0.0), 1.0)
    col = s.rgb('MIX', s.color(v.get('BottomCol', (10.0, 4.09, 0.83, 1.0))), s.color(v.get('TopCol', (1.54, 4.64, 10.0, 1.0))), grad)
    fade = s.math('POWER', s.math('SUBTRACT', 1.0, grad, clamp=True), float(sc.get('Gradient Contrast Alpha', sc.get('Gradient Contrast', 2.0))) * 0.5)
    path = res.main_texture(info)
    noise = 1.0
    if path:
        n_rgb, _ = _mesh_sample(s, s.image(path, True), (sc.get('Tiling_X', 1.0), sc.get('Tiling_Y', 1.0)))
        noise = s.math('POWER', s.math('MAXIMUM', s.sep(n_rgb)[0], 0.0), float(sc.get('Contrast', 2.0)) * 0.5)
    f = s.math('POWER', _facing(s), float(sc.get('Fresnel Exponent', 0.5)))
    k = s.math('MULTIPLY', s.math('MULTIPLY', fade, noise), s.math('MULTIPLY', f, P['a']))
    return _additive(s, s.scale(s.rgb('MULTIPLY', col, P['rgb']), k))


def _build_jetcone(s, info, kind, res, P):
    v = info['vectors']
    path = res.main_texture(info)
    noise = 1.0
    if path:
        n_rgb, _ = _mesh_sample(s, s.image(path, True))
        noise = s.math('ADD', 0.5, s.math('MULTIPLY', s.sep(n_rgb)[0], 0.5))
    uv = _uv(s)
    # Distance from the nozzle: SM_FX_JetCone_ShockDiamonds_XL imports with V=1 at the nozzle (z=0) and V=0 at the tip,
    # and the beam is brightest at the nozzle (frigate entrance footage).
    along = s.math('SUBTRACT', 1.0, s.math('MINIMUM', s.math('MAXIMUM', uv[1], 0.0), 1.0))
    diamonds = s.math('POWER', s.math('ABSOLUTE', s.math('SINE', s.math('MULTIPLY', along, 6.0 * math.pi))), 8.0)
    flame = s.scale(s.color(v.get('Param_1', (3.57, 1.22, 8.0, 1.0))), s.math('MULTIPLY', s.math('MAXIMUM', P['d0'][2], 0.0), noise))
    dia = s.scale(s.color(v.get('Param_3', (7.19, 2.74, 8.0, 1.0))), s.math('MULTIPLY', s.math('MAXIMUM', P['d0'][3], 0.0), diamonds))
    fade = s.math('MULTIPLY', s.math('SUBTRACT', 1.0, along, clamp=True), _facing(s))
    return _additive(s, s.scale(s.rgb('MULTIPLY', s.rgb('ADD', flame, dia), P['rgb']), s.math('MULTIPLY', fade, P['a'])))


def _build_shockwave(s, info, kind, res, P):
    path = res.main_texture(info)
    mask = 1.0
    if path:
        rgb, _a = _mesh_sample(s, s.image(path, True))
        mask = s.sep(rgb)[0]
    edge = s.math('DIVIDE', _facing(s), max(float(info['scalars'].get('EdgeFade', 0.7)), 1e-3), clamp=True)
    return _lit(s, P['rgb'], s.math('MULTIPLY', s.math('MULTIPLY', mask, edge), P['a']))


def _build_arcplane(s, info, kind, res, P):
    path = res.main_texture(info, 'SmokeTexture')
    if path:
        img = s.image(path, True)
        rgb, a = _mesh_sample(s, img)
        mask = s.sep(rgb)[0] if image_stats(img)[1] else a
    else:
        uv = _uv(s)
        mask = s.math('MULTIPLY', s.math('SINE', s.math('MULTIPLY', uv[1], math.pi)), s.math('SUBTRACT', 1.0, uv[0], clamp=True))
    glow = s.color(info['vectors'].get('GlowColor', (100.0, 50.3, 10.1, 1.0)))
    op = s.math('MULTIPLY', mask, s.math('MINIMUM', P['a'], 1.0))
    return _lit(s, P['rgb'], op, s.scale(s.rgb('MULTIPLY', glow, P['rgb']), s.math('MULTIPLY', mask, P['a'])))


def _build_debris(s, info, kind, res, P):
    path = res.main_texture(info, 'Base Color', 'Texture')
    bsdf = s.node('ShaderNodeBsdfPrincipled')
    alpha = 1.0
    if path:
        rgb, a = _sample(s, kind, s.image(path, False))
        s.put(bsdf.inputs['Base Color'], s.rgb('MULTIPLY', rgb, P['rgb']))
        alpha = s.math('GREATER_THAN', a, float(info.get('clip', 0.3333)))
    bsdf.inputs['Roughness'].default_value = 0.8
    mix = s.node('ShaderNodeMixShader')
    s.put(mix.inputs[0], alpha)
    s.L.new(s.node('ShaderNodeBsdfTransparent').outputs[0], mix.inputs[1])
    s.L.new(bsdf.outputs[0], mix.inputs[2])
    return mix.outputs[0]


BUILDERS = {'six_point': _build_six_point, 'smoke': _build_smoke, 'fire': _build_fire, 'flame': _build_flame, 'additive': _build_additive,
            'jet': _build_jet, 'jetcone': _build_jetcone, 'shockwave': _build_shockwave, 'arcplane': _build_arcplane, 'debris': _build_debris}
NEEDS_TEXTURE = ('six_point', 'smoke', 'fire', 'flame', 'additive')


def build_material(object_path, kind, resolver):
    """Translated material for a sprite or mesh renderer; None when unresolved (caller keeps its preview; ribbons keep theirs)."""
    import bpy
    if kind not in ('sprite', 'mesh'):
        return None
    info = resolver.material_info(object_path)
    fam = family(info)
    if fam is None or (fam in NEEDS_TEXTURE and resolver.main_texture(info, 'Texture', '6Point') is None):
        return None
    name = 'NXM_%s_%s' % (info['name'], kind)
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    s = _S(mat)
    surface = BUILDERS[fam](s, info, kind, resolver, _particle(s))
    s.L.new(surface, s.node('ShaderNodeOutputMaterial').inputs['Surface'])
    mat.surface_render_method = 'BLENDED' if fam != 'debris' else 'DITHERED'
    mat.use_backface_culling = False
    for prop, value in (('use_transparency_overlap', True), ('use_transparent_shadow', True)):
        if hasattr(mat, prop):
            setattr(mat, prop, value)
    mat['dr_nx_material'] = str(object_path)
    mat['dr_nx_family'] = fam
    mat['dr_nx_master'] = info['master']
    mat['dr_nx_blend'] = info['blend']
    return mat


def renderer_materials(roots):
    """Callback for niagara_rigs.build_system: (renderer info, kind, slot, slot material name) -> material or None."""
    resolver = Resolver(roots)

    def pick(info, kind, slot=None, slot_name=None):
        if kind == 'mesh':
            over = info.get('override_materials') or []
            path = over[slot] if info['props'].get('bOverrideMaterials') and slot is not None and slot < len(over) else None
            if not path and slot_name:
                path = '/Game/' + slot_name.split('.')[0]  # basename lookup (no package path on an imported slot)
            return build_material(path, 'mesh', resolver) if path else None
        return build_material(info.get('material'), kind, resolver) if info.get('material') else None
    pick.resolver = resolver
    return pick
