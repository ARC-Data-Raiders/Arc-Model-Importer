"""Native fur slot/colorway selection must bypass legacy folder/name guesses."""
import ast,json,os,sys,types,unittest
from pathlib import Path
from unittest.mock import patch
from section_fixture import add_parents

class Material(dict):
    def __init__(self,name):super().__init__();self.name=name
class Object(dict):
    def __init__(self,bindings):
        super().__init__(arc_outfit_material_bindings=json.dumps(bindings))
        self.type='MESH';self.name='Fixture';self.data={'arc_uemodel_lod':'LOD0'}
        self.material_slots=[types.SimpleNamespace(material=Material('SecondFur')),types.SimpleNamespace(material=Material('FirstFur'))]


def run_hook(bindings,legacy=False,missing=None):
    source=Path(__file__).resolve().parents[1]/'Shared/materials/clothing.py'
    tree=ast.parse(source.read_text(encoding='utf-8'));node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='apply_embedded_fur_slots')
    base={0:'/native/Base_Fur0.json',1:'/native/Base_Fur1.json'};selected={**base,**{int(k):v for k,v in bindings.items()}}
    obj=Object(bindings);calls=[];legacy_calls=[]
    def forbidden(*a,**k):raise AssertionError('Native exact route reached a fuzzy folder/name resolver')
    def setup(mat,path,**kwargs):mat['arc_mi_path']=path;calls.append(path)
    def unique(obj,target,index,**kwargs):return target.material
    namespace={'__package__':'_fur_fixture.Shared.materials','os':os,'json':json,'print':lambda *args,**kwargs:None,
      '_parse_sk_material_slots':lambda _: [('FirstFur','Base_Fur0',base[0]),('SecondFur','Base_Fur1',base[1])],
      '_slot_is_fur':lambda names,path:bool(path),
      '_native_clothing_section_paths':lambda obj,sections:selected,
      '_resolve_mi_json_path':forbidden,'_match_fur_material_slot':forbidden,
      '_resolve_colorway_fur_mi':forbidden,'_ensure_unique_fur_slot_material':unique,
      '_setup_fur_material':setup,'_ensure_fur_displacement_subdiv':lambda _:None,
      '_fur_slot_kind':lambda *a:'fur',
      'textures':types.SimpleNamespace(is_fur_mi=lambda path:bool(path),authored_visual_part_materials=lambda _:[])}
    if legacy:
        namespace['_match_fur_material_slot']=lambda obj,name,i,used,stem:(obj.material_slots[i],i)
        def resolver(path,*a):legacy_calls.append(path);return '/legacy/Selected_Fur.json'
        namespace['_resolve_colorway_fur_mi']=resolver
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
    root=types.ModuleType('_fur_fixture');root.__path__=[];materials=types.ModuleType('_fur_fixture.Shared.materials');materials.__path__=[]
    fur=types.ModuleType('_fur_fixture.Shared.materials.fur_shells');fur._asset=lambda source,reference,extension:reference
    ue=types.ModuleType('_fur_fixture.Shared.uemodel_import');ue.parse_uemodel=lambda source:('mesh',10,[types.SimpleNamespace(name='LOD0',materials=[('FirstFur',base[0],0,1),('SecondFur',base[1],3,1)])])
    native=types.ModuleType('_fur_fixture.Outfit_Accessories.native_material_slots');native.load_native_material_layout=lambda *args:None;native.translate_visual_part_bindings=lambda rows,layout:(rows,[])
    modules={root.__name__:root,materials.__name__:materials,fur.__name__:fur,ue.__name__:ue,native.__name__:native};add_parents(modules,root.__name__)
    with patch.dict(sys.modules,modules),patch('os.path.isfile',side_effect=lambda path:path!=missing):
        count=namespace[node.name](obj,'source.psk' if legacy else 'source.uemodel',skin_json='/native/Selected_Fur.json')
    return obj,count,calls,legacy_calls


class NativeFurSectionTests(unittest.TestCase):
    def test_stationmaster_brown_both_exact_sections(self):
        obj,count,calls,_=run_hook({'0':'/native/Fur_Brown.json','1':'/native/Fur_Brown.json'})
        self.assertEqual(calls,['/native/Fur_Brown.json']*2);self.assertEqual(count,2)
    def test_stationmaster_black_both_exact_sections(self):
        obj,count,calls,_=run_hook({'0':'/native/Fur_Black.json','1':'/native/Fur_Black.json'})
        self.assertEqual(calls,['/native/Fur_Black.json']*2)
    def test_bells_base_override_changes_slot0_without_name_swap(self):
        obj,count,calls,_=run_hook({'0':'/native/FurBottomBase_Fur.json'})
        self.assertEqual(obj.material_slots[0].material['arc_mi_path'],'/native/FurBottomBase_Fur.json')
        self.assertEqual(obj.material_slots[1].material['arc_mi_path'],'/native/Base_Fur1.json')
    def test_bells_secondary_colorway_preserves_base_section(self):
        obj,count,calls,_=run_hook({'1':'/native/Fur_White.json'})
        self.assertEqual(calls,['/native/Base_Fur0.json','/native/Fur_White.json'])
    def test_missing_exact_override_never_borrows_base(self):
        obj,count,calls,_=run_hook({'1':'/missing/Fur_Black.json'},missing='/missing/Fur_Black.json')
        self.assertEqual(calls,['/native/Base_Fur0.json']);self.assertNotIn('arc_mi_path',obj.material_slots[1].material)
        self.assertEqual(json.loads(obj['arc_fur_binding_missing']),[{'slot':1,'path':'/missing/Fur_Black.json'}])
    def test_unbound_legacy_psk_keeps_legacy_colorway_resolution(self):
        _,count,calls,legacy=run_hook({},legacy=True)
        self.assertEqual(len(legacy),2);self.assertEqual(calls,['/legacy/Selected_Fur.json']*2)

if __name__=='__main__':unittest.main()
