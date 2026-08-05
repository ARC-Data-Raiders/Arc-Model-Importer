"""
Map Placement — receive FModel placements (primary) or extract from JSON (backup),
then import into Blender (empties / optional PSK).

Architecture: FModel-Vibe owns umap → transforms → CSV/TCP.
This addon is primarily a receiver; manual Extract is backup only.
See map_tools/FMODEL_BRIDGE.md.

Units: placements CSV stays Unreal centimeters. Blender map space uses
``MAP_UNIT_SCALE`` (default 0.01) so 1 BU = 1 m. Mesh imports use the same
factor (UEFormat scale_factor / source object scale).

Axes: Unreal (X, Y, Z) with optional ``MAP_MIRROR_Y`` (default True) so
top-down Blender matches in-game map UI (Buried City: dual tracks bottom-left,
bridge spine left→right). Positions become (x, -y, z) * unit; rotations and
scale absorb the reflection (see ``csv_row_to_blender_pose``).

Rotations: Unreal Pitch/Yaw/Roll degrees → Blender XYZ Euler, then Y-mirror
adjustment when enabled. Pass-through without mirror is (-roll, -pitch, yaw).

Imports are batched via modal TIMER so thousands of empties/meshes never run
in a single operator tick. ESC cancels; progress is reported in the status bar.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from typing import Any

import bpy
import bpy_extras
import mathutils

from . import importing
from . import utils

CSV_COLUMNS = (
    "actor_name",
    "asset_path",
    "asset_kind",
    "x",
    "y",
    "z",
    "pitch",
    "yaw",
    "roll",
    "scale_x",
    "scale_y",
    "scale_z",
    "source_file",
    "notes",
)

# CSV = Unreal cm. Blender applies this on import (and via Scale Map to Meters).
MAP_UNIT_SCALE = 0.01
# Match in-game top-down maps (Unreal editor +Y-up view is flipped vs UI maps).
MAP_MIRROR_Y = True
ORIENTATION_PASSTHROUGH = "passthrough"
ORIENTATION_MIRROR_Y = "mirror_y"

_ADDON_DIR = os.path.dirname(__file__)
_DEFAULT_WORKSPACE = os.path.join(_ADDON_DIR, "MapPlacement")


def default_placement_workspace() -> str:
    return _DEFAULT_WORKSPACE


def get_placement_workspace(scene=None) -> str:
    scene = scene or bpy.context.scene
    custom = getattr(scene, "arc_placement_workspace", "") or ""
    if custom:
        path = bpy.path.abspath(custom)
        os.makedirs(path, exist_ok=True)
        return path
    os.makedirs(_DEFAULT_WORKSPACE, exist_ok=True)
    return _DEFAULT_WORKSPACE


def map_output_dir(map_name: str, scene=None) -> str:
    """Neat per-map folder: {workspace}/{MapName}/"""
    dest = os.path.join(get_placement_workspace(scene), map_name)
    os.makedirs(dest, exist_ok=True)
    return dest


def find_maps_root(pioneer_root: str = "") -> str:
    """Locate Content/Pioneer/Maps under the PioneerGame root."""
    root = pioneer_root or utils.get_pioneer_root()
    if not root:
        return ""
    root = os.path.abspath(bpy.path.abspath(root))
    candidates = [
        os.path.join(root, "Content", "Pioneer", "Maps"),
        os.path.join(root, "Pioneer", "Maps"),
        os.path.join(root, "Maps"),
    ]
    # If root is already Content/
    if os.path.basename(root).lower() == "content":
        candidates.insert(0, os.path.join(root, "Pioneer", "Maps"))
    for c in candidates:
        if os.path.isdir(c):
            return c
    # Fuzzy via utils
    hit = utils.find_relative_dir(root, ["Maps"])
    if hit and os.path.isdir(hit):
        return hit
    return ""


def _looks_like_map_folder(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    name = os.path.basename(path)
    if name.startswith("_") or name.lower() in ("lighting", "shared", "common"):
        return False
    try:
        entries = os.listdir(path)
    except OSError:
        return False
    lower = {e.lower() for e in entries}
    # Persistent level json or subfolder
    if any(e.endswith("_p.json") for e in lower):
        return True
    if any(e.endswith("_p") and os.path.isdir(os.path.join(path, e)) for e in entries):
        return True
    if "_generated_" in lower or any("_generated_" in e.lower() for e in entries):
        return True
    # World Partition map packages often just have a large folder tree
    if f"{name.lower()}_p.json" in lower or f"{name.lower()}_p" in lower:
        return True
    return False


def list_detected_maps(pioneer_root: str = "") -> list[str]:
    """Scan Pioneer/Maps for World Partition map folders."""
    maps_root = find_maps_root(pioneer_root)
    if not maps_root:
        return []
    found = []
    try:
        for entry in sorted(os.listdir(maps_root), key=str.lower):
            full = os.path.join(maps_root, entry)
            if _looks_like_map_folder(full):
                found.append(entry)
    except OSError:
        pass
    return found


def make_map_enum_items(self, context):
    """Dynamic EnumProperty items for detected maps."""
    items = [("NONE", "— Select Map —", "Pick a map under Pioneer/Maps")]
    root = ""
    try:
        root = utils.get_pioneer_root()
    except Exception:
        pass
    for name in list_detected_maps(root):
        items.append((name, name, f"Map folder: {name}"))
    # Also list maps already extracted into the workspace
    try:
        ws = get_placement_workspace(context.scene if context else None)
        for entry in sorted(os.listdir(ws), key=str.lower):
            full = os.path.join(ws, entry)
            if os.path.isdir(full) and not any(i[0] == entry for i in items):
                items.append((entry, f"{entry} (workspace)", "Already extracted in workspace"))
    except OSError:
        pass
    return items


def apply_map_selection_paths(scene, map_name: str) -> None:
    """Point CSV / bounds / overlay paths at the neat workspace folder for this map."""
    if not map_name or map_name == "NONE":
        return
    out = map_output_dir(map_name, scene)
    buildings = os.path.join(out, "placements_buildings.csv")
    all_csv = os.path.join(out, "placements.csv")
    if os.path.isfile(buildings):
        scene.arc_placement_csv = buildings
    elif os.path.isfile(all_csv):
        scene.arc_placement_csv = all_csv
    bounds = os.path.join(out, f"{map_name}_world_bounds.json")
    if not os.path.isfile(bounds):
        # Common alternate next to addon MapPlacement or Python tools
        for alt in (
            os.path.join(get_placement_workspace(scene), f"{map_name}_world_bounds.json"),
            os.path.join(_ADDON_DIR, f"{map_name}_world_bounds.json"),
        ):
            if os.path.isfile(alt):
                bounds = alt
                break
    if os.path.isfile(bounds):
        scene.arc_placement_world_bounds_json = bounds
    overlay = os.path.join(out, f"{map_name}_placements_overlay.png")
    if os.path.isfile(overlay):
        scene.arc_placement_heightmap_image = overlay
    scene.arc_placement_map_name = map_name


def _on_placement_map_changed(self, context):
    apply_map_selection_paths(context.scene, self.arc_placement_map)


def map_dir_for_name(map_name: str, pioneer_root: str = "") -> str:
    maps_root = find_maps_root(pioneer_root)
    if not maps_root or not map_name or map_name == "NONE":
        return ""
    path = os.path.join(maps_root, map_name)
    return path if os.path.isdir(path) else ""


def _copy_if_newer(src: str, dest: str) -> bool:
    """Copy src → dest when missing or older. Returns True if dest exists after."""
    if not src or not os.path.isfile(src):
        return os.path.isfile(dest)
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if (not os.path.isfile(dest)) or (os.path.getmtime(src) > os.path.getmtime(dest) + 0.5):
            shutil.copy2(src, dest)
        return True
    except OSError:
        return os.path.isfile(dest)


def collect_map_asset_candidates(map_name: str, map_dir: str = "") -> dict[str, list[str]]:
    """Search map/_PropHarvest, workspace, and nearby tool folders for bounds/PNGs."""
    names_bounds = [f"{map_name}_world_bounds.json"]
    names_png = [
        f"{map_name}_placements_overlay.png",
        f"{map_name}_heightmap.png",
        f"{map_name}_terrain.png",
        f"{map_name}_world_bounds.png",
        f"{map_name}_heightmap_bounds.png",
        f"{map_name}_heightmap_bounds_preview2048.png",
    ]
    search_dirs: list[str] = []
    if map_dir:
        search_dirs.extend(
            [
                os.path.join(map_dir, "_PropHarvest"),
                os.path.join(map_dir, "_Placement"),
                map_dir,
            ]
        )
    # Sibling Python tools next to common datamine layouts
    pioneer = utils.get_pioneer_root() or ""
    for base in filter(None, [pioneer, os.path.dirname(pioneer) if pioneer else ""]):
        for rel in (
            ("Python",),
            ("..", "Python"),
            ("..", "..", "Python"),
            ("Arc Raiders", "Python"),
        ):
            cand = os.path.abspath(os.path.join(base, *rel))
            if os.path.isdir(cand) and cand not in search_dirs:
                search_dirs.append(cand)
    # Bundled map_tools next to this add-on (optional; ignored if missing)
    known = os.path.join(_ADDON_DIR, "map_tools")
    if os.path.isdir(known) and known not in search_dirs:
        search_dirs.append(known)

    found: dict[str, list[str]] = {"bounds": [], "png": []}
    for d in search_dirs:
        for n in names_bounds:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                found["bounds"].append(p)
        for n in names_png:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                found["png"].append(p)
    return found


def gather_map_outputs_into_workspace(map_name: str, scene=None) -> list[str]:
    """
    Copy world_bounds / heightmap / existing overlay into {workspace}/{MapName}/.
    Returns list of destination paths that exist afterward.
    """
    scene = scene or bpy.context.scene
    out_dir = map_output_dir(map_name, scene)
    map_dir = map_dir_for_name(map_name, utils.get_pioneer_root())
    found = collect_map_asset_candidates(map_name, map_dir)
    written: list[str] = []

    for src in found["bounds"]:
        dest = os.path.join(out_dir, f"{map_name}_world_bounds.json")
        if _copy_if_newer(src, dest):
            written.append(dest)
            break

    # Prefer a real heightmap base over an already-composited overlay when copying refs
    preferred_png_order = (
        f"{map_name}_heightmap_bounds.png",
        f"{map_name}_heightmap.png",
        f"{map_name}_terrain.png",
        f"{map_name}_world_bounds.png",
        f"{map_name}_heightmap_bounds_preview2048.png",
        f"{map_name}_placements_overlay.png",
    )
    by_name = {os.path.basename(p): p for p in found["png"]}
    for name in preferred_png_order:
        src = by_name.get(name)
        if not src:
            continue
        dest = os.path.join(out_dir, name)
        if _copy_if_newer(src, dest):
            written.append(dest)

    return written


def run_overlay_for_map(map_name: str, scene=None) -> tuple[bool, str]:
    """Generate `{map}_placements_overlay.png` into the placement workspace."""
    scene = scene or bpy.context.scene
    out_dir = map_output_dir(map_name, scene)
    map_dir = map_dir_for_name(map_name, utils.get_pioneer_root())
    gather_map_outputs_into_workspace(map_name, scene)

    csv_path = os.path.join(out_dir, "placements_buildings.csv")
    if not os.path.isfile(csv_path):
        csv_path = os.path.join(out_dir, "placements.csv")
    if not os.path.isfile(csv_path):
        return False, f"No placements CSV in {out_dir} — run Extract first"

    bounds = os.path.join(out_dir, f"{map_name}_world_bounds.json")
    if not os.path.isfile(bounds):
        return (
            False,
            f"Missing {map_name}_world_bounds.json in workspace — place it under "
            f"{out_dir} (or under the map's _PropHarvest) then retry",
        )

    out_png = os.path.join(out_dir, f"{map_name}_placements_overlay.png")
    argv = [
        "--out-dir", out_dir,
        "--csv", csv_path,
        "--bounds", bounds,
        "--out", out_png,
    ]
    if map_dir:
        argv.extend(["--map-dir", map_dir])

    # Prefer in-process (needs Pillow + numpy)
    try:
        from .map_tools import map_placement_overlay as overlay_mod

        code = overlay_mod.main(argv)
        if code == 0 and os.path.isfile(out_png):
            apply_map_selection_paths(scene, map_name)
            return True, f"Overlay → {out_png}"
        if code != 0:
            raise RuntimeError(f"overlay exited {code}")
    except Exception as e:
        # Fallback: system / Blender python subprocess
        script = os.path.join(_ADDON_DIR, "map_tools", "map_placement_overlay.py")
        if not os.path.isfile(script):
            return False, f"Overlay failed: {e}"
        last_err = str(e)
        for exe in (sys.executable, "python", "py"):
            try:
                proc = subprocess.run(
                    [exe, script, *argv],
                    capture_output=True,
                    text=True,
                    timeout=600,
                    cwd=os.path.join(_ADDON_DIR, "map_tools"),
                )
                if proc.returncode == 0 and os.path.isfile(out_png):
                    apply_map_selection_paths(scene, map_name)
                    return True, f"Overlay → {out_png}"
                last_err = (proc.stderr or proc.stdout or last_err)[-600:]
            except Exception as e2:
                last_err = str(e2)
        # Last resort: copy a pre-made overlay if one was gathered
        existing = os.path.join(out_dir, f"{map_name}_placements_overlay.png")
        if os.path.isfile(existing):
            apply_map_selection_paths(scene, map_name)
            return True, f"Using existing overlay → {existing} (regenerate needs Pillow)"
        return False, f"Overlay failed (need Pillow/numpy): {last_err}"

    apply_map_selection_paths(scene, map_name)
    return True, f"Overlay → {out_png}"


def run_extract_for_map(map_name: str, scene=None) -> tuple[bool, str]:
    """Run bundled extract into {workspace}/{map}/. Returns (ok, message)."""
    scene = scene or bpy.context.scene
    pioneer = utils.get_pioneer_root()
    map_dir = map_dir_for_name(map_name, pioneer)
    if not map_dir:
        return False, f"Map folder not found for '{map_name}' — set PioneerGame root"
    out_dir = map_output_dir(map_name, scene)
    # Prefer in-process import of bundled tool
    try:
        from .map_tools import map_placement_extract as extract_mod
        argv = [
            "--map-dir", map_dir,
            "--out-dir", out_dir,
            "--prefer-named-component", "Building",
        ]
        code = extract_mod.main(argv)
        if code != 0:
            return False, f"Extract exited with code {code} — see System Console"
    except Exception as e:
        # Fallback: subprocess with Blender's python
        script = os.path.join(_ADDON_DIR, "map_tools", "map_placement_extract.py")
        if not os.path.isfile(script):
            return False, f"Extract failed: {e}"
        try:
            proc = subprocess.run(
                [sys.executable, script, "--map-dir", map_dir, "--out-dir", out_dir,
                 "--prefer-named-component", "Building"],
                capture_output=True,
                text=True,
                timeout=3600,
                cwd=os.path.join(_ADDON_DIR, "map_tools"),
            )
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "")[-800:]
                return False, f"Extract failed: {err}"
        except Exception as e2:
            return False, f"Extract failed: {e}; fallback: {e2}"

    gathered = gather_map_outputs_into_workspace(map_name, scene)
    overlay_ok, overlay_msg = run_overlay_for_map(map_name, scene)
    apply_map_selection_paths(scene, map_name)
    buildings = os.path.join(out_dir, "placements_buildings.csv")
    n = 0
    if os.path.isfile(buildings):
        with open(buildings, "r", encoding="utf-8-sig") as fh:
            n = max(0, sum(1 for _ in fh) - 1)
    parts = [f"Extracted {map_name} → {out_dir} ({n} building rows)"]
    if gathered:
        parts.append(f"copied {len(gathered)} support file(s)")
    if overlay_ok:
        parts.append("overlay PNG ready")
    else:
        parts.append(f"overlay skipped: {overlay_msg}")
    return True, " | ".join(parts)


def load_placements_csv(path: str) -> list[dict[str, str]]:
    if not path or not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


SPLINE_KEY_MARKER = "#spline:"


def split_spline_asset_key(asset_path: str) -> tuple[str, str]:
    """Return (base_asset_path, mesh_id_short). mesh_id empty when not a spline key."""
    path = (asset_path or "").strip()
    idx = path.find(SPLINE_KEY_MARKER)
    if idx < 0:
        return path, ""
    return path[:idx], path[idx + len(SPLINE_KEY_MARKER) :]


def is_spline_mesh_row(row: dict[str, str]) -> bool:
    kind = (row.get("asset_kind") or "").strip()
    notes = (row.get("notes") or "").strip()
    path = (row.get("asset_path") or "").strip()
    if kind == "SplineMesh":
        return True
    if SPLINE_KEY_MARKER in path:
        return True
    return notes.startswith("SplineMesh") or ";SplineMesh" in notes


def parse_spline_notes(notes: str) -> dict[str, Any]:
    """Parse FModel SplineMesh notes (start=x,y,z; end=...; undeformed|baked)."""
    out: dict[str, Any] = {"baked": False, "undeformed": True}
    if not notes:
        return out
    parts = [p for p in notes.split(";") if p]
    for part in parts[1:]:
        if part == "baked":
            out["baked"] = True
            out["undeformed"] = False
        elif part == "undeformed":
            out["undeformed"] = True
            out["baked"] = False
        elif "=" in part:
            k, _, v = part.partition("=")
            if "," in v:
                try:
                    out[k] = tuple(float(x) for x in v.split(","))
                except ValueError:
                    out[k] = v
            else:
                out[k] = v
    return out


def unreal_rotator_to_blender_xyz_radians(
    pitch: float, yaw: float, roll: float
) -> tuple[float, float, float]:
    """Unreal Pitch/Yaw/Roll degrees → Blender XYZ Euler radians (pass-through axes).

    Unreal's FRotationMatrix matches Blender XYZ Euler (-roll, -pitch, yaw):
    negate roll/pitch for left-handed → right-handed, keep yaw. Do **not** store
    (pitch, yaw, roll) as XYZ — that puts yaw on Blender's Y and tilts props.
    (roll, pitch, yaw) without negation matches pure yaw but fails once pitch/roll
    are non-zero.

    When ``MAP_MIRROR_Y`` is applied separately via ``apply_mirror_y_to_pose``,
    the euler/scale are adjusted further so Instance-on-Points matches the flipped
    world (in-game map orientation).
    """
    return (math.radians(-roll), math.radians(-pitch), math.radians(yaw))


def unreal_to_blender_euler(pitch: float, yaw: float, roll: float) -> mathutils.Euler:
    """Unreal degrees Pitch/Yaw/Roll → Blender XYZ Euler (radians), pass-through axes."""
    return mathutils.Euler(unreal_rotator_to_blender_xyz_radians(pitch, yaw, roll), "XYZ")


# arc_rotation / empty euler layout tags (Blender XYZ radians):
ROTATION_LAYOUT_UE = "ue_xyz"  # correct: (-roll, -pitch, yaw) before optional Y-mirror
ROTATION_LAYOUT_LEGACY_PYR = "pyr"  # buggy first import: (pitch, yaw, roll)
ROTATION_LAYOUT_RPY = "rpy"  # intermediate wrong fix: (roll, pitch, yaw)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def map_unit_scale(scene=None) -> float:
    """Blender meters per Unreal centimeter (default ``MAP_UNIT_SCALE`` = 0.01)."""
    scene = scene or getattr(bpy.context, "scene", None)
    try:
        raw = getattr(scene, "arc_map_unit_scale", None) if scene is not None else None
        value = float(raw) if raw not in (None, "") else MAP_UNIT_SCALE
        return value if value > 0.0 else MAP_UNIT_SCALE
    except (TypeError, ValueError):
        return MAP_UNIT_SCALE


def map_mirror_y(scene=None) -> bool:
    """Whether imports/orientation ops negate Unreal Y to match in-game maps."""
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return MAP_MIRROR_Y
    return bool(getattr(scene, "arc_map_mirror_y", MAP_MIRROR_Y))


def apply_mirror_y_to_pose(
    loc: tuple[float, float, float],
    euler_xyz: tuple[float, float, float],
    scale: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """Reflect a Blender pass-through pose across XZ (Unreal/Blender Y).

    For yaw-dominant props: ``M @ R @ S = RotZ(-yaw) @ diag(sx, -sy, sz)`` with
    ``M = diag(1,-1,1)``. General eulers use the same signed-scale form:
    location.y *= -1, euler (ex,ey,ez) → (-ex, ey, -ez), scale.y *= -1.
    """
    x, y, z = loc
    ex, ey, ez = euler_xyz
    sx, sy, sz = scale
    return (x, -y, z), (-ex, ey, -ez), (sx, -sy, sz)


def csv_row_to_blender_pose(
    row: dict[str, str],
    *,
    unit: float | None = None,
    mirror_y: bool | None = None,
    scene=None,
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """CSV Unreal cm row → Blender location / XYZ euler radians / scale."""
    scene = scene or getattr(bpy.context, "scene", None)
    if unit is None:
        unit = map_unit_scale(scene)
    if mirror_y is None:
        mirror_y = map_mirror_y(scene)

    loc = (
        _safe_float(row.get("x")),
        _safe_float(row.get("y")),
        _safe_float(row.get("z")),
    )
    euler = unreal_rotator_to_blender_xyz_radians(
        _safe_float(row.get("pitch")),
        _safe_float(row.get("yaw")),
        _safe_float(row.get("roll")),
    )
    scale = (
        _safe_float(row.get("scale_x"), 1.0),
        _safe_float(row.get("scale_y"), 1.0),
        _safe_float(row.get("scale_z"), 1.0),
    )
    if mirror_y:
        loc, euler, scale = apply_mirror_y_to_pose(loc, euler, scale)
    if unit != 1.0:
        loc = (loc[0] * unit, loc[1] * unit, loc[2] * unit)
    return loc, euler, scale


def empty_display_size_for_map(scene=None, *, meters: float = 5.0) -> float:
    """Empty display size in Blender units for an ~``meters``-sized marker.

    ``arc_map_unit_scale`` / ``MAP_UNIT_SCALE`` is Blender units per Unreal cm
    (0.01 → 1 BU = 1 m). So ``meters * 100 * unit`` BU.
    """
    unit = map_unit_scale(scene)
    if unit <= 0.0:
        unit = MAP_UNIT_SCALE
    return float(meters) * 100.0 * unit


def ensure_collection(name: str, parent: bpy.types.Collection | None = None) -> bpy.types.Collection:
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        if parent is not None:
            parent.children.link(coll)
        else:
            bpy.context.scene.collection.children.link(coll)
    elif parent is not None:
        # Re-parent if the collection already exists under a different parent.
        for other in list(bpy.data.collections):
            if coll.name in other.children:
                if other == parent:
                    break
                try:
                    other.children.unlink(coll)
                except Exception:
                    pass
        if coll.name not in parent.children:
            try:
                parent.children.link(coll)
            except Exception:
                pass
        # Unlink from scene root if now nested
        sc = bpy.context.scene.collection
        if coll.name in sc.children and coll.name in parent.children and parent != sc:
            try:
                sc.children.unlink(coll)
            except Exception:
                pass
    return coll


_FOLIAGE_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Trees", ("tree", "palm", "pine", "juniper", "apricot", "lemon", "olive", "cypress", "deadtree")),
    ("Vines", ("vine", "ivy", "creeper")),
    ("Bushes", ("bush", "shrub", "hedge", "berry", "candleberr")),
    ("Grass", ("grass", "weed", "fern", "lawn", "reed", "moss")),
    ("Overgrowth", ("overgrowth", "over_growth")),
    # Foliage-adjacent nature clutter (Buried City pulls MountainCompound snow piles).
    ("Snow", ("snowpile", "snow_pile", "snowbank", "snow_bank", "sm_snow", "/snow/", "snow")),
    ("Other", ("foliage", "plant", "flower", "leaf", "leaves", "planter", "vegetation", "clutterplant")),
)

# Non-foliage map organization (instancers moved under placements root).
_MAP_GROUP_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Light Modifiers",
        (
            "lightblock",
            "lightgeo",
            "lightportal",
            "light_portal",
            "lightfunction",
            "light_function",
            "lightmass",
            "importancevolume",
            "ies_profile",
            "/lighting/meshes/sm_lightgeo",
            "/lighting/meshes/sm_plane_lightportal",
            "toolkit/procstructures/sm_lightblock",
        ),
    ),
    (
        "Skybox / Spheres",
        (
            "skysphere",
            "sky_sphere",
            "skybox",
            "skydome",
            "ultra_dynamic_sky",
            "staticcloudssphere",
            "static_clouds_sphere",
            "/maptemplates/sky/",
            "/lighting/volumeclouds/",
        ),
    ),
    (
        "Debris Tiles",
        (
            "debristile",
            "debris_tile",
            "debrisrooftile",
            "debris_rooftile",
            "debrispile",
            "debris_pile",
            "concretedebris",
            "concrete_debris",
            "metaldebrispile",
            "rubblebricks",
            "/debris_tiles_",
            "/debristiles",
        ),
    ),
    (
        "Decals",
        (
            "stickerdecal",
            "tagdecal",
            "walldecal",
            "/textures/decals/",
        ),
    ),
    (
        "Planes",
        (
            "sm_waterplane",
            "/rivertool/assets/sm_waterplane",
            "waterplane_",
            "oceanbackdrop",
            "oceanlod",
            "tarmacpatch",
            "tarmac_patches",
        ),
    ),
)

# Helper / control meshes — grouped and eye-hidden by default.
_HELPER_HIDE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Occluders",
        ("occluder", "sm_occluderplane", "/toolkit/sm_occluder"),
    ),
    (
        "Engine Primitives",
        (
            "/engine/basicshapes/",
            "/engine/content/basicshapes/",
            "basicshapes/plane",
            "basicshapes/cube",
            "basicshapes/sphere",
            "basicshapes/cylinder",
            "basicshapes/cone",
        ),
    ),
    (
        "HLOD Cards",
        (
            "_hlod0_",
            "_hlod1_",
            "_hlod_",
            "hlod0_instancing",
            "hlod_instancing",
        ),
    ),
    (
        # Shared projector cards with WorldGridMaterial (no per-mesh albedo).
        # Sticker/wall/tag decals stay under Decals (may carry MI slots).
        "Decal Cards",
        (
            "decalmesh",
            "/materiallibrary/textures/decals/sm_decalmesh",
        ),
    ),
    (
        "Backdrop Landscape",
        (
            "backdroplandscape",
            "backdropisland",
        ),
    ),
    (
        "Collision Proxies",
        ("withcollision", "ucx_", "_collision", "complexcollision"),
    ),
)


# Name tokens for branding / graphic props that need unique datablocks (Stage 2
# per-actor GraphicAtlas / advertisement / MCP text / logo slots). Keep in sync
# with docs/DO_NOT_INSTANCE_DECAL_MESHES.md.
_BRANDING_GRAPHIC_NAME_RE = re.compile(
    r"(advertisementbillboard|companylogo|company_logo|mcp_text|mcptext|"
    r"roadsignscreen|signscreen|hme_banner|brandingdecal|"
    r"graphicatlas|graffiti|signage.*logo|logo.*signage)",
    re.I,
)


def is_decal_mesh_asset(asset_path: str = "", object_name: str = "") -> bool:
    """True for shared projector/sticker planes that need unique mesh datablocks.

    CSV uses StaticMesh rows like:
      .../MaterialLibrary/Textures/Decals/SM_DecalMesh_01
      .../SM_StickerDecals_01_A
      .../BrandingDecals_01/SM_Decal_AstraVenturo_01_A
    Structural *ForDecal* hosts and architecture *MeshDecalLeaks/Edges* companions
    stay GN-instanced (same MI per stem).
    """
    blob = f"{asset_path} {object_name}".lower().replace("\\", "/")
    if not blob.strip():
        return False
    compact = blob.replace("_", "").replace("-", "")
    joined = blob.replace("-", "_").replace("_", "")
    if "fordecal" in compact:
        return False
    # Building leak/edge companion meshes — shared SRC is fine (stem-local MI).
    if "meshdecalleaks" in joined or "meshdecaledges" in joined:
        return False
    if "decalmesh" in compact:
        return True
    if "stickerdecal" in compact:
        return True
    if "tagdecal" in compact or "walldecal" in compact:
        # WallTrimDecal is architecture trim, not a wall sticker card
        if "walltrimdecal" in joined or "trimdecal" in joined:
            return False
        return True
    # FrontEnd SM_MeshDecal_01..06 mural cards (not Hangar*_MeshDecal companions)
    underscored = blob.replace("-", "_")
    if re.search(r"(^|/|\s)sm_meshdecal", underscored):
        return True
    # Company branding cards: SM_Decal_AstraVenturo / Avantale / …
    if re.search(r"(^|/)sm_decal_[a-z]", blob.replace("\\", "/")):
        return True
    # POI / prop sticker cards like SM_POI16_Tower02_Decal_01 (not trim / railing)
    if (
        re.search(r"sm_[a-z0-9_]*decal_0\d", underscored)
        and "railing" not in joined
        and "walltrim" not in joined
        and "trimdecal" not in joined
        and "pipedecal" not in joined
    ):
        return True
    if "tunnelsplinedecal" in joined:
        return True
    if "raidermark" in joined:
        return True
    if "flamingodecal" in joined or "bilguuneyedecal" in joined or "handdecal" in joined:
        return True
    if "gatedecal" in joined:
        return True
    if "/materiallibrary/textures/decals/" in blob or "/textures/decals/" in blob:
        return True
    if "/brandingdecals" in blob or "/stickerdecals" in blob:
        return True
    return False


def is_plane_mesh_asset(asset_path: str = "", object_name: str = "") -> bool:
    """True for plane / flat-surface meshes that should NOT be GN-instanced.

    Any asset whose path/name contains ``plane`` (water, engine, occluder,
    light-portal, …) plus common flat tarmac patch cards. Shared meshes
    (e.g. ``SM_WaterPlane_32x32``) are scaled wildly per actor; unique
    datablocks + world-space tiling keep per-actor MIs and texel density sane.
    """
    blob = f"{asset_path} {object_name}".lower().replace("\\", "/")
    if not blob.strip():
        return False
    compact = blob.replace("-", "_")
    joined = compact.replace("_", "")
    # Broad *plane* match (occluderplane, waterplane, basicshapes/plane, …)
    if "plane" in joined and "airplane" not in joined:
        return True
    if "oceanbackdrop" in joined or "oceanlod" in joined or "oceantile" in joined:
        return True
    # Flat tarmac / asphalt patch cards (often scaled like planes, no "plane" in name)
    if "tarmacpatch" in joined or "tarmacpatches" in joined:
        return True
    if re.search(r"sm_[a-z0-9_]*tarmac[a-z0-9_]*patch", compact):
        return True
    return False


def is_poster_mesh_asset(asset_path: str = "", object_name: str = "") -> bool:
    """True for branding / poster / graphic-board meshes that should NOT be GN-instanced.

    Examples: ``SM_MCP_BrandingPoster_*``, ``SM_MCP_Text_*``, advertisement
    billboards, company logos, road sign screens, banners.
    Excludes impostor/imposter billboards (substring contains ``poster``).
    PosterFrame PropTrim MIs stay multi-slot Stage 2; unique placement lets each
    actor keep its GraphicAtlas / BackgroundDecal slots.
    """
    blob = f"{asset_path} {object_name}".lower().replace("\\", "/")
    if not blob.strip():
        return False
    compact = blob.replace("-", "_")
    joined = compact.replace("_", "")
    # Impostor/imposter billboards contain "poster" as a substring — skip.
    if "impostor" in joined or "imposter" in joined:
        return False
    if "brandingposter" in joined:
        return True
    if "companyposter" in joined or "companybrandingposter" in joined:
        return True
    # MCP text signs, ad boards, logos, road screens, banners
    if _BRANDING_GRAPHIC_NAME_RE.search(compact) or _BRANDING_GRAPHIC_NAME_RE.search(joined):
        return True
    if "banner" in joined and re.search(r"(^|/)sm_[a-z0-9_]*banner", compact):
        return True
    # SM_*Poster* / *Poster* mesh tokens (not MI_PosterFrame alone without mesh context)
    if re.search(r"(^|/)sm_[a-z0-9_]*poster", compact):
        return True
    if "poster" in joined and (
        "/props/branding/" in blob
        or "branding" in joined
        or "mcp_" in compact
        or re.search(r"(^|_)poster(_|$)", compact)
    ):
        return True
    return False


def needs_unique_mesh_placement(asset_path: str = "", object_name: str = "") -> bool:
    """Decal cards + posters/branding graphics + water/engine planes — unique datablocks."""
    return (
        is_decal_mesh_asset(asset_path, object_name)
        or is_poster_mesh_asset(asset_path, object_name)
        or is_plane_mesh_asset(asset_path, object_name)
    )


def classify_foliage_category(asset_path: str = "", object_name: str = "") -> str | None:
    """Return Trees/Vines/Bushes/Grass/Snow/Other or None when not foliage-like."""
    blob = f"{asset_path} {object_name}".lower()
    if not blob.strip():
        return None
    # Planters alone are props unless paired with plant tokens — still group under Other
    # when path clearly lives under Vegetation/Foliage folders.
    folder_hit = any(
        tok in blob
        for tok in (
            "/vegetation/",
            "/foliage/",
            "/trees/",
            "/bushes/",
            "/grass",
            "houseweeds",
            "concreteweeds",
            "statelessleaves",
        )
    )
    for category, keys in _FOLIAGE_CATEGORY_RULES:
        if any(k in blob for k in keys):
            return category
    if folder_hit:
        return "Other"
    return None


def _collection_contains(parent: bpy.types.Collection, child: bpy.types.Collection) -> bool:
    return child.name in parent.children


def _resolve_map_name(map_name: str = "") -> str:
    map_name = (map_name or "").strip()
    if map_name and map_name != "NONE":
        return map_name
    scene = bpy.context.scene
    map_name = (getattr(scene, "arc_placement_map", "") or "").strip()
    if not map_name or map_name == "NONE":
        map_name = (getattr(scene, "arc_placement_map_name", "") or "").strip()
    return map_name if map_name != "NONE" else ""


def _map_name_aliases(map_name: str = "") -> list[str]:
    """``BuriedCity_01`` / ``BuriedCity_01_P`` variants used across CSV folders vs UI."""
    name = (map_name or "").strip()
    if not name or name == "NONE":
        return []
    out = [name]
    if name.endswith("_P") and len(name) > 2:
        stem = name[:-2]
        if stem and stem not in out:
            out.append(stem)
    else:
        alt = f"{name}_P"
        if alt not in out:
            out.append(alt)
    return out


def _pick_map_name_for_scene(map_name: str = "", *, csv_path: str = "") -> str:
    """Choose the map tag that actually owns instancers / collections.

    Stage 1 stamps ``arc_map`` from the UI/dropdown; finalize used to overwrite with
    the CSV folder leaf, which silently skipped grouping when names disagreed.
    """
    candidates: list[str] = []
    for raw in (
        map_name,
        _resolve_map_name(map_name),
        os.path.basename(os.path.dirname(bpy.path.abspath(csv_path or "").rstrip("\\/")))
        if csv_path
        else "",
    ):
        for alias in _map_name_aliases(raw):
            if alias and alias not in candidates:
                candidates.append(alias)
    if not candidates:
        return ""
    for cand in candidates:
        if bpy.data.collections.get(f"{cand}_Placements") is not None:
            return cand
        if bpy.data.collections.get(f"{cand}_Instanced") is not None:
            return cand
    for cand in candidates:
        for _obj in _iter_map_instancers(cand, exact=True):
            return cand
    return candidates[0]


def _foliage_root_name(map_name: str) -> str:
    """Prefer ``{Map}_P_Foliage`` (BuriedCity_01_P_Foliage) naming."""
    if map_name.endswith("_P"):
        # BuriedCity_01_P → BuriedCity_01_P_Foliage
        return f"{map_name}_Foliage"
    return f"{map_name}_P_Foliage"


def _static_mesh_actors_coll_name(map_name: str) -> str:
    """``{Map}_StaticMeshActors`` — sibling under ``{Map}_Placements``."""
    return f"{(map_name or 'Map').strip() or 'Map'}_StaticMeshActors"


def _glass_coll_name(map_name: str) -> str:
    """``{Map}_Glass`` — transparent / BrokenGlass props (costly to render)."""
    return f"{(map_name or 'Map').strip() or 'Map'}_Glass"


def object_references_glass(obj: bpy.types.Object) -> bool:
    """True when this mesh or its InstanceSources SRC has a glass / BrokenGlass slot."""
    if obj is None or obj.type != "MESH":
        return False
    try:
        from . import materials as mats_mod
    except Exception:
        mats_mod = None

    def _check(mesh_obj) -> bool:
        if mesh_obj is None or mesh_obj.type != "MESH":
            return False
        if mats_mod is not None:
            try:
                return bool(mats_mod.mesh_references_glass(mesh_obj))
            except Exception:
                pass
        for slot in mesh_obj.material_slots or []:
            mat = slot.material
            if mat is None:
                continue
            fam = str(mat.get("arc_mi_family") or "")
            name_l = (mat.name or "").lower()
            stem_l = str(mat.get("arc_mi_stem") or "").lower()
            if fam == "glass" or "brokenglass" in name_l or "brokenglass" in stem_l:
                return True
        return False

    if _check(obj):
        return True
    # Instancer → SRC
    src_name = str(obj.get("arc_instance_source_name") or "")
    if src_name:
        src = bpy.data.objects.get(src_name)
        if _check(src):
            return True
    # SRC flag on the object itself
    if obj.get("arc_instance_source") and _check(obj):
        return True
    return False


def is_static_mesh_actor_object(obj: bpy.types.Object) -> bool:
    """True for StaticMeshActor / CSV ``asset_kind=StaticMesh`` placements.

    Fast-import instancers stamp ``arc_asset_kind``; unique meshes / empties also
    match ``StaticMeshActor*`` actor names from the placements CSV.
    """
    if obj is None:
        return False
    kind = str(obj.get("arc_asset_kind") or "").strip()
    if kind == "StaticMesh":
        return True
    if kind in (
        "InstancedStaticMesh",
        "HierarchicalInstancedStaticMesh",
        "FoliageInstancedStaticMesh",
        "SplineMesh",
    ):
        return False
    actor = str(obj.get("arc_actor_name") or "").strip()
    if actor.startswith("StaticMeshActor"):
        return True
    name = str(getattr(obj, "name", "") or "")
    if name.startswith("StaticMeshActor"):
        return True
    return False


_RELINK_GROUP_NAMES = frozenset({
    "Light Modifiers",
    "Skybox / Spheres",
    "Debris Tiles",
    "Decals",
    "Planes",
    "Helpers",
    "Occluders",
    "Engine Primitives",
    "HLOD Cards",
    "Decal Cards",
    "Landscape LOD",
    "Backdrop Landscape",
    "Collision Proxies",
    "Trees",
    "Vines",
    "Bushes",
    "Grass",
    "Overgrowth",
    "Snow",
    "Other",
    "StaticMeshActors",
    "Glass",
})


def _relink_instancer_to_collection(
    obj: bpy.types.Object,
    dest: bpy.types.Collection,
    *,
    map_name: str,
    placements: bpy.types.Collection,
    known_group_names: set[str],
    move_sources: bool = False,
) -> bool:
    """Move an instancer into ``dest``, unlinking Instanced/Placements/group peers."""
    users = list(obj.users_collection)
    already = any(c == dest for c in users)
    changed = False
    if not already:
        dest.objects.link(obj)
        changed = True
        users = list(obj.users_collection)
    for coll in users:
        if coll == dest:
            continue
        cname = coll.name
        if cname.endswith("_InstanceSources") and not move_sources:
            continue
        unlink = (
            cname.endswith("_Instanced")
            or coll == placements
            or cname.endswith("_Foliage")
            or cname.endswith("_StaticMeshActors")
            or cname in known_group_names
            or cname in _RELINK_GROUP_NAMES
            or (
                cname.startswith(map_name)
                and ("Instanced" in cname or cname.endswith("_Placements"))
            )
        )
        if unlink:
            try:
                coll.objects.unlink(obj)
                changed = True
            except Exception:
                pass
    return changed


def group_map_foliage(
    map_name: str = "",
    *,
    move_sources: bool = False,
) -> dict[str, int]:
    """Move foliage instancers under ``{map}_P_Foliage/{Trees|Vines|...|Overgrowth}``.

    Geometry Nodes instance sources stay in ``*_InstanceSources`` by default so
    GN object refs keep evaluating; set ``move_sources`` to also nest SRC meshes.
    """
    map_name = _pick_map_name_for_scene(map_name) or _resolve_map_name(map_name)
    if not map_name:
        return {"moved": 0, "skipped": 0, "categories": 0}

    placements = bpy.data.collections.get(f"{map_name}_Placements")
    if placements is None:
        placements = ensure_collection(f"{map_name}_Placements")

    foliage_root = ensure_collection(_foliage_root_name(map_name), parent=placements)

    cat_colls: dict[str, bpy.types.Collection] = {}
    for cat, _ in _FOLIAGE_CATEGORY_RULES:
        cat_colls[cat] = ensure_collection(cat, parent=foliage_root)

    known = set(cat_colls.keys())
    stats = {"moved": 0, "skipped": 0, "sources_linked": 0, "categories": len(cat_colls)}

    for obj in list(_iter_map_instancers(map_name)):
        asset = str(obj.get("arc_asset_path") or "")
        cat = classify_foliage_category(asset, obj.name)
        if cat is None:
            stats["skipped"] += 1
            continue
        dest = cat_colls[cat]
        if _relink_instancer_to_collection(
            obj, dest, map_name=map_name, placements=placements, known_group_names=known, move_sources=move_sources
        ):
            stats["moved"] += 1
            obj["arc_foliage_category"] = cat
        else:
            stats["skipped"] += 1

        if move_sources:
            src_name = str(obj.get("arc_instance_source_name") or "")
            src = bpy.data.objects.get(src_name) if src_name else None
            if src is not None:
                if dest.name not in {c.name for c in src.users_collection}:
                    dest.objects.link(src)
                    stats["sources_linked"] += 1

    return stats


def classify_map_group_category(asset_path: str = "", object_name: str = "") -> str | None:
    """Return Light Modifiers / Skybox / Debris Tiles or None."""
    blob = f"{asset_path} {object_name}".lower()
    if not blob.strip():
        return None
    # Decorative lamps / light poles stay as normal props (not Light Modifiers).
    if any(
        tok in blob
        for tok in (
            "lamp",
            "lightpole",
            "light_pole",
            "floodlight",
            "ceilinglamp",
            "streetlamp",
            "constructionlight",
            "lightceiling",
            "lightstrip",
            "fluorescent",
        )
    ) and not any(tok in blob for tok in ("lightblock", "lightgeo", "lightportal")):
        # Still allow sky / debris classification below.
        pass
    for category, keys in _MAP_GROUP_RULES:
        if category == "Light Modifiers":
            # Skip real light fixtures even if path contains /Lighting/
            if any(
                tok in blob
                for tok in (
                    "lamp",
                    "lightpole",
                    "floodlight",
                    "ceilinglamp",
                    "streetlamp",
                    "constructionlight",
                    "lightceiling",
                    "lightstrip",
                    "fluorescent",
                    "lantern",
                )
            ) and not any(k in blob for k in ("lightblock", "lightgeo", "lightportal", "lightfunction")):
                continue
        if any(k in blob for k in keys):
            return category
    return None


def classify_helper_hide_category(asset_path: str = "", object_name: str = "") -> str | None:
    """Return helper category when mesh should be eye-hidden by default.

    Covers occluders, Engine BasicShapes, HLOD impostor cards, DecalMesh
    projector cards, backdrop landscape, collision proxies, and light modifiers.
    Structural *ForDecal* hosts and textured sticker/wall/tag cards stay visible.
    """
    blob = f"{asset_path} {object_name}".lower().replace("\\", "/")
    if not blob.strip():
        return None
    compact = blob.replace("_", "").replace("-", "")
    if "fordecal" in compact:
        return None
    # Real advertisement billboards are props — only HLOD impostor tokens hide.
    for category, keys in _HELPER_HIDE_RULES:
        if category == "HLOD Cards" and "advertisementbillboard" in compact:
            continue
        if any(k in blob for k in keys):
            return category
    # Light-modifier helpers are also hidden by default.
    if classify_map_group_category(asset_path, object_name) == "Light Modifiers":
        return "Light Modifiers"
    return None


def group_map_collections(
    map_name: str = "",
    *,
    hide_helpers: bool = True,
    include_foliage: bool = True,
    include_static_mesh_actors: bool = True,
    include_glass: bool = True,
) -> dict[str, int]:
    """Organize map instancers into foliage + Light/Sky/Debris/Planes + Helpers + SMA + Glass.

    Safe to re-run after reimport. Helpers (occluders, Engine BasicShapes, HLOD
    cards, DecalMesh projector cards, backdrop landscape, light blockers,
    collision proxies) are moved under ``Helpers/`` and hidden when
    ``hide_helpers`` is True. Water planes go under ``Planes`` (visible).
    Remaining StaticMeshActor / ``asset_kind=StaticMesh`` placements land in
    ``{Map}_StaticMeshActors``. Meshes with glass / BrokenGlass slots go under
    ``{Map}_Glass`` (transparent materials cost more to render). WP ``SM_Landscape_*``
    tiles stay visible via ``organize_landscape_tiles``. Single pass over
    instancers (cheap name classify).
    """
    map_name = _pick_map_name_for_scene(map_name) or _resolve_map_name(map_name)
    stats: dict[str, int] = {
        "moved": 0,
        "hidden": 0,
        "foliage_moved": 0,
        "sma_moved": 0,
        "glass_moved": 0,
        "skipped": 0,
        "categories": 0,
    }
    if not map_name:
        return stats

    placements = bpy.data.collections.get(f"{map_name}_Placements")
    if placements is None:
        # Alias collection may exist under BuriedCity_01_P while UI says BuriedCity_01.
        for alias in _map_name_aliases(map_name):
            placements = bpy.data.collections.get(f"{alias}_Placements")
            if placements is not None:
                map_name = alias
                break
    if placements is None:
        placements = ensure_collection(f"{map_name}_Placements")

    foliage_colls: dict[str, bpy.types.Collection] = {}
    if include_foliage:
        foliage_root = ensure_collection(_foliage_root_name(map_name), parent=placements)
        for cat, _ in _FOLIAGE_CATEGORY_RULES:
            foliage_colls[cat] = ensure_collection(cat, parent=foliage_root)

    group_colls: dict[str, bpy.types.Collection] = {}
    for cat, _ in _MAP_GROUP_RULES:
        group_colls[cat] = ensure_collection(cat, parent=placements)

    helpers_root = ensure_collection("Helpers", parent=placements)
    helper_colls: dict[str, bpy.types.Collection] = {
        cat: ensure_collection(cat, parent=helpers_root) for cat, _ in _HELPER_HIDE_RULES
    }
    # Light modifiers live in their own top-level collection but still hide.
    helper_colls["Light Modifiers"] = group_colls["Light Modifiers"]

    sma_coll: bpy.types.Collection | None = None
    sma_name = _static_mesh_actors_coll_name(map_name)
    if include_static_mesh_actors:
        sma_coll = ensure_collection(sma_name, parent=placements)

    glass_coll: bpy.types.Collection | None = None
    glass_name = _glass_coll_name(map_name)
    if include_glass:
        glass_coll = ensure_collection(glass_name, parent=placements)

    known = set(group_colls.keys()) | set(helper_colls.keys()) | set(foliage_colls.keys()) | {
        "Helpers",
        "Trees",
        "Vines",
        "Bushes",
        "Grass",
        "Overgrowth",
        "Snow",
        "Other",
        "Decals",
        sma_name,
        "StaticMeshActors",
        glass_name,
        "Glass",
    }
    stats["categories"] = (
        len(group_colls)
        + len(helper_colls)
        + len(foliage_colls)
        + (1 if sma_coll else 0)
        + (1 if glass_coll else 0)
    )

    for obj in list(_iter_map_groupable_objects(map_name)):
        asset = str(obj.get("arc_asset_path") or "")
        dest: bpy.types.Collection | None = None
        hide_cat: str | None = None

        if include_foliage:
            foliage_cat = classify_foliage_category(asset, obj.name)
            if foliage_cat is not None:
                dest = foliage_colls.get(foliage_cat)

        if dest is None:
            hide_cat = classify_helper_hide_category(asset, obj.name)
            group_cat = classify_map_group_category(asset, obj.name)
            if hide_cat and hide_cat in helper_colls:
                dest = helper_colls[hide_cat]
            elif group_cat and group_cat in group_colls:
                dest = group_colls[group_cat]
            elif (
                obj.get("arc_decal_mesh")
                or obj.get("arc_poster_mesh")
                or is_decal_mesh_asset(asset, obj.name)
                or is_poster_mesh_asset(asset, obj.name)
            ):
                dest = group_colls.get("Decals")
            elif obj.get("arc_plane_mesh") or is_plane_mesh_asset(asset, obj.name):
                dest = group_colls.get("Planes")
            elif include_glass and glass_coll is not None and object_references_glass(obj):
                dest = glass_coll
            elif include_static_mesh_actors and sma_coll is not None and is_static_mesh_actor_object(obj):
                dest = sma_coll

        if dest is None:
            stats["skipped"] += 1
            continue

        if _relink_instancer_to_collection(
            obj, dest, map_name=map_name, placements=placements, known_group_names=known
        ):
            if dest.name in foliage_colls or dest.name in {
                "Trees", "Vines", "Bushes", "Grass", "Overgrowth", "Snow", "Other"
            }:
                stats["foliage_moved"] += 1
                obj["arc_foliage_category"] = dest.name
            elif glass_coll is not None and dest == glass_coll:
                stats["glass_moved"] += 1
                obj["arc_map_group"] = "Glass"
            elif sma_coll is not None and dest == sma_coll:
                stats["sma_moved"] += 1
                obj["arc_map_group"] = "StaticMeshActors"
            else:
                stats["moved"] += 1
                obj["arc_map_group"] = dest.name
        else:
            stats["skipped"] += 1

        if hide_helpers and hide_cat:
            try:
                obj.hide_set(True)
                obj.hide_render = True
                obj["arc_helper_hidden"] = 1
                stats["hidden"] += 1
            except Exception:
                pass

    # Also move unique InstanceSources SRC meshes that reference glass
    if include_glass and glass_coll is not None:
        for obj in list(bpy.data.objects):
            if obj.type != "MESH" or not obj.get("arc_instance_source"):
                continue
            if map_name and (obj.get("arc_map") or "") not in set(_map_name_aliases(map_name)):
                continue
            if not object_references_glass(obj):
                continue
            if _relink_instancer_to_collection(
                obj, glass_coll, map_name=map_name, placements=placements,
                known_group_names=known, move_sources=True,
            ):
                stats["glass_moved"] += 1
                obj["arc_map_group"] = "Glass"

    # Hide helper collections in the outliner viewport by default.
    if hide_helpers:
        for coll in helper_colls.values():
            try:
                coll.hide_viewport = True
                coll.hide_render = True
            except Exception:
                pass
        try:
            helpers_root.hide_viewport = True
            helpers_root.hide_render = True
        except Exception:
            pass

    # Skybox / spheres drown the city view — hide by default (collection only; GN safe).
    sky = group_colls.get("Skybox / Spheres")
    if sky is not None:
        try:
            sky.hide_viewport = True
            sky.hide_render = True
            stats["skybox_hidden"] = 1
        except Exception:
            stats["skybox_hidden"] = 0
        for obj in list(sky.objects):
            try:
                obj.hide_set(True)
                obj.hide_render = True
            except Exception:
                pass

    return stats


def _is_building_shell_asset(asset: str) -> bool:
    """True for primary residential/building shell meshes (not trim/detail props)."""
    a = (asset or "").lower()
    if "sm_bc_building_" not in a:
        return False
    for bad in (
        "decal",
        "roofhouse",
        "window",
        "door",
        "balcon",
        "mould",
        "molding",
        "trim",
        "shutter",
        "cobweb",
        "sand_pile",
        "weeds",
    ):
        if bad in a:
            return False
    return True


def strip_shell_origin_pile_details(
    map_name: str = "",
    *,
    shell_name_substrings: tuple[str, ...] = (
        "SM_BC_Building_Residential",
        "SM_BC_Building_",
        "/Building_Residential_",
    ),
    pos_eps: float | None = None,
    ang_eps: float | None = None,
    min_assets: int = 2,
) -> dict[str, int]:
    """Remove non-shell points that share a pose with a building shell (child-at-root piles).

    Live stopgap until FModel re-exports AttachSocketName-aware AbsoluteTransforms.
    Keeps shell instances; strips piled detail/window/trim points at the same pose.
    """
    if pos_eps is None:
        pos_eps = DEFAULT_DEDUP_POS_EPS
    if ang_eps is None:
        ang_eps = DEFAULT_DEDUP_ANG_EPS
    map_name = (map_name or "").strip()
    analysis = analyze_origin_piles(
        map_name, pos_eps=pos_eps, ang_eps=ang_eps, min_assets=min_assets
    )
    piles = analysis.get("piles") or {}
    shell_poses: set[tuple] = set()
    for pose, assets in piles.items():
        if any(_is_building_shell_asset(str(a)) for a in assets):
            shell_poses.add(pose)
        elif any(
            any(s.lower() in str(a).lower() for s in shell_name_substrings)
            for a in assets
        ):
            # Fallback: residential path tokens even if shell heuristic missed
            if any("building_residential" in str(a).lower() for a in assets):
                shell_poses.add(pose)

    removed = 0
    instancers_touched = 0
    for obj in list(_iter_map_instancers(map_name)):
        asset = str(obj.get("arc_asset_path") or obj.name)
        if _is_building_shell_asset(asset):
            continue

        mesh = obj.data
        rot_attr = mesh.attributes.get(ATTR_ROTATION)
        if rot_attr is None:
            continue
        keep: list[int] = []
        stripped_here = 0
        for i, vert in enumerate(mesh.vertices):
            rot = rot_attr.data[i].vector
            key = _pose_key_from_point(vert.co, rot, pos_eps=pos_eps, ang_eps=ang_eps)
            if key in shell_poses:
                stripped_here += 1
                continue
            keep.append(i)
        if stripped_here:
            removed += _rebuild_instancer_from_indices(obj, keep)
            instancers_touched += 1
            if obj.data and len(obj.data.vertices) == 0:
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except Exception:
                    pass

    return {
        "shell_piles": len(shell_poses),
        "points_removed": removed,
        "instancers_touched": instancers_touched,
    }

_MESH_EXTS = (".psk", ".pskx", ".uemodel", ".PSK", ".PSKX", ".UEMODEL")
# UEModel StaticMesh export is typically .uemodel; FModel ActorX uses .psk/.pskx.
_SOFT_SM_RE = re.compile(
    r"(?:/Game/|/Engine/|/Pioneer/)[^\s\"'<>]+?(?:SM_[A-Za-z0-9_]+)",
    re.IGNORECASE,
)


def package_to_content_rel(package: str) -> str | None:
    """'/Game/Pioneer/Foo/Bar' or '/Pioneer/Foo/Bar' -> 'Pioneer/Foo/Bar'."""
    if not package:
        return None
    pkg = package.replace("\\", "/").strip()
    # ObjectPath export index: .../SM_Foo.2 -> .../SM_Foo
    # Soft object path: .../SM_Foo.SM_Foo -> .../SM_Foo
    leaf = pkg.rsplit("/", 1)[-1]
    if "." in leaf and not leaf.lower().endswith((".psk", ".pskx", ".uemodel", ".uasset", ".json")):
        head, _, tail = leaf.rpartition(".")
        if head and (tail.isdigit() or tail == head or tail.lower() in {"uasset", "umap"}):
            pkg = pkg[: -len(tail) - 1]
    for prefix in ("/Game/", "/Engine/", "/EmbarkScript/", "/Pioneer/"):
        if pkg.startswith(prefix):
            rest = pkg[len(prefix) :]
            if prefix == "/Game/":
                return rest
            if prefix == "/Pioneer/":
                return "Pioneer/" + rest
            if prefix == "/Engine/":
                return "Engine/" + rest
            return "RemappedPlugins/EmbarkScript/Content/" + rest
    if pkg.startswith("Pioneer/"):
        return pkg
    return None


def _mesh_stem_aliases(name: str) -> list[str]:
    """BP_Foo → SM_Foo (UEModel exports the StaticMesh, not the Blueprint)."""
    out: list[str] = []
    if not name:
        return out
    out.append(name)
    if name.startswith("BP_"):
        out.append("SM_" + name[3:])
    elif name.startswith("BP") and len(name) > 2 and name[2].isupper():
        out.append("SM_" + name[2:])
    # Dedup preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for n in out:
        key = n.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(n)
    return uniq


def _content_search_roots(pioneer_root: str) -> list[str]:
    root = os.path.abspath(bpy.path.abspath(pioneer_root))
    candidates = [root]
    lower = root.replace("\\", "/").lower()
    if lower.endswith("/pioneergame"):
        candidates.append(os.path.join(root, "Content"))
    elif lower.endswith("/content"):
        pass
    elif lower.endswith("/pioneer"):
        candidates.append(os.path.dirname(root))
    # FModel MapPlacements/{Map} uses PioneerGame/Content + Engine/Content under the root.
    # When PioneerGame Root is pointed at that folder, treat it as an export root — not
    # as cooked Content sitting directly under the root.
    if _is_fmodel_map_export_root(root):
        for sub in (
            os.path.join(root, "PioneerGame", "Content"),
            os.path.join(root, "Engine", "Content"),
            os.path.join(root, "Game"),
            os.path.join(root, "Content"),
        ):
            if os.path.isdir(sub):
                candidates.append(sub)
    # Dedup
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        key = os.path.normcase(os.path.abspath(c))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _folder_candidates_for_rel(base: str, rel: str) -> list[str]:
    stem = rel.replace("/", os.sep)
    paths = [
        os.path.join(base, stem),
        os.path.join(base, "Content", stem),
        os.path.join(base, "PioneerGame", "Content", stem),
    ]
    if not stem.startswith("Pioneer") and not stem.startswith("Engine"):
        paths.append(os.path.join(base, "Pioneer", stem))
    if stem.startswith("Engine"):
        # Engine/BasicShapes/... vs Engine/Content/BasicShapes/...
        rest = stem[len("Engine") :].lstrip("\\/")
        if rest and not rest.lower().startswith("content"):
            paths.append(os.path.join(base, "Engine", "Content", rest))
    return paths


def _find_mesh_file(parent: str, stem: str) -> str | None:
    if not parent or not os.path.isdir(parent):
        return None
    # Prefer UEModel, then PSKX, then PSK — Stage 1 imports PSK/PSKX today and
    # falls back when .uemodel has no importer.
    preferred_exts = (".uemodel", ".UEMODEL", ".pskx", ".PSKX", ".psk", ".PSK")
    for name in _mesh_stem_aliases(stem):
        for ext in preferred_exts:
            path = os.path.join(parent, name + ext)
            if os.path.isfile(path):
                return path
    # Shallow: any mesh whose name contains the SM_/BP_ stem
    try:
        aliases = [a.lower() for a in _mesh_stem_aliases(stem)]
        hits: list[str] = []
        for fn in os.listdir(parent):
            low = fn.lower()
            if not low.endswith((".psk", ".pskx", ".uemodel")):
                continue
            body = os.path.splitext(low)[0]
            if any(a in body or body in a for a in aliases):
                hits.append(os.path.join(parent, fn))
        if hits:
            def _rank(p: str) -> int:
                e = os.path.splitext(p)[1].lower()
                return {".uemodel": 0, ".pskx": 1, ".psk": 2}.get(e, 9)
            hits.sort(key=_rank)
            return hits[0]
    except OSError:
        pass
    return None


def _coerce_imported_meshes(result) -> list:
    """Normalize UEFormat / PSK import return values to a list of MESH objects."""
    if result is None:
        return []
    if isinstance(result, list):
        return [o for o in result if getattr(o, "type", None) == "MESH"]
    if getattr(result, "type", None) == "MESH":
        return [result]
    # Some importers return an armature/empty root — collect mesh children.
    kids = []
    try:
        for obj in getattr(result, "children_recursive", []) or []:
            if getattr(obj, "type", None) == "MESH":
                kids.append(obj)
    except Exception:
        pass
    return kids


def _import_uemodel(mesh_path: str) -> list:
    """Try known UEFormat importers (standalone io_scene_ueformat or BlenderUmap2).

    Map placements CSV is Unreal cm; Blender map space uses ``map_unit_scale()``
    (default 0.01 → meters). Pass that as UEFormat ``scale_factor``.
    """
    scale = map_unit_scale()

    # 1) Standalone io_scene_ueformat add-on
    try:
        from io_scene_ueformat.importer import import_file as import_ueformat  # type: ignore

        try:
            return _coerce_imported_meshes(import_ueformat(mesh_path, scale_factor=scale))
        except TypeError:
            return _coerce_imported_meshes(import_ueformat(mesh_path))
    except Exception:
        pass

    # 2) BlenderUmap2 wrapper — build importer with map unit scale
    for mod_name in (
        "bl_ext.user_default.blenderumap2.ueformat.ue_format",
        "ueformat.ue_format",
    ):
        try:
            import importlib

            mod = importlib.import_module(mod_name)
            UEFormatImport = getattr(mod, "UEFormatImport", None)
            UEModelOptions = getattr(mod, "UEModelOptions", None)
            if UEFormatImport is None or UEModelOptions is None:
                continue
            importer = UEFormatImport(
                UEModelOptions(False, scale_factor=scale, import_morph_targets=False)
            )
            return _coerce_imported_meshes(importer.import_file(mesh_path))
        except Exception:
            continue

    # 3) wrapper.import_model (often defaults to 0.01 — rescale to map unit)
    for mod_name in (
        "bl_ext.user_default.blenderumap2.ueformat.wrapper",
        "ueformat.wrapper",
    ):
        try:
            import importlib

            mod = importlib.import_module(mod_name)
            fn = getattr(mod, "import_model", None)
            if not callable(fn):
                continue
            meshes = _coerce_imported_meshes(fn(mesh_path))
            # Wrapper commonly imports at 0.01; normalize object scale to map unit.
            target = scale / 0.01 if scale > 0 else 1.0
            for obj in meshes:
                try:
                    obj.scale = tuple(float(s) * target for s in obj.scale)
                except Exception:
                    obj.scale = (target, target, target)
            return meshes
        except Exception:
            continue

    return []


def _import_map_mesh(mesh_path: str) -> list:
    """Import a map mesh. Prefer UEModel via UEFormat when available; else PSK/PSKX."""
    low = mesh_path.lower()
    unit = map_unit_scale()
    if low.endswith(".uemodel"):
        meshes = _import_uemodel(mesh_path)
        if meshes:
            # UEFormat already applies unit scale; only normalize UV layer names.
            return _normalize_imported_map_uvs(meshes)
        # Sibling PSK/PSKX fallback next to the uemodel
        stem, _ = os.path.splitext(mesh_path)
        for ext in (".pskx", ".psk", ".PSKX", ".PSK"):
            sibling = stem + ext
            if os.path.isfile(sibling):
                try:
                    meshes = importing.import_psk(sibling) or []
                    return _scale_imported_map_meshes(meshes, unit)
                except Exception:
                    return []
        return []
    try:
        meshes = importing.import_psk(mesh_path) or []
        return _scale_imported_map_meshes(meshes, unit)
    except Exception:
        return []


def _normalize_imported_map_uvs(meshes: list) -> list:
    """Rename UVMap/EXTRAUV* → UV0/UV1 so GraphicAtlas ``Use UV1`` binds correctly."""
    if not meshes:
        return meshes
    from . import utils as _utils
    for obj in meshes:
        try:
            _utils.normalize_object_ue_uv_layers(obj)
        except Exception:
            pass
    return meshes


def _scale_imported_map_meshes(meshes: list, unit: float) -> list:
    """PSK imports are typically cm-sized; apply map unit scale on the object."""
    meshes = _normalize_imported_map_uvs(meshes)
    if not meshes or abs(unit - 1.0) < 1e-12:
        return meshes
    for obj in meshes:
        try:
            obj.scale = tuple(float(s) * unit for s in obj.scale)
        except Exception:
            try:
                obj.scale = (unit, unit, unit)
            except Exception:
                pass
    return meshes


def unflip_map_source_mesh_y(obj: bpy.types.Object) -> bool:
    """Bake ``co.y *= -1`` on a map SRC mesh so local space matches Unreal sockets.

    UEFormat imports often store StaticMesh verts with Y negated vs UStaticMeshSocket
    RelativeLocation. Map instances also use ``arc_scale.y < 0`` from MAP_MIRROR_Y.
    Together that puts shell geometry on the opposite side of the pivot from
    socket-placed details (mirror-plane split). Un-flipping SRC Y once makes
    ``instanceMatrix @ socketLocal`` land on the visible shell surface.

    Idempotent via ``arc_mesh_y_unflip``.
    """
    if obj is None or obj.type != "MESH" or not obj.data:
        return False
    if obj.get("arc_mesh_y_unflip"):
        return False
    mesh = obj.data
    n = len(mesh.vertices)
    if n <= 0:
        return False
    coords = [0.0] * (n * 3)
    mesh.vertices.foreach_get("co", coords)
    for i in range(n):
        coords[i * 3 + 1] *= -1.0
    mesh.vertices.foreach_set("co", coords)
    try:
        mesh.flip_normals()
    except Exception:
        pass
    mesh.update()
    try:
        obj["arc_mesh_y_unflip"] = 1
    except Exception:
        pass
    return True


def unflip_all_map_source_meshes(map_name: str = "") -> dict[str, int]:
    """Apply :func:`unflip_map_source_mesh_y` to every map instance source."""
    map_name = (map_name or "").strip()
    stats = {"unflipped": 0, "skipped": 0}
    for obj in list(bpy.data.objects):
        if not obj.get("arc_instance_source"):
            continue
        if map_name and (obj.get("arc_map") or "") not in ("", map_name):
            continue
        if unflip_map_source_mesh_y(obj):
            stats["unflipped"] += 1
        else:
            stats["skipped"] += 1
    return stats


def diagnose_building_shell_detail_alignment(
    map_name: str = "",
    *,
    shell_substr: str = "SM_BC_Building_Residential_01_C45_3F_A",
    detail_substr: str = "WindowMolding",
    max_shells: int = 5,
) -> list[dict[str, Any]]:
    """Report whether detail pivots sit on the same side of the shell pivot as the shell body.

    Positive ``xy_dot`` ≈ same side (good after mesh Y-unflip). Negative ≈ mirror-plane split.
    """
    map_name = (map_name or "").strip()
    if not map_name or map_name == "NONE":
        scene = bpy.context.scene
        map_name = (getattr(scene, "arc_placement_map", "") or "").strip()
        if not map_name or map_name == "NONE":
            map_name = (getattr(scene, "arc_placement_map_name", "") or "").strip()
    reports: list[dict[str, Any]] = []
    shells = [
        o
        for o in _iter_map_instancers(map_name)
        if shell_substr in (o.get("arc_asset_path") or o.name)
    ]
    # Fallback: map tag may be BuriedCity_01 vs BuriedCity_01_P
    if not shells and map_name.endswith("_P"):
        shells = [
            o
            for o in _iter_map_instancers(map_name[: -2])
            if shell_substr in (o.get("arc_asset_path") or o.name)
        ]
    if not shells and not map_name.endswith("_P"):
        shells = [
            o
            for o in _iter_map_instancers(map_name + "_P")
            if shell_substr in (o.get("arc_asset_path") or o.name)
        ]
    if not shells:
        return reports
    shell = shells[0]
    src_name = str(shell.get("arc_instance_source_name") or "")
    src = bpy.data.objects.get(src_name) if src_name else None
    if src is None or not src.data:
        return reports
    details = [
        o
        for o in _iter_map_instancers(map_name)
        if detail_substr in (o.get("arc_asset_path") or o.name)
    ]
    mesh = shell.data
    rot = mesh.attributes.get(ATTR_ROTATION)
    sca = mesh.attributes.get(ATTR_SCALE)
    n = min(len(mesh.vertices), max_shells)
    for i in range(n):
        p = mesh.vertices[i].co.copy()
        r = rot.data[i].vector if rot else mathutils.Vector((0, 0, 0))
        s = sca.data[i].vector if sca else mathutils.Vector((1, 1, 1))
        mat = (
            mathutils.Matrix.Translation(p)
            @ mathutils.Euler((r.x, r.y, r.z), "XYZ").to_matrix().to_4x4()
            @ mathutils.Matrix.Diagonal((s.x, s.y, s.z, 1.0))
        )
        shell_world = [mat @ v.co for v in src.data.vertices]
        shell_cen = sum(shell_world, mathutils.Vector()) / max(len(shell_world), 1)
        xs = [v.x for v in shell_world]
        ys = [v.y for v in shell_world]
        zs = [v.z for v in shell_world]
        pad = 2.0
        lo = (min(xs) - pad, min(ys) - pad, min(zs) - pad)
        hi = (max(xs) + pad, max(ys) + pad, max(zs) + pad)
        near: list[mathutils.Vector] = []
        on_surface = 0
        for dobj in details:
            dm = dobj.data
            for vert in dm.vertices:
                co = vert.co
                if not (
                    lo[0] <= co.x <= hi[0]
                    and lo[1] <= co.y <= hi[1]
                    and lo[2] <= co.z <= hi[2]
                ):
                    continue
                near.append(co.copy())
                dmin = min((sw - co).length for sw in shell_world[:: max(1, len(shell_world) // 800)])
                if dmin < 0.5:
                    on_surface += 1
        if not near:
            reports.append(
                {
                    "shell_index": i,
                    "pivot": tuple(round(x, 3) for x in p),
                    "n_details": 0,
                    "xy_dot": None,
                    "same_side": None,
                    "src_y_unflipped": bool(src.get("arc_mesh_y_unflip")),
                }
            )
            continue
        det_cen = sum(near, mathutils.Vector()) / len(near)
        v_shell = shell_cen - p
        v_det = det_cen - p
        xy_dot = v_shell.x * v_det.x + v_shell.y * v_det.y
        reports.append(
            {
                "shell_index": i,
                "pivot": (round(p.x, 3), round(p.y, 3), round(p.z, 3)),
                "shell_cen": (round(shell_cen.x, 3), round(shell_cen.y, 3), round(shell_cen.z, 3)),
                "detail_cen": (round(det_cen.x, 3), round(det_cen.y, 3), round(det_cen.z, 3)),
                "n_details": len(near),
                "on_surface_lt_0_5m": on_surface,
                "xy_dot": round(xy_dot, 3),
                "same_side": xy_dot > 0.0,
                "src_y_unflipped": bool(src.get("arc_mesh_y_unflip")),
                "offset_len": round((det_cen - shell_cen).length, 3),
            }
        )
    return reports


def _soft_sm_packages_from_json(json_path: str) -> list[str]:
    """Pull StaticMesh SoftObjectPaths from a BP/SM JSON dump (Tencent/FModel)."""
    if not json_path or not os.path.isfile(json_path):
        return []
    try:
        text = open(json_path, "r", encoding="utf-8", errors="ignore").read()
    except OSError:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for match in _SOFT_SM_RE.finditer(text):
        raw = match.group(0)
        # Normalize ObjectPath export index
        rel = package_to_content_rel(raw)
        if not rel:
            continue
        # Rebuild a /Game/-style path for resolve
        pkg = "/Game/" + rel if not raw.startswith("/Engine/") else raw
        key = pkg.lower()
        if key not in seen:
            seen.add(key)
            found.append(pkg)
    return found


def _normalize_asset_key(asset_path: str) -> str:
    """Collapse /Game/A/SM_X.SM_X and /Game/A/SM_X.uasset → /game/a/sm_x for lookups.

    Preserves trailing ``#spline:{id}`` so baked SplineMesh keys stay distinct.
    """
    path = (asset_path or "").replace("\\", "/").strip()
    if not path or path.lower() == "none":
        return ""
    spline_suffix = ""
    marker = path.find(SPLINE_KEY_MARKER)
    if marker >= 0:
        spline_suffix = path[marker:].lower()
        path = path[:marker]
    leaf = path.rsplit("/", 1)[-1]
    if "." in leaf:
        head, _, tail = leaf.rpartition(".")
        if head and (tail.isdigit() or tail.lower() in {"uasset", "umap"} or tail == head):
            path = path[: -len(tail) - 1]
    return path.lower() + spline_suffix


def _asset_key_aliases(asset_path: str) -> list[str]:
    """Normalized keys that should match the same mesh (Game/ vs Pioneer/ prefixes)."""
    base, spline_id = split_spline_asset_key(asset_path)
    want = _normalize_asset_key(base)
    if not want:
        return []
    out = [want]
    # /game/pioneer/... ↔ /pioneer/...
    if want.startswith("/game/pioneer/"):
        out.append("/pioneer/" + want[len("/game/pioneer/") :])
    elif want.startswith("/pioneer/"):
        out.append("/game/pioneer/" + want[len("/pioneer/") :])
    if spline_id:
        # Prefer exact spline keys first, then base (undeformed fallback).
        spline_keys = [f"{k}{SPLINE_KEY_MARKER}{spline_id.lower()}" for k in out]
        out = spline_keys + out
    # Dedup preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for key in out:
        if key not in seen:
            seen.add(key)
            uniq.append(key)
    return uniq


def _is_fmodel_map_export_root(root: str) -> bool:
    """True when root looks like FModel MapPlacements/{Map} (not cooked Content alone)."""
    if not root or not os.path.isdir(root):
        return False
    if os.path.isfile(os.path.join(root, "placements_manifest.json")):
        return True
    if os.path.isdir(os.path.join(root, "PioneerGame", "Content")):
        return True
    if os.path.isdir(os.path.join(root, "Engine", "Content")):
        return True
    if os.path.isdir(os.path.join(root, "Game")):
        return True
    return False


def _asset_export_rel_stems(asset_path: str) -> list[str]:
    """Relative stems matching CUE4Parse GetExportSavePath under a MapPlacements root.

    FModel FixPath writes Arc Raiders meshes as::

        {root}/PioneerGame/Content/Pioneer/.../SM_X.uemodel

    not ``{root}/Game/Pioneer/...``. Also try literal Game/ and Engine/Content forms.
    """
    path = (asset_path or "").replace("\\", "/").strip()
    if not path:
        return []
    leaf = path.rsplit("/", 1)[-1]
    export_name = leaf.split(".", 1)[0]
    pkg = path
    if "." in leaf:
        pkg = path[: -len(leaf.split(".", 1)[-1]) - 1]
    pkg = pkg[1:] if pkg.startswith("/") else pkg
    pkg_leaf = pkg.rsplit("/", 1)[-1]
    if pkg_leaf.lower() == export_name.lower():
        rel = pkg
    else:
        rel = f"{pkg}/{export_name}"

    stems: list[str] = [rel]
    low = rel.lower()
    if low.startswith("game/"):
        rest = rel[5:]  # after Game/
        stems.append(f"PioneerGame/Content/{rest}")
        stems.append(f"Content/{rest}")
        # /Game/Pioneer/... also appears as Content/Pioneer/... under PioneerGame
        if rest.lower().startswith("pioneer/"):
            stems.append(f"PioneerGame/Content/{rest}")
    elif low.startswith("engine/"):
        rest = rel[7:]
        if not rest.lower().startswith("content/"):
            stems.append(f"Engine/Content/{rest}")
    elif low.startswith("pioneer/"):
        stems.append(f"PioneerGame/Content/{rel}")
        stems.append(f"Game/{rel}")
        stems.append(f"Content/{rel}")

    # BP_ → SM_ variants on the leaf only
    out: list[str] = []
    seen: set[str] = set()
    for stem in stems:
        parent, _, name = stem.replace("\\", "/").rpartition("/")
        for alias in _mesh_stem_aliases(name or stem):
            candidate = f"{parent}/{alias}" if parent else alias
            key = candidate.lower()
            if key not in seen:
                seen.add(key)
                out.append(candidate)
    return out


def _fmodel_export_layout_candidates(base: str, asset_path: str) -> list[str]:
    """Paths matching FModel MapPlacements mesh export layout under ``base``.

    Tries both ``Game/...`` and ``PioneerGame/Content/...`` (actual Arc export).
    """
    if not base or not asset_path:
        return []
    out: list[str] = []
    for rel in _asset_export_rel_stems(asset_path):
        rel_os = rel.replace("/", os.sep)
        parent = os.path.join(base, os.path.dirname(rel_os)) if os.path.dirname(rel_os) else base
        stem = os.path.basename(rel_os)
        hit = _find_mesh_file(parent, stem)
        if hit:
            out.append(hit)
        # Nested ExportName/ExportName.uemodel variant
        nested_parent = os.path.join(base, rel_os)
        hit = _find_mesh_file(nested_parent, stem)
        if hit:
            out.append(hit)
        for ext in (".uemodel", ".pskx", ".psk"):
            direct = os.path.join(base, rel_os + ext)
            if os.path.isfile(direct):
                out.append(direct)

    def _rank(p: str) -> int:
        return {".uemodel": 0, ".pskx": 1, ".psk": 2}.get(os.path.splitext(p)[1].lower(), 9)

    uniq: list[str] = []
    seen: set[str] = set()
    for p in sorted(out, key=_rank):
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            uniq.append(os.path.abspath(p))
    return uniq


def _mesh_path_rank(path: str) -> int:
    ext = os.path.splitext(path)[1].lower()
    return {".uemodel": 0, ".pskx": 1, ".psk": 2}.get(ext, 9)


def build_mesh_resolve_lookup(
    mesh_by_asset: dict[str, str] | None = None,
    mesh_exports: list[str] | None = None,
) -> dict[str, Any]:
    """Pre-index manifest paths so Stage 1 resolve is O(1) per asset, not O(n²)."""

    def _prefer(old: str | None, new: str) -> str:
        if not old:
            return new
        return new if _mesh_path_rank(new) < _mesh_path_rank(old) else old

    by_norm: dict[str, str] = {}
    by_leaf: dict[str, str] = {}
    by_leaf_spline: dict[str, str] = {}
    export_by_stem: dict[str, str] = {}

    for key, path in (mesh_by_asset or {}).items():
        if not key or not path:
            continue
        ap = os.path.abspath(str(path))
        if not os.path.isfile(ap):
            continue
        has_spline = SPLINE_KEY_MARKER in key
        for alias in _asset_key_aliases(key):
            by_norm[alias] = _prefer(by_norm.get(alias), ap)
        leaf = key.replace("\\", "/").rsplit("/", 1)[-1].split(".", 1)[0].lower()
        if SPLINE_KEY_MARKER in leaf:
            leaf = leaf.split(SPLINE_KEY_MARKER, 1)[0]
        target = by_leaf_spline if has_spline else by_leaf
        for alias in _mesh_stem_aliases(leaf):
            al = alias.lower()
            target[al] = _prefer(target.get(al), ap)

    for raw in mesh_exports or []:
        path = str(raw or "").strip()
        if not path:
            continue
        ap = os.path.abspath(path)
        if not os.path.isfile(ap):
            continue
        body = os.path.splitext(os.path.basename(ap))[0].lower()
        for alias in _mesh_stem_aliases(body):
            al = alias.lower()
            export_by_stem[al] = _prefer(export_by_stem.get(al), ap)

    return {
        "by_norm": by_norm,
        "by_leaf": by_leaf,
        "by_leaf_spline": by_leaf_spline,
        "export_by_stem": export_by_stem,
    }


def resolve_psk_beside_package(
    pioneer_root: str,
    asset_path: str,
    *,
    extra_roots: list[str] | None = None,
    mesh_by_asset: dict[str, str] | None = None,
    mesh_exports: list[str] | None = None,
    resolve_lookup: dict[str, Any] | None = None,
) -> str | None:
    """
    Resolve a placement asset_path to a UEModel/FModel mesh export on disk.

    Preference order:
      1. Absolute path from FModel manifest mesh_by_asset (normalized keys)
      2. Stem / package match against manifest mesh_exports list
      3. FModel MapPlacements export layout under extra_roots / mesh_export_root
         ({root}/PioneerGame/Content/.../SM_X.uemodel — also tries Game/...)
      4. PioneerGame/Content package-relative search for .uemodel/.psk/.pskx
         (when Pioneer root IS a MapPlacements folder, search PioneerGame/Content)
    """
    if not asset_path:
        return None

    lookup = resolve_lookup
    if lookup is None and (mesh_by_asset or mesh_exports):
        # Slow path callers without a prebuilt index — still avoid O(n²) scans.
        lookup = build_mesh_resolve_lookup(mesh_by_asset, mesh_exports)

    if lookup:
        by_norm = lookup.get("by_norm") or {}
        for ak in _asset_key_aliases(asset_path):
            hit = by_norm.get(ak)
            if hit:
                return hit
        resolve_path = split_spline_asset_key(asset_path)[0] or asset_path
        leaf_base = (
            resolve_path.replace("\\", "/").rsplit("/", 1)[-1].split(".", 1)[0].lower()
        )
        if SPLINE_KEY_MARKER in leaf_base:
            leaf_base = leaf_base.split(SPLINE_KEY_MARKER, 1)[0]
        by_leaf = lookup.get("by_leaf") or {}
        by_leaf_spline = lookup.get("by_leaf_spline") or {}
        export_by_stem = lookup.get("export_by_stem") or {}
        if SPLINE_KEY_MARKER in asset_path:
            for alias in _mesh_stem_aliases(leaf_base):
                hit = by_leaf_spline.get(alias.lower())
                if hit:
                    return hit
            for alias in _mesh_stem_aliases(leaf_base):
                hit = export_by_stem.get(alias.lower())
                if hit:
                    return hit
        else:
            for alias in _mesh_stem_aliases(leaf_base):
                al = alias.lower()
                hit = by_leaf.get(al) or export_by_stem.get(al)
                if hit:
                    return hit
    else:
        # Exact manifest hit for #spline: keys (baked deformed meshes) before aliases.
        if mesh_by_asset and SPLINE_KEY_MARKER in asset_path:
            direct = mesh_by_asset.get(asset_path)
            if direct and os.path.isfile(direct):
                return os.path.abspath(direct)
            for key, path in mesh_by_asset.items():
                if key and path and key.lower() == asset_path.lower() and os.path.isfile(path):
                    return os.path.abspath(path)

        want_keys = set(_asset_key_aliases(asset_path))

        # 1) Manifest mesh_by_asset — exact / normalized / leaf
        if mesh_by_asset:
            for key, path in mesh_by_asset.items():
                if not key or not path:
                    continue
                if _normalize_asset_key(key) in want_keys and os.path.isfile(path):
                    return os.path.abspath(path)
            leaf_base = asset_path.replace("\\", "/").rsplit("/", 1)[-1].split(".", 1)[0].lower()
            if SPLINE_KEY_MARKER in leaf_base:
                leaf_base = leaf_base.split(SPLINE_KEY_MARKER, 1)[0]
            for alias in _mesh_stem_aliases(leaf_base):
                alias_l = alias.lower()
                for key, path in mesh_by_asset.items():
                    if not key or not path or not os.path.isfile(path):
                        continue
                    if SPLINE_KEY_MARKER in asset_path and SPLINE_KEY_MARKER not in key:
                        continue
                    kleaf = key.replace("\\", "/").rsplit("/", 1)[-1].split(".", 1)[0].lower()
                    if kleaf == alias_l:
                        return os.path.abspath(path)

        # 2) mesh_exports absolute list — match by stem
        resolve_path = split_spline_asset_key(asset_path)[0] or asset_path
        if mesh_exports:
            hit = _match_mesh_exports(resolve_path, mesh_exports)
            if hit:
                return hit

    resolve_path = split_spline_asset_key(asset_path)[0] or asset_path

    bases: list[str] = []
    export_bases: list[str] = []
    other_bases: list[str] = []
    for root in list(extra_roots or []) + ([pioneer_root] if pioneer_root else []):
        if not root:
            continue
        abs_root = os.path.abspath(bpy.path.abspath(root))
        if not os.path.isdir(abs_root):
            continue
        # Prefer FModel export roots first so cooked Content doesn't shadow them.
        if _is_fmodel_map_export_root(abs_root):
            if abs_root not in export_bases:
                export_bases.append(abs_root)
        elif abs_root not in other_bases and abs_root not in export_bases:
            other_bases.append(abs_root)
    bases = export_bases + other_bases
    if not bases:
        return None

    # 3) FModel MapPlacements layout (PioneerGame/Content/... and Game/...)
    for base in bases:
        hits = _fmodel_export_layout_candidates(base, resolve_path)
        if hits:
            return hits[0]

    # 4) Classic Content/Pioneer package-relative search
    queue: list[str] = [resolve_path]
    seen_pkg: set[str] = set()

    while queue:
        pkg = queue.pop(0)
        key = pkg.replace("\\", "/").lower()
        if key in seen_pkg:
            continue
        seen_pkg.add(key)

        rel = package_to_content_rel(pkg)
        if not rel:
            continue

        for base in bases:
            for content_base in _content_search_roots(base):
                for folder in _folder_candidates_for_rel(content_base, rel):
                    parent = os.path.dirname(folder)
                    name = os.path.basename(folder)
                    hit = _find_mesh_file(parent, name)
                    if hit:
                        return hit
                    for json_name in (name + ".json",):
                        json_path = os.path.join(parent, json_name)
                        for soft in _soft_sm_packages_from_json(json_path):
                            if soft.replace("\\", "/").lower() not in seen_pkg:
                                queue.append(soft)
                    for alias in _mesh_stem_aliases(name):
                        if alias == name:
                            continue
                        hit = _find_mesh_file(parent, alias)
                        if hit:
                            return hit
    return None


def _match_mesh_exports(asset_path: str, mesh_exports: list[str]) -> str | None:
    """Match asset_path against absolute FModel mesh_exports paths by stem / package leaf."""
    if not asset_path or not mesh_exports:
        return None
    leaf = asset_path.replace("\\", "/").rsplit("/", 1)[-1]
    stem = leaf.split(".", 1)[0].lower()
    aliases = {a.lower() for a in _mesh_stem_aliases(stem)}
    hits: list[str] = []
    for raw in mesh_exports:
        path = str(raw or "").strip()
        if not path or not os.path.isfile(path):
            continue
        body = os.path.splitext(os.path.basename(path))[0].lower()
        if body in aliases or any(a in body or body in a for a in aliases):
            hits.append(os.path.abspath(path))
    if not hits:
        return None

    def _rank(p: str) -> int:
        e = os.path.splitext(p)[1].lower()
        return {".uemodel": 0, ".pskx": 1, ".psk": 2}.get(e, 9)

    hits.sort(key=_rank)
    return hits[0]


def load_placements_manifest(csv_path: str) -> dict[str, Any]:
    """Load placements_manifest.json beside a placements.csv when present."""
    if not csv_path:
        return {}
    folder = os.path.dirname(bpy.path.abspath(csv_path))
    for name in ("placements_manifest.json", "placements_manifest.JSON"):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def placement_mesh_resolve_context(scene, csv_path: str = "") -> dict[str, Any]:
    """Build resolve kwargs from scene Pioneer root + FModel export folder / manifest."""
    csv_path = bpy.path.abspath(csv_path or getattr(scene, "arc_placement_csv", "") or "")
    manifest = load_placements_manifest(csv_path)
    pioneer = bpy.path.abspath(getattr(scene, "arc_pioneer_root", "") or "")
    extra: list[str] = []
    export_root = (manifest.get("mesh_export_root") or "").strip()
    if export_root:
        export_root = bpy.path.abspath(export_root)
        if os.path.isdir(export_root):
            extra.append(export_root)
    if csv_path:
        csv_dir = os.path.dirname(csv_path)
        if csv_dir and os.path.isdir(csv_dir) and csv_dir not in extra:
            extra.append(csv_dir)
    # Scene override: after TCP ingest we may stash the FModel MapPlacements folder.
    scene_mesh_root = bpy.path.abspath(getattr(scene, "arc_placement_mesh_root", "") or "")
    if scene_mesh_root and os.path.isdir(scene_mesh_root) and scene_mesh_root not in extra:
        extra.insert(0, scene_mesh_root)
    mesh_by_asset = manifest.get("mesh_by_asset") or {}
    if not isinstance(mesh_by_asset, dict):
        mesh_by_asset = {}
    mesh_exports = manifest.get("mesh_exports") or []
    if not isinstance(mesh_exports, list):
        mesh_exports = []
    mesh_by_asset_s = {str(k): str(v) for k, v in mesh_by_asset.items() if k and v}
    mesh_exports_s = [str(p) for p in mesh_exports if p]
    return {
        "pioneer_root": pioneer,
        "extra_roots": extra,
        "mesh_by_asset": mesh_by_asset_s,
        "mesh_exports": mesh_exports_s,
        "resolve_lookup": build_mesh_resolve_lookup(mesh_by_asset_s, mesh_exports_s),
        "mesh_export_root": export_root,
        "mesh_export_count": int(manifest.get("mesh_export_count") or len(mesh_exports) or 0),
        "meshes_exported": bool(manifest.get("meshes_exported")) or bool(mesh_exports),
        "world_bounds_path": str(manifest.get("world_bounds_path") or ""),
        "heightmap_image_path": str(manifest.get("heightmap_image_path") or ""),
        "spline_baked_count": int(manifest.get("spline_baked_count") or 0),
    }


def _directory_has_map_meshes(root: str, *, max_dirs: int = 2000) -> bool:
    """True if root contains at least one .uemodel/.psk/.pskx (bounded walk)."""
    if not root or not os.path.isdir(root):
        return False
    seen = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            low = name.lower()
            if low.endswith((".uemodel", ".psk", ".pskx")):
                return True
        seen += 1
        if seen >= max_dirs:
            break
    return False


def _manifest_has_existing_mesh_exports(ctx: dict[str, Any]) -> bool:
    by_asset = ctx.get("mesh_by_asset") or {}
    if isinstance(by_asset, dict):
        for path in by_asset.values():
            if path and os.path.isfile(str(path)):
                return True
    for path in ctx.get("mesh_exports") or []:
        if path and os.path.isfile(str(path)):
            return True
    return False


def _has_mesh_resolve_sources(ctx: dict[str, Any]) -> bool:
    """True when Stage 1 has somewhere to look for mesh files.

    FModel + Meshes writes under mesh_export_root using PioneerGame/Content/... (and
    sometimes Game/...) — that folder alone is enough. PioneerGame Root may be cooked
    content without .uemodel files; we still allow it as a search root, but
    MapPlacements must be present for auto-export.
    """
    if _manifest_has_existing_mesh_exports(ctx):
        return True
    export_root = ctx.get("mesh_export_root") or ""
    if export_root and os.path.isdir(export_root):
        if ctx.get("meshes_exported") or ctx.get("mesh_export_count") or _directory_has_map_meshes(export_root):
            return True
    for root in ctx.get("extra_roots") or []:
        if _directory_has_map_meshes(root):
            return True
    pioneer = ctx.get("pioneer_root") or ""
    # Cooked PioneerGame is valid for package search *if* the user already dumped
    # .uemodel/.psk beside packages. Otherwise they need MapPlacements from + Meshes.
    if pioneer and os.path.isdir(pioneer) and _directory_has_map_meshes(pioneer):
        return True
    return False


def _sample_mesh_files_under(root: str, *, limit: int = 3) -> list[str]:
    """Return up to ``limit`` mesh file paths under root (bounded walk)."""
    if not root or not os.path.isdir(root):
        return []
    found: list[str] = []
    seen_dirs = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            low = name.lower()
            if low.endswith((".uemodel", ".psk", ".pskx")):
                found.append(os.path.join(dirpath, name))
                if len(found) >= limit:
                    return found
        seen_dirs += 1
        if seen_dirs >= 2000:
            break
    return found


def _missing_mesh_source_message(
    ctx: dict[str, Any] | None = None,
    *,
    unresolved_assets: list[str] | None = None,
) -> str:
    ctx = ctx or {}
    export_root = ctx.get("mesh_export_root") or ""
    count = int(ctx.get("mesh_export_count") or 0)
    existing = 0
    for path in (ctx.get("mesh_exports") or []):
        if path and os.path.isfile(str(path)):
            existing += 1
    for path in (ctx.get("mesh_by_asset") or {}).values():
        if path and os.path.isfile(str(path)):
            existing += 1
            break
    bits = [
        "No map mesh files found for Stage 1.",
        "Use FModel → Export Map Placements + Meshes (not CSV only).",
    ]
    if export_root:
        bits.append(f"Expected mesh folder: {export_root}")
        if not os.path.isdir(export_root):
            bits.append("(folder missing)")
        elif count and existing == 0:
            bits.append(
                f"(manifest lists {count} mesh(es) but none exist on disk — "
                "re-run + Meshes or check FModel Model Directory)"
            )
        elif _directory_has_map_meshes(export_root):
            bits.append(
                "(meshes exist under the MapPlacements folder but asset paths did not match — "
                "reload the add-on and retry Stage 1)"
            )
    pioneer = ctx.get("pioneer_root") or ""
    if pioneer:
        if _is_fmodel_map_export_root(pioneer):
            bits.append(
                f"PioneerGame Root points at an FModel MapPlacements export "
                f"(expects PioneerGame/Content/... .uemodel layout): {pioneer}"
            )
        else:
            bits.append(
                f"PioneerGame Root is set but needs .uemodel/.psk/.pskx beside packages "
                f"(cooked .uasset alone is not enough): {pioneer}"
            )
    samples = [a for a in (unresolved_assets or []) if a][:3]
    if samples:
        bits.append("Unresolved asset_path examples: " + " | ".join(samples))
    sample_root = export_root or pioneer
    file_samples = _sample_mesh_files_under(sample_root, limit=3)
    if file_samples:
        bits.append(
            "Example mesh files under root: "
            + " | ".join(os.path.basename(p) for p in file_samples)
            + f" (under {os.path.dirname(file_samples[0])})"
        )
    return " ".join(bits)


def _stage1_zero_mesh_message(
    ctx: dict[str, Any] | None,
    *,
    total_rows: int,
    resolved: int,
    import_failed: int,
    unresolved_assets: list[str] | None = None,
) -> str:
    """Explain Stage 1 finding zero instancers — resolve miss vs import failure."""
    head = f"Stage 1 found 0 meshes for {total_rows} placements."
    if resolved > 0 and import_failed > 0:
        return (
            f"{head} Resolved {resolved} mesh file(s) on disk but import failed for all "
            f"({import_failed} failure(s)). Install/enable the UEFormat Blender add-on for "
            f".uemodel, or re-export Map Placements meshes as PSK/PSKX beside the packages."
        )
    return f"{head} {_missing_mesh_source_message(ctx, unresolved_assets=unresolved_assets)}"


def configure_viewport_for_map(context=None, objects=None) -> int:
    """Raise clip distance and frame the view on map-scale objects.

    Empties/instancers sit far from the origin — Blender's default clip_end
    hides them. Clip limits follow ``map_unit_scale()`` (meters vs legacy cm).
    Prefer :func:`frame_viewport_to_city_center` after import for a useful city view.
    """
    context = context or bpy.context
    framed = 0
    # Prefer provided objects; else anything tagged arc_map / in *_Placements
    targets = list(objects or [])
    if not targets:
        for obj in context.scene.objects:
            if obj.get("arc_map") or obj.get("arc_heightmap_plane"):
                targets.append(obj)
    if not targets:
        return 0

    unit = map_unit_scale(context.scene)
    # ~50 km of map in meters; ~5e6 cm in legacy cm space
    clip_end = max(50_000.0, 5_000_000.0 * unit)
    clip_start = max(0.01, 1.0 * unit)

    # Select targets for view_all / view_selected
    try:
        bpy.ops.object.select_all(action="DESELECT")
    except Exception:
        pass
    for obj in targets:
        try:
            obj.select_set(True)
        except Exception:
            pass
    try:
        context.view_layer.objects.active = targets[0]
    except Exception:
        pass

    for window in context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type != "VIEW_3D":
                    continue
                space.clip_start = clip_start
                # Prefer raising clip_end to map extent; don't shrink an already-huge clip
                space.clip_end = max(space.clip_end, clip_end)
                space.overlay.show_extras = True
                space.overlay.show_relationship_lines = False
            override = {
                "window": window,
                "screen": screen,
                "area": area,
                "region": next((r for r in area.regions if r.type == "WINDOW"), None),
            }
            try:
                with context.temp_override(**{k: v for k, v in override.items() if v is not None}):
                    bpy.ops.view3d.view_selected(use_all_regions=False)
                    framed += 1
            except Exception:
                try:
                    bpy.ops.view3d.view_selected({"area": area})
                    framed += 1
                except Exception:
                    pass
    return framed


def _city_core_center_bu(
    map_name: str = "",
    *,
    csv_path: str = "",
) -> tuple[float, float, float, float] | None:
    """Return (cx, cy, cz, radius) in Blender units for the city placements AABB.

    Prefers ``*_city_core_bounds.json``; falls back to instancer point cloud AABB
    (excluding skybox / helpers / landscape backdrop).
    """
    unit = map_unit_scale()
    mirror = map_mirror_y()
    csv_path = bpy.path.abspath(csv_path or "")
    map_name = _pick_map_name_for_scene(map_name, csv_path=csv_path) or _resolve_map_name(
        map_name
    )

    bounds_path = ""
    folders: list[str] = []
    if csv_path:
        folders.append(os.path.dirname(csv_path))
    for folder in folders:
        for nm in _map_name_aliases(map_name) or [map_name]:
            cand = os.path.join(folder, f"{nm}_city_core_bounds.json")
            if os.path.isfile(cand):
                bounds_path = cand
                break
        if bounds_path:
            break

    if bounds_path:
        try:
            with open(bounds_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            hm = data.get("heightmap") or data
            loc = hm.get("location") or {}
            size = hm.get("size_xy") or [0, 0]
            ox = float(loc.get("x", 0.0))
            oy = float(loc.get("y", 0.0))
            oz = float(loc.get("z", 0.0))
            sx = float(size[0]) if len(size) > 0 else 0.0
            sy = float(size[1]) if len(size) > 1 else 0.0
            if sx > 0 and sy > 0:
                cx_cm = ox + sx * 0.5
                cy_cm = oy + sy * 0.5
                cz_cm = oz
                # MAP_MIRROR_Y: Blender Y = -Unreal Y
                if mirror:
                    cy_cm = -cy_cm
                radius = max(sx, sy) * 0.35 * unit
                return (cx_cm * unit, cy_cm * unit, cz_cm * unit, max(radius, 50.0 * unit))
        except (OSError, json.JSONDecodeError, TypeError, ValueError, IndexError):
            pass

    # Fallback: sample instancer points
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for obj in _iter_map_instancers(map_name):
        asset = str(obj.get("arc_asset_path") or "")
        group = str(obj.get("arc_map_group") or "")
        if group in ("Skybox / Spheres", "Backdrop Landscape") or obj.get("arc_helper_hidden"):
            continue
        if classify_map_group_category(asset, obj.name) == "Skybox / Spheres":
            continue
        if classify_helper_hide_category(asset, obj.name):
            continue
        mesh = obj.data
        if mesh is None or len(mesh.vertices) == 0:
            continue
        # Sample up to 64 points per instancer
        step = max(1, len(mesh.vertices) // 64)
        for i in range(0, len(mesh.vertices), step):
            co = mesh.vertices[i].co
            xs.append(co.x)
            ys.append(co.y)
            zs.append(co.z)
    if len(xs) < 8:
        return None
    xs.sort()
    ys.sort()
    zs.sort()

    def _pct(vals: list[float], p: float) -> float:
        idx = (len(vals) - 1) * p
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        if lo == hi:
            return vals[lo]
        t = idx - lo
        return vals[lo] * (1.0 - t) + vals[hi] * t

    min_x, max_x = _pct(xs, 0.05), _pct(xs, 0.95)
    min_y, max_y = _pct(ys, 0.05), _pct(ys, 0.95)
    min_z, max_z = _pct(zs, 0.05), _pct(zs, 0.95)
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    cz = (min_z + max_z) * 0.5
    radius = max(max_x - min_x, max_y - min_y) * 0.35
    return (cx, cy, cz, max(radius, 50.0 * unit))


def frame_viewport_to_city_center(
    context=None,
    map_name: str = "",
    *,
    csv_path: str = "",
) -> dict[str, Any]:
    """Move 3D views to look at the city AABB center from a useful height/distance."""
    context = context or bpy.context
    stats: dict[str, Any] = {"framed": 0, "center": None}
    center = _city_core_center_bu(map_name, csv_path=csv_path)
    if center is None:
        # Fall back to framing tagged objects
        stats["framed"] = configure_viewport_for_map(context)
        return stats
    cx, cy, cz, radius = center
    stats["center"] = (cx, cy, cz, radius)
    unit = map_unit_scale(context.scene)
    clip_end = max(50_000.0, 5_000_000.0 * unit)
    clip_start = max(0.01, 1.0 * unit)
    # Camera sits above and offset so buildings read in perspective.
    eye = mathutils.Vector((cx + radius * 0.55, cy - radius * 0.85, cz + radius * 0.45))
    target = mathutils.Vector((cx, cy, cz + radius * 0.05))

    for window in context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type != "VIEW_3D":
                    continue
                space.clip_start = clip_start
                space.clip_end = max(space.clip_end, clip_end)
                rv3d = space.region_3d
                if rv3d is None:
                    continue
                try:
                    direction = (target - eye).normalized()
                    # Build view matrix looking from eye toward target (Z up).
                    quat = direction.to_track_quat("-Z", "Y")
                    rv3d.view_rotation = quat
                    rv3d.view_location = target
                    rv3d.view_distance = float(radius * 1.35)
                    rv3d.view_perspective = "PERSP"
                    stats["framed"] += 1
                except Exception:
                    pass
    return stats


def create_unique_placement_from_source(
    source_obj: bpy.types.Object,
    row: dict[str, str],
    *,
    map_name: str,
    collection: bpy.types.Collection,
    mesh_file: str = "",
    index: int = 0,
    stem: str = "",
) -> bpy.types.Object | None:
    """Duplicate SRC mesh datablock into a posed unique object (DecalMesh / poster / plane path)."""
    if source_obj is None or source_obj.type != "MESH" or source_obj.data is None:
        return None
    asset_path = (row.get("asset_path") or "").strip()
    actor = (row.get("actor_name") or "").strip()
    base = stem or os.path.splitext(os.path.basename(mesh_file or asset_path))[0] or "Plane"
    base = re.sub(r"^SRC_", "", base)
    name = (actor or f"{base}_{index:04d}")[:60]
    mesh = source_obj.data.copy()
    mesh.name = f"{name}_Mesh"[:63]
    obj = bpy.data.objects.new(name, mesh)
    loc, euler, scale = csv_row_to_blender_pose(row)
    obj.location = loc
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = euler
    obj.scale = scale
    mf = mesh_file or str(source_obj.get("arc_mesh_file") or "")
    is_decal = is_decal_mesh_asset(asset_path, actor or name)
    is_poster = is_poster_mesh_asset(asset_path, actor or name)
    is_plane = is_plane_mesh_asset(asset_path, actor or name)
    try:
        obj["arc_map"] = map_name
        obj["arc_asset_path"] = asset_path
        obj["arc_mesh_file"] = mf
        obj["arc_psk_path"] = mf
        obj["arc_model_type"] = "map"
        obj["arc_materials_pending"] = 1
        obj["arc_unique_placement"] = 1
        if is_decal:
            obj["arc_decal_mesh"] = 1
        if is_poster:
            obj["arc_poster_mesh"] = 1
        if is_plane:
            obj["arc_plane_mesh"] = 1
        obj["arc_actor_name"] = actor
        kind = (row.get("asset_kind") or "").strip()
        if kind:
            obj["arc_asset_kind"] = kind
        elif actor.startswith("StaticMeshActor"):
            obj["arc_asset_kind"] = "StaticMesh"
        obj["arc_rotation_layout"] = ROTATION_LAYOUT_UE
        obj["arc_map_unit_scale"] = map_unit_scale()
        obj["arc_map_orientation"] = (
            ORIENTATION_MIRROR_Y if map_mirror_y() else ORIENTATION_PASSTHROUGH
        )
        # Per-actor MI hint (BP_WaterPlane_* / BP_Decal_CrackTarmac_* → MI_*)
        try:
            from . import materials as mats_mod

            pref = mats_mod.preferred_mi_hint_from_actor_name(actor, os.path.dirname(mf or ""))
            if pref and mats_mod._preferred_mi_is_single_slot_override(pref):
                obj["arc_preferred_mi"] = pref
        except Exception:
            pass
    except Exception:
        pass
    collection.objects.link(obj)
    return obj


def create_placement_empty(
    row: dict[str, str],
    *,
    map_name: str,
    collection: bpy.types.Collection,
    missing_mesh: bool = False,
) -> bpy.types.Object:
    name = (row.get("actor_name") or "Placement").strip() or "Placement"
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_type = "CUBE"
    empty.empty_display_size = empty_display_size_for_map(meters=5.0)
    loc, euler, scale = csv_row_to_blender_pose(row)
    empty.location = loc
    empty.rotation_mode = "XYZ"
    empty.rotation_euler = euler
    empty.scale = scale
    empty["arc_asset_path"] = row.get("asset_path") or ""
    empty["arc_map"] = map_name
    empty["arc_yaw"] = _safe_float(row.get("yaw"))
    empty["arc_actor_name"] = name
    kind = (row.get("asset_kind") or "").strip()
    if kind:
        empty["arc_asset_kind"] = kind
    elif name.startswith("StaticMeshActor"):
        empty["arc_asset_kind"] = "StaticMesh"
    empty["arc_missing_mesh"] = 1 if missing_mesh else 0
    empty["arc_rotation_layout"] = ROTATION_LAYOUT_UE
    empty["arc_map_unit_scale"] = map_unit_scale()
    empty["arc_map_orientation"] = (
        ORIENTATION_MIRROR_Y if map_mirror_y() else ORIENTATION_PASSTHROUGH
    )
    collection.objects.link(empty)
    return empty


def create_placement_mesh_or_empty(
    row: dict[str, str],
    *,
    map_name: str,
    collection: bpy.types.Collection,
    pioneer_root: str,
    mesh_cache: dict | None = None,
    resolve_cache: dict[str, str | None] | None = None,
    extra_roots: list[str] | None = None,
    mesh_by_asset: dict[str, str] | None = None,
    mesh_exports: list[str] | None = None,
    resolve_lookup: dict[str, Any] | None = None,
) -> tuple[bpy.types.Object, bool]:
    """Returns (root_object, mesh_loaded). Geometry only — materials deferred to Stage 2.

    Each unique mesh file is parsed once per Stage 1 run. Repeated placements create
    linked Blender objects sharing the imported mesh datablocks; importing the same
    PSK/UEModel hundreds of times was the dominant map-import cost.
    """
    asset_path = row.get("asset_path") or ""
    if resolve_cache is not None and asset_path in resolve_cache:
        mesh_file = resolve_cache[asset_path]
    else:
        mesh_file = resolve_psk_beside_package(
            pioneer_root,
            asset_path,
            extra_roots=extra_roots,
            mesh_by_asset=mesh_by_asset,
            mesh_exports=mesh_exports,
            resolve_lookup=resolve_lookup,
        ) if asset_path else None
        if resolve_cache is not None:
            resolve_cache[asset_path] = mesh_file

    if mesh_file:
        cache_key = os.path.normcase(os.path.abspath(mesh_file))
        cached = mesh_cache.get(cache_key) if mesh_cache is not None else None
        unique_decal = needs_unique_mesh_placement(asset_path)
        if cached is False:
            new_objs = []
            model_type = "unknown"
        elif cached:
            new_objs = []
            for part in cached["parts"]:
                data = part["data"]
                if unique_decal and data is not None:
                    data = data.copy()
                new_objs.append(bpy.data.objects.new(part["name"], data))
            model_type = cached["model_type"]
        else:
            new_objs = _import_map_mesh(mesh_file)
            if new_objs:
                # Stage 1: skip outfit detect_model_type (folder walks); Stage 2 materials
                # do not depend on clothing/face classification for map props.
                model_type = "map"
                if mesh_cache is not None:
                    mesh_cache[cache_key] = {
                        "model_type": model_type,
                        "parts": [
                            {"name": obj.name, "data": obj.data}
                            for obj in new_objs
                            if getattr(obj, "data", None) is not None
                        ],
                    }
                # First placement of a unique plane/decal also gets a unique datablock
                # copy so the cached template stays shared for later duplicates.
                if unique_decal:
                    copied = []
                    for obj in new_objs:
                        if obj.data is None:
                            copied.append(obj)
                            continue
                        mesh = obj.data.copy()
                        dup = bpy.data.objects.new(obj.name, mesh)
                        copied.append(dup)
                        try:
                            bpy.data.objects.remove(obj, do_unlink=True)
                        except Exception:
                            pass
                    new_objs = copied
            else:
                model_type = "unknown"
                if mesh_cache is not None:
                    # Negative-cache broken/missing importers as well. Retrying a failed
                    # UEModel/PSK for every placement is just as expensive as reimporting it.
                    mesh_cache[cache_key] = False

        if new_objs:
            # Parent under an empty at the placement pose for a stable transform root
            root = create_placement_empty(row, map_name=map_name, collection=collection, missing_mesh=False)
            root.empty_display_type = "ARROWS"
            try:
                root["arc_mesh_file"] = mesh_file
                root["arc_psk_path"] = mesh_file
                root["arc_model_type"] = model_type
                root["arc_materials_pending"] = 1
            except Exception:
                pass
            actor = (row.get("actor_name") or "").strip()
            for obj in new_objs:
                try:
                    for coll in list(obj.users_collection):
                        coll.objects.unlink(obj)
                    collection.objects.link(obj)
                except Exception:
                    pass
                # Inherit the empty's world pose: local identity under the placement root.
                # Do NOT set matrix_parent_inverse to root.world.inverted() — that keeps
                # the mesh at world origin while only the empty moves.
                obj.parent = root
                obj.matrix_parent_inverse.identity()
                obj.location = (0.0, 0.0, 0.0)
                obj.rotation_mode = "XYZ"
                obj.rotation_euler = (0.0, 0.0, 0.0)
                obj.scale = (1.0, 1.0, 1.0)
                try:
                    obj["arc_asset_path"] = asset_path
                    obj["arc_map"] = map_name
                    obj["arc_actor_name"] = actor
                    obj["arc_mesh_file"] = mesh_file
                    obj["arc_psk_path"] = mesh_file
                    obj["arc_model_type"] = model_type
                    # Stage 1: geometry only — Stage 2 applies materials
                    obj["arc_materials_pending"] = 1
                    if unique_decal:
                        obj["arc_unique_placement"] = 1
                        if is_decal_mesh_asset(asset_path, actor):
                            obj["arc_decal_mesh"] = 1
                        if is_poster_mesh_asset(asset_path, actor):
                            obj["arc_poster_mesh"] = 1
                        if is_plane_mesh_asset(asset_path, actor):
                            obj["arc_plane_mesh"] = 1
                        try:
                            from . import materials as mats_mod

                            pref = mats_mod.preferred_mi_hint_from_actor_name(
                                actor, os.path.dirname(mesh_file or ""),
                            )
                            if pref and mats_mod._preferred_mi_is_single_slot_override(pref):
                                obj["arc_preferred_mi"] = pref
                        except Exception:
                            pass
                except Exception:
                    pass
            root["arc_missing_mesh"] = 0
            return root, True
    empty = create_placement_empty(row, map_name=map_name, collection=collection, missing_mesh=True)
    return empty, False


def normalize_mesh_lookup_stem(name: str) -> str:
    """Strip Blender / UMap / PTS_ noise so SM_* stems match export basenames.

    Someone else's untextured map often has mesh datablocks like
    ``PTS_SM_Foo.002`` while object names stay ``SM_Foo``.
    """
    n = (name or "").strip()
    if not n:
        return ""
    n = re.sub(r"\.mat$", "", n, flags=re.IGNORECASE)
    n = re.sub(r"\.\d+$", "", n)
    n = re.sub(r"_[0-9a-f]{8}$", "", n, flags=re.IGNORECASE)
    n = re.sub(r"-[0-9A-Fa-f]{4,10}$", "", n)
    n = re.sub(r"_LOD\d+$", "", n, flags=re.IGNORECASE)
    if n.upper().startswith("PTS_"):
        n = n[4:]
    if n.upper().startswith("SRC_"):
        n = n[4:]
    return n.strip()


_PLACEHOLDER_MESH_STEMS = frozenset(
    {
        "cube",
        "cylinder",
        "plane",
        "sphere",
        "ico_sphere",
        "uv_sphere",
        "1m_cube",
        "suzanne",
        "monkey",
    }
)

# Stem → export path (or "") so 600+ name-apply resolves never re-walk Pioneer.
_STEM_ASSET_PATH_CACHE: dict[str, str] = {}
# One-shot basename index for last-resort Pioneer lookup (json/uemodel/psk).
_PIONEER_MESH_BASENAME_INDEX: dict[str, str] | None = None


def clear_map_name_resolve_caches() -> None:
    """Drop stem→path + Pioneer basename index (after Pioneer root change)."""
    global _PIONEER_MESH_BASENAME_INDEX
    _STEM_ASSET_PATH_CACHE.clear()
    _PIONEER_MESH_BASENAME_INDEX = None


def _ensure_pioneer_mesh_basename_index() -> dict[str, str]:
    """Build stem.lower() → preferred mesh/json path under Pioneer/ once."""
    global _PIONEER_MESH_BASENAME_INDEX
    if _PIONEER_MESH_BASENAME_INDEX is not None:
        return _PIONEER_MESH_BASENAME_INDEX
    index: dict[str, str] = {}
    try:
        root = utils.get_pioneer_root()
        content = utils.find_content_dir(root) if root else ""
        pioneer = os.path.join(content, "Pioneer") if content else ""
        if not pioneer or not os.path.isdir(pioneer):
            _PIONEER_MESH_BASENAME_INDEX = index
            return index
        skip_segs = (
            "/characters/",
            "/heroes/",
            "/outfits/",
            "/saved/",
            "/intermediate/",
        )
        for walk_root, dirs, files in os.walk(pioneer):
            low = walk_root.replace("\\", "/").lower()
            if any(s in low for s in skip_segs):
                dirs[:] = []
                continue
            for fname in files:
                fl = fname.lower()
                if not (
                    fl.endswith(".json")
                    or fl.endswith(".uemodel")
                    or fl.endswith(".psk")
                    or fl.endswith(".pskx")
                ):
                    continue
                stem = os.path.splitext(fname)[0].lower()
                full = os.path.join(walk_root, fname)
                prev = index.get(stem)
                # Prefer json (StaticMaterials), then containers
                if prev is None:
                    index[stem] = full
                elif fl.endswith(".json") and not prev.lower().endswith(".json"):
                    index[stem] = full
                elif (
                    fl.endswith((".uemodel", ".psk", ".pskx"))
                    and prev.lower().endswith(".json")
                ):
                    pass
                elif fl.endswith(".uemodel") and prev.lower().endswith(
                    (".psk", ".pskx")
                ):
                    index[stem] = full
    except OSError as exc:
        print(f"Arc Raiders: Pioneer mesh index failed: {exc}")
    _PIONEER_MESH_BASENAME_INDEX = index
    print(f"Arc Raiders: Pioneer mesh basename index ({len(index)} stems)")
    return index


def _path_from_pioneer_index_hit(full: str) -> str:
    """Normalize an index hit to a mesh path (or synthetic .psk beside JSON)."""
    if not full or not os.path.isfile(full):
        return ""
    fl = full.lower()
    if fl.endswith(".json"):
        base = os.path.splitext(full)[0]
        for mesh_ext in (".uemodel", ".psk", ".pskx"):
            mesh_path = base + mesh_ext
            if os.path.isfile(mesh_path):
                return mesh_path
        return base + ".psk"
    return full


def mesh_lookup_stems_for_object(obj) -> list[str]:
    """Candidate UE mesh stems from object / mesh datablock / parent names."""
    if obj is None:
        return []
    raws: list[str] = []
    try:
        raws.append(obj.name)
    except ReferenceError:
        return []
    data = getattr(obj, "data", None)
    if data is not None:
        raws.append(getattr(data, "name", "") or "")
    parent = getattr(obj, "parent", None)
    if parent is not None:
        raws.append(getattr(parent, "name", "") or "")

    stems: list[str] = []
    seen: set[str] = set()

    def _add(stem: str) -> None:
        stem = normalize_mesh_lookup_stem(stem)
        if not stem:
            return
        key = stem.lower()
        if key in seen:
            return
        seen.add(key)
        stems.append(stem)

    for raw in raws:
        _add(raw)
        # Pull embedded SM_/SK_ tokens out of longer actor-style names
        for m in re.finditer(
            r"((?:SK|SM)_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)",
            raw or "",
            flags=re.IGNORECASE,
        ):
            _add(m.group(1))

    # Prefer SM_/SK_ stems first for map MI resolution
    preferred = [
        s for s in stems
        if s.upper().startswith(("SM_", "SK_"))
        and s.lower() not in _PLACEHOLDER_MESH_STEMS
    ]
    rest = [
        s for s in stems
        if s not in preferred and s.lower() not in _PLACEHOLDER_MESH_STEMS
    ]
    return preferred + rest


def resolve_map_asset_path_by_stem(stem: str) -> str:
    """Resolve SM_/SK_ stem → mesh/JSON path usable by Stage 2 / setup_map_material.

    Prefers Content ``*.json`` (StaticMaterials) over MapPlacements ``.uemodel``-only
    trees. Returns a real ``.uemodel``/``.psk`` when present, else a synthetic
    ``.psk`` beside the SM JSON so ``_parse_sk_material_slots`` still works.
    """
    stem = normalize_mesh_lookup_stem(stem)
    if not stem or stem.lower() in _PLACEHOLDER_MESH_STEMS:
        return ""
    cache_key = stem.lower()
    if cache_key in _STEM_ASSET_PATH_CACHE:
        return _STEM_ASSET_PATH_CACHE[cache_key]

    from . import fmdex
    from . import operators as ops

    try:
        fmdex.ensure_loaded()
    except Exception:
        pass

    found_path = ""

    # JSON first (authoritative StaticMaterials), then mesh containers
    for ext in (".json", ".uemodel", ".psk", ".pskx"):
        try:
            found = fmdex.resolve_export_file(
                stem, ext, context="map", allow_basename_walk=False,
            )
        except TypeError:
            found = fmdex.resolve_export_file(stem, ext)
        except Exception:
            found = ""
        if not found or not os.path.isfile(found):
            continue
        if ext == ".json":
            base = os.path.splitext(found)[0]
            for mesh_ext in (".uemodel", ".psk", ".pskx"):
                mesh_path = base + mesh_ext
                if os.path.isfile(mesh_path):
                    found_path = mesh_path
                    break
            else:
                found_path = base + ".psk"
        else:
            found_path = found
        break

    # Broader outfit/map finder (PSK / synthetic JSON path)
    if not found_path:
        try:
            hit = ops.find_psk_for_model(stem)
        except Exception:
            hit = ""
        if hit:
            found_path = hit

    # Last resort: one-shot Pioneer basename index (never os.walk per stem)
    if not found_path:
        try:
            idx = _ensure_pioneer_mesh_basename_index()
            hit = idx.get(cache_key) or ""
            found_path = _path_from_pioneer_index_hit(hit)
        except Exception:
            found_path = ""

    _STEM_ASSET_PATH_CACHE[cache_key] = found_path or ""
    return found_path or ""


def resolve_map_asset_path_for_object(obj) -> tuple[str, str]:
    """Return ``(asset_path, matched_stem)`` for an unstamped map mesh."""
    stamped = ""
    if obj is not None and hasattr(obj, "get"):
        for key in ("arc_psk_path", "arc_mesh_file"):
            raw = obj.get(key) or ""
            if raw:
                stamped = bpy.path.abspath(str(raw))
                if os.path.isfile(stamped) or os.path.isdir(os.path.dirname(stamped)):
                    return stamped, key
                stamped = ""
    for stem in mesh_lookup_stems_for_object(obj):
        hit = resolve_map_asset_path_by_stem(stem)
        if hit:
            return hit, stem
    return "", ""


def collect_meshes_from_collection(collection_name: str) -> list:
    """Recursive mesh objects under a collection (by name). Unique by datablock."""
    name = (collection_name or "").strip()
    if not name:
        return []
    col = bpy.data.collections.get(name)
    if col is None:
        return []
    meshes: list = []
    seen: set = set()

    def _walk(c):
        for obj in c.objects:
            if obj.type != "MESH":
                continue
            if obj.get("arc_placement_instancer") or obj.get("arc_heightmap_plane"):
                continue
            key = obj.data.as_pointer() if obj.data else obj.as_pointer()
            if key in seen:
                continue
            seen.add(key)
            meshes.append(obj)
        for ch in c.children:
            _walk(ch)

    _walk(col)
    return meshes


def collect_map_mesh_targets(
    context,
    map_name: str = "",
    *,
    only_selected: bool = False,
) -> list:
    """Unique mesh datablocks stamped by Stage 1 for Stage 2 materials.

    Stage 1 uses linked mesh data for repeated map assets. Rebuilding materials once
    per datablock updates every linked placement and avoids repeating identical work.

    By default scans the whole scene for the map (selection must not shrink Stage 2
    to a handful of leftover SRC meshes). Pass ``only_selected=True`` for a subset.
    """
    scene = context.scene
    map_name = (map_name or "").strip()
    if not map_name or map_name == "NONE":
        map_name = (getattr(scene, "arc_placement_map", "") or "").strip()
    if not map_name or map_name == "NONE":
        map_name = (getattr(scene, "arc_placement_map_name", "") or "").strip()

    if only_selected:
        selected = [
            o for o in context.selected_objects
            if o.type == "MESH" and o.get("arc_map") and not o.get("arc_placement_instancer")
        ]
        if selected:
            if map_name:
                filtered = [o for o in selected if o.get("arc_map") == map_name]
                if filtered:
                    selected = filtered
            unique = {}
            for obj in selected:
                key = obj.data.as_pointer() if obj.data else obj.as_pointer()
                unique.setdefault(key, obj)
            return list(unique.values())

    meshes = []
    seen_data = set()
    # Prefer bpy.data.objects — InstanceSources may be eye-hidden / not in view layer.
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        if obj.get("arc_heightmap_plane"):
            continue
        # Instancer point clouds carry arc_map but hold no shadeable geometry —
        # their materials come from the source meshes they instance.
        if obj.get("arc_placement_instancer"):
            continue
        tagged_map = obj.get("arc_map") or ""
        if map_name and tagged_map != map_name:
            continue
        if not tagged_map and not obj.get("arc_psk_path") and not obj.get("arc_mesh_file"):
            continue
        data_key = obj.data.as_pointer() if obj.data else obj.as_pointer()
        if data_key in seen_data:
            continue
        seen_data.add(data_key)
        meshes.append(obj)
    return meshes


def _map_mesh_has_arc_materials(obj) -> bool:
    """True when at least one slot already has a Stage-2 shared MI stamp."""
    if not obj or obj.type != "MESH":
        return False
    for slot in obj.material_slots:
        mat = slot.material
        if mat is not None and str(mat.get("arc_mi_path", "") or "").strip():
            return True
    return False


INSTANCER_NODE_GROUP = "ARC_MapInstancer_v2"
ATTR_ROTATION = "arc_rotation"
ATTR_SCALE = "arc_scale"


def _clear_node_group_interface(ng: bpy.types.NodeTree) -> None:
    items = list(getattr(ng.interface, "items_tree", []) or [])
    for item in items:
        try:
            ng.interface.remove(item)
        except Exception:
            pass


def _node_group_input_identifier(node_group: bpy.types.NodeTree, name: str) -> str | None:
    for item in node_group.interface.items_tree:
        item_type = getattr(item, "item_type", "SOCKET")
        if item_type and item_type != "SOCKET":
            continue
        if getattr(item, "in_out", "") == "INPUT" and item.name == name:
            return item.identifier
    return None


def _set_modifier_socket(modifier, socket_name: str, value) -> bool:
    """Assign a geometry-nodes modifier input, trying identifier then name fallbacks."""
    ng = modifier.node_group
    if ng is None:
        return False
    identifier = _node_group_input_identifier(ng, socket_name)
    candidates = []
    if identifier:
        candidates.append(identifier)
    candidates.append(socket_name)
    for key in candidates:
        try:
            modifier[key] = value
            return True
        except Exception:
            continue
    return False


def ensure_map_instancer_node_group() -> bpy.types.NodeTree:
    """Shared geometry-nodes tree: instance a source object onto placement points.

    Uses Object Info → Geometry (not As Instance). As Instance + hidden/bounds
    sources often evaluate to empty in Blender 4/5, which leaves only MissingPlacements
    visible because instancers hide their raw points.
    """
    existing = bpy.data.node_groups.get(INSTANCER_NODE_GROUP)
    # Always rebuild if the group still has As Instance enabled — that path is broken
    # for map sources drawn as bounds / hide_render.
    if existing is not None and existing.bl_idname == "GeometryNodeTree":
        obj_info = existing.nodes.get("Object Info")
        as_inst = False
        try:
            if obj_info is not None:
                as_inst = bool(obj_info.inputs["As Instance"].default_value)
        except Exception:
            as_inst = False
        if (
            _node_group_input_identifier(existing, "Source")
            and obj_info is not None
            and not as_inst
            and existing.get("arc_instancer_v") == 2
        ):
            return existing
        bpy.data.node_groups.remove(existing)

    # Drop the original Collection-Info based group if it is still around.
    legacy = bpy.data.node_groups.get("ARC_MapInstancer")
    if legacy is not None:
        try:
            bpy.data.node_groups.remove(legacy)
        except Exception:
            pass

    ng = bpy.data.node_groups.new(INSTANCER_NODE_GROUP, "GeometryNodeTree")
    ng["arc_instancer_v"] = 2
    _clear_node_group_interface(ng)
    iface = ng.interface
    iface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    iface.new_socket("Source", in_out="INPUT", socket_type="NodeSocketObject")
    iface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    nodes = ng.nodes
    links = ng.links
    nodes.clear()

    group_in = nodes.new("NodeGroupInput")
    group_in.location = (-700, 0)
    group_out = nodes.new("NodeGroupOutput")
    group_out.location = (420, 0)

    obj_info = nodes.new("GeometryNodeObjectInfo")
    obj_info.name = "Object Info"
    obj_info.location = (-420, -160)
    obj_info.transform_space = "ORIGINAL"
    try:
        # Real geometry — As Instance frequently yields empty for bounds/hide_render sources.
        obj_info.inputs["As Instance"].default_value = False
    except Exception:
        pass

    rot_attr = nodes.new("GeometryNodeInputNamedAttribute")
    rot_attr.location = (-700, -360)
    rot_attr.data_type = "FLOAT_VECTOR"
    rot_attr.inputs["Name"].default_value = ATTR_ROTATION

    scale_attr = nodes.new("GeometryNodeInputNamedAttribute")
    scale_attr.location = (-700, -520)
    scale_attr.data_type = "FLOAT_VECTOR"
    scale_attr.inputs["Name"].default_value = ATTR_SCALE

    instancer = nodes.new("GeometryNodeInstanceOnPoints")
    instancer.location = (100, 0)

    links.new(group_in.outputs["Geometry"], instancer.inputs["Points"])
    links.new(group_in.outputs["Source"], obj_info.inputs["Object"])
    geo_out = obj_info.outputs.get("Geometry") or obj_info.outputs[0]
    links.new(geo_out, instancer.inputs["Instance"])

    rot_socket = rot_attr.outputs["Attribute"]
    try:
        euler_to_rot = nodes.new("FunctionNodeEulerToRotation")
        euler_to_rot.location = (-240, -360)
        links.new(rot_socket, euler_to_rot.inputs[0])
        rot_socket = euler_to_rot.outputs[0]
    except RuntimeError:
        pass
    links.new(rot_socket, instancer.inputs["Rotation"])
    links.new(scale_attr.outputs["Attribute"], instancer.inputs["Scale"])
    links.new(instancer.outputs["Instances"], group_out.inputs["Geometry"])
    return ng


def build_placement_point_cloud(
    rows: list[dict[str, str]],
    *,
    name: str,
    map_name: str,
) -> bpy.types.Object:
    """One vertex per placement, with rotation/scale stored as point attributes."""
    coords: list[float] = []
    rotations: list[float] = []
    scales: list[float] = []
    unit = map_unit_scale()
    mirror = map_mirror_y()
    for row in rows:
        loc, euler, scale = csv_row_to_blender_pose(row, unit=unit, mirror_y=mirror)
        coords.extend(loc)
        rotations.extend(euler)
        scales.extend(scale)

    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(rows))
    mesh.vertices.foreach_set("co", coords)
    rot_attr = mesh.attributes.new(ATTR_ROTATION, "FLOAT_VECTOR", "POINT")
    rot_attr.data.foreach_set("vector", rotations)
    scale_attr = mesh.attributes.new(ATTR_SCALE, "FLOAT_VECTOR", "POINT")
    scale_attr.data.foreach_set("vector", scales)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    obj["arc_map"] = map_name
    obj["arc_placement_instancer"] = 1
    obj["arc_placement_count"] = len(rows)
    obj["arc_rotation_layout"] = ROTATION_LAYOUT_UE
    obj["arc_map_unit_scale"] = unit
    obj["arc_map_orientation"] = (
        ORIENTATION_MIRROR_Y if mirror else ORIENTATION_PASSTHROUGH
    )
    return obj


def _remap_euler_to_ue_xyz(
    ex: float, ey: float, ez: float, *, layout: str
) -> tuple[float, float, float] | None:
    """Remap a stored XYZ euler into (-roll, -pitch, yaw). None = already correct."""
    if layout == ROTATION_LAYOUT_UE:
        return None
    if layout == ROTATION_LAYOUT_RPY:
        # (roll, pitch, yaw) → (-roll, -pitch, yaw)
        return (-ex, -ey, ez)
    # Legacy / unknown: treat as (pitch, yaw, roll)
    return (-ez, -ex, ey)


def _angle_abs_diff(a: float, b: float) -> float:
    return abs((a - b + math.pi) % (2.0 * math.pi) - math.pi)


def _euler_nearly(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    *,
    eps: float = 1e-3,
) -> bool:
    return all(_angle_abs_diff(x, y) <= eps for x, y in zip(a, b))


def _placement_row_key(row: dict[str, str], *, pos_nd: int = 1, ang_nd: int = 2) -> tuple:
    """Stable key for exact duplicate placement rows (asset + near-identical transform)."""
    return (
        (row.get("asset_path") or "").strip(),
        round(_safe_float(row.get("x")), pos_nd),
        round(_safe_float(row.get("y")), pos_nd),
        round(_safe_float(row.get("z")), pos_nd),
        round(_safe_float(row.get("pitch")), ang_nd),
        round(_safe_float(row.get("yaw")), ang_nd),
        round(_safe_float(row.get("roll")), ang_nd),
        round(_safe_float(row.get("scale_x"), 1.0), 3),
        round(_safe_float(row.get("scale_y"), 1.0), 3),
        round(_safe_float(row.get("scale_z"), 1.0), 3),
    )


def dedupe_placement_rows(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    """Drop exact duplicate CSV rows (same asset_path + near-identical transform).

    Safe for StaticMesh/ISM double-emission and repeated Stage 1 CSV rows. Does **not**
    invent offsets for distinct assets wrongly stacked at an actor root (that needs a
    better FModel transform export). Also keeps one SM_Landscape cell — the pose closest
    to the WP grid corner (fixes Buried City x2_y3 double instance → ``_x2`` naming).
    """
    seen: set[tuple] = set()
    out: list[dict[str, str]] = []
    dropped = 0
    for row in rows:
        key = _placement_row_key(row)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(row)
    out, land_dropped = dedupe_landscape_placement_rows(out)
    return out, dropped + land_dropped


def dedupe_landscape_placement_rows(
    rows: list[dict[str, str]],
    *,
    cell_size_cm: float | None = None,
) -> tuple[list[dict[str, str]], int]:
    """One CSV row per SM_Landscape_x*_y* cell — nearest to (x*cell, y*cell, 0)."""
    if cell_size_cm is None:
        cell_size_cm = LANDSCAPE_CELL_SIZE_CM
    best: dict[tuple[int, int], tuple[float, dict[str, str]]] = {}
    for row in rows:
        xy = _parse_landscape_cell(row.get("asset_path") or "")
        if xy is None:
            continue
        cx, cy = xy
        gx = cx * cell_size_cm
        gy = cy * cell_size_cm
        dx = _safe_float(row.get("x")) - gx
        dy = _safe_float(row.get("y")) - gy
        dz = _safe_float(row.get("z"))
        dist_sq = dx * dx + dy * dy + dz * dz
        cur = best.get(xy)
        if cur is None or dist_sq < cur[0]:
            best[xy] = (dist_sq, row)
    kept_land = [t[1] for t in best.values()]
    dropped = sum(
        1
        for row in rows
        if _parse_landscape_cell(row.get("asset_path") or "") is not None
    ) - len(kept_land)
    # Preserve relative order: non-landscape in place, landscape best rows at first sighting.
    if dropped <= 0:
        return rows, 0
    seen_cells: set[tuple[int, int]] = set()
    out: list[dict[str, str]] = []
    land_by_cell = {xy: row for xy, (_, row) in best.items()}
    for row in rows:
        xy = _parse_landscape_cell(row.get("asset_path") or "")
        if xy is None:
            out.append(row)
            continue
        if xy in seen_cells:
            continue
        seen_cells.add(xy)
        out.append(land_by_cell[xy])
    return out, dropped


def detect_instancer_rotation_layout(
    obj: bpy.types.Object,
    rows_by_asset: dict[str, list[dict[str, str]]] | None = None,
    *,
    sample_limit: int = 24,
) -> str:
    """Guess stored euler layout by comparing point attributes to CSV Pitch/Yaw/Roll."""
    if not obj.get("arc_placement_instancer") or obj.type != "MESH" or not obj.data:
        return str(obj.get("arc_rotation_layout") or ROTATION_LAYOUT_UE)
    tagged = str(obj.get("arc_rotation_layout") or "")
    rot_attr = obj.data.attributes.get(ATTR_ROTATION)
    if rot_attr is None or not rows_by_asset:
        return tagged or ROTATION_LAYOUT_UE

    asset = (obj.get("arc_asset_path") or "").strip()
    cands = list(rows_by_asset.get(asset) or [])
    if not cands:
        return tagged or ROTATION_LAYOUT_UE

    votes = {ROTATION_LAYOUT_UE: 0, ROTATION_LAYOUT_LEGACY_PYR: 0, ROTATION_LAYOUT_RPY: 0}
    checked = 0
    for i, vert in enumerate(obj.data.vertices):
        if checked >= sample_limit:
            break
        stored = rot_attr.data[i].vector
        if abs(stored.x) + abs(stored.y) + abs(stored.z) < 1e-5:
            continue
        co = vert.co
        best = None
        best_d = 1e30
        for row in cands:
            loc, _, _ = csv_row_to_blender_pose(row)
            dx = loc[0] - co.x
            dy = loc[1] - co.y
            dz = loc[2] - co.z
            d = dx * dx + dy * dy + dz * dz
            if d < best_d:
                best_d = d
                best = row
        # Position epsilon in Blender units (~100 m² when meters; ~1e4 cm² legacy)
        pos_eps = (100.0 * map_unit_scale()) ** 2
        if best is None or best_d > max(10000.0, pos_eps * 100.0):
            continue
        p = _safe_float(best.get("pitch"))
        y = _safe_float(best.get("yaw"))
        r = _safe_float(best.get("roll"))
        _, ue, _ = csv_row_to_blender_pose(best)
        # Legacy layouts were stored before Y-mirror; compare raw pass-through forms.
        pyr = (math.radians(p), math.radians(y), math.radians(r))
        rpy = (math.radians(r), math.radians(p), math.radians(y))
        if map_mirror_y():
            pyr = apply_mirror_y_to_pose((0, 0, 0), pyr, (1, 1, 1))[1]
            rpy = apply_mirror_y_to_pose((0, 0, 0), rpy, (1, 1, 1))[1]
        s = (stored.x, stored.y, stored.z)
        if _euler_nearly(s, ue):
            votes[ROTATION_LAYOUT_UE] += 1
        elif _euler_nearly(s, pyr):
            votes[ROTATION_LAYOUT_LEGACY_PYR] += 1
        elif _euler_nearly(s, rpy):
            votes[ROTATION_LAYOUT_RPY] += 1
        checked += 1

    if checked <= 0:
        return tagged or ROTATION_LAYOUT_UE
    winner = max(votes, key=votes.get)
    if votes[winner] <= 0:
        return tagged or ROTATION_LAYOUT_UE
    return winner


def fix_legacy_placement_rotations(
    objects=None,
    *,
    force: bool = False,
    assume_layout: str = "",
    csv_path: str = "",
) -> tuple[int, int, dict[str, int]]:
    """Remap buggy placement eulers to Unreal pass-through (-roll, -pitch, yaw).

    Returns (instancers_fixed, empties_fixed, stats).
    Skips objects already tagged ue_xyz unless ``force`` or CSV detection says otherwise.
    """
    if objects is None:
        objects = bpy.data.objects

    rows_by_asset: dict[str, list[dict[str, str]]] = {}
    if csv_path and os.path.isfile(csv_path):
        for row in load_placements_csv(csv_path):
            asset = (row.get("asset_path") or "").strip()
            if asset:
                rows_by_asset.setdefault(asset, []).append(row)

    instancers = 0
    empties = 0
    stats = {
        "skipped_ue_xyz": 0,
        "detected_pyr": 0,
        "detected_rpy": 0,
        "forced": 0,
        "already_correct": 0,
    }

    for obj in objects:
        tagged = str(obj.get("arc_rotation_layout") or "")
        layout = (assume_layout or tagged or ROTATION_LAYOUT_LEGACY_PYR).strip()

        if obj.get("arc_placement_instancer") and obj.type == "MESH" and obj.data:
            if rows_by_asset and not assume_layout:
                detected = detect_instancer_rotation_layout(obj, rows_by_asset)
                if detected == ROTATION_LAYOUT_LEGACY_PYR:
                    stats["detected_pyr"] += 1
                elif detected == ROTATION_LAYOUT_RPY:
                    stats["detected_rpy"] += 1
                layout = detected

            if layout == ROTATION_LAYOUT_UE and not force:
                stats["skipped_ue_xyz"] += 1
                continue
            if force and tagged == ROTATION_LAYOUT_UE and layout == ROTATION_LAYOUT_UE:
                # Force with no alternate layout would re-apply identity — skip.
                stats["already_correct"] += 1
                continue
            if force:
                stats["forced"] += 1

            rot_attr = obj.data.attributes.get(ATTR_ROTATION)
            if rot_attr is None:
                continue
            n = len(obj.data.vertices)
            if n <= 0:
                continue
            vals: list[float] = []
            changed = False
            for i in range(n):
                v = rot_attr.data[i].vector
                remapped = _remap_euler_to_ue_xyz(v.x, v.y, v.z, layout=layout)
                if remapped is None:
                    vals.extend((v.x, v.y, v.z))
                else:
                    vals.extend(remapped)
                    changed = True
            if changed:
                rot_attr.data.foreach_set("vector", vals)
                obj.data.update()
                obj.update_tag()
                instancers += 1
            else:
                stats["already_correct"] += 1
            obj["arc_rotation_layout"] = ROTATION_LAYOUT_UE
            continue

        # Placement empties / missing-mesh markers from the empties or slow path.
        if obj.type == "EMPTY" and obj.get("arc_map") is not None and "arc_yaw" in obj:
            if layout == ROTATION_LAYOUT_UE and not force:
                stats["skipped_ue_xyz"] += 1
                continue
            e = obj.rotation_euler
            remapped = _remap_euler_to_ue_xyz(e.x, e.y, e.z, layout=layout)
            if remapped is not None:
                obj.rotation_mode = "XYZ"
                obj.rotation_euler = remapped
                empties += 1
            else:
                stats["already_correct"] += 1
            obj["arc_rotation_layout"] = ROTATION_LAYOUT_UE

    return instancers, empties, stats


# Near-duplicate pose bucketing (Unreal cm / Blender units).
# Default catches float noise without merging intentional nearby props.
DEFAULT_DEDUP_POS_EPS = 0.05
DEFAULT_DEDUP_ANG_EPS = 1e-3
DEFAULT_DEDUP_SCALE_EPS = 1e-3
# Slightly looser bucket for same-mesh near-collapse option.
NEAR_MESH_POS_EPS = 1.0
NEAR_MESH_ANG_EPS = 1e-2


def _quantize(value: float, eps: float) -> int:
    if eps <= 0.0:
        return round(value * 1e6)
    return int(round(value / eps))


def _point_cloud_dedupe_key(
    co,
    rot,
    scale,
    *,
    pos_nd: int | None = None,
    ang_nd: int | None = None,
    pos_eps: float = DEFAULT_DEDUP_POS_EPS,
    ang_eps: float = DEFAULT_DEDUP_ANG_EPS,
    scale_eps: float = DEFAULT_DEDUP_SCALE_EPS,
) -> tuple:
    """Pose key for within-instancer dedupe.

    Legacy ``pos_nd`` / ``ang_nd`` (decimal places) still work; prefer ``pos_eps`` /
    ``ang_eps`` so float near-misses collapse safely.
    """
    if pos_nd is not None:
        qx = round(co.x, pos_nd)
        qy = round(co.y, pos_nd)
        qz = round(co.z, pos_nd)
    else:
        qx = _quantize(co.x, pos_eps)
        qy = _quantize(co.y, pos_eps)
        qz = _quantize(co.z, pos_eps)
    if ang_nd is not None:
        rx = round(rot.x, ang_nd)
        ry = round(rot.y, ang_nd)
        rz = round(rot.z, ang_nd)
    else:
        rx = _quantize(rot.x, ang_eps)
        ry = _quantize(rot.y, ang_eps)
        rz = _quantize(rot.z, ang_eps)
    return (
        qx,
        qy,
        qz,
        rx,
        ry,
        rz,
        _quantize(scale.x, scale_eps),
        _quantize(scale.y, scale_eps),
        _quantize(scale.z, scale_eps),
    )


def _pose_key_from_point(
    co,
    rot,
    *,
    pos_eps: float = DEFAULT_DEDUP_POS_EPS,
    ang_eps: float = DEFAULT_DEDUP_ANG_EPS,
) -> tuple:
    return (
        _quantize(co.x, pos_eps),
        _quantize(co.y, pos_eps),
        _quantize(co.z, pos_eps),
        _quantize(rot.x, ang_eps),
        _quantize(rot.y, ang_eps),
        _quantize(rot.z, ang_eps),
    )


def _asset_package_key(asset_path: str) -> str:
    """Normalize asset path for package matching (strip #spline: suffix, lower)."""
    path, _ = split_spline_asset_key((asset_path or "").strip())
    return path.lower()


def _mesh_stem_key(obj: bpy.types.Object) -> str:
    mesh_file = str(obj.get("arc_mesh_file") or "").strip()
    if mesh_file:
        return os.path.splitext(os.path.basename(mesh_file))[0].lower()
    asset = (obj.get("arc_asset_path") or "").strip()
    base, _ = split_spline_asset_key(asset)
    if "." in base:
        return base.rsplit(".", 1)[-1].lower()
    return base.lower() or obj.name.lower()


def _source_mesh_vertex_count(obj: bpy.types.Object) -> int:
    """Vertex count of the GN Object Info source (for aggressive keep-largest)."""
    src_name = str(obj.get("arc_instance_source_name") or "")
    if src_name:
        src = bpy.data.objects.get(src_name)
        if src and src.type == "MESH" and src.data:
            return len(src.data.vertices)
    # Fallback: any SRC_ object linked via modifier, else instancer mesh size.
    for mod in obj.modifiers:
        if mod.type != "NODES" or not mod.node_group:
            continue
        for node in mod.node_group.nodes:
            if node.type == "OBJECT_INFO" and getattr(node, "inputs", None):
                sock = node.inputs.get("Object")
                if sock and sock.default_value and sock.default_value.type == "MESH":
                    return len(sock.default_value.data.vertices)
    return len(obj.data.vertices) if obj.type == "MESH" and obj.data else 0


def _kind_preference(kind: str) -> int:
    """Lower is preferred when collapsing multi-asset piles."""
    k = (kind or "").strip()
    if k == "StaticMesh":
        return 0
    if k in (
        "InstancedStaticMesh",
        "HierarchicalInstancedStaticMesh",
        "FoliageInstancedStaticMesh",
    ):
        return 1
    if k == "SplineMesh":
        return 3
    return 2


def _iter_map_instancers(map_name: str = "", *, exact: bool = False):
    """Yield Fast-import point-cloud instancers for a map.

    When ``exact`` is False, also match ``BuriedCity_01`` ↔ ``BuriedCity_01_P``.
    """
    aliases: set[str] | None = None
    if map_name:
        aliases = {map_name} if exact else set(_map_name_aliases(map_name))
    for obj in bpy.data.objects:
        if not obj.get("arc_placement_instancer") or obj.type != "MESH" or not obj.data:
            continue
        if aliases is not None and (obj.get("arc_map") or "") not in aliases:
            continue
        yield obj


def _iter_map_groupable_objects(map_name: str = "", *, exact: bool = False):
    """Yield instancers plus unique placement meshes (DecalMesh, planes, etc.) for grouping."""
    aliases: set[str] | None = None
    if map_name:
        aliases = {map_name} if exact else set(_map_name_aliases(map_name))
    for obj in bpy.data.objects:
        if obj.type != "MESH" or not obj.data:
            continue
        if aliases is not None and (obj.get("arc_map") or "") not in aliases:
            continue
        if obj.get("arc_placement_instancer"):
            yield obj
        elif (
            obj.get("arc_unique_placement")
            or obj.get("arc_decal_mesh")
            or obj.get("arc_poster_mesh")
            or obj.get("arc_plane_mesh")
        ):
            yield obj
        elif obj.get("arc_asset_path") and needs_unique_mesh_placement(
            str(obj.get("arc_asset_path") or ""), obj.name
        ):
            yield obj


def _rebuild_instancer_from_indices(obj: bpy.types.Object, keep_indices: list[int]) -> int:
    """Rewrite instancer point cloud keeping ``keep_indices``. Returns points removed."""
    mesh = obj.data
    rot_attr = mesh.attributes.get(ATTR_ROTATION)
    scale_attr = mesh.attributes.get(ATTR_SCALE)
    if rot_attr is None or scale_attr is None:
        return 0
    n = len(mesh.vertices)
    if n <= 0 or len(keep_indices) >= n:
        return 0
    keep_coords: list[float] = []
    keep_rots: list[float] = []
    keep_scales: list[float] = []
    for i in keep_indices:
        co = mesh.vertices[i].co
        rot = rot_attr.data[i].vector
        scale = scale_attr.data[i].vector
        keep_coords.extend((co.x, co.y, co.z))
        keep_rots.extend((rot.x, rot.y, rot.z))
        keep_scales.extend((scale.x, scale.y, scale.z))

    new_mesh = bpy.data.meshes.new(mesh.name)
    count = len(keep_indices)
    new_mesh.vertices.add(count)
    new_mesh.vertices.foreach_set("co", keep_coords)
    new_rot = new_mesh.attributes.new(ATTR_ROTATION, "FLOAT_VECTOR", "POINT")
    new_rot.data.foreach_set("vector", keep_rots)
    new_scale = new_mesh.attributes.new(ATTR_SCALE, "FLOAT_VECTOR", "POINT")
    new_scale.data.foreach_set("vector", keep_scales)
    new_mesh.update()

    old_mesh = obj.data
    obj.data = new_mesh
    obj["arc_placement_count"] = count
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)
    obj.update_tag()
    return n - count


def dedupe_instancer_point_cloud(
    obj: bpy.types.Object,
    *,
    pos_eps: float = DEFAULT_DEDUP_POS_EPS,
    ang_eps: float = DEFAULT_DEDUP_ANG_EPS,
    scale_eps: float = DEFAULT_DEDUP_SCALE_EPS,
) -> int:
    """Remove near-duplicate points inside one Fast-import instancer. Returns points removed."""
    if not obj.get("arc_placement_instancer") or obj.type != "MESH" or not obj.data:
        return 0
    mesh = obj.data
    rot_attr = mesh.attributes.get(ATTR_ROTATION)
    scale_attr = mesh.attributes.get(ATTR_SCALE)
    if rot_attr is None or scale_attr is None:
        return 0

    n = len(mesh.vertices)
    if n <= 1:
        return 0

    keep_indices: list[int] = []
    seen: set[tuple] = set()
    for i in range(n):
        co = mesh.vertices[i].co
        rot = rot_attr.data[i].vector
        scale = scale_attr.data[i].vector
        key = _point_cloud_dedupe_key(
            co, rot, scale, pos_eps=pos_eps, ang_eps=ang_eps, scale_eps=scale_eps
        )
        if key in seen:
            continue
        seen.add(key)
        keep_indices.append(i)

    if len(keep_indices) >= n:
        return 0
    return _rebuild_instancer_from_indices(obj, keep_indices)


def hide_instance_sources(context, map_name: str = "") -> int:
    """Keep InstanceSources evaluable but quiet (bounds + layer eye-hide)."""
    map_name = (map_name or "").strip()
    hidden = 0
    for obj in bpy.data.objects:
        if not obj.get("arc_instance_source"):
            continue
        if map_name and (obj.get("arc_map") or "") != map_name:
            continue
        try:
            obj.display_type = "BOUNDS"
            obj.hide_select = True
            obj.hide_render = False
            # Object-level viewport must stay on so Geometry Nodes can evaluate;
            # the InstanceSources *layer* eye-hide (below) is what quiets the pile.
            obj.hide_viewport = False
            obj.hide_set(False)
            hidden += 1
        except Exception:
            pass

    # Prefer layer eye-hide on *InstanceSources collections (depsgraph still evaluates).
    for coll in bpy.data.collections:
        if "InstanceSources" not in coll.name:
            continue
        if map_name and map_name not in coll.name:
            continue
        hide_layer_collection(context, coll)
        try:
            coll.hide_render = False
            coll.hide_viewport = False
        except Exception:
            pass
    return hidden


MAP_SIMPLIFY_MODIFIER = "ARC Map Simplify"


def _iter_map_sources(map_name: str = ""):
    """Yield mesh objects tagged as Geometry Nodes instance sources."""
    map_name = (map_name or "").strip()
    for obj in bpy.data.objects:
        if not obj.get("arc_instance_source") or obj.type != "MESH" or not obj.data:
            continue
        if map_name and (obj.get("arc_map") or "") != map_name:
            continue
        yield obj


def _source_exclude_names(
    map_name: str = "",
    *,
    exclude_foliage: bool = True,
    exclude_helpers: bool = True,
    exclude_landscape: bool = True,
    exclude_sky: bool = True,
) -> set[str]:
    """SRC object names that should skip map-wide simplify."""
    skip: set[str] = set()
    for inst in _iter_map_instancers(map_name):
        asset = str(inst.get("arc_asset_path") or "")
        src_name = str(inst.get("arc_instance_source_name") or "")
        if not src_name:
            continue
        blob = f"{asset} {inst.name} {src_name}".lower()
        if exclude_foliage and classify_foliage_category(asset, inst.name) is not None:
            skip.add(src_name)
            continue
        if exclude_landscape and (
            "sm_landscape_" in blob
            or "backdroplandscape" in blob
            or "backdropisland" in blob
        ):
            skip.add(src_name)
            continue
        if exclude_sky and classify_map_group_category(asset, inst.name) == "Skybox / Spheres":
            skip.add(src_name)
            continue
        if exclude_helpers:
            hide_cat = classify_helper_hide_category(asset, inst.name)
            if hide_cat is not None or classify_map_group_category(asset, inst.name) == "Light Modifiers":
                skip.add(src_name)
                continue
    # Also catch orphan SRC meshes by name when no instancer link matched.
    for src in _iter_map_sources(map_name):
        nl = src.name.lower()
        if exclude_landscape and ("landscape" in nl or "backdropisland" in nl):
            skip.add(src.name)
        if exclude_sky and any(k in nl for k in ("skysphere", "sky_sphere", "cloudssphere", "ultra_dynamic_sky")):
            skip.add(src.name)
        if exclude_helpers and any(k in nl for k in ("occluder", "lightblock", "lightgeo", "lightportal")):
            skip.add(src.name)
    return skip


def simplify_map_source_meshes(
    map_name: str = "",
    *,
    ratio: float = 0.35,
    min_vertices: int = 500,
    exclude_foliage: bool = True,
    exclude_helpers: bool = True,
    exclude_landscape: bool = True,
    exclude_sky: bool = True,
    apply: bool = False,
) -> dict[str, int | float]:
    """Add (or update) a Decimate modifier on map SRC meshes — once per unique asset.

    Geometry Nodes Object Info reads the *evaluated* SRC mesh, so one Decimate on
    each InstanceSources object reduces every instance. This is the practical
    \"global\" simplify for Fast import; a modifier on instancer point-clouds would
    not reduce instance triangle count.

    Non-destructive by default (``apply=False``). Call ``clear_map_source_simplify``
    or set ratio=1.0 / remove the modifier to restore full mesh.
    """
    map_name = _resolve_map_name(map_name)
    ratio = max(0.01, min(1.0, float(ratio)))
    min_vertices = max(0, int(min_vertices))
    skip = _source_exclude_names(
        map_name,
        exclude_foliage=exclude_foliage,
        exclude_helpers=exclude_helpers,
        exclude_landscape=exclude_landscape,
        exclude_sky=exclude_sky,
    )
    stats: dict[str, int | float] = {
        "touched": 0,
        "skipped": 0,
        "excluded": 0,
        "applied": 0,
        "verts_before": 0,
        "ratio": ratio,
    }
    for src in list(_iter_map_sources(map_name)):
        if src.name in skip:
            stats["excluded"] += 1
            continue
        mesh = src.data
        nverts = len(mesh.vertices)
        stats["verts_before"] = int(stats["verts_before"]) + nverts
        if nverts < min_vertices:
            stats["skipped"] += 1
            continue
        mod = src.modifiers.get(MAP_SIMPLIFY_MODIFIER)
        if mod is None or mod.type != "DECIMATE":
            if mod is not None:
                try:
                    src.modifiers.remove(mod)
                except Exception:
                    pass
            mod = src.modifiers.new(name=MAP_SIMPLIFY_MODIFIER, type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        try:
            mod.use_collapse_triangulate = False
        except Exception:
            pass
        src["arc_map_simplify_ratio"] = ratio
        stats["touched"] += 1
        if apply:
            # Destructive: bake Decimate into the mesh datablock (not reversible).
            try:
                prev = bpy.context.view_layer.objects.active
                was_hidden_select = bool(src.hide_select)
                for o in list(bpy.context.selected_objects):
                    o.select_set(False)
                src.hide_select = False
                src.hide_set(False)
                src.select_set(True)
                bpy.context.view_layer.objects.active = src
                bpy.ops.object.mode_set(mode="OBJECT")
                bpy.ops.object.modifier_apply(modifier=MAP_SIMPLIFY_MODIFIER)
                src.select_set(False)
                src.hide_select = was_hidden_select
                if prev is not None:
                    try:
                        bpy.context.view_layer.objects.active = prev
                    except Exception:
                        pass
                stats["applied"] += 1
            except Exception:
                stats["skipped"] += 1
    return stats


def clear_map_source_simplify(map_name: str = "") -> dict[str, int]:
    """Remove ``ARC Map Simplify`` Decimate modifiers from map SRC meshes."""
    map_name = _resolve_map_name(map_name)
    removed = 0
    for src in list(_iter_map_sources(map_name)):
        mod = src.modifiers.get(MAP_SIMPLIFY_MODIFIER)
        if mod is None:
            continue
        try:
            src.modifiers.remove(mod)
            removed += 1
        except Exception:
            pass
        if "arc_map_simplify_ratio" in src:
            try:
                del src["arc_map_simplify_ratio"]
            except Exception:
                pass
    return {"removed": removed}


def remove_duplicate_map_collections(map_name: str, *, aggressive: bool = True) -> int:
    """Remove Blender-duplicated map collections left by re-runs (.001 etc.).

    When ``aggressive``, also purge leftover objects that only live in the .00N
    Stage-1 copy (canonical collection kept), then drop the empty leftover.
    """
    if not map_name:
        return 0
    removed = 0
    prefixes = (
        f"{map_name}_Placements",
        f"{map_name}_Instanced",
        f"{map_name}_InstanceSources",
        f"{map_name}_SplineGuides",
    )
    for coll in list(bpy.data.collections):
        base = coll.name.split(".", 1)[0]
        if base not in prefixes:
            continue
        if coll.name == base:
            continue  # keep the canonical name

        if aggressive:
            canonical = bpy.data.collections.get(base)
            for obj in list(coll.objects):
                try:
                    coll.objects.unlink(obj)
                except Exception:
                    pass
                # Delete objects that are not also in the canonical collection.
                still_linked = False
                if canonical is not None:
                    try:
                        still_linked = obj.name in canonical.objects
                    except Exception:
                        still_linked = False
                if still_linked:
                    continue
                # Prefer deleting clear re-import leftovers (.00N name or arc tags).
                is_leftover = (
                    "." in obj.name
                    or bool(obj.get("arc_placement_instancer"))
                    or bool(obj.get("arc_instance_source"))
                    or (obj.get("arc_map") or "") == map_name
                )
                if is_leftover:
                    try:
                        bpy.data.objects.remove(obj, do_unlink=True)
                    except Exception:
                        pass

        if len(coll.objects) == 0 and len(coll.children) == 0:
            try:
                bpy.data.collections.remove(coll)
                removed += 1
            except Exception:
                pass
    return removed


def remove_duplicate_instancers(map_name: str = "") -> int:
    """Remove double-imported instancers that share mesh file + identical point fingerprint."""
    groups: dict[tuple, list] = {}
    for obj in _iter_map_instancers(map_name):
        mesh_file = os.path.normcase(os.path.abspath(str(obj.get("arc_mesh_file") or "")))
        asset = (obj.get("arc_asset_path") or "").strip()
        kind = (obj.get("arc_asset_kind") or "").strip()
        n = len(obj.data.vertices)
        # Fingerprint: count + first/mid/last point coords
        fp_parts = [n]
        if n > 0:
            for idx in (0, n // 2, n - 1):
                co = obj.data.vertices[idx].co
                fp_parts.extend((round(co.x, 1), round(co.y, 1), round(co.z, 1)))
        key = (mesh_file or asset, kind, tuple(fp_parts), int(obj.get("arc_placement_count") or n))
        groups.setdefault(key, []).append(obj)

    removed = 0
    for objs in groups.values():
        if len(objs) < 2:
            continue
        # Keep the one without Blender .00N suffix when possible
        objs_sorted = sorted(objs, key=lambda o: ("." in o.name, o.name))
        for dup in objs_sorted[1:]:
            try:
                bpy.data.objects.remove(dup, do_unlink=True)
                removed += 1
            except Exception:
                pass
    return removed


def remove_undeformed_spline_overlaps(
    map_name: str = "",
    *,
    pos_eps: float = DEFAULT_DEDUP_POS_EPS,
    ang_eps: float = DEFAULT_DEDUP_ANG_EPS,
) -> int:
    """Drop undeformed SplineMesh points when a static of the same package shares the pose."""
    static_poses: dict[tuple, set[str]] = {}
    spline_objs: list = []

    for obj in _iter_map_instancers(map_name):
        kind = (obj.get("arc_asset_kind") or "").strip()
        asset = (obj.get("arc_asset_path") or "").strip()
        pkg = _asset_package_key(asset)
        stem = _mesh_stem_key(obj)
        rot_attr = obj.data.attributes.get(ATTR_ROTATION)
        if kind == "SplineMesh" or SPLINE_KEY_MARKER in asset:
            spline_objs.append(obj)
            continue
        for i, vert in enumerate(obj.data.vertices):
            rot = rot_attr.data[i].vector if rot_attr else mathutils.Vector((0.0, 0.0, 0.0))
            key = _pose_key_from_point(vert.co, rot, pos_eps=pos_eps, ang_eps=ang_eps)
            bucket = static_poses.setdefault(key, set())
            if pkg:
                bucket.add(pkg)
            if stem:
                bucket.add(stem)

    removed_total = 0
    for obj in spline_objs:
        notes = str(obj.get("arc_notes") or obj.get("arc_spline_notes") or "")
        parsed = parse_spline_notes(notes)
        # Pose-only / undeformed spline doubles a static at the same transform.
        if parsed.get("baked") and not parsed.get("undeformed"):
            continue
        mesh = obj.data
        rot_attr = mesh.attributes.get(ATTR_ROTATION)
        if rot_attr is None:
            continue
        pkg = _asset_package_key(str(obj.get("arc_asset_path") or ""))
        stem = _mesh_stem_key(obj)
        keep: list[int] = []
        for i, vert in enumerate(mesh.vertices):
            rot = rot_attr.data[i].vector
            key = _pose_key_from_point(vert.co, rot, pos_eps=pos_eps, ang_eps=ang_eps)
            hits = static_poses.get(key) or set()
            if (pkg and pkg in hits) or (stem and stem in hits):
                continue
            keep.append(i)
        if len(keep) < len(mesh.vertices):
            removed_total += _rebuild_instancer_from_indices(obj, keep)
        # Empty spline instancer → remove object
        if obj.data and len(obj.data.vertices) == 0:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass
    return removed_total


def analyze_origin_piles(
    map_name: str = "",
    *,
    pos_eps: float = DEFAULT_DEDUP_POS_EPS,
    ang_eps: float = DEFAULT_DEDUP_ANG_EPS,
    min_assets: int = 2,
) -> dict[str, Any]:
    """Count multi-asset stacks at the same world pose (FModel child-at-root symptom)."""
    pose_assets: dict[tuple, dict[str, int]] = {}
    for obj in _iter_map_instancers(map_name):
        asset = (obj.get("arc_asset_path") or obj.name).strip()
        rot_attr = obj.data.attributes.get(ATTR_ROTATION)
        for i, vert in enumerate(obj.data.vertices):
            rot = rot_attr.data[i].vector if rot_attr else mathutils.Vector((0.0, 0.0, 0.0))
            key = _pose_key_from_point(vert.co, rot, pos_eps=pos_eps, ang_eps=ang_eps)
            bucket = pose_assets.setdefault(key, {})
            bucket[asset] = bucket.get(asset, 0) + 1

    piles = {k: v for k, v in pose_assets.items() if len(v) >= min_assets}
    points_in_piles = sum(sum(v.values()) for v in piles.values())
    return {
        "pile_count": len(piles),
        "points_in_piles": points_in_piles,
        "extra_if_keep_one": sum(sum(v.values()) - 1 for v in piles.values()),
        "piles": piles,
    }


def mark_origin_pile_diagnostics(
    context,
    map_name: str = "",
    *,
    min_assets: int = 3,
    pos_eps: float = DEFAULT_DEDUP_POS_EPS,
    ang_eps: float = DEFAULT_DEDUP_ANG_EPS,
) -> int:
    """Create empties at multi-asset pile positions so wrong FModel roots are visible."""
    scene = context.scene
    map_name = (map_name or "").strip()
    if not map_name or map_name == "NONE":
        map_name = (getattr(scene, "arc_placement_map", "") or "").strip()
    if not map_name or map_name == "NONE":
        map_name = (getattr(scene, "arc_placement_map_name", "") or "").strip()
    if not map_name:
        return 0

    analysis = analyze_origin_piles(
        map_name, pos_eps=pos_eps, ang_eps=ang_eps, min_assets=min_assets
    )
    coll_name = f"{map_name}_OriginPileDiagnostics"
    # Clear previous markers
    old = bpy.data.collections.get(coll_name)
    if old is not None:
        for obj in list(old.objects):
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass
        try:
            bpy.data.collections.remove(old)
        except Exception:
            pass

    if analysis["pile_count"] <= 0:
        return 0

    coll = ensure_collection(coll_name)
    created = 0
    # Recover approximate world position from first matching point
    pose_world: dict[tuple, mathutils.Vector] = {}
    for obj in _iter_map_instancers(map_name):
        rot_attr = obj.data.attributes.get(ATTR_ROTATION)
        for i, vert in enumerate(obj.data.vertices):
            rot = rot_attr.data[i].vector if rot_attr else mathutils.Vector((0.0, 0.0, 0.0))
            key = _pose_key_from_point(vert.co, rot, pos_eps=pos_eps, ang_eps=ang_eps)
            if key in analysis["piles"] and key not in pose_world:
                pose_world[key] = vert.co.copy()

    for key, assets in analysis["piles"].items():
        co = pose_world.get(key)
        if co is None:
            continue
        empty = bpy.data.objects.new(f"Pile_{len(assets)}x_{created:04d}", None)
        empty.empty_display_type = "PLAIN_AXES"
        empty.empty_display_size = empty_display_size_for_map(meters=4.0)
        empty.location = co
        empty["arc_map"] = map_name
        empty["arc_origin_pile"] = 1
        empty["arc_pile_asset_count"] = len(assets)
        empty["arc_pile_point_count"] = sum(assets.values())
        coll.objects.link(empty)
        created += 1
    return created


def collapse_multi_asset_poses(
    map_name: str = "",
    *,
    mode: str = "largest",
    pos_eps: float = DEFAULT_DEDUP_POS_EPS,
    ang_eps: float = DEFAULT_DEDUP_ANG_EPS,
) -> int:
    """Aggressive: at identical poses with multiple assets, keep one point and drop the rest.

    Modes:
      - largest: keep source mesh with most vertices
      - first: keep lexicographically first asset path
      - prefer_static: StaticMesh over ISM/Spline, then largest
    """
    # pose -> list of (obj, point_index, asset, kind, src_verts)
    groups: dict[tuple, list[tuple]] = {}
    for obj in _iter_map_instancers(map_name):
        asset = (obj.get("arc_asset_path") or obj.name).strip()
        kind = (obj.get("arc_asset_kind") or "").strip()
        src_n = _source_mesh_vertex_count(obj)
        rot_attr = obj.data.attributes.get(ATTR_ROTATION)
        for i, vert in enumerate(obj.data.vertices):
            rot = rot_attr.data[i].vector if rot_attr else mathutils.Vector((0.0, 0.0, 0.0))
            key = _pose_key_from_point(vert.co, rot, pos_eps=pos_eps, ang_eps=ang_eps)
            groups.setdefault(key, []).append((obj, i, asset, kind, src_n))

    drop: dict = {}
    for entries in groups.values():
        assets = {e[2] for e in entries}
        if len(assets) < 2:
            continue
        if mode == "first":
            winner = sorted(entries, key=lambda e: (e[2], e[0].name, e[1]))[0]
        elif mode == "prefer_static":
            winner = sorted(
                entries,
                key=lambda e: (_kind_preference(e[3]), -e[4], e[2], e[0].name, e[1]),
            )[0]
        else:  # largest
            winner = sorted(
                entries,
                key=lambda e: (-e[4], _kind_preference(e[3]), e[2], e[0].name, e[1]),
            )[0]
        for e in entries:
            if e is winner:
                continue
            if e[2] == winner[2] and e[0] is winner[0]:
                continue
            drop.setdefault(e[0], set()).add(e[1])

    removed = 0
    for obj, indices in drop.items():
        n = len(obj.data.vertices)
        keep = [i for i in range(n) if i not in indices]
        if len(keep) < n:
            removed += _rebuild_instancer_from_indices(obj, keep)
        if obj.data and len(obj.data.vertices) == 0:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass
    return removed


def cleanup_duplicate_placements(
    context,
    *,
    map_name: str = "",
    dedupe_points: bool = True,
    near_mesh_eps: bool = True,
    hide_sources: bool = True,
    remove_dup_instancers: bool = True,
    remove_empty_collections: bool = True,
    remove_spline_static_overlaps: bool = True,
    aggressive_collapse: bool = False,
    aggressive_mode: str = "largest",
    mark_origin_piles: bool = False,
    origin_pile_min_assets: int = 3,
    hide_landscape_tiles: bool = False,
    hide_foliage_piles: bool = False,
) -> dict[str, int]:
    """In-scene cleanup after Fast import / accidental double Stage 1."""
    scene = context.scene
    map_name = (map_name or "").strip()
    if not map_name or map_name == "NONE":
        map_name = (getattr(scene, "arc_placement_map", "") or "").strip()
    if not map_name or map_name == "NONE":
        map_name = (getattr(scene, "arc_placement_map_name", "") or "").strip()

    stats = {
        "points_removed": 0,
        "near_mesh_points_removed": 0,
        "spline_overlap_removed": 0,
        "aggressive_removed": 0,
        "foliage_pile_removed": 0,
        "landscape_tiles_hidden": 0,
        "instancers_touched": 0,
        "dup_instancers_removed": 0,
        "sources_quieted": 0,
        "collections_removed": 0,
        "origin_piles": 0,
        "origin_pile_points": 0,
        "origin_pile_markers": 0,
    }

    if dedupe_points:
        for obj in list(_iter_map_instancers(map_name)):
            removed = dedupe_instancer_point_cloud(obj)
            if removed:
                stats["points_removed"] += removed
                stats["instancers_touched"] += 1

    if near_mesh_eps:
        # Second pass: same instancer (= same mesh file) with a slightly looser pose bucket.
        for obj in list(_iter_map_instancers(map_name)):
            removed = dedupe_instancer_point_cloud(
                obj, pos_eps=NEAR_MESH_POS_EPS, ang_eps=NEAR_MESH_ANG_EPS
            )
            if removed:
                stats["near_mesh_points_removed"] += removed
                stats["points_removed"] += removed
                stats["instancers_touched"] += 1

    if remove_spline_static_overlaps:
        stats["spline_overlap_removed"] = remove_undeformed_spline_overlaps(map_name)
        stats["points_removed"] += stats["spline_overlap_removed"]

    if aggressive_collapse:
        stats["aggressive_removed"] = collapse_multi_asset_poses(
            map_name, mode=aggressive_mode or "largest"
        )
        stats["points_removed"] += stats["aggressive_removed"]

    if hide_foliage_piles:
        stats["foliage_pile_removed"] = hide_foliage_on_origin_piles(map_name)
        stats["points_removed"] += stats["foliage_pile_removed"]

    if remove_dup_instancers:
        stats["dup_instancers_removed"] = remove_duplicate_instancers(map_name)

    if hide_sources:
        stats["sources_quieted"] = hide_instance_sources(context, map_name)

    if hide_landscape_tiles:
        stats["landscape_tiles_hidden"] = hide_misplaced_landscape_tiles(map_name)

    if remove_empty_collections and map_name:
        stats["collections_removed"] = remove_duplicate_map_collections(
            map_name, aggressive=True
        )

    piles = analyze_origin_piles(map_name, min_assets=2)
    stats["origin_piles"] = int(piles["pile_count"])
    stats["origin_pile_points"] = int(piles["points_in_piles"])

    if mark_origin_piles:
        stats["origin_pile_markers"] = mark_origin_pile_diagnostics(
            context, map_name, min_assets=max(2, int(origin_pile_min_assets) or 3)
        )

    return stats

def hide_layer_collection(context, collection: bpy.types.Collection) -> None:
    """Hide from the viewport without disabling evaluation.

    layer_collection.hide_viewport is the eye toggle; collection.hide_viewport is the
    monitor toggle, which would stop the depsgraph from evaluating the instance sources.
    """
    def walk(layer_coll):
        if layer_coll.collection == collection:
            return layer_coll
        for child in layer_coll.children:
            found = walk(child)
            if found is not None:
                return found
        return None

    try:
        found = walk(context.view_layer.layer_collection)
        if found is not None:
            found.hide_viewport = True
    except Exception:
        pass


def _join_mesh_objects(objs: list, *, name: str):
    """Join multiple imported mesh parts into one source object for Object Info instancing."""
    meshes = [o for o in objs if getattr(o, "type", None) == "MESH" and o.data is not None]
    if not meshes:
        return None
    if len(meshes) == 1:
        meshes[0].name = name[:60]
        return meshes[0]

    # Object.join requires a valid view-layer context; fall back to the first part if
    # Blender refuses the override (headless / unexpected UI state).
    try:
        bpy.ops.object.select_all(action="DESELECT")
    except Exception:
        pass
    for obj in meshes:
        try:
            obj.select_set(True)
        except Exception:
            pass
    active = meshes[0]
    try:
        bpy.context.view_layer.objects.active = active
        bpy.ops.object.join()
        active.name = name[:60]
        return active
    except Exception:
        return active


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = (len(sorted_vals) - 1) * p
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    t = idx - lo
    return sorted_vals[lo] * (1.0 - t) + sorted_vals[hi] * t


def _is_landscape_placement_row(row: dict[str, str]) -> bool:
    """True for landscape LOD / backdrop rows that skew city-footprint AABB."""
    ap = (row.get("asset_path") or "").lower()
    kind = (row.get("asset_kind") or "").lower()
    if "landscape" in kind:
        return True
    if "sm_landscape_" in ap or "/landscape" in ap:
        return True
    if "backdroplandscape" in ap or "backdropisland" in ap:
        return True
    return False


def build_world_bounds_from_csv(
    csv_path: str,
    *,
    map_name: str = "",
    out_path: str = "",
    city_core: bool = True,
) -> str | None:
    """Write a DataRaiders world_bounds JSON from placement AABB (outlier-filtered).

    When ``city_core`` is True (default), skip landscape LOD / backdrop rows so the
    footprint hugs the main playable props instead of sparse WP landscape tiles.

    Writes ``{map}_city_core_bounds.json`` by default so FModel landscape
    ``{map}_world_bounds.json`` is never overwritten.
    """
    rows = load_placements_csv(csv_path)
    if not rows:
        return None
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for row in rows:
        if city_core and _is_landscape_placement_row(row):
            continue
        x = _safe_float(row.get("x"))
        y = _safe_float(row.get("y"))
        z = _safe_float(row.get("z"))
        if abs(x) > 5_000_000 or abs(y) > 5_000_000 or abs(z) > 5_000_000:
            continue
        if city_core and abs(z) < 1.0 and ("sm_landscape_" in (row.get("asset_path") or "").lower()):
            continue
        xs.append(x)
        ys.append(y)
        zs.append(z)
    if len(xs) < 8:
        return None
    xs.sort()
    ys.sort()
    zs.sort()
    # Slightly wider than before so the placeholder plane covers streets around buildings.
    lo, hi = (0.01, 0.99) if city_core else (0.01, 0.99)
    min_x, max_x = _percentile(xs, lo), _percentile(xs, hi)
    min_y, max_y = _percentile(ys, lo), _percentile(ys, hi)
    min_z = _percentile(zs, 0.05 if city_core else 0.01)
    pad_x = max(2000.0, (max_x - min_x) * 0.08)
    pad_y = max(2000.0, (max_y - min_y) * 0.08)
    min_x -= pad_x
    max_x += pad_x
    min_y -= pad_y
    max_y += pad_y
    min_z -= 200.0
    sx = max(1.0, max_x - min_x)
    sy = max(1.0, max_y - min_y)
    map_name = map_name or os.path.basename(os.path.dirname(csv_path)) or "Map"
    payload = {
        "map_name": map_name,
        "source": "placements_aabb_city_core" if city_core else "placements_aabb",
        "units": "unreal_cm",
        "axes": "passthrough",
        "heightmap": {
            "name": map_name,
            "base_cell_size": 50.0,
            "base_dimensions": 4096,
            "location": {"x": min_x, "y": min_y, "z": min_z},
            "scale": {"x": 1.0, "y": 1.0, "z": 1.0},
            "size_xy": [sx, sy],
            # Encoding hints (UE LandscapeDataAccess) for a future aligned heightmap.
            "height_mid": 32768,
            "height_zscale": 1.0 / 128.0,
        },
    }
    if not out_path:
        suffix = "_city_core_bounds.json" if city_core else "_world_bounds.json"
        out_path = os.path.join(os.path.dirname(csv_path), f"{map_name}{suffix}")
    try:
        # Never clobber an FModel landscape world_bounds with city-core AABB.
        if (
            city_core
            and out_path.endswith("_world_bounds.json")
            and os.path.isfile(out_path)
        ):
            try:
                with open(out_path, "r", encoding="utf-8") as fh:
                    existing = json.load(fh)
                src = str(existing.get("source") or "")
                if src.startswith("landscape"):
                    out_path = os.path.join(
                        os.path.dirname(out_path), f"{map_name}_city_core_bounds.json"
                    )
            except (OSError, json.JSONDecodeError):
                pass
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        return out_path
    except OSError:
        return None


def ue_landscape_displace_strength_bu(hm: dict[str, Any], unit: float) -> float:
    """Blender Displace strength (mid_level=0.5) matching UE Landscape height encoding.

    UE: local_z = (u16 - 32768) / 128; world_cm = local_z * Scale3D.Z
    FModel stores scale.z as Scale3D.Z/100 (so 1.0 ⇒ 100 cm).
    Blender texture is 0..1; with mid 0.5, full black→white span is ``strength``.
    """
    try:
        z_stored = float((hm.get("scale") or {}).get("z", 1.0) or 1.0)
    except (TypeError, ValueError):
        z_stored = 1.0
    # Prefer explicit cm scale when present.
    try:
        z_cm = float(hm.get("scale_z_cm") or 0.0)
    except (TypeError, ValueError):
        z_cm = 0.0
    if z_cm <= 0.0:
        z_cm = z_stored * 100.0
    try:
        zscale = float(hm.get("height_zscale") or (1.0 / 128.0))
    except (TypeError, ValueError):
        zscale = 1.0 / 128.0
    # Full ushort span mapped through GetLocalHeight then ScaleZ.
    local_span = 65535.0 * zscale  # ≈ 511.99
    strength_cm = local_span * z_cm
    strength_bu = strength_cm * float(unit if unit > 0 else 0.01)
    return max(1.0, strength_bu)


def _heightmap_size_xy(hm: dict[str, Any]) -> tuple[float, float]:
    """Resolve footprint size from size_xy or cell metadata (overlay-compatible)."""
    size_xy = hm.get("size_xy") or [0, 0]
    try:
        sx = float(size_xy[0]) if len(size_xy) > 0 else 0.0
        sy = float(size_xy[1]) if len(size_xy) > 1 else 0.0
    except (TypeError, ValueError, IndexError):
        sx = sy = 0.0
    if sx > 0 and sy > 0:
        return sx, sy
    try:
        cell = float(hm.get("base_cell_size", 50.0) or 50.0)
        dims = int(hm.get("base_dimensions", 4096) or 4096)
        scale = hm.get("scale") or {}
        sx = dims * cell * float(scale.get("x", 1.0) or 1.0)
        sy = dims * cell * float(scale.get("y", 1.0) or 1.0)
        return sx, sy
    except (TypeError, ValueError):
        return 0.0, 0.0


# UE Landscape mid height (u16) → Blender Displace mid_level 0.5.
_HEIGHTMAP_MID_U16 = 32768
_HEIGHTMAP_MID_F = 32768.0 / 65535.0


def _heightmap_gray_float_pixels(img: bpy.types.Image) -> tuple[Any, int, int, bool]:
    """Return (gray HxW float64 0..1, w, h, has_alpha_channel_zero).

    ``has_alpha_channel_zero`` is True when any pixel has alpha≈0 (RGBA sources).
    """
    import numpy as np

    w, h = int(img.size[0]), int(img.size[1])
    if w <= 0 or h <= 0:
        return np.zeros((0, 0), dtype=np.float64), 0, 0, False
    # Ensure pixel buffer is loaded (file-backed images may be deferred).
    try:
        img.pixels[0]
    except Exception:
        pass
    pix = np.array(img.pixels[:], dtype=np.float64)
    if pix.size < w * h * 4:
        return np.zeros((h, w), dtype=np.float64), w, h, False
    rgba = pix.reshape(h, w, 4)
    # Blender stores row0 = image bottom; matches UV V=0 convention for our plane.
    gray = rgba[:, :, 0].copy()
    alpha = rgba[:, :, 3]
    has_a0 = bool((alpha < 0.001).any())
    # Treat fully transparent as void candidates via caller.
    return gray, w, h, has_a0


def detect_heightmap_void_mask(
    gray: Any,
    *,
    alpha: Any | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Detect missing/invalid height samples that explode Displace.

    Voids include: near-black, near-white sentinels, NaN/Inf, alpha≈0, and the
    common prepare_blender mid-fill (32768 / 0.5) when it forms a large plateau
    above/below real terrain (causes rectangular wall spikes).
    """
    import numpy as np

    stats: dict[str, Any] = {
        "void_frac": 0.0,
        "fill_value": _HEIGHTMAP_MID_F,
        "reasons": {},
    }
    if gray is None:
        return None, stats
    g = np.asarray(gray, dtype=np.float64)
    if g.size == 0:
        return np.zeros_like(g, dtype=bool), stats

    void = np.zeros(g.shape, dtype=bool)
    nan_m = ~np.isfinite(g)
    void |= nan_m
    stats["reasons"]["nan"] = float(nan_m.mean())

    black = np.isfinite(g) & (g <= 0.002)
    void |= black
    stats["reasons"]["black"] = float(black.mean())

    white = np.isfinite(g) & (g >= 0.998)
    void |= white
    stats["reasons"]["white"] = float(white.mean())

    if alpha is not None:
        a = np.asarray(alpha, dtype=np.float64)
        if a.shape == g.shape:
            a0 = a < 0.001
            void |= a0
            stats["reasons"]["alpha0"] = float(a0.mean())

    # Mid-sentinel plateaus (zeros→32768 fill) vs real terrain below/above mid.
    mid = np.isfinite(g) & (np.abs(g - _HEIGHTMAP_MID_F) <= 0.0005)
    mid_frac = float(mid.mean())
    stats["reasons"]["mid"] = mid_frac
    if mid_frac >= 0.02:
        valid_probe = np.isfinite(g) & (~mid) & (~black) & (~white) & (~nan_m)
        if alpha is not None:
            a = np.asarray(alpha, dtype=np.float64)
            if a.shape == g.shape:
                valid_probe &= a >= 0.001
        if valid_probe.any():
            med = float(np.median(g[valid_probe]))
            # Real Landscape heights for Arc maps sit well below mid; mid fill
            # then reads as a raised rectangular slab under Displace mid_level=0.5.
            if abs(med - _HEIGHTMAP_MID_F) >= 0.04:
                void |= mid
                stats["reasons"]["mid_as_void"] = mid_frac
                stats["fill_value"] = med

    # Flat min-border slabs (packing / missing strips) when they dominate an edge.
    finite = np.isfinite(g) & (~void)
    if finite.any():
        gmin = float(g[finite].min())
        at_min = finite & (np.abs(g - gmin) <= 0.00015)
        min_frac = float(at_min.mean())
        stats["reasons"]["at_min"] = min_frac
        if min_frac >= 0.015:
            # Require the strip to touch an image border (packing void), not
            # a legitimate flat playable plateau in the interior.
            h, w = g.shape
            border = np.zeros_like(at_min)
            strip = max(2, min(h, w) // 64)
            border[:strip, :] = True
            border[-strip:, :] = True
            border[:, :strip] = True
            border[:, -strip:] = True
            border_min = at_min & border
            if border_min.any() and float(border_min.mean()) >= 0.005:
                void |= border_min
                stats["reasons"]["border_min_void"] = float(border_min.mean())

    valid = np.isfinite(g) & (~void)
    if valid.any():
        stats["fill_value"] = float(np.median(g[valid]))
    stats["void_frac"] = float(void.mean()) if void.size else 0.0
    return void, stats


def neutralize_heightmap_voids(
    img: bpy.types.Image,
    *,
    name_prefix: str = "Heightmap",
) -> tuple[bpy.types.Image, bpy.types.Image | None, dict[str, Any]]:
    """Return (displace_safe_image, void_mask_image_or_None, stats).

    Void samples are rewritten to the median of valid heights so Displace
    mid_level=0.5 no longer creates rectangular wall spikes. A grayscale mask
    (1=valid, 0=void) is returned for material alpha clipping when voids exist.
    """
    import numpy as np

    stats: dict[str, Any] = {"void_frac": 0.0, "modified": 0}
    try:
        gray, w, h, _has_a0 = _heightmap_gray_float_pixels(img)
    except Exception as exc:
        stats["error"] = str(exc)
        return img, None, stats
    if w <= 0 or h <= 0 or gray.size == 0:
        return img, None, stats

    # Rebuild alpha from source pixels when present.
    pix = np.array(img.pixels[:], dtype=np.float64).reshape(h, w, 4)
    alpha = pix[:, :, 3]
    void, vstats = detect_heightmap_void_mask(gray, alpha=alpha)
    stats.update(vstats)
    if void is None or not bool(void.any()):
        stats["modified"] = 0
        return img, None, stats

    fill = float(stats.get("fill_value", _HEIGHTMAP_MID_F))
    safe = gray.copy()
    safe[void] = fill
    # Keep RGB identical (Displace samples Color/R); alpha stays 1 on safe image.
    safe_rgba = np.stack([safe, safe, safe, np.ones_like(safe)], axis=-1)

    disp_name = f"{name_prefix}_DispSafe"[:63]
    mask_name = f"{name_prefix}_VoidMask"[:63]
    disp_img = bpy.data.images.get(disp_name)
    if disp_img is None or tuple(disp_img.size) != (w, h):
        if disp_img is not None:
            try:
                bpy.data.images.remove(disp_img)
            except Exception:
                pass
        disp_img = bpy.data.images.new(disp_name, width=w, height=h, alpha=True, float_buffer=True)
    try:
        if disp_img.colorspace_settings.name != "Non-Color":
            disp_img.colorspace_settings.name = "Non-Color"
    except Exception:
        pass
    disp_img.pixels.foreach_set(np.asarray(safe_rgba, dtype=np.float32).reshape(-1))
    try:
        disp_img.pack()
    except Exception:
        pass
    disp_img.update()

    mask = (~void).astype(np.float64)
    mask_rgba = np.stack([mask, mask, mask, mask], axis=-1)
    mask_img = bpy.data.images.get(mask_name)
    if mask_img is None or tuple(mask_img.size) != (w, h):
        if mask_img is not None:
            try:
                bpy.data.images.remove(mask_img)
            except Exception:
                pass
        mask_img = bpy.data.images.new(mask_name, width=w, height=h, alpha=True, float_buffer=True)
    try:
        if mask_img.colorspace_settings.name != "Non-Color":
            mask_img.colorspace_settings.name = "Non-Color"
    except Exception:
        pass
    mask_img.pixels.foreach_set(np.asarray(mask_rgba, dtype=np.float32).reshape(-1))
    try:
        mask_img.pack()
    except Exception:
        pass
    mask_img.update()

    stats["modified"] = 1
    stats["fill_value"] = fill
    return disp_img, mask_img, stats


def apply_heightmap_void_mask_to_material(
    mat: bpy.types.Material,
    mask_img: bpy.types.Image | None,
) -> bool:
    """Delegate to materials.apply_heightmap_void_mask_to_material."""
    try:
        from . import materials as mats_mod

        return bool(mats_mod.apply_heightmap_void_mask_to_material(mat, mask_img))
    except Exception:
        return False


def create_heightmap_plane(
    bounds_json: str,
    *,
    image_path: str = "",
    map_name: str = "",
    displace: bool = False,
) -> bpy.types.Object | None:
    if not bounds_json or not os.path.isfile(bounds_json):
        return None
    with open(bounds_json, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    hm = data.get("heightmap") or data
    loc = hm.get("location") or {}
    sx, sy = _heightmap_size_xy(hm if isinstance(hm, dict) else {})
    if sx <= 0 or sy <= 0:
        return None
    ox = float(loc.get("x", 0))
    oy = float(loc.get("y", 0))
    oz = float(loc.get("z", 0))

    unit = map_unit_scale()
    mirror = map_mirror_y()
    sx_b = sx * unit
    sy_b = sy * unit
    ox_b = ox * unit
    # After Y-mirror, world Y' = -Y. Place +Y local extent so corners match.
    oy_b = -(oy + sy) * unit if mirror else oy * unit
    oz_b = oz * unit

    # FModel PrepareBlenderHeightmap flips PNG so row0 = UE max Y. Legacy exports
    # have row0 = UE min Y — invert UV V relative to the new convention.
    row0_max = bool(hm.get("image_row0_is_max_y")) if isinstance(hm, dict) else False

    name = f"{map_name or 'Map'}_HeightmapPlane"
    # Remove prior plane for this map
    for obj in list(bpy.data.objects):
        if obj.get("arc_heightmap_plane") and (not map_name or obj.get("arc_map") == map_name):
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass

    mesh = bpy.data.meshes.new(name + "_Mesh")
    # Quad in XY, origin at heightmap location corner, extending +X/+Y
    verts = [
        (0.0, 0.0, 0.0),
        (sx_b, 0.0, 0.0),
        (sx_b, sy_b, 0.0),
        (0.0, sy_b, 0.0),
    ]
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = (ox_b, oy_b, oz_b)
    # Same Blender map space as props (identity rotation; plane in XY).
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = (0.0, 0.0, 0.0)
    coll = ensure_collection(f"{map_name or 'Map'}_Placements")
    coll.objects.link(obj)

    # Ground material: sandy maps get dual-scale sand BRDF; others keep a dry earth
    # preview (height PNG may tint Base Color for debug — never used as Displace source
    # for sand look; Displace modifier owns the heightfield).
    use_sand = False
    try:
        from . import materials as mats_mod

        use_sand = bool(displace and mats_mod.map_prefers_sand_ground(map_name or ""))
    except Exception:
        use_sand = False

    mat = bpy.data.materials.new(name + "_Ground")
    mat.use_nodes = True
    nt = mat.node_tree
    nodes = nt.nodes
    links = nt.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    # Dry earth tone — readable in Solid + Material Preview without a heightmap PNG.
    if "Base Color" in bsdf.inputs:
        bsdf.inputs["Base Color"].default_value = (0.42, 0.36, 0.28, 1.0)
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = 0.92
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    obj.data.materials.append(mat)
    mesh.uv_layers.new(name="UVMap")
    uv = mesh.uv_layers.active.data
    # Blender V=0 = image bottom. With row0=max_y: origin(minY) → V=0 (bottom).
    # With legacy row0=min_y: origin(minY) → V=1 (top). Mirror swaps which corner is origin.
    flip_v = (mirror and row0_max) or ((not mirror) and (not row0_max))
    if flip_v:
        uvs = [(0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]
    else:
        uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    for i, loop in enumerate(mesh.loops):
        uv[loop.index].uv = uvs[i % 4]

    if image_path and os.path.isfile(image_path):
        try:
            img = bpy.data.images.load(image_path, check_existing=True)
            # 16-bit heightmaps need non-sRGB; keep data colorspace when possible.
            try:
                if img.colorspace_settings.name != "Non-Color":
                    img.colorspace_settings.name = "Non-Color"
            except Exception:
                pass

            # Neutralize missing-data voids so Displace mid_level=0.5 does not
            # raise rectangular wall spikes (zeros→32768 fill / black packing).
            disp_img = img
            void_mask = None
            void_stats: dict[str, Any] = {}
            try:
                disp_img, void_mask, void_stats = neutralize_heightmap_voids(
                    img, name_prefix=f"{map_name or 'Map'}_HM"
                )
            except Exception as exc:
                void_stats = {"error": str(exc), "modified": 0}
                disp_img, void_mask = img, None

            # Height as albedo is only a debug preview for non-sand maps.
            if not use_sand:
                tex = nodes.new("ShaderNodeTexImage")
                tex.image = img
                tex.interpolation = "Closest"
                links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])

            if displace:
                # Subdivide for displacement, then add Displace modifier from the height image.
                try:
                    import bmesh

                    bm = bmesh.new()
                    bm.from_mesh(mesh)
                    # Match heightmap resolution more closely (legacy 96 cuts ≈ 27 m/quad on city).
                    img_w = int(getattr(disp_img, "size", [0, 0])[0] or 0)
                    img_h = int(getattr(disp_img, "size", [0, 0])[1] or 0)
                    target = max(img_w, img_h, 64)
                    cuts = int(min(511, max(96, target // 8)))
                    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cuts, use_grid_fill=True)
                    bm.to_mesh(mesh)
                    bm.free()
                    mesh.update()
                    tex_slot = bpy.data.textures.new(name + "_DispTex", type="IMAGE")
                    tex_slot.image = disp_img
                    try:
                        tex_slot.use_interpolation = False
                    except Exception:
                        pass
                    mod = obj.modifiers.new(name="HeightDisplace", type="DISPLACE")
                    mod.texture = tex_slot
                    mod.texture_coords = "UV"
                    mod.mid_level = 0.5
                    # UE LandscapeDataAccess: (u16-32768)/128 * ScaleZ_cm → Blender Displace.
                    gran = hm.get("granularity")
                    if gran not in (None, ""):
                        mod.strength = float(gran) * unit
                    else:
                        mod.strength = ue_landscape_displace_strength_bu(
                            hm if isinstance(hm, dict) else {}, unit
                        )
                    obj["arc_heightmap_displaced"] = 1
                    obj["arc_heightmap_displace_strength"] = float(mod.strength)
                    obj["arc_heightmap_subdiv_cuts"] = int(cuts)
                    if void_stats.get("modified"):
                        obj["arc_heightmap_void_neutralized"] = 1
                        obj["arc_heightmap_void_frac"] = float(void_stats.get("void_frac") or 0.0)
                except Exception:
                    obj["arc_heightmap_displace_error"] = 1

            if void_mask is not None:
                try:
                    obj["arc_heightmap_void_mask"] = void_mask.name
                except Exception:
                    pass
                apply_heightmap_void_mask_to_material(mat, void_mask)
        except Exception:
            pass

    obj["arc_map"] = map_name
    obj["arc_heightmap_plane"] = 1
    obj["arc_heightmap_row0_max_y"] = 1 if row0_max else 0
    obj["arc_rotation_layout"] = ROTATION_LAYOUT_UE
    obj["arc_map_unit_scale"] = unit
    obj["arc_map_orientation"] = (
        ORIENTATION_MIRROR_Y if mirror else ORIENTATION_PASSTHROUGH
    )
    obj.display_type = "TEXTURED"
    obj.show_wire = False

    # Sandy maps (RivenTides etc.): dual-scale sand BRDF on the displaced plane.
    if use_sand:
        try:
            from . import materials as mats_mod

            ingame = ""
            hlod = ""
            try:
                scene = bpy.context.scene
                ingame = (getattr(scene, "arc_placement_ingame_map_image", "") or "").strip()
                hlod = (getattr(scene, "arc_placement_hlod_color_image", "") or "").strip()
            except Exception:
                pass
            if mats_mod.apply_sand_to_heightmap_object(
                obj,
                map_name=map_name or "",
                force=True,
                ingame_map_path=ingame,
                hlod_color_path=hlod,
                use_map_texturing=True,
            ):
                obj["arc_sand_ground"] = 1
            # Re-wire void alpha after sand rebuilds the node tree.
            mask_name = str(obj.get("arc_heightmap_void_mask") or "")
            mask_img = bpy.data.images.get(mask_name) if mask_name else None
            if mask_img is not None and obj.data.materials:
                apply_heightmap_void_mask_to_material(obj.data.materials[0], mask_img)
        except Exception:
            pass
    return obj


def _iter_map_transform_targets(map_name: str = ""):
    """Objects that participate in map orientation / unit-scale repairs."""
    map_name = (map_name or "").strip()
    for obj in bpy.data.objects:
        if map_name and obj.get("arc_map") and obj.get("arc_map") != map_name:
            continue
        if (
            obj.get("arc_placement_instancer")
            or obj.get("arc_heightmap_plane")
            or obj.get("arc_instance_source")
            or obj.get("arc_map")
            or (obj.name.startswith("LM_") or obj.name.startswith("ArcOrient"))
        ):
            yield obj


def apply_map_orientation_mirror_y(
    *,
    map_name: str = "",
    force: bool = False,
) -> dict[str, int]:
    """In-place Y mirror so an existing cm/passthrough scene matches in-game maps.

    Transforms point clouds, placement empties, and heightmap planes. Source meshes
    stay at the origin (instance ``arc_scale.y`` absorbs the reflection).
    Idempotent via ``arc_map_orientation=mirror_y`` unless ``force``.
    """
    stats = {
        "instancers": 0,
        "empties": 0,
        "heightmaps": 0,
        "skipped": 0,
        "points": 0,
    }
    for obj in list(_iter_map_transform_targets(map_name)):
        tagged = str(obj.get("arc_map_orientation") or ORIENTATION_PASSTHROUGH)
        if tagged == ORIENTATION_MIRROR_Y and not force:
            stats["skipped"] += 1
            continue

        if obj.get("arc_placement_instancer") and obj.type == "MESH" and obj.data:
            mesh = obj.data
            rot_attr = mesh.attributes.get(ATTR_ROTATION)
            scale_attr = mesh.attributes.get(ATTR_SCALE)
            n = len(mesh.vertices)
            coords = [0.0] * (n * 3)
            mesh.vertices.foreach_get("co", coords)
            for i in range(n):
                coords[i * 3 + 1] *= -1.0
            mesh.vertices.foreach_set("co", coords)
            if rot_attr is not None:
                for i in range(n):
                    v = rot_attr.data[i].vector
                    rot_attr.data[i].vector = (-v.x, v.y, -v.z)
            if scale_attr is not None:
                for i in range(n):
                    v = scale_attr.data[i].vector
                    scale_attr.data[i].vector = (v.x, -v.y, v.z)
            mesh.update()
            obj["arc_map_orientation"] = ORIENTATION_MIRROR_Y
            stats["instancers"] += 1
            stats["points"] += n
            continue

        if obj.get("arc_heightmap_plane") and obj.type == "MESH" and obj.data:
            # Reflect world corners: y' = -y. Keep +Y local extent.
            mw = obj.matrix_world.copy()
            corners = [mw @ mathutils.Vector(c) for c in obj.bound_box]
            ys = [c.y for c in corners]
            xs = [c.x for c in corners]
            zs = [c.z for c in corners]
            min_x, max_x = min(xs), max(xs)
            min_z = min(zs)
            # After negate Y: world y in [-max_y, -min_y]
            new_min_y = -max(ys)
            new_max_y = -min(ys)
            sx = max_x - min_x
            sy = new_max_y - new_min_y
            mesh = obj.data
            mesh.vertices.foreach_set(
                "co",
                [
                    0.0, 0.0, 0.0,
                    sx, 0.0, 0.0,
                    sx, sy, 0.0,
                    0.0, sy, 0.0,
                ],
            )
            mesh.update()
            obj.location = (min_x, new_min_y, min_z)
            obj.rotation_euler = (0.0, 0.0, 0.0)
            obj.scale = (1.0, 1.0, 1.0)
            # Flip UV V if present
            if mesh.uv_layers.active:
                uv = mesh.uv_layers.active.data
                for loop in mesh.loops:
                    u, v = uv[loop.index].uv
                    uv[loop.index].uv = (u, 1.0 - v)
            obj["arc_map_orientation"] = ORIENTATION_MIRROR_Y
            stats["heightmaps"] += 1
            continue

        if obj.get("arc_instance_source"):
            # Sources stay at origin; mirroring is on instance scale.
            obj["arc_map_orientation"] = ORIENTATION_MIRROR_Y
            continue

        # Placement empties / diagnostics
        if obj.type == "EMPTY" or obj.get("arc_map"):
            loc = tuple(obj.location)
            obj.rotation_mode = "XYZ"
            eu = tuple(obj.rotation_euler)
            sc = tuple(obj.scale)
            loc2, eu2, sc2 = apply_mirror_y_to_pose(loc, eu, sc)
            obj.location = loc2
            obj.rotation_euler = eu2
            obj.scale = sc2
            obj["arc_map_orientation"] = ORIENTATION_MIRROR_Y
            stats["empties"] += 1

    try:
        scene = bpy.context.scene
        scene["arc_map_orientation"] = ORIENTATION_MIRROR_Y
        if hasattr(scene, "arc_map_mirror_y"):
            scene.arc_map_mirror_y = True
    except Exception:
        pass
    return stats


def apply_map_unit_scale_to_meters(
    *,
    map_name: str = "",
    target_unit: float = MAP_UNIT_SCALE,
    force: bool = False,
) -> dict[str, int]:
    """Scale an existing map scene from Unreal-cm BU to meters (÷100 by default).

    Idempotent when objects/scene already tagged ``arc_map_unit_scale == target_unit``.
    Scales point positions, empty/heightmap locations, source mesh object scales
    (geometry was imported at cm), and empty display sizes. Instance ``arc_scale``
    is relative and left unchanged.
    """
    stats = {
        "instancers": 0,
        "sources": 0,
        "heightmaps": 0,
        "empties": 0,
        "skipped": 0,
        "points": 0,
    }
    if target_unit <= 0:
        target_unit = MAP_UNIT_SCALE

    for obj in list(_iter_map_transform_targets(map_name)):
        current = obj.get("arc_map_unit_scale", None)
        try:
            current_f = float(current) if current not in (None, "") else 1.0
        except (TypeError, ValueError):
            current_f = 1.0
        # Untagged legacy imports are cm (1.0). Already at target → skip.
        if abs(current_f - target_unit) < 1e-12 and not force:
            stats["skipped"] += 1
            continue
        factor = target_unit / current_f if current_f > 0 else target_unit

        if obj.get("arc_placement_instancer") and obj.type == "MESH" and obj.data:
            mesh = obj.data
            n = len(mesh.vertices)
            coords = [0.0] * (n * 3)
            mesh.vertices.foreach_get("co", coords)
            for i in range(len(coords)):
                coords[i] *= factor
            mesh.vertices.foreach_set("co", coords)
            mesh.update()
            obj["arc_map_unit_scale"] = target_unit
            stats["instancers"] += 1
            stats["points"] += n
            continue

        if obj.get("arc_instance_source") and obj.type == "MESH":
            # Geometry was imported at cm (scale 1); shrink object to meters.
            obj.scale = tuple(float(s) * factor for s in obj.scale)
            obj["arc_map_unit_scale"] = target_unit
            stats["sources"] += 1
            continue

        if obj.get("arc_heightmap_plane") and obj.type == "MESH" and obj.data:
            mesh = obj.data
            n = len(mesh.vertices)
            coords = [0.0] * (n * 3)
            mesh.vertices.foreach_get("co", coords)
            for i in range(len(coords)):
                coords[i] *= factor
            mesh.vertices.foreach_set("co", coords)
            mesh.update()
            obj.location = tuple(float(c) * factor for c in obj.location)
            obj["arc_map_unit_scale"] = target_unit
            stats["heightmaps"] += 1
            continue

        # Empties / other tagged map objects
        obj.location = tuple(float(c) * factor for c in obj.location)
        if obj.type == "EMPTY":
            try:
                obj.empty_display_size = float(obj.empty_display_size) * factor
            except Exception:
                pass
        # Child meshes under slow-import empties: scale local geometry via object scale
        if obj.type == "MESH" and not obj.get("arc_placement_instancer"):
            obj.scale = tuple(float(s) * factor for s in obj.scale)
        obj["arc_map_unit_scale"] = target_unit
        stats["empties"] += 1

    try:
        scene = bpy.context.scene
        scene["arc_map_unit_scale"] = target_unit
        if hasattr(scene, "arc_map_unit_scale"):
            scene.arc_map_unit_scale = target_unit
    except Exception:
        pass
    return stats


def hide_misplaced_landscape_tiles(map_name: str = "") -> int:
    """Hide backdrop landscape islands; keep SM_Landscape_* WP tiles visible.

    ``SM_Landscape_x*_y*_H_LOD1`` meshes already bake terrain height in vertices
    (actor Z≈0). They are the real exported ground fragments. City cells
    (x0/x1 × y4/y5 for Buried City) are often missing — leftover tiles still
    help as surrounding terrain. Backdrop islands/landscapes stay hidden.
    """
    map_name = _resolve_map_name(map_name)
    hidden = 0
    shown = 0
    placements = bpy.data.collections.get(f"{map_name}_Placements") if map_name else None
    land_coll = None
    backdrop_coll = None
    if placements is not None:
        land_root = ensure_collection("Landscape", parent=placements)
        land_coll = ensure_collection("WP Landscape LOD", parent=land_root)
        backdrop_coll = ensure_collection("Backdrop Landscape", parent=land_root)
        try:
            backdrop_coll.hide_viewport = True
            backdrop_coll.hide_render = True
        except Exception:
            pass

    for obj in list(_iter_map_instancers(map_name)):
        asset = (obj.get("arc_asset_path") or obj.name or "").lower()
        is_wp = "sm_landscape_" in asset or "landscape_x" in obj.name.lower()
        is_backdrop = "backdroplandscape" in asset or "backdropisland" in asset
        if not is_wp and not is_backdrop:
            continue
        dest = backdrop_coll if is_backdrop else land_coll
        if dest is not None and placements is not None:
            _relink_instancer_to_collection(
                obj,
                dest,
                map_name=map_name or "",
                placements=placements,
                known_group_names={
                    "Landscape",
                    "WP Landscape LOD",
                    "Backdrop Landscape",
                    "Landscape LOD",
                    "Helpers",
                    "Occluders",
                    "Collision Proxies",
                },
            )
        if is_backdrop:
            try:
                obj.hide_set(True)
                obj.hide_render = True
                obj["arc_landscape_tile_hidden"] = 1
                obj["arc_map_group"] = "Backdrop Landscape"
                hidden += 1
            except Exception:
                pass
        else:
            # Real WP landscape LOD — show; height is baked in the SRC mesh.
            try:
                obj.hide_set(False)
                obj.hide_render = False
                if "arc_landscape_tile_hidden" in obj:
                    del obj["arc_landscape_tile_hidden"]
                obj["arc_map_group"] = "WP Landscape LOD"
                shown += 1
            except Exception:
                pass
    return hidden


def organize_landscape_tiles(map_name: str = "", *, show_wp_tiles: bool = True) -> dict[str, int]:
    """Group landscape instancers; optionally show WP LOD tiles (baked height)."""
    hidden = hide_misplaced_landscape_tiles(map_name)
    shown = 0
    if show_wp_tiles:
        for obj in list(_iter_map_instancers(map_name)):
            asset = (obj.get("arc_asset_path") or obj.name or "").lower()
            if "sm_landscape_" not in asset and "landscape_x" not in obj.name.lower():
                continue
            if "backdrop" in asset:
                continue
            try:
                obj.hide_set(False)
                obj.hide_render = False
                shown += 1
            except Exception:
                pass
    return {"backdrop_hidden": hidden, "wp_shown": shown}


# Unreal cm — matches FModel MapPlacementLandscapeLodInjector / Buried City actors.
LANDSCAPE_CELL_SIZE_CM = 100_800.0
_LANDSCAPE_CELL_RE = re.compile(
    r"SM_Landscape_x(?P<x>\d+)_y(?P<y>\d+)_H_LOD\d+",
    re.IGNORECASE,
)


def _parse_landscape_cell(asset_or_name: str) -> tuple[int, int] | None:
    m = _LANDSCAPE_CELL_RE.search(asset_or_name or "")
    if not m:
        return None
    return int(m.group("x")), int(m.group("y"))


def dedupe_landscape_instancer_grid_points(map_name: str = "") -> dict[str, int]:
    """Keep only the landscape instance point nearest each cell's WP grid corner.

    Fast import names objects ``{stem}_x{N}`` where N is placement count — ``_x2`` means
    two points on one mesh (not LOD2). Buried City x2_y3 had a correct grid pose plus a
    mis-resolved outlier; both became ``SM_Landscape_x2_y3_H_LOD1_x2``.
    """
    map_name = _pick_map_name_for_scene(map_name) or _resolve_map_name(map_name)
    unit = map_unit_scale()
    mirror = map_mirror_y()
    stats = {"instancers": 0, "points_removed": 0, "renamed": 0}
    if not map_name:
        return stats

    cell_bu = LANDSCAPE_CELL_SIZE_CM * unit
    # Drop points farther than 25% of a cell from the expected corner.
    max_dist = cell_bu * 0.25

    for obj in list(_iter_map_instancers(map_name)):
        xy = _parse_landscape_cell(str(obj.get("arc_asset_path") or obj.name))
        if xy is None or not obj.data:
            continue
        cx, cy = xy
        # Expected Blender corner from Unreal grid.
        ux = cx * LANDSCAPE_CELL_SIZE_CM
        uy = cy * LANDSCAPE_CELL_SIZE_CM
        ex = ux * unit
        ey = (-uy if mirror else uy) * unit
        ez = 0.0

        mesh = obj.data
        n = len(mesh.vertices)
        if n <= 1:
            # Still snap single points that are wildly off-grid.
            if n == 1:
                co = mesh.vertices[0].co
                dist = math.sqrt((co.x - ex) ** 2 + (co.y - ey) ** 2 + (co.z - ez) ** 2)
                if dist > max_dist:
                    # Reposition to grid rather than delete the only tile.
                    mesh.vertices[0].co = (ex, ey, ez)
                    mesh.update()
                    obj["arc_landscape_snapped"] = 1
            continue

        best_i = 0
        best_d = 1e30
        for i, vert in enumerate(mesh.vertices):
            co = vert.co
            d = (co.x - ex) ** 2 + (co.y - ey) ** 2 + (co.z - ez) ** 2
            if d < best_d:
                best_d = d
                best_i = i
        keep = [best_i]
        removed = _rebuild_instancer_from_indices(obj, keep)
        if removed:
            stats["points_removed"] += removed
            stats["instancers"] += 1
            # Rename …_x2 → …_x1 to match remaining count.
            try:
                stem = obj.name
                m = re.search(r"_x(\d+)$", stem)
                if m:
                    new_name = f"{stem[: m.start()]}_x{len(obj.data.vertices)}"[:60]
                    if new_name != obj.name and bpy.data.objects.get(new_name) is None:
                        obj.name = new_name
                        stats["renamed"] += 1
            except Exception:
                pass
            obj["arc_placement_count"] = len(obj.data.vertices) if obj.data else 0
    return stats


def remove_landscape_gap_fill_objects(map_name: str = "") -> int:
    """Delete any legacy synthetic GapFill landscape planes (not real cooked data)."""
    map_name = _resolve_map_name(map_name) if map_name else ""
    removed = 0
    for obj in list(bpy.data.objects):
        is_gap = bool(obj.get("arc_landscape_gap_fill"))
        name = obj.name or ""
        if not is_gap and "GapFill" not in name:
            continue
        if map_name and obj.get("arc_map") not in (None, "", map_name):
            aliases = set(_map_name_aliases(map_name) or [map_name])
            if obj.get("arc_map") not in aliases and map_name not in name:
                continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
        except Exception:
            pass
    return removed


def report_missing_landscape_grid_cells(
    map_name: str = "",
    *,
    csv_path: str = "",
) -> dict[str, Any]:
    """Report city landscape cells with no cooked SM_Landscape package — no fake geometry.

    Reads FModel ``*_missing_landscape_cells.json`` when present. Prefer omitting the
    hole over inventing planes; Buried City ``x1_y4`` was never shipped as H_LOD1.
    """
    map_name = _pick_map_name_for_scene(map_name, csv_path=csv_path) or _resolve_map_name(
        map_name
    )
    csv_path = bpy.path.abspath(csv_path or "")
    stats: dict[str, Any] = {
        "filled": 0,
        "removed_gap_fills": 0,
        "cells": [],
        "note": "",
    }
    if not map_name:
        return stats

    stats["removed_gap_fills"] = remove_landscape_gap_fill_objects(map_name)

    holes: list[dict[str, Any]] = []
    folders = [os.path.dirname(csv_path)] if csv_path else []
    for folder in folders:
        for nm in _map_name_aliases(map_name) or [map_name]:
            side = os.path.join(folder, f"{nm}_missing_landscape_cells.json")
            if not os.path.isfile(side):
                continue
            try:
                with open(side, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                for cell in data.get("missing") or []:
                    holes.append(cell)
            except (OSError, json.JSONDecodeError, TypeError):
                pass
            break
        if holes:
            break

    for cell in holes:
        try:
            cx = int(cell.get("x"))
            cy = int(cell.get("y"))
        except (TypeError, ValueError):
            continue
        stats["cells"].append(f"x{cx}_y{cy}")

    if stats["cells"]:
        stats["note"] = (
            f"No cooked SM_Landscape for cell(s): {', '.join(stats['cells'])} "
            "(hole left empty — no gap-fill; see *_missing_landscape_cells.json)"
        )
    elif stats["removed_gap_fills"]:
        stats["note"] = (
            f"Removed {stats['removed_gap_fills']} legacy GapFill object(s); "
            "no missing-cell sidecar"
        )
    else:
        stats["note"] = "no missing landscape cells reported"
    return stats


def setup_map_ground_plane(
    map_name: str = "",
    *,
    csv_path: str = "",
) -> dict[str, Any]:
    """Create city-core ground plane; displace only when landscape bounds overlap city.

    Default import path: flat placeholder under props. Never stretch a distant
    LandscapeStreamingProxy PNG onto the city AABB.
    """
    scene = bpy.context.scene
    map_name = _resolve_map_name(map_name)
    csv_path = bpy.path.abspath(
        csv_path or getattr(scene, "arc_placement_csv", "") or ""
    )
    if csv_path and os.path.isfile(csv_path):
        leaf = os.path.basename(os.path.dirname(csv_path).rstrip("\\/"))
        if leaf:
            map_name = leaf
            try:
                scene.arc_placement_map_name = leaf
            except Exception:
                pass

    stats: dict[str, Any] = {
        "plane": "",
        "displaced": 0,
        "placeholder": 0,
        "note": "",
    }
    if not map_name:
        return stats

    resolved = resolve_heightmap_assets(
        csv_path=csv_path,
        map_name=map_name,
        bounds_hint=getattr(scene, "arc_placement_world_bounds_json", "") or "",
        image_hint=getattr(scene, "arc_placement_heightmap_image", "") or "",
    )
    image = resolved.get("image") or ""
    if image:
        try:
            scene.arc_placement_heightmap_image = image
        except Exception:
            pass

    landscape_bounds = ""
    manifest_bounds = resolved.get("manifest_bounds") or ""
    if manifest_bounds and os.path.isfile(manifest_bounds):
        try:
            with open(manifest_bounds, "r", encoding="utf-8") as fh:
                mb = json.load(fh)
            if str(mb.get("source") or "").startswith("landscape"):
                landscape_bounds = manifest_bounds
        except (OSError, json.JSONDecodeError):
            pass
    if not landscape_bounds and csv_path:
        folder = os.path.dirname(csv_path)
        name_candidates = [map_name]
        if map_name.endswith("_P"):
            name_candidates.append(map_name[:-2])
        else:
            name_candidates.append(f"{map_name}_P")
        folder_leaf = os.path.basename(folder.rstrip("\\/"))
        if folder_leaf and folder_leaf not in name_candidates:
            name_candidates.insert(0, folder_leaf)
        for search in (folder, resolved.get("export_root") or ""):
            if not search:
                continue
            for nm in name_candidates:
                cand = os.path.join(search, f"{nm}_landscape_bounds.json")
                if os.path.isfile(cand):
                    landscape_bounds = cand
                    break
            if landscape_bounds:
                break

    bounds = ""
    if csv_path and os.path.isfile(csv_path):
        built = build_world_bounds_from_csv(csv_path, map_name=map_name, city_core=True)
        if built:
            bounds = built
            try:
                scene.arc_placement_world_bounds_json = built
            except Exception:
                pass
    if not bounds or not os.path.isfile(bounds):
        for folder in (
            os.path.dirname(csv_path) if csv_path else "",
            resolved.get("export_root") or "",
        ):
            if not folder:
                continue
            cand = os.path.join(folder, f"{map_name}_city_core_bounds.json")
            if os.path.isfile(cand):
                bounds = cand
                break
    if not bounds or not os.path.isfile(bounds):
        bounds = resolved.get("bounds") or ""

    # Prefer real WP landscape tiles over a fake flat plane.
    wp_tiles = 0
    for obj in _iter_map_instancers(map_name):
        asset = (obj.get("arc_asset_path") or obj.name or "").lower()
        if "sm_landscape_" in asset and "backdrop" not in asset:
            wp_tiles += 1

    do_displace = bool(image) and os.path.isfile(image) and bool(landscape_bounds)
    note = ""
    if do_displace and bounds and not heightmap_overlaps_city_core(landscape_bounds, bounds):
        do_displace = False
        note = (
            "NOT city ground: heightmap does not overlap city (stale distant proxy). "
            "Rebuild FModel — city LandscapeStreamingProxy components cover holes like x1_y4."
        )
    elif do_displace and landscape_bounds and bounds and heightmap_overlaps_city_core(
        landscape_bounds, bounds
    ):
        bounds = landscape_bounds
        note = (
            "City-aligned LandscapeComponent heightmap (covers playable core including "
            "cells without SM_Landscape H_LOD1)."
        )
    elif not do_displace:
        note = (
            "Flat city ground placeholder (hidden when WP landscape tiles are present). "
            "Rebuild FModel → Export Map Placements + Meshes for city-aligned heightmap."
        )

    if not bounds or not os.path.isfile(bounds):
        stats["note"] = "no bounds — skipped ground plane"
        stats["wp_tiles"] = wp_tiles
        return stats

    # When SM_Landscape tiles exist, skip creating a misleading flat slab (or hide old one).
    if wp_tiles > 0 and not do_displace:
        for obj in list(bpy.data.objects):
            if obj.get("arc_heightmap_plane") and (
                not map_name or obj.get("arc_map") == map_name or obj.get("arc_ground_placeholder")
            ):
                try:
                    obj.hide_set(True)
                    obj.hide_render = True
                    obj["arc_ground_placeholder_hidden"] = 1
                except Exception:
                    pass
        stats["placeholder"] = 0
        stats["wp_tiles"] = wp_tiles
        stats["note"] = (
            f"Using {wp_tiles} SM_Landscape_* WP tile(s) as ground — flat placeholder skipped."
        )
        return stats

    obj = create_heightmap_plane(
        bounds,
        image_path=image if do_displace else "",
        map_name=map_name,
        displace=do_displace,
    )
    if obj is None:
        stats["note"] = "failed to create ground plane"
        stats["wp_tiles"] = wp_tiles
        return stats
    if not do_displace:
        obj.name = f"{map_name}_CityGroundPlane"
        obj["arc_ground_placeholder"] = 1
        # Hidden by default — a flat slab is not terrain; real ground is SM_Landscape_*.
        try:
            obj.hide_set(True)
            obj.hide_render = True
        except Exception:
            pass
        stats["placeholder"] = 1
    else:
        stats["displaced"] = 1
    if note:
        obj["arc_heightmap_mismatch"] = 1
        obj["arc_heightmap_note"] = note
    stats["plane"] = obj.name
    stats["note"] = note
    stats["wp_tiles"] = wp_tiles
    return stats


def reload_heightmap_displace_only(
    map_name: str = "",
    *,
    csv_path: str = "",
) -> dict[str, Any]:
    """Swap/rebuild only the CityGroundPlane from on-disk heightmap + landscape_bounds.

    Does **not** reimport meshes, regroup collections, or touch SM_Landscape tiles.
    Use after ``prepare_blender_heightmap.py`` or FModel heightmap-only export.
    """
    scene = bpy.context.scene
    map_name = _resolve_map_name(map_name)
    csv_path = bpy.path.abspath(
        csv_path or getattr(scene, "arc_placement_csv", "") or ""
    )
    if csv_path and os.path.isfile(csv_path):
        leaf = os.path.basename(os.path.dirname(csv_path).rstrip("\\/"))
        if leaf:
            map_name = leaf
            try:
                scene.arc_placement_map_name = leaf
            except Exception:
                pass

    stats: dict[str, Any] = {
        "plane": "",
        "displaced": 0,
        "image": "",
        "bounds": "",
        "note": "",
    }
    if not map_name:
        stats["note"] = "no map name"
        return stats

    resolved = resolve_heightmap_assets(
        csv_path=csv_path,
        map_name=map_name,
        bounds_hint=getattr(scene, "arc_placement_world_bounds_json", "") or "",
        image_hint=getattr(scene, "arc_placement_heightmap_image", "") or "",
    )
    image = (resolved.get("image") or "").strip()
    export_root = resolved.get("export_root") or ""
    folder = os.path.dirname(csv_path) if csv_path else export_root

    landscape_bounds = ""
    name_candidates = [map_name]
    if map_name.endswith("_P"):
        name_candidates.append(map_name[:-2])
    else:
        name_candidates.append(f"{map_name}_P")
    if folder:
        leaf = os.path.basename(folder.rstrip("\\/"))
        if leaf and leaf not in name_candidates:
            name_candidates.insert(0, leaf)
    for search in (folder, export_root):
        if not search:
            continue
        for nm in name_candidates:
            cand = os.path.join(search, f"{nm}_landscape_bounds.json")
            if os.path.isfile(cand):
                landscape_bounds = cand
                break
        if landscape_bounds:
            break
    if not landscape_bounds:
        mb = resolved.get("manifest_bounds") or ""
        if mb and os.path.isfile(mb):
            try:
                with open(mb, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if str(data.get("source") or "").startswith("landscape"):
                    landscape_bounds = mb
            except (OSError, json.JSONDecodeError):
                pass

    if not image or not os.path.isfile(image):
        stats["note"] = "heightmap PNG not found — run prepare_blender_heightmap.py first"
        return stats
    if not landscape_bounds or not os.path.isfile(landscape_bounds):
        stats["note"] = "landscape_bounds.json not found — run prepare_blender_heightmap.py first"
        return stats

    try:
        with open(landscape_bounds, "r", encoding="utf-8") as fh:
            lb = json.load(fh)
        if lb.get("city_aligned") is False:
            stats["note"] = "landscape_bounds city_aligned=false — refusing displace"
            return stats
        city_path = ""
        if folder:
            for nm in name_candidates:
                c = os.path.join(folder, f"{nm}_city_core_bounds.json")
                if os.path.isfile(c):
                    city_path = c
                    break
        if city_path and not heightmap_overlaps_city_core(landscape_bounds, city_path):
            stats["note"] = "heightmap does not overlap city core"
            return stats
    except (OSError, json.JSONDecodeError) as exc:
        stats["note"] = f"bad landscape_bounds: {exc}"
        return stats

    # Force Blender to drop cached pixels so a overwritten PNG is picked up.
    abs_image = os.path.normpath(os.path.abspath(image))
    for img in list(bpy.data.images):
        try:
            fp = bpy.path.abspath(img.filepath) if img.filepath else ""
            if fp and os.path.normpath(os.path.abspath(fp)) == abs_image:
                bpy.data.images.remove(img)
        except Exception:
            pass

    try:
        scene.arc_placement_heightmap_image = image
        scene.arc_placement_world_bounds_json = landscape_bounds
    except Exception:
        pass

    obj = create_heightmap_plane(
        landscape_bounds,
        image_path=image,
        map_name=map_name,
        displace=True,
    )
    if obj is None:
        stats["note"] = "create_heightmap_plane failed"
        return stats
    obj.name = f"{map_name}_CityGroundPlane"
    obj["arc_ground_placeholder"] = 0
    try:
        obj.hide_set(False)
        obj.hide_render = False
    except Exception:
        pass
    stats["plane"] = obj.name
    stats["displaced"] = 1 if obj.get("arc_heightmap_displaced") else 0
    stats["sand"] = 1 if obj.get("arc_sand_ground") else 0
    stats["void_neutralized"] = 1 if obj.get("arc_heightmap_void_neutralized") else 0
    stats["void_frac"] = float(obj.get("arc_heightmap_void_frac") or 0.0)
    stats["image"] = image
    stats["bounds"] = landscape_bounds
    sand_note = " · sand BRDF" if stats["sand"] else ""
    void_note = ""
    if stats["void_neutralized"]:
        void_note = f" · voids masked ({stats['void_frac']:.1%})"
    stats["note"] = (
        f"Reloaded displace from {os.path.basename(image)} "
        f"({os.path.basename(landscape_bounds)}){sand_note}{void_note}"
    )
    return stats


def finalize_map_import_postprocess(
    map_name: str = "",
    *,
    csv_path: str = "",
) -> dict[str, Any]:
    """Default post-import: collections, landscape tiles, city ground, frame camera.

    Called automatically at the end of Stage 1 (Fast/Slow) and FModel bridge receive.
    Uses the import-stamped map tag (not only the CSV folder leaf) so grouping
    always finds the instancers that were just built.
    """
    csv_path = bpy.path.abspath(
        csv_path or getattr(bpy.context.scene, "arc_placement_csv", "") or ""
    )
    map_name = _pick_map_name_for_scene(map_name, csv_path=csv_path) or _resolve_map_name(
        map_name
    )
    out: dict[str, Any] = {"map": map_name}
    try:
        context = bpy.context
        if context and getattr(context, "workspace", None):
            context.workspace.status_text_set(
                f"Map import — grouping collections ({map_name})…"
            )
    except Exception:
        pass
    try:
        out["groups"] = group_map_collections(
            map_name, hide_helpers=True, include_foliage=True, include_glass=True,
        )
    except Exception as exc:
        out["groups_error"] = str(exc)
        out["groups"] = {}
    try:
        out["landscape"] = organize_landscape_tiles(map_name, show_wp_tiles=True)
    except Exception as exc:
        out["landscape_error"] = str(exc)
        out["landscape"] = {}
    try:
        out["landscape_dedupe"] = dedupe_landscape_instancer_grid_points(map_name)
    except Exception as exc:
        out["landscape_dedupe_error"] = str(exc)
        out["landscape_dedupe"] = {}
    try:
        out["landscape_gaps"] = report_missing_landscape_grid_cells(
            map_name, csv_path=csv_path
        )
    except Exception as exc:
        out["landscape_gaps_error"] = str(exc)
        out["landscape_gaps"] = {}
    try:
        out["ground"] = setup_map_ground_plane(map_name, csv_path=csv_path)
    except Exception as exc:
        out["ground_error"] = str(exc)
        out["ground"] = {}
    try:
        out["camera"] = frame_viewport_to_city_center(
            bpy.context, map_name, csv_path=csv_path
        )
    except Exception as exc:
        out["camera_error"] = str(exc)
        out["camera"] = {}
    try:
        context = bpy.context
        if context and getattr(context, "workspace", None):
            context.workspace.status_text_set(None)
    except Exception:
        pass
    return out


def _bounds_xy_from_heightmap_dict(hm: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """Return (min_x, min_y, max_x, max_y) in Unreal cm from a heightmap block."""
    loc = hm.get("location") or {}
    try:
        ox = float(loc.get("x", 0))
        oy = float(loc.get("y", 0))
    except (TypeError, ValueError):
        return None
    sx, sy = _heightmap_size_xy(hm)
    if sx <= 0 or sy <= 0:
        return None
    return (ox, oy, ox + sx, oy + sy)


def heightmap_overlaps_city_core(
    landscape_bounds_json: str,
    city_bounds_json: str,
    *,
    min_overlap_ratio: float = 0.05,
) -> bool:
    """True when FModel landscape footprint meaningfully overlaps the city-core AABB."""
    if not landscape_bounds_json or not city_bounds_json:
        return False
    if not os.path.isfile(landscape_bounds_json) or not os.path.isfile(city_bounds_json):
        return False
    try:
        with open(landscape_bounds_json, "r", encoding="utf-8") as fh:
            land = json.load(fh)
        with open(city_bounds_json, "r", encoding="utf-8") as fh:
            city = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return False
    a = _bounds_xy_from_heightmap_dict(land.get("heightmap") or land)
    b = _bounds_xy_from_heightmap_dict(city.get("heightmap") or city)
    if not a or not b:
        return False
    ox = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    oy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    overlap = ox * oy
    if overlap <= 0:
        return False
    city_area = max(1.0, (b[2] - b[0]) * (b[3] - b[1]))
    return (overlap / city_area) >= min_overlap_ratio


def resolve_heightmap_assets(
    *,
    csv_path: str = "",
    map_name: str = "",
    bounds_hint: str = "",
    image_hint: str = "",
) -> dict[str, str]:
    """Resolve world_bounds / heightmap PNG from hints, manifest, and export folders."""
    out = {"bounds": "", "image": "", "export_root": "", "manifest_bounds": "", "note": ""}
    csv_path = bpy.path.abspath(csv_path) if csv_path else ""
    bounds_hint = bpy.path.abspath(bounds_hint) if bounds_hint else ""
    image_hint = bpy.path.abspath(image_hint) if image_hint else ""
    if bounds_hint and os.path.isfile(bounds_hint):
        out["bounds"] = bounds_hint
    if image_hint and os.path.isfile(image_hint):
        out["image"] = image_hint

    manifest = load_placements_manifest(csv_path) if csv_path else {}
    export_root = (manifest.get("mesh_export_root") or "").strip()
    if export_root and os.path.isdir(export_root):
        out["export_root"] = export_root
    wb = (manifest.get("world_bounds_path") or "").strip()
    if wb and os.path.isfile(wb):
        out["manifest_bounds"] = wb
        if not out["bounds"]:
            out["bounds"] = wb
    hm = (manifest.get("heightmap_image_path") or "").strip()
    if hm and os.path.isfile(hm) and not out["image"]:
        out["image"] = hm

    map_name = map_name or (manifest.get("map_name") or "")
    search_dirs: list[str] = []
    for d in (
        os.path.dirname(csv_path) if csv_path else "",
        export_root,
        os.path.dirname(out["manifest_bounds"]) if out["manifest_bounds"] else "",
        os.path.dirname(out["image"]) if out["image"] else "",
    ):
        d = bpy.path.abspath(d) if d else ""
        if d and os.path.isdir(d) and d not in search_dirs:
            search_dirs.append(d)

    if not out["bounds"] or not os.path.isfile(out["bounds"]):
        for folder in search_dirs:
            for name in (
                f"{map_name}_city_core_bounds.json",
                f"{map_name}_world_bounds.json",
                "world_bounds.json",
            ):
                cand = os.path.join(folder, name)
                if os.path.isfile(cand):
                    out["bounds"] = cand
                    break
            if out["bounds"] and os.path.isfile(out["bounds"]):
                break

    if not out["image"] or not os.path.isfile(out["image"]):
        for folder in search_dirs:
            for name in (
                f"{map_name}_heightmap.png",
                "heightmap.png",
                f"{map_name}_placements_overlay.png",
            ):
                cand = os.path.join(folder, name)
                if os.path.isfile(cand):
                    out["image"] = cand
                    break
            if out["image"] and os.path.isfile(out["image"]):
                break
    return out


def hide_foliage_on_origin_piles(
    map_name: str = "",
    *,
    min_assets: int = 5,
    name_substrings: tuple[str, ...] = ("tree", "foliage", "bush", "plant"),
) -> int:
    """Remove foliage points that share a multi-asset origin pile (FModel child-at-root)."""
    analysis = analyze_origin_piles(map_name, min_assets=min_assets)
    pile_keys = set(analysis["piles"].keys())
    if not pile_keys:
        return 0
    removed = 0
    for obj in list(_iter_map_instancers(map_name)):
        nl = obj.name.lower()
        if not any(s in nl for s in name_substrings):
            continue
        rot_attr = obj.data.attributes.get(ATTR_ROTATION)
        keep: list[int] = []
        for i, vert in enumerate(obj.data.vertices):
            rot = rot_attr.data[i].vector if rot_attr else mathutils.Vector((0.0, 0.0, 0.0))
            key = _pose_key_from_point(vert.co, rot)
            if key in pile_keys:
                continue
            keep.append(i)
        if len(keep) < len(obj.data.vertices):
            removed += _rebuild_instancer_from_indices(obj, keep)
    return removed



def create_spline_guide_empties(
    rows: list[dict[str, str]],
    *,
    map_name: str = "",
) -> tuple[int, int]:
    """Create start/end empties for SplineMesh rows that carry Hermite notes.

    Returns (guides_created, undeformed_count).
    """
    spline_rows = [r for r in rows if is_spline_mesh_row(r)]
    if not spline_rows:
        return 0, 0
    coll = ensure_collection(f"{map_name or 'Map'}_SplineGuides")
    # Clear prior guides for this map
    for obj in list(coll.objects):
        if obj.get("arc_spline_guide"):
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass

    created = 0
    undeformed = 0
    for i, row in enumerate(spline_rows):
        meta = parse_spline_notes(row.get("notes") or "")
        if not meta.get("baked"):
            undeformed += 1
        start = meta.get("start")
        end = meta.get("end")
        if not (isinstance(start, tuple) and len(start) == 3 and isinstance(end, tuple) and len(end) == 3):
            continue
        # Notes are component-local; approximate world by adding placement translation.
        ox = _safe_float(row.get("x"))
        oy = _safe_float(row.get("y"))
        oz = _safe_float(row.get("z"))
        # Apply component world rotation so local start/end are oriented correctly.
        eul = unreal_to_blender_euler(
            _safe_float(row.get("pitch")),
            _safe_float(row.get("yaw")),
            _safe_float(row.get("roll")),
        )
        rot = eul.to_matrix().to_4x4()
        for label, local in (("Start", start), ("End", end)):
            loc = rot @ mathutils.Vector(local)
            empty = bpy.data.objects.new(
                f"SplineGuide_{i}_{label}"[:60], None
            )
            empty.empty_display_type = "PLAIN_AXES"
            empty.empty_display_size = 50.0
            empty.location = (ox + loc.x, oy + loc.y, oz + loc.z)
            empty.rotation_mode = "XYZ"
            empty.rotation_euler = eul
            empty["arc_map"] = map_name
            empty["arc_spline_guide"] = 1
            empty["arc_asset_path"] = row.get("asset_path") or ""
            empty["arc_rotation_layout"] = ROTATION_LAYOUT_UE
            coll.objects.link(empty)
            created += 1
    return created, undeformed


class ARC_OT_ImportPlacementBase:
    """Shared modal batch import state for empties / meshes."""

    _timer = None
    _rows: list = []
    _index: int = 0
    _batch: int = 100
    _map_name: str = ""
    _root_coll = None
    _batch_idx: int = 0
    _mode: str = "empties"
    _pioneer_root: str = ""
    _created: int = 0
    _meshes_loaded: int = 0
    _meshes_missing: int = 0
    _mesh_cache: dict = {}
    _resolve_cache: dict = {}
    _extra_roots: list = []
    _mesh_by_asset: dict = {}
    _mesh_exports: list = []
    _resolve_lookup: dict = {}
    _progress_tick: int = 0

    def _setup(self, context, mode: str):
        scene = context.scene
        csv_path = bpy.path.abspath(getattr(scene, "arc_placement_csv", "") or "")
        rows = load_placements_csv(csv_path)
        if not rows:
            self.report({"ERROR"}, "No placements CSV loaded — set Map Placement CSV path")
            return False
        rows, dropped_dupes = dedupe_placement_rows(rows)
        self._dropped_dupes = dropped_dupes
        resolve_ctx = placement_mesh_resolve_context(scene, csv_path)
        if mode == "meshes" and not _has_mesh_resolve_sources(resolve_ctx):
            self.report({"ERROR"}, _missing_mesh_source_message(resolve_ctx))
            return False
        self._rows = rows
        self._index = 0
        self._batch = max(1, int(getattr(scene, "arc_placement_batch_size", 100) or 100))
        self._map_name = (getattr(scene, "arc_placement_map", "") or "").strip()
        if not self._map_name or self._map_name == "NONE":
            self._map_name = (getattr(scene, "arc_placement_map_name", "") or "").strip()
        if not self._map_name:
            # Infer from filename / folder
            base = os.path.basename(csv_path)
            parent = os.path.basename(os.path.dirname(csv_path))
            grand = os.path.basename(os.path.dirname(os.path.dirname(csv_path)))
            if parent in ("_PropHarvest", "_Placement") or parent == os.path.basename(get_placement_workspace(scene)):
                self._map_name = grand or "Map"
            else:
                # workspace/MapName/placements_*.csv
                self._map_name = parent if parent else (os.path.splitext(base)[0] or "Map")
        self._root_coll = ensure_collection(f"{self._map_name}_Placements")
        self._batch_idx = 0
        self._mode = mode
        self._pioneer_root = resolve_ctx.get("pioneer_root") or ""
        self._extra_roots = list(resolve_ctx.get("extra_roots") or [])
        self._mesh_by_asset = dict(resolve_ctx.get("mesh_by_asset") or {})
        self._mesh_exports = list(resolve_ctx.get("mesh_exports") or [])
        self._resolve_lookup = dict(resolve_ctx.get("resolve_lookup") or {})
        self._created = 0
        self._meshes_loaded = 0
        self._meshes_missing = 0
        self._mesh_cache = {}
        self._resolve_cache = {}
        self._progress_tick = 0
        return True

    def modal(self, context, event):
        if event.type in {"ESC"}:
            self.cancel(context)
            extra = ""
            if self._mode == "meshes":
                extra = f" — meshes {self._meshes_loaded}, missing {self._meshes_missing}"
            self.report(
                {"WARNING"},
                f"Placement import cancelled ({self._created}/{len(self._rows)}){extra}",
            )
            return {"CANCELLED"}

        if event.type == "TIMER":
            end = min(self._index + self._batch, len(self._rows))
            batch_coll = ensure_collection(
                f"{self._map_name}_batch_{self._batch_idx:04d}",
                parent=self._root_coll,
            )
            for i in range(self._index, end):
                row = self._rows[i]
                if self._mode == "meshes":
                    _obj, loaded = create_placement_mesh_or_empty(
                        row,
                        map_name=self._map_name,
                        collection=batch_coll,
                        pioneer_root=self._pioneer_root,
                        mesh_cache=self._mesh_cache,
                        resolve_cache=self._resolve_cache,
                        extra_roots=self._extra_roots,
                        mesh_by_asset=self._mesh_by_asset,
                        mesh_exports=self._mesh_exports,
                        resolve_lookup=self._resolve_lookup,
                    )
                    if loaded:
                        self._meshes_loaded += 1
                    else:
                        self._meshes_missing += 1
                else:
                    create_placement_empty(
                        row,
                        map_name=self._map_name,
                        collection=batch_coll,
                        missing_mesh=False,
                    )
                self._created += 1
            self._index = end
            self._batch_idx += 1
            total = len(self._rows)
            status = (
                f"Map Placement: {self._created}/{total} "
                f"({100.0 * self._created / max(total, 1):.1f}%)"
            )
            if self._mode == "meshes":
                unique = sum(1 for value in self._mesh_cache.values() if value)
                status += (
                    f" | meshes {self._meshes_loaded} missing {self._meshes_missing}"
                    f" unique {unique}"
                )
            context.workspace.status_text_set(status)
            # Throttle area redraws — full-screen redraw every tick was a major cost.
            self._progress_tick = int(getattr(self, "_progress_tick", 0) or 0) + 1
            if self._progress_tick % 4 == 0 or self._index >= total:
                for area in context.screen.areas:
                    if area.type in {"VIEW_3D", "OUTLINER", "PROPERTIES"}:
                        area.tag_redraw()

            if self._index >= total:
                self.cancel(context)
                # Objects are far from origin (Unreal cm) — fix clip + frame view
                try:
                    tagged = [
                        o for o in context.scene.objects
                        if o.get("arc_map") == self._map_name
                    ]
                    configure_viewport_for_map(context, tagged)
                except Exception:
                    pass
                post_note = ""
                if self._mode == "meshes":
                    try:
                        post = finalize_map_import_postprocess(
                            self._map_name,
                            csv_path=getattr(context.scene, "arc_placement_csv", "") or "",
                        )
                        g = post.get("groups") or {}
                        land = post.get("landscape") or {}
                        post_note = (
                            f" · grouped={g.get('foliage_moved', 0)}+"
                            f"{g.get('moved', 0)} · WP land={land.get('wp_shown', 0)}"
                        )
                    except Exception:
                        pass
                    msg = (
                        f"Stage 1 done — {self._created} placements, "
                        f"{self._meshes_loaded} with mesh, {self._meshes_missing} empties"
                        f"{post_note}. "
                        f"Next: Stage 2: Apply Map Materials."
                    )
                    level = {"WARNING"} if self._meshes_loaded == 0 else {"INFO"}
                    self.report(level, msg)
                else:
                    self.report(
                        {"INFO"},
                        f"Imported {self._created} empties. "
                        f"Framed view + raised clip end (map is in Unreal cm).",
                    )
                return {"FINISHED"}

        return {"RUNNING_MODAL"}

    def cancel(self, context):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        context.workspace.status_text_set(None)

    def invoke_batch(self, context, mode: str):
        if not self._setup(context, mode):
            return {"CANCELLED"}
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        self.report(
            {"INFO"},
            f"Importing {len(self._rows)} placements in batches of {self._batch} (ESC to cancel)",
        )
        return {"RUNNING_MODAL"}


class ARC_OT_ImportPlacementEmpties(bpy.types.Operator, ARC_OT_ImportPlacementBase):
    bl_idname = "arc.import_placement_empties"
    bl_label = "Import Placement Empties"
    bl_description = "Batched import of placement empties from CSV (ESC cancels)"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return self.invoke_batch(context, "empties")

    def execute(self, context):
        return self.invoke(context, None)


class ARC_OT_ImportPlacementMeshes(bpy.types.Operator, ARC_OT_ImportPlacementBase):
    bl_idname = "arc.import_placement_meshes"
    bl_label = "Stage 1: Import Map Geometry"
    bl_description = (
        "Stage 1 — batched geometry only: find UEModel/FModel .uemodel/.psk/.pskx for each "
        "placement. Materials are deferred to Stage 2 (Apply Map Materials). "
        "Missing meshes become marked empties"
    )
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return self.invoke_batch(context, "meshes")

    def execute(self, context):
        return self.invoke(context, None)


class ARC_OT_ImportPlacementInstanced(bpy.types.Operator):
    """Stage 1 variant that collapses repeated assets into geometry-nodes instances."""

    bl_idname = "arc.import_placement_instanced"
    bl_label = "Stage 1: Import Map Geometry (Instanced)"
    bl_description = (
        "Stage 1 — import each unique mesh once and instance it onto a point cloud of "
        "placements. A map becomes one object per unique asset instead of one per "
        "placement, which keeps large maps interactive. ESC cancels"
    )
    bl_options = {"REGISTER", "UNDO"}

    _timer = None
    _phase: str = "resolve"
    _map_name: str = ""
    _pioneer_root: str = ""
    _rows_by_asset: dict = {}
    _asset_keys: list = []
    _asset_index: int = 0
    _by_mesh: dict = {}
    _mesh_keys: list = []
    _mesh_index: int = 0
    _missing_rows: list = []
    _root_coll = None
    _src_coll = None
    _inst_coll = None
    _node_group = None
    _instancers: int = 0
    _instanced_rows: int = 0
    _failed: int = 0
    _total_rows: int = 0
    _extra_roots: list = []
    _mesh_by_asset: dict = {}
    _mesh_exports: list = []
    _resolve_lookup: dict = {}
    _resolved: int = 0
    _resolve_miss_samples: list = []
    _progress_tick: int = 0
    _src_layer_ready: bool = False
    # Larger batches after O(1) manifest lookup; UEModel import still dominates build.
    _RESOLVE_BATCH = 200
    _BUILD_BATCH = 6
    _UNIQUE_BATCH = 80
    _unique_job = None
    _unique_assets: int = 0

    def invoke(self, context, event):
        scene = context.scene
        csv_path = bpy.path.abspath(getattr(scene, "arc_placement_csv", "") or "")
        rows = load_placements_csv(csv_path)
        if not rows:
            self.report({"ERROR"}, "No placements CSV loaded — set Map Placement CSV path")
            return {"CANCELLED"}
        rows, dropped_dupes = dedupe_placement_rows(rows)
        self._dropped_dupes = dropped_dupes

        resolve_ctx = placement_mesh_resolve_context(scene, csv_path)
        self._pioneer_root = resolve_ctx.get("pioneer_root") or ""
        self._extra_roots = list(resolve_ctx.get("extra_roots") or [])
        self._mesh_by_asset = dict(resolve_ctx.get("mesh_by_asset") or {})
        self._mesh_exports = list(resolve_ctx.get("mesh_exports") or [])
        self._resolve_lookup = dict(resolve_ctx.get("resolve_lookup") or {})
        self._resolve_ctx = resolve_ctx
        if not _has_mesh_resolve_sources(resolve_ctx):
            self.report({"ERROR"}, _missing_mesh_source_message(resolve_ctx))
            return {"CANCELLED"}

        try:
            self._node_group = ensure_map_instancer_node_group()
        except Exception as exc:
            self.report({"ERROR"}, f"Could not build instancer node group: {exc}")
            return {"CANCELLED"}

        self._map_name = (getattr(scene, "arc_placement_map", "") or "").strip()
        if not self._map_name or self._map_name == "NONE":
            self._map_name = (getattr(scene, "arc_placement_map_name", "") or "").strip()
        if not self._map_name:
            parent = os.path.basename(os.path.dirname(csv_path))
            self._map_name = parent or "Map"

        self._rows_by_asset = {}
        self._missing_rows = []
        for row in rows:
            asset_path = (row.get("asset_path") or "").strip()
            if not asset_path:
                self._missing_rows.append(row)
                continue
            # Keep SplineMesh out of StaticMesh/ISM buckets so Fast import does not
            # instance undeformed pipes/roads together with deformed spline rows.
            kind = (row.get("asset_kind") or "StaticMesh").strip() or "StaticMesh"
            if is_spline_mesh_row(row):
                kind = "SplineMesh"
            bucket_key = f"{kind}\0{asset_path}"
            self._rows_by_asset.setdefault(bucket_key, []).append(row)

        self._asset_keys = list(self._rows_by_asset)
        self._asset_index = 0
        self._by_mesh = {}
        self._mesh_keys = []
        self._mesh_index = 0
        self._phase = "resolve"
        self._instancers = 0
        self._instanced_rows = 0
        self._failed = 0
        self._resolved = 0
        self._resolve_miss_samples = []
        self._total_rows = len(rows)
        self._progress_tick = 0
        self._src_layer_ready = False
        self._unique_job = None
        self._unique_assets = 0

        self._root_coll = ensure_collection(f"{self._map_name}_Placements")
        self._src_coll = ensure_collection(
            f"{self._map_name}_InstanceSources", parent=self._root_coll
        )
        self._inst_coll = ensure_collection(
            f"{self._map_name}_Instanced", parent=self._root_coll
        )
        self._src_coll.hide_render = True
        self._ensure_src_collection_evaluable()

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        self.report(
            {"INFO"},
            f"Instanced import: {self._total_rows} placements across "
            f"{len(self._asset_keys)} unique assets (ESC to cancel)",
        )
        return {"RUNNING_MODAL"}

    def execute(self, context):
        return self.invoke(context, None)

    def modal(self, context, event):
        if event.type == "ESC":
            self._cleanup(context)
            self.report(
                {"WARNING"},
                f"Instanced import cancelled — {self._instancers} instancers, "
                f"{self._instanced_rows}/{self._total_rows} placements",
            )
            return {"CANCELLED"}

        if event.type != "TIMER":
            return {"RUNNING_MODAL"}

        if self._phase == "resolve":
            self._resolve_step()
        elif self._phase == "build":
            self._build_step()
        else:
            return self._finish(context)

        self._report_progress(context)
        return {"RUNNING_MODAL"}

    def _ensure_src_collection_evaluable(self) -> None:
        """Unexclude InstanceSources once (was previously walked per SRC mesh)."""
        if self._src_layer_ready or self._src_coll is None:
            return
        try:
            self._src_coll.hide_viewport = False
            self._src_coll.hide_render = False
        except Exception:
            pass
        try:
            def _unexclude(lc, target):
                if lc.collection == target:
                    lc.exclude = False
                    return True
                for ch in lc.children:
                    if _unexclude(ch, target):
                        return True
                return False

            _unexclude(bpy.context.view_layer.layer_collection, self._src_coll)
        except Exception:
            pass
        self._src_layer_ready = True

    def _resolve_step(self) -> None:
        end = min(self._asset_index + self._RESOLVE_BATCH, len(self._asset_keys))
        for i in range(self._asset_index, end):
            bucket_key = self._asset_keys[i]
            rows = self._rows_by_asset[bucket_key]
            asset_path = bucket_key.split("\0", 1)[-1]
            kind = bucket_key.split("\0", 1)[0] if "\0" in bucket_key else ""
            try:
                mesh_file = resolve_psk_beside_package(
                    self._pioneer_root,
                    asset_path,
                    extra_roots=self._extra_roots,
                    mesh_by_asset=self._mesh_by_asset,
                    mesh_exports=self._mesh_exports,
                    resolve_lookup=self._resolve_lookup,
                )
            except Exception:
                mesh_file = None
            if not mesh_file:
                self._missing_rows.extend(rows)
                if len(self._resolve_miss_samples) < 3:
                    self._resolve_miss_samples.append(asset_path)
                continue
            self._resolved += 1
            # Distinct asset paths can resolve to the same export; merge them so a
            # shared mesh yields one instancer rather than several — except SplineMesh
            # must not merge with non-spline rows (undeformed vs baked / pose-only),
            # and StaticMeshActor rows stay separate from ISM/HISM so grouping can
            # place them under ``{Map}_StaticMeshActors``.
            key = os.path.normcase(os.path.abspath(mesh_file))
            if kind == "SplineMesh":
                key = f"spline:{key}:{asset_path}"
            elif kind == "StaticMesh":
                key = f"static:{key}"
            bucket = self._by_mesh.get(key)
            unique = needs_unique_mesh_placement(asset_path)
            if bucket is None:
                self._by_mesh[key] = {
                    "file": mesh_file,
                    "rows": list(rows),
                    "spline": kind == "SplineMesh",
                    "asset_kind": kind or "StaticMesh",
                    "unique": unique,
                    "asset_path": asset_path,
                }
            else:
                bucket["rows"].extend(rows)
                bucket["unique"] = bool(bucket.get("unique")) or unique
                # Keep the first stamped kind (static:/spline: keys already isolate).
                if not bucket.get("asset_kind"):
                    bucket["asset_kind"] = kind or "StaticMesh"
        self._asset_index = end
        if self._asset_index >= len(self._asset_keys):
            self._mesh_keys = list(self._by_mesh)
            self._mesh_index = 0
            self._phase = "build" if self._mesh_keys else "done"

    def _build_step(self) -> None:
        # Finish pending unique DecalMesh / poster / plane batches before starting the next asset.
        if getattr(self, "_unique_job", None):
            self._continue_unique_placements()
            return
        if self._mesh_index >= len(self._mesh_keys):
            self._phase = "done"
            return
        end = min(self._mesh_index + self._BUILD_BATCH, len(self._mesh_keys))
        for i in range(self._mesh_index, end):
            key = self._mesh_keys[i]
            entry = self._by_mesh[key]
            mesh_file = entry["file"]
            rows = entry["rows"]
            is_spline = bool(entry.get("spline"))
            asset_kind = str(entry.get("asset_kind") or ("SplineMesh" if is_spline else "")).strip()
            stem = os.path.splitext(os.path.basename(mesh_file))[0][:40]
            if is_spline:
                stem = f"Spline_{stem}"[:40]

            source_obj = self._ensure_source_collection(stem, mesh_file)
            if source_obj is None:
                self._missing_rows.extend(rows)
                self._failed += 1
                continue
            if entry.get("unique"):
                # One asset may have thousands of DecalMesh/plane rows — batch across ticks.
                self._unique_job = {
                    "stem": stem,
                    "source": source_obj,
                    "rows": rows,
                    "index": 0,
                    "mesh_file": mesh_file,
                    "asset_path": entry.get("asset_path") or "",
                    "asset_kind": asset_kind or "StaticMesh",
                }
                self._mesh_index = i  # resume this key after unique job finishes
                self._continue_unique_placements()
                return
            self._create_instancer(
                stem,
                source_obj,
                rows,
                mesh_file,
                is_spline=is_spline,
                asset_kind=asset_kind,
            )
        self._mesh_index = end
        if self._mesh_index >= len(self._mesh_keys):
            self._phase = "done"

    def _continue_unique_placements(self) -> None:
        job = getattr(self, "_unique_job", None)
        if not job:
            return
        rows = job["rows"]
        source = job["source"]
        mesh_file = job["mesh_file"]
        stem = job["stem"]
        asset_path = job.get("asset_path") or ""
        asset_kind = str(job.get("asset_kind") or "StaticMesh").strip() or "StaticMesh"
        start = int(job.get("index") or 0)
        batch = int(getattr(self, "_UNIQUE_BATCH", 80) or 80)
        end = min(start + batch, len(rows))

        placements = self._root_coll
        planes_coll = ensure_collection("Planes", parent=placements)
        decals_coll = ensure_collection("Decals", parent=placements)
        for i in range(start, end):
            row = rows[i]
            asset_here = (row.get("asset_path") or asset_path or "").strip()
            actor_here = (row.get("actor_name") or "").strip()
            # Planes → Planes; decals + branding posters → Decals.
            if (
                is_plane_mesh_asset(asset_here, actor_here)
                and not is_decal_mesh_asset(asset_here, actor_here)
                and not is_poster_mesh_asset(asset_here, actor_here)
            ):
                dest = planes_coll
            else:
                dest = decals_coll
            obj = create_unique_placement_from_source(
                source,
                row,
                map_name=self._map_name,
                collection=dest,
                mesh_file=mesh_file,
                index=i,
                stem=stem,
            )
            if obj is None:
                self._failed += 1
                continue
            try:
                obj["arc_asset_path"] = row.get("asset_path") or asset_path
                row_kind = (row.get("asset_kind") or asset_kind or "StaticMesh").strip()
                obj["arc_asset_kind"] = row_kind or asset_kind
            except Exception:
                pass
            self._instanced_rows += 1
        job["index"] = end
        if end >= len(rows):
            self._unique_job = None
            self._unique_assets = int(getattr(self, "_unique_assets", 0) or 0) + 1
            self._mesh_index += 1
            if self._mesh_index >= len(self._mesh_keys):
                self._phase = "done"
        else:
            self._unique_job = job

    def _ensure_source_collection(self, stem: str, mesh_file: str):
        """Import unique mesh once and return the source object used for instancing."""
        abs_mesh = os.path.normcase(os.path.abspath(mesh_file))
        path_tag = hashlib.sha1(abs_mesh.encode("utf-8")).hexdigest()[:8]
        # Blender object names max 63 chars. Keep path_tag intact so long stems
        # (Merged_SplineDistributionActor_UAID_…) cannot collide by truncation.
        max_stem = max(8, 63 - len("SRC_") - 1 - len(path_tag))
        stem_part = (stem or "mesh")[:max_stem]
        obj_name = f"SRC_{stem_part}_{path_tag}"

        def _reuse_if_same_file(candidate_name: str):
            existing = bpy.data.objects.get(candidate_name)
            if existing is None or existing.type != "MESH" or existing.data is None:
                return None
            prev = str(existing.get("arc_mesh_file") or "")
            if prev and os.path.normcase(os.path.abspath(prev)) != abs_mesh:
                return None
            return existing

        hit = _reuse_if_same_file(obj_name)
        if hit is not None:
            return hit
        # Fallback name if a stale collision still occupies the primary name
        alt_name = f"SRC_{path_tag}"
        hit = _reuse_if_same_file(alt_name)
        if hit is not None:
            return hit
        if bpy.data.objects.get(obj_name) is not None:
            obj_name = alt_name

        objs = _import_map_mesh(mesh_file)
        objs = [o for o in objs if getattr(o, "type", None) == "MESH"]
        if not objs:
            return None

        source = _join_mesh_objects(objs, name=obj_name)
        if source is None:
            return None

        # UEFormat mesh Y often disagrees with UStaticMeshSocket space; un-flip so
        # socket AbsoluteTransforms from FModel land on the visible shell surface.
        unflip_map_source_mesh_y(source)

        # Keep sources in their own collection, visible to the depsgraph (Object Info
        # needs an evaluated object). Draw as bounds so the origin pile is quiet.
        for linked in list(source.users_collection):
            linked.objects.unlink(source)
        self._src_coll.objects.link(source)
        source.location = (0.0, 0.0, 0.0)
        source.rotation_mode = "XYZ"
        source.rotation_euler = (0.0, 0.0, 0.0)
        source.scale = (1.0, 1.0, 1.0)
        source.display_type = "BOUNDS"
        source.hide_set(False)
        source.hide_viewport = False
        source.hide_select = True
        # Must stay render-visible for some Object Info evaluation paths.
        source.hide_render = False
        self._ensure_src_collection_evaluable()

        try:
            source["arc_map"] = self._map_name
            source["arc_mesh_file"] = mesh_file
            source["arc_psk_path"] = mesh_file
            source["arc_model_type"] = "map"
            source["arc_materials_pending"] = 1
            source["arc_instance_source"] = 1
        except Exception:
            pass
        return source

    def _create_instancer(
        self,
        stem: str,
        source_obj,
        rows: list,
        mesh_file: str,
        *,
        is_spline: bool = False,
        asset_kind: str = "",
    ) -> None:
        obj = build_placement_point_cloud(
            rows,
            name=f"{stem}_x{len(rows)}"[:60],
            map_name=self._map_name,
        )
        obj["arc_mesh_file"] = mesh_file
        obj["arc_asset_path"] = rows[0].get("asset_path") or ""
        # BP_WaterPlane_MinorSwamp → stamp MI_Water_MinorSwamp on shared SRC
        try:
            from . import materials as mats_mod
            pref = mats_mod.preferred_mi_from_placement_rows(rows)
            # Only water / map-decal BP overrides — never stamp PropTrim/Vent onto SRC
            if pref and mats_mod._preferred_mi_is_single_slot_override(pref):
                obj["arc_preferred_mi"] = pref
                if source_obj is not None:
                    source_obj["arc_preferred_mi"] = pref
        except Exception:
            pass
        kind = (asset_kind or "").strip()
        if is_spline:
            kind = "SplineMesh"
        elif not kind and rows:
            kind = (rows[0].get("asset_kind") or "StaticMesh").strip() or "StaticMesh"
        if kind:
            obj["arc_asset_kind"] = kind
        if is_spline:
            notes = (rows[0].get("notes") or "")
            meta = parse_spline_notes(notes)
            obj["arc_spline_undeformed"] = 0 if meta.get("baked") else 1
            if meta.get("baked"):
                obj["arc_spline_baked"] = 1
            else:
                obj["arc_spline_note"] = (
                    "SplineMesh pose only — re-export Map Placements + Meshes from FModel "
                    "to bake along-spline deformation"
                )
        # Hide the raw point cloud; instances are what you want to see.
        try:
            obj.show_instancer_for_viewport = False
            obj.show_instancer_for_render = False
        except Exception:
            pass
        self._inst_coll.objects.link(obj)

        modifier = obj.modifiers.new(name="ARC Instancer", type="NODES")
        modifier.node_group = self._node_group
        if not _set_modifier_socket(modifier, "Source", source_obj):
            # Without a source object the modifier only shows points — surface the failure.
            self._failed += 1
            obj["arc_instancer_error"] = "failed_to_bind_source"
            return
        try:
            obj["arc_instance_source_name"] = source_obj.name
        except Exception:
            pass
        self._instancers += 1
        self._instanced_rows += len(rows)

    def _report_progress(self, context) -> None:
        if self._phase == "resolve":
            status = (
                f"Instanced import — resolving assets "
                f"{self._asset_index}/{len(self._asset_keys)}"
            )
        else:
            status = (
                f"Instanced import — {self._mesh_index}/{len(self._mesh_keys)} meshes, "
                f"{self._instanced_rows}/{self._total_rows} placements"
            )
        context.workspace.status_text_set(status)
        self._progress_tick = int(getattr(self, "_progress_tick", 0) or 0) + 1
        # Status bar updates every tick; full area redraws are expensive — throttle.
        if self._progress_tick % 3 == 0 or self._phase == "done":
            for area in context.screen.areas:
                if area.type in {"VIEW_3D", "OUTLINER", "PROPERTIES"}:
                    area.tag_redraw()

    def _finish(self, context):
        if self._missing_rows:
            marker = build_placement_point_cloud(
                self._missing_rows,
                name=f"{self._map_name}_MissingPlacements"[:60],
                map_name=self._map_name,
            )
            marker["arc_missing_mesh"] = 1
            # Keep missing markers visible as points so gaps stay obvious.
            self._inst_coll.objects.link(marker)

        # Keep InstanceSources evaluable (Object Info) but quiet: bounds + layer eye-hide.
        # Do NOT collection.hide_viewport / exclude — that stops depsgraph evaluation.
        try:
            for obj in self._src_coll.objects:
                if obj.get("arc_instance_source"):
                    obj.display_type = "BOUNDS"
                    obj.hide_set(False)
                    obj.hide_viewport = False
                    obj.hide_render = False
                    obj.hide_select = True
        except Exception:
            pass
        try:
            def _eye_hide(lc, target):
                if lc.collection == target:
                    lc.exclude = False
                    # Eye-hide only — monitor/exclude would break Object Info.
                    lc.hide_viewport = True
                    return True
                for ch in lc.children:
                    if _eye_hide(ch, target):
                        return True
                return False

            _eye_hide(bpy.context.view_layer.layer_collection, self._src_coll)
        except Exception:
            pass
        # Only rebind GN Source when missing — avoid rewriting every modifier each import.
        try:
            ng = self._node_group or ensure_map_instancer_node_group()
            for obj in self._inst_coll.objects:
                if not obj.get("arc_placement_instancer"):
                    continue
                mod = next((m for m in obj.modifiers if m.type == "NODES"), None)
                if mod is None:
                    continue
                if mod.node_group != ng:
                    mod.node_group = ng
                src_name = obj.get("arc_instance_source_name") or ""
                if not src_name:
                    continue
                # Skip if socket already points at the expected source.
                try:
                    ident = _node_group_input_identifier(ng, "Source")
                    cur = None
                    if ident:
                        try:
                            cur = mod[ident]
                        except (KeyError, TypeError):
                            cur = None
                    if cur is not None and getattr(cur, "name", None) == src_name:
                        continue
                except Exception:
                    pass
                source = bpy.data.objects.get(src_name)
                if source is not None:
                    _set_modifier_socket(mod, "Source", source)
        except Exception:
            pass
        self._cleanup(context)

        try:
            configure_viewport_for_map(
                context,
                [o for o in self._inst_coll.objects],
            )
        except Exception:
            pass

        post: dict = {}
        try:
            # Pass the stamp used on objects — do not let CSV folder leaf win alone.
            post = finalize_map_import_postprocess(
                self._map_name,
                csv_path=getattr(context.scene, "arc_placement_csv", "") or "",
            )
        except Exception as exc:
            post = {"error": str(exc)}

        level = {"INFO"} if self._instancers else {"WARNING"}
        spline_n = sum(
            1
            for o in self._inst_coll.objects
            if o.get("arc_asset_kind") == "SplineMesh"
        )
        undeformed_n = sum(
            1
            for o in self._inst_coll.objects
            if o.get("arc_spline_undeformed")
        )
        spline_note = ""
        if spline_n:
            spline_note = f" · {spline_n} SplineMesh group(s)"
            if undeformed_n:
                spline_note += (
                    f" ({undeformed_n} undeformed — re-export FModel + Meshes bake)"
                )
        if self._instancers == 0 and self._total_rows > 0:
            self.report(
                {"ERROR"},
                _stage1_zero_mesh_message(
                    getattr(self, "_resolve_ctx", None),
                    total_rows=self._total_rows,
                    resolved=int(getattr(self, "_resolved", 0) or 0),
                    import_failed=int(getattr(self, "_failed", 0) or 0),
                    unresolved_assets=list(getattr(self, "_resolve_miss_samples", None) or []),
                ),
            )
        else:
            dup_note = ""
            dropped = int(getattr(self, "_dropped_dupes", 0) or 0)
            if dropped:
                dup_note = f", dropped {dropped} exact CSV duplicate row(s)"
            g = post.get("groups") or {}
            land = post.get("landscape") or {}
            ground = post.get("ground") or {}
            post_note = (
                f" · grouped foliage={g.get('foliage_moved', 0)} "
                f"helpers={g.get('hidden', 0)}"
                f" · WP landscape={land.get('wp_shown', 0)}"
            )
            if ground.get("placeholder"):
                post_note += " · flat city ground (PNG not city-aligned)"
            elif ground.get("displaced"):
                post_note += " · displaced city-aligned heightmap"
            unique_n = int(getattr(self, "_unique_assets", 0) or 0)
            unique_note = f" · {unique_n} DecalMesh asset(s) as unique objects" if unique_n else ""
            self.report(
                level,
                f"Instanced Stage 1 done — {self._instancers} unique assets instancing "
                f"{self._instanced_rows} placements, {len(self._missing_rows)} unresolved"
                f"{f', {self._failed} bind/import failures' if self._failed else ''}"
                f"{dup_note}{spline_note}{unique_note}{post_note}. "
                f"Next: Stage 2: Apply Map Materials.",
            )
        return {"FINISHED"}

    def _cleanup(self, context):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        context.workspace.status_text_set(None)


class ARC_OT_ApplyMapMaterials(bpy.types.Operator):
    """Stage 2 — apply Arc materials to already-imported map meshes (batched)."""
    bl_idname = "arc.apply_map_materials"
    bl_label = "Stage 2: Apply Map Materials"
    bl_description = (
        "Stage 2 — rebuild materials on map meshes imported in Stage 1 "
        "(uses stamped PSK paths + shared MI cache). ESC cancels"
    )
    bl_options = {"REGISTER", "UNDO"}

    only_selected: bpy.props.BoolProperty(
        name="Only Selected",
        description="Only process currently selected map meshes (default: whole map)",
        default=False,
    )
    force_all: bpy.props.BoolProperty(
        name="Force All",
        description=(
            "Re-apply even when arc_materials_pending=0. Clears leaked preferred_mi "
            "stamps, invalidates stale PropTrim/trim graphs, and rebuilds every slot "
            "from SM JSON StaticMaterials"
        ),
        default=True,
    )

    _timer = None
    _targets: list = []
    _index: int = 0
    _batch: int = 40
    _ok: int = 0
    _fail: int = 0
    _cached: int = 0
    _skipped: int = 0
    _mat_cache: dict = {}
    _map_name: str = ""
    _ui_tick: int = 0
    _fail_samples: list = []
    _skip_samples: list = []
    _already_done: int = 0

    def invoke(self, context, event):
        scene = context.scene
        self._map_name = (getattr(scene, "arc_placement_map", "") or "").strip()
        if not self._map_name or self._map_name == "NONE":
            self._map_name = (getattr(scene, "arc_placement_map_name", "") or "").strip()

        targets = collect_map_mesh_targets(
            context, self._map_name, only_selected=bool(self.only_selected)
        )
        already_done = 0
        if self.force_all:
            from . import materials as mats_force
            # Cheap flags only — do NOT invalidate shared MI / wipe slots here.
            # That used to freeze Blender for hundreds of meshes before the modal
            # even started; force-rebuild is once-per-MI inside setup_map_material.
            for obj in targets:
                try:
                    obj["arc_materials_pending"] = 1
                    obj["arc_force_material_rebuild"] = 1
                except Exception:
                    pass
                try:
                    mats_force.clear_leaked_preferred_mi(obj)
                except Exception:
                    pass
            self._targets = list(targets)
            already_done = 0
        else:
            pending = [
                o for o in targets if int(o.get("arc_materials_pending", 1) or 0) == 1
            ]
            already_done = len(targets) - len(pending)
            # Tiny pending leftover after a selection-limited / early run: also requeue
            # SRC meshes that still lack an Arc MI stamp.
            if pending and already_done > 0 and len(pending) < max(32, len(targets) // 10):
                requeued = list(pending)
                for obj in targets:
                    if obj in requeued:
                        continue
                    if not _map_mesh_has_arc_materials(obj):
                        try:
                            obj["arc_materials_pending"] = 1
                        except Exception:
                            pass
                        requeued.append(obj)
                self._targets = requeued
                already_done = len(targets) - len(requeued)
            else:
                self._targets = pending if pending else list(targets)
                if not pending:
                    already_done = 0

        self._already_done = max(0, already_done)
        if not self._targets:
            self.report(
                {"WARNING"},
                "No map meshes found — run Stage 1 (Import Map Geometry) first, "
                "or select map meshes",
            )
            return {"CANCELLED"}

        # Drop stale path caches (e.g. prior run with MapPlacements-only pioneer root)
        # then re-index already-built shared MIs + warm FMDex once.
        from . import materials as mats_mod
        from . import fmdex
        from . import utils as utils_mod
        mats_mod.clear_material_session_caches()
        mats_mod.warm_shared_mi_material_cache()
        try:
            fmdex.ensure_loaded()
        except Exception:
            pass
        try:
            content_dirs = utils_mod.get_content_dirs()
            print(
                f"Arc Raiders Stage 2: map={self._map_name} targets={len(self._targets)} "
                f"already_done={self._already_done} content roots ({len(content_dirs)}): "
                + "; ".join(content_dirs[:4])
            )
        except Exception:
            pass

        self._index = 0
        base = int(getattr(scene, "arc_placement_batch_size", 100) or 100)
        self._batch = max(8, min(80, base // 2 or 40))
        self._ok = 0
        self._fail = 0
        self._cached = 0
        self._skipped = 0
        self._mat_cache = {}
        self._fail_samples = []
        self._skip_samples = []
        self._ui_tick = 0
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        self.report(
            {"INFO"},
            f"Stage 2: applying materials to {len(self._targets)} mesh(es) "
            f"(batch {self._batch}, already had mats≈{self._already_done}, ESC to cancel)",
        )
        return {"RUNNING_MODAL"}

    def execute(self, context):
        return self.invoke(context, None)

    def modal(self, context, event):
        if event.type in {"ESC"}:
            self._cleanup(context)
            self.report(
                {"WARNING"},
                f"Stage 2 cancelled ({self._ok + self._cached}/{len(self._targets)} done, "
                f"{self._fail} failed, {self._skipped} skipped, {self._cached} reused)",
            )
            return {"CANCELLED"}

        if event.type != "TIMER":
            return {"RUNNING_MODAL"}

        from . import operators as ops
        from . import materials as mats_mod

        end = min(self._index + self._batch, len(self._targets))
        for i in range(self._index, end):
            obj = self._targets[i]
            try:
                _ = obj.name
            except ReferenceError:
                self._fail += 1
                continue

            psk = ""
            for key in ("arc_psk_path", "arc_mesh_file"):
                raw = obj.get(key) or ""
                if raw:
                    psk = bpy.path.abspath(str(raw))
                    if os.path.isfile(psk) or os.path.isdir(os.path.dirname(psk)):
                        break
                    psk = ""

            asset = str(obj.get("arc_asset_path") or "")
            if mats_mod.is_engine_or_placeholder_mesh(psk, asset, obj.name):
                self._skipped += 1
                try:
                    obj["arc_materials_pending"] = 0
                    obj["arc_materials_skipped"] = "engine_or_placeholder"
                except Exception:
                    pass
                if len(self._skip_samples) < 6:
                    self._skip_samples.append(f"{obj.name}: engine/placeholder")
                continue

            if not psk:
                # Unstamped imports (someone else's map): resolve SM_* by mesh/object name
                psk, matched = resolve_map_asset_path_for_object(obj)
                if psk:
                    try:
                        obj["arc_psk_path"] = psk
                        obj["arc_model_type"] = "map"
                        if matched:
                            obj["arc_name_resolve"] = matched
                    except Exception:
                        pass
                else:
                    folder = ""
                    fixed = mats_mod.fix_object_materials_from_mi_slots(obj, folder)
                    if fixed:
                        self._ok += 1
                        try:
                            obj["arc_materials_pending"] = 0
                        except Exception:
                            pass
                    else:
                        self._skipped += 1
                        try:
                            obj["arc_materials_pending"] = 0
                            obj["arc_materials_skipped"] = "no_psk"
                        except Exception:
                            pass
                        if len(self._skip_samples) < 6:
                            self._skip_samples.append(f"{obj.name}: no psk path")
                    continue

            cache_key = mats_mod.map_material_cache_key(psk) or os.path.normcase(
                os.path.normpath(psk)
            )
            # Never reuse another asset's materials: cache key must include the
            # mesh basename so PropTrim/Vent SRC results cannot paint chairs.
            psk_base = os.path.splitext(os.path.basename(psk))[0].lower()
            cached_mats = self._mat_cache.get(cache_key)
            if cached_mats:
                cached_ok = True
                # Guard: if the representative object stamped a different mesh stem, skip
                try:
                    prev = str((cached_mats[0].get("arc_cache_psk_stem") if cached_mats and cached_mats[0] else "") or "")
                    if prev and prev != psk_base:
                        cached_ok = False
                except Exception:
                    pass
                if cached_ok and ops.assign_cached_materials(obj, cached_mats):
                    self._cached += 1
                    try:
                        obj["arc_psk_path"] = psk
                        obj["arc_materials_pending"] = 0
                        if obj.get("arc_force_material_rebuild"):
                            del obj["arc_force_material_rebuild"]
                    except Exception:
                        pass
                    continue

            if not str(obj.get("arc_model_type", "") or "").strip():
                try:
                    obj["arc_model_type"] = "map"
                except Exception:
                    pass

            try:
                status = ops.apply_materials_to_object(obj, psk)
            except Exception as exc:
                status = f"error ({exc})"
                print(f"Arc Raiders Stage 2: {obj.name}: {exc}")
            mats = ops.snapshot_object_materials(obj)
            if (
                mats
                and status not in ("unresolved", "skipped (not a mesh)")
                and not str(status).startswith("error")
            ):
                # Stamp stem on first mat for cache-guard (custom prop, non-fatal)
                try:
                    if mats[0] is not None:
                        mats[0]["arc_cache_psk_stem"] = psk_base
                except Exception:
                    pass
                self._mat_cache[cache_key] = mats
                self._ok += 1
                try:
                    obj["arc_psk_path"] = psk
                    obj["arc_materials_pending"] = 0
                    if obj.get("arc_force_material_rebuild"):
                        del obj["arc_force_material_rebuild"]
                except Exception:
                    pass
                continue

            folder = os.path.dirname(psk)
            fixed = mats_mod.fix_object_materials_from_mi_slots(obj, folder)
            if fixed:
                mats = ops.snapshot_object_materials(obj)
                if mats:
                    self._mat_cache[cache_key] = mats
                self._ok += 1
                try:
                    obj["arc_materials_pending"] = 0
                except Exception:
                    pass
                continue

            slots = mats_mod._parse_sk_material_slots(psk)
            resolved = sum(1 for _n, _s, p in slots if p)
            # No SM/SK JSON, or SM only has Engine WorldGridMaterial (DecalMesh cards):
            # soft-skip unless a BP preferred MI should have covered this (retried above).
            if not resolved:
                self._skipped += 1
                why = "no_mi_json" if not slots else "worldgrid_only"
                try:
                    obj["arc_materials_pending"] = 0
                    obj["arc_materials_skipped"] = why
                except Exception:
                    pass
                if len(self._skip_samples) < 6:
                    self._skip_samples.append(
                        f"{obj.name}: {why} sk={len(slots)} "
                        f"psk={os.path.basename(psk)}"
                    )
                continue

            self._fail += 1
            if len(self._fail_samples) < 8:
                self._fail_samples.append(
                    f"{obj.name}: status={status} sk_slots={len(slots)} "
                    f"mi_json={resolved} psk={os.path.basename(psk)}"
                )

        self._index = end
        total = len(self._targets)
        done = self._ok + self._cached + self._fail + self._skipped
        self._ui_tick += 1
        if self._ui_tick == 1 or self._ui_tick % 4 == 0 or self._index >= total:
            context.workspace.status_text_set(
                f"Stage 2 materials: {done}/{total} "
                f"({100.0 * done / max(total, 1):.1f}%) | "
                f"built {self._ok} reused {self._cached} "
                f"skip {self._skipped} fail {self._fail}"
            )
            for area in context.screen.areas:
                if area.type in {"VIEW_3D", "STATUSBAR"}:
                    area.tag_redraw()

        if self._index >= total:
            # Post-pass: fix remaining white / unassigned (water planes, empty slots)
            white_fixed = 0
            white_samples = []
            try:
                for obj in self._targets:
                    try:
                        _ = obj.name
                    except ReferenceError:
                        continue
                    need, why = mats_mod.object_needs_material_repair(obj)
                    if not need:
                        continue
                    psk = ""
                    for key in ("arc_psk_path", "arc_mesh_file"):
                        raw = obj.get(key) or ""
                        if raw:
                            psk = bpy.path.abspath(str(raw))
                            if os.path.isfile(psk) or os.path.isdir(os.path.dirname(psk)):
                                break
                            psk = ""
                    info = mats_mod.fix_white_unassigned_materials(obj, psk)
                    if info.get("ok") and info.get("reason") != "already_ok":
                        white_fixed += 1
                        if len(white_samples) < 6:
                            white_samples.append(
                                f"{obj.name}:{info.get('why_white')}"
                                f"→{info.get('mi_stem') or info.get('matched')}"
                            )
                    elif len(white_samples) < 6 and info.get("reason") not in ("", "already_ok"):
                        white_samples.append(
                            f"{obj.name}:{info.get('why_white')}/{info.get('reason')}"
                        )
            except Exception as exc:
                print(f"Arc Raiders Stage 2 white-repair pass: {exc}")

            self._cleanup(context)
            level = {"INFO"} if self._fail == 0 else {"WARNING"}
            sample = ""
            samples = getattr(self, "_fail_samples", None) or []
            skips = getattr(self, "_skip_samples", None) or []
            if samples:
                sample = " | fail e.g. " + " · ".join(samples[:2])
            elif skips:
                sample = " | skip e.g. " + " · ".join(skips[:2])
            if white_fixed:
                sample += f" | white-fixed {white_fixed}"
                if white_samples:
                    sample += " e.g. " + " · ".join(white_samples[:2])
            for line in samples:
                print(f"Arc Raiders Stage 2 fail: {line}")
            for line in skips:
                print(f"Arc Raiders Stage 2 skip: {line}")
            for line in white_samples:
                print(f"Arc Raiders Stage 2 white-repair: {line}")

            # Bake shore proximity onto water planes (attribute drives Water↔Shore)
            shore_note = ""
            try:
                prox = mats_mod.refresh_water_shore_proximity(
                    context, objects=self._targets, densify=True,
                )
                if prox.get("water"):
                    shore_note = (
                        f" | shore-prox {prox['water']} water "
                        f"vs {prox['targets']} targets"
                    )
                    print(
                        f"Arc Raiders Stage 2 shore proximity: water={prox['water']} "
                        f"targets={prox['targets']} verts={prox['verts']} "
                        f"densified={prox['densified']}"
                    )
            except Exception as exc:
                print(f"Arc Raiders Stage 2 shore proximity: {exc}")

            self.report(
                level,
                f"Stage 2 done — built {self._ok}, reused {self._cached}, "
                f"skipped_no_mi {self._skipped}, failed {self._fail} "
                f"(map={self._map_name or 'any'}, prior_done≈{self._already_done})"
                f"{sample}{shore_note}",
            )
            return {"FINISHED"}

        return {"RUNNING_MODAL"}

    def _cleanup(self, context):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        context.workspace.status_text_set(None)


class ARC_OT_ApplyMaterialsByMeshName(bpy.types.Operator):
    """Materials-only apply: match object/mesh names → Arc MI (no mesh re-import)."""
    bl_idname = "arc.apply_materials_by_mesh_name"
    bl_label = "Apply Materials by Mesh Name"
    bl_description = (
        "Apply Arc map materials to existing meshes by object or mesh datablock name "
        "(strips PTS_ / .001). Lightweight shared-MI assign by default; optional Force "
        "Rebuild. Target a collection (e.g. FrozenTrail_Props.002) or selection"
    )
    # No UNDO — batching 600+ slot assigns into one undo step freezes Blender on finish.
    bl_options = {"REGISTER"}

    collection_name: bpy.props.StringProperty(
        name="Collection",
        description=(
            "Collection to process (recursive). Leave empty to use selection, "
            "or selection if any meshes are selected"
        ),
        default="FrozenTrail_Props.002",
    )
    prefer_selection: bpy.props.BoolProperty(
        name="Prefer Selection",
        description="If any meshes are selected, use those instead of the collection",
        default=True,
    )
    dry_run: bpy.props.BoolProperty(
        name="Dry Run",
        description="Only resolve name→export paths; do not build materials",
        default=False,
    )
    force_rebuild: bpy.props.BoolProperty(
        name="Force Rebuild",
        description=(
            "Force All style: rebuild MI graphs once per MI path. Default is "
            "lightweight shared-MI slot assign (much faster on large collections)"
        ),
        default=False,
    )
    skip_already_assigned: bpy.props.BoolProperty(
        name="Skip Already Assigned",
        description="Skip meshes that already have Arc MI stamps on all slots",
        default=True,
    )

    _timer = None
    _targets: list = []
    _index: int = 0
    _batch: int = 32
    _ok: int = 0
    _fail: int = 0
    _skip: int = 0
    _cached: int = 0
    _already: int = 0
    _mat_cache: dict = {}
    _path_cache: dict = {}
    _fail_samples: list = []
    _skip_samples: list = []
    _ok_samples: list = []
    _ui_tick: int = 0
    _prev_global_undo: bool | None = None
    _progress_started: bool = False

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=440)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "collection_name")
        layout.prop(self, "prefer_selection")
        layout.prop(self, "dry_run")
        layout.prop(self, "force_rebuild")
        layout.prop(self, "skip_already_assigned")
        layout.label(
            text="Default: shared MI assign from SM JSON (cached). No mesh import.",
            icon="INFO",
        )

    def execute(self, context):
        from . import materials as mats_mod
        from . import fmdex
        from . import utils as utils_mod

        root = utils_mod.get_pioneer_root()
        if not root or not os.path.isdir(root):
            self.report({"ERROR"}, "Set PioneerGame Folder in Settings first")
            return {"CANCELLED"}

        targets: list = []
        selected = [o for o in context.selected_objects if o.type == "MESH"]
        if self.prefer_selection and selected:
            seen = set()
            for obj in selected:
                key = obj.data.as_pointer() if obj.data else obj.as_pointer()
                if key in seen:
                    continue
                seen.add(key)
                targets.append(obj)
        elif (self.collection_name or "").strip():
            targets = collect_meshes_from_collection(self.collection_name.strip())
            if not targets:
                self.report(
                    {"WARNING"},
                    f"Collection '{self.collection_name}' not found or has no meshes",
                )
                return {"CANCELLED"}
        else:
            targets = selected

        if not targets:
            self.report(
                {"WARNING"},
                "No meshes — select objects or set a collection name",
            )
            return {"CANCELLED"}

        # Keep image/shared-MI caches warm — wiping them forced 653 cold texture loads.
        # Only reset Force-All once-per-MI tracking when explicitly rebuilding.
        if self.force_rebuild:
            try:
                mats_mod._FORCE_REBUILT_MI_PATHS.clear()
            except Exception:
                pass
        mats_mod.warm_shared_mi_material_cache()
        try:
            fmdex.ensure_loaded()
        except Exception:
            pass

        if self.dry_run:
            import time as _time
            t0 = _time.perf_counter()
            resolved = 0
            missing = 0
            samples_ok = []
            samples_miss = []
            report_rows = []
            path_cache: dict[str, tuple] = {}
            for i, obj in enumerate(targets):
                cache_key = ""
                try:
                    stems = mesh_lookup_stems_for_object(obj)
                    cache_key = "|".join(s.lower() for s in stems[:4])
                except Exception:
                    stems = []
                if cache_key and cache_key in path_cache:
                    path, stem = path_cache[cache_key]
                else:
                    path, stem = resolve_map_asset_path_for_object(obj)
                    if cache_key:
                        path_cache[cache_key] = (path, stem)
                if path:
                    resolved += 1
                    mi_hint = ""
                    try:
                        slots = mats_mod._parse_sk_material_slots(path, context="map")
                        mis = [s for _n, s, p in slots if p]
                        mi_hint = ",".join(mis[:3])
                    except Exception:
                        pass
                    if len(samples_ok) < 12:
                        samples_ok.append(
                            f"{obj.name} → {os.path.basename(path)}"
                            + (f" [{mi_hint}]" if mi_hint else "")
                        )
                    report_rows.append(
                        {
                            "obj": obj.name,
                            "mesh": getattr(obj.data, "name", ""),
                            "stem": stem,
                            "path": path,
                            "mi": mi_hint,
                        }
                    )
                else:
                    missing += 1
                    if len(samples_miss) < 12:
                        samples_miss.append(obj.name)
                    report_rows.append(
                        {
                            "obj": obj.name,
                            "mesh": getattr(obj.data, "name", ""),
                            "stem": stem,
                            "path": "",
                            "mi": "",
                        }
                    )
                if (i + 1) % 100 == 0:
                    print(
                        f"Arc Raiders name-apply dry-run: {i + 1}/{len(targets)} "
                        f"({resolved} ok, {missing} miss)"
                    )
            out = os.path.join(
                os.path.dirname(__file__),
                "_apply_materials_by_name_dryrun.json",
            )
            try:
                with open(out, "w", encoding="utf-8") as fh:
                    json.dump(
                        {
                            "collection": self.collection_name,
                            "total": len(targets),
                            "resolved": resolved,
                            "missing": missing,
                            "elapsed_s": round(_time.perf_counter() - t0, 3),
                            "samples_ok": samples_ok,
                            "samples_miss": samples_miss,
                            "rows": report_rows,
                        },
                        fh,
                        indent=2,
                    )
            except OSError as exc:
                print(f"Arc Raiders name-apply dry-run write failed: {exc}")
            elapsed = _time.perf_counter() - t0
            self.report(
                {"INFO"} if missing == 0 else {"WARNING"},
                f"Dry run: {resolved}/{len(targets)} resolved, {missing} missing "
                f"in {elapsed:.1f}s (wrote {os.path.basename(out)})",
            )
            for line in samples_ok[:6]:
                print(f"Arc Raiders name-apply OK: {line}")
            for line in samples_miss[:6]:
                print(f"Arc Raiders name-apply MISS: {line}")
            return {"FINISHED"}

        self._targets = list(targets)
        self._index = 0
        base = int(getattr(context.scene, "arc_placement_batch_size", 100) or 100)
        # Smaller batches when force-rebuilding (texture work); larger for assign-only.
        if self.force_rebuild:
            self._batch = max(4, min(16, base // 4 or 8))
        else:
            self._batch = max(16, min(64, base // 2 or 32))
        self._ok = 0
        self._fail = 0
        self._skip = 0
        self._cached = 0
        self._already = 0
        self._mat_cache = {}
        self._path_cache = {}
        self._fail_samples = []
        self._skip_samples = []
        self._ok_samples = []
        self._ui_tick = 0
        self._progress_started = False

        # Disable global undo for the batch — restores in cleanup.
        self._prev_global_undo = None
        try:
            prefs = context.preferences.edit
            self._prev_global_undo = bool(prefs.use_global_undo)
            prefs.use_global_undo = False
        except Exception:
            self._prev_global_undo = None

        wm = context.window_manager
        try:
            wm.progress_begin(0, max(1, len(self._targets)))
            self._progress_started = True
        except Exception:
            self._progress_started = False
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        mode = "force-rebuild" if self.force_rebuild else "shared-MI assign"
        self.report(
            {"INFO"},
            f"Apply Materials by Mesh Name: {len(self._targets)} mesh(es) "
            f"({mode}, batch {self._batch}, ESC to cancel)",
        )
        print(
            f"Arc Raiders name-apply: start n={len(self._targets)} mode={mode} "
            f"batch={self._batch}"
        )
        return {"RUNNING_MODAL"}

    @staticmethod
    def _object_already_has_arc_mi(obj) -> bool:
        if not obj or obj.type != "MESH" or not obj.material_slots:
            return False
        mats = [s.material for s in obj.material_slots]
        if not mats or any(m is None for m in mats):
            return False
        return all(str(m.get("arc_mi_path") or "").strip() for m in mats)

    def modal(self, context, event):
        if event.type in {"ESC"}:
            self._cleanup(context)
            self.report(
                {"WARNING"},
                f"Name-apply cancelled ({self._ok + self._cached}/{len(self._targets)} "
                f"done, {self._fail} fail, {self._skip} skip, {self._already} already)",
            )
            return {"CANCELLED"}

        if event.type != "TIMER":
            return {"RUNNING_MODAL"}

        from . import operators as ops
        from . import materials as mats_mod

        end = min(self._index + self._batch, len(self._targets))
        for i in range(self._index, end):
            obj = self._targets[i]
            try:
                _ = obj.name
            except ReferenceError:
                self._fail += 1
                continue

            if self.skip_already_assigned and not self.force_rebuild:
                if self._object_already_has_arc_mi(obj):
                    self._already += 1
                    continue

            # Resolve with per-run stem-list cache (objects sharing names)
            stems = mesh_lookup_stems_for_object(obj)
            stem_key = "|".join(s.lower() for s in stems[:4]) or obj.name.lower()
            cached_resolve = self._path_cache.get(stem_key)
            if cached_resolve is not None:
                psk, matched = cached_resolve
            else:
                psk, matched = resolve_map_asset_path_for_object(obj)
                self._path_cache[stem_key] = (psk, matched)

            if not psk:
                self._skip += 1
                if len(self._skip_samples) < 8:
                    self._skip_samples.append(
                        f"{obj.name}: no export ({','.join(stems[:3]) or '?'})"
                    )
                try:
                    obj["arc_materials_skipped"] = "no_name_resolve"
                except Exception:
                    pass
                continue

            if mats_mod.is_engine_or_placeholder_mesh(psk, "", obj.name):
                self._skip += 1
                if len(self._skip_samples) < 8:
                    self._skip_samples.append(f"{obj.name}: placeholder")
                continue

            try:
                obj["arc_psk_path"] = psk
                obj["arc_model_type"] = "map"
                if matched:
                    obj["arc_name_resolve"] = matched
                if self.force_rebuild:
                    obj["arc_force_material_rebuild"] = 1
                elif obj.get("arc_force_material_rebuild"):
                    del obj["arc_force_material_rebuild"]
            except Exception:
                pass

            # Lightweight path: never invalidate shared MI / wipe slots per object.
            # Force rebuild relies on once-per-MI tracking inside setup_map_material.
            if self.force_rebuild:
                try:
                    mats_mod.clear_leaked_preferred_mi(obj)
                except Exception:
                    pass

            cache_key = mats_mod.map_material_cache_key(psk) or os.path.normcase(
                os.path.normpath(psk)
            )
            psk_base = os.path.splitext(os.path.basename(psk))[0].lower()
            cached_mats = self._mat_cache.get(cache_key)
            if cached_mats and ops.assign_cached_materials(obj, cached_mats):
                self._cached += 1
                try:
                    obj["arc_materials_pending"] = 0
                    if obj.get("arc_force_material_rebuild"):
                        del obj["arc_force_material_rebuild"]
                except Exception:
                    pass
                continue

            try:
                status = ops.apply_materials_to_object(obj, psk)
            except Exception as exc:
                status = f"error ({exc})"
                print(f"Arc Raiders name-apply: {obj.name}: {exc}")

            mats = ops.snapshot_object_materials(obj)
            if (
                mats
                and status not in ("unresolved", "skipped (not a mesh)")
                and not str(status).startswith("error")
            ):
                try:
                    if mats[0] is not None:
                        mats[0]["arc_cache_psk_stem"] = psk_base
                except Exception:
                    pass
                self._mat_cache[cache_key] = mats
                self._ok += 1
                if len(self._ok_samples) < 8:
                    self._ok_samples.append(
                        f"{obj.name}←{matched or os.path.basename(psk)} ({status})"
                    )
                try:
                    obj["arc_materials_pending"] = 0
                    if obj.get("arc_force_material_rebuild"):
                        del obj["arc_force_material_rebuild"]
                except Exception:
                    pass
                continue

            folder = os.path.dirname(psk)
            fixed = mats_mod.fix_object_materials_from_mi_slots(obj, folder)
            if fixed:
                mats = ops.snapshot_object_materials(obj)
                if mats:
                    self._mat_cache[cache_key] = mats
                self._ok += 1
                try:
                    obj["arc_materials_pending"] = 0
                except Exception:
                    pass
                continue

            self._fail += 1
            if len(self._fail_samples) < 8:
                self._fail_samples.append(
                    f"{obj.name}: {status} psk={os.path.basename(psk)}"
                )

        self._index = end
        total = len(self._targets)
        done = (
            self._ok + self._cached + self._fail + self._skip + self._already
        )
        self._ui_tick += 1
        if self._ui_tick == 1 or self._ui_tick % 3 == 0 or self._index >= total:
            msg = (
                f"Materials by name: {done}/{total} "
                f"({100.0 * done / max(total, 1):.1f}%) | "
                f"ok {self._ok} reuse {self._cached} already {self._already} "
                f"skip {self._skip} fail {self._fail}"
            )
            context.workspace.status_text_set(msg)
            print(f"Arc Raiders name-apply: {msg}")
            try:
                if self._progress_started:
                    context.window_manager.progress_update(done)
            except Exception:
                pass
            for area in context.screen.areas:
                if area.type in {"VIEW_3D", "STATUSBAR"}:
                    area.tag_redraw()
            # Periodic operator report so the info bar updates during long runs
            if self._ui_tick == 1 or self._ui_tick % 8 == 0:
                self.report({"INFO"}, msg)

        if self._index >= total:
            self._cleanup(context)
            level = {"INFO"} if self._fail == 0 else {"WARNING"}
            sample = ""
            if self._ok_samples:
                sample = " | e.g. " + " · ".join(self._ok_samples[:2])
            elif self._skip_samples:
                sample = " | skip e.g. " + " · ".join(self._skip_samples[:2])
            elif self._fail_samples:
                sample = " | fail e.g. " + " · ".join(self._fail_samples[:2])
            for line in self._ok_samples:
                print(f"Arc Raiders name-apply OK: {line}")
            for line in self._skip_samples:
                print(f"Arc Raiders name-apply skip: {line}")
            for line in self._fail_samples:
                print(f"Arc Raiders name-apply fail: {line}")
            self.report(
                level,
                f"Materials by name — ok {self._ok}, reused {self._cached}, "
                f"already {self._already}, skipped {self._skip}, failed {self._fail}"
                f"{sample}",
            )
            return {"FINISHED"}

        return {"RUNNING_MODAL"}

    def _cleanup(self, context):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        if self._progress_started:
            try:
                wm.progress_end()
            except Exception:
                pass
            self._progress_started = False
        if self._prev_global_undo is not None:
            try:
                context.preferences.edit.use_global_undo = self._prev_global_undo
            except Exception:
                pass
            self._prev_global_undo = None
        context.workspace.status_text_set(None)


class ARC_OT_FixWhiteUnassignedMaterials(bpy.types.Operator):
    """Post-pass: repair empty slots and default Principled-white map materials."""
    bl_idname = "arc.fix_white_unassigned_materials"
    bl_label = "Fix Unassigned/White Materials"
    bl_description = (
        "Scan map meshes for empty material slots or default white Principled "
        "(no Base Color texture). Rebuild from SM JSON StaticMaterials only "
        "(no fuzzy MI invent). Water/decal preferred_mi still allowed when restricted"
    )
    bl_options = {"REGISTER", "UNDO"}

    only_selected: bpy.props.BoolProperty(
        name="Only Selected",
        description="Only process selected meshes (default: whole map)",
        default=False,
    )

    def execute(self, context):
        from . import materials as mats_mod
        from . import fmdex

        scene = context.scene
        map_name = (getattr(scene, "arc_placement_map", "") or "").strip()
        if not map_name or map_name == "NONE":
            map_name = (getattr(scene, "arc_placement_map_name", "") or "").strip()

        targets = collect_map_mesh_targets(
            context, map_name, only_selected=bool(self.only_selected)
        )
        if not targets and self.only_selected:
            targets = [o for o in context.selected_objects if o.type == "MESH"]
        if not targets:
            targets = [
                o for o in bpy.data.objects
                if o.type == "MESH"
                and not o.get("arc_placement_instancer")
                and (o.get("arc_map") or o.get("arc_psk_path") or o.get("arc_mesh_file"))
            ]
        if not targets:
            self.report({"WARNING"}, "No map meshes to repair")
            return {"CANCELLED"}

        try:
            fmdex.ensure_loaded()
        except Exception:
            pass
        mats_mod.warm_shared_mi_material_cache()

        repaired = []
        skipped = []
        failed = []
        for obj in targets:
            need, why = mats_mod.object_needs_material_repair(obj)
            if not need:
                continue
            psk = ""
            for key in ("arc_psk_path", "arc_mesh_file"):
                raw = obj.get(key) or ""
                if raw:
                    psk = bpy.path.abspath(str(raw))
                    break
            info = mats_mod.fix_white_unassigned_materials(obj, psk)
            entry = {
                "name": obj.name,
                "why": why,
                "matched": info.get("matched") or "",
                "mi": info.get("mi_stem") or "",
                "ok": bool(info.get("ok") and info.get("reason") != "already_ok"),
                "reason": info.get("reason") or "",
            }
            if entry["ok"]:
                repaired.append(entry)
            elif info.get("reason") == "already_ok":
                skipped.append(entry)
            else:
                failed.append(entry)
            print(
                f"Arc Raiders white-fix: {entry['name']} why={entry['why']} "
                f"mi={entry['mi'] or '-'} matched={entry['matched'] or '-'} "
                f"ok={entry['ok']} ({entry['reason']})"
            )

        if repaired:
            try:
                prox = mats_mod.refresh_water_shore_proximity(
                    context, objects=targets, densify=True,
                )
                if prox.get("water"):
                    print(
                        f"Arc Raiders white-fix shore proximity: water={prox['water']} "
                        f"targets={prox['targets']} verts={prox['verts']}"
                    )
            except Exception as exc:
                print(f"Arc Raiders white-fix shore proximity: {exc}")

        if repaired and not failed:
            self.report(
                {"INFO"},
                f"Fixed {len(repaired)} white/unassigned mesh(es)"
                + (f" e.g. {repaired[0]['name']}→{repaired[0]['mi']}" if repaired else ""),
            )
        elif repaired:
            self.report(
                {"WARNING"},
                f"Fixed {len(repaired)}, failed {len(failed)}"
                + (f" e.g. {failed[0]['name']}:{failed[0]['reason']}" if failed else ""),
            )
        elif failed:
            self.report(
                {"WARNING"},
                f"No fixes — {len(failed)} still white/unassigned"
                + (f" e.g. {failed[0]['name']}:{failed[0]['why']}/{failed[0]['reason']}" if failed else ""),
            )
        else:
            self.report({"INFO"}, "No white/unassigned materials found")
        return {"FINISHED"}


class ARC_OT_RepairSmaTrimMaterials(bpy.types.Operator):
    """Strip leaked preferred stamps and rebuild SMA / PropTrim / architecture trim slots."""

    bl_idname = "arc.repair_sma_trim_materials"
    bl_label = "Repair SMA / Trim Materials"
    bl_description = (
        "Clear leaked PropTrim/Vent preferred_mi stamps on opaque meshes, then "
        "force-rebuild every material slot from SM JSON StaticMaterials (UVOffset, "
        "world-UV trims, PropTrim CR inherit). Prefer this when Stage 2 left vents "
        "on StaticMeshActors or trim sheets look wrong"
    )
    bl_options = {"REGISTER", "UNDO"}

    only_selected: bpy.props.BoolProperty(
        name="Only Selected",
        description="Only process selected meshes (default: whole map)",
        default=False,
    )
    sma_only: bpy.props.BoolProperty(
        name="StaticMeshActors Only",
        description="Limit to StaticMeshActors group / collection",
        default=False,
    )

    def execute(self, context):
        from . import materials as mats_mod
        from . import fmdex

        scene = context.scene
        map_name = (getattr(scene, "arc_placement_map", "") or "").strip()
        if not map_name or map_name == "NONE":
            map_name = (getattr(scene, "arc_placement_map_name", "") or "").strip()

        targets = collect_map_mesh_targets(
            context, map_name, only_selected=bool(self.only_selected)
        )
        if not targets and self.only_selected:
            targets = [o for o in context.selected_objects if o.type == "MESH"]
        if self.sma_only:
            filtered = []
            for obj in targets:
                group = str(obj.get("arc_map_group") or "")
                name = obj.name or ""
                coll_hit = False
                try:
                    for c in obj.users_collection:
                        if "StaticMeshActors" in (c.name or ""):
                            coll_hit = True
                            break
                except Exception:
                    pass
                if (
                    group == "StaticMeshActors"
                    or coll_hit
                    or "StaticMeshActor" in name
                ):
                    filtered.append(obj)
            targets = filtered

        if not targets:
            self.report({"WARNING"}, "No meshes to repair")
            return {"CANCELLED"}

        try:
            fmdex.ensure_loaded()
        except Exception:
            pass
        mats_mod.clear_material_session_caches()
        mats_mod.warm_shared_mi_material_cache()

        ok_n = 0
        cleared = 0
        fail_n = 0
        samples = []
        for obj in targets:
            psk = ""
            for key in ("arc_psk_path", "arc_mesh_file"):
                raw = obj.get(key) or ""
                if raw:
                    psk = bpy.path.abspath(str(raw))
                    break
            info = mats_mod.repair_sma_trim_materials(obj, psk)
            if info.get("cleared_preferred"):
                cleared += 1
            if info.get("ok"):
                ok_n += 1
            else:
                fail_n += 1
            if len(samples) < 8:
                samples.append(
                    f"{obj.name}: fixed={info.get('fixed')} "
                    f"cleared={info.get('cleared_preferred')} ({info.get('reason')})"
                )
            print(
                f"Arc Raiders SMA/trim repair: {obj.name} "
                f"fixed={info.get('fixed')} cleared_pref={info.get('cleared_preferred')} "
                f"ok={info.get('ok')} ({info.get('reason')})"
            )

        for line in samples:
            print(f"Arc Raiders SMA/trim repair sample: {line}")

        self.report(
            {"INFO"} if ok_n and not fail_n else {"WARNING"},
            f"Repair SMA/Trim: rebuilt {ok_n}, cleared preferred {cleared}, "
            f"unresolved {fail_n} / {len(targets)}",
        )
        return {"FINISHED"}


class ARC_OT_RefreshWaterShoreProximity(bpy.types.Operator):
    """Bake shore proximity attribute onto water meshes for Water↔Shore mix."""
    bl_idname = "arc.refresh_water_shore_proximity"
    bl_label = "Refresh Water Shore Proximity"
    bl_description = (
        "Bake arc_shore_proximity on water mesh vertices from distance to nearby "
        "opaque meshes (heightmap / StaticMeshActors by default). Drives Shore Color "
        "near intersections. Sparse planes are lightly subdivided. Re-run after moving "
        "terrain or changing Shore Distance. Cycles AO is a soft fallback without bake"
    )
    bl_options = {"REGISTER", "UNDO"}

    only_selected: bpy.props.BoolProperty(
        name="Only Selected",
        description="Only bake selected water meshes (default: all water in the scene)",
        default=False,
    )

    densify: bpy.props.BoolProperty(
        name="Densify Sparse Planes",
        description="Subdivide low-poly unique water planes so mid-face shore shows",
        default=True,
    )

    def execute(self, context):
        from . import materials as mats_mod

        objs = None
        if self.only_selected:
            objs = [o for o in context.selected_objects if o.type == "MESH"]
            if not objs:
                self.report({"WARNING"}, "Select water mesh(es) or disable Only Selected")
                return {"CANCELLED"}
        stats = mats_mod.refresh_water_shore_proximity(
            context, objects=objs, densify=bool(self.densify),
        )
        if not stats.get("water"):
            self.report(
                {"WARNING"},
                "No water materials found"
                + (f" (targets available: {stats.get('targets', 0)})" if stats.get("targets") else ""),
            )
            return {"CANCELLED"}
        if not stats.get("targets"):
            self.report(
                {"WARNING"},
                f"Updated {stats['water']} water mesh(es) but no proximity targets "
                "(set Shore Target Collection or import heightmap / SMA)",
            )
            return {"FINISHED"}
        self.report(
            {"INFO"},
            f"Shore proximity on {stats['water']} water mesh(es) "
            f"({stats['verts']} verts) vs {stats['targets']} targets"
            + (f", densified {stats['densified']}" if stats.get("densified") else ""),
        )
        return {"FINISHED"}


class ARC_OT_ImportPlacementHeightmapPlane(bpy.types.Operator):
    bl_idname = "arc.import_placement_heightmap_plane"
    bl_label = "Import Landscape / Heightmap"
    bl_description = (
        "Create a city-core placeholder ground plane from placements AABB. "
        "Only Displace from heightmap.png when that landscape footprint overlaps the city "
        "(Buried City exported PNG often does NOT). Shows SM_Landscape_* WP tiles "
        "(baked vertex height) and hides backdrop islands"
    )
    bl_options = {"REGISTER", "UNDO"}

    displace: bpy.props.BoolProperty(
        name="Displace from heightmap",
        description=(
            "Displace only when heightmap world_bounds overlap the city. "
            "Wrong/distant LandscapeStreamingProxy PNGs are never stretched onto the city"
        ),
        default=True,
    )
    rebuild_city_bounds: bpy.props.BoolProperty(
        name="Rebuild City-Core Bounds",
        description=(
            "Write {Map}_city_core_bounds.json from CSV prop footprint "
            "(does not overwrite FModel landscape world_bounds)"
        ),
        default=True,
    )
    show_landscape_tiles: bpy.props.BoolProperty(
        name="Show WP Landscape LOD Tiles",
        description=(
            "Show SM_Landscape_* section meshes (real baked terrain). "
            "City cells may be missing from export — surrounding tiles still help. "
            "Backdrop islands stay hidden"
        ),
        default=True,
    )
    hide_landscape_tiles: bpy.props.BoolProperty(
        name="Hide All Landscape Tiles (legacy)",
        description="Deprecated: hides WP tiles too. Prefer Show WP Landscape LOD Tiles",
        default=False,
    )

    def execute(self, context):
        scene = context.scene
        csv_path = bpy.path.abspath(getattr(scene, "arc_placement_csv", "") or "")
        map_name = (getattr(scene, "arc_placement_map_name", "") or "Map").strip() or "Map"
        if not map_name or map_name == "NONE":
            map_name = (getattr(scene, "arc_placement_map", "") or "Map").strip() or "Map"
        # Prefer the MapPlacements folder leaf (BuriedCity_01_P) over a truncated enum.
        if csv_path:
            leaf = os.path.basename(os.path.dirname(csv_path).rstrip("\\/"))
            if leaf and ("_P" in leaf or leaf.startswith(map_name) or map_name.startswith(leaf.rstrip("_P"))):
                map_name = leaf
                scene.arc_placement_map_name = leaf

        resolved = resolve_heightmap_assets(
            csv_path=csv_path,
            map_name=map_name,
            bounds_hint=getattr(scene, "arc_placement_world_bounds_json", "") or "",
            image_hint=getattr(scene, "arc_placement_heightmap_image", "") or "",
        )
        image = resolved.get("image") or ""
        manifest_bounds = resolved.get("manifest_bounds") or ""
        if image:
            scene.arc_placement_heightmap_image = image

        landscape_bounds = ""
        if manifest_bounds and os.path.isfile(manifest_bounds):
            try:
                with open(manifest_bounds, "r", encoding="utf-8") as fh:
                    mb = json.load(fh)
                src = str(mb.get("source") or "")
                if src.startswith("landscape"):
                    landscape_bounds = manifest_bounds
            except (OSError, json.JSONDecodeError):
                pass
        if not landscape_bounds and csv_path:
            folder = os.path.dirname(csv_path)
            export_root = resolved.get("export_root") or ""
            name_candidates = [map_name]
            if map_name.endswith("_P"):
                name_candidates.append(map_name[:-2])
            else:
                name_candidates.append(f"{map_name}_P")
            # Prefer names derived from the CSV folder (BuriedCity_01_P).
            folder_leaf = os.path.basename(folder.rstrip("\\/"))
            if folder_leaf and folder_leaf not in name_candidates:
                name_candidates.insert(0, folder_leaf)
            for search in (folder, export_root):
                if not search:
                    continue
                for nm in name_candidates:
                    cand = os.path.join(search, f"{nm}_landscape_bounds.json")
                    if os.path.isfile(cand):
                        landscape_bounds = cand
                        break
                if landscape_bounds:
                    break

        bounds = ""
        obj_tag_note = ""
        if self.rebuild_city_bounds and csv_path and os.path.isfile(csv_path):
            built = build_world_bounds_from_csv(csv_path, map_name=map_name, city_core=True)
            if built:
                bounds = built
                scene.arc_placement_world_bounds_json = built

        if not bounds or not os.path.isfile(bounds):
            for folder in (
                os.path.dirname(csv_path) if csv_path else "",
                resolved.get("export_root") or "",
            ):
                if not folder:
                    continue
                cand = os.path.join(folder, f"{map_name}_city_core_bounds.json")
                if os.path.isfile(cand):
                    bounds = cand
                    scene.arc_placement_world_bounds_json = cand
                    break

        if not bounds or not os.path.isfile(bounds):
            bounds = resolved.get("bounds") or ""
            if bounds and os.path.isfile(bounds):
                scene.arc_placement_world_bounds_json = bounds

        if (not bounds or not os.path.isfile(bounds)) and csv_path and os.path.isfile(csv_path):
            built = build_world_bounds_from_csv(csv_path, map_name=map_name, city_core=True)
            if built:
                bounds = built
                scene.arc_placement_world_bounds_json = built
                self.report({"INFO"}, f"Built city_core bounds → {os.path.basename(built)}")

        do_displace = bool(self.displace) and bool(image) and os.path.isfile(image)
        # Only trust bounds whose source is landscape* — never self-overlap city_core vs city_core
        # (that previously stretched a distant PNG onto the city AABB).
        overlap_bounds = landscape_bounds
        if do_displace and overlap_bounds and bounds:
            if not heightmap_overlaps_city_core(overlap_bounds, bounds):
                do_displace = False
                obj_tag_note = (
                    "NOT city ground: heightmap is a non-overlapping LandscapeStreamingProxy. "
                    "Real terrain = SM_Landscape_* WP LOD meshes (city cells often missing). "
                    "Plane is a flat placeholder under props only."
                )
        elif do_displace:
            do_displace = False
            obj_tag_note = (
                "Refusing displace without landscape_bounds (source=landscape*). "
                "Exported PNG is often a distant proxy — use SM_Landscape_* tiles or re-export."
            )

        if (
            bool(self.displace)
            and landscape_bounds
            and os.path.isfile(landscape_bounds)
            and image
            and os.path.isfile(image)
            and bounds
            and heightmap_overlaps_city_core(landscape_bounds, bounds)
        ):
            bounds = landscape_bounds
            do_displace = True
            obj_tag_note = ""

        image_for_plane = image if do_displace else ""
        obj = create_heightmap_plane(
            bounds,
            image_path=image_for_plane,
            map_name=map_name,
            displace=do_displace,
        )
        if obj is None:
            self.report(
                {"ERROR"},
                "Could not create heightmap plane — need world_bounds JSON or a placements CSV",
            )
            return {"CANCELLED"}
        if not do_displace:
            obj.name = f"{map_name}_CityGroundPlane"
            obj["arc_ground_placeholder"] = 1
        if obj_tag_note:
            obj["arc_heightmap_mismatch"] = 1
            obj["arc_heightmap_note"] = obj_tag_note

        land_stats = {"backdrop_hidden": 0, "wp_shown": 0}
        if self.hide_landscape_tiles:
            land_stats["backdrop_hidden"] = hide_misplaced_landscape_tiles(map_name)
            for o in list(_iter_map_instancers(map_name)):
                ap = (o.get("arc_asset_path") or o.name or "").lower()
                if "sm_landscape_" in ap:
                    try:
                        o.hide_set(True)
                        o.hide_render = True
                    except Exception:
                        pass
        elif self.show_landscape_tiles:
            land_stats = organize_landscape_tiles(map_name, show_wp_tiles=True)

        note = f"Created {obj.name}"
        if do_displace:
            note += f" · displaced from {os.path.basename(image)} (city-aligned)"
        else:
            note += " · flat placeholder (exported heightmap is NOT under Buried City)"
        if land_stats.get("wp_shown"):
            note += f" · showed {land_stats['wp_shown']} WP landscape tile(s)"
        if land_stats.get("backdrop_hidden"):
            note += f" · hid {land_stats['backdrop_hidden']} backdrop(s)"
        self.report({"INFO" if do_displace else "WARNING"}, note)
        return {"FINISHED"}


class ARC_OT_ReloadPlacementHeightmap(bpy.types.Operator):
    bl_idname = "arc.reload_placement_heightmap"
    bl_label = "Reload Heightmap Only"
    bl_description = (
        "Rebuild CityGroundPlane displace from on-disk {Map}_heightmap.png + "
        "landscape_bounds.json. Neutralizes missing-data voids (mid-fill / black) so "
        "Displace does not create wall spikes; clips void alpha on the ground material. "
        "Does not reimport meshes or rerun Stage 1. "
        "Run map_tools/prepare_blender_heightmap.py first if the PNG needs crop/flip. "
        "Sandy maps (RivenTides etc.) auto-apply dual-scale sand BRDF"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        csv_path = bpy.path.abspath(getattr(scene, "arc_placement_csv", "") or "")
        map_name = (getattr(scene, "arc_placement_map_name", "") or "").strip()
        if not map_name or map_name == "NONE":
            map_name = (getattr(scene, "arc_placement_map", "") or "").strip()
        stats = reload_heightmap_displace_only(map_name, csv_path=csv_path)
        if not stats.get("displaced"):
            self.report({"WARNING"}, stats.get("note") or "Heightmap reload failed")
            return {"CANCELLED"}
        self.report({"INFO"}, stats.get("note") or "Heightmap reloaded")
        return {"FINISHED"}


class ARC_OT_ApplyHeightmapSandMaterial(bpy.types.Operator):
    bl_idname = "arc.apply_heightmap_sand_material"
    bl_label = "Apply Sand to Heightmap"
    bl_description = (
        "Assign dual-scale South/Dunes sand BRDF to CityGroundPlane without reimport. "
        "Uses In-Game Map for spatial masks and HLOD Color for cream/rock/pink palette "
        "(auto from Content or panel paths). Keeps Displace from heightmap.png. "
        "Works on any map (force); auto on RivenTides during Reload Heightmap"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import materials as mats_mod

        scene = context.scene
        map_name = (getattr(scene, "arc_placement_map_name", "") or "").strip()
        if not map_name or map_name == "NONE":
            map_name = (getattr(scene, "arc_placement_map", "") or "").strip()

        ingame = (getattr(scene, "arc_placement_ingame_map_image", "") or "").strip()
        hlod = (getattr(scene, "arc_placement_hlod_color_image", "") or "").strip()
        # Fill empty slots from Content so UI shows what was used
        if not ingame:
            path, _why = mats_mod.resolve_ingame_map_texture(map_name, "")
            if path:
                scene.arc_placement_ingame_map_image = path
                ingame = path
        if not hlod:
            path, _why, _tiles = mats_mod.resolve_hlod_color_texture(map_name, "")
            if path:
                scene.arc_placement_hlod_color_image = path
                hlod = path

        targets = []
        for obj in context.selected_objects:
            if obj.get("arc_heightmap_plane") or "CityGroundPlane" in obj.name or "HeightmapPlane" in obj.name:
                targets.append(obj)
        if not targets:
            for obj in bpy.data.objects:
                if not obj.get("arc_heightmap_plane"):
                    continue
                if map_name and obj.get("arc_map") and obj.get("arc_map") != map_name:
                    continue
                targets.append(obj)

        if not targets:
            self.report({"WARNING"}, "No CityGroundPlane / heightmap object found")
            return {"CANCELLED"}

        applied = 0
        note = ""
        for obj in targets:
            if mats_mod.apply_sand_to_heightmap_object(
                obj,
                map_name=map_name or str(obj.get("arc_map") or ""),
                force=True,
                ingame_map_path=ingame,
                hlod_color_path=hlod,
                use_map_texturing=True,
            ):
                applied += 1
                mat = obj.data.materials[0] if obj.data.materials else None
                if mat is not None:
                    note = str(mat.get("arc_hlod_palette_mode") or "")
        if not applied:
            self.report(
                {"WARNING"},
                "Sand textures not found — set Pioneer root / Arc_Raiders_Current Content",
            )
            return {"CANCELLED"}
        extra = ""
        if ingame:
            extra += f" map={os.path.basename(ingame)}"
        if hlod:
            extra += f" hlod={os.path.basename(hlod)}"
        if note:
            extra += f" ({note})"
        self.report(
            {"INFO"},
            f"Applied landscape sand to {applied} heightmap object(s)"
            f"{extra} (Displace kept)",
        )
        return {"FINISHED"}


class ARC_OT_PickPlacementInGameMap(bpy.types.Operator):
    bl_idname = "arc.pick_placement_ingame_map"
    bl_label = "Pick In-Game Map Image"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(
        default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.tga", options={"HIDDEN"},
    )

    def execute(self, context):
        context.scene.arc_placement_ingame_map_image = self.filepath
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class ARC_OT_ClearPlacementInGameMap(bpy.types.Operator):
    bl_idname = "arc.clear_placement_ingame_map"
    bl_label = "Clear In-Game Map Image"

    def execute(self, context):
        context.scene.arc_placement_ingame_map_image = ""
        return {"FINISHED"}


class ARC_OT_PickPlacementHlodColor(bpy.types.Operator):
    bl_idname = "arc.pick_placement_hlod_color"
    bl_label = "Pick HLOD Color Image"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(
        default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.tga", options={"HIDDEN"},
    )

    def execute(self, context):
        context.scene.arc_placement_hlod_color_image = self.filepath
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class ARC_OT_ClearPlacementHlodColor(bpy.types.Operator):
    bl_idname = "arc.clear_placement_hlod_color"
    bl_label = "Clear HLOD Color Image"

    def execute(self, context):
        context.scene.arc_placement_hlod_color_image = ""
        return {"FINISHED"}


class ARC_OT_ResolveHeightmapGroundRefs(bpy.types.Operator):
    bl_idname = "arc.resolve_heightmap_ground_refs"
    bl_label = "Auto-Find Map / HLOD Refs"
    bl_description = (
        "Search Pioneer Content / MapPlacement workspace / addon assets for "
        "T_InGameMap_* and T_*_Color_* textures for the selected map"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        from . import materials as mats_mod

        scene = context.scene
        map_name = (getattr(scene, "arc_placement_map_name", "") or "").strip()
        if not map_name or map_name == "NONE":
            map_name = (getattr(scene, "arc_placement_map", "") or "").strip()
        if not map_name or map_name == "NONE":
            self.report({"WARNING"}, "Select a map first")
            return {"CANCELLED"}

        ingame, ig_why = mats_mod.resolve_ingame_map_texture(
            map_name, getattr(scene, "arc_placement_ingame_map_image", "") or "",
        )
        hlod, hl_why, tiles = mats_mod.resolve_hlod_color_texture(
            map_name, getattr(scene, "arc_placement_hlod_color_image", "") or "",
        )
        if ingame:
            scene.arc_placement_ingame_map_image = ingame
        if hlod:
            scene.arc_placement_hlod_color_image = hlod

        bits = []
        if ingame:
            bits.append(f"map={os.path.basename(ingame)} ({ig_why})")
        if hlod:
            extra = f", {len(tiles)} tile(s)" if len(tiles) > 1 else ""
            bits.append(f"hlod={os.path.basename(hlod)} ({hl_why}{extra})")
        if not bits:
            self.report(
                {"WARNING"},
                "No InGameMap / HLOD Color found — set Pioneer root or pick manually",
            )
            return {"CANCELLED"}
        self.report({"INFO"}, " · ".join(bits))
        return {"FINISHED"}


class ARC_OT_FixImportSplines(bpy.types.Operator):
    bl_idname = "arc.fix_import_splines"
    bl_label = "Fix / Guide Splines"
    bl_description = (
        "Tag SplineMesh placements and create start/end guide empties from FModel notes. "
        "Full deformation requires FModel Export Map Placements + Meshes (baked UEModels)"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        csv_path = bpy.path.abspath(getattr(scene, "arc_placement_csv", "") or "")
        rows = load_placements_csv(csv_path)
        map_name = (getattr(scene, "arc_placement_map_name", "") or "Map").strip() or "Map"
        if not rows:
            self.report({"ERROR"}, "No placements CSV loaded")
            return {"CANCELLED"}
        spline_rows = [r for r in rows if is_spline_mesh_row(r)]
        if not spline_rows:
            self.report({"WARNING"}, "No SplineMesh rows in CSV")
            return {"CANCELLED"}
        guides, undeformed = create_spline_guide_empties(spline_rows, map_name=map_name)
        baked = sum(1 for r in spline_rows if parse_spline_notes(r.get("notes") or "").get("baked"))
        msg = (
            f"{len(spline_rows)} SplineMesh row(s): {baked} baked notes, "
            f"{undeformed} undeformed, {guides} guide empties"
        )
        if undeformed and baked == 0:
            msg += (
                " — re-run FModel “Export Map Placements + Meshes” for deformed UEModels; "
                "then Stage 1 Fast again"
            )
            self.report({"WARNING"}, msg)
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class ARC_OT_PickPlacementCSV(bpy.types.Operator):
    bl_idname = "arc.pick_placement_csv"
    bl_label = "Pick Placement CSV"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.csv", options={"HIDDEN"})

    def execute(self, context):
        context.scene.arc_placement_csv = self.filepath
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class ARC_OT_PickPlacementBounds(bpy.types.Operator):
    bl_idname = "arc.pick_placement_bounds"
    bl_label = "Pick World Bounds JSON"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        context.scene.arc_placement_world_bounds_json = self.filepath
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class ARC_OT_PickPlacementHeightmapImage(bpy.types.Operator):
    bl_idname = "arc.pick_placement_heightmap_image"
    bl_label = "Pick Heightmap Image"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.png;*.jpg;*.jpeg;*.tif;*.tiff", options={"HIDDEN"})

    def execute(self, context):
        context.scene.arc_placement_heightmap_image = self.filepath
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class ARC_OT_PickPlacementWorkspace(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    """Choose the central Map Placement output folder."""
    bl_idname = "arc.pick_placement_workspace"
    bl_label = "Select Placement Workspace"
    filename_ext = ""
    filter_glob: bpy.props.StringProperty(default="*", options={"HIDDEN"})

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        context.scene.arc_placement_workspace = folder
        self.report({"INFO"}, f"Placement workspace: {folder}")
        return {"FINISHED"}


class ARC_OT_ClearPlacementWorkspace(bpy.types.Operator):
    bl_idname = "arc.clear_placement_workspace"
    bl_label = "Clear Placement Workspace"

    def execute(self, context):
        context.scene.arc_placement_workspace = ""
        return {"FINISHED"}


class ARC_OT_RefreshPlacementMaps(bpy.types.Operator):
    """Re-scan Pioneer/Maps for the map dropdown."""
    bl_idname = "arc.refresh_placement_maps"
    bl_label = "Refresh Maps"
    bl_options = {"REGISTER"}

    def execute(self, context):
        maps = list_detected_maps(utils.get_pioneer_root())
        self.report({"INFO"}, f"Found {len(maps)} map(s)")
        # Force enum refresh by touching the property
        cur = context.scene.arc_placement_map
        context.scene.arc_placement_map = cur
        return {"FINISHED"}


class ARC_OT_ExtractMapPlacements(bpy.types.Operator):
    """Run extract for the selected map into the placement workspace."""
    bl_idname = "arc.extract_map_placements"
    bl_label = "Extract Placements"
    bl_description = (
        "Scan the selected map's JSON cells and write placements CSV, "
        "world_bounds, and overlay PNG into {Workspace}/{MapName}/ "
        "(default: addon/MapPlacement/) — may take a minute for large maps"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        map_name = getattr(scene, "arc_placement_map", "NONE") or "NONE"
        if map_name == "NONE":
            self.report({"ERROR"}, "Select a map from the dropdown first")
            return {"CANCELLED"}
        if not utils.get_pioneer_root():
            self.report({"ERROR"}, "Set PioneerGame Folder in Settings first")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Extracting {map_name}… (see System Console)")
        ok, msg = run_extract_for_map(map_name, scene)
        if ok:
            self.report({"INFO"}, msg)
            return {"FINISHED"}
        self.report({"ERROR"}, msg)
        return {"CANCELLED"}


class ARC_OT_GeneratePlacementOverlay(bpy.types.Operator):
    """Copy bounds/heightmap assets and build the placements overlay PNG."""
    bl_idname = "arc.generate_placement_overlay"
    bl_label = "Generate Overlay PNG"
    bl_description = (
        "Gather world_bounds / heightmap refs into {Workspace}/{MapName}/ "
        "and write {MapName}_placements_overlay.png there"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        map_name = getattr(scene, "arc_placement_map", "NONE") or "NONE"
        if map_name == "NONE":
            self.report({"ERROR"}, "Select a map from the dropdown first")
            return {"CANCELLED"}
        gathered = gather_map_outputs_into_workspace(map_name, scene)
        ok, msg = run_overlay_for_map(map_name, scene)
        if gathered:
            msg = f"{msg} | gathered {len(gathered)} file(s)"
        if ok:
            self.report({"INFO"}, msg)
            return {"FINISHED"}
        self.report({"ERROR"}, msg)
        return {"CANCELLED"}


class ARC_OT_FramePlacementView(bpy.types.Operator):
    """Raise viewport clip end and frame all map placement objects."""
    bl_idname = "arc.frame_placement_view"
    bl_label = "Frame Map Placements"
    bl_description = (
        "Placements use Unreal centimeters and sit far from the world origin. "
        "This raises the 3D View clip end and frames all arc_map objects."
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        map_name = getattr(context.scene, "arc_placement_map", "") or ""
        if map_name and map_name != "NONE":
            tagged = [o for o in context.scene.objects if o.get("arc_map") == map_name]
        else:
            tagged = [o for o in context.scene.objects if o.get("arc_map")]
        if not tagged:
            self.report({"WARNING"}, "No placement objects found (arc_map custom property)")
            return {"CANCELLED"}
        n = configure_viewport_for_map(context, tagged)
        # Also bump empty display sizes if still tiny
        for obj in tagged:
            if obj.type == "EMPTY" and obj.empty_display_size < empty_display_size_for_map(meters=2.0):
                obj.empty_display_size = empty_display_size_for_map(meters=5.0)
                obj.empty_display_type = "CUBE"
        self.report({"INFO"}, f"Framed {len(tagged)} object(s) in {n} 3D view(s); clip_end=5e6")
        return {"FINISHED"}


class ARC_OT_OpenPlacementWorkspace(bpy.types.Operator):
    """Reveal the placement workspace folder in the OS file browser."""
    bl_idname = "arc.open_placement_workspace"
    bl_label = "Open Workspace Folder"
    bl_options = {"REGISTER"}

    def execute(self, context):
        path = get_placement_workspace(context.scene)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self.report({"WARNING"}, f"Could not open folder: {e}")
            return {"CANCELLED"}
        return {"FINISHED"}


class ARC_OT_StartPlacementListener(bpy.types.Operator):
    """Listen for FModel placements_ready on localhost TCP."""
    bl_idname = "arc.start_placement_listener"
    bl_label = "Start FModel Listener"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from .map_tools import fmodel_bridge as bridge

        port = int(getattr(context.scene, "arc_placement_listen_port", bridge.DEFAULT_PORT) or bridge.DEFAULT_PORT)
        msg = bridge.start_listener(port=port)
        self.report({"INFO"} if bridge.is_listening() else {"ERROR"}, msg)
        return {"FINISHED"} if bridge.is_listening() else {"CANCELLED"}


class ARC_OT_StopPlacementListener(bpy.types.Operator):
    """Stop the FModel placement TCP listener."""
    bl_idname = "arc.stop_placement_listener"
    bl_label = "Stop Listener"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from .map_tools import fmodel_bridge as bridge

        msg = bridge.stop_listener()
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class ARC_OT_FixPlacementRotations(bpy.types.Operator):
    """Convert legacy placement eulers to Unreal pass-through (-roll, -pitch, yaw)."""

    bl_idname = "arc.fix_placement_rotations"
    bl_label = "Fix Placement Rotations"
    bl_description = (
        "Remap Fast/instanced arc_rotation attributes (and placement empties) to "
        "Blender XYZ=(-roll,-pitch,yaw). Detects pyr/rpy vs ue_xyz via CSV when set; "
        "skips already-correct ue_xyz unless Force is enabled. Safe vs double-apply"
    )
    bl_options = {"REGISTER", "UNDO"}

    force: bpy.props.BoolProperty(
        name="Force",
        description=(
            "Re-check even when tagged ue_xyz. Still no-ops when CSV confirms "
            "values already match (-roll,-pitch,yaw). Use Assume Layout to "
            "recover from a mistaken double remap"
        ),
        default=False,
    )
    assume_layout: bpy.props.EnumProperty(
        name="Assume Layout",
        description="Override detected/tagged layout before remapping to ue_xyz",
        items=(
            ("AUTO", "Auto (detect)", "Use tag + CSV sample when available"),
            (ROTATION_LAYOUT_LEGACY_PYR, "Legacy pyr (pitch,yaw,roll)", "First buggy import"),
            (ROTATION_LAYOUT_RPY, "rpy (roll,pitch,yaw)", "Intermediate wrong fix"),
            (ROTATION_LAYOUT_UE, "Already ue_xyz", "Leave as-is / verify only"),
        ),
        default="AUTO",
    )

    def execute(self, context):
        scene = context.scene
        csv_path = bpy.path.abspath(getattr(scene, "arc_placement_csv", "") or "")
        assume = "" if self.assume_layout == "AUTO" else self.assume_layout
        instancers, empties, stats = fix_legacy_placement_rotations(
            force=bool(self.force),
            assume_layout=assume,
            csv_path=csv_path,
        )
        if instancers == 0 and empties == 0:
            skipped = int(stats.get("skipped_ue_xyz") or 0)
            already = int(stats.get("already_correct") or 0)
            self.report(
                {"INFO"},
                f"No rotation remap needed "
                f"(skipped_ue_xyz={skipped}, already_correct={already}, "
                f"detected_pyr={stats.get('detected_pyr', 0)}, "
                f"detected_rpy={stats.get('detected_rpy', 0)})",
            )
            return {"FINISHED"}
        self.report(
            {"INFO"},
            f"Fixed rotations on {instancers} instancer(s) and {empties} empty/marker(s) "
            f"(pyr={stats.get('detected_pyr', 0)}, rpy={stats.get('detected_rpy', 0)})",
        )
        return {"FINISHED"}


class ARC_OT_FixMapOrientation(bpy.types.Operator):
    """Mirror map Y so Blender top-down matches the in-game map UI."""

    bl_idname = "arc.fix_map_orientation"
    bl_label = "Fix Map Orientation"
    bl_description = (
        "Reflect placements / heightmap across XZ (negate Unreal Y). Matches Buried City "
        "in-game map (dual tracks bottom-left, bridge left→right). Idempotent via "
        "arc_map_orientation=mirror_y. Compose with Scale Map to Meters for unit fix"
    )
    bl_options = {"REGISTER", "UNDO"}

    force: bpy.props.BoolProperty(
        name="Force",
        description="Apply even when already tagged mirror_y (will double-flip)",
        default=False,
    )

    def execute(self, context):
        map_name = (getattr(context.scene, "arc_placement_map", "") or "").strip()
        if not map_name or map_name == "NONE":
            map_name = (getattr(context.scene, "arc_placement_map_name", "") or "").strip()
        stats = apply_map_orientation_mirror_y(
            map_name=map_name if map_name != "NONE" else "",
            force=bool(self.force),
        )
        configure_viewport_for_map(context)
        self.report(
            {"INFO"},
            f"Map orientation mirror_y: instancers={stats['instancers']} "
            f"points={stats['points']} heightmaps={stats['heightmaps']} "
            f"empties={stats['empties']} skipped={stats['skipped']}",
        )
        return {"FINISHED"}


class ARC_OT_ScaleMapToMeters(bpy.types.Operator):
    """Scale an existing Unreal-cm map scene down to Blender meters (÷100)."""

    bl_idname = "arc.scale_map_to_meters"
    bl_label = "Scale Map to Meters"
    bl_description = (
        "Multiply locations / source scales / heightmap by 0.01 so 1 BU = 1 m. "
        "CSV stays Unreal cm; this matches new Fast imports. Idempotent when "
        "arc_map_unit_scale=0.01. Apply Fix Map Orientation first if needed"
    )
    bl_options = {"REGISTER", "UNDO"}

    force: bpy.props.BoolProperty(
        name="Force",
        description="Re-scale even when already tagged 0.01 (will shrink again)",
        default=False,
    )

    def execute(self, context):
        map_name = (getattr(context.scene, "arc_placement_map", "") or "").strip()
        if not map_name or map_name == "NONE":
            map_name = (getattr(context.scene, "arc_placement_map_name", "") or "").strip()
        stats = apply_map_unit_scale_to_meters(
            map_name=map_name if map_name != "NONE" else "",
            target_unit=MAP_UNIT_SCALE,
            force=bool(self.force),
        )
        configure_viewport_for_map(context)
        self.report(
            {"INFO"},
            f"Scaled to meters (×{MAP_UNIT_SCALE}): instancers={stats['instancers']} "
            f"sources={stats['sources']} heightmaps={stats['heightmaps']} "
            f"empties={stats['empties']} skipped={stats['skipped']}",
        )
        return {"FINISHED"}


class ARC_OT_RemoveDuplicatePlacements(bpy.types.Operator):
    """Dedupe Fast-import points, quiet InstanceSources, drop double-imported instancers."""

    bl_idname = "arc.remove_duplicate_placements"
    bl_label = "Remove Duplicate Placements"
    bl_description = (
        "In-scene cleanup: near-duplicate points inside instancers (same mesh + epsilon pose), "
        "optional undeformed-Spline vs Static overlap removal, drop double-imported instancers, "
        "quiet InstanceSources (bounds + layer eye-hide), purge leftover .001 Stage-1 collections. "
        "Origin piles of DIFFERENT assets at one pose are wrong FModel child transforms — "
        "use Mark Origin Piles, or Aggressive Collapse only with the warning understood"
    )
    bl_options = {"REGISTER", "UNDO"}

    dedupe_points: bpy.props.BoolProperty(
        name="Dedupe Points",
        description="Remove near-identical points within each Fast instancer (pos/rot epsilon)",
        default=True,
    )
    near_mesh_eps: bpy.props.BoolProperty(
        name="Near Same-Mesh Collapse",
        description=(
            "Also collapse same-mesh-file instances within ~1cm / small rotation epsilon "
            "(still same instancer only — not cross-asset)"
        ),
        default=True,
    )
    remove_spline_static_overlaps: bpy.props.BoolProperty(
        name="Drop Undeformed Spline Overlaps",
        description=(
            "Remove undeformed SplineMesh points when a Static of the same package "
            "already exists at the same pose"
        ),
        default=True,
    )
    hide_sources: bpy.props.BoolProperty(
        name="Quiet Instance Sources",
        description="Draw sources as bounds and eye-hide the InstanceSources collection",
        default=True,
    )
    remove_dup_instancers: bpy.props.BoolProperty(
        name="Remove Dup Instancers",
        description="Delete double-imported instancers with the same mesh + point fingerprint",
        default=True,
    )
    mark_origin_piles: bpy.props.BoolProperty(
        name="Mark Origin Piles",
        description=(
            "Create diagnostic empties at poses where multiple DIFFERENT assets share a "
            "transform (FModel child AbsoluteTransform bug — not same-mesh duplicates)"
        ),
        default=True,
    )
    hide_landscape_tiles: bpy.props.BoolProperty(
        name="Hide Landscape LOD Tiles",
        description="Hide SM_Landscape_* WP section tiles that do not cover the city core",
        default=False,
    )
    hide_foliage_piles: bpy.props.BoolProperty(
        name="Drop Foliage On Origin Piles",
        description=(
            "Remove tree/bush points that sit on multi-asset origin piles "
            "(child-at-root exports). Off by default — most tree intersections are not piles"
        ),
        default=False,
    )
    aggressive_collapse: bpy.props.BoolProperty(
        name="Aggressive Multi-Asset Collapse",
        description=(
            "WARNING: at identical poses with multiple different assets, keep only one "
            "(largest / first / prefer StaticMesh). Removes real building props that were "
            "exported at the actor root — use only as a visual stopgap before FModel re-export"
        ),
        default=False,
    )
    aggressive_mode: bpy.props.EnumProperty(
        name="Keep When Aggressive",
        description="Which asset to keep at a multi-asset identical pose",
        items=(
            ("largest", "Largest Mesh", "Keep the source with the most vertices"),
            ("prefer_static", "Prefer StaticMesh", "StaticMesh over ISM/Spline, then largest"),
            ("first", "First Asset Path", "Keep lexicographically first asset path"),
        ),
        default="largest",
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "dedupe_points")
        layout.prop(self, "near_mesh_eps")
        layout.prop(self, "remove_spline_static_overlaps")
        layout.prop(self, "remove_dup_instancers")
        layout.prop(self, "hide_sources")
        layout.prop(self, "mark_origin_piles")
        layout.prop(self, "hide_landscape_tiles")
        layout.prop(self, "hide_foliage_piles")
        layout.separator()
        box = layout.box()
        box.alert = True
        box.label(text="Aggressive collapse deletes DIFFERENT assets at one pose", icon="ERROR")
        box.label(text="Only a stopgap — props need FModel AbsoluteTransform fix")
        box.prop(self, "aggressive_collapse")
        row = box.row()
        row.enabled = bool(self.aggressive_collapse)
        row.prop(self, "aggressive_mode", text="Keep")

    def execute(self, context):
        stats = cleanup_duplicate_placements(
            context,
            dedupe_points=bool(self.dedupe_points),
            near_mesh_eps=bool(self.near_mesh_eps),
            hide_sources=bool(self.hide_sources),
            remove_dup_instancers=bool(self.remove_dup_instancers),
            remove_spline_static_overlaps=bool(self.remove_spline_static_overlaps),
            aggressive_collapse=bool(self.aggressive_collapse),
            aggressive_mode=str(self.aggressive_mode or "largest"),
            mark_origin_piles=bool(self.mark_origin_piles),
            hide_landscape_tiles=bool(self.hide_landscape_tiles),
            hide_foliage_piles=bool(self.hide_foliage_piles),
        )
        tight = (
            int(stats["points_removed"])
            - int(stats["near_mesh_points_removed"])
            - int(stats["spline_overlap_removed"])
            - int(stats["aggressive_removed"])
            - int(stats.get("foliage_pile_removed") or 0)
        )
        msg = (
            f"Cleanup — tight_eps={tight} near_mesh={stats['near_mesh_points_removed']} "
            f"spline_ov={stats['spline_overlap_removed']} "
            f"aggressive={stats['aggressive_removed']} "
            f"foliage_pile={stats.get('foliage_pile_removed', 0)} | "
            f"dup_instancers={stats['dup_instancers_removed']} "
            f"sources={stats['sources_quieted']} "
            f"cols={stats['collections_removed']} "
            f"land_hid={stats.get('landscape_tiles_hidden', 0)} | "
            f"origin_piles_remain={stats['origin_piles']} "
            f"(pts={stats['origin_pile_points']}"
            + (
                f", markers={stats['origin_pile_markers']}"
                if stats.get("origin_pile_markers")
                else ""
            )
            + ")"
        )
        level = {"WARNING"} if self.aggressive_collapse else {"INFO"}
        self.report(level, msg)
        return {"FINISHED"}


class ARC_OT_GroupMapFoliage(bpy.types.Operator):
    """Move foliage instancers into Trees/Vines/Bushes/Grass/Overgrowth/Other under the map placements root."""

    bl_idname = "arc.group_map_foliage"
    bl_label = "Group Map Foliage"
    bl_description = (
        "Classify foliage instancers by asset/name heuristics and move them under "
        "{Map}_P_Foliage/{Trees|Vines|Bushes|Grass|Overgrowth|Other}. SRC meshes stay in "
        "InstanceSources so Geometry Nodes keep evaluating"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        map_name = getattr(scene, "arc_placement_map", "") or ""
        if not map_name or map_name == "NONE":
            map_name = getattr(scene, "arc_placement_map_name", "") or ""
        stats = group_map_foliage(map_name)
        self.report(
            {"INFO"},
            f"Foliage grouped: moved={stats.get('moved', 0)} "
            f"skipped={stats.get('skipped', 0)} "
            f"cats={stats.get('categories', 0)}",
        )
        return {"FINISHED"}


def realize_decal_instancers_to_unique(map_name: str = "") -> dict[str, int]:
    """Convert existing DecalMesh / poster / plane GN instancers into unique mesh objects."""
    map_name = _pick_map_name_for_scene(map_name) or _resolve_map_name(map_name)
    stats = {
        "instancers": 0, "created": 0, "removed": 0,
        "planes": 0, "decals": 0, "posters": 0,
    }
    if not map_name:
        return stats
    placements = bpy.data.collections.get(f"{map_name}_Placements")
    if placements is None:
        placements = ensure_collection(f"{map_name}_Placements")
    decals = ensure_collection("Decals", parent=placements)
    planes = ensure_collection("Planes", parent=placements)

    for inst in list(_iter_map_instancers(map_name)):
        asset = str(inst.get("arc_asset_path") or "")
        if not needs_unique_mesh_placement(asset, inst.name):
            continue
        is_poster = is_poster_mesh_asset(asset, inst.name)
        is_plane = (
            is_plane_mesh_asset(asset, inst.name)
            and not is_decal_mesh_asset(asset, inst.name)
            and not is_poster
        )
        dest = planes if is_plane else decals
        src_name = str(inst.get("arc_instance_source_name") or "")
        source = bpy.data.objects.get(src_name) if src_name else None
        if source is None or source.type != "MESH" or source.data is None:
            continue
        mesh = inst.data
        rot_attr = mesh.attributes.get(ATTR_ROTATION)
        scale_attr = mesh.attributes.get(ATTR_SCALE)
        if rot_attr is None or scale_attr is None:
            continue
        stats["instancers"] += 1
        mesh_file = str(inst.get("arc_mesh_file") or source.get("arc_mesh_file") or "")
        stem = os.path.splitext(inst.name)[0]
        stem = re.sub(r"_x\d+$", "", stem)
        n = len(mesh.vertices)
        # Recover per-point actor names when stamped on the instancer (optional).
        try:
            from . import materials as mats_mod
        except Exception:
            mats_mod = None
        for i in range(n):
            co = mesh.vertices[i].co
            rot = rot_attr.data[i].vector
            sc = scale_attr.data[i].vector
            new_mesh = source.data.copy()
            name = f"{stem}_{i:04d}"[:60]
            new_mesh.name = f"{name}_Mesh"[:63]
            obj = bpy.data.objects.new(name, new_mesh)
            obj.location = (co.x, co.y, co.z)
            obj.rotation_mode = "XYZ"
            obj.rotation_euler = (rot.x, rot.y, rot.z)
            obj.scale = (sc.x, sc.y, sc.z)
            try:
                obj["arc_map"] = map_name
                obj["arc_asset_path"] = asset
                obj["arc_mesh_file"] = mesh_file
                obj["arc_psk_path"] = mesh_file
                obj["arc_model_type"] = "map"
                obj["arc_materials_pending"] = 1
                obj["arc_unique_placement"] = 1
                if is_plane:
                    obj["arc_plane_mesh"] = 1
                    stats["planes"] += 1
                elif is_poster:
                    obj["arc_poster_mesh"] = 1
                    stats["posters"] += 1
                else:
                    obj["arc_decal_mesh"] = 1
                    stats["decals"] += 1
                # Keep majority water/decal MI from old instancer as a fallback.
                pref = str(inst.get("arc_preferred_mi") or "")
                if pref:
                    try:
                        if mats_mod and mats_mod._preferred_mi_is_single_slot_override(pref):
                            obj["arc_preferred_mi"] = pref
                    except Exception:
                        if "water" in pref.lower() or "decal" in pref.lower() or "crack" in pref.lower():
                            obj["arc_preferred_mi"] = pref
            except Exception:
                pass
            dest.objects.link(obj)
            stats["created"] += 1
        try:
            bpy.data.objects.remove(inst, do_unlink=True)
            stats["removed"] += 1
        except Exception:
            pass
    return stats


class ARC_OT_RealizeDecalInstancers(bpy.types.Operator):
    """Convert DecalMesh / poster / plane / branding GN instancers into unique objects."""

    bl_idname = "arc.realize_decal_instancers"
    bl_label = "Realize Unique Instancers (instance fixer)"
    bl_description = (
        "Instance fixer: expand existing DecalMesh / branding-poster / sticker / "
        "water-plane / graphic-prop Geometry Nodes instancers into unique mesh "
        "objects (duplicated datablocks) so Stage 2 can assign materials per "
        "placement (e.g. GraphicAtlas posters, MinorSwamp vs DuneLagoon water MIs)"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        map_name = getattr(scene, "arc_placement_map", "") or ""
        if not map_name or map_name == "NONE":
            map_name = getattr(scene, "arc_placement_map_name", "") or ""
        stats = realize_decal_instancers_to_unique(map_name)
        self.report(
            {"INFO"},
            f"Realized: {stats.get('created', 0)} objects "
            f"(planes={stats.get('planes', 0)} decals={stats.get('decals', 0)} "
            f"posters={stats.get('posters', 0)}) from "
            f"{stats.get('instancers', 0)} instancer(s). Re-run Stage 2.",
        )
        return {"FINISHED"}


class ARC_OT_GroupMapCollections(bpy.types.Operator):
    """Organize foliage, light modifiers, sky spheres, debris, and hide helper meshes."""

    bl_idname = "arc.group_map_collections"
    bl_label = "Group Map Collections"
    bl_description = (
        "Re-runnable organizer: foliage (+ Snow/Overgrowth), Light Modifiers, Skybox/Spheres, "
        "Debris Tiles, Decals, Planes, {Map}_Glass (transparent / BrokenGlass — costly to render), "
        "{Map}_StaticMeshActors (CSV StaticMesh / StaticMeshActor), "
        "and Helpers (Occluders / Landscape LOD / Collision) hidden by default"
    )
    bl_options = {"REGISTER", "UNDO"}

    hide_helpers: bpy.props.BoolProperty(
        name="Hide Helpers",
        description="Hide occluders, landscape LOD tiles, light blockers, collision proxies",
        default=True,
    )
    include_foliage: bpy.props.BoolProperty(
        name="Include Foliage",
        description="Also run Group Map Foliage (Trees/Vines/Bushes/Grass/Overgrowth/Other)",
        default=True,
    )
    include_static_mesh_actors: bpy.props.BoolProperty(
        name="StaticMeshActors",
        description="Move StaticMesh / StaticMeshActor placements into {Map}_StaticMeshActors",
        default=True,
    )
    include_glass: bpy.props.BoolProperty(
        name="Glass",
        description=(
            "Move meshes with glass / BrokenGlass slots into {Map}_Glass. "
            "Transparent materials need special handling and cost more to render"
        ),
        default=True,
    )

    def execute(self, context):
        scene = context.scene
        map_name = getattr(scene, "arc_placement_map", "") or ""
        if not map_name or map_name == "NONE":
            map_name = getattr(scene, "arc_placement_map_name", "") or ""
        stats = group_map_collections(
            map_name,
            hide_helpers=bool(self.hide_helpers),
            include_foliage=bool(self.include_foliage),
            include_static_mesh_actors=bool(self.include_static_mesh_actors),
            include_glass=bool(self.include_glass),
        )
        self.report(
            {"INFO"},
            f"Map groups: moved={stats.get('moved', 0)} "
            f"foliage={stats.get('foliage_moved', 0)} "
            f"glass={stats.get('glass_moved', 0)} "
            f"sma={stats.get('sma_moved', 0)} "
            f"hidden={stats.get('hidden', 0)} "
            f"cats={stats.get('categories', 0)}",
        )
        return {"FINISHED"}


class ARC_OT_AuditMapMaterials(bpy.types.Operator):
    """List unique mesh types with broken / white / incomplete materials."""

    bl_idname = "arc.audit_map_materials"
    bl_label = "Audit Map Materials"
    bl_description = (
        "Scan map meshes for missing/white/WorldGrid/incomplete materials; "
        "group by unique SM type; write docs/MATERIAL_AUDIT_*.md + CSV with "
        "SM/MI JSON notes, library matches, similar meshes (glass vs PropTrim called out). "
        "Selects representatives of broken types"
    )
    bl_options = {"REGISTER", "UNDO"}

    only_selected: bpy.props.BoolProperty(
        name="Only Selected",
        description="Only audit selected meshes",
        default=False,
    )
    select_reps: bpy.props.BoolProperty(
        name="Select Representatives",
        description="Select one representative object per unique broken type",
        default=True,
    )

    def execute(self, context):
        from . import materials as mats_mod

        scene = context.scene
        map_name = getattr(scene, "arc_placement_map", "") or ""
        if not map_name or map_name == "NONE":
            map_name = getattr(scene, "arc_placement_map_name", "") or ""
        report = mats_mod.audit_map_materials(
            context,
            map_name,
            only_selected=bool(self.only_selected),
            write_report=True,
        )
        if self.select_reps:
            reps = []
            for g in report.get("broken") or []:
                name = g.get("representative") or ""
                obj = bpy.data.objects.get(name) if name else None
                if obj is not None:
                    reps.append(obj)
            if reps:
                try:
                    bpy.ops.object.select_all(action="DESELECT")
                except Exception:
                    pass
                for obj in reps:
                    try:
                        obj.select_set(True)
                    except Exception:
                        pass
                try:
                    context.view_layer.objects.active = reps[0]
                except Exception:
                    pass
        paths = report.get("paths") or []
        path_note = os.path.basename(paths[0]) if paths else "(no file)"
        self.report(
            {"INFO"},
            f"Audit: {report.get('unique_broken_types', 0)} broken types / "
            f"{report.get('broken_meshes', 0)} meshes "
            f"(fuzzy={'ON' if report.get('fuzzy_enabled') else 'OFF'}) → {path_note}",
        )
        for p in paths:
            print(f"Arc Raiders material audit wrote: {p}")
        return {"FINISHED"}


class ARC_OT_SimplifyMapSources(bpy.types.Operator):
    """Decimate unique InstanceSources meshes once — Eevee-friendly global simplify for GN maps."""

    bl_idname = "arc.simplify_map_sources"
    bl_label = "Simplify Map Sources (Decimate)"
    bl_description = (
        "Add a Decimate modifier on each unique SRC mesh (InstanceSources). "
        "Geometry Nodes instances pick up the evaluated mesh — one simplify for all copies. "
        "Non-destructive by default; use Clear Map Source Simplify to restore. "
        "Also: hide Helpers/Foliage collections + Enable Scene Simplify for more Eevee FPS"
    )
    bl_options = {"REGISTER", "UNDO"}

    ratio: bpy.props.FloatProperty(
        name="Keep Ratio",
        description="Decimate collapse ratio (1.0 = full mesh, 0.35 ≈ keep 35% of faces)",
        default=0.35,
        min=0.01,
        max=1.0,
        subtype="FACTOR",
    )
    min_vertices: bpy.props.IntProperty(
        name="Min Vertices",
        description="Skip SRC meshes below this vertex count (small props stay crisp)",
        default=500,
        min=0,
        max=1_000_000,
    )
    exclude_foliage: bpy.props.BoolProperty(
        name="Exclude Foliage",
        description="Skip Trees/Vines/Bushes/Grass/Overgrowth SRC meshes",
        default=True,
    )
    exclude_helpers: bpy.props.BoolProperty(
        name="Exclude Helpers",
        description="Skip occluders, light blockers, collision proxies",
        default=True,
    )
    exclude_landscape: bpy.props.BoolProperty(
        name="Exclude Landscape",
        description="Skip SM_Landscape_* / backdrop landscape SRC meshes",
        default=True,
    )
    exclude_sky: bpy.props.BoolProperty(
        name="Exclude Sky / Spheres",
        description="Skip skybox and cloud sphere SRC meshes",
        default=True,
    )
    apply_modifier: bpy.props.BoolProperty(
        name="Apply (Destructive)",
        description="Bake Decimate into mesh data — faster draw, not reversible without reimport",
        default=False,
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "ratio")
        layout.prop(self, "min_vertices")
        col = layout.column(align=True)
        col.prop(self, "exclude_foliage")
        col.prop(self, "exclude_helpers")
        col.prop(self, "exclude_landscape")
        col.prop(self, "exclude_sky")
        layout.prop(self, "apply_modifier")
        layout.label(text="Tip: hide Foliage/Helpers collections for more FPS", icon="INFO")

    def execute(self, context):
        scene = context.scene
        map_name = getattr(scene, "arc_placement_map", "") or ""
        if not map_name or map_name == "NONE":
            map_name = getattr(scene, "arc_placement_map_name", "") or ""
        stats = simplify_map_source_meshes(
            map_name,
            ratio=float(self.ratio),
            min_vertices=int(self.min_vertices),
            exclude_foliage=bool(self.exclude_foliage),
            exclude_helpers=bool(self.exclude_helpers),
            exclude_landscape=bool(self.exclude_landscape),
            exclude_sky=bool(self.exclude_sky),
            apply=bool(self.apply_modifier),
        )
        mode = "applied" if self.apply_modifier else "modifier"
        self.report(
            {"INFO"},
            f"SRC simplify ({mode}): touched={stats.get('touched', 0)} "
            f"excluded={stats.get('excluded', 0)} "
            f"skipped={stats.get('skipped', 0)} "
            f"ratio={stats.get('ratio', 0):.2f}",
        )
        return {"FINISHED"}


class ARC_OT_ClearMapSourceSimplify(bpy.types.Operator):
    """Remove ARC Map Simplify Decimate modifiers from InstanceSources meshes."""

    bl_idname = "arc.clear_map_source_simplify"
    bl_label = "Clear Map Source Simplify"
    bl_description = (
        "Remove non-destructive ARC Map Simplify Decimate modifiers from SRC meshes. "
        "Does nothing if modifiers were already Applied"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        map_name = getattr(scene, "arc_placement_map", "") or ""
        if not map_name or map_name == "NONE":
            map_name = getattr(scene, "arc_placement_map_name", "") or ""
        stats = clear_map_source_simplify(map_name)
        self.report({"INFO"}, f"Cleared SRC simplify modifiers: {stats.get('removed', 0)}")
        return {"FINISHED"}


class ARC_OT_StripShellOriginPileDetails(bpy.types.Operator):
    """Strip detail props piled on building-shell origins (FModel socket AbsoluteTransform bug)."""

    bl_idname = "arc.strip_shell_origin_pile_details"
    bl_label = "Strip Shell Origin Pile Details"
    bl_description = (
        "Remove non-shell instance points that share a pose with SM_BC_Building_* shells. "
        "Stopgap for child-at-root exports; re-export from FModel after AttachSocketName fix"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        map_name = getattr(scene, "arc_placement_map", "") or ""
        if not map_name or map_name == "NONE":
            map_name = getattr(scene, "arc_placement_map_name", "") or ""
        stats = strip_shell_origin_pile_details(map_name)
        self.report(
            {"INFO"},
            f"Shell piles={stats.get('shell_piles', 0)} "
            f"removed={stats.get('points_removed', 0)} "
            f"instancers={stats.get('instancers_touched', 0)}",
        )
        return {"FINISHED"}


class ARC_OT_UnflipMapSourceMeshes(bpy.types.Operator):
    """Bake Y-unflip on map SRC meshes so shells coincide with socket-placed details."""

    bl_idname = "arc.unflip_map_source_meshes"
    bl_label = "Unflip Map Source Meshes (Y)"
    bl_description = (
        "UEFormat often stores map meshes with Y negated vs Unreal sockets. Combined with "
        "MAP_MIRROR_Y instance scale.y<0 this mirrors shells away from details. "
        "Bake co.y*=-1 on SRC meshes (idempotent). Required after reimport until import path runs it"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        map_name = getattr(scene, "arc_placement_map", "") or ""
        if not map_name or map_name == "NONE":
            map_name = getattr(scene, "arc_placement_map_name", "") or ""
        stats = unflip_all_map_source_meshes(map_name)
        diag = diagnose_building_shell_detail_alignment(map_name)
        same = sum(1 for r in diag if r.get("same_side"))
        self.report(
            {"INFO"},
            f"SRC Y-unflip: done={stats.get('unflipped', 0)} "
            f"skipped={stats.get('skipped', 0)} | "
            f"C45 same-side={same}/{len(diag)}",
        )
        return {"FINISHED"}


class ARC_OT_DiagnoseShellDetailAlignment(bpy.types.Operator):
    """Print shell-vs-detail same-side metrics for residential C45."""

    bl_idname = "arc.diagnose_shell_detail_alignment"
    bl_label = "Diagnose Shell/Detail Alignment"
    bl_description = (
        "For C45 residential shells, report whether nearby WindowMolding pivots share "
        "the shell body's side of the pivot (xy_dot>0) after mesh Y-unflip"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        map_name = getattr(scene, "arc_placement_map", "") or ""
        if not map_name or map_name == "NONE":
            map_name = getattr(scene, "arc_placement_map_name", "") or ""
        rows = diagnose_building_shell_detail_alignment(map_name)
        if not rows:
            self.report({"WARNING"}, "No C45 shell instancer found")
            return {"CANCELLED"}
        for r in rows:
            print("[arc shell/detail]", r)
        same = sum(1 for r in rows if r.get("same_side"))
        self.report(
            {"INFO"},
            f"C45 shells checked={len(rows)} same_side={same} "
            f"(see console). src_unflipped={rows[0].get('src_y_unflipped')}",
        )
        return {"FINISHED"}


class ARC_OT_ImportLastFModelExport(bpy.types.Operator):
    """Find the newest FModel MapPlacements CSV and wire it for import."""
    bl_idname = "arc.import_last_fmodel_export"
    bl_label = "Import Last FModel Export"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from .map_tools import fmodel_bridge as bridge

        scene = context.scene
        roots = []
        # Prefer ModelDirectory-style roots next to Pioneer / FModel settings
        fmd = getattr(scene, "arc_fmdex_root", "") or ""
        if fmd:
            roots.append(os.path.dirname(bpy.path.abspath(fmd)))
            roots.append(bpy.path.abspath(fmd))
        csv_path, map_name = bridge.find_latest_fmodel_export(roots)
        if not csv_path:
            self.report(
                {"ERROR"},
                "No placements.csv found under MapPlacements — export from FModel Snooper first",
            )
            return {"CANCELLED"}
        dest_csv, map_name = bridge.ingest_fmodel_payload(
            {"CsvPath": csv_path, "MapName": map_name, "ImportMode": "instanced"},
            scene,
        )
        self.report({"INFO"}, f"Loaded {map_name}: {os.path.basename(dest_csv)}")
        return bpy.ops.arc.import_placement_instanced("INVOKE_DEFAULT")


_OWNER = object()
_selection_msgbus_handles: list = []
_last_instancer_focus_name: list[str] = [""]


def get_instancer_source_object(obj: bpy.types.Object | None) -> bpy.types.Object | None:
    """Resolve the SRC mesh for a Fast-import GN instancer (or return unique mesh as-is)."""
    if obj is None or obj.type != "MESH":
        return None
    if obj.get("arc_unique_placement") or obj.get("arc_decal_mesh") or obj.get("arc_poster_mesh") or obj.get("arc_plane_mesh"):
        return obj
    if not obj.get("arc_placement_instancer"):
        # Already a source, or a normal mesh
        if obj.get("arc_instance_source"):
            return obj
        return None
    src_name = str(obj.get("arc_instance_source_name") or "").strip()
    if not src_name:
        return None
    source = bpy.data.objects.get(src_name)
    if source is None or source.type != "MESH":
        return None
    return source


def pin_shader_editors_to_object_material(obj: bpy.types.Object | None) -> str:
    """Pin every Shader Editor to ``obj``'s active / first material. Returns material name.

    Blender 3–5 ``SpaceNodeEditor``: set ``tree_type`` to ShaderNodeTree, assign
    ``id`` to the Material (pinned), and fall back to ``node_tree`` if ``id`` fails.
    """
    if obj is None or obj.type != "MESH":
        return ""
    mat = obj.active_material
    if mat is None:
        for slot in obj.material_slots:
            if slot.material is not None:
                mat = slot.material
                break
    if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return ""
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "NODE_EDITOR":
                continue
            space = area.spaces.active
            if space is None:
                continue
            try:
                tree_type = getattr(space, "tree_type", None)
                if tree_type and tree_type != "ShaderNodeTree":
                    continue
            except Exception:
                continue
            try:
                if hasattr(space, "tree_type"):
                    space.tree_type = "ShaderNodeTree"
            except Exception:
                pass
            try:
                # Prefer material ID so the graph stays pinned across selection changes
                space.pin = True
                space.id = mat
                if hasattr(space, "node_tree"):
                    space.node_tree = mat.node_tree
            except Exception:
                try:
                    space.pin = True
                    if hasattr(space, "node_tree"):
                        space.node_tree = mat.node_tree
                except Exception:
                    continue
            try:
                area.tag_redraw()
            except Exception:
                pass
    return mat.name


def focus_instancer_material_from_selection(context=None) -> tuple[str, str]:
    """If active object is a map instancer, pin its SRC material. Returns (src_name, mat_name)."""
    ctx = context or bpy.context
    obj = getattr(ctx, "active_object", None)
    source = get_instancer_source_object(obj)
    if source is None:
        return "", ""
    # Unique meshes: just pin their own material
    if obj is not None and (
        obj.get("arc_unique_placement")
        or obj.get("arc_decal_mesh")
        or obj.get("arc_poster_mesh")
        or obj.get("arc_plane_mesh")
    ):
        mat_name = pin_shader_editors_to_object_material(obj)
        return obj.name, mat_name
    if obj is not None and obj.get("arc_placement_instancer"):
        mat_name = pin_shader_editors_to_object_material(source)
        return source.name, mat_name
    if obj is not None and obj.get("arc_instance_source"):
        mat_name = pin_shader_editors_to_object_material(obj)
        return obj.name, mat_name
    return "", ""


def _on_active_object_changed(*_args):
    """Msgbus: selecting a GN instancer pins the SRC material in Shader Editors."""
    try:
        scene = bpy.context.scene
        if scene is None or not getattr(scene, "arc_map_focus_instancer_material", True):
            return
        obj = bpy.context.view_layer.objects.active
        if obj is None:
            return
        if not (
            obj.get("arc_placement_instancer")
            or obj.get("arc_unique_placement")
            or obj.get("arc_decal_mesh")
            or obj.get("arc_poster_mesh")
            or obj.get("arc_plane_mesh")
        ):
            return
        # Debounce identical focus
        key = obj.name
        if key == _last_instancer_focus_name[0]:
            return
        src_name, mat_name = focus_instancer_material_from_selection(bpy.context)
        if src_name or mat_name:
            _last_instancer_focus_name[0] = key
    except Exception:
        pass


def _instancer_focus_timer_poll():
    """Fallback when msgbus subscribe fails — light poll of active object."""
    try:
        _on_active_object_changed()
    except Exception:
        pass
    return 0.25


def register_instancer_material_focus():
    """Subscribe to active-object changes (call from addon register)."""
    unregister_instancer_material_focus()
    try:
        # msgbus on LayerObjects.active works in Blender 3+ (depsgraph paths are flaky).
        handle = object()
        bpy.msgbus.subscribe_rna(
            key=(bpy.types.LayerObjects, "active"),
            owner=_OWNER,
            args=(),
            notify=_on_active_object_changed,
            options={"PERSISTENT"},
        )
        _selection_msgbus_handles.append(handle)
    except Exception:
        try:
            if not bpy.app.timers.is_registered(_instancer_focus_timer_poll):
                bpy.app.timers.register(
                    _instancer_focus_timer_poll, first_interval=0.5, persistent=True
                )
        except Exception:
            pass


def unregister_instancer_material_focus():
    try:
        bpy.msgbus.clear_by_owner(_OWNER)
    except Exception:
        pass
    try:
        if bpy.app.timers.is_registered(_instancer_focus_timer_poll):
            bpy.app.timers.unregister(_instancer_focus_timer_poll)
    except Exception:
        pass
    _selection_msgbus_handles.clear()
    _last_instancer_focus_name[0] = ""


class ARC_OT_FocusInstancerSourceMaterial(bpy.types.Operator):
    """Pin Shader Editor to the SRC material of the selected map instancer."""

    bl_idname = "arc.focus_instancer_source_material"
    bl_label = "Focus Instancer Material"
    bl_description = (
        "Pin Shader Node Editors to the Instance Source mesh material for the active "
        "Fast-import instancer (or the selected unique plane/decal)"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        src_name, mat_name = focus_instancer_material_from_selection(context)
        if not mat_name:
            self.report(
                {"WARNING"},
                "Select a map instancer (or unique plane/decal) that has Stage 2 materials",
            )
            return {"CANCELLED"}
        self.report({"INFO"}, f"Shader Editor → {mat_name} (source {src_name})")
        return {"FINISHED"}


class ARC_OT_SelectInstancerSource(bpy.types.Operator):
    """Select the InstanceSources SRC mesh for the active GN instancer."""

    bl_idname = "arc.select_instancer_source"
    bl_label = "Select Instance Source"
    bl_description = (
        "Select the SRC_* mesh in InstanceSources that this Fast-import instancer instances. "
        "InstanceSources is required for GN Fast import (Object Info needs an evaluable object); "
        "the collection name is just organization"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        source = get_instancer_source_object(obj)
        if source is None:
            self.report({"WARNING"}, "Active object is not a map instancer with a SRC link")
            return {"CANCELLED"}
        # Un-hide source enough to select
        try:
            source.hide_set(False)
            source.hide_select = False
            source.hide_viewport = False
        except Exception:
            pass
        bpy.ops.object.select_all(action="DESELECT")
        source.select_set(True)
        context.view_layer.objects.active = source
        pin_shader_editors_to_object_material(source)
        self.report({"INFO"}, f"Selected source {source.name}")
        return {"FINISHED"}


OPERATOR_CLASSES = (
    ARC_OT_ImportPlacementEmpties,
    ARC_OT_ImportPlacementMeshes,
    ARC_OT_ImportPlacementInstanced,
    ARC_OT_ApplyMapMaterials,
    ARC_OT_ApplyMaterialsByMeshName,
    ARC_OT_FixWhiteUnassignedMaterials,
    ARC_OT_RepairSmaTrimMaterials,
    ARC_OT_RefreshWaterShoreProximity,
    ARC_OT_AuditMapMaterials,
    ARC_OT_FixPlacementRotations,
    ARC_OT_FixMapOrientation,
    ARC_OT_ScaleMapToMeters,
    ARC_OT_RemoveDuplicatePlacements,
    ARC_OT_ImportPlacementHeightmapPlane,
    ARC_OT_ReloadPlacementHeightmap,
    ARC_OT_ApplyHeightmapSandMaterial,
    ARC_OT_PickPlacementInGameMap,
    ARC_OT_ClearPlacementInGameMap,
    ARC_OT_PickPlacementHlodColor,
    ARC_OT_ClearPlacementHlodColor,
    ARC_OT_ResolveHeightmapGroundRefs,
    ARC_OT_FixImportSplines,
    ARC_OT_GroupMapFoliage,
    ARC_OT_GroupMapCollections,
    ARC_OT_RealizeDecalInstancers,
    ARC_OT_FocusInstancerSourceMaterial,
    ARC_OT_SelectInstancerSource,
    ARC_OT_SimplifyMapSources,
    ARC_OT_ClearMapSourceSimplify,
    ARC_OT_StripShellOriginPileDetails,
    ARC_OT_UnflipMapSourceMeshes,
    ARC_OT_DiagnoseShellDetailAlignment,
    ARC_OT_PickPlacementCSV,
    ARC_OT_PickPlacementBounds,
    ARC_OT_PickPlacementHeightmapImage,
    ARC_OT_PickPlacementWorkspace,
    ARC_OT_ClearPlacementWorkspace,
    ARC_OT_RefreshPlacementMaps,
    ARC_OT_ExtractMapPlacements,
    ARC_OT_GeneratePlacementOverlay,
    ARC_OT_FramePlacementView,
    ARC_OT_OpenPlacementWorkspace,
    ARC_OT_StartPlacementListener,
    ARC_OT_StopPlacementListener,
    ARC_OT_ImportLastFModelExport,
)
