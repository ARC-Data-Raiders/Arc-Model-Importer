"""Authored backpack assemblies and colour options from DA_OI / DA_VP assets."""
from __future__ import annotations
import os
import re
import json
from . import utils, textures, pak_extract, pak_catalog

_PREFIX = 'PioneerGame/Content/Pioneer/Items/Characters/Skins/BackpackContainer/'
_ENUM_CACHE = {}


def _package(reference):
    if isinstance(reference, dict):
        reference = reference.get('AssetPathName') or reference.get('ObjectPath') or ''
    text = str(reference or '').replace('\\', '/').strip()
    return text.split('.', 1)[0] if text else ''


def _canonical(reference):
    return pak_extract.game_path(_package(reference)).casefold()


def _index():
    import bpy
    game = pak_extract.configured_dir(bpy.context.scene)
    return pak_catalog.find_index(game, pak_extract.index_dir()) if game else None


def _item(choice):
    return str((choice or {}).get('icon_stem') or (choice or {}).get('folder') or '').strip()


def _paths(choice):
    index = _index()
    prefix = _PREFIX + _item(choice) + '/'
    if index:
        return [p for p in index['paths'] if p.casefold().startswith(prefix.casefold())]
    root = utils.get_pioneer_root() or ''
    directory = utils.find_relative_dir(root, ['Items', 'Characters', 'Skins', 'BackpackContainer', _item(choice)]) if root else ''
    if not directory:
        return []
    return [prefix + os.path.splitext(name)[0] for name in os.listdir(directory)
            if name.casefold().startswith('da_oi_') and name.casefold().endswith('.json')
            and 'persistence' not in name.casefold()]


def cached_file(package, extension='.json'):
    package = pak_extract.game_path(_package(package))
    candidates = []
    cache = pak_extract.cache_dir()
    for prefix in ('PioneerGame/Content/', 'RemappedPlugins/Content/', 'Game/'):
        canonical = 'PioneerGame/Content/'
        relative = prefix + package[len(canonical):] if package.startswith(canonical) else package
        path = os.path.join(cache, *relative.split('/')) + extension
        if os.path.isfile(pak_catalog.filesystem_path(path)):
            candidates.append(path)
    if candidates:
        return pak_catalog.filesystem_path(max(candidates, key=lambda path: os.stat(pak_catalog.filesystem_path(path)).st_mtime_ns))
    import bpy
    if pak_extract.configured_dir(bpy.context.scene):
        return ''  # A current-Pak assembly must not borrow an older exported dump.
    game = '/Game/' + package[len('PioneerGame/Content/'):] if package.startswith('PioneerGame/Content/') else package
    found = textures.find_asset_from_object_path(game, extension)
    return pak_catalog.filesystem_path(found) if found else ''


def _json(package):
    path = cached_file(package)
    if not path:
        return []
    try:
        with open(pak_catalog.filesystem_path(path), encoding='utf-8-sig') as stream:
            return utils.ue_export_entries(json.load(stream))
    except (OSError, ValueError):
        return []


def _ensure(packages):
    import bpy
    wanted = list(dict.fromkeys(pak_extract.game_path(_package(p)) for p in packages if _package(p)))
    missing = [p for p in wanted if not cached_file(p)]
    game = pak_extract.configured_dir(bpy.context.scene)
    if missing and game:
        code, log, _meshes = pak_extract.extract_paths(game, pak_extract.resolve_usmap(bpy.context.scene), missing)
        if code:
            raise RuntimeError('Backpack dependency extraction failed: ' + '\n'.join(log.splitlines()[-4:]))


def _default_option(choice, paths):
    setting = next((p for p in paths if p.rsplit('/', 1)[-1].casefold() == ('DA_OI_BackpackContainer_' + _item(choice) + '_Color').casefold()), '')
    for entry in _json(setting) if setting else []:
        query = (entry.get('Properties') or {}).get('DefaultItemQuery') or {}
        for tag in query.get('TagDictionary') or []:
            name = str(tag.get('TagName') or '')
            if name.startswith('Character.Option.'):
                return name[len('Character.Option.'):]
    tokens = [p.rsplit('_Color_', 1)[-1] for p in paths if '_Color_' in p]
    return next((token for token in ('Default', 'Basic') if token in tokens), '')


def list_colorways(choice):
    paths = _paths(choice)
    variants = []
    marker = 'DA_OI_BackpackContainer_' + _item(choice) + '_Color_'
    for path in paths:
        leaf = path.rsplit('/', 1)[-1]
        if not leaf.casefold().startswith(marker.casefold()):
            continue
        token = leaf[len(marker):]
        label = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', token).replace('_', ' ')
        variants.append({'id': token, 'label': label, 'package': path})
    return [{'id': 'BASE', 'label': 'Game Default', 'package': ''}] + sorted(variants, key=lambda row: row['label'].casefold())


