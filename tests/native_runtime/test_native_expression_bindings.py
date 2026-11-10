import importlib,json,os,sys,tempfile,types,unittest
from pathlib import Path
from .native_test_support import ROOT as REPO,PACKAGE as PKG,namespace,temp_fixture
namespace()
defaults=importlib.import_module(PKG+'.Shared.materials.native.defaults');core=importlib.import_module(PKG+'.Shared.materials.native.core');catalog=importlib.import_module(PKG+'.Shared.materials.native.catalog');assets=importlib.import_module(PKG+'.Shared.materials.native.assets')
def material(values=(1,2),kind='Scalar',textures=False):
 parameters=[{'ParameterInfo':{'Name':'Param'},'ParameterType':kind,'DefaultValueOffset':i*4,'Value':v} for i,v in enumerate(values)]
 uniform={'UniformNumericParameters':parameters}
 if textures:uniform={'UniformTextureParameters':[[{'ParameterInfo':{'Name':'NOH'},'TextureIndex':0},{'ParameterInfo':{'Name':'NOH'},'TextureIndex':1}]]}
 return {'Type':'Material','Name':'M','Package':'/Game/M','Properties':{},'CachedExpressionData':{'ReferencedTextures':['/Game/A','/Game/B']},'LoadedMaterialResources':[{'LoadedShaderMap':{'ResourceHash':'one-resource','ShaderMapId':{'QualityLevel':'Num','FeatureLevel':'SM6'},'Content':{'MaterialCompilationOutput':{'UniformExpressionSet':uniform}}}}]}
class BindingTests(unittest.TestCase):
 def test_same_resource_defaults_are_distinct_occurrences(self):
  r,report=defaults.project_uniform_defaults(material(),core.MaterialRecord('/Game/M','/Game/M'),policy='defer-bindings');self.assertNotIn('Param',r.scalars);item=report['deferred_bindings'][0];self.assertEqual(item['ambiguity'],'within-resource-expression-defaults');self.assertEqual([tuple(x['occurrence']) for x in item['bindings']],[(0,),(1,)]);self.assertEqual([x['value'] for x in item['bindings']],[1.,2.]);self.assertTrue(all(x['resource']['resource_hash']=='one-resource' for x in item['bindings']))
 def test_resource_selector_cannot_resolve_duplicate_defaults(self):
  with self.assertRaisesRegex(defaults.UniformDefaultsError,'binding-level resolution'):defaults.project_uniform_defaults(material(),core.MaterialRecord('/Game/M','/Game/M'),selector={'FeatureLevel':'SM6','QualityLevel':'Num'})
 def test_ambiguous_cached_named_value_is_not_selected(self):
  r,report=defaults.project_uniform_defaults(material(),core.MaterialRecord('/Game/M','/Game/M',scalars={'Param':9}),policy='defer-bindings');self.assertNotIn('Param',r.scalars);self.assertEqual(report['deferred_bindings'][0]['authored_value'],9)
 def test_explicit_properties_zero_override_preserved(self):
  m=material();m['Properties']['ScalarParameterValues']=[{'ParameterInfo':{'Name':'Param'},'ParameterValue':0}];r,report=defaults.project_uniform_defaults(m,core.MaterialRecord('/Game/M','/Game/M',scalars={'Param':0}),policy='defer-bindings');self.assertEqual(r.scalars['Param'],0);self.assertFalse(report['deferred_bindings']);self.assertEqual(report['conflicts'][0]['status'],'explicit-properties-preserved')
 def test_same_value_occurrences_may_form_consensus(self):
  r,report=defaults.project_uniform_defaults(material((0,0)),core.MaterialRecord('/Game/M','/Game/M'),policy='defer-bindings');self.assertEqual(r.scalars['Param'],0);self.assertFalse(report['deferred_bindings']);self.assertEqual(len(report['uniform_bindings']),2)
 def test_texture_binding_nested_occurrences_and_index_preserved(self):
  r,report=defaults.project_uniform_defaults(material(textures=True),core.MaterialRecord('/Game/M','/Game/M'),policy='defer-bindings');self.assertNotIn('NOH',r.textures);bindings=report['deferred_bindings'][0]['bindings'];self.assertEqual([tuple(x['occurrence']) for x in bindings],[(0,0),(0,1)]);self.assertEqual([x['native_parameter']['TextureIndex'] for x in bindings],[0,1]);self.assertEqual([x['value'] for x in bindings],['/Game/A','/Game/B'])
 def test_deferred_record_is_not_render_signature_complete(self):
  with temp_fixture('native-binding-defaults-') as temp:
   root=Path(temp);(root/'M.json').write_text(json.dumps(material()),encoding='utf8');repo=assets.NativeAssetRepository({'/Game':root},uniform_policy='defer-bindings',build='fixture',mapping='fixture');signature=catalog.material_build_signature('/Game/M',repo,texture_bindings=[],recipe='fixture',implementation_revision='fixture');self.assertFalse(signature.complete);self.assertTrue(any('Unresolved native uniform defaults' in d for d in signature.diagnostics))
if __name__=='__main__':unittest.main()
