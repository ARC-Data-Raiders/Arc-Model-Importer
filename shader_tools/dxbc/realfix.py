"""Static WARP capture and real-input fixture construction."""
import json
from pathlib import Path
import struct
import zlib
import subprocess
import numpy as np
F32 = np.float32

PARITY = Path(r"Z:\Datamining\Arc Raiders\CodexScratch\DataRaiders-BlenderImporter\.local\parity-v2")
ORACLE = PARITY / "wave2/build/dr_oracle/dr_oracle.exe"


def capture_texture(raw_json_path, out_root):
    raw_json_path, out_root = Path(raw_json_path), Path(out_root)
    meta = json.loads(raw_json_path.read_text(encoding="utf-8"))
    out_root.mkdir(parents=True, exist_ok=True)
    shaders = {
        "vs.hlsl": "float4 main(uint vid : SV_VertexID) : SV_Position { float2 p[3]={float2(-1,-1),float2(-1,3),float2(3,-1)}; return float4(p[vid],0.5,1); }\r\n",
        "ps.hlsl": "Texture2D<float4> T : register(t0); float4 main(float4 pos : SV_Position) : SV_Target0 { return T.Load(int3(pos.xy,0)); }\r\n",
    }
    for name, text in shaders.items():
        target = out_root / name
        if not target.exists():
            target.write_text(text, encoding="ascii", newline="")
    srgb = meta["tags"]["SRGB"] if meta["tags"]["SRGB"] is not None else True
    views = {71: 72, 77: 78, 98: 99, 87: 91, 28: 29}
    if srgb and meta["dxgi_format"] not in views:
        raise ValueError("unmapped_srgb:" + str(meta["dxgi_format"]))
    view = views[meta["dxgi_format"]] if srgb else meta["dxgi_format"]
    suffix = meta["object_path"].lstrip("/")
    raw = out_root / (suffix + ".mip0.f32")
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw_dds = raw_json_path.with_name(raw_json_path.name[:-len(".raw.json")] + ".dds")
    job = out_root / (suffix + ".txt")
    rows = [["DRJOB", 1], ["device", "warp"],
            ["vs", "hlsl", out_root / "vs.hlsl", "main", "vs_5_0"],
            ["ps", "hlsl", out_root / "ps.hlsl", "main", "ps_5_0"],
            ["tex", "ps", 0, raw_dds, view], ["draw", 3], ["clear", 0, 0, 0, 0],
            ["rt", 0, meta["size_x"], meta["size_y"], raw]]
    job.write_text("\n".join("\t".join(map(str, row)) for row in rows) + "\n", encoding="utf-8", newline="\n")
    result = subprocess.run([str(ORACLE), str(job)], capture_output=True, text=True, timeout=1800)
    log = PARITY / "wave4a/logs" / ("D1-capture-" + suffix.replace("/", "_") + ".log")
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("WARP exit %d: %s" % (result.returncode, log))
    data = np.fromfile(raw, dtype="<f4").reshape(meta["size_y"], meta["size_x"], 4)
    header = bytearray(148)
    struct.pack_into("<4sIIIIIII", header, 0, b"DDS ", 124, 0x1 | 0x2 | 0x4 | 0x1000, data.shape[0], data.shape[1], 0, 0, 1)
    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<I", header, 80, 4)
    struct.pack_into("<I", header, 84, 0x30315844)
    struct.pack_into("<IIIII", header, 128, 2, 3, 0, 1, 0)
    float_dds = out_root / (suffix + ".rgba32f.dds")
    float_dds.write_bytes(bytes(header) + np.ascontiguousarray(data, dtype="<f4").tobytes())
    return dict(object_path=meta["object_path"], dxgi_format=meta["dxgi_format"], srgb=srgb, view=view,
                width=meta["size_x"], height=meta["size_y"], raw_dds=str(raw_dds), float_dds=str(float_dds))


def write_png(path, rgb):
    a = (np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)
    raw = b''.join(b'\x00' + a[y].tobytes() for y in range(a.shape[0]))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', a.shape[1], a.shape[0], 8, 2, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))

