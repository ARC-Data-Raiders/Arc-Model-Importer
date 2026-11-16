"""U2 G4: control schema, imported-look reset, look presets, sync timeout, Retry, API, and
the framed lighting world / compositor graphs. Runs inside Blender (bpy)."""
import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

import bpy

ROOT = Path(os.environ.get("DATARAIDERS_TEST_REPOSITORY", Path(__file__).resolve().parents[1]))
PKG = "_dr_probe_u2_g4"


def _load(name):
    for pkg, path in ((PKG, ROOT), (PKG + ".Shared", ROOT / "Shared"),
                      (PKG + ".Lighting", ROOT / "Lighting")):
        if pkg not in sys.modules:
            mod = types.ModuleType(pkg)
            mod.__path__ = [str(path)]
            sys.modules[pkg] = mod
    return importlib.import_module(PKG + "." + name)


def _look_material(name="G4TestLook"):
    tree = bpy.data.node_groups.new("Arc G4 Test Look", "ShaderNodeTree")
    colour = tree.interface.new_panel("Colour")
    weather = tree.interface.new_panel("Weathering")
    tint = tree.interface.new_socket("Tint", in_out="INPUT", socket_type="NodeSocketColor", parent=colour)
    tint.default_value = (1.0, 1.0, 1.0, 1.0)
    wear = tree.interface.new_socket("Wear", in_out="INPUT", socket_type="NodeSocketFloat", parent=weather)
    wear.min_value, wear.max_value, wear.default_value = 0.0, 1.0, 0.0
    tree.interface.new_socket("Dirt", in_out="INPUT", socket_type="NodeSocketFloat", parent=weather)
    tree.interface.new_socket("Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    node = mat.node_tree.nodes.new("ShaderNodeGroup")
    node.node_tree = tree
    node.inputs["Tint"].default_value = (0.2, 0.4, 0.6, 1.0)
    node.inputs["Wear"].default_value = 0.25
    val = mat.node_tree.nodes.new("ShaderNodeValue")
    val.outputs[0].default_value = 0.75
    mat.node_tree.links.new(val.outputs[0], node.inputs["Dirt"])
    return mat, tree, node, val


def _row(rows, socket):
    return next(r for r in rows if r["socket"] == socket)


class LookPresetsTest(unittest.TestCase):
    def test_roundtrip_and_suffix_match(self):
        lp = _load("Shared.look_presets")
        data = lp.build("Mine", {"Body.001": {"Wear": 0.5, "Tint": (0.1, 0.2, 0.3, 1.0)}})
        self.assertEqual(data["materials"]["Body"]["Tint"], [0.1, 0.2, 0.3, 1.0])
        with tempfile.TemporaryDirectory() as d:
            path = lp.save(lp.preset_path(d, "Mine"), data)
            back = lp.load(path)
            self.assertEqual(lp.values_for(back, "Body.004")["Wear"], 0.5)
            self.assertEqual([n for n, _ in lp.list_presets(d)], ["Mine"])
            bad = os.path.join(d, "bad.json")
            with open(bad, "w", encoding="utf-8") as f:
                f.write("[1]")
            with self.assertRaisesRegex(ValueError, "not a look preset"):
                lp.load(bad)


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.uc = _load("Shared.materials.user_controls")
        self.mat, self.tree, self.node, self.val = _look_material()

    def tearDown(self):
        bpy.data.materials.remove(self.mat)
        bpy.data.node_groups.remove(self.tree)

    def test_rows_panels_and_linked_value(self):
        rows = self.uc.schema(self.mat)
        self.assertEqual([r["socket"] for r in rows], ["Tint", "Wear", "Dirt"])
        self.assertEqual([r["panel"] for r in rows], ["Colour", "Weathering", "Weathering"])
        self.assertEqual(_row(rows, "Tint")["type"], "COLOR")
        self.assertAlmostEqual(_row(rows, "Dirt")["value"], 0.75)
        self.assertAlmostEqual(_row(rows, "Wear")["max"], 1.0)

    def test_capture_set_reset(self):
        self.assertEqual(self.uc.capture_import_defaults(self.mat), 1)
        self.assertIn(self.uc.IMPORT_DEFAULTS_KEY, self.node)
        self.assertEqual(self.uc.capture_import_defaults(self.mat), 0)
        wear = _row(self.uc.schema(self.mat), "Wear")["id"]
        dirt = _row(self.uc.schema(self.mat), "Dirt")["id"]
        self.assertTrue(self.uc.schema_set(self.mat, wear, 0.9))
        self.assertTrue(self.uc.schema_set(self.mat, dirt, 0.1))
        self.assertAlmostEqual(self.val.outputs[0].default_value, 0.1, places=5)
        rows = self.uc.schema(self.mat)
        self.assertAlmostEqual(_row(rows, "Wear")["imported"], 0.25, places=5)
        self.assertAlmostEqual(_row(rows, "Wear")["value"], 0.9, places=5)
        self.uc.schema_reset(self.mat, ids={wear})
        self.assertAlmostEqual(self.node.inputs["Wear"].default_value, 0.25, places=5)
        self.assertAlmostEqual(self.val.outputs[0].default_value, 0.1, places=5)
        self.uc.schema_reset(self.mat)
        self.assertAlmostEqual(self.val.outputs[0].default_value, 0.75, places=5)

    def test_api_set_and_reset(self):
        api = _load("Shared.api")
        mesh = bpy.data.meshes.new("G4TestMesh")
        ob = bpy.data.objects.new("G4TestObj", mesh)
        ob.data.materials.append(self.mat)
        try:
            self.uc.capture_import_defaults(self.mat)
            self.assertIn(self.mat.name, api.get_controls(ob))
            self.assertGreaterEqual(api.set_control(ob, "Wear", 0.6), 1)
            self.assertAlmostEqual(self.node.inputs["Wear"].default_value, 0.6, places=5)
            with self.assertRaises(KeyError):
                api.set_control(ob, "No Such Control", 1.0)
            api.reset_look(ob)
            self.assertAlmostEqual(self.node.inputs["Wear"].default_value, 0.25, places=5)
        finally:
            bpy.data.objects.remove(ob)
            bpy.data.meshes.remove(mesh)


class SyncStateTest(unittest.TestCase):
    def test_timeout_clock_and_retry(self):
        sco = _load("Shared.shader_control_ops")
        sco.clear_sync_clock()
        self.assertEqual(sco.sync_elapsed("A", now=10.0), 0.0)
        self.assertFalse(sco.sync_timed_out("A", now=11.0))
        self.assertTrue(sco.sync_timed_out("A", now=10.0 + sco.SYNC_TIMEOUT + 0.1))
        self.assertEqual(sco.sync_elapsed("B", now=20.0), 0.0)  # new target restarts the clock
        sco.clear_sync_clock()
        self.assertEqual(sco.sync_elapsed("B", now=30.0), 0.0)
        self.assertEqual(sco.ARC_OT_shader_control_retry.bl_idname, "arc.shader_control_retry")
        self.assertEqual(sco.ARC_OT_shader_control_retry.bl_label, "Retry")


class LightingFramesTest(unittest.TestCase):
    def test_compositor_sections(self):
        post = _load("Lighting.lighting_post")
        tree = bpy.data.node_groups.new("G4TestStack", "CompositorNodeTree")
        try:
            for name in ("ArcMeterLog2", "ArcExposureBias", "ArcLightingBloom", "ArcVignetteMask",
                         "ArcLightingGraded", "ArcLighting3DLUT", "ArcLightingRenderLayers"):
                n = tree.nodes.new("ShaderNodeValue")
                n.name = n.label = name
            counts = post.frame_stack(tree)
            self.assertEqual(counts, {"Exposure": 2, "Bloom": 1, "Vignette": 1, "Tonemap/LUT": 1,
                                      post.LUT_FRAME_LABEL: 1})
            lut = tree.nodes["ArcLighting3DLUT"]
            self.assertEqual(lut.parent.label, "Game tonemap (do not edit)")
            self.assertEqual(lut.parent.parent.label, "Tonemap/LUT")
            self.assertIsNone(tree.nodes["ArcLightingRenderLayers"].parent)
            self.assertEqual(post._find(tree.nodes, "ArcLightingBloom"), tree.nodes["ArcLightingBloom"])
        finally:
            bpy.data.node_groups.remove(tree)

    def test_world_sections(self):
        sky = _load("Lighting.lighting_sky")
        world = bpy.data.worlds.new("G4TestWorld")
        world.use_nodes = True
        try:
            nt = world.node_tree
            for name in ("ArcSkyRotation", "ArcSkyAtmosphereScaled", "ArcSkyBackground"):
                n = nt.nodes.new("ShaderNodeValue")
                n.name = n.label = name
            counts = sky.frame_world(nt)
            self.assertEqual(counts, {"Sun": 1, "Atmosphere": 1, "Sky": 1})
            self.assertEqual(nt.nodes["ArcSkyRotation"].parent.label, "Sun")
        finally:
            bpy.data.worlds.remove(world)


if __name__ == "__main__":
    unittest.main()
