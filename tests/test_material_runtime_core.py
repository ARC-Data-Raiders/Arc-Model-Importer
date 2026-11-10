import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'/'native_runtime'));__import__('native_test_support').real_package()
from _native_runtime_regression_addon.Shared.materials.native import *
from _native_runtime_regression_addon.Shared.materials.native.repository import digest
from _native_runtime_regression_addon.Shared.materials.native.core import base_property_overrides

class NumericTests(unittest.TestCase):
    def test_negative_world_triplanar_reference(self):
        from _native_runtime_regression_addon.Shared.materials.native.interop import triplanar_reference
        coordinates,weights=triplanar_reference((-.3,-.7,-.2),(-.2,.3,.9),200)
        expected=((.35,-.1,0),(-.15,-.1,0),(-.15,.35,0))
        for axis,target in zip(coordinates,expected):
            for actual,wanted in zip(axis,target): self.assertAlmostEqual(actual,wanted)
        for actual,wanted in zip(weights,(1/7,3/14,9/14)): self.assertAlmostEqual(actual,wanted)
        self.assertAlmostEqual(sum(weights),1)
        self.assertEqual(triplanar_reference((-3,5,7),(0,0,0),0),(((0.,0.,0.),(0.,0.,0.),(0.,0.,0.)),(0.,0.,0.)))
    def test_shared_dag_hash_and_evaluation_are_memoized(self):
        from _native_runtime_regression_addon.Shared.materials.native.core import expression_fingerprint
        source=expr('texture',reference='/Game/T',role='data',uv=literal((0,0,0)))
        value=expr('channel',source,index=0)
        for _ in range(100): value=expr('add',value,value)
        calls=[]
        result=evaluate(value,{'texture':lambda *args:(calls.append(1) or (.5,0,0,1))})
        self.assertEqual(result,.5*2**100); self.assertEqual(len(calls),1)
        memo={}; first=expression_fingerprint(value,memo)
        self.assertEqual(len(memo),103)
        self.assertEqual(expression_fingerprint(value,memo),first)
        changed=expr('texture',reference='/Game/T',role='color',uv=literal((0,0,0)))
        self.assertNotEqual(expression_fingerprint(source),expression_fingerprint(changed))
    def test_immutable_and_serializable(self):
        r=MaterialRecord('/Game/A',scalars={'zero':0},switches={'off':False})
        with self.assertRaises(TypeError): r.scalars['zero']=1
        e=expr('multiply',literal((1,2,3)),literal(2))
        self.assertEqual(evaluate(e),(2,4,6)); json.dumps(e.to_dict())
    def test_random_mix_endpoints_and_linearity(self):
        rng=random.Random(944)
        for _ in range(120):
            x,y=[tuple(rng.uniform(-4,4) for i in range(3)) for j in range(2)]
            self.assertEqual(evaluate(expr('mix',literal(x),literal(y),literal(0))),x)
            self.assertEqual(evaluate(expr('mix',literal(x),literal(y),literal(1))),y)
            t=rng.random(); result=evaluate(expr('mix',literal(x),literal(y),literal(t)))
            for i in range(3): self.assertAlmostEqual(result[i],x[i]+(y[i]-x[i])*t)
    def test_coordinate_width_promotes_only_uv_zero_z(self):
        expression=expr('vector_add',expr('vector_scale',expr('uv'),literal((2.,3.))),literal((.25,-.5)))
        self.assertEqual(evaluate(expression,{'uv':(.1,.2,0.)}),(.45,.10000000000000009,0.))
        self.assertEqual(evaluate(expression,{'uv':(.1,.2)}),(.45,.10000000000000009))
        with self.assertRaises(ValueError): evaluate(expr('multiply',literal((1,2,3)),literal((4,5))))
        with self.assertRaises(ValueError): evaluate(expr('multiply',literal((1,2,3,4)),literal((4,5,6))))
    def test_native_settings_normalization_preserves_false_zero_and_known_enum(self):
        from _native_runtime_regression_addon.Shared.materials.native.core import normalize_material_settings
        settings,diagnostics=normalize_material_settings({'TwoSided':True,'two_sided':False,'BlendMode':2,'blend':0,'OpacityMaskClipValue':.5,'cutoff':0})
        self.assertFalse(settings['two_sided']); self.assertEqual(settings['blend'],'opaque'); self.assertEqual(settings['cutoff'],0)
        for raw in (1,'EBlendMode::BLEND_Masked','EMaterialBlendMode::Masked','masked'):
            normalized,_=normalize_material_settings({'bTwoSided':0,'BlendMode':raw})
            self.assertEqual(normalized['blend'],'masked'); self.assertEqual(normalized['render_method'],'DITHERED'); self.assertFalse(normalized['two_sided'])
        for raw in (2,'EBlendMode::BLEND_TranslucentGreyTransmittance','translucent'):
            normalized,_=normalize_material_settings({'TwoSided':True,'BlendMode':raw})
            self.assertEqual(normalized['blend'],'translucent'); self.assertEqual(normalized['render_method'],'DITHERED'); self.assertTrue(normalized['two_sided'])
        for raw in (3,4,5,6,99,'unknown','BLEND_Additive'):
            normalized,diagnostics=normalize_material_settings({'BlendMode':raw})
            self.assertFalse(normalized['blend_supported']); self.assertTrue(any('unsupported_blend_physics' in value for value in diagnostics))
        normalized,diagnostics=normalize_material_settings({'BlendMode':7})
        self.assertEqual(normalized['blend'],'translucent_colored'); self.assertTrue(diagnostics)
    def test_only_explicit_opaque_mode_ignores_alpha_output(self):
        from _native_runtime_regression_addon.Shared.materials.native.core import ignores_alpha_output
        self.assertTrue(ignores_alpha_output({'BlendMode':0}))
        self.assertTrue(ignores_alpha_output({'blend':'opaque','BlendMode':2}))
        self.assertFalse(ignores_alpha_output({})); self.assertFalse(ignores_alpha_output({'BlendMode':1})); self.assertFalse(ignores_alpha_output({'blend':'translucent'}))
    def test_normal_properties(self):
        self.assertEqual(evaluate(expr('normal_decode',literal((.5,.5,1,1)))),(0.,0.,1.))
        for x in ((0.,0.,1.),(.3,.4,math.sqrt(.75))):
            result=evaluate(expr('rnm',literal(x),literal((0.,0.,1.))))
            for i in range(3): self.assertAlmostEqual(result[i],x[i])
            self.assertAlmostEqual(sum(v*v for v in result),1)
    def test_ops_and_inputs(self):
        vectors={'uv':(0.1,0.2,0),'geometry_normal':(0,0,1),'incoming':(0,1,0),'backfacing':0,'object_position':(1,2,3),'camera_distance':4,'attribute':{'Color':(1,0,0,1)},'time':3}
        for key in vectors:
            e=expr(key,name='Color') if key=='attribute' else expr(key)
            self.assertEqual(evaluate(e,vectors),vectors[key]['Color'] if key=='attribute' else vectors[key])
        tests=[('add',(2,3),5),('subtract',(2,3),-1),('multiply',(2,3),6),('divide',(2,0),0),('power',(2,3),8),('min',(2,3),2),('max',(2,3),3),('clamp',(2,0,1),1),('abs',(-2,),2),('floor',(-.2,),-1),('fract',(-.2,),.8),('sqrt',(4,),2),('sin',(0,),0),('cos',(0,),1),('less_than',(2,3),1),('greater_than',(3,2),1),('compare',(2,2),1)]
        for op,args,result in tests: self.assertAlmostEqual(evaluate(expr(op,*(literal(a) for a in args))),result)
        self.assertEqual(evaluate(expr('cross',literal((1,0,0)),literal((0,1,0)))),(0,0,1))
        self.assertEqual(evaluate(expr('dot',literal((1,2,3)),literal((4,5,6)))),32)
        self.assertEqual(evaluate(expr('normalize',literal((0,0,0)))),(0,0,0))
        self.assertEqual(evaluate(expr('channel',literal((1,2,3)),index=1)),2)
        self.assertEqual(evaluate(expr('combine',literal(1),literal(2),literal(3))),(1,2,3))
    def test_texture_is_explicit_and_linear(self):
        calls=[]
        def sample(ref,uv,attrs): calls.append((ref,uv,attrs['role'])); return (.2,.3,.4,.5)
        e=expr('texture',reference='/Game/T',role='data')
        self.assertEqual(evaluate(e,{'texture':sample,'uv':(.1,.2,0)}),(.2,.3,.4,.5))
        self.assertEqual(calls,[('/Game/T',(.1,.2,0),'data')])
        with self.assertRaises(KeyError): evaluate(e)
        with self.assertRaises(NotImplementedError): evaluate(expr('unknown'))

