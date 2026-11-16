"""Real-socket regression checks; run with background Blender, not system Python."""
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT.parent))
addon = importlib.import_module(ROOT.name)
interop = importlib.import_module(ROOT.name + '.Shared.materials.native.interop')
bulbs = importlib.import_module(ROOT.name + '.Shared.materials.rebuild.lightbulb')
decals = importlib.import_module(ROOT.name + '.Shared.materials.rebuild.decals')


def record(**scalars):
    return {'scalars': scalars, 'colors': {}, 'textures': {}, 'switches': {}, 'settings': {}}


def trees(tree):
    pending = [tree]
    seen = set()
    while pending:
        current = pending.pop()
        if current.as_pointer() in seen:
            continue
        seen.add(current.as_pointer())
        yield current
        pending.extend(node.node_tree for node in current.nodes if node.type == 'GROUP' and node.node_tree is not None)


def nodes(tree):
    return [node for current in trees(tree) for node in current.nodes]


class ShaderControls(unittest.TestCase):
    def setUp(self):
        self.material = bpy.data.materials.new('Upgrade fixture')
        self.mesh = bpy.data.meshes.new('Upgrade mesh')
        self.mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        self.mesh.uv_layers.new(name='UV0')
        self.obj = bpy.data.objects.new('Upgrade object', self.mesh)
        bpy.context.scene.collection.objects.link(self.obj)
        self.obj.data.materials.append(self.material)
        self.graph = interop.Graph(self.material, self.obj, SimpleNamespace(), {'warnings': [], 'missing_textures': []})

    def tearDown(self):
        bpy.data.objects.remove(self.obj, do_unlink=True)
        bpy.data.meshes.remove(self.mesh)
        bpy.data.materials.remove(self.material)

    def physical(self):
        return next(node for node in self.graph.tree.nodes if node.bl_idname == 'ShaderNodeBsdfPrincipled')

    def test_window_preserves_authored_zeroes(self):
        decals.build_window(self.graph, record(OpacityOutside=0, Roughness=0, Specular=0))
        shader = self.physical()
        alpha = shader.inputs['Alpha']
        self.assertTrue(alpha.is_linked or alpha.default_value == 0)
        self.assertNotEqual(alpha.default_value, .35)
        self.assertEqual(shader.inputs['Specular IOR Level'].default_value, 0)

    def test_window_has_real_transmission_and_inside_outside_controls(self):
        decals.build_window(self.graph, record(OpacityOutside=.2, OpacityInside=.7, IOR=1.45))
        shader = self.physical()
        self.assertTrue(shader.inputs['Transmission Weight'].is_linked or shader.inputs['Transmission Weight'].default_value > 0)
        self.assertTrue(any(node.bl_idname == 'ShaderNodeNewGeometry' for node in nodes(self.graph.tree)))
        labels = {node.label for node in nodes(self.graph.tree)}
        self.assertTrue({'OpacityOutside', 'OpacityInside', 'IOR'} <= labels)

    def test_translucent_zero_is_not_replaced_by_default(self):
        decals.build_translucent_glass(self.graph, record(BaseOpacity=0, DirtOpacity=0, Specular=0))
        shader = self.physical()
        alpha = shader.inputs['Alpha']
        self.assertTrue(alpha.is_linked or alpha.default_value == 0)
        self.assertFalse(alpha.default_value == .1 and not alpha.is_linked)
        self.assertEqual(shader.inputs['Specular IOR Level'].default_value, 0)

    def test_pulse_is_timeline_driven_and_rate_independent(self):
        data = record(LightIntensity=20, PulseAmount=.75, PulseSpeed=2)
        data['switches']['Pulsating'] = True
        bulbs.build_lightbulb(self.graph, data)
        tree = self.graph.tree
        animated = [current for current in trees(tree) if current.animation_data and current.animation_data.drivers]
        self.assertEqual(sum(len(current.animation_data.drivers) for current in animated), 1)
        seconds = next(node for node in nodes(tree) if node.label == 'Light time (seconds)' and node.bl_idname == 'ShaderNodeValue')
        scene = bpy.context.scene
        for fps, base, frame in ((24, 1, 24), (60, 1, 60), (48, 2, 24)):
            scene.render.fps = fps
            scene.render.fps_base = base
            scene.frame_set(frame)
            scene.frame_set(frame + 1)
            scene.frame_set(frame)
            self.assertAlmostEqual(seconds.outputs[0].default_value, 1., places=5)
        self.assertTrue(any('approximate:' in warning for warning in self.graph.report['warnings']))

    def test_nonpulsating_light_has_no_animation_overhead(self):
        bulbs.build_lightbulb(self.graph, record(LightIntensity=0))
        self.assertIsNone(self.graph.tree.animation_data)
        self.assertFalse(any(node.label == 'Pulse Amount' for node in self.graph.tree.nodes))

    def test_bump_offset_builds_view_projection(self):
        data = record(BumpOffset=.02)
        data['textures']['Lightbulb'] = ''
        bulbs.build_lightbulb(self.graph, data)
        labels = {node.label for node in nodes(self.graph.tree)}
        self.assertIn('Bulb tangent', labels)
        self.assertIn('BumpOffset', labels)
        self.assertTrue(any('parallax' in warning for warning in self.graph.report['warnings']))


