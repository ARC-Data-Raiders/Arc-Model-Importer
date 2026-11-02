"""Spec N-O4: GBuffer DI, SceneTexturesStruct binding from the record resource table, synthetic flat ground (scene textures and Landscape)."""
import os
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import niagara_gpu as g
import niagara_view_ub as vu

CORPUS = os.environ.get('DR_NIAGARA_CORPUS')
SHIM = os.environ.get('DR_SVML_SHIM')
BINDINGS = os.environ.get('DR_NIAGARA_GPU_BINDINGS')
SHADERS = os.environ.get('DR_NIAGARA_GPU_SHADERS')
VECTOR_FIELDS = os.environ.get('DR_NIAGARA_VECTOR_FIELDS')
END = 0xFFFFFFFF
BASE_COLOR = (0.16, 0.15, 0.11)


def token(ub, res, bind):
    return ub << 24 | res << 8 | bind


def write_record(root, resource_hash, maps, names):
    """Minimal shader record: FShaderResourceTable header, empty DXBC container, trailer with the 'u' (uniform buffer names) chunk."""
    header = struct.pack('<I', 0x10)
    for name in g.RESOURCE_TABLE_MAPS:
        words = maps.get(name, [0] * len(names) + [END])
        header += struct.pack('<I%dI' % len(words), len(words), *words)
    dxbc = b'DXBC' + b'\0' * 16 + struct.pack('<III', 1, 32, 0)
    u = struct.pack('<I', len(names)) + b''.join(struct.pack('<I', len(n) + 1) + n.encode('ascii') + b'\0' for n in names)
    chunk = b'u' + struct.pack('<I', len(u)) + u
    trailer = chunk + struct.pack('<I', len(chunk) + 4)
    path = Path(root) / 'PCD3D_SM5' / resource_hash
    path.mkdir(parents=True)
    (path / 'Shader.ushaderrecord').write_bytes(header + dxbc + trailer)


class FakeDevice:
    def texture2d(self, data, size, fmt):
        return SimpleNamespace(data=np.array(data), size=size, fmt=fmt)

    def sampler(self, filter_, address):
        return ('sampler', filter_, address)


def words_f(prog, base, offsets):
    return [struct.unpack('<f', struct.pack('<I', prog.di_values[base + o]))[0] for o in offsets]


class ResourceTable(unittest.TestCase):
    NAMES = ['$Globals', 'View', 'FNiagaraExternalParameters', 'PREV_FNiagaraExternalParameters', 'SceneTexturesStruct']

    def test_grounddust_layout_parses_to_gbuffer_b_c_and_point_sampler(self):
        # the cooked NS_Frigate_Intro_GroundDust records: texture map UB 4 res 4 -> t0, res 5 -> t1; sampler map res 16 -> s0
        maps = {'texture': [0, 0, 0, 0, 5, token(4, 4, 0), token(4, 5, 1), END], 'sampler': [0, 0, 0, 0, 5, token(4, 16, 0), END]}
        with tempfile.TemporaryDirectory() as root:
            write_record(root, 'AB12', maps, self.NAMES)
            self.assertEqual(g.record_uniform_buffers(root, 'AB12'), self.NAMES)
            table = g.record_resource_table(root, 'AB12')
        self.assertEqual(table, [('sampler', 'SceneTexturesStruct', 16, 0), ('texture', 'SceneTexturesStruct', 4, 0),
                                 ('texture', 'SceneTexturesStruct', 5, 1)])
        self.assertEqual(g.scene_texture_bindings(table), [('PointClampSampler', 'sampler', 0), ('GBufferBTexture', 'srv', 0),
                                                           ('GBufferCTexture', 'srv', 1)])

    def test_tokens_of_other_buffers_and_missing_record(self):
        maps = {'texture': [0, 5, 0, 0, 6, token(1, 3, 2), token(4, 1, 3), END]}   # sorted tokens, one end-of-stream token
        with tempfile.TemporaryDirectory() as root:
            write_record(root, 'CD34', maps, self.NAMES)
            table = g.record_resource_table(root, 'CD34')
            self.assertEqual(g.record_resource_table(root, 'MISSING'), [])
        self.assertEqual(table, [('texture', 'View', 3, 2), ('texture', 'SceneTexturesStruct', 1, 3)])
        self.assertEqual(g.scene_texture_bindings(table), [('SceneDepthTexture', 'srv', 3)])

    def test_unmapped_scene_texture_is_an_error(self):
        for entry in (('texture', 'SceneTexturesStruct', 7, 0), ('sampler', 'SceneTexturesStruct', 3, 0), ('texture', 'SceneTexturesStruct', 16, 1)):
            with self.assertRaisesRegex(g.GpuError, 'gpu_scene_texture_unmapped'):
                g.scene_texture_bindings([entry])


