import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))


class BrowserCodenameRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addon = importlib.import_module(ROOT.name)
        cls.browser = importlib.import_module(ROOT.name + '.Outfit_Accessories.icon_browser')
        cls.appearance = importlib.import_module(ROOT.name + '.Outfit_Accessories.appearance')
        if not hasattr(bpy.types, 'ARC_OUTFITS_OT_icon_browser'):
            cls.addon.register()

    def test_appearance_labels_use_exact_folders(self):
        for kind in ('head', 'hair', 'facial_hair', 'backpack', 'attachment', 'charm'):
            items = self.browser.collect_items(bpy.context, kind)
            self.assertTrue(items, kind)
            for ident, label in items:
                choice = self.appearance.resolve_choice(ident)
                folder = (choice or {}).get('folder')
                if folder:
                    self.assertIn(('(' + folder + ')').casefold(), label.casefold(), (kind, ident, label))

    def test_weapon_and_enemy_use_catalog_identities(self):
        weapons = importlib.import_module(ROOT.name + '.Weapons.weapon_catalog')
        enemies = importlib.import_module(ROOT.name + '.Arc.arc_catalog_data')
        for ident, label in self.browser.collect_items(bpy.context, 'weapon'):
            self.assertIn(('(' + weapons.weapon_by_key(ident)['stem'] + ')').casefold(), label.casefold())
        for ident, label in self.browser.collect_items(bpy.context, 'enemy'):
            self.assertIsNotNone(enemies.by_key(ident))
            self.assertIn(('(' + ident + ')').casefold(), label.casefold())

    def test_outfit_and_effect_keep_source_labels(self):
        properties = importlib.import_module(ROOT.name + '.Outfit_Accessories.properties')
        properties._refresh_outfit_csv_state()
        outfits = self.browser.collect_items(bpy.context, 'outfit')
        self.assertTrue(outfits)
        importing = importlib.import_module(ROOT.name + '.Shared.importing')
        csv_path, _mtime = properties.outfit_csv_state(bpy.context)
        rows = properties._CSV_ROWS_MEMO[csv_path]
        for ident, label in outfits:
            row = importing.find_outfit_row(rows, ident) or {}
            codename = self.browser._outfit_codename(row)
            self.assertTrue(codename, ident)
            self.assertIn(('(' + codename + ')').casefold(), label.casefold())
        self.assertEqual(self.browser._outfit_codename({'Pak Mesh Paths': 'Characters/Pilot/SK_Pilot.psk'}), 'SK_Pilot')
        self.assertEqual(self.browser._outfit_codename({'ST': 'ID_TIANWEN_NAME'}), 'ID_TIANWEN_NAME')
        niagara = importlib.import_module(ROOT.name + '.Niagara.niagara')
        entry = {'key': 'NS_Test', 'stem': 'NS_Test', 'folder': 'Effects/Test'}
        with patch.object(niagara, 'filter_entries', return_value=[entry]):
            self.assertEqual(self.browser._raw_items(bpy.context, 'effect')[0][1], niagara.display_label(entry) + ' (NS_Test)')

    def test_page_clamps_after_item_count_type_and_filter_changes(self):
        browser = SimpleNamespace(kind='weapon', filter='', columns='3', page=999)
        counts = {'weapon': 37, 'enemy': 8}
        context = SimpleNamespace(scene=object())
        with patch.object(self.browser, 'collect_items', side_effect=lambda ctx, kind, query: list(range(2 if query else counts[kind]))):
            self.browser._clamp_page(browser, context)
            self.assertEqual(browser.page, 3)
            browser.columns, browser.page = '5', 999
            self.browser._clamp_page(browser, context)
            self.assertEqual(browser.page, 3)
            browser.kind, browser.page = 'enemy', 999
            self.browser._clamp_page(browser, context)
            self.assertEqual(browser.page, 1)
            browser.kind, browser.filter, browser.page = 'weapon', 'filtered', 999
            self.browser._clamp_page(browser, context)
            self.assertEqual(browser.page, 1)
            browser.filter, browser.page = '', 999
            counts['weapon'] = 0
            self.browser._clamp_page(browser, context)
            self.assertEqual(browser.page, 1)

    def test_native_placeholder_icon_is_valid(self):
        expected = bpy.types.UILayout.bl_rna.functions['label'].parameters['icon'].enum_items['IMAGE_DATA'].value
        self.assertEqual(self.browser._placeholder_icon_id(), expected)
        self.assertGreater(expected, 0)

    def test_equal_human_name_and_codename_remain_explicit(self):
        self.assertEqual(self.browser._coded_label('Wasp', 'Wasp'), 'Wasp (Wasp)')

    def test_cancel_keeps_loaded_previews_available_for_reopen(self):
        preview = SimpleNamespace(icon_id=987654, image_size=(4, 2))
        collection = {'test': preview}
        operator = SimpleNamespace()
        old_dialog = self.browser._BROWSER_DIALOG
        self.browser._BROWSER_DIALOG = operator
        try:
            with patch.object(self.appearance, '_PREVIEW_COLLECTION', collection):
                self.browser.ARC_OUTFITS_OT_IconBrowser.cancel(operator, bpy.context)
                self.assertIsNone(self.browser._BROWSER_DIALOG)
                self.assertIs(self.appearance._PREVIEW_COLLECTION['test'], preview)
        finally:
            self.browser._BROWSER_DIALOG = old_dialog

    def test_preview_completion_requests_one_popup_update(self):
        operator = SimpleNamespace(kind='weapon', filter='', columns='3', page=1)
        context = SimpleNamespace(scene=object(), window_manager=SimpleNamespace(arc_browser_multiple=False))
        with patch.object(self.browser, 'collect_items', return_value=[('test', 'Test (Test)')]), patch.object(self.appearance, '_PREVIEW_GENERATION', 10):
            self.assertTrue(self.browser.ARC_OUTFITS_OT_IconBrowser.check(operator, context))
            self.assertFalse(self.browser.ARC_OUTFITS_OT_IconBrowser.check(operator, context))
            self.appearance._PREVIEW_GENERATION = 11
            self.assertTrue(self.browser.ARC_OUTFITS_OT_IconBrowser.check(operator, context))
            self.assertFalse(self.browser.ARC_OUTFITS_OT_IconBrowser.check(operator, context))

    def test_real_blender_preview_pixels_are_reused_without_scene_textures(self):
        import bpy.utils.previews
        collection = bpy.utils.previews.new()
        preview = collection.new('browser-memory.avif')
        preview.image_size = (4, 2)
        preview.image_pixels = [-1] * 8
        before = (len(bpy.data.images), len(bpy.data.textures))
        pending = {}
        try:
            with patch.object(self.appearance, '_PREVIEW_COLLECTION', collection), patch.object(self.appearance, '_PREVIEW_PENDING', pending):
                for repeat in range(100):
                    self.assertEqual(self.appearance._preview_icon_id_for_path('browser-memory.avif', priority=False), preview.icon_id)
                self.assertFalse(pending)
                self.assertEqual(tuple(preview.image_size), (4, 2))
                self.assertEqual(list(preview.image_pixels), [-1] * 8)
                self.assertEqual(before, (len(bpy.data.images), len(bpy.data.textures)))
        finally:
            bpy.utils.previews.remove(collection)

    def test_weapon_preview_uses_public_helper(self):
        self.browser._PATH_MEMO.clear()
        with patch.object(self.appearance, 'browser_preview_path', return_value='exact-preview', create=True) as helper:
            self.assertEqual(self.browser.icon_path(bpy.context, 'weapon', 'exact-key'), 'exact-preview')
            helper.assert_called_once_with(bpy.context, 'weapon', 'exact-key')

    def test_weapon_preview_uses_real_public_helper(self):
        catalog = importlib.import_module(ROOT.name + '.Weapons.weapon_catalog')
        ident = catalog.weapons()[0]['key']
        expected = self.appearance.browser_preview_path(bpy.context, 'weapon', ident)
        self.assertTrue(expected)
        self.browser._PATH_MEMO.clear()
        self.assertEqual(self.browser.icon_path(bpy.context, 'weapon', ident), expected)


if __name__ == '__main__':
    unittest.main()
