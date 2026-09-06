"""
Utility functions for the Arc Raiders Importer
"""

import os
import re
import json
import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import bpy
import mathutils
from mathutils import Vector

_ADDON_DIR = os.path.dirname(__file__)
_BLEND_PATH = os.path.join(_ADDON_DIR, "ArcTexturer.blend")
_NODE_GROUP = "ArcTexturer"
_NODE_GROUP_ALIASES = ("ArcTexturer", "Arc Texturer")
_COLORMASK_GROUP = "ColorMask_XYZ"
_DECAL_DATA_GROUP = "Decal Data"
_DECAL_LAYERMASK_GATE_GROUP = "Decal LayerMask Gate"
# External per-decal helper: Material ID + LayerMask bitmask + sticker Alpha → masked Alpha.
_DECAL_LAYER_MASK_GROUP = "Decal LayerMask"
_CURVATURE_ID_OVERRIDE_GROUP = "CurvatureID_Override"
_VISOR_GROUP = "Visor"
_DEBUG_LOG_PATH = os.path.join(_ADDON_DIR, "arc_raiders_debug.log")
_PERF_LOG_NAME = "arc_outfits_perf.log"
# Final builds: leave False. Set True to re-enable %TEMP%/arc_outfits_perf.log drill-down.
_PERF_LOGGING_ENABLED = False
_LOGGER = None
_IO_POOL_DEFAULT_WORKERS = 6
_ACTIVE_PERF = None  # type: ignore[var-annotated]


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


def perf_log_path() -> str:
    """Dedicated drill-down perf log (easy to find: ``%TEMP%/arc_outfits_perf.log``)."""
    base = os.environ.get("TEMP") or os.environ.get("TMP") or os.path.expanduser("~")
    return os.path.join(base, _PERF_LOG_NAME)


def _perf_category(label: str) -> str:
    """Map a span label to a SUMMARY bucket (exclusive rollup of depth-1 steps)."""
    low = (label or "").lower()
    if low.startswith(("psk.parse", "psk.create", "psk.ops", "import_psk", "process_entry")):
        return "psk"
    if low.startswith("duplicate_colourway") or "mesh_copy" in low or "duplicate" in low:
        return "mesh_copy"
    if low.startswith(("batch_join_io", "batch_io", "batch_collect_io")):
        return "io_prefetch"
    if "prefetch_images" in low or low.startswith("batch_prefetch"):
        return "image_prefetch"
    if low.startswith(("batch_materials", "apply_materials")):
        return "materials"
    if "view_layer" in low:
        return "view_layer"
    if low.startswith(("layout", "shift_instance", "link_collections", "post_import_ux")):
        return "layout"
    if low.startswith(("fix_rig", "rename_outfit", "preload_arc")):
        return "rig_setup"
    return "other"


