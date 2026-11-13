"""Source-driven weapon visuals, Ascended variants and preview references."""
from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path
from functools import lru_cache

_REVISION=0

@lru_cache(maxsize=1)
def bundled():
    path=Path(__file__).parent.parent/'assets/weapon_browser/catalog.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'weapons':{},'mods':{}}

@lru_cache(maxsize=1)
def generated_previews():
    path=Path(__file__).parent.parent/'assets/weapon_browser/generated/index.json'
    return json.loads(path.read_text(encoding='utf-8'))

def bundled_preview(entry,variant='OFF',mod=False,*,tier=None,stencil='NONE'):
    if not mod and tier is not None:
        tiers=generated_previews()['weapons'].get(entry['stem'],{}).get(variant,{})
        icon=tiers.get(str(tier),{}).get(stencil or 'NONE')
        if not icon and variant!='OFF':
            icon=tiers.get('3',{}).get('NONE')
        return Path(__file__).parent.parent/'assets/weapon_browser/generated'/icon if icon else None
    data=bundled().get('mods' if mod else 'weapons',{}).get(entry['stem'],{})
    icon=data.get('preview') if mod or variant=='OFF' else next((v.get('preview') for v in data.get('variants',[]) if v['id']==variant),data.get('preview'))
    path=Path(__file__).parent.parent/'assets/weapon_browser'/str(icon) if icon else None
    return path if path and path.is_file() else None

def bundled_variants(entry):
    return [dict(variant) for variant in bundled().get('weapons',{}).get(entry['stem'],{}).get('variants',[])]

def bundled_skins(entry):
    skins=[dict(skin) for skin in bundled().get('weapons',{}).get(entry['stem'],{}).get('skins',[])]
    for skin in skins:
        skin['label']=stencil_name(skin['id']) or skin['label']
    return sorted(skins,key=lambda skin:skin['label'].casefold())

def reference(value):
    if isinstance(value,dict):value=value.get('ObjectPath') or value.get('AssetPathName') or ''
    return str(value or '').split('.')[0]

def long_path(path,force=False):
    """Win32 extended-length form: Blender's Python is not long-path aware, and
    cached Ascended/skin packages under an AppData addon exceed MAX_PATH."""
    text=str(path)
    if os.name!='nt' or text.startswith('\\\\?\\') or (len(text)<240 and not force):return Path(text)
    text=os.path.abspath(text)
    return Path('\\\\?\\UNC\\'+text[2:] if text.startswith('\\\\') else '\\\\?\\'+text)

def exports(path):
    data=json.loads(long_path(path).read_text(encoding='utf-8-sig'))
    if isinstance(data,dict):data=data.get('Exports',data.get('exports',[data]))
    return data

def properties(path,kind=None):
    rows=exports(path)
    row=next((x for x in rows if not kind or x.get('Type')==kind),{})
    return row.get('Properties',{})

SUPPLIED_UNPACK=r'Z:\Datamining\Arc Raiders\2.0.0_FrozenTrail\Unpack_2.0.0\PioneerGame\Content\Pioneer'

def pioneer_roots(scene=None,*,source_path=None):
    from ..Shared import utils
    primary=[]
    for parent in Path(source_path).parents if source_path else ():
        if parent.name.casefold()=='pioneer' and parent.parent.name.casefold()=='content':
            primary.append(parent)
            break
    raw=getattr(scene,'arc_pioneer_root','') if scene else utils.get_pioneer_root()
    return _PioneerRoots((*primary,raw,*getattr(utils,'_pak_cache_roots',[]),str(Path(__file__).parent.parent/'ArcPak/cache')))

class _PioneerRoots:
    def __init__(self,primary,include_old=True):
        self.primary=tuple(primary)
        self.include_old=include_old
        self._roots=[]
        self._iterator=None
        self._complete=False

    def __iter__(self):
        index=0
        while True:
            if index<len(self._roots):
                root=self._roots[index]
            else:
                if self._complete:return
                if self._iterator is None:self._iterator=self._discover()
                try:root=next(self._iterator)
                except StopIteration:
                    self._complete=True
                    return
                self._roots.append(root)
            yield root
            index+=1

    def _discover(self):
        from ..Shared import utils
        seen=set()
        old=Path(SUPPLIED_UNPACK)
        def candidates(roots):
            for root in roots:
                if not root:continue
                path=Path(root)
                for candidate in (path,path/'Content/Pioneer',path/'PioneerGame/Content/Pioneer'):
                    if candidate in seen or (not self.include_old and (candidate==old or old in candidate.parents)):continue
                    if (candidate/'Items').is_dir():
                        seen.add(candidate)
                        yield candidate
        yield from candidates(self.primary)
        yield from candidates(Path(content)/'Pioneer' for content in utils.get_content_dirs())
        if self.include_old:yield from candidates((SUPPLIED_UNPACK,))

    def __reversed__(self):
        return reversed(list(self))

