"""Niagara playback cache (spec N-O5): renderer binding resolution, per-tick capture and binary PLY streams.

Capture drives Scheduler.tick() from outside and only reads scheduler state, so bakes stay byte-identical (Gate C0).
Positions and vectors are converted to Blender space here (UE (x,y,z) cm -> (x,-y,z) m); see spec N-O5 section 1.
"""
import argparse,hashlib,json,math,struct,sys
from pathlib import Path
import numpy as np
import niagara_ir as ir
import niagara_ribbons
from niagara_sched_model import load_system,type_info,short
from niagara_scheduler import Scheduler,BakeInputs,BakeError,save_bake,quat_axes,IDENTITY_QUAT
from niagara_runtime import word_float,word_int
from niagara_render_ref import read_ply,quat_mul

SCHEMA='dr.niagara-cache/1'
UNIT=0.01
KINDS={'NiagaraSpriteRendererProperties':'sprite','NiagaraMeshRendererProperties':'mesh','NiagaraLightRendererProperties':'light','NiagaraRibbonRendererProperties':'ribbon'}
CAPTURED=('sprite','mesh','light','ribbon')
DYN=[('DynamicMaterialBinding','DynamicMaterialParameter','dyn0'),('DynamicMaterial1Binding','DynamicMaterialParameter1','dyn1'),('DynamicMaterial2Binding','DynamicMaterialParameter2','dyn2'),('DynamicMaterial3Binding','DynamicMaterialParameter3','dyn3')]
# (binding property, default particle attribute, cache prefix, components, conversion, default value or a renderer-property key)
SPRITE=[('PositionBinding','Position','pos',3,'pos',None),('ColorBinding','Color','col',4,'raw',(1,1,1,1)),('VelocityBinding','Velocity','vel',3,'vel',(0,0,0)),
        ('SpriteRotationBinding','SpriteRotation','rot',1,'raw',(0,)),('SpriteSizeBinding','SpriteSize','size',2,'len',(50,50)),
        ('SpriteFacingBinding','SpriteFacing','facing',3,'dir',(1,0,0)),('SpriteAlignmentBinding','SpriteAlignment','align',3,'dir',(1,0,0)),
        ('SubImageIndexBinding','SubImageIndex','subimg',1,'raw',(0,))]+[(b,a,p,4,'raw',(1,1,1,1)) for b,a,p in DYN]+[
        ('CameraOffsetBinding','CameraOffset','camoff',1,'len',(0,)),('UVScaleBinding','UVScale','uvs',2,'raw',(1,1)),('PivotOffsetBinding','PivotOffset','pivot',2,'raw','PivotInUVSpace'),
        ('MaterialRandomBinding','MaterialRandom','matrand',1,'raw',(0,)),('NormalizedAgeBinding','NormalizedAge','nage',1,'raw',(0,)),
        ('RendererVisibilityTagBinding','VisibilityTag','vistag',1,'raw','RendererVisibility'),('CustomSortingBinding','Age','sortkey',1,'raw',(0,))]
MESH=[('PositionBinding','Position','pos',3,'pos',None),('ColorBinding','Color','col',4,'raw',(1,1,1,1)),('VelocityBinding','Velocity','vel',3,'vel',(0,0,0)),
      ('SubImageIndexBinding','SubImageIndex','subimg',1,'raw',(0,))]+[(b,a,p,4,'raw',(1,1,1,1)) for b,a,p in DYN]+[
      ('MeshOrientationBinding','MeshOrientation','quat',4,'quat',(0,0,0,1)),('ScaleBinding','Scale','scale',3,'raw',(1,1,1)),
      ('MaterialRandomBinding','MaterialRandom','matrand',1,'raw',(0,)),('NormalizedAgeBinding','NormalizedAge','nage',1,'raw',(0,)),
      ('CameraOffsetBinding','CameraOffset','camoff',1,'len',(0,)),('RendererVisibilityTagBinding','VisibilityTag','vistag',1,'raw','RendererVisibility'),
      ('MeshIndexBinding','MeshIndex','meshidx',1,'raw',(0,)),('CustomSortingBinding','Age','sortkey',1,'raw',(0,))]
