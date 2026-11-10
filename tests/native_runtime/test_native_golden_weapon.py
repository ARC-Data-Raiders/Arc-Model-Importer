try:
    from .native_test_support import ROOT,namespace,functions_module,PACKAGE,temp_fixture,create_shell_fixture,write_json
except ImportError:
    from native_test_support import ROOT,namespace,functions_module,PACKAGE,temp_fixture,create_shell_fixture,write_json
import json,unittest
from pathlib import Path
namespace()
from _native_runtime_regression_addon.Shared.materials.native.core import MaterialRecord,evaluate,literal
from _native_runtime_regression_addon.Weapons.material_runtime.weapons import compile_weapon,WEAPON_DETAIL_ARRAY,RUST_NORMAL,RUST_COLOR

class NativeNumericReplayCheckpoints(unittest.TestCase):
    def test_native_float32_instruction_replay_checkpoints(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/weapon_native_numeric_checkpoints.json').read_text(encoding='utf-8'))
        record=MaterialRecord(**fixture['record'])
        self.assertEqual(len(fixture['cases']),20)
        for row in fixture['cases']:
            textures=row['textures'];detail=row['detail'];refs={record.textures['NXM']:textures['t2'],record.textures['Weapon ID']:textures['t3'],record.textures['Wear']:textures['t4'],record.textures['Paintjob Pattern']:textures['t5'],RUST_NORMAL:textures['t6'],record.textures['CR']:textures['t7'],RUST_COLOR:textures['t8']}
            inputs={'uv':(.5,.5,0.),'texture':lambda ref,uv,attrs:detail[attrs['slice']] if ref==WEAPON_DETAIL_ARRAY else refs[ref]}
            plan=compile_weapon(record,{'weapon_detail_amount':literal(row['quality'])})
            with self.subTest(quality=row['quality'],case=row['case']):
                for output,expected in row['expected'].items():
                    actual=evaluate(plan.outputs[output],inputs)
                    for a,b in zip(actual if isinstance(actual,tuple) else (actual,),expected if isinstance(expected,list) else (expected,)):
                        self.assertAlmostEqual(a,b,delta=.0003,msg=output+' independent native float32 checkpoint')
