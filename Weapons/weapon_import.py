"""Transactional, weapon-only visual assembly and material assignment."""
import json
import math
from pathlib import Path
import bpy
from mathutils import Matrix,Vector,Euler,Quaternion
from . import weapon_assets as assets,weapon_catalog as catalog,weapon_position as positions

def mesh_file(ref,roots):
    return next((p for root in roots for ext in ['.psk','.pskx','.uemodel'] if (p:=assets.resolve(ref,[root],ext))),None)

def _extract_missing(scene,refs):
    from ..Shared import pak_extract
    rels=[assets.reference(r).removeprefix('/Game/Pioneer/') for r in refs if assets.reference(r)]
    # Folder extraction lists meshes only. Explicitly request visual DA and socket
    # metadata as well; these packages are not in the mesh browser index.
    for rel in list(rels):
        parts=rel.strip('/').split('/')
        if len(parts)==3 and parts[:2] in [['Items','Firearms'],['Items','Launchers']]:
            e=assets.bundled().get('weapons',{}).get(parts[-1],{})
            rels.extend(assets.reference(v).removeprefix('/Game/Pioneer/') for v in [e.get('base_visual'),e.get('skeleton')] if v)
            rels.extend(v['reference'].removeprefix('/Game/Pioneer/') for v in e.get('variants',[]))
            rels.append(rel+'/DA_Item_'+parts[-1])
        if len(parts)==3 and parts[:2]==['Items','WeaponMod']:
            stem=parts[-1]
            rels.extend(rel+'/'+prefix+stem for prefix in ['DA_Item_WeaponMod_','DA_Visual_WeaponMod_','BP_Item_WeaponMod_'])
    rels=list(dict.fromkeys(rels))
    if rels:
        pak_extract.ensure_rels(scene,rels)
        # Extraction drops the plain-path material index only; long packages are
        # indexed under the extended-length cache root, which would stay stale.
        from ..Shared.materials.rebuild.assets import invalidate_index
        invalidate_index(assets.long_path(pak_extract.cache_dir(),force=True))

def _import_mesh(path,context):
    from ..Shared import importing
    if path.suffix.casefold()=='.uemodel':
        from ..Shared import uemodel_import
        # Skeletal meshes come back skinned from WEIGHTS (groups + Armature modifier, parented).
        return uemodel_import.import_uemodel_skinned(str(path),scale=importing.ue_import_unit_scale(context.scene))
    return importing.import_psk(str(path),context=context)

def _bind_groups(objects,main):
    """Every mesh gets a vertex group: rigid parts take their socket bone at weight 1.0.
    Socket parts already follow the bone through the anchor constraints, so they get no
    Armature modifier; meshes parented straight to an armature get one (rest pose: no change)."""
    from ..Shared import uemodel_import
    for obj in objects:
        if obj.type!='MESH' or obj.vertex_groups:continue
        bone='';node=obj.parent
        while node is not None and node.type!='ARMATURE':
            sub=next((c.subtarget for c in node.constraints if getattr(c,'subtarget','')),'')
            if sub:bone=sub;break
            node=node.parent
        if node is not None and node.type=='ARMATURE' and not bone:
            root=next((b.name for b in node.data.bones if b.parent is None),'')
            if root:uemodel_import.rigid_bind_to_bone(obj,node,root)
            continue
        bone=bone or (next((b.name for b in main.data.bones if b.parent is None),'') if main else '')
        if not bone:continue
        group=obj.vertex_groups.new(name=bone);group.add(range(len(obj.data.vertices)),1.0,'REPLACE')
        obj['arc_rigid_bone']=bone

def _bone(main,name):
    if main is None:return None
    return next((b for b in main.pose.bones if b.name.casefold()==str(name).casefold()),None)

def _socket(main,name):
    if str(name or '').casefold() in ['', 'none']:return None,{'component_space':True}
    if str(name).casefold()=='weapo_root':name='weapon_root'  # Authored Vulcano typo.
    direct=_bone(main,name)
    if direct:return direct,{}
    path=Path(main.get('arc_source_mesh','')).with_suffix('.json')
    rows=assets.exports(path) if path.is_file() else []
    mesh=next((x for x in rows if x.get('Type')=='SkeletalMesh'),{})
    skeleton=assets.resolve(mesh.get('Properties',{}).get('Skeleton'),assets.pioneer_roots(bpy.context.scene))
    if not skeleton:skeleton=assets.resolve(main.get('arc_weapon_skeleton_ref'),assets.pioneer_roots(bpy.context.scene))
    if skeleton:rows+=assets.exports(skeleton)
    for row in rows:
        p=row.get('Properties',{})
        if row.get('Type')=='SkeletalMeshSocket' and str(p.get('SocketName','')).casefold()==str(name).casefold():
            return _bone(main,p.get('BoneName')),p
    return None,{}

