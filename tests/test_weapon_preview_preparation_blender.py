"""Verify weapon preview preparation in an isolated Blender process."""
import ast
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys


def load_ui(root):
    source = root / 'Weapons/weapon_ui.py'
    tree = ast.parse(source.read_text(encoding='utf-8-sig'), filename=str(source))
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or node.level == 0]
    module = ModuleType('_weapon_preview_preparation_test')
    module.assets = SimpleNamespace(invalidate=lambda: None)
    sys.modules[module.__name__] = module
    exec(compile(tree, str(source), 'exec'), module.__dict__)
    if not hasattr(module, 'draw_gun_preview'):
        # Commit 5f70ddb moved draw_gun_preview into sidebar.py; it reads state from Weapons.weapon_ui.
        import bpy
        sidebar = root / 'sidebar.py'
        nodes = [node for node in ast.parse(sidebar.read_text(encoding='utf-8-sig')).body
                 if isinstance(node, ast.FunctionDef) and node.name == 'draw_gun_preview']
        namespace = {'bpy': bpy, '_lazy': lambda name: module, '_resolved_str': lambda path: str(Path(path).resolve())}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(sidebar), 'exec'), namespace)
        module.draw_gun_preview = namespace['draw_gun_preview']
    return module


class Layout:
    def __init__(self):
        self.textures = []

    def row(self):
        return self

    def label(self, **kwargs):
        assert kwargs == {'text': ''}

    def template_preview(self, texture, **kwargs):
        assert kwargs == {'show_buttons': False, 'preview_id': 'arc_weapon_gun_fixed_5x2'}
        self.textures.append(texture)


def main():
    import argparse
    import json
    import bpy
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    root = Path(__file__).resolve().parents[1]
    ui = load_ui(root)
    # Generated previews ship as AVIF since commit 5f70ddb; sample evenly to keep the run short.
    generated = root / 'assets/weapon_browser/generated'
    paths = sorted([*generated.glob('*.png'), *generated.glob('*.avif')])
    paths = paths[::max(1, len(paths) // 24)]
    assert len(paths) > 1
    initial_images = set(bpy.data.images)
    initial_textures = set(bpy.data.textures)
    background = np.asarray(bpy.context.preferences.themes[0].user_interface.panel_back[:3], dtype=np.float32)
    max_error = 0.0
    for path in paths:
        key = str(path.resolve())
        ui._GUN_PENDING.add(key)
        ui._prepare_gun_textures()
        assert ui._PREVIEWS is None, 'Texture-only preparation must not allocate an ImagePreview'
        texture = ui._GUN_TEXTURES[key]
        actual = np.empty(512 * 205 * 4, dtype=np.float32)
        texture.image.pixels.foreach_get(actual)
        ui.gun_preview_id(path)
        preview = ui._PREVIEWS['gun:' + key]
        assert tuple(preview.image_size) == (512, 205)
        reference = np.asarray(preview.image_pixels_float[:], dtype=np.float32).reshape(-1, 4)
        alpha = reference[:, 3:4]
        reference[:, :3] = reference[:, :3] * alpha + background * (1 - alpha)
        reference[:, 3] = 1
        expected_image = bpy.data.images.new('.weapon_preview_expected', width=512, height=205, alpha=True)
        expected_image.pixels.foreach_set(reference.ravel())
        expected = np.empty_like(actual)
        expected_image.pixels.foreach_get(expected)
        error = float(np.max(np.abs(actual - expected)))
        max_error = max(max_error, error)
        assert error == 0, (path.name, error)
        assert texture.extension == 'EXTEND' and abs(texture.crop_max_x - .4) < 1e-6
        assert texture.use_preview_alpha and tuple(texture.image.size) == (512, 205)
        assert not texture.image.use_fake_user and not texture.use_fake_user
        ui._GUN_PENDING.add(key)
        ui._prepare_gun_textures()
        assert ui._GUN_TEXTURES[key] == texture
        bpy.data.images.remove(expected_image)
        ui.unregister()
        assert set(bpy.data.images) == initial_images
        assert set(bpy.data.textures) == initial_textures

    context = SimpleNamespace(region=SimpleNamespace(width=320, as_pointer=lambda: 42), preferences=bpy.context.preferences)
    layout = Layout()
    first, second = (str(path.resolve()) for path in paths[:2])
    ui._GUN_PENDING.add(first)
    ui._prepare_gun_textures()
    ui.draw_gun_preview(layout, context, first)
    previous = layout.textures[-1]
    ui.draw_gun_preview(layout, context, second)
    assert layout.textures[-1] == previous and second in ui._GUN_PENDING
    assert bpy.app.timers.is_registered(ui._prepare_gun_textures)
    ui._prepare_gun_textures()
    ui.draw_gun_preview(layout, context, second)
    assert layout.textures[-1] != previous
    replacement = layout.textures[-1]
    ui.draw_gun_preview(layout, context, first)
    assert layout.textures[-1] == previous
    ui.draw_gun_preview(layout, context, second)
    assert layout.textures[-1] == replacement and not ui._GUN_PENDING
    missing = args.output.parent / 'missing-weapon-preview.png'
    ui.draw_gun_preview(layout, context, missing)
    assert layout.textures[-1] == replacement
    displayed = len(layout.textures)
    ui._prepare_gun_textures()
    ui.draw_gun_preview(layout, context, missing)
    assert str(missing.resolve()) in ui._GUN_FAILED
    assert len(layout.textures) == displayed and not ui._GUN_PENDING
    ui.draw_gun_preview(layout, context, None)
    assert len(layout.textures) == displayed
    corrupt = args.output.parent / 'corrupt-weapon-preview.png'
    corrupt.write_bytes(b'not a PNG')
    ui.draw_gun_preview(layout, context, second)
    ui.draw_gun_preview(layout, context, corrupt)
    assert layout.textures[-1] == replacement
    displayed = len(layout.textures)
    ui._prepare_gun_textures()
    ui.draw_gun_preview(layout, context, corrupt)
    assert str(corrupt.resolve()) in ui._GUN_FAILED and len(layout.textures) == displayed
    corrupt.unlink()
    assert ui.gun_preview_id(None) == ui.gun_preview_id(missing) == 0
    ui.unregister()
    ui.unregister()
    assert not bpy.app.timers.is_registered(ui._prepare_gun_textures)
    assert not ui._GUN_TEXTURES and not ui._GUN_PENDING and not ui._GUN_DISPLAY and not ui._GUN_FAILED
    assert ui._PREVIEWS is None
    assert set(bpy.data.images) == initial_images and set(bpy.data.textures) == initial_textures
    result = {'passed': True, 'images': len(paths), 'max_pixel_error': max_error,
              'cold_and_warm_switching': True, 'missing_and_corrupt_files': True,
              'unregister_cleanup': True, 'native_visual_latency': 'unverified'}
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print('WEAPON_PREPARATION_TEST', json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
