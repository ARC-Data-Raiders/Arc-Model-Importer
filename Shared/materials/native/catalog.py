"""Portable current-native material facts and pre-reuse content signatures.

Catalogs store local overrides, never flattened inherited defaults. No bpy import.
"""
from dataclasses import dataclass, replace
from collections.abc import Mapping
from pathlib import Path
import hashlib, inspect, json, os, threading, copy, tempfile, gzip
from collections import OrderedDict
from types import MappingProxyType
from .core import MaterialRecord, freeze, serialize, Expr, expression_fingerprint, ignores_alpha_output
from .assets import NativeAssetRepository,root_values,native_texture_kind,native_resource_kind
from .defaults import project_uniform_defaults
from .repository import AssetRepository,parse_material,merge_records,package_ref,object_name,digest,resolved_io_path,_exports

FORMAT='native-material-catalog-v2'
MATERIAL_TYPES={'Material','MaterialInstanceConstant','MaterialInstance'}
_FACT_CACHE=OrderedDict(); _FACT_LOCK=threading.RLock(); _FACT_LIMIT=2048
def _remember(key,value):
    with _FACT_LOCK:
        _FACT_CACHE[key]=value; _FACT_CACHE.move_to_end(key)
        while len(_FACT_CACHE)>_FACT_LIMIT: _FACT_CACHE.popitem(last=False)
def _remembered(key):
    with _FACT_LOCK:
        if key not in _FACT_CACHE: return None
        _FACT_CACHE.move_to_end(key); return _FACT_CACHE[key]
class StaleCatalogError(ValueError): pass
class NativeMetadataError(ValueError): pass

_JSON_ENCODER=json.JSONEncoder(sort_keys=True,separators=(',',':'),allow_nan=False)
_DECODED_JSON_ENCODER=json.JSONEncoder(sort_keys=True,separators=(',',':'),allow_nan=False,check_circular=False)

def _json_hash(value, *, decoded_tree=False):
    encoder=_DECODED_JSON_ENCODER if decoded_tree else _JSON_ENCODER
    return hashlib.sha256(encoder.encode(value).encode()).hexdigest()

def _identity(reference,aliases=None):
    package=package_ref(reference,aliases); name=object_name(reference)
    if not package: raise ValueError('An exact material package is required')
    key=package if name.casefold()==package.rsplit('/',1)[-1].casefold() else package+'.'+name
    return package,name,key

def record_facts(record):
    return {'package':record.package,'parent':record.master,'scalars':serialize(record.scalars),'colors':serialize(record.colors),'textures':serialize(record.textures),'switches':serialize(record.switches),'settings':serialize(record.settings),
            'scopes':[{'association':association,'index':index,'parameters':serialize(parameters)} for (association,index),parameters in sorted(record.scopes.items())]}

def _from_facts(facts,provenance=()):
    scopes={(entry['association'],int(entry['index'])):entry['parameters'] for entry in facts.get('scopes',())}
    return MaterialRecord(facts['package'],facts['parent'],**{key:facts.get(key,{}) for key in ('scalars','colors','textures','switches','settings')},scopes=scopes,provenance=provenance)

_READ_LOCK=threading.Lock()
_READ_CACHE={}

def _read(path):
    # Catalog transport may be deterministic stdlib gzip. Schema/fact hashes
    # are validated after decoding; activation binds the compressed file bytes.
    raw=Path(path).read_bytes()
    if raw.startswith(b'\x1f\x8b'):raw=gzip.decompress(raw)
    return json.loads(raw.decode('utf-8-sig'))

def _material_sources(repository,reference):
    if hasattr(repository,'material_sources'): return repository.material_sources(reference)
    sources=[]
    for suffix in ('.arc-material.json','.json'):
        try: sources.append((suffix,repository.resolve(reference,suffix)))
        except FileNotFoundError: pass
    return sources

def _difference_observations(full,explicit,parent):
    values={kind:{name:value for name,value in getattr(full,kind).items() if name not in getattr(explicit,kind)} for kind in ('scalars','colors','textures','switches')}
    scopes={}
    for scope,parameters in full.scopes.items():
        for kind,entries in parameters.items():
            difference={name:value for name,value in entries.items() if name not in explicit.scopes.get(scope,{}).get(kind,{})}
            if difference: scopes.setdefault(scope,{})[kind]=difference
    observed=MaterialRecord(full.package,parent,**values,scopes=scopes)
    return record_facts(observed) if any(values.values()) or scopes else None

def _projection_event(record):
    return next((entry for entry in record.provenance if entry.get('kind')=='native-default-projection'),None)

