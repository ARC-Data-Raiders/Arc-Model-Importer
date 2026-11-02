"""Reference Niagara renderer maths in Blender space (spec N-O5 sections 5-7; Gate R0 oracle). numpy only, no bpy.

UE formulas are evaluated on Blender-space vectors (UE (x,y,z) -> (x,-y,z)); every UE cross product becomes cross_ue(a,b) = -cross(a,b).
Sprite corners map directly (corner_b = M corner_ue). Mesh frames are conjugated (M R M), so the facing frame's Y column is negated.
"""
import math
from pathlib import Path
import numpy as np

E1_CYCLES=1.0/(4.0*math.pi)   # Blender point-light irradiance per Watt at 1 m (Cycles; measured 0.07965 in the N6 dry run); Gate R0 re-measures it
UE_CM2_PER_M2=1.0e4
X_AXIS,Z_AXIS=np.array([1.0,0.0,0.0]),np.array([0.0,0.0,1.0])

def read_ply(path):
    data=Path(path).read_bytes();end=data.index(b'end_header\n')+len(b'end_header\n');lines=data[:end].decode('ascii').splitlines()
    if lines[1]!='format binary_little_endian 1.0':raise ValueError('ply_format')
    n=int(lines[2].split()[2]);props=[l.split()[2] for l in lines if l.startswith('property float ')]
    rows=np.frombuffer(data,dtype='<f4',count=n*len(props),offset=end).reshape(n,len(props));return {p:rows[:,i].copy() for i,p in enumerate(props)}

def cross_ue(a,b):return -np.cross(a,b)
def safe_normalize(v,eps):
    L=np.linalg.norm(v,axis=-1,keepdims=True);return v/np.maximum(L,eps)
def normalize(v):
    L=np.linalg.norm(v,axis=-1,keepdims=True);return np.where(L>0,v/np.where(L>0,L,1),0.0)
def vec(cols,prefix,n):return np.stack([cols[f'{prefix}_{c}'].astype(np.float64) for c in range(n)],axis=1)
def scalar(cols,prefix):return cols[prefix].astype(np.float64)

def camera_rel(cam_world,rig_world):
    """Camera in rig space: position and unit axes (X right, Y up, Z backward)."""
    rel=np.linalg.inv(np.asarray(rig_world,dtype=np.float64))@np.asarray(cam_world,dtype=np.float64);axes=rel[:3,:3]/np.linalg.norm(rel[:3,:3],axis=0,keepdims=True)
    return {'pos':rel[:3,3],'x':axes[:,0],'y':axes[:,1],'z':axes[:,2]}

def visible_rows(cols,props):
    keep=cols['nx_valid']>0.5
    if 'vistag' in cols:keep&=np.rint(cols['vistag']).astype(np.int64)==int(props.get('RendererVisibility',0))
    return np.nonzero(keep)[0]

def view_sort(P,cam,mode):
    if mode=='ViewDistance':key=np.linalg.norm(P-cam['pos'],axis=1)
    elif mode=='ViewDepth':key=(P-cam['pos'])@(-cam['z'])
    else:return np.arange(len(P))
    return np.argsort(-key,kind='stable')

def sprite_frame(cols,info,cam):
    """Per drawn sprite (in draw order): corners [N,4,3] for (u,v) = (0,0),(1,0),(1,1),(0,1); packed instancer attributes."""
    pr=info['props'];rows=visible_rows(cols,pr);c={k:v[rows] for k,v in cols.items()}
    P=np.stack([c['x'],c['y'],c['z']],axis=1).astype(np.float64);order=view_sort(P,cam,pr.get('SortMode','None'));c={k:v[order] for k,v in c.items()};P=P[order];n=len(P)
    S=vec(c,'size',2);theta=scalar(c,'rot')*math.pi/180.0;D=safe_normalize(cam['pos']-P,1e-8);CO=D*scalar(c,'camoff')[:,None]
    V=vec(c,'vel',3);Vl=np.linalg.norm(V,axis=1,keepdims=True);Vhat=np.where(Vl>1e-8,V/np.where(Vl>0,Vl,1),Z_AXIS)
    F=safe_normalize(vec(c,'facing',3),1e-6);A=safe_normalize(vec(c,'align',3),1e-6)
    align,facing=pr['Alignment_resolved'],pr['FacingMode_resolved'];camX,camY,camZ=(np.tile(cam[k],(n,1)) for k in ('x','y','z'))
    if align=='Unaligned':
        if facing=='FaceCamera':R,U=camX,-camY
        elif facing=='FaceCameraPlane':R=safe_normalize(cross_ue(camZ,camY),1e-6);U=cross_ue(camZ,R)
        elif facing=='CustomFacingVector':R=safe_normalize(cross_ue(F,camY),1e-6);U=cross_ue(F,R)
        elif facing=='FaceCameraPosition':R=safe_normalize(cross_ue(D,np.tile(Z_AXIS,(n,1))),1e-6);U=cross_ue(D,R)
        else:raise ValueError('facing_unsupported:'+facing)
    else:
        Dir=Vhat if align=='VelocityAligned' else A;CF=F if facing=='CustomFacingVector' else D
        R=safe_normalize(cross_ue(CF,Dir),1e-6);U=cross_ue(CF,R) if facing=='CustomFacingVector' else -Dir
    cs,sn=np.cos(theta)[:,None],np.sin(theta)[:,None];OutR=R*cs+U*sn;OutU=U*cs-R*sn
    Pv=vec(c,'pivot',2);aS=np.abs(S);uv=np.array([[0,0],[1,0],[1,1],[0,1]],dtype=np.float64)
    corners=(P+CO)[:,None,:]+(aS[:,0:1]*(uv[None,:,0]-Pv[:,0:1]))[:,:,None]*OutR[:,None,:]+(aS[:,1:2]*(uv[None,:,1]-Pv[:,1:2]))[:,:,None]*OutU[:,None,:]
    blend=1.0 if pr.get('bSubImageBlend',True) else 0.0;sub=pr.get('SubImageSize',[1.0,1.0]);uvs=vec(c,'uvs',2)
    attrs={'nx_color':vec(c,'col',4),'nx_dyn0':vec(c,'dyn0',4),'nx_dyn1':vec(c,'dyn1',4),'nx_dyn2':vec(c,'dyn2',4),'nx_dyn3':vec(c,'dyn3',4),
           'nx_misc':np.stack([scalar(c,'subimg'),scalar(c,'nage'),scalar(c,'matrand'),np.full(n,blend)],axis=1),
           'nx_uvx':np.stack([uvs[:,0]*np.sign(S[:,0]),uvs[:,1]*np.sign(S[:,1]),np.full(n,float(sub[0])),np.full(n,float(sub[1]))],axis=1)}
    return {'rows':rows[order],'corners':corners,'quad_uv':uv,'attrs':attrs}

