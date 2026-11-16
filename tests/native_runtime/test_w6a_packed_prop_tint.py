"""W6a: packed-prop tint follows MI switches/swatches; never the unset master Tint default (0,0,1) (no Blender)."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAMES = ('_packed_prop_tint_fallback',)
BLUE = (0.0, 0.0, 1.0, 1.0)  # M_PropPreset_CR+NOM / CR+NOX+XEM master Tint default 0000FF


def mi_switch(switches, *names, default=None):
    for name in names:
        if name in switches:
            return bool(switches[name])
    return default


def mi_colour(colours, *names, default=None):
    lower = {n.lower() for n in names}
    for param, rgba in colours or []:
        if param.lower() in lower:
            return rgba
    return default


def load():
    tree = ast.parse((ROOT / 'Shared/materials/environment.py').read_text(encoding='utf-8'))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in NAMES]
    body += [n for n in tree.body if isinstance(n, ast.Assign) and any(
        getattr(t, 'id', '') == '_PACKED_PROP_DEFAULT_TINT' for t in n.targets)]
    ns = {'_mi_switch': mi_switch, '_mi_colour': mi_colour}
    exec(compile(ast.Module(body=body, type_ignores=[]), '<environment>', 'exec'), ns)
    return ns


def mi(switches, colours=None):
    return {'switches': switches, 'colours': [('Tint', BLUE)] + list(colours or [])}


class PackedPropTintFallback(unittest.TestCase):
    def setUp(self):
        self.assertIn('_packed_prop_tint_fallback', load(), 'helper missing')
        self.fn = load()['_packed_prop_tint_fallback']

    def test_swatch_mis_use_swatch_not_raw_blue(self):
        # MI_Burgerman_01 / MI_Wrh_Chair_01_Green (CR+NOM): Use Swatch Colors, Tint unset 0000FF.
        burger = mi({'Use Tinting': True, 'Use Swatch Colors': True, 'Use Slice Mask': False})
        self.assertEqual(self.fn(burger), ('swatch', None))

    def test_xem_packed_tint_mask_uses_slots(self):
        # MI_Rsr_Cabinet_S_01_A_Blue: Use single Mask Texture + Use Packed Tint Mask, swatches 4/5/5.
        cab = mi({'Use Tinting': True, 'Use Packed Tint Mask': True, 'Use Swatch Colors': True,
                  'Use single Mask Texture': True})
        route, plan = self.fn(cab)
        self.assertEqual(route, 'slots')
        self.assertEqual(plan, {'slots': 3, 'weights': 'slice', 'source': 'Mask',
                                'colour': ('swatch', 'swatch', 'swatch')})
        # MI_TrolleyWelding_01_A_Blue: Override Swatch (1) -> Tint(1) 8A9DCB on slot 3, Packed Mask slices.
        trolley = mi({'Use Tinting': True, 'Use Packed Tint Mask': True, 'Use Swatch Colors': True,
                      'Override Swatch (1)': True})
        route, plan = self.fn(trolley)
        self.assertEqual(route, 'slots')
        self.assertEqual(plan['source'], 'Packed Mask')
        self.assertEqual(plan['colour'], ('swatch', 'swatch', 'tint'))

    def test_unset_default_tint_is_skipped(self):
        self.assertEqual(self.fn(mi({'Use Tinting': True})), (None, None))

    def test_authored_tint_kept(self):
        # MI_NetTennisCable: authored Tint (0, 0.188, 1), no swatch, no mask.
        net = {'switches': {'Use Tinting': True, 'UseOverlay': True},
               'colours': [('Tint', (0.0, 0.188, 1.0, 1.0))]}
        self.assertEqual(self.fn(net), ('tint', None))


if __name__ == '__main__':
    unittest.main()
