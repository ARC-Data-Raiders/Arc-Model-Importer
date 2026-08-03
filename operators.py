"""
Processing logic for PSK imports and model texturing
"""

import os
import re
import bpy
import bpy_extras
import mathutils
from bpy.props import StringProperty
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


def process_entry(entry) -> tuple:
    """Process a single PSK entry: import and set up materials."""
    psk_path = bpy.path.abspath(entry.psk_path)
    json_path = "" if entry.skin_choice == 'NONE' else bpy.path.abspath(entry.skin_choice)
    if not json_path:
        json_path = textures.get_base_skin_json(psk_path, entry.manual_skins_folder)
    body_variant = entry.body_choice if hasattr(entry, 'body_choice') else 'NONE'
    
    if not os.path.isfile(psk_path):
        return False, f"PSK not found: {psk_path}", []
    
    model_type = textures.detect_model_type(psk_path)
    
    try:
        new_objects = importing.import_psk(psk_path)
    except RuntimeError as e:
        return False, str(e), []
    
    mesh_objects = [o for o in new_objects if o.type == "MESH"]
    hair_mi = entry.hair_mi if hasattr(entry, 'hair_mi') else 'NONE'
    for obj in mesh_objects:
        try:
            obj["arc_psk_path"] = psk_path
            obj["arc_model_type"] = model_type
            obj["arc_materials_pending"] = 0
            obj["arc_body_variant"] = body_variant if body_variant else "NONE"
            obj["arc_hair_mi"] = hair_mi if hair_mi else "NONE"
        except Exception:
            pass
        apply_materials_to_object(
            obj, psk_path,
            body_variant=body_variant,
            hair_mi=hair_mi,
            skin_json=json_path,
            manual_skins_folder=entry.manual_skins_folder,
            skin_choice=entry.skin_choice,
        )
    kind = model_type
    if model_type == "weapon":
        kind = "enemy" if textures.is_enemy(psk_path) else "weapon"
    elif model_type == "clothing":
        mi_data = textures.parse_clothing_mi(json_path) if json_path else {}
        kind = "with skin colours" if mi_data.get("colours") else "default skin"
    elif model_type == "body":
        kind = f"body, {body_variant if body_variant != 'NONE' else 'no skin'}"
    return True, f"Imported ({kind}): {os.path.basename(psk_path)}", new_objects


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
        new_objects = importing.import_psk(psk_path)
    except RuntimeError as e:
        return False, str(e), []

    mesh_objects = [o for o in new_objects if o.type == "MESH"]
    for obj in mesh_objects:
        try:
            obj["arc_psk_path"] = psk_path
            obj["arc_model_type"] = model_type
            obj["arc_materials_pending"] = 0
            obj["arc_bridge_source"] = "fmodel"
            obj["arc_body_variant"] = body_variant if body_variant else "NONE"
            obj["arc_hair_mi"] = hair_mi if hair_mi else "NONE"
        except Exception:
            pass
        apply_materials_to_object(
            obj, psk_path,
            body_variant=body_variant,
            hair_mi=hair_mi,
            skin_json=skin_json,
            manual_skins_folder=manual_skins_folder,
            skin_choice=skin_choice,
        )
    return True, f"Imported ({model_type}): {os.path.basename(psk_path)}", new_objects


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
) -> str:
    """Apply Arc materials to an existing mesh without importing. Returns a short status."""
    if not obj or obj.type != "MESH":
        return "skipped (not a mesh)"

    folder = os.path.dirname(psk_path) if psk_path else ""
    # FModel's outfit manifest already resolved the part type. Prefer that stamp because bridge
    # exports may not have had their material PNGs on disk when the PSK was first inspected.
    model_type = str(obj.get("arc_model_type", "") or "").strip().lower()
    if not model_type:
        model_type = textures.detect_model_type(psk_path) if psk_path else "unknown"

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
        hair_json = hair_mi if hair_mi and hair_mi != "NONE" else ""
        if not hair_json:
            mis = textures.scan_hair_mis(psk_path)
            hair_json = mis[0][1] if mis else ""
        try:
            if hair_json:
                obj["arc_hair_mi"] = hair_json
        except Exception:
            pass
        materials.setup_hair_material(obj, hair_json)
        return "hair"

    if model_type == "weapon":
        materials.setup_weapon_material(obj, psk_path)
        return "enemy" if textures.is_enemy(psk_path) else "weapon"

    # A visor is a clothing shell plus a glass slot, so it takes the same ArcTexturer path and
    # only differs in which slots get replaced afterwards.
    if model_type in ("clothing", "visor"):
        json_path = skin_json
        if not json_path:
            json_path = textures.get_base_skin_json(psk_path, manual_skins_folder)
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
        mi_data = textures.parse_clothing_mi(json_path) if json_path else {
            "colours": {}, "ta_ids": {}, "zone_scalars": {},
            "mi_params": {"scalars": [], "vectors": []}, "decals": [],
        }
        colours = mi_data["colours"]
        selected_skin_name = _colorway_name_from_skin_path(
            skin_choice if skin_choice and skin_choice != "NONE" else json_path
        )
        try:
            main_pngs = sorted(f for f in os.listdir(folder) if f.lower().endswith(".png")) if folder else []
        except OSError:
            main_pngs = []
        base_pngs = textures.scan_base_skin_textures(
            psk_path, selected_skin_name, manual_skins_folder
        ) if psk_path else []
        materials.setup_arc_texturer_material(
            obj, folder, colours, psk_path,
            json_path=json_path, decal_folder=decal_folder,
            selected_skin_name=selected_skin_name,
            manual_skins_folder=manual_skins_folder,
            mi_data=mi_data, main_pngs=main_pngs, base_pngs=base_pngs,
        )
        glass_json = str(obj.get("arc_glass_skin_json", "") or "") or json_path
        applied = materials.apply_embedded_visor_slots(obj, psk_path, skin_json=glass_json)
        if model_type == "visor":
            if not applied:
                materials.setup_visor_material(obj, psk_path, skin_json=glass_json)
            return "visor"
        return "clothing"

    if model_type == "misc":
        materials.setup_misc_material(obj, psk_path)
        return "misc"

    # Map props (Stage 1 stamps arc_model_type=map): shared MI cache, no outfit walks
    if model_type == "map":
        fixed = materials.setup_map_material(obj, psk_path)
        return f"map ({fixed})" if fixed else "unresolved"

    # unknown / hero character SKs: same multi-slot path as weapons (SK SkeletalMaterials
    # ObjectPaths → Materials/ siblings). detect_model_type returns "unknown" for
    # SK_Kalika_Base_Body (not sk_body / clothing occlusion / firearm).
    if psk_path and os.path.isfile(psk_path):
        sk_slots = materials._parse_sk_material_slots(psk_path)
        if any(mi_path for _name, _stem, mi_path in sk_slots):
            sk_wired = materials.setup_weapon_material(obj, psk_path)
            if sk_wired:
                return f"sk-slots ({sk_wired})"

    # Fall back: MI-named Blender slots (BlenderUMap / PSK slot names) without SK JSON
    fixed = materials.fix_object_materials_from_mi_slots(obj, folder)
    if fixed:
        return f"mi-slots ({fixed})"

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