def _native_local(repository,reference):
    package,name,key=_identity(reference,repository.mount_aliases); sources=_material_sources(repository,key)
    dependencies=[{'kind':'local-source','package':key,'path':str(path),'sha256':digest(path),'suffix':suffix,'selected':False} for suffix,path in sources]
    selectors=getattr(repository,'uniform_selectors',{})
    selector=selectors.get(key,selectors.get(package,selectors.get('*',{})))
    policy=getattr(repository,'uniform_policy','preserve-authored')
    cache_key=('material-v2',_json_hash([package,name,repository.mount_aliases,dependencies,selector,policy]))
    previous=_remembered(cache_key)
    if previous is not None: return previous[0],serialize(previous[1])
    for index,(suffix,path) in enumerate(sources):
        raw=_read(path)
        native=not (isinstance(raw,dict) and 'Data' in raw) and any(isinstance(entry,dict) and entry.get('Type') in MATERIAL_TYPES for entry in _exports(raw))
        if suffix!='.arc-material.json' and not native: continue
        record=parse_material(raw,package,strict_identity=True,expected_name=name,mount_aliases=repository.mount_aliases)
        selected=[entry for entry in _exports(raw) if entry.get('Type') in MATERIAL_TYPES and entry.get('Name','').casefold()==name.casefold() and package_ref(entry.get('Package',''),repository.mount_aliases).casefold()==package.casefold()]
        if len(selected)!=1: raise NativeMetadataError('Ambiguous exact native material export: '+key)
        material=selected[0]; properties=material.get('Properties',{})
        parent=_identity(properties['Parent'],repository.mount_aliases)[2] if properties.get('Parent') else key
        observations=None
        if material.get('Type')!='Material' and ('CachedExpressionData' in material or 'CachedExpressionData' in properties):
            explicit_material=copy.deepcopy(material); explicit_material.pop('CachedExpressionData',None); explicit_material.get('Properties',{}).pop('CachedExpressionData',None)
            explicit=parse_material({'Exports':[explicit_material]},package,strict_identity=True,expected_name=name,mount_aliases=repository.mount_aliases)
            observations=_difference_observations(record,explicit,parent); record=explicit
        record,projection=project_uniform_defaults(material,record,selector=selector,policy=policy)
        record=replace(record,master=parent)
        semantic=_json_hash({'type':material['Type'],'facts':record_facts(record),'cached_observations':observations,'uniform_policy':policy,'selector':selector})
        dependencies[index]['selected']=True; dependencies[index]['semantic_sha256']=semantic
        event={'kind':'native-default-projection','package':key,'report':projection,'cached_observations':observations,'semantic_sha256':semantic}
        result=replace(record,provenance=tuple(dependencies)+(event,))
        _remember(cache_key,(result,freeze(dependencies)))
        return result,dependencies
    _remember(cache_key,(None,freeze(dependencies)))
    return None,dependencies

def _texture_refs(record):
    for name,reference in record.textures.items():
        if reference is not None: yield name,reference,None
    for scope,parameters in record.scopes.items():
        for name,reference in parameters.get('textures',{}).items():
            if reference is not None: yield name,reference,scope

def _texture_fact(repository,reference):
    expected_name=object_name(reference);reference=package_ref(reference,repository.mount_aliases)
    try:
        if hasattr(repository,'texture_sources'):
            sources=repository.texture_sources(reference)
            if not sources: return None
        else:
            sources=[]
            for suffix in ('.arc-texture.json','.json'):
                try: sources.append((suffix,repository.resolve(reference,suffix)))
                except FileNotFoundError: pass
            if not sources: return None
        selected_suffix,path=sources[0]
    except FileNotFoundError: return None
    aliases=[{'path':str(candidate),'sha256':digest(candidate),'suffix':suffix} for suffix,candidate in sources]
    cache_key=('resource-kind-v3',str(path),digest(path),reference,expected_name,_json_hash(aliases))
    previous=_remembered(cache_key)
    if previous is not None: return serialize(previous)
    raw=_read(path)
    if isinstance(raw,dict) and 'Data' in raw:
        if selected_suffix=='.arc-texture.json': raise NativeMetadataError('Authoritative texture sidecar contains thin Data wrapper')
        return None
    candidates=[entry for entry in _exports(raw) if isinstance(entry,dict) and (native_texture_kind(entry) is not None or native_resource_kind(entry) is not None) and entry.get('Package') and package_ref(entry['Package'],repository.mount_aliases).casefold()==reference.casefold() and str(entry.get('Name','')).casefold()==expected_name.casefold()]
    if len(candidates)!=1: raise NativeMetadataError('Expected one exact texture metadata export: '+reference)
    entry=candidates[0]; properties=entry.get('Properties',{}); typ=entry['Type'];kind=native_texture_kind(entry)
    fact={'package':reference,'name':entry.get('Name'),'type':typ,'native_texture_kind':kind,'properties':{key:properties[key] for key in ('SRGB','Filter','AddressX','AddressY','CompressionSettings') if key in properties},'source':{'path':str(path),'sha256':digest(path),'aliases':aliases}}
    if native_resource_kind(entry) is not None:
        fact.pop('native_texture_kind',None);fact['resource_kind']=native_resource_kind(entry);fact['properties']=serialize(properties)
    if kind=='Texture2DArray':
        sources=properties.get('SourceTextures',entry.get('SourceTextures'))
        normalized=None if not sources else [None if value is None else package_ref(value,repository.mount_aliases) for value in sources]
        counts=[int(mip['SizeZ']) for mip in entry.get('Mips',()) if mip.get('SizeZ') is not None]
        counts.extend(int(container[key]) for container in (entry,properties) for key in ('SizeZ','ArraySize','ArrayLayerCount') if container.get(key) is not None)
        if normalized is not None: counts.append(len(normalized))
        if not counts or any(count<=0 for count in counts) or len(set(counts))!=1: raise NativeMetadataError('Unknown or inconsistent native array slice count: '+reference)
        fact['slice_count']=counts[0]; fact['source_textures']=normalized
    _remember(cache_key,freeze(fact))
    return fact

