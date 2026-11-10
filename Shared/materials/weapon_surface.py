"""Weapon-only source reconstruction using the bundled graph compiler.

No dependency on ARC Texturer or any outfit/accessory builder.
Untraced engine stages remain explicit in arc_build_report.
"""
from pathlib import Path
import json
import bpy
from .rebuild.assets import get_index,infer_export_root,relative_package
from .rebuild.records import MaterialLibrary
from .rebuild.graph import Graph

def sample(g,ref,uv=None,color=False,required=True):
    # Default constant maps are authored shader inputs, never a neighbouring gun's atlas.
    stem=ref.rsplit('/',1)[-1].lower()
    if stem in ['t_default_01_red','t_default_01_x','t_default_01_mask']:
        rgba=(1,0,0,1) if stem.endswith('_red') else (0,0,0,1) if stem.endswith('_x') else (1,1,1,1)
        return dict(zip(['r','g','b','a'],rgba),color=rgba,constant=True)
    return g.image(ref,uv,srgb=color,required=required) if ref else None
def weapon_master(r):
    """Master profile build() reconstructs, or None for another family's master."""
    s=r['scalars'];c=r['colors'];t=r['textures']
    master=r['master'].rsplit('/',1)[-1]
    if master.startswith('MI_'):
        if 'LightColor' in c and 'LightIntensity' in s:return 'M_Weapon_Emissive_01'
        if 'Tint' in c and 'Emissive' in s and 'Roughness' in s:return 'M_Weapon_Screen_01'
        if t.get('CR') and any(t.get(k) for k in ['NXM','NOM','NEM','NAM']):return 'M_WeaponPreset_CR+NXX+WPO+ID' if t.get('Weapon ID') else 'M_WeaponPreset_CR+NOM'
        return None
    return master if master.startswith('M_WeaponPreset_') or master in ['M_MasterLight_Emissive_01','M_Weapon_Emissive_01','M_Weapon_Screen_01'] else None
