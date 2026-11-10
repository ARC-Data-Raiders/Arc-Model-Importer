import importlib.util
import os
import unittest

_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Shared/shader_upgrade.py")
_spec = importlib.util.spec_from_file_location("shader_upgrade", _path)
su = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(su)


class Sock:
    def __init__(self, name, v):
        self.name = name
        self.default_value = v


class Inputs(list):
    def get(self, name):
        for s in self:
            if s.name == name:
                return s
        return None


class Group(dict):
    def __init__(self, name, inputs, ver=None):
        super().__init__()
        self.name = name
        self.inputs = Inputs(Sock(n, 0.0) for n in inputs)
        if ver is not None:
            self[su.STAMP] = ver


class Node:
    def __init__(self, tree, values):
        self.inputs = Inputs(Sock(n, values.get(n, 0.0)) for n in [s.name for s in tree.inputs])
        self._tree = tree

    @property
    def node_tree(self):
        return self._tree

    @node_tree.setter
    def node_tree(self, tree):
        self._tree = tree
        old = {s.name: s.default_value for s in self.inputs}
        self.inputs = Inputs(Sock(s.name, s.default_value) for s in tree.inputs)


class Tree:
    def __init__(self, nodes):
        self.nodes = nodes


class Mat:
    def __init__(self, nodes):
        self.node_tree = Tree(nodes)


BASE = "DR.Pioneer.Definition.Body"


class T(unittest.TestCase):
    def test_base_name(self):
        self.assertEqual(su.base_name(BASE + ".a1b2c3d4e5f6"), BASE)
        self.assertEqual(su.base_name(BASE + ".001"), BASE + ".001")
        self.assertEqual(su.base_name(BASE), BASE)

    def test_stale(self):
        old = Group(BASE + ".a1b2c3d4e5f6", [], 1)
        cur = Group(BASE + ".0123456789ab", [], 2)
        none = Group(BASE + ".ffffffffffff", [])
        other = Group("Other.a1b2c3d4e5f6", [], 0)
        got = su.stale_groups([old, cur, none, other], {BASE: 2})
        self.assertEqual(got, [old, none])

    def test_noop_current(self):
        g = Group(BASE + ".a1b2c3d4e5f6", ["A"], 3)
        m = Mat([Node(g, {"A": 0.4})])
        stale = su.stale_groups([g], {BASE: 3})
        self.assertEqual(stale, [])
        self.assertEqual(su.plan_upgrade([m], stale, lambda o: 1 / 0), [])

    def test_carry_and_drop(self):
        old = Group(BASE + ".a1b2c3d4e5f6", ["Rough", "Gone", "Tint"], 1)
        new = Group(BASE + ".0123456789ab", ["Rough", "Tint", "Added"], 2)
        n = Node(old, {"Rough": 0.3, "Gone": 0.9, "Tint": [1, 0, 0, 1]})
        m = Mat([n])
        calls = []
        plan = su.plan_upgrade([m, Mat([])], su.stale_groups([old], {BASE: 2}),
                               lambda o: calls.append(o) or new)
        self.assertEqual(len(plan), 1)
        _m, node, o, nt, carried, dropped = plan[0]
        self.assertIs(o, old)
        self.assertIs(nt, new)
        self.assertEqual(carried, {"Rough": 0.3, "Tint": [1, 0, 0, 1]})
        self.assertEqual(dropped, ["Gone"])
        su.apply_upgrade(plan)
        self.assertIs(n.node_tree, new)
        self.assertEqual(n.inputs.get("Rough").default_value, 0.3)
        self.assertEqual(n.inputs.get("Tint").default_value, [1, 0, 0, 1])
        self.assertEqual(n.inputs.get("Added").default_value, 0.0)
        self.assertEqual(old.name, BASE + ".a1b2c3d4e5f6")

    def test_build_once_per_tree(self):
        old = Group(BASE + ".a1b2c3d4e5f6", ["A"])
        new = Group(BASE + ".0123456789ab", ["A"], 2)
        calls = []
        mats = [Mat([Node(old, {})]), Mat([Node(old, {})])]
        plan = su.plan_upgrade(mats, [old], lambda o: calls.append(o) or new)
        self.assertEqual(len(plan), 2)
        self.assertEqual(len(calls), 1)



SRC = """
def ensure_a_group():
    name = "GT_A"
    ng, created = _gt_ng_fresh(name, 3)


def ensure_b_group(mask_int: int):
    name = f"GT_B_{bits}"
    ng, created = _gt_ng_fresh(name, 2)


def _c():
    tree, created = _gt_ng_fresh("GT_C", 1)
"""


class VersionParseTests(unittest.TestCase):
    def test_parse(self):
        t = su.parse_versions(SRC)
        self.assertEqual(t["GT_A"], (3, "ensure_a_group", False))
        self.assertEqual(t["GT_B_*"], (2, "ensure_b_group", True))
        self.assertEqual(t["GT_C"], (1, "_c", False))

    def test_real_source(self):
        t = su.current_versions()
        self.assertIn("GT_ZoneEq1", t)
        self.assertTrue(all(isinstance(v[0], int) for v in t.values()))

    def test_scan(self):
        t = su.parse_versions(SRC)
        old = Group("GT_A.0123456789ab", ["X"], ver=2)
        cur = Group("GT_A", ["X"], ver=3)
        pre = Group("GT_B_0101", ["X"])
        other = Group("Foo", ["X"], ver=0)
        out = su.scan([old, cur, pre, other], t)
        self.assertEqual([(o[0].name, o[2], o[3]) for o in out],
                         [("GT_A.0123456789ab", 2, 3), ("GT_B_0101", None, 2)])

if __name__ == "__main__":
    unittest.main()


class CacheKeyTests(unittest.TestCase):
    def test_stamp_change_changes_key(self):
        a = Group("GT_A", ["X"], ver=2)
        b = Group("Foo", ["X"])
        k1 = su.cache_key([a, b])
        self.assertEqual(k1, su.cache_key([a, b]))
        a["arc_gt_ver"] = 3
        self.assertNotEqual(k1, su.cache_key([a, b]))
        self.assertNotEqual(su.cache_key([a]), su.cache_key([a, b]))
