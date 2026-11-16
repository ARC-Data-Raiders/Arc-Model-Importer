"""Outfit import blockers found by the default-colourway validation (no Blender)."""
import ast
import importlib.util
import os
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _function(rel, name, **ns):
    tree = ast.parse((ROOT / rel).read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    source = ast.get_source_segment((ROOT / rel).read_text(encoding='utf-8'), node)
    source = source.replace('    from ..Outfit_Accessories import pak_catalog\n', '').replace('    from . import pak_catalog\n', '')
    namespace = {'os': os, **ns}
    exec(compile(source, rel, 'exec'), namespace)
    return namespace[name]


def _pak_catalog():
    spec = importlib.util.spec_from_file_location('pak_catalog_under_test', ROOT / 'Outfit_Accessories/pak_catalog.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CachedPluginPackageTests(unittest.TestCase):
    def setUp(self):
        self.package = _function('Shared/pak_extract.py', 'cached_plugin_package', pak_catalog=_pak_catalog())
        self.folder = tempfile.TemporaryDirectory()
        self.cache = os.path.join(self.folder.name, 'ArcPak', 'cache')

    def tearDown(self):
        self.folder.cleanup()

    def test_plugin_parent_resolves_to_its_mount_path(self):
        # Banshee's hierarchy reaches EmbarkScript's cloth template, written without a Content folder.
        source = os.path.join(self.cache, 'EmbarkScript', 'ClothingAssetTemplates', 'M_Character_ClothMaterial.json')
        self.assertEqual(self.package(source, self.cache), 'EmbarkScript/ClothingAssetTemplates/M_Character_ClothMaterial')

    def test_game_roots_and_foreign_files_are_not_plugins(self):
        game = os.path.join(self.cache, 'PioneerGame', 'Content', 'Pioneer', 'M_X.json')
        outside = os.path.join(self.folder.name, 'EmbarkScript', 'M_X.json')
        texture = os.path.join(self.cache, 'EmbarkScript', 'Textures', 'T_X.png')
        for source in (game, outside, texture):
            self.assertEqual(self.package(source, self.cache), '', source)


class TextureLogicalPackageTests(unittest.TestCase):
    def test_slashless_plugin_texture_mount_is_accepted(self):
        # TA_Character_Colors' exporter PackagePath has no leading slash; it aborted 76 outfit imports.
        text = (ROOT / 'ArcPak' / 'TextureMetadataExport.cs').read_text(encoding='utf-8')
        body = text[text.index('internal static string LogicalPackage'):]
        self.assertRegex(body, r'if \(!path\.Contains\("/Content/", StringComparison\.OrdinalIgnoreCase\) && path\.Contains\(\'/\'\)\) return "/" \+ path;')
        self.assertNotIn("physical.StartsWith('/') && !path.Contains", body)


class DecalOverrideTests(unittest.TestCase):
    def test_zero_colour_override_keeps_the_decal_artwork(self):
        # Driftcoat's T_Patch_02_Color_C keeps its chevron artwork at ColorOverride 0 (master default).
        text = (ROOT / 'Shared/materials' / 'gt_outfit_pbr.py').read_text(encoding='utf-8')
        # tint_fac became a clamped 'override' in commit 2c88423; zero must still stay zero.
        self.assertIn('override = max(0.0, min(1.0, float(decal.get("color_override", 0.0))))', text)
        self.assertIsNone(re.search(r'tint_fac < 0\.01:\s*\n\s*tint_fac = 1\.0', text))
        self.assertIsNone(re.search(r'override < 0\.01:\s*\n\s*override = 1\.0', text))


class ReceiptLongPathTests(unittest.TestCase):
    def test_receipt_names_take_the_long_path_form(self):
        text = (ROOT / 'Shared/pak_extract.py').read_text(encoding='utf-8')
        self.assertIn("target = filesystem_path(_material_companion(material_file) + '.ready')", text)
        self.assertIn("temp = filesystem_path(target + '.tmp-' + uuid.uuid4().hex)", text)
        self.assertIn("open(filesystem_path(_material_companion(material_file) + '.ready'), encoding='utf-8')", text)


class LongDependencyPathTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'MAX_PATH is a Windows limit')
    def test_signature_hashes_a_dependency_past_max_path(self):
        # Driftcoat's InnerSleeves closure manifest is 260 characters under the validator cache.
        from tests.test_material_texture_upgrade import load_pak
        pak = load_pak()
        with tempfile.TemporaryDirectory() as folder:
            deep = os.path.join(os.path.abspath(folder), *(['Characters_Assets_Driftcoat_Skins'] * 8))
            target = deep + os.sep + 'MI_Driftcoat_InnerSleeves_Cotton.arc-material.json.textures.json'
            self.assertGreater(len(target), 260)
            os.makedirs('\\\\?\\' + deep)
            with open('\\\\?\\' + target, 'w', encoding='utf-8') as stream:
                stream.write('{}')
            signature = pak._material_dependency_signature([target])
            self.assertEqual(signature[0]['path'], os.path.normcase(target))
            self.assertEqual(len(signature[0]['sha256']), 64)
            os.remove('\\\\?\\' + target)
            for _ in range(8):
                os.rmdir('\\\\?\\' + deep)
                deep = os.path.dirname(deep)


class PluginParentWalkTests(unittest.TestCase):
    """ArcPak writes plugin nodes at <cache>/<Plugin>/... (Banshee's EmbarkScript cloth template)."""

    def setUp(self):
        import json
        from tests.test_material_texture_upgrade import load_pak
        self.pak = load_pak()
        self.folder = tempfile.TemporaryDirectory()
        self.cache = Path(self.folder.name) / 'cache'

        def node(rel, package, parent):
            path = self.cache / (rel + '.arc-material.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            source = {'schema': 'dataraiders.native-material-hierarchy', 'version': 1, 'package': package,
                      'object_path': '/' + package + '.' + package.rsplit('/', 1)[-1], 'parent': parent}
            path.write_text(json.dumps({'Exports': [{'Name': package.rsplit('/', 1)[-1], 'Properties': {'Parent': parent}}],
                                        'DataRaidersSource': source}), encoding='utf-8')
            (self.cache / (rel + '.json')).write_text('{}', encoding='utf-8')
            return path
        self.plugin = node('EmbarkScript/Templates/M_Cloth', 'EmbarkScript/Templates/M_Cloth', '')
        self.mi = node('PioneerGame/Content/Pioneer/Characters/MI_Boots', 'PioneerGame/Content/Pioneer/Characters/MI_Boots',
                       '/EmbarkScript/Templates/M_Cloth.M_Cloth')

    def tearDown(self):
        self.folder.cleanup()

    def test_pioneer_instance_walks_into_its_plugin_parent(self):
        deps = self.pak._material_hierarchy_dependencies(str(self.cache / 'PioneerGame/Content/Pioneer/Characters/MI_Boots.json'))
        self.assertEqual([Path(d).resolve() for d in deps], [self.mi.resolve(), self.plugin.resolve()])

    def test_plugin_root_names_its_own_package(self):
        deps = self.pak._material_hierarchy_dependencies(str(self.cache / 'EmbarkScript/Templates/M_Cloth.json'))
        self.assertEqual([Path(d).resolve() for d in deps], [self.plugin.resolve()])

    def test_misplaced_plugin_root_is_rejected(self):
        moved = self.cache / 'Elsewhere' / 'M_Cloth.arc-material.json'
        moved.parent.mkdir(parents=True)
        moved.write_bytes(self.plugin.read_bytes())
        (self.cache / 'Elsewhere' / 'M_Cloth.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'no exact native mount'):
            self.pak._material_hierarchy_dependencies(str(self.cache / 'Elsewhere' / 'M_Cloth.json'))


class BatchedHierarchyTests(unittest.TestCase):
    def setUp(self):
        import contextlib
        import threading
        self.calls = []
        ns = {'threading': threading, 'contextmanager': contextlib.contextmanager,
              'raw_material_metadata_plan': lambda scene, **kw: self.calls.append(kw),
              '_join_material_metadata_prefetch': lambda scene: None}
        tree = ast.parse((ROOT / 'Shared/pak_extract.py').read_text(encoding='utf-8'))
        nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in ('batched_material_hierarchy', 'ensure_cached_material_hierarchy'))
                 or (isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == '_HIERARCHY_BATCH' for t in n.targets))]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'Shared/pak_extract.py', 'exec'), ns)
        self.ns = ns

    def test_an_outfits_part_requests_launch_once(self):
        ensure, batch = self.ns['ensure_cached_material_hierarchy'], self.ns['batched_material_hierarchy']
        with batch('scene'):
            ensure('scene', model_paths=['SK_A.uemodel'])
            with batch('scene'):
                ensure('scene', material_paths=['MI_A.json'])
            ensure('scene', material_paths=['MI_B.json'])
            self.assertEqual(self.calls, [])
        self.assertEqual(self.calls, [{'model_paths': ['SK_A.uemodel'], 'material_paths': ['MI_A.json', 'MI_B.json']}])

    def test_a_failed_outfit_launches_nothing_and_unbatches(self):
        ensure, batch = self.ns['ensure_cached_material_hierarchy'], self.ns['batched_material_hierarchy']
        with self.assertRaises(KeyError):
            with batch('scene'):
                ensure('scene', material_paths=['MI_A.json'])
                raise KeyError('part')
        self.assertEqual(self.calls, [])
        ensure('scene', material_paths=['MI_C.json'])
        self.assertEqual(len(self.calls), 1)

    def test_apply_outfit_preset_runs_inside_one_batch(self):
        text = (ROOT / 'Shared/importing.py').read_text(encoding='utf-8')
        body = text[text.index('def apply_outfit_preset('):text.index('def _apply_outfit_preset(')]
        self.assertIn('with pak_extract.batched_material_hierarchy(scene):', body)
        self.assertIn('pak_extract.ensure_cached_mesh_material_metadata(scene, meshes)', body)


if __name__ == '__main__':
    unittest.main()
