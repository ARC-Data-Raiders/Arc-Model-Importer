"""UEModel section-table MI references resolve per slot across folders, and outrank folder guesses (no Blender)."""
import ast,importlib.util,logging,os,sys,types,unittest
from pathlib import Path
ROOT=Path(os.environ['DATARAIDERS_TEST_REPOSITORY']) if os.environ.get('DATARAIDERS_TEST_REPOSITORY') else next(p for p in Path(__file__).resolve().parents if (p/'Shared/materials/dispatch.py').is_file())
PKG='_authored_section_probe'
HERO='/Game/Pioneer/Characters/Heroes/Celeste/Resources/Base/Materials/MI_Celeste_Base_Visor.MI_Celeste_Base_Visor'
FX='/Game/Pioneer/Effects/Systems/Weapons/EnergyShotgun/MI_FX_EnergyShotgun.MI_FX_EnergyShotgun'
FILES={HERO.split('.')[0]:'X:/cache/Heroes/MI_Celeste_Base_Visor.json',FX.split('.')[0]:'X:/cache/Effects/MI_FX_EnergyShotgun.json'}

def functions(path,*names):
    tree=ast.parse((ROOT/path).read_text(encoding='utf-8'))
    return ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[])

class Mat(dict):
    def __init__(self,name):super().__init__();self.name=name;self.users=1
    def __bool__(self):return True

class Obj(dict):
    def __init__(self,names,**props):
        super().__init__(props);self.type='MESH';self.name='SM_Probe';self.active_material_index=0
        self.material_slots=[types.SimpleNamespace(material=Mat(n)) for n in names]

class SectionSlotTests(unittest.TestCase):
    def setUp(self):
        self.built=[];self.sections=[]
        def module(name,**attrs):
            m=types.ModuleType(name);m.__path__=[];m.__dict__.update(attrs);return m
        self.modules=[module(PKG),module(PKG+'.Shared'),module(PKG+'.Shared.materials'),
            module(PKG+'.fmdex',lookup_asset_path=lambda stem:('',[]),fmdex_summary_for_report=lambda:''),
            module(PKG+'.Shared.textures',is_hair=lambda path:False,is_hair_cards_mi=lambda path:False,
                   find_asset_from_object_path=lambda path,ext:FILES.get(path.split('.')[0],''))]
        for m in self.modules:sys.modules[m.__name__]=m
        self.ns={'__package__':PKG+'.Shared.materials','os':types.SimpleNamespace(path=types.SimpleNamespace(
                    isfile=lambda path:False,dirname=os.path.dirname,join=os.path.join,basename=os.path.basename)),
                 'json':__import__('json'),'re':__import__('re'),'utils':types.SimpleNamespace(get_logger=lambda:logging.getLogger('probe')),
                 'CTX_MAP':'map','_active_dedicated_outfit_profile':lambda mat:'',
                 'material_has_map_forbidden_content':lambda mat:False,
                 'path_allowed_for_context':lambda path,ctx:not (ctx=='map' and '/characters/' in path.lower()),
                 '_resolve_mi_json_path':lambda stem,mesh,folder,context='any':'',
                 '_uemodel_section_materials':lambda path:list(self.sections),
                 '_get_or_build_shared_mi_material':lambda stem,mi,psk,slot,obj=None:self.built.append((stem,mi)) or Mat('built:'+stem)}
        exec(compile(functions('Shared/materials/dispatch.py','fix_object_materials_from_mi_slots','_mi_stem_from_blender_name'),'<dispatch>','exec'),self.ns)
    def tearDown(self):
        for m in self.modules:sys.modules.pop(m.__name__,None)
    def fix(self,obj,context='outfit'):
        return self.ns['fix_object_materials_from_mi_slots'](obj,'X:/cache/Avatar',context=context)
    def test_cross_folder_slot_mi_comes_from_section_table(self):
        self.sections=['/Engine/EngineMaterials/WorldGridMaterial.WorldGridMaterial',HERO,FX]
        obj=Obj(['WorldGridMaterial','MI_Celeste_Base_Visor','MI_FX_EnergyShotgun'],arc_psk_path='X:/cache/Avatar/SM_Probe.uemodel')
        self.assertEqual(self.fix(obj,context='any'),2)
        self.assertEqual(self.built,[('MI_Celeste_Base_Visor',FILES[HERO.split('.')[0]]),('MI_FX_EnergyShotgun',FILES[FX.split('.')[0]])])
    def test_slot_must_still_carry_the_authored_mi(self):
        # A slot another pipeline renamed or reassigned never inherits that section's reference.
        self.sections=[HERO]
        self.assertEqual(self.fix(Obj(['MI_Other_Material'],arc_psk_path='X:/cache/Avatar/SM_Probe.uemodel')),0)
        self.assertEqual(self.built,[])
    def test_exact_authored_reference_is_not_a_context_guess(self):
        # Map props refuse Characters MIs found by basename, not the mesh's own reference.
        self.sections=[HERO]
        self.assertEqual(self.fix(Obj(['MI_Celeste_Base_Visor'],arc_psk_path='X:/cache/Props/SM_Probe.uemodel'),context='map'),1)
    def test_no_section_table_keeps_previous_result(self):
        self.assertEqual(self.fix(Obj(['MI_Celeste_Base_Visor'],arc_psk_path='X:/cache/Avatar/SM_Probe.psk')),0)

