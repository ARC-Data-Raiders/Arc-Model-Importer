"""Gate S0: static CPU Niagara scheduler self-consistency and reproducibility."""
import argparse,collections,json,multiprocessing as mp,pathlib,sys,time,traceback
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent))
from Niagara.niagara import Scheduler, BakeInputs, BakeError, save_bake
from Niagara.niagara import load_system

def read(p):return json.loads(pathlib.Path(p).read_text(encoding='utf-8-sig'))
def write(p,v):
 p=pathlib.Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,sort_keys=True,indent=2)+'\n',encoding='utf-8')
def run_variant(job):
 start=time.perf_counter();path,variant,out,shim,vector_fields=job;out=pathlib.Path(out);checks={k:True for k in ['no_exception','count_bound','inactive_spawning','state_range','disabled_terminal','complete_empty','live_ids_unique','id_to_index','id_partition','warmup_reset']};failures=[];nonfinite=collections.Counter()
 def fail(name,detail):
  checks[name]=False
  if len(failures)<10:failures.append({'check':name,**detail})
 try:
  model=load_system(path);previous={e.index:0 if e.enabled else 4 for e in model.emitters}
  class ObservedScheduler(Scheduler):
   def _record(self,phase,dt_in,dt):
    record=super()._record(phase,dt_in,dt)
    for r in self.emitters:
     index=r.model.index;detail={'tick':self.global_tick,'phase':phase,'emitter':index}
     if phase=='normal':
      if r.count>r.orig+r.spawn_total+r.event_requested:fail('count_bound',{**detail,'count':r.count,'orig':r.orig,'spawn_total':r.spawn_total,'events':r.event_requested})
      if r.state==1 and r.spawned!=0:fail('inactive_spawning',{**detail,'spawned':r.spawned})
      if r.state not in range(5):fail('state_range',{**detail,'state':r.state})
      if previous[index]==4 and r.state!=4:fail('disabled_terminal',{**detail,'state':r.state})
      if r.state==3 and r.count!=0:fail('complete_empty',{**detail,'count':r.count})
     previous[index]=r.state
     if not r.model.enabled:continue
     if r.model.requires_persistent_ids:
      v=r.model.particle_layout.find('ID')
      if v is None:fail('live_ids_unique',{**detail,'reason':'ID attribute missing'})
      else:
       live=[int(x) for x in r.buffer['int32',v.int_start][:r.count]]
       if len(set(live))!=len(live):fail('live_ids_unique',detail)
       if any(x<0 or x>=len(r.id_to_index) or r.id_to_index[x]!=i for i,x in enumerate(live)):fail('id_to_index',detail)
       if sorted(r.free+live)!=list(range(len(r.id_to_index))):fail('id_partition',detail)
     for v in r.model.particle_layout.variables:
      for j in range(len(v.type.float_offsets)):
       values=r.buffer['float',v.float_start+j].view(np.float32);key=str(index)+':'+v.name
       nonfinite[key+':nan']+=int(np.count_nonzero(np.isnan(values)));nonfinite[key+':inf']+=int(np.count_nonzero(np.isinf(values)))
    return record
  mode='none' if variant in ['none_a','none_b','move'] else 'real' if variant in ['real_a','real_b'] else 'stub';inputs=BakeInputs(ticks=300,di_mode=mode,svml_shim_path=shim,vector_fields=vector_fields if mode=='real' else None,deactivate_tick=120 if variant=='deact' else None,owner_translation_list=tuple((10*t,0,0) for t in range(1,301)) if variant=='move' else None)
  scheduler=ObservedScheduler(model,inputs);scheduler.activate()
  if scheduler.age!=0 or scheduler.tick_count!=0:fail('warmup_reset',{'age':scheduler.age,'tick_count':scheduler.tick_count})
  scheduler.run();save_bake(scheduler,out)
  result={'class':'pass' if all(checks.values()) else 'fail','checks':checks,'first10_failures':failures,'nonfinite_by_attribute':dict(nonfinite),'wall_seconds':time.perf_counter()-start}
 except BakeError as exc:
  if variant in ['real_a','real_b'] and str(exc).startswith('di_unimplemented:'):result={'class':'skipped_unimplemented','checks':{},'first10_failures':[],'reason':str(exc),'nonfinite_by_attribute':{},'wall_seconds':time.perf_counter()-start}
  else:checks['no_exception']=False;result={'class':'error','checks':checks,'first10_failures':failures+[{'check':'no_exception','traceback':traceback.format_exc()}],'nonfinite_by_attribute':dict(nonfinite),'wall_seconds':time.perf_counter()-start}
 except Exception:
  checks['no_exception']=False;result={'class':'error','checks':checks,'first10_failures':failures+[{'check':'no_exception','traceback':traceback.format_exc()}],'nonfinite_by_attribute':dict(nonfinite),'wall_seconds':time.perf_counter()-start}
 write(out/'checks.json',result)

