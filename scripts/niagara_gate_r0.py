"""Gate R0 (spec N-O5 section 11): Blender GN rigs vs niagara_render_ref on synthetic and corpus caches; SubUV render; smoke renders.

Subcommands: synth, blender, compare, subuv-check, report. Blender runs headless; every launch must exit 0 and print R0_BLENDER_DONE.
"""
import argparse,json,os,pathlib,subprocess,sys,time
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent))
import Niagara.niagara as nc
import Niagara.niagara as ref
import Niagara.niagara as rb

REPO=pathlib.Path(__file__).resolve().parent.parent
CAMERAS=[{'location':[2.5,-3.5,1.8],'look_at':[0.3,-0.2,0.1],'type':'PERSP','lens':35.0},{'location':[0.4,-6.0,0.2],'rotation_deg':[90.0,0.0,8.0],'type':'ORTHO','ortho_scale':6.0}]
ORIGIN_UE=(30.0,20.0,10.0)
SUBUV_CAMERA={'location':[0.0,-10.0,0.0],'rotation_deg':[90.0,0.0,0.0],'type':'ORTHO','ortho_scale':8.0}
SUBUV_RES=(320,80)
SUBUV_INDEX=[0.0,1.0,5.0,6.7,10.25,15.0,3.5,12.9]

def write(p,v):
    p=pathlib.Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,sort_keys=True,indent=2,default=float)+'\n',encoding='utf-8')
def read(p):return json.loads(pathlib.Path(p).read_text(encoding='utf-8-sig'))

def synth_info(kind,ordinal,over,meshes=None,mib=False):
    props={k:(list(v) if isinstance(v,tuple) else v) for k,v in nc.DEFAULTS[kind].items()};props.update(over)
    if kind=='sprite':
        props['Alignment_resolved']=props['Alignment'] if props['Alignment']!='Automatic' else 'Unaligned'
        props['FacingMode_resolved']=props['FacingMode'] if props['FacingMode']!='Automatic' else 'FaceCamera'
    info={'emitter':0,'ordinal':ordinal,'export_index':-1,'type':'synthetic','kind':kind,'enabled':True,'props':props,'source_mode':'Particles'}
    if kind=='mesh':info['meshes']=meshes;info['mesh_index_bound']=mib
    return info

