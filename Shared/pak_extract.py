"""Pull a mesh out of shipping Arc Raiders paks and import it as .uemodel.

The extractor is ArcPak.Export.exe (self-contained). AES and the Arc 5.7
profile stay inside that exe. This module only passes the paks folder, usmap,
and asset path, then imports the written mesh with the existing material path.
"""

import os
import re
import subprocess
import hashlib
import itertools
import json
import threading
import time
from contextlib import contextmanager
from functools import lru_cache

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty
from bpy.types import Operator, OperatorFileListElement
from bpy_extras.io_utils import ImportHelper

from ..Outfit_Accessories import appearance; from . import textures, utils

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEFAULT_PAK_DIR = r"C:\Program Files (x86)\Steam\steamapps\common\Arc Raiders"
DEFAULT_PAK_PICKER_DIR = r"C:\Program Files (x86)\Steam\steamapps\common"
_DEFAULT_PAK_HAS_FILES = None
_BROWSER_WARMING = set()
BUNDLED_USMAP_FILENAME = "ArcRaiders_2.3.0_UE5.7.4_CL1438640.usmap"


def _addon_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))



class _ArcPakResult:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


_SERVE = {"proc": None, "conn": None, "broken": False, "logged": False}
_SERVE_LOCK = threading.Lock()  # guards starting/replacing the serve connection only
_SERVE_IDS = itertools.count(1)
_FRESH_RETRY_VERBS = frozenset(("extract", "mesh-metadata", "material-metadata"))
_OUT_LOCKS = {}
_OUT_LOCKS_GUARD = threading.Lock()


def _serve_log_once(msg):
    if not _SERVE["logged"]:
        _SERVE["logged"] = True
        print(f"[ArcPak] serve mode off, per-call fallback: {msg}")


def _serve_start(exe):
    proc = subprocess.Popen([exe, "serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
                            creationflags=_CREATE_NO_WINDOW)
    ready = proc.stdout.readline()
    if '"ready"' not in ready:
        proc.kill()
        raise RuntimeError(f"no ready line: {ready[:120]!r}")
    return proc


class _ServeRetired(RuntimeError):
    pass


class _ServeConn:
    """One warm ArcPak serve process shared by every thread (subprocess IO only, no bpy).

    Requests carry an "id"; one reader thread hands each reply to the caller
    waiting on that id, so replies may arrive in any order. An exporter that
    predates ids ignores the field and answers in request order without an id:
    the reader then matches the oldest pending request and later callers run
    one at a time (sequential mode).
    """

    def __init__(self, proc):
        self.proc = proc
        self.write_lock = threading.Lock()
        self.seq_lock = threading.Lock()
        self.lock = threading.Lock()
        self.pending = {}  # id -> [Event, reply]; insertion order == write order
        self.dead = None
        self.seq = False
        self.retired = False
        self.reader = threading.Thread(target=self._read, name="ArcPakServeReader", daemon=True)
        self.reader.start()

    def usable(self):
        return self.dead is None and not self.retired and self.proc.poll() is None

    def _read(self):
        reason = "serve process closed stdout"
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(reply, dict) or "rc" not in reply:
                    continue
                rid = reply.get("id")
                with self.lock:
                    if rid is None:
                        self.seq = True
                        rid = next(iter(self.pending), None)
                    slot = self.pending.pop(str(rid), None)
                    close = self.retired and not self.pending
                if slot is not None:
                    slot[1] = reply
                    slot[0].set()
                if close:
                    self._close_stdin()
        except Exception as exc:
            reason = f"serve reader failed: {exc}"
        finally:
            self._fail(reason)

    def _fail(self, reason):
        with self.lock:
            if self.dead is None:
                self.dead = reason
            slots, self.pending = list(self.pending.values()), {}
        for slot in slots:
            slot[0].set()  # reply stays None: the caller raises

    def _close_stdin(self):
        with self.write_lock:
            try:
                self.proc.stdin.close()
            except Exception:
                pass

    def call(self, args, timeout=None):
        if self.seq:
            with self.seq_lock:
                return self._call(args, timeout)
        return self._call(args, timeout)

    def _call(self, args, timeout):
        rid = f"r{next(_SERVE_IDS)}"
        slot = [threading.Event(), None]
        with self.write_lock:
            with self.lock:
                if self.dead is not None:
                    raise RuntimeError(f"serve process gone: {self.dead}")
                if self.retired:
                    raise _ServeRetired("serve process retired")
                self.pending[rid] = slot
            try:
                self.proc.stdin.write(json.dumps({"id": rid, "args": list(args)}) + "\n")
                self.proc.stdin.flush()
            except Exception as exc:
                with self.lock:
                    self.pending.pop(rid, None)
                raise RuntimeError(f"serve stdin write failed: {exc}") from exc
        if not slot[0].wait(timeout):
            with self.lock:
                self.pending.pop(rid, None)
            raise subprocess.TimeoutExpired(["serve", *args], timeout)
        reply = slot[1]
        if reply is None:
            raise RuntimeError(f"serve process died with request {rid} pending: {self.dead}")
        return _ArcPakResult(int(reply["rc"]), reply.get("log", ""), "")

    def retire(self):
        """Take no new requests; close stdin once in-flight replies are in (exe then exits)."""
        with self.lock:
            self.retired = True
            idle = not self.pending
        if idle:
            self._close_stdin()

    def stop(self, wait=2):
        with self.lock:
            self.retired = True
        self._close_stdin()
        try:
            self.proc.wait(wait)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self._fail("serve stopped")


_SERVE_GEN = [0]


def _set_conn(conn):
    _SERVE["conn"], _SERVE["proc"] = conn, (conn.proc if conn is not None else None)


def _serve_prestart(generation):
    """Worker thread: start the warm exporter before the first import (subprocess IO only)."""
    try:
        exe = exe_path()
        if not exe:
            return
        with _SERVE_LOCK:
            conn = _SERVE["conn"]
            if _SERVE_GEN[0] != generation or _SERVE["broken"] or (conn is not None and conn.usable()):
                return
            proc = _serve_start(exe)
            if _SERVE_GEN[0] != generation:
                proc.kill()
                return
            _set_conn(_ServeConn(proc))
    except Exception:
        pass  # first run_arcpak starts serve or falls back as before


_PRESTART = {"thread": None, "gen": -1}


def prestart_serve():
    """Warm the exporter at first import intent. Never call from registration:
    a running exporter locks the installed exe against Blender's ZIP overwrite."""
    if os.environ.get("ARCPAK_NO_SERVE") == "1" or os.environ.get("ARCPAK_NO_PRESTART") == "1":
        return
    generation = _SERVE_GEN[0]
    conn = _SERVE["conn"]
    if _SERVE["broken"] or (conn is not None and conn.usable()):
        return
    thread = _PRESTART["thread"]
    if thread is not None and thread.is_alive() and _PRESTART["gen"] == generation:
        return
    thread = threading.Thread(target=_serve_prestart, args=(generation,), name="ArcPakServePrestart", daemon=True)
    _PRESTART["thread"], _PRESTART["gen"] = thread, generation
    thread.start()


def stop_serve():
    _SERVE_GEN[0] += 1
    conn = _SERVE["conn"]
    _set_conn(None)
    if conn is not None:
        conn.stop()


def _retire_serve(conn):
    with _SERVE_LOCK:
        if _SERVE["conn"] is conn:
            _set_conn(None)
    conn.retire()


def _serve_conn(exe, start=_serve_start):
    waited = time.perf_counter()
    with _SERVE_LOCK:
        waited = time.perf_counter() - waited
        conn = _SERVE["conn"]
        if conn is None or not conn.usable():
            if conn is not None:
                conn.retire()
            conn = _ServeConn(start(exe))
            _set_conn(conn)
    return conn, waited


def _serve_call(command, start=_serve_start, timeout=None):
    """Send one command (argv without exe) to the warm ArcPak; raise on any serve error."""
    for attempt in (0, 1):
        conn, waited = _serve_conn(command[0], start)
        if waited >= 0.5:
            # Usually the prestart thread finishing its ready handshake; the
            # request then reuses that process instead of starting another.
            print(f"[ArcPak] serve lock wait_s={waited:.2f} verb={command[1] if len(command) > 1 else ''}")
        try:
            return conn, conn.call(command[1:], timeout)
        except _ServeRetired:
            if attempt:
                raise


@contextmanager
def _out_dir_lock(command):
    """Requests sharing an --out folder run one at a time, in arrival order.

    The exporter shares its folder lock between requests of one serve process,
    so this keeps the old one-writer-per-folder order for overlapping outputs
    (prefetch, extract, previews all write the addon cache).
    """
    try:
        out = command[list(command).index("--out") + 1]
    except (ValueError, IndexError):
        yield
        return
    key = os.path.normcase(os.path.abspath(out))
    with _OUT_LOCKS_GUARD:
        lock = _OUT_LOCKS.setdefault(key, threading.Lock())
    t = time.perf_counter()
    with lock:
        t = time.perf_counter() - t
        if t >= 0.5:
            print(f"[ArcPak] out-dir wait_s={t:.2f} verb={command[1] if len(command) > 1 else ''}")
        yield


def run_arcpak(command, timeout=600, *, start=_serve_start, fallback=None):
    """Run an ArcPak command via the warm serve process; per-call subprocess on any serve error."""
    with _out_dir_lock(command):
        if not _SERVE["broken"] and os.environ.get("ARCPAK_NO_SERVE") != "1":
            try:
                conn, served = _serve_call(command, start, timeout)
            except subprocess.TimeoutExpired:
                _SERVE["broken"] = True
                stop_serve()
                _serve_log_once(f"request timed out after {timeout}s")
                raise
            except Exception as exc:
                _SERVE["broken"] = True
                stop_serve()
                _serve_log_once(exc)
            else:
                if served.returncode == 0 or len(command) < 2 or command[1] not in _FRESH_RETRY_VERBS:
                    return served
                # The warm process keeps state across requests (mappings, sticky OOM
                # flag, providers). A cache-writing verb that fails there is retried
                # once in a fresh process so a success always stamps its meshes.
                # Other in-flight requests finish on the retired process.
                print(f"[ArcPak] serve {command[1]} rc={served.returncode}; retrying in a fresh process: "
                      f"{_log_tail(served.stdout or '', 3)}")
                _retire_serve(conn)
        if fallback is not None:
            return fallback(command)
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              creationflags=_CREATE_NO_WINDOW)


