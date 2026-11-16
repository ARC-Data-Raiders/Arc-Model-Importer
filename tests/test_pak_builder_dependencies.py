import ast
import contextlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import unittest.mock


ROOT = Path(__file__).resolve().parents[1]
SELECTED = {'run_arcpak', '_run_arcpak_request', '_builder_texture_packages'}
DECAL_MASTER = 'PioneerGame/Content/Pioneer/MaterialLibrary/M_Presets/M_DecalPreset_Masked_A+Detail'
SAND_MASTER = 'PioneerGame/Content/Pioneer/Environment/South/Dunes/Meshes/Dune_Spline/M_SandDune'
DETAIL = 'PioneerGame/Content/Pioneer/MaterialLibrary/Textures/Breakup/T_Generic_02_GRM'
SAND = 'PioneerGame/Content/Pioneer/Environment/South/Dunes/Textures/T_South_Base_Sand_Dunes_04_CH'


def load_extractor():
    source = ROOT / 'Shared/pak_extract.py'
    tree = ast.parse(source.read_text(encoding='utf-8-sig'))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in SELECTED]
    namespace = {'os': os, 'subprocess': subprocess, '_serve_start': lambda: None,
                 '_SERVE': {'broken': True}, '_CREATE_NO_WINDOW': 0,
                 '_ArcPakResult': lambda code, stdout='', stderr='': subprocess.CompletedProcess([], code, stdout, stderr)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


class BuilderTextureDependencies(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('DATARAIDERS_TEST_TEMP'))
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.module = load_extractor()
        self.calls = []
        self.locked = False
        self.materials = [DECAL_MASTER]
        self.fetch_code = 0
        self.write_images = True
        self.command = ['ArcPak.Export.exe', 'extract', '--game-dir', 'game', '--usmap', 'map',
                        '--out', str(self.cache), '--index-dir', 'indexes', '--path', 'Exact/Prop']
        @contextlib.contextmanager
        def output_lock(command):
            self.assertFalse(self.locked)
            self.locked = True
            try:
                yield
            finally:
                self.locked = False
        self.module['_out_dir_lock'] = output_lock

    def export(self, command):
        self.assertTrue(self.locked)
        self.calls.append(list(command))
        paths = [value for index, value in enumerate(command) if index and command[index - 1] == '--path']
        if paths == ['Exact/Prop']:
            for master in self.materials:
                metadata = self.cache / (master + '.arc-material.json')
                metadata.parent.mkdir(parents=True, exist_ok=True)
                metadata.write_text('{}', encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, 'prop export', '')
        if self.write_images and self.fetch_code == 0:
            for reference in paths:
                self.texture(reference)
        return subprocess.CompletedProcess(command, self.fetch_code, 'texture export', 'texture failure' if self.fetch_code else '')

    def texture(self, reference, metadata=True):
        image = self.cache / (reference + '.png')
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b'fixture image; not decoded')
        if metadata:
            image.with_suffix('.arc-texture.json').write_text('{}', encoding='utf-8')

    def run_export(self):
        return self.module['run_arcpak'](self.command, fallback=self.export)

    def test_cold_decal_fetches_exact_builder_default(self):
        result = self.run_export()
        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.cache / (DETAIL + '.png')).is_file())
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1][-2:], ['--path', DETAIL])
        self.assertNotIn('Exact/Prop', self.calls[1])
        self.assertIn('prop export', result.stdout)
        self.assertIn('texture export', result.stdout)

    def test_cold_sand_fetches_exact_standin_without_claiming_rvt(self):
        self.materials = [SAND_MASTER]
        self.assertEqual(self.run_export().returncode, 0)
        self.assertTrue((self.cache / (SAND + '.png')).is_file())

    def test_both_defaults_are_requested_once_in_one_batch(self):
        self.materials = [DECAL_MASTER, SAND_MASTER]
        self.assertEqual(self.run_export().returncode, 0)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(set(self.calls[1][self.calls[1].index('--path') + 1::2]), {DETAIL, SAND})

    def test_complete_cached_default_is_not_exported_again(self):
        self.texture(DETAIL)
        self.assertEqual(self.run_export().returncode, 0)
        self.assertEqual(len(self.calls), 1)

    def test_png_without_native_texture_metadata_is_refetched(self):
        self.texture(DETAIL, metadata=False)
        self.assertEqual(self.run_export().returncode, 0)
        self.assertEqual(len(self.calls), 2)
        self.assertTrue((self.cache / (DETAIL + '.arc-texture.json')).is_file())

    def test_unrelated_master_does_not_fetch_defaults(self):
        self.materials = ['Other/Path/M_SandDune']
        self.assertEqual(self.run_export().returncode, 0)
        self.assertEqual(len(self.calls), 1)

    def test_metadata_only_commands_do_not_fetch_images(self):
        self.command[1] = 'material-metadata'
        self.assertEqual(self.run_export().returncode, 0)
        self.assertEqual(len(self.calls), 1)

    def test_failed_supplement_remains_a_failed_export(self):
        self.fetch_code = 7
        result = self.run_export()
        self.assertEqual(result.returncode, 7)
        self.assertIn('texture failure', result.stderr)
        self.assertFalse(self.locked)

    def test_false_success_without_output_is_rejected(self):
        self.write_images = False
        result = self.run_export()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(DETAIL, result.stderr)
        self.assertFalse(self.locked)

    def test_warm_serve_also_fetches_defaults(self):
        self.module['_SERVE']['broken'] = False
        self.module['_serve_call'] = lambda command, start, timeout: (object(), self.export(command))
        with unittest.mock.patch.dict(os.environ, {'ARCPAK_NO_SERVE': '0'}):
            self.assertEqual(self.run_export().returncode, 0)
        self.assertEqual(len(self.calls), 2)

    def test_registry_stays_bound_to_actual_builder_constants(self):
        for name, filename, expected in [('ATLAS_DETAIL', 'decals.py', DETAIL), ('SAND_LAYER', 'sand_dune.py', SAND)]:
            tree = ast.parse((ROOT / 'Shared/materials/rebuild' / filename).read_text(encoding='utf-8'))
            values = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
            self.assertEqual(values, [expected])


if __name__ == '__main__':
    unittest.main()
