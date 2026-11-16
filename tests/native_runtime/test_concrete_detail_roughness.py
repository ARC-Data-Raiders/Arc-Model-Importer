import numbers
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    from .test_surface_layer_stages import FakeGraph, load_surfaces, record
except ImportError:
    from test_surface_layer_stages import FakeGraph, load_surfaces, record


class RoughnessGraph(FakeGraph):
    def __init__(self):
        super().__init__()
        self.samples = []

    def add(self, first, second):
        return first + second if isinstance(first, numbers.Real) and isinstance(second, numbers.Real) else super().add(first, second)

    def sub(self, first, second):
        return first - second if isinstance(first, numbers.Real) and isinstance(second, numbers.Real) else super().sub(first, second)

    def mul(self, first, second):
        return first * second if isinstance(first, numbers.Real) and isinstance(second, numbers.Real) else super().mul(first, second)

    def div(self, first, second):
        return first / second if isinstance(first, numbers.Real) and isinstance(second, numbers.Real) else super().div(first, second)

    def clamp(self, value):
        return min(1, max(0, value)) if isinstance(value, numbers.Real) else super().clamp(value)

    def mix(self, first, second, factor, label="lerp"):
        if all(isinstance(value, numbers.Real) for value in (first, second, factor)):
            return first + (second - first) * factor
        return super().mix(first, second, factor, label)

    def image(self, reference, uv=None, **kwargs):
        texture = super().image(reference, uv, **kwargs)
        if texture:
            texture["a"] = .3 if reference.endswith("Detail") else .4
            self.samples.append((reference, uv, kwargs))
        return texture

    def node(self, *args):
        return SimpleNamespace(outputs={"Normal": "geometry_normal", "Incoming": "geometry_incoming"})


class ConcreteDetailRoughnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.surfaces = load_surfaces()

    def build(self, enabled=True, strength=.5, tile=3, detail_tile=0, **other_switches):
        graph = RoughnessGraph()
        textures = {"CR": "/Game/Test/CR", "NOH": "/Game/Test/NOH", "Detail Normal Texture": "/Game/Test/Detail"}
        switches = {"Enable DetailNormal": False, "Enable Detail Albedo": False,
                    "Enable Detail Roughness": enabled, **other_switches}
        scalars = {"Detail Roughness Strength": strength, "Tiling": tile, "Detail Normal Tiling": detail_tile}
        with patch.object(self.surfaces, "_concrete_dry_fresnel", side_effect=lambda graph, rough, spec, exponent, facing: (rough, spec)):
            self.surfaces.build_architecture(graph, record("M_ArchitecturePreset_Concrete_01", textures, switches, scalars))
        return graph

    def test_roughness_only_samples_detail(self):
        graph = self.build()
        self.assertIn("/Game/Test/Detail", graph.sampled)
        self.assertNotIn("Enable Detail Roughness", graph.report.get("unsupported_features", []))

    def test_exact_alpha_roughness_equation(self):
        for strength in (0, .5, 1, 1.5):
            with self.subTest(strength=strength):
                graph = self.build(strength=strength)
                self.assertAlmostEqual(graph.finished["rough"], .4 + strength * (2 * .3 * .4 - .4))

    def test_zero_detail_tiling_is_preserved(self):
        graph = self.build()
        sample = next(item for item in graph.samples if item[0].endswith("Detail"))
        self.assertEqual(sample[1], graph.transform_uv(0, 0))
        self.assertIs(sample[2]["srgb"], False)

    def test_switch_off_keeps_base_roughness(self):
        graph = self.build(enabled=False)
        self.assertNotIn("/Game/Test/Detail", graph.sampled)
        self.assertAlmostEqual(graph.finished["rough"], .4)

    def test_roughness_does_not_change_normal_or_albedo(self):
        enabled, disabled = self.build(), self.build(enabled=False)
        self.assertEqual(enabled.finished["normal"], disabled.finished["normal"])
        self.assertEqual(enabled.finished["color"], disabled.finished["color"])

    def test_shared_detail_sample(self):
        graph = self.build(**{"Enable DetailNormal": True, "Enable Detail Albedo": True})
        self.assertEqual(graph.sampled.count("/Game/Test/Detail"), 1)


if __name__ == "__main__":
    unittest.main()
