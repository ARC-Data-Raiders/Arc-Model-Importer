import importlib.util
import os
import unittest

_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Shared/shader_tweaks.py")
_spec = importlib.util.spec_from_file_location("shader_tweaks", _path)
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)


class Sock:
    def __init__(self, v):
        self.default_value = v


class Node:
    def __init__(self, **inputs):
        self.inputs = {k: Sock(v) for k, v in inputs.items()}


class T(unittest.TestCase):
    def test_record_and_default_removal(self):
        r = {}
        st.record(r, "all", "Rough", 0.7, 0.5)
        self.assertEqual(st.get(r), {"all": {"Rough": 0.7}})
        st.record(r, "all", "Rough", 0.500001, 0.5)
        self.assertEqual(st.get(r), {})
        self.assertNotIn(st.PROP, r)

    def test_colour(self):
        r = {}
        st.record(r, "Body", "Tint", [1, 0, 0, 1], [1, 1, 1, 1])
        self.assertEqual(st.get(r)["Body"]["Tint"], [1.0, 0.0, 0.0, 1.0])
        st.record(r, "Body", "Tint", [1, 1, 1, 1], [1, 1, 1, 1])
        self.assertEqual(st.get(r), {})

    def test_reset(self):
        r = {}
        st.record(r, "all", "A", 1.0, 0.0)
        st.record(r, "all", "B", 1.0, 0.0)
        st.record(r, "P", "A", 1.0, 0.0)
        st.reset(r, "all", "A")
        self.assertEqual(st.get(r), {"all": {"B": 1.0}, "P": {"A": 1.0}})
        st.reset(r, "P")
        self.assertEqual(st.get(r), {"all": {"B": 1.0}})
        st.reset(r)
        self.assertEqual(st.get(r), {})

    def test_apply(self):
        r = {}
        st.record(r, "all", "Rough", 0.2, 0.5)
        st.record(r, "all", "Missing", 1.0, 0.0)
        n = Node(Rough=0.5)
        skipped = st.apply(r, lambda s: [("mat", n)] if s == "all" else [])
        self.assertEqual(n.inputs["Rough"].default_value, 0.2)
        self.assertEqual(skipped, ["Missing"])

    def test_json_roundtrip(self):
        r = {}
        st.record(r, "all", "Rough", 0.2, 0.5)
        text = st.to_json(r)
        r2 = {}
        st.from_json(r2, text)
        self.assertEqual(st.get(r2), st.get(r))
        with self.assertRaises(ValueError):
            st.from_json(r2, '{"version":2,"tweaks":{}}')


    def test_sync(self):
        r = {}
        st.record(r, "A", "X", 1.0, 0.0)
        st.record(r, "B", "X", 1.0, 0.0)
        st.record(r, "B", "Y", 2.0, 0.0)
        st.record(r, "C", "X", 3.0, 0.0)
        self.assertEqual(st.sync_preview(r, "A", ["B", "C", "D"]), [("B", ["Y"]), ("C", ["X"])])
        st.sync(r, "A", ["B", "C", "D"])
        d = st.get(r)
        self.assertEqual(d["B"], {"X": 1.0})
        self.assertEqual(d["C"], {"X": 1.0})
        self.assertEqual(d["D"], {"X": 1.0})


if __name__ == "__main__":
    unittest.main()
