# Headless: blender --background --factory-startup --python tests/test_shader_pick.py
import importlib.util
import os
import sys
import unittest

import types

import numpy as np

try:
    import bpy
    HAS_BPY = hasattr(bpy, "data")
except ImportError:
    HAS_BPY = False
    bpy = types.ModuleType("bpy")
    bpy.types = types.SimpleNamespace(Operator=object, Panel=object, Menu=object, WindowManager=object)
    bpy.props = types.SimpleNamespace(**{k: (lambda *a, **kw: None) for k in (
        "FloatVectorProperty", "StringProperty", "IntProperty", "BoolProperty", "FloatProperty", "EnumProperty")})
    bpy.context = None
    mu = types.ModuleType("mathutils")
    mu.Vector = tuple
    mui = types.ModuleType("mathutils.interpolate")
    mui.poly_3d_calc = None
    mu.interpolate = mui
    sys.modules.update({"bpy": bpy, "mathutils": mu, "mathutils.interpolate": mui})

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_spec = importlib.util.spec_from_file_location("shader_pick", os.path.join(ROOT, "Shared/shader_pick.py"))
sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sp)


def _mask(name, left, right, w=8, h=8):
    img = bpy.data.images.new(name, w, h, alpha=True, float_buffer=True)
    px = np.zeros((h, w, 4), np.float32)
    px[:, : w // 2] = left
    px[:, w // 2:] = right
    img.pixels.foreach_set(px.ravel())
    return img


def _outfit_mat(img):
    m = bpy.data.materials.new("PickOutfit")
    m.use_nodes = True
    nt = m.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = img
    uvn = nt.nodes.new("ShaderNodeUVMap"); uvn.uv_map = "UVMap"
    nt.links.new(uvn.outputs[0], tex.inputs["Vector"])
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(tex.outputs[0], sep.inputs[0])
    mul = nt.nodes.new("ShaderNodeMath"); mul.name = "GT_mid_x8"; mul.operation = "MULTIPLY"
    nt.links.new(sep.outputs["Blue"], mul.inputs[0])
    return m


def _weapon_mat(img):
    m = bpy.data.materials.new("PickWeapon")
    m.use_nodes = True
    nt = m.node_tree
    ng = bpy.data.node_groups.new("FakeWeaponTexturer", "ShaderNodeTree")
    ng.interface.new_socket("Weapon ID", in_out="INPUT", socket_type="NodeSocketColor")
    g = nt.nodes.new("ShaderNodeGroup"); g.node_tree = ng
    tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = img
    nt.links.new(tex.outputs[0], g.inputs["Weapon ID"])
    return m


def _plane(mat):
    # Two quads: x in [0,1] uv u in [0,0.5]; x in [1,2] u in [0.5,1].
    me = bpy.data.meshes.new("PickPlane")
    verts = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 1, 0), (1, 1, 0), (2, 1, 0)]
    me.from_pydata(verts, [], [(0, 1, 4, 3), (1, 2, 5, 4)])
    uv = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        for li in poly.loop_indices:
            co = me.vertices[me.loops[li].vertex_index].co
            uv.uv[li].vector = (co.x / 2.0, co.y)
    me.materials.append(mat)
    ob = bpy.data.objects.new("PickPlane", me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = (5, 0, 0)
    ob["arc_model_type"] = "test"
    bpy.context.view_layer.update()
    return ob


@unittest.skipUnless(HAS_BPY, "needs Blender")
class ShaderPickTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        sp.clear_caches()

    def test_outfit_sampling_and_faces(self):
        img = _mask("m_out", (0, 0, 1 / 8, 1), (0, 0, 3 / 8, 1))
        ob = _plane(_outfit_mat(img))
        info = sp.find_zone_mask(ob.material_slots[0].material)
        self.assertEqual((info.kind, info.image.name, info.uv_map), ("outfit", "m_out", "UVMap"))
        self.assertEqual(list(sp.sample_zones(info, [(0.1, 0.5), (0.9, 0.5), (1.1, 0.5)])), [1, 3, 1])
        mat, z, name = sp.get_zone_at(ob, {"face_index": 1, "location": (6.5, 0.5, 0)})
        self.assertEqual((z, name), (3, "Colour 3"))
        mat, z, name = sp.get_zone_at(ob, {"face_index": 0, "location": (5.5, 0.5, 0)})
        self.assertEqual((z, name), (1, "Colour 1"))
        mat = ob.material_slots[0].material
        od = sp.overlay_data(ob, mat)
        self.assertEqual((od["pos"].shape, od["uv"].shape), ((12, 3), (12, 2)))
        self.assertIs(od["image"], img)
        ids = sp.overlay_zone_ids(img, "outfit")
        self.assertEqual((int(ids[0, 0]), int(ids[0, -1])), (1, 3))
        np.testing.assert_allclose(od["uv"][:, 0], od["pos"][:, 0] / 2.0, atol=1e-6)

    def test_mask_through_groups(self):
        # x8 sits in a wrapper group and takes blue from a shared block that also gets a base colour texture.
        ocm = _mask("m_ocm", (0, 0, 2 / 8, 1), (0, 0, 5 / 8, 1))
        base = _mask("m_base", (0, 0, 1, 1), (0, 0, 1, 1))
        block = bpy.data.node_groups.new("FakeLayerInputs", "ShaderNodeTree")
        block.interface.new_socket("Base", in_out="INPUT", socket_type="NodeSocketColor")
        block.interface.new_socket("OCM", in_out="INPUT", socket_type="NodeSocketColor")
        block.interface.new_socket("Blue", in_out="OUTPUT", socket_type="NodeSocketFloat")
        gi = block.nodes.new("NodeGroupInput"); go = block.nodes.new("NodeGroupOutput")
        sep = block.nodes.new("ShaderNodeSeparateColor")
        block.links.new(gi.outputs["OCM"], sep.inputs[0])
        block.links.new(sep.outputs["Blue"], go.inputs["Blue"])
        wrap = bpy.data.node_groups.new("FakeWrapper", "ShaderNodeTree")
        wrap.interface.new_socket("Base", in_out="INPUT", socket_type="NodeSocketColor")
        wrap.interface.new_socket("OCM", in_out="INPUT", socket_type="NodeSocketColor")
        wi = wrap.nodes.new("NodeGroupInput")
        bn = wrap.nodes.new("ShaderNodeGroup"); bn.node_tree = block
        wrap.links.new(wi.outputs["Base"], bn.inputs["Base"])
        wrap.links.new(wi.outputs["OCM"], bn.inputs["OCM"])
        mul = wrap.nodes.new("ShaderNodeMath"); mul.name = "DR.LayerInputs.mid.x8"; mul.operation = "MULTIPLY"
        wrap.links.new(bn.outputs["Blue"], mul.inputs[0])
        m = bpy.data.materials.new("PickWrapped"); m.use_nodes = True
        nt = m.node_tree
        wn = nt.nodes.new("ShaderNodeGroup"); wn.node_tree = wrap
        for name, img in (("Base", base), ("OCM", ocm)):
            tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = img
            nt.links.new(tex.outputs[0], wn.inputs[name])
        old = sp.wrapper_tree.graph_tree
        sp.wrapper_tree.graph_tree = lambda _t: wrap
        try:
            info = sp.find_zone_mask(m)
        finally:
            sp.wrapper_tree.graph_tree = old
        self.assertIsNotNone(info)
        self.assertEqual(info.image.name, "m_ocm")

    def test_raycast_path(self):
        img = _mask("m_out2", (0, 0, 1 / 8, 1), (0, 0, 6 / 8, 1))
        ob = _plane(_outfit_mat(img))
        times, hits = sp.bench_hover([ob], [((6.5, 0.5, 5), (0, 0, -1)), ((5.2, 0.5, 5), (0, 0, -1))])
        self.assertEqual(hits, 2)
        dg = bpy.context.evaluated_depsgraph_get()
        ok, loc, _n, fi, o, _m = bpy.context.scene.ray_cast(dg, (6.5, 0.5, 5), (0, 0, -1))
        self.assertEqual(sp.get_zone_at(o.original, {"face_index": fi, "location": loc})[1], 6)

    def test_weapon_mask(self):
        img = _mask("m_wpn", (1, 0, 0, 0), (0, 0, 1, 0))
        ob = _plane(_weapon_mat(img))
        info = sp.find_zone_mask(ob.material_slots[0].material)
        self.assertEqual(info.kind, "weapon")
        self.assertEqual(sp.overlay_data(ob, ob.material_slots[0].material)["kind"], "weapon")
        self.assertEqual(sp.get_zone_at(ob, {"face_index": 1, "location": (6.5, 0.5, 0)})[2], "Blue")

    def test_no_mask(self):
        m = bpy.data.materials.new("Plain"); m.use_nodes = True
        ob = _plane(m)
        self.assertIsNone(sp.find_zone_mask(m))
        self.assertIsNone(sp.overlay_data(ob, m))

def _decal_mat():
    m = bpy.data.materials.new("PickDecal"); m.use_nodes = True
    nt = m.node_tree
    uvn = nt.nodes.new("ShaderNodeUVMap"); uvn.uv_map = "UVMap"
    rot = nt.nodes.new("ShaderNodeMapping"); rot["dr.decal_slot"] = 1; rot["dr.decal_role"] = "rot"
    rot.inputs["Rotation"].default_value = (0, 0, 0.3)
    place = nt.nodes.new("ShaderNodeMapping"); place["dr.decal_slot"] = 1; place["dr.decal_role"] = "place"
    place.inputs["Scale"].default_value = (4.0, 3.0, 1.0)
    col = nt.nodes.new("ShaderNodeTexImage"); col["dr.decal_slot"] = 1; col["dr.decal_role"] = "color"
    nt.links.new(uvn.outputs[0], rot.inputs["Vector"])
    nt.links.new(rot.outputs[0], place.inputs["Vector"])
    nt.links.new(place.outputs[0], col.inputs["Vector"])
    use = nt.nodes.new("ShaderNodeValue"); use.name = "P:1_UseDecal"; use.outputs[0].default_value = 1.0
    return m


@unittest.skipUnless(HAS_BPY, "needs Blender")
class DecalDragTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        sp.clear_caches()

    def test_drag_esc_undo(self):
        ob = _plane(_decal_mat())
        mat = ob.material_slots[0].material
        h = sp.get_decals(mat)[0]
        pushes = []
        sp.DECAL_UNDO_PUSH = pushes.append
        _m, uv = sp.hit_uv(ob, {"face_index": 1, "location": (6.3, 0.7, 0)})
        self.assertAlmostEqual(uv[0], 0.65, 6)
        start = h.snapshot()
        drag = sp.DecalDrag(h)
        drag.move(uv)
        tc = h.tex_coord(uv)
        self.assertLess(max(abs(tc[0] - 0.5), abs(tc[1] - 0.5)), 1e-4)
        self.assertTrue(h.contains(uv))
        self.assertIs(sp.decal_at(ob, {"face_index": 1, "location": (6.3, 0.7, 0)})[0].idx, 1)
        drag.wheel(1); drag.wheel(1, ctrl=True); drag.wheel(-1, alt=True)
        self.assertNotEqual(h.snapshot(), start)
        drag.cancel()
        self.assertEqual(h.snapshot(), start)
        drag2 = sp.DecalDrag(h); drag2.move(uv); drag2.confirm(); drag2.confirm()
        self.assertEqual(pushes, ["Drag Decal"])
        h.set_mode("PROJECTED"); self.assertEqual(h.get_mode(), "PROJECTED")
        h.set_mode("UV")
        self.assertEqual(h.chain[0].inputs["Vector"].links[0].from_node.type, "UVMAP")
        sp.DECAL_UNDO_PUSH = None


class ZoneParamTests(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual([sp.zone_param(i) for i in range(8)], [f"Eq{i}" for i in range(8)])
        for bad in (-1, 8, None, 1.0, True, "0"):
            self.assertIsNone(sp.zone_param(bad))

    def test_label(self):
        self.assertEqual(sp.zone_label(8), "Tint 8")
        self.assertEqual(sp.zone_label(9), "Zone 9")


class ZoneWriteTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        fake = types.SimpleNamespace(set_param=lambda ctx, name, value: self.calls.append((ctx, name, value)) or True)
        self._orig = sp._control_ops
        sp._control_ops = lambda: fake
        self._hook = sp.SET_ZONE_COLOUR_HOOK
        sp.SET_ZONE_COLOUR_HOOK = None

    def tearDown(self):
        sp._control_ops = self._orig
        sp.SET_ZONE_COLOUR_HOOK = self._hook

    def test_write_goes_through_set_param(self):
        ctx = types.SimpleNamespace(scene=types.SimpleNamespace(arc_shader_scope="PART"))
        self.assertTrue(sp.write_zone_colour(ctx, 3, (0.1, 0.2, 0.3)))
        self.assertEqual(self.calls, [(ctx, "Eq2", (0.1, 0.2, 0.3, 1.0))])

    def test_out_of_range_skips(self):
        self.assertFalse(sp.write_zone_colour(object(), 0, (1, 1, 1)))
        self.assertEqual(self.calls, [])

    def test_operator_execute(self):
        ctx = types.SimpleNamespace(scene=types.SimpleNamespace(arc_shader_scope="WHOLE"))
        op = sp.ARC_OT_set_zone_colour()
        op.colour, op.material, op.zone = (1.0, 0.0, 0.0), "M", 1
        self.assertEqual(op.execute(ctx), {"FINISHED"})
        self.assertEqual(self.calls, [(ctx, "Eq0", (1.0, 0.0, 0.0, 1.0))])


class _Tree(dict):
    def __init__(self, name, **props):
        super().__init__(props)
        self.name = name


class WeaponKindTests(unittest.TestCase):
    def test_weapon_mapping(self):
        self.assertEqual([sp.zone_param(i, "weapon") for i in range(5)],
                         ["Base Color Red", "Base Color Green", "Base Color Blue", "Base Color Alpha", None])
        self.assertEqual(sp.zone_param(5, "outfit"), "Eq5")
        self.assertIsNone(sp.zone_param(True, "weapon"))

    def test_detect_kind(self):
        self.assertEqual(sp.detect_kind(_Tree("Independent weapon")), "weapon")
        self.assertEqual(sp.detect_kind(_Tree("DR_Weapon_Master")), "weapon")
        self.assertEqual(sp.detect_kind(_Tree("Weapon X", arc_gt_ver=5)), "outfit")
        self.assertEqual(sp.detect_kind(_Tree("GT_Outfit")), "outfit")
        self.assertEqual(sp.detect_kind(None), "outfit")

    def test_material_kind(self):
        mat = types.SimpleNamespace(node_tree=types.SimpleNamespace(nodes=[
            types.SimpleNamespace(), types.SimpleNamespace(node_tree=_Tree("Independent weapon"))]))
        self.assertEqual(sp.material_kind(mat), "weapon")
        self.assertEqual(sp.material_kind(None), "outfit")

    def test_weapon_write(self):
        calls = []
        orig = sp._control_ops
        sp._control_ops = lambda: types.SimpleNamespace(set_param=lambda c, n, v: calls.append((n, v)) or True)
        try:
            self.assertTrue(sp.write_zone_colour(None, 2, (0.5, 0.5, 0.5), "weapon"))
            self.assertFalse(sp.write_zone_colour(None, 5, (0.5, 0.5, 0.5), "weapon"))
        finally:
            sp._control_ops = orig
        self.assertEqual(calls, [("Base Color Green", (0.5, 0.5, 0.5, 1.0))])
        self.assertEqual(sp.zone_label(1, "weapon"), "Base Color Red")


if __name__ == "__main__":
    r = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in (ZoneParamTests, ZoneWriteTests, WeaponKindTests, ShaderPickTests, DecalDragTests)]))
    sys.exit(0 if r.wasSuccessful() else 1)