import atexit as _atexit
_atexit.register(stop_serve)

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


@lru_cache(maxsize=8)
def _mapping_sha256(path, mtime_ns, size):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _extraction_revision(scene=None) -> str:
    if scene is None:
        scene = bpy.context.scene
    mapping = resolve_usmap(scene)
    if not mapping or not os.path.isfile(mapping):
        return ''
    from ..Outfit_Accessories.pak_catalog import filesystem_path, display_path
    mapping = filesystem_path(os.path.abspath(mapping))
    stat = os.stat(mapping)
    game = configured_dir(scene)
    if not game:
        return ''
    build, depot = _installed_material_provenance(game)
    containers = []
    with os.scandir(game) as entries:
        for entry in entries:
            if entry.name.casefold().endswith(('.utoc', '.ucas', '.pak')) and entry.is_file():
                file_stat = entry.stat()
                containers.append((entry.name.casefold(), file_stat.st_size, file_stat.st_mtime_ns))
    if not containers:
        return ''
    record = {'game': os.path.normcase(os.path.abspath(display_path(game))),
              'build': build, 'depot': depot, 'containers': sorted(containers),
              'usmap': _mapping_sha256(mapping, stat.st_mtime_ns, stat.st_size)}
    return 'native-source-v1:' + hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


def mesh_cache_is_current(path: str, scene=None, allow_legacy_root=False) -> bool:
    from ..Outfit_Accessories.pak_catalog import filesystem_path
    # No container mounts RemappedPlugins/Content. Older exporters wrote there when
    # CUE4Parse guessed the wrong project and dropped /Game soft texture defaults
    # from MI JSON. Re-extract once; the newer PioneerGame/Content copy then wins.
    if not allow_legacy_root and "/remappedplugins/content/" in str(path).replace("\\", "/").casefold():
        return False
    try:
        with open(filesystem_path(path + ".arc-extract.json"), "r", encoding="utf-8") as stream:
            record = json.load(stream)
        return bool(record.get("revision")) and record["revision"] == _extraction_revision(scene)
    except (OSError, ValueError):
        return False


@lru_cache(maxsize=256)
def _mesh_metadata_state(path, model_stamp, metadata_stamp):
    from . import uemodel_import
    from ..Outfit_Accessories.native_material_slots import load_native_material_layout
    _name, version, _flip, attributes = uemodel_import._read_uemodel_root(path)
    if version < 10 or not any(name == 'SKELETON' for name, _payload in attributes):
        return True
    if metadata_stamp is None:
        return False
    _name, _version, lods = uemodel_import.parse_uemodel(path)
    return load_native_material_layout(path, lods[0].name, lods[0].materials) is not None


def mesh_material_metadata_is_current(path: str) -> bool:
    """Validate native-skinned metadata separately from geometry cache revision."""
    from ..Outfit_Accessories.pak_catalog import filesystem_path
    path = filesystem_path(path)
    if not str(path).lower().endswith('.uemodel'):
        return True
    sidecar = filesystem_path(os.path.splitext(path)[0] + '.arc-material-slots.json')
    try:
        model = os.stat(path)
        meta = os.stat(sidecar) if os.path.isfile(sidecar) else None
        return _mesh_metadata_state(path, (model.st_mtime_ns, model.st_size),
                                    (meta.st_mtime_ns, meta.st_size) if meta else None)
    except (OSError, ValueError, RuntimeError):
        return False


def material_metadata_plan(scene, model_paths):
    """Prepare only cached primary-Pak meshes; offline FModel never launches a helper."""
    if str(getattr(scene, 'arc_import_source', 'PAK')) != 'PAK':
        return None
    game = configured_dir(scene)
    if not game:
        return None
    from ..Outfit_Accessories.pak_catalog import filesystem_path, display_path
    out = cache_dir()
    # A cache-hit import skips ensure_rels, which normally registers this root;
    # exact MI lookups search only registered pak caches.
    _utils_mod = globals().get("utils")  # test stubs exec this body without utils
    if _utils_mod is not None:
        _utils_mod.set_pak_cache_root(out)
    root = os.path.normcase(os.path.abspath(display_path(out)))
    candidates = []
    for model in dict.fromkeys(model_paths):
        path = filesystem_path(model)
        if not str(path).lower().endswith('.uemodel'):
            continue
        try:
            if os.path.commonpath((root, os.path.normcase(os.path.abspath(display_path(path))))) != root:
                continue
        except ValueError:
            continue
        candidates.append(path)
    stale = [path for path in candidates if not mesh_cache_is_current(path, scene, allow_legacy_root=True)]
    if stale:
        # A mesh on disk without a current stamp (failed or interrupted batch,
        # older build) is re-extracted from the configured game, not refused.
        packages = list(dict.fromkeys(filter(None, (saved_material_package(path) for path in stale))))
        mapping = resolve_usmap(scene)
        logs = ['no exact package or usmap']
        if packages and mapping:
            print(f"[ArcPak] re-extracting {len(packages)} mesh(es) with stale source stamps")
            code, log, _meshes = extract_paths(game, mapping, packages)
            logs = [log]
            if code and len(packages) > 1:
                # One failing package fails the whole batch and clears every stamp in it,
                # so retry each still-stale package alone; 24 is the single-retry cap.
                for package in packages[:24]:
                    still_stale = any(saved_material_package(path) == package
                                      and not mesh_cache_is_current(path, scene, allow_legacy_root=True)
                                      for path in stale)
                    if still_stale:
                        _code, one_log, _meshes = extract_paths(game, mapping, [package])
                        logs.append(one_log)
        for path in stale:
            if not mesh_cache_is_current(path, scene, allow_legacy_root=True):
                full_log = '\n'.join(str(text) for text in logs)
                log_path = _write_last_failure_log(out, full_log)
                raise RuntimeError('Cached primary geometry source stamp is stale after re-extraction from the configured game: '
                                   + path + ': ' + _arcpak_error_summary(full_log, log_path))
    models, paths = [], []
    for path in candidates:
        if mesh_material_metadata_is_current(path):
            continue
        package = saved_material_package(path)
        if not package:
            raise RuntimeError('Cannot recover exact native mesh package for metadata: ' + path)
        models.append(path); paths.append(package)
    if not models:
        return None
    mapping = resolve_usmap(scene)
    if not mapping:
        raise RuntimeError('Select a usmap before preparing native mesh metadata')
    return {'models': models, 'paths': paths,
            'command': extraction_command(game, mapping, paths, out_dir=out,
                                           metadata_only=True, model_files=models)}