class FlatGround(unittest.TestCase):
    def view(self, cam):
        vb = vu.camera_view(vu.ViewBuffer(), cam)
        get = lambda name: vb.words[vb.layout[name][0] // 4:(vb.layout[name][0] + vb.layout[name][1]) // 4].view(np.float32)
        return get

    def test_looking_down_sees_ground_everywhere(self):
        scene = g.flat_ground_scene((0.0, 0.0, 0.0, -90.0, 0.0, 0.0, 90.0), -200.0, BASE_COLOR, size=(16, 9))
        fmt, depth = scene['SceneDepthTexture']
        self.assertEqual((fmt, depth.shape, depth.dtype), ('float', (9, 16), np.float32))
        np.testing.assert_allclose(depth, vu.NEAR / 200.0, rtol=1e-6)
        self.assertIs(scene['ScenePartialDepthTexture'][1], depth)
        np.testing.assert_array_equal(scene['GBufferATexture'][1][4, 7], np.float16([0.5, 0.5, 1.0, 1.0]))
        b = scene['GBufferBTexture'][1][4, 7]
        self.assertEqual(int(round(float(b[3]) * 255.0)) & 15, g.SHADING_MODEL_DEFAULT_LIT)   # DecodeShadingModelId
        np.testing.assert_array_equal(b[:3], np.float16([0.0, 0.5, 1.0]))
        np.testing.assert_array_equal(scene['GBufferCTexture'][1][0, 0], np.float16(BASE_COLOR + (1.0,)))

    def test_texels_agree_with_the_view_uniform_buffer(self):
        cam = (120.0, -40.0, 0.0, -12.0, 35.0, 4.0, 54.43)
        w, h = 64, 36
        scene = g.flat_ground_scene(cam, -200.0, BASE_COLOR, size=(w, h))
        depth = scene['SceneDepthTexture'][1]
        sky = depth == 0
        self.assertTrue(sky[0].all() and not sky[-1].any())          # horizon inside the frame: sky on top, ground below
        np.testing.assert_array_equal(scene['GBufferCTexture'][1][sky], 0)
        get = self.view(cam)
        to_world = np.linalg.inv(get('TranslatedWorldToClip').reshape(4, 4).astype(np.float64))
        scale_bias = get('ScreenPositionScaleBias')
        inv_z = get('InvDeviceZToWorldZTransform')
        forward = get('ViewForward').astype(np.float64)
        checked = 0
        for j in range(0, h, 3):
            for i in range(0, w, 5):
                if sky[j, i]:
                    continue
                # texel centre UV -> NDC through the shaders' UV = NDC * ScreenPositionScaleBias.xy + .wz, then unproject with the texel depth
                u, v = (i + 0.5) / w, (j + 0.5) / h
                ndc = ((u - scale_bias[3]) / scale_bias[0], (v - scale_bias[2]) / scale_bias[1])
                q = np.array([ndc[0], ndc[1], depth[j, i], 1.0]) @ to_world
                world = q[:3] / q[3] + cam[:3]
                self.assertAlmostEqual(world[2], -200.0, delta=0.05)    # on the ground plane
                # ConvertFromDeviceZ (reversed infinite): 1 / (DeviceZ * Inv[2] - Inv[3]) is the view depth of that point
                view_z = (world - cam[:3]) @ forward
                self.assertAlmostEqual(1.0 / (depth[j, i] * inv_z[2] - inv_z[3]), view_z, delta=1e-4 * view_z)
                checked += 1
        self.assertGreater(checked, 40)

    def test_camera_level_with_the_ground_or_looking_up_sees_no_ground(self):
        level = g.flat_ground_scene((0.0, 0.0, -200.0, 0.0, 0.0, 0.0, 90.0), -200.0, BASE_COLOR, size=(8, 8))
        self.assertFalse(level['SceneDepthTexture'][1].any())
        up = g.flat_ground_scene((0.0, 0.0, 0.0, 60.0, 0.0, 0.0, 54.43), -200.0, BASE_COLOR, size=(8, 8))
        self.assertFalse(up['SceneDepthTexture'][1].any())

    def test_scene_views_bind_texels_or_the_empty_scene_fallbacks(self):
        dev = FakeDevice()
        scene = g.flat_ground_scene(None, -200.0, BASE_COLOR, size=(8, 4))
        views = g.scene_views(dev, scene, ['GBufferCTexture', 'SceneDepthTexture', 'SceneColorTexture', 'PointClampSampler'])
        self.assertEqual((views['GBufferCTexture'].fmt, views['GBufferCTexture'].size), ('half4', (8, 4)))
        self.assertEqual((views['SceneDepthTexture'].fmt, views['SceneDepthTexture'].size), ('float', (8, 4)))
        self.assertEqual((views['SceneColorTexture'].size, views['SceneColorTexture'].data.tolist()), ((1, 1), [[[0, 0, 0, 0]]]))
        self.assertEqual(views['PointClampSampler'], ('sampler', g.FILTER_POINT, g.ADDRESS_CLAMP))
        empty = g.scene_views(dev, None, ['GBufferBTexture', 'ScenePartialDepthTexture'])
        self.assertEqual((empty['GBufferBTexture'].fmt, empty['GBufferBTexture'].data.tolist()), ('half4', [[[0, 0, 0, 0]]]))
        self.assertEqual((empty['ScenePartialDepthTexture'].fmt, empty['ScenePartialDepthTexture'].data.tolist()), ('float', [[0.0]]))


class DataInterfaces(unittest.TestCase):
    def prog(self):
        prog = g.GpuEmitterProgram.__new__(g.GpuEmitterProgram)
        prog.dev, prog.di_values, prog.di_views = FakeDevice(), {}, {}
        return prog

    def test_flat_landscape_uses_the_cached_height_source(self):
        """UV pinned to texel (1, 1)'s centre of the 4x4 (0.375 * 4 - 0.5 = 1, exact sample) and inside the GetWorldNormal border masks."""
        prog, base = self.prog(), 1776
        prog._flat_landscape(base, -2903.5)
        self.assertEqual((prog.di_values[base + 288], prog.di_values[base + 568], prog.di_values[base + 548]), (0, 1, 0))   # height VT off, cached on
        self.assertEqual(words_f(prog, base, (720, 724, 728, 732, 736, 740)), [0.0, 0.0, 0.375, 0.375, 100.0, 100.0])
        self.assertEqual((prog.di_values[base + 744], prog.di_values[base + 748]), (4, 4))
        for m in (592, 656):                                            # WorldToUv / UvToWorld identity
            np.testing.assert_array_equal(np.array(words_f(prog, base, range(m, m + 64, 4))).reshape(4, 4), np.eye(4))
        tex = prog.di_views[base + 552]
        self.assertEqual((tex.fmt, tex.size), ('float', (4, 4)))
        np.testing.assert_array_equal(tex.data, np.float32(-2903.5))

    def test_no_landscape_unchanged_and_gbuffer_dummy(self):
        prog, base = self.prog(), 1728
        prog._no_landscape(base)
        self.assertEqual((prog.di_values[base + 288], prog.di_values[base + 568]), (0, 0))
        prog._gbuffer(base + 784)
        self.assertEqual((prog.di_views[base + 784].fmt, prog.di_views[base + 784].data.tolist()), ('half4', [[[0, 0, 0, 0]]]))
        self.assertEqual(prog.di_views[base + 792], ('sampler', g.FILTER_POINT, g.ADDRESS_CLAMP))
        self.assertEqual(g.DI_STRUCT_SIZE[g.GBUFFER], 16)

    def test_scheduler_scene_is_cached_per_camera_and_off_without_ground(self):
        from niagara_scheduler import Scheduler
        ns = SimpleNamespace(inputs=SimpleNamespace(ground_height=None, ground_base_color=BASE_COLOR), di_state=SimpleNamespace(camera=None))
        self.assertIsNone(Scheduler.gpu_scene(ns))
        ns.inputs.ground_height = -200.0
        first = Scheduler.gpu_scene(ns)
        self.assertIs(Scheduler.gpu_scene(ns), first)
        ns.di_state.camera = (0.0, 0.0, 0.0, -30.0, 0.0, 0.0, 54.43)
        self.assertIsNot(Scheduler.gpu_scene(ns), first)


@unittest.skipUnless(CORPUS and SHIM and BINDINGS and SHADERS and VECTOR_FIELDS, 'GPU corpus inputs unset')
class GroundDust(unittest.TestCase):
    def run_dust(self, ground, camera=None, ticks=60):
        from niagara_sched_model import load_system
        from niagara_scheduler import BakeInputs, Scheduler
        path = next(Path(CORPUS).rglob('NS_Frigate_Intro_GroundDust.json'))
        s = Scheduler(load_system(path), BakeInputs(ticks=ticks, di_mode='real', svml_shim_path=SHIM, vector_fields=VECTOR_FIELDS, gpu_bindings=BINDINGS,
                                                    gpu_shaders=SHADERS, ground_height=ground, camera=camera))
        s.run()
        return {r.model.unique_name: r for r in s.emitters if getattr(r.model, 'gpu', None) is not None}

    def colors(self, r):
        v = r.model.particle_layout.find('Color')
        return np.stack([np.asarray(r.buffer['float', v.float_start + c][:r.count], np.uint32).view(np.float32) for c in range(3)], axis=1)

    def test_terrain_snap_needs_ground(self):
        empty = self.run_dust(None)
        self.assertEqual({k: r.count for k, r in empty.items()}, {'Dust_Initial001': 0, 'Dust_Steady': 0})
        flat = self.run_dust(-200.0)
        self.assertTrue(all(r.count > 0 for r in flat.values()))
        r = flat['Dust_Initial001']
        z = np.asarray(r.buffer['float', r.model.particle_layout.find('Position').float_start + 2][:r.count], np.uint32).view(np.float32)
        self.assertLessEqual(float(z.max()), -190.0 + 1e-3)             # snapped to the ground (+10 cm), never above
        # GBuffer tint 0.5 + 0.75 * BaseColor where the spawn point projects onto ground texels, 0.5 (black G-buffer) elsewhere
        c = self.colors(r)
        on_ground = np.all(np.abs(c - (0.5 + 0.75 * np.array(BASE_COLOR))) < 1e-3, axis=1)
        self.assertGreater(on_ground.mean(), 0.3)                         # about half with the default camera 2 m above the ground
        self.assertTrue(np.all(on_ground | np.all(np.abs(c - 0.5) < 1e-3, axis=1)))

    def test_tint_is_view_dependent(self):
        sky = self.run_dust(-200.0, camera=(0.0, 0.0, 0.0, 60.0, 0.0, 0.0, 54.43))['Dust_Initial001']
        self.assertGreater(sky.count, 0)
        np.testing.assert_allclose(self.colors(sky), 0.5, atol=1e-3)    # camera looking up: no ground texel anywhere


if __name__ == '__main__':
    unittest.main()
