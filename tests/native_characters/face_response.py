"""Render the actual native face group outputs against independent scalar oracles."""
import importlib, json, math, sys, traceback
from pathlib import Path
import bpy
import numpy as np
# OUT and repo are supplied by test_native_character_materials_blender.py.
if 'OUT' not in globals() or 'repo' not in globals():
    raise RuntimeError('Run tests/test_native_character_materials_blender.py with Blender')

F=importlib.import_module(repo.name+'.Shared.materials.character_face')
bpy.ops.wm.read_factory_settings(use_empty=True)
scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=1;scene.cycles.use_denoising=False;scene.render.dither_intensity=0
scene.render.threads_mode='FIXED';scene.render.threads=4
scene.render.resolution_x=32;scene.render.resolution_y=32;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='OPEN_EXR';scene.render.image_settings.color_depth='32'
scene.view_settings.view_transform='Standard';scene.view_settings.look='None';scene.view_settings.exposure=0;scene.view_settings.gamma=1
scene.world=bpy.data.worlds.new('Test world');scene.world.use_nodes=True
scene.world.node_tree.nodes.get('Background').inputs['Color'].default_value=(0,0,0,1)
bpy.ops.mesh.primitive_plane_add(size=2);obj=bpy.context.object;obj.data.uv_layers[0].name='UV0'
cam=bpy.data.cameras.new('Camera');camera=bpy.data.objects.new('Camera',cam)
scene.collection.objects.link(camera);camera.location=(0,0,3);cam.type='ORTHO';cam.ortho_scale=2;scene.camera=camera
rows=[]

def render_output(mat,group,name,label):
    n,l=mat.node_tree.nodes,mat.node_tree.links
    emission=n.new('ShaderNodeEmission');l.new(group.outputs[name],emission.inputs['Color'])
    output=n.new('ShaderNodeOutputMaterial');output.is_active_output=True;l.new(emission.outputs[0],output.inputs['Surface'])
    path=OUT/(label+'.exr');scene.render.filepath=str(path);bpy.ops.render.render(write_still=True)
    img=bpy.data.images.load(str(path),check_existing=False)
    values=np.empty(32*32*4,np.float32);img.pixels.foreach_get(values)
    result=values.reshape(32,32,4)[16,16,:3].copy()
    bpy.data.images.remove(img);n.remove(output);n.remove(emission)
    return result

