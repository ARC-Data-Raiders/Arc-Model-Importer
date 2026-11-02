"""Native M_Character_Layered decal composition shared by every garment.

Evidence: Striker SM5 map371F58EF65229FA4C04D250848A2F0F6ADDA80D0,
pixel E8F62A20B02C6C74DFF08E62F8489A21847AD26A; independently corroborated
by enabled Urn map4D173095A6A3A14CFD3AA8D6B1EF73928A29B91E. Native samples
are composed before garment wear, not painted over the finished BSDF inputs.
"""
from __future__ import annotations
import math


def _math(api, nodes, links, role, operation, a, b=None):
    n=api._dr_ops.new_operation(nodes,'ShaderNodeMath',stage='Coverage',role='Decal.'+role)
    n.operation=operation
    for value,socket in zip((a,b),n.inputs):
        if value is None:continue
        if hasattr(value,'node'):links.new(value,socket)
        else:socket.default_value=value
    return n.outputs[0]


def _mix(api,nodes,links,role,a,b,weight,*,vector=False):
    n=api._dr_ops.new_operation(nodes,'ShaderNodeMix',stage='Surface',role='Decal.'+role)
    n.data_type='VECTOR' if vector else ('RGBA' if getattr(a,'type',None)=='RGBA' else 'FLOAT')
    n.clamp_factor=True
    sockets=(n.inputs[0],n.inputs[4],n.inputs[5]) if vector else ((n.inputs[0],n.inputs[6],n.inputs[7]) if n.data_type=='RGBA' else (n.inputs[0],n.inputs[2],n.inputs[3]))
    for value,socket in zip((weight,a,b),sockets):
        if hasattr(value,'node'):links.new(value,socket)
        else:socket.default_value=value
    return n.outputs[1] if vector else n.outputs[2 if n.data_type=='RGBA' else 0]


def native_uv(api,nodes,links,uv,aspect=1.):
    """Native frac(abs(U))/sign fold, with V left unwrapped; PNG-space output.

    Positive U uses frac(abs(U)); U<=0 uses 1-frac(abs(U)). At exact seam
    endpoints this intentionally differs from a blanket Vector FRACTION.
    """
    ops=api._dr_ops
    sep=ops.new_operation(nodes,'ShaderNodeSeparateXYZ',stage='Coordinates',role='Decal.MeshUV.Split');links.new(uv,sep.inputs[0])
    ab=_math(api,nodes,links,'UV.AbsU','ABSOLUTE',sep.outputs['X'])
    fr=_math(api,nodes,links,'UV.FractionU','FRACT',ab)
    other=_math(api,nodes,links,'UV.OtherHalf','SUBTRACT',1.,fr)
    positive=_math(api,nodes,links,'UV.PositiveU','GREATER_THAN',sep.outputs['X'],0.)
    u=_mix(api,nodes,links,'UV.NativeFold',other,fr,positive)
    v=_math(api,nodes,links,'UV.CenterV','SUBTRACT',sep.outputs['Y'],.5)
    v=_math(api,nodes,links,'UV.MeshAspect','MULTIPLY',v,float(aspect))
    v=_math(api,nodes,links,'UV.RestoreV','ADD',v,.5)
    out=ops.new_operation(nodes,'ShaderNodeCombineXYZ',stage='Coordinates',role='Decal.MeshUV.NativeTile')
    links.new(u,out.inputs['X']);links.new(v,out.inputs['Y']);links.new(sep.outputs['Z'],out.inputs['Z'])
    return out.outputs[0]


def domain_mask(api,nodes,links,uv,tiling,index):
    sep=api._dr_ops.new_operation(nodes,'ShaderNodeSeparateXYZ',stage='Coverage',role=f'Decal.Slot{index}.Domain');links.new(uv,sep.inputs[0]);parts=[]
    for axis in ('X','Y'):
        lo=_math(api,nodes,links,f'Slot{index}.Domain.{axis}.Lo','GREATER_THAN',sep.outputs[axis],0.)
        hi=_math(api,nodes,links,f'Slot{index}.Domain.{axis}.Hi','LESS_THAN',sep.outputs[axis],1.)
        parts.append(_math(api,nodes,links,f'Slot{index}.Domain.{axis}','MULTIPLY',lo,hi))
    rect=_math(api,nodes,links,f'Slot{index}.Domain.Rectangle','MULTIPLY',*parts)
    result=_math(api,nodes,links,f'Slot{index}.Domain.Tiling','ADD',rect,tiling);result.node.use_clamp=True
    return result


def wear_multiplier(api,nodes,links,slots):
    value=1.
    for slot in slots:value=_mix(api,nodes,links,f'Slot{slot["index"]}.WearOverride',value,slot['wear'],slot['alpha'])
    return value


def _stack(api,nodes,links,slots,key,control=None):
    """Native first-enabled sample, successive lerps, and summed coverage.

    Do not use the partially worn base surface as the running decal accumulator.
    UseDecal makes the first sample selection work across colourway swaps.
    """
    value=None;active=0.;total=0.
    for slot in slots:
        idx=slot['index'];weight=slot['alpha']
        if control:weight=_math(api,nodes,links,f'Slot{idx}.{key}.Weight','MULTIPLY',weight,slot[control])
        sample=slot[key]
        if value is None:value=sample
        else:
            mixed=_mix(api,nodes,links,f'Slot{idx}.{key}.Accumulate',value,sample,weight)
            # If every previous slot was disabled, this is the native first sample.
            value=_mix(api,nodes,links,f'Slot{idx}.{key}.FirstEnabled',sample,mixed,active)
        active=_math(api,nodes,links,f'Slot{idx}.{key}.Enabled','ADD',active,slot['use']);active.node.use_clamp=True
        total=_math(api,nodes,links,f'Slot{idx}.{key}.Coverage','ADD',total,weight)
    if hasattr(total,'node'):
        # Clamp only the final sum: each slot's contribution keeps its own weight.
        total=_math(api,nodes,links,key+'.CoverageClamp','MULTIPLY',total,1.);total.node.use_clamp=True
    return value,total


