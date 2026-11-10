"""
Rig fixing and merging logic for the Arc Raiders Importer
"""

import bpy
import bmesh
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


def _activate_armature(arm):
    """Make *arm* active/selected so mode_set / edit ops can run (nested ops OK)."""
    ctx = bpy.context
    view = ctx.view_layer
    prev = view.objects.active
    try:
        arm.hide_set(False)
    except Exception:
        pass
    arm.hide_viewport = False
    try:
        arm.hide_select = False
    except Exception:
        pass
    if arm.name not in view.objects:
        try:
            ctx.scene.collection.objects.link(arm)
        except Exception:
            pass
    arm.select_set(True)
    view.objects.active = arm
    return prev


def _mode_set(arm, mode: str):
    """object.mode_set with a temp override — nested operators often lack active_object."""
    ctx = bpy.context
    _activate_armature(arm)
    kw = {
        "active_object": arm,
        "object": arm,
        "selected_editable_objects": [arm],
        "view_layer": ctx.view_layer,
        "scene": ctx.scene,
    }
    if ctx.window is not None:
        kw["window"] = ctx.window
    with ctx.temp_override(**kw):
        bpy.ops.object.mode_set(mode=mode)


def fix_bone_orientations(arm):
    arm.data.display_type = 'OCTAHEDRAL'
    arm.show_in_front = True
    prev = _activate_armature(arm)
    _mode_set(arm, "EDIT")

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

    _mode_set(arm, "OBJECT")
    if prev is not None:
        try:
            bpy.context.view_layer.objects.active = prev
        except Exception:
            pass


_SHAPE_BONE = "ArcBoneShape"
_SHAPE_MARKER = "ArcBoneMarker"


def _shape_object(name: str, verts, faces):
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type == 'MESH':
        return obj
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)  # never linked to a scene
    return obj


def _bone_shapes():
    r = 0.1
    bone = _shape_object(
        _SHAPE_BONE,
        [(0, 0, 0), (r, r, r), (r, r, -r), (-r, r, -r), (-r, r, r), (0, 1, 0)],
        [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 1), (5, 2, 1), (5, 3, 2), (5, 4, 3), (5, 1, 4)],
    )
    marker = _shape_object(
        _SHAPE_MARKER,
        [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)],
        [(0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4), (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5)],
    )
    return bone, marker


def _bone_collection(arm_data, name: str):
    coll = arm_data.collections.get(name)
    return coll if coll is not None else arm_data.collections.new(name)


def apply_bone_display(arm) -> int:
    """Octahedral bones aimed at the next joint, markers on leaves and face bones.

    Display only: pose-bone custom shapes. Rest matrices stay in the UE frames
    the PSA importer reads, so poses and animations are unaffected.
    """
    if arm is None or getattr(arm, "type", "") != 'ARMATURE':
        return 0
    bone_shape, marker_shape = _bone_shapes()
    bones = arm.data.bones
    heads = {b.name: b.head_local.copy() for b in bones}
    y_axis = Vector((0.0, 1.0, 0.0))
    body = _bone_collection(arm.data, "Body")
    face = _bone_collection(arm.data, "Face")
    shaped = 0
    for pb in arm.pose.bones:
        b = pb.bone
        low = b.name.lower()
        is_face = low.startswith("facial")
        (face if is_face else body).assign(b)
        target = None
        if not is_face and not _is_helper_bone(b.name) and "root" not in low:
            target = find_joint_target(b, heads, b.x_axis)
            if target is None:
                kids = [
                    c for c in b.children
                    if not _is_helper_bone(c.name) and (heads[c.name] - heads[b.name]).length > 0.005
                ]
                if kids:
                    target = min(kids, key=lambda c: (heads[c.name] - heads[b.name]).length)
        pb.use_custom_shape_bone_size = False
        pb.custom_shape_translation = (0.0, 0.0, 0.0)
        if target is not None:
            d = b.matrix_local.inverted() @ heads[target.name]
            length = d.length
            pb.custom_shape = bone_shape
            pb.custom_shape_rotation_euler = y_axis.rotation_difference(d).to_euler()
            pb.custom_shape_scale_xyz = (length, length, length)
        else:
            size = 0.0025 if is_face else (0.03 if "root" in low else 0.01)
            pb.custom_shape = marker_shape
            pb.custom_shape_rotation_euler = (0.0, 0.0, 0.0)
            pb.custom_shape_scale_xyz = (size, size, size)
        shaped += 1
    arm.data.display_type = 'OCTAHEDRAL'
    arm.show_in_front = True
    return shaped


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
    prev = _activate_armature(arm)
    _mode_set(arm, "EDIT")

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

    _mode_set(arm, "OBJECT")
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
    _activate_armature(master)
    ctx = bpy.context
    kw = {
        "active_object": master,
        "object": master,
        "selected_editable_objects": list(armatures),
        "view_layer": ctx.view_layer,
        "scene": ctx.scene,
    }
    if ctx.window is not None:
        kw["window"] = ctx.window
    with ctx.temp_override(**kw):
        bpy.ops.object.join()
    return bpy.context.active_object or master, shared_bone_names


