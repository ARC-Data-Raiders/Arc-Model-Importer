"""Normal-incidence reflected radiance checks the actual authored-eye BSDF F0."""
import importlib,json,sys,traceback
from pathlib import Path
import bpy,numpy as np
# OUT and repo are supplied by test_native_character_materials_blender.py.
if 'OUT' not in globals() or 'repo' not in globals():
    raise RuntimeError('Run tests/test_native_character_materials_blender.py with Blender')

C=importlib.import_module(repo.name+'.Shared.materials.character')
F=importlib.import_module(repo.name+'.Shared.materials.character_fidelity')
bpy.ops.wm.read_factory_settings(use_empty=True)
scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=32
scene.cycles.use_denoising=False;scene.render.dither_intensity=0
scene.render.threads_mode='FIXED';scene.render.threads=4
scene.render.resolution_x=32;scene.render.resolution_y=32;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='OPEN_EXR';scene.render.image_settings.color_depth='32'
scene.view_settings.view_transform='Standard';scene.view_settings.look='None';scene.view_settings.exposure=0;scene.view_settings.gamma=1
scene.world=bpy.data.worlds.new('Uniform unit-radiance environment');scene.world.use_nodes=True
bg=next(n for n in scene.world.node_tree.nodes if n.type=='BACKGROUND');bg.inputs['Color'].default_value=(1,1,1,1);bg.inputs['Strength'].default_value=1
bpy.ops.mesh.primitive_plane_add(size=2);obj=bpy.context.object;obj.data.uv_layers[0].name='UV0'
cam=bpy.data.cameras.new('Camera');camera=bpy.data.objects.new('Camera',cam);scene.collection.objects.link(camera)
camera.location=(0,0,3);cam.type='ORTHO';cam.ortho_scale=2;scene.camera=camera
rows=[]
try:
    texture_path=OUT/'eye-reflection-test.png'
    img=bpy.data.images.new('test eye texture',width=2,height=2,alpha=True)
    img.pixels.foreach_set(np.tile([.5,.5,1,1],4).astype(np.float32));img.filepath_raw=str(texture_path);img.file_format='PNG';img.save()
    old_find=C._find_tex_path
    C._find_tex_path=lambda ref:str(texture_path)
    for specular in [.25,.6,1]:
        fixture=OUT/('eye-reflection-'+str(specular)+'.json')
        fixture.write_text(json.dumps({'Textures':{'IrisColor':'/Game/Test/Iris','ScleraColor':'/Game/Test/Sclera'},
             'Parameters':{'Scalars':{'SpecularityIris':specular,'SpecularityScelera':specular,'IoR':1.336}}}))
        mat=F._authored_eye_material(str(fixture),'UV0');assert mat
        p=next(n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
        assert p.inputs['IOR'].default_value==1.5
        assert abs(mat['arc_eye_refraction_ior']-1.336)<1e-5
        for name in ['Base Color','Roughness']:
            for link in list(p.inputs[name].links):mat.node_tree.links.remove(link)
        p.inputs['Base Color'].default_value=(0,0,0,1)
        p.inputs['Roughness'].default_value=0
        p.inputs['Coat Weight'].default_value=0
        # Keep the actual eye scalar graph connected to Specular IOR Level.
        for link in list(p.inputs['Normal'].links):mat.node_tree.links.remove(link)
        obj.data.materials.clear();obj.data.materials.append(mat)
        path=OUT/('eye-f0-'+str(specular)+'.exr');scene.render.filepath=str(path)
        bpy.ops.render.render(write_still=True)
        image=bpy.data.images.load(str(path),check_existing=False)
        values=np.empty(32*32*4,np.float32);image.pixels.foreach_get(values)
        actual=values.reshape(32,32,4)[16,16,:3]
        expected=.08*specular
        assert np.max(np.abs(actual-expected))<2e-4,(specular,actual,expected)
        rows.append({'specular':specular,'expected_normal_reflectance':expected,'actual':actual.tolist()})
        bpy.data.images.remove(image)
    C._find_tex_path=old_find
    result={'passed':True,'rows':rows,'limitations':'Static opaque eye reflection only, not native refraction/cornea/SSS.'}
except Exception:result={'passed':False,'rows':rows,'error':traceback.format_exc()}
(OUT/'eye-reflection-results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if not result['passed']:raise RuntimeError(result['error'])
