"""Smoke-test a packaged addon through Blender's actual install operators.

Run by package_addon.ps1 in a private Blender profile.  A nonzero Python exit
code or a missing INSTALL_CHECK_OK marker blocks the package sync.
"""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy


def _expect_finished(label, result):
    if result != {'FINISHED'}:
        raise RuntimeError(f'{label} returned {result!r}')


def _check_enabled(folder, version):
    if bpy.context.preferences.addons.get(folder) is None:
        raise RuntimeError(f'{folder} is not enabled')
    if not addon_utils.check(folder)[1]:
        raise RuntimeError(f'{folder} failed addon_utils.check')
    module = importlib.import_module(folder)
    importlib.import_module(folder + ".material_texture_metadata")
    if tuple(module.bl_info['version']) != version:
        raise RuntimeError(f'{folder} version {module.bl_info["version"]!r} != {version!r}')
    if folder == 'DataRaiders-Outfits':
        expected_name = 'Arc Model Importer v' + '.'.join(str(part) for part in version)
        if module.bl_info.get('name') != expected_name:
            raise RuntimeError(f'Packaged addon name {module.bl_info.get("name")!r} != {expected_name!r}')
    weapon_ui = getattr(module, 'weapon_ui', None)
    if weapon_ui and hasattr(weapon_ui, '_finish_ascended_registration'):
        weapon_ui._finish_ascended_registration()
        if not weapon_ui._ASCENDED_ENUM_READY:
            raise RuntimeError('Ascended weapon controls did not finish registration')
    fit = importlib.import_module(folder + '.character_fit')
    overlay = importlib.import_module(folder + '.reference_overlay')
    pak_extract = importlib.import_module(folder + '.pak_extract')
    package_root = Path(module.__file__).parent.resolve()
    mapping = Path(pak_extract.bundled_usmap()).resolve()
    reference = Path(module.importing.default_outfit_csv_path()).resolve()
    if not mapping.is_relative_to(package_root) or not mapping.is_file() or not mapping.stat().st_size:
        raise RuntimeError('Packaged usmap is missing')
    if not reference.is_relative_to(package_root) or not reference.is_file() or not reference.stat().st_size:
        raise RuntimeError('Packaged outfit reference is missing')
    if hasattr(module, '_search_poll'):
        raise RuntimeError('idle search polling is still present')
    if bpy.app.timers.is_registered(overlay._timer):
        raise RuntimeError('reference overlay idle timer is still registered')
    if bpy.app.timers.is_registered(pak_extract._initialize_pak_defaults):
        raise RuntimeError('PAK startup initialization timer is still registered')
    if fit._load_fit in bpy.app.handlers.load_post:
        raise RuntimeError('character-fit load handler is still registered while idle')
    if fit._changed_fit in bpy.app.handlers.depsgraph_update_post:
        raise RuntimeError('character-fit depsgraph handler is still registered while idle')
    parity = importlib.import_module(folder + '.material_runtime.shader_parity.props')
    for collection in (bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.render_pre, bpy.app.handlers.frame_change_post):
        if any(getattr(handler, '__module__', '') == parity.__name__ for handler in collection):
            raise RuntimeError('shader parity handler is registered while idle')
    scene = bpy.context.scene
    watched_pointer = 8675309
    fit._WATCHED_IDS[scene.as_pointer()] = {watched_pointer}
    scheduled = []
    original_schedule = fit._schedule_sync
    original_signature = fit._scene_signature
    fit._schedule_sync = lambda: scheduled.append(True)
    fit._scene_signature = lambda _scene: (_ for _ in ()).throw(
        AssertionError('depsgraph handler must not walk a scene signature')
    )

    class FakeUpdatedID:
        def __init__(self, pointer):
            self.pointer = pointer

        def as_pointer(self):
            return self.pointer

    try:
        # An unrelated ID must not schedule a scene scan; an animated transform
        # on a tracked object must also be ignored.
        fit._changed_fit(scene, SimpleNamespace(updates=[SimpleNamespace(
            id=FakeUpdatedID(watched_pointer + 1), is_updated_transform=False,
            is_updated_geometry=False)]))
        fit._changed_fit(scene, SimpleNamespace(updates=[SimpleNamespace(
            id=FakeUpdatedID(watched_pointer), is_updated_transform=True,
            is_updated_geometry=False)]))
        if scheduled:
            raise RuntimeError('unrelated/transform depsgraph updates scheduled character-fit sync')

        # A real relevant update schedules one deferred reconciliation without
        # evaluating the O(scene objects) signature in the handler itself.
        fit._changed_fit(scene, SimpleNamespace(updates=[SimpleNamespace(
            id=FakeUpdatedID(watched_pointer), is_updated_transform=False,
            is_updated_geometry=True)]))
        if scheduled != [True]:
            raise RuntimeError('relevant depsgraph update did not schedule exactly one character-fit sync')
    finally:
        fit._schedule_sync = original_schedule
        fit._scene_signature = original_signature
        fit._WATCHED_IDS.pop(scene.as_pointer(), None)


def main():
    args = sys.argv[sys.argv.index('--') + 1:]
    if len(args) != 3:
        raise RuntimeError('expected ZIP path, addon folder, and dotted version')
    zip_path, folder, dotted_version = args
    version = tuple(int(part) for part in dotted_version.split('.'))

    _expect_finished('fresh ZIP install', bpy.ops.preferences.addon_install(filepath=zip_path, overwrite=True))
    _expect_finished('fresh enable', bpy.ops.preferences.addon_enable(module=folder))
    _check_enabled(folder, version)

    _expect_finished('disable', bpy.ops.preferences.addon_disable(module=folder))
    _expect_finished('re-enable', bpy.ops.preferences.addon_enable(module=folder))
    _check_enabled(folder, version)

    _expect_finished('overwrite enabled addon', bpy.ops.preferences.addon_install(filepath=zip_path, overwrite=True))
    _expect_finished('enable after overwrite', bpy.ops.preferences.addon_enable(module=folder))
    _check_enabled(folder, version)
    import runpy
    checks = runpy.run_path(str(Path(__file__).with_name('addon_sidebar_smoke.py')))
    checks['check_sidebar'](importlib.import_module(folder))
    checks['check_fx_actions'](importlib.import_module(folder))
    checks['check_controls'](importlib.import_module(folder))
    print(f'IDLE_WORK_CHECK_OK {folder}')
    print(f'INSTALL_CHECK_OK {folder} {dotted_version}')


if __name__ == '__main__':
    main()
