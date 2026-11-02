"""Exact native material-table -> exported UEModel render-section binding.

No bpy dependency. A versioned sidecar is optional for legacy caches, but once
present it must match the actual model bytes, header object, names and ranges.
"""
from __future__ import annotations
import hashlib
import json
import os
import struct
from functools import lru_cache
from pathlib import Path

SCHEMA = 'dataraiders.native-material-slots'

class ResolvedMaterialBindings(dict):
    def __init__(self, *args, unused_native_bindings=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.unused_native_bindings = list(unused_native_bindings)

def _fs(path):
    """Normalize each concrete Windows path before any filesystem operation."""
    value = os.path.abspath(os.fspath(path))
    if os.name == 'nt' and not value.startswith('\\\\?\\'):
        if value.startswith('\\\\'):
            return '\\\\?\\UNC\\' + value[2:]
        return '\\\\?\\' + value
    return value


def _identity(reference):
    text = str(reference or '').replace('\\', '/').strip(" /'")
    if "'" in text:
        text = text.split("'", 1)[-1].rstrip("'")
    return text.casefold()

def _package(reference):
    text = _identity(reference).split('.')[0].strip('/')
    for prefix in ('pioneergame/content/', 'remappedplugins/content/'):
        if text.startswith(prefix):
            return 'game/' + text[len(prefix):]
    return text

def _object_identity(reference):
    text = _identity(reference)
    leaf = text.rsplit('/', 1)[-1]
    return _package(text), leaf.split('.', 1)[1] if '.' in leaf else ''

def _header_object(path):
    def string(stream):
        raw = stream.read(4)
        if len(raw) != 4:
            raise ValueError('Truncated UEModel header')
        size = struct.unpack('<i', raw)[0]
        if size < 0 or size > 1024 * 1024:
            raise ValueError('Invalid UEModel header string size')
        data = stream.read(size)
        if len(data) != size:
            raise ValueError('Truncated UEModel header string')
        return data.rstrip(b'\0').decode('utf-8')
    with open(_fs(path), 'rb') as stream:
        if stream.read(8) != b'UEFORMAT' or string(stream) != 'UEMODEL':
            raise ValueError('Native material metadata requires a UEModel')
        version = stream.read(1)
        if not version or version[0] < 10:
            raise ValueError('Native material metadata requires UEModel object-path header')
        name = string(stream)
        return name, string(stream)

def _sha256(path):
    digest = hashlib.sha256()
    with open(_fs(path), 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

@lru_cache(maxsize=256)
def _read_verified(path, model_stamp, sidecar_stamp):
    sidecar = _fs(Path(path).with_suffix('.arc-material-slots.json'))
    with open(sidecar, 'r', encoding='utf-8-sig') as stream:
        data = json.load(stream)
    if data.get('schema') != SCHEMA or data.get('version') != 1:
        raise ValueError('Unsupported native material-slot metadata schema')
    model = data.get('uemodel') or {}
    if model.get('size') != model_stamp[1] or str(model.get('sha256') or '').casefold() != _sha256(path):
        raise ValueError('Native material-slot metadata UEModel hash mismatch')
    name, object_path = _header_object(path)
    source = data.get('source') or {}
    if not object_path or _object_identity(source.get('object_path')) != _object_identity(object_path):
        raise ValueError('Native material-slot metadata source object mismatch')
    if (_package(source.get('package')) != _package(object_path)
            or _package(str(model.get('relative_path') or '').removesuffix('.uemodel')) != _package(object_path)):
        raise ValueError('Native material-slot metadata source package mismatch')
    if name.casefold() != object_path.rsplit('/', 1)[-1].split('.')[0].casefold():
        raise ValueError('UEModel name/object identity mismatch')
    slots = data.get('materials')
    if not isinstance(slots, list) or not slots or any(slot.get('index') != i for i, slot in enumerate(slots)):
        raise ValueError('Native material-slot table is not ordered and complete')
    return data

def load_native_material_layout(mesh_path, lod_name, exported_rows):
    path = Path(_fs(mesh_path))
    sidecar = Path(_fs(Path(mesh_path).with_suffix('.arc-material-slots.json')))
    if not sidecar.is_file():
        return None
    model_stat, sidecar_stat = path.stat(), sidecar.stat()
    data = _read_verified(str(path), (model_stat.st_mtime_ns, model_stat.st_size),
                          (sidecar_stat.st_mtime_ns, sidecar_stat.st_size))
    lods = [lod for lod in data.get('lods', ()) if lod.get('name') == lod_name]
    if len(lods) != 1:
        raise ValueError('Native material-slot metadata has no unique exported LOD: ' + str(lod_name))
    sections = lods[0].get('sections') or []
    slots = data['materials']
    remap = lods[0].get('lod_material_map') or []
    if not isinstance(remap, list) or any(isinstance(value, bool) or not isinstance(value, int) for value in remap):
        raise ValueError('Invalid authored LODMaterialMap metadata')
    if len(sections) != len(exported_rows):
        raise ValueError('Native material-slot metadata render-section count mismatch')
    for index, (section, row) in enumerate(zip(sections, exported_rows)):
        native_index = section.get('effective_material_index')
        if section.get('index') != index or isinstance(native_index, bool) or not isinstance(native_index, int) or not 0 <= native_index < len(slots):
            raise ValueError('Invalid native render-section material index')
        if index < len(remap) and 0 <= remap[index] < len(slots) and native_index != remap[index]:
            raise ValueError('Native material-slot metadata LODMaterialMap mismatch')
        table = slots[native_index]
        if (_identity(section.get('material')) != _identity(table.get('material'))
                or _identity(row[1]) != _identity(table.get('material'))
                or str(row[0]) != str(section.get('export_name'))
                or str(section.get('export_name')) != str(table.get('export_name'))
                or (int(row[2]), int(row[3])) != (section.get('first_index'), section.get('triangles'))):
            raise ValueError('Native material-slot metadata render-section identity/range mismatch')
    return data, lods[0]

def resolve_native_slot_records(records, layout):
    data, lod = layout
    slots = data['materials']
    selected = {}
    for record in records:
        selector = record['slot']
        if isinstance(selector, bool):
            raise ValueError('Invalid authored native material slot selector')
        if isinstance(selector, int):
            index = selector
        else:
            matches = [slot['index'] for slot in slots if any(str(name or '').casefold() == str(selector).casefold()
                       for name in (slot.get('slot_name'), slot.get('imported_slot_name')))]
            if len(matches) != 1:
                raise ValueError('Authored material key has no unique native material slot: ' + str(selector))
            index = matches[0]
        if index < 0 or index >= len(slots):
            raise ValueError('Authored material slot exceeds native material table: ' + str(index))
        previous = selected.get(index)
        if previous and _identity(previous['material']) != _identity(record['material']):
            raise ValueError('Distinct materials bind the same exact native material slot: ' + str(index))
        selected[index] = record
    result = ResolvedMaterialBindings()
    for native_index, record in selected.items():
        active = [section['index'] for section in lod['sections'] if section['effective_material_index'] == native_index]
        if not active:
            result.unused_native_bindings.append({'native_slot': native_index, 'slot_name': slots[native_index]['slot_name'],
                 'material': record['material'], 'part': record.get('part', ''), 'lod': lod['name'],
                 'reason': 'valid native material slot unused by imported LOD'})
        for index in active:
            result[str(index)] = record['material']
    return result





def translate_visual_part_bindings(bindings, layout):
    """DA_VP native selectors -> render indices; never re-map preset RMB values.

    Copy each authored binding so its original provenance object is unchanged.
    Multiple DA variants retain separate candidates; callers preserve their
    existing ambiguity policy instead of inventing a last-wins material.
    """
    rows, unused = [], []
    for binding in bindings:
        selector = binding['slot']
        if isinstance(selector, str) and selector.isdigit():
            selector = int(selector)
        elif isinstance(selector, str):
            # DA_VP MaterialOverrides names can identify material-bank roles,
            # not native MaterialSlotName (e.g. Metal for Patcher Watch).
            # Preserve the pre-sidecar exact exported-label match; never infer
            # a native index from a material/mesh name or silently assign slot 0.
            matches = [slot for slot in layout[0]['materials'] if any(
                str(name or '').casefold() == selector.casefold()
                for name in (slot.get('slot_name'), slot.get('imported_slot_name')))]
            if len(matches) != 1:
                rows.append(dict(binding))
                unused.append({'authored_key': selector,
                    'material': binding.get('material_path') or binding['path'],
                    'part': binding.get('visual_part', ''),
                    'reason': 'DA material-bank key has no unique native slot; legacy exact exported-label matching only'})
                continue
        mapped = resolve_native_slot_records([{'slot': selector,
                    'material': binding.get('material_path') or binding['path'],
                    'part': binding.get('visual_part', '')}], layout)
        unused.extend(mapped.unused_native_bindings)
        for render_index in mapped:
            row = dict(binding)
            row['render_index'] = int(render_index)
            row['native_slot'] = layout[1]['sections'][int(render_index)]['effective_material_index']
            rows.append(row)
    return rows, unused