def _write_last_failure_log(cache_root, text):
    folder = os.path.join(cache_root, 'logs')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, 'arcpak-last-failure.log')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(text)
    return path


def _arcpak_error_summary(log, log_path):
    lines = [line.strip() for line in str(log).splitlines()]
    is_error = lambda line: 'ERR]' in line or 'FTL]' in line
    first = next((i for i, line in enumerate(lines) if is_error(line)), None)
    head = [lines[first]] if first is not None else []
    if first is not None:
        head += [line for line in lines[first + 1:first + 4] if 'Exception' in line][:1]
    detail = [line for line in lines if is_error(line) and any(m in line for m in ('export error', 'load failed', 'json failed'))]
    errors = list(dict.fromkeys(head + detail))[:6]
    done = [line for line in lines if 'Done queued=' in line][-1:]
    picked = errors + done or [_log_tail(log)]
    return ' | '.join(picked) + ' | full log: ' + log_path


def ensure_cached_mesh_material_metadata(scene, model_paths):
    """One bounded metadata-only helper call; no mesh/texture/blend re-export."""
    plan = material_metadata_plan(scene, model_paths)
    if plan is None:
        ensure_cached_material_hierarchy(scene, model_paths=model_paths)
        return
    proc = run_arcpak(plan['command'])
    log = ((proc.stdout or '') + '\n' + (proc.stderr or '')).strip()
    start_pending_previews()
    if proc.returncode:
        raise RuntimeError('Native mesh metadata preparation failed: ' + _log_tail(log))
    missing = [path for path in plan['models'] if not mesh_material_metadata_is_current(path)]
    if missing:
        raise RuntimeError('Native mesh metadata does not match cached geometry: ' + str(missing))
    ensure_cached_material_hierarchy(scene, model_paths=model_paths)


def _material_companion(path):
    from ..Outfit_Accessories.pak_catalog import filesystem_path
    return filesystem_path(path if path.casefold().endswith('.arc-material.json') else os.path.splitext(path)[0] + '.arc-material.json')


@lru_cache(maxsize=512)
def _raw_material_record(path, stamp):
    with open(path, encoding='utf-8-sig') as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError('Malformed raw material metadata: ' + path)
    return data


def _material_hierarchy_dependencies(material_file, build='', depot=''):
    """Named native raw metadata, exact physical alias and parent closure proof."""
    from ..Outfit_Accessories.pak_catalog import canonical_path, filesystem_path, display_path
    path = filesystem_path(material_file)
    physical = display_path(path).replace('\\', '/')
    def plugin(package):
        # A mounted plugin is cached at <cache>/<Plugin>/... with no Content folder (EmbarkScript templates).
        return ('/' in package and '/content/' not in package.casefold()
                and package.split('/', 1)[0].casefold() not in ('pioneergame', 'remappedplugins', 'engine', 'game', 'indexes'))
    marker = physical.casefold().find('/content/pioneer/')
    if marker >= 0:
        mount_root = physical[:marker]
        cache_root = mount_root.rsplit('/', 1)[0]
        relative = physical[marker + len('/content/'):]
        if relative.casefold().endswith('.arc-material.json'):
            relative = relative[:-18]
        elif relative.casefold().endswith('.json'):
            relative = relative[:-5]
        else:
            raise ValueError('Material metadata requires exact JSON source')
        expected = canonical_path('PioneerGame/Content/' + relative)
    else:
        # A plugin root names its own package; its location under the cache must agree.
        companion = display_path(_material_companion(path)).replace('\\', '/')
        try:
            stat = os.stat(filesystem_path(companion))
        except FileNotFoundError:
            return None
        source = _raw_material_record(filesystem_path(companion), (stat.st_mtime_ns, stat.st_size)).get('DataRaidersSource') or {}
        if not source.get('package'):
            return None
        expected = canonical_path(source['package'])
        suffix = '/' + expected + '.arc-material.json'
        if not plugin(expected) or not companion.casefold().endswith(suffix.casefold()):
            raise ValueError('Material path has no exact native mount: ' + physical)
        cache_root = companion[:-len(suffix)]
        mount_root = cache_root + '/PioneerGame'
    visited = set()
    dependencies = []
    for depth in range(64):
        if expected.casefold().startswith('pioneergame/content/'):
            sidecar = filesystem_path(mount_root + '/Content/' + expected[len('PioneerGame/Content/'):] + '.arc-material.json')
        elif plugin(expected):
            sidecar = filesystem_path(cache_root + '/' + expected + '.arc-material.json')
        else:
            raise ValueError('Unsupported exact material parent mount for raw cache upgrade: ' + expected)
        if expected.casefold() in visited:
            raise ValueError('Native material parent cycle: ' + expected)
        visited.add(expected.casefold())
        try:
            stat = os.stat(sidecar)
        except FileNotFoundError:
            return None
        data = _raw_material_record(sidecar, (stat.st_mtime_ns, stat.st_size))
        source = data.get('DataRaidersSource')
        if not source:
            return None  # Old raw exports need a provenance-only refresh.
        if source.get('schema') != 'dataraiders.native-material-hierarchy' or source.get('version') != 1:
            return None
        if canonical_path(source.get('package', '')).casefold() != expected.casefold() or canonical_path(source.get('object_path', '')).casefold() != expected.casefold():
            raise ValueError('Exact native material provenance mismatch: ' + sidecar)
        exports = data.get('Exports')
        if not isinstance(exports, list) or len(exports) != 1 or str(exports[0].get('Name', '')).casefold() != expected.rsplit('/', 1)[-1].casefold():
            raise ValueError('Raw material export identity mismatch: ' + sidecar)
        if (build and source.get('build_id') != build) or (depot and source.get('depot_manifest') != depot):
            return None
        parent = source.get('parent', '')
        properties = exports[0].get('Properties', exports[0])
        raw_parent = properties.get('Parent') or ''
        if isinstance(raw_parent, dict):
            raw_parent = raw_parent.get('ObjectPath') or raw_parent.get('AssetPathName') or ''
        if not isinstance(raw_parent, str) or bool(raw_parent) != bool(parent) or (raw_parent and canonical_path(raw_parent).casefold() != canonical_path(parent).casefold()):
            raise ValueError('Raw material Parent disagrees with native provenance: ' + sidecar)
        if not isinstance(parent, str):
            raise ValueError('Malformed native Parent provenance: ' + sidecar)
        dependencies.append(sidecar)
        if not parent:
            return dependencies
        expected = canonical_path(parent)
    raise ValueError('Native material parent depth exceeds 64')


@lru_cache(maxsize=512)
def _raw_material_digest(path, mtime_ns, size):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _material_dependency_signature(paths):
    from ..Outfit_Accessories.pak_catalog import display_path, filesystem_path
    result = []
    for path in paths:
        # Closure members can cross MAX_PATH by suffix alone (Driftcoat's ...InnerSleeves_Cotton.arc-material.json.textures.json, 260).
        native = filesystem_path(path)
        stat = os.stat(native)
        result.append({'path': os.path.normcase(os.path.abspath(display_path(path))),
                       'sha256': _raw_material_digest(native, stat.st_mtime_ns, stat.st_size)})
    return result


