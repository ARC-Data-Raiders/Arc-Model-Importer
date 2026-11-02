"""Offline per-tick cooked Niagara CPU scheduler (Wave N3)."""
from dataclasses import dataclass,asdict
import copy
import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import numpy as np
from niagara_context import LAYOUTS
from niagara_runtime import Dataset,IdState,execute,float_word,word_float,word_int
from niagara_vm_numpy import NpDataset,execute_np
import niagara_di
import niagara_ir

class BakeError(ValueError):pass
@dataclass
class BakeInputs:
    ticks:int
    dt:float=1/60
    dt_list:tuple=None
    quality_level:int=3
    max_delta_time:float=.125
    random_seed_offset:int=0
    bake_seed:int=0
    owner_translation:tuple=(0.,0.,0.)
    owner_translation_list:tuple=None
    owner_rotation:tuple=(0.,0.,0.,1.)
    owner_rotation_list:tuple=None
    owner_actor_velocity:tuple=(0.,0.,0.)
    world_time0:float=0.
    lod_distance:float=0.
    max_lod_distance:float=3.4028234663852886e38
    deactivate_tick:int=None
    di_mode:str='none'
    di_unimplemented:str='error'
    di_fma_mode:str='unfused'
    vector_fields:str=None
    di_vf_fma_mode:str='unfused'
    di_vf_half_decode:str='fast'
    vm_backend:str='numpy'
    math_backend:str='svml_shim'
    svml_shim_path:str=None
    record_buffers:bool=False
    camera:tuple=None
    camera_list:tuple=None
    collision_mesh:str=None
    gpu_bindings:str=None
    scene_view_header:str=None
    gpu_shaders:str=None
    ground_height:float=None
    ground_base_color:tuple=(.16,.15,.11)
    parameter_overrides:dict=None

def f32(x):return word_float(float_word(float(x)))
def hexfloat(x):return format(float_word(x),'08X')
def flip_indices(cur_frame,params_valid):
    cur_frame^=1
    if cur_frame==1:params_valid=True
    return cur_frame,params_valid,cur_frame,1 if (params_valid^(cur_frame==1)) else 0

def allocate_ids(id_to_index,free,max_used,alloc):
    required=max(alloc,max_used+1);length=len(id_to_index);free=list(free)
    if required>length:free.extend(range(required-1,length-1,-1))
    return [-1]*max(required,length),free,-1

def delta_step(dt_in,max_delta_time):
    dt_in32=f32(dt_in);dt=min(dt_in32,f32(max_delta_time));return dt_in32,dt,f32(dt/dt_in32)
def _pack(kind,d):
    size,fields=LAYOUTS[kind];data=bytearray(size)
    for name,(offset,fmt,count) in fields.items():
        if name not in d:continue
        values=[d[name]] if count==1 else d[name]
        if fmt=='f':values=[f32(v) for v in values]
        else:values=[word_int(int(v)&0xffffffff) if fmt=='i' else int(v)&0xffffffff for v in values]
        struct.pack_into('<'+fmt*count,data,offset,*values)
    return bytes(data)
def pack_global(d):return _pack('global',d)
def pack_system(d):return _pack('system',d)
def pack_emitter(d):return _pack('emitter',d)
def pack_owner(d):return _pack('owner',d)
IDENTITY_QUAT=(0.,0.,0.,1.)
def quat_axes(q):
    """UE FQuat (x,y,z,w) -> rotated X/Y/Z axes as rows (FQuatRotationTranslationMatrix, row-vector convention)."""
    x,y,z,w=(float(v) for v in q);x2,y2,z2=x+x,y+y,z+z;xx,xy,xz,yy,yz,zz,wx,wy,wz=x*x2,x*y2,x*z2,y*y2,y*z2,z*z2,w*x2,w*y2,w*z2
    return ((1-(yy+zz),xy+wz,xz-wy),(xy-wz,1-(xx+zz),yz+wx),(xz+wy,yz-wx,1-(xx+yy)))
def owner_fields(location,velocity,tile=2097152,rotation=IDENTITY_QUAT):
    tx,ty,tz=location
    if tuple(rotation)==IDENTITY_QUAT:
        local=[1,0,0,0,0,1,0,0,0,0,1,0,tx,ty,tz,1];world=[1,0,0,0,0,1,0,0,0,0,1,0,-tx,-ty,-tz,1];axes=((1,0,0),(0,1,0),(0,0,1));rotation=(0,0,0,1)
    else:
        axes=quat_axes(rotation);r=np.array(axes,dtype=np.float64);t=np.array([tx,ty,tz],dtype=np.float64);m=np.eye(4);m[:3,:3]=r;m[3,:3]=t;inv=np.eye(4);inv[:3,:3]=r.T;inv[3,:3]=-(t@r.T)
        local=m.ravel().tolist();world=inv.ravel().tolist();rotation=tuple(float(v) for v in rotation)
    return dict(EngineLocalToWorld=local,EngineWorldToLocal=world,EngineLocalToWorldTransposed=np.array(local).reshape(4,4).T.ravel().tolist(),EngineWorldToLocalTransposed=np.array(world).reshape(4,4).T.ravel().tolist(),EngineLocalToWorldNoScale=local,EngineWorldToLocalNoScale=world,EngineRotation=rotation,EnginePosition=location,EngineVelocity=velocity,EngineXAxis=axes[0],EngineYAxis=axes[1],EngineZAxis=axes[2],EngineScale=(1,1,1),EngineLWCTile=(0,0,0,tile))
