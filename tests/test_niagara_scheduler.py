import unittest,os,tempfile,struct,json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from niagara_scheduler import *
from niagara_sched_model import load_system,load_param_store,type_info,VarLayout
class SchedulerTests(unittest.TestCase):
 def test_flip(self):
  frame,valid=1,False;out=[]
  for _ in range(4):frame,valid,cur,prev=flip_indices(frame,valid);out.append((cur,prev))
  self.assertEqual(out,[(0,0),(1,0),(0,1),(1,0)])
 def test_ids(self):
  table,free,used=allocate_ids([],[],-1,3);self.assertEqual(free,[2,1,0]);self.assertEqual(table,[-1]*3)
  self.assertEqual([free.pop(),free.pop(),free.pop()],[0,1,2]);table,free,used=allocate_ids(table,free,2,5);self.assertEqual(free,[4,3]);self.assertEqual(len(table),5)
  table,free,used=allocate_ids(table,free,3,2);self.assertEqual(table,[-1]*5)
 def test_delta(self):
  self.assertEqual(delta_step(.5,.125),(.5,.125,.25));self.assertEqual(delta_step(1/60,.125)[2],1.)
 def test_global(self):
  self.assertEqual(pack_global(dict(WorldDeltaTime=.5,EngineDeltaTime=.125,EngineInvDeltaTime=8.,EngineTime=1.,EngineRealTime=1.,QualityLevel=3)).hex(),'0000003f0000003e000000410000803f0000803f030000000000000000000000')
 def test_owner(self):
  fields=owner_fields((100,-50,25),(1,2,3));packed=pack_owner(fields)
  for name,(offset,kind,count) in LAYOUTS['owner'][1].items():self.assertEqual(struct.unpack_from('<'+kind*count,packed,offset),tuple(fields[name]))
  self.assertEqual(struct.unpack_from('<4f',packed,496),(0,0,0,2097152))
 def test_owner_rotation(self):
  import math
  p,y,r=(math.radians(v) for v in (30.,-115.,10.));sp,cp,sy,cy,sr,cr=math.sin(p),math.cos(p),math.sin(y),math.cos(y),math.sin(r),math.cos(r)
  rows=np.array([[cp*cy,cp*sy,sp],[sr*sp*cy-cr*sy,sr*sp*sy+cr*cy,-sr*cp],[-(cr*sp*cy+sr*sy),cy*sr-cr*sp*sy,cr*cp]])  # FRotationMatrix
  p,y,r=p/2,y/2,r/2;sp,cp,sy,cy,sr,cr=math.sin(p),math.cos(p),math.sin(y),math.cos(y),math.sin(r),math.cos(r);q=(cr*sp*sy-sr*cp*cy,-cr*sp*cy-sr*cp*sy,cr*cp*sy-sr*sp*cy,cr*cp*cy+sr*sp*sy)
  self.assertTrue(np.allclose(quat_axes(q),rows));f=owner_fields((100,-50,25),(0,0,0),rotation=q);l2w=np.array(f['EngineLocalToWorld']).reshape(4,4);w2l=np.array(f['EngineWorldToLocal']).reshape(4,4)
  self.assertTrue(np.allclose(l2w@w2l,np.eye(4)));self.assertTrue(np.allclose(np.array([1.,0,0,1])@l2w,[100+rows[0][0],-50+rows[0][1],25+rows[0][2],1]))
  self.assertTrue(np.allclose(f['EngineLocalToWorldTransposed'],l2w.T.ravel()));self.assertTrue(np.allclose([f['EngineXAxis'],f['EngineYAxis'],f['EngineZAxis']],rows));self.assertEqual(f['EngineRotation'],q)
  self.assertEqual(owner_fields((1.,2.,3.),(0,0,0),rotation=(0.,0.,0.,1.)),owner_fields((1.,2.,3.),(0,0,0)))
 def test_planner(self):
  infos=[(3,0.,.1,0),(2,.05,.2,1),(0,0.,1.,0)];self.assertEqual(plan_spawn_batches(infos,4,True),[(3,.1,0.,0),(1,.2,.05,1)]);self.assertEqual(plan_spawn_batches(infos,4,False),[])
 def test_event_counts(self):
  self.assertEqual(event_spawn_counts(7,5,2,True),[2]*5);self.assertEqual(event_spawn_counts(7,0,2,True),[2]*7);self.assertEqual(event_spawn_counts(7,5,2,False),[]);self.assertEqual(event_spawn_counts(3,0,0,True),[])
 def test_row_to_store(self):
  td={'ClassStructOrEnum':{'ObjectName':"Class'NiagaraSpawnInfo'"},'UnderlyingType':2};store=load_param_store({'ParameterSize':32,'ParameterData':[0]*32,'SortedParameterOffsets':[{'Name':'S','Offset':16,'TypeDef':td}]},False);v=VarLayout('S',type_info(td),0,0);row_to_store(v,[float_word(.25),float_word(.5)],[7,2],store);self.assertEqual(store.data[16:32].hex(),'070000000000803e0000003f02000000')
 def test_prev(self):
  store=load_param_store({'ParameterSize':4,'ParameterData':[1,2,3,4,9,9,9,9]},True);prev_copy(store);self.assertEqual(store.data,bytearray([1,2,3,4]*2))
 def model(self,name):return load_system(next(Path(os.environ['DR_NIAGARA_CORPUS']).rglob(name+'.json')))
 @unittest.skipUnless(os.environ.get('DR_NIAGARA_CORPUS'),'corpus unset')
 def test_spawn_totals(self):
  s=Scheduler(self.model('NS_LocalFogSphere'),BakeInputs(ticks=0,math_backend='reference'));s.activate();s.cur=s.prev=0;s.diagnostics=[];r=s.emitters[0];r.total=10;r.spawn_infos=[(3,0.,.1,0),(2,.05,.2,1)];seen=[]
  def record_vm(script,n,datasets,tables,*args,**kw):
   seen.append(struct.unpack_from('<i',tables[3],4)[0]);outputs={};written={}
   for d,ds in datasets.items():
    for k,cap in ds.output_capacities.items():outputs[d,k]=np.zeros(cap,dtype=np.uint32);written[d,k]=np.ones(cap,dtype=bool)
   return SimpleNamespace(outputs=outputs,written=written,access_indices={d:n-1 for d in datasets},id_states={})
  s._vm=record_vm;s._emitter_tick(r,1/60);self.assertEqual(seen,[10,13]);self.assertEqual(r.total,15)
 @unittest.skipUnless(os.environ.get('DR_NIAGARA_CORPUS'),'corpus unset')
 def test_fog_double(self):
  with tempfile.TemporaryDirectory(prefix='ns2-fog-') as temp:
   model=self.model('NS_LocalFogSphere');inputs=BakeInputs(ticks=120,di_mode='none',svml_shim_path=os.environ['DR_SVML_SHIM']);a=bake(model,inputs,Path(temp)/'a');b=bake(model,inputs,Path(temp)/'b');self.assertEqual(a,b);rows=[json.loads(line) for line in (Path(temp)/'a/ticks.jsonl').read_text().splitlines()]
   for row in rows:
    for e in row['emitters']:self.assertGreaterEqual(e['count'],0);self.assertIn(e['state'],range(5))
   self.assertLess(abs(word_float(int(rows[-1]['system']['age'],16))-2.),1e-4)
 @unittest.skipUnless(os.environ.get('DR_NIAGARA_CORPUS'),'corpus unset')
 def test_tracers_double(self):
  with tempfile.TemporaryDirectory(prefix='ns2-tracers-') as temp:
   model=self.model('NS_Cin_FakeWeaponTracers');inputs=BakeInputs(ticks=300,di_mode='stub',svml_shim_path=os.environ['DR_SVML_SHIM']);self.assertEqual(bake(model,inputs,Path(temp)/'a'),bake(model,inputs,Path(temp)/'b'))
