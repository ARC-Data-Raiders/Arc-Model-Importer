"""Preservation contracts for the later generic surface pass; no Blender needed."""
import ast,json,types,unittest
from unittest.mock import patch
from .native_test_support import ROOT,PACKAGE,namespace
nf=namespace()
class Material(dict):
    name='fixture';use_nodes=True;node_tree=None

def functions():
    path=ROOT/'revamped_bridge.py';tree=ast.parse(path.read_text(encoding='utf-8'))
    names={'_preserve_native_material','verify_surface_materials'}
    selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    module=types.ModuleType(PACKAGE+'.preservation_contract');module.__package__=PACKAGE
    module.__dict__.update(json=json,_has_surface_output=lambda mat:True,_loaded_images=lambda tree:[],_image_ready=lambda image:True)
    code=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+selected,type_ignores=[])
    exec(compile(ast.fix_missing_locations(code),str(path),'exec'),module.__dict__)
    return module

class NativePreservationTests(unittest.TestCase):
    def setUp(self):
        self.api=functions();self.obj=types.SimpleNamespace(name='fixture')
        self.master='/Game/Pioneer/MaterialLibrary/M_Presets/M_BlockoutPreset_Glow'
        self.mat=Material(dr_native_family='prop',dr_native_active_signature='complete',dr_native_signature_complete=True,arc_mi_path='source',dr_native_report=json.dumps({'master':self.master,'family':'prop','subtype':'blockout_glow','images':[],'diagnostics':[]}))
    def preserve(self,mat=None):return self.api._preserve_native_material(self.obj,0,self.mat if mat is None else mat,None)
    def test_exact_native_color_only_profile_is_preserved_and_verifies_without_images(self):
        with patch.object(nf,'native_cache_is_stale',return_value=False):report=self.preserve()
        self.assertEqual(report['family'],'native_prop');self.assertEqual(report['subtype'],'blockout_glow');self.assertTrue(report['native_cache_current']);self.assertFalse(report['texture_required'])
        self.obj.material_slots=[types.SimpleNamespace(material=self.mat)];self.assertTrue(self.api.verify_surface_materials(self.obj)['ok'])
    def test_stale_profile_is_retained_and_diagnosed_instead_of_generic_replacement(self):
        with patch.object(nf,'native_cache_is_stale',return_value=True):report=self.preserve()
        self.assertTrue(report['preserved_native_graph']);self.assertFalse(report['native_cache_current']);self.assertTrue(any('needs refresh' in w for w in report['warnings']));self.assertEqual(self.mat['dr_native_active_signature'],'complete')
    def test_override_bypasses_source_and_output_queries_and_preserves_mode(self):
        override=Material(arc_build_report=json.dumps({'user_override':{'color':'red'}}));override.use_nodes=False
        self.api._has_surface_output=lambda mat:(_ for _ in ()).throw(AssertionError('override caused output query'))
        with patch.object(nf,'native_cache_is_stale',side_effect=AssertionError('override caused source query')):report=self.preserve(override)
        self.assertEqual(report['family'],'user_override');self.assertFalse(override.use_nodes);self.obj.material_slots=[types.SimpleNamespace(material=override)];self.assertTrue(self.api.verify_surface_materials(self.obj)['ok'])
    def test_unknown_master_or_signature_cannot_claim_native_preservation(self):
        self.mat['dr_native_report']=json.dumps({'master':'/Game/Unproved','images':[]})
        self.assertIsNone(self.preserve());self.mat['dr_native_report']=json.dumps({'master':self.master});del self.mat['dr_native_active_signature'];self.assertIsNone(self.preserve())
    def test_missing_material_is_safe(self):self.assertIsNone(self.api._preserve_native_material(self.obj,0,None,None))
