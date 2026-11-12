import importlib.util
import os
import sys
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    bpy = types.ModuleType("bpy")
    bpy.types = types.SimpleNamespace(Operator=type("Operator", (), {}), PropertyGroup=type("PropertyGroup", (), {}),
                                      Material=type("Material", (), {}), NodeTree=type("NodeTree", (), {}), UIList=type("UIList", (), {}))
    bpy.app = types.SimpleNamespace(handlers=types.SimpleNamespace(persistent=lambda f: f))
    props = types.ModuleType("bpy.props")
    for n in ("BoolProperty", "EnumProperty", "FloatProperty", "FloatVectorProperty", "IntProperty", "StringProperty"):
        setattr(props, n, lambda *a, **k: None)
    bpy.props = props
    extras = types.ModuleType("bpy_extras")
    io = types.ModuleType("bpy_extras.io_utils")
    io.ExportHelper = type("ExportHelper", (), {})
    io.ImportHelper = type("ImportHelper", (), {})
    pkg = types.ModuleType("_scopkg")
    pkg.__path__ = [_ROOT]
    mats = types.ModuleType("_scopkg.Shared.materials")
    mats.param_labels = types.SimpleNamespace(label_for=lambda n: (n, "Other"))
    uspec = importlib.util.spec_from_file_location("_scopkg.Shared.materials.user_controls",
                                                   os.path.join(_ROOT, "Shared/materials/user_controls.py"))
    mats.user_controls = importlib.util.module_from_spec(uspec)
    uspec.loader.exec_module(mats.user_controls)
    spec = importlib.util.spec_from_file_location("_scopkg.Shared.shader_tweaks", os.path.join(_ROOT, "Shared/shader_tweaks.py"))
    st = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(st)
    fakes = {"bpy": bpy, "bpy.props": props, "bpy_extras": extras, "bpy_extras.io_utils": io,
             "_scopkg": pkg, "_scopkg.Shared.materials": mats, "_scopkg.Shared.shader_tweaks": st}
    saved = {k: sys.modules.get(k) for k in fakes}
    sys.modules.update(fakes)
    try:
        spec = importlib.util.spec_from_file_location("_scopkg.Shared.shader_control_ops",
                                                      os.path.join(_ROOT, "Shared/shader_control_ops.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        # Restore so fake bpy never leaks into other test modules (e.g. test_shader_pick).
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod, st


sco, st = _load()


class T(unittest.TestCase):
    def test_edited_names(self):
        r = {}
        st.record(r, "part:A", "DirtAmount", 0.8, 0.2)
        st.record(r, "whole", "Eq0", (1, 0, 0, 1), (0, 0, 0, 1))
        self.assertEqual(sco.edited_names(st.get(r), "part:A"), {"DirtAmount"})
        self.assertEqual(sco.edited_names(st.get(r), "part:B"), set())
        st.record(r, "part:A", "DirtAmount", 0.2, 0.2)
        self.assertEqual(sco.edited_names(st.get(r), "part:A"), set())
        self.assertEqual(sco.edited_names(None, "whole"), set())

    def test_apply_order_whole_first(self):
        self.assertEqual(sco.apply_order(["part:B", "whole", "part:A"]), ["whole", "part:A", "part:B"])

    def test_lost_summary(self):
        lines = sco.lost_summary([("part:B", ["DirtAmount", "Eq0"])])
        self.assertEqual(lines, ["B: DirtAmount, Eq0"])
        many = [(f"part:P{i}", ["X"]) for i in range(8)]
        self.assertEqual(sco.lost_summary(many)[-1], "... and 2 more parts")

    def test_roundtrip_json(self):
        r = {}
        st.record(r, "part:A", "DirtAmount", 0.8, 0.2)
        text = st.to_json(r)
        st.reset(r)
        self.assertEqual(st.get(r), {})
        st.from_json(r, text)
        self.assertEqual(sco.edited_names(st.get(r), "part:A"), {"DirtAmount"})




class FilterTests(unittest.TestCase):
    def test_filter(self):
        m = sco.item_matches
        self.assertTrue(m("Dirt Amount", "DirtAmount", False, "dirt am"))
        self.assertTrue(m("Emission Strength", "Emissive Power", False, "EMISSIVE"))
        self.assertFalse(m("Dirt Amount", "DirtAmount", False, "wear"))
        self.assertTrue(m("X", "Y", False, ""))
        self.assertFalse(m("X", "Y", False, "", edited_only=True))
        self.assertTrue(m("X", "Y", True, "x", edited_only=True))


class PickTests(unittest.TestCase):
    def test_hit_to_slot(self):
        f = sco.hit_to_slot
        o = object()
        self.assertEqual(f(True, o, 2, [0, 1, 3, 2], 4), (o, 3))
        self.assertEqual(f(True, o, 0, [7], 2), (o, 1))
        self.assertEqual(f(True, o, 0, [0], 0), (o, None))
        self.assertIsNone(f(False, o, 0, [0], 1))
        self.assertIsNone(f(True, o, 5, [0], 1))
        self.assertIsNone(f(True, None, 0, [0], 1))


class ColourwayStoreTests(unittest.TestCase):
    def test_round_trip(self):
        root = {}
        st.save_colourway(root, "Red", {"Dirt": 0.5, "Eq0": (1.0, 0.0, 0.0, 1.0)})
        st.save_colourway(root, "Blue", {"Dirt": 0.1})
        self.assertEqual(sorted(st.colourways(root)), ["Blue", "Red"])
        self.assertEqual(st.load_colourway(root, "Red")["Eq0"], [1.0, 0.0, 0.0, 1.0])
        text = st.colourways_to_json(root)
        self.assertTrue(st.delete_colourway(root, "Red"))
        self.assertFalse(st.delete_colourway(root, "Red"))
        self.assertEqual(st.colourways_from_json(root, text), ["Blue", "Red"])
        self.assertEqual(st.load_colourway(root, "Blue"), {"Dirt": 0.1})
        st.delete_colourway(root, "Red"); st.delete_colourway(root, "Blue")
        self.assertNotIn(st.CW_PROP, root)
        with self.assertRaises(ValueError):
            st.colourways_from_json(root, '{"version": 99}')


class ZonePickTests(unittest.TestCase):
    def test_zone_param_match(self):
        self.assertTrue(sco.zone_param_match("Eq2", 3, "outfit"))
        self.assertFalse(sco.zone_param_match("Eq3", 3, "outfit"))
        self.assertTrue(sco.zone_param_match("Anything", 0, "outfit"))
        self.assertTrue(sco.zone_param_match("Base - Color - Green", 2, "weapon"))
        self.assertFalse(sco.zone_param_match("Base Color Red", 2, "weapon"))
        self.assertFalse(sco.zone_param_match("Base Color Red", 7, "weapon"))

    def test_visible_groups_zone_filter(self):
        Item = types.SimpleNamespace
        items = [Item(name=f"Eq{i}", label=f"Tint {i+1}", group="Palette", edited=False) for i in range(3)]
        items.append(Item(name="Wear", label="Wear", group="Wear", edited=False))
        scene = types.SimpleNamespace(arc_shader_mode="PRO", arc_shader_search="", arc_shader_edited_only=False,
                                      arc_shader_items=items, arc_shader_pick_zone=2, arc_shader_pick_zone_kind="outfit")
        ctx = types.SimpleNamespace(scene=scene)
        orig = sco._picked_names
        try:
            sco._picked_names = lambda c: {"Eq0", "Eq1", "Eq2", "Wear"}
            got = {g: [i.name for i in e] for g, e in sco.visible_groups(ctx)}
            self.assertEqual(got, {"Palette": ["Eq1"], "Wear": ["Wear"]})
            scene.arc_shader_pick_zone = 0
            got = {g: [i.name for i in e] for g, e in sco.visible_groups(ctx)}
            self.assertEqual(len(got["Palette"]), 3)
        finally:
            sco._picked_names = orig

    def test_clear_zone_caches(self):
        key = f"{sco.__package__}.shader_pick"
        calls = []
        sys.modules.pop(key, None)
        self.assertFalse(sco.clear_zone_caches())
        sys.modules[key] = types.SimpleNamespace(clear_caches=lambda: calls.append(1))
        try:
            self.assertTrue(sco.clear_zone_caches())
            self.assertEqual(calls, [1])
        finally:
            sys.modules.pop(key, None)


class _Props(dict):
    pass


class _Node(_Props):
    def __init__(self, name, ntype="MATH", label="", tree=None):
        super().__init__(); self.name = name; self.type = ntype; self.label = label
        self.node_tree = tree; self.mute = False


class _Nodes(list):
    def get(self, name, default=None):
        return next((n for n in self if n.name == name), default)


class _Mat(_Props):
    def __init__(self, nodes):
        super().__init__(); self.name = "M"
        self.node_tree = types.SimpleNamespace(nodes=_Nodes(nodes))


class ProToggleTests(unittest.TestCase):
    def test_classify(self):
        def G(name):
            t = _Props(); t.name = name; return t
        self.assertEqual(sco.pro_classify(_Node("x", "GROUP", tree=G(name="GT_DecalLayer_3"))), "decals")
        self.assertEqual(sco.pro_classify(_Node("x", "TEX_IMAGE", label="Decal 1 Data")), "decals")
        self.assertEqual(sco.pro_classify(_Node("x", "TEX_IMAGE", label="Detail Normal Map")), "detail_normal")
        self.assertEqual(sco.pro_classify(_Node("Emission.Sum.0")), "emission")
        self.assertIsNone(sco.pro_classify(_Node("Principled", "BSDF_PRINCIPLED", label="Emission")))
        self.assertIsNone(sco.pro_classify(_Node("DR.Decal", "GROUP", tree=G(name="DR.Outfit"))))
        self.assertIsNone(sco.pro_classify(_Node("Mix")))

    def test_roundtrip_respects_premuted(self):
        a = _Node("a", label="Decal 1"); b = _Node("b", label="Decal 2"); b.mute = True
        c = _Node("c", label="Detail Normal Map")
        m = _Mat([a, b, c])
        self.assertEqual(sco.apply_pro_toggle(m, "decals", False), 1)
        self.assertFalse(sco.pro_state(m, "decals"))
        self.assertEqual((a.mute, b.mute, c.mute), (True, True, False))
        sco.apply_pro_toggle(m, "decals", True)
        self.assertTrue(sco.pro_state(m, "decals"))
        self.assertEqual((a.mute, b.mute, c.mute), (False, True, False))
        self.assertNotIn("arc_pro_muted", a); self.assertNotIn("arc_pro_decals", m)

    def test_reapply_after_rebuild(self):
        m = _Mat([_Node("a", label="Detail Normal Map")])
        sco.apply_pro_toggle(m, "detail_normal", False)
        fresh = _Node("a", label="Detail Normal Map")
        m.node_tree.nodes[:] = [fresh]
        self.assertEqual(sco.reapply_pro_toggles(m), 1)
        self.assertTrue(fresh.mute)


if __name__ == "__main__":
    unittest.main(argv=["test_shader_control_ops"])   # Blender owns sys.argv
