"""DR stage circuits for layered outfit composition.

Physical palette equations and public controls remain credited to the retained
native-informed GT recipe. Contribution scheduling and shared expression lowering
are original construction. No binary group or retained graph is copied.
"""
import hashlib,json
from pathlib import Path
from .native.core import expr,literal
from .dr_primary_graph.identity import assign

def e(op,*args,name,stage='Surface',**attrs):return expr(op,*args,name=name,stage='DR.'+stage,**attrs)
def binding(key,stage='Source'):return e('attribute',name=key,stage=stage)
def clamp(value,name,stage='Surface'):return e('min',e('max',value,literal(0.),name=name+'.Lower',stage=stage),literal(1.),name=name+'.Upper',stage=stage)

def palette_circuit():
    """Four independent source contributions, followed by a balanced sum.

    C over B over A over canvas is retained, including clamped overlapping masks.
    This differs from the old sequential three-Mix composition in its dataflow.
    """
    packed=clamp(binding('PackedR'),'Palette.SecondaryWeight')
    raw=e('multiply',e('multiply',binding('ColorMask'),binding('Swatch'),name='Palette.MaskTimesSwatch'),binding('Amt'),name='Palette.Strength')
    wa,wb,wc=[clamp(e('channel',raw,index=i,name='Palette.Mask.'+letter),'Palette.Weight.'+letter) for i,letter in enumerate('ABC')]
    ib=e('subtract',literal(1.),wb,name='Palette.Remaining.B')
    ic=e('subtract',literal(1.),wc,name='Palette.Remaining.C')
    tail=e('multiply',ib,ic,name='Palette.Remaining.BC')
    weights=[e('multiply',e('subtract',literal(1.),wa,name='Palette.Remaining.A'),tail,name='Palette.Contribution.Canvas'),e('multiply',wa,tail,name='Palette.Contribution.A'),e('multiply',wb,ic,name='Palette.Contribution.B'),wc]
    colors=[binding('Canvas')]+[e('mix',binding('Color'+letter),binding('Color'+letter+'2'),packed,name='Palette.Secondary.'+letter) for letter in 'ABC']
    terms=[e('multiply',color,weight,name='Palette.Term.'+letter) for color,weight,letter in zip(colors,weights,('Canvas','A','B','C'))]
    left=e('add',terms[0],terms[1],name='Palette.Sum.CanvasA')
    right=e('add',terms[2],terms[3],name='Palette.Sum.BC')
    return e('multiply',e('add',left,right,name='Palette.Sum.All'),binding('Overlay'),name='Palette.Overlay')

