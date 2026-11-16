import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
common = importlib.import_module(ROOT.name + ".Shared.materials.common")


class DemandTextureTests(unittest.TestCase):
    def setUp(self):
        common.clear_material_session_caches()
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "sample_CR.png")
        image = bpy.data.images.new("Demand fixture", width=2, height=2)
        image.filepath_raw = self.path
        image.file_format = "PNG"
        image.save()
        bpy.data.images.remove(image)
        self.before = set(bpy.data.images)
        self.material = bpy.data.materials.new("Demand fixture")
        self.material.use_nodes = True

    def tearDown(self):
        bpy.data.materials.remove(self.material)
        for image in set(bpy.data.images) - self.before:
            bpy.data.images.remove(image)
        self.directory.cleanup()
        common.clear_material_session_caches()

    def test_inventory_and_missing_search_do_not_load_images(self):
        with patch.object(common, "_resolve_mi_texture_path", return_value=self.path):
            inventory = common._tex_lookup_from_flat_mi({"textures": [("BaseColor", "/Game/Color")]})
        self.assertEqual(set(bpy.data.images), self.before)
        self.assertEqual(common._find_flat_tex(inventory, "missing"), (None, None))
        self.assertEqual(set(bpy.data.images), self.before)
        self.assertIsNotNone(common._find_flat_tex(inventory, "_cr")[1])

    def test_first_role_adopts_unused_neutral_buffer_but_other_roles_isolate(self):
        neutral = common._load_image_cached(self.path)
        color = common._shader_role_image(neutral, non_color=False)
        self.assertEqual(neutral, color)
        data = common._shader_role_image(neutral, non_color=True)
        self.assertNotEqual(color, data)
        self.assertEqual(color.colorspace_settings.name, "sRGB")

    def test_no_unused_inspection_nodes(self):
        nodes = self.material.node_tree.nodes
        before = len(nodes)
        common._dump_unconnected_tex(nodes, {"Unused": (self.path, None)}, set(), 0, 0)
        self.assertEqual(len(nodes), before)

    def test_material_cleanup_preserves_existing_unused_images(self):
        operators = importlib.import_module(ROOT.name + ".Shared.operators")
        existing = bpy.data.images.new("Existing unused", width=1, height=1)
        mesh = bpy.data.meshes.new("Cleanup fixture")
        obj = bpy.data.objects.new("Cleanup fixture", mesh)
        def build(*args, **kwargs):
            bpy.data.images.new("Import unused", width=1, height=1)
            return "fixture"
        try:
            with patch.object(operators, "_apply_materials_to_object_impl", side_effect=build), patch.object(operators, "_isolate_material_controls"):
                self.assertEqual(operators.apply_materials_to_object(obj, ""), "fixture")
            self.assertIn(existing.name, bpy.data.images)
            self.assertNotIn("Import unused", bpy.data.images)
        finally:
            bpy.data.objects.remove(obj)
            bpy.data.meshes.remove(mesh)

    def test_native_sampling_cache_keeps_data_linear_after_srgb_promotion(self):
        Graph = importlib.import_module(ROOT.name + ".Shared.materials.native.interop").Graph
        target = self.path
        class Index:
            def texture_info(self, reference):
                return {"SRGB": True}
            def texture(self, reference, layer=None):
                return target
        graph = Graph(self.material, None, Index(), {})
        color = graph.image("/Game/Fixture", srgb=True)
        data = graph.image("/Game/Fixture", srgb=False)
        self.assertEqual(color["node"].interpolation, "Cubic")
        self.assertEqual(data["node"].interpolation, "Linear")
        self.assertNotEqual(color["node"], data["node"])
        self.assertEqual(graph.image("/Game/Fixture", srgb=True)["node"], color["node"])

    def test_face_sampler_exposes_explicit_albedo_role(self):
        ResponseGraph = importlib.import_module(ROOT.name + ".Shared.materials.character_body").ResponseGraph
        image = common._load_image_cached(self.path)
        graph = ResponseGraph(self.material.node_tree.nodes, self.material.node_tree.links)
        uv = graph.node("ShaderNodeUVMap", "UV0").outputs[0]
        self.assertEqual(graph.texture(image, uv, "BaseColor", albedo=True).interpolation, "Cubic")
        self.assertEqual(graph.texture(image, uv, "Normal").interpolation, "Linear")

    def test_albedo_is_cubic_without_changing_normal_sampling(self):
        image = common._load_image_cached(self.path)
        color = common._new_tex_image(self.material.node_tree.nodes, image, "BaseColor", (0, 0))
        normal = common._new_tex_image(self.material.node_tree.nodes, image, "Normal", (0, 0), non_color=True, normal=True)
        self.assertEqual(color.interpolation, "Cubic")
        self.assertEqual(normal.interpolation, "Linear")