def synth_rows(kind,props,seed,n=7,meshes=1):
    rng=np.random.default_rng(seed);cols={}
    for p in nc.props_of(kind):cols[p]=np.zeros(n)
    cols['x'],cols['y'],cols['z']=(rng.uniform(-1,1,n) for _ in range(3));cols['nx_valid'][:]=1
    for c in range(4):cols['col_%d'%c]=rng.uniform(0,1,n)
    if kind in ('sprite','mesh'):
        for c in range(3):cols['vel_%d'%c]=rng.normal(0,2,n)
        for c in range(3):cols['vel_%d'%c][3]=0.0
        for k in range(4):
            for c in range(4):cols['dyn%d_%d'%(k,c)]=rng.uniform(0,1,n)
        cols['subimg']=rng.uniform(0,15.9,n);cols['camoff']=rng.uniform(0,0.2,n);cols['matrand']=rng.uniform(0,1,n);cols['nage']=rng.uniform(0,1,n);cols['sortkey']=rng.uniform(0,10,n)
        cols['vistag'][:]=props['RendererVisibility'];cols['vistag'][5]=props['RendererVisibility']+1
    if kind=='sprite':
        cols['rot']=rng.uniform(-180,180,n);sgn=rng.choice([-1.0,1.0],(n,2));sgn[0]=1
        cols['size_0'],cols['size_1']=rng.uniform(0.1,0.6,n)*sgn[:,0],rng.uniform(0.1,0.6,n)*sgn[:,1]
        for c in range(3):cols['facing_%d'%c]=rng.normal(0,1,n);cols['align_%d'%c]=rng.normal(0,1,n)
        cols['uvs_0'],cols['uvs_1']=rng.uniform(0.5,1.5,n),rng.uniform(0.5,1.5,n)
        piv=props['PivotInUVSpace'];cols['pivot_0'][:],cols['pivot_1'][:]=piv[0],piv[1]
    if kind=='mesh':
        q=rng.normal(0,1,(n,4));q=q/np.linalg.norm(q,axis=1,keepdims=True)*1.7
        for c in range(4):cols['quat_%d'%c]=q[:,c]
        for c in range(3):cols['scale_%d'%c]=rng.uniform(0.5,2.0,n)
        cols['meshidx']=rng.integers(0,meshes+1,n).astype(np.float64)
    if kind=='ribbon':
        t=np.linspace(0,2.5,n)+rng.uniform(0,0.05,n);cols['x'],cols['y'],cols['z']=np.cos(t*1.3)*0.8,np.sin(t*1.3)*0.8,t*0.4
        cols['width']=rng.uniform(0.05,0.3,n);cols['twist']=rng.uniform(-1.5,1.5,n);cols['nage']=np.sort(rng.uniform(0,1,n));cols['matrand']=rng.uniform(0,1,n)
        for c in range(3):cols['facing_%d'%c]=rng.normal(0,1,n)
        for k in range(4):
            for c in range(4):cols['dyn%d_%d'%(k,c)]=rng.uniform(0,1,n)
        cols['uid']=np.arange(n,0,-1).astype(np.float64);cols['uvdist']=np.cumsum(rng.uniform(0.1,0.3,n))
    if kind=='light':
        cols['radius']=rng.uniform(0.5,3.0,n);cols['radius'][2]=0.0;cols['enabled'][:]=1;cols['enabled'][4]=0;cols['exponent']=rng.uniform(1,8,n)
        cols['volscat']=rng.uniform(0,1,n);cols['spec']=rng.uniform(0,1,n);cols['diff']=rng.uniform(0,1,n);cols['vistag'][:]=props['RendererVisibility'];cols['vistag'][6]=props['RendererVisibility']+1
        cols['uid']=np.array(rng.choice(np.arange(10,30),n,replace=False),dtype=np.float64)
    return np.stack([cols[p] for p in nc.props_of(kind)],axis=1).astype(np.float32)

def write_cache(out,name,infos,rows_fn,frames=(1,2)):
    out=pathlib.Path(out)/name;out.mkdir(parents=True,exist_ok=True);streams=[]
    for info in infos:
        sn=nc.stream_name(info);props=nc.props_of(info['kind']);d=out/sn;d.mkdir(exist_ok=True);(d/'f_0000.ply').write_bytes(nc.ply_bytes(props,None))
        sm=rb.Smoothing()
        if info['kind']=='ribbon':(d/'m_0000.ply').write_bytes(rb.mesh_ply_bytes(None))
        for f in frames:
            rows=rows_fn(info,f);(d/('f_%04d.ply'%f)).write_bytes(nc.ply_bytes(props,rows))
            if info['kind']=='ribbon':(d/('m_%04d.ply'%f)).write_bytes(rb.mesh_ply_bytes(rb.generate({p:rows[:,i] for i,p in enumerate(props)},info['props'],sm)))
        streams.append({'name':sn,'kind':info['kind'],'emitter':0,'ordinal':info['ordinal'],'ply_properties':props})
    manifest={'schema':nc.SCHEMA,'system':name,'package':'synthetic','bake_hashes':None,'inputs':{},'frame_start':0,'frame_end':max(frames),'fps':60.0,'fps_base':1.0,
              'bake_origin_ue':list(ORIGIN_UE),'bake_origin_b':[ORIGIN_UE[0]*0.01,-ORIGIN_UE[1]*0.01,ORIGIN_UE[2]*0.01],'emitters':[],'renderers':infos,'streams':streams,'stats':{}}
    write(out/'cache_manifest.json',manifest);return str(out)

