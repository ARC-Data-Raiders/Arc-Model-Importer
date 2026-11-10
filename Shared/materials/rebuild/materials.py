"""One material pipeline with explicit family profiles and an inspectable build report."""
import hashlib,json,traceback
import bpy
from .graph import Graph
from .native_catalog_bridge import begin as native_begin, lookup as native_lookup, stamp as native_stamp
from .character import build_character
from .surfaces import build_surface, build_architecture, build_environment_layers, build_weapon
from .records import MaterialLibrary
from .trim import build_trim
from .enemy_tile import build_enemy_tile
from .vehicle import build_vehicle
from .decals import build_decal, build_window, build_translucent_glass
from .prop_trim import build_prop_trim, build_architecture_trim
from .heads import build_head
from .body_skin import build_body_skin
from .hair import build_hair
from .clouds import build_fx_cloud
from .fx_beams import build_fx_fogsheet, build_fx_lightbeam
from .background_decal import build_background_decal
from .glass_opaque import build_glass_opaque
from .sand_dune import build_sand_dune
from .lightbulb import build_lightbulb
from .snow_pile import build_snow_pile
from .fzt_snow import build_fzt_snow
from .fx_invisible import build_fx_invisible


def build_material(obj, record, index):
    scan_display = (record['package'].rsplit('/', 1)[-1].casefold().startswith('mi_enemyscandisplay')
                    or str(record.get('master', '')).casefold().endswith('m_enemypreset_scandisplay'))
    shader_record = {k:record.get(k) for k in ['package','master','textures','scalars','colors','switches','settings','family']}
    if record['family']=='body_skin':
        # Clothing changes the same shared skin instance's coverage parameters.
        # Include them so two outfits cannot borrow each other's body masks.
        shader_record.update({k:record.get(k) for k in ['body_masks','body_hidden','body_gender','body_mask_sources']})
    from .emission_preview import applies_to_cache, RECIPE as EMISSION_PREVIEW_RECIPE
    if applies_to_cache(record):
        shader_record['generic_emission_preview_recipe'] = EMISSION_PREVIEW_RECIPE
    if scan_display:
        # Native SM5 display replaced the earlier user-requested #FF6666 preview.
        shader_record['scan_display_recipe'] = 2
    if record.get('native_source_mode')=='fmodel-compatibility':
        from ..native.native_data import compatibility_signature
        shader_record['compatibility_source_signature']=compatibility_signature(record,index)
    # These recovered profiles bake primitive scale into their UV/detail controls.
    # A graph built for one scale cannot be reused by a differently scaled object.
    imported_scale = [float(abs(v)) for v in obj.matrix_world.to_scale()] if record['family'] in {'prop_trim','architecture_trim'} else None
    # Local paint coordinates are stored as metres in UEModel and centimetres
    # in PSK. Camera distance also needs the importing scene's unit conversion.
    paint_units = None
    if record['family'] == 'architecture' and record.get('switches', {}).get('Use Paint'):
        paint_units = {'local': float(obj.data.get('arc_uemodel_scale', 1)),
                       'world': float(obj.get('arc_ue_unit_scale', obj.data.get('arc_uemodel_scale', .01)))}
    key = hashlib.sha256(json.dumps({'record':shader_record, 'uv':[u.name for u in obj.data.uv_layers],
                                    'colors':[a.name for a in obj.data.color_attributes],
                                    'imported_scale':imported_scale, 'paint_units':paint_units,
                                    'root':str(index.root), 'version':'1.5.0', 'surface_profile_revision':3, 'graph_backend_revision':'dr-private-stages-v1'},sort_keys=True).encode()).hexdigest()
    native_owner = native_begin(index, record)
    if native_owner is None:
        existing = next((m for m in bpy.data.materials if m.get('arc_build_key') == key), None)
    else:
        existing, _ = native_lookup(native_owner, bpy.data.materials, key, record['package'])
    if existing:
        report = json.loads(existing.get('arc_build_report', '{}'))
        report['skin_selection'] = record.get('skin_selection',report.get('skin_selection','authored material'))
        return existing, report
    mat = bpy.data.materials.new(record['name'])
    report = {'object': obj.name, 'material': record['name'], 'package': record['package'],
              'master': record.get('master', ''), 'family': record['family'], 'missing_textures': [], 'warnings': [],
              'skin_selection': record.get('skin_selection', 'authored material'), 'engine_parity_verified': False}
    report['warnings'].extend(record.get('native_source_diagnostics',()))
    native_tracker = native_owner.tracker() if native_owner is not None else None
    g = Graph(mat, obj, native_tracker if native_tracker is not None else index, report)
    builders = {'character':build_character, 'surface':build_surface, 'architecture':build_architecture,
                'environment_layers':build_environment_layers, 'weapon':build_weapon, 'trim':build_trim,
                'enemy_tile':build_enemy_tile, 'vehicle':build_vehicle, 'decal':build_decal,
                'prop_trim':build_prop_trim, 'architecture_trim':build_architecture_trim,'window':build_window,'translucent_glass':build_translucent_glass,'hair':build_hair,'body_skin':build_body_skin,'fx_cloud':build_fx_cloud,'background_decal':build_background_decal,'glass_opaque':build_glass_opaque,'sand_dune':build_sand_dune,'snow_pile':build_snow_pile,'fzt_snow':build_fzt_snow,'fx_invisible':build_fx_invisible,'lightbulb':build_lightbulb,'fx_lightbeam':build_fx_lightbeam,'fx_fogsheet':build_fx_fogsheet,
                **{k:build_head for k in ['head_face','head_eyes','head_teeth','head_eyelashes','head_occlusion','head_tearline','head_lacrimal']}}
    try:
        if scan_display:
            # Game-authored SM5 ScanDisplay: palette, LED cells and sweep from bytecode, state from
            # the object's AlertnessState property, frame-driven time.
            from ..enemy_scan import setup_enemy_scan_display_material
            setup_enemy_scan_display_material(mat, record.get('source_json', ''), obj=obj)
            if g.tree.users == 0:
                bpy.data.node_groups.remove(g.tree)
            report['family'] = 'scan_display'
            report['implemented_stages'] = ['native ScanDisplay palette/cells/sweep', 'per-object AlertnessState', 'animated time']
        else:
            labels = {'character':'Character layers and surface response', 'trim':'Trim, tiled surface and overlays',
                      'architecture':'Architecture surface response', 'weapon':'Weapon zones and surface response',
                      'environment_layers':'Environment layers and blend', 'surface':'Packed textures and surface response',
                      'enemy_tile':'Enemy panels, wear and leaks', 'vehicle':'Vehicle paint, masks and surface response',
                      'decal':'Decal palette, masks and opacity', 'prop_trim':'Trim colour masks and detail response',
                      'architecture_trim':'Concrete trim colour, opacity and normals'}
            labels.update(head_face='Skin, stubble and face response',head_eyes='Iris, sclera and eye response',
                          head_teeth='Shared mouth textures',head_eyelashes='Eyelash colours and opacity',
                          head_occlusion='Eye occlusion and clear shell',head_tearline='Wet tear line',head_lacrimal='Lacrimal fluid',
                          hair='Hair strands, root colour and coverage',body_skin='Body skin and outfit coverage',
                          fx_cloud='Cloud light-vector scatter and opacity',
                          background_decal='Grunge overlay colour and opacity',
                          glass_opaque='Opaque glass fresnel, dirt and metallic ramp',
                          sand_dune='Sand colour (RVT stand-in), normal and roughness',
                          snow_pile='Compiled snow colour, RNM detail normals and roughness',
                          fzt_snow='World-projected snow normals, grazing colour and roughness',
                          fx_invisible='Effect with no base-pass colour (invisible)',
                          lightbulb='Glass base and bulb emissive scaled by LightIntensity',
                          fx_lightbeam='Unlit beam glow, reflection falloff and fades',
                          fx_fogsheet='Unlit fog sheet distance and facing opacity')
            with g.block(labels.get(record['family'],'Surface response')) as stage:
                builders.get(record['family'], build_surface)(stage, record)
    except Exception:
        bpy.data.materials.remove(mat)
        raise
    if record['family'] == 'fallback' and 'user_override' not in report:
        report['warnings'].append('Unknown master material; generic fallback used')
    if record['family'] == 'character_preview':
        report['warnings'].append('Preview material selected; enable character skins for the layered outfit appearance')
    report['warnings'] = list(dict.fromkeys(report['warnings']))
    mat['arc_build_key'] = key
    mat['arc_package'] = record['package']
    mat['arc_source_json'] = record.get('source_json', '')
    mat.use_backface_culling = not bool(record['settings'].get('TwoSided', record['settings'].get('bTwoSided', False)))
    blend = str(record['settings'].get('BlendMode', ''))
    if 'Masked' in blend or blend == '1':
        mat.surface_render_method = 'DITHERED'
    if 'Translucent' in blend or blend in {'2', '3', '4'}:
        mat.surface_render_method = 'DITHERED'
        if not record['family'].startswith('head_') and record['family'] not in ('fx_cloud', 'background_decal', 'fx_lightbeam', 'fx_fogsheet', 'fx_invisible'):
            report['warnings'].append('Translucent/refraction shader response uses a generic V1 approximation')
    if record['family']=='hair':
        mat.surface_render_method='DITHERED';mat.use_backface_culling=False
    if record['family'] in ('window','translucent_glass','fx_cloud','background_decal','fx_lightbeam','fx_fogsheet','fx_invisible'):
        mat.surface_render_method='BLENDED'
        mat.use_backface_culling=False
    if native_owner is not None:
        try:
            native_stamp(native_owner, mat, key, record['package'], native_tracker, report)
        except Exception:
            bpy.data.materials.remove(mat)
            raise
    mat['arc_build_report'] = json.dumps(report, ensure_ascii=False)
    return mat, report


