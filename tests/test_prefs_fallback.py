"""Add-on preferences fall back safely; the Paks folder resolves scene first, then prefs."""
import ast
import importlib.util
import os
import re
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY', Path(__file__).resolve().parents[1])).resolve()
SELECTED = {'configured_dir', '_prefs_pak_dir', 'resolve_pak_folder', '_resolve_pak_folder', 'pak_dir_has_game_files'}

try:
    import bpy  # noqa: F401
    HAS_BPY = True
except ImportError:
    HAS_BPY = False


def _load_prefs():
    spec = importlib.util.spec_from_file_location('_prefs_fallback_probe.Shared.prefs', ROOT / 'Shared/prefs.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_resolver(store):
    path = ROOT / 'Shared/pak_extract.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in SELECTED]
    if {n.name for n in nodes} != SELECTED:
        raise AssertionError('pak folder resolver functions changed')
    module = types.ModuleType('_prefs_fallback_probe_pak')
    module.__dict__.update(os=os, re=re)
    module.bpy = types.SimpleNamespace(path=types.SimpleNamespace(abspath=lambda value: value))
    module.prefs = types.SimpleNamespace(
        get=lambda key, default=None: store.get(key) or default,
        remember=lambda key, value: bool(value) and not store.get(key) and store.__setitem__(key, value) is None)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), module.__dict__)
    return module


class Scene(dict):
    def __init__(self, folder=''):
        super().__init__()
        self.arc_pak_dir = folder


@unittest.skipUnless(HAS_BPY, 'Shared/prefs.py imports bpy')
class PrefsGetFallback(unittest.TestCase):
    def setUp(self):
        self.prefs = _load_prefs()

    def test_unregistered_addon_returns_default(self):
        self.assertEqual(self.prefs.ADDON_PACKAGE, '_prefs_fallback_probe')
        self.assertIsNone(self.prefs._addon_prefs())
        self.assertEqual(self.prefs.get('pak_dir', 'fallback'), 'fallback')
        self.assertIsNone(self.prefs.get('pak_dir'))
        self.assertFalse(self.prefs.set_value('pak_dir', 'X:/game'))
        self.assertFalse(self.prefs.remember('pak_dir', 'X:/game'))

    def test_empty_and_unknown_fields_return_default(self):
        self.prefs._addon_prefs = lambda: types.SimpleNamespace(pak_dir='', usmap_path='map.usmap')
        self.assertEqual(self.prefs.get('pak_dir', 'fallback'), 'fallback')
        self.assertEqual(self.prefs.get('usmap_path', 'fallback'), 'map.usmap')
        self.assertEqual(self.prefs.get('no_such_field', 'fallback'), 'fallback')
        self.assertFalse(self.prefs.set_value('no_such_field', 'value'))


class PakDirOrder(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='prefs-fallback-', dir=os.environ.get('DATARAIDERS_TEST_TEMP'))
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.games = []
        for name in ('scene_game', 'prefs_game'):
            paks = root / name / 'Arc Raiders' / 'PioneerGame' / 'Content' / 'Paks'
            paks.mkdir(parents=True)
            (paks / 'pakchunk0-Windows.utoc').write_bytes(b'')
            self.games.append((str(root / name / 'Arc Raiders'), str(paks)))
        self.missing = str(root / 'missing')
        self.store = {}
        self.pak = _load_resolver(self.store)

    def same(self, got, want):
        self.assertEqual(os.path.normcase(os.path.normpath(got)), os.path.normcase(os.path.normpath(want)))

    def test_valid_scene_value_wins_over_prefs(self):
        (scene_game, scene_paks), (prefs_game, _) = self.games
        self.store['pak_dir'] = prefs_game
        self.same(self.pak.configured_dir(Scene(scene_game)), scene_paks)

    def test_empty_or_invalid_scene_value_uses_prefs(self):
        _, (prefs_game, prefs_paks) = self.games
        self.store['pak_dir'] = prefs_game
        self.same(self.pak.configured_dir(Scene('')), prefs_paks)
        self.same(self.pak.configured_dir(Scene(self.missing)), prefs_paks)

    def test_user_cleared_scene_skips_prefs(self):
        _, (prefs_game, _) = self.games
        self.store['pak_dir'] = prefs_game
        scene = Scene('')
        scene['arc_pak_dir_user_cleared'] = True
        self.assertEqual(self.pak.configured_dir(scene), '')

    def test_no_prefs_and_no_scene_value_is_empty(self):
        self.assertEqual(self.pak.configured_dir(Scene('')), '')
        self.store['pak_dir'] = self.missing
        self.assertEqual(self.pak.configured_dir(Scene('')), '')

    def test_valid_scene_value_seeds_empty_prefs(self):
        (scene_game, _), _ = self.games
        self.pak.configured_dir(Scene(scene_game))
        self.assertEqual(self.store.get('pak_dir'), scene_game)


if __name__ == '__main__':
    unittest.main()
