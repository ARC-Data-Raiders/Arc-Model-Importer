"""prop-audit R7: unresolved material ref causes, rare_master severity, Large Movement is WPO only."""
import importlib.util
import os
import unittest

_P = os.path.join(os.path.dirname(__file__), '..', '..', 'Shared', 'materials', 'audit_param_rules.py')
_spec = importlib.util.spec_from_file_location('_r7_rules', _P)
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)


class R7RefClasses(unittest.TestCase):
    def test_unresolved_ref_causes(self):
        cases = {
            '/Game/Pioneer/Maps/TheDam_01/L_TheDam_01/L_TheDam_01_x3_y3_NatureLOD/M_PROXY_L_TheDam_01_x3_y3.M_PROXY_L':
                ('ref_hlod_proxy', R.INFO),
            '/Game/Pioneer/Maps/Spaceport_01/Gameplay/POI/Spaceport/POI_Spaceport_LOD/M_PROXY_POI_Spaceport.M':
                ('ref_hlod_proxy', R.INFO),
            '/Game/Pioneer/Interactables/Frigate/Art/SM_Frigate_Merged_Collision.SM_Frigate_Merged_Collision':
                ('ref_not_material', R.INFO),
            'RemappedPlugins/EmbarkScript/Content/MaterialLibrary/PostProcess/M_PP_PurkinjeShift.M_PP_PurkinjeShift':
                ('ref_post_process', R.INFO),
            '/Game/Pioneer/Maps/Spaceport_01/BackdropMesh/MI_BuriedCityBackdrop_01.MI_BuriedCityBackdrop_01':
                ('ref_missing_package', R.INCONSISTENCY),
            '/Game/Pioneer/Env/M_PROXY_Loose.M_PROXY_Loose': ('ref_missing_package', R.INCONSISTENCY),
        }
        for path, want in cases.items():
            self.assertEqual(R.unresolved_ref_class(path), want, path)
            self.assertEqual(R.severity(want[0] + ':x'), want[1])

    def test_rare_master_stays_gated(self):
        self.assertEqual(R.severity('rare_master:M_Something'), R.INCONSISTENCY)

    def test_large_movement_render_only(self):
        self.assertEqual(R.switch_class('Large Movement'), ('render_only', R.INFO))
        self.assertEqual(R.switch_class('Use Heat'), ('runtime_input', R.INFO))


if __name__ == '__main__':
    unittest.main()
