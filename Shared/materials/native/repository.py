"""Exact mounted asset lookup and native-only material inheritance/cache."""
from pathlib import Path
from collections.abc import Mapping
import hashlib
import json
import re
import os, threading
from collections import OrderedDict
from .core import MaterialRecord, base_property_overrides, serialize


def resolved_io_path(path):
    """Resolve a filesystem path while preserving an explicitly supplied Windows prefix."""
    raw=os.fspath(path); prefix=chr(92)*2+'?'+chr(92)
    extended=raw.startswith(prefix)
    result=Path(path).resolve()
    if os.name=='nt' and extended and not str(result).startswith(prefix):
        text=str(result)
        text=prefix+'UNC'+chr(92)+text.lstrip(chr(92)) if text.startswith(chr(92)*2) else prefix+text
        result=Path(text)
    return result

_DEFAULT_ALIASES={'PioneerGame/Content':'/Game','RemappedPlugins/Content':'/Game','Engine/Content':'/Engine','RemappedPlugins/EmbarkScript/Content':'/EmbarkScript'}

def _reference_text(value):
    if isinstance(value,Mapping): value=value.get('ObjectPath',value.get('AssetPathName',value.get('Package','')))
    if value is None: return ''
    value=str(value).strip().replace('\\','/')
    if "'" in value: value=value.split("'",1)[1].rstrip("'")
    return value.split(':',1)[0]

def object_name(reference):
    if isinstance(reference,Mapping) and reference.get('ObjectName'):
        named=str(reference['ObjectName'])
        if "'" in named: return named.split("'",1)[1].rstrip("'").rsplit('.',1)[-1]
    value=_reference_text(reference); leaf=value.rsplit('/',1)[-1]
    if '.' in leaf:
        package,name=leaf.rsplit('.',1)
        if not name.isdecimal() and name.lower() not in ('json','uasset','png','hdr','tga','psk','pskx'): return name
        return package
    return leaf

def package_ref(value,mount_aliases=None):
    value=_reference_text(value)
    if not value: return ''
    if '.' in value.rsplit('/',1)[-1]: value=value.rsplit('.',1)[0]
    aliases={**_DEFAULT_ALIASES,**(mount_aliases or {})}
    physical=value.lstrip('/')
    for prefix,target in sorted(aliases.items(),key=lambda entry:len(entry[0]),reverse=True):
        prefix=prefix.strip('/').replace('\\','/')
        if physical.casefold()==prefix.casefold() or physical.casefold().startswith(prefix.casefold()+'/'):
            value=str(target).rstrip('/')+physical[len(prefix):]; break
    if not value.startswith('/'): raise ValueError('Exact mounted package reference required: '+value)
    if any(p in ('.','..') for p in value.split('/')): raise ValueError('Unsafe package reference')
    return value.rstrip('/')
_DIGEST_CACHE=OrderedDict()
_DIGEST_LOCK=threading.RLock()
_DIGEST_LIMIT=1024

def _path_identity(path):
    return os.path.normcase(str(resolved_io_path(path)))

def invalidate_digest_cache(path=None):
    """Force a content rescan after an external writer with preserved timestamps."""
    with _DIGEST_LOCK:
        if path is None: _DIGEST_CACHE.clear()
        else:
            normalized=_path_identity(path)
            for key in list(_DIGEST_CACHE):
                if key[0]==normalized: del _DIGEST_CACHE[key]

def digest(path):
    path=resolved_io_path(path); normalized=_path_identity(path)
    with _DIGEST_LOCK:
        for attempt in range(3):
            stat=path.stat()
            key=(normalized,stat.st_size,stat.st_mtime_ns,getattr(stat,'st_ino',None),getattr(stat,'st_ctime_ns',None))
            if key in _DIGEST_CACHE:
                _DIGEST_CACHE.move_to_end(key); return _DIGEST_CACHE[key]
            hasher=hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''): hasher.update(chunk)
            after=path.stat()
            if (stat.st_size,stat.st_mtime_ns,getattr(stat,'st_ino',None),getattr(stat,'st_ctime_ns',None))!=(after.st_size,after.st_mtime_ns,getattr(after,'st_ino',None),getattr(after,'st_ctime_ns',None)): continue
            result=hasher.hexdigest(); _DIGEST_CACHE[key]=result
            while len(_DIGEST_CACHE)>_DIGEST_LIMIT: _DIGEST_CACHE.popitem(last=False)
            return result
    raise RuntimeError('Asset changed repeatedly while hashing: '+str(path))


def _exports(raw):
    if isinstance(raw,list): return raw
    if isinstance(raw,dict) and 'Exports' in raw: return raw['Exports']
    if isinstance(raw,dict) and isinstance(raw.get('Data'),list): return raw['Data']
    return [raw]


