"""niagara_fx_materials: path mapping, MI-chain parameter merge, graph family and the pure helpers (no bpy)."""
import json
import os
import tempfile
import unittest

import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # repo root
from Niagara import niagara as fxm

MASTER = [{'Type': 'Material', 'Name': 'M_FX_Smoke_Cheap_Master', 'Properties': {
    'BlendMode': 'EBlendMode::BLEND_Translucent', 'ShadingModel': 'EMaterialShadingModel::MSM_DefaultLit'},
    'CachedExpressionData': {
        'RuntimeEntries': {'ParameterInfoSet': [{'Name': 'Channel select'}, {'Name': 'Contrast'}, {'Name': 'Density'}]},
        'RuntimeEntries[1]': {'ParameterInfoSet': [{'Name': 'Tint'}]},
        'RuntimeEntries[3]': {'ParameterInfoSet': [{'Name': 'Tex'}, {'Name': 'Mask'}]},
        'ScalarValues': [1.0, 0.5, 3.0],
        'VectorValues': [{'R': 1.0, 'G': 0.5, 'B': 0.25, 'A': 1.0}],
        'TextureValues': [{'AssetPathName': '/Game/Pioneer/FX/T_Smoke.T_Smoke'}, {'AssetPathName': '/Engine/EngineResources/DefaultTexture.DefaultTexture'}],
        'DynamicParameterNames': ['Erode', 'Glow']},
    'ReferencedTextures': [{'ObjectPath': '/Game/Pioneer/FX/T_Smoke_N.0'}, {'ObjectPath': '/Game/Pioneer/FX/T_Smoke.0'}]}]


def mi(name, parent, **props):
    return [{'Type': 'MaterialInstanceConstant', 'Name': name, 'Properties': dict(Parent={'ObjectPath': parent}, **props)}]


def scalar(name, value):
    return {'ParameterInfo': {'Name': name}, 'ParameterValue': value}


