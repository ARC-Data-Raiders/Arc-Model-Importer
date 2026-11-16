"""U2 F2: moving nodes into a child group keeps reroute socket types (FloatFactor stays
FloatFactor, not Color), and group data names never end in .001. Runs inside Blender (bpy)."""
import importlib
import os
import sys
import types
import unittest
from pathlib import Path

import bpy

ROOT = Path(os.environ.get("DATARAIDERS_TEST_REPOSITORY", Path(__file__).resolve().parents[1]))
PKG = "_dr_probe_u2_f2r"


def _load(name):
    for pkg, path in ((PKG, ROOT), (PKG + ".Shared", ROOT / "Shared")):
        if pkg not in sys.modules:
            mod = types.ModuleType(pkg)
            mod.__path__ = [str(path)]
            sys.modules[pkg] = mod
    return importlib.import_module(PKG + "." + name)


class CopyRerouteTest(unittest.TestCase):
    def setUp(self):
        self.gl = _load("Shared.materials.graph_layout")
        self.mat = bpy.data.materials.new("F2 Reroute Test")
        self.mat.use_nodes = True
        self.made = []

    def tearDown(self):
        bpy.data.materials.remove(self.mat)
        for g in list(bpy.data.node_groups):
            if g.name.startswith("F2 Reroute"):
                bpy.data.node_groups.remove(g)

    def _chain(self, nt):
        val = nt.nodes.new("ShaderNodeValue")
        val.outputs[0].default_value = 0.3
        rr = nt.nodes.new("NodeReroute")
        rr.name = "F2 Fac Reroute"
        rr.socket_idname = "NodeSocketFloatFactor"
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        # Reroute fed by nothing: its type is only what socket_idname says (a fresh one is Color).
        nt.links.new(val.outputs[0], mix.inputs["A"])
        nt.links.new(rr.outputs[0], mix.inputs["Factor"])
        out = nt.nodes.get("Principled BSDF")
        nt.links.new(mix.outputs[2], out.inputs["Base Color"])
        return val, rr, mix

    def test_reroute_keeps_float_factor(self):  # reroute fed by nothing, typed by its consumer
        nt = self.mat.node_tree
        val, rr, mix = self._chain(nt)
        self.assertEqual(rr.socket_idname, "NodeSocketFloatFactor")
        gn, _created = self.gl._move_into_group(nt, [val, rr, mix], "F2 Reroute Group", "surface")
        inner = gn.node_tree.nodes.get("F2 Fac Reroute")
        self.assertIsNotNone(inner)
        self.assertEqual(inner.socket_idname, "NodeSocketFloatFactor")
        self.assertEqual(inner.outputs[0].type, "VALUE")

    def test_reroute_fed_by_factor_input_stays_factor(self):
        # Source: a group with a Factor input feeding a reroute; move the reroute + mix down.
        g = bpy.data.node_groups.new("F2 Reroute Src", "ShaderNodeTree")
        g.interface.new_socket(name="Fac", in_out="INPUT", socket_type="NodeSocketFloat").subtype = "FACTOR"
        g.interface.new_socket(name="Color", in_out="OUTPUT", socket_type="NodeSocketColor")
        gi = g.nodes.new("NodeGroupInput")
        go = g.nodes.new("NodeGroupOutput")
        rr = g.nodes.new("NodeReroute")
        rr.name = "F2 Fac Reroute"
        mix = g.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        g.links.new(gi.outputs[0], rr.inputs[0])
        g.links.new(rr.outputs[0], mix.inputs["Factor"])
        val = g.nodes.new("ShaderNodeValue")
        g.links.new(val.outputs[0], mix.inputs["A"])
        g.links.new(mix.outputs[2], go.inputs[0])
        src_type = rr.socket_idname
        self.assertNotEqual(src_type, "NodeSocketColor")
        gn, _created = self.gl._move_into_group(g, [rr, mix, val], "F2 Reroute Inner", "surface")
        inner = gn.node_tree.nodes.get("F2 Fac Reroute")
        self.assertEqual(inner.socket_idname, src_type)
        self.assertEqual(inner.outputs[0].type, "VALUE")

    def test_group_name_has_no_numeric_suffix(self):
        first = bpy.data.node_groups.new("F2 Reroute Name", "ShaderNodeTree")
        nt = self.mat.node_tree
        val, rr, mix = self._chain(nt)
        gn, _created = self.gl._move_into_group(nt, [val, rr, mix], "F2 Reroute Name", "surface")
        self.assertNotRegex(gn.node_tree.name, r"\.\d{3}$")
        self.assertNotEqual(gn.node_tree, first)


if __name__ == "__main__":
    unittest.main()
