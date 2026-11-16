"""Missing appearance meshes report why the pak lookup failed (no Blender)."""
import ast, os, sys, types, unittest
from pathlib import Path
from section_fixture import add_parents

ROOT = Path(os.environ['DATARAIDERS_TEST_REPOSITORY']) if os.environ.get('DATARAIDERS_TEST_REPOSITORY') else next(
    p for p in Path(__file__).resolve().parents if (p / 'Outfit_Accessories/appearance.py').is_file())
PKG = '_appearance_reason_probe'
NAMES = {'locate_appearance_mesh', '_pak_reason_suffix', '_mesh_from_pak', '_cached_mesh_from_rel'}


class MissingMeshReasonTests(unittest.TestCase):
    def setUp(self):
        self.scene = types.SimpleNamespace(arc_import_source='PAK')
        self.pak = types.ModuleType(PKG + '.Shared.pak_extract')
        self.pak.__dict__.update(
            configured_dir=lambda scene: 'P:/Paks',
            resolve_usmap=lambda scene: '',
            ensure_rels=lambda scene, rels: [],
            cache_dir=lambda: 'C:/nowhere',
            game_path=lambda rel: rel,
            _expected_uemodel=lambda root, rel: '',
            mesh_cache_is_current=lambda path: False,
        )
        pkg = types.ModuleType(PKG); pkg.__path__ = []; pkg.pak_extract = self.pak
        sys.modules[PKG] = pkg; sys.modules[PKG + '.Shared.pak_extract'] = self.pak
        add_parents(sys.modules, PKG)
        src = ast.parse((ROOT / 'Outfit_Accessories/appearance.py').read_text(encoding='utf-8'))
        body = [n for n in src.body if isinstance(n, ast.FunctionDef) and n.name in NAMES]
        body += [n for n in src.body if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == '_mesh_from_pak.last_reason']
        self.assertEqual({n.name for n in body if isinstance(n, ast.FunctionDef)}, NAMES)
        self.ns = {
            '__package__': PKG + '.Outfit_Accessories', '__name__': PKG + '.Outfit_Accessories.appearance',
            'bpy': types.SimpleNamespace(context=types.SimpleNamespace(scene=self.scene)),
            '_mesh_rel_variants': lambda rel: [rel],
            '_resolve_csv_rel_path': lambda rel: '',
            '_mesh_file_usable': lambda path: bool(path),
        }
        exec(compile(ast.Module(body=body, type_ignores=[]), 'Outfit_Accessories/appearance.py', 'exec'), self.ns)

    def tearDown(self):
        for name in [n for n in sys.modules if n == PKG or n.startswith(PKG + '.')]:
            sys.modules.pop(name, None)

    def test_pak_failure_reason_is_reported(self):
        self.assertEqual(self.ns['locate_appearance_mesh']('Characters/Shared/Bodies/SK_Body'), '')
        self.assertEqual(self.ns['_pak_reason_suffix'](), ' (The game mappings file is missing)')

    def test_non_pak_source_is_reported(self):
        self.scene.arc_import_source = 'DISK'
        self.assertEqual(self.ns['locate_appearance_mesh']('Characters/Shared/Bodies/SK_Body'), '')
        self.assertIn('Import Source is not Paks', self.ns['_pak_reason_suffix']())

    def test_stale_reason_is_cleared(self):
        self.ns['locate_appearance_mesh']('Characters/Shared/Bodies/SK_Body')
        self.ns['locate_appearance_mesh']('')
        self.assertEqual(self.ns['_pak_reason_suffix'](), '')


if __name__ == '__main__':
    unittest.main()