try:
    tree=bpy.data.node_groups.new('Owned face','ShaderNodeTree')
    old=tree.nodes.new('ShaderNodeBsdfPrincipled');old.inputs['Subsurface Weight'].default_value=.123
    record={'scalars':dict(F.SCALARS,**{'Cavity Color Intensity':.25,'MicroCavity Color Intensity':.5,
            'RoughnessMultiplier':.9,'Cavity Roughness':.2,'Micro Cavity Roughness':.1,
            'Specular Cavity Amount':.4,'Specular MicroCavity Amount':.2,'LipSpecular':.35,
            'Hair Mask Amount':1,'Hair Roughness':.71,'Hair Specular':.13}),
            'colors':dict(F.COLORS,**{'Cavity Color':(.04,.01,0,1),
                  'HairCap Root Color':(.1,.2,.3,1),'HairCap Tip Color':(.5,.4,.2,1)}),
            'textures':{},'switches':{'UseHair':True},'sources':['synthetic independent oracle']}
    F.build_face_tree(tree,record)
    native=next(n for n in tree.nodes if n.type=='BSDF_PRINCIPLED')
    assert abs(native.inputs['Subsurface Weight'].default_value-.123)<1e-6
    assert native.inputs['IOR'].default_value==1.5
    assert native.inputs['Coat Weight'].default_value==0
    mat=bpy.data.materials.new('FaceProof');mat.use_nodes=True;obj.data.materials.append(mat)
    mat.node_tree.nodes.clear();group=mat.node_tree.nodes.new('ShaderNodeGroup');group.node_tree=tree
    for name,value in {'Face Albedo':(.6,.3,.2,1),'Normal 1':(.5,.5,.4,1),'Base Cavity':.5,
                       'Blue Map':(0,0,.8,1),'Micro Cavity':.3,'Zone Masks':(0,.25,0,1)}.items():
        group.inputs[name].default_value=value
    expected={
        'Native Color':np.array([.04,.01,0])+(.875*.65)*(np.array([.6,.3,.2])-np.array([.04,.01,0])),
        'Native Roughness':(.4*.9+.5*.2+.7*.1),
        'Native Specular':(.35+(.25-.35)*.8)*.8*.86,
        'Native Alpha':1}
    for name,value in expected.items():
        actual=render_output(mat,group,name,'face-'+name.replace(' ','-'))
        assert np.max(np.abs(actual-value))<2e-4,(name,actual,value)
        rows.append({'case':name,'actual':actual.tolist(),'expected':value.tolist() if hasattr(value,'tolist') else value})
    group.inputs['Hair Coverage'].default_value=1
    group.inputs['Normal 2'].default_value=(.6,.4,.3,1)
    for name,value in {'Native Color':np.array([.22,.26,.27]),'Native Roughness':.71,
                       'Native Specular':.13,'Native Alpha':1}.items():
        actual=render_output(mat,group,name,'face-scalp-'+name.replace(' ','-'))
        assert np.max(np.abs(actual-value))<2e-4,(name,actual,value)
        rows.append({'case':'Scalp '+name,'actual':actual.tolist(),'expected':value.tolist() if hasattr(value,'tolist') else value})
    group.inputs['Neck Fade'].default_value=0
    actual=render_output(mat,group,'Native Alpha','face-neck-opacity')
    assert np.max(np.abs(actual))<1e-5,actual
    rows.append({'case':'Neck alpha independent of NXA coverage','actual':actual.tolist(),'expected':0})
    # Native view-dependent response at known vertex-normal facing angles.
    for name,value in {'Hair Coverage':0,'FacialHair Coverage':0,'Neck Fade':1,
                       'Blue Map':(1,0,1,1),'Normal 1':(.5,.5,.2,1),'Base Cavity':1,
                       'Micro Cavity':1,'Zone Masks':(0,1,1,1),'RoughnessMultiplier':1,
                       'Mask_FaceFresnel':2,'Mask_FaceFresnel_Contrast':0,
                       'Fresnel':.5,'FresnelContrast':0,'EarFresnel':0,
                       'Specular':.25}.items():
        group.inputs[name].default_value=value
    for degrees in [0,60,80]:
        obj.rotation_euler.y=math.radians(degrees)
        expected=1-math.sqrt(math.cos(math.radians(degrees)))
        actual=render_output(mat,group,'Native Face Fresnel','face-fresnel-'+str(degrees))
        assert np.max(np.abs(actual-expected))<4e-4,(degrees,actual,expected)
        rough=render_output(mat,group,'Native Roughness','face-fresnel-rough-'+str(degrees))
        assert np.max(np.abs(rough-(.2+expected)))<4e-4,(degrees,rough,.2+expected)
        rows.append({'case':'Actor radial/vertex fresnel '+str(degrees),'actual':actual.tolist(),
                     'expected':expected,'actual_roughness':rough.tolist(),'expected_roughness':.2+expected})
    obj.rotation_euler.y=0
    group.inputs['Zone Masks'].default_value=(0,1,.3,1);group.inputs['EarFresnel'].default_value=.4
    actual=render_output(mat,group,'Native Face Fresnel','face-ear-fresnel')
    assert np.max(np.abs(actual-.28))<2e-4,actual
    rows.append({'case':'Authored ear response','actual':actual.tolist(),'expected':.28})
    result={'passed':True,'rows':rows,'limitations':'Static equation and facing-angle probes. Engine refraction, paint/weather and SSS lighting remain unverified.'}
except Exception:
    result={'passed':False,'rows':rows,'error':traceback.format_exc()}
(OUT/'face-response-results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if not result['passed']:raise RuntimeError(result['error'])
