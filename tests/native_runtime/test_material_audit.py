import importlib,unittest
from .native_test_support import PACKAGE,namespace
namespace()
audit=importlib.import_module(PACKAGE+'.Shared.materials.material_audit')
IMG=lambda stem,**k:dict({'stem':stem,'filepath':'//t/'+stem+'.png','colorspace':'sRGB','linked':True,'has_image':True,'sidecar_srgb':None},**k)
MI=lambda **k:dict({'package':'/Game/MI_X','master':'/Game/M_Base','family':'surface','textures':{'BaseColor':'/Game/T_X_BC'},'settings':{}},**k)
G=lambda **k:dict({'name':'MI_X','images':[IMG('T_X_BC')],'render_method':'DITHERED','alpha_linked':False,'alpha_clip_threshold':None,'emission_linked':False,'vertex_colour_attrs':[],'uv_maps_used':[],'output_linked':True},**k)
def flags(graph=None,mi=None,report=None,mesh=None,error=None,swallowed=()):
 return set(audit.audit_material_facts(G() if graph is None else graph,MI() if mi is None else mi,report,mesh or {'uv_layers':['UV0'],'color_attributes':['Col']},error,swallowed)['flags'])
# flag: (red kwargs, green kwargs)
CASES={
 'tex_param_unlinked:BaseColor':({'graph':G(images=[IMG('T_X_BC',linked=False)])},{}),
 'tex_param_skipped:BaseColor':({'report':{'skipped_textures':{'BaseColor':'unused by layout'}}},{}),
 'image_missing:BaseColor':({'report':{'missing_textures':[{'reference':'/Game/T_X_BC','reason':'absent'}]}},{}),
 'colorspace_mismatch:BaseColor':({'graph':G(images=[IMG('T_X_BC',sidecar_srgb=False)])},{'graph':G(images=[IMG('T_X_BC',sidecar_srgb=True)])}),
 'blend_mismatch':({'graph':G(render_method='BLENDED')},{}),
 'opacity_unwired':({'mi':MI(settings={'BlendMode':'BLEND_Translucent'}),'graph':G(render_method='BLENDED')},{'mi':MI(settings={'BlendMode':'BLEND_Translucent'}),'graph':G(render_method='BLENDED',alpha_linked=True)}),
 'clip_mismatch':({'mi':MI(settings={'BlendMode':'BLEND_Masked'},opacity_clip=0.5),'graph':G(alpha_linked=True,alpha_clip_threshold=0.2)},{'mi':MI(settings={'BlendMode':'BLEND_Masked'},opacity_clip=0.5),'graph':G(alpha_linked=True,alpha_clip_threshold=0.5)}),
 'uv_missing:1':({'error':'Required authored UV channel unavailable: 1'},{}),
 'uv_clamped:2':({'report':{'notes':['UV2 clamped to last mesh channel UV0']}},{}),
 'emissive_unwired':({'mi':MI(scalars={'EmissiveIntensity':4.0})},{'mi':MI(scalars={'EmissiveIntensity':4.0}),'graph':G(emission_linked=True)}),
 'vertex_colour_missing':({'graph':G(vertex_colour_attrs=['Paint'])},{'graph':G(vertex_colour_attrs=['Col'])}),
 'layout_unknown:qq':({'mi':MI(textures={'BaseColor':'/Game/T_X_QQ'}),'graph':G(images=[IMG('T_X_QQ')])},{}),
 'build_exception':({'error':'KeyError: Foo'},{}),
 'fallback_family':({'mi':MI(family='fallback')},{}),
 'unknown_master':({'mi':MI(master='')},{}),
 'no_texture_master':({'mi':MI(family='parameterless')},{}),
 'engine_default_material':({'mi':MI(family='engine_default')},{}),
 'swallowed:dispatch.slot_setup':({'swallowed':[{'where':'dispatch.slot_setup','error':'x','error_type':'ValueError','traceback':''}]},{}),
}
class MaterialFlagTests(unittest.TestCase):
 def test_red_cases_raise_flag(self):
  for name,(red,_) in CASES.items():
   with self.subTest(flag=name):self.assertIn(name,flags(**red))
 def test_green_cases_clear_flag(self):
  for name,(_,green) in CASES.items():
   with self.subTest(flag=name):self.assertNotIn(name,flags(**green))
 def test_clean_material_has_no_flags(self):self.assertEqual(flags(),set())
 def test_build_exception_keeps_full_text(self):
  text='RuntimeError: '+'x'*2000
  record=audit.audit_material_facts({},MI(),None,{},text)
  self.assertEqual(record['error'],text);self.assertEqual(record['details']['build_exception'][0],text)
 def test_native_render_limit_is_flagged_not_exception(self):
  text='Native expression bindings unresolved for X/M_Road_Snow_01; affected source: /Game/M_Road_Snow_01'
  record=audit.audit_material_facts({},MI(),None,{},text)
  self.assertNotIn('build_exception',record['flags']);self.assertEqual(record['severity']['native_render_limit'],'inconsistency')
 def test_swallowed_queue_drains(self):
  try:raise RuntimeError('boom '*100)
  except RuntimeError as exc:audit.record_swallowed('t.where',exc)
  items=audit.drain_swallowed();self.assertEqual(len(items),1);self.assertIn('boom',items[0]['traceback']);self.assertEqual(items[0]['error'],'boom '*100);self.assertEqual(audit.drain_swallowed(),[])
