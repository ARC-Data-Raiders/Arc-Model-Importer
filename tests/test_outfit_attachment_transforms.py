import ast
import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy
from mathutils import Euler, Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent / 'native_runtime'))
from native_test_support import namespace
namespace()
rig = importlib.import_module('_native_runtime_regression_addon.Animations.rig')
ROOT = Path(__file__).resolve().parents[1]


def merge_function():
    tree = ast.parse((ROOT / 'Outfit_Accessories/appearance.py').read_text(encoding='utf-8-sig'))
    names = {'merge_into_armature', '_object_alive', '_mesh_is_skinned'}
    importing = importlib.import_module('_native_runtime_regression_addon.Shared.importing')
    scope = {'bpy': bpy, 'rig': rig, 'importing': importing, '__package__': '_native_runtime_regression_addon.Outfit_Accessories'}
    exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[]), 'appearance.py', 'exec'), scope)
    return scope['merge_into_armature']


def armature(name, units=1.0, extra=False):
    bpy.ops.object.select_all(action='DESELECT')
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    parent = None
    for index, label in enumerate(('root', 'spine', 'face') if extra else ('root', 'spine')):
        bone = data.edit_bones.new(label)
        bone.head = (0, 0, index * units)
        bone.tail = (0, 0, (index + 0.5) * units)
        bone.parent = parent
        parent = bone
    bpy.ops.object.mode_set(mode='OBJECT')
    obj['arc_ue_unit_scale'] = 1.0 / units
    return obj


def mesh_for(arm, units=1.0, pivot=False):
    data = bpy.data.meshes.new('Part')
    vertices = [(0.1 * units, 0, units), (-0.1 * units, 0, units), (0, 0.1 * units, 1.5 * units)]
    data.from_pydata(vertices, [], [(0, 1, 2)])
    obj = bpy.data.objects.new('Part', data)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = arm
    mod = obj.modifiers.new('Armature', 'ARMATURE')
    mod.object = arm
    obj.vertex_groups.new(name='spine').add(range(3), 1.0, 'REPLACE')
    if pivot:
        offset = Matrix.Translation((0.1 * units, 0, units))
        data.transform(offset.inverted())
        obj.matrix_basis = offset
    return obj


def world_vertices(obj):
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    return [evaluated.matrix_world @ vertex.co for vertex in evaluated.data.vertices]