class FolderGuessTests(unittest.TestCase):
    def setUp(self):
        self.sections=[]
        self.ns={'os':os,'materials':types.SimpleNamespace(_uemodel_section_materials=lambda path:list(self.sections))}
        exec(compile(functions('Shared/operators.py','_folder_mi_contradicts_sections'),'<operators>','exec'),self.ns)
    def check(self,folder_mi):
        return self.ns['_folder_mi_contradicts_sections']('X:/cache/Outpost/SM_OutpostModules_01_B.uemodel',folder_mi)
    def test_folder_mi_must_be_the_only_authored_mi(self):
        cloth='X:/cache/Outpost/MI_Outpost_Cloth_01_A.arc-material.json'
        self.sections=['/Game/Pioneer/Items/Outpost/Rooms/Module_01_B/MI_Outpost_CorrugatedMetal_01_A.MI_Outpost_CorrugatedMetal_01_A',
                       '/Game/Pioneer/Items/Outpost/Rooms/Module_01_B/MI_Outpost_Cloth_01_A.MI_Outpost_Cloth_01_A']
        self.assertTrue(self.check(cloth))
        self.sections=['/Game/Pioneer/Effects/Systems/Weapons/EnergyShotgun/MI_FX_EnergyShotgun.MI_FX_EnergyShotgun']
        self.assertTrue(self.check(cloth))
        self.sections=['/Game/Pioneer/Items/Outpost/Rooms/Module_01_B/MI_Outpost_Cloth_01_A.MI_Outpost_Cloth_01_A']*2
        self.assertFalse(self.check(cloth))
    def test_without_section_table_folder_guess_stands(self):
        self.assertFalse(self.check('X:/cache/Outpost/MI_Outpost_Cloth_01_A.json'))
        self.sections=[HERO];self.assertFalse(self.check(''))

class LayeredPartTests(unittest.TestCase):
    RADIO='/Game/Pioneer/Characters/Assets/Recruit/ToggleRadio/Skins/Metal/MI_Recruit_ToggleRadio_Metal.MI_Recruit_ToggleRadio_Metal'
    def setUp(self):
        self.sections=[];self.layered=True
        files={self.RADIO:'X:/cache/Recruit/ToggleRadio/Skins/Metal/MI_Recruit_ToggleRadio_Metal.json',
               HERO:'X:/cache/Heroes/Celeste/Resources/Base/Materials/MI_Celeste_Base_Visor.json'}
        self.ns={'os':os,'textures':types.SimpleNamespace(find_asset_from_object_path=lambda path,ext:files.get(path,'')),
                 'materials':types.SimpleNamespace(_uemodel_section_materials=lambda path:list(self.sections),
                     dispatch=types.SimpleNamespace(_mi_uses_layered_character_profile=lambda path:self.layered))}
        exec(compile(functions('Shared/operators.py','_authored_layered_part_mi'),'<operators>','exec'),self.ns)
    def part(self):
        return self.ns['_authored_layered_part_mi']('X:/cache/Items/Animation/Frontend/SM_Celeste_ToggleRadio_01.uemodel')
    def test_single_layered_skin_mi_names_the_part(self):
        self.sections=[self.RADIO,'/Engine/EngineMaterials/WorldGridMaterial.WorldGridMaterial']
        self.assertTrue(self.part().endswith('Skins/Metal/MI_Recruit_ToggleRadio_Metal.json'))
    def test_other_shapes_keep_the_generic_route(self):
        self.sections=[self.RADIO];self.layered=False
        self.assertEqual(self.part(),'')        # not a layered character MI
        self.layered=True;self.sections=[HERO]
        self.assertEqual(self.part(),'')        # hero Materials/ folder is not a part skin
        self.sections=[self.RADIO,HERO]
        self.assertEqual(self.part(),'')        # several authored MIs: per-slot resolution

class DomainTests(unittest.TestCase):
    def test_environment_folders_outside_environment(self):
        spec=importlib.util.spec_from_file_location('_asset_domain_probe',ROOT/'Outfit_Accessories/asset_domain.py')
        ad=importlib.util.module_from_spec(spec);spec.loader.exec_module(ad)
        for path in ('PioneerGame/Content/Pioneer/Items/Outpost/Rooms/Module_01_B/SM_OutpostModules_01_B.uemodel',
                     'PioneerGame/Content/Pioneer/Maps/BuriedCity_01/Cables/SM_Cable_01.uemodel',
                     'PioneerGame/Content/Pioneer/Lighting/Meshes/SM_Lightbulb_01.uemodel',
                     'PioneerGame/Content/Pioneer/MaterialLibrary/Trims/SM_EdgeTrim_01.uemodel'):
            self.assertEqual(ad.classify_asset_domain(path),ad.DOMAIN_ENVIRONMENT,path)
        self.assertEqual(ad.classify_asset_domain('PioneerGame/Content/Pioneer/Characters/Heroes/Celeste/Maps/SM_Part.uemodel'),ad.DOMAIN_OUTFIT)
        self.assertEqual(ad.classify_asset_domain('PioneerGame/Content/Pioneer/Items/Animation/Frontend/SM_Celeste_ToggleRadio_01.uemodel'),ad.DOMAIN_UNKNOWN)
if __name__=='__main__':unittest.main()