def cmd_synth(a):
    out=pathlib.Path(a.out);caches=[]
    sprite_cases={'s_facecam':{},'s_plane':{'FacingMode':'FaceCameraPlane'},'s_custom':{'FacingMode':'CustomFacingVector'},'s_pos':{'FacingMode':'FaceCameraPosition'},
        's_vel_fc':{'Alignment':'VelocityAligned','FacingMode':'FaceCamera'},'s_vel_cf':{'Alignment':'VelocityAligned','FacingMode':'CustomFacingVector'},
        's_ca_fc':{'Alignment':'CustomAlignment','FacingMode':'FaceCamera'},'s_ca_cf':{'Alignment':'CustomAlignment','FacingMode':'CustomFacingVector'},
        's_pivot1':{'PivotInUVSpace':[0.5,0.01]},'s_pivot2':{'PivotInUVSpace':[0.5,0.8],'SortMode':'ViewDistance','RendererVisibility':1},
        's_sub44':{'SubImageSize':[4.0,4.0],'SortMode':'ViewDepth'},'s_sub164':{'SubImageSize':[16.0,4.0],'bSubImageBlend':False}}
    for i,(name,over) in enumerate(sorted(sprite_cases.items())):
        info=synth_info('sprite',0,over);caches.append(write_cache(out,name,[info],lambda inf,f,i=i:synth_rows('sprite',inf['props'],1000*i+f)))
    q=lambda r:list(nc.rotator_quat({'Pitch':r[0],'Yaw':r[1],'Roll':r[2]}))
    m2=[{'object_path':'/T/A','scale':[1.0,2.0,0.5],'rotation_quat_ue':q((10,20,30)),'pivot_offset':[5.0,-3.0,2.0],'pivot_space':'Mesh'},
        {'object_path':'/T/B','scale':[0.7,0.7,1.3],'rotation_quat_ue':q((-40,5,90)),'pivot_offset':[0.0,0.0,0.0],'pivot_space':'Mesh'}]
    m1w=[{'object_path':'/T/A','scale':[1.0,1.0,1.0],'rotation_quat_ue':q((0,45,0)),'pivot_offset':[4.0,6.0,-2.0],'pivot_space':'World'}]
    L=(0.3,0.5,0.8);Ln=[v/float(np.linalg.norm(L)) for v in L]
    mesh_cases={'m_default':({},m2,True),'m_vel':({'FacingMode':'Velocity'},m1w,False),'m_plane':({'FacingMode':'CameraPlane'},m2,False),
        'm_lock_world':({'FacingMode':'Velocity','bLockedAxisEnable':True,'LockedAxis':Ln,'LockedAxisSpace':'World'},m2,True),
        'm_campos_lock':({'FacingMode':'CameraPosition','bLockedAxisEnable':True,'LockedAxis':[0.0,0.0,1.0],'LockedAxisSpace':'Simulation'},m1w,False)}
    for i,(name,(over,meshes,mib)) in enumerate(sorted(mesh_cases.items())):
        info=synth_info('mesh',0,over,meshes,mib);caches.append(write_cache(out,name,[info],lambda inf,f,i=i:synth_rows('mesh',inf['props'],5000+100*i+f,meshes=len(inf['meshes']))))
    for i,(name,over) in enumerate([('l_basic',{'bAlphaScalesBrightness':True,'ColorAdd':[0.1,0.2,0.0],'RadiusScale':2.0}),('l_exp',{'bUseInverseSquaredFalloff':False,'RendererVisibility':99})]):
        info=synth_info('light',0,over);caches.append(write_cache(out,name,[info],lambda inf,f,i=i:synth_rows('light',inf['props'],9000+10*i+f)))
    # EEVEE order probe: green (near, row 0) then red (far, row 1), alpha 0.5, overlapping; index order puts the far quad last.
    oinfo=synth_info('sprite',0,{});op=nc.props_of('sprite')
    def order_rows(inf,f):
        rows=np.zeros((2,len(op)),dtype=np.float32);c={p:i for i,p in enumerate(op)}
        for r,(y,col) in enumerate([(-0.5,(0,1,0,0.5)),(0.5,(1,0,0,0.5))]):
            rows[r,c['y']]=y;rows[r,c['nx_valid']]=1;rows[r,c['size_0']]=rows[r,c['size_1']]=1.0;rows[r,c['pivot_0']]=rows[r,c['pivot_1']]=0.5;rows[r,c['uvs_0']]=rows[r,c['uvs_1']]=1.0
            for k in range(4):rows[r,c['col_%d'%k]]=col[k]
            for k in range(4):
                for j in range(4):rows[r,c['dyn%d_%d'%(k,j)]]=1.0
        return rows
    rib_base={'link_order_source':'UniqueID','twist_bound':False,'ribbon_id_bound':False,'uvdist_bound':True,'u0_override_bound':False,'u1_override_bound':False,'v0_range_bound':False,'v1_range_bound':False}
    rib_cases={'r_screen':{},'r_tube_twist':{'Shape':'Tube','TubeSubdivisions':4,'twist_bound':True,'TessellationMode':'Custom','TessellationFactor':6,'bUseConstantFactor':True,
                                             'UV0Settings':{'DistributionMode':'ScaledUniformly'}},
               'r_custom':{'FacingMode':'Custom','UV0Settings':{'DistributionMode':'TiledOverRibbonLength','TilingLength':50.0,'LeadingEdgeMode':'SmoothTransition'}},
               'r_side_multi':{'FacingMode':'CustomSideVector','Shape':'MultiPlane','MultiPlaneCount':3,'WidthSegmentationCount':2,'twist_bound':True,'link_order_source':'NormalizedAge',
                               'UV0Settings':{'DistributionMode':'TiledFromStartOverRibbonLength','TilingLength':40.0,'Scale':[-1.0,2.0]}}}
    for i,(name,over) in enumerate(sorted(rib_cases.items())):
        info=synth_info('ribbon',0,dict(rib_base,**over));caches.append(write_cache(out,name,[info],lambda inf,f,i=i:synth_rows('ribbon',inf['props'],7000+10*i+f,n=9)))
    order=write_cache(out,'order',[oinfo],order_rows,frames=(1,))
    sinfo=synth_info('sprite',0,{'SubImageSize':[4.0,4.0],'bSubImageBlend':True})
    def subuv_rows(inf,f):
        rows=np.zeros((len(SUBUV_INDEX),len(op)),dtype=np.float32);c={p:i for i,p in enumerate(op)}
        for r,idx in enumerate(SUBUV_INDEX):
            rows[r,c['x']]=-3.5+r;rows[r,c['nx_valid']]=1;rows[r,c['size_0']]=rows[r,c['size_1']]=0.8;rows[r,c['pivot_0']]=rows[r,c['pivot_1']]=0.5
            rows[r,c['uvs_0']]=rows[r,c['uvs_1']]=1.0;rows[r,c['subimg']]=idx
            for k in range(4):rows[r,c['col_%d'%k]]=1.0
        return rows
    sub=write_cache(out,'subuv',[sinfo],subuv_rows,frames=(1,))
    write(out/'synth_index.json',{'caches':caches,'order':order,'subuv':sub});print(json.dumps({'synthetic_caches':len(caches)}))

