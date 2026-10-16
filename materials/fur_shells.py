"""Native M_FurShells/M_FurLOD dry static surface and authored shell offsets.

Current SM5 39527D9A/E6E0456E: shell index lives in floor(original UV0.V).
Preshader ShellCount is reciprocal(count+1) in Shells and count*Length in LOD.
No invented shell generation, subdivision, displacement maps or procedural fur.
"""
from __future__ import annotations
import json, os
import bpy
import numpy as np
from .. import textures, utils, uemodel_import

SCHEMA = 'native-shells-v1'
MODIFIER = 'ARC Native Fur Shell Offset'
OFFSET = 'arc_native_fur_offset'
SOURCE_POSITION = 'arc_native_fur_source_cm'
STATE_IDS = {'0EFD3174-42A2521C-D62D71BB-C1D84A42':'shells', '84A975DB-403BE39A-019F7EB3-D067328C':'lod'}
_DEFAULTS = {'shells': {'scalars': {'FadeStrength': 0.0, 'SnowMaskTiling': 10.0, 'Snow_Overspray': 0.2, 'WetGloss': 0.7, 'WetGlossCloth': 0.5, 'SnowAmount': 0.0, 'Wet_Amount': 0.0, 'MaxSnowAmount': 0.5, 'MaxWetAmount': 0.6, 'WetTint': 0.8, 'Metallic': 0.4, 'SubSurfaceScattering': 0.88, 'Length': 0.15, 'FurTiling': 9.0, 'Roughness': 0.56, 'FlowmapStrength': 0.0, 'FlowmapTiling': 3.0, 'ColorTiling': 1.0, 'AlphaCutoff': 0.378122, 'WindSpeed': 0.2, 'ShellTimeOffset': 0.01, 'Fresnel': 0.18, 'Sheen': 0.25, 'WindMaskTiling': 0.5, 'WindStrength': 0.5, 'ShellCount': 6.0, 'MaskAlphaCutoff': 0.0}, 'colors': {'SnowColor': (0.8125, 0.8125, 0.8125, 1.0), 'FresnelColor': (0.463542, 0.463542, 0.463542, 1.0), 'SheenColor': (0.192708, 0.192708, 0.192708, 1.0), 'ColorTint': (0.578125, 0.578125, 0.578125, 1.0), 'RootColor': (0.0, 0.0, 0.0, 1.0), 'TipColor': (1.0, 1.0, 1.0, 1.0)}, 'textures': {'FurMask': '/Game/Pioneer/MaterialLibrary/Character/CustomMaterials/Textures/T_FurMask_01_A.T_FurMask_01_A', 'Color': '/Game/Pioneer/MaterialLibrary/Character/LayeredMaterials/Textures/Color/T_White_C.T_White_C', 'WindMask': '/Game/Pioneer/MaterialLibrary/Character/CustomMaterials/Textures/T_WindMask_01_A.T_WindMask_01_A', 'Flowmap': '/Game/Pioneer/MaterialLibrary/Textures/Default/T_Default_01_M.T_Default_01_M'}}, 'lod': {'scalars': {'FadeStrength': 0.0, 'SnowMaskTiling': 10.0, 'Snow_Overspray': 0.2, 'WetGloss': 0.7, 'WetGlossCloth': 0.5, 'SnowAmount': 0.0, 'Wet_Amount': 0.0, 'MaxSnowAmount': 0.5, 'MaxWetAmount': 0.6, 'WetTint': 0.8, 'Metallic': 0.4, 'SubSurfaceScattering': 0.88, 'Roughness': 0.56, 'FlowmapStrength': 0.0, 'FlowmapTiling': 3.0, 'ColorTiling': 1.0, 'AlphaCutoff': 0.378122, 'WindSpeed': 0.2, 'ShellTimeOffset': 0.01, 'Fresnel': 0.18, 'Sheen': 0.25, 'WindMaskTiling': 0.5, 'WindStrength': 0.5, 'ShellCount': 6.0, 'Length': 0.15, 'FurTiling': 9.0, 'MaskLength': 0.0}, 'colors': {'SnowColor': (0.8125, 0.8125, 0.8125, 1.0), 'FresnelColor': (0.463542, 0.463542, 0.463542, 1.0), 'SheenColor': (0.192708, 0.192708, 0.192708, 1.0), 'ColorTint': (0.578125, 0.578125, 0.578125, 1.0), 'RootColor': (0.0, 0.0, 0.0, 1.0), 'TipColor': (1.0, 1.0, 1.0, 1.0)}, 'textures': {'Color': '/Game/Pioneer/MaterialLibrary/Character/LayeredMaterials/Textures/Color/T_White_C.T_White_C', 'WindMask': '/Game/Pioneer/MaterialLibrary/Character/CustomMaterials/Textures/T_WindMask_01_A.T_WindMask_01_A', 'Flowmap': '/Game/Pioneer/MaterialLibrary/Textures/Default/T_Default_01_M.T_Default_01_M', 'FurMask': '/Game/Pioneer/MaterialLibrary/Character/CustomMaterials/Textures/T_FurMask_01_A.T_FurMask_01_A'}}}

