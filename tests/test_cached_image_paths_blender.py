"""Run with Blender --python this.py -- --output <scratch directory>."""
from pathlib import Path

def main():
    import argparse, importlib, json, sys
    import bpy
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    output = Path(args.output).resolve()
    (output / 'original').mkdir(parents=True, exist_ok=True)
    (output / 'moved').mkdir(exist_ok=True)
    sys.pycache_prefix = str(output / 'pycache')
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root.parent))
    common = importlib.import_module(root.name + '.Shared.materials.common')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    texture = output / 'original/texture.png'
    generated = bpy.data.images.new('Path regression', width=2, height=2)
    generated.pixels[:] = [.3, .5, .7, 1] * 4
    generated.filepath_raw = str(texture)
    generated.file_format = 'PNG'
    generated.save()
    bpy.data.images.remove(generated)
    bpy.ops.wm.save_as_mainfile(filepath=str(output / 'original/source.blend'))
    image = bpy.data.images.load(str(texture))
    image.filepath = '//texture.png'
    pixels = list(image.pixels[:])
    key = common._norm_path_key(common.textures._fs_abspath(str(texture)))
    common._IMAGE_BY_PATH[key] = image
    reused = common._load_image_cached(str(texture))
    color = common._load_image_cached(str(texture), non_color=False)
    data = common._load_image_cached(str(texture), non_color=True)
    checks = {
        'same_buffer': reused == image,
        'same_pixels': pixels == list(reused.pixels[:]),
        'absolute_path': not reused.filepath.startswith('//') and Path(reused.filepath).is_absolute(),
        'isolated_sampling_roles': color != data and color.colorspace_settings.name == 'sRGB' and data.colorspace_settings.name == 'Non-Color',
    }
    bpy.ops.wm.save_as_mainfile(filepath=str(output / 'moved/copy.blend'), relative_remap=False)
    checks['same_file_after_move'] = Path(bpy.path.abspath(reused.filepath)).is_file()
    result = {'passed': all(checks.values()), 'checks': checks, 'module': common.__file__}
    (output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    assert result['passed'], result
    print('IMAGE_CACHE_PATH_REGRESSION_PASS', flush=True)

if __name__ == '__main__':
    main()
