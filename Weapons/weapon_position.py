"""Independent weapon display rigs and anchors; never edits a character rig."""
import json
import math
import os
import bpy
from mathutils import Matrix,Vector,Euler,Quaternion

POSITIONS=(('HORIZONTAL','Horizontal (Default)','Display horizontally'),('BACKPACK','On Backpack','Carry on the back of a mock skeleton'),('AIMED','Aimed','Shouldered on a posed mock skeleton'),('UNAIMED','Unaimed','Lowered ready pose on a mock skeleton'),('WEAPON_ICON','Weapon Icon','The rotation, position and scale the game uses to capture the icon of this weapon, with the icon camera'))

def _segments(mode):
    # Metre-space mock pose: +X forward, -Y character right, +Z up.
    rh=(.42,-.12,1.43) if mode=='AIMED' else (.36,-.13,1.08)
    lh=(.72,.04,1.42) if mode=='AIMED' else (.60,.03,.97)
    if mode in ['HORIZONTAL','BACKPACK']:rh=(0,-.30,.91);lh=(0,.30,.91)
    return [('root',(0,0,0),(0,0,.1),None),('pelvis',(0,0,.94),(0,0,1.05),'root'),('spine',(0,0,1.05),(0,0,1.47),'pelvis'),('neck',(0,0,1.47),(0,0,1.62),'spine'),('head',(0,0,1.62),(0,0,1.80),'neck'),
      ('upperarm_r',(0,-.20,1.47),(.08,-.36,1.20) if mode=='AIMED' else (.10,-.29,1.15),'spine'),('forearm_r',(.08,-.36,1.20) if mode=='AIMED' else (.10,-.29,1.15),rh,'upperarm_r'),('hand_r',rh,(rh[0]+.08,rh[1],rh[2]),'forearm_r'),
      ('upperarm_l',(0,.20,1.47),(.28,.31,1.15),'spine'),('forearm_l',(.28,.31,1.15),lh,'upperarm_l'),('hand_l',lh,(lh[0]+.08,lh[1],lh[2]),'forearm_l'),
      ('thigh_r',(0,-.1,.94),(.04,-.13,.50),'pelvis'),('calf_r',(.04,-.13,.50),(0,-.13,.08),'thigh_r'),('foot_r',(0,-.13,.08),(.16,-.13,.04),'calf_r'),('thigh_l',(0,.1,.94),(-.04,.13,.50),'pelvis'),('calf_l',(-.04,.13,.50),(0,.13,.08),'thigh_l'),('foot_l',(0,.13,.08),(.16,.13,.04),'calf_l')]

_CAPTURE=None
_H=Matrix(((1,0,0),(0,-1,0),(0,0,1)))   # UE (left-handed, cm) <-> Blender (right-handed, m)

def capture_row(root):
    """DA_UIMetaData Big icon capture row (actor + camera transform) for the weapon of this assembly, or None."""
    global _CAPTURE
    if _CAPTURE is None:
        path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'assets','weapon_icon_capture.json')
        try:
            with open(path,'r',encoding='utf-8') as fh:_CAPTURE=json.load(fh).get('weapons') or {}
        except (OSError,ValueError) as exc:
            print('Arc Raiders: weapon icon capture data unreadable:',exc);_CAPTURE={}
    from . import weapon_catalog as catalog
    entry=catalog.weapon_by_key(str(root.get('arc_weapon_key') or '')) or {}
    return _CAPTURE.get(entry.get('stem') or '')

def _quat(t):
    q=t.get('Rotation') or {}
    return Quaternion((q.get('W',1.0),q.get('X',0.0),q.get('Y',0.0),q.get('Z',0.0))).to_matrix()

def _vec(d,default=0.0):
    d=d or {}
    return Vector((d.get('X',default),d.get('Y',default),d.get('Z',default)))

def capture_actor_matrix(t):
    # The game actor scale is (1.2,-1.2,1.2): the icon is a mirror image. The mirror is dropped here (it would
    # break the socket constraints of the attachments), so the gun keeps its real handedness at 1.2 scale.
    s=_vec(t.get('Scale3D'),1.0)
    m=(_H@_quat(t)@Matrix.Diagonal((abs(s.x),abs(s.y),abs(s.z)))@_H).to_4x4()
    m.translation=_H@_vec(t.get('Translation'))*.01
    return m

def capture_camera_matrix(t):
    r=_quat(t);f=_H@r.col[0];up=_H@r.col[2]
    m=Matrix((f.cross(up),up,-f)).transposed().to_4x4()
    m.translation=_H@_vec(t.get('Translation'))*.01
    return m

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
    capture=capture_row(root) if mode=='WEAPON_ICON' else None
    if mode=='WEAPON_ICON' and capture is None:
        print('Arc Raiders: no icon capture data for',root.get('arc_weapon_key'),'- using Horizontal');mode='HORIZONTAL'
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
    pose={'HORIZONTAL':((0,0,0),(0,0,0)),'WEAPON_ICON':((0,0,0),(0,0,0)),'AIMED':((.42,-.12,1.43),(0,0,0)),'UNAIMED':((.36,-.13,1.08),(0,math.radians(25),0)),'BACKPACK':((-.18,0,1.45),(0,math.radians(65),math.radians(10)))}[mode]
    anchor.matrix_basis=capture_actor_matrix(capture['actor']) if capture else Matrix.Translation(Vector(pose[0]))@Euler(pose[1],'XYZ').to_matrix().to_4x4()
    rig.hide_set(mode in ['HORIZONTAL','WEAPON_ICON']);rig.select_set(False)
    root['arc_weapon_position']=mode
    context.view_layer.update()
    align_grip(root,anchor)
    if capture:
        from ..Lighting import scenario_cameras
        if scenario_cameras.enabled(context.scene):scenario_cameras.weapon_icon(context.scene,root,capture_camera_matrix(capture['camera']))
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