def batch_import_instances(context, selected_presets) -> tuple:
    """Import one full model instance per selected outfit colourway."""
    entries = list(context.scene.arc_psk_entries)
    if not entries:
        return 0, 0
    
    types = [textures.detect_model_type(bpy.path.abspath(e.psk_path)) for e in entries]
    any_clothing = any(t == "clothing" for t in types)
    dominant_type = "weapon" if all(t == "weapon" for t in types) else ""
    
    scene = context.scene
    x_cursor = 0.0
    margin_frac = 0.25
    instances = 0
    parts_total = 0
    
    for preset_name, preset_path in selected_presets:
        importing.apply_outfit_preset(context, preset_path)
        
        inst_objs = []
        for entry in entries:
            ok, msg, new_objs = process_entry(entry)
            inst_objs.extend(new_objs)
            if ok:
                parts_total += 1
                print(f"    [{preset_name}] {msg}")
            else:
                print(f"    [{preset_name}] FAILED: {msg}")
        
        if not inst_objs:
            continue
        
        rig.fix_rig_all(inst_objs, merge=any_clothing, model_type=dominant_type)
        
        def _still_alive(o):
            try:
                o.name
                return True
            except ReferenceError:
                return False
        inst_objs = [o for o in inst_objs if _still_alive(o)]
        
        coll_name = f"{preset_name}"
        coll = bpy.data.collections.new(coll_name)
        scene.collection.children.link(coll)
        for o in inst_objs:
            for c in list(o.users_collection):
                c.objects.unlink(o)
            coll.objects.link(o)
        
        context.view_layer.update()
        minx = maxx = None
        for o in inst_objs:
            if o.type != 'MESH':
                continue
            for corner in o.bound_box:
                wx = (o.matrix_world @ mathutils.Vector(corner)).x
                minx = wx if minx is None else min(minx, wx)
                maxx = wx if maxx is None else max(maxx, wx)
        
        if minx is not None:
            width = max(maxx - minx, 1e-4)
            shift = x_cursor - minx
            for o in inst_objs:
                if o.parent is None:
                    o.location.x += shift
            x_cursor += width * (1.0 + margin_frac)
        
        instances += 1
    
    return instances, parts_total

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
        leftover = materials.fix_object_materials_from_mi_slots(obj, folder)
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
    fixed = materials.fix_object_materials_from_mi_slots(obj, asset_folder)
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
class ARC_OT_SelectHairMI(Operator):
    """Select a hair MI JSON for this entry."""
    bl_idname = "arc.select_hair_mi"
    bl_label = "Select Hair MI"
    psk_path: bpy.props.StringProperty(options={'HIDDEN'})
    mi_path: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        for entry in context.scene.arc_psk_entries:
            if bpy.path.abspath(entry.psk_path) == bpy.path.abspath(self.psk_path):
                entry.hair_mi = self.mi_path
                break
        return {'FINISHED'}


