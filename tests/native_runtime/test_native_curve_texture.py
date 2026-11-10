"""Bounded typed curve/HDR consumer regressions; all unit resources are tiny fixtures."""
import pathlib,types,sys,importlib,tempfile,json,unittest
try:
 from .native_test_support import ROOT,PACKAGE,namespace,temp_fixture
except ImportError:
 from native_test_support import ROOT,PACKAGE,namespace,temp_fixture
namespace()
assets=importlib.import_module(PACKAGE+'.Shared.materials.native.assets');catalog=importlib.import_module(PACKAGE+'.Shared.materials.native.catalog');repository=importlib.import_module(PACKAGE+'.Shared.materials.native.repository')
class CurveKindTests(unittest.TestCase):
 def setUp(self):self.tmp=temp_fixture('native-curve-kind-');self.root=pathlib.Path(self.tmp.name);self.game=self.root/'native/PioneerGame/Content';self.game.mkdir(parents=True);self.repo=assets.NativeAssetRepository({'/Game':self.game},build='fixture',mapping='typed-kind')
 def tearDown(self):self.tmp.cleanup()
 def texture(self,name='Atlas',kind='CurveLinearColorAtlas',native_kind=None,extra=None):
  entry={'Type':kind,'Name':name,'Package':'/Game/'+name,'Properties':{'SRGB':False,'Filter':'TF_Nearest'},'PixelFormat':'PF_FloatRGBA','Mips':[{'SizeX':256,'SizeY':31,'SizeZ':1}]}
  if native_kind is not None:entry['NativeTextureKind']=native_kind
  entry.update(extra or {});path=self.game/(name+'.arc-texture.json');path.write_text(json.dumps(entry),encoding='utf8');return path
 def test_concrete_curve_class_preserved_and_provider_projects_native_texture2d_kind(self):
  self.texture();fact=catalog._texture_fact(self.repo,'/Game/Atlas');self.assertEqual(fact['type'],'CurveLinearColorAtlas');self.assertEqual(fact['native_texture_kind'],'Texture2D');self.assertIs(fact['properties']['SRGB'],False);self.assertNotIn('slice_count',fact);info=self.repo.texture_info('/Game/Atlas');self.assertEqual(info['NativeTextureKind'],'Texture2D');self.assertEqual(info['Type'],'CurveLinearColorAtlas')
  bank=catalog.NativeCatalog({'format':catalog.FORMAT,'build':'fixture','mapping':'typed-kind','records':{},'textures':{'/Game/Atlas':fact}});raw=bank.texture_metadata('/Game/Atlas',self.repo);self.assertEqual(raw['Type'],'CurveLinearColorAtlas');self.assertEqual(raw['NativeTextureKind'],'Texture2D');self.assertNotIn('Mips',raw)
 def test_explicit_typed_subclass_kind_and_conflicting_or_missing_kinds(self):
  self.texture(kind='DerivedCurveAtlas',native_kind='Texture2D');self.assertEqual(catalog._texture_fact(self.repo,'/Game/Atlas')['native_texture_kind'],'Texture2D')
  self.texture(kind='TextureUnproved')
  with self.assertRaises(ValueError):catalog._texture_fact(self.repo,'/Game/Atlas')
  self.texture(native_kind='Texture2DArray')
  with self.assertRaisesRegex(ValueError,'conflict'):catalog._texture_fact(self.repo,'/Game/Atlas')
  self.texture(native_kind=False)
  with self.assertRaises(ValueError):catalog._texture_fact(self.repo,'/Game/Atlas')
 def test_hdr_bytes_aliases_role_and_signature_are_preserved_without_decode(self):
  self.texture();hdr=self.game/'Atlas.hdr';hdr.write_bytes(b'#?RADIANCE\nfloat source fixture\x00\xff');material={'Type':'Material','Name':'M','Package':'/Game/M','Properties':{'textures':{'Palette':'/Game/Atlas'}}};(self.game/'M.json').write_text(json.dumps(material),encoding='utf8')
  before=catalog.material_build_signature('/Game/M',self.repo,texture_bindings=[{'reference':'/Game/Atlas','role':'data'}],recipe='curve-v1',implementation_revision='unit');self.assertTrue(before.complete,before.diagnostics);self.assertEqual(self.repo.image_path('/Game/Atlas'),hdr);self.assertEqual(self.repo.image_path('/Game/Atlas.hdr'),hdr);self.assertEqual(self.repo.image_aliases('/Game/Atlas',path=hdr)[0]['sha256'],repository.digest(hdr));self.assertNotEqual(self.repo.image_key('/Game/Atlas','data'),self.repo.image_key('/Game/Atlas','color'));hdr.write_bytes(hdr.read_bytes()+b'changed HDR values');after=catalog.material_build_signature('/Game/M',self.repo,texture_bindings=[{'reference':'/Game/Atlas','role':'data'}],recipe='curve-v1',implementation_revision='unit');self.assertNotEqual(before.key,after.key)
 def test_png_hdr_dual_representations_are_explicit_ambiguity(self):
  (self.game/'Atlas.png').write_bytes(b'old8bit');(self.game/'Atlas.hdr').write_bytes(b'float HDR')
  with self.assertRaisesRegex(assets.AmbiguousAssetError,'PNG/HDR'):self.repo.image_path('/Game/Atlas')
 def test_identical_hdr_aliases_coalesce_all_paths_and_differing_aliases_fail(self):
  other=self.root/'native/RemappedPlugins/Content';other.mkdir(parents=True);repo=assets.NativeAssetRepository({'/Game':[self.game,other]});(self.game/'Atlas.hdr').write_bytes(b'HDRsame');(other/'Atlas.hdr').write_bytes(b'HDRsame');path=repo.image_path('/Game/Atlas');self.assertEqual(len(repo.image_aliases('/Game/Atlas',path=path)),2);(other/'Atlas.hdr').write_bytes(b'HDRdifferent')
  with self.assertRaises(assets.AmbiguousAssetError):repo.image_path('/Game/Atlas')
 def test_wrong_native_object_name_does_not_reuse_cached_good_fact(self):
  self.texture();self.assertEqual(catalog._texture_fact(self.repo,'/Game/Atlas')['name'],'Atlas')
  with self.assertRaises(ValueError):catalog._texture_fact(self.repo,'/Game/Atlas.WrongObject')
 def test_curve_raw_wins_thin_across_exact_physical_aliases(self):
  ordinary=self.game/'Atlas.json';ordinary.write_text(json.dumps({'Type':'CurveLinearColorAtlas','Name':'Atlas','Package':'/Game/Atlas','Properties':{}}),encoding='utf8');other=self.root/'native/RemappedPlugins/Content';other.mkdir(parents=True);(other/'Atlas.json').write_text(json.dumps({'Data':[]}),encoding='utf8');repo=assets.NativeAssetRepository({'/Game':[self.game,other]});fact=catalog._texture_fact(repo,'/Game/Atlas');self.assertEqual(fact['type'],'CurveLinearColorAtlas');self.assertEqual(len(fact['source']['aliases']),2)
if __name__=='__main__':unittest.main(verbosity=2)