def _asset(source, reference, extension):
    ref = str(reference or '').replace('\\','/').strip('/')
    if '.' in ref.rsplit('/',1)[-1]: ref = ref.rsplit('.',1)[0]
    for prefix in ('Game/','PioneerGame/Content/','RemappedPlugins/Content/'):
        if ref.lower().startswith(prefix.lower()): ref = ref[len(prefix):]; break
    src = textures._fs_abspath(source).replace('\\','/') if source else ''
    for boundary in ('/PioneerGame/Content/','/RemappedPlugins/Content/','/Game/'):
        index = src.lower().find(boundary.lower())
        if index < 0: continue
        for mount in (boundary.strip('/'),'PioneerGame/Content','RemappedPlugins/Content','Game'):
            path = textures._fs_abspath(src[:index]+'/'+mount+'/'+ref+extension)
            if os.path.isfile(path): return path
    return textures.find_asset_from_object_path(reference,extension) or ''

def profile(path, seen=None):
    """Resolve only proven native master identities, including compact exports."""
    seen = set() if seen is None else seen
    if not path or path in seen or not os.path.isfile(path): return {}
    seen.add(path)
    with open(path,encoding='utf8') as fh: data=json.load(fh)
    entry=utils.first_ue_export(data,'MaterialInstanceConstant') or utils.first_ue_export(data,'Material') or {}
    props=entry.get('Properties') or {}
    parent=(props.get('Parent') or {}).get('ObjectPath','')
    kind=''
    if parent:
        leaf=parent.rsplit('/',1)[-1].split('.')[0]
        kind={'M_FurShells':'shells','M_FurLOD':'lod'}.get(leaf,'')
    flat=data if isinstance(data,dict) else {}
    params=flat.get('Parameters') or {}
    state=(params.get('Properties') or {}).get('StateId') or props.get('StateId')
    kind=kind or STATE_IDS.get(str(state or '').upper(),'')
    inherited={}
    if not kind and parent:
        inherited=profile(_asset(path,parent,'.json'),seen)
        kind=inherited.get('kind','')
    if not kind: return {}
    result={'kind':kind, **{k:dict(v) for k,v in _DEFAULTS[kind].items()}}
    if inherited:
        for key in ('scalars','colors','textures'): result[key].update(inherited[key])
    for key,group in [('scalars','ScalarParameterValues'),('colors','VectorParameterValues'),('textures','TextureParameterValues')]:
        for item in props.get(group,[]):
            name=(item.get('ParameterInfo') or {}).get('Name')
            value=item.get('ParameterValue')
            if not name: continue
            if key=='colors': value=tuple(float(value.get(c,1)) for c in 'RGBA')
            if key=='textures': value=value.get('ObjectPath','')
            result[key][name]=value
    for key,pkey in [('scalars','Scalars'),('colors','Colors')]:
        for name,value in (params.get(pkey) or {}).items():
            result[key][name]=tuple(float(value.get(c,1)) for c in 'RGBA') if key=='colors' else float(value)
    result['textures'].update(flat.get('Textures') or {})
    bpo=props.get('BasePropertyOverrides') or (params.get('Properties') or {}).get('BasePropertyOverrides') or {}
    result['clip']=float(bpo.get('OpacityMaskClipValue',.3333))
    result['two_sided']=bool(bpo.get('TwoSided',False))
    return result