class PerfSession:
    """Accumulate nested ``timed`` spans for a drill-down import report."""

    def __init__(self, name: str):
        self.name = name or "import"
        self.t0 = time.perf_counter()
        self.records = []  # (start, depth, label, elapsed)
        self._stack = []  # open labels for nesting depth (main thread only)
        self._lock = threading.Lock()
        self.counters = {}
        self.meta = {}
        self.report_text = ""
        self.summary_line = ""
        self._finished = False

    def count(self, key: str, n: int = 1) -> None:
        with self._lock:
            self.counters[key] = int(self.counters.get(key, 0)) + int(n)

    def set_meta(self, **kwargs) -> None:
        self.meta.update(kwargs)

    def record(self, label: str, elapsed: float, depth: int = 0, start: float = None) -> None:
        t_start = float(self.t0 if start is None else start)
        with self._lock:
            self.records.append((t_start, int(depth), str(label), float(elapsed)))

    def format_report(self) -> str:
        wall = max(time.perf_counter() - self.t0, 1e-9)
        lines = [
            "",
            "=" * 72,
            f"Arc Outfits Perf — {self.name}",
            f"Wall: {wall:.3f}s",
        ]
        if self.meta:
            meta_bits = [f"{k}={v}" for k, v in self.meta.items()]
            lines.append("Meta: " + "  ".join(meta_bits))
        if self.counters:
            ctr = "  ".join(f"{k}={v}" for k, v in sorted(self.counters.items()))
            lines.append(f"Counters: {ctr}")
        lines.append("-" * 72)
        lines.append(f"{'elapsed':>10}  {'%wall':>6}  step")
        ordered = sorted(self.records, key=lambda r: (r[0], r[1]))
        for _start, depth, label, elapsed in ordered:
            pct = 100.0 * elapsed / wall
            indent = "  " * max(depth, 0)
            lines.append(f"{elapsed:10.3f}s  {pct:5.1f}%  {indent}{label}")

        # Roll up depth-1 spans (direct children of the outermost timed block).
        buckets = {}
        for _start, depth, label, elapsed in ordered:
            if depth != 1:
                continue
            cat = _perf_category(label)
            buckets[cat] = buckets.get(cat, 0.0) + elapsed
        if not buckets:
            for _start, depth, label, elapsed in ordered:
                if depth == 0:
                    continue
                cat = _perf_category(label)
                buckets[cat] = buckets.get(cat, 0.0) + elapsed

        accounted = sum(buckets.values())
        if accounted + 1e-6 < wall:
            buckets["unaccounted"] = wall - accounted

        lines.append("-" * 72)
        lines.append("SUMMARY (% of wall):")
        for cat, sec in sorted(buckets.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {cat:16s} {sec:8.3f}s  {100.0 * sec / wall:5.1f}%")
        lines.append(f"Perf log: {perf_log_path()}")
        lines.append(f"Debug log: {debug_log_path()}")
        lines.append("=" * 72)

        top = sorted(buckets.items(), key=lambda kv: -kv[1])[:4]
        top_s = ", ".join(f"{c} {100.0 * s / wall:.0f}%" for c, s in top) if top else ""
        # ASCII arrow — Blender console / Windows cp1252 safe
        self.summary_line = f"Perf {wall:.2f}s ({top_s}) -> {perf_log_path()}"
        self.report_text = "\n".join(lines)
        return self.report_text

    def finish(self, *, also_print: bool = True) -> str:
        """Format, write to perf + debug logs, optionally print. Returns SUMMARY line."""
        if self._finished:
            return self.summary_line
        self._finished = True
        # Perf logging disabled in final builds — flip _PERF_LOGGING_ENABLED to re-enable.
        if not _PERF_LOGGING_ENABLED:
            return self.summary_line
        text = self.format_report()
        log = get_logger()
        for line in text.splitlines():
            if line:
                log.info("%s", line)
        try:
            with open(perf_log_path(), "a", encoding="utf-8") as fh:
                fh.write(text)
                fh.write("\n")
        except OSError as exc:
            log.warning("Could not write perf log (%s): %s", perf_log_path(), exc)
        if also_print:
            print(text)
        return self.summary_line


@contextmanager
def perf_session(name: str, **meta):
    """Start a nested-aware timing session; on exit write the drill-down report."""
    global _ACTIVE_PERF
    # When disabled, still yield a session so callers can count/set_meta, but finish is a no-op.
    prev = _ACTIVE_PERF
    session = PerfSession(name)
    if meta:
        session.set_meta(**meta)
    _ACTIVE_PERF = session if _PERF_LOGGING_ENABLED else None
    try:
        yield session
    finally:
        _ACTIVE_PERF = prev
        # session.finish(also_print=True)  # re-enable with _PERF_LOGGING_ENABLED
        if _PERF_LOGGING_ENABLED:
            session.finish(also_print=True)
        else:
            session._finished = True


def active_perf():
    """Return the current ``PerfSession`` or None."""
    return _ACTIVE_PERF


@contextmanager
def timed(label: str, logger=None):
    """Log wall-clock elapsed for a span (``with timed("psk import"): ...``).

    When a ``perf_session`` is active, also records nested depth for the drill-down report.
    Off-main threads (PSK parse prefetch) record without touching the main nest stack.
    """
    # Fast path when perf logging is off: no timing / no logger spam.
    if not _PERF_LOGGING_ENABLED:
        yield
        return
    log = logger or get_logger()
    session = _ACTIVE_PERF
    on_main = threading.current_thread() is threading.main_thread()
    depth = 0
    if session is not None and on_main:
        depth = len(session._stack)
        session._stack.append(label)
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - t0
        if session is not None:
            if on_main:
                if session._stack and session._stack[-1] == label:
                    session._stack.pop()
                session.record(label, elapsed, depth=depth, start=t0)
            else:
                # Prefetch workers: nest under typical import depth without corrupting stack.
                session.record(label, elapsed, depth=2, start=t0)
        log.info("%s took %.3fs", label, elapsed)


def io_prefetch_enabled(context=None) -> bool:
    """IO prefetch is always on for the optimized import path (UI toggle removed)."""
    return True
    # Previous scene.toggle (kept for easy restore):
    # try:
    #     scene = None
    #     if context is not None:
    #         scene = getattr(context, "scene", None)
    #     if scene is None:
    #         scene = getattr(bpy.context, "scene", None)
    #     if scene is None:
    #         return True
    #     return bool(getattr(scene, "arc_io_prefetch", True))
    # except Exception:
    #     return True


def run_io_pool(func, items, *, max_workers=None, enabled=None, context=None):
    """Map ``func`` over ``items`` with a ThreadPoolExecutor (IO-bound only).

    Workers must not touch ``bpy``. Returns results in the same order as ``items``.
    On failure or when disabled, falls back to sequential ``func(item)`` calls.

    ``enabled`` defaults to On (``io_prefetch_enabled``). Pass ``False`` to
    force sequential, or ``True`` to force the pool regardless of the toggle.
    """
    items = list(items)
    if not items:
        return []
    if enabled is None:
        enabled = io_prefetch_enabled(context)
    if not enabled or len(items) == 1:
        return [func(item) for item in items]

    workers = max_workers
    if workers is None:
        cpu = os.cpu_count() or 4
        workers = max(4, min(8, cpu, _IO_POOL_DEFAULT_WORKERS, len(items)))
    try:
        results = [None] * len(items)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            future_map = {ex.submit(func, item): idx for idx, item in enumerate(items)}
            for fut in as_completed(future_map):
                idx = future_map[fut]
                results[idx] = fut.result()
        return results
    except Exception as exc:
        get_logger().warning("run_io_pool failed (%s); falling back sequential", exc)
        return [func(item) for item in items]


def preload_arc_node_groups() -> None:
    """Warm ArcTexturer + helper groups so the first material skips libraries.load."""
    ensure_arc_texturer_node_group()
    ensure_decal_data_node_group()
    ensure_decal_layermask_gate_node_group()
    ensure_decal_layer_mask_node_group()
    ensure_colormask_node_group()
    ensure_curvature_id_override_node_group()
    ensure_visor_node_group()
    try:
        from .materials import weapon as _weapon_mats

        _weapon_mats.ensure_weapon_texturer_node_group()
    except Exception as exc:
        print(f"Arc Raiders PSK Importer: WeaponTexturer preload failed (non-fatal): {exc}")


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
        # Single listdir — previously listed twice when no PSK at this level.
        try:
            entries = sorted(os.listdir(path))
        except OSError:
            return
        psks = [
            os.path.join(path, f) for f in entries
            if f.lower().endswith(".psk") or f.lower().endswith(".pskx")
        ]
        if psks:
            results.extend(pick_preferred(psks))
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
            from . import importing as _imp
            getattr(_imp, "_COLOURWAY_INDEX_CACHE", {}).clear()
        except Exception:
            pass
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
        try:
            from . import weapon_catalog as _wcat
            _wcat.invalidate_enum_caches()
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
    elif base == "pioneer":
        # Root is already Content/Pioneer — parent is the UE Content folder.
        parent = os.path.dirname(os.path.normpath(root))
        if os.path.basename(parent).lower() == "content":
            found = parent
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

def resolved_picked_dir(filepath: str, directory: str = "") -> str:
    """Resolve a Blender file-browser pick to a directory.

    ImportHelper often sets ``filepath`` to the selected folder (or a file
    inside it). Using ``os.path.dirname(filepath)`` unconditionally walks up
    one level and the addon then scans the parent (e.g. ``Outfit`` instead of
    ``Outfit/TickHunter``).
    """
    for raw in (directory, filepath):
        if not raw:
            continue
        path = os.path.normpath(raw)
        if os.path.isdir(path):
            return path
        parent = os.path.dirname(path)
        if parent and os.path.isdir(parent):
            return parent
    return ""


_REL_DIR_SKIP = frozenset({
    "mapplacements", "shadercode", "shadercorpus", "logs", "backups",
    ".git", "__pycache__",
})


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
                    elow = entry.lower()
                    if elow in _REL_DIR_SKIP:
                        continue
                    # FModel composer dumps: <root>/Outfits/<name>/Parts/...
                    # (singular Items/.../Skins/Outfit is the real colourway tree).
                    if elow == "outfits" and "skins" not in current.lower():
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

def find_node_group(name: str):
    """Resolve a node group by exact name.

    ``bpy.data.node_groups.get`` / ``in`` can miss a live group after certain
    library appends; fall back to linear scan.
    """
    if not name:
        return None
    try:
        ng = bpy.data.node_groups.get(name)
        if ng is not None:
            return ng
    except Exception:
        pass
    for ng in bpy.data.node_groups:
        if ng.name == name:
            return ng
    return None


def find_arc_texturer_node_group():
    """Find ArcTexturer under either historical spelling (space / no space)."""
    for name in _NODE_GROUP_ALIASES:
        ng = find_node_group(name)
        if ng is not None:
            if ng.name != _NODE_GROUP:
                try:
                    ng.name = _NODE_GROUP
                except Exception:
                    pass
            return find_node_group(_NODE_GROUP) or ng
    for ng in bpy.data.node_groups:
        compact = ng.name.replace(" ", "")
        if compact == "ArcTexturer" or compact.startswith("ArcTexturer."):
            if ng.name != _NODE_GROUP and not ng.name.startswith(_NODE_GROUP + "."):
                try:
                    ng.name = _NODE_GROUP
                except Exception:
                    pass
            return ng
    return None


def _arc_texturer_has_overlay_sockets(ng) -> bool:
    """True when bundled ArcTexturer exposes internalized overlay + decal sockets."""
    try:
        names = {item.name for item in ng.interface.items_tree if hasattr(item, "name")}
    except Exception:
        return False
    # DN Alpha marks Decal Data / LayerMask Gate internalized into ArcTexturer.
    # LayerGate bitmasks are internal Value defaults (no external Decal LayerGate sockets).
    # Decal ColorOverride marks ColorA/B tint + override Fac internalized (no external
    # ColorRamp / Original↔Override nodes).
    return (
        "Overlay 1" in names
        and "DN Enable 5" in names
        and "DN Alpha 1" in names
        and "Decal 1 ColorOverride" in names
        and "Decal LayerGate 1" not in names
    )


def arc_texturer_has_internal_decal_data(ng=None) -> bool:
    """True when ArcTexturer expects raw DecalData into DN / DN Alpha (not preprocessed)."""
    if ng is None:
        ng = find_arc_texturer_node_group()
    if ng is None:
        return False
    try:
        names = {item.name for item in ng.interface.items_tree if hasattr(item, "name")}
    except Exception:
        return False
    return "DN Alpha 1" in names


def arc_texturer_has_internal_decal_color_override(ng=None) -> bool:
    """True when ColorA/B + ColorOverride live on Arc sockets (no external tint nodes)."""
    if ng is None:
        ng = find_arc_texturer_node_group()
    if ng is None:
        return False
    try:
        names = {item.name for item in ng.interface.items_tree if hasattr(item, "name")}
    except Exception:
        return False
    return "Decal 1 ColorOverride" in names and "Decal 1 ColorA" in names


def _iface_socket_type(item) -> str:
    return getattr(item, "socket_type", None) or getattr(item, "bl_socket_idname", None) or ""


def _set_iface_socket_type(item, socket_type: str, default=None) -> bool:
    """Return True when the socket type changed."""
    cur = _iface_socket_type(item)
    if cur == socket_type:
        if default is not None:
            try:
                item.default_value = default
            except Exception:
                pass
        return False
    try:
        item.socket_type = socket_type
    except Exception:
        return False
    if default is not None:
        try:
            item.default_value = default
        except Exception:
            pass
    return True


def _mix_sock(node, role: str, *, prefer_float: bool = True):
    """Enabled Mix socket for Factor/A/B/Result (avoids disabled parallel socks)."""
    socks = node.outputs if role == "Result" else node.inputs
    if role == "Factor":
        idents = ("Factor_Float", "Factor") if prefer_float else ("Factor", "Factor_Float")
    elif role == "A":
        idents = ("A_Float", "A") if prefer_float else ("A", "A_Float")
    elif role == "B":
        idents = ("B_Float", "B") if prefer_float else ("B", "B_Float")
    elif role == "Result":
        idents = ("Result_Float", "Result") if prefer_float else ("Result", "Result_Float")
    else:
        return None
    by_id = {s.identifier: s for s in socks}
    for ident in idents:
        s = by_id.get(ident)
        if s is not None and s.enabled:
            return s
    prefix = {"Factor": "Factor", "A": "A_", "B": "B_", "Result": "Result"}[role]
    for s in socks:
        if s.enabled and (s.identifier == role or s.identifier.startswith(prefix)):
            return s
    return None


def _mix_to_float(ng, node, *, unbound_a=None) -> bool:
    """Convert a Mix node to FLOAT; relink via enabled *_Float sockets only."""
    if node is None or getattr(node, "bl_idname", "") != "ShaderNodeMix":
        return False
    if node.data_type == "FLOAT":
        a = _mix_sock(node, "A", prefer_float=True)
        if unbound_a is not None and a is not None and not a.is_linked:
            try:
                a.default_value = float(unbound_a)
            except Exception:
                pass
        return False

    prefer = False  # capture from currently enabled (usually RGBA) socks
    fac = _mix_sock(node, "Factor", prefer_float=prefer)
    a = _mix_sock(node, "A", prefer_float=prefer)
    b = _mix_sock(node, "B", prefer_float=prefer)
    result = _mix_sock(node, "Result", prefer_float=prefer)
    fac_from = fac.links[0].from_socket if fac is not None and fac.is_linked else None
    a_from = a.links[0].from_socket if a is not None and a.is_linked else None
    b_from = b.links[0].from_socket if b is not None and b.is_linked else None
    out_tos = [l.to_socket for l in result.links] if result is not None else []

    for role in ("Factor", "A", "B"):
        s = _mix_sock(node, role, prefer_float=prefer)
        if s is None:
            continue
        for l in list(s.links):
            ng.links.remove(l)
    if result is not None:
        for l in list(result.links):
            ng.links.remove(l)

    node.data_type = "FLOAT"

    fac = _mix_sock(node, "Factor", prefer_float=True)
    a = _mix_sock(node, "A", prefer_float=True)
    b = _mix_sock(node, "B", prefer_float=True)
    result = _mix_sock(node, "Result", prefer_float=True)
    if fac_from is not None and fac is not None:
        ng.links.new(fac_from, fac)
    if a_from is not None and a is not None:
        ng.links.new(a_from, a)
    elif unbound_a is not None and a is not None:
        a.default_value = float(unbound_a)
    if b_from is not None and b is not None:
        ng.links.new(b_from, b)
    if result is not None:
        for ts in out_tos:
            try:
                ng.links.new(result, ts)
            except Exception:
                pass
    return True


# DN fac Mix.073–083 → normal mixes only (not rough/metal — those blocked zone PBR).
_ARC_DN_FAC_TARGETS = (
    ("Mix.073", ("Mix.066",), ("Mix.095", "Mix.096"), "Math.004", "DecalData1"),
    ("Mix.074", ("Mix.067",), ("Mix.097", "Mix.098"), "Math.005", "DecalData2"),
    ("Mix.075", ("Mix.068",), ("Mix.099", "Mix.100"), "Math.006", "DecalData3"),
    ("Mix.076", ("Mix.069",), ("Mix.101", "Mix.102"), "Math.007", "DecalData4"),
    ("Mix.077", ("Mix.070",), ("Mix.103", "Mix.104"), "Math.008", "DecalData5"),
    ("Mix.078", ("Mix.071",), ("Mix.105", "Mix.106"), "Math.009", "DecalData6"),
    ("Mix.079", ("Mix.072",), ("Mix.107", "Mix.108"), "Math.010", "DecalData7"),
    ("Mix.082", ("Mix.080",), ("Mix.109", "Mix.110"), "Math.011", "DecalData8"),
    ("Mix.083", ("Mix.081",), ("Mix.111", "Mix.112"), "Math.012", "DecalData9"),
)


def _ensure_decal_data_normal_alpha_mask(dng) -> bool:
    """Decal Data: Normal = mix(flat, reconstructed, Mask); Mask = decal alpha."""
    changed = False
    if dng is None:
        return False
    mask_item = None
    for item in dng.interface.items_tree:
        if getattr(item, "name", None) == "Mask" and item.in_out == "INPUT":
            mask_item = item
            break
    if mask_item is None:
        mask_item = dng.interface.new_socket(
            name="Mask", in_out="INPUT", socket_type="NodeSocketFloat"
        )
        try:
            mask_item.default_value = 0.0
        except Exception:
            pass
        changed = True
    gi = next((n for n in dng.nodes if n.type == "GROUP_INPUT"), None)
    go = next((n for n in dng.nodes if n.type == "GROUP_OUTPUT"), None)
    combine = next(
        (
            n
            for n in dng.nodes
            if n.bl_idname == "ShaderNodeCombineColor" or n.type == "COMBINE_COLOR"
        ),
        None,
    )
    if gi is None or go is None or combine is None:
        return changed
    mixn = dng.nodes.get("NormalAlphaMix")
    if mixn is None:
        mixn = dng.nodes.new("ShaderNodeMix")
        mixn.name = "NormalAlphaMix"
        mixn.label = "Normal × Mask (decal alpha)"
        mixn.data_type = "RGBA"
        mixn.location = (combine.location.x + 220.0, combine.location.y)
        changed = True
    fac = _mix_sock(mixn, "Factor", prefer_float=False)
    a = _mix_sock(mixn, "A", prefer_float=False)
    b = _mix_sock(mixn, "B", prefer_float=False)
    rout = _mix_sock(mixn, "Result", prefer_float=False)
    for sock in (fac, a, b):
        if sock is None:
            continue
        for l in list(sock.links):
            dng.links.remove(l)
    if rout is not None:
        for l in list(rout.links):
            dng.links.remove(l)
    mask_out = gi.outputs.get("Mask") or next(
        (o for o in gi.outputs if o.name == "Mask"), None
    )
    if mask_out is not None and fac is not None:
        dng.links.new(mask_out, fac)
    if a is not None:
        a.default_value = (0.5, 0.5, 1.0, 1.0)
    cout = combine.outputs[0]
    for l in list(cout.links):
        dng.links.remove(l)
    if b is not None:
        dng.links.new(cout, b)
    norm_in = go.inputs.get("Normal") or next(
        (s for s in go.inputs if s.name == "Normal"), None
    )
    if norm_in is not None and rout is not None:
        for l in list(norm_in.links):
            dng.links.remove(l)
        dng.links.new(rout, norm_in)
    return changed


def _ensure_arc_dn_fac_links(ng) -> int:
    """DN Enable×alpha gates normals only; zero rough/metal DN Fac; wire Mask."""
    fixed = 0
    dd0 = ng.nodes.get("DecalData1")
    if dd0 is not None and dd0.node_tree is not None:
        if _ensure_decal_data_normal_alpha_mask(dd0.node_tree):
            fixed += 1
    for src_name, norm_tgts, rm_tgts, math_name, dd_name in _ARC_DN_FAC_TARGETS:
        src = ng.nodes.get(src_name)
        if src is None:
            continue
        rout = _mix_sock(src, "Result", prefer_float=True)
        if rout is None:
            continue
        # Rebuild Result fan-out: normals only (drop rough/metal Fac links).
        wanted = set(norm_tgts)
        for l in list(rout.links):
            if l.to_node.name not in wanted:
                ng.links.remove(l)
                fixed += 1
        for dst_name in norm_tgts:
            dst = ng.nodes.get(dst_name)
            if dst is None:
                continue
            fac = _mix_sock(dst, "Factor", prefer_float=(dst.data_type == "FLOAT"))
            if fac is None:
                continue
            if not (fac.is_linked and fac.links[0].from_socket == rout):
                while fac.is_linked:
                    ng.links.remove(fac.links[0])
                ng.links.new(rout, fac)
                fixed += 1
        for rm_name in rm_tgts:
            rm = ng.nodes.get(rm_name)
            if rm is None:
                continue
            rf = _mix_sock(rm, "Factor", prefer_float=True)
            if rf is None:
                continue
            if rf.is_linked:
                while rf.is_linked:
                    ng.links.remove(rf.links[0])
                fixed += 1
            try:
                if abs(float(rf.default_value)) > 1e-6:
                    rf.default_value = 0.0
                    fixed += 1
            except Exception:
                rf.default_value = 0.0
        math = ng.nodes.get(math_name)
        dd = ng.nodes.get(dd_name)
        if math is not None and dd is not None and "Mask" in dd.inputs:
            ms = dd.inputs["Mask"]
            if not (ms.is_linked and ms.links[0].from_node == math):
                while ms.is_linked:
                    ng.links.remove(ms.links[0])
                ng.links.new(math.outputs["Value"], ms)
                fixed += 1
    return fixed


def repair_arc_texturer_metal_rough_types(ng=None) -> dict:
    """Heal ArcTexturer: Float Metallic/Decal Alpha + FLOAT Mix chain + DN alpha mask."""
    stats = {"metallic": 0, "decal_alpha": 0, "mixes": 0, "dn_fac": 0}
    if ng is None:
        ng = find_arc_texturer_node_group()
    if ng is None:
        return stats
    for item in list(ng.interface.items_tree):
        name = getattr(item, "name", "") or ""
        st = _iface_socket_type(item)
        if name.startswith("Metallic ") and st in ("NodeSocketBool", "NodeSocketBoolean"):
            if _set_iface_socket_type(item, "NodeSocketFloat", 0.0):
                stats["metallic"] += 1
        elif name.startswith("Decal Alpha ") and st in ("NodeSocketColor",):
            if _set_iface_socket_type(item, "NodeSocketFloat", 0.0):
                stats["decal_alpha"] += 1
    zone_rough = (
        "Mix.037", "Mix.044", "Mix.045", "Mix.038", "Mix.039",
        "Mix.040", "Mix.041", "Mix.042", "Mix.043",
    )
    zone_metal = (
        "Mix.046", "Mix.053", "Mix.054", "Mix.047", "Mix.048",
        "Mix.049", "Mix.050", "Mix.051", "Mix.052",
    )
    dn_fac = tuple(src for src, *_ in _ARC_DN_FAC_TARGETS)
    dn_value = (
        "Mix.095", "Mix.097", "Mix.099", "Mix.101", "Mix.103",
        "Mix.105", "Mix.107", "Mix.109", "Mix.111",
        "Mix.096", "Mix.098", "Mix.100", "Mix.102", "Mix.104",
        "Mix.106", "Mix.108", "Mix.110", "Mix.112",
    )
    for name in zone_rough:
        if _mix_to_float(ng, ng.nodes.get(name), unbound_a=1.0):
            stats["mixes"] += 1
    for name in zone_metal:
        unbound = 0.0 if name == "Mix.046" else None
        if _mix_to_float(ng, ng.nodes.get(name), unbound_a=unbound):
            stats["mixes"] += 1
    for name in dn_fac:
        if _mix_to_float(ng, ng.nodes.get(name), unbound_a=0.0):
            stats["mixes"] += 1
    for name in dn_value:
        if _mix_to_float(ng, ng.nodes.get(name)):
            stats["mixes"] += 1
    for name, val in (("Mix.037", 1.0), ("Mix.046", 0.0)):
        n = ng.nodes.get(name)
        if n is None:
            continue
        a = _mix_sock(n, "A", prefer_float=True)
        if a is not None and not a.is_linked:
            try:
                a.default_value = val
            except Exception:
                pass
    stats["dn_fac"] = _ensure_arc_dn_fac_links(ng)
    if stats["metallic"] or stats["decal_alpha"] or stats["mixes"] or stats["dn_fac"]:
        print(
            "Arc Raiders PSK Importer: ArcTexturer metal/rough type fix — "
            f"metallic={stats['metallic']} decal_alpha={stats['decal_alpha']} "
            f"mixes={stats['mixes']} dn_fac={stats['dn_fac']}"
        )
    return stats


def apply_arc_debug_weather_defaults(ng=None) -> int:
    """Force ArcTexturer Dirt Toggle + Variation iface defaults to 0 (ColorABC debug).

    Dirt Toggle 0.7 + internal HSV Value=5 washes ColorMask albedo toward white
    (see docs/COLOR_ROUTING_FAILED_ATTEMPTS.md Q01). Variation off while debugging.
    Returns number of sockets updated.
    """
    if ng is None:
        ng = find_arc_texturer_node_group()
    if ng is None:
        return 0
    updated = 0
    for item in list(ng.interface.items_tree):
        name = getattr(item, "name", "") or ""
        if name not in ("Dirt Toggle", "Variation"):
            continue
        try:
            cur = float(getattr(item, "default_value", 0.0) or 0.0)
        except (TypeError, ValueError):
            cur = None
        if cur is not None and abs(cur) <= 1e-6:
            continue
        try:
            item.default_value = 0.0
            updated += 1
        except Exception:
            pass
    return updated


def apply_arc_instance_weather_defaults(group_node) -> None:
    """Zero Dirt Toggle / Variation on a live ArcTexturer group instance."""
    if group_node is None:
        return
    for sock_name in ("Dirt Toggle", "Variation"):
        sock = group_node.inputs.get(sock_name)
        if sock is None or sock.is_linked:
            continue
        try:
            sock.default_value = 0.0
        except Exception:
            pass


def _arc_mix019_mid_softlight_nodes(ng):
    """Yield Mix nodes that Soft-Light Colour×AO with OCM Blue at Fac=1 (Q02).

    Identified as SOFT_LIGHT whose Factor is unlinked (not Dirt Toggle) and whose
    B input is driven by a Separate Color **Blue** socket (MaterialID).
    """
    if ng is None:
        return
    for node in ng.nodes:
        if getattr(node, "bl_idname", "") != "ShaderNodeMix":
            continue
        if getattr(node, "blend_type", None) != "SOFT_LIGHT":
            continue
        b_in = None
        for sock in node.inputs:
            if sock.name != "B" or not sock.is_linked:
                continue
            link = sock.links[0]
            from_node = link.from_node
            from_sock = link.from_socket
            from_type = getattr(from_node, "bl_idname", "") or ""
            from_ntype = getattr(from_node, "type", "") or ""
            if "Separate" not in from_type and "SEPARATE" not in from_ntype:
                continue
            if (getattr(from_sock, "name", "") or "") != "Blue":
                continue
            b_in = sock
            break
        if b_in is None:
            continue
        # Prefer unlinked float Factor (Dirt Soft Light has Factor linked).
        fac = None
        for sock in node.inputs:
            if sock.name != "Factor":
                continue
            if sock.is_linked:
                # Linked float Factor (Dirt) — not Mix.019.
                if getattr(sock, "type", "") == "VALUE":
                    fac = None
                    break
                continue
            if getattr(sock, "type", "") != "VALUE":
                continue
            fac = sock
        if fac is None:
            continue
        yield node, fac


def apply_arc_debug_disable_mid_softlight(ng=None) -> int:
    """Force Mix.019 Soft Light Factor to 0 (ColorABC debug, Q02).

    Always-on Soft Light × OCM Blue washes high MaterialID pixels toward white
    even with Dirt=0. Python-only — do not libraries.write the blend (F14).
    Returns number of Mix nodes updated.
    """
    if ng is None:
        ng = find_arc_texturer_node_group()
    if ng is None:
        return 0
    updated = 0
    for node, fac in _arc_mix019_mid_softlight_nodes(ng):
        try:
            cur = float(fac.default_value)
        except (TypeError, ValueError):
            cur = None
        if cur is not None and abs(cur) <= 1e-6:
            continue
        try:
            fac.default_value = 0.0
            updated += 1
        except Exception:
            pass
    return updated


def ensure_arc_texturer_node_group():
    """Load ArcTexturer from ArcTexturer.blend; refresh if missing new sockets.

    Returns the node group on success, or None (truthy/falsy for callers).
    """
    existing = find_arc_texturer_node_group()
    if existing is not None and _arc_texturer_has_overlay_sockets(existing):
        ensure_decal_data_node_group()
        ensure_decal_layermask_gate_node_group()
        ensure_colormask_node_group()
        repair_arc_texturer_metal_rough_types(existing)
        apply_arc_debug_weather_defaults(existing)
        apply_arc_debug_disable_mid_softlight(existing)
        return existing
    if not os.path.isfile(_BLEND_PATH):
        print(f"Arc Raiders PSK Importer: Cannot find bundled blend at '{_BLEND_PATH}'")
        return None
    # Stale in-memory group (pre-overlay / broken DN5): replace from blend.
    if existing is not None:
        try:
            existing.name = f"{_NODE_GROUP}_stale"
        except Exception:
            pass
    with bpy.data.libraries.load(_BLEND_PATH, link=False) as (data_from, data_to):
        blend_names = list(data_from.node_groups)
        load_name = None
        for candidate in _NODE_GROUP_ALIASES:
            if candidate in blend_names:
                load_name = candidate
                break
        if load_name is None:
            for n in blend_names:
                if n.replace(" ", "") == "ArcTexturer":
                    load_name = n
                    break
        if load_name is None:
            print(f"Arc Raiders PSK Importer: Node group '{_NODE_GROUP}' not in blend file")
            return None
        data_to.node_groups = [load_name]
    loaded = find_arc_texturer_node_group()
    if loaded is None:
        for ng in bpy.data.node_groups:
            compact = ng.name.replace(" ", "")
            if compact.startswith("ArcTexturer") and _arc_texturer_has_overlay_sockets(ng):
                try:
                    ng.name = _NODE_GROUP
                except Exception:
                    pass
                loaded = find_arc_texturer_node_group() or ng
                break
    stale = find_node_group(f"{_NODE_GROUP}_stale")
    if stale is not None and stale.users == 0:
        bpy.data.node_groups.remove(stale)
    ensure_decal_data_node_group()
    ensure_decal_layermask_gate_node_group()
    ensure_colormask_node_group()
    final = find_arc_texturer_node_group() or loaded
    if final is not None and _arc_texturer_has_overlay_sockets(final):
        repair_arc_texturer_metal_rough_types(final)
        apply_arc_debug_weather_defaults(final)
        apply_arc_debug_disable_mid_softlight(final)
        return final
    return None


def ensure_decal_data_node_group() -> bool:
    """Load the Decal Data helper group (RG->Z normal reconstruct + rough/metal)."""
    if find_node_group(_DECAL_DATA_GROUP) is not None:
        return True
    if not os.path.isfile(_BLEND_PATH):
        return False
    with bpy.data.libraries.load(_BLEND_PATH, link=False) as (data_from, data_to):
        if _DECAL_DATA_GROUP not in data_from.node_groups:
            print(f"Arc Raiders PSK Importer: Node group '{_DECAL_DATA_GROUP}' not in blend file")
            return False
        data_to.node_groups = [_DECAL_DATA_GROUP]
    loaded = find_node_group(_DECAL_DATA_GROUP)
    if loaded is None:
        for ng in bpy.data.node_groups:
            if ng.name.startswith(f"{_DECAL_DATA_GROUP}."):
                try:
                    ng.name = _DECAL_DATA_GROUP
                except Exception:
                    pass
                loaded = find_node_group(_DECAL_DATA_GROUP) or ng
                break
    return loaded is not None


def ensure_decal_layermask_gate_node_group() -> bool:
    """Load Decal LayerMask Gate (Material ID + bitmask → 0/1 zone gate)."""
    if find_node_group(_DECAL_LAYERMASK_GATE_GROUP) is not None:
        return True
    if not os.path.isfile(_BLEND_PATH):
        return False
    with bpy.data.libraries.load(_BLEND_PATH, link=False) as (data_from, data_to):
        if _DECAL_LAYERMASK_GATE_GROUP not in data_from.node_groups:
            print(
                f"Arc Raiders PSK Importer: Node group "
                f"'{_DECAL_LAYERMASK_GATE_GROUP}' not in blend file"
            )
            return False
        data_to.node_groups = [_DECAL_LAYERMASK_GATE_GROUP]
    loaded = find_node_group(_DECAL_LAYERMASK_GATE_GROUP)
    if loaded is None:
        for ng in bpy.data.node_groups:
            if ng.name.startswith(f"{_DECAL_LAYERMASK_GATE_GROUP}."):
                try:
                    ng.name = _DECAL_LAYERMASK_GATE_GROUP
                except Exception:
                    pass
                loaded = find_node_group(_DECAL_LAYERMASK_GATE_GROUP) or ng
                break
    return loaded is not None


def ensure_decal_layer_mask_node_group() -> bool:
    """Ensure ``Decal LayerMask``: Material ID + bitmask + Alpha → masked Alpha.

    Wraps ``Decal LayerMask Gate`` × sticker Alpha so each decal only needs one
    external node (per-material LayerMask default on the group instance).
    """
    existing = find_node_group(_DECAL_LAYER_MASK_GROUP)
    if existing is not None:
        try:
            names = {item.name for item in existing.interface.items_tree if hasattr(item, "name")}
        except Exception:
            names = set()
        if {"Material ID", "LayerMask", "Alpha"}.issubset(names) and "Alpha" in names:
            return True
        # Stale/incomplete — rebuild.
        try:
            existing.name = f"{_DECAL_LAYER_MASK_GROUP}_stale"
        except Exception:
            pass

    if not ensure_decal_layermask_gate_node_group():
        return False
    gate_tree = find_node_group(_DECAL_LAYERMASK_GATE_GROUP)
    if gate_tree is None:
        return False

    ng = bpy.data.node_groups.new(_DECAL_LAYER_MASK_GROUP, "ShaderNodeTree")
    iface = ng.interface
    iface.new_socket(name="Material ID", in_out="INPUT", socket_type="NodeSocketFloat")
    mask_sock = iface.new_socket(
        name="LayerMask", in_out="INPUT", socket_type="NodeSocketFloat"
    )
    try:
        mask_sock.default_value = 255.0
    except Exception:
        pass
    iface.new_socket(name="Alpha", in_out="INPUT", socket_type="NodeSocketFloat")
    iface.new_socket(name="Alpha", in_out="OUTPUT", socket_type="NodeSocketFloat")

    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-480.0, 0.0)
    go = ng.nodes.new("NodeGroupOutput")
    go.location = (280.0, 0.0)

    gate = ng.nodes.new("ShaderNodeGroup")
    gate.node_tree = gate_tree
    gate.label = "Zone Gate"
    gate.location = (-200.0, 80.0)

    mul = ng.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    mul.label = "Gate × Alpha"
    mul.location = (40.0, 0.0)

    ng.links.new(gi.outputs["Material ID"], gate.inputs["Material ID"])
    ng.links.new(gi.outputs["LayerMask"], gate.inputs["LayerMask"])
    ng.links.new(gate.outputs["Gate"], mul.inputs[0])
    ng.links.new(gi.outputs["Alpha"], mul.inputs[1])
    ng.links.new(mul.outputs[0], go.inputs["Alpha"])

    stale = find_node_group(f"{_DECAL_LAYER_MASK_GROUP}_stale")
    if stale is not None and stale.users == 0:
        try:
            bpy.data.node_groups.remove(stale)
        except Exception:
            pass
    return find_node_group(_DECAL_LAYER_MASK_GROUP) is not None


