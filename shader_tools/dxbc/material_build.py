"""Standalone Blender material-plan builder (O5.4)."""
import json
import math
from pathlib import Path
import bpy
import numpy as np
from . import engine_inputs as engine, lower_dag, lower_blender as lb, blender_harness as harness

P=Path(r"Z:\Datamining\Arc Raiders\CodexScratch\DataRaiders-BlenderImporter\.local\parity-v2")
RUN=P/"wave4a"


def _split(nt,socket):
    node=nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(socket,node.inputs[0])
    return list(node.outputs)


def geometry_sources(nt,shader):
    geom=nt.nodes.new("ShaderNodeNewGeometry")
    tangent=nt.nodes.new("ShaderNodeTangent")
    tangent.direction_type="UV_MAP";tangent.uv_map="UVMap"
    position=_split(nt,geom.outputs["Position"])
    normal=_split(nt,geom.outputs["Normal"])
    tan=_split(nt,tangent.outputs["Tangent"])
    camera=[]
    for c in "xyz":
        attr=nt.nodes.new("ShaderNodeAttribute")
        attr.attribute_type="VIEW_LAYER";attr.attribute_name="dr_cam_"+c
        camera.append(_split(nt,attr.outputs["Vector"])[0])
    translated=[lb.math(nt,"SUBTRACT",lb.math(nt,"MULTIPLY",position[i],(-100.0 if i==1 else 100.0)),camera[i]) for i in range(3)]
    uvs=[]
    for name in ("UVMap","UVMap.001"):
        node=nt.nodes.new("ShaderNodeUVMap");node.uv_map=name
        xyz=_split(nt,node.outputs[0])
        uvs.append((xyz[0],lb.math(nt,"SUBTRACT",1.0,xyz[1])))
    result={}
    for row in shader["signatures"]["input"]:
        reg="v"+row["Register"];semantic=row["Name"]+row["Index"]
        for i,c in enumerate(row["Mask"]):
            if (row["SysValue"]=="POS" or semantic=="TEXCOORD9") and i<3:value=translated[i]
            elif semantic=="TEXCOORD10" and i<3:value=lb.math(nt,"MULTIPLY",tan[i],-1.0 if i==1 else 1.0)
            elif semantic=="TEXCOORD11":value=lb.math(nt,"MULTIPLY",normal[i],-1.0 if i==1 else 1.0) if i<3 else 1.0
            elif semantic=="TEXCOORD0":value=uvs[i//2][i%2]
            else:continue
            result[(reg,c)]=value
    return result


def param_sources(nt,table):
    result={};memo={}
    for item in table:
        name=item["name"]
        attribute="dr_cam_"+name[-1] if name.startswith("dr_camneg_") else name
        typ="OBJECT" if name.startswith(("dr_l2w_","dr_w2l_","dr_cpd_")) else "VIEW_LAYER"
        if (typ,attribute) not in memo:
            node=nt.nodes.new("ShaderNodeAttribute")
            node.attribute_type=typ;node.attribute_name=attribute
            memo[(typ,attribute)]=_split(nt,node.outputs["Vector"])[0]
        value=memo[(typ,attribute)]
        if name.startswith("dr_camneg_"):value=lb.math(nt,"MULTIPLY",value,-1.0)
        result[(item.get("preg",item.get("reg")),"x")]=value
    return result


def param_props(obj,scene,names,value_set,mpc_meta):
    for item in names:
        name=item["name"] if isinstance(item,dict) else item
        value=engine.param_value(name,value_set,mpc_meta)
        if name.startswith("dr_camneg_"):
            scene["dr_cam_"+name[-1]]=float(np.float32(-value))
        else:
            owner=obj if name.startswith(("dr_l2w_","dr_w2l_","dr_cpd_")) else scene
            owner[name]=float(np.float32(value))
    for i,c in enumerate("xyz"):
        scene["dr_cam_"+c]=float(np.float32(engine.GEOMETRY[value_set["geometry"]]["C"][i]))
        scene["dr_camfwd_"+c]=float(np.float32(engine.GEOMETRY[value_set["geometry"]]["F"][i]))


def image_for(texture_entry,exr_root):
    target=Path(exr_root)/(texture_entry["object_path"].lstrip("/")+".exr")
    if target.exists():
        image=bpy.data.images.load(str(target),check_existing=True)
        image.colorspace_settings.name="Non-Color";image.alpha_mode="CHANNEL_PACKED"
        return image
    captures=json.loads((RUN/"out/D1/textures.json").read_text())
    record=next(t for t in captures if t["object_path"]==texture_entry["object_path"])
    values=np.fromfile(record["float_dds"],dtype="<f4",offset=148).reshape(record["height"],record["width"],4)
    target.parent.mkdir(parents=True,exist_ok=True)
    return harness.image_from_array(values,target)


def build_group(name,dag,shader,table,images):
    group=bpy.data.node_groups.new(name,"ShaderNodeTree")
    for label,kind in [("Base Color","NodeSocketColor"),("Metallic","NodeSocketFloat"),("Specular IOR Level","NodeSocketFloat"),("Roughness","NodeSocketFloat"),("Normal","NodeSocketVector")]:
        group.interface.new_socket(name=label,in_out="OUTPUT",socket_type=kind)
    sources=geometry_sources(group,shader);sources.update(param_sources(group,table))
    names=["o3."+c for c in "xyz"]+["o2."+c for c in "xyz"]+["o1."+c for c in "xyz"]
    outputs=lower_dag.lower(group,dag,sources,images,names,"production")
    base=group.nodes.new("ShaderNodeCombineColor")
    for i,c in enumerate("xyz"):group.links.new(outputs["o3."+c],base.inputs[i])
    normal=[]
    for c in "xyz":
        value=lb.math(group,"SUBTRACT",lb.math(group,"MULTIPLY",outputs["o1."+c],2.0),1.0)
        if c=="y":value=lb.math(group,"MULTIPLY",value,-1.0)
        normal.append(value)
    length=lb.sqrt(group,lb.dp3(group,normal,normal))
    vector=group.nodes.new("ShaderNodeCombineXYZ")
    for i,v in enumerate(normal):group.links.new(lb.div(group,v,length),vector.inputs[i])
    out=group.nodes.new("NodeGroupOutput")
    for label,socket in [("Base Color",base.outputs[0]),("Metallic",outputs["o2.x"]),("Specular IOR Level",outputs["o2.y"]),("Roughness",outputs["o2.z"]),("Normal",vector.outputs[0])]:
        group.links.new(socket,out.inputs[label])
    return group


def build_material(mi_short,plan,images):
    folder=RUN/"out/D6/plans"/mi_short
    dag=json.loads((folder/plan["dag"]).read_text())
    shader=json.loads((P/"wave2/out/W7v2/slice"/(plan["shader_sha"]+".json")).read_text())
    group=build_group("DR "+mi_short,dag,shader,plan["params"],images)
    material=bpy.data.materials.new(mi_short);material.use_nodes=True
    nt=material.node_tree;nt.nodes.clear()
    node=nt.nodes.new("ShaderNodeGroup");node.node_tree=group
    bsdf=nt.nodes.new("ShaderNodeBsdfPrincipled");bsdf.inputs["IOR"].default_value=1.5
    for name in ("Base Color","Metallic","Specular IOR Level","Roughness","Normal"):nt.links.new(node.outputs[name],bsdf.inputs[name])
    out=nt.nodes.new("ShaderNodeOutputMaterial");nt.links.new(bsdf.outputs[0],out.inputs["Surface"])
    return material


def wall_quad(span):
    ue=[(100,-1000,-200),(100,1000,-200),(100,1000,1800),(100,-1000,1800)]
    mesh=bpy.data.meshes.new("wall")
    mesh.from_pydata([(x/100,-y/100,z/100) for x,y,z in ue],[],[(0,3,2,1)])
    for name,div in [("UVMap",1),("UVMap.001",4)]:
        layer=mesh.uv_layers.new(name=name)
        for loop in mesh.loops:
            x,y,z=ue[loop.vertex_index]
            u=.5*(1+y/1000)*span/div;v=.5*(1-(z-800)/1000)*span/div
            layer.data[loop.index].uv=(u,1-v)
    obj=bpy.data.objects.new("Wall",mesh);bpy.context.scene.collection.objects.link(obj)
    return obj


def floor_quad():
    ue=[(-1000,-1000,0),(1000,-1000,0),(1000,1000,0),(-1000,1000,0)]
    mesh=bpy.data.meshes.new("floor")
    mesh.from_pydata([(x/100,-y/100,z/100) for x,y,z in ue],[],[(0,3,2,1)])
    assert mesh.polygons[0].normal.z>.99
    for name,div in [("UVMap",1),("UVMap.001",4)]:
        layer=mesh.uv_layers.new(name=name)
        for loop in mesh.loops:
            x,y,z=ue[loop.vertex_index];u=.5*(1+y/1000)/div;v=.5*(1+x/1000)/div
            layer.data[loop.index].uv=(u,1-v)
    obj=bpy.data.objects.new("Floor",mesh);bpy.context.scene.collection.objects.link(obj)
    return obj


def check_scene(span=1,size=256):
    scene,_,_=harness.grid(1,{})
    bpy.data.objects.remove(bpy.data.objects["grid"],do_unlink=True)
    obj=wall_quad(span)
    material=bpy.data.materials.new("check");material.use_nodes=True;material.node_tree.nodes.clear()
    obj.data.materials.append(material)
    scene.camera.location=(6,0,8);scene.camera.rotation_euler=(math.pi/2,0,math.pi/2)
    scene.camera.data.ortho_scale=20
    scene.render.resolution_x=scene.render.resolution_y=size
    return scene,obj,material.node_tree


def production_scene(kind):
    scene,_,_=harness.grid(1,{})
    bpy.data.objects.remove(bpy.data.objects["grid"],do_unlink=True)
    if kind=="wall":
        wall_quad(1)
        scene.camera.location=(6,0,8);scene.camera.rotation_euler=(math.pi/2,0,math.pi/2)
    elif kind=="floor":
        floor_quad()
        scene.camera.location=(0,0,10);scene.camera.rotation_euler=(0,0,0)
    else:raise ValueError(kind)
    scene.camera.data.ortho_scale=20
    scene.cycles.samples=32;scene.cycles.max_bounces=12
    scene.render.resolution_x=scene.render.resolution_y=512
    scene.view_settings.view_transform="AgX"
    scene.render.image_settings.file_format="PNG";scene.render.image_settings.color_depth="8"
    world=bpy.data.worlds.new("Preview World");world.use_nodes=True
    world.node_tree.nodes["Background"].inputs["Color"].default_value=(.08,.08,.08,1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value=1
    scene.world=world
    data=bpy.data.lights.new("Sun","SUN");data.energy=3;data.angle=math.radians(1)
    sun=bpy.data.objects.new("Sun",data);scene.collection.objects.link(sun)
    sun.rotation_euler=(0,math.radians(45),math.radians(30))
    return scene
