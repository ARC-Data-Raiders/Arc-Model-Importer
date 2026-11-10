"""Run with Blender --background --factory-startup --python this.py.

Colourway ticks in the confirm dialog must show in the per-part skin dropdown.
"""
import importlib, os, sys, tempfile
from pathlib import Path


def main():
    import bpy
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root.parent))
    addon = importlib.import_module(root.name)
    addon.register()
    props = importlib.import_module(root.name + '.Outfit_Accessories.properties')
    importing = importlib.import_module(root.name + '.Shared.importing')
    scene = bpy.context.scene
    tmp = Path(tempfile.mkdtemp())
    psk = str(tmp / 'SK_Goalie_Boots.uemodel')
    leather = str(tmp / 'Skins/Leather/MI_Goalie_Boots_Leather.arc-material.json')
    cyan = str(tmp / 'Skins/Leather_CyanWhite/MI_Goalie_Boots_CyanWhite.arc-material.json')
    entry = scene.arc_psk_entries.add()
    entry.psk_path = psk
    skins = [('Leather', leather), ('Leather_CyanWhite', cyan)]
    failures = []

    def check(name, ok):
        print(('PASS ' if ok else 'FAIL ') + name)
        if not ok:
            failures.append(name)

    def open_dialog():
        props.publish_confirm_dialog_cache({
            'fingerprint': props.confirm_dialog_fingerprint(bpy.context),
            'entries': {bpy.path.abspath(psk): {
                'manual_folder': '', 'skins': skins, 'default_dir': 'Leather',
                'skin_items': props._skin_enum_items(skins, 'Leather'),
                'is_body': False, 'is_hair': False, 'model_type': 'clothing', 'hair_mis': []}},
            'is_outfit_queue': True, 'outfit_preset_items': [('NONE', '-', '')]})

    # Preset resolves the authored MI_*.json while the dropdown lists the .arc-material twin.
    authored = cyan.replace('.arc-material.json', '.json')
    open_dialog()
    importing._show_skin_in_dropdown(bpy.context, entry, authored)
    entry.batch_skin_json = authored
    check('dropdown shows colourway MI', entry.skin_choice == cyan)
    check('batch MI kept after mirror', entry.batch_skin_json == authored)
    importing._show_skin_in_dropdown(bpy.context, entry, leather.replace('.arc-material.json', '.json'))
    check('default colourway shows Default item', entry.skin_choice == 'NONE')

    props.clear_confirm_dialog_cache()
    lookups = []
    real_lookup = props.skin_choice_for_json
    props.skin_choice_for_json = lambda *a: lookups.append(a)
    importing._show_skin_in_dropdown(bpy.context, entry, leather)
    props.skin_choice_for_json = real_lookup
    check('no mirror outside dialog (batch runs)', lookups == [])

    # A tick only marks the colourway; the preset is resolved on OK or by the Apply button.
    calls = []
    real_apply = importing._apply_outfit_preset
    importing._apply_outfit_preset = lambda ctx, path: calls.append(path) or 1
    sel_a = scene.arc_outfit_selections.add(); sel_a.preset_name = 'A'; sel_a.json_path = str(tmp / 'DA_OI_A.json')
    sel_b = scene.arc_outfit_selections.add(); sel_b.preset_name = 'B'; sel_b.json_path = str(tmp / 'DA_OI_B.json')
    open_dialog()
    sel_a.selected = True
    sel_b.selected = True
    sel_b.selected = False
    importing._apply_outfit_preset = real_apply
    check('tick does no preset work while the dialog is open', calls == [])
    check('tick state kept', sel_a.selected and not sel_b.selected)
    props.clear_confirm_dialog_cache()
    addon.unregister()
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