def _material_slots(mesh_path):
    if mesh_path.casefold().endswith('.uemodel'):
        from . import uemodel_import
        _name, _version, lods = uemodel_import.parse_uemodel(mesh_path)
        return [row[0] for row in lods[0].materials] if lods else []
    from .materials.common import _parse_sk_material_slots
    return [row[0] for row in _parse_sk_material_slots(mesh_path)]


def plan(choice, colorway_id='BASE'):
    """Resolve every authored part/mesh/material binding, extracting missing data."""
    paths = _paths(choice)
    item = _item(choice)
    base = next((p for p in paths if p.rsplit('/', 1)[-1].casefold() == ('DA_OI_BackpackContainer_' + item).casefold()), '')
    if not base:
        return []
    setting = next((p for p in paths if p.rsplit('/', 1)[-1].casefold() == ('DA_OI_BackpackContainer_' + item + '_Color').casefold()), '')
    _ensure([base, setting] + [path for path in paths if '_Color_' in path])
    parts = []
    for entry in _json(base):
        if entry.get('Type') == 'CharacterVisualSkinOnlineItemDataAsset':
            parts.extend(_package(value) for value in (entry.get('Properties') or {}).get('Parts') or [])
    _ensure(parts)
    selected = _default_option(choice, paths) if colorway_id == 'BASE' else colorway_id
    variant = next((p for p in paths if p.rsplit('/', 1)[-1].casefold() == ('DA_OI_BackpackContainer_' + item + '_Color_' + selected).casefold()), '') if selected else ''
    if colorway_id != 'BASE' and not variant:
        raise ValueError("Backpack '" + str(choice.get('label') or item) + "' has no authored colour '" + colorway_id + "'")
    _ensure([variant])
    modifiers = [entry.get('Properties') or {} for entry in _json(variant) if entry.get('Type') == 'CustomizationVisualPartSetMaterialModifier'] if variant else []
    result = []
    material_packages = []
    for package in parts:
        entries = [entry for entry in _json(package) if entry.get('Type') == 'CustomizationVisualPartDataAsset']
        if not entries:
            raise RuntimeError('Missing authored backpack part ' + package)
        props = entries[0].get('Properties') or {}
        mesh = _package(props.get('Mesh') or props.get('StaticMesh'))
        if not mesh:
            continue
        defaults = props.get('MaterialOverrides') or []
        if isinstance(defaults, dict):
            defaults = [{'Key': key, 'Value': value} for key, value in defaults.items()]
        bindings = [{'slot_name': value.get('Key') or '', 'package': _package(value.get('Value'))} for value in defaults if isinstance(value, dict)]
        for modifier in modifiers:
            if _canonical(modifier.get('Part')) == _canonical(package):
                bindings.append({'slot': int(modifier.get('MaterialSlot') or 0), 'package': _package(modifier.get('Material'))})
        material_packages.extend(binding['package'] for binding in bindings)
        result.append({'mesh_package': mesh, 'part': package, 'bindings': bindings,
                       'socket': str(props.get('AttachToSocket') or 'slot_container'),
                       'colorway': colorway_id, 'colorway_package': variant, 'choice_id': choice['id'],
                       'slot_hints': sorted({int((value.get('Properties') or {}).get('MaterialSlot') or 0)
                                             for candidate in paths if '_Color_' in candidate for value in _json(candidate)
                                             if value.get('Type') == 'CustomizationVisualPartSetMaterialModifier'
                                             and _canonical((value.get('Properties') or {}).get('Part')) == _canonical(package)})})
    _ensure(material_packages)
    import bpy
    game = pak_extract.configured_dir(bpy.context.scene)
    missing_meshes = [part['mesh_package'] for part in result if not cached_file(part['mesh_package'], '.uemodel')
                      and not cached_file(part['mesh_package'], '.psk') and not cached_file(part['mesh_package'], '.pskx')]
    if missing_meshes and game:
        code, log, _meshes = pak_extract.extract_paths(game, pak_extract.resolve_usmap(bpy.context.scene), missing_meshes)
        if code:
            raise RuntimeError('Backpack mesh extraction failed: ' + '\n'.join(log.splitlines()[-4:]))
    for part in result:
        exts = ('.uemodel',) if game else ('.psk', '.pskx', '.uemodel')
        part['mesh'] = next((cached_file(part['mesh_package'], ext) for ext in exts if cached_file(part['mesh_package'], ext)), '')
        if not part['mesh']:
            raise RuntimeError('Missing authored backpack mesh ' + part['mesh_package'])
        slots = _material_slots(part['mesh'])
        resolved = {}
        for binding in part['bindings']:
            slot = binding.get('slot')
            if slot is None:
                slot = next((index for index, name in enumerate(slots) if name.casefold() == binding.get('slot_name', '').casefold()), None)
                if slot is None and len(slots) == 1:
                    slot = 0
                if slot is None and len(part['bindings']) >= 1 and len(part['slot_hints']) == 1:
                    slot = part['slot_hints'][0]
                if slot is None:
                    continue  # Preserve an unmatched multi-slot binding; never overwrite slot zero by guess.
            path = cached_file(binding['package'])
            if path:
                resolved[int(slot)] = path
        part['materials'] = resolved
    return result