def native_mounts(native_root,extra=None):
    root=resolved_io_path(native_root); result={}
    game=[root/'PioneerGame/Content',root/'RemappedPlugins/Content']; game=[path for path in game if path.is_dir()]
    if game: result['/Game']=tuple(game)
    for mount,relative in (('/Engine','Engine/Content'),('/EmbarkScript','RemappedPlugins/EmbarkScript/Content')):
        if (root/relative).is_dir(): result[mount]=(root/relative,)
    for mount,paths in (extra or {}).items(): result[mount]=tuple(root/path if not Path(path).is_absolute() else Path(path) for path in root_values(paths))
    for mount,paths in result.items():
        resolved=tuple(resolved_io_path(path) for path in paths)
        if any(not path.is_relative_to(root) for path in resolved): raise ValueError('Catalog source mount escapes native root: '+mount)
        result[mount]=resolved
    if not result: raise ValueError('No declared native content mounts')
    return result
class _FrozenEntries(Mapping):
    def __init__(self, entries, *, folded=False, cache=None):
        self.__entries = entries
        self.__keys = {key.casefold() if folded else key: key for key in entries}
        self.__cache = {} if cache is None else cache
    def __len__(self):
        return len(self.__keys)
    def __iter__(self):
        return iter(self.__keys)
    def __getitem__(self, key):
        original = self.__keys[key]
        if original not in self.__cache:
            self.__cache[original] = freeze(self.__entries[original])
        return self.__cache[original]

