def _benchmark_root():
    import os
    from pathlib import Path
    value = os.environ.get("DATARAIDERS_BENCHMARK_ROOT", r"Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/benchmark-111-20261028")
    root = Path(value).resolve()
    boundary = Path(r"Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local").resolve()
    if not root.is_relative_to(boundary):
        raise ValueError("Benchmark output must remain under the designated Z: scratch root")
    return root

import bpy,sys,json,time,traceback,importlib,hashlib,os
from pathlib import Path
from mathutils import Vector
T=_benchmark_root()
M=json.loads((T/'benchmark-manifest.json').read_text())
EXPORT=T/'exports'; PIONEER=EXPORT/'PioneerGame/Content/Pioneer'
SIDE=globals().get('SIDE','A')
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.context.preferences.filepaths.save_version=0
bpy.context.scene.render.threads_mode='FIXED';bpy.context.scene.render.threads=4
if SIDE=='A':
 sys.path.insert(0,r'C:/Users/Silarious/AppData/Roaming/Blender Foundation/Blender/5.1/scripts/addons')
 addon=importlib.import_module('Arc-Asset-Importer'); addon.register()
 scene=bpy.context.scene
 scene.arc_pioneer_root=str(PIONEER);scene.arc_pak_dir='';scene['arc_pak_dir_user_cleared']=True;scene.arc_usmap_path=M['usmap']
 addon.utils._pak_cache_roots[:]=[str(EXPORT)]
 scene.arc_outfit_color_pipeline='GROUND_TRUTH';scene.arc_include_reference=False
 appearance=importlib.import_module(addon.__name__+'.Outfit_Accessories.appearance')
else:
 sys.path.insert(0,r'C:/Users/Silarious/Downloads/BETA-ARC_Texturer_Revamped_1.11.0')
 addon=importlib.import_module('arc_texturer_revamped');addon.register()
 bpy.context.scene.arc_revamped.export_root=str(PIONEER)
 index=addon.get_index(PIONEER,refresh=True)
 batch=importlib.import_module(addon.__name__+'.batch')
report={'side':SIDE,'plugin_version':addon.bl_info['version'],'plugin_path':addon.__file__,'blender':bpy.app.version_string,'steam_build':M['build'],'usmap':M['usmap'],'groups':[],'all_errors':[],'format':'UEModel' if SIDE=='A' else 'ActorX','limits':['Native UEModel versus ActorX product import; geometry metrics recorded independently.','Each case imported in its own scene to isolate body masks and appearance state.','No pose action, compositor, grading or shader adjustment.']}

def dump():
 (T/f'report-{SIDE}.json').write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')

def source(package):
 for ext in (['.uemodel'] if SIDE=='A' else ['.psk','.pskx']):
  p=EXPORT/(package+ext)
  if p.is_file():return p
 raise FileNotFoundError(package)

def material_bindings(package):
 folder=(EXPORT/package).parent; out={}
 for p in sorted(folder.glob('DA_VP_*.json')):
  d=json.loads(p.read_text(encoding='utf-8-sig'));rows=d.get('Exports',[]) if isinstance(d,dict) else d
  for row in rows:
   props=row.get('Properties',{})
   for value in props.get('MaterialOverrides',[]):
    ref=value.get('Value',{});ref=ref.get('ObjectPath') or ref.get('AssetPathName') if isinstance(ref,dict) else ref
    if ref:out[value['Key']]=str(ref).split('.')[0]
 return out

def local_ref(ref):
 s=str(ref).split('.')[0].replace('/Game/','PioneerGame/Content/').lstrip('/')
 p=EXPORT/(s+'.json');return str(p) if p.is_file() else ''

def nodes_recursive(tree,seen=None):
 seen=seen or set()
 if not tree or tree.as_pointer() in seen:return []
 seen.add(tree.as_pointer());out=list(tree.nodes)
 for n in tree.nodes:
  if n.type=='GROUP' and n.node_tree:out+=nodes_recursive(n.node_tree,seen)
 return out

def inspect_obj(obj):
 item={'name':obj.name,'type':obj.type,'location':list(obj.location),'scale':list(obj.scale),'matrix_world':[list(x) for x in obj.matrix_world]}
 if obj.type!='MESH':return item
 corners=[obj.matrix_world@Vector(c) for c in obj.bound_box]
 lo=[min(v[i] for v in corners) for i in range(3)];hi=[max(v[i] for v in corners) for i in range(3)]
 item.update(vertices=len(obj.data.vertices),polygons=len(obj.data.polygons),triangles=sum(len(p.vertices)-2 for p in obj.data.polygons),bounds_min=lo,bounds_max=hi,dimensions=[b-a for a,b in zip(lo,hi)],uv_layers=[u.name for u in obj.data.uv_layers],color_attributes=[u.name for u in obj.data.color_attributes],attributes=[(a.name,a.domain,a.data_type) for a in obj.data.attributes],shape_keys=[(k.name,k.value) for k in obj.data.shape_keys.key_blocks] if obj.data.shape_keys else [],properties={k:str(obj[k]) for k in obj.keys() if k.startswith(('arc_','dr_'))},slots=[])
 for i,slot in enumerate(obj.material_slots):
  mat=slot.material
  if not mat:item['slots'].append({'slot':i,'missing_material':True});continue
  nodes=nodes_recursive(mat.node_tree);images=[];warnings=[]
  for n in nodes:
   if n.type!='TEX_IMAGE':continue
   image=n.image
   if not image:images.append({'node':n.name,'missing_image':True});continue
   path=bpy.path.abspath(image.filepath)
   images.append({'name':image.name,'path':path,'exists':os.path.isfile(path) or bool(image.packed_file),'size':list(image.size),'colorspace':image.colorspace_settings.name})
  principled=[]
  for n in nodes:
   if n.type=='BSDF_PRINCIPLED':
    p={}
    for key in ['Base Color','Roughness','Metallic','Alpha','Emission Color','Emission Strength']:
     sock=n.inputs.get(key)
     if sock:p[key]={'linked':sock.is_linked,'value':list(sock.default_value) if hasattr(sock.default_value,'__len__') else sock.default_value}
    principled.append(p)
  outputs=[n for n in mat.node_tree.nodes if n.type=='OUTPUT_MATERIAL'] if mat.node_tree else []
  item['slots'].append({'slot':i,'name':mat.name,'polygons':sum(p.material_index==i for p in obj.data.polygons),'use_nodes':mat.use_nodes,'surface_connected':any(n.inputs['Surface'].is_linked for n in outputs),'node_count':len(nodes),'images':images,'principled':principled,'properties':{k:str(mat[k]) for k in mat.keys() if k.startswith(('arc_','dr_','gt_'))}})
 return item