LIGHT=[('PositionBinding','Position','pos',3,'pos',None),('ColorBinding','Color','col',4,'raw',(1,1,1,1)),('RadiusBinding','LightRadius','radius',1,'len',(100,)),
       ('LightExponentBinding','LightExponent','exponent',1,'raw','DefaultExponent'),('LightRenderingEnabledBinding','LightEnabled','enabled',1,'bool',(1,)),
       ('VolumetricScatteringBinding','LightVolumetricScattering','volscat',1,'raw',(0,)),('RendererVisibilityTagBinding','VisibilityTag','vistag',1,'raw','RendererVisibility'),
       ('SpecularScaleBinding','LightSpecularScale','spec',1,'raw','SpecularScale'),('DiffuseScaleBinding','LightDiffuseScale','diff',1,'raw','DiffuseScale')]
RIBBON=[('PositionBinding','Position','pos',3,'pos',None),('ColorBinding','Color','col',4,'raw',(1,1,1,1)),('VelocityBinding','Velocity','vel',3,'vel',(0,0,0)),
        ('NormalizedAgeBinding','NormalizedAge','nage',1,'raw',(0,)),('RibbonTwistBinding','RibbonTwist','twist',1,'raw',(0,)),('RibbonWidthBinding','RibbonWidth','width',1,'len',(1,)),
        ('RibbonFacingBinding','RibbonFacing','facing',3,'dir',(0,0,1)),('RibbonIdBinding','RibbonID','rid',2,'raw',(0,0)),('RibbonLinkOrderBinding','RibbonLinkOrder','linkorder',1,'raw',(0,)),
        ('MaterialRandomBinding','MaterialRandom','matrand',1,'raw',(0,))]+[(b,a,p,4,'raw',(1,1,1,1)) for b,a,p in DYN]+[
        ('RibbonUVDistance','RibbonUVDistance','uvdist',1,'len',(0,)),('U0OverrideBinding','RibbonU0Override','u0ovr',1,'raw',(0,)),('V0RangeOverrideBinding','RibbonV0RangeOverride','v0rng',2,'raw',(0,1)),
        ('U1OverrideBinding','RibbonU1Override','u1ovr',1,'raw',(0,)),('V1RangeOverrideBinding','RibbonV1RangeOverride','v1rng',2,'raw',(0,1))]
TABLES={'sprite':SPRITE,'mesh':MESH,'light':LIGHT,'ribbon':RIBBON}
DEFAULTS={'sprite':{'Alignment':'Automatic','FacingMode':'Automatic','SortMode':'None','PivotInUVSpace':(0.5,0.5),'SubImageSize':(1.0,1.0),'bSubImageBlend':True,
                    'RendererVisibility':0,'SourceMode':'Particles','MinFacingCameraBlendDistance':0.0,'MaxFacingCameraBlendDistance':0.0,'bRemoveHMDRollInVR':False},
          'mesh':{'FacingMode':'Default','bLockedAxisEnable':False,'LockedAxis':(0.0,0.0,1.0),'LockedAxisSpace':'Simulation','SortMode':'None','RendererVisibility':0,
                  'SourceMode':'Particles','bOverrideMaterials':False},
          'light':{'bUseInverseSquaredFalloff':True,'bAffectsTranslucency':False,'bAlphaScalesBrightness':False,'RadiusScale':1.0,'DefaultExponent':1.0,
                   'ColorAdd':(0.0,0.0,0.0),'SpecularScale':1.0,'DiffuseScale':1.0,'RendererVisibility':0,'SourceMode':'Particles'},
          'ribbon':{'FacingMode':'Screen','Shape':'Plane','WidthSegmentationCount':1,'MultiPlaneCount':2,'TubeSubdivisions':3,'bEnableAccurateGeometry':False,
                    'bUseGeometryNormals':True,'TessellationMode':'Automatic','CurveTension':0.0,'TessellationFactor':16,'TessellationAngle':15.0,'bUseConstantFactor':False,
                    'bScreenSpaceTessellation':True,'bLinkOrderUseUniqueID':True,'SourceMode':'Particles','DrawDirection':'BackToFront'}}