class _Graph:
    def __init__(self,mat,source):
        self.mat=mat;self.source=source;self.nodes=mat.node_tree.nodes;self.links=mat.node_tree.links;self.missing=[]
    def put(self,socket,value):
        if hasattr(value,'node'): self.links.new(value,socket)
        else: socket.default_value=value
    def m(self,op,a,b=0,label=''):
        n=self.nodes.new('ShaderNodeMath');n.operation=op;n.label=label or op
        self.put(n.inputs[0],a);self.put(n.inputs[1],b);return n.outputs[0]
    def v(self,op,a,b):
        n=self.nodes.new('ShaderNodeVectorMath');n.operation=op
        self.put(n.inputs[0],a);self.put(n.inputs[3 if op=='SCALE' else 1],b)
        return n.outputs['Value' if op=='DOT_PRODUCT' else 'Vector']
    def combine(self,x,y,z=0):
        n=self.nodes.new('ShaderNodeCombineXYZ')
        for socket,value in zip(n.inputs,(x,y,z)):self.put(socket,value)
        return n.outputs[0]
    def split(self,value):
        n=self.nodes.new('ShaderNodeSeparateXYZ');self.put(n.inputs[0],value);return tuple(n.outputs)
    def sat(self,value):return self.m('MINIMUM',self.m('MAXIMUM',value,0),1)
    def color(self,value):
        n=self.nodes.new('ShaderNodeRGB');n.outputs[0].default_value=tuple(value);return n.outputs[0]
    def sample(self,reference,uv,label,data=True):
        path=_asset(self.source,reference,'.png')
        if not path:self.missing.append(str(reference));return {'color':self.color((0,0,0,1)),'r':0.0,'g':0.0}
        image=bpy.data.images.load(path,check_existing=True)
        space='Non-Color' if data else 'sRGB'
        if image.colorspace_settings.name!=space and image.users:image=image.copy()
        image.colorspace_settings.name=space
        n=self.nodes.new('ShaderNodeTexImage');n.image=image;n.label=label;n.interpolation='Linear';n.extension='REPEAT'
        x,y,_=self.split(uv);self.put(n.inputs['Vector'],self.combine(x,self.m('SUBTRACT',1,y)))
        s=self.nodes.new('ShaderNodeSeparateColor');self.links.new(n.outputs['Color'],s.inputs['Color'])
        return {'color':n.outputs['Color'],'r':s.outputs['Red'],'g':s.outputs['Green']}
    def lerp_color(self,a,b,factor):return self.v('ADD',a,self.v('SCALE',self.v('SUBTRACT',b,a),factor))
    def clamp_color(self,value):return self.v('MINIMUM',self.v('MAXIMUM',value,(0,0,0)),(1,1,1))

