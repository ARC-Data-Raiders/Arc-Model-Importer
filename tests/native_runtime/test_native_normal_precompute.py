"""Exact per-action morph normal reuse, including source and base identity."""
try:
    from .native_test_support import PACKAGE, namespace
except ImportError:
    from native_test_support import PACKAGE, namespace
import importlib
import struct
import unittest
from unittest.mock import patch

namespace()
deformation = importlib.import_module(PACKAGE + '.Shared.materials.native.deformation')


def run(normals, corners, targets=(), weights=None, corner_normals=None):
    return deformation.deform_morph_targets(
        ((0., 0., 0.),) * len(normals), normals, tuple(range(len(normals))),
        targets, weights or {}, source_vertex_count=len(normals),
        corner_normals=corner_normals or tuple(normals[index] for index in corners),
        corner_vertex_indices=corners)


def bits(values):
    return b''.join(struct.pack('<3d', *row) for row in values)


class NativeNormalPrecomputeTests(unittest.TestCase):
    def test_repeated_corners_normalize_once(self):
        with patch.object(deformation.math, 'hypot', wraps=deformation.math.hypot) as hypot:
            result = run(((.2, .3, .4),), (0,) * 12)
        self.assertEqual(hypot.call_count, 1)
        self.assertEqual(bits(result.corner_normals), bits(result.normals) * 12)

    def test_equal_bases_with_different_source_deltas_are_distinct(self):
        target = deformation.MorphTarget('pose', (
            deformation.MorphDelta((0., 0., 0.), (.2, 0., 0.), 0),
            deformation.MorphDelta((0., 0., 0.), (0., .4, 0.), 1)))
        result = run(((0., 0., 1.),) * 2, (0, 1, 0, 1), (target,), {'pose': .5})
        self.assertNotEqual(bits(result.normals[:1]), bits(result.normals[1:]))
        self.assertEqual(bits(result.corner_normals), bits(result.normals) * 2)

    def test_hard_edge_corner_base_does_not_reuse_vertex_base(self):
        with patch.object(deformation.math, 'hypot', wraps=deformation.math.hypot) as hypot:
            result = run(((0., 0., 1.),), (0, 0, 0), corner_normals=((0., 1., 0.),) * 3)
        self.assertEqual(hypot.call_count, 2)
        self.assertEqual(result.normals, ((0., 0., 1.),))
        self.assertEqual(result.corner_normals, ((0., 1., 0.),) * 3)

    def test_later_actions_use_new_weights(self):
        target = deformation.MorphTarget('pose', (deformation.MorphDelta((0., 0., 0.), (.5, 0., 0.), 0),))
        before = run(((0., 0., 1.),), (0, 0), (target,), {'pose': .2})
        after = run(((0., 0., 1.),), (0, 0), (target,), {'pose': .8})
        self.assertNotEqual(bits(before.corner_normals), bits(after.corner_normals))

    def test_full_cache_stays_bounded_and_computes_uncached_sources(self):
        normals = ((.1, .2, .3), (.2, .3, .4), (.3, .4, .5))
        with patch.object(deformation, '_MORPH_NORMAL_CACHE_LIMIT', 1), patch.object(deformation.math, 'hypot', wraps=deformation.math.hypot) as hypot:
            result = run(normals, (0, 1, 2, 0, 1, 2))
        self.assertEqual(hypot.call_count, 7)
        self.assertEqual(bits(result.corner_normals), bits(result.normals) * 2)

    def test_invalid_inputs_still_fail_before_reuse(self):
        with self.assertRaisesRegex(ValueError, 'finite'):
            run(((float('nan'), 0., 1.),), (0,))
        with self.assertRaisesRegex(ValueError, 'singular normal'):
            run(((0., 0., 0.),), (0,))
        with self.assertRaisesRegex(ValueError, 'out of bounds'):
            run(((0., 0., 1.),), (1,), corner_normals=((0., 0., 1.),))

    def test_signed_zero_bases_keep_distinct_cache_identity(self):
        with patch.object(deformation.math, 'hypot', wraps=deformation.math.hypot) as hypot:
            result = run(((0., .2, 1.),), (0, 0), corner_normals=((0., .2, 1.), (-0., .2, 1.)))
        self.assertEqual(hypot.call_count, 2)
        self.assertEqual(bits(result.corner_normals[:1]), bits(result.corner_normals[1:]))

    def test_disabled_cache_keeps_original_computation(self):
        with patch.object(deformation, '_MORPH_NORMAL_CACHE_LIMIT', 0), patch.object(deformation.math, 'hypot', wraps=deformation.math.hypot) as hypot:
            result = run(((.2, .3, .4),), (0,) * 12)
        self.assertEqual(hypot.call_count, 13)
        self.assertEqual(bits(result.corner_normals), bits(result.normals) * 12)

    def test_vertex_only_calls_do_not_build_corner_cache_keys(self):
        from types import SimpleNamespace
        pack = unittest.mock.Mock(wraps=struct.Struct('<3d').pack)
        with patch.object(deformation.struct, 'Struct', return_value=SimpleNamespace(pack=pack)):
            result = deformation.deform_morph_targets(((0., 0., 0.),), ((.2, .3, .4),), (0,), (), {}, source_vertex_count=1)
        pack.assert_not_called()
        self.assertEqual(result.corner_normals, ())


if __name__ == '__main__':
    unittest.main(verbosity=2)
