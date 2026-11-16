"""Graph contract layout engine, on fake node trees (no bpy)."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "Shared" / "materials" / "graph_layout.py"


def load_engine():
    tree = ast.parse(SRC.read_text(encoding="ascii"))
    ns = {"__name__": "graph_layout_under_test"}
    exec(compile(tree, str(SRC), "exec"), ns)
    return ns


class Socket:
    def __init__(self, node, name, is_output, kind="VALUE"):
        self.node, self.name, self.identifier, self.is_output = node, name, name, is_output
        self.type, self.enabled, self.hide, self.hide_value = kind, True, False, False
        self.default_value = 0.0

    @property
    def links(self):
        t = self.node.tree
        return [l for l in t.links if (l.from_socket is self if self.is_output else l.to_socket is self)]

    @property
    def is_linked(self):
        return bool(self.links)

    def as_pointer(self):
        return id(self)


class Node(dict):
    def __init__(self, tree, bl_idname, name, kind, ins=1, outs=1):
        super().__init__()
        self.tree, self.bl_idname, self.name, self.type = tree, bl_idname, name, kind
        self.label, self.parent, self.location, self.width, self.hide = "", None, (0.0, 0.0), 140.0, False
        self.dimensions = (0.0, 0.0)
        self.inputs = [Socket(self, "In%d" % i, False) for i in range(ins)]
        self.outputs = [Socket(self, "Out%d" % i, True) for i in range(outs)]
        self.operation, self.label_size, self.shrink, self.use_custom_color, self.color = "ADD", 20, True, False, (0, 0, 0)
        self.node_tree = None

    __eq__ = object.__eq__      # ID props live in the dict; identity like bpy structs
    __hash__ = object.__hash__

    def as_pointer(self):
        return id(self)


KINDS = {"NodeFrame": ("FRAME", 0, 0), "NodeReroute": ("REROUTE", 1, 1), "ShaderNodeMath": ("MATH", 2, 1),
         "ShaderNodeTexImage": ("TEX_IMAGE", 1, 2), "ShaderNodeOutputMaterial": ("OUTPUT_MATERIAL", 3, 0),
         "ShaderNodeBsdfPrincipled": ("BSDF_PRINCIPLED", 3, 1), "ShaderNodeValue": ("VALUE", 0, 1)}


class Nodes(list):
    def __init__(self, tree):
        super().__init__()
        self.tree = tree

    def new(self, bl_idname):
        kind, i, o = KINDS[bl_idname]
        n = Node(self.tree, bl_idname, "%s.%03d" % (bl_idname, len(self)), kind, i, o)
        self.append(n)
        return n

    def remove(self, n):
        self.tree.links[:] = [l for l in self.tree.links if l.from_node is not n and l.to_node is not n]
        for c in self:
            if c.parent is n:
                c.parent = None
        list.remove(self, n)

    def get(self, name):
        return next((n for n in self if n.name == name), None)

    def foreach_get(self, attr, flat):
        for i, n in enumerate(self):
            flat[2 * i], flat[2 * i + 1] = n.location

    def foreach_set(self, attr, flat):
        for i, n in enumerate(self):
            n.location = (flat[2 * i], flat[2 * i + 1])


class Link:
    def __init__(self, a, b):
        self.from_socket, self.to_socket = a, b
        self.from_node, self.to_node = a.node, b.node
        self.is_muted = False


class Links(list):
    def new(self, a, b):
        self[:] = [l for l in self if l.to_socket is not b]
        l = Link(a, b)
        self.append(l)
        return l


class Tree(dict):
    def __init__(self, name="Mat"):
        super().__init__()
        self.name, self.library = name, None
        self.nodes, self.links = Nodes(self), Links()

    def as_pointer(self):
        return id(self)

    def add(self, bl_idname, name, loc=(0.0, 0.0)):
        n = self.nodes.new(bl_idname)
        n.name, n.location = name, loc
        return n

    def link(self, a, b, out=0, inp=0):
        return self.links.new(a.outputs[out], b.inputs[inp])


def effective_inputs(tree):
    """Signature view: each input's real source, reroutes passed through, frames ignored."""
    out = set()
    for l in tree.links:
        if l.to_node.bl_idname == "NodeReroute":
            continue
        src = l.from_socket
        while src.node.bl_idname == "NodeReroute":
            src = src.node.inputs[0].links[0].from_socket
        out.add((src.node.name, src.name, l.to_node.name, l.to_socket.name))
    return out


def chain_tree():
    t = Tree()
    img = t.add("ShaderNodeTexImage", "Image", (900, 300))
    maths = [t.add("ShaderNodeMath", "M%d" % i, (500 - 90 * i, 40 * i)) for i in range(6)]
    bsdf = t.add("ShaderNodeBsdfPrincipled", "BSDF", (-400, 0))
    out = t.add("ShaderNodeOutputMaterial", "Out", (-900, 0))
    t.link(img, maths[0])
    for a, b in zip(maths, maths[1:]):
        t.link(a, b)
    t.link(maths[-1], bsdf)
    t.link(img, bsdf, 0, 1)          # spans the whole chain: must ride the bus
    t.link(bsdf, out)
    return t