def build(mat,obj,record,index):
    r=record;s=r['scalars'];c=r['colors'];sw=r['switches'];t=r['textures']
    report={'package':r['package'],'source_json':r.get('source_json'),'master':r['master'],'missing_textures':[],'warnings':[],'implemented_stages':[],'engine_parity_verified':False}
    master=weapon_master(r)
    if master is None:raise RuntimeError('Unsupported weapon master '+r['master'].rsplit('/',1)[-1])
    g=Graph(mat,obj,index,report)
    if r['master'].rsplit('/',1)[-1].startswith('MI_'):
        report['warnings'].append('Flat exported material omits master identity; weapon profile selected from explicit packed parameters')
    if master in ['M_MasterLight_Emissive_01','M_Weapon_Emissive_01']:
        col=c.get('LightColor',(1,1,1,1));g.finish(col,roughness=.5,emission=col,emission_strength=s.get('LightIntensity',1))
        report['implemented_stages']=['authored linear LightColor and LightIntensity']
        report['warnings'].append('Master light texture modulation is not shader-traced; uniform authored lamp response retained')
    elif master=='M_Weapon_Screen_01':
        col=c.get('Tint',(1,1,1,1));g.finish(col,roughness=s.get('Roughness',.1),emission=col,emission_strength=s.get('Emissive',1))
        report['implemented_stages']=['authored screen Tint, Roughness and Emissive']
        report['warnings'].append('Animated screen scan/glitch equations are not reconstructed')
    elif master.startswith('M_WeaponPreset_'):
        uv=g.uv(0)
        cr=sample(g,t.get('CR',''),uv,color=True)
        nkey=next((k for k in ['NXM','NOM','NEM','NAM'] if t.get(k)),None)
        normalmap=sample(g,t.get(nkey,''),uv) if nkey else None
        if not cr or not normalmap:raise RuntimeError('Required CR or normal texture unavailable: '+r['name'])
        color=cr['color'];rough=cr['a'];metal=normalmap['a']
        normal=g.decode_normal(normalmap,s.get('Normal Strength',1))
        report['implemented_stages']=['CR RGB/alpha, NXX RG reconstructed Z, DirectX tangent basis, NXX alpha metallic','exact authored texture references; no folder atlas guessing']
        zones=[('r','Red'),('g','Green'),('b','Blue'),('a','Alpha')]
        ids=sample(g,t.get('Weapon ID',''),uv,required=any(sw.get('Enable '+z+' Mask') for _,z in zones)) if t.get('Weapon ID') else None
        for ch,z in zones:
            if not ids or not sw.get('Enable '+z+' Mask',False):continue
            f=ids[ch];tint=c.get('Base - Color - '+z)
            if tint: color=g.mix(color,g.cmul(color,tint,'Base '+z+' tint'),f,'Enable '+z+' Mask')
            mt=s.get('Base - Metalness Tweak - '+z)
            if mt is not None:metal=g.mix(metal,g.clamp(g.add(metal,mt)),f,'Metalness '+z)
            rt=c.get('Base - Roughness Tweak - '+z)
            if rt:
                # Match the reference's supported offset; do not invent R/B semantics.
                rough=g.mix(rough,g.clamp(g.add(rough,rt[1])),f,'Roughness offset '+z)
                if rt[0]!=0 or rt[2]!=0:report['warnings'].append('Untraced roughness tweak R/B components in '+z+': '+str(rt))
        active=[(ch,z) for ch,z in zones if sw.get('Enable '+z+' Mask',False) and sw.get('Pattern - Use - '+z,False)]
        if active:
            puv=g.uv(1 if sw.get('Use UV1',False) else 0)
            u,v,_=g.split(puv);tile=s.get('Paintjob - masterTiling',1)
            puv=g.combine(g.add(g.mul(u,tile*s.get('Paintjob - uTiling',1)),s.get('Paintjob - uOffset',0)),g.add(g.mul(v,tile*s.get('Paintjob - vTiling',1)),s.get('Paintjob - vOffset',0)))
            pattern=sample(g,t.get('Paintjob Pattern',''),puv,color=True)
            mask=sample(g,t.get('Paintjob Pattern Mask',''),uv) if sw.get('Use Paintjob Pattern Mask',False) else ids
            if not pattern or not mask:raise RuntimeError('Active pattern texture/mask missing: '+r['name'])
            for ch,z in active:
                pc=pattern['color']
                if sw.get('Set ID Specific Pattern Color - '+z,False):pc=g.cmul(pc,c.get('Specific Pattern Color - '+z,(1,1,1,1)))
                color=g.mix(color,pc,mask[ch],'Authored pattern '+z)
                off=s.get('Pattern Roughness Offset - '+{'Red':'R','Green':'G','Blue':'B','Alpha':'A'}[z],0)
                rough=g.mix(rough,g.clamp(g.add(rough,off)),mask[ch])
            report['implemented_stages'].append('Pattern - Use switches, Use UV1, UV tiling/offset and authored pattern-mask switch')
            report['warnings'].append('Pattern blend and paint-thickness response remain a Blender approximation')
        # Retain the previous scene wear approximation only when the game enables quality.
        # It is labelled explicitly and never allowed to contaminate quality-disabled parts.
        if sw.get('UseWeaponQuality',False) and t.get('Wear'):
            wear=sample(g,t['Wear'],uv,required=False)
            if wear and not wear.get('constant'):
                hotspot=g.clamp(g.div(g.sub(wear['r'],.55),.92-.55))
                hs=g.node('ShaderNodeHueSaturation','Legacy wear approximation (not engine parity)')
                hs.inputs['Saturation'].default_value=.05;hs.inputs['Value'].default_value=.85;g.set(hs.inputs['Color'],wear['color'])
                color=g.mix(color,hs.outputs['Color'],g.mul(hotspot,.55*.35),'Legacy wear scuff')
                rough=g.clamp(g.add(rough,g.mul(wear['a'],.4)))
                report['warnings'].append('Existing quality/wear approximation retained; slider, resistance, rust and layer equations are not engine-verified')
        if any(sw.get('Mid Layer - Use - '+z,False) for _,z in zones):report['warnings'].append('Enabled mid-layer response requires engine shader tracing; reference V1 does not implement it')
        if sw.get('Use Opacity'):
            opacity=sample(g,t.get('Opacity',''),uv);alpha=g.mul(opacity['r'],s.get('MasterOpacityMultiply',1)) if opacity else 1
        else:alpha=1
        emission=normalmap['b'] if nkey=='NEM' else None
        g.finish(color,g.clamp(rough),g.clamp(metal),normal,alpha=alpha,emission=emission)
    else:raise RuntimeError('Unsupported weapon master '+master)
    mat['arc_source_json']=r.get('source_json','');mat['arc_package']=r['package'];mat['arc_mi_path']=r.get('source_json','');mat['arc_weapon_shader_version']=1
    mat['arc_build_report']=json.dumps(report)
    mat.use_backface_culling=not bool(r['settings'].get('TwoSided',r['settings'].get('bTwoSided',False)))
    if report['missing_textures']:raise RuntimeError('Missing textures '+str(report['missing_textures']))
    return mat

