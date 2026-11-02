"""Native table selectors are distinct from UEModel render-section positions."""
import copy,hashlib,importlib.util,json,os,struct,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('native_material_slots',ROOT/'native_material_slots.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
FIXTURES=Path(__file__).resolve().parent/'fixtures'

def make_layout(evidence):
    slots=[{'index':s['index'],'slot_name':s['slot'],'imported_slot_name':s['imported'],
            'material':s['material'],'export_name':s['material'].rsplit('.',1)[-1]} for s in evidence['materialSlots']]
    lods=[]
    for lod in evidence['lods']:
        sections=[{'index':s['section'],'raw_material_index':s['materialIndex'],'effective_material_index':s['materialIndex'],
            'first_index':s['BaseIndex'],'triangles':s['NumTriangles'],'material':slots[s['materialIndex']]['material'],
            'export_name':slots[s['materialIndex']]['export_name']} for s in lod['sections']]
        lods.append({'name':'LOD'+str(lod['lod']),'lod_material_map':[],'sections':sections})
    package=evidence['target'];obj='/Game/'+package.split('/Content/',1)[1];obj+='.'+package.rsplit('/',1)[-1]
    return {'schema':M.SCHEMA,'version':1,'source':{'package':package,'object_path':obj,'build_id':'25979580','depot_manifest':''},'materials':slots,'lods':lods}

class NativeMaterialSlotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=make_layout(json.loads((FIXTURES/'gwranger_native_material_slots_current.json').read_text(encoding='utf-8-sig')))
        self.path=Path(self.temp.name)/'SK_GWRanger_UpperBody.uemodel'
        def string(text):
            raw=text.encode()+b'\0';return struct.pack('<i',len(raw))+raw
        self.path.write_bytes(b'UEFORMAT'+string('UEMODEL')+bytes([10])+string('SK_GWRanger_UpperBody')+string(self.data['source']['package']+'.SK_GWRanger_UpperBody')+b'fixture-body')
        self.data['uemodel']={'relative_path':self.data['source']['package']+'.uemodel','size':self.path.stat().st_size,'sha256':hashlib.sha256(self.path.read_bytes()).hexdigest()}
        self.sidecar=self.path.with_suffix('.arc-material-slots.json')
        self.rows=[(s['export_name'],s['material'],s['first_index'],s['triangles']) for s in self.data['lods'][0]['sections']]
    def save(self):self.sidecar.write_text(json.dumps(self.data))
    def verified(self):self.save();return M.load_native_material_layout(str(self.path),'LOD0',self.rows)
    def test_actual_gwranger_four_native_slots_three_render_sections_unused_lod_slot(self):
        result=M.resolve_native_slot_records([{'slot':i,'material':'/Game/Variant'+str(i),'part':'/Game/DA_VP'} for i in range(4)],self.verified())
        self.assertEqual(result,{'0':'/Game/Variant0','1':'/Game/Variant1','2':'/Game/Variant2'})
        self.assertEqual(result.unused_native_bindings[0]['native_slot'],3)
        self.assertEqual(result.unused_native_bindings[0]['slot_name'],'UpperBody_FurLOD')
    def test_actual_outlander_three_table_slots_two_render_sections(self):
        data=make_layout(json.loads((FIXTURES/'outlander_native_material_slots_current.json').read_text(encoding='utf-8-sig')))
        result=M.resolve_native_slot_records([{'slot':1,'material':'/Game/WhiteFur'},{'slot':2,'material':'/Game/WhiteFurLOD'}],(data,data['lods'][0]))
        self.assertEqual(result,{'1':'/Game/WhiteFur'});self.assertEqual(result.unused_native_bindings[0]['native_slot'],2)
    def test_sparse_repeated_section_map_broadcasts_native_slot_to_all_matching_rows(self):
        data=copy.deepcopy(self.data);data['lods'][0]['sections'][1]['effective_material_index']=2
        result=M.resolve_native_slot_records([{'slot':2,'material':'/Game/SlotTwo'},{'slot':1,'material':'/Game/UnusedOne'}],(data,data['lods'][0]))
        self.assertEqual(result,{'1':'/Game/SlotTwo','2':'/Game/SlotTwo'});self.assertEqual(result.unused_native_bindings[0]['native_slot'],1)
    def test_equal_original_mi_paths_do_not_merge_distinct_native_slots(self):
        self.assertEqual(self.data['materials'][1]['material'],self.data['materials'][2]['material'])
        result=M.resolve_native_slot_records([{'slot':1,'material':'/Game/A'},{'slot':2,'material':'/Game/B'}],self.verified())
        self.assertEqual(result,{'1':'/Game/A','2':'/Game/B'})
    def test_named_key_uses_native_slot_name_not_exported_mi_label(self):
        result=M.resolve_native_slot_records([{'slot':'UpperBody_FurLOD','material':'/Game/LOD'}],self.verified())
        self.assertFalse(result);self.assertEqual(result.unused_native_bindings[0]['native_slot'],3)
    def test_invalid_native_slot_four_and_conflicts_still_fail(self):
        with self.assertRaisesRegex(ValueError,'native material table'):M.resolve_native_slot_records([{'slot':4,'material':'/Game/Bad'}],self.verified())
        with self.assertRaisesRegex(ValueError,'Distinct materials'):M.resolve_native_slot_records([{'slot':1,'material':'/Game/A'},{'slot':1,'material':'/Game/B'}],self.verified())
    def test_missing_metadata_returns_legacy_route_not_an_invented_mapping(self):
        self.assertIsNone(M.load_native_material_layout(str(self.path),'LOD0',self.rows))
    def test_model_hash_mismatch_is_hard_failure(self):
        self.data['uemodel']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'hash mismatch'):self.verified()
    def test_source_object_and_package_mismatch_are_hard_failures(self):
        self.data['source']['object_path']='/Game/Wrong.Wrong'
        with self.assertRaisesRegex(ValueError,'source object'):self.verified()
        self.data['source']['object_path']='/Game/'+self.data['source']['package'].split('/Content/',1)[1]+'.SK_GWRanger_UpperBody'
        self.data['source']['package']='PioneerGame/Content/Wrong'
        with self.assertRaisesRegex(ValueError,'source package'):self.verified()
    def test_section_count_range_material_and_name_mismatch_fail(self):
        good=copy.deepcopy(self.data)
        for field,value in [('first_index',3),('material','/Game/Wrong.Wrong'),('export_name','Wrong')]:
            self.data=copy.deepcopy(good);self.data['lods'][0]['sections'][0][field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'identity/range'):self.verified()
        self.data=copy.deepcopy(good);self.data['lods'][0]['sections'].pop()
        with self.assertRaisesRegex(ValueError,'section count'):self.verified()
    def test_model_replacement_at_same_path_invalidates_cached_metadata(self):
        self.verified();self.path.write_bytes(self.path.read_bytes()+b'replaced')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):M.load_native_material_layout(str(self.path),'LOD0',self.rows)

    def test_authored_lod_material_map_validated_without_inverting_missing_raw_index(self):
        self.data['lods'][0]['lod_material_map']=[0,2,2]
        self.data['lods'][0]['sections'][1]['effective_material_index']=2
        self.data['lods'][0]['sections'][1]['raw_material_index']=None
        layout=self.verified()
        self.assertIsNone(layout[1]['sections'][1]['raw_material_index'])
        result=M.resolve_native_slot_records([{'slot':2,'material':'/Game/Remapped'}],layout)
        self.assertEqual(result,{'1':'/Game/Remapped','2':'/Game/Remapped'})
        self.data['lods'][0]['sections'][1]['effective_material_index']=1
        with self.assertRaisesRegex(ValueError,'LODMaterialMap mismatch'):self.verified()

    def test_real_long_windows_model_and_sidecar_paths_are_normalized_before_io(self):
        deep=Path(self.temp.name)/('native-material-slot-long-'*5)/('extra-path-segment-'*5)
        os.makedirs(M._fs(deep),exist_ok=True)
        model=deep/self.path.name
        with open(M._fs(model),'wb') as stream:stream.write(self.path.read_bytes())
        sidecar=model.with_suffix('.arc-material-slots.json')
        self.assertGreater(len(str(sidecar)),300)
        with open(M._fs(sidecar),'w',encoding='utf8') as stream:json.dump(self.data,stream)
        layout=M.load_native_material_layout(str(model),'LOD0',self.rows)
        result=M.resolve_native_slot_records([{'slot':3,'material':'/Game/LOD'}],layout)
        self.assertEqual(result.unused_native_bindings[0]['native_slot'],3)
        self.assertEqual(M._fs(M._fs(model)),M._fs(model))
    @unittest.skipUnless(os.name=='nt','Windows UNC prefix rule')
    def test_windows_unc_path_normalized_without_network_io(self):
        slash=chr(92)
        source=slash*2+'server'+slash+'share'+slash+'mesh.uemodel'
        self.assertEqual(M._fs(source),slash*2+'?'+slash+'UNC'+slash+source[2:])

    @unittest.skipUnless(os.name=='nt','Windows importer caller guard')
    def test_long_mesh_public_resolver_enters_verified_native_sidecar_route(self):
        import ast,sys,types
        from unittest.mock import patch
        deep=Path(self.temp.name)/('public-resolver-long-'*6)/('mesh-source-folder-'*5)
        os.makedirs(M._fs(deep),exist_ok=True);model=deep/self.path.name
        with open(M._fs(model),'wb') as stream:stream.write(self.path.read_bytes())
        with open(M._fs(model.with_suffix('.arc-material-slots.json')),'w') as stream:json.dump(self.data,stream)
        tree=ast.parse((ROOT/'importing.py').read_text(encoding='utf-8-sig'))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='resolve_outfit_preset_material_bindings')
        native_calls=[]
        package=types.ModuleType('long_resolver_fixture');package.__path__=[]
        ue=types.ModuleType('long_resolver_fixture.uemodel_import')
        ue.parse_uemodel=lambda path:(native_calls.append(path) or ('mesh',10,[types.SimpleNamespace(name='LOD0',materials=self.rows)]))
        records=[{'part':'/Game/Visual','slot':0,'material':'/Game/Variant0'},{'part':'/Game/Visual','slot':3,'material':'/Game/Variant3'}]
        namespace={'os':os,'json':json,'__package__':package.__name__,
            'get_part_key':lambda path:'part','get_characters_rel_key':lambda path:'part',
            'parse_outfit_preset_slot_bindings':lambda path:{'part':records},
            '_outfit_package_key':lambda path:'same-exact-mesh','_soft_asset_path':lambda value:value['ObjectPath'],
            '_load_ue_json':lambda path:[{'Properties':{'Mesh':{'ObjectPath':'/Game/Mesh'}}}],
            'textures':types.SimpleNamespace(_fs_abspath=M._fs,find_asset_from_object_path=lambda ref,ext:'Material.json'),
            'utils':types.SimpleNamespace(normalize_part_key=lambda part:part,ue_export_entries=lambda value:value,
                get_logger=lambda:types.SimpleNamespace(info=lambda *args:None))}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'importing.py','exec'),namespace)
        original=os.path.isfile
        def exists(path):
            if path=='Material.json':return True
            if len(str(path))>260 and not str(path).startswith(chr(92)*2+'?'+chr(92)):return False
            return original(path)
        with patch.dict(sys.modules,{package.__name__:package,ue.__name__:ue,package.__name__+'.native_material_slots':M}),patch('os.path.isfile',side_effect=exists):
            bindings=namespace[function.name]('preset.json',str(model))
        self.assertEqual(native_calls,[M._fs(model)])
        self.assertEqual(set(bindings),{'0'});self.assertEqual(bindings.unused_native_bindings[0]['native_slot'],3)
