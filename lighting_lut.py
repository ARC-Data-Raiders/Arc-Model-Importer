"""Cross-channel 3D strip LUT sampling for Blender 5 compositing.

This implements the existing linear -> sRGB -> display LUT -> linear contract,
not Unreal's tonemapper, exposure, gamut mapping, or complete lighting pipeline.
Strip layout follows RGBTable16x1: X = red + blue * size; top row = green zero.
Map UV's vector Z is its coverage alpha, so every LUT coordinate explicitly has
Z=1. Official node source: source/blender/nodes/composite/nodes/node_composite_map_uv.cc.
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import os

SCHEMA_VERSION = 2


def srgb_encode(value: float) -> float:
    value = float(value)
    return value * 12.92 if value <= .0031308 else 1.055 * value ** (1 / 2.4) - .055


def srgb_decode(value: float) -> float:
    value = float(value)
    return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4


def strip_size(width: int, height: int) -> int:
    if height < 2 or width != height * height:
        raise ValueError(f'Expected an N*N by N LUT strip with N>=2, got {width}x{height}')
    return int(height)


def sample_strip_trilinear(pixels, width: int, height: int, rgb) -> tuple:
    """Independent CPU oracle over Blender's bottom-up flat RGBA pixel layout."""
    size = strip_size(width, height)
    coordinates = [max(0., min(1., float(v))) * (size - 1) for v in rgb[:3]]
    low = [int(math.floor(value)) for value in coordinates]
    high = [min(value + 1, size - 1) for value in low]
    fraction = [value - lower for value, lower in zip(coordinates, low)]
    output = [0., 0., 0.]
    for blue_bit in range(2):
        for green_bit in range(2):
            for red_bit in range(2):
                bits = (red_bit, green_bit, blue_bit)
                r, g, b = [high[i] if bit else low[i] for i, bit in enumerate(bits)]
                weight = math.prod(fraction[i] if bit else 1 - fraction[i] for i, bit in enumerate(bits))
                offset = ((height - 1 - g) * width + b * size + r) * 4
                for channel in range(3):
                    output[channel] += float(pixels[offset + channel]) * weight
    return tuple(output)


def apply_lut_rgba(rgba, pixels, width, height, *, strength=1., display_referred=True,
                   premultiplied=True) -> tuple:
    """CPU oracle matching the compositor helper, including unchanged alpha."""
    alpha = float(rgba[3])
    straight = [float(c) / max(alpha, 1e-8) if premultiplied else float(c) for c in rgba[:3]]
    coordinate = [srgb_encode(c) for c in straight] if display_referred else straight
    graded = sample_strip_trilinear(pixels, width, height, coordinate)
    if display_referred:
        graded = tuple(srgb_decode(c) for c in graded)
    amount = max(0., min(1., float(strength)))
    output = [a + (b - a) * amount for a, b in zip(straight, graded)]
    if premultiplied:
        output = [value * alpha for value in output]
    return (*output, alpha)


def _owned_lut_image(source):
    """Own raw LUT sampling settings; never recolor an image used elsewhere."""
    import bpy
    if isinstance(source, (str, os.PathLike)):
        path = os.path.abspath(os.fspath(source))
        stat = os.stat(path)
        identity = ('file', path.casefold(), stat.st_mtime_ns, stat.st_size)
    else:
        path = ''
        values = array.array('f', [0.]) * (len(source.pixels))
        source.pixels.foreach_get(values)
        identity = ('image', source.name, tuple(source.size), hashlib.sha256(values.tobytes()).hexdigest())
    marker = json.dumps(identity)
    image = next((image for image in bpy.data.images
                  if image.get('arc_lut_raw_source') == marker
                  and image.colorspace_settings.name == 'Non-Color'
                  and image.alpha_mode == 'CHANNEL_PACKED'), None)
    if image is None:
        if path:
            loaded = bpy.data.images.load(path, check_existing=False)
            try:
                loaded.colorspace_settings.name = 'Non-Color'
                loaded.alpha_mode = 'CHANNEL_PACKED'
                width, height = loaded.size
                values = array.array('f', [0.]) * len(loaded.pixels)
                loaded.pixels.foreach_get(values)
            finally:
                bpy.data.images.remove(loaded)
        else:
            width, height = source.size
        # Image.copy() copies its ID, not a reliable snapshot of the runtime
        # pixel buffer. Allocate and explicitly populate a dedicated buffer.
        # Map UV also premultiplies its image input: LUT alpha has no grading
        # meaning, so make every stored sample explicitly opaque.
        for index in range(3, len(values), 4):
            values[index] = 1.
        image = bpy.data.images.new('Arc LUT Raw ' + (os.path.basename(path) if path else source.name),
                                    width=width, height=height, alpha=True, float_buffer=True)
        image.colorspace_settings.name = 'Non-Color'
        image.alpha_mode = 'CHANNEL_PACKED'
        image.pixels.foreach_set(values)
        image.update()
        image.pack()
        image['arc_lut_raw_source'] = marker
    strip_size(*image.size)
    return image, marker