def plan_spawn_batches(spawn_infos,remaining,active):
    if not active:return []
    batches=[]
    for count,start,interval,group in spawn_infos:
        c=min(count,remaining)
        if c>0:batches.append((c,interval,start,group))
        remaining-=c
    return batches

def event_spawn_counts(n_events,max_events_per_frame,spawn_number,active):
    if not active or spawn_number<=0:return []
    n=min(n_events,max_events_per_frame) if max_events_per_frame>0 else n_events
    return [spawn_number]*n

def prev_copy(store):
    if store.interpolated:store.data[store.size:2*store.size]=store.data[:store.size]
def copy_by_name(src_entries,src_bytes,dst_store):
    for name,(offset,t) in src_entries.items():
        if name.startswith('PREV_') or name not in dst_store.entries:continue
        target,dt=dst_store.entries[name]
        if dt.key!=t.key or target>=dst_store.size:continue
        for b in t.float_offsets+t.int_offsets:
            dst_store.data[target+b:target+b+4]=src_bytes[offset+b:offset+b+4]
def row_to_store(var_layout,float_words,int_words,store):
    v=var_layout
    if v.name.startswith('PREV_') or v.name not in store.entries:return
    offset,t=store.entries[v.name]
    if t.key!=v.type.key or offset>=store.size:return
    for offsets,words,start in [(t.float_offsets,float_words,v.float_start),(t.int_offsets,int_words,v.int_start)]:
        for j,b in enumerate(offsets):struct.pack_into('<I',store.data,offset+b,int(words[start+j])&0xffffffff)
def set_named(store,name,words):
    if name not in store.entries or name.startswith('PREV_'):return
    offset,t=store.entries[name]
    if offset>=store.size:return
    for b,w in zip(sorted(t.float_offsets+t.int_offsets),words):struct.pack_into('<I',store.data,offset+b,int(w)&0xffffffff)
def override_words(value):
    vals=value if isinstance(value,(list,tuple)) else [value]
    return [v&0xffffffff if isinstance(v,int) and not isinstance(v,bool) else float_word(f32(float(v))) for v in vals]
def apply_overrides(store,overrides):
    """User./NPC. values from a placed component or parameter collection; interpolated stores get both halves."""
    for name,value in (overrides or {}).items():
        words=override_words(value);set_named(store,name,words)
        if store.interpolated and name in store.entries:
            offset,t=store.entries[name]
            for b,w in zip(sorted(t.float_offsets+t.int_offsets),words):struct.pack_into('<I',store.data,store.size+offset+b,int(w)&0xffffffff)
def _columns(layout,count=0):return {(kind,j):np.zeros(count,dtype=np.uint32) for kind,n in [('float',layout.total_float),('int32',layout.total_int)] for j in range(n)}
def _extend(columns,count):
    return {k:np.pad(v,(0,max(0,count-len(v))))[:count].copy() for k,v in columns.items()}
def _hash(text):return hashlib.sha256(text.encode('utf-8')).digest()
def rand31(text):return int.from_bytes(_hash(text)[:4],'little')&0x7fffffff

