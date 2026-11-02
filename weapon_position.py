"""Independent weapon display rigs and anchors; never edits a character rig."""
import math
import bpy
from mathutils import Matrix,Vector,Euler

POSITIONS=(('HORIZONTAL','Horizontal (Default)','Display horizontally'),('BACKPACK','On Backpack','Carry on the back of a mock skeleton'),('AIMED','Aimed','Shouldered on a posed mock skeleton'),('UNAIMED','Unaimed','Lowered ready pose on a mock skeleton'))

def _segments(mode):
    # Metre-space mock pose: +X forward, -Y character right, +Z up.
    rh=(.42,-.12,1.43) if mode=='AIMED' else (.36,-.13,1.08)
    lh=(.72,.04,1.42) if mode=='AIMED' else (.60,.03,.97)
    if mode in ['HORIZONTAL','BACKPACK']:rh=(0,-.30,.91);lh=(0,.30,.91)
    return [('root',(0,0,0),(0,0,.1),None),('pelvis',(0,0,.94),(0,0,1.05),'root'),('spine',(0,0,1.05),(0,0,1.47),'pelvis'),('neck',(0,0,1.47),(0,0,1.62),'spine'),('head',(0,0,1.62),(0,0,1.80),'neck'),
      ('upperarm_r',(0,-.20,1.47),(.08,-.36,1.20) if mode=='AIMED' else (.10,-.29,1.15),'spine'),('forearm_r',(.08,-.36,1.20) if mode=='AIMED' else (.10,-.29,1.15),rh,'upperarm_r'),('hand_r',rh,(rh[0]+.08,rh[1],rh[2]),'forearm_r'),
      ('upperarm_l',(0,.20,1.47),(.28,.31,1.15),'spine'),('forearm_l',(.28,.31,1.15),lh,'upperarm_l'),('hand_l',lh,(lh[0]+.08,lh[1],lh[2]),'forearm_l'),
      ('thigh_r',(0,-.1,.94),(.04,-.13,.50),'pelvis'),('calf_r',(.04,-.13,.50),(0,-.13,.08),'thigh_r'),('foot_r',(0,-.13,.08),(.16,-.13,.04),'calf_r'),('thigh_l',(0,.1,.94),(-.04,.13,.50),'pelvis'),('calf_l',(-.04,.13,.50),(0,.13,.08),'thigh_l'),('foot_l',(0,.13,.08),(.16,.13,.04),'calf_l')]

def create_mock(root):
    data=bpy.data.armatures.new(root.name+' Mock Skeleton')
    rig=bpy.data.objects.new(data.name,data);bpy.context.scene.collection.objects.link(rig)
    rig.parent=root;rig['arc_weapon_mock']=True;rig['arc_weapon_owner']=root.name
    rig.show_in_front=True;rig.hide_render=True;data.display_type='STICK'
    return rig

def apply(root,mode):
    if mode not in {x[0] for x in POSITIONS}:raise ValueError('Unknown gun position: '+mode)
    context=bpy.context
    if context.object and context.object.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
    rig=next((o for o in root.children if o.get('arc_weapon_mock')),None) or create_mock(root)
    old_active=context.view_layer.objects.active
    rig.hide_set(False);rig.select_set(True);context.view_layer.objects.active=rig
    bpy.ops.object.mode_set(mode='EDIT')
    for b in list(rig.data.edit_bones):rig.data.edit_bones.remove(b)
    for name,head,tail,parent in _segments(mode):
        b=rig.data.edit_bones.new(name);b.head=head;b.tail=tail
        if parent:b.parent=rig.data.edit_bones[parent]
    bpy.ops.object.mode_set(mode='OBJECT')
    anchor=next((o for o in root.children if o.get('arc_weapon_position_anchor')),None)
    if anchor is None:
        anchor=bpy.data.objects.new(root.name+' Weapon Anchor',None);context.scene.collection.objects.link(anchor);anchor.parent=root
        anchor['arc_weapon_position_anchor']=True;anchor['arc_weapon_owner']=root.name;anchor.empty_display_size=.08
    pose={'HORIZONTAL':((0,0,0),(0,0,0)),'AIMED':((.42,-.12,1.43),(0,0,0)),'UNAIMED':((.36,-.13,1.08),(0,math.radians(25),0)),'BACKPACK':((-.18,0,1.45),(0,math.radians(65),math.radians(10)))}[mode]
    anchor.matrix_basis=Matrix.Translation(Vector(pose[0]))@Euler(pose[1],'XYZ').to_matrix().to_4x4()
    rig.hide_set(mode=='HORIZONTAL');rig.select_set(False)
    root['arc_weapon_position']=mode
    context.view_layer.update()
    align_grip(root,anchor)
    if old_active and old_active.name in context.scene.objects:context.view_layer.objects.active=old_active
    context.view_layer.update()
    return anchor

def align_grip(root,anchor=None):
    if root.get('arc_weapon_position') not in ['AIMED','UNAIMED']:return
    main=next((o for o in root.children_recursive if o.get('arc_weapon_main')),None)
    if main is None:return
    anchor=anchor or next((o for o in root.children if o.get('arc_weapon_position_anchor')),None)
    grip=next((b for b in main.pose.bones if b.name.casefold()=='weapon_grip'),None)
    if anchor is None or grip is None:return
    bpy.context.view_layer.update()
    offset=anchor.matrix_world.inverted_safe()@(main.matrix_world@grip.head)
    matrix=anchor.matrix_basis.copy();matrix.translation-=matrix.to_3x3()@offset;anchor.matrix_basis=matrix
    bpy.context.view_layer.update()

def roots_for_selection(context):
    result=[]
    for o in context.selected_objects:
        while o and not o.get('arc_weapon_assembly'):o=o.parent
        if o and o not in result:result.append(o)
    if not result and not context.selected_objects:
        name=getattr(context.scene,'arc_weapon_last_assembly','')
        o=context.scene.objects.get(name)
        if o and o.get('arc_weapon_assembly'):result=[o]
    return result
