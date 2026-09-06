"""
Rig fixing and merging logic for the Arc Raiders Importer
"""

import bpy
import re
from mathutils import Vector

# Accessory / twist / roll helpers — never use these as the next deform joint.
_HELPERS = (
    "roll", "vpj", "twist", "adj", "corr", "socket", "target",
    "ik", "pole", "jj", "offset",
)

# Preferred next deform bone (stem → stems), after stripping side suffix _l/_r.
_CHAIN_NEXT = {
    "pelvis": ("spine_01", "spine01", "spine"),
    "spine_01": ("spine_02", "spine02"),
    "spine_02": ("spine_03", "spine03"),
    "spine_03": ("neck_01", "neck01", "neck"),
    "spine_04": ("neck_01", "neck01", "neck"),
    "spine_05": ("neck_01", "neck01", "neck"),
    "neck_01": ("neck_02", "neck02", "head"),
    "neck_02": ("head",),
    "thigh": ("calf",),
    "calf": ("foot",),
    "foot": ("ball", "toe"),
    "clavicle": ("upperarm",),
    "upperarm": ("lowerarm",),
    "lowerarm": ("hand",),
    "hand": ("middle_01", "ring_01", "index_01", "pinky_01", "thumb_01"),
}


def _bone_stem(name: str) -> str:
    n = (name or "").lower()
    n = re.sub(r"\.\d{3}$", "", n)
    n = re.sub(r"_[lr]$", "", n)
    return n


def _is_helper_bone(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in _HELPERS)


def _chain_score(bone_name: str, child_name: str) -> int:
    bstem = _bone_stem(bone_name)
    cstem = _bone_stem(child_name)
    preferred = _CHAIN_NEXT.get(bstem)
    if not preferred:
        # Exact key first, then longest prefix (avoid 'hand' matching 'handle').
        best_key = ""
        for key in _CHAIN_NEXT:
            if bstem == key or bstem.startswith(key + "_"):
                if len(key) > len(best_key):
                    best_key = key
        if best_key:
            preferred = _CHAIN_NEXT[best_key]
        elif bstem.startswith("spine"):
            preferred = _CHAIN_NEXT["spine_03"]
    if not preferred:
        return 0
    for i, pref in enumerate(preferred):
        if cstem == pref or cstem.startswith(pref):
            return 100 - i
    return 0


def find_joint_target(bone, orig_heads: dict, orig_dir: Vector):
    """Pick the real next deform joint, skipping helper/accessory bones.

    Uses snapshotted rest heads so processing order cannot cascade through
    connected (or previously edited) bones. Prefers anatomical chain names
    (spine→spine/neck, thigh→calf) over whatever child happens to be first —
    merge/join often prepends clothing JJ bones and reorders L/R limbs.
    """
    origin = orig_heads.get(bone.name)
    if origin is None:
        origin = bone.head.copy()

    candidates = []

    def walk(node):
        for child in node.children:
            head = orig_heads.get(child.name)
            if head is None:
                head = child.head.copy()
            dist = (head - origin).length
            if _is_helper_bone(child.name):
                walk(child)
                continue
            if dist > 0.04:
                candidates.append(child)
            else:
                walk(child)

    walk(bone)
    if not candidates:
        return None

    def score(ch):
        head = orig_heads.get(ch.name, ch.head)
        vec = head - origin
        align = 0.0
        if vec.length > 1e-8 and orig_dir.length > 1e-8:
            align = vec.normalized().dot(orig_dir.normalized())
        side = 0
        bn = bone.name.lower()
        cn = ch.name.lower()
        if bn.endswith("_l") and cn.endswith("_l"):
            side = 1
        elif bn.endswith("_r") and cn.endswith("_r"):
            side = 1
        return (_chain_score(bone.name, ch.name), side, align)

    return max(candidates, key=score)


def fix_bone_orientations(arm):
    arm.data.display_type = 'OCTAHEDRAL'
    arm.show_in_front = True
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = list(arm.data.edit_bones)
    orig_heads = {b.name: b.head.copy() for b in edit_bones}
    orig_dirs = {b.name: (b.tail - b.head).copy() for b in edit_bones}

    for bone in edit_bones:
        name_low = bone.name.lower()
        orig_matrix = bone.matrix.copy()
        orig_z = orig_matrix.col[2].to_3d()
        is_helper = _is_helper_bone(bone.name)

        if not is_helper and "root" not in name_low:
            target = find_joint_target(bone, orig_heads, orig_dirs.get(bone.name, Vector((0, 1, 0))))
            if target:
                # Aim at the target's *original* rest head (pre-pass snapshot).
                bone.tail = orig_heads.get(target.name, target.head).copy()
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


# Attachment / socket leaves under weapon_root — never aim these at siblings.
_WEAPON_ATTACHMENT_NAMES = {
    "muzzle", "shelleject", "stock", "weapon_mag", "weapon_magazine",
    "weapon_underbarrel", "weapon_grip", "weapon_root", "root",
}


def _is_weapon_attachment_leaf(name: str) -> bool:
    n = (name or "").lower()
    n = re.sub(r"\.\d{3}$", "", n)
    if n in _WEAPON_ATTACHMENT_NAMES:
        return True
    if n.startswith("weapon_"):
        return True
    if n in ("muzzle", "stock", "shelleject"):
        return True
    return False


