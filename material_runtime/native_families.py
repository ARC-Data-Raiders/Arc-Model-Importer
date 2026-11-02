"""Exact native family candidate adapters; no registration or canonical mutations.
Call prepare() before setup(). Rejected preparation leaves the caller's working
material intact. Verification overlays must be explicitly enabled and are reported.
"""
from dataclasses import dataclass,replace
from pathlib import Path
import hashlib,json,math,os,ntpath
from .assets import NativeAssetRepository
from .repository import package_ref,parse_material,merge_records,_exports,resolved_io_path
from .core import Expr,expr,literal,serialize
from .loaded_identity import RestartRequired

MASTERS={
 '/Game/Pioneer/MaterialLibrary/M_Presets/M_WeaponPreset_CR+NXX+WPO+ID':'weapon',
 '/Game/Pioneer/MaterialLibrary/M_Presets/M_EnemyPreset_ScanDisplay':'display',
 '/Game/Pioneer/Characters/Heads/Shared/Eyes/Materials/M_Eyelashes':'eyelashes',
 '/Game/Pioneer/Characters/Heads/Shared/Mouth/Materials/M_Teeth':'teeth',
 '/Game/Pioneer/MaterialLibrary/M_Presets/M_EnemyPreset_Masked_NAO+Detail':'enemy_shell',
 '/Game/Pioneer/MaterialLibrary/M_Presets/M_PropPreset_CR+NOX+XEM':'prop',
 '/Game/Pioneer/MaterialLibrary/M_Presets/M_BlockoutPreset_Glow':'prop',
 '/Game/Pioneer/MaterialLibrary/M_Presets/M_Tileable_Metal_CR+NXX':'prop',
 '/Game/Pioneer/MaterialLibrary/M_Presets/M_PropTrimPreset_CR+NOM+AO':'prop',
}
PROP_SUBTYPES={'/Game/Pioneer/MaterialLibrary/M_Presets/M_BlockoutPreset_Glow':'blockout_glow',
 '/Game/Pioneer/MaterialLibrary/M_Presets/M_Tileable_Metal_CR+NXX':'tileable_metal',
 '/Game/Pioneer/MaterialLibrary/M_Presets/M_PropTrimPreset_CR+NOM+AO':'prop_trim'}
PROP_SETUPS={'blockout_glow':'blockout-glow-v1-native','tileable_metal':'tileable-metal-v1-native','prop_trim':'prop-trim-v1-native'}
STATES={'SCANNING':0.,'ALERT':.5,'AGGRO':1.}

@dataclass
class Prepared:
    accepted: bool
    reason: str
    source: str=''
    family: str=''
    record: object=None
    plan: object=None
    repository: object=None
    report: object=None

def extended_windows_path_text(value):
    """Idempotent Win32 extended path syntax; pure string conversion for tests."""
    text=ntpath.normpath(os.fspath(value));slash=chr(92);prefix=slash*2+'?'+slash
    if text.startswith(prefix):return text
    if text.startswith(slash*2):return prefix+'UNC'+slash+text[2:]
    drive,tail=ntpath.splitdrive(text)
    if len(drive)==2 and drive[1]==':' and tail.startswith(slash):return prefix+text
    raise ValueError('extended Windows I/O needs an absolute drive or UNC path')

def io_path(value):
    raw=os.fspath(value)
    if os.name=='nt':raw=extended_windows_path_text(os.path.abspath(raw))
    return resolved_io_path(raw)

def normalize_mounts(mounts):
    return {mount:tuple(str(io_path(root)) for root in roots) if isinstance(roots,(tuple,list)) else str(io_path(roots)) for mount,roots in mounts.items()}

def normalize_options(options):
    options=dict(options)
    for key in ('mounts','image_mounts'):
        if options.get(key) is not None:options[key]=normalize_mounts(options[key])
    for key in ('texture_sources','verification_sources'):
        if options.get(key) is not None:options[key]={ref:str(io_path(path)) for ref,path in options[key].items()}
    return options

