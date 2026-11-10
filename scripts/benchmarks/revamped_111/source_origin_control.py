def _benchmark_root():
    import os
    from pathlib import Path
    value = os.environ.get("DATARAIDERS_BENCHMARK_ROOT", r"Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/benchmark-111-20261028")
    root = Path(value).resolve()
    boundary = Path(r"Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local").resolve()
    if not root.is_relative_to(boundary):
        raise ValueError("Benchmark output must remain under the designated Z: scratch root")
    return root

import bpy,json,math
from pathlib import Path
from mathutils import Vector
T=_benchmark_root()
bpy.ops.wm.open_mainfile(filepath=str(T/'import-A.blend'))
sc=bpy.data.scenes['_IMPORT_A_14_weapon'];bpy.context.window.scene=sc
col=bpy.data.collections['A__14_weapon'];corners=[o.matrix_world@Vector(c) for o in col.all_objects if o.type=='MESH' for c in o.bound_box]
lo=Vector(tuple(min(v[i] for v in corners) for i in range(3)));hi=Vector(tuple(max(v[i] for v in corners) for i in range(3)));center=(lo+hi)/2
world=bpy.data.worlds.new('Source origin neutral');world.use_nodes=True;world.node_tree.nodes['Background'].inputs['Color'].default_value=(.16,.16,.16,1);world.node_tree.nodes['Background'].inputs['Strength'].default_value=.45;sc.world=world
for name,energy,rot in [('Key',2.5,(25,-30,-25)),('Fill',.75,(65,10,140)),('Rim',1.1,(-35,15,0))]:
 light=bpy.data.lights.new(name,'SUN');light.energy=energy;obj=bpy.data.objects.new(name,light);sc.collection.objects.link(obj);obj.rotation_euler=[math.radians(x) for x in rot]
camdata=bpy.data.cameras.new('Source origin camera');cam=bpy.data.objects.new(camdata.name,camdata);sc.collection.objects.link(cam);cam.location=center+Vector((0,-1.5,.5));cam.rotation_euler=(center-cam.location).to_track_quat('-Z','Y').to_euler();camdata.type='ORTHO';camdata.ortho_scale=1.15;sc.camera=cam
sc.render.engine='CYCLES';sc.cycles.samples=16;sc.cycles.use_denoising=True;sc.render.threads_mode='FIXED';sc.render.threads=4
prefs=bpy.context.preferences.addons['cycles'].preferences;prefs.compute_device_type='OPTIX';prefs.get_devices()
for d in prefs.devices:d.use=d.type!='CPU'
sc.cycles.device='GPU';sc.render.resolution_x=1000;sc.render.resolution_y=600;sc.render.resolution_percentage=100;sc.render.image_settings.file_format='PNG';sc.render.filepath=str(T/'renders/14_weapon_A_source_origin_control.png');sc.view_settings.view_transform='AgX';sc.use_nodes=False
bpy.ops.render.render(write_still=True,scene=sc.name)
(T/'verification/source-origin-control.json').write_text(json.dumps({'case':'14_weapon','side':'A','result':'rendered','translation_or_scale_presentation_applied':False,'mesh_world_bounds':[list(lo),list(hi)],'render':sc.render.filepath},indent=2))
print('SOURCE_ORIGIN_CONTROL_RENDERED',flush=True)
