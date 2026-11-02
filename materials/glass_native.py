"""Current M_Character_Glass_Opaque dry material response.

Current packaged SM5/SM6 dry material equations verified; runtime weather is separate.
Transparent master and weather transport are separate, never inferred here.
"""
from __future__ import annotations
import json
import os

MASTER = '/Game/Pioneer/MaterialLibrary/Character/CustomMaterials/M_Character_Glass_Opaque'
SCALARS = ('DirtTiling','CircularPositionU','CircularPositionV','CircularScaleU','CircularScaleV',
           'CircularSize','DirtSoftness','MaskDirt','DirtAmount','FresnelLow','FresnelHigh',
           'VerticalEnd','VerticalStart','Metallic','RoughnessTiling','RoughnessModifier','DirtRoughness')
COLORS = ('ColorA','ColorB','ColorMultiply','DirtColor')
TEXTURES = ('DirtNormal','DirtMask','Roughness')


def read_parameters(mi_path):
    from .. import textures
    from .common import _parse_flat_mi_json
    path = textures._fs_abspath(mi_path)
    parsed = _parse_flat_mi_json(path)
    # Full parent package, not filename or suggestive scalar/mesh names.
    parent = str(parsed.get('parent_object_path') or '').split('.',1)[0]
    if parent.casefold() != MASTER.casefold():
        return None
    scalars = dict(parsed.get('scalars') or {})
    colors = dict(parsed.get('colours') or [])
    texture_refs = dict(parsed.get('textures') or [])
    missing = [name for name in SCALARS if name not in scalars]
    missing += [name for name in COLORS if name not in colors]
    missing += [name for name in TEXTURES if not texture_refs.get(name)]
    if missing:
        raise ValueError('Native opaque current defaults/bindings unresolved: '+repr(missing))
    return {'path':path,'master':MASTER,'scalars':{name:float(scalars[name]) for name in SCALARS},
            'colors':{name:tuple(colors[name]) for name in COLORS},
            'textures':{name:texture_refs[name] for name in TEXTURES},
            'signature':textures.material_lineage_signature(path),'two_sided':bool(parsed.get('two_sided'))}


def build_response(g, record, images, uv_blender, uv_name='UV0', *, view_dot=None):
    """Named sockets aid numeric render probes; input vectors follow Blender UVs."""
    p,c=record['scalars'],record['colors']
    values={name:g.value(p[name],name) for name in SCALARS}
    def sat_div(numerator, denominator):
        # Native reciprocal-zero + _sat: +inf->1, -inf/NaN->0.
        # Blender Divide(0) returns0, so preserve native step behaviour explicitly.
        valid=g.math('GREATER_THAN',g.math('ABSOLUTE',denominator),0)
        ordinary=g.clamp(g.math('DIVIDE',numerator,denominator))
        zero=g.math('GREATER_THAN',numerator,0)
        return g.lerp(zero,ordinary,valid)
    u,v,_=g.split(uv_blender)
    native_v=g.math('SUBTRACT',1,v)
    def tiled_uv(tiling):
        return g.combine(g.math('MULTIPLY',u,tiling),
                         g.math('SUBTRACT',1,g.math('MULTIPLY',native_v,tiling)),0)
    dirt_uv=tiled_uv(values['DirtTiling'])
    dirt_normal=g.texture(images['DirtNormal'],dirt_uv,'Native DirtNormal data RG')
    dirt_mask=g.texture(images['DirtMask'],dirt_uv,'Native DirtMask data R')
    rough_map=g.texture(images['Roughness'],tiled_uv(values['RoughnessTiling']),'Native Roughness data R')
    # Frac is applied in UE UV coordinates, before elliptical distance.
    du=g.math('SUBTRACT',values['CircularPositionU'],g.math('FRACT',u))
    dv=g.math('SUBTRACT',values['CircularPositionV'],g.math('FRACT',native_v))
    radial=g.math('MINIMUM',g.vector('LENGTH',g.combine(
        g.math('DIVIDE',du,values['CircularScaleU']),g.math('DIVIDE',dv,values['CircularScaleV']),0)),1)
    valid_scale=g.math('MULTIPLY',g.math('GREATER_THAN',g.math('ABSOLUTE',values['CircularScaleU']),0),
                       g.math('GREATER_THAN',g.math('ABSOLUTE',values['CircularScaleV']),0))
    # Native min(length(inf/NaN),1)=1, including zero-scale authored defaults.
    radial=g.lerp(1,radial,valid_scale)
    circle=g.math('SUBTRACT',1,sat_div(g.math('SUBTRACT',radial,values['CircularSize']),values['DirtSoftness']))
    gate=g.math('SUBTRACT',1,g.math('MULTIPLY',circle,values['MaskDirt']))
    mask_r=g.split(dirt_mask.outputs['Color'])[0]
    dirt=sat_div(g.math('SUBTRACT',g.math('MULTIPLY',mask_r,gate),
                    g.math('SUBTRACT',1,values['DirtAmount'])),values['DirtSoftness'])
    tangent=g.vector('NORMALIZE',g.vlerp((0,0,1),g.decode_rg(dirt_normal.outputs['Color']),dirt))
    normal=g.normal_to_world(tangent,uv_name)
    if view_dot is None:
        geometry=g.node('ShaderNodeNewGeometry','Native opaque view')
        view_dot=g.vector('DOT_PRODUCT',normal,geometry.outputs['Incoming'])
    facing=sat_div(g.math('SUBTRACT',g.clamp(view_dot),values['FresnelLow']),
                   g.math('SUBTRACT',values['FresnelHigh'],values['FresnelLow']))
    vertical=sat_div(g.math('SUBTRACT',native_v,values['VerticalEnd']),
                     g.math('SUBTRACT',values['VerticalStart'],values['VerticalEnd']))
    weight=g.math('MULTIPLY',facing,vertical)
    color=g.vector('MULTIPLY',g.vlerp(g.rgb(c['ColorA'],'ColorA'),g.rgb(c['ColorB'],'ColorB'),weight),
                   g.rgb(c['ColorMultiply'],'ColorMultiply'))
    color=g.vlerp(color,g.rgb(c['DirtColor'],'DirtColor'),dirt)
    r,gc,b=g.split(color);color=g.combine(g.clamp(r),g.clamp(gc),g.clamp(b))
    base_rough=g.math('ADD',g.split(rough_map.outputs['Color'])[0],values['RoughnessModifier'])
    rough=g.clamp(g.lerp(base_rough,values['DirtRoughness'],dirt))
    # Native metallic is attenuated by SNOW, not dirt. Dry snow=0 retains the authored value.
    metal=g.clamp(values['Metallic'])
    return {'color':color,'roughness':rough,'metallic':metal,'normal':normal,'tangent_normal':tangent,
            'circle':circle,'dirt':dirt,'view_weight':weight,'alpha':1,'specular':.5,'transmission':0}