class GraphLayoutTests(unittest.TestCase):
    def setUp(self):
        self.g = load_engine()

    def test_tag_api_names_exist(self):
        for name in ("tag", "finalize_tree", "ROLE_COLORS", "STAGES", "partition_into_groups", "assert_clean"):
            self.assertIn(name, self.g)
        t = Tree()
        n = t.add("ShaderNodeMath", "m")
        self.assertIs(self.g["tag"](n, stage="Mask", zone="Zone 1", role="mask", label="Mask A"), n)
        self.assertEqual((n["arc_stage"], n["arc_zone"], n["arc_role"], n.label), ("Mask", "Zone 1", "mask", "Mask A"))

    def test_untagged_tree_is_clean_after_finalize(self):
        t = chain_tree()
        before = effective_inputs(t)
        self.assertTrue(self.g["assert_clean"](t))
        stats = self.g["finalize_tree"](t, "Test", recursive=False)
        self.assertEqual(self.g["assert_clean"](t), [])
        self.assertEqual(stats["laid_out"], 1)
        self.assertEqual(stats["violations"], 0)
        self.assertEqual(effective_inputs(t), before)
        # Bus reroutes only where they cut crossings, capped at 15% of the node count.
        self.assertLessEqual(stats["bus_reroutes"], int(0.15 * len(t.nodes)))
        labels = {n.label for n in t.nodes if n.bl_idname == "NodeReroute"}
        self.assertLessEqual(labels, {"Out0"})
        for n in t.nodes:
            if n.bl_idname != "NodeFrame":
                self.assertIsNotNone(n.parent, n.name)
            if n.bl_idname == "ShaderNodeMath":
                self.assertEqual(n.label, "Add")
        frames = {n.label for n in t.nodes if n.bl_idname == "NodeFrame"}
        self.assertTrue({"Textures", "Output"} <= frames, frames)
        self.assertEqual(t["arc_layout_ver"], 2)

    def test_second_pass_is_cached_and_stable(self):
        t = chain_tree()
        self.g["finalize_tree"](t, "Test", recursive=False)
        def snapshot():   # bus reroutes are rebuilt each pass, so key them by place, not name
            return sorted((n.label if n.bl_idname == "NodeReroute" else n.name, n.location) for n in t.nodes)
        locs = snapshot()
        stats = self.g["finalize_tree"](t, "Test", recursive=False)
        self.assertEqual(stats["cached"], 1)
        stats = self.g["finalize_tree"](t, "Test", recursive=False, force=True)
        self.assertEqual(stats["laid_out"], 1)
        self.assertEqual(locs, snapshot())
        self.assertLessEqual(sum(1 for n in t.nodes if n.bl_idname == "NodeReroute"), int(0.15 * len(t.nodes)))

    def test_hand_edit_is_detected_and_left_alone(self):
        t = chain_tree()
        self.g["finalize_tree"](t, "Test", recursive=False)
        self.assertFalse(self.g["is_user_edited"](t))
        m = t.nodes.get("M2")
        m.location = (m.location[0] + 500, m.location[1])
        self.assertTrue(self.g["is_user_edited"](t))
        stats = self.g["finalize_tree"](t, "Test", recursive=False)
        self.assertEqual((stats["edited"], stats["laid_out"]), (1, 0))

    def test_zones_get_own_frames_in_natural_order(self):
        t = Tree()
        out = t.add("ShaderNodeOutputMaterial", "Out")
        prev = None
        for z in (10, 2, 1):
            a = self.g["tag"](t.add("ShaderNodeMath", "Z%d" % z), zone="Zone %d" % z)
            t.link(a, out, 0, 0 if z == 1 else (1 if z == 2 else 2))
            prev = a
        self.g["finalize_tree"](t, "Test", recursive=False)
        self.assertEqual(self.g["assert_clean"](t), [])
        ys = {n.parent.label: n.location[1] for n in t.nodes if n.name.startswith("Z")}
        self.assertGreater(ys["Zone 1"], ys["Zone 2"])
        self.assertGreater(ys["Zone 2"], ys["Zone 10"])
        shades = {n.label: n.color for n in t.nodes if n.bl_idname == "NodeFrame"}
        self.assertNotEqual(shades["Zone 1"], shades["Zone 2"])
        self.assertEqual(shades["Zone 1"], shades["Zone 10"])
        self.assertIsNotNone(prev)

    def test_remove_unused_and_partition_plan(self):
        t = chain_tree()
        dead = t.add("ShaderNodeMath", "Dead")
        t.link(t.nodes.get("Image"), dead)
        self.assertEqual(self.g["remove_unused"](t), 1)
        self.assertIsNone(t.nodes.get("Dead"))
        self.g["tag"](t.nodes.get("M1"), zone="Zone 1")
        plan = self.g["partition_into_groups"](t, "arc_zone")
        self.assertEqual(plan, {"Zone 1": ["M1"]})
        self.assertIsNotNone(t.nodes.get("M1"))

    def test_clean_name_drops_prefix_and_hash(self):
        clean = self.g["_clean_name"]
        self.assertEqual(clean("DR.ZoneMask.0a1b2c3d4e"), "ZoneMask")
        self.assertEqual(clean("DR.Look.001"), "Look")


if __name__ == "__main__":
    unittest.main()
