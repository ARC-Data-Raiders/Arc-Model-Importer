"""Engine default materials (WorldGridMaterial) resolve to the engine_default family, never raise on basename ambiguity."""
try:
    from . import native_test_support as s
except ImportError:
    import native_test_support as s
import importlib,sys,types,unittest
from pathlib import Path

REBUILD=s.PACKAGE+'.Shared.materials.rebuild'

def rebuild(name):
    s.real_package('Shared.materials.native')
    if REBUILD not in sys.modules:
        m=types.ModuleType(REBUILD);m.__path__=[str(s.ROOT/'Shared/materials/rebuild')];sys.modules[REBUILD]=m
    return importlib.import_module(REBUILD+'.'+name)

ENGINE='/Engine/EngineMaterials/WorldGridMaterial.WorldGridMaterial'

class EngineDefaultSlot(unittest.TestCase):
    def setUp(self):
        self.assets=rebuild('assets');self.records=rebuild('records')
        self.tmp=s.temp_fixture();self.root=Path(self.tmp.name)/'export'
        # Two unrelated game-side copies with different digests: basename is ambiguous.
        s.write_json(self.root/'PioneerGame/Content/A/WorldGridMaterial.json',[{'Type':'Material','Name':'WorldGridMaterial','Properties':{'A':1}}])
        s.write_json(self.root/'PioneerGame/Content/B/WorldGridMaterial.json',[{'Type':'Material','Name':'WorldGridMaterial','Properties':{'B':2}}])
        (self.root/'PioneerGame/Content/Props').mkdir(parents=True)
    def tearDown(self):self.tmp.cleanup()
    def lib(self,compat):
        index=self.assets.AssetIndex(self.root);index.compatibility_mode=compat
        lib=self.records.MaterialLibrary(index);lib.native=None
        return lib
    def check(self,name,compat):
        mesh=str(self.root/'PioneerGame/Content/Props/SM_X.json')
        record=self.lib(compat).slot(name,mesh)
        self.assertEqual(record['family'],'engine_default',(name,compat))
        self.assertEqual(record['package'].casefold(),'engine/content/enginematerials/worldgridmaterial')
    def test_bare_name_ambiguous(self):
        for compat in (False,True):self.check('WorldGridMaterial',compat)
    def test_engine_path_ambiguous(self):
        for compat in (False,True):self.check(ENGINE,compat)
    def test_engine_path_exact_json_wins(self):
        s.write_json(self.root/'Engine/Content/EngineMaterials/WorldGridMaterial.json',[{'Type':'Material','Name':'WorldGridMaterial'}])
        record=self.lib(True).slot(ENGINE)
        self.assertEqual(record['package'].casefold(),'engine/content/enginematerials/worldgridmaterial')
        self.assertEqual(record['family'],'engine_default')
    def test_engine_parent_ambiguous(self):
        s.write_json(self.root/'PioneerGame/Content/Props/MI_Grid.json',[{'Type':'MaterialInstanceConstant','Name':'MI_Grid','Properties':{'Parent':{'ObjectName':"Material'WorldGridMaterial'",'ObjectPath':ENGINE+'.0'}}}])
        record=self.lib(True).slot('MI_Grid')
        self.assertEqual(record['family'],'engine_default')

if __name__=='__main__':unittest.main()