def apply_material_bindings(obj, part):
    """Build each authored MI with existing shader logic, then target its exact slot."""
    from .operators import apply_materials_to_object
    applied = 0
    # Unmodified sections keep their own authored shader (e.g. Survival lantern).
    if part['mesh'].casefold().endswith('.uemodel'):
        from . import uemodel_import
        from .materials.dispatch import _get_or_build_shared_mi_material
        _name, _version, lods = uemodel_import.parse_uemodel(part['mesh'])
        for index, (_slot, reference, _first, _faces) in enumerate(lods[0].materials):
            if index in part.get('materials', {}):
                continue
            source = cached_file(reference)
            material = _get_or_build_shared_mi_material(os.path.splitext(os.path.basename(source))[0], source, part['mesh']) if source else None
            if material is not None:
                while len(obj.data.materials) <= index:
                    obj.data.materials.append(material)
                obj.material_slots[index].material = material
    for slot, path in sorted(part.get('materials', {}).items()):
        original = [material.material for material in obj.material_slots]
        apply_materials_to_object(obj, part['mesh'], skin_json=path, manual_skins_folder=os.path.dirname(path), skin_choice=path)
        normalized = os.path.normcase(os.path.abspath(path))
        material = next((value.material for value in obj.material_slots if value.material and
                         os.path.normcase(os.path.abspath(str(value.material.get('arc_mi_path') or value.material.get('arc_skin_json') or ''))) == normalized), None)
        if material is None:
            continue
        for index, previous in enumerate(original):
            obj.material_slots[index].material = previous
        while len(obj.data.materials) <= slot:
            obj.data.materials.append(material)
        obj.material_slots[slot].material = material
        applied += 1
    obj['arc_backpack_choice'] = part['choice_id']
    obj['arc_backpack_colorway'] = part['colorway']
    obj['arc_backpack_part'] = part['part']
    obj['arc_backpack_material_bindings'] = json.dumps({str(slot): path for slot, path in part.get('materials', {}).items()})
    return applied


def material_dependencies(choice, colorway_id='BASE'):
    """Missing exact authored MIs after the item/container folder preparation pass."""
    paths = _paths(choice)
    base = next((p for p in paths if p.rsplit('/', 1)[-1].casefold() == ('DA_OI_BackpackContainer_' + _item(choice)).casefold()), '')
    packages = []
    for entry in _json(base):
        for part in (entry.get('Properties') or {}).get('Parts') or []:
            for visual in _json(_package(part)):
                overrides = (visual.get('Properties') or {}).get('MaterialOverrides') or []
                if isinstance(overrides, dict):
                    overrides = [{'Value': value} for value in overrides.values()]
                packages.extend(_package(override.get('Value')) for override in overrides if isinstance(override, dict))
    selected = _default_option(choice, paths) if colorway_id == 'BASE' else colorway_id
    variant = next((p for p in paths if p.rsplit('/', 1)[-1].casefold() == ('DA_OI_BackpackContainer_' + _item(choice) + '_Color_' + selected).casefold()), '') if selected else ''
    for entry in _json(variant):
        if entry.get('Type') == 'CustomizationVisualPartSetMaterialModifier':
            packages.append(_package((entry.get('Properties') or {}).get('Material')))
    return list(dict.fromkeys(pak_extract.game_path(package) for package in packages if package and not cached_file(package)))


def enum_items(choice):
    import zlib
    rows = list_colorways(choice)
    key = (str((choice or {}).get('id') or ''), tuple((row['id'], row['label'], row.get('package', '')) for row in rows))
    if key not in _ENUM_CACHE:
        _ENUM_CACHE[key] = [(row['id'], row['label'], row.get('package') or 'Authored default backpack colour',
                             0 if row['id'] == 'BASE' else (zlib.crc32(row['id'].encode()) & 0x7fffffff) or 1)
                            for row in rows]
    return _ENUM_CACHE[key]
