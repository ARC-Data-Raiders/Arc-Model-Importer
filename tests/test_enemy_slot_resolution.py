"""Enemy UEModel slots resolve their exact authored MI, including MIs outside the mesh folder (no Blender)."""
import ast,os,re,sys,tempfile,types,unittest
from unittest.mock import Mock, call
from pathlib import Path
ROOT=Path(os.environ['DATARAIDERS_TEST_REPOSITORY']) if os.environ.get('DATARAIDERS_TEST_REPOSITORY') else next(p for p in Path(__file__).resolve().parents if (p/'Shared/materials/enemy.py').is_file())
PKG='_enemy_slot_probe'
class EnemySlotResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();base=Path(self.tmp.name)
        self.mesh=base/'Enemies/LightDrone/Wasp/Art/SK_LightDrone_01_A.uemodel'
        self.files={'/Game/Pioneer/Enemies/Shared/Art/LightAndEliteDrone/MI_Lightdrone_SK_01':base/'Enemies/Shared/Art/LightAndEliteDrone/MI_Lightdrone_SK_01.json',
                    '/Game/Pioneer/MaterialLibrary/Material_Instances/Enemies/MI_EnemyScanDisplay_01_A':base/'MaterialLibrary/MI_EnemyScanDisplay_01_A.json'}
        for path in (self.mesh,*self.files.values()):path.parent.mkdir(parents=True,exist_ok=True);path.write_text('[]')
        self.built=[];sections=[('MI_Lightdrone_SK_01','/Game/Pioneer/Enemies/Shared/Art/LightAndEliteDrone/MI_Lightdrone_SK_01.MI_Lightdrone_SK_01',0,9),
                                 ('MI_EnemyScanDisplay_01_A','/Game/Pioneer/MaterialLibrary/Material_Instances/Enemies/MI_EnemyScanDisplay_01_A.MI_EnemyScanDisplay_01_A',27,2)]
        def module(name,**attrs):
            m=types.ModuleType(name);m.__path__=[];m.__dict__.update(attrs);return m
        self.modules=[module(PKG),module(PKG+'.Shared.materials'),
            module(PKG+'.Shared.uemodel_import',parse_uemodel=lambda path,scale=.01:('SK',10,[types.SimpleNamespace(materials=sections)])),
            module(PKG+'.Shared.materials.common',_parse_sk_material_slots=Mock(return_value=[])),
            module(PKG+'.Shared.materials.dispatch',_get_or_build_shared_mi_material=lambda stem,mi,psk,slot,obj=None:self.built.append((stem,mi)) or {'built':mi}),
            module(PKG+'.Shared.materials.weapon',_ensure_mesh_material_slot_count=lambda obj,count:None,_match_material_slot=lambda obj,name,index,used,stem:(obj.material_slots[index],index)),
            module(PKG+'.Shared.materials.enemy_surface',ensure_enemy_sources=lambda paths:None)]
        for m in self.modules:sys.modules[m.__name__]=m
        common=ast.parse((ROOT/'Shared/materials/common.py').read_text(encoding='utf-8'))
        helper={'__package__':PKG+'.Shared.materials','os':os}
        exec(compile(ast.Module(body=[n for n in common.body if isinstance(n,ast.FunctionDef) and n.name=='_uemodel_section_materials'],type_ignores=[]),'<section table>','exec'),helper)
        sys.modules[PKG+'.Shared.materials.common']._uemodel_section_materials=helper['_uemodel_section_materials']
        source=ast.parse((ROOT/'Shared/materials/enemy.py').read_text(encoding='utf-8'))
        nodes=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in ('setup_enemy_material','_adopt_slot_name')]
        textures=types.SimpleNamespace(find_asset_from_object_path=lambda path,ext:str(self.files.get(path.rsplit('.',1)[0] if '.' in path.rsplit('/',1)[-1] else path,'')))
        self.namespace={'__package__':PKG+'.Shared.materials','os':os,'re':re,'textures':textures,'bpy':sys.modules.get('bpy')}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<enemy slot resolution>','exec'),self.namespace)
    def tearDown(self):
        for m in self.modules:sys.modules.pop(m.__name__,None)
        self.tmp.cleanup()
    def obj(self):
        class Mat(dict):
            def __init__(self,name):super().__init__();self.name=name
            def __bool__(self):return True
        slots=[types.SimpleNamespace(name=n,material=Mat(n)) for n in ('MI_Lightdrone_SK_01','MI_EnemyScanDisplay_01_A')]
        return types.SimpleNamespace(type='MESH',material_slots=slots)
    def test_shared_folder_mi_comes_from_uemodel_section_table(self):
        obj=self.obj();self.assertEqual(self.namespace['setup_enemy_material'](obj,str(self.mesh)),2)
        self.assertEqual([(stem,Path(mi)) for stem,mi in self.built],[('MI_Lightdrone_SK_01',self.files['/Game/Pioneer/Enemies/Shared/Art/LightAndEliteDrone/MI_Lightdrone_SK_01']),
                                                                     ('MI_EnemyScanDisplay_01_A',self.files['/Game/Pioneer/MaterialLibrary/Material_Instances/Enemies/MI_EnemyScanDisplay_01_A'])])
    def test_sibling_mi_still_wins(self):
        sibling=self.mesh.parent/'MI_Lightdrone_SK_01.json';sibling.write_text('[]')
        self.namespace['setup_enemy_material'](self.obj(),str(self.mesh));self.assertEqual(Path(self.built[0][1]),sibling)
    def test_non_uemodel_source_has_no_section_table(self):
        self.assertEqual(sys.modules[PKG+'.Shared.materials.common']._uemodel_section_materials(str(self.mesh.with_suffix('.psk'))),[])
    def test_complete_primary_sections_do_not_discover_legacy_metadata(self):
        parser=sys.modules[PKG+'.Shared.materials.common']._parse_sk_material_slots
        self.assertEqual(self.namespace['setup_enemy_material'](self.obj(),str(self.mesh)),2)
        parser.assert_called_once_with(str(self.mesh),allow_fallback=False)
    def test_missing_primary_material_keeps_legacy_fallback(self):
        parser=sys.modules[PKG+'.Shared.materials.common']._parse_sk_material_slots
        source=self.files['/Game/Pioneer/Enemies/Shared/Art/LightAndEliteDrone/MI_Lightdrone_SK_01']
        source.unlink()
        fallback=source.parent/'MI_Legacy.json';fallback.write_text('[]')
        parser.side_effect=[[],[('Legacy','MI_Legacy',str(fallback))]]
        self.assertEqual(self.namespace['setup_enemy_material'](self.obj(),str(self.mesh)),1)
        self.assertEqual(parser.call_args_list,[call(str(self.mesh),allow_fallback=False),call(str(self.mesh))])
        self.assertEqual(self.built,[('MI_Legacy',str(fallback))])
    def test_local_metadata_keeps_precedence_over_inferred_sections(self):
        parser=sys.modules[PKG+'.Shared.materials.common']._parse_sk_material_slots
        fallback=self.mesh.parent/'MI_LocalOverride.json';fallback.write_text('[]')
        parser.return_value=[('Local','MI_LocalOverride',str(fallback))]
        self.assertEqual(self.namespace['setup_enemy_material'](self.obj(),str(self.mesh)),1)
        parser.assert_called_once_with(str(self.mesh),allow_fallback=False)
        self.assertEqual(self.built,[('MI_LocalOverride',str(fallback))])
if __name__=='__main__':unittest.main()
