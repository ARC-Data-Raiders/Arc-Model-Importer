"""W6b: M_BlockoutPreset MIs whose stem carries a shade token "_Light_" are blockout, not emissive."""
import unittest
try:
    from .test_r17_builder_gaps import TranslucentAtlasRouting
except ImportError:
    from test_r17_builder_gaps import TranslucentAtlasRouting

BLK = '/Game/Pioneer/MaterialLibrary/MI_Generic/Blockout/'


class BlockoutLightShadeRouting(unittest.TestCase):
    def test_blockout_light_shade_not_emissive(self):
        ns = TranslucentAtlasRouting.classify_ns()
        # MI_Blockout_Metal_Light_Gray: Parent MI_Blockout_Metal_Light_Base, root M_BlockoutPreset, BaseColor 828282.
        mi = {'parent': BLK + 'MI_Blockout_Metal_Light_Base.MI_Blockout_Metal_Light_Base',
              'root_master': 'M_BlockoutPreset', 'textures': []}
        self.assertNotEqual(ns['classify_mi_family'](mi, 'mi_blockout_metal_light_gray'), ns['FAMILY_EMISSIVE'])
        base = {'parent': BLK + 'MI_Blockout_DamageableBase.MI_Blockout_DamageableBase',
                'root_master': 'M_BlockoutPreset', 'textures': []}
        self.assertNotEqual(ns['classify_mi_family'](base, 'mi_blockout_softrubber_light_base'), ns['FAMILY_EMISSIVE'])
        direct = {'parent': '/Game/Pioneer/MaterialLibrary/M_Presets/M_BlockoutPreset.M_BlockoutPreset', 'textures': []}
        self.assertNotEqual(ns['classify_mi_family'](direct, 'mi_blockout_metal_light_base'), ns['FAMILY_EMISSIVE'])

    def test_real_light_mis_stay_emissive(self):
        ns = TranslucentAtlasRouting.classify_ns()
        light = {'parent': '/Game/Pioneer/MaterialLibrary/M_MasterLight_Emissive_01.M_MasterLight_Emissive_01', 'textures': []}
        self.assertEqual(ns['classify_mi_family'](light, 'mi_weapon_emissive_light_01'), ns['FAMILY_EMISSIVE'])
        # Blockout glow is a separate master; its emissive stems keep the light route.
        glow = {'parent': '/Game/Pioneer/MaterialLibrary/M_Presets/M_BlockoutPreset_Glow.M_BlockoutPreset_Glow',
                'root_master': 'M_BlockoutPreset_Glow', 'textures': []}
        self.assertEqual(ns['classify_mi_family'](glow, 'mi_blockout_glow_light_01'), ns['FAMILY_EMISSIVE'])


if __name__ == '__main__':
    unittest.main()