class VerificationRepository(NativeAssetRepository):
    def __init__(self,*args,verification_sources=None,**kwargs):
        self.verification_sources=dict(verification_sources or {});super().__init__(*args,**kwargs)
    def load_material(self,reference,_chain=()):
        reference=package_ref(reference,self.mount_aliases)
        if reference not in self.verification_sources:
            sources=self.material_sources(reference)
            if not sources or not self._is_native_json(sources[0][1],reference):raise ValueError('raw native authority missing for selected material/parent '+reference+'; refresh native sidecars, retain existing profile')
            return super().load_material(reference,_chain)
        if reference in _chain:raise ValueError('verification parent cycle')
        path=io_path(self.verification_sources[reference]);data=path.read_bytes();raw=json.loads(data.decode('utf-8-sig'))
        local=parse_material(raw,reference,({'path':str(path),'sha256':hashlib.sha256(data).hexdigest(),'kind':'explicit-verification-overlay'},),strict_identity=True)
        return merge_records(self.load_material(local.master,_chain+(reference,)),local) if local.master and local.master!=reference else local

def infer_mounts(source):
    """Only known published export directory identities establish mounts."""
    path=io_path(source)
    for content in path.parents:
        if content.name.casefold()!='content':continue
        prefix=content.parent.name
        if prefix not in ('PioneerGame','RemappedPlugins','Engine'):continue
        root=content.parent.parent
        return {'/Game':(str(root/'PioneerGame/Content'),str(root/'RemappedPlugins/Content')),'/Engine':str(root/'Engine/Content')}
    raise ValueError('unproven export root; explicit exact mounts required for plugin or flat exports')

def source_package(source,mounts):
    source=io_path(source);matches=[]
    for mount,roots in mounts.items():
        for root in roots if isinstance(roots,(list,tuple)) else (roots,):
            base=io_path(root)
            if source.is_relative_to(base):
                rel=source.relative_to(base).as_posix()
                for suffix in ('.arc-material.json','.json'):
                    if rel.endswith(suffix):rel=rel[:-len(suffix)];break
                else:raise ValueError('material source must be exact native JSON')
                matches.append(package_ref(mount+'/'+rel))
    if len(set(matches))!=1:raise ValueError('material package identity cannot be proven from mounts')
    return matches[0]

def register_array(repository,reference,texture_sources=None,native_texture_metadata=None):
    """Native mip SizeZ is authority; PNG file count is never the depth oracle."""
    reference=package_ref(reference);mapped=(texture_sources or {}).get(reference)
    projected=native_texture_metadata(reference) if native_texture_metadata else None
    if projected is not None:
        if projected.get('DataRaidersSource',{}).get('projection')!='native-catalog-texture-v1':raise ValueError('unproven texture metadata projection')
        path=None
    elif mapped:path=io_path(mapped)
    else:
        path=None
        for suffix in ('.arc-texture.json','.json'):
            try:path=repository.resolve(reference,suffix);break
            except FileNotFoundError:pass
        if path is None:raise FileNotFoundError('native Texture2DArray metadata required: '+reference)
    data=path.read_bytes() if path else json.dumps(projected,sort_keys=True).encode();raw=json.loads(data.decode('utf-8-sig'));entries=[e for e in _exports(raw) if e.get('Type')=='Texture2DArray']
    entries=[e for e in entries if (package_ref(e['Package'])==reference if e.get('Package') else e.get('Name')==reference.rsplit('/',1)[-1])]
    if len(entries)!=1:raise ValueError('exact Texture2DArray identity missing or ambiguous: '+reference)
    depths=[]
    def walk(value,in_mip=False):
        if isinstance(value,dict):
            if in_mip and 'SizeZ' in value:depths.append(value['SizeZ'])
            for key,item in value.items():walk(item,in_mip or key=='Mips')
        elif isinstance(value,list):
            for item in value:walk(item,in_mip)
    walk(entries[0]);unique=set(depths)
    if len(unique)!=1 or not depths or isinstance(depths[0],bool) or int(depths[0])!=depths[0] or not 0<int(depths[0])<=2048:raise ValueError('native mip array depth missing or inconsistent')
    refs=tuple(reference+'_LAYER'+str(i) for i in range(int(depths[0])))
    for ref in refs:repository.image_path(ref,'data')
    repository.array_slices[reference]=refs
    return {'reference':reference,'depth':len(refs),'metadata':str(path) if path else projected['DataRaidersSource'],'sha256':hashlib.sha256(data).hexdigest(),'layers':refs}

def _active(record,keys):return [key for key in keys if record.switches.get(key,False)]