class ARC_OT_PickManualOutfitFolder(Operator, bpy_extras.io_utils.ImportHelper):
    """Browse to the DA_OI_Outfit folder for this character."""
    bl_idname = "arc.pick_manual_outfit_folder"
    bl_label = "Select DA_OI_Outfit Folder"
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={'HIDDEN'})

    def invoke(self, context, event):
        root = utils.get_pioneer_root()
        default_dir = utils.find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit"]) if root else ""
        if default_dir:
            self.filepath = default_dir.rstrip("/\\") + os.sep
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        folder = os.path.dirname(bpy.path.abspath(self.filepath))
        if not folder:
            folder = bpy.path.abspath(self.filepath)
        if not os.path.isdir(folder):
            self.report({'ERROR'}, f"Not a valid folder: {folder}")
            return {'CANCELLED'}
        context.scene.arc_manual_outfit_folder = folder
        found = len(importing.scan_outfit_presets_in_folder(folder))
        if found:
            self.report({'INFO'}, f"Found {found} outfit colourway(s) in '{os.path.basename(folder)}'.")
        else:
            self.report({'WARNING'}, f"No DA_OI_Outfit_..._Color_....json files found in '{os.path.basename(folder)}'.")
        importing.populate_outfit_selections(context)
        bpy.ops.arc.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OT_ClearManualOutfitFolder(Operator):
    """Clear the manual DA_OI_Outfit folder override."""
    bl_idname = "arc.clear_manual_outfit_folder"
    bl_label = "Clear Outfit Folder Override"

    def execute(self, context):
        context.scene.arc_manual_outfit_folder = ""
        importing.populate_outfit_selections(context)
        bpy.ops.arc.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OT_ImportSinglePSK(Operator):
    """Import a single .psk file."""
    bl_idname = "arc.import_single_psk"
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
        scene.arc_psk_entries.clear()
        entry = scene.arc_psk_entries.add()
        entry.psk_path = psk_path
        entry.display_name = os.path.basename(psk_path)
        bpy.ops.arc.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OT_ImportOutfitFolder(Operator):
    """Select a folder — PSKs are auto-discovered from subfolders."""
    bl_idname = "arc.import_outfit_folder"
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
        bpy.ops.arc.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OT_PickOutfitCSV(Operator, bpy_extras.io_utils.ImportHelper):
    """Browse for a different outfit_reference.csv."""
    bl_idname = "arc.pick_outfit_csv"
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


class ARC_OT_ClearOutfitCSV(Operator):
    """Go back to the CSV bundled with this addon."""
    bl_idname = "arc.clear_outfit_csv"
    bl_label = "Use Bundled CSV"

    def execute(self, context):
        context.scene.arc_outfit_csv_path = ""
        from .properties import rebuild_csv_outfit_map
        rebuild_csv_outfit_map("")
        return {'FINISHED'}


