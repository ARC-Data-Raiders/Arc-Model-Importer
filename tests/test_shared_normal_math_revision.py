import importlib
import math
import sys
import unittest
from pathlib import Path
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
Graph = importlib.import_module(ROOT.name + ".Shared.materials.native.interop").Graph


def sample(socket, incoming):
    if not socket.is_output:
        return sample(socket.links[0].from_socket, incoming) if socket.is_linked else socket.default_value
    node = socket.node
    if node.type == "GROUP_INPUT":
        return incoming[socket.name]
    values = [sample(port, incoming) for port in node.inputs if port.enabled]
    if node.type == "MATH":
        first = values[0]
        second = values[1] if len(values) > 1 else 0
        return {"ADD": lambda: first + second, "SUBTRACT": lambda: first - second, "MULTIPLY": lambda: first * second, "DIVIDE": lambda: first / second if second else 0, "MAXIMUM": lambda: max(first, second), "SQRT": lambda: math.sqrt(max(first, 0))}[node.operation]()
    if node.type == "COMBXYZ":
        return tuple(values[:3])
    if node.type == "SEPXYZ":
        return values[0][list(node.outputs).index(socket)]
    if node.type == "VECT_MATH":
        first = values[0]
        if node.operation == "NORMALIZE":
            length = math.sqrt(sum(part * part for part in first))
            return tuple(part / length for part in first) if length else (0, 0, 0)
        if node.operation == "DOT_PRODUCT":
            return sum(left * right for left, right in zip(first, values[1]))
        if node.operation == "SUBTRACT":
            return tuple(left - right for left, right in zip(first, values[1]))
        if node.operation == "SCALE":
            return tuple(part * sample(node.inputs["Scale"], incoming) for part in first)
    if node.type == "GROUP":
        group_inputs = {port.name: sample(port, incoming) for port in node.inputs}
        output = next(item for item in node.node_tree.nodes if item.type == "GROUP_OUTPUT")
        return sample(output.inputs[socket.name], group_inputs)
    raise AssertionError(node.type)


class SharedMathTests(unittest.TestCase):
    def test_non_neutral_rnm_and_saturated_decode_match_original_math(self):
        material = bpy.data.materials.new("Math parity fixture")
        graph = Graph(material, None, None, {})
        def normalized(values):
            magnitude = math.sqrt(sum(value * value for value in values))
            return tuple(value / magnitude for value in values) if magnitude else (0, 0, 0)
        try:
            for red, green, strength in ((0, 0, 1), (.2, .9, 2), (.5, .5, 0), (1, .6, -.7)):
                horizontal, vertical = (red * 2 - 1) * strength, (green * 2 - 1) * strength
                depth = math.sqrt(max(1 - horizontal * horizontal - vertical * vertical, 0))
                actual = sample(graph.decode_normal({"r": red, "g": green}, strength), {})
                for value, expected in zip(actual, normalized((horizontal, vertical, depth))):
                    self.assertAlmostEqual(value, expected, places=6)
            for base, detail in (((.3, .4, .8660254), (-.2, .4, .8944272)), ((-.5, .5, .7071068), (.6, -.2, .7745967))):
                translated = (base[0], base[1], base[2] + 1)
                reflected = (-detail[0], -detail[1], detail[2])
                factor = sum(left * right for left, right in zip(translated, reflected)) / translated[2]
                expected = normalized(tuple(value * factor - delta for value, delta in zip(translated, reflected)))
                for value, reference in zip(sample(graph.rnm(base, detail), {}), expected):
                    self.assertAlmostEqual(value, reference, places=6)
        finally:
            bpy.data.materials.remove(material)

    def test_decode_and_rnm_share_immutable_groups_with_instance_inputs(self):
        material = bpy.data.materials.new("Math fixture")
        graph = Graph(material, None, None, {})
        first = graph.decode_normal({"r": .7, "g": .3}, .5)
        second = graph.decode_normal({"r": .7, "g": .3}, 1)
        self.assertEqual(first.node.type, "GROUP")
        self.assertEqual(first.node.node_tree, second.node.node_tree)
        for result, strength in ((first, .5), (second, 1)):
            x, y = .4 * strength, -.4 * strength
            expected = (x, y, math.sqrt(1 - x * x - y * y))
            for actual, reference in zip(sample(result, {}), expected):
                self.assertAlmostEqual(actual, reference, places=6)
        before = tuple(sample(second, {}))
        first.node.inputs["Strength"].default_value = .25
        self.assertEqual(tuple(sample(second, {})), before)
        combined = graph.rnm((0, 0, 1), second)
        self.assertEqual(combined.node.type, "GROUP")
        for actual, expected in zip(sample(combined, {}), before):
            self.assertAlmostEqual(actual, expected, places=6)
        for tree in (first.node.node_tree, combined.node.node_tree):
            self.assertFalse(any(node.type in {"TEX_IMAGE", "VALUE", "RGB"} or node.get("dr_preview_parameter") for node in tree.nodes))