def _scene_fps():
    try:
        import bpy
        render=bpy.context.scene.render
        return float(render.fps)/max(float(render.fps_base),1e-6)
    except Exception:
        return 24.

def prepare(mi_path,*,obj=None,mounts=None,image_mounts=None,texture_sources=None,native_texture_metadata=None,verification_sources=None,allow_verification_overlay=False,state=None,hair_copy=False,hair_color=None,context=None,preserve_user_override=False):
    source=str(io_path(mi_path))
    try:
        if preserve_user_override:raise ValueError('explicit user material override retained')
        if verification_sources and not allow_verification_overlay:raise ValueError('verification overlays are disabled in production')
        mounts=normalize_mounts(mounts or infer_mounts(source));image_mounts=normalize_mounts(image_mounts or mounts)
        verification_sources={ref:str(io_path(path)) for ref,path in (verification_sources or {}).items()}
        repository=VerificationRepository(mounts,image_mounts=image_mounts,verification_sources=verification_sources)
        package=source_package(source,mounts)
        # A thin transport JSON alone is not sufficient native authority.
        if package not in (verification_sources or {}):
            sources=repository.material_sources(package)
            if not sources:raise ValueError('native material source missing')
            authority=next((p for suffix,p in sources if suffix=='.arc-material.json'),sources[0][1])
            if not repository._is_native_json(authority,package):raise ValueError('flat export has no proven raw native material identity; retain existing profile')
        record=repository.load_material(package);family=MASTERS.get(record.master)
        if not family:raise ValueError('master is outside verified native family set: '+record.master)
        context=dict(context or {});arrays=[]
        uv_layers=getattr(getattr(obj,'data',None),'uv_layers',())
        uv_name=uv_layers[0].name if uv_layers else ''
        if family=='weapon':
            from .weapons import compile_weapon,WEAPON_DETAIL_ARRAY
            # Mid layers are native-verified (weapons.compile_native_weapon_paint); it refuses mid+pattern itself.
            # The pattern mask only recolours zones with Pattern - Use on; with none on it compiles out.
            patterned=any(record.switches.get('Enable '+z+' Mask',False) and record.switches.get('Pattern - Use - '+z,False) for z in ('Red','Green','Blue','Alpha'))
            blocked=_active(record,['Use Opacity','Use Triplanar Pattern']+(['Use Paintjob Pattern Mask'] if patterned else []))
            if blocked:raise ValueError('unverified active weapon features: '+','.join(blocked))
            arrays.append(register_array(repository,WEAPON_DETAIL_ARRAY,texture_sources,native_texture_metadata))
            if arrays[-1]['depth']!=3:raise ValueError('verified weapon detail shader requires native three-layer array')
            context['weapon_detail_layer_count']=arrays[-1]['depth'];plan=compile_weapon(record,context)
        elif family=='display':
            from .enemies import compile_enemy_display
            if record.switches.get('Glitch Effect',False) and 'enemy_glitch_noise_sum' not in context:raise ValueError('native glitch noise resource unavailable; retain existing profile')
            if state is not None:
                if state not in STATES:raise ValueError('unknown scan UI state')
                record=replace(record,scalars={**record.scalars,'AlertnessState':STATES[state]})
            else:
                # Per-object AlertnessState lets shared display materials differ per ARC and be keyframed.
                from .enemies import ALERTNESS_PROPERTY
                context.setdefault('enemy_alertness',expr('channel',expr('attribute',name=ALERTNESS_PROPERTY,type='OBJECT'),index=0))
            # Engine Time is View.GameTime; a frame-driven clock keeps the native sweep animated.
            context.setdefault('enemy_display_time',expr('time',animated=True,fps=_scene_fps()))
            plan=compile_enemy_display(record,context)
        elif family=='eyelashes':
            from .hair import compile_eyelashes
            if hair_copy:
                if hair_color is None or len(hair_color)<3 or not all(math.isfinite(float(x)) for x in hair_color):raise ValueError('explicit Hair Copy needs resolved finite ColorA')
                rgba=tuple(hair_color[:3])+(1.,);record=replace(record,colors={**record.colors,'Eyelash Color':rgba,'Eyelash Color 2':rgba})
            plan=compile_eyelashes(record,context)
        elif family=='teeth':
            from .teeth import compile_teeth
            if 'pre_skin_normal_z' not in context:
                from .deformation import NORMAL_BASE_VERTEX,NORMAL_SOURCE_VERSION
                mesh=getattr(obj,'data',None)
                attributes=getattr(mesh,'attributes',None)
                attribute=attributes.get(NORMAL_BASE_VERTEX) if attributes is not None else None
                if mesh is None or mesh.get(NORMAL_SOURCE_VERSION)!=1 or attribute is None or attribute.domain!='POINT' or attribute.data_type!='FLOAT_VECTOR':
                    raise ValueError('teeth requires preserved native pre-skin vertex normal source; retain existing profile')
                # UEModel's native destination basis reflects Y only, preserving Z.
                context['pre_skin_normal_z']=expr('channel',expr('attribute',name=NORMAL_BASE_VERTEX),index=2)
            plan=compile_teeth(record,context)
        elif family=='enemy_shell':
            from .decals import compile_enemy_shell
            blocked=_active(record,['Use Frost','Use Snow'])
            hides=[bool(record.switches.get('V'+str(i)+'_HideWhenDamaged',False)) for i in range(3)]
            # Each zone switch only selects whether coverage fades by |DamagePercent|; at an
            # authored 0 (MI_EnemyDecals_01: V0 off, V1/V2 on) a mixed set changes nothing.
            mixed=len(set(hides))>1 and float(record.scalars.get('DamagePercent',0.) or 0.)!=0.
            if blocked or mixed:raise ValueError('unverified shell frost/snow or mixed hide permutation')
            if record.switches.get('Parallax',False):
                if not uv_name:raise ValueError('shell parallax requires actual UV0 name')
                context['tangent_view']=expr('tangent_view',uv_map=uv_name)
            plan=compile_enemy_shell(record,context)
        elif record.master=='/Game/Pioneer/MaterialLibrary/M_Presets/M_BlockoutPreset_Glow':
            from .blockout_glow import compile_blockout_glow
            plan=compile_blockout_glow(record,context)
        elif PROP_SUBTYPES.get(record.master) in ('tileable_metal','prop_trim'):
            from .tileable_trim import COMPILERS,DETAIL_ARRAY
            # Detail tiling reads the owner's LocalToWorld axis lengths in the shader; UV Mode takes them from the owner here.
            if record.switches.get('Use Detail',False):
                arrays.append(register_array(repository,DETAIL_ARRAY,texture_sources,native_texture_metadata))
                context['detail_layer_count']=arrays[-1]['depth']
            matrix=getattr(obj,'matrix_world',None)
            if record.scalars.get('UV Mode',0.)!=0. and matrix is not None:context.setdefault('object_scale',tuple(matrix.col[i].to_3d().length for i in range(3)))
            if record.scalars.get('ConcaveNormal',0.)!=0. and 'vertex_color_green' not in context:
                colors=getattr(getattr(obj,'data',None),'color_attributes',None)
                # A mesh without a color stream reads UE's white null color buffer.
                if colors is not None and len(colors):context['vertex_color_green']=expr('channel',expr('attribute',name=colors[0].name),index=1)
            plan=COMPILERS[record.master](record,context)
        else:
            from .surfaces import compile_native_prop
            blocked=_active(record,['Use Detail','Use Dust','UseOverlay','Use Tinting','Use Swatch Colors','EnableSigns','Use Frost','Use Snow','Use Pattern Overlay','Use Slice Mask','Use single Mask Texture','Use CR as Emissive'])
            if blocked or not record.switches.get('Use Masks',False):raise ValueError('unverified prop mask/detail/overlay permutation: '+','.join(blocked))
            if record.switches.get('Pulsate',False) and not record.switches.get('ConstantPulsate',False):raise ValueError('unverified nonconstant pulse permutation')
            histogram=float(record.scalars.get('Use Histogram (0=false | 1=true)',0.))
            if histogram not in (0.,1.):raise ValueError('unverified fractional histogram permutation')
            selector=float(record.scalars.get('Mask on UVs',0.))
            if selector not in (0.,1.,2.):raise ValueError('unverified prop UV selector')
            if record.scalars.get('UV Mode',0.)!=0.:
                if obj is None:raise ValueError('prop UV mode requires actual object scale')
                context['object_scale']=tuple(obj.scale)
            plan=compile_native_prop(record,context)
        if not plan.outputs:raise ValueError('native compiler refused active features: '+';'.join(plan.diagnostics))
        images=[];seen=set()
        def visit(node):
            if not isinstance(node,Expr) or id(node) in seen:return
            seen.add(id(node))
            if node.op=='texture':
                ref=node.attrs['reference'];layer=node.attrs.get('slice')
                if layer is not None:ref=repository.array_reference(ref,int(layer))
                image=repository.image_path(ref,node.attrs.get('role','data'));images.append(str(image))
            for child in node.args:visit(child)
            visit(node.attrs.get('uv'))
        from .core import ignores_alpha_output
        for output_name,node in plan.outputs.items():
            if output_name=='alpha' and ignores_alpha_output(plan.settings):continue
            visit(node)
        subtype=PROP_SUBTYPES.get(record.master,'')
        report={'subtype':subtype,'source':source,'package':package,'master':record.master,'family':family,'diagnostics':list(plan.diagnostics),'verification_overlay':bool(verification_sources),'arrays':arrays,'images':sorted(set(images)),'native_sources':[dict(x) for x in record.provenance]}
        return Prepared(True,'verified exact master and dependencies',source,family,record,plan,repository,report)
    except (ValueError,KeyError,FileNotFoundError,NotImplementedError) as error:return Prepared(False,str(error),source,report={'status':'retain-existing-profile','reason':str(error)})

