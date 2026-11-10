"""Spec N-O4: GPU emitter emulation helpers and (when the corpus is configured) M2 through its own compute shader on WARP."""
import os
import unittest
from pathlib import Path

import numpy as np

import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # repo root
from Niagara import niagara as g

CORPUS = os.environ.get('DR_NIAGARA_CORPUS')
SHIM = os.environ.get('DR_SVML_SHIM')
BINDINGS = os.environ.get('DR_NIAGARA_GPU_BINDINGS')
SHADERS = os.environ.get('DR_NIAGARA_GPU_SHADERS')


class SpawnInfo(unittest.TestCase):
    def test_running_sums_and_unused_entries(self):
        total, offs, params = g.gpu_spawn_info([(3, 0.1, 0.5, 0), (0, 0, 0, 0), (5, 0.2, 0.25, 1)], True)
        self.assertEqual(total, 8)
        self.assertEqual(offs[:3], [3, 8, g.INT32_MAX])
        self.assertEqual(params[0], (0.5, 0.1, 0, 0))
        self.assertEqual(params[1], (0.25, 0.2, 1, 3))
        self.assertEqual(params[2], (0.0, 0.0, 0, 8))

    def test_inactive_and_cap(self):
        self.assertEqual(g.gpu_spawn_info([(4, 0, 0, 0)], False)[0], 0)
        self.assertEqual(g.gpu_spawn_info([(4, 0, 0, 0), (4, 0, 0, 0)], True, cap=6)[0], 6)


class Ids(unittest.TestCase):
    def test_chunked_allocation_new_ids_first(self):
        ids = g.GpuIdState()
        ids.allocate(10)
        self.assertEqual((ids.allocated, ids.free_list[:3].tolist()), (1024, [0, 1, 2]))
        table = np.full(1024, -1, np.int32)
        table[[0, 1, 5]] = [0, 1, 2]
        ids.compute_free(table)
        self.assertEqual(ids.free_list[:4].tolist(), [2, 3, 4, 6])
        ids.allocate(1500)
        self.assertEqual((ids.allocated, ids.free_list[:2].tolist(), ids.free_list[1024:1026].tolist()), (2048, [1024, 1025], [2, 3]))


@unittest.skipUnless(CORPUS and SHIM and BINDINGS and SHADERS, 'GPU corpus inputs unset')
class RoofSmoke(unittest.TestCase):
    def test_m2_runs_from_its_own_compute_shader(self):
        from Niagara.niagara import load_system
        from Niagara.niagara import BakeInputs, Scheduler
        path = next(Path(CORPUS).rglob('NS_FrontEnd_RoofSmoke_01.json'))
        s = Scheduler(load_system(path), BakeInputs(ticks=30, di_mode='real', svml_shim_path=SHIM, gpu_bindings=BINDINGS, gpu_shaders=SHADERS))
        s.run()
        r = next(r for r in s.emitters if getattr(r.model, 'gpu', None) is not None)
        self.assertGreater(r.count, 0)
        age = r.model.particle_layout.find('Age')
        self.assertTrue(np.all(r.buffer['float', age.float_start].view(np.float32) > 0))


if __name__ == '__main__':
    unittest.main()