def _material_receipt_root(path):
    from ..Outfit_Accessories.pak_catalog import display_path
    return os.path.normcase(os.path.abspath(display_path(_material_companion(path))))


def material_hierarchy_is_current(material_file, build='', depot='', source_revision='', *, metadata_revision='', producer=None, mapping_sha=''):
    from .material_texture_metadata import (RECEIPT_VERSION, RECIPE, producer_signature,
        metadata_revision as texture_revision, closure_dependencies)
    if not source_revision:
        return False
    try:
        from ..Outfit_Accessories.pak_catalog import filesystem_path
        with open(filesystem_path(_material_companion(material_file) + '.ready'), encoding='utf-8') as stream:
            receipt = json.load(stream)
    except (FileNotFoundError, ValueError):
        return False
    # Version1 proved raw parents only. It must upgrade once even when every
    # existing mesh/PNG remains current under the unchanged source revision.
    if receipt.get('schema') != 'dataraiders.material-hierarchy-ready' or receipt.get('version') != RECEIPT_VERSION or receipt.get('recipe') != RECIPE:
        return False
    producer = producer if producer is not None else producer_signature(exe_path())
    metadata_revision = metadata_revision or texture_revision(source_revision, producer)
    if receipt.get('metadata_revision') != metadata_revision or receipt.get('producer_assemblies') != producer:
        return False
    if mapping_sha and receipt.get('mapping_sha256') != mapping_sha:
        return False
    dependencies = _material_hierarchy_dependencies(material_file, build, depot)
    if dependencies is None:
        return False
    textures = closure_dependencies(_material_companion(material_file), dependencies, build, depot, producer, mapping_sha or receipt.get('mapping_sha256',''))
    if textures is None:
        return False
    return (receipt.get('root') == _material_receipt_root(material_file)
            and receipt.get('revision') == source_revision
            and receipt.get('dependencies') == _material_dependency_signature(dependencies + textures))


def _write_material_receipt(material_file, revision, dependencies, *, metadata_revision='', producer=None, mapping_sha=''):
    import time
    import uuid
    from ..Outfit_Accessories.pak_catalog import filesystem_path
    # The temp name adds 43 characters, enough to cross MAX_PATH under the installed addon cache
    # (MI_ShoulderPadSkull_ShoulderPadSkull, 283), so both names take the long-path form on their own length.
    target = filesystem_path(_material_companion(material_file) + '.ready')
    temp = filesystem_path(target + '.tmp-' + uuid.uuid4().hex)
    from .material_texture_metadata import RECEIPT_VERSION, RECIPE
    data = {'schema': 'dataraiders.material-hierarchy-ready', 'version': RECEIPT_VERSION, 'recipe': RECIPE,
            'root': _material_receipt_root(material_file), 'revision': revision,
            'metadata_revision': metadata_revision, 'producer_assemblies': producer, 'mapping_sha256': mapping_sha,
            'dependencies': _material_dependency_signature(dependencies)}
    try:
        with open(temp, 'x', encoding='utf-8') as stream:
            json.dump(data, stream, sort_keys=True)
        for attempt in range(7):
            try:
                os.replace(temp, target)
                break
            except OSError as error:
                if attempt == 6 or getattr(error, 'winerror', None) not in (5, 32, 33):
                    raise
                time.sleep(0.01 * (2 ** attempt))
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _installed_material_provenance(game):
    from pathlib import Path
    from ..Outfit_Accessories import pak_catalog
    build = pak_catalog.installed_build(game)
    depot = ''
    for folder in (Path(game).absolute(), *tuple(Path(game).absolute().parents)[:8]):
        if folder.name.casefold() != 'steamapps':
            continue
        try:
            text = (folder / 'appmanifest_1808500.acf').read_text(encoding='utf-8-sig')
        except FileNotFoundError:
            break
        installed = text.split('"InstalledDepots"', 1)
        if len(installed) == 2:
            match = re.search(r'"1808501"\s*\{[^{}]*"manifest"\s*"(\d{1,32})"', installed[1])
            depot = match.group(1) if match else ''
        break
    return build, depot


def raw_material_metadata_plan(scene, *, model_paths=(), material_paths=()):
    """Capture exact material roots on the main thread; no worker bpy access."""
    if str(getattr(scene, 'arc_import_source', 'PAK')) != 'PAK':
        return None
    game = configured_dir(scene)
    if not game:
        return None
    from ..Outfit_Accessories.pak_catalog import filesystem_path, display_path
    out = cache_dir()
    # A cache-hit import skips ensure_rels, which normally registers this root;
    # exact MI lookups search only registered pak caches.
    _utils_mod = globals().get("utils")  # test stubs exec this body without utils
    if _utils_mod is not None:
        _utils_mod.set_pak_cache_root(out)
    root = os.path.normcase(os.path.abspath(display_path(out)))
    def owned(path):
        try:
            return os.path.commonpath((root, os.path.normcase(os.path.abspath(display_path(path))))) == root
        except ValueError:
            return False
    files = list(material_paths)
    for model in dict.fromkeys(model_paths):
        if not owned(model):
            continue
        sidecar = filesystem_path(os.path.splitext(model)[0] + '.arc-material-slots.json')
        if os.path.isfile(sidecar):
            with open(sidecar, encoding='utf-8-sig') as stream:
                layout = json.load(stream)
            for slot in layout['materials']:
                reference = slot.get('material')
                if reference:
                    path = textures.find_asset_from_object_path(reference, '.json')
                    if not path:
                        raise FileNotFoundError('Exact native material cache source missing: ' + reference)
                    if not owned(path):
                        raise ValueError('Owned primary mesh default material resolved to a foreign cache: ' + reference)
                    files.append(path)
        for binding in textures.authored_visual_part_materials(model):
            if not owned(binding['path']):
                raise ValueError('Owned primary mesh DA material resolved to a foreign cache: ' + str(binding.get('material_path', '')))
            files.append(binding['path'])
    build, depot = _installed_material_provenance(game)
    revision = _extraction_revision(scene)
    if not revision:
        raise RuntimeError('Cannot establish current game/mapping revision for raw material upgrade')
    from .material_texture_metadata import producer_signature, metadata_revision as texture_revision
    producer = producer_signature(exe_path())
    metadata_revision = texture_revision(revision, producer)
    mapping = resolve_usmap(scene)
    if not mapping or not os.path.isfile(mapping):
        raise RuntimeError('Select a current usmap before preparing compact texture metadata')
    mapping_stat = os.stat(mapping)
    mapping_sha = _mapping_sha256(mapping, mapping_stat.st_mtime_ns, mapping_stat.st_size)
    selected, packages = [], []
    for file in dict.fromkeys(files):
        path = filesystem_path(file)
        if not owned(path):
            continue  # FModel/foreign caches never invoke a primary-game helper.
        package_source = path[:-18] + '.json' if path.casefold().endswith('.arc-material.json') else path
        package = saved_material_package(package_source) or cached_plugin_package(package_source, display_path(out))
        if not package:
            raise ValueError('Exact material package cannot be recovered: ' + path)
        if not material_hierarchy_is_current(path, build, depot, revision, metadata_revision=metadata_revision, producer=producer, mapping_sha=mapping_sha):
            selected.append(path); packages.append(package)
    if not selected:
        return None
    mapping = resolve_usmap(scene)
    if not mapping:
        raise RuntimeError('Select a usmap before preparing raw material metadata')
    return {'files': selected, 'paths': packages, 'build': build, 'depot': depot, 'revision': revision,
            'metadata_revision': metadata_revision, 'producer': producer, 'mapping_sha': mapping_sha,
            'command': extraction_command(game, mapping, packages, out_dir=out,
                        material_metadata_only=True, material_files=selected)}


