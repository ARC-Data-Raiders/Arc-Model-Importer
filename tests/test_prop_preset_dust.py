"""M_PropPreset dust follows the decoded SM5 base pass (PS F49808A9AA1DBB15), not T_Propdust (no Blender)."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('_math', '_prop_dust_planar', 'apply_prop_preset_dust')
CONSTANTS = ('WEATHERING_ARRAY', 'PROP_DUST_BREAKUP_TEXTURE', '_PROP_DUST_COLOURS', '_PROP_DUST_BREAKUP_MEAN')


class Socket:
    def __init__(self, node, key):
        self.node, self.key, self.links, self.default_value = node, key, [], None


class Sockets(dict):
    def __init__(self, node):
        super().__init__()
        self.node = node

    def __missing__(self, key):
        self[key] = Socket(self.node, key)
        return self[key]


class Node:
    def __init__(self, kind):
        self.kind, self.label, self.operation, self.image = kind, '', None, None
        self.inputs, self.outputs = Sockets(self), Sockets(self)


class Graph:
    def __init__(self):
        self.made, self.edges = [], []

    def new(self, kind):
        self.made.append(Node(kind))
        return self.made[-1]

    def link(self, a, b):
        self.edges.append((a, b))
        b.links.append(a)

    def of(self, kind, **attrs):
        return [n for n in self.made if n.kind == kind and all(getattr(n, k) == v for k, v in attrs.items())]


def mi_switch(switches, *names, default=None):
    for name in names:
        if name in switches:
            return bool(switches[name])
    return default


def mi_scalar(scalars, *names, default=0.0):
    for name in names:
        if name in scalars:
            return float(scalars[name])
    return float(default)


def load(images):
    tree = ast.parse((ROOT / 'materials/env_props/weathering.py').read_text(encoding='utf-8'))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in NAMES]
    body += [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, 'id', '') in CONSTANTS for t in n.targets)]
    loads = []

    def load_library_image(path, folders=None, slice_index=None):
        loads.append((path.rsplit('/', 1)[-1], slice_index))
        return images.get(path.rsplit('/', 1)[-1])

    def new_tex_image(nodes, img, label, loc, non_color=False):
        node = nodes.new('ShaderNodeTexImage')
        node.image, node.label, node.non_color = img, label, non_color
        return node

    def mix(kind):
        def build(nodes, links, a, b, fac, loc, label, blend='MIX'):
            node = nodes.new(kind)
            node.label, node.blend, node.args = label, blend, (a, b, fac)
            return node.outputs['Result']
        return build

    ns = {'_mi_switch': mi_switch, '_mi_scalar': mi_scalar, 'load_library_image': load_library_image,
          '_new_tex_image': new_tex_image, '_mix_rgba': mix('MixRGBA'), '_mix_normals_vec': mix('MixVector')}
    exec(compile(ast.Module(body=body, type_ignores=[]), '<weathering>', 'exec'), ns)
    return ns, loads


class PropPresetDustTests(unittest.TestCase):
    IMAGES = {'TA_Weathering_Props_01': 'W', 'T_ConcreteBuildingWorld_01_GRM': 'GRM'}

    def run_dust(self, mi, images=None, unit_scale=0.01):
        ns, loads = load(self.IMAGES if images is None else images)
        graph = Graph()
        links = type('Links', (), {'new': staticmethod(graph.link)})()
        albedo = Socket(Node('Albedo'), 'Color')
        out, dust, applied = ns['apply_prop_preset_dust'](
            graph, links, mi=mi, albedo_sock=albedo, ao_sock=Socket(Node('Normal'), 'Blue'), unit_scale=unit_scale)
        return graph, loads, out, dust, applied

    def math(self, graph, label):
        nodes = graph.of('ShaderNodeMath', label=label)
        self.assertEqual(len(nodes), 1, label)
        return nodes[0]

    def test_master_defaults_pin_prim_data_dust(self):
        # Use Dust and Use Prim Data Weathering Index default on: sharpness/bias 5/-1.5, slice 0 (CPD 9 unset).
        graph, loads, _out, dust, applied = self.run_dust({'parent': '/Game/X/M_PropPreset_CR+NOM.0',
                                                          'scalars': {'Top Down Dust Bias': 3.0}})
        self.assertTrue(applied)
        self.assertIn(('TA_Weathering_Props_01', 0), loads)
        topdown = self.math(graph, 'Top-down dust')
        self.assertEqual((topdown.operation, topdown.inputs[1].default_value, topdown.inputs[2].default_value),
                         ('MULTIPLY_ADD', 2.5, -1.5))
        tone = graph.of('ShaderNodeRGB')[0].outputs[0].default_value
        self.assertEqual(tone, (0.631944, 0.535275, 0.443328, 1.0))
        self.assertEqual(self.math(graph, 'Prop dust').inputs[1].default_value, 1.2)
        self.assertIs(dust['fac'], self.math(graph, 'Prop dust').outputs['Value'])
        # Metres-scaled mesh: breakup period 1024 uu = 10.24 m, W at Weathering Tiling 5 over it.
        scales = sorted(n.inputs['Scale'].default_value for n in graph.of('ShaderNodeVectorMath'))
        self.assertAlmostEqual(scales[0], 1 / 10.24)
        self.assertAlmostEqual(scales[1], 5 / 10.24)

    def test_ao_dust_contrast_and_breakup(self):
        graph, *_ = self.run_dust({'parent': 'M_PropPreset_CR+NOX+XEM',
                                   'scalars': {'AO Dust Contrast': 0.25, 'AO Dust Multiply': 3.0,
                                               'Dust Extra World Breakup Strength': 0.8}})
        shaped = self.math(graph, 'AO dust contrast')
        self.assertEqual((shaped.inputs[1].default_value, shaped.inputs[2].default_value, shaped.use_clamp),
                         (1.5, -0.25, True))
        self.assertEqual(self.math(graph, 'AO dust').inputs[1].default_value, 3.0)
        self.assertEqual(self.math(graph, 'Breakup').inputs[1].default_value, 0.8)

    def test_authored_index_and_dust_params_without_prim_data(self):
        graph, loads, *_ = self.run_dust({'parent': 'M_PropPreset_CR+NOM', 'switches': {
            'Use Prim Data Weathering Index': False}, 'scalars': {
            'Weathering Index': 2.0, 'Top Down Dust Sharpness': 8.0, 'Top Down Dust Bias': -3.0,
            'Dust Detail Breakup Tiling': 2.0}})
        self.assertIn(('TA_Weathering_Props_01', 2), loads)
        topdown = self.math(graph, 'Top-down dust')
        self.assertEqual((topdown.inputs[1].default_value, topdown.inputs[2].default_value), (4.0, -3.0))
        # Slice 2's colour scales by W.b; the breakup period is 1024 / tiling + 1 uu.
        self.assertEqual([n.blend for n in graph.of('MixRGBA', label='Dust colour x W.b')], ['MULTIPLY'])
        scales = sorted(n.inputs['Scale'].default_value for n in graph.of('ShaderNodeVectorMath'))
        self.assertAlmostEqual(scales[0], 1 / (0.01 * 513.0))

    def test_switch_gating(self):
        self.assertFalse(self.run_dust({'parent': 'M_PropPreset_CR+NOM', 'switches': {'Use Dust': False}})[4])
        # Parentless compact exports omit switches that are off.
        self.assertFalse(self.run_dust({'switches': {'Use Masks': True}})[4])
        self.assertTrue(self.run_dust({'switches': {'Use Dust': True}})[4])
        # Without the weathering array there is no W.a: no dust rather than an invented mask.
        self.assertFalse(self.run_dust({'parent': 'M_PropPreset_CR+NOM'}, images={})[4])

    def test_missing_breakup_uses_texture_mean(self):
        graph, *_ = self.run_dust({'parent': 'M_PropPreset_CR+NOM'}, images={'TA_Weathering_Props_01': 'W'})
        keep = self.math(graph, 'x (1 - breakup)').inputs[1].default_value
        self.assertAlmostEqual(keep, 1 - (0.275 + 0.234 * 0.5))


if __name__ == '__main__':
    unittest.main()
