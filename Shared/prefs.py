"""Addon preferences: install paths that persist across blend files.

Scene properties stay the per-file override. ``get`` returns the stored
preference value, or ``default`` when the preferences are not registered
(headless runs, module loaded outside ``addon_utils``).
"""

import os
import threading

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import AddonPreferences, Operator


def _addon_package() -> str:
    """Top-level addon module name; the folder may be renamed on install."""
    parts = (__package__ or __name__ or "").split(".")
    if parts and parts[0] == "bl_ext" and len(parts) >= 3:
        return ".".join(parts[:3])
    return parts[0] if parts else ""


ADDON_PACKAGE = _addon_package()
FIELDS = ("pak_dir", "usmap_path", "pioneer_root", "outfit_csv_path", "log_level", "ui_favorites")


def _addon_prefs():
    try:
        addon = bpy.context.preferences.addons.get(ADDON_PACKAGE)
    except Exception:
        return None
    return getattr(addon, "preferences", None) if addon is not None else None


def get(name, default=None):
    """Preference value by field name, or ``default`` when unset or unavailable."""
    prefs = _addon_prefs()
    if prefs is None:
        return default
    try:
        value = getattr(prefs, name)
    except Exception:
        return default
    if value in (None, ""):
        return default
    return value


def set_value(name, value, *, only_if_empty=False) -> bool:
    """Store ``value`` in preferences. Main thread only; returns True when written."""
    if name not in FIELDS or threading.current_thread() is not threading.main_thread():
        return False
    prefs = _addon_prefs()
    if prefs is None:
        return False
    try:
        current = getattr(prefs, name)
        if only_if_empty and current:
            return False
        if current == value:
            return False
        setattr(prefs, name, value)
        return True
    except Exception:
        return False


def remember(name, value) -> bool:
    """Copy a user-set scene value into empty preferences so first setup persists."""
    return bool(value) and set_value(name, value, only_if_empty=True)


def _steam_roots():
    roots = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _kind = winreg.QueryValueEx(key, "SteamPath")
            if value:
                roots.append(os.path.normpath(value))
    except Exception:
        pass
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            roots.append(os.path.join(base, "Steam"))
    roots.append(r"C:\Program Files (x86)\Steam")
    seen, unique = set(), []
    for root in roots:
        key = os.path.normcase(os.path.abspath(root))
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def detect_game_install() -> str:
    """Read-only search for the Arc Raiders install. Returns the game folder or ''."""
    from . import pak_extract
    for root in _steam_roots():
        if not os.path.isdir(root):
            continue
        paks = pak_extract.resolve_pak_folder(root)
        if paks:
            game = os.path.dirname(os.path.dirname(os.path.dirname(paks)))
            return game if pak_extract.resolve_pak_folder(game) == paks else paks
    paks = pak_extract.resolve_pak_folder(pak_extract.DEFAULT_PAK_DIR)
    if paks:
        return pak_extract.DEFAULT_PAK_DIR
    return ""


class ARC_OT_detect_game_install(Operator):
    """Find the Arc Raiders install through Steam library folders and save it in preferences"""

    bl_idname = "arc_outfits.detect_game_install"
    bl_label = "Find Steam Install"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        found = detect_game_install()
        if not found:
            self.report({"WARNING"}, "Arc Raiders was not found. Choose the game folder manually")
            return {"CANCELLED"}
        set_value("pak_dir", found)
        scene = getattr(context, "scene", None)
        if scene is not None:
            try:
                scene.arc_pak_dir = found
            except Exception:
                pass
            try:
                from . import pak_extract
                pak_extract.ensure_pak_browser(scene)
            except Exception:
                pass
        _SOURCE_STATUS_CACHE.clear()
        self.report({"INFO"}, f"Game folder found: {found}")
        return {"FINISHED"}


_SOURCE_STATUS_CACHE = {}
_PAK_RESOLVE_PENDING = set()


