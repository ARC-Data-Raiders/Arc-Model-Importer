"""Recovered character equations, organized as editable material-layer groups."""
import numpy as np
from .channel_pack import character_masks

_LAYERS = {}

def active_layers(image):
    from pathlib import Path
    path = Path(image.filepath)
    stamp = path.stat() if path.is_file() else None
    key = (str(path), stamp.st_mtime_ns if stamp else 0, tuple(image.size))
    if key not in _LAYERS:
        pixels = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(pixels)
        ids = np.unique(np.floor(pixels[2::4] * 8 + .5).astype(np.int16))
        _LAYERS[key] = [int(i) for i in ids if 1 <= i <= 8] or [1]
    return _LAYERS[key]


def build_character(g, r):
    s, c, t = r['scalars'], r['colors'], r['textures']
    report = g.report
    g.section('Packed maps — UV0, linear data')
    packed = g.image(t.get('BaseColor', ''), srgb=False, label='Tint / crease / edge controls')
    ocm = g.image(t.get('OcclusionCurvatureMaterialID', ''), srgb=False, label='AO / curvature / layer ID')
    cm = g.image(t.get('ColorMask', ''), srgb=False, label='Palette mask RGB')
    base_n = g.image(t.get('Normal', ''), srgb=False)
    if not (packed and ocm):
        report['warnings'].append('Required packed character maps missing; material uses a neutral fallback')
        g.finish((.3, .3, .3, 1), normal=g.decode_normal(base_n)); return
    layer_id = g.math('FLOOR', g.add(g.mul(ocm['b'], 8), .5), label='round(8 × linear layer ID)')
    active = active_layers(ocm['node'].image)
    report['layers'] = active
    masks = character_masks(g, r, active)
    report['implemented_stages'] = ['linear packed controls', '8-layer integer ID', 'per-pixel primary/secondary palette',
        'RGB swatches', 'UV1 texture-array slices', 'crease/edge masks and overlays', 'RGB pattern-array compositing',
        'roughness addition', 'layer metallic/specular', 'RNM normal blending and quarter-turn rotation']
    channels = [cm[k] for k in 'rgb'] if cm else [0, 0, 0]
    palette = [(g.color(c.get('Color'+letter, (1,1,1,1)), 'Palette '+letter),
                g.color(c.get('Color'+letter+'2', c.get('Color'+letter, (1,1,1,1))), 'Palette '+letter+' secondary')) for letter in 'ABC']
    normal = g.decode_normal(base_n)
    breakup = g.image(t.get('BreakupMask', ''), g.transform_uv(1, s.get('BreakupMaskTiling', 1) * s.get('Tiling', 1)), srgb=False, required=False)
    br = breakup['r'] if breakup else 1
    if not breakup: report['warnings'].append('Breakup texture absent; wear breakup uses constant 1')
    color, rough, metal, spec, normals, emission = (0,0,0,1), 0, 0, 0, (0,0,0), (0,0,0,1)
    for layer in active:
        with g.block(f'Layer {layer} — palette, wear and surface') as stage:
            values = build_layer(stage, r, layer, packed, ocm, channels, br, normal, palette, masks)
            outputs = [stage.export(v, label, typ) for v, label, typ in zip(values,
                       ['Colour','Roughness','Metallic','Specular','Normal','Emission'],
                       ['Color','Float','Float','Float','Vector','Color'])]
        mask = g.math('COMPARE', layer_id, layer, .1, label=f'Layer {layer} selection')
        color = g.mix(color, outputs[0], mask)
        rough = g.add(rough, g.mul(outputs[1], mask))
        metal = g.add(metal, g.mul(outputs[2], mask))
        spec = g.add(spec, g.mul(outputs[3], mask))
        normals = g.vector('ADD', normals, g.vector('SCALE', outputs[4], scale=mask))
        emission = g.mix(emission, outputs[5], mask)
    report['warnings'].append('Cloth directional specular, AO lighting, weather, and decal overrides are not engine-identical')
    if any(k.endswith('_DecalPlacement') for k in c): report['warnings'].append('Character decal placement/overrides omitted; artwork may be missing')
    g.finish(color, g.clamp(rough), g.clamp(metal), g.vector('NORMALIZE', normals), specular=spec, emission=emission)