def complete_raw_material_metadata(scene, plan, code, log):
    if code or re.search(r'(?im)^\s*\[[^\]\r\n]*\bERR\]|ParserException|Failed to deserialize', log or ''):
        raise RuntimeError('Native material hierarchy preparation failed: ' + _log_tail(log))
    from .material_texture_metadata import producer_signature, metadata_revision as texture_revision, closure_dependencies
    producer = producer_signature(exe_path())
    if producer != plan['producer'] or texture_revision(plan['revision'], producer) != plan['metadata_revision']:
        raise RuntimeError('Compact metadata producer changed during upgrade; completion rejected')
    if _extraction_revision(scene) != plan['revision']:
        raise RuntimeError('Game or mapping changed during raw material metadata export; completion rejected')
    closures = []
    for path in plan['files']:
        dependencies = _material_hierarchy_dependencies(path, plan['build'], plan['depot'])
        if dependencies is None:
            raise RuntimeError('Native material hierarchy does not match exact source: ' + path)
        textures = closure_dependencies(_material_companion(path), dependencies, plan['build'], plan['depot'], producer, plan['mapping_sha'])
        if textures is None:
            raise RuntimeError('Required compact native texture metadata is missing/stale: ' + path)
        closures.append((path, dependencies + textures))
    for path, dependencies in closures:
        _write_material_receipt(path, plan['revision'], dependencies, metadata_revision=plan['metadata_revision'], producer=producer, mapping_sha=plan['mapping_sha'])
    if _extraction_revision(scene) != plan['revision'] or producer_signature(exe_path()) != producer:
        raise RuntimeError('Game, mapping or producer changed during raw material receipt commit; completion rejected')
    if any(not material_hierarchy_is_current(path, plan['build'], plan['depot'], plan['revision'], metadata_revision=plan['metadata_revision'], producer=producer, mapping_sha=plan['mapping_sha']) for path in plan['files']):
        raise RuntimeError('Raw material dependency changed during receipt commit; completion rejected')
    from .materials.rebuild.assets import invalidate_index
    invalidate_index()


_PREFETCH = {"job": None}


def _prefetch_run(job):
    """Worker thread: subprocess IO only. The request goes to the same warm serve
    process as the main thread (one process, one mount); run_arcpak orders it
    against other requests that write the same cache folder."""
    try:
        if job['cancelled']:
            return
        proc = run_arcpak(job['plan']['command'])
        job['result'] = (proc.returncode, ((proc.stdout or '') + '\n' + (proc.stderr or '')).strip())
    except Exception as exc:
        job['error'] = exc
    finally:
        job['t1'] = time.perf_counter()


def _stop_prefetch_proc(job):
    proc = job.get('proc')
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
        except Exception:
            pass


def stop_material_metadata_prefetch():
    job, _PREFETCH['job'] = _PREFETCH['job'], None
    if job is not None:
        job['cancelled'] = True
        _stop_prefetch_proc(job)


def start_material_metadata_prefetch(scene, meshes, materials=()):
    """Start the raw material-metadata pass for fresh meshes while the import continues.

    The plan (all bpy/cache reads) is built here on the main thread; the worker
    only runs the exporter. ensure_cached_material_hierarchy joins it, then
    validates and commits receipts on the main thread with the usual checks.
    """
    if _PREFETCH['job'] is not None or os.environ.get('ARCPAK_NO_PREFETCH') == '1':
        return
    try:
        plan = raw_material_metadata_plan(scene, model_paths=meshes, material_paths=materials)
    except Exception:
        return  # the regular path raises the same error where it is needed
    if plan is None:
        return
    job = {'plan': plan, 'proc': None, 'result': None, 'error': None, 'cancelled': False,
           't0': time.perf_counter(), 't1': None}
    job['thread'] = threading.Thread(target=_prefetch_run, args=(job,), name='ArcPakMetadataPrefetch', daemon=True)
    _PREFETCH['job'] = job
    job['thread'].start()


def _join_material_metadata_prefetch(scene):
    job, _PREFETCH['job'] = _PREFETCH['job'], None
    if job is None:
        return
    t = time.perf_counter()
    job['thread'].join()
    wait = time.perf_counter() - t
    run = (job['t1'] or time.perf_counter()) - job['t0']
    print(f"[ArcPak] metadata prefetch files={len(job['plan']['files'])} run_s={run:.2f} "
          f"overlap_s={max(0.0, run - wait):.2f} wait_s={wait:.2f}")
    if job['error'] is not None or job['result'] is None or job['result'][0]:
        return  # the regular pass below reruns and reports the failure
    try:
        complete_raw_material_metadata(scene, job['plan'], *job['result'])
    except Exception as exc:
        print(f"[ArcPak] metadata prefetch rejected, rerunning: {exc}")
    else:
        start_pending_previews()


_atexit.register(stop_material_metadata_prefetch)
_HIERARCHY_BATCH = threading.local()


@contextmanager
def batched_material_hierarchy(scene):
    """Defer hierarchy requests inside the block to one helper call (an outfit's parts).

    Each part used to launch its own ArcPak metadata pass (about 20 per outfit).
    Requests are still exact per path; only the launches are merged. A failed
    block launches nothing, and nested blocks join the outermost one.
    """
    if getattr(_HIERARCHY_BATCH, 'pending', None) is not None:
        yield
        return
    pending = _HIERARCHY_BATCH.pending = ([], [])
    try:
        yield
    finally:
        _HIERARCHY_BATCH.pending = None
    if pending[0] or pending[1]:
        ensure_cached_material_hierarchy(scene, model_paths=pending[0], material_paths=pending[1])


def ensure_cached_material_hierarchy(scene, *, model_paths=(), material_paths=()):
    pending = getattr(globals().get('_HIERARCHY_BATCH'), 'pending', None)
    if pending is not None:
        pending[0].extend(model_paths); pending[1].extend(material_paths)
        return
    _join_material_metadata_prefetch(scene)
    plan = raw_material_metadata_plan(scene, model_paths=model_paths, material_paths=material_paths)
    if plan is None:
        return
    proc = run_arcpak(plan['command'])
    complete_raw_material_metadata(scene, plan, proc.returncode, ((proc.stdout or '') + '\n' + (proc.stderr or '')).strip())
    start_pending_previews()


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


_PAK_FOLDER_CACHE = {}
_PAK_FOLDER_TTL_S = 30.0


def resolve_pak_folder(path: str) -> str:
    """Cached ``_resolve_pak_folder``: one import asks for the Paks folder ~200 times and the
    uncached walk costs ~60 ms of path checks each. A hit is reused for 30 s while it still
    exists, so a moved or newly installed game is picked up within that window."""
    import time as _time
    key = os.path.normcase(os.path.abspath(path)) if path else ''
    now = _time.monotonic()
    cache = globals().setdefault('_PAK_FOLDER_CACHE', {})
    ttl = globals().get('_PAK_FOLDER_TTL_S', 30.0)
    hit = cache.get(key) if os.environ.get("DR_PAK_FOLDER_CACHE") != "0" else None
    if hit is not None and now - hit[1] < ttl and (not hit[0] or os.path.isdir(hit[0])):
        return hit[0]
    result = _resolve_pak_folder(path)
    cache[key] = (result, now)
    return result


