"""Collection-only organization and readable names for imported assets."""
from __future__ import annotations
import json
import csv
import os
import re
import bpy
from bpy.types import Operator

MODE_KEY = 'arc_asset_grouping'
COLLECTION_KEY = 'arc_asset_collection'
LABELS = {'Body':'Bodies','Head':'Heads','Hair':'Hair','FacialHair':'Facial Hair',
          'Outfit':'Outfits','Backpack':'Backpacks','BackpackFrame':'Backpack Frames',
          'BackpackStrap':'Backpack Straps','Attachment':'Attachments','Charm':'Charms',
          'CharmConnector':'Charm Connectors','RaiderTool':'Raider Tools',
          'Weapon':'Weapons','WeaponMod':'Weapon Mods','Prop':'Props',
          'Enemies':'Enemies','Environment':'Environment'}


_NAME_CATALOG_CACHE = {}
_ENEMY_FLAVOURS = {
    'BullCrab': 'Leaper', 'Bastion': 'Bastion', 'Chonk': 'Bastion',
    'Monk': 'Bombardier', 'Peppermint': 'Queen', 'Spearmint': 'Matriarch',
    'RollBot': 'Surveyor', 'Sniper': 'Sentinel', 'Snitch': 'Snitch',
    'Runner': 'Shredder', 'LightDrone_02': 'Wasp', 'LightDroneElite': 'Hornet',
    'Firefly': 'Firefly', 'Pop': 'Pop', 'Tick': 'Tick', 'Fireball': 'Fireball',
}


def _source_path(obj):
    return str(obj.get('arc_source_mesh') or obj.get('arc_psk_path') or '').replace('\\', '/')


def _folder_category(obj):
    path = '/' + _source_path(obj).strip('/') + '/'
    low = path.casefold()
    if '/enemies/' in low:
        return 'Enemies'
    if '/environment/' in low:
        return 'Environment'
    if '/pioneer/' in low:
        tail = path[low.index('/pioneer/') + len('/pioneer/'):]
        category = tail.split('/')[0]
        if category and category.casefold() not in {'characters','items','shared'}:
            return category
    return ''


def _catalog_flavour(obj, role, fallback):
    explicit = str(obj.get('arc_asset_flavour') or '').strip()
    if explicit:
        return explicit
    if role == 'Enemies':
        stem = re.sub(r'^(SK|SM)_', '', source_code_name(obj), flags=re.I)
        for code, label in sorted(_ENEMY_FLAVOURS.items(), key=lambda pair: -len(pair[0])):
            if stem.casefold() == code.casefold() or stem.casefold().startswith(code.casefold() + '_'):
                return label
    if role == 'Body' and obj.get('arc_body_variant') not in (None, '', 'NONE'):
        return fallback
    source = _source_path(obj).casefold()
    stem = source_code_name(obj).casefold()
    for filename in ('appearance_reference.csv', 'outfit_reference.csv'):
        path = os.path.join(os.path.dirname(__file__), filename)
        try:
            stamp = os.stat(path).st_mtime_ns
            cached = _NAME_CATALOG_CACHE.get(path)
            if not cached or cached[0] != stamp:
                with open(path, encoding='utf-8-sig', newline='') as stream:
                    rows = list(csv.DictReader(stream))
                _NAME_CATALOG_CACHE[path] = (stamp, rows)
            rows = _NAME_CATALOG_CACHE[path][1]
        except OSError:
            continue
        for row in rows:
            if filename == 'appearance_reference.csv':
                candidate = os.path.splitext((row.get('rel_path') or '').replace('\\','/').rsplit('/',1)[-1])[0]
                if candidate.casefold() == stem:
                    label = (row.get('label') or '').strip()
                    return re.sub(r'^Facial\s*[^A-Za-z0-9]*\s*', '', label) or fallback
            else:
                folders = (row.get('Model Folder Name') or '').split(';')
                if any('/' + folder.strip().casefold() + '/' in source for folder in folders if folder.strip()):
                    return (row.get('Flavour') or '').strip() or fallback
    return fallback


