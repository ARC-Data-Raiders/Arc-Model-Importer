"""Face response tree must fold into plain stage groups that meet the graph contract (needs bpy).

The headless capture skips the head import, so this builds DR.Face from a synthetic record.
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
STAGES = {"Skin Base", "Ears and Fresnel", "Zone Roughness", "Lips", "Micro Normal"}


def _abs_x(n):
    la = getattr(n, "location_absolute", None)
    if la is not None:
        return float(la[0])
    x, p = float(n.location[0]), n.parent
    while p is not None:
        x, p = x + p.location[0], p.parent
    return x


def _record():
    return {"scalars": {}, "colors": {}, "switches": {"UseHair": True}, "sources": {}, "textures": {}}


@unittest.skipIf(bpy is None, "needs Blender")
class FaceLayoutContractTests(unittest.TestCase):
    def setUp(self):
        if str(ROOT.parent) not in sys.path:
            sys.path.insert(0, str(ROOT.parent))
        self.face = importlib.import_module(ROOT.name + ".Shared.materials.character_face")
        self.gl = importlib.import_module(ROOT.name + ".Shared.materials.graph_layout")
        self.before = set(bpy.data.node_groups.keys())
        self.tree = bpy.data.node_groups.new("DR.Face.U2F3.Head_Setup", "ShaderNodeTree")

    def tearDown(self):
        for name in list(bpy.data.node_groups.keys()):
            if name not in self.before:
                bpy.data.node_groups.remove(bpy.data.node_groups[name])

    def _walk(self, tree, seen=None):
        seen = seen if seen is not None else set()
        if tree is None or tree.name in seen:
            return []
        seen.add(tree.name)
        out = [tree]
        for n in tree.nodes:
            if n.type == "GROUP":
                out += self._walk(n.node_tree, seen)
        return out

    def test_face_tree_meets_contract(self):
        tree = self.face.build_face_tree(self.tree, _record())
        top = [n for n in tree.nodes if n.bl_idname not in ("NodeFrame", "NodeReroute")]
        self.assertLessEqual(len(top), 12, [n.name for n in top])
        names = {n.node_tree.name for n in tree.nodes if n.type == "GROUP" and n.node_tree}
        for stage in STAGES:
            self.assertTrue(any(nm.startswith(stage) for nm in names), (stage, sorted(names)))
        self.assertLessEqual(self.gl.tree_depth(tree), 4)
        for t in self._walk(tree):
            body = [n for n in t.nodes if n.bl_idname != "NodeFrame"]
            self.assertLessEqual(len(body), 90, t.name)
            xs = [_abs_x(n) for n in body]
            right = [_abs_x(n) + n.width for n in body]
            self.assertLessEqual(max(right) - min(xs), 6000, t.name)
            self.assertEqual(self.gl.assert_clean(t), [], t.name)

    def test_face_runtime_lookups_survive(self):
        tree = self.face.build_face_tree(self.tree, _record())
        # sss_values and the scalp bridge find these at the top level.
        self.assertIsNotNone(next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None))
        outs = {i.name for i in tree.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "OUTPUT"}
        self.assertTrue({"BSDF", "Native Color", "Native Face Fresnel"} <= outs)


if __name__ == "__main__":
    unittest.main()