def _resolve_pak_folder(path: str) -> str:
    """Resolve any selected folder in the Steam library to Arc Raiders Paks.

    Handles Paks, Content, PioneerGame, Arc Raiders, common, steamapps, and
    Steam/library roots. Steam roots are resolved through libraryfolders.vdf
    instead of recursively scanning every installed game.
    """
    if not path or not os.path.isdir(path):
        return ''

    def add(candidates, value):
        if value:
            normalized = os.path.normpath(os.path.abspath(value))
            key = os.path.normcase(normalized)
            if key not in candidates[0]:
                candidates[0].add(key)
                candidates[1].append(normalized)

    candidates = (set(), [])
    selected = os.path.normpath(os.path.abspath(path))
    add(candidates, selected)
    cursor = selected
    while cursor:
        leaf = os.path.basename(cursor).casefold()
        if leaf == 'paks':
            add(candidates, cursor)
        elif leaf == 'content':
            add(candidates, os.path.join(cursor, 'Paks'))
        elif leaf == 'pioneergame':
            add(candidates, os.path.join(cursor, 'Content', 'Paks'))
        elif leaf == 'arc raiders':
            add(candidates, os.path.join(cursor, 'PioneerGame', 'Content', 'Paks'))
        elif leaf == 'common':
            add(candidates, os.path.join(cursor, 'Arc Raiders', 'PioneerGame', 'Content', 'Paks'))
        elif leaf == 'steamapps':
            add(candidates, os.path.join(cursor, 'common', 'Arc Raiders', 'PioneerGame', 'Content', 'Paks'))

        # Steam's manifest enumerates secondary libraries such as D:\\SteamLibrary.
        steamapps = os.path.join(cursor, 'steamapps')
        vdf = os.path.join(steamapps, 'libraryfolders.vdf')
        if os.path.isfile(vdf):
            try:
                with open(vdf, 'r', encoding='utf-8-sig', errors='replace') as stream:
                    contents = stream.read()
                libraries = re.findall(r'"path"\s+"([^"]+)"', contents, re.IGNORECASE)
                if not libraries:
                    # Older Steam clients stored numeric library entries directly.
                    libraries = re.findall(r'^\s*"\d+"\s+"([^"]+)"\s*$', contents, re.MULTILINE)
                for library in libraries:
                    library = library.replace('\\\\', '\\')
                    add(candidates, os.path.join(library, 'steamapps', 'common', 'Arc Raiders',
                                                  'PioneerGame', 'Content', 'Paks'))
            except OSError:
                pass

        parent = os.path.dirname(cursor)
        if parent == cursor:
            break
        cursor = parent

    for candidate in candidates[1]:
        if pak_dir_has_game_files(candidate):
            return candidate
    return ''


def default_pak_has_files() -> bool:
    return bool(resolve_pak_folder(DEFAULT_PAK_DIR))


def reconcile_pak_dir(scene, *, restore_default: bool = False) -> None:
    """Use the Steam Paks folder when it has game files. Clear that default when it does not."""
    if scene is None or not hasattr(scene, "arc_pak_dir"):
        return
    current = getattr(scene, "arc_pak_dir", "") or ""
    has = default_pak_has_files()
    if current and not resolve_pak_folder(bpy.path.abspath(current)):
        scene.arc_pak_dir = ""
        scene["arc_pak_dir_user_cleared"] = True
        return
    if restore_default and not current:
        scene["arc_pak_dir_user_cleared"] = False
    if has and not current and not scene.get("arc_pak_dir_user_cleared"):
        scene.arc_pak_dir = DEFAULT_PAK_DIR


def configured_dir(scene) -> str:
    """Resolve the configured Steam/game folder to its Paks directory."""
    path = bpy.path.abspath(getattr(scene, "arc_pak_dir", "") or "") if scene is not None else ""
    return resolve_pak_folder(path) if path else ""


def _initialize_pak_defaults():
    """Retired startup callback. PAK validation belongs to explicit actions."""
    return None


@bpy.app.handlers.persistent
def _load_pak_defaults(_unused=None):
    _initialize_pak_defaults()


def register_defaults():
    # Starts no exporter process: the import operator's invoke calls prestart_serve().
    unregister_defaults()


def unregister_defaults():
    stop_prefetch = globals().get('stop_material_metadata_prefetch')
    if stop_prefetch:
        stop_prefetch()
    stop_serve()
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


def saved_material_package(source: str) -> str:
    """Canonical identity of a physical saved game source, or empty if untrusted."""
    from ..Outfit_Accessories import pak_catalog
    original = str(source or "")
    text = pak_catalog.display_path(original).replace("\\", "/")
    low = text.casefold()
    marker = "/content/pioneer/"
    offset = low.find(marker)
    if offset < 0:
        return ""
    # A coincidental user folder named Content/Pioneer is not game provenance.
    owner = low[:offset].rstrip("/").rsplit("/", 1)[-1]
    if owner not in ("pioneergame", "remappedplugins"):
        return ""
    relative = text[offset + len("/content/"):]
    extension = os.path.splitext(relative)[1].lower()
    if extension not in (".psk", ".pskx", ".uemodel", ".json", ".png"):
        return ""
    try:
        return pak_catalog.canonical_path("PioneerGame/Content/" + relative)
    except ValueError:
        return ""


def cached_plugin_package(source: str, cache_root: str) -> str:
    """Mounted-plugin package of an exporter cache file, or empty.

    ArcPak writes plugin sources under their mount name with no Content folder
    (<cache>/EmbarkScript/ClothingAssetTemplates/M_Character_ClothMaterial.json, Banshee's cloth parent), so the
    cache-relative path is the package the exporter itself resolved.
    """
    from ..Outfit_Accessories import pak_catalog
    try:
        relative = os.path.relpath(os.path.abspath(pak_catalog.display_path(str(source or ''))),
                                   os.path.abspath(cache_root)).replace('\\', '/')
    except ValueError:
        return ''
    head = relative.split('/', 1)[0].casefold()
    if relative.startswith('../') or '/' not in relative or head in ('pioneergame', 'remappedplugins', 'engine', 'indexes'):
        return ''
    if os.path.splitext(relative)[1].lower() != '.json':
        return ''
    try:
        return pak_catalog.canonical_path(relative)
    except ValueError:
        return ''


def recover_saved_material_source(source: str, roots, *, native_pak=False) -> dict:
    """Resolve an old physical game path by exact package in explicit cache roots.

    Pure filesystem lookup; never walks or borrows a basename. A native UEModel
    fallback supplies a material folder only, retaining the original source format.
    """
    from ..Outfit_Accessories import pak_catalog
    original = str(source or "")
    package = saved_material_package(original)
    if not package:
        return {}
    extension = os.path.splitext(original)[1].lower()
    seen = set()
    search_roots = []
    for root in roots:
        if not root:
            continue
        root = os.path.normpath(str(root))
        key = os.path.normcase(os.path.abspath(root))
        if key not in seen:
            seen.add(key)
            search_roots.append(root)
    extensions = [extension]
    if native_pak and extension in (".psk", ".pskx"):
        extensions.append(".uemodel")
    # Format-preserving copies in any explicit root beat a UEModel fallback.
    for candidate_extension in extensions:
        for root in search_roots:
            matches = []
            for alias in _package_aliases(package):
                direct = os.path.join(root, *alias.split("/"))
                leaf = alias.rsplit("/", 1)[-1]
                for path in (direct + candidate_extension,
                             os.path.join(direct, leaf + candidate_extension)):
                    path = pak_catalog.filesystem_path(path)
                    if os.path.isfile(path):
                        matches.append(path)
            if matches:
                actual = max(matches, key=lambda path: os.stat(path).st_mtime_ns)
                same_format = candidate_extension == extension
                lookup = actual if same_format else os.path.splitext(actual)[0] + extension
                return {"path": lookup, "actual_path": actual,
                        "package": package, "same_format": same_format,
                        "original": original}
        if candidate_extension == extension:
            # The stamped file itself is explicit provenance, even after an
            # offline restart with no registered roots. Probe only that file;
            # never discover an unregistered tree. A real PSK beats a native
            # UEModel folder fallback and preserves its source-unit semantics.
            supplied = pak_catalog.filesystem_path(original)
            if os.path.isfile(supplied):
                return {"path": supplied, "actual_path": supplied,
                        "package": package, "same_format": True,
                        "original": original}
    return {}


def _expected_uemodel(out_dir: str, asset_path: str) -> str:
    from ..Outfit_Accessories import pak_catalog
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
    from ..Outfit_Accessories import pak_catalog
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
    from ..Outfit_Accessories import pak_catalog
    game = configured_dir(scene)
    if not game:
        return None
    bundled = os.path.join(_addon_dir(), "ArcPak", "mappings", "pak-index.bin")
    index = pak_catalog.find_index(game, index_dir(), bundled)
    if index is None and allow_reindex:
        code, log = reindex(game, resolve_usmap(scene))
        if code:
            raise RuntimeError(_log_tail(log))
        index = pak_catalog.find_index(game, index_dir())
    return index


