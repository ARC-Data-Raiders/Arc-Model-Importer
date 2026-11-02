"""Static cooked Niagara scheduler model (Wave N3)."""
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import argparse
import collections
import json
import re
import niagara_ir as ir
import niagara_vm as vm

class ModelError(ValueError): pass
@dataclass(frozen=True)
class TypeInfo:
    key: str
    is_object: bool
    float_offsets: tuple
    int_offsets: tuple

def type_info(d):
    key=d.get('ClassStructOrEnum',{}).get('ObjectName','')
    if d.get('UnderlyingType')==1:return TypeInfo(key,True,(),())
    if d.get('UnderlyingType')==3:return TypeInfo(key,False,(),(0,))
    floats={'NiagaraFloat':1,'Vector2f':2,'Vector3f':3,'NiagaraPosition':3,'Vector4f':4,'LinearColor':4,'Quat4f':4,'NiagaraMatrix':16}
    for name,n in floats.items():
        if key=="Class'"+name+"'":return TypeInfo(key,False,tuple(range(0,n*4,4)),())
    if key in ["Class'NiagaraInt32'","Class'NiagaraBool'"]:return TypeInfo(key,False,(),(0,))
    if key=="Class'NiagaraID'":return TypeInfo(key,False,(),(0,4))
    if key=="Class'NiagaraSpawnInfo'":return TypeInfo(key,False,(4,8),(0,12))
    raise ModelError('unknown_type:'+key)
@dataclass(frozen=True)
class VarLayout:
    name: str
    type: TypeInfo
    float_start: int
    int_start: int
@dataclass(frozen=True)
class DataSetLayout:
    variables: tuple
    total_float: int
    total_int: int
    def find(self,name):return next((v for v in self.variables if v.name==name),None)
def build_layout(variables):
    f=i=0;out=[]
    for v in variables:
        t=type_info(v['TypeDef'])
        if t.is_object:raise ModelError('object_dataset:'+v['Name'])
        out.append(VarLayout(v['Name'],t,f,i));f+=len(t.float_offsets);i+=len(t.int_offsets)
    return DataSetLayout(tuple(out),f,i)
def compare_cooked_layout(layout,cooked_variable_layouts):
    errors=[]
    if len(layout.variables)!=len(cooked_variable_layouts):errors.append('variable_count')
    for v,c in zip(layout.variables,cooked_variable_layouts):
        info=c.get('LayoutInfo',{})
        for key,actual,expected in [('FloatComponentStart',c.get('FloatComponentStart',0),v.float_start),('Int32ComponentStart',c.get('Int32ComponentStart',0),v.int_start),('NumFloatComponents',info.get('NumFloatComponents',0),len(v.type.float_offsets)),('NumInt32Components',info.get('NumInt32Components',0),len(v.type.int_offsets))]:
            if actual!=expected:errors.append(v.name+':'+key)
        if info.get('NumHalfComponents',0):errors.append('half')
    return errors
@dataclass
class ParamStore:
    size:int
    interpolated:bool
    data:bytearray
    entries:dict
    object_entries:dict
def load_param_store(store_json,interpolated):
    data=bytearray(ir.byte_payload(store_json.get('ParameterData')))
    if 'ParameterSize' in store_json:size=store_json['ParameterSize']
    elif not interpolated:size=len(data)
    else:raise ModelError('param_store_size')
    if len(data)!=size*(2 if interpolated else 1):raise ModelError('param_store_size')
    entries={};objects={}
    for p in store_json.get('SortedParameterOffsets',[]):
        t=type_info(p['TypeDef']);name=p['Name'];offset=p['Offset']
        if t.is_object:objects[name]=(offset,t.key)
        else:entries[name]=(offset,t)
    return ParamStore(size,interpolated,data,entries,objects)
@dataclass
class ScriptModel:
    export_index:int
    name:str
    usage:str
    bytecode:bytes
    literals:bytes
    attributes:tuple
    ext_functions:list
    param_store:ParamStore
    num_user_ptrs:int
    program:object
    has_ext:bool
    reads:dict
    writes:dict
@dataclass
class EventGeneratorModel:
    event_id:str
    layout:DataSetLayout
    shared:bool
