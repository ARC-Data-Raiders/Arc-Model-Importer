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
from mathutils import Vector,Matrix
T=_benchmark_root()
bpy.ops.wm.open_mainfile(filepath=str(T/'ARC_AB_Benchmark_2.20.0_vs_1.11.0.blend'))
summary=json.loads((T/'benchmark-results.json').read_text());row=next(x for x in summary['cases'] if x['id']=='09_charm_decal');case_index=8;gx=(case_index%4)*12;gy=(case_index//4)*12
boxes=list(row['source_bounds'].values());lo=Vector(tuple(min(b[0][i] for b in boxes) for i in range(3)));hi=Vector(tuple(max(b[1][i] for b in boxes) for i in range(3)));common=Vector(((lo.x+hi.x)/2,(lo.y+hi.y)/2,lo.z));rotation=Matrix.Rotation(math.pi,4,'Z')
for side,sign in [('A',-1),('B',1)]:
 obj=bpy.data.objects[side+' 09_charm_decal DISPLAY TRANSFORM'];factor=row['display_scale'];target=Vector((gx+sign*2.15,gy,0))
 obj.rotation_euler=(0,0,math.pi);obj.location=target-rotation.to_3x3()@(common*factor);obj['benchmark_presentation_rotation_degrees']=180
sc=bpy.data.scenes[row['scene']];prefs=bpy.context.preferences.addons['cycles'].preferences;prefs.compute_device_type='OPTIX';prefs.get_devices()
for d in prefs.devices:d.use=d.type!='CPU'
sc.cycles.device='GPU';sc.render.threads_mode='FIXED';sc.render.threads=4;sc.render.filepath=str(T/'renders/09_charm_decal.png')
bpy.ops.render.render(write_still=True,scene=sc.name)
row['presentation']='Both ticket models turned180degrees around Z to show decal artwork; same transform on both sides, A left/B right.'
summary['source_origin_weapon_control']=json.loads((T/'verification/source-origin-control.json').read_text());summary['source_origin_weapon_control']['visual_result']='near-solid white persists at original imported coordinates and scale'
summary['reopen_verification']= {'report':'verification/final-blend-reopen.json','packed_bound_images':810,'missing_bound_files':0,'invalid_named_uv_bindings':0}
(T/'benchmark-results.json').write_text(json.dumps(summary,indent=2))
for name,path in [('BENCHMARK REPORT - READ ME',T/'REPORT.md'),('RUNTIME FAILURE TRIAGE',T/'analysis-runtime-failures.md')]:
 text=bpy.data.texts.get(name) or bpy.data.texts.new(name);text.clear();text.write(path.read_text(encoding='utf-8'))
text=bpy.data.texts.get('FINAL BENCHMARK METRICS');text.clear();text.write(json.dumps(summary,indent=2))
bpy.context.preferences.filepaths.save_version=0;bpy.ops.wm.save_as_mainfile(filepath=str(T/'ARC_AB_Benchmark_2.20.0_vs_1.11.0.blend'),compress=True)
print('ARTWORK_FRONT_DEFAULT_AND_REPORT_EMBEDDED',flush=True)
