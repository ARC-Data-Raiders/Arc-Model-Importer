"""R19: an MI-overridden world-state layer param is authored intent, not world state."""
import importlib,unittest
from .native_test_support import PACKAGE,namespace
namespace()
audit=importlib.import_module(PACKAGE+'.Shared.materials.material_audit')
rules=importlib.import_module(PACKAGE+'.Shared.materials.audit_param_rules')
IMG=lambda stem,**k:dict({'stem':stem,'filepath':'//t/'+stem+'.png','colorspace':'sRGB','linked':True,'has_image':True,'sidecar_srgb':None},**k)
MI=lambda **k:dict({'package':'/Game/MI_X','master':'/Game/M_Base','family':'surface','textures':{'BaseColor':'/Game/T_X_BC'},'settings':{}},**k)
G=lambda **k:dict({'name':'MI_X','images':[IMG('T_X_BC')],'render_method':'DITHERED','alpha_linked':False,'alpha_clip_threshold':None,'emission_linked':False,'vertex_colour_attrs':[],'uv_maps_used':[],'output_linked':True},**k)
MASTER=lambda **k:dict({'package':'/Game/M_Base','textures':{'Snow Normal':'/Game/T_Snow_N'},'switches':{}},**k)
def rec(ref,switches=None):
 tex={'BaseColor':'/Game/T_X_BC','Snow Normal':ref}
 return audit.audit_material_facts(G(images=[IMG('T_X_BC'),IMG(ref.rsplit('/',1)[-1],linked=False)]),MI(textures=tex,switches=switches or {}),None,{'uv_layers':['UV0'],'color_attributes':['Col']},None,(),master=MASTER())
class WorldStateOverrideTests(unittest.TestCase):
 def test_rule_overridden_param_no_switch_is_inconsistency(self):
  self.assertEqual(rules.layer_rule('snow',None,[],True),('layer_not_built:snow','inconsistency'))
 def test_rule_overridden_param_switch_off_is_info(self):
  self.assertEqual(rules.layer_rule('snow',False,[{'switch':'Use Snow','value':False,'overridden':True}],True),('layer_world_state:snow','info'))
 def test_rule_inherited_param_is_info(self):
  self.assertEqual(rules.layer_rule('snow',None,[],False),('layer_world_state:snow','info'))
  self.assertEqual(rules.layer_rule('snow',None,[]),('layer_world_state:snow','info'))
 def test_rule_lidar_stays_info(self):
  self.assertEqual(rules.layer_rule('lidar',None,[],True),('layer_world_state:lidar','info'))
 def test_audit_mi_set_snow_texture_is_medium_inconsistency(self):
  r=rec('/Game/T_Snow_Custom_N')
  self.assertIn('layer_not_built:snow',r['flags']);self.assertNotIn('layer_world_state:snow',r['flags'])
  self.assertEqual(r['severity']['layer_not_built:snow'],'inconsistency')
  self.assertEqual(r['details']['layer_not_built:snow'][0]['level'],'medium')
 def test_audit_master_default_snow_texture_is_info(self):
  r=rec('/Game/T_Snow_N')
  self.assertIn('layer_world_state:snow',r['flags']);self.assertEqual(r['gate_count'],0)
 def test_audit_mi_set_snow_texture_switch_off_is_info(self):
  r=rec('/Game/T_Snow_Custom_N',switches={'Use Snow':False})
  self.assertIn('layer_world_state:snow',r['flags']);self.assertNotIn('layer_not_built:snow',r['flags'])
if __name__=='__main__':unittest.main()