def setup_native_opaque(mat, mi_path, psk_path='', *, record=None, dry_snapshot=False):
    """ReturnFalse for other masters; dry state must be explicitly authorized."""
    import bpy
    from .. import textures
    from .common import _resolve_mi_texture_path
    from .character_body import ResponseGraph,image_for_role
    record=record if record is not None else read_parameters(mi_path)
    if record is None:
        return False
    if not dry_snapshot:
        raise ValueError('Opaque candidate implements explicit dry snapshot only; weather state not inferred')
    loaded,missing={},[]
    for role,reference in record['textures'].items():
        path=_resolve_mi_texture_path(reference)
        image=image_for_role(path,non_color=True) if path else None
        if image is None:missing.append({'role':role,'reference':reference})
        else:loaded[role]=image
    if missing:
        mat['arc_glass_missing']=json.dumps(missing)
        raise FileNotFoundError('Native opaque role textures unresolved: '+repr(missing))
    mat.use_nodes=True;nodes,links=mat.node_tree.nodes,mat.node_tree.links;nodes.clear()
    g=ResponseGraph(nodes,links,'NativeOpaque_');uv=g.node('ShaderNodeUVMap','UV0');uv.uv_map='UV0'
    response=build_response(g,record,loaded,uv.outputs[0],uv.uv_map)
    p=g.node('ShaderNodeBsdfPrincipled','Native opaque BSDF')
    for name,key in [('Base Color','color'),('Roughness','roughness'),('Metallic','metallic'),('Normal','normal')]:
        g.put(p.inputs[name],response[key])
    p.inputs['IOR'].default_value=1.5;p.inputs['Specular IOR Level'].default_value=.5
    p.inputs['Alpha'].default_value=1;p.inputs['Transmission Weight'].default_value=0
    p.inputs['Coat Weight'].default_value=0;p.inputs['Sheen Weight'].default_value=0
    p.inputs['Specular Tint'].default_value=(1,1,1,1)
    output=g.node('ShaderNodeOutputMaterial','Native opaque output');g.put(output.inputs['Surface'],p.outputs[0])
    mat['arc_mi_path']=textures._fs_abspath(mi_path);mat['arc_skin_json']=mat['arc_mi_path']
    mat['arc_glass_setup']='native-character-glass-opaque-dry-v1';mat['arc_mi_family']='character_glass_opaque'
    mat['arc_glass_master']=MASTER;mat['arc_glass_source_signature']=json.dumps(record.get('signature'),default=str)
    mat['arc_glass_engine_parity_verified']=False
    mat['arc_glass_scope']='Current packaged SM5/SM6 dry material response; runtime weather and lighting not verified'
    mat['arc_glass_ignored_leaf_controls']='Opacity/RefractionDepthBias are not uniforms in this native opaque shader'
    mat['arc_glass_texture_roles']=json.dumps(record['textures'],sort_keys=True)
    mat.use_backface_culling=not bool(record.get('two_sided'))
    if hasattr(mat,'surface_render_method'):mat.surface_render_method='DITHERED'
    return True
