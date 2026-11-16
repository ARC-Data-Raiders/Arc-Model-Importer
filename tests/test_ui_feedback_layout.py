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

    def column(self, **kwargs):
        return self.child("column")

    def split(self, **kwargs):
        return self.child("split")

    def column_flow(self, **kwargs):
        self.events.append(("columns", kwargs["columns"], self.path))
        return self.child("flow")

    def separator(self, **kwargs):
        self.events.append(("separator", None, self.path))

    def prop(self, owner, name, **kwargs):
        self.events.append(("prop", name, self.path))

    def prop_enum(self, owner, name, value, **kwargs):
        self.events.append(("prop", name, self.path))

    def operator(self, name, **kwargs):
        self.events.append(("operator", name, self.path))
        return SimpleNamespace()

    def menu(self, name, **kwargs):
        self.events.append(("menu", name, self.path))

    def template_list(self, list_type, list_id, *args, **kwargs):
        self.events.append(("template_list", args[1], self.path))

    def panel(self, panel_id, **kwargs):
        self.events.append(("panel", panel_id, self.path))
        return self.child("header"), self.child("body")

    def label(self, **kwargs):
        self.events.append(("label", kwargs.get("text", ""), self.path))


def _sections(package):
    # sidebar imports through section folders (.Outfit_Accessories, .Shared, .Animations); one fake serves all.
    modules = {"feedback_addon": package}
    for section in ("Outfit_Accessories", "Shared", "Animations", "Maps", "Maps.map_tools", "Arc"):
        modules["feedback_addon." + section] = package
    return modules


def _module_nodes(filename):
    return ast.parse((ROOT / filename).read_text(encoding="utf-8-sig")).body


def function(filename, name, extra=None):
    tree = module = SimpleNamespace(body=_module_nodes(filename))
    if "." in name:
        class_name, name = name.split(".")
        tree = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = {"__package__": "feedback_addon", "_collapsible": lambda *args, **kwargs: None}
    # Pure text helpers the draw functions share.
    for helper in ("_pretty_name", "_pretty_stem"):
        if helper != name:
            found = next((n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == helper), None)
            if found is not None:
                exec(compile(ast.Module(body=[found], type_ignores=[]), filename, "exec"), namespace)
    namespace.update(extra or {})
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, "exec"), namespace)
    return namespace[name]


def assigned(filename, name):
    node = next(n for n in _module_nodes(filename) if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == name for t in n.targets))
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, "exec"), namespace)
    return namespace[name]


def fake_lists():
    """The pure row helpers of Shared/ui_lists.py; request_sync builds at once instead of in a timer."""
    namespace = {}
    pure = [n for n in _module_nodes("Shared/ui_lists.py")
            if (isinstance(n, ast.FunctionDef) and n.name in ("outfit_rows", "_folder", "named_rows", "count_text"))
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "_ANIM_LATE" for t in n.targets))]
    exec(compile(ast.Module(body=pure, type_ignores=[]), "ui_lists.py", "exec"), namespace)
    stored, builds = {}, []

    def request_sync(kind, signature, build, selected=""):
        builds.append(kind)
        stored[kind] = build()

    namespace.update(request_sync=request_sync, rows_of=lambda context, kind: stored.get(kind, ()),
                     stored=stored, builds=builds)
    return SimpleNamespace(**namespace)


def context_of(scene, **kwargs):
    return SimpleNamespace(scene=scene, window_manager=SimpleNamespace(), **kwargs)


