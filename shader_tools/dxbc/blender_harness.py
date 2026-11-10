import pathlib
import bpy
import numpy as np


def grid(size,attributes):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene
    verts=[]
    faces=[]
    for py in range(size):
        for px in range(size):
            y=size-py-1
            base=len(verts)
            verts.extend([(px,y,0),(px+1,y,0),(px+1,y+1,0),(px,y+1,0)])
            faces.append((base,base+1,base+2,base+3))
    mesh=bpy.data.meshes.new('grid')
    mesh.from_pydata(verts,[],faces)
    for name,values in attributes.items():
        attr=mesh.attributes.new(name,'FLOAT','FACE')
        attr.data.foreach_set('value',np.asarray(values,np.float32).ravel())
    obj=bpy.data.objects.new('grid',mesh)
    scene.collection.objects.link(obj)
    mat=bpy.data.materials.new('op')
    mat.use_nodes=True
    nt=mat.node_tree
    nt.nodes.clear()
    obj.data.materials.append(mat)
    cam_data=bpy.data.cameras.new('cam')
    cam_data.type='ORTHO'
    cam_data.ortho_scale=size
    cam=bpy.data.objects.new('cam',cam_data)
    cam.location=(size/2,size/2,10)
    cam.rotation_euler=(0,0,0)
    scene.collection.objects.link(cam)
    scene.camera=cam
    scene.render.engine='CYCLES'
    scene.cycles.device='CPU'
    scene.cycles.samples=1
    scene.cycles.use_denoising=False
    scene.cycles.pixel_filter_type='BOX'
    scene.cycles.filter_width=0.01
    scene.cycles.max_bounces=0
    scene.render.resolution_x=scene.render.resolution_y=size
    scene.render.resolution_percentage=100
    scene.render.film_transparent=False
    scene.world=None
    scene.view_settings.view_transform='Raw'
    scene.view_settings.look='None'
    scene.view_settings.exposure=0.0
    scene.view_settings.gamma=1.0
    scene.render.image_settings.file_format='OPEN_EXR'
    scene.render.image_settings.color_depth='32'
    scene.render.image_settings.exr_codec='NONE'
    sockets={}
    for name in attributes:
        n=nt.nodes.new('ShaderNodeAttribute')
        n.attribute_type='GEOMETRY'
        n.attribute_name=name
        sockets[name]=n.outputs['Fac']
    return scene,nt,sockets


def image_from_array(values,path):
    height,width=values.shape[:2]
    image=bpy.data.images.new('tex',width,height,alpha=True,float_buffer=True)
    image.colorspace_settings.name='Non-Color'
    image.pixels.foreach_set(np.ascontiguousarray(values[::-1]).ravel())
    image.filepath_raw=str(path)
    image.file_format='OPEN_EXR'
    image.use_half_precision=False
    image.save()
    result=bpy.data.images.load(str(path))
    result.colorspace_settings.name='Non-Color'
    result.alpha_mode='CHANNEL_PACKED'
    return result


def render(scene,nt,outputs,path):
    comb=nt.nodes.new('ShaderNodeCombineColor')
    for i,value in enumerate(outputs):
        if isinstance(value,(int,float)):comb.inputs[i].default_value=float(value)
        else:nt.links.new(value,comb.inputs[i])
    em=nt.nodes.new('ShaderNodeEmission')
    em.inputs['Strength'].default_value=1.0
    nt.links.new(comb.outputs[0],em.inputs['Color'])
    output=nt.nodes.new('ShaderNodeOutputMaterial')
    nt.links.new(em.outputs[0],output.inputs['Surface'])
    scene.render.filepath=str(path)
    bpy.ops.render.render(write_still=True)
    image=bpy.data.images.load(str(path))
    image.colorspace_settings.name='Non-Color'
    values=np.array(image.pixels[:],dtype=np.float32).reshape(scene.render.resolution_y,scene.render.resolution_x,4)[::-1].copy()
    bpy.data.images.remove(image)
    nt.nodes.remove(output)
    nt.nodes.remove(em)
    nt.nodes.remove(comb)
    return values
