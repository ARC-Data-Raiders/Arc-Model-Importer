import copy
import json
import math
import pathlib
import numpy as np
import bpy
from . import uniform,opt,ssa,lower_dag,lower_blender as lb,blender_harness as harness


def virtual_fixture(original,name,folder):
    fixture=copy.deepcopy(original)
    folder.mkdir(parents=True,exist_ok=True)
    for resource in fixture['resources']:
        if resource['kind']!='cb' or resource['slot'] not in [0,1]:continue
        values=np.fromfile(resource['file'],dtype='<f4').reshape(-1,4).copy()
        if resource['slot']==0:values[44:48]=np.eye(4,dtype=np.float32)
        else:
            values[:2]=np.array([[0,90000,0,0.25],[1 if name=='wet' else 0,250,1,0]],dtype=np.float32)
        target=folder/('cb'+str(resource['slot'])+'.bin')
        values.astype('<f4').tofile(target)
        resource['file']=str(target)
    (folder/'fixture.json').write_text(json.dumps(fixture,indent=2)+'\n',encoding='utf-8',newline='\n')
    return fixture


def material_dag(dag,shader,fixture):
    folded=uniform.fold_uniform(dag,shader,fixture)
    result,stats=opt.optimize(folded,ssa.Evaluator(shader,fixture))
    allowed={('v0',c) for c in 'xyz'}|{('v1',c) for c in 'xyzw'}|{('v3',c) for c in 'xyzw'}|{(r,c) for r in ['v5','v6'] for c in 'xyz'}
    bad=[n for n in result['nodes'] if n['op']=='input' and tuple(n['args']) not in allowed]
    if bad:raise lower_dag.Unsupported(str(bad))
    return result,stats


def geometry_sources(nt):
    geom=nt.nodes.new('ShaderNodeNewGeometry')
    tangent=nt.nodes.new('ShaderNodeTangent')
    tangent.direction_type='UV_MAP'
    tangent.uv_map='UVMap'
    def separate(socket):
        node=nt.nodes.new('ShaderNodeSeparateXYZ')
        nt.links.new(socket,node.inputs[0])
        return list(node.outputs)
    pos=separate(geom.outputs['Position'])
    normal=separate(geom.outputs['Normal'])
    tan=separate(tangent.outputs['Tangent'])
    result={}
    for reg,values in [('v0',tan),('v1',normal)]:
        for i,c in enumerate('xyz'):result[(reg,c)]=lb.math(nt,'MULTIPLY',values[i],-1.0 if c=='y' else 1.0)
    result[('v1','w')]=1.0
    for i,c in enumerate('xyz'):
        world=lb.math(nt,'MULTIPLY',pos[i],-100.0 if c=='y' else 100.0)
        translated=lb.math(nt,'SUBTRACT',world,[600.0,0.0,800.0][i])
        result[('v6',c)]=translated
        result[('v5',c)]=translated
    for name,components in [('UVMap','xy'),('UVMap.001','zw')]:
        uv=nt.nodes.new('ShaderNodeUVMap')
        uv.uv_map=name
        u,v,w=separate(uv.outputs[0])
        result[('v3',components[0])]=u
        result[('v3',components[1])]=lb.math(nt,'SUBTRACT',1.0,v)
    return result


def wall_scene():
    scene,nt,_=harness.grid(1,{})
    obj=bpy.data.objects['grid']
    mat=obj.data.materials[0]
    mesh=bpy.data.meshes.new('concrete_wall')
    ue=[(100,-1000,-200),(100,1000,-200),(100,1000,1800),(100,-1000,1800)]
    mesh.from_pydata([(x/100,-y/100,z/100) for x,y,z in ue],[],[(0,3,2,1)])
    for name,divisor in [('UVMap',1),('UVMap.001',4)]:
        layer=mesh.uv_layers.new(name=name)
        for loop in mesh.loops:
            x,y,z=ue[loop.vertex_index]
            u=0.5*(1+y/1000)/divisor
            v=0.5*(1-(z-800)/1000)/divisor
            layer.data[loop.index].uv=(u,1-v)
    obj.data=mesh
    mesh.materials.append(mat)
    obj.name='Concrete Wall'
    scene.camera.location=(6,0,8)
    scene.camera.rotation_euler=(math.pi/2,0,math.pi/2)
    scene.camera.data.ortho_scale=20
    scene.render.resolution_x=scene.render.resolution_y=256
    return scene,obj,nt


def images_from_fixture(fixture,folder):
    folder.mkdir(parents=True,exist_ok=True)
    images={}
    for resource in fixture['resources']:
        if resource['kind'].startswith('texture'):
            data=np.fromfile(resource['file'],dtype='<f4',offset=148).reshape(resource['height'],resource['width'],4)
            images[resource['slot']]=harness.image_from_array(data,folder/('texture_'+str(resource['slot'])+'.exr'))
    return images


def principled(nt,dag,images):
    outputs=lower_dag.lower(nt,dag,geometry_sources(nt),images,['o3.x','o3.y','o3.z','o2.x','o2.y','o2.z','o1.x','o1.y','o1.z'],'production')
    base=nt.nodes.new('ShaderNodeCombineColor')
    for i,c in enumerate('xyz'):nt.links.new(outputs['o3.'+c],base.inputs[i])
    normal=[]
    for c in 'xyz':
        component=lb.math(nt,'SUBTRACT',lb.math(nt,'MULTIPLY',outputs['o1.'+c],2.0),1.0)
        if c=='y':component=lb.math(nt,'MULTIPLY',component,-1.0)
        normal.append(component)
    length=lb.sqrt(nt,lb.dp3(nt,normal,normal))
    vector=nt.nodes.new('ShaderNodeCombineXYZ')
    for i,component in enumerate(normal):nt.links.new(lb.div(nt,component,length),vector.inputs[i])
    bsdf=nt.nodes.new('ShaderNodeBsdfPrincipled')
    nt.links.new(base.outputs[0],bsdf.inputs['Base Color'])
    nt.links.new(outputs['o2.x'],bsdf.inputs['Metallic'])
    nt.links.new(outputs['o2.y'],bsdf.inputs['Specular IOR Level'])
    bsdf.inputs['IOR'].default_value=1.5
    nt.links.new(outputs['o2.z'],bsdf.inputs['Roughness'])
    nt.links.new(vector.outputs[0],bsdf.inputs['Normal'])
    output=nt.nodes.new('ShaderNodeOutputMaterial')
    nt.links.new(bsdf.outputs[0],output.inputs['Surface'])
    return outputs
