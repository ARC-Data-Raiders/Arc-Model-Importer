import unittest

from native_runtime.fake_graph import FakeGraph, rebuild


MASTER = "/Game/Pioneer/MaterialLibrary/M_Presets/M_DecalPreset_Translucent_Atlas+Detail"


class AtlasDetailColorTests(unittest.TestCase):
    def build(self, master=MASTER, ca="/Game/Textures/T_Decal_CA", switches=None):
        graph = FakeGraph()
        record = {"master": master, "name": "MI_Test",
                  "textures": {"CA": ca, "Mask": "/Game/Textures/T_Decal_M"},
                  "scalars": {}, "colors": {"BC Color": (.2, .3, .4, 1)},
                  "switches": dict(switches or {}), "settings": {"BlendMode": "BLEND_Translucent"}}
        rebuild("decals").build_decal(graph, record)
        return graph

    def test_actual_translucent_atlas_master_samples_ca_for_base_color(self):
        graph = self.build()
        self.assertIn("Atlas colour and alpha", graph.images)
        self.assertIn("T[Atlas colour and alpha].rgb", graph.out["color"])

    def test_compiled_ca_alpha_does_not_replace_mask_opacity(self):
        graph = self.build()
        self.assertNotIn("T[Atlas colour and alpha].a", graph.out["alpha"])
        self.assertEqual(graph.out["alpha"], self.build(ca="").out["alpha"])

    def test_color_uses_compiled_desaturation_and_multiply_parameters(self):
        graph = self.build()
        self.assertIn("Color Desaturation", graph.params)
        self.assertIn("Color Multiply", graph.params)
        self.assertIn("P[Color Multiply]", graph.out["color"])
        self.assertIn("0.212639", graph.out["color"])
        self.assertNotIn("P[BC Color]", graph.out["color"])

    def test_approximate_color_stages_remain_visible(self):
        graph = self.build()
        self.assertTrue(any("compiled fixed colour overlay" in warning for warning in graph.report["warnings"]))
        self.assertIn("CA colour", graph.report["implemented_stages"])
        self.assertNotIn("authored BC Color", graph.report["implemented_stages"])

    def test_missing_overlay_warning_is_not_hidden_by_ca_stage(self):
        graph = self.build(switches={"Use Color Overlay": True})
        self.assertIn("Atlas branch not reconstructed: Use Color Overlay", graph.report["warnings"])

    def test_authored_bc_is_preserved_when_ca_is_missing(self):
        graph = self.build(ca="")
        self.assertIn("P[BC Color]", graph.out["color"])
        self.assertTrue(any("Atlas CA colour texture missing" in warning for warning in graph.report["warnings"]))

    def test_masked_no_contact_branch_keeps_authored_color(self):
        graph = self.build(MASTER.replace("Translucent", "Masked") + "_NoContact")
        self.assertNotIn("Atlas colour and alpha", graph.images)
        self.assertNotIn("Atlas colour and alpha", graph.out["color"])

    def test_missing_ca_does_not_discard_existing_opacity_mask(self):
        graph = self.build(ca="")
        self.assertIn("Atlas opacity masks", graph.images)
        self.assertIn("T[Atlas opacity masks]", graph.out["alpha"])


if __name__ == "__main__":
    unittest.main()
