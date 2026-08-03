"""
FModel → DataRaiders bridge (TCP listener).

FModel owns mesh export and optional umap placement CSV export.
This module receives newline JSON on localhost and queues work on
Blender's main thread (Surf-style socket + timer pump).

Primary command: `import_models` (PSK/PSKX + auto materials from Snooper).
Outfit command: `import_outfit` (manifest with colorways / explicit skin JSON paths).
Secondary: `placements_ready` (umap CSV → Stage 1 geometry; Stage 2 is manual).

Port 28563 (SurfBlender uses 28562 — do not clash).
Protocol: newline-delimited JSON. See FMODEL_BRIDGE.md.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import threading
import traceback
from collections import deque
from typing import Any

# Dedicated Arc / DataRaiders port (SurfBlender = 28562)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 28563
PROTOCOL_VERSION = 1

_listener_lock = threading.Lock()
_listener_thread: threading.Thread | None = None
_listener_sock: socket.socket | None = None
_listening = False
_listen_port = DEFAULT_PORT
_last_status = "idle"
_last_error = ""
_last_import = ""
_last_map = ""
_pending: deque = deque()
_pump_registered = False


def is_listening() -> bool:
    return _listening


def listen_port() -> int:
    return _listen_port


def last_status() -> str:
    return _last_status


def last_error() -> str:
    return _last_error


def last_import_path() -> str:
    return _last_import


def last_map_name() -> str:
    return _last_map


def start_listener(port: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> str:
    """Start background TCP listener. Returns status message."""
    global _listener_thread, _listening, _listen_port, _last_status, _last_error, _listener_sock
    with _listener_lock:
        if _listening:
            return f"Already listening on {host}:{_listen_port}"
        _listen_port = int(port)
        _last_error = ""
        _last_status = "starting"
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, _listen_port))
            sock.listen(8)
            sock.settimeout(1.0)
        except OSError as e:
            _last_status = "error"
            _last_error = str(e)
            return f"Bind failed on {host}:{_listen_port}: {e}"

        _listener_sock = sock
        _listening = True
        _last_status = "listening"
        _listener_thread = threading.Thread(
            target=_accept_loop,
            name="ArcFModelBridge",
            daemon=True,
        )
        _listener_thread.start()
        _ensure_pump()
        return f"Listening on {host}:{_listen_port}"


def stop_listener() -> str:
    global _listening, _listener_sock, _last_status
    with _listener_lock:
        _listening = False
        sock = _listener_sock
        _listener_sock = None
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass
    _last_status = "stopped"
    return "Listener stopped"


def _accept_loop():
    global _last_status, _last_error
    while _listening:
        sock = _listener_sock
        if sock is None:
            break
        try:
            conn, _addr = sock.accept()
        except socket.timeout:
            continue
        except OSError:
            if _listening:
                continue
            break
        try:
            _handle_connection(conn)
        except Exception as e:
            _last_error = str(e)
            _last_status = "error"
            traceback.print_exc()
        finally:
            try:
                conn.close()
            except OSError:
                pass


def _handle_connection(conn: socket.socket):
    conn.settimeout(5.0)
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 1_000_000:
            raise ValueError("Request too large")

    line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()
    if not line:
        _reply(conn, _snapshot(False, "", "Empty request"))
        return

    try:
        request = json.loads(line)
    except json.JSONDecodeError as e:
        _reply(conn, _snapshot(False, "", f"Invalid JSON: {e}"))
        return

    response = handle_request(request)
    _reply(conn, response)


def _reply(conn: socket.socket, payload: dict):
    data = (json.dumps(payload) + "\n").encode("utf-8")
    try:
        conn.sendall(data)
    except OSError:
        pass


def handle_request(request: dict) -> dict:
    global _last_status
    command = request.get("Command") or request.get("command")
    message_id = request.get("MessageId") or request.get("message_id") or ""

    if command == "status_query":
        return _snapshot(True, message_id, "Status reported")

    if command == "import_models":
        data = request.get("Data") or request.get("data") or {}
        paths = data.get("Paths") or data.get("paths") or []
        if not isinstance(paths, list) or not paths:
            return _snapshot(False, message_id, "Missing Data.Paths")
        with _listener_lock:
            _pending.append(
                {"command": "import_models", "data": data, "message_id": message_id}
            )
            _last_status = "queued"
        _ensure_pump()
        return _snapshot(True, message_id, f"Queued {len(paths)} model(s)")

    if command == "import_outfit":
        data = request.get("Data") or request.get("data") or {}
        colorways = data.get("Colorways") or data.get("colorways") or []
        if not isinstance(colorways, list) or not colorways:
            return _snapshot(False, message_id, "Missing Data.Colorways")
        with _listener_lock:
            _pending.append(
                {"command": "import_outfit", "data": data, "message_id": message_id}
            )
            _last_status = "queued"
        _ensure_pump()
        return _snapshot(True, message_id, f"Queued outfit ({len(colorways)} colorway(s))")

    if command == "placements_ready":
        data = request.get("Data") or request.get("data") or {}
        if not data:
            return _snapshot(False, message_id, "Missing Data payload")
        with _listener_lock:
            _pending.append(
                {"command": "placements_ready", "data": data, "message_id": message_id}
            )
            _last_status = "queued"
        _ensure_pump()
        return _snapshot(True, message_id, "Queued for import")

    return _snapshot(False, message_id, f"Unsupported command '{command}'")


def _snapshot(accepted: bool, message_id: str, message: str) -> dict:
    with _listener_lock:
        depth = len(_pending)
    return {
        "Accepted": accepted,
        "MessageId": message_id,
        "Status": _last_status,
        "Listening": _listening,
        "Port": _listen_port,
        "QueueDepth": depth,
        "LastImport": _last_import,
        "LastMap": _last_map,
        "Error": _last_error,
        "Message": message,
        "ProtocolVersion": PROTOCOL_VERSION,
    }


def _ensure_pump():
    global _pump_registered
    try:
        import bpy
    except ImportError:
        return
    if _pump_registered:
        return
    _pump_registered = True
    bpy.app.timers.register(_pump_queue, first_interval=0.0)


def _pump_queue():
    """Main-thread timer: apply received models / placements into the scene."""
    global _pump_registered, _last_status, _last_error, _last_import, _last_map
    try:
        import bpy
    except ImportError:
        _pump_registered = False
        return None

    with _listener_lock:
        if not _pending:
            _pump_registered = False
            return None
        item = _pending.popleft()

    try:
        command = item.get("command") or ""
        data = item.get("data") or {}
        if command == "import_models":
            imported = import_model_paths(data)
            _last_import = imported[0] if imported else ""
            _last_map = data.get("Source") or data.get("source") or "snooper"
            _last_status = "imported"
            _last_error = ""
        elif command == "import_outfit":
            imported = import_outfit_manifest(data)
            _last_import = imported[0] if imported else ""
            _last_map = data.get("DisplayName") or data.get("displayName") or data.get("OutfitName") or "outfit"
            _last_status = "imported"
            _last_error = ""
        else:
            csv_path, map_name = ingest_fmodel_payload(data, bpy.context.scene)
            _last_import = csv_path
            _last_map = map_name
            _last_status = "imported"
            _last_error = ""
            mode = (data.get("ImportMode") or data.get("import_mode") or "instanced").lower()
            # CSV-only FModel export has no meshes on disk — ingest paths and stop.
            if mode in {"csv", "manifest", "none", "placements"}:
                _last_status = "csv_ready"
                _last_error = (
                    "Placements CSV received — no meshes exported. "
                    "In FModel use Export Map Placements + Meshes, then Stage 1."
                )
                print(f"Arc Raiders FModel bridge: {_last_error}")
            elif mode in {"instanced", "fast", "instances"}:
                bpy.ops.arc.import_placement_instanced("INVOKE_DEFAULT")
            elif mode == "meshes":
                bpy.ops.arc.import_placement_meshes("INVOKE_DEFAULT")
            elif mode in {"empties", "empty"}:
                bpy.ops.arc.import_placement_empties("INVOKE_DEFAULT")
            else:
                bpy.ops.arc.import_placement_instanced("INVOKE_DEFAULT")
        _tag_redraw()
    except Exception as e:
        _last_status = "error"
        _last_error = str(e)
        traceback.print_exc()

    with _listener_lock:
        if _pending:
            return 0.05
    _pump_registered = False
    return None


def import_model_paths(data: dict[str, Any]) -> list[str]:
    """Import absolute PSK/PSKX paths from FModel and apply Arc materials automatically."""
    global _last_error
    try:
        from .. import operators
    except ImportError:
        import operators  # type: ignore

    paths = data.get("Paths") or data.get("paths") or []
    imported: list[str] = []
    errors: list[str] = []
    for raw in paths:
        path = os.path.abspath(str(raw).strip())
        if not path:
            continue
        if not os.path.isfile(path):
            errors.append(f"missing: {path}")
            continue
        ext = os.path.splitext(path)[1].lower()
        if ext not in {".psk", ".pskx"}:
            errors.append(f"unsupported: {path}")
            continue
        # Single-model bridge path: geometry + materials (no outfit dialog)
        ok, msg, _objs = operators.import_psk_with_materials(path)
        if ok:
            imported.append(path)
        else:
            errors.append(msg or path)

    if not imported and errors:
        raise RuntimeError("; ".join(errors[:3]))
    if errors:
        # Partial success still counts as imported; keep first error for status.
        _last_error = "; ".join(errors[:3])
    return imported


def import_outfit_manifest(data: dict[str, Any]) -> list[str]:
    """Import a complete outfit from an FModel Outfit Composer manifest.

    FModel owns discovery/colorway selection. Blender only:
    - imports explicit PSK paths
    - applies explicit SkinJsonPath materials (no DA_OI rescan / dialog)
    - one collection per colorway, laid out along +X
    - reuses mesh/material datablocks across colorways when safe
    """
    global _last_error
    try:
        import bpy
        import mathutils
    except ImportError as e:
        raise RuntimeError(f"Blender API unavailable: {e}") from e

    try:
        from .. import operators
        from .. import importing as psk_importing
        from .. import rig
    except ImportError:
        import operators  # type: ignore
        import importing as psk_importing  # type: ignore
        import rig  # type: ignore

    colorways = data.get("Colorways") or data.get("colorways") or []
    display = (
        data.get("DisplayName")
        or data.get("displayName")
        or data.get("OutfitName")
        or data.get("outfitName")
        or "Outfit"
    )
    if not isinstance(colorways, list) or not colorways:
        raise RuntimeError("Outfit manifest has no colorways")

    # Cache: (psk, skin_json, glass_json) → first mesh Material list + mesh data for optional reuse
    mat_cache: dict[tuple[str, str, str], list] = {}
    imported: list[str] = []
    errors: list[str] = []
    x_cursor = 0.0
    margin_frac = 0.25
    scene = bpy.context.scene

    for cw in colorways:
        if not isinstance(cw, dict):
            continue
        cw_name = cw.get("Name") or cw.get("name") or "Default"
        coll_name = cw.get("CollectionName") or cw.get("collectionName") or f"{display}_{cw_name}"
        parts = cw.get("Parts") or cw.get("parts") or []
        if not isinstance(parts, list) or not parts:
            continue

        coll = bpy.data.collections.get(coll_name)
        if coll is None:
            coll = bpy.data.collections.new(coll_name)
            scene.collection.children.link(coll)

        inst_objs: list = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            psk_path = os.path.abspath(str(part.get("PskPath") or part.get("pskPath") or "").strip())
            skin_json = str(part.get("SkinJsonPath") or part.get("skinJsonPath") or "").strip()
            if skin_json:
                skin_json = os.path.abspath(skin_json)
            glass_json = str(part.get("GlassSkinJsonPath") or part.get("glassSkinJsonPath") or "").strip()
            if glass_json:
                glass_json = os.path.abspath(glass_json)
            if not psk_path or not os.path.isfile(psk_path):
                errors.append(f"missing psk: {psk_path or '?'}")
                continue
            ext = os.path.splitext(psk_path)[1].lower()
            if ext not in {".psk", ".pskx"}:
                errors.append(f"unsupported: {psk_path}")
                continue

            cache_key = (psk_path.lower(), (skin_json or "").lower(), (glass_json or "").lower())
            try:
                new_objects = psk_importing.import_psk(psk_path)
            except RuntimeError as e:
                errors.append(str(e))
                continue

            mesh_objects = [o for o in new_objects if o.type == "MESH"]
            for obj in mesh_objects:
                try:
                    obj["arc_psk_path"] = psk_path
                    obj["arc_model_type"] = part.get("ModelType") or part.get("modelType") or "clothing"
                    obj["arc_part_key"] = part.get("PartKey") or part.get("partKey") or ""
                    obj["arc_skin_json"] = (
                        skin_json if skin_json and os.path.isfile(skin_json) else ""
                    )
                    obj["arc_manual_skins_folder"] = (
                        os.path.dirname(skin_json)
                        if skin_json and os.path.isfile(skin_json) else ""
                    )
                    obj["arc_skin_choice"] = (
                        skin_json if skin_json and os.path.isfile(skin_json) else "NONE"
                    )
                    # Visor glass lives in its own MI; the shell skin JSON does not describe it.
                    obj["arc_glass_skin_json"] = glass_json if os.path.isfile(glass_json) else ""
                    obj["arc_outfit"] = display
                    obj["arc_colorway"] = cw_name
                    obj["arc_materials_pending"] = 0
                    obj["arc_bridge_source"] = "fmodel_outfit"
                    routing = str(
                        part.get("PaletteRouting") or part.get("paletteRouting") or "auto"
                    ).strip().lower()
                    obj["arc_palette_routing"] = routing or "auto"
                    mat_key = str(part.get("MaterialKey") or part.get("materialKey") or "")
                    if mat_key:
                        obj["arc_material_key"] = mat_key
                except Exception:
                    pass

                if cache_key in mat_cache and mat_cache[cache_key]:
                    operators.assign_cached_materials(obj, mat_cache[cache_key])
                else:
                    operators.apply_materials_to_object(
                        obj,
                        psk_path,
                        skin_json=skin_json if skin_json and os.path.isfile(skin_json) else "",
                        # FModel writes each colourway's Texture2DArray slices beside its explicit
                        # skin JSON. Point material discovery at that exact directory; otherwise it
                        # picks one arbitrary "default" skin folder for every colourway.
                        manual_skins_folder=os.path.dirname(skin_json)
                        if skin_json and os.path.isfile(skin_json) else "",
                    )
                    mat_cache[cache_key] = operators.snapshot_object_materials(obj)

                for c in list(obj.users_collection):
                    c.objects.unlink(obj)
                coll.objects.link(obj)

            inst_objs.extend(new_objects)
            imported.append(psk_path)

        if not inst_objs:
            continue

        # Outfit parts share the same Arc character skeleton. Merge the per-PSK armatures,
        # collapse duplicate shared bones, and redirect only this colorway's mesh modifiers.
        rig.fix_rig_all(inst_objs, merge=True)

        def _still_alive(obj):
            try:
                obj.name
                return True
            except ReferenceError:
                return False

        inst_objs = [obj for obj in inst_objs if _still_alive(obj)]
        for obj in inst_objs:
            for source_coll in list(obj.users_collection):
                source_coll.objects.unlink(obj)
            coll.objects.link(obj)

        bpy.context.view_layer.update()
        minx = maxx = None
        for o in inst_objs:
            if o.type != "MESH":
                continue
            for corner in o.bound_box:
                wx = (o.matrix_world @ mathutils.Vector(corner)).x
                minx = wx if minx is None else min(minx, wx)
                maxx = wx if maxx is None else max(maxx, wx)

        if minx is not None:
            width = max(maxx - minx, 1e-4)
            shift = x_cursor - minx
            for o in inst_objs:
                if o.parent is None:
                    o.location.x += shift
            x_cursor += width * (1.0 + margin_frac)

    if not imported and errors:
        raise RuntimeError("; ".join(errors[:3]))
    if errors:
        _last_error = "; ".join(errors[:3])
    return imported


def ingest_fmodel_payload(data: dict[str, Any], scene) -> tuple[str, str]:
    """
    Copy FModel CSV into the placement workspace and wire scene properties.
    Returns (csv_path, map_name).
    """
    try:
        from .. import map_placement as mp
    except ImportError:
        import map_placement as mp  # type: ignore

    csv_src = (data.get("CsvPath") or data.get("csv_path") or "").strip()
    map_name = (data.get("MapName") or data.get("map_name") or "").strip()
    if not csv_src or not os.path.isfile(csv_src):
        raise FileNotFoundError(f"CSV not found: {csv_src}")

    if not map_name:
        map_name = os.path.basename(os.path.dirname(csv_src)) or "Map"

    dest_dir = mp.map_output_dir(map_name, scene)
    dest_csv = os.path.join(dest_dir, "placements.csv")
    if os.path.abspath(csv_src) != os.path.abspath(dest_csv):
        shutil.copy2(csv_src, dest_csv)

    manifest_src = (data.get("ManifestPath") or data.get("manifest_path") or "").strip()
    if not manifest_src:
        sibling = os.path.join(os.path.dirname(csv_src), "placements_manifest.json")
        if os.path.isfile(sibling):
            manifest_src = sibling
    if manifest_src and os.path.isfile(manifest_src):
        shutil.copy2(manifest_src, os.path.join(dest_dir, "placements_manifest.json"))

    # Prefer FModel's MapPlacements mesh folder from the manifest (absolute paths).
    try:
        manifest_dest = os.path.join(dest_dir, "placements_manifest.json")
        if os.path.isfile(manifest_dest):
            with open(manifest_dest, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            export_root = (manifest.get("mesh_export_root") or "").strip()
            mesh_count = int(manifest.get("mesh_export_count") or 0)
            pioneer = (getattr(scene, "arc_pioneer_root", "") or "").strip()
            if export_root and os.path.isdir(export_root):
                scene.arc_placement_mesh_root = export_root
                # Only fill Pioneer root when unset. Prefer the sibling full FModel
                # dump (MI/SM JSON) over MapPlacements mesh-only trees — Stage 2
                # needs those JSONs; Map + Meshes folders usually have .uemodel only.
                if not pioneer and mesh_count > 0:
                    materials_root = export_root
                    try:
                        from .. import utils as _utils

                        export_content = _utils.find_content_dir(export_root)
                        for guessed in _utils.guess_full_fmodel_content_dirs(
                            export_content or ""
                        ):
                            if not _utils._content_dir_has_mi_jsons(guessed):
                                continue
                            # Point at FModel output root (parent of PioneerGame/Content)
                            # so outfits + ObjectPath resolve keep working.
                            pg = os.path.dirname(guessed)  # .../PioneerGame
                            fmodel_out = os.path.dirname(pg) if pg else ""
                            if fmodel_out and os.path.isdir(fmodel_out):
                                materials_root = fmodel_out
                                break
                    except Exception:
                        materials_root = export_root
                    scene.arc_pioneer_root = materials_root
                print(
                    f"Arc Raiders FModel bridge: map meshes root={export_root} "
                    f"(manifest lists {mesh_count} export(s)); "
                    f"Pioneer root={getattr(scene, 'arc_pioneer_root', '') or export_root}"
                )
            elif mesh_count > 0:
                print(
                    f"Arc Raiders FModel bridge: manifest lists {mesh_count} mesh(es) "
                    f"but mesh_export_root is missing: {export_root!r}"
                )
    except Exception:
        pass

    scene.arc_placement_csv = dest_csv
    scene.arc_placement_map_name = map_name
    try:
        scene.arc_placement_map = map_name
    except Exception:
        pass

    return dest_csv, map_name


def _tag_redraw():
    try:
        import bpy

        for area in bpy.context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    except Exception:
        pass


def find_latest_fmodel_export(search_roots: list[str] | None = None) -> tuple[str, str]:
    """
    Find the newest placements.csv under MapPlacements folders.
    Returns (csv_path, map_name) or ("", "").
    """
    roots = list(search_roots or [])
    home = os.path.expanduser("~")
    # FModel's conventional default ModelDirectory parent
    roots.append(os.path.join(home, "Documents", "FModel", "Exports"))
    try:
        try:
            from .. import map_placement as mp
        except ImportError:
            import map_placement as mp  # type: ignore

        roots.append(mp.default_placement_workspace())
    except Exception:
        pass

    best = ("", "", 0.0)
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            if "placements.csv" not in filenames:
                continue
            parent = os.path.basename(dirpath)
            grand = os.path.basename(os.path.dirname(dirpath))
            if grand.lower() != "mapplacements":
                # Still accept any placements.csv; prefer MapPlacements layout
                pass
            path = os.path.join(dirpath, "placements.csv")
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            map_name = parent
            score = mtime
            if grand.lower() == "mapplacements":
                score += 1e12  # prefer canonical layout when ages tie-ish
            if score > best[2]:
                best = (path, map_name, score)

    return best[0], best[1]
