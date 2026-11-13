"""Versioned native-catalog activation and sampled-resource cache integration.

An absent activation keeps current local exports usable without historical data.
A reviewed source-complete catalog supplies current defaults with exact rendering
limitations; incomplete focus catalogs cannot activate.
"""
from pathlib import Path
import hashlib,json,os
from ..native.assets import NativeAssetRepository
from ..native.catalog import NativeCatalog,native_mounts,material_build_signature,implementation_signature,record_facts,_load_current,_texture_fact
from ..native.repository import digest,package_ref
from ..native.core import serialize

SCHEMA='native-catalog-activation-v1'
LIMITED_SCHEMA='native-catalog-activation-v2'
RECIPE='retained-native-profile-content-cache-v1'

def _io(path):
    value=os.path.abspath(os.fspath(path))
    if os.name=='nt' and not value.startswith('\\\\?\\'):
        value='\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value
    return Path(value)

def activation_path(): return Path(__file__).parent/'data/native_catalog_active.json'

def session(index):
    manifest=Path(getattr(index,'native_catalog_activation',activation_path()))
    if not manifest.is_file(): return None
    config=json.loads(manifest.read_text(encoding='utf-8-sig'))
    if not ((config.get('schema')==SCHEMA and config.get('reviewed_complete') is True) or (config.get('schema')==LIMITED_SCHEMA and config.get('reviewed_source_complete') is True)):
        raise ValueError('Native catalog activation requires explicit source review and exact limitations')
    bank=(manifest.parent/config['catalog']).resolve()
    if not bank.is_relative_to(manifest.parent.resolve()): raise ValueError('Native catalog must remain inside activation data directory')
    identity=(digest(manifest),digest(bank))
    if identity[1]!=config['sha256']: raise ValueError('Activated native catalog SHA mismatch')
    previous=getattr(index,'_native_catalog_session',None)
    if previous is not None and previous.activation_identity==identity: return previous
    try:
        if getattr(index,'compatibility_mode',False):raise ValueError('No declared native content mounts')
        native_mounts(_io(index.root),config.get('mounts'))
    except ValueError as error:
        if str(error)!='No declared native content mounts':raise
        from ..native.native_data import reviewed_source
        index._native_catalog_compat_source=reviewed_source(manifest)
        index._native_catalog_compatibility='Explicit flat/nonstandard FModel export; native namespace association unverified'
        return None
    result=Session(index,bank,config); result.activation_identity=identity
    index._native_catalog_session=result
    return result