def ue_location(value,unit):
    """Unreal cm vector -> Blender units in the importers' Y-reflected basis."""
    value=value or {}
    return Vector((float(value.get('X',0))*unit,-float(value.get('Y',0))*unit,float(value.get('Z',0))*unit))

def ue_rotation(value):
    """FRotator (applied roll, pitch, then yaw) reflected by H=diag(1,-1,1): H R H."""
    value=value or {}
    return Euler((math.radians(float(value.get('Roll',0))),-math.radians(float(value.get('Pitch',0))),-math.radians(float(value.get('Yaw',0)))),'XYZ')

def ue_transform(location,rotation,scale,unit):
    scale=scale or {}
    return Matrix.LocRotScale(ue_location(location,unit),ue_rotation(rotation),Vector(tuple(float(scale.get(k,1)) for k in 'XYZ')))

def _unit(main):
    # UEModel rigs are built in Blender units; PSK rigs carry the scale on the object.
    from ..Shared import importing
    return float(main.get('arc_ue_unit_scale') or importing.ue_import_unit_scale(bpy.context.scene))

def _source_rest(main):
    """Reference-pose bone matrices (armature space, unscaled) from the source rig file."""
    cached=main.get('arc_weapon_source_rest')
    if cached:return {k:Matrix(v) for k,v in json.loads(cached).items()}
    path=Path(main.get('arc_source_mesh',''))
    if path.suffix.casefold()!='.uemodel' or not path.is_file():return {}
    from ..Shared import uemodel_import
    bones=uemodel_import.parse_uemodel_bones(str(path),scale=1.0)
    world={}
    for i,b in enumerate(bones):
        local=Matrix.LocRotScale(Vector(b.translation),Quaternion((b.rotation[3],b.rotation[0],b.rotation[1],b.rotation[2])),Vector((1,1,1)))
        world[i]=world[b.parent]@local if 0<=b.parent<i else local
    result={b.name.casefold():world[i] for i,b in enumerate(bones)}
    main['arc_weapon_source_rest']=json.dumps({k:[list(r) for r in m] for k,m in result.items()})
    return result

def _bone_correction(main,bone):
    """Rotation from the Blender bone frame to the cooked bone frame (identity for UEModel rigs)."""
    rest=_source_rest(main).get(bone.name.casefold()) if bone else None
    if rest is None:return Matrix.Identity(4)
    blender=bone.bone.matrix_local.to_quaternion()
    return (blender.inverted()@rest.to_quaternion()).to_matrix().to_4x4()

def _attach(objects,main,socket,part,owner):
    """Socket world = bone(ref pose) x socket relative; part = socket x LocalOffset/Rotation/Scale."""
    bone,socket_props=_socket(main,socket)
    if bone is None and not socket_props.get('component_space'):raise ValueError('Weapon attachment socket missing: '+str(socket))
    unit=_unit(main)
    anchor=bpy.data.objects.new(owner.name+' '+str(socket),None);bpy.context.scene.collection.objects.link(anchor)
    anchor.parent=owner;anchor['arc_weapon_owner']=owner.name;anchor['arc_weapon_socket']=str(socket)
    # Separate location/rotation constraints intentionally exclude skeletal object scale.
    for typ in ['COPY_LOCATION','COPY_ROTATION']:
        c=anchor.constraints.new(typ);c.target=main
        if bone:c.subtarget=bone.name
    frame=_bone_correction(main,bone)
    if socket_props and not socket_props.get('component_space'):
        frame=frame@ue_transform(socket_props.get('RelativeLocation'),socket_props.get('RelativeRotation'),socket_props.get('RelativeScale'),unit)
    if frame!=Matrix.Identity(4):
        offset_anchor=bpy.data.objects.new(owner.name+' '+str(socket)+' Socket Offset',None);bpy.context.scene.collection.objects.link(offset_anchor)
        offset_anchor.parent=anchor;offset_anchor['arc_weapon_owner']=owner.name
        offset_anchor.matrix_parent_inverse=Matrix.Identity(4);offset_anchor.matrix_basis=frame
        anchor=offset_anchor
    bpy.context.view_layer.update()
    local=ue_transform(part.get('LocalOffset'),part.get('LocalRotation'),part.get('LocalScale'),unit)
    members=set(objects)
    for obj in objects:
        if obj.parent not in members:
            original_scale=Matrix.Diagonal(obj.scale.copy()).to_4x4()
            obj.parent=anchor;obj.matrix_parent_inverse=Matrix.Identity(4)
            obj.matrix_basis=local@original_scale
            obj['arc_weapon_socket']=str(socket)
    return anchor