def ensure_colormask_node_group() -> bool:
    existing = find_node_group(_COLORMASK_GROUP)
    if existing is not None:
        return True
    if not os.path.isfile(_BLEND_PATH):
        print(f"Arc Raiders PSK Importer: Cannot find bundled blend at '{_BLEND_PATH}'")
        return False
    with bpy.data.libraries.load(_BLEND_PATH, link=False) as (data_from, data_to):
        if _COLORMASK_GROUP not in data_from.node_groups:
            print(f"Arc Raiders PSK Importer: Node group '{_COLORMASK_GROUP}' not in blend file")
            return False
        data_to.node_groups = [_COLORMASK_GROUP]
    return find_node_group(_COLORMASK_GROUP) is not None


def _curvature_id_override_has_color_map(ng) -> bool:
    """True when CurvatureID_Override takes OCM Color (not legacy float Material ID)."""
    try:
        for item in ng.interface.items_tree:
            if getattr(item, "name", None) != "Material ID Map":
                continue
            if getattr(item, "in_out", "") != "INPUT":
                continue
            sock_type = _iface_socket_type(item)
            return "Color" in sock_type or sock_type.endswith("NodeSocketColor")
    except Exception:
        pass
    return False


def _curvature_id_override_has_enable(ng) -> bool:
    """True when mixer exposes Enable (mask × Enable; default OFF passthrough)."""
    try:
        for item in ng.interface.items_tree:
            if getattr(item, "name", None) != "Enable":
                continue
            if getattr(item, "in_out", "") != "INPUT":
                continue
            return True
    except Exception:
        pass
    return False