def clear_character_fallback_provenance(mat):
    """Called only after native success; stale fallback status is not live proof."""
    for key in ('dr_character_fallback','dr_character_fallback_revision','dr_character_missing_source','dr_character_fallback_note'):
        if key in mat:del mat[key]
    for key in ('arc_face_surface_setup','arc_teeth_setup','arc_eye_setup','arc_eye_aux_setup','arc_eyelash_setup'):
        if str(mat.get(key,'')).startswith('fallback-'):del mat[key]
    raw=mat.get('arc_build_report')
    try:report=json.loads(raw) if isinstance(raw,str) else dict(raw) if raw else {}
    except (TypeError,ValueError):report={}
    if isinstance(report,dict) and report.get('status')=='original-character-fallback':
        override=report.get('user_override')
        if override is None:
            if 'arc_build_report' in mat:del mat['arc_build_report']
        else:mat['arc_build_report']=json.dumps({'user_override':override})
    if str(mat.get('dr_native_route_diagnostic','')).startswith('Original character fallback active:'):
        del mat['dr_native_route_diagnostic']


def setup(mat,mi_path,mesh_path='',*,obj=None,**options):
    """Canonical setup-shaped API. False means keep the existing working graph."""
    options=normalize_options(options);mi_path=str(io_path(mi_path))
    candidate=prepare(mi_path,obj=obj,**options)
    if not candidate.accepted:return False
    try:signature=_candidate_signature(candidate)
    except RestartRequired as error:
        mat['dr_native_restart_required']=True;mat['dr_native_signature_complete']=False
        mat['dr_native_route_diagnostic']=str(error)+'; existing material retained'
        return False
    mat['dr_native_restart_required']=False
    if not signature.complete:
        mat['dr_native_route_diagnostic']='Incomplete native dependency signature; retain existing profile: '+'; '.join(signature.diagnostics)
        return False
    from .compiler import compile_plan
    compile_plan(candidate.plan,mat,obj,candidate.repository,replace=True)
    clear_character_fallback_provenance(mat)
    mat['dr_native_subtype']=candidate.report.get('subtype','')
    mat['dr_native_family']=candidate.family;mat['arc_mi_path']=str(mi_path);mat['arc_package']=candidate.record.package
    mat['dr_native_active_signature']=signature.key
    mat['dr_native_signature_complete']=True
    mat['dr_native_report']=json.dumps(candidate.report);mat['dr_native_options']=json.dumps(serialize({key:value for key,value in options.items() if key!='native_texture_metadata'}),default=str)
    if candidate.family=='display':
        from .enemies import native_alert_state
        from .core import evaluate
        alert=float(candidate.record.scalars.get('AlertnessState',0.));palette=evaluate(native_alert_state(literal(alert))[0])
        mat['arc_scan_display']=True;mat['arc_scan_state']=options.get('state') or ('AGGRO' if abs(alert-1.)<=.33 else 'ALERT' if abs(alert-.5)<=.33 else 'SCANNING')
        mat['arc_alertness_state']=alert;mat.diffuse_color=tuple(palette)+(1.,)
        mat['arc_alertness_live']=options.get('state') is None
    if candidate.family in ('eyelashes','teeth'):mat['arc_head_role']=candidate.family
    return True

