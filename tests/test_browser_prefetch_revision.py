import importlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]


class BrowserPrefetchRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.browser = importlib.import_module(ROOT.name + '.Outfit_Accessories.icon_browser')
        cls.appearance = importlib.import_module(ROOT.name + '.Outfit_Accessories.appearance')
        cls.assets = importlib.import_module(ROOT.name + '.Outfit_Accessories.preview_assets')

    def test_page_button_prepares_thumbnails_without_parent_check(self):
        operator = SimpleNamespace(kind='outfit', filter='', page=1)
        context = SimpleNamespace(area=None)
        with patch.object(self.browser, '_BROWSER_DIALOG', operator), patch.object(self.browser, 'collect_items', return_value=[('item', 'Item')] * 31), patch.object(self.browser, '_warm_browser_thumbnails') as warm:
            result = self.browser.ARC_OUTFITS_OT_BrowserPage.execute(SimpleNamespace(direction=1), context)
        self.assertEqual(result, {'FINISHED'})
        self.assertEqual(operator.page, 2)
        warm.assert_called_once_with(operator, context)

    def test_prefetch_follows_current_page_and_only_loads_next_page(self):
        operator = SimpleNamespace(kind='outfit', filter='', page=1)
        items = [(str(index), 'Item') for index in range(50)]
        warmed = []
        with patch.object(self.browser, '_BROWSER_DIALOG', operator), patch.object(self.browser, '_BROWSER_PREFETCH', None), patch.object(self.browser, 'collect_items', return_value=items), patch.object(self.browser, 'icon_path', side_effect=lambda context, kind, ident: ident), patch.object(self.appearance, '_low_resolution_icon_id', side_effect=lambda path, **options: warmed.append(path)), patch.object(self.appearance, '_tag_preview_redraw'), patch.object(self.appearance, '_PREVIEW_PENDING', {}), patch.object(self.appearance, '_preview_icon_id_for_path', return_value=1), patch.object(bpy.app.timers, 'is_registered', return_value=True):
            self.browser._warm_browser_thumbnails(operator, bpy.context)
            self.assertEqual(warmed, [str(index) for index in range(15)])
            ticks = 0
            while self.browser._prepare_browser_prefetch() is not None:
                ticks += 1
                self.assertLess(ticks, 40)
            self.assertEqual(warmed, [str(index) for index in range(30)])

    def test_prefetch_is_bounded_and_stops_after_page_changes(self):
        operator = SimpleNamespace(kind='outfit', filter='', page=1)
        with patch.object(self.browser, '_BROWSER_DIALOG', operator), patch.object(self.browser, '_BROWSER_PREFETCH', None), patch.object(self.browser, 'collect_items', return_value=[(str(index), 'Item') for index in range(45)]), patch.object(self.browser, 'icon_path', return_value='preview'), patch.object(self.appearance, '_low_resolution_icon_id') as load, patch.object(self.appearance, '_tag_preview_redraw'), patch.object(bpy.app.timers, 'is_registered', return_value=True):
            self.browser._warm_browser_thumbnails(operator, bpy.context)
            load.reset_mock()
            self.browser._prepare_browser_prefetch()
            self.assertLessEqual(load.call_count, 2)
            operator.page = 3
            load.reset_mock()
            self.assertIsNone(self.browser._prepare_browser_prefetch())
            load.assert_not_called()

    def test_prefetch_finishes_without_queuing_source_upgrades(self):
        operator = SimpleNamespace(kind='outfit', filter='', page=1)
        with patch.object(self.browser, '_BROWSER_DIALOG', operator), patch.object(self.browser, '_BROWSER_PREFETCH', None), patch.object(self.browser, 'collect_items', return_value=[(str(index), 'Item') for index in range(30)]), patch.object(self.browser, 'icon_path', return_value='preview'), patch.object(self.appearance, '_low_resolution_icon_id'), patch.object(self.appearance, '_tag_preview_redraw'), patch.object(self.appearance, '_PREVIEW_PENDING', {'visible': 'visible'}), patch.object(self.appearance, '_preview_icon_id_for_path') as upgrade, patch.object(bpy.app.timers, 'is_registered', return_value=True):
            self.browser._warm_browser_thumbnails(operator, bpy.context)
            for tick in range(12):
                self.browser._prepare_browser_prefetch()
            upgrade.assert_not_called()
            self.appearance._PREVIEW_PENDING.clear()
            self.browser._prepare_browser_prefetch()
            upgrade.assert_not_called()
            self.assertIsNone(self.browser._BROWSER_PREFETCH)

    def test_closing_browser_cancels_prefetch(self):
        operator = SimpleNamespace(kind='outfit', filter='', page=1)
        with patch.object(self.browser, '_BROWSER_DIALOG', operator), patch.object(self.browser, '_BROWSER_PREFETCH', None), patch.object(self.browser, 'collect_items', return_value=[('item', 'Item')] * 30), patch.object(self.browser, 'icon_path', return_value='preview'), patch.object(self.appearance, '_low_resolution_icon_id') as load, patch.object(bpy.app.timers, 'is_registered', return_value=True):
            self.browser._warm_browser_thumbnails(operator, bpy.context)
            self.browser._BROWSER_DIALOG = None
            load.reset_mock()
            self.assertIsNone(self.browser._prepare_browser_prefetch())
            load.assert_not_called()

    def test_visible_icons_use_foreground_priority(self):
        with patch.object(self.browser, 'icon_path', return_value='preview'), patch.object(self.appearance, '_preview_icon_id_for_path', return_value=10) as load:
            self.assertEqual(self.browser.icon_id(bpy.context, 'outfit', 'item'), 10)
            load.assert_called_once_with('preview', priority=True)

    def test_cold_page_preparation_queues_remainder_after_time_budget(self):
        operator = SimpleNamespace(kind='outfit', filter='', page=1)
        clock = iter([0.0, 0.0] + [0.05] * 14)
        with patch.object(self.browser, 'collect_items', return_value=[('item', 'Item')] * 15), patch.object(self.browser, 'icon_path', return_value='preview'), patch.object(self.browser.time, 'perf_counter', side_effect=lambda: next(clock)), patch.object(self.appearance, '_low_resolution_icon_id') as load:
            self.browser._warm_browser_thumbnails(operator, bpy.context)
        self.assertEqual(load.call_count, 15)
        self.assertEqual(sum(call.kwargs['prepare'] for call in load.call_args_list), 1)
        self.assertTrue(all(call.kwargs['priority'] for call in load.call_args_list))

    def test_visible_thumbnail_queue_runs_before_old_requests(self):
        loaded = []
        with patch.object(self.appearance, '_LOW_RESOLUTION_PENDING', {'old': 'old', 'visible': 'visible'}), patch.object(self.appearance, '_LOW_RESOLUTION_PRIORITY', {'visible'}), patch.object(self.appearance, '_load_low_resolution_thumbnail', side_effect=lambda path: loaded.append(path)), patch.object(self.appearance, '_tag_preview_redraw'):
            self.appearance._prepare_low_resolution_previews()
        self.assertEqual(loaded, ['visible', 'old'])
