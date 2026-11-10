"""Still poses for the character dropdown.

Each entry except A Pose is one Unreal pose asset (``HM_*_P_*``, a single
frame). Full clips stay in the Animations panel.
"""
from __future__ import annotations

import os

import bpy

from . import animation_import as aimp
from ..Shared import utils

# Relative to PioneerGame/Content/Pioneer.
_DB = "Characters/Avatar/Animations/AnimationDatabase"

# (id, label, pioneer-relative psa or "" for bind pose)
POSE_PRESETS = (
    ("APOSE", "A Pose (Default)", ""),
    ("IDLE", "Idle", f"{_DB}/NewBaseLoco/HM_Loco_P_UA_StandIdle_00.psa"),
    ("TOOL", "Holding Raider Tool", f"{_DB}/NewBaseWep/HM_Item_P_RT_OneHanded_00.psa"),
    ("GUN_1H", "Holding Gun 1 Handed", f"{_DB}/NewBaseWep/HM_Wep_P_HG_GunDown_00.psa"),
    ("GUN_2H", "Holding Gun 2 Handed", f"{_DB}/NewBaseWep/HM_Wep_P_AR_GunDown_00.psa"),
)

_CHAR_ROLES = {"Body", "Head", "Hair", "FacialHair", "Outfit"}
_PRESET_BY_ID = {item[0]: item for item in POSE_PRESETS}
_APPLYING = False


def applying() -> bool:
    return _APPLYING


def set_applying(value: bool) -> None:
    global _APPLYING
    _APPLYING = bool(value)


def on_pose_preset_update(self, context):
    """Defer apply so the RNA update is not inside the property write."""
    # Skip addon register / file load. A UI change has a screen area.
    if _APPLYING or getattr(context, "area", None) is None:
        return
    pose_id = getattr(self, "arc_pose_preset", "APOSE") or "APOSE"

    def _run():
        if _APPLYING:
            return None
        try:
            bpy.ops.arc_outfits.apply_pose_preset()
        except Exception as exc:
            print(f"Arc Raiders: pose preset apply failed ({pose_id}): {exc}")
        return None

    try:
        bpy.app.timers.register(_run, first_interval=0.0)
    except Exception:
        _run()


POSE_ENUM_ITEMS = tuple(
    (pid, label, "Bind pose" if pid == "APOSE" else f"Single frame: {label}")
    for pid, label, _rel in POSE_PRESETS
)


def preset_relpath(pose_id: str) -> str:
    item = _PRESET_BY_ID.get(pose_id or "")
    return item[2] if item else ""


def resolve_psa(pose_id: str) -> str:
    rel = preset_relpath(pose_id)
    if not rel:
        return ""
    root = utils.get_pioneer_root() or ""
    if not root:
        return ""
    path = os.path.join(root, "Content", "Pioneer", rel.replace("/", os.sep))
    if os.path.isfile(path):
        return path
    # Export layouts sometimes drop Content/Pioneer.
    alt = os.path.join(root, rel.replace("/", os.sep))
    return alt if os.path.isfile(alt) else path


def _slot_of(obj) -> int | None:
    if obj is None:
        return None
    if "arc_import_slot" in obj.keys():
        try:
            return int(obj["arc_import_slot"])
        except (TypeError, ValueError):
            return None
    for child in getattr(obj, "children", ()):
        if "arc_import_slot" in child.keys():
            try:
                return int(child["arc_import_slot"])
            except (TypeError, ValueError):
                return None
    return None


def _role_of(obj) -> str:
    role = str(obj.get("arc_assembly_role") or "")
    if role:
        return role
    for child in getattr(obj, "children", ()):
        role = str(child.get("arc_assembly_role") or "")
        if role:
            return role
    return ""


def character_armatures(context, primary):
    """Body, head, hair, and outfit armatures on the same import slot."""
    if primary is None:
        return []
    slot = _slot_of(primary)
    found = []
    for obj in context.scene.objects:
        if getattr(obj, "type", "") != "ARMATURE":
            continue
        role = _role_of(obj)
        if obj != primary and role not in _CHAR_ROLES:
            continue
        if slot is not None:
            other = _slot_of(obj)
            if other is not None and other != slot:
                continue
            if other is None and obj != primary:
                continue
        elif obj != primary:
            continue
        found.append(obj)
    if primary not in found:
        found.insert(0, primary)
    return found


def _clear_to_apose(armature) -> None:
    ad = armature.animation_data
    if ad is not None:
        ad.action = None
    for bone in armature.pose.bones:
        bone.location = (0.0, 0.0, 0.0)
        bone.scale = (1.0, 1.0, 1.0)
        if bone.rotation_mode == "QUATERNION":
            bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        else:
            bone.rotation_euler = (0.0, 0.0, 0.0)
    if "arc_applied_anim" in armature:
        del armature["arc_applied_anim"]
    armature["arc_pose_preset"] = "APOSE"


def _hold_frame(context, action) -> None:
    try:
        frame = int(action.frame_range[0])
    except Exception:
        frame = context.scene.frame_current
    try:
        if context.screen and context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)
    except Exception:
        pass
    try:
        context.scene.frame_set(frame)
    except Exception:
        context.scene.frame_current = frame


def apply_preset(context, pose_id: str) -> tuple[bool, str]:
    """Apply one still pose to the character armatures on the active slot."""
    pose_id = pose_id or "APOSE"
    if pose_id not in _PRESET_BY_ID:
        return False, f"Unknown pose: {pose_id}"
    primary = aimp.resolve_target_armature(context)
    if primary is None:
        return False, "Select a character armature first"
    arms = character_armatures(context, primary)

    if pose_id == "APOSE":
        for arm in arms:
            _clear_to_apose(arm)
        return True, f"A Pose on {len(arms)} armature(s)"

    psa = resolve_psa(pose_id)
    if not psa or not os.path.isfile(psa):
        return False, "Pose PSA not found under PioneerGame Root"
    stem = os.path.splitext(os.path.basename(psa))[0]
    action = None
    # One import per armature: Blender 5 keeps the curves on that object's action slot.
    for arm in arms:
        prefix = "" if len(arms) == 1 else f"{arm.name}_"
        ok, msg, action = aimp.import_psa_onto_armature(
            psa, arm, context, replace_previous=True, action_name_prefix=prefix,
        )
        if not ok or action is None:
            return False, msg
        arm["arc_pose_preset"] = pose_id
    if action is not None:
        _hold_frame(context, action)
    label = _PRESET_BY_ID[pose_id][1]
    return True, f"{label} ({stem}) on {len(arms)} armature(s)"