def _curvature_id_override_has_debug(ng) -> bool:
    """True when mixer exposes integer Debug 0..3 mask-preview modes."""
    try:
        for item in ng.interface.items_tree:
            if getattr(item, "name", None) != "Debug":
                continue
            if getattr(item, "in_out", "") != "INPUT":
                continue
            return True
    except Exception:
        pass
    return False


def _curvature_id_override_is_current(ng) -> bool:
    """Color Map + Enable + Debug (post-2.18.117 mixer)."""
    return (
        ng is not None
        and _curvature_id_override_has_color_map(ng)
        and _curvature_id_override_has_enable(ng)
        and _curvature_id_override_has_debug(ng)
    )


def ensure_curvature_id_override_node_group() -> bool:
    """Load CurvatureID_Override (Arc + override BSDF mixer via OCM Color N).

    Reloads from ArcTexturer.blend when the in-memory group still has the legacy
    float ``Material ID`` socket (pre Color-input Separate Color inside group)
    or is missing the ``Enable`` passthrough gate.
    """
    existing = find_node_group(_CURVATURE_ID_OVERRIDE_GROUP)
    if existing is not None and _curvature_id_override_is_current(existing):
        return True
    if not os.path.isfile(_BLEND_PATH):
        return existing is not None
    if existing is not None:
        try:
            existing.name = f"{_CURVATURE_ID_OVERRIDE_GROUP}_stale"
        except Exception:
            pass
    with bpy.data.libraries.load(_BLEND_PATH, link=False) as (data_from, data_to):
        if _CURVATURE_ID_OVERRIDE_GROUP not in data_from.node_groups:
            print(
                f"Arc Raiders PSK Importer: Node group "
                f"'{_CURVATURE_ID_OVERRIDE_GROUP}' not found in blend file"
            )
            stale = find_node_group(f"{_CURVATURE_ID_OVERRIDE_GROUP}_stale")
            if stale is not None:
                try:
                    stale.name = _CURVATURE_ID_OVERRIDE_GROUP
                except Exception:
                    pass
            return find_node_group(_CURVATURE_ID_OVERRIDE_GROUP) is not None
        data_to.node_groups = [_CURVATURE_ID_OVERRIDE_GROUP]
    loaded = find_node_group(_CURVATURE_ID_OVERRIDE_GROUP)
    if loaded is None:
        for ng in bpy.data.node_groups:
            if ng.name.startswith(f"{_CURVATURE_ID_OVERRIDE_GROUP}."):
                try:
                    ng.name = _CURVATURE_ID_OVERRIDE_GROUP
                except Exception:
                    pass
                loaded = find_node_group(_CURVATURE_ID_OVERRIDE_GROUP) or ng
                break
    stale = find_node_group(f"{_CURVATURE_ID_OVERRIDE_GROUP}_stale")
    if stale is not None and stale.users == 0:
        try:
            bpy.data.node_groups.remove(stale)
        except Exception:
            pass
    return loaded is not None and _curvature_id_override_is_current(loaded)