class FurControls(unittest.TestCase):
    def test_current_compact_export_keeps_fur_identity_values_and_texture_references(self):
        fur = importlib.import_module(ROOT.name + '.Shared.materials.fur_shells')
        weapon = importlib.import_module(ROOT.name + '.Shared.materials.weapon_fur')
        compact = {'Scalars': {'Length': .1}, 'Colors': {'RootColor': {'R': .2, 'G': .3, 'B': .4, 'A': 1}},
                   'Textures': {'FurMask': {'AssetPathName': '/Game/Test/Mask.Mask'}},
                   'Properties': {'StateId': '0EFD3174-42A2521C-D62D71BB-C1D84A42'}}
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'MI_Fur.json'
            source.write_text(json.dumps(compact), encoding='utf-8')
            parsed = fur.profile(str(source))
            self.assertEqual(parsed['kind'], 'shells')
            for value in (parsed, weapon.profile(str(source))):
                self.assertEqual(value['scalars']['Length'], .1)
                self.assertEqual(value['colors']['RootColor'], (.2, .3, .4, 1.))
                self.assertEqual(value['textures']['FurMask'], '/Game/Test/Mask')

    def test_length_is_per_object_idempotent_and_non_destructive(self):
        controls = importlib.import_module(ROOT.name + '.Shared.materials.fur_controls')
        mesh = bpy.data.meshes.new('Fur mesh')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        mesh.attributes.new('test_fur_offset', 'FLOAT_VECTOR', 'POINT')
        for value in mesh.attributes['test_fur_offset'].data:
            value.vector = (0, 0, .1)
        objects = []
        try:
            for number in range(2):
                obj = bpy.data.objects.new('Fur control ' + str(number), mesh)
                bpy.context.scene.collection.objects.link(obj)
                obj.shape_key_add(name='Basis')
                objects.append(obj)
                group = bpy.data.node_groups.new('Fur offset fixture', 'GeometryNodeTree')
                group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
                group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
                inp = group.nodes.new('NodeGroupInput')
                out = group.nodes.new('NodeGroupOutput')
                offset = group.nodes.new('GeometryNodeInputNamedAttribute')
                offset.data_type = 'FLOAT_VECTOR'
                offset.inputs['Name'].default_value = 'test_fur_offset'
                position = group.nodes.new('GeometryNodeSetPosition')
                group.links.new(inp.outputs['Geometry'], position.inputs['Geometry'])
                group.links.new(offset.outputs['Attribute'], position.inputs['Offset'])
                group.links.new(position.outputs['Geometry'], out.inputs['Geometry'])
                mod = obj.modifiers.new('Fur control fixture', 'NODES')
                mod.node_group = group
                socket = controls.ensure_length_control(mod)
                self.assertEqual(mod[socket], 1.)
                if number == 0:
                    mod[socket] = 2.
                self.assertEqual(controls.ensure_length_control(mod), socket)
                self.assertEqual(mod[socket], 2. if number == 0 else 1.)
                self.assertEqual(sum(node.bl_idname == 'ShaderNodeVectorMath' for node in group.nodes), 1)
                for amount in (0., 1., 2.):
                    mod[socket] = amount
                    obj.update_tag()
                    bpy.context.view_layer.update()
                    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
                    self.assertAlmostEqual(evaluated.data.vertices[0].co.z, .1 * amount, places=6)
                self.assertEqual(mesh.vertices[0].co.z, 0.)
                self.assertEqual(mesh.shape_keys.reference_key.data[0].co.z, 0.)
        finally:
            for obj in objects:
                bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)

    def test_artist_tints_survive_rebuild_but_unedited_defaults_follow_colorways(self):
        controls = importlib.import_module(ROOT.name + '.Shared.materials.fur_controls')
        fur = importlib.import_module(ROOT.name + '.Shared.materials.fur_shells')
        mat = bpy.data.materials.new('Fur colour fixture')
        mat.use_nodes = True
        try:
            graph = fur._Graph(mat, '')
            socket = controls.color_control(graph, 'Root Color', (.2, .3, .4, 1), {})
            self.assertEqual(controls.material_overrides(mat), {})
            socket.default_value = (.6, .2, .1, 1)
            saved = controls.material_overrides(mat)
            mat.node_tree.nodes.clear()
            graph = fur._Graph(mat, '')
            rebuilt = controls.color_control(graph, 'Root Color', (.9, .9, .9, 1), saved)
            self.assertAlmostEqual(rebuilt.default_value[0], .6, places=6)
        finally:
            bpy.data.materials.remove(mat)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt = {'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'blender': bpy.app.version_string}
    destination = ROOT / 'temp/shader-upgrade/shader-check.json'
    destination.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print('SHADER_UPGRADE ' + json.dumps(receipt))
    if not result.wasSuccessful():
        raise RuntimeError('Shader upgrade regression failure')
