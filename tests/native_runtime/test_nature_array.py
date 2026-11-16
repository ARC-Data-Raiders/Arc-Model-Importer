import math
import types
import unittest

from native_runtime.test_surface_layer_stages import FakeGraph, load_surfaces, record


MASTER = 'M_Nature_01_TBG'
TEXTURES = {'CR': '/Game/T/Cliff_CR', 'NOH': '/Game/T/Cliff_NOH',
            '2. Detail Array': '/Game/T/TA_Cliff'}


class ArrayGraph(FakeGraph):
    def __init__(self, alpha=0.6, distance=10, depth=2, strength=1):
        super().__init__()
        self.alpha = alpha
        self.distance = distance
        self.layers = []
        self.coords = []
        self.obj = types.SimpleNamespace(data={'arc_uemodel_scale': .01}, get=lambda key, default: default)
        self.index = types.SimpleNamespace(texture_info=lambda ref: {'Type': 'Texture2DArray', 'Mips': [{'SizeZ': depth}]})
        self.pixels = {'CR': {'color': (.2, .4, .6), 'r': .2, 'g': .4, 'b': .6, 'a': alpha},
                       'NOH': {'r': .6, 'g': .7, 'b': .8, 'a': .9},
                       0: {'r': .4, 'g': .5, 'b': .3, 'a': .2},
                       1: {'r': .7, 'g': .4, 'b': .8, 'a': .9}}

    def image(self, ref, uv=None, layer=None, **kwargs):
        self.sampled.append(ref)
        if ref == TEXTURES['2. Detail Array']:
            self.layers.append(layer)
            self.coords.append(uv)
            return self.pixels[layer]
        return self.pixels['CR' if ref == TEXTURES['CR'] else 'NOH']

    def transform_uv(self, channel=0, scale=1, offset=(0, 0), rotation=0):
        return channel, scale, offset

    def node(self, kind, label=''):
        if kind != 'ShaderNodeCameraData':
            raise AssertionError(kind)
        return types.SimpleNamespace(outputs={'View Distance': self.distance})

    def math(self, op, first, second=0):
        return {'FLOOR': lambda: math.floor(first), 'LESS_THAN': lambda: float(first < second),
                'GREATER_THAN': lambda: float(first > second), 'MAXIMUM': lambda: max(first, second),
                'SQRT': lambda: math.sqrt(first)}[op]()

    def add(self, first, second): return first + second
    def sub(self, first, second): return first - second
    def mul(self, first, second): return first * second
    def div(self, first, second): return first / second
    def combine(self, first, second, third=0): return first, second, third
    def mix(self, first, second, amount, label=''):
        if isinstance(first, tuple): return tuple(self.mix(a, b, amount) for a, b in zip(first, second))
        return first + (second - first) * amount
    def vector(self, op, first, second=(0, 0, 0), scale=1):
        if op == 'SCALE': return tuple(value * scale for value in first)
        if op == 'NORMALIZE':
            length = math.sqrt(sum(value * value for value in first))
            return tuple(value / length for value in first)
        raise AssertionError(op)


