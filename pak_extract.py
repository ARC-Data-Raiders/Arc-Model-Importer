"""Pull a mesh out of shipping Arc Raiders paks and import it as .uemodel.

The extractor is ArcPak.Export.exe (self-contained). AES and the Arc 5.7
profile stay inside that exe. This module only passes the paks folder, usmap,
and asset path, then imports the written mesh with the existing material path.
"""

from __future__ import annotations

import os
import subprocess
import hashlib
import json

import bpy
from bpy.props import CollectionProperty, StringProperty
from bpy.types import Operator, OperatorFileListElement
from bpy_extras.io_utils import ImportHelper

from . import appearance, textures, utils

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEFAULT_PAK_DIR = (
    r"C:\Program Files (x86)\Steam\steamapps\common\Arc Raiders"
    r"\PioneerGame\Content\Paks"
)
_DEFAULT_PAK_HAS_FILES = None
BUNDLED_USMAP_FILENAME = "ArcRaiders_2.1.0_inheritance_fixed.usmap"


def _addon_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def exe_path() -> str:
    root = os.path.join(_addon_dir(), "ArcPak")
    candidates = (
        os.path.join(root, "ArcPak.Export.exe"),
        os.path.join(root, "publish", "ArcPak.Export.exe"),
        os.path.join(root, "bin", "ArcPak.Export", "Release", "net10.0", "win-x64", "ArcPak.Export.exe"),
        os.path.join(root, "bin", "ArcPak.Export", "Release", "net10.0", "ArcPak.Export.exe"),
        os.path.join(root, "bin", "ArcPak.Export", "Debug", "net10.0", "ArcPak.Export.exe"),
    )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def bundled_usmap() -> str:
    folder = os.path.join(_addon_dir(), "ArcPak", "mappings")
    if not os.path.isdir(folder):
        return ""
    preferred = os.path.join(folder, BUNDLED_USMAP_FILENAME)
    if os.path.isfile(preferred):
        return preferred
    hits = [
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.lower().endswith(".usmap")
    ]
    hits.sort()
    return hits[-1] if hits else ""


def _extraction_revision(scene=None) -> str:
    if scene is None:
        scene = bpy.context.scene
    mapping = resolve_usmap(scene)
    if not mapping or not os.path.isfile(mapping):
        return ""
    with open(mapping, "rb") as stream:
        return "2.19.12:" + hashlib.sha256(stream.read()).hexdigest()


def mesh_cache_is_current(path: str, scene=None) -> bool:
    try:
        with open(path + ".arc-extract.json", "r", encoding="utf-8") as stream:
            record = json.load(stream)
        return bool(record.get("revision")) and record["revision"] == _extraction_revision(scene)
    except (OSError, ValueError):
        return False


def cache_dir() -> str:
    path = os.path.join(_addon_dir(), "ArcPak", "cache")
    os.makedirs(path, exist_ok=True)
    return path


def index_dir() -> str:
    path = os.path.join(cache_dir(), "indexes")
    os.makedirs(path, exist_ok=True)
    return path


def pak_dir_has_game_files(path: str) -> bool:
    """True when the folder contains a shipping pakchunk container."""
    if not path or not os.path.isdir(path):
        return False
    try:
        names = os.listdir(path)
    except OSError:
        return False
    for name in names:
        low = name.casefold()
        if low.endswith(".pak") or (low.endswith(".utoc") and low != "global.utoc"):
            return True
    return False


def default_pak_has_files() -> bool:
    return pak_dir_has_game_files(DEFAULT_PAK_DIR)


def reconcile_pak_dir(scene, *, restore_default: bool = False) -> None:
    """Use the Steam Paks folder when it has game files. Clear that default when it does not."""
    if scene is None or not hasattr(scene, "arc_pak_dir"):
        return
    current = getattr(scene, "arc_pak_dir", "") or ""
    has = default_pak_has_files()
    norm_current = os.path.normcase(os.path.normpath(bpy.path.abspath(current))) if current else ""
    norm_default = os.path.normcase(os.path.normpath(DEFAULT_PAK_DIR))
    if current and not pak_dir_has_game_files(bpy.path.abspath(current)):
        scene.arc_pak_dir = ""
        scene["arc_pak_dir_user_cleared"] = True
        return
    if restore_default and not current:
        scene["arc_pak_dir_user_cleared"] = False
    if has and not current and not scene.get("arc_pak_dir_user_cleared"):
        scene.arc_pak_dir = DEFAULT_PAK_DIR