# QualityLevel 0..3 (tier I..IV) -> CustomPrimitiveData[1] ("WeaponQualitySlider",
# 0 = pristine, 1 = worn). The game writes CPD1 from Angelscript at runtime and cooks no
# per-tier value; these are fitted to the cooked per-tier BigIcons of three weapons in
# the recovered icon-capture projection (best summed score over Bettina, Anvil and
# Hullcracker; tier II estimates .66-.73, tier III .29-.33, flat within about .05).
# DataRaiders-Documentation sections/weapon-assembly-tier-skins.md.
TIER_WEAR={0:1.0,1:0.65,2:0.30,3:0.0}
TIER_LABELS={0:'I',1:'II',2:'III',3:'IV'}

def tier_wear(level):
    return TIER_WEAR[max(0,min(3,int(level)))]

def set_assembly_wear(root,level=None,wear=None):
    """Write the per-object CPD1 input on every mesh of a weapon assembly."""
    if level is not None:root['arc_weapon_tier']=int(level)
    value=float(wear if wear is not None else tier_wear(root.get('arc_weapon_tier',3)))
    root['arc_weapon_wear']=value
    from .material_runtime.weapons import WEAR_PROPERTY
    for obj in root.children_recursive:
        if obj.type=='MESH':obj[WEAR_PROPERTY]=value;obj.update_tag()
    return value

def _take_plain_name(mat,name):
    """The mesh import leaves a placeholder material under the MI name, so ours became .001.
    Hand the placeholder's users to ours, drop it and take the plain name."""
    if mat.name==name:return
    old=bpy.data.materials.get(name)
    if old is not None:
        if old.library is not None or any(str(k).startswith('arc_') for k in old.keys()):return
        if old.node_tree is not None and len(old.node_tree.nodes)>4:return
        old.user_remap(mat);bpy.data.materials.remove(old)
    mat.name=name

def _material(obj,path,overrides,skin='',cache=None):
    from ..Shared.materials import weapon_surface
    roots=assets.pioneer_roots(bpy.context.scene,source_path=path)
    slots=assets.mesh_slots(path,resolve_slot_names=bool(overrides))
    if not slots:raise ValueError('No source material slots: '+str(path))
    while len(obj.data.materials)<len(slots):obj.data.materials.append(None)
    cache=cache if cache is not None else {}
    for i,(name,ref) in enumerate(slots):
        # Skins are authored per slot name in MaterialOverrides; the whole MI is swapped.
        ref=overrides.get(name.casefold(),ref)
        mi=assets.resolve(ref,roots)
        if not mi:raise FileNotFoundError('Weapon material missing: '+ref)
        key=(str(mi).casefold(),tuple(u.name for u in obj.data.uv_layers))
        mat=cache.get(key)
        if mat is None:
            mat=bpy.data.materials.new(mi.stem)
            # No pre-built record: the native shader-verified route runs first.
            try:weapon_surface.setup(mat,str(mi),str(path),obj=obj)
            except FileNotFoundError:
                # Textures another Blender (or a non-weapon extraction) added to the cache after
                # this session indexed it are not in the inventory: refresh it once and retry.
                from ..Shared.materials.rebuild.assets import infer_export_root, invalidate_index
                bpy.data.materials.remove(mat);invalidate_index(infer_export_root(str(mi)))
                mat=bpy.data.materials.new(mi.stem);weapon_surface.setup(mat,str(mi),str(path),obj=obj)
            _take_plain_name(mat,mi.stem)
            cache[key]=mat
        obj.material_slots[i].link='OBJECT';obj.material_slots[i].material=mat
    from ..Shared.materials import weapon_fur
    weapon_fur.apply_shell_offsets(obj)
    obj['arc_source_mesh']=str(path);obj['arc_model_type']='weapon'
    obj['arc_weapon_material_overrides']=json.dumps(overrides)
    if skin:obj['arc_weapon_skin_visual']=str(skin)
    return len(slots)

