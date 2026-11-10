"""Original character fallback composition with explicit missing-source status.

These are readable Blender approximations, not transplanted shader templates.
Known native texture roles are retained; missing transport data is never invented.
"""
import json,math
import bpy

CONTROLS=('Base Color','Roughness','Metallic','Specular IOR Level','IOR','Subsurface Weight','Subsurface Radius','Subsurface Scale','Subsurface Anisotropy','Coat Weight','Coat Roughness','Coat IOR','Coat Tint')

def surface_controls(material,role):
    if material is None or material.get('arc_head_role')!=role:return {}
    trees=[material.node_tree] if material.use_nodes else [];seen=set()
    while trees:
        tree=trees.pop()
        if tree is None or tree.as_pointer() in seen:continue
        seen.add(tree.as_pointer())
        for node in tree.nodes:
            if node.type=='BSDF_PRINCIPLED':
                values={}
                for name in CONTROLS:
                    socket=node.inputs.get(name)
                    if socket is not None:
                        value=socket.default_value;values[name]=tuple(value) if hasattr(value,'__len__') else value
                return values
            if node.type=='GROUP' and node.node_tree is not None:trees.append(node.node_tree)
    return {}

def apply_controls(principled,values):
    for name,value in values.items():
        socket=principled.inputs.get(name)
        if socket is not None:socket.default_value=value

NATIVE_CHARACTER_PROOF_KEYS = (
    'arc_face_surface_revision','arc_face_surface_note','arc_face_missing_textures',
    'arc_face_surface_failure','arc_face_parameter_sources','arc_face_native_revision',
    'arc_face_normal_revision','arc_face_unresolved_parent','arc_face_surface_setup','arc_teeth_setup',
    'arc_eye_setup','arc_eye_static_approximation','arc_eye_mi_identity',
    'arc_eye_aux_setup','arc_eye_aux_key','arc_eye_aux_parameter_sources',
    'arc_eye_aux_native_identity','arc_eye_aux_unsupported','arc_eye_aux_transmission_node',
    'arc_eye_aux_shadow_mask_node','arc_eye_aux_alpha_node','arc_eyelash_setup',
    'arc_glass_engine_parity_verified','arc_glass_source_signature',
    'arc_weapon_shader_version','arc_scan_reconstruction_version','arc_scan_display',
    'arc_enemy_surface_setup','arc_env_surface_setup','arc_mi_source_signature',
    'arc_mi_family','arc_package',
)

def finish(material,role,missing):
    """Transition provenance as well as graph ownership to an approximation.

    Never leave a stale success report/cache stamp attached to a fallback.
    Explicit user override provenance survives the transition unchanged.
    """
    missing=list(missing)
    raw=material.get('arc_build_report')
    try:previous=json.loads(raw) if isinstance(raw,str) else dict(raw) if raw else {}
    except (TypeError,ValueError):previous={}
    if not isinstance(previous,dict):previous={}
    override=previous.get('user_override')
    for key in tuple(material.keys()):
        if key.startswith('dr_native_') and key!='dr_native_user_override':del material[key]
    for key in NATIVE_CHARACTER_PROOF_KEYS:
        if key in material:del material[key]
    material['arc_head_role']=role;material['dr_character_fallback']=role;material['dr_character_fallback_revision']='original-character-v2'
    material['dr_character_missing_source']=json.dumps(missing);material['dr_character_fallback_note']='Original Blender approximation; verified native transport is unavailable. Existing attribution remains in project credits.'
    material['dr_native_route_diagnostic']='Original character fallback active: '+'; '.join(missing)
    report={'status':'original-character-fallback','family':role,'diagnostics':missing}
    if override is not None:report['user_override']=override
    material['arc_build_report']=json.dumps(report)
    setup={'head':('arc_face_surface_setup','fallback-principled-v2'),
           'teeth':('arc_teeth_setup','fallback-static-v2'),
           'eyes':('arc_eye_setup','fallback-original-v2'),
           'eyelashes':('arc_eyelash_setup','fallback-lashes-v2'),
           'eyeshell':('arc_eye_aux_setup','fallback-eye-aux-v2'),
           'eyeedge':('arc_eye_aux_setup','fallback-eye-aux-v2')}
    if role in setup:
        key,value=setup[role];material[key]=value
    organize(material.node_tree)
    return material

