def _benchmark_root():
    import os
    from pathlib import Path
    value = os.environ.get("DATARAIDERS_BENCHMARK_ROOT", r"Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/benchmark-111-20261028")
    root = Path(value).resolve()
    boundary = Path(r"Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local").resolve()
    if not root.is_relative_to(boundary):
        raise ValueError("Benchmark output must remain under the designated Z: scratch root")
    return root

import bpy,json,math,time,traceback,os
from pathlib import Path
from mathutils import Vector
T=_benchmark_root();manifest=json.loads((T/'benchmark-manifest.json').read_text())
reports={s:json.loads((T/f'report-{s}.json').read_text()) for s in ['A','B']}
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.context.preferences.filepaths.save_version=0
scene=bpy.context.scene;scene.name='00_BENCHMARK_OVERVIEW'
for side in ['A','B']:
 with bpy.data.libraries.load(str(T/f'import-{side}.blend'),link=False) as (src,dst):dst.collections=[n for n in src.collections if n.startswith(side+'__')]
 for col in dst.collections:
  if col:scene.collection.children.link(col)
sys_path_for_benchmark=str(T/'verification')
import sys
sys.path.insert(0,sys_path_for_benchmark)
from elevator_presentation import assemble
elevator_reports={}
for side in ['A','B']:
 col=bpy.data.collections.get(side+'__20_elevator')
 elevator_reports[side]=assemble(col,str(T/'exports'))
 if elevator_reports[side].get('unresolved'):print('ELEVATOR_UNRESOLVED',side,elevator_reports[side]['unresolved'],flush=True)
(T/'verification/elevator-blender-assembly.json').write_text(json.dumps(elevator_reports,indent=2,default=str))
lighting=bpy.data.collections.new('BENCHMARK_NEUTRAL_LIGHTS');scene.collection.children.link(lighting)
world=bpy.data.worlds.new('Neutral benchmark world');world.use_nodes=True
world.node_tree.nodes.get('Background').inputs['Color'].default_value=(.16,.16,.16,1);world.node_tree.nodes.get('Background').inputs['Strength'].default_value=.45
for name,strength,rot,angle in [('Key',2.5,(25,-30,-25),.2),('Fill',.75,(65,10,140),.35),('Rim',1.1,(-35,15,0),.15)]:
 data=bpy.data.lights.new(name,'SUN');data.energy=strength;data.angle=angle;data.color=(1,1,1)
 obj=bpy.data.objects.new(name,data);lighting.objects.link(obj);obj.rotation_euler=[math.radians(v) for v in rot]
