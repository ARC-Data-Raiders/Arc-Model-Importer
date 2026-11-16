import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]


def function(path, name, namespace=None):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8-sig'))
    node = next(item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.ClassDef)) and item.name == name)
    scope = dict(namespace or {})
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    return scope[name]


class SidebarUserRevisionTests(unittest.TestCase):
    def test_weapon_selector_uses_aspect_preserving_preview(self):
        calls = []

        class Layout:
            def column(self, **kwargs):
                return self

            def row(self, **kwargs):
                return self

            def prop(self, *args, **kwargs):
                pass

            def separator(self):
                pass

            def operator(self, *args, **kwargs):
                return SimpleNamespace()

        weapon = SimpleNamespace(
            ensure_registered=lambda: True, _PREVIEWS=object(),
            preview_id=lambda path: calls.append(('preserved', path)) or 42,
            arc_preview_id=lambda path: calls.append(('cropped', path)) or 7,
        )
        catalog = SimpleNamespace(weapon_by_key=lambda key: {'stem': 'gun'}, weapon_enum_items=lambda: [])
        assets = SimpleNamespace(bundled=lambda: {'weapons': {}}, bundled_preview=lambda *args, **kwargs: 'wide-gun.png')
        modules = {'Weapons.weapon_ui': weapon, 'Weapons.weapon_catalog': catalog, 'Weapons.weapon_assets': assets}
        draw = function('sidebar.py', '_draw_weapons_content', {
            '_lazy': modules.get, '_ui_lists': lambda: SimpleNamespace(draw_list_header=True),
            '_icon_memo': lambda key, previews, load: load(),
            '_draw_catalog_selector': lambda layout, context, kind, items, key, icon: calls.append(('selector', kind, icon)) or False,
            '_weapon_section': lambda *args: None,
        })
        draw(Layout(), SimpleNamespace(scene=SimpleNamespace(arc_weapon_key='gun')))
        self.assertEqual(calls, [('preserved', 'wide-gun.png'), ('selector', 'weapon', 42)])

    def test_top_level_contents_have_explicit_side_insets(self):
        for boxed in (False, True):
            events = []

            class Layout:
                def __init__(self, path):
                    self.path = path

                def split(self, **kwargs):
                    events.append(('split', self.path, kwargs))
                    return Layout(self.path + '/split')

                def column(self, **kwargs):
                    events.append(('column', self.path))
                    return Layout(self.path + '/column')

                def box(self):
                    events.append(('box', self.path))
                    return Layout(self.path + '/box')

                def separator(self, **kwargs):
                    events.append(('separator', self.path, kwargs))

            body = Layout('body')
            draw = function('sidebar.py', '_draw_top_level_section', {
                '_collapsible': lambda *args, **kwargs: body, '_SECTION_ICONS': {},
            })
            draw('root', None, 'section', 'Section', lambda layout, context: events.append(('content', layout.path)), boxed=boxed)
            splits = [event for event in events if event[0] == 'split']
            self.assertEqual(len(splits), 2, events)
            self.assertAlmostEqual(splits[0][2]['factor'], 0.04)
            self.assertAlmostEqual(splits[1][2]['factor'], 0.92 / 0.96)
            self.assertIn(('separator', 'body', {'type': 'LINE'}), events)

    def test_top_level_section_draws_inset_and_end_line_only_when_open(self):
        events = []

        class Body:
            def split(self, **kwargs):
                events.append(('split', kwargs))
                return self

            def box(self):
                events.append('box')
                return 'boxed contents'

            def column(self, **kwargs):
                events.append('column')
                return self

            def separator(self, **kwargs):
                events.append(('separator', kwargs))

        opened = [True]

        def collapsible(layout, panel_id, title, **kwargs):
            events.append(('panel', layout, panel_id, kwargs))
            return Body() if opened[0] else None

        draw = function('sidebar.py', '_draw_top_level_section', {
            '_collapsible': collapsible, '_SECTION_ICONS': {'section': 'MODIFIER'},
        })
        content = lambda layout, context: events.append(('content', layout, context))
        draw('root', 'context', 'section', 'Section', content, boxed=True)
        self.assertEqual(events[0], ('panel', 'root', 'section', {'default_closed': True, 'icon': 'MODIFIER'}))
        self.assertEqual(events[-3:], ['box', ('content', 'boxed contents', 'context'), ('separator', {'type': 'LINE'})])
        events.clear()
        draw('root', 'context', 'section', 'Section', content)
        self.assertIsInstance(events[-2][1], Body)
        self.assertEqual(events[-1], ('separator', {'type': 'LINE'}))
        events.clear()
        opened[0] = False
        draw('root', 'context', 'section', 'Section', content)
        self.assertEqual(len(events), 1)

    def test_weapon_preview_loads_outside_draw(self):
        source = ast.parse((ROOT / 'sidebar.py').read_text(encoding='utf-8-sig'))
        node = next(item for item in source.body if isinstance(item, ast.FunctionDef) and item.name == '_draw_weapons_content')
        callbacks = [item for item in ast.walk(node) if isinstance(item, ast.Lambda)]
        self.assertTrue(any(isinstance(item, ast.Attribute) and item.attr == 'preview_id' for callback in callbacks for item in ast.walk(callback)))

    def test_single_tier_weapon_preview_uses_available_tier(self):
        previews = []

        class Layout:
            def column(self, **kwargs):
                return self

            def row(self, **kwargs):
                return self

            def prop(self, *args, **kwargs):
                pass

            def separator(self):
                pass

            def operator(self, *args, **kwargs):
                return SimpleNamespace()

        weapon = SimpleNamespace(ensure_registered=lambda: True)
        catalog = SimpleNamespace(weapon_by_key=lambda key: {'stem': 'single'}, weapon_enum_items=lambda: [])
        assets = SimpleNamespace(
            bundled=lambda: {'weapons': {'single': {'quality_levels': [0]}}},
            bundled_preview=lambda entry, variant, **kwargs: previews.append(kwargs['tier']),
        )
        modules = {'Weapons.weapon_ui': weapon, 'Weapons.weapon_catalog': catalog, 'Weapons.weapon_assets': assets}
        draw = function('sidebar.py', '_draw_weapons_content', {
            '_lazy': modules.get, '_ui_lists': lambda: SimpleNamespace(draw_list_header=True),
            '_draw_catalog_selector': lambda *args: False, '_weapon_section': lambda *args: None,
        })
        scene = SimpleNamespace(arc_weapon_key='single', arc_weapon_ascended='OFF', arc_weapon_tier='3')
        draw(Layout(), SimpleNamespace(scene=scene))
        self.assertEqual(previews, ['0'])

    def test_catalog_selector_matches_outfit_layout_and_hides_filtered_choice(self):
        previews, splits, draws, browse = [], [], [], []

        class Layout:
            def prop(self, *args, **kwargs):
                pass

            def row(self, **kwargs):
                return self

            def operator(self, name, **kwargs):
                op = SimpleNamespace()
                if name == 'arc_outfits.icon_browser':
                    browse.append(op)
                return op

            def template_list(self, *args, **kwargs):
                draws.append((args, kwargs))

        layout = Layout()
        context = SimpleNamespace(scene=SimpleNamespace(arc_weapon_search='second'), window_manager=object())
        lists = SimpleNamespace(
            outfit_rows=lambda items, query, pretty: ([row for row in items if query in row[1]], len(items)),
            filter_keys=lambda *args: None, filter_mode=lambda *args: 'ALL',
            request_sync=lambda *args: None, draw_list_header=lambda *args: 1,
        )
        draw = function('sidebar.py', '_draw_catalog_selector', {
            '_ui_lists': lambda: lists, '_pretty_name': str,
            '_split_controls_preview': lambda parent, ctx, icon, **kwargs: splits.append((icon, kwargs)) or (layout, object()),
            '_draw_preview_icon': lambda parent, icon: previews.append(icon),
        })
        items = [('first', 'first', ''), ('second', 'second', '')]
        self.assertTrue(draw(layout, context, 'weapon', items, 'first', 99))
        self.assertEqual(previews[-1], 0)
        self.assertEqual(splits[-1][0], 0)
        self.assertFalse(draw(layout, context, 'weapon', items, 'second', 99))
        self.assertEqual(previews[-1], 99)
        self.assertEqual(splits[-1][1], dict(rows=11, factor=0.6))
        self.assertEqual(draws[-1][1]['rows'], 3)
        self.assertEqual([op.kind for op in browse], ['weapon', 'weapon'])

    def test_layout_only_emits_section_names(self):
        events = []

        class Layout:
            def row(self, **kwargs):
                return Layout()

            def label(self, **kwargs):
                events.append(kwargs.get('text'))

            def progress(self, **kwargs):
                events.append(kwargs.get('text'))

            def panel(self, *args, **kwargs):
                return Layout(), Layout()

        wrapped = function('sidebar.py', '_SidebarLayout')(Layout())
        wrapped.label(text='Forbidden description')
        child = wrapped.row()
        child.label(text='Forbidden hint')
        child.use_property_split = True
        self.assertFalse(child.use_property_split)
        child.heading(text='Weapons')
        header, body = wrapped.panel('attachments')
        header.label(text='Attachments')
        body.label(text='Forbidden section description')
        wrapped.progress(factor=0.5, text='Forbidden progress description')
        self.assertEqual(events, ['Weapons', 'Attachments', ''])

    def test_weapon_selection_does_not_reapply_same_choice(self):
        scene = SimpleNamespace(arc_weapon_key='anvil', arc_weapon_browser_key='unchanged')
        owner = SimpleNamespace(arc_weapon_rows=[SimpleNamespace(key='anvil')], arc_weapon_rows_index=0)
        apply = function('Shared/ui_lists.py', '_apply_weapon', {'_SYNCING': False})
        apply(owner, SimpleNamespace(scene=scene))
        self.assertEqual(scene.arc_weapon_browser_key, 'unchanged')
        owner.arc_weapon_rows[0].key = 'bettina'
        apply(owner, SimpleNamespace(scene=scene))
        self.assertEqual(scene.arc_weapon_browser_key, 'bettina')

    def test_source_always_visible_and_file_imports_separate(self):
        events = []

        class Layout:
            def split(self, **kwargs):
                return self

            def prop_enum(self, owner, name, value, **kwargs):
                events.append(('prop', name))

            def box(self):
                return self

            def row(self, **kwargs):
                return self

            def column(self, **kwargs):
                return self

            def prop(self, owner, name, **kwargs):
                events.append(('prop', name))

            def operator(self, name, **kwargs):
                events.append(('operator', name, kwargs.get('text')))
                return SimpleNamespace()

        draw = function('sidebar.py', '_draw_import_section', {
            '_draw_pak_folder_picker': lambda *args: None,
            '_draw_fmodel_folder_picker': lambda *args: None,
        })
        for source, expected in (('PAK', 'arc_outfits.import_from_pak'), ('FILES', 'arc_outfits.import_single_psk')):
            with self.subTest(source=source):
                events.clear()
                draw(Layout(), SimpleNamespace(scene=SimpleNamespace(arc_import_source=source, arc_pak_dir='ready')))
                self.assertEqual(events[0], ('prop', 'arc_import_source'))
                texts = [event[2] for event in events if event[0] == 'operator']
                self.assertEqual(texts[:2], ['Import Single', 'Import Folder'])
                self.assertIn(expected, [event[1] for event in events])
                if source == 'FILES':
                    self.assertEqual(texts[2:], ['Import Again'])

    def test_removed_sections_and_exact_niagara_name(self):
        tree = ast.parse((ROOT / 'sidebar.py').read_text(encoding='utf-8-sig'))
        panel = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ARC_OUTFITS_PT_MainPanel')
        text = ast.unparse(panel)
        self.assertNotIn('arc_first_image', text)
        self.assertNotIn('First Image', (ROOT / 'sidebar.py').read_text(encoding='utf-8-sig'))
        self.assertNotIn('text="Import"', text)
        self.assertNotIn('_draw_editing_line', text)
        self.assertIn('Niagara Effects', text)

    def test_gear_sections_are_not_tabs(self):
        source = ast.unparse(ast.parse((ROOT / 'sidebar.py').read_text(encoding='utf-8-sig')))
        draw = source[source.index('def _draw_appearance_section'):source.index('def _anim_target')]
        self.assertNotIn('arc_gear_slot', draw)
        self.assertIn('_GEAR_SLOTS.items()', draw)

    def test_all_asset_lists_have_selection_callbacks(self):
        source = (ROOT / 'Shared/ui_lists.py').read_text(encoding='utf-8-sig')
        for kind in ('weapon', 'enemy'):
            self.assertIn('arc_' + kind + '_rows', source)
            self.assertIn('def _apply_' + kind, source)

    def test_import_again_reuses_last_path_without_dialog(self):
        tree = ast.parse((ROOT / 'Shared/operators.py').read_text(encoding='utf-8-sig'))
        names = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        self.assertIn('ARC_OUTFITS_OT_ImportAgain', names)
        text = ast.unparse(names['_queue_review'])
        self.assertIn("'EXEC_DEFAULT'", text)
        for cls in ('ARC_OUTFITS_OT_ImportSinglePSK', 'ARC_OUTFITS_OT_ImportOutfitFolder'):
            self.assertIn('remember_import', ast.unparse(names[cls]))
            self.assertIn('_last_import_dir', ast.unparse(names[cls]))


if __name__ == '__main__':
    unittest.main()