def _schedule_pak_resolve(path):
    """Resolve the Paks folder once, off the draw path, then redraw the sidebar."""
    if path in _PAK_RESOLVE_PENDING:
        return
    _PAK_RESOLVE_PENDING.add(path)

    def run():
        try:
            from . import pak_extract
            pak_extract.resolve_pak_folder(path)
        except Exception:
            pass
        finally:
            _PAK_RESOLVE_PENDING.discard(path)
            _SOURCE_STATUS_CACHE.pop(path, None)
            try:
                for window in bpy.context.window_manager.windows:
                    for area in window.screen.areas:
                        if area.type == "VIEW_3D":
                            area.tag_redraw()
            except Exception:
                pass
        return None

    try:
        bpy.app.timers.register(run, first_interval=0.0)
    except Exception:
        _PAK_RESOLVE_PENDING.discard(path)


def source_status(scene):
    """Cheap game-folder readiness for the sidebar: (state, message).

    States: "missing", "invalid", "indexing", "ready". No disk walk: one
    cached ``os.path.isdir`` per folder value plus the in-memory pak index
    state. Safe to call from draw code.
    """
    raw = ""
    try:
        raw = str(getattr(scene, "arc_pak_dir", "") or "").strip()
    except Exception:
        raw = ""
    if not raw:
        raw = str(get("pak_dir", "") or "").strip()
    if not raw:
        return ("missing", "Choose the Arc Raiders game folder")
    try:
        path = bpy.path.abspath(raw)
    except Exception:
        path = raw
    try:
        from . import pak_extract
    except Exception:
        pak_extract = None
    import time as _time
    now = _time.monotonic()
    hit = _SOURCE_STATUS_CACHE.get(path)
    if hit is None or now - hit[1] > 5.0:
        if not os.path.isdir(path):
            paks = None
        elif pak_extract is not None:
            # Draw code must not resolve the Paks folder; read the resolver's cache and
            # resolve on a timer when it has no entry yet ("indexing" until then).
            key = os.path.normcase(os.path.abspath(path))
            cached = (getattr(pak_extract, "_PAK_FOLDER_CACHE", None) or {}).get(key)
            if cached is None:
                _schedule_pak_resolve(path)
                return ("indexing", "Checking the game folder...")
            paks = cached[0] or ""
        else:
            paks = path
        if len(_SOURCE_STATUS_CACHE) > 32:
            _SOURCE_STATUS_CACHE.clear()
        hit = (paks, now)
        _SOURCE_STATUS_CACHE[path] = hit
    if hit[0] is None:
        return ("invalid", "That folder was not found. Choose the Arc Raiders game folder")
    if not hit[0]:
        return ("invalid", "No game files found there. Choose the Arc Raiders game folder")
    if pak_extract is not None and getattr(pak_extract, "_BROWSER_WARMING", None):
        return ("indexing", "Reading the asset list...")
    return ("ready", "Ready")


def _log_file() -> str:
    try:
        from . import utils
        return utils.log_path() or ""
    except Exception:
        return ""


def _log_folder() -> str:
    path = _log_file()
    return os.path.dirname(path) if path else ""


