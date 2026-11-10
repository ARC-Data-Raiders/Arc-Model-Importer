"""
Processing logic for PSK imports and model texturing
"""

import os
import re
import json
import time
import functools
import bpy
import bpy_extras
import mathutils
from bpy_extras.io_utils import ExportHelper
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator

from . import utils
from . import textures
from . import materials
from . import importing
from ..Animations import rig
from .. import fmdex
from ..Maps import map_placement
from ..Outfit_Accessories import palette_calibration

log = utils.get_logger()


def _entry_material_bindings(entry):
    """Restore audit carried separately across Blender ID-property JSON storage."""
    payload = entry.get("arc_outfit_material_bindings", "{}")
    bindings = json.loads(payload) if isinstance(payload, str) else dict(payload)
    audit = entry.get("arc_unused_outfit_material_bindings")
    if audit is None:
        return bindings
    from ..Outfit_Accessories.native_material_slots import ResolvedMaterialBindings
    unused = json.loads(audit) if isinstance(audit, str) else list(audit)
    return ResolvedMaterialBindings(bindings, unused_native_bindings=unused)


def _picked_folder(op) -> str:
    """Directory from a file-browser operator without walking up one extra level."""
    filepath = bpy.path.abspath(getattr(op, "filepath", "") or "")
    directory = bpy.path.abspath(getattr(op, "directory", "") or "")
    return utils.resolved_picked_dir(filepath, directory)


def _colorway_name_from_skin_path(path: str) -> str:
    """Return the Skins/<Colorway> folder name when path points at a colorway MI JSON."""
    if not path or path == "NONE":
        return ""
    abs_path = bpy.path.abspath(str(path))
    parent = os.path.basename(os.path.dirname(abs_path))
    skins_parent = os.path.basename(os.path.dirname(os.path.dirname(abs_path)))
    if skins_parent.casefold() == "skins" and parent:
        return parent
    return ""


def _import_uemodel_mesh_only(psk_path, entry, *, body_variant, hair_mi, json_path,
                              model_type, asset_domain, batch_skin_json) -> tuple:
    """UEModel mesh import with materials deferred.

    ``appearance.import_appearance_mesh`` applies materials inline for .uemodel, so the
    batch mesh step calls the UEModel reader directly. Falls back to the full import when
    the reader returns nothing (sibling PSK path, which applies materials as before).
    """
    from ..Outfit_Accessories import appearance

    objs = appearance._import_uemodel_outfit(psk_path)
    if not objs:
        return appearance.import_appearance_mesh(
            psk_path,
            body_variant=body_variant if body_variant else "NONE",
            hair_mi=hair_mi if hair_mi else "NONE",
            skin_json=json_path, manual_skins_folder=entry.manual_skins_folder,
            skin_choice=batch_skin_json or entry.skin_choice,
            material_bindings=_entry_material_bindings(entry),
        )
    for obj in [o for o in objs if o.type == "MESH"]:
        try:
            obj["arc_psk_path"] = psk_path
            obj["arc_model_type"] = model_type
            if asset_domain:
                obj["arc_asset_domain"] = asset_domain
            obj["arc_materials_pending"] = 1
            obj["arc_body_variant"] = body_variant if body_variant else "NONE"
            obj["arc_hair_mi"] = hair_mi if hair_mi else "NONE"
            if json_path:
                obj["arc_skin_json"] = os.path.abspath(json_path)
            if batch_skin_json:
                obj["arc_skin_choice"] = os.path.abspath(batch_skin_json)
        except Exception:
            pass
    return True, f"Imported ({model_type}, materials deferred): {os.path.basename(psk_path)}", objs


def _deferred_psk_materials(obj, psk_path, kwargs):
    """Material step of a mesh that ``process_entry`` imported with ``defer``."""
    status = apply_materials_to_object(obj, psk_path, **kwargs)
    try:
        obj["arc_material_status"] = str(status or "")[:900]
        obj["arc_materials_pending"] = 0
    except Exception:
        pass


def _deferred_appearance_materials(obj, path, kwargs, post):
    from ..Outfit_Accessories import appearance
    appearance.finish_deferred_materials(obj, path, **kwargs)
    if post is not None:
        post(obj)


def process_entry(entry, *, psk=None, context=None, skip_materials=False, defer=None) -> tuple:
    """Process a single PSK entry: import and set up materials.

    ``psk`` — optional pre-parsed ``psk_psa_py`` object (parse pipelining).
    ``skip_materials`` — import mesh/armature only (Phase C batch defers materials).
    ``defer`` — a list: import the mesh only and queue ``(obj, build)`` there; calling
    ``build()`` later runs the same material calls as the inline path.
    """
    with utils.timed(f"process_entry:{os.path.basename(getattr(entry, 'psk_path', '') or '')}"):
        psk_path = bpy.path.abspath(entry.psk_path)
        batch_skin_json = str(getattr(entry, "batch_skin_json", "") or "")
        json_path = bpy.path.abspath(batch_skin_json) if batch_skin_json else (
            "" if entry.skin_choice == 'NONE' else bpy.path.abspath(entry.skin_choice)
        )
        if not json_path:
            json_path = textures.get_base_skin_json(psk_path, entry.manual_skins_folder)
        if json_path:
            resolver = textures.resolve_part_mi_json if batch_skin_json else textures.resolve_clothing_mi_json
            json_path = resolver(json_path) or json_path
        body_variant = entry.body_choice if hasattr(entry, 'body_choice') else 'NONE'

        if not os.path.isfile(psk_path):
            return False, f"PSK not found: {psk_path}", []

        if psk_path.casefold().endswith(".uemodel"):
            from ..Outfit_Accessories import appearance

            hair_mi = entry.hair_mi if hasattr(entry, "hair_mi") else "NONE"
            if skip_materials:
                try:
                    from ..Outfit_Accessories import asset_domain as _ad_u
                    asset_domain_u = _ad_u.classify_asset_domain(psk_path)
                except Exception:
                    asset_domain_u = ""
                return _import_uemodel_mesh_only(
                    psk_path, entry, body_variant=body_variant, hair_mi=hair_mi,
                    json_path=json_path, model_type=textures.detect_model_type(psk_path),
                    asset_domain=asset_domain_u, batch_skin_json=batch_skin_json,
                )
            queued = [] if defer is not None else None
            result = appearance.import_appearance_mesh(
                psk_path,
                body_variant=body_variant if body_variant else "NONE",
                hair_mi=hair_mi if hair_mi else "NONE",
                skin_json=json_path, manual_skins_folder=entry.manual_skins_folder,
                skin_choice=batch_skin_json or entry.skin_choice,
                material_bindings=_entry_material_bindings(entry),
                defer_materials=queued,
            )
            for q_obj, q_path, q_kwargs, q_post in queued or ():
                defer.append((q_obj, functools.partial(
                    _deferred_appearance_materials, q_obj, q_path, q_kwargs, q_post)))
            return result

        model_type = textures.detect_model_type(psk_path)
        try:
            from ..Outfit_Accessories import asset_domain as _ad
            asset_domain = _ad.classify_asset_domain(psk_path)
        except Exception:
            asset_domain = ""

        try:
            with utils.timed(f"import_psk:{os.path.basename(psk_path)}"):
                new_objects = importing.import_psk(psk_path, psk=psk, context=context)
        except RuntimeError as e:
            return False, str(e), []

        mesh_objects = [o for o in new_objects if o.type == "MESH"]
        hair_mi = entry.hair_mi if hasattr(entry, 'hair_mi') else 'NONE'
        for obj in mesh_objects:
            try:
                obj["arc_psk_path"] = psk_path
                obj["arc_model_type"] = model_type
                if asset_domain:
                    obj["arc_asset_domain"] = asset_domain
                obj["arc_materials_pending"] = 1 if (skip_materials or defer is not None) else 0
                obj["arc_body_variant"] = body_variant if body_variant else "NONE"
                obj["arc_hair_mi"] = hair_mi if hair_mi else "NONE"
                if json_path:
                    obj["arc_skin_json"] = os.path.abspath(json_path)
                if batch_skin_json:
                    obj["arc_skin_choice"] = os.path.abspath(batch_skin_json)
            except Exception:
                pass
            if skip_materials:
                continue
            mat_kwargs = dict(
                body_variant=body_variant,
                hair_mi=hair_mi,
                skin_json=json_path,
                manual_skins_folder=entry.manual_skins_folder,
                skin_choice=batch_skin_json or entry.skin_choice,
                material_bindings=_entry_material_bindings(entry),
            )
            if defer is not None:
                defer.append((obj, functools.partial(
                    _deferred_psk_materials, obj, psk_path, mat_kwargs)))
                continue
            mat_status = apply_materials_to_object(obj, psk_path, **mat_kwargs)
            try:
                obj["arc_material_status"] = str(mat_status or "")[:900]
            except Exception:
                pass
        kind = model_type
        if skip_materials:
            kind = f"{model_type}, materials deferred"
        elif model_type in ("weapon", "enemy"):
            kind = "enemy" if (
                model_type == "enemy" or textures.is_enemy(psk_path)
            ) else "weapon"
        elif model_type == "clothing":
            mi_data = textures.parse_clothing_mi(json_path) if json_path else {}
            kind = "with skin colours" if mi_data.get("colours") else "default skin"
        elif model_type == "body":
            kind = f"body, {body_variant if body_variant != 'NONE' else 'no skin'}"
        status_bits = [
            str(o.get("arc_material_status") or "")
            for o in mesh_objects
            if o.get("arc_material_status")
        ]
        status = (" | " + " | ".join(status_bits)) if status_bits else ""
        return True, f"Imported ({kind}): {os.path.basename(psk_path)}{status}", new_objects


def _process_entries_pipelined(entries, context=None, *, skip_materials=False) -> list:
    """Run ``process_entry`` for each entry; overlap parse of the next PSK off-main.

    Returns a list of ``(ok, msg, new_objects)`` tuples.
    """
    return list(_iter_process_entries_pipelined(
        entries, context, skip_materials=skip_materials
    ))


def _iter_process_entries_pipelined(entries, context=None, *, skip_materials=False, defer=None):
    """Generator form of ``_process_entries_pipelined``: yields one result per entry."""
    entries = list(entries)
    if not entries:
        return
    use_prefetch = (
        utils.io_prefetch_enabled(context)
        and importing.psk_reader_available()
        and len(entries) > 1
    )
    fut = None
    executor = None
    try:
        if use_prefetch:
            from concurrent.futures import ThreadPoolExecutor
            executor = ThreadPoolExecutor(max_workers=1)
        for i, entry in enumerate(entries):
            parsed = None
            if fut is not None:
                try:
                    parsed = fut.result()
                except Exception as exc:
                    log.debug("PSK parse prefetch failed: %s", exc)
                    parsed = None
                fut = None
            if executor is not None and i + 1 < len(entries):
                next_path = bpy.path.abspath(entries[i + 1].psk_path)
                fut = executor.submit(importing.read_psk_safe, next_path)
            yield process_entry(
                entry, psk=parsed, context=context, skip_materials=skip_materials,
                defer=defer,
            )
    finally:
        if fut is not None:
            try:
                fut.cancel()
            except Exception:
                pass
        if executor is not None:
            executor.shutdown(wait=False)


def import_psk_with_materials(
    psk_path: str,
    *,
    body_variant: str = "NONE",
    hair_mi: str = "NONE",
    skin_json: str = "",
    manual_skins_folder: str = "",
    skin_choice: str = "NONE",
    import_slot=None,
) -> tuple:
    """Import a PSK and apply default Arc materials (no outfit dialog).

    Returns (ok, message, new_objects) — same shape as process_entry.
    """
    psk_path = bpy.path.abspath(psk_path)
    if not os.path.isfile(psk_path):
        return False, f"PSK not found: {psk_path}", []

    if not skin_json:
        skin_json = textures.get_base_skin_json(psk_path, manual_skins_folder) or ""

    model_type = textures.detect_model_type(psk_path)
    try:
        from ..Outfit_Accessories import asset_domain as _ad
        asset_domain = _ad.classify_asset_domain(psk_path)
    except Exception:
        asset_domain = ""
    try:
        new_objects = importing.import_psk(psk_path)
    except RuntimeError as e:
        return False, str(e), []

    if import_slot is not None:
        from ..Outfit_Accessories import import_layout
        import_layout.stamp_objects(new_objects, slot=import_slot)
    mesh_objects = [o for o in new_objects if o.type == "MESH"]
    for obj in mesh_objects:
        try:
            obj["arc_psk_path"] = psk_path
            obj["arc_model_type"] = model_type
            if asset_domain:
                obj["arc_asset_domain"] = asset_domain
            obj["arc_materials_pending"] = 0
            obj["arc_bridge_source"] = "fmodel"
            obj["arc_body_variant"] = body_variant if body_variant else "NONE"
            obj["arc_hair_mi"] = hair_mi if hair_mi else "NONE"
        except Exception:
            pass
        mat_status = apply_materials_to_object(
            obj, psk_path,
            body_variant=body_variant,
            hair_mi=hair_mi,
            skin_json=skin_json,
            manual_skins_folder=manual_skins_folder,
            skin_choice=skin_choice,
        )
        try:
            obj["arc_material_status"] = str(mat_status or "")[:900]
        except Exception:
            pass
    status_bits = [
        str(o.get("arc_material_status") or "")
        for o in mesh_objects
        if o.get("arc_material_status")
    ]
    status = (" | " + " | ".join(status_bits)) if status_bits else ""
    return True, f"Imported ({model_type}): {os.path.basename(psk_path)}{status}", new_objects


def assign_cached_materials(obj, materials: list) -> bool:
    """Assign previously built Material datablocks to obj (reuse, no rebuild)."""
    if not obj or obj.type != "MESH" or not materials:
        return False
    try:
        if obj.get("arc_shared_backpack_geometry"):
            from ..Outfit_Accessories import backpack_instances
            if backpack_instances.assign_instance_materials(obj, materials):
                return True
        from ..Maps import staged_import as _staged_slots
        _staged_slots.replace_slot_materials(obj.data, [m for m in materials if m is not None])
        return True
    except Exception:
        return False


def snapshot_object_materials(obj) -> list:
    """Return Material datablocks currently on a mesh (for cache reuse)."""
    if not obj or obj.type != "MESH":
        return []
    return [slot.material for slot in obj.material_slots]


def _recover_saved_material_lookup(obj, source: str, *, stamp_mesh=True) -> str:
    """Recover exact cached package folders without changing mesh/PSK semantics."""
    if not source:
        return source
    from . import pak_extract
    registered = list(reversed(getattr(utils, "_pak_cache_roots", [])))
    roots = list(registered)
    try:
        if pak_extract.configured_dir(bpy.context.scene):
            roots.insert(0, os.path.join(pak_extract._addon_dir(), "ArcPak", "cache"))
    except Exception:
        pass
    normalized = str(source).replace("\\", "/").casefold()
    native = "/arcpak/cache/" in normalized
    if hasattr(obj, "get"):
        native = native or bool(obj.get("arc_pak_package") or obj.get("arc_pak_source"))
        native = native or any("/arcpak/cache/" in str(obj.get(key) or "").replace("\\", "/").casefold()
                               for key in ("arc_psk_path", "arc_source_mesh", "arc_original_psk_path", "arc_original_source_mesh"))
    result = pak_extract.recover_saved_material_source(source, roots, native_pak=native)
    if not result:
        package = pak_extract.saved_material_package(source)
        if native and package and hasattr(obj, "get"):
            if stamp_mesh:
                obj["arc_material_source_unresolved"] = package
            elif not os.path.isfile(source):
                obj["arc_material_mi_unresolved"] = package
        return source
    if stamp_mesh and hasattr(obj, "get") and obj.get("arc_material_source_unresolved"):
        del obj["arc_material_source_unresolved"]
    if not stamp_mesh and hasattr(obj, "get") and obj.get("arc_material_mi_unresolved"):
        del obj["arc_material_mi_unresolved"]
    path = result["path"]
    if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(source)):
        return source
    # Keep the first imported source as provenance. Format-changing native
    # recovery is lookup-only: downstream pose/unit consumers retain PSK stamps.
    if stamp_mesh and hasattr(obj, "get"):
        try:
            if not obj.get("arc_original_source_mesh"):
                obj["arc_original_source_mesh"] = str(obj.get("arc_source_mesh") or source)
            if not obj.get("arc_original_psk_path"):
                obj["arc_original_psk_path"] = str(obj.get("arc_psk_path") or source)
            obj["arc_material_source_path"] = path
            obj["arc_material_source_package"] = result["package"]
            if result["same_format"]:
                for name in ("arc_psk_path", "arc_source_mesh"):
                    stamped = str(obj.get(name) or "")
                    if stamped and os.path.normcase(os.path.abspath(stamped)) == os.path.normcase(os.path.abspath(source)):
                        obj[name] = path
        except Exception:
            log.exception("Could not persist exact material source recovery for %s", obj.name)
    log.info("Material source exact package recovery: %s -> %s", source, path)
    return path


def _folder_mi_contradicts_sections(psk_path: str, folder_mi: str) -> bool:
    """True when the section table names other MIs than a guessed folder MI.

    The part-folder route writes one MI into the first slot. On a multi-material
    mesh that overwrote another shared MI datablock (SM_OutpostModules_01_B slot 0
    MI_Outpost_CorrugatedMetal_01_A got the cloth awning graph) and left every
    other slot empty; per-slot resolution reads the exact authored references.
    """
    if not folder_mi:
        return False
    authored = {
        path.rsplit("/", 1)[-1].split(".")[0].casefold()
        for path in materials._uemodel_section_materials(psk_path) if path
    }
    return bool(authored) and authored != {os.path.basename(folder_mi).split(".")[0].casefold()}


def _authored_layered_part_mi(psk_path: str) -> str:
    """The one layered character skin MI (``<part>/Skins/<skin>/``) a mesh's sections name.

    SM_Celeste_ToggleRadio_01 lives under Items/Animation, but its only section
    references MI_Recruit_ToggleRadio_Metal: an 8-zone M_Character_Layered skin
    whose ColorMask/OCM sit in the Recruit/ToggleRadio part folder.
    """
    authored = {path for path in materials._uemodel_section_materials(psk_path)
                if path and not path.casefold().startswith("/engine/")}
    if len(authored) != 1:
        return ""
    mi_path = textures.find_asset_from_object_path(authored.pop(), ".json")
    if not mi_path or os.path.basename(os.path.dirname(os.path.dirname(mi_path))).casefold() != "skins":
        return ""
    return mi_path if materials.dispatch._mi_uses_layered_character_profile(mi_path) else ""


def _isolate_material_controls(obj):
    copies = {}
    for slot in getattr(obj, "material_slots", ()):
        material = slot.material
        if material is None or not material.use_nodes:
            continue
        if not any(node.get("dr_preview_parameter") for node in material.node_tree.nodes):
            continue
        if material.get("dr_control_owner") == obj.name_full:
            continue
        identity = material.as_pointer()
        if identity not in copies:
            copy = material.copy()
            copy["dr_control_owner"] = obj.name_full
            copies[identity] = copy
        slot.link = "OBJECT"
        slot.material = copies[identity]


def apply_materials_to_object(
    obj,
    psk_path: str,
    *,
    body_variant: str = "NONE",
    hair_mi: str = "NONE",
    skin_json: str = "",
    manual_skins_folder: str = "",
    skin_choice: str = "NONE",
    mi_data: dict = None,
    main_pngs: list = None,
    base_pngs: list = None,
    material_bindings=None,
) -> str:
    """Apply Arc materials to an existing mesh without importing. Returns a short status."""
    if material_bindings is not None:
        unused_native = getattr(material_bindings, "unused_native_bindings", None)
        if unused_native is not None:
            obj["arc_unused_outfit_material_bindings"] = json.dumps(unused_native, sort_keys=True)
        bindings = json.loads(material_bindings) if isinstance(material_bindings, str) else dict(material_bindings)
        obj["arc_outfit_material_bindings"] = json.dumps(bindings, sort_keys=True)
    psk_path = _recover_saved_material_lookup(obj, psk_path)
    if hasattr(obj, "get") and obj.get("arc_material_source_unresolved"):
        log.warning("Material update skipped: missing exact game package %s", obj.get("arc_material_source_unresolved"))
        return "unresolved"
    if hasattr(obj, "get") and obj.get("arc_material_mi_unresolved"):
        del obj["arc_material_mi_unresolved"]
    original_skin = skin_json
    if skin_json:
        skin_json = _recover_saved_material_lookup(obj, skin_json, stamp_mesh=False)
        if hasattr(obj, "get") and obj.get("arc_material_mi_unresolved"):
            log.warning("Material update skipped: missing exact selected MI %s", obj.get("arc_material_mi_unresolved"))
            return "unresolved"
        if skin_json != original_skin:
            if manual_skins_folder and os.path.normcase(os.path.abspath(manual_skins_folder)) == os.path.normcase(os.path.dirname(os.path.abspath(original_skin))):
                manual_skins_folder = os.path.dirname(skin_json)
            if skin_choice == original_skin:
                skin_choice = skin_json
    if skin_choice and skin_choice != "NONE":
        skin_choice = _recover_saved_material_lookup(obj, skin_choice, stamp_mesh=False)
        if hasattr(obj, "get") and obj.get("arc_material_mi_unresolved"):
            log.warning("Material update skipped: missing exact selected MI %s", obj.get("arc_material_mi_unresolved"))
            return "unresolved"
    if hair_mi and hair_mi != "NONE":
        hair_mi = _recover_saved_material_lookup(obj, hair_mi, stamp_mesh=False)
        if hasattr(obj, "get") and obj.get("arc_material_mi_unresolved"):
            log.warning("Material update skipped: missing exact selected MI %s", obj.get("arc_material_mi_unresolved"))
            return "unresolved"
    label = f"apply_materials:{getattr(obj, 'name', '?')}"
    with utils.timed(label):
        status = _apply_materials_to_object_impl(
            obj, psk_path,
            body_variant=body_variant,
            hair_mi=hair_mi,
            skin_json=skin_json,
            manual_skins_folder=manual_skins_folder,
            skin_choice=skin_choice,
            mi_data=mi_data,
            main_pngs=main_pngs,
            base_pngs=base_pngs,
        )
        _isolate_material_controls(obj)
        return status


