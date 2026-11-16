"""R16: opacity/emissive gated rows. Root-master runtime reasons, BSDF constant alpha, black emissive default."""
import importlib
import unittest
from types import SimpleNamespace as NS

from .native_test_support import PACKAGE, namespace
from .test_material_audit import G, IMG, MI, audit

namespace()
rules = importlib.import_module(PACKAGE + '.Shared.materials.audit_param_rules')

MESH = {'uv_layers': ['UV0'], 'color_attributes': ['Col']}
XEM = '/Game/M_PropPreset_CR+NOX+XEM.M_PropPreset_CR+NOX+XEM'


def run(mi, graph):
    return audit.audit_material_facts(graph, mi, None, MESH, None, ())


class R16OpacityTests(unittest.TestCase):
    def test_masked_xem_runtime(self):
        r = run(MI(master=XEM, settings={'BlendMode': 'BLEND_Masked'}), G())
        self.assertNotIn('opacity_unwired', r['flags'])
        self.assertIn('opacity_runtime', r['flags'])
        self.assertEqual(r['severity']['opacity_runtime'], 'inconsistency')

    def test_base_master_keeps_defect(self):
        r = run(MI(settings={'BlendMode': 'BLEND_Masked'}), G())
        self.assertIn('opacity_unwired', r['flags'])

    def test_translucent_constant_alpha_is_info(self):
        r = run(MI(settings={'BlendMode': 'BLEND_Translucent'}), G(render_method='BLENDED', alpha_constant=0.35))
        self.assertNotIn('opacity_unwired', r['flags'])
        self.assertEqual(r['severity'].get('opacity_constant'), 'info')

    def test_graph_facts_reads_unlinked_alpha(self):
        sock = NS(name='Alpha', is_linked=False, default_value=0.35, links=[])
        bsdf = NS(type='BSDF_PRINCIPLED', inputs=[sock], outputs=[], label='')
        tree = NS(name='T', nodes=[bsdf])

        class Mat(dict):
            name, use_nodes, node_tree = 'M', True, tree
        self.assertAlmostEqual(audit.graph_facts(Mat())['alpha_constant'], 0.35)


class R16EmissiveTests(unittest.TestCase):
    def tex(self, overridden):
        return [{'name': 'BaseColor', 'path': '/Game/T_X_BC', 'overridden': True},
                {'name': 'Emissive', 'path': '/Game/T_Base_Black_D', 'overridden': overridden}]

    def test_black_default_off(self):
        r = run(MI(textures=self.tex(False), scalars={'Emissive Intesity': 40.0}), G())
        self.assertNotIn('emissive_unwired', r['flags'])
        self.assertIn('emissive_default_off', r['flags'])

    def test_overridden_emissive_keeps_defect(self):
        r = run(MI(textures=self.tex(True), scalars={'Emissive Intesity': 40.0}), G())
        self.assertIn('emissive_unwired', r['flags'])

    def test_runtime_masters(self):
        for base in ('M_Vegetation_01', 'M_BrokenGlassSDF', 'M_Beacon'):
            with self.subTest(master=base):
                r = run(MI(master='/Game/%s.%s' % (base, base), scalars={'EmissiveIntensity': 4.0}), G())
                self.assertNotIn('emissive_unwired', r['flags'])
                self.assertIn('emissive_runtime', r['flags'])


class R16GateLayoutTests(unittest.TestCase):
    def test_nam_normal_layout_known(self):
        layouts = importlib.import_module(PACKAGE + '.Shared.materials.texture_layouts')
        self.assertTrue(layouts.is_known('PM_Normals', 'nam'))
        self.assertTrue(layouts.is_known('Normal', 'NAM'))

    def facts(self, master, param, switches):
        return {'master': master, 'textures': {param: {'path': '/Game/T_X_X', 'overridden': True}},
                'switches': {k: {'value': v, 'overridden': True} for k, v in switches.items()}}

    def test_nom_mask_gated_by_use_mask_texture(self):
        m = '/Game/M_PropPreset_CR+NOM.M_PropPreset_CR+NOM'
        flag, sev, _ = rules.classify_unlinked('Mask', self.facts(m, 'Mask', {'Use Mask Texture': False}))
        self.assertEqual((flag, sev), ('tex_gated_off:Mask', 'info'))
        flag, sev, _ = rules.classify_unlinked('Mask', self.facts(m, 'Mask', {'Use Mask Texture': True}))
        self.assertEqual(flag, 'tex_param_unlinked:Mask')

    def test_metal_additional_rust_mask_gated(self):
        m = '/Game/M_ArchitecturePreset_Metal.M_ArchitecturePreset_Metal'
        p = '3.1 Additional Rust Mask'
        flag, sev, _ = rules.classify_unlinked(p, self.facts(m, p, {'3. Use Additional Rust Mask': False}))
        self.assertEqual((flag, sev), ('tex_gated_off:' + p, 'info'))


if __name__ == '__main__':
    unittest.main()