def cleanup_duplicate_bones(master_arm, shared_bone_names, mesh_objects=None):
    mesh_objects = (
        [obj for obj in bpy.data.objects if obj.type == 'MESH']
        if mesh_objects is None
        else [obj for obj in mesh_objects if obj.type == 'MESH']
    )
    _mode_set(master_arm, "EDIT")
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

    _mode_set(master_arm, "OBJECT")

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
        print("Arc Asset Importer: No armatures found to fix.")
        return

    if merge and len(armatures) > 1:
        print(f"Arc Asset Importer: Merging {len(armatures)} armatures...")
        master, shared = merge_armatures(armatures)
        cleanup_duplicate_bones(master, shared, mesh_objects)
        print("Arc Asset Importer: Merge complete.")
    else:
        master = armatures[0]
        if len(armatures) > 1:
            print(f"Arc Asset Importer: Skipping merge for non-clothing import ({len(armatures)} armatures).")
        else:
            print("Arc Asset Importer: Single armature, skipping merge.")

    if model_type == "weapon":
        print("Arc Asset Importer: Fixing weapon bone axes (all armatures)...")
        for arm in armatures:
            fix_weapon_bone_orientations(arm)

    for obj in mesh_objects:
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE':
                mod.use_deform_preserve_volume = True

    # Mesh object origins at part centers (skeleton stays at feet). May split
    # gloves/boots into L/R so scale pivots sit near each hand/foot.
    created = recenter_imported_mesh_origins(mesh_objects)
    if created:
        all_new_objects.extend(created)

    if model_type != "weapon":
        for arm in armatures:
            try:
                if arm.name in bpy.data.objects:
                    apply_bone_display(arm)
            except ReferenceError:
                pass  # merged away


# ---------------------------------------------------------------------------
# Mesh origins (scale pivots) — leave armature at feet
# ---------------------------------------------------------------------------

_PAIRED_PART_RE = re.compile(
    r"(glove|mitt|gauntlet|boot|shoe|footwear|sneaker|sandal)",
    re.IGNORECASE,
)


def _mesh_still_alive(obj) -> bool:
    try:
        obj.name
        return True
    except ReferenceError:
        return False


def _side_vertex_group_indices(obj, side: str) -> set[int]:
    """Vertex-group indices whose names belong to left ('l') or right ('r')."""
    side = (side or "").lower()
    out: set[int] = set()
    suffix = f"_{side}"
    for vg in obj.vertex_groups:
        n = (vg.name or "").lower()
        n = re.sub(r"\.\d{3}$", "", n)
        if n.endswith(suffix) or n.endswith(f".{side}"):
            out.add(vg.index)
    return out


def _connected_vert_islands(obj) -> list[list[int]]:
    """Vertex-index islands (connected components) on a mesh."""
    me = obj.data
    if not me or not me.vertices:
        return []
    # Edge adjacency from mesh edges (faster than bmesh for large hair cards).
    n = len(me.vertices)
    adj = [[] for _ in range(n)]
    for e in me.edges:
        a, b = e.vertices
        adj[a].append(b)
        adj[b].append(a)
    seen = [False] * n
    islands: list[list[int]] = []
    for i in range(n):
        if seen[i]:
            continue
        stack = [i]
        comp: list[int] = []
        seen[i] = True
        while stack:
            v = stack.pop()
            comp.append(v)
            for nb in adj[v]:
                if not seen[nb]:
                    seen[nb] = True
                    stack.append(nb)
        islands.append(comp)
    return islands


def _island_lr_side(obj, indices: list[int], left_gis: set[int], right_gis: set[int]) -> str:
    wl = wr = 0.0
    for vi in indices:
        for g in obj.data.vertices[vi].groups:
            if g.group in left_gis:
                wl += float(g.weight)
            elif g.group in right_gis:
                wr += float(g.weight)
    if wl > 1e-6 or wr > 1e-6:
        return "L" if wl >= wr else "R"
    # No hand/foot weights: fall back to island centroid X in object space.
    cx = 0.0
    for vi in indices:
        cx += obj.data.vertices[vi].co.x
    cx /= max(len(indices), 1)
    return "L" if cx < 0.0 else "R"


def _delete_verts_keep_side(obj, keep_side: str, left_gis: set[int], right_gis: set[int]) -> bool:
    """Delete verts belonging to the opposite side (island majority). Returns True if culled."""
    islands = _connected_vert_islands(obj)
    if len(islands) < 2:
        return False
    delete: list[int] = []
    kept = 0
    for isl in islands:
        side = _island_lr_side(obj, isl, left_gis, right_gis)
        if side == keep_side:
            kept += 1
        else:
            delete.extend(isl)
    if not delete or kept == 0:
        return False
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    kill = {i for i in delete}
    to_del = [bm.verts[i] for i in kill if i < len(bm.verts)]
    bmesh.ops.delete(bm, geom=to_del, context="VERTS")
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    return True


