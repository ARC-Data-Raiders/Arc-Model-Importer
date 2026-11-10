"""DR packed decal contract, constructed from native channel evidence and probes.

Native enabled Urn SM5 proves RG signed normal/Z reconstruction, B roughness,
A metallic. The retained Blender interface supplies metallic through a SEPARATE
Alpha input; this compatibility decoder preserves that interface and its Mask
behavior. The GT caller connects the sampled metallic Alpha explicitly. No retained
NodeTree topology, node names or layout is copied. Evidence is staged alongside.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
from .native.core import expr, literal

RECIPE='dr_packed_decal_'+hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]

def contract_dag():
    def e(op,*args,stage='Surface',name,**attrs):
        return expr(op,*args,stage='DR.'+stage,name=name,**attrs)
    color=e('attribute',stage='Source',name='Color')
    alpha=e('attribute',stage='Source',name='Alpha')
    mask=e('attribute',stage='Source',name='Mask')
    r=e('channel',color,index=0,stage='Source',name='Packed.R')
    g=e('channel',color,index=1,stage='Source',name='Packed.G')
    b=e('channel',color,index=2,stage='Source',name='Packed.B')
    x=e('subtract',e('multiply',r,literal(2.),name='Normal.X.Scale'),literal(1.),name='Normal.X.Signed')
    y=e('subtract',e('multiply',g,literal(2.),name='Normal.Y.Scale'),literal(1.),name='Normal.Y.Signed')
    xx=e('multiply',x,x,name='Normal.X.Squared')
    yy=e('multiply',y,y,name='Normal.Y.Squared')
    radial=e('add',xx,yy,name='Normal.RadialSquared')
    remaining=e('subtract',literal(1.),radial,name='Normal.Z.Squared')
    z=e('sqrt',e('max',remaining,literal(0.),name='Normal.Z.Nonnegative'),name='Normal.Z.Positive')
    ze=e('multiply',e('add',z,literal(1.),name='Normal.Z.Offset'),literal(.5),name='Normal.Z.Encoded')
    decoded=e('combine',r,g,ze,name='Normal.Encoded')
    flat=e('combine',literal(.5),literal(.5),literal(1.),name='Normal.Flat')
    weight=e('min',e('max',mask,literal(0.),stage='Coverage',name='Mask.LowerBound'),literal(1.),stage='Coverage',name='Mask.UpperBound')
    normal=e('mix',flat,decoded,weight,stage='Coverage',name='Normal.MaskedEncoded')
    return {'Normal':normal,'Roughness':b,'Metallic':alpha,'Nx':x,'Ny':y,'Nz':z}


def build_group(bpy):
    """Lower the narrow shared expression DAG with explicit group-input bindings.

    All generated identities express operation purpose; layout follows semantic
    stage and dependency depth. There is no vendor-library loader or dependency.
    """
    name='DR.PackedDecal.'+RECIPE.rsplit('_',1)[-1]
    existing=bpy.data.node_groups.get(name)
    if existing is not None:
        if existing.get('dr.packed_decal.recipe')!=RECIPE:raise ValueError('Unproved packed decal name collision')
        return existing
    tree=bpy.data.node_groups.new(name,'ShaderNodeTree')
    try:
        def socket(name,direction,kind,default):
            s=tree.interface.new_socket(name=name,in_out=direction,socket_type=kind)
            s.default_value=default
            if kind=='NodeSocketFloat':s.min_value=-3.4028234663852886e38;s.max_value=3.4028234663852886e38
        # Creation order retains stable external socket identifiers, not layout.
        socket('Color','INPUT','NodeSocketColor',(.5,.5,1.,1.))
        socket('Alpha','INPUT','NodeSocketFloat',0.)
        socket('Normal','OUTPUT','NodeSocketColor',(.5,.5,1.,1.))
        for key,default in [('Roughness',.5),('Metallic',0.),('Nx',0.),('Ny',0.),('Nz',1.)]:socket(key,'OUTPUT','NodeSocketFloat',default)
        socket('Mask','INPUT','NodeSocketFloat',0.)
        nodes=tree.nodes;links=tree.links;stages={};stage_nodes={};depths={}
        def node(kind,stage,role,depth=0):
            n=nodes.new(kind);n.name='DR.'+stage+'.'+role;n.label=role.replace('.',' ')
            n['dr.semantic_id']=n.name;n['dr.stage']='DR.'+stage;n['dr.operation.factory']=1;n['dr.operation.role']=role
            stage_nodes.setdefault(stage,[]).append(n);depths[n.as_pointer()]=depth
            return n
        inputs=node('NodeGroupInput','Source','PackedDecal.Inputs')
        outputs=node('NodeGroupOutput','Output','PackedDecal.Outputs');outputs.is_active_output=True
        bindings={key:inputs.outputs[key] for key in ('Color','Alpha','Mask')}
        memo={};splits={}
        def assign(value,target):
            if hasattr(value,'node'):links.new(value,target)
            else:
                if isinstance(value,(tuple,list)) and len(value)==3 and len(target.default_value)==4:value=tuple(value)+(1.,)
                target.default_value=value
        def emit(e):
            prior=memo.get(id(e))
            if prior is not None and prior[0] is e:return prior[1]
            op=e.op
            if op=='literal':return e.args[0]
            if op=='attribute':return bindings[e.attrs['name']]
            args=[emit(a) for a in e.args]
            stage=e.attrs.get('stage','DR.Surface').removeprefix('DR.');role=e.attrs['name']
            dep=1+max([depths.get(a.node.as_pointer(),0) for a in args if hasattr(a,'node')]+[-1])
            if op=='channel':
                key=id(e.args[0]);split=splits.get(key)
                if split is None:
                    split=node('ShaderNodeSeparateColor','Source','PackedDecal.Channels',dep);split.mode='RGB';assign(args[0],split.inputs[0]);splits[key]=split
                result=split.outputs[e.attrs['index']]
            elif op=='combine':
                n=node('ShaderNodeCombineColor',stage,role,dep);n.mode='RGB'
                for value,target in zip(args,n.inputs):assign(value,target)
                result=n.outputs[0]
            elif op=='mix':
                n=node('ShaderNodeMixRGB',stage,role,dep);n.blend_type='MIX';n.use_clamp=False
                assign(args[2],n.inputs[0]);assign(args[0],n.inputs[1]);assign(args[1],n.inputs[2]);result=n.outputs[0]
            else:
                operations={'add':'ADD','subtract':'SUBTRACT','multiply':'MULTIPLY','min':'MINIMUM','max':'MAXIMUM','sqrt':'SQRT'}
                if op not in operations:raise ValueError('Unproved decoder operation '+op)
                n=node('ShaderNodeMath',stage,role,dep);n.operation=operations[op];n.use_clamp=False
                for value,target in zip(args,n.inputs):assign(value,target)
                result=n.outputs[0]
            memo[id(e)]=(e,result);return result
        for key,e in contract_dag().items():assign(emit(e),outputs.inputs[key])
        x=0.
        for stage in ('Source','Surface','Coverage','Output'):
            members=stage_nodes.get(stage,[])
            if not members:continue
            f=nodes.new('NodeFrame');f.name='DR.'+stage+'.PackedDecal';f.label=stage;f['dr.semantic_id']=f.name;f['dr.stage']='DR.'+stage;f['dr.operation.stage_frame']=1
            bins={}
            # Only depth within each semantic stage determines its columns.
            for n in members:bins.setdefault(depths[n.as_pointer()],[]).append(n)
            for col,(_,members_in_column) in enumerate(sorted(bins.items())):
                for row,n in enumerate(sorted(members_in_column,key=lambda n:n.name)):n.parent=f;n.location=(col*230.,-row*160.)
            f.location=(x,0.);x+=len(bins)*230.+320.
        tree['dr.packed_decal.recipe']=RECIPE;tree['dr.definition.legacy_key']='DR Packed Decal Contract'
        tree['dr.provenance']='Native enabled Urn SM5 channels plus retained interface/output probes049/050; original semantic DAG construction'
        tree['dr.compatibility.alpha']='Separate input pass-through, GT caller binds native texture Alpha'
        return tree
    except BaseException:
        bpy.data.node_groups.remove(tree);raise
