"""Portable native teeth response tests; no Blender or external game files."""
import ast,importlib,json,math,os,sys,tempfile,types,unittest
from pathlib import Path
ROOT=Path(os.environ['DATARAIDERS_TEST_REPOSITORY']).resolve() if os.environ.get('DATARAIDERS_TEST_REPOSITORY') else next(parent for parent in Path(__file__).resolve().parents if (parent/'Shared/materials/native/core.py').is_file());sys.path.insert(0,str(ROOT))
from Shared.materials import native as native
STAGE=Path(os.environ['DATARAIDERS_TEETH_STAGE']) if os.environ.get('DATARAIDERS_TEETH_STAGE') else ROOT
if STAGE!=ROOT:materials.native.__path__.insert(0,str(STAGE/'Shared/materials/native'))
from Shared.materials.native.core import MaterialRecord, evaluate
from Shared.materials.native.teeth import compile_teeth, COLOR, NORMAL, SNOW, MASTER

def record(**values):return MaterialRecord('/Game/Test/MI_Teeth',MASTER,**values)
def context(case):return {'pre_skin_normal_z':case['normal_z'],'primitive_data':case['cpd'],'screen_radius':1.,'blue_noise':1.}
def run(plan,inputs):
    def texture(ref,uv,attrs):
        if ref==COLOR:return tuple(inputs['color'])+(inputs.get('color_alpha',1.),)
        if ref==NORMAL:return tuple(inputs['normal'])+(inputs.get('normal_b',0.),inputs.get('normal_alpha',1.))
        if ref==SNOW:return tuple(inputs['snow'])+(inputs.get('snow_alpha',1.),)
        raise AssertionError(ref)
    return {name:evaluate(node,{'uv':(.25,.75,0.),'texture':texture}) for name,node in plan.outputs.items()}