def _static_look(objects,main,plan,roots,context,root,path):
    """Show the visual's BasePartStaticMesh instead of the skeletal base's geometry, keeping the
    skeleton for sockets. The skeletal bind pose can differ from the item look (Dolabra's SK binds
    with its barrel 13.5 cm out; its SM and the cooked icon have it retracted). Same component space."""
    ref=plan.get('static_mesh') if not plan.get('static_base') else ''
    sm=mesh_file(ref,roots) if ref else None
    if not sm:return objects,path
    skinned=[o for o in objects if o.type=='MESH']
    statics=[o for o in _import_mesh(sm,context) if o.type=='MESH']
    if not statics:return objects,path
    local=skinned[0].matrix_local.copy() if skinned else None
    for obj in statics:
        obj.parent=main
        if local is not None:obj.matrix_local=local
        obj['arc_weapon_owner']=root.name;obj['arc_weapon_static_look']=True
    for obj in skinned:bpy.data.objects.remove(obj,do_unlink=True)
    # MaterialOverrides name the skeletal mesh's slots; the SM's slot names can differ (Canto's SM
    # fell back to MI_SMG_Medium_01_A, losing Ascended_Base_A). Carry each override to the SM slot
    # that has the same default MI.
    overrides=plan.get('overrides')
    if overrides and path:
        by_ref={ref.casefold():overrides[name.casefold()] for name,ref in assets.mesh_slots(path) if name.casefold() in overrides}
        for name,ref in assets.mesh_slots(sm):
            if name.casefold() not in overrides and ref.casefold() in by_ref:overrides[name.casefold()]=by_ref[ref.casefold()]
    main['arc_weapon_static_look']=str(sm)
    return [o for o in objects if o not in skinned]+statics,sm

def next_slot(scene):
    occupied={int(o['arc_weapon_slot']) for o in scene.objects if o.get('arc_weapon_assembly') and 'arc_weapon_slot' in o}
    slot=0
    while slot in occupied:slot+=1
    return slot

def _skin_visual(scene,entry,roots,skin):
    choice=assets.skin_by_id(entry,roots,skin)
    if not choice:
        if skin and skin!='NONE':raise ValueError('Skin is not authored for '+entry['name']+': '+str(skin))
        return None,None
    path=assets.long_path(choice['visual'])
    # The supplied 2.0 unpack's skin DAs lack MaterialOverrides (Hairpin Ochre/Verdigris came out
    # as the base MI): with the paks set, take the DA (and its MI/stencil/pattern deps) from them.
    from ..Shared import pak_extract
    stale=bool(pak_extract.configured_dir(scene)) and not assets.resolve(choice['reference'],assets.current_roots(roots))
    if stale or not path.is_file():
        _extract_missing(scene,[choice['reference']]);assets.invalidate()
        path=assets.resolve(choice['reference'],assets.pioneer_roots(scene))
    if not path:raise FileNotFoundError('Skin visual metadata unavailable: '+choice['label'])
    return choice,Path(path)

def _stamp_weapon(root,entry):
    """Tag the assembly as one import for the scene inventory and target resolver."""
    try:
        from ..Shared import ui_targets
    except Exception:
        return
    ui_targets.stamp([root,*root.children_recursive],ui_targets.new_import_id(),'weapon',str(entry.get('name') or root.name))

def import_weapon(context,entry,variant='OFF',*,skin=None,tier=None,location=None):
    """Synchronous import (scripts, rebuild, headless): drains ``iter_import_weapon``."""
    gen=iter_import_weapon(context,entry,variant,skin=skin,tier=tier,location=location)
    while True:
        try:next(gen)
        except StopIteration as stop:return stop.value

