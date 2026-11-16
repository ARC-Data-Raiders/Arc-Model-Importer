"""Prop / environment materials using the in-addon surface reconstruction.

The graphs live in ``materials.rebuild`` (architecture, trim, prop-trim, packed
surfaces). This module does not import any other Blender addon.

Call this only after the mesh already has named material slots. An empty slot
list is a failure, not a silent skip.
"""
from __future__ import annotations

import json
import os

import bpy

from ..Shared.materials.rebuild import get_index, infer_export_root, texture_objects
from ..Shared.materials import material_audit


def _audit_failure(obj, src, line, exc=None):
    """A whole-object rebuild failure still leaves one audit record per slot material and the object."""
    import traceback
    error = line if exc is None else line + "\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    for slot in obj.material_slots:
        if slot.material is not None:
            material_audit.audit_material(slot.material, {}, obj, error=error)
    material_audit.audit_object(obj, material_audit.mesh_meta_for(obj, src))
    print(f"Arc Raiders surface rebuild {obj.name}: {line}")
    return line


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
        return _audit_failure(obj, src, f"FAIL export root: {exc}", exc)
    if not root or not os.path.isdir(root):
        return _audit_failure(obj, src, f"FAIL export root not a directory: {root}")
    try:
        index = get_index(root)
        reports = texture_objects([obj], index, "", src, False, preserve_material=_preserve_native_material, audit=True) or []
    except Exception as exc:
        return _audit_failure(obj, src, f"FAIL surface rebuild: {exc}", exc)
    errors = [r for r in reports if r.get("error")]
    _fix_beacon_slots(obj, src)
    check = verify_surface_materials(obj)
    material_audit.audit_object(obj, material_audit.mesh_meta_for(obj, src))
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


def _mi_package(mat) -> str:
    """Package of a legacy slot's MI JSON (``.../PioneerGame/Content/...MI_X.json`` -> ``PioneerGame/.../MI_X``)."""
    path = str(mat.get("arc_mi_path") or "").replace("\\", "/")
    at = path.casefold().find("pioneergame/content/")
    return path[at:].rsplit(".", 1)[0] if at >= 0 and path.casefold().endswith(".json") else ""


REBUILD_FALLBACK_SKIP = frozenset({None, "engine_default", "character_preview"})


def rebuilds_slot(family, legacy_family, families) -> bool:
    """Rebuild a slot the rebuild library classifies into ``families``, or one the legacy chain left on the
    catch-all ``simple`` graph (which samples only basename textures and drops authored NA/mask maps)."""
    if family in families:
        return True
    return legacy_family == "simple" and family not in REBUILD_FALLBACK_SKIP


def rebuild_surface_families(obj, psk_path: str, families) -> list:
    """Rebuild only the slots materials.rebuild classifies into ``families``; every other slot keeps its graph.

    Item routes wire accessories through the authored-slot dispatch, which picks a legacy family from the
    shading model. A master with a compiled-shader surface profile (M_GlassOpaque_NX_01 on the resin pickup,
    TwoSidedFoliage) would otherwise get the foliage graph.
    """
    if obj is None or getattr(obj, "type", "") != "MESH" or not psk_path or not os.path.isfile(psk_path):
        return []
    from ..Shared.materials.rebuild.records import MaterialLibrary
    src = os.path.abspath(psk_path)
    try:
        index = get_index(str(infer_export_root(src)))
    except Exception as exc:
        print(f"Arc Raiders surface families {obj.name}: export root unavailable: {exc}")
        return []
    lib = MaterialLibrary(index)

    def keep_other_families(obj, slot_index, mat, index):
        package = mat.get("arc_package") or _mi_package(mat)
        try:
            family = lib.load(package)["family"] if package else None
        except Exception:
            family = None
        if rebuilds_slot(family, str(mat.get("arc_mi_family") or "") or None, families):
            mat["arc_package"] = package
            return None
        # The kept slot keeps its routed family, so the audit stamp below never renames it.
        return {"object": obj.name, "slot": slot_index, "material": mat.name,
                "family": str(mat.get("arc_mi_family") or "") or None,
                "preserved_native_graph": True, "missing_textures": [], "warnings": []}

    reports = texture_objects([obj], index, "", src, False, preserve_material=keep_other_families, audit=True) or []
    rebuilt = [r for r in reports if not r.get("preserved_native_graph") or r.get("error")]
    for report in rebuilt:
        print(f"Arc Raiders surface families {obj.name}[{report.get('slot')}]: "
              f"{report.get('family') or 'error'} {str(report.get('error') or '')[:120]}")
    return rebuilt