def _apply_materials_to_object_impl(
    obj,
    psk_path: str,
    *,
    body_variant: str = "NONE",
    hair_mi: str = "NONE",
    skin_json: str = "",
    manual_skins_folder: str = "",
    skin_choice: str = "NONE",
    mi_data: dict = None,
    main_pngs: list = None,
    base_pngs: list = None,
) -> str:
    if not obj or obj.type != "MESH":
        return "skipped (not a mesh)"

    folder = os.path.dirname(psk_path) if psk_path else ""
    normalized_source = (psk_path or "").replace("\\", "/")
    cache_marker = "/arcpak/cache/"
    if cache_marker in normalized_source.casefold():
        marker_index = normalized_source.casefold().index(cache_marker)
        utils.set_pak_cache_root(normalized_source[:marker_index + len(cache_marker) - 1])
    # Hard folder-path domains (Outfit / Environment / Gun / Arc) win over stamps.
    from ..Outfit_Accessories import asset_domain as _ad
    domain = _ad.classify_asset_domain(psk_path or "")
    stamped = str(obj.get("arc_model_type", "") or "").strip().lower()

    # Domain hard gates — never fall through to another pipeline.
    if domain == _ad.DOMAIN_ENEMY:
        try:
            obj["arc_model_type"] = "enemy"
            obj["arc_asset_domain"] = domain
        except Exception:
            pass
        materials.setup_enemy_material(obj, psk_path)
        return "enemy"
    if domain == _ad.DOMAIN_WEAPON:
        try:
            obj["arc_model_type"] = "weapon"
            obj["arc_asset_domain"] = domain
        except Exception:
            pass
        weapon_skin = ""
        if skin_choice and skin_choice != "NONE":
            weapon_skin = bpy.path.abspath(skin_choice)
        if (not weapon_skin or not os.path.isfile(weapon_skin)) and skin_json:
            weapon_skin = skin_json if os.path.isfile(skin_json) else ""
        if weapon_skin and os.path.isfile(weapon_skin):
            try:
                obj["arc_skin_json"] = os.path.abspath(weapon_skin)
                obj["arc_skin_choice"] = os.path.abspath(weapon_skin)
            except Exception:
                pass
        materials.setup_weapon_material(obj, psk_path, skin_json=weapon_skin or "")
        return "weapon"
    if domain == _ad.DOMAIN_ENVIRONMENT:
        try:
            obj["arc_model_type"] = "map"
            obj["arc_asset_domain"] = domain
        except Exception:
            pass
        # Named MI slots first. The surface rebuild replaces those graphs.
        # Calling the rebuild first used to no-op on empty slots and leave
        # the old simple Principled materials in place.
        materials.setup_map_material(obj, psk_path)
        from ..Outfit_Accessories import revamped_bridge

        report = revamped_bridge.texture_environment_object(obj, psk_path)
        return f"prop {report}"

    # Outfit / unknown: refine subtype (stamp kept when still in-domain).
    outfit_types = ("clothing", "visor", "body", "hair", "face", "fur", "misc")
    if domain == _ad.DOMAIN_OUTFIT and stamped in outfit_types:
        model_type = stamped
    elif stamped and not _ad.domains_conflict(stamped, psk_path or ""):
        model_type = stamped
    else:
        model_type = textures.detect_model_type(psk_path) if psk_path else "unknown"
    part_mi = _authored_layered_part_mi(psk_path) if domain == _ad.DOMAIN_UNKNOWN and psk_path else ""
    if part_mi and (not skin_json or os.path.normcase(os.path.abspath(skin_json)) == os.path.normcase(os.path.abspath(part_mi))):
        # Build it as that outfit part: textures come from the part folder, not the mesh's.
        model_type, skin_json = "clothing", part_mi
        manual_skins_folder = os.path.dirname(part_mi)
        folder = os.path.dirname(os.path.dirname(manual_skins_folder))
        mi_data = main_pngs = base_pngs = None
    try:
        if model_type:
            obj["arc_model_type"] = model_type
        if domain != _ad.DOMAIN_UNKNOWN:
            obj["arc_asset_domain"] = domain
    except Exception:
        pass

    if model_type == "face":
        materials.setup_face_material(obj, psk_path)
        return "face"

    if model_type == "body":
        try:
            if body_variant and body_variant != "NONE":
                obj["arc_body_variant"] = body_variant
        except Exception:
            pass
        from ..Outfit_Accessories import character_fit
        character_fit.apply_body_gender(obj, body_variant)
        materials.setup_body_material(obj, psk_path, body_variant)
        if "arc_import_slot" in obj:
            character_fit.sync_scene(bpy.context.scene)
        return "body"

    if model_type == "hair":
        # Outfit colourways (FoxHatCards ArcticFox/…) stamp skin_json / skin_choice;
        # Characters/Hairs still use the hair_mi dropdown.
        hair_json = hair_mi if hair_mi and hair_mi != "NONE" else ""
        if not hair_json and skin_json:
            hair_json = skin_json
        if not hair_json:
            mis = textures.scan_hair_mis(psk_path)
            hair_json = mis[0][1] if mis else ""
        colour_json = ""
        try:
            from ..Outfit_Accessories import appearance as _appearance
            cid = getattr(bpy.context.scene, "arc_appearance_hair_color", "NONE") or "NONE"
            colour_json = _appearance.resolve_hair_color_path(cid) if cid != "NONE" else ""
        except Exception:
            colour_json = ""
        try:
            if hair_json:
                obj["arc_hair_mi"] = hair_json
                obj["arc_skin_json"] = os.path.abspath(hair_json)
            if colour_json and colour_json != "NONE" and os.path.isfile(colour_json):
                obj["arc_hair_color_mi"] = os.path.abspath(colour_json)
            if skin_choice and skin_choice != "NONE":
                obj["arc_skin_choice"] = skin_choice
        except Exception:
            pass
        materials.setup_hair_material(obj, hair_json, colour_json=colour_json)
        return "hair"

    # A visor is a clothing shell plus a glass slot, so it takes the same ArcTexturer path and
    # only differs in which slots get replaced afterwards.
    if model_type in ("clothing", "visor"):
        json_path = skin_json
        if not json_path:
            json_path = textures.get_base_skin_json(psk_path, manual_skins_folder)
        if json_path:
            repaired = textures.resolve_part_mi_json(json_path)
            if repaired:
                json_path = repaired
            else:
                log.warning(
                    "clothing MI rejected (corrupt/wrong identity): %s", json_path
                )
                # Prefer another valid colourway / mesh-folder MI over a cross-wired MIC.
                json_path = textures.get_base_skin_json(psk_path, manual_skins_folder) or ""
        previous_json = json_path
        json_path = textures.prefer_authored_part_material(psk_path, json_path)
        if json_path != previous_json:
            # Update Materials on older imports inferred these hints from the
            # preview MI. They must not keep texture-array scans in that folder.
            if manual_skins_folder and os.path.normcase(os.path.abspath(manual_skins_folder)) == os.path.normcase(os.path.dirname(os.path.abspath(previous_json))):
                manual_skins_folder = ""
            if skin_choice == previous_json:
                skin_choice = json_path
            mi_data = None
            base_pngs = None
        if not json_path and materials._uemodel_section_materials(psk_path):
            # No colourway, visual-part or folder MI: a pose/preview mesh whose
            # sections reference hero MIs directly (SM_Avatar_001_Pose ->
            # Heroes/Celeste/.../Materials). A garment graph with no MI behind it
            # renders flat white; build each authored slot MI instead.
            fixed = materials.fix_object_materials_from_mi_slots(
                obj, folder, context=materials.context_from_model_type(model_type, psk_path),
            )
            if fixed:
                obj["arc_materials_pending"] = 0
                return f"mi-slots ({fixed})"
        # Only persist real values. Empty/default fallbacks must not erase a prior colorway stamp.
        try:
            if json_path:
                obj["arc_skin_json"] = os.path.abspath(json_path)
                obj["arc_manual_skins_folder"] = (
                    os.path.abspath(manual_skins_folder)
                    if manual_skins_folder
                    else os.path.dirname(os.path.abspath(json_path))
                )
            if skin_choice and skin_choice != "NONE":
                obj["arc_skin_choice"] = skin_choice
            elif json_path:
                obj["arc_skin_choice"] = os.path.abspath(json_path)
        except Exception:
            pass
        decal_folder = utils.get_decal_folder()
        with utils.timed("mat.parse_mi"):
            if mi_data is None:
                empty_mi = {
                    "colours": {}, "ta_ids": {}, "zone_scalars": {},
                    "mi_params": {"scalars": [], "vectors": []}, "decals": [],
                }
                if json_path:
                    try:
                        mi_data = textures.parse_clothing_mi(json_path) or empty_mi
                    except Exception:
                        mi_data = empty_mi
                else:
                    mi_data = empty_mi
        colours = mi_data.get("colours") or {}
        selected_skin_name = _colorway_name_from_skin_path(
            skin_choice if skin_choice and skin_choice != "NONE" else json_path
        )
        with utils.timed("mat.scan_textures"):
            if main_pngs is None:
                try:
                    main_pngs = sorted(
                        f for f in os.listdir(folder) if f.lower().endswith(".png")
                    ) if folder else []
                except OSError:
                    main_pngs = []
            if base_pngs is None:
                base_pngs = textures.scan_base_skin_textures(
                    psk_path, selected_skin_name, manual_skins_folder
                ) if psk_path else []
        from ..Maps import staged_import as _staged_sink
        sink = _staged_sink.FINISH_SINK
        sink_n0 = len(sink) if isinstance(sink, list) else -1
        materials.get_or_build_clothing_material(
            obj, folder, colours, psk_path,
            json_path=json_path, decal_folder=decal_folder,
            selected_skin_name=selected_skin_name,
            manual_skins_folder=manual_skins_folder,
            mi_data=mi_data, main_pngs=main_pngs, base_pngs=base_pngs,
        )
        try:
            obj["arc_materials_pending"] = 0
        except Exception:
            pass
        def _post_clothing_hooks():
            # Runs after the staged GT finish so slot repair sees the connected
            # dedicated graph instead of swapping slots to simple shared MIs.
            glass_json = str(obj.get("arc_glass_skin_json", "") or "") or json_path
            with utils.timed("mat.fur_visor_hooks"):
                applied = materials.apply_embedded_visor_slots(obj, psk_path, skin_json=glass_json)
                # Fur shell / card / LOD sections beside the cloth ColorMask slot.
                materials.apply_embedded_fur_slots(obj, psk_path, skin_json=json_path)
                # Visor / goggles / *_Glass: keep ArcTexturer; wire CurvatureID_Override
                # ← Visor (Enable OFF). Glass slots with shell maps never go Visor-only.
                if not applied:
                    materials.setup_visor_material(obj, psk_path, skin_json=glass_json)
            from .materials.clothing import restore_native_clothing_sections
            restore_native_clothing_sections(obj, psk_path, json_path)
            # Clothing shells often have additional named MI slots (CRT displays,
            # SimplePBR lenses, fringe, fur, etc.). Building the ArcTexturer garment
            # alone leaves those imported PSK slots on empty Principled fallbacks.
            slot_context = materials.context_from_model_type(model_type, psk_path)
            slot_repairs = materials.fix_object_materials_from_mi_slots(
                obj, folder, context=slot_context,
            )
            if slot_repairs:
                log.info(
                    "Rebuilt %d named outfit MI slot(s) on '%s'",
                    slot_repairs, obj.name,
                )
        if sink_n0 >= 0 and len(sink) > sink_n0:
            _staged_sink.defer_after(_post_clothing_hooks)
        else:
            _post_clothing_hooks()
        if model_type == "visor":
            return "visor"
        return "clothing"

    if model_type == "fur":
        json_path = skin_json
        if not json_path:
            json_path = textures.get_base_skin_json(psk_path, manual_skins_folder)
        try:
            if json_path:
                obj["arc_skin_json"] = os.path.abspath(json_path)
        except Exception:
            pass
        n = materials.apply_fur_materials_to_object(obj, psk_path, skin_json=json_path)
        return f"fur ({n})" if n else "fur"

    if model_type == "misc":
        materials.setup_misc_material(obj, psk_path)
        return "misc"

    # Stamped map without env path (e.g. Map Stage 1 remapped asset path).
    if model_type == "map":
        materials.setup_map_material(obj, psk_path)
        from ..Outfit_Accessories import revamped_bridge

        report = revamped_bridge.texture_environment_object(obj, psk_path)
        return f"prop {report}"

    # Unknown domain: multi-slot SK/SM before single folder MI.
    # Do not invent weapon/map here when path domain is already known (gated above).
    if psk_path and os.path.isfile(psk_path) and domain == _ad.DOMAIN_UNKNOWN:
        sk_slots = materials._parse_sk_material_slots(psk_path)
        if any(mi_path for _name, _stem, mi_path in sk_slots):
            # Hero / character SKs outside Characters/ still use multi-slot wiring.
            sk_wired = materials.setup_weapon_material(obj, psk_path)
            if sk_wired:
                return f"sk-slots ({sk_wired})"

    # Mesh-folder MI with no Skins/ (shared ColorA/B/C SimplePBR — PonchoFringe,
    # FoxHat M_SimplePBR, etc.). Honour outfit colourway skin_json when stamped.
    # Outfit-only / unknown — env/gun/arc never reach here.
    if psk_path and model_type not in (
        "clothing", "visor", "body", "hair", "face", "fur", "weapon", "enemy", "map",
    ):
        folder_mi = ""
        if skin_json and os.path.isfile(skin_json):
            folder_mi = textures.resolve_part_mi_json(skin_json) or skin_json
        if not folder_mi:
            folder_mi = textures.get_base_skin_json(psk_path, manual_skins_folder)
            if _folder_mi_contradicts_sections(psk_path, folder_mi):
                folder_mi = ""
        if folder_mi and textures.is_hair_cards_mi(folder_mi):
            colour_json = ""
            try:
                from ..Outfit_Accessories import appearance as _appearance
                cid = getattr(bpy.context.scene, "arc_appearance_hair_color", "NONE") or "NONE"
                colour_json = _appearance.resolve_hair_color_path(cid) if cid != "NONE" else ""
            except Exception:
                pass
            try:
                obj["arc_skin_json"] = os.path.abspath(folder_mi)
                obj["arc_hair_mi"] = os.path.abspath(folder_mi)
                if colour_json:
                    obj["arc_hair_color_mi"] = os.path.abspath(colour_json)
            except Exception:
                pass
            materials.setup_hair_material(obj, folder_mi, colour_json=colour_json)
            return "hair-cards"
        if folder_mi and textures.is_fur_mi(folder_mi):
            n = materials.apply_fur_materials_to_object(obj, psk_path, skin_json=folder_mi)
            if n:
                return f"fur ({n})"
        if folder_mi and materials.setup_part_folder_mi_material(obj, folder_mi, psk_path):
            try:
                if folder_mi:
                    obj["arc_skin_json"] = os.path.abspath(folder_mi)
                if skin_choice and skin_choice != "NONE":
                    obj["arc_skin_choice"] = skin_choice
            except Exception:
                pass
            return "part-folder-mi"

    # Fall back: MI-named Blender slots (BlenderUMap / PSK slot names) without SK JSON
    ctx = materials.context_from_model_type(model_type, psk_path)
    fixed = materials.fix_object_materials_from_mi_slots(obj, folder, context=ctx)
    if fixed:
        return f"mi-slots ({fixed})"

    # Last chance: part-folder MI even for odd clothing detections without occlusion.
    if psk_path:
        folder_mi = ""
        if skin_json and os.path.isfile(skin_json):
            folder_mi = textures.resolve_part_mi_json(skin_json) or skin_json
        if not folder_mi:
            folder_mi = textures.get_base_skin_json(psk_path, manual_skins_folder)
            if _folder_mi_contradicts_sections(psk_path, folder_mi):
                folder_mi = ""
        if folder_mi and textures.is_hair_cards_mi(folder_mi):
            colour_json = ""
            try:
                from ..Outfit_Accessories import appearance as _appearance
                cid = getattr(bpy.context.scene, "arc_appearance_hair_color", "NONE") or "NONE"
                colour_json = _appearance.resolve_hair_color_path(cid) if cid != "NONE" else ""
            except Exception:
                pass
            materials.setup_hair_material(obj, folder_mi, colour_json=colour_json)
            return "hair-cards"
        if folder_mi and materials.setup_part_folder_mi_material(obj, folder_mi, psk_path):
            return "part-folder-mi"

    tex_folder = os.path.join(folder, "Textures") if folder else ""
    # Heroes layout: Textures is sibling of Meshes, not a child
    if (not tex_folder or not os.path.isdir(tex_folder)) and folder:
        parent_tex = os.path.join(os.path.dirname(folder), "Textures")
        if os.path.isdir(parent_tex):
            tex_folder = parent_tex
    if tex_folder and os.path.isdir(tex_folder):
        mat = bpy.data.materials.new(name=obj.name + "_Mat")
        mat.use_nodes = True
        obj.active_material = mat
        nodes = mat.node_tree.nodes
        row = 400
        try:
            for fname in sorted(f for f in os.listdir(tex_folder) if f.lower().endswith(".png")):
                img = bpy.data.images.load(os.path.join(tex_folder, fname), check_existing=True)
                node = nodes.new("ShaderNodeTexImage")
                node.image = img
                node.label = fname
                node.interpolation = "Cubic"
                node.location = (-600, row)
                row -= 300
        except OSError:
            pass
        return "textures-folder"

    return "unresolved"


def _still_alive(o) -> bool:
    try:
        o.name
        return True
    except ReferenceError:
        return False


def _duplicate_object_set(objects: list, coll=None) -> list:
    """Deep-copy objects (new mesh/armature datablocks) preserving parent links.

    Used after a single PSK import so extra colourways skip ``psk.import_file``.
    Not Blender instancing — each copy owns its own ``.data``.
    """
    src = [o for o in objects if _still_alive(o)]
    if not src:
        return []
    mapping = {}
    for o in src:
        no = o.copy()
        if o.data is not None:
            no.data = o.data.copy()
        mapping[o] = no
        # Custom props on ObjectID are copied with object.copy(); mesh-level stamps
        # on the new mesh datablock need a refresh from the source mesh.
        if o.type == "MESH" and no.data is not None and o.data is not None:
            for key in ("arc_psk_path", "arc_model_type", "arc_body_variant", "arc_hair_mi"):
                try:
                    if key in o.keys():
                        no[key] = o[key]
                except Exception:
                    pass
    for o, no in mapping.items():
        if o.parent is not None and o.parent in mapping:
            no.parent = mapping[o.parent]
            try:
                no.matrix_parent_inverse = o.matrix_parent_inverse.copy()
            except Exception:
                pass
        # Armature modifiers must point at the copied armature.
        if no.type == "MESH":
            for mod in no.modifiers:
                if mod.type == "ARMATURE" and getattr(mod, "object", None) in mapping:
                    mod.object = mapping[mod.object]
    # Link straight into the colourway collection when given (else the scene root).
    scene_coll = coll if coll is not None else bpy.context.scene.collection
    for no in mapping.values():
        try:
            scene_coll.objects.link(no)
        except RuntimeError:
            pass
    return list(mapping.values())


def _rename_outfit_base_objects(objects: list, character_name: str) -> None:
    """Rename merged armature object to SK_{Character} — not SK_*_PartName.

    Armature join keeps the largest part's name (e.g. SK_AntlerShaman_PonchoFringe);
    collapse that to the model stem so the skeleton icon in the outliner matches
    the character. Mesh objects keep their original body-part names
    (e.g. SK_AntlerShaman_Body, SK_AntlerShaman_PonchoFringe).

    Only armature object / Armature datablock names are changed — never mesh
    names, bone names, or rest-pose transforms (those are handled solely by
    ``rig.fix_bone_orientations``).
    """
    sk_name = importing.character_sk_name(character_name)
    arms = [o for o in objects if _still_alive(o) and o.type == "ARMATURE"]
    for arm in arms:
        try:
            arm.name = sk_name
            # Datablock rename only; bones live in arm.data.bones and are untouched.
            if arm.data is not None:
                arm.data.name = sk_name
        except Exception:
            pass


def _ensure_child_collection(parent, name: str):
    """Return a collection named *name* under *parent*, creating if needed."""
    for child in parent.children:
        if child.name == name or child.name.startswith(name + "."):
            return child
    coll = bpy.data.collections.new(name)
    parent.children.link(coll)
    return coll


def _link_objects_to_collection(objects: list, coll) -> None:
    for o in objects:
        if not _still_alive(o):
            continue
        users = o.users_collection
        if len(users) == 1 and users[0] == coll:
            continue
        for c in list(users):
            try:
                c.objects.unlink(o)
            except Exception:
                pass
        try:
            coll.objects.link(o)
        except RuntimeError:
            pass


def _snapshot_entry_skin_choices(entries) -> list:
    """Capture skin state so batch IO discovery can restore after dry-runs."""
    out = []
    for entry in entries:
        try:
            out.append((entry, entry.skin_choice, str(getattr(entry, "batch_skin_json", "") or ""), entry.get("arc_outfit_material_bindings"), entry.get("arc_unused_outfit_material_bindings")))
        except Exception:
            out.append((entry, "NONE", ""))
    return out


def _restore_entry_skin_choices(snapshot) -> None:
    for item in snapshot or ():
        entry, choice = item[:2]
        try:
            entry.skin_choice = choice
        except Exception:
            pass
        try:
            entry.batch_skin_json = item[2] if len(item) > 2 else ""
            if len(item) > 3 and item[3] is not None:
                entry["arc_outfit_material_bindings"] = item[3]
            elif "arc_outfit_material_bindings" in entry:
                del entry["arc_outfit_material_bindings"]
            if len(item) > 4 and item[4] is not None:
                entry["arc_unused_outfit_material_bindings"] = item[4]
            elif "arc_unused_outfit_material_bindings" in entry:
                del entry["arc_unused_outfit_material_bindings"]
        except Exception:
            pass


def _collect_batch_io_jobs(context, entries, selected_presets) -> list:
    """Resolve clothing MI paths for every colourway×part (main thread; no bpy in workers)."""
    jobs = []
    if not entries or not selected_presets:
        return jobs
    snap = _snapshot_entry_skin_choices(entries)
    decal_folder = utils.get_decal_folder() or ""
    try:
        for preset_idx, (_preset_name, preset_path) in enumerate(selected_presets):
            importing.apply_outfit_preset(context, preset_path)
            for entry in entries:
                psk_path = bpy.path.abspath(entry.psk_path)
                model_type = textures.detect_model_type(psk_path)
                # Appearance import shows the shell albedo. A batch colourway
                # still builds the clothing graph when Skins/ is present.
                if model_type == "misc" and textures.gear_shell_has_colorway(psk_path):
                    model_type = "clothing"
                if model_type not in ("clothing", "visor"):
                    continue
                batch_skin_json = str(getattr(entry, "batch_skin_json", "") or "")
                json_path = bpy.path.abspath(batch_skin_json) if batch_skin_json else (
                    "" if entry.skin_choice == "NONE" else bpy.path.abspath(entry.skin_choice)
                )
                if not json_path:
                    json_path = textures.get_base_skin_json(
                        psk_path, entry.manual_skins_folder
                    ) or ""
                if json_path:
                    json_path = textures.resolve_part_mi_json(json_path) or json_path
                manual = (
                    bpy.path.abspath(entry.manual_skins_folder)
                    if entry.manual_skins_folder else ""
                )
                selected_skin_name = _colorway_name_from_skin_path(
                    entry.skin_choice if entry.skin_choice != "NONE" else json_path
                )
                jobs.append({
                    "key": (
                        preset_idx,
                        os.path.normcase(os.path.normpath(psk_path)),
                    ),
                    "json_path": json_path or "",
                    "folder": os.path.dirname(psk_path) if psk_path else "",
                    "psk_path": psk_path,
                    "selected_skin_name": selected_skin_name or "",
                    "manual_skins_folder": manual,
                    "decal_folder": decal_folder,
                    # Authored shared materials / inherited TA paths can require
                    # Scene-aware resolution. Resolve them here, never in workers.
                    "resolved_base_pngs": textures.scan_base_skin_textures(
                        psk_path, selected_skin_name or "", manual),
                })
    finally:
        _restore_entry_skin_choices(snap)
    return jobs


def _batch_io_prefetch_one(job: dict) -> dict:
    """Worker: discover image paths for later main-thread load. No bpy, no full PNG reads.

    MI JSON paths are already resolved on the main thread. Workers must not call
    ``bpy``, stem-index repair, or ``get_pioneer_root`` (those race / hang Blender).
    Prefetched ``mi_data`` is for path discovery only — materials always re-parse
    on the main thread so colourways cannot share a mutated / wrong MI dict.
    """
    json_path = job.get("json_path") or ""
    folder = job.get("folder") or ""
    empty_mi = {
        "colours": {}, "ta_ids": {}, "zone_scalars": {},
        "mi_params": {"scalars": [], "vectors": []}, "decals": [],
    }
    try:
        # Local parse only — path already repaired during job collect.
        mi_data = (
            textures.parse_clothing_mi_local(json_path) if json_path else empty_mi
        )
    except Exception:
        mi_data = empty_mi
    try:
        main_pngs = sorted(
            f for f in os.listdir(folder) if f.lower().endswith(".png")
        ) if folder and os.path.isdir(folder) else []
    except OSError:
        main_pngs = []
    if "resolved_base_pngs" not in job:
        raise RuntimeError("Clothing IO job needs main-thread texture-array resolution")
    base_pngs = list(job["resolved_base_pngs"])
    try:
        paths = materials.collect_clothing_image_paths(
            folder,
            main_pngs=main_pngs,
            base_pngs=base_pngs,
            decals=mi_data.get("decals") or [],
            decal_folder=job.get("decal_folder") or "",
            search_dirs=[folder] if folder else None,
            resolve_object_paths=False,
        )
    except Exception:
        paths = []
    # Do NOT full-read PNG bytes here. That was saturating disk for minutes after
    # mesh import finished (PSK ~2s vs join ~177s). Path discovery is enough;
    # ``prefetch_images`` on the main thread loads into bpy safely.
    return {
        "key": job.get("key"),
        "json_path": json_path,
        # Path-discovery aid only — never applied as material mi_data.
        "paths": paths,
        "main_pngs": main_pngs,
        "base_pngs": base_pngs,
    }


def _reapply_materials_for_preset(
    objects: list,
    entries,
    *,
    io_cache: dict = None,
    preset_idx: int = 0,
) -> int:
    """Re-run material setup on copied meshes using current entry skin choices."""
    gen = _iter_reapply_materials_for_preset(
        objects, entries, io_cache=io_cache, preset_idx=preset_idx
    )
    applied = 0
    for _ in gen:
        applied += 1
    return applied


def _iter_reapply_materials_for_preset(
    objects: list,
    entries,
    *,
    io_cache: dict = None,
    preset_idx: int = 0,
    clear_placeholder: bool = False,
    mesh_prefetch=None,
):
    """Generator form: yields ("material", index, total, label) before each object.

    ``mesh_prefetch(key)``, when given, reads that mesh's textures just before its
    materials are built (key = (preset_idx, psk key) as in ``io_cache``).

    Returns the applied count as the generator's StopIteration value.
    """
    by_psk = {}
    for entry in entries:
        psk = bpy.path.abspath(entry.psk_path)
        by_psk[os.path.normcase(os.path.normpath(psk))] = entry
    applied = 0
    objects = list(objects)
    n_objects = len(objects)
    for obj_idx, obj in enumerate(objects):
        if not _still_alive(obj) or obj.type != "MESH":
            continue
        psk_path = ""
        try:
            psk_path = str(obj.get("arc_psk_path", "") or "")
        except Exception:
            psk_path = ""
        if not psk_path:
            continue
        key = os.path.normcase(os.path.normpath(bpy.path.abspath(psk_path)))
        entry = by_psk.get(key)
        if entry is None:
            continue
        obj_label = _object_label(obj)
        yield ("material", obj_idx + 1, n_objects, obj_label)
        if clear_placeholder:
            # Staged mesh phase filled slots with the grey placeholder. Empty the list so the
            # material setup sees a fresh mesh, as the sync path does.
            from ..Maps import staged_import as _staged_ph
            _staged_ph.restore_slots(obj)
        batch_skin_json = str(getattr(entry, "batch_skin_json", "") or "")
        json_path = bpy.path.abspath(batch_skin_json) if batch_skin_json else (
            "" if entry.skin_choice == "NONE" else bpy.path.abspath(entry.skin_choice)
        )
        if not json_path:
            json_path = textures.get_base_skin_json(psk_path, entry.manual_skins_folder)
        if json_path:
            json_path = textures.resolve_part_mi_json(json_path) or json_path
        body_variant = entry.body_choice if hasattr(entry, "body_choice") else "NONE"
        hair_mi = entry.hair_mi if hasattr(entry, "hair_mi") else "NONE"
        prefetch = None
        if mesh_prefetch is not None:
            yield ("textures", obj_idx + 1, n_objects, f"{obj_label} (textures)")
            with utils.timed("mesh_prefetch_images"):
                mesh_prefetch((preset_idx, key))
        if io_cache is not None:
            prefetch = io_cache.get((preset_idx, key))
            # Refuse stale prefetch if the colourway MI path changed since collect.
            if prefetch is not None and json_path:
                pref_json = (prefetch.get("json_path") or "").strip()
                if pref_json and (
                    os.path.normcase(os.path.normpath(pref_json))
                    != os.path.normcase(os.path.normpath(json_path))
                ):
                    log.debug(
                        "prefetch MI path mismatch for %s — ignoring path lists",
                        getattr(obj, "name", "?"),
                    )
                    prefetch = None
        # Main-thread parse (session-cached). Never reuse worker mi_data.
        mi_data = None
        if json_path:
            try:
                mi_data = textures.parse_clothing_mi(json_path)
            except Exception:
                mi_data = None
        if clear_placeholder:
            from ..Maps import staged_import as _staged_sink
            _staged_sink.FINISH_SINK = []
        try:
          apply_materials_to_object(
            obj, psk_path,
            body_variant=body_variant,
            hair_mi=hair_mi,
            skin_json=json_path,
            manual_skins_folder=entry.manual_skins_folder,
            skin_choice=batch_skin_json or entry.skin_choice,
            mi_data=mi_data,
            main_pngs=(prefetch or {}).get("main_pngs"),
            base_pngs=(prefetch or {}).get("base_pngs"),
            material_bindings=_entry_material_bindings(entry),
          )
        finally:
            if clear_placeholder:
                _deferred = _staged_sink.FINISH_SINK or []
                _staged_sink.FINISH_SINK = None
        if clear_placeholder:
            from .materials import dr_primary_operations as _dr_ops
            for _ent in _deferred:
                # Deferred finish runs outside apply_materials' session; open one per graph.
                with _dr_ops.operation_session():
                    for _cut in _ent["gen"]:
                        _cut = str(_cut)
                        _tidy = _cut in ("tidy", "blocks")
                        yield ("tidy" if _tidy else "slice", obj_idx + 1, n_objects,
                               obj_label if _tidy else f"{obj_label} ({_cut})")
                    for _after in _ent["after"]:
                        _after()
        if psk_path.casefold().endswith(".uemodel"):
            try:
                from .materials.native import shader_parity
                shader_parity.apply_uemodel_slots(obj, psk_path)
            except Exception as exc:
                print(f"Arc Raiders shader parity: slot pass failed for {obj.name}: {exc}")
        if json_path:
            try:
                obj["arc_skin_json"] = os.path.abspath(json_path)
                if batch_skin_json:
                    obj["arc_skin_choice"] = os.path.abspath(batch_skin_json)
            except Exception:
                pass
        applied += 1
        yield ("slice", obj_idx + 1, n_objects, f"{obj_label} (slots)")
    return applied


def _object_label(obj) -> str:
    try:
        return str(obj.name)
    except Exception:
        return "?"


def _entry_label(entry) -> str:
    try:
        return os.path.splitext(os.path.basename(bpy.path.abspath(entry.psk_path)))[0]
    except Exception:
        return "?"


def _shift_instance_on_x(
    objects: list,
    x_cursor: float,
    margin_frac: float = 0.25,
    *,
    update_view: bool = True,
    snap_left_edge: bool = True,
) -> float:
    """Lay out colourway instances along X; return updated cursor.

    ``snap_left_edge=True`` (extra colourways): translate so the mesh bbox
    left edge lands at ``x_cursor``.
    ``snap_left_edge=False`` (first colourway): leave PSK import origin alone
    so the outfit lines up with body/head/hair at world origin; only advance
    the cursor past the right edge for the next instance.
    """
    if update_view:
        bpy.context.view_layer.update()
    minx = maxx = None
    for o in objects:
        if not _still_alive(o) or o.type != "MESH":
            continue
        for corner in o.bound_box:
            wx = (o.matrix_world @ mathutils.Vector(corner)).x
            minx = wx if minx is None else min(minx, wx)
            maxx = wx if maxx is None else max(maxx, wx)
    if minx is None:
        return x_cursor
    width = max(maxx - minx, 1e-4)
    if snap_left_edge:
        shift = x_cursor - minx
        for o in objects:
            if _still_alive(o) and o.parent is None:
                o.location.x += shift
        return x_cursor + width * (1.0 + margin_frac)
    # First instance: keep import origin (body/hair stay at 0,0,0 the same way).
    return maxx + width * margin_frac


def _hide_imported_armatures(objects) -> int:
    """Viewport-hide armature objects (outliner eye off). Meshes stay parented/skinned."""
    hidden = 0
    for o in objects:
        if not _still_alive(o) or o.type != "ARMATURE":
            continue
        try:
            o.hide_set(True)
            hidden += 1
        except Exception:
            try:
                o.hide_viewport = True
                hidden += 1
            except Exception:
                pass
    return hidden