def current_roots(roots):
    """``roots`` without the supplied 2.0 unpack, which is only a fallback for unreadable paks."""
    if isinstance(roots,_PioneerRoots):return _PioneerRoots(roots.primary,include_old=False)
    old=Path(SUPPLIED_UNPACK)
    return [r for r in roots if Path(r)!=old]

def resolve(ref,roots,suffix='.json'):
    ref=reference(ref)
    if not ref:return None
    if ref.startswith('/Game/Pioneer/'):rel=ref[len('/Game/Pioneer/'):]
    elif ref.startswith('PioneerGame/Content/Pioneer/'):rel=ref[len('PioneerGame/Content/Pioneer/'):]
    else:
        p=long_path(ref)
        if p.is_file():return p
        rel=ref
    for root in roots:
        p=long_path((Path(root)/rel).with_suffix(suffix))
        if p.is_file():return p
    return None

def weapon_folder(entry,roots):
    for r in roots:
        p=long_path(Path(r)/'Items'/entry.get('kind','Firearms')/entry['stem'])
        if p.is_dir():return p
    return None

def base_visual(entry,roots):
    folder=weapon_folder(entry,roots)
    if not folder:return None
    exact=folder/('DA_WeaponVisuals_'+entry['stem']+'.json')
    if exact.is_file():return exact
    choices=sorted(folder.glob('DA_WeaponVisuals_*.json'))
    return choices[0] if choices else None

def label_words(value):
    value=re.sub(r'([a-z])([A-Z])',r'\1 \2',value)
    return value.replace('_',' ').strip()

@lru_cache(maxsize=128)
def _variants(folder,files=None):
    path=Path(folder)/'Ascended'
    if files is None:files=tuple(str(p) for p in sorted(path.rglob('DA_WeaponVisuals_*.json')))
    result=[]
    for filename in files:
        p=Path(filename)
        rel=p.relative_to(Path(folder)).as_posix()
        token=p.parent.name
        number=re.search(r'_[A-Z]_(\d+)$',p.stem)
        if number:token+=' '+number[1]
        result.append({'id':rel,'label':label_words(token),'visual':str(p)})
    return tuple(result)

def variants(entry,roots):
    # A PAK cache may contain only the last imported permutation. Merge it with
    # the catalog and every resource root instead of treating it as complete.
    result={v['id']:dict(v,visual=str(resolve(v['reference'],roots) or v['reference']))
            for v in bundled().get('weapons',{}).get(entry['stem'],{}).get('variants',[])}
    found=set()
    for root in roots:
        folder=Path(root)/'Items'/entry.get('kind','Firearms')/entry['stem']
        files=tuple(str(p) for p in sorted((folder/'Ascended').rglob('DA_WeaponVisuals_*.json')))
        for v in _variants(str(folder),files):
            if v['id'] in found:continue
            result[v['id']]=dict(result.get(v['id'],{}),**v)
            found.add(v['id'])
    choices=sorted(result.values(),key=lambda v:(not v['label'].casefold().startswith('default'),v['label'].casefold(),v['id']))
    # A visual permutation is useful in the selector only when its resolved
    # assembly uses a different mesh set from the base weapon.
    base=base_visual(entry,roots)
    if not base:return choices
    try:
        signature=lambda plan: (plan['base'],tuple(sorted(mesh_reference(p) for p in plan['parts'] if mesh_reference(p))))
        base_signature=signature(visual_plan(base,roots))
    except (OSError,ValueError,FileNotFoundError):return choices
    filtered=[]
    for variant in choices:
        try:
            path=variant.get('visual')
            if not path or not long_path(path).is_file():
                # Bundled descriptors may point to PAK content not extracted yet.
                filtered.append(variant)
            elif signature(visual_plan(path,roots))!=base_signature:
                filtered.append(variant)
        except (OSError,ValueError,FileNotFoundError):
            # Keep an option if incomplete exports prevent a safe comparison.
            filtered.append(variant)
    return filtered

