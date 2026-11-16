import math
import types
import unittest

from native_runtime.fake_graph import FakeGraph, rebuild
from native_runtime.test_generic_families import rec


MASTER = "/Game/Pioneer/Environment/Nature/Locations/FrozenTrail/Snow/M_SnowSkirt_Decal_01"


class NumericGraph(FakeGraph):
    def __init__(self, texel=(.7, .4, .6, .25), uv=(.3, .4, 0), scale=(1, 1, 1)):
        super().__init__()
        self.texel = texel
        self.coords = uv
        self.scale = scale
        self.requests = []
        self.obj = types.SimpleNamespace(matrix_world=types.SimpleNamespace(to_scale=lambda: self.scale))

    def parameter(self, label, value, typ="Float"):
        self.params[label] = value
        return value

    def color(self, value, label=""):
        return tuple(value)

    def uv(self, channel=0):
        return self.coords

    def split(self, value):
        return tuple(value[:3])

    def combine(self, first, second, third=0):
        return first, second, third

    def math(self, op, first, second=0, third=0, label=""):
        return {"ADD": lambda: first + second, "SUBTRACT": lambda: first - second,
                "MULTIPLY": lambda: first * second, "DIVIDE": lambda: first / second,
                "GREATER_THAN": lambda: float(first > second),
                "MAXIMUM": lambda: max(first, second), "SQRT": lambda: math.sqrt(first)}[op]()

    def clamp(self, value):
        return min(max(value, 0), 1)

    def vector(self, op, first, second=(0, 0, 0), scale=1):
        if op == "SCALE":
            return tuple(value * scale for value in first)
        if op == "NORMALIZE":
            length = math.sqrt(sum(value * value for value in first))
            return tuple(value / length for value in first)
        raise AssertionError(op)

    def image(self, reference, uv=None, **kwargs):
        self.requests.append((reference, uv, kwargs))
        if not reference:
            return None
        self.images.append(kwargs.get("label"))
        return dict(zip(("r", "g", "b", "a"), self.texel))


class SnowSkirtTests(unittest.TestCase):
    def build(self, scalars=None, texel=(.7, .4, .6, .25), uv=(.3, .4, 0)):
        graph = NumericGraph(texel, uv)
        record = rec(MASTER, ["NX"], scalars, settings={"BlendMode": "BLEND_Masked"})
        rebuild("generic_families").build_snow_pile_basic(graph, record)
        return graph

    def test_real_master_samples_nx_without_requesting_nonexistent_basetextrue(self):
        graph = self.build()
        self.assertTrue(graph.requests)
        self.assertTrue(all(reference for reference, _, _ in graph.requests))
        self.assertIn("NX", graph.images)
        self.assertIsNotNone(graph.out["normal"])

    def test_shader_normal_strength_lerps_reconstructed_z_before_normalization(self):
        for strength in (0, .318297, 1, 1.7):
            with self.subTest(strength=strength):
                graph = self.build({"Normal Strength": strength})
                decoded = (.4 * strength, -.2 * strength, 1 + (math.sqrt(.8) - 1) * strength)
                length = math.sqrt(sum(value * value for value in decoded))
                self.assertIsNotNone(graph.out["normal"])
                for actual, expected in zip(graph.out["normal"], decoded):
                    self.assertAlmostEqual(actual, expected / length)

    def test_compiled_base_color_does_not_require_an_albedo_texture(self):
        graph = self.build()
        self.assertEqual(graph.out["color"][:3], (.854993, .846873, .887923))
        self.assertNotIn("ColorA", graph.params)
        self.assertEqual(graph.out["metallic"], 0)

    def test_compiled_uv0_tiling_drives_nx(self):
        graph = self.build({"Tiling U": 2, "Tiling V": 3})
        for actual, expected in zip(graph.requests[0][1], (.6, 1.2, 0)):
            self.assertAlmostEqual(actual, expected)

    def test_object_scale_axis_and_uv_mask_follow_cooked_branches(self):
        for axis, expected_scale in ((0, 2), (1, 3), (2, 4)):
            for mask, coords, enabled in ((0, (.3, .4, 0), True), (1, (1.1, .4, 0), False),
                                         (2, (.3, 1.1, 0), False), (1, (1, .4, 0), True)):
                with self.subTest(axis=axis, mask=mask, coords=coords):
                    graph = NumericGraph(uv=coords, scale=(2, 3, 4))
                    record = rec(MASTER, ["NX"], {"UV Mode": 1, "Scale Axis": axis, "Mask on UVs": mask})
                    rebuild("generic_families").build_snow_pile_basic(graph, record)
                    self.assertAlmostEqual(graph.requests[0][1][0], coords[0] * (expected_scale if enabled else 1))
                    self.assertEqual(graph.requests[0][1][1], coords[1])

    def test_analytic_opacity_uses_blue_not_nx_alpha(self):
        graph = self.build({"Opacity": 1, "Opacity Min": .2, "Opacity Max": .9,
                            "Fade Min": 0, "Fade Max": 1, "BreakupRange": 0},
                           texel=(.5, .5, .6, 0), uv=(.3, .4, 0))
        self.assertEqual(graph.out["alpha"], 1)
        hidden = self.build({"Fade Min": 0, "Fade Max": 1, "BreakupRange": 0}, uv=(.3, 1, 0))
        self.assertEqual(hidden.out["alpha"], 0)
        self.assertTrue(any("dither" in warning for warning in graph.report["warnings"]))

    def test_existing_snow_pile_contract_stays_separate(self):
        graph = FakeGraph()
        rebuild("generic_families").build_snow_pile_basic(graph, rec("/Game/M_SnowPile_01", ["BaseTextrue"]))
        self.assertEqual(graph.images, ["BaseTextrue"])
        # T_Snow_NRH: RG normal (MF_NormalReconstructZ), so the pile now binds a normal from the same sample.
        self.assertIn("T[BaseTextrue].r", graph.out["normal"])


if __name__ == "__main__":
    unittest.main()
