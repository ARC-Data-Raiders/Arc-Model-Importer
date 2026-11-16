"""Lazy plan identity, root selection, and content validation."""
import hashlib
import json
from pathlib import Path

RUNTIME = "parity-1"
last_reason = ""
_INDEX = {}
_HASHES = {}
_LOADED = {}


class Plan(dict):
    def __init__(self, data, root, canonical, entry):
        super().__init__(data)
        self.root = Path(root)
        self.canonical = canonical
        self.entry = entry
        self.stamp = entry["plan_sha256"] + ":" + entry["dag_sha256"]
        self.directory = self.root / entry["dir"]

    def dag(self):
        return json.loads((self.directory / self["dag"]).read_text(encoding="utf-8"))


def _addon():
    from .... import pak_extract; from .....Outfit_Accessories import pak_catalog
    return pak_extract, pak_catalog


def installed_build():
    try:
        import bpy
    except ImportError:
        return ""
    return _addon()[1].installed_build(getattr(bpy.context.scene, "arc_pak_dir", ""))


def root():
    global last_reason
    import bpy
    selected = getattr(bpy.context.scene, "dr_parity_root", "")
    if selected:
        return Path(bpy.path.abspath(selected))
    build = installed_build()
    if not build:
        last_reason = "build_unknown"
        return None
    return Path(_addon()[0].cache_path()) / "shader_parity" / build


def index(root_path):
    global last_reason
    if root_path is None:
        return None
    path = Path(root_path) / "index.json"
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if key not in _INDEX:
            _INDEX[key] = json.loads(path.read_text(encoding="utf-8"))
        value = _INDEX[key]
        if value["runtime"] != RUNTIME:
            last_reason = "runtime_mismatch"
            return None
        build = installed_build()
        if build and value["build_id"] != build:
            last_reason = "build_mismatch"
            return None
        last_reason = ""
        return value
    except (OSError, ValueError, KeyError) as error:
        last_reason = "index:" + str(error)
        return None


def _hash(path):
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _HASHES:
        _HASHES[key] = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    return _HASHES[key]


def load(root_path, canonical):
    global last_reason
    idx = index(root_path)
    if idx is None:
        return None
    entry = idx["plans"].get(canonical)
    if entry is None:
        last_reason = "plan_missing"
        return None
    try:
        directory = Path(root_path) / entry["dir"]
        for name, expected in (("plan.json", entry["plan_sha256"]), ("material.dag.json", entry["dag_sha256"])):
            if _hash(directory / name) != expected:
                last_reason = name + ":hash_mismatch"
                return None
        key = (str(root_path), canonical, entry["plan_sha256"], entry["dag_sha256"])
        if key not in _LOADED:
            _LOADED[key] = Plan(json.loads((directory / "plan.json").read_text(encoding="utf-8")), root_path, canonical, entry)
        last_reason = ""
        return _LOADED[key]
    except (OSError, ValueError, KeyError) as error:
        last_reason = "plan:" + str(error)
        return None


def identity(mi_path):
    global last_reason
    text = str(mi_path)
    sidecar = Path(text if text.endswith(".arc-material.json") else text[:-5] + ".arc-material.json" if text.endswith(".json") else text + ".arc-material.json")
    if sidecar.is_file():
        try:
            value = json.loads(sidecar.read_text(encoding="utf-8"))["Exports"][0]["Package"]
            return _addon()[1].canonical_path(value)
        except (OSError, ValueError, KeyError, IndexError) as error:
            last_reason = "identity:" + str(error)
            return None
    result = _addon()[0].saved_material_package(text)
    if not result:
        last_reason = "identity_missing"
    return result or None


def find(mi_path):
    canonical = identity(mi_path)
    if canonical is None:
        return None
    return load(root(), canonical)


def for_material(mat):
    stamp = mat.get("dr_parity_plan_sha256", "")
    for plan in _LOADED.values():
        if plan.stamp == stamp:
            return plan
    path = mat.get("arc_mi_path")
    return find(path) if path else None


def defaults(root_path=None):
    selected = root() if root_path is None else Path(root_path)
    if selected is None:
        return None
    try:
        return json.loads((selected / "mpc_defaults.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