def skins(entry,roots):
    """Authored skin visual DAs (Skins/<Name>/DA_WeaponVisuals_*), bundled list merged with roots."""
    result={s['id']:dict(s,visual=str(resolve(s['reference'],roots) or s['reference'])) for s in bundled().get('weapons',{}).get(entry['stem'],{}).get('skins',[])}
    for root in reversed(roots):
        folder=Path(root)/'Items'/entry.get('kind','Firearms')/entry['stem']/'Skins'
        for path in sorted(folder.glob('*/DA_WeaponVisuals_*.json')):
            name=path.parent.name
            reference='/Game/Pioneer/'+path.relative_to(Path(root)).with_suffix('').as_posix()
            result[name]=dict(result.get(name,{}),id=name,label=label_words(name),reference=reference,visual=str(path))
    for skin in result.values():
        stencil=stencil_name(skin['id'],roots)
        if stencil:skin['label']=stencil
    return sorted(result.values(),key=lambda s:s['label'].casefold())

# In-game stencil names (Items/Recipes/ItemSkin_<id>/DA_UIMetaData_* ItemName, ST_WeaponStencils, 2.3.0).
# A gun's Skins/<id> folder is the stencil applied to that gun; recipe folders may add a 01 suffix.
STENCIL_NAMES={'CommonBlue':'Verdigris','CommonRed':'Garnet','CommonYellow':'Ochre','DustStrokeCamo':'Milky Terraccota',
    'MuralTree':'Cerulean','OrganicIsland':'Dusty Camo','Bells':'Sacrifice','Outlander':'Bulwark','Renzo':"Dragon's Breath",
    'StationMaster':'Fortuna','Corto':'Slipstream','Tortoiseshell':'Tortoise','Yeti':'Serac','Banshee':'Empyrean'}

def stencil_name(skin_id,roots=()):
    """Player-facing stencil name for a Skins/<id> folder, read from the recipe DA when present."""
    return _stencil_name(skin_id,tuple(str(r) for r in roots))

@lru_cache(maxsize=256)
def _stencil_name(skin_id,roots):
    for root in roots:
        folder=Path(root)/'Items'/'Recipes'
        for path in (folder/('ItemSkin_'+skin_id),folder/('ItemSkin_'+skin_id+'01')):
            for meta in sorted(path.glob('DA_UIMetaData_*.json')) if path.is_dir() else ():
                try:rows=json.loads(meta.read_text(encoding='utf-8'))
                except (OSError,ValueError):continue
                for row in rows if isinstance(rows,list) else rows.get('Exports',[]):
                    name=(row.get('Properties',{}).get('ItemName') or {})
                    text=name.get('LocalizedString') or name.get('SourceString')
                    if text:return str(text)
    return STENCIL_NAMES.get(skin_id,'')

def skin_by_id(entry,roots,skin):
    return next((s for s in skins(entry,roots) if s['id']==skin),None) if skin and skin!='NONE' else None

def compose_skin(plan,skin_path,roots):
    """Apply a skin DA's own deltas (parts, MaterialOverrides) to another visual's plan."""
    row=next((r for r in exports(skin_path) if r.get('Type','').endswith('WeaponVisualsDataAsset')),None)
    if row is None:raise ValueError('Not weapon skin visual metadata: '+str(skin_path))
    p=row.get('Properties',{})
    plan=dict(plan,parts=list(plan['parts']),overrides=dict(plan['overrides']))
    remove=set(p.get('PartsToRemove',[]))
    new=[q for q in p.get('WeaponParts',[]) if spawns(q,plan)]
    for part in new:remove.update(tags(part,'ReplacesPartUsingTags'))
    plan['parts']=[q for q in plan['parts'] if not tags(q)&remove]+new
    plan['overrides'].update({x['SlotName'].casefold():reference(x.get('Material')) for x in p.get('MaterialOverrides',[])})
    plan['skin_visual']=str(skin_path)
    return plan