class EntryAuditPreservationTests(unittest.TestCase):
    def test_initial_batch_json_roundtrip_keeps_separate_unused_audit(self):
        import ast,sys,types
        tree=ast.parse((ROOT/'operators.py').read_text(encoding='utf-8-sig'))
        names={'_entry_material_bindings','_snapshot_entry_skin_choices','_restore_entry_skin_choices'}
        namespace={'json':json,'__package__':'native_entry_fixture'}
        package=types.ModuleType('native_entry_fixture');package.__path__=[]
        sys.modules[package.__name__]=package;sys.modules[package.__name__+'.native_material_slots']=M
        for node in tree.body:
            if isinstance(node,ast.FunctionDef) and node.name in names:
                exec(compile(ast.Module(body=[node],type_ignores=[]),'operators.py','exec'),namespace)
        class Entry(dict):pass
        entry=Entry(arc_outfit_material_bindings=json.dumps({'0':'exact.json'}),arc_unused_outfit_material_bindings=json.dumps([{'native_slot':3,'material':'/Game/LOD'}]))
        entry.skin_choice='White';entry.batch_skin_json='exact.json'
        bindings=namespace['_entry_material_bindings'](entry)
        self.assertIsInstance(bindings,M.ResolvedMaterialBindings)
        self.assertEqual(bindings.unused_native_bindings,[{'native_slot':3,'material':'/Game/LOD'}])
        before=dict(entry);snapshot=namespace['_snapshot_entry_skin_choices']([entry])
        entry['arc_outfit_material_bindings']='{}';entry['arc_unused_outfit_material_bindings']='[]'
        namespace['_restore_entry_skin_choices'](snapshot)
        self.assertEqual(dict(entry),before)
        del entry['arc_unused_outfit_material_bindings']
        self.assertIs(type(namespace['_entry_material_bindings'](entry)),dict)

class DefaultVisualPartIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.data=make_layout(json.loads((FIXTURES/'gwranger_native_material_slots_current.json').read_text(encoding='utf-8-sig')))
        self.data['lods'][0]['sections'][1]['effective_material_index']=2
    def test_raw_da_native_selector_broadcasts_sparse_rows_without_mutating_source(self):
        raw=[{'slot':'2','path':'ExactDefault.json','material_path':'/Game/Default','visual_part':'/Game/DA_VP'}]
        before=copy.deepcopy(raw)
        translated,unused=M.translate_visual_part_bindings(raw,(self.data,self.data['lods'][0]))
        self.assertEqual([row['render_index'] for row in translated],[1,2])
        self.assertEqual([row['native_slot'] for row in translated],[2,2])
        self.assertFalse(unused);self.assertEqual(raw,before)
    def test_unmatched_default_da_bank_keys_preserve_legacy_exact_matching_without_native_assignment(self):
        for key,native_name in [('HelmetmaskUp','Helmetmask'),('Metal','Watch')]:
            data=copy.deepcopy(self.data)
            data['materials'][0]['slot_name']=native_name;data['materials'][0]['imported_slot_name']=''
            binding={'slot':key,'path':'ExactBank.json','material_path':'/Game/ExactBank','visual_part':'/Game/ExactDA'}
            rows,diagnostics=M.translate_visual_part_bindings([binding],(data,data['lods'][0]))
            self.assertEqual(rows,[binding]);self.assertIsNot(rows[0],binding)
            self.assertNotIn('render_index',rows[0]);self.assertNotIn('native_slot',rows[0])
            self.assertEqual(diagnostics[0]['authored_key'],key)
            self.assertIn('legacy exact exported-label',diagnostics[0]['reason'])
    def test_ambiguous_default_bank_key_does_not_choose_a_native_slot(self):
        data=copy.deepcopy(self.data)
        for slot in data['materials']:slot['slot_name']='Shared';slot['imported_slot_name']=''
        rows,diagnostics=M.translate_visual_part_bindings([{'slot':'Shared','path':'Bank.json'}],(data,data['lods'][0]))
        self.assertNotIn('render_index',rows[0]);self.assertEqual(len(diagnostics),1)
    def test_numeric_default_da_selector_remains_strict_and_unused_is_zero_assignment(self):
        layout=(self.data,self.data['lods'][0])
        with self.assertRaisesRegex(ValueError,'exceeds native material table'):
            M.translate_visual_part_bindings([{'slot':'4','path':'Invalid.json'}],layout)
        rows,unused=M.translate_visual_part_bindings([{'slot':'3','path':'LOD.json'}],layout)
        self.assertEqual(rows,[]);self.assertEqual(unused[0]['native_slot'],3)
    def test_real_three_consumer_predicates_use_render_index_before_legacy_name(self):
        import ast
        targets=[('materials/clothing.py','_native_clothing_sections'),('materials/clothing.py','apply_embedded_fur_slots'),('materials/character.py','setup_hair_material')]
        for path,name in targets:
            tree=ast.parse((ROOT/path).read_text(encoding='utf-8-sig'))
            function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            self.assertTrue(any(isinstance(n,ast.Name) and n.id=='translate_visual_part_bindings' for n in ast.walk(function)),name)
            predicate=next(n for n in ast.walk(function) if isinstance(n,ast.IfExp) and 'render_index' in ast.unparse(n))
            code=compile(ast.Expression(predicate),str(ROOT/path),'eval')
            context={'binding':{'slot':'SameLabel','render_index':2},'section':{'index':1,'slot':'SameLabel'},'index':1,'slot':'SameLabel'}
            self.assertFalse(eval(code,context),name+' must not match alias on different render index')
            context.update(section={'index':2,'slot':'OtherLabel'},index=2,slot='OtherLabel')
            self.assertTrue(eval(code,context),name+' must use exact numeric render index')
            context['binding']={'slot':'OtherLabel'}
            self.assertTrue(eval(code,context),name+' keeps legacy named route when metadata absent')
    def test_explicit_already_render_preset_override_is_not_double_mapped(self):
        import ast,sys,types
        tree=ast.parse((ROOT/'materials/clothing.py').read_text(encoding='utf-8-sig'))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_native_clothing_section_paths')
        namespace={'json':json,'__package__':'slot_merge_fixture.materials'}
        package=types.ModuleType('slot_merge_fixture');package.__path__=[]
        materials=types.ModuleType('slot_merge_fixture.materials');materials.__path__=[]
        operators=types.ModuleType('slot_merge_fixture.operators');operators._recover_saved_material_lookup=lambda obj,path,stamp_mesh=False:path
        sys.modules[package.__name__]=package;sys.modules[materials.__name__]=materials;sys.modules[operators.__name__]=operators
        exec(compile(ast.Module(body=[function],type_ignores=[]),'clothing.py','exec'),namespace)
        translated,_=M.translate_visual_part_bindings([{'slot':2,'path':'Default.json','material_path':'/Game/Default'}],(self.data,self.data['lods'][0]))
        sections=[{'index':i,'path':'Native'+str(i)} for i in range(3)]
        for binding in translated:sections[binding['render_index']]['path']=binding['path']
        obj={'arc_outfit_material_bindings':json.dumps({'2':'PresetRenderTwo.json'})}
        paths=namespace[function.name](obj,sections)
        self.assertEqual(paths[1],'Default.json');self.assertEqual(paths[2],'PresetRenderTwo.json')

