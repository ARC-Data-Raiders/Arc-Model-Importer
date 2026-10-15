"""
Material setup — character domain (split from materials.py monolith).
"""
from __future__ import annotations

import os
import re
import json
import struct
import math
import difflib

import bpy

from .. import utils
from .. import textures
from .. import palette_calibration
from ..properties import BODY_ALBEDO, BODY_NORMAL

from .common import (
    _load_mi_textures,
    _parse_flat_mi_json,
    _parse_sk_material_slots,
    _set_material_alpha_mode,
)


# ---------------------------------------------------------------------------
# Other material setups (body, hair, weapon, misc, face)
# ---------------------------------------------------------------------------

def setup_body_material(obj, psk_path: str, body_variant: str):
    if body_variant == 'NONE':
        return
    
    tex_folder = os.path.join(os.path.dirname(psk_path), "Textures")
    albedo_name = BODY_ALBEDO.get(body_variant)
    normal_name = BODY_NORMAL.get(body_variant)
    
    albedo_path = os.path.join(tex_folder, albedo_name) if albedo_name else None
    normal_path = os.path.join(tex_folder, normal_name) if normal_name else None
    micro_path = os.path.join(tex_folder, "T_SkinMicroNormal_01.png")
    
    if albedo_path and not os.path.isfile(albedo_path):
        print(f"Arc Raiders PSK Importer: Body albedo not found: '{albedo_path}'")
        albedo_path = None
    if normal_path and not os.path.isfile(normal_path):
        print(f"Arc Raiders PSK Importer: Body normal not found: '{normal_path}'")
        normal_path = None
    if not os.path.isfile(micro_path):
        print(f"Arc Raiders PSK Importer: MicroNormal not found: '{micro_path}'")
        micro_path = None
    
    mat = bpy.data.materials.new(name=obj.name + "_Body_Mat")
    mat.use_nodes = True
    obj.active_material = mat
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    
    # Material updates must preserve authored vertex IDs and live morph values.
    # Welding/resetting the body here destroys later gender-shape inheritance.

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (300, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (600, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    # Match body tint to the paired face via MI_Face_*_SkinColor (M_Skin ColorMultiply).
    skin_mi = _resolve_skin_color_mi_for_obj(obj, psk_path)
    skin = _parse_skin_color_mi(skin_mi) if skin_mi else {}
    _apply_default_skin_bsdf(principled, subsurface_color=skin.get("subsurface_color"))

    if albedo_path:
        img_albedo = bpy.data.images.load(albedo_path, check_existing=True)
        albedo_node = nodes.new("ShaderNodeTexImage")
        albedo_node.image = img_albedo
        albedo_node.label = albedo_name
        albedo_node.interpolation = "Cubic"
        albedo_node.location = (-600, 300)
        albedo_sock = albedo_node.outputs["Color"]
        if skin.get("color_multiply"):
            albedo_sock = _mix_color_multiply(
                nodes, links, albedo_sock, skin["color_multiply"], (-320, 260),
            )
        if skin.get("skin_desaturation") is not None:
            albedo_sock = _apply_skin_desaturation(
                nodes, links, albedo_sock, skin["skin_desaturation"], (-120, 220),
            )
        links.new(albedo_sock, principled.inputs["Base Color"])

        ramp_node = nodes.new("ShaderNodeValToRGB")
        ramp_node.label = "Body Alpha Ramp"
        ramp_node.location = (-200, 0)
        ramp = ramp_node.color_ramp
        ramp.interpolation = "CONSTANT"
        ramp.elements[0].position = 0.0
        ramp.elements[0].color = (0.0, 0.0, 0.0, 0.0)
        ramp.elements[1].position = 0.01
        ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
        links.new(albedo_node.outputs["Color"], ramp_node.inputs["Fac"])
        # Alpha: black@0 → white@0.01, Constant (hard cut for skin mask).
        links.new(ramp_node.outputs["Alpha"], principled.inputs["Alpha"])
        _set_material_alpha_mode(mat, mode="CLIP", threshold=0.05, two_sided=False)

    try:
        if skin_mi:
            obj["arc_skin_color_mi"] = os.path.abspath(skin_mi)
            mat["arc_skin_color_mi"] = os.path.abspath(skin_mi)
    except Exception:
        pass

    if normal_path or micro_path:
        normal_flipper = None
        if utils.ensure_node_group("NormalFlipper"):
            normal_flipper = nodes.new("ShaderNodeGroup")
            normal_flipper.node_tree = bpy.data.node_groups["NormalFlipper"]
            normal_flipper.location = (-800, -200)

        if normal_path and normal_flipper:
            img_normal = bpy.data.images.load(normal_path, check_existing=True)
            img_normal.colorspace_settings.name = "Non-Color"
            normal_tex = nodes.new("ShaderNodeTexImage")
            normal_tex.image = img_normal
            normal_tex.label = normal_name
            normal_tex.interpolation = "Cubic"
            normal_tex.location = (-1100, -200)
            links.new(normal_tex.outputs["Color"], normal_flipper.inputs[0])

        if micro_path:
            tex_coord = nodes.new("ShaderNodeTexCoord")
            tex_coord.location = (-1500, -500)
            mapping = nodes.new("ShaderNodeMapping")
            mapping.location = (-1300, -500)
            mapping.inputs["Scale"].default_value = (15.0, 15.0, 15.0)
            links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])

            img_micro = bpy.data.images.load(micro_path, check_existing=True)
            img_micro.colorspace_settings.name = "Non-Color"
            micro_node = nodes.new("ShaderNodeTexImage")
            micro_node.image = img_micro
            micro_node.label = "T_SkinMicroNormal_01"
            micro_node.interpolation = "Cubic"
            micro_node.location = (-1100, -500)
            links.new(mapping.outputs["Vector"], micro_node.inputs["Vector"])

        mix_node = nodes.new("ShaderNodeMix")
        mix_node.data_type = 'RGBA'
        mix_node.blend_type = 'OVERLAY'
        mix_node.location = (-500, -300)
        mix_node.inputs["Factor"].default_value = 1.0

        if normal_flipper:
            links.new(normal_flipper.outputs[0], mix_node.inputs[6])
        if micro_path:
            links.new(micro_node.outputs["Color"], mix_node.inputs[7])

        normal_map_node = nodes.new("ShaderNodeNormalMap")
        normal_map_node.location = (-100, -300)
        try:
            normal_map_node.convention = 'DIRECTX'
        except Exception:
            pass
        links.new(mix_node.outputs[2], normal_map_node.inputs["Color"])
        links.new(normal_map_node.outputs["Normal"], principled.inputs["Normal"])
        if "Coat Normal" in principled.inputs:
            links.new(normal_map_node.outputs["Normal"], principled.inputs["Coat Normal"])


    # Apply exact outfit UV1 coverage without changing body geometry or the
    # existing skin alpha ramp. Assembly sync reapplies this after either order.
    from .. import body_mask
    body_mask.apply_body_masks(obj)


def _apply_default_skin_bsdf(principled, subsurface_color=None) -> None:
    """Default Principled settings for Avatar body/face skin (SSS + soft coat).

    Shared by body materials, Face Shader's internal Principled, and head
    Principled fallback so face/body share the same skin response.
    """

    def _set(name: str, value) -> None:
        sock = principled.inputs.get(name)
        if sock is None:
            return
        try:
            sock.default_value = value
        except Exception:
            pass

    try:
        principled.subsurface_method = "RANDOM_WALK_SKIN"
    except Exception:
        try:
            principled.subsurface_method = "RANDOM_WALK"
        except Exception:
            pass
    try:
        principled.distribution = "MULTI_GGX"
    except Exception:
        pass

    _set("Metallic", 0.0)
    _set("Roughness", 0.6)
    _set("IOR", 1.45)
    _set("Subsurface Weight", 0.1)
    _set("Subsurface Radius", (1.0, 0.2, 0.1))
    _set("Subsurface Scale", 0.03)
    _set("Subsurface IOR", 1.45)
    _set("Subsurface Anisotropy", 0.0)
    _set("Specular IOR Level", 0.44)
    _set("Specular Tint", (0.55, 0.55, 0.55, 1.0))
    _set("Anisotropic", 0.0)
    _set("Anisotropic Rotation", 0.0)
    _set("Coat Weight", 0.1)
    _set("Coat Roughness", 0.5)
    _set("Coat IOR", 1.45)
    _set("Coat Tint", (1.0, 1.0, 1.0, 1.0))
    if subsurface_color is not None:
        rgba = tuple(float(c) for c in subsurface_color[:4])
        if len(rgba) < 4:
            rgba = (rgba + (1.0, 1.0, 1.0, 1.0))[:4]
        # Blender 4+/5: Subsurface Color removed; tint via Radius when possible.
        if principled.inputs.get("Subsurface Color") is not None:
            _set("Subsurface Color", rgba)
        else:
            # Approximate: scale default radius by subsurface hue.
            _set("Subsurface Radius", (
                max(0.05, rgba[0] * 2.5),
                max(0.05, rgba[1] * 1.2),
                max(0.05, rgba[2] * 0.8),
            ))


def _align_face_shader_skin_defaults(subsurface_color=None) -> None:
    """Push body skin BSDF defaults onto ArcTexturer Face Shader Principled."""
    ng = utils.find_node_group("Face Shader") or bpy.data.node_groups.get("Face Shader")
    if ng is None:
        return
    for node in ng.nodes:
        if node.type == "BSDF_PRINCIPLED":
            _apply_default_skin_bsdf(node, subsurface_color=subsurface_color)
            break


def _find_face_skin_color_mi(psk_or_folder: str) -> str:
    """Locate ``MI_Face_*_SkinColor.json`` beside a head mesh / MI folder."""
    folder = psk_or_folder or ""
    if folder and os.path.isfile(folder):
        folder = os.path.dirname(folder)
    if not folder or not os.path.isdir(folder):
        return ""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return ""
    for fname in names:
        low = fname.lower()
        if low.startswith("mi_face_") and low.endswith("_skincolor.json"):
            return os.path.join(folder, fname)
    # Face_14_Pio/Base → MI_Face_14_SkinColor.json
    m = re.search(r"Face[_\s]?(\d+)", folder, re.I)
    if m:
        cand = os.path.join(folder, f"MI_Face_{int(m.group(1)):02d}_SkinColor.json")
        if os.path.isfile(cand):
            return cand
        cand = os.path.join(folder, f"MI_Face_{m.group(1)}_SkinColor.json")
        if os.path.isfile(cand):
            return cand
    return ""


def _parse_skin_color_mi(json_path: str) -> dict:
    """Parse ``MI_Face_*_SkinColor`` (parent ``M_Skin``) ColorMultiply / SSS.

    Game path: ``Characters/Heads/Face_N_Pio/Base/MI_Face_N_SkinColor.json``
    → parent ``M_Skin`` (``Characters/Shared/Bodies/M_Skin``). Body albedo is
    tinted by ``ColorMultiply``; SSS by ``SubsurfaceColor`` + ``SP_Head``.
    """
    result = {
        "path": "",
        "color_multiply": None,
        "subsurface_color": None,
        "skin_desaturation": None,
    }
    if not json_path or not os.path.isfile(json_path):
        return result
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return result
    result["path"] = os.path.abspath(json_path)
    entry = utils.first_ue_export(data, "MaterialInstanceConstant") or utils.first_ue_export(data)
    props = (entry or {}).get("Properties") or {}
    for vp in props.get("VectorParameterValues") or []:
        if not isinstance(vp, dict):
            continue
        name = str((vp.get("ParameterInfo") or {}).get("Name") or "")
        val = vp.get("ParameterValue")
        if not isinstance(val, dict):
            continue
        try:
            rgba = (
                float(val.get("R", 1.0)),
                float(val.get("G", 1.0)),
                float(val.get("B", 1.0)),
                float(val.get("A", 1.0)),
            )
        except (TypeError, ValueError):
            continue
        if name == "ColorMultiply":
            result["color_multiply"] = rgba
        elif name == "SubsurfaceColor":
            result["subsurface_color"] = rgba
    for sp in props.get("ScalarParameterValues") or []:
        if not isinstance(sp, dict):
            continue
        name = str((sp.get("ParameterInfo") or {}).get("Name") or "")
        if name != "SkinDesaturation":
            continue
        try:
            result["skin_desaturation"] = float(sp.get("ParameterValue"))
        except (TypeError, ValueError):
            pass
    return result


