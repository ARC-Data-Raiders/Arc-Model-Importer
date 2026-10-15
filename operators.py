"""
Processing logic for PSK imports and model texturing
"""

import os
import re
import bpy
import bpy_extras
import mathutils
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from . import utils
from . import textures
from . import materials
from . import importing
from . import rig
from . import fmdex
from . import map_placement
from . import palette_calibration

log = utils.get_logger()


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


def process_entry(entry, *, psk=None, context=None, skip_materials=False) -> tuple:
    """Process a single PSK entry: import and set up materials.

    ``psk`` — optional pre-parsed ``psk_psa_py`` object (parse pipelining).
    ``skip_materials`` — import mesh/armature only (Phase C batch defers materials).
    """
    with utils.timed(f"process_entry:{os.path.basename(getattr(entry, 'psk_path', '') or '')}"):
        psk_path = bpy.path.abspath(entry.psk_path)
        json_path = "" if entry.skin_choice == 'NONE' else bpy.path.abspath(entry.skin_choice)
        if not json_path:
            json_path = textures.get_base_skin_json(psk_path, entry.manual_skins_folder)
        if json_path:
            json_path = textures.resolve_clothing_mi_json(json_path) or json_path
        body_variant = entry.body_choice if hasattr(entry, 'body_choice') else 'NONE'

        if not os.path.isfile(psk_path):
            return False, f"PSK not found: {psk_path}", []

        if psk_path.casefold().endswith(".uemodel"):
            from . import appearance

            hair_mi = entry.hair_mi if hasattr(entry, "hair_mi") else "NONE"
            return appearance.import_appearance_mesh(
                psk_path,
                body_variant=body_variant if body_variant else "NONE",
                hair_mi=hair_mi if hair_mi else "NONE",
                skin_json=json_path, manual_skins_folder=entry.manual_skins_folder,
                skin_choice=entry.skin_choice,
            )

        model_type = textures.detect_model_type(psk_path)
        try:
            from . import asset_domain as _ad
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
                obj["arc_materials_pending"] = 1 if skip_materials else 0
                obj["arc_body_variant"] = body_variant if body_variant else "NONE"
                obj["arc_hair_mi"] = hair_mi if hair_mi else "NONE"
            except Exception:
                pass
            if skip_materials:
                continue
            mat_status = apply_materials_to_object(
                obj, psk_path,
                body_variant=body_variant,
                hair_mi=hair_mi,
                skin_json=json_path,
                manual_skins_folder=entry.manual_skins_folder,
                skin_choice=entry.skin_choice,
            )
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
    entries = list(entries)
    if not entries:
        return []
    use_prefetch = (
        utils.io_prefetch_enabled(context)
        and importing.psk_reader_available()
        and len(entries) > 1
    )
    results = []
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
            results.append(
                process_entry(
                    entry, psk=parsed, context=context, skip_materials=skip_materials
                )
            )
    finally:
        if fut is not None:
            try:
                fut.cancel()
            except Exception:
                pass
        if executor is not None:
            executor.shutdown(wait=False)
    return results