class NativeCatalog:
    def __init__(self,payload, *, native_root=None,cached_policy='source-associated',_owned=False):
        if payload.get('format')!=FORMAT or not payload.get('build') or not payload.get('mapping'): raise ValueError('Invalid native catalog schema/build/mapping')
        if cached_policy not in ('source-associated','require-associated','ignore-observations'): raise ValueError('Unknown cached observation policy')
        self.cached_policy=cached_policy
        if _owned:
            record_cache = {}
            texture_cache = {}
            self.payload=MappingProxyType({key: _FrozenEntries(value, cache=record_cache if key=='records' else texture_cache) if key in ('records','textures') else freeze(value) for key,value in payload.items()})
            self._records=_FrozenEntries(payload['records'],folded=True,cache=record_cache)
            self._textures=_FrozenEntries(payload.get('textures',{}),folded=True,cache=texture_cache)
            self.__validation_payload=payload
        else:
            self.payload=freeze(payload)
            self._records={key.casefold():value for key,value in self.payload['records'].items()}
            self._textures={key.casefold():value for key,value in self.payload.get('textures',{}).items()}
            self.__validation_payload=self.payload
        self.build=payload['build']; self.mapping=payload['mapping']; self.native_root=resolved_io_path(native_root) if native_root is not None else None
        if len(self._records)!=len(self.payload['records']): raise ValueError('Duplicate case-insensitive material identities')
        for key,entry in payload['records'].items():
            if _json_hash(entry['facts'] if _owned else serialize(entry['facts']),decoded_tree=_owned)!=entry['facts_sha256']: raise ValueError('Catalog facts digest mismatch: '+key)
            package,_,identity=_identity(key,dict(payload.get('mount_aliases',{})))
            if package.casefold()!=entry['facts']['package'].casefold(): raise ValueError('Catalog fact package mismatch: '+key)
            relative=Path(entry['source']['relative_path'])
            if relative.is_absolute() or '..' in relative.parts: raise ValueError('Unsafe catalog source path')
        self._source_repository=None
        if self.native_root is not None:
            mounts={mount:tuple(self.native_root/path for path in root_values(paths)) for mount,paths in self.payload['mounts'].items()}
            self._source_repository=NativeAssetRepository(native_mounts(self.native_root,mounts),build=self.build,mapping=self.mapping,mount_aliases=dict(self.payload.get('mount_aliases',{})),uniform_selectors=serialize(self.payload.get('uniform_selectors',{})),uniform_policy=self.payload.get('uniform_policy','preserve-authored'))
    @classmethod
    def read(cls,path, *, native_root=None,cached_policy='source-associated'):
        # Validation freezes and hashes every record (about 6 s for the bundled bank).
        # A catalog without a native root is immutable after __init__, so one validated
        # instance per unchanged file serves every session and reviewed-source reader.
        if native_root is not None: return cls(_read(path),native_root=native_root,cached_policy=cached_policy)
        target=resolved_io_path(path); stat=target.stat()
        key=(str(target).casefold(),stat.st_size,stat.st_mtime_ns,getattr(stat,'st_ino',0),cached_policy)
        with _READ_LOCK:
            cached=_READ_CACHE.get(cls)
            if cached is not None and cached[0]==key: return cached[1]
            # Decoding and freezing allocate about 3M containers. The cyclic collector
            # rescans them repeatedly (load 7.7 s with it on, 3.6 s with it off) and each
            # pass stalls every other thread, so pause it for the build.
            import gc
            paused=gc.isenabled()
            if paused: gc.disable()
            try: result=cls(_read(target),cached_policy=cached_policy,_owned=True)
            finally:
                if paused: gc.enable()
            _READ_CACHE[cls]=(key,result)
            return result
    def validate_reviewed_limits(self, config):
        from ..rebuild.native_catalog_bridge import validate_reviewed_limits
        return validate_reviewed_limits(self.__validation_payload, config)
    def write(self,path):
        target=resolved_io_path(path)
        if self.native_root is not None and target.is_relative_to(self.native_root): raise ValueError('Catalog output must not overwrite the native source corpus')
        target.parent.mkdir(parents=True,exist_ok=True)
        data=json.dumps(serialize(self.payload),indent=2,sort_keys=True,allow_nan=False)
        handle,temporary=tempfile.mkstemp(prefix='.'+target.name+'.',suffix='.tmp',dir=target.parent)
        try:
            with os.fdopen(handle,'w',encoding='utf8') as stream: stream.write(data)
            os.replace(temporary,target)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
    def _context(self,repository):
        if not repository.build or not repository.mapping: raise StaleCatalogError('Active build and mapping are required for catalog fallback')
        if repository.build and repository.build!=self.build: raise StaleCatalogError('Catalog build differs from current repository')
        if repository.mapping and repository.mapping!=self.mapping: raise StaleCatalogError('Catalog mapping differs from current repository')
    def _fallback(self,reference,repository):
        self._context(repository)
        package,name,key=_identity(reference,repository.mount_aliases)
        entry=self._records.get(key.casefold())
        if entry is None: raise FileNotFoundError('No exact native catalog material: '+key)
        if self._source_repository is not None:
            current,ignored=_native_local(self._source_repository,key)
            if current is None: raise StaleCatalogError('Current native source was removed: '+key)
            return current
        source=entry['source']
        provenance=({'kind':'catalog-native','package':key,'relative_path':source['relative_path'],'sha256':source['sha256'],'facts_sha256':entry['facts_sha256'],'semantic_sha256':entry['semantic_sha256'],'build':self.build,'mapping':self.mapping},
                    {'kind':'native-default-projection','package':key,'report':entry.get('uniform_projection',{}),'cached_observations':entry.get('cached_observations'),'semantic_sha256':entry['semantic_sha256']})
        return _from_facts(entry['facts'],provenance)
    def load_material(self,reference,repository,_chain=()):
        aliases={**dict(self.payload.get('mount_aliases',{})),**repository.mount_aliases}
        repository=copy.copy(repository); repository.mount_aliases=aliases
        if not hasattr(repository,'uniform_selectors'): repository.uniform_selectors=serialize(self.payload.get('uniform_selectors',{}))
        if not hasattr(repository,'uniform_policy'): repository.uniform_policy=self.payload.get('uniform_policy','preserve-authored')
        package,name,key=_identity(reference,aliases)
        if key.casefold() in _chain: raise ValueError('Catalog material parent cycle: '+key)
        local,ignored=_native_local(repository,reference)
        if local is None:
            local=self._fallback(reference,repository)
            local=replace(local,provenance=local.provenance+tuple(ignored))
        parent=None
        if local.master and local.master.casefold()!=key.casefold(): parent=self.load_material(local.master,repository,_chain+(key.casefold(),))
        event=_projection_event(local); observations=event.get('cached_observations') if event else None
        if observations is not None:
            entry=self._records.get(key.casefold()); proof=entry.get('association') if entry else None
            current=[]
            if parent is not None:
                for source in parent.provenance:
                    if source.get('kind')=='catalog-native' or (source.get('kind')=='local-source' and source.get('selected')):
                        current.append({'package':source['package'],'sha256':source['sha256'],'semantic_sha256':source.get('semantic_sha256')})
            status='unassociated'; apply=self.cached_policy=='source-associated'
            if proof is not None:
                expected=proof['parent_chain']
                same_identity=proof['original_parent'].casefold()==local.master.casefold() and proof['build']==repository.build and proof['mapping']==repository.mapping
                same_chain=len(expected)==len(current) and all(a['package'].casefold()==b['package'].casefold() and (a['sha256']==b['sha256'] or (a.get('semantic_sha256') is not None and a.get('semantic_sha256')==b.get('semantic_sha256'))) for a,b in zip(expected,current))
                apply=same_identity and same_chain; status='associated' if apply else 'suppressed-parent-change'
            if self.cached_policy=='ignore-observations': apply=False; status='ignored-by-policy'
            if apply: local=merge_records(_from_facts(observations),local)
            binding={'kind':'cached-observation-binding','package':key,'status':status,'policy':self.cached_policy,'applied':apply,'original_parent':observations['parent'],'recorded_parent':proof['original_parent'] if proof else None,'current_parent':local.master,'recorded_parent_chain':serialize(proof['parent_chain']) if proof else None,'current_parent_chain':current,'build':repository.build,'mapping':repository.mapping}
            local=replace(local,provenance=local.provenance+(binding,))
        return merge_records(parent,local) if parent is not None else local
    def texture_metadata(self,reference,repository):
        fact=self.texture_fact(package_ref(reference,repository.mount_aliases),repository)
        if fact is None: return None
        source=fact['source']
        result={'Type':fact['type'],'NativeTextureKind':native_texture_kind({'Type':fact['type'],**({'NativeTextureKind':fact['native_texture_kind']} if 'native_texture_kind' in fact else {})}),'Name':fact['name'],'Package':fact['package'],'Properties':serialize(fact['properties']),
                'DataRaidersSource':{'projection':'native-catalog-texture-v1','source_sha256':source['sha256'],'build':self.build,'mapping':self.mapping}}
        for key in ('path','relative_path','aliases'):
            if key in source: result['DataRaidersSource']['source_'+key]=serialize(source[key])
        if fact.get('resource_kind'):
            result.pop('NativeTextureKind',None);result['NativeResourceKind']=fact['resource_kind']
        if result.get('NativeTextureKind')=='Texture2DArray': result['Mips']=[{'SizeZ':int(fact['slice_count'])}]
        return result
    def texture_fact(self,reference,repository):
        current=_texture_fact(repository,reference)
        if current is not None: return current
        self._context(repository)
        if self._source_repository is not None:
            current=_texture_fact(self._source_repository,reference)
            if current is None and reference.casefold() in self._textures: raise StaleCatalogError('Current native texture source was removed: '+reference)
            return current
        value=self._textures.get(reference.casefold())
        return None if value is None else serialize(value)


