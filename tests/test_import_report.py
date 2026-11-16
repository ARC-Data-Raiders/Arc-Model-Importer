"""Per-import problem list: summary text, bounded size, nesting and calls outside a report."""
import importlib.util
import os
import unittest
from pathlib import Path

ROOT = Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY', Path(__file__).resolve().parents[1])).resolve()


def _load():
    spec = importlib.util.spec_from_file_location('_import_report_probe', ROOT / 'Shared/import_report.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Operator:
    def __init__(self):
        self.reports = []

    def report(self, level, text):
        self.reports.append((set(level), text))


class ImportReport(unittest.TestCase):
    def setUp(self):
        self.report = _load()

    def test_clean_run_has_empty_summary(self):
        self.report.begin('Outfit')
        summary = self.report.end()
        self.assertEqual(summary['kind'], 'Outfit')
        self.assertEqual((summary['count'], summary['items'], summary['first'], summary['text']), (0, [], '', ''))
        operator = Operator()
        self.report.emit(operator, summary)
        self.assertEqual(operator.reports, [])

    def test_warn_and_fail_build_summary(self):
        self.report.begin('Weapon')
        self.report.warn('missing texture T_A')
        self.report.fail('material MI_B', ValueError('bad slot'))
        self.report.fail('mod attach')
        self.assertEqual(self.report.count(), 3)
        summary = self.report.end()
        self.assertEqual(summary['count'], 3)
        self.assertEqual(summary['items'], ['missing texture T_A', 'material MI_B: ValueError: bad slot', 'mod attach'])
        self.assertEqual(summary['first'], 'missing texture T_A')
        self.assertTrue(summary['text'].startswith('3 problem(s): missing texture T_A'))
        self.assertIn('+2 more', summary['text'])
        operator = Operator()
        self.report.emit(operator, summary)
        self.assertEqual(len(operator.reports), 1)
        self.assertEqual(operator.reports[0][0], {'WARNING'})
        self.assertTrue(operator.reports[0][1].startswith('Weapon: '))

    def test_items_are_bounded_but_counted(self):
        limit = self.report.MAX_ITEMS
        self.report.begin('Bulk')
        for i in range(limit + 25):
            self.report.warn('problem %d' % i)
        summary = self.report.end()
        self.assertEqual(len(summary['items']), limit)
        self.assertEqual(summary['count'], limit + 25)

    def test_nested_report_returns_once(self):
        self.report.begin('Outer')
        self.report.begin('Inner')
        self.report.warn('inner problem')
        inner = self.report.end()
        self.assertEqual(inner['count'], 0)
        self.report.warn('outer problem')
        outer = self.report.end()
        self.assertEqual(outer['kind'], 'Outer')
        self.assertEqual(outer['items'], ['inner problem', 'outer problem'])

    def test_calls_without_begin_are_safe_and_dropped(self):
        self.report.warn('stray')
        self.report.fail('stray failure', RuntimeError('x'))
        self.report.end()
        self.report.abort()
        self.report.emit(None, {'count': 1, 'items': ['x'], 'text': 'x'})
        self.report.begin('Fresh')
        self.assertEqual(self.report.count(), 0)
        self.assertEqual(self.report.end()['count'], 0)

    def test_abort_drops_open_report(self):
        self.report.begin('Cancelled')
        self.report.begin('Cancelled')
        self.report.warn('lost')
        self.report.abort()
        self.report.begin('Next')
        self.assertEqual(self.report.end()['count'], 0)


if __name__ == '__main__':
    unittest.main()
