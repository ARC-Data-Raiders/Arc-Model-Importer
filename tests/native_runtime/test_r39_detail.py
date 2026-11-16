"""R39: switch-less masters (ArchitecturePreset_CR+NOM+Detail, Trim+CR+NAH) sample Detail Normal; Interior_Walls spellings;
EnemyPreset_Masked_NAO+Detail Height/HX is a Parallax-gated layer (info when the switch is off)."""
import importlib.util
import sys
import unittest
from pathlib import Path
try:
    from .test_r12_detail_normal import RnmGraph
    from .test_surface_layer_stages import load_surfaces, record
except ImportError:
    from test_r12_detail_normal import RnmGraph
    from test_surface_layer_stages import load_surfaces, record

ROOT = Path(__file__).resolve().parents[2]
DN = '/Game/T/T_Detail_N'


def load_rules():
    spec = importlib.util.spec_from_file_location('r39_audit_param_rules', ROOT / 'Shared/materials/audit_param_rules.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class SwitchlessDetail(unittest.TestCase):
    surfaces = load_surfaces()

    def run_surface(self, master, textures, switches):
        g = RnmGraph()
        self.surfaces.build_surface(g, record(master, textures, switches))
        return g

    def test_switchless_master_builds_detail(self):
        g = self.run_surface('M_ArchitecturePreset_CR+NOM+Detail', {'CR': '/Game/T/CR', 'NOM': '/Game/T/NOM', 'Detail Normal': DN},
                             {'Use Vertex Color': False})
        self.assertEqual(g.rnm_calls, 1)
        self.assertTrue(any('switch-less' in str(w) for w in g.report.get('notes', [])))

    def test_interior_walls_spelling(self):
        g = self.run_surface('M_Interior_Walls', {'CR': '/Game/T/CR', 'NOH': '/Game/T/NOH', 'DetailTexture': DN},
                             {'EnableDetailNormal': True})
        self.assertEqual(g.rnm_calls, 1)

    def test_switch_off_skips_detail(self):
        g = self.run_surface('M_Concrete_01', {'CR': '/Game/T/CR', 'NOH': '/Game/T/NOH', 'Detail Normal Texture': DN},
                             {'Enable Detail Normal': False})
        self.assertEqual(g.rnm_calls, 0)


class ParallaxHeight(unittest.TestCase):
    rules = load_rules()

    def test_height_hx_is_parallax(self):
        self.assertEqual(self.rules.layer_of('Height/HX'), 'parallax')

    def test_parallax_off_is_info(self):
        state, ev = self.rules.layer_gate('parallax', {'Parallax': {'value': False, 'overridden': False}})
        self.assertIs(state, False)
        cls, sev = self.rules.layer_rule('parallax', state, ev)
        self.assertTrue(cls.startswith('tex_layer_skipped'), cls)


if __name__ == '__main__':
    unittest.main()
