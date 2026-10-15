"""Unreal material inheritance, current-pak defaults, and skin candidates."""
from pathlib import Path
import gzip, json, re
from .assets import package, relative_package, ResolutionError


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
        r['settings'].update(p.get('Properties', {}).get('BasePropertyOverrides', {}))
        r['parent'] = package(data.get('Parent') or p.get('Properties', {}).get('Parent'))
        return r
    r = empty(key or data.get('Package', ''))
    p = data.get('properties', data.get('Properties', {})) or {}
    cached = data.get('cached', data.get('CachedExpressionData', {})) or {}
    r['name'] = data.get('name', data.get('Name', r['name']))
    r['parent'] = package(p.get('Parent'))
    # FModel serializes cached typed arrays in a fixed order.
    for i, values, target in [(0, 'ScalarValues', 'scalars'), (1, 'VectorValues', 'colors'),
                              (3, 'TextureValues', 'textures'), (7, 'StaticSwitchValues', 'switches')]:
        entries = cached.get('RuntimeEntries' + (f'[{i}]' if i else ''), {}).get('ParameterInfoSet', [])
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
    for k in ['BlendMode', 'ShadingModel', 'TwoSided', 'bTwoSided', 'OpacityMaskClipValue']:
        if k in p:
            r['settings'][k] = p[k]
    overrides = p.get('BasePropertyOverrides', {})
    for k, v in overrides.items():
        if not k.startswith('bOverride') and overrides.get('bOverride_' + k, True):
            r['settings'][k] = v
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
    global _DATA
    if _DATA is None:
        path = Path(__file__).parent / 'data' / 'materials_142.json.gz'
        _DATA = json.loads(gzip.decompress(path.read_bytes())) if path.is_file() else {'materials': {}, 'textures': {}}
        supplemental = path.parent / 'character_materials_142.json.gz'
        if supplemental.is_file():
            for key, record in json.loads(gzip.decompress(supplemental.read_bytes())).items():
                _DATA['materials'].setdefault(key, record)
        scopes = Path(__file__).parent / 'data' / 'parameter_scopes_142.json.gz'
        if scopes.is_file():
            for key, record in json.loads(gzip.decompress(scopes.read_bytes())).items():
                for category, values in record.items():
                    _DATA['materials'][key][category].update(values)
    return _DATA


class MaterialLibrary:
    def __init__(self, index):
        self.index = index
        self.data = metadata()['materials']
        self.memo = {}
        self.anchor_memo = {}

    def load(self, key, stack=()):
        key = package(key)
        low = key.casefold()
        if low in self.memo:
            return self.memo[low]
        if low in stack:
            raise ResolutionError('Material inheritance cycle: ' + key)
        cached = self.data.get(low)
        path = self.index.resolve(key, '.json')
        if not cached and not path:
            raise ResolutionError('Material metadata missing: ' + key)
        record = dict(cached) if cached else raw_record(json.loads(path.read_text(encoding='utf-8-sig')), key)
        if record.get('parent'):
            record = merge(self.load(record['parent'], stack + (low,)), record)
        # Current local exports take precedence over the packaged 1.42 snapshot.
        if path:
            local = raw_record(json.loads(path.read_text(encoding='utf-8-sig')), key)
            local['parent'] = local.get('parent') or record.get('parent', '')
            record = merge(record, local)
        record['source_json'] = str(path) if path else ''
        record['master'] = self.load(record['parent'], stack + (low,)).get('master', record['parent']) if record.get('parent') else key
        record['family'] = classify(record)
        from .head_records import augment
        record=augment(record)
        self.memo[low] = record
        return record

    def slot(self, name, mesh_path=None):
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
        keys=set(self.data)
        keys.update(key for key,ext in self.index.paths if ext=='.json' and '/skins/' in key)
        for key in sorted(keys):
            if '/skins/' not in key:
                continue
            if anchor.casefold() == self.skin_anchor(key).casefold():
                candidates.append(self.data.get(key,{}).get('package',key))
        return sorted(candidates)

    def skin_anchor(self, key, stack=()):
        key = package(key).casefold()
        if key in self.anchor_memo: return self.anchor_memo[key]
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
    if 'decalpreset' in base:
        return 'decal'
    if 'windowpane' in base:
        return 'window'
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
