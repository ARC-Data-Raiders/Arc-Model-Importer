"""Native UEModel reader for FModel-Vibe AttributeFormatRestructure (v10).

Built into DataRaiders — no external UEFormat / BlenderUmap2 add-on required.
Matches CUE4Parse ``UEModel`` / ``FDataAttributeSet`` as written by FModel-Vibe
``EUEFormatVersion.AttributeFormatRestructure``.
"""

from __future__ import annotations

import gzip
import io
import json
import hashlib
import os
import struct
from dataclasses import dataclass, field

import bpy
import numpy as np

MAGIC = b"UEFORMAT"
MODEL_IDENTIFIER = "UEMODEL"
ATTR_VERSION = 10  # AttributeFormatRestructure
PRESERVE_TRANSFORMS = 8

_U32 = struct.Struct("<i")


class UeModelError(RuntimeError):
    pass


def _read_exact(buf: io.BytesIO, n: int) -> bytes:
    data = buf.read(n)
    if len(data) != n:
        raise UeModelError(f"truncated UEModel (wanted {n} bytes, got {len(data)})")
    return data


def _read_i32(buf: io.BytesIO) -> int:
    return _U32.unpack(_read_exact(buf, 4))[0]


def _read_bool(buf: io.BytesIO) -> bool:
    return _read_exact(buf, 1) != b"\x00"


def _read_fstring(buf: io.BytesIO) -> str:
    size = _read_i32(buf)
    if size <= 0:
        return ""
    raw = _read_exact(buf, size)
    return raw.rstrip(b"\x00").decode("utf-8", errors="replace")


def peek_uemodel_version(path: str) -> int | None:
    """Return the UEFormat version byte, or None if the file is not a UEModel."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8 + 4 + 64)
    except OSError:
        return None
    if not head.startswith(MAGIC):
        return None
    buf = io.BytesIO(head[8:])
    try:
        ident = _read_fstring(buf)
        if ident != MODEL_IDENTIFIER:
            return None
        return _read_exact(buf, 1)[0]
    except (UeModelError, struct.error, IndexError):
        return None


def _read_attribute_set(buf: io.BytesIO) -> list[tuple[str, bytes]]:
    count = _read_i32(buf)
    if count < 0 or count > 10_000:
        raise UeModelError(f"implausible attribute count {count}")
    out: list[tuple[str, bytes]] = []
    for _ in range(count):
        name = _read_fstring(buf)
        length = _read_i32(buf)
        if length < 0 or length > 512 * 1024 * 1024:
            raise UeModelError(f"implausible attribute size {length} for {name}")
        payload = _read_exact(buf, length) if length else b""
        out.append((name, payload))
    return out


def _read_array_header(buf: io.BytesIO) -> int:
    count = _read_i32(buf)
    if count < 0:
        raise UeModelError(f"negative array count {count}")
    return count


@dataclass
class UeMorphTarget:
    """Sparse authored morph deltas in the imported mesh's coordinate space."""

    name: str
    indices: np.ndarray
    position_deltas: np.ndarray
    normal_deltas: np.ndarray | None = None


@dataclass
class _Lod:
    name: str
    vertices: np.ndarray
    indices: np.ndarray
    uvs: list[np.ndarray] = field(default_factory=list)
    materials: list[tuple[str, str, int, int]] = field(default_factory=list)
    normals: np.ndarray | None = None
    morph_targets: list[UeMorphTarget] = field(default_factory=list)
    vertex_colors: list[tuple[str, np.ndarray]] = field(default_factory=list)
    weights: np.ndarray | None = None  # structured: bone i16, vertex i32, weight f32


@dataclass
class UeBone:
    """Rest bone from a UEModel SKELETON block. Translation is already scaled."""

    name: str
    parent: int
    translation: tuple[float, float, float]
    rotation: tuple[float, float, float, float]  # xyzw
    scale: tuple[float, float, float]


def _parse_vertices(payload: bytes, *, scale: float, flip_y: bool) -> np.ndarray:
    buf = io.BytesIO(payload)
    count = _read_array_header(buf)
    raw = np.frombuffer(_read_exact(buf, count * 12), dtype="<f4").reshape(count, 3).copy()
    raw *= scale
    if flip_y:
        raw[:, 1] *= -1.0
    return raw


