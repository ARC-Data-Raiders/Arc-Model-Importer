import importlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]


class BrowserProgressiveRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addon = importlib.import_module(ROOT.name)
        cls.browser = importlib.import_module(ROOT.name + '.Outfit_Accessories.icon_browser')
        if not hasattr(bpy.types, 'ARC_OUTFITS_OT_icon_browser'):
            cls.addon.register()

    def test_import_timer_event_has_no_timer_attribute(self):
        steps = []
        scene = object()
        operator = SimpleNamespace(_scene=scene, _timer=object(), _pending=['NPC_APOLLO'],
                                   _failed=[], _step=lambda context: steps.append('NPC_APOLLO'),
                                   _finish=lambda: None)
        with patch.object(self.browser, '_job_busy', return_value=False):
            result = self.browser.ARC_OUTFITS_OT_ImportBrowserSelection.modal(
                operator, SimpleNamespace(scene=scene), SimpleNamespace(type='TIMER'))
        self.assertEqual(result, {'RUNNING_MODAL'})
        self.assertEqual(steps, ['NPC_APOLLO'])

    def test_fixed_three_rows_five_columns_has_no_layout_setting(self):
        self.assertNotIn('columns', self.browser.ARC_OUTFITS_OT_IconBrowser.__annotations__)
        self.assertFalse(hasattr(bpy.types.WindowManager, 'arc_browser_columns'))
        self.assertEqual(self.browser.BROWSER_COLUMNS, 5)
        self.assertEqual(self.browser.BROWSER_ROWS, 3)
        self.assertGreaterEqual(self.browser.BROWSER_WIDTH, 1000)

    def test_fixed_page_clamps_at_fifteen_items(self):
        operator = SimpleNamespace(kind='outfit', filter='', page=99, columns='3')
        with patch.object(self.browser, 'collect_items', return_value=[('test', 'Test')] * 31):
            self.browser._clamp_page(operator, SimpleNamespace(scene=object()))
        self.assertEqual(operator.page, 3)

    def test_every_bundled_preview_uses_its_original_resolution_avif(self):
        previews = importlib.import_module(ROOT.name + '.Outfit_Accessories.preview_assets')
        checked = 0
        for relative in ('assets/ui_icons/appearance', 'assets/arc_browser', 'assets/weapon_browser'):
            for source in (ROOT / relative).rglob('*.avif'):
                placeholder = Path(previews.low_resolution_path(str(source)))
                self.assertTrue(placeholder.is_file(), str(source))
                self.assertEqual(placeholder.suffix, '.avif')
                image = bpy.data.images.load(str(placeholder), check_existing=False)
                original = bpy.data.images.load(str(source), check_existing=False)
                try:
                    self.assertGreater(min(image.size), 0)
                    self.assertEqual(tuple(image.size), tuple(original.size))
                finally:
                    bpy.data.images.remove(image)
                    bpy.data.images.remove(original)
                checked += 1
        self.assertGreater(checked, 800)
        import json
        manifest = json.loads((ROOT / 'assets/preview_thumbnails/manifest.json').read_text())
        self.assertEqual(manifest['scale'], 1.0)
        self.assertEqual(manifest['compression'], 'original-avif')


    def test_prewarmed_thumbnail_is_immediate_and_failed_upgrade_keeps_it(self):
        appearance = importlib.import_module(ROOT.name + '.Outfit_Accessories.appearance')
        assets = importlib.import_module(ROOT.name + '.Outfit_Accessories.preview_assets')
        source = str(next((ROOT / 'assets/ui_icons/appearance').glob('*.avif')))

        class Previews(dict):
            def new(self, key):
                self[key] = SimpleNamespace(icon_id=310, image_size=(0, 0), image_pixels=[])
                return self[key]

            def load(self, key, path, kind):
                self[key] = SimpleNamespace(icon_id=310, image_size=(96, 48))
                return self[key]

        previews = Previews()
        with patch.object(appearance, '_PREVIEW_COLLECTION', previews), patch.object(appearance, 'register_previews'), patch.object(assets, 'source_preview_path', return_value=source), patch.object(appearance, '_queued_preview_icon_id_for_path', return_value=0):
            self.assertEqual(appearance._low_resolution_icon_id(source, prepare=True), 310)
            self.assertEqual(appearance._preview_icon_id_for_path(source), 310)
            self.assertEqual(appearance._preview_icon_id_for_path(source), 310)
            self.assertEqual(len(previews), 1)
            self.assertIn(assets.low_resolution_path(source).lower(), next(iter(previews)).lower())
        with patch.object(assets, 'source_preview_path', side_effect=AssertionError('No source upgrades')), patch.object(appearance, '_queued_preview_icon_id_for_path', side_effect=AssertionError('No replacement')), patch.object(appearance, '_low_resolution_icon_id', return_value=310):
            self.assertEqual(appearance._preview_icon_id_for_path(source), 310)

    def test_thumbnail_draw_never_loads_an_image(self):
        appearance = importlib.import_module(ROOT.name + '.Outfit_Accessories.appearance')
        assets = importlib.import_module(ROOT.name + '.Outfit_Accessories.preview_assets')
        source = str(next((ROOT / 'assets/ui_icons/appearance').glob('*.avif')))

        loads = []

        class Previews(dict):
            def load(self, *arguments):
                loads.append(arguments)
                raise AssertionError('Drawing must not decode images')

        with patch.object(appearance, '_PREVIEW_COLLECTION', Previews()), patch.object(assets, 'source_preview_path', return_value=source), patch.object(appearance, '_queued_preview_icon_id_for_path', return_value=0):
            self.assertEqual(appearance._preview_icon_id_for_path(source), 0)
        self.assertEqual(loads, [])

    def test_popup_warms_visible_thumbnails_before_first_draw(self):
        appearance = importlib.import_module(ROOT.name + '.Outfit_Accessories.appearance')
        items = [(str(index), 'Item') for index in range(35)]
        operator = SimpleNamespace(kind='outfit', filter='', page=2)
        warmed = []
        with patch.object(self.browser, 'collect_items', return_value=items), patch.object(self.browser, 'icon_path', side_effect=lambda context, kind, ident: ident), patch.object(appearance, '_low_resolution_icon_id', side_effect=lambda path, **options: warmed.append((path, options))):
            self.browser._warm_browser_thumbnails(operator, SimpleNamespace())
        self.assertEqual([path for path, options in warmed], [str(index) for index in range(15, 30)])
        self.assertTrue(all(options.get('priority') for path, options in warmed))
        self.assertTrue(any(options.get('prepare') for path, options in warmed))

    def test_packaged_source_identity_never_queues_extraction(self):
        assets = importlib.import_module(ROOT.name + '.Outfit_Accessories.preview_assets')
        context = SimpleNamespace(scene=SimpleNamespace(arc_pak_dir='game'))
        with patch.object(bpy.app.timers, 'register', side_effect=AssertionError('No upgrade timer')):
            self.assertEqual(assets.source_preview_path('bundled.avif', context), 'bundled.avif')
            self.assertFalse(hasattr(assets, '_prepare_sources'))

    def test_sidebar_replaces_a_cached_thumbnail_when_preview_generation_changes(self):
        sidebar = importlib.import_module(ROOT.name + '.sidebar')
        appearance = importlib.import_module(ROOT.name + '.Outfit_Accessories.appearance')
        owner = object()
        with patch.object(sidebar, '_ICON_MEMO', {}), patch.object(appearance, '_PREVIEW_GENERATION', 100):
            self.assertEqual(sidebar._icon_memo(('progressive',), owner, lambda: 310), 310)
            self.assertEqual(sidebar._icon_memo(('progressive',), owner, lambda: 520), 310)
            appearance._PREVIEW_GENERATION += 1
            self.assertEqual(sidebar._icon_memo(('progressive',), owner, lambda: 520), 520)


    def test_painted_weapon_composites_are_not_replaced_with_base_icons(self):
        import json
        assets = importlib.import_module(ROOT.name + '.Outfit_Accessories.preview_assets')
        generated = json.loads((ROOT / 'assets/weapon_browser/generated/index.json').read_text())
        checked = 0
        for variants in generated['weapons'].values():
            for tiers in variants.values():
                for stencils in tiers.values():
                    for stencil, filename in stencils.items():
                        if stencil != 'NONE':
                            self.assertFalse(assets.manifest()['assets/weapon_browser/generated/' + filename]['package'])
                            checked += 1
        self.assertGreater(checked, 0)


if __name__ == '__main__':
    unittest.main()

