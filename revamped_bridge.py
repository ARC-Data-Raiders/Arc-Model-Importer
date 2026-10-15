"""Prop / environment materials using the in-addon surface reconstruction.

The graphs live in ``surface_rebuild`` (architecture, trim, prop-trim, packed
surfaces). This module does not import any other Blender addon.

Call this only after the mesh already has named material slots. An empty slot
list is a failure, not a silent skip.
"""
from __future__ import annotations

import json
import os

import bpy

from .surface_rebuild import get_index, infer_export_root, texture_objects

def texture_environment_object(obj, psk_path: str) -> str:
    """Rebuild slot materials. Returns a verification string, never a silent success.

    ``ok N/M ...`` means rebuilt graphs are on the mesh.
    ``FAIL ...`` means the old graph is still what the user will see.
    """
    if obj is None or getattr(obj, "type", "") != "MESH":
        return "FAIL not a mesh"
    if not psk_path or not os.path.isfile(psk_path):
        return "FAIL mesh path missing"
    slots = list(getattr(obj, "material_slots", []) or [])
    named = [s for s in slots if s.material]
    if not named:
        return "FAIL no material slots to rebuild"
    src = os.path.abspath(psk_path)
    try:
        obj["arc_source_mesh"] = src
    except Exception:
        pass
    try:
        root = str(infer_export_root(src))
    except Exception as exc:
        return f"FAIL export root: {exc}"
    if not root or not os.path.isdir(root):
        return f"FAIL export root not a directory: {root}"
    try:
        index = get_index(root)
        reports = texture_objects([obj], index, "", src, False) or []
    except Exception as exc:
        return f"FAIL surface rebuild: {exc}"
    errors = [r for r in reports if r.get("error")]
    _fix_beacon_slots(obj, src)
    check = verify_surface_materials(obj)
    families = ",".join(sorted({r.get("family") or "?" for r in reports if r.get("family")}))
    detail = (
        f"{check['rebuilt']}/{check['slots']} rebuilt"
        f" families={families or 'none'}"
        f" nodes={check['node_counts']}"
    )
    if errors:
        detail += " errors=" + "; ".join(str(e.get("error"))[:80] for e in errors[:3])
    status = "ok" if check["ok"] else "FAIL"
    line = f"{status} {detail}"
    try:
        obj["arc_surface_rebuild"] = status
        obj["arc_surface_report"] = line[:900]
    except Exception:
        pass
    print(f"Arc Raiders surface rebuild {obj.name}: {line}")
    _write_report(obj.name, line, reports)
    return line


def _loaded_images(tree) -> list:
    found = []
    if tree is None:
        return found
    for node in tree.nodes:
        if node.type == "TEX_IMAGE" and getattr(node, "image", None) is not None:
            found.append(node.image)
        elif node.type == "GROUP" and node.node_tree is not None:
            found.extend(_loaded_images(node.node_tree))
    return found


def _image_ready(img) -> bool:
    try:
        if int(img.size[0]) <= 0 or int(img.size[1]) <= 0:
            img.reload()
    except Exception:
        return False
    try:
        return int(img.size[0]) > 0 and int(img.size[1]) > 0
    except Exception:
        return False


def verify_surface_materials(obj) -> dict:
    """A slot counts only when its graph (including groups) has a loaded texture.

    Top-level node count is not enough: the old simple graph and a failed
    rebuild can both be a handful of nodes.
    """
    rebuilt = 0
    counts = []
    slots = list(getattr(obj, "material_slots", []) or [])
    for slot in slots:
        mat = slot.material
        if mat is None or not getattr(mat, "use_nodes", False):
            counts.append(0)
            continue
        images = _loaded_images(mat.node_tree)
        ready = [img for img in images if _image_ready(img)]
        counts.append(len(ready))
        name = (mat.name or "").lower()
        if "beacon" in name:
            if ready:
                rebuilt += 1
            continue
        family = ""
        try:
            family = json.loads(mat.get("arc_build_report") or "{}").get("family") or ""
        except Exception:
            family = ""
        if mat.get("arc_build_key") and family and family != "fallback" and ready:
            rebuilt += 1
    return {
        "slots": len(slots),
        "rebuilt": rebuilt,
        "node_counts": counts,
        "ok": bool(slots) and rebuilt == len(slots),
    }


def _fix_beacon_slots(obj, psk_path: str) -> None:
    from .materials.environment import setup_beacon_material, stamp_beacon_islands

    try:
        stamp_beacon_islands(obj)
    except Exception as exc:
        print(f"Arc Raiders: beacon center stamp failed: {exc}")

    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            continue
        name = (mat.name or "").lower()
        package = str(mat.get("arc_package") or "").lower()
        if "beacon" not in name and not package.endswith("mi_beacon_01"):
            continue
        mi_path = str(mat.get("arc_source_json") or "")
        if not mi_path or not os.path.isfile(mi_path):
            guess = os.path.join(os.path.dirname(psk_path), "MI_Beacon_01.json")
            mi_path = guess if os.path.isfile(guess) else ""
        try:
            setup_beacon_material(mat, mi_path, psk_path)
        except Exception as exc:
            print(f"Arc Raiders: beacon material failed: {exc}")


def _write_report(obj_name: str, line: str, reports: list) -> None:
    try:
        text = bpy.data.texts.get("Arc Surface Rebuild") or bpy.data.texts.new("Arc Surface Rebuild")
        text.write(f"{obj_name}: {line}\n")
        for report in reports:
            if report.get("error"):
                text.write(f"  slot {report.get('slot')}: {report.get('error')}\n")
    except Exception:
        pass