rules=importlib.import_module(PACKAGE+'.Shared.materials.audit_param_rules')
def rec(mi=None,graph=None,master=None):
 return audit.audit_material_facts(G() if graph is None else graph,MI() if mi is None else mi,None,{'uv_layers':['UV0'],'color_attributes':['Col']},None,(),master=master)
# Two textures, BaseColor linked; the other param unlinked.
def two(param,ref,switches=None,master=None,global_copy=False):
 tex={'BaseColor':'/Game/T_X_BC',param:ref}
 if global_copy:tex['Global.'+param]=ref
 return rec(MI(textures=tex,switches=switches or {}),G(images=[IMG('T_X_BC'),IMG(ref.rsplit('/',1)[-1],linked=False)]),master)
MASTER=lambda **k:dict({'package':'/Game/M_Base','textures':{'Prop AO Texture':'/Game/T_Default_AO','Snow Normal':'/Game/T_Snow_N'},'switches':{}},**k)
class ParamClassTests(unittest.TestCase):
 def test_global_copy_is_one_flag(self):
  r=two('Prop AO Texture','/Game/T_Y_AO',global_copy=True)
  hits=[f for f in r['flags'] if 'Prop AO Texture' in f];self.assertEqual(hits,['tex_param_unlinked:Prop AO Texture'])
  self.assertFalse(any(f.startswith('tex_param_unlinked:Global.') for f in r['flags']))
 def test_layer_switch_off_is_skipped_info(self):
  r=two('Snow Normal','/Game/T_Snow_N',switches={'Use Snow':False},global_copy=True)
  self.assertIn('layer_world_state:snow',r['flags']);self.assertNotIn('layer_not_built:snow',r['flags'])
  self.assertEqual(r['severity']['layer_world_state:snow'],'info');self.assertEqual(r['gate_count'],0)
  self.assertFalse(r['details']['layer_world_state:snow'][0]['enabled'])
 def test_world_state_layer_default_on_is_info(self):
  # Enabling switch on only at the root default: weather decides, not the MI.
  m=MASTER(switches={'Puddles':True});r=two('Puddles Mask','/Game/T_Puddle_M',switches={'Puddles':True},master=m)
  self.assertIn('layer_world_state:waterline',r['flags']);self.assertNotIn('layer_not_built:waterline',r['flags']);self.assertEqual(r['gate_count'],0)
 def test_world_state_layer_without_switch_is_info(self):
  r=two('10. LandscapeSnowMask','/Game/T_Snow_M',switches={'Is Terrain':True})
  self.assertIn('layer_world_state:snow',r['flags']);self.assertEqual(r['severity']['layer_world_state:snow'],'info')
 def test_world_state_layer_mi_enabled_is_inconsistency(self):
  m=MASTER(switches={'Puddles':False});r=two('Puddles Mask','/Game/T_Puddle_M',switches={'Puddles':True},master=m)
  self.assertIn('layer_not_built:waterline',r['flags']);self.assertEqual(r['details']['layer_not_built:waterline'][0]['level'],'medium')
 def test_reclass_layer_rule_entry(self):
  self.assertEqual(rules.layer_rule('frost',None,[]),('layer_world_state:frost','info'))
  self.assertEqual(rules.layer_rule('sign',True,[{'switch':'Use Sign','value':True,'overridden':False}]),('layer_not_built:sign','inconsistency'))
 def test_layer_switch_on_is_not_built(self):
  r=two('Snow Normal','/Game/T_Snow_N',switches={'Use Snow':True})
  self.assertIn('layer_not_built:snow',r['flags']);self.assertNotIn('tex_layer_skipped:snow',r['flags'])
  self.assertEqual(r['severity']['layer_not_built:snow'],'inconsistency');self.assertEqual(r['gate_count'],1)
 def test_layer_unknown_switch_is_skipped(self):
  r=two('SignTexture','/Game/T_Sign_C');self.assertIn('tex_layer_skipped:sign',r['flags']);self.assertIsNone(r['details']['tex_layer_skipped:sign'][0]['enabled'])
 def test_inherited_default_is_info_only(self):
  r=two('Prop AO Texture','/Game/T_Default_AO',master=MASTER())
  self.assertIn('tex_default_unlinked:Prop AO Texture',r['flags']);self.assertNotIn('tex_param_unlinked:Prop AO Texture',r['flags'])
  self.assertEqual(r['gate_count'],0);self.assertEqual(r['overridden_source'],'root_master_compare')
 def test_inherited_default_with_gate_on_is_defect(self):
  r=two('Prop AO Texture','/Game/T_Default_AO',switches={'Use AO':True},master=MASTER())
  self.assertIn('tex_param_unlinked:Prop AO Texture',r['flags'])
 def test_override_is_defect(self):
  r=two('Prop AO Texture','/Game/T_Y_AO',master=MASTER())
  self.assertIn('tex_param_unlinked:Prop AO Texture',r['flags']);self.assertEqual(r['severity']['tex_param_unlinked:Prop AO Texture'],'defect')
 def test_override_gated_off_is_info(self):
  r=two('Prop AO Texture','/Game/T_Y_AO',switches={'Use AO':False},master=MASTER())
  self.assertIn('tex_gated_off:Prop AO Texture',r['flags']);self.assertEqual(r['gate_count'],0)
 def test_core_param_unlinked_is_defect_even_if_default(self):
  m=MASTER(textures={'BaseColor':'/Game/T_X_BC'})
  r=rec(graph=G(images=[IMG('T_X_BC',linked=False)]),master=m)
  self.assertIn('tex_param_unlinked:BaseColor',r['flags']);self.assertEqual(r['gate_count'],1)
 def test_stem_named_texture_unlinked_is_info(self):
  f=flags(mi=MI(textures={'T_X_AO':'/Game/T_X_AO','Prop AO Texture':'/Game/T_X_AO'}),graph=G(images=[IMG('T_X_AO',linked=False)]))
  self.assertIn('tex_unnamed_unlinked:T_X_AO',f);self.assertNotIn('tex_param_unlinked:T_X_AO',f)
 def test_modifier_switch_does_not_open_gate(self):
  tx=[{'name':'BaseColor','path':'/Game/T_X_BC','overridden':True},{'name':'CR Blend','path':'/Game/T_B_CR','overridden':True}]
  sw=[{'name':'Invert Blend Mask','value':False,'overridden':False},{'name':'BlendTextureUV0','value':True,'overridden':False},{'name':'Enable Blend Textures','value':False,'overridden':False}]
  g=G(images=[IMG('T_X_BC'),IMG('T_B_CR',linked=False)])
  f=flags(mi=MI(textures=tx,switches=sw),graph=g)
  self.assertIn('tex_gated_off:CR Blend',f);self.assertNotIn('tex_param_unlinked:CR Blend',f)
  sw[2]['value']=True
  self.assertIn('tex_param_unlinked:CR Blend',flags(mi=MI(textures=tx,switches=sw),graph=g))
 def test_prefix_switch_gates_param(self):
  tx=[{'name':'BaseColor','path':'/Game/T_X_BC','overridden':True},{'name':'RGB Tint Mask Input','path':'/Game/T_T_M','overridden':True}]
  sw=[{'name':'RGB Tint Mask','value':False,'overridden':False},{'name':'Use Tinting','value':True,'overridden':True}]
  g=G(images=[IMG('T_X_BC'),IMG('T_T_M',linked=False)])
  self.assertNotIn('tex_param_unlinked:RGB Tint Mask Input',flags(mi=MI(textures=tx,switches=sw),graph=g))
 def test_stem_named_texture_without_twin_stays_flagged(self):
  f=flags(mi=MI(textures={'T_X_AO':'/Game/T_X_AO'}),graph=G(images=[IMG('T_X_AO',linked=False)]))
  self.assertNotIn('tex_unnamed_unlinked:T_X_AO',f);self.assertIn('tex_param_unlinked:T_X_AO',f)
 def test_unset_stand_in_is_not_tex_param_unlinked(self):
  f=flags(graph=G(images=[IMG('T_X_BC',linked=False)]),report={'unset_textures':['BaseColor']})
  self.assertNotIn('tex_param_unlinked:BaseColor',f)
 def test_emissive_switches_off_is_gated_info(self):
  sw=[{'name':'Use Emissive','value':False,'overridden':False},{'name':'Use CR as Emissive','value':False,'overridden':False},{'name':'World Position Emissive Toggle','value':True,'overridden':False}]
  r=rec(MI(scalars={'EmissiveIntensity':4.0},switches=sw),G())
  self.assertNotIn('emissive_unwired',r['flags']);self.assertEqual(r['severity']['emissive_gated_off'],'info')
  sw[0]['value']=True
  self.assertIn('emissive_unwired',rec(MI(scalars={'EmissiveIntensity':4.0},switches=sw),G())['flags'])
 def test_emissive_zero_default_without_switch_is_info(self):
  r=rec(MI(scalars={'EmissiveIntensity':0.0},textures={'BaseColor':'/Game/T_X_BC','Emissive':'/Game/T_X_E'}),G())
  self.assertNotIn('emissive_unwired',r['flags']);self.assertEqual(r['severity']['emissive_default_off'],'info')
  self.assertIn('emissive_unwired',rec(MI(scalars={'EmissiveIntensity':3.0}),G())['flags'])
 def test_core_packed_mask_off_switch_is_gated(self):
  sw={'Don\'t use Packed Mask':True}
  f=two('Packed Mask','/Game/T_X_M',switches=sw)['flags']
  self.assertIn('tex_gated_off:Packed Mask',f);self.assertNotIn('tex_param_unlinked:Packed Mask',f)
  sw['Don\'t use Packed Mask']=False
  self.assertIn('tex_param_unlinked:Packed Mask',two('Packed Mask','/Game/T_X_M',switches=sw)['flags'])
 def test_mask_gated_by_use_masks(self):
  f=two('Mask','/Game/T_X_Mask',switches={'Use Masks':False,'RGB Tint Mask':True})['flags']
  self.assertIn('tex_gated_off:Mask',f)
 def test_explicit_mi_params_lists(self):
  mi=MI(textures=[{'name':'BaseColor','path':'/Game/T_X_BC','overridden':True},{'name':'Prop AO Texture','path':'/Game/T_D_AO','overridden':False}],switches=[{'name':'Use Snow','value':False,'overridden':True}])
  r=rec(mi,G(images=[IMG('T_X_BC'),IMG('T_D_AO',linked=False)]))
  self.assertIn('tex_default_unlinked:Prop AO Texture',r['flags']);self.assertEqual(r['overridden_source'],'explicit')
 def test_layout_unknown_carries_param_and_texture(self):
  r=rec(MI(textures={'BaseColor':'/Game/T_X_QQ'}),G(images=[IMG('T_X_QQ')]))
  self.assertEqual(r['details']['layout_unknown:qq'][0],{'param':'BaseColor','texture':'/Game/T_X_QQ','suffix':'qq'});self.assertEqual(r['severity']['layout_unknown:qq'],'defect')
 def test_builder_layout_suffixes_are_known(self):
  for sfx in ('NHM','CA','NAOH','NAA','NH','NOX','NXR','NCH','XEM'):
   with self.subTest(suffix=sfx):
    r=rec(MI(textures={'Normal':'/Game/T_X_'+sfx}),G(images=[IMG('T_X_'+sfx)]))
    self.assertNotIn('layout_unknown:'+sfx.casefold(),r['flags'])
 def test_unproven_suffixes_stay_flagged(self):
  for sfx in ('NM','CX','NX','BCH','CW','CRA'):
   with self.subTest(suffix=sfx):
    r=rec(MI(textures={'Mask':'/Game/T_X_'+sfx}),G(images=[IMG('T_X_'+sfx)]))
    self.assertIn('layout_unknown:'+sfx.casefold(),r['flags'])
 def test_round4_proven_suffixes_are_known(self):
  for sfx in ('CH','ID','NR','NTR','NTX','NRH','NXM','NRC','NMD','NC','NXH'):
   with self.subTest(suffix=sfx):
    r=rec(MI(textures={'Normal':'/Game/T_X_'+sfx}),G(images=[IMG('T_X_'+sfx)]))
    self.assertNotIn('layout_unknown:'+sfx.casefold(),r['flags'])
 def test_round4_layouts_wire_only_proven_channels(self):
  tl=importlib.import_module(PACKAGE+'.Shared.materials.texture_layouts')
  self.assertEqual(tl.LAYOUTS['CH'],[('color','color'),('a','height')])
  for sfx in ('NR','NTR','NTX','NRH','NXM','NRC','NMD','NC','NXH'):
   with self.subTest(suffix=sfx):
    self.assertEqual(tl.LAYOUTS[sfx],[('normal','normal')]);self.assertIn(sfx,tl.UNPROVEN_CHANNELS)
  self.assertEqual({r for _,r in tl.LAYOUTS['ID']},{'mask'})
 def test_param_proven_layouts_are_known(self):
  for param,sfx in (('Overlay','CX'),('NA','NHA'),('NA','NOA'),('NOH Blend','NX'),('BaseColor','CX'),('Global.TrunkBaseColor','CX')):
   with self.subTest(param=param,suffix=sfx):
    r=rec(MI(textures={param:'/Game/T_X_'+sfx}),G(images=[IMG('T_X_'+sfx)]))
    self.assertFalse([f for f in r['flags'] if f.startswith('layout_unknown')])
 def test_name_words_are_not_suffixes(self):
  for name in ('T_Default_01_White_Grayscale','T_BuriedCity_01_Color_Meshes','T_Rock_tex','T_Grid_13x13'):
   with self.subTest(texture=name):
    r=rec(MI(textures={'Mask':'/Game/'+name}),G(images=[IMG(name)]))
    self.assertFalse([f for f in r['flags'] if f.startswith('layout_unknown')])
 def test_builder_reads_param_layout(self):
  tl=importlib.import_module(PACKAGE+'.Shared.materials.texture_layouts')
  self.assertEqual(tl.param_layout('Global.Overlay','cx'),[('r','mask')])
  self.assertIsNone(tl.param_layout('2. Overlay CR','CX'))
 def test_linked_texture_in_gated_off_layer_is_info(self):
  ref='/Game/T_Frost_01_A_NM'
  r=rec(MI(textures={'BaseColor':'/Game/T_X_BC','Frost Fresnel Mask Breakup':ref},switches={'Use Frost':False}),G(images=[IMG('T_X_BC'),IMG('T_Frost_01_A_NM')]))
  self.assertEqual(r['severity']['layout_unknown:nm'],'info')
 def test_r10_nx_xam_param_layouts(self):
  tl=importlib.import_module(PACKAGE+'.Shared.materials.texture_layouts')
  for p in ('NX','Normal','PM_Normals'):self.assertEqual(tl.param_layout(p,'nx'),[('normal','normal')],p)
  self.assertEqual(tl.param_layout('Packed Mask','xam'),[('b','metal')])
  self.assertIsNone(tl.param_layout('3. Blend Mask','NX'))
 def test_r23_xem_param_layouts(self):
  # R23: PM_SpecularMasks is the Packed Mask alias; default NX packed mask and single Mask.R are XEM plan reads.
  tl=importlib.import_module(PACKAGE+'.Shared.materials.texture_layouts')
  self.assertEqual(tl.param_layout('PM_SpecularMasks','xam'),tl.param_layout('Packed Mask','XAM'))
  self.assertEqual(tl.param_layout('Packed Mask','nx'),[('b','metal')])
  self.assertEqual(tl.param_layout('Mask','XAM'),[('r','mask')])
  self.assertNotIn('g',[c for c,_ in tl.param_layout('Packed Mask','XAM')])
  self.assertIsNone(tl.param_layout('PaintBreakup','NX'))
  for s in ('NX','nah'):self.assertEqual(tl.param_layout('NormalMap',s),[('normal','normal')],s)
 def test_r33_param_layouts(self):
  # R33: CR params read _CW as CR; CH reads _BCH as CH; Vegetation Normals reads NA/NTA RG only.
  tl=importlib.import_module(PACKAGE+'.Shared.materials.texture_layouts')
  for p in ('CR','CR Blend','2. CR'):self.assertEqual(tl.param_layout(p,'cw'),tl.LAYOUTS['CR'],p)
  self.assertIsNone(tl.param_layout('Overlay','CW'))
  self.assertEqual(tl.param_layout('CH','bch'),tl.LAYOUTS['CH'])
  for s in ('NA','nta'):self.assertEqual(tl.param_layout('Normals',s),[('normal','normal')],s)
  self.assertEqual(tl.param_layout('T_Default_01_NX','NX'),[('normal','normal')])
  self.assertIsNone(tl.param_layout('3. Blend Mask','NX'))
 def test_r23_stem_pseudo_param_follows_named_param(self):
  ref='/Game/T_Rsr_Cab_01_A_XAM'
  r=rec(MI(textures={'Packed Mask':ref,'T_Rsr_Cab_01_A_XAM':ref}),G(images=[IMG('T_Rsr_Cab_01_A_XAM')]))
  self.assertNotIn('layout_unknown:xam',r['flags'])
  r=rec(MI(textures={'T_Solo_01_A_XAM':'/Game/T_Solo_01_A_XAM'}),G(images=[IMG('T_Solo_01_A_XAM')]))
  self.assertIn('layout_unknown:xam',r['flags'])
 def test_linked_world_state_layer_texture_is_info(self):
  # R10: frost switch not set on the MI (inherited Frost Fresnel default) -> world state, info.
  ref='/Game/T_Frost_01_A_NM'
  r=rec(MI(textures={'BaseColor':'/Game/T_X_BC','Frost Fresnel Mask Breakup':ref},switches={}),G(images=[IMG('T_X_BC'),IMG('T_Frost_01_A_NM')]))
  self.assertEqual(r['severity']['layout_unknown:nm'],'info')
 def test_linked_world_state_layer_enabled_stays_defect(self):
  ref='/Game/T_Frost_01_A_NM'
  r=rec(MI(textures={'BaseColor':'/Game/T_X_BC','Frost Fresnel Mask Breakup':ref},switches={'Use Frost':True}),G(images=[IMG('T_X_BC'),IMG('T_Frost_01_A_NM')]))
  self.assertEqual(r['severity']['layout_unknown:nm'],'defect')
 def test_inherited_default_not_sampled_is_info(self):
  ref='/Game/T_Wear_01_QQ'
  r=two('ID Wear Mask',ref,master=MASTER(textures={'ID Wear Mask':ref}))
  self.assertEqual(r['severity']['layout_unknown:qq'],'info')
 def test_linked_core_unknown_stays_defect(self):
  r=rec(MI(textures={'Normal':'/Game/T_X_NM'}),G(images=[IMG('T_X_NM')]))
  self.assertEqual(r['severity']['layout_unknown:nm'],'defect')
 def test_audit_and_builder_share_layout_table(self):
  tl=importlib.import_module(PACKAGE+'.Shared.materials.texture_layouts')
  self.assertIs(audit.KNOWN_SUFFIXES,tl.KNOWN_SUFFIXES)
  self.assertTrue({k.casefold() for k in tl.LAYOUTS}<=audit.KNOWN_SUFFIXES)
 def test_layout_unknown_on_skipped_layer_is_info(self):
  r=two('Snow Normal','/Game/T_Snow_NQ',switches={'Use Snow':False})
  self.assertEqual(r['severity']['layout_unknown:nq'],'info')
 def test_severity_defaults(self):
  for f,s in {'no_texture_master:M':'inconsistency','engine_default_material:M':'info','fallback_family:fallback':'defect','tex_param_unlinked:X':'defect','layer_not_built:snow':'inconsistency','tex_default_unlinked:X':'info','uv_clamped:1':'info'}.items():
   with self.subTest(flag=f):self.assertEqual(rules.severity(f),s)
