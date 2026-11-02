"""Wave N6 (spec N-O5): playback cache, binding resolution and the reference renderer maths (no bpy)."""
import json
import math
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

import niagara_cache as nc
import niagara_render_ref as ref

CORPUS = os.environ.get('DR_NIAGARA_CORPUS')
SHIM = os.environ.get('DR_SVML_SHIM')
VECTOR_FIELDS = os.environ.get('DR_NIAGARA_VECTOR_FIELDS')
M = np.diag([1.0, -1.0, 1.0])


def front_camera():
    """Camera at (0, -10, 0) looking along +Y with Z up (Blender rotation (90, 0, 0) deg)."""
    return {'pos': np.array([0.0, -10.0, 0.0]), 'x': np.array([1.0, 0.0, 0.0]), 'y': np.array([0.0, 0.0, 1.0]), 'z': np.array([0.0, -1.0, 0.0])}


def sprite_cols(**over):
    cols = {p: np.zeros(1, np.float32) for p in nc.props_of('sprite')}
    cols['nx_valid'][:] = 1
    for key, value in {'size_0': 2.0, 'size_1': 1.0, 'pivot_0': 0.5, 'pivot_1': 0.5, 'uvs_0': 1.0, 'uvs_1': 1.0}.items():
        cols[key][:] = value
    for key, value in over.items():
        cols[key][:] = value
    return cols


def sprite_info(**props):
    pr = dict(nc.DEFAULTS['sprite'])
    pr.update(props)
    pr.setdefault('Alignment_resolved', 'Unaligned')
    pr.setdefault('FacingMode_resolved', 'FaceCamera')
    return {'kind': 'sprite', 'props': pr}


class Conventions(unittest.TestCase):
    def test_cross_ue_is_mirrored_cross(self):
        rng = np.random.default_rng(1)
        a, b = rng.normal(size=3), rng.normal(size=3)
        np.testing.assert_allclose(ref.cross_ue(M @ a, M @ b), M @ np.cross(a, b), atol=1e-12)

    def test_quat_conversion_is_conjugation(self):
        rng = np.random.default_rng(2)
        for _ in range(5):
            q = rng.normal(size=4)
            q /= np.linalg.norm(q)
            qb = np.array(nc.quat_b(q))
            np.testing.assert_allclose(ref.quat_matrix(qb), M @ ref.quat_matrix(q) @ M, atol=1e-12)

    def test_rotator_yaw(self):
        np.testing.assert_allclose(nc.rotator_quat({'Yaw': 90.0}), [0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5)], atol=1e-12)

    def test_quat_mul_order(self):
        qa, qb = np.array([0, 0, math.sin(0.4), math.cos(0.4)]), np.array([math.sin(0.3), 0, 0, math.cos(0.3)])
        np.testing.assert_allclose(ref.quat_matrix(ref.quat_mul(qa, qb)), ref.quat_matrix(qa) @ ref.quat_matrix(qb), atol=1e-12)


class Sprite(unittest.TestCase):
    def test_face_camera_top_left_corner(self):
        r = ref.sprite_frame(sprite_cols(), sprite_info(), front_camera())
        np.testing.assert_allclose(r['corners'][0], [[-1, 0, 0.5], [1, 0, 0.5], [1, 0, -0.5], [-1, 0, -0.5]], atol=1e-12)

    def test_positive_rotation_is_clockwise_on_screen(self):
        r = ref.sprite_frame(sprite_cols(rot=90.0, size_0=1.0), sprite_info(), front_camera())
        right_mid = (r['corners'][0, 1] + r['corners'][0, 2]) / 2
        np.testing.assert_allclose(right_mid, [0, 0, -0.5], atol=1e-12)

    def test_negative_size_flips_texturing_only(self):
        a = ref.sprite_frame(sprite_cols(size_0=-2.0), sprite_info(), front_camera())
        b = ref.sprite_frame(sprite_cols(), sprite_info(), front_camera())
        np.testing.assert_allclose(a['corners'], b['corners'], atol=1e-12)
        self.assertEqual(a['attrs']['nx_uvx'][0, 0], -1.0)

    def test_velocity_aligned_up_is_minus_direction(self):
        r = ref.sprite_frame(sprite_cols(vel_2=5.0, size_0=1.0, size_1=1.0), sprite_info(Alignment_resolved='VelocityAligned'), front_camera())
        top_mid = (r['corners'][0, 0] + r['corners'][0, 1]) / 2
        np.testing.assert_allclose(top_mid, [0, 0, 0.5], atol=1e-12)

    def test_visibility_and_custom_sort_rows(self):
        cols = sprite_cols()
        cols = {k: np.repeat(v, 3) for k, v in cols.items()}
        cols['vistag'][1] = 3
        r = ref.sprite_frame(cols, sprite_info(), front_camera())
        self.assertEqual(r['rows'].tolist(), [0, 2])

    def test_subuv_cell_and_blend(self):
        misc = np.array([[5.25, 0, 0, 1.0]])
        uvx = np.array([[1.0, 1.0, 4.0, 4.0]])
        ta, tb, lerp = ref.subuv(np.array([[0.5, 0.5]]), misc, uvx)
        np.testing.assert_allclose(ta[0, 0], [1.5 / 4, 1.5 / 4])
        np.testing.assert_allclose(tb[0, 0], [2.5 / 4, 1.5 / 4])
        self.assertAlmostEqual(lerp[0], 0.25)