class ARC_OT_LoadOutfit(Operator):
    """Load all PSK parts for the selected outfit from the dropdown."""
    bl_idname = "arc.load_outfit"
    bl_label = "Load Outfit"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        root = utils.get_pioneer_root()
        if not root:
            self.report({'ERROR'}, "Set the PioneerGame root folder first.")
            return {'CANCELLED'}
        choice = scene.arc_selected_outfit
        if not choice:
            self.report({'ERROR'}, "No outfit selected — click one in the list first.")
            return {'CANCELLED'}
        rows = importing.load_outfit_csv(importing.get_outfit_csv_path(context))
        row = importing.find_outfit_row(rows, choice)
        if not row:
            self.report({'ERROR'}, "No outfit selected.")
            return {'CANCELLED'}
        item_ui_folders = [f.strip() for f in row.get("Item/UI Folder Name", "").split(";") if f.strip()]
        if not item_ui_folders:
            self.report({'ERROR'}, "Selected outfit has no Item/UI Folder Name to import from.")
            return {'CANCELLED'}
        psks = importing.collect_psks_for_outfit_row(root, item_ui_folders)
        if not psks:
            model_folder = (row.get("Model Folder Name") or "").strip()
            psks = importing.collect_psks_from_model_folder(root, model_folder)
        if not psks:
            self.report({'ERROR'}, "No PSK files found for this outfit's parts.")
            return {'CANCELLED'}
        scene.arc_psk_entries.clear()
        for psk_path in psks:
            entry = scene.arc_psk_entries.add()
            entry.psk_path = psk_path
            entry.display_name = os.path.basename(psk_path)
        self.report({'INFO'}, f"Queued {len(psks)} part(s) for '{row.get('Flavour') or row.get('ST')}'.")
        bpy.ops.arc.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OT_SelectOutfit(Operator):
    """Select an outfit from the searchable list."""
    bl_idname = "arc.select_outfit"
    bl_label = "Select Outfit"
    outfit_key: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        context.scene.arc_selected_outfit = self.outfit_key
        context.scene.arc_selected_outfit_browse = self.outfit_key
        return {'FINISHED'}


class ARC_OT_ClearOutfitSearch(Operator):
    """Clear the outfit search filter."""
    bl_idname = "arc.clear_outfit_search"
    bl_label = "Clear Search"

    def execute(self, context):
        context.scene.arc_outfit_search = ""
        return {'FINISHED'}


class ARC_OT_MergeSelectedArmatures(Operator):
    """Merge all selected armatures using Arc Raiders rig fix logic."""
    bl_idname = "arc.merge_selected_armatures"
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