def _restore_options(value):
    if isinstance(value,dict):
        if set(value)=={'op','args','attrs'}:return Expr(value['op'],tuple(_restore_options(x) for x in value['args']),_restore_options(value['attrs']))
        return {key:_restore_options(item) for key,item in value.items()}
    if isinstance(value,list):return [_restore_options(item) for item in value]
    return value

def set_scan_state(mat,state,*,obj=None,**services):
    if is_user_override(mat):return True
    if mat.get('dr_native_family')!='display':return False
    options=_restore_options(json.loads(mat.get('dr_native_options','{}')));options['state']=state;options.update(services)
    return setup(mat,mat['arc_mi_path'],obj=obj,**options)

def set_eyelash_hair_color(mat,rgba=None,*,enabled=False,obj=None,**services):
    if is_user_override(mat):return True
    if mat.get('dr_native_family')!='eyelashes':return False
    options=_restore_options(json.loads(mat.get('dr_native_options','{}')));options.update(hair_copy=bool(enabled),hair_color=list(rgba) if rgba is not None else None);options.update(services)
    return setup(mat,mat['arc_mi_path'],obj=obj,**options)


# Canonical entrypoint bridge. This version is a routing/cache recipe, independent
# of native material parameters and unrelated existing profile revisions.
NATIVE_FAMILY_RECIPE = 1