class NativeTeeth(unittest.TestCase):
    def test_64_independent_instruction_checkpoints(self):
        data=json.loads((Path(__file__).parent/'fixtures/teeth_native_checkpoints.json').read_text(encoding='utf-8'))
        self.assertEqual(len(data['cases']),64)
        for index,case in enumerate(data['cases']):
            with self.subTest(index=index):
                actual=run(compile_teeth(record(),context(case['inputs'])),case['inputs'])
                for key in ('base_color','normal','emission'):
                    for a,b in zip(actual[key],case['outputs'][key]):self.assertAlmostEqual(a,b,places=11)
                self.assertAlmostEqual(actual['roughness'],case['outputs']['roughness'],places=11)
    def test_material_surface_controls_and_unused_texture_channels(self):
        values={'normal':(.2,.8),'snow':(.6,.4,.7),'color':(.2,.4,.6),'normal_z':.2,'cpd':(0.,0.,0.)};plan=compile_teeth(record(),context(values));first=run(plan,values);second=run(plan,{**values,'color_alpha':0.,'normal_alpha':0.,'normal_b':.731,'snow_alpha':0.})
        self.assertEqual(first,second);self.assertEqual(first['coat'],1.);self.assertEqual(first['coat_roughness'],.1);self.assertEqual(first['metallic'],0.);self.assertEqual(first['specular'],.5)
    def test_raw_preskin_source_required_no_world_normal_substitution(self):
        rejected=compile_teeth(record(),{'primitive_data':(0.,0.,0.)});self.assertFalse(rejected.outputs);self.assertIn('raw pre-skin',rejected.diagnostics[0])
    def test_fractional_visibility_requires_explicit_temporal_inputs(self):
        self.assertFalse(compile_teeth(record(scalars={'Visibility':.5}),{'pre_skin_normal_z':0.}).outputs)
        self.assertEqual(evaluate(compile_teeth(record(scalars={'Visibility':0.}),{'pre_skin_normal_z':0.}).outputs['alpha']),0.)
        self.assertEqual(evaluate(compile_teeth(record(),{'pre_skin_normal_z':0.}).outputs['alpha']),1.)
    def test_exact_native_discard_boundary(self):
        c={'pre_skin_normal_z':0.,'primitive_data':(0.,0.,0.),'screen_radius':1.,'blue_noise':0.}
        self.assertEqual(evaluate(compile_teeth(record(scalars={'Visibility':.8333}),c).outputs['alpha']),1.)
        self.assertEqual(evaluate(compile_teeth(record(scalars={'Visibility':.8333-1e-8}),c).outputs['alpha']),0.)
    def test_native_default_cpd_is_explicit_preview_state(self):
        plan=compile_teeth(record(),{'pre_skin_normal_z':0.});self.assertTrue(any('cached preview CPD' in x for x in plan.diagnostics))
    def test_no_guessed_wet_tint_or_cloth_response(self):
        i={'normal':(.5,.5),'snow':(.4,.5,.6),'color':(.1,.2,.3),'normal_z':.1,'cpd':(0.,0.,1.)};a=run(compile_teeth(record(),context(i)),i);b=run(compile_teeth(record(scalars={'WetTint':.123,'WetGlossCloth':.923}),context(i)),i);self.assertEqual(a,b)
    def test_only_exact_native_master_supported(self):self.assertFalse(compile_teeth(MaterialRecord('/Game/Test/Other','/Game/Other'),{'pre_skin_normal_z':0.}).outputs)
    def test_exact_adapter_requires_preserved_point_normal_source(self):
        from Shared.materials.native import native_families as nf
        from Shared.materials.native.teeth import COLOR, NORMAL, SNOW
        with tempfile.TemporaryDirectory(dir=os.environ.get('DATARAIDERS_TEST_TEMP')) as folder:
            root=Path(folder)/'PioneerGame/Content';source=root/(MASTER.removeprefix('/Game/')+'.arc-material.json');source.parent.mkdir(parents=True)
            source.write_text(json.dumps({'Exports':[{'Type':'Material','Name':'M_Teeth','Package':MASTER,'Properties':{}}],'DataRaidersSource':{'schema':'dataraiders.native-material-hierarchy','version':1,'package':'PioneerGame/Content/'+MASTER.removeprefix('/Game/'),'object_path':MASTER+'.M_Teeth'}}),encoding='utf-8')
            for ref in (COLOR,NORMAL,SNOW):
                image=root/(ref.removeprefix('/Game/')+'.png');image.parent.mkdir(parents=True,exist_ok=True);image.write_bytes(b'non-decoded fixture image')
            class Mesh(dict):pass
            mesh=Mesh();mesh.attributes={};mesh.uv_layers=[types.SimpleNamespace(name='UV0')];obj=types.SimpleNamespace(data=mesh,type='MESH',scale=(1.,1.,1.));mounts={'/Game':str(root)}
            self.assertFalse(nf.prepare(source,obj=obj,mounts=mounts).accepted)
            mesh['DR.NativeNormalSourceVersion']=1;mesh.attributes['DR.NativeBaseVertexNormal']=types.SimpleNamespace(domain='POINT',data_type='FLOAT_VECTOR')
            prepared=nf.prepare(source,obj=obj,mounts=mounts);self.assertTrue(prepared.accepted,prepared.reason);self.assertEqual(prepared.family,'teeth');self.assertEqual(len(prepared.report['images']),3)
            mesh.attributes['DR.NativeBaseVertexNormal'].domain='CORNER';self.assertFalse(nf.prepare(source,obj=obj,mounts=mounts).accepted)
    def test_public_teeth_override_precedes_candidate_allocation(self):
        tree=ast.parse((STAGE/'Shared/materials/character.py').read_text(encoding='utf-8'));function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_setup_head_slot_teeth')
        namespace={'__package__':'_native_teeth_entry_probe.Shared.materials'}
        package=types.ModuleType('_native_teeth_entry_probe');package.__path__=[];sys.modules[package.__name__]=package
        runtime=types.ModuleType('_native_teeth_entry_probe.Shared.materials.native');runtime.__path__=[];sys.modules[runtime.__name__]=runtime
        bridge=types.ModuleType(runtime.__name__+'.native_families');bridge.is_user_override=lambda mat:True
        def forbidden(*a,**k):raise AssertionError('Override must precede material allocation/setup')
        bridge.setup_entrypoint=forbidden;sys.modules[bridge.__name__]=bridge
        exec(compile(ast.Module(body=[function],type_ignores=[]),'<actual staged teeth entry>','exec'),namespace)
        namespace['_setup_head_slot_teeth'](types.SimpleNamespace(material={'explicit':'override'}),'unused','unused')
        for key in (bridge.__name__,runtime.__name__,package.__name__):sys.modules.pop(key,None)
    def test_coat_roughness_maps_to_actual_principled_socket(self):
        tree=ast.parse((STAGE/'Weapons/material_runtime/compiler.py').read_text(encoding='utf-8'));mapping=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_OUTPUTS' for t in n.targets));self.assertEqual(mapping['coat_roughness'],'Coat Roughness');self.assertEqual(mapping['coat'],'Coat Weight')
if __name__=='__main__':unittest.main(verbosity=2)
