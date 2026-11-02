"""The legacy folder-texture fallback only takes maps named after the mesh (no Blender).

Effects/Meshes holds SM_FX_RadialBlur_01 beside T_MechanicalDebris_01_CR/NOM; the fallback used to take any *_CR in
the folder and painted the blur disc with debris.
"""
import ast
import os
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('_MISC_GENERIC_TOKENS', '_misc_name_tokens', '_find_misc_textures')


def load():
    tree = ast.parse((ROOT / 'materials/items.py').read_text(encoding='utf-8'))
    body = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in NAMES)
            or (isinstance(n, ast.Assign) and any(getattr(t, 'id', '') in NAMES for t in n.targets))]
    ns = {'os': os, 're': re}
    exec(compile(ast.Module(body=body, type_ignores=[]), '<items>', 'exec'), ns)
    return ns['_find_misc_textures']


class MiscTextureAffinityTests(unittest.TestCase):
    def folder(self, *names):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name in names:
            Path(tmp.name, name).write_bytes(b'')
        return tmp.name

    def test_foreign_folder_maps_are_rejected(self):
        folder = self.folder('SM_FX_RadialBlur_01.uemodel', 'T_MechanicalDebris_01_CR.png', 'T_MechanicalDebris_01_NOM.png')
        found = load()(folder, os.path.join(folder, 'SM_FX_RadialBlur_01.uemodel'))
        self.assertEqual((found['cr'], found['normal'], found['albedo']), (None, None, None))

    def test_named_maps_win_over_neighbours(self):
        folder = self.folder('T_Charm_Cat_01_CR.png', 'T_Charm_Duck_01_CR.png', 'T_Charm_Duck_01_NOM.png',
                             'T_Charm_Duck_01_Normal.png', 'T_Charm_Cat_01_NAM.png')
        found = load()(folder, os.path.join(folder, 'SK_Charm_Duck_01.psk'))
        self.assertTrue(found['cr'].endswith('T_Charm_Duck_01_CR.png'))
        self.assertTrue(found['normal'].endswith('T_Charm_Duck_01_NOM.png'))
        self.assertEqual(found['normal_type'], 'nom')

    def test_unnamed_mesh_keeps_the_old_any_map_behaviour(self):
        folder = self.folder('T_Thing_CR.png')
        self.assertTrue(load()(folder, '')['cr'].endswith('T_Thing_CR.png'))


if __name__ == '__main__':
    unittest.main()
