import importlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class BrowserTypeSwitchRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.browser = importlib.import_module(ROOT.name + ".Outfit_Accessories.icon_browser")

    def test_type_switch_prepares_visible_page_without_parent_check(self):
        for page in (1, 4):
            with self.subTest(page=page):
                operator = SimpleNamespace(kind="weapon", filter="", page=page)
                context = SimpleNamespace(area=None)
                with patch.object(self.browser, "_clamp_page"), patch.object(self.browser, "_warm_browser_thumbnails") as warm, patch.object(self.browser, "_tag_browser_redraw") as redraw:
                    self.browser._reset_page(operator, context)
                self.assertEqual(operator.page, 1)
                warm.assert_called_once_with(operator, context)
                redraw.assert_called_once_with(context)

    def test_page_number_edit_prepares_visible_page_without_parent_check(self):
        operator = SimpleNamespace(kind="weapon", filter="", page=2)
        context = SimpleNamespace(area=None, scene=SimpleNamespace())
        with patch.object(self.browser, "collect_items", return_value=[("item", "Item")] * 31), patch.object(self.browser, "_warm_browser_thumbnails") as warm, patch.object(self.browser, "_tag_browser_redraw") as redraw:
            self.browser._update_page(operator, context)
        warm.assert_called_once_with(operator, context)
        redraw.assert_called_once_with(context)
