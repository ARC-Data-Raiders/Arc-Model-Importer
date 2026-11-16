"""Colors/Wear apply polls use shader_target_info: an active object outside any import never enables them."""
import importlib
import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(os.environ.get("DATARAIDERS_TEST_REPOSITORY", Path(__file__).resolve().parents[1]))
PKG = "_dr_probe_c_t4_shader"


def _load():
    for name, path in ((PKG, ROOT), (PKG + ".Shared", ROOT / "Shared")):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            mod.__path__ = [str(path)]
            sys.modules[name] = mod
    return importlib.import_module(PKG + ".Shared.shader_control_ops")


class _Op:
    def __init__(self):
        self.messages = []

    def poll_message_set(self, text):
        self.messages.append(text)


class ShaderTargetPollTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ops = _load()

    def _poll(self, op_cls, info):
        fake = _Op()
        stray = SimpleNamespace(name="Cube")
        with patch.object(self.ops, "shader_target_info", lambda context: info), \
                patch.object(self.ops, "target_object", lambda context: stray):
            return op_cls.poll.__func__(fake, SimpleNamespace()), fake.messages

    def test_unowned_active_object_disables_apply(self):
        info = {"state": "none", "name": "", "source": "", "root": None, "message": ""}
        for op_cls in (self.ops.ARC_OT_shader_colourway_apply, self.ops.ARC_OT_shader_control_reset):
            ok, messages = self._poll(op_cls, info)
            self.assertFalse(ok, op_cls.__name__)
            self.assertEqual(messages, ["Select an imported outfit, weapon or enemy"])

    def test_syncing_target_disables_apply_with_reason(self):
        root = SimpleNamespace(name="Outfit")
        info = {"state": "syncing", "name": "Outfit", "source": "selection", "root": root,
                "message": "Syncing material controls for Outfit..."}
        ok, messages = self._poll(self.ops.ARC_OT_shader_colourway_apply, info)
        self.assertFalse(ok)
        self.assertEqual(messages, [info["message"]])

    def test_ready_target_enables_apply(self):
        root = SimpleNamespace(name="Outfit")
        info = {"state": "ready", "name": "Outfit", "source": "last_import", "root": root, "message": ""}
        ok, _messages = self._poll(self.ops.ARC_OT_shader_colourway_apply, info)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
