import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import sys


ROOT = Path(__file__).resolve().parents[1]


class Layout:
    def __init__(self, events=None, path=()):
        self.events = [] if events is None else events
        self.path = path

    def child(self, kind):
        marker = (kind, len(self.events))
        self.events.append(("container", marker, self.path))
        return Layout(self.events, self.path + (marker,))

    def row(self, **kwargs):
        return self.child("row")

    def box(self):
        return self.child("box")

    def column_flow(self, **kwargs):
        self.events.append(("columns", kwargs["columns"], self.path))
        return self.child("flow")

    def separator(self, **kwargs):
        self.events.append(("separator", None, self.path))

    def prop(self, owner, name, **kwargs):
        self.events.append(("prop", name, self.path))

    def operator(self, name, **kwargs):
        self.events.append(("operator", name, self.path))
        return SimpleNamespace()

    def menu(self, name, **kwargs):
        self.events.append(("menu", name, self.path))

    def label(self, **kwargs):
        pass


def _sections(package):
    # sidebar imports through section folders (.Outfit_Accessories, .Shared, .Animations); one fake serves all.
    modules = {"feedback_addon": package}
    for section in ("Outfit_Accessories", "Shared", "Animations"):
        modules["feedback_addon." + section] = package
    return modules


def function(filename, name, extra=None):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
    if "." in name:
        class_name, name = name.split(".")
        tree = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = {"__package__": "feedback_addon", "_collapsible": lambda *args, **kwargs: None}
    namespace.update(extra or {})
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, "exec"), namespace)
    return namespace[name]


class FeedbackLayoutTests(unittest.TestCase):
    def test_hair_color_controls_use_separate_properties(self):
        scene = SimpleNamespace(arc_appearance_hair="hair", arc_appearance_facial_hair="beard", arc_appearance_hair_color="black", arc_appearance_facial_hair_color="brown")
        for facial, expected, other in ((False, "arc_appearance_hair_color", "arc_appearance_facial_hair_color"), (True, "arc_appearance_facial_hair_color", "arc_appearance_hair_color")):
            layout = Layout()
            extras = {"_add_or_all": function("sidebar.py", "_add_or_all"), "_appearance_preview_icon_id": lambda choice: 0, "_split_controls_preview": lambda *args, **kwargs: (layout, None), "_draw_preview_icon": lambda *args: None}
            function("sidebar.py", "_draw_hair_section", extras)(layout, SimpleNamespace(scene=scene), facial=facial)
            names = [event[1] for event in layout.events if event[0] == "prop"]
            self.assertIn(expected, names)
            self.assertNotIn(other, names)

    def test_outfit_results_and_two_column_browse(self):
        layout = Layout()
        entries = [(str(index), str(index), "") for index in range(9)]
        package = SimpleNamespace(properties=SimpleNamespace(make_outfit_selector_items=lambda *args: entries))
        context = SimpleNamespace(scene=SimpleNamespace(arc_outfit_search="", arc_selected_outfit="0"))
        extras = {"_split_controls_preview": lambda *args, **kwargs: (layout, None), "_draw_preview_icon": lambda *args: None}
        with patch.dict(sys.modules, _sections(package)):
            function("sidebar.py", "_draw_outfit_selector_preview", extras)(layout, context, 0)
            selections = [event for event in layout.events if event[1] == "arc_outfits.select_outfit"]
            self.assertEqual(len(selections), 3)
            action = next(event for event in layout.events if event[1] == "arc_outfits.load_outfit")
            self.assertNotEqual(selections[0][2], action[2])
            self.assertTrue(any(event[0] == "separator" for event in layout.events))
            menu = Layout()
            function("sidebar.py", "ARC_OUTFITS_MT_outfit_browse.draw")(SimpleNamespace(layout=menu), context)
            self.assertIn(("columns", 2, ()), menu.events)
            self.assertEqual(len([event for event in menu.events if event[1] == "arc_outfits.select_outfit"]), len(entries))

    def test_import_source_visible_without_advanced(self):
        for source in ('PAK', 'FMODEL'):
            layout = Layout()
            def picker(mode):
                return lambda child, context: child.events.append(('picker', mode, child.path))
            extras = {'_draw_pak_folder_picker': picker('PAK'), '_draw_fmodel_folder_picker': picker('FMODEL')}
            function("sidebar.py", "_draw_import_section", extras)(layout, SimpleNamespace(scene=SimpleNamespace(arc_import_source=source)))
            events = [event for event in layout.events if event[0] in ('prop', 'picker', 'operator')]
            self.assertEqual(events[0][1], 'arc_import_source')
            self.assertEqual(events[1][:2], ('picker', source))
            self.assertEqual(len([event for event in events if event[0] == 'picker']), 1)

    def test_arc_controls_visible_without_advanced(self):
        layout = Layout()
        layout.panel = lambda *args, **kwargs: (layout, None)
        for use_all, expected in ((False, "arc_outfits.import_codex_enemy"), (True, "arc_outfits.add_all_arc")):
            layout = Layout()
            layout.panel = lambda *args, **kwargs: (layout, None)
            wm = SimpleNamespace(arc_add_all_arc=use_all)
            context = SimpleNamespace(scene=SimpleNamespace(arc_enemy_key="NONE"), active_object=None, window_manager=wm)
            extras = {"arc_catalog_data": SimpleNamespace(by_key=lambda key: None), "_add_or_all": function("sidebar.py", "_add_or_all")}
            function("sidebar.py", "_draw_arc_content", extras)(layout, context)
            names = [event[1] for event in layout.events]
            self.assertIn(expected, names)
            self.assertIn("arc_add_all_arc", names)
            self.assertIn("arc_scan_state", names)

    def test_animation_results_limited_and_actions_separate(self):
        layout = Layout()
        limits = []
        def entries(*args, **kwargs):
            limits.append(kwargs["limit"])
            return [{"key": str(index)} for index in range(kwargs["limit"])]
        package = SimpleNamespace(animation_catalog=SimpleNamespace(filter_entries=entries, display_label=lambda entry: entry["key"]), animation_import=SimpleNamespace(resolve_target_armature=lambda context: None))
        scene = SimpleNamespace(arc_anim_search="", arc_anim_kind_filter="ALL")
        with patch.dict(sys.modules, _sections(package)):
            function("sidebar.py", "_draw_animations_content")(layout, SimpleNamespace(scene=scene))
        self.assertEqual(limits, [3])
        select = next(event for event in layout.events if event[1] == "arc_outfits.select_anim")
        apply = next(event for event in layout.events if event[1] == "arc_outfits.apply_anim")
        self.assertNotEqual(select[2], apply[2])
        self.assertTrue(any(event[0] == "separator" for event in layout.events))

    def test_fx_results_limited_and_actions_separate(self):
        layout = Layout()
        limits = []
        def entries(*args, **kwargs):
            limits.append(kwargs["limit"])
            return [{"key": str(index)} for index in range(kwargs["limit"])]
        extras = {"filter_entries": entries, "display_label": lambda entry: entry["key"],
                  "draw_playback_box": lambda *args, **kwargs: None}
        function("sidebar.py", "draw_niagara_fx_box", extras)(layout, SimpleNamespace(scene=SimpleNamespace()))
        self.assertEqual(limits, [3])
        select = next(event for event in layout.events if event[1] == "arc_outfits.select_ns")
        apply = next(event for event in layout.events if event[1] == "arc_outfits.import_niagara_fx")
        self.assertNotEqual(select[2], apply[2])


if __name__ == "__main__":
    unittest.main()
