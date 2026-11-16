import ast
import csv
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load_function(relative, name, scope):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8-sig"))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), relative, "exec"), scope)
    return scope[name]


class BrowserUserRevisionTests(unittest.TestCase):
    def test_source_and_import_buttons_share_equal_columns(self):
        source = (ROOT / "sidebar.py").read_text()
        draw = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "_draw_import_section")
        body = ast.get_source_segment(source, draw)
        self.assertIn("source = box.split(factor=0.5, align=True)", body)
        self.assertIn('source.prop_enum(scene, "arc_import_source", "PAK")', body)
        self.assertIn('source.prop_enum(scene, "arc_import_source", "FILES")', body)
        self.assertIn("actions = box.split(factor=0.5, align=True)", body)
        self.assertIn('box.operator("arc_outfits.import_again"', body)

    def test_source_overrides_and_scene_items_are_nested(self):
        events = []
        draw_source = load_function("sidebar.py", "_draw_source_settings_content", {
            "_draw_import_section": lambda *args: events.append("import controls"),
            "_collapsible": lambda *args, **kwargs: events.append(args[2]) or None,
        })
        draw_source(object(), SimpleNamespace(scene=object()))
        self.assertEqual(events, ["import controls", "Advanced File Overrides"])
        events.clear()
        draw_scene = load_function("sidebar.py", "_draw_scene_lighting_content", {
            "_draw_box_section": lambda *args: events.append(args[3]),
            "_draw_map_placement_section": lambda *args: events.append("map placement"),
            "_draw_lighting_look_content": lambda *args: events.append("lighting"),
            "_draw_scene_items_content": object(),
        })
        draw_scene(object(), object())
        self.assertEqual(events, ["In this Scene", "map placement", "lighting"])

    def test_grid_page_size_and_image_scale(self):
        tree = ast.parse((ROOT / "Outfit_Accessories/icon_browser.py").read_text())
        browser = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ARC_OUTFITS_OT_IconBrowser")
        draw = next(node for node in browser.body if isinstance(node, ast.FunctionDef) and node.name == "draw")
        previews, buttons = [], []

        class Layout:
            def row(self, **kwargs):
                return self

            def column(self, **kwargs):
                return self

            def grid_flow(self, **kwargs):
                return self

            def prop(self, *args, **kwargs):
                pass

            def operator(self, name, **kwargs):
                buttons.append((name, kwargs))
                return SimpleNamespace()

            def template_popup_confirm(self, *args, **kwargs):
                return SimpleNamespace()

            def template_icon(self, **kwargs):
                previews.append(kwargs)

        scope = {"selected_items": lambda *args: set(), "collect_items": lambda *args: [(str(index), str(index)) for index in range(80)],
                 "current_selection": lambda *args: "0", "icon_id": lambda *args: 42, "_job_busy": lambda: False, "_BROWSER_IMPORT_ACTIVE": None,
                 "_placeholder_icon_id": lambda: 1, "BROWSER_COLUMNS": 5, "BROWSER_PAGE_SIZE": 15, "BROWSER_WIDTH": 1100}
        exec(compile(ast.Module(body=[draw], type_ignores=[]), "browser draw", "exec"), scope)
        context = SimpleNamespace(region=None, window_manager=SimpleNamespace(arc_browser_multiple=False))
        for columns in (3, 4, 5):
            previews.clear()
            scope["draw"](SimpleNamespace(layout=Layout(), kind="enemy", filter="", columns=str(columns), width=720, height=0, page=1), context)
            self.assertEqual(len(previews), 15)
            self.assertEqual(previews[0]["icon_value"], 42)
            self.assertAlmostEqual(previews[0]["scale"], (1100 - 40) / 5 / 20)

    def test_sandveil_aliases_are_not_interchangeable(self):
        with (ROOT / "Data/outfit_reference.csv").open(encoding="utf-8-sig") as stream:
            rows = {row["ST"]: row for row in csv.DictReader(stream)}
        available = rows["ID_PLAYERSKIN_DASHWOOD"]
        missing = rows["ID_PLAYERSKIN_SANDVEIL"]
        self.assertEqual(available["Flavour"], missing["Flavour"])
        self.assertEqual(available["Pak Status"], "available")
        self.assertTrue(available["Pak Mesh Paths"])
        self.assertEqual(missing["Pak Status"], "no cooked mesh")
        self.assertFalse(missing["Pak Mesh Paths"])
        self.assertFalse(rows["ID_PLAYERSKIN_VULPINE"]["Pak Mesh Paths"])

    def test_browser_picks_do_not_snapshot_the_scene(self):
        tree = ast.parse((ROOT / "Outfit_Accessories/icon_browser.py").read_text())
        picker = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ARC_OUTFITS_OT_IconBrowserPick")
        options = next(node.value for node in picker.body if isinstance(node, ast.Assign) and any(getattr(target, "id", "") == "bl_options" for target in node.targets))
        self.assertNotIn("UNDO", ast.literal_eval(options))

    def test_selection_never_extracts_colourways(self):
        calls = []
        callback = load_function("Outfit_Accessories/properties.py", "_on_selected_outfit_changed", {
            "_OUTFIT_SELECTION_UPDATING": False,
            "ensure_outfit_selection": lambda *args: None,
            "_scan_selected_colorways": lambda *args: calls.append("blocking scan"),
            "_request_selected_colorways": lambda *args: None,
        })
        callback(SimpleNamespace(), SimpleNamespace())
        self.assertEqual(calls, [])

    def test_wide_sidebar_splits_preview(self):
        events = []

        class Layout:
            def column(self, **kwargs):
                return self

            def split(self, **kwargs):
                events.append(kwargs)
                return self

        split = load_function("sidebar.py", "_split_controls_preview", {"_sidebar_width": lambda context: 700})
        controls, preview = split(Layout(), None, 42)
        self.assertTrue(events)
        self.assertIsNotNone(preview)

    def test_browser_has_no_layout_or_width_settings(self):
        tree = ast.parse((ROOT / "Outfit_Accessories/icon_browser.py").read_text())
        browser = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ARC_OUTFITS_OT_IconBrowser")
        fields = {node.target.id: node.annotation for node in browser.body if isinstance(node, ast.AnnAssign)}
        self.assertNotIn("columns", fields)
        self.assertNotIn("width", fields)

    def test_saved_layout_ignores_stale_resize_dimensions(self):
        tree = ast.parse((ROOT / "Outfit_Accessories/icon_browser.py").read_text())
        browser = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ARC_OUTFITS_OT_IconBrowser")
        invoke = next(node for node in browser.body if isinstance(node, ast.FunctionDef) and node.name == "invoke")
        scope = {"on_multiple_changed": lambda context: None, "BROWSER_WIDTH": 1100,
                 "_clamp_page": lambda operator, context: None,
                 "_warm_browser_thumbnails": lambda operator, context: None}
        exec(compile(ast.Module(body=[invoke], type_ignores=[]), "browser invoke", "exec"), scope)
        calls = []
        wm = SimpleNamespace(arc_browser_columns="5", arc_browser_width=960, arc_browser_height=820,
                             invoke_props_dialog=lambda operator, **kwargs: calls.append(kwargs) or {"RUNNING_MODAL"})
        operator = SimpleNamespace(kind="outfit", filter="", page=1, columns="3", width=720,
                                   properties=SimpleNamespace(is_property_set=lambda name: False))
        self.assertEqual(scope["invoke"](operator, SimpleNamespace(window_manager=wm), None), {"RUNNING_MODAL"})
        self.assertEqual(operator.columns, "3")
        self.assertEqual(calls[0]["width"], 1100)


if __name__ == "__main__":
    unittest.main()
