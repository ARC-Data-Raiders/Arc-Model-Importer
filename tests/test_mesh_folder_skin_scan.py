import ast, os, types, unittest
from pathlib import Path

SOURCE = Path(os.environ.get('ARC_TEXTURES_SOURCE') or Path(__file__).resolve().parents[1] / 'Shared/textures.py')


class MeshFolderSkinScanTests(unittest.TestCase):
    def scan(self, files):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_scan_mi_jsons_in_folder')
        ns = {'os': types.SimpleNamespace(listdir=lambda _p: list(files), path=os.path),
              '_is_mi_json_filename': lambda f: f.lower().startswith('mi_') and f.lower().endswith('.json'),
              'resolve_part_mi_json': lambda p: p}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), ns)
        return ns[function.name]('/mesh')

    def test_arc_material_twin_is_not_a_skin(self):
        names = [n for n, _p in self.scan(['MI_Goalie_Boots.json', 'MI_Goalie_Boots.arc-material.json'])]
        self.assertEqual(names, ['__DEFAULT__'])

    def test_plain_json_wins_over_arc_material_twin(self):
        result = self.scan(['MI_Goalie_Boots.arc-material.json', 'MI_Goalie_Boots.json'])
        self.assertEqual([os.path.basename(p) for _n, p in result], ['MI_Goalie_Boots.json'])

    def test_arc_material_only_folder_keeps_skins(self):
        names = sorted(n for n, _p in self.scan(['MI_Goalie_Boots_Red.arc-material.json',
                                                 'MI_Goalie_Boots_Blue.arc-material.json']))
        self.assertEqual(names, ['Blue', 'Red'])


if __name__ == '__main__':
    unittest.main()
