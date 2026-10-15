"""Pack independent linear R masks into RGBA; preserve UVs and bilinear filtering."""
import hashlib, json
from pathlib import Path
import bpy
import numpy as np
from .assets import package
from .records import metadata


def character_masks(g, record, layers):
    ref = record['textures'].get('TextureArray_Masks', '')
    scalars = record['scalars']
    ids = sorted({int(round(scalars.get(f'{layer}_{key}', 0))) for layer in layers for key in
                  ['CreaseMaskID', 'EdgeMaskID', 'BaseRoughnessID', 'CreaseRoughnessID', 'EdgeRoughnessID']})
    # Small outfits already fit the GPU budget; avoid unnecessary pixel reads.
    if not ref or len(ids) < 8: return {}
    buckets = {}
    source_images = []
    info = metadata().get('textures', {}).get(package(ref).casefold(), {})
    for ident in ids:
        path = g.index.texture(ref, ident)
        if path is None: continue  # Normal image resolution records any missing slice.
        key = str(path).casefold() + '|Non-Color'
        image = next((i for i in bpy.data.images if i.get('arc_image_key') == key), None)
        if image is None:
            image = bpy.data.images.load(str(path), check_existing=False)
            image.colorspace_settings.name = 'Non-Color';image.alpha_mode = 'CHANNEL_PACKED'
            image['arc_image_key'] = key
        source_images.append(image)
        buckets.setdefault((tuple(image.size), image.is_float), []).append((ident, path, image))
    packed = {}
    for (size, floating), entries in buckets.items():
        for start in range(0, len(entries), 4):
            batch = entries[start:start+4]
            if len(batch) == 1: continue
            fingerprint = [(str(p), p.stat().st_mtime_ns, p.stat().st_size) for _,p,_ in batch]
            key = 'mask-pack:' + hashlib.sha256(json.dumps(fingerprint).encode()).hexdigest()
            image = next((i for i in bpy.data.images if i.get('arc_image_key') == key), None)
            if image is None:
                image = bpy.data.images.new('ARC masks · '+Path(ref).name+' · '+','.join(str(i) for i,_,_ in batch),
                                            width=size[0], height=size[1], alpha=True, float_buffer=floating)
                image.colorspace_settings.name = 'Non-Color';image.alpha_mode = 'CHANNEL_PACKED'
                pixels = np.zeros((size[0]*size[1],4),dtype=np.float32)
                for channel, (_,_,source) in enumerate(batch):
                    values=np.empty(len(source.pixels),dtype=np.float32);source.pixels.foreach_get(values)
                    pixels[:,channel]=values[0::4]
                image.pixels.foreach_set(pixels.ravel());image.update()
                image.file_format = 'OPEN_EXR' if floating else 'PNG'
                image['arc_image_key'] = key
                image['arc_mask_channels'] = json.dumps({c:str(p) for c,(_,p,_) in zip('RGBA',batch)})
                image.pack()
            for channel,(ident,_,_) in zip('rgba',batch): packed[ident]=(image,channel,info)
    if packed:
        g.report['mask_channel_packing'] = {'source_slices':len(ids),
                'packed_slices':len(packed),'image_packs':len({v[0].as_pointer() for v in packed.values()}),
                'method':'linear source R channels packed into RGBA; original resolution and UV transforms preserved'}
    return packed
