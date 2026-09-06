"""
Material setup — dispatch domain (split from materials.py monolith).
"""
from __future__ import annotations

import os
import re
import json
import struct
import math
import difflib

import bpy
from mathutils import Vector

from .. import utils
from .. import textures
from .. import palette_calibration
from ..properties import BODY_ALBEDO, BODY_NORMAL

from .common import (
    CTX_MAP,
    _SHARED_MI_MATERIALS,
    _norm_path_key,
    _parse_flat_mi_json,
    _resolve_mi_json_path,
    _stamp_mi_family,
    material_has_map_forbidden_content,
    path_allowed_for_context,
    wipe_out_of_context_map_slot,
)
from .classify import (
    FAMILY_DECAL,
    FAMILY_EMISSIVE,
    FAMILY_ENVIRONMENT,
    FAMILY_FOLIAGE,
    FAMILY_GLASS,
    FAMILY_METAL,
    FAMILY_ROAD,
    FAMILY_SAND,
    FAMILY_SCAN,
    FAMILY_TARP,
    FAMILY_TRIMMAP,
    FAMILY_WATER,
    FAMILY_WEAPON,
    _needs_map_decal_mask_rebuild,
    _needs_proptrim_or_glass_rebuild,
    _needs_env_props_rebuild,
    _needs_trim_setup_rebuild,
    _needs_water_setup_rebuild,
    classify_mi_family,
)
from .enemy import (
    _setup_enemy_scan_display_material,
)
from .weapon import (
    _setup_weapon_emissive_material,
    _setup_weapon_main_material,
)
from .environment import (
    _setup_environment_material,
    _setup_foliage_material,
    _setup_glass_env_material,
    _setup_map_decal_material,
    _setup_sand_material,
    _setup_simple_material,
    _setup_tarp_material,
    _setup_water_material,
)



def _get_or_build_shared_mi_material(
    mi_stem: str,
    mi_path: str,
    psk_path: str,
    slot_lower: str = "",
):
    """Return a shared Material datablock for this MI JSON (build once).

    Cache key is the MI JSON path. Stale datablocks that were mis-routed through
    the enemy-decal family (env NAO trims) are rebuilt when classification now
    disagrees.
    """
    if not mi_path or not os.path.isfile(mi_path):
        return None
    key = _norm_path_key(mi_path)
    stem = (mi_stem or os.path.splitext(os.path.basename(mi_path))[0] or "MI").strip()
    slot_l = (slot_lower or stem).lower()

    def _expected_family() -> str:
        try:
            mi_probe = _parse_flat_mi_json(mi_path)
        except Exception:
            mi_probe = {}
        return classify_mi_family(mi_probe, stem.lower(), slot_l)

    def _stale_decal_misclass(mat) -> bool:
        """Env trim previously stamped as enemy/map decal — must rebuild."""
        fam = str(mat.get("arc_mi_family") or "")
        if fam != FAMILY_DECAL:
            return False
        expected = _expected_family()
        return expected != FAMILY_DECAL

    def _stale_shared(mat) -> bool:
        return (
            _stale_decal_misclass(mat)
            or _needs_map_decal_mask_rebuild(mat, mi_path)
            or _needs_trim_setup_rebuild(mat, mi_path)
            or _needs_proptrim_or_glass_rebuild(mat, mi_path, stem)
            or _needs_env_props_rebuild(mat, mi_path)
            or _needs_water_setup_rebuild(mat, mi_path)
        )

    mat = _SHARED_MI_MATERIALS.get(key)
    if mat is not None:
        try:
            _ = mat.name
            if _stale_shared(mat):
                _SHARED_MI_MATERIALS.pop(key, None)
                try:
                    mat.name = f"{mat.name}_stale_decal"
                except Exception:
                    pass
                mat = None
            else:
                return mat
        except ReferenceError:
            _SHARED_MI_MATERIALS.pop(key, None)
            mat = None

    low = stem.lower()
    if low.startswith("mi_") or low.startswith("m_"):
        mat_name = stem
    else:
        mat_name = f"MI_{stem}"
    # Avoid colliding with a leftover empty/placeholder material of the same name
    existing = bpy.data.materials.get(mat_name)
    if existing is not None and str(existing.get("arc_mi_path", "") or "") == key:
        if _stale_shared(existing):
            try:
                existing.name = f"{existing.name}_stale_decal"
            except Exception:
                pass
        else:
            _SHARED_MI_MATERIALS[key] = existing
            return existing

    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    try:
        mat["arc_mi_path"] = key
        mat["arc_mi_stem"] = stem
    except Exception:
        pass
    try:
        _dispatch_weapon_slot_material(
            mat,
            mi_path,
            psk_path or "",
            stem.lower(),
            (slot_lower or stem).lower(),
        )
    except Exception as e:
        print(
            f"Arc Raiders PSK Importer: Failed shared MI '{stem}' "
            f"({mi_path}): {e}"
        )
        try:
            bpy.data.materials.remove(mat)
        except Exception:
            pass
        return None
    _SHARED_MI_MATERIALS[key] = mat
    return mat