class Scheduler:
    def __init__(self,model,inputs):
        self.model=model;self.inputs=inputs
        if model.unsupported:raise BakeError('unsupported:'+','.join(model.unsupported))
        gpu=[e for e in model.emitters if e.enabled and e.sim_target!='CPUSim']
        if gpu and (inputs.gpu_bindings is None or inputs.gpu_shaders is None):raise BakeError('gpu_emitter')
        self.gpu_programs={}
        if gpu:
            import niagara_d3d11,niagara_gpu
            self.gpu_device=niagara_d3d11.Device(warp=True);doc=json.loads(Path(model.path).read_text(encoding='utf-8-sig'))
            try:gpu_vf=niagara_di.load_vector_fields(inputs.vector_fields) if inputs.vector_fields else {}
            except (OSError,ValueError,KeyError) as exc:raise BakeError('vector_fields:'+str(exc)) from exc
            for e in gpu:
                try:self.gpu_programs[e.index]=niagara_gpu.GpuEmitterProgram(self.gpu_device,doc,doc[e.gpu.emitter_index],e.gpu.script_index,inputs.gpu_bindings,inputs.gpu_shaders,Path(model.path).stem,gpu_vf,inputs.ground_height)
                except niagara_gpu.GpuError as exc:raise BakeError(str(exc)) from exc
        if inputs.dt_list is not None and len(inputs.dt_list)!=inputs.ticks:raise BakeError('dt_list_length')
        if inputs.owner_translation_list is not None and len(inputs.owner_translation_list)!=inputs.ticks:raise BakeError('owner_translation_list_length')
        if inputs.owner_rotation_list is not None and len(inputs.owner_rotation_list)!=inputs.ticks:raise BakeError('owner_rotation_list_length')
        if any(len(q)!=4 or not abs(sum(float(v)*float(v) for v in q)-1)<1e-3 for q in [inputs.owner_rotation]+list(inputs.owner_rotation_list or ())):raise BakeError('owner_rotation')
        if any(not float(dt)>0 for dt in (inputs.dt_list if inputs.dt_list is not None else [inputs.dt])):raise BakeError('dt')
        if any(not abs(float(x))<1048576 for loc in [inputs.owner_translation]+list(inputs.owner_translation_list or ()) for x in loc):raise BakeError('owner_translation')
        if inputs.ground_height is not None and not abs(float(inputs.ground_height))<1048576:raise BakeError('ground_height')
        if inputs.math_backend=='svml_shim' and inputs.svml_shim_path is None:raise BakeError('svml_shim_path')
        if inputs.di_mode not in ('none','stub','real'):raise BakeError('di_mode')
        self.di_handlers={};self.di_unimplemented=[];self.di_state=niagara_di.DIInstanceState()
        if inputs.camera_list is not None and len(inputs.camera_list)!=inputs.ticks:raise BakeError('camera_list_length')
        if inputs.collision_mesh:
            try:self.di_state.collision=niagara_di.CollisionWorld.load(inputs.collision_mesh)
            except (OSError,ValueError,KeyError) as exc:raise BakeError('collision_mesh:'+str(exc)) from exc
        if inputs.di_mode=='real':
            try:vector_fields=niagara_di.load_vector_fields(inputs.vector_fields) if inputs.vector_fields else {}
            except (OSError,ValueError,KeyError) as exc:raise BakeError('vector_fields:'+str(exc)) from exc
            exports=niagara_ir.exports_from_document(json.loads(Path(model.path).read_text(encoding='utf-8-sig')))
            scripts=[model.system_spawn,model.system_update]+[s for e in model.emitters if e.enabled for s in [e.spawn,e.update]+[h.script for h in e.event_handlers if not h.dead]]
            for script in scripts:
                if script is None or not script.has_ext or script.export_index in self.di_handlers:continue
                try:handler=niagara_di.script_handler(exports,niagara_di.bind_script(exports,script.export_index),fma_mode=inputs.di_fma_mode,unimplemented=inputs.di_unimplemented,state=self.di_state,vector_fields=vector_fields,vf_fma_mode=inputs.di_vf_fma_mode,vf_half_decode=inputs.di_vf_half_decode)
                except niagara_di.DIError as exc:raise BakeError(str(exc)) from exc
                self.di_handlers[script.export_index]=handler;self.di_unimplemented=sorted(set(self.di_unimplemented)|set(handler.unimplemented_functions))
        self.records=[];self.activated=False
    def _particle_source(self,r):
        return lambda:(r.count,r.model.particle_layout,r.buffer,r.id_to_index,getattr(r.model,'gpu',None) is not None) if r.model.enabled else None
    def activate(self):
        m,a=self.model,self.inputs;self.age=0.;self.tick_count=0;self.global_tick=0;self.normal_tick=0;self.actual=0;self.requested=0;self.prev_location=None;self.world_time=a.world_time0;self.cur_frame=1;self.params_valid=False;self.pending=True
        self.random_seed=m.random_seed if m.determinism else rand31(f'{a.bake_seed}|{m.package}|system_seed')
        self.global_params=[{},{}];self.system_params=[{},{}];self.owner_params=[owner_fields((0,0,0),(0,0,0),0),owner_fields((0,0,0),(0,0,0),0)];self.emitter_params=[[{'EmitterSpawnCountScale':1.},{'EmitterSpawnCountScale':1.}] for e in m.emitters];self.instance_store=copy.deepcopy(m.instance_params);self.system_stores={key:copy.deepcopy(s.param_store) for key,s in [('spawn',m.system_spawn),('update',m.system_update)]};self.main=_columns(m.main_layout,1);self.events={};self.emitters=[]
        for e in m.emitters:
            r=SimpleNamespace(model=e,state=0 if e.enabled else 4,age=0.,tick_count=0,total=0,count=0,buffer={},id_to_index=[],free=[],max_used=-1,max_alloc=0,estimate=0,spawn_infos=[],stores={},spawned=0,killed_update=0,killed_spawn=0,event_spawned=0,orig=0,spawn_total=0,event_requested=0,alloc=0)
            if e.enabled and getattr(e,'gpu',None) is not None:
                import niagara_gpu
                r.buffer=_columns(e.particle_layout);r.stores={'gpu':copy.deepcopy(e.gpu.param_store)};r.gpu_reset=True;r.gpu_ids=niagara_gpu.GpuIdState() if e.requires_persistent_ids else None
                r.random_seed=word_int((e.random_seed+a.random_seed_offset)&0xffffffff);r.instance_seed=rand31(f'{a.bake_seed}|{m.package}|instance_seed|{e.index}|0')
            elif e.enabled:
                r.buffer=_columns(e.particle_layout);r.stores={'spawn':copy.deepcopy(e.spawn.param_store),'update':copy.deepcopy(e.update.param_store)}
                for j,h in enumerate(e.event_handlers):
                    if not h.dead:r.stores['event'+str(j)]=copy.deepcopy(h.script.param_store)
                r.random_seed=word_int((e.random_seed+a.random_seed_offset)&0xffffffff);r.instance_seed=rand31(f'{a.bake_seed}|{m.package}|instance_seed|{e.index}|0')
                for g in e.spawn_generators+e.update_generators:self.events[e.id_name,g.event_id]=SimpleNamespace(layout=g.layout,columns=_columns(g.layout),count=0)
            self.emitters.append(r)
        for store in [self.instance_store,*self.system_stores.values(),*(st for r in self.emitters for st in r.stores.values())]:apply_overrides(store,a.parameter_overrides)
        for r in self.emitters:
            for key in {r.model.unique_name,r.model.handle_name}:
                if key:self.di_state.particle_sources[key]=self._particle_source(r)
        self.records=[];self.activated=True
        if m.warmup_tick_count>0 and m.warmup_tick_delta>1e-8:
            for _ in range(m.warmup_tick_count):self._tick(m.warmup_tick_delta,a.owner_translation,'warmup',a.owner_rotation)
        self.age=0.;self.tick_count=0;self.activation_state={'age':self.age,'tick_count':self.tick_count}
        return self
    def _rng(self,scope,usage,event,batch):
        h=_hash(f'{self.inputs.bake_seed}|{self.model.package}|{scope}|{usage}|{event}|{self.global_tick}|{batch}')
        return int.from_bytes(h[:8],'little'),int.from_bytes(h[8:16],'little')|1
    def _vm(self,script,n,datasets,tables,scope,usage,event=-1,batch=0,ids=None):
        if script.has_ext and self.inputs.di_mode=='none':raise BakeError('external_func_call')
        kw=dict(num_instances=n,constant_tables=tables,external_functions=script.ext_functions,profile='pioneer-2.1.0',math_backend=self.inputs.math_backend,svml_shim_path=self.inputs.svml_shim_path,fma_mode='unfused',external_handler='gate_v_stub' if self.inputs.di_mode=='stub' else self.di_handlers.get(script.export_index) if self.inputs.di_mode=='real' else None,id_states=ids)
        if any(i.name=='random' for i in script.program.instructions):kw['rng_seed']=self._rng(scope,usage,event,batch)
        if self.inputs.vm_backend=='numpy':return execute_np(program=script.program,datasets=datasets,**kw)
        scalar={d:Dataset(inputs={k:[word_float(int(w)) if k[0]=='float' else word_int(int(w)) for w in v] for k,v in s.inputs.items()},output_capacities=s.output_capacities,instance_offset=s.instance_offset,access_index=s.access_index) for d,s in datasets.items()}
        r=execute(script.bytecode,datasets=scalar,x86_semantics=True,max_instances=4_000_000,max_instructions=1_000_000,max_lane_operations=10**12,max_storage_words=10**12,**kw);written={};out={}
        for key,v in r.outputs.items():written[key]=np.array([x is not None for x in v]);out[key]=np.array([0xdeadbeef if x is None else float_word(x) if key[1][0]=='float' else int(x)&0xffffffff for x in v],dtype=np.uint32)
        r.outputs=out;r.written=written;return r
    def _params_row(self,layout,phase):
        row=_columns(layout,1)
        for v in layout.variables:
            if v.name not in self.instance_store.entries:continue
            offset,t=self.instance_store.entries[v.name]
            if t.key!=v.type.key or v.name.startswith('PREV_'):continue
            for kind,offsets,start in [('float',t.float_offsets,v.float_start),('int32',t.int_offsets,v.int_start)]:
                for j,b in enumerate(offsets):row[kind,start+j][0]=struct.unpack_from('<I',self.instance_store.data,offset+b)[0]
        for group,packed in [('global',pack_global(self.global_params[self.cur])),('system',pack_system(self.system_params[self.cur])),('owner',pack_owner(self.owner_params[self.cur]))]+[('emitter',pack_emitter(v[self.cur])) for v in self.emitter_params]:
            if group=='emitter':continue
            for kind,offset,comp in self.model.bindings.get((phase,group),()):row[kind,comp][0]=struct.unpack_from('<I',packed,offset)[0]
        for i,values in enumerate(self.emitter_params):
            packed=pack_emitter(values[self.cur])
            for kind,offset,comp in self.model.bindings.get((phase,'emitter',i),()):row[kind,comp][0]=struct.unpack_from('<I',packed,offset)[0]
        return row
    def _system(self,phase,row,usage,global_override=None):
        script=self.model.system_spawn if phase=='spawn' else self.model.system_update;store=self.system_stores[phase];ds={0:NpDataset(self.main,{k:1 for k in self.main}),1:NpDataset(row,{})};r=self._vm(script,1,ds,[pack_global(global_override or self.global_params[self.cur]),bytes(store.data[:store.size]),script.literals],'system',usage)
        for key,words in self.main.items():
            if r.written[0,key][0]:words[0]=r.outputs[0,key][0]
            else:
                name=next(v.name for v in self.model.main_layout.variables if (v.float_start<=key[1]<v.float_start+len(v.type.float_offsets) if key[0]=='float' else v.int_start<=key[1]<v.int_start+len(v.type.int_offsets)))
                self.diagnostics.append('system_unwritten:'+name)
    def _row_value(self,name):
        v=self.model.main_layout.find(name)
        if v is None:return None
        f=[word_float(int(self.main['float',v.float_start+j][0])) for j in range(len(v.type.float_offsets))];i=[word_int(int(self.main['int32',v.int_start+j][0])) for j in range(len(v.type.int_offsets))];return f,i
    def gpu_view_words(self):
        """View UB constants (5.7 layout) for the current DI camera (spec N-O4 section 4); cached per camera value."""
        import niagara_view_ub
        cam=self.di_state.camera
        if getattr(self,'_view_key',object())!=cam:
            vb=niagara_view_ub.ViewBuffer(self.inputs.scene_view_header or niagara_view_ub.DEFAULT_HEADER);niagara_view_ub.camera_view(vb,cam)
            self._view_key=cam;self._view_words=vb.words.tobytes()
        return self._view_words
    def gpu_scene(self):
        """SceneTexturesStruct texels of the synthetic flat ground (BakeInputs.ground_height) seen by the current DI camera; None (empty-scene
        fallbacks) without a ground. Cached per camera value, like the View UB."""
        import niagara_gpu
        if self.inputs.ground_height is None:return None
        cam=self.di_state.camera
        if getattr(self,'_scene_key',object())!=cam:
            self._scene_key=cam;self._scene=niagara_gpu.flat_ground_scene(cam,self.inputs.ground_height,self.inputs.ground_base_color)
        return self._scene
    def _emitter_tick_gpu(self,r,dt):
        """Spec N-O4: one spawn+update dispatch of the emitter's own compute shader (WARP); counts and buffers read back."""
        import niagara_gpu
        e=r.model;prog=self.gpu_programs[e.index];r.orig=r.count
        r.spawn_total=sum(max(v[0],0) for v in r.spawn_infos) if r.state==0 else 0;required=r.orig+r.spawn_total
        estimate=e.pre_allocation_count if e.allocation_mode in ['ManualEstimate','FixedCount'] else r.estimate;r.alloc=max(estimate,required)
        if r.alloc>r.max_alloc:r.max_alloc=r.alloc;r.estimate=r.max_alloc
        params=self.emitter_params[e.index][self.cur];params.update(EmitterNumParticles=r.count,EmitterTotalSpawnedParticles=r.total,EmitterAge=r.age,EmitterRandomSeed=r.random_seed,EmitterInstanceSeed=r.instance_seed)
        i=e.index;blocks={'Global':(pack_global(self.global_params[self.cur]),pack_global(self.global_params[self.prev])),'System':(pack_system(self.system_params[self.cur]),pack_system(self.system_params[self.prev])),
                          'Owner':(pack_owner(self.owner_params[self.cur]),pack_owner(self.owner_params[self.prev])),'Emitter':(pack_emitter(self.emitter_params[i][self.cur]),pack_emitter(self.emitter_params[i][self.prev]))}
        lay=e.particle_layout;store=r.stores['gpu']
        ub={'FNiagaraExternalParameters':bytes(store.data[:store.size]),'PREV_FNiagaraExternalParameters':bytes(store.data[store.size:2*store.size]) if store.interpolated else bytes(store.data[:store.size]),
            'View':self.gpu_view_words()}
        try:count,spawned,fcols,icols=niagara_gpu.dispatch_tick(prog,lay,{j:r.buffer['float',j][:r.count] for j in range(lay.total_float)},{j:r.buffer['int32',j][:r.count] for j in range(lay.total_int)},
                                                                r.count,r.alloc,r.spawn_infos,r.state==0,r.gpu_reset,self.global_tick,blocks,r.gpu_ids,ub,self._gpu_read_source,
                                                                self.gpu_scene() if prog.scene_resources else None)
        except niagara_gpu.GpuError as exc:raise BakeError(str(exc)) from exc
        r.gpu_reset=False;r.gpu_tag=self.global_tick;r.spawned=spawned;r.total=word_int((r.total+spawned)&0xffffffff);r.killed_update=r.orig+spawned-count;r.count=count
        r.buffer={**{('float',j):fcols[j] for j in range(lay.total_float)},**{('int32',j):icols[j] for j in range(lay.total_int)}}
    def _gpu_read_source(self,name):
        """GPU ParticleRead source by emitter name: its current data (ticked earlier this frame by execution order) or None."""
        r=next((x for x in self.emitters if name and x.model.enabled and name in (x.model.unique_name,x.model.handle_name)),None)
        if r is None:return None
        e=r.model;gpu=getattr(e,'gpu',None) is not None
        return SimpleNamespace(gpu=gpu,local_space=e.local_space,count=r.count,layout=e.particle_layout,buffer=r.buffer,ids=getattr(r,'gpu_ids',None),
                               spawned=r.spawned,acquire_tag=getattr(r,'gpu_tag',0))
    def _tables(self,r,script,store):
        i=r.model.index;tables=[pack_global(self.global_params[self.cur]),pack_system(self.system_params[self.cur]),pack_owner(self.owner_params[self.cur]),pack_emitter(self.emitter_params[i][self.cur]),bytes(store.data[:store.size])]
        if store.interpolated:tables += [pack_global(self.global_params[self.prev]),pack_system(self.system_params[self.prev]),pack_owner(self.owner_params[self.prev]),pack_emitter(self.emitter_params[i][self.prev]),bytes(store.data[store.size:2*store.size])]
        return tables+[script.literals]
    def _ids(self,r):
        return {0:IdState(list(r.id_to_index),list(r.free),len(r.free),len(r.free)+r.alloc+64,r.max_used,0,[],r.tick_count)} if r.model.requires_persistent_ids else None
    def _read_ids(self,r,result):
        if r.model.requires_persistent_ids:
            s=result.id_states[0];r.id_to_index=s.id_table;r.free=s.free_ids;r.max_used=s.max_used_id
    def _clear(self,r):
        r.count=0;r.buffer=_columns(r.model.particle_layout) if r.model.enabled else {};r.id_to_index=[];r.free=[];r.max_used=-1
        if r.model.enabled:
            for g in r.model.spawn_generators+r.model.update_generators:
                self.events[r.model.id_name,g.event_id]=SimpleNamespace(layout=g.layout,columns=_columns(g.layout),count=0)
    def _particle_result(self,r,result,start,count,base):
        out={k:v.copy() for k,v in base.items()}
        for k in base:
            out[k][start:start+count]=result.outputs[0,k][start:start+count]
            if not np.all(result.written[0,k][start:start+count]):self.diagnostics.append('particle_unwritten')
        return out
    def _generator_sets(self,r,generators,capacity):
        ds={}
        for j,g in enumerate(generators):
            ev=self.events[r.model.id_name,g.event_id];cap=capacity(g);ds[j+1]=NpDataset(ev.columns,{k:cap for k in ev.columns},instance_offset=ev.count)
        return ds
    def _read_generators(self,r,generators,ds,result):
        for j,g in enumerate(generators):
            ev=self.events[r.model.id_name,g.event_id];d=j+1;offset=ds[d].instance_offset;count=offset+result.access_indices[d]+1;columns=_extend(ev.columns,max(count,len(next(iter(result.outputs.values()),[]))))
            for k in ev.columns:
                arr=result.outputs[d,k];columns[k]=_extend({k:ev.columns[k]},len(arr))[k];mask=result.written[d,k];columns[k][mask]=arr[mask];columns[k]=columns[k][:count]
            ev.columns=columns;ev.count=count
    def _emitter_tick(self,r,dt):
        e=r.model
        if r.state==3:self._clear(r);return
        if r.state==4:return
        r.age=f32(r.age+dt)
        if r.state==2:self._clear(r);r.state=1;return
        if r.count==0 and r.state!=0:self._clear(r);return
        if getattr(e,'gpu',None) is not None:return self._emitter_tick_gpu(r,dt)
        counts=[]
        for h in e.event_handlers:
            if h.dead:counts.append([]);continue
            if h.random_spawn_number:raise BakeError('random_spawn_number')
            source=next(x for x in self.model.emitters if x.handle_id==h.source_emitter_id);ev=self.events.get((source.id_name,h.source_event_name));counts.append(event_spawn_counts(ev.count if ev else 0,h.max_events_per_frame,h.spawn_number,r.state==0))
        r.spawn_total=sum(max(v[0],0) for v in r.spawn_infos) if r.state==0 else 0;r.orig=r.count;r.event_requested=sum(sum(v) for v in counts);required=r.orig+r.spawn_total+r.event_requested;estimate=e.pre_allocation_count if e.allocation_mode in ['ManualEstimate','FixedCount'] else r.estimate;r.alloc=max(estimate,required)
        if r.alloc>r.max_alloc:r.max_alloc=r.alloc;r.estimate=r.max_alloc
        if r.alloc>1_000_000:r.estimate=0;r.alloc=r.orig;r.spawn_total=0;r.event_requested=0;counts=[[] for _ in counts]
        params=self.emitter_params[e.index][self.cur];params.update(EmitterTotalSpawnedParticles=r.total,EmitterAge=r.age,EmitterRandomSeed=r.random_seed,EmitterInstanceSeed=r.instance_seed)
        if e.requires_persistent_ids:r.id_to_index,r.free,r.max_used=allocate_ids(r.id_to_index,r.free,r.max_used,r.alloc)
        for g in e.spawn_generators+e.update_generators:self.events[e.id_name,g.event_id]=SimpleNamespace(layout=g.layout,columns=_columns(g.layout),count=0)
        old={k:v.copy() for k,v in r.buffer.items()};buffer=_extend(old,r.alloc)
        if r.orig>0:
            store=r.stores['update'];set_named(store,'Engine.ExecutionCount',[r.orig]);ds={0:NpDataset(old,{k:r.orig for k in old})};ds.update(self._generator_sets(r,e.update_generators,lambda g:r.orig+r.spawn_total+r.event_requested if g.shared else r.orig));result=self._vm(e.update,r.orig,ds,self._tables(r,e.update,store),e.index,'ParticleUpdate',ids=self._ids(r));r.count=result.access_indices[0]+1;r.killed_update=r.orig-r.count;buffer=self._particle_result(r,result,0,r.count,buffer);self._read_ids(r,result);self._read_generators(r,e.update_generators,ds,result)
        remaining=r.alloc-r.orig;batch=0
        def spawn(c,interval,start_dt,group):
            nonlocal buffer,batch
            store=r.stores['spawn'];set_named(store,e.names['spawn_interval'],[float_word(interval)]);set_named(store,e.names['interp_spawn_start_dt'],[float_word(start_dt)]);set_named(store,e.names['spawn_group'],[group]);set_named(store,'Engine.ExecutionCount',[c]);params['EmitterTotalSpawnedParticles']=r.total;r.total=word_int((r.total+c)&0xffffffff);start=r.count;ds={0:NpDataset(_extend(old,r.alloc),{k:r.alloc for k in old},instance_offset=start)};ds.update(self._generator_sets(r,e.spawn_generators,lambda g:r.alloc if g.shared else r.spawn_total+r.event_requested));result=self._vm(e.spawn,c,ds,self._tables(r,e.spawn,store),e.index,'ParticleSpawn',batch=batch,ids=self._ids(r));batch+=1;actual=result.access_indices[0]+1;buffer=self._particle_result(r,result,start,actual,buffer);r.count+=actual;r.killed_spawn+=c-actual;self._read_ids(r,result);self._read_generators(r,e.spawn_generators,ds,result);return actual
        if r.spawn_total+r.event_requested>0:
            batches=plan_spawn_batches(r.spawn_infos,remaining,r.state==0)
            for c,interval,start_dt,group in batches:r.spawned+=spawn(c,interval,start_dt,group)
            # Remaining follows the planner even for non-positive Count entries.
            for count,_,_,_ in r.spawn_infos:remaining-=min(count,remaining)
            event_start=r.count
            for j,per_event in enumerate(counts):
                for k,n in enumerate(per_event):
                    c=min(n,remaining);actual=spawn(c,0,f32(dt*.5),0) if c>0 else 0;counts[j][k]=actual;remaining-=actual;r.event_spawned+=actual
            if sum(sum(v) for v in counts)>0:
                source_buffer={k:v.copy() for k,v in buffer.items()};dest={k:v.copy() for k,v in buffer.items()};r.count=event_start
                for j,h in enumerate(e.event_handlers):
                    if h.dead:continue
                    source=next(x for x in self.model.emitters if x.handle_id==h.source_emitter_id);ev=self.events[source.id_name,h.source_event_name]
                    for k,n in enumerate(counts[j]):
                        if n<=0:continue
                        store=r.stores['event'+str(j)];set_named(store,'Engine.ExecutionCount',[n]);ds={0:NpDataset(source_buffer,{key:r.alloc for key in source_buffer},instance_offset=event_start),1:NpDataset(ev.columns,{},instance_offset=k)};result=self._vm(h.script,n,ds,self._tables(r,h.script,store),e.index,'ParticleEvent',event=j,batch=k,ids=self._ids(r));actual=result.access_indices[0]+1;dest=self._particle_result(r,result,event_start,actual,dest);r.count=event_start+actual;event_start=r.count;self._read_ids(r,result)
                buffer=dest
        r.buffer={k:v[:r.count].copy() for k,v in buffer.items()}
        for store in r.stores.values():prev_copy(store)
    def _tick(self,dt_in,loc,phase,rot=IDENTITY_QUAT):
        a=self.inputs;self.di_state.camera=a.camera_list[self.normal_tick-1] if (phase=='normal' and a.camera_list is not None) else a.camera
        self.di_state.pre_tick();record=self._tick_body(dt_in,loc,phase,rot);self.di_state.post_tick();return record
    def _tick_body(self,dt_in,loc,phase,rot=IDENTITY_QUAT):
        self.global_tick+=1;self.diagnostics=[]
        for r in self.emitters:r.spawned=r.killed_update=r.killed_spawn=r.event_spawned=r.spawn_total=r.event_requested=0;r.orig=r.count
        if self.actual==3:return self._record(phase,dt_in,None)
        dt_in32,dt,fraction=delta_step(dt_in,self.inputs.max_delta_time)
        velocity=(0,0,0) if abs(self.age)<=1e-8 and not self.model.initial_owner_velocity_from_actor else tuple((x-y)/dt for x,y in zip(loc,self.prev_location)) if self.prev_location is not None else self.inputs.owner_actor_velocity;self.prev_location=loc
        self.cur_frame,self.params_valid,self.cur,self.prev=flip_indices(self.cur_frame,self.params_valid);sys=self.system_params[self.cur];sys.update(EngineSystemAge=self.age,EngineTickCount=self.tick_count,EngineTimeSinceRendered=0,EngineExecutionState=self.requested,EngineLodDistance=self.inputs.lod_distance,EngineLodDistanceFraction=f32(f32(self.inputs.lod_distance)/f32(self.inputs.max_lod_distance)),SignificanceIndex=0,RandomSeed=word_int((self.random_seed+self.inputs.random_seed_offset)&0xffffffff),CurrentTimeStep=0,NumTimeSteps=1,TimeStepFraction=fraction,NumParticles=sum(r.count for r in self.emitters if r.state!=4))
        alive=0
        for r in self.emitters:
            p=self.emitter_params[r.model.index][self.cur]
            if r.state!=4:p.update(EmitterNumParticles=r.count,EmitterTotalSpawnedParticles=r.total,EmitterRandomSeed=r.random_seed,EmitterInstanceSeed=r.instance_seed,EmitterSpawnCountScale=1.);alive+=1
            else:p['EmitterNumParticles']=0
        self.age=f32(self.age+dt);self.tick_count+=1;self.owner_params[self.cur]=owner_fields(loc,velocity,rotation=rot);sys.update(EngineEmitterCount=len(self.emitters),EngineAliveEmitterCount=alive)
        if phase=='normal':self.world_time+=dt_in32
        self.global_params[self.cur]=dict(WorldDeltaTime=dt_in32,EngineDeltaTime=dt,EngineInvDeltaTime=f32(1/dt),EngineTime=f32(self.world_time),EngineRealTime=f32(self.world_time),QualityLevel=self.inputs.quality_level)
        for r in self.emitters:
            if not r.model.enabled:continue
            for store in r.stores.values():copy_by_name(self.instance_store.entries,self.instance_store.data,store)
        spawn_row=self._params_row(self.model.spawn_params_layout,'spawn');update_row=self._params_row(self.model.update_params_layout,'update')
        if self.pending:
            self._system('spawn',spawn_row,'SystemSpawn');override=dict(self.global_params[self.cur],EngineDeltaTime=.0001,EngineInvDeltaTime=10000);self._system('update',update_row,'SystemUpdateOnSpawn',override);self.pending=False
        else:self._system('update',update_row,'SystemUpdate')
        previous=self.actual;self.actual=self._row_value('System.ExecutionState')[1][0]
        if self.actual!=previous and previous!=4 and self.actual==0:
            for r in self.emitters:
                if r.state!=4:r.state=0
        if self.actual!=4:
            main_f=[self.main['float',j][0] for j in range(self.model.main_layout.total_float)];main_i=[self.main['int32',j][0] for j in range(self.model.main_layout.total_int)]
            for r in self.emitters:
                if r.state in [3,4]:continue
                value=self._row_value(r.model.names['execution_state']);state=value[1][0] if value is not None else 4;r.state=state if state in range(5) else 1;r.spawn_infos=[]
                for name in r.model.spawn_attributes:
                    f,i=self._row_value(name);r.spawn_infos.append((i[0],f[0],f[1],i[1]))
                for store in r.stores.values():
                    for v in self.model.main_layout.variables:row_to_store(v,main_f,main_i,store)
        ticking=[self.emitters[i] for i in self.model.execution_order if self.emitters[i].state==0 or self.emitters[i].count>0]
        if dt>=1e-8:
            for r in ticking:
                if r.tick_count==0:
                    for store in r.stores.values():prev_copy(store)
                r.tick_count+=1
            for r in ticking:self._emitter_tick(r,dt)
        for r in self.emitters:
            if r.state==3:self._clear(r)
        if self.actual==3 or all(r.state in [3,4] for r in self.emitters):
            self.actual=3
            for r in self.emitters:
                if r.state!=4:r.state=3
                self._clear(r)
        return self._record(phase,dt_in32,dt)
    def _record(self,phase,dt_in,dt):
        record=dict(tick=self.global_tick,phase=phase,dt_in=hexfloat(dt_in),dt=hexfloat(dt) if dt is not None else None,system=dict(age=hexfloat(self.age),tick_count=self.tick_count,requested=self.requested,actual=self.actual),emitters=[dict(index=r.model.index,state=r.state,age=hexfloat(r.age),tick_count=r.tick_count,count=r.count,total_spawned=r.total,spawned=r.spawned,killed_update=r.killed_update,killed_spawn=r.killed_spawn,event_spawned=r.event_spawned,alloc=r.alloc,id_table_len=len(r.id_to_index),num_free=len(r.free)) for r in self.emitters],diagnostics=self.diagnostics);self.records.append(record);return record
    def tick(self):
        if not self.activated:self.activate()
        self.normal_tick+=1;a=self.inputs;t=self.normal_tick;self.requested=1 if a.deactivate_tick is not None and t>=a.deactivate_tick else 0;dt=a.dt_list[t-1] if a.dt_list is not None else a.dt;loc=a.owner_translation_list[t-1] if a.owner_translation_list is not None else a.owner_translation;rot=a.owner_rotation_list[t-1] if a.owner_rotation_list is not None else a.owner_rotation;return self._tick(dt,loc,'normal',rot)
    def run(self):
        if not self.activated:self.activate()
        while self.normal_tick<self.inputs.ticks:self.tick()
        return self.records