class _Graph:
    def __init__(self, tree):
        self.tree = tree
        self.nodes, self.links = tree.nodes, tree.links
        self.count = 0

    def node(self, kind, label):
        node = self.nodes.new(kind)
        node.name = label
        node.label = label
        node.location = ((self.count % 10) * 220, -(self.count // 10) * 190)
        self.count += 1
        return node

    def put(self, socket, value):
        if hasattr(value, 'node'):
            self.links.new(value, socket)
        else:
            socket.default_value = value

    def math(self, operation, a, b=0., label=''):
        node = self.node('ShaderNodeMath', label or operation)
        node.operation = operation
        self.put(node.inputs[0], a)
        self.put(node.inputs[1], b)
        return node.outputs[0]

    def clamp(self, value):
        return self.math('MINIMUM', self.math('MAXIMUM', value, 0.), 1.)

    def lerp(self, a, b, factor):
        return self.math('ADD', a, self.math('MULTIPLY', self.math('SUBTRACT', b, a), factor))

    def transfer(self, value, encode):
        if encode:
            low = self.math('MULTIPLY', value, 12.92)
            high = self.math('SUBTRACT', self.math('MULTIPLY',
                    self.math('POWER', self.math('MAXIMUM', value, 0.), 1 / 2.4), 1.055), .055)
            threshold = .0031308
        else:
            low = self.math('DIVIDE', value, 12.92)
            high = self.math('POWER', self.math('MAXIMUM', self.math('DIVIDE',
                    self.math('ADD', value, .055), 1.055), 0.), 2.4)
            threshold = .04045
        return self.lerp(low, high, self.math('GREATER_THAN', value, threshold))


def ensure_lut_group(source, *, display_referred=True, premultiplied=True):
    """Return a cached compositor group with Image/Strength inputs and Image output.

    The two Map UV samplers perform bilinear red/green interpolation within two
    blue slices; an explicit blue-axis lerp completes exact trilinear sampling.
    No scene color-management or compositor attachment is changed here.
    """
    import bpy
    image, marker = _owned_lut_image(source)
    size = strip_size(*image.size)
    identity = json.dumps((marker, SCHEMA_VERSION, bool(display_referred), bool(premultiplied)))
    name = 'ArcLighting3DLUT ' + hashlib.sha256(identity.encode()).hexdigest()[:20]
    tree = bpy.data.node_groups.get(name)
    if tree is not None and tree.get('arc_lut_identity') == identity:
        return tree
    tree = bpy.data.node_groups.new(name, 'CompositorNodeTree')
    tree.interface.new_socket(name='Image', in_out='INPUT', socket_type='NodeSocketColor')
    strength = tree.interface.new_socket(name='Strength', in_out='INPUT', socket_type='NodeSocketFloat')
    strength.default_value = 1.
    strength.min_value, strength.max_value = 0., 1.
    tree.interface.new_socket(name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
    graph = _Graph(tree)
    inp = graph.node('NodeGroupInput', 'Linear input')
    out = graph.node('NodeGroupOutput', 'Linear LUT output')
    rgba = graph.node('CompositorNodeSeparateColor', 'Input channels and original alpha')
    graph.put(rgba.inputs['Image'], inp.outputs['Image'])
    alpha = rgba.outputs['Alpha']
    safe_alpha = graph.math('MAXIMUM', alpha, 1e-8)
    original = [graph.math('DIVIDE', rgba.outputs[channel], safe_alpha)
                if premultiplied else rgba.outputs[channel] for channel in ('Red', 'Green', 'Blue')]
    coordinates = [graph.clamp(graph.transfer(value, True) if display_referred else value) for value in original]
    red, green, blue = [graph.math('MULTIPLY', value, size - 1) for value in coordinates]
    lower = graph.math('FLOOR', blue)
    upper = graph.math('MINIMUM', graph.math('ADD', lower, 1.), size - 1)
    factor = graph.math('SUBTRACT', blue, lower)
    vertical = graph.math('SUBTRACT', 1., graph.math('DIVIDE', graph.math('ADD', green, .5), size))
    lut = graph.node('CompositorNodeImage', 'Dedicated raw display LUT')
    lut.image = image
    samples = []
    for label, slice_index in (('Lower', lower), ('Upper', upper)):
        horizontal = graph.math('DIVIDE', graph.math('ADD',
                       graph.math('ADD', graph.math('MULTIPLY', slice_index, size), red), .5), size * size)
        uv = graph.node('ShaderNodeCombineXYZ', label + ' slice UV; Z is opaque coverage')
        graph.put(uv.inputs['X'], horizontal)
        graph.put(uv.inputs['Y'], vertical)
        uv.inputs['Z'].default_value = 1.
        sampler = graph.node('CompositorNodeMapUV', label + ' slice bilinear sampling')
        # Newly declared Map UV nodes default to bilinear. Staying at pixel
        # centers within each slice prevents inter-slice filtering/edge bleed.
        graph.put(sampler.inputs['Image'], lut.outputs['Image'])
        graph.put(sampler.inputs['UV'], uv.outputs['Vector'])
        channels = graph.node('CompositorNodeSeparateColor', label + ' sample channels')
        graph.put(channels.inputs['Image'], sampler.outputs['Image'])
        samples.append(channels)
    combine = graph.node('CompositorNodeCombineColor', 'Graded RGB with unchanged input alpha')
    amount = graph.clamp(inp.outputs['Strength'])
    for index, channel in enumerate(('Red', 'Green', 'Blue')):
        graded = graph.lerp(samples[0].outputs[channel], samples[1].outputs[channel], factor)
        if display_referred:
            graded = graph.transfer(graded, False)
        blended = graph.lerp(original[index], graded, amount)
        if premultiplied:
            blended = graph.math('MULTIPLY', blended, alpha)
        graph.put(combine.inputs[channel], blended)
    graph.put(combine.inputs['Alpha'], alpha)
    graph.put(out.inputs['Image'], combine.outputs['Image'])
    tree['arc_lut_identity'] = identity
    tree['arc_lut_schema'] = SCHEMA_VERSION
    tree['arc_lut_size'] = size
    tree['arc_lut_sampling'] = 'two bilinear RG samples + linear blue slice interpolation; UV.Z=1'
    tree['arc_lut_contract'] = 'linear-sRGB-strip-sRGB-linear' if display_referred else 'raw-strip-raw'
    tree['arc_lut_limitations'] = 'UE tonemapper, gamut mapping, lighting and runtime exposure are separate.'
    return tree


def build_lut_node(tree, input_socket, source, *, label='ArcLighting3DLUT',
                   location=(0., 0.), display_referred=True, premultiplied=True, strength=1.):
    node = tree.nodes.new('CompositorNodeGroup')
    node.name = label
    node.label = label
    node.node_tree = ensure_lut_group(source, display_referred=display_referred,
                                     premultiplied=premultiplied)
    node.location = location
    tree.links.new(input_socket, node.inputs['Image'])
    node.inputs['Strength'].default_value = float(strength)
    return node