def _weapon_axis_heuristic(name: str) -> Vector:
    """Fallback axis when an attachment bone imports with zero length."""
    n = (name or "").lower()
    if "muzzle" in n or "shell" in n:
        return Vector((0.0, 1.0, 0.0))
    if "stock" in n:
        return Vector((0.0, -1.0, 0.0))
    if "mag" in n:
        return Vector((0.0, 0.0, -1.0))
    if "underbarrel" in n or "grip" in n:
        return Vector((0.0, 0.0, -1.0))
    return Vector((0.0, 1.0, 0.0))


def fix_weapon_bone_orientations(arm, min_length: float = 5.0):
    """Preserve attachment axes; only aim true deform chains at child heads.

    Under ``weapon_root``, siblings (Muzzle / Stock / weapon_mag / …) must not
    be aimed at each other — that collapses every socket onto one axis and
    breaks mod bone-parent snap.
    """
    if arm is None or arm.type != "ARMATURE":
        return
    arm.data.display_type = "OCTAHEDRAL"
    arm.show_in_front = True
    prev = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")

    edit_bones = list(arm.data.edit_bones)
    orig_heads = {b.name: b.head.copy() for b in edit_bones}
    orig_dirs = {b.name: (b.tail - b.head).copy() for b in edit_bones}
    orig_z = {b.name: b.matrix.copy().col[2].to_3d() for b in edit_bones}

    for bone in edit_bones:
        name_low = bone.name.lower()
        snap_dir = orig_dirs.get(bone.name, Vector((0, 1, 0)))
        is_attach = _is_weapon_attachment_leaf(bone.name)
        is_root = "root" in name_low and (
            name_low.endswith("root") or name_low in ("weapon_root", "root")
        )

        # Attachment leaves + weapon_root: keep import axis, just ensure length.
        if is_attach or is_root:
            direction = snap_dir
            if direction.length < 1e-6:
                direction = _weapon_axis_heuristic(bone.name)
            bone.tail = bone.head + direction.normalized() * max(min_length, 0.05)
            try:
                bone.align_roll(orig_z.get(bone.name, Vector((0, 0, 1))))
            except Exception:
                pass
            continue

        # Chain bones: prefer a real deform child (not another attachment socket).
        deform_child = None
        best_dist = 0.0
        for child in bone.children:
            if _is_weapon_attachment_leaf(child.name) or _is_helper_bone(child.name):
                continue
            head = orig_heads.get(child.name, child.head)
            dist = (head - bone.head).length
            if dist > 0.0001 and dist >= best_dist:
                deform_child = child
                best_dist = dist

        if deform_child is not None:
            bone.tail = orig_heads.get(deform_child.name, deform_child.head).copy()
            try:
                bone.align_roll(orig_z.get(bone.name, Vector((0, 0, 1))))
            except Exception:
                pass
        else:
            direction = snap_dir
            if direction.length < 1e-6:
                direction = _weapon_axis_heuristic(bone.name)
            bone.tail = bone.head + direction.normalized() * max(min_length, 0.05)
            try:
                bone.align_roll(orig_z.get(bone.name, Vector((0, 0, 1))))
            except Exception:
                pass

        if bone.length < 0.001:
            bone.length = min_length

    bpy.ops.object.mode_set(mode="OBJECT")
    if prev is not None:
        try:
            bpy.context.view_layer.objects.active = prev
        except Exception:
            pass


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


def cleanup_duplicate_bones(master_arm, shared_bone_names, mesh_objects=None):
    mesh_objects = (
        [obj for obj in bpy.data.objects if obj.type == 'MESH']
        if mesh_objects is None
        else [obj for obj in mesh_objects if obj.type == 'MESH']
    )
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

    for obj in mesh_objects:
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE' and mod.object is not master_arm:
                mod.object = master_arm

    for obj in mesh_objects:
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
    mesh_objects = [o for o in all_new_objects if o.type == 'MESH']
    if not armatures:
        print("Arc Raiders PSK Importer: No armatures found to fix.")
        return

    if merge and len(armatures) > 1:
        print(f"Arc Raiders PSK Importer: Merging {len(armatures)} armatures...")
        master, shared = merge_armatures(armatures)
        cleanup_duplicate_bones(master, shared, mesh_objects)
        print("Arc Raiders PSK Importer: Merge complete.")
    else:
        master = armatures[0]
        if len(armatures) > 1:
            print(f"Arc Raiders PSK Importer: Skipping merge for non-clothing import ({len(armatures)} armatures).")
        else:
            print("Arc Raiders PSK Importer: Single armature, skipping merge.")

    if model_type == "weapon":
        print("Arc Raiders PSK Importer: Fixing weapon bone axes (all armatures)...")
        for arm in armatures:
            fix_weapon_bone_orientations(arm)
    else:
        print("Arc Raiders PSK Importer: Fixing bone orientations...")
        fix_bone_orientations(master)

    for obj in mesh_objects:
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE':
                mod.use_deform_preserve_volume = True
