"""Driven Value nodes move into a shared group so stage grouping no longer skips the tree.

Needs bpy. Covers hoist_driven_values (no drivers left, one shared group per driver,
links kept, collapse not skipped as animated) and drop_reroutes (links kept).
"""
import importlib
import sys
import unittest
from pathlib import Path

try:
    import bpy
except ImportError:
    bpy = None

ROOT = Path(__file__).resolve().parents[1]
EXPR = "frame / 24.0"


@unittest.skipIf(bpy is None, "needs Blender")
class DrivenValueTests(unittest.TestCase):
    def setUp(self):
        if str(ROOT.parent) not in sys.path:
            sys.path.insert(0, str(ROOT.parent))
        self.dv = importlib.import_module(ROOT.name + ".Shared.materials.native.driven_values")
        self.gl = importlib.import_module(ROOT.name + ".Shared.materials.graph_layout")
        self.before = set(bpy.data.node_groups.keys())
        self.trees = []

    def tearDown(self):
        for t in self.trees:
            if t.name in bpy.data.node_groups:
                bpy.data.node_groups.remove(t)
        for name in list(bpy.data.node_groups.keys()):
            if name not in self.before:
                bpy.data.node_groups.remove(bpy.data.node_groups[name])

    def _tree(self, name):
        t = bpy.data.node_groups.new(name, "ShaderNodeTree")
        self.trees.append(t)
        t.interface.new_socket("Out", in_out="OUTPUT", socket_type="NodeSocketFloat")
        out = t.nodes.new("NodeGroupOutput")
        val = t.nodes.new("ShaderNodeValue")
        val.name = val.label = "Time"
        reroute = t.nodes.new("NodeReroute")
        t.links.new(val.outputs[0], reroute.inputs[0])
        sock = reroute.outputs[0]
        for i in range(8):
            m = t.nodes.new("ShaderNodeMath")
            m.operation = "MULTIPLY"
            m.inputs[1].default_value = 1.0 + i
            t.links.new(sock, m.inputs[0])
            sock = m.outputs[0]
        t.links.new(sock, out.inputs[0])
        fc = val.outputs[0].driver_add("default_value")
        fc.driver.type = "SCRIPTED"
        fc.driver.expression = EXPR
        return t

    def test_hoist_shares_group_and_keeps_links(self):
        a, b = self._tree("DR_u2_f3_dv_a"), self._tree("DR_u2_f3_dv_b")
        self.assertEqual(self.dv.hoist_driven_values(a), 1)
        self.assertEqual(self.dv.hoist_driven_values(b), 1)
        for t in (a, b):
            self.assertTrue(t.animation_data is None or not len(t.animation_data.drivers))
            g = t.nodes["Time"]
            self.assertEqual(g.bl_idname, "ShaderNodeGroup")
            self.assertEqual(len(g.outputs[0].links), 1)
        self.assertIs(a.nodes["Time"].node_tree, b.nodes["Time"].node_tree)
        inner = a.nodes["Time"].node_tree
        drivers = inner.animation_data.drivers
        self.assertEqual(len(drivers), 1)
        self.assertEqual(drivers[0].driver.expression, EXPR)

    def test_drop_reroutes_keeps_links(self):
        t = self._tree("DR_u2_f3_dv_r")
        self.dv.hoist_driven_values(t)
        self.assertEqual(self.dv.drop_reroutes(t), 1)
        self.assertFalse([n for n in t.nodes if n.bl_idname == "NodeReroute"])
        link = t.nodes["Time"].outputs[0].links[0]
        self.assertEqual(link.to_node.bl_idname, "ShaderNodeMath")

    def test_collapse_not_skipped_after_hoist(self):
        t = self._tree("DR_u2_f3_dv_c")
        self.dv.hoist_driven_values(t)
        self.dv.drop_reroutes(t)
        stats = self.gl.finalize_tree(t, collapse=True, force=True)
        coll = (stats or {}).get("collapse") or {}
        self.assertNotEqual(coll.get("skipped"), "animated", coll)


if __name__ == "__main__":
    unittest.main()
