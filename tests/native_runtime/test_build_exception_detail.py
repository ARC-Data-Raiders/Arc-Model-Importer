"""build_exception rows keep exception type + traceback tail when the message is empty or 'None'."""
import importlib,unittest
from .native_test_support import PACKAGE,namespace
namespace()
audit=importlib.import_module(PACKAGE+'.Shared.materials.material_audit')

class BuildExceptionDetail(unittest.TestCase):
    def test_none_message_names_type(self):
        msg=audit.exception_message(KeyError(None))
        self.assertIn('KeyError',msg)
        self.assertNotEqual(msg,'None')
        self.assertIn('ValueError',audit.exception_message(ValueError()))
        self.assertEqual(audit.exception_message(ValueError('bad slot')),'bad slot')
    def test_row_error_keeps_type_and_tail(self):
        tb='Traceback (most recent call last):\n'+'  x\n'*400+'KeyError: None\n'
        entry=audit.row_error('mat:MI_A.001',{'error':'None','error_type':'KeyError','traceback':tb})
        self.assertEqual(entry['error_type'],'KeyError')
        self.assertTrue(entry['traceback_tail'].endswith('KeyError: None\n'))
        self.assertLessEqual(len(entry['traceback_tail']),audit.TRACEBACK_TAIL)
    def test_row_error_from_prior(self):
        entry=audit.row_error('mat:MI_A.001',{'flags':[],'prior':{'error':'x','error_type':'LookupError','traceback':'tb'}})
        self.assertEqual((entry['error'],entry['error_type'],entry['traceback_tail']),('x','LookupError','tb'))
        self.assertIsNone(audit.row_error('mat:MI_A',{'flags':[]}))

if __name__=='__main__':unittest.main()
