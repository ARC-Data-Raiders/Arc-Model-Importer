"""Exact native UV orientation and private instance bindings."""
try:
    from .native_test_support import ROOT, PACKAGE, namespace
except ImportError:
    from native_test_support import ROOT, PACKAGE, namespace

import ast
import importlib
import random
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

namespace()
composer = importlib.import_module(PACKAGE + '.Shared.materials.dr_layered_composer')
core = importlib.import_module(PACKAGE + '.Shared.materials.native.core')


class NativeUVOrientationGroupTests(unittest.TestCase):
    def test_shared_circuit_preserves_exact_components(self):
        rng = random.Random(2517)
        vectors = [(0., 0., 0.), (-0., -0., -0.), (1., 1., 1.), (-2., 3., -.125), (1048576., -1048576., 1.25)]
        vectors.extend(tuple(rng.uniform(-32., 32.) for component in range(3)) for index in range(256))
        circuit = composer.orient_uv_circuit()
        for vector in vectors:
            with self.subTest(vector=vector):
                actual = core.evaluate(circuit, {'attribute': {'Vector': vector}})
                expected = (vector[0], 1. - vector[1], vector[2])
                self.assertEqual(struct.pack('<3d', *actual), struct.pack('<3d', *expected))

    def test_shared_definition_exposes_only_instance_vector(self):
        name, inputs, output, circuit = composer.GROUP_CONTRACTS['orient_uv']
        self.assertEqual(name, 'GT_OrientUV')
        self.assertEqual(inputs, [('Vector', 'NodeSocketVector', (0., 0., 0.))])
        self.assertEqual(output, ('Vector', 'NodeSocketVector', (0., 0., 0.)))
        self.assertIs(circuit, composer.orient_uv_circuit)

    def helper(self):
        source = ast.parse((ROOT / 'Shared/materials/gt_outfit_pbr.py').read_text(encoding='utf8'))
        function = next(item for item in source.body if isinstance(item, ast.FunctionDef) and item.name == 'orient_uv')
        function.decorator_list = []
        output = object()
        group = SimpleNamespace(inputs={'Vector': object()}, outputs={'Vector': output})
        factory = SimpleNamespace(new_operation=Mock(return_value=group), set_label=Mock())
        definition = object()
        context = {'_dr_ops': factory, '_dr_stage_composer': Mock(return_value=definition), 'place': Mock()}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<orient_uv>', 'exec'), context)
        return context['orient_uv'], context, group, output

    def test_none_input_does_not_build_a_group(self):
        helper, context, group, output = self.helper()
        self.assertIsNone(helper(object(), object(), None, 'Coordinates', 10., 20.))
        context['_dr_ops'].new_operation.assert_not_called()
        context['_dr_stage_composer'].assert_not_called()

    def test_instance_retains_output_alias_and_input_binding(self):
        helper, context, group, output = self.helper()
        nodes = object()
        links = SimpleNamespace(new=Mock())
        incoming = object()
        self.assertIs(helper(nodes, links, incoming, 'GT_Array_ImageUV', 10., 20., hide=False), output)
        context['_dr_ops'].new_operation.assert_called_once_with(nodes, 'ShaderNodeGroup', 'GT_Array_ImageUV', stage='Coordinates', role=None)
        context['_dr_stage_composer'].assert_called_once_with('orient_uv')
        self.assertIs(group.node_tree, context['_dr_stage_composer'].return_value)
        links.new.assert_called_once_with(incoming, group.inputs['Vector'])
        context['place'].assert_called_once_with(group, 190., 20., hide=False)

    def test_bad_link_keeps_original_fallback(self):
        helper, context, group, output = self.helper()
        incoming = object()
        links = SimpleNamespace(new=Mock(side_effect=ValueError('bad vector link')))
        self.assertIs(helper(object(), links, incoming, 'Coordinates', 10., 20.), incoming)


if __name__ == '__main__':
    unittest.main()
