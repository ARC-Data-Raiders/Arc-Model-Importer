"""Weapon import is a staged generator; the synchronous entry point drains it (structure only)."""
import ast
import os
import unittest
from pathlib import Path

ROOT = Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY', Path(__file__).resolve().parents[1])).resolve()
SOURCE = ROOT / 'Weapons/weapon_import.py'
STAGES = {'extract', 'mesh', 'material', 'attach', 'finish'}


def _functions():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _signature(node):
    args = node.args
    return ([a.arg for a in args.args], [a.arg for a in args.kwonlyargs])


class WeaponStagedGenerator(unittest.TestCase):
    def setUp(self):
        self.functions = _functions()

    def test_iter_import_weapon_is_a_generator_with_the_same_signature(self):
        self.assertIn('iter_import_weapon', self.functions)
        self.assertIn('import_weapon', self.functions)
        gen = self.functions['iter_import_weapon']
        self.assertTrue(any(isinstance(n, ast.Yield) for n in ast.walk(gen)))
        self.assertEqual(_signature(gen), _signature(self.functions['import_weapon']))

    def test_yields_are_four_tuples_with_known_stages(self):
        yields = sorted((n for n in ast.walk(self.functions['iter_import_weapon']) if isinstance(n, ast.Yield)),
                        key=lambda n: (n.lineno, n.col_offset))
        stages = []
        for node in yields:
            self.assertIsInstance(node.value, ast.Tuple)
            self.assertEqual(len(node.value.elts), 4)
            first = node.value.elts[0]
            self.assertIsInstance(first, ast.Constant)
            stages.append(first.value)
        self.assertEqual(stages[0], 'extract')
        self.assertEqual(stages[-1], 'finish')
        self.assertLessEqual(set(stages), STAGES)

    def test_import_weapon_drains_the_generator_and_returns_its_value(self):
        node = self.functions['import_weapon']
        calls = []

        def fake_iter(context, entry, variant='OFF', *, skin=None, tier=None, location=None):
            calls.append((context, entry, variant, skin, tier, location))
            for stage in ('extract', 'mesh', 'material', 'attach', 'finish'):
                calls.append(stage)
                yield (stage, 0, 0, entry['name'])
            return 'root-object'

        namespace = {'iter_import_weapon': fake_iter}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        result = namespace['import_weapon']('ctx', {'name': 'Gun'}, 'V1', skin='S', tier=2, location=(1, 2, 3))
        self.assertEqual(result, 'root-object')
        self.assertEqual(calls[0], ('ctx', {'name': 'Gun'}, 'V1', 'S', 2, (1, 2, 3)))
        self.assertEqual(calls[1:], ['extract', 'mesh', 'material', 'attach', 'finish'])

    def test_import_weapon_propagates_stage_errors(self):
        node = self.functions['import_weapon']

        def failing(context, entry, variant='OFF', *, skin=None, tier=None, location=None):
            yield ('extract', 0, 0, entry['name'])
            raise FileNotFoundError('Weapon visual data unavailable')

        namespace = {'iter_import_weapon': failing}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        with self.assertRaises(FileNotFoundError):
            namespace['import_weapon']('ctx', {'name': 'Gun'})


if __name__ == '__main__':
    unittest.main()