def build_layer(g, r, layer, packed, ocm, channels, br, base_normal, palette_inputs, masks=None):
    s, c, t = r['scalars'], r['colors'], r['textures']
    def S(k, default=0): return float(s.get(f'{layer}_{k}', default))
    def C(k, default=(1,1,1,1)): return c.get(f'{layer}_{k}', default)
    detail_scale = g.mul(s.get('Tiling',1), g.parameter('Detail tiling', S('DetailTiling',1)))
    def array(kind, ident, tiling=1, srgb=False):
        ref = t.get('TextureArray_'+kind, '')
        uv = g.transform_uv(1, g.mul(detail_scale,tiling))
        if kind == 'Masks' and masks and int(round(ident)) in masks:
            image,channel,info = masks[int(round(ident))]
            sample = g.sample_image(image, uv, f'L{layer} Masks[{ident:g}] → {channel.upper()}', info)
            return {'r':sample[channel]}
        return g.image(ref, uv, layer=int(round(ident)), srgb=srgb,
                       label=f'L{layer} {kind}[{ident:g}]') if ref else None
    crease_tex = array('Masks', S('CreaseMaskID'), S('CreaseMaskTiling',1))
    edge_tex = array('Masks', S('EdgeMaskID'), S('EdgeMaskTiling',1))
    curvature = g.clamp(g.mul(ocm['g'],2))
    signed_c = g.math('MINIMUM',g.math('MAXIMUM',g.mul(g.sub(g.mul(packed['g'],2),1),g.sub(2,br)),-1),1)
    crease_raw = g.add(g.mul(curvature,g.add(curvature,crease_tex['r'] if crease_tex else 0)),signed_c)
    edge_base = g.sub(1,g.clamp(g.mul(g.mul(g.sub(ocm['g'],.5),2),ocm['r'])))
    signed_e = g.math('MINIMUM',g.math('MAXIMUM',g.mul(g.sub(g.mul(packed['b'],2),1),g.add(1,br)),-1),1)
    edge_raw = g.add(g.mul(edge_base,g.sub(g.add(1,edge_base),edge_tex['r'] if edge_tex else 0)),signed_e)
    def wear(raw, kind):
        softness = S(kind+'MaskSoftness',1)
        amount = g.parameter(kind+' amount',S(kind+'Amount'))
        threshold = g.sub(g.mul(amount,1+softness),softness)
        if softness <= 0: return g.math('LESS_THAN',raw,threshold)
        return g.clamp(g.sub(1,g.div(g.sub(raw,threshold),softness)))
    crease, edge = wear(crease_raw,'Crease'), wear(edge_raw,'Edge')
    body = g.clamp(g.mul(g.sub(1,g.add(crease,edge)),2))
    strength = g.clamp(g.add(g.add(g.mul(body,S('BaseColorMaskStrength')),g.mul(edge,S('EdgeColorMaskStrength'))),g.mul(crease,S('CreaseColorMaskStrength'))))
    swatch = g.split(g.parameter('Palette swatch RGB', C('ColorMaskSwatch'), 'Color'))
    colors = [g.mix(a,b,packed['r'],f'{letter}: primary / secondary') for letter,(a,b) in zip('ABC',palette_inputs)]
    weights = [g.mul(g.mul(channels[i],swatch[i]),strength) for i in range(3)]
    palette = (1,1,1,1)
    for col, weight in zip(colors,weights): palette = g.mix(palette,col,weight)
    ct = array('Colors',S('ColorTextureID'),S('ColorTextureTiling',1),srgb=True)
    detail_color = ct['color'] if ct else (1,1,1,1)
    detail = g.mix((1,1,1,1),detail_color,g.clamp(g.mul(body,S('BaseTextureStrength'))))
    detail = g.mix(detail,detail_color,g.clamp(g.mul(edge,S('EdgeTextureStrength'))))
    detail = g.mix(detail,detail_color,g.clamp(g.mul(crease,S('CreaseTextureStrength'))))
    color = g.cmul(g.cmul(g.parameter('Base colour overlay',C('BaseColorOverlay'),'Color'),detail),palette)
    pm = C('PatternMask',(0,0,0,0)); pcs = [C('PatternColor'+a,(1,1,1,0)) for a in 'ABC']
    if any(pm[:3]) and (pm[3] or any(pc[3] for pc in pcs)):
        pattern = g.image(t.get('TextureArray_Patterns',''),g.transform_uv(1,s.get('Tiling',1)*S('PatternTiling',1)),
                          layer=int(round(S('PatternID'))),srgb=False,label=f'L{layer} pattern')
        if pattern:
            patterned = g.mix(color,pattern['color'],pm[3])
            for pc, channel in zip(pcs,'rgb'): patterned = g.mix(patterned,pc,g.mul(pc[3],pattern[channel]))
            gate = g.mix(0,pm[0],channels[0]); gate = g.mix(gate,pm[1],channels[1]); gate = g.mix(gate,pm[2],channels[2])
            color = g.mix(color,patterned,gate)
    for kind,factor in [('Crease',crease),('Edge',edge)]:
        overlay = C(kind+'ColorOverlay',(1,1,1,0))
        color = g.mix(color,overlay,g.mul(factor,overlay[3]),kind+' overlay')
    if any(S(k)!=0 for k in ['HueVariationStrength','MediumNormalStrength']):
        g.report['warnings'].append(f'Layer {layer} hue variation / medium normal stage omitted')
    def response(kind, normal=False):
        if normal:
            bt = array('Normals',S('BaseNormalID'))
            ct = array('Normals',S('CreaseNormalID'),S('CreaseNormalTiling',1))
            et = array('Normals',S('EdgeNormalID'),S('EdgeNormalTiling',1))
            return g.mix(g.mix(g.decode_normal(bt),g.decode_normal(ct),crease),g.decode_normal(et),edge)
        if kind=='Roughness':
            values=[]
            for stage in ['Base','Crease','Edge']:
                tex=array('Masks',S(stage+'RoughnessID'),S(stage+('RougnessTiling' if stage!='Base' else 'RoughnessTiling'),1))
                values.append(g.clamp(g.add(tex['r'] if tex else 0,g.parameter(stage+' roughness offset',S(stage+kind,.5)))))
        else:
            suffix='Metallicity' if kind=='Metallic' else 'Specular'
            values=[g.parameter(stage+' '+kind.lower(),S(stage+suffix,.5 if kind=='Specular' else 0)) for stage in ['Base','Crease','Edge']]
        return g.mix(g.mix(values[0],values[1],crease),values[2],edge)
    detail_n = response('Normal',normal=True)
    nx,ny,nz = g.split(detail_n)
    angle = g.mul(g.math('FLOOR',g.split(g.uv(0))[0]),1.5707963267948966)
    sn,co = g.math('SINE',angle),g.math('COSINE',angle)
    detail_n = g.combine(g.sub(g.mul(nx,co),g.mul(ny,sn)),g.add(g.mul(nx,sn),g.mul(ny,co)),nz)
    if S('DetailNormalOverride'): detail_n = g.mix(detail_n,(0,0,1),max(0,min(1,S('DetailNormalOverride'))))
    normal = g.rnm(base_normal,detail_n)
    emissive = C('EmissiveColorOverlay',(0,0,0,0))
    em_strength = g.mix(g.mix(S('BaseEmissiveStrength'),S('CreaseEmissiveStrength'),crease),S('EdgeEmissiveStrength'),edge)
    emission = g.cmul(g.mix(color,emissive,emissive[3]),g.combine(em_strength,em_strength,em_strength))
    return color,response('Roughness'),response('Metallic'),response('Specular'),normal,emission