class ARC_OT_FixMaterials(Operator):
    """Rebuild shaders on existing meshes using Pioneer MI JSONs / textures."""
    bl_idname = "arc.fix_materials"
    bl_label = "Update Materials"
    bl_description = (
        "Rebuild materials on selected meshes (or all scene meshes if none selected) "
        "from PioneerGame MI JSONs and textures. Use after BlenderUMap imports, or to "
        "refresh old blends after an addon update — object placement is kept."
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        root = utils.get_pioneer_root()
        if not root or not os.path.isdir(root):
            self.report({'ERROR'}, "Set PioneerGame Folder in Settings first")
            return {'CANCELLED'}

        objs = [o for o in context.selected_objects if o.type == 'MESH']
        if not objs:
            objs = [o for o in context.scene.objects if o.type == 'MESH']
        if not objs:
            self.report({'WARNING'}, "No mesh objects to fix")
            return {'CANCELLED'}

        fmdex.ensure_loaded()
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


class ARC_OT_ReimportSelected(Operator):
    """Refresh materials/UVs on selected meshes only (no whole map/folder re-import)."""
    bl_idname = "arc.reimport_selected"
    bl_label = "Re-import Selected"
    bl_description = (
        "Rebuild textures, UV names (incl. poster UV1 / GraphicAtlas), and Arc materials "
        "on currently selected mesh objects only. Does not re-run Stage 1 or Import Folder. "
        "Meshes without a resolvable Pioneer/FMDex path are reported and skipped"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        root = utils.get_pioneer_root()
        if not root or not os.path.isdir(root):
            self.report({'ERROR'}, "Set PioneerGame Folder in Settings first")
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


class ARC_OT_ApplyOutfitPreset(Operator):
    """Apply the selected outfit preset's per-part skin colours."""
    bl_idname = "arc.apply_outfit_preset"
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


class ARC_OT_PickManualSkinsFolder(Operator, bpy_extras.io_utils.ImportHelper):
    """Browse to a folder to use as this part's Skins source."""
    bl_idname = "arc.pick_manual_skins_folder"
    bl_label = "Select Skins Folder"
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={'HIDDEN'})
    entry_index: bpy.props.IntProperty(default=-1, options={'HIDDEN'})

    def execute(self, context):
        entries = context.scene.arc_psk_entries
        if not (0 <= self.entry_index < len(entries)):
            self.report({'ERROR'}, "Internal error: part entry not found.")
            return {'CANCELLED'}
        folder = os.path.dirname(bpy.path.abspath(self.filepath))
        if not folder:
            folder = bpy.path.abspath(self.filepath)
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
        bpy.ops.arc.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OT_ConfirmPSKImport(Operator):
    """Review parts, assign skins, then confirm import."""
    bl_idname = "arc.confirm_psk_import"
    bl_label = "PSKImporter_SIL_AI"
    bl_options = {'REGISTER', 'UNDO'}

    def _has_any_choices(self, context) -> bool:
        for entry in context.scene.arc_psk_entries:
            if textures.scan_skins(entry.psk_path, entry.manual_skins_folder):
                return True
            if textures.is_body(entry.psk_path):
                return True
            if textures.is_hair(entry.psk_path) and textures.scan_hair_mis(entry.psk_path):
                return True
            if textures.detect_model_type(bpy.path.abspath(entry.psk_path)) == "clothing":
                return True
        return False

    def invoke(self, context, event):
        # Skin/colourway review UI is only for layered outfit/clothing queues.
        # Weapons, misc, face/body/hair-only, etc. import immediately.
        if not importing.queue_supports_outfit_batch(context):
            context.scene.arc_outfit_selections.clear()
            return self.execute(context)

        importing.populate_outfit_selections(context)
        has_batch = len(context.scene.arc_outfit_selections) > 0
        if not self._has_any_choices(context) and not has_batch:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self, width=640)

    def draw(self, context):
        layout = self.layout
        entries = context.scene.arc_psk_entries
        if not entries:
            layout.label(text="No PSK files queued.", icon='ERROR')
            return

        layout.label(text=f"{len(entries)} part(s) — assign skins then click OK:", icon='IMPORT')

        is_outfit_queue = importing.queue_supports_outfit_batch(context)
        if is_outfit_queue:
            from .properties import make_outfit_preset_items
            outfit_items = make_outfit_preset_items(self, context)
            if len(outfit_items) > 1:
                preset_box = layout.box()
                row = preset_box.row(align=True)
                row.prop(context.scene, "arc_outfit_preset", text="Outfit")
                row.operator("arc.apply_outfit_preset", text="Apply", icon='CHECKMARK')

            sels = context.scene.arc_outfit_selections
            manual_outfit = getattr(context.scene, 'arc_manual_outfit_folder', '')
            bbox = layout.box()
            bbox.label(text="Batch import colourways — each becomes a separate model:", icon='DUPLICATE')

            if manual_outfit:
                mrow = bbox.row()
                mrow.label(text=f"Outfit source: {manual_outfit}", icon='FILE_FOLDER')
                mrow.operator("arc.pick_manual_outfit_folder", text="Change...", icon='FILEBROWSER')
                mrow.operator("arc.clear_manual_outfit_folder", text="", icon='X')

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
                bbox.operator("arc.pick_manual_outfit_folder", text="Browse for DA_OI Colourway Folder...", icon='FILEBROWSER')

            layout.separator()

        for idx, entry in enumerate(entries):
            skins = textures.scan_skins(entry.psk_path, entry.manual_skins_folder)
            is_body = textures.is_body(entry.psk_path)
            is_hair = textures.is_hair(entry.psk_path)
            is_clothing = textures.detect_model_type(bpy.path.abspath(entry.psk_path)) == "clothing"
            hair_mis = textures.scan_hair_mis(entry.psk_path) if is_hair else []
            if not skins and not is_body and not hair_mis and not is_clothing:
                continue
            box = layout.box()
            box.label(text=entry.display_name, icon='FILE')
            if skins:
                box.prop(entry, "skin_choice", text="Skin")
                if entry.manual_skins_folder:
                    mrow = box.row()
                    mrow.label(text=f"Skin source: {os.path.basename(entry.manual_skins_folder)}", icon='FILE_FOLDER')
                    mop = mrow.operator("arc.pick_manual_skins_folder", text="Change...", icon='FILEBROWSER')
                    mop.entry_index = idx
            elif is_clothing:
                warn = box.box()
                warn.label(text="No skin data detected for this part.", icon='ERROR')
                warn.label(text="The Skins folder name may not match — pick it manually:")
                wop = warn.operator("arc.pick_manual_skins_folder", text="Browse for Skins Folder...", icon='FILEBROWSER')
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
                    op = row.operator("arc.select_hair_mi",
                                      text=("\u2713 " if is_sel else "  ") + mi_name,
                                      icon=icon)
                    op.psk_path = entry.psk_path
                    op.mi_path = mi_path

    def execute(self, context):
        selected = []
        if importing.queue_supports_outfit_batch(context):
            selected = [(s.preset_name, bpy.path.abspath(s.json_path))
                        for s in context.scene.arc_outfit_selections if s.selected]
        if selected:
            instances, parts = batch_import_instances(context, selected)
            self.report({'INFO'}, f"Arc Raiders: batch imported {instances} instance(s) ({parts} part(s) total).")
            context.scene.arc_psk_entries.clear()
            context.scene.arc_outfit_selections.clear()
            return {'FINISHED'}

        entries = context.scene.arc_psk_entries
        total = len(entries)
        ok_count = 0
        all_new_objs = []

        for entry in entries:
            ok, msg, new_objs = process_entry(entry)
            all_new_objs.extend(new_objs)
            if ok:
                ok_count += 1
                self.report({'INFO'}, msg)
            else:
                self.report({'WARNING'}, msg)

        any_clothing = any(
            textures.detect_model_type(bpy.path.abspath(e.psk_path)) == "clothing"
            for e in entries
        )
        types = [textures.detect_model_type(bpy.path.abspath(e.psk_path)) for e in entries]
        dominant_type = "weapon" if all(t == "weapon" for t in types) else ""
        rig.fix_rig_all(all_new_objs, merge=any_clothing, model_type=dominant_type)

        self.report({'INFO'}, f"Arc Raiders: {ok_count}/{total} part(s) imported.")
        context.scene.arc_psk_entries.clear()
        context.scene.arc_outfit_selections.clear()
        return {'FINISHED'}