def ensure_visor_node_group() -> bool:
    """Load reusable Visor glass BSDF group."""
    return ensure_node_group(_VISOR_GROUP)


def ensure_node_group(name: str) -> bool:
    if find_node_group(name) is not None:
        return True
    if not os.path.isfile(_BLEND_PATH):
        return False
    with bpy.data.libraries.load(_BLEND_PATH, link=False) as (data_from, data_to):
        if name not in data_from.node_groups:
            print(f"Arc Raiders PSK Importer: Node group '{name}' not found in blend file")
            return False
        data_to.node_groups = [name]
    return find_node_group(name) is not None

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


_PSK_EXT_ID = "io_scene_psk_psa"
# Official repo first — a second user_default copy often conflicts ("already registered").
_PSK_MODULE_CANDIDATES = (
    f"bl_ext.blender_org.{_PSK_EXT_ID}",
    f"bl_ext.user_default.{_PSK_EXT_ID}",
    _PSK_EXT_ID,
)
_PSK_BUNDLE_ZIP = "add-on-io-scene-psk-psa-v9_1_2.zip"


def psk_import_available() -> bool:
    """True when ``bpy.ops.psk.import_file`` is registered and callable."""
    try:
        bpy.ops.psk.import_file.get_rna_type()
        return True
    except Exception:
        return False


