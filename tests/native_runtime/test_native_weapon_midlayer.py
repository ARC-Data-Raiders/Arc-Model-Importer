try:
    from .native_test_support import ROOT
except ImportError:
    from native_test_support import ROOT
try: from .native_test_support import real_package as _ns
except ImportError: from native_test_support import real_package as _ns
_ns()
import json,unittest
from pathlib import Path
from _native_runtime_regression_addon.Shared.materials.native.core import MaterialRecord,evaluate,literal
from _native_runtime_regression_addon.Weapons.material_runtime.weapons import compile_weapon,WEAPON_DETAIL_ARRAY,RUST_NORMAL,RUST_COLOR,WEAR_PROPERTY

class NativeMidLayerCheckpoints(unittest.TestCase):
    def test_mid_layer_matches_cooked_bettina_pixel_shader(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/weapon_native_midlayer_checkpoints.json').read_text(encoding='utf-8'))
        record=MaterialRecord(**{k:v for k,v in fixture['record'].items()})
        self.assertTrue(record.switches['Mid Layer - Use - Green'] and record.switches['Mid Layer - Use - Alpha'])
        for row in fixture['cases']:
            t=row['textures']
            refs={record.textures['NXM']:t['NXM'],record.textures['Weapon ID']:t['Weapon ID'],record.textures['Wear']:t['Wear'],record.textures['CR']:t['CR'],RUST_NORMAL:t['RustN'],RUST_COLOR:t['RustCR']}
            inputs={'uv':(.25,.25,0.),'texture':lambda ref,uv,attrs:tuple(t['Detail'][attrs['slice']]) if ref==WEAPON_DETAIL_ARRAY else tuple(refs[ref])}
            plan=compile_weapon(record,{'weapon_detail_amount':literal(row['quality'])})
            with self.subTest(case=row['case'],quality=row['quality']):
                for output,expected in row['expected'].items():
                    actual=evaluate(plan.outputs[output],inputs)
                    for a,b in zip(actual if isinstance(actual,tuple) else (actual,),expected if isinstance(expected,list) else (expected,)):
                        self.assertAlmostEqual(a,b,delta=1e-5,msg=output)

    def test_wear_defaults_to_per_object_custom_primitive_data(self):
        record=MaterialRecord('/Game/test/w',textures={'CR':'/Game/cr','NXM':'/Game/nxm','Weapon ID':'/Game/id','Wear':'/Game/wear'},switches={'Enable Red Mask':True,'UseWeaponQuality':True})
        plan=compile_weapon(record)
        found=[]
        from _native_runtime_regression_addon.Shared.materials.native.core import Expr
        def walk(node):
            if not isinstance(node,Expr):return
            if node.op=='attribute':found.append((node.attrs.get('name'),node.attrs.get('type')))
            for a in node.args:walk(a)
        for n in plan.outputs.values():walk(n)
        self.assertIn((WEAR_PROPERTY,'OBJECT'),found)
        self.assertFalse(any('custom primitive data1' in d for d in plan.diagnostics))

if __name__=='__main__':unittest.main()