def color(api,nodes,links,base,slots):
    value,weight=_stack(api,nodes,links,slots,'color')
    return _mix(api,nodes,links,'Color.OnGarment',base,value,weight) if value is not None else base


def surface(api,nodes,links,roughness,metallic,slots):
    results=[]
    for key,base in [('roughness',roughness),('metallic',metallic)]:
        value,weight=_stack(api,nodes,links,slots,key,'surface')
        results.append(_mix(api,nodes,links,key+'.OnGarment',base,value,weight) if value is not None else base)
    return tuple(results)


def normals(api,nodes,links,mesh,detail,slots):
    """Preserve native mesh/detail normals unless their authored controls mute them.

    RG decal XY is rotated back out of placement space and Z is reconstructed
    after compositing. RNM adds that field to mesh normals, then clothing detail.
    """
    ops=api._dr_ops;xy=None;active=0.;base_override=0.;detail_override=0.
    for slot in slots:
        idx=slot['index'];decoder=slot['decoder']
        combine=ops.new_operation(nodes,'ShaderNodeCombineXYZ',stage='Details',role=f'Decal.Slot{idx}.SignedXY')
        links.new(decoder.outputs['Nx'],combine.inputs['X']);links.new(decoder.outputs['Ny'],combine.inputs['Y'])
        rot=ops.new_operation(nodes,'ShaderNodeVectorRotate',stage='Details',role=f'Decal.Slot{idx}.NormalRotation');rot.rotation_type='AXIS_ANGLE';rot.inputs['Axis'].default_value=(0.,0.,1.);links.new(combine.outputs[0],rot.inputs['Vector']);links.new(slot['normal_rotation'],rot.inputs['Angle'])
        scale=ops.new_operation(nodes,'ShaderNodeVectorMath',stage='Details',role=f'Decal.Slot{idx}.NormalDomain');scale.operation='SCALE';links.new(rot.outputs[0],scale.inputs[0]);links.new(slot['domain'],scale.inputs['Scale']);sample=scale.outputs[0]
        weight=_math(api,nodes,links,f'Slot{idx}.DetailOverrideWeight','MULTIPLY',slot['alpha'],slot['detail_normal'])
        if xy is None:xy=sample
        else:
            mixed=_mix(api,nodes,links,f'Slot{idx}.NormalXY.Accumulate',xy,sample,weight,vector=True)
            xy=_mix(api,nodes,links,f'Slot{idx}.NormalXY.FirstEnabled',sample,mixed,active,vector=True)
        active=_math(api,nodes,links,f'Slot{idx}.NormalXY.Enabled','ADD',active,slot['use']);active.node.use_clamp=True
        base_override=_mix(api,nodes,links,f'Slot{idx}.BaseNormalOverride',base_override,slot['base_normal'],slot['alpha'])
        detail_override=_math(api,nodes,links,f'Slot{idx}.DetailNormalOverrideSum','ADD',detail_override,weight)
    if xy is None:return mesh,detail
    detail_override=_math(api,nodes,links,'DetailNormalOverrideClamp','MULTIPLY',detail_override,1.);detail_override.node.use_clamp=True
    mesh=_mix(api,nodes,links,'MeshNormal.Override',mesh,(0.,0.,1.),base_override,vector=True)
    detail=_mix(api,nodes,links,'ClothingNormal.Override',detail,(0.,0.,1.),detail_override,vector=True)
    dot=ops.new_operation(nodes,'ShaderNodeVectorMath',stage='Details',role='Decal.NormalXY.Radial');dot.operation='DOT_PRODUCT';links.new(xy,dot.inputs[0]);links.new(xy,dot.inputs[1])
    z=_math(api,nodes,links,'Normal.ZSquared','SUBTRACT',1.,dot.outputs['Value']);z=_math(api,nodes,links,'Normal.ZNonnegative','MAXIMUM',z,0.);z=_math(api,nodes,links,'Normal.Z','SQRT',z)
    split=ops.new_operation(nodes,'ShaderNodeSeparateXYZ',stage='Details',role='Decal.NormalXY.Split');links.new(xy,split.inputs[0]);normal=ops.new_operation(nodes,'ShaderNodeCombineXYZ',stage='Details',role='Decal.Normal.Reconstructed');links.new(split.outputs['X'],normal.inputs['X']);links.new(split.outputs['Y'],normal.inputs['Y']);links.new(z,normal.inputs['Z'])
    rnm=ops.new_operation(nodes,'ShaderNodeGroup',stage='Details',role='Decal.MeshNormal.RNM');rnm.node_tree=api.ensure_gt_rnm_group();links.new(mesh,rnm.inputs['Base']);links.new(normal.outputs[0],rnm.inputs['Detail'])
    return rnm.outputs['Normal'],detail