def srgb_encode(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def _write_lines(path, rows):
    Path(path).write_text("\n".join("\t".join(map(str,row)) for row in rows)+"\n",encoding="utf-8",newline="\n")


def build_fixture(root, sha, ub, textures, value_set, name):
    from . import engine_inputs as engine, material_ub
    root=Path(root)
    d=root/name
    if d.exists():
        raise FileExistsError(str(d))
    d.mkdir(parents=True)
    shader=json.loads((PARITY/"wave2/out/W7v2/slice"/(sha+".json")).read_text())
    bindings=json.loads((PARITY/"wave2/out/X1/slice"/(sha+".bindings.json")).read_text())
    sizes=engine.dcl_cbs(shader)
    resources=[]
    arrays={}
    for slot,n in sorted(sizes.items()):
        binding=bindings["cb"][str(slot)]
        if binding=="View":
            array=engine.view_cb(n,value_set["geometry"],value_set["size"],"fixture")
        elif binding=="Material":
            array=material_ub.cb_words(ub,n)
        elif binding.startswith("MaterialCollection"):
            index=int(binding[len("MaterialCollection"):])
            array,_=engine.mpc_cb(n,ub["parameter_collections"][index],value_set["mpc"],PARITY/"supervisor/o3/mpc_export")
        else:
            raise engine.Unsupported("cb:"+binding)
        path=d/("cb%d.bin"%slot)
        path.write_bytes(np.ascontiguousarray(array).tobytes())
        arrays[slot]=path
    prim=engine.primitive(value_set["cpd"])
    (d/"sbuf0.bin").write_bytes(prim.tobytes())
    coef=engine.coefficients(shader,value_set["geometry"],value_set["span"])
    (d/"vs.hlsl").write_text(engine.vs_text(shader,coef),encoding="ascii",newline="\n")
    container=next(l for l in (PARITY/"wave2/out/X2b"/sha/"A/W0/job.txt").read_text().splitlines() if l.startswith("ps\t")).split("\t")[2]
    rows=[["DRJOB",1],["device","warp"],["vs","hlsl",d/"vs.hlsl","main","vs_5_0"],["ps","dxbc",container]]
    for slot,n in sorted(sizes.items()):
        rows.append(["cb","ps",slot,arrays[slot]])
        resources.append(dict(kind="cb",slot=slot,vectors=n,file=str(arrays[slot])))
    for slot in sorted(map(int,bindings["s"])):
        rows.append(["samp","ps",slot,0,1,1,1,0,1,0,0])
        resources.append(dict(kind="sampler",slot=slot))
    for slot,entry in sorted(bindings["t"].items(),key=lambda kv:int(kv[0])):
        if entry["ub"]=="Scene":
            rows.append(["sbuf","ps",slot,16,d/"sbuf0.bin"])
            resources.append(dict(kind="structured",slot=int(slot),stride=16,elements=44,file=str(d/"sbuf0.bin")))
        elif entry["ub"]=="Material":
            tex=next(t for t in ub["textures"] if (t["type_index"],t["param_index"])==(entry["type_index"],entry["param_index"]))
            t=textures[tex["texture"]]
            rows.append(["tex","ps",slot,t["raw_dds"],t["view"]])
            resources.append(dict(kind="texture2d",slot=int(slot),width=t["width"],height=t["height"],layers=1,file=t["float_dds"],format=2,object_path=t["object_path"],warp_view_format=t["view"]))
        else:
            raise engine.Unsupported("srv:"+entry["ub"])
    rows.extend([["draw",3],["clear",-12345,-12345,-12345,-12345]])
    size=value_set["size"]
    outputs=[]
    for k in range(4):
        rows.append(["rt",k,size,size,d/("rt%d.f32"%k)])
        outputs.append(dict(slot=k,file=str(d/("rt%d.f32"%k))))
    _write_lines(d/"job.txt",rows)
    result=subprocess.run([str(ORACLE),str(d/"job.txt")],capture_output=True,text=True,timeout=1800)
    log=PARITY/"wave4a/logs"/("D4-"+d.parent.name+"-"+name+".log")
    log.write_text(result.stdout+result.stderr,encoding="utf-8")
    if result.returncode:raise RuntimeError("WARP exit %d %s"%(result.returncode,log))
    fixture=dict(sha=sha,set="REAL",mode="B",width=size,height=size,seed=None,seed_offset=0,variant=name,
                 engine_inputs=dict(geometry=value_set["geometry"],camera_world_cm=list(engine.GEOMETRY[value_set["geometry"]]["C"]),view_forward=list(engine.GEOMETRY[value_set["geometry"]]["F"]),mpc={k:value_set["mpc"].get(k,0) for k in ("SnowAmount","Wetness")},cpd={str(k):v for k,v in value_set["cpd"].items()},uv_span=value_set["span"],embark_view_167y=1.0,prim_flags=512),
                 vertex=dict(status="supported",coefficients=coef),winding="W0",frontface=1,resources=resources,outputs=outputs,
                 job=str(d/"job.txt"),shader_json=str(PARITY/"wave2/out/W7v2/slice"/(sha+".json")))
    (d/"fixture.json").write_text(json.dumps(fixture,indent=1)+"\n",encoding="utf-8",newline="\n")
    rt=[np.fromfile(d/("rt%d.f32"%k),dtype="<f4").reshape(size,size,4) for k in range(4)]
    previews=d/"previews";previews.mkdir()
    write_png(previews/"basecolor.png",srgb_encode(rt[3][...,:3]))
    write_png(previews/"roughness.png",np.repeat(rt[2][...,2:3],3,-1))
    write_png(previews/"specular.png",np.repeat(rt[2][...,1:2],3,-1))
    write_png(previews/"normal.png",rt[1][...,:3])
    write_png(previews/"ao.png",np.repeat(rt[3][...,3:4],3,-1))
    discarded=int(np.all(np.stack([np.all(r==-12345,axis=-1) for r in rt]),axis=0).sum())
    return dict(shader_set="REAL",sha=sha,mode="B",winding="W0",seed_offset=0,variant=name,fixture=str(d/"fixture.json"),discarded_pixels=discarded)


def capture(items, run):
    import shutil
    from . import fixture
    run=Path(run)
    cap=run/"tmp/capture"
    if cap.exists():raise FileExistsError(str(cap))
    (cap/"out/X2c").mkdir(parents=True)
    (cap/"build/dr_oracle").mkdir(parents=True)
    shutil.copy2(ORACLE,cap/"build/dr_oracle/dr_oracle.exe")
    (cap/"out/X2c/fixtures.json").write_text(json.dumps(items,indent=1)+"\n",encoding="utf-8")
    totals=fixture.capture_inputs(cap)
    if not cap.resolve().is_relative_to((run/"tmp").resolve()):raise RuntimeError("cleanup containment")
    # A single native shell owns verification and deletion.
    command="$target = '"+str(cap).replace("'","''")+"'; $root = '"+str(run/"tmp").replace("'","''")+"'; $resolved = (Resolve-Path -LiteralPath $target).Path; if (-not $resolved.StartsWith((Resolve-Path -LiteralPath $root).Path + '\\')) { throw 'cleanup containment' }; Remove-Item -LiteralPath $resolved -Recurse -Force"
    proc=subprocess.run(["powershell","-NoProfile","-Command",command],capture_output=True,text=True,timeout=1800)
    if proc.returncode:raise RuntimeError(proc.stderr)
    return totals
