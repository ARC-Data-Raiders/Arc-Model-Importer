"""Unreal material inheritance, current-pak defaults, and skin candidates."""
from pathlib import Path
import json, re
from .assets import package, relative_package, ResolutionError
from ..material_runtime.core import base_property_overrides


def rgba(v, default=(1, 1, 1, 1)):
    if isinstance(v, dict):
        return tuple(float(v.get(c, default[i])) for i, c in enumerate('RGBA'))
    if isinstance(v, (list, tuple)):
        return tuple(v) + (1,) * (4 - len(v))
    return default


def empty(key=''):
    return {'package': package(key), 'parent': '', 'name': package(key).rsplit('/', 1)[-1],
            'textures': {}, 'scalars': {}, 'colors': {}, 'switches': {}, 'settings': {}}


def scoped_key(info):
    association = str(info.get('Association','GlobalParameter')).rsplit('::',1)[-1]
    scope = 'Global' if association == 'GlobalParameter' else ('Layer' if association == 'LayerParameter' else 'Blend') + str(info.get('Index',0))
    return scope + '.' + info['Name']


def cached_parameter_infos(cached, values_name, parameter_type):
    """Match typed cached values to the serializer's runtime entry layout.

    SparseVolumeTexture precedes StaticSwitch in current UE dumps (index 8),
    whereas older dumps put StaticSwitch at 7. Never zip switch values to an
    unrelated/empty entry set merely because its historical index was 7.
    """
    values = cached.get(values_name) or []
    if not values:
        return []
    runtime = cached.get('RuntimeEntries')
    if isinstance(runtime, dict):
        named = runtime.get(parameter_type)
        if isinstance(named, dict):
            entries = named.get('ParameterInfoSet') or []
            if len(entries) == len(values):
                return entries
    # EMaterialParameterType in the exporter, validated against typed value count.
    indices = {'Scalar': (0,), 'Vector': (1,), 'Texture': (3,),
               'StaticSwitch': (8, 7)}[parameter_type]
    for index in indices:
        entry = (runtime[index] if isinstance(runtime, list) and index < len(runtime)
                 else cached.get('RuntimeEntries' + (f'[{index}]' if index else ''), {}))
        entries = entry.get('ParameterInfoSet') or [] if isinstance(entry, dict) else []
        if len(entries) == len(values):
            return entries
    return []


