"""Main-thread Blender lowering of the independent numeric IR.
No bpy import occurs until compilation. Every graph uses private nodes and role-keyed images.
"""
import threading
import math
import json
from collections import OrderedDict
from ...Shared.materials.native.core import Expr, expr, literal, expression_fingerprint, _coordinate_expr, normalize_material_settings, ignores_alpha_output, preview_parameter_definition

from ...Shared.materials import graph_layout as _graph_layout
_UC_KEYS={'base_color':'Albedo','roughness':'Roughness','metallic':'Metallic'}
_OUTPUTS={'base_color':'Base Color','roughness':'Roughness','metallic':'Metallic','specular':'Specular IOR Level','normal':'Normal','alpha':'Alpha','emission':'Emission Color','emission_strength':'Emission Strength','ior':'IOR','transmission':'Transmission Weight','coat':'Coat Weight','coat_roughness':'Coat Roughness','tangent':'Tangent','anisotropy':'Anisotropic IOR Level','subsurface_weight':'Subsurface Weight','subsurface_radius':'Subsurface Radius'}

# Stage frames: internal name -> plain label, graph_layout stage tag.
_STAGE_LABELS={'DR.Controls':'Controls','DR.Coordinates':'Coordinates','DR.Source':'Textures','DR.LayerInputs':'Layer Inputs',
               'DR.Wear':'Weathering','DR.Surface':'Surface','DR.Details':'Detail','DR.Light':'Emission',
               'DR.Coverage':'Coverage','DR.Output':'Output'}
_STAGE_TAGS={'DR.Controls':'Controls','DR.Coordinates':'Coordinates','DR.Source':'Textures','DR.LayerInputs':'Inputs',
             'DR.Wear':'Weathering','DR.Surface':'Surface','DR.Details':'Detail','DR.Light':'Surface',
             'DR.Coverage':'Mask','DR.Output':'Output'}
LAYOUT_VER=1

def _stage_role(stage):
    return getattr(_graph_layout,'STAGE_ROLE',{}).get(_STAGE_TAGS.get(stage),'surface')

def _stage_color(stage):
    return getattr(_graph_layout,'ROLE_COLORS',{}).get(_stage_role(stage),(0.12,0.20,0.28))

# Control sockets: key -> (plain name, panel, min, max, subtype, hint). The key stays the
# stable id (group['arc_control_keys'] maps it to the socket identifier); old names alias it.
_CONTROL_SPEC={
    'dr_roughness_scale':('Roughness Scale','Surface',0.,2.,'NONE','Multiplies the game roughness'),
    'dr_metallic_scale':('Metallic Scale','Surface',0.,2.,'NONE','Multiplies the game metallic value'),
    'dr_emission_scale':('Emission Strength','Surface',0.,10.,'NONE','Multiplies the glow colour'),
    'dr_alpha_scale':('Alpha Scale','Surface',0.,2.,'NONE','Multiplies the game opacity'),
    'dr_uc_hue':('Hue Shift','Colour',-.5,.5,'NONE','Rotates the colour hue'),
    'dr_uc_sat':('Saturation','Colour',0.,2.,'NONE','Scales colour saturation'),
    'dr_uc_val':('Brightness','Colour',0.,2.,'NONE','Scales colour brightness'),
    'dr_uc_tint_h':('Tint Hue','Colour',0.,1.,'FACTOR','Hue of the tint colour'),
    'dr_uc_tint_a':('Tint Amount','Colour',0.,1.,'FACTOR','How much tint to mix in'),
    'dr_uc_wear':('Wear','Weathering',0.,1.,'FACTOR','Adds worn edges'),
    'dr_uc_dirt':('Dirt','Weathering',0.,1.,'FACTOR','Adds dirt in creases'),
    'dr_uc_wet':('Wetness','Weathering',0.,1.,'FACTOR','Makes the surface look wet'),
    'dr_uc_snow':('Snow','Weathering',0.,1.,'FACTOR','Adds snow on top faces'),
    'dr_uc_rust':('Rust','Weathering',0.,1.,'FACTOR','Adds rust on metal'),
}
_PANELS=('Colour','Surface','Weathering','Advanced')

