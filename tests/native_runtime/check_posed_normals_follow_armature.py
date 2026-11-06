"""Blender check: importer custom normals must follow Armature deformation.

Run: blender --background --factory-startup --python tests/native_runtime/check_posed_normals_follow_armature.py
Exit 0 when posed shading normals stay within 5 degrees (median) of the posed
smooth surface normals; exit 1 otherwise. Free FLOAT_VECTOR custom normals stay
in rest space when the Armature modifier bends the mesh, which shades posed
clothing faceted (inner thighs, crotch).
"""
import bpy,math,sys
from pathlib import Path
from mathutils import Vector,Matrix
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from material_runtime.deformation import write_corner_normals

bpy.ops.wm.read_factory_settings(use_empty=True)
# Upright tube, 2 m tall, 16 sides, 20 rings, skinned to two bones that meet at z=1.
sides,rings=16,20
verts=[(math.cos(2*math.pi*s/sides)*.2,math.sin(2*math.pi*s/sides)*.2,2*r/rings) for r in range(rings+1) for s in range(sides)]
faces=[(r*sides+s,r*sides+(s+1)%sides,(r+1)*sides+(s+1)%sides,(r+1)*sides+s) for r in range(rings) for s in range(sides)]
me=bpy.data.meshes.new('PosedNormalTube');me.from_pydata(verts,[],faces);me.update()
me.polygons.foreach_set('use_smooth',[True]*len(me.polygons))
radial=[Vector((me.vertices[l.vertex_index].co.x,me.vertices[l.vertex_index].co.y,0)).normalized() for l in me.loops]
write_corner_normals(me,[tuple(v) for v in radial])
ob=bpy.data.objects.new('PosedNormalTube',me);bpy.context.collection.objects.link(ob)
ad=bpy.data.armatures.new('PosedNormalRig');rig=bpy.data.objects.new('PosedNormalRig',ad);bpy.context.collection.objects.link(rig)
bpy.context.view_layer.objects.active=rig;bpy.ops.object.mode_set(mode='EDIT')
lower=ad.edit_bones.new('lower');lower.head=(0,0,0);lower.tail=(0,0,1)
upper=ad.edit_bones.new('upper');upper.head=(0,0,1);upper.tail=(0,0,2);upper.parent=lower;upper.use_connect=True
bpy.ops.object.mode_set(mode='OBJECT')
gl=ob.vertex_groups.new(name='lower');gu=ob.vertex_groups.new(name='upper')
for v in me.vertices:
    t=min(1.,max(0.,(v.co.z-.8)/.4));gl.add([v.index],1-t,'REPLACE');gu.add([v.index],t,'REPLACE')
mod=ob.modifiers.new('Armature','ARMATURE');mod.object=rig
pb=rig.pose.bones['upper'];pb.rotation_mode='XYZ';pb.rotation_euler=(math.radians(90),0,0)
bpy.context.view_layer.update()
dg=bpy.context.evaluated_depsgraph_get();ev=ob.evaluated_get(dg).to_mesh()
# Reference: area-weighted smooth normal of the posed surface at each vertex.
acc=[Vector() for _ in ev.vertices]
for p in ev.polygons:
    for vi in p.vertices:acc[vi]+=p.normal*p.area
dev=sorted(math.degrees(Vector(c.vector).angle(acc[ev.loops[i].vertex_index].normalized(),0)) for i,c in enumerate(ev.corner_normals))
upper_dev=sorted(math.degrees(Vector(c.vector).angle(acc[ev.loops[i].vertex_index].normalized(),0)) for i,c in enumerate(ev.corner_normals) if me.vertices[ev.loops[i].vertex_index].co.z>1.3)
stored=me.attributes.get('custom_normal')
median=dev[len(dev)//2];upper_median=upper_dev[len(upper_dev)//2]
print('POSED_NORMALS storage',stored and stored.data_type,'median_deg %.2f'%median,'upper_median_deg %.2f'%upper_median)
ok=median<5 and upper_median<5
print('POSED_NORMALS','PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