def raw_record(data, key=''):
    import_map = []
    if isinstance(data, dict):
        import_map = (data.get('Metadata') or {}).get('ImportMap') or []
    if isinstance(data, dict) and ('Exports' in data or 'exports' in data):
        exports = data['Exports'] if isinstance(data.get('Exports'), list) else data.get('exports')
        if not isinstance(exports, list):
            exports = []
        data = next((r for r in exports if isinstance(r, dict) and 'Material' in r.get('Type', '')),
                    next((r for r in exports if isinstance(r, dict)), {}))
    if isinstance(data, list):
        data = next((r for r in data if 'Material' in r.get('Type', '')), data[0] if data else {})
    if isinstance(data, dict) and 'Parameters' in data:
        p = data['Parameters']; r = empty(key)
        r.update(textures={k: package(v) for k, v in data.get('Textures', {}).items()},
                 scalars=dict(p.get('Scalars', {})), colors={k: rgba(v) for k, v in p.get('Colors', {}).items()},
                 switches=dict(p.get('Switches', {})))
        r['settings'] = {k: p[k] for k in ['BlendMode', 'ShadingModel', 'IsNull', 'IsTranslucent'] if k in p}
        r['settings'].update(base_property_overrides(p.get('Properties', {}).get('BasePropertyOverrides')))
        if 'DitherOpacityMask' in p.get('Properties', {}):
            r['settings']['DitherOpacityMask'] = p['Properties']['DitherOpacityMask']
        r['parent'] = package(data.get('Parent') or p.get('Properties', {}).get('Parent'))
        return r
    r = empty(key or data.get('Package', ''))
    p = data.get('properties', data.get('Properties', {})) or {}
    cached = data.get('cached', data.get('CachedExpressionData', {})) or {}
    r['name'] = data.get('name', data.get('Name', r['name']))
    r['parent'] = package(p.get('Parent'))
    for parameter_type, values, target in [('Scalar', 'ScalarValues', 'scalars'),
            ('Vector', 'VectorValues', 'colors'), ('Texture', 'TextureValues', 'textures'),
            ('StaticSwitch', 'StaticSwitchValues', 'switches')]:
        entries = cached_parameter_infos(cached, values, parameter_type)
        for info, value in zip(entries, cached.get(values, [])):
            r[target][info['Name']] = rgba(value) if target == 'colors' else package(value) if target == 'textures' else value
            r[target][scoped_key(info)] = r[target][info['Name']]
    for array, target in [('TextureParameterValues', 'textures'), ('ScalarParameterValues', 'scalars'), ('VectorParameterValues', 'colors')]:
        for entry in p.get(array, []):
            name = entry.get('ParameterInfo', {}).get('Name')
            if name:
                value = entry.get('ParameterValue')
                r[target][name] = rgba(value) if target == 'colors' else package(value) if target == 'textures' else value
                r[target][scoped_key(entry['ParameterInfo'])] = r[target][name]
    for entry in p.get('StaticParametersRuntime', p.get('StaticParameters', {})).get('StaticSwitchParameters', []):
        if entry.get('bOverride', True):
            r['switches'][entry['ParameterInfo']['Name']] = bool(entry.get('Value'))
    for k in ['BlendMode', 'ShadingModel', 'TwoSided', 'bTwoSided', 'OpacityMaskClipValue', 'DitherOpacityMask']:
        if k in p:
            r['settings'][k] = p[k]
    # Enabled overrides only (the parent merge supplies the rest); bOverride_TwoSided with no TwoSided is one-sided.
    r['settings'].update(base_property_overrides(p.get('BasePropertyOverrides')))
    # Masters such as M_Flag_01 keep their maps in TextureStreamingData, not
    # TextureParameterValues. The channel suffix (CA, NOH) is the parameter.
    if not r['textures']:
        for item in p.get('TextureStreamingData') or []:
            name = str(item.get('TextureName') or '')
            code = name.rsplit('_', 1)[-1].upper()
            if not name or not re.fullmatch(r'[CDNROMAESXHGT]{1,4}', code):
                continue
            r['textures'].setdefault(code, name)
        for imp in import_map:
            if not isinstance(imp, dict):
                continue
            object_name = str(imp.get('ObjectName') or '')
            if 'Texture2D' not in object_name:
                continue
            name = object_name.split("'", 1)[-1].rstrip("'")
            code = name.rsplit('_', 1)[-1].upper()
            if not re.fullmatch(r'[CDNROMAESXHGT]{1,4}', code):
                continue
            ref = package(imp.get('ObjectPath') or name)
            r['textures'].setdefault(code, ref)
    return r


def merge(parent, child):
    result = dict(child)
    for k in ['textures', 'scalars', 'colors', 'switches', 'settings']:
        result[k] = dict(parent.get(k, {}))
        result[k].update(child.get(k, {}))
    return result


_DATA = None


def metadata():
    """Current reviewed native facts only; historical flattened banks are retired."""
    from ..material_runtime.native_data import current_metadata
    return current_metadata()


