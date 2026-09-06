"""
Material setup — common domain (split from materials.py monolith).
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



# ---------------------------------------------------------------------------
# Session caches (map Stage 2 / shared MI reuse)
# ---------------------------------------------------------------------------
# Identical MI defs are rebuilt thousands of times on Buried City-scale maps when
# each unique SRC mesh creates its own Material datablock. Cache by absolute MI
# JSON path so shaders + texture loads happen once per material definition.
_FLAT_MI_CACHE: dict[str, dict] = {}

_MI_JSON_PATH_CACHE: dict[tuple, str] = {}

_SK_SLOTS_CACHE: dict[str, list] = {}

_SHARED_MI_MATERIALS: dict[str, object] = {}

_IMAGE_BY_PATH: dict[str, object] = {}

# Clothing ArcTexturer mats: (normalized_mi_json_path, texture_fingerprint) → Material
_CLOTHING_MATERIAL_CACHE: dict[tuple, object] = {}

_CLOTHING_CACHE_STATS: dict[str, int] = {"hits": 0, "misses": 0}

_WIDTH_RATIO_BY_PATH: dict[str, float] = {}

_MI_STEM_IDENTITY_INDEX: dict[str, list[str]] | None = None

_MI_STEM_IDENTITY_INDEX_ROOTS: tuple[str, ...] = ()

# When True (batch colourway import), skip per-material palette report cache writes.
_BATCH_MATERIAL_MODE: bool = False

# Known engine stub decals — never BFS Content for these.
_NULL_DECAL_MARKERS = frozenset({
    "t_decal_null",
    "t_decal_null_m",
    "t_decal_null_c",
    "t_decal_null_n",
    "t_decal_null_d",
})


# ---------------------------------------------------------------------------
# Resolve contexts — assignment/discovery scopes (node wiring stays shared)
# ---------------------------------------------------------------------------
# Map/prop Stage 2 must NEVER pull Characters/Heroes/outfit cosmetics via
# basename walks or mismatched SM JSON bodies (FModel dump races).
CTX_MAP = "map"

CTX_OUTFIT = "outfit"

CTX_WEAPON = "weapon"

CTX_EFFECT = "effect"

CTX_ENEMY = "enemy"

CTX_ANY = "any"


# Path segments forbidden when resolving MIs/textures for map/environment props.
_MAP_FORBIDDEN_SEGMENTS = (
    "/characters/",
    "/heroes/",
    "/outfits/",
    "/scrappy/",
    "/backpacks/charms/",
    "/heads/",
)

# Soft allow markers for map (ObjectPath / MaterialLibrary / Environment).
_MAP_ALLOWED_SEGMENTS = (
    "/environment/",
    "/materiallibrary/",
    "/lighting/",
    "/effects/",
    "/architecture/",
    "/foliage/",
    "/landscape/",
    "/blueprints/",
    "/toolkit/",
)



def _norm_game_path(path: str) -> str:
    return (path or "").replace("\\", "/").lower()



def path_allowed_for_context(path: str, context: str = CTX_ANY) -> bool:
    """True when an on-disk or ObjectPath location may be used for ``context``.

    Shared node-wiring helpers ignore this; only MI/texture *discovery* gates.
    """
    if not path or context in ("", CTX_ANY):
        return True
    p = _norm_game_path(path)
    if context == CTX_MAP:
        if any(seg in p for seg in _MAP_FORBIDDEN_SEGMENTS):
            return False
        # Allow MaterialLibrary + Environment + remapped mesh folders.
        if any(seg in p for seg in _MAP_ALLOWED_SEGMENTS):
            return True
        # Sibling files next to an Environment mesh (no mid-path marker yet)
        # still pass when under PioneerGame/Content and not Characters.
        if "/pioneergame/content/" in p or "/content/pioneer/" in p:
            return not any(seg in p for seg in _MAP_FORBIDDEN_SEGMENTS)
        # /Game/ ObjectPaths without Content prefix
        if p.startswith("/game/"):
            return not any(seg in p for seg in _MAP_FORBIDDEN_SEGMENTS)
        return True
    if context == CTX_OUTFIT:
        return any(
            seg in p
            for seg in (
                "/characters/", "/heroes/", "/outfits/", "/scrappy/",
                "/heads/", "/backpacks/", "/bodycosmetics/",
                "/items/characters/",
            )
        )
    if context == CTX_WEAPON:
        return any(
            seg in p
            for seg in (
                "/weapons/", "/weapon/", "/gun/", "/firearm/", "/firearms/",
                "/items/firearms/", "/items/melee/", "/items/launchers/",
                "/materiallibrary/",
            )
        )
    if context == CTX_EFFECT:
        return any(
            seg in p
            for seg in ("/effects/", "/decals/", "/materiallibrary/", "/toolkit/", "/environment/")
        )
    if context == CTX_ENEMY:
        return "/enemies/" in p or "/materiallibrary/" in p
    return True



def _iter_material_image_filepaths(mat) -> list[str]:
    """Collect Image Texture node filepaths (absolute when Blender can resolve)."""
    out: list[str] = []
    if mat is None or not getattr(mat, "use_nodes", False) or not mat.node_tree:
        return out
    for node in mat.node_tree.nodes:
        if getattr(node, "type", "") != "TEX_IMAGE":
            continue
        img = getattr(node, "image", None)
        if img is None:
            continue
        fp = ""
        try:
            fp = img.filepath_from_user() or ""
        except Exception:
            fp = ""
        if not fp:
            fp = getattr(img, "filepath", "") or ""
        if fp:
            out.append(fp)
    return out



def material_has_map_forbidden_content(mat) -> bool:
    """True when a map slot still points at Characters/Heroes/outfit cosmetics.

    Checks ``arc_mi_path`` *and* TEX_IMAGE filepaths — Force All used to only
    unset custom props (or skip textured stamps), leaving belt/helmet images.
    """
    if mat is None:
        return False
    mi = str(mat.get("arc_mi_path") or "").strip()
    if mi and not path_allowed_for_context(mi, CTX_MAP):
        return True
    for fp in _iter_material_image_filepaths(mat):
        if fp and not path_allowed_for_context(fp, CTX_MAP):
            return True
    return False



def _new_map_cleared_placeholder(hint: str = "") -> object:
    """Fresh material stub that will not resolve as an MI_* cosmetic stem."""
    base = "ARC_MapCleared"
    stem = (hint or "").strip()
    if stem and not stem.lower().startswith("mi_"):
        base = f"ARC_MapCleared_{stem[:48]}"
    mat = bpy.data.materials.new(name=base)
    mat.use_nodes = True
    try:
        mat["arc_map_cleared"] = 1
    except Exception:
        pass
    return mat



def wipe_out_of_context_map_slot(obj, slot) -> bool:
    """Replace a cosmetic/out-of-context map slot with a clean unique stub.

    Always assigns a *new* datablock (never edits a shared MI in place), so
    other objects keeping the old cosmetic mat are unaffected.
    """
    if obj is None or slot is None:
        return False
    old = slot.material
    if old is None or not material_has_map_forbidden_content(old):
        return False
    try:
        utils.get_logger().warning(
            "wiping out-of-context map material '%s' on '%s' (mi=%s)",
            old.name,
            getattr(obj, "name", "?"),
            str(old.get("arc_mi_path") or ""),
        )
    except Exception:
        pass
    # Detach first so a 1-user cosmetic can be orphaned without mutating it.
    stub = _new_map_cleared_placeholder()
    try:
        slot.material = stub
    except Exception:
        return False
    return True



def clear_out_of_context_map_materials(obj) -> int:
    """Wipe every map-slot material that still references outfit/character paths."""
    if not obj or getattr(obj, "type", "") != "MESH":
        return 0
    n = 0
    for slot in obj.material_slots or []:
        if wipe_out_of_context_map_slot(obj, slot):
            n += 1
    return n



def context_from_model_type(model_type: str = "", psk_path: str = "") -> str:
    """Map ``arc_model_type`` / path domain to a resolve context.

    Folder path domain (outfit / environment / weapon / enemy) wins when present
    so stamps cannot pull Characters MIs into map props or vice versa.
    """
    try:
        from .. import asset_domain as _ad
        domain = _ad.classify_asset_domain(psk_path) if psk_path else _ad.DOMAIN_UNKNOWN
        if domain != _ad.DOMAIN_UNKNOWN:
            return _ad.domain_resolve_context(psk_path)
    except Exception:
        pass
    mt = (model_type or "").strip().lower()
    if mt == "map":
        return CTX_MAP
    if mt in ("clothing", "visor", "face", "body", "hair", "misc"):
        return CTX_OUTFIT
    if mt == "enemy":
        return CTX_ENEMY
    if mt == "weapon":
        return CTX_WEAPON
    pl = _norm_game_path(psk_path)
    if "/environment/" in pl or "/mapplacements/" in pl:
        return CTX_MAP
    if "/enemies/" in pl:
        return CTX_ENEMY
    if any(s in pl for s in ("/firearms/", "/weapons/", "/gun/")):
        return CTX_WEAPON
    if "/characters/" in pl or "/heroes/" in pl:
        return CTX_OUTFIT
    return CTX_ANY



def set_batch_material_mode(enabled: bool) -> None:
    """Toggle batch import behaviour (skip palette report spam, etc.)."""
    global _BATCH_MATERIAL_MODE
    _BATCH_MATERIAL_MODE = bool(enabled)



def clear_material_session_caches():
    """Drop in-memory caches (e.g. after Pioneer root change). Keeps Blender data."""
    global _MI_STEM_IDENTITY_INDEX, _MI_STEM_IDENTITY_INDEX_ROOTS
    _FLAT_MI_CACHE.clear()
    _MI_JSON_PATH_CACHE.clear()
    _SK_SLOTS_CACHE.clear()
    _SHARED_MI_MATERIALS.clear()
    _IMAGE_BY_PATH.clear()
    _CLOTHING_MATERIAL_CACHE.clear()
    _CLOTHING_CACHE_STATS["hits"] = 0
    _CLOTHING_CACHE_STATS["misses"] = 0
    _WIDTH_RATIO_BY_PATH.clear()
    _MI_STEM_IDENTITY_INDEX = None
    _MI_STEM_IDENTITY_INDEX_ROOTS = ()
    try:
        textures.clear_clothing_path_caches()
    except Exception:
        pass



def warm_shared_mi_material_cache():
    """Index already-built materials stamped with arc_mi_path (Stage 2 re-runs)."""
    for mat in bpy.data.materials:
        key = str(mat.get("arc_mi_path", "") or "").strip()
        if not key:
            continue
        if key not in _SHARED_MI_MATERIALS:
            _SHARED_MI_MATERIALS[key] = mat



def _norm_path_key(path: str) -> str:
    if not path:
        return ""
    return os.path.normcase(os.path.normpath(path))



def _load_image_cached(fpath: str):
    """Load an image once per path; faster than repeated check_existing scans."""
    if not fpath:
        return None
    key = _norm_path_key(fpath)
    img = _IMAGE_BY_PATH.get(key)
    if img is not None:
        try:
            _ = img.name
            return img
        except ReferenceError:
            _IMAGE_BY_PATH.pop(key, None)
    img = bpy.data.images.load(fpath, check_existing=True)
    _IMAGE_BY_PATH[key] = img
    return img



def _force_image_ready(img) -> None:
    """Force Blender to decode pixels now (not lazily during first shader eval)."""
    if img is None:
        return
    try:
        w, h = int(img.size[0]), int(img.size[1])
        if w > 0 and h > 0:
            # Reading a pixel forces full decode into memory.
            _ = img.pixels[0]
    except Exception:
        try:
            img.reload()
            _ = img.size[0]
        except Exception:
            pass



def prefetch_images(paths, *, force_decode: bool = True) -> int:
    """Main-thread warm of ``_IMAGE_BY_PATH``. Returns number of paths loaded.

    When ``force_decode`` is True (default), touch pixels so cold PNG decode
    happens here — not mid ``apply_materials`` on a random part.
    """
    n = 0
    for p in paths or ():
        if not p:
            continue
        try:
            img = _load_image_cached(p)
            if img is None:
                continue
            if force_decode:
                _force_image_ready(img)
            n += 1
        except Exception:
            pass
    return n



# ---------------------------------------------------------------------------
# Outfit colour helpers (ColorMask_XYZ / BaseColorOverlay)
# ---------------------------------------------------------------------------

# Node layout: each node keeps this empty margin (Blender editor units ≈ px).
# Overlap tests use these expanded boxes; nudge repeats until clear.
NODE_PAD_TOP = 200.0
NODE_PAD_X = 50.0
NODE_PAD_BOTTOM = 50.0

# Legacy names — total gap between bodies is pad_x*2 horizontally.
_NODE_PAD_X = NODE_PAD_X
_NODE_PAD_Y = NODE_PAD_TOP

_NODE_FALLBACK_W = 240.0
_NODE_FALLBACK_H = 180.0



def _node_editor_size(node) -> tuple:
    """Return (width, height) for layout; prefer live dimensions, else fallbacks."""
    try:
        dims = getattr(node, "dimensions", None)
        if dims is not None and float(dims[0]) > 1.0 and float(dims[1]) > 1.0:
            return float(dims[0]), float(dims[1])
    except Exception:
        pass
    w = float(getattr(node, "width", 0) or 0) or _NODE_FALLBACK_W
    if getattr(node, "hide", False):
        return w, 36.0
    return w, _NODE_FALLBACK_H



def _node_world_loc(n) -> tuple[float, float]:
    x, y = float(n.location.x), float(n.location.y)
    p = getattr(n, "parent", None)
    while p is not None:
        x += float(p.location.x)
        y += float(p.location.y)
        p = getattr(p, "parent", None)
    return x, y


def _set_node_world_loc(n, wx: float, wy: float) -> None:
    px = py = 0.0
    p = getattr(n, "parent", None)
    while p is not None:
        px += float(p.location.x)
        py += float(p.location.y)
        p = getattr(p, "parent", None)
    n.location = (wx - px, wy - py)


def resolve_node_overlaps(
    nodes,
    *,
    pad_top: float = NODE_PAD_TOP,
    pad_x: float = NODE_PAD_X,
    pad_bottom: float = NODE_PAD_BOTTOM,
) -> int:
    """Nudge nodes until padded AABBs do not overlap. Keeps frame parents.

    Each node owns 200px above, 50px left/right, 50px below. Collisions move
    the later node (left-to-right, then top-to-bottom) down or right by those
    margins, then the pass repeats until clear.
    """
    items = []
    obstacles = []
    for node in nodes:
        if getattr(node, "bl_idname", "") == "NodeFrame" or getattr(node, "type", "") == "FRAME":
            continue
        if hasattr(node, "hide_preview"):
            try:
                node.hide_preview = True
            except Exception:
                pass
        w, h = _node_editor_size(node)
        if getattr(node, "bl_idname", "") == "ShaderNodeGroup":
            w = max(w, 320.0)
            h = max(h, 240.0)
        wx, wy = _node_world_loc(node)
        box = [node, wx, wy, w, h]
        try:
            locked = bool(node.get("arc_layout_lock"))
        except Exception:
            locked = False
        if locked:
            obstacles.append(box)
        else:
            items.append(box)
    items.sort(key=lambda t: (t[1], -t[2], getattr(t[0], "name", "")))
    moved = 0
    guard = 0
    while guard < 250:
        guard += 1
        changed = False
        for i in range(len(items)):
            _node_i, xi, yi, wi, hi = items[i]
            li, ti, ri, bi = xi - pad_x, yi + pad_top, xi + wi + pad_x, yi - hi - pad_bottom
            for other in list(obstacles) + items[:i]:
                _nj, xj, yj, wj, hj = other
                lj, tj, rj, bj = xj - pad_x, yj + pad_top, xj + wj + pad_x, yj - hj - pad_bottom
                if li >= rj or ri <= lj or bi >= tj or ti <= bj:
                    continue
                if abs(xi - xj) < max(wj, wi) * 0.55:
                    yi = yj - hj - pad_bottom - pad_top
                else:
                    xi = xj + wj + pad_x + pad_x
                items[i][1], items[i][2] = xi, yi
                li, ti, ri, bi = xi - pad_x, yi + pad_top, xi + wi + pad_x, yi - hi - pad_bottom
                changed = True
                moved += 1
        if not changed:
            break
    for node, wx, wy, _w, _h in items:
        ox, oy = _node_world_loc(node)
        if abs(ox - wx) > 0.5 or abs(oy - wy) > 0.5:
            _set_node_world_loc(node, wx, wy)
    return moved


def apply_node_graph_padding(nodes, pad_x: float = NODE_PAD_X, pad_y: float = NODE_PAD_TOP):
    """Core rule: shader nodes never overlap. ``pad_y`` is the top margin."""
    resolve_node_overlaps(nodes, pad_top=pad_y, pad_x=pad_x, pad_bottom=NODE_PAD_BOTTOM)


_BASE_COLOR_OVERLAY_SLOT_LOCS = {
    1: (-97.06, -292.69),
    2: (-85.53, -541.69),
    3: (-80.65, -794.9),
    4: (-85.22, -1043.42),
    5: (-79.4, -1290.58),
    6: (-79.4, -1540.05),
    7: (-79.4, -1789.52),
    8: (-79.4, -2039.0),
    9: (-79.4, -2288.47),
}



def _parent_name_from_object(parent_obj) -> str:
    """Extract Material'/MI' name from an FModel Parent ObjectName dict/string."""
    if not parent_obj:
        return ""
    if isinstance(parent_obj, str):
        raw = parent_obj
    elif isinstance(parent_obj, dict):
        raw = parent_obj.get("ObjectName") or parent_obj.get("ObjectPath") or ""
    else:
        return ""
    # ObjectName shapes: Material'M_Vegetation_01' / MaterialInstanceConstant'MI_X'
    m = re.search(r"'([^']+)'", str(raw))
    if m:
        return m.group(1)
    return str(raw).rsplit("/", 1)[-1].split(".", 1)[0]



def _parent_object_path(parent_obj) -> str:
    """ObjectPath from a Parent soft reference (for Material JSON resolve)."""
    if isinstance(parent_obj, dict):
        return str(parent_obj.get("ObjectPath") or "").strip()
    return ""



# Filename suffix → MI param name when inheriting from parent Material ReferencedTextures.
# Deterministic suffix map only — no fuzzy soft-match.
_PARENT_REF_SUFFIX_PARAMS = (
    ("_bch", "BaseColor"),
    ("_cxa", "BaseColor"),
    ("_cr", "CR"),
    ("_bc", "BaseColor"),
    ("_ca", "CA"),
    ("_ch", "BaseColor"),
    ("_d", "BaseColor"),
    ("_diff", "BaseColor"),
    ("_nao", "NAO"),
    ("_naoh", "NAO"),
    ("_noh", "NOH"),
    ("_ndd", "Normal"),
    ("_nn", "Normal"),
    ("_n", "Normal"),
    ("_nrh", "BaseColor"),  # snow packed NRH used as albedo+rough
    ("_nc", "Overlay"),
    ("_grm", "PM_SpecularMasks"),
    ("_nom", "NOM"),
    ("_nxx", "NXX"),
    ("_nmx", "NMX"),
)



def _param_from_texture_stem(stem: str) -> str:
    """Map ``T_Foo_CR`` → ``CR`` via known suffixes (longest first)."""
    s = (stem or "").lower()
    if not s:
        return ""
    for suf, param in _PARENT_REF_SUFFIX_PARAMS:
        if s.endswith(suf):
            return param
    return ""


# Coarse slot role of the params produced by _PARENT_REF_SUFFIX_PARAMS.
_PARENT_REF_PARAM_ROLE = {
    "BaseColor": "albedo",
    "CR": "albedo",
    "CA": "albedo",
    "NAO": "normal",
    "NOH": "normal",
    "NOM": "normal",
    "NXX": "normal",
    "NMX": "normal",
    "Normal": "normal",
    "Overlay": "overlay",
    "PM_SpecularMasks": "spec",
}

_ROLE_TOKENS = (
    ("albedo", {"cr", "bch", "bc", "ca", "cxa", "ch", "albedo", "basecolor", "diffuse"}),
    ("normal", {"noh", "nao", "naoh", "nom", "nxx", "nmx", "nxm", "ndd", "normal", "normals"}),
    ("overlay", {"overlay"}),
    ("spec", {"grm", "specularmasks"}),
)


def _mi_param_role(name: str) -> str:
    """Coarse role of an MI texture param, e.g. ``NXX/NMX Texture`` → ``normal``."""
    s = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    if not s:
        return ""
    toks = set(s.split())
    # Detail/blend/wear layers are extras, not the slot the base map fills.
    if toks & {"detail", "blend", "wear", "mask", "breakup"}:
        return ""
    if "base" in toks and "color" in toks:
        return "albedo"
    for role, markers in _ROLE_TOKENS:
        if toks & markers:
            return role
    return ""



def _enrich_parent_material_textures(mi: dict, mi_path: str = "", parent_obj_path: str = "") -> None:
    """Fill missing albedo/normal params from parent Material ReferencedTextures.

    Child MIs often only override Overlay/Trim sheet while the parent Material
    authors ``T_*_BCH`` / ``T_*_CR``. Uses Parent ObjectPath only (exact).
    """
    if not mi:
        return
    have = {p for p, _ in (mi.get("textures") or [])}
    # Preset parents reference their own placeholder maps; only fill roles the
    # child leaves empty, or a differently named param shadows the MI override.
    have_roles = {r for r in (_mi_param_role(p) for p in have) if r}

    obj_path = (parent_obj_path or "").strip()
    parent_stem = (mi.get("parent") or "").strip()
    folder = os.path.dirname(mi_path or "")
    parent_json = ""
    if obj_path:
        parent_json = textures.find_asset_from_object_path(obj_path, ".json")
        if not parent_json and obj_path.endswith(".0"):
            parent_json = textures.find_asset_from_object_path(obj_path[:-2], ".json")
    # Sibling beside the child MI (common for prop-local Materials).
    if (not parent_json or not os.path.isfile(parent_json)) and parent_stem and folder:
        cand = os.path.join(folder, parent_stem + ".json")
        if os.path.isfile(cand):
            parent_json = cand
    if not parent_json or not os.path.isfile(parent_json):
        return
    # Avoid re-entry on same file
    if os.path.normcase(os.path.normpath(parent_json)) == os.path.normcase(
        os.path.normpath(mi_path or "")
    ):
        return
    try:
        with open(parent_json, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return
    entry = None
    for e in utils.ue_export_entries(data):
        if not isinstance(e, dict):
            continue
        if e.get("Type") in ("Material", "MaterialInstanceConstant", "MaterialInstance"):
            entry = e
            break
    if not entry:
        return
    # Identity: parent file must match parent stem when Name is present
    ename = (entry.get("Name") or "").split(".", 1)[0]
    if parent_stem and ename and ename.lower() != parent_stem.lower():
        return

    refs = list(entry.get("ReferencedTextures") or [])
    # CachedExpressionData.TextureValues often lists the authored defaults
    ced = entry.get("CachedExpressionData") or {}
    for tv in ced.get("TextureValues") or []:
        if isinstance(tv, dict) and tv.get("AssetPathName"):
            refs.append({"ObjectPath": tv["AssetPathName"]})

    added = 0
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        op = str(ref.get("ObjectPath") or "").strip()
        if not op or op.startswith("/Engine/"):
            continue
        leaf = op.rstrip("/").replace("\\", "/").split("/")[-1]
        stem = leaf.split(".", 1)[0]
        param = _param_from_texture_stem(stem)
        if not param or param in have:
            continue
        role = _PARENT_REF_PARAM_ROLE.get(param, "")
        if role and role in have_roles:
            continue
        clean = op.rsplit(".", 1)[0] if "." in leaf else op
        # Avoid duplicate ObjectPaths under different params
        if any(p == clean for _, p in (mi.get("textures") or [])):
            continue
        mi.setdefault("textures", []).append((param, clean))
        have.add(param)
        if role:
            have_roles.add(role)
        added += 1
    if added:
        try:
            utils.get_logger().info(
                "Parent Material refs %s ← %s (+%d tex)",
                os.path.splitext(os.path.basename(mi_path or ""))[0],
                os.path.basename(parent_json),
                added,
            )
        except Exception:
            pass



# BrokenGlassSDF parent MaterialLibrary dump is often corrupt; PNGs still exist.
_BROKEN_GLASS_DEFAULT_TEX = (
    ("T_BrokenGlassSDF_NDD", "/Game/Pioneer/MaterialLibrary/Textures/Glass/BrokenGlass_SDF/T_BrokenGlassSDF_NDD"),
    ("T_BrokenGlassOverlay_01_NC", "/Game/Pioneer/MaterialLibrary/Textures/Glass/BrokenGlass_SDF/T_BrokenGlassOverlay_01_NC"),
    ("TA_BrokenGlassShards_01", "/Game/Pioneer/MaterialLibrary/Textures/Glass/BrokenGlass_SDF/TA_BrokenGlassShards_01_0"),
)



def _enrich_broken_glass_default_textures(mi: dict, mi_stem: str = "") -> None:
    """Inject known BrokenGlass SDF texture ObjectPaths when the MI authored none."""
    if not mi:
        return
    stem_l = (mi_stem or mi.get("parent") or "").lower()
    if "brokenglass" not in stem_l.replace("_", ""):
        return
    if mi.get("textures"):
        return
    for param, path in _BROKEN_GLASS_DEFAULT_TEX:
        mi.setdefault("textures", []).append((param, path))



def _apply_bpo_flags(result: dict, bpo: dict):
    """Copy BlendMode / TwoSided / OpacityMaskClip / shading from BasePropertyOverrides."""
    if not bpo:
        return
    if result.get("blend_mode") is None and bpo.get("BlendMode") is not None:
        result["blend_mode"] = bpo.get("BlendMode")
    if bpo.get("OpacityMaskClipValue") is not None:
        try:
            result["opacity_clip"] = float(bpo["OpacityMaskClipValue"])
        except (TypeError, ValueError):
            pass
    if "TwoSided" in bpo:
        result["two_sided"] = bool(bpo.get("TwoSided"))
    if bpo.get("ShadingModel") is not None:
        result["shading_model"] = str(bpo.get("ShadingModel") or "")
    if "DitheredLODTransition" in bpo:
        result["dithered_lod"] = bool(bpo.get("DitheredLODTransition"))



def _parse_flat_mi_json(json_path: str) -> dict:
    result = {
        'textures': [], 'colours': [], 'switches': {}, 'scalars': {},
        'opacity_clip': 0.3333, 'blend_mode': None, 'is_null': False,
        'parent': '', 'two_sided': False, 'shading_model': '',
        'dithered_lod': False, 'is_translucent': False,
    }
    if not json_path or not os.path.isfile(json_path):
        return result
    cache_key = _norm_path_key(json_path)
    cached = _FLAT_MI_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        with open(json_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if isinstance(data, list):
            data = data[0]
        for param, path in data.get('Textures', {}).items():
            obj_path = path.rsplit('.', 1)[0] if '.' in path.split('/')[-1] else path
            result['textures'].append((param, obj_path))
        params = data.get('Parameters', {}) or {}
        for param, val in params.get('Colors', {}).items():
            result['colours'].append((param, (
                float(val.get('R', 1.0)),
                float(val.get('G', 1.0)),
                float(val.get('B', 1.0)),
                float(val.get('A', 1.0)),
            )))
        result['switches'] = params.get('Switches', {}) or {}
        result['scalars'] = params.get('Scalars', {}) or {}
        result['is_null'] = bool(params.get('IsNull', False))
        result['is_translucent'] = bool(params.get('IsTranslucent', False))
        props = params.get('Properties') or {}
        bpo = props.get('BasePropertyOverrides') or {}
        result['blend_mode'] = bpo.get('BlendMode') or params.get('BlendMode')
        _apply_bpo_flags(result, bpo)

        # Full FModel Exports dump — fill gaps + always capture Parent / BPO flags.
        parent_obj_path = ""
        entry = utils.first_ue_export(data, "MaterialInstanceConstant")
        if entry:
            eprops = entry.get("Properties") or {}
            parent_ref = eprops.get("Parent")
            if not result.get("parent"):
                result["parent"] = _parent_name_from_object(parent_ref)
            parent_obj_path = _parent_object_path(parent_ref)
            if parent_obj_path:
                result["parent_object_path"] = parent_obj_path
            bpo2 = eprops.get("BasePropertyOverrides") or {}
            _apply_bpo_flags(result, bpo2)
            need_tex = not result['textures']
            need_scalars = not result['scalars']
            need_switches = not result['switches']
            need_colours = not result['colours']
            if need_tex or need_scalars or need_switches or need_colours:
                if need_tex:
                    for t in eprops.get("TextureParameterValues") or []:
                        info = t.get("ParameterInfo") or {}
                        name = info.get("Name") or t.get("ParameterName") or ""
                        val = t.get("ParameterValue") or {}
                        obj_path = ""
                        if isinstance(val, dict):
                            obj_path = val.get("ObjectPath") or ""
                        elif isinstance(val, str):
                            obj_path = val
                        if name and obj_path:
                            clean = obj_path.rsplit(".", 1)[0] if "." in obj_path.split("/")[-1] else obj_path
                            result['textures'].append((name, clean))
                if need_scalars:
                    for s in eprops.get("ScalarParameterValues") or []:
                        info = s.get("ParameterInfo") or {}
                        name = info.get("Name") or s.get("ParameterName") or ""
                        if name and "ParameterValue" in s:
                            try:
                                result['scalars'][name] = float(s["ParameterValue"])
                            except (TypeError, ValueError):
                                pass
                if need_switches:
                    static = eprops.get("StaticSwitchParameterValues")
                    if static is None:
                        static = (eprops.get("StaticParameters") or {}).get("StaticSwitchParameters")
                    if not static:
                        # Full FModel dumps often store switches here (ArchitecturePreset_Trim).
                        static = (eprops.get("StaticParametersRuntime") or {}).get(
                            "StaticSwitchParameters"
                        ) or []
                    for s in static or []:
                        info = s.get("ParameterInfo") or {}
                        name = info.get("Name") or s.get("ParameterName") or ""
                        if name:
                            result['switches'][name] = bool(s.get("Value", s.get("ParameterValue", False)))
                if need_colours:
                    for c in eprops.get("VectorParameterValues") or []:
                        info = c.get("ParameterInfo") or {}
                        name = info.get("Name") or c.get("ParameterName") or ""
                        val = c.get("ParameterValue") or {}
                        if name and isinstance(val, dict):
                            result['colours'].append((name, (
                                float(val.get("R", 1.0)),
                                float(val.get("G", 1.0)),
                                float(val.get("B", 1.0)),
                                float(val.get("A", 1.0)),
                            )))
    except Exception as e:
        print(f"Arc Raiders PSK Importer: Failed to parse flat MI JSON '{json_path}': {e}")
        return result
    # Compact FModel dumps for child PropTrim MIs often omit inherited CR/NXX
    # (only Prop AO + Overlay overrides). Pull those from the library parent.
    stem = os.path.splitext(os.path.basename(json_path or ""))[0]
    _enrich_proptrim_inherited_textures(result, stem, json_path)
    _enrich_parent_material_textures(
        result, json_path, result.get("parent_object_path") or "",
    )
    _enrich_broken_glass_default_textures(result, stem)
    _FLAT_MI_CACHE[cache_key] = result
    return result



# Soft-match folder MI inference (Vent/PropTrim/Rebar guesses by name similarity).
# Hard-disabled: Stage 2 / Fix White use SM JSON StaticMaterials only, plus
# water/decal preferred_mi when already restricted by _preferred_mi_is_single_slot_override.
ENABLE_FUZZY_MI_INFER = False


# Child PropTrim MIs often only override Prop AO + Overlay; CR/NXX live on the
# MaterialLibrary parent (MI_PropTrim_Painted_01_A) or a sibling that authored
# the full sheet. Class-based inherit (token / sibling / folder / library
# fallback) — no per-asset allowlists, no fuzzy soft-match.
_PROPTRIM_INHERIT_PARAM_KEYS = frozenset({
    "CR Texture", "NXX/NMX Texture", "NXX", "NOM", "NXM", "NMX", "HolesNXX",
})

_PROPTRIM_LIBRARY_BY_TOKEN = (
    (("paintedbeams", "painted_beams"), "MI_PropTrim_Painted_01_A"),
    (("paintedworn", "painted_worn"), "MI_PropTrim_PaintedWorn_01_A"),
    (("paintedclean", "painted_clean"), "MI_PropTrim_Painted_01_A"),
    (("paintedmetal", "painted_metal"), "MI_PropTrim_Painted_01_A"),
    (("woodpaint", "woodpainted", "wood_paint"), "MI_PropTrim_WoodPaint_01"),
    (("barealuminium", "barealuminum", "bare_aluminium", "bare_aluminum"), "MI_PropTrim_Metal_01_A"),
    (("rusted", "rust"), "MI_PropTrim_Rusted_01_A"),
    (("metal", "cleanmetal", "aluminium", "aluminum", "steel"), "MI_PropTrim_Metal_01_A"),
    (("wood",), "MI_PropTrim_Wood_01"),
    (("worn",), "MI_PropTrim_PaintedWorn_01_A"),
    (("painted",), "MI_PropTrim_Painted_01_A"),
)

_PROPTRIM_LIBRARY_FALLBACKS = (
    "MI_PropTrim_Painted_01_A",
    "MI_PropTrim_Metal_01_A",
    "MI_PropTrim_PaintedWorn_01_A",
    "MI_PropTrim_Rusted_01_A",
)



def _proptrim_library_parent_stem(mi_stem: str) -> str:
    s = (mi_stem or "").lower().replace("-", "_")
    if "proptrim" not in s and "prop_trim" not in s:
        return ""
    for tokens, lib in _PROPTRIM_LIBRARY_BY_TOKEN:
        if any(t in s for t in tokens):
            return lib
    return ""



def _proptrim_sibling_mi_stems(mi_stem: str) -> list[str]:
    """MI_*_PropTrim_X → Beams / family siblings that often author full CR/NXX."""
    m = re.search(r"(?i)(_PropTrim_.+)$", mi_stem or "")
    if not m:
        return []
    suffix = m.group(1)
    out = []
    for prefix in (
        "MI_Wrh_Beams_01", "MI_Wrh_Beams_02", "MI_Wrh_Beams_03",
        "MI_Wrh_Ceiling_01", "MI_Wrh_Ceiling_02",
    ):
        out.append(f"{prefix}{suffix}")
        out.append(f"{prefix}{suffix}_MCP_01")
    return out



def _mi_json_has_cr_texture(json_path: str) -> bool:
    """Peek MI JSON for authored CR without running PropTrim inherit (avoids recursion)."""
    if not json_path or not os.path.isfile(json_path):
        return False
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return False
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return False
    tex = data.get("Textures") or {}
    if isinstance(tex, dict) and ("CR Texture" in tex or "CR" in tex):
        return True
    entry = utils.first_ue_export(data, "MaterialInstanceConstant") if hasattr(utils, "first_ue_export") else None
    if not entry and isinstance(data.get("Exports"), list):
        for e in data["Exports"]:
            if "MaterialInstance" in str(e.get("Type") or ""):
                entry = e
                break
    if not entry:
        return False
    for t in (entry.get("Properties") or {}).get("TextureParameterValues") or []:
        info = t.get("ParameterInfo") or {}
        name = str(info.get("Name") or t.get("ParameterName") or "")
        if name in ("CR Texture", "CR"):
            return True
    return False



def _proptrim_folder_cr_donor(folder: str, mi_path: str = "") -> str:
    """Same-folder PropTrim MI that already authors CR/NXX (any sibling family)."""
    if not folder or not os.path.isdir(folder):
        return ""
    mi_key = os.path.normcase(os.path.normpath(mi_path or ""))
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return ""
    for fname in names:
        low = fname.lower()
        if not low.endswith(".json"):
            continue
        if "proptrim" not in low and "prop_trim" not in low:
            continue
        if not (low.startswith("mi_") or low.startswith("m_")):
            continue
        path = os.path.join(folder, fname)
        if os.path.normcase(os.path.normpath(path)) == mi_key:
            continue
        if _mi_json_has_cr_texture(path):
            return path
    return ""



def _enrich_proptrim_inherited_textures(mi: dict, mi_stem: str, mi_path: str = "") -> None:
    """Fill missing CR/NXX on PropTrim children from library / sibling / folder donor."""
    if not mi:
        return
    stem = (mi_stem or "").strip()
    stem_l = stem.lower()
    if "proptrim" not in stem_l and "prop_trim" not in stem_l:
        return
    params = {p for p, _ in (mi.get("textures") or [])}
    if "CR Texture" in params or "CR" in params:
        return

    folder = os.path.dirname(mi_path or "")
    candidates: list[str] = []
    for sib in _proptrim_sibling_mi_stems(stem):
        if sib not in candidates:
            candidates.append(sib)
    lib = _proptrim_library_parent_stem(stem)
    if lib and lib not in candidates:
        candidates.append(lib)
    for fb in _PROPTRIM_LIBRARY_FALLBACKS:
        if fb not in candidates:
            candidates.append(fb)

    parent_path = ""
    mi_key = os.path.normcase(os.path.normpath(mi_path or ""))
    for cand in candidates:
        found = _resolve_mi_json_path(cand, "", folder)
        if not found or not os.path.isfile(found):
            continue
        if os.path.normcase(os.path.normpath(found)) == mi_key:
            continue
        # Prefer donors that actually expose CR (skip other AO-only children).
        if not _mi_json_has_cr_texture(found):
            continue
        parent_path = found
        break
    if not parent_path:
        parent_path = _proptrim_folder_cr_donor(folder, mi_path)
    if not parent_path:
        return

    # Parent parse re-enters enrich; parents with CR return immediately.
    parent = _parse_flat_mi_json(parent_path)
    have = {p for p, _ in (mi.get("textures") or [])}
    added = 0
    for param, path in parent.get("textures") or []:
        if not param or not path or param in have:
            continue
        pl = str(param).lower()
        # Never clobber child Prop AO / Overlay overrides.
        if param in ("Prop AO Texture", "Overlay") or "overlay" in pl:
            continue
        if "ao" in pl and "prop" in pl:
            continue
        inherit = (
            param in _PROPTRIM_INHERIT_PARAM_KEYS
            or pl.startswith("t_proptrimsheet")
            or pl.endswith("_cr")
            or "nxx" in pl
            or "nmx" in pl
            or pl.endswith("_nom")
        )
        if not inherit:
            continue
        mi.setdefault("textures", []).append((param, path))
        have.add(param)
        added += 1
    if added and not mi.get("parent"):
        mi["parent"] = os.path.splitext(os.path.basename(parent_path))[0]
    if added:
        try:
            utils.get_logger().info(
                "PropTrim inherit %s ← %s (+%d tex)",
                stem, os.path.basename(parent_path), added,
            )
        except Exception:
            pass



def _effective_sm_slot_name(slot: dict) -> str:
    """Prefer ImportedMaterialSlotName when MaterialSlotName is mapper / Material_N noise."""
    name = (slot.get("MaterialSlotName") or "").strip()
    imported = (slot.get("ImportedMaterialSlotName") or "").strip()
    nl = name.lower()
    if imported and (
        not name
        or nl.startswith("m_trimmapper")
        or nl.startswith("material_")
        or re.match(r"^material(\.\d+)?$", nl)
        or nl.startswith("uematerial")
    ):
        return imported
    return name or imported



def _set_material_clip(mat, threshold: float = 0.3333, two_sided: bool = False):
    """Masked/CLIP alpha so shell-style meshes don't render as opaque floats."""
    try:
        mat.blend_method = 'CLIP'
    except Exception:
        pass
    try:
        mat.alpha_threshold = float(threshold)
    except Exception:
        pass
    try:
        # EEVEE Next / Blender 4.2+
        mat.surface_render_method = 'DITHERED'
    except Exception:
        pass
    try:
        mat.use_backface_culling = not bool(two_sided)
    except Exception:
        pass



