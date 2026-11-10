"""M_PropPreset_CR+NOX+XEM mask plan, swatch texel and preset detection follow the decoded SM5 base passes (no Blender)."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('_xem_prop_mask_plan', '_swatch_atlas_uv', '_is_xem_prop_preset', '_is_skinned_prop_preset')


def mi_switch(switches, *names, default=None):
    for name in names:
        if name in switches:
            return bool(switches[name])
    return default


def load():
    tree = ast.parse((ROOT / 'Shared/materials/environment.py').read_text(encoding='utf-8'))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in NAMES]
    body += [n for n in tree.body if isinstance(n, ast.Assign) and any(
        getattr(t, 'id', '') in ('_XEM_PROP_PRESET', '_SKINNED_PROP_PRESET') for t in n.targets)]
    ns = {'_mi_switch': mi_switch}
    exec(compile(ast.Module(body=body, type_ignores=[]), '<environment>', 'exec'), ns)
    return ns


class XemPropMaskPlanTests(unittest.TestCase):
    ns = load()

    def plan(self, **switches):
        return self.ns['_xem_prop_mask_plan']({k.replace('_', ' '): v for k, v in switches.items()})

    def test_masks_off_reads_no_metal_and_ignores_tint(self):
        # MI_SupplyStationColony_01_BagsStraps_A / MI_SeismometerGraph_01: o2.x = 0, o3 = CR.
        self.assertEqual(self.plan(Use_Masks=False, Use_Tinting=True),
                         {'texture': None, 'metal': None, 'tint': None})

    def test_packed_mask_metal_is_blue_whatever_its_suffix(self):
        # MI_WeatherMonitor_WindDirection_01_A binds an _XAM packed mask: metallic = sat(B x Metalness Value).
        self.assertEqual(self.plan(Use_Masks=True, Use_Slice_Mask=True),
                         {'texture': 'Packed Mask', 'metal': 'Blue', 'tint': 'Red'})

    def test_packed_tint_weight_is_red_even_without_slice_mask(self):
        # MI_Res_Pots: Use Slice Mask off still lerps CR -> CR x Tint x 2 by Packed Mask.R.
        self.assertEqual(self.plan(Use_Masks=True, Use_Slice_Mask=False, Use_Tinting=True)['tint'], 'Red')

    def test_single_mask_is_tint_weight_while_tinting(self):
        # MI_DisplayFridge_01_A: o2.x = 0 and Mask.R weights the tint.
        self.assertEqual(self.plan(Use_Masks=True, Use_single_Mask_Texture=True, Use_Tinting=True),
                         {'texture': 'Mask', 'metal': None, 'tint': 'Red'})

    def test_single_mask_is_metal_without_tinting(self):
        # MI_Vent_03_Details_01_A: o2.x = sat(Mask.R x Metalness Value).
        self.assertEqual(self.plan(Use_Masks=True, Use_single_Mask_Texture=True, Use_Tinting=False),
                         {'texture': 'Mask', 'metal': 'Red', 'tint': None})

    def test_metalness_as_tint_mask_tints_the_non_metal(self):
        # MI_Truck_01_A (single) and MI_DiscoBall_01_A (packed): lerp(CR, CR x Tint x 2, 1 - metal).
        truck = self.plan(Use_Masks=True, Use_single_Mask_Texture=True, Use_Tinting=True,
                          Use_Metalness_as_tint_mask=True)
        disco = self.plan(Use_Masks=True, Use_Tinting=True, Use_Metalness_as_tint_mask=True)
        self.assertEqual(truck, {'texture': 'Mask', 'metal': 'Red', 'tint': '1-metal'})
        self.assertEqual(disco, {'texture': 'Packed Mask', 'metal': 'Blue', 'tint': '1-metal'})


class SwatchTexelTests(unittest.TestCase):
    ns = load()

    def test_yellow_sofa_reads_row_zero_band_one(self):
        # MI_Gvt_Sofa_03_A_Yellow: Swatch Color 1, Swatch Curve default 0 on the 256 x 31 CA_Prop_Colors atlas.
        u, v = self.ns['_swatch_atlas_uv'](1.0, 0.0, 256, 31)
        self.assertAlmostEqual(u * 256 - 0.5, 14.025, places=6)
        self.assertAlmostEqual(v, 0.5 / 31, places=9)

    def test_blue_curtain_reads_band_four(self):
        # MI_DividerCourtain_01_A_Blue: Swatch Color 4 -> texel 91.0, the blue band of row 0.
        u, _ = self.ns['_swatch_atlas_uv'](4.0, 0.0, 256, 31)
        self.assertAlmostEqual(u * 256 - 0.5, 90.525, places=6)


class XemPresetDetectionTests(unittest.TestCase):
    ns = load()

    def test_resolved_key_and_parentless_use_masks(self):
        detect = self.ns['_is_xem_prop_preset']
        self.assertTrue(detect({'preset_key': 'M_PropPreset_CR+NOX+XEM', 'parent': 'x'}))
        self.assertTrue(detect({'preset_key': '/Game/Pioneer/MaterialLibrary/M_Presets/M_PropPreset_CR+NOX+XEM.M_PropPreset_CR+NOX+XEM'}))
        self.assertTrue(detect({'switches': {'Use Masks': True}}))
        self.assertFalse(detect({'preset_key': 'M_PropPreset_CR+NOM', 'parent': 'M_PropPreset_CR+NOM'}))
        self.assertFalse(detect({'parent': 'M_PropPreset_CR+NOM', 'switches': {'Use Masks': True}}))

    def test_parent_path_and_colourway_root_master(self):
        # MI_Gvt_Sofa_03_A_Yellow names the master by path and carries no merged preset key.
        detect = self.ns['_is_xem_prop_preset']
        self.assertTrue(detect({'parent': '/Game/Pioneer/MaterialLibrary/M_Presets/M_PropPreset_CR+NOX+XEM.0'}))
        self.assertTrue(detect({'parent': '/Game/Pioneer/Environment/Props/X/MI_X_Blue_A',
                                'root_master': 'M_PropPreset_CR+NOX+XEM'}))
        self.assertFalse(detect({'parent': '/Game/Pioneer/Environment/Props/X/MI_X_Blue_A',
                                 'root_master': 'M_PropPreset_CR+NOM'}))
        self.assertFalse(detect({'parent': '/Game/Pioneer/Environment/Props/X/MI_X_Blue_A',
                                 'switches': {'Use Masks': True}}))


class SkinnedPresetDetectionTests(unittest.TestCase):
    ns = load()

    def test_skinned_preset_keeps_its_propdust_stage(self):
        # MI_Barricade_01_A / MI_Backpack_Alpha: the Skinned master references T_Propdust_01_X and its breakup.
        detect = self.ns['_is_skinned_prop_preset']
        self.assertTrue(detect({'parent': '/Game/Pioneer/MaterialLibrary/M_Presets/M_PropPreset_CR+NOM+Skinned.0'}))
        self.assertTrue(detect({'preset_key': 'M_PropPreset_CR+NOM+Skinned'}))
        self.assertTrue(detect({'parent': '/Game/Pioneer/Environment/Props/X/MI_X_Blue_A',
                                'root_master': 'M_PropPreset_CR+NOM+Skinned'}))

    def test_decoded_dust_masters_are_not_skinned(self):
        detect = self.ns['_is_skinned_prop_preset']
        self.assertFalse(detect({'parent': '/Game/Pioneer/MaterialLibrary/M_Presets/M_PropPreset_CR+NOM.0'}))
        self.assertFalse(detect({'parent': '/Game/Pioneer/MaterialLibrary/M_Presets/M_PropPreset_CR+NOX+XEM.0'}))
        self.assertFalse(detect({'switches': {'Use Masks': True}}))


if __name__ == '__main__':
    unittest.main()