def indexed_package_paths(scene, *, mesh_only: bool = False) -> tuple[str, ...]:
    from ..Outfit_Accessories import pak_catalog
    index = load_pak_index(scene)
    if index is None:
        return ()
    return pak_catalog.mesh_paths(index) if mesh_only else index["paths"]


def indexed_mesh_paths(scene) -> tuple[str, ...]:
    return indexed_package_paths(scene, mesh_only=True)


def prewarm_pak_browser(scene):
    """Build selectors from an existing index without blocking addon startup."""
    game = configured_dir(scene)
    if not game:
        return
    cache, indexes = cache_dir(), index_dir()
    bundled = os.path.join(_addon_dir(), "ArcPak", "mappings", "pak-index.bin")
    key = (game, cache, indexes)
    if key in _BROWSER_WARMING:
        return
    _BROWSER_WARMING.add(key)
    def worker():
        try:
            from ..Outfit_Accessories import pak_catalog
            index = pak_catalog.find_index(game, indexes, bundled)
            if index is not None:
                pak_catalog.browser_start(pak_catalog.browser_tree(index, cache, game))
        except Exception as exc:
            print(f"Arc Pak: browser prebuild deferred: {exc}")
        finally:
            _BROWSER_WARMING.discard(key)
    threading.Thread(target=worker, name='ArcPak browser prebuild', daemon=True).start()


def ensure_pak_browser(scene) -> tuple[dict, str]:
    from ..Outfit_Accessories import pak_catalog
    index = load_pak_index(scene, allow_reindex=True)
    if index is None:
        raise RuntimeError("No current Pak mesh index. Load / Refresh Paks in Settings.")
    return index, pak_catalog.browser_tree(index, cache_dir(), configured_dir(scene))


def extraction_command(game_dir: str, usmap: str, paths: list[str], *, out_dir=None, metadata_only=False, model_files=(), material_metadata_only=False, material_files=()) -> list[str]:
    """Build extraction arguments on the main thread; workers receive strings only."""
    exe = exe_path()
    if not exe:
        raise FileNotFoundError("ArcPak.Export.exe is missing; reinstall the packaged addon")
    if metadata_only and material_metadata_only:
        raise ValueError("Choose one metadata mode")
    cmd = [exe, "material-metadata" if material_metadata_only else "mesh-metadata" if metadata_only else "extract", "--game-dir", game_dir, "--usmap", usmap,
           "--out", out_dir or cache_dir(), "--index-dir", index_dir()]
    for asset in paths:
        cmd.extend(["--path", asset])
    if model_files:
        if not metadata_only or len(model_files) != len(paths):
            raise ValueError("Exact metadata model files must match mesh package count")
        for model in model_files:
            cmd.extend(["--model-file", model])
    if material_files:
        if not material_metadata_only or len(material_files) != len(paths):
            raise ValueError("Exact metadata material files must match material package count")
        for material in material_files:
            cmd.extend(["--material-file", material])
    bundled_index = os.path.join(_addon_dir(), "ArcPak", "mappings", "pak-index.bin")
    if os.path.isfile(bundled_index):
        cmd.extend(["--index", bundled_index])
    return cmd


def complete_extraction(out_dir, paths, code, log, revision):
    """Finalize cache state on the main thread after an exporter has finished."""
    global _LAST_EXTRACT_LOG
    _LAST_EXTRACT_LOG = log
    from ..Outfit_Accessories.pak_catalog import filesystem_path
    meshes = _collect_uemodels(out_dir, paths)
    producer = _extraction_producer() if code == 0 else None
    for mesh in meshes:
        stamp = filesystem_path(mesh + ".arc-extract.json")
        if code == 0:
            with open(stamp, "w", encoding="utf-8") as stream:
                json.dump({"revision": revision, "producer": producer}, stream)
        else:
            # A failed overwrite may leave incomplete metadata beside an old
            # current stamp. Force the next import to retry the extraction.
            try:
                os.remove(stamp)
            except FileNotFoundError:
                pass
    _finish_extraction(out_dir)
    return meshes


def _finish_extraction(out_dir):
    utils.set_pak_cache_root(out_dir)
    textures.clear_clothing_path_caches()
    from .materials.common import clear_material_session_caches
    from .materials.rebuild.assets import invalidate_index
    clear_material_session_caches()
    invalidate_index(out_dir)
    appearance._ICON_SCAN_CACHE.clear()


def _extraction_producer():
    """Exporter assemblies that write the cache; None when the bundle is incomplete."""
    from .material_texture_metadata import producer_signature
    try:
        return producer_signature(exe_path())
    except (OSError, ValueError):
        return None


def current_extracted_meshes(scene, paths: list[str]) -> list[str]:
    """Cached models for exact mesh packages, without running the exporter.

    Empty unless every package has a cached model whose receipt names this game
    revision and this exporter build. That successful extraction also wrote the
    mesh's materials, textures and data assets.
    """
    from ..Outfit_Accessories.pak_catalog import filesystem_path, is_mesh
    revision = _extraction_revision(scene)
    producer = _extraction_producer()
    if not revision or producer is None:
        return []
    out_dir = cache_dir()
    for path in paths:
        model = _expected_uemodel(out_dir, path) if is_mesh(path) else ""
        # Same legacy-root rule as mesh_cache_is_current.
        if not model or "/remappedplugins/content/" in model.replace("\\", "/").casefold():
            return []
        try:
            with open(filesystem_path(model + ".arc-extract.json"), "r", encoding="utf-8") as stream:
                record = json.load(stream)
        except (OSError, ValueError):
            return []
        if not isinstance(record, dict) or record.get("revision") != revision or record.get("producer") != producer:
            return []
    return _collect_uemodels(out_dir, paths)


def extract_paths(game_dir: str, usmap: str, asset_paths: list[str]) -> tuple[int, str, list[str]]:
    """Extract packages/folders, then finalize their material/mesh caches."""
    paths = [path for path in (asset_paths or []) if path]
    if not paths:
        return 1, "no asset path", []
    out_dir = cache_dir()
    command = extraction_command(game_dir, usmap, paths, out_dir=out_dir)
    proc = run_arcpak(command)
    log = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    meshes = complete_extraction(out_dir, paths, proc.returncode, log, _extraction_revision())
    return proc.returncode, log, meshes


def extract_asset(game_dir: str, usmap: str, asset_path: str) -> tuple[int, str, str]:
    """Run the helper for one path. Returns (exit code, log, first uemodel)."""
    code, log, meshes = extract_paths(game_dir, usmap, [asset_path])
    return code, log, meshes[0] if meshes else ""