def configured_dir(scene) -> str:
    """Paks folder when it exists and contains game containers."""
    path = bpy.path.abspath(getattr(scene, "arc_pak_dir", "") or "") if scene is not None else ""
    if path and pak_dir_has_game_files(path):
        return path
    return ""


def _initialize_pak_defaults():
    # addon_utils.enable() registers addons under _RestrictData. Scene data
    # becomes available after registration returns to Blender's event loop.
    scenes = getattr(bpy.data, "scenes", None)
    if scenes is None:
        return 0.1
    for scene in scenes:
        reconcile_pak_dir(scene, restore_default=True)
    return None


@bpy.app.handlers.persistent
def _load_pak_defaults(_unused=None):
    _initialize_pak_defaults()


def register_defaults():
    # Scene writes belong in registration/load handlers, never panel draw().
    unregister_defaults()
    bpy.app.handlers.load_post.append(_load_pak_defaults)
    delay = _initialize_pak_defaults()
    if delay is not None:
        bpy.app.timers.register(_initialize_pak_defaults, first_interval=delay)


def unregister_defaults():
    if bpy.app.timers.is_registered(_initialize_pak_defaults):
        bpy.app.timers.unregister(_initialize_pak_defaults)
    for handler in list(bpy.app.handlers.load_post):
        if getattr(handler, "__module__", "") == __name__ and getattr(handler, "__name__", "") == "_load_pak_defaults":
            bpy.app.handlers.load_post.remove(handler)


def game_path(rel: str) -> str:
    """Turn a Pioneer-relative or cooked path into the form the extractor mounts."""
    text = (rel or "").replace("\\", "/").strip().lstrip("/")
    leaf = text.split("/")[-1]
    if "." in leaf:
        text = text[: -len(leaf)] + leaf.split(".", 1)[0]
        leaf = text.split("/")[-1]
    for ext in (".psk", ".pskx", ".uemodel", ".uasset", ".json", ".png"):
        if text.casefold().endswith(ext):
            text = text[: -len(ext)]
            break
    low = text.casefold()
    if low.startswith("pioneergame/"):
        return text
    if low.startswith("game/"):
        return "PioneerGame/Content/" + text[len("game/") :]
    if low.startswith("content/pioneer/"):
        return "PioneerGame/" + text
    return "PioneerGame/Content/Pioneer/" + text


def resolve_usmap(scene) -> str:
    chosen = bpy.path.abspath(getattr(scene, "arc_usmap_path", "") or "")
    if chosen and os.path.isfile(chosen):
        return chosen
    return bundled_usmap()


def _normalize_asset_path(path: str) -> str:
    text = (path or "").strip().replace("\\", "/")
    if text.lower().endswith(".uasset"):
        text = text[: -len(".uasset")]
    return text


def _package_aliases(asset_path: str) -> list[str]:
    clean = _normalize_asset_path(asset_path).lstrip("/")
    if "." in clean.rsplit("/", 1)[-1]:
        clean = clean.rsplit(".", 1)[0]
    aliases = [clean]
    for prefix in ("PioneerGame/Content/", "RemappedPlugins/Content/", "Game/"):
        if clean.casefold().startswith(prefix.casefold()):
            tail = clean[len(prefix):]
            aliases.extend(root + tail for root in ("PioneerGame/Content/", "RemappedPlugins/Content/"))
            break
    return list(dict.fromkeys(aliases))


def _expected_uemodel(out_dir: str, asset_path: str) -> str:
    from . import pak_catalog
    candidates = []
    for rel in _package_aliases(asset_path):
        leaf = rel.rsplit("/", 1)[-1]
        if not leaf:
            continue
        direct = os.path.join(out_dir, *rel.split("/"))
        for candidate in (direct + ".uemodel", os.path.join(direct, leaf + ".uemodel")):
            candidate = pak_catalog.filesystem_path(candidate)
            if os.path.isfile(candidate):
                candidates.append(candidate)
                continue
            folder = os.path.dirname(candidate)
            if not os.path.isdir(folder):
                continue
            target = os.path.basename(candidate).casefold()
            for name in os.listdir(folder):
                if name.casefold() == target:
                    candidates.append(os.path.join(folder, name))
    # An extraction may change mount aliases across builds. Prefer its latest
    # output while retaining exact package identity; never search by basename.
    return max(candidates, key=lambda p: os.stat(p).st_mtime_ns) if candidates else ""


