"""U2 F1: one shared outfit zone colour group, per-zone values on the instances (needs bpy)."""
import re
import sys
import unittest
from pathlib import Path

try:
    import bpy
except ImportError:
    bpy = None

sys.path.insert(0, str(Path(__file__).resolve().parent / 'native_runtime'))
from native_test_support import namespace, PACKAGE

namespace()
import importlib

if bpy is not None:
    gt = importlib.import_module(PACKAGE + '.Shared.materials.gt_outfit_pbr')
    dlc = importlib.import_module(PACKAGE + '.Shared.materials.dr_layered_composer')
    ops = importlib.import_module(PACKAGE + '.Shared.materials.dr_primary_operations')
    uc = importlib.import_module(PACKAGE + '.Shared.materials.user_controls')

_ZONES = {
    1: dict(swatch=(1., 0., 0., 1.), color_a=(.1, .2, .3, 1.), color_b=(.4, .5, .6, 1.), color_c=(.7, .8, .9, 1.), overlay=(1., 1., 1., 1.)),
    2: dict(swatch=(0., 1., 0., 1.), color_a=(.9, .1, .1, 1.), color_b=(.2, .9, .2, 1.), color_c=(.3, .3, .9, 1.), overlay=(.5, .5, .5, 1.), color_a2=(.05, .05, .05, 1.)),
}


def _close(a, b):
    return all(abs(float(x) - float(y)) < 1e-5 for x, y in zip(a, b))


@unittest.skipIf(bpy is None, 'needs bpy')
class SharedPaletteTests(unittest.TestCase):
    def setUp(self):
        old = bpy.data.node_groups.get(dlc.PALETTE_GROUP)
        if old is not None:
            bpy.data.node_groups.remove(old)

    def _material(self, name, zones=_ZONES):
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        nodes = []
        with ops.operation_session():
            for zone, kw in zones.items():
                tree = gt.build_gt_colour_n_zone_group(zone=zone, amt=0.75, tree_key=name, **kw)
                node = mat.node_tree.nodes.new('ShaderNodeGroup')
                node.node_tree = tree
                dlc.apply_palette_values(node, dlc.palette_values(gt, **kw), 0.75)
                nodes.append(node)
        return mat, nodes

    def test_one_shared_group_with_plain_name(self):
        before = len(bpy.data.node_groups)
        mat1, n1 = self._material('F1 Palette A')
        mat2, n2 = self._material('F1 Palette B')
        trees = {n.node_tree.name for n in n1 + n2}
        self.assertEqual(trees, {dlc.PALETTE_GROUP})
        self.assertEqual(len(bpy.data.node_groups), before + 1)
        self.assertIsNone(re.search(r'[0-9a-f]{8,}|\.\d{3}$', dlc.PALETTE_GROUP))

    def test_instance_values_reach_sockets(self):
        _mat, nodes = self._material('F1 Palette C')
        for node, (zone, kw) in zip(nodes, _ZONES.items()):
            self.assertTrue(_close(node.inputs['Zone swatch'].default_value, kw['swatch']))
            self.assertTrue(_close(node.inputs['Colour A'].default_value, kw['color_a']))
            self.assertTrue(_close(node.inputs['Colour A2'].default_value, kw.get('color_a2', kw['color_a'])))
            self.assertTrue(_close(node.inputs['Colour C2'].default_value, kw['color_c']))
            self.assertTrue(_close(node.inputs['Colour overlay'].default_value, kw['overlay']))
            self.assertAlmostEqual(node.inputs['Amt'].default_value, 0.75, places=5)
        self.assertFalse(_close(nodes[0].inputs['Colour A'].default_value, nodes[1].inputs['Colour A'].default_value))

    def test_every_colour_input_is_used(self):
        _mat, nodes = self._material('F1 Palette D')
        tree = nodes[0].node_tree
        gi = next(n for n in tree.nodes if n.type == 'GROUP_INPUT')
        for _key, sock in dlc.PALETTE_INPUTS + [('', 'ColorMask'), ('', 'PackedR'), ('', 'Amt')]:
            self.assertTrue(gi.outputs[sock].is_linked, sock)
        go = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
        self.assertTrue(go.inputs['Colour'].is_linked)

    def test_palette_adds_no_controls_and_look_controls_still_write(self):
        mat, nodes = self._material('F1 Palette E')
        self.assertFalse(uc.schema_is_control_tree(nodes[0].node_tree))
        look = bpy.data.node_groups.new('Arc F1 Outfit Look', 'ShaderNodeTree')
        look.interface.new_socket('Wear', in_out='INPUT', socket_type='NodeSocketFloat').default_value = 0.2
        look.interface.new_socket('Tint', in_out='INPUT', socket_type='NodeSocketColor')
        look.interface.new_socket('Shader', in_out='OUTPUT', socket_type='NodeSocketShader')
        ln = mat.node_tree.nodes.new('ShaderNodeGroup')
        ln.node_tree = look
        ids = {r['id'] for r in uc.schema(mat)}
        self.assertIn('Wear', ids)
        self.assertIn('Tint', ids)
        for _key, sock in dlc.PALETTE_INPUTS:
            self.assertNotIn(sock, ids)
        self.assertEqual(uc.schema_set(mat, 'Wear', 0.6), 1)
        uc.schema_set(mat, 'Tint', (0.3, 0.2, 0.1, 1.0))
        self.assertAlmostEqual(ln.inputs['Wear'].default_value, 0.6, places=5)
        self.assertTrue(_close(ln.inputs['Tint'].default_value, (0.3, 0.2, 0.1, 1.0)))
        # zone colours untouched by the look write
        self.assertTrue(_close(nodes[0].inputs['Colour A'].default_value, _ZONES[1]['color_a']))