class ParserTests(unittest.TestCase):
    def test_scoped_zero_false_and_defaults(self):
        raw={'Exports':[{'Type':'MaterialInstanceConstant','Package':'/Game/C','Properties':{'Parent':{'ObjectPath':'/Game/P.0'},'ScalarParameterValues':[{'ParameterInfo':{'Name':'S'},'ParameterValue':0},{'ParameterInfo':{'Name':'S','Association':'EMaterialParameterAssociation::LayerParameter','Index':2},'ParameterValue':.4}], 'StaticParametersRuntime':{'StaticSwitchParameters':[{'ParameterInfo':{'Name':'on'},'Value':False},{'ParameterInfo':{'Name':'ignored'},'Value':True,'bOverride':False}]},'BasePropertyOverrides':{'bOverride_TwoSided':True,'BlendMode':'EBlendMode::BLEND_Masked'}}}]}
        child=parse_material(raw,'/Game/C'); parent=MaterialRecord('/Game/P','/Game/M',scalars={'S':1,'inherited':2},switches={'on':True},settings={'two_sided':True,'blend':'EBlendMode::BLEND_Translucent'})
        r=merge_records(parent,child)
        self.assertEqual(r.master,'/Game/M'); self.assertEqual(r.scalars['S'],0); self.assertFalse(r.switches['on']); self.assertNotIn('ignored',r.switches)
        # The enabled TwoSided override is omitted at its false default; the unflagged blend is a stale parent copy.
        self.assertEqual(r.scopes[('LayerParameter',2)]['scalars']['S'],.4); self.assertFalse(r.settings['two_sided']); self.assertEqual(r.settings['blend'],'EBlendMode::BLEND_Translucent')
    def test_base_property_overrides_follow_ue_serialization(self):
        # MI_HighwayAddon_Window_01: a one-sided override of the two-sided M_GlassOpaque_NX_01. UE copied the parent's
        # blend, shading model and clip into the struct for the disabled overrides and omitted TwoSided=false.
        bpo={'bOverride_TwoSided':True,'BlendMode':'EBlendMode::BLEND_Masked','ShadingModel':'EMaterialShadingModel::MSM_TwoSidedFoliage','OpacityMaskClipValue':.3333}
        self.assertEqual(base_property_overrides(bpo),{'TwoSided':False})
        self.assertEqual(base_property_overrides(bpo,snapshot=True),{'TwoSided':False,'BlendMode':'EBlendMode::BLEND_Masked','ShadingModel':'EMaterialShadingModel::MSM_TwoSidedFoliage','OpacityMaskClipValue':.3333})
        self.assertEqual(base_property_overrides({'bOverride_BlendMode':True,'bOverride_OpacityMaskClipValue':True,'OpacityMaskClipValue':.5,'bOverride_CastDynamicShadowAsMasked':True}),{'BlendMode':'EBlendMode::BLEND_Opaque','OpacityMaskClipValue':.5,'bCastDynamicShadowAsMasked':False})
        self.assertEqual(base_property_overrides({'bOverride_TwoSided':False,'TwoSided':True}),{})
        self.assertEqual(base_property_overrides(None),{})
    def test_master_expression_defaults(self):
        raw=[{'Type':'Material','Properties':{}},{'Type':'MaterialExpressionScalarParameter','Properties':{'ParameterName':'native','DefaultValue':0}},{'Type':'MaterialExpressionVectorParameter','Properties':{'ParameterName':'C','DefaultValue':{'R':0,'G':1,'B':0,'A':0}}}]
        r=parse_material(raw,'/Game/M'); self.assertEqual(r.scalars['native'],0); self.assertEqual(r.colors['C'],(0,1,0,0))
    def test_cached_scope_and_null_texture_override(self):
        raw={'Type':'Material','CachedExpressionData':{'RuntimeEntries':{'ParameterInfoSet':[{'Name':'Zero','Association':'LayerParameter','Index':1}]},'ScalarValues':[0], 'RuntimeEntries[8]':{'ParameterInfoSet':[{'Name':'Off','Association':'LayerParameter','Index':1}]},'StaticSwitchValues':[False]},'Properties':{'TextureParameterValues':[{'ParameterInfo':{'Name':'Disabled'},'ParameterValue':None}]}}
        native=parse_material(raw,'/Game/Native')
        self.assertEqual(native.scopes[('LayerParameter',1)]['scalars']['Zero'],0)
        self.assertFalse(native.scopes[('LayerParameter',1)]['switches']['Off'])
        self.assertIsNone(native.textures['Disabled'])
        packaged=parse_material({'textures':{'Named':{'Package':'PioneerGame/Content/A/T'}}},'/Game/Native')
        self.assertEqual(packaged.textures['Named'],'/Game/A/T')
        with self.assertRaises(ValueError): parse_material({'textures':{'Named':{'ObjectName':"Texture2D'T'"}}},'/Game/Native')
        merged=merge_records(MaterialRecord('/Game/P',textures={'Disabled':'/Game/T'}),native)
        self.assertIn('Disabled',merged.textures); self.assertIsNone(merged.textures['Disabled'])
        raw['CachedExpressionData']['ScalarValues']=[]
        with self.assertRaises(ValueError): parse_material(raw,'/Game/Native')
    def test_declared_mount_aliases_are_fixed_prefix_only(self):
        for source,target in (('PioneerGame/Content/A/T','/Game/A/T'),('RemappedPlugins/Content/A/T','/Game/A/T'),('RemappedPlugins/EmbarkScript/Content/A/T','/EmbarkScript/A/T'),('/EmbarkScript/A/T','/EmbarkScript/A/T'),('Engine/Content/A/T','/Engine/A/T'),('PioneerGame\\Content\\A\\T','/Game/A/T')):
            self.assertEqual(package_ref(source),target)
        self.assertEqual(package_ref('RemappedPlugins/Known/Content/A/T',{'RemappedPlugins/Known/Content':'/Known'}),'/Known/A/T')
        with self.assertRaises(ValueError): package_ref('OtherProject/Content/A/T')
        with self.assertRaises(ValueError): package_ref('RemappedPlugins/Unknown/Content/A/T')
        self.assertEqual(package_ref('/Unknown/Content/A/T'),'/Unknown/Content/A/T')
    def test_exact_reference(self):
        self.assertEqual(package_ref("Texture2D'/Game/A/T.T'"),'/Game/A/T')
        with self.assertRaises(ValueError): package_ref('T')
        with self.assertRaises(ValueError): package_ref('/Game/../T')