class MasterPassThroughTests(unittest.TestCase):
 # Live flat MI rows (dispatch route) carry textures as (name, path) pairs.
 def flat(self,ref):
  return MI(textures=[('BaseColor','/Game/T_X_BC'),('Prop AO Texture',ref)],switches={})
 def graph(self,ref):
  return G(images=[IMG('T_X_BC'),IMG(ref.rsplit('/',1)[-1],linked=False)])
 def test_tuple_textures_with_master_inherit(self):
  ref='/Game/T_Default_AO';r=rec(self.flat(ref),self.graph(ref),MASTER())
  self.assertEqual(r['overridden_source'],'root_master_compare')
  self.assertIn('tex_default_unlinked:Prop AO Texture',r['flags']);self.assertEqual(r['gate_count'],0)
  self.assertIs(r['details']['tex_default_unlinked:Prop AO Texture'][0]['overridden'],False)
 def test_tuple_textures_with_master_override_is_defect(self):
  ref='/Game/T_Y_AO';r=rec(self.flat(ref),self.graph(ref),MASTER())
  self.assertIn('tex_param_unlinked:Prop AO Texture',r['flags'])
  self.assertIs(r['details']['tex_param_unlinked:Prop AO Texture'][0]['overridden'],True)
  self.assertEqual(r['severity']['tex_param_unlinked:Prop AO Texture'],'defect')
 def test_master_unavailable_is_info(self):
  ref='/Game/T_Default_AO'
  r=audit.audit_material_facts(self.graph(ref),self.flat(ref),None,{'uv_layers':['UV0'],'color_attributes':['Col']},None,(),master_error='ResolutionError: missing')
  self.assertEqual(r['severity']['master_unavailable'],'info');self.assertEqual(r['details']['master_unavailable'],['ResolutionError: missing'])
 def test_r33_own_root_is_its_master(self):
  # Base UMaterial slot: the record is its own master, so no master_unavailable and nothing overridden.
  ref='/Game/T_Default_AO'
  r=audit.audit_material_facts(self.graph(ref),self.flat(ref),None,{'uv_layers':['UV0'],'color_attributes':['Col']},None,(),master_error='MI is its own root')
  self.assertNotIn('master_unavailable',r['flags']);self.assertEqual(r['overridden_source'],'root_master_compare')
  self.assertIs(r['details']['tex_default_unlinked:Prop AO Texture'][0]['overridden'],False)
 def test_load_master_caches_and_never_raises(self):
  audit.clear_masters();calls=[]
  def loader(k):
   calls.append(k)
   if k=='bad':raise KeyError(k)
   return MASTER()
  self.assertEqual(audit.load_master('/Game/M_Base',loader,'r')[0]['package'],'/Game/M_Base')
  audit.load_master('/game/m_base',loader,'r');self.assertEqual(calls,['/Game/M_Base'])
  m,e=audit.load_master('bad',loader,'r');self.assertIsNone(m);self.assertIn('KeyError',e)
 def test_detail_facts(self):
  r=rec(graph=G(images=[IMG('T_X_BC',sidecar_srgb=False)]))
  self.assertEqual(r['details']['colorspace_mismatch:BaseColor'][0],{'image':'T_X_BC','expected':'linear','actual':'sRGB'})
  r=rec(MI(textures={'BaseColor':'/Game/T_X_QQ'}),G(images=[IMG('T_X_QQ')]))
  self.assertEqual(r['details']['layout_unknown:qq'][0]['suffix'],'qq')
