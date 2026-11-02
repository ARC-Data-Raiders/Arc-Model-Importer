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

source = output / 'address.png'
png(source, [(v, v, v, 255) for v in [0, 64, 128, 255]])
scene, nodes, links, texture = setup(9, 4.5, 0, 9)
texture.interpolation = 'Linear'
image = bpy.data.images.load(str(source), check_existing=False)
image.colorspace_settings.name = 'Non-Color'
texture.image = image
coordinates = nodes.new('ShaderNodeTexCoord')
separate = nodes.new('ShaderNodeSeparateXYZ')
links.new(coordinates.outputs['Object'], separate.inputs[0])
floor = nodes.new('ShaderNodeMath')
floor.operation = 'FLOOR'
links.new(separate.outputs['X'], floor.inputs[0])
scale = nodes.new('ShaderNodeMath')
scale.operation = 'MULTIPLY'
scale.inputs[1].default_value = .25
links.new(floor.outputs[0], scale.inputs[0])
offset = nodes.new('ShaderNodeMath')
offset.operation = 'ADD'
offset.inputs[1].default_value = -.5625
links.new(scale.outputs[0], offset.inputs[0])
vector = nodes.new('ShaderNodeCombineXYZ')
vector.inputs['Y'].default_value = .5
links.new(offset.outputs[0], vector.inputs['X'])
links.new(vector.outputs[0], texture.inputs['Vector'])
measured = {}
for extension in ['REPEAT', 'EXTEND', 'MIRROR', 'CLIP']:
    texture.extension = extension
    measured[extension] = [p[0] for p in render(scene, output / (extension + '.exr'))]
reference = json.loads((pathlib.Path(r'Z:\Datamining\Arc Raiders\CodexScratch\DataRaiders-BlenderImporter\.local\parity-v2\wave1') / 'out/W6/t5_address.json').read_text(encoding='utf-8'))
comparisons = []
for extension, mode in [('REPEAT', 'WRAP'), ('EXTEND', 'CLAMP'), ('MIRROR', 'MIRROR')]:
    errors = [abs(a-b) for a, b in zip(measured[extension], reference[mode])]
    comparisons.append({'pair': extension + '<->' + mode, 'max_abs_err': max(errors), 'per_pixel': errors})
result = {'measured': measured, 'comparisons': comparisons}
(output / 'address_probe.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8', newline='\n')
print(json.dumps(result, indent=2))
