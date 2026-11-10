"""Body / head / hair discovery and import for the Outfit sidebar.

Browses packaged appearance metadata. Asset paths resolve only during actions.
UI enums live on the scene; import can stand alone
or merge onto an existing outfit armature.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from typing import Iterable

import bpy

from ..Shared import importing
from ..Animations import rig
from ..Shared import utils

_CACHE: dict = {}
_PATH_BY_ID: dict[str, str] = {}
_CHOICE_BY_ID: dict[str, dict] = {}
_APPEARANCE_CSV_CACHE: dict = {}
_PREVIEW_COLLECTION = None
_PREVIEW_FAILED: set[str] = set()
_PREVIEW_PENDING: dict[str, str] = {}
_ICON_SCAN_CACHE: dict = {}
_OUTFIT_PREVIEW_JOBS: dict = {}
_OUTFIT_PREVIEW_READY: dict = {}
_OUTFIT_PREVIEW_ATTEMPTED: set = set()
_OUTFIT_PREVIEW_EXECUTOR = None

# ActorX preferred; UEModel is what most hair folders actually ship today.
_MESH_EXTS = (".psk", ".pskx", ".uemodel")
_MESH_PREF = (".psk", ".pskx", ".uemodel")

_GEAR_KINDS = ("backpack", "attachment", "charm", "raider_tool")
_GEAR_ROLE = {
    "backpack": "Backpack",
    "attachment": "Attachment",
    "charm": "Charm",
    "raider_tool": "RaiderTool",
}
_GEAR_SCENE_PROP = {
    "backpack": "arc_appearance_backpack",
    "attachment": "arc_appearance_attachment",
    "charm": "arc_appearance_charm",
    "raider_tool": "arc_appearance_raider_tool",
}

# Default rack under Characters/Backpacks/Frames — containers hang on slot_container
# off backpackframe (see SK_Default_Strap REFSKELT). Frame mesh → slot_frame.
_BACKPACK_FRAME_RELS = (
    "Characters/Backpacks/Frames/Default/SM_Backpack_Frame_01_A.pskx",
    "Characters/Backpacks/Frames/Default/SM_Backpack_Frame_01_A.psk",
)
_BACKPACK_STRAP_RELS = (
    "Characters/Backpacks/Straps/Default/Strap/SK_Default_Strap.psk",
    "Characters/Backpacks/Straps/Default/Strap/SK_Default_Strap.pskx",
)
_BACKPACK_CHARM_CONNECTOR_RELS = (
    "Characters/Backpacks/Charms/Karabiner/SM_BackpackCharm_Karabiner.pskx",
    "Characters/Backpacks/Charms/Karabiner/SM_BackpackCharm_Karabiner.psk",
)
# Gear role → attach bone on the player (after strap bones are merged in).
_GEAR_ATTACH_BONE = {
    "Backpack": "slot_container",
    "BackpackFrame": "slot_frame",
    "Attachment": "slot_backpackattachment",
    "Charm": "slot_backpackcharm",
    "CharmConnector": "slot_backpackcharm",
    # Strap skeleton bone (SK_Default_Strap name map), not a backpack slot.
    "RaiderTool": "ToolStowSlot_1",
}
_BACKPACK_SLOT_BONES = (
    "backpackframe",
    "slot_frame",
    "slot_container",
    "slot_backpackattachment",
    "slot_backpackcharm",
    "ToolStowSlot_1",
)
# Dump string tables. Folder tokens are matched with the digits kept so
# Gas Canister and Pressurizer stay distinct. A missing row falls back to
# dropping a trailing " 01" when that cleaned name is unique.
_FLAVOUR_REL = {
    "attachment": "UI/Localization/ST_Character_BackpackAttachments.json",
    "charm": "UI/Localization/ST_Customization_Charms.json",
    "raider_tool": "UI/Localization/ST_Customization_RaiderToolSkins.json",
    "backpack": "UI/Localization/ST_Customization_Backpacks.json",
}
_FLAVOUR_PREFIXES = (
    "ID_CHARACTER_BACKPACKATTACHMENTS_",
    "ID_CHARACTER_BACKPACKATTACHMENT_",
    "ID_CUSTOMIZATION_CHARM_",
    "ID_RAIDERTOOLCOSMETIC_",
    "ID_CUSTOMIZATION_BACKPACK_",
)


def invalidate_cache() -> None:
    _CACHE.clear()
    _PATH_BY_ID.clear()
    _CHOICE_BY_ID.clear()
    _APPEARANCE_CSV_CACHE.clear()
    _ICON_SCAN_CACHE.clear()


def default_appearance_csv_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Data/appearance_reference.csv')


def get_appearance_csv_path(context=None) -> str:
    custom = ""
    try:
        scene = context.scene if context is not None else bpy.context.scene
        custom = getattr(scene, "arc_appearance_csv_path", "") or ""
    except Exception:
        custom = ""
    if custom:
        abspath = bpy.path.abspath(custom)
        if os.path.isfile(abspath):
            return abspath
    default = default_appearance_csv_path()
    return default if os.path.isfile(default) else ""


def load_appearance_csv(csv_path: str = "") -> list[dict]:
    path = csv_path or default_appearance_csv_path()
    if not path or not os.path.isfile(path):
        return []
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    cached = _APPEARANCE_CSV_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    rows = []
    try:
        with open(path, "r", newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    except OSError:
        rows = []
    _APPEARANCE_CSV_CACHE[path] = (mtime, rows)
    return rows


def _pioneer_content_root() -> str:
    """Absolute Content/Pioneer folder when the known layout is present."""
    root = utils.get_pioneer_root() or ""
    if not root:
        return ""
    content = utils.find_content_dir(root)
    if content:
        pioneer = os.path.join(content, "Pioneer")
        if os.path.isdir(pioneer):
            return pioneer
    for candidate in (
        os.path.join(root, "PioneerGame", "Content", "Pioneer"),
        os.path.join(root, "Content", "Pioneer"),
        root if os.path.basename(os.path.normpath(root)).casefold() == "pioneer" else "",
    ):
        if candidate and os.path.isdir(candidate):
            return candidate
    return ""


def _resolve_csv_rel_path(rel_path: str) -> str:
    rel = (rel_path or "").replace("\\", "/").strip()
    if not rel:
        return ""
    pioneer = _pioneer_content_root()
    if pioneer:
        candidate = os.path.join(pioneer, *rel.split("/"))
        if os.path.isfile(candidate):
            return candidate
    root = utils.get_pioneer_root() or ""
    if root:
        candidate = os.path.join(root, *rel.split("/"))
        if os.path.isfile(candidate):
            return candidate
    return _pak_cache_rel_path(rel)


def _pak_cache_rel_path(rel: str) -> str:
    """Resolve an exact cached package across mount aliases, including long paths."""
    try:
        from ..Shared import pak_extract; from . import pak_catalog
    except Exception:
        return ""
    extension = os.path.splitext(rel or "")[1]
    if "t_ui_icon" in (rel or "").casefold():
        try:
            pak_extract.ensure_previews()
        except Exception:
            pass
    cache = pak_extract.cache_dir()
    candidates = []
    for package in pak_extract._package_aliases(pak_extract.game_path(rel)):
        candidate = pak_catalog.filesystem_path(os.path.join(cache, *package.split("/")) + extension)
        if os.path.isfile(candidate):
            candidates.append(candidate)
    return max(candidates, key=lambda path: os.stat(path).st_mtime_ns) if candidates else ""


def _alnum_token(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", text or "").upper()


def _strip_variant_a(token: str) -> str:
    """Drop one trailing A that marks the mesh variant (BUCKET01A), not a word."""
    if len(token) > 2 and token.endswith("A") and token[-2].isdigit():
        return token[:-1]
    return token


def _one_insert_apart(left: str, right: str) -> bool:
    """True when the longer token is the shorter one plus one character."""
    if abs(len(left) - len(right)) != 1:
        return False
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    index = 0
    while index < len(shorter) and shorter[index] == longer[index]:
        index += 1
    return shorter[index:] == longer[index + 1 :]


def _strip_trailing_digits(token: str) -> str:
    return re.sub(r"\d+$", "", token or "")


def _appearance_browse_manifest() -> dict:
    key = "appearance_browse_manifest"
    if key not in _CACHE:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "appearance_browse.json")
        try:
            with open(path, encoding="utf-8") as stream:
                data = json.load(stream)
            _CACHE[key] = data if data.get("schema") == 1 else {}
        except (OSError, ValueError, AttributeError):
            _CACHE[key] = {}
    return _CACHE[key]

def _flavour_entries(json_path: str) -> dict:
    try:
        with open(json_path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    exports = data.get("Exports") if isinstance(data, dict) else data
    if isinstance(data, dict) and not exports:
        exports = [data]
    table = {}
    for entry in exports or []:
        if not isinstance(entry, dict):
            continue
        pairs = ((entry.get("StringTable") or {}).get("KeysToEntries") or {})
        if not isinstance(pairs, dict):
            continue
        for key, value in pairs.items():
            name = str(value or "").strip()
            if not name or str(key).upper().endswith("_ITEMCATEGORY"):
                continue
            raw_key = str(key).upper()
            for prefix in _FLAVOUR_PREFIXES:
                if raw_key.startswith(prefix):
                    raw_key = raw_key[len(prefix):]
                    break
            token = _strip_variant_a(_alnum_token(raw_key))
            if token and token not in table:
                table[token] = name
    return table


def _flavour_table(kind: str) -> dict:
    return _appearance_browse_manifest().get("flavour_tables", {}).get((kind or "").casefold(), {})

def _flavour_name(kind: str, folder: str, icon_stem: str) -> str:
    table = _flavour_table(kind)
    if not table:
        return ""
    exact = []
    bare = []
    for raw in (folder, icon_stem):
        token = _strip_variant_a(_alnum_token(raw))
        if not token:
            continue
        exact.append(token)
        stripped = _strip_trailing_digits(token)
        if stripped and stripped != token:
            bare.append(stripped)
    for token in exact:
        if token in table:
            return table[token]
    for token in bare:
        if token in table:
            return table[token]
    # HoseBunde vs HoseBundle: a short prefix, or one inserted letter.
    near = []
    needles = bare or exact
    for token in needles:
        if len(token) < 5:
            continue
        for key, name in table.items():
            if abs(len(key) - len(token)) > 2:
                continue
            if key.startswith(token) or token.startswith(key) or _one_insert_apart(token, key):
                near.append(name)
    near = list(dict.fromkeys(near))
    if len(near) == 1:
        return near[0]
    return ""


def _strip_index_label(label: str) -> str:
    """Drop a trailing variant index. Leaves a leading number (10 Mil Tool)."""
    text = (label or "").strip()
    cleaned = re.sub(r"(?:\s+\d+)+(?:\s+A)?$", "", text).strip()
    cleaned = re.sub(r"\s+A$", "", cleaned).strip()
    return cleaned


def _apply_gear_display_names(kind: str, choices: list[dict]) -> None:
    """Prefer the string-table flavour. Otherwise drop a unique trailing index."""
    pending = []
    for choice in choices:
        flavour = _flavour_name(
            kind,
            choice.get("folder") or "",
            choice.get("icon_stem") or "",
        )
        cleaned = _strip_index_label(choice.get("label") or "")
        pending.append((choice, flavour, cleaned))
    counts: dict[str, int] = {}
    for _choice, flavour, cleaned in pending:
        if flavour or not cleaned:
            continue
        counts[cleaned.casefold()] = counts.get(cleaned.casefold(), 0) + 1
    for choice, flavour, cleaned in pending:
        if flavour:
            choice["label"] = flavour
            _register_choice(choice)
            continue
        original = (choice.get("label") or "").strip()
        if cleaned and cleaned != original and counts.get(cleaned.casefold(), 0) == 1:
            choice["label"] = cleaned
            _register_choice(choice)


def _choices_from_csv(kind: str) -> list[dict]:
    """Return packaged choices without resolving action-time asset paths."""
    rows = load_appearance_csv()
    result = []
    for row in rows:
        if (row.get("kind") or "").strip().casefold() != kind.casefold():
            continue
        key = (row.get("key") or "").strip()
        cid = (row.get("id") or "").strip() or _choice_id(f"{kind}|{key}")
        rel = (row.get("rel_path") or "").strip()
        path = ""
        has_mesh = str(row.get("has_mesh") or "0").strip() in ("1", "true", "True", "yes")
        label = (row.get("label") or key or cid).strip()
        choice = _register_choice({
            "id": cid,
            "label": label,
            "path": path,
            "folder": (row.get("folder") or "").strip(),
            "has_mesh": has_mesh,
            "segment": (row.get("segment") or "").strip(),
            "kind": kind,
            "icon_stem": (row.get("icon_stem") or row.get("folder") or "").strip(),
            "rel_path": rel,
            "key": key,
        })
        result.append(choice)
    if kind.casefold() in _GEAR_KINDS:
        _apply_gear_display_names(kind, result)
    return result


# ---------------------------------------------------------------------------
# Preview icons (T_UI_Icon_Face_* / T_UI_Icon_Hair_* / T_UI_Icon_FacialHair_*)
# ---------------------------------------------------------------------------

def register_previews() -> None:
    global _PREVIEW_COLLECTION
    if _PREVIEW_COLLECTION is not None:
        return
    try:
        import bpy.utils.previews as _previews

        _PREVIEW_COLLECTION = _previews.new()
    except Exception as exc:
        print(f"Arc Raiders: appearance preview collection unavailable: {exc}")
        _PREVIEW_COLLECTION = None


def unregister_previews() -> None:
    global _PREVIEW_COLLECTION, _OUTFIT_PREVIEW_EXECUTOR
    if bpy.app.timers.is_registered(_prepare_sidebar_previews):
        bpy.app.timers.unregister(_prepare_sidebar_previews)
    _PREVIEW_PENDING.clear()
    for timer, future in _OUTFIT_PREVIEW_JOBS.values():
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
        if future is not None:
            future.cancel()
    _OUTFIT_PREVIEW_JOBS.clear()
    _OUTFIT_PREVIEW_READY.clear()
    _OUTFIT_PREVIEW_ATTEMPTED.clear()
    if _OUTFIT_PREVIEW_EXECUTOR is not None:
        _OUTFIT_PREVIEW_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        _OUTFIT_PREVIEW_EXECUTOR = None
    if _PREVIEW_COLLECTION is None:
        return
    try:
        import bpy.utils.previews as _previews

        _previews.remove(_PREVIEW_COLLECTION)
    except Exception:
        pass
    _PREVIEW_COLLECTION = None
    _PREVIEW_FAILED.clear()


def _gear_icon_spec(choice: dict) -> tuple[str, str]:
    """Return (skin folder, icon filename prefix) for a sidebar choice."""
    kind = (choice.get("kind") or "").casefold()
    segment = (choice.get("segment") or "").casefold()
    if kind == "head" or segment == "heads":
        return "Face", "Face"
    if segment in ("facialhairs", "facialhair"):
        return "FacialHair", "FacialHair"
    if kind == "hair" or segment == "hairs":
        return "Hair", "Hair"
    if kind == "backpack" or segment == "backpackcontainer":
        return "BackpackContainer", "BackpackContainer"
    if kind == "attachment" or segment == "backpackattachment":
        return "BackpackAttachment", "BackpackAttachment"
    if kind == "charm" or segment == "backpackcharm":
        return "BackpackCharm", "BackpackCharm"
    if kind == "raider_tool" or segment == "raidertool":
        return "RaiderTool", "RaiderTool"
    return "", ""


def _packaged_appearance_preview(stem: str) -> str:
    """AVIF icon under ``assets/ui_icons/appearance`` named by its game texture stem."""
    if not stem or os.path.basename(stem) != stem or "/" in stem or "\\" in stem:
        return ""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "ui_icons", "appearance", stem + ".avif")
    return path if os.path.isfile(path) else ""


def _browse_icon_file_for_choice(choice: dict) -> str:
    filename = _appearance_browse_manifest().get("icons", {}).get((choice or {}).get("id"), "")
    return _packaged_appearance_preview(filename)


def _icon_skin_dirs(segment: str) -> list[str]:
    if not segment:
        return []
    found = []
    pioneer = _pioneer_content_root()
    if pioneer:
        folder = os.path.join(pioneer, "Items", "Characters", "Skins", segment)
        if os.path.isdir(folder):
            found.append(folder)
    try:
        from ..Shared import pak_extract

        cache = os.path.join(
            pak_extract.cache_dir(),
            "PioneerGame", "Content", "Pioneer",
            "Items", "Characters", "Skins", segment,
        )
        if os.path.isdir(cache):
            found.append(cache)
    except Exception:
        pass
    return found


def _scan_gear_icon(segment: str, prefix: str, stems: list[str]) -> str:
    """Find T_UI_Icon_{prefix}_*.png when the CSV stem is missing the _A suffix."""
    want = []
    for stem in stems:
        token = _strip_variant_a(_alnum_token(stem))
        if token and token not in want:
            want.append(token)
        bare = _strip_trailing_digits(token)
        if bare and bare not in want and bare != token:
            want.append(bare)
    if not want:
        return ""
    key = (segment, prefix, tuple(want), utils.get_pioneer_root() or "")
    cached = _ICON_SCAN_CACHE.get(key)
    if cached is not None:
        return cached
    best = ""
    best_score = 0
    needle = f"t_ui_icon_{prefix.lower()}_"
    for folder in _icon_skin_dirs(segment):
        try:
            children = os.listdir(folder)
        except OSError:
            continue
        for child in children:
            png_dir = os.path.join(folder, child)
            if not os.path.isdir(png_dir):
                continue
            try:
                names = os.listdir(png_dir)
            except OSError:
                continue
            for name in names:
                low = name.casefold()
                if not low.endswith(".png") or not low.startswith(needle):
                    continue
                token = _strip_variant_a(_alnum_token(name[len(needle):-4]))
                score = 0
                for stem in want:
                    if token == stem:
                        score = 100
                    elif token == stem + "A" or stem == token + "A":
                        score = max(score, 90)
                    elif len(stem) >= 5 and abs(len(token) - len(stem)) <= 2 and (
                        token.startswith(stem)
                        or stem.startswith(token)
                        or _one_insert_apart(token, stem)
                    ):
                        score = max(score, 70)
                if score > best_score:
                    best_score = score
                    best = os.path.join(png_dir, name)
                    if score >= 100:
                        _ICON_SCAN_CACHE[key] = best
                        return best
    _ICON_SCAN_CACHE[key] = best
    return best


def _icon_file_for_choice(choice: dict) -> str:
    """Resolve source art during explicit reference-image actions only."""
    if not choice:
        return ""
    segment, prefix = _gear_icon_spec(choice)
    if not segment:
        return ""
    stems = []
    for raw in (choice.get("icon_stem"), choice.get("folder")):
        text = (raw or "").strip()
        if text and text not in stems:
            stems.append(text)
    if not stems:
        return ""
    for stem in stems:
        variants = [stem]
        if not stem.upper().endswith("_A"):
            variants.append(stem + "_A")
        for folder_name in variants:
            rel = (
                f"Items/Characters/Skins/{segment}/{folder_name}/"
                f"T_UI_Icon_{prefix}_{folder_name}.png"
            )
            found = _resolve_csv_rel_path(rel)
            if found:
                return found
    return _scan_gear_icon(segment, prefix, stems) or _browse_icon_file_for_choice(choice)


def _crop_sidebar_preview(preview):
    """Trim only the UI thumbnail; preserve source art and reference cards."""
    import numpy as np
    width, height = preview.image_size
    if not width or not height:
        return
    pixels = np.asarray(preview.image_pixels_float[:], dtype=np.float32).reshape(height, width, 4)
    visible = pixels[:, :, 3] > 1.0 / 255.0
    if np.all(visible):
        corners = pixels[[0, 0, -1, -1], [0, -1, 0, -1], :3]
        background = np.median(corners, axis=0)
        edge = np.concatenate((pixels[0, :, :3], pixels[-1, :, :3], pixels[:, 0, :3], pixels[:, -1, :3]))
        tolerance = 2.0 / 255.0
        if (np.max(np.abs(corners - background)) <= tolerance
                and np.mean(np.max(np.abs(edge - background), axis=1) <= tolerance) > 0.98):
            visible &= np.max(np.abs(pixels[:, :, :3] - background), axis=2) > tolerance
    ys, xs = np.nonzero(visible)
    if not len(xs):
        return
    # Keep the actual artwork rectangle. Re-padding it into a square adds
    # avoidable vertical margins to Blender's already-square icon widget.
    cropped = np.ascontiguousarray(pixels[ys.min():ys.max()+1, xs.min():xs.max()+1])
    h, w = cropped.shape[:2]
    preview.image_size = (w, h)
    preview.image_pixels_float = cropped.ravel()



def _preview_icon_id_for_path(path: str) -> int:
    """Return a cached icon; prepare missing images outside panel drawing."""
    if not path:
        return 0
    key = os.path.normcase(path)
    if key in _PREVIEW_FAILED:
        return 0
    if _PREVIEW_COLLECTION is not None and key in _PREVIEW_COLLECTION:
        try:
            return int(_PREVIEW_COLLECTION[key].icon_id)
        except Exception:
            _PREVIEW_FAILED.add(key)
            return 0
    _PREVIEW_PENDING[key] = path
    if not bpy.app.timers.is_registered(_prepare_sidebar_previews):
        bpy.app.timers.register(_prepare_sidebar_previews, first_interval=0.01)
    return 0


def _prepare_sidebar_previews():
    if not _PREVIEW_PENDING:
        return None
    key = next(iter(_PREVIEW_PENDING))
    path = _PREVIEW_PENDING.pop(key)
    try:
        register_previews()
        if _PREVIEW_COLLECTION is None or not os.path.isfile(path):
            _PREVIEW_FAILED.add(key)
        elif key not in _PREVIEW_COLLECTION:
            preview = _PREVIEW_COLLECTION.load(key, path, "IMAGE")
            _crop_sidebar_preview(preview)
    except Exception:
        _PREVIEW_FAILED.add(key)
    manager = getattr(bpy.context, "window_manager", None)
    for window in getattr(manager, "windows", ()):
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    return 0.01 if _PREVIEW_PENDING else None


def bald_preview_icon_id() -> int:
    """Default Hair preview when None / bald is selected."""
    bundled = _packaged_appearance_preview("T_UI_Icon_Hair_Bald")
    return _preview_icon_id_for_path(bundled)

def preview_icon_id(choice_id: str) -> int:
    """Custom icon id for the selected head/hair, or 0 if missing."""
    if not choice_id or choice_id == "NONE":
        return 0
    choice = resolve_choice(choice_id)
    if not choice:
        return 0
    return _preview_icon_id_for_path(_browse_icon_file_for_choice(choice))


def _shipped_outfit_preview_path(stem: str) -> str:
    """Full 512 px AVIF outfit icon under ``assets/ui_icons/appearance/``."""
    if not stem:
        return ""
    for prefix in ("T_UI_Icon_Outfit_", "T_UI_Icon_RoosterOutfit_"):
        path = _packaged_appearance_preview(prefix + stem)
        if path:
            return path
    return ""


def _outfit_preview_stems(row: dict) -> list[str]:
    """Candidate icon folder stems (Model folder first — matches T_UI_Icon_Outfit_*).

    Flavour / Item-UI names (e.g. Gearshifter) often differ from the mesh/icon
    folder (MotoRacer).
    """
    stems: list[str] = []
    for key in ("Model Folder Name", "Item/UI Folder Name"):
        raw = (row.get(key) or "").strip()
        for part in raw.split(";"):
            stem = part.strip()
            if stem and stem not in stems:
                stems.append(stem)
    return stems


def _queue_missing_outfit_preview(context, row, *, allow_extract=False):
    """Resolve retained selected artwork; extraction requires an explicit import."""
    scene = context.scene
    stems = tuple(stem for stem in _outfit_preview_stems(row) if re.fullmatch(r"[A-Za-z0-9_]+", stem))
    identity = row.get("ST") or ""
    if not stems or not identity:
        return ""
    settings = tuple(bpy.path.abspath(getattr(scene, name, "") or "") for name in ("arc_pioneer_root", "arc_pak_dir", "arc_usmap_path"))
    ready_key = (identity, stems, settings)
    key = (ready_key, bool(allow_extract))
    ready = _OUTFIT_PREVIEW_READY.get(ready_key, "")
    if ready or key in _OUTFIT_PREVIEW_ATTEMPTED:
        return ready
    _OUTFIT_PREVIEW_ATTEMPTED.add(key)

    def finish():
        global _OUTFIT_PREVIEW_EXECUTOR
        try:
            future = _OUTFIT_PREVIEW_JOBS[key][1]
            if future is None:
                from concurrent.futures import ThreadPoolExecutor
                from ..Shared import pak_extract
                from .outfit_preview import selected_game_icon
                cache = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ArcPak", "cache")
                pioneer_cache = os.path.join(cache, "PioneerGame", "Content", "Pioneer")
                source, game, mapping = settings
                roots = []
                if source:
                    roots.extend(os.path.join(source, suffix) for suffix in ("", "Pioneer", "Content/Pioneer", "PioneerGame/Content/Pioneer"))
                roots = tuple(dict.fromkeys(roots + [pioneer_cache]))
                command = ()
                if allow_extract and game:
                    mapping = mapping or pak_extract.bundled_usmap()
                    if mapping:
                        try:
                            command = tuple(pak_extract.extraction_command(game, mapping, [], out_dir=cache))
                        except FileNotFoundError:
                            command = ()
                if _OUTFIT_PREVIEW_EXECUTOR is None:
                    _OUTFIT_PREVIEW_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="outfit-preview")
                future = _OUTFIT_PREVIEW_EXECUTOR.submit(selected_game_icon, stems, identity, roots, pioneer_cache, command)
                _OUTFIT_PREVIEW_JOBS[key] = (finish, future)
                return 0.1
            if not future.done():
                return 0.1
            source = future.result()
            if source:
                _OUTFIT_PREVIEW_READY[ready_key] = source
                for window in bpy.context.window_manager.windows:
                    for area in window.screen.areas:
                        area.tag_redraw()
        except Exception as exc:
            print(f"Arc Raiders: selected outfit preview unavailable: {exc}")
        _OUTFIT_PREVIEW_JOBS.pop(key, None)
        return None

    _OUTFIT_PREVIEW_JOBS[key] = (finish, None)
    try:
        bpy.app.timers.register(finish, first_interval=0.01)
    except Exception:
        _OUTFIT_PREVIEW_JOBS.pop(key, None)
    return ready


def outfit_preview_icon_id(context) -> int:
    """Custom icon for the selected outfit row, or 0.

    Uses the shipped ``assets/ui_icons/appearance/T_UI_Icon_Outfit_{stem}.avif``.
    Otherwise the pak extraction result is shown as written.
    """
    scene = getattr(context, "scene", None)
    if scene is None:
        return 0
    key = (
        getattr(scene, "arc_selected_outfit", "")
        or getattr(scene, "arc_selected_outfit_browse", "")
        or ""
    )
    if not key or key == "NONE":
        return 0
    try:
        from ..Shared import importing as _importing

        csv_path = _importing.get_outfit_csv_path(context)
        rows = _importing.load_outfit_csv(csv_path)
        row = _importing.find_outfit_row(rows, key)
    except Exception:
        return 0
    if not row:
        return 0
    for stem in _outfit_preview_stems(row):
        bundled = _shipped_outfit_preview_path(stem)
        if bundled:
            return _preview_icon_id_for_path(bundled)
    return _preview_icon_id_for_path(_queue_missing_outfit_preview(context, row))


def _characters_root(pioneer_root: str = "") -> str:
    root = pioneer_root or utils.get_pioneer_root() or ""
    if not root:
        return ""
    # find_relative_dir joins Content/Pioneer + rel_parts.
    found = utils.find_relative_dir(root, ["Characters"])
    return found if found and os.path.isdir(found) else ""


def _humanize(name: str) -> str:
    text = (name or "").replace("_", " ").strip()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return text or name


def _choice_id(key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:16]
    return digest


def _register_choice(choice: dict) -> dict:
    cid = choice["id"]
    _CHOICE_BY_ID[cid] = choice
    path = choice.get("path") or ""
    if path and os.path.isfile(path):
        _PATH_BY_ID[cid] = os.path.normcase(os.path.abspath(path))
    return choice


def resolve_choice_path(choice_id: str) -> str:
    if not choice_id or choice_id == "NONE":
        return ""
    path = _PATH_BY_ID.get(choice_id) or ""
    if path and os.path.isfile(path):
        return path
    choice = resolve_choice(choice_id)
    path = _resolve_csv_rel_path((choice or {}).get("rel_path") or "")
    if path:
        choice["path"] = path
        _register_choice(choice)
    return path

def resolve_choice(choice_id: str) -> dict | None:
    if not choice_id or choice_id == "NONE":
        return None
    hit = _CHOICE_BY_ID.get(choice_id)
    if hit:
        return hit
    list_head_choices()
    list_hair_choices()
    list_hair_color_choices()
    for kind in _GEAR_KINDS:
        list_gear_choices(kind)
    return _CHOICE_BY_ID.get(choice_id)


def _iter_meshes(folder: str) -> Iterable[str]:
    if not folder or not os.path.isdir(folder):
        return
    for dirpath, _dirnames, filenames in os.walk(folder):
        # Skip colour / shared material banks under Hairs/Materials.
        parts = {p.casefold() for p in dirpath.replace("\\", "/").split("/")}
        if "materials" in parts and ("haircolors" in parts or "scalps" in parts or "scalpcolors" in parts):
            continue
        for name in filenames:
            low = name.casefold()
            if not any(low.endswith(ext) for ext in _MESH_EXTS):
                continue
            if low.endswith("_wraps.psk") or low.endswith("_wraps.pskx"):
                continue
            if low.endswith("_skeleton.uemodel") or low.endswith("_skeleton.psk"):
                continue
            if "_underhat" in low:
                continue
            yield os.path.join(dirpath, name)


def _exports_list(data) -> list:
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("Exports", "exports"):
        if isinstance(data.get(key), list):
            return [e for e in data[key] if isinstance(e, dict)]
    return [data] if data.get("Type") or data.get("Properties") else []


def _mesh_package_stem(mesh_ref) -> str:
    """Stem from DA Mesh SoftObjectPath / ObjectName."""
    if isinstance(mesh_ref, dict):
        name = str(mesh_ref.get("ObjectName") or "")
        path = str(mesh_ref.get("ObjectPath") or mesh_ref.get("AssetPathName") or "")
        if "'" in name:
            # SkeletalMesh'SK_AfroFade'
            name = name.split("'", 1)[1].rstrip("'")
        if name:
            return os.path.basename(name.replace("\\", "/"))
        if path:
            leaf = path.replace("\\", "/").split("/")[-1]
            return leaf.split(".", 1)[0]
    elif mesh_ref:
        leaf = str(mesh_ref).replace("\\", "/").split("/")[-1]
        return leaf.split(".", 1)[0]
    return ""


def _resolve_mesh_file(folder: str, stem: str) -> str:
    if not folder or not stem:
        return ""
    for ext in _MESH_PREF:
        candidate = os.path.join(folder, stem + ext)
        if os.path.isfile(candidate):
            return candidate
    # Nested skins (Buzzcut/Skins/Straight/SM_Buzzcut_Straight.*)
    stem_cf = stem.casefold()
    for dirpath, _dns, filenames in os.walk(folder):
        for name in filenames:
            low = name.casefold()
            base, ext = os.path.splitext(low)
            if ext not in _MESH_EXTS:
                continue
            if base == stem_cf or base.startswith(stem_cf + "_"):
                if "_skeleton" in base or "_underhat" in base or "_wraps" in base:
                    continue
                return os.path.join(dirpath, name)
    return ""


def _pick_primary_mesh(paths: list[str]) -> str:
    """Prefer SK_/SM_Hair_ ActorX, then UEModel; skip cover variants when a main exists."""
    if not paths:
        return ""
    scored = []
    for p in paths:
        low = os.path.basename(p).casefold()
        score = 0
        if low.endswith(".psk"):
            score += 30
        elif low.endswith(".pskx"):
            score += 20
        elif low.endswith(".uemodel"):
            score += 10
        if low.startswith("sk_"):
            score += 5
        if "sm_hair_" in low:
            score += 4
        if any(x in low for x in ("_chincover", "_fullcover", "_collar", "_headwrap", "_mask")):
            score -= 8
        scored.append((score, p))
    scored.sort(key=lambda t: (-t[0], t[1].casefold()))
    return scored[0][1]


def list_body_mesh() -> str:
    """Return SK_Body.psk/.pskx path under Shared/Bodies, or ''."""
    cache_key = ("body", utils.get_pioneer_root() or "", get_appearance_csv_path())
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    path = ""
    csv_choices = _choices_from_csv("body")
    if csv_choices:
        for choice in csv_choices:
            if choice.get("path") and os.path.isfile(choice["path"]):
                path = choice["path"]
                break
    if not path:
        chars = _characters_root()
        if chars:
            bodies = os.path.join(chars, "Shared", "Bodies")
            if not os.path.isdir(bodies):
                bodies = os.path.join(chars, "Bodies")
            for candidate in (
                os.path.join(bodies, "SK_Body.psk"),
                os.path.join(bodies, "SK_Body.pskx"),
            ):
                if os.path.isfile(candidate):
                    path = candidate
                    break
            if not path:
                for mesh in _iter_meshes(bodies):
                    if os.path.basename(mesh).casefold().startswith("sk_body"):
                        path = mesh
                        break
    _CACHE[cache_key] = path
    return path


def _head_label(folder: str) -> str:
    """Short UI label: Face_01_Pio → Face 01 (drop redundant Head_/Base/Pio noise).

    Non-Pio twins are dropped in ``list_head_choices`` (prefer ``*_Pio``), so
    labels never need a trailing Pio disambiguator.
    """
    raw = (folder or "").strip()
    text = raw
    text = re.sub(r"^(Head_|Face_)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"_Base$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"_Pio$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"_+", " ", text).strip()
    core = _humanize(text)
    if raw.casefold().startswith("face_") and not core.casefold().startswith("face"):
        core = f"Face {core}"
    return core or _humanize(raw)


def _head_core_key(folder: str) -> str:
    """Face_07_Pio / Face_07 → face_07 (for Pio-preference dedupe)."""
    raw = (folder or "").strip()
    return re.sub(r"_Pio$", "", raw, flags=re.IGNORECASE).casefold()


def _dedupe_prefer_pio_heads(choices: list[dict]) -> list[dict]:
    """Keep Face_N_Pio when both Face_N and Face_N_Pio exist; drop the non-Pio twin."""
    best: dict[str, dict] = {}
    order: list[str] = []
    for choice in choices:
        folder = (choice.get("folder") or choice.get("key") or "").strip()
        core = _head_core_key(folder) or (choice.get("id") or "").casefold()
        is_pio = bool(re.search(r"_Pio$", folder, flags=re.IGNORECASE))
        prev = best.get(core)
        if prev is None:
            best[core] = choice
            order.append(core)
            continue
        prev_folder = (prev.get("folder") or prev.get("key") or "").strip()
        prev_pio = bool(re.search(r"_Pio$", prev_folder, flags=re.IGNORECASE))
        if is_pio and not prev_pio:
            best[core] = choice
    out = []
    for core in order:
        choice = best[core]
        folder = (choice.get("folder") or choice.get("key") or "").strip()
        if folder:
            label = _head_label(folder)
            if choice.get("label") != label:
                choice = dict(choice)
                choice["label"] = label
                choice = _register_choice(choice)
        out.append(choice)
    return out


def list_head_choices() -> list[dict]:
    cache_key = "heads"
    if cache_key not in _CACHE:
        _CACHE[cache_key] = sorted(
            _dedupe_prefer_pio_heads(_choices_from_csv("head")),
            key=lambda choice: choice["label"].casefold(),
        )
    return _CACHE[cache_key]

_HAIR_COLOR_PAK_READY = set()


def hair_color_choice(scene, role="Hair") -> str:
    property_name = "arc_appearance_facial_hair_color" if role == "FacialHair" else "arc_appearance_hair_color"
    return getattr(scene, property_name, "NONE") or "NONE"


def hair_object_role(obj) -> str:
    if obj.type != "MESH":
        return ""
    role = str(obj.get("arc_assembly_role") or "")
    if role:
        return role if role in ("Hair", "FacialHair") else ""
    source = str(obj.get("arc_source_mesh") or obj.get("arc_psk_path") or "").replace(chr(92), "/").casefold()
    if "/facialhairs/" in source or "/facialhair/" in source:
        return "FacialHair"
    name = (obj.name or "").casefold()
    if "facial" in name or "beard" in name or "moustache" in name or "mustache" in name:
        return "FacialHair"
    if str(obj.get("arc_model_type") or "").casefold() == "hair" or "hair" in name:
        return "Hair"
    return ""


def isolate_appearance_materials(obj) -> None:
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    for index, material in enumerate(obj.data.materials):
        if material is not None and material.users > 1:
            obj.data.materials[index] = material.copy()
    for slot in getattr(obj, "material_slots", ()):
        if slot.link == 'OBJECT' and slot.material is not None and slot.material.users > 1:
            slot.material = slot.material.copy()


def resolve_hair_color_path(choice_id=None, scene=None) -> str:
    """Resolve and extract the selected palette MI before applying a hairstyle.

    The catalog can list a palette that has never been extracted. Its display
    name alone must not silently leave the hairstyle's default brown colours.
    """
    scene = scene or bpy.context.scene
    choice_id = choice_id or getattr(scene, "arc_appearance_hair_color", "NONE")
    if not choice_id or choice_id == "NONE":
        return ""
    choice = resolve_choice(choice_id)
    if not choice or choice.get("kind") != "hair_color":
        return ""
    path = choice.get("path") or _resolve_csv_rel_path(choice.get("rel_path") or "")
    from ..Shared import pak_extract
    game = pak_extract.configured_dir(scene)
    rel = choice.get("rel_path") or ""
    if game and rel:
        package = pak_extract.game_path(rel)
        token = (os.path.normcase(game), package.casefold(), pak_extract._extraction_revision(scene))
        if token not in _HAIR_COLOR_PAK_READY or not (path and os.path.isfile(path)):
            code, log, _meshes = pak_extract.extract_paths(game, pak_extract.resolve_usmap(scene), [package])
            if code:
                print("Arc Pak hair colour: " + log[-1200:])
                return ""
        candidates = [os.path.join(pak_extract.cache_dir(), *package.split("/")) + ".json"]
        candidates.append(candidates[0].replace("PioneerGame" + os.sep + "Content", "RemappedPlugins" + os.sep + "Content"))
        path = next((candidate for candidate in candidates if os.path.isfile(candidate)), "")
        if path:
            _HAIR_COLOR_PAK_READY.add(token)
    if path and os.path.isfile(path):
        choice["path"] = path
        _register_choice(choice)
        return path
    return ""


def list_hair_color_choices() -> list[dict]:
    cache_key = "hair_colors"
    if cache_key not in _CACHE:
        _CACHE[cache_key] = sorted(_choices_from_csv("hair_color"), key=lambda choice: choice["label"].casefold())
    return _CACHE[cache_key]

def make_hair_color_enum_items(self, context):
    choices = list_hair_color_choices()
    items = [("NONE", "From style MI", "Use ColorA/B on the hairstyle MI (e.g. *_Black)")]
    for i, choice in enumerate(choices):
        items.append((choice["id"], choice["label"], choice.get("path") or "", i + 1))
    return items


def _list_all_hair_choices() -> list[dict]:
    """Return packaged styles while preserving legacy mixed-catalog identities."""
    cache_key = "hairs"
    if cache_key not in _CACHE:
        by_key = {}
        for choice in _choices_from_csv("hair"):
            label = choice.get("label") or choice["key"]
            if not choice.get("has_mesh") and not label.endswith("(no mesh)"):
                choice["label"] = f"{label} (no mesh)"
            by_key[(choice.get("key") or choice["id"]).casefold()] = choice
        _CACHE[cache_key] = sorted(by_key.values(), key=lambda choice: (0 if choice.get("has_mesh") else 1, choice["label"].casefold()))
    return _CACHE[cache_key]

def hair_choice_role(choice_or_id) -> str:
    """Classify legacy mixed-catalog IDs without changing their saved identity."""
    if isinstance(choice_or_id, dict):
        choice = choice_or_id
    else:
        _list_all_hair_choices()
        choice = _CHOICE_BY_ID.get(str(choice_or_id)) or {}
    segment = str(choice.get("segment") or "").casefold()
    path = str(choice.get("rel_path") or choice.get("path") or "").replace("\\", "/").casefold()
    return "FacialHair" if segment == "facialhairs" or "/facialhairs/" in path else "Hair"


def list_hair_choices() -> list[dict]:
    return [choice for choice in _list_all_hair_choices() if hair_choice_role(choice) == "Hair"]


def list_facial_hair_choices() -> list[dict]:
    return [choice for choice in _list_all_hair_choices() if hair_choice_role(choice) == "FacialHair"]


def make_head_enum_items(self, context):
    choices = list_head_choices()
    if not choices:
        return [("NONE", "No heads exported", "Export Characters/Heads as .psk/.pskx")]
    items = [("NONE", "None", "Skip head import")]
    for i, choice in enumerate(choices):
        tip = choice.get("path") or "No mesh on disk"
        items.append((choice["id"], choice["label"], tip, i + 1))
    return items


def make_hair_enum_items(self, context):
    choices = list_hair_choices()
    legacy_numbers = {choice["id"]: i + 1 for i, choice in enumerate(_list_all_hair_choices())}
    items = [("NONE", "None / bald", "Skip hair import")]
    ready = sum(1 for c in choices if c.get("has_mesh"))
    missing = len(choices) - ready
    for i, choice in enumerate(choices):
        tip = choice.get("path") or "No .psk/.pskx/.uemodel — re-export mesh in FModel"
        items.append((choice["id"], choice["label"], tip, legacy_numbers[choice["id"]]))
    if not choices:
        items = [("NONE", "No hairstyles found", "Export Characters/Hairs (DA_VP + mesh)")]
    elif missing and ready:
        items[0] = (
            "NONE",
            "None / bald",
            f"{ready} importable · {missing} JSON-only (no mesh)",
        )
    return items


def make_facial_hair_enum_items(self, context):
    numbers = {choice["id"]: i + 1 for i, choice in enumerate(_list_all_hair_choices())}
    items = [("NONE", "None", "Skip facial hair import", 0)]
    for choice in list_facial_hair_choices():
        label = choice["label"].removeprefix("Facial · ")
        items.append((choice["id"], label, choice.get("path") or "No mesh exported", numbers[choice["id"]]))
    return items


def list_gear_choices(kind: str) -> list[dict]:
    """CSV-backed backpack / attachment / charm / raider tool catalog."""
    kind = (kind or "").strip().casefold()
    if kind not in _GEAR_KINDS:
        return []
    cache_key = f"gear:{kind}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    choices = _choices_from_csv(kind) or []
    for choice in choices:
        if not choice.get("has_mesh"):
            label = choice.get("label") or choice.get("key") or choice["id"]
            if not str(label).endswith("(no mesh)"):
                choice = dict(choice)
                choice["label"] = f"{label} (no mesh)"
                choice = _register_choice(choice)
    _CACHE[cache_key] = choices
    return choices


def list_backpack_colorways(choice_id=None) -> list[dict]:
    from . import backpack_catalog
    if choice_id is None:
        choice_id = getattr(bpy.context.scene, "arc_appearance_backpack", "NONE")
    list_gear_choices("backpack")
    return backpack_catalog.list_colorways(resolve_choice(choice_id) or {})


def make_backpack_colorway_enum_items(self, context):
    from . import backpack_catalog
    list_gear_choices("backpack")
    return backpack_catalog.enum_items(resolve_choice(getattr(self, "arc_appearance_backpack", "NONE")) or {})


def backpack_extraction_rels(choice_id, colorway="BASE") -> list[str]:
    list_gear_choices("backpack")
    choice = resolve_choice(choice_id) or {}
    item = choice.get("icon_stem") or choice.get("folder") or ""
    rel = str(choice.get("rel_path") or "").replace("\\", "/")
    parts = rel.split("/")
    container = "/".join(parts[:4]) if parts[:3] == ["Characters", "Backpacks", "Containers"] else ""
    return [value for value in (f"Items/Characters/Skins/BackpackContainer/{item}" if item else "", container) if value]


def backpack_material_extraction_rels(choice_id, colorway="BASE") -> list[str]:
    from . import backpack_catalog
    list_gear_choices("backpack")
    return backpack_catalog.material_dependencies(resolve_choice(choice_id) or {}, colorway)


def _make_gear_enum_items(kind: str, empty_label: str):
    def _items(self, context):
        choices = list_gear_choices(kind)
        items = [("NONE", "None", f"Skip {kind.replace('_', ' ')} import")]
        for i, choice in enumerate(choices):
            tip = choice.get("path") or choice.get("rel_path") or "No mesh on disk"
            items.append((choice["id"], choice["label"], tip[:60], i + 1))
        if not choices:
            return [("NONE", empty_label, "Ship/regenerate appearance_reference.csv")]
        return items

    return _items


make_backpack_enum_items = _make_gear_enum_items("backpack", "No backpacks in CSV")
make_attachment_enum_items = _make_gear_enum_items("attachment", "No attachments in CSV")
make_charm_enum_items = _make_gear_enum_items("charm", "No charms in CSV")
make_raider_tool_enum_items = _make_gear_enum_items("raider_tool", "No raider tools in CSV")


def armature_on_first_slot_missing(role: str):
    """Outfit/body armature on the lowest slot that does not already have *role*."""
    from . import import_layout as layout

    occupied = layout.discover_slots()
    for slot in sorted(occupied):
        if role in occupied.get(slot, ()):
            continue
        for obj in bpy.data.objects:
            if getattr(obj, "type", "") != "ARMATURE":
                continue
            if layout.slot_of_object(obj) != slot:
                continue
            roles = {layout.infer_role(obj)}
            for child in getattr(obj, "children", ()):
                roles.add(layout.infer_role(child))
            if "Outfit" in roles or "Body" in roles:
                return obj
    return None


def find_target_armature(context) -> bpy.types.Object | None:
    """Prefer selected armature, else armature modifier / parent of selected mesh."""
    selected = list(context.selected_objects or [])
    for obj in selected:
        if obj.type == "ARMATURE":
            return obj
    for obj in selected:
        if obj.type != "MESH":
            continue
        for mod in obj.modifiers:
            if mod.type == "ARMATURE" and mod.object and mod.object.type == "ARMATURE":
                return mod.object
        if obj.parent and obj.parent.type == "ARMATURE":
            return obj.parent
    active = context.view_layer.objects.active
    if active is not None:
        if active.type == "ARMATURE":
            return active
        if active.type == "MESH":
            for mod in active.modifiers:
                if mod.type == "ARMATURE" and mod.object and mod.object.type == "ARMATURE":
                    return mod.object
            if active.parent and active.parent.type == "ARMATURE":
                return active.parent
    return None


def _role_matches(obj_role: str, want_role: str) -> bool:
    a = (obj_role or "").strip().casefold()
    b = (want_role or "").strip().casefold()
    if not a or not b:
        return False
    if a == b:
        return True
    return a.replace("_", "").replace(" ", "") == b.replace("_", "").replace(" ", "")


def _object_alive(obj) -> bool:
    try:
        obj.name
        return True
    except ReferenceError:
        return False


def find_selected_role_objects(context, role: str, *, attach_to=None) -> list:
    """Meshes/armatures matching *role* on the selected pad (or selection).

    Prefer parts skinned to *attach_to* / sharing its import slot so Replace
    Selected works when the outfit armature is selected (not the old hair mesh).
    """
    from . import import_layout as _layout

    selected = [o for o in list(context.selected_objects or []) if _object_alive(o)]
    master = attach_to if attach_to is not None and _object_alive(attach_to) else None
    if master is None:
        master = find_target_armature(context)

    prefer_slot = _layout.slot_of_object(master) if master is not None else None
    if prefer_slot is None:
        for obj in selected:
            prefer_slot = _layout.slot_of_object(obj)
            if prefer_slot is not None:
                break

    def _on_pad(obj) -> bool:
        if master is not None:
            if obj == master:
                return True
            if obj.parent == master:
                return True
            for mod in getattr(obj, "modifiers", []) or []:
                if getattr(mod, "type", "") == "ARMATURE" and getattr(mod, "object", None) == master:
                    return True
        if prefer_slot is not None:
            return _layout.slot_of_object(obj) == prefer_slot
        return obj in selected

    hits = []
    pool = list(bpy.data.objects) if (master is not None or prefer_slot is not None) else selected
    for obj in pool:
        if not _object_alive(obj) or obj.type not in {"MESH", "ARMATURE"}:
            continue
        try:
            stamped = str(obj.get("arc_assembly_role", "") or "")
        except Exception:
            stamped = ""
        if not stamped:
            stamped = _layout.infer_role(obj)
        if not _role_matches(stamped, role):
            continue
        if not _on_pad(obj):
            continue
        # Never delete the outfit master armature when replacing a part role.
        if obj.type == "ARMATURE" and master is not None and obj == master and role not in (
            "Body", "Outfit",
        ):
            continue
        hits.append(obj)
    return hits


def remove_appearance_objects(objects: list, *, keep_armature=None) -> list[str]:
    """Delete appearance meshes (and orphaned standalone armatures). Returns log lines."""
    msgs = []
    meshes = [o for o in objects if o and o.name in bpy.data.objects and o.type == "MESH"]
    arms = {o for o in objects if o and o.name in bpy.data.objects and o.type == "ARMATURE" and o != keep_armature}
    for mesh in meshes:
        # Standalone import: armature parent that only served this part.
        parent = mesh.parent
        if parent and parent.type == "ARMATURE" and parent != keep_armature:
            siblings = [
                c for c in parent.children
                if c.type == "MESH" and c != mesh and c.name in bpy.data.objects
            ]
            if not siblings:
                arms.add(parent)
        try:
            name = mesh.name
            bpy.data.objects.remove(mesh, do_unlink=True)
            msgs.append(f"Removed {name}")
        except Exception as exc:
            msgs.append(f"Remove failed: {exc}")
    for arm in list(arms):
        if arm.name not in bpy.data.objects:
            continue
        # Do not delete armatures that still have other mesh children (outfit masters).
        kids = [c for c in arm.children if c.type == "MESH" and c.name in bpy.data.objects]
        if kids:
            continue
        try:
            name = arm.name
            bpy.data.objects.remove(arm, do_unlink=True)
            msgs.append(f"Removed armature {name}")
        except Exception as exc:
            msgs.append(f"Armature remove failed: {exc}")
    return msgs


def _resolve_pioneer_mesh_rel(rel_candidates: tuple[str, ...] | list[str]) -> str:
    """First existing mesh under Pioneer content for the given relative paths."""
    from ..Shared import pak_extract
    if pak_extract.configured_dir(bpy.context.scene):
        for rel in rel_candidates:
            found = _mesh_from_pak(rel)
            if found:
                return found
        return ""
    root = utils.get_pioneer_root() or ""
    if not root:
        return ""
    for rel in rel_candidates:
        parts = rel.replace("\\", "/").split("/")
        if not parts:
            continue
        folder = utils.find_relative_dir(root, parts[:-1])
        if folder:
            path = os.path.join(folder, parts[-1])
            if os.path.isfile(path):
                return path
        # Direct join when pioneer root already is Content/Pioneer.
        direct = os.path.join(root, *parts)
        if os.path.isfile(direct):
            return direct
    return ""


def _mesh_rel_variants(rel: str) -> list[str]:
    """Same package as .psk / .pskx / .uemodel."""
    text = (rel or "").replace("\\", "/").strip()
    if not text:
        return []
    low = text.casefold()
    base = text
    for ext in _MESH_EXTS:
        if low.endswith(ext):
            base = text[: -len(ext)]
            break
    else:
        return [text]
    return [base + ext for ext in _MESH_PREF]


def _mesh_file_usable(path: str) -> bool:
    """False for a UEModel whose first LOD has no vertices."""
    if not path or not os.path.isfile(path):
        return False
    if not path.casefold().endswith(".uemodel"):
        return True
    try:
        from ..Shared import uemodel_import

        _name, _version, lods = uemodel_import.parse_uemodel(path, scale=1.0)
    except Exception:
        return True
    if not lods:
        return False
    return len(lods[0].vertices) > 0


def _cached_mesh_from_rel(rel: str) -> str:
    """Newest exact cached mesh across equivalent cooked mount aliases."""
    from ..Shared import pak_extract
    return pak_extract._expected_uemodel(pak_extract.cache_dir(), pak_extract.game_path(rel))


def locate_appearance_mesh(rel: str) -> str:
    """On-disk mesh, pak-cache mesh, then a fresh pak extract."""
    rel = (rel or "").replace("\\", "/").strip()
    _mesh_from_pak.last_reason = ""
    if not rel:
        return ""
    from ..Shared import pak_extract
    if str(getattr(bpy.context.scene, "arc_import_source", "PAK")) == "PAK" and pak_extract.configured_dir(bpy.context.scene):
        return _mesh_from_pak(rel)
    for variant in _mesh_rel_variants(rel):
        found = _resolve_csv_rel_path(variant)
        if _mesh_file_usable(found):
            return found
        cached = _cached_mesh_from_rel(variant)
        if _mesh_file_usable(cached):
            return cached
    if str(getattr(bpy.context.scene, "arc_import_source", "PAK")) == "PAK":
        return _mesh_from_pak(rel)
    _mesh_from_pak.last_reason = "not on disk, and Import Source is not Paks"
    return ""


def _pak_reason_suffix() -> str:
    """' (reason)' from the last mesh lookup, or ''."""
    reason = (getattr(_mesh_from_pak, "last_reason", "") or "").strip()
    if len(reason) > 300:
        reason = "..." + reason[-300:]
    return f" ({reason})" if reason else ""


def _mesh_from_pak(rel: str) -> str:
    """Extract one Pioneer-relative mesh when the paks folder is set and disk has no file."""
    rel = (rel or "").replace("\\", "/").strip()
    _mesh_from_pak.last_reason = ""
    if not rel:
        _mesh_from_pak.last_reason = "no mesh path"
        return ""
    cached = _cached_mesh_from_rel(rel)
    from ..Shared import pak_extract
    if _mesh_file_usable(cached) and pak_extract.mesh_cache_is_current(cached):
        try:
            pak_extract.ensure_cached_mesh_material_metadata(bpy.context.scene, [cached])
        except Exception as exc:
            _mesh_from_pak.last_reason = str(exc)
            return ""
        return cached
    try:
        from ..Shared import pak_extract

        if not pak_extract.configured_dir(bpy.context.scene):
            _mesh_from_pak.last_reason = "Paks folder is not set"
            return ""
        if not pak_extract.resolve_usmap(bpy.context.scene):
            _mesh_from_pak.last_reason = "usmap is missing"
            return ""
        meshes = pak_extract.ensure_rels(bpy.context.scene, [rel])
    except Exception as exc:
        _mesh_from_pak.last_reason = str(exc)
        print(f"Arc Pak: {exc}")
        return ""
    for mesh in meshes:
        if _mesh_file_usable(mesh):
            return mesh
    cached = _cached_mesh_from_rel(rel)
    if _mesh_file_usable(cached):
        return cached
    tail = ""
    try:
        from ..Shared import pak_extract

        tail = pak_extract._log_tail(getattr(pak_extract, "_LAST_EXTRACT_LOG", "") or "")
    except Exception:
        tail = ""
    _mesh_from_pak.last_reason = tail or "pak extract wrote no mesh"
    return ""


_mesh_from_pak.last_reason = ""


def resolve_backpack_frame_mesh() -> str:
    """Default rack mesh ``SM_Backpack_Frame_01_A`` (static)."""
    return _resolve_pioneer_mesh_rel(_BACKPACK_FRAME_RELS)


def resolve_backpack_strap_mesh() -> str:
    """Default strap SK that owns ``backpackframe`` / ``slot_*`` bones."""
    return _resolve_pioneer_mesh_rel(_BACKPACK_STRAP_RELS)


def _charm_needs_connector(objects: list) -> bool:
    """Shared karabiner bridges the authored gap above a hanging charm.

    A charm with its own connector reaches/crosses local Z=0. Preserve its
    authored origin; moving every charm up hides the gap but discards the clip.
    """
    tops = [max(v.co.z for v in obj.data.vertices)
            for obj in objects
            if _object_alive(obj) and obj.type == "MESH" and len(obj.data.vertices)]
    return bool(tops) and max(tops) < -1e-5


def _charm_connector_on_armature(master) -> bool:
    if master is None or not _object_alive(master):
        return False
    return any(
        obj.type == "MESH" and obj.parent == master
        and str(obj.get("arc_assembly_role") or "") == "CharmConnector"
        for obj in bpy.data.objects
    )


def _mesh_is_skinned(obj) -> bool:
    if not _object_alive(obj) or obj.type != "MESH":
        return False
    if any(m.type == "ARMATURE" and m.object for m in obj.modifiers):
        return True
    # A single rigid bone group (gear, charms) is not skin.
    return bool(obj.vertex_groups) and not obj.get("arc_rigid_bone")


def _gear_source_socket_bind(obj, bone_name):
    """Skeletal gear is authored in character component space, not at the socket."""
    from mathutils import Matrix, Vector, Quaternion
    cached = obj.get("arc_gear_source_socket_matrix")
    if cached and obj.get("arc_gear_source_socket_name") == bone_name:
        return Matrix([cached[index:index + 4] for index in range(0, 16, 4)])
    path = str(obj.get("arc_source_mesh") or obj.get("arc_psk_path") or "")
    bind = None
    if path.casefold().endswith(".uemodel"):
        from ..Shared import uemodel_import
        scale = float(obj.data.get("arc_uemodel_scale", importing.ue_import_unit_scale()))
        matrices = {}
        for index, bone in enumerate(uemodel_import.parse_uemodel_bones(path, scale=scale)):
            local = Matrix.LocRotScale(Vector(bone.translation), Quaternion((bone.rotation[3], *bone.rotation[:3])), Vector(bone.scale))
            matrices[index] = matrices[bone.parent] @ local if bone.parent in matrices else local
            if bone.name == bone_name:
                bind = matrices[index]
                break
    else:
        arm = next((mod.object for mod in obj.modifiers if mod.type == "ARMATURE" and mod.object), None)
        arm = arm or (obj.parent if obj.parent and obj.parent.type == "ARMATURE" and obj.parent_type != "BONE" else None)
        if arm and bone_name in arm.data.bones:
            bind = arm.matrix_world @ arm.data.bones[bone_name].matrix_local
    if bind is not None:
        bind = Matrix.LocRotScale(bind.to_translation(), bind.to_quaternion(), Vector((1.0, 1.0, 1.0)))
        obj["arc_gear_source_socket_matrix"] = [value for row in bind for value in row]
        obj["arc_gear_source_socket_name"] = bone_name
    return bind


def bone_parent_to_slot(objects: list, master, bone_name: str) -> int:
    """Attach at the Unreal socket head while preserving imported world size."""
    if master is None or not _object_alive(master) or master.type != "ARMATURE":
        return 0
    if not bone_name or bone_name not in master.data.bones:
        return 0
    bpy.context.view_layer.update()
    snapped = 0
    for obj in objects:
        if not _object_alive(obj) or obj.type != "MESH":
            continue
        try:
            actual_bone = str(obj.get("arc_gear_attach_bone") or bone_name)
            if actual_bone != bone_name:
                snapped += bone_parent_to_slot([obj], master, actual_bone)
                continue
            source_bind = _gear_source_socket_bind(obj, bone_name)
            old_arm = obj.parent if obj.parent is not None and obj.parent.type == "ARMATURE" else None
            # Gear follows the socket; component-space SKs retain their bind offset.
            for mod in list(obj.modifiers):
                if mod.type == "ARMATURE":
                    obj.modifiers.remove(mod)
            from mathutils import Matrix
            world_scale = obj.matrix_world.to_scale()
            pose_bone = master.pose.bones[bone_name]
            socket = master.matrix_world @ pose_bone.matrix
            target = Matrix.LocRotScale(socket.to_translation(), socket.to_quaternion(), world_scale)
            if source_bind is not None:
                from mathutils import Vector
                socket_pose = Matrix.LocRotScale(socket.to_translation(), socket.to_quaternion(), Vector((1.0, 1.0, 1.0)))
                target = socket_pose @ source_bind.inverted_safe() @ Matrix.Diagonal((*world_scale, 1.0))
            obj.parent = master
            obj.parent_type = "BONE"
            obj.parent_bone = bone_name
            obj.matrix_parent_inverse = Matrix.Identity(4)
            # Blender bone parenting uses the tail; Unreal sockets use the head.
            parent_world = socket @ Matrix.Translation((0.0, pose_bone.length, 0.0))
            obj.matrix_basis = parent_world.inverted_safe() @ target
            # Rigid part: one group named after the slot bone at weight 1.0. The bone
            # parent carries it, so no Armature modifier (it would move it twice).
            for group in list(obj.vertex_groups):
                obj.vertex_groups.remove(group)
            group = obj.vertex_groups.new(name=bone_name)
            group.add(range(len(obj.data.vertices)), 1.0, "REPLACE")
            obj["arc_rigid_bone"] = bone_name
            if (old_arm is not None and _object_alive(old_arm) and old_arm != master
                    and not old_arm.children and not old_arm.get("arc_assembly_role")
                    and not any(m.type == "ARMATURE" and m.object == old_arm
                                for o in bpy.data.objects for m in getattr(o, "modifiers", []))):
                bpy.data.objects.remove(old_arm, do_unlink=True)
            snapped += 1
        except Exception as exc:
            print(f"Arc Raiders: bone parent {obj.name} → {bone_name} failed: {exc}")
    return snapped


def _copy_backpack_slot_bones_from_strap(master, strap_arm) -> list[str]:
    """Copy backpackframe / slot_* edit bones from *strap_arm* onto *master*.

    Prefer this over ``object.join`` — outfit armatures are often hidden, and
    join fails with \"Active object is not a selected armature\".
    """
    from mathutils import Vector

    need = list(_BACKPACK_SLOT_BONES)
    # Also copy intermediate parents under spine if present on strap only.
    strap_bones = strap_arm.data.bones
    master_bones = master.data.bones
    if "spine_03" not in master_bones:
        return ["Outfit armature missing spine_03 — cannot attach backpack slots"]

    # Collect backpackframe subtree names (strap order is parent-before-child).
    subtree: list[str] = []
    for b in strap_bones:
        if b.name == "backpackframe" or (
            b.parent and (b.parent.name == "backpackframe" or b.name in need)
        ):
            subtree.append(b.name)
    # Ensure backpackframe first, then direct children we care about.
    ordered = []
    if "backpackframe" in strap_bones:
        ordered.append("backpackframe")
    for n in need:
        if n != "backpackframe" and n in strap_bones and n not in ordered:
            ordered.append(n)

    # PSK bones use centimeters with a .01 object scale; UEModel bones
    # already use meters. Align component space without applying that scale twice.
    from mathutils import Matrix
    master_units = float(master.get("arc_ue_unit_scale", 1.0) or 1.0)
    strap_units = float(strap_arm.get("arc_ue_unit_scale", 1.0) or 1.0)
    aligned_world = master.matrix_world @ Matrix.Scale(strap_units / master_units, 4)
    bpy.context.view_layer.update()

    # Map world head/tail from strap → master edit bones.
    world_rest = {}
    for name in ordered:
        pb = strap_arm.pose.bones.get(name) if strap_arm.pose else None
        db = strap_bones.get(name)
        if db is None:
            continue
        # Bone rest head/tail in armature local → world via matrix_world.
        mw = aligned_world
        head_w = mw @ db.head_local
        tail_w = mw @ db.tail_local
        parent_name = db.parent.name if db.parent else ""
        world_rest[name] = (head_w, tail_w, parent_name, mw @ db.matrix_local)

    if "backpackframe" not in world_rest:
        return ["Strap armature has no backpackframe bone"]

    # Enter edit mode on master (unhide temporarily).
    was_hide = bool(getattr(master, "hide_get", lambda: False)())
    was_hide_vp = bool(getattr(master, "hide_viewport", False))
    try:
        if was_hide:
            master.hide_set(False)
        master.hide_viewport = False
    except Exception:
        pass

    prev_active = bpy.context.view_layer.objects.active
    prev_mode = getattr(prev_active, "mode", "OBJECT") if prev_active else "OBJECT"
    created = []
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        master.select_set(True)
        bpy.context.view_layer.objects.active = master
        bpy.ops.object.mode_set(mode="EDIT")
        ebones = master.data.edit_bones
        inv = master.matrix_world.inverted()
        for name in ordered:
            if name not in world_rest:
                continue
            if name in ebones:
                continue
            head_w, tail_w, parent_name, rest_world = world_rest[name]
            eb = ebones.new(name)
            eb.head = inv @ head_w
            tip = inv @ tail_w
            if (tip - eb.head).length < 1e-4:
                tip = eb.head + Vector((0.0, 0.05, 0.0))
            eb.tail = tip
            length = (tip - eb.head).length
            eb.matrix = inv @ rest_world
            eb.length = length
            # Parent: backpackframe under spine_03; slots under backpackframe.
            if name == "backpackframe":
                parent = ebones.get("spine_03")
            else:
                parent = ebones.get(parent_name) or ebones.get("backpackframe")
            if parent is not None:
                eb.parent = parent
                eb.use_connect = False
            created.append(name)
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception as exc:
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
        return [f"Edit-bone copy failed: {exc}"]
    finally:
        try:
            if was_hide:
                master.hide_set(True)
            master.hide_viewport = was_hide_vp
        except Exception:
            pass
        try:
            if prev_active is not None and _object_alive(prev_active):
                bpy.context.view_layer.objects.active = prev_active
        except Exception:
            pass

    have = {b.name for b in master.data.bones}
    missing = [n for n in ("backpackframe", "slot_frame", "slot_container") if n not in have]
    if missing:
        return [f"Copied {created} but still missing: {', '.join(missing)}"]
    return [f"Added backpack slot bones: {', '.join(created)}"]


def ensure_backpack_slot_bones(master, strap_arm=None, *, discard_strap: bool = True) -> tuple[bool, list[str]]:
    """Ensure ``slot_frame`` / ``slot_container`` exist on *master*.

    Outfit/body armatures do not ship these bones; they live on
    ``SK_Default_Strap`` under ``spine_03`` → ``backpackframe``. Pass *strap_arm*
    to copy from an already imported harness. Otherwise import the strap, copy
    the bones, and delete that temporary import (mesh included).
    """
    msgs: list[str] = []
    if master is None or not _object_alive(master) or master.type != "ARMATURE":
        return False, ["No target armature for backpack slots"]
    have = {b.name for b in master.data.bones}
    if all(n in have for n in ("slot_frame", "slot_container", "backpackframe")):
        return True, []

    owned_import = False
    strap_objs: list = []
    if strap_arm is None or not _object_alive(strap_arm):
        strap_path = resolve_backpack_strap_mesh() or locate_appearance_mesh(_BACKPACK_STRAP_RELS[0])
        if not strap_path:
            return False, [
                "Backpack strap SK_Default_Strap not found in the Pioneer folder or the paks"
            ]
        before = set(bpy.data.objects)
        ok, msg, strap_objs = import_appearance_mesh(strap_path)
        if not ok:
            return False, [f"Strap import failed: {msg}"]
        strap_objs = _bind_strap_armature(strap_path, strap_objs)
        strap_arm = next(
            (o for o in strap_objs if _object_alive(o) and o.type == "ARMATURE"),
            None,
        )
        owned_import = True
        if strap_arm is None:
            for obj in list(strap_objs):
                if _object_alive(obj):
                    try:
                        bpy.data.objects.remove(obj, do_unlink=True)
                    except Exception:
                        pass
            return False, ["Strap import produced no armature"]
    else:
        before = set()

    copy_msgs = _copy_backpack_slot_bones_from_strap(master, strap_arm)
    msgs.extend(copy_msgs)

    if discard_strap and owned_import:
        for obj in list(strap_objs):
            if _object_alive(obj):
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except Exception:
                    pass
        for obj in list(bpy.data.objects):
            if obj in before or not _object_alive(obj):
                continue
            if obj.type in {"MESH", "ARMATURE"} and "strap" in (obj.name or "").casefold():
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except Exception:
                    pass

    have = {b.name for b in master.data.bones} if _object_alive(master) else set()
    ok_bones = all(n in have for n in ("slot_frame", "slot_container", "backpackframe"))
    return ok_bones, msgs


def adopt_backpack_strap(master, strap_objects: list) -> list[str]:
    """Skin the strap mesh onto *master* and drop the temporary strap armature.

    The harness shares the body skeleton. Slot bones are copied first so the
    rack and container can bone-parent afterwards.
    """
    alive = [o for o in strap_objects if _object_alive(o)]
    strap_arm = next((o for o in alive if o.type == "ARMATURE"), None)
    meshes = [o for o in alive if o.type == "MESH"]
    if strap_arm is None:
        return ["Backpack strap import has no armature"]
    ok_bones, msgs = ensure_backpack_slot_bones(
        master, strap_arm, discard_strap=False,
    )
    if not ok_bones:
        return msgs
    from mathutils import Matrix
    master_units = float(master.get("arc_ue_unit_scale", 1.0) or 1.0)
    strap_units = float(strap_arm.get("arc_ue_unit_scale", 1.0) or 1.0)
    strap_arm.matrix_world = master.matrix_world @ Matrix.Scale(strap_units / master_units, 4)
    bpy.context.view_layer.update()
    for mesh in meshes:
        try:
            mw = mesh.matrix_world.copy()
            for mod in list(mesh.modifiers):
                if mod.type == "ARMATURE":
                    mod.object = master
                    mod.use_deform_preserve_volume = True
            mesh.parent = master
            mesh.matrix_world = mw
        except Exception as exc:
            msgs.append(f"Strap mesh attach failed: {exc}")
    try:
        bpy.data.objects.remove(strap_arm, do_unlink=True)
    except Exception as exc:
        msgs.append(f"Strap armature remove failed: {exc}")
    if meshes:
        msgs.append(f"Backpack straps skinned onto '{master.name}'")
    return msgs


def merge_into_armature(
    master,
    new_objects: list,
    *,
    snap_skinned_world: bool = True,
) -> bpy.types.Object:
    """Join new armatures into *master* and retarget mesh armature modifiers."""
    if master is None or master.type != "ARMATURE":
        raise ValueError("Target armature required")
    new_arms = [
        o for o in new_objects
        if _object_alive(o) and o.type == "ARMATURE" and o != master
    ]
    meshes = [o for o in new_objects if _object_alive(o) and o.type == "MESH"]
    if new_arms:
        from collections import Counter

        name_counts = Counter()
        for arm in [master] + new_arms:
            if not _object_alive(arm):
                continue
            for bone in arm.data.bones:
                name_counts[bone.name] += 1
        shared = {name for name, count in name_counts.items() if count > 1}
        bpy.ops.object.select_all(action="DESELECT")
        master.select_set(True)
        for arm in new_arms:
            if _object_alive(arm):
                arm.select_set(True)
        context_view = bpy.context.view_layer
        context_view.objects.active = master
        bpy.ops.object.join()
        master = bpy.context.active_object
        alive_meshes = [m for m in meshes if _object_alive(m)]
        rig.cleanup_duplicate_bones(master, shared, alive_meshes)
    # Skinned parts import at world origin with their own armature; after join the
    # master may sit on an offset pad — snap mesh world = master so they land on
    # the outfit. Static backpack/frame gear is bone-parented separately.
    master_mw = master.matrix_world.copy()
    for obj in meshes:
        if not _object_alive(obj):
            continue
        for mod in obj.modifiers:
            if mod.type == "ARMATURE":
                mod.object = master
                mod.use_deform_preserve_volume = True
        if not snap_skinned_world or not _mesh_is_skinned(obj):
            try:
                if obj.parent is None or (
                    obj.parent.type == "ARMATURE" and obj.parent_type != "BONE"
                ):
                    obj.parent = master
            except Exception:
                pass
            continue
        try:
            obj.parent = master
            obj.matrix_world = master_mw.copy()
        except Exception:
            try:
                obj.parent = master
            except Exception:
                pass
    return master


def _stamp_role(obj, role: str, source_path: str) -> None:
    try:
        obj["arc_assembly_role"] = role
        obj["arc_source_mesh"] = source_path
        obj["arc_psk_path"] = source_path
    except Exception:
        pass


def _reusable_strap_armature(*, require_empty_backpack: bool = True):
    """Strap skeleton on the lowest pad that does not already carry a backpack.

    Tool, charm, and attachment imports each used to spawn their own skeleton.
    Those empty skeletons occupied the strap slot, so the first backpack was
    placed on the next pad.
    """
    from . import import_layout as _layout

    best = None
    best_slot = 10 ** 9
    for obj in bpy.data.objects:
        if not _object_alive(obj) or obj.type != "ARMATURE":
            continue
        if str(obj.get("arc_assembly_role") or "") != "BackpackStrap":
            continue
        try:
            slot = int(obj.get("arc_import_slot") or 0)
        except (TypeError, ValueError):
            slot = _layout.slot_of_object(obj)
            slot = 0 if slot is None else slot
        if require_empty_backpack and any(
            _object_alive(other)
            and str(other.get("arc_assembly_role") or "") == "Backpack"
            and int(other.get("arc_import_slot") or 0) == slot
            for other in bpy.data.objects
        ):
            continue
        if slot < best_slot:
            best = obj
            best_slot = slot
    return best


def _parent_meshes_to_armature(arm, objects: list) -> None:
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    for mesh in objects:
        if not _object_alive(mesh) or mesh.type != "MESH" or mesh == arm:
            continue
        if mesh.parent == arm:
            continue
        try:
            world = mesh.matrix_world.copy()
            mesh.parent = arm
            mesh.matrix_world = world
        except Exception:
            pass


def _bind_strap_armature(path: str, objects: list, *, reuse_existing=True) -> list:
    """Add the strap skeleton when a UEModel import did not bring an armature.

    PSK imports already include one. The strap mesh stays in component space,
    parented to the armature object, so slot bones can carry the other gear.
    """
    alive = [o for o in objects if _object_alive(o)]
    own = next((o for o in alive if getattr(o, "type", "") == "ARMATURE"), None)
    if own is not None and not str(path or "").casefold().endswith(".uemodel"):
        return alive
    existing = _reusable_strap_armature() if reuse_existing else None
    if existing is not None and existing is not own:
        if own is not None:
            # Skinned UEModel import brought its own copy of the strap skeleton.
            for mesh in alive:
                if mesh.type == "MESH":
                    for mod in mesh.modifiers:
                        if mod.type == "ARMATURE" and mod.object == own:
                            mod.object = existing
            alive.remove(own)
            _parent_meshes_to_armature(existing, alive)
            bpy.data.objects.remove(own, do_unlink=True)
        else:
            _parent_meshes_to_armature(existing, alive)
        if existing not in alive:
            alive.append(existing)
        return alive
    if own is not None:
        _stamp_role(own, "BackpackStrap", path)
        return alive
    if not path or not str(path).casefold().endswith(".uemodel"):
        return alive
    try:
        from ..Shared import uemodel_import

        arm = uemodel_import.armature_from_uemodel(
            path, scale=importing.ue_import_unit_scale(),
        )
    except Exception as exc:
        print(f"Arc Raiders: strap armature skipped: {exc}")
        return alive
    if arm is None or not _object_alive(arm):
        return alive
    _stamp_role(arm, "BackpackStrap", path)
    alive.append(arm)
    _parent_meshes_to_armature(arm, alive)
    return alive


def _import_uemodel_outfit(mesh_path: str) -> list:
    """Import .uemodel via the addon's native UEFormat v10 reader (no external add-ons)."""
    from ..Shared import uemodel_import

    scale = importing.ue_import_unit_scale()
    try:
        # Skeletal meshes: [armature, *meshes] with WEIGHTS groups (PSK route parity).
        return uemodel_import.import_uemodel_skinned(mesh_path, scale=scale) or []
    except Exception as exc:
        print(f"[arc-uemodel] skinned import failed ({mesh_path}): {exc}")
        return uemodel_import.try_import_uemodel_v10(mesh_path, scale=scale) or []


