"""Native M_Character_Layered decal composition shared by every garment.

Evidence: Striker SM5 map371F58EF65229FA4C04D250848A2F0F6ADDA80D0,
pixel E8F62A20B02C6C74DFF08E62F8489A21847AD26A; independently corroborated
by enabled Urn map4D173095A6A3A14CFD3AA8D6B1EF73928A29B91E. Native samples
are composed before garment wear, not painted over the finished BSDF inputs.
"""
from __future__ import annotations
import math


_GROUP_VER=1


def _feed(links,socket,value):
    if value is None:return
    if hasattr(value,'node'):links.new(value,socket)
    else:socket.default_value=value


def _group(api,name,ins,outs,build):
    """One shared definition per process (versioned); parameter nodes stay top-level."""
    if api is None:return ('inline',{nm:dv for nm,_t,dv in ins},build)
    tree,created=api._gt_ng_fresh(name,_GROUP_VER)
    if not created:return tree
    for nm,typ,dv in ins:api._gt_ng_sock(tree,nm,'INPUT',typ,dv)
    for nm,typ in outs:api._gt_ng_sock(tree,nm,'OUTPUT',typ)
    n,l=tree.nodes,tree.links
    gi=api._dr_ops.new_operation(n,'NodeGroupInput',stage='Source',role='Decal.Group.Input')
    go=api._dr_ops.new_operation(n,'NodeGroupOutput',stage='Output',role='Decal.Group.Output')
    for nm,sock in build(api,n,l,gi.outputs).items():l.new(sock,go.inputs[nm])
    return api._dr_ops.finish_definition(tree)


def _instance(api,nodes,links,tree,stage,role,inputs):
    if isinstance(tree,tuple):  # api-less evaluation (unit tests): run the step body inline
        _k,defaults,build=tree
        return build(api,nodes,links,{nm:(defaults[nm] if v is None else v) for nm,v in {**defaults,**inputs}.items()})
    g=api._dr_ops.new_operation(nodes,'ShaderNodeGroup',stage=stage,role=role);g.node_tree=tree
    for nm,value in inputs.items():_feed(links,g.inputs[nm],value)
    return g.outputs


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
    tree=_group(api,'GT_DecalNativeUV',[('UV','NodeSocketVector',(0.,0.,0.)),('Aspect','NodeSocketFloat',1.)],[('UV','NodeSocketVector')],_build_native_uv)
    return _instance(api,nodes,links,tree,'Coordinates','Decal.MeshUV.NativeTile',{'UV':uv,'Aspect':float(aspect)})['UV']


def _build_native_uv(api,nodes,links,gi):
    ops=api._dr_ops
    sep=ops.new_operation(nodes,'ShaderNodeSeparateXYZ',stage='Coordinates',role='Decal.MeshUV.Split');links.new(gi['UV'],sep.inputs[0])
    ab=_math(api,nodes,links,'UV.AbsU','ABSOLUTE',sep.outputs['X'])
    fr=_math(api,nodes,links,'UV.FractionU','FRACT',ab)
    other=_math(api,nodes,links,'UV.OtherHalf','SUBTRACT',1.,fr)
    positive=_math(api,nodes,links,'UV.PositiveU','GREATER_THAN',sep.outputs['X'],0.)
    u=_mix(api,nodes,links,'UV.NativeFold',other,fr,positive)
    v=_math(api,nodes,links,'UV.CenterV','SUBTRACT',sep.outputs['Y'],.5)
    v=_math(api,nodes,links,'UV.MeshAspect','MULTIPLY',v,gi['Aspect'])
    v=_math(api,nodes,links,'UV.RestoreV','ADD',v,.5)
    out=ops.new_operation(nodes,'ShaderNodeCombineXYZ',stage='Coordinates',role='Decal.MeshUV.NativeTile')
    links.new(u,out.inputs['X']);links.new(v,out.inputs['Y']);links.new(sep.outputs['Z'],out.inputs['Z'])
    return {'UV':out.outputs[0]}