class Mesh(unittest.TestCase):
    def test_facing_frames_are_proper(self):
        rng = np.random.default_rng(3)
        cam = front_camera()
        cols = {p: rng.normal(size=6).astype(np.float32) for p in nc.props_of('mesh')}
        cols['nx_valid'][:] = 1
        cols['vistag'][:] = 0
        cols['meshidx'][:] = 0
        for c in range(3):
            cols['scale_%d' % c][:] = 1
        entry = {'object_path': '/T', 'scale': [1, 1, 1], 'rotation_quat_ue': [0, 0, 0, 1], 'pivot_offset': [0, 0, 0], 'pivot_space': 'Mesh'}
        for over in ({'FacingMode': 'Velocity'}, {'FacingMode': 'CameraPlane'}, {'FacingMode': 'CameraPosition', 'bLockedAxisEnable': True}):
            pr = dict(nc.DEFAULTS['mesh'])
            pr.update(over)
            m = ref.mesh_frame(cols, {'kind': 'mesh', 'props': pr, 'meshes': [entry], 'mesh_index_bound': False}, cam, 0)['matrices']
            np.testing.assert_allclose(np.linalg.det(m[:, :3, :3]), 1.0, atol=1e-9)

    def test_mesh_index_selection(self):
        cols = {p: np.zeros(3, np.float32) for p in nc.props_of('mesh')}
        cols['nx_valid'][:] = 1
        cols['quat_3'][:] = 1
        cols['meshidx'][:] = [0, 1, 2]
        entry = {'object_path': '/T', 'scale': [1, 1, 1], 'rotation_quat_ue': [0, 0, 0, 1], 'pivot_offset': [0, 0, 0], 'pivot_space': 'Mesh'}
        info = {'kind': 'mesh', 'props': dict(nc.DEFAULTS['mesh']), 'meshes': [entry, entry], 'mesh_index_bound': True}
        self.assertEqual([ref.mesh_frame(cols, info, front_camera(), k)['rows'].tolist() for k in (0, 1)], [[0], [1]])


class Light(unittest.TestCase):
    def test_energy_and_gating(self):
        cols = {p: np.zeros(3, np.float32) for p in nc.props_of('light')}
        cols['nx_valid'][:] = 1
        cols['enabled'][:] = [1, 0, 1]
        cols['radius'][:] = [1.0, 1.0, 0.0]
        cols['col_0'][:] = 2.0
        cols['col_3'][:] = 0.5
        cols['uid'][:] = [7, 8, 9]
        pr = dict(nc.DEFAULTS['light'])
        pr['bAlphaScalesBrightness'] = True
        out = ref.light_frame(cols, {'kind': 'light', 'props': pr})
        self.assertEqual([s['uid'] for s in out], [7])
        self.assertAlmostEqual(out[0]['energy'], 1.0 / (1e4 * ref.E1_CYCLES))
        np.testing.assert_allclose(out[0]['color'], [1, 0, 0])

    def test_slots_are_stable(self):
        slots, size = ref.assign_slots([[5, 6], [6, 7], [7, 5, 8]])
        self.assertEqual((slots, size), ([[0, 1], [1, 0], [0, 1, 2]], 3))


