import gzip,hashlib,importlib,json,os,sys,tempfile,types,unittest
from pathlib import Path
from .native_test_support import PACKAGE,namespace,temp_fixture
namespace()
catalog=importlib.import_module(PACKAGE+'.Shared.materials.native.catalog')
def fixture():
 facts={'package':'/Game/M','parent':'/Game/M','scalars':{'Zero':0},'colors':{},'textures':{'Null':None},'switches':{'Off':False},'settings':{},'scopes':[]}
 digest=hashlib.sha256(json.dumps(facts,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 return {'format':catalog.FORMAT,'build':'fixture','mapping':'fixture','records':{'/Game/M':{'facts':facts,'facts_sha256':digest,'semantic_sha256':'1'*64,'source':{'relative_path':'PioneerGame/Content/M.json','sha256':'0'*64}}},'textures':{},'mounts':{'/Game':['PioneerGame/Content']},'errors':[],'statistics':{'material_records':1,'texture_metadata':0,'error_count':0}}
class GzipCatalogTests(unittest.TestCase):
 def setUp(self):self.temp=temp_fixture('native-gzip-catalog-');self.root=Path(self.temp.name)
 def tearDown(self):self.temp.cleanup()
 def test_gzip_and_plain_json_have_identical_native_facts(self):
  data=json.dumps(fixture(),separators=(',',':')).encode();plain=self.root/'bank.json';compressed=self.root/'bank.json.gz';plain.write_bytes(data);compressed.write_bytes(gzip.compress(data,mtime=0));a=catalog.NativeCatalog.read(plain);b=catalog.NativeCatalog.read(compressed);self.assertEqual(a.payload,b.payload);self.assertEqual(b.payload['records']['/Game/M']['facts']['scalars']['Zero'],0);self.assertIs(b.payload['records']['/Game/M']['facts']['switches']['Off'],False);self.assertIsNone(b.payload['records']['/Game/M']['facts']['textures']['Null'])
 def test_gzip_detected_by_magic_not_filename(self):
  p=self.root/'opaque.catalog';p.write_bytes(gzip.compress(json.dumps(fixture()).encode(),mtime=0));self.assertEqual(catalog.NativeCatalog.read(p).build,'fixture')
 def test_utf8_bom_inside_gzip_supported(self):
  p=self.root/'bom.gz';p.write_bytes(gzip.compress(b'\xef\xbb\xbf'+json.dumps(fixture()).encode(),mtime=0));self.assertEqual(catalog.NativeCatalog.read(p).mapping,'fixture')
 def test_corrupt_or_truncated_gzip_refuses(self):
  p=self.root/'bad.gz';p.write_bytes(gzip.compress(json.dumps(fixture()).encode(),mtime=0)[:-7])
  with self.assertRaises((EOFError,OSError)):catalog.NativeCatalog.read(p)
 def test_decoded_fact_digest_still_validated(self):
  value=fixture();value['records']['/Game/M']['facts']['scalars']['Zero']=99;p=self.root/'tampered.gz';p.write_bytes(gzip.compress(json.dumps(value).encode(),mtime=0))
  with self.assertRaisesRegex(ValueError,'digest mismatch'):catalog.NativeCatalog.read(p)
if __name__=='__main__':unittest.main()