@dataclass
class EventHandlerModel:
    script:ScriptModel
    execution_mode:str
    spawn_number:int
    min_spawn_number:int
    random_spawn_number:bool
    max_events_per_frame:int
    source_emitter_id:str
    source_event_name:str
@dataclass
class EmitterModel:
    index:int
    handle_name:str
    handle_id:str
    id_name:str
    unique_name:str
    enabled:bool
    disabled_reason:object
    sim_target:str
    local_space:bool
    determinism:bool
    random_seed:int
    interpolated:bool
    requires_persistent_ids:bool
    allocation_mode:str
    pre_allocation_count:int
    spawn:object
    update:object
    event_handlers:list
    spawn_generators:list
    update_generators:list
    particle_layout:object
    spawn_attributes:tuple
    names:dict
@dataclass
class SystemModel:
    package:str
    path:str
    emitters:list
    system_spawn:object
    system_update:object
    main_layout:object
    spawn_params_layout:object
    update_params_layout:object
    bindings:dict
    instance_params:ParamStore
    determinism:bool
    random_seed:int
    warmup_tick_count:int
    warmup_tick_delta:float
    fixed_tick_delta:object
    initial_owner_velocity_from_actor:bool
    execution_order:list
    quality_level:int
    unsupported:tuple
    notes:tuple