def is_user_override(mat):
    if mat.get('dr_native_user_override') or mat.get('arc_native_user_override'):return True
    # Existing surface_rebuild user requests persist explicit provenance here.
    # Never infer an override from editable node labels, colors or graph shape.
    raw=mat.get('arc_build_report')
    try:report=json.loads(raw) if isinstance(raw,str) else dict(raw) if raw else {}
    except (TypeError,ValueError):return False
    override=report.get('user_override')
    return override is True or isinstance(override,dict)


def cache_recipe_is_current(mat):
    return int(mat.get('dr_native_recipe_version',0)) == NATIVE_FAMILY_RECIPE

def _implementation_revision(family):
    # Disk changes are not evidence that new Python bodies execute. Require
    # import-time identity and retain old graphs until a full Blender restart.
    from .loaded_identity import implementation_identity
    import importlib
    files=('loaded_identity.py','native_families.py','assets.py','core.py','compiler.py','repository.py','catalog.py','defaults.py','renderer_vectors.py','charfx_common.py','surfaces_common.py',{'weapon':'weapons.py','display':'enemies.py','eyelashes':'hair.py','teeth':'teeth.py','enemy_shell':'decals.py','prop':'surfaces.py'}[family])
    if family=='prop':files+=('blockout_glow.py','tileable_trim.py')
    modules=[importlib.import_module(__package__+'.'+name[:-3]) for name in files]
    return implementation_identity(modules)


def _candidate_signature(candidate):
    from .catalog import material_build_signature
    return material_build_signature(candidate.record.package,candidate.repository,plan=candidate.plan,
        recipe={'native_family':candidate.family,'revision':NATIVE_FAMILY_RECIPE},
        implementation_revision=_implementation_revision(candidate.family),slice_suffix='_LAYER{index}')

def native_cache_is_stale(mat,mi_path,*,obj=None):
    if is_user_override(mat):return False
    if not cache_recipe_is_current(mat) or not mat.get('dr_native_active_signature'):return True
    try:
        options=_restore_options(json.loads(mat.get('dr_native_options','{}')))
        candidate=prepare(mi_path,obj=_owner_for_material(mat,obj),**options)
        if not candidate.accepted:
            if candidate.reason.startswith('master is outside verified native family set:'):return True
            mat['dr_native_signature_complete']=False
            mat['dr_native_route_diagnostic']='Existing profile retained; native cache validation unavailable: '+candidate.reason
            return False
        signature=_candidate_signature(candidate)
        if not signature.complete:
            mat['dr_native_signature_complete']=False
            mat['dr_native_route_diagnostic']='Existing profile retained; incomplete dependency validation: '+'; '.join(signature.diagnostics)
            return False
        mat['dr_native_signature_complete']=True;mat['dr_native_restart_required']=False
        return not signature.can_reuse(mat.get('dr_native_active_signature'))
    except RestartRequired as error:
        mat['dr_native_signature_complete']=False;mat['dr_native_restart_required']=True
        mat['dr_native_route_diagnostic']=str(error)+'; existing material retained'
        return False
    except Exception as error:
        mat['dr_native_signature_complete']=False
        mat['dr_native_route_diagnostic']='Existing profile retained; native dependency validation failed: '+str(error)
        return False

