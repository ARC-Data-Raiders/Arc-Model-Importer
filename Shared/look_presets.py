"""Look presets: an asset's control values saved as JSON in the addon's user config folder.

File format (version 1)::

    {"version": 1, "name": "Desert", "materials": {"MI_Body": {"Wear": 0.4, "Tint": [1, 0, 0, 1]}}}

Material names are stored without Blender's ``.001`` suffix so a preset loads onto another copy
of the same asset. Pure helpers (no bpy at import) so tests can run them anywhere.
"""
import json
import os
import re

PRESET_VERSION = 1
PRESET_FOLDER = "arc_look_presets"
_SUFFIX = re.compile(r"\.\d{3,}$")
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def base_name(name):
    """Material name without Blender's numeric copy suffix."""
    return _SUFFIX.sub("", name or "")


def safe_name(name):
    """File-safe preset name; empty becomes "Look"."""
    clean = _BAD.sub("_", (name or "").strip()).strip(" .")
    return clean[:80] or "Look"


def preset_dir(create=True):
    """Addon user config folder for presets (follows BLENDER_USER_CONFIG, so tests stay private)."""
    import bpy
    return bpy.utils.user_resource("CONFIG", path=PRESET_FOLDER, create=create)


def preset_path(folder, name):
    return os.path.join(folder, safe_name(name) + ".json")


def build(name, material_values):
    """Preset dict from {material name: {control id: value}}; colours become lists."""
    mats = {}
    for mat_name, values in material_values.items():
        row = {}
        for cid, v in values.items():
            row[cid] = [float(x) for x in v] if isinstance(v, (list, tuple)) else float(v)
        if row:
            mats.setdefault(base_name(mat_name), row)
    return {"version": PRESET_VERSION, "name": name, "materials": mats}


def save(path, data):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)
    return path


def load(path):
    """Preset dict, or raise ValueError with a plain message."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError("Could not read the preset file") from e
    if not isinstance(data, dict) or not isinstance(data.get("materials"), dict):
        raise ValueError("This file is not a look preset")
    return data


def list_presets(folder):
    """[(name, path)] sorted by name; empty when the folder is missing."""
    try:
        files = os.listdir(folder)
    except OSError:
        return []
    out = [(f[:-5], os.path.join(folder, f)) for f in files if f.lower().endswith(".json")]
    return sorted(out, key=lambda t: t[0].lower())


def values_for(data, mat_name):
    """{control id: value} stored for *mat_name* (matched without the copy suffix)."""
    return dict((data.get("materials") or {}).get(base_name(mat_name)) or {})
