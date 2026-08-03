#!/usr/bin/env python3
"""
Extract actor placements from exported World Partition JSON (backup path).

Primary workflow: FModel-Vibe resolves transforms and pushes/exports CSV;
DataRaiders receives that CSV. Use this script only when FModel is unavailable.

Streams `_Generated_` / persistent-level JSONs, skips heightmap giants, resolves
root (or preferred named) component transforms, and writes CSVs under a neat
workspace folder: `{workspace}/{MapName}/` (preferred) or legacy `{map}/_PropHarvest/`.

Example:
  python map_placement_extract.py --map-dir "<PioneerGame Root>/Content/Pioneer/Maps/FrozenTrail_01" --workspace "D:/ArcPlacement"
  python map_placement_extract.py --map-dir "<Maps>/<MapName>" --prefer-named-component Building
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from .json_stream import iter_top_level_objects_skipping_heightmaps as _iter_top_level_objects_skipping_heightmaps
except ImportError:
    from json_stream import iter_top_level_objects_skipping_heightmaps as _iter_top_level_objects_skipping_heightmaps  # type: ignore

CSV_COLUMNS = [
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
]

# Files larger than this are treated as heightmap cells unless forced
HEIGHTMAP_SIZE_BYTES = 200 * 1024 * 1024

SKIP_ACTOR_SUBSTR = (
    "EmbarkHierarchicalHeightMap",
    "EmbarkOctreeNavigation",
    "EmbarkLensFlare",
    "Landscape",
    "NavMesh",
    "NavModifier",
    "WorldPartition",
    "LevelInstance",
    "HLOD",
    "DDGI_",
    "AtmosphericFog",
    "SkyAtmosphere",
    "ExponentialHeightFog",
    "SkyLight",
    "DirectionalLight",
    "PostProcessVolume",
    "WorldSettings",
    "DefaultPhysicsVolume",
    "Brush",
)

SKIP_TYPE_EXACT = {
    "BodySetup",
    "Texture2D",
    "DynamicMesh",
    "LandscapeMaterialInstanceConstant",
    "LandscapeComponent",
    "LandscapeHeightfieldCollisionComponent",
    "LandscapeStreamingProxy",
    "ObjectScatterComponent",
}

COMPONENT_TYPES = {
    "SceneComponent",
    "StaticMeshComponent",
    "SplineComponent",
    "SplineMeshComponent",
    "InstancedStaticMeshComponent",
    "HierarchicalInstancedStaticMeshComponent",
    "SpotLightComponent",
    "PointLightComponent",
    "RectLightComponent",
    "TextRenderComponent",
    "BillboardComponent",
    "ArrowComponent",
    "CapsuleComponent",
    "BoxComponent",
    "SphereComponent",
    "ChildActorComponent",
}

PATH_INDEX_RE = re.compile(r"\.\d+$")
OUTER_ACTOR_RE = re.compile(r"'([^']+)'$")
COMPONENT_REF_RE = re.compile(r"'([^']+)'$")


def normalize_package(path: str) -> str:
    """'/Game/Foo/Bar.Baz.0' or soft path -> '/Game/Foo/Bar' package."""
    path = (path or "").strip().rstrip("',\"")
    if not path:
        return ""
    # Class'/Game/Foo/Bar.Bar_C' style
    m = re.search(r"'((?:/Game|/Engine|/EmbarkScript)/[^']+)'", path)
    if m:
        path = m.group(1)
    path = PATH_INDEX_RE.sub("", path)
    if path.count("/") >= 2:
        base = path.rsplit("/", 1)[-1]
        if "." in base:
            pkg, leaf = base.split(".", 1)
            if leaf == pkg or leaf.endswith("_C") or leaf.startswith(pkg):
                path = path.rsplit("/", 1)[0] + "/" + pkg
            else:
                path = path.rsplit("/", 1)[0] + "/" + pkg
    return path


def parse_vec3(obj: dict[str, Any] | None, default: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    if not obj:
        return default
    return (
        float(obj.get("X", default[0])),
        float(obj.get("Y", default[1])),
        float(obj.get("Z", default[2])),
    )


def parse_rotator(obj: dict[str, Any] | None) -> tuple[float, float, float]:
    if not obj:
        return (0.0, 0.0, 0.0)
    return (
        float(obj.get("Pitch", 0.0)),
        float(obj.get("Yaw", 0.0)),
        float(obj.get("Roll", 0.0)),
    )


def outer_actor_name(obj: dict[str, Any]) -> str:
    """Component Outer -> owning actor name; actor Outer (Level) -> ''."""
    outer = obj.get("Outer") or {}
    name = outer.get("ObjectName") or ""
    m = OUTER_ACTOR_RE.search(name)
    if not m:
        return ""
    inner = m.group(1)
    # Level'Map:PersistentLevel' -> not a component outer
    if "PersistentLevel" in inner and "." not in inner.split(":")[-1]:
        return ""
    # Type'Map:PersistentLevel.ActorName' or ...ActorName.Component
    after = inner.rsplit(":", 1)[-1]
    if after.startswith("PersistentLevel."):
        after = after[len("PersistentLevel.") :]
    # ActorName or ActorName.Comp
    return after.split(".", 1)[0]


def ref_component_name(ref: dict[str, Any] | None) -> str:
    if not ref:
        return ""
    name = ref.get("ObjectName") or ""
    m = COMPONENT_REF_RE.search(name)
    if not m:
        return ""
    inner = m.group(1)
    after = inner.rsplit(":", 1)[-1]
    if after.startswith("PersistentLevel."):
        after = after[len("PersistentLevel.") :]
    parts = after.split(".")
    return parts[-1] if parts else ""


def object_path_from_soft(ref: Any) -> str:
    if isinstance(ref, dict):
        return normalize_package(ref.get("ObjectPath") or "")
    if isinstance(ref, str):
        return normalize_package(ref)
    return ""


def classify_asset_kind(
    asset_path: str,
    lists: dict[str, set[str]],
) -> str:
    if not asset_path:
        return "other"
    # Prefer PropHarvest filter membership
    for kind in ("architecture", "poi", "prop", "props"):
        key = "props" if kind == "prop" else kind
        bucket = lists.get(key) or lists.get(kind) or set()
        if asset_path in bucket:
            return "prop" if kind in ("prop", "props") else kind
    # Building candidates often mix architecture + BP
    if asset_path in (lists.get("building_candidates") or set()):
        if "/Architecture/" in asset_path:
            return "architecture"
        if "/POI/" in asset_path:
            return "poi"
        return "blueprint"
    # Path heuristics
    if "/Architecture/" in asset_path:
        return "architecture"
    if "/Props/" in asset_path:
        return "prop"
    if "/POI/" in asset_path:
        return "poi"
    leaf = asset_path.rsplit("/", 1)[-1]
    if leaf.startswith("BP_") or "/Blueprints/" in asset_path:
        return "blueprint"
    if leaf.startswith("SM_"):
        return "staticmesh"
    return "other"


def load_filter_lists(harvest_dir: Path) -> dict[str, set[str]]:
    """Load optional package lists from _PropHarvest."""
    out: dict[str, set[str]] = {}
    mapping = {
        "architecture": ("frozentrail_architecture.txt", "*_architecture.txt", "architecture.txt"),
        "props": ("frozentrail_props.txt", "*_props.txt", "props.txt"),
        "poi": ("frozentrail_poi.txt", "*_poi.txt", "poi.txt"),
        "building_candidates": (
            "frozentrail_building_candidates.txt",
            "*_building_candidates.txt",
            "building_candidates.txt",
        ),
    }
    if not harvest_dir.is_dir():
        return out
    for key, patterns in mapping.items():
        found: set[str] = set()
        for pat in patterns:
            for path in harvest_dir.glob(pat):
                with path.open("r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            found.add(normalize_package(line))
        if found:
            out[key] = found
    return out


def load_optional_filter_file(path: Path | None) -> set[str] | None:
    if path is None:
        return None
    pkgs: set[str] = set()
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                pkgs.add(normalize_package(line))
    return pkgs


def resolve_out_dir(
    map_dir: Path,
    out_dir: Path | None = None,
    workspace: Path | None = None,
) -> Path:
    """Prefer `{workspace}/{map_name}/`, else explicit --out-dir, else legacy beside the map."""
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir
    if workspace is not None:
        dest = Path(workspace) / map_dir.name
        dest.mkdir(parents=True, exist_ok=True)
        return dest
    prop = map_dir / "_PropHarvest"
    if prop.is_dir():
        return prop
    placement = map_dir / "_Placement"
    placement.mkdir(parents=True, exist_ok=True)
    return placement


def is_heightmap_cell(path: Path) -> bool:
    name = path.name.lower()
    if "heightmap" in name or "height_map" in name:
        return True
    try:
        if path.stat().st_size >= HEIGHTMAP_SIZE_BYTES:
            return True
    except OSError:
        return False
    return False


def iter_map_json_files(
    map_dir: Path,
    include_heightmap: bool = False,
    all_json: bool = False,
) -> list[Path]:
    """
    Default: persistent level (*_P.json) + `_Generated_` cells only.
    Skip heightmap giants by size/name. `--all-json` also walks other map JSONs.
    """
    files: list[Path] = []
    for p in map_dir.rglob("*.json"):
        if p.name.lower().endswith(".metadata.json"):
            continue
        if any(part in ("_PropHarvest", "_Placement") for part in p.parts):
            continue
        if not all_json:
            if "_Generated_" not in p.parts and not p.name.endswith("_P.json"):
                continue
            # Persistent DDGI/HLOD stubs match *_P.json but aren't placement sources
            if p.name.startswith("DDGI_") or "HLOD" in p.name:
                continue
        else:
            # Still skip obvious non-placement dumps
            if any(
                s in p.parts or s in p.name
                for s in ("LightingScenarios", "VirtualTexture", "DDGI_", "HLOD")
            ):
                continue
        if not include_heightmap and is_heightmap_cell(p):
            continue
        files.append(p)
    files.sort(key=lambda p: p.stat().st_size)
    return files


def should_skip_actor(name: str, typ: str) -> bool:
    if typ in SKIP_TYPE_EXACT:
        return True
    for s in SKIP_ACTOR_SUBSTR:
        if s in name or s in typ:
            return True
    if typ.startswith("BP_") and any(
        x in typ for x in ("Audio", "Ambience", "Volume", "Trigger", "Nav")
    ):
        # Keep building BPs; skip ambience volumes heuristically unless Building in name
        if "Building" not in typ and "POI" not in typ and "Architecture" not in typ:
            if any(x in typ for x in ("Audio", "Ambience")):
                return True
    return False


def is_actor_export(obj: dict[str, Any]) -> bool:
    props = obj.get("Properties") or {}
    if "RootComponent" in props:
        return True
    typ = obj.get("Type") or ""
    if typ in ("StaticMeshActor", "DynamicMeshActor", "Actor"):
        return True
    if typ.endswith("_C") and typ.startswith("BP_"):
        return True
    return False


def is_component_export(obj: dict[str, Any]) -> bool:
    typ = obj.get("Type") or ""
    if typ in COMPONENT_TYPES:
        return True
    if typ.endswith("Component") and typ not in SKIP_TYPE_EXACT:
        return True
    return False


class ComponentRec:
    __slots__ = (
        "name",
        "actor",
        "typ",
        "loc",
        "rot",
        "scale",
        "attach_parent",
        "static_mesh",
        "has_loc",
        "has_rot",
        "has_scale",
        "template_path",
        "template_name",
        "instances",
        "hidden",
    )

    def __init__(self, name: str, actor: str, typ: str):
        self.name = name
        self.actor = actor
        self.typ = typ
        self.loc = (0.0, 0.0, 0.0)
        self.rot = (0.0, 0.0, 0.0)
        self.scale = (1.0, 1.0, 1.0)
        self.attach_parent = ""
        self.static_mesh = ""
        self.has_loc = False
        self.has_rot = False
        self.has_scale = False
        self.template_path = ""
        self.template_name = ""
        self.instances: list[dict[str, Any]] = []
        self.hidden = False


class ActorRec:
    __slots__ = ("name", "typ", "root_comp", "template_path", "class_path", "source_file")

    def __init__(self, name: str, typ: str, source_file: str):
        self.name = name
        self.typ = typ
        self.root_comp = ""
        self.template_path = ""
        self.class_path = ""
        self.source_file = source_file


def ingest_file(
    path: Path,
    actors: dict[str, ActorRec],
    components: dict[tuple[str, str], ComponentRec],
) -> int:
    count = 0
    rel = path.name
    for obj in _iter_top_level_objects_skipping_heightmaps(path):
        count += 1
        typ = obj.get("Type") or ""
        name = obj.get("Name") or ""
        if typ in SKIP_TYPE_EXACT:
            continue

        if is_actor_export(obj):
            if should_skip_actor(name, typ):
                continue
            props = obj.get("Properties") or {}
            rec = actors.get(name) or ActorRec(name, typ, rel)
            rec.typ = typ
            rec.source_file = rel
            rec.root_comp = ref_component_name(props.get("RootComponent")) or rec.root_comp
            tpl = object_path_from_soft(obj.get("Template")) or object_path_from_soft(
                props.get("Template")
            )
            if tpl:
                rec.template_path = tpl
            cls = obj.get("Class")
            if isinstance(cls, str):
                cp = normalize_package(cls)
                if cp:
                    rec.class_path = cp
            elif isinstance(cls, dict):
                cp = object_path_from_soft(cls)
                if cp:
                    rec.class_path = cp
            actors[name] = rec
            continue

        if is_component_export(obj):
            actor = outer_actor_name(obj)
            if not actor:
                continue
            props = obj.get("Properties") or {}
            key = (actor, name)
            crec = components.get(key) or ComponentRec(name, actor, typ)
            crec.typ = typ
            if "RelativeLocation" in props:
                crec.loc = parse_vec3(props.get("RelativeLocation"))
                crec.has_loc = True
            if "RelativeRotation" in props:
                crec.rot = parse_rotator(props.get("RelativeRotation"))
                crec.has_rot = True
            if "RelativeScale3D" in props:
                crec.scale = parse_vec3(props.get("RelativeScale3D"), (1.0, 1.0, 1.0))
                crec.has_scale = True
            ap = props.get("AttachParent")
            if ap:
                crec.attach_parent = ref_component_name(ap)
            sm = props.get("StaticMesh")
            if sm:
                crec.static_mesh = object_path_from_soft(sm)
            template = obj.get("Template") or props.get("Template")
            if template:
                crec.template_path = object_path_from_soft(template)
                crec.template_name = ref_component_name(template)
            instance_data = obj.get("PerInstanceSMData") or props.get("PerInstanceSMData")
            if isinstance(instance_data, list):
                crec.instances = instance_data
            crec.hidden = bool(props.get("bHiddenInGame", False))
            components[key] = crec

    return count


def quat_to_rotator(q: dict[str, Any] | None) -> tuple[float, float, float]:
    """Match CUE4Parse FQuat.Rotator() for Unreal pitch/yaw/roll degrees."""
    if not q:
        return (0.0, 0.0, 0.0)
    x = float(q.get("X", 0.0))
    y = float(q.get("Y", 0.0))
    z = float(q.get("Z", 0.0))
    w = float(q.get("W", 1.0))
    singularity_test = z * x - w * y
    yaw_y = 2.0 * (w * z + x * y)
    yaw_x = 1.0 - 2.0 * (y * y + z * z)
    singularity_threshold = 0.4999995
    rad_to_deg = 180.0 / math.pi

    def normalize_axis(angle: float) -> float:
        angle = math.fmod(angle + 180.0, 360.0)
        if angle < 0.0:
            angle += 360.0
        return angle - 180.0

    if singularity_test < -singularity_threshold:
        pitch = -90.0
        yaw = math.atan2(yaw_y, yaw_x) * rad_to_deg
        roll = normalize_axis(-yaw - (2.0 * math.atan2(x, w) * rad_to_deg))
    elif singularity_test > singularity_threshold:
        pitch = 90.0
        yaw = math.atan2(yaw_y, yaw_x) * rad_to_deg
        roll = normalize_axis(yaw - (2.0 * math.atan2(x, w) * rad_to_deg))
    else:
        pitch = math.asin(max(-1.0, min(1.0, 2.0 * singularity_test))) * rad_to_deg
        yaw = math.atan2(yaw_y, yaw_x) * rad_to_deg
        roll = math.atan2(-2.0 * (w * x + y * z), (1.0 - 2.0 * (x * x + y * y))) * rad_to_deg
    return (pitch, yaw, roll)


def parse_instance_transform(entry: dict[str, Any]) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    data = entry.get("TransformData") if isinstance(entry, dict) else None
    if not isinstance(data, dict):
        data = entry if isinstance(entry, dict) else {}
    loc = parse_vec3(data.get("Translation") if isinstance(data, dict) else None)
    rot = quat_to_rotator(data.get("Rotation") if isinstance(data, dict) else None)
    scale = parse_vec3(data.get("Scale3D") if isinstance(data, dict) else None, (1.0, 1.0, 1.0))
    return loc, rot, scale


def multiply_transforms(
    child_loc: tuple[float, float, float],
    child_rot: tuple[float, float, float],
    child_scale: tuple[float, float, float],
    parent_loc: tuple[float, float, float],
    parent_rot: tuple[float, float, float],
    parent_scale: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """Approximate Unreal FTransform child * parent using full pitch/yaw/roll."""
    # Convert parent rotator to basis vectors (Unreal left-handed, Z-up)
    pitch = math.radians(parent_rot[0])
    yaw = math.radians(parent_rot[1])
    roll = math.radians(parent_rot[2])
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cr, sr = math.cos(roll), math.sin(roll)
    # Columns of FRotationMatrix
    x_axis = (cp * cy, cp * sy, sp)
    y_axis = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    z_axis = (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp)

    sx, sy_, sz = parent_scale
    lx, ly, lz = child_loc
    scaled = (lx * sx, ly * sy_, lz * sz)
    wx = parent_loc[0] + scaled[0] * x_axis[0] + scaled[1] * y_axis[0] + scaled[2] * z_axis[0]
    wy = parent_loc[1] + scaled[0] * x_axis[1] + scaled[1] * y_axis[1] + scaled[2] * z_axis[1]
    wz = parent_loc[2] + scaled[0] * x_axis[2] + scaled[1] * y_axis[2] + scaled[2] * z_axis[2]

    # Compose rotations: parent * child (same as FTransform multiplication order for Rotation)
    # Convert to quats then multiply.
    def rotator_to_quat(rot: tuple[float, float, float]) -> tuple[float, float, float, float]:
        p = math.radians(rot[0]) * 0.5
        y = math.radians(rot[1]) * 0.5
        r = math.radians(rot[2]) * 0.5
        sp, cp = math.sin(p), math.cos(p)
        sy, cy = math.sin(y), math.cos(y)
        sr, cr = math.sin(r), math.cos(r)
        return (
            cr * sp * sy - sr * cp * cy,
            -cr * sp * cy - sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
            cr * cp * cy + sr * sp * sy,
        )

    def quat_mul(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        ax, ay, az, aw = a
        bx, by, bz, bw = b
        return (
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz,
        )

    composed = quat_mul(rotator_to_quat(parent_rot), rotator_to_quat(child_rot))
    wrot = quat_to_rotator({"X": composed[0], "Y": composed[1], "Z": composed[2], "W": composed[3]})
    wscale = (
        child_scale[0] * parent_scale[0],
        child_scale[1] * parent_scale[1],
        child_scale[2] * parent_scale[2],
    )
    return (wx, wy, wz), wrot, wscale


_template_file_cache: dict[str, list[dict[str, Any]]] = {}


def resolve_template_mesh_and_instances(
    crec: ComponentRec,
    map_dir: Path,
) -> tuple[str, list[dict[str, Any]]]:
    """Follow Template ObjectPath to Blueprint JSON for StaticMesh / PerInstanceSMData."""
    mesh = crec.static_mesh
    instances = list(crec.instances)
    if not crec.template_path:
        return mesh, instances
    if mesh and instances:
        return mesh, instances

    content_root = None
    for parent in [map_dir, *map_dir.parents]:
        if parent.name == "Content":
            content_root = parent
            break
    if content_root is None:
        return mesh, instances

    pkg = normalize_package(crec.template_path)
    if not pkg.startswith("/Game/"):
        return mesh, instances
    rel = pkg[len("/Game/") :]
    path = content_root / Path(rel + ".json")
    key = str(path)
    if key not in _template_file_cache:
        if not path.is_file():
            _template_file_cache[key] = []
        else:
            try:
                _template_file_cache[key] = json.load(path.open(encoding="utf-8", errors="replace"))
            except Exception:
                _template_file_cache[key] = []

    wanted = crec.template_name or crec.name
    for obj in _template_file_cache[key]:
        if not isinstance(obj, dict):
            continue
        name = obj.get("Name") or ""
        if name != wanted and name != f"{wanted}_GEN_VARIABLE" and wanted not in name:
            continue
        props = obj.get("Properties") or {}
        if not mesh:
            sm = props.get("StaticMesh")
            if sm:
                mesh = object_path_from_soft(sm)
        if not instances:
            raw = obj.get("PerInstanceSMData") or props.get("PerInstanceSMData")
            if isinstance(raw, list):
                instances = raw
        if mesh and (instances or crec.typ == "SplineMeshComponent"):
            break
    return mesh, instances


def compose_local_to_parent(
    loc: tuple[float, float, float],
    rot: tuple[float, float, float],
    scale: tuple[float, float, float],
    parent_loc: tuple[float, float, float],
    parent_rot: tuple[float, float, float],
    parent_scale: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    return multiply_transforms(loc, rot, scale, parent_loc, parent_rot, parent_scale)


def resolve_component_world(
    actor: str,
    comp_name: str,
    components: dict[tuple[str, str], ComponentRec],
    depth: int = 0,
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float], str]:
    """Return loc, rot, scale, notes."""
    if depth > 16:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), "relative_unresolved"
    crec = components.get((actor, comp_name))
    if crec is None:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), "missing_component"
    if not crec.attach_parent:
        return crec.loc, crec.rot, crec.scale, ""
    ploc, prot, pscale, notes = resolve_component_world(
        actor, crec.attach_parent, components, depth + 1
    )
    if notes:
        return crec.loc, crec.rot, crec.scale, notes
    wloc, wrot, wscale = compose_local_to_parent(
        crec.loc, crec.rot, crec.scale, ploc, prot, pscale
    )
    return wloc, wrot, wscale, "composed_attach"


def pick_pose_component(
    actor: ActorRec,
    components: dict[tuple[str, str], ComponentRec],
    prefer_named: str,
) -> tuple[str, str]:
    """Return (component_name, notes_hint)."""
    if prefer_named:
        pref = components.get((actor.name, prefer_named))
        if pref is not None and not pref.attach_parent and (pref.has_loc or pref.has_rot):
            return prefer_named, f"prefer_named:{prefer_named}"
    if actor.root_comp:
        return actor.root_comp, "root"
    # Fallback: any component without attach parent that has a location
    for (an, cn), crec in components.items():
        if an != actor.name:
            continue
        if not crec.attach_parent and crec.has_loc:
            return cn, "fallback_unattached"
    return "", "no_component"


def _make_row(
    *,
    actor_name: str,
    asset_path: str,
    asset_kind: str,
    loc: tuple[float, float, float],
    rot: tuple[float, float, float],
    scale: tuple[float, float, float],
    source_file: str,
    notes: str,
) -> dict[str, Any]:
    return {
        "actor_name": actor_name,
        "asset_path": asset_path,
        "asset_kind": asset_kind,
        "x": f"{loc[0]:.6f}",
        "y": f"{loc[1]:.6f}",
        "z": f"{loc[2]:.6f}",
        "pitch": f"{rot[0]:.6f}",
        "yaw": f"{rot[1]:.6f}",
        "roll": f"{rot[2]:.6f}",
        "scale_x": f"{scale[0]:.6f}",
        "scale_y": f"{scale[1]:.6f}",
        "scale_z": f"{scale[2]:.6f}",
        "source_file": source_file,
        "notes": notes,
    }


def build_rows(
    actors: dict[str, ActorRec],
    components: dict[tuple[str, str], ComponentRec],
    lists: dict[str, set[str]],
    prefer_named: str,
    filter_pkgs: set[str] | None,
    map_dir: Path | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    emitted_components: set[tuple[str, str]] = set()

    for actor in actors.values():
        if should_skip_actor(actor.name, actor.typ):
            continue
        comp_name, pick_note = pick_pose_component(actor, components, prefer_named)
        notes_parts: list[str] = []
        if pick_note and pick_note not in ("root",):
            notes_parts.append(pick_note)

        loc = (0.0, 0.0, 0.0)
        rot = (0.0, 0.0, 0.0)
        scale = (1.0, 1.0, 1.0)
        crec = None
        if comp_name:
            crec = components.get((actor.name, comp_name))
            wloc, wrot, wscale, rnotes = resolve_component_world(
                actor.name, comp_name, components
            )
            loc, rot, scale = wloc, wrot, wscale
            if rnotes:
                notes_parts.append(rnotes)
            emitted_components.add((actor.name, comp_name))
        else:
            notes_parts.append("relative_unresolved")

        asset_path = ""
        if crec and crec.static_mesh:
            asset_path = crec.static_mesh
        if not asset_path:
            asset_path = actor.template_path or actor.class_path
        if not asset_path and prefer_named:
            pref = components.get((actor.name, prefer_named))
            if pref and pref.static_mesh:
                asset_path = pref.static_mesh

        asset_kind = classify_asset_kind(asset_path, lists)
        if filter_pkgs is not None and asset_path not in filter_pkgs:
            typ_pkg = normalize_package(actor.template_path or "")
            if asset_path not in filter_pkgs and typ_pkg not in filter_pkgs:
                stem = asset_path.rsplit("/", 1)[-1] if asset_path else ""
                if not any(stem and stem == p.rsplit("/", 1)[-1] for p in filter_pkgs):
                    if asset_path not in filter_pkgs:
                        continue

        rows.append(
            _make_row(
                actor_name=actor.name,
                asset_path=asset_path,
                asset_kind=asset_kind,
                loc=loc,
                rot=rot,
                scale=scale,
                source_file=actor.source_file,
                notes=";".join(notes_parts),
            )
        )

    # Extra rows: ISM/HISM instances and spline meshes (component pose; deformation out of scope)
    for (actor_name, comp_name), crec in components.items():
        if crec.hidden:
            continue
        actor = actors.get(actor_name)
        source_file = actor.source_file if actor else ""
        is_instanced = crec.typ in (
            "InstancedStaticMeshComponent",
            "HierarchicalInstancedStaticMeshComponent",
        )
        is_spline = crec.typ == "SplineMeshComponent"

        if not is_instanced and not is_spline:
            continue

        mesh = crec.static_mesh
        instances: list[dict[str, Any]] = list(crec.instances)
        if map_dir is not None and (not mesh or (is_instanced and not instances)):
            mesh, instances = resolve_template_mesh_and_instances(crec, map_dir)
        if not mesh:
            continue
        if "/EditorLandscapeResources/SplineEditorMesh" in mesh:
            continue

        base_loc, base_rot, base_scale, rnotes = resolve_component_world(
            actor_name, comp_name, components
        )
        asset_kind = classify_asset_kind(mesh, lists)

        if is_spline:
            notes = ["SplineMesh"]
            if rnotes:
                notes.append(rnotes)
            rows.append(
                _make_row(
                    actor_name=f"{actor_name}.{comp_name}",
                    asset_path=mesh,
                    asset_kind=asset_kind if asset_kind != "other" else "staticmesh",
                    loc=base_loc,
                    rot=base_rot,
                    scale=base_scale,
                    source_file=source_file,
                    notes=";".join(notes),
                )
            )
            continue

        if not instances:
            # No PerInstanceSMData even after template walk: still emit the component once.
            notes = [crec.typ, "no_instances"]
            if rnotes:
                notes.append(rnotes)
            rows.append(
                _make_row(
                    actor_name=f"{actor_name}.{comp_name}",
                    asset_path=mesh,
                    asset_kind=asset_kind if asset_kind != "other" else "staticmesh",
                    loc=base_loc,
                    rot=base_rot,
                    scale=base_scale,
                    source_file=source_file,
                    notes=";".join(notes),
                )
            )
            continue

        for idx, entry in enumerate(instances):
            iloc, irot, iscale = parse_instance_transform(entry)
            wloc, wrot, wscale = multiply_transforms(
                iloc, irot, iscale, base_loc, base_rot, base_scale
            )
            notes = [crec.typ, f"instance:{idx}"]
            if rnotes:
                notes.append(rnotes)
            rows.append(
                _make_row(
                    actor_name=f"{actor_name}.{comp_name}[{idx}]",
                    asset_path=mesh,
                    asset_kind=asset_kind if asset_kind != "other" else "staticmesh",
                    loc=wloc,
                    rot=wrot,
                    scale=wscale,
                    source_file=source_file,
                    notes=";".join(notes),
                )
            )

    return rows


def is_building_row(row: dict[str, Any], lists: dict[str, set[str]]) -> bool:
    kind = row.get("asset_kind") or ""
    if kind in ("architecture", "poi", "blueprint"):
        return True
    path = row.get("asset_path") or ""
    if path in (lists.get("building_candidates") or set()):
        return True
    if path in (lists.get("architecture") or set()):
        return True
    if path in (lists.get("poi") or set()):
        return True
    name = row.get("actor_name") or ""
    if "Building" in name or name.startswith("BP_BC_Building"):
        return True
    return False


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: list[dict[str, Any]], buildings: list[dict[str, Any]], map_name: str) -> dict[str, Any]:
    def bbox(rs: list[dict[str, Any]]) -> dict[str, float] | None:
        if not rs:
            return None
        xs = [float(r["x"]) for r in rs]
        ys = [float(r["y"]) for r in rs]
        zs = [float(r["z"]) for r in rs]
        return {
            "min_x": min(xs),
            "max_x": max(xs),
            "min_y": min(ys),
            "max_y": max(ys),
            "min_z": min(zs),
            "max_z": max(zs),
        }

    kinds: dict[str, int] = defaultdict(int)
    unresolved = 0
    for r in rows:
        kinds[r["asset_kind"]] += 1
        if "relative_unresolved" in (r.get("notes") or ""):
            unresolved += 1
    return {
        "map_name": map_name,
        "total_placements": len(rows),
        "building_placements": len(buildings),
        "unresolved_attach_count": unresolved,
        "by_kind": dict(sorted(kinds.items())),
        "bbox_all": bbox(rows),
        "bbox_buildings": bbox(buildings),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract map actor placements to CSV")
    ap.add_argument(
        "--map-dir",
        required=True,
        type=Path,
        help="Map folder under Pioneer/Maps (e.g. FrozenTrail_01)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Exact output directory (overrides --workspace)",
    )
    ap.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Central folder; writes to {workspace}/{MapName}/ (recommended)",
    )
    ap.add_argument(
        "--prefer-named-component",
        default="Building",
        help="Prefer this component name when it has world-ish relatives (default: Building)",
    )
    ap.add_argument(
        "--filter-list",
        type=Path,
        default=None,
        help="Optional package list; if set, only emit matching asset_path rows to buildings CSV filter path",
    )
    ap.add_argument(
        "--include-heightmap-cells",
        action="store_true",
        help="Also stream >200MB cells (still skips HeightMap arrays)",
    )
    ap.add_argument(
        "--all-json",
        action="store_true",
        help="Also stream non-Generated / non-persistent JSONs under the map folder",
    )
    args = ap.parse_args(argv)

    map_dir: Path = args.map_dir
    if not map_dir.is_dir():
        print(f"ERROR: map dir not found: {map_dir}", file=sys.stderr)
        return 1

    map_name = map_dir.name
    out_dir = resolve_out_dir(map_dir, args.out_dir, getattr(args, "workspace", None))
    lists = load_filter_lists(map_dir / "_PropHarvest")
    if not lists:
        lists = load_filter_lists(out_dir)

    filter_pkgs = load_optional_filter_file(args.filter_list)

    to_process = iter_map_json_files(
        map_dir,
        include_heightmap=args.include_heightmap_cells,
        all_json=args.all_json,
    )

    print(f"Map: {map_name}")
    print(
        f"JSON files to stream: {len(to_process)} "
        f"(Generated + *_P.json; heightmap-sized skipped unless --include-heightmap-cells)"
    )
    print(f"Output: {out_dir}")

    actors: dict[str, ActorRec] = {}
    components: dict[tuple[str, str], ComponentRec] = {}
    t0 = time.time()
    total_objs = 0
    for i, path in enumerate(to_process, 1):
        size_mb = path.stat().st_size / (1024 * 1024)
        print(f"  [{i}/{len(to_process)}] {path.name} ({size_mb:.1f} MB)...")
        n = ingest_file(path, actors, components)
        total_objs += n
        print(f"      objects={n:,}  actors={len(actors):,}  components={len(components):,}")

    rows = build_rows(
        actors,
        components,
        lists,
        prefer_named=args.prefer_named_component or "",
        filter_pkgs=None,  # full placements.csv is unfiltered
        map_dir=map_dir,
    )
    buildings = [r for r in rows if is_building_row(r, lists)]
    if filter_pkgs is not None:
        buildings = [
            r
            for r in buildings
            if (r["asset_path"] in filter_pkgs)
            or any(
                (r["asset_path"] or "").rsplit("/", 1)[-1] == p.rsplit("/", 1)[-1]
                for p in filter_pkgs
            )
        ]

    placements_path = out_dir / "placements.csv"
    buildings_path = out_dir / "placements_buildings.csv"
    summary_path = out_dir / "placements_summary.json"

    write_csv(placements_path, rows)
    write_csv(buildings_path, buildings)
    summary = summarize(rows, buildings, map_name)
    summary["elapsed_sec"] = round(time.time() - t0, 2)
    summary["files_processed"] = [p.name for p in to_process]
    summary["objects_streamed"] = total_objs
    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print(f"\nWrote {placements_path} ({len(rows)} rows)")
    print(f"Wrote {buildings_path} ({len(buildings)} rows)")
    print(f"Wrote {summary_path}")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
