"""R14: colorspace_mismatch ignores unlinked inspection nodes; foreign_texture needs a role conflict
on a non-library stem. Pure facts, no Blender."""
import importlib
import unittest

from .native_test_support import PACKAGE, namespace
from .test_material_audit import G, IMG, MI, audit, flags

namespace()
rules = importlib.import_module(PACKAGE + '.Shared.materials.audit_param_rules')


def _fl(r):
    return flags(r) if callable(flags) else r['flags']


class ColorspaceUnlinkedTests(unittest.TestCase):
    def _run(self, linked):
        img = IMG('T_Rust_01_N', linked=linked, colorspace='sRGB', sidecar_srgb=False,
                  filepath='//t/T_Rust_01_N.png')
        return audit.audit_material_facts(G(images=[img]), MI(textures={'Normal': '/Game/T_Rust_01_N'}),
                                          None, {}, None, ())

    def test_unlinked_srgb_node_is_not_mismatch(self):
        r = self._run(False)
        self.assertFalse(any(f.startswith('colorspace_mismatch') for f in r['flags']), r['flags'])

    def test_linked_srgb_node_is_mismatch(self):
        r = self._run(True)
        self.assertTrue(any(f.startswith('colorspace_mismatch') for f in r['flags']), r['flags'])


LIBRARY = ('TA_Weathering_Props_01_LAYER0', 'T_PropTrimSheet_Mask', 'T_GrungeAtlas_Props_A', 'CA_Prop_Colors',
           'T_ConcreteBuildingWorld_01_GRM', 'Generic_01_GRM', 'T_Global_04_GRM', 'T_Dirt_01_NXX', 'TA_Detail_Metal_N')


class ForeignTextureTests(unittest.TestCase):
    def _run(self, stem, label=''):
        img = IMG(stem, filepath='//t/%s.png' % stem, label=label)
        return audit.audit_material_facts(G(images=[IMG('T_X_BC'), img]), MI(textures={'BaseColor': '/Game/T_X_BC'}),
                                          None, {}, None, ())

    def test_library_stems_not_foreign(self):
        for stem in LIBRARY:
            with self.subTest(stem=stem):
                r = self._run(stem)
                self.assertFalse(any(f.startswith('foreign_texture') for f in r['flags']), r['flags'])

    def test_matching_role_not_foreign(self):
        r = self._run('T_Billboard_02_A_N', 'Normal')
        self.assertFalse(any(f.startswith('foreign_texture') for f in r['flags']), r['flags'])

    def test_contradicting_role_is_foreign(self):
        r = self._run('T_Billboard_02_A_N', 'Base Color')
        self.assertIn('foreign_texture', r['flags'])

    def test_role_rules(self):
        self.assertEqual(rules.texture_role('T_Rust_01_N'), 'normal')
        self.assertEqual(rules.texture_role('T_Rust_01_C'), 'colour')
        self.assertEqual(rules.param_role('Roughness Mask'), 'data')
        self.assertFalse(rules.foreign_role_conflict('T_Rust_01_C', 'Diffuse'))
        self.assertTrue(rules.foreign_role_conflict('T_Rust_01_C', 'Normal'))
        self.assertTrue(rules.is_shared_library_texture('T_Propdust'))
        self.assertFalse(rules.is_shared_library_texture('T_Billboard_02_A_C'))


if __name__ == '__main__':
    unittest.main()