class OutfitAttachmentTransformTests(unittest.TestCase):
    def setUp(self):
        self.before = set(bpy.data.objects)

    def tearDown(self):
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in list(bpy.data.objects):
            if obj not in self.before:
                bpy.data.objects.remove(obj, do_unlink=True)

    def test_pose_units_ignore_distant_attachment_bone(self):
        module = importlib.import_module('_native_runtime_regression_addon.Animations.animation_import')
        for units, expected in ((1.0, 0.01), (100.0, 1.0)):
            arm = armature('Character', units, extra=True)
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode='EDIT')
            socket = arm.data.edit_bones.new('socket')
            socket.head = (0, 0, 20 * units)
            socket.tail = (0, 0, 21 * units)
            bpy.ops.object.mode_set(mode='OBJECT')
            self.assertEqual(module._psa_translation_scale(arm), expected)

    def test_static_part_preserves_size_and_follows_moved_outfit(self):
        master = armature('Outfit', 100.0)
        master.matrix_world = Matrix.LocRotScale(Vector((5, -3, 2)), Euler((0.2, 0.1, 0.7)).to_quaternion(), Vector((0.02, 0.03, 0.015)))
        temporary = armature('Temporary')
        mesh = mesh_for(temporary, pivot=True)
        world = mesh.matrix_world.copy()
        mesh.modifiers.clear()
        mesh.vertex_groups.clear()
        mesh.parent = None
        mesh.matrix_world = world
        expected = [master.matrix_world @ Matrix.Scale(100, 4) @ point for point in world_vertices(mesh)]
        merge_function()(master, [mesh])
        actual = world_vertices(mesh)
        self.assertLess(max((point - reference).length for point, reference in zip(actual, expected)), 1e-4)
        master.location.x += 3
        self.assertLess(max((point - reference - Vector((3, 0, 0))).length for point, reference in zip(world_vertices(mesh), actual)), 1e-4)

    def test_hair_without_body_uses_outfit_transform_instead_of_pad(self):
        hair = importlib.import_module('_native_runtime_regression_addon.Outfit_Accessories.hair_placement')
        master = armature('Outfit', 100.0)
        master.matrix_world = Matrix.LocRotScale(Vector((5, -3, 2)), Euler((0.2, 0.1, 0.7)).to_quaternion(), Vector((0.02, 0.03, 0.015)))
        mesh = mesh_for(master)
        mesh['arc_import_slot'] = 3
        mesh['arc_assembly_role'] = 'Hair'
        expected = master.matrix_world @ Matrix.Scale(100, 4)
        with patch.object(hair, '_source', return_value='/Characters/Hairs/Fixture.uemodel'), patch.object(hair, '_frame', return_value=Matrix.Identity(4)), patch.object(hair.uemodel_import, 'parse_uemodel_bones', return_value=[object()]):
            ok, message = hair.place_hair_at_head(mesh, bpy.context.scene)
        bpy.context.view_layer.update()
        self.assertTrue(ok, message)
        self.assertLess(max(abs(mesh.matrix_world[row][col] - expected[row][col]) for row in range(4) for col in range(4)), 1e-4)

    def test_static_hair_without_body_retains_outfit_frame_on_repeat(self):
        hair = importlib.import_module('_native_runtime_regression_addon.Outfit_Accessories.hair_placement')
        appearance = importlib.import_module('_native_runtime_regression_addon.Outfit_Accessories.appearance')
        master = armature('Outfit', 100.0)
        master.matrix_world = Matrix.LocRotScale(Vector((5, -3, 2)), Euler((0.2, 0.1, 0.7)).to_quaternion(), Vector((0.02, 0.03, 0.015)))
        mesh = mesh_for(master)
        mesh.modifiers.clear()
        mesh.vertex_groups.clear()
        anchor = Matrix.Translation((0, 0, 1.6))
        expected = master.matrix_world @ Matrix.Scale(100, 4) @ anchor
        with patch.object(hair, '_source', return_value='/Characters/Hairs/Fixture.uemodel'), patch.object(hair, '_frame', return_value=Matrix.Identity(4)), patch.object(hair, '_head_anchor', return_value=anchor), patch.object(hair.uemodel_import, 'parse_uemodel_bones', return_value=[]), patch.object(appearance, 'list_body_mesh', return_value='reference.uemodel'), patch.object(appearance, 'list_head_choices', return_value=[]), patch.object(hair.os.path, 'isfile', return_value=True):
            for repeat in range(2):
                ok, message = hair.place_static_hair(mesh, bpy.context.scene)
                bpy.context.view_layer.update()
                self.assertTrue(ok, message)
                self.assertLess(max(abs(mesh.matrix_world[row][col] - expected[row][col]) for row in range(4) for col in range(4)), 1e-4)

    def test_parts_merge_in_outfit_space_after_move_rotate_scale(self):
        for units, pivot, snap in ((1.0, False, True), (1.0, True, True), (100.0, True, True), (1.0, True, False)):
            with self.subTest(units=units, pivot=pivot, snap=snap):
                master = armature('Outfit', 100.0)
                master.matrix_world = Matrix.LocRotScale(Vector((5, -3, 2)), Euler((0.2, 0.1, 0.7)).to_quaternion(), Vector((0.02, 0.03, 0.015)))
                incoming = armature('Head', units, extra=True)
                incoming.scale = (1.0 / units,) * 3
                mesh = mesh_for(incoming, units, pivot)
                bpy.context.view_layer.update()
                source_to_master = master.matrix_world @ Matrix.Scale(100.0 / units, 4) @ incoming.matrix_world.inverted()
                expected = [source_to_master @ point for point in world_vertices(mesh)]
                merge_function()(master, [incoming, mesh], snap_skinned_world=snap)
                actual = world_vertices(mesh)
                self.assertEqual(set(master.data.bones.keys()), {'root', 'spine', 'face'})
                self.assertLess(max((point - reference).length for point, reference in zip(actual, expected)), 1e-4)
                self.assertAlmostEqual(master.data.bones['face'].head_local.z, 200.0, places=3)
                master.pose.bones['spine'].location.x = 10
                moved = world_vertices(mesh)
                delta = master.matrix_world.to_3x3() @ Vector((10, 0, 0))
                self.assertLess(max((after - before - delta).length for before, after in zip(actual, moved)), 1e-4)


if __name__ == '__main__':
    unittest.main()
