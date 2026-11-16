"""Reference cards are created hidden from render but visible in the viewport."""
import importlib
from pathlib import Path
import sys
import tempfile
import unittest

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent / 'native_runtime'))
from native_test_support import PACKAGE, namespace  # noqa: E402

namespace()
import types  # noqa: E402

_outfit_pkg = PACKAGE + '.Outfit_Accessories'
if _outfit_pkg not in sys.modules:
    _mod = types.ModuleType(_outfit_pkg)
    _mod.__path__ = [str(Path(__file__).resolve().parents[1] / 'Outfit_Accessories')]
    sys.modules[_outfit_pkg] = _mod
_anim_pkg = PACKAGE + '.Animations'
if _anim_pkg not in sys.modules:
    _mod = types.ModuleType(_anim_pkg)
    _mod.__path__ = [str(Path(__file__).resolve().parents[1] / 'Animations')]
    sys.modules[_anim_pkg] = _mod
reference_planes = importlib.import_module(_outfit_pkg + '.reference_planes')


class ReferencePlaneRenderDefault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ref-render-')
        image = bpy.data.images.new('T_UI_Icon_Test', 8, 8, alpha=True)
        image.pixels = [1.0] * (8 * 8 * 4)
        self.path = str(Path(self.tmp.name) / 'T_UI_Icon_Test.png')
        image.filepath_raw = self.path
        image.file_format = 'PNG'
        image.save()
        bpy.data.images.remove(image)

    def tearDown(self):
        for obj in list(bpy.data.objects):
            if obj.get('arc_reference_plane'):
                bpy.data.objects.remove(obj, do_unlink=True)
        self.tmp.cleanup()

    def test_new_reference_card_hidden_from_render(self):
        plane = reference_planes.create_image_plane(
            name='REF_Backpack', image_path=self.path, height_m=0.5,
            location=Vector((0, 0, 0)), reference_role='Backpack',
        )
        self.assertIsNotNone(plane)
        self.assertTrue(plane.hide_render)
        self.assertFalse(plane.hide_viewport)
        self.assertFalse(plane.hide_get())
        self.assertIn(plane.name, bpy.context.view_layer.objects)


if __name__ == '__main__':
    unittest.main()