def _psk_extension_dir(repo_module: str) -> str:
    """Return ``.../extensions/<repo>/io_scene_psk_psa`` if it exists."""
    try:
        prefs = bpy.context.preferences
        for repo in prefs.extensions.repos:
            if getattr(repo, "module", "") != repo_module:
                continue
            directory = getattr(repo, "directory", "") or ""
            if not directory:
                continue
            path = os.path.join(directory, _PSK_EXT_ID)
            if os.path.isdir(path):
                return path
    except Exception:
        pass
    # Fall back to the usual Blender 5.1 AppData layout.
    appdata = os.environ.get("APPDATA") or ""
    if appdata:
        path = os.path.join(
            appdata,
            "Blender Foundation",
            "Blender",
            "5.1",
            "extensions",
            repo_module,
            _PSK_EXT_ID,
        )
        if os.path.isdir(path):
            return path
    return ""


def _scrub_psk_rna_leftovers() -> int:
    """Unregister orphaned PSK/PSA/PSX classes from a failed dual-enable."""
    import sys

    removed = 0
    for name in list(dir(bpy.types)):
        if not name.startswith(("PSK_", "PSA_", "PSX_")):
            continue
        cls = getattr(bpy.types, name, None)
        if cls is None:
            continue
        try:
            bpy.utils.unregister_class(cls)
            removed += 1
        except Exception:
            pass
    for key in list(sys.modules):
        if "io_scene_psk_psa" in key:
            try:
                del sys.modules[key]
            except Exception:
                pass
    return removed