def organize(tree):
    """Original semantic layout for a freshly constructed fallback material."""
    frames={};counts={};positions={'DR.Source':0,'DR.Coordinates':0,'DR.Details':1,'DR.Surface':2,'DR.Coverage':2,'DR.Output':3}
    for node in list(tree.nodes):
        if node.type=='FRAME':continue
        stage='DR.Source' if node.type in ('TEX_IMAGE','RGB','VALUE') else 'DR.Coordinates' if node.type in ('UVMAP','TEX_COORD') else 'DR.Output' if node.type=='OUTPUT_MATERIAL' else 'DR.Surface' if node.type=='BSDF_PRINCIPLED' else 'DR.Details'
        if stage not in frames:
            frame=tree.nodes.new('NodeFrame');frame.name=stage;frame.label=stage;frame.location=(positions[stage]*420,0);frames[stage]=frame;counts[stage]=0
        label=node.label or node.name;node.parent=frames[stage];node.name=stage+'.'+label;node.location=(0,-counts[stage]*170);counts[stage]+=1

class Graph:
    def __init__(self,material):self.nodes=material.node_tree.nodes;self.links=material.node_tree.links;self.nodes.clear()
    def node(self,kind,label):n=self.nodes.new(kind);n.name='DR.'+label;n.label=label;return n
    def put(self,socket,value):
        if hasattr(value,'node'):self.links.new(value,socket)
        else:socket.default_value=value
    def math(self,op,a,b=0.):
        n=self.node('ShaderNodeMath',op);n.operation=op;self.put(n.inputs[0],a);self.put(n.inputs[1],b);return n.outputs[0]
    def color(self,label,value):n=self.node('ShaderNodeRGB',label);n.outputs[0].default_value=tuple(value[:3])+(1.,);return n
    def texture(self,image,label):n=self.node('ShaderNodeTexImage',label);n.image=image;return n
    def decode_normal(self,color):
        split=self.node('ShaderNodeSeparateColor','Packed normal RG');self.put(split.inputs['Color'],color)
        x=self.math('SUBTRACT',self.math('MULTIPLY',split.outputs[0],2.),1.);y=self.math('SUBTRACT',self.math('MULTIPLY',split.outputs[1],2.),1.)
        z=self.math('SQRT',self.math('MAXIMUM',0.,self.math('SUBTRACT',1.,self.math('ADD',self.math('MULTIPLY',x,x),self.math('MULTIPLY',y,y)))))
        combine=self.node('ShaderNodeCombineXYZ','Original DirectX normal encoding')
        for socket,value in zip(combine.inputs,(self.math('ADD',.5,self.math('MULTIPLY',x,.5)),self.math('SUBTRACT',.5,self.math('MULTIPLY',y,.5)),self.math('ADD',.5,self.math('MULTIPLY',z,.5)))):self.put(socket,value)
        normal=self.node('ShaderNodeNormalMap','Native RG normal to Blender tangent basis');normal.uv_map='UV0';self.put(normal.inputs['Color'],combine.outputs[0]);return normal.outputs['Normal']
    def output(self,principled):n=self.node('ShaderNodeOutputMaterial','Character surface');self.put(n.inputs['Surface'],principled.outputs[0])

def static_teeth(color_image,normal_image,previous=None):
    controls=surface_controls(previous,'teeth');material=bpy.data.materials.new('DR.Teeth.StaticFallback');material.use_nodes=True;g=Graph(material);p=g.node('ShaderNodeBsdfPrincipled','Static coated mouth surface')
    for name,value in [('Base Color',(.8,.8,.8,1)),('Roughness',.5),('Specular IOR Level',.5),('Metallic',0.),('Coat Weight',1.),('Coat Roughness',.1)]:p.inputs[name].default_value=value
    missing=['Raw pre-skin normal/complete native material transport unavailable; snow/wet/proximity effects intentionally absent in this static approximation']
    if color_image is not None:g.put(p.inputs['Base Color'],g.texture(color_image,'Authored teeth RGB').outputs['Color'])
    else:missing.append('teeth_color_map unavailable; preserved/default base color')
    if normal_image is not None:g.put(p.inputs['Normal'],g.decode_normal(g.texture(normal_image,'Authored teeth packed RG').outputs['Color']))
    else:missing.append('teeth_normal_map unavailable; mesh normal retained')
    apply_controls(p,controls);g.output(p);material.diffuse_color=tuple(p.inputs['Base Color'].default_value);return finish(material,'teeth',missing)