def _import_colored_hair(path, hair_mi, colour_json, import_slot):
    from ..Shared.materials.character import setup_hair_material
    try:
        objects = _import_uemodel_outfit(path) if path.casefold().endswith(".uemodel") else importing.import_psk(path)
        if not objects and path.casefold().endswith(".uemodel"):
            for extension in (".psk", ".pskx", ".PSK", ".PSKX"):
                sibling = os.path.splitext(path)[0] + extension
                if os.path.isfile(sibling):
                    path = sibling
                    objects = importing.import_psk(path)
                    break
    except Exception as exc:
        return False, f"Hair import failed: {exc}", []
    if not objects:
        return False, f"Hair import produced no objects: {path}", []
    if import_slot is not None:
        from . import import_layout
        import_layout.stamp_objects(objects, slot=import_slot)
    mi = hair_mi if hair_mi and hair_mi != "NONE" else ""
    for obj in objects:
        if obj.type != "MESH":
            continue
        obj["arc_psk_path"] = path
        obj["arc_source_mesh"] = path
        obj["arc_model_type"] = "hair"
        obj["arc_hair_mi"] = mi
        obj["arc_bridge_source"] = "fmodel"
        if mi:
            obj["arc_skin_json"] = os.path.abspath(mi)
        isolate_appearance_materials(obj)
        setup_hair_material(obj, mi, colour_json=colour_json)
        obj["arc_material_status"] = "hair"
    return True, f"Imported hair: {os.path.basename(path)}", objects


