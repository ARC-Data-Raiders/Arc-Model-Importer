"""Portable pure inheritance tests; no bpy or Blender process.
python -B tests/test_character_parameter_companions.py --scratch Z:/your/task/scratch
All test-created files stay under the passed directory (or DATARAIDERS_TEST_SCRATCH).
"""
import argparse,ast,importlib.util,json,os,sys,tempfile,types,unittest
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--scratch',default=os.environ.get('DATARAIDERS_TEST_SCRATCH') or os.environ.get('TEMP'));opts,rest=parser.parse_known_args();sys.argv=[sys.argv[0]]+rest
if not opts.scratch:raise RuntimeError('Pass --scratch or DATARAIDERS_TEST_SCRATCH; no implicit temp location')
SCRATCH=Path(opts.scratch);SCRATCH.mkdir(parents=True,exist_ok=True)
for name,folder in [('char_test',REPO),('char_test.surface_rebuild',REPO/'surface_rebuild'),('char_test.materials',REPO/'materials')]:
 m=types.ModuleType(name);m.__path__=[str(folder)];sys.modules[name]=m
for module,file in [('char_test.surface_rebuild.assets','assets.py'),('char_test.surface_rebuild.records','records.py')]:
 spec=importlib.util.spec_from_file_location(module,REPO/'surface_rebuild'/file);m=importlib.util.module_from_spec(spec);sys.modules[module]=m;spec.loader.exec_module(m)
def function(path,name,scope):
 node=next(x for x in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(x,ast.FunctionDef) and x.name==name)
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),scope);return scope[name]
READ=function(REPO/'materials/character_body.py','read_character_parameters',{'os':os,'json':json,'__package__':'char_test.materials'})
NATIVE=function(REPO/'materials/fur_cards.py','is_native_colormap_source',{})
body=types.ModuleType('char_test.materials.character_body');body.read_character_parameters=READ;sys.modules[body.__name__]=body
fur=types.ModuleType('char_test.materials.fur_cards');fur.is_native_colormap_source=NATIVE;sys.modules[fur.__name__]=fur
spec=importlib.util.spec_from_file_location('char_test.materials.hair_tangent',REPO/'materials/hair_tangent.py');tangent=importlib.util.module_from_spec(spec);sys.modules[spec.name]=tangent;spec.loader.exec_module(tangent)
COMPLETE=function(REPO/'materials/character.py','_complete_hair_mi',{'__package__':'char_test.materials'})
MASTER='/Game/Pioneer/Characters/Heads/Shared/Hair/Materials/M_Hair_Metahuman_ColorMap'
CHILD='/Game/Pioneer/Characters/Hairs/Style/MI_Style'
def physical(root,logical):return root/'PioneerGame/Content'/logical.removeprefix('/Game/')
def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf-8');return path
def raw(package,kind='MaterialInstanceConstant',parent='',scalars=None,colors=None):
 props={}
 if parent:props['Parent']={'ObjectPath':parent+'.0'}
 if scalars is not None:props['ScalarParameterValues']=[{'ParameterInfo':{'Name':k},'ParameterValue':v} for k,v in scalars.items()]
 if colors is not None:props['VectorParameterValues']=[{'ParameterInfo':{'Name':k},'ParameterValue':v} for k,v in colors.items()]
 return {'Exports':[{'Type':kind,'Name':package.rsplit('/',1)[-1],'Package':package,'Properties':props}]}