def _collect_uemodels(out_dir: str, asset_paths: list[str]) -> list[str]:
    from . import pak_catalog
    found = {}

    def add(path):
        path = pak_catalog.filesystem_path(pak_catalog.display_path(path))
        relative = os.path.relpath(pak_catalog.display_path(path), pak_catalog.display_path(out_dir)).replace("\\", "/")
        package = pak_catalog.canonical_path(relative[:-len(".uemodel")]).casefold()
        previous = found.get(package)
        if previous is None or os.stat(path).st_mtime_ns > os.stat(previous).st_mtime_ns:
            found[package] = path

    for asset in asset_paths:
        direct = _expected_uemodel(out_dir, asset)
        if direct:
            add(direct)
            continue
        for clean in _package_aliases(asset):
            folder = os.path.join(out_dir, *clean.split("/")) if clean else ""
            if not folder:
                continue
            folder = str(pak_catalog._io_path(folder))
            if not os.path.isdir(folder):
                continue
            for dirpath, _dirs, files in os.walk(folder):
                for name in files:
                    low = name.casefold()
                    if low.endswith(".uemodel") and pak_catalog.is_mesh(low[:-len(".uemodel")]):
                        add(os.path.join(dirpath, name))
    return list(found.values())


def load_pak_index(scene, *, allow_reindex: bool = False):
    from . import pak_catalog
    game = configured_dir(scene)
    if not game:
        return None
    index = pak_catalog.find_index(game, index_dir())
    if index is None and allow_reindex:
        code, log = reindex(game, resolve_usmap(scene))
        if code:
            raise RuntimeError(_log_tail(log))
        index = pak_catalog.find_index(game, index_dir())
    return index


def indexed_package_paths(scene, *, mesh_only: bool = False) -> tuple[str, ...]:
    from . import pak_catalog
    index = load_pak_index(scene)
    if index is None:
        return ()
    return pak_catalog.mesh_paths(index) if mesh_only else index["paths"]


def indexed_mesh_paths(scene) -> tuple[str, ...]:
    return indexed_package_paths(scene, mesh_only=True)


def ensure_pak_browser(scene) -> tuple[dict, str]:
    from . import pak_catalog
    index = load_pak_index(scene, allow_reindex=True)
    if index is None:
        raise RuntimeError("No current Pak mesh index. Load / Refresh Paks in Settings.")
    return index, pak_catalog.browser_tree(index, cache_dir(), configured_dir(scene))


def extraction_command(game_dir: str, usmap: str, paths: list[str], *, out_dir=None) -> list[str]:
    """Build extraction arguments on the main thread; workers receive strings only."""
    exe = exe_path()
    if not exe:
        raise FileNotFoundError("ArcPak.Export.exe is missing; reinstall the packaged addon")
    cmd = [exe, "extract", "--game-dir", game_dir, "--usmap", usmap,
           "--out", out_dir or cache_dir(), "--index-dir", index_dir()]
    for asset in paths:
        cmd.extend(["--path", asset])
    bundled_index = os.path.join(_addon_dir(), "ArcPak", "mappings", "pak-index.bin")
    if os.path.isfile(bundled_index):
        cmd.extend(["--index", bundled_index])
    return cmd


def complete_extraction(out_dir, paths, code, log, revision):
    """Finalize cache state on the main thread after an exporter has finished."""
    global _LAST_EXTRACT_LOG
    _LAST_EXTRACT_LOG = log
    meshes = _collect_uemodels(out_dir, paths)
    if code == 0:
        for mesh in meshes:
            with open(mesh + ".arc-extract.json", "w", encoding="utf-8") as stream:
                json.dump({"revision": revision}, stream)
    utils.set_pak_cache_root(out_dir)
    textures.clear_clothing_path_caches()
    return meshes


def extract_paths(game_dir: str, usmap: str, asset_paths: list[str]) -> tuple[int, str, list[str]]:
    """Extract packages/folders, then finalize their material/mesh caches."""
    paths = [path for path in (asset_paths or []) if path]
    if not paths:
        return 1, "no asset path", []
    out_dir = cache_dir()
    command = extraction_command(game_dir, usmap, paths, out_dir=out_dir)
    proc = subprocess.run(command, capture_output=True, text=True, timeout=600,
                          creationflags=_CREATE_NO_WINDOW)
    log = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    meshes = complete_extraction(out_dir, paths, proc.returncode, log, _extraction_revision())
    return proc.returncode, log, meshes