OBJ=lambda **k:dict({'name':'SM_X','slots':['MI_X'],'dims':[1.0,2.0,3.0],'tris':12},**k)
META=lambda **k:dict({'slots':[('S','MI_X','p')],'dims':[1.0,2.0,3.0],'tris':12},**k)
OCASES={
 'slot_count_mismatch':((OBJ(),META(slots=[1,2])),(OBJ(),META())),
 'slot_unassigned':((OBJ(slots=[None]),META()),(OBJ(),META())),
 'bounds_mismatch':((OBJ(dims=[1.0,2.0,4.0]),META()),(OBJ(dims=[1.0,2.0,3.01]),META())),
 'tri_count_mismatch':((OBJ(tris=10),META()),(OBJ(),META())),
}
class ObjectFlagTests(unittest.TestCase):
 def test_red_and_green(self):
  for name,(red,green) in OCASES.items():
   with self.subTest(flag=name):
    self.assertIn(name,audit.audit_object_facts(*red)['flags']);self.assertNotIn(name,audit.audit_object_facts(*green)['flags'])
 def test_missing_meta_is_listed_unchecked(self):
  r=audit.audit_object_facts(OBJ(),{});self.assertEqual(set(r['unchecked']),{'slot_count','bounds','tris'})
class _Mat(dict):
 name='MI_Kept'