def lash_material(mask_image,scalars,colors,previous=None,copy_hair=False,hair_color=None):
    material=bpy.data.materials.new('DR.Eyelashes.Fallback');material.use_nodes=True;g=Graph(material);p=g.node('ShaderNodeBsdfPrincipled','Authored lash surface')
    p.inputs['Roughness'].default_value=float(scalars.get('Roughness',.5));p.inputs['Specular IOR Level'].default_value=float(scalars.get('Specular',0.))
    authored=[tuple(colors.get('Eyelash Color',(.036458,.024128,.01709,1.))),tuple(colors.get('Eyelash Color 2',(0,0,0,1)))];material['dr_lash_authored_colors']=json.dumps(authored)
    palette=[tuple(hair_color[:3])+(1.,)]*2 if copy_hair and hair_color is not None else authored
    root=g.color('Lash authored color A',palette[0]);tip=g.color('Lash authored color B',palette[1]);root['dr_lash_color_index']=0;tip['dr_lash_color_index']=1
    missing=['Raw native material lineage unavailable; original lash graph uses verified master R/color/opacity roles with available local controls','Unreal Hair scattering and temporal proximity transport unavailable']
    if mask_image is not None:
        tex=g.texture(mask_image,'Eyelash R mask');split=g.node('ShaderNodeSeparateColor','Authored lash R');g.put(split.inputs['Color'],tex.outputs['Color']);mask=split.outputs[0]
        mix=g.node('ShaderNodeMixRGB','Dual authored lash colors');g.put(mix.inputs[0],mask);g.put(mix.inputs[1],root.outputs[0]);g.put(mix.inputs[2],tip.outputs[0]);g.put(p.inputs['Base Color'],mix.outputs[0])
        powered=g.math('POWER',g.math('MAXIMUM',mask,1e-30),float(scalars.get('OpacityPower',1.)));alpha=g.math('MULTIPLY',g.math('MULTIPLY',powered,float(scalars.get('Lash Opacity',1.))),float(scalars.get('Visibility',1.)));alpha=g.math('MULTIPLY',g.math('MINIMUM',1.,g.math('MAXIMUM',0.,alpha)),g.math('GREATER_THAN',mask,0.));g.put(p.inputs['Alpha'],alpha)
    else:p.inputs['Alpha'].default_value=0.;g.put(p.inputs['Base Color'],root.outputs[0]);missing.append('Eyelash mask unavailable; missing cards remain transparent rather than opaque blocks')
    apply_controls(p, surface_controls(previous, 'eyelashes'));g.output(p);material.surface_render_method='DITHERED';material.use_backface_culling=False;return finish(material,'eyelashes',missing)

def update_lash_color(material,rgba,enabled):
    authored=json.loads(material.get('dr_lash_authored_colors','[]'))
    if len(authored)!=2:return False
    values=[tuple(rgba[:3])+(1.,)]*2 if enabled and rgba is not None else authored
    for node in material.node_tree.nodes:
        index=node.get('dr_lash_color_index')
        if node.type=='RGB' and index in (0,1):node.outputs[0].default_value=values[index]
    return True


def prepare_default_face(material,previous_tree=None):
    """Compatibility interface built from Python, without any template copy."""
    from .character_face import build_face_tree,SCALARS,COLORS
    from .character_bootstrap import sss_values
    tree=bpy.data.node_groups.new('DR.Face.DefaultInterface.'+material.name,'ShaderNodeTree')
    values=sss_values(previous_tree)
    if values:
        seed=tree.nodes.new('ShaderNodeBsdfPrincipled')
        apply_controls(seed,values)
    try:
        build_face_tree(tree,{'scalars':dict(SCALARS),'colors':dict(COLORS),'switches':{},'sources':[]})
    except Exception:
        bpy.data.node_groups.remove(tree)
        raise
    if 'arc_face_native_revision' in tree:del tree['arc_face_native_revision']
    tree['arc_face_shader']=True;tree['arc_face_shader_owner']=material.name;tree['dr_character_fallback']='head'
    tree['dr_character_missing_source']='Native face source unavailable; explicit native master defaults are an approximate compatibility interface'
    return tree