def tags(part,key='Tags'):
    return {str(t.get('TagName','')) if isinstance(t,dict) else str(t) for t in (part.get(key) or [])}

# A part spawns only when the visual context holds all its RequiredTags. The importer builds the
# skeletal (in-hand) assembly; only a static base carries Weapon.Visual.StaticMesh and no import
# is Weapon.Visual.Stowed. Rascal's static stocks are the only tagged parts (2.2-2.3): its
# skeletal base already has the stock, and the folded one is the stowed-on-back look.
STATIC_CONTEXT=frozenset({'Weapon.Visual.StaticMesh'})

def spawns(part,plan):
    # The importer shows the BasePartStaticMesh look (weapon_import._static_look), so the static
    # context's parts complete it (Rascal's SM has no stock; its stock is a StaticMesh-tagged part).
    static=plan.get('static_base') or plan.get('static_mesh')
    return tags(part,'RequiredTags')<=(STATIC_CONTEXT if static else frozenset())

def _visual_chain(path,roots,stack=()):
    """[(path, Properties)] of a weapon visual and its ParentVisuals, root first."""
    path=Path(path)
    if str(path) in stack:raise ValueError('Weapon visual inheritance cycle: '+str(path))
    row=next((r for r in exports(path) if r.get('Type','').endswith('WeaponVisualsDataAsset')),None)
    if row is None:raise ValueError('Not weapon visual metadata: '+str(path))
    p=row.get('Properties',{})
    parent=reference(p.get('ParentVisuals'))
    if not parent:return [(path,p)]
    pp=resolve(parent,roots)
    if not pp:raise FileNotFoundError('Parent weapon visuals missing: '+parent)
    return _visual_chain(pp,roots,stack+(str(path),))+[(path,p)]

def visual_plan(path,roots):
    chain=_visual_chain(path,roots)
    plan={'base':'','parts':[],'overrides':{},'visual':str(Path(path)),'static_base':False}
    for _path,p in chain:
        skeletal=reference(p.get('BasePartSkeletalMesh'))
        static=reference(p.get('BasePartStaticMesh'))
        base=skeletal or static
        if base:plan['base']=base;plan['static_base']=not skeletal
        if static:plan['static_mesh']=static
    for _path,p in chain:
        remove=set(p.get('PartsToRemove',[]))
        new=[q for q in p.get('WeaponParts',[]) if spawns(q,plan)]
        for part in new:remove.update(tags(part,'ReplacesPartUsingTags'))
        plan['parts']=[q for q in plan['parts'] if not tags(q)&remove]+new
        plan['overrides'].update({x['SlotName'].casefold():reference(x.get('Material')) for x in p.get('MaterialOverrides',[])})
    return plan

def slot_kind(slot):
    """Mod type of a ModSlot asset: DA_ModSlot_Firearm_MagazineMedium -> Magazine."""
    name=reference(slot).split('/')[-1].removeprefix('DA_ModSlot_Firearm_')
    return next((kind for key,kind in [('Magazine','Magazine'),('Muzzle','Muzzle'),('Stock','Stock'),('UnderBarrel','UnderBarrel')] if key in name),name)

def _items(entry,variant):
    """Catalog DA_Items showing ``variant`` ('OFF' = base visual), the one the icon names first."""
    items=[i for i in bundled().get('weapons',{}).get(entry['stem'],{}).get('items',[]) if i.get('variant')==(variant or 'OFF')]
    if not items and (variant or 'OFF')=='OFF':
        items=[{'reference':f"/Game/Pioneer/Items/{entry.get('kind','Firearms')}/{entry['stem']}/DA_Item_{entry['stem']}"}]
    return items

def item_packages(entry,variant):
    """Packages default_mods reads for ``variant``: the pak index lists meshes only."""
    item=next(iter(_items(entry,variant)),None)
    return [item['reference'],*item.get('packages',[])] if item else []

