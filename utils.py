"""
Utility functions for the Arc Raiders Importer
"""

import os
import re
import json
import logging
from collections import deque
import bpy
import mathutils
from mathutils import Vector

_ADDON_DIR = os.path.dirname(__file__)
_BLEND_PATH = os.path.join(_ADDON_DIR, "ArcTexturer.blend")
_NODE_GROUP = "Arc Texturer"
_COLORMASK_GROUP = "ColorMask_XYZ"
_DECAL_DATA_GROUP = "Decal Data"
_DEBUG_LOG_PATH = os.path.join(_ADDON_DIR, "arc_raiders_debug.log")
_LOGGER = None


def get_logger() -> logging.Logger:
    """Logger that writes to the addon debug file and stderr (System Console)."""
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER
    log = logging.getLogger("arc_raiders")
    log.setLevel(logging.DEBUG)
    if not log.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
        try:
            fh = logging.FileHandler(_DEBUG_LOG_PATH, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(fmt)
            log.addHandler(fh)
        except OSError:
            pass
        sh = logging.StreamHandler()
        sh.setLevel(logging.DEBUG)
        sh.setFormatter(fmt)
        log.addHandler(sh)
    log.propagate = False
    _LOGGER = log
    return log


def debug_log_path() -> str:
    return _DEBUG_LOG_PATH


# ---------------------------------------------------------------------------
# Folder scanning
# ---------------------------------------------------------------------------

def find_psks_in_folder(folder: str) -> tuple:
    """Scan a folder (and subfolders) for PSK/PSKX files.

    Includes meshes in the selected folder itself (needed for firearms that
    keep SK_/SM_ files at the root). When several LODs share a stem, prefers
    LOD0. Distinct meshes in the same folder are all returned.

    When both a .psk and a .pskx share the exact same stem (case-insensitive),
    the .pskx is silently dropped — a .psk is a skeletal mesh with bones and
    is always preferred over the boneless .pskx static mesh.

    Returns:
        (paths, skipped_pskx) — paths is the deduplicated sorted list;
        skipped_pskx is a list of .pskx basenames that were dropped because
        a matching .psk existed.
    """
    results = []

    def list_psks(path):
        try:
            entries = sorted(os.listdir(path))
        except OSError:
            return []
        return [
            os.path.join(path, f) for f in entries
            if f.lower().endswith(".psk") or f.lower().endswith(".pskx")
        ]

    def pick_preferred(psks):
        groups = {}
        for p in psks:
            stem = os.path.splitext(os.path.basename(p))[0]
            base = re.sub(r'_lod\d+$', '', stem, flags=re.IGNORECASE).lower()
            groups.setdefault(base, []).append(p)
        picked = []
        for paths in groups.values():
            lod0 = [p for p in paths if "lod0" in os.path.basename(p).lower()]
            picked.append(lod0[0] if lod0 else sorted(paths)[0])
        return sorted(picked)

    def scan(path):
        psks = list_psks(path)
        if psks:
            results.extend(pick_preferred(psks))
            return
        try:
            entries = sorted(os.listdir(path))
        except OSError:
            return
        for entry in entries:
            sub = os.path.join(path, entry)
            if os.path.isdir(sub):
                scan(sub)

    if folder and os.path.isdir(folder):
        scan(folder)

    # Dedup: when a .psk and a .pskx share the same body (ignoring SK_/SM_ prefix),
    # drop the .pskx — a .psk is a skeletal mesh with bones and is always preferred.
    def _mesh_body(p):
        """Stem with SK_/SM_ prefix and LOD suffix stripped, lowercased."""
        stem = os.path.splitext(os.path.basename(p))[0]
        stem = re.sub(r'^(SK|SM)_', '', stem, flags=re.IGNORECASE)
        stem = re.sub(r'_lod\d+$', '', stem, flags=re.IGNORECASE)
        return stem.lower()

    by_body = {}
    for p in results:
        by_body.setdefault(_mesh_body(p), []).append(p)

    filtered = []
    skipped_pskx = []
    for paths in by_body.values():
        has_psk = any(p.lower().endswith(".psk") for p in paths)
        pskx_paths = [p for p in paths if p.lower().endswith(".pskx")]
        if has_psk and pskx_paths:
            filtered.extend(p for p in paths if not p.lower().endswith(".pskx"))
            skipped_pskx.extend(os.path.basename(p) for p in pskx_paths)
        else:
            filtered.extend(paths)

    return sorted(filtered), skipped_pskx

def normalize_folder_name(name: str) -> str:
    """Normalize a folder/file name for fuzzy comparison."""
    return re.sub(r'[^a-z0-9]', '', name.lower())

def normalize_part_key(key: str) -> str:
    """Normalize a '<Character>/<Part>' key for fuzzy comparison."""
    return re.sub(r'[^a-z0-9/]', '', key.lower())

# ---------------------------------------------------------------------------
# UE JSON dump helpers
# ---------------------------------------------------------------------------

def ue_export_entries(data):
    """Normalize UE asset JSON into a list of export objects.

    Supports:
      - legacy bare list of export objects
      - legacy single export object
      - newer {Exports: [...], Metadata: ...} wrappers from current dumps
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        exports = data.get("Exports")
        if isinstance(exports, list):
            return exports
        return [data]
    return []

def first_ue_export(data, type_name: str = ""):
    """Return the first export matching Type, or first export when untyped.

    When ``type_name`` is set, never fall back to a different Type (e.g. BodySetup
    under an MI_*.json filename from a corrupt FModel dump). Callers that want any
    export should omit ``type_name`` or call again without it.
    """
    entries = ue_export_entries(data)
    if not entries:
        return {}
    if not type_name:
        return entries[0]
    for entry in entries:
        if entry.get("Type") == type_name:
            return entry
    return {}

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
        try:
            from . import materials as _mats
            _mats.clear_material_session_caches()
        except Exception:
            pass
        try:
            from . import textures as _tex
            _tex.invalidate_clothing_mi_index()
        except Exception:
            pass

def _content_dir_score(content_dir: str) -> int:
    """Prefer PioneerGame Content over Engine/plugin Content under FModel dumps."""
    parent = os.path.basename(os.path.dirname(os.path.normpath(content_dir))).lower()
    score = 0
    if parent in ("pioneergame", "pioneer"):
        score += 100
    if os.path.isdir(os.path.join(content_dir, "Pioneer")):
        score += 50
    if os.path.isdir(os.path.join(content_dir, "Pioneer", "MaterialLibrary")):
        score += 25
    if parent == "engine":
        score -= 100
    if "plugin" in parent or parent == "engine":
        score -= 50
    return score


def find_content_dir(root: str) -> str:
    if not root or not os.path.isdir(root):
        return ""
    if root in _CONTENT_DIR_CACHE:
        return _CONTENT_DIR_CACHE[root]
    found = ""
    base = os.path.basename(os.path.normpath(root)).lower()
    if base == "content":
        found = root
    elif base == "pioneergame":
        # Root is already PioneerGame — Content is a direct child.
        direct_pg = os.path.join(root, "Content")
        if os.path.isdir(direct_pg):
            found = direct_pg
    if not found:
        # FModel output roots often contain both Engine/Content and PioneerGame/Content.
        # Prefer the game package Content so /Game/Pioneer/... ObjectPaths resolve.
        direct = os.path.join(root, "PioneerGame", "Content")
        if os.path.isdir(direct):
            found = direct
        else:
            MAX_DEPTH = 6
            MAX_VISITED = 20000
            visited = 0
            candidates = []
            queue = deque([(root, 0)])
            while queue:
                current, depth = queue.popleft()
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
                        candidates.append(full)
                    if depth < MAX_DEPTH:
                        queue.append((full, depth + 1))
            if candidates:
                found = max(candidates, key=_content_dir_score)
    _CONTENT_DIR_CACHE[root] = found
    return found


def _content_dir_has_mi_jsons(content_dir: str, *, sample_dirs: int = 80) -> bool:
    """Cheap probe: does this Content tree hold exported MI_*.json (not mesh-only)?"""
    if not content_dir or not os.path.isdir(content_dir):
        return False
    pioneer = os.path.join(content_dir, "Pioneer")
    if not os.path.isdir(pioneer):
        return False
    # Prefer MaterialLibrary / Environment which hold most map MIs.
    for rel in (
        ("MaterialLibrary", "Material_Instances"),
        ("Environment",),
        ("Characters",),
    ):
        start = os.path.join(pioneer, *rel)
        if not os.path.isdir(start):
            continue
        seen = 0
        try:
            for walk_root, _dirs, files in os.walk(start):
                for fname in files:
                    if fname.startswith("MI_") and fname.lower().endswith(".json"):
                        return True
                seen += 1
                if seen >= sample_dirs:
                    break
        except OSError:
            pass
    return False


def guess_full_fmodel_content_dirs(seed_content_dir: str = "") -> list[str]:
    """When seed is a MapPlacements mesh tree, find the sibling full FModel Content dump.

    Map + Meshes writes uemodels under ``MapPlacements/{Map}/PioneerGame/Content`` with
    almost no MI/SM JSON. The full dump (MI JSON + SM StaticMaterials) usually lives at
    ``{FModelOutput}/PioneerGame/Content`` next to ``MapPlacements/``.
    """
    out: list[str] = []
    seed = os.path.abspath(seed_content_dir or "")
    if not seed:
        return out

    parts = seed.replace("/", os.sep).split(os.sep)
    for i, part in enumerate(parts):
        if part.lower() != "mapplacements":
            continue
        # Parent of MapPlacements (FModel output root).
        if i == 0:
            break
        if parts[0].endswith(":"):
            parent = parts[0] + os.sep
            if i > 1:
                parent = os.path.join(parent, *parts[1:i])
        else:
            parent = os.path.join(*parts[:i])
        sibling = os.path.join(parent, "PioneerGame", "Content")
        if os.path.isdir(sibling):
            out.append(os.path.normpath(sibling))
        break
    return out


def get_content_dirs(extra_roots: list[str] | None = None) -> list[str]:
    """Ordered Content directories for ObjectPath / MI / SM JSON resolve.

    Prefers Content trees that actually contain MI_*.json (full FModel dump) over
    MapPlacements mesh-only trees that only have .uemodel files.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(path: str, *, prefer_front: bool = False) -> None:
        if not path:
            return
        norm = os.path.normcase(os.path.normpath(path))
        if norm in seen or not os.path.isdir(path):
            return
        seen.add(norm)
        if prefer_front:
            ordered.insert(0, os.path.normpath(path))
        else:
            ordered.append(os.path.normpath(path))

    roots: list[str] = []
    pioneer = get_pioneer_root()
    if pioneer:
        roots.append(pioneer)
    try:
        scene = bpy.context.scene
        mesh_root = bpy.path.abspath(getattr(scene, "arc_placement_mesh_root", "") or "")
        if mesh_root:
            roots.append(mesh_root)
        csv = bpy.path.abspath(getattr(scene, "arc_placement_csv", "") or "")
        if csv:
            roots.append(os.path.dirname(csv))
    except Exception:
        pass
    for er in extra_roots or []:
        if er:
            roots.append(er)

    seed_contents: list[str] = []
    for root in roots:
        cd = find_content_dir(root)
        if cd:
            seed_contents.append(cd)
            has_mi = _content_dir_has_mi_jsons(cd)
            _add(cd, prefer_front=has_mi)

    for seed in seed_contents:
        for guessed in guess_full_fmodel_content_dirs(seed):
            if _content_dir_has_mi_jsons(guessed):
                _add(guessed, prefer_front=True)
            else:
                _add(guessed)

    return ordered


def remap_path_into_content_dirs(file_path: str, content_dirs: list[str] | None = None) -> list[str]:
    """Map a MapPlacements (or any Content-relative) file into alternate Content trees.

    Examples:
      .../MapPlacements/RivenTides_01_P/PioneerGame/Content/Pioneer/Environment/.../SM_X.uemodel
      → .../PioneerGame/Content/Pioneer/Environment/.../SM_X.uemodel

      .../MapPlacements/TheDam_02_P/Game/Pioneer/Environment/.../SM_X.uemodel
      → .../PioneerGame/Content/Pioneer/Environment/.../SM_X.uemodel

    FModel Map + Meshes may write uemodels under either ``PioneerGame/Content/...``
    or a shorter ``Game/...`` mirror. Stage 2 needs the full dump (SM/MI JSON + PNG)
    which usually lives only under the sibling ``PioneerGame/Content`` tree.
    """
    if not file_path:
        return []
    abs_path = os.path.normpath(os.path.abspath(bpy.path.abspath(file_path)))
    rel = ""
    parts = abs_path.replace("/", os.sep).split(os.sep)
    for i, part in enumerate(parts):
        if part.lower() == "content" and i + 1 < len(parts):
            rel = os.sep.join(parts[i + 1 :])
            break
    # Literal ``Game/`` folder (not PioneerGame): same relative payload as Content/.
    if not rel:
        for i, part in enumerate(parts):
            if part.lower() == "game" and i + 1 < len(parts):
                rel = os.sep.join(parts[i + 1 :])
                break
    if not rel:
        return []

    dirs = content_dirs if content_dirs is not None else get_content_dirs()
    out: list[str] = []
    seen: set[str] = set()
    for cd in dirs:
        candidate = os.path.normpath(os.path.join(cd, rel))
        key = os.path.normcase(candidate)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out

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
            queue = deque([(root, 0)])
            while queue:
                current, depth = queue.popleft()
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

def _arc_texturer_has_overlay_sockets(ng) -> bool:
    """True when bundled Arc Texturer exposes internalized Base Overlay inputs."""
    try:
        names = {item.name for item in ng.interface.items_tree if hasattr(item, "name")}
    except Exception:
        return False
    return "Overlay 1" in names and "Decal LayerGate 1" in names and "DN Enable 5" in names


def ensure_arc_texturer_node_group() -> bool:
    """Load Arc Texturer from ArcTexturer.blend; refresh if missing new sockets."""
    existing = bpy.data.node_groups.get(_NODE_GROUP)
    if existing is not None and _arc_texturer_has_overlay_sockets(existing):
        ensure_decal_data_node_group()
        return True
    if not os.path.isfile(_BLEND_PATH):
        print(f"Arc Raiders PSK Importer: Cannot find bundled blend at '{_BLEND_PATH}'")
        return False
    # Stale in-memory group (pre-overlay / broken DN5): replace from blend.
    if existing is not None:
        try:
            existing.name = f"{_NODE_GROUP}_stale"
        except Exception:
            pass
    with bpy.data.libraries.load(_BLEND_PATH, link=False) as (data_from, data_to):
        if _NODE_GROUP not in data_from.node_groups:
            print(f"Arc Raiders PSK Importer: Node group '{_NODE_GROUP}' not in blend file")
            return False
        data_to.node_groups = [_NODE_GROUP]
    loaded = bpy.data.node_groups.get(_NODE_GROUP)
    if loaded is None:
        # Append may have created Arc Texturer.001 when stale still held the name.
        for ng in bpy.data.node_groups:
            if ng.name.startswith(f"{_NODE_GROUP}.") and _arc_texturer_has_overlay_sockets(ng):
                ng.name = _NODE_GROUP
                loaded = ng
                break
    stale = bpy.data.node_groups.get(f"{_NODE_GROUP}_stale")
    if stale is not None and stale.users == 0:
        bpy.data.node_groups.remove(stale)
    ensure_decal_data_node_group()
    return _NODE_GROUP in bpy.data.node_groups and _arc_texturer_has_overlay_sockets(
        bpy.data.node_groups[_NODE_GROUP]
    )


def ensure_decal_data_node_group() -> bool:
    """Load the Decal Data helper group (RG→Z normal reconstruct + rough/metal)."""
    if _DECAL_DATA_GROUP in bpy.data.node_groups:
        return True
    if not os.path.isfile(_BLEND_PATH):
        return False
    with bpy.data.libraries.load(_BLEND_PATH, link=False) as (data_from, data_to):
        if _DECAL_DATA_GROUP not in data_from.node_groups:
            print(f"Arc Raiders PSK Importer: Node group '{_DECAL_DATA_GROUP}' not in blend file")
            return False
        data_to.node_groups = [_DECAL_DATA_GROUP]
    return _DECAL_DATA_GROUP in bpy.data.node_groups


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

def normalize_ue_uv_layer_names(mesh) -> int:
    """Rename UV layers to ``UV0``, ``UV1``, … matching UE TexCoord indices.

    UEFormat already names layers ``UV0``/``UV1``. PSK (``io_scene_psk_psa``) uses
    ``UVMap`` for TexCoord0 and ``EXTRAUV0`` for TexCoord1. GraphicAtlas posters
    set ``Use UV1`` and bind a UV Map node to ``UV1`` — without this rename, PSK
    imports sample the wrong (or missing) set and textures look misplaced.

    Returns the number of layers renamed (0 if already normalized / empty).
    """
    if mesh is None:
        return 0
    uv_layers = getattr(mesh, "uv_layers", None)
    if not uv_layers or len(uv_layers) == 0:
        return 0
    current = [uv.name for uv in uv_layers]
    expected = [f"UV{i}" for i in range(len(current))]
    if current == expected:
        return 0
    # Two-pass rename avoids collisions (e.g. EXTRAUV0 → UV1 while UV1 exists).
    for i, uv in enumerate(list(uv_layers)):
        uv.name = f"__arc_uv_tmp_{i}"
    for i, uv in enumerate(list(uv_layers)):
        uv.name = f"UV{i}"
    return len(expected)


def normalize_object_ue_uv_layers(obj) -> int:
    """Normalize UV layer names on a mesh object. See :func:`normalize_ue_uv_layer_names`."""
    if obj is None or getattr(obj, "type", None) != "MESH":
        return 0
    return normalize_ue_uv_layer_names(getattr(obj, "data", None))


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
