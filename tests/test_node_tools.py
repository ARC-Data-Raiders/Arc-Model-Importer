"""Node editor Arc tab: helpers by AST extraction, draw-code and wording rules."""
import ast
import pathlib
import re
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "Shared" / "node_tools.py"
LAYOUT = ROOT / "Shared" / "materials" / "graph_layout.py"


def _module():
    return ast.parse(SRC.read_text(encoding="utf-8"))


def _layout_stub():
    tree = ast.parse(LAYOUT.read_text(encoding="ascii"))
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("_clean_name", "_natural")]
    keep += [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    keep += [n for n in tree.body if isinstance(n, ast.Assign)
             and any(getattr(t, "id", "").startswith(("TAG_", "_HASH", "_PREFIX", "_NAT")) for t in n.targets)]
    ns = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(LAYOUT), "exec"), ns)
    return types.SimpleNamespace(**ns)


def _extract(*names):
    mod = _module()
    fns = [n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {"graph_layout": _layout_stub()}
    exec(compile(ast.Module(body=fns, type_ignores=[]), str(SRC), "exec"), ns)
    return ns


class Sock:
    def __init__(self, name, linked=False):
        self.name = self.identifier = name
        self.is_linked, self.hide, self.default_value = linked, False, 0.5


class Group(dict):
    def __init__(self, name, sub, label="", inputs=()):
        super().__init__()
        self.name, self.label, self.bl_idname = name, label, "ShaderNodeGroup"
        self.node_tree, self.inputs = sub, list(inputs)

    __eq__ = object.__eq__
    __hash__ = object.__hash__


class Sub(dict):
    def __init__(self, name):
        super().__init__()
        self.name = name

    __eq__ = object.__eq__
    __hash__ = object.__hash__


class T:
    def __init__(self, nodes):
        self.nodes = nodes


class Step:
    def __init__(self, name):
        self.node_tree = Sub(name)


class NodeToolsHelperTests(unittest.TestCase):
    def test_breadcrumb_joins_clean_names(self):
        ns = _extract("breadcrumb")
        self.assertEqual(ns["breadcrumb"]([Step("MI_Body"), Step("DR.Look.001"), Step("DR.Zone 1.0a1b2c3d4e")]),
                         "MI Body > Look > Zone 1")
        self.assertEqual(ns["breadcrumb"](None), "")

    def test_zone_nodes_natural_order_and_tags(self):
        ns = _extract("zone_nodes")
        g10, g2 = Group("a", Sub("DR.Zone 10"), "Zone 10"), Group("b", Sub("DR.Zone 2"), "Zone 2")
        tagged = Group("c", Sub("DR.Paint"), "Paint")
        tagged["arc_zone"] = "Zone 3"
        other = Group("d", Sub("DR.Noise"), "Noise")
        names = [label for _n, label in ns["zone_nodes"](T([g10, other, tagged, g2]))]
        self.assertEqual(names, ["Paint", "Zone 2", "Zone 10"])
        self.assertEqual(ns["zone_nodes"](None), [])

    def test_look_node_prefers_widest_look_group(self):
        ns = _extract("look_node")
        small = Group("s", Sub("DR.Look"), inputs=[Sock("A")])
        big = Group("b", Sub("Arc Clothing Look"), inputs=[Sock("A"), Sock("B")])
        noise = Group("n", Sub("DR.Noise"), inputs=[Sock("A")] * 5)
        self.assertIs(ns["look_node"](T([small, noise, big])), big)
        self.assertIsNone(ns["look_node"](T([noise])))


class NodeToolsRulesTests(unittest.TestCase):
    def test_panel_draw_has_no_io_or_data_writes(self):
        mod = _module()
        panel = next(n for n in mod.body if isinstance(n, ast.ClassDef) and n.name == "ARC_PT_node_tools")
        draw = next(n for n in panel.body if isinstance(n, ast.FunctionDef) and n.name == "draw")
        text = ast.unparse(draw)
        for bad in ("bpy.data", "open(", "bpy.ops", "os.", "scandir", "listdir", "graph_layout."):
            self.assertNotIn(bad, text)
        for node in ast.walk(draw):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    self.assertNotIsInstance(t, (ast.Attribute, ast.Subscript), ast.unparse(t))

    def test_panel_sits_in_arc_tab(self):
        text = SRC.read_text(encoding="utf-8")
        self.assertIn('CATEGORY = "Arc"', text)
        self.assertIn('bl_space_type = "NODE_EDITOR"', text)

    def test_labels_title_case_and_hints_plain(self):
        mod = _module()
        small = {"a", "an", "the", "by", "of", "to", "in", "and", "or"}
        jargon = re.compile(r"\b(usmap|niagara|pak|MI)\b", re.I)
        for cls in (n for n in mod.body if isinstance(n, ast.ClassDef)):
            attrs = {t.id: v.value for a in cls.body if isinstance(a, ast.Assign)
                     for t in a.targets if isinstance(t, ast.Name)
                     for v in [a.value] if isinstance(v, ast.Constant) and isinstance(v.value, str)}
            label = attrs.get("bl_label", "")
            for i, w in enumerate(label.replace("...", "").split()):
                if i == 0 or w.lower() not in small:
                    self.assertTrue(w[0].isupper(), (cls.name, label))
            hint = attrs.get("bl_description", "")
            if hint:
                self.assertFalse(hint.endswith("."), (cls.name, hint))
                self.assertTrue(hint[0].isupper(), (cls.name, hint))
            for s in (label, hint):
                self.assertIsNone(jargon.search(s), (cls.name, s))


if __name__ == "__main__":
    unittest.main()