def _import_mesh_deferred(path, *, body_variant, hair_mi, skin_json, manual_skins_folder,
                          skin_choice, material_bindings, import_slot, post_material, defer_materials):
    """Mesh-only import for the staged path. Queues material work on defer_materials.

    Mirrors import_psk_with_materials / the UEModel branch of import_appearance_mesh,
    minus the material step. Returns (ok, msg, objs), or None when the inline path
    must handle the file (other formats, or a UEModel with no LOD verts).
    """
    from ..Shared import textures
    low = path.casefold()
    if low.endswith(".uemodel"):
        try:
            objs = _import_uemodel_outfit(path)
        except Exception:
            return None
        if not objs:
            return None
        psk = False
        model_type = ""
        asset_domain = ""
    elif low.endswith((".psk", ".pskx")):
        if not skin_json:
            skin_json = textures.get_base_skin_json(path, manual_skins_folder) or ""
        model_type = textures.detect_model_type(path)
        try:
            from . import asset_domain as _ad
            asset_domain = _ad.classify_asset_domain(path)
        except Exception:
            asset_domain = ""
        try:
            objs = importing.import_psk(path)
        except RuntimeError as exc:
            return False, str(exc), []
        psk = True
    else:
        return None
    if import_slot is not None:
        from . import import_layout
        import_layout.stamp_objects(objs, slot=import_slot)
    kwargs = dict(
        body_variant=body_variant, hair_mi=hair_mi, skin_json=skin_json,
        manual_skins_folder=manual_skins_folder, skin_choice=skin_choice,
        material_bindings=None if psk else material_bindings, psk=psk,
    )
    for obj in objs:
        if obj.type != "MESH":
            continue
        try:
            obj["arc_psk_path"] = path
            obj["arc_hair_mi"] = hair_mi if hair_mi else "NONE"
            obj["arc_materials_pending"] = 1
            if psk:
                obj["arc_model_type"] = model_type
                if asset_domain:
                    obj["arc_asset_domain"] = asset_domain
                obj["arc_bridge_source"] = "fmodel"
                obj["arc_body_variant"] = body_variant if body_variant else "NONE"
        except Exception:
            pass
        defer_materials.append((obj, path, dict(kwargs), post_material))
    if psk:
        return True, f"Imported ({model_type}): {os.path.basename(path)}", objs
    return True, f"Imported UEModel: {os.path.basename(path)}", objs