def ensure_rels(scene, rels: list[str], *, reuse_current: bool = False) -> list[str]:
    """Extract Pioneer-relative paths when the paks folder is set. Empty if it is not.

    ``reuse_current`` returns current cached meshes without the exporter when
    every path is an exact mesh package (see current_extracted_meshes).
    """
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
    from ..Outfit_Accessories.pak_catalog import is_mesh
    mesh_paths = [path for path in paths if is_mesh(path)]
    extra = [path for path in paths if not is_mesh(path)]
    if reuse_current:
        # Extra non-mesh packages (colourway MIs) never void current meshes.
        meshes = current_extracted_meshes(scene, mesh_paths or paths)
        if meshes:
            utils.set_pak_cache_root(cache_dir())
            if mesh_paths and extra:
                extract_paths(game, usmap, extra)
            return meshes
    code, log, meshes = extract_paths(game, usmap, paths)
    if code or not meshes:
        print(f"Arc Pak: extract rc={code}: {_log_tail(log)}")
    elif globals().get('start_material_metadata_prefetch'):
        # Overlap the material-metadata pass with the rest of the import; extra
        # MI packages extracted here (colourways) join the same plan.
        out = cache_dir()
        materials = [os.path.join(out, *path.split("/")) + ".json" for path in extra
                     if path.rsplit("/", 1)[-1].casefold().startswith("mi_")]
        globals()['start_material_metadata_prefetch'](
            scene, meshes, materials=[path for path in materials if os.path.isfile(path)])
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
        from ..Outfit_Accessories import pak_catalog
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
    bl_label = "Select Steam Install / Paks Folder"
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={"HIDDEN"})
    directory: StringProperty(
        subtype="DIR_PATH",
        options={"HIDDEN"},
        default=DEFAULT_PAK_PICKER_DIR,
    )

    def invoke(self, context, event):
        # ImportHelper can restore Blender's last-used file-browser location,
        # which otherwise hides this property's default. Set both fields so
        # the first picker view consistently opens at Steam's common folder.
        start = DEFAULT_PAK_PICKER_DIR
        if os.path.isdir(start):
            self.directory = start + os.sep
            self.filepath = self.directory
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        folder = utils.resolved_picked_dir(
            bpy.path.abspath(getattr(self, "filepath", "") or ""),
            bpy.path.abspath(getattr(self, "directory", "") or ""),
        )
        paks_folder = resolve_pak_folder(folder)
        if not paks_folder:
            context.scene.arc_pak_dir = ""
            context.scene["arc_pak_dir_user_cleared"] = True
            self.report({"ERROR"}, "Select a Steam library, Arc Raiders, PioneerGame, Content, or Paks folder")
            return {"CANCELLED"}
        # Preserve the selected install/root folder for a clearer UI value;
        # configured_dir() resolves it to PioneerGame/Content/Paks as needed.
        context.scene.arc_pak_dir = folder
        context.scene["arc_pak_dir_user_cleared"] = False
        window = getattr(context, "window", None)
        cursor_set = getattr(window, "cursor_set", None)
        if callable(cursor_set):
            cursor_set("WAIT")
        try:
            index, _root = ensure_pak_browser(context.scene)
            from ..Outfit_Accessories import pak_catalog
            self.report({"INFO"}, f"Paks loaded: {len(pak_catalog.mesh_paths(index))} meshes available")
        except Exception as exc:
            self.report({"WARNING"}, f"Paks folder saved; catalog could not load: {exc}")
        finally:
            if callable(cursor_set):
                cursor_set("DEFAULT")
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

    def invoke(self, context, event):
        override = bpy.path.abspath(getattr(context.scene, "arc_usmap_path", "") or "")
        self.filepath = override or bundled_usmap()
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

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
        context.scene.arc_usmap_path = bundled_usmap()
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
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={"HIDDEN"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})
    files: CollectionProperty(type=OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    browser_root: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    select_folder: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    filemode: IntProperty(default=8, options={"HIDDEN", "SKIP_SAVE"})

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
        self.filemode = 9 if self.select_folder else 8
        from ..Outfit_Accessories import pak_catalog
        self.directory = pak_catalog.browser_start(root) + os.sep
        self.filepath = self.directory
        self.files.clear()
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def check(self, context):
        return False if self.select_folder else ImportHelper.check(self, context)

    def draw(self, context):
        self.layout.label(text="Select the folder to import." if self.select_folder else "Select a mesh to import.", icon="FILE_FOLDER" if self.select_folder else "MESH_DATA")

    def execute(self, context):
        scene = context.scene
        if self.asset_path:
            assets = [_normalize_asset_path(self.asset_path)]
        else:
            try:
                from ..Outfit_Accessories import pak_catalog
                index, root = ensure_pak_browser(scene)
                if self.browser_root and os.path.normcase(os.path.realpath(self.browser_root)) != os.path.normcase(os.path.realpath(root)):
                    raise ValueError("The Pak index changed. Reopen Import from Pak.")
                if self.select_folder:
                    selections = [utils.resolved_picked_dir(self.filepath, self.directory)]
                else:
                    selections = [os.path.join(self.directory, item.name) for item in self.files]
                    if not selections:
                        selections = [self.filepath]
                    if len(selections) != 1 or not pak_catalog._io_path(selections[0]).is_file():
                        raise ValueError("Choose one mesh, or use Import Folder")
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
            from ..Outfit_Accessories import asset_organization
            asset_organization.organize_objects(_objs, scene)
            imported += 1
            last = msg or os.path.basename(mesh)
        from ..Outfit_Accessories import character_fit
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


_PREVIEWS_TRIED = set()


def previews_marker(cache: str, build: str) -> str:
    return os.path.join(cache, f".previews_{build or 'unknown'}")


def run_previews_once(cache, build, run, log=print) -> bool:
    """Run ``run()`` once per (cache, build) unless the marker exists; marker only on exit 0."""
    marker = previews_marker(cache, build)
    if os.path.isfile(marker):
        return True
    key = (cache, build)
    if key in _PREVIEWS_TRIED:
        return False
    _PREVIEWS_TRIED.add(key)
    try:
        code = run()
    except Exception as exc:
        code = exc
    if code != 0:
        log(f"[ArcPak] previews mode failed ({code}); using live icon fallback")
        return False
    os.makedirs(cache, exist_ok=True)
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write("ok\n")
    return True


_PREVIEWS_THREAD = None


def _previews_job(scene):
    """Return (cache, build, run) for the previews pass, or None when no game dir."""
    from ..Outfit_Accessories import pak_catalog
    try:
        scene = scene or bpy.context.scene
        game = configured_dir(scene)
        usmap = resolve_usmap(scene)
    except Exception:
        return None
    if not game:
        return None
    cache = cache_dir()
    build = pak_catalog.installed_build(game) or ""
    bundled = os.path.join(_addon_dir(), "ArcPak", "mappings", "pak-index.bin")
    cmd = [exe_path(), "previews", "--game-dir", game, "--index-dir", index_dir(), "--out", cache]
    if os.path.isfile(bundled):
        cmd += ["--index", bundled]
    if usmap:
        cmd += ["--usmap", usmap]
    def run():
        import time
        t0 = time.perf_counter()
        rc = run_arcpak(cmd).returncode
        print(f"[ArcPak] previews rc={rc} secs={time.perf_counter() - t0:.2f}")
        return rc
    return cache, build, run


_PREVIEWS_PENDING = []


def start_pending_previews():
    """Start a deferred previews pass (called after metadata extraction returns)."""
    while _PREVIEWS_PENDING:
        scene, on_done = _PREVIEWS_PENDING.pop()
        kick_previews(scene, on_done)


def kick_previews(scene=None, on_done=None, *, defer=False) -> bool:
    """Start the previews pass in a daemon thread; never blocks. True if marker already exists.

    ``defer`` queues it until the next metadata call returns, so it never contends with it."""
    global _PREVIEWS_THREAD
    if defer:
        _PREVIEWS_PENDING[:] = [(scene, on_done)]
        return False
    job = _previews_job(scene)
    if job is None:
        return False
    cache, build, run = job
    if os.path.isfile(previews_marker(cache, build)):
        return True
    if _PREVIEWS_THREAD is not None and _PREVIEWS_THREAD.is_alive():
        return False
    if (cache, build) in _PREVIEWS_TRIED:
        return False
    def worker():
        ok = run_previews_once(cache, build, run)
        if ok and on_done is not None:
            try:
                bpy.app.timers.register(on_done, first_interval=0.0)
            except Exception:
                pass
    _PREVIEWS_THREAD = threading.Thread(target=worker, name="ArcPakPreviews", daemon=True)
    _PREVIEWS_THREAD.start()
    return False


def previews_running() -> bool:
    if _PREVIEWS_PENDING:
        start_pending_previews()
    return _PREVIEWS_THREAD is not None and _PREVIEWS_THREAD.is_alive()


def wait_previews(timeout=None) -> float:
    """Join the previews thread (end of import only). Returns seconds blocked."""
    import time
    t0 = time.perf_counter()
    if previews_running():
        _PREVIEWS_THREAD.join(timeout)
    return time.perf_counter() - t0


def ensure_previews(scene=None) -> bool:
    """Non-blocking: kick the previews pass; sidebar redraws when it completes."""
    return kick_previews(scene, on_done=_redraw_ui)


def _redraw_ui():
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    except Exception:
        pass
    return None