class R12PackedMaskRoutingTests(unittest.TestCase):
 XEM='/Game/M_PropPreset_CR+NOX+XEM.M_PropPreset_CR+NOX+XEM'
 NOM='/Game/M_PropPreset_CR+NOM.M_PropPreset_CR+NOM'
 def pm(self,master,switches):
  return rec(MI(master=master,textures={'BaseColor':'/Game/T_X_BC','Packed Mask':'/Game/T_Default_01_M'},switches=switches),G(images=[IMG('T_X_BC'),IMG('T_Default_01_M',linked=False)]))['flags']
 def test_xem_single_mask_gates_packed_off(self):
  f=self.pm(self.XEM,{'Use Masks':True,'Use single Mask Texture':True})
  self.assertIn('tex_gated_off:Packed Mask',f);self.assertNotIn('tex_param_unlinked:Packed Mask',f)
 def test_xem_packed_read_stays_defect(self):
  self.assertIn('tex_param_unlinked:Packed Mask',self.pm(self.XEM,{'Use Masks':True,'Use single Mask Texture':False}))
 def test_nom_without_tinting_gates_packed_off(self):
  f=self.pm(self.NOM,{"Don't use Packed Mask":False,'Use Tinting':False,'Use Default Texture Setup':True,'Use Packed Tint Mask':True,'Use Mask Texture':False})
  self.assertIn('tex_gated_off:Packed Mask',f)
 def test_nom_tint_plan_read_stays_defect(self):
  f=self.pm(self.NOM,{"Don't use Packed Mask":False,'Use Tinting':True,'Use Default Texture Setup':True,'Use Packed Tint Mask':True,'Use Mask Texture':False})
  self.assertIn('tex_param_unlinked:Packed Mask',f)
 def test_other_master_keeps_name_gate(self):
  self.assertIn('tex_param_unlinked:Packed Mask',self.pm('/Game/M_Base',{'Use Tinting':False}))
 def test_routed_family_fills_empty_report(self):
  m=_Mat(arc_mi_family='environment')
  self.assertEqual(audit.with_routed_family(None,m)['family'],'environment')
  self.assertEqual(audit.with_routed_family({'family':'surface'},m)['family'],'surface')
 def test_kept_family_replaces_routed_placeholder(self):
  import json
  m=_Mat(arc_mi_family='environment',arc_audit=json.dumps({'family':'environment','flags':[]}))
  self.assertTrue(audit.stamp_kept_family(m,'native_packed_prop'))
  self.assertEqual(json.loads(m['arc_audit'])['family'],'native_packed_prop')
  m2=_Mat(arc_audit=json.dumps({'family':'surface','flags':[]}))
  self.assertFalse(audit.stamp_kept_family(m2,'native_packed_prop'))
