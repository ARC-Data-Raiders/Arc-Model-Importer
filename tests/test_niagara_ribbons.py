"""Spec N-O5b: ribbon generation (port of FNiagaraRendererRibbons CPU generation, UE 5.7)."""
import math
import unittest

import numpy as np

import niagara_cache as nc
import niagara_ribbons as rb


def straight(n, spacing=0.5):
    cols = {p: np.zeros(n) for p in nc.props_of('ribbon')}
    cols['nx_valid'][:] = 1
    cols['x'] = np.arange(n) * spacing
    cols['width'][:] = 0.2
    cols['nage'] = np.linspace(0, 1, n)
    cols['uid'] = np.arange(n, 0, -1).astype(float)
    return cols


def props(**over):
    p = dict(nc.DEFAULTS['ribbon'])
    p.update({'link_order_source': 'UniqueID', 'twist_bound': False, 'ribbon_id_bound': False, 'uvdist_bound': False})
    p.update(over)
    return p


class Ribbons(unittest.TestCase):
    def test_acos_fast_endpoints(self):
        self.assertAlmostEqual(rb.acos_fast(1.0), 0.0)
        self.assertAlmostEqual(rb.acos_fast(-1.0), math.pi, places=6)
        self.assertAlmostEqual(rb.acos_fast(0.0), math.pi / 2, places=6)

    def test_slice_tables(self):
        self.assertEqual(len(rb.slice_vertices({'Shape': 'Plane', 'WidthSegmentationCount': 3})[0]), 4)
        verts, pairs = rb.slice_vertices({'Shape': 'Tube', 'TubeSubdivisions': 4})
        self.assertEqual((len(verts), len(pairs)), (5, 4))
        verts, pairs = rb.slice_vertices({'Shape': 'MultiPlane', 'MultiPlaneCount': 3, 'WidthSegmentationCount': 2})
        self.assertEqual((len(verts), len(pairs)), (9, 6))

    def test_straight_ribbon_uniform_u(self):
        m = rb.generate(straight(5), props(TessellationMode='Disabled', UV0Settings={'DistributionMode': 'ScaledUniformly'}), rb.Smoothing())
        self.assertEqual(m['nsub'], 1)
        self.assertEqual(len(m['center']), 5 * 2)
        np.testing.assert_allclose(m['uv0'][::2, 0], [0, 0.25, 0.5, 0.75, 1.0])
        self.assertEqual(len(m['faces']), 4)

    def test_unique_id_order_is_descending(self):
        cols = straight(4)
        cols['uid'] = np.array([1.0, 3.0, 2.0, 4.0])
        self.assertEqual(rb.link_order(cols, props()).tolist(), [3, 1, 2, 0])

    def test_short_segments_are_merged(self):
        cols = straight(4, spacing=0.004)  # 0.4 cm apart: points 1 and 2 are skipped, 0 joins 3 (1.2 cm > 1 cm minimum)
        m = rb.generate(cols, props(TessellationMode='Disabled'), rb.Smoothing())
        np.testing.assert_allclose(m['center'][::2, 0], [0.0, 0.012])
        self.assertIsNone(rb.generate(straight(2, spacing=0.004), props(TessellationMode='Disabled'), rb.Smoothing()))

    def test_mesh_ply_roundtrip(self):
        m = rb.generate(straight(3), props(TessellationMode='Disabled'), rb.Smoothing())
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'm.ply'
            p.write_bytes(rb.mesh_ply_bytes(m))
            cols, faces = rb.read_mesh_ply(p)
            np.testing.assert_array_equal(faces, m['faces'])
            np.testing.assert_allclose(cols['x'], m['center'][:, 0].astype(np.float32))


if __name__ == '__main__':
    unittest.main()
