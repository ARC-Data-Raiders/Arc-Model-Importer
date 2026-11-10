"""Enabled singleton color arrays resolve from exact native depth, not image color."""
import ast
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import tempfile
import unittest


def resolver():
    source = Path(__file__).resolve().parents[1] / 'Shared/materials/clothing.py'
    module = ast.parse(source.read_text(encoding='utf-8'))
    function = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == '_find_colortex_slice_png')
    def find_slice(paths, index):
        for path in paths:
            match = re.search(r'_(?:LAYER_?)?(\d+)$', Path(path).stem, re.I)
            if match and int(match.group(1)) == index:
                return path
        return ''
    namespace = dict(os=os, re=re, json=json, textures=SimpleNamespace(find_slice_png=find_slice))
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[function.name]


class SingletonColorArrayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.resolve = resolver()

    def bank(self, name='TA_Example_Colors', depth=1, layers=(0,), metadata=True, kind='Texture2DArray'):
        paths = []
        for layer in layers:
            path = self.folder / f'{name}_LAYER{layer}.png'
            path.write_bytes(b'content irrelevant to resolution')
            paths.append(str(path))
        if metadata:
            (self.folder / f'{name}.arc-texture.json').write_text(json.dumps(dict(Type=kind, Name=name, Mips=[dict(SizeZ=depth)])))
        return paths

    def test_native_singleton_selects_authored_layer_zero(self):
        paths = self.bank()
        self.assertEqual(self.resolve([paths], 1), paths[0])
        self.assertEqual(self.resolve([paths], 12), paths[0])

    def test_disabled_id_remains_disabled(self):
        self.assertEqual(self.resolve([self.bank()], 0), '')

    def test_sparse_export_is_not_assumed_singleton(self):
        self.assertEqual(self.resolve([self.bank(depth=8)], 1), '')
        self.assertEqual(self.resolve([self.bank(metadata=False)], 1), '')

    def test_non_color_or_non_array_cannot_supply_singleton(self):
        self.assertEqual(self.resolve([self.bank(name='TA_Example_Normals')], 1), '')
        self.assertEqual(self.resolve([self.bank(kind='Texture2D')], 1), '')

    def test_existing_multilayer_indices_remain_unchanged(self):
        paths = self.bank(depth=3, layers=(0, 1, 2))
        self.assertEqual(self.resolve([paths], 1), paths[1])
        self.assertEqual(self.resolve([paths], 2), paths[2])
        self.assertEqual(self.resolve([paths], 3), '')

    def test_missing_file_or_mismatched_metadata_is_rejected(self):
        paths = self.bank()
        Path(paths[0]).unlink()
        self.assertEqual(self.resolve([paths], 1), '')
        paths = self.bank()
        sidecar = self.folder / 'TA_Example_Colors.arc-texture.json'
        data = json.loads(sidecar.read_text());data['Name'] = 'TA_Other_Colors';sidecar.write_text(json.dumps(data))
        self.assertEqual(self.resolve([paths], 1), '')


if __name__ == '__main__':
    unittest.main()