def _dispatch_weapon_slot_material(mat, mi_path: str, psk_path: str, mi_stem_lower: str, slot_lower: str):
    """Route a single weapon/enemy/map/item SK material slot to its family setup."""
    log = utils.get_logger()
    try:
        mi_probe = _parse_flat_mi_json(mi_path)
        family = classify_mi_family(mi_probe, mi_stem_lower, slot_lower)
        _stamp_mi_family(mat, family)
        log.debug(
            "MI family '%s' → %s (slot=%s)",
            os.path.basename(mi_path), family, slot_lower or "(none)",
        )

        if family == FAMILY_EMISSIVE:
            _setup_weapon_emissive_material(mat, mi_path)
        elif family == FAMILY_SCAN:
            _setup_enemy_scan_display_material(mat, mi_path)
        elif family == FAMILY_DECAL:
            _setup_map_decal_material(mat, mi_path, psk_path)
        elif family == FAMILY_GLASS:
            _setup_glass_env_material(mat, mi_path, psk_path)
        elif family == FAMILY_WATER:
            _setup_water_material(mat, mi_path, psk_path)
        elif family == FAMILY_SAND:
            _setup_sand_material(mat, mi_path, psk_path)
        elif family == FAMILY_FOLIAGE:
            _setup_foliage_material(mat, mi_path, psk_path)
        elif family == FAMILY_TARP:
            _setup_tarp_material(mat, mi_path, psk_path)
        elif family == FAMILY_TRIMMAP:
            from .env_props import setup_trim_map_material

            setup_trim_map_material(mat, mi_path, psk_path)
        elif family in (FAMILY_ROAD, FAMILY_METAL, FAMILY_ENVIRONMENT):
            _setup_environment_material(mat, mi_path, psk_path, family=family)
        elif family == FAMILY_WEAPON:
            _setup_weapon_main_material(mat, mi_path, psk_path)
        else:
            _setup_simple_material(mat, mi_path, psk_path)
    except Exception as exc:
        # Soft-fail: never crash Stage 2 on a single bad MI
        log.error(
            "Material setup failed for '%s' (%s): %s — falling back to simple",
            os.path.basename(mi_path or ""), mi_stem_lower, exc,
        )
        try:
            _setup_simple_material(mat, mi_path, psk_path)
        except Exception as exc2:
            log.error("Simple fallback also failed for '%s': %s", mi_path, exc2)



def setup_part_folder_mi_material(obj, mi_path: str, psk_path: str = "") -> bool:
    """Apply a mesh-folder MI (often thin SimplePBR with shared ColorA/B/C, no Skins/).

    Used when ``get_base_skin_json`` finds ``MI_*.json`` beside the PSK because there
    is no per-colorway Skins tree (e.g. AntlerShaman PonchoFringe).
    """
    if obj is None or not mi_path or not os.path.isfile(mi_path):
        return False
    stem = os.path.splitext(os.path.basename(mi_path))[0]
    stem_l = stem.lower()
    # Skip pure clothing ColorMask MIs — those need ArcTexturer + occlusion.
    if stem_l.startswith("mi_visor_") or "colormask" in stem_l:
        return False
    mat = None
    if getattr(obj, "material_slots", None):
        for slot in obj.material_slots:
            if slot.material is not None:
                mat = slot.material
                break
    if mat is None:
        mat = bpy.data.materials.new(name=f"{getattr(obj, 'name', 'Part')}_{stem}"[:63])
        mat.use_nodes = True
        try:
            obj.active_material = mat
        except Exception:
            pass
    else:
        mat.use_nodes = True
    slot_lower = ""
    try:
        if obj.material_slots:
            slot_lower = (obj.material_slots[0].name or "").lower()
    except Exception:
        pass
    _dispatch_weapon_slot_material(mat, mi_path, psk_path or "", stem_l, slot_lower)
    try:
        mat["arc_mi_path"] = os.path.abspath(mi_path)
        mat["arc_mi_stem"] = stem
    except Exception:
        pass
    print(
        f"Arc Raiders: Applied part-folder MI '{stem}' on '{getattr(obj, 'name', '?')}' "
        f"(no Skins/ — shared colorway params)"
    )
    return True



def _mi_stem_from_blender_name(name: str) -> str:
    """Extract an MI_* stem from a Blender / BlenderUMap material name.

    Handles names like:
      MI_CatBed_01_A
      MI_CatBed_01_A.mat
      MI_CatBed_01_A.001
    """
    if not name:
        return ""
    stem = name.strip()
    stem = re.sub(r"\.mat$", "", stem, flags=re.IGNORECASE)
    stem = stem.split(".")[0].strip()
    # Skip materials we already created: Obj_MI_Foo_Mat
    if stem.lower().endswith("_mat") and "_mi_" in stem.lower():
        return ""
    if stem.lower().startswith("mi_"):
        return stem
    m = re.search(r"(MI_[A-Za-z0-9_]+)", stem, flags=re.IGNORECASE)
    return m.group(1) if m else ""



