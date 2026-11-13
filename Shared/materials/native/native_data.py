"""Reviewed current-native metadata views for legacy-shaped compatibility APIs.
No1.42 material/texture banks are opened. A flat export remains explicitly
source/namespace-unverified; catalog facts do not certify its image association.
"""
from pathlib import Path
from threading import RLock
import json,sys,os
from .loaded_identity import require_loaded_identity
from .catalog import NativeCatalog
from .repository import digest,package_ref,invalidate_digest_cache
from .core import serialize
ACTIVATION_PATH=Path(__file__).parents[1]/'rebuild/data/native_catalog_active.json'
_CACHE={};_LOCK=RLock()

def _file_identity(path):
 """Include write-change time, not Windows creation time, in reuse proofs."""
 with path.open('rb') as stream:
  stat=os.fstat(stream.fileno());changed=stat.st_ctime_ns
  if os.name=='nt':
   import ctypes,msvcrt
   class FileBasicInfo(ctypes.Structure):
    _fields_=[('creation',ctypes.c_longlong),('access',ctypes.c_longlong),('write',ctypes.c_longlong),('change',ctypes.c_longlong),('attributes',ctypes.c_ulong)]
   query=ctypes.WinDLL('kernel32',use_last_error=True).GetFileInformationByHandleEx
   query.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_ulong];query.restype=ctypes.c_int
   info=FileBasicInfo()
   if not query(msvcrt.get_osfhandle(stream.fileno()),0,ctypes.byref(info),ctypes.sizeof(info)):return None
   changed=info.change
  if not stat.st_ino or not changed:return None
  return (str(path.resolve()),stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,changed)

def reviewed_source(manifest=None):
 require_loaded_identity(sys.modules[__name__])
 manifest=Path(ACTIVATION_PATH if manifest is None else manifest)
 if not manifest.is_file():return None
 with _LOCK:
  manifest_identity=_file_identity(manifest)
  if manifest_identity is not None:
   for (previous_manifest,previous_bank),previous in _CACHE.items():
    if previous_manifest==manifest_identity:
     bank=(manifest.parent/previous['config']['catalog']).resolve()
     if not bank.is_relative_to(manifest.parent.resolve()):raise ValueError('Native bank escaped activation directory')
     if previous_bank==_file_identity(bank) and manifest_identity==_file_identity(manifest):return previous
  _CACHE.clear()
  config=json.loads(manifest.read_text(encoding='utf-8-sig'));bank=(manifest.parent/config['catalog']).resolve()
  if not bank.is_relative_to(manifest.parent.resolve()):raise ValueError('Native bank escaped activation directory')
  bank_identity=_file_identity(bank)
  invalidate_digest_cache(manifest);invalidate_digest_cache(bank)
  identity=(str(manifest.resolve()),digest(manifest),digest(bank))
  if identity[2]!=config['sha256']:raise ValueError('Reviewed native bank SHA mismatch')
  from ..rebuild.native_catalog_bridge import SCHEMA,LIMITED_SCHEMA,reviewed_limits
  catalog=NativeCatalog.read(bank);payload=catalog.payload;stats=payload.get('statistics',{})
  if config.get('schema')==LIMITED_SCHEMA and config.get('reviewed_source_complete') is True:limits=reviewed_limits(catalog,config)
  elif config.get('schema')==SCHEMA and config.get('reviewed_complete') is True and not payload.get('errors') and stats.get('error_count')==0:limits={}
  else:raise ValueError('Current-native metadata requires an explicitly reviewed activation manifest')
  for field in ('build','mapping'):
   if config.get(field)!=payload[field]:raise ValueError('Native bank context mismatch: '+field)
  for field,key in (('expected_material_records','records'),('expected_texture_metadata','textures')):
   if config.get(field)!=len(payload[key]):raise ValueError('Native bank coverage review mismatch: '+field)
  if manifest_identity!=_file_identity(manifest) or bank_identity!=_file_identity(bank):raise ValueError('Reviewed native source changed during validation')
  result={'catalog':catalog,'config':config,'limits':limits,'identity':identity,'provenance':{'kind':'reviewed-current-native-bank','manifest':str(manifest),'manifest_sha256':identity[1],'bank':str(bank),'bank_sha256':identity[2],'build':payload['build'],'mapping':payload['mapping']}}
  if manifest_identity is not None and bank_identity is not None:_CACHE[(manifest_identity,bank_identity)]=result
  return result

def current_metadata(source=None):
 source=reviewed_source() if source is None else source
 if source is None:return {'materials':{},'textures':{},'provenance':{'kind':'local-export-only','diagnostic':'No reviewed current-native bank activated; local FModel observations only'}}
 if 'metadata' in source:return source['metadata']
 from ..rebuild.assets import package
 materials={}
 for key,entry in source['catalog'].payload['records'].items():
  facts=serialize(entry['facts']);full=facts['package'];parent=facts['parent'];record={'package':package(full),'parent':'' if parent.casefold()==full.casefold() else package(parent),'name':full.rsplit('/',1)[-1]}
  for kind in ('textures','scalars','switches','settings'):record[kind]=facts[kind]
  record['colors']={name:tuple(value) for name,value in facts['colors'].items()}
  for normal,native in (('blend','BlendMode'),('two_sided','TwoSided'),('cutoff','OpacityMaskClipValue'),('dither_opacity_mask','DitherOpacityMask')):
   if normal in record['settings'] and native not in record['settings']:record['settings'][native]=record['settings'][normal]
  for scope in facts['scopes']:
   prefix=('Layer' if 'Layer' in scope['association'] else 'Blend')+str(scope['index'])+'.'
   for kind,values in scope['parameters'].items():record[kind].update({prefix+name:value for name,value in values.items()})
  record['native_fact_sources']=[{**source['provenance'],'package':full,'source':serialize(entry['source']),'facts_sha256':entry['facts_sha256'],'semantic_sha256':entry['semantic_sha256']}]
  if full.casefold() in source['limits']:record['_native_render_limit']=list(source['limits'][full.casefold()])
  materials[package(full).casefold()]=record
 textures={package(ref).casefold():{**serialize(fact['properties']),'Type':fact['type'],'NativeTextureKind':fact.get('native_texture_kind'),'NativeResourceKind':fact.get('resource_kind'),'source':serialize(fact['source']),**({'ArrayLayerCount':fact['slice_count'],'Mips':[{'SizeZ':fact['slice_count']}]} if 'slice_count' in fact else {})} for ref,fact in source['catalog'].payload['textures'].items()}
 source['metadata']={'materials':materials,'textures':textures,'provenance':source['provenance']};return source['metadata']

