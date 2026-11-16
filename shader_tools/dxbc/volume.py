"""Retained offline Texture3D sampling. No inferred game sampler defaults."""
import hashlib
from pathlib import Path
import struct

import numpy as np


class Unsupported(ValueError):
    pass


POLICY = 'retained_point_wrap_mip0'


def decode(resource, payload):
    if resource.get('kind') != 'texture3d' or payload[:4] != b'DDS ' or len(payload) < 148 or payload[84:88] != b'DX10':
        raise Unsupported('volume requires Texture3D DX10 DDS')
    height, width = struct.unpack_from('<II', payload, 12)
    depth, mips = struct.unpack_from('<II', payload, 24)
    dxgi, dimension, misc, arrays, alpha = struct.unpack_from('<IIIII', payload, 128)
    if (dxgi, dimension, arrays, mips) != (2, 4, 1, 1) or misc & 4:
        raise Unsupported('volume requires single-mip RGBA32F Texture3D, one array entry')
    if (width, height, depth) != (resource.get('width'), resource.get('height'), resource.get('depth')) or min(width, height, depth) <= 0:
        raise Unsupported('volume resource dimensions disagree with DDS')
    if resource.get('format') != 2 or resource.get('layers') != 1 or resource.get('mip', 0) != 0:
        raise Unsupported('volume resource format/layers/mip mismatch')
    if len(payload) != 148 + width * height * depth * 16:
        raise Unsupported('volume payload size mismatch')
    data = np.frombuffer(payload, '<u4', offset=148).reshape(depth, height, width, 4)
    if not np.isfinite(data.view('<f4')).all():
        raise Unsupported('nonfinite retained volume payload')
    return data


def retained_sampler(payload, slot):
    rows = [line.split('\t') for line in payload.decode('utf-8-sig').splitlines()]
    selected = [row for row in rows if row[:3] == ['samp', 'ps', str(slot)]]
    if len(selected) != 1 or selected[0][3:] != ['0', '1', '1', '1', '0', '1', '0', '0']:
        raise Unsupported('missing or unsupported retained point/wrap/mip-zero sampler')
    return dict(policy=POLICY, sampler=slot, filter=0, address_u=1, address_v=1,
        address_w=1, mip_lod_bias=0, min_lod=0, max_lod=0,
        classification='offline_emulator_gpu_compute_readback', production_parity='unverified')


def fixture_contract(fixture, sampler):
    pin = fixture.get('retained_sampler_job')
    if not pin or pin.get('file') != fixture.get('job'):
        raise Unsupported('missing retained sampler job pin')
    payload = Path(pin['file']).read_bytes()
    if hashlib.sha256(payload).hexdigest() != pin.get('sha256'):
        raise Unsupported('retained sampler job identity mismatch')
    return retained_sampler(payload, sampler)


def sample_words(data, coordinates, contract):
    if contract.get('policy') != POLICY:
        raise Unsupported('unsupported volume sampler policy')
    uvw = np.asarray(coordinates, np.float32)
    if uvw.shape[-1] != 3 or not np.isfinite(uvw).all():
        raise Unsupported('nonfinite or incomplete volume coordinates')
    depth, height, width, channels = data.shape
    if channels != 4:
        raise Unsupported('volume requires four components')
    indices = []
    for index, size in enumerate((width, height, depth)):
        scaled = np.asarray((uvw[..., index] - np.floor(uvw[..., index])) * np.float32(size), np.float32)
        # Matches the retained interpreter's 8-bit subtexel nearest-even rule.
        indices.append(np.floor(np.rint(scaled.astype(np.float64) * 256) / 256).astype(np.int64) % size)
    return data[indices[2], indices[1], indices[0]]


class Volume:
    def __init__(self, image, width, height, depth, contract):
        if contract.get('policy') != POLICY or min(width, height, depth) <= 0 or tuple(image.size) != (width, height * depth):
            raise Unsupported('invalid retained volume atlas or sampler')
        self.image, self.width, self.height, self.depth, self.contract = image, width, height, depth, contract


def image(resource, payload, contract):
    import bpy
    data = decode(resource, payload)
    depth, height, width, _ = data.shape
    result = bpy.data.images.new('NX_Volume_t%d' % resource['slot'], width=width, height=height * depth, alpha=True, float_buffer=True)
    result.colorspace_settings.name = 'Non-Color'
    result.alpha_mode = 'CHANNEL_PACKED'
    # DDS stores z slices in increasing order, each with its top row first.
    result.pixels.foreach_set(np.flipud(data.view('<f4').reshape(height * depth, width, 4)).ravel())
    result.update()
    result['dr_nx_volume_payload_sha256'] = hashlib.sha256(payload).hexdigest()
    result['dr_nx_volume_sampling'] = POLICY
    return Volume(result, width, height, depth, contract)


def lower(nt, texture, coordinates, component, cache, slot, sampler):
    from . import lower_blender as lb
    if texture.contract.get('policy') != POLICY or texture.contract.get('sampler') != sampler or len(coordinates) != 3:
        raise Unsupported('volume sampler/coordinate mismatch')
    key = ('volume', slot, sampler, *(value.as_pointer() if hasattr(value, 'as_pointer') else value for value in coordinates))
    if key not in cache:
        def texel(coordinate, dimension):
            scaled = lb.math(nt, 'MULTIPLY', lb.math(nt, 'FRACT', coordinate), float(dimension))
            quantized = lb.math(nt, 'FLOOR', lb.math(nt, 'ADD', lb.math(nt, 'MULTIPLY', scaled, 256.0), .5))
            index = lb.math(nt, 'FLOOR', lb.math(nt, 'MULTIPLY', quantized, 1 / 256))
            # Wrap each axis before flattening. Atlas repeat alone mixes adjacent slices at seams.
            return lb.math(nt, 'MODULO', index, float(dimension))
        x, y, z = [texel(coordinate, dimension) for coordinate, dimension in zip(coordinates, (texture.width, texture.height, texture.depth))]
        u = lb.math(nt, 'DIVIDE', lb.math(nt, 'ADD', x, .5), float(texture.width))
        row = lb.math(nt, 'ADD', lb.math(nt, 'MULTIPLY', z, float(texture.height)), lb.math(nt, 'ADD', y, .5))
        v = lb.math(nt, 'SUBTRACT', 1.0, lb.math(nt, 'DIVIDE', row, float(texture.height * texture.depth)))
        vector = nt.nodes.new('ShaderNodeCombineXYZ')
        for index, value in enumerate([u, v]):
            nt.links.new(value, vector.inputs[index])
        node = nt.nodes.new('ShaderNodeTexImage')
        node.image = texture.image
        node.interpolation, node.extension = 'Closest', 'EXTEND'
        nt.links.new(vector.outputs[0], node.inputs['Vector'])
        separate = nt.nodes.new('ShaderNodeSeparateColor')
        nt.links.new(node.outputs['Color'], separate.inputs[0])
        cache[key] = list(separate.outputs[:3]) + [node.outputs['Alpha']]
    return cache[key][component]