def _build_coverage(api,nodes,links,gi):
    sep=api._dr_ops.new_operation(nodes,'ShaderNodeSeparateXYZ',stage='Coverage',role='Decal.Slot.Domain');links.new(gi['UV'],sep.inputs[0]);parts=[]
    for axis in ('X','Y'):
        lo=_math(api,nodes,links,f'Domain.{axis}.Lo','GREATER_THAN',sep.outputs[axis],0.)
        hi=_math(api,nodes,links,f'Domain.{axis}.Hi','LESS_THAN',sep.outputs[axis],1.)
        parts.append(_math(api,nodes,links,f'Domain.{axis}','MULTIPLY',lo,hi))
    rect=_math(api,nodes,links,'Domain.Rectangle','MULTIPLY',*parts)
    domain=_math(api,nodes,links,'Domain.Tiling','ADD',rect,gi['Tiling']);domain.node.use_clamp=True
    return {'Coverage':_math(api,nodes,links,'ArtworkCoverage','MULTIPLY',gi['Alpha'],domain),
            'NormalCoverage':_math(api,nodes,links,'NormalCoverage','MULTIPLY',domain,gi['Use'])}


def slot_coverage(api,nodes,links,uv,tiling,alpha,use,index):
    """(artwork alpha x domain, domain x UseDecal) in one shared group per slot."""
    tree=_group(api,'GT_DecalSlotCoverage',[('UV','NodeSocketVector',(0.,0.,0.)),('Tiling','NodeSocketFloat',0.),('Alpha','NodeSocketFloat',0.),('Use','NodeSocketFloat',0.)],
        [('Coverage','NodeSocketFloat'),('NormalCoverage','NodeSocketFloat')],_build_coverage)
    out=_instance(api,nodes,links,tree,'Coverage',f'Decal.Slot{index}.Coverage',{'UV':uv,'Tiling':tiling,'Alpha':alpha,'Use':use})
    return out['Coverage'],out['NormalCoverage']


def wear_multiplier(api,nodes,links,slots):
    value=1.
    for slot in slots:value=_mix(api,nodes,links,f'Slot{slot["index"]}.WearOverride',value,slot['wear'],slot['alpha'])
    return value


def _stack(api,nodes,links,slots,key,control):
    """Reference native accumulator for one scalar (same math as the step groups)."""
    value=0.;active=0.;total=0.
    for s in slots:
        w=_math(api,nodes,links,f'Slot{s["index"]}.{key}.Weight','MULTIPLY',s['alpha'],s[control])
        mixed=_mix(api,nodes,links,f'Slot{s["index"]}.{key}.Accumulate',value,s[key],w)
        value=_mix(api,nodes,links,f'Slot{s["index"]}.{key}.FirstEnabled',s[key],mixed,active)
        active=_math(api,nodes,links,f'Slot{s["index"]}.{key}.Enabled','ADD',active,s['use']);active.node.use_clamp=True
        total=_math(api,nodes,links,f'Slot{s["index"]}.{key}.Coverage','ADD',total,w)
    return value,total


def _build_color_step(api,nodes,links,gi):
    mixed=_mix(api,nodes,links,'Color.Accumulate',gi['Value'],gi['Sample'],gi['Weight'])
    value=_mix(api,nodes,links,'Color.FirstEnabled',gi['Sample'],mixed,gi['Active'])
    active=_math(api,nodes,links,'Color.Enabled','ADD',gi['Active'],gi['Use']);active.node.use_clamp=True
    return {'Value':value,'Active':active,'Total':_math(api,nodes,links,'Color.Coverage','ADD',gi['Total'],gi['Weight'])}


def _build_surface_step(api,nodes,links,gi):
    weight=_math(api,nodes,links,'Surface.Weight','MULTIPLY',gi['Alpha'],gi['Control'])
    out={}
    for key in ('Roughness','Metallic'):
        mixed=_mix(api,nodes,links,key+'.Accumulate',gi[key],gi[key+'Sample'],weight)
        out[key]=_mix(api,nodes,links,key+'.FirstEnabled',gi[key+'Sample'],mixed,gi['Active'])
    active=_math(api,nodes,links,'Surface.Enabled','ADD',gi['Active'],gi['Use']);active.node.use_clamp=True
    out['Active']=active;out['Total']=_math(api,nodes,links,'Surface.Coverage','ADD',gi['Total'],weight)
    return out