def _mesh_world_corners(objects) -> list:
    """World-space bound_box corners for MESH objects only (skip armatures)."""
    corners = []
    for o in objects:
        if not _still_alive(o) or o.type != "MESH":
            continue
        try:
            mw = o.matrix_world
            for corner in o.bound_box:
                corners.append(mw @ mathutils.Vector(corner))
        except Exception:
            pass
    return corners


def _bbox_center_from_corners(corners) -> mathutils.Vector | None:
    if not corners:
        return None
    min_x = min(c.x for c in corners)
    max_x = max(c.x for c in corners)
    min_y = min(c.y for c in corners)
    max_y = max(c.y for c in corners)
    min_z = min(c.z for c in corners)
    max_z = max(c.z for c in corners)
    return mathutils.Vector((
        (min_x + max_x) * 0.5,
        (min_y + max_y) * 0.5,
        (min_z + max_z) * 0.5,
    ))


def _group_mesh_center(objects) -> mathutils.Vector | None:
    return _bbox_center_from_corners(_mesh_world_corners(objects))


def _sort_instance_groups_by_x(instance_groups: list) -> list:
    """Order colourway/instance groups left-to-right by mesh bbox center X."""
    keyed = []
    for group in instance_groups:
        alive = [o for o in group if _still_alive(o)]
        if not alive:
            continue
        center = _group_mesh_center(alive)
        cx = float(center.x) if center is not None else 0.0
        keyed.append((cx, alive))
    keyed.sort(key=lambda t: t[0])
    return [g for _, g in keyed]