def item_slots(item,roots):
    """(ModSlots or None, missing reference) of a DA_Item: its ItemClass, then up the Blueprint
    Super chain to the first class default object that sets ModSlots (a cooked CDO holds only
    what differs from its parent class)."""
    path=resolve(item,roots)
    if not path:return None,reference(item)
    cls=next((r['Properties']['ItemClass'] for r in exports(path) if 'ItemClass' in r.get('Properties',{})),None)
    for _ in range(12):
        ref=reference(cls)
        if not ref.startswith('/Game/'):break
        path=resolve(ref,roots)
        if not path:return None,ref
        rows=exports(path)
        slots=next((r for r in rows if str(r.get('Name','')).startswith('Default__')),{}).get('Properties',{}).get('ModSlots')
        if slots is not None:return slots,''
        cls=next((r.get('Super') or r.get('SuperStruct') for r in rows if r.get('Type')=='BlueprintGeneratedClass'),None)
    return [],''

def default_mods(entry,variant,roots):
    """([part], [missing reference]): the ModSlots DefaultVisual parts of the item showing
    ``variant``. A part is the WeaponModVisualizationAsset's Visual plus 'arc_mod_slot'."""
    items=_items(entry,variant)
    if not items:return [],[]
    slots,missing=item_slots(items[0]['reference'],roots)
    if slots is None:return [],[missing]
    parts,missing=[],[]
    for slot in slots:
        ref=reference(slot.get('DefaultVisual'))
        if not ref:continue
        path=resolve(ref,roots)
        if not path:missing.append(ref);continue
        part=properties(path,'WeaponModVisualizationAsset').get('Visual')
        if part:parts.append(dict(part,arc_mod_slot=reference(slot.get('ModSlotAsset')).split('/')[-1]))
    return parts,missing

def _part_key(part):
    vec=lambda v,keys,unit:tuple(round(float((v or {}).get(k,unit)),3) for k in keys)
    return (mesh_reference(part).casefold(),str(part.get('AttachmentSocket') or 'weapon_root').casefold(),
            vec(part.get('LocalOffset'),'XYZ',0),vec(part.get('LocalRotation'),('Pitch','Yaw','Roll'),0),vec(part.get('LocalScale'),'XYZ',1))

def with_default_mods(plan,parts):
    """``plan`` with the item's default mod parts. RequiredTags apply as to the visual's parts; a
    default replaces the parts its ReplacesPartUsingTags name; one identical to a visual part
    (mesh, socket, local transform) marks that part with its slot instead of adding a copy."""
    parts=[p for p in parts if spawns(p,plan)]
    remove=set()
    for part in parts:remove.update(tags(part,'ReplacesPartUsingTags'))
    kept=[q for q in plan['parts'] if not tags(q)&remove]
    index={_part_key(q):i for i,q in enumerate(kept)}
    added,report=[],[]
    for part in parts:
        i=index.get(_part_key(part))
        if i is None:added.append(part)
        else:kept[i]=dict(kept[i],arc_mod_slot=part['arc_mod_slot'])
        report.append({'slot':part['arc_mod_slot'],'mesh':Path(mesh_reference(part)).name,'built_in':i is not None})
    return dict(plan,parts=kept+added,default_mods=report)

def mesh_slots(path,*,resolve_slot_names=True):
    if Path(path).suffix.casefold()=='.uemodel':
        from ..Shared import uemodel_import
        _name,_version,lods=uemodel_import.parse_uemodel(str(path))
        lod=next((l for l in lods if len(l.vertices) and l.materials),None)
        if not lod:return []
        names=(_native_slot_names(path,lod) or _json_slot_names(path,lod)) if resolve_slot_names else None
        return [(names[i] if names else slot,reference(ref)) for i,(slot,ref,_first,_faces) in enumerate(lod.materials)]
    p=long_path(Path(path).with_suffix('.json'))
    if not p.is_file():raise FileNotFoundError('Mesh material metadata missing: '+str(p))
    rows=exports(p)
    row=next((r for r in rows if r.get('Type') in ['SkeletalMesh','StaticMesh']),{})
    slots=row.get('SkeletalMaterials') or row.get('Properties',{}).get('StaticMaterials') or []
    return [(s.get('MaterialSlotName',''),reference(s.get('MaterialInterface') or s.get('Material'))) for s in slots]

