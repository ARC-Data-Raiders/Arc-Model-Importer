"""R15: library prefix normalisation; authored SRGB=false beats a builder colour hint. No Blender."""
import importlib
import tempfile
import types
import unittest
from pathlib import Path

from .native_test_support import PACKAGE, namespace

namespace()
rules = importlib.import_module(PACKAGE + '.Shared.materials.audit_param_rules')
interop = importlib.import_module(PACKAGE + '.Shared.materials.native.interop')


class LibraryPrefixTests(unittest.TestCase):
    def test_t_prefixed_library_stems(self):
        for stem in ('T_Generic_02_GRM', 'Generic_01_GRM', 'T_Global_04_GRM', 'T_Default_N',
                     'TA_Anything_01', 'CA_Prop_Colors', 'TA_Generic_01'):
            with self.subTest(stem=stem):
                self.assertTrue(rules.is_shared_library_texture(stem))

    def test_material_library_path_is_library(self):
        # T_TrimDirectional_NMD lives in /Game/Pioneer/MaterialLibrary/Textures/Trims.
        path = '//cache/Pioneer/MaterialLibrary/Textures/Trims/T_TrimDirectional_NMD.png'
        self.assertTrue(rules.is_shared_library_texture('T_TrimDirectional_NMD', path))
        self.assertFalse(rules.is_shared_library_texture('T_TrimDirectional_NMD', '//cache/Props/T_X.png'))

    def test_owned_stems_stay_foreign_candidates(self):
        for stem in ('T_Billboard_02_A_C', 'T_AmmoBox_01_A_CR', 'T_GenericLike'):
            with self.subTest(stem=stem):
                self.assertFalse(rules.is_shared_library_texture(stem))


class ProducerInfoSeverityTests(unittest.TestCase):
    # material_audit/classify_unlinked emit these as INFO; reclass must not turn them into DEFECT.
    def test_producer_info_flags_stay_info(self):
        for flag in ('emissive_gated_off:Emissive', 'emissive_default_off', 'tex_unnamed_unlinked:T_X'):
            with self.subTest(flag=flag):
                self.assertEqual(rules.severity(flag), rules.INFO)

    def test_missing_master_stays_defect(self):
        self.assertEqual(rules.severity('master_unavailable'), rules.DEFECT)


class _Image(dict):
    pass


class _Repo:
    def __init__(self, path, info):
        self.path, self.info, self.mount_aliases = path, info, None

    def texture_info(self, reference):
        return self.info

    def image_path(self, reference, role='data'):
        return self.path


class AuthoredLinearColourHintTests(unittest.TestCase):
    def sample(self, info, srgb):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'T_BrokenGlassOverlay_01_NC.png'
            path.write_bytes(b'png')
            loaded = []

            def load(filepath, check_existing=False):
                image = _Image()
                image.colorspace_settings = types.SimpleNamespace(name='')
                image.filepath, image.alpha_mode = filepath, 'STRAIGHT'
                loaded.append(image)
                return image

            graph = object.__new__(interop.Graph)
            graph.index = _Repo(path, {**info, 'source_path': str(path)})
            graph.report, graph._state = {}, {'images': {}, 'samples': {}}
            images = type('Images', (), {'load': staticmethod(load), '__iter__': lambda self: iter(())})()
            graph.bpy = types.SimpleNamespace(data=types.SimpleNamespace(images=images))
            graph.sample_image = lambda image, uv, label, info: {'image': image, 'info': info}
            result = graph.image('/Game/Pioneer/MaterialLibrary/Textures/Glass/T_BrokenGlassOverlay_01_NC', srgb=srgb)
            return result['info']['role'], loaded[0].colorspace_settings.name

    def test_authored_linear_beats_colour_hint(self):
        self.assertEqual(self.sample({'SRGB': False}, True), ('data', 'Non-Color'))

    def test_default_flag_keeps_colour_hint(self):
        self.assertEqual(self.sample({}, True), ('color', 'sRGB'))

    def test_normal_role_untouched(self):
        self.assertEqual(self.sample({'SRGB': False}, 'normal'), ('normal', 'Non-Color'))


if __name__ == '__main__':
    unittest.main()