def _focus_point_for_instance_groups(sorted_groups: list) -> mathutils.Vector | None:
    """Count-based viewport focus for imported models (one group = one model).

    1 → bbox center of that model; odd N → middle group's center;
    even N → midpoint between the two central groups.
    """
    centers = []
    for group in sorted_groups:
        c = _group_mesh_center(group)
        if c is not None:
            centers.append(c)
    n = len(centers)
    if n == 0:
        return None
    if n == 1:
        return centers[0]
    if n % 2 == 1:
        return centers[n // 2]
    return (centers[n // 2 - 1] + centers[n // 2]) * 0.5


def _frame_viewport_on_outfit_models(context, instance_groups: list) -> int:
    """Center VIEW_3D regions on imported models using count-based focus logic."""
    context = context or bpy.context
    try:
        context.view_layer.update()
    except Exception:
        pass

    sorted_groups = _sort_instance_groups_by_x(instance_groups)
    if not sorted_groups:
        return 0

    focus = _focus_point_for_instance_groups(sorted_groups)
    if focus is None:
        return 0

    all_corners = []
    for group in sorted_groups:
        all_corners.extend(_mesh_world_corners(group))
    if not all_corners:
        return 0

    # Distance so all imported meshes stay in frame around the focus point.
    max_dist = 0.0
    for c in all_corners:
        max_dist = max(max_dist, (c - focus).length)
    view_distance = max(max_dist * 2.15, 0.5)

    framed = 0
    for window in context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type != "VIEW_3D":
                    continue
                rv3d = space.region_3d
                if rv3d is None:
                    continue
                try:
                    rv3d.view_location = focus.copy()
                    rv3d.view_distance = float(view_distance)
                    if getattr(rv3d, "view_perspective", None) == "CAMERA":
                        rv3d.view_perspective = "PERSP"
                    framed += 1
                except Exception:
                    pass
    return framed


def _maybe_import_references(context, objects, *, roles: set[str] | None = None) -> None:
    """Honor scene Include Reference toggle after outfit / appearance import."""
    if not bool(getattr(context.scene, "arc_include_reference", True)):
        return
    try:
        from ..Outfit_Accessories import reference_planes

        msgs = reference_planes.import_references_for_objects(
            context, objects, roles=roles,
        )
        for m in msgs:
            print(f"Arc Asset Importer: {m}")
    except Exception as exc:
        print(f"Arc Asset Importer: reference planes skipped: {exc}")


# Reference cards are outfit / cosmetic UI art. Props, weapons, and map
# meshes must not grow a REF plane just because an outfit is selected.
_REFERENCE_MODEL_TYPES = {"clothing", "face", "body", "hair", "visor", "fur"}


def _post_import_outfit_ux(context, instance_groups: list, *, references: bool = True) -> None:
    """After rig/rename/layout: hide armatures and frame the viewport on models."""
    if not instance_groups:
        return
    for group in instance_groups:
        _hide_imported_armatures(group)
        if references:
            _maybe_import_references(context, group, roles={"Outfit"})
    _frame_viewport_on_outfit_models(context, instance_groups)


def _drain_generator(gen):
    """Run a progress generator to completion and return its StopIteration value."""
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


_CACHE_KEY_PROP = "arc_clothing_cache_key"


def _clothing_cache_dict():
    from .materials import common as _mat_common
    return _mat_common._CLOTHING_MATERIAL_CACHE


def _restore_clothing_cache_from_data() -> int:
    """Drop dead share-cache entries; re-index stamped materials still in bpy.data."""
    import ast
    cache = _clothing_cache_dict()
    for key, mat in list(cache.items()):
        try:
            if mat.name not in bpy.data.materials or bpy.data.materials[mat.name] != mat:
                cache.pop(key, None)
        except ReferenceError:
            cache.pop(key, None)
    added = 0
    for mat in bpy.data.materials:
        raw = mat.get(_CACHE_KEY_PROP)
        if not raw or mat.users == 0:
            continue
        try:
            key = ast.literal_eval(str(raw))
        except Exception:
            continue
        if isinstance(key, tuple) and key not in cache:
            cache[key] = mat
            added += 1
    return added


def _stamp_clothing_cache_keys() -> None:
    """Write each live share-cache key on its material so a later import can reuse it."""
    for key, mat in list(_clothing_cache_dict().items()):
        try:
            text = repr(key)
            if mat.get(_CACHE_KEY_PROP) != text:
                mat[_CACHE_KEY_PROP] = text
        except Exception:
            pass


def batch_import_instances(context, selected_presets) -> tuple:
    """Sync batch import: drains ``iter_batch_import_instances`` in one call."""
    return _drain_generator(iter_batch_import_instances(context, selected_presets))


def iter_batch_import_instances(context, selected_presets, *, staged: bool = False):
    """Import one full model instance per selected outfit colourway.

    Generator: yields ``(stage, index, total, label)`` after each mesh and each
    material step so a caller can run it in time slices. ``staged=True`` also
    shows a grey placeholder on each new mesh until its materials are built.
    The return value (StopIteration.value) is ``(instances, parts, summary)``.

    Phase C pipeline:
      1. Preload Arc groups
      2. Kick ThreadPool path discovery (no bpy / no full PNG reads) while main continues
      3. Import PSK once (colourway 0), materials deferred
      4. Deep-copy × N remaining colourways
      5. Join IO + ``prefetch_images`` (main-thread bpy image load)
      6. Build materials one colourway at a time (MI re-parsed on main; shared cache)
      7. Link collections + X layout; single ``view_layer.update``

    Returns ``(instances, parts_total, perf_summary)`` where ``perf_summary`` is a
    short INFO string pointing at ``%TEMP%/arc_outfits_perf.log``.
    """
    n_presets = len(selected_presets or [])

    def _staged_mark(objs):
        if not staged:
            return
        from ..Maps import staged_import as _staged
        _staged.stage_placeholder(objs)

    with utils.perf_session("batch_import", colourways=n_presets) as session:
        with utils.timed(f"batch_import_instances:{n_presets} colourways"):
            try:
                with utils.timed("preload_arc_groups"):
                    utils.preload_arc_node_groups()
            except Exception as exc:
                print(f"Arc Asset Importer: ArcTexturer preload failed (non-fatal): {exc}")

            entries = list(context.scene.arc_psk_entries)
            if not entries or not selected_presets:
                session.finish(also_print=True)
                return 0, 0, session.summary_line

            # Re-import fast path: keep live share-cache hits and rebuild the index from
            # stamped materials still in bpy.data (keys carry recipe + fingerprint, so a
            # changed graph or texture set never hits). Update Materials still clears.
            _restore_clothing_cache_from_data()
            try:
                textures.clear_clothing_path_caches()
            except Exception:
                pass

            types = [textures.detect_model_type(bpy.path.abspath(e.psk_path)) for e in entries]
            any_clothing = any(t == "clothing" for t in types)
            dominant_type = "weapon" if all(t == "weapon" for t in types) else ""

            character_name = ""
            for entry in entries:
                character_name = importing.get_character_name(bpy.path.abspath(entry.psk_path))
                if character_name:
                    break
            scene = context.scene
            # One collection scheme: each colourway is linked straight into the
            # collection asset_organization keeps it in, on the slot it is placed on.
            from ..Outfit_Accessories import asset_organization as _asset_org
            from ..Outfit_Accessories import import_layout as _layout
            _slot_taken = _layout.discover_slots(scene) if _layout.offset_enabled(scene) else None
            _slot_next = 0
            preset_slots = {}
            preset_colls = {}

            # --- Kick IO prefetch (overlap with PSK import) ---
            try:
                import time as _t
                _t0 = _t.perf_counter()
                from . import pak_extract as _pe
                _pe.kick_previews(scene, defer=True)
                print(f"[ArcPak] previews kick main-thread ms={(_t.perf_counter() - _t0) * 1000:.1f}")
            except Exception:
                pass
            io_executor = None
            io_futures = None
            jobs = []

            def _kick_io_prefetch():
                # Staged mode starts this after the first mesh is linked so
                # MI path resolution does not delay first visible mesh.
                nonlocal io_executor, io_futures, jobs
                try:
                    with utils.timed("batch_collect_io_jobs"):
                        jobs = _collect_batch_io_jobs(context, entries, selected_presets)
                except Exception as exc:
                    log.debug("batch IO job collect failed: %s", exc)
                    jobs = []

                if jobs and utils.io_prefetch_enabled(context):
                    from concurrent.futures import ThreadPoolExecutor
                    cpu = os.cpu_count() or 4
                    workers = max(4, min(8, cpu, len(jobs)))
                    io_executor = ThreadPoolExecutor(max_workers=workers)
                    io_futures = [
                        io_executor.submit(_batch_io_prefetch_one, job) for job in jobs
                    ]
                    log.info(
                        "batch IO prefetch: %d job(s) on %d worker(s)",
                        len(jobs), workers,
                    )

            io_kicked = not staged
            if io_kicked:
                _kick_io_prefetch()

            # --- Import mesh once, copy remaining colourways (materials deferred) ---
            template_objs = None
            pending = []  # (preset_idx, preset_name, preset_path, inst_objs)
            mesh_done = 0
            mesh_total = len(entries)

            for preset_idx, (preset_name, preset_path) in enumerate(selected_presets):
                importing.apply_outfit_preset(context, preset_path)
                # Each mesh goes into its final collection as soon as it exists.
                if _slot_taken is None:
                    slot = 0
                else:
                    while "Outfit" in _slot_taken.get(_slot_next, ()):
                        _slot_next += 1
                    slot = _slot_next
                    _slot_next += 1
                preset_slots[preset_idx] = slot
                color_coll = _asset_org.collection_for_role("Outfit", scene, slot)
                preset_colls[preset_idx] = color_coll

                if preset_idx == 0 or template_objs is None:
                    inst_objs = []
                    for e_idx, (ok, msg, new_objs) in enumerate(
                        _iter_process_entries_pipelined(
                            entries, context, skip_materials=True
                        )
                    ):
                        inst_objs.extend(new_objs)
                        _link_objects_to_collection(new_objs, color_coll)
                        if ok:
                            print(f"    [{preset_name}] {msg}")
                        else:
                            print(f"    [{preset_name}] FAILED: {msg}")
                        _staged_mark(new_objs)
                        mesh_done += 1
                        yield ("mesh", mesh_done, mesh_total, _entry_label(entries[e_idx]))
                        if not io_kicked:
                            io_kicked = True
                            _kick_io_prefetch()
                    if not io_kicked:
                        io_kicked = True
                        _kick_io_prefetch()
                    if not inst_objs:
                        continue
                    yield ("rig", preset_idx + 1, n_presets, str(preset_name))
                    with utils.timed("fix_rig"):
                        rig.fix_rig_all(inst_objs, merge=any_clothing, model_type=dominant_type)
                    inst_objs = [o for o in inst_objs if _still_alive(o)]
                    _link_objects_to_collection(inst_objs, color_coll)
                    _staged_mark(inst_objs)
                    with utils.timed("rename_outfit"):
                        _rename_outfit_base_objects(inst_objs, character_name)
                    template_objs = list(inst_objs)
                    mesh_total = mesh_done + (len(selected_presets) - 1) * len(template_objs)
                    yield ("rig", preset_idx + 1, n_presets, str(preset_name))
                else:
                    with utils.timed(f"duplicate_colourway:{preset_name}"):
                        inst_objs = _duplicate_object_set(template_objs, color_coll)
                    if not inst_objs:
                        continue
                    print(
                        f"    [{preset_name}] Copied {len(inst_objs)} object(s) from template "
                        f"(materials deferred)"
                    )
                    _staged_mark(inst_objs)
                    for dup_obj in inst_objs:
                        mesh_done += 1
                        yield ("mesh", mesh_done, mesh_total, _object_label(dup_obj))

                pending.append((preset_idx, preset_name, preset_path, inst_objs))

            # --- Join IO + warm image cache on main thread ---
            yield ("textures", 0, 0, "reading material lists")
            io_cache = {}
            path_union = []
            seen_paths = set()
            io_ok = io_fail = 0
            if io_futures is not None:
                with utils.timed("batch_join_io_prefetch"):
                    for fut in io_futures:
                        try:
                            result = fut.result()
                        except Exception as exc:
                            io_fail += 1
                            log.debug("batch IO worker failed: %s", exc)
                            continue
                        if not result:
                            io_fail += 1
                            continue
                        io_ok += 1
                        key = result.get("key")
                        if key is not None:
                            io_cache[key] = result
                        for p in result.get("paths") or ():
                            nk = os.path.normcase(os.path.normpath(p)) if p else ""
                            if not nk or nk in seen_paths:
                                continue
                            seen_paths.add(nk)
                            path_union.append(p)
                if io_executor is not None:
                    io_executor.shutdown(wait=False)
                log.info(
                    "batch_join_io_prefetch: %d ok, %d fail, %d unique path(s)",
                    io_ok, io_fail, len(path_union),
                )
            elif jobs:
                with utils.timed("batch_io_sequential"):
                    for job in jobs:
                        try:
                            result = _batch_io_prefetch_one(job)
                        except Exception as exc:
                            io_fail += 1
                            log.debug("batch IO sequential failed: %s", exc)
                            continue
                        if not result:
                            io_fail += 1
                            continue
                        io_ok += 1
                        key = result.get("key")
                        if key is not None:
                            io_cache[key] = result
                        for p in result.get("paths") or ():
                            nk = os.path.normcase(os.path.normpath(p)) if p else ""
                            if not nk or nk in seen_paths:
                                continue
                            seen_paths.add(nk)
                            path_union.append(p)
                log.info(
                    "batch_io_sequential: %d ok, %d fail, %d unique path(s)",
                    io_ok, io_fail, len(path_union),
                )

            # Textures are read per mesh, right before that mesh's materials are
            # built, instead of one up-front read of every outfit texture.
            _prefetched = set()

            def _mesh_prefetch(key):
                result = io_cache.get(key)
                if not isinstance(result, dict):
                    return 0
                paths = materials.expand_clothing_image_paths(
                    list(result.get("paths") or ()), {key: result}
                )
                fresh = []
                for p in paths:
                    nk = os.path.normcase(os.path.normpath(p)) if p else ""
                    if nk and nk not in _prefetched:
                        _prefetched.add(nk)
                        fresh.append(p)
                return materials.prefetch_images(fresh, force_decode=True) if fresh else 0

            yield ("textures", 0, 0, "ready")
            # --- Build materials one colourway at a time; defer collection links ---
            pending_links = []
            parts_total = 0
            from ..Maps import staged_import as _staged_mod
            _view_saved = _staged_mod.enter_solid_viewports()
            materials.set_batch_material_mode(True)
            # Later colourways of a mesh slot start from the first colourway's flat graph.
            from .materials import cw_replay as _cw_replay
            _cw_replay.STASH_ON[0] = len(pending) > 1
            # Every mesh of the import starts from the flat graph of an earlier mesh.
            _cw_replay.SHARE_ON[0] = True
            try:
                mat_total = sum(len(p[3]) for p in pending)
                mat_done = 0
                for preset_idx, preset_name, preset_path, inst_objs in pending:
                    importing.apply_outfit_preset(context, preset_path)
                    mat_gen = _iter_reapply_materials_for_preset(
                        inst_objs, entries, io_cache=io_cache, preset_idx=preset_idx,
                        clear_placeholder=staged, mesh_prefetch=_mesh_prefetch,
                    )
                    n_mat = 0
                    while True:
                        try:
                            ev = next(mat_gen)
                        except StopIteration as stop:
                            n_mat = stop.value or 0
                            break
                        if ev[0] == "material":
                            mat_done += 1
                        yield (ev[0], mat_done, mat_total, ev[3])
                        # Graph first: unbind this run's new image nodes; bound later.
                        pass  # lazy bind off: rebind cost 45 s (L2)
                    parts_total += n_mat
                    print(
                        f"    [{preset_name}] Applied materials on {n_mat} mesh(es)"
                    )
                    pending_links.append((inst_objs, preset_colls[preset_idx]))
                materials.set_batch_material_mode(False)
                yield ("finish", 0, 5, "binding textures")
                _stamp_clothing_cache_keys()
                yield ("finish", 0, 5, "stamping materials")
            finally:
                # Cancel or error: never leave a switched viewport.
                materials.set_batch_material_mode(False)
                _cw_replay.STASH_ON[0] = False
                _cw_replay.SHARE_ON[0] = False
                _cw_replay.clear_stashes()
                _staged_mod.restore_viewports(_view_saved)

            yield ("finish", 0, 5, "restoring the viewport")
            if staged:
                from ..Maps import staged_import as _staged
                _staged.sweep_placeholder(
                    [o for _p, _n, _pp, objs in pending for o in objs]
                )

            yield ("finish", 1, 5, "linking collections")
            with utils.timed("link_collections"):
                for inst_objs, color_coll in pending_links:
                    _link_objects_to_collection(inst_objs, color_coll)

            yield ("finish", 2, 5, "updating the scene")
            with utils.timed("view_layer_update"):
                try:
                    context.view_layer.update()
                except Exception:
                    pass

            yield ("finish", 3, 5, "placing the outfit")
            instances = 0
            instance_groups = []
            with utils.timed("layout_slots"):
                from ..Outfit_Accessories import import_layout as _layout

                for _preset_idx, _preset_name, _preset_path, inst_objs in pending:
                    alive = [o for o in inst_objs if _still_alive(o)]
                    for o in alive:
                        try:
                            if not o.get("arc_assembly_role"):
                                o["arc_assembly_role"] = "Outfit"
                        except Exception:
                            pass
                    slot = _layout.place_imported(
                        alive, "Outfit", scene=context.scene,
                        prefer_slot=preset_slots.get(_preset_idx),
                    )
                    instances += 1
                    instance_groups.append(alive)
                    if _layout.offset_enabled(context.scene):
                        print(
                            f"    [{_preset_name}] slot {slot} @ "
                            f"X={_layout.slot_world_x(slot, context.scene):.1f} m"
                        )

            yield ("finish", 4, 5, "selecting and framing")
            with utils.timed("post_import_ux"):
                _post_import_outfit_ux(context, instance_groups)
            yield ("finish", 5, 5, "saving the graph cache")

            # All materials are bound: pack images later, one per timer tick.
            _staged_mod.schedule_auto_pack()
            try:
                from .materials import graph_cache as _gcm
                for _gc_step in _gcm.iter_flush():
                    yield ("finish", 5, 5, "saving the graph cache")
            except Exception as _gc_exc:
                print("DataRaiders graph_cache flush:", repr(_gc_exc))

            cache = materials.clothing_cache_stats()
            session.count("share_cache_hits", cache.get("hits", 0))
            session.count("share_cache_misses", cache.get("misses", 0))
            session.count("io_jobs_ok", io_ok)
            if io_fail:
                session.count("io_jobs_fail", io_fail)
            session.set_meta(
                instances=instances,
                parts=parts_total,
                cache_size=cache.get("size", 0),
                io_jobs=len(jobs),
                io_paths=len(path_union),
            )

        session.finish(also_print=True)
        return instances, parts_total, session.summary_line

def texture_existing_model(obj, model_name: str, manual_folder: str = "") -> bool:
    """Apply Arc Raiders texturing to an existing model in place (no re-import)."""
    psk_path = find_psk_for_model(model_name, manual_folder)
    if not psk_path:
        fixed = materials.fix_object_materials_from_mi_slots(obj, manual_folder)
        if fixed:
            print(f"Arc Raiders: Fixed {fixed} MI slot(s) on '{obj.name}' via material names")
            return True
        print(f"Arc Raiders: Could not find PSK/asset for '{model_name}'")
        return False
    status = apply_materials_to_object(obj, psk_path, manual_skins_folder=manual_folder)
    print(f"Arc Raiders: Fixed materials on '{obj.name}' as {status}")
    return status != "unresolved"


def _strip_blender_name_suffix(name: str) -> str:
    """SM_Foo.001 → SM_Foo; also strip BlenderUMap '.mat' material suffix."""
    n = name or ""
    n = re.sub(r"\.mat$", "", n, flags=re.IGNORECASE)
    return re.sub(r"\.\d+$", "", n)


def _strip_umap_hash_suffix(name: str) -> str:
    """SM_CatBed_01_A_14b0362f → SM_CatBed_01_A (BlenderUMap mesh-data hash)."""
    return re.sub(r"_[0-9a-f]{8}$", "", name or "", flags=re.IGNORECASE)


def _mesh_name_body(name: str) -> str:
    """Normalize mesh name for fuzzy match: strip .001, hash, and SK_/SM_ prefix."""
    stem = _strip_umap_hash_suffix(_strip_blender_name_suffix(name))
    stem = re.sub(r"^(SK|SM)_", "", stem, flags=re.IGNORECASE)
    return stem.lower()


def _name_candidates_for_object(obj) -> list:
    """Collect likely UE mesh names from an object (UMap / Blender suffixes / parents)."""
    raws = [obj.name, str(obj.get("arc_source_object_name") or "")]
    for prop in ("arc_psk_path", "arc_source_mesh"):
        source = str(obj.get(prop) or "").replace("\\", "/")
        if source:
            raws.append(os.path.splitext(source.rsplit("/", 1)[-1])[0])
    if getattr(obj, "data", None) is not None:
        raws.append(obj.data.name)
    if obj.parent is not None:
        raws.append(obj.parent.name)
    # BlenderUMap material names are often the best clue (MI_CatBed_01_A.mat)
    for slot in getattr(obj, "material_slots", []) or []:
        if slot.material:
            raws.append(slot.material.name)

    names = []

    def _add(n: str):
        n = (n or "").strip()
        if n and n not in names:
            names.append(n)

    for raw in raws:
        n = _strip_blender_name_suffix(raw)
        _add(n)
        stripped = _strip_umap_hash_suffix(n)
        _add(stripped)

    # Pull embedded SK_/SM_/MI_ tokens out of longer actor-style names
    extra = []
    for n in names:
        for m in re.finditer(
            r"((?:SK|SM|MI)_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)",
            n,
            flags=re.IGNORECASE,
        ):
            token = _strip_umap_hash_suffix(m.group(1))
            if token not in names and token not in extra:
                extra.append(token)
    for token in extra:
        _add(token)
        # MI_CatBed_01_A → also try SM_/SK_ equivalents for mesh JSON lookup
        if token.upper().startswith("MI_"):
            body = token[3:]
            _add("SM_" + body)
            _add("SK_" + body)
            _add(body)
        elif token.upper().startswith("SM_") or token.upper().startswith("SK_"):
            body = token[3:]
            _add("MI_" + body)
            _add(body)

    # Drop useless UMap actor names from priority (keep as last resort only)
    prioritized = [
        n for n in names
        if not n.lower().startswith("staticmeshcomponent")
        and not n.lower().startswith("skeletalmeshcomponent")
    ]
    leftovers = [n for n in names if n not in prioritized]
    return prioritized + leftovers


def _asset_path_from_folder(folder: str, model_name: str) -> str:
    """Find a real PSK/PSKX or a synthetic path from SK_/SM_*.json in folder."""
    if not folder or not os.path.isdir(folder):
        return ""
    want = _mesh_name_body(model_name)
    if not want:
        return ""

    psks, _ = utils.find_psks_in_folder(folder)
    for psk in psks:
        base = os.path.basename(psk)
        body = _mesh_name_body(base)
        if want == body or want in body or body in want or want in base.lower():
            return psk

    try:
        for fname in os.listdir(folder):
            fl = fname.lower()
            if not fl.endswith(".json"):
                continue
            stem = os.path.splitext(fname)[0]
            body = _mesh_name_body(stem)
            is_mesh_json = fl.startswith("sk_") or fl.startswith("sm_")
            if not is_mesh_json and body != want:
                continue
            if want == body or want in body or body in want:
                # Synthetic path so dirname + sibling JSON heuristics still work
                return os.path.join(folder, stem + ".psk")
    except OSError:
        pass
    return ""


def _fmdex_psk_for_stem(stem: str) -> str:
    """Use FMDex to resolve SM_/SK_ (or bare body) to a synthetic/real mesh path."""
    if not stem:
        return ""
    pkg, tags = fmdex.lookup_asset_path(stem)
    if not pkg:
        return ""
    tags_l = [t.lower() for t in (tags or [])]
    meshish = any(
        t in ("staticmesh", "skeletalmesh", "bodysetup")
        or "mesh" in t
        for t in tags_l
    )
    # Still try even without mesh tags — basename may be unique MI/SM sibling folder
    folder = fmdex.resolve_mesh_asset_folder(stem)
    if folder:
        hit = _asset_path_from_folder(folder, stem)
        if hit:
            return hit
        # Synthetic path from resolved JSON folder
        base = os.path.splitext(os.path.basename(pkg))[0] or stem
        for suf in (".uasset", ".umap"):
            if base.lower().endswith(suf):
                base = base[: -len(suf)]
        return os.path.join(folder, base + ".psk")
    if meshish:
        log.debug("FMDex hit for '%s' (%s) but no on-disk mesh export found", stem, pkg)
    return ""


def find_psk_for_model(model_name: str, manual_folder: str = "") -> str:
    """Find a PSK (or synthetic mesh-JSON path) matching the model name under Pioneer root."""
    model_name = _strip_blender_name_suffix(model_name)
    if not model_name:
        return ""

    if manual_folder and os.path.isdir(manual_folder):
        hit = _asset_path_from_folder(manual_folder, model_name)
        if hit:
            return hit
        for ext in [".psk", ".pskx"]:
            candidate = os.path.join(manual_folder, f"{model_name}{ext}")
            if os.path.isfile(candidate):
                return candidate
        return ""

    # FMDex-assisted path (before shallow Pioneer walks)
    fmdex_hit = _fmdex_psk_for_stem(model_name)
    if fmdex_hit:
        return fmdex_hit

    root = utils.get_pioneer_root()
    if not root:
        return ""

    search_roots = []
    for rel in (
        ["Characters", "Assets"],
        ["Items", "Firearms"],
        ["Characters", "Enemies"],
        ["Enemies"],
        ["Items"],
        ["Props"],
        ["Environment"],
    ):
        found = utils.find_relative_dir(root, rel)
        if found and found not in search_roots:
            search_roots.append(found)

    # Shallow walk: each search root's immediate children (and one level deeper for Assets)
    for base in search_roots:
        hit = _asset_path_from_folder(base, model_name)
        if hit:
            return hit
        try:
            for entry in os.listdir(base):
                sub = os.path.join(base, entry)
                if not os.path.isdir(sub):
                    continue
                hit = _asset_path_from_folder(sub, model_name)
                if hit:
                    return hit
                # Characters/Assets/<Char>/<Part>
                try:
                    for part in os.listdir(sub):
                        part_path = os.path.join(sub, part)
                        if os.path.isdir(part_path):
                            hit = _asset_path_from_folder(part_path, model_name)
                            if hit:
                                return hit
                except OSError:
                    continue
        except OSError:
            continue

    # Last resort: basename match under Content/Pioneer (bounded)
    content_dir = utils.find_content_dir(root)
    pioneer = os.path.join(content_dir, "Pioneer") if content_dir else ""
    if pioneer and os.path.isdir(pioneer):
        want = _mesh_name_body(model_name)
        raw_l = model_name.lower()
        targets = {
            f"{model_name}.psk".lower(),
            f"{model_name}.pskx".lower(),
            f"{model_name}.json".lower(),
            f"sk_{want}.psk".lower(),
            f"sk_{want}.pskx".lower(),
            f"sk_{want}.json".lower(),
            f"sm_{want}.psk".lower(),
            f"sm_{want}.pskx".lower(),
            f"sm_{want}.json".lower(),
        }
        max_visited = 40000
        visited = 0
        for walk_root, dirs, files in os.walk(pioneer):
            visited += 1
            if visited > max_visited:
                break
            low = walk_root.replace("\\", "/").lower()
            if any(skip in low for skip in ("/saved/", "/intermediate/", "/deriveddatacache/")):
                dirs[:] = []
                continue
            for fname in files:
                fl = fname.lower()
                if not (fl.endswith(".psk") or fl.endswith(".pskx") or fl.endswith(".json")):
                    continue
                if fl in targets:
                    full = os.path.join(walk_root, fname)
                    if fl.endswith(".json"):
                        stem = os.path.splitext(fname)[0]
                        return os.path.join(walk_root, stem + ".psk")
                    return full
                # Fuzzy: SK_/SM_ mesh dumps whose body matches
                if fl.startswith("sk_") or fl.startswith("sm_"):
                    body = _mesh_name_body(fname)
                    if body == want or (len(want) >= 6 and (want in body or body in want)):
                        full = os.path.join(walk_root, fname)
                        if fl.endswith(".json"):
                            stem = os.path.splitext(fname)[0]
                            return os.path.join(walk_root, stem + ".psk")
                        return full
                # Exact raw stem without prefix requirement
                stem = os.path.splitext(fname)[0].lower()
                if stem == raw_l or stem == want:
                    full = os.path.join(walk_root, fname)
                    if fl.endswith(".json"):
                        return os.path.join(walk_root, os.path.splitext(fname)[0] + ".psk")
                    return full

    return ""


def fix_materials_for_object(obj) -> tuple:
    """Resolve asset for a scene mesh and re-apply materials in place.

    Returns (ok: bool, message: str).
    """
    candidates = _name_candidates_for_object(obj)
    mat_names = [
        _strip_blender_name_suffix(s.material.name)
        for s in obj.material_slots if s.material
    ]
    fmdex.ensure_loaded()
    fmdex_st = fmdex.fmdex_summary_for_report()
    log.info(
        "Update Materials '%s' candidates=%s mats=%s %s",
        obj.name, candidates, mat_names, fmdex_st,
    )

    # Prefer path stamped at import time (reliable for old addon blends)
    psk_path = ""
    used_name = ""
    stamped = obj.get("arc_psk_path", "") if hasattr(obj, "get") else ""
    if stamped:
        stamped = _recover_saved_material_lookup(obj, bpy.path.abspath(str(stamped)))
        missing_package = str(obj.get("arc_material_source_unresolved") or "")
        if missing_package:
            return False, f"{obj.name}: missing exact game package '{missing_package}'; materials unchanged"
        folder = os.path.dirname(stamped)
        if os.path.isdir(folder):
            psk_path = stamped
            used_name = "arc_psk_path"
            log.info("Update Materials: using stamped path '%s'", psk_path)

    if not psk_path:
        for cand in candidates:
            psk_path = find_psk_for_model(cand)
            if psk_path:
                used_name = cand
                log.info("Update Materials: resolved '%s' → %s", cand, psk_path)
                break

    # FMDex: try MI stems directly for asset_folder even when mesh PSK missing
    fmdex_folder = ""
    if not psk_path:
        for cand in candidates:
            folder = fmdex.resolve_mesh_asset_folder(cand)
            if folder:
                fmdex_folder = folder
                used_name = f"fmdex:{cand}"
                log.info("Update Materials: FMDex folder for '%s' → %s", cand, folder)
                break
        if not fmdex_folder:
            for mat_name in mat_names:
                if not mat_name.upper().startswith("MI_"):
                    continue
                mi_json = fmdex.resolve_export_file(mat_name, ".json")
                if mi_json:
                    fmdex_folder = os.path.dirname(mi_json)
                    used_name = f"fmdex-mi:{mat_name}"
                    log.info(
                        "Update Materials: FMDex MI '%s' → %s", mat_name, mi_json
                    )
                    break

    if psk_path:
        skin_json = str(obj.get("arc_skin_json", "") or "") if hasattr(obj, "get") else ""
        if skin_json:
            skin_json = _recover_saved_material_lookup(obj, bpy.path.abspath(skin_json), stamp_mesh=False)
            if obj.get("arc_material_mi_unresolved"):
                return False, f"{obj.name}: missing exact selected MI '{obj.get('arc_material_mi_unresolved')}'; materials unchanged"
            if not os.path.isfile(skin_json):
                log.warning("Update Materials: stamped skin JSON missing: %s", skin_json)
                skin_json = ""
        # Backward compatibility for objects imported before arc_skin_json was persisted.
        # FModel outfit imports already carry the colourway name, so recover its exact JSON.
        if not skin_json and hasattr(obj, "get"):
            colorway = str(obj.get("arc_colorway", "") or "").strip()
            stamped_choice = str(obj.get("arc_skin_choice", "") or "").strip()
            if not colorway and stamped_choice and stamped_choice != "NONE":
                colorway = _colorway_name_from_skin_path(stamped_choice)
            if colorway:
                wanted = colorway.casefold()
                skin_candidates = textures.scan_skins(psk_path)
                matches = [
                    path for name, path in skin_candidates
                    if name.casefold() == wanted
                    or os.path.basename(os.path.dirname(path)).casefold() == wanted
                ]
                if len(matches) == 1:
                    skin_json = os.path.abspath(matches[0])
                    log.info(
                        "Update Materials: recovered colourway '%s' skin JSON: %s",
                        colorway, skin_json,
                    )
        manual_skins_folder = (
            str(obj.get("arc_manual_skins_folder", "") or "")
            if hasattr(obj, "get") else ""
        )
        if manual_skins_folder:
            manual_skins_folder = bpy.path.abspath(manual_skins_folder)
            if not os.path.isdir(manual_skins_folder):
                manual_skins_folder = ""
        if not manual_skins_folder and skin_json:
            manual_skins_folder = os.path.dirname(skin_json)
        skin_choice = (
            str(obj.get("arc_skin_choice", "NONE") or "NONE")
            if hasattr(obj, "get") else "NONE"
        )
        if (not skin_choice or skin_choice == "NONE") and skin_json:
            skin_choice = skin_json
        body_variant = (
            str(obj.get("arc_body_variant", "NONE") or "NONE")
            if hasattr(obj, "get") else "NONE"
        )
        hair_mi = (
            str(obj.get("arc_hair_mi", "NONE") or "NONE")
            if hasattr(obj, "get") else "NONE"
        )
        status = apply_materials_to_object(
            obj,
            psk_path,
            skin_json=skin_json,
            manual_skins_folder=manual_skins_folder,
            skin_choice=skin_choice,
            body_variant=body_variant,
            hair_mi=hair_mi,
        )
        folder = os.path.dirname(psk_path)
        # fix_object_materials_from_mi_slots defaults to CTX_MAP and will wipe
        # Characters/Outfits ColorMask paths as "out of context". Only use it as
        # a fallback when the primary outfit/clothing path did not wire shaders.
        leftover = 0
        outfit_done = status in (
            "clothing", "visor", "face", "body", "hair", "fur", "weapon", "enemy", "misc",
        ) or str(status).startswith("sk-slots")
        if not outfit_done:
            ctx = materials.context_from_model_type(
                str(obj.get("arc_model_type", "") or ""), psk_path
            )
            leftover = materials.fix_object_materials_from_mi_slots(
                obj, folder, context=ctx,
            )
        try:
            if os.path.isfile(psk_path):
                obj["arc_psk_path"] = psk_path
        except Exception:
            pass
        if status not in ("unresolved",):
            extra = f" +{leftover} mi-slots" if leftover else ""
            return True, (
                f"{obj.name}: {status}{extra} ← {os.path.basename(psk_path)} "
                f"(via '{used_name}'); {fmdex_st}"
            )
        if leftover:
            return True, (
                f"{obj.name}: mi-slots ({leftover}) via {os.path.basename(psk_path)}; "
                f"{fmdex_st}"
            )
        return False, (
            f"{obj.name}: found '{os.path.basename(psk_path)}' but could not wire materials "
            f"(type unresolved; mats={mat_names}; tried={candidates}; {fmdex_st})"
        )

    asset_folder = fmdex_folder or ""
    ctx = materials.context_from_model_type(
        str(obj.get("arc_model_type", "") or ""), asset_folder
    )
    fixed = materials.fix_object_materials_from_mi_slots(
        obj, asset_folder, context=ctx,
    )
    if fixed:
        via = used_name or "MaterialLibrary/FMDex"
        return True, f"{obj.name}: mi-slots ({fixed}) via {via}; {fmdex_st}"
    return False, (
        f"{obj.name}: no asset for names {candidates}; "
        f"mats={mat_names or ['(none)']}; {fmdex_st}. "
        f"See {utils.debug_log_path()}"
    )


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------
class ARC_OUTFITS_OT_SelectHairMI(Operator):
    """Select a hair MI JSON for this entry."""
    bl_idname = "arc_outfits.select_hair_mi"
    bl_label = "Select Hair MI"
    psk_path: bpy.props.StringProperty(options={'HIDDEN'})
    mi_path: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        for entry in context.scene.arc_psk_entries:
            if bpy.path.abspath(entry.psk_path) == bpy.path.abspath(self.psk_path):
                entry.hair_mi = self.mi_path
                break
        return {'FINISHED'}


class ARC_OUTFITS_OT_PickManualOutfitFolder(Operator, bpy_extras.io_utils.ImportHelper):
    """Browse to the DA_OI_Outfit folder for this character."""
    bl_idname = "arc_outfits.pick_manual_outfit_folder"
    bl_label = "Select DA_OI_Outfit Folder"
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={'HIDDEN'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN'})

    def invoke(self, context, event):
        root = utils.get_pioneer_root()
        default_dir = utils.find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit"]) if root else ""
        if default_dir:
            self.directory = default_dir.rstrip("/\\") + os.sep
            self.filepath = self.directory
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        folder = _picked_folder(self)
        if not os.path.isdir(folder):
            self.report({'ERROR'}, f"Not a valid folder: {folder}")
            return {'CANCELLED'}
        context.scene.arc_manual_outfit_folder = folder
        found = len(importing.scan_outfit_presets_in_folder(folder))
        if found:
            self.report({'INFO'}, f"Found {found} outfit colourway(s) for '{os.path.basename(folder)}'.")
        else:
            self.report(
                {'WARNING'},
                f"No usable DA_OI colourways for '{os.path.basename(folder)}' "
                "(filename may not match the JSON Name, or the dump has no material modifiers).",
            )
        importing.populate_outfit_selections(context)
        bpy.ops.arc_outfits.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OUTFITS_OT_ClearManualOutfitFolder(Operator):
    """Clear the manual DA_OI_Outfit folder override."""
    bl_idname = "arc_outfits.clear_manual_outfit_folder"
    bl_label = "Clear Outfit Folder Override"

    def execute(self, context):
        context.scene.arc_manual_outfit_folder = ""
        importing.populate_outfit_selections(context)
        bpy.ops.arc_outfits.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OUTFITS_OT_ImportSinglePSK(Operator):
    """Import a single .psk file."""
    bl_idname = "arc_outfits.import_single_psk"
    bl_label = "Import Single Model"
    bl_options = {'REGISTER', 'UNDO'}
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.psk;*.pskx;*.uemodel", options={'HIDDEN'})

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = bpy.path.abspath(getattr(context.scene, "arc_pioneer_root", "") or "") + os.sep
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        psk_path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(psk_path):
            self.report({'ERROR'}, f"File not found: {psk_path}")
            return {'CANCELLED'}
        scene = context.scene
        try:
            del scene["arc_import_folder_layout"]
        except Exception:
            pass
        scene.arc_psk_entries.clear()
        entry = scene.arc_psk_entries.add()
        entry.psk_path = psk_path
        entry.display_name = os.path.basename(psk_path)
        bpy.ops.arc_outfits.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OUTFITS_OT_ImportOutfitFolder(Operator):
    """Select a folder — PSKs are auto-discovered from subfolders."""
    bl_idname = "arc_outfits.import_outfit_folder"
    bl_label = "Import Folder"
    bl_options = {'REGISTER', 'UNDO'}
    directory: StringProperty(subtype='DIR_PATH')

    def invoke(self, context, event):
        if not self.directory:
            self.directory = bpy.path.abspath(getattr(context.scene, "arc_pioneer_root", "") or "")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        folder = bpy.path.abspath(self.directory)
        if not os.path.isdir(folder):
            self.report({'ERROR'}, f"Not a valid folder: {folder}")
            return {'CANCELLED'}
        psks = utils.find_import_models(folder)
        skipped_pskx = []
        if not psks:
            self.report({'ERROR'}, f"No .psk/.pskx/.uemodel files found in folder (or its subfolders): {folder}")
            return {'CANCELLED'}
        scene = context.scene
        scene["arc_import_folder_layout"] = os.path.abspath(folder)
        scene.arc_psk_entries.clear()
        for psk_path in psks:
            entry = scene.arc_psk_entries.add()
            entry.psk_path = psk_path
            entry.display_name = os.path.basename(psk_path)
        if skipped_pskx:
            names = ", ".join(
                f"{name} ({os.path.splitext(name)[0]}.psk preferred \u2014 has bones)"
                for name in skipped_pskx
            )
            self.report(
                {'INFO'},
                f"Skipped {names}. To import a static mesh, use 'Import Single Model'.",
            )
        self.report({'INFO'}, f"Found {len(psks)} PSK(s).")
        bpy.ops.arc_outfits.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OUTFITS_OT_AssemblePropFolder(Operator):
    """Import a prop folder and place submeshes from StaticMesh sockets + BP attach graph.

    No umap required — uses the root SM JSON sockets and (when Pioneer root is set)
    the matching Blueprint SCS (e.g. Extraction Elevator).
    """
    bl_idname = "arc_outfits.assemble_prop_folder"
    bl_label = "Assemble Prop Folder"
    bl_options = {'REGISTER', 'UNDO'}
    directory: StringProperty(subtype='DIR_PATH')

    def invoke(self, context, event):
        # Prefer folder of active mesh if it came from a prop import.
        obj = context.active_object
        if obj and obj.type == 'MESH':
            psk = str(obj.get("arc_psk_path") or "")
            if psk and os.path.isfile(psk):
                self.directory = os.path.dirname(psk) + os.sep
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from ..Maps import prop_assemble

        folder = bpy.path.abspath(self.directory)
        if not os.path.isdir(folder):
            self.report({'ERROR'}, f"Not a valid folder: {folder}")
            return {'CANCELLED'}
        pioneer = utils.get_pioneer_root() or ""
        stats = prop_assemble.assemble_extraction_elevator_folder(
            folder,
            pioneer_root=pioneer,
            import_missing=True,
        )
        if stats.get("error"):
            self.report({'ERROR'}, stats["error"])
            return {'CANCELLED'}
        self.report(
            {'INFO'},
            (
                f"Assembled prop: placed {stats['placed']} "
                f"(+{stats['duplicated']} dups), skipped {stats['skipped']}, "
                f"imported {stats['imported']}. "
                f"BP={os.path.basename(stats['bp']) or 'socket-fallback'}."
            ),
        )
        return {'FINISHED'}


class ARC_OUTFITS_OT_PickOutfitCSV(Operator, bpy_extras.io_utils.ImportHelper):
    """Browse for a different outfit_reference.csv."""
    bl_idname = "arc_outfits.pick_outfit_csv"
    bl_label = "Select Outfit CSV"
    filename_ext = ".csv"
    filter_glob: StringProperty(default="*.csv", options={'HIDDEN'})

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(path):
            self.report({'ERROR'}, f"Not a valid file: {path}")
            return {'CANCELLED'}
        context.scene.arc_outfit_csv_path = path
        from ..Outfit_Accessories.properties import rebuild_csv_outfit_map
        rebuild_csv_outfit_map(path)
        rows = importing.load_outfit_csv(path)
        self.report({'INFO'}, f"Loaded {len(rows)} row(s) from '{os.path.basename(path)}'.")
        return {'FINISHED'}


class ARC_OUTFITS_OT_ClearOutfitCSV(Operator):
    """Go back to the CSV bundled with this addon."""
    bl_idname = "arc_outfits.clear_outfit_csv"
    bl_label = "Use Bundled CSV"

    def execute(self, context):
        context.scene.arc_outfit_csv_path = ""
        from ..Outfit_Accessories.properties import rebuild_csv_outfit_map
        rebuild_csv_outfit_map("")
        return {'FINISHED'}


def _queue_selected_outfit_psks(context):
    """Queue PSK parts for ``scene.arc_selected_outfit``.

    Returns ``(ok, row, label, missing, error_msg)``. On success, populates
    ``scene.arc_psk_entries``. ``error_msg`` is set when ``ok`` is False.
    """
    scene = context.scene
    root = utils.get_pioneer_root()
    choice = scene.arc_selected_outfit
    if not choice:
        return False, None, "", [], "No outfit selected — click one in the list first."
    rows = importing.load_outfit_csv(importing.get_outfit_csv_path(context))
    row = importing.find_outfit_row(rows, choice)
    if not row:
        return False, None, "", [], "No outfit selected."
    label = row.get("Flavour") or row.get("ST") or choice
    from ..Outfit_Accessories import appearance
    if not any(appearance._shipped_outfit_preview_path(stem) for stem in appearance._outfit_preview_stems(row)):
        appearance._queue_missing_outfit_preview(context, row, allow_extract=True)
    from . import pak_extract
    if pak_extract.configured_dir(scene) or not root:
        if not pak_extract.configured_dir(scene):
            return False, row, label, [], "Set the Steam Install root or Paks folder."
        from ..Outfit_Accessories import outfit_catalog
        rels = outfit_catalog.resolve_pak_outfit_paths(scene, row)
        # A warm cache skips the exporter; apply_outfit_preset still checks each
        # mesh's material metadata against the game before building materials.
        meshes = pak_extract.ensure_rels(scene, rels, reuse_current=True) if rels else []
        if not meshes:
            if not rels:
                message = f"'{label}' has no cooked outfit meshes in this game's Pak index."
            else:
                message = f"Could not extract the {len(rels)} indexed mesh path(s) for '{label}'. See the Arc Pak log."
            return False, row, label, [], message
        scene.arc_psk_entries.clear()
        for mesh in meshes:
            entry = scene.arc_psk_entries.add()
            entry.psk_path = mesh
            entry.display_name = os.path.basename(mesh)
        return True, row, label, [], ""
    item_ui_folders = [f.strip() for f in row.get("Item/UI Folder Name", "").split(";") if f.strip()]
    if not item_ui_folders:
        return False, row, label, [], "Selected outfit has no Item/UI Folder Name to import from."
    psks, missing = importing.collect_psks_for_outfit_row(root, item_ui_folders)
    if not psks:
        model_folder = (row.get("Model Folder Name") or "").strip()
        psks, missing = importing.collect_psks_from_model_folder(root, model_folder)
    if not psks:
        hint = ""
        if missing:
            sample = "; ".join(missing[:4])
            extra = f" (+{len(missing) - 4} more)" if len(missing) > 4 else ""
            hint = f" Missing: {sample}{extra}."
        return (
            False,
            row,
            label,
            missing,
            "No PSK files found for this outfit's parts." + hint
            + " If folders only have .uemodel, re-export meshes as ActorX (.psk) in FModel.",
        )
    scene.arc_psk_entries.clear()
    for psk_path in psks:
        entry = scene.arc_psk_entries.add()
        entry.psk_path = psk_path
        entry.display_name = os.path.basename(psk_path)
    return True, row, label, missing, ""


class ARC_OUTFITS_OT_LoadOutfit(Operator):
    """Load all PSK parts for the selected outfit from the dropdown."""
    bl_idname = "arc_outfits.load_outfit"
    bl_label = "Load Outfit"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        ok, _row, label, missing, err = _queue_selected_outfit_psks(context)
        if not ok:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        psk_count = len(context.scene.arc_psk_entries)
        if missing:
            sample = "; ".join(missing[:5])
            extra = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
            print(
                f"Arc Asset Importer: Load Outfit '{label}' queued {psk_count} "
                f"PSK(s); missing {len(missing)}: {'; '.join(missing)}"
            )
            self.report(
                {'WARNING'},
                f"Queued {psk_count} part(s) for '{label}', but {len(missing)} "
                f"lack .psk: {sample}{extra}. Re-export those meshes as ActorX "
                f"(.psk) in FModel (not UEFormat .uemodel).",
            )
        else:
            self.report({'INFO'}, f"Queued {psk_count} part(s) for '{label}'.")
        bpy.ops.arc_outfits.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OUTFITS_OT_RescanAppearance(Operator):
    """Clear body/head/hair catalog cache and rebuild enum items (kept for scripts)."""
    bl_idname = "arc_outfits.rescan_appearance"
    bl_label = "Rescan Appearance"
    bl_options = {'REGISTER'}

    def execute(self, context):
        from ..Outfit_Accessories import appearance

        appearance.invalidate_cache()
        heads = appearance.list_head_choices()
        hairs = appearance.list_hair_choices()
        body = appearance.list_body_mesh()
        packs = appearance.list_gear_choices("backpack")
        atts = appearance.list_gear_choices("attachment")
        charms = appearance.list_gear_choices("charm")
        tools = appearance.list_gear_choices("raider_tool")
        hair_ready = sum(1 for h in hairs if h.get("has_mesh"))
        hair_missing = len(hairs) - hair_ready
        self.report(
            {'INFO'},
            f"Appearance: {len(heads)} head(s), {len(hairs)} hair style(s) "
            f"({hair_ready} with mesh, {hair_missing} JSON-only), "
            f"body={'yes' if body else 'missing'}, "
            f"{len(packs)} backpack(s), {len(atts)} attachment(s), "
            f"{len(charms)} charm(s), {len(tools)} raider tool(s).",
        )
        return {'FINISHED'}


class ARC_OUTFITS_OT_DeepPathSearch(Operator, bpy_extras.io_utils.ImportHelper):
    """Consent to an expensive folder walk when the standard Pioneer layout is missing."""
    bl_idname = "arc_outfits.deep_path_search"
    bl_label = "Search Selected Folder"
    bl_description = (
        "Pick a folder to allow a deep Content/Characters search. "
        "Only runs after you confirm — known PioneerGame/Content/Pioneer "
        "paths are tried first without this"
    )
    bl_options = {'REGISTER'}

    filename_ext = ""
    filter_glob: StringProperty(default="*", options={'HIDDEN'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN'})

    def invoke(self, context, event):
        root = utils.get_pioneer_root() or ""
        if root and os.path.isdir(root):
            self.directory = root.rstrip("/\\") + os.sep
            self.filepath = self.directory
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from ..Outfit_Accessories import appearance

        folder = _picked_folder(self)
        if not folder:
            folder = bpy.path.abspath(self.filepath)
        if not os.path.isdir(folder):
            self.report({'ERROR'}, f"Not a valid folder: {folder}")
            return {'CANCELLED'}
        # Confirm via operator execute = user agreed to the expensive walk.
        granted = utils.set_deep_search_root(folder)
        chars = utils.find_relative_dir(utils.get_pioneer_root() or folder, ["Characters"])
        appearance.invalidate_cache()
        if chars:
            self.report(
                {'INFO'},
                f"Deep search enabled under '{os.path.basename(granted)}' — "
                f"found Characters at {os.path.basename(chars)}",
            )
        else:
            self.report(
                {'WARNING'},
                f"Deep search enabled under '{os.path.basename(granted)}' — "
                f"Characters still not found. Pick a closer parent folder.",
            )
        return {'FINISHED'}


class ARC_OUTFITS_OT_ClearDeepPathSearch(Operator):
    """Disable consent for expensive path discovery."""
    bl_idname = "arc_outfits.clear_deep_path_search"
    bl_label = "Clear Deep Path Search"
    bl_options = {'REGISTER'}

    def execute(self, context):
        utils.clear_deep_search_root()
        self.report({'INFO'}, "Deep path search disabled")
        return {'FINISHED'}


class ARC_OUTFITS_OT_UpdateHairColor(Operator):
    """Re-apply the selected Hair Color MI to selected hair meshes (no re-import)."""
    bl_idname = "arc_outfits.update_hair_color"
    bl_label = "Update Hair Color"
    bl_options = {'REGISTER', 'UNDO'}

    part: EnumProperty(
        name="Part",
        items=(('HAIR', "Hair", ""), ('FACIAL_HAIR', "Facial Hair", "")),
        default='HAIR',
    )

    def execute(self, context):
        from ..Outfit_Accessories import appearance as _appearance

        role = "FacialHair" if self.part == 'FACIAL_HAIR' else "Hair"
        cid = _appearance.hair_color_choice(context.scene, role)
        colour_json = _appearance.resolve_hair_color_path(cid, context.scene) if cid != "NONE" else ""
        if cid != "NONE" and (not colour_json or not os.path.isfile(colour_json)):
            self.report({'ERROR'}, f"Hair Color MI not found for '{cid}'")
            return {'CANCELLED'}

        copy_lashes = role == "Hair" and bool(
            getattr(context.scene, "arc_appearance_eyelash_copy_hair_color", True)
        )

        def _is_head_mesh(obj) -> bool:
            mtype = str(obj.get("arc_model_type", "") or "").lower()
            role = str(obj.get("arc_assembly_role", "") or "")
            name_l = (obj.name or "").lower()
            return mtype in ("face", "head") or role == "Head" or "head_face" in name_l

        targets = []
        head_targets = []
        for obj in context.selected_objects or []:
            if obj.type != "MESH":
                continue
            if _appearance.hair_object_role(obj) == role:
                targets.append(obj)
            elif _is_head_mesh(obj):
                head_targets.append(obj)
        if not targets and not (copy_lashes and head_targets):
            self.report(
                {'ERROR'},
                "Select hair mesh(es), or a head when Eyelash copy Hair Color is on",
            )
            return {'CANCELLED'}

        done = 0
        lash_done = 0
        for obj in targets:
            hair_json = str(obj.get("arc_hair_mi", "") or obj.get("arc_skin_json", "") or "")
            if not hair_json or hair_json == "NONE" or not os.path.isfile(hair_json):
                psk = str(obj.get("arc_psk_path", "") or "")
                if psk and os.path.isfile(psk):
                    mis = textures.scan_hair_mis(psk)
                    hair_json = mis[0][1] if mis else ""
            if not hair_json or not os.path.isfile(hair_json):
                print(f"Arc Raiders: Update Hair Color skipped '{obj.name}' (no hair MI)")
                continue
            try:
                if colour_json:
                    obj["arc_hair_color_mi"] = os.path.abspath(colour_json)
                elif "arc_hair_color_mi" in obj:
                    del obj["arc_hair_color_mi"]
                obj["arc_hair_mi"] = os.path.abspath(hair_json)
            except Exception:
                pass
            _appearance.isolate_appearance_materials(obj)
            materials.setup_hair_material(obj, hair_json, colour_json=colour_json)
            done += 1

        # Tint eyelash materials on selected / paired heads when the checkbox is on.
        from .materials import character as _character

        if copy_lashes:
            if not head_targets:
                for hair in targets:
                    slot = hair.get("arc_import_slot")
                    for other in bpy.data.objects:
                        if other.type != "MESH" or other in head_targets:
                            continue
                        if slot is not None and other.get("arc_import_slot") != slot:
                            continue
                        if slot is None:
                            armature = hair.find_armature()
                            if armature is not None:
                                if other.find_armature() != armature:
                                    continue
                            elif hair.parent is None or other.parent != hair.parent:
                                continue
                        if _is_head_mesh(other):
                            head_targets.append(other)
            for head in head_targets:
                _appearance.isolate_appearance_materials(head)
                try:
                    if colour_json:
                        head["arc_hair_color_mi"] = os.path.abspath(colour_json)
                    elif "arc_hair_color_mi" in head:
                        del head["arc_hair_color_mi"]
                except Exception:
                    pass
                for slot in head.material_slots:
                    mat = slot.material
                    if mat is None:
                        continue
                    role = str(mat.get("arc_head_role") or "")
                    if role != "eyelashes" and "eyelash" not in (mat.name or "").lower():
                        continue
                    if _character._apply_hair_color_to_eyelash_material(
                        mat, colour_json=colour_json, use_hair_color=True,
                    ):
                        lash_done += 1

        if copy_lashes:
            for head in head_targets:
                try:
                    _character.sync_face_shader_hair(
                        "",
                        _character._resolve_scene_hair_color_rgba(colour_json),
                        head_obj=head,
                    )
                except Exception:
                    pass

        if done <= 0 and lash_done <= 0:
            self.report({'ERROR'}, "No hair MI / eyelash mats found on selection")
            return {'CANCELLED'}
        msg = f"Updated hair color on {done} mesh(es)"
        if lash_done:
            msg += f", {lash_done} eyelash mat(s)"
        elif copy_lashes and head_targets:
            msg += " (no eyelash mats on paired head)"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class ARC_OUTFITS_OT_AddAppearancePart(Operator):
    """Import one appearance part (Hair / Head / Body / gear), optionally onto the selected outfit."""
    bl_idname = "arc_outfits.add_appearance_part"
    bl_label = "Add Appearance Part"
    bl_options = {'REGISTER', 'UNDO'}

    part: EnumProperty(
        name="Part",
        items=(
            ('HAIR', "Hair", "Import the selected hair style"),
            ('FACIAL_HAIR', "Facial Hair", "Import the selected facial hair style"),
            ('HEAD', "Head", "Import the selected head"),
            ('BODY', "Body", "Import the shared body mesh"),
            ('BACKPACK', "Backpack", "Import the selected backpack"),
            ('ATTACHMENT', "Attachment", "Import the selected backpack attachment"),
            ('CHARM', "Charm", "Import the selected backpack charm"),
            ('RAIDER_TOOL', "Raider Tool", "Import the selected raider tool"),
        ),
        default='HAIR',
    )

    def execute(self, context):
        from ..Outfit_Accessories import appearance

        from . import pak_extract

        if not utils.get_pioneer_root() and not pak_extract.configured_dir(context.scene):
            self.report({'ERROR'}, "Set PioneerGame Folder or the Paks folder in Settings")
            return {'CANCELLED'}

        role = {
            "HAIR": "Hair",
            "FACIAL_HAIR": "FacialHair",
            "HEAD": "Head",
            "BODY": "Body",
            "BACKPACK": "Backpack",
            "ATTACHMENT": "Attachment",
            "CHARM": "Charm",
            "RAIDER_TOOL": "RaiderTool",
        }.get(self.part, "Hair")
        replace = bool(getattr(context.scene, "arc_appearance_replace_selected", False))
        attach = bool(getattr(context.scene, "arc_appearance_attach_to_outfit", False))
        attach_to = None
        # Replace also needs the pad armature so we can find the old part on it.
        if attach or replace:
            attach_to = appearance.find_target_armature(context)
            if attach and attach_to is None:
                self.report(
                    {'ERROR'},
                    "Select an outfit armature (or a mesh skinned to one) first",
                )
                return {'CANCELLED'}
        # Only merge when Add to Selected Outfit is on (replace may only swap).
        merge_target = attach_to if attach else None

        def _finish(op, ctx, result):
            count, msgs, objs = result
            for m in msgs:
                print(f"Arc Asset Importer: {m}")
            if count <= 0:
                op.report({'ERROR'}, "; ".join(msgs[:3]) or f"Nothing imported ({role})")
                return
            if role in ("Body", "Head", "Hair", "FacialHair", "Backpack", "Attachment", "Charm", "RaiderTool"):
                _maybe_import_references(ctx, objs, roles={role})
            verb = "Replaced" if replace else "Added" if attach_to is not None else "Imported"
            if attach_to is not None:
                op.report({'INFO'}, f"{verb} {role} on '{attach_to.name}'. " + "; ".join(msgs[:4]))
            else:
                op.report({'INFO'}, f"{verb} {role}. " + "; ".join(msgs[:4]))

        return _start_staged_generator(
            self, context,
            lambda ctx: appearance.iter_appearance_parts(
                ctx, roles={role}, attach_to=merge_target, replace_selected=replace,
            ),
            _finish,
        )

class ARC_OUTFITS_OT_UpdateBackpackColorway(Operator):
    """Apply an authored color option to selected backpack assemblies."""
    bl_idname = "arc_outfits.update_backpack_colorway"
    bl_label = "Update Backpack Colorway"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from ..Outfit_Accessories import appearance
        targets = set()
        for selected in context.selected_objects:
            candidates = [selected] + list(selected.children_recursive)
            targets.update(obj for obj in candidates if obj.type == 'MESH' and obj.get('arc_assembly_role') == 'Backpack')
        if not targets:
            self.report({'ERROR'}, "Select backpack meshes or their armature")
            return {'CANCELLED'}
        colorway = getattr(context.scene, 'arc_appearance_backpack_colorway', 'BASE')
        for obj in targets:
            choice = obj.get('arc_backpack_choice')
            if choice and colorway not in {row['id'] for row in appearance.list_backpack_colorways(choice)}:
                self.report({'ERROR'}, f"Colorway '{colorway}' is unavailable for '{obj.name}'")
                return {'CANCELLED'}
        try:
            messages, added = appearance.repair_backpack_assemblies(context, list(targets), add_missing_parts=True, colorway_id=colorway)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        for message in messages:
            print('Arc Raiders: ' + message)
        self.report({'INFO'}, f"Updated selected backpack colorways; {len(added)} missing objects added")
        return {'FINISHED'}


class ARC_OUTFITS_OT_ImportReference(Operator):
    """Place UI reference image planes for the selected outfit / head / hair."""
    bl_idname = "arc_outfits.import_reference"
    bl_label = "Import Reference"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from ..Outfit_Accessories import reference_planes

        made, msgs = reference_planes.import_references_from_scene(context)
        for m in msgs:
            print(f"Arc Asset Importer: {m}")
        if made <= 0:
            self.report({'ERROR'}, "; ".join(msgs[:3]) or "No reference planes created")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Created {made} reference plane(s). " + "; ".join(msgs[:4]))
        return {'FINISHED'}


class ARC_OUTFITS_OT_ImportColorBenchmark(Operator):
    """Import curated B1–B8 body parts with a flat inspection material (no ArcTexturer)."""
    bl_idname = "arc_outfits.import_color_benchmark"
    bl_label = "Import Color Benchmark"
    bl_description = (
        "Import OUTFIT_COLOR_BENCHMARK body parts with all color nodes exposed "
        "(no ArcTexturer). Nodes are auto-laid out without overlaps for reading "
        "scheme / assemble / overlay algebra"
    )
    bl_options = {'REGISTER', 'UNDO'}

    benchmark_id: StringProperty(
        name="Benchmark",
        description="B1–B8, or ALL",
        default="ALL",
    )

    def execute(self, context):
        from .materials import benchmark_graph as bg

        from . import pak_extract

        root = utils.get_pioneer_root()
        if (not root or not os.path.isdir(root)) and not pak_extract.configured_dir(context.scene):
            self.report({'ERROR'}, "Set PioneerGame Folder or the Paks folder in Settings")
            return {'CANCELLED'}

        bid = (self.benchmark_id or "ALL").strip().upper()
        if bid in ("ALL", "*"):
            ok_n, msgs = bg.import_benchmarks(context, list(bg.BENCHMARK_IDS))
            for m in msgs:
                print(f"Arc Asset Importer: {m}")
            if ok_n <= 0:
                self.report({'ERROR'}, "; ".join(msgs[:3]) or "No benchmarks imported")
                return {'CANCELLED'}
            self.report(
                {'INFO'},
                f"Imported {ok_n}/{len(bg.BENCHMARK_IDS)} color benchmarks (inspection graphs).",
            )
            return {'FINISHED'}

        if bid not in bg.COLOR_BENCHMARKS:
            self.report({'ERROR'}, f"Unknown benchmark '{bid}' (use B1–B8 or ALL)")
            return {'CANCELLED'}
        ok, msg, _ = bg.import_benchmark(context, bid)
        if not ok:
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def _start_staged_import(op, context, presets, finish):
    """Start the staged (non-blocking) batch import as a modal operator.

    The caller returns CANCELLED so only the modal run pushes one undo step.
    """
    from ..Maps import staged_import as _staged
    from . import import_view as _import_view
    _import_view.prepare_outfit_import(context, frame=True)
    _staged.queue_job(presets, finish)
    try:
        result = bpy.ops.arc_outfits.staged_import('INVOKE_DEFAULT')
    except Exception as exc:
        op.report({'ERROR'}, f"Outfit import could not start: {exc}")
        return {'CANCELLED'}
    if 'RUNNING_MODAL' not in result:
        op.report({'WARNING'}, "Outfit import did not start.")
    return {'CANCELLED'}


def _start_staged_generator(op, context, make_gen, finish, *, frame=False):
    """Start a step generator (mesh step, then material steps) as the staged modal run.

    ``make_gen(context)`` builds the generator; ``finish(op, context, result)`` runs on success.
    The caller returns CANCELLED so only the modal run pushes one undo step.
    """
    from ..Maps import staged_import as _staged
    from . import import_view as _import_view
    _import_view.prepare_outfit_import(context, frame=frame)
    _staged.queue_generator(make_gen, finish)
    try:
        result = bpy.ops.arc_outfits.staged_import('INVOKE_DEFAULT')
    except Exception as exc:
        _staged._PENDING_GEN = None
        op.report({'ERROR'}, f"Import could not start: {exc}")
        return {'CANCELLED'}
    if 'RUNNING_MODAL' not in result:
        op.report({'WARNING'}, "Import did not start.")
    return {'CANCELLED'}


def _finish_load_all_colorways(op, context, label, result):
    instances, parts, _perf_summary = result
    context.scene.arc_psk_entries.clear()
    context.scene.arc_outfit_selections.clear()
    if instances <= 0:
        op.report({'WARNING'}, f"No colourways imported for '{label}'.")
        return {'CANCELLED'}
    op.report(
        {'INFO'},
        f"Imported {instances} colourway(s) ({parts} part(s)) for '{label}'.",
    )
    return {'FINISHED'}


def _finish_selector_batch(op, context, result):
    instances, parts, _perf_summary = result
    op.report(
        {'INFO'},
        f"Arc Raiders: batch imported {instances} instance(s) ({parts} part(s) total).",
    )
    # Perf drill-down reports disabled in final builds (see utils._PERF_LOGGING_ENABLED).
    context.scene.arc_psk_entries.clear()
    context.scene.arc_outfit_selections.clear()
    return {'FINISHED'}


class ARC_OUTFITS_OT_LoadAllColorways(Operator):
    """Import every colourway for the selected outfit via batch import."""
    bl_idname = "arc_outfits.load_all_colorways"
    bl_label = "Load All Colorways"
    bl_description = "Import all colourways for the selected outfit"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        choice = getattr(scene, "arc_selected_outfit", "") or ""
        if not choice:
            return False
        rows = importing.load_outfit_csv(importing.get_outfit_csv_path(context))
        row = importing.find_outfit_row(rows, choice)
        if not row:
            return False
        # poll runs on every sidebar redraw: no folder scan and no Pak extraction here.
        # execute reports a missing colourway folder.
        if not utils.get_pioneer_root():
            from . import pak_extract
            if not pak_extract.configured_dir(scene):
                return False
        return bool((row.get("Item/UI Folder Name") or row.get("Model Folder Name") or "").strip())

    def execute(self, context):
        ok, row, label, missing, err = _queue_selected_outfit_psks(context)
        if not ok:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        root = utils.get_pioneer_root()
        presets = importing.colorways_for_outfit_row(root, row) if row else []
        if not presets:
            # Fallback: same discovery as the confirm-dialog batch UI.
            importing.populate_outfit_selections(context)
            presets = [
                (s.preset_name, bpy.path.abspath(s.json_path))
                for s in context.scene.arc_outfit_selections
            ]
        if not presets:
            context.scene.arc_psk_entries.clear()
            context.scene.arc_outfit_selections.clear()
            self.report({'ERROR'}, f"No colourways found for '{label}'.")
            return {'CANCELLED'}

        if missing:
            print(
                f"Arc Asset Importer: Load All Colorways '{label}' — "
                f"{len(presets)} colourway(s), {len(missing)} missing part(s)"
            )

        def _finish(op, ctx, result):
            return _finish_load_all_colorways(op, ctx, label, result)

        return _start_staged_import(self, context, presets, _finish)


_ALL_OUTFITS_COLORWAYS_ACTIVE = None


def all_outfits_colorways_status():
    active = _ALL_OUTFITS_COLORWAYS_ACTIVE
    if active is None or active._closed:
        return None
    return {
        'phase': active._phase,
        'current': active._current_label,
        'done': active._position,
        'total': len(active._jobs),
        'imported': active._imported,
        'failed': len(active._failures),
        'audit_done': active._audit_position,
        'audit_total': len(active._audit_objects),
    }


def _batch_mesh_material_profile(obj):
    """Profile reachable shader inputs and identify bare generic materials."""
    profile = {'materials': [], 'has_texture': False, 'has_group': False,
               'has_procedural': False, 'generic': False, 'missing': False,
               'untextured': False, 'missing_image_files': []}
    slots = list(getattr(obj, 'material_slots', ()))
    used_indices = {poly.material_index for poly in getattr(obj.data, 'polygons', ())}
    # Imported meshes can carry trailing empty PSK material slots that no face
    # references. Treat only face-assigned slots as missing/untextured; keep all
    # slots for meshes without polygons so malformed empty meshes remain visible.
    if not used_indices:
        used_indices = set(range(len(slots)))
    mats = [slot.material for index, slot in enumerate(slots) if index in used_indices]
    if not mats or not any(mats):
        profile['missing'] = True
        profile['generic'] = True
        profile['untextured'] = True
        return profile

    for mat in mats:
        if mat is None:
            profile['missing'] = True
            continue
        types, groups, images, procedures, links = [], [], [], [], []
        image_paths, missing_image_files = [], []
        reachable = {}
        visited_nodes = set()

        def trace_inputs(tree, node):
            """Walk only nodes that can feed a material output (including nested groups)."""
            if not tree or not node:
                return
            key = (tree.as_pointer(), node.as_pointer())
            if key in visited_nodes:
                return
            visited_nodes.add(key)
            reachable.setdefault(tree.as_pointer(), {})[node.as_pointer()] = node
            if node.type == 'GROUP' and node.node_tree:
                groups.append(node.node_tree.name)
                for group_output in node.node_tree.nodes:
                    if group_output.type == 'GROUP_OUTPUT' and getattr(group_output, 'is_active_output', True):
                        for socket in group_output.inputs:
                            for link in socket.links:
                                trace_inputs(node.node_tree, link.from_node)
            for socket in node.inputs:
                for link in socket.links:
                    links.append((link.from_node.bl_idname, node.bl_idname,
                                  link.from_socket.name, link.to_socket.name))
                    trace_inputs(tree, link.from_node)

        if mat.use_nodes and mat.node_tree:
            output_nodes = [node for node in mat.node_tree.nodes
                            if node.type == 'OUTPUT_MATERIAL' and getattr(node, 'is_active_output', True)]
            for output_node in output_nodes:
                for socket in output_node.inputs:
                    if socket.name in {'Surface', 'Volume', 'Displacement'}:
                        for link in socket.links:
                            trace_inputs(mat.node_tree, link.from_node)
            for tree_nodes in reachable.values():
                for node in tree_nodes.values():
                    types.append(node.bl_idname)
                    if node.type == 'TEX_IMAGE':
                        image = node.image
                        if image:
                            images.append(image.name)
                            path = bpy.path.abspath(image.filepath, library=image.library) if image.filepath else ''
                            image_paths.append(path)
                            if (path and not image.packed_file and not image.is_dirty
                                    and not os.path.isfile(path)):
                                missing_image_files.append({'image': image.name, 'path': path})
                        else:
                            missing_image_files.append({'image': node.name, 'path': '', 'reason': 'image node has no image'})
                    elif (node.type.startswith('TEX_') and node.type not in {'TEX_COORD'}) or node.type in {
                        'NOISE_TEXTURE', 'VORONOI_TEXTURE', 'MUSGRAVE_TEXTURE',
                        'WAVE_TEXTURE', 'GRADIENT_TEXTURE', 'WHITE_NOISE_TEXTURE',
                    }:
                        procedures.append(node.bl_idname)
        profile['has_texture'] |= bool(images)
        profile['has_group'] |= bool(groups)
        profile['has_procedural'] |= bool(procedures)
        material_untextured = not bool(images)
        profile['untextured'] |= material_untextured
        profile['missing_image_files'].extend(missing_image_files)
        generic = not images and not groups and not procedures and (
            'ShaderNodeBsdfPrincipled' in types
        )
        profile['generic'] |= generic
        family = str(mat.get('arc_material_family') or mat.get('arc_shader_family')
                      or mat.get('arc_mi_family') or '')
        if family == 'simple':
            family = 'SimplePBR authored shader'
        if not family:
            material_name = mat.name.lower()
            group_names = [name.lower() for name in set(groups)]
            if any('crt' in name or 'screen' in name for name in (material_name, *group_names)):
                family = 'CRT/display'
            elif any('fur' in name or 'hair' in name for name in (material_name, *group_names)):
                family = 'fur/hair'
            elif any('gt_colour' in name or 'arc texturer' in name for name in group_names):
                family = 'ArcTexturer layered surface'
            elif groups:
                family = 'authored node-group shader'
            elif procedures:
                family = 'procedural shader'
            elif images:
                family = 'image-textured shader'
            elif generic:
                family = 'generic Principled fallback'
            else:
                family = 'image-less authored shader'
        profile['materials'].append({
            'name': mat.name,
            'images': sorted(set(images)),
            'image_paths': sorted(set(image_paths)),
            'missing_image_files': missing_image_files,
            'groups': sorted(set(groups)),
            'node_types': sorted(types),
            'procedural': sorted(set(procedures)),
            'links': sorted(set(links)),
            'generic_principled': generic,
            'untextured': material_untextured,
            'material_status': str(mat.get('arc_material_status') or ''),
            'family': family,
        })
    return profile


class ARC_OUTFITS_OT_LoadAllOutfitsColorways(Operator, ExportHelper):
    """Import all outfit rows, finishing every colourway of one outfit before the next."""
    bl_idname = 'arc_outfits.load_all_outfits_colorways'
    bl_label = 'Load All Outfits & Colorways'
    bl_description = 'Import every outfit and all its colourways sequentially, checkpointing every five outfits'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.blend'
    filter_glob: StringProperty(default='*.blend', options={'HIDDEN'})
    filepath: StringProperty(subtype='FILE_PATH')

    @classmethod
    def poll(cls, context):
        return _ALL_OUTFITS_COLORWAYS_ACTIVE is None

    def invoke(self, context, event):
        if bpy.data.filepath:
            self.filepath = bpy.data.filepath
            return self.execute(context)
        # An unsaved scene must get a real Save As path before the first import.
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        global _ALL_OUTFITS_COLORWAYS_ACTIVE
        from ..Outfit_Accessories import appearance, properties; from . import importing, pak_extract, utils
        scene = context.scene
        game = pak_extract.configured_dir(scene)
        if not game and not utils.get_pioneer_root():
            self.report({'ERROR'}, 'Set the Steam Install or FModel Output folder before importing all outfits.')
            return {'CANCELLED'}

        rows = importing.load_outfit_csv(importing.get_outfit_csv_path(context))
        by_key = {importing.outfit_row_key(row): row for row in rows if importing.outfit_row_key(row)}
        jobs = []
        for item in properties.make_outfit_selector_items(scene, context):
            key = item[0]
            row = by_key.get(key)
            if not row or key == 'NONE':
                continue
            jobs.append({'key': key, 'label': item[1], 'row': row})
        if not jobs:
            self.report({'ERROR'}, 'No outfit entries were found in the Outfit Selector catalog.')
            return {'CANCELLED'}

        filepath = bpy.path.abspath(self.filepath or bpy.data.filepath or '')
        if not filepath or not filepath.lower().endswith('.blend'):
            self.report({'ERROR'}, 'Choose a .blend checkpoint path before starting the all-outfits import.')
            return {'CANCELLED'}
        current = os.path.normcase(os.path.abspath(bpy.data.filepath)) if bpy.data.filepath else ''
        target = os.path.normcase(os.path.abspath(filepath))
        if not current and os.path.exists(filepath):
            self.report({'ERROR'}, 'The selected checkpoint already exists. Choose a new filename to protect existing work.')
            return {'CANCELLED'}
        if current and current != target:
            self.report({'ERROR'}, 'Batch imports save to the current blend. Use Save As first to choose its checkpoint path.')
            return {'CANCELLED'}
        os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)
        if not current:
            result = bpy.ops.wm.save_as_mainfile(filepath=filepath, check_existing=False)
            if 'FINISHED' not in result:
                self.report({'ERROR'}, f'Could not create the first checkpoint at {filepath}')
                return {'CANCELLED'}

        self._scene = scene
        self._window = context.window
        self._view_layer = context.view_layer
        self._source_state = tuple(str(getattr(scene, name, '') or '') for name in (
            'arc_pak_dir', 'arc_usmap_path', 'arc_pioneer_root', 'arc_outfit_csv_path'))
        self._filepath = filepath
        self._report_path = os.path.splitext(filepath)[0] + '.all-outfits-progress.json'
        self._original_outfit = str(getattr(scene, 'arc_selected_outfit', '') or '')
        self._original_browse = str(getattr(scene, 'arc_selected_outfit_browse', '') or '')
        self._saved_entries = [
            {name: getattr(entry, name, '') for name in (
                'psk_path', 'display_name', 'skin_choice', 'manual_skins_folder',
                'body_choice', 'hair_mi')}
            for entry in scene.arc_psk_entries
        ]
        self._saved_selections = [
            {name: getattr(entry, name, '') for name in ('preset_name', 'json_path', 'selected')}
            for entry in scene.arc_outfit_selections
        ]
        already = {str(obj.get('arc_batch_outfit_key') or '') for obj in scene.objects}
        self._skipped_existing = [job['label'] for job in jobs if job['key'] in already]
        self._jobs = [job for job in jobs if job['key'] not in already]
        self._position = 0
        self._imported = 0
        self._failures = []
        self._records = []
        self._current_label = 'Starting'
        self._phase = 'Importing outfits' if self._jobs else 'Auditing and repairing materials'
        self._audit_objects = []
        self._audit_position = 0
        self._audit_before = []
        self._audit_after = []
        self._material_setups = {}
        self._cancel_requested = False
        self._closed = False
        self._timer = None
        self._wm = context.window_manager
        self._origin_filepath = current
        _ALL_OUTFITS_COLORWAYS_ACTIVE = self
        self._wm.progress_begin(0, max(1, len(self._jobs)))
        if self._jobs:
            self._persist_report('running')
        else:
            self._begin_audit()

        if bpy.app.background:
            while self._phase == 'Importing outfits' and self._position < len(self._jobs):
                self._import_one(context)
            self._begin_audit()
            while self._phase == 'Auditing and repairing materials' and self._audit_position < len(self._audit_objects):
                self._audit_one()
            self._finish(context)
            return {'FINISHED'}

        self._timer = self._wm.event_timer_add(0.1, window=context.window)
        self._wm.modal_handler_add(self)
        self._redraw()
        return {'RUNNING_MODAL'}

    def _write_json(self, payload):
        temp = self._report_path + '.tmp'
        with open(temp, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        os.replace(temp, self._report_path)

    def _persist_report(self, status):
        report = {
            'status': status, 'phase': self._phase, 'processed_outfits': self._position,
            'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'total_outfits': len(self._jobs), 'imported_outfits': self._imported,
            'skipped_existing': list(self._skipped_existing), 'failures': list(self._failures),
            'items': list(self._records), 'untextured_before_repair': list(self._audit_before),
            'untextured_after_repair': list(self._audit_after),
            'unique_material_setups': list(self._material_setups.values()),
            'audit_done': self._audit_position,
            'audit_total': len(self._audit_objects),
            'audit_current': self._current_label if self._phase == 'Auditing and repairing materials' else '',
            'blend': self._filepath, 'report': self._report_path,
            'current': self._current_label,
        }
        summary = {
            'status': status, 'phase': self._phase, 'processed_outfits': self._position,
            'total_outfits': len(self._jobs), 'imported_outfits': self._imported,
            'failed_outfits': len(self._failures), 'current': self._current_label,
            'unresolved_materials': len(self._audit_after),
            'unique_material_setups': len(self._material_setups),
            'audit_done': self._audit_position,
            'audit_total': len(self._audit_objects),
            'report': self._report_path,
        }
        self._scene['arc_all_outfits_colorways_progress'] = json.dumps(summary, ensure_ascii=False)
        self._write_json(report)

    def _persist_audit_progress(self):
        """Write a compact heartbeat without serializing the full material inventory."""
        summary = {
            'status': 'auditing',
            'phase': self._phase,
            'audit_done': self._audit_position,
            'audit_total': len(self._audit_objects),
            'current': self._current_label,
            'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'blend': self._filepath,
        }
        self._scene['arc_all_outfits_colorways_progress'] = json.dumps(summary, ensure_ascii=False)
        progress_path = os.path.splitext(self._report_path)[0] + '.audit-progress.json'
        temp = progress_path + '.tmp'
        with open(temp, 'w', encoding='utf-8') as stream:
            json.dump(summary, stream, ensure_ascii=False)
        os.replace(temp, progress_path)

    def _save_checkpoint(self, reason):
        self._scene['arc_all_outfits_colorways_checkpoint'] = reason
        result = bpy.ops.wm.save_as_mainfile(filepath=self._filepath, check_existing=False)
        if 'FINISHED' not in result:
            raise RuntimeError(f'Blender did not save the checkpoint ({result})')
        print(f"Arc Raiders all outfits: checkpoint saved after {self._position} outfit(s): {self._filepath}")

    def _redraw(self):
        self._wm.progress_update(min(self._position, len(self._jobs)))
        if self._window:
            for area in self._window.screen.areas:
                if area.type in {'VIEW_3D', 'PROPERTIES'}:
                    area.tag_redraw()

    _job_state = None   # one outfit in progress: generator plus what its record needs

    def _import_one(self, context, budget=None):
        """Advance the current outfit. ``budget=None`` runs it to the end (background mode);
        a number of seconds runs steps until that time is spent, so the window keeps drawing."""
        from . import importing, utils
        from ..Maps import staged_import as _staged
        job = self._jobs[self._position]
        scene = self._scene
        state = self._job_state
        try:
            if state is None:
                self._current_label = job['label']
                self._phase = f"Importing outfit {self._position + 1}/{len(self._jobs)}"
                state = self._job_state = {
                    'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                    'started_clock': time.monotonic(),
                }
                self._persist_report('running')
                scene.arc_selected_outfit = job['key']
                ok, row, label, missing, error = _queue_selected_outfit_psks(context)
                if not ok:
                    raise RuntimeError(error)
                root = utils.get_pioneer_root()
                presets = importing.colorways_for_outfit_row(root, row)
                if not presets:
                    importing.populate_outfit_selections(context)
                    presets = [(sel.preset_name, bpy.path.abspath(sel.json_path))
                               for sel in scene.arc_outfit_selections if sel.json_path]
                base_only = not bool(presets)
                if base_only:
                    presets = [(f"{label} - Base", "")]
                state.update(missing=missing, presets=presets, base_only=base_only,
                             before=set(bpy.data.objects),
                             gen=iter_batch_import_instances(context, presets))
            started_at = state['started_at']
            started_clock = state['started_clock']
            missing, presets, base_only = state['missing'], state['presets'], state['base_only']
            tick = time.perf_counter()
            try:
                while True:
                    event = next(state['gen'])
                    if budget is not None and time.perf_counter() - tick >= budget:
                        stage, index, total, piece = event
                        self._current_label = f"{job['label']}: " + _staged.progress_text(
                            stage, index, total, piece)
                        self._redraw()
                        return
            except StopIteration as stop:
                instances, parts, perf = stop.value
            created = [obj for obj in bpy.data.objects if obj not in state['before']]
            if instances <= 0:
                raise RuntimeError('No colourway instances completed; ' + (perf or 'import returned zero instances'))
            preset_names = [str(name) for name, _path in presets]
            for obj in created:
                obj['arc_batch_outfit_key'] = job['key']
                obj['arc_batch_outfit_label'] = job['label']
                colors = [coll.name for coll in obj.users_collection
                          if any(coll.name == name or coll.name.startswith(name + '.') for name in preset_names)]
                if colors:
                    obj['arc_batch_colorway'] = colors[0]
            self._imported += 1
            record = {'key': job['key'], 'label': job['label'], 'ok': True,
                      'started_at': started_at,
                      'completed_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                      'duration_seconds': round(time.monotonic() - started_clock, 2),
                      'colorways_requested': len(presets), 'instances_imported': instances,
                      'parts_textured': parts, 'base_only': base_only,
                      'missing_parts': list(missing), 'objects': len(created), 'perf': perf}
            self._records.append(record)
            print(f"Arc Raiders all outfits: {job['label']} — {instances}/{len(presets)} colorway instances")
        except Exception as exc:
            state = self._job_state or {}
            failure = {'key': job['key'], 'label': job['label'], 'error': str(exc)}
            failure.update({'started_at': state.get('started_at', ''),
                            'completed_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                            'duration_seconds': round(
                                time.monotonic() - state.get('started_clock', time.monotonic()), 2)})
            self._failures.append(failure)
            self._records.append({**failure, 'ok': False})
            print(f"Arc Raiders all outfits FAILED: {job['label']}: {exc}")
        self._job_state = None
        scene.arc_psk_entries.clear()
        scene.arc_outfit_selections.clear()
        self._position += 1
        self._current_label = ''
        self._persist_report('running')
        if self._position and self._position % 5 == 0:
            self._save_checkpoint('five-outfit-checkpoint')
        self._redraw()
        if self._position >= len(self._jobs):
            self._begin_audit()

    def _begin_audit(self):
        self._phase = 'Auditing and repairing materials'
        self._audit_objects = [obj for obj in self._scene.objects
                               if obj.type == 'MESH' and obj.get('arc_batch_outfit_key')]
        self._audit_position = 0
        # Make all imported outfits durable before applying any audit repairs.
        self._save_checkpoint('post-import-pre-audit')
        self._persist_report('auditing')
        print(f"Arc Raiders all outfits: auditing {len(self._audit_objects)} outfit meshes")

    def _audit_one(self):
        from . import operators as _self, materials
        obj = self._audit_objects[self._audit_position]
        before = _batch_mesh_material_profile(obj)
        item = {'name': obj.name, 'outfit': str(obj.get('arc_batch_outfit_label') or ''),
                'colorway': str(obj.get('arc_batch_colorway') or ''),
                'source': str(obj.get('arc_psk_path') or obj.get('arc_source_mesh') or ''),
                'generic_before': before['generic'], 'missing_materials_before': before['missing'],
                'untextured_before': before['untextured'],
                'missing_image_files_before': list(before['missing_image_files']),
                'materials': before['materials'], 'repair': ''}
        if before['generic'] or before['missing'] or before['untextured'] or before['missing_image_files']:
            self._audit_before.append(item)
            source = bpy.path.abspath(item['source']) if item['source'] else ''
            if (before['generic'] or before['missing'] or before['missing_image_files']) and source and os.path.isfile(source):
                skin_json = str(obj.get('arc_skin_json') or obj.get('arc_skin_choice') or '')
                skin_json = bpy.path.abspath(skin_json) if skin_json and skin_json != 'NONE' else ''
                try:
                    item['repair'] = _self.apply_materials_to_object(
                        obj, source, skin_json=skin_json,
                        skin_choice=skin_json or 'NONE',
                        manual_skins_folder=str(obj.get('arc_manual_skins_folder') or ''),
                    )
                except Exception as exc:
                    item['repair'] = f'error: {exc}'
            elif before['generic'] or before['missing'] or before['missing_image_files']:
                item['repair'] = 'source mesh path missing; automatic material rebuild unavailable'
            else:
                item['repair'] = 'no image textures found; authored shader nodes left intact for family-specific review'
            after = _batch_mesh_material_profile(obj)
            item['materials_after'] = after['materials']
            item['missing_image_files_after'] = list(after['missing_image_files'])
            item['resolved'] = not (after['generic'] or after['missing'] or after['untextured']
                                    or after['missing_image_files'])
            if not item['resolved']:
                self._audit_after.append(item)
        for material in before['materials']:
            signature = json.dumps({key: material.get(key) for key in (
                'images', 'image_paths', 'missing_image_files', 'groups', 'node_types', 'procedural', 'links',
                'generic_principled', 'untextured', 'material_status', 'family')},
                sort_keys=True, ensure_ascii=False)
            setup = self._material_setups.setdefault(signature, {
                'signature': signature, 'material_names': [], 'mesh_names': [],
                'generic_principled': material.get('generic_principled', False),
                'untextured': material.get('untextured', False),
                'family': material.get('family', ''), 'node_types': material.get('node_types', []),
                'images': material.get('images', []), 'image_paths': material.get('image_paths', []),
                'missing_image_files': material.get('missing_image_files', []),
                'groups': material.get('groups', []),
                'procedural': material.get('procedural', []), 'links': material.get('links', []),
            })
            if material['name'] not in setup['material_names']:
                setup['material_names'].append(material['name'])
            if obj.name not in setup['mesh_names']:
                setup['mesh_names'].append(obj.name)
        self._audit_position += 1
        self._current_label = obj.name
        # Keep external progress visible without rewriting a potentially large JSON
        # report after every mesh; the active UI still shows per-mesh progress.
        if self._audit_position % 25 == 0 or self._audit_position == len(self._audit_objects):
            self._persist_audit_progress()
        self._redraw()

    def _finish(self, context, error=''):
        global _ALL_OUTFITS_COLORWAYS_ACTIVE
        if self._closed:
            return
        self._closed = True
        state, self._job_state = self._job_state, None
        if state and state.get('gen') is not None:
            # Stopped mid-outfit: close the generator so its finally blocks run.
            try:
                state['gen'].close()
            except Exception:
                pass
        if self._timer:
            self._wm.event_timer_remove(self._timer)
        self._wm.progress_end()
        scene = self._scene
        try:
            scene.arc_selected_outfit = self._original_outfit
            if self._original_browse:
                scene.arc_selected_outfit_browse = self._original_browse
            scene.arc_psk_entries.clear()
            for saved in self._saved_entries:
                entry = scene.arc_psk_entries.add()
                for name, value in saved.items():
                    if hasattr(entry, name):
                        setattr(entry, name, value)
            scene.arc_outfit_selections.clear()
            for saved in self._saved_selections:
                entry = scene.arc_outfit_selections.add()
                for name, value in saved.items():
                    if hasattr(entry, name):
                        setattr(entry, name, value)
        except (ReferenceError, RuntimeError, TypeError):
            pass
        self._phase = 'Finished'
        self._current_label = ''
        try:
            self._persist_report('failed' if error else ('cancelled' if self._cancel_requested else 'complete'))
            self._save_checkpoint('final-material-audit' if self._audit_position else 'final-batch-checkpoint')
        except Exception as exc:
            error = error or f'Final checkpoint failed: {exc}'
        _ALL_OUTFITS_COLORWAYS_ACTIVE = None
        self._redraw()
        failed = len(self._failures)
        message = f"Imported {self._imported} outfit(s); {failed} failed. Final material audit: {len(self._audit_after)} unresolved generic/untextured mesh(es)."
        if error:
            message += ' ' + error
        self.report({'WARNING'} if error or failed or self._audit_after else {'INFO'}, message)

    def modal(self, context, event):
        if self._closed:
            return {'CANCELLED'}
        if event.type == 'ESC':
            self._cancel_requested = True
        if context.scene != self._scene:
            self._cancel_requested = True
        current = tuple(str(getattr(self._scene, name, '') or '') for name in (
            'arc_pak_dir', 'arc_usmap_path', 'arc_pioneer_root', 'arc_outfit_csv_path'))
        if current != self._source_state:
            self._cancel_requested = True
        if event.type == 'TIMER':
            if self._cancel_requested:
                self._finish(context)
                return {'CANCELLED'}
            try:
                if self._phase.startswith('Importing outfit'):
                    self._import_one(context, budget=0.2)
                elif self._phase == 'Auditing and repairing materials':
                    if self._audit_position < len(self._audit_objects):
                        self._audit_one()
                    else:
                        self._finish(context)
                elif self._phase == 'Finished':
                    self._finish(context)
            except Exception as exc:
                self._failures.append({'label': self._current_label, 'error': str(exc)})
                self._finish(context, error=str(exc))
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self._cancel_requested = True


class ARC_OUTFITS_OT_SelectOutfit(Operator):
    """Select an outfit from the searchable list."""
    bl_idname = "arc_outfits.select_outfit"
    bl_label = "Select Outfit"
    outfit_key: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        # Browse All reads this key through its getter, so one write is the whole change.
        if context.scene.arc_selected_outfit != self.outfit_key:
            context.scene.arc_selected_outfit = self.outfit_key
        return {'FINISHED'}


class ARC_OUTFITS_OT_ClearOutfitSearch(Operator):
    """Clear the outfit search filter."""
    bl_idname = "arc_outfits.clear_outfit_search"
    bl_label = "Clear Search"

    def execute(self, context):
        context.scene.arc_outfit_search = ""
        return {'FINISHED'}


class ARC_OUTFITS_OT_MergeSelectedArmatures(Operator):
    """Merge all selected armatures using Arc Raiders rig fix logic."""
    bl_idname = "arc_outfits.merge_selected_armatures"
    bl_label = "Merge Selected Armatures"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        armatures = [o for o in context.selected_objects if o.type == 'ARMATURE']
        if len(armatures) < 2:
            self.report({'WARNING'}, "Select at least two armatures to merge.")
            return {'CANCELLED'}
        rig.fix_rig_all(armatures, merge=True)
        self.report({'INFO'}, f"Merged {len(armatures)} armatures.")
        return {'FINISHED'}


class ARC_OUTFITS_OT_FixMaterials(Operator):
    bl_idname = "arc_outfits.fix_materials"
    bl_label = "Update Materials"
    bl_description = ""
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from . import pak_extract

        root = utils.get_pioneer_root()
        if (not root or not os.path.isdir(root)) and not pak_extract.configured_dir(context.scene):
            self.report({'ERROR'}, "Set PioneerGame Folder or the Paks folder in Settings")
            return {'CANCELLED'}

        objs = [o for o in context.selected_objects if o.type == 'MESH']
        if not objs:
            objs = [o for o in context.scene.objects if o.type == 'MESH']
        if not objs:
            self.report({'WARNING'}, "No mesh objects to fix")
            return {'CANCELLED'}

        fmdex.ensure_loaded()
        # Shared clothing mats must rebuild, not re-assign the previous session hit.
        materials.clear_clothing_material_cache()
        log.info(
            "Update Materials start: %d mesh(es); %s; log=%s",
            len(objs), fmdex.fmdex_summary_for_report(), utils.debug_log_path(),
        )

        ok_n = 0
        fail_n = 0
        last_fail = ""
        for obj in objs:
            ok, msg = fix_materials_for_object(obj)
            log.info("%s", msg)
            if ok:
                ok_n += 1
            else:
                fail_n += 1
                last_fail = msg

        if ok_n and not fail_n:
            self.report({'INFO'}, f"Updated materials on {ok_n} mesh(es)")
        elif ok_n and fail_n:
            short = last_fail if len(last_fail) < 180 else (last_fail[:177] + "...")
            self.report(
                {'WARNING'},
                f"Updated {ok_n}, failed {fail_n}. Last: {short}",
            )
        else:
            short = last_fail if len(last_fail) < 220 else (last_fail[:217] + "...")
            self.report({'ERROR'}, short or f"Could not update {fail_n} mesh(es)")
            return {'CANCELLED'}
        return {'FINISHED'}


def reimport_selected_mesh(obj) -> tuple:
    """Refresh UV + Arc materials on one mesh in place (no Stage 1 / folder import).

    Returns (ok: bool, message: str).
    """
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False, "skipped (not a mesh)"
    try:
        name = obj.name
    except ReferenceError:
        return False, "skipped (deleted object)"

    if obj.get("arc_placement_instancer"):
        return False, (
            f"{name}: GN instancer — select its SRC mesh (or Realize Unique Instancers) "
            "instead of the point cloud"
        )

    try:
        utils.normalize_object_ue_uv_layers(obj)
    except Exception as exc:
        log.warning("Re-import Selected UV normalize '%s': %s", name, exc)

    materials.clear_clothing_material_cache()

    is_map = bool(obj.get("arc_map")) or str(obj.get("arc_model_type", "") or "").lower() == "map"
    if is_map:
        try:
            obj["arc_materials_pending"] = 1
            obj["arc_force_material_rebuild"] = 1
        except Exception:
            pass
        try:
            materials.clear_leaked_preferred_mi(obj)
            materials.invalidate_shared_mi_on_object(obj)
            materials.clear_out_of_context_map_materials(obj)
        except Exception as exc:
            log.warning("Re-import Selected map invalidate '%s': %s", name, exc)

    ok, msg = fix_materials_for_object(obj)
    if ok and is_map:
        try:
            if obj.get("arc_force_material_rebuild"):
                del obj["arc_force_material_rebuild"]
            obj["arc_materials_pending"] = 0
        except Exception:
            pass
    return ok, msg


class ARC_OUTFITS_OT_ReimportSelected(Operator):
    bl_idname = "arc_outfits.reimport_selected"
    bl_label = "Re-import Selected"
    bl_description = ""
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from . import pak_extract

        root = utils.get_pioneer_root()
        if (not root or not os.path.isdir(root)) and not pak_extract.configured_dir(context.scene):
            self.report({'ERROR'}, "Set PioneerGame Folder or the Paks folder in Settings")
            return {'CANCELLED'}

        objs = [o for o in context.selected_objects if o.type == 'MESH']
        if not objs:
            self.report({'WARNING'}, "Select one or more mesh objects first")
            return {'CANCELLED'}

        fmdex.ensure_loaded()
        log.info(
            "Re-import Selected start: %d mesh(es); %s; log=%s",
            len(objs), fmdex.fmdex_summary_for_report(), utils.debug_log_path(),
        )

        ok_n = 0
        fail_n = 0
        skip_n = 0
        fail_samples = []
        for obj in objs:
            ok, msg = reimport_selected_mesh(obj)
            log.info("%s", msg)
            if ok:
                ok_n += 1
                continue
            # Instancer / soft skips vs hard path failures
            low = (msg or "").lower()
            if "instancer" in low or low.startswith("skipped"):
                skip_n += 1
            else:
                fail_n += 1
            if len(fail_samples) < 4:
                fail_samples.append(msg)

        sample = "; ".join(fail_samples)
        if len(sample) > 200:
            sample = sample[:197] + "..."

        if ok_n and not fail_n and not skip_n:
            self.report({'INFO'}, f"Re-imported {ok_n} selected mesh(es)")
        elif ok_n and (fail_n or skip_n):
            self.report(
                {'WARNING'},
                f"Re-imported {ok_n}; failed {fail_n}; skipped {skip_n}. {sample}".strip(),
            )
        elif skip_n and not fail_n:
            self.report({'WARNING'}, sample or f"Skipped {skip_n} selected mesh(es)")
            return {'CANCELLED'}
        else:
            self.report(
                {'ERROR'},
                sample or f"Could not re-import {fail_n} selected mesh(es)",
            )
            return {'CANCELLED'}
        return {'FINISHED'}


class ARC_OUTFITS_OT_ApplyOutfitPreset(Operator):
    """Apply the selected outfit preset's per-part skin colours."""
    bl_idname = "arc_outfits.apply_outfit_preset"
    bl_label = "Apply Outfit Preset"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        preset_path = context.scene.arc_outfit_preset
        if not preset_path or preset_path == 'NONE':
            self.report({'WARNING'}, "No outfit preset selected.")
            return {'CANCELLED'}
        updated = importing.apply_outfit_preset(context, preset_path)
        if updated:
            self.report({'INFO'}, f"Outfit preset applied to {updated} part(s).")
        else:
            self.report({'WARNING'}, "Outfit preset didn't match any queued parts.")
        return {'FINISHED'}


class ARC_OUTFITS_OT_PickManualSkinsFolder(Operator, bpy_extras.io_utils.ImportHelper):
    """Browse to a folder to use as this part's Skins source."""
    bl_idname = "arc_outfits.pick_manual_skins_folder"
    bl_label = "Select Skins Folder"
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={'HIDDEN'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN'})
    entry_index: bpy.props.IntProperty(default=-1, options={'HIDDEN'})

    def execute(self, context):
        entries = context.scene.arc_psk_entries
        if not (0 <= self.entry_index < len(entries)):
            self.report({'ERROR'}, "Internal error: part entry not found.")
            return {'CANCELLED'}
        folder = _picked_folder(self)
        if not os.path.isdir(folder):
            self.report({'ERROR'}, f"Not a valid folder: {folder}")
            return {'CANCELLED'}
        entry = entries[self.entry_index]
        entry.manual_skins_folder = folder
        found = len(textures.scan_skins(entry.psk_path, folder))
        if found:
            self.report({'INFO'}, f"Found {found} skin(s) in '{os.path.basename(folder)}'.")
        else:
            self.report({'WARNING'}, f"No skin JSONs found in '{os.path.basename(folder)}'.")
        bpy.ops.arc_outfits.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


def _apply_folder_prop_layout(context) -> str:
    """Place folder props onto StaticMesh socket coordinates, duplicating shared meshes.

    Runs only for Import Folder. A folder with no sockets imports as before.
    """
    scene = context.scene
    folder = str(scene.get("arc_import_folder_layout") or "")
    try:
        del scene["arc_import_folder_layout"]
    except Exception:
        pass
    if not folder or not os.path.isdir(folder):
        return ""
    folder_norm = os.path.abspath(folder).replace("\\", "/").lower().rstrip("/")
    entries = list(scene.arc_psk_entries)
    if not entries:
        return ""
    for entry in entries:
        src = os.path.abspath(entry.psk_path).replace("\\", "/")
        if not src.lower().startswith(folder_norm + "/"):
            return ""
    try:
        from ..Maps import prop_assemble

        stats = prop_assemble.apply_detected_folder_layout(folder)
    except Exception as exc:
        print(f"Arc Raiders: prop coordinate detection failed: {exc}")
        return ""
    if stats.get("error"):
        print(f"Arc Raiders: prop layout: {stats['error']}")
        return ""
    summary = str(stats.get("summary") or "")
    if not summary:
        return ""
    return (
        f"{summary}. Placed {stats.get('placed', 0)}, "
        f"duplicated {stats.get('duplicated', 0)}."
    )


class ARC_OUTFITS_OT_ConfirmPSKImport(Operator):
    """Review parts, assign skins, then confirm import."""
    bl_idname = "arc_outfits.confirm_psk_import"
    bl_label = "Arc Asset Importer"
    bl_options = {'REGISTER', 'UNDO'}

    def _dialog_cache(self, context):
        """Operator + module cache; rebuild only when entry/manual paths change."""
        from ..Outfit_Accessories import properties as props
        fp = props.confirm_dialog_fingerprint(context)
        cache = getattr(self, "_scan_cache", None)
        if cache and cache.get("fingerprint") == fp:
            # Keep enum callbacks (make_skin_items) on the same warm cache.
            props.publish_confirm_dialog_cache(cache)
            return cache
        cache = props.get_confirm_dialog_cache(context)
        if cache is not None:
            self._scan_cache = cache
            return cache
        # Paths changed mid-dialog (manual folder pick re-invokes normally).
        cache = props.build_confirm_dialog_cache(context)
        self._scan_cache = cache
        return cache

    def _entry_info(self, context, entry):
        cache = self._dialog_cache(context)
        psk_abs = bpy.path.abspath(entry.psk_path) if entry.psk_path else ""
        return cache["entries"].get(psk_abs) or {
            "skins": [],
            "is_body": False,
            "is_hair": False,
            "model_type": "unknown",
            "hair_mis": [],
        }

    def _has_any_choices(self, context) -> bool:
        cache = self._dialog_cache(context)
        for info in cache["entries"].values():
            if info["skins"]:
                return True
            if info["is_body"]:
                return True
            if info["is_hair"] and info["hair_mis"]:
                return True
            if info["model_type"] == "clothing":
                return True
        return False

    def invoke(self, context, event):
        from ..Outfit_Accessories import properties as props
        props.clear_confirm_dialog_cache()
        self._scan_cache = None

        # Skin/colourway review UI is only for layered outfit/clothing queues.
        # Weapons, misc, face/body/hair-only, etc. import immediately.
        is_outfit_queue = importing.queue_supports_outfit_batch(context)
        if not is_outfit_queue:
            context.scene.arc_outfit_selections.clear()
            return self.execute(context)

        importing.populate_outfit_selections(context)
        # Scan once for dialog lifetime — draw / enum callbacks must not rescan.
        self._scan_cache = props.build_confirm_dialog_cache(
            context, is_outfit_queue=is_outfit_queue
        )
        has_batch = len(context.scene.arc_outfit_selections) > 0
        if not self._has_any_choices(context) and not has_batch:
            props.clear_confirm_dialog_cache()
            self._scan_cache = None
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self, width=640)

    def draw(self, context):
        layout = self.layout
        entries = context.scene.arc_psk_entries
        if not entries:
            layout.label(text="No PSK files queued.", icon='ERROR')
            return

        layout.label(text=f"{len(entries)} part(s) — assign skins then click OK:", icon='IMPORT')

        # Cache-only: scans run in invoke (or on fingerprint change via _dialog_cache).
        cache = self._dialog_cache(context)
        is_outfit_queue = cache["is_outfit_queue"]
        if is_outfit_queue:
            outfit_items = cache["outfit_preset_items"]
            if len(outfit_items) > 1:
                preset_box = layout.box()
                row = preset_box.row(align=True)
                row.prop(context.scene, "arc_outfit_preset", text="Outfit")
                row.operator("arc_outfits.apply_outfit_preset", text="Apply", icon='CHECKMARK')

            sels = context.scene.arc_outfit_selections
            manual_outfit = getattr(context.scene, 'arc_manual_outfit_folder', '')
            bbox = layout.box()
            bbox.label(text="Batch import colourways — each becomes a separate model:", icon='DUPLICATE')

            if manual_outfit:
                mrow = bbox.row()
                mrow.label(text=f"Outfit source: {manual_outfit}", icon='FILE_FOLDER')
                mrow.operator("arc_outfits.pick_manual_outfit_folder", text="Change...", icon='FILEBROWSER')
                mrow.operator("arc_outfits.clear_manual_outfit_folder", text="", icon='X')

            if len(sels) > 0:
                grid = bbox.grid_flow(row_major=True, columns=3, even_columns=True)
                for s in sels:
                    grid.prop(s, "selected", text=s.preset_name)
                n_sel = sum(1 for s in sels if s.selected)
                if n_sel:
                    bbox.label(text=f"{n_sel} selected → {n_sel} separate instance(s)", icon='INFO')
                else:
                    bbox.label(text="None ticked → single import using the choices below", icon='INFO')
            else:
                bbox.label(text="No colourway data detected automatically.", icon='ERROR')
                bbox.label(text="Pick the DA_OI colourway folder manually (Outfit or BackpackContainer/…):")
                bbox.operator("arc_outfits.pick_manual_outfit_folder", text="Browse for DA_OI Colourway Folder...", icon='FILEBROWSER')

            self._draw_outfit_preview(layout, context, sels)
            layout.separator()

            # Per-part skin picks are an advanced override: collapsed by default.
            wm = context.window_manager
            shown = bool(getattr(wm, "arc_confirm_show_parts", False))
            parts = [e for e in entries if self._entry_has_choices(context, e)]
            missing = sum(1 for e in parts if self._entry_info(context, e)["model_type"] == "clothing"
                          and not self._entry_info(context, e)["skins"])
            adv = layout.box()
            head = adv.row()
            if hasattr(wm, "arc_confirm_show_parts"):
                head.prop(wm, "arc_confirm_show_parts", text=f"Advanced: Per-Part Skins ({len(parts)})",
                          icon='TRIA_DOWN' if shown else 'TRIA_RIGHT', emboss=False)
            else:
                shown = True
            if missing and not shown:
                adv.label(text=f"{missing} part(s) have no skin data; expand to pick folders", icon='ERROR')
            if shown:
                self._draw_parts(adv, context, entries)
            return

        self._draw_parts(layout, context, entries)

    def _entry_has_choices(self, context, entry) -> bool:
        info = self._entry_info(context, entry)
        return bool(info["skins"] or info["is_body"] or (info["is_hair"] and info["hair_mis"])
                    or info["model_type"] == "clothing")

    def _draw_outfit_preview(self, layout, context, sels):
        """Placeholder for colourway outfit icon renders (not produced yet)."""
        del context
        pbox = layout.box()
        pbox.label(text="Outfit Preview", icon='IMAGE_DATA')
        names = [s.preset_name for s in sels if s.selected]
        col = pbox.column(align=True)
        col.scale_y = 3.0
        col.alignment = 'CENTER'
        shown = ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
        col.label(text=(f"Colourway icon for {shown} will show here" if names
                        else "Colourway icon render will show here"), icon='RENDER_STILL')

    def _draw_parts(self, layout, context, entries):
        for idx, entry in enumerate(entries):
            info = self._entry_info(context, entry)
            skins = info["skins"]
            is_body = info["is_body"]
            is_hair = info["is_hair"]
            is_clothing = info["model_type"] == "clothing"
            hair_mis = info["hair_mis"] if is_hair else []
            if not skins and not is_body and not hair_mis and not is_clothing:
                continue
            box = layout.box()
            box.label(text=entry.display_name, icon='FILE')
            if skins:
                box.prop(entry, "skin_choice", text="Skin")
                if entry.manual_skins_folder:
                    mrow = box.row()
                    mrow.label(text=f"Skin source: {os.path.basename(entry.manual_skins_folder)}", icon='FILE_FOLDER')
                    mop = mrow.operator("arc_outfits.pick_manual_skins_folder", text="Change...", icon='FILEBROWSER')
                    mop.entry_index = idx
            elif is_clothing:
                warn = box.box()
                warn.label(text="No skin data detected for this part.", icon='ERROR')
                warn.label(text="The Skins folder name may not match — pick it manually:")
                wop = warn.operator("arc_outfits.pick_manual_skins_folder", text="Browse for Skins Folder...", icon='FILEBROWSER')
                wop.entry_index = idx
            if is_body:
                box.prop(entry, "body_choice", text="Body Skin")
            if is_hair and hair_mis:
                col = box.column(align=True)
                col.label(text="Hair MI — click to select:")
                for mi_name, mi_path in hair_mis:
                    is_sel = entry.hair_mi == mi_path
                    row = col.row()
                    row.alert = is_sel
                    icon = 'LAYER_ACTIVE' if is_sel else 'LAYER_USED'
                    op = row.operator("arc_outfits.select_hair_mi",
                                      text=("\u2713 " if is_sel else "  ") + mi_name,
                                      icon=icon)
                    op.psk_path = entry.psk_path
                    op.mi_path = mi_path

    def execute(self, context):
        from ..Outfit_Accessories import properties as props
        props.clear_confirm_dialog_cache()
        self._scan_cache = None

        try:
            utils.preload_arc_node_groups()
        except Exception as exc:
            print(f"Arc Asset Importer: ArcTexturer preload failed (non-fatal): {exc}")

        selected = []
        if importing.queue_supports_outfit_batch(context):
            selected = [(s.preset_name, bpy.path.abspath(s.json_path))
                        for s in context.scene.arc_outfit_selections if s.selected]
        if selected:
            return _start_staged_import(self, context, selected, _finish_selector_batch)

        if bpy.app.background or context.window is None:
            # No window to keep responsive: drain the same step generator in one go.
            gen = _iter_folder_import(context)
            try:
                while True:
                    next(gen)
            except StopIteration as stop:
                _finish_folder_import(self, context, stop.value)
            return {'FINISHED'}
        return _start_staged_generator(self, context, _iter_folder_import, _finish_folder_import,
                                       frame=importing.queue_supports_outfit_batch(context))


def _iter_folder_import(context):
    """Step generator for the folder import: every mesh first, then one material step per mesh.

    Yields ``(stage, index, total, label)``. Returns the list of ``(level, message)`` reports.
    """
    from ..Maps import staged_import as _staged_sink
    from .materials import dr_primary_operations as _dr_ops

    entries = list(context.scene.arc_psk_entries)
    total = len(entries)
    ok_count = 0
    all_new_objs = []
    reports = []

    # Pass 1: meshes only. Each new mesh shows the grey placeholder until pass 2.
    pending_materials = []
    gen = _iter_process_entries_pipelined(entries, context, defer=pending_materials)
    idx = 0
    while True:
        label = os.path.basename(bpy.path.abspath(entries[idx].psk_path)) if idx < total else ""
        yield ("mesh", min(idx + 1, total), total, label)
        queued_before = len(pending_materials)
        try:
            ok, msg, new_objs = next(gen)
        except StopIteration:
            break
        idx += 1
        all_new_objs.extend(new_objs)
        _staged_sink.stage_placeholder(
            [q[0] for q in pending_materials[queued_before:] if _still_alive(q[0])])
        if ok:
            ok_count += 1
            reports.append(('WARNING' if "FAIL" in msg else 'INFO', msg))
        else:
            reports.append(('WARNING', msg))

    # Pass 2: materials, one mesh per step, after the last mesh exists.
    mat_total = len(pending_materials)
    for mat_idx, (obj, build) in enumerate(pending_materials, start=1):
        if not _still_alive(obj):
            continue
        label = _object_label(obj)
        yield ("material", mat_idx, mat_total, label)
        _staged_sink.restore_slots(obj)
        # GT outfit graphs hand their finish pass to the sink, so it can run in slices below.
        _staged_sink.FINISH_SINK = []
        try:
            build()
        finally:
            deferred = _staged_sink.FINISH_SINK or []
            _staged_sink.FINISH_SINK = None
        status = str(obj.get("arc_material_status") or "")
        if "FAIL" in status:
            reports.append(('WARNING', f"{label}: {status}"))
        for ent in deferred:
            with _dr_ops.operation_session():
                for cut in ent["gen"]:
                    cut = str(cut)
                    tidy = cut in ("tidy", "blocks")
                    yield ("tidy" if tidy else "slice", mat_idx, mat_total,
                           label if tidy else f"{label} ({cut})")
                for after in ent["after"]:
                    after()
    _staged_sink.sweep_placeholder([o for o in all_new_objs if _still_alive(o)])

    types = [textures.detect_model_type(bpy.path.abspath(e.psk_path)) for e in entries]
    any_clothing = any(t == "clothing" for t in types)
    cosmetic_import = any(t in _REFERENCE_MODEL_TYPES for t in types)
    dominant_type = "weapon" if all(t == "weapon" for t in types) else ""
    yield ("rig", 1, 1, "")
    rig.fix_rig_all(all_new_objs, merge=any_clothing, model_type=dominant_type)
    all_new_objs = [o for o in all_new_objs if _still_alive(o)]
    char = ""
    for entry in entries:
        char = importing.get_character_name(bpy.path.abspath(entry.psk_path))
        if char:
            break
    if char:
        _rename_outfit_base_objects(all_new_objs, char)

    yield ("finish", 1, 3, "layout")
    # One model = whole imported outfit (same grouping as a single batch instance).
    if all_new_objs and cosmetic_import:
        try:
            from ..Outfit_Accessories import import_layout as _layout

            layout_role = "Body" if types and all(t == "body" for t in types) else "Outfit"

            for o in all_new_objs:
                if _still_alive(o) and not o.get("arc_assembly_role"):
                    try:
                        o["arc_assembly_role"] = layout_role
                    except Exception:
                        pass
            slot = _layout.place_imported(
                [o for o in all_new_objs if _still_alive(o)],
                layout_role,
                scene=context.scene,
            )
            if _layout.offset_enabled(context.scene):
                reports.append((
                    'INFO',
                    f"{layout_role} slot {slot} @ X={_layout.slot_world_x(slot, context.scene):.1f} m",
                ))
        except Exception as exc:
            print(f"Arc Asset Importer: slot layout skipped: {exc}")
        yield ("finish", 2, 3, "view")
        _post_import_outfit_ux(context, [all_new_objs], references=True)
    elif all_new_objs:
        layout_msg = _apply_folder_prop_layout(context)
        _hide_imported_armatures(all_new_objs)
        yield ("finish", 2, 3, "view")
        _frame_viewport_on_outfit_models(context, [all_new_objs])
        if layout_msg:
            reports.append(('INFO', layout_msg))

    yield ("finish", 3, 3, "collections")
    from ..Outfit_Accessories import asset_organization
    asset_organization.organize_objects(all_new_objs, context.scene)
    reports.append(('INFO', f"Arc Raiders: {ok_count}/{total} part(s) imported."))
    return reports


def _finish_folder_import(op, context, reports):
    for level, msg in reports or ():
        op.report({level}, msg)
    context.scene.arc_psk_entries.clear()
    context.scene.arc_outfit_selections.clear()


class ARC_OUTFITS_OT_PickPioneerRoot(Operator, bpy_extras.io_utils.ImportHelper):
    """Browse to the PioneerGame root folder."""
    bl_idname = "arc_outfits.pick_pioneer_root"
    bl_label = "Select PioneerGame Folder"
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={'HIDDEN'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN'})

    def execute(self, context):
        folder = _picked_folder(self)
        if not folder:
            folder = bpy.path.abspath(self.filepath)
        if not os.path.isdir(folder):
            self.report({'ERROR'}, f"Not a valid folder: {folder}")
            return {'CANCELLED'}
        context.scene.arc_pioneer_root = folder
        return {'FINISHED'}


class ARC_OUTFITS_OT_ClearPioneerRoot(Operator):
    """Clear the PioneerGame root folder."""
    bl_idname = "arc_outfits.clear_pioneer_root"
    bl_label = "Clear PioneerGame Folder"

    def execute(self, context):
        context.scene.arc_pioneer_root = ""
        return {'FINISHED'}


class ARC_OUTFITS_OT_PickFmdexRoot(Operator, bpy_extras.io_utils.ImportHelper):
    """Browse to FModel's FMDex output folder (contains *_FMDex.json.br)."""
    bl_idname = "arc_outfits.pick_fmdex_root"
    bl_label = "Select FMDex Folder"
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={'HIDDEN'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN'})

    def invoke(self, context, event):
        default = fmdex.get_fmdex_directory()
        if default:
            self.directory = default.rstrip("/\\") + os.sep
            self.filepath = self.directory
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        folder = _picked_folder(self)
        if not folder:
            folder = bpy.path.abspath(self.filepath)
        if not os.path.isdir(folder):
            self.report({'ERROR'}, f"Not a valid folder: {folder}")
            return {'CANCELLED'}
        context.scene.arc_fmdex_root = folder
        fmdex.invalidate_cache()
        try:
            from ..Animations import animation_catalog as acat
            acat.invalidate()
        except Exception:
            pass
        files = fmdex.find_fmdex_index_files(folder)
        if files:
            fmdex.ensure_loaded(force=True)
            st = fmdex.status()
            self.report(
                {'INFO'},
                f"FMDex: {len(files)} index file(s); loaded {st.get('entry_count', 0)} entries",
            )
        else:
            self.report(
                {'WARNING'},
                "No *_FMDex.json.br found — pick FModel's FMDex/<Profile> output, not source code",
            )
        return {'FINISHED'}


class ARC_OUTFITS_OT_ClearFmdexRoot(Operator):
    """Clear the FMDex folder override."""
    bl_idname = "arc_outfits.clear_fmdex_root"
    bl_label = "Clear FMDex Folder"

    def execute(self, context):
        context.scene.arc_fmdex_root = ""
        fmdex.invalidate_cache()
        return {'FINISHED'}


class ARC_OUTFITS_OT_PaletteSetSelected(Operator):
    bl_idname = "arc_outfits.palette_set_selected"
    bl_label = "Set Palette On Selected"
    bl_description = (
        "Apply the scene Palette mode to selected meshes now (rewires ColorMask_XYZ) "
        "and store it as an override for later Update Materials / re-import"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mode = str(getattr(context.scene, "arc_palette_mode", "AUTO") or "AUTO").lower()
        count = 0
        rewired = 0
        skipped = 0
        for obj in context.selected_objects or []:
            if getattr(obj, "type", "") != "MESH":
                continue
            key = str(obj.get("arc_material_key", "") or "")
            mat = obj.active_material
            if not key and mat is not None:
                key = str(mat.get("arc_material_key", "") or "")
            if key:
                palette_calibration.set_override(key, mode)
            obj["arc_palette_mode"] = mode
            if mat is not None:
                mat["arc_palette_mode"] = mode
                # Prefer real clothing mats (skip Mask Debug slot copies).
                try:
                    from ..Script_Helpers import mask_debug as _md
                    cloth = _md._source_materials_for_object(obj)
                except Exception:
                    cloth = []
                targets = cloth if cloth else [mat]
                n = 0
                for t in targets:
                    if t is None:
                        continue
                    t["arc_palette_mode"] = mode
                    n += materials.apply_palette_routing_live(t, mode, obj=obj)
                # Keep Mode 1/2 overlay ColorABC in sync with sources.
                try:
                    from ..Script_Helpers import mask_debug as _md
                    for i, slot in enumerate(obj.material_slots):
                        ov = slot.material
                        src = _md._slot_source_material(obj, i)
                        if ov is not None and src is not None and _md._is_debug_mat(ov):
                            _md._sync_palette_rgb_defaults(src, ov)
                            materials.apply_palette_routing_live(ov, mode, obj=obj)
                except Exception:
                    pass
                rewired += n
                if n:
                    count += 1
                else:
                    skipped += 1
            elif key:
                count += 1
            else:
                skipped += 1
        if count == 0 and rewired == 0:
            self.report(
                {'WARNING'},
                "No clothing ColorMask_XYZ on selection — select imported outfit meshes "
                "(need ColorA/B/C nodes). Scene Palette alone does not change look.",
            )
            return {'CANCELLED'}
        self.report(
            {'INFO'},
            f"Palette '{mode}' applied on {count} mesh(es), rewired {rewired} sockets"
            + (f"; {skipped} skipped" if skipped else ""),
        )
        return {'FINISHED'}


class ARC_OUTFITS_OT_PaletteResetSelected(Operator):
    bl_idname = "arc_outfits.palette_reset_selected"
    bl_label = "Reset Palette Selected"
    bl_description = (
        "Clear palette overrides on selected meshes and rewire ColorMask_XYZ to Auto"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        count = 0
        rewired = 0
        for obj in context.selected_objects or []:
            if getattr(obj, "type", "") != "MESH":
                continue
            key = str(obj.get("arc_material_key", "") or "")
            mat = obj.active_material
            if not key and mat is not None:
                key = str(mat.get("arc_material_key", "") or "")
            if key:
                palette_calibration.set_override(key, "auto")
            obj["arc_palette_mode"] = "auto"
            obj["arc_palette_routing"] = "auto"
            if mat is not None:
                mat["arc_palette_mode"] = "auto"
                try:
                    from ..Script_Helpers import mask_debug as _md
                    cloth = _md._source_materials_for_object(obj)
                except Exception:
                    cloth = []
                targets = cloth if cloth else [mat]
                for t in targets:
                    if t is None:
                        continue
                    t["arc_palette_mode"] = "auto"
                    rewired += materials.apply_palette_routing_live(t, "auto", obj=obj)
                try:
                    from ..Script_Helpers import mask_debug as _md
                    for i, slot in enumerate(obj.material_slots):
                        ov = slot.material
                        src = _md._slot_source_material(obj, i)
                        if ov is not None and src is not None and _md._is_debug_mat(ov):
                            _md._sync_palette_rgb_defaults(src, ov)
                            rewired += materials.apply_palette_routing_live(ov, "auto", obj=obj)
                except Exception:
                    pass
            count += 1
        self.report(
            {'INFO'},
            f"Reset palette on {count} object(s), rewired {rewired} sockets",
        )
        return {'FINISHED'}


class ARC_OUTFITS_OT_PaletteExportCalibration(Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "arc_outfits.palette_export_calibration"
    bl_label = "Export Palette Calibration"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, "w", encoding="utf-8") as fh:
            fh.write(palette_calibration.export_overrides_json())
        self.report({'INFO'}, f"Exported {self.filepath}")
        return {'FINISHED'}


class ARC_OUTFITS_OT_PaletteImportCalibration(Operator, bpy_extras.io_utils.ImportHelper):
    bl_idname = "arc_outfits.palette_import_calibration"
    bl_label = "Import Palette Calibration"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, "r", encoding="utf-8") as fh:
            n = palette_calibration.import_overrides_json(fh.read())
        self.report({'INFO'}, f"Imported {n} override(s) — Update Materials")
        return {'FINISHED'}


class ARC_OUTFITS_OT_PaletteClearCalibration(Operator):
    bl_idname = "arc_outfits.palette_clear_calibration"
    bl_label = "Clear Palette Calibration"
    bl_options = {'REGISTER'}

    def execute(self, context):
        palette_calibration.clear_overrides()
        self.report({'INFO'}, "Cleared all palette overrides")
        return {'FINISHED'}


def _scene_decal_exclude_mask(scene) -> int:
    mask = 0
    for z in range(1, 9):
        if bool(getattr(scene, f"arc_decal_excl_c{z}", False)):
            mask |= 1 << (z - 1)
    return mask


def _clothing_mats_from_selection(context):
    mats = []
    seen = set()
    for obj in context.selected_objects or []:
        if getattr(obj, "type", "") != "MESH":
            continue
        for slot in obj.material_slots:
            m = slot.material
            if m is None or m.name in seen:
                continue
            seen.add(m.name)
            mats.append(m)
        am = obj.active_material
        if am is not None and am.name not in seen:
            seen.add(am.name)
            mats.append(am)
    return mats


class ARC_OUTFITS_OT_DecalExcludeApply(Operator):
    bl_idname = "arc_outfits.decal_exclude_apply"
    bl_label = "Apply LayerMask Override"
    bl_description = (
        "Manual override on top of cooked LayerMask Allow bits. "
        "Tick Colour N to also block stickers on that zone (e.g. 4 = knee pad). "
        "Does not replace the MIC LayerMask"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .materials import gt_outfit_pbr as gt_pbr

        excl = _scene_decal_exclude_mask(context.scene)
        try:
            slot = int(getattr(context.scene, "arc_decal_excl_slot", "0") or "0")
        except Exception:
            slot = 0
        mats = _clothing_mats_from_selection(context)
        if not mats:
            self.report({'WARNING'}, "Select a clothing mesh with materials")
            return {'CANCELLED'}
        n_gates = 0
        for mat in mats:
            n_gates += gt_pbr.apply_decal_exclude_mask_to_material(mat, excl, slot=slot)
        if n_gates == 0:
            self.report(
                {'WARNING'},
                "No LayerMask gates found — run Update Materials (GT) first, "
                "or select a material that has decals",
            )
            return {'CANCELLED'}
        parts = [str(z) for z in range(1, 9) if excl & (1 << (z - 1))]
        self.report(
            {'INFO'},
            f"Override excl Colour {','.join(parts) or '(none)'} on {n_gates} gate(s) "
            f"(cooked Allow unchanged)",
        )
        return {'FINISHED'}


class ARC_OUTFITS_OT_DecalExcludeClear(Operator):
    bl_idname = "arc_outfits.decal_exclude_clear"
    bl_label = "Clear LayerMask Override"
    bl_description = (
        "Remove manual Colour N excludes; cooked LayerMask Allow bits stay in effect"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .materials import gt_outfit_pbr as gt_pbr

        scene = context.scene
        for z in range(1, 9):
            prop = f"arc_decal_excl_c{z}"
            if hasattr(scene, prop):
                setattr(scene, prop, False)
        try:
            slot = int(getattr(scene, "arc_decal_excl_slot", "0") or "0")
        except Exception:
            slot = 0
        mats = _clothing_mats_from_selection(context)
        n_gates = 0
        for mat in mats:
            n_gates += gt_pbr.apply_decal_exclude_mask_to_material(mat, 0, slot=slot)
        self.report(
            {'INFO'},
            f"Cleared override on {n_gates} gate(s); cooked LayerMask kept",
        )
        return {'FINISHED'}


class ARC_OUTFITS_OT_OrganizeNodes(Operator):
    """Run deferred NCT / place_* / MI-param layout on clothing materials."""
    bl_idname = "arc_outfits.organize_nodes"
    bl_label = "Organize Nodes"
    bl_description = (
        "Apply layout on clothing materials: ArcTexturer NCT, or Ground Truth 200/50/50 overlap resolve. "
        "Uses selected meshes, or all scene clothing mats if none are selected."
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objs = [o for o in context.selected_objects if o.type == 'MESH']
        if not objs:
            objs = [o for o in context.scene.objects if o.type == 'MESH']
        if not objs:
            self.report({'WARNING'}, "No mesh objects to organize")
            return {'CANCELLED'}
        n = materials.organize_clothing_nodes(objects=objs)
        if n:
            self.report({'INFO'}, f"Organized nodes on {n} material(s)")
        else:
            self.report({'WARNING'}, "No ArcTexturer clothing materials found to organize")
        return {'FINISHED'}


class ARC_OUTFITS_OT_MaskDebugApply(Operator):
    """Inject Mask Debug overlay into clothing materials on selected meshes."""
    bl_idname = "arc_outfits.mask_debug_apply"
    bl_label = "Apply Mask Debug"
    bl_description = (
        "Inject Mask Debug into selected clothing materials. "
        "Mode 0 without Numbers/ColorMask clears the inject. "
        "Use Clear to restore. Switch viewport to Material Preview"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from ..Script_Helpers import mask_debug

        try:
            mode = int(getattr(context.scene, "arc_mask_debug_mode", 0) or 0)
        except Exception:
            mode = 0
        show_nums = bool(getattr(context.scene, "arc_mask_debug_show_numbers", True))
        show_cm = bool(getattr(context.scene, "arc_mask_debug_show_colormask", False))
        if mode <= 0 and not show_nums and not show_cm:
            n = mask_debug.clear_mask_debug_selected(context)
            self.report({'INFO'}, f"Mask Debug cleared on {n} object(s)")
            return {'FINISHED'}
        ok, skip, status = mask_debug.apply_mask_debug_selected(context, mode=mode)
        if not ok:
            self.report({'WARNING'}, "Select mesh objects with clothing materials")
            return {'CANCELLED'}
        err = bool(status) and ("failed" in status or status.startswith("no "))
        self.report(
            {'WARNING' if err else 'INFO'},
            f"Mask Debug on {ok} object(s)"
            + (f" ({skip} skipped)" if skip else "")
            + (f": {status}" if status else "")
            + " — Material Preview",
        )
        return {'FINISHED'}


class ARC_OUTFITS_OT_MaskDebugClear(Operator):
    bl_idname = "arc_outfits.mask_debug_clear"
    bl_label = "Clear Mask Debug"
    bl_description = "Remove Mask Debug inject and restore original Surface links"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from ..Script_Helpers import mask_debug
        from ..Outfit_Accessories import properties as _props

        _props._mask_debug_updating = True
        try:
            n = mask_debug.clear_mask_debug_selected(context)
            context.scene.arc_mask_debug_mode = "0"
        finally:
            _props._mask_debug_updating = False
        self.report({'INFO'}, f"Mask Debug cleared on {n} object(s)")
        return {'FINISHED'}


class ARC_OUTFITS_OT_UpdateSelectedGroupNodes(Operator):
    """Hot-swap selected group nodes to the blend's current trees; keep links by name."""
    bl_idname = "arc_outfits.update_selected_group_nodes"
    bl_label = "Update Selected Group Nodes"
    bl_description = (
        "Reload ArcTexturer / CurvatureID_Override / Visor / ColorMask_XYZ / … from "
        "the bundled blend onto selected group nodes (Shader Editor selection, or "
        "groups on selected objects' materials). Reconnects by socket name"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from ..Script_Helpers import group_hotswap

        updated, skipped, messages = group_hotswap.update_selected_group_nodes(context)
        if not updated and not skipped:
            self.report(
                {'WARNING'},
                "No group nodes found — select groups in the Shader Editor, "
                "or select mesh objects with Arc groups",
            )
            return {'CANCELLED'}
        for msg in messages[:8]:
            print(f"Arc Raiders group hot-swap: {msg}")
        self.report(
            {'INFO'},
            f"Updated {updated} group node(s)"
            + (f", skipped {skipped}" if skipped else ""),
        )
        return {'FINISHED'}


def _queue_mesh_paths(context, paths: list[str]) -> int:
    """Queue already extracted mesh files (.psk or .uemodel)."""
    scene = context.scene
    scene.arc_psk_entries.clear()
    count = 0
    for path in paths or []:
        if not path or not os.path.isfile(path):
            continue
        entry = scene.arc_psk_entries.add()
        entry.psk_path = path
        entry.display_name = os.path.basename(path)
        count += 1
    return count


def _queue_folder_psks(context, folder: str):
    """Clear and fill arc_psk_entries from a folder. Returns (count, skipped_pskx)."""
    psks, skipped = utils.find_psks_in_folder(folder)
    scene = context.scene
    scene.arc_psk_entries.clear()
    for psk_path in psks:
        entry = scene.arc_psk_entries.add()
        entry.psk_path = psk_path
        entry.display_name = os.path.basename(psk_path)
    return len(psks), skipped


def _find_weapon_armature(context):
    """Prefer selected SK_* armature, else last imported, else any weapon armature."""
    scene = context.scene
    for obj in context.selected_objects:
        if obj.type == "ARMATURE":
            return obj
    name = getattr(scene, "arc_last_weapon_armature", "") or ""
    if name and name in bpy.data.objects:
        obj = bpy.data.objects[name]
        if obj.type == "ARMATURE":
            return obj
    for obj in bpy.data.objects:
        if obj.type != "ARMATURE":
            continue
        if str(obj.get("arc_model_type", "") or "").lower() == "weapon":
            return obj
        nl = obj.name.lower()
        if nl.startswith("sk_") and any(
            tok in nl for tok in ("weapon", "gun", "rifle", "pistol", "shotgun", "smg", "lmg")
        ):
            return obj
    return None


def _snap_mod_to_gun(mod_objects, gun_arm, bone_name: str) -> int:
    """Bone-parent mod meshes to gun attachment bone; zero local transforms."""
    if gun_arm is None or gun_arm.type != "ARMATURE":
        return 0
    bones = gun_arm.data.bones
    target = None
    if bone_name and bone_name in bones:
        target = bone_name
    else:
        for cand in (bone_name, "weapon_root", "root", "Root"):
            if cand and cand in bones:
                target = cand
                break
        if target is None and len(bones):
            target = bones[0].name
    if not target:
        return 0

    snapped = 0
    targets = [o for o in mod_objects if o.type == "MESH"]
    if not targets:
        targets = [o for o in mod_objects if o.type == "ARMATURE"]
    for obj in targets:
        obj.parent = gun_arm
        obj.parent_type = "BONE"
        obj.parent_bone = target
        obj.location = (0.0, 0.0, 0.0)
        obj.rotation_euler = (0.0, 0.0, 0.0)
        obj.scale = (1.0, 1.0, 1.0)
        snapped += 1
    return snapped


def _is_mainshroud_material(mat, obj=None) -> bool:
    name = (getattr(mat, "name", "") or "").lower()
    if "mainshroud" in name or "main_shroud" in name or "shroud" in name:
        return True
    if obj is not None:
        on = (obj.name or "").lower()
        if "mainshroud" in on or "shroud" in on:
            return True
    return False


class ARC_OUTFITS_OT_ImportWeapon(Operator):
    """Import the selected ST firearm/launcher folder from Pioneer."""
    bl_idname = "arc_outfits.import_weapon"
    bl_label = "Import Weapon"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from ..Weapons import weapon_import
        return weapon_import.selected_import(self, context)


class ARC_OUTFITS_OT_ImportWeaponMod(Operator):
    """Import selected ST weapon mod and bone-parent snap to the active gun."""
    bl_idname = "arc_outfits.import_weapon_mod"
    bl_label = "Import Weapon Mod"
    bl_options = {"REGISTER", "UNDO"}

    mod_type: StringProperty(name="Mod Type", default="Muzzle")

    def execute(self, context):
        from ..Weapons import weapon_position, weapon_import
        if weapon_position.roots_for_selection(context):
            return weapon_import.import_mod(self, context)
        from ..Weapons import weapon_catalog as wcat
        from ..Animations import rig

        scene = context.scene
        prop_map = {
            "Muzzle": "arc_weapon_mod_muzzle",
            "Stock": "arc_weapon_mod_stock",
            "Magazine": "arc_weapon_mod_magazine",
            "UnderBarrel": "arc_weapon_mod_underbarrel",
            "Tech": "arc_weapon_mod_tech",
        }
        prop = prop_map.get(self.mod_type, "arc_weapon_mod_muzzle")
        key = getattr(scene, prop, "NONE") or "NONE"
        if key == "NONE":
            self.report({"ERROR"}, f"Select a {self.mod_type} mod first")
            return {"CANCELLED"}
        entry = wcat.mod_by_key(key)
        if not entry:
            self.report({"ERROR"}, f"Unknown mod key: {key}")
            return {"CANCELLED"}
        pioneer = utils.get_pioneer_root() or getattr(scene, "arc_pioneer_root", "") or ""
        folder = wcat.resolve_mod_folder(pioneer, entry)
        if not folder:
            from . import pak_extract

            stem = entry.get("stem") or ""
            meshes = pak_extract.ensure_rels(scene, [f"Items/WeaponMod/{stem}"]) if stem else []
            if not meshes:
                self.report(
                    {"ERROR"},
                    f"Content folder missing: Items/WeaponMod/{stem}. "
                    f"Set PioneerGame Folder or the Paks folder in Settings.",
                )
                return {"CANCELLED"}
            n = _queue_mesh_paths(context, meshes)
            if not n:
                self.report({"ERROR"}, f"No mesh extracted for {entry.get('name') or stem}")
                return {"CANCELLED"}
            before = set(bpy.data.objects)
            bpy.ops.arc_outfits.confirm_psk_import("INVOKE_DEFAULT")
            new_objs = [o for o in bpy.data.objects if o not in before]
            if any(o.type == "ARMATURE" for o in new_objs):
                rig.fix_rig_all(new_objs, merge=False, model_type="weapon")
            gun = _find_weapon_armature(context)
            bone = wcat.attachment_bone_for_stem(stem)
            snapped = _snap_mod_to_gun(new_objs, gun, bone) if gun else 0
            if gun is None:
                self.report(
                    {"WARNING"},
                    f"Imported {entry.get('name')} but no gun armature found to snap to",
                )
            else:
                self.report(
                    {"INFO"},
                    f"Imported {entry.get('name')} → bone '{bone}' on {gun.name} ({snapped} obj)",
                )
            return {"FINISHED"}

        gun = _find_weapon_armature(context)
        before = set(bpy.data.objects)
        n, _skipped = _queue_folder_psks(context, folder)
        if not n:
            self.report({"ERROR"}, f"No .psk/.pskx in {folder}")
            return {"CANCELLED"}
        bpy.ops.arc_outfits.confirm_psk_import("INVOKE_DEFAULT")
        new_objs = [o for o in bpy.data.objects if o not in before]
        if any(o.type == "ARMATURE" for o in new_objs):
            rig.fix_rig_all(new_objs, merge=False, model_type="weapon")
        bone = wcat.attachment_bone_for_stem(entry.get("stem") or "")
        snapped = _snap_mod_to_gun(new_objs, gun, bone) if gun else 0
        if gun is None:
            self.report(
                {"WARNING"},
                f"Imported {entry.get('name')} but no gun armature found to snap to",
            )
        else:
            self.report(
                {"INFO"},
                f"Imported {entry.get('name')} → bone '{bone}' on {gun.name} ({snapped} obj)",
            )
        return {"FINISHED"}


def _weapon_mesh_targets(context):
    """Selected weapon meshes, else all meshes stamped arc_model_type=weapon."""
    objs = [o for o in context.selected_objects if o.type == "MESH"]
    if not objs:
        objs = [
            o
            for o in bpy.data.objects
            if o.type == "MESH" and str(o.get("arc_model_type", "")).lower() == "weapon"
        ]
    return objs


class ARC_OUTFITS_OT_ApplyWeaponSkin(Operator):
    """Rebuild weapon materials from the selected Skins/<colourway> MI."""
    bl_idname = "arc_outfits.apply_weapon_skin"
    bl_label = "Apply Weapon Skin"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from ..Weapons import weapon_import
        result = weapon_import.apply_skin(self, context)
        if result is not None:
            return result

        from ..Weapons import weapon_catalog as wcat
        from .materials import weapon as weapon_mats
        from .materials import common as mat_common
        from .materials import dispatch as mat_dispatch

        scene = context.scene
        skin_name = getattr(scene, "arc_weapon_skin", "NONE") or "NONE"
        pioneer = utils.get_pioneer_root() or getattr(scene, "arc_pioneer_root", "") or ""
        key = getattr(scene, "arc_weapon_key", "NONE") or "NONE"
        entry = wcat.weapon_by_key(key) if key != "NONE" else None

        skin_mi = ""
        if skin_name != "NONE":
            if entry:
                skin_mi = wcat.resolve_weapon_skin_mi(pioneer, entry, skin_name)
            if not skin_mi:
                # Fallback: resolve from selected mesh's Skins/ tree.
                for obj in _weapon_mesh_targets(context):
                    psk = str(obj.get("arc_psk_path", "") or "")
                    if not psk:
                        continue
                    for name, path in textures.scan_skins(psk):
                        if name == skin_name:
                            skin_mi = path
                            break
                    if skin_mi:
                        break
            if not skin_mi:
                self.report(
                    {"ERROR"},
                    f"Skin '{skin_name}' not found under gun Skins/ "
                    f"(Pioneer root + selected Gun required)",
                )
                return {"CANCELLED"}

        objs = _weapon_mesh_targets(context)
        if not objs:
            self.report({"ERROR"}, "No weapon meshes selected / in scene")
            return {"CANCELLED"}

        # Drop shared MI / ObjectPath caches so colourways rebuild and Pattern
        # Mask lookups retry across Content roots (2.0 Unpack + Steam dump).
        try:
            mat_common._FLAT_MI_CACHE.clear()
            mat_common._SK_SLOTS_CACHE.clear()
            if hasattr(mat_dispatch, "_SHARED_MI_MATERIALS"):
                mat_dispatch._SHARED_MI_MATERIALS.clear()
            if hasattr(textures, "_OBJECT_PATH_ASSET_CACHE"):
                textures._OBJECT_PATH_ASSET_CACHE.clear()
        except Exception:
            pass

        n = 0
        paint_ok = 0
        for obj in objs:
            psk = str(obj.get("arc_psk_path", "") or "")
            if not psk or not os.path.isfile(psk):
                continue
            # Per-mesh skin when gun catalog skin doesn't match this mesh's stem.
            mi = skin_mi
            if skin_name != "NONE" and mi:
                gun_folder = os.path.dirname(psk).replace("\\", "/").lower()
                if entry and (entry.get("stem") or "").lower() not in gun_folder:
                    mi = ""
                    for name, path in textures.scan_skins(psk):
                        if name == skin_name:
                            mi = path
                            break
            wired = weapon_mats.setup_weapon_material(obj, psk, skin_json=mi or "")
            if wired:
                n += 1
                try:
                    if mi:
                        obj["arc_skin_json"] = os.path.abspath(mi)
                        obj["arc_skin_choice"] = os.path.abspath(mi)
                    else:
                        obj["arc_skin_json"] = ""
                        obj["arc_skin_choice"] = "NONE"
                except Exception:
                    pass
                # Count materials that got Use Pattern enabled (paintjob skins).
                for slot in obj.material_slots:
                    mat = slot.material
                    node = weapon_mats._find_weapon_texturer_instance(mat) if mat else None
                    if node is None:
                        continue
                    sock = node.inputs.get("Use Pattern R")
                    if sock is not None and float(sock.default_value or 0.0) > 0.5:
                        paint_ok += 1
                        break
        label = skin_name if skin_name != "NONE" else "base MI"
        extra = f", paintjob on {paint_ok}" if paint_ok else ""
        if skin_name != "NONE" and n and not paint_ok:
            extra = (
                ", no paintjob pattern (export MaterialLibrary/"
                "Textures/Weapons/Patterns PNGs if this skin needs one)"
            )
        self.report({"INFO"}, f"Applied skin '{label}' to {n} mesh(es){extra}")
        return {"FINISHED"} if n else {"CANCELLED"}


class ARC_OUTFITS_OT_ClearWeaponSkin(Operator):
    """Rebuild weapon materials with the base MI (no Skins/ colourway)."""
    bl_idname = "arc_outfits.clear_weapon_skin"
    bl_label = "Clear Weapon Skin"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        if hasattr(scene, "arc_weapon_skin"):
            try:
                scene.arc_weapon_skin = "NONE"
            except Exception:
                pass
        return bpy.ops.arc_outfits.apply_weapon_skin()


class ARC_OUTFITS_OT_ApplyWeaponPattern(Operator):
    """Load the selected pattern into WeaponTexturer and enable Use Pattern R/G/B."""
    bl_idname = "arc_outfits.apply_weapon_pattern"
    bl_label = "Apply Weapon Pattern"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from ..Weapons import weapon_catalog as wcat
        from .materials import weapon as weapon_mats

        scene = context.scene
        pat = getattr(scene, "arc_weapon_pattern", "NONE") or "NONE"
        if pat == "NONE":
            self.report({"ERROR"}, "Select a pattern PNG first")
            return {"CANCELLED"}
        pioneer = utils.get_pioneer_root() or getattr(scene, "arc_pioneer_root", "") or ""
        pdir = wcat.patterns_dir(pioneer)
        if not pdir:
            self.report({"ERROR"}, "Patterns folder not found under Pioneer root")
            return {"CANCELLED"}
        path = os.path.join(pdir, pat)
        if not os.path.isfile(path):
            self.report({"ERROR"}, f"Pattern file missing: {pat}")
            return {"CANCELLED"}

        objs = [o for o in context.selected_objects if o.type == "MESH"]
        if not objs:
            objs = [
                o
                for o in bpy.data.objects
                if o.type == "MESH" and str(o.get("arc_model_type", "")).lower() == "weapon"
            ]
        mats = []
        for obj in objs:
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None:
                    continue
                mats.append((mat, obj))
        if not mats:
            self.report({"ERROR"}, "No weapon materials on selection / scene")
            return {"CANCELLED"}

        shroud = [(m, o) for m, o in mats if _is_mainshroud_material(m, o)]
        targets = shroud or mats
        n = 0
        seen = set()
        for mat, _obj in targets:
            if mat.name in seen:
                continue
            seen.add(mat.name)
            if weapon_mats.apply_pattern_to_material(mat, path, use_pattern=True):
                n += 1
        self.report({"INFO"}, f"Applied pattern to {n} material(s)")
        return {"FINISHED"} if n else {"CANCELLED"}


class ARC_OUTFITS_OT_ClearWeaponPattern(Operator):
    """Set Use Pattern R/G/B = 0 on WeaponTexturer groups (selected / weapon mats)."""
    bl_idname = "arc_outfits.clear_weapon_pattern"
    bl_label = "Clear Weapon Pattern"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .materials import weapon as weapon_mats

        objs = [o for o in context.selected_objects if o.type == "MESH"]
        if not objs:
            objs = [
                o
                for o in bpy.data.objects
                if o.type == "MESH" and str(o.get("arc_model_type", "")).lower() == "weapon"
            ]
        n = 0
        seen = set()
        for obj in objs:
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None or mat.name in seen:
                    continue
                seen.add(mat.name)
                if weapon_mats.clear_pattern_on_material(mat):
                    n += 1
        self.report({"INFO"}, f"Cleared pattern on {n} material(s)")
        return {"FINISHED"} if n else {"CANCELLED"}


class ARC_OUTFITS_OT_ApplyPosePreset(Operator):
    """Apply one still pose (A-pose, idle, tool, or gun hold) to the character."""
    bl_idname = "arc_outfits.apply_pose_preset"
    bl_label = "Apply Pose"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from ..Animations import pose_presets

        pose_id = getattr(context.scene, "arc_pose_preset", "APOSE") or "APOSE"
        if pose_presets.applying():
            return {"CANCELLED"}
        pose_presets.set_applying(True)
        try:
            ok, msg = pose_presets.apply_preset(context, pose_id)
        finally:
            pose_presets.set_applying(False)
        self.report({"INFO"} if ok else {"ERROR"}, msg)
        return {"FINISHED"} if ok else {"CANCELLED"}


class ARC_OUTFITS_OT_SelectAnim(Operator):
    """Select an animation from the searchable catalog (does not import it)."""
    bl_idname = "arc_outfits.select_anim"
    bl_label = "Select Animation"
    bl_options = {"INTERNAL"}

    anim_key: StringProperty(name="Animation Key", default="")

    def execute(self, context):
        context.scene.arc_selected_anim = self.anim_key
        return {"FINISHED"}


class ARC_OUTFITS_OT_ClearAnimSearch(Operator):
    """Clear the animation search filter."""
    bl_idname = "arc_outfits.clear_anim_search"
    bl_label = "Clear Animation Search"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        context.scene.arc_anim_search = ""
        return {"FINISHED"}


class ARC_OUTFITS_OT_RefreshAnimCatalog(Operator):
    """Rebuild the animation name list from FMDex and the PSA cache folder."""
    bl_idname = "arc_outfits.refresh_anim_catalog"
    bl_label = "Refresh Animation Catalog"

    def execute(self, context):
        from ..Animations import animation_catalog as acat

        acat.invalidate()
        cache_dir = getattr(context.scene, "arc_animation_cache", "") or ""
        entries = acat.ensure_loaded(cache_dir)
        self.report({"INFO"}, f"{len(entries)} animation(s) in catalog")
        return {"FINISHED"}


class ARC_OUTFITS_OT_ApplyAnim(Operator):
    """Import the selected animation onto the active armature (one Action, not a bulk dump)."""
    bl_idname = "arc_outfits.apply_anim"
    bl_label = "Apply Animation"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from ..Animations import animation_catalog as acat
        from ..Animations import animation_import as aimp

        scene = context.scene
        key = getattr(scene, "arc_selected_anim", "") or ""
        if not key:
            self.report({"ERROR"}, "Select an animation from the list first")
            return {"CANCELLED"}
        cache_dir = getattr(scene, "arc_animation_cache", "") or ""
        entry = acat.get_entry(key, cache_dir)
        if not entry:
            self.report({"ERROR"}, f"Unknown animation: {key}")
            return {"CANCELLED"}
        ok, msg = aimp.apply_animation(
            context,
            entry=entry,
            spawn_notifies_flag=bool(getattr(scene, "arc_anim_spawn_notifies", True)),
            replace_action=bool(getattr(scene, "arc_anim_replace_action", True)),
        )
        self.report({"INFO"} if ok else {"ERROR"}, msg)
        return {"FINISHED"} if ok else {"CANCELLED"}


class ARC_OUTFITS_OT_ClearAnimProps(Operator):
    """Remove notify-spawned props / FX markers from the last applied animation."""
    bl_idname = "arc_outfits.clear_anim_props"
    bl_label = "Clear Anim Props"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from ..Animations import animation_import as aimp

        armature = aimp.resolve_target_armature(context)
        if armature is None:
            self.report({"ERROR"}, "Select an armature first")
            return {"CANCELLED"}
        aimp._clear_spawned(armature)
        self.report({"INFO"}, "Cleared animation notify props")
        return {"FINISHED"}


class ARC_OUTFITS_OT_PickAnimationCache(Operator, bpy_extras.io_utils.ImportHelper):
    """Folder of exported .psa / .notifies.json files (FModel Save Animations output)."""
    bl_idname = "arc_outfits.pick_animation_cache"
    bl_label = "Animation Cache Folder"
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={"HIDDEN"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})

    def execute(self, context):
        from ..Animations import animation_catalog as acat

        folder = _picked_folder(self)
        if not folder:
            folder = bpy.path.abspath(self.filepath)
        if not os.path.isdir(folder):
            self.report({"ERROR"}, f"Not a valid folder: {folder}")
            return {"CANCELLED"}
        context.scene.arc_animation_cache = folder
        acat.invalidate()
        self.report({"INFO"}, f"Animation cache: {folder}")
        return {"FINISHED"}


class ARC_OUTFITS_OT_ApplyLightingLook(Operator):
    """Apply the selected map scenario's color grade, sky and sun."""

    bl_idname = "arc_outfits.apply_lighting_look"
    bl_label = "Apply Lighting Look"
    bl_description = (
        "Run the game's tonemapper and color grade in the compositor and build the scenario's "
        "sky and sun (LUT strips and cloud textures are fetched from the game files)"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from ..Lighting import lighting_looks

        ok, msg = lighting_looks.apply_lighting_look(context.scene)
        self.report({"INFO"} if ok else {"ERROR"}, msg)
        return {"FINISHED"} if ok else {"CANCELLED"}


class ARC_OUTFITS_OT_ImportStudioLights(Operator):
    """Import a user-authored studio light rig exactly as authored."""

    bl_idname = "arc_outfits.import_studio_lights"
    bl_label = "Import Studio Lights"
    bl_description = (
        "Add the selected authored light rig (exact lights, transforms and light settings) to the scene; "
        "re-importing the same rig replaces it"
    )
    bl_options = {"REGISTER", "UNDO"}

    set_world: BoolProperty(name="Set World", description="Use the rig's world background", default=True)
    set_view: BoolProperty(name="Set View Transform", description="Use the rig's view transform and exposure", default=True)
    set_camera: BoolProperty(name="Camera", description="Add the camera that matches this scenario and make it the scene camera", default=True)

    def execute(self, context):
        from ..Lighting import studio_lights

        ok, msg = studio_lights.import_rig(context.scene, context.scene.arc_studio_light_rig,
                                           world=self.set_world, view=self.set_view,
                                           camera=self.set_camera, context=context)
        self.report({"INFO"} if ok else {"ERROR"}, msg)
        return {"FINISHED"} if ok else {"CANCELLED"}


class ARC_OUTFITS_OT_ClearAnimationCache(Operator):
    """Clear the extra PSA cache folder (FMDex names still list)."""
    bl_idname = "arc_outfits.clear_animation_cache"
    bl_label = "Clear Animation Cache"

    def execute(self, context):
        from ..Animations import animation_catalog as acat

        context.scene.arc_animation_cache = ""
        acat.invalidate()
        return {"FINISHED"}


class ARC_OUTFITS_OT_ClearGraphCache(Operator):
    """Delete the cached outfit material graphs; the next import rebuilds them."""
    bl_idname = "arc_outfits.clear_graph_cache"
    bl_label = "Clear Material Graph Cache"

    def execute(self, context):
        from .materials import graph_cache as gc

        removed = gc.clear_material_cache()
        self.report({"INFO"}, "Material graph cache cleared" if removed else "Material graph cache was empty")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class ARC_OUTFITS_OT_SelectNS(Operator):
    """Select a Niagara system from the searchable catalog (does not import it)."""
    bl_idname = "arc_outfits.select_ns"
    bl_label = "Select Niagara System"
    bl_options = {"INTERNAL"}

    ns_key: StringProperty(name="NS Key", default="")

    def execute(self, context):
        from ..Niagara import niagara as ncat

        scene = context.scene
        scene.arc_ns_selected = self.ns_key
        entry = ncat.get_entry(
            self.ns_key, getattr(scene, "arc_ns_catalog_root", "") or ""
        )
        if entry and entry.get("path"):
            scene.arc_ns_json_path = entry["path"]
        return {"FINISHED"}


class ARC_OUTFITS_OT_ClearNSSearch(Operator):
    """Clear the Niagara catalog search filter."""
    bl_idname = "arc_outfits.clear_ns_search"
    bl_label = "Clear NS Search"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        context.scene.arc_ns_search = ""
        return {"FINISHED"}


class ARC_OUTFITS_OT_ImportNiagaraFX(Operator):
    """Import a cooked Niagara system (NS_*.json) as inspectable scene data.

    Builds the emitter/renderer graph, real .pskx meshes where the cook names
    one, and bakes curve data-interface LUTs as F-curves. Particle motion is not
    recovered: module inputs are compiled into VectorVM / DXBC, not the cook.
    """
    bl_idname = "arc_outfits.import_niagara_fx"
    bl_label = "Import NS Effect"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})
    use_selected: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        scene = context.scene
        selected = getattr(scene, "arc_ns_selected", "") or ""
        if self.use_selected and selected and selected != "NONE":
            return self.execute(context)

        existing = bpy.path.abspath(scene.arc_ns_json_path or "")
        if os.path.isfile(existing):
            self.filepath = existing
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from ..Niagara import niagara as ncat
        from ..Niagara import niagara as niagara_fx

        scene = context.scene
        source_mode = getattr(scene, "arc_import_source", "PAK")
        if self.use_selected and not self.filepath:
            selected = scene.arc_ns_selected or ""
            entry = ncat.get_entry(selected, scene.arc_ns_catalog_root or "")
            self.filepath = ncat.resolve_entry_path(entry, scene=scene)
            if (not self.filepath and entry
                    and source_mode == "PAK"):
                from . import pak_extract

                game_dir = pak_extract.configured_dir(scene)
                usmap = pak_extract.resolve_usmap(scene) if game_dir else ""
                asset_path = entry.get("game_path") or entry.get("package") or ""
                if game_dir and usmap and asset_path:
                    code, log, _meshes = pak_extract.extract_paths(
                        game_dir, usmap, [asset_path]
                    )
                    if code != 0:
                        detail = " ".join((log or "").splitlines()[-4:])
                        self.report(
                            {'ERROR'},
                            f"NS PAK extraction failed ({code}): {detail or selected}",
                        )
                        return {'CANCELLED'}
                    self.filepath = ncat.resolve_entry_path(
                        entry, pak_extract.cache_dir()
                    )
            if selected not in ('', 'NONE') and not self.filepath:
                self.report(
                    {'ERROR'},
                    f"NS json not found: {selected}. "
                    + ("Set a FModel output root or use Import NS File."
                       if source_mode == "FILES" else
                       "Set a FModel output root or configure Steam Paks and a usmap."),
                )
                return {'CANCELLED'}
        path = bpy.path.abspath(self.filepath or scene.arc_ns_json_path or "")
        if not os.path.isfile(path):
            self.report({'ERROR'}, f"NS json not found: {path}")
            return {'CANCELLED'}
        scene.arc_ns_json_path = path

        asset_root = ncat.resolved_asset_root(
            bpy.path.abspath(scene.arc_ns_asset_root or "") or ""
        )
        if scene.arc_ns_asset_root and not asset_root:
            self.report(
                {'WARNING'},
                f"Asset root missing, meshes will be placeholders: {scene.arc_ns_asset_root}",
            )
        elif not asset_root:
            asset_root = None

        try:
            root, stats, missing = niagara_fx.import_ns_json(
                path,
                asset_root=asset_root or None,
                bake=bool(scene.arc_ns_bake_curves),
            )
        except Exception as exc:  # noqa: BLE001 - surface to the user, not the console
            self.report({'ERROR'}, f"NS import failed: {exc}")
            print(f"Arc Raiders: NS import failed for {path}: {exc}")
            return {'CANCELLED'}

        lines = [
            f"{root.name}: {stats.get('emitters', 0)} emitters, "
            f"{stats.get('renderers', 0)} renderers"
        ]
        if stats.get("vmProgramsRetained"):
            lines.append(f"{stats['vmProgramsRetained']} compiled CPU programs retained; playback not executed")
        if stats.get("curves"):
            lines.append(
                f"{stats['curves']} curves, {stats.get('curveKeys', 0)} keyframes"
            )
        if stats.get("meshImported"):
            lines.append(f"{stats['meshImported']} meshes imported")
        placeholders = sum(
            v for k, v in stats.items() if k.endswith("Placeholder")
        )
        if placeholders:
            lines.append(f"{placeholders} placeholders")
        if asset_root:
            lines.append(f"asset root: {os.path.basename(asset_root.rstrip('/\\'))}")
        lines.extend(f"missing: {m}" for m in missing)
        scene["arc_ns_last_report"] = "\n".join(lines)

        self.report({'INFO'}, lines[0])
        return {'FINISHED'}


from ..Outfit_Accessories.appearance_queue import ARC_OUTFITS_OT_AddAllAppearance, ARC_OUTFITS_OT_CancelAppearanceQueue
from ..Niagara.niagara import ARC_OUTFITS_OT_PlayNiagaraFX
from ..Outfit_Accessories.asset_organization import ARC_OUTFITS_OT_GroupByAssetType, ARC_OUTFITS_OT_GroupByOutfitSlot


_outfit_classes = (
    ARC_OUTFITS_OT_SelectNS,
    ARC_OUTFITS_OT_ClearNSSearch,
    ARC_OUTFITS_OT_ImportNiagaraFX,
    ARC_OUTFITS_OT_PlayNiagaraFX,
    ARC_OUTFITS_OT_SelectHairMI,
    ARC_OUTFITS_OT_ImportSinglePSK,
    ARC_OUTFITS_OT_ImportOutfitFolder,
    ARC_OUTFITS_OT_AssemblePropFolder,
    ARC_OUTFITS_OT_PickOutfitCSV,
    ARC_OUTFITS_OT_ClearOutfitCSV,
    ARC_OUTFITS_OT_LoadOutfit,
    ARC_OUTFITS_OT_RescanAppearance,
    ARC_OUTFITS_OT_DeepPathSearch,
    ARC_OUTFITS_OT_ClearDeepPathSearch,
    ARC_OUTFITS_OT_AddAppearancePart,
    ARC_OUTFITS_OT_GroupByAssetType,
    ARC_OUTFITS_OT_GroupByOutfitSlot,
    ARC_OUTFITS_OT_AddAllAppearance,
    ARC_OUTFITS_OT_CancelAppearanceQueue,
    ARC_OUTFITS_OT_ImportReference,
    ARC_OUTFITS_OT_UpdateBackpackColorway,
    ARC_OUTFITS_OT_UpdateHairColor,
    ARC_OUTFITS_OT_ImportColorBenchmark,
    ARC_OUTFITS_OT_LoadAllColorways,
    ARC_OUTFITS_OT_LoadAllOutfitsColorways,
    ARC_OUTFITS_OT_SelectOutfit,
    ARC_OUTFITS_OT_ClearOutfitSearch,
    ARC_OUTFITS_OT_MergeSelectedArmatures,
    ARC_OUTFITS_OT_FixMaterials,
    ARC_OUTFITS_OT_OrganizeNodes,
    ARC_OUTFITS_OT_ReimportSelected,
    ARC_OUTFITS_OT_ApplyOutfitPreset,
    ARC_OUTFITS_OT_PickManualSkinsFolder,
    ARC_OUTFITS_OT_PickManualOutfitFolder,
    ARC_OUTFITS_OT_ClearManualOutfitFolder,
    ARC_OUTFITS_OT_ConfirmPSKImport,
    ARC_OUTFITS_OT_PickPioneerRoot,
    ARC_OUTFITS_OT_ClearPioneerRoot,
    ARC_OUTFITS_OT_PickFmdexRoot,
    ARC_OUTFITS_OT_ClearFmdexRoot,
    ARC_OUTFITS_OT_PaletteSetSelected,
    ARC_OUTFITS_OT_PaletteResetSelected,
    ARC_OUTFITS_OT_PaletteExportCalibration,
    ARC_OUTFITS_OT_PaletteImportCalibration,
    ARC_OUTFITS_OT_PaletteClearCalibration,
    ARC_OUTFITS_OT_DecalExcludeApply,
    ARC_OUTFITS_OT_DecalExcludeClear,
    ARC_OUTFITS_OT_MaskDebugApply,
    ARC_OUTFITS_OT_MaskDebugClear,
    ARC_OUTFITS_OT_UpdateSelectedGroupNodes,
    ARC_OUTFITS_OT_ImportWeapon,
    ARC_OUTFITS_OT_ImportWeaponMod,
    ARC_OUTFITS_OT_ApplyWeaponSkin,
    ARC_OUTFITS_OT_ClearWeaponSkin,
    ARC_OUTFITS_OT_ApplyWeaponPattern,
    ARC_OUTFITS_OT_ClearWeaponPattern,
    ARC_OUTFITS_OT_ApplyPosePreset,
    ARC_OUTFITS_OT_SelectAnim,
    ARC_OUTFITS_OT_ClearAnimSearch,
    ARC_OUTFITS_OT_RefreshAnimCatalog,
    ARC_OUTFITS_OT_ApplyAnim,
    ARC_OUTFITS_OT_ClearAnimProps,
    ARC_OUTFITS_OT_PickAnimationCache,
    ARC_OUTFITS_OT_ClearAnimationCache,
    ARC_OUTFITS_OT_ClearGraphCache,
    ARC_OUTFITS_OT_ApplyLightingLook,
    ARC_OUTFITS_OT_ImportStudioLights,
)

# Outfits package: TCP listener only. Full Map Placement ops ship in DataRaiders-MapImporter.
from ..Outfit_Accessories import addon_line as _addon_line


def _map_operator_classes():
    """Resolve map ops without assuming a fresh map_placement module.

    Blender can keep a stale ``Arc-Asset-Importer.map_placement`` in
    ``sys.modules`` across zip installs. Prefer ``BRIDGE_OPERATOR_CLASSES``;
    fall back to listen-only classes from ``OPERATOR_CLASSES``.
    """
    if _addon_line.is_map_importer_line():
        return tuple(getattr(map_placement, "OPERATOR_CLASSES", ()) or ())
    bridge = getattr(map_placement, "BRIDGE_OPERATOR_CLASSES", None)
    if bridge:
        return tuple(bridge)
    listen = []
    for cls in getattr(map_placement, "OPERATOR_CLASSES", ()) or ():
        name = getattr(cls, "__name__", "") or ""
        if name in (
            "ARC_OUTFITS_OT_StartPlacementListener",
            "ARC_OUTFITS_OT_StopPlacementListener",
        ):
            listen.append(cls)
    return tuple(listen)


_map_ops = _map_operator_classes()

classes = _outfit_classes + _map_ops
