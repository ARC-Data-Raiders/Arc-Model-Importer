"""
Rig fixing and merging logic for the Arc Raiders Importer
"""

import bpy
import re
from mathutils import Vector

def find_joint_target(bone):
    """Recursively skip zero-length helper bones to find the real next joint."""
    for child in bone.children:
        dist = (child.head - bone.head).length
        if dist > 0.04:
            return child
        result = find_joint_target(child)
        if result:
            return result
    return None

def fix_bone_orientations(arm):
    arm.data.display_type = 'OCTAHEDRAL'
    arm.show_in_front = True
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    
    HELPERS = ["roll", "vpj", "twist", "adj", "corr", "socket", "target", "ik", "pole", "jj", "offset"]
    
    for bone in arm.data.edit_bones:
        name_low = bone.name.lower()
        orig_matrix = bone.matrix.copy()
        orig_z = orig_matrix.col[2].to_3d()
        is_helper = any(x in name_low for x in HELPERS)
        
        if not is_helper and "root" not in name_low:
            target = find_joint_target(bone)
            if target:
                bone.tail = target.head
                try:
                    bone.align_roll(orig_z)
                except Exception:
                    pass
            else:
                bone.length = 0.05
        else:
            bone.length = 0.02
        
        if bone.length < 0.01:
            bone.length = 0.02
    
    bpy.ops.object.mode_set(mode='OBJECT')

def merge_armatures(armatures):
    master = max(armatures, key=lambda a: len(a.data.bones))
    
    from collections import Counter
    name_counts = Counter()
    for arm in armatures:
        for bone in arm.data.bones:
            name_counts[bone.name] += 1
    shared_bone_names = {name for name, count in name_counts.items() if count > 1}
    
    bpy.ops.object.select_all(action='DESELECT')
    for arm in armatures:
        arm.select_set(True)
    bpy.context.view_layer.objects.active = master
    bpy.ops.object.join()
    return bpy.context.active_object, shared_bone_names

def cleanup_duplicate_bones(master_arm, shared_bone_names):
    bpy.ops.object.mode_set(mode='EDIT')
    bone_map = {}
    all_edit_bones = sorted([b.name for b in master_arm.data.edit_bones], reverse=True)
    
    for b_name in all_edit_bones:
        match = re.match(r"(.*)\.\d{3}$", b_name)
        if match:
            orig_name = match.group(1)
            if orig_name not in shared_bone_names:
                continue
            b_dup = master_arm.data.edit_bones.get(b_name)
            b_orig = master_arm.data.edit_bones.get(orig_name)
            if b_dup and b_orig and (b_dup.head - b_orig.head).length < 0.001:
                bone_map[b_name] = orig_name
    
    for b_dup_name, b_orig_name in bone_map.items():
        b_dup = master_arm.data.edit_bones.get(b_dup_name)
        b_orig = master_arm.data.edit_bones.get(b_orig_name)
        if b_dup and b_orig:
            for child in b_dup.children:
                child.parent = b_orig
            master_arm.data.edit_bones.remove(b_dup)
    
    bpy.ops.object.mode_set(mode='OBJECT')
    
    for obj in bpy.data.objects:
        if obj.type == 'MESH':
            for mod in obj.modifiers:
                if mod.type == 'ARMATURE' and mod.object is not master_arm:
                    mod.object = master_arm
    
    for obj in bpy.data.objects:
        if obj.type == 'MESH':
            for b_dup_name, b_orig_name in bone_map.items():
                vg_dup = obj.vertex_groups.get(b_dup_name)
                vg_orig = obj.vertex_groups.get(b_orig_name)
                if vg_dup:
                    if not vg_orig:
                        vg_dup.name = b_orig_name
                    else:
                        mod = obj.modifiers.new(name="TempWeightMerge", type='VERTEX_WEIGHT_MIX')
                        mod.vertex_group_a = b_orig_name
                        mod.vertex_group_b = b_dup_name
                        mod.mix_mode = 'ADD'
                        mod.mix_set = 'ALL'
                        prev_active = bpy.context.view_layer.objects.active
                        bpy.context.view_layer.objects.active = obj
                        bpy.ops.object.modifier_apply(modifier=mod.name)
                        bpy.context.view_layer.objects.active = prev_active
                        obj.vertex_groups.remove(vg_dup)

def fix_rig_all(all_new_objects: list, merge: bool = True, model_type: str = ""):
    """Fix and merge all armatures. merge: if False, skip merge step."""
    armatures = [o for o in all_new_objects if o.type == 'ARMATURE']
    if not armatures:
        print("Arc Raiders PSK Importer: No armatures found to fix.")
        return
    
    if merge and len(armatures) > 1:
        print(f"Arc Raiders PSK Importer: Merging {len(armatures)} armatures...")
        master, shared = merge_armatures(armatures)
        cleanup_duplicate_bones(master, shared)
        print("Arc Raiders PSK Importer: Merge complete.")
    else:
        master = armatures[0]
        if len(armatures) > 1:
            print(f"Arc Raiders PSK Importer: Skipping merge for non-clothing import ({len(armatures)} armatures).")
        else:
            print("Arc Raiders PSK Importer: Single armature, skipping merge.")
    
    if model_type == "weapon":
        print("Arc Raiders PSK Importer: Setting weapon bone lengths...")
        master.data.display_type = 'OCTAHEDRAL'
        master.show_in_front = True
        bpy.context.view_layer.objects.active = master
        bpy.ops.object.mode_set(mode='EDIT')
        MIN_LENGTH = 5.0
        for bone in master.data.edit_bones:
            orig_z = bone.matrix.copy().col[2].to_3d()
            target = None
            for child in bone.children:
                if (child.head - bone.head).length > 0.0001:
                    target = child
                    break
            if target:
                bone.tail = target.head
                try:
                    bone.align_roll(orig_z)
                except Exception:
                    pass
            else:
                if bone.parent:
                    parent_dir = bone.parent.tail - bone.parent.head
                    if parent_dir.length > 0.0001:
                        bone.tail = bone.head + parent_dir.normalized() * MIN_LENGTH
                    else:
                        bone.tail = bone.head + Vector((0, MIN_LENGTH, 0))
                else:
                    bone.tail = bone.head + Vector((0, 0, MIN_LENGTH))
            if bone.length < 0.001:
                bone.length = MIN_LENGTH
        bpy.ops.object.mode_set(mode='OBJECT')
    else:
        print("Arc Raiders PSK Importer: Fixing bone orientations...")
        fix_bone_orientations(master)
    
    for obj in bpy.data.objects:
        if obj.type == 'MESH':
            for mod in obj.modifiers:
                if mod.type == 'ARMATURE':
                    mod.use_deform_preserve_volume = True