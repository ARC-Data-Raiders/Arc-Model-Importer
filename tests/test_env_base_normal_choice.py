"""The packed-prop layer-1 normal prefers exact MI parameters over filename suffixes (no Blender)."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOH = ("1. NOH", "NOH", "Normal Base", "Normal", "Normals", "NormalMap")
PACKED = ("NXX/NMX Texture", "NOM", "NXM", "NMX", "NXX", "NormalMap")


class BaseNormalChoiceTests(unittest.TestCase):
    def choose(self, lookup):
        tree = ast.parse((ROOT / 'materials/environment.py').read_text(encoding='utf-8'))
        body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_base_normal_image']

        def suffix_only(tex_lookup, *keys):
            # Stand-in for the suffix fallback: '_noh' on the file stem.
            for key in keys:
                for param, (path, img) in tex_lookup.items():
                    if path.lower().endswith('_' + key.lower() + '.png'):
                        return param, img
            return None, None

        ns = {'_find_env_tex': suffix_only}
        exec(compile(ast.Module(body=body, type_ignores=[]), '<environment>', 'exec'), ns)
        return ns['_base_normal_image'](lookup, NOH, PACKED)

    def test_bound_packed_normal_beats_suffix_matched_master_default(self):
        # MI_EnemyTrim_Insides: own NMX, plus the master's disabled layer-2 concrete defaults.
        lookup = {
            'CR Texture': ('T_EnemyTrimSheet_InsulBlanket_01_CR.png', 'cr'),
            'NXX/NMX Texture': ('T_EnemyTrimSheet_InsulBlanket_01_NMX.png', 'nmx'),
            'NOH Blend': ('T_Concrete_Floor_03_NOH.png', 'concrete'),
        }
        self.assertEqual(self.choose(lookup), 'nmx')

    def test_exact_noh_still_wins_and_suffix_fallback_remains(self):
        self.assertEqual(self.choose({'NOH': ('T_A_NOH.png', 'noh'), 'NXX/NMX Texture': ('T_A_NMX.png', 'nmx')}), 'noh')
        self.assertEqual(self.choose({'Base Normal Map': ('T_B_NOH.png', 'noh')}), 'noh')


if __name__ == '__main__':
    unittest.main()