def finish_deferred_materials(obj, path, *, body_variant, hair_mi, skin_json,
                              manual_skins_folder, skin_choice, material_bindings, psk):
    """Material step for a mesh imported with defer_materials. Same calls as the inline path."""
    from ..Shared.operators import apply_materials_to_object
    status = apply_materials_to_object(
        obj, path, body_variant=body_variant, hair_mi=hair_mi,
        skin_json=skin_json, manual_skins_folder=manual_skins_folder,
        skin_choice=skin_choice, material_bindings=material_bindings,
    )
    if not psk:
        try:
            from ..Shared.materials.native import shader_parity
            parity = shader_parity.apply_uemodel_slots(obj, path)
            if parity:
                status = f"{status}; shader-parity ({parity})"
        except Exception as exc:
            print(f"Arc Raiders shader parity: slot pass failed for {obj.name}: {exc}")
    try:
        obj["arc_material_status"] = str(status or "")[:900]
        obj["arc_materials_pending"] = 0
    except Exception:
        pass


def import_appearance_mesh(path: str, *, body_variant: str = "NONE", hair_mi: str = "NONE",
                           skin_json: str = "", manual_skins_folder: str = "", skin_choice: str = "NONE", import_slot=None, material_bindings=None, hair_colour_json=None,
                           defer_materials=None, post_material=None):
    """Import .psk/.pskx/.uemodel and apply Arc materials. Returns (ok, msg, objs).

    ``defer_materials`` (a list) makes this mesh-only: each mesh is queued there as
    ``(obj, path, kwargs, post_material)`` and ``finish_deferred_materials`` builds its materials later.
    """
    path = bpy.path.abspath(path)
    if not os.path.isfile(path):
        return False, f"Mesh not found: {path}", []
    if hair_colour_json is not None:
        return _import_colored_hair(path, hair_mi, hair_colour_json, import_slot)
    if defer_materials is not None:
        deferred = _import_mesh_deferred(
            path, body_variant=body_variant, hair_mi=hair_mi, skin_json=skin_json,
            manual_skins_folder=manual_skins_folder, skin_choice=skin_choice,
            material_bindings=material_bindings, import_slot=import_slot,
            post_material=post_material, defer_materials=defer_materials,
        )
        if deferred is not None:
            return deferred
    low = path.casefold()
    if low.endswith(".uemodel"):
        try:
            objs = _import_uemodel_outfit(path)
        except Exception as exc:
            return False, f"UEModel import failed: {exc}", []
        if not objs:
            # Prefer sibling ActorX when the UEModel LOD has empty verts (common for
            # incomplete SK_* UEFormat exports).
            stem, _ = os.path.splitext(path)
            for ext in (".psk", ".pskx", ".PSK", ".PSKX"):
                sibling = stem + ext
                if os.path.isfile(sibling):
                    from ..Shared.operators import import_psk_with_materials

                    return import_psk_with_materials(
                        sibling, body_variant=body_variant, hair_mi=hair_mi,
                        skin_json=skin_json, manual_skins_folder=manual_skins_folder, skin_choice=skin_choice, import_slot=import_slot,
                    )
            tip = ""
            try:
                from ..Shared import uemodel_import

                _n, _v, lods = uemodel_import.parse_uemodel(path, scale=1.0)
                if lods and len(lods[0].vertices) == 0:
                    tip = (
                        " — UEModel LOD has 0 vertices (SK export incomplete); "
                        "re-export the SkeletalMesh as ActorX .psk in FModel"
                    )
            except Exception:
                tip = " — re-export as ActorX .psk if this is a skeletal mesh"
            return False, (
                f"Could not import {os.path.basename(path)}{tip}"
            ), []
        from ..Shared.operators import apply_materials_to_object

        if import_slot is not None:
            from . import import_layout
            import_layout.stamp_objects(objs, slot=import_slot)
        for obj in objs:
            if obj.type != "MESH":
                continue
            try:
                obj["arc_psk_path"] = path
                obj["arc_hair_mi"] = hair_mi if hair_mi else "NONE"
            except Exception:
                pass
            status = apply_materials_to_object(
                obj, path, body_variant=body_variant, hair_mi=hair_mi,
                skin_json=skin_json, manual_skins_folder=manual_skins_folder, skin_choice=skin_choice,
                material_bindings=material_bindings,
            )
            try:
                from ..Shared.materials.native import shader_parity
                parity = shader_parity.apply_uemodel_slots(obj, path)
                if parity:
                    status = f"{status}; shader-parity ({parity})"
            except Exception as exc:
                print(f"Arc Raiders shader parity: slot pass failed for {obj.name}: {exc}")
            obj["arc_material_status"] = str(status or "")[:900]
        return True, f"Imported UEModel: {os.path.basename(path)}", objs

    from ..Shared.operators import import_psk_with_materials

    return import_psk_with_materials(
        path, body_variant=body_variant, hair_mi=hair_mi,
        skin_json=skin_json, manual_skins_folder=manual_skins_folder, skin_choice=skin_choice, import_slot=import_slot,
    )


