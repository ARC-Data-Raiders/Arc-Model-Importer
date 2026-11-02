try:
    from .native_test_support import ROOT
except ImportError:
    from native_test_support import ROOT
"""Fresh canonical native reader regressions; no addon registration is executed."""
import importlib.util,struct,sys,types,unittest,json
from pathlib import Path
from unittest.mock import patch
import numpy as np
CANONICAL=ROOT/'uemodel_import.py'

def load_reader():
    package='_charfx_canonical_parser_probe';pkg=types.ModuleType(package);pkg.__path__=[str(CANONICAL.parent)];sys.modules[package]=pkg
    name=package+'.uemodel_import';spec=importlib.util.spec_from_file_location(name,CANONICAL);module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    with patch.dict(sys.modules,{'bpy':types.ModuleType('bpy')}):spec.loader.exec_module(module)
    return module

def morph_payload():
    name=b'female';return struct.pack('<ii',1,len(name))+name+struct.pack('<i',2)+struct.pack('<6fI',100,200,300,.1,.2,.3,0)+struct.pack('<6fI',-1,-2,-3,-.4,.5,-.6,1)

class TestCanonicalNativeMorphParser(unittest.TestCase):
    def test_normal_deltas_are_dimensionless_and_y_reflected_once(self):
        reader=load_reader();normal_results=[]
        for scale in (.01,1.):
            target=reader._parse_morph_targets(morph_payload(),scale=scale,flip_y=True)[0]
            np.testing.assert_array_equal(target.indices,[0,1])
            np.testing.assert_allclose(target.position_deltas,np.array([[100,-200,300],[-1,2,-3]])*scale,rtol=1e-6,atol=1e-7)
            np.testing.assert_allclose(target.normal_deltas,[[.1,-.2,.3],[-.4,-.5,-.6]],rtol=1e-6,atol=1e-7)
            normal_results.append(target.normal_deltas)
        np.testing.assert_array_equal(normal_results[0],normal_results[1])
        target=reader._parse_morph_targets(morph_payload(),scale=.01,flip_y=False)[0]
        np.testing.assert_allclose(target.normal_deltas,[[.1,.2,.3],[-.4,.5,-.6]],rtol=1e-6,atol=1e-7)
    def test_nonfinite_or_truncated_native_records_fail(self):
        reader=load_reader();good=morph_payload()
        with self.assertRaises(reader.UeModelError):reader._parse_morph_targets(good[:-1],scale=.01,flip_y=True)
        bad=bytearray(good);struct.pack_into('<f',bad,4+4+6+4+12,float('nan'))
        with self.assertRaises(reader.UeModelError):reader._parse_morph_targets(bytes(bad),scale=.01,flip_y=True)
    def test_inactive_weights_are_not_unknown_targets_at_reset(self):
        reader=load_reader();package=reader.__package__;calls=[]
        from material_runtime.deformation import MorphDelta,MorphTarget
        spy=types.ModuleType(package+'.material_runtime.deformation');spy.MorphDelta=MorphDelta;spy.MorphTarget=MorphTarget
        def apply(mesh,targets,weights):
            self.assertEqual(dict(weights),{});self.assertEqual(list(targets),[]);calls.append(True)
        spy.apply_normal_deltas=apply
        rt=types.ModuleType(package+'.material_runtime');rt.__path__=[]
        basis=object();keys={name:types.SimpleNamespace(value=0.,mute=False,vertex_group='',relative_key=basis) for name in ('female','male')}
        mesh={'arc_native_normal_targets':json.dumps({'female':'F','male':'M'})}
        class Mesh(dict):pass
        m=Mesh(mesh);m.shape_keys=types.SimpleNamespace(use_relative=True,key_blocks=keys,reference_key=basis)
        obj=Mesh();obj.type='MESH';obj.data=m
        with patch.dict(sys.modules,{package+'.material_runtime':rt,package+'.material_runtime.deformation':spy}):self.assertTrue(reader.apply_uemodel_morph_normals(obj))
        self.assertEqual(calls,[True]);self.assertIn('male',m['arc_native_normal_weights'])
        self.assertFalse(reader.apply_uemodel_morph_normals(obj))

if __name__=='__main__':unittest.main()
