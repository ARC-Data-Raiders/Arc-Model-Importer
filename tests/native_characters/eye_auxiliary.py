"""Numerical native EyeOcclusion/TearLine probes. Parent runs capped Blender."""
import importlib,json,math,sys,traceback
from pathlib import Path
import bpy
import numpy as np
# OUT and repo are supplied by test_native_character_materials_blender.py.
if 'OUT' not in globals() or 'repo' not in globals():
    raise RuntimeError('Run tests/test_native_character_materials_blender.py with Blender')

A=importlib.import_module(repo.name+'.materials.character_eye_aux')
bpy.ops.wm.read_factory_settings(use_empty=True)
s=bpy.context.scene;s.render.engine='CYCLES';s.cycles.device='CPU';s.cycles.samples=1;s.cycles.use_denoising=False
s.render.dither_intensity=0;s.render.threads_mode='FIXED';s.render.threads=4
s.render.resolution_x=32;s.render.resolution_y=32;s.render.resolution_percentage=100
s.render.image_settings.file_format='OPEN_EXR';s.render.image_settings.color_depth='32'
s.world=bpy.data.worlds.new('Probe');s.world.use_nodes=True;s.world.node_tree.nodes['Background'].inputs['Color'].default_value=(0,0,0,1)
bpy.ops.mesh.primitive_plane_add(size=4);obj=bpy.context.object
cam=bpy.data.cameras.new('Camera');camera=bpy.data.objects.new('Camera',cam);s.collection.objects.link(camera)
camera.location=(0,0,3);cam.type='ORTHO';cam.ortho_scale=2;s.camera=camera
rows=[]
def probe(label,build,expected,angle=0):
 obj.rotation_euler[1]=math.radians(angle)
 m=bpy.data.materials.new(label);m.use_nodes=True;m.node_tree.nodes.clear();obj.data.materials.clear();obj.data.materials.append(m)
 g=A.ResponseGraph(m.node_tree.nodes,m.node_tree.links,'Probe_');value=build(g)
 emission=g.node('ShaderNodeEmission','Numeric emission');g.put(emission.inputs['Color'],value)
 output=g.node('ShaderNodeOutputMaterial','Output');g.put(output.inputs['Surface'],emission.outputs[0])
 s.render.filepath=str(OUT/(label+'.exr'));bpy.ops.render.render(write_still=True)
 img=bpy.data.images.load(s.render.filepath,check_existing=False);pixels=np.empty(32*32*4,np.float32);img.pixels.foreach_get(pixels)
 actual=pixels.reshape(32,32,4)[16,16,:3].copy();bpy.data.images.remove(img)
 assert np.max(np.abs(actual-expected))<3e-4,(label,actual,expected)
 rows.append({'case':label,'actual':actual.tolist(),'expected':np.broadcast_to(expected,(3,)).tolist()})
def occ_oracle(uv,sc,tint):
 def clear(dist,r,h):return 1-min(max((1-abs(dist)/max(r,1e-5))/max(1-h,1e-5),0),1)
 dist=dict(Outer=uv[0],Inner=uv[0]-1,Bottom=uv[1]-1,Top=uv[1])
 def prod(kind):return math.prod(clear(d,sc[e+kind+'_Radius'],sc[e+kind+'_Hardness']) for e,d in dist.items())
 mask=((1-prod('Shadow'))*prod('ShadowBorder'))**2
 return (1-mask+mask*np.array(tint))*(1-mask*sc['ShadowMultiply'])
try:
 cases=[('identity',(.25,.5),{},(0,0,0)),
 ('single-edge',(.25,.5),{'OuterShadow_Radius':.5,'ShadowMultiply':.5},(.2,.4,.6)),
 ('edge-product',(.1,.2),{'OuterShadow_Radius':.5,'TopShadow_Radius':.4,'ShadowMultiply':.3},(.1,.2,.3)),
 ('border-product',(.025,.5),{'OuterShadow_Radius':.5,'ShadowMultiply':.5},(.2,.4,.6)),
 ('tint-with-zero-multiply',(.25,.5),{'OuterShadow_Radius':.5},(.2,.4,.6))]
 for label,uv,changes,tint in cases:
  sc=dict(A.OCCLUSION_DEFAULTS,**changes)
  probe('eyeaux-'+label,lambda g,uv=uv,sc=sc,tint=tint:A.occlusion_transmittance(g,g.combine(*uv,0),sc,{'ShadowTint':(*tint,1)})[0],occ_oracle(uv,sc,tint))
 for angle in (0,60,80):
  sc=dict(A.TEAR_DEFAULTS)
  expected=(.04+.96*max(abs(1-max(math.cos(math.radians(angle)),0)),.0001))*.5
  probe('tear-angle-'+str(angle),lambda g:A.tearline_response(g,sc)['alpha'],expected,angle)
 for gap in (0,.025,.05,.1):
  depth=min(max(3*min(max(gap/.1,0),1)-1,0),1)
  expected=(.04+.96*.0001)*depth*.5
  probe('tear-depth-'+str(gap),lambda g,gap=gap:A.tearline_response(g,A.TEAR_DEFAULTS,depth_gap_cm=gap)['alpha'],expected)
 # Exercise actual material builders with authored overrides, without game assets.
 shell=OUT/'EyeShell.json';edge=OUT/'EyeEdge.json'
 shell.write_text(json.dumps({'Parameters':{'Scalars':dict(A.OCCLUSION_DEFAULTS)}}))
 edge.write_text(json.dumps({'Parameters':{'Scalars':dict(A.TEAR_DEFAULTS)}}))
 for role,path in [('eyeshell',shell),('eyeedge',edge)]:
  mat=A.authored_eye_aux_material(str(path),role)
  assert mat['arc_eye_aux_setup']=='native-eye-aux-v1'
  if role=='eyeshell':
   assert not any(n.type=='BSDF_PRINCIPLED' for n in mat.node_tree.nodes)
   assert mat['arc_eye_aux_native_identity']
   assert json.loads(mat['arc_eye_aux_unsupported'])==[]
  else:
   p=next(n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
   assert p.inputs['Transmission Weight'].default_value==0
   assert p.inputs['IOR'].default_value==1.5
   assert p.inputs['Metallic'].links[0].from_socket.default_value==1
   assert 'scene-depth' in mat['arc_eye_aux_unsupported']
  rows.append({'case':'authored-builder-'+role,'material':mat.name,'family':mat['arc_eye_aux_family'],'limitations':json.loads(mat['arc_eye_aux_unsupported'])})
 (OUT/'eye-aux-regression.json').write_text(json.dumps({'passed':True,'rows':rows},indent=2))
 print('EYE_AUX_REGRESSION_OK',len(rows))
except Exception:
 (OUT/'eye-aux-regression.json').write_text(json.dumps({'passed':False,'rows':rows,'error':traceback.format_exc()},indent=2));raise
