import argparse
import hashlib
import json
import pathlib
import subprocess
import numpy as np


def capture(wave1,oracle,run):
    output=run/'out/Y2'
    shaders=output/'shaders'
    shaders.mkdir(parents=True,exist_ok=True)
    vs=shaders/'vs.hlsl'
    ps=shaders/'ps.hlsl'
    vs.write_text('float4 main(uint vid : SV_VertexID) : SV_Position { float2 p[3]={float2(-1,-1),float2(-1,3),float2(3,-1)}; return float4(p[vid],0.5,1); }\r\n',encoding='utf-8',newline='')
    ps.write_text('Texture2D<float4> T : register(t0); float4 main(float4 pos : SV_Position) : SV_Target0 { return T.Load(int3(pos.xy,0)); }\r\n',encoding='utf-8',newline='')
    manifest=[]
    jobs=[]
    metadata={}
    views={71:72,77:78,98:99,87:91,28:29}
    def job(meta,view,target,path):
        path.parent.mkdir(parents=True,exist_ok=True)
        target.parent.mkdir(parents=True,exist_ok=True)
        rows=[['DRJOB','1'],['device','warp'],['vs','hlsl',str(vs),'main','vs_5_0'],['ps','hlsl',str(ps),'main','ps_5_0'],['tex','ps','0',str(wave1/'out/W3'/meta['dds_file']),str(view)],['draw','3'],['clear','0','0','0','0'],['rt','0',str(meta['size_x']),str(meta['size_y']),str(target)]]
        path.write_text('\n'.join('\t'.join(row) for row in rows)+'\n',encoding='utf-8',newline='\n')
        proc=subprocess.run([str(oracle),str(path)],capture_output=True,text=True,timeout=1800)
        log=run/'logs'/('Y2-'+str(len(jobs)).zfill(2)+'.log')
        log.write_text(proc.stdout+proc.stderr,encoding='utf-8')
        jobs.append(dict(job=str(path.relative_to(run)),exit=proc.returncode,log=str(log.relative_to(run))))
        (output/'jobs.json').write_text(json.dumps(jobs,indent=2)+'\n',encoding='utf-8')
        if proc.returncode:raise RuntimeError('Y2 oracle exit '+str(proc.returncode)+' '+str(log))
        if target.stat().st_size!=meta['size_x']*meta['size_y']*16:raise RuntimeError('Y2 size mismatch '+meta['object_path'])
        a=np.fromfile(target,dtype='<f4').reshape(meta['size_y'],meta['size_x'],4)
        if not np.all(np.isfinite(a)&(a>=0)&(a<=1)):raise RuntimeError('Y2 nonfinite/out of range '+meta['object_path'])
        return a
    raws=[json.loads(path.read_text()) for path in (wave1/'out/W3').rglob('*.raw.json')]
    for meta in sorted(raws,key=lambda m:m['object_path']):
        path=meta['object_path'].lstrip('/')
        tag=meta['tags']['SRGB']
        srgb=tag if tag is not None else True
        fmt=meta['dxgi_format']
        if srgb and fmt not in views:raise RuntimeError('Y2 unmapped SRGB format '+str(fmt))
        view=views[fmt] if srgb else fmt
        target=output/'tex'/(path+'.mip0.f32')
        job(meta,view,target,output/'jobs'/(path+'.txt'))
        manifest.append(dict(object_path=meta['object_path'],dxgi_format=fmt,view_format=view,srgb=srgb,srgb_source='tag' if tag is not None else 'default',width=meta['size_x'],height=meta['size_y'],f32_file=target.relative_to(run).as_posix(),sha256=hashlib.sha256(target.read_bytes()).hexdigest().upper()))
        metadata[meta['object_path']]=meta
    (output/'texcap.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    checks=[]
    for entry in sorted(manifest,key=lambda e:(e['width']*e['height'],e['object_path']))[:3]:
        target=run/'tmp/recheck'/(entry['object_path'].lstrip('/')+'.mip0.f32')
        job(metadata[entry['object_path']],entry['view_format'],target,target.with_suffix('.txt'))
        same=hashlib.sha256(target.read_bytes()).hexdigest().upper()==entry['sha256']
        checks.append(dict(object_path=entry['object_path'],identical=same))
        if not same:raise RuntimeError('Y2 recheck mismatch '+entry['object_path'])
    records={}
    for name in ['/Engine/EngineDebugMaterials/HeatmapGradient','/Engine/EngineMaterials/Good64x64TilingNoiseHighFreq']:
        entry=next(e for e in manifest if e['object_path']==name)
        meta=metadata[name]
        captured=np.fromfile(run/entry['f32_file'],dtype='<f4').reshape(entry['height'],entry['width'],4)
        dds=(wave1/'out/W3'/meta['dds_file']).read_bytes()
        if name.endswith('HeatmapGradient'):
            raw=np.frombuffer(dds,dtype=np.uint8,offset=148,count=entry['width']*entry['height']*4).reshape(entry['height'],entry['width'],4)[...,[2,1,0,3]].astype(np.float64)/255
            ref=raw.copy()
            ref[...,:3]=np.where(raw[...,:3]<=0.04045,raw[...,:3]/12.92,((raw[...,:3]+0.055)/1.055)**2.4)
            records[name]=dict(max_abs_err_per_channel=np.max(np.abs(captured-ref),axis=(0,1)).tolist())
        else:
            raw=np.frombuffer(dds,dtype=np.uint8,offset=148,count=entry['width']*entry['height']).reshape(entry['height'],entry['width']).astype(np.float64)/255
            records[name]=dict(max_abs_err_r=float(np.max(np.abs(captured[...,0]-raw))),g_b_zero_a_one=bool(np.all(captured[...,1:3]==0) and np.all(captured[...,3]==1)))
    acceptance=dict(jobs=len(manifest),recheck=checks,records=records)
    (output/'acceptance.json').write_text(json.dumps(acceptance,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(acceptance,indent=2))


def main():
    parser=argparse.ArgumentParser()
    for name in ['wave1','oracle','run']:parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    capture(pathlib.Path(args.wave1),pathlib.Path(args.oracle),pathlib.Path(args.run))


if __name__=='__main__':main()
