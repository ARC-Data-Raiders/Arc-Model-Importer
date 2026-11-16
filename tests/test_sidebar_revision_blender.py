import ast
import importlib
from pathlib import Path
import sys
import unittest

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent / 'native_runtime'))
from native_test_support import namespace

namespace()
lists = importlib.import_module('_native_runtime_regression_addon.Shared.ui_lists')


class SidebarRevisionBlenderTests(unittest.TestCase):
    def test_section_icons_exist_in_blender(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / 'sidebar.py').read_text(encoding='utf-8-sig'))
        assignment = next(node for node in source.body if isinstance(node, ast.Assign)
                          and any(getattr(target, 'id', '') == '_SECTION_ICONS' for target in node.targets))
        icons = ast.literal_eval(assignment.value)
        valid = bpy.types.UILayout.bl_rna.functions['label'].parameters['icon'].enum_items.keys()
        for icon in icons.values():
            self.assertIn(icon, valid)
        browser = importlib.import_module('_native_runtime_regression_addon.Outfit_Accessories.icon_browser')
        kinds = {item[0]: item[3] for item in browser.KIND_ITEMS}
        for kind in ('weapon', 'enemy', 'effect'):
            self.assertIn(kinds[kind], valid)
        print('ICONS_OK', icons['arc_weapons'], icons['arc_enemies'], icons['arc_niagara_fx'])

    def test_asset_browser_registration_and_selection(self):
        lists.register()
        bpy.types.Scene.arc_weapon_key = bpy.props.StringProperty(default='first')
        bpy.types.Scene.arc_weapon_browser_key = bpy.props.StringProperty()
        bpy.types.Scene.arc_enemy_key = bpy.props.StringProperty()
        try:
            wm, scene = bpy.context.window_manager, bpy.context.scene
            for kind in ('weapon', 'enemy'):
                rows = getattr(wm, 'arc_' + kind + '_rows')
                rows.clear()
                row = rows.add()
                row.key, row.label = 'selected', 'Selected'
                setattr(wm, 'arc_' + kind + '_rows_index', 0)
                self.assertEqual(getattr(scene, 'arc_' + kind + '_search'), '')
            self.assertEqual(scene.arc_weapon_browser_key, 'selected')
            self.assertEqual(scene.arc_enemy_key, 'selected')
            print('BROWSER_RNA_OK weapon_selection=1 enemy_selection=1')
        finally:
            lists.unregister()
            for name in ('arc_weapon_key', 'arc_weapon_browser_key', 'arc_enemy_key'):
                delattr(bpy.types.Scene, name)
        self.assertFalse(hasattr(bpy.types.WindowManager, 'arc_weapon_rows'))
        self.assertFalse(hasattr(bpy.types.Scene, 'arc_enemy_search'))


if __name__ == '__main__':
    unittest.main()