def _default_slot_armature(*, reuse_existing=True):
    """Strap skeleton used when no outfit is in the scene. Mesh is not kept."""
    existing = _reusable_strap_armature() if reuse_existing else None
    if existing is not None:
        return existing, ""
    path = resolve_backpack_strap_mesh() or locate_appearance_mesh(_BACKPACK_STRAP_RELS[0])
    if not path:
        reason = getattr(_mesh_from_pak, "last_reason", "") or "SK_Default_Strap was not found"
        return None, f"No default strap skeleton ({reason})"
    if path.casefold().endswith(".uemodel"):
        bound = _bind_strap_armature(path, [], reuse_existing=reuse_existing)
        arm = next((o for o in bound if _object_alive(o) and o.type == "ARMATURE"), None)
        if arm is None:
            return None, "Strap UEModel has no skeleton"
        return arm, ""
    ok, msg, objs = import_appearance_mesh(path)
    if not ok:
        return None, f"Strap skeleton import failed: {msg}"
    arm = next((o for o in objs if _object_alive(o) and o.type == "ARMATURE"), None)
    for obj in list(objs):
        if not _object_alive(obj) or obj.type != "MESH":
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass
    if arm is None:
        return None, "Strap import produced no armature"
    _stamp_role(arm, "BackpackStrap", path)
    return arm, ""


