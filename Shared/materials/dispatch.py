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
import hashlib

import bpy
from mathutils import Vector

from .. import utils
from .. import textures
from ...Outfit_Accessories import palette_calibration
from ...Outfit_Accessories.properties import BODY_ALBEDO, BODY_NORMAL

from .common import (
    CTX_MAP,
    _SHARED_MI_MATERIALS,
    _norm_path_key,
    _parse_flat_mi_json,
    _resolve_mi_json_path,
    _stamp_mi_family,
    _uemodel_section_materials,
    material_has_map_forbidden_content,
    path_allowed_for_context,
    wipe_out_of_context_map_slot,
)
from .classify import (
    FAMILY_DECAL,
    FAMILY_CRT,
    FAMILY_EMISSIVE,
    FAMILY_ENEMY,
    FAMILY_ENVIRONMENT,
    FAMILY_FOLIAGE,
    FAMILY_FLIPBOOK,
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
from .enemy_surface import setup_enemy_surface_material
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


PLACEHOLDER_LABEL = "Placeholder: no game material found"


def mark_placeholder(mat):
    """Flag a default 2-node material left on a slot that found no game material.

    Adds one labelled frame around its nodes (the shader result is unchanged) and sets
    mat["arc_placeholder"]. Graphs with more than 2 nodes are left alone. Returns True when marked."""
    nt = getattr(mat, "node_tree", None) if mat is not None else None
    if nt is None or getattr(mat, "library", None) is not None:
        return False
    body = [n for n in nt.nodes if n.bl_idname != "NodeFrame"]
    if len(body) > 2:
        return False
    if mat.get("arc_placeholder") and any(n.bl_idname == "NodeFrame" for n in nt.nodes):
        return True
    frame = nt.nodes.new("NodeFrame")
    frame.name = "DR.Placeholder"
    frame.label = PLACEHOLDER_LABEL
    frame.label_size = 20
    for n in body:
        n.parent = frame
    mat["arc_placeholder"] = True
    return True


def _get_or_build_shared_mi_material(
    mi_stem: str,
    mi_path: str,
    psk_path: str,
    slot_lower: str = "",
    obj=None,
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
    mi_probe = _parse_flat_mi_json(mi_path)
    from .native.native_data import guard_known_source
    current_native_defaults=guard_known_source(mi_path,mi_probe)
    stamps = []
    for source in (mi_path, os.path.splitext(mi_path)[0] + ".arc-material.json"):
        try:
            info = os.stat(source)
            stamps.append((source, info.st_mtime_ns, info.st_size))
        except OSError:
            pass
    signature = hashlib.sha256(json.dumps(
        {"parameters": mi_probe, "sources": stamps, "current_native_defaults": current_native_defaults,
         "lineage": textures.material_lineage_signature(mi_path)},
        sort_keys=True, default=str,
    ).encode("utf-8")).hexdigest()

    def _expected_family() -> str:
        from .native import shader_parity
        if shader_parity.planned(mi_path):
            return "shader_parity"
        from .glass_native import read_parameters
        if read_parameters(mi_path) is not None:
            return "character_glass_opaque"
        parent_l = str(mi_probe.get("parent") or "").lower()
        if stem.lower().startswith("mi_beacon") or parent_l.endswith("m_beacon"):
            return "beacon"
        family = classify_mi_family(mi_probe, stem.lower(), slot_l)
        # Enemy exports often omit the inherited Parent field, leaving only
        # CR/NOM/EX textures that resemble a firearm MI. Preserve explicit
        # special families (scan displays, decals, emissives), but route generic
        # enemy surfaces through their own shader whenever the source asset is
        # under Pioneer/Enemies.
        if (
            family in (FAMILY_WEAPON, "simple")
            and "/enemies/" in (psk_path or "").replace("\\", "/").lower()
        ):
            return FAMILY_ENEMY
        return family

    def _stale_decal_misclass(mat) -> bool:
        """Env trim previously stamped as enemy/map decal — must rebuild."""
        fam = str(mat.get("arc_mi_family") or "")
        if fam != FAMILY_DECAL:
            return False
        expected = _expected_family()
        return expected != FAMILY_DECAL

    def _stale_shared(mat) -> bool:
        from .native.native_families import is_user_override, native_cache_is_stale
        if is_user_override(mat):
            return False
        expected = _expected_family()
        if expected == "shader_parity":
            from .native import shader_parity
            return not shader_parity.material_current(mat, mi_path)
        if mat.get('dr_native_family'):
            if str(mat.get('arc_mi_family') or '') != expected:
                return True
            return native_cache_is_stale(mat, mi_path, obj=obj)
        if expected == "character_glass_opaque":
            return (mat.get("arc_mi_family") != expected
                    or mat.get("arc_mi_source_signature") != signature
                    or _active_dedicated_outfit_profile(mat) != "native_glass_opaque")
        return (
            str(mat.get("arc_mi_family") or "") != expected
            or str(mat.get("arc_mi_source_signature") or "") != signature
            or (expected == FAMILY_ENEMY
                and str(mat.get("arc_enemy_surface_setup") or "") != "enemy-surface-v5")
            or (expected == FAMILY_SCAN
                and int(mat.get("arc_scan_reconstruction_version") or 0) < 4)
            or (expected == "simple" and str(mat.get("arc_simple_setup") or "") != "v5")
            or (expected in (FAMILY_ENVIRONMENT, FAMILY_METAL, FAMILY_ROAD)
                and str(mat.get("arc_env_surface_setup") or "") != "packed-prop-v2")
            or _stale_decal_misclass(mat)
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
                    mat.name = f"{mat.name}_stale_material"
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
    if existing is not None and not existing.get("dr_control_owner") and str(existing.get("arc_mi_path", "") or "") == key:
        if _stale_shared(existing):
            try:
                existing.name = f"{existing.name}_stale_material"
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
    from . import material_audit
    import time as _time
    started = _time.perf_counter()
    try:
        _dispatch_weapon_slot_material(
            mat,
            mi_path,
            psk_path or "",
            stem.lower(),
            (slot_lower or stem).lower(),
            obj=obj,
        )
    except Exception as e:
        print(
            f"Arc Asset Importer: Failed shared MI '{stem}' "
            f"({mi_path}): {e}"
        )
        # The material is removed below; the swallowed record lands on the
        # slot's next audited material (fallback or importer placeholder).
        material_audit.record_swallowed('dispatch.shared_mi', e)
        try:
            bpy.data.materials.remove(mat)
        except Exception:
            pass
        return None
    material_audit.note_build_time(_time.perf_counter() - started)
    try:
        flat_mi = _parse_flat_mi_json(mi_path)
        master, master_error = material_audit.master_for_flat(flat_mi, mi_path)
        material_audit.audit_material(mat, flat_mi, obj, master_record=master, master_error=master_error)
    except Exception as exc:
        material_audit.record_swallowed('dispatch.audit', exc)
    mat["arc_mi_source_signature"] = signature
    _SHARED_MI_MATERIALS[key] = mat
    return mat



def _dispatch_weapon_slot_material(mat, mi_path: str, psk_path: str, mi_stem_lower: str, slot_lower: str, obj=None):
    """Route a single weapon/enemy/map/item SK material slot to its family setup."""
    from .native.native_families import is_user_override
    if is_user_override(mat):
        return
    from .native import shader_parity
    if shader_parity.setup_if_planned(mat, mi_path, obj=obj):
        return
    log = utils.get_logger()
    # An unresolved exact native family is a failure, never a generic fallback.
    # Keep this outside the legacy catch-all that converts failures to SimplePBR.
    from .glass_native import read_parameters, setup_native_opaque
    native_opaque = read_parameters(mi_path)
    if native_opaque is not None:
        setup_native_opaque(mat, mi_path, psk_path, record=native_opaque, dry_snapshot=True)
        return
    try:
        mi_probe = _parse_flat_mi_json(mi_path)
        family = classify_mi_family(mi_probe, mi_stem_lower, slot_lower)
        if (
            family in (FAMILY_WEAPON, "simple")
            and "/enemies/" in (psk_path or "").replace("\\", "/").lower()
        ):
            family = FAMILY_ENEMY
        from .native.native_families import setup_entrypoint
        if setup_entrypoint(mat, mi_path, psk_path, obj=obj, cache_family=family):
            return
        _stamp_mi_family(mat, family)
        log.debug(
            "MI family '%s' → %s (slot=%s)",
            os.path.basename(mi_path), family, slot_lower or "(none)",
        )

        parent_l = str(mi_probe.get("parent") or "").lower()
        from .emission import is_lantern, setup_lantern_material, setup_light_material
        if is_lantern(mi_probe):
            setup_lantern_material(mat, mi_path)
        elif mi_stem_lower.startswith("mi_beacon") or parent_l.endswith("m_beacon"):
            from .environment import setup_beacon_material
            setup_beacon_material(mat, mi_path, psk_path)
        elif family == FAMILY_EMISSIVE:
            setup_light_material(mat, mi_path)
        elif family == FAMILY_FLIPBOOK:
            from .items import _setup_flipbook_material
            _setup_flipbook_material(mat, mi_path, psk_path)
        elif family == FAMILY_SCAN:
            _setup_enemy_scan_display_material(mat, mi_path)
        elif family == FAMILY_CRT:
            from .crt_display import setup_crt_display_material
            setup_crt_display_material(mat, mi_path)
        elif family == FAMILY_ENEMY:
            setup_enemy_surface_material(mat, mi_path, psk_path, obj=obj)
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
        from .native.shared_helpers import UnsupportedHelperGroup
        from .rebuild.native_catalog_bridge import NativeRenderLimitationError
        if isinstance(exc,(UnsupportedHelperGroup,NativeRenderLimitationError)):raise
        from . import material_audit
        material_audit.record_swallowed('dispatch.slot_setup', exc)
        try:
            mat["arc_audit_fallback"] = "simple"
        except Exception as exc_tag:
            material_audit.record_swallowed('dispatch.fallback_tag', exc_tag)
        # Soft-fail: never crash Stage 2 on a single bad MI
        log.error(
            "Material setup failed for '%s' (%s): %s — falling back to simple",
            os.path.basename(mi_path or ""), mi_stem_lower, exc,
        )
        try:
            _setup_simple_material(mat, mi_path, psk_path)
        except Exception as exc2:
            material_audit.record_swallowed('dispatch.simple_fallback', exc2)
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
    if stem.lower() in {"m_simplepbr", "m_simplepbr_colorscheme"}:
        # These UE parent materials can be assigned directly to mesh sections;
        # resolve their authored defaults instead of leaving Blender's empty PSK slot.
        return stem
    # Skip materials we already created: Obj_MI_Foo_Mat
    if stem.lower().endswith("_mat") and "_mi_" in stem.lower():
        return ""
    if stem.lower().startswith("mi_"):
        return stem
    m = re.search(r"(MI_[A-Za-z0-9_]+)", stem, flags=re.IGNORECASE)
    return m.group(1) if m else ""



def _active_dedicated_outfit_profile(mat) -> str:
    """Recognize a successfully connected owner-built shader, not an orphan tag."""
    if mat is None or not getattr(mat, 'use_nodes', False) or mat.node_tree is None:
        return ''
    tree = mat.node_tree
    outputs = {}
    for node in tree.nodes:
        if node.type == 'OUTPUT_MATERIAL' and getattr(node, 'is_active_output', True):
            surface = node.inputs.get('Surface')
            if surface is not None:
                outputs[node] = surface
    # NodeSocket.links scans the entire tree in Blender. Reading it for every
    # input makes large native graphs quadratic; index all incoming edges once.
    incoming = {}
    pending = []
    for link in tree.links:
        incoming.setdefault(link.to_node, []).append(link.from_node)
        if link.to_node in outputs and link.to_socket == outputs[link.to_node]:
            pending.append(link.from_node)
    active = set()
    while pending:
        node = pending.pop()
        if node in active:
            continue
        active.add(node)
        pending.extend(incoming.get(node, ()))
    if not active:
        return ''
    if mat.get('arc_glass_setup') == 'native-character-glass-opaque-dry-v1':
        if any(node.type == 'BSDF_PRINCIPLED' and node.name.startswith('NativeOpaque_') for node in active):
            return 'native_glass_opaque'
    if (mat.get('arc_visor_setup') in ('bundled-visor-approx-v1', 'manual_fallback')
            and mat.get('arc_visor_source_read')):
        if any(node.type == 'GROUP' and node.node_tree is not None
               and (node.node_tree.name == 'Visor' or node.node_tree.name.startswith('Visor.'))
               for node in active):
            return 'visor_approx_manual' if mat.get('arc_visor_setup') == 'manual_fallback' else 'visor_approx'
    if mat.get('arc_fur_setup') and not mat.get('arc_fur_fallback'):
        if any(node.type in ('BSDF_PRINCIPLED', 'BSDF_HAIR_PRINCIPLED') for node in active):
            return 'fur'
    if mat.get('arc_outfit_color_pipeline') == 'ground_truth':
        if any(node.type == 'BSDF_PRINCIPLED' and getattr(node, 'get', lambda *_: node.name)('dr.legacy_name', node.name).startswith('GT_Principled') for node in active):
            return 'layered'
    want = str(getattr(utils, '_NODE_GROUP', 'ArcTexturer') or 'ArcTexturer').replace(' ', '').casefold()
    for node in active:
        if node.type != 'GROUP' or node.node_tree is None:
            continue
        name = node.node_tree.name.replace(' ', '').casefold()
        if name == want or name.startswith(want + '.'):
            return 'layered'
    return ''


def _material_identity_path(path: str) -> str:
    if not path:
        return ''
    path = textures._fs_abspath(str(path))
    if path.casefold().endswith('.arc-material.json'):
        path = path[:-len('.arc-material.json')] + '.json'
    return _norm_path_key(path)


def _dedicated_outfit_material_current(mat, requested_mi_path: str = '', obj=None) -> bool:
    """Only reuse a connected owner shader for the actual requested MI identity."""
    profile = _active_dedicated_outfit_profile(mat)
    if not profile:
        return False
    recorded = str(mat.get('arc_skin_json') or mat.get('arc_mi_path') or '')
    if not recorded:
        return False
    # Matching MI identity cannot validate the wrong shader family. A CRT or
    # native glass MI may have been built by the clothing path before slot repair.
    if profile == 'layered' and not _mi_uses_layered_character_profile(recorded):
        return False
    if profile in ('visor_approx', 'visor_approx_manual', 'native_glass_opaque'):
        # A source marker alone cannot establish reuse after parent/MI edits.
        signature = mat.get('arc_glass_source_signature' if profile == 'native_glass_opaque' else 'arc_visor_source_signature')
        if not signature:
            return False
        try:
            current = json.dumps(textures.material_lineage_signature(textures._fs_abspath(recorded)), default=str)
        except Exception:
            return False
        if current != signature:
            return False
    requested = str(requested_mi_path or '')
    if not requested and obj is not None:
        # The object's chosen outfit MI identifies its main layered shell.
        # Embedded fur sections have their own exact MI selected by fur hooks.
        if profile == 'layered':
            # One object may contain independently layered shell/prop sections.
            # Its single selected MI is an implicit request only for a unique
            # layered owner; explicit per-slot requests above remain strict.
            owners = set()
            for slot in getattr(obj, 'material_slots', ()):
                candidate = slot.material
                if candidate is not None and (candidate == mat or _active_dedicated_outfit_profile(candidate) == 'layered'):
                    owners.add(candidate.as_pointer())
                    if len(owners) > 1:
                        break
            if len(owners) <= 1:
                requested = str(obj.get('arc_skin_json') or '')
        elif str(obj.get('arc_model_type') or '') == 'fur':
            requested = str(obj.get('arc_skin_json') or '')
    return not requested or _material_identity_path(recorded) == _material_identity_path(requested)


def _mi_uses_layered_character_profile(mi_path: str) -> bool:
    """Current named parent/packed maps establish the layered owner route."""
    if not mi_path or not os.path.isfile(mi_path):
        return False
    mi = _parse_flat_mi_json(mi_path)
    parent = str(mi.get('parent') or '').casefold()
    names = {str(name).casefold() for name, _path in mi.get('textures') or []}
    return 'character_layered' in parent or {'colormask', 'occlusioncurvaturematerialid'} <= names


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

    from ... import fmdex
    from .. import textures as texmod

    log = utils.get_logger()
    # Cosmetic repair must retain the dedicated card shader and opaque tie slots.
    # Generic MI family dispatch has no character object/section context.
    source = str(obj.get("arc_source_mesh") or obj.get("arc_psk_path") or "")
    colour_json = str(obj.get("arc_hair_color_mi") or "")
    if context != CTX_MAP and source and os.path.isfile(source) and texmod.is_hair(source):
        from .character import setup_hair_material
        setup_hair_material(obj, str(obj.get("arc_hair_mi") or ""), colour_json=colour_json)
        return sum(slot.material is not None for slot in obj.material_slots)

    def rebuild_hair_slot(index, mi_path):
        if context == CTX_MAP or not texmod.is_hair_cards_mi(mi_path):
            return False
        from .character import _setup_hair_card_material
        old_index = obj.active_material_index
        try:
            obj.active_material_index = index
            if obj.active_material and obj.active_material.users > 1:
                obj.active_material = obj.active_material.copy()
            _setup_hair_card_material(obj, mi_path, colour_json=colour_json)
        finally:
            obj.active_material_index = old_index
        return True

    fixed = 0
    authored = None
    for index, slot in enumerate(obj.material_slots):
        if not slot.material:
            continue
        # Already wired to a shared Arc MI — skip rebuild unless out of context
        existing_key = str(slot.material.get("arc_mi_path", "") or "")
        resolve_stem = _mi_stem_from_blender_name(slot.material.name)
        if context != CTX_MAP and _active_dedicated_outfit_profile(slot.material):
            if _dedicated_outfit_material_current(slot.material, obj=obj):
                # The main clothing/fur builder just established this graph.
                # Generic classification cannot represent layered presets and
                # previously replaced them with a simple fallback here.
                continue
            # The clothing builder can create a connected GT graph even for an
            # authored CRT/glass MI. This is a wrong-family graph, not a changed
            # layered colorway: repair its exact binding through native dispatch.
            if (_active_dedicated_outfit_profile(slot.material) == 'layered'
                    and existing_key and os.path.isfile(existing_key)
                    and not _mi_uses_layered_character_profile(existing_key)):
                try:
                    bindings = json.loads(str(obj.get('arc_outfit_material_bindings') or '{}'))
                except (TypeError, ValueError):
                    bindings = {}
                requested = str(bindings.get(str(index)) or obj.get('arc_skin_json') or existing_key)
                if _material_identity_path(existing_key) == _material_identity_path(requested):
                    native = _get_or_build_shared_mi_material(
                        os.path.basename(existing_key).split('.')[0], existing_key,
                        source or os.path.join(asset_folder, 'mesh.psk'), resolve_stem.lower(), obj=obj)
                    if native is not None:
                        slot.material = native
                        fixed += 1
                        continue
            # A changed requested MI must return to its dedicated owner; never
            # report the old graph as repaired, or convert it to a generic MI.
            obj['arc_materials_pending'] = 1
            log.warning("Dedicated shader on '%s' slot %d has a different requested MI; dedicated material rebuild required", obj.name, index)
            continue
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
                if rebuild_hair_slot(index, existing_key):
                    fixed += 1
                    continue
                # Update Materials must run the same version/family gate as a
                # fresh import, including materials retained in saved blends.
                current = _get_or_build_shared_mi_material(
                    slot.material.get("arc_mi_stem") or resolve_stem,
                    existing_key, os.path.join(asset_folder, "mesh.psk"),
                    resolve_stem.lower(),
                )
                if current is not None:
                    slot.material = current
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

        if not mi_path and source:
            # The section table records this slot's exact MI wherever it lives
            # (hero Materials/, effect Systems/, another room's folder). It is the
            # mesh's own reference, not a basename guess, so no context gate applies.
            if authored is None:
                authored = _uemodel_section_materials(source)
            reference = authored[index] if index < len(authored) else ""
            if reference and reference.rsplit("/", 1)[-1].split(".")[0].casefold() == mi_stem.casefold():
                mi_path = texmod.find_asset_from_object_path(reference, ".json")
                if mi_path:
                    folder = os.path.dirname(mi_path)

        if not mi_path:
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
            if rebuild_hair_slot(index, mi_path):
                fixed += 1
                continue
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