class StageEmitter:
    """Narrow socket-bound immutable DAG emitter; no bpy object proxy."""
    def __init__(self,tree,bindings,ops):self.tree=tree;self.bindings=bindings;self.ops=ops;self.memo={};self.channels={}
    def wire(self,value,target):
        if hasattr(value,'node'):self.tree.links.new(value,target)
        else:
            if getattr(target,'type',None)=='VECTOR' and isinstance(value,(int,float)):value=(value,)*3
            if getattr(target,'type',None)=='RGBA' and isinstance(value,(tuple,list)) and len(value)==3:value=tuple(value)+(1.,)
            target.default_value=value
    def emit(self,item):
        if item.op=='literal':return item.args[0]
        if item.op=='attribute':return self.bindings[item.attrs['name']]
        prior=self.memo.get(id(item))
        if prior is not None and prior[0] is item:return prior[1]
        args=[self.emit(arg) for arg in item.args];kind=item.op
        vector=any(getattr(value,'type',None) in ('RGBA','VECTOR') or isinstance(value,(tuple,list)) for value in args)
        stage=item.attrs['stage'].removeprefix('DR.');role=item.attrs['name']
        def node(type):return self.ops.new_operation(self.tree.nodes,type,stage=stage,role=role)
        if kind=='combine':
            n=node('ShaderNodeCombineXYZ')
            for value,target in zip(args,n.inputs):self.wire(value,target)
            output=n.outputs[0]
        elif kind in ('dot','normalize'):
            n=node('ShaderNodeVectorMath');n.operation={'dot':'DOT_PRODUCT','normalize':'NORMALIZE'}[kind]
            for value,target in zip(args,n.inputs):self.wire(value,target)
            output=n.outputs['Value' if kind=='dot' else 'Vector']
        elif kind=='channel':
            key=id(item.args[0]);split=self.channels.get(key)
            if split is None:
                split=node('ShaderNodeSeparateXYZ' if getattr(args[0],'type',None)=='VECTOR' else 'ShaderNodeSeparateColor')
                if hasattr(split,'mode'):assign(split,'mode','RGB')
                self.wire(args[0],split.inputs[0]);self.channels[key]=split
            output=split.outputs[item.attrs['index']]
        elif kind=='mix':
            # Skip writes of the defaults: each write re-runs the whole tree update.
            n=node('ShaderNodeMix');assign(n,'data_type','RGBA');assign(n,'blend_type','MIX');assign(n,'clamp_factor',True);assign(n,'clamp_result',False)
            self.wire(args[2],n.inputs[0]);self.wire(args[0],n.inputs[6]);self.wire(args[1],n.inputs[7]);output=n.outputs[2]
        elif vector:
            operation={'add':'ADD','subtract':'SUBTRACT','multiply':'MULTIPLY'}[kind]
            n=node('ShaderNodeVectorMath');assign(n,'operation',operation)
            for value,target in zip(args,n.inputs):self.wire(value,target)
            output=n.outputs['Vector']
        else:
            n=node('ShaderNodeMath');assign(n,'operation',{'add':'ADD','subtract':'SUBTRACT','multiply':'MULTIPLY','min':'MINIMUM','max':'MAXIMUM','divide':'DIVIDE','sqrt':'SQRT','less_than':'LESS_THAN'}[kind]);assign(n,'use_clamp',False)
            for value,target in zip(args,n.inputs):self.wire(value,target)
            output=n.outputs[0]
        self.memo[id(item)]=(item,output);return output