def texture_objects(objects, index, skin_filter='', source_path='', use_skins=True, material_selector=None, preserve_material=None):
    lib = MaterialLibrary(index)
    reports = []
    for obj in objects:
        if obj.type != 'MESH': continue
        mesh_source = source_path or obj.get('arc_source_mesh', '')
        if mesh_source: obj['arc_source_mesh'] = mesh_source
        for i, slot in enumerate(obj.material_slots):
            old = slot.material
            if old is None: continue
            try:
                # The importer can supply an explicitly supported native profile.
                # Never replace that graph with the older generic reconstruction.
                if preserve_material is not None and material_selector is None and not use_skins:
                    kept = preserve_material(obj, i, old, index)
                    if kept is not None:
                        reports.append(dict(kept, object=obj.name, slot=i))
                        continue
                if old.get('arc_package'):
                    record = lib.load(old['arc_package'])
                    if material_selector is None and use_skins and skin_filter and record['family'] == 'character':
                        record = lib.choose_skin(record, skin_filter)
                else:
                    name = old.get('arc_original_slot_name', old.name)
                    try:record = lib.slot(name, mesh_source)
                    except ValueError:
                        if material_selector is None:raise
                        # Exact DA_OI/DA_VP slot references can identify the
                        # authored material even when a preview JSON is absent.
                        record=None
                if material_selector is not None:
                    record = material_selector(lib, obj, i, record)
                elif record['family'] == 'character_preview' and use_skins:
                    record = lib.choose_skin(record, skin_filter)
                if material_selector is None and obj.get('arc_assembly_material_overrides'):
                    record=dict(record)
                    overrides=json.loads(obj['arc_assembly_material_overrides']).get(str(i),{})
                    for category,values in overrides.items():record[category]=dict(record.get(category,{}),**values)
                if record['family']=='body_skin' and material_selector is None and obj.get('arc_body_masks'):
                    record=dict(record,body_masks=json.loads(obj['arc_body_masks']),
                        body_mask_sources=json.loads(obj.get('arc_body_mask_sources','[]')),
                        body_hidden=bool(obj.get('arc_body_hidden',False)),body_gender=obj.get('arc_body_type','DEFAULT').upper())
                mat, report = build_material(obj, record, index)
                # Per-object slot links preserve other objects sharing the same mesh datablock.
                slot.link = 'OBJECT'
                slot.material = mat
                from .spline_thicken import apply as spline_thicken_apply
                spline_thicken_apply(obj, mat, record, report, slot=i)
                item = dict(report, object=obj.name, slot=i)
                if record.get('resolution_note'):item.setdefault('warnings',[]).append(record['resolution_note'])
                for name in ['character_customization','colorway_package','visual_parts']:
                    if name in record: item[name] = record[name]
                if record.get('scalp_templates'):item['scalp_templates']=record['scalp_templates']
                reports.append(item)
            except Exception as exc:
                reports.append({'object':obj.name, 'slot':i, 'material':old.name, 'source_mesh':mesh_source,
                                'error':str(exc),'error_type':type(exc).__name__,'traceback':traceback.format_exc()})
    write_report(reports)
    return reports


def write_report(reports, character=None):
    text = bpy.data.texts.get('ARC Texturer Report') or bpy.data.texts.new('ARC Texturer Report')
    data = {'version':'1.7.3','materials':reports}
    if character is not None: data['character_import'] = character
    text.clear(); text.write(json.dumps(data,indent=2,ensure_ascii=False))
