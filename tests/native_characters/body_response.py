"""Render the real body graph's output sockets against independent numeric oracles."""
import importlib, json, math, sys, traceback
from pathlib import Path
import bpy
import numpy as np

# OUT and repo are supplied by test_native_character_materials_blender.py.
if 'OUT' not in globals() or 'repo' not in globals():
    raise RuntimeError('Run tests/test_native_character_materials_blender.py with Blender')

M=importlib.import_module(repo.name+'.materials.character_body')
bpy.ops.wm.read_factory_settings(use_empty=True)
scene=bpy.context.scene
scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=1;scene.cycles.use_denoising=False;scene.render.dither_intensity=0
scene.render.threads_mode='FIXED';scene.render.threads=4
scene.render.resolution_x=32;scene.render.resolution_y=32;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='OPEN_EXR';scene.render.image_settings.color_depth='32'
scene.view_settings.view_transform='Standard';scene.view_settings.look='None';scene.view_settings.exposure=0;scene.view_settings.gamma=1
scene.world=bpy.data.worlds.new('Test world');scene.world.use_nodes=True
scene.world.node_tree.nodes.get('Background').inputs['Color'].default_value=(0,0,0,1)
bpy.ops.mesh.primitive_plane_add(size=2);obj=bpy.context.object
obj.data.uv_layers[0].name='UV0'
cam=bpy.data.cameras.new('Camera');camera=bpy.data.objects.new('Camera',cam)
scene.collection.objects.link(camera);camera.location=(0,0,3);cam.type='ORTHO';cam.ortho_scale=2;scene.camera=camera
rows=[]

def image(name,color):
    img=bpy.data.images.new(name,width=4,height=4,alpha=True,float_buffer=True)
    img.colorspace_settings.name='Non-Color';img.alpha_mode='CHANNEL_PACKED'
    img.pixels.foreach_set(np.tile(color,16).astype(np.float32));img.update()
    return img

def render_socket(mat,socket,label):
    n,l=mat.node_tree.nodes,mat.node_tree.links
    emission=n.new('ShaderNodeEmission');l.new(socket,emission.inputs['Color'])
    out=n.new('ShaderNodeOutputMaterial');out.is_active_output=True;l.new(emission.outputs[0],out.inputs['Surface'])
    path=OUT/(label+'.exr');scene.render.filepath=str(path)
    bpy.ops.render.render(write_still=True)
    img=bpy.data.images.load(str(path),check_existing=False)
    values=np.empty(32*32*4,np.float32);img.pixels.foreach_get(values)
    result=values.reshape(32,32,4)[16,16,:3].copy()
    bpy.data.images.remove(img);n.remove(out);n.remove(emission)
    return result

def make(micro,flatness=0):
    mat=bpy.data.materials.new('BodyProof');mat.use_nodes=True
    obj.data.materials.clear();obj.data.materials.append(mat)
    n,l=mat.node_tree.nodes,mat.node_tree.links;n.clear()
    p=n.new('ShaderNodeBsdfPrincipled');p.inputs['Subsurface Weight'].default_value=.123
    p.inputs['Alpha'].default_value=.432
    a=n.new('ShaderNodeTexImage');a.image=image('Albedo',(.8,.4,.2,1))
    params={'scalars':{'SkinDesaturation':.2,'Specular':.23,'Roughness':.7,'MicroNormal Flatness':flatness},
            'colors':{'ColorMultiply':(.3,.4,.5,1)}}
    result=M.build_body_response(n,l,p,a,image('Macro',(.5,.5,1,.25)),image('Micro',micro),params)
    assert abs(p.inputs['Alpha'].default_value-.432)<1e-6,'external body alpha changed'
    assert abs(p.inputs['Subsurface Weight'].default_value-.123)<1e-6,'SSS setting overwritten'
    assert p.inputs['Coat Weight'].default_value==0
    assert p.inputs['IOR'].default_value==1.5
    assert abs(n['Body_Specular'].outputs[0].default_value-.23)<1e-6
    assert abs(n['Body_MicroNormal_Tiling_Body'].outputs[0].default_value-13)<1e-6
    assert all(x.interpolation=='Linear' for x in n if x.type=='TEX_IMAGE')
    assert tuple(n['Body_Native micro UV aspect'].inputs[1].default_value)==(2,1,1)
    return mat,result

try:
    mat,r=make((.5,.5,1,1))
    actual=render_socket(mat,r['color'],'body-albedo-proof')
    gray=.8*.212639+.4*.715169+.2*.072192
    expected=((np.array([.8,.4,.2])*.8+gray*.2)*np.array([.6,.8,1]))
    assert np.max(np.abs(actual-expected))<2e-4,(actual,expected)
    rows.append({'case':'linear desaturation then twice tint','actual':actual.tolist(),'expected':expected.tolist()})
    actual=render_socket(mat,r['roughness'],'body-roughness-proof')
    expected=(.25+(.8-.25)*.04)*.7
    assert np.max(np.abs(actual-expected))<2e-4,(actual,expected)
    rows.append({'case':'macro alpha roughness and authored fresnel','actual':actual.tolist(),'expected':expected})
    # Independent RNM numerator oracle using signed RG detail and flat macro.
    for flat in [0,.5,1]:
        mat,r=make((.7,.4,1,1),flat)
        detail=np.array([.4,-.2,math.sqrt(.8)])
        detail=detail*(1-flat)+np.array([0,0,1])*flat;detail/=np.linalg.norm(detail)
        expected=detail
        g=M.ResponseGraph(mat.node_tree.nodes,mat.node_tree.links,'Proof_')
        encoded=g.vector('ADD',g.vector('SCALE',r['tangent_normal'],.5),(.5,.5,.5))
        actual=(render_socket(mat,encoded,'body-normal-flat-'+str(flat))*2-1)
        assert np.max(np.abs(actual-expected))<3e-4,(flat,actual,expected)
        rows.append({'case':'RNM micro flatness '+str(flat),'actual':actual.tolist(),'expected':expected.tolist()})
    result={'passed':True,'rows':rows,'limitations':'Equation render probes, not matched in-game lighting or SSS parity.'}
except Exception:
    result={'passed':False,'rows':rows,'error':traceback.format_exc()}
(OUT/'body-response-results.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
if not result['passed']:raise RuntimeError(result['error'])
