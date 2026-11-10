"""Slot-aware preset parsing uses exact current Patcher metadata excerpts."""
import ast,json,os,sys
from pathlib import Path
from types import SimpleNamespace,ModuleType
from unittest.mock import patch
import unittest
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=json.loads((ROOT/'tests/fixtures/patcher_slot_modifiers_current.json').read_text())


def functions(data):
    names={'_soft_asset_path','get_part_key_from_asset_path','get_characters_rel_key','get_part_key',
           'parse_outfit_preset_slot_bindings','parse_outfit_preset_bindings','parse_outfit_preset',
           '_outfit_package_key','_merge_outfit_slot_records','resolve_outfit_preset_material_bindings'}
    tree=ast.parse((ROOT/'Shared/importing.py').read_text(encoding='utf-8'))
    body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    namespace={'os':os,'json':json,'__package__':'_outfit_slot_fixture.Shared',
      '_COLOURWAY_MOD_TYPES':{'CustomizationVisualPartSetMaterialModifier','CustomizationVisualPartSetMaterialPropertyModifier'},
      '_load_ue_json':lambda path:data.get(path),
      'utils':SimpleNamespace(ue_export_entries=lambda value:value if isinstance(value,list) else value.get('Exports',[value]),normalize_part_key=lambda key:key.casefold()),
      'textures':SimpleNamespace(_fs_abspath=lambda path:path,find_asset_from_object_path=lambda ref,ext:ref.split('.')[0]+ext),
      'bpy':SimpleNamespace(path=SimpleNamespace(abspath=lambda path:path))}
    exec(compile(ast.fix_missing_locations(ast.Module(body=body,type_ignores=[])),'Shared/importing.py','exec'),namespace)
    return namespace


class OutfitPresetSlotBindingTests(unittest.TestCase):
    def test_current_three_colorways_keep_shell_and_crt(self):
        for filename,entries in FIXTURE['presets'].items():
            n=functions({'preset':entries});bindings=n['parse_outfit_preset_slot_bindings']('preset')
            primary=n['parse_outfit_preset_bindings']('preset')
            for part in ['patcher/headgear','patcher/mask','patcher/pauldrone']:
                self.assertEqual({r['slot'] for r in bindings[part]},{0,1})
                self.assertIn('/Skins/',primary[part]);self.assertNotIn('MI_CRT',primary[part])
                self.assertTrue(any('MI_CRT_Screen_' in r['material'] for r in bindings[part] if r['slot']==1))
                self.assertTrue(all(r['part'].startswith('/Game/') for r in bindings[part]))
    def test_primary_is_order_independent(self):
        entries=next(iter(FIXTURE['presets'].values()))
        self.assertEqual(functions({'p':entries})['parse_outfit_preset_bindings']('p'),functions({'p':list(reversed(entries))})['parse_outfit_preset_bindings']('p'))
    def test_conflicting_same_slot_fails_instead_of_last_wins(self):
        f=functions({})['_merge_outfit_slot_records']
        with self.assertRaisesRegex(ValueError,'Distinct materials'):
            f([{'slot':1,'material':'/Game/A'},{'slot':1,'material':'/Game/B'}],['Shell','Screen'])
    def test_named_override_key_matches_native_section(self):
        f=functions({})['_merge_outfit_slot_records']
        self.assertEqual(f([{'slot':'Screen','material':'/Game/CRT'}],['Shell','Screen']),{'1':'/Game/CRT'})
        with self.assertRaisesRegex(ValueError,'no unique native'):
            f([{'slot':'Missing','material':'/Game/CRT'}],['Shell','Screen'])
    def test_exact_visual_part_mesh_prevents_tech_crt_on_plain_headgear(self):
        entries=next(iter(FIXTURE['presets'].values()));data={'preset':entries}
        for entry in entries:
            reference=entry['Properties']['Part']['AssetPathName'];part=reference.rsplit('/',1)[-1].split('.')[0]
            mesh='SK_Patcher_HeadgearTech' if part.endswith('HeadgearTech') else 'SK_Patcher_Headgear'
            if '/Headgear/' in reference:
                data[reference.split('.')[0]+'.json']=[{'Properties':{'Mesh':{'ObjectPath':'/Game/Pioneer/Characters/Assets/Patcher/Headgear/'+mesh+'.0'}}}]
        n=functions(data)
        package=ModuleType('_outfit_slot_fixture');package.__path__=[str(ROOT)]
        ue=ModuleType('_outfit_slot_fixture.Shared.uemodel_import')
        ue.parse_uemodel=lambda path:('mesh',10,[SimpleNamespace(name='LOD0',materials=[('Headgear','',0,1),('Screen','',3,1)])])
        native=ModuleType('_outfit_slot_fixture.Outfit_Accessories.native_material_slots');native.load_native_material_layout=lambda *args:None
        with patch.dict(sys.modules,{'_outfit_slot_fixture':package,'_outfit_slot_fixture.Shared.uemodel_import':ue,'_outfit_slot_fixture.Outfit_Accessories.native_material_slots':native}),patch('os.path.isfile',return_value=True):
            tech=n['resolve_outfit_preset_material_bindings']('preset','/Game/Pioneer/Characters/Assets/Patcher/Headgear/SK_Patcher_HeadgearTech.uemodel')
            plain=n['resolve_outfit_preset_material_bindings']('preset','/Game/Pioneer/Characters/Assets/Patcher/Headgear/SK_Patcher_Headgear.uemodel')
        self.assertEqual(set(tech),{'0','1'});self.assertEqual(set(plain),{'0'})
        self.assertIn('MI_CRT_Screen_',tech['1'])


if __name__=='__main__':unittest.main()

