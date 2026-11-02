import unittest,os
from pathlib import Path
from niagara_sched_model import *
def td(name,underlying=2):return {'ClassStructOrEnum':{'ObjectName':"Class'"+name+"'"},'UnderlyingType':underlying}
class ModelTests(unittest.TestCase):
 def test_layout(self):
  layout=build_layout([{'Name':n,'TypeDef':td(k)} for n,k in [('A','NiagaraFloat'),('B','Vector3f'),('ID','NiagaraID'),('S','NiagaraSpawnInfo'),('C','NiagaraBool'),('M','NiagaraMatrix')]])
  self.assertEqual([v.float_start for v in layout.variables],[0,1,4,4,6,6]);self.assertEqual([v.int_start for v in layout.variables],[0,0,0,2,4,5]);self.assertEqual((layout.total_float,layout.total_int),(22,5))
 def test_types(self):
  s=type_info(td('NiagaraSpawnInfo'));self.assertEqual(s.float_offsets,(4,8));self.assertEqual(s.int_offsets,(0,12));self.assertEqual(type_info(td('Enum',3)).int_offsets,(0,));self.assertTrue(type_info(td('Object',1)).is_object)
  with self.assertRaises(ModelError):type_info(td('Unknown'))
 def test_store_size(self):
  with self.assertRaisesRegex(ModelError,'param_store_size'):load_param_store({'ParameterSize':4,'ParameterData':[0]},False)
 @unittest.skipUnless(os.environ.get('DR_NIAGARA_CORPUS'),'corpus unset')
 def test_fog(self):
  m=load_system(next(Path(os.environ['DR_NIAGARA_CORPUS']).rglob('NS_LocalFogSphere.json')));self.assertEqual(m.unsupported,());self.assertEqual(len(m.emitters),1);e=m.emitters[0];self.assertTrue(e.enabled);self.assertEqual(e.sim_target,'CPUSim');self.assertTrue(e.interpolated);self.assertFalse(any(s.has_ext for s in [m.system_spawn,m.system_update,e.spawn,e.update]))
 @unittest.skipUnless(os.environ.get('DR_NIAGARA_CORPUS'),'corpus unset')
 def test_event_order(self):
  m=load_system(next(Path(os.environ['DR_NIAGARA_CORPUS']).rglob('NS_Cin_FakeWeaponTracers.json')));names=[m.emitters[i].handle_name for i in m.execution_order];self.assertLess(names.index('Fountain'),names.index('SimpleSpriteBurst'))