class MaterialLibrary:
    def __init__(self, index):
        self.index = index
        from .native_catalog_bridge import session
        self.native = session(index)
        self._data = None if self.native is None else {package(entry['facts']['package']).casefold(): {'package':package(entry['facts']['package'])} for entry in self.native.catalog.payload['records'].values()}
        self.memo = {}
        self.anchor_memo = {}

    @property
    def data(self):
        if self.native is None:
            manifest=getattr(self.index,'native_catalog_activation',None)
            if manifest is not None:
                from ..material_runtime.native_data import current_metadata,reviewed_source
                source=reviewed_source(manifest)
                if source is not None:return current_metadata(source)['materials']
            return metadata()['materials']
        return self._data

    def load(self, key, stack=()):
        from .native_catalog_bridge import session
        current=session(self.index)
        if current is not self.native:
            self.native=current
            self._data=None if current is None else {package(entry['facts']['package']).casefold():{'package':package(entry['facts']['package'])} for entry in current.catalog.payload['records'].values()}
        if self.native is None:
            from .native_catalog_bridge import raw_session
            owner=None if getattr(self.index,'compatibility_mode',False) else raw_session(self.index)
            record=owner.legacy_record_if_authoritative(key) if owner is not None else None
            if record is not None:
                record['family']=classify(record)
                from .head_records import augment
                return augment(record)
        if self.native is not None:
            # Reload current source association; do not reuse flattened memo values.
            record = self.native.legacy_record(key)
            record['family'] = classify(record)
            from .head_records import augment
            return augment(record)
        key = package(key)
        low = key.casefold()
        if low in stack:
            raise ResolutionError('Material inheritance cycle: ' + key)
        cached = self.data.get(low)
        if cached and cached.get('_native_render_limit'):
            from .native_catalog_bridge import NativeRenderLimitationError
            raise NativeRenderLimitationError('Current native preset/hierarchy has deferred expression bindings: '+key)
        path = self.index.resolve(key, '.json')
        if not cached and not path:
            raise ResolutionError('Material metadata missing: ' + key)
        local = raw_record(json.loads(path.read_text(encoding='utf-8-sig')), key) if path else None
        record = dict(cached) if cached else empty(key)
        if local is not None:
            cached_parent = package(record.get('parent', ''))
            local_parent = package(local.get('parent', ''))
            if local_parent and local_parent.casefold() != cached_parent.casefold():
                # A changed current parent invalidates the old child's inherited
                # snapshot too: do not carry values across unrelated masters.
                record = dict(local)
            else:
                local['parent'] = local_parent or cached_parent
                record = merge(record, local)
        # Resolve inheritance only after the current export selects its parent.
        own_sources=list(record.get('native_fact_sources',()))
        if path:
            from ..material_runtime.repository import digest
            own_sources.append({'kind':'local-fmodel-json-observation','path':str(path),'sha256':digest(path),'namespace_association':'unverified selected-root compatibility'})
        parent_record=self.load(record['parent'], stack+(low,)) if record.get('parent') else None
        if parent_record is not None:record=merge(parent_record,record)
        record['compatibility_sources']=list(parent_record.get('compatibility_sources',()))+own_sources if parent_record else own_sources
        record['source_json'] = str(path) if path else ''
        record['master'] = parent_record.get('master', record['parent']) if parent_record is not None else key
        record['family'] = classify(record)
        record['native_source_mode']='fmodel-compatibility'
        record['native_source_diagnostics']=['FModel compatibility: local JSON observations and reviewed current-native defaults; export build/namespace association is uncertain, never native exact']
        from .head_records import augment
        record=augment(record)
        self.memo[low] = record
        return record

    def slot(self, name, mesh_path=None):
        if self.native is None:
            from .native_catalog_bridge import raw_session
            owner=None if getattr(self.index,'compatibility_mode',False) else raw_session(self.index)
            if owner is not None:
                if mesh_path:
                    try: nearby=relative_package(Path(mesh_path).parent / (package(name).rsplit('/',1)[-1]+'.json'),self.index.root)
                    except ValueError: nearby=None
                    if nearby and owner.legacy_record_if_authoritative(nearby) is not None:return self.load(nearby)
                # A fully qualified slot also works when only raw sidecars exist.
                if '/' in str(name).replace('\\','/') and owner.legacy_record_if_authoritative(name) is not None:return self.load(name)
        if mesh_path:
            try:
                nearby = relative_package(Path(mesh_path).parent / (package(name).rsplit('/', 1)[-1]+'.json'), self.index.root)
                if nearby.casefold() in self.data:
                    return self.load(nearby)
            except ValueError:
                pass
        path = self.index.resolve(name, '.json', mesh_path)
        if not path:
            raise ResolutionError('No material JSON for slot ' + name)
        result=self.load(relative_package(path, self.index.root))
        note=self.index.resolution_notes.get((str(name),str(mesh_path)))
        if note:
            result=dict(result);result['resolution_note']=note
        return result

    def skins(self, preview):
        anchor = preview['textures'].get('Normal') or preview['textures'].get('BaseColor')
        if not anchor:
            return []
        candidates = []
        # Colour-only descendants can inherit their mesh anchor from a skin parent.
        raw_only=preview.get('native_source_mode')=='raw-complete'
        keys=set() if raw_only else set(self.data)
        keys.update(key for key,ext in self.index.paths if ext=='.json' and '/skins/' in key.casefold())
        for key in sorted(keys):
            if '/skins/' not in key.casefold():
                continue
            if raw_only:
                from .native_catalog_bridge import raw_session
                owner=None if getattr(self.index,'compatibility_mode',False) else raw_session(self.index)
                current=owner.legacy_record_if_authoritative(key)
                if current is None:continue
                current_anchor=current['textures'].get('Normal') or current['textures'].get('BaseColor') or ''
                if anchor.casefold()==current_anchor.casefold():candidates.append(current['package'])
            elif anchor.casefold() == self.skin_anchor(key).casefold():
                candidates.append(self.data.get(key,{}).get('package',key))
        return sorted(candidates)

    def skin_anchor(self, key, stack=()):
        if self.native is None:
            from .native_catalog_bridge import raw_session
            owner=None if getattr(self.index,'compatibility_mode',False) else raw_session(self.index)
            current=owner.legacy_record_if_authoritative(key) if owner is not None else None
            if current is not None:return current['textures'].get('Normal') or current['textures'].get('BaseColor') or ''
        if self.native is not None:
            rec = self.load(key)
            return rec['textures'].get('Normal') or rec['textures'].get('BaseColor') or ''
        key = package(key).casefold()
        if key in stack: return ''
        rec = self.data.get(key, {})
        local=self.index.resolve(key,'.json')
        if local:
            rec=merge(rec,raw_record(json.loads(local.read_text(encoding='utf-8-sig')),key))
        tex = rec.get('textures', {})
        anchor = tex.get('Normal') or tex.get('BaseColor')
        if not anchor and rec.get('parent'):
            anchor = self.skin_anchor(rec['parent'], stack + (key,))
        self.anchor_memo[key] = anchor or ''
        return anchor or ''

    def choose_skin(self, preview, filter_text=''):
        candidates = self.skins(preview)
        if filter_text:
            candidates = [p for p in candidates if filter_text.casefold() in p.casefold()]
        if not candidates:
            raise ResolutionError('No matching skin for ' + preview['name'] + (': ' + filter_text if filter_text else ''))
        # Deterministic convenience selection; recorded explicitly, never presented as an authored default.
        if not filter_text:
            plain = [p for p in candidates if p.casefold().split('/skins/')[-1].split('/')[0] in {'base', 'basic', 'default'}]
            candidates = plain or candidates
        result = dict(self.load(candidates[0]))
        result['skin_selection'] = 'explicit filter' if filter_text else 'first available skin; verify variant'
        return result


