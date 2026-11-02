"""Native layered medium-normal mask and strength (Urn SM5 4D173095...).

The scalar mask, strength and RNM order follow native shader arithmetic.
Blender lacks explicit image SampleLevel: in-memory BOX mip reconstruction
approximates the authored OCM mip pyramid, then uses the native five taps.
"""
from __future__ import annotations
import hashlib, math, os
import bpy
import numpy as np
from . import dr_primary_operations as _dr_ops
from .dr_primary_graph.identity import assign, write_locations


def mip_image(source, level):
    level=max(0,min(int(level),int(math.log2(max(source.size)))))
    path=bpy.path.abspath(source.filepath) if source.filepath else ''
    try:stamp=(os.stat(path).st_mtime_ns,os.stat(path).st_size)
    except OSError:stamp=('memory',source.name)
    identity=str((path,source.size[:],level,stamp))
    name='ARC OCM Medium Mip '+hashlib.sha256(identity.encode()).hexdigest()[:16]
    image=bpy.data.images.get(name)
    if image is not None and image.get('arc_medium_mip')==identity:return image
    width,height=source.size
    values=np.empty(width*height*4,np.float32);source.pixels.foreach_get(values)
    values=values.reshape(height,width,4)
    for _ in range(level):
        h,w=values.shape[:2]
        if h%2:values=np.concatenate((values,values[-1:]),axis=0)
        if w%2:values=np.concatenate((values,values[:,-1:]),axis=1)
        values=values.reshape(values.shape[0]//2,2,values.shape[1]//2,2,4).mean(axis=(1,3))
    image=bpy.data.images.new(name,width=values.shape[1],height=values.shape[0],float_buffer=True)
    image.colorspace_settings.name='Non-Color';image.alpha_mode='CHANNEL_PACKED'
    image.pixels.foreach_set(values.ravel());image.update();image.pack()
    image['arc_medium_mip']=identity
    image['arc_medium_mip_note']='BOX reconstructed from exported base mip; stored UE mip filtering/compression can differ.'
    return image


class Graph:
    def __init__(self,nodes,links):self.nodes=nodes;self.links=links
    def put(self,socket,value):
        if hasattr(value,'node'):self.links.new(value,socket)
        else:socket.default_value=value
    @_dr_ops.operation_build
    def math(self,op,a,b=0,name=''):
        n=_dr_ops.new_operation(self.nodes, 'ShaderNodeMath', name or 'GT_Medium_' + op, stage=None, role=None);assign(n,'operation',op);pass;_dr_ops.set_label(n, _dr_ops.legacy_name(n))
        self.put(n.inputs[0],a);self.put(n.inputs[1],b);return n.outputs[0]
    @_dr_ops.operation_build
    def vec(self,op,a,b,name=''):
        n=_dr_ops.new_operation(self.nodes, 'ShaderNodeVectorMath', name or 'GT_Medium_' + op, stage=None, role=None);assign(n,'operation',op);pass
        self.put(n.inputs[0],a);self.put(n.inputs[3 if op=='SCALE' else 1],b);return n.outputs[0]


@_dr_ops.operation_build
def mask_strength(nodes,links,curvature,value,softness,strength,zone=1):
    g=Graph(nodes,links)
    distance=g.math('ABSOLUTE',g.math('SUBTRACT',curvature,value))
    ridge=g.math('SUBTRACT',1,g.math('MINIMUM',g.math('MULTIPLY',distance,2),1))
    ratio=g.math('DIVIDE',g.math('SUBTRACT',ridge,g.math('SUBTRACT',1,softness)),softness)
    mask=g.math('MINIMUM',g.math('MAXIMUM',ratio,0),1)
    return g.math('MULTIPLY',mask,strength,f'GT_z{zone}_MediumFactor')


@_dr_ops.operation_build
def strengthen_normal(nodes,links,normal,factor):
    g=Graph(nodes,links)
    return g.vec('ADD',g.vec('SCALE',g.vec('SUBTRACT',normal,(0,0,1)),factor),(0,0,1),'GT_Medium_StrengthNormal')


@_dr_ops.operation_build
def blurred_curvature(nodes,links,source,uv,offset,mip):
    from .gt_outfit_pbr import orient_uv
    g=Graph(nodes,links);lo=max(0,int(math.floor(mip)));hi=max(0,int(math.ceil(mip)));weight=mip-lo
    levels=[(mip_image(source,lo),1-weight)] if lo!=hi else [(mip_image(source,lo),1)]
    if lo!=hi:levels.append((mip_image(source,hi),weight))
    total=None
    for image,lodweight in levels:
        for i,(x,y,tapweight) in enumerate(((offset,0,.1875),(-offset,0,.1875),(0,offset,.1875),(0,-offset,.1875),(0,0,.25))):
            coord=g.vec('ADD',uv,(x,y,0))
            coord=orient_uv(nodes,links,coord,f'GT_Medium_ImageUV_{lo if image==levels[0][0] else hi}_{i}',0,0)
            tex=_dr_ops.new_operation(nodes, 'ShaderNodeTexImage', f'GT_Medium_BlurMip{(lo if image == levels[0][0] else hi)}_Tap{i}', stage='LayerInputs', role='Medium.Curvature.tex');pass;_dr_ops.set_label(tex, 'Native OCM.G blur (reconstructed mip)');tex.image=image;assign(tex,'interpolation','Linear');assign(tex,'extension','REPEAT');links.new(coord,tex.inputs['Vector'])
            sep=_dr_ops.new_operation(nodes, 'ShaderNodeSeparateColor', None, stage='Details', role='Medium.Curvature.SeparateChannels');links.new(tex.outputs['Color'],sep.inputs['Color'])
            value=g.math('MULTIPLY',sep.outputs['Green'],tapweight*lodweight)
            total=value if total is None else g.math('ADD',total,value)
    return total


@_dr_ops.operation_build
def wire_medium(nodes,links,detail,normals,zone_eq,zone_scalars,mi_data,ocm_image,uv0,*,curvature=None):
    if not normals:return detail
    from . import clothing as C,gt_outfit_pbr as G
    if curvature is None:
        if ocm_image is None:return detail
        offset=C._mi_global_float(zone_scalars,'BlurOffset',.0075,mi_data=mi_data)
        mip=C._mi_global_float(zone_scalars,'BlurMipLevel',5,mi_data=mi_data)
        curvature=blurred_curvature(nodes,links,ocm_image,uv0,offset,mip)
    from . import param_bind as _pb
    factors={}
    # Bound parameters: a colourway swap rewrites the mask and strength (0 turns the zone off).
    for zone in normals:
        factors[zone]=mask_strength(nodes,links,curvature,
            _pb.bind(nodes,f'{zone}_MediumMaskValue',C._mi_layer_float(zone_scalars,zone,'MediumMaskValue',.5)),
            _pb.bind(nodes,f'{zone}_MediumMaskSoftness',C._mi_layer_float(zone_scalars,zone,'MediumMaskSoftness',.25)),
            _pb.bind(nodes,f'{zone}_MediumNormalStrength',C._mi_layer_float(zone_scalars,zone,'MediumNormalStrength',0)),zone)
    flat=G._tangent_up(nodes,'GT_Medium_Flat',G.wear_x(),G.colour_row_y('n_med'))
    zero=G._value(nodes,'GT_Medium_Zero','Medium off',0,G.wear_x(),G.colour_row_y('n_med'))
    normal=G.mux_by_zone(nodes,links,None,normals,dtype='VECTOR',start=flat,prefix='GT_mux_nMedium',zone_eq=zone_eq,x_fn=G.colour_col_x,y=G.colour_row_y('n_med'))
    factor=G.mux_by_zone(nodes,links,None,factors,dtype='FLOAT',start=zero,prefix='GT_mux_MediumFactor',zone_eq=zone_eq,x_fn=G.colour_col_x,y=G.colour_row_y('n_med'))
    strengthened=strengthen_normal(nodes,links,normal,factor)
    rnm=_dr_ops.new_operation(nodes, 'ShaderNodeGroup', 'GT_n_medium_rnm', stage='Details', role='Medium.Normal.rnm');rnm.node_tree=G.ensure_gt_rnm_group();pass;_dr_ops.set_label(rnm, 'Native RNM wear + masked medium normal')
    links.new(detail,rnm.inputs['Base']);links.new(strengthened,rnm.inputs['Detail'])
    # One batched write: each node.location write re-runs the whole tree update.
    write_locations(nodes,{node.as_pointer():(G.wear_x()-400+(i%6)*210,G.colour_row_y('n_med')-(i//6)*180) for i,node in enumerate(n for n in nodes if _dr_ops.legacy_name(n).startswith(('GT_Medium_','GT_z')) and n.location.x==0 and n.location.y==0)})
    return rnm.outputs['Normal']