class Cache(unittest.TestCase):
    def test_ply_roundtrip_and_sentinel(self):
        props = nc.props_of('light')
        rows = np.arange(2 * len(props), dtype=np.float32).reshape(2, len(props))
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'f.ply'
            p.write_bytes(nc.ply_bytes(props, rows))
            cols = ref.read_ply(p)
            self.assertEqual(list(cols), props)
            np.testing.assert_array_equal(np.stack([cols[k] for k in props], axis=1), rows)
            p.write_bytes(nc.ply_bytes(props, None))
            self.assertEqual(ref.read_ply(p)['nx_valid'].tolist(), [0.0])

    def test_binding_name_delta_serialization(self):
        self.assertIs(nc.binding_name({'bBindingExistsOnSource': True}), nc.DEFAULT_NAME)
        self.assertIs(nc.binding_name(None), nc.DEFAULT_NAME)
        self.assertIsNone(nc.binding_name({'ParamMapVariable': {'Name': 'None'}}))
        self.assertEqual(nc.binding_name({'ParamMapVariable': {'Name': 'Particles.GlowSize'}}), 'Particles.GlowSize')

    def test_sprite_class_defaults(self):
        class E:
            index, unique_name = 0, 'Em'
        info = nc.resolve_renderer(E, 0, 1, 'NiagaraSpriteRendererProperties', {})
        self.assertTrue(info['props']['bSubImageBlend'])
        self.assertEqual(info['bindings']['sortkey']['name'], 'Particles.Age')
        self.assertEqual(info['bindings']['pos']['name'], 'Particles.Position')
        self.assertEqual((info['props']['Alignment'], info['props']['FacingMode']), ('Automatic', 'Automatic'))

    def test_convert_positions(self):
        v = nc.convert('pos', np.array([[100.0, 200.0, 300.0]]), (10.0, 20.0, 30.0), (50.0, 0.0, 0.0), True)
        np.testing.assert_allclose(v, [[1.4, -1.8, 2.7]])

    def test_convert_local_space_rotation(self):
        q = nc.rotator_quat({'Yaw': 90.0})  # local +X -> UE world +Y
        x = np.array([[100.0, 0.0, 0.0]])
        np.testing.assert_allclose(nc.convert('pos', x, (0.0, 0.0, 0.0), (1000.0, 0.0, 0.0), True, q), [[10.0, -1.0, 0.0]], atol=1e-12)
        np.testing.assert_allclose(nc.convert('pos', x, (0.0, 0.0, 0.0), (1000.0, 0.0, 0.0), False, q), [[1.0, 0.0, 0.0]], atol=1e-12)
        np.testing.assert_allclose(nc.convert('vel', x, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), True, q), [[0.0, -1.0, 0.0]], atol=1e-12)
        np.testing.assert_allclose(nc.convert('dir', x / 100.0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), True, q), [[0.0, -1.0, 0.0]], atol=1e-12)
        qp = np.array([[math.sin(0.3), 0.0, 0.0, math.cos(0.3)]])
        world = ref.quat_mul(np.array(q)[None, :], qp)
        np.testing.assert_allclose(nc.convert('quat', qp, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), True, q), [nc.quat_b(world[0])], atol=1e-12)
        np.testing.assert_allclose(nc.convert('quat', qp, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), False, q), [nc.quat_b(qp[0])], atol=1e-12)


@unittest.skipUnless(CORPUS and SHIM and VECTOR_FIELDS, 'DR_NIAGARA_CORPUS, DR_SVML_SHIM or DR_NIAGARA_VECTOR_FIELDS unset')
class LocalFogSphere(unittest.TestCase):
    def test_emitter_mode_bindings(self):
        path = next(Path(CORPUS).rglob('NS_LocalFogSphere.json'))
        with tempfile.TemporaryDirectory() as tmp:
            m = nc.bake_cache(path, tmp, nc.BakeInputs(ticks=5, di_mode='real', svml_shim_path=SHIM, vector_fields=VECTOR_FIELDS))
            r = m['renderers'][0]
            self.assertEqual(r['source_mode'], 'Emitter')
            self.assertEqual(r['binding_sources']['col'], 'param:Empty.Color')
            self.assertEqual(r['binding_sources']['size'], 'param:Empty.SpriteSize')
            self.assertEqual(r['binding_sources']['pos'], 'param_missing')
            self.assertEqual(r['flag_disagreements'], [])
            cols = ref.read_ply(Path(tmp) / 'e0_r0' / 'f_0006.ply')
            self.assertEqual(cols['nx_valid'].tolist(), [1.0])
            self.assertEqual((float(cols['size_0'][0]), float(cols['size_1'][0])), (4.0, 4.0))
            self.assertEqual(json.loads((Path(tmp) / 'cache_manifest.json').read_text(encoding='utf-8'))['frame_end'], 6)


if __name__ == '__main__':
    unittest.main()