def generate_catalog(native_root,destination,references, *, build,mapping,mounts=None,mount_aliases=None,uniform_selectors=None,uniform_policy='preserve-authored',continue_on_error=False):
    if not build or not mapping: raise ValueError('Explicit game build and mapping provenance are required')
    root=resolved_io_path(native_root); mounted=native_mounts(root,mounts)
    repository=NativeAssetRepository(mounted,build=build,mapping=mapping,mount_aliases=mount_aliases,uniform_selectors=uniform_selectors,uniform_policy=uniform_policy)
    records={}; textures={}; visiting=set(); diagnostics=[]; errors=[]; requested_count=0
    def add(reference):
        package,name,key=_identity(reference,repository.mount_aliases)
        if key in records: return
        if key in visiting: raise ValueError('Native generation parent cycle: '+key)
        visiting.add(key); record,_=_native_local(repository,reference)
        if record is None: raise FileNotFoundError('No exact raw native source: '+key)
        if record.master and record.master.casefold()!=key.casefold(): add(record.master)
        chosen=next(source for source in record.provenance if source['selected'])
        source_path=resolved_io_path(chosen['path'])
        if not source_path.is_relative_to(root): raise ValueError('Non-native source escaped generation root')
        facts=record_facts(record)
        projection=_projection_event(record); parent_chain=[]
        if record.master.casefold()!=key.casefold():
            parent_entry=next(value for identity,value in records.items() if identity.casefold()==record.master.casefold())
            parent_chain=serialize(parent_entry['association']['parent_chain'])+[{'package':record.master,'sha256':parent_entry['source']['sha256'],'semantic_sha256':parent_entry['semantic_sha256']}]
        records[key]={'facts':facts,'facts_sha256':_json_hash(facts),'source':{'relative_path':source_path.relative_to(root).as_posix(),'sha256':chosen['sha256']},'semantic_sha256':projection['semantic_sha256'],
                      'uniform_projection':serialize(projection['report']),'cached_observations':serialize(projection['cached_observations']),
                      'association':{'original_parent':record.master,'parent_chain':parent_chain,'build':build,'mapping':mapping}}
        if projection['report']['conflicts']: diagnostics.append({'kind':'native-default-conflicts','package':key,'details':serialize(projection['report']['conflicts'])})
        if projection['report']['missing']: diagnostics.append({'kind':'native-default-missing','package':key,'details':serialize(projection['report']['missing'])})
        texture_references=[reference for _,reference,_ in _texture_refs(record)]+[entry['reference'] for entry in projection['report'].get('constant_textures',())]
        for reference in texture_references:
            if reference in textures: continue
            fact=_texture_fact(repository,reference)
            if fact is None: diagnostics.append({'kind':'missing-native-texture-metadata','reference':reference}); continue
            source=resolved_io_path(fact['source']['path'])
            if not source.is_relative_to(root): raise ValueError('Texture source escaped native root')
            fact['source']={'relative_path':source.relative_to(root).as_posix(),'sha256':fact['source']['sha256'],'aliases':[{'relative_path':resolved_io_path(alias['path']).relative_to(root).as_posix(),'sha256':alias['sha256'],'suffix':alias.get('suffix','.json')} for alias in fact['source'].get('aliases',())]}
            textures[reference]=fact
        visiting.remove(key)
    for reference in references:
        requested_count+=1
        try: add(reference)
        except Exception as error:
            if not continue_on_error: raise
            errors.append({'reference':str(reference),'error_type':type(error).__name__,'reason':str(error)})
            for pending in tuple(visiting): records.pop(pending,None)
            visiting.clear()
    payload={'format':FORMAT,'build':build,'mapping':mapping,'mounts':{mount:[path.relative_to(root).as_posix() for path in paths] for mount,paths in mounted.items()},'mount_aliases':dict(mount_aliases or {}),'records':records,'textures':textures,'uniform_selectors':dict(uniform_selectors or {}),'uniform_policy':uniform_policy,'diagnostics':diagnostics,'errors':errors,'statistics':{'requested_references':requested_count,'material_records':len(records),'texture_metadata':len(textures),'error_count':len(errors)}}
    catalog=NativeCatalog(payload,native_root=root); catalog.write(destination); return catalog