def _control_socket(group,panels,keys,key,default,label):
    """New float group input for control ``key`` in its panel, with range, subtype and hint."""
    spec=_CONTROL_SPEC.get(key)
    if spec is None:
        text=str(label or key).replace('_',' ').strip()
        name=' '.join(w[:1].upper()+w[1:] for w in text.split()) or key
        spec=(name,'Advanced',min(0.,float(default)),max(1.,abs(float(default))*4.),'NONE','Game parameter '+str(key))
    name,panel_name,lo,hi,subtype,hint=spec
    taken={s.name for s in group.interface.items_tree if getattr(s,'item_type','')=='SOCKET' and s.in_out=='INPUT'}
    if name in taken: name=name+' ('+str(key)+')'
    if not panels:
        for i,pname in enumerate(_PANELS):
            try: panels[pname]=group.interface.new_panel(pname,default_closed=i>0)
            except (TypeError,AttributeError): panels[pname]=None
    parent=panels.get(panel_name)
    try: socket=group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketFloat',parent=parent)
    except TypeError: socket=group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketFloat')
    socket.default_value=default
    socket.min_value=min(lo,float(default)); socket.max_value=max(hi,float(default))
    try: socket.subtype=subtype
    except (TypeError,AttributeError): pass
    socket.description=hint
    keys[key]=socket.identifier
    group['arc_control_keys']=json.dumps(keys,sort_keys=True)
    return socket

def _finish_panels(group,panels):
    """Drop empty panels and open the first one that holds sockets."""
    opened=False
    for pname in _PANELS:
        panel=panels.get(pname)
        if panel is None: continue
        if not any(getattr(item,'parent',None)==panel and item!=panel for item in group.interface.items_tree):
            group.interface.remove(panel); continue
        panel.default_closed=opened; opened=True

def control_keys(group):
    """{control key: socket identifier} of a compiled group ({} for an old graph)."""
    try: return json.loads(group.get('arc_control_keys') or '{}') if group is not None else {}
    except (TypeError,ValueError): return {}

def control_input(group_node,key):
    """Wrapper input for control ``key`` (new graphs map by identifier; old ones named it ``key``)."""
    ident=control_keys(getattr(group_node,'node_tree',None)).get(key)
    if ident:
        for s in group_node.inputs:
            if s.identifier==ident: return s
    return group_node.inputs.get(key)


def authored_role(repository,reference,role):
    """Image role the game's sampler gives a texture whatever a recipe asks (T_Door_02_A_XEM, native prop).

    A default-flag sRGB-capable encoding is decoded and an authored SRGB=false is read raw, as in
    interop.Graph.image; normal maps keep their raw read.
    """
    if role=='normal' or not hasattr(repository,'texture_info'): return role
    from ...Shared.materials.native.interop import authored_srgb_decode
    try: info=repository.texture_info(reference) or {}
    except (OSError,ValueError,KeyError): return role
    if role=='data' and authored_srgb_decode(info): return 'color'
    if role=='color' and info.get('SRGB',True) is False: return 'data'
    return role

_TEMPLATE_CACHE=OrderedDict()

def clear_template_cache():
    import bpy
    for group,_parameters,_labels in _TEMPLATE_CACHE.values():
        try:
            if group.users==0: bpy.data.node_groups.remove(group)
        except ReferenceError: pass
    _TEMPLATE_CACHE.clear()