def _native_slot_names(path,lod):
    """MaterialSlotName per UEModel render section, from the pak-direct material-slot sidecar.

    A UEModel section carries only its material's export name, so skin MaterialOverrides (keyed by
    slot name) never matched pak-direct meshes. None when the sidecar is absent or disagrees.
    """
    sidecar=long_path(Path(path).with_suffix('.arc-material-slots.json'))
    if not sidecar.is_file():return None
    data=json.loads(sidecar.read_text(encoding='utf-8-sig'))
    slots=data.get('materials') or []
    native=next((l for l in data.get('lods',[]) if l.get('name')==lod.name),None)
    sections=(native or {}).get('sections') or []
    if len(sections)!=len(lod.materials):return None
    names=[]
    for section,(export,_ref,first,faces) in zip(sections,lod.materials):
        i=section.get('effective_material_index')
        if (not isinstance(i,int) or not 0<=i<len(slots) or str(section.get('export_name'))!=str(export)
                or (section.get('first_index'),section.get('triangles'))!=(int(first),int(faces))):return None
        names.append(slots[i].get('slot_name') or export)
    return names

def _json_slot_names(path,lod):
    """MaterialSlotName per section from the mesh's JSON export when no sidecar exists.

    The sidecar covers skeletal meshes only, so part static meshes (Hairpin barrel/grip) kept
    their MI export names and missed the stencil's MaterialOverrides. Their StaticMaterials
    carry the real slot names (Pistol_Silenced_01_A; Kettle's barrel is BurstRifle_01_AAssetUV0).
    Used only when every section's MI matches the JSON slot in order.
    """
    path=Path(path);parts=[p.casefold() for p in path.parts]
    try:i=max(k for k in range(len(parts)-1) if parts[k]=='content' and parts[k+1]=='pioneer')
    except ValueError:return None
    rel=Path(*path.parts[i+2:]).with_suffix('.json')
    for candidate in (path.with_suffix('.json'),Path(SUPPLIED_UNPACK)/rel):
        candidate=long_path(candidate)
        if not candidate.is_file():continue
        try:rows=exports(candidate)
        except (OSError,ValueError):continue
        row=next((r for r in rows if r.get('Type') in ['SkeletalMesh','StaticMesh']),{})
        slots=row.get('SkeletalMaterials') or row.get('StaticMaterials') or row.get('Properties',{}).get('StaticMaterials') or []
        if len(slots)!=len(lod.materials):continue
        names=[]
        for slot,(export,_ref,_first,_faces) in zip(slots,lod.materials):
            value=slot.get('MaterialInterface') or slot.get('Material') or {}
            mi=reference(value).rsplit('/',1)[-1]
            if not mi and isinstance(value,dict):mi=str(value.get('ObjectName','')).strip("'").split("'")[-1]
            if mi.casefold()!=str(export).casefold() or not slot.get('MaterialSlotName'):names=None;break
            names.append(slot['MaterialSlotName'])
        if names:return names
    return None

def mesh_reference(part):
    return reference(part.get('SkeletalMesh') if part.get('bUseSkeletalMesh') else part.get('Mesh'))

def icon_refs(folder):
    result=[]
    for p in sorted(Path(folder).glob('DA_UIMetaData*.json')):
        for row in exports(p):
            props=row.get('Properties',{})
            for name in ['BigIcon','Icon']:
                ref=reference(props.get(name))
                if ref and ref not in result:result.append(ref)
    return result

def ascended_preview(entry,roots,variant):
    visual=next((v for v in variants(entry,roots) if v['id']==variant),None)
    if not visual:return None
    expected=visual.get('preview')
    if expected:
        # Prefer T3 for this exact visual; never replace it with base-gun art.
        t3=re.sub(r'_T\d+(?=\.png$)','_T3',expected,flags=re.IGNORECASE)
        for filename in dict.fromkeys([t3,expected]):
            for root in roots:
                folder=Path(root)/'Items'/entry.get('kind','Firearms')/entry['stem']
                for parent in [folder/'Ascended',folder/'Ascended/Permutations',Path(visual['visual']).parent]:
                    path=parent/filename
                    if path.is_file():return path
            path=Path(__file__).parent.parent/'assets/weapon_browser'/Path(filename).with_suffix('.avif')
            if path.is_file():return path
    token=Path(variant).parent.name.casefold()
    ascended_folder=None
    for root in roots:
        folder=Path(root)/'Items'/entry.get('kind','Firearms')/entry['stem']/'Ascended'
        if folder.is_dir() and ascended_folder is None:ascended_folder=folder
        for parent in [folder/Path(variant).parent.name,folder/'Permutations',folder]:
            if not parent.is_dir():continue
            refs=icon_refs(parent)
            for ref in refs:
                name=Path(ref).name.casefold()
                if 'big' not in name:continue
                if token=='default' and 'ascended' not in name:continue
                if token!='default' and token not in name:continue
                path=resolve(ref,roots,'.png')
                if path:return path
            for path in sorted(parent.glob('T_UI*Big*.png')):
                name=path.stem.casefold()
                if (token=='default' and 'ascended' in name) or (token!='default' and token in name):return path
    # Variant folders commonly share one generic Ascended icon. Use that for
    # permutations without their own authored icon (e.g. Sprint Shooting).
    if ascended_folder:
        for path in sorted(ascended_folder.glob('T_UI*Big*.png')):
            if 'ascended' in path.stem.casefold():return path
    data=bundled().get('weapons',{}).get(entry['stem'],{})
    icon=next((v.get('preview') for v in data.get('variants',[]) if v.get('id')==variant and v.get('preview')),None)
    path=Path(__file__).parent.parent/'assets/weapon_browser'/str(icon) if icon else None
    if path and path.is_file():return path
    return None

