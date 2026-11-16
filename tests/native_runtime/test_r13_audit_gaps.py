"""R13: checker gaps ported from the old sanity audit (side_checks / source_checks). Pure facts, no Blender."""
import importlib
import unittest

from .native_test_support import PACKAGE, namespace
from .test_material_audit import G, IMG, META, MI, OBJ, audit, flags

namespace()
rules = importlib.import_module(PACKAGE + '.Shared.materials.audit_param_rules')

FOREIGN = IMG('T_Billboard_02_A_C', filepath='//t/T_Billboard_02_A_C.png')
MCASES = {
    'surface_disconnected': ({'graph': G(output_linked=False)}, {}),
    'image_node_empty': ({'graph': G(images=[IMG('T_X_BC', has_image=False)])}, {}),
    'two_sided_culled': ({'mi': MI(settings={'TwoSided': True}), 'graph': G(backface_culling=True)},
                         {'mi': MI(settings={'TwoSided': True}), 'graph': G(backface_culling=False)}),
    'foreign_texture': ({'graph': G(images=[IMG('T_X_BC'), FOREIGN])},
                        {'mi': MI(textures={'BaseColor': '/Game/T_X_BC', 'Dust': '/Game/T_Billboard_02_A_C'}),
                         'graph': G(images=[IMG('T_X_BC'), FOREIGN])}),
}
OCASES = {
    'huge_bounds': ((OBJ(dims=[1.0, 2.0, 400.0]), {}), (OBJ(), {})),
    'tiny_bounds': ((OBJ(dims=[0.001, 0.001, 0.002]), {}), (OBJ(), {})),
    'section_material_mismatch': ((OBJ(slot_tris=[2, 10]), META(section_tris=[6, 6])),
                                  (OBJ(slot_tris=[6, 6]), META(section_tris=[6, 6]))),
}
SEV = {'surface_disconnected': 'defect', 'image_node_empty': 'defect', 'two_sided_culled': 'defect',
       'section_material_mismatch': 'defect', 'foreign_texture': 'inconsistency', 'huge_bounds': 'inconsistency',
       'tiny_bounds': 'inconsistency', 'blown_out': 'inconsistency'}


class R13AuditGapTests(unittest.TestCase):
    def test_material_red_green(self):
        for name, (red, green) in MCASES.items():
            with self.subTest(flag=name):
                self.assertIn(name, flags(**red))
                self.assertNotIn(name, flags(**green))

    def test_object_red_green(self):
        for name, (red, green) in OCASES.items():
            with self.subTest(flag=name):
                self.assertIn(name, audit.audit_object_facts(*red)['flags'])
                self.assertNotIn(name, audit.audit_object_facts(*green)['flags'])

    def test_huge_world_scale_mesh_is_info(self):
        r = audit.audit_object_facts(dict(OBJ(dims=[1.0, 2.0, 6000.0]), name='SM_World_02_BackdropIsland_01'), {})
        self.assertIn('huge_bounds_world', r['flags'])
        self.assertNotIn('huge_bounds', r['flags'])
        self.assertEqual(str(rules.SEVERITY['huge_bounds_world']), 'info')
        for n in ('SM_Outpost_Landscape_01', 'SM_PROXY_POI_Spaceport_LOD1', 'SM_RivenTideLOD_x7_y6',
                  'SM_MCP_Lightblocker_01', 'SM_TBG_HeightfieldCliffs_01_J'):
            self.assertTrue(rules.world_scale_mesh(n), n)
        self.assertIn('huge_bounds', audit.audit_object_facts(dict(OBJ(dims=[1.0, 2.0, 400.0]), name='SM_Crate_01'), {})['flags'])

    def test_blown_out_from_render_stats(self):
        # R37: only ``hot`` (above the lit-albedo ceiling) blocks; legacy ``blown`` (>= 1.0) is info
        self.assertIn('blown_out', audit.render_stats_flags({'coverage_px': 100, 'blown': 0.4, 'hot': 0.4}))
        self.assertNotIn('blown_out', audit.render_stats_flags({'coverage_px': 100, 'blown': 0.4}))
        self.assertNotIn('blown_out', audit.render_stats_flags({'coverage_px': 100, 'blown': 0.1, 'hot': 0.1}))
        self.assertEqual(audit.render_stats_flags({'coverage_px': 0, 'blown': 1.0}), {})

    def test_foreign_texture_allows_master_default(self):
        r = audit.audit_material_facts(G(images=[IMG('T_X_BC'), FOREIGN]), MI(), None,
                                       {'uv_layers': ['UV0'], 'color_attributes': ['Col']}, None, (),
                                       master={'package': '/Game/M_Base', 'textures': {'Dust': '/Game/T_Billboard_02_A_C'}})
        self.assertNotIn('foreign_texture', r['flags'])

    def test_layer_suffix_images_are_not_foreign(self):
        self.assertNotIn('foreign_texture', flags(graph=G(images=[IMG('T_X_BC', filepath='//t/T_X_BC_layer2.png')])))

    def test_severity(self):
        for name, sev in SEV.items():
            with self.subTest(flag=name):
                self.assertEqual(str(rules.SEVERITY.get(name, rules.DEFECT)), sev)
        r = audit.audit_material_facts(G(images=[IMG('T_X_BC'), FOREIGN]), MI(), None, {}, None, ())
        self.assertEqual(r['severity']['foreign_texture'], 'inconsistency')


