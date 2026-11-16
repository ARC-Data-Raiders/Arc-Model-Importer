"""Debug log lives outside the add-on folder; reloads keep one tagged handler of each kind."""
import importlib
import logging
import logging.handlers
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY', Path(__file__).resolve().parents[1])).resolve()
PACKAGE = '_log_rotation_probe'

try:
    import bpy  # noqa: F401
    HAS_BPY = True
except ImportError:
    HAS_BPY = False


def _tagged(log, tag):
    return [h for h in log.handlers if getattr(h, '_arc_importer', '') == tag]


@unittest.skipUnless(HAS_BPY, 'Shared/utils.py imports bpy and mathutils')
class LogRotation(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='log-rotation-', dir=os.environ.get('DATARAIDERS_TEST_TEMP'))
        self.addCleanup(temp.cleanup)
        self.scratch = Path(temp.name)
        env = mock.patch.dict(os.environ, {'LOCALAPPDATA': str(self.scratch / 'local'), 'TEMP': str(self.scratch / 'temp'),
                                           'TMP': str(self.scratch / 'temp')})
        env.start()
        self.addCleanup(env.stop)
        # Detach the process-global logger so the probe starts from a clean state; restore after.
        self.log = logging.getLogger('arc_raiders')
        saved = (list(self.log.handlers), self.log.level, self.log.propagate)
        for handler in saved[0]:
            self.log.removeHandler(handler)
        self.addCleanup(self._restore, saved)
        for name, path in ((PACKAGE, ROOT), (PACKAGE + '.Shared', ROOT / 'Shared')):
            package = types.ModuleType(name)
            package.__path__ = [str(path)]
            sys.modules[name] = package
        sys.modules.pop(PACKAGE + '.Shared.utils', None)
        self.utils = importlib.import_module(PACKAGE + '.Shared.utils')

    def _restore(self, saved):
        for handler in list(self.log.handlers):
            self.log.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        handlers, level, propagate = saved
        for handler in handlers:
            self.log.addHandler(handler)
        self.log.setLevel(level)
        self.log.propagate = propagate
        for name in list(sys.modules):
            if name == PACKAGE or name.startswith(PACKAGE + '.'):
                sys.modules.pop(name, None)

    def test_log_path_is_outside_the_addon_folder(self):
        path = Path(self.utils.log_path()).resolve()
        self.assertTrue(str(path))
        self.assertNotIn(ROOT, path.parents)
        self.assertIn(self.scratch.resolve(), path.parents)
        self.assertEqual(path.name, 'arc_raiders_debug.log')
        handler = _tagged(self.log, 'file')[0]
        self.assertIsInstance(handler, logging.handlers.RotatingFileHandler)
        self.assertGreater(handler.maxBytes, 0)
        self.assertGreater(handler.backupCount, 0)

    def test_reload_keeps_one_tagged_handler_each(self):
        first = self.utils.log_path()
        # An untagged FileHandler from an older build is dropped on the next get_logger.
        stale = logging.FileHandler(str(self.scratch / 'stale.log'), delay=True)
        self.log.addHandler(stale)
        for _ in range(3):
            self.utils = importlib.reload(self.utils)
            self.utils.get_logger()
        self.assertEqual(len(_tagged(self.log, 'file')), 1)
        self.assertEqual(len(_tagged(self.log, 'stream')), 1)
        self.assertNotIn(stale, self.log.handlers)
        self.assertEqual(len(self.log.handlers), 2)
        self.assertEqual(self.utils.log_path(), first)
        self.assertFalse(self.log.propagate)


if __name__ == '__main__':
    unittest.main()