def compile_plan(plan,material,obj=None,repository=None, *, replace=False,template_key=None):
    if threading.current_thread() is not threading.main_thread(): raise RuntimeError('Blender compilation requires the main thread')
    settings,settings_diagnostics=normalize_material_settings(plan.settings)
    if not settings['blend_supported']: raise NotImplementedError('; '.join(settings_diagnostics))
    import bpy
    if template_key is not None and template_key in _TEMPLATE_CACHE:
        pristine,parameters,labels=_TEMPLATE_CACHE[template_key]
        try: group=pristine.copy()
        except ReferenceError: del _TEMPLATE_CACHE[template_key]
        else:
            _TEMPLATE_CACHE.move_to_end(template_key)
            try: return _attach_group(plan,material,group,parameters,labels,settings,replace)
            except Exception:
                if group.users==0: bpy.data.node_groups.remove(group)
                raise
    group=bpy.data.node_groups.new('Independent '+plan.family,'ShaderNodeTree')
    group['independent_owned_graph']=True
    group.interface.new_socket(name='Shader',in_out='OUTPUT',socket_type='NodeSocketShader')
    nodes=group.nodes; links=group.links
    memo={}; coordinate_memo={}; uv_values={}; fingerprint_memo={}; input_memo={}; image_memo={}; retained=[]; preview_parameters={}
    stages=('DR.Controls','DR.Coordinates','DR.Source','DR.LayerInputs','DR.Wear','DR.Surface','DR.Details','DR.Light','DR.Coverage','DR.Output')
    frames={}
    current=['DR.Surface','','']
    step=['']  # nearest named consumer step, used to word helper maths
    serial=[0]
    def node(kind):
        stage=current[0]
        if stage not in frames:
            frame=nodes.new('NodeFrame'); frame.name=stage; frame.label=_STAGE_LABELS.get(stage,stage); frame.use_custom_color=True
            frame.color=_stage_color(stage); frame.label_size=20; frames[stage]=frame
            _graph_layout.tag(frame,stage=_STAGE_TAGS.get(stage),role=_stage_role(stage))
        n=nodes.new(kind); n.parent=frames[stage]; serial[0]+=1
        n.name=stage+'.'+kind.removeprefix('ShaderNode')+'.'+str(serial[0]).zfill(4)
        n.label=current[1] or kind.removeprefix('ShaderNode')
        _graph_layout.tag(n,stage=_STAGE_TAGS.get(stage),zone=current[2] or None,role=_stage_role(stage))
        return n
    def math_label(n,operation):
        # Plain words for helper maths: '<step>: Multiply' (Blender caps labels at 63 chars).
        text=operation.replace('_',' ').title()
        name=current[1] or step[0]
        n.label=((name+': ') if name else '')[:60-len(text)]+text
    def connect(value,socket):
        if isinstance(value,(tuple,list)):
            value=combine(value)
        if isinstance(value,(float,int,bool)):
            if socket.type in ('VECTOR','RGBA'): socket.default_value=(float(value),)*3+((1.,) if socket.type=='RGBA' else ())
            else: socket.default_value=float(value)
        else: links.new(value,socket)
    # Common-subexpression and constant-folding memo: one node per distinct (operation, inputs).
    cse={}
    def operand(v):
        if isinstance(v,(float,int,bool)): return ('c',float(v))
        return ('s',v.as_pointer(),v.identifier)
    def number(v): return isinstance(v,(float,int,bool)) and not isinstance(v,bool) or isinstance(v,bool)
    def fold(operation,x,y,third):
        import math
        x=float(x); y=float(y)
        if operation=='ADD': return x+y
        if operation=='SUBTRACT': return x-y
        if operation=='MULTIPLY': return x*y
        if operation=='DIVIDE': return x/y if y!=0. else 0.
        if operation=='MINIMUM': return min(x,y)
        if operation=='MAXIMUM': return max(x,y)
        if operation=='LESS_THAN': return 1. if x<y else 0.
        if operation=='GREATER_THAN': return 1. if x>y else 0.
        if operation=='ABSOLUTE': return abs(x)
        if operation=='FLOOR': return float(math.floor(x))
        if operation=='SQRT': return math.sqrt(x) if x>0. else 0.
        if operation=='SINE': return math.sin(x)
        if operation=='COSINE': return math.cos(x)
        return None
    def simplify(operation,x,y):
        xc=number(x); yc=number(y)
        if operation in ('ADD',) and xc and float(x)==0.: return y
        if operation in ('ADD','SUBTRACT') and yc and float(y)==0.: return x
        if operation=='MULTIPLY' and xc and float(x)==1.: return y
        if operation in ('MULTIPLY','DIVIDE') and yc and float(y)==1.: return x
        return None
    def combine(values):
        values=tuple(values[:3]); key=('combine',)+tuple(operand(v) for v in values)
        if key in cse: return cse[key]
        n=node('ShaderNodeCombineXYZ')
        for i,v in enumerate(values): connect(v,n.inputs[i])
        cse[key]=n.outputs[0]; return n.outputs[0]
    def split(socket):
        key=('split',operand(socket))
        if key in cse: return cse[key]
        n=node('ShaderNodeSeparateXYZ'); links.new(socket,n.inputs[0]); cse[key]=tuple(n.outputs); return cse[key]
    def unary(operation,x):
        if isinstance(x,(tuple,list)): return tuple(unary(operation,v) for v in x)
        if number(x):
            folded=fold(operation,x,0.,None)
            if folded is not None: return folded
        key=(operation,operand(x))
        if key in cse: return cse[key]
        n=node('ShaderNodeMath'); n.operation=operation; math_label(n,operation); connect(x,n.inputs[0]); cse[key]=n.outputs[0]; return n.outputs[0]
    def binary(operation,x,y,third=None):
        if isinstance(x,(tuple,list)) or isinstance(y,(tuple,list)):
            vectors=[value for value in (x,y) if isinstance(value,(tuple,list))]
            widths={len(value) for value in vectors}; coordinate=any(id(value) in uv_values for value in vectors)
            if len(widths)>1:
                if widths=={2,3} and coordinate:
                    x=tuple(x)+(0.,) if isinstance(x,(tuple,list)) and len(x)==2 else x
                    y=tuple(y)+(0.,) if isinstance(y,(tuple,list)) and len(y)==2 else y
                else: raise ValueError('Incompatible shader vector widths: '+repr(sorted(widths)))
            vector=x if isinstance(x,(tuple,list)) else y
            result=tuple(binary(operation,x[i] if isinstance(x,(tuple,list)) else x,y[i] if isinstance(y,(tuple,list)) else y,third) for i in range(len(vector)))
            if coordinate: uv_values[id(result)]=result
            return result
        if third is None:
            if number(x) and number(y):
                folded=fold(operation,x,y,None)
                if folded is not None: return folded
            simple=simplify(operation,x,y)
            if simple is not None: return simple
        key=(operation,operand(x),operand(y),None if third is None else operand(third))
        if key in cse: return cse[key]
        n=node('ShaderNodeMath'); n.operation=operation; math_label(n,operation); connect(x,n.inputs[0]); connect(y,n.inputs[1])
        if third is not None: connect(third,n.inputs[2])
        cse[key]=n.outputs[0]; return n.outputs[0]
    def dot(x,y):
        terms=[binary('MULTIPLY',u,v) for u,v in zip(x,y)]
        result=terms[0]
        for t in terms[1:]: result=binary('ADD',result,t)
        return result
    def norm(x):
        length=unary('SQRT',dot(x,x)); return binary('DIVIDE',x,length)
    control_labels={}; control_inputs={}; control_panels={}; control_keys={}
    def control(key,default,label,policy=''):
        if key in preview_parameters:
            prior_default,value=preview_parameters[key]
            if prior_default!=default: raise ValueError('Conflicting preview parameter definitions: '+key)
            return value
        socket=_control_socket(group,control_panels,control_keys,key,default,label)
        if not control_inputs:
            current_stage=current[:]; current[:]=['DR.Controls','Controls','']
            control_inputs['node']=node('NodeGroupInput')
            current[:]=current_stage
            _graph_layout.tag(control_inputs['node'],stage='Controls',role='controls')
        value=next(s for s in control_inputs['node'].outputs if s.identifier==socket.identifier)
        preview_parameters[key]=(default,value)
        control_labels[key]=(label,policy)
        return value
    def lower(e):
        if not isinstance(e,Expr): return e
        retained.append(e)
        identity=id(e)
        if identity in memo: return memo[identity]
        op=e.op; at=e.attrs
        signature=expression_fingerprint(e,fingerprint_memo) if op in ('uv','texture','attribute','geometry_normal','incoming','tangent_view','object_position','object_normal','object_axis_lengths','backfacing','camera_distance','camera_depth','time') else None
        if signature is not None and signature in input_memo: return input_memo[signature]
        previous=tuple(current)
        inferred='DR.Coordinates' if op=='uv' else 'DR.Source' if op=='texture' else 'DR.Details' if op in ('normal_decode','rnm') else previous[0]
        stage=str(at.get('stage',inferred)); stage=stage if stage.startswith('DR.') else 'DR.'+stage
        # Unknown specialized stage labels remain in the nearest physical stage.
        if stage not in stages: stage=next((s for s in stages if stage.startswith(s+'.')),inferred)
        # Zone tag: explicit 'zone' attr, else inherit from the consumer; shared inputs and named stage steps reset it.
        zone=str(at['zone']) if at.get('zone') else '' if (signature is not None or 'stage' in at or 'name' in at) else previous[2]
        current[2]=zone
        own_name=str(at.get('name',at.get('label','')))
        previous_step=step[0]; step[0]=own_name or (previous_step if not (signature is not None or 'stage' in at) else '')
        a=[lower(v) for v in e.args]
        current[:]=[stage,own_name,zone]
        if op=='literal': value=a[0]
        elif op=='preview_parameter':
            key,default=preview_parameter_definition(at)
            value=control(key,default,str(at.get('label',at.get('name',key))),str(at.get('policy','')))
        elif op=='combine': value=tuple(a)
        elif op=='channel': value=a[0][int(at.get('index',a[1] if len(a)>1 else 0))]
        elif op in ('add','subtract','multiply','divide','power','min','max','less_than','greater_than','compare','vector_add','vector_subtract','vector_scale'):
            operations={'add':'ADD','subtract':'SUBTRACT','multiply':'MULTIPLY','divide':'DIVIDE','power':'POWER','min':'MINIMUM','max':'MAXIMUM','less_than':'LESS_THAN','greater_than':'GREATER_THAN','compare':'COMPARE','vector_add':'ADD','vector_subtract':'SUBTRACT','vector_scale':'MULTIPLY'}
            value=binary(operations[op],a[0],a[1],at.get('epsilon',1e-5) if op=='compare' else None)
        elif op in ('abs','floor','fract','sqrt','sin','cos'):
            value=unary({'abs':'ABSOLUTE','floor':'FLOOR','fract':'FRACT','sqrt':'SQRT','sin':'SINE','cos':'COSINE'}[op],a[0])
        elif op=='clamp': value=binary('MINIMUM',binary('MAXIMUM',a[0],a[1] if len(a)>1 else at.get('min',0.0)),a[2] if len(a)>2 else at.get('max',1.0))
        elif op=='mix': value=binary('ADD',binary('MULTIPLY',a[0],binary('SUBTRACT',1.0,a[2])),binary('MULTIPLY',a[1],a[2]))
        elif op=='normalize': value=norm(a[0])
        elif op=='dot': value=dot(a[0],a[1])
        elif op=='cross':
            x,y=a; value=tuple(binary('SUBTRACT',binary('MULTIPLY',x[(i+1)%3],y[(i+2)%3]),binary('MULTIPLY',x[(i+2)%3],y[(i+1)%3])) for i in range(3))
        elif op=='uv':
            n=node('ShaderNodeUVMap'); index=int(at.get('index',at.get('channel',0)))
            if at.get('name'): n.uv_map=at['name']
            elif obj is not None and hasattr(obj.data,'uv_layers') and index<len(obj.data.uv_layers): n.uv_map=obj.data.uv_layers[index].name
            # Local vertex fetch clamps the texcoord index to NumTexCoords-1 (LocalVertexFactory.ush).
            elif obj is not None and len(getattr(obj.data,'uv_layers',())): n.uv_map=obj.data.uv_layers[len(obj.data.uv_layers)-1].name
            elif index: raise ValueError('Requested UV channel unavailable: '+str(index))
            raw_uv=split(n.outputs['UV'])
            value=(raw_uv[0],binary('SUBTRACT',1.0,raw_uv[1]),raw_uv[2])
        elif op=='texture':
            if repository is None: raise ValueError('Texture repository required')
            reference=at['reference']
            if at.get('slice') is not None:
                selected=at['slice']
                if isinstance(selected,Expr) and selected.op=='literal': selected=selected.args[0]
                if not isinstance(selected,(int,float)) or int(selected)!=selected: raise NotImplementedError('Dynamic texture array slice must be expanded into static samples')
                reference=repository.array_reference(reference,int(selected))
            role=authored_role(repository,reference,at.get('role','data')); key=repository.image_key(reference,role)
            image=image_memo.get(key)
            if image is None:
                image=next((im for im in bpy.data.images if im.get('independent_image_key')==key),None)
                if image is None:
                    image=bpy.data.images.load(str(repository.image_path(reference,role)),check_existing=False)
                    image.colorspace_settings.name='sRGB' if role=='color' else 'Non-Color'
                    image['independent_image_key']=key
                # Unreal samples RGB and A independently; STRAIGHT would premultiply sRGB texels by packed alpha.
                if image.alpha_mode!='CHANNEL_PACKED': image.alpha_mode='CHANNEL_PACKED'
                image_memo[key]=image
            n=node('ShaderNodeTexImage'); n.image=image
            n.interpolation='Closest' if at.get('filter') in ('nearest','point','TF_Nearest') else 'Linear'
            address=at.get('address','repeat'); n.extension='EXTEND' if address in ('clamp','TA_Clamp') else 'REPEAT'
            uv=lower(at.get('uv',e.args[0] if e.args else expr('uv')))
            # Unreal UV origin is top-left; conversion occurs only at sampling.
            links.new(combine((uv[0],binary('SUBTRACT',1.0,uv[1]),0.0)),n.inputs['Vector'])
            rgb=split(n.outputs['Color']); value=rgb+(n.outputs['Alpha'],)
        elif op=='tangent_view':
            from ...Shared.materials.native.renderer_vectors import lower_native_tangent_view
            current[:]=['DR.Coordinates','Native view in authored tangent basis','']
            def view_node(kind,label=''):
                result=node(kind)
                if label: result.label=label
                return result
            value=lower_native_tangent_view(view_node,links.new,str(at.get('uv_map','')))
        elif op in ('geometry_normal','incoming','backfacing','object_position','object_normal'):
            if op in ('object_position','object_normal'):
                n=node('ShaderNodeTexCoord'); value=split(n.outputs['Object' if op=='object_position' else 'Normal'])
            else:
                n=node('ShaderNodeNewGeometry'); socket=n.outputs[{'geometry_normal':'Normal','incoming':'Incoming','backfacing':'Backfacing'}[op]]
                value=socket if op=='backfacing' else split(socket)
        elif op=='object_axis_lengths':
            # UE LocalToWorld axis lengths: per-object world images of the unit axes (parents and animation included).
            lengths=[]
            for axis in ((1.,0.,0.),(0.,1.,0.),(0.,0.,1.)):
                n=node('ShaderNodeVectorTransform'); n.vector_type='VECTOR'; n.convert_from='OBJECT'; n.convert_to='WORLD'; n.inputs['Vector'].default_value=axis
                length=node('ShaderNodeVectorMath'); length.operation='LENGTH'; links.new(n.outputs['Vector'],length.inputs[0]); lengths.append(length.outputs['Value'])
            value=tuple(lengths)
        elif op in ('camera_distance','camera_depth'):
            n=node('ShaderNodeCameraData'); value=n.outputs['View Distance' if op=='camera_distance' else 'View Z Depth']
        elif op=='attribute':
            n=node('ShaderNodeAttribute'); n.attribute_type=str(at.get('type','GEOMETRY')); n.attribute_name=str(at.get('name',a[0] if a else 'Color')); value=split(n.outputs['Color'])+(n.outputs['Alpha'],)
        elif op=='time':
            n=node('ShaderNodeValue'); n.outputs[0].default_value=float(at.get('value',0.0))
            if at.get('animated',False):
                driver=n.outputs[0].driver_add('default_value').driver; driver.expression='frame / '+str(float(at.get('fps',24)))
            value=n.outputs[0]
        elif op=='normal_decode':
            x=binary('SUBTRACT',binary('MULTIPLY',a[0][0],2.0),1.0); y=binary('SUBTRACT',binary('MULTIPLY',a[0][1],2.0),1.0)
            if at.get('flip_y',False): y=binary('MULTIPLY',y,-1.0)
            z=unary('SQRT',binary('MAXIMUM',binary('SUBTRACT',1.0,binary('ADD',binary('MULTIPLY',x,x),binary('MULTIPLY',y,y))),0.0)) if at.get('reconstruct_z',True) else binary('SUBTRACT',binary('MULTIPLY',a[0][2],2.0),1.0)
            value=norm((x,y,z))
        elif op=='rnm':
            x,y=a; t=(x[0],x[1],binary('ADD',x[2],1.0)); u=(binary('MULTIPLY',y[0],-1.0),binary('MULTIPLY',y[1],-1.0),y[2])
            combined=norm(binary('SUBTRACT',binary('MULTIPLY',t,binary('DIVIDE',dot(t,u),t[2])),u))
            singular=binary('LESS_THAN',unary('ABSOLUTE',t[2]),1e-12)
            value=binary('ADD',binary('MULTIPLY',combined,binary('SUBTRACT',1.0,singular)),binary('MULTIPLY',norm(x),singular))
        else: raise NotImplementedError('Unsupported Blender IR op: '+op)
        if isinstance(value,(tuple,list)) and _coordinate_expr(e,coordinate_memo): uv_values[id(value)]=value
        memo[identity]=value
        if signature is not None: input_memo[signature]=value
        current[:]=previous; step[0]=previous_step; return value
    try:
        current[:]=['DR.Surface','Physical surface','']; shader=node('ShaderNodeBsdfPrincipled')
        current[:]=['DR.Output','Material shader','']; output=node('NodeGroupOutput'); links.new(shader.outputs['BSDF'],output.inputs['Shader'])
        uc_in={}
        for key,e in plan.outputs.items():
            if key=='alpha' and ignores_alpha_output(plan.settings):
                shader.inputs['Alpha'].default_value=1.; continue
            if key not in _OUTPUTS: raise NotImplementedError('Unknown shader output '+key)
            socket=shader.inputs.get(_OUTPUTS[key])
            if socket is None: raise NotImplementedError('Blender socket unavailable: '+_OUTPUTS[key])
            current[:]=['DR.Light' if key in ('emission','emission_strength') else 'DR.Coverage' if key=='alpha' else 'DR.Details' if key in ('normal','tangent') else 'DR.Surface',key.replace('_',' ').title(),'']
            v=lower(e)
            if key in ('roughness','metallic','emission','alpha'):
                gain=control('dr_'+key+'_scale',1.0,key.replace('_',' ').title()+' scale')
                v=binary('MULTIPLY',v,gain)
            if key=='alpha' and settings['blend']=='masked':
                v=binary('SUBTRACT',1.0,binary('LESS_THAN',v,settings['cutoff']))
            if key=='normal':
                # Family normal IR is unit tangent space; Normal Map converts to shading space.
                n=node('ShaderNodeNormalMap')
                encoded=(binary('ADD',binary('MULTIPLY',v[0],.5),.5),binary('SUBTRACT',.5,binary('MULTIPLY',v[1],.5)),binary('ADD',binary('MULTIPLY',v[2],.5),.5))
                connect(encoded,n.inputs['Color']); connect(n.outputs['Normal'],socket)
            elif key in _UC_KEYS: uc_in[key]=(v,socket)
            else: connect(v,socket)
        if uc_in:
            # Shared User Controls group between game values and the BSDF (neutral defaults are exact).
            from ...Shared.materials import user_controls as _uc
            current[:]=['DR.Surface',_uc.UC_LABEL,'']; ucn=node('ShaderNodeGroup'); ucn.node_tree=_uc.ensure_group()
            ucn.label=_uc.UC_LABEL; ucn['arc_user_controls']=_uc.UC_VER
            for key,(v,socket) in uc_in.items():
                connect(v,ucn.inputs[_UC_KEYS[key]]); links.new(ucn.outputs[_UC_KEYS[key]],socket)
            for sock,prop,label,default,_lo,_hi in _uc.CONTROLS:
                connect(control(_uc.WEAPON_KEY[prop],default,label),ucn.inputs[sock])
        # Precompute the dependency graph once; shared DAGs remain linear in N+E.
        members_by_frame={frame.name:[] for frame in frames.values()}
        incoming_by_name={}
        for n in nodes:
            if n.parent is not None and n.parent.name in members_by_frame:
                members_by_frame[n.parent.name].append(n)
                incoming_by_name[n.name]=[]
        for link in links:
            if link.from_node.parent is not None and link.from_node.parent==link.to_node.parent:
                incoming_by_name[link.to_node.name].append(link.from_node)
        stage_widths={}
        for stage,frame in frames.items():
            members=members_by_frame[frame.name]; depth={}
            def visit(n,active=None):
                if n.name in depth: return depth[n.name]
                active=set() if active is None else active
                if n.name in active: raise ValueError('Shader graph dependency cycle')
                active.add(n.name)
                result=1+max((visit(src,active) for src in incoming_by_name[n.name]),default=-1)
                active.remove(n.name); depth[n.name]=result
                return result
            rows={}
            for n in members:
                d=visit(n); row=rows.get(d,0); rows[d]=row+1
                n.location=(d*190,-row*120); n.width=170
            stage_widths[stage]=(max(depth.values(),default=0)+1)*190+100
        x=0
        for stage in stages:
            if stage in frames:
                frames[stage].location=(x,0); x+=stage_widths[stage]+120
        _finish_panels(group,control_panels)
        _layout(group,collapse=True)  # group root: collapse zone/stage chunks into child groups
        result=_attach_group(plan,material,group,preview_parameters,control_labels,settings,replace)
        if template_key is not None:
            pristine=group.copy()
            pristine.name='.DR.Template '+plan.family
            _TEMPLATE_CACHE[template_key]=(pristine,{key:(default,None) for key,(default,_value) in preview_parameters.items()},dict(control_labels))
            while len(_TEMPLATE_CACHE)>8:
                _key,(old,_parameters,_labels)=_TEMPLATE_CACHE.popitem(last=False)
                try:
                    if old.users==0: bpy.data.node_groups.remove(old)
                except ReferenceError: pass
        return result
    except Exception:
        if group.users==0: bpy.data.node_groups.remove(group)
        raise