def _has_surface_output(mat) -> bool:
    tree = getattr(mat, "node_tree", None)
    return bool(tree and any(n.type == "OUTPUT_MATERIAL" and n.inputs["Surface"].is_linked
                             for n in tree.nodes))


def _preserve_native_material(obj, slot_index, mat, index):
    """Keep only root profiles with an explicit, supported implementation marker.

    A texture-bearing graph alone is not evidence of the correct shader family.
    The packed profile additionally has to resolve to a recognized native master.
    Neither graph preservation nor a successful build establishes game parity.
    """
    if mat is None:
        return None
    from ..Shared.materials.native.native_families import MASTERS, is_user_override, native_cache_is_stale
    if is_user_override(mat):
        report = {"object": obj.name, "slot": slot_index, "material": mat.name,
                  "family": "user_override", "preserved_native_graph": True,
                  "texture_required": False, "engine_parity_verified": False,
                  "missing_textures": [], "warnings": ["Explicit user material override preserved"]}
        mat["arc_native_surface_report"] = json.dumps(report)
        return report
    if not getattr(mat, "use_nodes", False) or not _has_surface_output(mat):
        return None
    native_family = str(mat.get("dr_native_family") or "")
    try:
        native = json.loads(mat.get("dr_native_report") or "{}")
    except (ValueError, TypeError):
        native = {}
    if (native_family and MASTERS.get(native.get("master")) == native_family
            and mat.get("dr_native_active_signature")):
        source = str(mat.get("arc_mi_path") or mat.get("arc_source_json") or "")
        stale = native_cache_is_stale(mat, source, obj=obj)
        warnings = list(native.get("diagnostics") or [])
        diagnostic = str(mat.get("dr_native_route_diagnostic") or "")
        if diagnostic:
            warnings.append(diagnostic)
        if stale:
            warnings.append("Dedicated native profile needs refresh; retained instead of generic reconstruction")
        report = {"object": obj.name, "slot": slot_index, "material": mat.name,
                  "package": str(mat.get("arc_package") or ""), "master": native["master"],
                  "family": "native_" + native_family, "subtype": native.get("subtype", ""),
                  "preserved_native_graph": True, "implementation": "original-native-runtime",
                  "native_cache_current": not stale and bool(mat.get("dr_native_signature_complete")),
                  "texture_required": bool(native.get("images")), "engine_parity_verified": False,
                  "missing_textures": [], "warnings": list(dict.fromkeys(warnings))}
        mat["arc_native_surface_report"] = json.dumps(report)
        return report
    family = ""
    master = ""
    if str(mat.get("arc_env_surface_setup") or "") == "packed-prop-v2":
        from ..Shared.materials.common import _parse_flat_mi_json
        from ..Shared.materials.environment import _is_packed_prop_preset, env_props
        source = str(mat.get("arc_mi_path") or mat.get("arc_source_json") or "")
        if not source or not os.path.isfile(source):
            return None
        mi = env_props.merge_preset_defaults(_parse_flat_mi_json(source), source)
        if not _is_packed_prop_preset(mi):
            return None
        family = "native_packed_prop"
        master = str(mi.get("parent") or mi.get("preset_key") or "")
    emission = str(mat.get("arc_emissive_setup") or "")
    if emission in {"authored-light-v1", "native-lantern-static-v1"}:
        family = "native_light" if emission == "authored-light-v1" else "native_lantern"
    if not family:
        return None
    report = {
        "object": obj.name, "slot": slot_index, "material": mat.name,
        "package": str(mat.get("arc_package") or mat.get("arc_mi_path") or ""),
        "master": master, "family": family, "preserved_native_graph": True,
        "implementation": emission or str(mat.get("arc_env_surface_setup") or ""),
        "texture_required": family != "native_light", "engine_parity_verified": False,
        "missing_textures": [], "warnings": [],
    }
    mat["arc_native_surface_report"] = json.dumps(report)
    return report


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
    """Require loaded maps, or an explicitly preserved texture-free light profile.

    Top-level node count is not enough: the old simple graph and a failed
    rebuild can both be a handful of nodes.
    """
    rebuilt = 0
    counts = []
    slots = list(getattr(obj, "material_slots", []) or [])
    for slot in slots:
        mat = slot.material
        if mat is not None:
            from ..Shared.materials.native.native_families import is_user_override
            if is_user_override(mat):
                counts.append(0)
                rebuilt += 1
                continue
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
        native = {}
        try:
            native = json.loads(mat.get("arc_native_surface_report") or "{}")
        except (TypeError, ValueError):
            pass
        if (native.get("preserved_native_graph") and _has_surface_output(mat)
                and (ready or not native.get("texture_required", True))):
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
    from ..Shared.materials.environment import setup_beacon_material, stamp_beacon_islands

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
