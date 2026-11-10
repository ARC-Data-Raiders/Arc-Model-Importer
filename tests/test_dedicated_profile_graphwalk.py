"""Pure linear traversal regression; fake socket.links forbids quadratic access."""
import ast
from pathlib import Path
import types
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "Shared/materials" / "dispatch.py"
parsed = ast.parse(SOURCE.read_text(encoding="utf-8"))
function = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and
                node.name == "_active_dedicated_outfit_profile")
namespace = {"utils": types.SimpleNamespace(_NODE_GROUP="ArcTexturer")}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
profile = namespace["_active_dedicated_outfit_profile"]

class Socket:
    def __init__(self, name):
        self.name = name
    @property
    def links(self):
        raise AssertionError("NodeSocket.links must never be read")

class Inputs(list):
    def get(self, name):
        return next((socket for socket in self if socket.name == name), None)

class Node:
    def __init__(self, kind, name="", active=True, group=None):
        self.type = kind
        self.name = name
        self.is_active_output = active
        self.node_tree = types.SimpleNamespace(name=group) if group else None
        self.inputs = Inputs([Socket("Surface"), Socket("Volume")])

class Tree:
    def __init__(self, nodes, links):
        self.nodes = nodes
        self.edges = links
        self.link_reads = 0
        self.edge_visits = 0
    @property
    def links(self):
        self.link_reads += 1
        for link in self.edges:
            self.edge_visits += 1
            yield link

class Material(dict):
    use_nodes = True
    def __init__(self, nodes, links, **tags):
        super().__init__(tags)
        self.node_tree = Tree(nodes, links)

def edge(source, target, socket="Surface"):
    return types.SimpleNamespace(from_node=source, to_node=target,
                                 to_socket=target.inputs.get(socket))

class GraphWalkTests(unittest.TestCase):
    def test_ground_truth_connected(self):
        shader, output = Node("BSDF_PRINCIPLED", "GT_Principled_0"), Node("OUTPUT_MATERIAL")
        mat = Material([shader, output], [edge(shader, output)], arc_outfit_color_pipeline="ground_truth")
        self.assertEqual(profile(mat), "layered")
        self.assertEqual(mat.node_tree.link_reads, 1)

    def test_orphan_profile_is_not_connected(self):
        shader, output = Node("BSDF_PRINCIPLED", "GT_Principled_0"), Node("OUTPUT_MATERIAL")
        self.assertEqual(profile(Material([shader, output], [], arc_outfit_color_pipeline="ground_truth")), "")

    def test_inactive_output_does_not_qualify(self):
        shader, output = Node("BSDF_PRINCIPLED", "GT_Principled_0"), Node("OUTPUT_MATERIAL", active=False)
        self.assertEqual(profile(Material([shader, output], [edge(shader, output)], arc_outfit_color_pipeline="ground_truth")), "")

    def test_volume_link_does_not_qualify(self):
        shader, output = Node("BSDF_PRINCIPLED", "GT_Principled_0"), Node("OUTPUT_MATERIAL")
        self.assertEqual(profile(Material([shader, output], [edge(shader, output, "Volume")], arc_outfit_color_pipeline="ground_truth")), "")

    def test_group_via_secondary_input_remains_connected(self):
        group, mix, output = Node("GROUP", group="Arc Texturer.001"), Node("MIX_SHADER"), Node("OUTPUT_MATERIAL")
        self.assertEqual(profile(Material([group, mix, output], [edge(group, mix, "Volume"), edge(mix, output)])), "layered")

    def test_fur_and_fallback_semantics(self):
        shader, output = Node("BSDF_HAIR_PRINCIPLED"), Node("OUTPUT_MATERIAL")
        mat = Material([shader, output], [edge(shader, output)], arc_fur_setup="native-shells-v1")
        self.assertEqual(profile(mat), "fur")
        mat["arc_fur_fallback"] = True
        self.assertEqual(profile(mat), "")

    def test_cycles_and_large_graph_visit_each_edge_once(self):
        shader, output = Node("BSDF_PRINCIPLED", "GT_Principled_0"), Node("OUTPUT_MATERIAL")
        chain = [Node("MATH") for _ in range(2000)]
        links = [edge(shader, chain[0]), edge(chain[-1], output), edge(chain[-1], chain[0])]
        links.extend(edge(left, right) for left, right in zip(chain, chain[1:]))
        mat = Material([shader, output] + chain, links, arc_outfit_color_pipeline="ground_truth")
        self.assertEqual(profile(mat), "layered")
        self.assertEqual(mat.node_tree.link_reads, 1)
        self.assertEqual(mat.node_tree.edge_visits, len(links))

    def test_independent_orphan_section_cannot_leak_profile(self):
        group, plain, output = Node("GROUP", group="ArcTexturer"), Node("BSDF_PRINCIPLED", "Plain"), Node("OUTPUT_MATERIAL")
        mat = Material([group, plain, output], [edge(plain, output)])
        self.assertEqual(profile(mat), "")

if __name__ == "__main__":
    unittest.main(verbosity=2)
