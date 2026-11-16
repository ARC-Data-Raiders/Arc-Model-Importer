"""t4 council UI fixes: each test pins one behaviour the t3 review flagged."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from test_ui_feedback_layout import Layout, function, ROOT


def _labels(events):
    return [value for kind, value, _path in events if kind == "label"]


def _source(filename):
    return (ROOT / filename).read_text(encoding="utf-8-sig")


def _lists_namespace(names, extra=None):
    nodes = [n for n in ast.parse(_source("Shared/ui_lists.py")).body
             if (isinstance(n, ast.FunctionDef) and n.name in names)
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in names for t in n.targets))]
    namespace = dict(extra or {})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "ui_lists.py", "exec"), namespace)
    return namespace


class ApplyRowTests(unittest.TestCase):
    def test_disabled_apply_button_has_reason_line(self):
        draw = function("sidebar.py", "_draw_apply_row", {"_edit_target": lambda context, part: ""})
        layout = Layout()
        draw(layout, SimpleNamespace(), "BACKPACK", op_id="x.apply", text="Apply Backpack Colorway")
        self.assertIn("Import or select a character to change its backpack", _labels(layout.events))

    def test_disabled_hair_apply_reason_names_the_part(self):
        draw = function("sidebar.py", "_draw_apply_row", {"_edit_target": lambda context, part: ""})
        layout = Layout()
        draw(layout, SimpleNamespace(), "HAIR", op_id="x.apply", text="Apply Hair Colorway")
        self.assertIn("Import or select a character to change its hair", _labels(layout.events))


class LastErrorTests(unittest.TestCase):
    def _api(self, report):
        err = {"title": "Import failed", "detail": "Mesh missing for Backpack"}
        return SimpleNamespace(last_error=lambda: err, job_report=lambda: report)

    def _draw(self, api, shown):
        error_in_report = function("sidebar.py", "_error_in_report")
        draw = function("sidebar.py", "_draw_last_error",
                        {"_job_api": lambda: api, "_error_in_report": error_in_report})
        layout = Layout()
        draw(layout, shown)
        return layout.events

    def test_single_failure_already_in_report_draws_no_second_box(self):
        api = self._api({"failed": 1, "total": 1, "lines": ["Mesh missing for Backpack"]})
        self.assertEqual(self._draw(api, "report"), [])

    def test_error_box_has_copy_error_button(self):
        events = self._draw(self._api(None), "")
        self.assertIn(("operator", "arc_outfits.copy_error"), [(k, v) for k, v, _ in events])


class BatchHintTests(unittest.TestCase):
    def setUp(self):
        self.ns = _lists_namespace({"batch_hint", "BATCH_KINDS", "_BATCH"})

    def test_planned_import_counts(self):
        hint = self.ns["batch_hint"]
        self.assertTrue(hint("HEADS", 0).startswith("Nothing to import"))
        self.assertTrue(hint("HEADS", 1).startswith("1 planned import."))
        self.assertTrue(hint("HEADS", 12).startswith("12 planned imports."))
        self.assertNotIn("About", hint("OUTFITS", 40))


class OutfitVisibleTests(unittest.TestCase):
    def setUp(self):
        self.ns = _lists_namespace({"outfit_is_visible"}, {
            "filter_mode": lambda context, kind: "ALL", "filter_keys": lambda kind, mode: None})

    def test_query_rows_decide_visibility_before_sync(self):
        visible = self.ns["outfit_is_visible"]
        wm = SimpleNamespace(arc_outfit_rows=[SimpleNamespace(key="old")])
        context = SimpleNamespace(window_manager=wm)
        self.assertTrue(visible(context, "new", rows=[("new", "New")]))
        self.assertFalse(visible(context, "old", rows=[("new", "New")]))


class SceneRowKindTests(unittest.TestCase):
    def test_kind_icons_match_section_icons(self):
        ns = _lists_namespace({"_KIND_ICON"})
        self.assertEqual(ns["_KIND_ICON"]["outfit"], "USER")
        self.assertEqual(ns["_KIND_ICON"]["weapon"], "TOOL_SETTINGS")

    def test_kind_text_column_removed(self):
        self.assertNotIn("_KIND_TEXT", _source("Shared/ui_lists.py"))


class PlaybackAndFilesTests(unittest.TestCase):
    def test_bake_curves_drawn_without_playback_props(self):
        draw = function("sidebar.py", "draw_playback_box")
        layout = Layout()
        draw(layout, SimpleNamespace(scene=SimpleNamespace(arc_ns_bake_curves=True)))
        self.assertIn(("prop", "arc_ns_bake_curves"), [(k, v) for k, v, _ in layout.events])

    def test_custom_effect_files_has_no_duplicate_hint(self):
        draw = function("sidebar.py", "_draw_custom_effect_files")
        layout = Layout()
        draw(layout, SimpleNamespace(arc_ns_catalog_root="", arc_ns_asset_root=""))
        self.assertNotIn("Empty folders use the effects bundled with the add-on", _labels(layout.events))

    def test_map_placement_has_no_inner_title_box(self):
        draw = function("sidebar.py", "draw_map_placement_panel")
        layout = Layout()
        draw(layout, SimpleNamespace(scene=SimpleNamespace(arc_placement_csv="")))
        self.assertNotIn("Map Placement", _labels(layout.events))
        self.assertFalse(any(k == "container" and v[0] == "box" for k, v, _ in layout.events))


class StringTests(unittest.TestCase):
    def test_renamed_buttons(self):
        text = _source("sidebar.py")
        self.assertIn('"Delete Last Import..."', text)
        self.assertIn("'Add Attachment'", text)
        self.assertIn('"Counting imports..."', text)
        self.assertNotIn("def _checkbox_option", text)


if __name__ == "__main__":
    unittest.main()
