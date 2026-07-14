"""
Utility functions for the Arc Raiders Importer
"""

import os
import re
import json
import bpy
import mathutils
from mathutils import Vector

_ADDON_DIR = os.path.dirname(__file__)
_BLEND_PATH = os.path.join(_ADDON_DIR, "ArcTexturer.blend")
_NODE_GROUP = "Arc Texturer"
_COLORMASK_GROUP = "ColorMask_XYZ"

# ---------------------------------------------------------------------------
# Folder scanning
# ---------------------------------------------------------------------------

def find_psks_in_folder(folder: str) -> list:
    """Recursively scan subfolders for PSK files. Prefers LOD0."""
    results = []
    def scan(path):
        try:
            entries = sorted(os.listdir(path))
        except OSError:
            return
        psks = sorted(
            os.path.join(path, f) for f in entries
            if f.lower().endswith(".psk") or f.lower().endswith(".pskx")
        )
        if psks:
            lod0 = [p for p in psks if "lod0" in p.lower() and (p.lower().endswith(".psk") or p.lower().endswith(".pskx"))]
            results.append(lod0[0] if lod0 else psks[0])
            return
        for entry in entries:
            sub = os.path.join(path, entry)
            if os.path.isdir(sub):
                scan(sub)
    try:
        for sub in sorted(os.listdir(folder)):
            sub_path = os.path.join(folder, sub)
            if os.path.isdir(sub_path):
                scan(sub_path)
    except OSError:
        pass
    return results

def normalize_folder_name(name: str) -> str:
    """Normalize a folder/file name for fuzzy comparison."""
    return re.sub(r'[^a-z0-9]', '', name.lower())

def normalize_part_key(key: str) -> str:
    """Normalize a '<Character>/<Part>' key for fuzzy comparison."""
    return re.sub(r'[^a-z0-9/]', '', key.lower())

# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

_CONTENT_DIR_CACHE = {}
_RELATIVE_DIR_CACHE = {}
_OUTFIT_CHAR_MAP_CACHE = {}
_CACHE_ROOT_SEEN = [None]

def get_pioneer_root():
    try:
        root = bpy.context.scene.arc_pioneer_root
        return bpy.path.abspath(root) if root else ""
    except Exception:
        return ""

def invalidate_dir_caches_if_root_changed():
    root = get_pioneer_root()
    if root != _CACHE_ROOT_SEEN[0]:
        _CONTENT_DIR_CACHE.clear()
        _RELATIVE_DIR_CACHE.clear()
        _OUTFIT_CHAR_MAP_CACHE.clear()
        _CACHE_ROOT_SEEN[0] = root

def find_content_dir(root: str) -> str:
    if not root or not os.path.isdir(root):
        return ""
    if root in _CONTENT_DIR_CACHE:
        return _CONTENT_DIR_CACHE[root]
    found = ""
    if os.path.basename(os.path.normpath(root)).lower() == "content":
        found = root
    else:
        MAX_DEPTH = 6
        MAX_VISITED = 20000
        visited = 0
        queue = [(root, 0)]
        while queue:
            current, depth = queue.pop(0)
            visited += 1
            if visited > MAX_VISITED:
                break
            try:
                entries = os.listdir(current)
            except OSError:
                continue
            for entry in entries:
                full = os.path.join(current, entry)
                if not os.path.isdir(full):
                    continue
                if entry.lower() == "content":
                    found = full
                    queue = []
                    break
                if depth < MAX_DEPTH:
                    queue.append((full, depth + 1))
            if found:
                break
    _CONTENT_DIR_CACHE[root] = found
    return found

def find_relative_dir(root: str, rel_parts: list) -> str:
    if not root:
        return ""
    cache_key = (root, tuple(rel_parts))
    if cache_key in _RELATIVE_DIR_CACHE:
        return _RELATIVE_DIR_CACHE[cache_key]
    result = ""
    content_dir = find_content_dir(root)
    if content_dir:
        candidate = os.path.join(content_dir, "Pioneer", *rel_parts)
        if os.path.isdir(candidate):
            result = candidate
    if not result and os.path.isdir(root):
        MAX_DEPTH = 8
        MAX_VISITED = 30000
        def _search_for_suffix(suffix_parts):
            if not suffix_parts:
                return ""
            target_leaf_norm = normalize_folder_name(suffix_parts[-1])
            visited = 0
            queue = [(root, 0)]
            while queue:
                current, depth = queue.pop(0)
                visited += 1
                if visited > MAX_VISITED:
                    break
                try:
                    entries = os.listdir(current)
                except OSError:
                    continue
                for entry in entries:
                    full = os.path.join(current, entry)
                    if not os.path.isdir(full):
                        continue
                    if normalize_folder_name(entry) == target_leaf_norm:
                        ok = True
                        check = full
                        for part in reversed(suffix_parts[:-1]):
                            check = os.path.dirname(check)
                            if normalize_folder_name(os.path.basename(check)) != normalize_folder_name(part):
                                ok = False
                                break
                        if ok:
                            return full
                    if depth < MAX_DEPTH:
                        queue.append((full, depth + 1))
            return ""
        for start in range(len(rel_parts)):
            suffix = rel_parts[start:]
            hit = _search_for_suffix(suffix)
            if hit:
                result = hit
                break
    _RELATIVE_DIR_CACHE[cache_key] = result
    return result

