"""prop-audit E1: masters behind stale fallback_family/unknown_master ledger rows keep their HEAD routes.

Each case keys on root master or texture contract read from mi_params.jsonl; no prop or MI names.
"""
try:
    from .fake_graph import rebuild
    from .test_generic_families import rec
except ImportError:
    from fake_graph import rebuild
    from test_generic_families import rec
import unittest

ENV = '/Game/Pioneer/MaterialLibrary/'
ROUTES = {
    'broken_glass': [rec(ENV + 'M_BrokenGlassSDF', ['NDD', 'NC'])],
    'concrete_trim': [rec(ENV + 'M_ConcreteTrim_UXR', ['Trim sheet', 'CH'])],
    'graphic_atlas': [rec(ENV + 'M_Graphic_Atlas_01', ['Graphic Atlas'])],
    'snow_pile_basic': [rec(ENV + 'M_SnowPile_01', ['BaseTextrue'])],
    'rusty_trim': [rec(ENV + 'M_TrimPreset_Rusty', ['Base Texture', 'HolesNXX'])],
    'nr_vpo_detail': [rec(ENV + 'M_PropPreset_NR+VPO+Detail', ['Variation Mask', 'LargeScaleVariation'])],
    'blockout': [rec(ENV + 'M_BlockoutPreset', [])],
    'vt_blend': [rec(ENV + 'M_Tarmac_02', [])],
    # Masters whose MIs expose no texture parameter at all (root_defaults, tex == []).
    'parameterless': [rec(ENV + b, []) for b in ('M_DebugPreset_Grid_1m', 'M_FakeSkylight_01_A',
                                                  'M_Res_TrimsTransparent_01_NRA', 'M_RivenTideLOD_01')],
}


class E1StaleFallbackRoutes(unittest.TestCase):
    def test_routes(self):
        r = rebuild('records')
        for family, recs in ROUTES.items():
            for x in recs:
                with self.subTest(master=x['master']):
                    self.assertEqual(r.classify(x), family)

    def test_no_generic_fallback(self):
        r = rebuild('records')
        for recs in ROUTES.values():
            for x in recs:
                with self.subTest(master=x['master']):
                    self.assertNotIn(r.classify(x), ('fallback', ''))


if __name__ == '__main__':
    unittest.main()
