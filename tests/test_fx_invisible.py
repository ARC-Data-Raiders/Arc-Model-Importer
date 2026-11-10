"""M_FX_MeshRadialBlur builds an invisible surface (no Blender).

Its compiled additive base pass (shader map 0921C5BD, PS 317636DA) outputs only the editor SelectionColor, black in
game, with alpha 0; the visible radial blur is motion blur from its world-position-offset velocity.
"""
import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import test_glass_opaque as glass_test  # noqa: E402  (registers the _sr_glass_pkg loader package)

spec = importlib.util.spec_from_file_location('_sr_glass_pkg.fx_invisible', ROOT / 'Shared/materials/rebuild/fx_invisible.py')
fx = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fx
spec.loader.exec_module(fx)
MASTER = 'PioneerGame/Content/Pioneer/Effects/Materials/Impacts/M_FX_MeshRadialBlur'


class AlphaGraph(glass_test.NumericGraph):
    def finish(self, color, roughness=.5, metallic=0, normal=None, specular=.5, emission=None, alpha=1, **_):
        super().finish(color, roughness, metallic, normal, specular, emission)
        self.result['alpha'] = alpha


class FxInvisibleTests(unittest.TestCase):
    def test_radial_blur_is_transparent_black_without_textures(self):
        g = AlphaGraph({})
        fx.build_fx_invisible(g, {'master': MASTER, 'scalars': {}, 'colors': {}, 'switches': {}, 'textures': {},
                                  'settings': {'BlendMode': 3, 'ShadingModel': 0}})
        self.assertEqual(g.result['alpha'], 0.)
        self.assertEqual(g.result['color'], (0., 0., 0.))
        self.assertIsNone(g.result['emission'])
        self.assertEqual(g.requested, [])
        self.assertTrue(any('motion blur' in w for w in g.report['warnings']))

    def test_master_routes_to_the_invisible_family(self):
        tree = ast.parse((ROOT / 'Shared/materials/rebuild/records.py').read_text(encoding='utf-8'))
        body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'classify']
        package = types.ModuleType('_fxinv_pkg'); package.__path__ = []
        sys.modules['_fxinv_pkg'] = package
        sys.modules['_fxinv_pkg.head_records'] = SimpleNamespace(family=lambda record: None)
        try:
            ns = {'__name__': '_fxinv_pkg.records', '__package__': '_fxinv_pkg'}
            exec(compile(ast.Module(body=body, type_ignores=[]), '<records>', 'exec'), ns)
            self.assertEqual(ns['classify']({'master': MASTER, 'colors': {}, 'textures': {}}), 'fx_invisible')
        finally:
            sys.modules.pop('_fxinv_pkg.head_records', None); sys.modules.pop('_fxinv_pkg', None)


if __name__ == '__main__':
    unittest.main()