@dataclass(frozen=True)
class BuildSignature:
    key: str
    record: MaterialRecord
    dependencies: tuple
    diagnostics: tuple
    complete: bool
    def can_reuse(self,stored_key): return self.complete and stored_key==self.key


def _load_current(reference,repository,chain=()):
    _,_,key=_identity(reference,repository.mount_aliases)
    if key.casefold() in chain: raise ValueError('Current native parent cycle: '+key)
    local,ignored=_native_local(repository,reference)
    if local is None: raise FileNotFoundError('No exact native source or catalog fallback: '+key)
    parent=_load_current(local.master,repository,chain+(key.casefold(),)) if local.master.casefold()!=key.casefold() else None
    event=_projection_event(local); observations=event.get('cached_observations') if event else None
    if observations is not None:
        local=merge_records(_from_facts(observations),local)
        local=replace(local,provenance=local.provenance+({'kind':'cached-observation-binding','package':key,'status':'unassociated','policy':'current-raw-unassociated','applied':True,'original_parent':local.master,'current_parent':local.master,'build':repository.build,'mapping':repository.mapping},))
    return merge_records(parent,local) if parent is not None else local


def implementation_signature(paths=None):
    """Content revision of the owned runtime; callers may provide explicit code files."""
    paths=sorted(Path(__file__).parent.glob('*.py')) if paths is None else sorted(Path(path) for path in paths)
    if not paths: raise ValueError('Implementation revision requires source files')
    return _json_hash([{'name':path.name,'sha256':digest(path)} for path in paths])

def _active_plan_bindings(plan):
    pending=[value for name,value in plan.outputs.items() if name!='alpha' or not ignores_alpha_output(plan.settings)]; visited=set(); result=[]
    while pending:
        value=pending.pop()
        if isinstance(value,Expr):
            if id(value) in visited: continue
            visited.add(id(value))
            if value.op=='texture': result.append({'name':value.attrs.get('parameter','active-texture'),'reference':value.attrs['reference'],'role':value.attrs.get('role','data')})
            pending.extend(value.args); pending.extend(value.attrs.values())
        elif isinstance(value,Mapping): pending.extend(value.values())
        elif isinstance(value,(list,tuple)): pending.extend(value)
    return result

