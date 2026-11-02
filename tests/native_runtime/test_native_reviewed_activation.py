import ast,copy,hashlib,importlib,json,os,sys,tempfile,types,unittest
from pathlib import Path
from .native_test_support import ROOT as REPO,PACKAGE as PKG,namespace,temp_fixture
namespace()
if PKG+'.surface_rebuild' not in sys.modules:
 m=types.ModuleType(PKG+'.surface_rebuild');m.__path__=[str(REPO/'surface_rebuild')];sys.modules[m.__name__]=m
bridge=importlib.import_module(PKG+'.surface_rebuild.native_catalog_bridge');catalog=importlib.import_module(PKG+'.material_runtime.catalog');records=importlib.import_module(PKG+'.surface_rebuild.records')
class ActivationTests(unittest.TestCase):
 def setUp(self):
  self.temp=temp_fixture('native-reviewed-activation-');self.root=Path(self.temp.name);native=self.root/'native';content=native/'PioneerGame/Content';content.mkdir(parents=True);cache=self.root/'empty-cache';(cache/'PioneerGame/Content').mkdir(parents=True)
  uniform={'UniformNumericParameters':[{'ParameterInfo':{'Name':'Param'},'ParameterType':'Scalar','Value':i} for i in (1,2)]}
  for name,props in (('Bad',{}),('Child',{'Parent':'/Game/Bad','scalars':{'Param':0}}),('Good',{'scalars':{'Value':2}})):
   material={'Type':'MaterialInstanceConstant' if name=='Child' else 'Material','Name':name,'Package':'/Game/'+name,'Properties':props}
   if name=='Bad':material['LoadedMaterialResources']=[{'LoadedShaderMap':{'ResourceHash':'resource','ShaderMapId':{'FeatureLevel':'SM5'},'Content':{'MaterialCompilationOutput':{'UniformExpressionSet':uniform}}}}]
   (content/(name+'.json')).write_text(json.dumps(material),encoding='utf8')
  bank=catalog.generate_catalog(native,self.root/'bank-base.json',['/Game/Bad','/Game/Child','/Game/Good'],build='fixture',mapping='fixture',uniform_policy='defer-bindings');self.payload=catalog.serialize(bank.payload);self.payload['errors']=[{'reference':'/Game/'+name,'error_type':'DeferredRenderingAmbiguity','reason':'Occurrence defaults unresolved'} for name in ('Bad','Child')];self.payload['statistics'].update(normalization_error_count=0,error_count=2);self.bank=self.root/'bank.json';self.manifest=self.root/'active.json';self.index=types.SimpleNamespace(root=cache,native_catalog_activation=self.manifest,paths={},resolve=lambda *a:None)
  limits,sources,affected=bridge.reviewed_limit_facts(self.payload);self.config={'schema':bridge.LIMITED_SCHEMA,'reviewed_source_complete':True,'catalog':'bank.json','build':'fixture','mapping':'fixture','expected_material_records':3,'expected_texture_metadata':0,'expected_render_limitations':2,'expected_binding_sources':1,'reviewed_limitations':limits,'reviewed_binding_sources':sources};self.save();self.metadata=records.metadata;records.metadata=lambda:(_ for _ in ()).throw(AssertionError('legacy data read'))
 def tearDown(self):records.metadata=self.metadata;self.temp.cleanup()
 def save(self):
  catalog.NativeCatalog(self.payload).write(self.bank);self.config['sha256']=hashlib.sha256(self.bank.read_bytes()).hexdigest();self.manifest.write_text(json.dumps(self.config),encoding='utf8')
 def test_unaffected_material_uses_native_bank_without_legacy(self):
  record=records.MaterialLibrary(self.index).load('/Game/Good');self.assertEqual(record['scalars']['Value'],2)
 def test_affected_and_inherited_override_refused(self):
  owner=bridge.session(self.index)
  for ref in ('/Game/Bad','/Game/Child'):
   with self.assertRaises(bridge.NativeRenderLimitationError):owner.legacy_record(ref)
 def test_affected_build_refuses_before_graph_mutation(self):
  mutations=[];tree=ast.parse((REPO/'surface_rebuild/materials.py').read_text(encoding='utf8'));function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='build_material');module=types.ModuleType('gate_test');module.__package__=PKG+'.surface_rebuild';module.__dict__.update(hashlib=hashlib,json=json,native_begin=bridge.begin,bpy=types.SimpleNamespace(data=types.SimpleNamespace(materials=types.SimpleNamespace(new=lambda *a:mutations.append(a)))))
  exec(compile(ast.Module(body=[function],type_ignores=[]),'<actual build_material entry>','exec'),module.__dict__);obj=types.SimpleNamespace(data=types.SimpleNamespace(uv_layers=[],color_attributes=[]));record={'package':'/Game/Child','master':'/Game/Bad','textures':{},'scalars':{},'colors':{},'switches':{},'settings':{},'family':'surface'}
  with self.assertRaises(bridge.NativeRenderLimitationError):module.build_material(obj,record,self.index)
  self.assertEqual(mutations,[])
 def test_unknown_error_rejects_even_with_rehashed_bank(self):
  self.payload['errors'][0]['error_type']='NativeMetadataError';self.save()
  with self.assertRaisesRegex(ValueError,'unknown error'):bridge.session(self.index)
 def test_missing_reviewed_limit_rejects(self):
  self.config['reviewed_limitations'].pop();self.save()
  with self.assertRaisesRegex(ValueError,'Missing, changed'):bridge.session(self.index)
 def test_changed_binding_same_counts_rejects(self):
  self.payload['records']['/Game/Bad']['uniform_projection']['deferred_bindings'][0]['compiled_values'][0]=999;self.save()
  with self.assertRaisesRegex(ValueError,'Missing, changed'):bridge.session(self.index)
 def test_missing_error_identity_does_not_allow_partial_closure(self):
  self.payload['errors'].pop();self.payload['statistics']['error_count']=1;self.save()
  with self.assertRaisesRegex(ValueError,'inheritance closure'):bridge.session(self.index)
 def test_normalization_errors_cannot_be_allowlisted(self):
  self.payload['statistics']['normalization_error_count']=1;self.save()
  with self.assertRaisesRegex(ValueError,'normalization errors'):bridge.session(self.index)
 def test_unreviewed_candidate_never_activates(self):
  self.config['reviewed_source_complete']=False;self.save()
  with self.assertRaisesRegex(ValueError,'explicit source review'):bridge.session(self.index)
 def test_new_local_child_of_known_affected_parent_refused_even_without_uniforms(self):
  content=self.index.root/'PioneerGame/Content'
  for name,props in (('Bad',{}),('New',{'Parent':'/Game/Bad'})):
   (content/(name+'.arc-material.json')).write_text(json.dumps({'Type':'Material' if name=='Bad' else 'MaterialInstanceConstant','Name':name,'Package':'/Game/'+name,'Properties':props}),encoding='utf8')
  with self.assertRaises(bridge.NativeRenderLimitationError):bridge.session(self.index).load('/Game/New')
if __name__=='__main__':unittest.main()