def short(v):return str(v).rsplit('::',1)[-1]
def load_system(path,*,quality_level=3):
    path=Path(path);doc=json.loads(path.read_text(encoding='utf-8-sig'));reasons=[];notes=[];emitters=[];bindings={};empty=build_layout([])
    model=SystemModel(path.stem,str(path),emitters,None,None,empty,empty,empty,bindings,load_param_store({},False),False,0,0,1/15,None,True,[],quality_level,(),())
    def unsupported(reason):
        if reason not in reasons:reasons.append(reason)
    try:
        evidence=ir.extract_document(doc,source_path=str(path));model.package=evidence['source']['packagePath'];systems=evidence['systems']
        if len(systems)!=1:raise ModelError('system_count')
        props=systems[0]['rawExport'].get('Properties',{});records={r['exportIndex']:r for key in ['systems','emitters','scripts','renderers','dataInterfaces','otherExports'] for r in evidence[key]}
        def resolve(ref):
            try:
                r=records[int(ref['ObjectPath'].rsplit('.',1)[1])];name=ref['ObjectName'].split("'",1)[1].rstrip("'");name=re.split('[:.]',name)[-1]
                if r['name']!=name:raise ValueError()
                return r
            except (KeyError,ValueError,TypeError,IndexError):raise ModelError('reference_mismatch')
        def script(ref):
            r=resolve(ref);p=r['rawExport'].get('Properties',{});v=p.get('CachedScriptVM',{});code=vm.bytecode_from_vm(v);ext=v.get('CalledVMExternalFunctions') or [];usage=short(p.get('Usage',''));prog=vm.disassemble(code,external_functions=ext,profile='pioneer-2.1.0') if code else None;reads={};writes={}
            for ins in prog.instructions if prog else []:
                if ins.name.startswith(('inputdata','outputdata')):
                    o=ins.operands;dest=reads if ins.name.startswith('inputdata') else writes;key=(o[0].value,'int32' if ins.name.endswith('int32') else 'float');dest[key]=max(dest.get(key,0),o[1 if dest is reads else 3].value+1)
            return ScriptModel(r['exportIndex'],r['name'],usage,code,ir.byte_payload(v.get('ScriptLiterals')),tuple(v.get('Attributes',[])),ext,load_param_store(p.get('ScriptExecutionParamStore',{}),usage=='ParticleSpawnScriptInterpolated'),v.get('NumUserPtrs',0),prog,any(i.name=='external_func_call' for i in prog.instructions) if prog else False,reads,writes)
        def compiled(data):
            layout=build_layout(data.get('Variables',[]))
            if compare_cooked_layout(layout,data.get('VariableLayouts',[])):unsupported('layout_mismatch')
            return layout
        c=props.get('SystemCompiledData',{});model.main_layout=compiled(c.get('DataSetCompiledData',{}));model.spawn_params_layout=compiled(c.get('SpawnInstanceParamsDataSetCompiledData',{}));model.update_params_layout=compiled(c.get('UpdateInstanceParamsDataSetCompiledData',{}));model.instance_params=load_param_store(c.get('InstanceParamStore',{}),False)
        model.system_spawn=script(props.get('SystemSpawnScript'));model.system_update=script(props.get('SystemUpdateScript'))
        if not model.system_spawn.bytecode or not model.system_update.bytecode:unsupported('system_script_missing_bytecode')
        for i,h in enumerate(props.get('EmitterHandles',[])):
            emitter_ref=(h.get('VersionedInstance') or {}).get('Emitter')
            if not isinstance(emitter_ref,dict):
                reason='handle_disabled' if not h.get('bIsEnabled',True) else 'no_emitter'
                if reason=='no_emitter':notes.append('no_emitter:%d'%i)
                emitters.append(EmitterModel(i,h.get('Name'),h.get('ID'),h.get('IdName'),'',False,reason,'Unknown',False,False,0,False,False,'AutomaticEstimate',0,None,None,[],[],[],None,(),{k:'' for k in ('spawn_interval','interp_spawn_start_dt','spawn_group','execution_state')}));continue
            r=resolve(emitter_ref);p=r['rawExport'].get('Properties',{});selected,_=ir.select_emitter_version(p)
            if selected is None:raise ModelError('emitter_version')
            v=p['VersionData'][selected];platform=v.get('Platforms',{});reason=None
            if not h.get('bIsEnabled',True):reason='handle_disabled'
            elif short(h.get('EmitterMode','Standard'))!='Standard':reason='stateless'
            elif not (platform.get('QualityLevelMask',-1)&(1<<quality_level)):reason='quality'
            if platform.get('CVarConditions') or platform.get('DeviceProfileStates'):unsupported('platform_conditions')
            u=p.get('UniqueEmitterName','');target=short(v.get('SimTarget','CPUSim'));interp=short(v.get('InterpolatedSpawnMode','None'))=='Interpolation'
            e=EmitterModel(i,h.get('Name'),h.get('ID'),h.get('IdName'),u,reason is None,reason,target,v.get('bLocalSpace',False),v.get('bDeterminism',False),v.get('RandomSeed',0),interp,v.get('bRequiresPersistentIDs',False),short(v.get('AllocationMode','AutomaticEstimate')),v.get('PreAllocationCount',0),None,None,[],[],[],None,(),{k:u+'.'+suffix for k,suffix in [('spawn_interval','SpawnInterval'),('interp_spawn_start_dt','InterpSpawnStartDt'),('spawn_group','SpawnGroup'),('execution_state','ExecutionState')]});emitters.append(e)
            def spawn_attributes():
                spawnattrs=[]
                for a in model.system_spawn.attributes+model.system_update.attributes:
                    if type_info(a['TypeDef']).key=="Class'NiagaraSpawnInfo'" and a['Name'].startswith(u+'.') and a['Name'] not in spawnattrs:
                        spawnattrs.append(a['Name']);found=model.main_layout.find(a['Name'])
                        if found is None or found.type.key!="Class'NiagaraSpawnInfo'":unsupported('spawn_attribute_missing')
                return tuple(spawnattrs)
            if not e.enabled:continue
            if target=='GPUComputeSim':
                # spec N-O4: the particle logic is the GPU compute script's shader; CPU system scripts still produce spawn infos
                gr=resolve(v.get('GPUComputeScript'));gvm=gr['rawExport'].get('Properties',{}).get('CachedScriptVM',{})
                gps=gr['rawExport'].get('Properties',{}).get('ScriptExecutionParamStore',{});gdata=ir.byte_payload(gps.get('ParameterData'))
                e.gpu=SimpleNamespace(script_index=gr['exportIndex'],emitter_index=r['exportIndex'],stages=len(gvm.get('SimulationStageMetaData',[])),
                                      param_store=load_param_store(gps,len(gdata)==2*gps.get('ParameterSize',len(gdata)) and len(gdata)>0))
                e.particle_layout=build_layout(gvm.get('Attributes',[]));e.requires_persistent_ids|=e.particle_layout.find('ID') is not None
                for rd in gr['rawExport'].get('Properties',{}).get('ResolvedDataInterfaces') or []:
                    ref=rd.get('ResolvedDataInterface')
                    dx=(records.get(int(ref['ObjectPath'].rsplit('.',1)[1]),{}).get('rawExport') or {}) if isinstance(ref,dict) else {}
                    if dx.get('Type')=='NiagaraDataInterfaceParticleRead':   # GPU ParticleRead: the source dispatches first
                        e.particle_read_sources=getattr(e,'particle_read_sources',set())|{((dx.get('Properties') or {}).get('EmitterBinding') or {}).get('EmitterName','')}
                if v.get('EventHandlerScriptProps'):unsupported('gpu_event_handlers')
                e.spawn_attributes=spawn_attributes()
                if model.main_layout.find(e.names['execution_state']) is None:notes.append('missing_emitter_state:'+str(i))
                continue
            if target!='CPUSim':unsupported('sim_target:'+target);continue
            e.spawn=script(v.get('SpawnScriptProps',{}).get('Script'));e.update=script(v.get('UpdateScriptProps',{}).get('Script'))
            if (e.spawn.usage=='ParticleSpawnScriptInterpolated')!=interp:unsupported('interp_mismatch')
            for hp in v.get('EventHandlerScriptProps',[]):e.event_handlers.append(EventHandlerModel(script(hp.get('Script')),short(hp.get('ExecutionMode','SpawnedParticles')),hp.get('SpawnNumber',0),hp.get('MinSpawnNumber',0),hp.get('bRandomSpawnNumber',False),hp.get('MaxEventsPerFrame',0),hp.get('SourceEmitterID','00000000-00000000-00000000-00000000'),hp.get('SourceEventName','None')))
            attrs=list(e.update.attributes)
            for a in e.spawn.attributes:
                if not any(x['Name']==a['Name'] and type_info(x['TypeDef']).key==type_info(a['TypeDef']).key for x in attrs):attrs.append(a)
            e.particle_layout=build_layout(attrs);e.requires_persistent_ids|=e.particle_layout.find('ID') is not None
            for s in [e.spawn,e.update]+[h.script for h in e.event_handlers]:
                if not s.bytecode:unsupported('cpu_script_missing_bytecode')
                for counts in [s.reads,s.writes]:
                    if counts.get((0,'float'),0)>e.particle_layout.total_float or counts.get((0,'int32'),0)>e.particle_layout.total_int:unsupported('component_out_of_layout')
                vp=records[s.export_index]['rawExport'].get('Properties',{}).get('CachedScriptVM',{})
                for k,info in enumerate(vp.get('DataInterfaceInfo',[])):
                    if 'NiagaraDataInterfaceParticleRead' not in json.dumps(info.get('Type',{})):continue
                    dis=(records[s.export_index]['rawExport'].get('Properties',{}).get('ScriptExecutionParamStore',{}) or {}).get('DataInterfaces') or []
                    if k<len(dis) and isinstance(dis[k],dict):
                        dp=(records.get(int(dis[k]['ObjectPath'].rsplit('.',1)[1]),{}).get('rawExport') or {}).get('Properties',{})
                        e.particle_read_sources=getattr(e,'particle_read_sources',set())|{(dp.get('EmitterBinding') or {}).get('EmitterName','')}
            sg=v.get('SpawnScriptProps',{}).get('EventGenerators',[]);ug=v.get('UpdateScriptProps',{}).get('EventGenerators',[]);shared={g['ID'] for g in sg}&{g['ID'] for g in ug}
            for gs,out in [(sg,e.spawn_generators),(ug,e.update_generators)]:
                for g in gs:out.append(EventGeneratorModel(g['ID'],compiled(g.get('DataSetCompiledData',{})),g['ID'] in shared))
            e.spawn_attributes=spawn_attributes()
            if model.main_layout.find(e.names['execution_state']) is None:notes.append('missing_emitter_state:'+str(i))
        for phase in ['spawn','update']:
            for group in ['global','system','owner','emitter']:
                sources=c.get(phase.title()+'Instance'+group.title()+('Bindings' if group=='emitter' else 'Binding'),[] if group=='emitter' else {})
                for j,b in enumerate(sources if group=='emitter' else [sources]):
                    if b.get('HalfOffsets'):unsupported('half_binding')
                    bindings[(phase,group,j) if group=='emitter' else (phase,group)]=tuple((kind,x['ParameterOffset'],x['DataSetComponentOffset']) for field,kind in [('FloatOffsets','float'),('Int32Offsets','int32')] for x in b.get(field,[]))
        if model.main_layout.find('System.ExecutionState') is None:unsupported('missing_system_state')
        dependencies={e.index:[] for e in emitters}
        for e in emitters:
            for name in getattr(e,'particle_read_sources',()):
                src=next((x for x in emitters if name and (x.unique_name==name or x.handle_name==name)),None)
                if src is not None and src.index!=e.index:dependencies[e.index].append(src.index)   # ParticleRead: reader ticks after its source
            for h in e.event_handlers:
                source=next((x for x in emitters if x.handle_id==h.source_emitter_id),None)
                h.dead=source is None or not any(g.event_id==h.source_event_name for g in source.spawn_generators+source.update_generators)
                if h.dead:notes.append('dead_event_handler');continue
                if e.requires_persistent_ids:unsupported('event_receiver_with_ids')
                if h.execution_mode!='SpawnedParticles':unsupported('event_mode:'+h.execution_mode)
                dependencies[e.index].append(source.index)
        priorities={};visiting=set()
        def priority(i):
            if i in visiting:raise ModelError('execution_order_cycle')
            if i not in priorities:
                visiting.add(i);priorities[i]=max((priority(d)+1 for d in dependencies[i]),default=0);visiting.remove(i)
            return priorities[i]
        model.execution_order=sorted(range(len(emitters)),key=priority)
        model.determinism=props.get('bDeterminism',False);model.random_seed=props.get('RandomSeed',0);model.warmup_tick_count=props.get('WarmupTickCount',0);model.warmup_tick_delta=props.get('WarmupTickDelta',1/15);model.initial_owner_velocity_from_actor=props.get('bInitialOwnerVelocityFromActor',True)
        if props.get('bFixedTickDelta',False):model.fixed_tick_delta=props.get('FixedTickDeltaTime',.01667);unsupported('fixed_tick')
    except (ModelError,vm.DecodeError,KeyError,TypeError,ValueError,IndexError) as exc:unsupported(str(exc))
    model.unsupported=tuple(reasons);model.notes=tuple(notes);return model