def _owner_for_material(mat,obj=None):
    if obj is not None and getattr(obj,'type',None)=='MESH':return obj
    import bpy
    return next((owner for owner in bpy.context.scene.objects if owner.type=='MESH' and any(slot.material==mat for slot in owner.material_slots)),None)

def _has_existing_profile(mat,expected_family=None):
    if mat.get('dr_native_family'):return True
    fields={'weapon':'arc_weapon_shader_version','enemy_shell':'arc_enemy_surface_setup','display':'arc_scan_reconstruction_version','prop':'arc_env_surface_setup','eyelashes':'arc_head_role','teeth':'arc_teeth_setup'}
    if expected_family:return bool(mat.get(fields.get(expected_family,'')))
    return any(bool(mat.get(field)) for field in fields.values())

def setup_entrypoint(mat,mi_path,mesh_path='',*,obj=None,expected_family=None,cache_family=None,state=None,hair_copy=False,hair_color=None):
    """True = successful setup OR explicit handled material retention.

    Restart-required mixed code stops caller fallback without stamping a native
    graph or new source identity. The retained material carries a diagnostic.
    Unsupported masters fall through to the existing dedicated native code.
    A metadata/permutation refusal retains an existing profile with a diagnostic;
    it never stamps a new recipe or replaces it with a generic shader.
    """
    if is_user_override(mat):return True
    obj=_owner_for_material(mat,obj)
    options={'state':state,'hair_copy':bool(hair_copy),'hair_color':hair_color}
    try:
        candidate=prepare(mi_path,obj=obj,**options)
    except Exception as error:
        mat['dr_native_route_diagnostic']='Native preparation retained existing profile: '+str(error)
        return _has_existing_profile(mat,expected_family)
    if not candidate.accepted:
        mat['dr_native_route_diagnostic']=candidate.reason
        if candidate.reason.startswith('master is outside verified native family set:'):return False
        return _has_existing_profile(mat,expected_family)
    if expected_family and candidate.family!=expected_family:return False
    try:
        success=setup(mat,mi_path,mesh_path,obj=obj,**options)
    except Exception as error:
        mat['dr_native_route_diagnostic']='Native graph retained after compilation failure: '+str(error)
        return _has_existing_profile(mat,expected_family)
    if not success:
        if mat.get('dr_native_restart_required'):return True
        if not mat.get('dr_native_route_diagnostic'):mat['dr_native_route_diagnostic']='Native dependencies changed during compilation; refresh exact metadata'
        return _has_existing_profile(mat,expected_family)
    family=candidate.family
    mat['dr_native_recipe_version']=NATIVE_FAMILY_RECIPE
    mat['dr_native_route_diagnostic']='; '.join(candidate.plan.diagnostics)
    mat['arc_mi_family']=cache_family or {'display':'scan_display','enemy_shell':'decal','prop':'environment','eyelashes':'eyelashes','teeth':'teeth','weapon':'weapon'}[family]
    if family=='weapon':mat['arc_weapon_shader_version']=2
    elif family=='enemy_shell':
        mat['arc_enemy_surface_setup']='enemy-surface-v5-native-shell';mat['arc_enemy_master']=candidate.record.master;mat['arc_enemy_build_report']=json.dumps(candidate.report)
    elif family=='display':mat['arc_scan_reconstruction_version']=5
    elif family=='prop':mat['arc_env_surface_setup']=PROP_SETUPS.get(candidate.report.get('subtype'),'packed-prop-v3-native')
    elif family=='eyelashes':mat['arc_eyelash_setup']='native-lashes-v1'
    elif family=='teeth':mat['arc_teeth_setup']='native-teeth-v1'
    return True

# Bind this module's source identity at import; overwrites require restart.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
