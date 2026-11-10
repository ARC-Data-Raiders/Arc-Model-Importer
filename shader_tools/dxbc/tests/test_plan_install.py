import json
from pathlib import Path
import unittest
from .. import plan_install as pi

P = Path(r"Z:\Datamining\Arc Raiders\CodexScratch\DataRaiders-BlenderImporter\.local\parity-v2")
RUN = P / "wave4b"
ROOT = RUN / "out/G1/parity_root"
EXPECTED = [{"default": 0.0, "index": 10, "name": "Is Indoors"}]


class PlanInstallTests(unittest.TestCase):
    def test_T1_canonical(self):
        self.assertEqual(pi.canonical_path("/Game/Pioneer/Items/Outpost/Rooms/Module_01_B/MI_Outpost_LinoleumFloor_01_A"),
                         "PioneerGame/Content/Pioneer/Items/Outpost/Rooms/Module_01_B/MI_Outpost_LinoleumFloor_01_A")

    def test_T2_bindings(self):
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
            self.assertEqual(pi.binding(name), expected)

    def test_T3_cpd(self):
        data = json.loads((P / "supervisor/w4/expected_material_ub.json").read_text())
        for short in ("MI_POI16_Rsr_Walls_01_Grey_01", "MI_FZT_Observatory_FloorIndustrial_01_A"):
            row = next(v for k, v in data.items() if k.rsplit("/", 1)[-1] == short)
            self.assertEqual(pi.cpd_slots(P / "wave1/out/W2/full" / row["shadermaps"]), EXPECTED)

    def test_T4_determinism(self):
        other = RUN / "tmp/root2"
        if other.exists():
            import shutil
            assert other.resolve().is_relative_to((RUN / "tmp").resolve())
            shutil.rmtree(other)
        pi.install(P / "wave4a", P / "wave1", P / "supervisor", other)
        relative = sorted(str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file())
        self.assertEqual(relative, sorted(str(p.relative_to(other)) for p in other.rglob("*") if p.is_file()))
        for rel in relative:
            self.assertEqual((ROOT / rel).read_bytes(), (other / rel).read_bytes(), rel)
