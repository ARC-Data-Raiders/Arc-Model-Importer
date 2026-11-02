import importlib,json,os,sys,tempfile,types,unittest
from pathlib import Path
from .native_test_support import ROOT as REPO,PACKAGE as PKG,namespace,temp_fixture
namespace()
catalog=importlib.import_module(PKG+'.material_runtime.catalog');assets=importlib.import_module(PKG+'.material_runtime.assets')
class ResourceTests(unittest.TestCase):
 def setUp(self):
  self.temp=temp_fixture('native-nonimage-');self.root=Path(self.temp.name)/"native";self.content=self.root/'PioneerGame/Content';self.content.mkdir(parents=True)
 def tearDown(self):self.temp.cleanup()
 def write(self,name,kind,props):
  p=self.content/(name+'.json');p.write_text(json.dumps({'Type':kind,'Name':name,'Package':'/Game/'+name,'Properties':props}),encoding='utf8');return p
 def repo(self):return assets.NativeAssetRepository({'/Game':self.content},build='fixture',mapping='fixture')
 def test_runtime_virtual_texture_retained_without_fake_image(self):
  self.write('M','Material',{'textures':{'Virtual':'/Game/VT'}});self.write('VT','RuntimeVirtualTexture',{'bClearTextures':False,'TileCount':13})
  bank=catalog.generate_catalog(self.root,self.root.parent/'bank.json',['/Game/M'],build='fixture',mapping='fixture');self.assertFalse(bank.payload['errors']);fact=bank.texture_fact('/Game/VT',self.repo());self.assertEqual(fact['resource_kind'],'runtime-virtual-texture');self.assertNotIn('native_texture_kind',fact);self.assertIs(fact['properties']['bClearTextures'],False)
  metadata=bank.texture_metadata('/Game/VT',self.repo());self.assertNotIn('NativeTextureKind',metadata);self.assertEqual(metadata['NativeResourceKind'],'runtime-virtual-texture')
 def test_unused_nonimage_resource_does_not_block_material_signature(self):
  self.write('M','Material',{'textures':{'Virtual':'/Game/VT'}});self.write('VT','RuntimeVirtualTexture',{})
  result=catalog.material_build_signature('/Game/M',self.repo(),texture_bindings=[],recipe='fixture',implementation_revision='test');self.assertTrue(result.complete)
 def test_active_nonimage_diagnoses_and_strict_refuses(self):
  self.write('M','Material',{});self.write('VT','RuntimeVirtualTexture',{})
  bindings=[{'reference':'/Game/VT','role':'data'}];r=catalog.material_build_signature('/Game/M',self.repo(),texture_bindings=bindings,recipe='fixture',implementation_revision='test');self.assertFalse(r.complete);self.assertTrue(any('Unsupported active native non-image' in d for d in r.diagnostics));self.assertFalse(any(d.get('kind')=='image' for d in r.dependencies))
  with self.assertRaises(FileNotFoundError):catalog.material_build_signature('/Game/M',self.repo(),texture_bindings=bindings,recipe='fixture',implementation_revision='test',strict_images=True)
 def test_font_is_not_a_texture(self):
  self.write('M','Material',{'textures':{'Glyphs':'/Game/F'}});self.write('F','Font',{'FontCacheType':'EFontCacheType::Runtime'});bank=catalog.generate_catalog(self.root,self.root.parent/'bank.json',['/Game/M'],build='fixture',mapping='fixture');self.assertEqual(bank.payload['textures']['/Game/F']['resource_kind'],'font')
 def test_unknown_resource_still_refuses(self):
  self.write('Unknown','NotAProvenResource',{})
  with self.assertRaises(catalog.NativeMetadataError):catalog._texture_fact(self.repo(),'/Game/Unknown')
 def test_nonimage_identity_still_exact(self):
  p=self.write('VT','RuntimeVirtualTexture',{});raw=json.loads(p.read_text(encoding='utf8'));raw['Name']='Other';p.write_text(json.dumps(raw),encoding='utf8')
  with self.assertRaises(catalog.NativeMetadataError):catalog._texture_fact(self.repo(),'/Game/VT')
if __name__=='__main__':unittest.main()