def _enable_psk_module(module_name: str, *, scrub: bool = True) -> bool:
    """Enable a PSK/PSA module; returns True if import ops become available."""
    if not module_name:
        return False
    if scrub:
        _scrub_psk_rna_leftovers()
    try:
        import addon_utils

        addon_utils.enable(module_name, default_set=True, persistent=True)
    except Exception as exc:
        print(f"Arc Raiders PSK Importer: addon_utils.enable({module_name!r}) failed: {exc}")
    try:
        bpy.ops.preferences.addon_enable(module=module_name)
    except Exception as exc:
        # Often "already registered" after a partial enable — scrub once and retry.
        print(f"Arc Raiders PSK Importer: preferences.addon_enable({module_name!r}): {exc}")
        if scrub and "already registered" in str(exc).lower():
            _scrub_psk_rna_leftovers()
            try:
                import addon_utils

                addon_utils.enable(module_name, default_set=True, persistent=True)
            except Exception:
                pass
            try:
                bpy.ops.preferences.addon_enable(module=module_name)
            except Exception as exc2:
                print(
                    f"Arc Raiders PSK Importer: retry addon_enable({module_name!r}): {exc2}"
                )
    return psk_import_available()


def _psk_manifest_id(psk_zip: str) -> str:
    """Read extension id from blender_manifest.toml inside the bundled zip."""
    try:
        import zipfile

        with zipfile.ZipFile(psk_zip) as zf:
            if "blender_manifest.toml" not in zf.namelist():
                return _PSK_EXT_ID
            text = zf.read("blender_manifest.toml").decode("utf-8", errors="replace")
        for line in text.splitlines():
            raw = line.strip()
            if raw.startswith("id") and "=" in raw:
                value = raw.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    except Exception as exc:
        print(f"Arc Raiders PSK Importer: could not read PSK manifest id: {exc}")
    return _PSK_EXT_ID


