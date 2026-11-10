"""Pure compact-texture closure receipt validation for owned PAK cache upgrades."""
import hashlib,json,os
from functools import lru_cache
from ..Outfit_Accessories.pak_catalog import canonical_path, filesystem_path, display_path

RECIPE='native-material-and-compact-textures-v2'
RECEIPT_VERSION=2
CLOSURE_SCHEMA='dataraiders.native-material-texture-closure'
CLOSURE_RECIPE='inherited-native-texture-metadata-v1'
TEXTURE_PROJECTION='native-texture-sidecar-v1'
PRODUCER_FILES={'native_parser':('CUE4Parse.Arc','CUE4Parse.Arc.dll'),'texture_conversion':('CUE4Parse.Conversion.Arc','CUE4Parse.Conversion.Arc.dll'),'metadata_cli':('ArcPak.Export','ArcPak.Export.dll')}

@lru_cache(maxsize=512)
def _digest(path,mtime_ns,size):
    with open(path,'rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def digest(path):
    path=filesystem_path(path);stat=os.stat(path);return _digest(path,stat.st_mtime_ns,stat.st_size)

def producer_signature(executable):
    folder=os.path.dirname(os.path.abspath(executable));result={}
    for role,(name,filename) in PRODUCER_FILES.items():
        path=filesystem_path(os.path.join(folder,filename))
        if not os.path.isfile(path):raise FileNotFoundError('Compact metadata producer bundle incomplete: '+filename)
        result[role]={'assembly_name':name,'sha256':digest(path)}
    return result

def metadata_revision(source_revision,producer):
    if not source_revision or not producer:raise ValueError('Current source and producer revision required')
    record={'recipe':RECIPE,'texture_projection':TEXTURE_PROJECTION,'texture_schema_version':1,'source_revision':source_revision,'producer_assemblies':producer}
    return RECIPE+':'+hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()

def producer_matches(actual,expected):
    return isinstance(actual,dict) and all(isinstance(actual.get(role),dict) and actual[role].get('assembly_name')==item['assembly_name'] and actual[role].get('sha256')==item['sha256'] for role,item in expected.items())

def _json(path):
    with open(filesystem_path(path),encoding='utf-8-sig') as stream:return json.load(stream)

def closure_dependencies(material_sidecar,raw_dependencies,build,depot,producer,mapping_sha=''):
    """Return owned exact manifest+compact facts, or None when a refresh is needed.

    No image files are loaded, read, decoded, rewritten or directory-searched.
    Every dependency path is declared by the trusted current native producer.
    """
    sidecar=filesystem_path(material_sidecar);manifest_path=filesystem_path(display_path(sidecar)+'.textures.json')
    try:manifest=_json(manifest_path)
    except (FileNotFoundError,ValueError):return None
    if not isinstance(manifest,dict) or manifest.get('schema')!=CLOSURE_SCHEMA or manifest.get('version')!=1 or manifest.get('recipe')!=CLOSURE_RECIPE:return None
    if manifest.get('texture_projection')!=TEXTURE_PROJECTION or manifest.get('texture_schema_version')!=1:return None
    if (build and manifest.get('build_id')!=build) or (depot and manifest.get('depot_manifest')!=depot):return None
    if mapping_sha and manifest.get('mapping_sha256')!=mapping_sha:return None
    if not producer_matches(manifest.get('producer_assemblies'),producer):return None
    raw_root=_json(raw_dependencies[0]);expected=canonical_path(raw_root['DataRaidersSource']['package'])
    if canonical_path(manifest.get('material_package','')).casefold()!=expected.casefold():raise ValueError('Native texture closure material identity mismatch')
    if canonical_path(manifest.get('material_object_path','')).casefold()!=expected.casefold():raise ValueError('Native texture closure object identity mismatch')
    physical=os.path.abspath(display_path(sidecar)).replace('\\','/');marker=physical.casefold().find('/content/pioneer/')
    # A mounted-plugin root (EmbarkScript template) sits at <cache>/<package>.arc-material.json.
    suffix='/'+expected+'.arc-material.json'
    if marker>=0:cache_root=os.path.dirname(physical[:marker])
    elif '/content/' not in expected.casefold() and physical.casefold().endswith(suffix.casefold()):cache_root=physical[:-len(suffix)]
    else:raise ValueError('Compact metadata has no exact owned native material mount')
    resolved_root=os.path.normcase(os.path.realpath(cache_root))
    entries=manifest.get('dependencies')
    if not isinstance(entries,list) or len(entries)>16384:raise ValueError('Native texture metadata closure budget/schema invalid')
    result=[manifest_path];packages=set();seen=set()
    for entry in entries:
        if not isinstance(entry,dict):raise ValueError('Malformed native texture metadata dependency')
        relative=entry.get('relative_path','')
        if not isinstance(relative,str) or not relative.endswith('.arc-texture.json') or os.path.isabs(relative) or any(part in ('','.','..') for part in relative.replace('\\','/').split('/')):raise ValueError('Unsafe native texture metadata dependency path')
        path=filesystem_path(os.path.join(cache_root,relative.replace('/',os.sep)))
        resolved=os.path.normcase(os.path.realpath(display_path(path)))
        if os.path.commonpath((resolved_root,resolved))!=resolved_root:raise ValueError('Native texture metadata escaped owned cache')
        if resolved in seen:raise ValueError('Duplicate compact metadata dependency path')
        seen.add(resolved)
        try:
            if digest(path)!=entry.get('sha256'):return None
            fact=_json(path)
        except (FileNotFoundError,ValueError):return None
        if not isinstance(fact,dict) or not isinstance(fact.get('Type'),str):return None
        package=canonical_path(entry.get('package',''))
        if canonical_path(fact.get('Package','')).casefold()!=package.casefold():raise ValueError('Exact compact texture package mismatch')
        if str(fact.get('Name','')).casefold()!=package.rsplit('/',1)[-1].casefold():raise ValueError('Exact compact texture name mismatch')
        source=fact.get('DataRaidersSource',{})
        if source.get('projection')!=TEXTURE_PROJECTION or source.get('schema_version')!=1:return None
        if not producer_matches(source.get('producer_assemblies'),producer):return None
        if (build and source.get('build')!=build) or (depot and source.get('depot_manifest')!=depot):return None
        if source.get('mapping_sha256')!=manifest.get('mapping_sha256') or (mapping_sha and source.get('mapping_sha256')!=mapping_sha):return None
        packages.add(package.casefold());result.append(path)
    # Verify every explicit serialized parameter/default/reference in the raw
    # parent closure is covered. Fixed native references are also captured by
    # CUE's non-rendered inherited reference enumeration and bound manifest.
    required=set()
    def reference(value):
        # Runtime virtual textures are world RVT assets, not Texture sidecars; the producer never emits them.
        if isinstance(value,dict) and str(value.get('ObjectName','')).startswith("RuntimeVirtualTexture'"):return
        if isinstance(value,str) and value.startswith("RuntimeVirtualTexture'"):return
        if isinstance(value,dict):value=value.get('ObjectPath') or value.get('AssetPathName') or ''
        if not isinstance(value,str) or not value:return
        if "'" in value:value=value.split("'",1)[1].rstrip("'")
        required.add(canonical_path(value).casefold())
    def walk(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key=='TextureParameterValues' and isinstance(item,list):
                    for parameter in item:
                        if isinstance(parameter,dict):reference(parameter.get('ParameterValue'))
                elif key in ('TextureValues','ReferencedTextures') and isinstance(item,list):
                    for texture in item:reference(texture)
                else:walk(item)
        elif isinstance(value,list):
            for item in value:walk(item)
    for path in raw_dependencies:
        raw=_json(path)
        for export in raw.get('Exports',[]):walk(export)
    if not required.issubset(packages):return None
    return result