UV_KEYS=('DistributionMode','LeadingEdgeMode','TrailingEdgeMode','bEnablePerParticleUOverride','bEnablePerParticleVRangeOverride','TilingLength')

class CacheError(ValueError):pass

def props_of(kind):return ['x','y','z','nx_valid']+[f'{p}_{c}' if n>1 else p for _,_,p,n,_,_ in TABLES[kind] if p!='pos' for c in range(n)]+(['uid'] if kind in ('light','ribbon') else [])
def to_b(v):return (float(v[0]),-float(v[1]),float(v[2]))
def rotator_quat(r):
    p,y,ro=(math.radians(float(r.get(k,0.0)))*0.5 for k in ('Pitch','Yaw','Roll'));sp,cp,sy,cy,sr,cr=math.sin(p),math.cos(p),math.sin(y),math.cos(y),math.sin(ro),math.cos(ro)
    return (cr*sp*sy-sr*cp*cy,-cr*sp*cy-sr*cp*sy,cr*cp*sy-sr*sp*cy,cr*cp*cy+sr*sp*sy)
def quat_b(q):return (-float(q[0]),float(q[1]),-float(q[2]),float(q[3]))
def _vec(v,keys):return tuple(float(v.get(k,0.0)) for k in keys) if isinstance(v,dict) else v
def _ref_index(ref):return int(ref['ObjectPath'].rsplit('.',1)[1])
DEFAULT_NAME=object()
def binding_name(value):
    """Cooked binding structs are delta-serialized: a struct without a variable name keeps the class-default binding."""
    if not isinstance(value,dict):return DEFAULT_NAME
    for key in ('ParamMapVariable','BoundVariable','ResolvedParameter'):
        name=(value.get(key) or {}).get('Name')
        if name:return None if name=='None' else name
    return DEFAULT_NAME

def renderer_records(doc,model):
    """Renderer exports per enabled CPU emitter, from the scheduler-selected emitter version, in RendererProperties order."""
    system=next(e for e in doc if e.get('Type')=='NiagaraSystem');handles=system['Properties'].get('EmitterHandles',[]);out=[]
    for e in model.emitters:
        if not e.enabled or e.sim_target not in ('CPUSim','GPUComputeSim'):continue
        em=doc[_ref_index(handles[e.index]['VersionedInstance']['Emitter'])]['Properties'];selected,_=ir.select_emitter_version(em)
        for k,ref in enumerate(em['VersionData'][selected].get('RendererProperties') or []):
            if isinstance(ref,dict) and ref.get('ObjectPath'):
                x=doc[_ref_index(ref)];out.append((e,k,_ref_index(ref),x.get('Type',''),x.get('Properties') or {}))
    return out

