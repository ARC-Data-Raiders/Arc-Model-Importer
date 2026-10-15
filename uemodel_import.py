"""Native UEModel reader for FModel-Vibe AttributeFormatRestructure (v10).

Built into DataRaiders — no external UEFormat / BlenderUmap2 add-on required.
Matches CUE4Parse ``UEModel`` / ``FDataAttributeSet`` as written by FModel-Vibe
``EUEFormatVersion.AttributeFormatRestructure``.
"""

from __future__ import annotations

import gzip
import io
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
class _Lod:
    name: str
    vertices: np.ndarray
    indices: np.ndarray
    uvs: list[np.ndarray] = field(default_factory=list)
    materials: list[tuple[str, str, int, int]] = field(default_factory=list)
    normals: np.ndarray | None = None


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
            )
        )
    return lods


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
        # Vertices negate Y. Negating the quaternion as well drops spine_03 below
        # the pelvis, so only the translation is mirrored.
        if flip_y:
            ty = -ty
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
            mesh.normals_split_custom_set_from_vertices(lod.normals.tolist())
        mesh["arc_uemodel_lod"] = lod.name
        obj = bpy.data.objects.new(mesh_name, mesh)
        bpy.context.collection.objects.link(obj)
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