class RawVisualPartSelectorTests(unittest.TestCase):
    def test_actual_da_parser_preserves_native_numeric_zero(self):
        import ast,types
        tree=ast.parse((ROOT/'textures.py').read_text(encoding='utf-8-sig'))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='authored_visual_part_materials')
        def stem(ref):return str(ref.get('ObjectPath') or ref.get('AssetPathName') or '').rsplit('/',1)[-1].split('.')[0]
        namespace={'os':os,'json':json,'_fs_abspath':os.path.abspath,'mi_stem_from_material_ref':stem,
            'find_asset_from_object_path':lambda ref,ext:'ExactDefault.json','resolve_part_mi_json':lambda path,name:path,
            'utils':types.SimpleNamespace(ue_export_entries=lambda data:data['Exports'])}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'textures.py','exec'),namespace)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'DA_VP_Raw.json'
            path.write_text(json.dumps({'Exports':[{'Properties':{'Mesh':{'ObjectPath':'/Game/SK_Raw.SK_Raw'},
                'MaterialOverrides':[{'Key':0,'Value':{'ObjectPath':'/Game/Default.Default'}}]}}]}))
            bindings=namespace[function.name](str(Path(directory)/'SK_Raw.uemodel'))
        self.assertEqual(bindings[0]['slot'],'0')

if __name__=='__main__':unittest.main()