def resolve_renderer(e,k,export_index,typ,p):
    kind=KINDS.get(typ,'other');info={'emitter':e.index,'ordinal':k,'export_index':export_index,'type':typ,'kind':kind,'enabled':p.get('bIsEnabled',True)}
    if kind not in CAPTURED:return info
    d=dict(DEFAULTS[kind])
    for key in d:
        if key in p:
            v=p[key];d[key]=short(v) if isinstance(v,str) and '::' in v else _vec(v,('X','Y','Z')[:len(d[key])]) if isinstance(d[key],tuple) else v
    info['props']=d;mode=d['SourceMode'];info['source_mode']=mode;bindings={};notes=[]
    for bkey,attr,prefix,n,conv,default in TABLES[kind]:
        name=binding_name(p.get(bkey))
        if name is DEFAULT_NAME:name='Particles.'+attr if mode=='Particles' else e.unique_name+'.'+attr
        if name and name.startswith('Emitter.'):name=e.unique_name+name[len('Emitter'):]
        bindings[prefix]={'binding':bkey,'name':name,'components':n,'conversion':conv,'default':list(d[default]) if isinstance(default,str) and isinstance(d[default],tuple) else [d[default]] if isinstance(default,str) else list(default) if default else None,
                          'exists_on_source_flag':(p.get(bkey) or {}).get('bBindingExistsOnSource') if isinstance(p.get(bkey),dict) else None}
    info['bindings']=bindings
    if kind=='ribbon':
        for key in ('UV0Settings','UV1Settings'):
            u=p.get(key) or {};uv={k:(short(u[k]) if isinstance(u[k],str) and '::' in u[k] else u[k]) for k in UV_KEYS if k in u}
            if isinstance(u.get('Offset'),dict):uv['Offset']=[float(u['Offset'].get('X',0.0)),float(u['Offset'].get('Y',0.0))]
            if isinstance(u.get('Scale'),dict):uv['Scale']=[float(u['Scale'].get('X',1.0)),float(u['Scale'].get('Y',1.0))]
            d[key]=uv
        mat=p.get('Material');info['material']=mat.get('ObjectPath') if isinstance(mat,dict) else None
    if kind=='sprite':
        mat=p.get('Material');info['material']=mat.get('ObjectPath') if isinstance(mat,dict) else None
        info['cutout_vertices']=bool(((p.get('DerivedData') or {}).get('BoundingGeometry')))
    if kind=='mesh':
        meshes=[]
        for m in p.get('Meshes') or []:
            ref=m.get('Mesh');meshes.append({'object_path':ref.get('ObjectPath') if isinstance(ref,dict) else None,'scale':list(_vec(m.get('Scale',{'X':1,'Y':1,'Z':1}),('X','Y','Z'))),
                'rotation_quat_ue':list(rotator_quat(m.get('Rotation') or {})),'pivot_offset':list(_vec(m.get('PivotOffset',{}),('X','Y','Z'))),'pivot_space':short(m.get('PivotOffsetSpace','Mesh'))})
        info['meshes']=meshes;info['override_materials']=[(x.get('ExplicitMat') or {}).get('ObjectPath') if isinstance(x,dict) else None for x in p.get('OverrideMaterials') or []]
    info['notes']=notes;return info