def iter_import_weapon(context,entry,variant='OFF',*,skin=None,tier=None,location=None):
    """Step generator: yields (stage, index, total, label) between stages, returns the root.

    Closing it at a yield (Esc in the staged run) removes the objects it created."""
    from ..Outfit_Accessories import import_layout
    yield ('extract',0,0,entry['name'])
    scene=context.scene;roots=assets.pioneer_roots(scene)
    if not str(getattr(scene,'arc_pioneer_root','') or '').strip():
        from ..Shared import pak_extract
        cache=Path(pak_extract.cache_dir())/'PioneerGame/Content/Pioneer'
        if pak_extract.configured_dir(scene) and not assets.base_visual(entry,[cache]):
            _extract_missing(scene,[f"/Game/Pioneer/Items/{entry.get('kind','Firearms')}/{entry['stem']}"])
            roots=assets.pioneer_roots(scene)
    folder=assets.weapon_folder(entry,roots)
    if not folder or not assets.base_visual(entry,roots):
        _extract_missing(scene,[f"/Game/Pioneer/Items/{entry.get('kind','Firearms')}/{entry['stem']}"])
        assets.invalidate();roots=assets.pioneer_roots(scene);folder=assets.weapon_folder(entry,roots)
    base=assets.base_visual(entry,roots)
    if not base:raise FileNotFoundError('Weapon visual data unavailable: '+entry['stem'])
    variants=assets.variants(entry,roots) if variant!='OFF' else ()
    chosen=next((v for v in variants if v['id']==variant),None)
    if variant!='OFF' and chosen is None:raise ValueError('Ascended variant no longer belongs to this gun; select Off or a listed variant')
    visual=chosen['visual'] if chosen else base
    if chosen and not assets.long_path(visual).is_file():
        _extract_missing(scene,[chosen.get('reference',visual)])
        roots=assets.pioneer_roots(scene);visual=assets.resolve(chosen.get('reference',visual),roots)
    if not visual:raise FileNotFoundError('Ascended visual metadata unavailable for '+entry['name'])
    skin=getattr(scene,'arc_weapon_skin','NONE') if skin is None else (skin or 'NONE')
    skin_choice,skin_path=_skin_visual(scene,entry,roots,skin)
    roots=assets.pioneer_roots(scene)
    if skin_choice and not chosen:
        # A skin DA inherits the base visual (ParentVisuals) and adds its own deltas.
        plan=assets.visual_plan(skin_path,roots)
    else:
        plan=assets.visual_plan(visual,roots)
        if skin_choice:
            # Ascended items share the skinnable tag; the game's composition rule is not
            # cooked data, so only the skin's own slot overrides/part deltas are applied.
            plan=assets.compose_skin(plan,skin_path,roots);plan['skin_composition']='ascended-unproven'
    defaults,roots=_default_mods(scene,entry,chosen['id'] if chosen else 'OFF',roots)
    plan=assets.with_default_mods(plan,defaults)
    level=int(getattr(scene,'arc_weapon_tier','3') if tier is None else tier)
    refs=[plan['base']]+[assets.mesh_reference(p) for p in plan['parts']]
    # Shared magazines and cartridges are also in the supplied 2.0 unpack (an older build):
    # with the paks set, extract them from the current build instead of settling for it.
    from ..Shared import pak_extract
    current=assets.current_roots(roots) if pak_extract.configured_dir(scene) else roots
    missing=[r for r in refs if r and not mesh_file(r,current)]
    if missing:
        _extract_missing(scene,missing);roots=assets.pioneer_roots(scene)
    if not mesh_file(plan['base'],roots):raise FileNotFoundError('Main weapon mesh unavailable: '+plan['base'])
    missing=[r for r in refs if r and not mesh_file(r,roots)]
    if missing:raise FileNotFoundError('Assembly mesh dependency missing: '+', '.join(missing))
    existing=set(bpy.data.objects);selection=list(context.selected_objects);active=context.view_layer.objects.active
    try:
        root=bpy.data.objects.new(entry['name']+(' · '+chosen['label'] if chosen else '')+(' · '+skin_choice['label'] if skin_choice else ''),None);scene.collection.objects.link(root)
        root['arc_weapon_assembly']=True;root['arc_weapon_key']=entry['key'];root['arc_weapon_variant']=variant
        root['arc_weapon_skin']=skin_choice['id'] if skin_choice else 'NONE'
        slot=next_slot(scene);root['arc_weapon_slot']=slot;root['arc_assembly_role']='Weapon'
        x,y=import_layout.slot_world_xy(slot,scene);root.location=(x,y,0) if location is None else location
        # Build the requested mock pose and placement anchor before importing any gun geometry.
        anchor=positions.apply(root,getattr(scene,'arc_weapon_position','HORIZONTAL'))
        path=mesh_file(plan['base'],roots)
        yield ('mesh',0,0,Path(path).name)
        objects=_import_mesh(path,context);members=set(objects)
        main=next((o for o in objects if o.type=='ARMATURE'),None)
        if not main:raise ValueError('Weapon import has no skeleton for attachment sockets')
        main['arc_source_mesh']=str(path)
        main['arc_weapon_main']=True
        main['arc_weapon_skeleton_ref']=assets.bundled().get('weapons',{}).get(entry['stem'],{}).get('skeleton','')
        for obj in objects:
            if obj.parent not in members:obj.parent=anchor
            obj['arc_weapon_owner']=root.name
        objects,path=_static_look(objects,main,plan,roots,context,root,path)
        cache={}
        skin_ref=str(skin_path) if skin_path else ''
        meshes=[o for o in objects if o.type=='MESH']
        for i,obj in enumerate(meshes,1):
            yield ('material',i,len(meshes),obj.name)
            _material(obj,path,plan['overrides'],skin_ref,cache)
        positions.align_grip(root,anchor)
        parts=plan['parts']
        for i,part in enumerate(parts,1):
            ref=assets.mesh_reference(part)
            if not ref:continue
            yield ('attach',i,len(parts),Path(ref).name)
            path=mesh_file(ref,roots);part_objects=_import_mesh(path,context)
            # An authored None socket is component space; an empty one is the root bone.
            socket=part.get('AttachmentSocket')
            _attach(part_objects,main,'weapon_root' if socket in (None,'') else socket,part,root)
            for obj in part_objects:
                obj['arc_weapon_owner']=root.name;obj['arc_weapon_part_tags']=','.join(sorted(assets.tags(part)))
                if part.get('arc_mod_slot'):obj['arc_weapon_default_mod']=assets.slot_kind(part['arc_mod_slot'])
            for obj in part_objects:
                if obj.type!='MESH':continue
                # Mesh import and material build are separate UI slices.
                yield ('attach',i,len(parts),Path(ref).name)
                _material(obj,path,plan['overrides'],skin_ref,cache)
        yield ('finish',0,0,root.name)
        set_assembly_wear(root,level)
        _bind_groups(list(root.children_recursive),main)
        for obj in context.selected_objects:obj.select_set(False)
        root.select_set(True);context.view_layer.objects.active=root
        main['arc_model_type']='weapon';root['arc_weapon_armature']=main.name
        scene.arc_last_weapon_armature=main.name;scene.arc_weapon_last_assembly=root.name
        _stamp_weapon(root,entry)
        scene['arc_weapon_last_report']=json.dumps({'weapon':entry['name'],'variant':variant,'skin':root['arc_weapon_skin'],'skin_composition':plan.get('skin_composition',''),'tier':TIER_LABELS[max(0,min(3,level))],'wear':root['arc_weapon_wear'],'parts':len(plan['parts']),'default_mods':plan.get('default_mods',[]),'slot':slot,'materials':len(cache)})
        context.view_layer.update()
        return root
    except BaseException:
        # BaseException: a closed generator (cancel) raises GeneratorExit here.
        if context.object and context.object.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
        for obj in set(bpy.data.objects)-existing:bpy.data.objects.remove(obj,do_unlink=True)
        for obj in selection:
            if obj.name in scene.objects:obj.select_set(True)
        if active and active.name in scene.objects:context.view_layer.objects.active=active
        raise