def source_display_names(paths):
    """Snapshot display catalogs once for a whole browser build (no Blender RNA)."""
    appearance_names = {}
    outfit_names = []
    for filename in ('appearance_reference.csv', 'outfit_reference.csv'):
        try:
            with open(os.path.join(os.path.dirname(__file__), filename), encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
        except OSError:
            continue
        for row in rows:
            if filename == 'appearance_reference.csv':
                code = os.path.splitext((row.get('rel_path') or '').replace('\\', '/').rsplit('/', 1)[-1])[0]
                label = re.sub(r'^Facial\s*[^A-Za-z0-9]*\s*', '', row.get('label') or '').strip()
                if code and label:
                    appearance_names.setdefault(code.casefold(), label)
            else:
                label = (row.get('Flavour') or '').strip()
                if label:
                    outfit_names.extend(('/' + folder.strip().casefold() + '/', label)
                                        for folder in (row.get('Model Folder Name') or '').split(';') if folder.strip())
    enemies = sorted(_ENEMY_FLAVOURS.items(), key=lambda pair: -len(pair[0]))
    result = {}
    for path in paths:
        code = path.rsplit('/', 1)[-1]
        stem = re.sub(r'^(SK|SM)_', '', code, flags=re.I)
        low = '/' + path.casefold().strip('/') + '/'
        label = ''
        if '/enemies/' in low:
            label = next((value for key, value in enemies
                          if stem.casefold() == key.casefold() or stem.casefold().startswith(key.casefold() + '_')), '')
        label = label or appearance_names.get(code.casefold(), '')
        if not label:
            label = next((value for folder, value in outfit_names if folder in low), '')
        result[path] = label or stem
    return result


def role_for(obj):
    from . import import_layout
    if obj.get('arc_reference_plane'):
        from .reference_planes import _reference_role
        return _reference_role(obj)
    marked = any(obj.get(k) for k in ('arc_assembly_role','arc_model_type','arc_source_mesh','arc_psk_path','arc_outfit','arc_bridge_source'))
    if marked:
        category = _folder_category(obj)
        if category in {'Enemies', 'Environment'}:
            return category
        stamped = str(obj.get('arc_assembly_role') or '')
        if stamped in LABELS:return stamped
        role = import_layout.infer_role(obj)
        if role:return role
        model = str(obj.get('arc_model_type') or '').casefold()
        path = str(obj.get('arc_source_mesh') or obj.get('arc_psk_path') or '').replace('\\','/').casefold()
        if model == 'weapon' or '/weapons/' in path:return 'Weapon'
        if '/weaponmod/' in path:return 'WeaponMod'
        return category or 'Prop'
    if obj.type in {'EMPTY','ARMATURE'}:
        roles = {role_for(o) for o in obj.children if o.type == 'MESH'} - {''}
        if len(roles)==1:return next(iter(roles))
        if roles:return 'Outfit'
    return ''


def slot_for(obj):
    from .import_layout import slot_of_object
    current = obj
    while current is not None:
        slot = slot_of_object(current)
        if slot is not None:return slot
        current = current.parent
    return None


def _child(parent, key, label):
    for col in parent.children:
        if col.get(COLLECTION_KEY)==key:return col
    col=bpy.data.collections.new(label);col[COLLECTION_KEY]=key;parent.children.link(col)
    return col


def root_collection(scene):
    return _child(scene.collection, 'ROOT', 'Arc Assets')


def collection_for_object(obj, scene=None):
    scene=scene or bpy.context.scene;role=role_for(obj)
    if not role:return None
    root=root_collection(scene);mode=scene.get(MODE_KEY,'TYPE')
    if mode=='SLOT':
        slot=slot_for(obj);key='UNASSIGNED' if slot is None else str(slot)
        label='Unassigned Assets' if slot is None else f'Outfit Slot {slot+1:03d}'
        col=_child(root,'SLOT:'+key,label)
    else:
        col=_child(root,'TYPE:'+role,LABELS.get(role,role))
    if obj.get('arc_reference_plane'):
        col=_child(col,'REFERENCES',col.name+' References')
    return col


def _source_stem(obj):
    if obj.get('arc_reference_plane'):
        image=str(obj.get('arc_reference_image') or '').replace('\\','/')
        stem=os.path.splitext(image.rsplit('/',1)[-1])[0]
        if stem.startswith('T_UI_Icon_'):
            stem=stem[len('T_UI_Icon_'):]
            return stem if role_for(obj)=='Head' else stem.partition('_')[2] or stem
        anchor=obj.parent or bpy.data.objects.get(str(obj.get('arc_reference_anchor') or ''))
        return _source_stem(anchor) if anchor is not None else stem
    if obj.type=='ARMATURE':
        raw=str(obj.get('arc_source_object_name') or obj.name)
    else:
        raw=str(obj.get('arc_source_mesh') or obj.get('arc_psk_path') or obj.get('arc_source_object_name') or obj.name)
    stem=raw.replace('\\','/').rsplit('/',1)[-1]
    stem=re.sub(r'\.\d{3,}$','',stem)
    return os.path.splitext(stem)[0]


def source_code_name(obj):
    """Retain the exact exported asset identifier independently of readable labels."""
    if obj.get('arc_reference_plane'):
        raw = str(obj.get('arc_reference_image') or '')
        if not raw:
            anchor = obj.parent or bpy.data.objects.get(str(obj.get('arc_reference_anchor') or ''))
            return source_code_name(anchor) if anchor is not None else 'Unknown'
    else:
        raw = str(obj.get('arc_source_mesh') or obj.get('arc_psk_path') or
                  obj.get('arc_source_object_name') or obj.name)
    stem = raw.replace('\\', '/').rsplit('/', 1)[-1]
    stem = re.sub(r'\.\d{3,}$', '', stem)
    stem = os.path.splitext(stem)[0]
    return stem or 'Unknown'


def standard_name(obj, role):
    stem=re.sub(r'^(?:SK|SM)_','',_source_stem(obj),flags=re.I)
    # Remove only redundant type wrappers, retaining authored part identifiers.
    aliases={'Head':['Head_'],'Hair':['Hair_'],'FacialHair':['FacialHair_'],
             'BackpackFrame':['Backpack_Frame_','BackpackFrame_'],
             'BackpackStrap':['BackpackStrap_'],'Charm':['BackpackCharm_','Charm_'],
             'CharmConnector':['BackpackCharm_','CharmConnector_'],
             'RaiderTool':['RaiderTool_'],'Weapon':['Weapon_'],'WeaponMod':['WeaponMod_']}
    for prefix in [role+'_',*aliases.get(role,[])]:
        if stem.casefold().startswith(prefix.casefold()):stem=stem[len(prefix):];break
    if role=='Head':stem=re.sub(r'_Pio_Base$','',stem,flags=re.I)
    if role=='Body':
        variant=str(obj.get('arc_body_variant') or '').strip()
        if variant and variant!='NONE':stem='_'.join(part.title() for part in variant.split('_'))
        elif stem.casefold()=='body':stem='Shared'
    if role=='Backpack':stem=re.sub(r'_Backpack$','',stem,flags=re.I)
    if role=='BackpackStrap':stem=re.sub(r'_Strap(?:_Skeleton)?$','',stem,flags=re.I)
    parts=stem.split('_');n=len(parts)//2
    if len(parts)%2==0 and parts[:n]==parts[n:]:stem='_'.join(parts[:n])
    stem=re.sub(r'_(?:Rig|Skeleton|Pivot)$','',stem,flags=re.I) or 'Asset'
    suffix='_Reference' if obj.get('arc_reference_plane') else '_Rig' if obj.type=='ARMATURE' else '_Pivot' if obj.type=='EMPTY' else ''
    stem = _catalog_flavour(obj, role, stem)
    code = source_code_name(obj)
    obj['arc_asset_code_name'] = code
    return role+'_'+stem+suffix+' ('+code+')'


def rename_objects(objects, scene=None):
    scene=scene or bpy.context.scene;renamed={}
    # Name anchors before their reference cards.
    for obj in sorted(objects,key=lambda o:bool(o.get('arc_reference_plane'))):
        role=role_for(obj)
        if not role:continue
        old=obj.name
        if not obj.get('arc_source_object_name'):obj['arc_source_object_name']=old
        base=standard_name(obj,role)
        if obj.get('arc_standard_name')!=base or not re.fullmatch(re.escape(base)+r'(?:\.\d+)?',obj.name):obj.name=base
        obj['arc_standard_name']=base
        if obj.data is not None and obj.data.users==1 and obj.type in {'MESH','ARMATURE'}:
            if not obj.get('arc_source_data_name'):obj['arc_source_data_name']=obj.data.name
            obj.data.name=base+('_Mesh' if obj.type=='MESH' else '_Armature')
        if old!=obj.name:renamed[old]=obj.name
    if renamed:
        for obj in scene.objects:
            for key in ('arc_reference_anchor','arc_inherit_body','arc_inherit_head','arc_head_wrap_head'):
                value=obj.get(key)
                if isinstance(value,str) and value in renamed:obj[key]=renamed[value]
        for prop in ('arc_last_weapon_armature','arc_last_applied_anim_armature'):
            value=getattr(scene,prop,'')
            if value in renamed:setattr(scene,prop,renamed[value])
        try:
            report=json.loads(scene.get('arc_add_all_last_report','{}'))
            for item in report.get('items',[]):
                detail=item.get('detail') or {}
                if 'objects' in detail:detail['objects']=[renamed.get(name,name) for name in detail['objects']]
            if report:scene['arc_add_all_last_report']=json.dumps(report)
        except (ValueError,TypeError):pass
    return renamed


def _tree(root):
    result={root}
    for child in root.children:result.update(_tree(child))
    return result


def sync_light_receivers(scene=None):
    """Maintain explicitly configured studio receiver sets across imports/regrouping."""
    scene = scene or bpy.context.scene
    characters = {'Body', 'Head', 'Hair', 'FacialHair', 'Outfit'}
    meshes = [o for o in scene.objects if o.type == 'MESH' and not o.get('arc_reference_plane')]
    counts = {}
    configured = {light.light_linking.receiver_collection for light in scene.objects
                  if light.type == 'LIGHT' and light.light_linking.receiver_collection}
    for collection in configured:
        group = collection.get('arc_light_receiver_group')
        if group not in {'CHARACTERS', 'ACCESSORIES'}:
            continue
        wanted = {o for o in meshes if role_for(o) in characters} if group == 'CHARACTERS' else {
            o for o in meshes if role_for(o) and role_for(o) not in characters}
        for obj in list(collection.objects):
            if obj not in wanted:
                collection.objects.unlink(obj)
        for obj in wanted:
            if obj.name not in collection.objects:
                collection.objects.link(obj)
        for entry in collection.collection_objects:
            entry.light_linking.link_state = 'INCLUDE'
        counts[group] = len(wanted)
    return counts


def organize_objects(objects=None, scene=None, mode=None):
    scene=scene or bpy.context.scene
    if mode is not None:scene[MODE_KEY]=mode
    full_scene = objects is None
    normalize_names = not full_scene or int(scene.get('arc_asset_naming_schema', 0)) < 3
    objects=[o for o in (list(objects) if objects is not None else list(scene.objects)) if role_for(o)]
    if not objects:return {'objects':0,'renamed':{}}
    state={o: [(layer,o.hide_get(view_layer=layer),o.select_get(view_layer=layer)) for layer in scene.view_layers if o.name in layer.objects] for o in objects}
    active={layer:layer.objects.active for layer in scene.view_layers}
    current=_tree(scene.collection);protected=set()
    for other in bpy.data.scenes:
        if other!=scene:protected.update(_tree(other.collection))
    old_groups={c for o in objects for c in o.users_collection if c in current and c not in protected}
    managed=set(objects)
    old_groups.update(c for c in current if c.all_objects and set(c.all_objects).issubset(managed))
    renamed=rename_objects(objects,scene) if normalize_names else {}
    if full_scene:scene['arc_asset_naming_schema']=3
    for obj in objects:
        destination=collection_for_object(obj,scene)
        if obj.name not in destination.objects:destination.objects.link(obj)
        for col in list(obj.users_collection):
            if col!=destination and col in current and col not in protected:col.objects.unlink(obj)
    # Remove emptied former groups; leave unrelated objects and shared scenes intact.
    candidates=old_groups | {c for c in current if c.get(COLLECTION_KEY) or c.name=='Arc Reference Planes' or c.name.startswith('Arc References - ')}
    root=root_collection(scene)
    for layer in scene.view_layers:
        def find(node):
            if node.collection==root:return node
            for child in node.children:
                found=find(child)
                if found:return found
        found=find(layer.layer_collection)
        if found:layer.active_layer_collection=found
    for _ in range(len(candidates)+1):
        empty=[c for c in candidates if c!=scene.collection and c!=root and c not in protected and not c.objects and not c.children and c.name!='Collection']
        if not empty:break
        for col in empty:candidates.discard(col);bpy.data.collections.remove(col)
    for obj,values in state.items():
        for layer,hidden,selected in values:
            if obj.name in layer.objects:obj.hide_set(hidden,view_layer=layer);obj.select_set(selected,view_layer=layer)
    for layer,obj in active.items():
        if obj is not None and obj.name in layer.objects:layer.objects.active=obj
    sync_light_receivers(scene)
    from .lighting_looks import sync_reference_compositor_mask
    sync_reference_compositor_mask(scene)
    return {'objects':len(objects),'renamed':renamed}


class _GroupAssets:
    bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):
        from . import appearance_queue
        return appearance_queue.queue_status() is None
    def execute(self,context):
        result=organize_objects(scene=context.scene,mode=self.group_mode)
        self.report({'INFO'},f"Organized {result['objects']} imported objects")
        return {'FINISHED'}


class ARC_OUTFITS_OT_GroupByAssetType(_GroupAssets,Operator):
    bl_idname='arc_outfits.group_by_asset_type'
    bl_label='Group by Asset Type'
    bl_description='Regroup imported meshes, rigs and reference cards by asset type'
    group_mode='TYPE'


class ARC_OUTFITS_OT_GroupByOutfitSlot(_GroupAssets,Operator):
    bl_idname='arc_outfits.group_by_outfit_slot'
    bl_label='Group by Outfit Slot'
    bl_description='Regroup each character slot and its reference cards into one collection'
    group_mode='SLOT'
