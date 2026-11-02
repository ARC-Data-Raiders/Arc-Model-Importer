"""Packaged weapon/ARC browsing and lazy action-root regressions."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY', Path(__file__).resolve().parents[1]))
PACKAGE = '_weapon_browser_regression'


def load_module(name):
    spec = importlib.util.spec_from_file_location(PACKAGE + '.' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    setattr(sys.modules[PACKAGE], name, module)
    return module


class Layout:
    def __init__(self, events=None, scope='default', expanded=False):
        self.events = events if events is not None else []
        self.scope = scope
        self.expanded = expanded
    def panel(self, identifier, default_closed=True):
        self.events.append((self.scope, 'panel', identifier))
        return self, Layout(self.events, identifier, self.expanded) if self.expanded else None
    def label(self, **kwargs):
        self.events.append((self.scope, 'label', kwargs.get('text')))
    def prop(self, owner, name, **kwargs):
        self.events.append((self.scope, 'prop', name))
    def operator(self, name, **kwargs):
        self.events.append((self.scope, 'operator', name))
        return SimpleNamespace()
    def row(self, **kwargs):
        return self
    def separator(self):
        pass


class WeaponBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {name: module for name, module in sys.modules.items() if name == 'bpy' or name.startswith('bpy.') or name.startswith(PACKAGE)}
        package = ModuleType(PACKAGE)
        package.__path__ = [str(ROOT)]
        sys.modules[PACKAGE] = package
        bpy = ModuleType('bpy')
        bpy.types = SimpleNamespace(Operator=type('Operator', (), {}), Scene=type('Scene', (), {}))
        bpy.context = SimpleNamespace(scene=SimpleNamespace(arc_pioneer_root='/export/must/not/read'))
        bpy.props = ModuleType('bpy.props')
        for name in ('BoolProperty', 'EnumProperty', 'StringProperty', 'CollectionProperty'):
            setattr(bpy.props, name, lambda **kwargs: None)
        sys.modules['bpy'] = bpy
        sys.modules['bpy.props'] = bpy.props
        for name in ('weapon_import', 'weapon_position', 'utils'):
            module = ModuleType(PACKAGE + '.' + name)
            sys.modules[module.__name__] = module
            setattr(package, name, module)
        cls.utils = package.utils
        cls.utils._pak_cache_roots = []
        cls.utils.get_pioneer_root = lambda: ''
        cls.utils.get_content_dirs = lambda: []
        cls.utils.find_content_dir = lambda root: None
        load_module('weapon_catalog_data')
        cls.assets = load_module('weapon_assets')
        cls.catalog = load_module('weapon_catalog')
        cls.ui = load_module('weapon_ui')
        cls.arc_catalog = load_module('arc_catalog_data')
        cls.arc = load_module('arc_ui')

    @classmethod
    def tearDownClass(cls):
        for name in list(sys.modules):
            if name == 'bpy' or name.startswith('bpy.') or name.startswith(PACKAGE):
                sys.modules.pop(name, None)
        sys.modules.update(cls.saved)

    def setUp(self):
        self.catalog.invalidate_enum_caches()
        self.ui._ENUMS.clear()
        self.ui._LEGACY_ENUMS.clear()
        self.scene = SimpleNamespace(arc_weapon_key=self.catalog.weapons()[0]['key'], arc_weapon_ascended='OFF', arc_enemy_key=self.arc_catalog.ARC_ENEMIES[0][0])
        self.context = SimpleNamespace(scene=self.scene, active_object=None)

    def test_cold_and_warm_enums_use_only_packaged_metadata(self):
        with patch.object(self.assets, 'pioneer_roots', side_effect=AssertionError('browse root discovery')), patch.object(self.utils, 'get_pioneer_root', side_effect=AssertionError('browse configured root')), patch.object(self.utils, 'get_content_dirs', side_effect=AssertionError('browse backup discovery')), patch.object(self.assets, 'resolve', side_effect=AssertionError('browse source resolution')):
            for entry in self.catalog.weapons():
                self.scene.arc_weapon_key = entry['key']
                for repeat in range(2):
                    variants = self.ui.ascended_items(self.scene, self.context)
                    self.assertEqual({item[0] for item in variants[1:]}, {item['id'] for item in self.assets.bundled()['weapons'][entry['stem']]['variants']})
                    self.ui.legacy_ascended_items(self.scene, self.context)
                    skins = self.catalog.skin_enum_items(self.scene, self.context)
                    self.assertEqual({item[0] for item in skins[1:]}, {item['id'] for item in self.assets.bundled()['weapons'][entry['stem']]['skins']})
            self.catalog.pattern_enum_items(self.scene, self.context)

    def test_previews_never_consult_export_roots(self):
        class ForbiddenRoots:
            def __iter__(self):
                raise AssertionError('preview inspected export roots')
        for entry in self.catalog.weapons():
            self.assertEqual(self.assets.preview_path(entry, ForbiddenRoots()), self.assets.bundled_preview(entry))
            for variant in self.assets.bundled()['weapons'][entry['stem']]['variants']:
                self.assertEqual(self.assets.preview_path(entry, ForbiddenRoots(), variant['id']), self.assets.bundled_preview(entry, variant['id']))
        for entry in self.catalog.mods():
            self.assertEqual(self.assets.preview_path(entry, ForbiddenRoots(), mod=True), self.assets.bundled_preview(entry, mod=True))
        with patch.object(self.utils, 'find_content_dir', side_effect=AssertionError('ARC preview export discovery')):
            for key, label, filename in self.arc_catalog.ARC_ENEMIES:
                self.assertEqual(self.arc._pioneer_image_path(filename), self.arc._ARC_PREVIEW_DIR / filename)

    def test_gun_controls_are_packaged_and_visible_without_advanced(self):
        with patch.object(self.assets, 'pioneer_roots', side_effect=AssertionError('draw root discovery')), patch.object(self.ui, 'ensure_registered', return_value=True), patch.object(self.ui, 'draw_gun_preview'), patch.object(self.ui, 'draw_preview'), patch.object(self.ui, 'draw_arc_preview'):
            for expanded in (False, True):
                layout = Layout(expanded=expanded)
                self.ui.draw(layout, self.context)
                default = {(kind, name) for scope, kind, name in layout.events if scope == 'default'}
                self.assertIn(('operator', 'arc_outfits.import_weapon'), default)
                for name in ('arc_weapon_position', 'arc_weapon_tier', 'arc_weapon_ascended', 'arc_weapon_include_ascended'):
                    self.assertIn(('prop', name), default)
                for name in ('arc_outfits.add_all_weapons', 'arc_outfits.set_weapon_tier', 'arc_outfits.update_weapon_position'):
                    self.assertIn(('operator', name), default)
                self.assertFalse(any(kind == 'panel' and name == 'arc_weapon_advanced' for scope, kind, name in layout.events))

    def test_generated_preview_identity_and_no_external_io(self):
        entry = next(entry for entry in self.catalog.weapons() if entry['stem'] == 'HandCannon_SingleAction_01')
        self.assets.generated_previews.cache_clear()
        original_read = Path.read_text
        def packaged_read(path, *args, **kwargs):
            self.assertTrue(path.is_relative_to(ROOT / 'assets/weapon_browser'))
            return original_read(path, *args, **kwargs)
        with patch.object(Path, 'read_text', packaged_read), patch.object(Path, 'is_file', side_effect=AssertionError('browse stat')), patch.object(Path, 'glob', side_effect=AssertionError('browse scan')), patch.object(self.assets, 'pioneer_roots', side_effect=AssertionError('browse root discovery')):
            paths = [self.assets.bundled_preview(entry, tier=tier, stencil=skin) for tier, skin in [('0', 'CommonBlue'), ('1', 'CommonBlue'), ('1', 'CommonRed')]]
            self.assertEqual(len(set(paths)), 3)
            for path in paths:
                self.assertTrue(path.is_relative_to(ROOT / 'assets/weapon_browser/generated'))
            self.assertIsNone(self.assets.bundled_preview(entry, tier='1', stencil='CommonBl'))
            self.assertIsNone(self.assets.bundled_preview(entry, 'nonexistent', tier='1', stencil='CommonBlue'))
            self.assertIsNone(self.assets.bundled_preview(entry, tier='invalid', stencil='CommonBlue'))

    def test_draw_passes_selected_tier_and_stencil(self):
        self.scene.arc_weapon_tier = '1'
        self.scene.arc_weapon_skin = 'CommonBlue'
        with patch.object(self.ui, 'ensure_registered', return_value=True), patch.object(self.ui, 'draw_gun_preview') as preview, patch.object(self.assets, 'bundled_preview', return_value=None) as select:
            self.ui.draw(Layout(), self.context)
            select.assert_called_once_with(self.catalog.weapon_by_key(self.scene.arc_weapon_key), 'OFF', tier='1', stencil='CommonBlue')
            preview.assert_called_once()

    def test_ascended_preview_survives_tier_and_stencil_selection(self):
        for stem, weapon in self.assets.bundled()['weapons'].items():
            for variant in weapon.get('variants', []):
                expected = self.assets.generated_previews()['weapons'][stem][variant['id']]['3']['NONE']
                for tier in ('0', '1', '2', '3'):
                    for stencil in ('NONE', 'CommonBlue'):
                        with patch.object(Path, 'is_file', side_effect=AssertionError('browse stat')):
                            actual = self.assets.bundled_preview({'stem': stem}, variant['id'], tier=tier, stencil=stencil)
                        self.assertEqual(actual, ROOT / 'assets/weapon_browser/generated' / expected)

    def test_gun_preview_retains_ready_image_until_replacement(self):
        class PreviewLayout(Layout):
            def template_preview(self, texture, **kwargs):
                self.events.append(('preview', texture.name))
        first = str((ROOT / 'first.png').resolve())
        second = str((ROOT / 'second.png').resolve())
        textures = {first: SimpleNamespace(name='first')}
        callbacks = []
        timers = SimpleNamespace(is_registered=lambda callback: callback in callbacks,
                                 register=lambda callback, **kwargs: callbacks.append(callback))
        context = SimpleNamespace(region=None, preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=1)))
        with patch.object(self.ui, '_GUN_TEXTURES', textures), patch.object(self.ui, '_GUN_PENDING', set()), patch.object(self.ui, '_GUN_DISPLAY', {}, create=True), patch.object(self.ui, '_GUN_FAILED', set(), create=True), patch.object(self.ui.bpy, 'app', SimpleNamespace(timers=timers), create=True), patch.object(Path, 'is_file', return_value=True):
            for path, expected in ((first, 'first'), (second, 'first'), (second, 'second')):
                layout = PreviewLayout()
                self.ui.draw_gun_preview(layout, context, path)
                self.assertIn(('preview', expected), layout.events)
                if path == second:
                    textures[second] = SimpleNamespace(name='second')
            self.assertEqual(len(callbacks), 1)
            layout = PreviewLayout()
            self.ui.draw_gun_preview(layout, context, None)
            self.assertFalse(any(event[0] == 'preview' for event in layout.events))
            self.assertTrue(layout.events)
            self.ui.draw_gun_preview(PreviewLayout(), context, first)
            self.ui._GUN_FAILED.add(str((ROOT / 'missing.png').resolve()))
            layout = PreviewLayout()
            self.ui.draw_gun_preview(layout, context, ROOT / 'missing.png')
            self.assertFalse(any(event[0] == 'preview' for event in layout.events))
            self.assertTrue(layout.events)

    def test_cold_gun_preview_reserves_space_without_decoding(self):
        callbacks = []
        timers = SimpleNamespace(is_registered=lambda callback: callback in callbacks,
                                 register=lambda callback, **kwargs: callbacks.append(callback))
        context = SimpleNamespace(region=None, preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=1)))
        layout = Layout()
        with patch.object(self.ui, '_GUN_TEXTURES', {}), patch.object(self.ui, '_GUN_PENDING', set()), patch.object(self.ui, '_GUN_DISPLAY', {}, create=True), patch.object(self.ui, '_GUN_FAILED', set(), create=True), patch.object(self.ui.bpy, 'app', SimpleNamespace(timers=timers), create=True), patch.object(Path, 'is_file', return_value=True), patch.object(self.ui, 'gun_preview_id', side_effect=AssertionError('decode in draw')):
            self.ui.draw_gun_preview(layout, context, ROOT / 'cold.png')
        self.assertTrue(layout.events)
        self.assertEqual(len(callbacks), 1)

    def test_every_generated_identity_is_selected_without_io_when_warm(self):
        index = self.assets.generated_previews()
        self.assertEqual(index['source_level'], '1-4')
        identities = 0
        with patch('builtins.open', side_effect=AssertionError('warm file open')), patch('io.open', side_effect=AssertionError('warm path open')), patch.object(Path, 'read_text', side_effect=AssertionError('warm metadata read')), patch.object(Path, 'is_file', side_effect=AssertionError('warm stat')), patch.object(Path, 'glob', side_effect=AssertionError('warm scan')), patch.object(self.assets, 'pioneer_roots', side_effect=AssertionError('warm source discovery')):
            for stem, variants in index['weapons'].items():
                for variant, tiers in variants.items():
                    for tier, stencils in tiers.items():
                        for stencil, filename in stencils.items():
                            selected = self.assets.bundled_preview({'stem': stem}, variant, tier=tier, stencil=stencil)
                            self.assertEqual(selected, ROOT / 'assets/weapon_browser/generated' / filename)
                            identities += 1
        self.assertGreater(identities, 400)
        self.assertLessEqual(identities, 600)

    def test_fixed_quality_weapon_draw_uses_its_only_authored_tier(self):
        entry = next(entry for entry in self.catalog.weapons() if entry['stem'] == 'BattleRifle_EnergyBurst_01')
        self.scene.arc_weapon_key = entry['key']
        self.scene.arc_weapon_tier = '3'
        with patch.object(self.ui, 'ensure_registered', return_value=True), patch.object(self.ui, 'draw_gun_preview'), patch.object(self.assets, 'bundled_preview', return_value=None) as select:
            self.ui.draw(Layout(), self.context)
            select.assert_called_once_with(entry, 'OFF', tier='0', stencil='NONE')

    def test_arc_preview_decode_is_deferred_and_queued_once(self):
        registered = []
        timers = SimpleNamespace(is_registered=lambda callback: callback in registered, register=lambda callback, **kwargs: registered.append(callback))
        class PreviewCache(dict):
            def load(self, *args):
                raise AssertionError('ARC image decode in draw')
        with patch.object(self.ui, '_PREVIEWS', PreviewCache()), patch.object(self.ui, '_ARC_PENDING', set()), patch.object(self.ui.bpy, 'app', SimpleNamespace(timers=timers), create=True), patch.object(Path, 'is_file', return_value=True):
            self.assertEqual(self.ui.arc_preview_id(ROOT / 'assets/arc_browser/test.png'), 0)
            self.assertEqual(self.ui.arc_preview_id(ROOT / 'assets/arc_browser/test.png'), 0)
            self.assertEqual(len(registered), 1)

    def test_arc_timer_prepares_one_image_per_tick_and_redraws(self):
        pending = {'first.png', 'second.png'}
        redraws = []
        windows = [SimpleNamespace(screen=SimpleNamespace(areas=[SimpleNamespace(type='VIEW_3D', tag_redraw=lambda: redraws.append(True))]))]
        with patch.object(self.ui, '_ARC_PENDING', pending), patch.object(self.ui, '_load_arc_preview') as load, patch.object(self.ui.bpy, 'data', SimpleNamespace(images=[]), create=True), patch.object(self.ui.bpy, 'context', SimpleNamespace(window_manager=SimpleNamespace(windows=windows))):
            self.assertEqual(self.ui._prepare_arc_previews(), 0.0)
            self.assertEqual(load.call_count, 1)
            self.assertEqual(len(pending), 1)
            self.assertIsNone(self.ui._prepare_arc_previews())
            self.assertEqual(load.call_count, 2)
            self.assertEqual(len(redraws), 2)
            self.assertFalse(pending)

    def test_warm_arc_draw_does_not_decode_or_schedule(self):
        path = ROOT / 'assets/arc_browser/test.png'
        key = 'arc-5x4-512:' + str(path.resolve())
        with patch.object(self.ui, '_PREVIEWS', {key: SimpleNamespace(icon_id=123)}), patch.object(Path, 'is_file', side_effect=AssertionError('warm ARC stat')), patch.object(self.ui, '_prepare_arc_previews', side_effect=AssertionError('warm ARC decode')):
            self.assertEqual(self.ui.arc_preview_id(path), 123)

    def test_primary_hit_skips_general_discovery_and_old_unpack(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'Items').mkdir()
            (root / 'Items/present.json').write_text('[]')
            (root / 'Items/present.uemodel').write_bytes(b'fixture')
            scene = SimpleNamespace(arc_pioneer_root=str(root))
            original = Path.is_dir
            old = Path(self.assets.SUPPLIED_UNPACK)
            def is_dir(path):
                if path == old or old in path.parents:
                    raise AssertionError('primary hit inspected old unpack')
                return original(path)
            with patch.object(self.utils, 'get_content_dirs', side_effect=AssertionError('primary hit discovered backup roots')), patch.object(Path, 'is_dir', is_dir):
                roots = self.assets.pioneer_roots(scene)
                self.assertEqual(self.assets.resolve('/Game/Pioneer/Items/present', roots), root / 'Items/present.json')
                self.assertEqual(self.assets.resolve('/Game/Pioneer/Items/present', self.assets.current_roots(roots)), root / 'Items/present.json')
                source = ast.parse((ROOT / 'weapon_import.py').read_text(encoding='utf-8'))
                mesh_file = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'mesh_file')
                namespace = {'assets': self.assets}
                exec(compile(ast.Module(body=[mesh_file], type_ignores=[]), str(ROOT / 'weapon_import.py'), 'exec'), namespace)
                self.assertEqual(namespace['mesh_file']('/Game/Pioneer/Items/present', self.assets.current_roots(roots)), root / 'Items/present.uemodel')

    def test_action_miss_resolves_backup_and_current_excludes_old_unpack(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            primary = base / 'primary'
            backup = base / 'backup/Content/Pioneer'
            old = base / 'old'
            for root in (primary, backup, old):
                (root / 'Items').mkdir(parents=True)
            (backup / 'Items/shared.json').write_text('[]')
            (old / 'Items/legacy.json').write_text('[]')
            calls = []
            with patch.object(self.utils, 'get_content_dirs', side_effect=lambda: calls.append('discovery') or [backup.parent]), patch.object(self.assets, 'SUPPLIED_UNPACK', str(old)):
                roots = self.assets.pioneer_roots(SimpleNamespace(arc_pioneer_root=str(primary)))
                self.assertFalse(calls)
                self.assertEqual(self.assets.resolve('/Game/Pioneer/Items/shared', roots), backup / 'Items/shared.json')
                self.assertEqual(self.assets.resolve('/Game/Pioneer/Items/legacy', roots), old / 'Items/legacy.json')
                self.assertEqual(calls, ['discovery'])
                current = self.assets.current_roots(roots)
                self.assertIsNone(self.assets.resolve('/Game/Pioneer/Items/legacy', current))
                self.assertEqual(self.assets.resolve('/Game/Pioneer/Items/shared', current), backup / 'Items/shared.json')
                self.assertEqual(calls, ['discovery', 'discovery'])

    def test_catalog_coverage_and_action_references(self):
        bundled = self.assets.bundled()
        self.assertEqual({entry['stem'] for entry in self.catalog.weapons()}, set(bundled['weapons']))
        self.assertEqual({entry['stem'] for entry in self.catalog.mods()}, set(bundled['mods']))
        for entry in self.catalog.mods():
            if bundled['mods'][entry['stem']].get('preview'):
                self.assertIsNotNone(self.assets.bundled_preview(entry, mod=True))
        for entry in self.catalog.weapons():
            data = bundled['weapons'][entry['stem']]
            self.assertIsNotNone(self.assets.bundled_preview(entry))
            self.assertTrue(data['base_visual'].startswith('/Game/Pioneer/'))
            identifiers = [item['id'] for item in data['variants']]
            numbers = [self.assets.enum_number(identifier) for identifier in identifiers]
            self.assertEqual(len(numbers), len(set(numbers)))
            for item in data['variants'] + data['skins']:
                self.assertTrue(item['reference'].startswith('/Game/Pioneer/'))
            for variant in data['variants']:
                self.assertIsNotNone(self.assets.bundled_preview(entry, variant['id']))

    def test_skin_mi_primary_hit_does_not_build_fallback_roots(self):
        with patch.object(self.catalog, 'list_weapon_skins', return_value=[('Skin', '/primary/MI.json')]), patch.object(self.assets, 'pioneer_roots', side_effect=AssertionError('skin primary hit discovered backups')):
            self.assertEqual(self.catalog.resolve_weapon_skin_mi('/primary', None, 'Skin'), '/primary/MI.json')
        with patch.object(self.catalog, 'list_weapon_skins', side_effect=lambda root, entry: [('Skin', '/backup/MI.json')] if Path(root) == Path('/backup') else []), patch.object(self.assets, 'pioneer_roots', return_value=[Path('/backup')]) as fallback:
            self.assertEqual(self.catalog.resolve_weapon_skin_mi('/primary', None, 'Skin'), '/backup/MI.json')
            fallback.assert_called_once()

    def test_real_skin_action_extracts_missing_visual_then_resolves_it(self):
        source = ast.parse((ROOT / 'weapon_import.py').read_text(encoding='utf-8'))
        action = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == '_skin_visual')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'Items').mkdir()
            entry = {'stem': 'RegressionWeapon', 'kind': 'Firearms', 'name': 'Regression weapon'}
            reference = '/Game/Pioneer/Items/Firearms/RegressionWeapon/Skins/RegressionSkin/DA_WeaponVisuals_RegressionSkin'
            path = root / (reference.removeprefix('/Game/Pioneer/') + '.json')
            calls = []
            def extract(scene, refs):
                calls.extend(refs)
                path.parent.mkdir(parents=True)
                path.write_text('[]')
            pak = ModuleType(PACKAGE + '.pak_extract')
            pak.configured_dir = lambda scene: ''
            namespace = {'__package__': PACKAGE, 'assets': self.assets, 'Path': Path, '_extract_missing': extract}
            exec(compile(ast.Module(body=[action], type_ignores=[]), str(ROOT / 'weapon_import.py'), 'exec'), namespace)
            data = {'weapons': {entry['stem']: {'skins': [{'id': 'RegressionSkin', 'label': 'Regression skin', 'reference': reference}]}}, 'mods': {}}
            with patch.object(self.assets, 'bundled', return_value=data), patch.dict(sys.modules, {pak.__name__: pak}):
                choice, resolved = namespace['_skin_visual'](SimpleNamespace(arc_pioneer_root=str(root)), entry, [root], 'RegressionSkin')
            self.assertEqual(calls, [reference])
            self.assertEqual(resolved, path)
            self.assertEqual(choice['id'], 'RegressionSkin')
            self.assertTrue(resolved.is_file())

    def test_producer_extends_existing_manifest_and_preserves_partial_exports(self):
        spec = importlib.util.spec_from_file_location('_weapon_browser_producer', ROOT / 'tools/regen_weapon_catalog.py')
        producer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(producer)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            patterns = root / 'MaterialLibrary/Textures/Weapons/Patterns'
            patterns.mkdir(parents=True)
            (patterns / 'T_Weapons_Pattern_Fixture_C.png').write_bytes(b'png')
            (patterns / 'T_Weapons_Pattern_Fixture_N.png').write_bytes(b'png')
            catalog_path = root / 'catalog.json'
            existing = {'weapons': {'Gun': {'items': ['unchanged']}}, 'mods': {}, 'patterns': [{'id': 'Kept_C.png', 'label': 'Kept'}]}
            catalog_path.write_text(json.dumps(existing))
            result = producer.refresh_browser_catalog(root, [{'stem': 'Mod'}], catalog_path)
            self.assertEqual(result['weapons'], existing['weapons'])
            self.assertIn('Mod', result['mods'])
            self.assertEqual(result['patterns'], [{'id': 'Kept_C.png', 'label': 'Kept'}, {'id': 'T_Weapons_Pattern_Fixture_C.png', 'label': 'Fixture'}])
            self.assertEqual(producer.refresh_browser_catalog(root, [{'stem': 'Mod'}], catalog_path), result)


if __name__ == '__main__':
    unittest.main(argv=[__file__], verbosity=2)