class ARC_OT_PickPioneerRoot(Operator, bpy_extras.io_utils.ImportHelper):
    """Browse to the PioneerGame root folder."""
    bl_idname = "arc.pick_pioneer_root"
    bl_label = "Select PioneerGame Folder"
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={'HIDDEN'})

    def execute(self, context):
        folder = os.path.dirname(bpy.path.abspath(self.filepath))
        if not folder:
            folder = bpy.path.abspath(self.filepath)
        context.scene.arc_pioneer_root = folder
        return {'FINISHED'}


class ARC_OT_ClearPioneerRoot(Operator):
    """Clear the PioneerGame root folder."""
    bl_idname = "arc.clear_pioneer_root"
    bl_label = "Clear PioneerGame Folder"

    def execute(self, context):
        context.scene.arc_pioneer_root = ""
        return {'FINISHED'}


class ARC_OT_PickFmdexRoot(Operator, bpy_extras.io_utils.ImportHelper):
    """Browse to FModel's FMDex output folder (contains *_FMDex.json.br)."""
    bl_idname = "arc.pick_fmdex_root"
    bl_label = "Select FMDex Folder"
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={'HIDDEN'})

    def invoke(self, context, event):
        default = fmdex.get_fmdex_directory()
        if default:
            self.filepath = default.rstrip("/\\") + os.sep
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        folder = os.path.dirname(bpy.path.abspath(self.filepath))
        if not folder:
            folder = bpy.path.abspath(self.filepath)
        if not os.path.isdir(folder):
            self.report({'ERROR'}, f"Not a valid folder: {folder}")
            return {'CANCELLED'}
        context.scene.arc_fmdex_root = folder
        fmdex.invalidate_cache()
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