class R35BlownPropLightTests(unittest.TestCase):
 E=[{'name':'Emissive Intensity','value':1.0},{'name':'Roughness','value':0.5}]
 def mates(self,vals,p=None):return [(v,p if p is not None else self.E) for v in vals]
 def test_clean_mates_demote(self):
  c='/G/M_A.M_A|base|abc|BLEND_Opaque'
  p=audit.blown_out_by_prop_light([c],{c:self.mates([0.0,0.0,0.01])},{c:self.E})
  self.assertTrue(p.startswith('blown_out_prop_light: M_A.M_A median 0.00 over 3'))
 def test_blown_mates_or_few_keep(self):
  self.assertIsNone(audit.blown_out_by_prop_light(['K'],{'K':self.mates([0.9,0.8,0.0])}))
  self.assertIsNone(audit.blown_out_by_prop_light(['K'],{'K':self.mates([0.0,0.0])}))
  self.assertIsNone(audit.blown_out_by_prop_light(['K','L'],{'K':self.mates([0,0,0])}))
  self.assertIsNone(audit.blown_out_by_prop_light([''],{}))
 def test_seeded_emissive_x50_keeps_flag(self):
  hot=[{'name':'Emissive Intensity','value':50.0},{'name':'Roughness','value':0.5}]
  self.assertIsNone(audit.blown_out_by_prop_light(['K'],{'K':self.mates([0,0,0])},{'K':hot}))
  vec=[{'name':'EmissiveColor','value':[50.0,50.0,50.0,1.0]}]
  self.assertIsNone(audit.blown_out_by_prop_light(['K'],{'K':self.mates([0,0,0],[{'name':'EmissiveColor','value':[1,1,1,1]}])},{'K':vec}))
 def test_unrelated_param_change_still_demotes(self):
  own=[{'name':'Emissive Intensity','value':1.0},{'name':'Roughness','value':0.9}]
  self.assertIsNotNone(audit.blown_out_by_prop_light(['K'],{'K':self.mates([0,0,0])},{'K':own}))
if __name__=='__main__':unittest.main()