def inherit_null_textures(record,library):
    """UE's instance resource skips a null texture override and reads the parent chain
    (e.g. Outlander launcher Detail MI: Opacity=None -> master T_Default_01_Mask)."""
    missing=[k for k,v in record['textures'].items() if not v]
    if not missing:return record
    record=dict(record,textures=dict(record['textures']))
    parent=record.get('parent');seen=set()
    while missing and parent and parent.casefold() not in seen:
        seen.add(parent.casefold())
        try:above=library.load(parent)
        except Exception:break
        for k in list(missing):
            if above['textures'].get(k):record['textures'][k]=above['textures'][k];missing.remove(k)
        parent=above.get('parent')
    return record
def channel_packed(mat):
    """UE samples texture RGB and alpha independently. Blender's default STRAIGHT mode
    makes Cycles premultiply an sRGB image's bytes by its alpha before decoding, whether
    or not the Alpha output is used (Non-Color images are left alone), so the weapon CR
    colour came out scaled by its packed roughness (Hullcracker barrel base 0.07 vs CR
    0.42 linear). The shared native and interop loaders keep the default."""
    seen=set()
    def walk(tree):
        if tree is None or tree in seen:return
        seen.add(tree)
        for n in tree.nodes:
            if n.type=='TEX_IMAGE' and n.image and n.image.alpha_mode=='STRAIGHT':n.image.alpha_mode='CHANNEL_PACKED'
            elif n.type=='GROUP':walk(n.node_tree)
    walk(mat.node_tree)
    return mat
def setup(mat,mi_path,mesh_path='',*,obj=None,record=None):
    from .native.native_families import setup_entrypoint
    if record is None and setup_entrypoint(mat, mi_path, mesh_path, obj=obj, expected_family='weapon', cache_family='weapon'):
        return channel_packed(mat)
    index=get_index(infer_export_root(mi_path))
    if record is None:
        library=MaterialLibrary(index)
        record=inherit_null_textures(library.load(relative_package(Path(mi_path),index.root)),library)
    from . import weapon_fur
    if weapon_fur.is_weapon_fur(record['master']):
        mat['arc_weapon_foreign_master']=record['master']
        return weapon_fur.setup_material(mat,str(mi_path))
    if weapon_master(record) is None:
        # Launcher item presets, skin decal meshes and fur shells are other families'
        # masters: route them like any other mesh slot instead of failing the gun.
        from .dispatch import _dispatch_weapon_slot_material
        _dispatch_weapon_slot_material(mat,str(mi_path),str(mesh_path),Path(str(mi_path)).stem.lower(),'',obj=obj)
        mat['arc_weapon_foreign_master']=record['master']
        return mat
    if obj is None:
        obj=next((o for o in bpy.context.scene.objects if o.type=='MESH' and any(s.material==mat for s in o.material_slots)),None)
    # Fresh material compilation happens before the caller assigns a slot.
    if obj is None:
        class UV: name='UVMap'
        class Data: uv_layers=[UV()]
        class Mesh: data=Data()
        obj=Mesh()
    return channel_packed(build(mat,obj,record,index))
