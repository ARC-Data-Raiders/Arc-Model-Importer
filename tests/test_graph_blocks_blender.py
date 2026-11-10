"""graph_blocks collapses repeated math into shared block groups (needs bpy)."""
import importlib.util
import json
import unittest
from pathlib import Path

try:
    import bpy
except ImportError:
    bpy = None

ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "Shared" / "materials" / "graph_blocks.py"
    spec = importlib.util.spec_from_file_location("dr_graph_blocks_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    tree.links.new(bsdf.outputs[0], out.inputs[0])
    param = tree.nodes.new("ShaderNodeValue")
    param.name = "P:Roughness"
    param.outputs[0].default_value = 0.4
    prev = param.outputs[0]
    for i, op in enumerate(("MULTIPLY", "ADD", "POWER", "MINIMUM", "MAXIMUM")):
        node = tree.nodes.new("ShaderNodeMath")
        node.operation = op
        node.name = "Surface.Rough.%d" % i
        node["dr.stage"] = "DR.Surface"
        node.inputs[1].default_value = 0.5 + 0.1 * i
        tree.links.new(prev, node.inputs[0])
        prev = node.outputs[0]
    tree.links.new(prev, bsdf.inputs["Roughness"])
    return tree


def _abs(node):
    x, y = node.location.x, node.location.y
    parent = node.parent
    while parent is not None:
        x += parent.location.x
        y += parent.location.y
        parent = parent.parent
    return x, y


def _wrapper(tree):
    node = tree.nodes.get("DR Outfit")
    return node, node.node_tree


def _param(tree, key):
    """(socket, level) holding parameter ``key``: a look input on the wrapper node or a block input inside it."""
    node, inner = _wrapper(tree)
    for owner in [node] + [n for n in inner.nodes if n.bl_idname == "ShaderNodeGroup"]:
        reg = json.loads(owner.get("dr.param_inputs", "{}"))
        for sock in owner.inputs:
            if reg.get(sock.identifier) == key:
                return sock, ("look" if owner == node else "block")
    return None, None


@unittest.skipIf(bpy is None, "needs Blender")
class GraphBlocksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gb = _load()
        cls.trees = [_build("DR_BlocksTest_A"), _build("DR_BlocksTest_B")]
        cls.stats = [cls.gb.collapse_and_arrange(t) for t in cls.trees]

    def test_top_level_is_shader_output_and_wrapper(self):
        for tree in self.trees:
            self.assertEqual(sorted(n.bl_idname for n in tree.nodes),
                             ["ShaderNodeBsdfPrincipled", "ShaderNodeGroup", "ShaderNodeOutputMaterial"])
            self.assertFalse(any(n.name.startswith("P:") for n in tree.nodes))
            node, inner = _wrapper(tree)
            self.assertEqual(inner.get("dr.wrapper"), 1)
            self.assertTrue(inner.name.startswith("DR Outfit · "))

    def test_wrappers_are_per_material_and_blocks_are_shared(self):
        inner = [_wrapper(t)[1] for t in self.trees]
        self.assertNotEqual(inner[0], inner[1])
        groups = []
        for w in inner:
            self.assertFalse(any(n.bl_idname == "ShaderNodeMath" for n in w.nodes))
            blocks = [n for n in w.nodes if n.bl_idname == "ShaderNodeGroup"]
            self.assertEqual(len(blocks), 1)
            self.assertTrue(blocks[0].node_tree.name.startswith("DR · Surface · "))
            groups.append(blocks[0].node_tree)
        self.assertEqual(groups[0], groups[1])
        self.assertEqual(self.stats[0]["created"], 1)
        self.assertEqual(self.stats[1]["reused"], 1)
        self.assertEqual(sum(1 for n in groups[0].nodes if n.bl_idname == "ShaderNodeMath"), 5)

    def test_second_material_is_filled_from_the_stored_template(self):
        self.assertFalse(self.stats[0]["template"])
        self.assertTrue(self.stats[1]["template"])
        self.assertFalse(any(g.get("dr.wrapper") and g.get("dr.wrapper.template") for g in bpy.data.node_groups))

    def test_value_reaches_shader_through_wrapper(self):
        for tree in self.trees:
            bsdf = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
            self.assertEqual(bsdf.inputs["Roughness"].links[0].from_node.name, "DR Outfit")
            sock, level = _param(tree, "Roughness")
            self.assertIsNotNone(sock)
            self.assertFalse(sock.is_linked)
            self.assertAlmostEqual(sock.default_value, 0.4, places=6)

    def test_writing_one_material_leaves_the_other(self):
        a, _ = _param(self.trees[0], "Roughness")
        b, _ = _param(self.trees[1], "Roughness")
        a.default_value = 0.9
        try:
            self.assertAlmostEqual(b.default_value, 0.4, places=6)
        finally:
            a.default_value = 0.4

    def test_wrapper_layout_left_to_right(self):
        for tree in self.trees:
            inner = _wrapper(tree)[1]
            for link in inner.links:
                self.assertLess(_abs(link.from_node)[0], _abs(link.to_node)[0])

    def test_second_pass_is_a_no_op(self):
        tree = self.trees[0]
        before = (len(tree.nodes), len(tree.links))
        self.assertEqual(self.gb.collapse_and_arrange(tree), {"skipped": "recipe"})
        self.assertEqual((len(tree.nodes), len(tree.links)), before)


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
