import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))


class RecordingLayout:
    def __init__(self, events=None):
        self.events = [] if events is None else events
        self.scale_y = 1.0

    def row(self, **kwargs):
        return RecordingLayout(self.events)

    def column(self, **kwargs):
        return RecordingLayout(self.events)

    def grid_flow(self, **kwargs):
        return RecordingLayout(self.events)

    def prop(self, *args, **kwargs):
        pass

    def operator(self, name, **kwargs):
        self.events.append(('operator', name, kwargs, self.scale_y))
        return SimpleNamespace()

    def template_popup_confirm(self, *args, **kwargs):
        return SimpleNamespace()

    def template_icon(self, **kwargs):
        self.events.append(('icon', kwargs, self.scale_y))

    def template_preview(self, *args, **kwargs):
        self.events.append(('render', kwargs, self.scale_y))


class BrowserStabilityRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addon = importlib.import_module(ROOT.name)
        cls.browser = importlib.import_module(ROOT.name + '.Outfit_Accessories.icon_browser')
        cls.appearance = importlib.import_module(ROOT.name + '.Outfit_Accessories.appearance')
        if not hasattr(bpy.types, 'ARC_OUTFITS_OT_icon_browser'):
            cls.addon.register()

    def test_resize_operator_is_not_registered(self):
        self.assertFalse(hasattr(bpy.types, 'ARC_OUTFITS_OT_resize_icon_browser'))
        self.assertFalse(hasattr(bpy.types.WindowManager, 'arc_browser_width'))
        self.assertFalse(hasattr(bpy.types.WindowManager, 'arc_browser_height'))

    def draw_browser(self, icon):
        layout = RecordingLayout()
        operator = SimpleNamespace(layout=layout, kind='weapon', filter='', columns='3', width=720, height=0, page=1)
        context = SimpleNamespace(region=None, window_manager=SimpleNamespace(arc_browser_multiple=False))
        before = (len(bpy.data.images), len(bpy.data.textures))
        with patch.object(self.browser, 'collect_items', return_value=[('test', 'Test (Test)')]), patch.object(self.browser, 'selected_items', return_value=set()), patch.object(self.browser, 'current_selection', return_value='test'), patch.object(self.browser, 'icon_id', return_value=icon), patch.object(self.browser, '_job_busy', return_value=False):
            self.browser.ARC_OUTFITS_OT_IconBrowser.draw(operator, context)
        self.assertEqual(before, (len(bpy.data.images), len(bpy.data.textures)))
        return layout.events

    def test_loaded_icon_is_drawn_immediately_without_render_job(self):
        events = self.draw_browser(987654)
        icons = [event for event in events if event[0] == 'icon']
        self.assertEqual(len(icons), 1, 'A loaded preview must appear on the first draw, without a second texture/render queue')
        self.assertEqual(icons[0][1]['icon_value'], 987654)
        self.assertFalse(any(event[0] == 'render' for event in events))

    def test_pending_and_loaded_icons_have_identical_tile_geometry(self):
        pending = [event for event in self.draw_browser(0) if event[0] == 'icon']
        loaded = [event for event in self.draw_browser(987654) if event[0] == 'icon']
        self.assertEqual(len(pending), 1)
        self.assertEqual(len(loaded), 1)
        self.assertEqual((pending[0][1]['scale'], pending[0][2]), (loaded[0][1]['scale'], loaded[0][2]))

    def test_unchanged_check_does_not_rebuild_popup(self):
        operator = SimpleNamespace(kind='weapon', filter='', columns='3', width=720, page=1)
        context = SimpleNamespace(scene=object(), window_manager=SimpleNamespace(arc_browser_multiple=False))
        with patch.object(self.browser, 'collect_items', return_value=[('test', 'Test (Test)')]):
            self.browser.ARC_OUTFITS_OT_IconBrowser.check(operator, context)
            self.assertFalse(self.browser.ARC_OUTFITS_OT_IconBrowser.check(operator, context))

    def test_corrupt_cached_preview_falls_back_to_source_in_same_batch(self):
        cache = importlib.import_module(ROOT.name + '.Outfit_Accessories.preview_cache')
        from unittest.mock import MagicMock
        preview = SimpleNamespace(image_size=(4, 2), icon_id=24680)
        collection = MagicMock()
        collection.load.side_effect = [ValueError('corrupt retained thumbnail'), preview]
        pending, failed, retry = {}, set(), {}
        source, cached = 'source-preview.avif', 'corrupt-cache.png'
        pending['source'] = source
        with patch.object(self.appearance, '_PREVIEW_COLLECTION', collection), patch.object(self.appearance, '_PREVIEW_PENDING', pending), patch.object(self.appearance, '_PREVIEW_PRIORITY', set()), patch.object(self.appearance, '_PREVIEW_FAILED', failed), patch.object(self.appearance, '_PREVIEW_RETRY_AFTER', retry), patch.object(self.appearance, 'register_previews'), patch.object(cache, 'cache_path', return_value=cached), patch.object(self.appearance.os.path, 'isfile', return_value=True), patch.object(self.appearance.os, 'remove'), patch.object(self.appearance, '_crop_sidebar_preview', return_value=None):
            self.appearance._prepare_sidebar_previews()
        self.assertEqual([call.args[1] for call in collection.load.call_args_list], [cached, source])
        self.assertNotIn('source', failed)
        self.assertFalse(pending)


    def test_empty_cached_preview_is_replaced_without_waiting_for_retry(self):
        cache = importlib.import_module(ROOT.name + '.Outfit_Accessories.preview_cache')
        from unittest.mock import MagicMock
        empty = SimpleNamespace(image_size=(0, 0))
        loaded = SimpleNamespace(image_size=(4, 2), icon_id=24680)
        collection = MagicMock()
        collection.load.side_effect = [empty, loaded]
        pending, failed, retry = {'source': 'source.avif'}, set(), {}
        with patch.object(self.appearance, '_PREVIEW_COLLECTION', collection), patch.object(self.appearance, '_PREVIEW_PENDING', pending), patch.object(self.appearance, '_PREVIEW_PRIORITY', set()), patch.object(self.appearance, '_PREVIEW_FAILED', failed), patch.object(self.appearance, '_PREVIEW_RETRY_AFTER', retry), patch.object(self.appearance, 'register_previews'), patch.object(cache, 'cache_path', return_value='cache.png'), patch.object(self.appearance.os.path, 'isfile', return_value=True), patch.object(self.appearance.os, 'remove'), patch.object(self.appearance, '_crop_sidebar_preview', return_value=None):
            self.appearance._prepare_sidebar_previews()
        self.assertEqual([call.args[1] for call in collection.load.call_args_list], ['cache.png', 'source.avif'])
        self.assertFalse(failed)
        self.assertFalse(retry)

    def test_real_blender_decoder_recovers_corrupt_thumbnail(self):
        import os
        import numpy as np
        import bpy.utils.previews
        cache = importlib.import_module(ROOT.name + '.Outfit_Accessories.preview_cache')
        root = Path(os.environ['DATARAIDERS_TEST_TEMP']) / 'browser-recovery'
        source = str(root / 'source.png')
        cache.store(source, np.full((2, 4, 4), 255, dtype=np.uint8))
        cached = Path(cache.cache_path(source))
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(b'corrupt thumbnail')
        key = os.path.normcase(source)
        pending, failed, retry = {key: source}, set(), {}
        collection = bpy.utils.previews.new()
        try:
            with patch.object(self.appearance, '_PREVIEW_COLLECTION', collection), patch.object(self.appearance, '_PREVIEW_PENDING', pending), patch.object(self.appearance, '_PREVIEW_PRIORITY', set()), patch.object(self.appearance, '_PREVIEW_FAILED', failed), patch.object(self.appearance, '_PREVIEW_RETRY_AFTER', retry), patch.object(self.appearance, 'register_previews'):
                self.appearance._prepare_sidebar_previews()
                self.assertIn(key, collection)
                self.assertEqual(tuple(collection[key].image_size), (4, 2))
                self.assertEqual(len(collection[key].image_pixels), 8)
                self.assertFalse(pending)
                self.assertFalse(failed)
                self.assertFalse(retry)
                self.assertTrue(cached.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
        finally:
            bpy.utils.previews.remove(collection)


if __name__ == '__main__':
    unittest.main()
