"""A UEModel import that returns no meshes must keep why (exception text + traceback), not only 'Could not import'."""
import importlib.util,sys,types,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
def load():
    name='_uemodel_reason_probe.Shared.uemodel_import';spec=importlib.util.spec_from_file_location(name,ROOT/'Shared/uemodel_import.py');module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    with patch.dict(sys.modules,{'bpy':types.ModuleType('bpy')}):spec.loader.exec_module(module)
    return module

class UeModelImportReason(unittest.TestCase):
    def setUp(self):self.u=load()
    def test_exception_reason_kept(self):
        def boom(*a,**k):raise KeyError(None)
        with patch.object(self.u,'import_uemodel_v10',boom),patch('builtins.print'):
            self.assertEqual(self.u.try_import_uemodel_v10('x.uemodel'),[])
        reason=self.u.last_import_error()
        self.assertIn('KeyError',reason)
        self.assertIn('Traceback',reason)
    def test_empty_result_reason_kept(self):
        with patch.object(self.u,'import_uemodel_v10',lambda *a,**k:[]),patch('builtins.print'):
            self.assertEqual(self.u.try_import_uemodel_v10('x.uemodel'),[])
        self.assertIn('no meshes',self.u.last_import_error())
    def test_success_clears_reason(self):
        with patch.object(self.u,'import_uemodel_v10',lambda *a,**k:['obj']):
            self.assertEqual(self.u.try_import_uemodel_v10('x.uemodel'),['obj'])
        self.assertEqual(self.u.last_import_error(),'')
    def test_appearance_message_uses_reason(self):
        src=(ROOT/'Outfit_Accessories/appearance.py').read_text(encoding='utf-8')
        block=src[src.index('def _import_uemodel_outfit'):src.index('def _import_colored_hair')]
        self.assertIn('last_import_error',src[src.index('Could not import')-2500:src.index('Could not import')+300])
        self.assertIn('set_import_error',block)

if __name__=='__main__':unittest.main()