def extract_asset(game_dir: str, usmap: str, asset_path: str) -> tuple[int, str, str]:
    """Run the helper for one path. Returns (exit code, log, first uemodel)."""
    code, log, meshes = extract_paths(game_dir, usmap, [asset_path])
    return code, log, meshes[0] if meshes else ""


def ensure_rels(scene, rels: list[str]) -> list[str]:
    """Extract Pioneer-relative paths when the paks folder is set. Empty if it is not."""
    game = configured_dir(scene)
    if not game:
        return []
    usmap = resolve_usmap(scene)
    if not usmap:
        return []
    paths = []
    seen = set()
    for rel in rels or []:
        cooked = game_path(rel)
        key = cooked.casefold()
        if not cooked or key in seen:
            continue
        seen.add(key)
        paths.append(cooked)
    if not paths:
        return []
    _code, log, meshes = extract_paths(game, usmap, paths)
    if not meshes:
        print(f"Arc Pak: {_log_tail(log)}")
    return meshes


def reindex(game_dir: str, usmap: str = "") -> tuple[int, str]:
    """Mount every container, write a pak index, and cache sidebar preview icons."""
    exe = exe_path()
    if not exe:
        raise FileNotFoundError(
            "ArcPak.Export.exe is not next to the addon. Publish ArcPak or reinstall the packaged addon."
        )
    cmd = [
        exe,
        "reindex",
        "--game-dir",
        game_dir,
        "--index-dir",
        index_dir(),
        "--out",
        cache_dir(),
    ]
    if usmap and os.path.isfile(usmap):
        cmd.extend(["--usmap", usmap])
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
        creationflags=_CREATE_NO_WINDOW,
    )
    log = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if proc.returncode == 0:
        utils.set_pak_cache_root(cache_dir())
        from . import pak_catalog
        index = pak_catalog.find_index(game_dir, index_dir())
        if index is not None:
            root = pak_catalog.browser_tree(index, cache_dir(), game_dir)
            log += f"\nPak browser ready: {len(pak_catalog.mesh_paths(index))} meshes ({root})"
    return proc.returncode, log


def _log_tail(log: str, limit: int = 6) -> str:
    lines = [ln.strip() for ln in (log or "").splitlines() if ln.strip()]
    if not lines:
        return "extract failed"
    return " | ".join(lines[-limit:])


class ARC_OT_pick_pak_dir(Operator, ImportHelper):
    """Folder that contains the shipping .utoc/.ucas files (PioneerGame/Content/Paks)."""

    bl_idname = "arc_outfits.pick_pak_dir"
    bl_label = "Select Paks Folder"
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={"HIDDEN"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})

    def execute(self, context):
        folder = utils.resolved_picked_dir(
            bpy.path.abspath(getattr(self, "filepath", "") or ""),
            bpy.path.abspath(getattr(self, "directory", "") or ""),
        )
        if not pak_dir_has_game_files(folder):
            context.scene.arc_pak_dir = ""
            context.scene["arc_pak_dir_user_cleared"] = True
            self.report({"ERROR"}, "Select a folder containing game .pak or .utoc files")
            return {"CANCELLED"}
        context.scene.arc_pak_dir = folder
        context.scene["arc_pak_dir_user_cleared"] = False
        context.window_manager.cursor_set("WAIT")
        try:
            index, _root = ensure_pak_browser(context.scene)
            from . import pak_catalog
            self.report({"INFO"}, f"Paks loaded: {len(pak_catalog.mesh_paths(index))} meshes available")
        except Exception as exc:
            self.report({"WARNING"}, f"Paks folder saved; catalog could not load: {exc}")
        finally:
            context.window_manager.cursor_set("DEFAULT")
        return {"FINISHED"}


class ARC_OT_clear_pak_dir(Operator):
    bl_idname = "arc_outfits.clear_pak_dir"
    bl_label = "Clear Paks Folder"

    def execute(self, context):
        context.scene.arc_pak_dir = ""
        context.scene["arc_pak_dir_user_cleared"] = True
        return {"FINISHED"}


class ARC_OT_pick_usmap(Operator, ImportHelper):
    """Optional newer .usmap. Leave empty to use the file shipped with the addon."""

    bl_idname = "arc_outfits.pick_usmap"
    bl_label = "Select Usmap"
    filename_ext = ".usmap"
    filter_glob: StringProperty(default="*.usmap", options={"HIDDEN"})

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(path):
            self.report({"ERROR"}, "Not a usmap file")
            return {"CANCELLED"}
        context.scene.arc_usmap_path = path
        return {"FINISHED"}