def cmd_blender(a):
    job=read(a.job);job['repo']=str(REPO);job['out']=a.out;jp=pathlib.Path(a.out)/('job_%s.json'%job['mode']);write(jp,job)
    start=time.perf_counter();log=pathlib.Path(a.out)/('blender_%s.log'%job['mode'])
    p=subprocess.run([a.blender,'--factory-startup','--background','--python',str(REPO/'scripts'/'niagara_r0_blender.py'),'--',str(jp)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=a.timeout_min*60)
    text=p.stdout+'\n'+p.stderr;log.write_text(text,encoding='utf-8')
    bad=[l for l in text.splitlines() if 'Traceback' in l or 'Error:' in l]
    ok=p.returncode==0 and 'R0_BLENDER_DONE' in text and not bad
    print(json.dumps({'mode':job['mode'],'exit':p.returncode,'done_marker':'R0_BLENDER_DONE' in text,'error_lines':bad[:10],'ok':ok,'seconds':round(time.perf_counter()-start,1)}))
    sys.exit(0 if ok else 1)

def _tol_pos(exp,got):return np.abs(got-exp)<=1e-5+1e-6*np.linalg.norm(exp,axis=-1,keepdims=True)  # spec N-O5 section 11: 1e-5 m + 1e-6 |p|
def compare_sprite(npz,cols,info,cam):
    r=ref.sprite_frame(cols,info,cam);n=len(r['corners']);pos=npz['positions'].astype(np.float64);fails=[]
    if len(pos)!=4*n:return [{'what':'count','expected':4*n,'got':int(len(pos))}]
    if n==0:return []
    uv=npz['attr__nx_quad_uv'].astype(np.float64);inst=np.arange(len(pos))//4;ci=np.rint(uv[:,0]).astype(int)+2*np.rint(uv[:,1]).astype(int)
    corner_of={0:0,1:1,3:2,2:3}
    if not all(sorted(ci[inst==i])==[0,1,2,3] for i in range(n)):return [{'what':'quad_uv_set'}]
    exp=r['corners'][inst,[corner_of[c] for c in ci]];okp=_tol_pos(exp,pos)
    if not okp.all():k=int(np.argwhere(~okp.all(axis=1))[0][0]);fails.append({'what':'corner','vertex':k,'expected':exp[k].tolist(),'got':pos[k].tolist(),'max_abs':float(np.abs(exp-pos).max())})
    for name,val in r['attrs'].items():
        got=npz.get('attr__'+name)
        if got is None:fails.append({'what':'missing_attr','attr':name});continue
        e=val.astype(np.float32)[inst]
        if not np.array_equal(e,got):k=int(np.argwhere(~(e==got).all(axis=1))[0][0]);fails.append({'what':'attr','attr':name,'vertex':k,'expected':e[k].tolist(),'got':got[k].tolist()})
    return fails
def compare_mesh(npz,cols,info,cam,verts):
    """Per mesh entry (grouped by the nx_mesh tag; Join Geometry order is not defined), instances in point order."""
    v=np.asarray(verts,dtype=np.float64);pos_all=npz['positions'].astype(np.float64);tag=np.rint(npz['attr__nx_mesh'][:,0]).astype(int) if len(pos_all) else np.zeros(0,int);fails=[]
    for k in range(len(info['meshes'])):
        M=ref.mesh_frame(cols,info,cam,k)['matrices'];exp=(np.einsum('nij,kj->nki',M[:,:3,:3],v)+M[:,None,:3,3]).reshape(-1,3);pos=pos_all[tag==k]
        if len(pos)!=len(exp):fails.append({'what':'count','mesh':k,'expected':int(len(exp)),'got':int(len(pos))});continue
        ok=_tol_pos(exp,pos)
        if not ok.all():i=int(np.argwhere(~ok.all(axis=1))[0][0]);fails.append({'what':'vertex','mesh':k,'vertex':i,'expected':exp[i].tolist(),'got':pos[i].tolist(),'max_abs':float(np.abs(exp-pos).max())})
    return fails
def compare_ribbon(npz,mcols,info,cam):
    """Ribbon vertices in mesh-PLY order (instances counted as vertices)."""
    r=ref.ribbon_frame(mcols,info,cam);pos=npz['positions'].astype(np.float64);exp=r['positions'];fails=[]
    if len(pos)!=len(exp):return [{'what':'count','expected':int(len(exp)),'got':int(len(pos))}]
    if len(pos)==0:return []
    ok=_tol_pos(exp,pos)
    if not ok.all():k=int(np.argwhere(~ok.all(axis=1))[0][0]);fails.append({'what':'vertex','vertex':k,'expected':exp[k].tolist(),'got':pos[k].tolist(),'max_abs':float(np.abs(exp-pos).max())})
    for name,val in r['attrs'].items():
        got=npz.get('attr__'+name)
        if got is None:fails.append({'what':'missing_attr','attr':name});continue
        if not np.array_equal(val.astype(np.float32),got):fails.append({'what':'attr','attr':name})
    return fails


def compare_lights(blights,cache,manifest,info,sname,frame,e1):
    frames=list(range(manifest['frame_start'],manifest['frame_end']+1));states=[ref.light_frame(ref.read_ply(pathlib.Path(cache)/sname/('f_%04d.ply'%f)),info,e1) for f in frames]
    slots,size=ref.assign_slots([[s['uid'] for s in st] for st in states]);fi=frames.index(frame);by={k:s for k,s in zip(slots[fi],states[fi])};fails=[]
    if len(blights)!=size:return [{'what':'pool_size','expected':size,'got':len(blights)}]
    for k,b in enumerate(blights):
        s=by.get(k)
        if (s is None)!=b['hidden']:fails.append({'what':'visibility','slot':k});continue
        if s is None:continue
        checks=[('loc',np.allclose(b['loc'],s['pos'],atol=1e-6)),('color',np.allclose(b['color'],s['color'],atol=1e-6)),('energy',abs(b['energy']-s['energy'])<=1e-5*max(1.0,abs(s['energy']))),
                ('cutoff',abs(b['cutoff']-s['radius'])<=1e-6*max(1.0,s['radius'])),('scales',np.allclose([b['volume'],b['specular'],b['diffuse']],[s['volume'],s['specular'],s['diffuse']],atol=1e-6))]
        fails+=[{'what':w,'slot':k} for w,ok in checks if not ok]
    return fails

def cmd_compare(a):
    from_dump=pathlib.Path(a.dump);index=read(from_dump/'dump_index.json');e1=a.e1;rows=[];verts=[(0.1,0.0,0.0),(0.0,0.2,0.0),(0.0,0.0,0.3),(0.05,0.07,0.11)]
    for key,entry in sorted(index.items()):
        cache=pathlib.Path(entry['cache']);manifest=read(cache/'cache_manifest.json');infos={nc.stream_name(r):r for r in manifest['renderers'] if r['kind'] in nc.CAPTURED}
        for f,fr in sorted(entry['frames'].items(),key=lambda kv:int(kv[0])):
            for sname,s in fr['streams'].items():
                info=infos[sname];cols=ref.read_ply(cache/sname/('f_%04d.ply'%int(f)));cam=ref.camera_rel(fr['cam_world'],s['rig_world'])
                npz=dict(np.load(from_dump/s['npz']))
                if info['kind']=='ribbon':
                    mcols,_=rb.read_mesh_ply(cache/sname/('m_%04d.ply'%int(f)));fails=compare_ribbon(npz,mcols,info,cam);count=int(len(npz['positions']))
                else:
                    fails=compare_sprite(npz,cols,info,cam) if info['kind']=='sprite' else compare_mesh(npz,cols,info,cam,verts);count=int(len(npz['positions'])//4)
                rows.append({'case':key,'frame':int(f),'stream':sname,'kind':info['kind'],'instances':count,'pass':not fails,'fails':fails[:3]})
            for sname,bl in fr['lights'].items():
                fails=compare_lights(bl,cache,manifest,infos[sname],sname,int(f),e1)
                rows.append({'case':key,'frame':int(f),'stream':sname,'kind':'light','instances':sum(not b['hidden'] for b in bl),'pass':not fails,'fails':fails[:3]})
    summary={'checks':len(rows),'pass':sum(r['pass'] for r in rows),'fail':sum(not r['pass'] for r in rows),'by_kind':{k:{'checks':sum(r['kind']==k for r in rows),'pass':sum(r['kind']==k and r['pass'] for r in rows),
             'instances':sum(r['instances'] for r in rows if r['kind']==k)} for k in ('sprite','mesh','light','ribbon')},'failures':[r for r in rows if not r['pass']][:50]}
    write(a.out,summary);print(json.dumps({k:summary[k] for k in ('checks','pass','fail','by_kind')},sort_keys=True))

def cmd_subuv_check(a):
    arr=np.load(a.npy);manifest=read(pathlib.Path(a.cache)/'cache_manifest.json');colours=np.array(read(a.job)['atlas_colours']);fails=[];res=[]
    w,h=SUBUV_RES;ppm=w/SUBUV_CAMERA['ortho_scale']
    for r,idx in enumerate(SUBUV_INDEX):
        x=-3.5+r;px=int(round((x+SUBUV_CAMERA['ortho_scale']/2)*ppm));py=h//2;got=arr[py-1:py+2,px-1:px+2,:3].reshape(-1,3).mean(axis=0)
        lerp=idx-np.floor(idx);a_=int(np.floor(idx))%16;b_=(a_+1)%16;exp=colours[a_]*(1-lerp)+colours[b_]*lerp
        ok=bool(np.all(np.abs(got-exp)<=0.02));res.append({'index':idx,'expected':exp.round(4).tolist(),'got':got.round(4).tolist(),'pass':ok})
    summary={'sprites':len(res),'pass':sum(r['pass'] for r in res),'results':res};write(a.out,summary);print(json.dumps({'sprites':summary['sprites'],'pass':summary['pass']}))

def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='cmd',required=True)
    s=sub.add_parser('synth');s.add_argument('--out',required=True)
    b=sub.add_parser('blender');b.add_argument('--job',required=True);b.add_argument('--out',required=True);b.add_argument('--blender',required=True);b.add_argument('--timeout-min',type=int,default=60)
    c=sub.add_parser('compare');c.add_argument('--dump',required=True);c.add_argument('--out',required=True);c.add_argument('--e1',type=float,default=ref.E1_CYCLES)
    u=sub.add_parser('subuv-check');u.add_argument('--npy',required=True);u.add_argument('--cache',required=True);u.add_argument('--job',required=True);u.add_argument('--out',required=True)
    j=sub.add_parser('jobs',help='write the standard job files');j.add_argument('--synth',required=True);j.add_argument('--corpus-caches',required=True);j.add_argument('--out',required=True);j.add_argument('--m1-cache',required=True);j.add_argument('--m1-blend',required=True)
    a=ap.parse_args()
    if a.cmd=='synth':cmd_synth(a)
    elif a.cmd=='blender':cmd_blender(a)
    elif a.cmd=='compare':cmd_compare(a)
    elif a.cmd=='subuv-check':cmd_subuv_check(a)
    elif a.cmd=='jobs':cmd_jobs(a)

SMOKE=['NS_LocalFogSphere']
def cmd_jobs(a):
    """Job files: synthetic dump (frames 1,2), corpus dump (frame_start + 1/30/90), probe, smoke (10 systems), subuv."""
    si=read(pathlib.Path(a.synth)/'synth_index.json');root=pathlib.Path(a.corpus_caches)
    corpus=sorted(str(p.parent) for p in root.glob('*/cache_a/cache_manifest.json'));out=pathlib.Path(a.out)
    write(out/'job_dump_synth.json',{'mode':'dump','caches':si['caches'],'cameras':CAMERAS,'frames':[1,2]})
    write(out/'job_dump_corpus.json',{'mode':'dump','caches':corpus,'cameras':CAMERAS[:1],'frames_rel':[1,30,90]})
    write(out/'job_probe.json',{'mode':'probe','order_cache':si['order']})
    combos={};
    for c in corpus:
        m=read(pathlib.Path(c)/'cache_manifest.json');k=','.join(sorted({s['kind'] for s in m['streams']}))
        combos.setdefault(k,[]).append(c)
    smoke=[v[0] for k,v in sorted(combos.items())]+[c for c in corpus if pathlib.Path(c).parent.name in SMOKE]
    for k,v in sorted(combos.items(),key=lambda kv:-len(kv[1])):
        for c in v[1:]:
            if len(smoke)>=10:break
            if c not in smoke:smoke.append(c)
    write(out/'job_smoke.json',{'mode':'smoke','caches':sorted(set(smoke))[:12],'frame':61})
    rng=np.random.default_rng(7);write(out/'job_subuv.json',{'mode':'subuv','cache':si['subuv'],'atlas_cells':[4,4],'atlas_colours':rng.uniform(0.1,0.9,(16,3)).round(3).tolist(),
        'subuv_camera':SUBUV_CAMERA,'subuv_resolution':list(SUBUV_RES)})
    write(out/'job_m1.json',{'mode':'m1','cache':a.m1_cache,'blend':a.m1_blend})
    print(json.dumps({'corpus_caches':len(corpus),'smoke':len(sorted(set(smoke))[:12]),'combos':{k:len(v) for k,v in combos.items()}},sort_keys=True))

if __name__=='__main__':main()
