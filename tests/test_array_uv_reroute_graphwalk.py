"""Pure operation-count regression for layout-only array UV reroutes."""
import ast
from pathlib import Path
import types
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "Shared/materials" / "gt_outfit_pbr.py"
parsed = ast.parse(SOURCE.read_text(encoding="utf-8"))
function = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and
                node.name == "_wire_array_uv_reroutes")

operation_source=SOURCE.with_name("dr_primary_operations.py")
operation_tree=ast.parse(operation_source.read_text(encoding="utf-8"))
operation_helpers=[node for node in operation_tree.body if isinstance(node,ast.FunctionDef) and node.name in {"legacy_name","legacy_label"}]
operation_namespace={}
exec(compile(ast.Module(body=operation_helpers,type_ignores=[]),str(operation_source),"exec"),operation_namespace)
operation_adapter=types.SimpleNamespace(**{name:operation_namespace[name] for name in ("legacy_name","legacy_label")})

class Socket:
    @property
    def links(self):
        raise AssertionError("Socket.links must not scan the tree")
    @property
    def is_linked(self):
        raise AssertionError("Traversal must use its initial edge snapshot")

class Inputs(list):
    def get(self, name):
        return self[0] if name == "Vector" else None

class Node:
    def __init__(self, name="GT_z0_Tex", kind="ShaderNodeTexImage", y=0):
        self.name = name
        self.bl_idname = kind
        self.label = "TA_Roughness"
        self.inputs = Inputs([Socket()])
        self.location = [100, y]
        self.hide = False

class Links:
    def __init__(self, edges):
        self.edges = list(edges)
        self.iterations = 0
        self.visits = 0
    def __iter__(self):
        self.iterations += 1
        for edge in list(self.edges):
            self.visits += 1
            yield edge
    def remove(self, edge):
        self.edges.remove(edge)
    def new(self, source, target):
        self.edges.append(types.SimpleNamespace(from_socket=source, to_socket=target))

def link(source, node):
    return types.SimpleNamespace(from_socket=source, to_socket=node.inputs[0])

def run(nodes, links, uv):
    reroutes = []
    def reroute(nodes, links, source, name, x, y):
        result = Socket()
        reroutes.append(result)
        return result
    namespace = {"_dr_ops": operation_adapter, "shared_row_y": lambda name: 0,
                 "world_loc": lambda node: tuple(node.location),
                 "place": lambda node, x, y, **kwargs: setattr(node, "location", [x, y]),
                 "_reroute": reroute}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
    bus = namespace["_wire_array_uv_reroutes"](nodes, links, uv)
    return bus, reroutes

class ArrayUVWalkTests(unittest.TestCase):
    def test_semantic_operation_ids_keep_native_array_role_and_color_exclusion(self):
        uv=Socket()
        data=Node("DR.Source.Array.1");color=Node("DR.Source.Array.2")
        data.get=lambda key: {"dr.legacy_name":"GT_z0_Tex","dr.legacy_label":"TA_Roughness"}.get(key)
        color.get=lambda key: {"dr.legacy_name":"GT_ColorTex_0","dr.legacy_label":"TA_Colors"}.get(key)
        data.label="Source data";color.label="Source colors"
        color_edge=link(uv,color);edges=Links([link(uv,data),color_edge])
        _,reroutes=run([data,color],edges,uv)
        self.assertEqual(len(reroutes),1)
        self.assertIn(color_edge,edges.edges)
        self.assertEqual(edges.iterations,1)

    def test_direct_bus_edge_only_rewired(self):
        uv, semantic = Socket(), Socket()
        direct, transformed, disconnected = Node(), Node("GT_z1_Tex"), Node("GT_z2_Tex")
        semantic_edge = link(semantic, transformed)
        edges = Links([link(uv, direct), semantic_edge])
        bus, reroutes = run([direct, transformed, disconnected], edges, uv)
        self.assertEqual(edges.iterations, 1)
        self.assertEqual(edges.visits, 2)
        self.assertIn(semantic_edge, edges.edges)
        self.assertTrue(any(edge.to_socket is direct.inputs[0] and edge.from_socket in reroutes for edge in edges.edges))
        self.assertFalse(any(edge.to_socket is disconnected.inputs[0] for edge in edges.edges))

    def test_mapping_rewired_and_color_texture_excluded(self):
        uv = Socket()
        mapping = Node("GT_z0_uv", "ShaderNodeMapping")
        color = Node("GT_ColorTex0")
        excluded = link(uv, color)
        edges = Links([link(uv, mapping), excluded])
        _, reroutes = run([mapping, color], edges, uv)
        self.assertIn(excluded, edges.edges)
        self.assertTrue(any(edge.to_socket is mapping.inputs[0] and edge.from_socket in reroutes for edge in edges.edges))

    def test_large_graph_reads_each_original_edge_once(self):
        uv = Socket()
        nodes = [Node(f"GT_z{i}_Tex") for i in range(2000)]
        edges = Links([link(uv, node) for node in nodes])
        run(nodes, edges, uv)
        self.assertEqual(edges.iterations, 1)
        self.assertEqual(edges.visits, 2000)
        self.assertEqual(len(edges.edges), 2000)

if __name__ == "__main__":
    unittest.main(verbosity=2)
