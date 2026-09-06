"""Assemble multi-mesh props from StaticMesh sockets + Blueprint SCS (no umap).

Ground truth for the Extraction Elevator:

* sockets on ``SM_ExtractionElevator_01_A`` (and door / piston child sockets)
* attach graph in ``BP_SalvageExtractionPoint_Elevator`` (SCS ``AttachToName`` +
  RelativeLocation / RelativeRotation / RelativeScale3D)

Imports every ``.psk``/``.pskx`` in the prop folder, duplicates shared meshes
(engines, doors, barriers), parents children to the owning socket mesh, and
applies transforms in Unreal centimetres (same space as single-model import).
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Any

_ELEVATOR_BP_REL = os.path.join(
    "Core", "GameModes", "Scavenge", "BP_SalvageExtractionPoint_Elevator.json",
)

# Folder-core pieces only (no scavenger debris / railings from other packs).
_CORE_MESH_PREFIXES = (
    "SM_ExtractionElevator_",
    "SM_Barrier_01",
    "SM_Barrier_02",
)


def _ue_entries(data) -> list[dict]:
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _vec(d: Any, *keys, default=(0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and isinstance(d[k], dict):
            v = d[k]
            return (
                float(v.get("X", 0.0)),
                float(v.get("Y", 0.0)),
                float(v.get("Z", 0.0)),
            )
    return default


def _rot(d: Any) -> tuple[float, float, float]:
    """Pitch, Yaw, Roll degrees."""
    if not isinstance(d, dict):
        return (0.0, 0.0, 0.0)
    r = d.get("RelativeRotation")
    if not isinstance(r, dict):
        return (0.0, 0.0, 0.0)
    return (
        float(r.get("Pitch", 0.0)),
        float(r.get("Yaw", 0.0)),
        float(r.get("Roll", 0.0)),
    )


def _scale(d: Any) -> tuple[float, float, float]:
    if not isinstance(d, dict):
        return (1.0, 1.0, 1.0)
    for key in ("RelativeScale", "RelativeScale3D"):
        s = d.get(key)
        if isinstance(s, dict):
            return (
                float(s.get("X", 1.0)),
                float(s.get("Y", 1.0)),
                float(s.get("Z", 1.0)),
            )
    return (1.0, 1.0, 1.0)


def unreal_to_blender_xyz_radians(pitch: float, yaw: float, roll: float) -> tuple[float, float, float]:
    """Match map_placement pass-through: (-roll, -pitch, yaw)."""
    return (math.radians(-roll), math.radians(-pitch), math.radians(yaw))


def parse_static_mesh_sockets(sm_json_path: str) -> dict[str, dict]:
    """SocketName → {location, rotation_pyr_deg, scale}."""
    out: dict[str, dict] = {}
    if not sm_json_path or not os.path.isfile(sm_json_path):
        return out
    with open(sm_json_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    for e in _ue_entries(data):
        props = e.get("Properties") or {}
        name = str(props.get("SocketName") or "").strip()
        if not name:
            continue
        out[name] = {
            "location": _vec(props, "RelativeLocation"),
            "rotation": _rot(props),
            "scale": _scale(props),
        }
    return out


def load_folder_socket_index(folder: str) -> dict[str, dict[str, dict]]:
    """mesh_stem → socket_name → transform."""
    index: dict[str, dict[str, dict]] = {}
    for name in os.listdir(folder):
        if not name.lower().endswith(".json"):
            continue
        stem = os.path.splitext(name)[0]
        socks = parse_static_mesh_sockets(os.path.join(folder, name))
        if socks:
            index[stem] = socks
    return index


def _mesh_stem_from_ref(ref: Any) -> str:
    if isinstance(ref, dict):
        op = str(ref.get("ObjectPath") or ref.get("ObjectName") or "")
    else:
        op = str(ref or "")
    leaf = op.replace("\\", "/").rstrip("/").split("/")[-1]
    stem = leaf.split(".", 1)[0]
    m = re.search(r"'([^']+)'", stem)
    if m:
        stem = m.group(1)
    if stem.startswith("StaticMesh'"):
        stem = stem[len("StaticMesh'") :]
    return stem


def _template_name_from_ref(tmpl: Any) -> str:
    if not isinstance(tmpl, dict):
        return ""
    on = str(tmpl.get("ObjectName") or "")
    if ":" in on:
        return on.split(":", 1)[-1].rstrip("'")
    m = re.search(r"'([^']+)'", on)
    return m.group(1) if m else on


def parse_bp_mesh_attachments(bp_json_path: str) -> list[dict]:
    """List of attachment rows from a BP dump."""
    if not bp_json_path or not os.path.isfile(bp_json_path):
        return []
    with open(bp_json_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    entries = _ue_entries(data)

    templates: dict[str, dict] = {}
    for e in entries:
        if e.get("Type") != "StaticMeshComponent":
            continue
        templates[str(e.get("Name") or "")] = e.get("Properties") or {}

    rows: list[dict] = []
    for e in entries:
        if e.get("Type") != "SCS_Node":
            continue
        props = e.get("Properties") or {}
        var = str(props.get("InternalVariableName") or "").strip()
        attach = props.get("AttachToName")
        parent = props.get("ParentComponentOrVariableName")
        tmpl_name = _template_name_from_ref(props.get("ComponentTemplate"))
        tprops = templates.get(tmpl_name) or {}
        if not tprops:
            for k, v in templates.items():
                if tmpl_name and (k.endswith(tmpl_name) or tmpl_name.endswith(k)):
                    tprops = v
                    break
        mesh = _mesh_stem_from_ref(tprops.get("StaticMesh"))
        if not mesh:
            continue
        rows.append(
            {
                "var": var or tmpl_name,
                "mesh": mesh,
                "attach": str(attach).strip() if attach else "",
                "parent": str(parent).strip() if parent else "",
                "location": _vec(tprops, "RelativeLocation"),
                "rotation": _rot(tprops),
                "scale": _scale(tprops),
            }
        )
    return rows


def find_elevator_bp(pioneer_root: str) -> str:
    if not pioneer_root:
        return ""
    cand = os.path.join(pioneer_root, _ELEVATOR_BP_REL)
    if os.path.isfile(cand):
        return cand
    base = os.path.join(pioneer_root, "Core", "GameModes")
    if os.path.isdir(base):
        for dp, _dn, fn in os.walk(base):
            if "BP_SalvageExtractionPoint_Elevator.json" in fn:
                return os.path.join(dp, "BP_SalvageExtractionPoint_Elevator.json")
    return ""


def find_root_psk(folder: str) -> str:
    """Return ``SM_ExtractionElevator_01_A`` shell mesh only (no weapon fallback)."""
    folder = os.path.abspath(folder)
    for name in os.listdir(folder):
        low = name.lower()
        if not low.endswith((".psk", ".pskx")):
            continue
        stem = os.path.splitext(name)[0].lower()
        if re.search(r"extractionelevator_01_a$", stem) and "cable" not in stem:
            return os.path.join(folder, name)
    return ""


def _folder_looks_like_weapon(folder: str) -> bool:
    """True when folder has firearm SK_/SM_ meshes (not an elevator prop)."""
    folder = os.path.abspath(folder)
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    for name in names:
        low = name.lower()
        if not low.endswith((".psk", ".pskx")):
            continue
        if low.startswith("sk_") or low.startswith("sm_"):
            if "extractionelevator" not in low:
                return True
    return False


def list_folder_psks(folder: str) -> list[str]:
    return [
        os.path.join(folder, name)
        for name in sorted(os.listdir(folder))
        if name.lower().endswith((".psk", ".pskx"))
    ]


def _is_core_mesh(stem: str) -> bool:
    return any(stem.startswith(p) for p in _CORE_MESH_PREFIXES)


# Barriers: authored +X faces the shell; +180° yaw faces them outward.
_OUTWARD_YAW_FLIP_MESHES = frozenset(
    {
        "SM_Barrier_01",
        "SM_Barrier_02",
    }
)
# Engines: flip vertically (UE pitch), not yaw/roll — those left them sideways.
_OUTWARD_PITCH_FLIP_MESHES = frozenset(
    {
        "SM_ExtractionElevator_01_Engine_A",
    }
)


def _mesh_vert_count(obj) -> int:
    try:
        data = getattr(obj, "data", None)
        if data is None:
            return 0
        return int(len(data.vertices))
    except Exception:
        return 0


def _compose_child_pose(
    socket: dict | None,
    component: dict,
    *,
    mesh: str = "",
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    sl = socket["location"] if socket else (0.0, 0.0, 0.0)
    sr = socket["rotation"] if socket else (0.0, 0.0, 0.0)
    ss = socket["scale"] if socket else (1.0, 1.0, 1.0)
    cl = component.get("location") or (0.0, 0.0, 0.0)
    cr = component.get("rotation") or (0.0, 0.0, 0.0)
    cs = component.get("scale") or (1.0, 1.0, 1.0)

    # When attached to a socket, component Relative* is usually identity —
    # socket carries placement (including negative scale mirrors).
    if socket is not None:
        loc = (sl[0] + cl[0], sl[1] + cl[1], sl[2] + cl[2])
        pyr = (sr[0] + cr[0], sr[1] + cr[1], sr[2] + cr[2])
        scale = (ss[0] * cs[0], ss[1] * cs[1], ss[2] * cs[2])
    else:
        loc = cl
        pyr = cr
        scale = cs
    if mesh in _OUTWARD_YAW_FLIP_MESHES:
        pyr = (pyr[0], pyr[1] + 180.0, pyr[2])
    if mesh in _OUTWARD_PITCH_FLIP_MESHES:
        pyr = (pyr[0] + 180.0, pyr[1], pyr[2])
    euler = unreal_to_blender_xyz_radians(pyr[0], pyr[1], pyr[2])
    return loc, euler, scale


def _set_parent_local(obj, parent_obj, loc, euler, scale) -> None:
    """Parent then set local pose (never set pose before parent — Blender keeps world)."""
    from mathutils import Euler, Vector

    # Drop armature / empty parents left by the PSK importer.
    obj.parent = None
    obj.parent = parent_obj
    obj.matrix_parent_inverse.identity()
    obj.rotation_mode = "XYZ"
    obj.location = Vector(loc)
    obj.rotation_euler = Euler(euler, "XYZ")
    obj.scale = Vector(scale)


def _object_stem(obj) -> str:
    try:
        psk = str(obj.get("arc_psk_path") or "")
        if psk:
            return os.path.splitext(os.path.basename(psk))[0]
    except Exception:
        pass
    return obj.name.split(".")[0]


def _duplicate_mesh_object(obj):
    import bpy

    new = obj.copy()
    # Share mesh datablock (materials stay linked).
    for coll in obj.users_collection:
        coll.objects.link(new)
        break
    else:
        bpy.context.scene.collection.objects.link(new)
    return new


def _find_socket_owner(
    sock_name: str,
    socket_index: dict[str, dict[str, dict]],
    placed_by_mesh: dict[str, list],
    socket_usage: dict[tuple[str, str], int],
):
    """Return (parent_obj, socket_dict) for the next free owner of *sock_name*."""
    owners = [
        stem for stem, socks in socket_index.items() if sock_name in socks
    ]
    # Prefer non-root doors/pistons before root when multiple define same name (rare).
    owners.sort(key=lambda s: (0 if "Door" in s or "Piston" in s else 1, s))
    for stem in owners:
        sock = socket_index[stem][sock_name]
        pool = placed_by_mesh.get(stem) or []
        key = (stem, sock_name)
        idx = socket_usage.get(key, 0)
        if idx < len(pool):
            socket_usage[key] = idx + 1
            return pool[idx], sock
    return None, None


def apply_attachment_poses(
    *,
    root_obj,
    root_stem: str,
    templates: dict[str, object],
    socket_index: dict[str, dict[str, dict]],
    attachments: list[dict],
) -> tuple[int, int, int]:
    """Parent/place children. Returns (placed, duplicated, skipped)."""
    placed = 0
    duplicated = 0
    skipped = 0
    placed_by_mesh: dict[str, list] = {root_stem: [root_obj]}
    socket_usage: dict[tuple[str, str], int] = {}
    consumed: dict[str, int] = {root_stem: 1}

    root_socks = socket_index.get(root_stem) or {}

    def _nest_key(a: dict) -> tuple:
        """Root-socket children first; nested piston sockets after their owners exist."""
        attach = a.get("attach") or ""
        mesh_name = a.get("mesh") or ""
        if attach in root_socks:
            return (0, a.get("var") or "")
        # DoorPistons: A hangs on Door*; B hangs on A — place all A before any B.
        if "Piston" in attach or "Piston" in mesh_name:
            is_inner = mesh_name.endswith("_B") or attach.endswith("_B")
            return (2, 1 if is_inner else 0, a.get("var") or "")
        return (1, a.get("var") or "")

    ordered = sorted(attachments, key=_nest_key)

    for att in ordered:
        mesh = att.get("mesh") or ""
        if not mesh or mesh == root_stem:
            skipped += 1
            continue
        if not _is_core_mesh(mesh):
            skipped += 1
            continue
        tmpl = templates.get(mesh)
        if tmpl is None:
            skipped += 1
            continue

        idx = consumed.get(mesh, 0)
        if idx == 0:
            obj = tmpl
        else:
            obj = _duplicate_mesh_object(tmpl)
            duplicated += 1
        consumed[mesh] = idx + 1
        placed_by_mesh.setdefault(mesh, []).append(obj)

        sock_name = att.get("attach") or ""
        socket = None
        parent_obj = root_obj
        att_use = att

        if sock_name:
            if sock_name in root_socks and (
                not att.get("parent") or att.get("parent") in ("Mesh", root_stem, "")
            ):
                socket = root_socks[sock_name]
                parent_obj = root_obj
            else:
                owner, socket = _find_socket_owner(
                    sock_name, socket_index, placed_by_mesh, socket_usage
                )
                if owner is not None:
                    parent_obj = owner
                elif sock_name in root_socks:
                    socket = root_socks[sock_name]
                    parent_obj = root_obj
                else:
                    # Nested socket owner not ready yet — skip (should not happen with sort)
                    skipped += 1
                    continue
        elif mesh.endswith("_Tower_A") and "Tower" in root_socks:
            socket = root_socks["Tower"]
            parent_obj = root_obj
            att_use = {
                **att,
                "location": (0.0, 0.0, 0.0),
                "rotation": (0.0, 0.0, 0.0),
                "scale": (1.0, 1.0, 1.0),
            }

        loc, euler, scale = _compose_child_pose(socket, att_use, mesh=mesh)
        _set_parent_local(obj, parent_obj, loc, euler, scale)
        try:
            obj["arc_prop_attach_socket"] = sock_name or ""
            obj["arc_prop_attach_mesh"] = mesh
            obj["arc_prop_attach_var"] = att.get("var") or ""
        except Exception:
            pass
        placed += 1

    return placed, duplicated, skipped


def index_templates_in_scene(folder: str) -> dict[str, object]:
    """Map SM stem → one mesh object imported from *folder*.

    Prefer the densest mesh datablock for a stem — PSK import sometimes leaves a
    zero-vert placeholder named without a ``.001`` suffix that would otherwise
    win and leave door pistons floating at the origin.
    """
    import bpy

    folder_norm = os.path.abspath(folder).replace("\\", "/").lower()
    best: dict[str, object] = {}
    best_verts: dict[str, int] = {}
    for obj in bpy.data.objects:
        if getattr(obj, "type", None) != "MESH":
            continue
        psk = str(obj.get("arc_psk_path") or "").replace("\\", "/")
        if not psk:
            continue
        if os.path.dirname(psk).replace("\\", "/").lower() != folder_norm:
            # Still accept basename match if path folder matches loosely
            if folder_norm not in psk.lower():
                continue
        stem = os.path.splitext(os.path.basename(psk))[0]
        verts = _mesh_vert_count(obj)
        prev = best_verts.get(stem, -1)
        if verts > prev or (
            verts == prev
            and best.get(stem) is not None
            and obj.name < best[stem].name
        ):
            best[stem] = obj
            best_verts[stem] = verts
    return best


def assemble_extraction_elevator_folder(
    folder: str,
    *,
    pioneer_root: str = "",
    import_missing: bool = True,
) -> dict:
    """Import (optional) + assemble core elevator meshes in *folder*."""
    import bpy

    folder = os.path.abspath(folder)
    stats = {
        "folder": folder,
        "root": "",
        "imported": 0,
        "placed": 0,
        "duplicated": 0,
        "skipped": 0,
        "sockets": 0,
        "attachments": 0,
        "bp": "",
        "error": "",
    }
    root_psk = find_root_psk(folder)
    if not root_psk:
        if _folder_looks_like_weapon(folder):
            stats["error"] = (
                "This folder is a firearm/weapon — use Weapons → Import Weapon, "
                "not Assemble Prop Folder"
            )
        else:
            stats["error"] = (
                "No Extraction Elevator root mesh (SM_ExtractionElevator_01_A) in folder. "
                "Pick the elevator prop folder, or use Weapons → Import Weapon for guns."
            )
        return stats
    stats["root"] = root_psk
    root_stem = os.path.splitext(os.path.basename(root_psk))[0]
    socket_index = load_folder_socket_index(folder)
    stats["sockets"] = sum(len(v) for v in socket_index.values())

    bp = find_elevator_bp(pioneer_root)
    stats["bp"] = bp
    attachments = parse_bp_mesh_attachments(bp) if bp else []
    if not attachments:
        # Fallback: one child per root socket with a guessed folder mesh
        for sock_name in (socket_index.get(root_stem) or {}):
            mesh = _guess_mesh_for_socket(sock_name, folder)
            if mesh:
                attachments.append(
                    {
                        "var": sock_name,
                        "mesh": mesh,
                        "attach": sock_name,
                        "parent": "Mesh",
                        "location": (0.0, 0.0, 0.0),
                        "rotation": (0.0, 0.0, 0.0),
                        "scale": (1.0, 1.0, 1.0),
                    }
                )
    stats["attachments"] = len(attachments)

    if import_missing:
        scene = bpy.context.scene
        existing = {
            os.path.splitext(os.path.basename(str(o.get("arc_psk_path") or "")))[0].lower()
            for o in bpy.data.objects
            if o.type == "MESH" and o.get("arc_psk_path")
        }
        scene.arc_psk_entries.clear()
        for psk in list_folder_psks(folder):
            stem = os.path.splitext(os.path.basename(psk))[0].lower()
            if stem in existing:
                continue
            e = scene.arc_psk_entries.add()
            e.psk_path = psk
            e.display_name = os.path.basename(psk)
            stats["imported"] += 1
        if len(scene.arc_psk_entries) > 0:
            bpy.ops.arc_outfits.confirm_psk_import("EXEC_DEFAULT")

    templates = index_templates_in_scene(folder)
    root_obj = templates.get(root_stem)
    if root_obj is None:
        for obj in bpy.data.objects:
            if obj.type == "MESH" and _object_stem(obj) == root_stem:
                root_obj = obj
                templates[root_stem] = obj
                break
    if root_obj is None:
        stats["error"] = f"root mesh object not in scene: {root_stem}"
        return stats

    # Reset previous assembly so re-run is idempotent (keep one template per stem).
    folder_norm = folder.replace("\\", "/").lower()
    by_stem: dict[str, list] = {}
    for obj in list(bpy.data.objects):
        if obj.type != "MESH":
            continue
        psk = str(obj.get("arc_psk_path") or "").replace("\\", "/").lower()
        if not psk or folder_norm not in os.path.dirname(psk):
            continue
        stem = os.path.splitext(os.path.basename(psk))[0]
        # Detach prior socket parenting
        if obj.get("arc_prop_attach_mesh") or obj.parent:
            obj.parent = None
            obj.location = (0.0, 0.0, 0.0)
            obj.rotation_euler = (0.0, 0.0, 0.0)
            obj.scale = (1.0, 1.0, 1.0)
            for key in (
                "arc_prop_attach_socket",
                "arc_prop_attach_mesh",
                "arc_prop_attach_var",
                "arc_prop_assembled",
            ):
                try:
                    if key in obj:
                        del obj[key]
                except Exception:
                    pass
        by_stem.setdefault(stem, []).append(obj)
    for stem, pool in by_stem.items():
        # Keep densest mesh (skip zero-vert PSK placeholders), then stable name.
        pool.sort(
            key=lambda o: (-_mesh_vert_count(o), 0 if "." not in o.name else 1, o.name)
        )
        for extra in pool[1:]:
            bpy.data.objects.remove(extra, do_unlink=True)
    templates = index_templates_in_scene(folder)
    root_obj = templates.get(root_stem) or root_obj
    templates[root_stem] = root_obj
    root_obj.parent = None
    root_obj.location = (0.0, 0.0, 0.0)
    root_obj.rotation_euler = (0.0, 0.0, 0.0)
    root_obj.scale = (1.0, 1.0, 1.0)

    placed, duplicated, skipped = apply_attachment_poses(
        root_obj=root_obj,
        root_stem=root_stem,
        templates=templates,
        socket_index=socket_index,
        attachments=attachments,
    )
    stats["placed"] = placed
    stats["duplicated"] = duplicated
    stats["skipped"] = skipped
    try:
        root_obj["arc_prop_assembled"] = 1
        root_obj["arc_prop_bp"] = os.path.basename(bp) if bp else ""
    except Exception:
        pass
    return stats


def _guess_mesh_for_socket(sock_name: str, folder: str) -> str:
    s = sock_name.lower()
    files = {
        os.path.splitext(f)[0]
        for f in os.listdir(folder)
        if f.lower().endswith((".psk", ".pskx"))
    }
    rules = [
        ("door_a", "SM_ExtractionElevator_01_Door_A"),
        ("door_b", "SM_ExtractionElevator_01_Door_B"),
        ("doorpiston_01_a", "SM_ExtractionElevator_01_DoorPiston_01_A"),
        ("doorpiston_01_b", "SM_ExtractionElevator_01_DoorPiston_01_B"),
        ("doorpiston_02_a", "SM_ExtractionElevator_01_DoorPiston_02_A"),
        ("doorpiston_02_b", "SM_ExtractionElevator_01_DoorPiston_02_B"),
        ("engine", "SM_ExtractionElevator_01_Engine_A"),
        ("elevator", "SM_ExtractionElevator_01_Elevator_A"),
        ("tower", "SM_ExtractionElevator_01_Tower_A"),
        ("barrier_01", "SM_Barrier_01"),
        ("barrier_02", "SM_Barrier_02"),
        ("cable", "SM_ExtractionElevator_01_A_Cables"),
    ]
    for key, stem in rules:
        if key in s and stem in files:
            return stem
    return ""
