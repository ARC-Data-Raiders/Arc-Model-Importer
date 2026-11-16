"""Root masters with hardcoded TextureSamples (no texture parameters) keep their maps in
TextureStreamingData; the native producer must expose them as channel-coded textures."""
try:
    from .native_test_support import real_package
except ImportError:
    from native_test_support import real_package
import unittest

T = '/Game/Pioneer/MaterialLibrary/Textures/Trims/'


def root(streaming, referenced, typ='Material', params=None):
    props = {'TextureStreamingData': [{'TextureName': n} for n in streaming]}
    if params:
        props['TextureParameterValues'] = params
    return [{'Type': typ, 'Name': 'M_Root', 'Package': '/Game/M_Root',
             'CachedExpressionData': {'ReferencedTextures': [None] + [{'ObjectName': "Texture2D'%s'" % r.rsplit('/', 1)[-1].split('.')[0], 'ObjectPath': r} for r in referenced]},
             'Properties': props}]


class RootStreamingTextures(unittest.TestCase):
    def setUp(self):
        self.repo = real_package('Shared.materials.native.repository')

    def test_root_material_streaming_codes_resolve_to_referenced_paths(self):
        raw = root(['T_CableTrim_CR', 'T_CableTrim_NXM', 'T_Dust_Breakup'], [T + 'T_CableTrim_CR.0', T + 'T_CableTrim_NXM.0'])
        r = self.repo.parse_material(raw, '/Game/M_Root')
        self.assertEqual(set(r.textures), {'CR', 'NXM'})
        self.assertIn('T_CableTrim_CR', r.textures['CR'])

    def test_parameter_textures_win_and_instances_untouched(self):
        raw = root(['T_CableTrim_CR'], [T + 'T_CableTrim_CR.0'], typ='MaterialInstanceConstant')
        self.assertEqual(dict(self.repo.parse_material(raw, '/Game/M_Root').textures), {})
        raw = root(['T_CableTrim_CR'], [T + 'T_CableTrim_CR.0'],
                   params=[{'ParameterInfo': {'Name': 'Base'}, 'ParameterValue': {'ObjectPath': T + 'T_Other.0'}}])
        self.assertEqual(set(self.repo.parse_material(raw, '/Game/M_Root').textures), {'Base'})


if __name__ == '__main__':
    unittest.main()