def _default_mods(scene,entry,variant,roots):
    """(ModSlots default parts of the item showing ``variant``, roots). Item, class and mod
    visual packages are not in the mesh index: extract the catalog's list, then whatever the
    cooked class chain still names. With the paks set, the supplied 2.0 unpack is skipped: its
    item classes predate ModSlots defaults (no cooked ModSlots), like its shared meshes."""
    from ..Shared import pak_extract
    current=lambda r:assets.current_roots(r) if pak_extract.configured_dir(scene) else r
    tried=set()
    for _ in range(4):
        parts,missing=assets.default_mods(entry,variant,current(roots))
        missing=[m for m in missing if m not in tried]
        if not missing:return parts,roots
        if not tried:missing=list(dict.fromkeys(missing+assets.item_packages(entry,variant)))
        tried.update(missing);_extract_missing(scene,missing);roots=assets.pioneer_roots(scene)
    return assets.default_mods(entry,variant,current(roots))[0],roots

def selected_entry(context):
    """(entry, variant) for the sidebar selection, or (None, 'OFF')."""
    entry=catalog.weapon_by_key(getattr(context.scene,'arc_weapon_key','NONE'))
    if not entry:return None,'OFF'
    variant=getattr(context.scene,'arc_weapon_ascended','OFF') or 'OFF'
    # A stale enum from another gun is the Off choice until the user selects again.
    if variant!='OFF' and variant not in {v['id'] for v in assets.variants(entry,assets.pioneer_roots(context.scene))}:variant='OFF'
    return entry,variant

def job_busy_message():
    """Text when another import job runs (D5), else ''."""
    try:
        from ..Shared import ui_targets
        if ui_targets.job_active():return ui_targets.job_busy_message() or 'Another import is running'
    except Exception:
        pass
    return ''