def build_palette(api,*,zone,amt,swatch,color_a,color_b,color_c,overlay,tree_key,secondary=False,color_a2=None,color_b2=None,color_c2=None):
    import bpy
    # Identity/default/public parameter contract retained; implementation differs.
    rgba=api._as_rgba;source_key=str(tree_key or 'mat')
    payload={'schema':5,'source_key':source_key,'zone':int(zone),'amt':float(amt),'swatch':rgba(swatch),'overlay':rgba(overlay),'a':rgba(color_a),'b':rgba(color_b),'c':rgba(color_c),'a2':rgba(color_a2 if color_a2 is not None else color_a),'b2':rgba(color_b2 if color_b2 is not None else color_b),'c2':rgba(color_c2 if color_c2 is not None else color_c),'secondary':bool(secondary)}
    identity=json.dumps(payload,sort_keys=True,separators=(',',':'));digest=hashlib.sha256(identity.encode()).hexdigest()[:20];key=''.join(ch for ch in source_key if ch.isalnum() or ch in '_-')[:20] or 'mat';name=f'GT_Colour{int(zone)}_{key}_{digest}'
    ops=api._dr_ops;tree=ops.find_group(bpy.data.node_groups,name)
    if tree is not None and tree.get('arc_gt_identity')==identity:return ops.finish_definition(tree)
    tree=ops.new_group(bpy.data.node_groups,name,'ShaderNodeTree');tree['arc_gt_ver']=5;tree['arc_gt_source_key']=source_key;tree['arc_gt_zone']=int(zone)
    for key,direction,type,default in [('ColorMask','INPUT','NodeSocketColor',(1.,1.,1.,1.)),('PackedR','INPUT','NodeSocketFloat',0.),('Amt','INPUT','NodeSocketFloat',float(amt)),('Colour','OUTPUT','NodeSocketColor',None)]:api._gt_ng_sock(tree,key,direction,type,default)
    gi=ops.new_operation(tree.nodes,'NodeGroupInput',stage='Source',role=f'Palette.Zone{zone}.Input');go=ops.new_operation(tree.nodes,'NodeGroupOutput',stage='Output',role=f'Palette.Zone{zone}.Output')
    bindings={key:gi.outputs[key] for key in ('ColorMask','PackedR','Amt')}
    # Preserve established control order and compatibility labels/aliases.
    controls=[('Swatch',f'{zone}_ColorMaskSwatch',payload['swatch']),('Canvas','white canvas',(1.,1.,1.,1.)),('ColorA','ColorA',payload['a']),('ColorA2','ColorA2',payload['a2']),('ColorB','ColorB',payload['b']),('ColorB2','ColorB2',payload['b2']),('ColorC','ColorC',payload['c']),('ColorC2','ColorC2',payload['c2']),('Overlay',f'{zone}_BaseColorOverlay',payload['overlay'])]
    for key,label,value in controls:
        n=ops.new_operation(tree.nodes,'ShaderNodeRGB',stage='LayerInputs',role=f'Palette.Zone{zone}.Control.{key}');ops.set_label(n,label);n.outputs[0].default_value=value;bindings[key]=n.outputs[0]
    emitter=StageEmitter(tree,bindings,ops);tree.links.new(emitter.emit(palette_circuit()),go.inputs['Colour'])
    tree['arc_gt_identity']=identity;tree['dr.composer']='clamped source contributions and balanced sum';tree['dr.composer.source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return ops.finish_definition(tree)


def decode_circuit():
    source=binding('Color');x=e('subtract',e('multiply',e('channel',source,index=0,name='Normal.Decode.R'),literal(2.),name='Normal.Decode.X.Double'),literal(1.),name='Normal.Decode.X')
    y=e('subtract',e('multiply',e('channel',source,index=1,name='Normal.Decode.G'),literal(2.),name='Normal.Decode.Y.Double'),literal(1.),name='Normal.Decode.Y')
    radial=e('add',e('multiply',x,x,name='Normal.Decode.X.Square'),e('multiply',y,y,name='Normal.Decode.Y.Square'),name='Normal.Decode.Radial')
    z=e('sqrt',e('max',e('subtract',literal(1.),radial,name='Normal.Decode.Z.Square'),literal(0.),name='Normal.Decode.Z.Clamp'),name='Normal.Decode.Z')
    return e('combine',x,y,z,name='Normal.Decode.Vector',stage='Details')

def rnm_circuit():
    t=e('add',binding('Base'),literal((0.,0.,1.)),name='Normal.RNM.BiasedBase',stage='Details')
    u=e('multiply',binding('Detail'),literal((-1.,-1.,1.)),name='Normal.RNM.SignedDetail',stage='Details')
    dot=e('dot',t,u,name='Normal.RNM.Projection',stage='Details')
    scale=e('divide',dot,e('channel',t,index=2,name='Normal.RNM.BaseZ'),name='Normal.RNM.Scale',stage='Details')
    offset=e('subtract',e('multiply',t,scale,name='Normal.RNM.ProjectedBase',stage='Details'),u,name='Normal.RNM.Oriented',stage='Details')
    return e('normalize',offset,name='Normal.RNM.Unit',stage='Details')

def occlusion_circuit():return e('multiply',binding('Albedo'),binding('Occ'),name='Surface.Occlusion.Apply')

def orient_uv_circuit():
    source=binding('Vector')
    horizontal=e('channel',source,index=0,name='UV.Orientation.U',stage='Coordinates')
    vertical=e('channel',source,index=1,name='UV.Orientation.V',stage='Coordinates')
    depth=e('channel',source,index=2,name='UV.Orientation.W',stage='Coordinates')
    flipped=e('subtract',literal(1.),vertical,name='UV.Orientation.FlipV',stage='Coordinates')
    return e('combine',horizontal,flipped,depth,name='UV.Orientation.Vector',stage='Coordinates')

def wear_circuit(hard=False):
    amount=binding('Amount');softness=binding('Softness')
    threshold=e('subtract',e('multiply',amount,e('add',softness,literal(1.),name='Wear.TransitionWidth',stage='Wear'),name='Wear.AuthoredAmount',stage='Wear'),softness,name='Wear.Threshold',stage='Wear')
    if hard:return e('less_than',binding('Raw'),threshold,name='Wear.HardCoverage',stage='Wear')
    distance=e('divide',e('subtract',binding('Raw'),threshold,name='Wear.Distance',stage='Wear'),softness,name='Wear.SoftDistance',stage='Wear')
    return clamp(e('subtract',literal(1.),distance,name='Wear.ReverseDistance',stage='Wear'),'Wear.Coverage',stage='Wear')

_SOCK={'VALUE':'NodeSocketFloat','RGBA':'NodeSocketColor','VECTOR':'NodeSocketVector'}
def emit_grouped(api,nodes,bindings,expression,group_name,*,stage,role,ver=1):
    """Emit expression once into a shared node group; instance it per material (O1d)."""
    ops=api._dr_ops;keys=sorted(bindings);tree,created=api._gt_ng_fresh(group_name,ver)
    if created:
        for key in keys:api._gt_ng_sock(tree,key,'INPUT',_SOCK.get(getattr(bindings[key],'type','VALUE'),'NodeSocketFloat'),None)
        api._gt_ng_sock(tree,'Result','OUTPUT','NodeSocketFloat',None)
        gi=ops.new_operation(tree.nodes,'NodeGroupInput',stage='Source',role=group_name+'.Input');go=ops.new_operation(tree.nodes,'NodeGroupOutput',stage='Output',role=group_name+'.Output')
        tree.links.new(StageEmitter(tree,{key:gi.outputs[key] for key in keys},ops).emit(expression),go.inputs['Result']);ops.finish_definition(tree)
    inst=ops.new_operation(nodes,'ShaderNodeGroup',stage=stage,role=role);inst.node_tree=tree
    for key in keys:nodes.id_data.links.new(bindings[key],inst.inputs[key])
    return inst.outputs[0]

GROUP_CONTRACTS={
 'orient_uv':('GT_OrientUV',[('Vector','NodeSocketVector',(0.,0.,0.))],('Vector','NodeSocketVector',(0.,0.,0.)),orient_uv_circuit),
 'decode':('GT_DxNormalDecode',[('Color','NodeSocketColor',(.5,.5,1.,1.))],('Normal','NodeSocketVector',(0.,0.,1.)),decode_circuit),
 'rnm':('GT_RnmNormals',[('Base','NodeSocketVector',(0.,0.,1.)),('Detail','NodeSocketVector',(0.,0.,1.))],('Normal','NodeSocketVector',(0.,0.,1.)),rnm_circuit),
 'occlusion':('GT_AlbedoXOcc',[('Albedo','NodeSocketColor',(1.,1.,1.,1.)),('Occ','NodeSocketFloat',1.)],('Result','NodeSocketColor',None),occlusion_circuit),
}

def build_stage_group(api,kind):
    name,inputs,output,circuit=GROUP_CONTRACTS[kind];tree,created=api._gt_ng_fresh(name,1)
    if not created:return api._dr_ops.finish_definition(tree)
    for key,type,default in inputs:api._gt_ng_sock(tree,key,'INPUT',type,default)
    api._gt_ng_sock(tree,output[0],'OUTPUT',output[1],output[2]);ops=api._dr_ops
    gi=ops.new_operation(tree.nodes,'NodeGroupInput',stage='Source',role='Layered.'+kind+'.Input');go=ops.new_operation(tree.nodes,'NodeGroupOutput',stage='Output',role='Layered.'+kind+'.Output')
    emitter=StageEmitter(tree,{key:gi.outputs[key] for key,_,_ in inputs},ops);tree.links.new(emitter.emit(circuit()),go.inputs[output[0]])
    tree['dr.composer']='immutable layered '+kind+' circuit';return ops.finish_definition(tree)

def build_wear(api,nodes,links,raw,amount,softness,name,x,y,*,decal_wear=None):
    kind='Crease' if 'crease' in (name or '').casefold() else 'Edge'
    a=api._value(nodes,name+'_amt',kind+' amount',float(amount),x,y)
    if decal_wear is not None:
        mul=api._dr_ops.new_operation(nodes,'ShaderNodeMath',stage='Wear',role='Decal.'+name+'.AuthoredAmount')
        mul.operation='MULTIPLY';links.new(a,mul.inputs[0]);links.new(decal_wear,mul.inputs[1]);a=mul.outputs[0]
    s=api._value(nodes,name+'_soft',kind+' softness',float(softness),x,y)
    hard=float(softness)<=1e-6
    result=emit_grouped(api,nodes,{'Raw':raw,'Amount':a,'Softness':s},wear_circuit(hard),'GT_Wear'+('Hard' if hard else 'Soft'),stage='Wear',role='Wear.'+name+'.Circuit')
    api._dr_ops.set_identity(result.node,name,stage='Wear',role='Wear.'+name+'.Coverage')
    return result


def response_circuit(stages=('Base','Crease','Edge'),*,clamped=True,prefix='Response'):
    """Native Base→Crease→Edge order as independently weighted contributions."""
    factors={key:binding(key+'Factor') for key in stages if key!='Base'}
    if clamped:factors={key:clamp(value,prefix+'.'+key+'.Weight') for key,value in factors.items()}
    one=literal(1.);weights={};remaining=one
    for key in reversed(stages[1:]):
        weights[key]=e('multiply',factors[key],remaining,name=prefix+'.'+key+'.Contribution')
        remaining=e('multiply',e('subtract',one,factors[key],name=prefix+'.'+key+'.Remaining'),remaining,name=prefix+'.RemainingTail')
    weights['Base']=remaining
    terms=[e('multiply',binding(key),weights[key],name=prefix+'.'+key+'.Term') for key in stages]
    while len(terms)>1:terms=[e('add',terms[i],terms[i+1],name=prefix+'.Sum'+str(i)) if i+1<len(terms) else terms[i] for i in range(0,len(terms),2)]
    return terms[0]

def build_scalar_response(api,nodes,links,mi_data,zone_eq,crease,edge,suffix,default,*,x,y):
    from . import clothing as C
    bindings={};stages=['Base']
    for stage,factor in [('Base',None),('Crease',crease),('Edge',edge)]:
        if stage!='Base' and factor is None:continue
        values={zone:api._value(nodes,f'GT_z{zone}_{stage}{suffix}',f'{zone}_{stage}{suffix}',C._gt_param_float(mi_data,f'{zone}_{stage}{suffix}',default),x,y) for zone in range(1,9)}
        start=api._value(nodes,f'GT_{stage}{suffix}_Default',suffix,default,x,y)
        bindings[stage]=api.mux_by_zone(nodes,links,None,values,dtype='FLOAT',start=start,prefix=f'GT_mux_{stage}{suffix}',zone_eq=zone_eq,x_fn=api.colour_col_x,y=y)
        if stage!='Base':stages.append(stage);bindings[stage+'Factor']=factor
        y-=180.
    result=emit_grouped(api,nodes,bindings,clamp(response_circuit(tuple(stages),prefix='Surface.Response'),'Surface.Response.Clamp'),'GT_Response_'+''.join(stages),stage='Surface',role='Response.'+suffix+'.Circuit')
    api._dr_ops.set_identity(result.node,'GT_'+suffix+'_Clamp',stage='Surface',role='Response.'+suffix+'.Output');return result

def cloth_circuit():
    dot=e('dot',binding('Incoming'),binding('WorldNormal'),name='Cloth.ViewNormalDot')
    grazing=e('multiply',e('subtract',literal(1.),dot,name='Cloth.Grazing'),literal(.5),name='Cloth.Response')
    # Authored cloth factor is intentionally unbounded, as in the retained Mix.
    result=e('add',e('multiply',binding('Specular'),e('subtract',literal(1.),binding('Cloth'),name='Cloth.DielectricWeight'),name='Cloth.Dielectric'),e('multiply',grazing,binding('Cloth'),name='Cloth.Fabric'),name='Cloth.Combined')
    return clamp(result,'Cloth.Specular')

def build_cloth(api,nodes,links,specular,mi_data,zone_eq,world_normal):
    from . import clothing as C
    # ShadeAsCloth is a zone scalar; mi_params drops it (_MI_KEEP_TOKENS), which left cloth shading off.
    values={zone:api._value(nodes,f'GT_z{zone}_ShadeAsCloth',f'{zone}_ShadeAsCloth',C._mi_layer_float((mi_data or {}).get('zone_scalars'),zone,'ShadeAsCloth',0.),0,0) for zone in range(1,9)}
    zero=api._value(nodes,'GT_Cloth_Default','Dry cloth default',0.,0,0)
    cloth=api.mux_by_zone(nodes,links,None,values,dtype='FLOAT',start=zero,prefix='GT_mux_Cloth',zone_eq=zone_eq,x_fn=api.colour_col_x,y=0.)
    geometry=api._dr_ops.new_operation(nodes,'ShaderNodeNewGeometry','GT_Cloth_View',stage='Source',role='Cloth.View')
    emitter=StageEmitter(nodes.id_data,{'Specular':specular,'Cloth':cloth,'Incoming':geometry.outputs['Incoming'],'WorldNormal':world_normal},api._dr_ops);result=emitter.emit(cloth_circuit())
    api._dr_ops.set_identity(result.node,'GT_Cloth_SpecularClamp',stage='Surface',role='Cloth.Specular.Output');return result

def emission_circuit(rows,stages=('Base','Crease','Edge'),overall=1.):
    terms=[]
    for row in rows:
        zone=row['zone'];prefix='Emission.Zone'+str(zone)
        response=response_circuit(stages,clamped=False,prefix=prefix+'.Strength')
        # Bind authored strengths as literal parameters without falsy fallback.
        replacements={stage:literal(value) for stage,value in zip(('Base','Crease','Edge'),row['strengths'])}
        def resolve(item):
            if item.op=='attribute' and item.attrs['name'] in replacements:return replacements[item.attrs['name']]
            if item.op=='attribute' and item.attrs['name'] in ('CreaseFactor','EdgeFactor'):return binding(item.attrs['name'])
            return expr(item.op,*(resolve(arg) if hasattr(arg,'op') else arg for arg in item.args),**dict(item.attrs))
        strength=e('max',e('multiply',resolve(response),binding('Zone'+str(zone)),name=prefix+'.ZoneGate',stage='Light'),literal(0.),name=prefix+'.Nonnegative',stage='Light')
        overlay=row['overlay'];_a=max(0.,min(1.,overlay[3]));color=e('mix',binding('Albedo'),literal(tuple(overlay[:3])),literal(_a),name=prefix+'.AuthoredColor',stage='Light')
        terms.append(e('multiply',color,strength,name=prefix+'.Contribution',stage='Light'))
    if not terms:return None
    level=0
    while len(terms)>1:
        terms=[e('add',terms[i],terms[i+1],name='Emission.Sum.'+str(level)+'.'+str(i),stage='Light') if i+1<len(terms) else terms[i] for i in range(0,len(terms),2)];level+=1
    # Compatibility: retained OverallEmissive was a literal Color socket input,
    # whose RNA assignment clamps negative components to0. Dynamic strengths
    # remain socket expressions; this conversion applies only to that literal.
    gain=overall if hasattr(overall,'op') else literal(max(0.,float(overall)))
    return e('multiply',terms[0],gain,name='Emission.OverallAuthoredGain',stage='Light')

def compress_emissive_strength(value):
    """Authored zone strength -> Blender strength. Up to 1 is kept; above 1 grows as 1+ln(v).
    The card camera's eye adaptation keeps strong glows (BANSHEE helmet dots, 10) saturated; linear 10 clips to white
    in Blender, while weak glows (PIRATE goggles, 0.7) already match linearly (user 2026-11-08, emis_cmp crops)."""
    import math
    value=float(value)
    return value if value<=1. else 1.+math.log(value)

def build_emission(api,nodes,links,mi_data,colours,zone_eq,albedo,crease,edge):
    from . import clothing as C
    rows=[]
    for zone in range(1,9):
        values=[compress_emissive_strength(C._gt_param_float(mi_data,f'{zone}_{stage}EmissiveStrength',0.)) for stage in ('Base','Crease','Edge')]
        if zone in zone_eq and any(value!=0. for value in values):rows.append({'zone':zone,'strengths':values,'overlay':C._colour_rgba(colours,f'{zone}_EmissiveColorOverlay',(0.,0.,0.,0.))})
    if not rows:return None
    stages=('Base',)+(('Crease',) if crease is not None else ())+ (('Edge',) if edge is not None else ())
    bindings={'Albedo':albedo,**{'Zone'+str(row['zone']):zone_eq[row['zone']] for row in rows}}
    if crease is not None:bindings['CreaseFactor']=crease
    if edge is not None:bindings['EdgeFactor']=edge
    # One bound parameter, so the gain is a wrapper input (Emission panel) and colourways can write it.
    from . import param_bind
    bindings['OverallEmissive']=param_bind.bind(nodes,'OverallEmissive',max(0.,float(C._gt_param_float(mi_data,'OverallEmissive',1.))))
    expression=emission_circuit(rows,stages,binding('OverallEmissive'))
    return StageEmitter(nodes.id_data,bindings,api._dr_ops).emit(expression)

def build_terminal(api,nodes,links,*,albedo,roughness,metallic,specular,n_color,emission,mi_data,zone_eq):
    """Source→normal transport→cloth response→closure→output, without rewiring."""
    ops=api._dr_ops;nmap=ops.new_operation(nodes,'ShaderNodeNormalMap','GT_NormalMap',stage='Details',role='Surface.NormalTransport')
    links.new(n_color,nmap.inputs['Color']);specular=build_cloth(api,nodes,links,specular,mi_data,zone_eq,nmap.outputs['Normal'])
    bsdf=ops.new_operation(nodes,'ShaderNodeBsdfPrincipled','GT_Principled',stage='Output',role='Surface.Closure');ops.set_label(bsdf,'GT Principled (full outfit PBR)');bsdf.inputs['IOR'].default_value=1.5
    for source,key in [(albedo,'Base Color'),(roughness,'Roughness'),(metallic,'Metallic'),(specular,'Specular IOR Level'),(nmap.outputs['Normal'],'Normal')]:links.new(source,bsdf.inputs[key])
    if emission is not None:links.new(emission,bsdf.inputs['Emission Color']);from .emissive_exposure import character_emissive_exposure;bsdf.inputs['Emission Strength'].default_value=character_emissive_exposure();bsdf['dr.pro']='emission'
    out=ops.new_operation(nodes,'ShaderNodeOutputMaterial','GT_MaterialOutput',stage='Output',role='Surface.Output');links.new(bsdf.outputs['BSDF'],out.inputs['Surface']);return bsdf,nmap,out