def repair_hair_object(obj, scene=None, *, place_static=True):
    """Repair an existing hair/beard in place, preserving topology and palette."""
    from ..Shared import textures; from . import hair_placement
    from ..Shared.materials.character import setup_hair_material
    scene = scene or bpy.context.scene
    source = str(obj.get("arc_source_mesh") or obj.get("arc_psk_path") or "")
    if obj.type != "MESH" or not textures.is_hair(source):
        return False, "Not a hair or facial-hair mesh"
    choices = textures.scan_hair_mis(source)
    mi = choices[0][1] if choices else str(obj.get("arc_hair_mi") or "")
    role = hair_object_role(obj)
    colour_id = hair_color_choice(scene, role)
    colour = resolve_hair_color_path(colour_id, scene) if colour_id != "NONE" else ""
    isolate_appearance_materials(obj)
    setup_hair_material(obj, mi, colour_json=colour)
    obj["arc_model_type"] = "hair"
    _stamp_role(obj, role or "Hair", source)
    if place_static:
        from mathutils import Vector
        from . import import_layout, reference_planes

        def center():
            corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
            return Vector(tuple((min(v[i] for v in corners) + max(v[i] for v in corners)) * 0.5 for i in range(3)))

        bpy.context.view_layer.update()
        old_center = center()
        slot = import_layout.slot_of_object(obj)
        # Existing cards can be parented directly to the static mesh, or its
        # origin wrapper. Keep their upright world frame when the mesh rotates
        # into the head's authored reference frame.
        references = []
        for plane in scene.objects:
            if not plane.get("arc_reference_plane") or reference_planes._reference_role(plane) != role:
                continue
            if int(plane.get("arc_reference_layout_schema", 0)) >= 2:
                continue
            if slot is not None and import_layout.slot_of_object(plane) != slot:
                continue
            owner = plane.parent
            if owner == obj or (obj.parent is not None and owner == obj.parent) or str(plane.get("arc_reference_anchor") or "") == obj.name:
                references.append((plane, plane.matrix_world.copy()))
        placed, message = hair_placement.place_hair_at_head(obj, scene)
        if placed and references:
            bpy.context.view_layer.update()
            delta = center() - old_center
            for plane, world in references:
                world.translation += delta
                plane.matrix_world = world
            bpy.context.view_layer.update()
    else:
        message = "Hair materials repaired"
    return True, message