class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests'); self.path=Path(self.tmp.name)
        (self.path/'A').mkdir(); (self.path/'B').mkdir()
    def tearDown(self): self.tmp.cleanup()
    def write(self,name,data):
        p=self.path/(name+'.json'); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(data)); return p
    @unittest.skipUnless(os.name=='nt','Windows extended-prefix path fixture')
    def test_extended_prefix_survives_asset_and_digest_resolution(self):
        from _native_runtime_regression_addon.Shared.materials.native.repository import resolved_io_path
        self.write('A/Long',{'scalars':{'zero':0}})
        prefix=chr(92)*2+'?'+chr(92)
        repo=AssetRepository({'/Game':prefix+str(self.path.resolve())})
        source=repo.resolve('/Game/A/Long')
        self.assertTrue(str(source).startswith(prefix)); self.assertTrue(str(resolved_io_path(source)).startswith(prefix))
        self.assertEqual(repo.load_material('/Game/A/Long').scalars['zero'],0)
        self.assertEqual(len(digest(source)),64)
    def test_bounded_stat_digest_cache_and_explicit_invalidation(self):
        import hashlib
        from unittest.mock import patch
        from _native_runtime_regression_addon.Shared.materials.native.repository import digest,invalidate_digest_cache,_DIGEST_CACHE,_DIGEST_LIMIT
        image=self.path/'A/T.png'; image.write_bytes(b'first')
        invalidate_digest_cache()
        with patch('_native_runtime_regression_addon.Shared.materials.native.repository.hashlib.sha256',wraps=hashlib.sha256) as hashing:
            first=digest(image); self.assertEqual(digest(image),first); self.assertEqual(hashing.call_count,1)
            image.write_bytes(b'second changed size')
            self.assertNotEqual(digest(image),first); self.assertEqual(hashing.call_count,2)
            invalidate_digest_cache(image); digest(image); self.assertEqual(hashing.call_count,3)
        self.assertLessEqual(len(_DIGEST_CACHE),_DIGEST_LIMIT)
    def test_parent_freshness_image_roles_and_missing_exact(self):
        parent=self.write('A/M',{'scalars':{'S':1}}); self.write('A/C',{'Properties':{'Parent':{'ObjectPath':'/Game/A/M.0'},'scalars':{'child':0},'textures':{'T':'/Game/A/T'}}})
        (self.path/'A/T.png').write_bytes(b'image-first')
        repo=AssetRepository({'/Game':self.path},build='2.2',mapping='native')
        first=repo.load_material('/Game/A/C'); key=repo.material_key(first); self.assertEqual(first.scalars['S'],1)
        parent.write_text(json.dumps({'scalars':{'S':2}}))
        self.assertEqual(repo.load_material('/Game/A/C').scalars['S'],2)
        color=repo.image_key('/Game/A/T','color'); data=repo.image_key('/Game/A/T','data'); self.assertNotEqual(color,data)
        (self.path/'A/T.png').write_bytes(b'image-second')
        self.assertNotEqual(key,repo.material_key(first)); self.assertNotEqual(data,repo.image_key('/Game/A/T','data'))
        before_metadata=repo.material_key(first)
        self.write('A/T',{'Exports':[{'Type':'Texture2D','Properties':{'SRGB':False}}]})
        self.assertNotEqual(repo.material_key(first),before_metadata); self.assertFalse(repo.texture_info('/Game/A/T')['SRGB'])
        with self.assertRaises(FileNotFoundError): repo.resolve('/Game/B/T','.png',image=True)
        generated=repo.generate_defaults(['/Game/A/C'],self.path/'generated.json'); self.assertEqual(generated['records'][0]['scalars']['child'],0)
    def test_authoritative_source_identity_and_thin_placeholder_precedence(self):
        raw={'Exports':[{'Type':'MaterialInstanceConstant','Name':'C','Package':'PioneerGame/Content/A/C','Properties':{'scalars':{'Roughness':.7},'BlendMode':'BLEND_Masked'}}]}
        authoritative=self.write('A/C.arc-material',raw)
        thin=self.write('A/C',{'Data':[],'IsNull':True,'Properties':{'BlendMode':0},'scalars':{'Roughness':0}})
        repo=AssetRepository({'/Game':self.path})
        record=repo.load_material('RemappedPlugins/Content/A/C')
        self.assertEqual(record.scalars['Roughness'],.7); self.assertEqual(record.settings['blend'],'BLEND_Masked')
        self.assertEqual(len(record.provenance),2); self.assertEqual(sum(p['selected'] for p in record.provenance),1)
        key=repo.material_key(record); thin.write_text(json.dumps({'Data':[],'IsNull':False,'Blend':0}))
        self.assertNotEqual(repo.material_key(repo.load_material('/Game/A/C')),key)
        raw['Exports'][0]['Name']='Wrong'; authoritative.write_text(json.dumps(raw))
        with self.assertRaises(ValueError): repo.load_material('/Game/A/C')
        raw['Exports'][0]['Name']='C'; raw['Exports'][0]['Package']='/Game/B/C'; authoritative.write_text(json.dumps(raw))
        with self.assertRaises(ValueError): repo.load_material('/Game/A/C')
        raw['Exports'][0]['Package']='/Game/A/C'; raw['Exports'].append(dict(raw['Exports'][0])); authoritative.write_text(json.dumps(raw))
        with self.assertRaises(ValueError): repo.load_material('/Game/A/C')
    def test_explicit_object_reference_and_cross_mount_identity(self):
        self.write('A/Container.arc-material',{'Exports':[{'Type':'Material','Name':'OtherObject','Package':'/Game/A/Container','Properties':{}}]})
        repo=AssetRepository({'/Game':self.path,'/EmbarkScript':self.path/'B'},mount_aliases={'RemappedPlugins/Known/Content':'/Game'})
        self.assertEqual(repo.load_material('/Game/A/Container.OtherObject').provenance[0]['object_name'],'OtherObject')
        with self.assertRaises(ValueError): repo.load_material('/Game/A/Container')
        with self.assertRaises(FileNotFoundError): repo.load_material('/EmbarkScript/A/Container.OtherObject')
        self.write('A/Alias',{'Package':'RemappedPlugins/Known/Content/A/Alias','Name':'Alias','scalars':{'Zero':0},'textures':{'T':'RemappedPlugins/Known/Content/A/T'}})
        aliased=repo.load_material('RemappedPlugins/Known/Content/A/Alias')
        self.assertEqual(aliased.scalars['Zero'],0); self.assertEqual(aliased.textures['T'],'/Game/A/T')
    def test_unknown_scoped_remap_fails_global_sentinel_is_preserved(self):
        global_child=parse_material({'Type':'MaterialInstanceConstant','Properties':{'scalars':{'X':0}},'CachedData':{'ParentLayerIndexRemap':[-1]}},'/Game/C')
        merged=merge_records(MaterialRecord('/Game/P',scalars={'X':1}),global_child)
        self.assertEqual(merged.scalars['X'],0)
        parent=MaterialRecord('/Game/P',scopes={('LayerParameter',0):{'scalars':{'X':1}}})
        with self.assertRaises(NotImplementedError): merge_records(parent,global_child)
    def test_cycles_and_arrays(self):
        self.write('A/M',{'Properties':{'Parent':'/Game/A/M2'}}); self.write('A/M2',{'Properties':{'Parent':'/Game/A/M'}})
        repo=AssetRepository({'/Game':self.path},array_slices={'/Game/Array':['/Game/A/T','/Game/B/T']})
        with self.assertRaises(ValueError): repo.load_material('/Game/A/M')
        self.assertEqual(repo.array_reference('/Game/Array',1),'/Game/B/T')
        with self.assertRaises(IndexError): repo.array_reference('/Game/Array',2)