def subuv(quad_uv,misc,uvx):
    """UE SubUV texture coordinates (UE v-down convention) for corners quad_uv [K,2] of instances with nx_misc/nx_uvx [N,4]."""
    idx=misc[:,0];lerp=idx-np.floor(idx);a=idx-lerp+0.5;b=a+1.0;sx,sy=uvx[:,2],uvx[:,3]
    uvT=0.5+(quad_uv[None,:,:]-0.5)*uvx[:,None,0:2]
    def tex(k):
        h=np.floor(np.fmod(k,sx));v=np.floor(k/sx);return (np.stack([h,v],axis=1)[:,None,:]+uvT)/np.stack([sx,sy],axis=1)[:,None,:]
    return tex(a),tex(b),lerp*misc[:,3]

def quat_mul(a,b):
    """Hamilton product (NiagaraQuatMul); quaternions as (x,y,z,w)."""
    av,aw,bv,bw=a[...,:3],a[...,3:],b[...,:3],b[...,3:];return np.concatenate([bv*aw+av*bw+np.cross(av,bv),aw*bw-np.sum(av*bv,axis=-1,keepdims=True)],axis=-1)
def quat_matrix(q):
    """Column-vector rotation matrices [N,3,3] for unit quaternions (x,y,z,w)."""
    x,y,z,w=(q[...,i] for i in range(4))
    return np.stack([np.stack([1-2*(y*y+z*z),2*(x*y-w*z),2*(x*z+w*y)],-1),np.stack([2*(x*y+w*z),1-2*(x*x+z*z),2*(y*z-w*x)],-1),np.stack([2*(x*z-w*y),2*(y*z+w*x),1-2*(x*x+y*y)],-1)],-2)

def mesh_frame(cols,info,cam,mesh_k):
    """Instance matrices [N,4,4] (rig space) for mesh entry mesh_k, in draw order; vertex_b = T + Rot (S * v_b)."""
    pr=info['props'];rows=visible_rows(cols,pr);entry=info['meshes'][mesh_k]
    if info.get('mesh_index_bound'):rows=rows[np.rint(cols['meshidx'][rows]).astype(np.int64)==mesh_k]
    c={k:v[rows] for k,v in cols.items()};P=np.stack([c['x'],c['y'],c['z']],axis=1).astype(np.float64);n=len(P)
    q=vec(c,'quat',4);q=q/np.maximum(np.linalg.norm(q,axis=1,keepdims=True),1e-30)
    mq=np.array(entry['rotation_quat_ue'],dtype=np.float64)*np.array([-1,1,-1,1]);Rot=quat_matrix(quat_mul(q,np.tile(mq,(n,1))))
    mode=pr.get('FacingMode','Default');D=safe_normalize(cam['pos']-P,1e-8)
    if mode!='Default':
        if mode=='Velocity':
            V=vec(c,'vel',3);Vl=np.linalg.norm(V,axis=1,keepdims=True);F=np.where(Vl>0,V/np.where(Vl>0,Vl,1),Z_AXIS)
        elif mode=='CameraPosition':F=D
        else:F=np.tile(cam['z'],(n,1))
        if pr.get('bLockedAxisEnable'):
            L=np.array(pr.get('LockedAxis',[0,0,1]),dtype=np.float64)*np.array([1,-1,1]);Lt=np.tile(L,(n,1))
            near=np.abs(F@L)>0.99;F=np.where(near[:,None],X_AXIS if abs(L[2])>0.99 else Z_AXIS,F)
            Zc=Lt;Yc=normalize(cross_ue(Zc,F));Xc=cross_ue(Yc,Zc)
        else:
            if mode=='CameraPlane':Ref=np.tile(cam['y'],(n,1))
            else:
                dz=F@Z_AXIS;Ref=np.where((np.abs(dz)>0.99)[:,None],(-np.sign(dz))[:,None]*X_AXIS,Z_AXIS)
            Xc=F;Yc=normalize(cross_ue(Ref,F));Zc=cross_ue(Xc,Yc)
        Fm=np.stack([Xc,-Yc,Zc],axis=-1);Rot=Fm@Rot
    pscale=vec(c,'scale',3);S=pscale*np.array(entry['scale'],dtype=np.float64)
    T=P+D*scalar(c,'camoff')[:,None];piv=np.array(entry['pivot_offset'],dtype=np.float64)*np.array([0.01,-0.01,0.01])
    T=T+(np.einsum('nij,nj->ni',Rot,piv*pscale) if entry.get('pivot_space','Mesh')=='Mesh' else piv)
    M=np.tile(np.eye(4),(n,1,1));M[:,:3,:3]=Rot*S[:,None,:];M[:,:3,3]=T
    return {'rows':rows,'matrices':M}

