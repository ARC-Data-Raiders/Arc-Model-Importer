import importlib
import json
import types
import unittest
from pathlib import Path

from .native_test_support import PACKAGE, namespace, temp_fixture

namespace()
interop = importlib.import_module(PACKAGE + '.Shared.materials.native.interop')
assets = importlib.import_module(PACKAGE + '.Shared.materials.native.assets')
catalog = importlib.import_module(PACKAGE + '.Shared.materials.native.catalog')


class DefaultTextureMetadata(unittest.TestCase):
    def setUp(self):
        self.temp = temp_fixture()
        self.addCleanup(self.temp.cleanup)
        self.graph = interop.Graph.__new__(interop.Graph)
        self.graph.index = assets.NativeAssetRepository({'/Game': self.temp.name})
        self.graph.report = {}
        self.fallbacks = []
        self.graph._unset_image = self.fallback

    def fallback(self, uv, label, srgb):
        self.fallbacks.append((uv, label, srgb))
        return {'neutral': True, 'role': srgb}

    def test_unmounted_plugin_default_normal_reaches_existing_fallback(self):
        result = self.graph.image('/MeshModelingToolsetExp/Textures/DefaultNormal')
        self.assertEqual(result, {'neutral': True, 'role': 'data'})
        self.assertEqual(len(self.fallbacks), 1)
        self.assertTrue(any('approximate:' in warning for warning in self.graph.report['warnings']))
        self.assertNotIn('missing_textures', self.graph.report)

    def test_unmounted_engine_default_color_reaches_existing_fallback(self):
        result = self.graph.image('/Engine/EngineMaterials/DefaultTexture', srgb=True)
        self.assertEqual(result, {'neutral': True, 'role': True})

    def test_game_texture_named_default_normal_is_not_an_engine_fallback(self):
        with self.assertRaises(FileNotFoundError):
            self.graph.image('/Game/Missing/DefaultNormal')
        self.assertEqual(self.fallbacks, [])
        self.assertEqual(len(self.graph.report['missing_textures']), 1)

    def test_unmounted_nondefault_engine_texture_remains_missing(self):
        with self.assertRaises(FileNotFoundError):
            self.graph.image('/Engine/Textures/T_AuthoredNormal')
        self.assertEqual(self.fallbacks, [])
        self.assertEqual(len(self.graph.report['missing_textures']), 1)

    def test_optional_unmounted_nondefault_texture_does_not_raise(self):
        result = self.graph.image('/Plugin/Textures/T_AuthoredNormal', required=False)
        self.assertIsNone(result)
        self.assertEqual(self.fallbacks, [])
        self.assertEqual(len(self.graph.report['missing_textures']), 1)

    def test_absent_texture_stays_reported_when_caller_uses_nonthrowing_mode(self):
        for reference in (None, ''):
            with self.subTest(reference=reference):
                self.graph.report = {}
                result = self.graph.image(reference, label='Optional overlay mask', required=False)
                self.assertIsNone(result)
                self.assertEqual(len(self.graph.report['missing_textures']), 1)
                self.assertEqual(self.fallbacks, [])

    def test_absent_required_texture_still_uses_explicit_fallback(self):
        self.assertEqual(self.graph.image(None, label='Core normal', srgb=False),
                         {'neutral': True, 'role': False})
        self.assertEqual(self.fallbacks, [(None, 'Core normal', False)])

    def test_invalid_metadata_identity_is_not_hidden(self):
        def invalid_metadata(reference):
            raise ValueError('Texture metadata identity mismatch: ' + reference)
        self.graph.index = types.SimpleNamespace(texture_info=invalid_metadata)
        with self.assertRaisesRegex(ValueError, 'metadata identity mismatch'):
            self.graph.image('/Engine/EngineMaterials/DefaultNormal')
        self.assertEqual(self.fallbacks, [])


class MissingSignatureMetadata(unittest.TestCase):
    def setUp(self):
        self.temp = temp_fixture()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write('M', 'Material', '/Game/M')
        self.repo = assets.NativeAssetRepository({'/Game': self.root})
        self.reference = '/MeshModelingToolsetExp/Textures/DefaultNormal'

    def write(self, name, kind, package):
        (self.root / (name + '.json')).write_text(json.dumps({
            'Type': kind, 'Name': name, 'Package': package, 'Properties': {}
        }), encoding='utf-8')

    def signature(self, **kwargs):
        return catalog.material_build_signature('/Game/M', self.repo,
            texture_bindings=[{'reference': self.reference, 'role': 'data'}],
            recipe='missing-metadata', implementation_revision='test', **kwargs)

    def test_unmounted_metadata_marks_signature_incomplete(self):
        result = self.signature()
        self.assertFalse(result.complete)
        self.assertTrue(any('Missing exact native texture metadata' in item for item in result.diagnostics))
        self.assertTrue(any(item.get('status') == 'missing' and item.get('package') == self.reference for item in result.dependencies))

    def test_strict_signature_still_refuses_unmounted_metadata(self):
        with self.assertRaises(FileNotFoundError):
            self.signature(strict_images=True)

    def test_game_basename_does_not_supply_plugin_metadata(self):
        self.write('DefaultNormal', 'Texture2D', '/Game/DefaultNormal')
        (self.root / 'DefaultNormal.png').write_bytes(b'image')
        result = self.signature()
        self.assertFalse(result.complete)
        self.assertFalse(any(item.get('status') == 'present' for item in result.dependencies))

    def test_newly_available_exact_resources_change_signature(self):
        before = self.signature()
        self.write('DefaultNormal', 'Texture2D', self.reference)
        (self.root / 'DefaultNormal.png').write_bytes(b'image')
        self.repo = assets.NativeAssetRepository({'/Game': self.root,
            '/MeshModelingToolsetExp/Textures': self.root})
        after = self.signature()
        self.assertTrue(after.complete, after.diagnostics)
        self.assertNotEqual(before.key, after.key)

    def test_metadata_identity_error_is_not_swallowed(self):
        self.reference = '/Game/DefaultNormal'
        self.write('DefaultNormal', 'Texture2D', '/Game/Wrong')
        with self.assertRaisesRegex(ValueError, 'exact texture metadata export|package mismatch'):
            self.signature()


if __name__ == '__main__':
    unittest.main()