def _parse_indices(payload: bytes) -> np.ndarray:
    buf = io.BytesIO(payload)
    count = _read_array_header(buf)
    raw = np.frombuffer(_read_exact(buf, count * 4), dtype="<i4")
    usable = (count // 3) * 3
    if usable <= 0:
        return np.zeros((0, 3), dtype=np.int32)
    return raw[:usable].reshape(-1, 3)


def _parse_texcoords(payload: bytes, *, flip_uv: bool) -> list[np.ndarray]:
    buf = io.BytesIO(payload)
    n_sets = _read_array_header(buf)
    uvs: list[np.ndarray] = []
    for _ in range(n_sets):
        _name = _read_fstring(buf)
        n = _read_array_header(buf)
        arr = np.frombuffer(_read_exact(buf, n * 8), dtype="<f4").reshape(n, 2).copy()
        if flip_uv:
            arr[:, 1] *= -1.0
            arr[:, 1] += 1.0
        uvs.append(arr)
    return uvs


def _parse_materials(payload: bytes) -> list[tuple[str, str, int, int]]:
    buf = io.BytesIO(payload)
    count = _read_array_header(buf)
    mats: list[tuple[str, str, int, int]] = []
    for _ in range(count):
        slot = _read_fstring(buf)
        path = _read_fstring(buf)
        first = _read_i32(buf)
        faces = _read_i32(buf)
        mats.append((slot, path, first, faces))
    return mats


def _parse_morph_targets(payload: bytes, *, scale: float, flip_y: bool) -> list[UeMorphTarget]:
    """UEFormat FMorphTarget: name, array of position/normal delta + source index."""
    buf = io.BytesIO(payload)
    count = _read_array_header(buf)
    record = np.dtype([("position", "<f4", (3,)), ("normal", "<f4", (3,)), ("index", "<u4")])
    morphs = []
    for _ in range(count):
        name = _read_fstring(buf)
        size = _read_array_header(buf)
        values = np.frombuffer(_read_exact(buf, size * record.itemsize), dtype=record)
        positions = values["position"].copy()
        normals = values["normal"].copy()
        if not np.isfinite(positions).all() or not np.isfinite(normals).all():
            raise UeModelError("nonfinite authored morph delta")
        positions *= scale
        if flip_y:
            positions[:, 1] *= -1.0
            normals[:, 1] *= -1.0
        morphs.append(UeMorphTarget(name, values["index"].copy(), positions, normals))
    return morphs


def _parse_vertex_colors(payload: bytes, vertex_count: int) -> list[tuple[str, np.ndarray]]:
    if not payload:
        return []
    buf = io.BytesIO(payload)
    result = []
    for _ in range(_read_array_header(buf)):
        name = _read_fstring(buf)
        count = _read_array_header(buf)
        if count != vertex_count:
            raise UeModelError('vertex colour count differs from positions')
        rgba = np.frombuffer(_read_exact(buf, count * 4), dtype=np.uint8).reshape(count, 4).copy()
        result.append((name, rgba))
    if buf.read(1):
        raise UeModelError('unexpected trailing vertex colour data')
    return result


def _assign_vertex_colors(mesh, lod) -> list[str]:
    added = []
    # Existing colours may contain deliberate user edits; never replace them.
    for name, rgba in lod.vertex_colors:
        if name in mesh.color_attributes:
            continue
        if len(rgba) != len(mesh.vertices):
            raise UeModelError('vertex colour topology differs from destination')
        attr = mesh.color_attributes.new(name=name, type='FLOAT_COLOR', domain='POINT')
        attr.data.foreach_set('color', (rgba.astype(np.float32) / 255.0).ravel())
        added.append(name)
    return added


def ensure_uemodel_vertex_colors(obj, path: str) -> list[str]:
    """Hydrate exact native vertex masks; no nearest-point transfer or fake layers."""
    if obj is None or obj.type != 'MESH' or obj.data.color_attributes.get('COL0'):
        return []
    _name, _version, lods = parse_uemodel(path, scale=float(obj.data.get('arc_uemodel_scale', .01)))
    lod_name = str(obj.data.get('arc_uemodel_lod') or 'LOD0')
    lod = next((l for l in lods if l.name == lod_name),None)
    if lod is None or len(lod.vertices) != len(obj.data.vertices) or len(lod.indices) != len(obj.data.polygons):
        raise UeModelError('native vertex colour topology differs')
    if any(tuple(p.vertices) != tuple(face) for p,face in zip(obj.data.polygons,lod.indices)):
        raise UeModelError('native vertex colour index order differs')
    return _assign_vertex_colors(obj.data,lod)


def _parse_lods_attribute(payload: bytes, *, scale: float, flip_y: bool) -> list[_Lod]:
    buf = io.BytesIO(payload)
    count = _read_array_header(buf)
    lods: list[_Lod] = []
    for _ in range(count):
        name = _read_fstring(buf)
        attrs = dict(_read_attribute_set(buf))
        verts = _parse_vertices(attrs.get("VERTICES") or b"", scale=scale, flip_y=flip_y)
        indices = _parse_indices(attrs.get("INDICES") or b"")
        uvs = (
            _parse_texcoords(attrs.get("TEXCOORDS") or b"", flip_uv=flip_y)
            if attrs.get("TEXCOORDS")
            else []
        )
        mats = _parse_materials(attrs.get("MATERIALS") or b"") if attrs.get("MATERIALS") else []
        normals = None
        if attrs.get("NORMALS"):
            normal_buf = io.BytesIO(attrs["NORMALS"])
            count = _read_array_header(normal_buf)
            normals = np.frombuffer(_read_exact(normal_buf, count * 16), dtype="<f4").reshape(count, 4)[:, 1:].copy()
            if flip_y:
                normals[:, 1] *= -1.0
        lods.append(
            _Lod(
                name=name or f"LOD{len(lods)}",
                vertices=verts,
                indices=indices,
                uvs=uvs,
                materials=mats,
                normals=normals,
                morph_targets=_parse_morph_targets(attrs["MORPHTARGETS"], scale=scale, flip_y=flip_y)
                if attrs.get("MORPHTARGETS") else [],
                vertex_colors=_parse_vertex_colors(attrs.get("VERTEXCOLORS") or b"", len(verts)),
                weights=_parse_weights(attrs.get("WEIGHTS") or b"", len(verts)),
            )
        )
    return lods


_WEIGHT_DTYPE = np.dtype([("bone", "<i2"), ("vertex", "<i4"), ("weight", "<f4")])


def _parse_weights(payload: bytes, vertex_count: int) -> np.ndarray | None:
    """WEIGHTS array: (bone index i16, vertex index i32, weight f32), 10 bytes each."""
    if not payload:
        return None
    buf = io.BytesIO(payload)
    count = _read_array_header(buf)
    raw = _read_exact(buf, count * _WEIGHT_DTYPE.itemsize)
    weights = np.frombuffer(raw, dtype=_WEIGHT_DTYPE).copy()
    if len(weights) and (int(weights["vertex"].max()) >= vertex_count or int(weights["vertex"].min()) < 0):
        raise UeModelError("weight vertex index outside mesh")
    return weights


def _decompress_body(buf: io.BytesIO, *, compressed: bool) -> bytes:
    if not compressed:
        return buf.read()
    codec = _read_fstring(buf)
    _uncompressed = _read_i32(buf)
    _compressed = _read_i32(buf)
    blob = buf.read()
    if codec.upper() == "GZIP":
        return gzip.decompress(blob)
    raise UeModelError(f"unsupported UEModel compression {codec!r}")


def _parse_lods_v9(payload: bytes, *, scale: float, flip_y: bool) -> list[_Lod]:
    """UEFormat v9 LODS: count, then per LOD (size, name, named arrays).

    Each array is ``name, count, byte_length, data`` (not v10 FDataAttributeSet).
    """
    buf = io.BytesIO(payload)
    lod_count = _read_array_header(buf)
    lods: list[_Lod] = []
    for _ in range(lod_count):
        _lod_size = _read_i32(buf)
        lod_name = _read_fstring(buf)
        # v9 stores a second inner byte-length after the LOD name.
        try:
            _inner_size = _read_i32(buf)
        except (UeModelError, struct.error):
            _inner_size = 0
        verts = np.zeros((0, 3), dtype=np.float32)
        indices = np.zeros((0, 3), dtype=np.int32)
        uvs: list[np.ndarray] = []
        mats: list[tuple[str, str, int, int]] = []
        morphs: list[UeMorphTarget] = []
        while buf.tell() < len(payload) - 8:
            peek = buf.tell()
            try:
                attr = _read_fstring(buf)
            except (UeModelError, struct.error):
                break
            if not attr or attr == "COLLISION":
                break
            if attr == "VERTICES":
                count = _read_i32(buf)
                nbytes = _read_i32(buf)
                raw = np.frombuffer(_read_exact(buf, nbytes), dtype="<f4").reshape(count, 3).copy()
                raw *= scale
                if flip_y:
                    raw[:, 1] *= -1.0
                verts = raw
            elif attr == "INDICES":
                count = _read_i32(buf)
                nbytes = _read_i32(buf)
                raw = np.frombuffer(_read_exact(buf, nbytes), dtype="<i4")
                usable = (min(count, len(raw)) // 3) * 3
                indices = raw[:usable].reshape(-1, 3).copy() if usable else indices
            elif attr == "TEXCOORDS":
                n_sets = _read_i32(buf)
                _tex_bytes = _read_i32(buf)
                set_start = buf.tell()
                for _s in range(max(n_sets, 1)):
                    n = _read_i32(buf)
                    arr = np.frombuffer(_read_exact(buf, n * 8), dtype="<f4").reshape(n, 2).copy()
                    if flip_y:
                        arr[:, 1] *= -1.0
                        arr[:, 1] += 1.0
                    uvs.append(arr)
                    if buf.tell() - set_start >= _tex_bytes:
                        break
            elif attr == "MATERIALS":
                count = _read_i32(buf)
                _nbytes = _read_i32(buf)
                for _m in range(count):
                    slot = _read_fstring(buf)
                    path = _read_fstring(buf)
                    first = _read_i32(buf)
                    faces = _read_i32(buf)
                    mats.append((slot, path, first, faces))
            elif attr == "MORPHTARGETS":
                count = _read_i32(buf)
                nbytes = _read_i32(buf)
                morphs = _parse_morph_targets(
                    _U32.pack(count) + _read_exact(buf, nbytes), scale=scale, flip_y=flip_y,
                )
            elif attr in ("NORMALS", "TANGENTS", "VERTEXCOLORS", "WEIGHTS"):
                count = _read_i32(buf)
                nbytes = _read_i32(buf)
                _read_exact(buf, nbytes)
            else:
                buf.seek(peek)
                break
        lods.append(
            _Lod(
                name=lod_name or f"LOD{len(lods)}",
                vertices=verts,
                indices=indices,
                uvs=uvs,
                materials=mats,
                morph_targets=morphs,
            )
        )
    return lods


def parse_uemodel(path: str, *, scale: float = 0.01) -> tuple[str, int, list[_Lod]]:
    with open(path, "rb") as fh:
        data = fh.read()
    buf = io.BytesIO(data)
    magic = _read_exact(buf, len(MAGIC))
    if magic != MAGIC:
        raise UeModelError(f"not a UEFORMAT file: {path}")
    ident = _read_fstring(buf)
    if ident != MODEL_IDENTIFIER:
        raise UeModelError(f"not a UEMODEL ({ident}): {path}")
    version = _read_exact(buf, 1)[0]
    name = _read_fstring(buf)
    # ObjectPath was added with AttributeFormatRestructure (v10). v9 spline
    # bakes are: name, compressed flag, then a LODS chunk (no attribute-set wrap).
    if version >= ATTR_VERSION:
        _object_path = _read_fstring(buf)
    compressed = _read_bool(buf)
    body = _decompress_body(buf, compressed=compressed)
    flip_y = version >= PRESERVE_TRANSFORMS
    lods: list[_Lod] = []
    if version >= ATTR_VERSION:
        root = _read_attribute_set(io.BytesIO(body))
        for attr_name, payload in root:
            if attr_name == "LODS":
                lods = _parse_lods_attribute(payload, scale=scale, flip_y=flip_y)
                break
    else:
        chunk_buf = io.BytesIO(body)
        chunk = _read_fstring(chunk_buf)
        if chunk == "LODS":
            lods = _parse_lods_v9(chunk_buf.read(), scale=scale, flip_y=flip_y)
    if not lods:
        raise UeModelError(f"UEModel has no LODS: {path}")
    return name or os.path.splitext(os.path.basename(path))[0], version, lods


def _read_uemodel_root(path: str) -> tuple[str, int, bool, list[tuple[str, bytes]]]:
    """Return name, version, flip_y, and the root attribute list."""
    with open(path, "rb") as fh:
        data = fh.read()
    buf = io.BytesIO(data)
    magic = _read_exact(buf, len(MAGIC))
    if magic != MAGIC:
        raise UeModelError(f"not a UEFORMAT file: {path}")
    ident = _read_fstring(buf)
    if ident != MODEL_IDENTIFIER:
        raise UeModelError(f"not a UEMODEL ({ident}): {path}")
    version = _read_exact(buf, 1)[0]
    name = _read_fstring(buf)
    if version >= ATTR_VERSION:
        _read_fstring(buf)
    compressed = _read_bool(buf)
    body = _decompress_body(buf, compressed=compressed)
    if version < ATTR_VERSION:
        return name, version, version >= PRESERVE_TRANSFORMS, []
    return name, version, version >= PRESERVE_TRANSFORMS, _read_attribute_set(io.BytesIO(body))


def _parse_bones(payload: bytes, *, scale: float, flip_y: bool) -> list[UeBone]:
    """BONES array: name, parent, translation, quaternion xyzw, scale."""
    buf = io.BytesIO(payload)
    count = _read_array_header(buf)
    bones: list[UeBone] = []
    for _ in range(count):
        name = _read_fstring(buf)
        parent = _read_i32(buf)
        raw = np.frombuffer(_read_exact(buf, 12), dtype="<f4").astype(np.float64)
        tx, ty, tz = float(raw[0]) * scale, float(raw[1]) * scale, float(raw[2]) * scale
        quat = np.frombuffer(_read_exact(buf, 16), dtype="<f4").astype(np.float64)
        qx, qy, qz, qw = (float(quat[0]), float(quat[1]), float(quat[2]), float(quat[3]))
        scl = np.frombuffer(_read_exact(buf, 12), dtype="<f4").astype(np.float64)
        sx, sy, sz = float(scl[0]), float(scl[1]), float(scl[2])
        # The mesh reflects Unreal Y. Apply the same basis change to the full
        # local transform: H @ M @ H, H = diag(1, -1, 1). For a quaternion this
        # negates X/Z, not W or all components (which would invert the rotation).
        # Leaving rotation unconverted puts the frame and its sockets on
        # opposite sides of the wearer and changes the accumulated rest pose.
        if flip_y:
            ty = -ty
            qx, qz = -qx, -qz
        if not name:
            name = f"bone_{len(bones)}"
        bones.append(
            UeBone(
                name=name,
                parent=parent,
                translation=(tx, ty, tz),
                rotation=(qx, qy, qz, qw),
                scale=(sx or 1.0, sy or 1.0, sz or 1.0),
            )
        )
    return bones


def parse_uemodel_bones(path: str, *, scale: float = 0.01) -> list[UeBone]:
    """Rest pose bones, in the same axis space as imported vertices."""
    _name, _version, flip_y, attrs = _read_uemodel_root(path)
    skel = dict(attrs).get("SKELETON")
    if not skel:
        return []
    block = dict(_read_attribute_set(io.BytesIO(skel)))
    payload = block.get("BONES")
    if not payload:
        return []
    return _parse_bones(payload, scale=scale, flip_y=flip_y)


def armature_from_uemodel(path: str, *, scale: float = 0.01):
    """Build a hidden-ready armature whose bone axes match the mesh vertex space.

    Static attachments are authored at the socket origin. Parenting them to these
    bones with a zero local transform puts them on the strap slots.
    Returns the armature object, or None when the file has no skeleton.
    """
    from mathutils import Matrix, Quaternion, Vector

    bones = parse_uemodel_bones(path, scale=scale)
    if not bones:
        return None
    name = os.path.splitext(os.path.basename(path))[0] + "_Skeleton"
    arm_data = bpy.data.armatures.new(name)
    arm_data.display_type = "STICK"
    arm_obj = bpy.data.objects.new(name, arm_data)
    collection = bpy.context.collection
    try:
        collection.objects.link(arm_obj)
    except Exception:
        bpy.context.scene.collection.objects.link(arm_obj)
    prev_active = bpy.context.view_layer.objects.active
    prev_mode = getattr(prev_active, "mode", "OBJECT") if prev_active else "OBJECT"
    try:
        if prev_active is not None and prev_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    try:
        bpy.ops.object.select_all(action="DESELECT")
    except Exception:
        pass
    arm_obj.select_set(True)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    edit = arm_data.edit_bones
    world: dict[int, Matrix] = {}
    created: dict[int, object] = {}
    for index, bone in enumerate(bones):
        local = Matrix.LocRotScale(
            Vector(bone.translation),
            Quaternion((bone.rotation[3], bone.rotation[0], bone.rotation[1], bone.rotation[2])),
            Vector(bone.scale),
        )
        parent_index = bone.parent
        if parent_index >= 0 and parent_index in world:
            matrix = world[parent_index] @ local
        else:
            matrix = local
            parent_index = -1
        world[index] = matrix
        bone_name = bone.name
        if edit.get(bone_name) is not None:
            bone_name = f"{bone.name}.{index}"
        edit_bone = edit.new(bone_name)
        edit_bone.head = (0.0, 0.0, 0.0)
        edit_bone.tail = (0.0, max(0.01, 4.0 * scale), 0.0)
        if parent_index >= 0 and parent_index in created:
            edit_bone.parent = created[parent_index]
            edit_bone.use_connect = False
        try:
            edit_bone.matrix = matrix
        except Exception:
            edit_bone.head = matrix.to_translation()
            edit_bone.tail = edit_bone.head + Vector((0.0, max(0.01, 4.0 * scale), 0.0))
        created[index] = edit_bone
    bpy.ops.object.mode_set(mode="OBJECT")
    try:
        if prev_active is not None and prev_active.name in bpy.data.objects:
            bpy.context.view_layer.objects.active = prev_active
    except Exception:
        pass
    return arm_obj


def uemodel_bone_names(bones: list[UeBone]) -> list[str]:
    """Armature bone name per skeleton index (same duplicate rule as armature_from_uemodel)."""
    names: list[str] = []
    seen: set[str] = set()
    for index, bone in enumerate(bones):
        name = bone.name if bone.name not in seen else f"{bone.name}.{index}"
        seen.add(name)
        names.append(name)
    return names


def _attach_to_armature(obj, arm_obj) -> None:
    """Armature modifier + object parent; world transform kept."""
    mod = next((m for m in obj.modifiers if m.type == "ARMATURE"), None)
    if mod is None:
        mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm_obj
    mod.use_vertex_groups = True
    if obj.parent != arm_obj or obj.parent_type != "OBJECT":
        world = obj.matrix_world.copy()
        obj.parent = arm_obj
        obj.parent_type = "OBJECT"
        obj.matrix_world = world


def rigid_bind_to_bone(obj, arm_obj, bone_name: str) -> None:
    """Rigid part: one group named after the bone, weight 1.0 on every vertex."""
    group = obj.vertex_groups.get(bone_name) or obj.vertex_groups.new(name=bone_name)
    group.add(range(len(obj.data.vertices)), 1.0, "REPLACE")
    obj["arc_rigid_bone"] = bone_name
    _attach_to_armature(obj, arm_obj)


def skin_mesh_to_armature(obj, arm_obj, weights, bone_names: list[str]) -> int:
    """One vertex group per weighted bone from native WEIGHTS; rigid root bind when none."""
    groups = 0
    if weights is not None and len(weights):
        valid = weights[(weights["bone"] >= 0) & (weights["bone"] < len(bone_names)) & (weights["weight"] > 0.0)]
        for bone in np.unique(valid["bone"]):
            rows = valid[valid["bone"] == bone]
            group = obj.vertex_groups.new(name=bone_names[int(bone)])
            for value in np.unique(rows["weight"]):
                group.add(rows["vertex"][rows["weight"] == value].tolist(), float(value), "REPLACE")
            groups += 1
    if groups == 0:
        if not bone_names:
            return 0
        rigid_bind_to_bone(obj, arm_obj, bone_names[0])
        return 1
    if groups == 1:
        obj["arc_rigid_bone"] = obj.vertex_groups[0].name
    _attach_to_armature(obj, arm_obj)
    return groups


def import_uemodel_skinned(path: str, *, scale: float = 0.01) -> list:
    """Native v10 mesh + skeleton; meshes skinned from WEIGHTS. Returns [armature, *meshes]."""
    meshes = import_uemodel_v10(path, scale=scale)
    if not meshes:
        return []
    arm_obj = armature_from_uemodel(path, scale=scale)
    if arm_obj is None:
        return meshes
    names = uemodel_bone_names(parse_uemodel_bones(path, scale=scale))
    _name, _version, lods = parse_uemodel(path, scale=scale)
    by_name = {lod.name: lod for lod in lods}
    for obj in meshes:
        lod = by_name.get(str(obj.data.get("arc_uemodel_lod") or ""))
        weights = lod.weights if lod is not None and len(lod.vertices) == len(obj.data.vertices) else None
        skin_mesh_to_armature(obj, arm_obj, weights, names)
    return [arm_obj, *meshes]


def _assign_uvs(mesh: bpy.types.Mesh, lod: _Lod) -> None:
    n_verts = len(lod.vertices)
    for i, uvs in enumerate(lod.uvs):
        if len(uvs) != n_verts:
            continue
        layer_name = "UV0" if i == 0 else f"UV{i}"
        uv_layer = mesh.uv_layers.new(name=layer_name) if not mesh.uv_layers.get(layer_name) else mesh.uv_layers[layer_name]
        loops = mesh.loops
        if not loops:
            continue
        data = uv_layer.data
        # Per-loop UV from vertex index.
        coords = uvs
        for li, loop in enumerate(loops):
            vi = loop.vertex_index
            if 0 <= vi < len(coords):
                data[li].uv = (float(coords[vi, 0]), float(coords[vi, 1]))


def apply_uemodel_section_slots(mesh, path: str) -> int:
    """Write polygon material_index from UEModel section ranges.

    ``first_index`` is an index into the triangle index buffer. Returns the
    number of faces assigned to a non-zero section. No-op when the file has
    one section or the face counts do not match the mesh.
    """
    if mesh is None or not getattr(mesh, "polygons", None) or not path:
        return 0
    try:
        _name, _ver, lods = parse_uemodel(path, scale=1.0)
    except Exception:
        return 0
    if not lods or not lods[0].materials or len(lods[0].materials) < 2:
        return 0
    ranges = []
    for _slot, _asset, first, faces in lods[0].materials:
        start = max(0, int(first) // 3)
        count = max(0, int(faces))
        ranges.append((start, count))
    expected = sum(count for _start, count in ranges)
    if expected != len(mesh.polygons):
        return 0
    poly_mat = [0] * len(mesh.polygons)
    for mat_i, (start, count) in enumerate(ranges):
        end = min(len(poly_mat), start + count)
        for pi in range(start, end):
            poly_mat[pi] = mat_i
    mesh.polygons.foreach_set("material_index", poly_mat)
    try:
        mesh.update()
    except Exception:
        pass
    return sum(1 for idx in poly_mat if idx)


def _assign_materials(obj: bpy.types.Object, lod: _Lod) -> None:
    mesh = obj.data
    if not lod.materials:
        return
    # One Blender slot per mesh section. The same MI is often reused
    # (SM_Frigate_Merged: 3x MI_Blockout_Enemy_Armor). Skipping a name that is
    # already on the mesh drops the later slots and leaves those faces unassigned.
    for slot, _path, _first, _faces in lod.materials:
        mat = bpy.data.materials.get(slot) or bpy.data.materials.new(slot)
        mesh.materials.append(mat)
    if not lod.materials or not mesh.polygons:
        return
    # first_index is an index into the triangle list (NumFaces triangles).
    poly_mat = [0] * len(mesh.polygons)
    for mat_i, (_slot, _path, first, faces) in enumerate(lod.materials):
        start = max(0, int(first) // 3)
        end = min(len(poly_mat), start + max(0, int(faces)))
        for pi in range(start, end):
            poly_mat[pi] = mat_i
    mesh.polygons.foreach_set("material_index", poly_mat)


def _preserve_lod_normal_targets(obj, lod):
    """Persist native dimensionless deltas and pristine floats with the mesh."""
    targets = [m for m in lod.morph_targets if m.normal_deltas is not None]
    if not targets:
        return
    mesh = obj.data
    if mesh.get("arc_native_normal_targets"):
        return
    from .materials.native.deformation import preserve_normal_source, NORMAL_SOURCE_VERSION
    current = mesh.attributes.get("custom_normal")
    if (lod.normals is None or len(lod.normals) != len(mesh.vertices)
            or current is None or current.domain != "CORNER"):
        obj["arc_normal_delta_note"] = "Native float normal source unavailable; reimport this mesh with the current UEModel reader"
        return
    for target in targets:
        if target.normal_deltas.shape != (len(target.indices), 3):
            raise UeModelError("morph normal layout differs from source indices")
    if not mesh.get(NORMAL_SOURCE_VERSION):
        preserve_normal_source(mesh, lod.normals.tolist(), tuple(range(len(mesh.vertices))),
                               source_vertex_count=len(mesh.vertices))
    names = {}
    for target in targets:
        name = "DR.MorphNormal." + hashlib.sha256(target.name.encode("utf-8")).hexdigest()[:20]
        if mesh.attributes.get(name):
            raise UeModelError("native normal delta attribute collision")
        values = np.zeros((len(mesh.vertices), 3), dtype=np.float32)
        np.add.at(values, target.indices, target.normal_deltas)
        attr = mesh.attributes.new(name, "FLOAT_VECTOR", "POINT")
        attr.data.foreach_set("vector", values.ravel())
        names[target.name] = name
    mesh["arc_native_normal_targets"] = json.dumps(names, sort_keys=True)
    mesh["arc_native_normal_weights"] = ""


def apply_uemodel_morph_normals(obj):
    """Apply current authored relative-key normals during explicit fit actions.

    No idle scene scan or depsgraph watcher is installed. Deltas remain saved
    as mesh attributes so reopening a blend does not require a source file.
    """
    if obj is None or obj.type != "MESH":
        return False
    mesh = obj.data
    encoded = mesh.get("arc_native_normal_targets")
    if not encoded or not mesh.shape_keys:
        return False
    if not mesh.shape_keys.use_relative:
        obj["arc_normal_delta_note"] = "Absolute shape-key timing has no authored normal mapping"
        return False
    names = json.loads(encoded)
    keys = mesh.shape_keys.key_blocks
    weights = {}
    for name in names:
        key = keys.get(name)
        if key is None:
            continue
        if key.vertex_group or key.relative_key != mesh.shape_keys.reference_key:
            obj["arc_normal_delta_note"] = "Edited key masks or relative bases require an explicit native normal mapping"
            return False
        weights[name] = 0.0 if key.mute else float(key.value)
    signature = json.dumps(weights, sort_keys=True)
    if mesh.get("arc_native_normal_weights") == signature:
        return False
    from .materials.native.deformation import MorphDelta, MorphTarget, apply_normal_deltas
    targets = []
    for name, attr_name in names.items():
        if not weights.get(name, 0.0):
            continue
        attr = mesh.attributes.get(attr_name)
        if attr is None or attr.data_type != "FLOAT_VECTOR" or attr.domain != "POINT":
            raise UeModelError("saved native normal delta attribute is unavailable")
        values = np.empty((len(mesh.vertices), 3), dtype=np.float32)
        attr.data.foreach_get("vector", values.ravel())
        indices = np.flatnonzero(np.any(values != 0.0, axis=1))
        targets.append(MorphTarget(name, tuple(MorphDelta((0.0, 0.0, 0.0),
                       tuple(float(v) for v in values[index]), int(index)) for index in indices)))
    active_weights = {name: value for name, value in weights.items() if value != 0.0}
    apply_normal_deltas(mesh, targets, active_weights)
    mesh["arc_native_normal_weights"] = signature
    if "arc_normal_delta_note" in obj:
        del obj["arc_normal_delta_note"]
    return True


def _apply_lod_shape_keys(obj, lod: _Lod) -> list[str]:
    """Add missing authored keys without changing geometry, key values or edits."""
    if not lod.morph_targets:
        return []
    if obj is None or obj.type != "MESH" or len(obj.data.vertices) != len(lod.vertices):
        raise UeModelError("morph target topology does not match the destination mesh")
    for morph in lod.morph_targets:
        if len(morph.indices) and int(morph.indices.max()) >= len(lod.vertices):
            raise UeModelError(f"morph {morph.name!r} references a vertex outside its LOD")
    keys = obj.data.shape_keys
    basis = keys.reference_key if keys else obj.shape_key_add(name="Basis", from_mix=False)
    coordinates = np.empty((len(obj.data.vertices), 3), dtype=np.float32)
    basis.data.foreach_get("co", coordinates.ravel())
    added = []
    for morph in lod.morph_targets:
        if not morph.name or (obj.data.shape_keys and morph.name in obj.data.shape_keys.key_blocks):
            continue
        coords = coordinates.copy()
        np.add.at(coords, morph.indices, morph.position_deltas)
        key = obj.shape_key_add(name=morph.name, from_mix=False)
        key.relative_key = basis
        key.data.foreach_set("co", coords.ravel())
        key.value = 0.0
        added.append(key.name)
    _preserve_lod_normal_targets(obj, lod)
    apply_uemodel_morph_normals(obj)
    return added


def _remap_coincident_weld_morphs(obj, lod: _Lod, coordinates: np.ndarray, scale: float) -> _Lod:
    """Recover an old seam-welded mesh only when source geometry/deltas agree."""
    from dataclasses import replace
    from mathutils.kdtree import KDTree

    if not len(coordinates) or len(coordinates) >= len(lod.vertices):
        raise UeModelError("destination is not a coincident-welded source mesh")
    tolerance = max(abs(scale) * 0.0001, 1e-7)
    offset = coordinates.min(axis=0) - lod.vertices.min(axis=0)
    source = lod.vertices + offset
    tree = KDTree(len(coordinates))
    for index, point in enumerate(coordinates):
        tree.insert(point, index)
    tree.balance()
    mapping = np.empty(len(source), dtype=np.int32)
    representatives = np.full(len(coordinates), -1, dtype=np.int32)
    for index, point in enumerate(source):
        _co, target, distance = tree.find(point)
        if distance > tolerance:
            raise UeModelError("legacy weld changed source positions; morph hydration skipped")
        mapping[index] = target
        representatives[target] = index
    if np.any(representatives < 0):
        raise UeModelError("legacy welded mesh contains unmatched vertices")

    def triangle_key(face):
        a, b, c = (int(index) for index in face)
        return min((a, b, c), (b, c, a), (c, a, b))

    source_faces = {triangle_key(mapping[face]) for face in lod.indices
                    if len(set(mapping[face])) == 3}
    if any(len(face.vertices) != 3 for face in obj.data.polygons):
        raise UeModelError("legacy welded topology is not the authored triangle mesh")
    target_faces = {triangle_key(face.vertices) for face in obj.data.polygons}
    if source_faces != target_faces:
        raise UeModelError("legacy welded mesh faces differ from the source UEModel")
    remapped = []
    for morph in lod.morph_targets:
        if len(morph.indices) and int(morph.indices.max()) >= len(source):
            raise UeModelError(f"morph {morph.name!r} references a vertex outside its LOD")
        deltas = np.zeros_like(lod.vertices)
        np.add.at(deltas, morph.indices, morph.position_deltas)
        target_deltas = deltas[representatives]
        conflicts = np.linalg.norm(deltas - target_deltas[mapping], axis=1)
        if len(conflicts) and float(conflicts.max()) > tolerance:
            raise UeModelError(f"coincident source vertices disagree on morph {morph.name!r}")
        indices = np.flatnonzero(np.any(target_deltas != 0.0, axis=1))
        remapped.append(UeMorphTarget(morph.name, indices, target_deltas[indices]))
    return replace(lod, vertices=coordinates, morph_targets=remapped)


def ensure_uemodel_shape_keys(obj, path: str, *, scale: float | None = None,
                               allow_coincident_welds: bool = False) -> list[str]:
    """Hydrate missing keys on an existing unchanged UEModel mesh, preserving values.

    Exact vertex/triangle order and base geometry must match the source LOD.
    Origin recentering is allowed. An explicit legacy-weld recovery requires
    coincident source positions, matching triangle faces, and agreeing morph
    deltas for every welded vertex. Edited geometry is rejected. Existing key
    names/values remain.
    """
    if obj is None or obj.type != "MESH":
        raise UeModelError("shape keys require a mesh object")
    if scale is None:
        scale = float(obj.data.get("arc_uemodel_scale", 0.01))
    _name, _version, lods = parse_uemodel(path, scale=scale)
    name = str(obj.data.get("arc_uemodel_lod") or "LOD0")
    lod = next((candidate for candidate in lods if candidate.name == name), None)
    if lod is None:
        raise UeModelError(f"source UEModel does not contain destination LOD {name!r}")
    mesh = obj.data
    coordinates = np.empty((len(mesh.vertices), 3), dtype=np.float32)
    existing = mesh.shape_keys.reference_key.data if mesh.shape_keys else mesh.vertices
    existing.foreach_get("co", coordinates.ravel())
    if len(mesh.vertices) != len(lod.vertices):
        if not allow_coincident_welds:
            raise UeModelError("existing mesh topology differs from the source UEModel")
        lod = _remap_coincident_weld_morphs(obj, lod, coordinates, scale)
    else:
        if len(mesh.polygons) != len(lod.indices):
            raise UeModelError("existing mesh topology differs from the source UEModel")
        if any(tuple(p.vertices) != tuple(face) for p, face in zip(mesh.polygons, lod.indices)):
            raise UeModelError("existing mesh vertex order differs from the source UEModel")
        if len(coordinates):
            difference = coordinates - lod.vertices
            if float(np.abs(difference - difference[0]).max()) > max(abs(scale) * 0.001, 1e-5):
                raise UeModelError("existing mesh base geometry was edited; morph hydration skipped")
    return _apply_lod_shape_keys(obj, lod)


def import_uemodel_v10(path: str, *, scale: float = 0.01, import_lods: bool = False) -> list:
    """Import a v10 (or v7+ attribute-header) UEModel into the current collection."""
    name, _version, lods = parse_uemodel(path, scale=scale)
    usable = [lod for lod in lods if len(lod.vertices) and len(lod.indices)]
    def quality(lod):
        if lod.name.lower() == "nanite":
            return -1
        suffix = lod.name.upper().removeprefix("LOD")
        return int(suffix) if suffix.isdigit() else 1000000
    chosen = usable if import_lods else sorted(usable, key=quality)[:1]
    created: list = []
    for lod in chosen:
        if len(lod.vertices) == 0 or len(lod.indices) == 0:
            continue
        mesh_name = f"{name}_{lod.name}" if import_lods and len(chosen) > 1 else name
        mesh = bpy.data.meshes.new(mesh_name)
        verts = [tuple(map(float, v)) for v in lod.vertices]
        faces = [tuple(int(i) for i in tri) for tri in lod.indices]
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        if lod.normals is not None and len(lod.normals) == len(mesh.vertices):
            mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
            from .materials.native.deformation import write_corner_normals
            write_corner_normals(mesh, (lod.normals[loop.vertex_index] for loop in mesh.loops))
        mesh["arc_uemodel_lod"] = lod.name
        mesh["arc_uemodel_scale"] = float(scale)
        obj = bpy.data.objects.new(mesh_name, mesh)
        bpy.context.collection.objects.link(obj)
        _apply_lod_shape_keys(obj, lod)
        _assign_vertex_colors(mesh, lod)
        try:
            _assign_uvs(mesh, lod)
        except Exception as exc:
            print(f"[arc-uemodel] UV assign failed for {mesh_name}: {exc}")
        try:
            _assign_materials(obj, lod)
        except Exception as exc:
            print(f"[arc-uemodel] material assign failed for {mesh_name}: {exc}")
        created.append(obj)
    return created


def try_import_uemodel_v10(path: str, *, scale: float = 0.01, import_lods: bool = False) -> list:
    """Best-effort import; prints the reason and returns [] on failure."""
    try:
        objs = import_uemodel_v10(path, scale=scale, import_lods=import_lods)
        if not objs:
            print(f"[arc-uemodel] v10 import produced no meshes: {path}")
        return objs
    except Exception as exc:
        print(f"[arc-uemodel] v10 import failed ({path}): {exc}")
        return []

