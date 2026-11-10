"""Scene-input data interfaces (spec N-O3 §12): Camera and async CollisionQuery."""
import struct
import unittest

import numpy as np

import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # repo root
from Niagara import niagara as di

T = 0xffffffff


def fw(value):
    return struct.unpack('<I', struct.pack('<f', value))[0]


def wf(word):
    return struct.unpack('<f', struct.pack('<I', int(word)))[0]


class Camera(unittest.TestCase):
    def test_axes_match_frotationmatrix(self):
        x, y, z = di.rotation_axes(0.0, 90.0, 0.0)  # yaw 90: forward +Y, right -X, up +Z
        np.testing.assert_allclose(x, (0, 1, 0), atol=1e-12)
        np.testing.assert_allclose(y, (-1, 0, 0), atol=1e-12)
        np.testing.assert_allclose(z, (0, 0, 1), atol=1e-12)
        x, y, z = di.rotation_axes(30.0, 0.0, 0.0)  # pitch up 30
        np.testing.assert_allclose(x, (math_cos(30), 0, math_sin(30)), atol=1e-12)

    def test_output_order_and_default(self):
        words = di.camera_properties_words((1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 90.0))
        self.assertEqual([wf(w) for w in words], [1, 2, 3, 1, 0, 0, 0, 0, 1, 0, 1, 0])
        self.assertEqual([wf(w) for w in di.camera_properties_words(None)][:3], [0, 0, 0])


def math_cos(d):
    import math
    return math.cos(math.radians(d))


def math_sin(d):
    import math
    return math.sin(math.radians(d))


class Collision(unittest.TestCase):
    def setUp(self):
        # Floor quad at z = 0 spanning +-100 cm.
        tris = [[[-100, -100, 0], [100, -100, 0], [100, 100, 0]], [[-100, -100, 0], [100, 100, 0], [-100, 100, 0]]]
        self.world = di.CollisionWorld(tris)

    def args(self, ids, start, end, skip=0):
        z = np.zeros(len(ids), np.uint32)
        col = lambda v: np.array([fw(v)] * len(ids), np.uint32)
        return [z, np.array(ids, np.uint32)] + [col(v) for v in start] + [col(v) for v in end] + [z, np.array([T if skip else 0] * len(ids), np.uint32), z]

    def test_trace_hits_floor(self):
        hit, pos, nrm = self.world.trace((10, 20, 50), (10, 20, -50))
        self.assertTrue(hit)
        np.testing.assert_allclose(pos, (10, 20, 0))
        np.testing.assert_allclose(nrm, (0, 0, 1))
        self.assertFalse(self.world.trace((10, 20, 50), (10, 20, 10))[0])

    def test_async_results_arrive_next_tick(self):
        state = di.DIInstanceState(collision=self.world)
        batch = state.collision_batches.setdefault((0,), di.CollisionBatch())
        state.pre_tick()
        out = di.collision_query_async(batch, self.args([0], (0, 0, 50), (0, 0, -50)), 1)
        self.assertEqual((out[0][0], out[1][0]), (1, 0))  # query id 1 issued, nothing to read yet
        state.post_tick()
        state.pre_tick()
        out = di.collision_query_async(batch, self.args([1], (0, 0, 50), (0, 0, 40)), 1)
        self.assertEqual(out[1][0], T)
        self.assertEqual([wf(out[k][0]) for k in (3, 4, 5, 6, 7, 8)], [0, 0, 0, 0, 0, 1])
        state.post_tick()
        state.pre_tick()
        out = di.collision_query_async(batch, self.args([1], (0, 0, 50), (0, 0, 40)), 1)
        self.assertEqual(out[1][0], 0)  # the short trace from the previous tick missed

    def test_empty_world_never_hits(self):
        state = di.DIInstanceState()
        batch = di.CollisionBatch()
        state.collision_batches[(0,)] = batch
        for _ in range(3):
            state.pre_tick()
            out = di.collision_query_async(batch, self.args([1], (0, 0, 50), (0, 0, -50)), 1)
            self.assertEqual(out[1][0], 0)
            state.post_tick()

    def test_skip_and_degenerate_trace(self):
        batch = di.CollisionBatch()
        self.assertEqual(di.collision_query_async(batch, self.args([0], (0, 0, 0), (0, 0, 9)), 1, )[0][0], 1)
        self.assertEqual(di.collision_query_async(batch, self.args([0], (0, 0, 0), (0, 0, 9), skip=1), 1)[0][0], 0)
        self.assertEqual(di.collision_query_async(batch, self.args([0], (0, 0, 0), (0, 0, 0)), 1)[0][0], 0)


if __name__ == '__main__':
    unittest.main()