def iter_appearance_parts(context, **kwargs):
    """Staged add of appearance parts: mesh step first, then one material step per mesh.

    Generator for ``staged_import.StagedRun``. Yields ``(stage, index, total, label)``.
    The first advance runs the mesh import (``import_appearance_parts`` with
    ``defer_materials``), then each deferred mesh gets the grey placeholder. Each
    material step restores that mesh's slots and builds its materials. Return value
    (StopIteration.value) is ``(count, msgs, objs)`` as ``import_appearance_parts`` returns.
    """
    from ..Maps import staged_import
    defer: list = []
    count, msgs, objs = import_appearance_parts(context, defer_materials=defer, **kwargs)
    staged_import.stage_placeholder([entry[0] for entry in defer])
    yield ("mesh", 1, 1, f"{sum(1 for o in objs if _object_alive(o) and o.type == 'MESH')} mesh(es) imported")
    total = len(defer)
    for index, (obj, path, mat_kwargs, post) in enumerate(defer, start=1):
        if not _object_alive(obj):
            continue
        # Work first, then yield: each step's time covers its own mesh.
        name = obj.name
        staged_import.restore_slots(obj)
        finish_deferred_materials(obj, path, **mat_kwargs)
        if post is not None:
            post(obj)
        yield ("material", index, total, name)
    staged_import.sweep_placeholder([o for o in objs if _object_alive(o)])
    alive = [o for o in objs if _object_alive(o)]
    if any(str(o.get("arc_assembly_role") or "") in {"BackpackFrame", "BackpackStrap"} for o in alive):
        # Same pass as the sync path, run once real materials exist.
        from . import backpack_instances
        shared = backpack_instances.share_backpack_data(alive, scene=context.scene)
        if shared["meshes_linked"] or shared["materials_linked"]:
            msgs.append("Shared identical backpack data: " + str(shared["meshes_linked"]) + " meshes, " + str(shared["materials_linked"]) + " materials")
    return count, msgs, objs