def selected_import(operator,context):
    busy=job_busy_message()
    if busy:operator.report({'WARNING'},busy);return {'CANCELLED'}
    entry,variant=selected_entry(context)
    if not entry:operator.report({'ERROR'},'Select a weapon first');return {'CANCELLED'}
    try:root=import_weapon(context,entry,variant)
    except Exception as exc:operator.report({'ERROR'},str(exc));return {'CANCELLED'}
    operator.report({'INFO'},'Imported '+root.name);return {'FINISHED'}

def import_mod(operator,context):
    mod_type=operator.mod_type
    prop={'Muzzle':'arc_weapon_mod_muzzle','Stock':'arc_weapon_mod_stock','Magazine':'arc_weapon_mod_magazine','UnderBarrel':'arc_weapon_mod_underbarrel'}.get(mod_type)
    if not prop:operator.report({'ERROR'},'Unsupported mod type');return {'CANCELLED'}
    entry=catalog.mod_by_key(getattr(context.scene,prop,'NONE'))
    roots=positions.roots_for_selection(context)
    if not entry or not roots:operator.report({'ERROR'},'Select an attachment and an imported weapon first');return {'CANCELLED'}
    owner=roots[0];main=next((o for o in owner.children_recursive if o.get('arc_weapon_main')),None)
    source_roots=assets.pioneer_roots(context.scene)
    folder=next((r/'Items/WeaponMod'/entry['stem'] for r in source_roots if (r/'Items/WeaponMod'/entry['stem']).is_dir()),None)
    if folder is None:
        _extract_missing(context.scene,['/Game/Pioneer/Items/WeaponMod/'+entry['stem']]);source_roots=assets.pioneer_roots(context.scene)
        folder=next((r/'Items/WeaponMod'/entry['stem'] for r in source_roots if (r/'Items/WeaponMod'/entry['stem']).is_dir()),None)
    weapon=catalog.weapon_by_key(owner['arc_weapon_key'])
    visual=assets.mod_visual(entry,weapon,source_roots)
    if not visual and not (folder and any(folder.glob('BP_Item_WeaponMod_*.json'))):
        _extract_missing(context.scene,['/Game/Pioneer/Items/WeaponMod/'+entry['stem']]);source_roots=assets.pioneer_roots(context.scene)
        visual=assets.mod_visual(entry,weapon,source_roots)
    if visual and visual.get('metadata_reference'):
        _extract_missing(context.scene,[visual['metadata_reference']]);source_roots=assets.pioneer_roots(context.scene)
        visual=assets.mod_visual(entry,weapon,source_roots)
    if not visual:
        operator.report({'ERROR'},'This mod has no compatible visual for '+weapon['name']);return {'CANCELLED'}
    ref=assets.mesh_reference(visual)
    path=mesh_file(ref,source_roots)
    if not path:
        _extract_missing(context.scene,[ref]);source_roots=assets.pioneer_roots(context.scene);path=mesh_file(ref,source_roots)
    if not path:operator.report({'ERROR'},'Mod mesh unavailable: '+entry['name']);return {'CANCELLED'}
    unique={path.stem:path}
    existing=set(bpy.data.objects)
    try:
        for path in unique.values():
            objects=_import_mesh(path,context)
            anchor=_attach(objects,main,visual.get('AttachmentSocket') or catalog.attachment_bone_for_stem(entry['stem']),visual,owner)
            while anchor is not None and anchor!=owner:anchor['arc_weapon_mod_type']=mod_type;anchor=anchor.parent
            for obj in objects:
                obj['arc_weapon_owner']=owner.name;obj['arc_weapon_mod']=entry['key'];obj['arc_weapon_mod_type']=mod_type
                if obj.type=='MESH':_material(obj,path,{})
            _bind_groups(objects,main)
        _install_mod(owner,mod_type,visual,set(bpy.data.objects)-existing)
        # Installed parts share the weapon's CustomPrimitiveData in game.
        set_assembly_wear(owner,wear=owner.get('arc_weapon_wear'))
    except Exception as exc:
        for obj in set(bpy.data.objects)-existing:bpy.data.objects.remove(obj,do_unlink=True)
        operator.report({'ERROR'},str(exc));return {'CANCELLED'}
    owner.select_set(True);context.view_layer.objects.active=owner
    operator.report({'INFO'},'Added '+entry['name']+' to '+owner.name);return {'FINISHED'}

