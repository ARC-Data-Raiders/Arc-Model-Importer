"""frigate_entrance: orbital stage path, orchestrator segments and socket owner tracks (no bpy)."""
import json
import math
import os
import tempfile
import unittest

import numpy as np

import frigate_entrance as fe
import niagara_render_ref as ref
from niagara_cache import rotator_quat
from niagara_scheduler import quat_axes

RAW = os.environ.get('DR_FRIGATE_RAW')


def key(t, v, mode='RCIM_Cubic', arrive=0.0, leave=0.0):
    return {'InterpMode': mode, 'TangentMode': 'RCTM_User', 'TangentWeightMode': 'RCTWM_WeightedNone', 'Time': t, 'Value': v,
            'ArriveTangent': arrive, 'LeaveTangent': leave}


CURVE = [{'Type': 'CurveTranslateRotate', 'Name': 'C', 'Properties': {
    'FloatCurves': {'Keys': [key(10.0, 0.0), key(0.0, -1000.0, 'RCIM_Linear')]},
    'FloatCurves[4]': {'Keys': [key(0.0, 0.0), key(10.0, 90.0)]}}}]
ORCH = [{'Type': 'X_C', 'Name': 'Default__X_C', 'Properties': {'SpawnStages': [{
    'Segments': [
        {'SegmentStartEffect': {'ObjectPath': '/Game/FX/DA_Retro.0'}, 'StartTime': 9.2, 'EndTime': 60.0, 'FXAttachToComponentTag': 'Mesh',
         'FXAttachBoneOrSocketName': 'VFX_RetroThruster_FL', 'bDetachFXOnSegmentEnd': True},
        {'SegmentStartEffect': {'ObjectPath': '/Game/FX/DA_Audio.0'}, 'StartTime': 0.0, 'EndTime': 50.0, 'FXAttachToComponentTag': 'None',
         'FXAttachBoneOrSocketName': 'VFX_Ground', 'bDetachFXOnSegmentEnd': False}],
    'MotionData': {'Curve': {'ObjectPath': '/Game/Spawning/C.0'}, 'ShakeMaximumMagnitude': {'X': 3.0, 'Y': 3.0, 'Z': 1.0},
                   'NormalizedActorScale': {'EditorCurveData': {'Keys': [key(0.0, 0.0, 'RCIM_Linear'), key(0.1, 1.0, 'RCIM_Linear'), key(1.0, 1.0)]}}}}]}},
        {'Type': 'CloudPassthroughComponent', 'Name': 'Cloud_GEN_VARIABLE', 'Properties': {'CloudLayerAltitudeOffset': 150000.0}}]
DA = [{'Type': 'EffectDataAsset', 'Properties': {'NiagaraSettings': {'DefaultNiagaraSystem': {'NiagaraSystem': {
    'AssetPathName': '/Game/FX/NS_Retro.NS_Retro'}}}}}]


