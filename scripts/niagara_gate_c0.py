"""Gate C0 (spec N-O5 section 11): capture leaves bakes byte-identical, cache writes are deterministic, PLY round-trips."""
import argparse,collections,json,multiprocessing as mp,pathlib,shutil,sys,time,traceback
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent))
import Niagara.niagara as nc
from Niagara.niagara import Scheduler, BakeInputs, BakeError, save_bake
from Niagara.niagara import load_system

REQUIRED=('schema','system','package','bake_hashes','inputs','frame_start','frame_end','fps','fps_base','bake_origin_ue','bake_origin_b','emitters','renderers','streams','stats')
def write(p,v):
    p=pathlib.Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,sort_keys=True,indent=2)+'\n',encoding='utf-8')
def run_system(job):
    path,out,ticks,shim,vf=job;out=pathlib.Path(out);start=time.perf_counter();checks={};notes=[]
    try:
        mk=lambda:BakeInputs(ticks=ticks,dt=1/60,di_mode='real',svml_shim_path=shim,vector_fields=vf)
        s=Scheduler(load_system(path),mk());s.run();plain=save_bake(s,out/'plain')
        m1=nc.bake_cache(path,out/'cache_a',mk(),1,60.0,1.0,out/'bake_a');m2=nc.bake_cache(path,out/'cache_b',mk(),1,60.0,1.0,out/'bake_b')
        checks['bake_identical']=plain==m1['bake_hashes']==m2['bake_hashes']
        ha,hb=nc.tree_hashes(out/'cache_a'),nc.tree_hashes(out/'cache_b');checks['cache_deterministic']=ha==hb and len(ha)>0
        checks['manifest_keys']=all(k in m1 for k in REQUIRED) and m1['schema']==nc.SCHEMA
        frames=m1['frame_end']-m1['frame_start']+1;ok=True;counts={}
        for st in m1['streams']:
            files=sorted((out/'cache_a'/st['name']).glob('f_*.ply'));ok&=len(files)==frames;counts[st['name']]=0
            for f in files:
                cols=nc.read_ply(f);props=list(cols)
                if props!=st['ply_properties']:ok=False;notes.append('props:'+f.name);break
                rows=nc.np.stack([cols[p] for p in props],axis=1) if props else None
                ok&=nc.ply_bytes(props,rows)==f.read_bytes();ok&=bool(nc.np.all(nc.np.isfinite(rows)))
                counts[st['name']]=max(counts[st['name']],int(cols['nx_valid'].sum()))
        checks['ply_roundtrip']=ok;checks['counts_match_stats']=counts==m1['stats']['max_count']
        kinds=collections.Counter(r['kind'] for r in m1['renderers']);flags={'%s/%s'%(nc.stream_name(r),b):1 for r in m1['renderers'] for b in r.get('flag_disagreements',[])}
        result={'class':'pass' if all(checks.values()) else 'fail','checks':checks,'notes':notes[:10],'renderer_kinds':dict(kinds),'flag_disagreements':sorted(flags),'streams':len(m1['streams']),'max_count':m1['stats']['max_count'],
                'cache_files':len(ha),'wall_seconds':time.perf_counter()-start}
    except (BakeError,nc.CacheError) as exc:result={'class':'error','checks':checks,'reason':str(exc),'wall_seconds':time.perf_counter()-start}
    except Exception as exc:result={'class':'error','checks':checks,'reason':repr(exc),'traceback':traceback.format_exc()[-3000:],'wall_seconds':time.perf_counter()-start}
    for d in ('plain','bake_a','bake_b','cache_b'):shutil.rmtree(out/d,ignore_errors=True)
    write(out/'result.json',result);return pathlib.Path(path).stem,result
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--corpus',required=True);ap.add_argument('--systems',required=True);ap.add_argument('--out',required=True);ap.add_argument('--summary',required=True)
    ap.add_argument('--shim',required=True);ap.add_argument('--vector-fields',required=True);ap.add_argument('--ticks',type=int,default=120);ap.add_argument('--jobs',type=int,default=4);a=ap.parse_args()
    names=json.loads(pathlib.Path(a.systems).read_text(encoding='utf-8-sig'));paths={p.stem:p for p in pathlib.Path(a.corpus).rglob('NS_*.json')}
    jobs=[(str(paths[n]),str(pathlib.Path(a.out)/n),a.ticks,a.shim,a.vector_fields) for n in names]
    with mp.Pool(a.jobs) as pool:results=dict(pool.imap_unordered(run_system,jobs))
    classes=collections.Counter(r['class'] for r in results.values());kinds=collections.Counter()
    for r in results.values():kinds.update(r.get('renderer_kinds',{}))
    summary={'systems':len(names),'completed':len(results),'classes':dict(classes),'renderer_kinds':dict(kinds),'streams':sum(r.get('streams',0) for r in results.values()),'flag_disagreements':sum(len(r.get('flag_disagreements',[])) for r in results.values()),
             'failing_checks':dict(collections.Counter(k for r in results.values() for k,v in r.get('checks',{}).items() if not v)),'results':dict(sorted(results.items()))}
    write(a.summary,summary);print(json.dumps({k:summary[k] for k in ('systems','completed','classes','renderer_kinds','streams','flag_disagreements','failing_checks')},sort_keys=True))
if __name__=='__main__':main()