def preview_path(entry,roots=(),variant='OFF',mod=False):
    return bundled_preview(entry,variant,mod)

def enum_number(identifier):
    # Blender UI buttons store numeric enum choices through float32. Keep IDs
    # exactly representable; the old seven-hex-digit hash rounded on mouse click.
    return int(hashlib.sha256(identifier.encode()).hexdigest()[:5],16)+1

def mod_visual(entry,weapon,roots):
    # Game order: the first VisualAssetOverrides entry whose tags the weapon's
    # DA_Item carries, else ModData.VisualizedAsset. The folder's own DA_Visual
    # is only a fallback for a mod whose BP_Item is not extracted.
    folder=next((Path(r)/'Items/WeaponMod'/entry['stem'] for r in roots if (Path(r)/'Items/WeaponMod'/entry['stem']).is_dir()),None)
    if folder is None:return None
    direct=long_path(folder/('DA_Visual_WeaponMod_'+entry['stem']+'.json'))
    bp=long_path(folder/('BP_Item_WeaponMod_'+entry['stem']+'.json'))
    gun=weapon_folder(weapon,roots)
    weapon_tags=set()
    if gun:
        for p in gun.glob('DA_Item_*.json'):
            weapon_tags.update(re.findall(r'Mod\.Visual\.[A-Za-z0-9_.]+',long_path(p).read_text(encoding='utf-8-sig')))
    def visual(asset):
        path=resolve(asset,roots)
        if path:return properties(path,'WeaponModVisualizationAsset').get('Visual')
        return {'metadata_reference':reference(asset)}
    if bp.is_file():
        for row in exports(bp):
            md=row.get('Properties',{}).get('ModData',{})
            for override in md.get('VisualAssetOverrides',[]):
                query=override.get('TagQuery',{})
                tags={x.get('TagName','') for x in query.get('TagDictionary',[])}
                if tags and not tags&weapon_tags:continue
                return visual(override.get('Asset'))
            if reference(md.get('VisualizedAsset')):return visual(md['VisualizedAsset'])
    if direct.is_file():return properties(direct,'WeaponModVisualizationAsset').get('Visual')
    return None

def mod_visual_folders(entry,roots):
    """Content-relative folders of the visuals an item-only mod points at, in game
    order. Magazine items carry no mesh of their own: the item folder holds the
    BP_Item, and each VisualAssetOverrides entry names a per-gun-family mesh folder."""
    stem=entry.get('stem') or ''
    out=[]
    for r in roots:
        bp=long_path(Path(r)/'Items/WeaponMod'/stem/('BP_Item_WeaponMod_'+stem+'.json'))
        if not bp.is_file():continue
        for row in exports(bp):
            if not isinstance(row,dict):continue
            md=row.get('Properties',{}).get('ModData',{})
            for asset in [o.get('Asset') for o in md.get('VisualAssetOverrides',[])]+[md.get('VisualizedAsset')]:
                ref=reference(asset)
                if ref.startswith('/Game/Pioneer/'):out.append(ref[len('/Game/Pioneer/'):].rsplit('/',1)[0])
        break
    return list(dict.fromkeys(out))

def invalidate():
    global _REVISION
    _REVISION+=1
    _variants.cache_clear()