class Session:
    def __init__(self,index,bank,config):
        self.index=index; self.config=config; self.catalog=NativeCatalog.read(bank)
        payload=self.catalog.payload; stats=payload.get('statistics',{})
        self.render_limits={}
        if config.get('schema')==LIMITED_SCHEMA:self.render_limits=reviewed_limits(self.catalog,config)
        elif payload.get('errors') or stats.get('error_count')!=0: raise ValueError('Partial native catalogs cannot activate')
        for field in ('build','mapping'):
            if config.get(field)!=payload[field]: raise ValueError('Native catalog activation context mismatch: '+field)
        for field,container in (('material_records','records'),('texture_metadata','textures')):
            if config.get('expected_'+field)!=len(payload[container]): raise ValueError('Native catalog activation reviewed coverage mismatch: '+field)
        mounts=native_mounts(_io(index.root),config.get('mounts'))
        self.repository=NativeAssetRepository(mounts,build=payload['build'],mapping=payload['mapping'],mount_aliases=payload.get('mount_aliases'),uniform_selectors=payload.get('uniform_selectors'),uniform_policy=payload.get('uniform_policy','preserve-authored'))
        self.slice_suffix=config.get('slice_suffix','_LAYER{index}')
        # Include retained profile bodies as well as the original backend. Hashing
        # attribution-bearing sources does not change their provenance.
        paths=sorted(Path(__file__).parent.glob('*.py'))+sorted((Path(__file__).parents[1]/'native').glob('*.py'))+sorted((Path(__file__).parents[3]/'Weapons'/'material_runtime').glob('*.py'))
        self.code_paths=tuple(paths)
    def guard_reference(self,reference):
        key=package_ref(reference,self.repository.mount_aliases).casefold()
        if key in self.render_limits:raise NativeRenderLimitationError('Native expression bindings unresolved for '+str(reference)+'; affected source: '+', '.join(self.render_limits[key]))
    def guard_record(self,record):
        if record.master:self.guard_reference(record.master)
        for event in record.provenance:
            if event.get('package'):self.guard_reference(event['package'])
            if event.get('kind')=='native-default-projection' and event.get('report',{}).get('deferred_bindings'):
                raise NativeRenderLimitationError('Native expression bindings unresolved in current hierarchy: '+str(event.get('package')))
        return record
    def load(self,reference):
        self.guard_reference(reference)
        return self.guard_record(self.catalog.load_material(reference,self.repository))
    def legacy_record(self,reference):
        from .assets import package as legacy_package
        record=self.load(reference); result={'package':legacy_package(record.package),'parent':legacy_package(record.master) if record.master!=record.package else '', 'name':record.package.rsplit('/',1)[-1]}
        for category in ('textures','scalars','colors','switches','settings'): result[category]=serialize(getattr(record,category))
        for normalized,native in (('blend','BlendMode'),('two_sided','TwoSided'),('cutoff','OpacityMaskClipValue'),('dither_opacity_mask','DitherOpacityMask')):
            if normalized in result['settings'] and native not in result['settings']:
                result['settings'][native]=result['settings'][normalized]
        for category in ('textures','scalars','colors','switches'):
            for name,value in list(result[category].items()): result[category]['Global.'+name]=value
        for (association,index),parameters in record.scopes.items():
            prefix=('Layer' if 'Layer' in association else 'Blend')+str(index)+'.'
            for category,values in parameters.items():
                if category not in result: raise ValueError('Unsupported native scoped category: '+category)
                result[category].update({prefix+name:serialize(value) for name,value in values.items()})
        current=record; visited=set()
        while current.master and current.master!=current.package:
            if current.package.casefold() in visited: raise ValueError('Native material parent cycle')
            visited.add(current.package.casefold()); current=self.load(current.master)
        result['master']=legacy_package(current.package)
        result['source_json']=next((str(p['path']) for p in record.provenance if p.get('selected') and str(p.get('package','')).split('.',1)[0]==record.package),'')
        result['native_catalog_provenance']=serialize(record.provenance)
        return result
    def signature(self,reference,bindings):
        return material_build_signature(reference,self.repository,catalog=self.catalog,texture_bindings=bindings,recipe=RECIPE,implementation_revision=implementation_signature(self.code_paths),slice_suffix=self.slice_suffix)
    def texture_fact(self,reference): return self.catalog.texture_fact(reference,self.repository)
    def tracker(self): return TrackingIndex(self)

class TrackingIndex:
    def __init__(self,owner): self.owner=owner; self.bindings=[]
    def __getattr__(self,name): return getattr(self.owner.index,name)
    def texture_info(self,reference):
        fact=self.owner.texture_fact(reference)
        return None if fact is None else dict(fact['properties'],Type=fact['type'])
    def image_path(self,reference,role='data',slice=None):
        normalized=package_ref(reference,self.owner.repository.mount_aliases)
        binding={'reference':normalized,'role':role}
        if binding not in self.bindings: self.bindings.append(binding)
        if slice is not None:
            fact=self.owner.texture_fact(normalized)
            if fact is None or fact['type']!='Texture2DArray': raise ValueError('Exact native array metadata required: '+normalized)
            if not 0<=int(slice)<fact['slice_count']: raise IndexError('Native array slice out of range')
            sources=fact.get('source_textures')
            normalized=sources[int(slice)] if sources else normalized+self.owner.slice_suffix.format(index=int(slice))
        try: return self.owner.repository.image_path(normalized,role)
        except FileNotFoundError: return None
    def texture(self,reference,layer=None): return self.image_path(reference,'data',slice=layer)

