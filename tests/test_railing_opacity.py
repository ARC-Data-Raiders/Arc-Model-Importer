import ast
from pathlib import Path
import unittest

from test_prop_preset_dust import Graph, mi_switch


ROOT = Path(__file__).resolve().parents[1]
MATERIAL = '/Pioneer/Environment/Props/Residential/Railing_01/MI_Res_Railing_01.json'


def load():
    tree = ast.parse((ROOT / 'Shared/materials/environment.py').read_text(encoding='utf-8'))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and
                 node.name == '_apply_nom_opacity_mask']
    if not functions:
        raise AssertionError('The live legacy environment mask producer is absent')
    modes = []

    def image(nodes, value, label, location, non_color=False):
        node = nodes.new('ShaderNodeTexImage')
        node.image, node.label, node.non_color = value, label, non_color
        return node

    namespace = {'_mi_switch': mi_switch, '_is_masked_blend': lambda material: material['blend_mode'] == 'BLEND_Masked',
                 '_find_env_tex': lambda lookup, name: (name, lookup.get(name)), '_new_tex_image': image,
                 '_set_material_alpha_mode': lambda material, **settings: modes.append(settings)}
    exec(compile(ast.Module(body=functions, type_ignores=[]), '<environment>', 'exec'), namespace)
    return namespace['_apply_nom_opacity_mask'], modes, tree


class RailingOpacityTests(unittest.TestCase):
    def run_mask(self, changes=None, path=MATERIAL, images=None):
        function, modes, tree = load()
        graph = Graph()
        material = {}
        shader = graph.new('ShaderNodeBsdfPrincipled')
        links = type('Links', (), {'new': staticmethod(graph.link)})()
        record = {'parent': '/Game/M_PropPreset_CR+NOM.M_PropPreset_CR+NOM',
                  'blend_mode': 'BLEND_Masked', 'opacity_clip': .3333, 'two_sided': True,
                  'switches': {'Use Default Texture Setup': True, 'Use Mask Texture': True,
                               'Use Slice Mask': True, 'Use Tinting': False}}
        if changes:
            record.update(changes)
        applied = function(graph, links, material, shader, record, path,
                           {'Mask': 'A'} if images is None else images, lambda *args: None, (0, 0))
        return graph, shader, material, modes, applied, tree

    def test_live_builder_links_red_mask_and_preserves_two_sided(self):
        graph, shader, material, modes, applied, tree = self.run_mask()
        self.assertTrue(applied)
        images = graph.of('ShaderNodeTexImage')
        self.assertEqual([(node.image, node.non_color) for node in images], [('A', True)])
        separate = graph.of('ShaderNodeSeparateColor')[0]
        less = graph.of('ShaderNodeMath', operation='LESS_THAN')[0]
        self.assertIn((separate.outputs['Red'], less.inputs[0]), graph.edges)
        self.assertEqual(less.inputs[1].default_value, .3333)
        inverse = graph.of('ShaderNodeMath', operation='SUBTRACT')[0]
        self.assertEqual(inverse.inputs[0].default_value, 1)
        self.assertIn((inverse.outputs['Value'], shader.inputs['Alpha']), graph.edges)
        self.assertEqual(modes, [{'mode': 'CLIP', 'threshold': .3333, 'two_sided': True}])
        setup = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_setup_environment_material')
        self.assertTrue(any(isinstance(node, ast.Call) and getattr(node.func, 'id', '') == '_apply_nom_opacity_mask'
                            for node in ast.walk(setup)))

    def test_zero_clip_is_not_replaced_by_default(self):
        graph, *rest = self.run_mask({'opacity_clip': 0})
        self.assertEqual(graph.of('ShaderNodeMath', operation='LESS_THAN')[0].inputs[1].default_value, 0)

    def test_wrong_material_master_opaque_or_disabled_stays_unchanged(self):
        for changes, path in [({}, '/Game/Other.json'), ({'parent': '/Game/M_Other'}, MATERIAL),
                              ({'blend_mode': 'BLEND_Opaque'}, MATERIAL), ({'switches': {}}, MATERIAL)]:
            with self.subTest(changes=changes, path=path):
                graph, _, _, modes, applied, _ = self.run_mask(changes, path)
                self.assertFalse(applied)
                self.assertEqual(graph.of('ShaderNodeTexImage'), [])
                self.assertEqual(modes, [])

    def test_missing_mask_does_not_claim_implementation(self):
        self.assertFalse(self.run_mask(images={})[4])


if __name__ == '__main__':
    unittest.main()
