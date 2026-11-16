"""Native interop flow must fold into stage groups after finish() (needs bpy).

Checks the graph contract shape, the recursive albedo target and that a sample taken
after finish() builds fresh nodes instead of reusing sockets the grouping moved.
"""
import importlib
import json
import sys
import unittest
from pathlib import Path

try:
    import bpy
except ImportError:
    bpy = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(bpy is None, "needs Blender")
class InteropStageGroupTests(unittest.TestCase):
    def setUp(self):
        if str(ROOT.parent) not in sys.path:
            sys.path.insert(0, str(ROOT.parent))
        self.interop = importlib.import_module(ROOT.name + ".Shared.materials.native.interop")
        self.gl = importlib.import_module(ROOT.name + ".Shared.materials.graph_layout")
        self.before = set(bpy.data.node_groups.keys())
        self.mat = bpy.data.materials.new("DR_u2_f3_interop")
        self.img = bpy.data.images.new("DR_u2_f3_img", 4, 4)

    def tearDown(self):
        bpy.data.materials.remove(self.mat)
        bpy.data.images.remove(self.img)
        for name in list(bpy.data.node_groups.keys()):
            if name not in self.before and bpy.data.node_groups[name].users == 0:
                bpy.data.node_groups.remove(bpy.data.node_groups[name])

    def _walk(self, tree, seen=None):
        seen = seen if seen is not None else set()
        if tree is None or tree.name in seen:
            return []
        seen.add(tree.name)
        out = [tree]
        for n in tree.nodes:
            if n.type == "GROUP":
                out += self._walk(n.node_tree, seen)
        return out

    def _build(self):
        g = self.interop.Graph(self.mat, None, None, {"warnings": []})
        with g.block("Source"):
            tex = g.sample_image(self.img, label="base")
        with g.block("Colour grade"):
            col = tex["color"]
            for i in range(40):
                col = g.vector("SCALE", col, scale=g.add(tex["r"], 0.01 * i))
        with g.block("Roughness"):
            rough = tex["a"]
            for i in range(40):
                rough = g.clamp(g.mul(rough, 1.0 + 0.001 * i))
        g.finish(col, roughness=rough)
        return g

    def test_flow_meets_contract(self):
        g = self._build()
        top = [n for n in g.tree.nodes if n.bl_idname not in ("NodeFrame", "NodeReroute")]
        self.assertLessEqual(len(top), 12, [n.name for n in top])
        self.assertLessEqual(self.gl.tree_depth(g.tree), 4)
        for t in self._walk(g.tree):
            if t.name.startswith("DR.UserControls"):
                continue  # shared controls group: owned by user_controls.py, not this flow
            body = [n for n in t.nodes if n.bl_idname != "NodeFrame"]
            self.assertLessEqual(len(body), 90, t.name)
            self.assertEqual(self.gl.assert_clean(t), [], t.name)

    def test_albedo_target_resolves(self):
        self._build()
        target = json.loads(self.mat["arc_albedo_target"])
        tree = bpy.data.node_groups[target["tree"]]
        node = tree.nodes[target["node"]]
        self.assertTrue(node.get("dr_albedo_target"))
        self.assertEqual(self.mat["arc_albedo_node"], node.name)

    def test_sample_after_finish_builds_fresh_nodes(self):
        g = self._build()
        late = g.sample_image(self.img, label="late cubemap")
        node = late["node"]
        self.assertIn(node.name, g.tree.nodes)
        src = node.inputs["Vector"].links[0].from_node
        self.assertIn(src.name, g.tree.nodes)


if __name__ == "__main__":
    unittest.main()
