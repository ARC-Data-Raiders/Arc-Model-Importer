import base64,pathlib,struct,sys,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from Shared.materials.native import MaterialRecord
from Shared.materials.native.defaults import project_uniform_defaults, UniformDefaultsError

def resource(feature,uniform):
    return {'LoadedShaderMap':{'ShaderMapId':{'FeatureLevel':feature,'QualityLevel':'High','CookedShaderMapIdHash':feature+'-hash'},'Content':{'MaterialCompilationOutput':{'UniformExpressionSet':uniform}}}}
class UniformDefaultsTests(unittest.TestCase):
    def test_preserves_native_authored_compiled_conflict_and_explicit_policy(self):
        uniform={'UniformScalarParameters':[{'ParameterInfo':{'Name':'Fresnel'},'DefaultValue':15}]}
        material={'Type':'Material','Properties':{},'LoadedMaterialResources':[resource('SM5',uniform),resource('SM6',uniform)]}
        record=MaterialRecord('/Game/M','/Game/M',scalars={'Fresnel':5})
        preserved,report=project_uniform_defaults(material,record)
        self.assertEqual(preserved.scalars['Fresnel'],5); self.assertEqual(report['conflicts'][0]['compiled_values'],[15.]); self.assertEqual(report['conflicts'][0]['authored_value'],5)
        projected,report=project_uniform_defaults(material,record,policy='compiled-consensus')
        self.assertEqual(projected.scalars['Fresnel'],15); self.assertEqual(report['conflicts'][0]['status'],'declared-compiled-policy-not-runtime-proof')
        material['Properties']['ScalarParameterValues']=[{'ParameterInfo':{'Name':'Fresnel'},'ParameterValue':0}]
        explicit,report=project_uniform_defaults(material,MaterialRecord('/Game/M','/Game/M',scalars={'Fresnel':0}),policy='compiled-consensus')
        self.assertEqual(explicit.scalars['Fresnel'],0); self.assertEqual(report['conflicts'][0]['status'],'explicit-properties-preserved')
    def test_different_variants_require_selector_for_missing_default(self):
        low={'UniformScalarParameters':[{'ParameterName':'Value','DefaultValue':0}]}; high={'UniformScalarParameters':[{'ParameterName':'Value','DefaultValue':1}]}
        material={'Type':'Material','LoadedMaterialResources':[resource('SM5',low),resource('SM6',high)]}
        with self.assertRaises(UniformDefaultsError): project_uniform_defaults(material,MaterialRecord('/Game/M'))
        record,report=project_uniform_defaults(material,MaterialRecord('/Game/M'),selector={'FeatureLevel':'SM5'})
        self.assertEqual(record.scalars['Value'],0); self.assertEqual(len(report['resources']),1)
        with self.assertRaises(UniformDefaultsError): project_uniform_defaults(material,MaterialRecord('/Game/M'),selector={'FeatureLevel':'Unknown'})
    def test_modern_numeric_offsets_switches_scope_and_texture_indices(self):
        uniform={'DefaultValues':base64.b64encode(struct.pack('<f4f',0.,-.5,2.,0.,0.)).decode(),'UniformNumericParameters':[{'ParameterInfo':{'Name':'Zero'},'ParameterType':'Scalar','DefaultValueOffset':0},{'ParameterInfo':{'Name':'Vector','Association':'LayerParameter','Index':2},'ParameterType':'Vector','DefaultValueOffset':4},{'ParameterInfo':{'Name':'Off'},'ParameterType':'StaticSwitch','Value':False}], 'UniformTextureParameters':[[{'ParameterInfo':{'Name':'Map'},'TextureIndex':0},{'ParameterInfo':{'Name':'None'},'TextureIndex':0}]]}
        material={'Type':'Material','ReferencedTextures':[{'ObjectPath':'/Game/T.0'}],'LoadedMaterialResources':[resource('SM5',uniform)]}
        record,report=project_uniform_defaults(material,MaterialRecord('/Game/M'))
        self.assertEqual(record.scalars['Zero'],0); self.assertFalse(record.switches['Off']); self.assertEqual(record.textures['Map'],'/Game/T')
        self.assertEqual(record.scopes[('LayerParameter',2)]['colors']['Vector'],(-.5,2,0,0)); self.assertEqual(len(report['recovered']),4); self.assertEqual(report['unnamed_texture_constants'],1)
    def test_runtime_cached_texture_table_not_editor_aggregate(self):
        uniform={'UniformTextureParameters':[[{'ParameterInfo':{'Name':'Normal'},'TextureIndex':1}]]}
        material={'Type':'Material','ReferencedTextures':[{'ObjectPath':'/Game/Wrong0'},{'ObjectPath':'/Game/Wrong1'}],'CachedExpressionData':{'ReferencedTextures':[None,{'ObjectPath':'/Game/RightNormal'}]},'LoadedMaterialResources':[resource('SM5',uniform)]}
        record,report=project_uniform_defaults(material,MaterialRecord('/Game/M'))
        self.assertEqual(record.textures['Normal'],'/Game/RightNormal'); self.assertEqual(report['texture_index_table'],'CachedExpressionData.ReferencedTextures')
    def test_material_instance_uniforms_never_override_properties(self):
        material={'Type':'MaterialInstanceConstant','LoadedMaterialResources':[resource('SM5',{'UniformScalarParameters':[{'ParameterName':'Value','DefaultValue':9}]})]}
        record,report=project_uniform_defaults(material,MaterialRecord('/Game/C',scalars={'Value':0}),selector={'FeatureLevel':'not-relevant'})
        self.assertEqual(record.scalars['Value'],0); self.assertTrue(report['instance_uniform_defaults_not_promoted'])
    def test_missing_default_data_is_reported_not_guessed(self):
        material={'Type':'Material','ReferencedTextures':[],'LoadedMaterialResources':[resource('SM5',{'UniformNumericParameters':[{'ParameterInfo':{'Name':'Unknown'},'ParameterType':'UnknownType'}],'UniformTextureParameters':[[{'ParameterInfo':{'Name':'Bad'},'TextureIndex':99}]]})]}
        record,report=project_uniform_defaults(material,MaterialRecord('/Game/M'))
        self.assertEqual(len(report['missing']),2); self.assertFalse(record.scalars); self.assertFalse(record.textures)
if __name__=='__main__': unittest.main()