def main():
 parser=argparse.ArgumentParser()
 for key in ['corpus','census','out','shim']:parser.add_argument('--'+key,required=True)
 parser.add_argument('--jobs',type=int,required=True);parser.add_argument('--variants',choices=['s0','real'],default='s0');parser.add_argument('--vector-fields',default=None);a=parser.parse_args();out=pathlib.Path(a.out);universe=[r for r in read(a.census)['systems'] if r['all_enabled_cpu'] and not r['unsupported']];jobs=[]
 for row in universe:
  path=pathlib.Path(row['path']);key=path.stem
  for variant in (['stub_a','stub_b','deact']+(['none_a','none_b','move'] if row['noext'] else []) if a.variants=='s0' else ['real_a','real_b']):jobs.append((str(path),variant,str(out/key/variant),a.shim,a.vector_fields))
 # The coordinator is one process, leaving at most jobs-1 workers under R7.
 context=mp.get_context('spawn');pending=collections.deque(jobs);running=[];finished=[];start=time.perf_counter();last_resource_check=start
 while pending or running:
  while pending and len(running)<max(1,a.jobs-1):
   job=pending.popleft();process=context.Process(target=run_variant,args=(job,));process.start();running.append((process,job,time.monotonic()))
  for process,job,began in running[:]:
   timedout=time.monotonic()-began>1200
   if process.is_alive() and not timedout:continue
   if timedout:process.terminate()
   process.join();path,variant,directory,*_=job;checkpath=pathlib.Path(directory)/'checks.json'
   if timedout:write(checkpath,{'class':'timeout','checks':{'no_exception':False},'first10_failures':[{'check':'no_exception','reason':'20 minute per-run timeout'}],'wall_seconds':time.monotonic()-began})
   elif not checkpath.exists():write(checkpath,{'class':'error','checks':{'no_exception':False},'first10_failures':[{'check':'no_exception','reason':'worker exit '+str(process.exitcode)}],'wall_seconds':time.monotonic()-began})
   finished.append((path,variant,directory));running.remove((process,job,began));print('completed '+str(len(finished))+'/'+str(len(jobs))+' '+pathlib.Path(path).stem+' '+variant,flush=True)
  if time.perf_counter()-last_resource_check>30:
   size=sum(p.stat().st_size for p in out.parent.rglob('*') if p.is_file());last_resource_check=time.perf_counter()
   if size>30*1024**3:
    for process,_,_ in running:process.terminate();process.join()
    raise RuntimeError('RUN exceeds 30 GB')
  time.sleep(.2)
 results={};totals={};classes={}
 for row in universe:
  key=pathlib.Path(row['path']).stem;variants={variant:read(pathlib.Path(directory)/'checks.json') for path,variant,directory in finished if path==row['path']}
  for prefix in ['stub','none','real']:
   left,right=prefix+'_a',prefix+'_b'
   if left not in variants or variants[left]['class']=='skipped_unimplemented':continue
   ap,bp=out/key/left/'hashes.json',out/key/right/'hashes.json';same=ap.exists() and bp.exists() and read(ap)==read(bp)
   for variant in [left,right]:
    item=variants[variant];item['checks']['determinism']=same
    if not same:
     if item['class']=='pass':item['class']='fail'
     if len(item['first10_failures'])<10:item['first10_failures'].append({'check':'determinism','pair':[left,right]})
    write(out/key/variant/'checks.json',item)
  results[key]={'package':row['package'],'path':row['path'],'variants':variants}
  for variant,item in variants.items():
   classes.setdefault(variant,collections.Counter())[item['class']]+=1
   for check,value in item['checks'].items():totals.setdefault(variant,{}).setdefault(check,collections.Counter())['pass' if value else 'fail']+=1
 summary={'systems':results,'totals':totals,'classes_per_variant':classes,'universe_count':len(universe),'expected_runs':len(jobs),'completed_runs':len(finished),'wall_seconds':time.perf_counter()-start};destination=out.parent/('out/NS3/gate_s0_summary.json' if a.variants=='s0' else 'out/ND4/gate_s0r_summary.json');write(destination,summary)
 if len(finished)!=len(jobs):raise RuntimeError('Not every variant ran')
 print(json.dumps({'universe':len(universe),'runs':len(finished),'classes':classes,'wall_seconds':summary['wall_seconds']},sort_keys=True),flush=True)
if __name__=='__main__':main()
