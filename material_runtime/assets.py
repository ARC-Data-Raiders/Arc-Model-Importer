"""Exact multi-physical-root transport for one declared Unreal namespace."""
from collections.abc import Mapping
from pathlib import Path
import json,os
from .repository import AssetRepository,package_ref,object_name,resolved_io_path,parse_material,_exports,digest,merge_records,serialize

class AmbiguousAssetError(ValueError): pass

_NATIVE_TEXTURE_KINDS={'Texture','Texture2D','Texture2DArray','TextureCube','TextureCubeArray','VolumeTexture'}
_NATIVE_TEXTURE_CLASSES={kind:kind for kind in _NATIVE_TEXTURE_KINDS}
# Current native class + official UCurveLinearColorAtlas : public UTexture2D.
_NATIVE_TEXTURE_CLASSES['CurveLinearColorAtlas']='Texture2D'

def native_resource_kind(entry):
    # Exact native resource classes; neither is a static exported 2D texture.
    return {'RuntimeVirtualTexture':'runtime-virtual-texture','Font':'font'}.get(entry.get('Type'))


def native_texture_kind(entry):
    """Keep concrete Type, normalize only typed kind or an exact known class."""
    known=_NATIVE_TEXTURE_CLASSES.get(entry.get('Type'))
    if 'NativeTextureKind' in entry:
        declared=entry['NativeTextureKind']
        if not isinstance(declared,str) or declared not in _NATIVE_TEXTURE_KINDS:raise ValueError('Unknown explicit NativeTextureKind: '+str(declared))
        if known is not None and declared!=known:raise ValueError('Native texture class/kind conflict: '+str(entry.get('Type'))+'/'+str(declared))
        return declared
    return known

def root_values(value):
    return tuple(value) if isinstance(value,(list,tuple)) else (value,)

