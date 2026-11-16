"""Staged run end paths: finish failure, cancel accounting and the integrated replace.

Runs inside Blender (real bpy objects). staged_import loads under a fake package with the
real ui_targets and stub import_report / ui_lists, so the job report is the real one.
"""
import ast
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

import bpy

ROOT = Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY', Path(__file__).resolve().parents[1])).resolve()
PKG = '_staged_finish_pkg'


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _package(name):
    pkg = types.ModuleType(name)
    pkg.__path__ = []
    sys.modules[name] = pkg
    return pkg


def _load():
    pkg, shared, maps = _package(PKG), _package(PKG + '.Shared'), _package(PKG + '.Maps')
    pkg.Shared, pkg.Maps = shared, maps
    report = types.ModuleType(PKG + '.Shared.import_report')
    report.aborted = 0

    def abort():
        report.aborted += 1
    report.abort = abort
    report.begin = report.end = report.emit = lambda *a, **k: None
    lists = types.ModuleType(PKG + '.Shared.ui_lists')
    lists.snapshot = lambda: None
    lists.record_import = lists.record_recent = lambda *a, **k: None
    sys.modules.update({report.__name__: report, lists.__name__: lists})
    shared.import_report, shared.ui_lists = report, lists
    shared.ui_targets = _module(PKG + '.Shared.ui_targets', ROOT / 'Shared/ui_targets.py')
    maps.staged_import = _module(PKG + '.Maps.staged_import', ROOT / 'Maps/staged_import.py')
    return maps.staged_import, shared.ui_targets, report


def _alive(obj):
    try:
        obj.name
        return True
    except ReferenceError:
        return False


class _StagedBase(unittest.TestCase):
    def setUp(self):
        self.staged, self.targets, self.report = _load()
        self.made = []

    def tearDown(self):
        for obj in list(self.made):
            if _alive(obj) and obj.name in bpy.data.objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        self.staged._ACTIVE = None
        for name in [n for n in sys.modules if n.startswith(PKG)]:
            del sys.modules[name]

    def _new(self, name):
        obj = bpy.data.objects.new(name, None)
        self.made.append(obj)
        return obj

    def _op(self, gen, finish=None, kind='Hair'):
        src = self.staged.ARC_OUTFITS_OT_StagedImport.__dict__
        op = type('FakeStagedOp', (), {k: src[k] for k in ('_end', '_fail', '_job_end', '_import_label')})()
        op.reports = []
        op.report = lambda level, msg: op.reports.append((set(level), msg))
        op._timer, op._kind, op._label, op._snap = None, kind, kind, None
        op._finish = finish
        op._before, op._before_meshes = set(bpy.data.objects), set(bpy.data.meshes)
        op._run = self.staged.StagedRun(gen)
        op._run.prewarm = False
        self.ctx = types.SimpleNamespace(
            scene=types.SimpleNamespace(),
            window_manager=types.SimpleNamespace(arc_staged_cancel=False, windows=[],
                                                 event_timer_remove=lambda timer: None))
        self.assertTrue(self.targets.job_begin('hair', 'Importing ' + kind, 0, 'x.cancel'))
        self.staged._ACTIVE = op._run
        return op

    def _run_to_end(self, op):
        while not op._run.advance(budget=10.0):
            pass
        return op._end(self.ctx, cancelled=False)

    def _gen(self, log, name='NewHair', wait=False):
        def gen():
            obj = self._new(name)
            log.append(obj)
            self.assertTrue(self.staged.after_success(lambda: (log.append('after'), ['ok'])[1]))
            if wait:
                yield ('wait', 0, 0, '')
            yield ('mesh', 1, 1, name)
            return 1, [], [obj]
        return gen()


class StagedEnd(_StagedBase):
    def test_finish_raises_fails_and_removes_new_objects(self):
        log = []

        def finish(op, context, result):
            raise RuntimeError('graph broke')
        op = self._op(self._gen(log), finish)
        self.assertEqual(self._run_to_end(op), {'CANCELLED'})
        self.assertFalse(_alive(log[0]) and log[0].name in bpy.data.objects)
        self.assertNotIn('after', log)
        rep = self.targets.job_report()
        self.assertEqual((rep['status'], rep['imported'], rep['failed']), ('failed', 0, 1))
        self.assertIn('Failed: Hair: graph broke', rep['lines'])
        self.assertIn(({'ERROR'}, 'Hair import failed: graph broke'), op.reports)
        self.assertIsNone(self.targets.job_active())
        self.assertEqual(self.report.aborted, 1)

    def test_finish_false_fails(self):
        log = []
        op = self._op(self._gen(log), lambda op, context, result: False)
        self.assertEqual(self._run_to_end(op), {'CANCELLED'})
        rep = self.targets.job_report()
        self.assertEqual(rep['status'], 'failed')
        self.assertIn('Failed: Hair: nothing was imported', rep['lines'])
        self.assertNotIn('after', log)

    def test_generator_error_fails(self):
        def gen():
            self._new('Partial')
            raise ValueError('mesh missing')
            yield
        op = self._op(gen())
        self.assertEqual(self._run_to_end(op), {'CANCELLED'})
        self.assertNotIn('Partial', bpy.data.objects)
        self.assertEqual(self.targets.job_report()['status'], 'failed')

    def test_success_runs_after_success_then_completes(self):
        log = []
        op = self._op(self._gen(log), lambda op, context, result: True)
        self.assertEqual(self._run_to_end(op), {'FINISHED'})
        self.assertIn('after', log)
        self.assertTrue(_alive(log[0]))
        rep = self.targets.job_report()
        self.assertEqual((rep['status'], rep['imported'], rep['failed']), ('completed', 1, 0))
        self.assertIsNone(self.staged._ACTIVE)

    def test_cancel_reports_cancelled_and_drops_queue(self):
        log = []
        op = self._op(self._gen(log, wait=True), lambda op, context, result: True)
        self.assertFalse(op._run.advance(budget=10.0))
        op._run.cancel()
        self.assertEqual(op._end(self.ctx, cancelled=True), {'CANCELLED'})
        self.assertNotIn('after', log)
        self.assertNotIn('NewHair', bpy.data.objects)
        rep = self.targets.job_report()
        self.assertEqual((rep['status'], rep['imported'], rep['failed'], rep['skipped']),
                         ('cancelled', 0, 0, 0))
        self.assertIn('Hair: stopped, partial objects removed', rep['lines'])

    def test_after_success_without_run_returns_false(self):
        self.staged._ACTIVE = None
        self.assertFalse(self.staged.after_success(lambda: []))