class ARC_OT_open_log_folder(Operator):
    """Open the folder that holds the add-on log file"""

    bl_idname = "arc_outfits.open_log_folder"
    bl_label = "Open Log Folder"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        folder = _log_folder()
        if not folder or not os.path.isdir(folder):
            self.report({"WARNING"}, "No log file: LOCALAPPDATA and TEMP are not writable")
            return {"CANCELLED"}
        if bpy.app.background:
            self.report({"INFO"}, f"Log folder: {folder}")
            return {"FINISHED"}
        try:
            os.startfile(folder)
        except Exception as exc:
            self.report({"WARNING"}, f"Could not open {folder}: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


def _on_log_level(self, context):
    try:
        from . import utils
        utils.apply_log_level()
    except Exception:
        pass


def _path_problem(name, value) -> str:
    """Short reason a set path preference is unusable, or '' when it is fine or empty."""
    if not value:
        return ""
    path = bpy.path.abspath(value)
    if name == "pak_dir":
        try:
            from . import pak_extract
            ok = bool(pak_extract.resolve_pak_folder(path))
        except Exception:
            ok = os.path.isdir(path)
        return "" if ok else "No Arc Raiders .pak/.utoc files found here"
    if name == "usmap_path":
        if not os.path.isfile(path):
            return "File not found"
        return "" if path.casefold().endswith(".usmap") else "Not a .usmap file"
    if name == "pioneer_root":
        return "" if os.path.isdir(path) else "Folder not found"
    if name == "outfit_csv_path":
        if not os.path.isfile(path):
            return "File not found"
        return "" if path.casefold().endswith(".csv") else "Not a .csv file"
    return ""


class ARC_AP_preferences(AddonPreferences):
    bl_idname = ADDON_PACKAGE

    pak_dir: StringProperty(
        name="Game Install Folder",
        description="Arc Raiders install or Paks folder, used when a blend file has no valid folder set",
        default="",
        subtype="DIR_PATH",
    )
    usmap_path: StringProperty(
        name="Game Mappings File",
        description="Mappings file used when a blend file has no valid usmap set",
        default="",
        subtype="FILE_PATH",
    )
    pioneer_root: StringProperty(
        name="FModel Export Folder",
        description="FModel export folder used when a blend file has none set",
        default="",
        subtype="DIR_PATH",
    )
    outfit_csv_path: StringProperty(
        name="Outfit Catalog File",
        description="Outfit reference CSV used when a blend file has none set",
        default="",
        subtype="FILE_PATH",
    )
    log_level: EnumProperty(
        name="Log Level",
        description="Console log detail for the add-on",
        items=(
            ("INFO", "Info", "Normal console output"),
            ("DEBUG", "Debug", "Verbose console output for troubleshooting"),
        ),
        default="INFO",
        update=_on_log_level,
    )
    show_developer: BoolProperty(
        name="Show Developer Tools in the Sidebar",
        description="Adds mask debug, effect playback paths and shader compatibility tools to Settings in the sidebar",
        default=False,
    )
    ui_favorites: StringProperty(
        name="Favorites and Recents",
        description="Starred and recently imported sidebar items, stored as JSON",
        default="",
        options={'HIDDEN'},
    )

    def _draw_problem(self, col, name):
        problem = _path_problem(name, getattr(self, name, ""))
        if problem:
            col.label(text=problem, icon="ERROR")

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.label(text="The importer is in the 3D Viewport sidebar: press N, then open the Arc Asset Importer tab.", icon="INFO")
        row = col.row(align=True)
        row.prop(self, "pak_dir")
        row.operator(ARC_OT_detect_game_install.bl_idname, text="Find Steam Install", icon="VIEWZOOM")
        self._draw_problem(col, "pak_dir")
        for name in ("usmap_path", "pioneer_root", "outfit_csv_path"):
            col.prop(self, name)
            self._draw_problem(col, name)
        col.prop(self, "log_level")
        col.prop(self, "show_developer")
        folder = _log_folder()
        path = _log_file()
        row = col.row(align=True)
        row.label(text=f"Log: {path}" if path else "Log: none (LOCALAPPDATA and TEMP not writable)", icon="TEXT")
        sub = row.row(align=True)
        sub.enabled = bool(folder)
        sub.operator(ARC_OT_open_log_folder.bl_idname, text="Open Log Folder", icon="FILE_FOLDER")


_CLASSES = (ARC_OT_detect_game_install, ARC_OT_open_log_folder, ARC_AP_preferences)


def register():
    for cls in _CLASSES:
        if getattr(cls, "is_registered", False):
            continue
        stale = getattr(bpy.types, cls.__name__, None)
        if stale is not None and stale is not cls:
            try:
                bpy.utils.unregister_class(stale)
            except Exception:
                pass
        try:
            bpy.utils.register_class(cls)
        except Exception as exc:
            print(f"[Arc Asset Importer] preferences: could not register {cls.__name__}: {exc}")


def unregister():
    for cls in reversed(_CLASSES):
        if not getattr(cls, "is_registered", False):
            continue
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