# Match default visual-part material intent; each addon still resolves/builds its own graph.
for gi,g in enumerate(M['groups']):
 import_scene=bpy.data.scenes.new('_IMPORT_'+SIDE+'_'+g['id']);bpy.context.window.scene=import_scene
 if SIDE=='A':
  import_scene.arc_pioneer_root=str(PIONEER);import_scene.arc_pak_dir='';import_scene['arc_pak_dir_user_cleared']=True;import_scene.arc_usmap_path=M['usmap'];import_scene.arc_include_reference=False;import_scene.arc_outfit_color_pipeline='GROUND_TRUTH'
 else:import_scene.arc_revamped.export_root=str(PIONEER)
 started=time.perf_counter();entry={'id':g['id'],'label':g['label'],'imports':[],'errors':[],'material_reports':[]}
 collection=bpy.data.collections.new(f'{SIDE}__{g["id"]}');bpy.context.scene.collection.children.link(collection)
 collection['benchmark_side']=SIDE;collection['benchmark_group']=g['id'];collection['label']=g['label']
 for package in g['packages']:
  before=set(bpy.data.objects)
  try:
   print('BENCHMARK_MESH_BEGIN',SIDE,g['id'],package,flush=True)
   path=source(package);bindings=material_bindings(package)
   if SIDE=='A':
    kwargs={'body_variant':'female_white' if g['id']=='02_body' else 'NONE','material_bindings':bindings}
    ok,msg,objects=appearance.import_appearance_mesh(str(path),**kwargs)
    if not ok:raise RuntimeError(msg)
    entry['imports'].append({'package':package,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'message':msg,'bindings':bindings})
   else:
    selector=None
    if bindings:
     def selector(lib,obj,slot,record,bindings=bindings):
      from arc_texturer_revamped.characters import CharacterPlan
      rows=[{'slot':key,'material':ref} for key,ref in bindings.items()]
      selected=CharacterPlan._match(rows,lib,obj,slot,record)
      return lib.load(selected) if selected else record
    if g['id']=='02_body':
     def selector(lib,obj,slot,record):return dict(lib.load('/Game/Pioneer/Characters/Shared/Bodies/MI_Skin_Female'),family='body_skin',body_gender='FEMALE')
    rows,objects,count=batch.import_paths([path],bpy.context,index,use_skins=True,material_selector=selector)
    if g['id']=='02_body':
     from arc_texturer_revamped.deformation import set_gender
     for object_to_morph in objects:set_gender(object_to_morph,'FEMALE')
    entry['material_reports']+=rows
    entry['imports'].append({'package':package,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'file_count':count,'bindings':bindings})
    if not objects:raise RuntimeError('No mesh objects from product import')
  except Exception as exc:
   error={'package':package,'error':str(exc),'traceback':traceback.format_exc()};entry['errors'].append(error);report['all_errors'].append(error);print('BENCHMARK_IMPORT_ERROR',SIDE,g['id'],str(exc),flush=True)
  new=list(set(bpy.data.objects)-before)
  for obj in new:
   for c in list(obj.users_collection):c.objects.unlink(obj)
   collection.objects.link(obj);obj['benchmark_side']=SIDE;obj['benchmark_group']=g['id'];obj['benchmark_source_package']=package
 bpy.context.view_layer.update()
 entry['objects']=[inspect_obj(o) for o in collection.objects]
 entry['seconds']=time.perf_counter()-started;report['groups'].append(entry);dump()
 print('BENCHMARK_PROGRESS',SIDE,gi+1,len(M['groups']),g['id'],'errors',len(entry['errors']),flush=True)
 if (gi+1)%5==0:bpy.ops.wm.save_as_mainfile(filepath=str(T/f'import-{SIDE}.blend'))
# Prefix datablocks so appending A and B cannot accidentally identify graphs by display name.
for blocks in [bpy.data.materials,bpy.data.node_groups,bpy.data.meshes,bpy.data.armatures,bpy.data.images]:
 for data in blocks:
  if not data.name.startswith(SIDE+'::'):data.name=SIDE+'::'+data.name
for obj in bpy.data.objects:obj.name=SIDE+'::'+obj.name
for text in bpy.data.texts:
 if text.name not in ['benchmark report']:text.name=SIDE+'::'+text.name
text=bpy.data.texts.new(SIDE+'::benchmark report');text.write(json.dumps(report,indent=2,default=str))
bpy.ops.wm.save_as_mainfile(filepath=str(T/f'import-{SIDE}.blend'))
report['completed']=True;dump();print('BENCHMARK_SIDE_COMPLETE',SIDE,flush=True)


