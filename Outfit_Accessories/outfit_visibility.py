"""Apply explicitly selected DA_OI visual-part visibility modifiers by exact mesh identity."""
from __future__ import annotations

import os

from ..Shared import textures, utils

_VISIBILITY_MODIFIER = "CustomizationVisualPartSetVisibilityModifier"
_VISUAL_PART = "CustomizationVisualPartDataAsset"
_VISUAL_PART_MESH_MODIFIER = "CustomizationVisualPartSetMeshModifier"


def _exports(document):
    if isinstance(document, dict):
        return document.get("Exports") or [document]
    return document if isinstance(document, list) else []


def _asset_path(value):
    if isinstance(value, dict):
        return str(value.get("AssetPathName") or value.get("ObjectPath") or "")
    return str(value or "")


def _package_key(value):
    text = str(value or "").replace("\\", "/").strip()
    low = text.casefold()
    for marker in ("/pioneergame/content/", "/remappedplugins/content/", "/game/"):
        pos = low.find(marker)
        if pos >= 0:
            text = text[pos + len(marker):]
            low = text.casefold()
            break
    leaf = text.rsplit("/", 1)[-1]
    if "." in leaf:
        text = text[:len(text) - len(leaf)] + leaf.split(".", 1)[0]
    if "." in text.rsplit("/", 1)[-1]:
        text = text.rsplit(".", 1)[0]
    return text.strip("/").casefold()


def _mesh_keys_for_visual_part(part_ref):
    path = textures.find_asset_from_object_path(part_ref, ".json")
    if not path:
        return set()
    try:
        data = textures._load_ue_json(path) if hasattr(textures, "_load_ue_json") else None
    except Exception:
        data = None
    if data is None:
        from ..Shared import importing
        data = importing._load_ue_json(path)
    mesh_keys = set()
    for entry in utils.ue_export_entries(data) if data else []:
        kind = entry.get("Type") or ""
        props = entry.get("Properties") or {}
        if kind == _VISUAL_PART:
            for field in ("Mesh", "StaticMesh"):
                key = _package_key(_asset_path(props.get(field)))
                if key:
                    mesh_keys.add(key)
        elif kind == _VISUAL_PART_MESH_MODIFIER:
            key = _package_key(_asset_path(props.get("SkeletalMesh")))
            if key:
                mesh_keys.add(key)
    return mesh_keys


def _visible_value(value):
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes"}
    return bool(value)


_SLOT = "CharacterVisualSlotOnlineItemDataAsset"
_PART_ITEM = "CharacterVisualPartOnlineItemDataAsset"


def _visibility_desired(scene, option_paths, strict):
    desired = {}
    for option_path in option_paths or ():
        from ..Shared import importing
        data = importing._load_ue_json(str(option_path))
        if not data:
            continue
        for entry in utils.ue_export_entries(data):
            if entry.get("Type") != _VISIBILITY_MODIFIER:
                continue
            props = entry.get("Properties") or {}
            part_ref = _asset_path(props.get("Part"))
            part_key = _package_key(part_ref)
            mesh_keys = _mesh_keys_for_visual_part(part_ref)
            if not part_key or not mesh_keys:
                continue
            visible = _visible_value(props.get("bVisibility", False))
            for obj in scene.objects:
                if obj.type != "MESH":
                    continue
                explicit_value = obj.get("arc_visual_part") or obj.get("arc_visual_part_path")
                explicit = _package_key(explicit_value)
                if explicit_value:
                    matches = explicit == part_key
                else:
                    source = _package_key(obj.get("arc_source_mesh") or obj.get("arc_psk_path"))
                    matches = source in mesh_keys
                if matches:
                    prior = desired.get(obj)
                    if strict and prior is not None and prior != visible:
                        raise ValueError("Selected DA_OI options request conflicting visibility for exact mesh: " + obj.name)
                    desired[obj] = visible
    return desired


def preset_option_paths(outfit_dir):
    """DA_OI option files that the outfit card preset selects by game option tag.

    outfit_presets.PRESET_OPTION_TAGS maps a folder name to setting tag -> option
    tag. File names follow DA_OI_Outfit_<Folder>_<Setting>_<Option>.json.
    """
    from . import outfit_presets
    if not outfit_dir or not os.path.isdir(outfit_dir):
        return []
    folder = os.path.basename(os.path.normpath(outfit_dir))
    wanted = outfit_presets.PRESET_OPTION_TAGS.get(folder, {})
    result = []
    for setting_tag, option_tag in sorted(wanted.items()):
        setting = setting_tag.split(".")[-1]
        option = option_tag.split(".")[-1]
        path = os.path.join(outfit_dir, "DA_OI_Outfit_%s_%s_%s.json" % (folder, setting, option))
        if os.path.isfile(path):
            result.append(path)
    return result


def apply_visibility_options(scene, option_paths, preset_paths=()):
    """Apply DA_OI visibility modifiers by exact mesh identity.

    preset_paths (card preset options) are applied first and never raise on
    conflict. option_paths (explicit selections) are strict and override them.
    Unmentioned objects and visibility are left unchanged.
    """
    desired = _visibility_desired(scene, preset_paths, strict=False)
    desired.update(_visibility_desired(scene, option_paths, strict=True))
    for obj, visible in desired.items():
        hidden = not visible
        obj.hide_viewport = hidden
        obj.hide_render = hidden
        for layer in scene.view_layers:
            if obj.name in layer.objects:
                obj.hide_set(hidden, view_layer=layer)
    return len(desired)


def _object_source_key(obj):
    return _package_key(obj.get("arc_source_mesh") or obj.get("arc_psk_path") or "")


def _wrap_driver_keys_in_folder(folder, cache):
    """Mesh keys that DA_VP files in folder name only as WrapDeformation.OptionalWrapDeformerMesh."""
    if folder in cache:
        return cache[folder]
    keys = set()
    try:
        names = [n for n in os.listdir(folder) if n.startswith("DA_VP_") and n.endswith(".json")]
    except OSError:
        names = []
    from ..Shared import importing
    for name in names:
        data = importing._load_ue_json(os.path.join(folder, name))
        for entry in utils.ue_export_entries(data) if data else []:
            if entry.get("Type") != _VISUAL_PART:
                continue
            props = entry.get("Properties") or {}
            wrap = (props.get("WrapDeformation") or {}).get("OptionalWrapDeformerMesh")
            wrap_key = _package_key(_asset_path(wrap))
            mesh_key = _package_key(_asset_path(props.get("Mesh")))
            if wrap_key and wrap_key != mesh_key:
                keys.add(wrap_key)
    cache[folder] = keys
    return keys


def hide_wrap_deformer_drivers(scene):
    """Hide meshes that exist only to drive another part's wrap deformation.

    The game never draws a DA_VP OptionalWrapDeformerMesh (Banshee
    SK_Banshee_BeltStraps_ClothSim); importing its folder makes it a visible
    object. Render visibility and the view-layer hide are set; viewport
    evaluation stays on so anything bound to it still deforms.
    """
    cache, hidden = {}, []
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        source = str(obj.get("arc_source_mesh") or obj.get("arc_psk_path") or "")
        if not source:
            continue
        import bpy
        folder = os.path.dirname(textures._fs_abspath(bpy.path.abspath(source)))
        if _object_source_key(obj) in _wrap_driver_keys_in_folder(folder, cache):
            if not obj.hide_render:
                obj.hide_render = True
                for layer in scene.view_layers:
                    if obj.name in layer.objects:
                        obj.hide_set(True, view_layer=layer)
                hidden.append(obj.name)
    return hidden