XEM = '/Game/M_PropPreset_CR+NOX+XEM.M_PropPreset_CR+NOX+XEM'


def xem_mask(switches):
    mi = MI(master=XEM, switches=switches,
            textures={'BaseColor': '/Game/T_X_BC', 'Mask': '/Game/T_Default_01_White_Grayscale'})
    graph = G(images=[IMG('T_X_BC'), IMG('T_Default_01_White_Grayscale', linked=False)])
    return audit.audit_material_facts(graph, mi, None, {'uv_layers': ['UV0'], 'color_attributes': ['Col']}, None, ())['flags']


class R13XemMaskGateTests(unittest.TestCase):
    """SM_HangingArtefacts_06_A: Use Masks on, single mask off reads Packed Mask, never Mask."""

    def test_packed_permutation_gates_mask_off(self):
        f = xem_mask({'Use Masks': True, 'Use single Mask Texture': False})
        self.assertNotIn('tex_param_unlinked:Mask', f)
        self.assertIn('tex_gated_off:Mask', f)

    def test_single_mask_permutation_keeps_defect(self):
        self.assertIn('tex_param_unlinked:Mask', xem_mask({'Use Masks': True, 'Use single Mask Texture': True}))


NOM = '/Game/M_PropPreset_CR+NOM.M_PropPreset_CR+NOM'


def emissive_flags(master):
    mi = MI(master=master, switches={'Use Emissive': True}, colours={'EmissiveColor': {'R': 1, 'G': 1, 'B': 1}})
    return audit.audit_material_facts(G(emission_linked=False), mi, None,
                                      {'uv_layers': ['UV0'], 'color_attributes': ['Col']}, None, ())


class R13EmissiveRuntimeTests(unittest.TestCase):
    """CR+NOM emission mask lives in uncooked MF_Prop_Emissive: runtime, gated, with reason."""

    def test_cr_nom_marked_runtime(self):
        r = emissive_flags(NOM)
        self.assertNotIn('emissive_unwired', r['flags'])
        self.assertIn('emissive_runtime', r['flags'])
        self.assertEqual(r['severity']['emissive_runtime'], 'inconsistency')

    def test_other_master_keeps_defect(self):
        self.assertIn('emissive_unwired', emissive_flags(XEM)['flags'])


if __name__ == '__main__':
    unittest.main()
