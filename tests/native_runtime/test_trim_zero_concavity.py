import unittest

from .fake_graph import FakeGraph, rebuild


class MissingConcavityGraph(FakeGraph):
    def image(self, ref, *args, **kwargs):
        if kwargs.get("label") == "Fixed trim concavity mask":
            raise FileNotFoundError(ref)
        return super().image(ref, *args, **kwargs)


class TrimZeroConcavityTests(unittest.TestCase):
    def record(self, strength):
        scalars = {} if strength is None else {"ConcaveNormal": strength}
        return {"master": "/Game/M_Tileable_Metal_CR+NXX", "name": "MI_Corrugated",
                "scalars": scalars, "colors": {}, "switches": {"Use Slice Mask": False},
                "textures": {"CR Texture": "/Game/T_CR", "NXX/NMX Texture": "/Game/T_NOH"}}

    def test_disabled_concavity_imports_without_the_unused_mask(self):
        for strength in (None, 0, 0.0):
            with self.subTest(strength=strength):
                graph = MissingConcavityGraph()
                rebuild("prop_trim").build_prop_trim(graph, self.record(strength))
                self.assertIsNotNone(graph.out["normal"])
                self.assertNotIn("Fixed trim concavity mask", graph.images)

    def test_nonzero_concavity_still_requires_its_mask(self):
        for strength in (-1, 0.25, 1):
            with self.subTest(strength=strength):
                with self.assertRaises(FileNotFoundError):
                    rebuild("prop_trim").build_prop_trim(MissingConcavityGraph(), self.record(strength))

    def test_nonzero_concavity_keeps_its_authored_strength_and_graph(self):
        graph = FakeGraph()
        rebuild("prop_trim").build_prop_trim(graph, self.record(0.25))
        self.assertEqual(graph.params["ConcaveNormal"], 0.25)
        self.assertIn("Fixed trim concavity mask", graph.images)
        self.assertIn("T[Fixed trim concavity mask].r", str(graph.out["normal"]))


if __name__ == "__main__":
    unittest.main()