fontmat=bpy.data.materials.new('Benchmark label emission');fontmat.use_nodes=True
nt=fontmat.node_tree;nt.nodes.clear();emit=nt.nodes.new('ShaderNodeEmission');emit.inputs['Color'].default_value=(.8,.86,.92,1);emit.inputs['Strength'].default_value=1
out=nt.nodes.new('ShaderNodeOutputMaterial');nt.links.new(emit.outputs[0],out.inputs['Surface'])
floor_mat=bpy.data.materials.new('Neutral floor');floor_mat.use_nodes=True
p=next(n for n in floor_mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED');p.inputs['Base Color'].default_value=(.085,.085,.085,1);p.inputs['Roughness'].default_value=.8
render_device='CPU';devices=[]
try:
 prefs=bpy.context.preferences.addons['cycles'].preferences;prefs.compute_device_type='OPTIX';prefs.get_devices()
 for d in prefs.devices:
  d.use=d.type!='CPU';devices.append({'name':d.name,'type':d.type,'enabled':d.use})
 if any(d['enabled'] for d in devices):render_device='GPU'
except Exception as e:print('DEVICE_FALLBACK',e,flush=True)

def settings(sc):
 sc.world=world;sc.render.engine='CYCLES';sc.cycles.device=render_device;sc.cycles.samples=32;sc.cycles.use_denoising=True
 sc.cycles.max_bounces=8;sc.cycles.transparent_max_bounces=16
 sc.render.threads_mode='FIXED';sc.render.threads=4
 sc.render.resolution_x=1280;sc.render.resolution_y=800;sc.render.resolution_percentage=100
 sc.render.image_settings.file_format='PNG';sc.render.image_settings.color_mode='RGBA';sc.render.film_transparent=False
 sc.view_settings.view_transform='AgX';sc.view_settings.exposure=0;sc.view_settings.gamma=1
 sc.use_nodes=False
settings(scene)

def bounds(col):
 corners=[o.matrix_world@Vector(c) for o in col.all_objects if o.type=='MESH' and not o.hide_render for c in o.bound_box]
 if not corners:return None
 return Vector(tuple(min(v[i] for v in corners) for i in range(3))),Vector(tuple(max(v[i] for v in corners) for i in range(3)))

def label(col,name,text,location,cam,size=.18):
 curve=bpy.data.curves.new(name,'FONT');curve.body=text;curve.size=size;curve.align_x='CENTER';curve.align_y='CENTER'
 obj=bpy.data.objects.new(name,curve);col.objects.link(obj);obj.location=location;obj.rotation_euler=cam.rotation_euler;curve.materials.append(fontmat)
 return obj

comparisons=[];scenes=[]
for i,g in enumerate(manifest['groups']):
 pair=bpy.data.collections.new('CASE '+g['id']+' - '+g['label']);scene.collection.children.link(pair)
 cols={side:bpy.data.collections.get(side+'__'+g['id']) for side in ['A','B']}
 box={s:bounds(c) for s,c in cols.items()};valid=[b for b in box.values() if b]
 if not valid:continue
 lo=Vector(tuple(min(b[0][j] for b in valid) for j in range(3)));hi=Vector(tuple(max(b[1][j] for b in valid) for j in range(3)))
 span=hi-lo;factor=2.2/max(max(span),.00001);common=Vector(((lo.x+hi.x)/2,(lo.y+hi.y)/2,lo.z))
 gx=(i%4)*12;gy=(i//4)*12
 for side,sign in [('A',-1),('B',1)]:
  col=cols[side]
  if col.name in scene.collection.children:scene.collection.children.unlink(col)
  pair.children.link(col)
  wrapper=bpy.data.objects.new(side+' '+g['id']+' DISPLAY TRANSFORM',None);col.objects.link(wrapper)
  objects=list(col.objects)
  for o in objects:
   if o==wrapper:continue
   if o.parent not in objects:
    mw=o.matrix_world.copy();o.parent=wrapper;o.matrix_world=mw
  wrapper.scale=(factor,)*3;wrapper.location=Vector((gx+sign*2.15,gy,0))-common*factor
  wrapper['display_scale']=factor;wrapper['source_world_bounds']=json.dumps([list(x) for x in box[side]]) if box[side] else 'none'
  for o in col.all_objects:
   if o.type=='ARMATURE':o.hide_render=True;o.hide_set(True)
 # One identical orthographic projection and shared directional lighting per pair.
 cam_data=bpy.data.cameras.new('CAM '+g['id']);cam=bpy.data.objects.new(cam_data.name,cam_data);pair.objects.link(cam)
 cam.location=(gx,gy-10,3.6);target=Vector((gx,gy,.95));cam.rotation_euler=(target-cam.location).to_track_quat('-Z','Y').to_euler();cam_data.type='ORTHO';cam_data.ortho_scale=8.4;cam_data.lens=50
 right=cam.rotation_euler.to_quaternion()@Vector((1,0,0));up=cam.rotation_euler.to_quaternion()@Vector((0,1,0));center=Vector((gx,gy,.95))
 label(pair,'TITLE '+g['id'],g['label'],center+up*2.14,cam,.23)
 label(pair,'LABEL A '+g['id'],'A  DataRaiders 2.20.0',center-right*2.15+up*1.73,cam,.19)
 label(pair,'LABEL B '+g['id'],'B  Revamped 1.11.0',center+right*2.15+up*1.73,cam,.19)
 label(pair,'SUBTITLE '+g['id'],'Steam 26159202  |  CL1430164  |  shared scale '+format(factor,'.4g')+'x',center-up*1.75,cam,.13)
 # Local floor only; it is identical across the A/B pair.
 mesh=bpy.data.meshes.new('Floor '+g['id']);mesh.from_pydata([(-4.8,-2.8,0),(4.8,-2.8,0),(4.8,2.8,0),(-4.8,2.8,0)],[],[(0,1,2,3)]);mesh.materials.append(floor_mat)
 floor=bpy.data.objects.new(mesh.name,mesh);pair.objects.link(floor);floor.location=(gx,gy,-.035)
 sc=bpy.data.scenes.new(g['id']+' '+g['label'][:36]);sc.collection.children.link(pair);sc.collection.children.link(lighting);sc.camera=cam;settings(sc);scenes.append(sc)
 row={'id':g['id'],'label':g['label'],'display_scale':factor,'source_bounds':{s:[list(v) for v in b] if b else None for s,b in box.items()},'scene':sc.name,'camera':cam.name,'render':str(T/'renders'/(g['id']+'.png'))}
 comparisons.append(row)
 # Per-case provenance stays inspectable inside the blend.
 text=bpy.data.texts.new('REPORT '+g['id']);text.write(json.dumps({s:next(x for x in reports[s]['groups'] if x['id']==g['id']) for s in ['A','B']},indent=2,default=str))
# Overview camera, plus per-case scene selector for close inspection.
data=bpy.data.cameras.new('Overview camera');obj=bpy.data.objects.new('Overview camera',data);scene.collection.objects.link(obj);obj.location=(18,28,65);obj.rotation_euler=(Vector((18,30,0))-obj.location).to_track_quat('-Z','Y').to_euler();data.type='ORTHO';data.ortho_scale=91;scene.camera=obj
text=bpy.data.texts.new('START HERE - A B BENCHMARK');text.write('A = installed DataRaiders 2.20.0 native UEModel. B = supplied Revamped 1.11.0 ActorX. Each case uses matching fresh Steam build26159202 packages and supplied CL1430164 usmap. No copied material graphs, compositor or grading. Individual scenes offer camera-framed A/B pairs; overview shows all26 cases. Display transform uses ONE shared scale/offset per pair: original source bounds are recorded on wrapper and REPORT texts. Compare geometry differences before attributing differences to shaders. Raw import warnings/errors and every image/slot are in REPORT texts. Source and legacy material limitations are not game parity claims. All textures packed where available. No user original blend was opened or modified.\n\n'+(T/'analysis-revamped111.md').read_text(encoding='utf-8')+'\n\n'+(T/'benchmark-api-audit.md').read_text(encoding='utf-8'))
for s in ['A','B']:
 txt=bpy.data.texts.new(s+' FULL IMPORT REPORT');txt.write(json.dumps(reports[s],indent=2,default=str))
packing=[]
try:bpy.ops.file.pack_all()
except Exception as e:packing.append(str(e))
summary={'elevator_presentation':elevator_reports,'world_projection_limit':'Common display transforms may change world-projected sampling phase; use source metrics and controlled source-origin renders for that family.','steam_build':manifest['build'],'devices':devices,'render_device':render_device,'cases':comparisons,'packing_errors':packing,'renders':[]}
(T/'benchmark-results.json').write_text(json.dumps(summary,indent=2))
# Make opening the deliverable immediately useful: first case is the active scene.
if bpy.context.window:bpy.context.window.scene=scenes[0]
for screen in bpy.data.screens:
 for area in screen.areas:
  if area.type=='VIEW_3D':
   area.spaces.active.region_3d.view_perspective='CAMERA'
   area.spaces.active.shading.type='MATERIAL'
   area.spaces.active.shading.use_scene_world=True
   area.spaces.active.shading.use_scene_lights=True
bpy.ops.wm.save_as_mainfile(filepath=str(T/'ARC_AB_Benchmark_2.20.0_vs_1.11.0.blend'),compress=True)
print('BENCHMARK_BLEND_READY',flush=True)
for sc,row in zip(scenes,comparisons):
 start=time.perf_counter();sc.render.filepath=row['render']
 try:
  bpy.ops.render.render(write_still=True,scene=sc.name)
  summary['renders'].append({'id':row['id'],'status':'passed','seconds':time.perf_counter()-start,'path':row['render']})
 except Exception as e:summary['renders'].append({'id':row['id'],'status':'failed','error':str(e),'traceback':traceback.format_exc()})
 (T/'benchmark-results.json').write_text(json.dumps(summary,indent=2));print('BENCHMARK_RENDER',row['id'],summary['renders'][-1]['status'],flush=True)
# Opposite side exposes the ticket artwork rather than its plain metal back.
case_index=next(i for i,x in enumerate(comparisons) if x['id']=='09_charm_decal')
sc=scenes[case_index];cam=sc.camera;original_location=cam.location.copy();original_rotation=cam.rotation_euler.copy()
case_x=(case_index%4)*12;case_y=(case_index//4)*12
fonts=[o for o in sc.objects if o.type=='FONT'];old_visibility={o:o.hide_render for o in fonts}
for o in fonts:o.hide_render=True
cam.location=(case_x,case_y+10,3.6);cam.rotation_euler=(Vector((case_x,case_y,.95))-cam.location).to_track_quat('-Z','Y').to_euler()
sc.render.filepath=str(T/'renders/09_charm_decal_opposite.png')
try:
 bpy.ops.render.render(write_still=True,scene=sc.name)
 summary['renders'].append({'id':'09_charm_decal_opposite','status':'passed','path':sc.render.filepath,'note':'Opposite-facing camera reverses A/B screen positions; A is on the right in this raw opposite view.'})
finally:
 cam.location=original_location;cam.rotation_euler=original_rotation
 for o,hidden in old_visibility.items():o.hide_render=hidden
# Close views support inspecting eyes, pores and hairline beyond the overview scale.
face_index=next(i for i,x in enumerate(comparisons) if x['id']=='03_face')
fsc=scenes[face_index];fcam=fsc.camera;floc=fcam.location.copy();frot=fcam.rotation_euler.copy();fscale=fcam.data.ortho_scale
fx=(face_index%4)*12;fy=(face_index//4)*12
for side,sign in [('A',-1),('B',1)]:
 fcam.location=(fx+sign*2.15,fy-10,1.6);fcam.rotation_euler=(Vector((fx+sign*2.15,fy,1.6))-fcam.location).to_track_quat('-Z','Y').to_euler();fcam.data.ortho_scale=1.35
 fsc.render.filepath=str(T/('renders/03_face_eyes_'+side+'.png'))
 bpy.ops.render.render(write_still=True,scene=fsc.name)
 summary['renders'].append({'id':'03_face_eyes_'+side,'status':'passed','path':fsc.render.filepath,'note':'Matched camera distance and crop; per-side closeup'})
fcam.location=floc;fcam.rotation_euler=frot;fcam.data.ortho_scale=fscale
summary['completed']=True;(T/'benchmark-results.json').write_text(json.dumps(summary,indent=2))
print('BENCHMARK_RENDER_COMPLETE',len(summary['renders']),flush=True)

text=bpy.data.texts.new('FINAL BENCHMARK METRICS');text.write(json.dumps(summary,indent=2,default=str))
bpy.context.preferences.filepaths.save_version=0
bpy.ops.wm.save_as_mainfile(filepath=str(T/'ARC_AB_Benchmark_2.20.0_vs_1.11.0.blend'),compress=True)
print('FINAL_BLEND_SAVED',flush=True)
