"""Shared MI slots resolve by stamped source path, not by the Blender datablock name (.001/.012)."""
try:
    from . import native_test_support as s
except ImportError:
    import native_test_support as s
import ast,importlib,sys,types,unittest
from pathlib import Path

REBUILD=s.PACKAGE+'.Shared.materials.rebuild'

def rebuild(name):
    s.real_package('Shared.materials.native')
    if REBUILD not in sys.modules:
        m=types.ModuleType(REBUILD);m.__path__=[str(s.ROOT/'Shared/materials/rebuild')];sys.modules[REBUILD]=m
    return importlib.import_module(REBUILD+'.'+name)

def material_helpers():
    path=s.ROOT/'Shared/materials/rebuild/materials.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    names={'source_package','load_by_source'}
    body=[x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name in names]
    if {x.name for x in body}!=names:raise AssertionError('source identity helpers missing from rebuild/materials.py')
    module=types.ModuleType(REBUILD+'._slot_identity');module.__package__=REBUILD
    exec(compile(ast.fix_missing_locations(ast.Module(body=body,type_ignores=[])),str(path),'exec'),module.__dict__)
    return module

class FakeLib:
    def __init__(self,index):self.index=index;self.loaded=[]
    def load(self,key):
        if not self.index.resolve(key,'.json'):raise ValueError('Material metadata missing: '+key)
        self.loaded.append(key);return {'name':key.rsplit('/',1)[-1],'family':'environment'}

class SlotSourceIdentity(unittest.TestCase):
    def setUp(self):
        self.assets=rebuild('assets');self.m=material_helpers()
        self.tmp=s.temp_fixture();root=Path(self.tmp.name)/'export'
        (root/'PioneerGame/Content/Other').mkdir(parents=True)
        self.index=self.assets.AssetIndex(root)
        # Written after the index walk: a session-cached inventory predates this prop.
        self.mi=s.write_json(root/'PioneerGame/Content/Environment/Res/MI_Res_Stucco_01.json',[{'Type':'MaterialInstanceConstant','Name':'MI_Res_Stucco_01'}])
    def tearDown(self):self.tmp.cleanup()
    def check(self,blender_name):
        mat={'arc_mi_path':str(self.mi.resolve())}
        lib=FakeLib(self.index)
        self.assertIsNone(self.index.resolve(blender_name,'.json'))
        record=self.m.load_by_source(lib,mat,self.index)
        self.assertIsNotNone(record,blender_name)
        self.assertEqual(lib.loaded,['PioneerGame/Content/Environment/Res/MI_Res_Stucco_01'])
    def test_suffix_001(self):self.check('MI_Res_Stucco_01.001')
    def test_suffix_012(self):self.check('MI_Res_Stucco_01.012')
    def test_source_wins_over_unrelated_name(self):self.check('MI_BuriedCity_Whitebox_01_Metal.012')
    def test_no_stamp_falls_back_to_name(self):
        self.assertIsNone(self.m.load_by_source(FakeLib(self.index),{},self.index))
    def test_game_path_stamp(self):
        self.assertEqual(self.m.source_package({'arc_source_json':'/Game/Environment/Res/MI_Res_Stucco_01.json'},self.index),'PioneerGame/Content/Environment/Res/MI_Res_Stucco_01')

if __name__=='__main__':unittest.main()
