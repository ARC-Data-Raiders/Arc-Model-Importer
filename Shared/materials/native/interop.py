"""Original real-socket compatibility facade for retained native shader mathematics.

Names and stage topology are independent. Blocks are semantic contexts in one
private material graph, never copied nested shader groups. No bpy at import time.
"""
from contextlib import contextmanager
from pathlib import Path
import math as _math
import threading
from .repository import package_ref, digest, resolved_io_path
import hashlib, json, inspect
from .layout import arrange

_STAGES=('DR.Coordinates','DR.Source','DR.LayerInputs','DR.Wear','DR.Surface','DR.Details','DR.Light','DR.Coverage','DR.Output')

def _stage(label):
    text=str(label).lower()
    for words,stage in ((('uv','coord','projection'),'DR.Coordinates'),(('source','texture','sample'),'DR.Source'),(('layer','palette','tint','mask'),'DR.LayerInputs'),(('wear','damage','paint'),'DR.Wear'),(('normal','detail','tangent'),'DR.Details'),(('emiss','light','glow'),'DR.Light'),(('alpha','opacity','coverage','cutout'),'DR.Coverage'),(('output','finish'),'DR.Output')):
        if any(word in text for word in words): return stage
    return 'DR.Surface'

# Frame names stay stable ids; labels are plain words. Tags feed graph_layout.finalize_tree.
_STAGE_LABELS={'DR.Coordinates':'Coordinates','DR.Source':'Textures','DR.LayerInputs':'Layer Inputs','DR.Wear':'Weathering',
               'DR.Surface':'Surface','DR.Details':'Detail','DR.Light':'Emission','DR.Coverage':'Coverage','DR.Output':'Output'}
_STAGE_TAGS={'DR.Coordinates':'Coordinates','DR.Source':'Textures','DR.LayerInputs':'Inputs','DR.Wear':'Weathering',
             'DR.Surface':'Surface','DR.Details':'Detail','DR.Light':'Surface','DR.Coverage':'Mask','DR.Output':'Output'}

def _layout_mod():
    from .. import graph_layout
    return graph_layout

def _stage_color(stage):
    gl=_layout_mod(); role=getattr(gl,'STAGE_ROLE',{}).get(_STAGE_TAGS.get(stage),'surface')
    return getattr(gl,'ROLE_COLORS',{}).get(role,(.12,.20,.28))

def _tag(node,stage):
    gl=_layout_mod(); fn=getattr(gl,'tag',None)
    if fn is not None:
        name=_STAGE_TAGS.get(stage); fn(node,stage=name,role=getattr(gl,'STAGE_ROLE',{}).get(name))

_ENGINE_DEFAULT_TEXTURES=('defaultnormal','defaulttexture','defaultdiffuse','blacksquaretexture','whitesquaretexture','flatnormal')
def engine_default_texture_ref(ref):
    """True for a non-/Game engine or plugin default texture (DefaultNormal, DefaultTexture)."""
    text=str(ref or '').replace('\\','/').strip()
    if not text.startswith('/') or text.casefold().startswith('/game/'): return False
    return text.rsplit('/',1)[-1].split('.',1)[0].casefold() in _ENGINE_DEFAULT_TEXTURES

def authored_srgb_decode(info):
    """True when a native texture record leaves SRGB at its engine default on an sRGB-capable encoding.

    TC_Default (DXT1/DXT5, or BGRA8 uncompressed) and TC_BC7 have sRGB formats the sampler
    decodes. BC4 alpha, BC5 normal, BC6H/float HDR and curve atlases have none, so a
    missing flag there still means raw data. Every index shape names its provenance.
    """
    info=info or {}
    if not (info.get('source_path') or info.get('source') or info.get('Type')) or info.get('SRGB',True) is False: return False
    if str(info.get('Type') or 'Texture2D') not in ('Texture2D','Texture2DArray','TextureCube','VolumeTexture'): return False
    return str(info.get('CompressionSettings') or 'TC_Default').rsplit('::',1)[-1] in ('TC_Default','TC_BC7')