_F='NodeSocketFloat';_C='NodeSocketColor';_V='NodeSocketVector'


def _coverage_clamp(api,nodes,links,key,total):
    total=_math(api,nodes,links,key+'.CoverageClamp','MULTIPLY',total,1.);total.node.use_clamp=True
    return total


def color(api,nodes,links,base,slots):
    if not slots:return base
    tree=_group(api,'GT_DecalColorStep',[('Value',_C,(0.,0.,0.,1.)),('Sample',_C,(0.,0.,0.,1.)),('Weight',_F,0.),('Active',_F,0.),('Use',_F,0.),('Total',_F,0.)],
        [('Value',_C),('Active',_F),('Total',_F)],_build_color_step)
    value=None;active=0.;total=0.
    for slot in slots:
        o=_instance(api,nodes,links,tree,'Surface',f'Decal.Slot{slot["index"]}.ColorStep',{'Value':value,'Sample':slot['color'],'Weight':slot['alpha'],'Active':active,'Use':slot['use'],'Total':total})
        value,active,total=o['Value'],o['Active'],o['Total']
    return _mix(api,nodes,links,'Color.OnGarment',base,value,_coverage_clamp(api,nodes,links,'color',total))


def surface(api,nodes,links,roughness,metallic,slots):
    if not slots:return roughness,metallic
    tree=_group(api,'GT_DecalSurfaceStep',[('Roughness',_F,0.),('RoughnessSample',_F,0.),('Metallic',_F,0.),('MetallicSample',_F,0.),('Alpha',_F,0.),('Control',_F,0.),('Active',_F,0.),('Use',_F,0.),('Total',_F,0.)],
        [('Roughness',_F),('Metallic',_F),('Active',_F),('Total',_F)],_build_surface_step)
    r=m=None;active=0.;total=0.
    for slot in slots:
        o=_instance(api,nodes,links,tree,'Surface',f'Decal.Slot{slot["index"]}.SurfaceStep',{'Roughness':r,'RoughnessSample':slot['roughness'],'Metallic':m,'MetallicSample':slot['metallic'],
            'Alpha':slot['alpha'],'Control':slot['surface'],'Active':active,'Use':slot['use'],'Total':total})
        r,m,active,total=o['Roughness'],o['Metallic'],o['Active'],o['Total']
    total=_coverage_clamp(api,nodes,links,'surface',total)
    return (_mix(api,nodes,links,'roughness.OnGarment',roughness,r,total),_mix(api,nodes,links,'metallic.OnGarment',metallic,m,total))


def _build_normal_step(api,nodes,links,gi):
    ops=api._dr_ops
    combine=ops.new_operation(nodes,'ShaderNodeCombineXYZ',stage='Details',role='Decal.Slot.SignedXY')
    links.new(gi['Nx'],combine.inputs['X']);links.new(gi['Ny'],combine.inputs['Y'])
    rot=ops.new_operation(nodes,'ShaderNodeVectorRotate',stage='Details',role='Decal.Slot.NormalRotation');rot.rotation_type='AXIS_ANGLE';rot.inputs['Axis'].default_value=(0.,0.,1.);links.new(combine.outputs[0],rot.inputs['Vector']);links.new(gi['Rotation'],rot.inputs['Angle'])
    scale=ops.new_operation(nodes,'ShaderNodeVectorMath',stage='Details',role='Decal.Slot.NormalDomain');scale.operation='SCALE';links.new(rot.outputs[0],scale.inputs[0]);links.new(gi['Domain'],scale.inputs['Scale']);sample=scale.outputs[0]
    weight=_math(api,nodes,links,'DetailOverrideWeight','MULTIPLY',gi['Alpha'],gi['DetailNormal'])
    mixed=_mix(api,nodes,links,'NormalXY.Accumulate',gi['XY'],sample,weight,vector=True)
    xy=_mix(api,nodes,links,'NormalXY.FirstEnabled',sample,mixed,gi['Active'],vector=True)
    active=_math(api,nodes,links,'NormalXY.Enabled','ADD',gi['Active'],gi['Use']);active.node.use_clamp=True
    return {'XY':xy,'Active':active,
        'BaseOverride':_mix(api,nodes,links,'BaseNormalOverride',gi['BaseOverride'],gi['BaseNormal'],gi['Alpha']),
        'DetailOverride':_math(api,nodes,links,'DetailNormalOverrideSum','ADD',gi['DetailOverride'],weight)}