def import_appearance_parts(
    context,
    *,
    roles: set[str] | list[str] | None = None,
    attach_to=None,
    replace_selected: bool | None = None,
    standalone: bool = False,
    layout_slot: int | None = None,
    defer_materials: list | None = None,
) -> tuple[int, list[str], list]:
    """Import body/head/hair from scene props for the given *roles*.

    *roles* is a set of ``"Body"`` / ``"Head"`` / ``"Hair"``. When omitted,
    imports whichever dropdowns are non-NONE (body always if Body requested).

    Returns (imported_count, messages, new_objects).
    When *attach_to* is an armature, new parts are merged onto it.
    When *replace_selected* is True (or scene toggle is on), removes the
    currently selected matching part before importing.
    """
    scene = context.scene
    msgs: list[str] = []
    new_objects: list = []

    # Normalize roles: accept "Backpack" / "raider_tool" / "RaiderTool".
    want_raw = {str(r).strip() for r in (roles or ())}
    want = set()
    for r in want_raw:
        rl = r.casefold().replace(" ", "").replace("_", "")
        if rl == "body":
            want.add("Body")
        elif rl == "head":
            want.add("Head")
        elif rl == "hair":
            want.add("Hair")
        elif rl == "facialhair":
            want.add("FacialHair")
        elif rl in ("backpack",):
            want.add("Backpack")
        elif rl in ("attachment",):
            want.add("Attachment")
        elif rl in ("charm",):
            want.add("Charm")
        elif rl in ("raidertool", "raidertools"):
            want.add("RaiderTool")
        else:
            want.add(r.title())
    body_skin = getattr(scene, "arc_appearance_body_skin", "NONE") or "NONE"
    head_id = getattr(scene, "arc_appearance_head", "NONE") or "NONE"
    hair_id = getattr(scene, "arc_appearance_hair", "NONE") or "NONE"
    facial_hair_id = getattr(scene, "arc_appearance_facial_hair", "NONE") or "NONE"
    hair_colours = {}
    for hair_role in ("Hair", "FacialHair"):
        if hair_role not in want:
            continue
        colour_id = hair_color_choice(scene, hair_role)
        colour_path = resolve_hair_color_path(colour_id, scene) if colour_id != "NONE" else ""
        if colour_id != "NONE" and not colour_path:
            msgs.append(f"{hair_role}: Selected Hair Color MI could not be loaded from the Pioneer folder or Paks")
            want.discard(hair_role)
        hair_colours[hair_role] = colour_path
    do_replace = (
        bool(replace_selected)
        if replace_selected is not None
        else bool(getattr(scene, "arc_appearance_replace_selected", False))
    )

    jobs: list[tuple[str, str, str]] = []  # role, path, body_variant|hair hint
    if "Body" in want:
        _mesh_from_pak.last_reason = ""
        body_path = list_body_mesh()
        if not body_path:
            body_rel = ""
            for choice in _choices_from_csv("body") or []:
                body_rel = (choice.get("rel_path") or "").strip()
                if body_rel:
                    break
            body_path = locate_appearance_mesh(body_rel or "Characters/Shared/Bodies/SK_Body")
        if body_path:
            jobs.append(("Body", body_path, body_skin))
        else:
            msgs.append("Body mesh SK_Body not found under Characters/Shared/Bodies" + _pak_reason_suffix())
    if "Head" in want:
        if head_id == "NONE":
            msgs.append("Select a Head in the dropdown first")
        else:
            head_choice = resolve_choice(head_id)
            head_path = resolve_choice_path(head_id) or (head_choice or {}).get("path") or ""
            if not _mesh_file_usable(head_path):
                head_path = locate_appearance_mesh((head_choice or {}).get("rel_path") or "")
            if head_path:
                jobs.append(("Head", head_path, "NONE"))
            else:
                msgs.append("Selected head mesh missing — set PioneerGame Folder or the Paks folder")
    for hair_role, selected_hair_id in (("Hair", hair_id), ("FacialHair", facial_hair_id)):
        if hair_role not in want:
            continue
        if selected_hair_id == "NONE":
            msgs.append(f"Select a {hair_role} style in the dropdown first")
        else:
            choice = resolve_choice(selected_hair_id)
            hair_path = (choice or {}).get("path") or resolve_choice_path(selected_hair_id)
            if not _mesh_file_usable(hair_path):
                rel = (choice or {}).get("rel_path") or ""
                if not rel:
                    folder = (choice or {}).get("folder") or ""
                    segment = (choice or {}).get("segment") or "Hairs"
                    if folder:
                        rel = f"Characters/{segment}/{folder}/SK_{folder}.psk"
                hair_path = locate_appearance_mesh(rel)
            if hair_path:
                jobs.append((hair_role, hair_path, "NONE"))
            else:
                label = (choice or {}).get("label") or selected_hair_id
                reason = getattr(_mesh_from_pak, "last_reason", "") or ""
                detail = f" ({reason})" if reason else ""
                msgs.append(
                    f"Hair '{label}' has no mesh in the Pioneer folder or the paks{detail}"
                )

    backpack_parts = {}
    gear_want = {
        "Backpack": "backpack",
        "Attachment": "attachment",
        "Charm": "charm",
        "RaiderTool": "raider_tool",
    }
    for role_name, kind in gear_want.items():
        if role_name not in want:
            continue
        prop = _GEAR_SCENE_PROP[kind]
        choice_id = getattr(scene, prop, "NONE") or "NONE"
        if choice_id == "NONE":
            msgs.append(f"Select a {role_name.replace('RaiderTool', 'Raider Tool')} first")
            continue
        # Ensure gear catalog registered for resolve_choice.
        list_gear_choices(kind)
        choice = resolve_choice(choice_id)
        from ..Shared import pak_extract
        if role_name == "Backpack":
            from . import backpack_catalog
            colorway = getattr(scene, "arc_appearance_backpack_colorway", "BASE") or "BASE"
            planned = backpack_catalog.plan(choice or {}, colorway)
            if planned:
                for part in planned:
                    backpack_parts[os.path.normcase(os.path.abspath(part["mesh"]))] = part
                    jobs.append((role_name, part["mesh"], "NONE"))
                continue
        path = "" if pak_extract.configured_dir(scene) else ((choice or {}).get("path") or resolve_choice_path(choice_id))
        if not _mesh_file_usable(path):
            path = locate_appearance_mesh((choice or {}).get("rel_path") or "")
        if path:
            jobs.append((role_name, path, "NONE"))
        else:
            label = (choice or {}).get("label") or choice_id
            msgs.append(f"{role_name} '{label}' has no mesh in the Pioneer folder or the paks{_pak_reason_suffix()}")

    # Harness (visible straps) plus the rack the container hangs on.
    if any(r == "Backpack" for r, _p, _v in jobs):
        strap_path = resolve_backpack_strap_mesh() or locate_appearance_mesh(_BACKPACK_STRAP_RELS[0])
        if strap_path:
            jobs.insert(0, ("BackpackStrap", strap_path, "NONE"))
        else:
            msgs.append(
                "Backpack straps SK_Default_Strap not found under "
                "Characters/Backpacks/Straps/Default/Strap" + _pak_reason_suffix()
            )
        frame_path = resolve_backpack_frame_mesh() or locate_appearance_mesh(_BACKPACK_FRAME_RELS[0])
        if frame_path:
            jobs.insert(0, ("BackpackFrame", frame_path, "NONE"))
        else:
            msgs.append(
                "Backpack frame SM_Backpack_Frame_01_A not found under "
                "Characters/Backpacks/Frames/Default" + _pak_reason_suffix()
            )

    if not standalone and attach_to is None and any(r in _GEAR_ATTACH_BONE or r == "BackpackStrap" for r, _p, _v in jobs):
        # Same pad as the outfit: hang the rack on slot bones instead of the feet.
        if any(r == "Backpack" for r, _p, _v in jobs):
            attach_to = armature_on_first_slot_missing("Backpack")
        else:
            # Accessories belong to the selected/existing rack, including one
            # already carrying a container. Only a new backpack needs a free pad.
            attach_to = find_target_armature(context) or _reusable_strap_armature(require_empty_backpack=False)

    if not jobs:
        return 0, msgs or ["Nothing selected to import"], []

    prefer_slot = layout_slot
    if do_replace:
        # Prefer the outfit armature even when not merging (Add off) so we can
        # find the old part on that pad from a clothing-mesh selection.
        pad_arm = attach_to if attach_to is not None else find_target_armature(context)
        if pad_arm is not None:
            from . import import_layout as _layout

            prefer_slot = _layout.slot_of_object(pad_arm)
            if prefer_slot is None and _layout.offset_enabled(context.scene):
                spacing = _layout.slot_spacing_m(context.scene)
                wx = float(pad_arm.matrix_world.translation.x)
                if spacing > 1e-6:
                    prefer_slot = max(0, int(round(wx / spacing)))
        replace_roles = {r for r, _p, _v in jobs}
        # Swapping a container also swaps the rack on that pad.
        if "Backpack" in replace_roles:
            replace_roles.add("BackpackFrame")
            replace_roles.add("BackpackStrap")
        if "Charm" in replace_roles:
            replace_roles.add("CharmConnector")
        for role in replace_roles:
            victims = find_selected_role_objects(context, role, attach_to=pad_arm)
            if not victims and role in ("BackpackFrame", "BackpackStrap", "CharmConnector"):
                continue  # harness/rack optional on first replace
            if not victims and role not in ("BackpackFrame",):
                msgs.append(
                    f"Replace Selected: no {role} part on the selected outfit "
                    f"(select the outfit/part, or turn Replace Selected off)"
                )
                return 0, msgs, []
            if victims:
                msgs.extend(remove_appearance_objects(victims, keep_armature=pad_arm))

    before = set(bpy.data.objects)
    # Track slot role per imported batch (Hair vs FacialHair).
    placed_roles: list[str] = []
    role_meshes: dict[str, list] = {}
    for role, path, variant in jobs:
        hair_mi = "NONE"
        body_variant = "NONE"
        slot_role = role
        if role == "Body":
            body_variant = variant
        elif role in ("Hair", "FacialHair"):
            from ..Shared import textures as _tex

            selected_hair_id = facial_hair_id if role == "FacialHair" else hair_id
            choice = resolve_choice(selected_hair_id) if selected_hair_id != "NONE" else None
            seg = str((choice or {}).get("segment") or "")
            if seg.casefold() == "facialhairs" or "/facialhairs/" in path.replace("\\", "/").casefold():
                slot_role = "FacialHair"
            mis = _tex.scan_hair_mis(path) if hasattr(_tex, "scan_hair_mis") else []
            if mis:
                hair_mi = mis[0][1]
        before_defer = len(defer_materials) if defer_materials is not None else 0
        post_material = None
        if role == "Backpack" and defer_materials is not None:
            from . import backpack_catalog as _bc
            _part = backpack_parts.get(os.path.normcase(os.path.abspath(path)))
            if _part:
                post_material = lambda o, _p=_part: _bc.apply_material_bindings(o, _p)
        ok, msg, objs = import_appearance_mesh(
            path,
            body_variant=body_variant,
            hair_mi=hair_mi, import_slot=layout_slot,
            hair_colour_json=hair_colours.get(role),
            defer_materials=defer_materials,
            post_material=post_material,
        )
        if not ok:
            msgs.append(f"{role}: {msg}")
            continue
        for obj in objs:
            if not _object_alive(obj):
                continue
            _stamp_role(obj, slot_role, path)
            if role == "Body" and body_variant != "NONE":
                try:
                    obj["arc_body_variant"] = body_variant
                except Exception:
                    pass
        if role == "Backpack":
            from . import backpack_catalog
            part = backpack_parts.get(os.path.normcase(os.path.abspath(path)))
            # Staged path: bindings were queued with the material step, unless the inline
            # fallback ran (nothing queued), in which case bind now.
            queued_now = defer_materials is not None and len(defer_materials) > before_defer
            for obj in objs:
                if _object_alive(obj) and obj.type == "MESH":
                    if part:
                        if not queued_now:
                            backpack_catalog.apply_material_bindings(obj, part)
                        obj["arc_gear_attach_bone"] = part["socket"]
                    _gear_source_socket_bind(obj, str(obj.get("arc_gear_attach_bone") or "slot_container"))
        if role == "Charm" and _charm_needs_connector(objs):
            connector_host = attach_to if attach_to is not None else (None if standalone else _reusable_strap_armature(require_empty_backpack=False))
            if not _charm_connector_on_armature(connector_host):
                connector = _resolve_pioneer_mesh_rel(_BACKPACK_CHARM_CONNECTOR_RELS)
                if connector:
                    jobs.append(("CharmConnector", connector, "NONE"))
                else:
                    msgs.append("Charm connector SM_BackpackCharm_Karabiner not found in the Pioneer folder or paks")
        if role == "BackpackStrap":
            objs = _bind_strap_armature(path, objs, reuse_existing=not standalone)
        alive = [o for o in objs if _object_alive(o)]
        new_objects.extend(alive)
        role_meshes.setdefault(slot_role, []).extend(
            o for o in alive if o.type == "MESH"
        )
        placed_roles.append(slot_role)
        label = "Facial Hair" if slot_role == "FacialHair" else role
        msgs.append(f"{label}: {os.path.basename(path)}")
    imported = [o for o in bpy.data.objects if o not in before and _object_alive(o)]
    if not new_objects:
        new_objects = imported

    needs_backpack_slots = any(
        r in _GEAR_ATTACH_BONE for r in placed_roles
    )
    if attach_to is not None and new_objects:
        try:
            if not _object_alive(attach_to):
                raise ValueError("Target armature was removed")
            strap_arms = [
                o for o in new_objects
                if _object_alive(o)
                and o.type == "ARMATURE"
                and str(o.get("arc_assembly_role") or "") == "BackpackStrap"
            ]
            strap_meshes = [
                o for o in role_meshes.get("BackpackStrap", [])
                if _object_alive(o)
            ]
            merge_set = [o for o in new_objects if o not in strap_arms]
            if needs_backpack_slots and not strap_arms:
                ok_bones, bone_msgs = ensure_backpack_slot_bones(attach_to)
                msgs.extend(bone_msgs)
                if not ok_bones:
                    msgs.append("Gear will attach without slot bones (origin pose)")
            merge_into_armature(
                attach_to,
                merge_set,
                snap_skinned_world=not needs_backpack_slots,
            )
            if strap_arms:
                msgs.extend(adopt_backpack_strap(attach_to, strap_arms + strap_meshes))
            new_objects = [o for o in new_objects if _object_alive(o)]
            # Socket-parent rack + container (+ attachment/charm) onto strap slots.
            for role_name, bone_name in _GEAR_ATTACH_BONE.items():
                meshes = [
                    o for o in role_meshes.get(role_name, [])
                    if _object_alive(o)
                ]
                if not meshes:
                    continue
                n = bone_parent_to_slot(meshes, attach_to, bone_name)
                if n:
                    msgs.append(f"{role_name} → bone '{bone_name}' ({n})")
            msgs.append(f"Attached to armature '{attach_to.name}'")
        except Exception as exc:
            msgs.append(f"Attach failed: {exc}")
    elif needs_backpack_slots and new_objects:
        # No outfit in the scene: hang gear on the strap skeleton's rest pose.
        strap_arms = [
            o for o in new_objects
            if _object_alive(o) and o.type == "ARMATURE"
            and str(o.get("arc_assembly_role") or "") == "BackpackStrap"
        ]
        if not strap_arms:
            host, note = _default_slot_armature(reuse_existing=not standalone)
            if host is not None:
                strap_arms = [host]
                new_objects.append(host)
                msgs.append("Placed on the default strap skeleton")
            elif note:
                msgs.append(note)
        if strap_arms:
            host_arm = strap_arms[0]
            for role_name, bone_name in _GEAR_ATTACH_BONE.items():
                meshes = [o for o in role_meshes.get(role_name, []) if _object_alive(o)]
                n = bone_parent_to_slot(meshes, host_arm, bone_name)
                if n:
                    msgs.append(f"{role_name} → strap bone '{bone_name}' ({n})")
                elif meshes and bone_name not in host_arm.data.bones:
                    msgs.append(f"{role_name} slot bone '{bone_name}' is missing")
        else:
            frames = [o for o in role_meshes.get("BackpackFrame", []) if _object_alive(o)]
            packs = [o for o in role_meshes.get("Backpack", []) if _object_alive(o)]
            if frames and packs:
                host = frames[0]
                for pack in packs:
                    try:
                        mw = pack.matrix_world.copy()
                        pack.parent = host
                        pack.matrix_world = mw
                    except Exception:
                        pass

    # Part centers as scale pivots (skeleton stays at feet). Appearance imports
    # often skip fix_rig_all, so recenter here too. Skip bone-parented gear —
    # recentering breaks zero-local socket attachment.
    try:
        from ..Animations import rig as _rig

        alive_meshes = [
            o for o in new_objects
            if _object_alive(o)
            and o.type == "MESH"
            and o.parent_type != "BONE"
            and str(o.get("arc_assembly_role") or "") not in set(_GEAR_ATTACH_BONE) | {"BackpackStrap"}
        ]
        created = _rig.recenter_imported_mesh_origins(alive_meshes)
        if created:
            new_objects.extend(c for c in created if _object_alive(c))
    except Exception as exc:
        msgs.append(f"Origin recenter skipped: {exc}")

    new_objects = [o for o in new_objects if _object_alive(o)]

    # Character pad layout (2 m default): one Body/Head/Hair/FacialHair/Outfit each.
    try:
        from . import import_layout as _layout

        layout_role = "Backpack" if "Backpack" in placed_roles else _layout.normalize_role_name(
            placed_roles[0] if placed_roles else "Body"
        )
        if prefer_slot is None:
            for obj in new_objects:
                if (
                    _object_alive(obj)
                    and obj.type == "ARMATURE"
                    and str(obj.get("arc_assembly_role") or "") == "BackpackStrap"
                    and "arc_import_slot" in obj
                ):
                    try:
                        prefer_slot = int(obj.get("arc_import_slot"))
                    except (TypeError, ValueError):
                        prefer_slot = None
                    break
        slot = _layout.place_imported(
            new_objects,
            layout_role,
            scene=context.scene,
            prefer_slot=prefer_slot,
            attach_to=attach_to if attach_to is not None and _object_alive(attach_to) else None,
        )
        if _layout.offset_enabled(context.scene):
            msgs.append(
                f"Slot {slot} @ X={_layout.slot_world_x(slot, context.scene):.1f} m"
            )
    except Exception as exc:
        msgs.append(f"Slot layout skipped: {exc}")

    # Static styles are authored relative to the head, unlike skeletal cards.
    # Apply after origin recenter and character-pad placement so both frames are known.
    from . import hair_placement
    hair_targets = [o for o in new_objects if o.type == "MESH" and str(o.get("arc_assembly_role") or "") in {"Hair", "FacialHair"}]
    if "Head" in placed_roles or "Body" in placed_roles:
        hair_targets.extend(o for o in context.scene.objects if o.type == "MESH" and o not in hair_targets and str(o.get("arc_assembly_role") or "") in {"Hair", "FacialHair"})
    for hair in hair_targets:
        try:
            placed, note = hair_placement.place_hair_at_head(hair, context.scene)
            if placed:
                msgs.append(note)
        except (ValueError, OSError) as exc:
            msgs.append(f"Static hair attachment skipped: {exc}")

    # A user-requested appearance import may need new cloth metadata on an
    # existing paired head/facial mesh. Extract here on the main thread, never
    # from registration, file-load or depsgraph reconciliation.
    if set(placed_roles).intersection({"Body", "Head", "Hair", "FacialHair"}):
        from . import character_fit, head_wrap, import_layout as _layout
        fit_objects = [o for o in new_objects if o.type == "MESH" and not o.get("arc_reference_plane")]
        fit_slots = {_layout.slot_of_object(o) for o in fit_objects}
        fit_slots.discard(None)
        fit_objects.extend(o for o in context.scene.objects if o.type == "MESH" and o not in fit_objects and not o.get("arc_reference_plane") and _layout.slot_of_object(o) in fit_slots and _layout.infer_role(o) in {"Head", "Hair", "FacialHair"})
        try:
            refresh = head_wrap.ensure_metadata(context.scene, fit_objects)
            if refresh.get("error"):
                msgs.append("Head-fit metadata: " + refresh["error"])
            character_fit.sync_scene(context.scene)
        except (OSError, ValueError, RuntimeError) as exc:
            msgs.append(f"Authored head fit skipped: {exc}")

    # Bone shapes on the rig that now holds the new bones. A head brings the
    # face bones, so its rig stays visible; other standalone rigs are hidden.
    has_head = "Head" in placed_roles
    rig_objs = [attach_to] if attach_to is not None else [
        o for o in new_objects if _object_alive(o) and o.type == "ARMATURE"
    ]
    for obj in rig_objs:
        if not _object_alive(obj) or obj.type != "ARMATURE":
            continue
        try:
            rig.apply_bone_display(obj)
            if has_head:
                obj.hide_viewport = False
                obj.hide_set(False)
            elif attach_to is None:
                obj.hide_set(True)
                obj.hide_viewport = True
        except Exception:
            pass

    if defer_materials is None and any(role in {"BackpackFrame", "BackpackStrap"} for role in placed_roles):
        # Staged path shares after the last material step (iter_appearance_parts).
        from . import backpack_instances
        shared = backpack_instances.share_backpack_data(new_objects, scene=context.scene)
        if shared["meshes_linked"] or shared["materials_linked"]:
            msgs.append("Shared identical backpack data: " + str(shared["meshes_linked"]) + " meshes, " + str(shared["materials_linked"]) + " materials")
    if "CharmConnector" in placed_roles:
        from . import backpack_instances
        shared = backpack_instances.share_charm_connector_data(new_objects, scene=context.scene)
        if shared["meshes_linked"] or shared["materials_linked"]:
            msgs.append("Shared identical charm connector data: " + str(shared["meshes_linked"]) + " meshes, " + str(shared["materials_linked"]) + " materials")
    mesh_count = sum(
        1 for o in new_objects if _object_alive(o) and o.type == "MESH"
    )
    return mesh_count, msgs, [o for o in new_objects if _object_alive(o)]


def repair_backpack_assemblies(context, objects=None, *, add_missing_parts=True, colorway_id=None):
    """Repair selected/all existing backpack assemblies without moving their host."""
    from . import backpack_catalog
    choices = list_gear_choices("backpack")
    meshes = [obj for obj in (list(objects) if objects is not None else list(bpy.data.objects))
              if _object_alive(obj) and obj.type == "MESH" and obj.get("arc_assembly_role") == "Backpack"]
    groups = {}
    for obj in meshes:
        source = str(obj.get("arc_source_mesh") or obj.get("arc_psk_path") or "").replace("\\", "/")
        marker = "/characters/backpacks/containers/"
        if marker not in source.casefold():
            continue
        folder = source[source.casefold().index(marker) + len(marker):].split("/")[0]
        choice = next((value for value in choices if str(value.get("folder") or "").casefold() == folder.casefold()), None)
        if not choice:
            continue
        host = obj.parent if obj.parent and obj.parent.type == "ARMATURE" else None
        if host is None:
            continue
        groups.setdefault((host, choice['id']), (choice, []))[1].append(obj)
    # A selected section identifies the whole authored assembly. Include its
    # already imported siblings before filling missing parts or applying colour.
    for (host, _id), (choice, existing) in groups.items():
        folder_marker = "/characters/backpacks/containers/" + str(choice.get("folder") or "").casefold() + "/"
        for obj in bpy.data.objects:
            if obj in existing or obj.type != "MESH" or obj.parent != host or obj.get("arc_assembly_role") != "Backpack":
                continue
            source = str(obj.get("arc_source_mesh") or obj.get("arc_psk_path") or "").replace("\\", "/").casefold()
            if obj.get("arc_backpack_choice") == _id or folder_marker in source:
                existing.append(obj)
    messages, created = [], []
    for (host, _id), (choice, existing) in groups.items():
        selected = colorway_id if colorway_id is not None else str(existing[0].get("arc_backpack_colorway") or "BASE")
        planned = backpack_catalog.plan(choice, selected)
        if not planned:
            for obj in existing:
                bone_parent_to_slot([obj], host, 'slot_container')
            continue
        available = {os.path.basename(str(obj.get("arc_source_mesh") or obj.get("arc_psk_path") or "")).split('.')[0].casefold(): obj for obj in existing}
        slot = existing[0].get("arc_import_slot", host.get("arc_import_slot", 0))
        for part in planned:
            stem = os.path.basename(part['mesh']).split('.')[0].casefold()
            obj = available.get(stem)
            if obj is None and add_missing_parts:
                ok, message, imported = import_appearance_mesh(part['mesh'], import_slot=int(slot))
                if not ok:
                    messages.append(message)
                    continue
                new_meshes = [value for value in imported if _object_alive(value) and value.type == "MESH"]
                if not new_meshes:
                    continue
                obj = new_meshes[0]
                for value in imported:
                    _stamp_role(value, "Backpack", part['mesh'])
                    value['arc_import_slot'] = int(slot)
                created.extend(imported)
                available[stem] = obj
                messages.append("Added authored backpack part " + os.path.basename(part['mesh']))
            if obj is None:
                continue
            obj['arc_gear_attach_bone'] = part['socket']
            backpack_catalog.apply_material_bindings(obj, part)
            bone_parent_to_slot([obj], host, part['socket'])
        messages.append("Rebound " + str(choice.get('label') or choice['id']) + " with authored socket bind and colour " + selected)
    bpy.context.view_layer.update()
    return messages, created
