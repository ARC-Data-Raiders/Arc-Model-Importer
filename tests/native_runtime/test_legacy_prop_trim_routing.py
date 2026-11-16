"""Legacy classify routing: CR Texture + NXX/NMX Texture masters reach prop_trim instead of generic fallback."""
try:
    from . import native_test_support as s
except ImportError:
    import native_test_support as s
import importlib,sys,types,unittest

def records():
    s.real_package('Shared.materials.native')
    k=s.PACKAGE+'.Shared.materials.rebuild'
    if k not in sys.modules:
        m=types.ModuleType(k);m.__path__=[str(s.ROOT/'Shared/materials/rebuild')];sys.modules[k]=m
    return importlib.import_module(k+'.records')

def rec(master,textures,switches=None):
    return {'master':master,'textures':dict.fromkeys(textures,'/Game/T/X'),'scalars':{},'colors':{},'switches':switches or {},'name':'MI_Test'}

LIB='/Game/Pioneer/MaterialLibrary/Masters/'
class LegacyPropTrimRouting(unittest.TestCase):
    def setUp(self):self.r=records()
    def test_tileable_metal_routes_to_prop_trim(self):
        self.assertEqual(self.r.classify(rec(LIB+'M_Tileable_Metal_CR+NXX',['CR Texture','NXX/NMX Texture','Overlay'],{'Turn on basic tiling':True})),'prop_trim')
    def test_harvester_trim_routes_to_prop_trim(self):
        self.assertEqual(self.r.classify(rec(LIB+'M_PropTrimPreset_CR+NOM+AO_Harvester',['CR Texture','NXX/NMX Texture','Trim concave mask'])),'prop_trim')
    def test_textureless_master_routes_to_parameterless(self):
        self.assertEqual(self.r.classify(rec(LIB+'M_SplineThicken',[])),'parameterless')
    def test_single_cr_texture_does_not_route(self):
        self.assertNotEqual(self.r.classify(rec(LIB+'M_Unknown_Thing',['CR Texture'])),'prop_trim')

if __name__=='__main__':unittest.main()