def normals(api,nodes,links,mesh,detail,slots):
    """Preserve native mesh/detail normals unless their authored controls mute them.

    RG decal XY is rotated back out of placement space and Z is reconstructed
    after compositing. RNM adds that field to mesh normals, then clothing detail.
    """
    ops=api._dr_ops;xy=None;active=0.;base_override=0.;detail_override=0.
    if slots:
        tree=_group(api,'GT_DecalNormalStep',[('XY',_V,(0.,0.,0.)),('Nx',_F,0.),('Ny',_F,0.),('Rotation',_F,0.),('Domain',_F,0.),('Alpha',_F,0.),('DetailNormal',_F,0.),
            ('BaseNormal',_F,0.),('Use',_F,0.),('Active',_F,0.),('BaseOverride',_F,0.),('DetailOverride',_F,0.)],
            [('XY',_V),('Active',_F),('BaseOverride',_F),('DetailOverride',_F)],_build_normal_step)
    for slot in slots:
        d=slot['decoder']
        o=_instance(api,nodes,links,tree,'Details',f'Decal.Slot{slot["index"]}.NormalStep',{'XY':xy,'Nx':d.outputs['Nx'],'Ny':d.outputs['Ny'],'Rotation':slot['normal_rotation'],
            'Domain':slot['domain'],'Alpha':slot['alpha'],'DetailNormal':slot['detail_normal'],'BaseNormal':slot['base_normal'],'Use':slot['use'],'Active':active,
            'BaseOverride':base_override,'DetailOverride':detail_override})
        xy,active,base_override,detail_override=o['XY'],o['Active'],o['BaseOverride'],o['DetailOverride']
    if xy is None:return mesh,detail
    detail_override=_math(api,nodes,links,'DetailNormalOverrideClamp','MULTIPLY',detail_override,1.);detail_override.node.use_clamp=True
    mesh=_mix(api,nodes,links,'MeshNormal.Override',mesh,(0.,0.,1.),base_override,vector=True)
    detail=_mix(api,nodes,links,'ClothingNormal.Override',detail,(0.,0.,1.),detail_override,vector=True)
    dot=ops.new_operation(nodes,'ShaderNodeVectorMath',stage='Details',role='Decal.NormalXY.Radial');dot.operation='DOT_PRODUCT';links.new(xy,dot.inputs[0]);links.new(xy,dot.inputs[1])
    z=_math(api,nodes,links,'Normal.ZSquared','SUBTRACT',1.,dot.outputs['Value']);z=_math(api,nodes,links,'Normal.ZNonnegative','MAXIMUM',z,0.);z=_math(api,nodes,links,'Normal.Z','SQRT',z)
    split=ops.new_operation(nodes,'ShaderNodeSeparateXYZ',stage='Details',role='Decal.NormalXY.Split');links.new(xy,split.inputs[0]);normal=ops.new_operation(nodes,'ShaderNodeCombineXYZ',stage='Details',role='Decal.Normal.Reconstructed');links.new(split.outputs['X'],normal.inputs['X']);links.new(split.outputs['Y'],normal.inputs['Y']);links.new(z,normal.inputs['Z'])
    rnm=ops.new_operation(nodes,'ShaderNodeGroup',stage='Details',role='Decal.MeshNormal.RNM');rnm.node_tree=api.ensure_gt_rnm_group();links.new(mesh,rnm.inputs['Base']);links.new(normal.outputs[0],rnm.inputs['Detail'])
    return rnm.outputs['Normal'],detail