class NatureArrayStageTests(unittest.TestCase):
    surfaces = load_surfaces()

    def apply(self, graph=None, switches=None, scalars=None, master=MASTER):
        graph = graph or ArrayGraph()
        material = record(master, TEXTURES, {'2. Detail Texture': True} if switches is None else switches,
                          scalars=scalars)
        surface = {'color': (.2, .4, .6), 'normal': (0, 0, 1), 'rough': .4}
        return graph, self.surfaces.nature_detail_array(graph, material, surface)

    def test_surface_build_samples_active_array(self):
        graph = FakeGraph()
        graph.index = types.SimpleNamespace(texture_info=lambda ref: {'Type': 'Texture2DArray', 'Mips': [{'SizeZ': 2}]})
        graph.obj = types.SimpleNamespace(data={'arc_uemodel_scale': .01}, get=lambda key, default: default)
        graph.node = lambda *args: types.SimpleNamespace(outputs={'View Distance': ('distance',)})
        self.surfaces.build_surface(graph, record(MASTER, TEXTURES, {'2. Detail Texture': True}))
        self.assertIn(TEXTURES['2. Detail Array'], graph.sampled)

    def test_authored_uv_scales_and_all_native_slices(self):
        graph, surface = self.apply(scalars={'2. Detail Tile U': 32, '2. Detail Tile V': 16})
        self.assertEqual(graph.layers, [0, 1])
        self.assertEqual(graph.coords, [(0, (32, 16), (0, 0))] * 2)

    def test_near_normal_uses_encoded_multiply_not_rnm(self):
        graph, surface = self.apply()
        encoded = (2 * .6 * .7, 2 * .7 * .4)
        normal = (2 * encoded[0] - 1, 2 * encoded[1] - 1)
        expected = (*normal, math.sqrt(max(1 - sum(value * value for value in normal), 0)))
        for actual, wanted in zip(surface['normal'], expected): self.assertAlmostEqual(actual, wanted)

    def test_normal_strength_interpolates_reconstructed_z(self):
        graph, surface = self.apply(scalars={'1. Normal Strength': .3})
        raw = (.68, .12, math.sqrt(1 - .68 ** 2 - .12 ** 2))
        expected = (raw[0] * .3, raw[1] * .3, 1 + (raw[2] - 1) * .3)
        for actual, wanted in zip(surface['normal'], expected): self.assertAlmostEqual(actual, wanted)

    def test_near_color_uses_array_blue_squared(self):
        graph, surface = self.apply(scalars={'2. Detail Color': .5})
        factor = 1 + .5 * (2 * (.8 ** 2 + .2) - 1)
        for actual, base in zip(surface['color'], (.2, .4, .6)): self.assertAlmostEqual(actual, base * factor)

    def test_far_color_and_normal_do_not_use_detail(self):
        graph, surface = self.apply(ArrayGraph(distance=50.01))
        self.assertEqual(surface['color'], (.2, .4, .6))
        self.assertAlmostEqual(surface['normal'][0], .2)
        self.assertAlmostEqual(surface['normal'][1], .4)

    def test_distance_boundary_retains_near_branch(self):
        graph, surface = self.apply(ArrayGraph(distance=50))
        self.assertAlmostEqual(surface['normal'][0], .68)

    def test_fractional_start_slice_rounds_ties_even(self):
        graph, surface = self.apply(ArrayGraph(alpha=0), scalars={'2. Starting Slice': .5})
        self.assertAlmostEqual(surface['normal'][0], -.04)

    def test_array_slice_coordinate_clamps_to_native_depth(self):
        graph, surface = self.apply(ArrayGraph(alpha=1), scalars={'2. Starting Slice': 50})
        self.assertAlmostEqual(surface['normal'][0], .68)

    def test_disabled_or_unknown_detail_switch_samples_nothing(self):
        for switches in ({'2. Detail Texture': False}, {}):
            graph, surface = self.apply(switches=switches)
            self.assertEqual(graph.layers, [])

    def test_other_master_is_unchanged(self):
        graph, surface = self.apply(master='M_Nature_01_South')
        self.assertEqual(graph.layers, [])

    def test_unproven_mask_permutation_stays_unimplemented(self):
        graph, surface = self.apply(switches={'2. Detail Texture': True, 'Enable BlueGate Custom Masks': True})
        self.assertEqual(graph.layers, [])
        self.assertTrue(graph.report['warnings'])

    def test_missing_or_inconsistent_native_depth_is_not_inferred(self):
        for info in ({'Type': 'Texture2DArray'}, {'Type': 'Texture2DArray', 'Mips': [{'SizeZ': 2}, {'SizeZ': 3}]},
                     {'Type': 'Texture2D', 'Mips': [{'SizeZ': 2}]}):
            graph = ArrayGraph()
            graph.index.texture_info = lambda ref, info=info: info
            graph, surface = self.apply(graph)
            self.assertEqual(graph.layers, [])
            self.assertTrue(graph.report['warnings'])

    def test_missing_active_slice_is_not_replaced_by_flat_data(self):
        graph = ArrayGraph()
        graph.pixels[1] = None
        with self.assertRaisesRegex(FileNotFoundError, 'Incomplete active Nature detail array'):
            self.apply(graph)

    def test_zero_normal_strength_is_flat(self):
        graph, surface = self.apply(scalars={'1. Normal Strength': 0})
        self.assertEqual(surface['normal'], (0, 0, 1))

    def test_tracker_does_not_invent_nonarray_depth(self):
        bridge = load_surfaces('native_catalog_bridge')
        owner = types.SimpleNamespace(texture_fact=lambda ref: {'type': 'Texture2D', 'properties': {'SRGB': False}})
        self.assertNotIn('Mips', bridge.TrackingIndex(owner).texture_info('/Game/T/Plain'))

    def test_cache_key_scopes_camera_units_and_invalidates_old_recipe(self):
        import ast
        import hashlib
        import json
        from native_runtime.native_test_support import ROOT
        source = ast.parse((ROOT / 'Shared/materials/rebuild/materials.py').read_text(encoding='utf8'))
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'build_material')
        prefix = []
        for statement in function.body:
            if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'native_owner' for target in statement.targets):
                break
            prefix.append(statement)
        def executable(remove_recipe):
            body = [statement for statement in prefix if not (remove_recipe and isinstance(statement, ast.If)
                    and any(isinstance(node, ast.Constant) and node.value == 'nature_detail_array_recipe' for node in ast.walk(statement)))]
            compiled = ast.FunctionDef(name='cache_key', args=function.args, body=body + [ast.Return(value=ast.Name(id='key', ctx=ast.Load()))], decorator_list=[])
            namespace = {'hashlib': hashlib, 'json': json, '__package__': self.surfaces.__package__}
            exec(compile(ast.fix_missing_locations(ast.Module(body=[compiled], type_ignores=[])), '<real material key>', 'exec'), namespace)
            return namespace['cache_key']
        material = record(MASTER, TEXTURES, {'2. Detail Texture': True})
        material.update(package='/Game/T/MI_Cliff', family='surface')
        data = types.SimpleNamespace(uv_layers=[], color_attributes=[], get=lambda key, default: default)
        first = types.SimpleNamespace(data=data, get=lambda key, default: .01)
        second = types.SimpleNamespace(data=data, get=lambda key, default: 1.)
        repository = types.SimpleNamespace(root='/exact/cache')
        updated = executable(False)
        previous = executable(True)
        self.assertNotEqual(updated(first, material, repository), updated(second, material, repository))
        self.assertEqual(previous(first, material, repository), previous(second, material, repository))
        self.assertNotEqual(updated(first, material, repository), previous(first, material, repository))

    def test_native_tracker_retains_authoritative_array_depth(self):
        bridge = load_surfaces('native_catalog_bridge')
        owner = types.SimpleNamespace(texture_fact=lambda ref: {'type': 'Texture2DArray', 'slice_count': 2,
                                                               'properties': {'SRGB': False}})
        result = bridge.TrackingIndex(owner).texture_info(TEXTURES['2. Detail Array'])
        self.assertEqual(result['Mips'], [{'SizeZ': 2}])


if __name__ == '__main__':
    unittest.main()
