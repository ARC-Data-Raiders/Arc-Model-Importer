import json
from pathlib import Path
import unittest
import numpy as np
from .. import engine_inputs as e, material_ub, uniform, ssa, interp

P=Path(r"Z:\Datamining\Arc Raiders\CodexScratch\DataRaiders-BlenderImporter\.local\parity-v2")
C="00E3924164BE7A1E99BA08DDAA60A9AE3354B7851A172894B9A6D933473E83FA"
F="8155C98B72FD5D0E8E37E87B567BB4155B9ABC6AF3B66ED917CC183B004CF8FC"
ROOT=P/"wave3b/out/B1"
MPCROOT=P/"supervisor/o3/mpc_export"


def inputs(name):
    f=json.loads((ROOT/name/"fixture.json").read_text())
    s=json.loads(Path(f["shader_json"]).read_text())
    d=json.loads((P/"wave2/out/X4"/(C+".dag.json")).read_text())
    b=json.loads((P/"wave2/out/X1/slice"/(C+".bindings.json")).read_text())
    return f,s,d,b


def ub():
    expected=json.loads((P/"supervisor/w4/expected_material_ub.json").read_text())
    k=next(k for k in expected if k.endswith("/MI_POI16_Rsr_Walls_01_Grey_01"))
    return material_ub.evaluate(P/"wave1/out/W2/full"/expected[k]["shadermaps"])


def meta():
    return {"MaterialCollection0":e.mpc_cb(2,ub()["parameter_collections"][0],{},MPCROOT)[1]}


class EngineInputsTests(unittest.TestCase):
    def test_E1(self):
        material=ub()
        for name,snow,wet,indoor,size,span in [
          ("dry_tile1_256",0,0,0,256,1),("wet_tile1_256",0,1,0,256,1),
          ("snow_tile1_256",1,0,0,256,1),("indoors_wet_snow_tile1_256",1,1,1,256,1),
          ("dry_tile5_64",0,0,0,64,5)]:
            f,s,_,_=inputs(name)
            arrays={"cb0.bin":e.view_cb(320,"wall",size,"fixture"),"cb1.bin":e.mpc_cb(2,material["parameter_collections"][0],{"SnowAmount":snow,"Wetness":wet},MPCROOT)[0],"cb2.bin":material_ub.cb_words(material,31),"sbuf0.bin":e.primitive({10:indoor})}
            for file,a in arrays.items():self.assertEqual(a.tobytes(),(ROOT/name/file).read_bytes(),name+" "+file)
            coef=e.coefficients(s,"wall",span)
            self.assertEqual(coef,f["vertex"]["coefficients"])
            self.assertEqual(e.vs_text(s,coef).encode(),(ROOT/name/"vs.hlsl").read_bytes())

    def test_E2(self):
        self.assertEqual(e.view_cb(320,"wall",256,"material").tobytes(),(P/"wave3c/out/C3/virtual/dry/cb0.bin").read_bytes())

    def test_E3(self):
        for name in ["dry_tile1_256","wet_tile1_256","snow_tile1_256","indoors_wet_snow_tile1_256","dry_tile5_64"]:
            f,s,d,_=inputs(name)
            self.assertEqual(e.fold_live(d,s,f,{})[0],uniform.fold_uniform(d,s,f))

    def test_E4(self):
        f,s,d,b=inputs("dry_tile5_64")
        ev=ssa.Evaluator(s,f);ev.evaluate(d)
        params=e.find_params(d,s,b,ev,meta())
        self.assertEqual(len(params),25);self.assertEqual(len(set(params.values())),22)
        optimized,table,stats=e.live_dag(d,s,f,b,meta(),dict(geometry="wall",size=64,span=5,mpc={},cpd={10:0}))
        self.assertEqual(stats["nodes_out"],824)
        ev=ssa.Evaluator(s,f);e.inject(ev,table);actual=ev.evaluate(optimized)
        registers,discarded,_=interp.Interpreter(s,f).execute()
        for name,a in actual.items():
            ref=np.where(discarded,np.uint32(0xFFFFFFFF),np.uint32(0)) if name=="kill" else registers[name.split(".")[0]][...,"xyzw".index(name[-1])]
            mask=np.ones_like(discarded) if name=="kill" else ~discarded
            self.assertTrue(np.all(a[mask]==ref[mask]),name)

    def test_E5(self):
        f,s,d,b=inputs("dry_tile5_64")
        with self.assertRaisesRegex(e.Unsupported,"param_value:dr_mpc_MC_WeatherStates_Wetness"):
            e.live_dag(d,s,f,b,meta(),dict(geometry="wall",size=64,span=5,mpc={"Wetness":1},cpd={10:0}))

    def test_E6(self):
        shader=json.loads((P/"wave2/out/W7v2/slice"/(F+".json")).read_text())
        self.assertEqual(e.vs_struct(shader),"struct Output {centroid float4 a0 : TEXCOORD10; centroid float4 a1 : TEXCOORD11; linear float4 a2 : TEXCOORD0; nointerpolation uint1 a3 : PRIMITIVE_ID0; linear float3 a4 : TEXCOORD9; float4 p : SV_Position;};")


if __name__=="__main__":unittest.main()