class Tests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory(prefix='character-companions-',dir=SCRATCH);self.root=Path(self.tmp.name)/'current';self.leaf=physical(self.root,CHILD)
 def tearDown(self):self.tmp.cleanup()
 def pair(self):
  write(self.leaf.with_suffix('.json'),{'Textures':{},'Parameters':{'Scalars':{'Roughness':.1,'Scraggle':.15}}})
  write(self.leaf.with_suffix('.arc-material.json'),raw(CHILD,parent=MASTER,scalars={'Scraggle':0},colors={'ColorA':{'R':0,'G':.3,'B':0,'A':1}}))
  master=physical(self.root,MASTER)
  write(master.with_suffix('.json'),{'Textures':{},'Parameters':{'Scalars':{'Roughness':.2}}})
  write(master.with_suffix('.arc-material.json'),raw(MASTER,'Material',scalars={'Roughness':.7,'Scraggle':.8,'FlattenNormal':0}))
  return master
 def test_authoritative_chain_both_files_and_exact_parent(self):
  master=self.pair();r=READ(str(self.leaf.with_suffix('.json')))
  self.assertEqual(r['scalars']['Roughness'],.7);self.assertEqual(r['scalars']['Scraggle'],0);self.assertEqual(r['colors']['ColorA'],(0,.3,0,1));self.assertEqual(r['unresolved_parent'],'')
  self.assertTrue(NATIVE(r['sources'],r['unresolved_parent']));self.assertEqual(len(r['sources']),4);self.assertEqual(len(r['dependencies']),4)
 def test_mismatched_package_rejected_even_same_name(self):
  write(self.leaf.with_suffix('.json'),{'Textures':{},'Parameters':{'Scalars':{'Scraggle':.2}}});other=raw('/Game/Other/MI_Style',parent=MASTER,scalars={'Scraggle':0});write(self.leaf.with_suffix('.arc-material.json'),other)
  r=READ(str(self.leaf.with_suffix('.json')));self.assertEqual(r['scalars']['Scraggle'],.2);self.assertEqual(len(r['rejected_companions']),1);self.assertFalse(NATIVE(r['sources'],r['unresolved_parent']))
 def test_unrelated_name_rejected_even_claiming_correct_package(self):
  write(self.leaf.with_suffix('.json'),{'Textures':{},'Parameters':{'Scalars':{'Scraggle':.2}}});bad=raw(CHILD,parent=MASTER);bad['Exports'][0]['Name']='Other';write(self.leaf.with_suffix('.arc-material.json'),bad)
  r=READ(str(self.leaf.with_suffix('.json')));self.assertEqual(len(r['rejected_companions']),1);self.assertEqual(len(r['sources']),1)
 def test_parent_edit_updates_values_and_content_dependency(self):
  master=self.pair();before=READ(str(self.leaf.with_suffix('.json')));write(master.with_suffix('.arc-material.json'),raw(MASTER,'Material',scalars={'Roughness':.9}))
  after=READ(str(self.leaf.with_suffix('.json')));self.assertEqual(after['scalars']['Roughness'],.9)
  old={r['path']:r['sha256'] for r in before['dependencies']};new={r['path']:r['sha256'] for r in after['dependencies']};self.assertNotEqual(old[str(master.with_suffix('.arc-material.json'))],new[str(master.with_suffix('.arc-material.json'))])
 def test_other_root_and_same_basename_are_not_borrowed(self):
  write(self.leaf.with_suffix('.json'),{'Textures':{},'Parameters':{}});write(self.leaf.with_suffix('.arc-material.json'),raw(CHILD,parent=MASTER,scalars={'Scraggle':0}))
  other=physical(Path(self.tmp.name)/'old-database',MASTER);write(other.with_suffix('.arc-material.json'),raw(MASTER,'Material',scalars={'Roughness':.9}))
  write(self.root/'PioneerGame/Content/Unrelated/M_Hair_Metahuman_ColorMap.arc-material.json',raw(MASTER,'Material',scalars={'Roughness':.95}))
  r=READ(str(self.leaf.with_suffix('.json')));self.assertNotIn('Roughness',r['scalars']);self.assertTrue(r['unresolved_parent'].endswith('M_Hair_Metahuman_ColorMap'));self.assertEqual(len(r['sources']),2)
 def test_raw_only_parent_and_remapped_game_alias(self):
  master=self.pair();master.with_suffix('.json').unlink();remap=self.root/'RemappedPlugins/Content/Pioneer/Characters/Hairs/Style/MI_Style.json';write(remap,json.loads(self.leaf.with_suffix('.json').read_text()));write(remap.with_suffix('.arc-material.json'),raw(CHILD,parent=MASTER,scalars={'Scraggle':0}))
  r=READ(str(remap));self.assertEqual(r['scalars']['Roughness'],.7);self.assertTrue(NATIVE(r['sources']))
 def test_alias_cycle_stops_locally(self):
  master=self.pair();write(master.with_suffix('.arc-material.json'),raw(MASTER,parent=CHILD))
  r=READ(str(self.leaf.with_suffix('.json')));self.assertTrue(r['unresolved_parent'].startswith('cycle:'));self.assertLessEqual(len(r['sources']),4)
 def test_include_master_false_keeps_zero_child_and_tracks_parent(self):
  self.pair();r=READ(str(self.leaf.with_suffix('.json')),include_master=False);self.assertNotIn('Roughness',r['scalars']);self.assertEqual(r['scalars']['Scraggle'],0);self.assertEqual(len(r['dependencies']),4)
 def test_parent_cannot_escape_initial_root(self):
  write(self.leaf.with_suffix('.json'),{'Textures':{},'Parameters':{}});write(self.leaf.with_suffix('.arc-material.json'),raw(CHILD,parent='/Game/../../Outside/M_Forbidden'))
  r=READ(str(self.leaf.with_suffix('.json')));self.assertTrue(r['unresolved_parent']);self.assertEqual(len(r['dependencies']),2)
 def test_complete_verified_raw_wins_stale_flattened_defaults(self):
  self.pair();parsed={'scalars':{'Roughness':.1,'Scraggle':.15},'colours':[('ColorA',(1,1,1,1))],'textures':[]}
  r=COMPLETE(parsed,str(self.leaf.with_suffix('.json')));self.assertEqual(r['scalars']['Roughness'],.7);self.assertEqual(r['scalars']['Scraggle'],0);self.assertEqual(dict(r['colours'])['ColorA'],(0,.3,0,1));self.assertEqual(r['parameter_authority']['policy'],'source-local-native-companions')
 def test_complete_parent_edit_not_masked_by_converted_defaults(self):
  master=self.pair();r=COMPLETE({'scalars':{'Roughness':.1},'textures':[],'colours':[]},str(self.leaf.with_suffix('.json')));self.assertEqual(r['scalars']['Roughness'],.7)
  write(master.with_suffix('.arc-material.json'),raw(MASTER,'Material',scalars={'Roughness':.9}));r=COMPLETE({'scalars':{'Roughness':.1},'textures':[],'colours':[]},str(self.leaf.with_suffix('.json')));self.assertEqual(r['scalars']['Roughness'],.9)
 def test_legacy_explicit_json_zero_overrides_preserved(self):
  write(self.leaf.with_suffix('.json'),{'Textures':{},'Parameters':{'Scalars':{'Scraggle':.2}}});r=COMPLETE({'scalars':{'Scraggle':0},'textures':[],'colours':[]},str(self.leaf.with_suffix('.json')));self.assertEqual(r['scalars']['Scraggle'],0);self.assertEqual(r['parameter_authority']['policy'],'legacy-json-child-overrides')
 def test_explicit_palette_vectors_and_scalar_zero_still_overlay_style(self):
  self.pair();style=COMPLETE({'scalars':{},'colours':[],'textures':[]},str(self.leaf.with_suffix('.json')))
  palette=physical(self.root,'/Game/Pioneer/Characters/Hairs/HairColors/MI_Pink').with_suffix('.json');write(palette,raw('/Game/Pioneer/Characters/Hairs/HairColors/MI_Pink',scalars={'Spec0':0},colors={'ColorA':{'R':.9,'G':0,'B':.4,'A':1}}))
  def parse(path):
   record=READ(path);return {'instance_scalars':record['instance_scalars'],'scalars':record['scalars'],'colours':list(record['colors'].items())}
  merge=function(REPO/'materials/character.py','_merge_hair_colour_mi',{'os':os,'_parse_hair_mi':parse});merge(style,str(palette));self.assertEqual(style['scalars']['Spec0'],0);self.assertEqual(style['scalars']['Scraggle'],0);self.assertEqual(dict(style['colours'])['ColorA'],(.9,0,.4,1))
 def test_exact_companion_suffix_only(self):
  self.assertTrue(NATIVE(['/root/Pioneer/Characters/Heads/Shared/Hair/Materials/M_Hair_Metahuman_ColorMap.arc-material.json']))
  self.assertFalse(NATIVE(['/root/Other/M_Hair_Metahuman_ColorMap.arc-material.json']))
if __name__=='__main__':unittest.main()
