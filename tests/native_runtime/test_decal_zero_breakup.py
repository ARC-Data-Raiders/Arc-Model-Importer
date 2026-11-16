import unittest

from .fake_graph import FakeGraph, rebuild


class MissingAtlasBreakupGraph(FakeGraph):
    def image(self, reference, *args, **kwargs):
        if reference == rebuild("decals").ATLAS_DETAIL:
            self.report.setdefault("missing_textures", []).append({"reference": reference})
            return None
        return super().image(reference, *args, **kwargs)


class DecalZeroBreakupTests(unittest.TestCase):
    def build(self, strength=None, texture=None, graph_type=MissingAtlasBreakupGraph):
        graph = graph_type()
        record = {
            "master": "/Game/M_DecalPreset_Transluscient_A+Detail_ColourNoise",
            "name": "MI_MedicalBag_Decals_01",
            "textures": {"Alpha Mask": "/Game/T_RaiderSymbols_Atlas_A"},
            "scalars": {} if strength is None else {"Mask Detail Intensity": strength},
            "colors": {"Color": (.25, .6, .2, 1)},
            "switches": {},
            "settings": {"BlendMode": "BLEND_Translucent"},
        }
        if texture is not None:
            record["textures"]["Variation Masks"] = texture
        rebuild("decals").build_decal(graph, record)
        return graph

    def test_zero_unbound_breakup_does_not_load_a_fabricated_texture(self):
        for strength in (None, 0, 0.0, -0.0):
            with self.subTest(strength=strength):
                graph = self.build(strength)
                self.assertFalse(graph.report.get("missing_textures"))
                self.assertNotIn("Atlas breakup R", graph.images)
                self.assertIsNotNone(graph.out)

    def test_nonzero_unbound_breakup_still_reports_its_missing_texture(self):
        for strength in (-1, .25, 1):
            with self.subTest(strength=strength):
                graph = self.build(strength)
                self.assertEqual(len(graph.report["missing_textures"]), 1)
                self.assertTrue(any("Mask Detail Intensity not applied" in warning
                                    for warning in graph.report["warnings"]))

    def test_authored_breakup_binding_stays_observable_at_zero(self):
        graph = self.build(0, "/Game/T_AuthoredVariation_GRM")
        self.assertIn("Atlas breakup R", graph.images)
        self.assertIn("T[Atlas breakup R].r", graph.out["alpha"])
        self.assertEqual(graph.params["Mask Detail Intensity"], 0)

    def test_nonzero_existing_fallback_keeps_original_math(self):
        graph = self.build(.25, graph_type=FakeGraph)
        self.assertIn("T[Atlas breakup R].r", graph.out["alpha"])
        self.assertEqual(graph.params["Mask Detail Intensity"], .25)

    def test_missing_fallback_zero_preserves_existing_output(self):
        graph = self.build(0)
        self.assertIn("T[Atlas opacity masks]", graph.out["alpha"])
        self.assertIn("P[Color]", graph.out["color"])
        self.assertEqual(graph.out["roughness"], "CLAMP(P[Roughness])")
        self.assertEqual(graph.out["metallic"], "CLAMP(P[Metalness])")


if __name__ == "__main__":
    unittest.main()
