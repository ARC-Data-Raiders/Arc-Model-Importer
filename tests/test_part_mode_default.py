"""C1: Character Parts opens on New Character while the scene has no character; a picked mode sticks."""
import ast
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY', Path(__file__).resolve().parents[1])).resolve()


def _ui_targets():
    spec = importlib.util.spec_from_file_location('_part_mode_ui_targets', ROOT / 'Shared/ui_targets.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _accessors(targets, armatures):
    tree = ast.parse((ROOT / 'Outfit_Accessories/properties.py').read_text(encoding='utf-8'))
    wanted = {'_PART_MODE_ITEMS', '_PART_MODE_INDEX', '_get_part_mode', '_set_part_mode'}
    body = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in wanted)
            or (isinstance(n, ast.Assign) and any(getattr(t, 'id', '') in wanted for t in n.targets))]
    for fn in body:
        if isinstance(fn, ast.FunctionDef):
            fn.body = [s for s in fn.body if not isinstance(s, ast.ImportFrom)]
    ns = {'ui_targets': targets, 'bpy': types.SimpleNamespace(data=types.SimpleNamespace(armatures=armatures))}
    exec(compile(ast.Module(body=body, type_ignores=[]), 'properties', 'exec'), ns)
    return ns


class Scene(dict):
    def __init__(self, objects=()):
        super().__init__()
        self.objects = list(objects)


class PartModeDefault(unittest.TestCase):
    def setUp(self):
        self.targets = _ui_targets()

    def test_empty_scene_defaults_to_new(self):
        ns = _accessors(self.targets, [])
        self.assertEqual(ns['_PART_MODE_ITEMS'][ns['_get_part_mode'](Scene())][0], 'NEW')

    def test_character_scene_defaults_to_add(self):
        self.targets.scene_has_character = lambda scene: True
        ns = _accessors(self.targets, ['Armature'])
        self.assertEqual(ns['_PART_MODE_ITEMS'][ns['_get_part_mode'](Scene())][0], 'ADD')

    def test_armature_without_character_defaults_to_new(self):
        self.targets.scene_has_character = lambda scene: False
        self.assertEqual(self.targets.default_part_mode(Scene(), has_armatures=True), 'NEW')

    def test_picked_mode_sticks(self):
        ns = _accessors(self.targets, [])
        scene = Scene()
        ns['_set_part_mode'](scene, ns['_PART_MODE_INDEX']['REPLACE'])
        self.assertEqual(ns['_PART_MODE_ITEMS'][ns['_get_part_mode'](scene)][0], 'REPLACE')

    def test_property_uses_accessors(self):
        src = (ROOT / 'Outfit_Accessories/properties.py').read_text(encoding='utf-8')
        self.assertIn('get=_get_part_mode', src)
        self.assertIn('set=_set_part_mode', src)


if __name__ == '__main__':
    unittest.main()