def begin(index,record):
    owner=session(index)
    if owner is None and record.get('native_source_mode')=='raw-complete': owner=raw_session(index)
    if owner is not None:owner.load(record['package'])
    return owner

def lookup(owner,materials,context_key,reference):
    for material in materials:
        if material.get('arc_native_context')!=context_key: continue
        try: bindings=json.loads(material.get('arc_native_bindings','null'))
        except (ValueError,TypeError): continue
        if not isinstance(bindings,list): continue
        signature=owner.signature(reference,bindings)
        if signature.can_reuse(material.get('arc_native_signature')): return material,signature
    return None,None

def stamp(owner,material,context_key,reference,tracker,report):
    signature=owner.signature(reference,tracker.bindings)
    material['arc_native_context']=context_key
    material['arc_native_bindings']=json.dumps(tracker.bindings,sort_keys=True)
    material['arc_native_signature']=signature.key if signature.complete else ''
    report['native_content_signature']={'key':signature.key,'complete':signature.complete,'diagnostics':list(signature.diagnostics),'dependencies':serialize(signature.dependencies),'recipe':RECIPE}
    return signature


class PartialNativeHierarchyError(ValueError):
    """A typed primary source must never inherit legacy flattened facts."""

class RawSession(Session):
    """Current exact typed exports, without activating any bundled catalog."""
    def __init__(self,index,mounts):
        self.index=index;self.catalog=None;self.config={};self.activation_identity=None;self.render_limits={}
        self.repository=NativeAssetRepository(mounts,build=str(getattr(index,'native_build','local-typed-native')),mapping=str(getattr(index,'native_mapping','local-source-hashes')))
        self.slice_suffix='_LAYER{index}'
        self.code_paths=tuple(sorted(Path(__file__).parent.glob('*.py'))+sorted((Path(__file__).parents[1]/'native').glob('*.py'))+sorted((Path(__file__).parents[3]/'Weapons'/'material_runtime').glob('*.py')))
    def load(self,reference): return _load_current(reference,self.repository)
    def texture_fact(self,reference): return _texture_fact(self.repository,reference)
    def legacy_record_if_authoritative(self,reference):
        sources=self.repository.material_sources(reference)
        if not sources:return None
        if not self.repository._is_native_json(sources[0][1],reference):return None
        try:result=self.legacy_record(reference)
        except FileNotFoundError as error:
            raise PartialNativeHierarchyError('Incomplete current native hierarchy; legacy values were not mixed: '+str(error)) from error
        result['native_source_mode']='raw-complete'
        result['native_source_diagnostics']=['Current typed raw sources; no bundled legacy material facts used']
        for event in result['native_catalog_provenance']:
            if event.get('kind')=='cached-observation-binding' and event.get('status')!='associated':
                result['native_source_diagnostics'].append('Cached-only facts remain source-local observations without parent-association proof: '+event['package'])
        return result

def raw_session(index):
    previous=getattr(index,'_native_raw_session',None)
    if previous is not None:return previous
    try:mounts=native_mounts(_io(index.root),getattr(index,'native_mounts',None))
    except ValueError as error:
        if str(error)=='No declared native content mounts':return None
        raise
    result=RawSession(index,mounts);index._native_raw_session=result;return result


class NativeRenderLimitationError(RuntimeError):
    """Known native data retained, rendering refused before graph mutation."""