def split_paired_lr_meshes(objects: list) -> list:
    """Split glove/boot-style meshes into ``*_L`` / ``*_R`` objects.

    Returns newly created objects (the R half). Original becomes the L half.
    Armature parent + modifiers are preserved on the duplicate.
    """
    created: list = []
    meshes = [o for o in objects if _mesh_still_alive(o) and o.type == "MESH"]
    for obj in meshes:
        if obj.get("arc_lr_split"):
            continue
        if not _PAIRED_PART_RE.search(obj.name or ""):
            continue
        left_gis = _side_vertex_group_indices(obj, "l")
        right_gis = _side_vertex_group_indices(obj, "r")
        if not left_gis or not right_gis:
            continue
        islands = _connected_vert_islands(obj)
        if len(islands) < 2:
            continue
        sides = {_island_lr_side(obj, isl, left_gis, right_gis) for isl in islands}
        if sides != {"L", "R"}:
            continue

        # Duplicate full mesh, then cull opposite sides.
        dup = obj.copy()
        dup.data = obj.data.copy()
        linked = False
        for coll in list(obj.users_collection):
            try:
                coll.objects.link(dup)
                linked = True
            except RuntimeError:
                pass
        if not linked:
            bpy.context.scene.collection.objects.link(dup)
        # Keep parenting / armature binding.
        dup.parent = obj.parent
        try:
            dup.matrix_parent_inverse = obj.matrix_parent_inverse.copy()
        except Exception:
            pass
        for mod in dup.modifiers:
            if mod.type == "ARMATURE" and obj.modifiers.get(mod.name):
                src = obj.modifiers[mod.name]
                mod.object = src.object

        if not _delete_verts_keep_side(obj, "L", left_gis, right_gis):
            bpy.data.objects.remove(dup, do_unlink=True)
            continue
        if not _delete_verts_keep_side(dup, "R", left_gis, right_gis):
            bpy.data.objects.remove(dup, do_unlink=True)
            continue

        base = re.sub(r"\.\d{3}$", "", obj.name)
        obj.name = f"{base}_L"[:63]
        dup.name = f"{base}_R"[:63]
        try:
            obj["arc_lr_split"] = "L"
            dup["arc_lr_split"] = "R"
            if obj.get("arc_psk_path"):
                dup["arc_psk_path"] = obj.get("arc_psk_path")
            if obj.get("arc_model_type"):
                dup["arc_model_type"] = obj.get("arc_model_type")
        except Exception:
            pass
        created.append(dup)
        print(f"Arc Asset Importer: Split paired mesh → {obj.name} / {dup.name}")
    return created


def _origin_to_geometry_bounds(obj) -> bool:
    """Set object origin to bbox center without moving the mesh in world space."""
    if not _mesh_still_alive(obj) or obj.type != "MESH":
        return False
    if not obj.data or not obj.data.vertices:
        return False
    if obj.get("arc_origin_recentered"):
        return False
    view_layer = bpy.context.view_layer
    prev_active = view_layer.objects.active
    prev_selected = [o for o in bpy.context.selected_objects]
    was_hide = False
    try:
        was_hide = bool(obj.hide_get())
    except Exception:
        pass
    mode = getattr(bpy.context, "mode", "OBJECT") or "OBJECT"
    try:
        if mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    try:
        if was_hide:
            try:
                obj.hide_set(False)
            except Exception:
                obj.hide_viewport = False
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        view_layer.objects.active = obj
        bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
        try:
            obj["arc_origin_recentered"] = 1
        except Exception:
            pass
        return True
    except Exception as exc:
        print(f"Arc Asset Importer: origin recenter failed on {obj.name}: {exc}")
        return False
    finally:
        try:
            bpy.ops.object.select_all(action="DESELECT")
            for o in prev_selected:
                if _mesh_still_alive(o):
                    o.select_set(True)
            if prev_active is not None and _mesh_still_alive(prev_active):
                view_layer.objects.active = prev_active
            if was_hide:
                try:
                    obj.hide_set(True)
                except Exception:
                    pass
        except Exception:
            pass


def recenter_imported_mesh_origins(objects: list) -> list:
    """Put each mesh origin at its geometry center; split gloves/boots L/R first.

    Armatures are left at the feet (whole-character scale). Returns any new
    objects created by L/R splits so callers can track them.
    """
    if not objects:
        return []
    created = split_paired_lr_meshes(objects)
    meshes = [
        o for o in list(objects) + created
        if _mesh_still_alive(o) and o.type == "MESH"
    ]
    n = 0
    for obj in meshes:
        if _origin_to_geometry_bounds(obj):
            n += 1
    if n:
        print(f"Arc Asset Importer: Recentered {n} mesh origin(s) to part centers")
    return created
