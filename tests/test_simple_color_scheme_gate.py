"""Only the authored bUseColorScheme switch turns a simple-family BaseColor into a ColorMask (no Blender)."""
import ast,os,unittest
from pathlib import Path
ROOT=Path(os.environ['DATARAIDERS_TEST_REPOSITORY']) if os.environ.get('DATARAIDERS_TEST_REPOSITORY') else next(p for p in Path(__file__).resolve().parents if (p/'materials/environment.py').is_file())
WHITE=(1.0,1.0,1.0,1.0)

def gate():
    tree=ast.parse((ROOT/'materials/environment.py').read_text(encoding='utf-8'))
    ns={}
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_simple_uses_color_scheme'],type_ignores=[]),'<environment>','exec'),ns)
    return ns['_simple_uses_color_scheme']

class SimpleColorSchemeGateTests(unittest.TestCase):
    def setUp(self):self.uses=gate()
    def test_inherited_master_white_colours_do_not_enable_scheme(self):
        # MI_Celeste_Base_Helmet -> M_Character_Default_2: ColorA/B/C are white master defaults, no switches.
        mi={'parent':'M_Character_Default_2','switches':{},
            'colours':[('Global.ColorA',WHITE),('Global.ColorB',WHITE),('Global.ColorC',WHITE),('ColorA',WHITE),('ColorB',WHITE),('ColorC',WHITE)]}
        self.assertFalse(self.uses(mi))
    def test_authored_switch_enables_scheme(self):
        # MI_SimplePBR_PonchoFringe overrides bUseColorScheme=true with authored C49B61/977350/654B35.
        mi={'parent':'M_SimplePBR_ColorScheme','switches':{'Global.bUseColorScheme':True,'bUseColorScheme':True},
            'colours':[('ColorA',(.55,.33,.12,1.)),('ColorB',(.31,.17,.08,1.)),('ColorC',(.14,.07,.03,1.))]}
        self.assertTrue(self.uses(mi))
    def test_colour_scheme_master_default_switch_off(self):
        # M_SimplePBR_ColorScheme defaults the switch off: red/green/blue defaults never tint BaseColor.
        mi={'parent':'M_SimplePBR_ColorScheme','switches':{'bUseColorScheme':False},
            'colours':[('ColorA',(1.,0.,0.,1.)),('ColorB',(0.,1.,0.,1.)),('ColorC',(0.,0.,1.,1.))]}
        self.assertFalse(self.uses(mi))
if __name__=='__main__':unittest.main()
