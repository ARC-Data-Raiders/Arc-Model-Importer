"""U2 F4: grouped world ("Arc Sky") and compositor ("Arc Look", Arc LUT stages) graphs.
Runtime lookups must resolve through tree["arc_node_map"] or recursively. Runs inside Blender."""
import importlib
import json
import os
import sys
import types
import unittest
from pathlib import Path

import bpy

ROOT = Path(os.environ.get("DATARAIDERS_TEST_REPOSITORY", Path(__file__).resolve().parents[1]))
PKG = "_dr_probe_u2_f4"


def _load(name):
    for pkg, path in ((PKG, ROOT), (PKG + ".Shared", ROOT / "Shared"),
                      (PKG + ".Shared.materials", ROOT / "Shared" / "materials"),
                      (PKG + ".Lighting", ROOT / "Lighting")):
        if pkg not in sys.modules:
            mod = types.ModuleType(pkg)
            mod.__path__ = [str(path)]
            sys.modules[pkg] = mod
    return importlib.import_module(PKG + "." + name)


def _trees(tree, seen=None):
    seen = set() if seen is None else seen
    if tree is None or tree.name in seen:
        return []
    seen.add(tree.name)
    out = [tree]
    for n in tree.nodes:
        sub = getattr(n, "node_tree", None)
        if sub is not None:
            out += _trees(sub, seen)
    return out


def _solid(tree):
    return [n for n in tree.nodes if n.bl_idname not in ("NodeFrame", "NodeReroute")]


class WorldGraphTest(unittest.TestCase):
    def test_arc_sky_group(self):
        sky = _load("Lighting.lighting_sky")
        gl = _load("Shared.materials.graph_layout")
        catalog = json.loads((ROOT / "assets/lighting_scenarios.json").read_text(encoding="utf-8"))
        entry = next(e for m in catalog["maps"].values() for e in m["scenarios"].values()
                     if e.get("sky_material") and e.get("sky_sphere") and e.get("sun"))
        scene = bpy.context.scene
        saved = (scene.world, scene.render.film_transparent,
                 {k: scene[k] for k in ("arc_lighting_prev_world", "arc_lighting_prev_film_transparent") if k in scene})
        try:
            self._check_world(sky, gl, scene, entry)
        finally:
            scene.world, scene.render.film_transparent = saved[0], saved[1]
            for k in ("arc_lighting_prev_world", "arc_lighting_prev_film_transparent"):
                if k in scene:
                    del scene[k]
            for k, v in saved[2].items():
                scene[k] = v

    def _check_world(self, sky, gl, scene, entry):
        sky.ensure_scenario_sky(scene, entry, mirror_y=True)
        nt = scene.world.node_tree
        self.assertLessEqual(len(_solid(nt)), 6)
        self.assertTrue(any(n.bl_idname == "ShaderNodeGroup" and n.node_tree
                            and n.node_tree.name.startswith(sky.SKY_GROUP) for n in nt.nodes))
        for name in ("Extra_Contrast", "Overall Intensity", "Saturation"):
            self.assertIsNotNone(sky.find_node(nt, name), name)
        arc = [n for t in _trees(nt) for n in t.nodes if n.name.startswith("ArcSky")]
        self.assertTrue(arc)
        self.assertEqual(sky.find_node(nt, arc[0].name), arc[0])
        for t in _trees(nt):
            self.assertLessEqual(len(_solid(t)), 90, t.name)
            self.assertEqual(gl.assert_clean(t), [], t.name)
        # Re-apply keeps one sky group.
        sky.ensure_scenario_sky(scene, entry, mirror_y=True)
        self.assertLessEqual(len(_solid(scene.world.node_tree)), 6)


class CompositorGraphTest(unittest.TestCase):
    KEYS = ("ArcExposureBias", "ArcMeterLog2", "ArcLightingBloom", "ArcLocalExposureBlur", "ArcLighting3DLUT")

    def test_arc_look_apply_remove_reapply(self):
        post = _load("Lighting.lighting_post")
        grade = _load("Lighting.lighting_grade")
        gl = _load("Shared.materials.graph_layout")
        scene = bpy.context.scene
        settings = grade.blend_volumes([])
        lut = grade.combined_lut(settings)
        before = None
        for _ in range(2):
            post.configure(scene, settings, lut=lut, lut_identity="u2-f4", auto_exposure=True,
                           bloom=True, vignette=True, local_exposure=True)
            tree = scene.compositing_node_group
            self.assertIsNotNone(tree)
            self.assertTrue(any(getattr(n, "node_tree", None) is not None
                                and n.node_tree.name == post.LOOK_GROUP for n in tree.nodes))
            for key in self.KEYS:
                self.assertIsNotNone(post._find(tree.nodes, key), key)
            for t in _trees(tree):
                self.assertLessEqual(len(_solid(t)), 90, t.name)
                self.assertEqual(gl.assert_clean(t), [], t.name)
            count = len(bpy.data.node_groups)
            if before is not None:
                self.assertEqual(count, before)
            before = count
            post.detach(scene)
            self.assertIsNone(scene.compositing_node_group)
        # Recursive lookup without the stored map.
        post.configure(scene, settings, lut=lut, lut_identity="u2-f4")
        tree = scene.compositing_node_group
        nmap = dict(tree[post.NODE_MAP_KEY])
        self.assertIn("ArcMeterLog2", nmap)
        del tree[post.NODE_MAP_KEY]
        for key in self.KEYS:
            node = post._find(tree.nodes, key)
            self.assertIsNotNone(node, key)
            self.assertEqual(node.name, key)
        tree[post.NODE_MAP_KEY] = nmap
        post.detach(scene)

    def test_lut_stages(self):
        post = _load("Lighting.lighting_post")
        grade = _load("Lighting.lighting_grade")
        lut = grade.combined_lut(grade.blend_volumes([]))
        group = post.ensure_lut_group(post.ensure_lut_image(lut, "u2-f4-lut"))
        labels = {n.label for n in group.nodes if n.bl_idname == "CompositorNodeGroup"}
        self.assertTrue(set(post.LUT_SECTIONS) <= labels, labels)
        self.assertIn("Bilinear lower slice", group.nodes)
        self.assertIn(post.NODE_MAP_KEY, group)


if __name__ == "__main__":
    unittest.main()
