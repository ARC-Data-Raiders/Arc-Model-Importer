import json
import pathlib
import unittest
from unittest.mock import patch
import numpy as np
from .. import uniform, ssa

RUN=pathlib.Path(r'Z:\Datamining\Arc Raiders\CodexScratch\DataRaiders-BlenderImporter\.local\parity-v2\wave3b')


class UniformTests(unittest.TestCase):
    def setUp(self):
        item=json.loads((RUN/'out/B1/fixtures.json').read_text())[0]
        self.fixture=json.loads(pathlib.Path(item['fixture']).read_text())
        self.shader=json.loads(pathlib.Path(self.fixture['shader_json']).read_text())

    def dag(self,rows,outputs):
        return dict(nodes=[dict(id=i,op=op,args=args,type='u32',src=-1) for i,(op,args) in enumerate(rows)],outputs=outputs)

    def test_u1_cb_chain(self):
        d=self.dag([('cbload',[dict(slot=0,component=2,offset=156,dynamic=False)]),('add',[0,0])],dict(v=1))
        ev=ssa.Evaluator(self.shader,self.fixture)
        value=int(ev.evaluate(d)['v'][0,0])
        folded=uniform.fold_uniform(d,self.shader,self.fixture)
        self.assertEqual(folded['nodes'][1]['op'],'const')
        self.assertEqual(folded['nodes'][1]['args'],[value])

    def test_u2_varying(self):
        d=self.dag([('input',['v6','x']),('const',[0x3f800000]),('add',[0,1])],dict(v=2))
        self.assertEqual(uniform.fold_uniform(d,self.shader,self.fixture)['nodes'][2]['op'],'add')

    def test_u3_nonuniform_raises(self):
        d=self.dag([('const',[0])],dict(v=0))
        with patch.object(uniform.ssa,'Evaluator') as cls:
            cls.return_value.values=[np.array([[0,1]],np.uint32)]
            with self.assertRaises(ValueError):uniform.fold_uniform(d,self.shader,self.fixture)

    def test_u4_idempotence(self):
        d=self.dag([('cbload',[dict(slot=0,component=2,offset=156,dynamic=False)]),('add',[0,0])],dict(v=1))
        first=uniform.fold_uniform(d,self.shader,self.fixture)
        self.assertEqual(first,uniform.fold_uniform(first,self.shader,self.fixture))


if __name__=='__main__':unittest.main()