def _limit_hash(value):
    return hashlib.sha256(json.dumps(serialize(value),sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()

def reviewed_limit_facts(payload):
    """Derive exact known limitations and inherited closure in linear DAG work."""
    aliases=payload.get('mount_aliases',{})
    identity=lambda ref:package_ref(ref,aliases).casefold()
    entries={}
    for key,entry in payload['records'].items():
        current=identity(entry['facts']['package'])
        if current in entries:raise ValueError('Duplicate material identity in reviewed catalog')
        entries[current]=entry
    sources=[];own={}
    for key,entry in entries.items():
        bindings=entry.get('uniform_projection',{}).get('deferred_bindings',[])
        if bindings:
            package=entry['facts']['package'];own[key]=(package,)
            sources.append({'package':package,'bindings_sha256':_limit_hash(bindings),'source_sha256':entry['source']['sha256'],'facts_sha256':entry['facts_sha256'],'semantic_sha256':entry['semantic_sha256']})
    memo={};visiting=set()
    def visit(key):
        if key in memo:return memo[key]
        if key in visiting:raise ValueError('Reviewed catalog parent cycle')
        if key not in entries:raise ValueError('Reviewed catalog missing exact parent: '+key)
        visiting.add(key);parent=identity(entries[key]['facts']['parent']);result=own.get(key,())
        if parent!=key:result=tuple(sorted(set(result+visit(parent))))
        visiting.remove(key);memo[key]=result;return result
    for key in entries:visit(key)
    affected={key:values for key,values in memo.items() if values}
    errors=payload.get('errors',[])
    if any(error.get('error_type')!='DeferredRenderingAmbiguity' for error in errors):raise ValueError('Unreviewable normalization or unknown error in native catalog')
    limits=[{'reference':error['reference'],'error_type':error['error_type'],'error_sha256':_limit_hash(error)} for error in errors]
    identities=[identity(limit['reference']) for limit in limits]
    if len(set(identities))!=len(identities) or set(identities)!=set(affected):raise ValueError('Declared limitation references differ from affected inheritance closure')
    return sorted(limits,key=lambda item:item['reference']),sorted(sources,key=lambda item:item['package']),affected

def reviewed_limits(catalog,config):
    """validate_reviewed_limits for a shared validated catalog: one walk per (catalog, activation config)."""
    key=json.dumps(config,sort_keys=True,default=str)
    cached=getattr(catalog,'_reviewed_limits',None)
    if cached is not None and cached[0]==key:return cached[1]
    result=validate_reviewed_limits(catalog.payload,config)
    catalog._reviewed_limits=(key,result)
    return result

_WARM={'thread':None}
def warming():
    """True while the prewarm thread is still validating the bank. Python work on the
    main thread then shares the interpreter lock with it and both crawl (measured:
    4 s prewarm becomes 50 s), so staged imports idle until this returns False."""
    thread=_WARM['thread']
    return thread is not None and thread.is_alive()
def prewarm():
    """Validate the bundled bank on a worker thread at first import intent so the
    first material build does not block the UI for the full validation."""
    if _WARM['thread'] is not None:return
    def work():
        try:
            manifest=activation_path()
            if not manifest.is_file():return
            config=json.loads(manifest.read_text(encoding='utf-8-sig'))
            bank=(manifest.parent/config['catalog']).resolve()
            if not bank.is_relative_to(manifest.parent.resolve()):return
            catalog=NativeCatalog.read(bank)
            if config.get('schema')==LIMITED_SCHEMA:reviewed_limits(catalog,config)
        except Exception:
            pass  # the import path repeats every check and reports the error itself
    import threading
    _WARM['thread']=threading.Thread(target=work,name='ArcNativeCatalogWarm',daemon=True)
    _WARM['thread'].start()

def validate_reviewed_limits(payload,config):
    stats=payload.get('statistics',{})
    if stats.get('normalization_error_count')!=0:raise ValueError('Native source normalization errors cannot be allowlisted')
    if stats.get('error_count')!=len(payload.get('errors',[])):raise ValueError('Native limitation error count mismatch')
    limits,sources,affected=reviewed_limit_facts(payload)
    if config.get('expected_render_limitations')!=len(limits) or config.get('expected_binding_sources')!=len(sources):raise ValueError('Reviewed limitation count mismatch')
    if config.get('reviewed_limitations')!=limits or config.get('reviewed_binding_sources')!=sources:raise ValueError('Missing, changed or unreviewed native limitation identity/hash')
    return affected