class ARC_OT_ClearFmdexRoot(Operator):
    """Clear the FMDex folder override."""
    bl_idname = "arc.clear_fmdex_root"
    bl_label = "Clear FMDex Folder"

    def execute(self, context):
        context.scene.arc_fmdex_root = ""
        fmdex.invalidate_cache()
        return {'FINISHED'}


class ARC_OT_PaletteSetSelected(Operator):
    bl_idname = "arc.palette_set_selected"
    bl_label = "Set Palette On Selected"
    bl_description = "Persist the scene palette mode as an override for selected materials' stable keys"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mode = str(getattr(context.scene, "arc_palette_mode", "AUTO") or "AUTO").lower()
        count = 0
        for obj in context.selected_objects or []:
            key = str(obj.get("arc_material_key", "") or "")
            if not key and obj.active_material:
                key = str(obj.active_material.get("arc_material_key", "") or "")
            if not key:
                continue
            palette_calibration.set_override(key, mode)
            obj["arc_palette_mode"] = mode
            if obj.active_material:
                obj.active_material["arc_palette_mode"] = mode
            count += 1
        self.report({'INFO'}, f"Palette override '{mode}' on {count} object(s) — Update Materials")
        return {'FINISHED'}


class ARC_OT_PaletteResetSelected(Operator):
    bl_idname = "arc.palette_reset_selected"
    bl_label = "Reset Palette Selected"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        count = 0
        for obj in context.selected_objects or []:
            key = str(obj.get("arc_material_key", "") or "")
            if not key and obj.active_material:
                key = str(obj.active_material.get("arc_material_key", "") or "")
            if key:
                palette_calibration.set_override(key, "auto")
            obj["arc_palette_mode"] = "auto"
            obj["arc_palette_routing"] = "auto"
            if obj.active_material:
                obj.active_material["arc_palette_mode"] = "auto"
            count += 1
        self.report({'INFO'}, f"Reset palette on {count} object(s) — Update Materials")
        return {'FINISHED'}


class ARC_OT_PaletteExportCalibration(Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "arc.palette_export_calibration"
    bl_label = "Export Palette Calibration"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, "w", encoding="utf-8") as fh:
            fh.write(palette_calibration.export_overrides_json())
        self.report({'INFO'}, f"Exported {self.filepath}")
        return {'FINISHED'}


class ARC_OT_PaletteImportCalibration(Operator, bpy_extras.io_utils.ImportHelper):
    bl_idname = "arc.palette_import_calibration"
    bl_label = "Import Palette Calibration"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, "r", encoding="utf-8") as fh:
            n = palette_calibration.import_overrides_json(fh.read())
        self.report({'INFO'}, f"Imported {n} override(s) — Update Materials")
        return {'FINISHED'}


class ARC_OT_PaletteClearCalibration(Operator):
    bl_idname = "arc.palette_clear_calibration"
    bl_label = "Clear Palette Calibration"
    bl_options = {'REGISTER'}

    def execute(self, context):
        palette_calibration.clear_overrides()
        self.report({'INFO'}, "Cleared all palette overrides")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    ARC_OT_SelectHairMI,
    ARC_OT_ImportSinglePSK,
    ARC_OT_ImportOutfitFolder,
    ARC_OT_PickOutfitCSV,
    ARC_OT_ClearOutfitCSV,
    ARC_OT_LoadOutfit,
    ARC_OT_SelectOutfit,
    ARC_OT_ClearOutfitSearch,
    ARC_OT_MergeSelectedArmatures,
    ARC_OT_FixMaterials,
    ARC_OT_ReimportSelected,
    ARC_OT_ApplyOutfitPreset,
    ARC_OT_PickManualSkinsFolder,
    ARC_OT_PickManualOutfitFolder,
    ARC_OT_ClearManualOutfitFolder,
    ARC_OT_ConfirmPSKImport,
    ARC_OT_PickPioneerRoot,
    ARC_OT_ClearPioneerRoot,
    ARC_OT_PickFmdexRoot,
    ARC_OT_ClearFmdexRoot,
    ARC_OT_PaletteSetSelected,
    ARC_OT_PaletteResetSelected,
    ARC_OT_PaletteExportCalibration,
    ARC_OT_PaletteImportCalibration,
    ARC_OT_PaletteClearCalibration,
) + map_placement.OPERATOR_CLASSES
