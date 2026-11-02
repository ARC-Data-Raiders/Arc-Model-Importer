"""Generic packed surfaces never texture through a cloth VAT lookup row (no Blender).

SM_WhiteFlag_01_A_VAT: UV1 collapses to one point on the pole and to a v = 1 strip on the cloth; the cloth-sim item
masters default Use UV1 = True, yet MI_WhiteFlag_01_A's compiled base pass samples CR and NAM on mesh UV0.
"""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load():
    tree = ast.parse((ROOT / 'surface_rebuild/surfaces.py').read_text(encoding='utf-8'))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'collapsed_uv']
    ns = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), '<surfaces>', 'exec'), ns)
    return ns['collapsed_uv']


class Loops(list):
    def foreach_get(self, attr, out):
        out[:] = [value for uv in self for value in uv]


def mesh(*layers):
    return SimpleNamespace(data=SimpleNamespace(uv_layers=[SimpleNamespace(data=Loops(layer)) for layer in layers]))


LAYOUT = [(.0, .0), (.82, .1), (.5, .9), (1., 1.)]


class SurfaceUvRouteTests(unittest.TestCase):
    def test_vat_rows_and_points_are_collapsed(self):
        collapsed = load()
        self.assertTrue(collapsed(mesh(LAYOUT, [(0., 1.)] * 4), 1))
        self.assertTrue(collapsed(mesh(LAYOUT, [(.002, 1.), (.5, 1.), (.998, 1.)]), 1))
        self.assertFalse(collapsed(mesh(LAYOUT, LAYOUT), 1))

    def test_missing_layers_and_objects_are_left_to_the_uv_clamp(self):
        collapsed = load()
        self.assertFalse(collapsed(mesh(LAYOUT), 1))
        self.assertFalse(collapsed(None, 1))
        self.assertFalse(collapsed(SimpleNamespace(), 1))

    def test_cloth_sim_masters_ignore_use_uv1(self):
        source = (ROOT / 'surface_rebuild/surfaces.py').read_text(encoding='utf-8')
        self.assertIn("use_uv1 = sw.get('Use UV1') and 'clothsim' not in master", source)
        self.assertLess(source.index('use_uv1 = '), source.index('uv = g.transform_uv(uv_channel'))


if __name__ == '__main__':
    unittest.main()
