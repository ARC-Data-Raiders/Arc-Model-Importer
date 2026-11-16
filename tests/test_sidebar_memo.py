"""Outfit selector draw memo: same inputs reuse one filtered list; a new query refilters."""
import ast
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY', Path(__file__).resolve().parents[1])).resolve()
PACKAGE = '_sidebar_memo_probe'


class CountingList(list):
    def __init__(self, *args):
        super().__init__(*args)
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        return super().__iter__()


class Layout:
    def __init__(self, log):
        self.log = log

    def row(self, **_):
        return self

    def column(self, **_):
        return self

    def box(self):
        return self

    def prop(self, *args, **kwargs):
        pass

    def separator(self, **_):
        pass

    def menu(self, *args, **kwargs):
        pass

    def template_list(self, *args, **kwargs):
        self.log.append(('list', args[3]))

    def label(self, text='', **_):
        self.log.append(('label', text))

    def operator(self, idname, text='', **_):
        return types.SimpleNamespace()


def _fake_lists():
    """Pure row helpers of Shared/ui_lists.py; request_sync stores rows at once instead of in a timer."""
    nodes = ast.parse((ROOT / 'Shared' / 'ui_lists.py').read_text(encoding='utf-8')).body
    pure = [n for n in nodes if isinstance(n, ast.FunctionDef) and n.name in ('outfit_rows', 'count_text')]
    namespace = {}
    exec(compile(ast.Module(body=pure, type_ignores=[]), 'ui_lists.py', 'exec'), namespace)
    stored = {}

    def request_sync(kind, signature, build, selected=''):
        stored[kind] = build()

    namespace.update(request_sync=request_sync, stored=stored)
    return types.SimpleNamespace(**namespace)


class SidebarMemo(unittest.TestCase):
    def setUp(self):
        self.raw = CountingList([('NONE', 'None', ''), ('A', 'Alpha Coat', ''), ('B', 'Bravo Vest', ''),
                                 ('C', 'Alpine Boots', '')])
        properties = types.SimpleNamespace(make_outfit_selector_items=lambda scene, context: self.raw)
        package = types.ModuleType(PACKAGE)
        package.__path__ = []
        outfit = types.ModuleType(PACKAGE + '.Outfit_Accessories')
        outfit.__path__ = []
        outfit.properties = properties
        for module in (package, outfit):
            sys.modules[module.__name__] = module
        self.addCleanup(lambda: [sys.modules.pop(n, None) for n in (PACKAGE, PACKAGE + '.Outfit_Accessories')])
        text = (ROOT / 'sidebar.py').read_text(encoding='utf-8')
        node = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == '_draw_outfit_selector_preview')
        self.log = []
        self.lists = _fake_lists()
        namespace = {'__name__': PACKAGE + '.sidebar', '__package__': PACKAGE,
                     '_split_controls_preview': lambda layout, context, icon_id, rows=0, factor=0.5: (Layout(self.log), None),
                     '_pretty_name': lambda text: text, '_ui_lists': lambda: self.lists,
                     '_collapsible': lambda *args, **kwargs: None, '_draw_preview_icon': lambda preview, icon_id: None}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'sidebar.py', 'exec'), namespace)
        self.draw = namespace['_draw_outfit_selector_preview']
        self.memo = self.draw.__defaults__[-1]
        self.memo.clear()
        self.scene = types.SimpleNamespace(arc_outfit_search='', arc_selected_outfit='', arc_selected_outfit_browse='')
        self.context = types.SimpleNamespace(scene=self.scene, window_manager=types.SimpleNamespace())

    def rows(self):
        self.log.clear()
        self.lists.stored.clear()
        self.draw(Layout(self.log), self.context, 0)
        return [row[1] for row in self.lists.stored.get('outfit', ())]

    def test_same_key_reuses_the_filtered_list(self):
        self.assertEqual(self.rows(), ['Alpha Coat', 'Bravo Vest', 'Alpine Boots'])
        self.assertIn(('list', 'arc_outfit_rows'), self.log)
        self.assertEqual(len(self.memo), 1)
        filtered = next(iter(self.memo.values()))[1]
        scans = self.raw.iterations
        self.assertEqual(self.rows(), ['Alpha Coat', 'Bravo Vest', 'Alpine Boots'])
        self.assertEqual(self.raw.iterations, scans)
        self.assertEqual(len(self.memo), 1)
        self.assertIs(next(iter(self.memo.values()))[1], filtered)

    def test_query_change_refilters(self):
        self.rows()
        self.scene.arc_outfit_search = 'ALP'
        self.assertEqual(self.rows(), ['Alpha Coat', 'Alpine Boots'])
        self.scene.arc_outfit_search = 'vest'
        self.assertEqual(self.rows(), ['Bravo Vest'])
        self.scene.arc_outfit_search = 'zzz'
        self.assertEqual(self.rows(), [])
        self.assertIn(('label', 'No matching outfits'), self.log)

    def test_selection_keeps_order_and_new_list_refilters(self):
        # The list widget highlights the selection in place; rows keep catalog order.
        self.scene.arc_selected_outfit = 'C'
        self.assertEqual(self.rows(), ['Alpha Coat', 'Bravo Vest', 'Alpine Boots'])
        self.raw = CountingList([('D', 'Delta Hood', '')])
        self.assertEqual(self.rows(), ['Delta Hood'])


if __name__ == '__main__':
    unittest.main()
