import json
from pathlib import Path
import unittest
from .. import material_ub as m

P = Path(r"Z:\Datamining\Arc Raiders\CodexScratch\DataRaiders-BlenderImporter\.local\parity-v2")
EXPECTED = json.loads((P/"supervisor/w4/expected_material_ub.json").read_text())


class MaterialUBTests(unittest.TestCase):
    def test_M1(self):
        k = next(k for k in EXPECTED if k.endswith("/MI_POI16_Rsr_Walls_01_Grey_01"))
        actual=m.evaluate(P/"wave1/out/W2/full"/EXPECTED[k]["shadermaps"])
        ref=json.loads((P/"supervisor/o6/concrete_grey01_material_ub.json").read_text())
        self.assertEqual(actual["preshader_float_bits"],ref["preshader_float_bits"])
        self.assertEqual(actual["origin"],ref["origin"])

    def test_M2(self):
        for k,e in EXPECTED.items():
            with self.subTest(mi=k):
                actual=m.evaluate(P/"wave1/out/W2/full"/e["shadermaps"])
                for field in ("resource_index","resource_hash","preshader_offset_vectors","preshader_vectors","unresolved_floats","parameter_collections"):
                    self.assertEqual(actual[field],e[field],field)
                for field in ("layout_hash","layout_hash_computed"):
                    self.assertEqual(actual["ub"][field],e[field])
                self.assertEqual(m.bits_sha256(actual),e["preshader_bits_sha256"])
                b=json.loads((P/"wave2/out/X1/slice"/(e["shader_sha"]+".bindings.json")).read_text())
                for slot,t in b["t"].items():
                    if t["ub"]!="Material":continue
                    item=next(v for v in actual["textures"] if v["type_index"]==t["type_index"] and v["param_index"]==t["param_index"])
                    for a,z in (("parameter","parameter"),("texture","texture"),("resolved_from","source"),("sampler_source","sampler_source")):
                        self.assertEqual(item[a],e["textures"][slot][z])

    def test_M3(self):
        k=next(k for k in EXPECTED if k.endswith("/MI_Hotel_InteriorWall_RedStrip_01_A"))
        actual=m.evaluate(P/"wave1/out/W2/full"/EXPECTED[k]["shadermaps"])
        e=next(v for v in actual["textures"] if v["parameter"]=="PaintBreakup")
        self.assertEqual(e["texture"],"/Game/Pioneer/MaterialLibrary/Textures/Breakup/T_Paint_Breakup_01_X")
        self.assertEqual(e["resolved_from"],"default")

    def test_M4(self):
        with self.assertRaises(m.Unsupported):
            m.run(m.decode(bytes([8]),[],0,1),lambda i:None)


if __name__=="__main__": unittest.main()
