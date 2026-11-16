"""Graph nodes added after finish() must not reuse frames finalize_tree removed (needs bpy).

Enemy CR+NOM samples the cubemap after finish(); finalize_tree replaces the stage frames,
so a cached frame reference dangles and parenting to it crashed Blender (access violation).
"""
import importlib
import sys
import unittest
from pathlib import Path

try:
    import bpy
except ImportError:
    bpy = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(bpy is None, "needs Blender")
class InteropPostFinishFrameTests(unittest.TestCase):
    def setUp(self):
        if str(ROOT.parent) not in sys.path:
            sys.path.insert(0, str(ROOT.parent))
        self.interop = importlib.import_module(ROOT.name + ".Shared.materials.native.interop")
        self.mat = bpy.data.materials.new("DR_post_finish_frames")

    def tearDown(self):
        tree = self.mat.node_tree
        groups = [n.node_tree for n in tree.nodes if n.type == "GROUP" and n.node_tree] if tree else []
        bpy.data.materials.remove(self.mat)
        for g in groups:
            if g.users == 0:
                bpy.data.node_groups.remove(g)

    def test_node_after_finish_gets_live_frame(self):
        g = self.interop.Graph(self.mat, None, None, {"warnings": []})
        with g.block("Source"):
            src = g.value(0.5, "source value")
        g.finish((0.5, 0.5, 0.5, 1.0), roughness=src)
        stages_before = list(g._state["frames"])
        with g.block("Source"):
            late = g.node("ShaderNodeTexImage", "late cubemap")
        frame = late.parent
        self.assertIsNotNone(frame)
        self.assertEqual(frame.bl_idname, "NodeFrame")
        self.assertIn(frame.name, g.tree.nodes)
        self.assertEqual(g.tree.nodes[frame.name].as_pointer(), frame.as_pointer())
        self.assertEqual(list(g._state["frames"])[: len(stages_before)], stages_before)


if __name__ == "__main__":
    unittest.main()