def _resolve_skin_color_mi_for_obj(obj, psk_path: str = "") -> str:
    """Resolve SkinColor MI from head folder, object props, or paired head slot."""
    if obj is not None:
        try:
            stored = str(obj.get("arc_skin_color_mi") or "")
            if stored and os.path.isfile(stored):
                return stored
        except Exception:
            pass
    found = _find_face_skin_color_mi(psk_path)
    if found:
        return found
    # Body: pair with Head on the same appearance import slot.
    if obj is not None:
        try:
            slot = obj.get("arc_import_slot")
        except Exception:
            slot = None
        for other in bpy.data.objects:
            if other == obj or other.type != "MESH":
                continue
            try:
                if other.get("arc_assembly_role") != "Head" and other.get("arc_model_type") != "face":
                    continue
                if slot is not None and other.get("arc_import_slot") != slot:
                    continue
            except Exception:
                continue
            other_psk = ""
            try:
                other_psk = str(other.get("arc_psk_path") or "")
            except Exception:
                other_psk = ""
            try:
                stored = str(other.get("arc_skin_color_mi") or "")
                if stored and os.path.isfile(stored):
                    return stored
            except Exception:
                pass
            found = _find_face_skin_color_mi(other_psk)
            if found:
                return found
    return ""


def _mix_color_multiply(nodes, links, color_sock, rgba, loc):
    """Multiply a color socket by SkinColor ``ColorMultiply`` (game M_Skin tint)."""
    if color_sock is None or not rgba:
        return color_sock
    rgb = nodes.new("ShaderNodeRGB")
    rgb.label = "ColorMultiply"
    rgb.outputs[0].default_value = (
        float(rgba[0]), float(rgba[1]), float(rgba[2]),
        float(rgba[3]) if len(rgba) > 3 else 1.0,
    )
    rgb.location = (loc[0] - 200, loc[1] - 120)
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.label = "Skin ColorMultiply"
    mix.location = loc
    mix.inputs["Factor"].default_value = 1.0
    links.new(color_sock, mix.inputs["A"])
    links.new(rgb.outputs[0], mix.inputs["B"])
    return mix.outputs["Result"]


def _apply_skin_desaturation(nodes, links, color_sock, amount, loc):
    """Optional SkinDesaturation scalar from SkinColor MI (0=full chroma)."""
    if color_sock is None or amount is None:
        return color_sock
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        return color_sock
    if amt <= 0.001:
        return color_sock
    hsv = nodes.new("ShaderNodeHueSaturation")
    hsv.label = "SkinDesaturation"
    hsv.location = loc
    hsv.inputs["Saturation"].default_value = max(0.0, 1.0 - min(1.0, amt))
    links.new(color_sock, hsv.inputs["Color"])
    return hsv.outputs["Color"]


# ---------------------------------------------------------------------------
# Face material setup
# ---------------------------------------------------------------------------

def _ensure_face_shader_node_group() -> bool:
    ok = utils.ensure_node_group("Face Shader")
    if ok:
        ng = utils.find_node_group("Face Shader") or bpy.data.node_groups.get("Face Shader")
        utils.strip_node_group_material_outputs(ng)
    return ok