def classify(r):
    master = r.get('master', r.get('parent', '')).lower()
    t = r.get('textures', {})
    from .head_records import family
    kind=family(r)
    if kind:return kind
    if master.endswith('/characters/shared/bodies/m_skin'):return 'body_skin'
    if 'character_preview' in master:
        return 'character_preview'
    if 'character_layered' in master or ('OcclusionCurvatureMaterialID' in t and any(k.endswith('_BaseColorOverlay') for k in r['colors'])):
        return 'character'
    if 'weaponpreset' in master or 'Weapon ID' in t:
        return 'weapon'
    base = master.rsplit('/',1)[-1]
    if base == 'm_enemypreset_ox+tiling+detail':
        return 'enemy_tile'
    if base == 'm_vehiclestack':
        return 'vehicle'
    if base == 'm_fx_meshradialblur':
        return 'fx_invisible'
    if base == 'm_fx_cloud_lightvector':
        return 'fx_cloud'
    if base == 'm_backgrounddecal':
        return 'background_decal'
    if base == 'm_glassopaque_nx_01':
        return 'glass_opaque'
    if base == 'm_sanddune':
        return 'sand_dune'
    if base == 'm_fzt_snowpiles_01':
        return 'snow_pile'
    if base == 'm_fzt_snow_01':
        return 'fzt_snow'
    if base == 'm_masterlight_lightbulb_01':
        return 'lightbulb'
    if base == 'm_fx_lightbeam_master_01':
        return 'fx_lightbeam'
    if base == 'm_fx_fogsheet_master_01':
        return 'fx_fogsheet'
    if 'decalpreset' in base:
        return 'decal'
    if 'windowpane' in base:
        return 'window'
    if base == 'm_proppreset_translucent+reflection+detail':
        return 'translucent_glass'
    if base.startswith('m_flag'):
        return 'surface'
    if base in {'m_proptrimpreset_cr+nom+ao','m_enemytrimpreset_01'}:
        return 'prop_trim'
    if base == 'm_architecturepreset_trim+cr+nah':
        return 'architecture_trim'
    if master.rsplit('/',1)[-1] == 'm_trimmap_01':
        return 'trim'
    if any(k.startswith('1.') for k in t) and any(k.startswith('2.') for k in t):
        return 'environment_layers'
    if 'architecturepreset' in master:
        return 'architecture'
    if any(k in t for k in ['CR', 'NOM', 'NOH', 'NOX', 'Diffuse', 'BaseColor', 'Albedo', 'Color', 'Normal', 'Normals']):
        return 'surface'
    return 'fallback'