def census(corpus,out,quality):
    rows=[]
    for path in sorted(Path(corpus).rglob('NS_*.json')):
        m=load_system(path,quality_level=quality);enabled=[e for e in m.emitters if e.enabled];scripts=[s for s in [m.system_spawn,m.system_update] if s]+[s for e in enabled for s in [e.spawn,e.update]+[h.script for h in e.event_handlers if not getattr(h,'dead',False)] if s]
        allcpu=all(e.sim_target=='CPUSim' for e in enabled);eligible=allcpu and not m.unsupported;noext=eligible and not any(s.has_ext for s in scripts)
        rows.append(dict(package=m.package,path=m.path,unsupported=m.unsupported,notes=m.notes,emitter_count=len(m.emitters),enabled_cpu=sum(e.sim_target=='CPUSim' for e in enabled),enabled_gpu=sum(e.sim_target!='CPUSim' for e in enabled),has_ext=[{'export_index':s.export_index,'has_ext':s.has_ext} for s in scripts],all_enabled_cpu=allcpu,noext=noext,live_event_pairs=[(h.source_emitter_id,e.handle_id) for e in enabled for h in e.event_handlers if not getattr(h,'dead',False)],warmup_ticks=m.warmup_tick_count,requires_persistent_ids=sum(e.requires_persistent_ids for e in enabled)))
    data=dict(systems=rows,totals=dict(systems=len(rows),reasons=dict(collections.Counter(r for row in rows for r in row['unsupported'])),s0_universe=sum(r['all_enabled_cpu'] and not r['unsupported'] for r in rows),noext=sum(r['noext'] for r in rows)))
    Path(out).parent.mkdir(parents=True,exist_ok=True);Path(out).write_text(json.dumps(data,sort_keys=True,indent=2)+'\n',encoding='utf-8');print(json.dumps(data['totals'],sort_keys=True))
if __name__=='__main__':
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True);c=s.add_parser('census');c.add_argument('--corpus',required=True);c.add_argument('--out',required=True);c.add_argument('--quality',type=int,required=True);a=p.parse_args();census(a.corpus,a.out,a.quality)