def material_build_signature(reference,repository, *, catalog=None,roles=None,recipe=None,implementation_revision=None,plan=None,texture_bindings=None,slice_suffix=None,array_image_path=None,strict_images=False):
    """Resolve current parents and every native array slice BEFORE graph reuse.

    roles maps qualified parameter, parameter name or exact texture package to a role
    or role sequence. Native SRGB supplies the default. Array transport is explicit:
    repository.array_slices, native SourceTextures, an array_image_path callback, a
    repository image_path(...,slice=), or caller-declared slice_suffix such as
    '_LAYER{index}'. No directory scan or basename borrowing resolves resources.
    """
    if recipe is None or recipe=='': raise ValueError('An explicit family/build recipe revision is required before material reuse')
    implementation_revision=implementation_signature() if implementation_revision is None else implementation_revision
    if not implementation_revision: raise ValueError('Implementation revision must be nonempty')
    record=catalog.load_material(reference,repository) if catalog is not None else _load_current(reference,repository)
    dependencies=[serialize(entry) for entry in record.provenance]; diagnostics=[]; complete=True; roles={} if roles is None else roles
    metadata={}; resources={}
    def fact_for(ref):
        if ref not in metadata:
            try:
                paths=tuple(path for _,path in repository.texture_sources(ref)) if hasattr(repository,'texture_sources') else tuple(path for suffix in ('.arc-texture.json','.json') for path in (repository.resolve_all(ref,suffix) if hasattr(repository,'resolve_all') else tuple(path for ext,path in _material_sources(repository,ref) if ext==suffix)))
            except FileNotFoundError:
                paths=()
            dependencies.extend({'kind':'local-image-json','package':ref,'path':str(path),'sha256':digest(path)} for path in paths)
            metadata[ref]=catalog.texture_fact(ref,repository) if catalog is not None else _texture_fact(repository,ref)
        return metadata[ref]
    def unavailable(message):
        nonlocal complete
        complete=False; diagnostics.append(message)
        if strict_images: raise FileNotFoundError(message)
    def resolve_image(ref,role,index=None,transport=None):
        identity=(ref,role,index)
        if identity in resources: return
        entry={'kind':'image','package':ref,'role':role,'slice':index}
        try:
            if transport is not None: path=repository.image_path(transport,role)
            elif index is None: path=repository.image_path(ref,role)
            elif array_image_path is not None: path=array_image_path(ref,index,role)
            elif 'slice' in inspect.signature(repository.image_path).parameters: path=repository.image_path(ref,role,slice=index)
            else: raise FileNotFoundError('No declared array slice transport: '+ref+'['+str(index)+']')
            if path is None: raise FileNotFoundError('Missing exact image path')
            path=resolved_io_path(path)
            if not path.is_file(): raise FileNotFoundError(str(path))
            entry.update(status='present',path=str(path),sha256=digest(path),transport=transport)
            if hasattr(repository,'image_aliases'):
                entry['aliases']=serialize(repository.image_aliases(transport or ref,role,slice=index,path=path))
                if len(entry['aliases'])>1: diagnostics.append('byte_identical_exact_image_aliases:'+ref+':'+str(index))
        except FileNotFoundError as error:
            entry['status']='missing'; unavailable(str(error))
        resources[identity]=entry
    for source in record.provenance:
        if source.get('kind')=='cached-observation-binding' and source.get('status')!='associated': diagnostics.append('cached_observation_policy:'+source['package']+':'+source['status'])
        if source.get('kind')=='native-default-projection':
            report=source.get('report',{})
            if report.get('conflicts'): diagnostics.append('native_default_source_conflict:'+source['package']+':'+report.get('policy','preserve-authored'))
            if report.get('missing'): unavailable('Unresolved native uniform defaults: '+source['package'])
    if plan is not None and texture_bindings is not None: raise ValueError('Provide an active plan or explicit texture bindings, not both')
    active=_active_plan_bindings(plan) if plan is not None else texture_bindings
    if active is None:
        bindings=[(name,reference,scope,None) for name,reference,scope in _texture_refs(record)]
        for source in record.provenance:
            if source.get('kind')=='native-default-projection': bindings.extend(('fixed-native-texture:'+entry['reference'],entry['reference'],None,None) for entry in source.get('report',{}).get('constant_textures',()))
    else:
        bindings=[(entry.get('name','active-texture'),entry['reference'],entry.get('scope'),entry.get('role','data')) for entry in active]
    for name,ref,scope,bound_role in bindings:
        ref=package_ref(ref,repository.mount_aliases); fact=fact_for(ref)
        qualified=(scope[0]+':'+str(scope[1])+':'+name) if scope is not None else name
        selected=roles[qualified] if qualified in roles else roles[name] if name in roles else roles[ref] if ref in roles else None
        if bound_role is not None: selected=bound_role
        if selected is None:
            if fact is not None and 'SRGB' in fact['properties']: selected='color' if fact['properties']['SRGB'] else 'data'
            else: selected='color'; diagnostics.append('native_SRGB_absent_engine_default_color:'+ref)
        selected=(selected,) if isinstance(selected,(str,bool)) else tuple(selected)
        selected=tuple(sorted(set('color' if role is True else 'data' if role is False else role for role in selected)))
        if not selected or any(role not in ('color','data','normal') for role in selected): raise ValueError('Explicit image roles must be color/data/normal')
        if fact is None:
            unavailable('Missing exact native texture metadata: '+ref)
            dependencies.append({'kind':'texture-metadata','package':ref,'status':'missing'})
            if ref in repository.array_slices: raise NativeMetadataError('Array transport exists but native slice count is unavailable: '+ref)
            for role in selected: resolve_image(ref,role)
            continue
        if fact.get('resource_kind'):
            dependencies.append({'kind':'native-non-image-resource','package':ref,'resource_kind':fact['resource_kind'],'type':fact['type'],'source':serialize(fact['source']),'properties':serialize(fact['properties'])})
            unavailable('Unsupported active native non-image resource: '+fact['resource_kind']+':'+ref)
            continue
        texture_kind=native_texture_kind({'Type':fact['type'],**({'NativeTextureKind':fact['native_texture_kind']} if 'native_texture_kind' in fact else {})})
        dependencies.append({'kind':'texture-metadata','package':ref,'native_texture_kind':texture_kind,'type':fact['type'],'properties':serialize(fact['properties']),'source':serialize(fact['source']),'slice_count':fact.get('slice_count')})
        if texture_kind=='Texture2DArray':
            count=int(fact['slice_count']); transports=repository.array_slices.get(ref)
            if transports is not None and len(transports)!=count: raise NativeMetadataError('Native array declares '+str(count)+' slices, mapping has '+str(len(transports))+': '+ref)
            sources=fact.get('source_textures')
            for index in range(count):
                transport=transports[index] if transports is not None else sources[index] if sources is not None else ref+slice_suffix.format(index=index) if slice_suffix is not None else None
                for role in selected: resolve_image(ref,role,index,transport)
        elif texture_kind=='Texture2D':
            for role in selected: resolve_image(ref,role)
        else: unavailable('Unsupported texture resource type for signature: '+fact['type']+':'+ref)
    dependencies.extend(resources.values())
    # Deduplicate metadata encountered through multiple scoped/global bindings.
    unique={_json_hash(entry):entry for entry in dependencies}; dependencies=tuple(unique[key] for key in sorted(unique))
    plan_hash=None
    if plan is not None:
        memo={}
        plan_hash={'family':plan.family,'outputs':{name:expression_fingerprint(value,memo) for name,value in plan.outputs.items() if name!='alpha' or not ignores_alpha_output(plan.settings)},'settings':serialize(plan.settings)}
    payload={'format':'native-material-build-signature-v2','build':repository.build or (catalog.build if catalog else ''),'mapping':repository.mapping or (catalog.mapping if catalog else ''),'implementation_revision':serialize(implementation_revision),'family_recipe':serialize(recipe),'active_plan':plan_hash,'explicit_texture_bindings':serialize(texture_bindings),'facts':record_facts(record),'roles':serialize(roles),'dependencies':dependencies}
    return BuildSignature(_json_hash(payload),record,freeze(dependencies),tuple(diagnostics),complete)


def iter_native_material_references(native_root,mounts=None,mount_aliases=None):
    """Streaming corpus iterator; reads raw game JSON, retaining no flattened bank."""
    root=resolved_io_path(native_root); mounted=native_mounts(root,mounts); seen=set()
    for base in (path for paths in mounted.values() for path in paths):
        for directory,_,filenames in os.walk(base):
            for filename in sorted(filenames):
                if not filename.endswith('.json'): continue
                path=Path(directory)/filename
                try: raw=_read(path)
                except (UnicodeError,json.JSONDecodeError): continue
                for entry in _exports(raw):
                    if not isinstance(entry,dict) or entry.get('Type') not in MATERIAL_TYPES or not entry.get('Package') or not entry.get('Name'): continue
                    package=package_ref(entry['Package'],mount_aliases); name=entry['Name']
                    reference=package if name.casefold()==package.rsplit('/',1)[-1].casefold() else package+'.'+name
                    if reference.casefold() not in seen: seen.add(reference.casefold()); yield reference

# Bind this module's source identity at import; overwrites require restart.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