def setup_material(mat, mi_path, source='', obj=None):
    """Build a verified native family. Return False for a different master."""
    p=profile(mi_path)
    if not p:return False
    mat.use_nodes=True;mat.node_tree.nodes.clear();g=_Graph(mat,mi_path);s=p['scalars'];c=p['colors'];t=p['textures'];shells=p['kind']=='shells'
    uv=g.nodes.new('ShaderNodeUVMap');uv.uv_map='UV0';uv.label='Authored shell UV0'
    ux,by,_=g.split(uv.outputs[0]);uy=g.m('SUBTRACT',1,by,'Original Unreal V')
    index=g.m('FLOOR',uy,label='Authored integer shell index');fraction=g.m('SUBTRACT',uy,index,'Fractional strand UV')
    base=g.combine(ux,fraction)
    vertex=g.nodes.new('ShaderNodeVertexColor');vertex.layer_name='COL0';vertex.label='Authored COL0 length mask'
    sep=g.nodes.new('ShaderNodeSeparateColor');g.links.new(vertex.outputs['Color'],sep.inputs['Color'])
    green=sep.outputs['Green'] if obj is None or obj.data.color_attributes.get('COL0') else 1.0
    if green==1.0:mat['arc_fur_vertex_note']='No COL0: white null-buffer approximation; no painted mask is synthesized.'
    flow=g.sample(t['Flowmap'],g.v('SCALE',base,s['FlowmapTiling']),'Native Flowmap')
    flowx=g.m('MULTIPLY',flow['r'],index) if shells else flow['r']
    flowy=g.m('MULTIPLY',flow['g'],index) if shells else flow['g']
    flowvec=g.combine(g.m('MULTIPLY',g.m('SUBTRACT',g.m('MULTIPLY',flowx,2),1),s['FlowmapStrength']),g.m('MULTIPLY',g.m('SUBTRACT',g.m('MULTIPLY',flowy,2),1),s['FlowmapStrength']))
    drift=flowvec
    if shells:
        source_pos=g.nodes.new('ShaderNodeAttribute');source_pos.attribute_name=SOURCE_POSITION;source_pos.label='Native component position (cm)'
        px,_,pz=g.split(source_pos.outputs['Vector'])
        phase=g.m('MULTIPLY',uy,s['ShellTimeOffset'],'Frozen native time 0, shell phase')
        winduv=g.v('SCALE',g.combine(g.m('ADD',phase,g.m('MULTIPLY',px,s['WindMaskTiling'])),g.m('ADD',phase,g.m('MULTIPLY',pz,s['WindMaskTiling']))),3.5525550842285156)
        wind=g.sample(t['WindMask'],winduv,'Native WindMask (time 0)')
        noise=g.sample('/Game/Pioneer/MaterialLibrary/Character/CustomMaterials/Textures/T_UV_Noise_M',g.combine(ux,uy),'Native UV noise texture (time 0)')
        amount=g.m('MULTIPLY',wind['r'],s['WindStrength'])
        windvec=g.combine(g.m('MULTIPLY',g.m('SUBTRACT',g.m('MULTIPLY',noise['r'],2),1),amount),g.m('MULTIPLY',g.m('SUBTRACT',g.m('MULTIPLY',noise['g'],2),1),amount))
        drift=g.v('ADD',flowvec,windvec)
    samplebase=g.v('ADD',base,drift)
    maskuv=g.v('SCALE',samplebase if shells else flowvec,s['FurTiling'])
    mask=g.sample(t['FurMask'],maskuv,'Native FurMask')['r']
    colorref=t['Color'];color=g.sample(colorref,g.v('SCALE',samplebase,s['ColorTiling']),'Native Color',data='furmask' in colorref.lower() or colorref==t['FurMask'])['color']
    geometry=g.nodes.new('ShaderNodeNewGeometry')
    facing=g.m('SUBTRACT',1,g.sat(g.v('DOT_PRODUCT',geometry.outputs['Normal'],geometry.outputs['Incoming'])),'Native facing rim')
    sheen_uv=g.m('DIVIDE',g.m('SUBTRACT',facing,s['Sheen']),1-s['Sheen'])
    gradient=g.sample('/Game/Pioneer/MaterialLibrary/Character/CustomMaterials/Textures/T_Gradient_Anisotropy_M',g.combine(sheen_uv,sheen_uv),'Native sheen gradient')['r']
    fresnel=g.sat(g.m('DIVIDE',g.m('SUBTRACT',facing,s['Fresnel']),1-s['Fresnel']))
    rim=g.v('ADD',g.v('SCALE',g.color(c['SheenColor']),gradient),g.v('SCALE',g.color(c['FresnelColor']),fresnel))
    factor=g.m('ADD',g.m('SUBTRACT',1,green),g.sat(g.m('DIVIDE',index,s['ShellCount']+1))) if shells else g.sat(mask)
    tint=g.lerp_color(g.color(c['RootColor']),g.color(c['TipColor']),factor)
    albedo=g.v('MULTIPLY',g.v('MULTIPLY',color,g.color(c['ColorTint'])),tint)
    rimscale=g.m('MULTIPLY',g.m('MULTIPLY',uy,.2),mask) if shells else mask
    albedo=g.clamp_color(g.v('ADD',albedo,g.v('SCALE',rim,rimscale)))
    rough=g.sat(g.m('ADD',g.m('SUBTRACT',1,g.m('MULTIPLY',gradient,mask)),s['Roughness']))
    denom=g.m('MULTIPLY',index,s['AlphaCutoff'])
    numerator=g.m('MULTIPLY',mask,g.sat(g.m('ADD',green,1-s['MaskAlphaCutoff']))) if shells else mask
    ratio=g.m('DIVIDE',numerator,g.m('MAXIMUM',denom,1e-8))
    alpha=g.m('MAXIMUM',g.m('LESS_THAN',index,.5),g.m('GREATER_THAN',ratio,p['clip']),'Native masked shell cutoff')
    bsdf=g.nodes.new('ShaderNodeBsdfPrincipled');bsdf.label='Native dry fur surface (BSDF approximation)'
    for name,value in [('Base Color',albedo),('Roughness',rough),('Metallic',s['Metallic']),('Specular IOR Level',g.sat(g.m('MULTIPLY',mask,mask))),('Alpha',alpha)]:g.put(bsdf.inputs[name],value)
    out=g.nodes.new('ShaderNodeOutputMaterial');g.links.new(bsdf.outputs[0],out.inputs['Surface'])
    from .common import _set_material_alpha_mode
    _set_material_alpha_mode(mat,'HASHED',threshold=p['clip'],two_sided=p['two_sided'])
    try:mat.displacement_method='BUMP'
    except Exception:pass
    if obj is not None and source:obj['arc_native_fur_source']=textures._fs_abspath(source)
    mat['arc_fur_setup']=SCHEMA;mat['arc_fur_kind']=p['kind'];mat['arc_mi_path']=os.path.abspath(mi_path);mat['arc_native_fur_params']=json.dumps({'kind':p['kind'],'scalars':s},sort_keys=True)
    mat['arc_fur_limits']='Dry static surface, frozen time 0. Native BSDF, runtime weather/fade and morph normal deltas are not claimed identical.'
    if g.missing:mat['arc_fur_missing_textures']=json.dumps(sorted(set(g.missing)))
    elif 'arc_fur_missing_textures'in mat:del mat['arc_fur_missing_textures']
    depth={};lanes={}
    for node in g.nodes:
        value=max((depth.get(link.from_node.name,0)+1 for socket in node.inputs for link in socket.links),default=0)
        depth[node.name]=value;lane=lanes.get(value,0);lanes[value]=lane+1;node.location=(value*230,-lane*190)
    return True