class Resolver:
    """Resolves one renderer's bindings against a live scheduler emitter (spec N-O5 section 2.2)."""
    def __init__(self,info,model,emitter):
        self.info=info;self.e=emitter;self.layout=emitter.particle_layout;self.source={}
        for prefix,b in info['bindings'].items():
            name=b['name'];n=b['components']
            if name is None:self.source[prefix]=('default',None);continue
            if info['source_mode']=='Particles' and name.startswith('Particles.'):
                v=self.layout.find(name[len('Particles.'):])
                if v is None:self.source[prefix]=('default',None);continue
                nf,ni=len(v.type.float_offsets),len(v.type.int_offsets)
                if nf==n and ni==0:self.source[prefix]=('float',v)
                elif nf==0 and ni==n or (prefix=='rid' and nf==0 and ni==1):self.source[prefix]=('int',v)  # RibbonID: FNiagaraID (2 ints) or simple int
                else:self.source[prefix]=('type_mismatch',None)
            else:
                stores=[s.param_store for s in (emitter.update,emitter.spawn) if s is not None]
                self.source[prefix]=('param',name) if model.main_layout.find(name) is not None or any(name in s.entries for s in stores) else ('param_missing',None)
        b=info['bindings'];self.resolved={p:(s[0] in ('float','int','param')) for p,s in self.source.items()}
        if info['kind']=='sprite':
            pr=info['props']
            pr['Alignment_resolved']=pr['Alignment'] if pr['Alignment']!='Automatic' else ('CustomAlignment' if self.resolved['align'] else 'Unaligned')
            pr['FacingMode_resolved']=pr['FacingMode'] if pr['FacingMode']!='Automatic' else ('CustomFacingVector' if self.resolved['facing'] else 'FaceCamera')
        if info['kind']=='mesh':info['mesh_index_bound']=self.resolved['meshidx']
        if info['kind']=='ribbon':
            pr=info['props'];uid=self.layout.find('UniqueID')
            pr['twist_bound']=self.resolved['twist'];pr['ribbon_id_bound']=self.resolved['rid'];pr['uvdist_bound']=self.resolved['uvdist']
            for k in (0,1):pr['u%d_override_bound'%k]=self.resolved['u%dovr'%k];pr['v%d_range_bound'%k]=self.resolved['v%drng'%k]
            pr['link_order_source']='RibbonLinkOrder' if self.source['linkorder'][0]=='float' else 'UniqueID' if pr['bLinkOrderUseUniqueID'] and uid is not None and len(uid.type.int_offsets)==1 else 'NormalizedAge'
        info['flag_disagreements']=sorted(p for p,b in info['bindings'].items() if bool(b['exists_on_source_flag'])!=self.resolved[p])
        info['binding_sources']={p:(s[0] if s[0]!='float' and s[0]!='int' else 'particle:'+s[1].name) if s[0]!='param' else 'param:'+s[1] for p,s in self.source.items()}
    def param_value(self,sched,r,name,n):
        row=sched._row_value(name)
        if row is not None:f,i=row;vals=f if f else [float(x) for x in i];return vals[:n] if len(vals)>=n else None
        for key in ('update','spawn'):
            store=r.stores.get(key)
            if store is None or name not in store.entries:continue
            offset,t=store.entries[name];words=[struct.unpack_from('<I',store.data,offset+b)[0] for b in sorted(t.float_offsets+t.int_offsets)]
            vals=[word_float(w) for w in words] if t.float_offsets else [float(word_int(w)) for w in words];return vals[:n] if len(vals)>=n else None
        return None
    def column(self,sched,r,prefix,count):
        """float64 array [count, n] in UE units for one binding (defaults filled)."""
        b=self.info['bindings'][prefix];n=b['components'];kind,src=self.source[prefix]
        if kind=='float':return np.stack([r.buffer['float',src.float_start+j][:count].view(np.float32).astype(np.float64) for j in range(n)],axis=1)
        if kind=='int':
            cols=[r.buffer['int32',src.int_start+j][:count].view(np.int32).astype(np.float64) for j in range(len(src.type.int_offsets))]
            return np.stack(cols+[np.zeros(count)]*(n-len(cols)),axis=1)
        if kind=='param':
            vals=self.param_value(sched,r,src,n)
            if vals is not None:return np.tile(np.asarray(vals,dtype=np.float64),(count,1))
        if b['default'] is None:return None
        return np.tile(np.asarray(b['default'],dtype=np.float64),(count,1))

def convert(conv,values,origin_ue,owner_ue,local_space,owner_rot=None):
    """UE -> Blender for one binding; local-space emitters are moved into world space by the owner transform (rotation, then translation)."""
    v=values.copy();rot=owner_rot if (local_space and owner_rot is not None and tuple(owner_rot)!=IDENTITY_QUAT) else None
    axes=np.array(quat_axes(rot)) if rot is not None and conv in ('pos','vel','dir') else None
    if conv=='pos':
        if local_space:v=(v@axes if axes is not None else v)+np.asarray(owner_ue)
        v=(v-np.asarray(origin_ue))*UNIT;v[:,1]*=-1
    elif conv=='vel':
        if axes is not None:v=v@axes
        v=v*UNIT;v[:,1]*=-1
    elif conv=='dir':
        if axes is not None:v=v@axes
        v[:,1]*=-1
    elif conv=='len':v=v*UNIT
    elif conv=='quat':
        if rot is not None:v=quat_mul(np.asarray(rot,dtype=np.float64)[None,:],v)  # owner * particle: particle rotation first
        v[:,0]*=-1;v[:,2]*=-1
    elif conv=='bool':v=(v!=0).astype(np.float64)
    return v