def light_frame(cols,info,e1=E1_CYCLES):
    """Drawn lights in row order: uid, position, Blender colour/energy, radius (m), exponent, scales."""
    pr=info['props'];keep=(cols['nx_valid']>0.5)&(cols['enabled']!=0)&(np.rint(cols['vistag']).astype(np.int64)==int(pr.get('RendererVisibility',0)))
    radius=cols['radius'].astype(np.float64)*float(pr.get('RadiusScale',1.0));keep&=radius>0;rows=np.nonzero(keep)[0];out=[]
    for i in rows:
        col=np.array([cols['col_%d'%k][i] for k in range(4)],dtype=np.float64);C=col[:3]*(col[3] if pr.get('bAlphaScalesBrightness') else 1.0)+np.array(pr.get('ColorAdd',[0,0,0]),dtype=np.float64)
        peak=float(max(C.max(),0.0));color=C/peak if peak>0 else np.ones(3)
        out.append({'uid':int(cols['uid'][i]),'pos':np.array([cols['x'][i],cols['y'][i],cols['z'][i]],dtype=np.float64),'color':color,'energy':peak/(UE_CM2_PER_M2*e1),
                    'radius':float(radius[i]),'exponent':0.0 if pr.get('bUseInverseSquaredFalloff',True) else float(cols['exponent'][i]),
                    'volume':float(cols['volscat'][i]),'specular':float(cols['spec'][i]),'diffuse':float(cols['diff'][i])})
    return out

def ribbon_frame(mesh_cols,info,cam):
    """Ribbon vertex positions (rig space) in mesh-PLY vertex order, sentinel dropped (NiagaraRibbonVertexFactory.ush 533-640, 859-866)."""
    pr=info['props'];keep=np.abs(mesh_cols['sl_nx'])+np.abs(mesh_cols['sl_ny'])>=0.5;c={k:v[keep].astype(np.float64) for k,v in mesh_cols.items()};n=int(keep.sum())
    P=np.stack([c['x'],c['y'],c['z']],axis=1);Dir=vec(c,'dir',3)
    custom=pr.get('FacingMode','Screen') in ('Custom','CustomSideVector')
    F=normalize(vec(c,'facing',3) if custom else cam['pos']-P);R=normalize(cross_ue(F,Dir))
    if pr.get('FacingMode')=='CustomSideVector':R,F=-F,R
    if pr.get('twist_bound'):
        tw=scalar(c,'twist')[:,None];cs,sn=np.cos(tw),np.sin(tw);R,F=cs*R+sn*F,-sn*R+cs*F
    up=cross_ue(Dir,R);off=R*scalar(c,'sl_px')[:,None]+up*scalar(c,'sl_py')[:,None]
    attrs={'nx_color':vec(c,'col',4),'nx_uv':np.stack([c['uv0_0'],c['uv0_1'],c['uv1_0'],c['uv1_1']],axis=1),'nx_misc':np.stack([np.zeros(n),c['nage'],c['matrand'],np.zeros(n)],axis=1)}
    return {'positions':P+off*scalar(c,'width')[:,None],'attrs':attrs}


def assign_slots(frames):
    """Stable light-pool slots: frames is a list of lists of uids; returns (slot lists per frame, pool size)."""
    live={};free=[];size=0;result=[]
    for uids in frames:
        for uid in [u for u in live if u not in set(uids)]:free.append(live.pop(uid))
        free.sort();slots=[]
        for uid in uids:
            if uid not in live:
                if free:live[uid]=free.pop(0)
                else:live[uid]=size;size+=1
            slots.append(live[uid])
        result.append(slots)
    return result,size
