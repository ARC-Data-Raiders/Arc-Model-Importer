import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BROWSER = ROOT / "Outfit_Accessories/icon_browser.py"


def browser_class():
    return next(node for node in ast.parse(BROWSER.read_text()).body if isinstance(node, ast.ClassDef) and node.name == "ARC_OUTFITS_OT_IconBrowser")


class BrowserPopupRevisionTests(unittest.TestCase):
    def test_popup_has_no_width_slider_or_reopen_resize_button(self):
        tree = browser_class()
        draw = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "draw")
        calls = [node for node in ast.walk(draw) if isinstance(node, ast.Call)]
        width_sliders = [node for node in calls if isinstance(node.func, ast.Attribute) and node.func.attr == "prop" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and node.args[1].value == "width"]
        self.assertEqual(width_sliders, [])
        self.assertTrue(any(isinstance(node.func, ast.Attribute) and node.func.attr == "template_popup_confirm" and any(keyword.arg == "cancel_text" and ast.literal_eval(keyword.value) == "" for keyword in node.keywords) for node in calls))

    def test_page_is_one_based_and_has_two_navigation_buttons(self):
        tree = browser_class()
        page = next(node for node in tree.body if isinstance(node, ast.AnnAssign) and node.target.id == "page")
        keywords = {keyword.arg: ast.literal_eval(keyword.value) for keyword in page.annotation.keywords if keyword.arg in ("min", "default")}
        self.assertEqual(keywords, {"min": 1, "default": 1})
        draw = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "draw")
        calls = [node for node in ast.walk(draw) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "operator"]
        arrows = [keyword.value.value for node in calls for keyword in node.keywords if keyword.arg == "icon" and isinstance(keyword.value, ast.Constant) and keyword.value.value in ("TRIA_LEFT", "TRIA_RIGHT")]
        self.assertCountEqual(arrows, ["TRIA_LEFT", "TRIA_RIGHT"])

    def test_popup_has_no_resize_operator_or_height_override(self):
        tree = ast.parse(BROWSER.read_text())
        self.assertFalse(any(isinstance(node, ast.ClassDef) and node.name == "ARC_OUTFITS_OT_ResizeIconBrowser" for node in tree.body))
        draw = next(node for node in browser_class().body if isinstance(node, ast.FunctionDef) and node.name == "draw")
        self.assertFalse(any(isinstance(node, ast.Attribute) and node.attr == "ui_units_y" for node in ast.walk(draw)))
        preview_calls = [node for node in ast.walk(draw) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "template_icon"]
        self.assertEqual(len(preview_calls), 1)
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "template_preview" for node in ast.walk(draw)))

    def test_multiple_toggle_keeps_current_selection(self):
        tree = ast.parse(BROWSER.read_text())
        callback = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "on_multiple_changed"), None)
        self.assertIsNotNone(callback)
        chosen = {"head": {"older"}, "outfit": set()}
        scope = {"KIND_PROP": {"head": "head", "outfit": "outfit"}, "current_selection": lambda context, kind: {"head": "current", "outfit": "selected"}[kind], "selected_items": lambda context, kind: chosen[kind]}
        exec(compile(ast.Module(body=[callback], type_ignores=[]), str(BROWSER), "exec"), scope)
        scope["on_multiple_changed"](SimpleNamespace(window_manager=SimpleNamespace(arc_browser_multiple=True)))
        self.assertEqual(chosen["head"], {"older", "current"})
        self.assertEqual(chosen["outfit"], {"selected"})

    def test_no_resize_button_or_persisted_height_is_exposed(self):
        tree = browser_class()
        fields = {node.target.id for node in tree.body if isinstance(node, ast.AnnAssign)}
        self.assertNotIn("height", fields)
        draw = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "draw")
        values = [node.value for node in ast.walk(draw) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        self.assertNotIn("arc_outfits.resize_icon_browser", values)
        self.assertNotIn("FULLSCREEN_ENTER", values)

    def test_page_buttons_clamp_at_both_ends(self):
        tree = ast.parse(BROWSER.read_text())
        page_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ARC_OUTFITS_OT_BrowserPage")
        execute = next(node for node in page_class.body if isinstance(node, ast.FunctionDef) and node.name == "execute")
        browser = SimpleNamespace(page=1, columns="3", kind="head", filter="")
        scope = {"_BROWSER_DIALOG": browser, "collect_items": lambda *args: list(range(23)), "BROWSER_PAGE_SIZE": 15, "_tag_browser_redraw": lambda context: None}
        scope['_warm_browser_thumbnails'] = lambda operator, context: None
        exec(compile(ast.Module(body=[execute], type_ignores=[]), str(BROWSER), "exec"), scope)
        for direction, expected in ((-1, 1), (1, 2), (1, 2), (1, 2), (-1, 1)):
            self.assertEqual(scope["execute"](SimpleNamespace(direction=direction), None), {"FINISHED"})
            self.assertEqual(browser.page, expected)


if __name__ == "__main__":
    unittest.main()