def _layout(tree,collapse=None):
    """finalize_tree when graph_layout has it, else the column tidy; stamps the layout version."""
    fin=getattr(_graph_layout,'finalize_tree',None)
    if fin is not None: fin(tree,collapse=collapse)
    else: _graph_layout.tidy_tree(tree)
    tree['arc_layout_ver']=LAYOUT_VER

def _attach_group(plan,material,group,preview_parameters,control_labels,settings,replace):
    import bpy
    old_use_nodes=material.use_nodes
    old_culling=material.use_backface_culling
    old_render=getattr(material,'surface_render_method',None)
    old_props={key:material.get(key) for key in ('independent_family','independent_diagnostics','independent_owned_graph')}
    replace=bool(replace or material.get('independent_owned_graph',False))
    material.use_nodes=True; tree=material.node_tree
    old_nodes=list(tree.nodes)
    old_active=[n for n in old_nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
    wrapper=tree.nodes.new('ShaderNodeGroup'); wrapper.node_tree=group; wrapper.name='DR.Pending.Material'; wrapper.label='Arc '+str(plan.family).replace('_',' ').title()+' Look'
    out=tree.nodes.new('ShaderNodeOutputMaterial'); out.name='DR.Pending.Output'
    wrapper.location=(0,0); wrapper.width=280; out.location=(420,0)
    _graph_layout.tag(wrapper,stage='Look',role='surface'); _graph_layout.tag(out,stage='Output',role='output')
    try:
        # Controls are the wrapper's own sockets (no loose Value nodes at the top level).
        defaults={}
        for key,(default,_value) in preview_parameters.items():
            control_input(wrapper,key).default_value=default; defaults[key]=default
        if defaults:
            wrapper['dr_preview_parameter']=','.join(defaults)
            wrapper['dr_preview_policy']=json.dumps({key:control_labels[key][1] for key in defaults},sort_keys=True)
            wrapper['arc_import_defaults']=json.dumps(defaults,sort_keys=True)
        tree.links.new(wrapper.outputs['Shader'],out.inputs['Surface']); out.is_active_output=True
        material.use_backface_culling=not settings['two_sided']
        if hasattr(material,'surface_render_method') and settings['render_method'] is not None: material.surface_render_method=settings['render_method']
        material['independent_family']=plan.family
        material['independent_diagnostics']='\n'.join(map(str,plan.diagnostics))
        material['independent_owned_graph']=replace
    except Exception:
        tree.nodes.remove(out); tree.nodes.remove(wrapper)
        for previous in old_active: previous.is_active_output=True
        material.use_backface_culling=old_culling
        if old_render is not None: material.surface_render_method=old_render
        for key,value in old_props.items():
            if value is None:
                if key in material: del material[key]
            else: material[key]=value
        material.use_nodes=old_use_nodes
        raise
    if replace:
        old_groups=list({n.node_tree.name:n.node_tree for n in old_nodes if n.type=='GROUP' and n.node_tree and n.node_tree.get('independent_owned_graph',False)}.values())
        for old in old_nodes: tree.nodes.remove(old)
        for old in old_groups:
            if old.users==0: bpy.data.node_groups.remove(old)
    else:
        # Only a single active output is allowed even when retaining unmanaged nodes.
        for previous in old_active: previous.is_active_output=False
    wrapper.name='DR.Material'; out.name='DR.Output'; out.label='Material Output'
    _layout(tree)
    return material

# Bind this module's source identity at import; overwrites require restart.
from ...Shared.materials.native.loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