def _zone_inputs(mat):
    nt = mat.node_tree
    for key, col in (('ColorA', (.1, .2, .3, 1.)), ('ColorB', (.4, .5, .6, 1.)), ('ColorC', (.7, .8, .9, 1.))):
        n = nt.nodes.new('ShaderNodeRGB')
        n.name = key
        n.outputs[0].default_value = col
    swatch = nt.nodes.new('ShaderNodeRGB')
    mask = nt.nodes.new('ShaderNodeTexImage')
    packed = nt.nodes.new('ShaderNodeSeparateColor')
    return swatch, mask, packed.outputs[0]


def _pre_u2_keys(mat_name, zones):
    """Control keys of the pre-U2 wiring: a per-zone arc_gt_ver palette tree (inputs ColorMask,
    PackedR, Amt) fed by the mask texture, packed R and the GT_amt_z<N> Value node."""
    mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    nt = mat.node_tree
    swatch, mask, packed = _zone_inputs(mat)
    for zone in zones:
        tree = bpy.data.node_groups.new(f'GT_Colour{zone}_old_f1', 'ShaderNodeTree')
        tree['arc_gt_ver'] = 5
        tree.interface.new_socket('ColorMask', in_out='INPUT', socket_type='NodeSocketColor')
        tree.interface.new_socket('PackedR', in_out='INPUT', socket_type='NodeSocketFloat')
        tree.interface.new_socket('Amt', in_out='INPUT', socket_type='NodeSocketFloat')
        tree.interface.new_socket('Colour', in_out='OUTPUT', socket_type='NodeSocketColor')
        g = nt.nodes.new('ShaderNodeGroup')
        g.node_tree = tree
        amt = nt.nodes.new('ShaderNodeValue')
        nt.links.new(mask.outputs['Color'], g.inputs['ColorMask'])
        nt.links.new(amt.outputs[0], g.inputs['Amt'])
        nt.links.new(packed, g.inputs['PackedR'])
    return {r['id'] for r in uc.schema(mat)}


@unittest.skipIf(bpy is None, 'needs bpy')
class ZoneAmountControlTests(unittest.TestCase):
    def test_control_keys_match_pre_u2(self):
        clothing = importlib.import_module(PACKAGE + '.Shared.materials.clothing')
        zones = (1, 2, 3)
        before = _pre_u2_keys('F1 Amt pre', zones)
        self.assertEqual(before, {'Amt'})
        mat = bpy.data.materials.new('F1 Amt now')
        mat.use_nodes = True
        nt = mat.node_tree
        swatch, mask, packed = _zone_inputs(mat)
        with ops.operation_session():
            for zone in zones:
                out = clothing._gt_wire_colour_n_group(
                    nt.nodes, nt.links, None, lambda _k: (0.0, 0.0), zone, 0.4 + zone / 10.0,
                    swatch, mask, {}, False, mat_key='f1amt', packed_r=packed)
                self.assertIsNotNone(out)
        rows = uc.schema(mat)
        self.assertEqual({r['id'] for r in rows}, before)
        self.assertEqual(sum(1 for n in nt.nodes if n.type == 'GROUP' and n.node_tree.name == dlc.AMOUNT_GROUP), len(zones))
        self.assertEqual(uc.schema_set(mat, 'Amt', 0.9), len(zones))
        for n in nt.nodes:
            if n.type == 'GROUP' and n.node_tree.name == dlc.PALETTE_GROUP:
                src = n.inputs['Amt'].links[0].from_node
                self.assertEqual(src.node_tree.name, dlc.AMOUNT_GROUP)
                self.assertAlmostEqual(src.inputs['Amt'].links[0].from_node.outputs[0].default_value, 0.9, places=5)


if __name__ == '__main__':
    unittest.main()