class Graph:
    def __init__(self,material,obj,index,report,tree=None,parent=None):
        if threading.current_thread() is not threading.main_thread(): raise RuntimeError('Graph requires Blender main thread')
        import bpy
        self.bpy=bpy; self.mat=material; self.obj=obj; self.index=index; self.report=report
        self.x=0; self.y=0; self._stage='DR.Surface'
        if parent is not None:
            self._state=parent._state; self.tree=parent.tree
        else:
            self.tree=tree or bpy.data.node_groups.new('DR Material Flow','ShaderNodeTree')
            self.tree['independent_owned_graph']=True
            if not any(s.name=='Shader' and s.in_out=='OUTPUT' for s in self.tree.interface.items_tree): self.tree.interface.new_socket(name='Shader',in_out='OUTPUT',socket_type='NodeSocketShader')
            self._state={'frames':{},'serial':0,'uv':{},'images':{},'oriented':set(),'orientation_cache':{},'samples':{},'world_normals':set(),'finished':False}
        self.n=self.tree.nodes; self.l=self.tree.links
    def _frame(self,stage):
        # Frames are cached by name, never as bpy references: finish() runs finalize_tree, which
        # replaces stage frames; a cached NodeFrame then dangles and parenting to it corrupts memory.
        # Live references are a build-time fast path only; finish() drops them.
        live=None if self._state['finished'] else self._state.setdefault('frame_refs',{})
        if live is not None and stage in live: return live[stage]
        frames=self._state['frames']; name=frames.get(stage)
        f=self.n.get(name) if name else None
        if f is None or f.bl_idname!='NodeFrame':
            f=None
            if name:
                key=getattr(_layout_mod(),'TAG_STAGE',None); want=_STAGE_TAGS.get(stage)
                if key and want: f=next((n for n in self.n if n.bl_idname=='NodeFrame' and n.get(key)==want),None)
            if f is None:
                f=self.n.new('NodeFrame'); f.name=stage; f.label=_STAGE_LABELS.get(stage,stage); f.label_size=20
                f.use_custom_color=True; f.color=_stage_color(stage); _tag(f,stage)
            frames[stage]=f.name
        if live is not None: live[stage]=f
        return f
    def node(self,typ,label='',**attrs):
        stage=self._stage
        frame=self._frame(stage)
        result=self.n.new(typ); result.parent=frame; _tag(result,stage)
        self._state['serial']+=1; result.name=stage+'.'+typ.removeprefix('ShaderNode')+'.'+str(self._state['serial']).zfill(4)
        result.label=str(label) or typ.removeprefix('ShaderNode')
        for key,value in attrs.items():
            if key in ('name','label','location','parent'): continue
            setattr(result,key,value)
        return result
    def section(self,label):
        self._stage=_stage(label)
        return self
    @contextmanager
    def block(self,label):
        old=self._stage; self.section(label)
        try: yield self
        finally: self._stage=old
    def export(self,value,label,typ=None): return value
    def set(self,socket,v):
        if hasattr(v,'is_output'):
            if v.id_data!=socket.id_data: raise ValueError('Cross-tree shader link rejected')
            self.l.new(v,socket)
        else:
            if isinstance(v,(list,tuple)):
                if socket.type=='RGBA': v=tuple(v[:3])+(float(v[3]) if len(v)>3 else 1.,)
                elif socket.type=='VECTOR': v=tuple(v[:3])
            if isinstance(v,(int,float,bool)) and socket.type in ('VECTOR','RGBA'):
                v=(float(v),)*3+((1.,) if socket.type=='RGBA' else ())
            socket.default_value=v
        return socket
    def value(self,v,label=''):
        n=self.node('ShaderNodeValue',label); n.outputs[0].default_value=float(v); return n.outputs[0]
    def color(self,v,label=''):
        n=self.node('ShaderNodeRGB',label); rgba=tuple(v)
        n.outputs[0].default_value=rgba if len(rgba)==4 else rgba+(1.,)
        return n.outputs[0]
    def parameter(self,label,value,typ='Float'):
        kind=typ.lower()
        if kind in ('vector','float3','nodesocketvector'):
            if hasattr(value,'is_output'):
                if value.type!='VECTOR': raise ValueError('Vector parameter requires a vector socket')
                return value
            if len(value)!=3: raise ValueError('Vector parameter requires exactly three components; promote vector2 explicitly')
            result=self.combine(*value); result.node.label=str(label); return result
        if kind in ('vector2','float2'): raise ValueError('Vector2 parameter requires explicit promotion to three components')
        return self.color(value,label) if kind in ('color','rgba','float4','nodesocketcolor') or isinstance(value,(tuple,list)) else self.value(value,label)
    def time(self,label='Time (seconds)'):
        socket=self.value(0.,label)
        curve=socket.driver_add('default_value')
        curve.driver.type='SCRIPTED'
        for name,path in (('fps','render.fps'),('fps_base','render.fps_base')):
            variable=curve.driver.variables.new(); variable.name=name; variable.type='SINGLE_PROP'
            variable.targets[0].id_type='SCENE'; variable.targets[0].id=self.bpy.context.scene; variable.targets[0].data_path=path
        curve.driver.expression='frame * fps_base / max(fps, 1)'
        return socket
    def math(self,op,a,b=0,c=0,label=''):
        n=self.node('ShaderNodeMath',label,operation=op)
        for i,v in enumerate((a,b,c)):
            if i<len(n.inputs): self.set(n.inputs[i],v)
        return n.outputs[0]
    def add(self,a,b): return self.math('ADD',a,b)
    def sub(self,a,b): return self.math('SUBTRACT',a,b)
    def mul(self,a,b): return self.math('MULTIPLY',a,b)
    def div(self,a,b): return self.math('DIVIDE',a,b)
    def clamp(self,a): return self.math('MINIMUM',self.math('MAXIMUM',a,0),1)
    def _is_vector(self,v): return isinstance(v,(tuple,list)) or getattr(v,'type',None) in ('VECTOR','RGBA')
    def mix(self,a,b,f,label='lerp'):
        if not self._is_vector(a) and not self._is_vector(b): return self.add(self.mul(a,self.sub(1,f)),self.mul(b,f))
        n=self.node('ShaderNodeMixRGB',label,blend_type='MIX'); self.set(n.inputs[0],f); self.set(n.inputs[1],a); self.set(n.inputs[2],b); return n.outputs[0]
    def cmul(self,a,b,label='multiply'):
        n=self.node('ShaderNodeVectorMath',label,operation='MULTIPLY'); self.set(n.inputs[0],a); self.set(n.inputs[1],b); return n.outputs[0]
    def split(self,v):
        n=self.node('ShaderNodeSeparateXYZ'); self.set(n.inputs[0],v); return tuple(n.outputs)
    def combine(self,x,y,z=0):
        n=self.node('ShaderNodeCombineXYZ')
        for i,v in enumerate((x,y,z)): self.set(n.inputs[i],v)
        return n.outputs[0]
    def vector(self,op,a,b=(0,0,0),scale=1):
        n=self.node('ShaderNodeVectorMath',operation=op); self.set(n.inputs[0],a)
        if op not in ('NORMALIZE','LENGTH','ABSOLUTE','FLOOR','CEIL','FRACTION','SINE','COSINE','TANGENT'): self.set(n.inputs[1],b)
        if op=='SCALE': self.set(n.inputs['Scale'],scale)
        return n.outputs['Value'] if op in ('DOT_PRODUCT','DISTANCE','LENGTH') else n.outputs['Vector']
    def uv(self,channel=0):
        channel=int(channel)
        if channel not in self._state['uv']:
            old=self._stage; self._stage='DR.Coordinates'
            try:
                n=self.node('ShaderNodeUVMap','Native UV '+str(channel))
                layers=getattr(getattr(self.obj,'data',None),'uv_layers',())
                if channel<len(layers): n.uv_map=layers[channel].name
                elif layers:
                    # Local vertex fetch clamps a material's texcoord index to NumTexCoords-1
                    # (LocalVertexFactory.ush; vehicle VS 9287): M_TrimMap_01's UV1 tiling on the
                    # one-UV SM_Mdr_WindTurbine_01_BrokenBase_A reads UV0.
                    n.uv_map=layers[len(layers)-1].name
                    self.report.setdefault('notes',[]).append('UV'+str(channel)+' clamped to last mesh channel UV'+str(len(layers)-1))
                elif channel: raise IndexError('Required authored UV channel unavailable: '+str(channel))
                x,y,z=self.split(n.outputs['UV'])
                self._state['uv'][channel]=self.combine(x,self.sub(1,y),z)
            finally: self._stage=old
        return self._state['uv'][channel]
    def _transform_logical_uv(self,source,scale,offset,rotation):
        if isinstance(scale,(int,float)) or getattr(scale,'type',None)=='VALUE': out=self.vector('SCALE',source,scale=scale)
        else: out=self.vector('MULTIPLY',source,tuple(scale[:2])+(1.,) if isinstance(scale,(tuple,list)) else scale)
        x,y,z=self.split(out)
        if not isinstance(rotation,(int,float)) or rotation!=0:
            angle=self.mul(rotation,2*_math.pi); cosine=self.math('COSINE',angle); sine=self.math('SINE',angle)
            cx=self.sub(x,.5); cy=self.sub(y,.5)
            x=self.add(self.sub(self.mul(cx,cosine),self.mul(cy,sine)),.5)
            y=self.add(self.add(self.mul(cx,sine),self.mul(cy,cosine)),.5)
        if isinstance(offset,(tuple,list)): ox,oy=offset[:2]
        else: ox,oy,_=self.split(offset)
        return self.combine(self.add(x,ox),self.add(y,oy),z)
    def transform_uv(self,channel=0,scale=1,offset=(0,0),rotation=0):
        with self.block('Coordinates'): return self._transform_logical_uv(self.uv(channel),scale,offset,rotation)
    def orient_uv(self,uv):
        key=uv.as_pointer()
        if key in self._state['oriented']: return uv
        if key in self._state['orientation_cache']: return self._state['orientation_cache'][key]
        with self.block('Coordinates'):
            x,y,z=self.split(uv); result=self.combine(x,self.sub(1,y),z)
        self._state['oriented'].add(result.as_pointer()); self._state['orientation_cache'][key]=result; return result
    def _missing(self,ref,required,error):
        self.report.setdefault('missing_textures',[]).append({'reference':str(ref),'reason':str(error)})
        if required: raise FileNotFoundError(str(ref)) from error
        return None
    def _unset_image(self,uv,label,srgb):
        """Empty texture reference = parameter unset: never raise, sample a neutral stand-in.

        approximate: the engine would sample the master default (DefaultTexture /
        DefaultNormal); with no default in the records the input is skipped. RGB is
        mid grey (colour) or flat normal (data), alpha 0 so masks and decal opacity drop out.
        """
        name=str(label) or 'texture'
        self.report.setdefault('unset_textures',[]).append(name)
        self.report.setdefault('warnings',[]).append('approximate: unset texture parameter skipped: '+name)
        role='color' if srgb is None or srgb is True else 'data'
        key='DR_unset_texture_'+role
        image=self._state['images'].get(key)
        if image is None:
            image=next((im for im in self.bpy.data.images if im.get('independent_image_key')==key),None)
            if image is None:
                image=self.bpy.data.images.new(key,1,1,alpha=True)
                image.pixels[:]=(.5,.5,.5,0.) if role=='color' else (.5,.5,1.,0.)
                image.colorspace_settings.name='Non-Color'; image['independent_image_key']=key
            if image.alpha_mode!='CHANNEL_PACKED': image.alpha_mode='CHANNEL_PACKED'
            self._state['images'][key]=image
        return self.sample_image(image,uv,'Unset · '+name,{'reference':'','role':role,'image_key':key,'unset':True})
    def image(self,ref,uv=None,layer=None,srgb=None,label='',required=True):
        if ref is None or ref=='':
            if not required: return self._missing(ref,False,FileNotFoundError('Missing exact texture reference'))
            return self._unset_image(uv,label,srgb)
        sampling_role='albedo' if srgb is None or srgb is True else 'data'
        try:
            native_info=self.index.texture_info(ref) if hasattr(self.index,'texture_info') else None
            native_info={} if native_info is None else native_info
            if srgb is None:
                if 'SRGB' in native_info: srgb=bool(native_info['SRGB'])
                else:
                    srgb=True; self.report.setdefault('warnings',[]).append('Native SRGB metadata absent; engine default-color sampling: '+str(ref))
            elif srgb is False and authored_srgb_decode(native_info):
                srgb=True; self.report.setdefault('notes',[]).append('Authored sRGB texture decoded as colour: '+str(ref))
            elif srgb is True and native_info.get('SRGB',True) is False:
                srgb=False; self.report.setdefault('notes',[]).append('Authored linear texture sampled raw: '+str(ref))
            role='color' if srgb is True else 'normal' if srgb=='normal' else 'data'
            reference=package_ref(ref,getattr(self.index,'mount_aliases',None))
            if layer is not None and hasattr(layer,'is_output'): raise NotImplementedError('Dynamic slice must be expanded into explicit authored-source samples')
            if layer is not None and hasattr(self.index,'array_reference'): reference=self.index.array_reference(reference,int(layer)); layer=None
            if hasattr(self.index,'image_path'):
                parameters=inspect.signature(self.index.image_path).parameters
                if 'slice' in parameters: path=self.index.image_path(reference,role,slice=layer)
                elif layer is None: path=self.index.image_path(reference,role)
                else: raise NotImplementedError('Repository cannot resolve an exact authored array slice')
            elif hasattr(self.index,'texture'): path=self.index.texture(reference,layer=layer)
            else: raise TypeError('Image repository must provide image_path or texture')
            if path is None: raise FileNotFoundError(reference)
            path=resolved_io_path(path)
            if not path.is_file(): raise FileNotFoundError(str(path))
            key=hashlib.sha256(json.dumps([reference,layer,role,str(path),digest(path),str(getattr(self.index,'build','')),str(getattr(self.index,'mapping',''))]).encode()).hexdigest()
            image=self._state['images'].get(key)
            if image is None:
                image=next((im for im in self.bpy.data.images if im.get('independent_image_key')==key),None)
                if image is None:
                    image=self.bpy.data.images.load(str(path),check_existing=False)
                    image.colorspace_settings.name='sRGB' if role=='color' else 'Non-Color'; image['independent_image_key']=key
                # Unreal samples RGB and A independently. STRAIGHT premultiplies sRGB texels on load, so a packed
                # alpha of 0 (CX mask, CR roughness) would blacken the colour the material reads.
                if image.alpha_mode!='CHANNEL_PACKED': image.alpha_mode='CHANNEL_PACKED'
                self._state['images'][key]=image
            return self.sample_image(image,uv,label,{'reference':reference,'role':role,'sampling_role':sampling_role,'image_key':key,'native':native_info,'filter':'nearest' if native_info.get('Filter') in ('TF_Nearest','TextureFilter::TF_Nearest','nearest') else 'linear','address':'clamp' if native_info.get('AddressX') in ('TA_Clamp','TextureAddress::TA_Clamp','clamp') and native_info.get('AddressY') in ('TA_Clamp','TextureAddress::TA_Clamp','clamp') else 'repeat'})
        except (FileNotFoundError,KeyError) as error:
            if engine_default_texture_ref(ref):
                # Plugin/engine default textures (/MeshModelingToolsetExp/Textures/DefaultNormal) are not
                # in the Pioneer paks; sample the same neutral stand-in as an unset parameter.
                self.report.setdefault('warnings',[]).append('approximate: engine default texture not exported, neutral stand-in: '+str(ref))
                return self._unset_image(uv,label or str(ref).rsplit('/',1)[-1],'data' if 'normal' in str(ref).casefold() else srgb)
            return self._missing(ref,required,error)
    def sample_image(self,img,uv=None,label='',info=None):
        logical_uv=self.uv() if uv is None else uv
        metadata={} if info is None else info
        sample_key=(img.as_pointer(),logical_uv.as_pointer(),metadata.get('filter','linear'),metadata.get('address','repeat'),metadata.get('sampling_role'))
        if sample_key in self._state['samples']: return self._state['samples'][sample_key]
        with self.block('Source'):
            n=self.node('ShaderNodeTexImage',label); n.image=img
            n.interpolation='Cubic' if metadata.get('sampling_role')=='albedo' else 'Closest' if metadata.get('filter')=='nearest' else 'Linear'
            n.extension='EXTEND' if metadata.get('address')=='clamp' else 'REPEAT'
            self.set(n.inputs['Vector'],self.orient_uv(logical_uv))
            r,g,b=self.split(n.outputs['Color'])
            result={'color':n.outputs['Color'],'r':r,'g':g,'b':b,'a':n.outputs['Alpha'],'node':n,'info':metadata}
            self._state['samples'][sample_key]=result
            return result
    def decode_normal(self,tex,strength=1):
        if tex is None or (isinstance(tex,dict) and not tex):
            if 'flat_normal' not in self._state:
                with self.block('Normal details'): self._state['flat_normal']=self.combine(0.,0.,1.)
            return self._state['flat_normal']
        from .normal_math import ensure
        with self.block('Normal details'):
            if isinstance(tex,dict): horizontal,vertical=tex['r'],tex['g']
            else: horizontal,vertical,_=self.split(tex)
            node=self.node('ShaderNodeGroup','Decode tangent normal')
            node.node_tree=ensure('decode',self.bpy)
            for key,value in (('Red',horizontal),('Green',vertical),('Strength',strength)):
                self.set(node.inputs[key],value)
            return node.outputs['Normal']
    def rnm(self,a,b):
        from .normal_math import ensure
        with self.block('Normal details'):
            node=self.node('ShaderNodeGroup','Reorient detail normal')
            node.node_tree=ensure('rnm',self.bpy)
            self.set(node.inputs['Base'],a)
            self.set(node.inputs['Detail'],b)
            return node.outputs['Normal']
    def normal_to_world(self,normal,uv_channel=0):
        with self.block('Normal details'):
            x,y,z=self.split(normal)
            encoded=self.combine(self.add(self.mul(x,.5),.5),self.sub(.5,self.mul(y,.5)),self.add(self.mul(z,.5),.5))
            n=self.node('ShaderNodeNormalMap'); layers=getattr(getattr(self.obj,'data',None),'uv_layers',())
            if isinstance(uv_channel,str): n.uv_map=uv_channel
            elif int(uv_channel)<len(layers): n.uv_map=layers[int(uv_channel)].name
            elif layers: n.uv_map=layers[len(layers)-1].name  # same NumTexCoords-1 clamp as uv()
            elif int(uv_channel): raise IndexError('Normal UV channel unavailable')
            self.set(n.inputs['Color'],encoded); result=n.outputs['Normal']; self._state['world_normals'].add(result.as_pointer()); return result
    def helper(self,kind,values,uv_name=''):
        def get(name,index,default=None):
            if isinstance(values,dict):
                if name in values: return values[name]
                for key,value in values.items():
                    if str(key).lower()==name.lower(): return value
                return default
            return values[index] if index<len(values) else default
        if kind=='UV orientation': return self.orient_uv(get('UV',0,self.uv()))
        if kind=='UV transform':
            with self.block('Coordinates'):
                return self._transform_logical_uv(get('UV',0,self.uv()),get('Scale',1,1),get('Offset',2,(0,0)),get('Rotation',3,0))
        if kind=='DX normal decode':
            return self.decode_normal({'r':get('Red',0),'g':get('Green',1)},get('Strength',2,1))
        if kind=='RNM normals': return self.rnm(get('Base',0),get('Detail',1))
        if kind=='DX tangent to world': return self.normal_to_world(get('Normal',0),uv_name or 0)
        raise NotImplementedError('Unsupported native helper semantic: '+str(kind))
    def triplanar(self,ref,size=100,srgb=None,label='World projection'):
        # This retains the explicitly supplied preview projection contract. It is
        # recorded as compatibility behavior, not an independently proven game shader.
        with self.block('World coordinates'):
            geometry=self.node('ShaderNodeNewGeometry','World projection inputs')
            native=self.vector('MULTIPLY',geometry.outputs['Position'],(100.,-100.,100.))
            native=self.vector('SCALE',native,scale=self.div(1,size))
            x,y,z=self.split(native)
            normal=self.vector('ABSOLUTE',geometry.outputs['Normal'])
            nx,ny,nz=self.split(normal)
            denominator=self.math('MAXIMUM',self.add(self.add(nx,ny),nz),1e-8)
            weights=(self.div(nx,denominator),self.div(ny,denominator),self.div(nz,denominator))
            coordinates=(self.combine(y,z),self.combine(x,z),self.combine(x,y))
        samples=[self.image(ref,uv=uv,srgb=srgb,label=label+' '+axis) for uv,axis in zip(coordinates,'XYZ')]
        with self.block('Surface'):
            def weighted(channel):
                terms=[self.mul(sample[channel],weight) for sample,weight in zip(samples,weights)]
                return self.add(self.add(terms[0],terms[1]),terms[2])
            channels={key:weighted(key) for key in ('r','g','b','a')}
            color=self.combine(channels['r'],channels['g'],channels['b'])
        self.report.setdefault('runtime_diagnostics',[]).append('Triplanar projection is preserved preview compatibility; native shader proof is not claimed')
        return {'color':color,**channels,'node':color.node,'info':{'projection_status':'legacy-compatible','weights':weights,'coordinates':coordinates}}
    def _layout(self):
        arrange(self.tree)
    def _retarget_albedo(self,target):
        """Point arc_albedo_target at the tagged node wherever stage grouping moved it."""
        pending=[self.tree]; seen=set()
        while pending:
            tree=pending.pop()
            if tree is None or tree.as_pointer() in seen: continue
            seen.add(tree.as_pointer())
            for n in tree.nodes:
                if n.get('dr_albedo_target'):
                    target=dict(target,tree=tree.name,node=n.name)
                    self.mat['arc_albedo_node']=n.name; self.mat['arc_albedo_target']=json.dumps(target,sort_keys=True)
                    return target
                if n.type=='GROUP': pending.append(n.node_tree)
        return target
    def finish(self,color,roughness=.5,metallic=0,normal=None,specular=.5,emission=None,cloth=0,alpha=1,emission_strength=1,response=None):
        if self._state['finished']: raise RuntimeError('Graph has already been committed')
        # Shared User Controls group between game values and the BSDF (neutral defaults are exact).
        from .. import user_controls as _uc
        with self.block(_uc.UC_LABEL):
            ucn=self.node('ShaderNodeGroup',_uc.UC_LABEL); ucn.node_tree=_uc.ensure_group(); ucn.width=200.
            ucn['arc_user_controls']=_uc.UC_VER
            game=[]
            for value,key in ((color,'Albedo'),(roughness,'Roughness'),(metallic,'Metallic')):
                if value is None: game.append(value); continue
                self.set(ucn.inputs[key],value); game.append(ucn.outputs[key])
            albedo_game=color; color,roughness,metallic=game
        with self.block('Surface'):
            shader=self.node('ShaderNodeBsdfPrincipled','Physical surface')
            for key,value in (('Base Color',color),('Roughness',roughness),('Metallic',metallic),('Specular IOR Level',specular),('Alpha',alpha),('Emission Strength',emission_strength),('Sheen Weight',cloth)):
                self.set(shader.inputs[key],value)
            # Blender's Emission Color defaults to white; with the default strength of 1 an
            # emission-free family would glow uniformly white, so absent emission is black.
            self.set(shader.inputs['Emission Color'],emission if emission is not None else (0.,0.,0.,1.))
            if normal is not None:
                if not hasattr(normal,'as_pointer') or normal.as_pointer() not in self._state['world_normals']: normal=self.normal_to_world(normal)
                self.set(shader.inputs['Normal'],normal)
            if response:
                for key,value in response.items():
                    socket=shader.inputs.get(key)
                    if socket is None: raise NotImplementedError('Unknown native physical response socket: '+str(key))
                    self.set(socket,value)
        with self.block('Output'):
            output=self.node('NodeGroupOutput','Material shader'); self.l.new(shader.outputs['BSDF'],output.inputs['Shader'])
        self._layout()
        old_use_nodes=self.mat.use_nodes; self.mat.use_nodes=True
        root=self.mat.node_tree; previous=list(root.nodes); old_active=[n for n in previous if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
        wrapper=root.nodes.new('ShaderNodeGroup'); wrapper.node_tree=self.tree; wrapper.name='DR.Pending.Material'
        out=root.nodes.new('ShaderNodeOutputMaterial'); out.name='DR.Pending.Output'
        source=albedo_game if hasattr(albedo_game,'is_output') else ucn.inputs['Albedo']
        target={'root_node':'DR.Material','tree':self.tree.name,'node':source.node.name,'socket':source.name,'socket_identifier':source.identifier,'direction':'OUTPUT' if source.is_output else 'INPUT'}
        source.node['dr_albedo_target']=True
        property_keys=('independent_family','independent_owned_graph','arc_albedo_node','arc_albedo_target')
        previous_properties={key:self.mat.get(key) for key in property_keys}
        try:
            root.links.new(wrapper.outputs['Shader'],out.inputs['Surface']); out.is_active_output=True
            self.mat['arc_albedo_node']=source.node.name
            self.mat['arc_albedo_target']=json.dumps(target,sort_keys=True)
            self.mat['independent_family']='native-interop'; self.mat['independent_owned_graph']=True
        except Exception:
            root.nodes.remove(out); root.nodes.remove(wrapper)
            for n in old_active: n.is_active_output=True
            for key,value in previous_properties.items():
                if value is None:
                    if key in self.mat: del self.mat[key]
                else: self.mat[key]=value
            self.mat.use_nodes=old_use_nodes
            raise
        groups={n.node_tree.name:n.node_tree for n in previous if n.type=='GROUP' and n.node_tree and n.node_tree.get('independent_owned_graph',False)}
        for n in previous: root.nodes.remove(n)
        for old in groups.values():
            if old!=self.tree and old.users==0: self.bpy.data.node_groups.remove(old)
        wrapper.name='DR.Material'; wrapper.label='Arc Native Look'; out.name='DR.Output'; out.label='Material Output'
        self._state['finished']=True; self._state.pop('frame_refs',None)
        from .. import graph_layout as _graph_layout
        _tag=getattr(_graph_layout,'tag',None)
        if _tag is not None:
            _tag(wrapper,stage='Look',role='surface'); _tag(out,stage='Output',role='output')
        _finalize=getattr(_graph_layout,'finalize_tree',None)
        if _finalize is not None:
            # Builder done: fold the owned flow into plain stage groups. Node refs taken
            # before this point are stale; late callers only add new nodes through block().
            try:
                from .driven_values import hoist_driven_values
                hoist_driven_values(self.tree)  # grouping skips trees that hold drivers
                _finalize(self.tree,collapse=True,force=True)
                # Grouping moved the cached sockets; a late image() must build fresh ones.
                for key in ('uv','samples','orientation_cache'): self._state[key]={}
                self._state['oriented']=set(); self._state['world_normals']=set(); self._state.pop('flat_normal',None)
                self._retarget_albedo(target)
            except Exception as exc:
                self.report.setdefault('warnings',[]).append('Stage grouping skipped: '+str(exc))
        (_finalize or _graph_layout.tidy_tree)(root)
        root['arc_layout_ver']=getattr(_graph_layout,'LAYOUT_VER',1)
        seen=set(); pending=[root]; unique_count=0
        while pending:
            current=pending.pop(); key=current.as_pointer()
            if key in seen: continue
            seen.add(key); unique_count+=len(current.nodes)
            pending.extend(node.node_tree for node in current.nodes if node.type=='GROUP' and node.node_tree is not None)
        self.report['node_count']=len(root.nodes)
        self.report['unique_graph_nodes']=unique_count
        self.report.setdefault('independent_graphs',[]).append({'material':self.mat.name,'stages':list(self._state['frames']),'nodes':len(self.n)})
        return shader




def triplanar_reference(position,normal,size=100):
    """Numeric reference for the specified preview-compatible projection only."""
    scale=100./size if size else 0.
    x,y,z=(position[0]*scale,-position[1]*scale,position[2]*scale)
    absolute=tuple(abs(v) for v in normal); denominator=max(sum(absolute),1e-8)
    return ((y,z,0.),(x,z,0.),(x,y,0.)),tuple(v/denominator for v in absolute)
