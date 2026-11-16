import importlib
from pathlib import Path
import sys
import tempfile
import unittest

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
body = importlib.import_module(ROOT.name + '.Shared.materials.character_body')


class CharacterImageOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.before = set(bpy.data.images)
        self.directory = tempfile.TemporaryDirectory(prefix='character-images-')
        self.path = str(Path(self.directory.name) / 'atlas.png')
        image = bpy.data.images.new('FixtureAtlas', width=2, height=2)
        image.filepath_raw = self.path
        image.file_format = 'PNG'
        image.save()
        bpy.data.images.remove(image)
        self.material = bpy.data.materials.new('ImageOwnership')
        self.material.use_nodes = True
        self.node = self.material.node_tree.nodes.new('ShaderNodeTexImage')

    def tearDown(self):
        bpy.data.materials.remove(self.material)
        for image in list(bpy.data.images):
            if image not in self.before:
                bpy.data.images.remove(image)
        self.directory.cleanup()

    def test_new_noncolor_image_has_no_discarded_color_copy(self):
        image = body.image_for_role(self.path, non_color=True)
        self.node.image = image
        created = set(bpy.data.images) - self.before
        self.assertEqual(len(created), 1)
        self.assertTrue(all(item.users > 0 for item in created))
        self.assertEqual(image.colorspace_settings.name, 'Non-Color')
        self.assertIs(body.image_for_role(self.path, non_color=True), image)

    def test_existing_color_consumer_keeps_its_interpretation(self):
        original = bpy.data.images.load(self.path, check_existing=True)
        self.node.image = original
        image = body.image_for_role(self.path, non_color=True)
        other = self.material.node_tree.nodes.new('ShaderNodeTexImage')
        other.image = image
        self.assertIsNot(image, original)
        self.assertEqual(original.colorspace_settings.name, 'sRGB')
        self.assertEqual(image.colorspace_settings.name, 'Non-Color')
        self.assertTrue(all(item.users > 0 for item in set(bpy.data.images) - self.before))

    def test_replaced_owned_image_is_retired_only_after_last_consumer(self):
        previous = body.image_for_role(self.path, non_color=True)
        self.node.image = previous
        other = self.material.node_tree.nodes.new('ShaderNodeTexImage')
        other.image = previous
        replacement = bpy.data.images.new('Replacement', width=2, height=2)
        name = previous.name
        body.replace_role_image(self.node, replacement)
        self.assertIs(bpy.data.images.get(name), previous)
        body.replace_role_image(other, replacement)
        self.assertIsNone(bpy.data.images.get(name))

    def test_replaced_preexisting_image_is_preserved(self):
        previous = bpy.data.images.load(self.path, check_existing=True)
        self.node.image = previous
        replacement = bpy.data.images.new('Replacement', width=2, height=2)
        body.replace_role_image(self.node, replacement)
        self.assertIn(previous, bpy.data.images.values())

    def test_fake_user_protects_replaced_owned_image(self):
        previous = body.image_for_role(self.path, non_color=True)
        previous.use_fake_user = True
        self.node.image = previous
        replacement = bpy.data.images.new('Replacement', width=2, height=2)
        body.replace_role_image(self.node, replacement)
        self.assertIn(previous, bpy.data.images.values())


if __name__ == '__main__':
    unittest.main()
