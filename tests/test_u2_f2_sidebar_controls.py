"""U2 F2: sidebar "Colors and Wear" lists controls nested in stage/zone groups (weapon and enemy
native flow trees), with min/max and reset to the imported value. Runs inside Blender (bpy)."""
import importlib
import json
import os
import sys
import types
import unittest
from pathlib import Path

import bpy

ROOT = Path(os.environ.get("DATARAIDERS_TEST_REPOSITORY", Path(__file__).resolve().parents[1]))
PKG = "_dr_probe_u2_f2"


def _load(name):
    for pkg, path in ((PKG, ROOT), (PKG + ".Shared", ROOT / "Shared")):
        if pkg not in sys.modules:
            mod = types.ModuleType(pkg)
            mod.__path__ = [str(path)]
            sys.modules[pkg] = mod
    return importlib.import_module(PKG + "." + name)


def _flow_material(tag):
    """Material -> "DR Material Flow" (not a control tree) -> stage group holding a labelled MI
    parameter Value node and a nested DR.* control group, the shape collapse leaves behind."""
    ctl = bpy.data.node_groups.new("DR.F2Test" + tag, "ShaderNodeTree")
    wear = ctl.interface.new_socket("Wear", in_out="INPUT", socket_type="NodeSocketFloat")
    wear.min_value, wear.max_value, wear.default_value = 0.0, 2.0, 0.0
    ctl.interface.new_socket("Out", in_out="OUTPUT", socket_type="NodeSocketFloat")

    stage = bpy.data.node_groups.new("F2 Stage" + tag, "ShaderNodeTree")
    stage.interface.new_socket("Out", in_out="OUTPUT", socket_type="NodeSocketFloat")
    s_out = stage.nodes.new("NodeGroupOutput")
    param = stage.nodes.new("ShaderNodeValue")
    param.name, param.label = "DR.Surface.Value.0001", "Edge Wear Amount"
    param.outputs[0].default_value = 0.4
    math = stage.nodes.new("ShaderNodeMath")
    stage.links.new(param.outputs[0], math.inputs[0])
    inner = stage.nodes.new("ShaderNodeGroup")
    inner.node_tree = ctl
    inner.name = "F2Ctl"
    inner.inputs["Wear"].default_value = 0.5
    stage.links.new(inner.outputs[0], math.inputs[1])
    stage.links.new(math.outputs[0], s_out.inputs[0])
    unnamed = stage.nodes.new("ShaderNodeValue")      # constant: not a control
    stage.links.new(unnamed.outputs[0], math.inputs[2])

    flow = bpy.data.node_groups.new("DR Material Flow" + tag, "ShaderNodeTree")
    flow.interface.new_socket("Out", in_out="OUTPUT", socket_type="NodeSocketFloat")
    f_out = flow.nodes.new("NodeGroupOutput")
    st = flow.nodes.new("ShaderNodeGroup")
    st.node_tree = stage
    flow.links.new(st.outputs[0], f_out.inputs[0])

    mat = bpy.data.materials.new("F2Mat" + tag)
    mat.use_nodes = True
    top = mat.node_tree.nodes.new("ShaderNodeGroup")
    top.node_tree = flow
    mesh = bpy.data.meshes.new("F2Mesh" + tag)
    obj = bpy.data.objects.new("F2Obj" + tag, mesh)
    mesh.materials.append(mat)
    return obj, mat, param, inner


class SidebarNestedControlsTest(unittest.TestCase):
    def setUp(self):
        self.sco = _load("Shared.shader_control_ops")
        self.sco.clear_discover_cache()

    def _check(self, tag):
        sco = self.sco
        obj, mat, param, inner = _flow_material(tag)
        sco.capture_imported(obj)
        rows = {e["name"]: e for e in sco.discover(obj, False)}
        self.assertIn("Edge Wear Amount", rows)     # nested MI parameter
        self.assertIn("Wear", rows)                 # control group inside a non-control group
        self.assertNotIn("Value", rows)             # unnamed constant stays hidden
        wear, edge = rows["Wear"], rows["Edge Wear Amount"]
        self.assertEqual((wear["min"], wear["max"]), (0.0, 2.0))
        self.assertLess(edge["min"], edge["max"])
        self.assertAlmostEqual(edge["default"], 0.4, places=5)
        self.assertAlmostEqual(json.loads(param["arc_import_defaults"])["Edge Wear Amount"], 0.4, places=5)
        # write through the recursive lookup, then reset to the imported value
        for name, value in (("Edge Wear Amount", 0.9), ("Wear", 1.5)):
            for mat_name, node_name in rows[name]["targets"]:
                node = sco.find_control_node(bpy.data.materials[mat_name], node_name)
                sco.control_socket(node, name).default_value = value
        self.assertAlmostEqual(param.outputs[0].default_value, 0.9, places=5)
        self.assertAlmostEqual(inner.inputs["Wear"].default_value, 1.5, places=5)
        sco.clear_discover_cache()
        sco._restore_defaults(obj, False)
        self.assertAlmostEqual(param.outputs[0].default_value, 0.4, places=5)

    def test_weapon_flow_tree(self):
        self._check("Weapon")

    def test_enemy_flow_tree(self):
        self._check("Enemy")


if __name__ == "__main__":
    unittest.main()