def _set_material_alpha_mode(
    mat,
    mode: str = "CLIP",
    threshold: float = 0.3333,
    two_sided: bool = False,
):
    """Apply CLIP / HASHED / BLEND / OPAQUE for map materials.

    ``mode``: CLIP | HASHED | BLEND | OPAQUE
    """
    mode_u = (mode or "CLIP").upper()
    try:
        if mode_u == "HASHED":
            mat.blend_method = "HASHED"
        elif mode_u == "BLEND":
            mat.blend_method = "BLEND"
        elif mode_u == "OPAQUE":
            mat.blend_method = "OPAQUE"
        else:
            mat.blend_method = "CLIP"
    except Exception:
        pass
    try:
        if mode_u in ("CLIP", "HASHED"):
            mat.alpha_threshold = float(threshold)
    except Exception:
        pass
    try:
        if mode_u == "BLEND":
            mat.surface_render_method = "BLENDED"
        elif mode_u == "OPAQUE":
            mat.surface_render_method = "DITHERED"
        else:
            mat.surface_render_method = "DITHERED"
    except Exception:
        pass
    try:
        mat.use_backface_culling = not bool(two_sided)
    except Exception:
        pass



def _load_mi_textures(json_path: str, nodes, links, group_node,
                      connected: dict, x_conn=-600, x_unconn=-1000):
    mi = _parse_flat_mi_json(json_path)
    row_c = 400
    row_u = 400
    seen_paths = {}
    connected_set = set()

    for param, obj_path in mi['textures']:
        if obj_path.startswith('/Engine/') or 'CharacterSnow' in obj_path:
            continue
        fpath = textures.find_texture_from_object_path(obj_path + '.0')
        if not fpath:
            continue

        if fpath in seen_paths:
            existing_node = seen_paths[fpath]
            if param in connected and group_node:
                socket = connected[param]
                if socket not in connected_set and socket in group_node.inputs:
                    links.new(existing_node.outputs["Color"], group_node.inputs[socket])
                    connected_set.add(socket)
            continue

        img = bpy.data.images.load(fpath, check_existing=True)
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param
        node.interpolation = "Cubic"
        seen_paths[fpath] = node

        is_normal = ("Normal" in param or "normal" in param or
                     param in ("PM_Normals", "T_EYE_NORMALS", "T_Eye_Wet_Normal"))

        if param in connected and group_node:
            socket = connected[param]
            if socket not in connected_set and socket in group_node.inputs:
                if is_normal:
                    img.colorspace_settings.name = "Non-Color"
                node.location = (x_conn, row_c)
                links.new(node.outputs["Color"], group_node.inputs[socket])
                connected_set.add(socket)
                row_c -= 300
            else:
                node.location = (x_unconn, row_u)
                row_u -= 300
        else:
            if is_normal:
                img.colorspace_settings.name = "Non-Color"
            node.location = (x_unconn, row_u)
            row_u -= 300

    for j, (param, rgba) in enumerate(mi['colours']):
        rgb_node = nodes.new("ShaderNodeRGB")
        rgb_node.label = param
        rgb_node.outputs[0].default_value = rgba
        rgb_node.location = (700 + (j % 2) * 200, -(j // 2) * 180)



# ---------------------------------------------------------------------------
# Multi-slot material setup (weapons + enemies)
# ---------------------------------------------------------------------------

def _character_layout_search_folders(*paths: str) -> list[str]:
    """Heroes/character export layout: Meshes + Materials + Textures siblings.

    Kalika etc. keep ``SK_*.psk`` under ``.../Resources/Base/Meshes/`` while
    ``MI_*.json`` lives in ``.../Materials/`` and PNGs in ``.../Textures/``.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(folder: str) -> None:
        if not folder:
            return
        key = os.path.normcase(os.path.normpath(folder))
        if key in seen:
            return
        if os.path.isdir(folder):
            seen.add(key)
            out.append(folder)

    for path in paths:
        if not path:
            continue
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if not folder:
            continue
        _add(folder)
        parent = os.path.dirname(folder)
        _add(parent)
        for sub in ("Materials", "Material", "Textures", "Meshes"):
            _add(os.path.join(parent, sub))
            _add(os.path.join(folder, sub))
    return out



def _mi_json_matches_requested_stem(json_path: str, requested_stem: str) -> bool:
    """True when MI JSON body looks like the stem we asked for.

    Some FModel dumps write the wrong asset under an MI_*.json filename
    (Package/Name point at a different MI). Accept when Name/Package are
    missing (legacy wrappers) or match; reject clear mismatches so Stage 2
    does not paint Aircon with Awning / Concrete with Rock.
    """
    stem = (requested_stem or "").strip()
    if "." in stem:
        stem = stem.split(".", 1)[0]
    if not stem or not json_path or not os.path.isfile(json_path):
        return False
    stem_l = stem.lower()
    # Filename is a weak signal — still verify body when Name/Package exist.
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return False
    entry = None
    for e in utils.ue_export_entries(data):
        if not isinstance(e, dict):
            continue
        if e.get("Type") in ("MaterialInstanceConstant", "MaterialInstance"):
            entry = e
            break
        if entry is None:
            entry = e
    if not isinstance(entry, dict):
        return False
    name = (entry.get("Name") or "").strip()
    if "." in name:
        name = name.split(".", 1)[0]
    package = (entry.get("Package") or "").replace("\\", "/").strip()
    pkg_leaf = package.rsplit("/", 1)[-1] if package else ""
    if "." in pkg_leaf:
        pkg_leaf = pkg_leaf.split(".", 1)[0]
    # No identity fields — cannot prove mismatch (older / odd wrappers).
    if not name and not pkg_leaf:
        return True
    if name and name.lower() != stem_l:
        return False
    if pkg_leaf and pkg_leaf.lower() != stem_l:
        return False
    return True



def _mesh_json_matches_requested_stem(json_path: str, requested_stem: str) -> bool:
    """True when SM_/SK_ JSON body Name/Package matches the mesh stem we asked for.

    FModel parallel Properties export can write SK_Western_Belt under
    SM_POI16_WaterControl_Roof_01_A.json (shared Document race). Accepting that
    body paints belt/helmet cosmetics onto roofs and rocks.
    """
    stem = (requested_stem or "").strip()
    if "." in stem:
        stem = stem.split(".", 1)[0]
    if not stem or not json_path or not os.path.isfile(json_path):
        return False
    stem_l = stem.lower()
    # Hash-bake / LOD variants: compare against stem variants.
    variants = {v.lower() for v in _mesh_stem_variants(stem)}
    variants.add(stem_l)
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return False
    entry = None
    for e in utils.ue_export_entries(data):
        if not isinstance(e, dict):
            continue
        if e.get("Type") in ("StaticMesh", "SkeletalMesh"):
            entry = e
            break
    if not isinstance(entry, dict):
        return False
    name = (entry.get("Name") or "").strip()
    if "." in name:
        name = name.split(".", 1)[0]
    package = (entry.get("Package") or "").replace("\\", "/").strip()
    pkg_leaf = package.rsplit("/", 1)[-1] if package else ""
    if "." in pkg_leaf:
        pkg_leaf = pkg_leaf.split(".", 1)[0]
    if not name and not pkg_leaf:
        return True
    name_l = name.lower() if name else ""
    pkg_l = pkg_leaf.lower() if pkg_leaf else ""
    if name_l and name_l not in variants:
        return False
    if pkg_l and pkg_l not in variants:
        return False
    return True



def _resolve_mi_json_path(
    mi_stem: str,
    obj_path: str,
    psk_folder: str,
    *,
    context: str = CTX_ANY,
) -> str:
    """Find MI_*.json beside the PSK, via ObjectPath, or scoped FMDex.

    ``context`` gates which trees are legal (map never accepts Characters/).
    ObjectPath is preferred over any basename walk.
    """
    if not mi_stem:
        return ""

    mi_stem = mi_stem.strip()
    # ObjectPath outside the allowed context → refuse before any disk search.
    if obj_path and not path_allowed_for_context(obj_path, context):
        try:
            utils.get_logger().debug(
                "reject MI ObjectPath for context=%s stem=%s path=%s",
                context, mi_stem, obj_path,
            )
        except Exception:
            pass
        return ""

    cache_key = (
        mi_stem.lower(),
        _norm_path_key(psk_folder or ""),
        (obj_path or "").replace("\\", "/").lower(),
        (context or CTX_ANY),
    )
    if cache_key in _MI_JSON_PATH_CACHE:
        return _MI_JSON_PATH_CACHE[cache_key]

    def _remember(path: str) -> str:
        _MI_JSON_PATH_CACHE[cache_key] = path or ""
        return path or ""

    def _accept(path: str) -> str:
        """Cache + return path only when body identity + context match."""
        if not path or not os.path.isfile(path):
            return ""
        if not path_allowed_for_context(path, context):
            try:
                utils.get_logger().debug(
                    "reject MI path for context=%s stem=%s: %s",
                    context, mi_stem, path,
                )
            except Exception:
                pass
            return ""
        if not _mi_json_matches_requested_stem(path, mi_stem):
            try:
                utils.get_logger().debug(
                    "reject MI JSON identity mismatch for '%s': %s", mi_stem, path
                )
            except Exception:
                pass
            return ""
        return _remember(path)

    # UE asset ObjectPaths frequently include redundant dot segments (e.g.
    # "MI_X.MI_X.0"). Those may flow into mi_stem and break local filename
    # matching, so try both the raw stem and the "first token" stem.
    stem_candidates = [mi_stem]
    if "." in mi_stem:
        stem0 = mi_stem.split(".", 1)[0]
        if stem0 and stem0 not in stem_candidates:
            stem_candidates.append(stem0)

    for stem in stem_candidates:
        search_folders: list[str] = []
        if psk_folder:
            search_folders.append(psk_folder)
            # Heroes Base: MI JSON is under sibling Materials/, not Meshes/.
            # Never pull character layout siblings when resolving map/env props.
            if context != CTX_MAP:
                search_folders.extend(_character_layout_search_folders(psk_folder))
            for alt in utils.remap_path_into_content_dirs(
                os.path.join(psk_folder, "__probe__")
            ):
                search_folders.append(os.path.dirname(alt))
                if context != CTX_MAP:
                    search_folders.extend(
                        _character_layout_search_folders(os.path.dirname(alt))
                    )
        shared = utils.get_weapon_shared_folder()
        if shared and context in (CTX_ANY, CTX_WEAPON):
            search_folders.append(shared)
        seen_sf: set[str] = set()
        for search_folder in search_folders:
            key = os.path.normcase(os.path.normpath(search_folder or ""))
            if not search_folder or key in seen_sf:
                continue
            if not path_allowed_for_context(search_folder, context):
                continue
            seen_sf.add(key)
            candidate = os.path.join(search_folder, stem + ".json")
            hit = _accept(candidate)
            if hit:
                return hit

        # ObjectPath is O(1) path join — prefer before FMDex (basename keys may walk).
        if obj_path:
            found = textures.find_asset_from_object_path(obj_path, ".json")
            hit = _accept(found) if found else ""
            if hit:
                return hit

            # Extra defensive tries for instance-suffixed ObjectPaths.
            alt = obj_path
            if alt.endswith(".0"):
                found = textures.find_asset_from_object_path(alt[:-2], ".json")
                hit = _accept(found) if found else ""
                if hit:
                    return hit
            else:
                found = textures.find_asset_from_object_path(alt + ".0", ".json")
                hit = _accept(found) if found else ""
                if hit:
                    return hit

        # FMDex: full package path OK; basename Content walks are gated by context
        # (map forbids Characters/ and skips unconstrained Pioneer walks).
        try:
            from .. import fmdex
            found = fmdex.resolve_export_file(
                stem, ".json", context=context, allow_basename_walk=(context != CTX_MAP),
            )
            hit = _accept(found) if found else ""
            if hit:
                return hit
        except TypeError:
            # Older fmdex without context kwargs
            try:
                from .. import fmdex
                found = fmdex.resolve_export_file(stem, ".json")
                hit = _accept(found) if found else ""
                if hit:
                    return hit
            except Exception:
                pass
        except Exception:
            pass

        # Enemy meshes often live under Enemies/<Type>/.../Art while shared MIs
        # sit in Enemies/Shared/Art/<Set>/ (e.g. LightAndEliteDrone). Search there
        # before relying on PioneerGame Root ObjectPath resolve.
        if psk_folder and context in (CTX_ANY, CTX_ENEMY, CTX_WEAPON):
            norm = psk_folder.replace("\\", "/").lower()
            marker = "/enemies/"
            idx = norm.find(marker)
            if idx >= 0:
                enemies_root = psk_folder[: idx + len(marker) - 1]  # .../Enemies
                shared_art = os.path.join(enemies_root, "Shared", "Art")
                if os.path.isdir(shared_art):
                    target_l = (stem + ".json").lower()
                    try:
                        for walk_root, _dirs, files in os.walk(shared_art):
                            for fname in files:
                                if fname.lower() == target_l:
                                    hit = _accept(os.path.join(walk_root, fname))
                                    if hit:
                                        return hit
                    except OSError:
                        pass

        # Last-resort: MaterialLibrary MI folders under every known Content root.
        # Prefer direct folder hits; bounded walk of Material_Instances only (not
        # the whole Pioneer tree — that made Stage 2 unusable at city scale).
        # Map/effect contexts may use MaterialLibrary; never Characters.
        if context in (CTX_ANY, CTX_MAP, CTX_EFFECT, CTX_ENEMY, CTX_WEAPON):
            try:
                for content_dir in utils.get_content_dirs():
                    inst_root = os.path.join(
                        content_dir, "Pioneer", "MaterialLibrary", "Material_Instances",
                    )
                    for folder_name in (
                        "Enemies", "Decals", "Weapons", "Items", "Props", "Themes", "Trims",
                    ):
                        inst_folder = os.path.join(inst_root, folder_name)
                        candidate = os.path.join(inst_folder, stem + ".json")
                        hit = _accept(candidate)
                        if hit:
                            return hit
                    if os.path.isdir(inst_root):
                        target_l = (stem + ".json").lower()
                        walked = 0
                        for walk_root, _dirs, files in os.walk(inst_root):
                            for fname in files:
                                if fname.lower() == target_l:
                                    hit = _accept(os.path.join(walk_root, fname))
                                    if hit:
                                        return hit
                            walked += 1
                            if walked > 4000:
                                break
                    # Map props often live beside meshes under Environment/... —
                    # try ObjectPath-less basename only in the remapped mesh folder.
                    if psk_folder:
                        for alt in utils.remap_path_into_content_dirs(
                            os.path.join(psk_folder, stem + ".json"),
                            [content_dir],
                        ):
                            hit = _accept(alt)
                            if hit:
                                return hit
            except Exception:
                pass

        # ObjectPath / MaterialLibrary often hit corrupt multithread dumps (wrong
        # Name/Package body under the right filename). Exact-stem basename search
        # under Environment + MaterialLibrary with identity verify recovers good
        # duplicates (e.g. MI_Concrete_Trim_02_A beside Fountain_01). Not fuzzy.
        hit = _find_mi_json_by_stem_identity(stem, context=context)
        if hit:
            return _remember(hit)

    return _remember("")



# Exact-stem → candidate JSON paths (built once per Content root set).
# Declared with session caches at module top; rebuilt by _mi_stem_identity_index.


def _mi_stem_identity_index() -> dict[str, list[str]]:
    """Basename index of MI/M_*.json under Environment + MaterialLibrary.

    Built once; used when ObjectPath dumps fail identity so Stage 2 does not
    re-walk Pioneer for every unresolved slot.
    """
    global _MI_STEM_IDENTITY_INDEX, _MI_STEM_IDENTITY_INDEX_ROOTS
    try:
        roots = tuple(
            os.path.normcase(os.path.normpath(c)) for c in (utils.get_content_dirs() or [])
        )
    except Exception:
        roots = ()
    if _MI_STEM_IDENTITY_INDEX is not None and roots == _MI_STEM_IDENTITY_INDEX_ROOTS:
        return _MI_STEM_IDENTITY_INDEX
    index: dict[str, list[str]] = {}
    subtrees = (
        ("Pioneer", "Environment"),
        ("Pioneer", "MaterialLibrary"),
        ("Pioneer", "Architecture"),
    )
    try:
        content_dirs = utils.get_content_dirs() or []
    except Exception:
        content_dirs = []
    for content_dir in content_dirs:
        for parts in subtrees:
            root = os.path.join(content_dir, *parts)
            if not os.path.isdir(root):
                continue
            try:
                for walk_root, dirs, files in os.walk(root):
                    base = os.path.basename(walk_root).lower()
                    if base in {"characters", "outfits", "enemies", "weapons"}:
                        dirs[:] = []
                        continue
                    for fname in files:
                        fl = fname.lower()
                        if not fl.endswith(".json"):
                            continue
                        if not (fl.startswith("mi_") or fl.startswith("m_")):
                            continue
                        stem = fname[:-5]  # strip .json
                        key = stem.lower()
                        path = os.path.join(walk_root, fname)
                        index.setdefault(key, []).append(path)
            except OSError:
                continue
    # Prefer Environment (prop-local good copies) over MaterialLibrary dumps.
    for key, paths in index.items():
        paths.sort(
            key=lambda p: (
                0 if f"{os.sep}environment{os.sep}" in p.lower() else 1,
                0 if f"{os.sep}materiallibrary{os.sep}" not in p.lower() else 1,
                len(p),
            )
        )
    _MI_STEM_IDENTITY_INDEX = index
    _MI_STEM_IDENTITY_INDEX_ROOTS = roots
    return index



def _find_mi_json_by_stem_identity(mi_stem: str, *, context: str = CTX_ANY) -> str:
    """Find ``{stem}.json`` whose body Name matches (map-safe trees only).

    Used only after ObjectPath/MaterialLibrary identity rejects. Exact stem —
    not fuzzy. Indexed once for Stage 2 city-scale performance.
    """
    stem = (mi_stem or "").strip()
    if not stem:
        return ""
    key = stem.lower()
    for cand in _mi_stem_identity_index().get(key) or []:
        if not path_allowed_for_context(cand, context):
            continue
        if _mi_json_matches_requested_stem(cand, stem):
            return cand
    return ""



def _mesh_stem_variants(psk_stem: str) -> list[str]:
    """Stems to try when resolving SM/SK material JSON beside a mesh file.

    Order matters:
      1. Exact stem (keeps PROXY ``*_LOD1`` working)
      2. FModel spline-bake hash strip: ``SM_Road_…-BA270E`` → ``SM_Road_…``
      3. LOD-number strip as a last fallback only
      4. SM_/SK_ prefix removed variants of the above
    """
    stem = (psk_stem or "").strip()
    if not stem:
        return []

    # Blender object names sometimes leak into callers — normalize.
    stem = re.sub(r"^SRC_", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"^Spline_", "", stem, flags=re.IGNORECASE)

    variants: list[str] = []

    def _add(s: str) -> None:
        s = (s or "").strip()
        if s and s not in variants:
            variants.append(s)

    _add(stem)

    # FModel Map + Meshes spline bake: unique deformed copy Name-HEX.uemodel
    # Hex length is typically 5–8 (BA270E, 018AA6, …). Also accept 4+ for
    # truncated object names like …-BA27 when stem comes from Blender object.
    hash_stripped = re.sub(r"-[0-9A-Fa-f]{4,10}$", "", stem)
    if hash_stripped != stem:
        _add(hash_stripped)
        print(
            f"Arc Raiders Stage 2: spline-bake stem '{stem}' -> base '{hash_stripped}'"
        )

    for base in list(variants):
        lod_stripped = re.sub(r"_LOD\d+$", "", base, flags=re.IGNORECASE)
        if lod_stripped != base:
            _add(lod_stripped)

    for base in list(variants):
        if base.upper().startswith("SM_"):
            _add(base[3:])
        elif base.upper().startswith("SK_"):
            _add(base[3:])

    return variants



def _mesh_material_json_paths(psk_path: str) -> list[str]:
    """Candidate SK_/SM_*.json paths beside the mesh and under full Content dumps.

    FModel Map + Meshes exports .uemodel under MapPlacements without sibling JSON.
    StaticMaterials live in the full PioneerGame Content dump at the same relative path.

    Spline bake exports use ``SM_Asset-HEX.uemodel``; materials come from base
    ``SM_Asset.json`` in the same package folder.
    """
    if not psk_path:
        return []
    psk_path = os.path.normpath(os.path.abspath(bpy.path.abspath(psk_path)))
    psk_stem = os.path.splitext(os.path.basename(psk_path))[0]
    stems = _mesh_stem_variants(psk_stem)
    names = [s + ".json" for s in stems]

    folders: list[str] = []
    seen_folders: set[str] = set()

    def _add_folder(folder: str) -> None:
        if not folder:
            return
        key = os.path.normcase(os.path.normpath(folder))
        if key in seen_folders:
            return
        seen_folders.add(key)
        folders.append(os.path.normpath(folder))

    _add_folder(os.path.dirname(psk_path))
    # Remap MapPlacements/.../Content/Pioneer/... → full dump Content/Pioneer/...
    for alt_mesh in utils.remap_path_into_content_dirs(psk_path):
        _add_folder(os.path.dirname(alt_mesh))

    out: list[str] = []
    seen_paths: set[str] = set()

    def _add_path(path: str) -> None:
        key = os.path.normcase(os.path.normpath(path))
        if key in seen_paths:
            return
        seen_paths.add(key)
        out.append(os.path.normpath(path))

    for folder in folders:
        for name in names:
            candidate = os.path.join(folder, name)
            _add_path(candidate)
            if os.path.isfile(candidate) and re.search(
                r"-[0-9A-Fa-f]{4,10}\.uemodel$", psk_path, re.IGNORECASE
            ):
                # Proof / debug: hashed bake found base JSON
                print(
                    f"Arc Raiders Stage 2: hash-bake JSON hit "
                    f"{os.path.basename(psk_path)} -> {candidate}"
                )
        # Nested export: .../StaticMesh_0/StaticMesh_0.json beside .../StaticMesh_0.uemodel
        for stem in stems:
            _add_path(os.path.join(folder, stem, stem + ".json"))

    # Basename fallback via FMDex when folder remap missed the canonical SM package.
    # Map context: never unconstrained Pioneer basename walks (Characters bleed).
    try:
        from .. import fmdex

        for stem in stems:
            # Skip walking for the unique bake id itself (Name-HEX).
            if re.search(r"-[0-9A-Fa-f]{4,10}$", stem):
                continue
            found = fmdex.resolve_export_file(
                stem, ".json", context=CTX_MAP, allow_basename_walk=False,
            )
            if found and path_allowed_for_context(found, CTX_MAP):
                _add_path(found)
                print(
                    f"Arc Raiders Stage 2: FMDex JSON hit '{stem}' -> {found}"
                )
    except TypeError:
        try:
            from .. import fmdex

            for stem in stems:
                if re.search(r"-[0-9A-Fa-f]{4,10}$", stem):
                    continue
                found = fmdex.resolve_export_file(stem, ".json")
                if found and path_allowed_for_context(found, CTX_MAP):
                    _add_path(found)
        except Exception:
            pass
    except Exception:
        pass

    return out



_ENGINE_OR_EMPTY_MESH_RE = re.compile(
    r"^(cube|plane|sphere|cylinder|cone|staticmesh_\d+)$",
    re.IGNORECASE,
)



def _parse_sk_material_slots(psk_path: str, *, context: str = CTX_ANY) -> list:
    """Return [(slot_name, mi_stem, mi_json_path), ...] from sibling SK_/SM_*.json.

    Rejects mesh JSON whose Name/Package does not match the requested SM_/SK_
    stem (corrupt FModel dumps that put helmet/belt packages under rock/roof
    filenames). ``context`` gates MI ObjectPath / disk resolves.
    """
    if not psk_path:
        return []
    cache_key = f"{_norm_path_key(psk_path)}|{context or CTX_ANY}"
    if cache_key in _SK_SLOTS_CACHE:
        return _SK_SLOTS_CACHE[cache_key]

    psk_stem = os.path.splitext(os.path.basename(psk_path))[0]
    json_paths = _mesh_material_json_paths(psk_path)
    for json_path in json_paths:
        if not os.path.isfile(json_path):
            continue
        if not _mesh_json_matches_requested_stem(json_path, psk_stem):
            try:
                utils.get_logger().debug(
                    "reject SM/SK JSON identity mismatch for '%s': %s",
                    psk_stem, json_path,
                )
            except Exception:
                pass
            print(
                f"Arc Raiders: reject mismatched mesh JSON for '{psk_stem}': "
                f"{os.path.basename(json_path)} (wrong Name/Package body)"
            )
            continue
        if context == CTX_MAP and not path_allowed_for_context(json_path, CTX_MAP):
            print(
                f"Arc Raiders: reject out-of-context mesh JSON for map '{psk_stem}': "
                f"{json_path}"
            )
            continue
        folder = os.path.dirname(json_path)
        try:
            with open(json_path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            for entry in utils.ue_export_entries(data):
                entry_type = entry.get('Type', '')
                if entry_type not in ('SkeletalMesh', 'StaticMesh'):
                    continue
                props = entry.get('Properties', {})
                slots = (
                    entry.get('SkeletalMaterials')
                    or props.get('SkeletalMaterials')
                    or entry.get('StaticMaterials')
                    or props.get('StaticMaterials')
                    or []
                )
                result = []
                for slot in slots:
                    slot_name = _effective_sm_slot_name(slot)
                    mat_ref = (
                        slot.get('Material')
                        or slot.get('MaterialInterface')
                        or {}
                    )
                    obj_path = mat_ref.get('ObjectPath', '') or ""
                    mi_stem = textures.mi_stem_from_material_ref(mat_ref)
                    if not mi_stem:
                        # Keep a placeholder so later slots don't shift left and
                        # get matched against the wrong Blender material slot index.
                        result.append((slot_name, "", ""))
                        continue
                    # DecalMesh cards ship with Engine WorldGridMaterial — not a real MI.
                    stem_l = mi_stem.lower()
                    obj_l = (obj_path or "").replace("\\", "/").lower()
                    if (
                        stem_l in {"worldgridmaterial", "defaultmaterial"}
                        or "/engine/" in obj_l
                        or obj_l.startswith("/engine/")
                    ):
                        result.append((slot_name, "", ""))
                        continue
                    # Map props: refuse character/outfit ObjectPaths even if SM matched.
                    if obj_path and not path_allowed_for_context(obj_path, context):
                        print(
                            f"Arc Raiders: skip out-of-context MI '{mi_stem}' "
                            f"({obj_path}) for context={context}"
                        )
                        result.append((slot_name, mi_stem, ""))
                        continue
                    mi_json = _resolve_mi_json_path(
                        mi_stem, obj_path, folder, context=context,
                    )
                    result.append((slot_name, mi_stem, mi_json))
                if result:
                    _SK_SLOTS_CACHE[cache_key] = result
                    return result
        except Exception as e:
            print(f"Arc Raiders PSK Importer: Failed to parse mesh material slots: {e}")
    _SK_SLOTS_CACHE[cache_key] = []
    return []



def _parse_weapon_sk_json(psk_path: str) -> list:
    """Back-compat alias for multi-slot SK parsing."""
    return _parse_sk_material_slots(psk_path)



def _match_material_slot(
    obj,
    slot_name: str,
    slot_index: int,
    used_indices: set,
    mi_stem: str = "",
    *,
    allow_index_fallback: bool = True,
):
    """Bind SK/SM slot → Blender material slot.

    Prefer MI-stem / slot-name match when the mesh already has named materials.
    UEModel LOD material order often differs from SM JSON ``StaticMaterials``
    order (e.g. PerimeterWall: JSON slot0=Rebar, UEModel slot0=ConcreteDamaged).
    Index-first assignment painted rebar onto every face that still indexed 0.

    When ``allow_index_fallback`` is False (map props with UEModel MI_* names),
    never clobber by index — that is how belt/helmet MIs from corrupt SM JSON
    overwrote roofs and rocks.
    """
    wants = []
    for raw in (mi_stem, slot_name):
        want = (raw or "").strip().lower()
        if want and want not in wants:
            wants.append(want)
    # Warehouse ceilings etc.: SM ImportedMaterialSlotName is M_PropTrims while
    # Blender/UEModel keeps that name; MI stem is MI_*_PropTrim_*.
    if any("proptrim" in w for w in wants):
        for extra in ("m_proptrims", "m_proptrims_02", "proptrims", "m_proptrim"):
            if extra not in wants:
                wants.append(extra)
    if any("brokenglass" in w or w == "glass" for w in wants):
        for extra in ("brokenglass", "m_brokenglasssdf"):
            if extra not in wants:
                wants.append(extra)
    if wants:
        for i, s in enumerate(obj.material_slots):
            if i in used_indices or not s.material:
                continue
            sn = s.material.name.lower()
            sn0 = sn.split(".")[0]
            # Strip Stage 2 force_rebuild suffixes for matching
            sn0 = re.sub(r"(_force_rebuild)+$", "", sn0)
            arc_stem = ""
            try:
                arc_stem = str(s.material.get("arc_mi_stem") or "").strip().lower()
            except Exception:
                arc_stem = ""
            for want in wants:
                # PSK importer may prefix object name: "Obj_SlotName"
                if (
                    sn == want
                    or sn0 == want
                    or arc_stem == want
                    or sn.endswith("_" + want)
                    or (arc_stem and arc_stem.endswith("_" + want))
                ):
                    return s, i
                # Substring only for long tokens — short wants like "01"/"decal"
                # previously collided with MI_EnemyDecals_01 / unrelated slots.
                if len(want) >= 10 and (
                    want in sn0 or sn0 in want or (arc_stem and want in arc_stem)
                ):
                    return s, i
        if not allow_index_fallback:
            return None, -1
    if allow_index_fallback and slot_index < len(obj.material_slots) and slot_index not in used_indices:
        return obj.material_slots[slot_index], slot_index
    return None, -1



def _resolve_mi_texture_path(obj_path: str, local_folders: list = None) -> str:
    """Resolve a MI texture ObjectPath to a PNG, with local-folder fallback."""
    if not obj_path:
        return ""
    lookup = obj_path if obj_path.endswith(".0") else (obj_path + ".0")
    fpath = textures.find_texture_from_object_path(lookup)
    if fpath:
        return fpath
    leaf = obj_path.rstrip("/").split("/")[-1]
    leaf = leaf.split(".")[0]
    if not leaf:
        return ""
    for folder in (local_folders or []):
        if not folder or not os.path.isdir(folder):
            continue
        for ext in (".png", ".tga", ".jpg", ".jpeg"):
            candidate = os.path.join(folder, leaf + ext)
            if os.path.isfile(candidate):
                return candidate
    return ""



def _tex_lookup_from_flat_mi(mi: dict, local_folders: list = None) -> dict:
    """param -> (fpath, image) for textures referenced by a flat MI JSON."""
    tex_lookup = {}
    path_to_img = {}
    for param, obj_path in mi.get('textures', []):
        if not obj_path or obj_path.startswith('/Engine/') or 'CharacterSnow' in obj_path:
            continue
        fpath = _resolve_mi_texture_path(obj_path, local_folders)
        if not fpath:
            continue
        if fpath in path_to_img:
            tex_lookup[param] = (fpath, path_to_img[fpath])
            continue
        img = _load_image_cached(fpath)
        path_to_img[fpath] = img
        tex_lookup[param] = (fpath, img)
    return tex_lookup



def _find_flat_tex(tex_lookup: dict, *keys_or_suffixes: str):
    """Find a texture by exact MI param name, then by filename suffix (_cr, _nom, ...)."""
    lower_keys = {k.lower(): k for k in tex_lookup}
    for key in keys_or_suffixes:
        hit = lower_keys.get(key.lower())
        if hit:
            return hit, tex_lookup[hit][1]
    for key in keys_or_suffixes:
        suffix = key.lower()
        if not suffix.startswith('_'):
            suffix = '_' + suffix
        for param, (fpath, img) in tex_lookup.items():
            # Detail Normal / Detail* must not satisfy base NOH/CR suffix hunts.
            pl = (param or "").lower()
            if pl.startswith("detail") or "detail normal" in pl:
                continue
            stem = os.path.splitext(os.path.basename(fpath))[0].lower()
            if stem.endswith(suffix):
                return param, img
    return None, None



def _unlink_input(links, socket):
    """Remove any existing links into a node socket."""
    for lnk in list(socket.links):
        links.remove(lnk)

_TEX_IMAGE_EXTS = (".png", ".tga", ".jpg", ".jpeg")

# Map names / tokens that should get sand BRDF on CityGroundPlane
_SANDY_MAP_TOKENS = (
    "riventides", "dunes", "desert", "whitedesert", "sandsea", "south_dunes",
)

_INGAME_MAP_DIR_REL = os.path.join(
    "Pioneer", "UI", "Ingame", "HUD", "Map", "Assets",
)

# Addon-root assets/ (not materials/assets — __file__ lives under materials/).
_GROUND_MAP_REFS_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "assets", "ground_map_refs",
))

_IMAGE_FILE_EXTS = (".png", ".tga", ".jpg", ".jpeg", ".tif", ".tiff", ".webp")



def _stamp_mi_family(mat, family: str):
    try:
        mat["arc_mi_family"] = family
    except Exception:
        pass



def _mi_switch(switches: dict, *names, default=None):
    """Return the first authored static switch among *names*, else *default*."""
    for name in names:
        if name in switches:
            return bool(switches[name])
    return default



def _mi_scalar(scalars: dict, *names, default=0.0) -> float:
    for name in names:
        if name in scalars and scalars[name] is not None:
            try:
                return float(scalars[name])
            except (TypeError, ValueError):
                continue
    return float(default)



def _image_alpha_usable(img, clip: float = 0.3333, step: int = 32) -> bool:
    """True when an image's alpha channel has samples above the clip threshold.

    FModel sometimes exports opaque SimplePBR albedos with alpha=0 (FoxHat
    default). Wiring that into CLIP hides the whole mesh.
    """
    if img is None:
        return False
    try:
        channels = int(getattr(img, "channels", 0) or 0)
    except Exception:
        channels = 0
    if channels < 4:
        return False
    try:
        w, h = img.size
        px = img.pixels
    except Exception:
        return False
    if not w or not h or not px:
        return False
    thr = max(0.0, min(1.0, float(clip)))
    # Sample a coarse grid — enough to catch real masks without a full scan.
    for y in range(0, h, max(1, int(step))):
        row = y * w
        for x in range(0, w, max(1, int(step))):
            idx = (row + x) * 4 + 3
            if idx < len(px) and float(px[idx]) > thr:
                return True
    return False



def _fix_unusable_image_alpha(img, clip: float = 0.02) -> bool:
    """Ignore a dead alpha channel so Image Texture Color is not premultiplied black.

    FoxHat's default ``T_FoxHat_BaseColor`` ships with alpha=0 everywhere while RGB
    is valid. In Blender 5, ``alpha_mode=STRAIGHT`` still yields a black Color
    socket (associated/premul sampling), which looks like a linked black stub.
    Setting ``alpha_mode='NONE'`` restores RGB on Color without touching disk.
    Returns True when alpha was neutralized.
    """
    if img is None:
        return False
    try:
        mode = str(getattr(img, "alpha_mode", "") or "")
    except Exception:
        mode = ""
    if mode.upper() == "NONE":
        return False
    if _image_alpha_usable(img, clip=float(clip)):
        return False
    try:
        img.alpha_mode = "NONE"
        return True
    except Exception:
        return False



def _mi_colour(colours: list, *names, default=None):
    lower = {n.lower(): n for n in names}
    for param, rgba in colours or []:
        if param.lower() in lower:
            return rgba
    return default



def _find_env_tex(tex_lookup: dict, *keys: str):
    """Exact MI param match first (order preserved), then filename suffix fallback."""
    return _find_flat_tex(tex_lookup, *keys)



def _new_tex_image(nodes, img, label: str, loc, non_color: bool = False):
    if non_color:
        try:
            img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
    node = nodes.new("ShaderNodeTexImage")
    node.image = img
    node.label = label
    node.interpolation = "Cubic"
    node.location = loc
    return node



def _mapping_tiled(nodes, links, tiling: float, loc, from_uv=None):
    """UV → Mapping with uniform tiling. Returns Mapping Vector output."""
    if from_uv is None:
        uv = nodes.new("ShaderNodeTexCoord")
        uv.location = (loc[0] - 220, loc[1])
        from_uv = uv.outputs["UV"]
    mapping = nodes.new("ShaderNodeMapping")
    mapping.label = f"Tile ×{tiling:g}"
    mapping.location = loc
    scale = max(float(tiling), 0.001)
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    links.new(from_uv, mapping.inputs["Vector"])
    return mapping.outputs["Vector"]



def _mapping_world_density(
    nodes, links, meters_per_tile: float, loc, label: str = "",
    uv_offset: float = 0.0,
):
    """World Position → Mapping so texel density is constant in metres (not UV).

    Large scaled planes (tarmac / water cards) keep the same grain as small ones.
    ``meters_per_tile`` = world metres for one full texture repeat.
    ``uv_offset`` folds ArchitecturePreset_Trim ``UV Offset Amount`` into Location.
    """
    geo = nodes.new("ShaderNodeNewGeometry")
    geo.location = (loc[0] - 220, loc[1])
    mapping = nodes.new("ShaderNodeMapping")
    mpt = max(float(meters_per_tile), 0.05)
    scale = 1.0 / mpt
    mapping.label = label or f"World {mpt:g}m/tile"
    if abs(float(uv_offset)) > 1e-5:
        mapping.label += f" off={float(uv_offset):.3g}"
    mapping.location = loc
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    if abs(float(uv_offset)) > 1e-5:
        mapping.inputs["Location"].default_value = (float(uv_offset), 0.0, 0.0)
    links.new(geo.outputs["Position"], mapping.inputs["Vector"])
    return mapping.outputs["Vector"]



def _env_tex_vector(
    nodes,
    links,
    *,
    loc,
    tiling: float = 1.0,
    world_meters_per_tile: float | None = None,
    uv_offset: float = 0.0,
    rotate_uv: bool = False,
    force_mapping: bool = False,
):
    """Pick UV tiling or world-density vector for environment / road / tarp tex.

    ``uv_offset`` / ``rotate_uv`` honour PropTrim atlas cell selection (UVOffset
    scalar + Use Rotate UV's) and ArchitecturePreset_Trim UV Offset Amount.
    Without these, every PropTrim mesh samples the default atlas cell — often
    the vent/grille panel.
    """
    if world_meters_per_tile is not None and world_meters_per_tile > 0.0:
        # Fold MI Tiling into density: higher Tiling → finer grain
        mpt = float(world_meters_per_tile) / max(float(tiling), 0.05)
        mpt = max(0.25, min(mpt, 64.0))
        return _mapping_world_density(
            nodes, links, mpt, loc, uv_offset=float(uv_offset) if abs(float(uv_offset)) > 1e-5 else 0.0,
        )
    need_map = (
        force_mapping
        or abs(float(tiling) - 1.0) > 0.01
        or abs(float(uv_offset)) > 1e-5
        or rotate_uv
    )
    if not need_map:
        return None
    uv = nodes.new("ShaderNodeTexCoord")
    uv.location = (loc[0] - 220, loc[1])
    mapping = nodes.new("ShaderNodeMapping")
    scale = max(float(tiling), 0.001)
    mapping.label = f"Tile ×{scale:g}"
    if abs(float(uv_offset)) > 1e-5:
        mapping.label += f" off={float(uv_offset):.3g}"
    mapping.location = loc
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    # UE UVOffset shifts along U (and often V when Rotate is on).
    off = float(uv_offset)
    if rotate_uv:
        mapping.inputs["Location"].default_value = (off, off, 0.0)
        try:
            mapping.inputs["Rotation"].default_value = (0.0, 0.0, 1.5707963)  # 90°
        except Exception:
            pass
    else:
        mapping.inputs["Location"].default_value = (off, 0.0, 0.0)
    links.new(uv.outputs["UV"], mapping.inputs["Vector"])
    return mapping.outputs["Vector"]



def _wire_normal_map(nodes, links, color_sock, loc, strength: float = 1.0, label: str = "Normal Map"):
    """Tangent normal Color → (optional NormalFlipper) → Normal Map. Returns Normal socket."""
    nm_in = color_sock
    if utils.ensure_node_group("NormalFlipper"):
        flipper = nodes.new("ShaderNodeGroup")
        flipper.node_tree = bpy.data.node_groups["NormalFlipper"]
        flipper.location = (loc[0] - 180, loc[1])
        links.new(color_sock, flipper.inputs[0])
        nm_in = flipper.outputs[0]
    nm = nodes.new("ShaderNodeNormalMap")
    nm.label = label
    nm.location = loc
    try:
        nm.convention = "DIRECTX"
    except Exception:
        pass
    try:
        nm.inputs["Strength"].default_value = max(float(strength), 0.0)
    except Exception:
        pass
    links.new(nm_in, nm.inputs["Color"])
    return nm.outputs["Normal"]



def _noh_ao_from_tex(nodes, links, noh_node, loc):
    """NOH packing: RG = normal XY, B ≈ unused/Z pad, A = occlusion/height.

    Pioneer ``T_*_NOH`` PNGs carry meaningful variation in **Alpha** (mean ~0.45
    on painted metal) while Blue is nearly flat (~0.96). Using Blue as AO left
    surfaces un-occluded; route Alpha into the AO socket instead.

    Do **not** use this for ``*_NAO`` sheets — those pack AO in Blue and opacity
    in Alpha (see :func:`_nao_from_tex`).
    """
    alpha = noh_node.outputs.get("Alpha")
    if alpha is not None:
        return noh_node.outputs["Color"], alpha
    sep = nodes.new("ShaderNodeSeparateColor")
    sep.label = "NOH Channels"
    sep.location = loc
    links.new(noh_node.outputs["Color"], sep.inputs["Color"])
    return noh_node.outputs["Color"], sep.outputs["Blue"]


def _nao_from_tex(nodes, links, nao_node, loc):
    """NAO packing (Architecture trim / enemy decals): RG=normal, B=AO, A=opacity.

    Returns ``(normal_color_sock, ao_value_sock, alpha_sock)``.
    Normal feeds RG with Z forced to 1 — never pipe the full Color (or Alpha)
    into a gray Combine for AO; that was the ConcreteTrim bug.
    """
    sep = nodes.new("ShaderNodeSeparateColor")
    sep.label = "NAO Channels"
    sep.location = loc
    links.new(nao_node.outputs["Color"], sep.inputs["Color"])

    combine = nodes.new("ShaderNodeCombineColor")
    combine.label = "Normal RG + Z=1"
    combine.location = (loc[0] + 220, loc[1] + 80)
    links.new(sep.outputs["Red"], combine.inputs["Red"])
    links.new(sep.outputs["Green"], combine.inputs["Green"])
    combine.inputs["Blue"].default_value = 1.0

    alpha = nao_node.outputs.get("Alpha")
    return combine.outputs["Color"], sep.outputs["Blue"], alpha



def _mix_float(nodes, links, a, b, fac, loc, label: str):
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "FLOAT"
    mix.blend_type = "MIX"
    mix.label = label
    mix.location = loc
    if hasattr(fac, "links") or hasattr(fac, "node"):
        links.new(fac, mix.inputs["Factor"])
    else:
        mix.inputs["Factor"].default_value = float(fac)
    links.new(a, mix.inputs[2])
    links.new(b, mix.inputs[3])
    return mix.outputs[0]



def _mix_rgba(nodes, links, a, b, fac, loc, label: str, blend: str = "MIX"):
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = blend
    mix.label = label
    mix.location = loc
    if hasattr(fac, "links") or hasattr(fac, "node"):
        links.new(fac, mix.inputs["Factor"])
    else:
        mix.inputs["Factor"].default_value = float(fac)
    links.new(a, mix.inputs[6])
    links.new(b, mix.inputs[7])
    return mix.outputs[2]



def _mix_normals_vec(nodes, links, a, b, fac, loc, label: str):
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "VECTOR"
    mix.blend_type = "MIX"
    mix.label = label
    mix.location = loc
    if hasattr(fac, "links") or hasattr(fac, "node"):
        links.new(fac, mix.inputs["Factor"])
    else:
        mix.inputs["Factor"].default_value = float(fac)
    links.new(a, mix.inputs[4])
    links.new(b, mix.inputs[5])
    return mix.outputs[1]



def _contrast_mask(nodes, links, value_sock, low: float, high: float, loc, label: str):
    """MapRange contrast for blend / overlay masks (UE Mask High/Low)."""
    ramp = nodes.new("ShaderNodeMapRange")
    ramp.label = label
    ramp.location = loc
    ramp.clamp = True
    lo, hi = float(low), float(high)
    if hi <= lo:
        hi = lo + 0.001
    ramp.inputs["From Min"].default_value = lo
    ramp.inputs["From Max"].default_value = hi
    ramp.inputs["To Min"].default_value = 0.0
    ramp.inputs["To Max"].default_value = 1.0
    links.new(value_sock, ramp.inputs["Value"])
    return ramp.outputs["Result"]



def _mask_channel_value(nodes, links, mask_node, loc, prefer: str = "Red"):
    """GRM / X / grayscale masks → single float. Prefer Red, else luminance via Separate."""
    sep = nodes.new("ShaderNodeSeparateColor")
    sep.label = "Mask Channels"
    sep.location = loc
    links.new(mask_node.outputs["Color"], sep.inputs["Color"])
    channel = prefer if prefer in sep.outputs else "Red"
    return sep.outputs[channel]



def _dump_unconnected_tex(nodes, tex_lookup, handled_paths, loc_x, loc_y):
    """Leave unused MI textures as Image nodes for inspection."""
    row_u = loc_y
    for param, (fpath, img) in tex_lookup.items():
        if fpath in handled_paths:
            continue
        if param.startswith("T_") and any(
            param.endswith(suf) for suf in (
                "_CR", "_NOH", "_C", "_A", "_X", "_GRM", "_NOM", "_NXX", "_NMX",
                "_CA", "_CS", "_NTR", "_NX", "_EXX",
                "_Color", "_Normal", "_Roughnessmetal", "_Tintmask",
            )
        ):
            continue
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = param
        node.interpolation = "Cubic"
        node.location = (loc_x, row_u)
        row_u -= 280
    return row_u



# ---------------------------------------------------------------------------
# Foliage texture inference (empty / unresolved MI maps)
# ---------------------------------------------------------------------------

def _stem_tokens(name: str) -> set[str]:
    """Split asset stem into comparable tokens (StonePine_01_Branches → …)."""
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    stem = re.sub(r"^(mi_|m_|sm_|sk_|t_)", "", stem, flags=re.I)
    parts = [p for p in re.split(r"[_\-\s]+", stem.lower()) if p and p not in (
        "01", "02", "03", "04", "a", "b", "c", "d", "south", "north", "east",
        "west", "forest", "the", "of",
    )]
    # Also keep compound plant tokens glued (stonepine → pine)
    extras = set()
    for p in parts:
        for key in ("pine", "oak", "birch", "cypress", "juniper", "aleppo",
                    "heather", "reed", "vine", "leaf", "leaves", "branch",
                    "branches", "trunk", "grass", "bush", "tree"):
            if key in p and key != p:
                extras.add(key)
    return set(parts) | extras



def _list_image_files(folder: str) -> list[tuple[str, str]]:
    """Non-recursive [(stem_lower, abs_path), ...] for image textures in folder."""
    out = []
    if not folder or not os.path.isdir(folder):
        return out
    try:
        for fname in os.listdir(folder):
            low = fname.lower()
            if not low.startswith("t_"):
                continue
            if not any(low.endswith(ext) for ext in _TEX_IMAGE_EXTS):
                continue
            stem = os.path.splitext(fname)[0]
            out.append((stem.lower(), os.path.join(folder, fname)))
    except OSError:
        pass
    return out



def _load_fallback_tex_image(fpath: str):
    """Load via session image cache; returns Blender image or None."""
    if not fpath or not os.path.isfile(fpath):
        return None
    return _load_image_cached(fpath)



def _stamp_tex_fallback(mat, role: str, fpath: str, reason: str = ""):
    """Stamp custom props when a fallback texture was wired."""
    try:
        mat["arc_tex_fallback"] = True
        mat[f"arc_tex_fallback_{role}"] = fpath or ""
        if reason:
            mat[f"arc_tex_fallback_{role}_src"] = reason
    except Exception:
        pass



def _avg_rgb(samples: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    if not samples:
        return (0.5, 0.5, 0.5)
    n = float(len(samples))
    return (
        sum(s[0] for s in samples) / n,
        sum(s[1] for s in samples) / n,
        sum(s[2] for s in samples) / n,
    )



def _rgb_node(nodes, rgb: tuple[float, float, float], label: str, loc):
    node = nodes.new("ShaderNodeRGB")
    node.label = label
    node.location = loc
    r, g, b = float(rgb[0]), float(rgb[1]), float(rgb[2])
    node.outputs[0].default_value = (r, g, b, 1.0)
    return node.outputs[0]



def _invert_mask(nodes, links, value_sock, loc, label: str = "Invert Mask"):
    """1 - value for dark→1 luminance masks."""
    node = nodes.new("ShaderNodeMath")
    node.operation = "SUBTRACT"
    node.label = label
    node.location = loc
    node.inputs[0].default_value = 1.0
    links.new(value_sock, node.inputs[1])
    return node.outputs["Value"]



def _ensure_mesh_material_slot_count(obj, count: int) -> int:
    """Grow ``obj.data.materials`` so index-aligned SM/SK slots can be assigned.

    Tiny UEModel planes (e.g. SM_WaterPlane) often import with zero slots; Stage 2
    previously matched nothing and left the mesh unassigned / Principled-white.
    """
    if not obj or obj.type != "MESH" or not obj.data or count <= 0:
        return 0
    mesh = obj.data
    added = 0
    while len(mesh.materials) < count:
        mesh.materials.append(None)
        added += 1
    return added



def _principled_base_color_info(mat) -> tuple[tuple[float, float, float] | None, bool]:
    """Return (rgb or None, has_image_tex_linked_to_base_color)."""
    if mat is None or not getattr(mat, "use_nodes", False) or not mat.node_tree:
        return None, False
    for node in mat.node_tree.nodes:
        if node.type != "BSDF_PRINCIPLED":
            continue
        sock = node.inputs.get("Base Color")
        if sock is None:
            return None, False
        linked_tex = False
        for link in sock.links:
            from_node = link.from_node
            if from_node is None:
                continue
            if from_node.type == "TEX_IMAGE" and getattr(from_node, "image", None):
                linked_tex = True
                break
            # Follow one Mix/RGB hop for water shore mixes etc.
            if from_node.type in {"MIX", "MIX_RGB", "RGB", "GAMMA", "HUE_SAT"}:
                for inp in getattr(from_node, "inputs", []) or []:
                    for ln in getattr(inp, "links", []) or []:
                        src = ln.from_node
                        if src is not None and src.type == "TEX_IMAGE" and getattr(src, "image", None):
                            linked_tex = True
                            break
                    if linked_tex:
                        break
        try:
            dv = sock.default_value
            rgb = (float(dv[0]), float(dv[1]), float(dv[2]))
        except Exception:
            rgb = None
        return rgb, linked_tex
    return None, False



def _is_default_white_rgb(rgb, tol: float = 0.06) -> bool:
    if not rgb or len(rgb) < 3:
        return False
    r, g, b = float(rgb[0]), float(rgb[1]), float(rgb[2])
    # Blender Principled default ≈ 0.8; pure white 1,1,1; near-greys without tex
    if abs(r - 1.0) <= tol and abs(g - 1.0) <= tol and abs(b - 1.0) <= tol:
        return True
    if abs(r - 0.8) <= tol and abs(g - 0.8) <= tol and abs(b - 0.8) <= tol:
        return True
    return False



def _names_suggest_fur(names) -> bool:
    for raw in names or ():
        if textures.name_suggests_fur(raw):
            return True
    return False



def _slot_is_fur(names, mi_path: str = "") -> bool:
    if _names_suggest_fur(names):
        return True
    if mi_path and textures.is_fur_mi(mi_path):
        return True
    return False



def _remove_named_modifiers(obj, names) -> None:
    want = {str(n) for n in (names or ())}
    for mod in list(obj.modifiers):
        if mod.name in want:
            try:
                obj.modifiers.remove(mod)
            except Exception:
                pass



def _find_glass_mi_in_dir(folder: str) -> str:
    """Glass MI JSON beside an explicit skin JSON, e.g. MI_Goalie_Visor_Glass_Gold.json.

    Prefers opaque gameplay glass over FrontEnd variants when both sit in the same folder.
    """
    from .clothing import _is_visor_glass_mi, _parse_visor_mi
    if not folder or not os.path.isdir(folder):
        return ""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return ""
    opaque = ""
    frontend = ""
    for fname in names:
        if not fname.lower().endswith(".json") or "glass" not in fname.lower():
            continue
        candidate = os.path.join(folder, fname)
        if not _is_visor_glass_mi(_parse_visor_mi(candidate)):
            continue
        if "frontend" in fname.lower():
            if not frontend:
                frontend = candidate
        elif not opaque:
            opaque = candidate
    return opaque or frontend



def _is_simple_pbr_material_name(name: str) -> bool:
    """True for the UE parent ``M_SimplePBR`` only (not ``MI_SimplePBR_*`` props).

    Helmet / visor glass sections often ship with this parent as a placeholder while the
    authored glass instance lives beside the mesh as ``MI_*_Glass*.json``.
    """
    raw = (name or "").strip()
    if not raw:
        return False
    # Material'M_SimplePBR' / ObjectPath leaves
    if "'" in raw:
        parts = [p for p in raw.split("'") if p]
        raw = parts[-1] if parts else raw
    stem = os.path.splitext(os.path.basename(raw.replace("\\", "/")))[0]
    return stem.lower() in {"m_simplepbr", "simplepbr"}



def _normalize_mat_stem(name: str) -> str:
    """Basename stem from a slot / MI / ObjectPath-ish label."""
    n = (name or "").strip()
    if not n:
        return ""
    if "'" in n:
        parts = [p for p in n.split("'") if p]
        n = parts[-1] if parts else n
    return os.path.splitext(os.path.basename(n.replace("\\", "/")))[0].lower()



def _name_has_glass_lens_token(name: str) -> bool:
    """True for lens tokens (``*Glass*``, ``Visor_Glass``) — not ``glasses`` / shells.

    ``glasses`` / ``sunglasses`` contain the substring ``glass`` but name the frame
    mesh/MI, not the transparent lens material.
    """
    n = (name or "").strip().lower()
    if not n or "glass" not in n:
        return False
    stripped = n.replace("sunglasses", "").replace("glasses", "")
    return "glass" in stripped



def _psk_is_helmet_part(psk_path: str) -> bool:
    """True when the mesh path/filename is a helmet part (not nightvision / misc)."""
    norm = (psk_path or "").replace("\\", "/").lower()
    if not norm:
        return False
    base = os.path.basename(norm)
    if "nightvision" in norm or "night_vision" in norm:
        return False
    return (
        "/helmet/" in norm
        or "/helmets/" in norm
        or "helmetvisor" in norm
        or base.startswith("sk_") and "helmet" in base
    )



def _psk_is_headgear_part(psk_path: str) -> bool:
    """True when the mesh path/filename is Headgear / Headgear_Outer (etc.)."""
    norm = (psk_path or "").replace("\\", "/").lower()
    if not norm:
        return False
    base = os.path.basename(norm)
    return (
        "/headgear" in norm
        or (base.startswith("sk_") and "headgear" in base)
    )



def _psk_is_goggles_part(psk_path: str) -> bool:
    """True when the mesh path/filename is a Goggles / Glasses clothing part."""
    norm = (psk_path or "").replace("\\", "/").lower()
    if not norm:
        return False
    base = os.path.basename(norm)
    return (
        "/goggles/" in norm
        or "/glasses/" in norm
        or (base.startswith("sk_") and ("goggles" in base or "glasses" in base))
    )



def _psk_is_ocm_glass_hybrid_part(psk_path: str) -> bool:
    """Visor / goggles / helmet / headgear parts that may use OCM Color N for glass."""
    from .clothing import _psk_is_visor_part
    return (
        _psk_is_visor_part(psk_path)
        or _psk_is_goggles_part(psk_path)
        or _psk_is_helmet_part(psk_path)
        or _psk_is_headgear_part(psk_path)
    )



def _glass_slot_prefers_arc_override(
    psk_path: str = "", mi_path: str = "", skin_json: str = ""
) -> bool:
    """Glass / lens slots keep ArcTexturer when clothing maps exist; Visor is Override only."""
    from .clothing import _clothing_shell_maps_available
    return _clothing_shell_maps_available(psk_path, mi_path=mi_path, skin_json=skin_json)



def _object_has_matchable_lens_slot(obj, psk_path: str = "") -> bool:
    """True when a glass/lens SK slot maps onto a real Blender material slot."""
    from .clothing import _is_visor_shell_slot_name, _slot_is_visor_lens
    if obj is None or not getattr(obj, "material_slots", None):
        return False
    try:
        sk_slots = _parse_sk_material_slots(psk_path) if psk_path else []
    except Exception:
        sk_slots = []
    if not sk_slots:
        sk_slots = [
            ((s.material.name if s.material else ""), "", "")
            for s in obj.material_slots
        ]
    used = set()
    for i, (sk_name, mi_stem, mi_json) in enumerate(sk_slots):
        target, index = _match_material_slot(obj, sk_name, i, used, mi_stem)
        if target is None:
            continue
        mat_name = target.material.name if target.material else ""
        names = (sk_name, mi_stem, mat_name)
        mi_path = mi_json if mi_json and os.path.isfile(mi_json) else ""
        if any(_is_visor_shell_slot_name(n) for n in names) and not any(
            _name_has_glass_lens_token(n or "") for n in names
        ):
            continue
        if _slot_is_visor_lens(names, psk_path, mi_path):
            return True
        used.add(index)
    return False



def _ensure_arc_on_glass_slot(obj, target, index: int, psk_path: str = "",
                              mi_path: str = "", skin_json: str = ""):
    """Ensure a glass/lens slot owns an ArcTexturer material (never Visor-only).

    Prefers the existing Arc datablock on the clothing shell (copy per colorway).
    Returns the material assigned to *target*, or None if Arc cannot be sourced.
    """
    from .clothing import _find_arc_texturer_group_on_material, _find_clothing_shell_slot_index, _visor_unique_mat_name
    if target is None:
        return None
    current = target.material
    if current is not None and _find_arc_texturer_group_on_material(current) is not None:
        name = _visor_unique_mat_name(obj, index, mi_path=mi_path, skin_json=skin_json)
        if current.name != name and not current.name.startswith(name + "."):
            shared = (
                current.users > 1
                or _material_used_by_other_objects(current, obj)
                or sum(1 for s in obj.material_slots if s.material == current) > 1
            )
            if shared:
                mat = current.copy()
                try:
                    mat.name = name
                except Exception:
                    pass
                target.material = mat
                return mat
            try:
                current.name = name
            except Exception:
                pass
        return current

    shell_idx = _find_clothing_shell_slot_index(obj, psk_path)
    shell_mat = None
    if 0 <= shell_idx < len(obj.material_slots) and shell_idx != index:
        shell_mat = obj.material_slots[shell_idx].material
    if shell_mat is None or _find_arc_texturer_group_on_material(shell_mat) is None:
        # Single-slot glass: clothing path should already have put Arc on slot 0.
        for i, slot in enumerate(obj.material_slots):
            if i == index or slot.material is None:
                continue
            if _find_arc_texturer_group_on_material(slot.material) is not None:
                shell_mat = slot.material
                break
    if shell_mat is None or _find_arc_texturer_group_on_material(shell_mat) is None:
        return None

    name = _visor_unique_mat_name(obj, index, mi_path=mi_path, skin_json=skin_json)
    mat = shell_mat.copy()
    try:
        mat.name = name
    except Exception:
        pass
    target.material = mat
    return mat



def _material_used_by_other_objects(mat, obj) -> bool:
    """True when *mat* is assigned on any mesh other than *obj*."""
    if mat is None:
        return False
    for other in bpy.data.objects:
        if other == obj or getattr(other, "type", None) != "MESH":
            continue
        for slot in other.material_slots:
            if slot.material == mat:
                return True
    return False