def save_bake(scheduler,out_dir):
    s=scheduler;out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    def write(name,v):(out/name).write_text(json.dumps(v,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    write('manifest.json',dict(inputs=asdict(s.inputs),package=s.model.package,model=dict(emitters=len(s.model.emitters),execution_order=s.model.execution_order,unsupported=s.model.unsupported),di_stubbed_functions=s.di_unimplemented,di_events=dict(audio_plays=sum(e['plays'] for e in s.di_state.audio_log),fixed_bounds_calls=len(s.di_state.bounds_log))))
    ticks=''.join(json.dumps(v,sort_keys=True)+'\n' for v in s.records);(out/'ticks.jsonl').write_text(ticks,encoding='utf-8');arrays={f'e{r.model.index}_{"f" if k[0]=="float" else "i"}{k[1]}':v for r in s.emitters if r.model.enabled for k,v in r.buffer.items()};np.savez(out/'final_buffers.npz',**arrays);hashes={'ticks.jsonl':hashlib.sha256(ticks.encode('utf-8')).hexdigest().upper(),'arrays':{k:hashlib.sha256(v.tobytes()).hexdigest().upper() for k,v in arrays.items()}};write('hashes.json',hashes);return hashes

def bake(model,inputs,out_dir):
    s=Scheduler(model,inputs);s.run();return save_bake(s,out_dir)