def frame_rows(sched,r,res,origin_ue,owner_ue,owner_rot=None):
    """Rows [N, len(props)] float32 for one renderer stream at the current tick, or None for an empty frame."""
    info=res.info;kind=info['kind'];emitter_mode=info['source_mode']=='Emitter'
    if emitter_mode:count=1 if r.state in (0,1) else 0
    else:count=r.count if r.state!=4 else 0
    if count==0:return None
    cols={}
    for bkey,attr,prefix,n,conv,default in TABLES[kind]:
        c=res.column(sched,r,prefix,count)
        if c is None:c=np.zeros((count,n)) if r.model.local_space else np.tile(np.asarray(owner_ue,dtype=np.float64),(count,1))  # Position default: sim origin
        cols[prefix]=convert(conv,c,origin_ue,owner_ue,r.model.local_space,owner_rot)
    out=[cols['pos'],np.ones((count,1))]+[cols[p] for _,_,p,_,_,_ in TABLES[kind] if p!='pos']
    if kind in ('light','ribbon'):
        v=res.layout.find('UniqueID');uid=r.buffer['int32',v.int_start][:count].view(np.int32).astype(np.float64) if (v is not None and not emitter_mode and len(v.type.int_offsets)==1) else np.arange(count,dtype=np.float64)
        if np.any(np.abs(uid)>2**24):raise CacheError('uid_overflow')
        out.append(uid.reshape(count,1))
    rows=np.concatenate(out,axis=1)
    for prefix in ('meshidx','vistag','rid'):
        if prefix in cols and np.any(np.abs(cols[prefix])>2**24):raise CacheError('int_overflow:'+prefix)
    mode=info['props'].get('SortMode','None')
    if mode in ('CustomAscending','CustomDecending'):
        key=cols['sortkey'][:,0];order=np.argsort(key if mode=='CustomAscending' else -key,kind='stable');rows=rows[order]
    return rows.astype(np.float32)

def ply_bytes(props,rows):
    if rows is None:rows=np.zeros((1,len(props)),dtype=np.float32)
    head=('ply\nformat binary_little_endian 1.0\nelement vertex %d\n'%len(rows)+''.join('property float %s\n'%p for p in props)+'end_header\n').encode('ascii')
    return head+np.ascontiguousarray(rows,dtype='<f4').tobytes()

def stream_name(info):return 'e%d_r%d'%(info['emitter'],info['ordinal'])