class NativeAssetRepository(AssetRepository):
    """Game roots may include PioneerGame/Content AND RemappedPlugins/Content.

    Every lookup uses exactly the same complete relative package in each root. Raw
    material authority may resolve a thin-JSON collision; image collisions and multiple
    authoritative candidates fail, even when a basename happens to match elsewhere.
    """
    def __init__(self,mounts, *, image_mounts=None,uniform_selectors=None,uniform_policy='preserve-authored',**kwargs):
        self.uniform_selectors=dict(uniform_selectors or {}); self.uniform_policy=uniform_policy
        self.alias_diagnostics={}
        self.mount_roots={mount.rstrip('/'):tuple(resolved_io_path(path) for path in root_values(paths)) for mount,paths in mounts.items()}
        self.image_roots={mount.rstrip('/'):tuple(resolved_io_path(path) for path in root_values(paths)) for mount,paths in (image_mounts or mounts).items()}
        if any(not roots for roots in (*self.mount_roots.values(),*self.image_roots.values())): raise ValueError('Every declared mount needs at least one physical root')
        super().__init__({mount:roots[0] for mount,roots in self.mount_roots.items()},image_mounts={mount:roots[0] for mount,roots in self.image_roots.items()},**kwargs)
    def resolve_all(self,reference,suffix='.json', *, image=False):
        reference=package_ref(reference,self.mount_aliases); roots=self.image_roots if image else self.mount_roots
        choices=[mount for mount in roots if reference==mount or reference.startswith(mount+'/')]
        if not choices: raise FileNotFoundError('Unmounted exact reference '+reference)
        mount=max(choices,key=len); relative=reference[len(mount):].lstrip('/')+suffix
        result=[]; seen=set()
        for base in roots[mount]:
            path=resolved_io_path(base/relative)
            if not path.is_relative_to(base): raise ValueError('Exact package path escaped mount')
            identity=os.path.normcase(str(path))
            if path.is_file() and identity not in seen: result.append(path); seen.add(identity)
        return tuple(result)
    def _is_native_json(self,path,reference):
        raw=json.loads(path.read_text(encoding='utf-8-sig'))
        if isinstance(raw,Mapping) and 'Data' in raw: return False
        entries=[entry for entry in _exports(raw) if isinstance(entry,Mapping) and (entry.get('Type') in ('Material','MaterialInstanceConstant','MaterialInstance') or native_texture_kind(entry) is not None or native_resource_kind(entry) is not None)]
        if not entries: return False
        package=package_ref(reference,self.mount_aliases); name=object_name(reference)
        matching=[entry for entry in entries if entry.get('Package') and package_ref(entry['Package'],self.mount_aliases).casefold()==package.casefold()]
        if not matching: raise ValueError('Native metadata package mismatch at '+str(path))
        if not any(str(entry.get('Name','')).casefold()==name.casefold() for entry in matching):raise ValueError('Native metadata object mismatch at '+str(path))
        if any(entry.get('Type') in ('Material','MaterialInstanceConstant','MaterialInstance') for entry in matching): parse_material(raw,package,strict_identity=True,expected_name=name,mount_aliases=self.mount_aliases)
        return True
    def _coalesce(self,reference,suffix,paths,image=False):
        aliases=tuple({'path':str(path),'sha256':digest(path)} for path in paths)
        if len({entry['sha256'] for entry in aliases})!=1: raise AmbiguousAssetError('Different bytes for exact aliases: '+str(reference)+suffix)
        self.alias_diagnostics[(package_ref(reference,self.mount_aliases),suffix,image)]={'kind':'byte-identical-exact-aliases','aliases':aliases,'selected_path':str(paths[0])}
        return paths[0]
    def image_path(self,reference,role='data',slice=None):
        if role not in ('color','data','normal'):raise ValueError('Unknown image role')
        if slice is not None:reference=self.array_reference(reference,int(slice))
        found=[]
        for suffix in ('.png','.hdr'):
            try:found.append(self.resolve(reference,suffix,image=True))
            except FileNotFoundError:pass
        if not found:raise FileNotFoundError('No exact PNG/HDR image: '+str(reference))
        if len(found)>1:raise AmbiguousAssetError('Multiple exact PNG/HDR representations require an authoritative format witness: '+str(reference))
        return found[0]
    def image_key(self,reference,role='data',slice=None):
        import hashlib
        path=self.image_path(reference,role,slice=slice)
        return hashlib.sha256(json.dumps([package_ref(reference,self.mount_aliases),role,slice,str(path),digest(path),self.build,self.mapping]).encode()).hexdigest()
    def image_aliases(self,reference,role='data',slice=None,path=None):
        reference=package_ref(reference,self.mount_aliases)
        choices=[mount for mount in self.image_roots if reference==mount or reference.startswith(mount+'/')]
        if not choices:raise FileNotFoundError('Image namespace not mounted: '+reference)
        mount=max(choices,key=len)
        path=resolved_io_path(path if path is not None else self.image_path(reference,role,slice=slice))
        for base in self.image_roots[mount]:
            if path.is_relative_to(base):
                relative=path.relative_to(base).as_posix();suffix=Path(relative).suffix.lower()
                if suffix not in ('.png','.hdr'):raise ValueError('Image alias lookup requires exact PNG/HDR transport')
                reference=mount+'/'+relative[:-len(suffix)];break
        else:raise ValueError('Resolved image escaped its declared namespace')
        return tuple({'path':str(candidate),'sha256':digest(candidate)} for candidate in self.resolve_all(reference,suffix,image=True))
    def texture_sources(self,reference):
        arcs=self.resolve_all(reference,'.arc-texture.json'); ordinary=self.resolve_all(reference,'.json')
        if len(arcs)>1: self._coalesce(reference,'.arc-texture.json',arcs)
        if arcs: return [('.arc-texture.json',path) for path in arcs]+[('.json',path) for path in ordinary]
        if not ordinary: return []
        selected=self.resolve(reference,'.json')
        return [('.json',selected)]+[('.json',path) for path in ordinary if path!=selected]
    def texture_info(self,reference):
        sources=self.texture_sources(reference)
        if not sources: return {}
        suffix,path=sources[0]; raw=json.loads(path.read_text(encoding='utf-8-sig'))
        if suffix=='.json' and isinstance(raw,Mapping) and 'Data' in raw: return {}
        package=package_ref(reference,self.mount_aliases)
        entries=[entry for entry in _exports(raw) if isinstance(entry,Mapping) and (native_texture_kind(entry) is not None or native_resource_kind(entry) is not None) and entry.get('Package') and package_ref(entry['Package'],self.mount_aliases).casefold()==package.casefold() and str(entry.get('Name','')).casefold()==object_name(reference).casefold()]
        if len(entries)!=1: raise ValueError('Texture metadata identity mismatch: '+str(reference))
        result={**entries[0].get('Properties',{}),'Type':entries[0]['Type'],'source_path':str(path),'source_sha256':digest(path)}
        if native_resource_kind(entries[0]) is not None:result['NativeResourceKind']=native_resource_kind(entries[0])
        else:result['NativeTextureKind']=native_texture_kind(entries[0])
        return result
    def material_sources(self,reference):
        arcs=self.resolve_all(reference,'.arc-material.json'); converted=self.resolve_all(reference,'.json')
        if len(arcs)>1: self._coalesce(reference,'.arc-material.json',arcs)
        if arcs:
            self._is_native_json(arcs[0],reference)
            return [('.arc-material.json',path) for path in arcs]+[('.json',path) for path in converted]
        raw=[path for path in converted if self._is_native_json(path,reference)]
        if len(raw)>1: self._coalesce(reference,'.json',raw)
        ordered=raw+[path for path in converted if path not in raw]
        return [('.json',path) for path in ordered]
    def resolve(self,reference,suffix='.json', *, image=False):
        paths=self.resolve_all(reference,suffix,image=image)
        if not paths: raise FileNotFoundError('No exact resource '+str(reference)+suffix)
        if len(paths)==1: return paths[0]
        if len({digest(path) for path in paths})==1: return self._coalesce(reference,suffix,paths,image)
        if suffix=='.json' and not image:
            sidecar_roots=[]
            for path in paths:
                sidecar=path.with_name(path.name[:-5]+'.arc-material.json')
                if sidecar.is_file() and self._is_native_json(sidecar,reference): sidecar_roots.append(path)
            if len(sidecar_roots)==1: return sidecar_roots[0]
            raw=[path for path in paths if self._is_native_json(path,reference)]
            if not sidecar_roots and len(raw)==1: return raw[0]
        raise AmbiguousAssetError('Ambiguous exact '+('image' if image else 'asset')+' across declared physical roots: '+str(reference)+suffix)


    def record_key(self,record):
        import hashlib
        payload={key:serialize(getattr(record,key)) for key in ('package','master','scalars','colors','textures','switches','settings','scopes','provenance')}
        payload.update(build=self.build,mapping=self.mapping)
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def load_material(self,reference,_chain=()):
        expected=object_name(reference); package=package_ref(reference,self.mount_aliases)
        identity=package+'.'+expected
        if identity.casefold() in _chain: raise ValueError('Material parent cycle: '+identity)
        sources=self.material_sources(reference)
        if not sources: raise FileNotFoundError('No exact material source: '+package)
        if sources[0][0]!='.arc-material.json' and len(sources)>1 and not self._is_native_json(sources[0][1],reference):
            self._coalesce(reference,'.json',tuple(path for _,path in sources))
        suffix,path=sources[0]
        provenance=tuple({'kind':'native-arc' if ext=='.arc-material.json' else 'json','path':str(source),'sha256':digest(source),'selected':i==0,'package':identity,'build':self.build,'mapping':self.mapping} for i,(ext,source) in enumerate(sources))
        record=parse_material(json.loads(path.read_text(encoding='utf-8-sig')),package,provenance,strict_identity=suffix=='.arc-material.json',expected_name=expected,mount_aliases=self.mount_aliases)
        if record.master and record.master.casefold()!=package.casefold(): record=merge_records(self.load_material(record.master,_chain+(identity.casefold(),)),record)
        key='record:'+self.record_key(record)
        if key not in self._cache: self._cache[key]=record
        return self._cache[key]

# Bind this module's source identity at import; overwrites require restart.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