def _install_mod(owner,mod_type,visual,new):
    """A slot holds one mod: drop the earlier mod of this type; hide the slot's default part and
    the parts the mod's ReplacesPartUsingTags name (kept, so skin checks still see the plan);
    show again what an earlier mod of this type hid and this one does not."""
    replaced=assets.tags(visual,'ReplacesPartUsingTags')
    for obj in [o for o in owner.children_recursive if o.get('arc_weapon_mod_type')==mod_type and o not in new]:
        bpy.data.objects.remove(obj,do_unlink=True)
    for obj in owner.children_recursive:
        if obj.get('arc_weapon_mod'):continue
        hide=obj.get('arc_weapon_default_mod')==mod_type or bool(replaced&set(filter(None,str(obj.get('arc_weapon_part_tags','')).split(','))))
        if hide:obj['arc_weapon_hidden_by']=mod_type
        elif obj.get('arc_weapon_hidden_by')==mod_type:del obj['arc_weapon_hidden_by']
        else:continue
        obj.hide_viewport=obj.hide_render=hide

def _assembly_meshes(owner):
    return {Path(o['arc_source_mesh']).stem.casefold() for o in owner.children_recursive
            if o.type=='MESH' and o.get('arc_source_mesh') and not o.get('arc_weapon_mod')}

def rebuild(context,owner,*,skin=None,tier=None):
    """Re-assemble one weapon with another skin/tier at the same place; returns the new root."""
    entry=catalog.weapon_by_key(owner['arc_weapon_key'])
    variant=owner.get('arc_weapon_variant','OFF')
    skin=owner.get('arc_weapon_skin','NONE') if skin is None else skin
    tier=owner.get('arc_weapon_tier',3) if tier is None else tier
    location=owner.location.copy();mode=owner.get('arc_weapon_position')
    for obj in [owner,*owner.children_recursive]:bpy.data.objects.remove(obj,do_unlink=True)
    root=import_weapon(context,entry,variant,skin=skin,tier=tier,location=location)
    if mode and mode!=root.get('arc_weapon_position'):positions.apply(root,mode)
    return root

def apply_skin(operator,context):
    """Swap MIs by slot when the skin keeps the parts; rebuild when it swaps parts."""
    owners=positions.roots_for_selection(context)
    if not owners:return None
    try:
        skin=getattr(context.scene,'arc_weapon_skin','NONE') or 'NONE'
        for owner in owners:
            entry=catalog.weapon_by_key(owner['arc_weapon_key']);roots=assets.pioneer_roots(context.scene)
            variant=owner.get('arc_weapon_variant','OFF')
            choice,skin_path=_skin_visual(context.scene,entry,roots,skin)
            roots=assets.pioneer_roots(context.scene)
            chosen=next((v for v in assets.variants(entry,roots) if v['id']==variant),None) if variant!='OFF' else None
            visual=chosen['visual'] if chosen else assets.base_visual(entry,roots)
            plan=assets.visual_plan(skin_path if choice and not chosen else visual,roots)
            if choice and chosen:plan=assets.compose_skin(plan,skin_path,roots)
            defaults,roots=_default_mods(context.scene,entry,variant if chosen else 'OFF',roots)
            plan=assets.with_default_mods(plan,defaults)
            wanted={Path(r).name.casefold() for r in [plan['base']]+[assets.mesh_reference(p) for p in plan['parts']] if r}
            if wanted!=_assembly_meshes(owner):
                had_mods=any(o.get('arc_weapon_mod') for o in owner.children_recursive)
                root=rebuild(context,owner,skin=skin)
                if had_mods:operator.report({'WARNING'},'Skin swaps parts; re-add mods to '+root.name)
                continue
            cache={}
            for obj in owner.children_recursive:
                path=obj.get('arc_source_mesh')
                if obj.type=='MESH' and path and not obj.get('arc_weapon_mod'):_material(obj,Path(path),plan['overrides'],str(skin_path or ''),cache)
            owner['arc_weapon_skin']=choice['id'] if choice else 'NONE'
        operator.report({'INFO'},'Updated weapon skins');return {'FINISHED'}
    except Exception as exc:operator.report({'ERROR'},str(exc));return {'CANCELLED'}

def apply_tier(operator,context,level):
    owners=positions.roots_for_selection(context)
    if not owners:operator.report({'ERROR'},'Select an imported weapon first');return {'CANCELLED'}
    for owner in owners:set_assembly_wear(owner,level)
    operator.report({'INFO'},'Tier '+TIER_LABELS[max(0,min(3,int(level)))]+' on '+owners[0].name);return {'FINISHED'}