def bake_cache(system_path,out_dir,inputs,frame_start=1,fps=60.0,fps_base=1.0,bake_out=None):
    """Bake one system with per-tick capture; writes <out_dir>/cache_manifest.json and one PLY per stream per frame."""
    model=load_system(system_path);doc=json.loads(Path(system_path).read_text(encoding='utf-8-sig'));out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    sched=Scheduler(model,inputs);sched.activate()
    infos=[resolve_renderer(*rec) for rec in renderer_records(doc,model)]
    by_index={r.model.index:r for r in sched.emitters};streams=[]
    for info in infos:
        if info['kind'] in CAPTURED and info['enabled']:
            res=Resolver(info,model,by_index[info['emitter']].model);d=out/stream_name(info);d.mkdir(exist_ok=True);streams.append((info,res,d,props_of(info['kind'])))
    origin=tuple(inputs.owner_translation_list[0]) if inputs.owner_translation_list else tuple(inputs.owner_translation)
    stats={'max_count':{},'empty_frames':{},'frames':inputs.ticks+1,'ribbon_subsegments':{}};smoothing={stream_name(i):niagara_ribbons.Smoothing() for i,_,_,_ in streams if i['kind']=='ribbon'}
    def write_frame(frame,rows_by):
        for info,res,d,props in streams:
            rows=rows_by.get(stream_name(info));(d/('f_%04d.ply'%frame)).write_bytes(ply_bytes(props,rows));key=stream_name(info)
            if info['kind']=='ribbon':
                mesh=niagara_ribbons.generate({p:rows[:,i] for i,p in enumerate(props)},info['props'],smoothing[key]) if rows is not None else None
                if rows is None:smoothing[key].__init__()
                (d/('m_%04d.ply'%frame)).write_bytes(niagara_ribbons.mesh_ply_bytes(mesh));stats['ribbon_subsegments'].setdefault(key,[]).append(0 if mesh is None else int(mesh['nsub']))
            stats['max_count'][key]=max(stats['max_count'].get(key,0),0 if rows is None else len(rows));stats['empty_frames'][key]=stats['empty_frames'].get(key,0)+(rows is None)
    write_frame(frame_start,{})
    while sched.normal_tick<inputs.ticks:
        sched.tick();t=sched.normal_tick;owner=tuple(inputs.owner_translation_list[t-1]) if inputs.owner_translation_list else tuple(inputs.owner_translation)
        rot=tuple(inputs.owner_rotation_list[t-1]) if inputs.owner_rotation_list else tuple(inputs.owner_rotation)
        write_frame(frame_start+t,{stream_name(info):frame_rows(sched,by_index[info['emitter']],res,origin,owner,rot) for info,res,d,props in streams})
    hashes=save_bake(sched,bake_out) if bake_out else None
    manifest={'schema':SCHEMA,'system':model.package.rsplit('/',1)[-1].split('.')[0],'package':model.package,'source_path':str(system_path),'bake_hashes':hashes,
              'inputs':{k:v for k,v in vars(inputs).items() if k not in ('svml_shim_path','vector_fields')},'frame_start':frame_start,'frame_end':frame_start+inputs.ticks,
              'fps':fps,'fps_base':fps_base,'bake_origin_ue':list(origin),'bake_origin_b':[o*UNIT for o in to_b(origin)],
              'emitters':[{'index':e.index,'unique_name':e.unique_name,'local_space':e.local_space,'enabled':e.enabled,'sim_target':e.sim_target} for e in model.emitters],
              'renderers':infos,'streams':[{'name':stream_name(i),'kind':i['kind'],'emitter':i['emitter'],'ordinal':i['ordinal'],'ply_properties':p} for i,_,_,p in streams],'stats':stats}
    (out/'cache_manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=1,default=list)+'\n',encoding='utf-8')
    return manifest

def tree_hashes(root):
    root=Path(root);return {str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest().upper() for p in sorted(root.rglob('*')) if p.is_file()}

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='cmd',required=True)
    b=sub.add_parser('bake');b.add_argument('--system',required=True);b.add_argument('--out',required=True);b.add_argument('--ticks',type=int,default=120)
    b.add_argument('--fps',type=float,default=60.0);b.add_argument('--fps-base',type=float,default=1.0);b.add_argument('--frame-start',type=int,default=1)
    b.add_argument('--shim',required=True);b.add_argument('--vector-fields',required=True);b.add_argument('--bake-out');b.add_argument('--no-dump-params',action='store_true')
    a=ap.parse_args(argv)
    import niagara_dump_params
    inputs=BakeInputs(ticks=a.ticks,dt=a.fps_base/a.fps,di_mode='real',svml_shim_path=a.shim,vector_fields=a.vector_fields,parameter_overrides=None if a.no_dump_params else niagara_dump_params.overrides_for(a.system) or None)
    m=bake_cache(a.system,a.out,inputs,a.frame_start,a.fps,a.fps_base,a.bake_out)
    print(json.dumps({'system':m['system'],'streams':len(m['streams']),'max_count':m['stats']['max_count']},sort_keys=True))

if __name__=='__main__':main()