class FxMaterials(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        fx = os.path.join(self.root, 'PioneerGame', 'Content', 'Pioneer', 'FX')
        os.makedirs(fx)
        self.write(os.path.join(fx, 'M_FX_Smoke_Cheap_Master.json'), MASTER)
        self.write(os.path.join(fx, 'MI_Parent.json'), mi(
            'MI_Parent', '/Game/Pioneer/FX/M_FX_Smoke_Cheap_Master.0',
            ScalarParameterValues=[scalar('Contrast', 2.0), scalar('Density', 4.0)],
            BasePropertyOverrides={'BlendMode': 'EBlendMode::BLEND_Additive'}))
        self.write(os.path.join(fx, 'MI_Child.arc-material.json'), mi(
            'MI_Child', '/Game/Pioneer/FX/MI_Parent.0',
            ScalarParameterValues=[scalar('Density', 5.0)],
            VectorParameterValues=[{'ParameterInfo': {'Name': 'Tint'}, 'ParameterValue': {'R': 0.1, 'G': 0.2, 'B': 0.3, 'A': 0.4}}],
            TextureParameterValues=[{'ParameterInfo': {'Name': 'Tex'}, 'ParameterValue': {'ObjectPath': '/Game/Pioneer/FX/T_Smoke2.0'}}],
            StaticParametersRuntime={'StaticSwitchParameters': [{'ParameterInfo': {'Name': 'UseX'}, 'Value': True}]}))
        self.write(os.path.join(fx, 'MI_Child.json'), mi('MI_Child', '/Game/Pioneer/FX/Missing.0'))  # .arc-material.json wins
        for png in ('T_Smoke.png', 'T_Smoke2.png', 'T_Smoke_N.png'):
            open(os.path.join(fx, png), 'wb').close()
        os.makedirs(os.path.join(self.root, 'loose'))
        open(os.path.join(self.root, 'loose', 'T_Elsewhere.png'), 'wb').close()
        self.res = fxm.fx_materials_Resolver([self.root])

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def write(path, doc):
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh)

    def test_content_rel(self):
        self.assertEqual(fxm.content_rel('/Game/Pioneer/X/Y.Y'), 'PioneerGame/Content/Pioneer/X/Y')
        self.assertEqual(fxm.content_rel('/Engine/Foo/Bar.Bar'), 'Engine/Content/Foo/Bar')
        self.assertIsNone(fxm.content_rel('/Script/Engine'))
        self.assertIsNone(fxm.content_rel(None))

    def test_chain_merge(self):
        info = self.res.material_info('/Game/Pioneer/FX/MI_Child.MI_Child')
        self.assertEqual((info['name'], info['master'], info['blend']), ('MI_Child', 'M_FX_Smoke_Cheap_Master', 'BLEND_Additive'))
        # master defaults < parent overrides < child overrides
        self.assertEqual(info['scalars'], {'Channel select': 1.0, 'Contrast': 2.0, 'Density': 5.0})
        self.assertEqual(info['vectors'], {'Tint': (0.1, 0.2, 0.3, 0.4)})
        self.assertEqual(info['textures'], {'Tex': '/Game/Pioneer/FX/T_Smoke2.0'})  # DefaultTexture dropped
        self.assertEqual(info['switches'], {'UseX': True})
        self.assertEqual(info['dyn_names'], ['Erode', 'Glow'])
        self.assertTrue(info['complete'])
        self.assertFalse(info['unlit'])
        self.assertIs(self.res.material_info('/Game/Pioneer/FX/MI_Child.MI_Child'), info)  # cached

    def test_master_blend_and_incomplete_chain(self):
        self.assertEqual(self.res.material_info('/Game/Pioneer/FX/M_FX_Smoke_Cheap_Master.0')['blend'], 'BLEND_Translucent')
        self.write(os.path.join(self.root, 'PioneerGame', 'Content', 'Pioneer', 'FX', 'MI_Orphan.json'), mi('MI_Orphan', '/Game/Pioneer/FX/Nope.0'))
        info = fxm.fx_materials_Resolver([self.root]).material_info('/Game/Pioneer/FX/MI_Orphan.0')
        self.assertFalse(info['complete'])
        self.assertEqual(info['blend'], 'BLEND_Opaque')
        self.assertIsNone(self.res.material_info('/Game/Pioneer/FX/Absent.0'))

    def test_textures(self):
        info = self.res.material_info('/Game/Pioneer/FX/MI_Child.0')
        self.assertTrue(self.res.main_texture(info, 'Missing', 'Tex').endswith('T_Smoke2.png'))
        bare = dict(info, textures={})
        self.assertTrue(self.res.main_texture(bare).endswith('T_Smoke.png'))  # skips the _N referenced texture
        self.assertIsNone(self.res.texture('/Engine/EngineResources/DefaultTexture.DefaultTexture'))
        self.assertIsNone(self.res.texture(None))
        self.assertTrue(self.res.texture('/Game/Other/T_Elsewhere.0').endswith(os.path.join('loose', 'T_Elsewhere.png')))  # basename index

    def test_family(self):
        self.assertIsNone(fxm.family(None))
        self.assertEqual(fxm.family({'master': 'M_FX_Frigate_JetCone', 'blend': 'BLEND_Additive'}), 'jetcone')
        self.assertEqual(fxm.family({'master': 'M_Unknown', 'blend': 'BLEND_Additive'}), 'additive')
        self.assertEqual(fxm.family({'master': 'M_Unknown', 'blend': 'BLEND_Masked'}), 'debris')
        self.assertEqual(fxm.family({'master': 'M_Unknown', 'blend': 'BLEND_Translucent'}), 'smoke')

    def test_channel_index(self):
        self.assertEqual(fxm.channel_index({'scalars': {}}), 3)
        self.assertEqual(fxm.channel_index({'scalars': {'Channel select': 1.6}}), 2)
        self.assertEqual(fxm.channel_index({'scalars': {'Channel Select': 0.0}}), 0)
        self.assertEqual(fxm.channel_index({'scalars': {'Channel select': -2.0}}), 0)
        self.assertEqual(fxm.channel_index({'scalars': {'Channel select': 7.0}}), 3)

    def test_cheap_contrast(self):
        for x in (0.0, 0.3, 1.0):
            self.assertAlmostEqual(fxm.cheap_contrast(x, 0.0), x)
        self.assertEqual(fxm.cheap_contrast(0.0, 1.0), 0.0)
        self.assertAlmostEqual(fxm.cheap_contrast(0.5, 1.0), 0.5)
        self.assertEqual(fxm.cheap_contrast(1.0, 1.0), 1.0)
        self.assertAlmostEqual(fxm.cheap_contrast(0.6, 0.5), 0.7)


if __name__ == '__main__':
    unittest.main()