class ARC_OT_clear_usmap(Operator):
    bl_idname = "arc_outfits.clear_usmap"
    bl_label = "Use Bundled Usmap"

    def execute(self, context):
        context.scene.arc_usmap_path = ""
        return {"FINISHED"}


class ARC_OT_reindex_paks(Operator):
    """Read the Steam build id, rebuild the pak index, and cache preview icons.

    The next import of the same build mounts only the containers that asset needs.
    """

    bl_idname = "arc_outfits.reindex_paks"
    bl_label = "Load / Refresh Paks"
    bl_options = {"REGISTER"}

    def execute(self, context):
        reconcile_pak_dir(context.scene)
        game_dir = configured_dir(context.scene)
        if not game_dir:
            self.report({"ERROR"}, "Set the Paks folder in Settings")
            return {"CANCELLED"}
        try:
            code, log = reindex(game_dir, resolve_usmap(context.scene))
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if code != 0:
            self.report({"ERROR"}, _log_tail(log))
            return {"CANCELLED"}
        self.report({"INFO"}, _log_tail(log, limit=2))
        return {"FINISHED"}


class ARC_OT_import_from_pak(Operator, ImportHelper):
    """Extract a mesh from the Arc Raiders paks and import it.

    Static meshes come in with materials. Skinned meshes import without skin weights.
    """

    bl_idname = "arc_outfits.import_from_pak"
    bl_label = "Import from Pak"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".uasset"
    filter_glob: StringProperty(default="*.uasset", options={"HIDDEN"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})
    files: CollectionProperty(type=OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    browser_root: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    asset_path: StringProperty(
        name="Asset Path",
        description="Cooked object path or folder, for example PioneerGame/Content/Pioneer/Characters/Assets/Abyss/Airbag",
        default="",
    )

    def invoke(self, context, event):
        if self.asset_path:
            return self.execute(context)
        try:
            _index, root = ensure_pak_browser(context.scene)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.browser_root = root
        self.directory = root + os.sep
        self.filepath = self.directory
        self.files.clear()
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context):
        self.layout.label(text="Select meshes, or import the current folder.", icon="MESH_DATA")

    def execute(self, context):
        scene = context.scene
        if self.asset_path:
            assets = [_normalize_asset_path(self.asset_path)]
        else:
            try:
                from . import pak_catalog
                index, root = ensure_pak_browser(scene)
                if self.browser_root and os.path.normcase(os.path.realpath(self.browser_root)) != os.path.normcase(os.path.realpath(root)):
                    raise ValueError("The Pak index changed. Reopen Import from Pak.")
                selections = [os.path.join(self.directory, item.name) for item in self.files]
                if not selections:
                    selections = [self.filepath or self.directory]
                assets = pak_catalog.resolve_selection(index, root, selections)
            except Exception as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
        game_dir = configured_dir(scene)
        if not game_dir:
            self.report({"ERROR"}, "Set the Paks folder in Settings")
            return {"CANCELLED"}
        usmap = resolve_usmap(scene)
        if not usmap:
            self.report({"ERROR"}, "No usmap. Pick one in Settings or ship one under ArcPak/mappings")
            return {"CANCELLED"}
        try:
            code, log, meshes = extract_paths(game_dir, usmap, assets)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if not meshes:
            self.report({"ERROR"}, _log_tail(log))
            return {"CANCELLED"}
        if code != 0:
            print(f"Arc Pak: extract finished with warnings:\n{_log_tail(log)}")
        imported = 0
        last = ""
        for mesh in meshes:
            body_variant = getattr(scene, "arc_appearance_body_skin", "NONE") if textures.is_body(mesh) else "NONE"
            ok, msg, _objs = appearance.import_appearance_mesh(mesh, body_variant=body_variant)
            if not ok:
                self.report({"ERROR"}, msg or "Import failed")
                return {"CANCELLED"}
            imported += 1
            last = msg or os.path.basename(mesh)
        from . import character_fit
        character_fit.sync_scene(scene)
        if imported == 1:
            self.report({"INFO"}, last)
        else:
            self.report({"INFO"}, f"Imported {imported} meshes")
        return {"FINISHED"}


classes = (
    ARC_OT_pick_pak_dir,
    ARC_OT_clear_pak_dir,
    ARC_OT_pick_usmap,
    ARC_OT_clear_usmap,
    ARC_OT_reindex_paks,
    ARC_OT_import_from_pak,
)