def get_decal_folder() -> str:
    invalidate_dir_caches_if_root_changed()
    root = get_pioneer_root()
    if not root:
        return ""
    return find_relative_dir(
        root,
        ["MaterialLibrary", "Character", "LayeredMaterials", "Textures", "Decals"],
    ) or find_relative_dir(
        root,
        ["MaterialLibrary", "Textures", "Decals"],
    )

def get_weapon_shared_folder() -> str:
    root = get_pioneer_root()
    if not root:
        return ""
    return find_relative_dir(root, ["Items", "Firearms", "Shared", "Materials"])

# ---------------------------------------------------------------------------
# Node group management
# ---------------------------------------------------------------------------

def ensure_arc_texturer_node_group() -> bool:
    if _NODE_GROUP in bpy.data.node_groups:
        return True
    if not os.path.isfile(_BLEND_PATH):
        print(f"Arc Raiders PSK Importer: Cannot find bundled blend at '{_BLEND_PATH}'")
        return False
    with bpy.data.libraries.load(_BLEND_PATH, link=False) as (data_from, data_to):
        if _NODE_GROUP not in data_from.node_groups:
            print(f"Arc Raiders PSK Importer: Node group '{_NODE_GROUP}' not in blend file")
            return False
        data_to.node_groups = [_NODE_GROUP]
    return _NODE_GROUP in bpy.data.node_groups

def ensure_colormask_node_group() -> bool:
    if _COLORMASK_GROUP in bpy.data.node_groups:
        return True
    if not os.path.isfile(_BLEND_PATH):
        print(f"Arc Raiders PSK Importer: Cannot find bundled blend at '{_BLEND_PATH}'")
        return False
    with bpy.data.libraries.load(_BLEND_PATH, link=False) as (data_from, data_to):
        if _COLORMASK_GROUP not in data_from.node_groups:
            print(f"Arc Raiders PSK Importer: Node group '{_COLORMASK_GROUP}' not in blend file")
            return False
        data_to.node_groups = [_COLORMASK_GROUP]
    return _COLORMASK_GROUP in bpy.data.node_groups

def ensure_node_group(name: str) -> bool:
    if name in bpy.data.node_groups:
        return True
    if not os.path.isfile(_BLEND_PATH):
        return False
    with bpy.data.libraries.load(_BLEND_PATH, link=False) as (data_from, data_to):
        if name not in data_from.node_groups:
            print(f"Arc Raiders PSK Importer: Node group '{name}' not found in blend file")
            return False
        data_to.node_groups = [name]
    return name in bpy.data.node_groups

def ensure_material(name: str) -> bool:
    if name in bpy.data.materials:
        return True
    if not os.path.isfile(_BLEND_PATH):
        return False
    with bpy.data.libraries.load(_BLEND_PATH, link=False) as (data_from, data_to):
        if name not in data_from.materials:
            print(f"Arc Raiders PSK Importer: Material '{name}' not found in blend file")
            return False
        data_to.materials = [name]
    return name in bpy.data.materials

def ensure_psk_addon():
    """Install the bundled io_scene_psk_psa addon if not available."""
    try:
        bpy.ops.psk.import_file.get_rna_type()
        return
    except AttributeError:
        pass
    import zipfile
    psk_zip = os.path.join(_ADDON_DIR, "add-on-io-scene-psk-psa-v9_1_2.zip")
    if not os.path.isfile(psk_zip):
        print("Arc Raiders PSK Importer: Bundled PSK addon zip not found.")
        return
    print("Arc Raiders PSK Importer: Installing bundled io_scene_psk_psa addon...")
    try:
        bpy.ops.preferences.addon_install(filepath=psk_zip, overwrite=True)
        with zipfile.ZipFile(psk_zip) as zf:
            names = zf.namelist()
        module_name = next(
            (n.split("/")[0] for n in names if n.endswith("/__init__.py")),
            None
        )
        if module_name:
            bpy.ops.preferences.addon_enable(module=module_name)
            bpy.ops.wm.save_userpref()
            print(f"Arc Raiders PSK Importer: io_scene_psk_psa enabled as '{module_name}'.")
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to install PSK addon: {e}")