"""R3: Character Parts Replace asks "Replace <Part> on <Character>?" first; New and Add do not ask."""
import ast
import os
import types
import unittest
from pathlib import Path

ROOT = Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY', Path(__file__).resolve().parents[1])).resolve()


def _invoke(plan):
    tree = ast.parse((ROOT / 'Shared/operators.py').read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ARC_OUTFITS_OT_AddAppearancePart')
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'invoke')
    ns = {'ui_targets': types.SimpleNamespace(part_destination=lambda context, mode: plan)}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'operators', 'exec'), ns)
    return ns['invoke']


class Op:
    part = 'HAIR'
    bl_rna = types.SimpleNamespace(properties={'part': types.SimpleNamespace(
        enum_items={'HAIR': types.SimpleNamespace(name='Hair')})})

    def execute(self, context):
        return {'EXECUTED'}


def _context(calls):
    def confirm(op, event, **kw):
        calls.append(kw)
        return {'RUNNING_MODAL'}
    return types.SimpleNamespace(scene=types.SimpleNamespace(arc_part_mode='REPLACE'),
                                 window_manager=types.SimpleNamespace(invoke_confirm=confirm))


class ReplaceConfirm(unittest.TestCase):
    def test_replace_asks_with_part_and_character(self):
        calls = []
        plan = {'replace': True, 'error': '', 'target': types.SimpleNamespace(name='Scout')}
        self.assertEqual(_invoke(plan)(Op(), _context(calls), None), {'RUNNING_MODAL'})
        self.assertEqual(calls[0]['title'], 'Replace Hair on Scout?')
        self.assertEqual(calls[0]['confirm_text'], 'Replace')

    def test_add_and_new_do_not_ask(self):
        calls = []
        for plan in ({'replace': False, 'error': '', 'target': None},
                     {'replace': True, 'error': 'Import an outfit or body first', 'target': None}):
            self.assertEqual(_invoke(plan)(Op(), _context(calls), None), {'EXECUTED'})
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
