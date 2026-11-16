"""U2 F5: layer-stack chain mux equals the balanced selector (one-hot) and the general
contribution model (fractional weights); build_chain makes one tagged Mix per zone.
Runs inside Blender."""
import importlib
import itertools
import os
import random
import sys
import types
import unittest
from pathlib import Path

import bpy

ROOT = Path(os.environ.get("DATARAIDERS_TEST_REPOSITORY", Path(__file__).resolve().parents[1]))
PKG = "_dr_probe_u2_f5"
TOL = 1e-6


def _load(name):
    for pkg, path in ((PKG, ROOT), (PKG + ".Shared", ROOT / "Shared"),
                      (PKG + ".Shared.materials", ROOT / "Shared" / "materials")):
        if pkg not in sys.modules:
            mod = types.ModuleType(pkg)
            mod.__path__ = [str(path)]
            sys.modules[pkg] = mod
    return importlib.import_module(PKG + "." + name)


def _close(a, b):
    if isinstance(a, tuple):
        return len(a) == len(b) and all(abs(x - y) <= TOL for x, y in zip(a, b))
    return abs(a - b) <= TOL


class ChainModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sel = _load("Shared.materials.dr_primary_graph.selection")
        cls.gen = _load("Shared.materials.dr_primary_graph.general")

    def _cases(self, rng, vec):
        for k in range(1, 9):
            for zones in itertools.combinations(range(1, 9), k):
                if vec:
                    vals = {z: tuple(rng.uniform(-2, 2) for _ in range(3)) for z in zones}
                    default = tuple(rng.uniform(-2, 2) for _ in range(3))
                else:
                    vals = {z: rng.uniform(-2, 2) for z in zones}
                    default = rng.uniform(-2, 2)
                yield zones, vals, default

    def test_one_hot_chain_equals_balanced(self):
        rng = random.Random(5)
        n = 0
        for vec in (False, True):
            for zones, vals, default in self._cases(rng, vec):
                for hot in list(zones) + [None]:
                    w = {z: 1. if z == hot else 0. for z in zones}
                    a = self.sel.chain_value(zones, vals, w, default)
                    b = self.sel.balanced_value(zones, vals, w, default)
                    self.assertTrue(_close(a, b), (zones, hot, a, b))
                    n += 1
        self.assertGreater(n, 1000)

    def test_general_weights_chain_equals_contribution(self):
        rng = random.Random(7)
        for vec in (False, True):
            for zones, vals, default in self._cases(rng, vec):
                f = {z: rng.choice((0., 1., rng.random(), rng.uniform(-.5, 1.5))) for z in zones}
                a = self.sel.chain_value(zones, vals, f, default)
                b = self.gen.contribution_value(zones, vals, f, default)
                self.assertTrue(_close(a, b), (zones, f, a, b))


class ChainBuildTests(unittest.TestCase):
    def test_one_tagged_mix_per_zone(self):
        sel = _load("Shared.materials.dr_primary_graph.selection")
        tree = bpy.data.node_groups.new("_dr_f5_chain_probe", "ShaderNodeTree")
        try:
            nodes, links = tree.nodes, tree.links
            idx = nodes.new("ShaderNodeValue")
            start = nodes.new("ShaderNodeValue")
            socks = {}
            for z in (1, 3, 4):
                v = nodes.new("ShaderNodeValue")
                v.outputs[0].default_value = float(z)
                socks[z] = v.outputs[0]
            before = len(nodes)
            out = sel.build_chain(nodes, links, idx.outputs[0], socks, dtype="FLOAT",
                                  start=start.outputs[0], prefix="Roughness")
            mixes = [n for n in nodes if n.bl_idname == "ShaderNodeMix"]
            self.assertEqual(sorted(n[sel.CHAIN_ZONE] for n in mixes), [1, 3, 4])
            self.assertTrue(all(n.get(sel.CHAIN_PREFIX) == "Roughness" for n in mixes))
            self.assertEqual(len(nodes) - before, 6)
            roots = [n for n in mixes if n.get("dr.chain.root")]
            self.assertEqual(len(roots), 1)
            self.assertEqual(roots[0][sel.CHAIN_ZONE], 4)
            self.assertEqual(out.node.name, roots[0].name)
        finally:
            bpy.data.node_groups.remove(tree)


if __name__ == "__main__":
    unittest.main()
