import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "Shared/materials/native/shader_parity/xform.py"
spec = importlib.util.spec_from_file_location("parity_xform_test", path)
x = importlib.util.module_from_spec(spec)
spec.loader.exec_module(x)
IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


class ShaderParityRuntimeTests(unittest.TestCase):
    def test_R1_binding(self):
        rows = [
            ("dr_cam_y", ("VIEW_LAYER", "dr_cam", "y", False)),
            ("dr_camneg_x", ("VIEW_LAYER", "dr_cam", "x", True)),
            ("dr_camfwd_z", ("VIEW_LAYER", "dr_camfwd", "z", False)),
            ("dr_mpc_MC_WeatherStates_Wetness", ("VIEW_LAYER", "dr_mpc_MC_WeatherStates_Wetness", "x", False)),
            ("dr_l2w_1_w", ("OBJECT", "dr_l2w_1", "w", False)),
            ("dr_w2l_2_x", ("OBJECT", "dr_w2l_2", "x", False)),
            ("dr_cpd_10", ("OBJECT", "dr_cpd_2", "z", False)),
            ("dr_cpd_3", ("OBJECT", "dr_cpd_0", "w", False))]
        for name, expected in rows:
            self.assertEqual(x.binding(name), expected)

    def test_R2_uv(self):
        for names, k, result in [(None, 0, ""), (None, 1, "UV1"), ([], 1, ""), (["UV0"], 1, ""), (["UV0", "UV1"], 1, "UV1"), (["UVMap", "EXTRAUV0"], 1, "EXTRAUV0")]:
            self.assertEqual(x.uv_name(names, k), result)

    def test_R3_identity(self):
        l2w, w2l = x.ue_affine(IDENTITY)
        self.assertEqual(l2w, IDENTITY[:3])
        self.assertEqual(w2l, IDENTITY[:3])

    def test_R4_affine(self):
        l2w, w2l = x.ue_affine([[0, 1, 0, 3], [-1, 0, 0, 2], [0, 0, 1, 0.5], [0, 0, 0, 1]])
        self.assertEqual(l2w, [[0, -1, 0, 300], [1, 0, 0, -200], [0, 0, 1, 50]])
        self.assertEqual(w2l, [[0, 1, 0, 200], [-1, 0, 0, 300], [0, 0, 1, -50]])

    def test_R5_camera(self):
        camera, forward = x.camera_props([[0, 0, 1, 6], [1, 0, 0, 0], [0, 1, 0, 8], [0, 0, 0, 1]])
        self.assertEqual(camera, [600.0, 0.0, 800.0, 0.0])
        self.assertEqual(forward, [-1.0, 0.0, 0.0, 0.0])

    def test_R6_cpd(self):
        self.assertEqual(x.cpd_vector(2, [{"index": 10, "default": 0.0, "name": "Is Indoors"}]), [0, 0, 0, 0])
        self.assertEqual(x.cpd_vector(2, [{"index": 10, "default": 0.5, "name": "Is Indoors"}]), [0, 0, 0.5, 0])