def current_presets():
 source=reviewed_source()
 if source is None:return {}
 if 'presets' in source:return source['presets']
 from ..rebuild.assets import package
 data=current_metadata()['materials'];prefix='/Game/Pioneer/MaterialLibrary/M_Presets/';result={}
 def merged(ref,chain=()):
  key=package(ref).casefold()
  if key in chain:raise ValueError('Native preset parent cycle')
  record=data.get(key)
  if record is None:raise ValueError('Native preset exact parent missing: '+str(ref))
  if record.get('_native_render_limit'):
   from ..rebuild.native_catalog_bridge import NativeRenderLimitationError
   raise NativeRenderLimitationError('Native preset expression bindings unresolved: '+str(ref))
  value={kind:{} for kind in ('scalars','colors','switches','textures')}
  if record['parent']:value=merged(record['parent'],chain+(key,))
  for kind in value:value[kind].update(record[kind])
  return value
 for full,entry in source['catalog'].payload['records'].items():
  if not full.startswith(prefix) or '/' in full[len(prefix):]:continue
  name=full[len(prefix):]
  if full.casefold() in source['limits']:
   result[name]={'name':name,'package':full,'_native_render_limit':list(source['limits'][full.casefold()]),'source_provenance':source['provenance']};continue
  value=merged(full);projection=entry.get('uniform_projection',{});references=list(value['textures'].values())+[item['reference'] for item in projection.get('constant_textures',())];references=list(dict.fromkeys(ref for ref in references if ref is not None));result[name]={'name':name,'package':full,'scalars':value['scalars'],'vectors':value['colors'],'switches':value['switches'],'texture_params':list(value['textures']),'texture_defaults':value['textures'],'referenced_textures':references,'source_provenance':{**source['provenance'],'source':serialize(entry['source']),'facts_sha256':entry['facts_sha256']}}
 source['presets']=result;return result


def compatibility_signature(record,index):
 """Source-associated flat-export cache identity; never a native-exact stamp."""
 observations=[]
 for ref in sorted(set(value for value in record.get('textures',{}).values() if value)):
  try:
   info=index.texture_info(ref) if hasattr(index,'texture_info') else {};depth=info.get('ArrayLayerCount')
   if depth is None and info.get('Type')=='Texture2DArray':
    counts=[mip['SizeZ'] for mip in info.get('Mips',()) if 'SizeZ' in mip];depth=counts[0] if counts and len(set(counts))==1 else None
   slices=range(int(depth)) if depth is not None else (None,)
   for layer in slices:
    path=index.texture(ref,layer) if layer is not None else index.texture(ref)
    observations.append({'reference':ref,'slice':layer,'path':str(path) if path is not None else None,'sha256':digest(path) if path is not None else None,'metadata':info})
  except (ValueError,FileNotFoundError) as error:observations.append({'reference':ref,'unresolved':str(error)})
 for source in record.get('compatibility_sources',()):
  entry=dict(source)
  if entry.get('path') and Path(entry['path']).is_file():entry['sha256']=digest(entry['path'])
  observations.append(entry)
 return {'mode':'fmodel-selected-root-compatibility','native_exact':False,'observations':observations,'recipe':'current-native-data-flat-v1'}


def guard_known_source(source_path,parsed=None):
 """Refuse known unresolved native hierarchies before shared graph allocation."""
 source=reviewed_source()
 if source is None:return None
 refs=[];parsed=parsed or {}
 for field in ('parent_object_path','parent','package'):
  value=parsed.get(field)
  if value:
   text=str(value)
   if '/' not in text and text.startswith('M_'):text='/Game/Pioneer/MaterialLibrary/M_Presets/'+text
   try:refs.append(package_ref(text))
   except ValueError:pass
 if source_path:
  path=Path(source_path);parts=list(path.parts)
  for i in range(len(parts)-1):
   if parts[i].casefold() in ('pioneergame','engine','remappedplugins') and parts[i+1].casefold()=='content':
    try:refs.append(package_ref('/'.join(parts[i:]).removesuffix('.json').removesuffix('.arc-material')))
    except ValueError:pass
  if path.is_file():
   from .repository import _exports
   raw=json.loads(path.read_text(encoding='utf-8-sig'))
   for entry in _exports(raw):
    if not isinstance(entry,dict):continue
    for value in (entry.get('Package'),entry.get('Properties',{}).get('Parent')):
     if value:
      try:refs.append(package_ref(value))
      except ValueError:pass
 for ref in refs:
  if ref.casefold() in source['limits']:
   from ..rebuild.native_catalog_bridge import NativeRenderLimitationError
   raise NativeRenderLimitationError('Current native expression bindings unresolved for source/hierarchy: '+ref)
 return source['provenance']

from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