def _uninstall_psk_repo_copy(repo_module: str) -> None:
    """Best-effort removal of a conflicting extensions-repo copy."""
    try:
        prefs = bpy.context.preferences
        for i, repo in enumerate(prefs.extensions.repos):
            if getattr(repo, "module", "") != repo_module:
                continue
            directory = getattr(repo, "directory", "") or ""
            try:
                if directory:
                    bpy.ops.extensions.package_uninstall(
                        repo_directory=directory, pkg_id=_PSK_EXT_ID
                    )
                else:
                    bpy.ops.extensions.package_uninstall(
                        repo_index=i, pkg_id=_PSK_EXT_ID
                    )
                print(
                    f"Arc Raiders PSK Importer: removed conflicting "
                    f"{repo_module}/{_PSK_EXT_ID}."
                )
            except Exception as exc:
                print(
                    f"Arc Raiders PSK Importer: could not uninstall "
                    f"{repo_module}/{_PSK_EXT_ID}: {exc}"
                )
            return
    except Exception as exc:
        print(f"Arc Raiders PSK Importer: uninstall scan failed: {exc}")


def ensure_psk_addon():
    """Ensure Unreal PSK/PSA import ops exist (Blender 5.x extension-aware).

    The bundled zip is a Blender *extension* (flat root + blender_manifest.toml).
    Legacy ``addon_install`` + enabling the first ``*/__init__.py`` segment wrongly
    picks ``psa`` and leaves ``bpy.ops.psk.import_file`` missing — so Outfits
    mesh import fails after a clean reinstall / prefs reset.

    A second failure mode is dual installs (``blender_org`` + ``user_default``)
    that leave PropertyGroup RNA registered while ``psk.import_file`` is gone.
    """
    if psk_import_available():
        return True

    # Drop a conflicting user_default duplicate when the official copy exists.
    if _psk_extension_dir("blender_org") and _psk_extension_dir("user_default"):
        _uninstall_psk_repo_copy("user_default")
        _scrub_psk_rna_leftovers()

    # 1) Prefer an already-installed extension (official repo first).
    candidates = []
    for repo_module, mod_name in (
        ("blender_org", f"bl_ext.blender_org.{_PSK_EXT_ID}"),
        ("user_default", f"bl_ext.user_default.{_PSK_EXT_ID}"),
    ):
        if _psk_extension_dir(repo_module):
            candidates.append(mod_name)
    for module_name in _PSK_MODULE_CANDIDATES:
        if module_name not in candidates:
            candidates.append(module_name)

    for module_name in candidates:
        print(f"Arc Raiders PSK Importer: enabling existing '{module_name}'...")
        if _enable_psk_module(module_name):
            try:
                bpy.ops.wm.save_userpref()
            except Exception:
                pass
            print(f"Arc Raiders PSK Importer: PSK import ready via '{module_name}'.")
            return True

    psk_zip = os.path.join(_ADDON_DIR, _PSK_BUNDLE_ZIP)
    if not os.path.isfile(psk_zip):
        print("Arc Raiders PSK Importer: Bundled PSK addon zip not found.")
        return False

    ext_id = _psk_manifest_id(psk_zip)
    print("Arc Raiders PSK Importer: Installing bundled io_scene_psk_psa extension...")
    _scrub_psk_rna_leftovers()

    # 2) Blender 5.x: install into the user_default extensions repo and enable.
    #    Skip when blender_org already has the package (avoids dual-register fights).
    if not _psk_extension_dir("blender_org"):
        try:
            bpy.ops.extensions.package_install_files(
                filepath=psk_zip,
                repo="user_default",
                enable_on_install=True,
                overwrite=True,
            )
            if psk_import_available() or _enable_psk_module(
                f"bl_ext.user_default.{ext_id}"
            ):
                try:
                    bpy.ops.wm.save_userpref()
                except Exception:
                    pass
                print(
                    f"Arc Raiders PSK Importer: installed extension "
                    f"'bl_ext.user_default.{ext_id}'."
                )
                return True
        except Exception as exc:
            print(
                f"Arc Raiders PSK Importer: extensions.package_install_files failed: {exc}"
            )
            _scrub_psk_rna_leftovers()
            if _enable_psk_module(f"bl_ext.user_default.{ext_id}"):
                try:
                    bpy.ops.wm.save_userpref()
                except Exception:
                    pass
                return True
    else:
        # Official copy on disk but enable failed above — last scrub+retry.
        if _enable_psk_module(f"bl_ext.blender_org.{ext_id}"):
            try:
                bpy.ops.wm.save_userpref()
            except Exception:
                pass
            print(
                f"Arc Raiders PSK Importer: PSK import ready via "
                f"'bl_ext.blender_org.{ext_id}'."
            )
            return True

    print(
        "Arc Raiders PSK Importer: PSK import still unavailable. "
        "Enable 'Unreal PSK/PSA (.psk/.psa)' in Preferences → Extensions."
    )
    return False
