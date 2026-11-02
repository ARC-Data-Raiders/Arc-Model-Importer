"""Enemy UEModel slots resolve their exact authored MI, including MIs outside the mesh folder (no Blender)."""
import ast,os,re,sys,tempfile,types,unittest
from pathlib import Path
ROOT=Path(os.environ['DATARAIDERS_TEST_REPOSITORY']) if os.environ.get('DATARAIDERS_TEST_REPOSITORY') else next(p for p in Path(__file__).resolve().parents if (p/'materials/enemy.py').is_file())
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
        self.modules=[module(PKG),module(PKG+'.materials'),
            module(PKG+'.uemodel_import',parse_uemodel=lambda path,scale=.01:('SK',10,[types.SimpleNamespace(materials=sections)])),
            module(PKG+'.materials.common',_parse_sk_material_slots=lambda path:[]),
            module(PKG+'.materials.dispatch',_get_or_build_shared_mi_material=lambda stem,mi,psk,slot,obj=None:self.built.append((stem,mi)) or {'built':mi}),
            module(PKG+'.materials.weapon',_ensure_mesh_material_slot_count=lambda obj,count:None,_match_material_slot=lambda obj,name,index,used,stem:(obj.material_slots[index],index)),
            module(PKG+'.materials.enemy_surface',ensure_enemy_sources=lambda paths:None)]
        for m in self.modules:sys.modules[m.__name__]=m
        common=ast.parse((ROOT/'materials/common.py').read_text(encoding='utf-8'))
        helper={'__package__':PKG+'.materials','os':os}
        exec(compile(ast.Module(body=[n for n in common.body if isinstance(n,ast.FunctionDef) and n.name=='_uemodel_section_materials'],type_ignores=[]),'<section table>','exec'),helper)
        sys.modules[PKG+'.materials.common']._uemodel_section_materials=helper['_uemodel_section_materials']
        source=ast.parse((ROOT/'materials/enemy.py').read_text(encoding='utf-8'))
        nodes=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='setup_enemy_material']
        textures=types.SimpleNamespace(find_asset_from_object_path=lambda path,ext:str(self.files.get(path.rsplit('.',1)[0] if '.' in path.rsplit('/',1)[-1] else path,'')))
        self.namespace={'__package__':PKG+'.materials','os':os,'re':re,'textures':textures}
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
        self.assertEqual(sys.modules[PKG+'.materials.common']._uemodel_section_materials(str(self.mesh.with_suffix('.psk'))),[])
if __name__=='__main__':unittest.main()