class Entrance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for name, doc in [('C', CURVE), ('Orch', ORCH), ('DA_Retro', DA), ('DA_Audio', [{'Type': 'EffectDataAsset', 'Properties': {}}])]:
            with open(os.path.join(self.tmp.name, name + '.json'), 'w', encoding='utf-8') as fh:
                json.dump(doc, fh)
        self.channels = fe.curve_channels(os.path.join(self.tmp.name, 'C.json'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_channels(self):
        self.assertEqual([len(k) for k in self.channels], [2, 0, 0, 0, 2, 0])  # bare 'FloatCurves' is channel 0, keys sorted
        self.assertEqual(self.channels[0][0]['Time'], 0.0)
        self.assertEqual(fe.curve_duration(self.channels), 10.0)

    def test_stage_pose(self):
        loc, q = fe.stage_pose(self.channels, 5.0)
        self.assertAlmostEqual(loc[0], -500.0)  # linear segment
        np.testing.assert_allclose(q, rotator_quat({'Pitch': 45.0}), atol=1e-12)  # channel 4 = pitch, flat tangents
        loc, q = fe.stage_pose(self.channels, 40.0)  # constant post-extrapolation
        self.assertEqual(loc, (0.0, 0.0, 0.0))

    def test_quaternion_helpers(self):
        rng = np.random.default_rng(3)
        for _ in range(5):
            a, b = rng.normal(size=4), rng.normal(size=4)
            a, b, v = a / np.linalg.norm(a), b / np.linalg.norm(b), rng.normal(size=3)
            np.testing.assert_allclose(fe.quat_mul(a, b), ref.quat_mul(a, b), atol=1e-12)
            np.testing.assert_allclose(fe.quat_rotate(a, v), v @ np.array(quat_axes(a)), atol=1e-12)  # row-vector FQuat matrix

    def test_socket_pose(self):
        sock = {'location': (100.0, 0.0, 50.0), 'rotation': (0.0, 30.0, 0.0)}
        loc, q = fe.socket_pose((1000.0, 0.0, 0.0), rotator_quat({'Yaw': 90.0}), 0.5, sock)
        np.testing.assert_allclose(loc, (1000.0, 50.0, 25.0), atol=1e-9)  # yaw +90 turns +X into +Y; actor scale scales the offset
        np.testing.assert_allclose(quat_axes(q)[0], (math.cos(math.radians(120)), math.sin(math.radians(120)), 0.0), atol=1e-12)

    def test_owner_track(self):
        sock = {'location': (0.0, 0.0, 0.0), 'rotation': (0.0, 0.0, 0.0)}
        motion = {}
        start, vel, trans, rots, deact = fe.owner_track(self.channels, motion, sock, 2.0, 3.0, 10.0, end=2.5, detach=True)
        self.assertEqual((len(trans), len(rots), deact), (10, 10, 5))
        self.assertAlmostEqual(start[0][0], -800.0)
        self.assertAlmostEqual(trans[0][0], -790.0)  # tick 1 ends at t0 + 1/fps
        self.assertAlmostEqual(vel[0], 100.0)
        self.assertEqual(trans[4], trans[9])  # detached: frozen at the segment end
        _, _, trans, _, deact = fe.owner_track(self.channels, motion, sock, 2.0, 3.0, 10.0)
        self.assertIsNone(deact)
        self.assertAlmostEqual(trans[-1][0], -700.0)

    def test_actor_scale(self):
        orch = fe.load_orchestrator(os.path.join(self.tmp.name, 'Orch.json'), self.tmp.name)
        self.assertAlmostEqual(fe.actor_scale(orch['motion'], 1.5, 30.0), 0.5)
        self.assertAlmostEqual(fe.actor_scale(orch['motion'], 20.0, 30.0), 1.0)

    def test_bake_offset(self):
        self.assertEqual(fe.bake_offset([(1.0, 2.0, 3.0), (-1e5, 0.0, 0.0)]), (0.0, 0.0, 0.0))
        pts = [(-1125232.5, 15234.7, 486259.6), (0.0, 0.0, 0.0)]
        off = fe.bake_offset(pts)
        self.assertTrue(all(abs(c - o) < fe.LWC_SAFE for p in pts for c, o in zip(p, off)))
        self.assertEqual(off[0] % 100.0, 0.0)
        with self.assertRaises(ValueError):
            fe.bake_offset([(-3e6, 0.0, 0.0), (0.0, 0.0, 0.0)])

    def test_orchestrator(self):
        orch = fe.load_orchestrator(os.path.join(self.tmp.name, 'Orch.json'), self.tmp.name)
        self.assertEqual(orch['curve'], '/Game/Spawning/C')
        s = orch['segments']
        self.assertEqual((s[0]['effect'], s[0]['system'], s[0]['socket'], s[0]['start'], s[0]['detach']),
                         ('DA_Retro', '/Game/FX/NS_Retro.NS_Retro', 'VFX_RetroThruster_FL', 9.2, True))
        self.assertEqual(s[1]['system'], '')  # audio / camera-shake only
        self.assertEqual(orch['cloud']['CloudLayerAltitudeOffset'], 150000.0)
        self.assertEqual(fe.frame_of(9.2, 60.0), 553)

    def test_weighted_tangents(self):
        a = key(0.0, 1.0, leave=2.0)
        b = key(3.0, 4.0, arrive=-0.5)
        plain = [fe.eval_curve([a, b], t) for t in (0.4, 1.5, 2.9)]
        # weights equal to the implied handle length reproduce the non-weighted Bezier (time curve becomes linear)
        aw = dict(a, TangentWeightMode='RCTWM_WeightedBoth', LeaveTangentWeight=math.hypot(3.0, 2.0 * 3.0) / 3.0)
        bw = dict(b, TangentWeightMode='RCTWM_WeightedBoth', ArriveTangentWeight=math.hypot(3.0, -0.5 * 3.0) / 3.0)
        np.testing.assert_allclose([fe.eval_curve([aw, bw], t) for t in (0.4, 1.5, 2.9)], plain, atol=1e-9)
        heavy = dict(aw, LeaveTangentWeight=3.0)  # a longer leave handle changes the shape but not the end points
        self.assertNotAlmostEqual(fe.eval_curve([heavy, bw], 1.5), plain[1], places=3)
        self.assertAlmostEqual(fe.eval_curve([heavy, bw], 3.0), 4.0)
        self.assertEqual(fe.eval_curve([key(0.0, 2.0, 'RCIM_Constant'), key(1.0, 5.0)], 0.5), 2.0)

    def test_dispersion(self):
        ch = [[key(0.0, -1000.0, 'RCIM_Linear'), key(10.0, 0.0)], [], [key(0.0, 1000.0, 'RCIM_Linear'), key(10.0, 0.0)], [], [], []]
        self.assertAlmostEqual(fe.penetration_time(ch, 400.0), 6.0, places=6)
        cloud = {'CloudLayerAltitudeOffset': 400.0, 'SecondsBeforeBottomCloudLayerPenetrationToTriggerAt': 1.0}
        disp = {'max_size': 100.0, 'relative_origin': (-0.75, 0.0, -0.5)}
        curve = [key(0.0, 0.0, 'RCIM_Linear'), key(1.0, 1.0, 'RCIM_Linear')]
        tr = fe.dispersion_track(ch, cloud, disp, curve, [4.0, 5.5, 7.0])
        self.assertAlmostEqual(tr['trigger'], 5.0, places=6)
        np.testing.assert_allclose(tr['origin'], (-400.0, 0.0, 400.0), atol=1e-6)
        self.assertEqual(tr['samples'][0][2], 0.0)
        t, centre, size = tr['samples'][2]
        self.assertAlmostEqual(size, 100.0)
        np.testing.assert_allclose(centre, (-400.0 + 75.0, 0.0, 400.0 + 50.0), atol=1e-6)  # grows forward and up from P
        self.assertIsNone(fe.penetration_time(ch, -10.0))

    @unittest.skipUnless(RAW, 'DR_FRIGATE_RAW unset')
    def test_cooked_dispersion(self):
        keys = fe.float_curve(os.path.join(RAW, 'Clouds_DisperseCurve_Frigate.json'))
        for k in keys:
            self.assertAlmostEqual(fe.eval_curve(keys, float(k['Time'])), float(k['Value']), places=5)
        vals = [fe.eval_curve(keys, t) for t in np.linspace(0.0, 0.8333333, 30)]
        self.assertTrue(all(b >= a - 1e-9 for a, b in zip(vals, vals[1:])))  # opening is monotonic
        d = fe.load_dispersion(os.path.join(RAW, 'BP_FrigateCloudDispersion.json'))
        self.assertEqual((d['max_size'], d['relative_origin']), (85000.0, (-0.75, 0.0, -0.5)))

    @unittest.skipUnless(RAW, 'DR_FRIGATE_RAW unset')
    def test_cooked_stage0(self):
        ch = fe.curve_channels(os.path.join(RAW, 'Curve_Frigate_Orbital_Stage0.json'))
        self.assertEqual(fe.curve_duration(ch), 30.0)
        loc, q = fe.stage_pose(ch, 30.0)
        np.testing.assert_allclose(loc, (0.0, 0.0, 0.0), atol=1e-3)
        np.testing.assert_allclose(q, (0.0, 0.0, 0.0, 1.0), atol=1e-6)
        loc, _ = fe.stage_pose(ch, 0.0)
        np.testing.assert_allclose(loc, (-1125232.5, 15234.731, 486259.62), rtol=1e-6)


if __name__ == '__main__':
    unittest.main()