class SlotTests(unittest.TestCase):
    def test_repeated_sparse_unused_explicit(self):
        sections=[Section(0,1,0),Section(2,1,2),Section(3,0,3,1)]
        result=plan_bindings(['unused','same','unused2'],sections,explicit={2:'override'})
        self.assertEqual(result.assignments,((0,'same'),(2,'override')))
        self.assertEqual(len(result.diagnostics),2)
        with self.assertRaises(IndexError): plan_bindings(['a'],[Section(0,2,0)])
    def test_compile_once_and_rollback(self):
        class Slot:
            def __init__(self,value): self._value=value; self.fail=False
            @property
            def material(self): return self._value
            @material.setter
            def material(self,value):
                if self.fail and value=='new': raise RuntimeError('test failure')
                self._value=value
        class Obj: pass
        obj=Obj(); obj.material_slots=[Slot('old0'),Slot('keep'),Slot('old2')]; calls=[]
        def factory(ref): calls.append(ref); return 'new'
        bindings=plan_bindings(['same'],[Section(0,0,0),Section(1,0,2)])
        obj.material_slots[2].fail=True
        with self.assertRaises(RuntimeError): assign_transaction(obj,bindings,factory)
        self.assertEqual([s.material for s in obj.material_slots],['old0','keep','old2']); self.assertEqual(calls,['same'])
        obj.material_slots[2].fail=False; assign_transaction(obj,bindings,factory)
        self.assertEqual([s.material for s in obj.material_slots],['new','keep','new'])
    def test_multi_owner_shared_mesh_safety_and_single_slot_override(self):
        from _native_runtime_regression_addon.Shared.materials.native.slots import assign_many_transaction,assign_slot_transaction
        class Data:
            def __init__(self): self.materials=['data0','data1','data2']
        class Slot:
            def __init__(self,data,index,hidden): self.data=data; self.index=index; self.link='DATA'; self.hidden=hidden; self.fail=False
            @property
            def material(self): return self.data.materials[self.index] if self.link=='DATA' else self.hidden
            @material.setter
            def material(self,value):
                if self.fail and value=='new': raise RuntimeError('owner commit failed')
                if self.link=='DATA': self.data.materials[self.index]=value
                else: self.hidden=value
        class Obj:
            def __init__(self,data,name): self.data=data; self.material_slots=[Slot(data,i,name+str(i)) for i in range(3)]
        data=Data(); first=Obj(data,'hiddenA'); second=Obj(data,'hiddenB'); unrelated=Obj(data,'hiddenC')
        calls=[]
        def factory(identity): calls.append(identity); return 'new'
        bindings=plan_bindings(['same'],[Section(0,0,0),Section(1,0,2)])
        second.material_slots[2].fail=True
        with self.assertRaises(RuntimeError): assign_many_transaction(((first,bindings),(second,bindings)),factory)
        self.assertEqual(calls,['same'])
        self.assertEqual(data.materials,['data0','data1','data2'])
        for obj,name in ((first,'hiddenA'),(second,'hiddenB'),(unrelated,'hiddenC')):
            self.assertEqual([slot.link for slot in obj.material_slots],['DATA']*3)
            self.assertEqual([slot.hidden for slot in obj.material_slots],[name+str(i) for i in range(3)])
        second.material_slots[2].fail=False; calls.clear()
        assign_many_transaction(((first,bindings),(second,bindings)),factory)
        self.assertEqual(calls,['same']); self.assertEqual(data.materials,['data0','data1','data2'])
        self.assertEqual([slot.material for slot in unrelated.material_slots],['data0','data1','data2'])
        self.assertEqual([slot.material for slot in first.material_slots],['new','data1','new'])
        assign_slot_transaction(first,1,'explicit',lambda _: 'only-slot1')
        self.assertEqual([slot.material for slot in first.material_slots],['new','only-slot1','new'])
        self.assertEqual([slot.material for slot in second.material_slots],['new','data1','new'])
        with self.assertRaises(ValueError): assign_many_transaction(((first,((0,'a'),)),(first,((0,'b'),))),factory)
    def test_worker_guard_without_bpy(self):
        import threading
        errors=[]
        def worker():
            try: compile_plan(MaterialPlan('/Game/M','test',{}),None)
            except Exception as e: errors.append(e)
        t=threading.Thread(target=worker); t.start(); t.join()
        self.assertIsInstance(errors[0],RuntimeError)

class NativeFixtureTests(unittest.TestCase):
    def test_current_native_glass(self):
        game=Path('Z:/Datamining/Arc Raiders/2.2.0/Jsons_2.2.0/PioneerGame/Content')
        if not game.is_dir(): self.skipTest('native game corpus unavailable')
        repo=AssetRepository({'/Game':game},build='2.2.0',mapping='current raw JSON')
        r=repo.load_material('/Game/Pioneer/Vehicles/Hoverbike_02/MI_Vehicle_Hoverbike_02_Glass')
        self.assertEqual(r.scalars['RefractionDepthBias'],0); self.assertAlmostEqual(r.scalars['Roughness'],.15)
        self.assertEqual(r.textures['Mask'],'/Game/Pioneer/Vehicles/Hoverbike_02/Textures/T_Hoverbike_02_Glass_mask_01')
        self.assertEqual(len(r.provenance),2); self.assertTrue(all(len(p['sha256'])==64 for p in r.provenance))

if __name__=='__main__': unittest.main()