def _texture_ref(value,mount_aliases=None):
    if value is None or value=='' or value=='None': return None
    if isinstance(value,Mapping):
        keys=[key for key in ('ObjectPath','AssetPathName','Package') if key in value]
        if not keys:
            if value.get('IsNull') is True: return None
            raise ValueError('Texture reference has no exact package path')
        if value[keys[0]] in (None,'None',''): return None
    return package_ref(value,mount_aliases)


def _color(value):
    if isinstance(value,Mapping): return tuple(float(value.get(k,1.0 if k=='A' else 0.0)) for k in 'RGBA')
    values=tuple(float(v) for v in value)
    return values+(1.0,) if len(values)==3 else values


def _bool(value):
    if isinstance(value,bool): return value
    if isinstance(value,(int,float)) and value in (0,1): return bool(value)
    if isinstance(value,str) and value.lower() in ('true','false'): return value.lower()=='true'
    raise ValueError('Invalid static switch value: '+repr(value))


def parse_material(raw, package, provenance=(), *, strict_identity=False, expected_name=None, mount_aliases=None):
    expected_name=object_name(package) if expected_name is None else expected_name
    package=package_ref(package,mount_aliases)
    candidates=[e for e in _exports(raw) if isinstance(e,Mapping) and (e.get('Type') in ('Material','MaterialInstanceConstant','MaterialInstance') or (not e.get('Type') and any(field in e for field in ('Properties','Parent','master','scalars','colors','textures','switches','settings','ScalarParameterValues','VectorParameterValues','TextureParameterValues','StaticSwitchParameters','StaticParameters','StaticParametersRuntime'))))]
    matching=[e for e in candidates if e.get('Package') and package_ref(e['Package'],mount_aliases).casefold()==package.casefold() and (not e.get('Name') or str(e['Name']).casefold()==expected_name.casefold())]
    if matching: candidates=matching
    if len(candidates)!=1: raise ValueError('Expected one exact material export for '+package)
    item=candidates[0]
    if item.get('Package') and package_ref(item['Package'],mount_aliases).casefold()!=package.casefold(): raise ValueError('Material export package mismatch for '+package)
    if item.get('Name') and str(item['Name']).casefold()!=expected_name.casefold(): raise ValueError('Material export Name mismatch for '+package)
    if strict_identity and (not item.get('Package') or not item.get('Name')): raise ValueError('Authoritative material requires export Package and Name: '+package)
    p=item.get('Properties',item)
    if (isinstance(raw,Mapping) and raw.get('IsNull') is True) or item.get('IsNull') is True: raise ValueError('Null material export is not a current source record: '+package)
    result={k:{} for k in ('scalars','colors','textures','switches','settings','scopes')}
    kinds={'scalars':('ScalarParameterValues',float),'colors':('VectorParameterValues',_color),'textures':('TextureParameterValues',lambda value:_texture_ref(value,mount_aliases)),'switches':('StaticSwitchParameters',_bool)}
    for kind,(native,convert) in kinds.items():
        flat=p.get(kind,{})
        if isinstance(flat,Mapping): result[kind].update({str(k):convert(v) for k,v in flat.items()})
        entries=p.get(native,[])
        if kind=='switches':
            entries=list(entries)+list(p.get('StaticParameters',{}).get('StaticSwitchParameters',[]))+list(p.get('StaticParametersRuntime',{}).get('StaticSwitchParameters',[]))
        for entry in entries:
            if not isinstance(entry,Mapping): continue
            info=entry.get('ParameterInfo',{})
            name=info.get('Name',entry.get('ParameterName',entry.get('Name')))
            if name is None: continue
            if kind=='switches' and entry.get('bOverride',True) is False: continue
            key='Value' if kind=='switches' and 'Value' in entry else 'ParameterValue'
            if key not in entry: continue
            value=convert(entry[key]); association=str(info.get('Association','GlobalParameter')).split('::')[-1]; index=int(info.get('Index',-1))
            if association=='GlobalParameter': result[kind][name]=value
            else: result['scopes'].setdefault((association,index),{}).setdefault(kind,{})[name]=value
    # Cooked native cached default tables retain the association/index of each entry.
    # Entry indices below are evidenced by the current native Material JSON schema.
    cached=item.get('CachedExpressionData',p.get('CachedExpressionData',{}))
    for entry_index,value_key,kind in ((0,'ScalarValues','scalars'),(1,'VectorValues','colors'),(3,'TextureValues','textures'),(8,'StaticSwitchValues','switches')):
        table=cached.get('RuntimeEntries' if entry_index==0 else 'RuntimeEntries['+str(entry_index)+']',{})
        infos=table.get('ParameterInfoSet',[]); values=cached.get(value_key,[])
        if len(infos)!=len(values): raise ValueError('Native cached parameter table length mismatch: '+value_key)
        for info,value in zip(infos,values):
            name=info['Name']; association=str(info.get('Association','GlobalParameter')).split('::')[-1]; index=int(info.get('Index',-1))
            target=result[kind] if association=='GlobalParameter' else result['scopes'].setdefault((association,index),{}).setdefault(kind,{})
            if name not in target: target[name]=kinds[kind][1](value)
    # Typed native expression defaults are permitted only in the source package.
    for entry in _exports(raw):
        typ=str(entry.get('Type','')); ep=entry.get('Properties',{})
        name=ep.get('ParameterName')
        if not name: continue
        target='scalars' if 'ScalarParameter' in typ else 'colors' if 'VectorParameter' in typ else 'textures' if 'TextureSampleParameter' in typ or 'TextureObjectParameter' in typ else 'switches' if 'StaticSwitchParameter' in typ else None
        default=ep.get('DefaultValue',ep.get('Texture'))
        if target and default is not None and name not in result[target]: result[target][name]=kinds[target][1](default)
    # Root masters with hardcoded TextureSamples (M_SplineThicken) have no texture parameters;
    # their maps are named in TextureStreamingData and pathed in ReferencedTextures. The
    # channel suffix (CR, NXM) is the key, matching the legacy raw_record fallback.
    if item.get('Type')=='Material' and not result['textures']:
        referenced={}
        for ref in list(cached.get('ReferencedTextures') or [])+list(p.get('ReferencedTextures') or []):
            path=ref.get('ObjectPath') if isinstance(ref,Mapping) else ref
            if isinstance(path,str) and path not in ('','None'): referenced.setdefault(object_name(path).split('.')[0].casefold(),ref)
        for stream in p.get('TextureStreamingData') or []:
            tex=str(stream.get('TextureName') or '') if isinstance(stream,Mapping) else ''
            code=tex.rsplit('_',1)[-1].upper()
            ref=referenced.get(tex.casefold())
            if tex and ref is not None and re.fullmatch(r'[CDNROMAESXHGT]{1,4}',code): result['textures'].setdefault(code,_texture_ref(ref,mount_aliases))
    overrides=base_property_overrides(p.get('BasePropertyOverrides'))
    for native,key in (('BlendMode','blend'),('OpacityMaskClipValue','cutoff'),('TwoSided','two_sided'),('bTwoSided','two_sided'),('DitherOpacityMask','dither_opacity_mask')):
        if native in overrides: result['settings'][key]=overrides[native]
        elif native in p: result['settings'][key]=p[native]
    result['settings'].update(p.get('settings',{}))
    remap=item.get('CachedData',p.get('CachedData',{})).get('ParentLayerIndexRemap',[])
    if remap: result['settings']['_parent_layer_index_remap']=tuple(int(value) for value in remap)
    parent=package_ref(p['Parent'],mount_aliases) if p.get('Parent') else package_ref(item.get('master',p.get('master',package)),mount_aliases)
    return MaterialRecord(package,parent,provenance=provenance,**result)