def import_psk_with_materials(
    psk_path: str,
    *,
    body_variant: str = "NONE",
    hair_mi: str = "NONE",
    skin_json: str = "",
    manual_skins_folder: str = "",
    skin_choice: str = "NONE",
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
        from . import asset_domain as _ad
        asset_domain = _ad.classify_asset_domain(psk_path)
    except Exception:
        asset_domain = ""
    try:
        new_objects = importing.import_psk(psk_path)
    except RuntimeError as e:
        return False, str(e), []

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
        obj.data.materials.clear()
        for mat in materials:
            if mat is not None:
                obj.data.materials.append(mat)
        return True
    except Exception:
        return False


def snapshot_object_materials(obj) -> list:
    """Return Material datablocks currently on a mesh (for cache reuse)."""
    if not obj or obj.type != "MESH":
        return []
    return [slot.material for slot in obj.material_slots]


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
) -> str:
    """Apply Arc materials to an existing mesh without importing. Returns a short status."""
    label = f"apply_materials:{getattr(obj, 'name', '?')}"
    with utils.timed(label):
        return _apply_materials_to_object_impl(
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
    # Hard folder-path domains (Outfit / Environment / Gun / Arc) win over stamps.
    from . import asset_domain as _ad
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
        from . import revamped_bridge

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
        materials.setup_body_material(obj, psk_path, body_variant)
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
            from . import appearance as _appearance
            cid = getattr(bpy.context.scene, "arc_appearance_hair_color", "NONE") or "NONE"
            choice = _appearance.resolve_choice(cid) if cid != "NONE" else None
            colour_json = (choice or {}).get("path") or ""
        except Exception:
            colour_json = ""
        if not colour_json:
            # Keep stamped colour when the sidebar enum is NONE (Update Materials).
            colour_json = str(obj.get("arc_hair_color_mi", "") or "")
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
        glass_json = str(obj.get("arc_glass_skin_json", "") or "") or json_path
        with utils.timed("mat.fur_visor_hooks"):
            applied = materials.apply_embedded_visor_slots(obj, psk_path, skin_json=glass_json)
            # Fur shell / card / LOD sections beside the cloth ColorMask slot.
            materials.apply_embedded_fur_slots(obj, psk_path, skin_json=json_path)
            # Visor / goggles / *_Glass: keep ArcTexturer; wire CurvatureID_Override
            # ← Visor (Enable OFF). Glass slots with shell maps never go Visor-only.
            if not applied:
                materials.setup_visor_material(obj, psk_path, skin_json=glass_json)
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
        from . import revamped_bridge

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
        if folder_mi and textures.is_hair_cards_mi(folder_mi):
            colour_json = ""
            try:
                from . import appearance as _appearance
                cid = getattr(bpy.context.scene, "arc_appearance_hair_color", "NONE") or "NONE"
                choice = _appearance.resolve_choice(cid) if cid != "NONE" else None
                colour_json = (choice or {}).get("path") or ""
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
        if folder_mi and textures.is_hair_cards_mi(folder_mi):
            colour_json = ""
            try:
                from . import appearance as _appearance
                cid = getattr(bpy.context.scene, "arc_appearance_hair_color", "NONE") or "NONE"
                choice = _appearance.resolve_choice(cid) if cid != "NONE" else None
                colour_json = (choice or {}).get("path") or ""
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


def _duplicate_object_set(objects: list) -> list:
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
    # Link into scene temporarily; caller moves into colourway collections.
    scene_coll = bpy.context.scene.collection
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
        for c in list(o.users_collection):
            try:
                c.objects.unlink(o)
            except Exception:
                pass
        try:
            coll.objects.link(o)
        except RuntimeError:
            pass


def _snapshot_entry_skin_choices(entries) -> list:
    """Capture ``(entry, skin_choice)`` so batch IO discovery can restore after dry-runs."""
    out = []
    for entry in entries:
        try:
            out.append((entry, entry.skin_choice))
        except Exception:
            out.append((entry, "NONE"))
    return out


def _restore_entry_skin_choices(snapshot) -> None:
    for entry, choice in snapshot or ():
        try:
            entry.skin_choice = choice
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
                json_path = (
                    "" if entry.skin_choice == "NONE"
                    else bpy.path.abspath(entry.skin_choice)
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
    try:
        base_pngs = textures.scan_base_skin_textures(
            job.get("psk_path") or "",
            job.get("selected_skin_name") or "",
            job.get("manual_skins_folder") or "",
        )
    except Exception:
        base_pngs = []
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
    by_psk = {}
    for entry in entries:
        psk = bpy.path.abspath(entry.psk_path)
        by_psk[os.path.normcase(os.path.normpath(psk))] = entry
    applied = 0
    for obj in objects:
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
        json_path = "" if entry.skin_choice == "NONE" else bpy.path.abspath(entry.skin_choice)
        if not json_path:
            json_path = textures.get_base_skin_json(psk_path, entry.manual_skins_folder)
        if json_path:
            json_path = textures.resolve_part_mi_json(json_path) or json_path
        body_variant = entry.body_choice if hasattr(entry, "body_choice") else "NONE"
        hair_mi = entry.hair_mi if hasattr(entry, "hair_mi") else "NONE"
        prefetch = None
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
        apply_materials_to_object(
            obj, psk_path,
            body_variant=body_variant,
            hair_mi=hair_mi,
            skin_json=json_path,
            manual_skins_folder=entry.manual_skins_folder,
            skin_choice=entry.skin_choice,
            mi_data=mi_data,
            main_pngs=(prefetch or {}).get("main_pngs"),
            base_pngs=(prefetch or {}).get("base_pngs"),
        )
        applied += 1
    return applied


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
        from . import reference_planes

        msgs = reference_planes.import_references_for_objects(
            context, objects, roles=roles,
        )
        for m in msgs:
            print(f"Arc Raiders PSK Importer: {m}")
    except Exception as exc:
        print(f"Arc Raiders PSK Importer: reference planes skipped: {exc}")


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


def batch_import_instances(context, selected_presets) -> tuple:
    """Import one full model instance per selected outfit colourway.

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
    with utils.perf_session("batch_import", colourways=n_presets) as session:
        with utils.timed(f"batch_import_instances:{n_presets} colourways"):
            try:
                with utils.timed("preload_arc_groups"):
                    utils.preload_arc_node_groups()
            except Exception as exc:
                print(f"Arc Raiders PSK Importer: ArcTexturer preload failed (non-fatal): {exc}")

            entries = list(context.scene.arc_psk_entries)
            if not entries or not selected_presets:
                session.finish(also_print=True)
                return 0, 0, session.summary_line

            materials.clear_clothing_material_cache()
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
            group_label = importing.outfit_group_label_for_character(character_name, context)

            scene = context.scene
            parent_coll = _ensure_child_collection(scene.collection, group_label)

            # --- Kick IO prefetch (overlap with PSK import) ---
            io_executor = None
            io_futures = None
            jobs = []
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

            # --- Import mesh once, copy remaining colourways (materials deferred) ---
            template_objs = None
            pending = []  # (preset_idx, preset_name, preset_path, inst_objs)

            for preset_idx, (preset_name, preset_path) in enumerate(selected_presets):
                importing.apply_outfit_preset(context, preset_path)

                if preset_idx == 0 or template_objs is None:
                    inst_objs = []
                    for ok, msg, new_objs in _process_entries_pipelined(
                        entries, context, skip_materials=True
                    ):
                        inst_objs.extend(new_objs)
                        if ok:
                            print(f"    [{preset_name}] {msg}")
                        else:
                            print(f"    [{preset_name}] FAILED: {msg}")
                    if not inst_objs:
                        continue
                    with utils.timed("fix_rig"):
                        rig.fix_rig_all(inst_objs, merge=any_clothing, model_type=dominant_type)
                    inst_objs = [o for o in inst_objs if _still_alive(o)]
                    with utils.timed("rename_outfit"):
                        _rename_outfit_base_objects(inst_objs, character_name)
                    template_objs = list(inst_objs)
                else:
                    with utils.timed(f"duplicate_colourway:{preset_name}"):
                        inst_objs = _duplicate_object_set(template_objs)
                    if not inst_objs:
                        continue
                    print(
                        f"    [{preset_name}] Copied {len(inst_objs)} object(s) from template "
                        f"(materials deferred)"
                    )

                pending.append((preset_idx, preset_name, preset_path, inst_objs))

            # --- Join IO + warm image cache on main thread ---
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

            with utils.timed("batch_expand_image_paths"):
                path_union = materials.expand_clothing_image_paths(path_union, io_cache)
            if path_union:
                with utils.timed(f"batch_prefetch_images:{len(path_union)}"):
                    n_img = materials.prefetch_images(path_union, force_decode=True)
                    log.info("prefetch_images loaded %d / %d path(s)", n_img, len(path_union))
            else:
                log.info("prefetch_images skipped (0 discovered paths)")

            # --- Build materials one colourway at a time; defer collection links ---
            pending_links = []
            parts_total = 0
            materials.set_batch_material_mode(True)
            try:
                for preset_idx, preset_name, preset_path, inst_objs in pending:
                    importing.apply_outfit_preset(context, preset_path)
                    with utils.timed(f"batch_materials:{preset_name}"):
                        n_mat = _reapply_materials_for_preset(
                            inst_objs, entries, io_cache=io_cache, preset_idx=preset_idx
                        )
                    parts_total += n_mat
                    print(
                        f"    [{preset_name}] Applied materials on {n_mat} mesh(es)"
                    )
                    color_coll = _ensure_child_collection(parent_coll, str(preset_name))
                    pending_links.append((inst_objs, color_coll))
            finally:
                materials.set_batch_material_mode(False)

            with utils.timed("link_collections"):
                for inst_objs, color_coll in pending_links:
                    _link_objects_to_collection(inst_objs, color_coll)

            with utils.timed("view_layer_update"):
                try:
                    context.view_layer.update()
                except Exception:
                    pass

            instances = 0
            instance_groups = []
            with utils.timed("layout_slots"):
                from . import import_layout as _layout

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
                    )
                    instances += 1
                    instance_groups.append(alive)
                    if _layout.offset_enabled(context.scene):
                        print(
                            f"    [{_preset_name}] slot {slot} @ "
                            f"X={_layout.slot_world_x(slot, context.scene):.1f} m"
                        )

            with utils.timed("post_import_ux"):
                _post_import_outfit_ux(context, instance_groups)

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
    raws = [obj.name]
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
        stamped = bpy.path.abspath(str(stamped))
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
            skin_json = bpy.path.abspath(skin_json)
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
    filter_glob: StringProperty(default="*.psk;*.pskx", options={'HIDDEN'})

    def invoke(self, context, event):
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
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        folder = bpy.path.abspath(self.directory)
        if not os.path.isdir(folder):
            self.report({'ERROR'}, f"Not a valid folder: {folder}")
            return {'CANCELLED'}
        psks, skipped_pskx = utils.find_psks_in_folder(folder)
        if not psks:
            self.report({'ERROR'}, f"No .psk/.pskx files found in folder (or its subfolders): {folder}")
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
        from . import prop_assemble

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
        from .properties import rebuild_csv_outfit_map
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
        from .properties import rebuild_csv_outfit_map
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
    from . import pak_extract
    if pak_extract.configured_dir(scene) or not root:
        if not pak_extract.configured_dir(scene):
            return False, row, label, [], "Set the PioneerGame root folder or the Paks folder."
        from . import outfit_catalog
        rels = outfit_catalog.resolve_pak_outfit_paths(scene, row)
        meshes = pak_extract.ensure_rels(scene, rels) if rels else []
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
                f"Arc Raiders PSK Importer: Load Outfit '{label}' queued {psk_count} "
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
        from . import appearance

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
        from . import appearance

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

    def execute(self, context):
        from . import appearance as _appearance

        cid = getattr(context.scene, "arc_appearance_hair_color", "NONE") or "NONE"
        if cid == "NONE":
            self.report({'ERROR'}, "Select a Hair Color first")
            return {'CANCELLED'}
        choice = _appearance.resolve_choice(cid)
        colour_json = (choice or {}).get("path") or ""
        if not colour_json or not os.path.isfile(colour_json):
            self.report({'ERROR'}, f"Hair Color MI not found for '{cid}'")
            return {'CANCELLED'}

        copy_lashes = bool(
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
            mtype = str(obj.get("arc_model_type", "") or "").lower()
            name_l = (obj.name or "").lower()
            if mtype == "hair" or "hair" in name_l or "facial" in name_l:
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
                obj["arc_hair_color_mi"] = os.path.abspath(colour_json)
                obj["arc_hair_mi"] = os.path.abspath(hair_json)
            except Exception:
                pass
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
                        if _is_head_mesh(other):
                            head_targets.append(other)
            for head in head_targets:
                try:
                    head["arc_hair_color_mi"] = os.path.abspath(colour_json)
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

        try:
            _character.sync_face_shader_hair(
                "",
                _character._resolve_scene_hair_color_rgba(colour_json),
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
        from . import appearance

        from . import pak_extract

        if not utils.get_pioneer_root() and not pak_extract.configured_dir(context.scene):
            self.report({'ERROR'}, "Set PioneerGame Folder or the Paks folder in Settings")
            return {'CANCELLED'}

        role = {
            "HAIR": "Hair",
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

        count, msgs, objs = appearance.import_appearance_parts(
            context,
            roles={role},
            attach_to=merge_target,
            replace_selected=replace,
        )
        for m in msgs:
            print(f"Arc Raiders PSK Importer: {m}")
        if count <= 0:
            self.report({'ERROR'}, "; ".join(msgs[:3]) or f"Nothing imported ({role})")
            return {'CANCELLED'}
        if role in ("Head", "Hair"):
            _maybe_import_references(context, objs, roles={role})
        verb = "Replaced" if replace else "Added" if attach_to is not None else "Imported"
        if attach_to is not None:
            self.report(
                {'INFO'},
                f"{verb} {role} on '{attach_to.name}'. " + "; ".join(msgs[:4]),
            )
        else:
            self.report(
                {'INFO'},
                f"{verb} {role}. " + "; ".join(msgs[:4]),
            )
        return {'FINISHED'}


class ARC_OUTFITS_OT_ImportReference(Operator):
    """Place UI reference image planes for the selected outfit / head / hair."""
    bl_idname = "arc_outfits.import_reference"
    bl_label = "Import Reference"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from . import reference_planes

        made, msgs = reference_planes.import_references_from_scene(context)
        for m in msgs:
            print(f"Arc Raiders PSK Importer: {m}")
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
                print(f"Arc Raiders PSK Importer: {m}")
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
        root = utils.get_pioneer_root()
        if root:
            return bool(importing.colorways_for_outfit_row(root, row))
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
                f"Arc Raiders PSK Importer: Load All Colorways '{label}' — "
                f"{len(presets)} colourway(s), {len(missing)} missing part(s)"
            )

        instances, parts, _perf_summary = batch_import_instances(context, presets)
        context.scene.arc_psk_entries.clear()
        context.scene.arc_outfit_selections.clear()
        if instances <= 0:
            self.report({'WARNING'}, f"No colourways imported for '{label}'.")
            return {'CANCELLED'}
        self.report(
            {'INFO'},
            f"Imported {instances} colourway(s) ({parts} part(s)) for '{label}'.",
        )
        return {'FINISHED'}


class ARC_OUTFITS_OT_SelectOutfit(Operator):
    """Select an outfit from the searchable list."""
    bl_idname = "arc_outfits.select_outfit"
    bl_label = "Select Outfit"
    outfit_key: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        context.scene.arc_selected_outfit = self.outfit_key
        context.scene.arc_selected_outfit_browse = self.outfit_key
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
        from . import prop_assemble

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
    bl_label = "PSKImporter_SIL_AI"
    bl_options = {'REGISTER', 'UNDO'}

    def _dialog_cache(self, context):
        """Operator + module cache; rebuild only when entry/manual paths change."""
        from . import properties as props
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
        from . import properties as props
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

            layout.separator()

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
        from . import properties as props
        props.clear_confirm_dialog_cache()
        self._scan_cache = None

        try:
            utils.preload_arc_node_groups()
        except Exception as exc:
            print(f"Arc Raiders PSK Importer: ArcTexturer preload failed (non-fatal): {exc}")

        selected = []
        if importing.queue_supports_outfit_batch(context):
            selected = [(s.preset_name, bpy.path.abspath(s.json_path))
                        for s in context.scene.arc_outfit_selections if s.selected]
        if selected:
            instances, parts, _perf_summary = batch_import_instances(context, selected)
            self.report(
                {'INFO'},
                f"Arc Raiders: batch imported {instances} instance(s) ({parts} part(s) total).",
            )
            # Perf drill-down reports disabled in final builds (see utils._PERF_LOGGING_ENABLED).
            # if _perf_summary:
            #     self.report({'INFO'}, _perf_summary)
            # else:
            #     self.report({'INFO'}, f"Perf log: {utils.perf_log_path()}")
            context.scene.arc_psk_entries.clear()
            context.scene.arc_outfit_selections.clear()
            return {'FINISHED'}

        entries = context.scene.arc_psk_entries
        total = len(entries)
        ok_count = 0
        all_new_objs = []

        for ok, msg, new_objs in _process_entries_pipelined(entries, context):
            all_new_objs.extend(new_objs)
            if ok:
                ok_count += 1
                self.report({'WARNING'} if "FAIL" in msg else {'INFO'}, msg)
            else:
                self.report({'WARNING'}, msg)

        types = [textures.detect_model_type(bpy.path.abspath(e.psk_path)) for e in entries]
        any_clothing = any(t == "clothing" for t in types)
        cosmetic_import = any(t in _REFERENCE_MODEL_TYPES for t in types)
        dominant_type = "weapon" if all(t == "weapon" for t in types) else ""
        rig.fix_rig_all(all_new_objs, merge=any_clothing, model_type=dominant_type)
        all_new_objs = [o for o in all_new_objs if _still_alive(o)]
        char = ""
        for entry in entries:
            char = importing.get_character_name(bpy.path.abspath(entry.psk_path))
            if char:
                break
        if char:
            _rename_outfit_base_objects(all_new_objs, char)

        # One model = whole imported outfit (same grouping as a single batch instance).
        if all_new_objs and cosmetic_import:
            try:
                from . import import_layout as _layout

                for o in all_new_objs:
                    if _still_alive(o) and not o.get("arc_assembly_role"):
                        try:
                            o["arc_assembly_role"] = "Outfit"
                        except Exception:
                            pass
                slot = _layout.place_imported(
                    [o for o in all_new_objs if _still_alive(o)],
                    "Outfit",
                    scene=context.scene,
                )
                if _layout.offset_enabled(context.scene):
                    self.report(
                        {'INFO'},
                        f"Outfit slot {slot} @ X={_layout.slot_world_x(slot, context.scene):.1f} m",
                    )
            except Exception as exc:
                print(f"Arc Raiders PSK Importer: slot layout skipped: {exc}")
            _post_import_outfit_ux(context, [all_new_objs], references=True)
        elif all_new_objs:
            layout_msg = _apply_folder_prop_layout(context)
            _hide_imported_armatures(all_new_objs)
            _frame_viewport_on_outfit_models(context, [all_new_objs])
            if layout_msg:
                self.report({'INFO'}, layout_msg)

        self.report({'INFO'}, f"Arc Raiders: {ok_count}/{total} part(s) imported.")
        context.scene.arc_psk_entries.clear()
        context.scene.arc_outfit_selections.clear()
        return {'FINISHED'}


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
            from . import animation_catalog as acat
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
                    from . import mask_debug as _md
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
                    from . import mask_debug as _md
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
                    from . import mask_debug as _md
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
                    from . import mask_debug as _md
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
        from . import mask_debug

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
        from . import mask_debug
        from . import properties as _props

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
        from . import group_hotswap

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
        from . import weapon_catalog as wcat

        scene = context.scene
        key = getattr(scene, "arc_weapon_key", "NONE") or "NONE"
        if key == "NONE":
            self.report({"ERROR"}, "Select a weapon from the ST catalog first")
            return {"CANCELLED"}
        entry = wcat.weapon_by_key(key)
        if not entry:
            self.report({"ERROR"}, f"Unknown weapon key: {key}")
            return {"CANCELLED"}
        pioneer = utils.get_pioneer_root() or getattr(scene, "arc_pioneer_root", "") or ""
        folder = wcat.resolve_weapon_folder(pioneer, entry)
        if not folder:
            from . import pak_extract

            stem = entry.get("stem") or ""
            kind = entry.get("kind") or "Firearms"
            meshes = pak_extract.ensure_rels(scene, [f"Items/{kind}/{stem}"]) if stem else []
            if not meshes:
                root_hint = pioneer or "paks"
                self.report(
                    {"ERROR"},
                    f"Content folder missing: Items/{kind}/{stem} ({root_hint}). "
                    f"Set PioneerGame Folder or the Paks folder in Settings.",
                )
                return {"CANCELLED"}
            n = _queue_mesh_paths(context, meshes)
            if not n:
                self.report({"ERROR"}, f"No mesh extracted for {entry.get('name') or stem}")
                return {"CANCELLED"}
            bpy.ops.arc_outfits.confirm_psk_import("INVOKE_DEFAULT")
            self.report({"INFO"}, f"Imported {entry.get('name')} ({n} mesh(es)) from paks")
            return {"FINISHED"}
        before_arms = {o.name for o in bpy.data.objects if o.type == "ARMATURE"}
        n, _skipped = _queue_folder_psks(context, folder)
        if not n:
            self.report({"ERROR"}, f"No .psk/.pskx in {folder}")
            return {"CANCELLED"}
        # Stamp Weapons-panel skin onto import entries (Skins/<Colourway>/MI_*.json).
        skin_name = getattr(scene, "arc_weapon_skin", "NONE") or "NONE"
        skin_mi = wcat.resolve_weapon_skin_mi(pioneer, entry, skin_name)
        if skin_mi:
            for ent in scene.arc_psk_entries:
                try:
                    ent.skin_choice = skin_mi
                except Exception:
                    pass
        bpy.ops.arc_outfits.confirm_psk_import("INVOKE_DEFAULT")
        new_arms = [
            o
            for o in bpy.data.objects
            if o.type == "ARMATURE" and o.name not in before_arms
        ]
        for arm in new_arms:
            arm["arc_model_type"] = "weapon"
        if new_arms:
            scene.arc_last_weapon_armature = new_arms[-1].name
        skin_note = f", skin {skin_name}" if skin_mi else ""
        self.report({"INFO"}, f"Imported {entry.get('name')} ({n} mesh(es){skin_note})")
        return {"FINISHED"}


class ARC_OUTFITS_OT_ImportWeaponMod(Operator):
    """Import selected ST weapon mod and bone-parent snap to the active gun."""
    bl_idname = "arc_outfits.import_weapon_mod"
    bl_label = "Import Weapon Mod"
    bl_options = {"REGISTER", "UNDO"}

    mod_type: StringProperty(name="Mod Type", default="Muzzle")

    def execute(self, context):
        from . import weapon_catalog as wcat
        from . import rig

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
        from . import weapon_catalog as wcat
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
        from . import weapon_catalog as wcat
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
        from . import pose_presets

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
        from . import animation_catalog as acat

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
        from . import animation_catalog as acat
        from . import animation_import as aimp

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
        from . import animation_import as aimp

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
        from . import animation_catalog as acat

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
    """Apply Kodak LUT + paired HDRI from Pioneer/Lighting for the selected look."""

    bl_idname = "arc_outfits.apply_lighting_look"
    bl_label = "Apply Lighting Look"
    bl_description = (
        "Set World HDRI and compositor Kodak RGBTable16x1 LUT from the selected "
        "Lighting Look (requires PioneerGame Folder)"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import lighting_looks

        ok, msg = lighting_looks.apply_lighting_look(context.scene)
        self.report({"INFO"} if ok else {"ERROR"}, msg)
        return {"FINISHED"} if ok else {"CANCELLED"}


class ARC_OUTFITS_OT_ClearAnimationCache(Operator):
    """Clear the extra PSA cache folder (FMDex names still list)."""
    bl_idname = "arc_outfits.clear_animation_cache"
    bl_label = "Clear Animation Cache"

    def execute(self, context):
        from . import animation_catalog as acat

        context.scene.arc_animation_cache = ""
        acat.invalidate()
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
        from . import niagara_catalog as ncat

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

    def invoke(self, context, event):
        from . import niagara_catalog as ncat

        scene = context.scene
        selected = getattr(scene, "arc_ns_selected", "") or ""
        entry = ncat.get_entry(
            selected, getattr(scene, "arc_ns_catalog_root", "") or ""
        )
        if entry and entry.get("path") and os.path.isfile(entry["path"]):
            self.filepath = entry["path"]
            return self.execute(context)

        existing = bpy.path.abspath(scene.arc_ns_json_path or "")
        if os.path.isfile(existing):
            self.filepath = existing
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from . import niagara_catalog as ncat
        from . import niagara_fx

        scene = context.scene
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


_outfit_classes = (
    ARC_OUTFITS_OT_SelectNS,
    ARC_OUTFITS_OT_ClearNSSearch,
    ARC_OUTFITS_OT_ImportNiagaraFX,
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
    ARC_OUTFITS_OT_ImportReference,
    ARC_OUTFITS_OT_UpdateHairColor,
    ARC_OUTFITS_OT_ImportColorBenchmark,
    ARC_OUTFITS_OT_LoadAllColorways,
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
    ARC_OUTFITS_OT_ApplyLightingLook,
)

# Outfits package: TCP listener only. Full Map Placement ops ship in DataRaiders-MapImporter.
from . import addon_line as _addon_line


def _map_operator_classes():
    """Resolve map ops without assuming a fresh map_placement module.

    Blender can keep a stale ``DataRaiders-Outfits.map_placement`` in
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
