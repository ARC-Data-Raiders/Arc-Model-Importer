"""Bitwise scalar/numpy equivalence on Gate V cases and expanded lane counts."""
import argparse,base64,collections,concurrent.futures,dataclasses,json,math,pathlib,struct,sys,time
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent))
from Niagara.niagara import Dataset, IdState, execute, float_word, word_float, word_int, ExecutionError
from Niagara.niagara import NpDataset, execute_np

def read(p):return json.loads(pathlib.Path(p).read_text(encoding='utf-8-sig'))
def words(b):return np.frombuffer(base64.b64decode(b),dtype='<u4').copy()
def clean(x):
 if dataclasses.is_dataclass(x):return clean(dataclasses.asdict(x))
 if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
 if isinstance(x,(list,tuple,np.ndarray)):return [clean(v) for v in x]
 if isinstance(x,(set,frozenset)):return sorted(x)
 if isinstance(x,np.integer):return int(x)
 return x
def run_case(payload):
 path,mode,override,np_only,shim=payload;c=read(path);n=override or c['num_instances'];ds={};nd={};ids={}
 for d,s in enumerate(c['datasets']):
  inputs={};raw={};caps={}
  for kind,k,convert in [('float','float',word_float),('int','int32',word_int)]:
   for a,b in enumerate(s['inputs'][kind]):
    v=words(b);v=np.resize(v,n) if override else v;raw[k,a]=v;inputs[k,a]=[convert(int(w)) for w in v]
   for a in range(s['outputs'][kind]):caps[k,a]=n
  ds[d]=Dataset(inputs=inputs,output_capacities=caps);nd[d]=NpDataset(raw,caps)
  if s['id'] is not None:
   v=s['id'];ids[d]=IdState([-1]*(2*n+64),list(range(n+8))[::-1],n+8,4*n+64,-1,0,[],7) if override else IdState(v['id_table'],v['free_ids'],v['num_free_ids'],v['free_capacity'],v['max_used_id'],0,[],v['id_acquire_tag'])
 ext=[{'InputParamLocations':[0]*f['num_inputs'],'NumOutputs':f['num_outputs']} for f in c['ext_functions']]
 kw=dict(num_instances=n,constant_tables=[base64.b64decode(t) for t in c['constant_tables_b64']],external_functions=ext,profile='pioneer-2.1.0',math_backend='reference' if mode=='A' else 'svml_shim',svml_shim_path=shim if mode=='B' else None,fma_mode='unfused',external_handler='gate_v_stub',id_states=ids,rng_seed=(1,3));code=base64.b64decode(c['legacy_bytecode_b64']);values={};times={}
 for backend in (['numpy'] if np_only else ['scalar','numpy']):
  start=time.perf_counter()
  try:
   r=execute(code,datasets=ds,x86_semantics=True,max_lane_operations=10**9,max_storage_words=10**9,**kw) if backend=='scalar' else execute_np(code,datasets=nd,**kw)
   times[backend]=time.perf_counter()-start
   outputs={};written={}
   for key,arr in r.outputs.items():
    if backend=='scalar':outputs[key]=[0xdeadbeef if v is None else float_word(v) if key[1][0]=='float' else v&0xffffffff for v in arr];written[key]=[v is not None for v in arr]
    else:outputs[key]=arr.tolist();written[key]=r.written[key].tolist()
   values[backend]=clean(dict(ok=True,outputs=outputs,written=written,registers=r.registers,access_indices=r.access_indices,id_states=r.id_states,ext_calls=r.ext_calls,taints=r.taints,output_taints=r.output_taints,dataset_taints=r.dataset_taints,instruction_count=r.instruction_count))
  except ExecutionError as e:values[backend]={'ok':False,'error':str(e)}
  times.setdefault(backend,time.perf_counter()-start)
 diffs=[]
 def compare(a,b,loc):
  if len(diffs)>=50:return
  if type(a)!=type(b):diffs.append(dict(location=loc,scalar=a,numpy=b));return
  if isinstance(a,dict):
   for k in sorted(set(a)|set(b)):
    if k not in a or k not in b:diffs.append(dict(location=loc+'/'+k,scalar=a.get(k),numpy=b.get(k)))
    else:compare(a[k],b[k],loc+'/'+k)
  elif isinstance(a,list):
   if len(a)!=len(b):diffs.append(dict(location=loc+'/length',scalar=len(a),numpy=len(b)))
   for i,(x,y) in enumerate(zip(a,b)):compare(x,y,loc+'/'+str(i))
  elif a!=b:diffs.append(dict(location=loc,scalar=a,numpy=b))
 if not np_only:
  a,b=values['scalar'],values['numpy']
  if a['ok']!=b['ok']:compare(a,b,'status')
  elif a['ok']:compare(a,b,'result')
 return dict(case=c['case_id'],mode=mode,N=n,mismatches=diffs,timing=times,np_only=np_only)
def main():
 p=argparse.ArgumentParser()
 for name in ['cases','modes','shim','out']:p.add_argument('--'+name,required=True)
 p.add_argument('--jobs',required=True,type=int);a=p.parse_args();paths=sorted(pathlib.Path(a.cases).glob('batch_*/*.case.json'),key=lambda p:p.name);jobs=[(str(p),m,None,False,a.shim) for m in a.modes.split(',') for p in paths];selected=paths[::143][:64];jobs += [(str(p),'B',n,n==16384,a.shim) for n in [1024,16384] for p in selected];counts={};first=[];timing=collections.defaultdict(list)
 with concurrent.futures.ProcessPoolExecutor(max_workers=a.jobs) as pool:
  for r in pool.map(run_case,jobs):
   group=r['mode']+('/large-N' if r['N']==1024 else '/numpy-only' if r['np_only'] else '');bucket=counts.setdefault(group,{'cases':0,'mismatched_cases':0,'mismatches':0});bucket['cases']+=1;bucket['mismatched_cases']+=bool(r['mismatches']);bucket['mismatches']+=len(r['mismatches'])
   for d in r['mismatches']:
    if len(first)<50:first.append(dict(case=r['case'],mode=r['mode'],N=r['N'],**d))
   for backend,t in r['timing'].items():timing[r['mode']+'/'+str(r['N'])+'/'+backend].append(t)
 out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True);(out/'equivalence.json').write_text(json.dumps({'counts':counts,'first50_mismatches':first},sort_keys=True,indent=2)+'\n',encoding='utf-8');(out/'timing.json').write_text(json.dumps({k:{'n':len(v),'median_seconds':float(np.median(v)),'p95_seconds':float(np.percentile(v,95))} for k,v in timing.items()},sort_keys=True,indent=2)+'\n',encoding='utf-8');print(json.dumps(counts,sort_keys=True))
 if any(v['mismatches'] for v in counts.values()):sys.exit(1)
if __name__=='__main__':main()