def _strip_setup_suffixes(name: str) -> str:
    """Drop Blender ``.001`` and repeated ``_Setup`` so re-runs stay stable.

    Truncation to 63 chars can leave a dangling ``_Setu`` / ``_Set`` / ``_Se``.
    """
    text = (name or "Mat").strip()
    text = re.sub(r"\.\d+$", "", text)
    # Full ``_Setup`` runs, then a truncated tail from Blender's 63-char limit.
    text = re.sub(
        r"(?:_Setup)+(?:_Se(?:t(?:u)?)?)?$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"_Se(?:t(?:u)?)?$", "", text, flags=re.IGNORECASE)
    while text.lower().endswith("_setup"):
        text = text[:-6]
    return text or "Mat"


def _stable_setup_mat_name(src_name: str, suffix: str = "_Setup") -> str:
    return (_strip_setup_suffixes(src_name) + suffix)[:63]


def _reuse_or_new_material(slot, src_name: str, suffix: str = "_Setup"):
    """Replace slot material in-place with a stable ``*_Setup`` name (no stacking)."""
    want = _stable_setup_mat_name(src_name, suffix)
    mat = None
    if slot is not None and slot.material is not None:
        existing = slot.material
        if _strip_setup_suffixes(existing.name) == _strip_setup_suffixes(src_name):
            mat = existing
            if mat.name != want:
                try:
                    mat.name = want
                except Exception:
                    pass
    if mat is None:
        mat = bpy.data.materials.get(want) or bpy.data.materials.new(name=want)
    mat.use_nodes = True
    if slot is not None:
        slot.material = mat
    return mat



def _find_head_mi_json(psk_path: str, mi_name: str) -> str:
    folder = os.path.dirname(psk_path)
    if not folder or not mi_name:
        return ""
    path = os.path.join(folder, mi_name + ".json")
    if os.path.isfile(path):
        return path
    # Slot may be a short suffix ("Head") — match MI_*_<suffix>.json next to the mesh.
    suffix = mi_name.rsplit("_", 1)[-1] if "_" in mi_name else mi_name
    suffix_l = suffix.lower()
    try:
        for fname in sorted(os.listdir(folder)):
            low = fname.lower()
            if not low.startswith("mi_") or not low.endswith(".json"):
                continue
            stem = os.path.splitext(fname)[0]
            if stem.lower().endswith("_" + suffix_l) or stem.lower() == ("mi_" + suffix_l):
                return os.path.join(folder, fname)
    except OSError:
        pass
    return ""


def _mat_slot_role(mat_name: str, slot_name: str = "", mi_stem: str = "") -> str:
    """Map a head slot / MI name to a setup role (head, teeth, eyes, …)."""
    for raw in (mi_stem, mat_name, slot_name):
        text = (raw or "").strip()
        if not text:
            continue
        # Drop Blender .001 / our _Setup suffix so re-entry stays stable.
        text = re.sub(r"\.\d+$", "", text)
        if text.lower().endswith("_setup"):
            text = text[:-6]
        low = text.lower()
        # shader_head_shader / shader_eyeLeft_shader from SK JSON slot names.
        if "eyelash" in low:
            return "eyelashes"
        if "eyeshell" in low or "eye_shell" in low:
            return "eyeshell"
        if "eyeedge" in low or "eye_edge" in low:
            return "eyeedge"
        if re.search(r"(^|_)eyes?($|_)", low) or "eyeleft" in low or "eyeright" in low:
            return "eyes"
        if "teeth" in low or "saliva" in low:
            return "teeth"
        if re.search(r"(^|_)head($|_)", low) or "faceshader" in low or "skin" in low:
            return "head"
        suffix = text.rsplit("_", 1)[-1].lower()
        if suffix in (
            "head", "teeth", "saliva", "eyes", "eyelashes", "eyeshell", "eyeedge",
        ):
            return suffix
    return ""


def _ensure_head_material_slots(obj, psk_path: str) -> list:
    """Ensure Blender slots match SK SkeletalMaterials order (never reshuffle).

    ActorX heads often arrive with empty/placeholder slots. Rebuild *by index*
    from the sibling mesh JSON so polygon material_index stays valid. Returns
    ``[(role, mi_stem, mi_json_path), ...]`` aligned to slot indices.
    """
    if not obj or obj.type != "MESH":
        return []
    sk_slots = _parse_sk_material_slots(psk_path) if psk_path else []
    meta: list[tuple[str, str, str]] = []
    if sk_slots:
        used = 0
        try:
            used = max((p.material_index for p in obj.data.polygons), default=-1) + 1
        except Exception:
            used = 0
        n_existing = max(len(obj.data.materials), used)
        # PSK heads often combine eyeLeft+eyeRight into one slot (5 mats) while SK
        # lists 6. Expanding would shift EyeShell/Eyelashes indices and steal faces.
        if n_existing > 0 and len(sk_slots) > n_existing:
            for i in range(n_existing):
                mat = obj.data.materials[i] if i < len(obj.data.materials) else None
                role = ""
                mi_stem = ""
                mi_json = ""
                if mat is not None:
                    try:
                        role = str(mat.get("arc_head_role") or "")
                        mi_stem = str(mat.get("arc_mi_stem") or "")
                        mi_json = str(mat.get("arc_mi_path") or "")
                    except Exception:
                        pass
                    role = role or _mat_slot_role(mat.name, mi_stem=mi_stem)
                # Best-effort SK match by role / stem without reindexing faces.
                for slot_name, sk_stem, sk_json in sk_slots:
                    sk_role = _mat_slot_role(sk_stem or slot_name, slot_name, sk_stem)
                    if role and sk_role == role:
                        mi_stem = mi_stem or sk_stem
                        mi_json = mi_json if mi_json and os.path.isfile(mi_json) else sk_json
                        break
                    if mi_stem and sk_stem and mi_stem.lower() == sk_stem.lower():
                        role = role or sk_role
                        mi_json = mi_json if mi_json and os.path.isfile(mi_json) else sk_json
                        break
                if mat is not None:
                    try:
                        if role:
                            mat["arc_head_role"] = role
                        if mi_json:
                            mat["arc_mi_path"] = os.path.abspath(mi_json)
                        if mi_stem:
                            mat["arc_mi_stem"] = mi_stem
                    except Exception:
                        pass
                meta.append((role or "", mi_stem or "", mi_json or ""))
            return meta
        while len(obj.data.materials) < len(sk_slots):
            obj.data.materials.append(None)
        for i, (slot_name, mi_stem, mi_json) in enumerate(sk_slots):
            role = _mat_slot_role(mi_stem or slot_name, slot_name, mi_stem)
            name = (mi_stem or slot_name or f"HeadSlot{i}")[:63]
            mat = obj.data.materials[i] if i < len(obj.data.materials) else None
            if mat is None or not mat.use_nodes:
                mat = bpy.data.materials.new(name=name)
                mat.use_nodes = True
                obj.data.materials[i] = mat
            else:
                # Keep slot index; refresh name when still a generic placeholder.
                if mat.name.lower() in ("", "material", "defaultmaterial") or mat.name.startswith("Material"):
                    mat.name = name
            try:
                mat["arc_head_role"] = role or "head"
                if mi_json:
                    mat["arc_mi_path"] = os.path.abspath(mi_json)
                if mi_stem:
                    mat["arc_mi_stem"] = mi_stem
            except Exception:
                pass
            meta.append((role or "head", mi_stem or "", mi_json or ""))
        return meta

    # No SK JSON — synthesize from sibling MI_*.json (alphabetical; last resort).
    if any(s.material for s in obj.material_slots):
        for s in obj.material_slots:
            mat = s.material
            role = _mat_slot_role(mat.name if mat else "")
            mi = ""
            try:
                mi = (mat.get("arc_mi_path") if mat else "") or ""
            except Exception:
                mi = ""
            meta.append((role or "", "", mi))
        return meta
    folder = os.path.dirname(psk_path or "")
    if folder and os.path.isdir(folder):
        try:
            for fname in sorted(os.listdir(folder)):
                low = fname.lower()
                if not low.startswith("mi_") or not low.endswith(".json"):
                    continue
                stem = os.path.splitext(fname)[0]
                role = _mat_slot_role(stem)
                if role not in (
                    "head", "teeth", "saliva", "eyes", "eyelashes",
                    "eyeshell", "eyeedge",
                ):
                    continue
                mat = bpy.data.materials.new(name=stem[:63])
                mat.use_nodes = True
                try:
                    mat["arc_head_role"] = role
                    mat["arc_mi_path"] = os.path.abspath(os.path.join(folder, fname))
                    mat["arc_mi_stem"] = stem
                except Exception:
                    pass
                obj.data.materials.append(mat)
                meta.append((role, stem, os.path.join(folder, fname)))
        except OSError:
            pass
    if not meta:
        mat = bpy.data.materials.new(name="Head")
        mat.use_nodes = True
        try:
            mat["arc_head_role"] = "head"
        except Exception:
            pass
        obj.data.materials.append(mat)
        meta.append(("head", "", ""))
    return meta


def _setup_head_principled_from_mi(slot, psk_path: str, mi_path: str) -> None:
    """Principled fallback when ArcTexturer Face Shader is unavailable."""
    src_name = slot.material.name if slot and slot.material else "Head"
    mat = _reuse_or_new_material(slot, src_name)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (0, 0)
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (300, 0)
    links.new(principled.outputs["BSDF"], out_node.inputs["Surface"])

    skin_mi = _find_face_skin_color_mi(psk_path or mi_path or "")
    skin = _parse_skin_color_mi(skin_mi) if skin_mi else {}
    _apply_default_skin_bsdf(principled, subsurface_color=skin.get("subsurface_color"))

    albedo = normal = ""
    if mi_path:
        mi = _parse_flat_mi_json(mi_path)
        for param, obj_path in mi.get("textures") or []:
            pl = (param or "").lower()
            fpath = textures.find_texture_from_object_path(
                (obj_path or "") + ("" if str(obj_path).endswith(".0") else ".0")
            ) if obj_path else ""
            if not fpath and obj_path:
                fpath = textures.find_texture_from_object_path(obj_path)
            if not fpath:
                continue
            if pl in ("basecolor", "pm_diffuse", "diffuse") or pl.endswith("_d"):
                albedo = fpath
            elif pl in ("normal", "pm_normals", "normalmap") or pl.endswith("_n"):
                normal = fpath
    # Folder Textures fallback (T_*_Base_D / _N).
    if not albedo or not normal:
        tex_dir = os.path.join(os.path.dirname(psk_path or ""), "Textures")
        if os.path.isdir(tex_dir):
            try:
                for fname in os.listdir(tex_dir):
                    low = fname.lower()
                    if not low.endswith(".png"):
                        continue
                    full = os.path.join(tex_dir, fname)
                    if not albedo and (low.endswith("_d.png") or "_base_d" in low or low.endswith("_c.png")):
                        albedo = full
                    if not normal and (low.endswith("_n.png") or "_base_n" in low):
                        normal = full
            except OSError:
                pass
    row = 200
    if albedo and os.path.isfile(albedo):
        img = bpy.data.images.load(albedo, check_existing=True)
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = "BaseColor"
        node.location = (-400, row)
        # Face albedo stays authored; ColorMultiply is for body M_Skin matching.
        links.new(node.outputs["Color"], principled.inputs["Base Color"])
        row -= 280
    if normal and os.path.isfile(normal):
        img = bpy.data.images.load(normal, check_existing=True)
        try:
            img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = "Normal"
        node.interpolation = "Cubic"
        node.location = (-700, row)
        nrm_color = node.outputs["Color"]
        if utils.ensure_node_group("NormalFlipper"):
            flip = nodes.new("ShaderNodeGroup")
            flip.node_tree = (
                utils.find_node_group("NormalFlipper")
                or bpy.data.node_groups.get("NormalFlipper")
            )
            flip.location = (-480, row)
            if flip.inputs:
                links.new(nrm_color, flip.inputs[0])
            if flip.outputs:
                nrm_color = flip.outputs[0]
        nrm = nodes.new("ShaderNodeNormalMap")
        nrm.location = (-240, row)
        try:
            nrm.convention = "DIRECTX"
        except Exception:
            pass
        links.new(nrm_color, nrm.inputs["Color"])
        links.new(nrm.outputs["Normal"], principled.inputs["Normal"])
    try:
        if mi_path:
            mat["arc_mi_path"] = os.path.abspath(mi_path)
        if skin_mi:
            mat["arc_skin_color_mi"] = os.path.abspath(skin_mi)
    except Exception:
        pass


def _resolve_head_mi_path(psk_path: str, slot, mi_stem: str = "", mi_json: str = "") -> str:
    if mi_json and os.path.isfile(mi_json):
        return mi_json
    mat = slot.material if slot else None
    if mat is not None:
        try:
            stored = mat.get("arc_mi_path") or ""
            if stored and os.path.isfile(stored):
                return stored
        except Exception:
            pass
        stem = ""
        try:
            stem = mat.get("arc_mi_stem") or ""
        except Exception:
            stem = ""
        found = _find_head_mi_json(psk_path, stem or mat.name)
        if found:
            return found
    if mi_stem:
        found = _find_head_mi_json(psk_path, mi_stem)
        if found:
            return found
    return ""


def _load_head_face_textures_from_namemap(
    mi_path: str, psk_path: str, nodes, links, group_node,
) -> bool:
    """Cooked face MIs often only list texture stems in NameMap — wire Face Shader."""
    if not mi_path or group_node is None:
        return False
    stem_to_sock = {
        "base_d": "Face Albedo",
        "base_c": "Face Albedo",
        "_d": "Face Albedo",
        "base_n": "Normal 1",
        "_n": "Normal 1",
        "masks": "Blue Map",
        "zonemasks": "Blue Map",
    }
    wired = set()
    row = 200
    for stem, obj_path in _streaming_and_namemap_tex_refs(mi_path):
        low = stem.lower()
        sock_name = ""
        for key, sock in stem_to_sock.items():
            if key in low:
                sock_name = sock
                break
        if not sock_name or sock_name in wired:
            continue
        fpath = _find_tex_path(obj_path) if obj_path else ""
        if not fpath:
            # Sibling Textures/ folder (T_Face_10_Base_D.png).
            tex_dir = os.path.join(os.path.dirname(psk_path or mi_path or ""), "Textures")
            cand = os.path.join(tex_dir, stem + ".png")
            if os.path.isfile(cand):
                fpath = cand
            else:
                fpath = _find_tex_path(stem)
        if not fpath:
            continue
        img = bpy.data.images.load(fpath, check_existing=True)
        if sock_name != "Face Albedo":
            try:
                img.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.label = stem
        tex.interpolation = "Cubic"
        tex.location = (-400, row)
        row -= 280
        sock = group_node.inputs.get(sock_name)
        if sock is not None:
            links.new(tex.outputs["Color"], sock)
            wired.add(sock_name)
    return "Face Albedo" in wired


def _setup_head_slot_head(slot, psk_path: str, mi_path: str = ""):
    mi_path = mi_path or _resolve_head_mi_path(psk_path, slot)
    src_name = slot.material.name if slot.material else "Head"
    skin_mi = _find_face_skin_color_mi(psk_path or mi_path or "")
    skin = _parse_skin_color_mi(skin_mi) if skin_mi else {}
    if utils.ensure_node_group("Face Shader"):
        utils.repair_face_shader_normal_flipper()
        _align_face_shader_skin_defaults(subsurface_color=skin.get("subsurface_color"))
        mat = _reuse_or_new_material(slot, src_name)
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()
        group_node = nodes.new("ShaderNodeGroup")
        face_tree = utils.find_node_group("Face Shader") or bpy.data.node_groups.get("Face Shader")
        group_node.node_tree = face_tree
        group_node.location = (200, 0)
        out_node = nodes.new("ShaderNodeOutputMaterial")
        out_node.location = (480, 0)
        if group_node.outputs:
            links.new(group_node.outputs[0], out_node.inputs["Surface"])
        if mi_path:
            _load_mi_textures(mi_path, nodes, links, group_node, connected={
                "BaseColor": "Face Albedo",
                "PM_Diffuse": "Face Albedo",
                "Normal": "Normal 1",
                "PM_Normals": "Normal 1",
                "T_Head_01_MASKS": "Blue Map",
            })
        albedo = group_node.inputs.get("Face Albedo")
        normal1 = group_node.inputs.get("Normal 1")
        if albedo is not None and not albedo.links:
            _load_head_face_textures_from_namemap(mi_path, psk_path, nodes, links, group_node)
        # Ensure Normal 1 is Non-Color (Face Shader → NormalFlipper → Normal Map).
        if normal1 is not None and normal1.links:
            src = normal1.links[0].from_node
            if src.type == "TEX_IMAGE" and src.image:
                try:
                    src.image.colorspace_settings.name = "Non-Color"
                except Exception:
                    pass
        # If MI param names missed the normal, still pull T_*_Base_N from NameMap/folder.
        if normal1 is not None and not normal1.links:
            _load_head_face_textures_from_namemap(mi_path, psk_path, nodes, links, group_node)
        # Face albedo is per-face authored; SkinColor ColorMultiply tints body (M_Skin).
        if albedo is not None and albedo.links:
            try:
                mat["arc_head_role"] = "head"
                if mi_path:
                    mat["arc_mi_path"] = os.path.abspath(mi_path)
                if skin_mi:
                    mat["arc_skin_color_mi"] = os.path.abspath(skin_mi)
            except Exception:
                pass
            return
        # Face Shader present but textures missed — fall through to Principled.
    _setup_head_principled_from_mi(slot, psk_path, mi_path)
    if slot.material:
        try:
            slot.material["arc_head_role"] = "head"
            if skin_mi:
                slot.material["arc_skin_color_mi"] = os.path.abspath(skin_mi)
        except Exception:
            pass


# Bundled mouth atlas (game: Characters/Heads/Shared/Mouth/Textures/).
_TEETH_COLOR_ASSET = os.path.join("assets", "teeth", "teeth_color_map.png")
_TEETH_NORMAL_ASSET = os.path.join("assets", "teeth", "teeth_normal_map.png")


def _teeth_bundled_path(rel_under_addon: str) -> str:
    """Resolve ``assets/teeth/*.png`` next to the addon root."""
    addon_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(addon_root, rel_under_addon.replace("/", os.sep))
    return path if os.path.isfile(path) else ""


def _load_teeth_bundled_image(rel_under_addon: str, *, non_color: bool = False):
    path = _teeth_bundled_path(rel_under_addon)
    if not path:
        return None
    img = bpy.data.images.load(path, check_existing=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    try:
        if img.name != stem and stem not in bpy.data.images:
            img.name = stem
    except Exception:
        pass
    if non_color:
        try:
            img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
    return img


def _build_shared_teeth_material(teeth_ng) -> bpy.types.Material | None:
    """One shared Teeth material: bundled color/normal → Teeth group → Output."""
    if teeth_ng is None:
        return None
    mat = bpy.data.materials.get("Teeth")
    if mat is None:
        mat = bpy.data.materials.new(name="Teeth")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    color_img = _load_teeth_bundled_image(_TEETH_COLOR_ASSET, non_color=False)
    normal_img = _load_teeth_bundled_image(_TEETH_NORMAL_ASSET, non_color=True)

    color_tex = nodes.new("ShaderNodeTexImage")
    color_tex.name = "Teeth_Color"
    color_tex.label = "teeth_color_map"
    color_tex.location = (-520.0, 120.0)
    if color_img is not None:
        color_tex.image = color_img

    normal_tex = nodes.new("ShaderNodeTexImage")
    normal_tex.name = "Teeth_Normal"
    normal_tex.label = "teeth_normal_map"
    normal_tex.location = (-520.0, -160.0)
    if normal_img is not None:
        normal_tex.image = normal_img
        try:
            normal_tex.image.colorspace_settings.name = "Non-Color"
        except Exception:
            pass

    group_node = nodes.new("ShaderNodeGroup")
    group_node.node_tree = teeth_ng
    group_node.name = "Teeth"
    group_node.label = "Teeth"
    group_node.location = (0.0, 0.0)

    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (280.0, 0.0)

    base_in = group_node.inputs.get("Base Color")
    normal_in = group_node.inputs.get("Normal")
    if base_in is not None and color_tex.outputs:
        links.new(color_tex.outputs["Color"], base_in)
    if normal_in is not None and normal_tex.outputs:
        links.new(normal_tex.outputs["Color"], normal_in)
    if group_node.outputs:
        links.new(group_node.outputs[0], out_node.inputs["Surface"])

    _set_material_alpha_mode(mat, mode="OPAQUE", two_sided=False)
    try:
        mat["arc_head_role"] = "teeth"
    except Exception:
        pass
    return mat


def _setup_head_slot_teeth(slot, psk_path: str, mi_path: str = ""):
    """Shared Teeth material (bundled color+normal) on every teeth slot."""
    if not utils.ensure_node_group("Teeth"):
        return
    mi_path = mi_path or _resolve_head_mi_path(psk_path, slot)
    teeth_ng = utils.find_node_group("Teeth") or bpy.data.node_groups.get("Teeth")
    mat = _build_shared_teeth_material(teeth_ng)
    if mat is None:
        return
    slot.material = mat
    try:
        mat["arc_head_role"] = "teeth"
        if mi_path:
            mat["arc_mi_path"] = os.path.abspath(mi_path)
    except Exception:
        pass


# ArcTexturer.blend ships Eyes → group ``EYE Colour `` which historically nests
# ``Rust.003`` as its specular backend. That is a generic metal/dielectric stack
# (wraps Specular Cycles), not environment trim rust — but the name confuses
# users. We retarget the nest to a dedicated ``Eye Specular`` copy.
_EYE_COLOUR_GROUP = "EYE Colour "
_EYE_SPECULAR_GROUP = "Eye Specular"
_SPECULAR_CYCLES_GROUP = "Specular Cycles.002"


def _ensure_eye_specular_backend():
    """Copy ArcTexturer ``Rust.*`` specular stack to ``Eye Specular`` (same sockets)."""
    existing = utils.find_node_group(_EYE_SPECULAR_GROUP)
    if existing is not None:
        return True
    # Pull Eyes (brings EYE Colour + nested Rust.*) from ArcTexturer.blend.
    utils.ensure_material("Eyes")
    rust = None
    for name in ("Rust.003", "Rust.002", "Rust.001", "Rust"):
        rust = utils.find_node_group(name)
        if rust is not None:
            break
    if rust is None:
        for ng in bpy.data.node_groups:
            if (ng.name or "").startswith("Rust"):
                rust = ng
                break
    if rust is None:
        return False
    copy = rust.copy()
    try:
        copy.name = _EYE_SPECULAR_GROUP
    except Exception:
        pass
    return utils.find_node_group(_EYE_SPECULAR_GROUP) is not None


def _patch_eye_colour_backend() -> bool:
    """Point every ``EYE Colour*`` nested Rust group at ``Eye Specular``."""
    utils.ensure_material("Eyes")
    if not _ensure_eye_specular_backend():
        return False
    eye_spec = utils.find_node_group(_EYE_SPECULAR_GROUP)
    if eye_spec is None:
        return False
    patched_any = False
    for ng in list(bpy.data.node_groups):
        if not (ng.name or "").startswith("EYE Colour"):
            continue
        for node in ng.nodes:
            if node.type != "GROUP" or node.node_tree is None:
                continue
            tree_name = node.node_tree.name or ""
            if tree_name.startswith("Rust") or tree_name == _EYE_SPECULAR_GROUP:
                if node.node_tree != eye_spec:
                    node.node_tree = eye_spec
                    patched_any = True
                try:
                    node.label = _EYE_SPECULAR_GROUP
                except Exception:
                    pass
    return patched_any or utils.find_node_group(_EYE_COLOUR_GROUP) is not None


def _restore_eye_colour_default_output() -> None:
    """Undo Principled-Metal preference: EYE Colour → Eye Specular Specular Cycles path."""
    eye_colour = utils.find_node_group(_EYE_COLOUR_GROUP) or utils.find_node_group(
        "EYE Colour"
    )
    eye_spec = utils.find_node_group(_EYE_SPECULAR_GROUP)
    if eye_colour is None or eye_spec is None:
        return
    nest = next(
        (
            n
            for n in eye_colour.nodes
            if n.type == "GROUP" and n.node_tree == eye_spec
        ),
        None,
    )
    gout = next((n for n in eye_colour.nodes if n.type == "GROUP_OUTPUT"), None)
    if nest is None or gout is None:
        return
    # Prefer Specular Cycles / Specular socket; fall back to first non-Metal output.
    shader_out = nest.outputs.get("Specular")
    if shader_out is None:
        for out in nest.outputs:
            if (out.name or "").casefold() == "metal":
                continue
            if out.type in ("SHADER",):
                shader_out = out
                break
    if shader_out is None and nest.outputs:
        shader_out = nest.outputs[0]
    out_sock = gout.inputs.get("Specular")
    if out_sock is None and gout.inputs:
        out_sock = gout.inputs[0]
    if shader_out is None or out_sock is None:
        return
    for link in list(eye_colour.links):
        if link.to_socket == out_sock:
            eye_colour.links.remove(link)
    eye_colour.links.new(shader_out, out_sock)


def _setup_head_slot_eyes(slot, psk_path: str, mi_path: str = ""):
    """Assign ArcTexturer Eyes (EYE Colour → Eye Specular, not Rust).

    Reverts the EyeShell-merge / Principled-Metal experiment — shared Eyes mat only.
    """
    if not utils.ensure_material("Eyes"):
        return
    _patch_eye_colour_backend()
    _restore_eye_colour_default_output()
    slot.material = bpy.data.materials["Eyes"]
    try:
        slot.material["arc_head_role"] = "eyes"
        if mi_path:
            slot.material["arc_mi_path"] = os.path.abspath(mi_path)
    except Exception:
        pass


_EYELASH_MASK_OBJECT = (
    "/Game/Pioneer/Characters/Heads/Shared/Eyes/Textures/T_Eyelashes_M"
)


def _ensure_specular_cycles_group() -> bool:
    """Load ArcTexturer Specular Cycles (Alpha + Transparent mix) when present."""
    return utils.ensure_node_group(_SPECULAR_CYCLES_GROUP)


def _find_tex_path(obj_path: str) -> str:
    """Resolve a texture ObjectPath with and without the ``.0`` export suffix."""
    if not obj_path:
        return ""
    path = str(obj_path).strip()
    if path.startswith("/Engine/") or "CharacterSnow" in path:
        return ""
    for cand in (path, path + ".0" if not path.endswith(".0") else path[:-2]):
        fpath = textures.find_texture_from_object_path(cand)
        if fpath:
            return fpath
        # Bare stem → Shared Eyes / Hairs fallbacks via ObjectPath guess.
        leaf = cand.replace("\\", "/").rstrip("/").split("/")[-1]
        stem = leaf.split(".", 1)[0]
        if stem and "/" not in cand.strip("/"):
            for guess in (
                f"/Game/Pioneer/Characters/Heads/Shared/Eyes/Textures/{stem}",
                f"/Game/Pioneer/Characters/Hairs/BobSlick/Textures/{stem}",
            ):
                fpath = textures.find_texture_from_object_path(guess)
                if fpath:
                    return fpath
    return ""


def _mi_parent_chain_jsons(mi_path: str, max_depth: int = 5) -> list[str]:
    """Walk MI/Material Parent ObjectPaths; return existing sibling JSON paths."""
    out: list[str] = []
    seen: set[str] = set()
    cur = mi_path or ""
    for _ in range(max_depth):
        if not cur or not os.path.isfile(cur):
            break
        key = os.path.normcase(os.path.normpath(cur))
        if key in seen:
            break
        seen.add(key)
        out.append(cur)
        try:
            with open(cur, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            break
        entry = utils.first_ue_export(data, "MaterialInstanceConstant") or utils.first_ue_export(
            data, "Material"
        ) or utils.first_ue_export(data)
        props = (entry or {}).get("Properties") or {}
        parent_ref = props.get("Parent")
        obj_path = ""
        if isinstance(parent_ref, dict):
            obj_path = str(parent_ref.get("ObjectPath") or "").strip()
        if not obj_path:
            break
        parent_json = textures.find_asset_from_object_path(obj_path, ".json")
        if not parent_json and obj_path.endswith(".0"):
            parent_json = textures.find_asset_from_object_path(obj_path[:-2], ".json")
        cur = parent_json or ""
    return out


def _streaming_and_namemap_tex_refs(json_path: str) -> list[tuple[str, str]]:
    """Collect (stem, object_path) from TextureStreamingData + NameMap paths."""
    if not json_path or not os.path.isfile(json_path):
        return []
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    refs: list[tuple[str, str]] = []
    have: set[str] = set()
    entry = utils.first_ue_export(data, "MaterialInstanceConstant") or utils.first_ue_export(
        data, "Material"
    ) or utils.first_ue_export(data)
    props = (entry or {}).get("Properties") or {}
    for ts in props.get("TextureStreamingData") or []:
        if not isinstance(ts, dict):
            continue
        name = str(ts.get("TextureName") or "").strip()
        if not name or name.lower() in have:
            continue
        have.add(name.lower())
        refs.append((name, ""))
    meta = data.get("Metadata") if isinstance(data, dict) else None
    name_map = (meta or {}).get("NameMap") if isinstance(meta, dict) else None
    for entry_name in name_map or []:
        text = str(entry_name or "")
        if "/Game/" not in text and not text.startswith("T_"):
            continue
        if text.startswith("/Game/"):
            leaf = text.rstrip("/").split("/")[-1].split(".", 1)[0]
            if not leaf or leaf.lower() in have:
                continue
            # Skip non-texture package paths (Materials/, maps/, etc.).
            low = text.lower()
            if "/textures/" not in low and not leaf.lower().startswith("t_"):
                continue
            have.add(leaf.lower())
            refs.append((leaf, text.split(".", 1)[0]))
        elif text.startswith("T_"):
            stem = text.split(".", 1)[0]
            if stem.lower() in have:
                continue
            have.add(stem.lower())
            refs.append((stem, ""))
    return refs


def _resolve_eyelash_mask_path(mi_path: str = "") -> str:
    """Resolve T_Eyelashes_M via MI textures, parent chain, or Shared Eyes fallback.

    Face ``MI_*_Eyelashes`` dumps are thin children (no TextureParameterValues);
    the mask lives on ``M_Eyelashes`` TextureStreamingData / NameMap.
    """
    if mi_path:
        mi = _parse_flat_mi_json(mi_path)
        for param, obj_path in mi.get("textures") or []:
            blob = f"{param} {obj_path}".lower()
            if "eyelash" not in blob and "lash" not in blob:
                continue
            fpath = _find_tex_path(obj_path) or _find_tex_path(param)
            if fpath:
                return fpath
        for json_path in _mi_parent_chain_jsons(mi_path):
            for stem, obj_path in _streaming_and_namemap_tex_refs(json_path):
                blob = f"{stem} {obj_path}".lower()
                if "eyelash" not in blob and "lash" not in blob:
                    continue
                fpath = _find_tex_path(obj_path) if obj_path else ""
                if not fpath:
                    fpath = _find_tex_path(stem)
                if fpath:
                    return fpath
    for cand in (_EYELASH_MASK_OBJECT, _EYELASH_MASK_OBJECT + ".0"):
        fpath = textures.find_texture_from_object_path(cand)
        if fpath:
            return fpath
    return _find_tex_path("T_Eyelashes_M")


def _soft_mask_to_alpha(
    nodes, links, value_sock, loc, from_max: float = 0.35, power: float = 0.0,
):
    """Expand soft Coverage / lash masks into usable opacity.

    BobSlick Coverage and T_Eyelashes_M are low-mid grayscale (mean ~0.1). Mapping
    ``0..from_max`` → ``0..1`` keeps card volume. Optional ``power`` (<1) lifts
    midtones further after the range expand.
    """
    mapr = nodes.new("ShaderNodeMapRange")
    mapr.label = "Coverage Expand"
    mapr.clamp = True
    mapr.location = loc
    mapr.inputs["From Min"].default_value = 0.0
    mapr.inputs["From Max"].default_value = float(from_max)
    mapr.inputs["To Min"].default_value = 0.0
    mapr.inputs["To Max"].default_value = 1.0
    links.new(value_sock, mapr.inputs["Value"])
    out = mapr.outputs["Result"]
    if power and power > 0.0 and abs(power - 1.0) > 1e-6:
        node = nodes.new("ShaderNodeMath")
        node.operation = "POWER"
        node.label = "Soft Mask Lift"
        node.location = (loc[0] + 180, loc[1])
        node.inputs[1].default_value = float(power)
        links.new(out, node.inputs[0])
        return node.outputs["Value"]
    return out


def _wire_shader_surface(nodes, links, shader_out_sock, loc=(480, 0)):
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = loc
    links.new(shader_out_sock, out_node.inputs["Surface"])
    return out_node


def _resolve_scene_hair_color_rgba(colour_json: str = "") -> tuple:
    """Return ColorA RGBA from a HairColors MI or the scene hair-color choice."""
    path = (colour_json or "").strip()
    if (not path or path == "NONE" or not os.path.isfile(path)) and bpy.context:
        try:
            from .. import appearance as _appearance

            cid = getattr(bpy.context.scene, "arc_appearance_hair_color", "NONE") or "NONE"
            if cid != "NONE":
                path = _appearance.resolve_hair_color_path(cid)
        except Exception:
            path = path or ""
    if path and path != "NONE" and os.path.isfile(path):
        overlay = _parse_hair_mi(path)
        colours = {n: rgba for n, rgba in (overlay.get("colours") or [])}
        color_a = colours.get("ColorA") or colours.get("SecondaryColor")
        if color_a is not None:
            return tuple(color_a)
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        if "black" in stem:
            return (0.02, 0.015, 0.012, 1.0)
    return (0.0347, 0.0255, 0.0164, 1.0)


def _eyelash_copy_hair_color_enabled() -> bool:
    try:
        if bpy.context and bpy.context.scene:
            return bool(
                getattr(bpy.context.scene, "arc_appearance_eyelash_copy_hair_color", True)
            )
    except Exception:
        pass
    return True


def _apply_hair_color_to_eyelash_material(
    mat, colour_json: str = "", *, use_hair_color: bool | None = None,
) -> bool:
    """Tint an eyelash Principled Base Color from Hair ColorA (live Update Hair Color)."""
    if mat is None or not getattr(mat, "use_nodes", False):
        return False
    if use_hair_color is None:
        use_hair_color = _eyelash_copy_hair_color_enabled()
    if use_hair_color:
        rgba = _resolve_scene_hair_color_rgba(colour_json)
    else:
        rgba = (0.02, 0.015, 0.012, 1.0)
    principled = next(
        (n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"),
        None,
    )
    if principled is None:
        return False
    bc = principled.inputs.get("Base Color")
    if bc is None:
        return False
    # Prefer a labeled ColorA RGB node when present; else set Principled default.
    rgb = next(
        (
            n
            for n in mat.node_tree.nodes
            if n.type == "RGB" and (n.label or "") == "ColorA"
        ),
        None,
    )
    if rgb is None:
        rgb = mat.node_tree.nodes.new("ShaderNodeRGB")
        rgb.label = "ColorA"
        rgb.location = (-274, 173)
        # Replace any previous Base Color link with ColorA.
        for link in list(mat.node_tree.links):
            if link.to_socket == bc:
                mat.node_tree.links.remove(link)
        mat.node_tree.links.new(rgb.outputs[0], bc)
    rgb.outputs[0].default_value = rgba
    try:
        if (
            use_hair_color
            and colour_json
            and colour_json != "NONE"
            and os.path.isfile(colour_json)
        ):
            mat["arc_hair_color_mi"] = os.path.abspath(colour_json)
    except Exception:
        pass
    return True


def _setup_head_slot_eyelashes(slot, psk_path: str, mi_path: str = "", colour_json: str = ""):
    """Eyelash cards: T_Eyelashes_M Alpha → Alpha; Base Color from Hair ColorA.

    Matches the live ArcTexturer-style graph: ColorA RGB, Roughness 1, light Coat,
    HASHED alpha from mask Alpha (not soft-expand). Cooked face eyelash MIs inherit
    ``M_Eyelashes`` (DitherOpacityMask).
    """
    mi_path = mi_path or _resolve_head_mi_path(psk_path, slot)
    src_name = slot.material.name if slot.material else "Eyelashes"
    mat = _reuse_or_new_material(slot, src_name)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    mask_path = _resolve_eyelash_mask_path(mi_path)
    mi = _parse_flat_mi_json(mi_path) if mi_path else {}
    two_sided = bool(mi.get("two_sided", True))
    alpha_thresh = 0.05

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (200, 0)
    principled.inputs["Roughness"].default_value = 1.0
    coat = principled.inputs.get("Coat Weight")
    if coat is not None:
        coat.default_value = 0.15
    _wire_shader_surface(nodes, links, principled.outputs["BSDF"], (480, 0))
    _apply_hair_color_to_eyelash_material(
        mat,
        colour_json=colour_json,
        use_hair_color=_eyelash_copy_hair_color_enabled(),
    )

    if mask_path and os.path.isfile(mask_path):
        img = bpy.data.images.load(mask_path, check_existing=True)
        try:
            img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.label = "T_Eyelashes_M"
        tex.interpolation = "Cubic"
        tex.location = (-488, -73)
        # Live setup: Alpha channel → Principled Alpha.
        links.new(tex.outputs["Alpha"], principled.inputs["Alpha"])
        _set_material_alpha_mode(
            mat, mode="HASHED", threshold=alpha_thresh, two_sided=two_sided or True,
        )
    else:
        print(
            f"Arc Raiders PSK Importer: Eyelash mask not found for '{src_name}' "
            f"(mi={mi_path or 'NONE'})"
        )
        _set_material_alpha_mode(mat, mode="OPAQUE", two_sided=True)

    try:
        mat["arc_head_role"] = "eyelashes"
        if mi_path:
            mat["arc_mi_path"] = os.path.abspath(mi_path)
        if mask_path:
            mat["arc_eyelash_mask"] = os.path.abspath(mask_path)
    except Exception:
        pass


def _setup_head_slot_wet(slot):
    """EyeShell / EyeEdge → ArcTexturer Wet material (no face merge)."""
    if not utils.ensure_material("Wet"):
        return
    slot.material = bpy.data.materials["Wet"]
    try:
        slot.material["arc_head_role"] = "eyeshell"
    except Exception:
        pass


def _retint_paired_body_from_head(head_obj, skin_mi: str) -> None:
    """Re-run body material with this head's SkinColor so body matches face."""
    if not head_obj or not skin_mi or not os.path.isfile(skin_mi):
        return
    try:
        slot = head_obj.get("arc_import_slot")
    except Exception:
        slot = None
    for other in bpy.data.objects:
        if other == head_obj or other.type != "MESH":
            continue
        try:
            if other.get("arc_assembly_role") != "Body" and other.get("arc_model_type") != "body":
                continue
            if slot is not None and other.get("arc_import_slot") != slot:
                continue
            variant = str(other.get("arc_body_variant") or "NONE")
            psk = str(other.get("arc_psk_path") or "")
        except Exception:
            continue
        if variant == "NONE" or not psk:
            continue
        try:
            other["arc_skin_color_mi"] = os.path.abspath(skin_mi)
        except Exception:
            pass
        setup_body_material(other, psk, variant)


def setup_face_material(obj, psk_path: str):
    """Wire head slots in SK order. Never remove slots (keeps poly material_index)."""
    if not obj or obj.type != "MESH":
        return
    skin_mi = _find_face_skin_color_mi(psk_path)
    if skin_mi:
        try:
            obj["arc_skin_color_mi"] = os.path.abspath(skin_mi)
        except Exception:
            pass
    hair_color_json = ""
    try:
        hair_color_json = str(
            obj.get("arc_hair_color_mi")
            or getattr(bpy.context.scene, "arc_appearance_hair_color", "")
            or ""
        )
        # Scene enum stores choice id — resolve to MI path when needed.
        if hair_color_json and hair_color_json != "NONE" and not os.path.isfile(hair_color_json):
            from .. import appearance as _appearance

            hair_color_json = _appearance.resolve_hair_color_path(hair_color_json)
    except Exception:
        hair_color_json = ""
    meta = _ensure_head_material_slots(obj, psk_path)
    for i, slot in enumerate(obj.material_slots):
        if not slot.material and i < len(obj.data.materials):
            continue
        role = ""
        mi_stem = ""
        mi_json = ""
        if i < len(meta):
            role, mi_stem, mi_json = meta[i]
        if not role and slot.material:
            try:
                role = slot.material.get("arc_head_role") or ""
            except Exception:
                role = ""
            role = role or _mat_slot_role(slot.material.name, mi_stem=mi_stem)
        mi_path = mi_json if mi_json and os.path.isfile(mi_json) else ""
        if not mi_path:
            mi_path = _resolve_head_mi_path(psk_path, slot, mi_stem=mi_stem, mi_json=mi_json)
        if role == "head":
            _setup_head_slot_head(slot, psk_path, mi_path)
        elif role in ("teeth", "saliva"):
            _setup_head_slot_teeth(slot, psk_path, mi_path)
        elif role == "eyes":
            _setup_head_slot_eyes(slot, psk_path, mi_path)
        elif role == "eyelashes":
            _setup_head_slot_eyelashes(
                slot, psk_path, mi_path, colour_json=hair_color_json,
            )
        elif role in ("eyeshell", "eyeedge"):
            _setup_head_slot_wet(slot)
        elif len(obj.material_slots) == 1:
            _setup_head_slot_head(slot, psk_path, mi_path)
    # Body may have been imported first — retint with this face's SkinColor.
    if skin_mi:
        _retint_paired_body_from_head(obj, skin_mi)
    # Scalp Hair NXA / FacialHairNXA onto Face Shader when hair is already present.
    apply_hair_nxa_bridge(head_obj=obj)


# ---------------------------------------------------------------------------
# Hair material setup
# ---------------------------------------------------------------------------

def _hair_streaming_param_for_tex(texture_name: str) -> str:
    """Map TextureStreamingData names (BobSlick atlases) → hair MI param names."""
    tl = (texture_name or "").lower()
    if "coverage" in tl:
        return "Coverage"
    if "attribute" in tl:
        return "AttributeMap"
    if "depth" in tl:
        return "Depth"
    if "tangent" in tl:
        return "Tangent"
    if "globalhaircolor" in tl or ("fur" in tl and "basecolor" in tl):
        return "GlobalHairColor"
    return ""



def _textures_from_import_map(data: dict) -> list[tuple[str, str, str]]:
    """Cooked hair MIs often omit TextureParameterValues; textures live in Metadata.ImportMap."""
    meta = data.get("Metadata") if isinstance(data, dict) else None
    if not isinstance(meta, dict):
        return []
    out: list[tuple[str, str, str]] = []
    for imp in meta.get("ImportMap") or []:
        if not isinstance(imp, dict):
            continue
        oname = str(imp.get("ObjectName") or "")
        opath = str(imp.get("ObjectPath") or "")
        m = re.search(r"Texture2D'([^']+)'", oname, re.I)
        if not m:
            continue
        stem = m.group(1)
        param = _hair_streaming_param_for_tex(stem) or stem
        # ObjectPath may be package path without /Game prefix; normalize for resolver.
        if opath and not opath.startswith("/"):
            opath = "/" + opath.lstrip("/")
        if opath and "." in opath.split("/")[-1]:
            opath = opath.rsplit(".", 1)[0]
        out.append((param, stem, opath))
    return out


def _hair_card_files(folder: str) -> dict:
    """Card atlases sitting beside a pak hair mesh when the MI JSON did not parse."""
    found = {}
    if not folder:
        return found
    roots = [folder, os.path.join(folder, "Textures")]
    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            low = name.lower()
            if not low.endswith(".png"):
                continue
            param = ""
            if "cardsatlas_coverage" in low or low.endswith("_coverage.png"):
                param = "Coverage"
            elif "cardsatlas_attribute" in low or "attributemap" in low:
                param = "AttributeMap"
            elif "cardsatlas_depth" in low or low.endswith("_depth.png"):
                param = "Depth"
            elif "cardsatlas_tangent" in low:
                param = "Tangent"
            if param and param not in found:
                found[param] = os.path.join(root, name)
    return found


def _parse_hair_mi(json_path: str) -> dict:
    result = {
        'textures': [], 'colours': [], 'scalars': {},
        'two_sided': False, 'opacity_clip': 0.333, 'uv_channels': {},
    }
    if not json_path or json_path == 'NONE' or not os.path.isfile(json_path):
        return result
    try:
        with open(json_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        have = set()
        # Thin FModel dumps: top-level Textures / Parameters (no Exports).
        if isinstance(data, dict) and isinstance(data.get("Textures"), dict):
            for name, path in (data.get("Textures") or {}).items():
                if not name or not path:
                    continue
                # Skip duplicate stem aliases (same ObjectPath under atlas filename key).
                if name.startswith("T_") or name.startswith("Hair_") or name.startswith("Beard_"):
                    if name not in (
                        "Coverage", "AttributeMap", "Depth", "Tangent", "GlobalHairColor",
                        "PM_Diffuse",
                    ):
                        # Keep param-style keys; drop raw atlas stem aliases.
                        continue
                obj_path = str(path)
                if "." in obj_path.split("/")[-1]:
                    obj_path = obj_path.rsplit(".", 1)[0]
                stem = obj_path.rstrip("/").split("/")[-1] if obj_path else ""
                param = name if name in (
                    "Coverage", "AttributeMap", "Depth", "Tangent", "GlobalHairColor",
                ) else (_hair_streaming_param_for_tex(name) or _hair_streaming_param_for_tex(stem) or name)
                if param == "PM_Diffuse":
                    param = "Coverage"
                if param in have:
                    continue
                result['textures'].append((param, stem, obj_path))
                have.add(param)
            params = data.get("Parameters") or {}
            for name, val in (params.get("Colors") or {}).items():
                if not isinstance(val, dict):
                    continue
                try:
                    result['colours'].append((name, (
                        float(val.get('R', 1.0)),
                        float(val.get('G', 1.0)),
                        float(val.get('B', 1.0)),
                        float(val.get('A', 1.0)),
                    )))
                except (TypeError, ValueError):
                    continue
            for name, val in (params.get("Scalars") or {}).items():
                try:
                    result['scalars'][str(name)] = float(val)
                except (TypeError, ValueError):
                    pass
            bpo = (params.get("Properties") or {}).get("BasePropertyOverrides") or {}
            if bpo.get('TwoSided') is not None:
                result['two_sided'] = bool(bpo.get('TwoSided'))
            if bpo.get('OpacityMaskClipValue') is not None:
                try:
                    result['opacity_clip'] = float(bpo.get('OpacityMaskClipValue') or 0.333)
                except (TypeError, ValueError):
                    pass
            if result['textures']:
                return result
        entry = utils.first_ue_export(data, "MaterialInstanceConstant") or utils.first_ue_export(data)
        props = (entry or {}).get('Properties') or {}
        for tp in props.get('TextureParameterValues') or []:
            if not isinstance(tp, dict):
                continue
            name = (tp.get('ParameterInfo') or {}).get('Name', '')
            pv = tp.get('ParameterValue') or {}
            if not isinstance(pv, dict):
                continue
            obj_path = pv.get('ObjectPath', '') or ''
            obj_name = pv.get('ObjectName', '') or ''
            m = re.search(r"'([^']+)'", str(obj_name))
            stem = m.group(1) if m else ''
            if stem:
                result['textures'].append((name, stem, obj_path))
                have.add(name)
        # FoxHatCards only overrides GlobalHairColor; Coverage/Depth/Tangent live on
        # the Metahuman parent and appear in TextureStreamingData.
        for ts in props.get('TextureStreamingData') or []:
            if not isinstance(ts, dict):
                continue
            tname = str(ts.get('TextureName', '') or "").strip()
            if not tname:
                continue
            param = _hair_streaming_param_for_tex(tname)
            try:
                uv_ch = int(ts.get('UVChannelIndex', 0) or 0)
            except (TypeError, ValueError):
                uv_ch = 0
            if param:
                result['uv_channels'][param] = uv_ch
            if not param or param in have:
                continue
            result['textures'].append((param, tname, ""))
            have.add(param)
        # Pioneer cooked MIs: atlas refs only in ImportMap (no TextureParameterValues).
        if not result['textures']:
            for param, stem, opath in _textures_from_import_map(data):
                if param in have:
                    continue
                result['textures'].append((param, stem, opath))
                have.add(param)
        for vp in props.get('VectorParameterValues') or []:
            if not isinstance(vp, dict):
                continue
            name = (vp.get('ParameterInfo') or {}).get('Name', '')
            pv = vp.get('ParameterValue') or {}
            if not name or not isinstance(pv, dict):
                continue
            try:
                result['colours'].append((name, (
                    float(pv.get('R', 1.0)),
                    float(pv.get('G', 1.0)),
                    float(pv.get('B', 1.0)),
                    float(pv.get('A', 1.0)),
                )))
            except (TypeError, ValueError):
                continue
        for sp in props.get('ScalarParameterValues') or []:
            if not isinstance(sp, dict):
                continue
            name = str((sp.get('ParameterInfo') or {}).get('Name', '') or "")
            if not name:
                continue
            try:
                result['scalars'][name] = float(sp.get('ParameterValue'))
            except (TypeError, ValueError):
                pass
        bpo = props.get('BasePropertyOverrides') or {}
        if bpo.get('TwoSided') is not None:
            result['two_sided'] = bool(bpo.get('TwoSided'))
        if bpo.get('OpacityMaskClipValue') is not None:
            try:
                result['opacity_clip'] = float(bpo.get('OpacityMaskClipValue') or 0.333)
            except (TypeError, ValueError):
                pass
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to parse hair MI '{json_path}': {e}")
    return result



def _resolve_hair_texture(tex_stem: str, obj_path: str = "") -> str:
    """Resolve a hair atlas / colour map PNG from ObjectPath, FMDex, or Hairs/.

    FoxHatCards only overrides GlobalHairColor; Coverage/Depth/Tangent/Attribute
    come from TextureStreamingData names (BobSlick atlases) with no ObjectPath.
    FMDex often lacks those texture packages, so fall back to Content walks.
    """
    if obj_path:
        fpath = textures.find_texture_from_object_path(obj_path)
        if fpath:
            return fpath
    stem = (tex_stem or "").strip()
    if not stem:
        return ""
    try:
        from .. import fmdex
        found = fmdex.resolve_export_file(stem, ".png", context="any")
        if found and os.path.isfile(found):
            return found
    except Exception:
        pass

    # Guess common Metahuman / BobSlick atlas ObjectPaths.
    guesses = (
        f"/Game/Pioneer/Characters/Hairs/BobSlick/Textures/{stem}",
        f"/Game/Pioneer/Characters/Heads/Shared/Hair/Textures/{stem}",
    )
    for guess in guesses:
        fpath = textures.find_texture_from_object_path(guess)
        if fpath:
            return fpath

    # Last resort: basename under Characters/Hairs (and FacialHairs).
    target = (stem + ".png").lower()
    try:
        content_dirs = list(utils.get_content_dirs() or [])
    except Exception:
        content_dirs = []
    if not content_dirs:
        try:
            root = utils.get_pioneer_root() or ""
            cd = utils.find_content_dir(root) if root else ""
            if cd:
                content_dirs = [cd]
        except Exception:
            pass
    for content_dir in content_dirs:
        for rel in (
            os.path.join("Pioneer", "Characters", "Hairs"),
            os.path.join("Pioneer", "Characters", "FacialHairs"),
            os.path.join("Pioneer", "Characters", "Heads", "Shared", "Hair"),
        ):
            root = os.path.join(content_dir, rel)
            if not os.path.isdir(root):
                continue
            try:
                for walk_root, _dirs, files in os.walk(root):
                    for fname in files:
                        if fname.lower() == target:
                            return os.path.join(walk_root, fname)
            except OSError:
                continue
    return ""



def _merge_hair_colour_mi(mi: dict, colour_json: str) -> None:
    """Overlay ColorA/B (etc.) from a HairColors MI onto a style/atlas MI parse."""
    if not colour_json or colour_json == "NONE" or not os.path.isfile(colour_json):
        return
    overlay = _parse_hair_mi(colour_json)
    by_name = {n: rgba for n, rgba in (mi.get("colours") or [])}
    for n, rgba in overlay.get("colours") or []:
        by_name[n] = rgba
    # Black / Brunette often omit vectors — keep dark defaults when stem says black.
    stem = os.path.splitext(os.path.basename(colour_json))[0].lower()
    if "black" in stem and "ColorA" not in by_name:
        by_name["ColorA"] = (0.02, 0.015, 0.012, 1.0)
        by_name["ColorB"] = (0.01, 0.008, 0.006, 1.0)
    mi["colours"] = list(by_name.items())
    scalars = mi.setdefault("scalars", {})
    for k, v in (overlay.get("scalars") or {}).items():
        scalars[k] = v


def _style_dir_from_hair_hint(hint: str) -> str:
    """Directory for a hair mesh / MI / folder (style root when possible)."""
    p = os.path.abspath(hint or "")
    if not p:
        return ""
    if os.path.isfile(p):
        d = os.path.dirname(p)
    elif os.path.isdir(p):
        d = p
    else:
        return ""
    # Cooked colour MIs live under Materials/ — climb to the style folder.
    parts = d.replace("\\", "/").split("/")
    low = [x.lower() for x in parts]
    if "materials" in low:
        idx = low.index("materials")
        if idx > 0:
            return "/".join(parts[:idx]).replace("/", os.sep)
    return d


def _find_da_vp_json(style_dir: str) -> str:
    if not style_dir:
        return ""
    # Style root, then parent (Buzzcut/Skins/Coily → Buzzcut).
    roots = [style_dir, os.path.dirname(style_dir), os.path.dirname(os.path.dirname(style_dir))]
    seen: set[str] = set()
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        key = os.path.normcase(os.path.normpath(root))
        if key in seen:
            continue
        seen.add(key)
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            if name.lower().startswith("da_vp_") and name.lower().endswith(".json"):
                return os.path.join(root, name)
    return ""


def _scalp_mi_object_path_from_da_vp(da_path: str) -> str:
    """ImportMap scalp MI on a hair DA_VP (e.g. BowlCutMessy → Undercut scalp)."""
    if not da_path or not os.path.isfile(da_path):
        return ""
    try:
        with open(da_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return ""
    for imp in (data.get("Metadata") or {}).get("ImportMap") or []:
        if not isinstance(imp, dict):
            continue
        on = str(imp.get("ObjectName") or "")
        op = str(imp.get("ObjectPath") or "").replace("\\", "/")
        if "Scalp_Color" in on or "ScalpColors" in op:
            continue
        if "MI_Head_Scalp_" in on or "/Scalps/" in op:
            return op
    return ""


def _parse_scalp_nxa_pngs(scalp_json: str) -> tuple[str, str]:
    """Return (Hair NXA png, FacialHairNXA png) from a Scalps / beard scalp MI."""
    hair_nxa = ""
    facial_nxa = ""
    if not scalp_json or not os.path.isfile(scalp_json):
        return "", ""
    try:
        with open(scalp_json, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return "", ""
    entry = utils.first_ue_export(data, "MaterialInstanceConstant") or {}
    props = entry.get("Properties") or {}
    for tp in props.get("TextureParameterValues") or []:
        name = str((tp.get("ParameterInfo") or {}).get("Name") or "")
        op = str((tp.get("ParameterValue") or {}).get("ObjectPath") or "")
        if not op:
            continue
        fpath = _find_tex_path(op)
        if not fpath:
            continue
        nl = name.lower().replace(" ", "")
        if nl == "hairnxa" or name.strip() == "Hair NXA":
            hair_nxa = fpath
        elif "facial" in nl and "nxa" in nl:
            facial_nxa = fpath
        elif nl.endswith("nxa") and not hair_nxa:
            # Beard scalp MIs use FacialHairNXA only.
            facial_nxa = facial_nxa or fpath
    return hair_nxa, facial_nxa


def _guess_scalp_mi_json(style_dir: str) -> str:
    """Fuzzy-match style folder name to Materials/Scalps/MI_Head_Scalp_*_Base.json."""
    if not style_dir:
        return ""
    style = os.path.basename(style_dir.rstrip("\\/"))
    parent = os.path.basename(os.path.dirname(style_dir.rstrip("\\/")))
    grand = os.path.basename(os.path.dirname(os.path.dirname(style_dir.rstrip("\\/"))))
    candidates = [style]
    if parent.lower() == "skins" and grand:
        candidates.insert(0, f"{grand}_{style}")
        candidates.append(grand)

    scalp_dirs: list[str] = []
    try:
        content_dirs = list(utils.get_content_dirs() or [])
    except Exception:
        content_dirs = []
    for content_dir in content_dirs:
        for rel in (
            os.path.join("Pioneer", "Characters", "Hairs", "Materials", "Scalps"),
            os.path.join("Pioneer", "Characters", "FacialHairs", "Materials"),
        ):
            d = os.path.join(content_dir, rel)
            if os.path.isdir(d):
                scalp_dirs.append(d)

    bases: list[tuple[str, str]] = []  # (stem, fullpath)
    for scalp_dir in scalp_dirs:
        try:
            for walk_root, _dirs, files in os.walk(scalp_dir):
                if "scalpcolors" in walk_root.replace("\\", "/").lower():
                    continue
                for fname in files:
                    fl = fname.lower()
                    if not (fl.startswith("mi_head_scalp_") and fl.endswith("_base.json")):
                        continue
                    stem = fname[len("MI_Head_Scalp_") : -len("_Base.json")]
                    bases.append((stem, os.path.join(walk_root, fname)))
        except OSError:
            continue
    if not bases:
        return ""

    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())

    best = ""
    best_score = 0.0
    for cand in candidates:
        cn = _norm(cand)
        if not cn:
            continue
        for stem, path in bases:
            sn = _norm(stem)
            if not sn:
                continue
            if cn == sn:
                return path
            if cn in sn or sn in cn:
                score = float(min(len(cn), len(sn))) + 0.5
            else:
                score = difflib.SequenceMatcher(None, cn, sn).ratio()
            if score > best_score and score >= 0.72:
                best_score = score
                best = path
    return best


def _nxa_png_near_style(style_dir: str) -> str:
    """Last-resort scan for T_*_HairCards_NXA.png under the style folder."""
    if not style_dir or not os.path.isdir(style_dir):
        return ""
    prefer = ""
    other = ""
    try:
        for walk_root, _dirs, files in os.walk(style_dir):
            for fname in files:
                fl = fname.lower()
                if not fl.endswith(".png") or "nxa" not in fl:
                    continue
                full = os.path.join(walk_root, fname)
                if "haircards_nxa" in fl:
                    return full
                if not prefer and fl.endswith("_nxa.png"):
                    prefer = full
                elif not other:
                    other = full
    except OSError:
        return ""
    return prefer or other


def resolve_hair_nxa_textures(hair_hint: str) -> dict:
    """Resolve scalp-bridge NXA PNGs for a hair mesh, MI, or style folder.

    Order: DA_VP ImportMap → Scalps MI ``Hair NXA`` / ``FacialHairNXA``, then
    fuzzy Scalps MI, then folder ``*HairCards_NXA*.png``.
    """
    out = {"hair_nxa": "", "facial_nxa": "", "scalp_mi": "", "da_vp": ""}
    style_dir = _style_dir_from_hair_hint(hair_hint)
    da_path = _find_da_vp_json(style_dir)
    out["da_vp"] = da_path
    scalp_json = ""
    if da_path:
        op = _scalp_mi_object_path_from_da_vp(da_path)
        if op:
            scalp_json = textures.find_asset_from_object_path(op, ".json") or ""
            if not scalp_json and op.endswith(".0"):
                scalp_json = textures.find_asset_from_object_path(op[:-2], ".json") or ""
    if not scalp_json:
        scalp_json = _guess_scalp_mi_json(style_dir)
    out["scalp_mi"] = scalp_json
    hair_nxa, facial_nxa = _parse_scalp_nxa_pngs(scalp_json)
    if not hair_nxa:
        hair_nxa = _nxa_png_near_style(style_dir)
    out["hair_nxa"] = hair_nxa
    out["facial_nxa"] = facial_nxa
    return out


def _wire_face_shader_image(
    nodes, links, group_node, sock_name: str, fpath: str, label: str, loc,
) -> bool:
    sock = group_node.inputs.get(sock_name) if group_node is not None else None
    if sock is None or not fpath or not os.path.isfile(fpath):
        return False
    img = bpy.data.images.load(fpath, check_existing=True)
    try:
        img.colorspace_settings.name = "Non-Color"
    except Exception:
        pass
    if sock.links:
        src = sock.links[0].from_node
        if getattr(src, "type", "") == "TEX_IMAGE":
            src.image = img
            src.label = label
            return True
        for link in list(sock.links):
            links.remove(link)
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.label = label
    tex.interpolation = "Cubic"
    tex.location = loc
    links.new(tex.outputs["Color"], sock)
    return True


def _overlay_nxa_before_normal_flipper(mat, nxa_path: str, label: str = "Hair NXA") -> bool:
    """Principled head fallback: OVERLAY Hair NXA onto the base normal before NormalFlipper."""
    if mat is None or not getattr(mat, "use_nodes", False):
        return False
    if not nxa_path or not os.path.isfile(nxa_path):
        return False
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    flip = next(
        (
            n
            for n in nodes
            if n.type == "GROUP"
            and getattr(getattr(n, "node_tree", None), "name", "") == "NormalFlipper"
        ),
        None,
    )
    if flip is None or not flip.inputs:
        return False
    flip_in = flip.inputs[0]
    if not flip_in.links:
        return False
    prev = flip_in.links[0].from_node
    # Already bridged — refresh the NXA image on socket B.
    if prev.type == "MIX" and (prev.label or "") == "Hair NXA Overlay":
        b_sock = prev.inputs.get("B") or prev.inputs.get("B_Color")
        if b_sock is not None and b_sock.links:
            src = b_sock.links[0].from_node
            if getattr(src, "type", "") == "TEX_IMAGE":
                img = bpy.data.images.load(nxa_path, check_existing=True)
                try:
                    img.colorspace_settings.name = "Non-Color"
                except Exception:
                    pass
                src.image = img
                src.label = label
                return True
        return False

    base_sock = flip_in.links[0].from_socket
    links.remove(flip_in.links[0])
    img = bpy.data.images.load(nxa_path, check_existing=True)
    try:
        img.colorspace_settings.name = "Non-Color"
    except Exception:
        pass
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.label = label
    tex.interpolation = "Cubic"
    tex.location = (flip.location[0] - 360, flip.location[1] - 220)
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "OVERLAY"
    mix.label = "Hair NXA Overlay"
    mix.location = (flip.location[0] - 180, flip.location[1])
    fac = mix.inputs.get("Factor") or mix.inputs.get("Factor_Float")
    if fac is not None:
        fac.default_value = 1.0
    a_sock = mix.inputs.get("A") or mix.inputs.get("A_Color")
    b_sock = mix.inputs.get("B") or mix.inputs.get("B_Color")
    out_sock = mix.outputs.get("Result") or mix.outputs.get("Result_Color") or (
        mix.outputs[2] if len(mix.outputs) > 2 else mix.outputs[0]
    )
    if a_sock is not None:
        links.new(base_sock, a_sock)
    if b_sock is not None:
        links.new(tex.outputs["Color"], b_sock)
    links.new(out_sock, flip_in)
    return True


def sync_face_shader_hair(nxa_path: str = "", rgba=None) -> bool:
    """Set the Face Shader's Hair NXA image and the hair-color RGB beside it.

    In the working blend those live inside the Face Shader group:
    ``Hair NXA`` (image) Alpha → Mix factor, and the RGB node feeds Mix B
    against Face Albedo. That RGB is the hair color.
    """
    ng = bpy.data.node_groups.get("Face Shader")
    if ng is None:
        return False
    changed = False
    nxa = next(
        (
            n for n in ng.nodes
            if n.type == "TEX_IMAGE"
            and (
                (n.label or "") == "Hair NXA"
                or (getattr(n, "image", None) is not None and "NXA" in n.image.name.upper())
            )
        ),
        None,
    )
    if nxa is not None and nxa_path and os.path.isfile(nxa_path):
        try:
            nxa.image = bpy.data.images.load(nxa_path, check_existing=True)
            try:
                nxa.image.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
            nxa.label = "Hair NXA"
            changed = True
        except Exception:
            pass
    if rgba is None:
        return changed
    color = (float(rgba[0]), float(rgba[1]), float(rgba[2]), float(rgba[3]) if len(rgba) > 3 else 1.0)
    for node in ng.nodes:
        if node.type != "RGB":
            continue
        out = node.outputs[0] if node.outputs else None
        if out is None:
            continue
        for link in out.links:
            mix = link.to_node
            if getattr(mix, "type", "") != "MIX":
                continue
            for sock in mix.inputs:
                for src in sock.links:
                    if "Albedo" in (src.from_socket.name or ""):
                        out.default_value = color
                        node.label = node.label or "Hair Color"
                        return True
    return changed


def apply_nxa_to_head_object(head_obj, hair_nxa: str = "", facial_nxa: str = "") -> int:
    """Wire Hair NXA → Face Shader Normal 2 (or Principled NormalFlipper overlay).

    FacialHairNXA → Face Shader Normal 3 when that group is present.
    """
    if head_obj is None or head_obj.type != "MESH":
        return 0
    if not hair_nxa and not facial_nxa:
        return 0
    try:
        sync_face_shader_hair(hair_nxa, _resolve_scene_hair_color_rgba())
    except Exception:
        pass
    wired = 0
    for slot in head_obj.material_slots:
        mat = slot.material
        if mat is None or not getattr(mat, "use_nodes", False):
            continue
        role = str(mat.get("arc_head_role") or "")
        if role and role != "head":
            continue
        group_node = next(
            (
                n
                for n in mat.node_tree.nodes
                if n.type == "GROUP"
                and getattr(getattr(n, "node_tree", None), "name", "") == "Face Shader"
            ),
            None,
        )
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        if group_node is not None:
            if hair_nxa and _wire_face_shader_image(
                nodes, links, group_node, "Normal 2", hair_nxa, "Hair NXA", (-400, -200),
            ):
                wired += 1
                try:
                    mat["arc_hair_nxa"] = os.path.abspath(hair_nxa)
                except Exception:
                    pass
            if facial_nxa and _wire_face_shader_image(
                nodes, links, group_node, "Normal 3", facial_nxa, "FacialHairNXA", (-400, -480),
            ):
                wired += 1
                try:
                    mat["arc_facial_hair_nxa"] = os.path.abspath(facial_nxa)
                except Exception:
                    pass
            continue
        # Principled + NormalFlipper fallback (Face Shader missed textures / preload).
        if hair_nxa and _overlay_nxa_before_normal_flipper(mat, hair_nxa, label="Hair NXA"):
            wired += 1
            try:
                mat["arc_hair_nxa"] = os.path.abspath(hair_nxa)
            except Exception:
                pass
    return wired


def _iter_paired_heads(hair_obj):
    if hair_obj is None:
        return
    try:
        slot = hair_obj.get("arc_import_slot")
    except Exception:
        slot = None
    for other in bpy.data.objects:
        if other == hair_obj or other.type != "MESH":
            continue
        try:
            mtype = str(other.get("arc_model_type") or "").lower()
            role = str(other.get("arc_assembly_role") or "")
            if mtype not in ("face", "head") and role != "Head":
                continue
            if slot is not None and other.get("arc_import_slot") != slot:
                continue
        except Exception:
            continue
        yield other


def _iter_paired_hairs(head_obj):
    if head_obj is None:
        return
    try:
        slot = head_obj.get("arc_import_slot")
    except Exception:
        slot = None
    for other in bpy.data.objects:
        if other == head_obj or other.type != "MESH":
            continue
        try:
            mtype = str(other.get("arc_model_type") or "").lower()
            role = str(other.get("arc_assembly_role") or "")
            name_l = (other.name or "").lower()
            is_hair = (
                mtype == "hair"
                or role == "Hair"
                or "hair" in name_l
                or "facial" in name_l
            )
            if not is_hair:
                continue
            if slot is not None and other.get("arc_import_slot") != slot:
                continue
        except Exception:
            continue
        yield other


def _scene_hair_hint() -> str:
    """Path hint from the Appearance hair dropdown when no hair mesh is paired."""
    if not bpy.context:
        return ""
    try:
        from .. import appearance as _appearance

        hid = getattr(bpy.context.scene, "arc_appearance_hair", "NONE") or "NONE"
        if hid == "NONE":
            return ""
        choice = _appearance.resolve_choice(hid)
        return (choice or {}).get("path") or _appearance.resolve_choice_path(hid) or ""
    except Exception:
        return ""


def apply_hair_nxa_bridge(
    hair_obj=None,
    head_obj=None,
    hair_hint: str = "",
) -> int:
    """Resolve style Scalps NXA and overlay onto Face Shader head materials."""
    hint = (hair_hint or "").strip()
    if not hint and hair_obj is not None:
        try:
            hint = str(hair_obj.get("arc_psk_path") or hair_obj.get("arc_hair_mi") or "")
        except Exception:
            hint = ""
    if not hint and head_obj is not None:
        for hair in _iter_paired_hairs(head_obj):
            try:
                hint = str(hair.get("arc_psk_path") or hair.get("arc_hair_mi") or "")
            except Exception:
                hint = ""
            if hint:
                hair_obj = hair_obj or hair
                break
    if not hint:
        hint = _scene_hair_hint()
    if not hint:
        return 0

    resolved = resolve_hair_nxa_textures(hint)
    hair_nxa = resolved.get("hair_nxa") or ""
    facial_nxa = resolved.get("facial_nxa") or ""
    if hair_obj is not None:
        try:
            if hair_nxa:
                hair_obj["arc_hair_nxa"] = os.path.abspath(hair_nxa)
            if facial_nxa:
                hair_obj["arc_facial_hair_nxa"] = os.path.abspath(facial_nxa)
            if resolved.get("scalp_mi"):
                hair_obj["arc_scalp_mi"] = os.path.abspath(resolved["scalp_mi"])
        except Exception:
            pass

    heads = []
    if head_obj is not None:
        heads.append(head_obj)
    if hair_obj is not None:
        heads.extend(list(_iter_paired_heads(hair_obj)))
    # Dedupe
    seen: set[int] = set()
    uniq = []
    for h in heads:
        hid = id(h)
        if hid in seen:
            continue
        seen.add(hid)
        uniq.append(h)
    total = 0
    for h in uniq:
        total += apply_nxa_to_head_object(h, hair_nxa=hair_nxa, facial_nxa=facial_nxa)
    return total


def setup_hair_material(obj, json_path: str, colour_json: str = ""):
    """Build every authored card/accessory slot, including existing old imports."""
    from .. import textures, uemodel_import
    source = str(obj.get("arc_source_mesh") or obj.get("arc_psk_path") or "")
    native = os.path.splitext(source)[0] + ".uemodel"
    sections = []
    if os.path.isfile(native):
        try:
            _name, _version, lods = uemodel_import.parse_uemodel(native)
            sections = lods[0].materials if lods else []
        except (OSError, ValueError, uemodel_import.UeModelError):
            pass
    bindings = textures.authored_visual_part_materials(source) if source else []
    cards = textures.scan_hair_mis(source) if source else []
    if not textures.is_hair_cards_mi(json_path) and cards:
        json_path = cards[0][1]
    if not sections:
        _setup_hair_card_material(obj, json_path, colour_json)
        return
    previous_index = obj.active_material_index
    applied = []
    try:
        for index, (slot, reference, _first, _faces) in enumerate(sections):
            if index >= len(obj.material_slots):
                obj.data.materials.append(bpy.data.materials.new(str(slot)[:63]))
            mi_path = ""
            for binding in bindings:
                if binding["slot"].casefold() == str(slot).casefold():
                    mi_path = binding["path"]
                    break
            if not mi_path:
                mi_path = textures.find_asset_from_object_path(reference, ".json") if reference else ""
            if not mi_path:
                mi_path = textures.resolve_part_mi_json(os.path.join(os.path.dirname(source), str(slot) + ".json"), str(slot))
            card_slot = textures.is_hair_cards_mi(mi_path) if mi_path else False
            if not mi_path and len(sections) == 1:
                mi_path = json_path
                card_slot = textures.is_hair_cards_mi(mi_path)
            obj.active_material_index = index
            # Never reuse a shared source MI material for an independently coloured character.
            old = obj.active_material
            if old is not None and old.users > 1:
                obj.active_material = old.copy()
            if card_slot:
                _setup_hair_card_material(obj, mi_path, colour_json)
                applied.append(mi_path)
            elif mi_path and os.path.isfile(mi_path):
                from .dispatch import _dispatch_weapon_slot_material
                mat = obj.active_material
                if mat is None:
                    mat = bpy.data.materials.new((obj.name + "_" + str(slot))[:63])
                    obj.active_material = mat
                _dispatch_weapon_slot_material(mat, mi_path, source, os.path.basename(mi_path).lower(), str(slot).lower())
                mat["arc_mi_path"] = os.path.abspath(mi_path)
                for stamp in ("arc_hair_cards", "arc_hair_shader", "arc_hair_color_mi"):
                    if stamp in mat:
                        del mat[stamp]
        if applied:
            obj["arc_hair_mi"] = applied[0]
    finally:
        obj.active_material_index = min(previous_index, max(0, len(obj.material_slots) - 1))


def _setup_hair_card_material(obj, json_path: str, colour_json: str = ""):
    """Hair cards: Metahuman atlases → Principled + HASHED soft Coverage.

    ArcTexturer.blend has Face Shader / Teeth / Eyes but **no Hair group**.
    Specular Cycles.002 embeds Transparent BSDF (BLEND sorting) which punches
    holes through overlapping cards — unsuitable here. Coverage is soft RGB
    (Alpha=1); expand midtones and HASHED (not CLIP at 0.333).
    """
    base = _strip_setup_suffixes(getattr(obj, "name", "Hair"))
    suffix = f"_Hair_{obj.active_material_index}_Mat" if len(obj.material_slots) > 1 else "_Hair_Mat"
    mat_name = (base + suffix)[:63]
    # Reuse only this section, never another section's material with a matching name.
    mat = obj.active_material
    if mat is None:
        mat = bpy.data.materials.new(name=mat_name)
    else:
        try:
            mat.name = mat_name
        except Exception:
            pass
    mat.use_nodes = True
    obj.active_material = mat
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    if not json_path or json_path == "NONE":
        principled = nodes.new("ShaderNodeBsdfPrincipled")
        principled.location = (200, 0)
        _wire_shader_surface(nodes, links, principled.outputs["BSDF"])
        return

    mi = _parse_hair_mi(json_path)
    _merge_hair_colour_mi(mi, colour_json)
    if not mi.get("textures"):
        for param, fpath in _hair_card_files(os.path.dirname(json_path or "")).items():
            mi.setdefault("textures", []).append((param, os.path.splitext(os.path.basename(fpath))[0], fpath))
    loaded = {}
    for param_name, tex_stem, obj_path in mi["textures"]:
        fpath = obj_path if obj_path and os.path.isfile(obj_path) else ""
        if not fpath:
            fpath = _resolve_hair_texture(tex_stem, obj_path)
        if not fpath:
            continue
        img = bpy.data.images.load(fpath, check_existing=True)
        loaded[param_name] = (fpath, img)

    uv_channels = mi.get("uv_channels") or {}
    # FoxHatCards: GlobalHairColor → UV1; Coverage/atlases → UV0.
    if "GlobalHairColor" in loaded and "GlobalHairColor" not in uv_channels:
        uv_channels["GlobalHairColor"] = 1

    uv_cache = {}

    def _uv_for(param_name: str, loc):
        idx = int(uv_channels.get(param_name, 0) or 0)
        if idx in uv_cache:
            return uv_cache[idx]
        uv = nodes.new("ShaderNodeUVMap")
        uv.uv_map = f"UV{idx}"
        uv.label = f"UV{idx}"
        uv.location = loc
        uv_cache[idx] = uv
        return uv

    # Hair cards: Principled + HASHED (not Specular Cycles Transparent).
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (900, 0)
    _wire_shader_surface(nodes, links, principled.outputs["BSDF"], (1180, 0))
    group_node = None

    def _albedo_input():
        return principled.inputs.get("Base Color")

    def _alpha_input():
        return principled.inputs.get("Alpha")

    def _rough_input():
        return principled.inputs.get("Roughness")

    def _ao_input():
        return None

    def _transmission_input():
        return principled.inputs.get("Transmission Weight") or principled.inputs.get("Transmission")

    def _tangent_input():
        return principled.inputs.get("Tangent")

    x = -900
    row = 520

    colours = {n: rgba for n, rgba in (mi.get("colours") or [])}
    color_a = colours.get("ColorA") or colours.get("SecondaryColor") or (0.12, 0.07, 0.04, 1.0)
    color_b = colours.get("ColorB") or colours.get("SecondaryColorB") or (0.04, 0.025, 0.015, 1.0)
    rgb_a = nodes.new("ShaderNodeRGB")
    rgb_a.label = "ColorA"
    rgb_a.outputs[0].default_value = color_a
    rgb_a.location = (x - 200, row + 160)
    rgb_b = nodes.new("ShaderNodeRGB")
    rgb_b.label = "ColorB"
    rgb_b.outputs[0].default_value = color_b
    rgb_b.location = (x - 200, row - 20)

    base_color_src = None
    attr_ao_sock = None
    attr_rough_sock = None

    if "GlobalHairColor" in loaded:
        _, img = loaded["GlobalHairColor"]
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = "GlobalHairColor"
        node.interpolation = "Cubic"
        node.location = (x, row)
        uv = _uv_for("GlobalHairColor", (x - 280, row))
        links.new(uv.outputs["UV"], node.inputs["Vector"])
        base_color_src = node.outputs["Color"]
        row -= 300

    two_sided = bool(mi.get("two_sided"))
    # Soft Coverage: never hard-CLIP at MI OpacityMaskClipValue 0.333.
    alpha_thresh = 0.05

    rough_min = float((mi.get("scalars") or {}).get("Roughness min", 0.35) or 0.35)
    rough_max = float((mi.get("scalars") or {}).get("Roughness max", 0.65) or 0.65)
    mid_rough = max(0.0, min(1.0, (rough_min + rough_max) * 0.5))
    rough_in = _rough_input()
    if rough_in is not None:
        try:
            rough_in.default_value = mid_rough
        except Exception:
            pass

    # AttributeMap (Metahuman / arc_texturer_revamped): A=palette+rough, B=root.
    if "AttributeMap" in loaded:
        _, img = loaded["AttributeMap"]
        try:
            img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = "AttributeMap"
        node.interpolation = "Cubic"
        node.location = (x, row)
        uv = _uv_for("AttributeMap", (x - 280, row))
        links.new(uv.outputs["UV"], node.inputs["Vector"])
        sep = nodes.new("ShaderNodeSeparateColor")
        sep.location = (x + 280, row)
        links.new(node.outputs["Color"], sep.inputs["Color"])
        # Palette factor = Attribute.A (image Alpha), not R.
        attr_a = node.outputs.get("Alpha") or sep.outputs["Red"]
        mix_col = nodes.new("ShaderNodeMixRGB")
        mix_col.blend_type = "MIX"
        mix_col.use_clamp = True
        mix_col.label = "ColorA/B"
        mix_col.location = (x + 500, row + 40)
        links.new(attr_a, mix_col.inputs["Fac"])
        links.new(rgb_a.outputs[0], mix_col.inputs["Color1"])
        links.new(rgb_b.outputs[0], mix_col.inputs["Color2"])
        # Root darken by Attribute.B (Blue).
        root_pow = nodes.new("ShaderNodeMath")
        root_pow.operation = "POWER"
        root_pow.label = "Root Power"
        root_pow.location = (x + 500, row - 120)
        root_pow.inputs[1].default_value = float(
            (mi.get("scalars") or {}).get("Root position", 4.0) or 4.0
        )
        links.new(sep.outputs["Blue"], root_pow.inputs[0])
        root_mul = nodes.new("ShaderNodeMixRGB")
        root_mul.blend_type = "MIX"
        root_mul.label = "Root Darken"
        root_mul.location = (x + 720, row + 40)
        root_mul.use_clamp = True
        root_tint = nodes.new("ShaderNodeRGB")
        root_tint.label = "Root Multiply"
        root_tint.outputs[0].default_value = colours.get("Root Multiply") or (0.38, 0.32, 0.27, 1.0)
        root_tint.location = (x + 500, row - 280)
        darkened = nodes.new("ShaderNodeMixRGB")
        darkened.blend_type = "MULTIPLY"
        darkened.location = (x + 720, row - 120)
        darkened.inputs["Fac"].default_value = 1.0
        links.new(mix_col.outputs["Color"], darkened.inputs["Color1"])
        links.new(root_tint.outputs[0], darkened.inputs["Color2"])
        links.new(root_pow.outputs["Value"], root_mul.inputs["Fac"])
        links.new(darkened.outputs["Color"], root_mul.inputs["Color1"])
        links.new(mix_col.outputs["Color"], root_mul.inputs["Color2"])
        if base_color_src is None:
            base_color_src = root_mul.outputs["Color"]
        else:
            mul2 = nodes.new("ShaderNodeMixRGB")
            mul2.blend_type = "MULTIPLY"
            mul2.location = (x + 900, row + 220)
            mul2.inputs["Fac"].default_value = 1.0
            links.new(base_color_src, mul2.inputs["Color1"])
            links.new(root_mul.outputs["Color"], mul2.inputs["Color2"])
            base_color_src = mul2.outputs["Color"]
        mapr = nodes.new("ShaderNodeMapRange")
        mapr.location = (x + 500, row - 400)
        mapr.inputs["From Min"].default_value = 0.0
        mapr.inputs["From Max"].default_value = 1.0
        mapr.inputs["To Min"].default_value = rough_min
        mapr.inputs["To Max"].default_value = rough_max
        links.new(attr_a, mapr.inputs["Value"])
        attr_rough_sock = mapr.outputs["Result"]
        row -= 420

    if base_color_src is None:
        base_color_src = rgb_a.outputs[0]
    albedo_in = _albedo_input()
    if albedo_in is not None:
        links.new(base_color_src, albedo_in)

    if attr_rough_sock is not None and rough_in is not None:
        links.new(attr_rough_sock, rough_in)
    ao_in = _ao_input()
    if attr_ao_sock is not None and ao_in is not None:
        links.new(attr_ao_sock, ao_in)

    # Coverage → soft Alpha (RGB mask; atlas Alpha is solid 1).
    if "Coverage" in loaded:
        _, img = loaded["Coverage"]
        try:
            img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = "Coverage (Alpha)"
        node.interpolation = "Cubic"
        node.location = (x, row)
        uv = _uv_for("Coverage", (x - 280, row))
        links.new(uv.outputs["UV"], node.inputs["Vector"])
        sep_c = nodes.new("ShaderNodeSeparateColor")
        sep_c.location = (x + 280, row)
        links.new(node.outputs["Color"], sep_c.inputs["Color"])
        # Soft Coverage midtones (mean ~0.1–0.4 on cards) → near-opaque via expand.
        alpha_sock = _soft_mask_to_alpha(
            nodes, links, sep_c.outputs["Red"], (x + 500, row), from_max=0.5,
        )
        alpha_in = _alpha_input()
        if alpha_in is not None:
            links.new(alpha_sock, alpha_in)
        row -= 300
        _set_material_alpha_mode(
            mat, mode="HASHED", threshold=alpha_thresh, two_sided=two_sided or True,
        )
    elif two_sided:
        _set_material_alpha_mode(mat, mode="OPAQUE", two_sided=True)

    # Depth → transmission.
    if "Depth" in loaded:
        _, img = loaded["Depth"]
        try:
            img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = "Depth"
        node.interpolation = "Cubic"
        node.location = (x, row)
        uv = _uv_for("Depth", (x - 280, row))
        links.new(uv.outputs["UV"], node.inputs["Vector"])
        sep_d = nodes.new("ShaderNodeSeparateColor")
        sep_d.location = (x + 280, row)
        links.new(node.outputs["Color"], sep_d.inputs["Color"])
        mul_d = nodes.new("ShaderNodeMath")
        mul_d.operation = "MULTIPLY"
        mul_d.location = (x + 500, row)
        mul_d.inputs[1].default_value = 0.25
        links.new(sep_d.outputs["Red"], mul_d.inputs[0])
        tw = _transmission_input()
        if tw is not None:
            links.new(mul_d.outputs["Value"], tw)
        row -= 300

    # Tangent atlas only on Principled fallback (Specular Cycles has no Tangent sock).
    if "Tangent" in loaded and principled is not None:
        _, img = loaded["Tangent"]
        try:
            img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = "Tangent"
        node.interpolation = "Cubic"
        node.location = (x, row)
        uv = _uv_for("Tangent", (x - 280, row))
        links.new(uv.outputs["UV"], node.inputs["Vector"])
        tan_in = _tangent_input()
        if tan_in is not None:
            links.new(node.outputs["Color"], tan_in)
        aniso = principled.inputs.get("Anisotropic")
        if aniso is not None:
            aniso.default_value = 0.75
        row -= 300

    handled = {"GlobalHairColor", "Coverage", "AttributeMap", "Depth", "Tangent"}
    for param_name, (_fpath, img) in loaded.items():
        if param_name in handled:
            continue
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param_name
        node.interpolation = "Cubic"
        node.location = (x - 400, row)
        row -= 260

    try:
        mat["arc_mi_path"] = os.path.abspath(json_path)
        mat["arc_hair_cards"] = 1
        mat["arc_hair_shader"] = "Principled"
        if colour_json and colour_json != "NONE":
            mat["arc_hair_color_mi"] = os.path.abspath(colour_json)
            obj["arc_hair_color_mi"] = os.path.abspath(colour_json)
        else:
            for target in (mat, obj):
                if "arc_hair_color_mi" in target:
                    del target["arc_hair_color_mi"]
    except Exception:
        pass

    # Scalp bridge: style Hair NXA → paired head Face Shader Normal 2.
    hint = json_path or ""
    try:
        hint = hint or str(obj.get("arc_psk_path") or "")
    except Exception:
        pass
    apply_hair_nxa_bridge(hair_obj=obj, hair_hint=hint)