class IntegratedReplace(_StagedBase):
    """iter_appearance_parts queues the old-part removal; only a successful end runs it."""

    def _replace_gen(self):
        tree = ast.parse((ROOT / 'Outfit_Accessories/appearance.py').read_text(encoding='utf-8'))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'iter_appearance_parts')
        _package(PKG + '.Outfit_Accessories')
        mats = _package(PKG + '.Shared.materials')
        ops = types.ModuleType(PKG + '.Shared.materials.dr_primary_operations')
        sys.modules[ops.__name__] = ops
        mats.dr_primary_operations = ops
        self.old = self._new('OldHair')
        made = self._new

        def import_appearance_parts(context, defer_materials=None, replace_out=None, **kw):
            new = made('NewHair')
            old = self.old
            replace_out.append(lambda: (bpy.data.objects.remove(old, do_unlink=True), ['old removed'])[1])
            return 1, [], [new]
        ns = {'__name__': PKG + '.Outfit_Accessories.appearance', '__package__': PKG + '.Outfit_Accessories',
              'import_appearance_parts': import_appearance_parts, '_object_alive': _alive,
              'finish_deferred_materials': lambda *a, **k: None}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'appearance', 'exec'), ns)
        return ns['iter_appearance_parts'](None)

    def _old_alive(self):
        return _alive(self.old) and 'OldHair' in bpy.data.objects

    def test_success_removes_old_part(self):
        op = self._op(self._replace_gen(), lambda op, context, result: True)
        self.assertEqual(self._run_to_end(op), {'FINISHED'})
        self.assertFalse(self._old_alive())
        self.assertIn('NewHair', bpy.data.objects)

    def test_finish_failure_keeps_old_part(self):
        def finish(op, context, result):
            raise RuntimeError('material failed')
        op = self._op(self._replace_gen(), finish)
        self.assertEqual(self._run_to_end(op), {'CANCELLED'})
        self.assertTrue(self._old_alive())
        self.assertNotIn('NewHair', bpy.data.objects)

    def test_cancel_keeps_old_part(self):
        op = self._op(self._replace_gen(), lambda op, context, result: True)
        self.assertFalse(op._run.advance(budget=0.0))
        op._run.cancel()
        self.assertEqual(op._end(self.ctx, cancelled=True), {'CANCELLED'})
        self.assertTrue(self._old_alive())
        self.assertNotIn('NewHair', bpy.data.objects)

    def test_old_part_removed_only_in_deferred_step(self):
        tree = ast.parse((ROOT / 'Outfit_Accessories/appearance.py').read_text(encoding='utf-8'))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'import_appearance_parts')

        def calls(node):
            return {id(c) for c in ast.walk(node) if isinstance(c, ast.Call)
                    and getattr(c.func, 'id', '') == 'remove_appearance_objects'}
        inner = [n for n in ast.walk(fn) if isinstance(n, ast.FunctionDef) and n.name == '_remove_old']
        self.assertEqual(len(inner), 1)
        self.assertTrue(calls(inner[0]))
        self.assertEqual(calls(fn), calls(inner[0]))


class BatchLeftAccounting(unittest.TestCase):
    """Queued jobs never run are "left", never "skipped" (outfit batch and part queue)."""

    def _job_end_kw(self, rel, owner):
        tree = ast.parse((ROOT / rel).read_text(encoding='utf-8'))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == owner
                  and any(isinstance(c, ast.Call) and getattr(c.func, 'attr', '') == 'job_end' for c in ast.walk(n)))
        call = next(c for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, 'attr', '') == 'job_end')
        return ({k.arg: ast.unparse(k.value) for k in call.keywords},
                [ast.unparse(a) for a in call.args], ast.unparse(fn))

    def test_part_queue_passes_left(self):
        kw, args, _ = self._job_end_kw('Outfit_Accessories/appearance_queue.py', '_finish')
        self.assertIn('len(self._queue.jobs) - len(records)', kw.get('left', ''))
        self.assertIn('_skipped', args[2])

    def test_outfit_batch_passes_left(self):
        kw, args, src = self._job_end_kw('Shared/operators.py', '_finish')
        self.assertEqual(kw.get('left'), 'left')
        self.assertIn('left = max(0, len(self._jobs) - self._imported - len(self._failures))', src)
        self.assertIn('skipped', args[2])


if __name__ == '__main__':
    unittest.main()
