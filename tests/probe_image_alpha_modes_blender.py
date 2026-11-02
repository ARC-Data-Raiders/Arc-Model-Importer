import argparse
import json
import pathlib
import struct
import sys
import zlib
import bpy


def png(path, texels):
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)
    raw = b"\x00" + bytes(channel for texel in texels for channel in texel)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", len(texels), 1, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def setup(width, center, x0, x1):
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.cycles.pixel_filter_type = 'BOX'
    scene.cycles.filter_width = 1.0
    scene.render.threads_mode = 'FIXED'
    scene.render.threads = 4
    scene.render.resolution_x = width
    scene.render.resolution_y = 4
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'OPEN_EXR'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.image_settings.color_depth = '32'
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    camera_data = bpy.data.cameras.new('ProbeCamera')
    camera_data.type = 'ORTHO'
    camera_data.ortho_scale = width
    camera_data.sensor_fit = 'HORIZONTAL'
    camera = bpy.data.objects.new('ProbeCamera', camera_data)
    scene.collection.objects.link(camera)
    camera.location = (center, 0, 10)
    scene.camera = camera
    mesh = bpy.data.meshes.new('ProbePlane')
    mesh.from_pydata([(x0, -2, 0), (x1, -2, 0), (x1, 2, 0), (x0, 2, 0)], [], [(0, 1, 2, 3)])
    mesh.update()
    uv = mesh.uv_layers.new(name='UVMap')
    for index, value in enumerate([(0, 0), (1, 0), (1, 1), (0, 1)]):
        uv.data[index].uv = value
    plane = bpy.data.objects.new('ProbePlane', mesh)
    scene.collection.objects.link(plane)
    material = bpy.data.materials.new('ProbeMaterial')
    material.use_nodes = True
    plane.data.materials.append(material)
    material.node_tree.nodes.clear()
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    texture = nodes.new('ShaderNodeTexImage')
    emission = nodes.new('ShaderNodeEmission')
    emission.inputs['Strength'].default_value = 1
    output = nodes.new('ShaderNodeOutputMaterial')
    links.new(texture.outputs['Color'], emission.inputs['Color'])
    links.new(emission.outputs['Emission'], output.inputs['Surface'])
    return scene, nodes, links, texture


def render(scene, path):
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(path), check_existing=False)
    values = list(image.pixels[:])
    width, height = image.size[:]
    bpy.data.images.remove(image)
    rows = [values[y * width * 4:(y + 1) * width * 4] for y in range(height)]
    bits = [struct.pack("<" + "f" * len(row), *row) for row in rows]
    identical = height == 4 and all(row == bits[0] for row in bits)
    row_checks.append({"file": path.name, "width": width, "height": height, "rows_bit_identical": identical})
    (output / 'row_checks.json').write_text(json.dumps(row_checks, indent=2) + '\n', encoding='utf-8', newline='\n')
    if not identical:
        raise RuntimeError("Four render rows are not bit-identical: " + path.name)
    return [rows[0][index:index + 4] for index in range(0, len(rows[0]), 4)]


parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
output = pathlib.Path(args.output)
output.mkdir(parents=True, exist_ok=True)
row_checks = []

source = output / 'alpha.png'
texel_alphas = [255, 128, 1, 0]
png(source, [(255, 128, 64, a) for a in texel_alphas])
scene, nodes, links, texture = setup(4, 0, -2, 2)
texture.interpolation = 'Closest'
aov = bpy.context.view_layer.aovs.add()
aov.name = 'a'
aov.type = 'VALUE'
rows = []
expected = [1.0, 128/255, 64/255]
for mode in ['STRAIGHT', 'CHANNEL_PACKED']:
    for linked in [False, True]:
        image = bpy.data.images.load(str(source), check_existing=False)
        image.colorspace_settings.name = 'Non-Color'
        image.alpha_mode = mode
        texture.image = image
        add = aov_node = None
        if linked:
            add = nodes.new('ShaderNodeMath')
            add.operation = 'ADD'
            add.inputs[1].default_value = 0
            aov_node = nodes.new('ShaderNodeOutputAOV')
            aov_node.name = 'a'
            links.new(texture.outputs['Alpha'], add.inputs[0])
            links.new(add.outputs[0], aov_node.inputs['Value'])
        pixels = render(scene, output / (mode + '_' + str(linked) + '.exr'))
        for alpha_value, pixel in zip(texel_alphas, pixels):
            rgb = pixel[:3]
            rows.append({'alpha_mode': mode, 'alpha_linked': linked, 'texel_alpha': alpha_value, 'expected_rgb': expected, 'measured_rgb': rgb, 'max_abs_err': max(abs(a-b) for a, b in zip(expected, rgb))})
        if linked:
            nodes.remove(aov_node)
            nodes.remove(add)
        texture.image = None
        bpy.data.images.remove(image)
result = {'rows': rows, 'straight_corrupts': any(r['max_abs_err'] > 1/255 for r in rows if r['alpha_mode'] == 'STRAIGHT'), 'channel_packed_exact': all(r['max_abs_err'] <= 1e-6 for r in rows if r['alpha_mode'] == 'CHANNEL_PACKED')}
(output / 'alpha_probe.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8', newline='\n')
print(json.dumps(result, indent=2))
