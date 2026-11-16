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
            self.assertTrue(blocks[0].node_tree.name.startswith("Arc Outfit · Surface"))
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


def _build_staged(name):
    """Two stages of nodes the collapse keeps as plain nodes (decal names), 24 each."""
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
    for stage, tag in (("DR.Details", "A"), ("DR.Surface", "B")):
        for i in range(24):
            node = tree.nodes.new("ShaderNodeMath")
            node.operation = "MULTIPLY"
            node.name = "Decal.%s.%d" % (tag, i)
            node["dr.stage"] = stage
            node.inputs[1].default_value = 1.0
            tree.links.new(prev, node.inputs[0])
            prev = node.outputs[0]
    tree.links.new(prev, bsdf.inputs["Roughness"])
    return tree


def _load_wrapper_tree():
    path = ROOT / "Shared" / "materials" / "wrapper_tree.py"
    spec = importlib.util.spec_from_file_location("dr_wrapper_tree_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipIf(bpy is None, "needs Blender")
class NestedStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gb = _load()
        cls.wt = _load_wrapper_tree()
        cls.trees = [_build_staged("DR_StageTest_A"), _build_staged("DR_StageTest_B")]
        cls.stats = [cls.gb.collapse_and_arrange(t) for t in cls.trees]

    def test_wrapper_holds_one_node_per_stage(self):
        for tree in self.trees:
            inner = _wrapper(tree)[1]
            self.assertFalse(any(n.bl_idname == "ShaderNodeMath" for n in inner.nodes))
            stages = self.wt.stage_nodes(inner)
            self.assertEqual(sorted(n.label for n in stages), ["Details", "Surface"])
            self.assertLessEqual(sum(1 for n in inner.nodes if n.bl_idname != "NodeFrame"), 8)
            for node in stages:
                self.assertEqual(sum(1 for n in node.node_tree.nodes if n.bl_idname == "ShaderNodeMath"), 24)

    def test_stage_trees_are_per_material(self):
        a, b = (self.wt.stage_trees(_wrapper(t)[1]) for t in self.trees)
        self.assertEqual(len(a), 2)
        self.assertFalse(set(a) & set(b))
        self.assertTrue(self.stats[1]["template"])

    def test_lookup_reaches_stage_nodes(self):
        for tree in self.trees:
            node = self.wt.find_node(tree, "Decal.B.3")
            self.assertIsNotNone(node)
            self.assertTrue(node.id_data.get("dr.stage_tree"))
            self.assertIn("Decal.A.0", self.wt.graph_nodes(tree))

    def test_value_reaches_shader_through_stages(self):
        for tree in self.trees:
            bsdf = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
            self.assertEqual(bsdf.inputs["Roughness"].links[0].from_node.name, "DR Outfit")
            param = self.wt.find_node(tree, "P:Roughness")      # unshared parameter: kept as a node
            self.assertTrue(param.id_data.get("dr.stage_tree"))
            self.assertAlmostEqual(param.outputs[0].default_value, 0.4, places=6)
            inner = _wrapper(tree)[1]
            for link in inner.links:
                self.assertLess(_abs(link.from_node)[0], _abs(link.to_node)[0])

    def test_writing_one_material_leaves_the_other(self):
        a = self.wt.find_node(self.trees[0], "Decal.B.3")
        b = self.wt.find_node(self.trees[1], "Decal.B.3")
        a.inputs[1].default_value = 0.25
        try:
            self.assertAlmostEqual(b.inputs[1].default_value, 1.0, places=6)
        finally:
            a.inputs[1].default_value = 1.0


def _load_user_controls():
    path = ROOT / "Shared" / "materials" / "user_controls.py"
    spec = importlib.util.spec_from_file_location("dr_user_controls_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _look_material(uc, name, look=True):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    tree.links.new(bsdf.outputs[0], out.inputs[0])
    node, albedo, _r, _m = uc.insert(tree.nodes, tree.links, (0.5, 0.2, 0.1), 0.5, 0.0)
    tree.links.new(albedo, bsdf.inputs["Base Color"])
    if not look:
        tree.nodes.remove(tree.nodes[uc.LOOK_NAME])
    uc.bump_targets()
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    mesh.materials.append(mat)
    return mat, obj, node


@unittest.skipIf(bpy is None, "needs Blender")
class LookFromObjectTests(unittest.TestCase):
    """Look sliders are object properties read by DR.Look, not material socket values."""

    @classmethod
    def setUpClass(cls):
        cls.uc = _load_user_controls()

    def test_insert_links_every_control(self):
        uc = self.uc
        mat, _obj, node = _look_material(uc, "look_a")
        self.assertTrue(all(node.inputs[s].is_linked for s in uc.SOCKETS))
        self.assertTrue(uc.look_linked(mat))
        self.assertEqual(uc.cached_material_targets(mat, own=True), [])

    def test_apply_writes_the_object_only(self):
        uc = self.uc
        mat, obj, node = _look_material(uc, "look_b")
        other = bpy.data.objects.new("look_b_other", obj.data)
        before = node.inputs["UC Saturation"].default_value
        n = uc.apply([mat], {"arc_uc_sat": 1.4, "arc_uc_hue": 0.2}, [obj])
        self.assertEqual(n, 2)
        default = uc.BY_PROP["arc_uc_sat"][3]
        self.assertAlmostEqual(obj[uc.LOOK_KEY["arc_uc_sat"]], 1.4 - default, places=6)
        self.assertAlmostEqual(obj[uc.LOOK_KEY["arc_uc_hue"]], 0.2 - uc.BY_PROP["arc_uc_hue"][3], places=6)
        self.assertEqual(node.inputs["UC Saturation"].default_value, before)
        self.assertNotIn(uc.LOOK_KEY["arc_uc_sat"], other.keys())
        self.assertAlmostEqual(uc.LookSlot(other, "arc_uc_sat").default_value, default, places=6)
        self.assertAlmostEqual(uc.LookSlot(obj, "arc_uc_sat").default_value, 1.4, places=6)

    def test_old_material_keeps_socket_path_until_upgraded(self):
        uc = self.uc
        mat, obj, node = _look_material(uc, "look_c", look=False)
        self.assertFalse(uc.look_linked(mat))
        uc.apply([mat], {"arc_uc_wear": 0.7}, [obj])
        self.assertAlmostEqual(node.inputs["UC Wear"].default_value, 0.7, places=6)
        self.assertNotIn(uc.LOOK_KEY["arc_uc_wear"], obj.keys())
        self.assertEqual(uc.look_upgrade([obj]), 1)
        self.assertTrue(uc.look_linked(mat))
        self.assertAlmostEqual(uc.LookSlot(obj, "arc_uc_wear").default_value, 0.7, places=6)
        self.assertEqual(uc.look_upgrade([obj]), 0)


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
