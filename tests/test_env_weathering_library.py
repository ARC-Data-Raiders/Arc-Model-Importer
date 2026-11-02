"""Library weathering textures resolve current array-slice exports (no Blender)."""
import ast
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LibrarySliceTests(unittest.TestCase):
    def load(self, files):
        tree = ast.parse((ROOT / 'materials/env_props/weathering.py').read_text(encoding='utf-8'))
        body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'load_library_image']
        ns = {'os': types.SimpleNamespace(path=types.SimpleNamespace(isfile=lambda path: True)),
              '_resolve_mi_texture_path': lambda path, folders: files.get(path, ''),
              '_load_image_cached': lambda path: path}
        exec(compile(ast.Module(body=body, type_ignores=[]), '<weathering>', 'exec'), ns)
        return ns['load_library_image']

    def test_layer_named_slices_resolve(self):
        # UEFormat/ArcPak writes TA_Weathering_Props_01_LAYER0..3; FModel wrote _0..3.
        ref = '/Game/Pioneer/MaterialLibrary/Textures/Props/Detail/TA_Weathering_Props_01'
        load = self.load({ref + '_LAYER2': 'X:/TA_Weathering_Props_01_LAYER2.png', ref + '_1': 'X:/TA_Weathering_Props_01_1.png'})
        self.assertEqual(load(ref, slice_index=2), 'X:/TA_Weathering_Props_01_LAYER2.png')
        self.assertEqual(load(ref, slice_index=1), 'X:/TA_Weathering_Props_01_1.png')
        self.assertIsNone(load(ref, slice_index=3))


if __name__ == '__main__':
    unittest.main()