def fix_object_materials_from_mi_slots(
    obj, asset_folder: str = "", *, context: str = CTX_MAP,
) -> int:
    """Rebuild shaders for slots whose material names look like MI_*.

    Used when an object (e.g. from BlenderUMap) has MI-named materials but no
    sibling SK/SM mesh JSON is available. Returns the number of slots fixed.
    Shared MI datablocks are reused across meshes (map Stage 2).

    ``context`` defaults to map — never resolve Characters/ via FMDex basename.
    """
    if not obj or obj.type != "MESH":
        return 0

    from .. import fmdex
    from .. import textures as texmod

    log = utils.get_logger()
    fixed = 0
    for slot in obj.material_slots:
        if not slot.material:
            continue
        # Already wired to a shared Arc MI — skip rebuild unless out of context
        existing_key = str(slot.material.get("arc_mi_path", "") or "")
        resolve_stem = _mi_stem_from_blender_name(slot.material.name)
        if context == CTX_MAP and material_has_map_forbidden_content(slot.material):
            # Cosmetic MI and/or Characters TEX_IMAGE on a map prop — wipe the
            # slot datablock (do not leave belt/helmet Image Texture nodes).
            try:
                log.warning(
                    "clearing out-of-context MI '%s' on map mesh '%s'",
                    existing_key or slot.material.name, obj.name,
                )
            except Exception:
                pass
            wipe_out_of_context_map_slot(obj, slot)
            # Only re-resolve if the *original* MI stem still maps to an allowed path.
            mi_stem = resolve_stem
            if not mi_stem:
                continue
            # Fall through to resolve below (CTX_MAP will refuse Characters).
        elif existing_key:
            if path_allowed_for_context(existing_key, context):
                fixed += 1
                continue
            # Stamped path failed context gate without TEX hits — wipe anyway.
            try:
                log.warning(
                    "clearing out-of-context MI '%s' on map mesh '%s'",
                    existing_key, obj.name,
                )
            except Exception:
                pass
            wipe_out_of_context_map_slot(obj, slot)
            mi_stem = resolve_stem
            if not mi_stem:
                continue
        else:
            mi_stem = resolve_stem
            if not mi_stem:
                continue
        if not mi_stem:
            continue

        mi_path = ""
        folder = asset_folder or ""
        tags = []

        # FMDex: basename / full package path → exported JSON (context-gated)
        pkg, tags = fmdex.lookup_asset_path(mi_stem)
        if pkg:
            game_path = fmdex.package_to_game_path(pkg)
            if game_path and path_allowed_for_context(game_path, context):
                mi_path = texmod.find_asset_from_object_path(game_path, ".json")
            if not mi_path and context != CTX_MAP:
                mi_path = fmdex.resolve_export_file(
                    mi_stem, ".json", context=context, allow_basename_walk=True,
                )
            elif not mi_path and game_path:
                # Map: ObjectPath-only from FMDex full package keys — no basename walk
                mi_path = texmod.find_asset_from_object_path(game_path, ".json")
            if mi_path and not path_allowed_for_context(mi_path, context):
                mi_path = ""
            if mi_path:
                folder = os.path.dirname(mi_path)
                log.debug(
                    "FMDex MI '%s' → %s tags=%s", mi_stem, mi_path, tags
                )

        if not mi_path:
            mi_path = _resolve_mi_json_path(
                mi_stem, "", folder or asset_folder, context=context,
            )
            if mi_path:
                folder = os.path.dirname(mi_path)

        if not mi_path:
            log.warning(
                "MI JSON not found for '%s' on '%s' (folder=%s, ctx=%s, %s)",
                slot.material.name, obj.name, asset_folder or "(none)", context,
                fmdex.fmdex_summary_for_report(),
            )
            continue

        psk_hint = os.path.join(folder, "mesh.psk") if folder else ""
        try:
            tags_l = [t.lower() for t in (tags or [])]
            if tags_l and "materialinstanceconstant" not in tags_l and not any(
                "material" in t for t in tags_l
            ):
                log.debug(
                    "FMDex tags for '%s' are %s — still routing as MI slot",
                    mi_stem, tags,
                )
            mat = _get_or_build_shared_mi_material(
                mi_stem, mi_path, psk_hint, mi_stem.lower(),
            )
            if mat is None:
                continue
            slot.material = mat
            fixed += 1
        except Exception as e:
            log.error("Failed fixing MI slot '%s' on '%s': %s", mi_stem, obj.name, e)
    return fixed