def merge_records(parent,child):
    remap=child.settings.get('_parent_layer_index_remap',())
    if remap and (parent.scopes or child.scopes): raise NotImplementedError('Scoped parent-layer remap requires explicit native mapping semantics: '+repr(tuple(remap)))
    values={k:{**getattr(parent,k),**getattr(child,k)} for k in ('scalars','colors','textures','switches','settings')}
    values['textures']=dict(parent.textures)
    values['textures'].update({name:value for name,value in child.textures.items() if value not in (None,'')})
    scopes={key:{kind:dict(values) for kind,values in scoped.items()} for key,scoped in parent.scopes.items()}
    for key,scoped in child.scopes.items():
        for kind,parameters in scoped.items():
            inherited=scopes.setdefault(key,{}).setdefault(kind,{})
            inherited.update({name:value for name,value in parameters.items() if kind!='textures' or value not in (None,'')})
    return MaterialRecord(child.package,parent.master or parent.package,scopes=scopes,provenance=parent.provenance+child.provenance,**values)


class AssetRepository:
    """Mounts map exact Unreal roots to content directories; never basename-search."""
    def __init__(self,mounts, *, build='', mapping='', image_mounts=None, array_slices=None, mount_aliases=None):
        self.mount_aliases=dict(mount_aliases or {})
        self.mounts={str(k).rstrip('/'):resolved_io_path(v) for k,v in mounts.items()}
        self.image_mounts={str(k).rstrip('/'):resolved_io_path(v) for k,v in (image_mounts or mounts).items()}
        self.build=build; self.mapping=mapping; self._cache={}
        self.array_slices={package_ref(k,self.mount_aliases):tuple(package_ref(v,self.mount_aliases) for v in values) for k,values in (array_slices or {}).items()}
    def resolve(self,reference,suffix='.json', *, image=False):
        reference=package_ref(reference,self.mount_aliases); mounts=self.image_mounts if image else self.mounts
        choices=[root for root in mounts if reference==root or reference.startswith(root+'/')]
        if not choices: raise FileNotFoundError('Unmounted exact reference '+reference)
        mount=max(choices,key=len); base=mounts[mount]
        path=resolved_io_path(base.joinpath(reference[len(mount):].lstrip('/')+suffix))
        if not path.is_relative_to(base): raise ValueError('Path escaped mount')
        if not path.is_file(): raise FileNotFoundError(str(path))
        return path
    def load_material(self,reference,_chain=()):
        expected_name=object_name(reference); reference=package_ref(reference,self.mount_aliases)
        if reference in _chain: raise ValueError('Material parent cycle: '+' -> '.join(_chain+(reference,)))
        sources=[]
        for suffix in ('.arc-material.json','.json'):
            try: sources.append((suffix,self.resolve(reference,suffix)))
            except FileNotFoundError: pass
        if not sources: raise FileNotFoundError('No exact material source for '+reference)
        chosen_suffix,chosen=sources[0]
        provenance=tuple({'path':str(path),'sha256':digest(path),'build':self.build,'mapping':self.mapping,'selected':path==chosen,'object_name':expected_name,'kind':'native-arc' if suffix=='.arc-material.json' else 'json'} for suffix,path in sources)
        raw=json.loads(chosen.read_text(encoding='utf-8-sig'))
        local=parse_material(raw,reference,provenance,strict_identity=chosen_suffix=='.arc-material.json',expected_name=expected_name,mount_aliases=self.mount_aliases)
        merged=local
        if local.master and local.master!=reference:
            parent=self.load_material(local.master,_chain+(reference,)); merged=merge_records(parent,local)
        identity=self.material_key(merged)
        if identity not in self._cache: self._cache[identity]=merged
        return self._cache[identity]
    def material_key(self,record,roles=None):
        payload={'record':{key:serialize(getattr(record,key)) for key in ('package','master','scalars','colors','textures','switches','settings','scopes','provenance')},'build':self.build,'mapping':self.mapping,'roles':roles or {},'arrays':self.array_slices,'images':self._image_dependencies(record)}
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def _image_dependencies(self,record):
        result={}
        refs=list(record.textures.values())
        for scoped in record.scopes.values(): refs.extend(scoped.get('textures',{}).values())
        for reference in refs:
            if reference is None: continue
            try: result[reference+'::native_metadata']=digest(self.resolve(reference))
            except FileNotFoundError: result[reference+'::native_metadata']='missing'
            targets=self.array_slices.get(reference,(reference,))
            for target in targets:
                try: result[target+'::native_metadata']=digest(self.resolve(target))
                except FileNotFoundError: result[target+'::native_metadata']='missing'
                try: result[target]=digest(self.resolve(target,'.png',image=True))
                except FileNotFoundError: result[target]='missing'
        return result
    def array_reference(self,reference,index):
        reference=package_ref(reference,self.mount_aliases)
        if reference not in self.array_slices: raise KeyError('No authored array source mapping: '+reference)
        values=self.array_slices[reference]
        if index<0 or index>=len(values): raise IndexError('Texture array slice outside authored source list')
        return values[index]
    def image_key(self,reference,role):
        reference=package_ref(reference,self.mount_aliases)
        if role not in ('color','data','normal'): raise ValueError('Unknown image role')
        path=self.resolve(reference,'.png',image=True)
        return hashlib.sha256(json.dumps([package_ref(reference),role,str(path),digest(path),self.build,self.mapping]).encode()).hexdigest()
    def texture_info(self,reference):
        try: path=self.resolve(reference)
        except FileNotFoundError: return {}
        exports=_exports(json.loads(path.read_text(encoding='utf-8-sig')))
        candidates=[entry for entry in exports if str(entry.get('Type','')).startswith('Texture')]
        if len(candidates)!=1: return {}
        return {**candidates[0].get('Properties',{}),'source_path':str(path),'source_sha256':digest(path)}
    def image_path(self,reference,role='data'):
        return self.resolve(reference,'.png',image=True)
    def generate_defaults(self,references,destination):
        records=[self.load_material(ref) for ref in references]
        payload={'format':'native-defaults-v1','build':self.build,'mapping':self.mapping,'records':[{key:serialize(getattr(r,key)) for key in ('package','master','scalars','colors','textures','switches','settings','scopes','provenance')} for r in records]}
        Path(destination).write_text(json.dumps(payload,indent=2,sort_keys=True),encoding='utf8')
        return payload

# Bind this module's source identity at import; overwrites require restart.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