def _native_source(obj,source):
    p=str(source or obj.get('arc_source_mesh') or obj.get('arc_psk_path') or obj.get('arc_native_fur_source') or '')
    p=textures._fs_abspath(os.path.splitext(p)[0]+'.uemodel') if p else ''
    return p if p and os.path.isfile(p) else ''

def reconcile_geometry(obj,source=''):
    """Idempotent verified source offsets before skinning; never edit base/key data."""
    if obj is None or obj.type!='MESH':return {'managed':False}
    managed={i:json.loads(slot.material['arc_native_fur_params']) for i,slot in enumerate(obj.material_slots) if slot.material and slot.material.get('arc_fur_setup')==SCHEMA}
    if not managed:return {'managed':False}
    report={'managed':True,'applied':False}
    try:
        path=_native_source(obj,source)
        if not path:raise ValueError('Native UEModel source unavailable; no guessed shell displacement')
        scale=float(obj.data.get('arc_uemodel_scale',.01));lods=uemodel_import.parse_uemodel(path,scale=scale)[2]
        lod=next((x for x in lods if x.name==str(obj.data.get('arc_uemodel_lod','LOD0'))),None)
        mesh=obj.data
        if lod is None or len(lod.vertices)!=len(mesh.vertices) or len(lod.indices)!=len(mesh.polygons):raise ValueError('Source/current shell topology differs')
        if any(tuple(p.vertices)!=tuple(face)for p,face in zip(mesh.polygons,lod.indices)):raise ValueError('Source/current shell vertex indices differ')
        if not lod.uvs or not mesh.uv_layers:raise ValueError('Authored shell UV0 unavailable')
        loopidx=np.empty(len(mesh.loops),np.int32);mesh.loops.foreach_get('vertex_index',loopidx)
        uv=np.empty(len(mesh.loops)*2,np.float32);mesh.uv_layers[0].data.foreach_get('uv',uv)
        if np.max(np.abs(uv.reshape(-1,2)-lod.uvs[0][loopidx]))>1e-5:raise ValueError('Source/current shell UV0 differs')
        basis=mesh.shape_keys.reference_key.data if mesh.shape_keys else mesh.vertices
        current=np.empty((len(basis),3),np.float64);basis.foreach_get('co',current.ravel());src=np.asarray(lod.vertices,dtype=np.float64)
        augmented=np.column_stack((src,np.ones(len(src))));frame,_res,rank,_sv=np.linalg.lstsq(augmented,current,rcond=None)
        if rank!=4 or np.max(np.linalg.norm(augmented@frame-current,axis=1))>1e-5:raise ValueError('No exact native-to-current shell frame')
        if lod.normals is None or len(lod.normals)!=len(src):raise ValueError('Authored shell normals unavailable')
        colors=dict(lod.vertex_colors)
        if 'COL0'not in colors:raise ValueError('Authored shell COL0 unavailable; no invented length mask')
        green=colors['COL0'][:,1].astype(float)/255;index=np.floor(1-lod.uvs[0][:,1]);distance=np.zeros(len(src));assigned=np.zeros(len(src),bool)
        for material_index,p in managed.items():
            faces=[poly.index for poly in mesh.polygons if poly.material_index==material_index]
            if not faces:continue
            vertices=np.unique(lod.indices[faces].ravel());s=p['scalars']
            values=index[vertices]*green[vertices]*s['Length'] if p['kind']=='shells' else 1+(green[vertices]*s['ShellCount']*s['Length']-1)*s['MaskLength']
            values*=scale
            if np.any(assigned[vertices]& (np.abs(distance[vertices]-values)>1e-8)):raise ValueError('Shared vertex has conflicting authored fur offsets')
            distance[vertices]=values;assigned[vertices]=True
        offset=(np.asarray(lod.normals)*distance[:,None])@frame[:3,:]
        if mesh.users>1:obj.data=mesh.copy();mesh=obj.data
        for name,values in [(OFFSET,offset),(SOURCE_POSITION,src/scale)]:
            attr=mesh.attributes.get(name)
            if attr and mesh.get('arc_native_fur_attributes')!=SCHEMA:raise ValueError('Reserved fur attribute already belongs to user data')
            if attr is None:attr=mesh.attributes.new(name,'FLOAT_VECTOR','POINT')
            if attr.domain!='POINT' or attr.data_type!='FLOAT_VECTOR':raise ValueError('Existing fur attribute has incompatible domain/type')
            attr.data.foreach_set('vector',np.asarray(values,np.float32).ravel())
        mesh['arc_native_fur_attributes']=SCHEMA
        mod=obj.modifiers.get(MODIFIER)
        if mod and (mod.type!='NODES' or not mod.node_group or mod.node_group.get('arc_native_fur')!=SCHEMA):raise ValueError('Reserved fur modifier name belongs to user data')
        if mod is None:
            group=bpy.data.node_groups.new('ARC Native Fur Set Position','GeometryNodeTree');group['arc_native_fur']=SCHEMA
            group.interface.new_socket(name='Geometry',in_out='INPUT',socket_type='NodeSocketGeometry');group.interface.new_socket(name='Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
            n=group.nodes;l=group.links;inp=n.new('NodeGroupInput');out=n.new('NodeGroupOutput');attr=n.new('GeometryNodeInputNamedAttribute');attr.data_type='FLOAT_VECTOR';attr.inputs['Name'].default_value=OFFSET
            setpos=n.new('GeometryNodeSetPosition');l.new(inp.outputs['Geometry'],setpos.inputs['Geometry']);l.new(attr.outputs['Attribute'],setpos.inputs['Offset']);l.new(setpos.outputs['Geometry'],out.inputs['Geometry'])
            mod=obj.modifiers.new(MODIFIER,'NODES');mod.node_group=group
        old=obj.modifiers.get('ArcFurSubdiv')
        if old and (old.type=='SUBSURF' or old.type=='NODES' and old.node_group and old.node_group.name.startswith('ArcFurSubdivGN')):obj.modifiers.remove(old)
        armature=next((m for m in obj.modifiers if m.type=='ARMATURE'),None)
        if armature:
            start=list(obj.modifiers).index(mod);target=list(obj.modifiers).index(armature)
            if start>target:obj.modifiers.move(start,target)
        report.update(applied=True,source=path,vertices=int(assigned.sum()),maximum_offset_metres=float(np.max(np.linalg.norm(offset,axis=1))),kind_counts={k:sum(x['kind']==k for x in managed.values())for k in ['shells','lod']})
        obj['arc_native_fur_geometry']=json.dumps(report,sort_keys=True)
        if 'arc_native_fur_error'in obj:del obj['arc_native_fur_error']
    except Exception as exc:
        report['error']=str(exc);obj['arc_native_fur_error']=str(exc)
    return report