class FeedbackLayoutTests(unittest.TestCase):
    def test_hair_color_controls_use_separate_properties(self):
        scene = SimpleNamespace(arc_appearance_hair="hair", arc_appearance_facial_hair="beard", arc_appearance_hair_color="black", arc_appearance_facial_hair_color="brown")
        for facial, expected, other in ((False, "arc_appearance_hair_color", "arc_appearance_facial_hair_color"), (True, "arc_appearance_facial_hair_color", "arc_appearance_hair_color")):
            layout = Layout()
            extras = {"_add_part_button": function("sidebar.py", "_add_part_button", {"_PROMPT_GEAR_PARTS": ("BACKPACK", "ATTACHMENT", "CHARM", "RAIDER_TOOL")}),
                      "_draw_apply_row": function("sidebar.py", "_draw_apply_row", {"_edit_target": lambda context, part: ""}),
                      "_appearance_preview_icon_id": lambda choice: 0, "_split_controls_preview": lambda *args, **kwargs: (layout, None), "_draw_preview_icon": lambda *args: None,
                      "_prop_with_browse": lambda parent, scene, prop, text, kind: parent.prop(scene, prop, text=text)}
            function("sidebar.py", "_draw_hair_section", extras)(layout, SimpleNamespace(scene=scene), facial=facial)
            names = [event[1] for event in layout.events if event[0] == "prop"]
            self.assertIn(expected, names)
            self.assertNotIn(other, names)
            self.assertIn("arc_outfits.add_appearance_part", [event[1] for event in layout.events])

    def test_outfit_list_and_import_button(self):
        layout = Layout()
        entries = [(str(index), str(index), "") for index in range(9)]
        package = SimpleNamespace(properties=SimpleNamespace(make_outfit_selector_items=lambda *args: entries))
        context = context_of(SimpleNamespace(arc_outfit_search="", arc_selected_outfit="0"))
        lists = fake_lists()
        extras = {"_split_controls_preview": lambda *args, **kwargs: (layout, None), "_draw_preview_icon": lambda *args: None,
                  "_ui_lists": lambda: lists}
        with patch.dict(sys.modules, _sections(package)):
            function("sidebar.py", "_draw_outfit_selector_preview", extras)(layout, context, 0)
        # One scrolling list replaces the per-row buttons and the Browse menu.
        self.assertIn("arc_outfit_rows", [event[1] for event in layout.events if event[0] == "template_list"])
        self.assertEqual(len(lists.stored["outfit"]), 9)
        self.assertNotIn("arc_outfits.select_outfit", [event[1] for event in layout.events])
        self.assertIn(("operator", "arc_outfits.load_outfit"), [event[:2] for event in layout.events])
        self.assertIn(("menu", "ARC_OUTFITS_MT_outfit_import_more"), [event[:2] for event in layout.events])
        self.assertNotIn(("label", "9 outfits"), [event[:2] for event in layout.events])

    def test_import_source_visible_without_advanced(self):
        for source in ('PAK', 'FMODEL'):
            layout = Layout()
            def picker(mode):
                return lambda child, context: child.events.append(('picker', mode, child.path))
            extras = {'_draw_pak_folder_picker': picker('PAK'), '_draw_fmodel_folder_picker': picker('FMODEL')}
            function("sidebar.py", "_draw_import_section", extras)(layout, SimpleNamespace(scene=SimpleNamespace(arc_import_source=source)))
            events = [event for event in layout.events if event[0] in ('prop', 'picker', 'operator')]
            self.assertEqual(events[0][1], 'arc_import_source')
            self.assertEqual(events[2][:2], ('picker', source))
            self.assertEqual(len([event for event in events if event[0] == 'picker']), 1)

    def test_arc_controls_visible_without_advanced(self):
        layout = Layout()
        context = SimpleNamespace(scene=SimpleNamespace(arc_enemy_key="NONE", arc_scan_state="IDLE"), active_object=None)
        extras = {"arc_catalog_data": SimpleNamespace(by_key=lambda key: None),
                  "_role_target_name": lambda *args: "", "_pick_label": lambda module, key, fallback: fallback}
        function("sidebar.py", "_draw_arc_content", extras)(layout, context)
        self.assertNotIn(("label", "Import or select an enemy to change it"), [event[:2] for event in layout.events])
        names = [event[1] for event in layout.events]
        # One Import Enemy button; Import All Enemies lives in Settings > Batch Import.
        self.assertIn("arc_outfits.import_codex_enemy", names)
        self.assertNotIn("arc_outfits.add_all_arc", names)
        self.assertNotIn("arc_add_all_arc", names)
        self.assertIn("arc_scan_state", names)

    def test_animation_list_and_apply_button(self):
        layout = Layout()
        limits = []
        def entries(*args, **kwargs):
            limits.append(kwargs["limit"])
            return [{"key": str(index)} for index in range(12)]
        package = SimpleNamespace(animation_catalog=SimpleNamespace(filter_entries=entries, display_label=lambda entry: entry["key"], _cache={"entries": list(range(12))}),
                                  animation_import=SimpleNamespace(resolve_target_armature=lambda context: None))
        scene = SimpleNamespace(arc_anim_search="", arc_anim_kind_filter="ALL")
        lists = fake_lists()
        with patch.dict(sys.modules, _sections(package)):
            extras = {"_anim_target": function("sidebar.py", "_anim_target"), "_ui_lists": lambda: lists}
            function("sidebar.py", "_draw_animations_content", extras)(layout, context_of(scene))
        # The list holds every match; the scan runs in the list timer (limit 0 = no cap).
        self.assertEqual(limits, [0])
        self.assertEqual(len(lists.stored["anim"]), 12)
        self.assertIn("arc_anim_rows", [event[1] for event in layout.events if event[0] == "template_list"])
        self.assertNotIn("arc_outfits.select_anim", [event[1] for event in layout.events])
        self.assertIn("arc_outfits.apply_anim", [event[1] for event in layout.events])

    def test_fx_list_and_import_button(self):
        layout = Layout()
        limits = []
        def entries(*args, **kwargs):
            limits.append(kwargs["limit"])
            return [{"key": str(index)} for index in range(10)]
        lists = fake_lists()
        extras = {"filter_entries": entries, "display_label": lambda entry: entry["key"],
                  "draw_playback_box": lambda *args, **kwargs: None, "_ui_lists": lambda: lists}
        function("sidebar.py", "draw_niagara_fx_box", extras)(layout, context_of(SimpleNamespace()))
        self.assertEqual(limits, [0])
        self.assertEqual(len(lists.stored["fx"]), 10)
        self.assertIn("arc_fx_rows", [event[1] for event in layout.events if event[0] == "template_list"])
        self.assertNotIn("arc_outfits.select_ns", [event[1] for event in layout.events])
        self.assertIn("arc_outfits.import_niagara_fx", [event[1] for event in layout.events])

    def test_settings_hold_batch_imports_and_version(self):
        layout = Layout()
        scene = SimpleNamespace(arc_import_source="PAK", arc_pak_dir="", arc_usmap_path="", arc_outfit_csv_path="",
                                arc_include_reference=False)
        package = SimpleNamespace(fmodel_bridge=SimpleNamespace(), texture_sets=SimpleNamespace(draw_settings=lambda *args: None))
        drawn = []
        extras = {"_collapsible": lambda parent, panel_id, title, **kwargs: drawn.append(title) or parent.child(panel_id),
                  "_show_developer": lambda: False, "_draw_offset_settings": lambda *args: None,
                  "_path_problem": lambda *args: "", "_draw_custom_effect_files": lambda *args: None,
                  "_draw_fmodel_folder_picker": lambda *args: None, "_draw_fmodel_bridge": lambda *args: None,
                  "_draw_developer_tools": lambda *args: drawn.append("Developer Tools"),
                  "_addon_version": lambda: "9.8.7"}
        extras["_draw_batch_import"] = function("sidebar.py", "_draw_batch_import", {"_ui_lists": lambda: None})
        with patch.dict(sys.modules, _sections(package)):
            function("sidebar.py", "_draw_settings_content", extras)(layout, context_of(scene))
        # Game source hides Integration; file overrides nest inside Source.
        self.assertEqual(drawn, ["Placement", "Textures", "Batch Import", "About"])
        names = [event[1] for event in layout.events]
        self.assertNotIn("arc_outfits.pick_pak_dir", names)
        for op_id in ("arc_outfits.load_all_outfits_colorways", "arc.textures_pack_blend", "preferences.addon_show"):
            self.assertIn(op_id, names)
        self.assertNotIn("Version 9.8.7", names)


if __name__ == "__main__":
    unittest.main()
